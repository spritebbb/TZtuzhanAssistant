# -*- coding: utf-8 -*-
"""记忆版本化编辑：乐观并发（expected_version）与删除后 404。

验收锚点（docs/Zcode技术指导.md F07 + 调度文档批次 8）：
- 版本化编辑：带 expected_version 且已变化 → 409；不存在 → 404；
- 删除后再次删除 404（历史气泡不再可取全文）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_f07_"))

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core import memory_salience as ms
from backend.core.userdb import db


def _fact(uid: str, content: str, **kwargs) -> int:
    return int(db.add_fact(uid, content, **kwargs))


def test_versioned_api_conflict_and_gone() -> int:
    from backend.core.persona_profiles import active_user_id

    uid = active_user_id()  # API 走当前激活人格
    db.ensure_user(uid)
    fid = _fact(uid, "用户喜欢下雨天")
    app = create_app()
    with TestClient(app) as client:
        row = db.conn.execute(
            "SELECT id, content, pinned, expires_at, surface_policy, status "
            "FROM facts WHERE id=?", (fid,)
        ).fetchone()
        version = ms.fact_version(row)
        # 版本不符 → 409
        r = client.put(f"/api/memory/facts/{fid}",
                       json={"content": "用户喜欢阵雨", "expected_version": "stale"})
        assert r.status_code == 409, r.text
        # 正确版本 → 200
        r = client.put(f"/api/memory/facts/{fid}",
                       json={"content": "用户喜欢阵雨", "expected_version": version})
        assert r.status_code == 200, r.text
        # 删除：不存在 → 404
        r = client.delete("/api/memory/facts/999999")
        assert r.status_code == 404
        r = client.delete(f"/api/memory/facts/{fid}")
        assert r.status_code == 200
        r = client.delete(f"/api/memory/facts/{fid}")
        assert r.status_code == 404, "删除后再次删除应 404"
    print("[OK] 版本化编辑 409/200；删除后 404")
    return 0


def main() -> int:
    failed = test_versioned_api_conflict_and_gone()
    if failed:
        print(f"\n=== 记忆版本化编辑：{failed} 项失败 ===")
        return 1
    print("\n=== 记忆版本化编辑：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
