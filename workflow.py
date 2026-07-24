import asyncio
import json
import sqlite3
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from agent import (
    AGENTS,
    AGENT_MAP,
    AGENT_ORDER,
    current_speaker_id,
    default_personas,
    final_summary,
    generate_mind_map,
    normalize_persona,
    select_next_speaker,
    should_finish,
    stream_agent_reply,
)
from config import DEFAULT_MAX_TURNS, MAX_MAX_TURNS, MIN_MAX_TURNS


DB_PATH = Path(__file__).resolve().parent / "data" / "marvis.db"
SCHEMA_VERSION = 3


class TaskStore:
    def __init__(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = DB_PATH
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                self.rebuild_schema(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    description TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    max_turns INTEGER NOT NULL DEFAULT 200,
                    forced_convergence_at INTEGER NOT NULL DEFAULT 0,
                    speaker_index INTEGER NOT NULL,
                    agents INTEGER NOT NULL,
                    convergence_state TEXT NOT NULL DEFAULT '',
                    final_result TEXT NOT NULL DEFAULT '',
                    mind_map TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS personas (
                    task_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    current_role TEXT NOT NULL,
                    short_desc TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (task_id, agent_id),
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    speaker_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    branch_id TEXT NOT NULL DEFAULT '',
                    stance TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                )
                """
            )
            self.ensure_task_columns(conn)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def rebuild_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE IF EXISTS messages")
        conn.execute("DROP TABLE IF EXISTS personas")
        conn.execute("DROP TABLE IF EXISTS tasks")

    def ensure_task_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "max_turns" not in columns:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN max_turns INTEGER NOT NULL DEFAULT {DEFAULT_MAX_TURNS}")
        if "forced_convergence_at" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN forced_convergence_at INTEGER NOT NULL DEFAULT 0")

    def hydrate_task(self, row: sqlite3.Row, conn: sqlite3.Connection) -> Dict:
        task_id = row["id"]
        personas = {}
        for item in conn.execute(
                "SELECT agent_id, display_name, current_role, short_desc, profile_json FROM personas WHERE task_id = ?",
                (task_id,),
            ).fetchall():
            profile = json.loads(item["profile_json"]) if item["profile_json"] else {}
            personas[item["agent_id"]] = {
                "title": item["current_role"],
                "desc": item["short_desc"],
                "display_name": item["display_name"],
                "profile": profile,
            }
        messages = [
            {
                "id": item["id"],
                "speaker_id": item["speaker_id"],
                "content": item["content"],
                "kind": item["kind"],
                "branch_id": item["branch_id"],
                "stance": item["stance"],
                "created_at": item["created_at"],
            }
            for item in conn.execute(
                "SELECT id, speaker_id, content, kind, branch_id, stance, created_at FROM messages WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        ]
        turn_index = row["turn_index"]
        return {
            "id": task_id,
            "title": row["title"],
            "topic": row["topic"],
            "description": row["description"],
            "summary": row["summary"],
            "status": row["status"],
            "phase": row["phase"],
            "turn_index": turn_index,
            "max_turns": row["max_turns"] if "max_turns" in row.keys() else DEFAULT_MAX_TURNS,
            "forced_convergence_at": row["forced_convergence_at"] if "forced_convergence_at" in row.keys() else 0,
            "round": max(1, (turn_index // max(1, len(AGENT_ORDER))) + 1),
            "speaker_index": row["speaker_index"],
            "agents": row["agents"],
            "convergence_state": row["convergence_state"],
            "personaDrafts": {**default_personas(), **personas},
            "messages": messages,
            "final_result": row["final_result"],
            "mind_map": json.loads(row["mind_map"]) if row["mind_map"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_tasks(self) -> List[Dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
            return [self.hydrate_task(row, conn) for row in rows]

    def get(self, task_id: str) -> Optional[Dict]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self.hydrate_task(row, conn) if row else None

    def create(self, topic: str, description: str, personas: Optional[Dict[str, Dict]] = None, max_turns: int = DEFAULT_MAX_TURNS) -> Dict:
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        now = time.time()
        persona_data = personas or default_personas()
        max_turns = max(MIN_MAX_TURNS, min(MAX_MAX_TURNS, int(max_turns or DEFAULT_MAX_TURNS)))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, title, topic, description, summary, status, phase, turn_index,
                    max_turns, forced_convergence_at, speaker_index, agents, convergence_state,
                    final_result, mind_map, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    topic[:12],
                    topic[:12],
                    description,
                    description,
                    "running",
                    "群体涌动 · 人物入场",
                    1,
                    max_turns,
                    0,
                    0,
                    len(AGENTS),
                    "",
                    "",
                    "",
                    now,
                    now,
                ),
            )
            for agent in AGENTS:
                draft = normalize_persona(agent, persona_data.get(agent["id"], {}), topic)
                conn.execute(
                    """
                    INSERT INTO personas (
                        task_id, agent_id, display_name, current_role, short_desc,
                        profile_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        agent["id"],
                        agent["name"],
                        draft["title"],
                        draft["desc"],
                        json.dumps(draft["profile"], ensure_ascii=False),
                        now,
                        now,
                    ),
                )
        task = self.get(task_id)
        if not task:
            raise RuntimeError("failed to create task")
        return task

    def set_status(self, task_id: str, status: str) -> Optional[Dict]:
        with self.connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, time.time(), task_id),
            )
        return self.get(task_id)

    def delete(self, task_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM messages WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM personas WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return True

    def update_task_fields(self, task_id: str, **fields) -> Optional[Dict]:
        allowed = {
            "status",
            "phase",
            "turn_index",
            "max_turns",
            "forced_convergence_at",
            "speaker_index",
            "convergence_state",
            "final_result",
            "mind_map",
            "updated_at",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if "mind_map" in values and not isinstance(values["mind_map"], str):
            values["mind_map"] = json.dumps(values["mind_map"], ensure_ascii=False)
        if not values:
            return self.get(task_id)
        values["updated_at"] = time.time()
        set_clause = ", ".join([f"{key} = ?" for key in values])
        params = list(values.values()) + [task_id]
        with self.connect() as conn:
            conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", params)
        return self.get(task_id)

    def append_message(
        self,
        task_id: str,
        speaker_id: str,
        content: str,
        kind: str = "agent",
        branch_id: str = "",
        stance: str = "",
    ) -> Optional[Dict]:
        if not self.get(task_id):
            return None
        message = {
            "id": f"msg-{uuid.uuid4().hex[:10]}",
            "speaker_id": speaker_id,
            "content": content.strip(),
            "kind": kind,
            "branch_id": branch_id,
            "stance": stance,
            "created_at": time.time(),
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (
                    id, task_id, speaker_id, content, kind, branch_id, stance, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message["id"], task_id, speaker_id, message["content"], kind, branch_id, stance, message["created_at"]),
            )
            conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (time.time(), task_id))
        return message

    def advance_speaker(self, task_id: str, next_speaker_id: Optional[str] = None) -> Optional[str]:
        task = self.get(task_id)
        if not task:
            return None
        if next_speaker_id not in AGENT_ORDER:
            next_speaker_id = AGENT_ORDER[(task["speaker_index"] + 1) % len(AGENT_ORDER)]
        speaker_index = AGENT_ORDER.index(next_speaker_id)
        next_turn = task["turn_index"] + 1
        if int(task.get("forced_convergence_at") or 0) > 0 or str(task.get("phase", "")).startswith("强制收束"):
            phase = f"强制收束 · 投票中 {next_turn}/{task_max_turns(task)}"
        else:
            phase = f"群体涌动 · 第 {next_turn} 次发言"
        fields = {
            "speaker_index": speaker_index,
            "turn_index": next_turn,
            "phase": phase,
        }
        self.update_task_fields(task_id, **fields)
        return AGENT_ORDER[speaker_index]


def task_max_turns(task: Dict) -> int:
    return max(MIN_MAX_TURNS, min(MAX_MAX_TURNS, int(task.get("max_turns") or DEFAULT_MAX_TURNS)))


def agent_messages(task: Dict) -> List[Dict]:
    return [
        message
        for message in task.get("messages", [])
        if message.get("kind") == "agent" and message.get("speaker_id") in AGENT_ORDER
    ]


def forced_convergence_started(task: Dict) -> bool:
    return int(task.get("forced_convergence_at") or 0) > 0 or str(task.get("phase", "")).startswith("强制收束")


def forced_vote_messages(task: Dict) -> List[Dict]:
    forced_at = int(task.get("forced_convergence_at") or 0)
    if forced_at <= 0:
        return []
    messages = agent_messages(task)
    return messages[forced_at:]


def forced_vote_ids(task: Dict) -> set[str]:
    return {
        message["speaker_id"]
        for message in forced_vote_messages(task)
        if message.get("speaker_id") in AGENT_ORDER and message.get("speaker_id") != "marvis"
    }


def forced_votes_complete(task: Dict) -> bool:
    non_host_ids = {agent_id for agent_id in AGENT_ORDER if agent_id != "marvis"}
    return non_host_ids.issubset(forced_vote_ids(task))


def next_forced_speaker(task: Dict, previous_speaker_id: str) -> str:
    if forced_votes_complete(task):
        return "marvis"
    voted = forced_vote_ids(task)
    for agent_id in AGENT_ORDER:
        if agent_id == "marvis" or agent_id == previous_speaker_id:
            continue
        if agent_id not in voted:
            return agent_id
    return "marvis"


class TaskRunner:
    def __init__(self) -> None:
        self.running: Dict[str, asyncio.Task] = {}
        self.subscribers: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        self.empty_retries: Dict[str, int] = defaultdict(int)

    async def publish(self, task_id: str, event: str, data: Dict) -> None:
        for queue in list(self.subscribers.get(task_id, [])):
            await queue.put({"event": event, "data": data})

    def ensure(self, task_id: str) -> None:
        task = store.get(task_id)
        if not task or task["status"] != "running":
            return
        existing = self.running.get(task_id)
        if existing and not existing.done():
            return
        self.running[task_id] = asyncio.create_task(self.run(task_id))

    async def subscribe(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers[task_id].append(queue)
        task = store.get(task_id)
        if task:
            await queue.put({"event": "task_update", "data": {"status": task["status"], "task": task}})
        self.ensure(task_id)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        if queue in self.subscribers.get(task_id, []):
            self.subscribers[task_id].remove(queue)

    async def delete_task(self, task_id: str) -> bool:
        existing = self.running.pop(task_id, None)
        if existing and not existing.done():
            existing.cancel()
        await self.publish(task_id, "task_deleted", {"task_id": task_id})
        self.subscribers.pop(task_id, None)
        return store.delete(task_id)

    def prepare_forced_convergence(self, task_id: str, task: Dict) -> Dict:
        if forced_convergence_started(task):
            return task
        max_turns = task_max_turns(task)
        force_at = max(1, max_turns - len(AGENT_ORDER))
        if int(task.get("turn_index") or 1) < force_at:
            return task
        updated = store.update_task_fields(
            task_id,
            speaker_index=AGENT_ORDER.index("marvis"),
            forced_convergence_at=int(task.get("turn_index") or 1),
            phase=f"强制收束 · 接近最大 {max_turns} 轮",
            convergence_state=f"已接近最大 {max_turns} 轮，马维斯将介入并要求全员停止扩散、进入方案投票。",
        )
        return updated or task

    async def finish_task(self, task_id: str, task: Dict) -> None:
        summary = await final_summary(task)
        store.append_message(task_id, "marvis", summary, "final")
        latest_after_summary = store.get(task_id)
        mind_map = await generate_mind_map(latest_after_summary)
        latest = store.update_task_fields(
            task_id,
            final_result=summary,
            mind_map=mind_map,
            phase="已完成 · 输出结论",
            status="finished",
        )
        await self.publish(task_id, "task_finished", {"summary": summary, "task": latest})

    async def run(self, task_id: str) -> None:
        while True:
            current = store.get(task_id)
            if not current:
                await self.publish(task_id, "task_update", {"status": "missing"})
                return
            if current["status"] != "running":
                await self.publish(task_id, "task_update", {"status": current["status"], "task": current})
                return

            current = self.prepare_forced_convergence(task_id, current)

            speaker_id = current_speaker_id(current)
            agent = AGENT_MAP[speaker_id]
            await self.publish(task_id, "speaker_start", {"speaker_id": speaker_id, "agent": agent, "task": current})

            content = ""
            try:
                async for token in stream_agent_reply(current, speaker_id):
                    latest = store.get(task_id)
                    if not latest or latest["status"] != "running":
                        return
                    content += token
                    await self.publish(task_id, "token", {"speaker_id": speaker_id, "token": token})
                    await asyncio.sleep(0)
            except Exception as exc:
                content = f"模型调用失败：{exc}"
                await self.publish(task_id, "token", {"speaker_id": speaker_id, "token": content})

            if not content.strip():
                retry_key = f"{task_id}:{speaker_id}"
                self.empty_retries[retry_key] += 1
                await self.publish(task_id, "bubble_hide", {"speaker_id": speaker_id})
                if self.empty_retries[retry_key] <= 2:
                    await asyncio.sleep(0.8)
                    continue
                content = "我刚才卡了一下。接着说：这个点还没讨论完，我先把问题重新抛回桌面，别急着收束。"
                await self.publish(task_id, "token", {"speaker_id": speaker_id, "token": content})
            else:
                self.empty_retries.pop(f"{task_id}:{speaker_id}", None)

            message = store.append_message(task_id, speaker_id, content)
            latest = store.get(task_id)
            await self.publish(task_id, "message_done", {"speaker_id": speaker_id, "message": message, "task": latest})
            await self.publish(task_id, "bubble_hide", {"speaker_id": speaker_id})
            await asyncio.sleep(0.45)

            latest = store.get(task_id)
            if not latest or latest["status"] != "running":
                if latest:
                    await self.publish(task_id, "task_update", {"status": latest["status"], "task": latest})
                return

            if forced_convergence_started(latest) and speaker_id == "marvis" and forced_votes_complete(latest):
                await self.finish_task(task_id, latest)
                return

            if speaker_id == "marvis":
                decision = await should_finish(latest)
                if decision.get("reason"):
                    store.update_task_fields(task_id, convergence_state=decision["reason"])
                if decision["decision"] == "finish":
                    await self.finish_task(task_id, latest)
                    return

            if forced_convergence_started(latest):
                next_speaker_id = next_forced_speaker(latest, speaker_id)
            else:
                next_speaker_id = await select_next_speaker(latest, speaker_id)
            store.advance_speaker(task_id, next_speaker_id)


store = TaskStore()
runner = TaskRunner()
