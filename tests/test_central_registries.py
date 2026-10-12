# -*- coding: utf-8 -*-
"""中央登记处接线守卫：新增数据表必须被显式分类，否则重置会漏清。

背景（2026-10-11 审计 P1-2 / P1-4）：profile_evolution_log / her_statements /
knowledge_opinion_decisions 三张带 user_id 的新表没进 reset 的 _TABLES——
重置后旧数据残留且继续被消费（她的自述、画像演变轨迹仍在库里），而**所有既有
测试照样全绿**。根因不是某张表写错，是「新增一张表必须记得更新 reset 清单」
这条接缝没有任何机制强制，于是每次扩张都可能新增针孔。

本守卫把这条接缝变成断言。作用域：backend/core/userdb.py 建的 bot.db 表——
其余库（sessions / agent_tasks / telemetry / vector）各走自己的重置路径，
不在 reset._TABLES 管辖内。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tz_registries_"))

_SCHEMA_FILE = ROOT / "backend" / "core" / "userdb.py"

# 带 user_id 但不进 reset 清单的显式豁免（每加一条都要写清理由）。
# job_runs：全局调度表，无 user_id 列，reset 按 scope_key 单独清理（见 reset.py）。
_EXEMPT: dict[str, str] = {
    "job_runs": "全局调度表（无 user_id），reset 按 scope_key 单独清",
}


def _parse_schema() -> dict[str, str]:
    """返回 {表名: 建表体}（userdb.py 里的 CREATE TABLE IF NOT EXISTS）。"""
    text = _SCHEMA_FILE.read_text(encoding="utf-8")
    return {
        name: body
        for name, body in re.findall(
            r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", text, re.S
        )
    }


def _degradation_tables() -> set[str]:
    """userdb.reset() 降级分支（文件删除失败时）逐表 DELETE 的清单。

    必须锚定到该分支的注释——userdb.py 里有好几处 `for table in (...)`，
    只按 `for table in (` 会命中别处（如 1461/1504/2844 行）。
    """
    text = _SCHEMA_FILE.read_text(encoding="utf-8")
    m = re.search(
        r"退化的清空路径：覆盖全部业务表.*?for table in \(\s*(.*?)\n\s*\):",
        text, re.S,
    )
    assert m, "未找到 userdb.reset() 降级分支（解析器需更新）"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_schema_parsed() -> int:
    """解析器自身守卫：正则失效会静默漏表，从而让本守卫形同虚设。"""
    schema = _parse_schema()
    assert len(schema) >= 80, f"schema 解析异常，只找到 {len(schema)} 张表"
    assert len(_degradation_tables()) >= 80, "降级分支解析异常"
    print(f"[OK] schema 解析：{len(schema)} 张表")
    return 0


def test_user_scoped_tables_are_classified() -> int:
    from backend.core import reset as reset_mod

    schema = _parse_schema()
    with_uid = {n for n, body in schema.items() if re.search(r"\buser_id\b", body)}
    classified = set(reset_mod._TABLES) | set(reset_mod._TABLES_NO_USER) | set(_EXEMPT)

    missing = sorted(with_uid - classified)
    assert not missing, (
        "以下带 user_id 的表未分类：重置会漏清它们，且既有测试不会发现。"
        f"请加进 reset._TABLES（或 _EXEMPT 并写明理由）：{missing}"
    )
    print(f"[OK] 带 user_id 的表全部已分类（{len(with_uid)} 张）")
    return 0


def test_reset_tables_exist_and_are_user_scoped() -> int:
    from backend.core import reset as reset_mod

    schema = _parse_schema()
    tables = set(reset_mod._TABLES)
    no_user = set(reset_mod._TABLES_NO_USER)

    stale = sorted((tables | no_user) - set(schema))
    assert not stale, f"reset 清单里有 schema 中不存在的表（陈旧条目）：{stale}"

    wrong = sorted(t for t in tables if not re.search(r"\buser_id\b", schema[t]))
    assert not wrong, f"reset._TABLES 里的表不带 user_id 列，清理会报错或误伤：{wrong}"
    print(f"[OK] reset 清单全部存在；_TABLES {len(tables)} 张均带 user_id")
    return 0


def test_degradation_path_covers_reset_tables() -> int:
    """降级清空路径必须覆盖权威清单的每一张表，否则两条路径会再次漂移。"""
    from backend.core import reset as reset_mod

    degrad = _degradation_tables()
    missing = sorted(set(reset_mod._TABLES) - degrad)
    assert not missing, (
        "userdb.reset() 降级分支漏了 reset._TABLES 里的表——文件删除失败时会残留："
        f"{missing}"
    )
    print(f"[OK] 降级分支覆盖 reset._TABLES 全部 {len(reset_mod._TABLES)} 张表")
    return 0


def main() -> int:
    test_schema_parsed()
    test_user_scoped_tables_are_classified()
    test_reset_tables_exist_and_are_user_scoped()
    test_degradation_path_covers_reset_tables()
    print("\n=== 中央登记处接线守卫：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
