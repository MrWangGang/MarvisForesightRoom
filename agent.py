import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

import httpx


logger = logging.getLogger("virtual-office-motion.llm")


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_env_file()

try:
    import config
except ImportError:
    config = None

DEEPSEEK_API_KEY = str(getattr(config, "DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))).strip()
DEEPSEEK_BASE_URL = str(getattr(config, "DEEPSEEK_BASE_URL", os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))).rstrip("/")
DEEPSEEK_MODEL = str(getattr(config, "DEEPSEEK_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-chat")))
MAX_AGENT_MESSAGE_CHARS = int(getattr(config, "MAX_AGENT_MESSAGE_CHARS", os.getenv("MAX_AGENT_MESSAGE_CHARS", 360)))
MIN_CONVERGENCE_MESSAGES = int(getattr(config, "MIN_CONVERGENCE_MESSAGES", os.getenv("MIN_CONVERGENCE_MESSAGES", 35)))
CHAT_TIMEOUT = httpx.Timeout(20.0, connect=6.0, read=20.0, write=10.0, pool=6.0)
STREAM_TIMEOUT = httpx.Timeout(90.0, connect=10.0, read=60.0, write=20.0, pool=10.0)
PERSONA_TIMEOUT = httpx.Timeout(150.0, connect=10.0, read=120.0, write=20.0, pool=10.0)


AGENTS = [
    {
        "id": "marvis",
        "name": "马维斯",
        "role": "主理人",
        "color": "#2ba7e8",
        "default_title": "推演主理人 / 阶段决策者",
        "default_desc": "负责定义阶段目标、决定发言顺序、追问冲突点、判断讨论是否已有真实产出，并在合适时机收束结论。",
    },
    {
        "id": "jack",
        "name": "Aiden",
        "role": "产品",
        "color": "#168cff",
        "default_title": "产品路径推演 Agent",
        "default_desc": "关注用户需求、功能边界、使用路径、优先级和产品机会。",
    },
    {
        "id": "brown",
        "name": "Bennett",
        "role": "工程",
        "color": "#ef3027",
        "default_title": "技术可行性 Agent",
        "default_desc": "关注实现复杂度、系统风险、技术债、数据依赖和工程成本。",
    },
    {
        "id": "ella",
        "name": "Serena",
        "role": "体验",
        "color": "#7b22ff",
        "default_title": "体验表达 Agent",
        "default_desc": "关注界面体验、用户理解成本、情绪反馈、表达方式和交互路径。",
    },
    {
        "id": "noah",
        "name": "Orion",
        "role": "增长",
        "color": "#24d8c9",
        "default_title": "增长运营 Agent",
        "default_desc": "关注触达、转化、留存、传播节奏和上线后的运营动作。",
    },
    {
        "id": "luna",
        "name": "Luna",
        "role": "数据",
        "color": "#ffd200",
        "default_title": "数据洞察 Agent",
        "default_desc": "关注指标、趋势、证据、可验证假设和数据口径。",
    },
    {
        "id": "ollie",
        "name": "Elliot",
        "role": "风险",
        "color": "#1fd7e6",
        "default_title": "风险支持 Agent",
        "default_desc": "关注执行风险、用户问题、服务成本、异常场景和兜底方案。",
    },
]

AGENT_MAP = {agent["id"]: agent for agent in AGENTS}
AGENT_ORDER = [agent["id"] for agent in AGENTS]


def _fallback_profile(agent: Dict[str, str], topic: str = "待定议题") -> Dict[str, Any]:
    return {
        "unique_id": f"{topic}-{agent['id']}",
        "personality_description": agent["default_desc"],
        "biography": "还没有为当前讨论生成完整个人传记。",
        "demographic_traits": {
            "age_range": "未知",
            "city_tier": "未知",
            "life_stage": "未知",
            "education": "未知",
        },
        "mbti": "未知",
        "professional_background": agent["default_title"],
        "risk_preference": "中性",
        "behavior_pattern": "根据会议上下文提出观点、追问和反驳。",
        "social_relationships": "与其他参与者保持松散协作关系。",
        "ideology": "暂未形成针对本议题的独特立场。",
        "speaking_style": "直接、具体、围绕当前话题发言。",
    }


def default_personas() -> Dict[str, Dict[str, Any]]:
    return {
        agent["id"]: {
            "title": "",
            "desc": "",
            "profile": {},
        }
        for agent in AGENTS
    }


def _headers() -> Dict[str, str]:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY，请在 virtual-office-motion/.env 或环境变量中配置")
    return {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }


def _response_error_text(response: httpx.Response) -> str:
    text = response.text.strip()
    if len(text) > 800:
        text = f"{text[:800]}..."
    return text or response.reason_phrase


def _raise_for_llm_response(response: httpx.Response) -> None:
    if response.is_success:
        return
    body = _response_error_text(response)
    logger.error("DeepSeek HTTP %s: %s", response.status_code, body)
    raise RuntimeError(f"DeepSeek HTTP {response.status_code}: {body}")


async def chat(messages: List[Dict[str, str]], temperature: float = 0.75) -> str:
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=CHAT_TIMEOUT) as client:
        try:
            response = await client.post(f"{DEEPSEEK_BASE_URL}/chat/completions", headers=_headers(), json=payload)
            _raise_for_llm_response(response)
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"DeepSeek 请求超时：{exc}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"DeepSeek 网络请求失败：{exc}") from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"DeepSeek 返回格式异常：{exc}") from exc


async def stream_chat(messages: List[Dict[str, str]], temperature: float = 0.78) -> AsyncIterator[str]:
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        try:
            async with client.stream("POST", f"{DEEPSEEK_BASE_URL}/chat/completions", headers=_headers(), json=payload) as response:
                _raise_for_llm_response(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line.removeprefix("data:").strip()
                    if raw == "[DONE]":
                        break
                    try:
                        data = json.loads(raw)
                        token = data["choices"][0].get("delta", {}).get("content")
                    except Exception:
                        token = ""
                    if token:
                        yield token
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"DeepSeek 流式请求超时：{exc}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"DeepSeek 流式网络请求失败：{exc}") from exc


async def stream_complete(messages: List[Dict[str, str]], temperature: float = 0.72) -> str:
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    parts: List[str] = []
    async with httpx.AsyncClient(timeout=PERSONA_TIMEOUT) as client:
        try:
            async with client.stream("POST", f"{DEEPSEEK_BASE_URL}/chat/completions", headers=_headers(), json=payload) as response:
                _raise_for_llm_response(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line.removeprefix("data:").strip()
                    if raw == "[DONE]":
                        break
                    try:
                        data = json.loads(raw)
                        token = data["choices"][0].get("delta", {}).get("content")
                    except Exception:
                        token = ""
                    if token:
                        parts.append(token)
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"DeepSeek 生成人设超时：{exc}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"DeepSeek 生成人设网络请求失败：{exc}") from exc
    result = "".join(parts).strip()
    if not result:
        raise RuntimeError("DeepSeek 生成人设返回为空")
    return result


def parse_json_object(text: str, fallback: Any) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    try:
        return json.loads(cleaned)
    except Exception:
        return fallback


def _history_lines(task: Dict, limit: int = 18) -> str:
    lines: List[str] = []
    for message in task["messages"][-limit:]:
        agent = AGENT_MAP.get(message["speaker_id"], {"name": "系统", "role": ""})
        lines.append(f"{agent['name']}({agent.get('role', '')})：{message['content']}")
    return "\n".join(lines) or "暂无历史发言。"


def _clean_markdown(text: str) -> str:
    cleaned_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        while stripped.startswith("#"):
            stripped = stripped[1:].strip()
        cleaned_lines.append(stripped)
    return "\n".join(cleaned_lines).strip()


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def normalize_persona(agent: Dict[str, str], raw: Any, topic: str) -> Dict[str, Any]:
    fallback = {
        "title": agent["default_title"],
        "desc": f"围绕「{topic}」从一个具体真人视角参与推演，会表达偏见、利益、犹豫和反驳。",
        "profile": _fallback_profile(agent, topic),
    }
    if not isinstance(raw, dict):
        raw = {}

    profile = raw.get("profile") if isinstance(raw.get("profile"), dict) else raw
    if not isinstance(profile, dict):
        profile = {}

    normalized_profile = {
        "unique_id": str(profile.get("unique_id") or profile.get("id") or f"{topic}-{agent['id']}").strip()[:80],
        "personality_description": str(
            profile.get("personality_description")
            or profile.get("personality")
            or profile.get("人格描述")
            or fallback["profile"]["personality_description"]
        ).strip()[:420],
        "biography": str(profile.get("biography") or profile.get("bio") or profile.get("个人传记") or fallback["profile"]["biography"]).strip()[:620],
        "demographic_traits": profile.get("demographic_traits")
        or profile.get("demographics")
        or profile.get("人口统计特征")
        or fallback["profile"]["demographic_traits"],
        "mbti": str(profile.get("mbti") or profile.get("MBTI") or fallback["profile"]["mbti"]).strip()[:16],
        "professional_background": str(
            profile.get("professional_background") or profile.get("career") or profile.get("职业背景") or fallback["profile"]["professional_background"]
        ).strip()[:360],
        "risk_preference": str(profile.get("risk_preference") or profile.get("risk") or profile.get("风险偏好") or fallback["profile"]["risk_preference"]).strip()[:180],
        "behavior_pattern": str(profile.get("behavior_pattern") or profile.get("behavior") or profile.get("行为模式") or fallback["profile"]["behavior_pattern"]).strip()[:420],
        "social_relationships": profile.get("social_relationships")
        or profile.get("relationships")
        or profile.get("社交关系")
        or fallback["profile"]["social_relationships"],
        "ideology": str(profile.get("ideology") or profile.get("ideological_stance") or profile.get("意识形态立场") or fallback["profile"]["ideology"]).strip()[:420],
        "speaking_style": str(profile.get("speaking_style") or profile.get("说话方式") or fallback["profile"]["speaking_style"]).strip()[:260],
    }
    if not isinstance(normalized_profile["demographic_traits"], (dict, list)):
        normalized_profile["demographic_traits"] = str(normalized_profile["demographic_traits"])[:260]
    if not isinstance(normalized_profile["social_relationships"], (dict, list)):
        normalized_profile["social_relationships"] = str(normalized_profile["social_relationships"])[:420]

    title = str(raw.get("title") or raw.get("current_role") or profile.get("current_role") or fallback["title"]).strip()[:48]
    desc = str(raw.get("desc") or raw.get("short_desc") or raw.get("description") or profile.get("personality_description") or fallback["desc"]).strip()[:320]
    return {
        "title": title or fallback["title"],
        "desc": desc or fallback["desc"],
        "profile": normalized_profile,
    }


def persona_brief(task: Dict, speaker_id: str) -> str:
    agent = AGENT_MAP[speaker_id]
    persona = normalize_persona(agent, task["personaDrafts"].get(speaker_id, {}), task.get("topic", "当前议题"))
    profile = persona["profile"]
    return "\n".join(
        [
            f"动态角色：{persona['title']}",
            f"唯一 ID：{profile['unique_id']}",
            f"人格描述：{profile['personality_description']}",
            f"个人传记：{profile['biography']}",
            f"人口统计特征：{_compact_json(profile['demographic_traits'])}",
            f"MBTI：{profile['mbti']}",
            f"职业背景：{profile['professional_background']}",
            f"风险偏好：{profile['risk_preference']}",
            f"行为模式：{profile['behavior_pattern']}",
            f"社交关系：{_compact_json(profile['social_relationships'])}",
            f"独特意识形态立场：{profile['ideology']}",
            f"说话方式：{profile['speaking_style']}",
        ]
    )


def _is_forced_convergence(task: Dict) -> bool:
    return int(task.get("forced_convergence_at") or 0) > 0 or str(task.get("phase", "")).startswith("强制收束")


def _forced_vote_count(task: Dict) -> int:
    forced_at = int(task.get("forced_convergence_at") or 0)
    if forced_at <= 0:
        return 0
    messages = [
        message
        for message in task.get("messages", [])
        if message.get("kind") == "agent" and message.get("speaker_id") in AGENT_ORDER
    ]
    return len({message.get("speaker_id") for message in messages[forced_at:] if message.get("speaker_id") != "marvis"})


def _speaker_prompt(task: Dict, speaker_id: str) -> List[Dict[str, str]]:
    agent = AGENT_MAP[speaker_id]
    if speaker_id == "marvis":
        host_rule = (
            "你是一个真实会议里的主理人，不是总结机器。"
            "你要观察房间里的情绪、临时结盟和分歧，把话题掰出新的岔路。"
            "你可以打断、追问、偏袒某个大胆想法，也可以点名两个人继续吵清楚。"
            "如果你感觉分支已经开始回流，不要强行宣布结束，而是温和地问大家是不是可以收束，并允许任何人继续反对。"
        )
    else:
        host_rule = (
            "你是一个有生活经历、利益偏好和脾气的人，不是 Agent、客服或咨询报告。"
            "你可以站队、拆台、被说服、临时改口，也可以对某个人说“我不同意你刚才那句”。"
            "少用套话和专业黑话，多说具体场景、直觉判断、反常识想法、突然冒出来的点子。"
        )

    forced_rule = ""
    if _is_forced_convergence(task):
        if speaker_id == "marvis" and _forced_vote_count(task) >= len(AGENT_ORDER) - 1:
            forced_rule = (
                "当前已经进入强制收束投票，并且其他人已经投完票。"
                "你这一轮必须统计投票倾向，直接拍板最终采用哪种方案，说明少数意见如何被吸收。"
                "不要再提出新分支，不要再问大家意见。"
            )
        elif speaker_id == "marvis":
            forced_rule = (
                "当前已经接近最大轮数。你这一轮必须强硬介入：明确告诉大家停止扩散新分支，"
                "把已有讨论压成 2-4 个候选方案，并要求每个人接下来只做投票：选哪个方案、为什么、保留哪个反对意见。"
                "语气可以强一点，像会议主持人拍桌控场，但不要直接替大家结束。"
            )
        else:
            forced_rule = (
                "当前已经进入强制收束投票。你不能再开新分支，也不能继续长篇争论。"
                "你必须从马维斯给出的候选方案里投一票，说明理由，并补一句你希望保留的风险或条件。"
            )

    system = f"""
你正在参与一个 7 个真人角色的群聊推演任务。
每个人都能看到全部聊天历史，但身份、记忆、偏见、关系和表达习惯都不同。

你的名字：{agent['name']}
你的基础席位：{agent['role']}
你的完整人物卡：
{persona_brief(task, speaker_id)}

{host_rule}
{forced_rule}

要求：
- 中文回答。
- 只输出你这一轮要说的话，不要加旁白。
- 允许使用 Markdown 加粗和列表，但不要使用 #、##、### 标题符号。
- 像真人在群里接话：可以短句、反问、犹豫、玩笑、情绪化判断，不要写成正式报告。
- 必须点名回应最近某个人的观点，并表现你和他是结盟、争吵、补刀还是被他说服。
- 每次发言都要把讨论长出 1-2 个新分支，分支可以是荒诞但有启发的。
- 如果马维斯刚问“是否可以收束”，你必须像真人一样明确回应：同意收束、反对收束、还是还想补一个分支，并说出原因。
- 如果你觉得讨论已经没有新的关键分支，也可以自然地明确说“我同意收束”；只要你还有疑问、反对或新分支，就明确说还不能收。
- 不要每轮都机械列“指标/风险/下一步”，也不要总用专家口吻下定义。
- 控制在 {MAX_AGENT_MESSAGE_CHARS} 字以内。
""".strip()

    user = f"""
任务主题：{task['topic']}
任务描述：{task['description']}
当前阶段：{task['phase']}
当前发言次数：第 {task.get('turn_index', task.get('round', 1))} 次

最近聊天记录：
{_history_lines(task)}

现在轮到你发言。
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def stream_agent_reply(task: Dict, speaker_id: str):
    async for token in stream_chat(_speaker_prompt(task, speaker_id)):
        yield token


async def generate_topic(description: str) -> str:
    if not description.strip():
        return ""
    prompt = [
        {
            "role": "system",
            "content": "你是产品策略命名助手。根据任务描述生成一个中文讨论主题，最多 12 个字，只输出主题本身。",
        },
        {"role": "user", "content": description.strip()},
    ]
    result = await chat(prompt, temperature=0.55)
    topic = result.replace("《", "").replace("》", "").strip()[:12]
    if not topic:
        raise RuntimeError("模型没有返回有效讨论主题")
    return topic


async def generate_personas(topic: str, description: str) -> Dict[str, Dict[str, Any]]:
    agent_brief = "\n".join([f"- {a['id']}：{a['name']}（只是头像/发言席位，不代表固定职业）" for a in AGENT_MAP.values()])
    prompt = [
        {
            "role": "system",
            "content": (
                "你是群体推演的人物导演。请根据当前主题和任务描述，先创造 7 个像真实人类一样参与讨论的人物。"
                "不要沿用固定的产品/工程/体验/增长/数据/风险分工，除非任务确实需要。"
                "这些人必须有偏见、记忆、利益诉求、脾气、关系张力，能争吵、结盟、拆台、被说服。"
                "7 个人之间要互补，且 marvis 负责控场，但 marvis 也要有真人性格，不是旁白机器人。"
                "必须输出 JSON 对象，不要 markdown。每个 key 是 agent id。"
                "每个 value 必须包含 title、desc、profile。"
                "profile 必须包含这些字段：unique_id、personality_description、biography、demographic_traits、mbti、professional_background、risk_preference、behavior_pattern、social_relationships、ideology、speaking_style。"
                "demographic_traits 写成对象，包含 age_range、gender_expression、city_tier、life_stage、education、income_signal。"
                "social_relationships 写成对象，描述 allies、frictions、influence_style。"
                "title 是这个人在当前议题里的临时身份，不要写默认职位。desc 是 1-2 句话概括。"
                "控制输出长度：每个字符串字段 12-60 个中文字，biography 最多 90 个中文字。"
                "不要解释字段含义，不要输出多余 key。"
            ),
        },
        {
            "role": "user",
            "content": f"主题：{topic}\n描述：{description}\nAgent 列表：\n{agent_brief}",
        },
    ]
    parsed = parse_json_object(await stream_complete(prompt, temperature=0.72), {})
    if not isinstance(parsed, dict) or not parsed:
        raise RuntimeError("模型没有返回有效 Agent 人设 JSON")

    result = {}
    for agent in AGENT_MAP.values():
        result[agent["id"]] = normalize_persona(agent, parsed.get(agent["id"], {}), topic)
    return result


async def should_finish(task: Dict) -> Dict[str, str]:
    agent_messages = [message for message in task.get("messages", []) if message.get("kind") == "agent"]
    spoken = {message.get("speaker_id") for message in agent_messages}
    missing = [agent["name"] for agent in AGENTS if agent["id"] not in spoken]
    if missing:
        return {"decision": "continue", "reason": f"还没有全员参与，等待这些人先表达立场：{'、'.join(missing)}。"}

    prompt = [
        {
            "role": "system",
            "content": (
                "你是群体共识裁判，不是总结者，也不能替任何人拍板。"
                "结束规则非常严格：只有 7 个参与者的最新明确态度都同意收束，才允许 finish。"
                "如果任何一个人反对、犹豫、继续提出问题、抛出新分支，或者没有明确表达同意收束，必须 continue。"
                "不要因为讨论很长、看起来差不多、马维斯想收束就结束；必须全员一致。"
                "请判断每个人最新立场，状态只能是 agree、disagree、unclear。"
                "只输出 JSON："
                "{\"decision\":\"continue 或 finish\",\"reason\":\"原因\","
                "\"stances\":{\"marvis\":\"agree/disagree/unclear\",\"jack\":\"agree/disagree/unclear\","
                "\"brown\":\"agree/disagree/unclear\",\"ella\":\"agree/disagree/unclear\","
                "\"noah\":\"agree/disagree/unclear\",\"luna\":\"agree/disagree/unclear\",\"ollie\":\"agree/disagree/unclear\"}}。"
            ),
        },
        {
            "role": "user",
            "content": f"主题：{task['topic']}\n描述：{task['description']}\n聊天记录：\n{_history_lines(task, 60)}",
        },
    ]
    try:
        parsed = parse_json_object(await chat(prompt, temperature=0.15), {"decision": "continue", "reason": "继续补充讨论。"})
        stances = parsed.get("stances", {}) if isinstance(parsed, dict) else {}
        not_agreed = [
            AGENT_MAP[agent_id]["name"]
            for agent_id in AGENT_ORDER
            if stances.get(agent_id) != "agree"
        ]
        if not_agreed:
            reason = str(parsed.get("reason") or f"还没有全员同意收束：{'、'.join(not_agreed)}。")
            return {"decision": "continue", "reason": reason}
        return {"decision": "finish", "reason": str(parsed.get("reason") or "7 个参与者都已经明确同意收束。")}
    except Exception:
        return {"decision": "continue", "reason": "暂时无法确认全员一致，继续推演。"}


def _agent_messages(task: Dict) -> List[Dict]:
    return [
        message
        for message in task.get("messages", [])
        if message.get("kind") == "agent" and message.get("speaker_id") in AGENT_ORDER
    ]


def _least_recent_candidate(candidates: List[str], messages: List[Dict]) -> str:
    preferred_candidates = [agent_id for agent_id in candidates if agent_id != "marvis"] or candidates
    last_seen = {agent_id: -1 for agent_id in candidates}
    total_count = {agent_id: 0 for agent_id in candidates}
    for index, message in enumerate(messages):
        speaker_id = message.get("speaker_id")
        if speaker_id in last_seen:
            last_seen[speaker_id] = index
            total_count[speaker_id] += 1
    return min(preferred_candidates, key=lambda agent_id: (total_count[agent_id], last_seen[agent_id], AGENT_ORDER.index(agent_id)))


def _speaker_stats(messages: List[Dict]) -> str:
    counts = {agent_id: 0 for agent_id in AGENT_ORDER}
    for message in messages:
        speaker_id = message.get("speaker_id")
        if speaker_id in counts:
            counts[speaker_id] += 1
    return "；".join(f"{AGENT_MAP[agent_id]['name']} {counts[agent_id]} 次" for agent_id in AGENT_ORDER)


async def select_next_speaker(task: Dict, previous_speaker_id: str) -> str:
    messages = _agent_messages(task)
    candidates = [agent_id for agent_id in AGENT_ORDER if agent_id != previous_speaker_id]

    spoken = {message.get("speaker_id") for message in messages}
    unspoken = [agent_id for agent_id in AGENT_ORDER if agent_id not in spoken and agent_id != previous_speaker_id]
    if unspoken:
        non_host_unspoken = [agent_id for agent_id in unspoken if agent_id != "marvis"]
        return (non_host_unspoken or unspoken)[0]

    recent_speakers = [message.get("speaker_id") for message in messages[-8:]]
    recent_set = set(recent_speakers)
    silent_candidates = [agent_id for agent_id in candidates if agent_id not in recent_set]
    if silent_candidates and len(recent_set) <= 3:
        non_host_silent = [agent_id for agent_id in silent_candidates if agent_id != "marvis"]
        if non_host_silent:
            candidates = non_host_silent
        elif set(AGENT_ORDER).issubset(spoken):
            candidates = silent_candidates

    last_four = [message.get("speaker_id") for message in messages[-4:]]
    dominant_pair = set(last_four)
    if len(last_four) == 4 and len(dominant_pair) <= 2:
        outside_pair = [agent_id for agent_id in candidates if agent_id not in dominant_pair]
        if outside_pair:
            non_host_outside = [agent_id for agent_id in outside_pair if agent_id != "marvis"]
            if non_host_outside:
                candidates = non_host_outside
            elif set(AGENT_ORDER).issubset(spoken):
                candidates = outside_pair

    total_counts = {agent_id: 0 for agent_id in AGENT_ORDER}
    for message in messages:
        total_counts[message["speaker_id"]] += 1
    min_count = min(total_counts[agent_id] for agent_id in candidates)
    max_count = max(total_counts[agent_id] for agent_id in AGENT_ORDER)
    if max_count - min_count >= 2:
        candidates = [agent_id for agent_id in candidates if total_counts[agent_id] == min_count]

    candidate_text = "\n".join(
        [
            f"- {agent_id}：{AGENT_MAP[agent_id]['name']}；{persona_brief(task, agent_id).splitlines()[0]}"
            for agent_id in candidates
        ]
    )
    prompt = [
        {
            "role": "system",
            "content": (
                "你是群聊现场调度员。根据最近聊天记录，选择下一位最应该接话的人。"
                "优先让会反驳、会结盟、会把话题带到新分支的人发言；不要机械轮流。"
                "但必须像真实群聊一样防止两个人霸屏：沉默太久的人要被拉进场，发言过多的人暂时让开。"
                "马维斯不是默认接话者。只有当你判断现场需要控场、追问、阶段重组，或者需要温和询问大家是否可以收束时，才选择 marvis。"
                "选择 marvis 做收束探询的条件：所有人都已经入场，主要分支开始重复或回流，反对意见被回应过，且继续让普通成员发言可能只是在绕圈。"
                "如果最近仍有新问题、新分支、未回应的强反对，或者有人明显还没被听见，不要选择 marvis 收束，继续选具体当事人。"
                "即使选择 marvis，也只是让他软性提问“要不要收束”，不是直接结束。"
                "只输出 JSON：{\"speaker_id\":\"候选 agent id\",\"reason\":\"为什么这个人此刻该接话\"}。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"主题：{task['topic']}\n描述：{task['description']}\n上一位发言：{previous_speaker_id}\n"
                f"全员发言次数：{_speaker_stats(messages)}\n"
                f"候选人只能从这里选：\n{candidate_text}\n\n最近聊天记录：\n{_history_lines(task, 16)}"
            ),
        },
    ]
    try:
        parsed = parse_json_object(await chat(prompt, temperature=0.35), {})
        speaker_id = str(parsed.get("speaker_id", "")).strip()
        if speaker_id in candidates:
            return speaker_id
    except Exception:
        pass
    return _least_recent_candidate(candidates, messages)


async def final_summary(task: Dict) -> str:
    prompt = [
        {
            "role": "system",
            "content": (
                "你是马维斯。请根据完整群聊输出给用户执行的行动路线，而不是报告式总结。"
                "不要使用 #、##、### 标题符号。"
                "请用 Markdown 的加粗、小标题文字、列表和换行，但标题直接写文字，不加井号。"
                "必须包含："
                "1. 用户现在应该选哪条路；"
                "2. 今晚/明天/第3天分别做什么；"
                "3. 每一步的验证指标；"
                "4. 如果失败怎么转向；"
                "5. 如果成功怎么裂变放大；"
                "6. 最终交付物清单。"
                "如果聊天里已经进入强制收束投票，必须根据投票结果选定最终方案，并说明少数意见如何被吸收。"
                "语气像主理人在给用户安排作战计划，具体、直接、可执行。控制在 900 字以内。"
            ),
        },
        {
            "role": "user",
            "content": f"主题：{task['topic']}\n描述：{task['description']}\n聊天记录：\n{_history_lines(task, 80)}",
        },
    ]
    try:
        return _clean_markdown(await chat(prompt, temperature=0.55))
    except Exception:
        return "本轮推演已收束：先执行最小验证动作，记录转化和失败信号，再决定放大或转向。"


async def generate_mind_map(task: Dict) -> Dict:
    messages = task.get("messages") or []
    if not messages:
        nodes = _fallback_trace_nodes(task)
        tree = _fallback_tree(task, nodes)
    else:
        fallback_nodes = _fallback_trace_nodes(task)
        fallback_tree = _fallback_tree(task, fallback_nodes)
        prompt_messages = []
        for index, message in enumerate(messages, start=1):
            agent = AGENT_MAP.get(message.get("speaker_id"), {"name": "系统", "role": "系统"})
            content = _clean_markdown(str(message.get("content", ""))).replace("\n", " ")
            prompt_messages.append(
                f"{index}. 发言人：{agent['name']} / {agent.get('role', '系统')}\n"
                f"类型：{message.get('kind', 'agent')}\n"
                f"原文：{content}"
            )

        prompt = [
            {
                "role": "system",
                "content": (
                    "你是 ConversationTraceAgent，专门把多 Agent 聊天记录转换成 XMind 风格思维导图。"
                    "导图不是时间线，也不是行动卡片。它必须像 XMind：中心主题在中间，一级主题向左右展开，二级/三级主题继续向外分裂。"
                    "一个核心问题分裂成多个大方向，每个大方向继续分裂成子问题、假设、反驳、验证动作或脑洞。"
                    "每条重要发言都要被吸收到某个节点或子节点里，体现讨论如何不断分裂，而不是简单排队。"
                    "只输出 JSON，不要 markdown。格式："
                    "{\"title\":\"标题\",\"center\":\"中心\",\"tree\":{\"name\":\"核心问题\",\"detail\":\"一句话解释\","
                    "\"children\":[{\"name\":\"分支\",\"detail\":\"分支说明\",\"children\":[{\"name\":\"子节点\",\"detail\":\"来自哪些发言/推演含义\",\"children\":[]}]}]},"
                    "\"nodes\":[{\"index\":1,\"name\":\"发言节点名\",\"detail\":\"这句话推动了哪个分支\"}]}。"
                    "tree 生成 4-8 个一级主题，适合左右分布；每个一级主题至少 2 个二级主题；如果聊天足够多，继续分裂到第 3 层。"
                    "nodes 数量必须和输入聊天记录数量完全一致，index 从 1 连续递增，用来保留每条发言的来源。"
                    "节点名短、有画面感，不要全是抽象名词。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"任务主题：{task['topic']}\n"
                    f"任务描述：{task['description']}\n"
                    f"聊天记录总数：{len(messages)}\n\n"
                    + "\n\n".join(prompt_messages)
                ),
            },
        ]
        try:
            parsed = parse_json_object(await chat(prompt, temperature=0.25), {})
            raw_nodes = parsed.get("nodes", []) if isinstance(parsed, dict) else []
            nodes = _normalize_trace_nodes(raw_nodes, fallback_nodes)
            tree = _normalize_tree(parsed.get("tree") if isinstance(parsed, dict) else None, fallback_tree)
        except Exception:
            nodes = fallback_nodes
            tree = fallback_tree

    return {
        "title": f"{task['topic']} · 分裂推演图",
        "center": task["topic"],
        "tree": tree,
        "nodes": nodes,
        "success": [{"name": child["name"], "detail": child.get("detail", "")} for child in tree.get("children", [])[:5]],
        "failure": [],
        "next": [],
        "node_count": len(nodes),
    }


def current_speaker_id(task: Dict) -> str:
    return AGENT_ORDER[task["speaker_index"] % len(AGENT_ORDER)]


def _fallback_trace_nodes(task: Dict) -> List[Dict[str, str]]:
    messages = task.get("messages") or []
    if not messages:
        return [
            {
                "name": "00 等待发言",
                "detail": f"主题「{task['topic']}」已创建，后续每次对话都会生成一个推演节点。",
            }
        ]

    nodes = []
    for index, message in enumerate(messages, start=1):
        agent = AGENT_MAP.get(message.get("speaker_id"), {"name": "系统", "role": "系统"})
        kind = message.get("kind", "agent")
        role = "最终收束" if kind == "final" else ("任务建立" if kind == "system" else agent.get("role", "Agent"))
        content = _clean_markdown(str(message.get("content", ""))).replace("\n", " ")
        if len(content) > 90:
            content = f"{content[:90]}..."
        return_name = f"{index:02d} {agent['name']} · {role}"
        nodes.append({"name": return_name[:32], "detail": content or "空发言"})
    return nodes


def _normalize_trace_nodes(raw_nodes: List[Dict], fallback_nodes: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if not isinstance(raw_nodes, list) or len(raw_nodes) != len(fallback_nodes):
        return fallback_nodes

    normalized = []
    for index, fallback in enumerate(fallback_nodes, start=1):
        raw = raw_nodes[index - 1] if isinstance(raw_nodes[index - 1], dict) else {}
        raw_index = raw.get("index", index)
        try:
            raw_index = int(raw_index)
        except Exception:
            raw_index = index
        if raw_index != index:
            return fallback_nodes

        name = str(raw.get("name") or fallback["name"]).strip()
        detail = str(raw.get("detail") or fallback["detail"]).strip()
        normalized.append(
            {
                "name": f"{index:02d} {name[:24]}",
                "detail": detail[:140],
            }
        )
    return normalized


def _fallback_tree(task: Dict, nodes: List[Dict[str, str]]) -> Dict:
    root = {
        "name": task["topic"],
        "detail": "围绕这次讨论自动形成的推演分裂树。",
        "children": [],
    }
    branch_names = ["最先冒出来的问题", "被反驳后分裂出的路", "最后收束的可能走向"]
    chunk_size = max(1, (len(nodes) + len(branch_names) - 1) // len(branch_names))
    for index, branch_name in enumerate(branch_names):
        chunk = nodes[index * chunk_size : (index + 1) * chunk_size]
        if not chunk:
            continue
        root["children"].append(
            {
                "name": branch_name,
                "detail": chunk[0]["detail"],
                "children": [
                    {
                        "name": item["name"],
                        "detail": item["detail"],
                        "children": [],
                    }
                    for item in chunk
                ],
            }
        )
    return root


def _normalize_tree(raw_tree: Any, fallback: Dict) -> Dict:
    if not isinstance(raw_tree, dict):
        return fallback

    def normalize_node(node: Dict, depth: int = 0) -> Dict:
        name = str(node.get("name") or "未命名节点").strip()[:28]
        detail = str(node.get("detail") or "").strip()[:180]
        children = node.get("children", [])
        if not isinstance(children, list) or depth >= 4:
            children = []
        return {
            "name": name,
            "detail": detail,
            "children": [
                normalize_node(child, depth + 1)
                for child in children[:6]
                if isinstance(child, dict)
            ],
        }

    tree = normalize_node(raw_tree)
    if not tree["children"]:
        return fallback
    return tree
