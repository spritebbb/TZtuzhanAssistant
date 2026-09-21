# -*- coding: utf-8 -*-
"""QQ 官方机器人适配器（L13 首接，离线骨架）。

选型（任务书）：腾讯官方 bot SDK/API——授权接口与凭据边界清楚，风控小于
注入客户端的本地桥。骨架期：webhook 验签按官方 HMAC 口径实现（密钥注入），
真实 API 调用一律 ChannelUnavailable；能力自述全部关闭待真机实测后开启。
普通 QQ 账号桥接只保留 adapter 接口位，不进生产配置。"""
from __future__ import annotations

import hashlib
import hmac
import json

from ..core.channel_bus import InboundMessage, OutboundMessage
from .base import AdapterCapabilities, ChannelAdapter, ChannelUnavailable


class QQOfficialAdapter(ChannelAdapter):
    channel = "qq_official"

    def __init__(self, *, webhook_secret: str = "") -> None:
        # 凭据来自 secrets provider（环境注入），不进配置模板明文
        self._secret = webhook_secret

    def capabilities(self) -> AdapterCapabilities:
        # 能力以申请到的场景与官方端点实测为准——骨架期一律不明示
        return AdapterCapabilities()

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        if not self._secret:
            return False
        signature = str(headers.get("x-signature", "")).removeprefix("sha1=")
        expected = hmac.new(self._secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
        return hmac.compare_digest(signature, expected)

    def normalize_inbound(self, payload: dict) -> InboundMessage:
        """官方事件 → 统一入站。正文/附件是不可信输入，不携带任何权限。"""
        content = str(payload.get("content") or payload.get("text") or "").strip()
        return InboundMessage(
            channel=self.channel,
            external_account_id=str(payload.get("author_id") or payload.get("openid") or ""),
            external_message_id=str(payload.get("message_id") or payload.get("id") or ""),
            text=content,
            received_at=str(payload.get("timestamp") or ""),
        )

    async def send(self, outbound: OutboundMessage) -> dict:
        raise ChannelUnavailable(
            "QQ 官方通道未接入（离线骨架）：真实发送待凭据与场景审批后实现"
        )

    async def health(self) -> dict:
        return {"channel": self.channel, "connected": False, "offline_skeleton": True,
                "note": "待官方凭据接入"}


def parse_event(body: bytes) -> dict | None:
    """官方回调体解析（骨架容错：非法 JSON 返回 None 不抛）。"""
    try:
        data = json.loads(body.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None
