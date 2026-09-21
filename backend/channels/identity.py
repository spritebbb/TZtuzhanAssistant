# -*- coding: utf-8 -*-
"""渠道身份解析（L13）：外部账号 → 本地 user/persona，只认一次性码验证过的绑定。

不能仅凭昵称/openid 猜身份——resolve 失败即 rejected，由绑定流程补位。"""
from __future__ import annotations

from ..core.channel_bus import account_hash, resolve_binding


def resolve_identity(channel: str, external_account_id: str) -> dict | None:
    binding = resolve_binding(channel, external_account_id)
    if binding is None:
        return None
    return {
        "user_id": binding["user_id"],
        "persona_id": binding["persona_id"] or "",
        "binding_id": int(binding["id"]),
        "source_version": int(binding["source_version"]),
    }


def identity_hash(channel: str, external_account_id: str) -> str:
    return account_hash(channel, external_account_id)


__all__ = ["identity_hash", "resolve_identity"]
