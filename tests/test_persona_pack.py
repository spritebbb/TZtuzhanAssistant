# -*- coding: utf-8 -*-
"""NP-13 人格包回归：zip（persona.md + portraits/五档）导入与立绘跟随。

覆盖：合法包导入（立绘落位 + /persona/full/{state} 跟随人格）、缺档回退
包内 plain、无立绘人格回退全局默认、缺 persona.md 400、zip-slip 400、坏 zip 400。

运行：python -m tests.test_persona_pack（或经 pytest tests/ 由套件运行器执行）
"""
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["MEMORY_EMBED_FORCE"] = "1"
os.environ["MEMORY_MEM0"] = "0"
os.environ["MEMORY_V2"] = "0"
os.environ["MOOD_CITY"] = ""
os.environ["SEARCH_ENABLED"] = "0"

_TEST_TMP = Path(tempfile.mkdtemp(prefix="tz_pack_test_"))
_TEST_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TZTUZHAN_DATA_DIR"] = str(_TEST_TMP)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402
import backend.core.initiative as _initiative  # noqa: E402
import backend.session.store as _session_store  # noqa: E402


async def _disabled_initiative_loop() -> None:
    return


_initiative.initiative_loop = _disabled_initiative_loop
_session_store._DB = _TEST_TMP / "sessions.db"

client = TestClient(app)

CARD = """---
name: 测试猫娘
voice: zh-CN-XiaoyiNeural
---

# 测试猫娘
测试用人格卡。
"""

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # 合法 PNG 魔数占位


def _make_pack(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, blob in entries.items():
            zf.writestr(name, blob)
    return buf.getvalue()


def _upload(filename: str, data: bytes):
    return client.post(
        "/api/personas/import",
        files={"file": (filename, data, "application/zip")},
        headers={"Origin": "http://127.0.0.1:8801", "Sec-Fetch-Site": "same-origin"},
    )


def test_import_valid_pack_and_portrait_follows() -> None:
    pack = _make_pack({
        "persona.md": CARD.encode("utf-8"),
        "portraits/plain.png": PNG_BYTES,
        "portraits/happy.png": PNG_BYTES,
        "portraits/.DS_Store": b"junk",
    })
    r = _upload("catgirl.zip", pack)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["persona"]["name"] == "测试猫娘"
    assert d["portraits"] == ["happy", "plain"]

    # 立绘路由：激活人格包内 plain 优先于全局默认
    r_full = client.get("/persona/full")
    assert r_full.status_code == 200
    assert r_full.content == PNG_BYTES
    r_happy = client.get("/persona/full/happy")
    assert r_happy.content == PNG_BYTES
    print("[OK] 合法人格包导入：立绘落位且 /persona/full 跟随人格")


def test_missing_state_falls_back_to_pack_plain() -> None:
    # 包只带 plain：访问其他档位回退包内 plain（而非全局差分）
    r_low = client.get("/persona/full/low")
    assert r_low.status_code == 200
    assert r_low.content == PNG_BYTES
    print("[OK] 缺档回退包内 plain")


def test_card_without_portraits_falls_back_global() -> None:
    # 纯 .md 导入（无立绘）：立绘路由回退全局资产
    r = _upload("plain.md", CARD.encode("utf-8"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("portraits") == []
    # 切到无立绘人格后，/persona/full 应返回全局默认（非测试 PNG 字节）
    r_full = client.get("/persona/full")
    assert r_full.status_code == 200
    assert r_full.content != PNG_BYTES
    print("[OK] 无立绘人格：立绘路由回退全局默认")


def test_pack_without_md_rejected() -> None:
    pack = _make_pack({"portraits/plain.png": PNG_BYTES})
    r = _upload("bad.zip", pack)
    assert r.status_code == 400
    assert "persona.md" in r.json()["error"]
    print("[OK] 缺 persona.md 400")


def test_zip_slip_rejected() -> None:
    pack = _make_pack({
        "persona.md": CARD.encode("utf-8"),
        "portraits/../../evil.png": PNG_BYTES,
    })
    r = _upload("slip.zip", pack)
    assert r.status_code == 400
    assert "不安全" in r.json()["error"]
    print("[OK] zip-slip 路径拒绝 400")


def test_corrupt_zip_rejected() -> None:
    r = _upload("broken.zip", b"this is not a zip file at all........")
    assert r.status_code == 400
    print("[OK] 坏 zip 400")


def test_pack_over_one_mb_imports() -> None:
    """DF-3 回归：>1MB 的包必须完整导入——此前 API 层按 1MB 截断读取，zip
    目录在尾部被截掉，五档立绘包（天然超 1MB）全部被误报「不是有效的 zip」。"""
    big_png = PNG_BYTES + b"\x00" * (1200 * 1024)  # ~1.2MB，跨过旧 1MB 截断线
    pack = _make_pack({
        "persona.md": CARD.encode("utf-8"),
        "portraits/plain.png": big_png,
    })
    r = _upload("big.zip", pack)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["portraits"] == ["plain"], d
    from backend.core import persona_profiles as _pp

    saved = (_pp.active_card_path().parent / "portraits" / "plain.png").read_bytes()
    assert len(saved) == len(big_png), "大立绘应完整落盘不被截断"


def test_pack_over_hard_limit_rejected() -> None:
    """30MB 硬上限必须真实生效（修复前该分支因 1MB 截断而不可达）。"""
    from backend.api import personas as _personas
    from backend.core import persona_profiles as _pp

    big_png = PNG_BYTES + b"\x00" * (30 * 1024 * 1024)
    pack = _make_pack({"persona.md": CARD.encode("utf-8"), "portraits/plain.png": big_png})
    try:
        _personas._unpack_persona_pack(pack)
        raise AssertionError("超过 30MB 的包应被拒绝")
    except _pp.PersonaProfileError as exc:
        assert "过大" in str(exc), exc


def main() -> None:
    test_import_valid_pack_and_portrait_follows()
    test_missing_state_falls_back_to_pack_plain()
    test_card_without_portraits_falls_back_global()
    test_pack_without_md_rejected()
    test_zip_slip_rejected()
    test_corrupt_zip_rejected()
    test_pack_over_one_mb_imports()
    test_pack_over_hard_limit_rejected()
    print("\n=== NP-13 人格包: 8 项全部通过 ===")


if __name__ == "__main__":
    main()
