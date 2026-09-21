# -*- coding: utf-8 -*-
"""微信本地桥适配器（L14，离线骨架）：WeChatFerry sidecar 的最小消息协议客户端。

契约（任务书）：
- sidecar 只监听命名管道或 loopback 随机端口；主进程带一次性 token 握手；
- 启动握手返回 bridge_version / client_version / logged_in / capabilities，
  版本或能力不符 → 渠道暂停提示用户手工处理，不自动降级；
- logged_in=False → 暂停消费、保留有界 outbox，不模拟扫码不绕验证；
- 双向帧 ≤1MB；断线指数退避；发送超时进 unknown 不盲重试；
- 桥接进程不直连数据库/模型/工具——一切经本适配器走 L13 总线；
- 群事件默认丢弃并计数；日志脱敏 wxid/正文/媒体路径。

骨架：传输层注入（fake bridge 测试），真实命名管道/HTTP 传输待真机接入。"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from ..core.channel_bus import InboundMessage, OutboundMessage
from .base import AdapterCapabilities, ChannelAdapter, ChannelUnavailable

MAX_FRAME_BYTES = 1024 * 1024


class SidecarProtocolError(RuntimeError):
    """sidecar 协议违约（帧超限/握手失败/版本不符）。"""


@dataclass
class SidecarHandshake:
    bridge_version: str
    client_version: str
    logged_in: bool
    capabilities: dict


class WechatLocalAdapter(ChannelAdapter):
    channel = "wechat_local"

    def __init__(self, transport) -> None:
        """transport: 具备 request(frame: dict) -> dict 的对象（命名管道/loopback
        HTTP/测试 fake）。主进程不直接触碰桥的二进制协议。"""
        self._transport = transport
        self._token = secrets.token_urlsafe(24)  # 一次性 token：主进程↔sidecar 鉴权
        self._handshake: SidecarHandshake | None = None

    def capabilities(self) -> AdapterCapabilities:
        if not self._logged_in():
            return AdapterCapabilities()  # 未登录：一切能力不明示
        caps = self._handshake.capabilities if self._handshake else {}
        return AdapterCapabilities(
            inbound_text=bool(caps.get("text", False)),
            inbound_image=bool(caps.get("image", False)),
            outbound_text=bool(caps.get("send_text", False)),
            groups=False,  # 首期关闭；群事件默认丢弃并计数
        )

    def _logged_in(self) -> bool:
        return bool(self._handshake and self._handshake.logged_in)

    def handshake(self) -> SidecarHandshake:
        """启动握手：版本与能力校验，失败即暂停渠道（不自动降级）。"""
        resp = self._request({
            "op": "hello",
            "token": self._token,
            "expect": {"min_bridge_version": "0.1", "features": ["text"]},
        })
        hs = SidecarHandshake(
            bridge_version=str(resp.get("bridge_version") or ""),
            client_version=str(resp.get("client_version") or ""),
            logged_in=bool(resp.get("logged_in")),
            capabilities=dict(resp.get("capabilities") or {}),
        )
        if not hs.bridge_version or not hs.capabilities.get("text"):
            raise SidecarProtocolError(
                "sidecar 版本/能力不符：渠道暂停，请手工检查微信客户端与桥版本"
            )
        self._handshake = hs
        return hs

    def _request(self, frame: dict) -> dict:
        raw = json.dumps(frame, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_FRAME_BYTES:
            raise SidecarProtocolError("帧超过 1MB 上限")
        resp_raw = self._transport.request(raw)
        if len(resp_raw) > MAX_FRAME_BYTES:
            raise SidecarProtocolError("回帧超过 1MB 上限")
        resp = json.loads(resp_raw.decode("utf-8"))
        if not isinstance(resp, dict) or not resp.get("ok"):
            raise SidecarProtocolError(str(resp.get("error") or "sidecar 拒绝请求"))
        return resp

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        # 本地桥不走公网 webhook：入站来自 sidecar 推送帧，token 即边界
        return self._logged_in()

    def normalize_inbound(self, payload: dict) -> InboundMessage:
        """sidecar 推送帧 → 统一入站。群事件在此丢弃并计数（首期口径）。"""
        if payload.get("is_group"):
            # 群消息默认丢弃：不进 inbox，仅计数（由调用方统计）
            raise ChannelUnavailable("群消息首期关闭")
        return InboundMessage(
            channel=self.channel,
            external_account_id=str(payload.get("sender_wxid") or ""),
            external_message_id=str(payload.get("message_id") or ""),
            text=str(payload.get("content") or ""),
            received_at=str(payload.get("timestamp") or ""),
        )

    async def send(self, outbound: OutboundMessage) -> dict:
        if not self._logged_in():
            raise ChannelUnavailable("微信未登录：outbox 暂停（不模拟扫码）")
        resp = self._request({
            "op": "send_text",
            "token": self._token,
            "to": str(outbound.reply_to or ""),
            "text": outbound.text,
            "client_msg_id": outbound.logical_message_id,  # provider 幂等键
        })
        # 超时语义由传输层抛出 → 上层置 unknown；这里只处理明确成功/失败
        return {"provider_message_id": str(resp.get("server_msg_id") or "")}

    async def health(self) -> dict:
        return {
            "channel": self.channel,
            "connected": self._logged_in(),
            "handshake": self._handshake is not None,
            "note": "离线骨架：真实 sidecar 待 WeChatFerry 版本锁定接入",
        }


__all__ = [
    "MAX_FRAME_BYTES",
    "SidecarHandshake",
    "SidecarProtocolError",
    "WechatLocalAdapter",
]
