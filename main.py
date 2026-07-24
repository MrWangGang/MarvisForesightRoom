import json
import logging
import os
import signal
import subprocess
import time
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from agent import AGENTS, default_personas, generate_personas, generate_topic
from workflow import runner, store
from config import DEFAULT_MAX_TURNS, MAX_MAX_TURNS, MIN_MAX_TURNS


logger = logging.getLogger("virtual-office-motion.api")
HOST = "0.0.0.0"
PORT = 8080
app = FastAPI(title="Marvis Foresight Room")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")
templates = Jinja2Templates(directory="templates")


@app.middleware("http")
async def add_asset_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return response


class TopicRequest(BaseModel):
    description: str


class PersonaRequest(BaseModel):
    topic: str
    description: str


class TaskCreateRequest(BaseModel):
    topic: str
    description: str
    personas: Optional[Dict[str, Dict[str, Any]]] = None
    max_turns: Optional[int] = None


async def llm_result(awaitable):
    try:
        return await awaitable
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("LLM 调用失败")
        raise HTTPException(502, f"LLM 调用失败：{exc}") from exc


def public_task(task: Dict) -> Dict:
    mind_map = task.get("mind_map")
    if not isinstance(mind_map, dict) or not isinstance(mind_map.get("tree"), dict):
        mind_map = None
    return {
        "id": task["id"],
        "title": task["title"],
        "topic": task["topic"],
        "summary": task["summary"],
        "description": task["description"],
        "status": task["status"],
        "phase": task["phase"],
        "turn_index": task["turn_index"],
        "max_turns": task.get("max_turns", DEFAULT_MAX_TURNS),
        "forced_convergence_at": task.get("forced_convergence_at", 0),
        "round": task["round"],
        "agents": task["agents"],
        "convergence_state": task.get("convergence_state", ""),
        "personaDrafts": task["personaDrafts"],
        "messages": task["messages"],
        "final_result": task["final_result"],
        "mind_map": mind_map,
    }


def has_rich_personas(personas: Optional[Dict[str, Dict[str, Any]]]) -> bool:
    if not personas:
        return False
    required = {
        "unique_id",
        "personality_description",
        "biography",
        "demographic_traits",
        "mbti",
        "professional_background",
        "risk_preference",
        "behavior_pattern",
        "social_relationships",
        "ideology",
    }
    for agent in AGENTS:
        profile = (personas.get(agent["id"]) or {}).get("profile")
        if not isinstance(profile, dict) or not required.issubset(profile.keys()):
            return False
    return True


def normalize_max_turns(value: Optional[int]) -> int:
    try:
        max_turns = int(value or DEFAULT_MAX_TURNS)
    except (TypeError, ValueError):
        max_turns = DEFAULT_MAX_TURNS
    return max(MIN_MAX_TURNS, min(MAX_MAX_TURNS, max_turns))


def sse(event: str, data: Dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def pids_using_port(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        logger.warning("找不到 lsof，无法自动检测端口 %s 占用", port)
        return []
    pids = []
    current_pid = os.getpid()
    for line in result.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid != current_pid and pid not in pids:
            pids.append(pid)
    return pids


def kill_processes_on_port(port: int) -> None:
    pids = pids_using_port(port)
    if not pids:
        return
    logger.warning("端口 %s 已被进程 %s 占用，启动前先关闭", port, ", ".join(map(str, pids)))
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning("没有权限终止占用端口 %s 的进程 %s", port, pid)
    deadline = time.time() + 3
    while time.time() < deadline:
        if not any(process_exists(pid) for pid in pids):
            return
        time.sleep(0.1)
    for pid in pids:
        if not process_exists(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning("没有权限强制终止占用端口 %s 的进程 %s", port, pid)


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/bootstrap")
async def bootstrap():
    return {
        "agents": AGENTS,
        "tasks": [public_task(task) for task in store.list_tasks()],
        "defaultPersonas": default_personas(),
    }


@app.post("/api/topic")
async def api_topic(payload: TopicRequest):
    description = payload.description.strip()
    if not description:
        raise HTTPException(400, "任务描述不能为空")
    return {"topic": await llm_result(generate_topic(description))}


@app.post("/api/personas")
async def api_personas(payload: PersonaRequest):
    topic = payload.topic.strip()[:12]
    description = payload.description.strip()
    if not topic or not description:
        raise HTTPException(400, "主题和任务描述不能为空")
    return {"personas": await llm_result(generate_personas(topic, description))}


@app.get("/api/tasks")
async def api_tasks():
    return {"tasks": [public_task(task) for task in store.list_tasks()]}


@app.post("/api/tasks")
async def api_create_task(payload: TaskCreateRequest):
    topic = payload.topic.strip()[:12]
    description = payload.description.strip()
    if not topic or not description:
        raise HTTPException(400, "主题和任务描述不能为空")
    if not has_rich_personas(payload.personas):
        raise HTTPException(409, "请先点击 AI 生成人设，确认 7 个完整人物卡生成后再创建讨论室")
    personas = payload.personas
    max_turns = normalize_max_turns(payload.max_turns)
    task = store.create(topic, description, personas, max_turns=max_turns)
    store.append_message(
        task["id"],
        "marvis",
        f"「{topic}」讨论室已经开好。7 个人物卡已生成，最大轮数为 {max_turns}。他们会共享上下文，在争吵、结盟、拆分分支之后自然收束；如果接近上限仍未收束，马维斯会强制介入投票。",
        "system",
    )
    runner.ensure(task["id"])
    task = store.get(task["id"])
    return {"task": public_task(task)}


@app.get("/api/tasks/{task_id}")
async def api_task(task_id: str):
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return {"task": public_task(task)}


@app.post("/api/tasks/{task_id}/mind-map")
async def api_mind_map(task_id: str):
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    public = public_task(task)
    return {"mind_map": None, "task": public}


@app.post("/api/tasks/{task_id}/pause")
async def api_pause(task_id: str):
    task = store.set_status(task_id, "paused")
    if not task:
        raise HTTPException(404, "任务不存在")
    return {"task": public_task(task)}


@app.delete("/api/tasks/{task_id}")
async def api_delete_task(task_id: str):
    deleted = await runner.delete_task(task_id)
    if not deleted:
        raise HTTPException(404, "任务不存在")
    return {"deleted": True, "task_id": task_id}


@app.post("/api/tasks/{task_id}/resume")
async def api_resume(task_id: str):
    task = store.set_status(task_id, "running")
    if not task:
        raise HTTPException(404, "任务不存在")
    runner.ensure(task_id)
    return {"task": public_task(task)}


@app.post("/api/tasks/{task_id}/stop")
async def api_stop(task_id: str):
    task = store.set_status(task_id, "stopped")
    if not task:
        raise HTTPException(404, "任务不存在")
    return {"task": public_task(task)}


@app.get("/api/tasks/{task_id}/stream")
async def api_stream(task_id: str):
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    async def generator():
        queue = await runner.subscribe(task_id)
        try:
            while True:
                item = await queue.get()
                data = item["data"]
                if isinstance(data.get("task"), dict):
                    data = {**data, "task": public_task(data["task"])}
                yield sse(item["event"], data)
        finally:
            runner.unsubscribe(task_id, queue)

    return StreamingResponse(generator(), media_type="text/event-stream")


if __name__ == "__main__":
    kill_processes_on_port(PORT)
    uvicorn.run("main:app", host=HOST, port=PORT)
