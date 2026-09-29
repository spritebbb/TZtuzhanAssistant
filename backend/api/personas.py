# -*- coding: utf-8 -*-
"""Markdown 人格卡档案与热切换接口。"""
from __future__ import annotations

import io
import shutil
import zipfile

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from ..core import persona as persona_runtime
from ..core import persona_generator, persona_profiles

router = APIRouter(prefix="/api/personas", tags=["personas"])

# NP-13 人格包：zip 根 = persona.md + portraits/{五档}.png（任意子集）
_PACK_MAX_BYTES = 30 * 1024 * 1024
_PORTRAIT_STATES = ("low", "plain", "lazy", "happy", "excited")


def _busy_response() -> JSONResponse | None:
    # 切换发生在回复或 Agent 执行期间会让尚未完成的模型调用读取另一张卡，
    # 因此等后台工作结束再切换；已经落库的数据仍按各自 user_id 隔离。
    from . import agent, chat

    busy_chat = any(not task.done() for task in chat._bg_tasks)
    busy_agent = any(not task.done() for task in agent._agent_bg_tasks)
    if busy_chat or busy_agent:
        return JSONResponse(
            {"ok": False, "error": "正在生成回复或执行任务，请结束后再切换人格"},
            status_code=409,
        )
    return None


def _activate(profile_id: str) -> dict:
    profile = persona_profiles.activate(profile_id)
    persona_runtime._persona_cache = None
    return profile


@router.get("")
async def api_personas_list():
    return {
        "ok": True,
        "active": persona_profiles.active_profile(),
        "personas": persona_profiles.list_profiles(),
    }


@router.post("/generate")
async def api_personas_generate(request: Request):
    # 生成不读当前激活卡、不切库，无需 busy 守卫；生成后的导入/激活走原路径自守卫。
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON 解析失败"}, status_code=400)
    brief = str((body or {}).get("brief") or "").strip()
    if not brief:
        return JSONResponse({"ok": False, "error": "角色设定简报不能为空"}, status_code=400)
    try:
        card = await persona_generator.generate_card(brief)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"生成失败：{exc}"}, status_code=502)
    return {"ok": True, "markdown": card, "name": persona_generator.card_name(card)}


def _unpack_persona_pack(data: bytes) -> tuple[bytes, dict[str, bytes]]:
    """解析人格包 zip → (persona.md 字节, {档位: png 字节})。

    安全面：拒绝 zip-slip（绝对路径 / .. / 反斜杠）、超包体上限；
    persona.md 必须在 zip 根；portraits/ 只收五档白名单 png，其余条目忽略。
    """
    if len(data) > _PACK_MAX_BYTES:
        raise persona_profiles.PersonaProfileError("人格包过大（上限 30MB）")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise persona_profiles.PersonaProfileError("不是有效的 zip 文件") from exc
    with zf:
        names = zf.namelist()
        md_name = "persona.md"
        if md_name not in names:
            raise persona_profiles.PersonaProfileError("人格包根目录缺少 persona.md")
        portraits: dict[str, bytes] = {}
        for name in names:
            if name == md_name:
                continue
            parts = name.split("/")
            # zip-slip 防御：绝对路径、..、反斜杠分隔一律拒绝
            if name.startswith("/") or "\\" in name or ".." in parts:
                raise persona_profiles.PersonaProfileError(f"人格包包含不安全的路径：{name}")
            if len(parts) == 2 and parts[0] == "portraits" and parts[1].startswith("_"):
                continue  # macOS 元数据等下划线开头条目忽略
            if len(parts) == 2 and parts[0] == "portraits":
                state = parts[1][: -len(".png")] if parts[1].endswith(".png") else ""
                if state in _PORTRAIT_STATES:
                    portraits[state] = zf.read(name)
        return zf.read(md_name), portraits


@router.post("/convert-stcard")
async def api_personas_convert_stcard(file: UploadFile):
    """SillyTavern 角色卡 → 菟菚人格卡预览（单向转换，不入库）。

    拍板边界：这是导入**向导**而非卡格式兼容——返回的 markdown 经前端预览/
    编辑后由用户确认走既有 /import；关系与记忆从零开始。
    """
    try:
        data = await file.read(10 * 1024 * 1024 + 1)
        from ..core.st_card import StCardError, convert_st_card

        markdown, name, warnings = convert_st_card(file.filename or "card.json", data)
        return {"ok": True, "markdown": markdown, "name": name, "warnings": warnings}
    except StCardError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"转换失败：{exc}"}, status_code=500)


@router.post("/import")
async def api_personas_import(file: UploadFile):
    busy = _busy_response()
    if busy:
        return busy
    try:
        # 读取上限必须 ≥ 包体上限（_PACK_MAX_BYTES）：此前按 1MB 截断读取，zip
        # 目录在文件尾部，截断后 BadZipFile——五档立绘包（天然超 1MB）全部导入
        # 失败且被误报为「不是有效的 zip 文件」；>1MB 的裸 .md 卡还会被静默截断。
        data = await file.read(_PACK_MAX_BYTES + 1)
        filename = file.filename or "persona.md"
        portraits: dict[str, bytes] = {}
        if filename.lower().endswith(".zip"):
            # NP-13 人格包：卡 + 立绘。卡走既有 import_card 路径（目录/设置/激活
            # 逻辑零重复），立绘解包到该人格目录的 portraits/ 下。
            data, portraits = _unpack_persona_pack(data)
            filename = "persona.md"
        profile = persona_profiles.import_card(filename, data)
        if portraits:
            from ..core.persona_profiles import _ROOT

            target = _ROOT / str(profile["id"]) / "portraits"
            target.mkdir(parents=True, exist_ok=True)
            try:
                for state, png in portraits.items():
                    (target / f"{state}.png").write_bytes(png)
            except OSError as exc:
                # DF-13：立绘写盘失败（磁盘满/坏档）→ 回滚本次导入。否则留下
                # 「有卡无立绘」的半成品，而 import_card 对同名目录只会加后缀
                # 新建——重导必产生重复人格，残档也没有任何用户侧清理入口。
                shutil.rmtree(_ROOT / str(profile["id"]), ignore_errors=True)
                return JSONResponse(
                    {"ok": False, "error": f"立绘写入失败，已取消本次导入：{exc}"},
                    status_code=500,
                )
        profile = _activate(profile["id"])
        # 触发创建该人格的私有 current 会话，切回时会继续原来的对话。
        from ..session.store import CURRENT_SESSION_ID, get_messages

        await get_messages(CURRENT_SESSION_ID)
        return {"ok": True, "persona": profile, "portraits": sorted(portraits)}
    except persona_profiles.PersonaProfileError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/{profile_id}/activate")
async def api_personas_activate(profile_id: str):
    busy = _busy_response()
    if busy:
        return busy
    try:
        profile = _activate(profile_id)
        from ..session.store import CURRENT_SESSION_ID, get_messages

        await get_messages(CURRENT_SESSION_ID)
        return {"ok": True, "persona": profile}
    except persona_profiles.PersonaProfileError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)


@router.patch("/{profile_id}")
async def api_personas_update(profile_id: str, request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise persona_profiles.PersonaProfileError("请求体必须是对象")
        profile = persona_profiles.update_profile(profile_id, body)
        return {"ok": True, "persona": profile}
    except persona_profiles.PersonaProfileError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON 解析失败"}, status_code=400)
