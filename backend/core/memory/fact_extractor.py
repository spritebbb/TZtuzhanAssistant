"""更强 LLM 事实抽取：升级五元组提取 + 用户画像提炼 + 关系事实。

相对原 triple_memory.py 的改进：
- 更严格的提取 prompt（含置信度、来源、去重指令）
- 结构化输出新增「置信度」和「类别」字段
- 新增 extract_profile 用于画像提炼
- 新增 reconcile 用于冲突解决（新旧事实矛盾时合并）
"""
import json
from datetime import datetime

from ..config import config
from ..llm import chat
from ..log import logger
from ..userdb import db

EXTRACT_PROMPT = """你是一个专业的中文事实抽取助手。从以下对话中提取有价值的事实性五元组：

(主体, 主体类型, 谓词, 客体, 客体类型, 置信度, 类别)

## 类型可选
人物 / 地点 / 组织 / 物品 / 概念 / 时间 / 事件 / 活动 / 动物

## 类别可选
preference（偏好/喜好/厌恶）/ attribute（属性/特质）/ relationship（关系/人际）/
experience（经历/经验）/ plan（计划/约定/承诺）/ event（事件/事实）

## 提取规则
1. 只提取事实性信息：具体行为、实体关系、状态、属性、偏好、需求、计划、约定
2. 过滤：比喻/拟人/夸张、假设/想象、纯情感表达（"我很开心"）、赞美/调侃、闲聊废话
3. 一个句子可以提取多个五元组
4. 主体通常是"用户"或"{persona_name}"，客体是具体的事物
5. 置信度：high（明确说出的） / medium（能合理推断的） / low（猜测的，不重要的不提取）
6. 每个五元组输出务必简洁、客观

## 示例
输入：用户说：我喜欢下雨天养猫，上周刚买了新猫粮
输出：
[
  ["用户", "人物", "喜欢", "下雨天", "概念", "high", "preference"],
  ["用户", "人物", "养", "猫", "动物", "high", "preference"],
  ["用户", "人物", "最近买了", "新猫粮", "物品", "high", "event"]
]

只输出 JSON 数组，不要其他任何内容。
"""


async def extract_triples(
    text: str,
    *,
    mock: bool = False,
    persona_name: str = "菟菚",
) -> list[list[str]]:
    """从文本中提取结构化五元组。返回 [subject, st, predicate, obj, ot, confidence, category] 列表。"""
    if mock:
        return [["用户", "人物", "喜欢", "测试", "概念", "medium", "preference"]]
    try:
        resp = await chat(
            [
                {
                    "role": "system",
                    "content": EXTRACT_PROMPT.format(persona_name=persona_name),
                },
                {"role": "user", "content": text},
            ],
            temperature=0.3,
            max_tokens=600,
        )
        triples = _parse_triples(resp)
        return triples[:12]
    except Exception:
        logger.exception("[事实抽取] 提取失败")
        return []


def _parse_triples(text: str) -> list[list[str]]:
    """解析 LLM 返回的 JSON 五元组数组（兼容旧版 5 字段格式和新版 7 字段格式）。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    result = None
    try:
        result = json.loads(cleaned)
    except Exception:
        m = __import__("re").search(r"\[.*\]", cleaned, __import__("re").DOTALL)
        if m:
            try:
                result = json.loads(m.group())
            except Exception:
                return []
    if not isinstance(result, list):
        return []
    valid = []
    for item in result:
        if isinstance(item, list) and len(item) >= 5 and all(isinstance(x, str) for x in item[:5]):
            # 补齐元组到 7 字段
            item = list(item)
            while len(item) < 7:
                item.append("medium" if len(item) == 5 else "other")
            valid.append([x.strip() for x in item[:7]])
    return valid[:32]


async def extract_profile(text: str, *, mock: bool = False) -> list[dict]:
    """从对话中提取用户画像信息（偏好/习惯/个性/经历）。
    
    返回 [{"category": "喜好/习惯/个性/经历/其他", "content": "描述", "confidence": "high/medium/low"}, ...]
    """
    if mock or not text.strip():
        return []
    prompt = (
        "从以下对话中提取关于对方（用户）的画像信息：喜好、厌恶、习惯、个性、经历。\n"
        "只输出 JSON 数组，每个元素：{\"category\": \"喜好|厌恶|习惯|个性|经历|其他\", "
        "\"content\": \"具体描述\", \"confidence\": \"high|medium|low\"}\n"
        "要求：只提取确有依据的信息，不要臆测。最多 5 条。没有则输出 []。\n"
        f"对话：{text[:2000]}"
    )
    try:
        resp = await chat(
            [{"role": "system", "content": "只输出 JSON 数组，不要任何解释。"}, {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=400,
        )
        data = json.loads(_strip_json_fence(resp))
        return data[:5] if isinstance(data, list) else []
    except Exception:
        logger.warning("[画像] 提取失败")
        return []


async def reconcile(user_id: str, existing: list[str], new_text: str, *, mock: bool = False) -> list[str]:
    """冲突解决：新信息与旧事实矛盾时，用 LLM 决定保留/合并/替换。
    
    existing: 已有事实列表
    new_text: 新消息
    返回合并后的事实列表。
    """
    if mock or not new_text.strip() or not existing:
        return existing
    prompt = (
        "你是一名记忆管理员。下面是已知的关于某人的事实，以及一条新消息。\n"
        "如果新消息与已有事实冲突（矛盾/过时/被推翻），输出更新后的完整事实列表。\n"
        "如果新消息只是补充，在原有事实基础上合并。\n"
        "如果新消息与已有事实无关，保持原列表不变。\n"
        "只输出 JSON 字符串数组，不要其他文字。\n\n"
        f"已有事实：{json.dumps(existing, ensure_ascii=False)}\n"
        f"新消息：{new_text}\n"
    )
    try:
        resp = await chat(
            [{"role": "system", "content": "只输出 JSON 数组，不要任何解释。"}, {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=400,
        )
        data = json.loads(_strip_json_fence(resp))
        return data if isinstance(data, list) and all(isinstance(x, str) for x in data) else existing
    except Exception:
        logger.warning("[冲突解决] LLM 调用失败，保留原事实")
        return existing


def _strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    return cleaned
