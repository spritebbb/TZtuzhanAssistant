# -*- coding: utf-8 -*-
"""P3-2：加密态判定的自描述兜底。

此前 encrypted_mode() 只看数据目录旁的 encryption-migration.<name>.json。
journal 被删/丢失/搬迁后它返回 False，应用会拿明文方式去开一个加密库
（「file is not a database」），错误信息不指向真实原因。现在 journal 缺失时
以库文件头为准：SQLCipher 加密库没有 SQLite 明文魔数。

独立可运行脚本风格：python tests/test_encrypted_mode_fallback.py。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA = Path(tempfile.mkdtemp(prefix="tztuzhan_encmode_"))
os.environ["TZTUZHAN_DATA_DIR"] = str(DATA)

_SQLITE_MAGIC = b"SQLite format 3\x00"


def _runtime():
    from backend.storage import runtime

    runtime.reset_cache()  # 指纹缓存跨用例必须清
    return runtime


def _journal_path() -> Path:
    return DATA.parent / f"encryption-migration.{DATA.name}.json"


def test_plaintext_db_is_not_encrypted() -> int:
    rt = _runtime()
    _journal_path().unlink(missing_ok=True)
    (DATA / "bot.db").write_bytes(_SQLITE_MAGIC + b"\x00" * 100)
    assert rt.encrypted_mode() is False, "明文库（有 SQLite 魔数）不得判为加密态"
    print("[OK] 明文库 + 无 journal → 非加密态")
    return 0


def test_missing_journal_but_encrypted_header_detected() -> int:
    rt = _runtime()
    _journal_path().unlink(missing_ok=True)
    # 加密库：文件头是随机盐，没有 SQLite 魔数
    (DATA / "bot.db").write_bytes(b"\x9a\x3f\x01\xc4" * 40)
    assert rt.encrypted_mode() is True, "journal 丢失但库头非 SQLite 魔数 → 应判为加密态"
    print("[OK] journal 丢失 + 非 SQLite 头 → 判为加密态（自描述兜底）")
    return 0


def test_journal_state_still_authoritative() -> int:
    rt = _runtime()
    (DATA / "bot.db").write_bytes(_SQLITE_MAGIC + b"\x00" * 100)
    journal = _journal_path()
    journal.write_text('{"state": "encrypted"}', encoding="utf-8")
    try:
        assert rt.encrypted_mode() is True, "journal 明示 encrypted 时以 journal 为准"
    finally:
        journal.unlink(missing_ok=True)
    print("[OK] journal 存在时以 journal 状态为准")
    return 0


def main() -> int:
    test_plaintext_db_is_not_encrypted()
    test_missing_journal_but_encrypted_header_detected()
    test_journal_state_still_authoritative()
    print("\n=== P3-2 加密态自描述兜底：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
