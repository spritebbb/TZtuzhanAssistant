# -*- coding: utf-8 -*-
"""L12 设备管理端点（离线骨架）。remote_gateway_enabled 默认关：未启用一律 403。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import devices
from ..core.features import flag

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _gate() -> JSONResponse | None:
    if not flag("remote_gateway_enabled"):
        return JSONResponse(
            {"ok": False, "error": "远程网关未启用（L12 离线骨架：域名/Access 配置后开启）"},
            status_code=403,
        )
    return None


class RegisterBody(BaseModel):
    display_name: str = Field(default="", max_length=60)
    platform: str = Field(default="", max_length=30)


class PushBody(BaseModel):
    endpoint: str = Field(min_length=8, max_length=2000)


@router.post("/register")
async def api_register(body: RegisterBody):
    gate = _gate()
    if gate is not None:
        return gate
    from ..core.persona_profiles import active_user_id

    return devices.register_device(
        active_user_id(), display_name=body.display_name, platform=body.platform
    )


@router.get("")
async def api_list():
    gate = _gate()
    if gate is not None:
        return gate
    from ..core.persona_profiles import active_user_id

    return {"ok": True, "devices": devices.list_devices(active_user_id())}


@router.post("/{device_id}/revoke")
async def api_revoke(device_id: str):
    gate = _gate()
    if gate is not None:
        return gate
    from ..core.persona_profiles import active_user_id

    result = devices.revoke_device(device_id, active_user_id())
    if not result.get("ok"):
        return JSONResponse(result, status_code=404)
    return result


@router.post("/push/subscriptions")
async def api_push_add(body: PushBody):
    gate = _gate()
    if gate is not None:
        return gate
    return devices.add_push_subscription("pending-device", body.endpoint)


@router.delete("/push/subscriptions")
async def api_push_delete(body: PushBody):
    gate = _gate()
    if gate is not None:
        return gate
    return devices.retract_push(body.endpoint)
