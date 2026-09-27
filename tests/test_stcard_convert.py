# -*- coding: utf-8 -*-
"""ST 卡导入向导回归：V2/V1 JSON、PNG 内嵌卡、字段映射、警告、坏输入。

运行：python -m tests.test_stcard_convert（或经 pytest tests/ 由套件运行器执行）
"""
import base64
import io
import json
import os
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MEMORY_EMBED_FORCE", "1")
os.environ.setdefault("MEMORY_MEM0", "0")
os.environ.setdefault("MOOD_CITY", "")
os.environ.setdefault("SEARCH_ENABLED", "0")

from backend.core.st_card import StCardError, convert_st_card  # noqa: E402


def _minimal_png_with_chara(card: dict) -> bytes:
    """手工构造含 tEXt/chara chunk 的最小合法 PNG。"""
    payload = base64.b64encode(json.dumps(card).encode("utf-8"))
    chunk_data = b"chara\x00" + payload

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + ctype + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"tEXt", chunk_data)
        + _chunk(b"IEND", b"")
    )


V2_CARD = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "星野 澪",
        "description": "深夜电台的主持人，声音很轻。",
        "personality": "温柔但毒舌",
        "scenario": "你们在深夜电台相识。",
        "first_mes": "……还没睡？",
        "mes_example": "<START>\n{{user}}: 睡不着\n{{char}}: 那正好，点首歌吧。",
        "system_prompt": "You are...",
        "tags": ["深夜", "电台", "温柔"],
        "creator": "someone",
        "character_book": {"entries": []},
    },
}

V1_CARD = {"name": "老版卡", "description": "V1 顶层字段的旧卡。", "personality": "简洁"}


def test_v2_json_convert() -> None:
    md, name, warnings = convert_st_card("mio.json", json.dumps(V2_CARD).encode("utf-8"))
    assert name == "星野 澪"
    assert "name: 星野 澪" in md
    assert "## 设定" in md and "深夜电台的主持人" in md
    assert "## 性格" in md and "## 场景" in md and "## 开场白" in md
    assert "subtitle: 深夜、电台、温柔" in md
    assert any("system_prompt" in w for w in warnings)
    assert any("世界书" in w for w in warnings)
    print("[OK] V2 卡：字段映射 + 副标题来自 tags + 未迁移项如实告知")


def test_v1_json_convert() -> None:
    md, name, warnings = convert_st_card("old.json", json.dumps(V1_CARD).encode("utf-8"))
    assert name == "老版卡"
    assert "V1 顶层字段的旧卡" in md
    print("[OK] V1 卡（顶层字段）兼容")


def test_png_embedded_card() -> None:
    png = _minimal_png_with_chara(V2_CARD)
    md, name, _ = convert_st_card("mio.png", png)
    assert name == "星野 澪"
    print("[OK] PNG tEXt/chara 内嵌卡解析")


def test_bad_inputs_rejected() -> None:
    for filename, data in [
        ("x.txt", b"hello"),
        ("x.json", b"{not json"),
        ("x.png", b"not a png"),
        ("empty.json", json.dumps({"data": {"name": "空卡"}}).encode()),
    ]:
        try:
            convert_st_card(filename, data)
            raise AssertionError(f"{filename} 应被拒绝")
        except StCardError:
            pass
    print("[OK] 坏容器/坏 JSON/无 PNG 卡/全空内容一律拒绝")


def main() -> None:
    test_v2_json_convert()
    test_v1_json_convert()
    test_png_embedded_card()
    test_bad_inputs_rejected()
    print("\n=== ST 卡导入向导: 4 项全部通过 ===")


if __name__ == "__main__":
    main()
