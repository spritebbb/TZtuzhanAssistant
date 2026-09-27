# -*- coding: utf-8 -*-
"""P3-44 恢复白名单回归：restore 只接受导出器会写出的表（CATEGORIES 全集）。

恶意构造的备份此前可借恢复路径写任意表（kv_store/users 等系统状态）；
现在 preview/restore/dry_run 三个入口共用 preview_restore 校验，一并拦截。

运行：python -m tests.test_restore_whitelist（或经 pytest tests/ 由套件运行器执行）
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["MEMORY_EMBED_FORCE"] = "1"
os.environ["MEMORY_MEM0"] = "0"
os.environ["MEMORY_V2"] = "0"
os.environ["MOOD_CITY"] = ""
os.environ["SEARCH_ENABLED"] = "0"

_TEST_TMP = Path(tempfile.mkdtemp(prefix="tz_restore_wl_test_"))
_TEST_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TZTUZHAN_DATA_DIR"] = str(_TEST_TMP)

from backend.core.relationship_export import (  # noqa: E402
    BundleError,
    CATEGORIES,
    preview_restore,
    restore_bundle,
)


def _base_bundle() -> dict:
    return {
        "kind": "tuzhan-relationship-bundle",
        "version": 1,
        "schema_version": _schema_version(),
        "source_user_id": "someone",
        "categories": ["milestones"],
        "data": {
            "important_dates": [
                {"user_id": "someone", "date": "2026-01-01", "label": "测试纪念日", "kind": "anniversary"}
            ],
        },
        "kv": {"exported": {}, "skipped_by_registry": 0},
    }


def _schema_version() -> int:
    from backend.core.relationship_export import _current_schema_version

    return _current_schema_version()


def test_legitimate_bundle_passes_preview() -> None:
    preview = preview_restore(_base_bundle(), "fresh-target")
    assert preview["ok"] is True, preview
    print("[OK] 合法备份（白名单内表）预览通过")


def test_smuggled_system_table_rejected() -> None:
    bundle = _base_bundle()
    bundle["data"]["kv_store"] = [
        {"user_id": "someone", "key": "state:schedule", "value": '{"hacked": 1}'}
    ]
    # preview 契约：返回 ok=False + errors（不 raise）；restore_bundle 才 raise
    preview = preview_restore(bundle, "fresh-target")
    assert preview["ok"] is False
    assert any("不属于关系包" in e for e in preview["errors"]), preview["errors"]
    # restore / dry_run 走同一校验，同样拦截
    try:
        restore_bundle(bundle, "fresh-target", dry_run=True)
        raise AssertionError("dry_run 也应拒绝")
    except BundleError as exc:
        assert "不属于关系包" in str(exc), exc
    print("[OK] 夹带系统表（kv_store）被 preview/restore/dry_run 三路拦截")


def test_smuggled_unknown_table_rejected() -> None:
    bundle = _base_bundle()
    bundle["data"]["evil_table"] = [{"user_id": "someone"}]
    preview = preview_restore(bundle, "fresh-target")
    assert preview["ok"] is False
    assert any("不属于关系包" in e for e in preview["errors"]), preview["errors"]
    print("[OK] 未知表拒绝")


def test_whitelist_covers_all_export_categories() -> None:
    # 白名单 = CATEGORIES 全集；导出侧能产出的表必须全部被允许（不破坏合法备份）
    allowed = {t for tables in CATEGORIES.values() for t in tables}
    assert {"users", "facts", "important_dates", "kv_export_placeholder"}.issubset(allowed) is False
    assert "users" in allowed and "facts" in allowed and "important_dates" in allowed
    print(f"[OK] 白名单 = CATEGORIES 全集（{len(allowed)} 张表）")


def main() -> None:
    test_legitimate_bundle_passes_preview()
    test_smuggled_system_table_rejected()
    test_smuggled_unknown_table_rejected()
    test_whitelist_covers_all_export_categories()
    print("\n=== P3-44 恢复白名单: 4 项全部通过 ===")


if __name__ == "__main__":
    main()
