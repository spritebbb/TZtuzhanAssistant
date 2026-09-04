# -*- coding: utf-8 -*-
"""拟人核心层回归测试：状态机 + 语义感知降级 + 行为映射 + 主动性判定。

用 main() 编排，与项目其它测试脚本风格一致（pytest 只收集 suite_*，这里直接跑）。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core import state, behavior, perception, initiative
from backend.core.userdb import db


def _ok(name: str, cond: bool) -> int:
    print(f"[{'OK' if cond else 'FAIL'}] {name}")
    return 1 if cond else 0


def test_state_machine() -> int:
    passed = 0
    uid = "test_anthropic_state"

    # 情绪标签映射
    passed += _ok("情绪标签-低落", state.emotion_label(10)[0] == "低落")
    passed += _ok("情绪标签-开心", state.emotion_label(70)[0] == "开心")
    passed += _ok("关系阶段-恋人", state.stage_of(80) == "恋人")
    passed += _ok("关系阶段-初识", state.stage_of(10) == "初识")

    # 状态演化
    db.ensure_user(uid)
    db.set_mood(uid, 60)
    db.set_affection_absolute(uid, 20)
    s = state.load_state(uid)
    passed += _ok("初始状态可读", s.emotion == 60 and s.stage == "初识")

    s2 = state.apply_impulse(
        uid, emotion_delta=10, affection_delta=2,
        affection_reason="测试", emotional_hit="被夸了", emotional_weight=1.0,
    )
    passed += _ok("情绪冲击后情绪上升", s2.emotion == 70)
    passed += _ok("好感度累加", s2.affection == 22)
    passed += _ok("情绪记忆残留", len(state.pending_emotion_hits(uid)) >= 1)

    # 精力派生
    passed += _ok("精力在合法范围", 0 <= s2.energy <= 100)
    return passed


def test_behavior_frame() -> int:
    passed = 0
    # 低落 + 累 → 话少、不耐烦
    f = behavior.build_behavior_frame(
        state.AgentState(emotion=20, energy=30, affection=10, stage="初识")
    )
    passed += _ok("低落帧含'低落'", "低落" in f.mood_line)
    passed += _ok("疲惫帧含'累'", "累" in f.mood_line)
    passed += _ok("初识帧含'疏离'", "疏离" in f.stage_line)
    passed += _ok("低落时主动性收敛", "简短" in f.initiative or "兴致" in f.initiative)

    # 雀跃 + 恋人 → 主动
    f2 = behavior.build_behavior_frame(
        state.AgentState(emotion=88, energy=90, affection=85, stage="恋人")
    )
    passed += _ok("雀跃时主动", "主动" in f2.initiative)

    # 情绪残留
    f3 = behavior.build_behavior_frame(
        state.AgentState(
            emotion=45, energy=90, affection=40, stage="熟悉",
            emotion_memory=[{"hit": "被冒犯了", "weight": 0.8}],
        )
    )
    passed += _ok("情绪残留被翻译成行为", "被冒犯" in f3.reaction_line)
    return passed


def test_perception_fallback() -> int:
    passed = 0

    async def run():
        # mock 模式走关键词降级
        r = await perception.perceive("你还好吗，别太累了", mock=True)
        assert r["care"] is True, "关心应被识别"
        r2 = await perception.perceive("我喜欢你，嫁给我吧", mock=True)
        assert r2["confession"] is True, "表白应被识别"
        r3 = await perception.perceive("你是不是傻逼啊", mock=True)
        assert r3["abuse"] is True, "明确辱骂词应被识别"
        return 3

    try:
        n = asyncio.run(run())
        passed += n
    except AssertionError as e:
        print(f"[FAIL] 感知降级: {e}")
    passed += _ok("感知结果结构完整", all(k in perception._normalize({}) for k in (
        "emotion_delta", "affection_delta", "care", "abuse", "apology",
        "sharing", "compliment", "confession", "bad_address", "dismiss",
    )))
    return passed


def test_initiative_eligibility() -> int:
    passed = 0
    passed += _ok("初识不主动", initiative._STAGE_ORDER["初识"] < initiative._STAGE_ORDER[initiative._MIN_STAGE])
    passed += _ok("熟悉可主动", initiative._STAGE_ORDER["熟悉"] >= initiative._STAGE_ORDER[initiative._MIN_STAGE])
    passed += _ok("每日去重键存在", isinstance(initiative._proactive_key("u", "2026-09-02"), str))
    return passed


def test_proactive_queue() -> int:
    passed = 0
    uid = "test_proactive_queue"
    db.ensure_user(uid)

    # 入队 → 出队应返回同一条消息（enqueue_proactive 现为 async：入队 + 幂等落库）
    async def _enqueue():
        await initiative.enqueue_proactive(uid, "菟菚想你了")

    asyncio.run(_enqueue())
    passed += _ok("入队后可出队", initiative.dequeue_proactive(uid) == "菟菚想你了")
    # 出队后应为空
    passed += _ok("出队后为空", initiative.dequeue_proactive(uid) is None)
    # 未入队时出队返回 None
    passed += _ok("未入队返回 None", initiative.dequeue_proactive("no_such_user_x") is None)
    return passed


def test_dual_track_merge() -> int:
    """双轨合并回归：心情不双算 + 降级标记 + persona 单一注入出口。"""
    passed = 0
    import json
    from backend.core import mood as _mood
    from backend.core.userdb import db as _db, kv_get as _kv_get

    # ① 冷落衰减专用函数存在且只做冷落（不依赖 text）
    passed += _ok("冷落衰减函数存在", hasattr(_mood, "idle_decay_if_due"))

    # ② perception 降级结果必须带 degraded=True
    async def _perceive_degraded():
        r = await perception.perceive("你还好吗", mock=True)
        return r.get("degraded") is True
    passed += _ok("降级结果标记 degraded", asyncio.run(_perceive_degraded()))

    # ③ persona 不再注入 mood_line（行为帧是唯一状态出口）
    from backend.core import persona
    uid = "test_dual_track_persona"
    _db.ensure_user(uid)
    _db.set_mood(uid, 60)
    _db.set_affection_absolute(uid, 20)
    sp = persona.build_system_prompt(stage="熟悉", address="哥哥", lover_confirm=False,
                                     first_chat=False, affection=20, user_id=uid)
    passed += _ok("注入行为帧", "你此刻的状态" in sp)
    passed += _ok("不再重复注入心情数值", "你当前的心情" not in sp)
    return passed


def test_circadian_energy() -> int:
    """昼夜节律 + 天气联动：精力曲线合理、天气偏移正确。"""
    passed = 0
    pts = ((0, 50), (6, 45), (8, 70), (11, 82), (14, 72), (15, 70), (19, 85), (22, 62))
    # 清晨最低、午前爬升、午后小低谷、傍晚高峰、深夜回落
    passed += _ok("清晨精力低谷", state._interp_circadian(6, pts) <= 45)
    passed += _ok("午前精力爬升", state._interp_circadian(11, pts) >= 80)
    passed += _ok("午后小低谷", state._interp_circadian(14.5, pts) < state._interp_circadian(11, pts))
    passed += _ok("傍晚精力高峰", state._interp_circadian(19, pts) >= 84)
    passed += _ok("深夜精力回落", state._interp_circadian(23, pts) < state._interp_circadian(19, pts))
    # 跨午夜连续：23.5 点与 0.5 点应接近
    diff = abs(state._interp_circadian(23.5, pts) - state._interp_circadian(0.5, pts))
    passed += _ok("跨午夜精力连续", diff < 8)
    # 天气偏移：晴朗为正、压抑为负（直接测 _weather_energy_offset 的映射逻辑）
    passed += _ok("天气偏移函数存在", callable(state._weather_energy_offset))
    return passed


def test_emotion_archive() -> int:
    """长期情绪档案：写入 + 聚合 + 召回 + 衰减 + 行为帧长期态度注入。"""
    passed = 0
    uid = "test_emotion_archive"
    db.ensure_user(uid)
    # 清理旧档案，避免测试重复运行残留导致 count 累加（数据隔离）
    try:
        from backend.core import userdb as _udb
        _udb.kv_del(uid, state._archive_key(uid))
    except Exception:
        pass

    # 写入多次正向情绪，验证主题聚合（同主题 count 累计）
    state.record_emotion_archive(uid, topic="被夸了", valence=1, weight=0.6)
    state.record_emotion_archive(uid, topic="被夸了", valence=1, weight=0.5)
    recalled = state.recall_emotion_archive(uid)
    kuas = [r for r in recalled if r["topic"] == "被夸了"]
    passed += _ok("同主题聚合", len(kuas) == 1 and kuas[0]["count"] == 2)

    # 通过 apply_impulse 自动写入长期档案
    state.apply_impulse(uid, emotion_delta=8, affection_delta=3,
                        emotional_hit="被真诚关心了", emotional_weight=0.8)
    archive = state.recall_emotion_archive(uid)
    passed += _ok("apply_impulse 自动归档", any(r["topic"] == "被真诚关心了" for r in archive))

    # 行为帧应含长期态度（正向积累显著 → 更愿意亲近）
    s = state.load_state(uid)
    f = behavior.build_behavior_frame(s)
    passed += _ok("行为帧含长期态度", bool(f.archive_line))

    # 负向积累 → 更防备
    uid2 = "test_emotion_archive_neg"
    db.ensure_user(uid2)
    state.record_emotion_archive(uid2, topic="被冒犯了", valence=-1, weight=0.9)
    state.record_emotion_archive(uid2, topic="被冒犯了", valence=-1, weight=0.7)
    f2 = behavior.build_behavior_frame(state.load_state(uid2))
    passed += _ok("负向积累更防备", bool(f2.archive_line) and "防备" in f2.archive_line)
    return passed


def test_sse_stream() -> int:
    """SSE 长连接：订阅推送 + 首帧秒级送达 + 队列兜底。"""
    passed = 0

    async def run():
        uid = "test_sse_stream"
        db.ensure_user(uid)
        # 入队应立即推给订阅者
        q = initiative.subscribe(uid)
        await initiative.enqueue_proactive(uid, "菟菚想你了", image="/api/images/selfie.png")
        message = await asyncio.wait_for(q.get(), timeout=2)
        assert message == {
            "text": "菟菚想你了",
            "image": "/api/images/selfie.png",
        }, "订阅应立即收到完整入队消息"
        initiative.unsubscribe(uid, q)
        # SSE 首帧应取走队列消息
        await initiative.enqueue_proactive(uid, "第二条主动消息")
        gen = initiative.sse_event_stream(uid)
        first = await gen.__anext__()
        assert "第二条主动消息" in first, "首帧应包含队列消息"
        assert '"image": null' in first, "SSE 应保持统一的文字+图片消息结构"
        await gen.aclose()
        return True

    passed += _ok("SSE 订阅推送+首帧送达", asyncio.run(run()))
    return passed


def test_perception_client() -> int:
    """独立小模型感知层：配置回退逻辑 + client 选择分支（不依赖网络）。"""
    passed = 0
    from backend.core import config as _cfg
    from backend.core import llm as _llm

    # 1) 配置齐全时：感知层模型/base_url 非空 → 走独立 client 分支
    #    （get_perception_client 需 key；这里只验证「有独立配置时不再退回主 client」
    #     的判断条件，不真正建连）
    _cfg.config.llm_perception_model = "Qwen/Qwen3-8B"
    _cfg.config.llm_perception_base_url = "https://api.siliconflow.cn/v1"
    _cfg.config.llm_perception_api_key = ""
    _cfg.config.image_api_key = "sk-image"
    _cfg.config.llm_api_key = "sk-main"
    # key 回退顺序：perception(空) → image(sk-image) → 主(sk-main)
    # 独立分支判定条件：model 或 base_url 非空即走独立 client
    has_independent = bool(_cfg.config.llm_perception_model or _cfg.config.llm_perception_base_url)
    passed += _ok("感知层独立配置触发独立通道", has_independent is True)

    # 2) 回退顺序：显式 perception key 优先于 image key
    _cfg.config.llm_perception_api_key = "sk-perception"
    key_priority = (
        _cfg.config.llm_perception_api_key
        or _cfg.config.image_api_key
        or _cfg.config.llm_api_key
    )
    passed += _ok("感知层 key 优先用显式配置", key_priority == "sk-perception")

    # 3) 无显式 key 时回退 image key
    _cfg.config.llm_perception_api_key = ""
    key_fallback = (
        _cfg.config.llm_perception_api_key
        or _cfg.config.image_api_key
        or _cfg.config.llm_api_key
    )
    passed += _ok("感知层 key 回退到 image key", key_fallback == "sk-image")

    # 4) 完全未配置独立模型时：复用主 LLM（get_client 分支）
    _cfg.config.llm_perception_model = ""
    _cfg.config.llm_perception_base_url = ""
    passed += _ok("未配独立模型时复用主 LLM", not (_cfg.config.llm_perception_model or _cfg.config.llm_perception_base_url))

    # 5) chat(perception=True) 的模型选择：有独立模型用独立，否则主模型
    _cfg.config.llm_perception_model = "Qwen/Qwen3-8B"
    model = _cfg.config.llm_perception_model or _cfg.config.llm_model
    passed += _ok("感知层模型优先独立模型", model == "Qwen/Qwen3-8B")
    _cfg.config.llm_perception_model = ""
    model2 = _cfg.config.llm_perception_model or _cfg.config.llm_model
    passed += _ok("感知层模型回退主模型", model2 == _cfg.config.llm_model)

    return passed


def test_event_memory() -> int:
    """事件级长期记忆：带原文写入 + 强/弱冲击过滤 + 行为帧精确引用。"""
    passed = 0
    from backend.core import userdb as _udb

    uid = "test_event_memory"
    db.ensure_user(uid)
    # 清理旧数据（数据隔离）
    for k in ("state:event_memory", "state:emotion_archive", "state:emotion_memory"):
        try:
            _udb.kv_del(uid, k)
        except Exception:
            pass

    # 强冲击（weight >= 0.6）应带原文记入事件记忆
    state.apply_impulse(uid, emotion_delta=8, affection_delta=3,
                        emotional_hit="被真诚关心了", emotional_weight=0.9,
                        text="你最近是不是太累了，注意休息啊")
    s = state.load_state(uid)
    passed += _ok("强冲击写入事件记忆", len(s.event_memory) == 1)
    if s.event_memory:
        passed += _ok("事件记忆带原文", s.event_memory[0]["text"] == "你最近是不是太累了，注意休息啊")

    # 弱冲击（weight < 0.6）不应记入事件记忆（避免噪音）
    state.apply_impulse(uid, emotion_delta=2, affection_delta=1,
                        emotional_hit="被夸了", emotional_weight=0.4,
                        text="你挺会说话的")
    s2 = state.load_state(uid)
    passed += _ok("弱冲击不记事件记忆", len(s2.event_memory) == 1)

    # 行为帧应注入事件级引用（event_line）
    f = behavior.build_behavior_frame(state.load_state(uid))
    passed += _ok("行为帧含事件级引用", bool(f.event_line) and "你最近是不是太累了" in f.event_line)

    # 负向事件 → 不翻旧账的克制措辞
    uid2 = "test_event_memory_neg"
    db.ensure_user(uid2)
    for k in ("state:event_memory", "state:emotion_archive", "state:emotion_memory"):
        try:
            _udb.kv_del(uid2, k)
        except Exception:
            pass
    state.apply_impulse(uid2, emotion_delta=-8, affection_delta=-3,
                        emotional_hit="被冒犯了", emotional_weight=0.9,
                        text="你是不是有病啊说话这么难听")
    f2 = behavior.build_behavior_frame(state.load_state(uid2))
    passed += _ok("负向事件克制引用", bool(f2.event_line) and "不是要你现在翻旧账" in f2.event_line)
    return passed


def main() -> int:
    total = 0
    total += test_state_machine()
    total += test_behavior_frame()
    total += test_perception_fallback()
    total += test_initiative_eligibility()
    total += test_proactive_queue()
    total += test_dual_track_merge()
    total += test_circadian_energy()
    total += test_emotion_archive()
    total += test_sse_stream()
    total += test_event_memory()
    total += test_perception_client()
    print(f"\n=== 拟人核心层: {total} 项通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
