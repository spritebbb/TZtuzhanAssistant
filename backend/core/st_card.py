# -*- coding: utf-8 -*-
"""SillyTavern 角色卡 → 菟菚人格卡 单向转换器（ST 卡导入向导的后端）。

拍板边界（技术指导：不做完整卡格式兼容、不把人格改成可任意换卡）：这里是
**导入向导**——把 ST 卡的字段映射成菟菚人格 markdown，经用户预览/编辑后走
既有 import_card 入库；关系与记忆从零开始（向导 UI 明示）。酒馆专属概念
（world_book / extensions / 正则脚本等）不迁移。

支持：V2 卡（spec=chara_card_v2，字段在 data 下）与 V1 卡（字段在顶层）；
容器支持 .json 与 .png（PNG tEXt chunk `chara` 内嵌 base64 JSON）。
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any

MAX_CARD_BYTES = 10 * 1024 * 1024


class StCardError(ValueError):
    """ST 卡解析/转换失败（消息面向用户）。"""


def _unwrap_card(payload: dict[str, Any]) -> dict[str, Any]:
    """V2 卡字段在 data 下；V1 在顶层。返回字段层。"""
    if not isinstance(payload, dict):
        raise StCardError("卡内容不是 JSON 对象")
    data = payload.get("data")
    if isinstance(data, dict) and (data.get("name") or data.get("description")):
        return data
    return payload


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _png_chara_json(data: bytes) -> dict[str, Any]:
    """从 PNG 的 tEXt chunk `chara` 提取 base64 编码的卡 JSON。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise StCardError("不是有效的 PNG 文件")
    pos = 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"tEXt":
            keyword, _, text = chunk.partition(b"\x00")
            if keyword == b"chara":
                try:
                    payload = json.loads(base64.b64decode(text))
                except (binascii.Error, json.JSONDecodeError) as exc:
                    raise StCardError("PNG 里的角色卡数据损坏") from exc
                if not isinstance(payload, dict):
                    raise StCardError("PNG 里的角色卡数据不是 JSON 对象")
                return payload
        pos += 8 + length + 4  # data + CRC
    raise StCardError("PNG 里没有内嵌角色卡（tEXt/chara）")


def load_card_payload(filename: str, data: bytes) -> dict[str, Any]:
    """按容器类型解析出卡 JSON（字段层）。"""
    if len(data) > MAX_CARD_BYTES:
        raise StCardError("卡文件过大（上限 10MB）")
    name = (filename or "").lower()
    try:
        if name.endswith(".png"):
            return _unwrap_card(_png_chara_json(data))
        if name.endswith(".json"):
            payload = json.loads(data.decode("utf-8", errors="replace"))
            return _unwrap_card(payload)
        raise StCardError("支持 .json 或 .png 格式的 SillyTavern 角色卡")
    except StCardError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StCardError("卡文件不是有效的 JSON") from exc


def convert_card(payload: dict[str, Any]) -> tuple[str, str, list[str]]:
    """卡字段层 → (人格卡 markdown, 名字, warnings)。"""
    name = _clean(payload.get("name")) or "未命名角色"
    warnings: list[str] = []

    sections: list[str] = []
    description = _clean(payload.get("description"))
    if description:
        sections.append(f"## 设定\n\n{description}")
    personality = _clean(payload.get("personality"))
    if personality:
        sections.append(f"## 性格\n\n{personality}")
    scenario = _clean(payload.get("scenario"))
    if scenario:
        sections.append(f"## 场景\n\n{scenario}")
    mes_example = _clean(payload.get("mes_example"))
    if mes_example:
        sections.append(f"## 对话风格参考\n\n{mes_example}")
    first_mes = _clean(payload.get("first_mes"))
    if first_mes:
        sections.append(f"## 开场白\n\n{first_mes}")
    if not sections:
        raise StCardError("卡里没有可迁移的内容（description/personality 等全为空）")

    # ST 专属概念不迁移，但如实告知（不静默丢弃）
    if _clean(payload.get("system_prompt")) or _clean(payload.get("post_history_instructions")):
        warnings.append("卡自带的 system_prompt / 后置指令未迁移：菟菚有人格无关的固定状态系统，混入会互相打架")
    if isinstance(payload.get("character_book"), dict) or payload.get("world"):
        warnings.append("世界书 / lorebook 未迁移：世界书只是场景素材，如需要可作为知识库文档投喂")
    if _clean(payload.get("creator")) or _clean(payload.get("creator_notes")):
        warnings.append("作者信息未写入人格卡")

    tags = payload.get("tags")
    subtitle = ""
    if isinstance(tags, list) and tags:
        subtitle = "、".join(str(t) for t in tags[:4])[:80]

    front = ["---", f"name: {name}"]
    if subtitle:
        front.append(f"subtitle: {subtitle}")
    front.append("theme: dark")
    front.append("---")

    md = "\n".join([*front, "", f"# {name}", "", *"\n\n".join(sections).split("\n")])
    return md, name, warnings


def convert_st_card(filename: str, data: bytes) -> tuple[str, str, list[str]]:
    """入口：容器解析 + 字段映射。"""
    return convert_card(load_card_payload(filename, data))


__all__ = ["StCardError", "convert_st_card", "load_card_payload"]
