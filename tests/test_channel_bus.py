# -*- coding: utf-8 -*-
"""L12/L13 渠道总线回归（fake，不接真服务）。

覆盖：
1. 绑定一次性码：生成→消费→active；码一次性（重放拒绝）；过期拒绝；
   同渠道再生成作废旧 pending；
2. 身份解析：active 绑定可解析、哈希不存原文；未绑定账号入站 rejected；
3. inbox 状态机：(channel, external_message_id) 幂等；认领独占；done/failed；
4. outbox 状态机：入队/投递/失败退避（next_retry_at 递增）/due 查询；
5. 撤销联动：新入站 rejected、queued/unknown → suppressed、
   source_version 迟到投递阻断。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_chanbus_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.channels.base import ChannelUnavailable  # noqa: E402
from backend.channels.identity import resolve_identity  # noqa: E402
from backend.channels.qq_official import QQOfficialAdapter, parse_event  # noqa: E402
from backend.core import channel_bus as bus  # noqa: E402
from backend.core.channel_bus import (  # noqa: E402
    ChannelBusError,
    InboundMessage,
    OutboundMessage,
)
from backend.core.userdb import db  # noqa: E402

UID = "channel-user"
NOW = datetime(2026, 9, 21, 12, 0, 0)


def test_binding_code_lifecycle() -> None:
    db.ensure_user(UID)
    first = bus.create_binding_code(UID, persona_id="default", channel="qq_official", now=NOW)
    assert first["code"].isdigit() and len(first["code"]) == 6
    # 同渠道再生成：旧 pending 作废
    second = bus.create_binding_code(UID, persona_id="default", channel="qq_official", now=NOW)
    try:
        bus.consume_binding_code("qq_official", first["code"], "wx-acc-1", now=NOW)
        raise AssertionError("旧码应已作废")
    except ChannelBusError:
        pass
    # 新码消费成功 → active
    bound = bus.consume_binding_code("qq_official", second["code"], "qq-acc-42", now=NOW)
    assert bound["user_id"] == UID and bound["status"] == "active"
    # 一次性：同码重放拒绝
    try:
        bus.consume_binding_code("qq_official", second["code"], "qq-acc-42", now=NOW)
        raise AssertionError("绑定码必须一次性")
    except ChannelBusError:
        pass
    # 过期拒绝
    stale = bus.create_binding_code(UID, channel="qq_official", now=NOW)
    late = NOW + bus.CODE_TTL + timedelta(seconds=1)
    try:
        bus.consume_binding_code("qq_official", stale["code"], "qq-acc-7", now=late)
        raise AssertionError("过期码应拒绝")
    except ChannelBusError as exc:
        assert "过期" in str(exc)
    print("[OK] 绑定一次性码：生成/作废旧码/消费/一次性/过期")


def test_identity_resolution() -> None:
    db.ensure_user(UID)
    code = bus.create_binding_code(UID, persona_id="default", channel="wechat_local", now=NOW)
    bus.consume_binding_code("wechat_local", code["code"], "wxid_alice", now=NOW)
    ident = resolve_identity("wechat_local", "wxid_alice")
    assert ident and ident["user_id"] == UID and ident["persona_id"] == "default"
    assert resolve_identity("wechat_local", "wxid_bob") is None, "未绑定账号解析失败"
    # 库里只有哈希不存原文
    with db._lock:
        rows = db.conn.execute(
            "SELECT external_account_hash FROM channel_bindings WHERE channel='wechat_local'"
        ).fetchall()
    assert all("wxid" not in (r["external_account_hash"] or "") for r in rows)
    print("[OK] 身份解析：active 可解析、未绑定 None、账号只存哈希")


def test_inbox_state_machine() -> None:
    db.ensure_user(UID)
    code = bus.create_binding_code(UID, channel="qq_official", now=NOW)
    bus.consume_binding_code("qq_official", code["code"], "qq-inbox-acc", now=NOW)
    msg = InboundMessage(
        channel="qq_official", external_account_id="qq-inbox-acc",
        external_message_id="m-001", text="在吗", received_at="2026-09-21T12:00:00",
    )
    r1 = bus.record_inbound(msg)
    assert r1["deduplicated"] is False and r1["status"] == "pending"
    r2 = bus.record_inbound(msg)
    assert r2["deduplicated"] is True, "(channel, external_message_id) 幂等"
    # 未绑定账号 → rejected
    r3 = bus.record_inbound(InboundMessage(
        channel="qq_official", external_account_id="stranger",
        external_message_id="m-002", text="hi",
    ))
    assert r3["status"] == "rejected"
    # 认领独占：第一次成功，第二次（已 processing）返回 None
    claimed = bus.claim_inbox("qq_official", "m-001")
    assert claimed is not None and claimed["status"] == "processing"
    assert bus.claim_inbox("qq_official", "m-001") is None
    bus.finish_inbox("qq_official", "m-001", ok=True)
    again = bus.claim_inbox("qq_official", "m-001")
    assert again is None, "done 后不可再认领"
    print("[OK] inbox：幂等去重、未绑定拒绝、认领独占、终态不可再认领")


def test_outbox_state_machine() -> None:
    db.ensure_user(UID)
    code = bus.create_binding_code(UID, channel="qq_official", now=NOW)
    bound = bus.consume_binding_code("qq_official", code["code"], "qq-out-acc", now=NOW)
    bid = bound["binding_id"]
    msg = OutboundMessage(logical_message_id="L-1", channel="qq_official",
                          binding_id=bid, text="回你啦")
    assert bus.enqueue_outbound(msg)["status"] == "queued"
    assert bus.enqueue_outbound(msg)["status"] == "queued"  # 幂等入队
    due = bus.due_outbox(now=NOW)
    assert any(o["logical_message_id"] == "L-1" for o in due)
    # 失败退避：两次失败 next_retry_at 递增
    bus.mark_outbound("L-1", "qq_official", "failed", error="timeout", now=NOW)
    o1 = [o for o in bus.due_outbox(now=NOW) if o["logical_message_id"] == "L-1"]
    assert not o1, "失败后未到重试时间不在 due 里"
    bus.mark_outbound("L-1", "qq_official", "queued", now=NOW)
    bus.mark_outbound("L-1", "qq_official", "failed", error="timeout", now=NOW)
    later = NOW + timedelta(minutes=5)
    due2 = bus.due_outbox(now=later)
    hit = [o for o in due2 if o["logical_message_id"] == "L-1"]
    assert hit and hit[0]["attempts"] == 2, "退避后可再投递且 attempts 累计"
    # 非法状态
    try:
        bus.mark_outbound("L-1", "qq_official", "teleported")
        raise AssertionError("非法状态应拒绝")
    except ChannelBusError:
        pass
    print("[OK] outbox：幂等入队、失败退避递增、due 查询、非法状态拒绝")


def test_revoke_blocks_everything() -> None:
    db.ensure_user(UID)
    code = bus.create_binding_code(UID, channel="qq_official", now=NOW)
    bound = bus.consume_binding_code("qq_official", code["code"], "qq-rv-acc", now=NOW)
    bid = bound["binding_id"]
    sv_before = bus.binding_source_version(bid)
    bus.enqueue_outbound(OutboundMessage("L-rv", "qq_official", bid, "待投递"))
    # 撤销：未投递 outbox → suppressed；source_version 自增
    bus.revoke_binding(bid, now=NOW)
    assert bus.binding_source_version(bid) == sv_before + 1
    with db._lock:
        row = db.conn.execute(
            "SELECT status FROM channel_outbox WHERE logical_message_id='L-rv'"
        ).fetchone()
    assert row["status"] == "suppressed"
    # 撤销后新入站 rejected
    r = bus.record_inbound(InboundMessage(
        channel="qq_official", external_account_id="qq-rv-acc",
        external_message_id="m-after-rv", text="?",
    ))
    assert r["status"] == "rejected"
    # 迟到投递阻断：携带旧 source_version 的 delivered 判 suppressed
    db.ensure_user(UID)
    code2 = bus.create_binding_code(UID, channel="qq_official", now=NOW)
    b2 = bus.consume_binding_code("qq_official", code2["code"], "qq-rv2-acc", now=NOW)
    bid2 = b2["binding_id"]
    bus.enqueue_outbound(OutboundMessage("L-late", "qq_official", bid2, "在途"))
    old_sv = bus.binding_source_version(bid2)
    bus.revoke_binding(bid2, now=NOW)
    result = bus.mark_outbound("L-late", "qq_official", "delivered",
                               provider_message_id="pm-1", source_version=old_sv, now=NOW)
    assert result["status"] == "suppressed", "撤销后的迟到 delivered 必须被阻断"
    print("[OK] 撤销联动：outbox 抑制、新入站拒绝、source_version 迟到投递阻断")


def test_qq_adapter_fake() -> None:
    adapter = QQOfficialAdapter(webhook_secret="s3cret")
    body = b'{"content":"hello","message_id":"m1","author_id":"a1"}'
    good_sig = "sha1=" + __import__("hmac").new(
        b"s3cret", body, __import__("hashlib").sha1).hexdigest()
    assert adapter.verify_webhook({"x-signature": good_sig}, body) is True
    assert adapter.verify_webhook({"x-signature": "sha1=bad"}, body) is False
    assert adapter.verify_webhook({}, body) is False  # 无密钥配置即拒绝
    inbound = adapter.normalize_inbound(parse_event(body) or {})
    assert inbound.external_message_id == "m1" and inbound.text == "hello"
    caps = adapter.capabilities()
    assert not caps.inbound_text and not caps.groups, "骨架期能力一律不明示"
    health = asyncio.run(adapter.health())
    assert health["offline_skeleton"] is True
    try:
        asyncio.run(adapter.send(OutboundMessage("L-x", "qq_official", 1, "hi")))
        raise AssertionError("骨架期真实发送必须拒绝")
    except ChannelUnavailable:
        pass
    print("[OK] QQ 适配器（fake）：HMAC 验签、归一化、能力自述、发送拒绝")


def main() -> None:
    db.conn.execute("SELECT 1")
    test_binding_code_lifecycle()
    test_identity_resolution()
    test_inbox_state_machine()
    test_outbox_state_machine()
    test_revoke_blocks_everything()
    test_qq_adapter_fake()
    print("\n=== L13 渠道总线（fake）：6 组全部通过 ===")


if __name__ == "__main__":
    main()
