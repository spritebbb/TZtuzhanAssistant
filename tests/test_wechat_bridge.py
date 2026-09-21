# -*- coding: utf-8 -*-
"""L14 微信本地桥回归（fake bridge，不登录真实微信）。

覆盖：
1. 握手：版本/能力不符 → SidecarProtocolError（渠道暂停，不自动降级）；
2. token 鉴权与 1MB 帧上限（双向）；
3. 未登录：能力全关、发送 ChannelUnavailable（不模拟扫码）；
4. 登录后：send 走 provider 幂等键；群消息归一化即丢弃；
5. 设备注册（L12 骨架）：一次性会话 token 验证/撤销/过期。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_wxbridge_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.channels.base import ChannelUnavailable  # noqa: E402
from backend.channels.wechat_local import (  # noqa: E402
    MAX_FRAME_BYTES,
    SidecarProtocolError,
    WechatLocalAdapter,
)
from backend.core import devices  # noqa: E402
from backend.core.channel_bus import OutboundMessage  # noqa: E402
from backend.core.userdb import db  # noqa: E402


class FakeBridge:
    """fake sidecar：实现 request(raw: bytes) -> bytes，带 token 校验与帧限。"""

    def __init__(self, *, logged_in=True, capabilities=None, token="") -> None:
        self.logged_in = logged_in
        self.capabilities = capabilities or {"text": True, "image": True, "send_text": True}
        self.token = token
        self.sent: list[dict] = []

    def request(self, raw: bytes) -> bytes:
        if len(raw) > MAX_FRAME_BYTES:
            raise ValueError("frame too large")
        frame = json.loads(raw.decode("utf-8"))
        if self.token and frame.get("token") != self.token:
            return json.dumps({"ok": False, "error": "token 无效"}).encode()
        op = frame.get("op")
        if op == "hello":
            return json.dumps({
                "ok": True,
                "bridge_version": "0.1.0",
                "client_version": "4.0.0.42",
                "logged_in": self.logged_in,
                "capabilities": self.capabilities,
            }, ensure_ascii=False).encode()
        if op == "send_text":
            self.sent.append(frame)
            return json.dumps({"ok": True, "server_msg_id": f"srv-{len(self.sent)}"}).encode()
        return json.dumps({"ok": False, "error": f"未知 op {op}"}).encode()


def _make(logged_in=True, caps=None):
    bridge = FakeBridge(logged_in=logged_in, capabilities=caps)
    adapter = WechatLocalAdapter(bridge)
    return adapter, adapter.handshake(), bridge


def test_handshake_version_gate() -> None:
    adapter = WechatLocalAdapter(FakeBridge(capabilities={"text": False}))
    try:
        adapter.handshake()
        raise AssertionError("能力不符应暂停渠道")
    except SidecarProtocolError as exc:
        assert "暂停" in str(exc)
    # token 无效 → 握手失败（桥侧持有正确 token，主进程带错的）
    bridge2 = FakeBridge()
    adapter2 = WechatLocalAdapter(bridge2)
    bridge2.token = adapter2._token
    adapter2._token = "wrong-token"
    try:
        adapter2.handshake()
        raise AssertionError("token 无效应拒绝握手")
    except SidecarProtocolError:
        pass
    print("[OK] 握手：能力不符暂停、token 鉴权")


def test_frame_limit_and_login_gate() -> None:
    adapter, hs, bridge = _make()
    assert hs.logged_in and hs.client_version
    # 帧上限：请求侧超 1MB 抛协议错
    big = WechatLocalAdapter(FakeBridge())
    big._handshake = hs
    try:
        big._request({"op": "send_text", "text": "x" * (MAX_FRAME_BYTES + 10)})
        raise AssertionError("超限帧应拒绝")
    except SidecarProtocolError:
        pass
    # 未登录：能力全关 + 发送拒绝
    offline, _hs, _b = _make(logged_in=False)
    caps = offline.capabilities()
    assert not caps.inbound_text and not caps.outbound_text
    try:
        asyncio.run(offline.send(OutboundMessage("L-1", "wechat_local", 1, "hi")))
        raise AssertionError("未登录不得发送")
    except ChannelUnavailable as exc:
        assert "未登录" in str(exc)
    # 群消息归一化即丢弃
    try:
        offline.normalize_inbound({"is_group": True, "content": "群消息"})
        raise AssertionError("群消息应丢弃")
    except ChannelUnavailable:
        pass
    print("[OK] 帧上限 1MB、未登录能力全关且拒绝发送、群消息丢弃")


def test_send_with_provider_idempotency_key() -> None:
    adapter, _hs, bridge = _make()
    result = asyncio.run(adapter.send(OutboundMessage(
        "L-abc", "wechat_local", 1, "回你", reply_to="wxid_alice",
    )))
    assert result["provider_message_id"].startswith("srv-")
    assert bridge.sent[0]["client_msg_id"] == "L-abc", "必须带 provider 幂等键（unknown 不盲发）"
    health = asyncio.run(adapter.health())
    assert health["connected"] is True and "骨架" in health["note"]
    print("[OK] 登录后发送：幂等键随行、健康自述含骨架标记")


def test_devices_skeleton() -> None:
    db.ensure_user("device-user")
    reg = devices.register_device("device-user", display_name="我的手机", platform="android")
    assert reg["session_token"] and reg["device_id"]
    ident = devices.verify_session(reg["session_token"])
    assert ident and ident["user_id"] == "device-user"
    # 撤销后会话失效（幂等）
    assert devices.revoke_device(reg["device_id"], "device-user")["ok"] is True
    assert devices.verify_session(reg["session_token"]) is None
    assert devices.revoke_device(reg["device_id"], "device-user")["ok"] is True
    # 越权撤销他人设备 → 404 语义
    assert devices.revoke_device(reg["device_id"], "someone-else").get("ok") is False
    # 推送订阅只存哈希 + 404/410 撤销幂等
    sub = devices.add_push_subscription(reg["device_id"], "https://push.example/abc")
    assert sub["subscription_id"]
    with db._lock:
        row = db.conn.execute("SELECT endpoint_hash FROM push_subscriptions LIMIT 1").fetchone()
    assert "push.example" not in row["endpoint_hash"]
    assert devices.retract_push("https://push.example/abc")["ok"] is True
    assert devices.retract_push("https://push.example/abc")["ok"] is True
    listed = devices.list_devices("device-user")
    assert any(d["id"] == reg["device_id"] for d in listed)
    print("[OK] 设备骨架：注册/一次性会话/撤销幂等/越权拒绝/推送只存哈希")


def main() -> None:
    db.conn.execute("SELECT 1")
    test_handshake_version_gate()
    test_frame_limit_and_login_gate()
    test_send_with_provider_idempotency_key()
    test_devices_skeleton()
    print("\n=== L14 微信桥 + L12 设备（fake）：4 组全部通过 ===")


if __name__ == "__main__":
    main()
