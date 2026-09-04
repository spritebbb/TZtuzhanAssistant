# -*- coding: utf-8 -*-
"""主动性引擎：让菟菚在合适的时候主动发起对话，而不是永远被动等。

设计原则（轻量主动、不骚扰）：
- 只在「用户久未聊天」时考虑主动（默认 6 小时没说话才进入候选）；
- 只对「关系足够近」的用户主动（熟悉及以上，初识阶段不主动找——那会像骚扰）；
- 按状态决定主动内容：情绪好想分享 / 记挂着对方的事（特殊日子、上次话题）/ 单纯关心；
- 严格频率限制：每个用户每天最多主动 1 次，全局有冷却，避免刷屏。

主动消息的投递：本模块只负责「判断 + 生成文本」。会话系统是全局的、不绑定
user_id，因此默认**不主动写会话**，而是提供探测与生成能力，由调用方（前端轮询
端点 / 现有 greeting 通道）在合适时机触发。这样既符合「不骚扰」，也不污染会话。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import random
import time
from pathlib import Path
from typing import TypedDict

from .config import config
from .log import logger
from .llm import chat
from .persona import build_system_prompt
from .state import load_state, stage_of
from .userdb import db, kv_del, kv_get, kv_set

# ---- 阈值（可调）----
_IDLE_HOURS = 6            # 多久没聊才进入主动候选
_MIN_STAGE = "熟悉"        # 最低关系阶段（初识不主动）
_DAILY_MAX_PER_USER = 1    # 每用户每天最多主动 1 次
_GLOBAL_COOLDOWN_SEC = 900  # 全局冷却 15 分钟（避免集中轰炸）
_CHECK_INTERVAL_SEC = 300  # 后台轮询间隔 5 分钟

_STAGE_ORDER = {"初识": 0, "熟悉": 1, "亲密": 2, "恋人": 3}


class ProactiveMessage(TypedDict):
    text: str
    image: str | None


def _message(text: str, image: str | None = None) -> ProactiveMessage:
    return {"text": text, "image": image or None}


def _decode_message(raw: str | None) -> ProactiveMessage | None:
    """兼容旧版 kv 里的纯文本和新版 JSON 消息。"""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _message(raw)
    if not isinstance(data, dict) or not str(data.get("text", "")).strip():
        return _message(raw)
    return _message(str(data["text"]), str(data["image"]) if data.get("image") else None)


def _encode_message(message: ProactiveMessage) -> str:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def _proactive_key(user_id: str, day: str) -> str:
    return f"initiative:{day}:{user_id}"


def _proactive_done_today(user_id: str) -> bool:
    from .proactive_policy import active_done_today

    return active_done_today(user_id)


def _mark_proactive(user_id: str) -> None:
    from .proactive_policy import mark_active_done

    mark_active_done(user_id, "initiative")


def _last_chat_ts(user_id: str) -> float | None:
    ts = db.last_message_ts(user_id)
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


def _eligible_users() -> list[dict]:
    """找出「值得主动找」的用户：久未聊 + 关系够近 + 今天还没主动过。"""
    eligible = []
    try:
        with db._lock:
            rows = db.conn.execute("SELECT user_id FROM users").fetchall()
    except Exception:
        return eligible
    now = time.time()
    for r in rows:
        uid = r["user_id"]
        user = db.get_user(uid)
        if not user:
            continue
        # 关系够近才主动
        if _STAGE_ORDER.get(stage_of(user["affection"] or 0), 0) < _STAGE_ORDER[_MIN_STAGE]:
            continue
        # 今天已经主动过 → 跳过
        if _proactive_done_today(uid):
            continue
        # 没聊过或最近在聊 → 跳过（不骚扰正在聊天的人）
        last = _last_chat_ts(uid)
        if last is not None and now - last < config.proactive_idle_hours * 3600:
            continue
        eligible.append({"user_id": uid, "last_chat_ts": last})
    # 按「越久没聊越优先」排序，最多取 3 个（一次别主动找太多人）
    eligible.sort(key=lambda x: x["last_chat_ts"] or 0)
    return eligible[:3]


def _build_proactive_prompt(user_id: str, *, has_image: bool = False) -> list[dict] | None:
    """拼主动消息的 prompt（含状态帧），失败返回 None。"""
    user = db.get_user(user_id)
    if not user:
        return None
    affection_val = user["affection"] or 0
    sys_prompt = build_system_prompt(
        stage=stage_of(affection_val),
        address=user["nickname_pref"] or "",
        lover_confirm=bool(user["lover_confirm"]),
        first_chat=False,
        affection=affection_val,
        user_id=user_id,
    )
    # 注入当前状态帧，让主动消息也符合她此刻的心情
    try:
        from .behavior import build_behavior_frame

        frame = build_behavior_frame(load_state(user_id))
        sys_prompt += "\n\n## 此刻状态\n" + frame.compose()
    except Exception:
        pass

    now = datetime.datetime.now()
    h = now.hour
    period = "上午" if h < 12 else "下午" if h < 18 else "晚上"
    narrative_hint = ""
    last_chat = _last_chat_ts(user_id)
    if last_chat is not None:
        gap_hours = max(0.0, (time.time() - last_chat) / 3600)
        try:
            from .offline_narrative import collect_offline_context

            narrative = collect_offline_context(user_id, gap_hours, now=now)
            if narrative.mode == "dream" and stage_of(affection_val) in {"初识", "熟悉"}:
                narrative = type(narrative)(
                    mode="research",
                    gap_hours=narrative.gap_hours,
                    recent_lines=narrative.recent_lines,
                    triple_lines=narrative.triple_lines,
                )
            narrative_hint = "\n\n" + narrative.prompt_hint(stage_of(affection_val))
        except Exception as e:
            logger.warning("[主动性] 离线叙事素材整理失败: {}", e)
    image_hint = (
        "\n\n你已经随手做了一张自己的图片准备一起发给对方。"
        "正文只要自然提一句让对方看看，别说提示词、模型、路径或生成过程，也别逐项描述图片。"
        if has_image else ""
    )
    return [
        {"role": "system", "content": sys_prompt},
        {
            "role": "user",
            "content": (
                f"现在是{now.month}月{now.day}日{period}，对方有一阵子没来找你了。"
                "你主动想找他一下。别生硬地问「在干嘛」，就像朋友想起对方一样自然地开个头："
                "可以分享一件小事、说一句你刚想到的话、或者轻轻关心一句。"
                "一两句就够，别长篇大论，别解释，别加括号动作。"
                "语气符合你此刻的心情和你们的熟悉程度，别装熟也别太生分。"
                f"{narrative_hint}"
                f"{image_hint}"
            ),
        },
    ]


async def generate_proactive_message(user_id: str) -> str | None:
    """为某用户生成一条主动消息。返回文本或 None（失败静默）。"""
    msgs = _build_proactive_prompt(user_id)
    if not msgs:
        return None
    try:
        text = await chat(msgs, max_tokens=100, temperature=0.85)
        return text.strip()[:200] or None
    except Exception as e:
        logger.warning("[主动性] LLM 生成失败: {}", e)
        return None


async def _generate_proactive_image(user_id: str) -> str | None:
    """按关系与状态低频生成自拍/涂鸦，返回前端可访问的 /api/images URL。"""
    try:
        from . import imagegen
        from .proactive_media import decide_proactive_image, proactive_image_prompt

        state = load_state(user_id)
        last_chat = _last_chat_ts(user_id)
        gap_hours = max(0.0, (time.time() - last_chat) / 3600) if last_chat else 0.0
        decision = decide_proactive_image(
            stage=state.stage,
            emotion=state.emotion,
            energy=state.energy,
            gap_hours=gap_hours,
            enabled=config.proactive_image_enabled,
            image_service_enabled=imagegen.enabled(),
            chance_percent=config.proactive_image_chance_percent,
            min_mood=config.proactive_image_min_mood,
            roll=random.randrange(100),
        )
        if not decision.create:
            return None
        local_path = await imagegen.generate(proactive_image_prompt(decision.mode))
        if not local_path:
            return None
        filename = Path(local_path).name
        if not filename:
            return None
        return f"/api/images/{filename}"
    except Exception as e:
        logger.warning("[主动性] 主动发图失败，退化为文字: {}", e)
        return None


async def generate_proactive_content(user_id: str) -> ProactiveMessage | None:
    """生成一条完整主动消息；图片失败时自动退化为纯文字。"""
    image = await _generate_proactive_image(user_id)
    msgs = _build_proactive_prompt(user_id, has_image=bool(image))
    if not msgs:
        return None
    try:
        text = await chat(msgs, max_tokens=100, temperature=0.85)
        text = text.strip()[:200]
        return _message(text, image) if text else None
    except Exception as e:
        logger.warning("[主动性] LLM 生成失败: {}", e)
        return None


_last_global_run = 0.0


async def _tick_once() -> int:
    """执行一轮主动检查，返回实际主动的消息条数。"""
    global _last_global_run
    now = time.time()
    if now - _last_global_run < config.proactive_global_cooldown_sec:
        return 0
    _last_global_run = now

    # 归档建议独立于「主动找人」：会话是全局的，只要当前会话过长就提醒，
    # 用首个有记录的用户作为投递归属（单一会话模式下所有消息共享同一 user）。
    try:
        with db._lock:
            row = db.conn.execute("SELECT user_id FROM users LIMIT 1").fetchone()
        if row:
            await maybe_suggest_archive(row["user_id"])
    except Exception as e:
        logger.warning("[主动性] 归档建议检查失败: {}", e)

    users = _eligible_users()
    if not users:
        return 0

    count = 0
    for u in users:
        uid = u["user_id"]
        from .proactive_policy import finish_active_claim, try_claim_active

        claim_token = try_claim_active(uid, "initiative-loop", now=now)
        if not claim_token:
            continue
        from .reset import reset_epoch
        epoch = reset_epoch()
        message = await generate_proactive_content(uid)
        if not message:
            finish_active_claim(uid, claim_token, success=False, source="initiative-loop")
            continue
        text, image = message["text"], message["image"]
        from .reset import epoch_is_current
        if not epoch_is_current(epoch):
            finish_active_claim(uid, claim_token, success=False, source="initiative-loop")
            continue
        # 投递：优先走 delivery hook（若注册，实时推给前端）；否则入队，
        # 等前端轮询 /api/initiative 时取走（离线也不丢失）。
        delivered = False
        try:
            delivered = await _deliver(uid, message)
        except Exception as e:
            logger.warning("[主动性] 投递失败: {}", e)
        if not delivered:
            if not await enqueue_proactive(uid, text, image=image, epoch=epoch):
                finish_active_claim(uid, claim_token, success=False, source="initiative-loop")
                continue
        else:
            # 实时投递成功（不经 enqueue），仍需落库，避免刷新后丢失
            if not await _persist_proactive(uid, text, image=image, epoch=epoch):
                # 已实时送到用户，虽然落库失败也必须消耗额度，避免下一轮再发一遍。
                finish_active_claim(uid, claim_token, success=True, source="initiative-loop")
                count += 1
                continue
        if not epoch_is_current(epoch):
            finish_active_claim(uid, claim_token, success=False, source="initiative-loop")
            continue
        finish_active_claim(uid, claim_token, success=True, source="initiative-loop")
        logger.info("[主动性] 已主动联系 {}（{}）: {}", uid, "实时投递" if delivered else "入队待取", text[:40])
        count += 1
        if count >= 3:
            break
    return count


# ---- 投递通道（可注入）----
_deliver_hook = None  # async (user_id, message) -> bool


def set_deliver_hook(hook) -> None:
    """注册主动消息投递回调。hook 签名：async (user_id, message) -> bool。"""
    global _deliver_hook
    _deliver_hook = hook


async def _deliver(user_id: str, message: ProactiveMessage) -> bool:
    if _deliver_hook is None:
        return False
    try:
        return bool(await _deliver_hook(user_id, message))
    except Exception:
        return False


# ---- 待投递队列（离线消息不丢失）----
# 后台 loop 生成主动消息时用户可能不在线，先把消息持久化到 kv_store，
# 前端轮询 /api/initiative 时取走。每个用户每天最多 1 条，故单 key 即可。
_PENDING_KEY = "initiative:pending"


async def _persist_proactive(
    user_id: str,
    text: str,
    *,
    image: str | None = None,
    epoch: int | None = None,
) -> bool:
    """把主动消息写入当前会话的 messages 表（真正持久化 + 幂等）。

    主动消息是 bot 角色、挂在全局会话（CURRENT_SESSION_ID）下。走
    store.append_proactive_message（async 锁内 + 线程池），既避免阻塞事件
    循环，也保证与其它写路径同锁串行，不会插到用户/菟菚消息中间。
    幂等：最后一条 bot 消息内容相同则跳过，防 SSE 重连/队列残留重复落库。
    """
    try:
        from ..session import store as _store
        from .reset import ResetSuperseded, reset_epoch, user_write_guard

        write_epoch = reset_epoch() if epoch is None else epoch
        async with user_write_guard(write_epoch):
            await _store.append_proactive_message(_store.CURRENT_SESSION_ID, text, image)
        return True
    except ResetSuperseded:
        return False
    except Exception as e:
        logger.warning("[主动性] 主动消息落库失败: {}", e)
        return False


async def enqueue_proactive(
    user_id: str,
    text: str,
    *,
    image: str | None = None,
    epoch: int | None = None,
) -> bool:
    """把一条主动消息放入待投递队列（持久化，跨重启不丢）。

    入队后广播给该 user 的 SSE 订阅者，实现秒级实时推送；同时写入会话
    messages 表（幂等），避免主动消息只在内存/前端展示、刷新后丢失。
    """
    from .reset import epoch_is_current, reset_epoch

    write_epoch = reset_epoch() if epoch is None else epoch
    if not epoch_is_current(write_epoch):
        return False
    message = _message(text, image)
    encoded = _encode_message(message)
    kv_set(user_id, _PENDING_KEY, encoded)
    if not await _persist_proactive(user_id, text, image=image, epoch=write_epoch):
        if kv_get(user_id, _PENDING_KEY) == encoded:
            kv_del(user_id, _PENDING_KEY)
        return False
    _notify_subscribers(user_id, message)
    return True


def dequeue_proactive(user_id: str) -> str | None:
    """兼容旧调用：取出消息后只返回文字。新链路使用 dequeue_proactive_message。"""
    message = dequeue_proactive_message(user_id)
    return message["text"] if message else None


def dequeue_proactive_message(user_id: str) -> ProactiveMessage | None:
    """取出并删除完整主动消息（文字 + 可选图片）。"""
    message = _decode_message(kv_get(user_id, _PENDING_KEY))
    if message is None:
        return None
    kv_del(user_id, _PENDING_KEY)
    return message


# ---- SSE 发布订阅：后台生成消息 → 实时推给订阅的前端连接 ----
# 每个 user_id 一个 asyncio.Queue，SSE 连接订阅后等待；入队时 put 进去即可唤醒。
_SUBSCRIBERS: dict[str, set[asyncio.Queue]] = {}


def subscribe(user_id: str) -> asyncio.Queue:
    """SSE 连接订阅某 user 的主动消息，返回一个队列（连接据此等待推送）。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=8)
    _SUBSCRIBERS.setdefault(user_id, set()).add(q)
    return q


def unsubscribe(user_id: str, q: asyncio.Queue) -> None:
    """SSE 连接断开时取消订阅。"""
    subs = _SUBSCRIBERS.get(user_id)
    if subs:
        subs.discard(q)
        if not subs:
            _SUBSCRIBERS.pop(user_id, None)


def _notify_subscribers(user_id: str, message: ProactiveMessage) -> None:
    """入队时广播：唤醒所有订阅该 user 的 SSE 连接。"""
    subs = _SUBSCRIBERS.get(user_id)
    if not subs:
        return
    for q in list(subs):
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            pass  # 连接积压则丢弃（下一条消息会覆盖）


async def initiative_loop() -> None:
    """后台主动性循环：每 _CHECK_INTERVAL_SEC 检查一轮。

    异常全捕获，绝不因主动引擎故障拖垮服务。
    """
    logger.info("[主动性] 引擎启动，轮询间隔 {}s", config.proactive_check_interval_sec)
    while True:
        try:
            n = await _tick_once()
            if n:
                logger.info("[主动性] 本轮主动联系 {} 人", n)
        except Exception as e:
            logger.warning("[主动性] 循环异常: {}", e)
        await asyncio.sleep(config.proactive_check_interval_sec)


async def poll_message_for(user_id: str) -> ProactiveMessage | None:
    """供前端轮询：检查该用户是否「值得被主动联系」，是则生成并返回一条消息。

    与后台 loop 的区别：这是「拉」模式，由前端在打开窗口/定时轮询时调用，
    适合 Electron 桌面端这种有明确前台窗口的场景。返回消息文本或 None。
    """
    from .reset import reset_epoch, reset_in_progress
    if reset_in_progress():
        return None
    epoch = reset_epoch()
    user = db.get_user(user_id)
    if not user:
        return None
    # 关系够近才主动
    if _STAGE_ORDER.get(stage_of(user["affection"] or 0), 0) < _STAGE_ORDER[_MIN_STAGE]:
        return None
    # 今天已主动过
    if _proactive_done_today(user_id):
        return None
    # 最近在聊则不打扰
    last = _last_chat_ts(user_id)
    if last is not None and time.time() - last < config.proactive_idle_hours * 3600:
        return None
    from .proactive_policy import finish_active_claim, try_claim_active

    claim_token = try_claim_active(user_id, "initiative-poll")
    if not claim_token:
        return None
    message = await generate_proactive_content(user_id)
    from .reset import epoch_is_current
    if not epoch_is_current(epoch):
        finish_active_claim(user_id, claim_token, success=False, source="initiative-poll")
        return None
    if message:
        if not await _persist_proactive(
            user_id, message["text"], image=message["image"], epoch=epoch
        ):
            finish_active_claim(user_id, claim_token, success=False, source="initiative-poll")
            return None
        if not epoch_is_current(epoch):
            finish_active_claim(user_id, claim_token, success=False, source="initiative-poll")
            return None
        finish_active_claim(user_id, claim_token, success=True, source="initiative-poll")
    else:
        finish_active_claim(user_id, claim_token, success=False, source="initiative-poll")
    return message


async def poll_for(user_id: str) -> str | None:
    """兼容旧调用：执行轮询主动，但只返回文字。"""
    message = await poll_message_for(user_id)
    return message["text"] if message else None


# ---- 主动归档建议（会话过长时，菟菚主动提醒归档，而非擅自清空）----

_ARCHIVE_THRESHOLD = 40      # 当前会话消息数达到该值时提醒归档
_ARCHIVE_IDLE_MIN = 15       # 距最后一条真实聊天 ≥ 该分钟数才提醒归档（避免正聊天时插话）
_ARCHIVE_SUGGEST_KEY = "initiative:archive_suggest"  # kv 去重键


def _archive_suggested_today(user_id: str) -> bool:
    today = datetime.date.today().isoformat()
    return kv_get(user_id, f"{_ARCHIVE_SUGGEST_KEY}:{today}") is not None


def _mark_archive_suggested(user_id: str) -> None:
    today = datetime.date.today().isoformat()
    kv_set(user_id, f"{_ARCHIVE_SUGGEST_KEY}:{today}", "1")


def _build_archive_suggest_prompt(user_id: str) -> list[dict] | None:
    """拼一条「建议归档」的菟菚风格消息。"""
    user = db.get_user(user_id)
    if not user:
        return None
    affection_val = user["affection"] or 0
    sys_prompt = build_system_prompt(
        stage=stage_of(affection_val),
        address=user["nickname_pref"] or "",
        lover_confirm=bool(user["lover_confirm"]),
        first_chat=False,
        affection=affection_val,
        user_id=user_id,
    )
    return [
        {"role": "system", "content": sys_prompt},
        {
            "role": "user",
            "content": (
                "你们已经聊了很长一段了，这段对话攒了不少内容。"
                "你发现再聊下去前面的话会越来越难翻找，想提醒对方："
                "可以把这段对话归档存档，然后开一段新的。"
                "自然地说一句，别命令式、别啰嗦，一句到两句就够，"
                "符合你的性格（干脆、带点腹黑毒舌也行），别加括号动作。"
            ),
        },
    ]


async def _generate_archive_suggest(user_id: str) -> str | None:
    msgs = _build_archive_suggest_prompt(user_id)
    if not msgs:
        return None
    try:
        text = await chat(msgs, max_tokens=100, temperature=0.85)
        return text.strip()[:200] or None
    except Exception as e:
        logger.warning("[主动性] 归档建议生成失败: {}", e)
        return None


async def maybe_suggest_archive(user_id: str) -> str | None:
    """判断当前会话是否过长需要提醒归档，是则生成并投递一条建议消息。

    返回投递的消息文本（None = 无需提醒或投递失败）。采用「建议式」而非
    自动归档：擅自清空当前对话可能让用户丢失上下文，提醒更稳妥。

    「别打扰正在聊的人」：即使会话已 ≥ 阈值，若用户最近还在聊天
    （距最后一条真实消息 < _ARCHIVE_IDLE_MIN 分钟），也不提醒——
    归档建议是「你歇下来时」的轻提醒，不该在对方正聊得热络时硬插。
    """
    # 先看是否真的「空闲」：正在聊/刚聊过 → 闭嘴（即使消息数已达阈值）
    last = _last_chat_ts(user_id)
    if last is not None and time.time() - last < _ARCHIVE_IDLE_MIN * 60:
        return None
    try:
        from ..session import store as _store

        count = await _store.message_count(_store.CURRENT_SESSION_ID)
    except Exception:
        return None
    if count < _ARCHIVE_THRESHOLD:
        return None
    if _archive_suggested_today(user_id):
        return None
    text = await _generate_archive_suggest(user_id)
    if not text:
        return None
    if not await enqueue_proactive(user_id, text):
        return None
    _mark_archive_suggested(user_id)
    return text


# SSE 长连接：服务端推送的「心跳 + 主动消息」事件流。
# 前端用 EventSource 订阅，替代 30s 轮询。心跳用于保活（部分代理会断空闲连接），
# 并顺带探测一次待投递队列（兜底后台 loop 未启动/延迟的情况）。
_SSE_HEARTBEAT_SEC = 15
_SSE_QUEUE_POLL_SEC = 10


async def sse_event_stream(user_id: str):
    """SSE 事件流：订阅主动消息推送 + 心跳保活。

    生成器 yield 出 SSE 帧（字符串）。连接建立后：
    - 立即推送一次「当前待投递队列」里的消息（若有，秒级送达）；
    - 订阅发布订阅队列，后台 enqueue 时即时收到；
    - 每隔 _SSE_HEARTBEAT_SEC 发一个心跳注释帧保活；
    - 每隔 _SSE_QUEUE_POLL_SEC 主动探测一次待投递队列（兜底）。
    """
    q = subscribe(user_id)
    try:
        # 首帧：立即清一次队列，把已有的待投递消息秒级送达
        pending = dequeue_proactive_message(user_id)
        if pending:
            yield f"event: initiative\ndata: {json.dumps(pending, ensure_ascii=False)}\n\n"

        last_poll = time.monotonic()
        last_beat = time.monotonic()
        while True:
            try:
                # 等待订阅推送（后台入队会 put 进来），超时后做心跳/队列探测
                message = await asyncio.wait_for(q.get(), timeout=1.0)
                # 入队同时写了持久化 pending。实时队列已经投递成功时，仅在内容仍
                # 匹配的情况下清除 pending，避免 10 秒兜底轮询把同一条再发一遍。
                if _decode_message(kv_get(user_id, _PENDING_KEY)) == message:
                    kv_del(user_id, _PENDING_KEY)
                yield f"event: initiative\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"
                continue
            except asyncio.TimeoutError:
                pass

            now = time.monotonic()
            # 队列兜底探测
            if now - last_poll >= _SSE_QUEUE_POLL_SEC:
                last_poll = now
                pending = dequeue_proactive_message(user_id)
                if pending:
                    yield f"event: initiative\ndata: {json.dumps(pending, ensure_ascii=False)}\n\n"
            # 心跳保活
            if now - last_beat >= _SSE_HEARTBEAT_SEC:
                last_beat = now
                yield ": keepalive\n\n"
    finally:
        unsubscribe(user_id, q)
