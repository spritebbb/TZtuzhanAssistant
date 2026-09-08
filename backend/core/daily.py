"""每日总结任务 + 长期事实提炼。

- 好感度每日判定（聊爱好/尊重/轻视 + 称呼提取）
- 长期记忆的事实提炼（facts 表，带去重）
由 affection.on_message 跨天回滚或 pipeline 惰性触发。
"""
import asyncio
import json
from datetime import date, datetime, timedelta

from . import affection
from .llm import chat
from .log import logger
from .pipeline import clean_address
from .userdb import db, list_facts

JUDGE_PROMPT = """你是「菟菚」的好感度管理员。根据以下某用户与菟菚昨天的对话记录，判断并只输出 JSON：
1) hobby：用户是否聊了自己的爱好？（是→1，否→0）
2) respect：用户是否尊重菟菚的喜好（如避开火、回应植物意象、不强迫）？（是→1，否→0）
3) dismiss：用户是否有轻视、不重视菟菚的态度？（是→1，否→0）
4) address：如果用户明确表达了想被怎么称呼，给出该称呼；否则留空字符串。
5) care：用户是否有关心菟菚的言行（如问她累不累、注意休息、担心她等）？（是→1，否→0）
6) deep_chat：昨天是否有深度/走心的对话（如倾诉心事、分享感受、坦诚交流）？（是→1，否→0）

输出格式（不要任何其他内容）：
{"hobby": 0, "respect": 0, "dismiss": 0, "address": "", "care": 0, "deep_chat": 0}
"""

FACT_PROMPT = """你是记忆提取员。根据下面的对话，提取两样东西，只输出一个 JSON：
每条 facts 还应提供 source_message_ids（直接支持该事实的 user 消息编号数组，禁止引用 assistant 或编造编号）。如果是在再次提到已有事实，提供 existing_fact_id，且 content 必须逐字使用该已有事实的内容；不要为重复提及另建事实。重要性不由模型打分。
1) facts：值得记住的关于用户的事实——喜好、习惯、工作/生活状态、约定承诺、关系进展、重要经历、家人朋友等。**不要**把毫无后续价值的一次性话题记进去（比如"今天吃了饺子"，除非它反映长期习惯）。每条包含：content（以「用户」开头的一句短话）、confidence（0-1）、conflicts_with_fact_id（与已有事实明确矛盾时填其编号，否则为 null）、retention_days（保留天数）。明确长期稳定的身份/偏好/习惯/重要经历填 null；带“最近/当前/临时/这周/正在”等时效的状态填 7-180 的整数，期限越明确越贴近实际。用户明确说出的事实可为 0.9，依据间接或存在推断时不得高于 0.7。最多 5 条。不得自行覆盖或删除冲突事实。
2) style：对「用户说话风格」的简要描述（1-2 句），包括：句子长短、是否爱用语气词/表情、常用口头禅、语气是直接还是委婉、爱不爱开玩笑等。

输出格式（不要任何其他内容）：
{"facts": [{"content":"用户喜欢下雨天","confidence":0.9,"conflicts_with_fact_id":null,"retention_days":null}, {"content":"用户最近在准备考试","confidence":0.9,"conflicts_with_fact_id":12,"retention_days":30}], "style": "对方说话简短直接，常用'啊'和'哈'，喜欢发短句和表情。"}
没有值得记的事实就输出 {"facts": [], "style": "..."}
"""

DIARY_PROMPT = """你是菟菚，在当天聊天结束后写一小段只给自己看的私人日记。
根据对话，只输出 JSON：{"content":"日记正文","mood":"心情标签"}
要求：第一人称，90-220 个汉字；写出嘴上克制或毒舌、心里真实在意的轻微反差；最多自然提一件对话中的具体小事；不编造没发生的经历、论文或现实接触；不写系统、模型、提示词；不要把对话逐条总结。"""

RESEARCH_PROMPT = """你是研究员菟菚，课题名《观察人类：以你为样本》。
根据最近七篇私人日记写一份阶段研究记录，只输出 JSON：
{"title":"简短标题","content":"正文"}
正文 180-420 个汉字，包含一个观察、一个仍不确定的假设、一个接下来想留意的小问题。语气聪明、有一点腹黑，但尊重对方；只使用材料中确有依据的内容，不做心理诊断，不编造事实。"""

PROMISE_PROMPT = """你是约定记录员。根据对话，找出「双方明确许下的约定、承诺或计划中要做的事」，只输出 JSON：
{"promises": [{"content": "一句短话概括约定（如：用户答应周五把新游戏 demo 发给菟菚看）", "follow_up": "YYYY-MM-DD 或空字符串", "owner": "user 或 assistant", "due_at": "YYYY-MM-DD 或空字符串"}]}
规则：
- 只记明确说出口的约定（"明天给你看""周末一起""回头跟你说那件事"都算，但必须有具体事项）；
- 客套话、已经做完的事、单方面的心愿不算；
- owner 记许下这件事的人：用户许的填 user，菟菚许下的承诺填 assistant（分不清算 user）；
- due_at 只在有明确期限（"周五之前""月底前"）时填；
- follow_up 填最适合自然跟进的日期（约定当天或次日），判断不了就留空；
- 没有约定就输出 {"promises": []}。"""

TERMS_PROMPT = """你是语言观察员。根据对话，找出「两人之间反复使用的口头禅、黑话、内部梗」，只输出 JSON：
{"terms": [{"term": "词或短句（≤10字）", "category": "catchphrase 或 slang", "meaning": "含义（黑话/梗才需要，口头禅留空）"}]}
规则：
- 只记双方真实说过、且像是会再用的（口头禅、固定叫法、两人之间才懂的梗）；
- 一次性的话题词、普通网络流行语、礼貌用语不算；
- 最多 3 条；没有就输出 {"terms": []}。"""


def _parse_json(resp: str) -> dict:
    """解析 LLM 返回的 JSON，容忍常见噪声；截断时尽力补全。"""
    text = resp.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if text.startswith("json"):
        text = text[4:].strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    # 截断修复：LLM（尤其推理模型）可能因 max_tokens 被切断，
    # 尝试补全未闭合的引号/数组/对象后再解析。
    repaired = _repair_truncated_json(text)
    if repaired is not None:
        return repaired
    return {}


def _repair_truncated_json(text: str) -> dict | None:
    """尽力修复被截断的 JSON（补全引号/方括号/花括号），失败返回 None。"""
    t = text
    try:
        data = json.loads(t)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    candidates = []
    # 字符串值被切断（引号数成奇数）：补闭合引号，并连同括号一起闭合
    if t.count('"') % 2 == 1:
        candidates.append(t + '"')
        candidates.append(t + '"]}')
        candidates.append(t + '"]')
        candidates.append(t + '"}')
    for extra in ("]}", "}]", "]", "}"):
        candidates.append(t + extra)
    seen = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        try:
            data = json.loads(cand)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            continue
    return None


async def run_daily_batch(user_id: str, day: date) -> None:
    """昨日好感度判定 + 事实提炼。执行完成才推进 last_batch_date（失败可重试）。"""
    from .fact_decay import decay_expired_facts_async
    from .userdb import kv_get as _kv_get, kv_set as _kv_set
    from .persona_profiles import persona_name_for_user_id

    persona_name = persona_name_for_user_id(user_id)

    # 必须位于 done_key 与空日早退之前：批次重复触发或当天无消息时，已到期且
    # 未固定的事实仍应收敛；清理本身幂等，失败不阻塞其余每日总结。
    try:
        await decay_expired_facts_async(user_id)
    except Exception:
        logger.exception("[每日总结] {} 的过期事实清理失败", user_id)

    # G03：到点的开放问题做一次有限度复查（JOB-1 认领，最多 2 次、间隔 24h）；
    # 无新证据不发假进展，有新证据只挂候选，表达仍走主动仲裁与共享额度。
    try:
        from .open_questions import research_due_questions

        await asyncio.to_thread(research_due_questions, user_id)
    except Exception:
        logger.exception("[每日总结] {} 的开放问题复查失败", user_id)

    # 幂等防重跑：同一天只执行一次（schedule 按 key 去重，这里再兜一道）
    done_key = f"daily_batch:{day.isoformat()}"
    if _kv_get(user_id, done_key):
        db.set_batch_date(user_id, day.isoformat())
        return
    rows = db.messages_between(user_id, day, day)
    if not rows:
        db.set_batch_date(user_id, day.isoformat())
        return
    # P2-01/P2-02：对话语义关系事件的夜间提取（确定性词表、带源 message id
    # 幂等入账，ledger 唯一键防重复计分）。只记高置信的自愿披露；冒犯类
    # 不在此处扣分——必须经对话内明确确认才入账。
    try:
        from . import affection as _aff

        for row in rows:
            if row["role"] == "user" and _aff.check_sharing(str(row["content"] or "")):
                _aff.apply_relationship_event(user_id, int(row["id"]), "user_self_disclosure")
                break  # 每日批处理最多记一条披露
    except Exception:
        logger.exception("[每日总结] 语义关系事件提取失败（不影响批次）")
    transcript = "\n".join(f"{r['role']}: {r['content']}" for r in rows[-60:])
    data = {}
    llm_ok = False
    try:
        resp = await chat(
            [
                {"role": "system", "content": JUDGE_PROMPT.replace("菟菚", persona_name)},
                {"role": "user", "content": f"昨天的对话：\n{transcript}"},
            ],
            task="batch_other",
        )
        data = _parse_json(resp)
        llm_ok = True
    except Exception:
        logger.exception("[每日总结] {} 的好感度判定失败", user_id)

    if data.get("hobby"):
        db.update_affection(user_id, affection.HOBBY_BONUS, "用户聊自己的爱好")
    if data.get("respect"):
        db.update_affection(user_id, affection.RESPECT_BONUS, f"尊重{persona_name}的喜好")
    if data.get("dismiss"):
        db.update_affection(user_id, affection.DISMISS_PENALTY, "轻视/不重视")
    if data.get("care"):
        db.update_affection(user_id, affection.CARE_BONUS, f"关心{persona_name}")
    if data.get("deep_chat"):
        db.update_affection(user_id, affection.DEEP_CHAT_BONUS, "深度/走心对话")

    addr = (data.get("address") or "").strip()
    u = db.get_user(user_id)
    if u is None:
        return
    if addr and not u["nickname_pref"] and not affection.check_bad_address(addr):
        db.set_nickname(user_id, clean_address(addr)[:12])

    await extract_facts(user_id, day)
    await extract_promises(user_id, day, transcript)
    await extract_terms(user_id, day, transcript)
    await write_daily_diary(user_id, day, transcript)
    try:
        from .reunion import close_after_daily

        close_after_daily(user_id, day)
    except Exception:
        logger.exception("[每日总结] 重逢日记收束失败（不影响批次）")
    await maybe_write_research_report(user_id)
    # 仅 LLM 判定成功才标记当日已完成；失败保留 done_key 空缺，下次可重试补判
    if llm_ok:
        _kv_set(user_id, done_key, "1")
        db.set_batch_date(user_id, day.isoformat())


def _fallback_diary(day: date, transcript: str) -> tuple[str, str]:
    """LLM 暂时不可用时也不让日记断档；只引用最后一句用户原话，拒绝编造。"""
    last_user = ""
    for line in reversed(transcript.splitlines()):
        if line.startswith("user:"):
            last_user = line.split(":", 1)[1].strip()[:80]
            break
    detail = f"他说了「{last_user}」" if last_user else "今天有过一段不长的聊天"
    return (
        f"{day.isoformat()}。{detail}。我嘴上大概还是那副不太好哄的样子，"
        "不过愿意把这件小事记下来，本身就已经很说明问题了。其余的，等明天再观察。",
        "平静",
    )


async def write_daily_diary(user_id: str, day: date, transcript: str) -> dict:
    """生成并幂等保存一篇日记；生成失败则写保守的事实型兜底，保证不断档。"""
    from .userdb import get_diary, save_diary

    existing = get_diary(user_id, day.isoformat())
    if existing:
        return existing
    content = ""
    mood = ""
    try:
        from .persona_profiles import persona_name_for_user_id

        persona_name = persona_name_for_user_id(user_id)
        resp = await chat(
            [
                {"role": "system", "content": DIARY_PROMPT.replace("菟菚", persona_name)},
                {"role": "user", "content": f"日期：{day.isoformat()}\n当天对话：\n{transcript}"},
            ],
            temperature=0.65,
            max_tokens=520,
            task="batch_diary",
        )
        data = _parse_json(resp)
        content = str(data.get("content") or "").strip()
        mood = str(data.get("mood") or "").strip()
    except Exception:
        logger.warning("[日记] {} 的 {} 生成失败，使用事实型兜底", user_id, day.isoformat())
    if not content:
        content, mood = _fallback_diary(day, transcript)
    from .output_hygiene import HygieneContext, protect_visible_text

    fallback_content, fallback_mood = _fallback_diary(day, transcript)
    content = protect_visible_text(
        content,
        context=HygieneContext(kind="diary", source_namespace="daily"),
        fallback=fallback_content,
    ).text
    if not mood:
        mood = fallback_mood
    mood = protect_visible_text(
        mood,
        context=HygieneContext(kind="diary_mood", source_namespace="daily"),
        fallback=fallback_mood,
    ).text
    diary_id = save_diary(user_id, day.isoformat(), content, mood)
    return {
        "id": diary_id,
        "date": day.isoformat(),
        "content": content[:1200],
        "mood": mood[:24],
    }


async def maybe_write_research_report(user_id: str) -> dict | None:
    """每累计七篇新日记产出一份阶段研究记录，失败留到下次继续尝试。"""
    from .userdb import list_diaries, list_research_reports, save_research_report

    diaries = list_diaries(user_id, limit=365)
    reports = list_research_reports(user_id, limit=100)
    if len(diaries) < (len(reports) + 1) * 7:
        return None
    batch = list(reversed(diaries[:7]))
    period = f"{batch[0]['date']}~{batch[-1]['date']}"
    transcript = "\n\n".join(f"{d['date']}（{d['mood']}）：{d['content']}" for d in batch)
    try:
        from .persona_profiles import persona_name_for_user_id

        persona_name = persona_name_for_user_id(user_id)
        resp = await chat(
            [
                {"role": "system", "content": RESEARCH_PROMPT.replace("菟菚", persona_name)},
                {"role": "user", "content": transcript},
            ],
            temperature=0.55,
            max_tokens=800,
            task="batch_diary",
        )
        data = _parse_json(resp)
        title = str(data.get("title") or "").strip()
        content = str(data.get("content") or "").strip()
        if not content:
            return None
        from .output_hygiene import HygieneContext, protect_visible_text

        content = protect_visible_text(
            content,
            context=HygieneContext(kind="research_report", source_namespace="daily"),
            fallback="这阶段的材料还不够稳妥，我先把结论留到下一次记录。",
        ).text
        title = protect_visible_text(
            title,
            context=HygieneContext(kind="research_title", source_namespace="daily"),
            fallback="阶段观察",
        ).text
        if not content:
            return None
        report_id = save_research_report(user_id, period, title, content)
        return {"id": report_id, "period": period, "title": title, "content": content}
    except Exception:
        logger.warning("[研究课题] {} 阶段报告生成失败，保留到下次重试", user_id)
        return None


async def extract_promises(user_id: str, day: date, transcript: str) -> int:
    """从当天对话提炼「约定」进 promises 表（C6）。失败静默，返回新增条数。"""
    from .userdb import save_promise

    if not transcript.strip():
        return 0
    try:
        from .persona_profiles import persona_name_for_user_id

        persona_name = persona_name_for_user_id(user_id)
        resp = await chat(
            [
                {"role": "system", "content": PROMISE_PROMPT.replace("菟菚", persona_name)},
                {"role": "user", "content": f"今天的日期：{day.isoformat()}\n对话记录：\n{transcript}"},
            ],
            temperature=0.2,
            max_tokens=300,
            task="extract",
        )
        data = _parse_json(resp)
    except Exception:
        logger.warning("[约定提炼] {} 的约定提炼失败，次日重试", user_id)
        return 0
    items = data.get("promises") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return 0
    added = 0
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        follow_up = str(item.get("follow_up") or "").strip()
        if not content:
            continue
        owner = str(item.get("owner") or "user").strip()
        if owner not in ("user", "assistant"):
            owner = "user"
        due_at = str(item.get("due_at") or "").strip() or None
        # owner=assistant 且无可执行动作 → 只记为叙事约定，不后台执行任何工具（14.7）
        action_kind = "narrative" if owner == "assistant" else None
        if save_promise(user_id, content, follow_up, source=transcript[:200],
                        owner=owner, due_at=due_at, action_kind=action_kind) is not None:
            added += 1
    return added


async def extract_terms(user_id: str, day: date, transcript: str) -> int:
    """从当天对话提炼「共同语言」（口头禅/黑话/内部梗）进 user_terms（D1）。

    user_terms.add_term 自带去重与次数累加——同一梗被反复提到才长大，
    这是「演化幅度控制」的第一道闸（一次性话题进不来）。
    """
    if not transcript.strip():
        return 0
    try:
        resp = await chat(
            [
                {"role": "system", "content": TERMS_PROMPT},
                {
                    "role": "user",
                    "content": f"对话记录：\n{transcript}",
                },
            ],
            temperature=0.2,
            max_tokens=240,
            task="extract",
        )
        data = _parse_json(resp)
    except Exception:
        logger.warning("[共同语言] {} 的口头禅提炼失败，次日重试", user_id)
        return 0
    items = data.get("terms") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return 0
    added = 0
    for item in items:
        if added >= 3:  # 上限按「有效新增」计，空项不占额度
            break
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()
        if not term:
            continue
        category = str(item.get("category") or "catchphrase").strip()
        if category not in ("catchphrase", "slang"):
            category = "catchphrase"
        meaning = str(item.get("meaning") or "").strip()
        if db.add_term(user_id, term, category, meaning):
            added += 1
    return added


async def extract_facts(user_id: str, day: date | None = None) -> None:
    """把值得记住的事实提炼进 facts 表（带去重）。

    day 非空：提炼该日全部对话（每日模式）；
    day 为空：提炼 last_fact_msg_id 之后的新消息（惰性模式，消息太少会跳过）。
    """
    last_id = db.get_last_fact_msg_id(user_id)

    if day is not None:
        rows = db.messages_between(user_id, day, day)
        if not rows:
            return
        transcript = "\n".join(f"[{r['id']}] {r['role']}: {r['content']}" for r in rows[-60:])
        # 用该日最后一条消息的 id 推进游标，避免吞掉今天的新消息。
        # 注意：不能回退游标——若上次惰性提炼已推进到更大 id，本次取 max。
        done = max(rows[-1]["id"], last_id) if rows else last_id
    else:
        rows = db.messages_after(user_id, last_id, 60)
        if len(rows) < 8:  # 太少不值得提炼，省一次调用
            return
        transcript = "\n".join(f"[{r['id']}] {r['role']}: {r['content']}" for r in rows)
        done = rows[-1]["id"]

    try:
        from .persona_profiles import persona_name_for_user_id

        persona_name = persona_name_for_user_id(user_id)
        existing = list_facts(user_id, 100)
        active_fact_ids = {
            int(fact["id"])
            for fact in existing
            if fact["status"] == "active" and fact["surface_policy"] != "never_surface"
        }
        existing_text = "\n".join(
            f"{fact['id']}. {fact['content']}"
            for fact in existing
            if int(fact["id"]) in active_fact_ids
        ) or "（暂无）"
        resp = await chat(
            [
                {"role": "system", "content": FACT_PROMPT.replace("菟菚", persona_name)},
                {
                    "role": "user",
                    "content": f"已有事实（仅用于判断冲突）：\n{existing_text}\n\n对话记录：\n{transcript}",
                },
            ],
            temperature=0.3,
            max_tokens=400,
            task="extract",
        )
        data = _parse_json(resp)
    except Exception:
        # 失败不推进游标（与 profile 策略一致）：这批消息下次仍会重试，
        # 避免 LLM 临时故障导致事实永久丢失。后台任务按 key 去重，不会打爆。
        logger.warning("[事实提炼] {} 的事实提炼失败，游标保留待重试", user_id)
        return

    if isinstance(data, dict):
        facts = data.get("facts") or []
        style = (data.get("style") or "").strip()
    else:
        facts = data if isinstance(data, list) else []
        style = ""
    if isinstance(facts, list):
        for f in facts:
            evidence_ids = []
            if isinstance(f, dict):
                content = str(f.get("content") or "").strip()[:100]
                allowed_ids = {int(r["id"]) for r in rows[-60:] if r["role"] == "user"}
                supplied_ids = f.get("source_message_ids")
                if isinstance(supplied_ids, list):
                    evidence_ids = [i for i in supplied_ids
                                    if type(i) is int and i in allowed_ids]
                existing_id = f.get("existing_fact_id")
                if type(existing_id) is int and existing_id in active_fact_ids:
                    matching = next((old for old in existing
                                     if int(old["id"]) == existing_id and old["content"] == content), None)
                    if matching is not None:
                        from .memory_salience import observe_fact
                        observe_fact(user_id, existing_id, evidence_ids)
                        continue
                try:
                    confidence = float(f.get("confidence", 0.7))
                except (TypeError, ValueError):
                    confidence = 0.7
                try:
                    conflict_id = int(f["conflicts_with_fact_id"]) if f.get("conflicts_with_fact_id") is not None else None
                except (TypeError, ValueError):
                    conflict_id = None
                try:
                    retention_days = int(f["retention_days"]) if f.get("retention_days") is not None else None
                except (TypeError, ValueError):
                    retention_days = None
                if retention_days is not None and not 1 <= retention_days <= 365:
                    retention_days = None
                if conflict_id not in active_fact_ids:
                    conflict_id = None
            else:  # 兼容旧模型/旧测试仍返回字符串数组
                content = str(f).strip()[:100]
                confidence = 0.7
                conflict_id = None
                retention_days = None
            expires_at = (
                (datetime.now() + timedelta(days=retention_days)).isoformat(timespec="seconds")
                if retention_days is not None else None
            )
            source_ids = json.dumps([int(r["id"]) for r in rows], separators=(",", ":"))
            fid = db.add_fact(
                user_id,
                content,
                source_type="conversation_inference",
                source_message_ids=source_ids,
                confidence=confidence,
                expires_at=expires_at,
                conflicts_with_fact_id=conflict_id,
            )
            # 给新事实建稠密向量索引（失败静默）
            if fid is not None and conflict_id is None:
                from .memory_salience import observe_fact
                observe_fact(user_id, fid, evidence_ids, new_fact=True)
                try:
                    import asyncio as _asyncio
                    from .vector_store import index as vec_index

                    await _asyncio.to_thread(vec_index, user_id, fid, content, "facts")
                except Exception:
                    pass
    if style:
        db.set_style(user_id, style[:200])  # 逐渐学习对方说话风格
    db.set_last_fact_msg_id(user_id, done)

    # 复盘识别特殊日子（每天兜底：从这段对话里补录用户明确说过的日子）
    try:
        from .date_memory import extract_from_transcript

        await extract_from_transcript(user_id, transcript)
    except Exception:
        logger.exception("[日子记忆] {} 复盘识别失败", user_id)
