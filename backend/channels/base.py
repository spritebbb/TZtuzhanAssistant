# -*- coding: utf-8 -*-
"""L13 渠道适配层（离线骨架）：ChannelAdapter 协议与统一消息形态。

批次15 口径：全部 fake 测试，不接 QQ/微信真服务；能力以 capabilities()
明示，未明示的一律不可用（群聊/语音/文件/主动私聊默认关）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..core.channel_bus import InboundMessage, OutboundMessage


@dataclass
class AdapterCapabilities:
    inbound_text: bool = False
    inbound_image: bool = False        # 图片经 vision 事实层，不可信包裹
    outbound_text: bool = False
    proactive_private: bool = False    # 主动私聊（需渠道审批的场景）
    groups: bool = False               # 群聊（默认关）
    revoke_events: bool = False


class ChannelAdapter(ABC):
    """渠道适配器固定接口（L13 任务书）：能力自述 + 验签 + 归一化 + 发送 + 健康。"""

    channel: str = ""

    @abstractmethod
    def capabilities(self) -> AdapterCapabilities: ...

    @abstractmethod
    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        """平台回调验签（密钥来自 secrets provider，不进配置模板）。"""

    @abstractmethod
    def normalize_inbound(self, payload: dict) -> InboundMessage:
        """平台原始事件 → 统一 InboundMessage（内容是不可信用户输入）。"""

    @abstractmethod
    async def send(self, outbound: OutboundMessage) -> dict:
        """出站投递：成功回 provider_message_id；超时必须置 unknown 由上层
        按 provider 幂等能力确认，不盲发第二条。"""

    @abstractmethod
    async def health(self) -> dict: ...


class ChannelUnavailable(RuntimeError):
    """渠道未接入/未启用（离线骨架期所有真实发送的统一回答）。"""
