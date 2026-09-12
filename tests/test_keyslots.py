# -*- coding: utf-8 -*-
"""P3-04 D 片：密钥槽单元回归（临时目录，绝不碰真实 data/）。

覆盖：初始化双槽、DPAPI/恢复口令往返、错误口令统一失败、二次输入校验、
轮换（MK 不变）、重复初始化拒绝、损坏槽、key_id 元数据、参数越界。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_keyslots_"))

from backend.core import keyslots as ks  # noqa: E402

PASS = "correct horse 电池 staple"


def test_initialize_creates_both_slots_and_roundtrips() -> None:
    d = Path(tempfile.mkdtemp(prefix="ks_init_"))
    mk = ks.initialize(d, passphrase=PASS, passphrase_repeat=PASS)
    slots = ks.describe_slots(d)
    assert slots.local_slot and slots.recovery_slot, "双槽都应存在"
    assert ks.read_local_slot(d) == mk, "DPAPI 本机槽应解回同一 MK"
    assert ks.read_recovery_slot(d, PASS) == mk, "恢复口令应解回同一 MK"
    assert ks.master_key_id(mk) == ks.recovery_slot_key_id(d)
    # 槽文件不含明文 MK
    blob = (d / ks.LOCAL_SLOT).read_bytes()
    assert mk not in blob
    payload = json.loads((d / ks.RECOVERY_SLOT).read_text(encoding="utf-8"))
    assert mk not in json.dumps(payload).encode()
    assert "passphrase" not in payload and PASS not in json.dumps(payload)
    print("[OK] 初始化双槽 + 两种方式往返 + 槽文件无明文")


def test_wrong_passphrase_unified_failure_and_repeat_check() -> None:
    d = Path(tempfile.mkdtemp(prefix="ks_wrong_"))
    mk = ks.initialize(d, passphrase=PASS, passphrase_repeat=PASS)
    for wrong in ("", "Correct horse 电池 staple", "短", "x" * 64):
        try:
            ks.read_recovery_slot(d, wrong)
            raise AssertionError(f"错误口令被接受: {wrong!r}")
        except ks.KeySlotError as exc:
            assert "口令" in str(exc) or "损坏" in str(exc)  # 统一语义，不细分
    # 二次输入不一致直接拒绝
    try:
        ks.write_recovery_slot(d, mk, "aaa", "bbb")
        raise AssertionError("不一致的二次输入被接受")
    except ks.KeySlotError:
        pass
    print("[OK] 错误口令统一失败 + 二次输入校验")


def test_rotation_keeps_mk_and_updates_slot() -> None:
    d = Path(tempfile.mkdtemp(prefix="ks_rot_"))
    mk = ks.initialize(d, passphrase="old pass", passphrase_repeat="old pass")
    old_payload = (d / ks.RECOVERY_SLOT).read_text(encoding="utf-8")
    ks.rotate_recovery_passphrase(d, "new pass", "new pass", current_mk=mk)
    assert ks.read_recovery_slot(d, "new pass") == mk, "轮换后 MK 不变"
    try:
        ks.read_recovery_slot(d, "old pass")
        raise AssertionError("旧口令在轮换后仍有效")
    except ks.KeySlotError:
        pass
    assert (d / ks.RECOVERY_SLOT).read_text(encoding="utf-8") != old_payload
    assert ks.recovery_slot_key_id(d) == ks.master_key_id(mk)
    print("[OK] 轮换：MK 不变、旧口令失效、key_id 不变")


def test_reinitialize_refused_and_corrupt_slot_detected() -> None:
    d = Path(tempfile.mkdtemp(prefix="ks_refuse_"))
    ks.initialize(d, passphrase=PASS, passphrase_repeat=PASS)
    try:
        ks.initialize(d, passphrase="x", passphrase_repeat="x")
        raise AssertionError("重复初始化被接受（会静默换 MK）")
    except ks.KeySlotError:
        pass
    # 损坏恢复槽：截断 ciphertext → 统一失败
    payload = json.loads((d / ks.RECOVERY_SLOT).read_text(encoding="utf-8"))
    payload["ciphertext"] = payload["ciphertext"][:-8] + "AAAAAAAA"
    (d / ks.RECOVERY_SLOT).write_text(json.dumps(payload), encoding="utf-8")
    try:
        ks.read_recovery_slot(d, PASS)
        raise AssertionError("损坏槽被接受")
    except ks.KeySlotError:
        pass
    # 未知版本
    payload["slot_version"] = 99
    (d / ks.RECOVERY_SLOT).write_text(json.dumps(payload), encoding="utf-8")
    try:
        ks.read_recovery_slot(d, PASS)
        raise AssertionError("未知版本被接受")
    except ks.KeySlotError:
        pass
    print("[OK] 重复初始化拒绝 + 损坏/未知版本槽检出")


def test_bad_keys_and_argon2_bounds() -> None:
    d = Path(tempfile.mkdtemp(prefix="ks_bounds_"))
    d.mkdir(exist_ok=True)
    for bad in (b"short", b"x" * 31, b"x" * 33, "not-bytes"):
        try:
            ks.write_recovery_slot(d, bad, PASS, PASS)  # type: ignore[arg-type]
            raise AssertionError("非法 MK 被接受")
        except ks.KeySlotError:
            pass
    mk = ks.generate_master_key()
    assert len(mk) == 32
    try:
        ks.write_recovery_slot(d, mk, PASS, PASS, time_cost=0)
        raise AssertionError("越界 argon2 参数被接受")
    except ks.KeySlotError:
        pass
    print("[OK] MK 长度与 Argon2 参数边界")


def main() -> None:
    test_initialize_creates_both_slots_and_roundtrips()
    test_wrong_passphrase_unified_failure_and_repeat_check()
    test_rotation_keeps_mk_and_updates_slot()
    test_reinitialize_refused_and_corrupt_slot_detected()
    test_bad_keys_and_argon2_bounds()
    print("\n=== P3-04 D 密钥槽：全部通过 ===")


if __name__ == "__main__":
    main()
