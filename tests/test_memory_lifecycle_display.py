# -*- coding: utf-8 -*-
"""F07 对话内记忆生命周期露出：二次授权、版本化编辑、删除后不可再曝光。

验收锚点（docs/Zcode技术指导.md F07 + 调度文档批次 8）：
- 解释层只标本轮实际引用且允许展示的事实，返回时二次授权；
- 版本化编辑：带 expected_version 且已变化 → 409；不存在 → 404；
- 删除后解释快照不再可取全文（410/404 语义由权威 API 承担）；
- 取消固定不凭空延长原 expires_at。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_f07_"))

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core import memory_salience as ms
from backend.core.userdb import db

NOW = datetime(2026, 9, 8, 18, 0)


def _fact(uid: str, content: str, **kwargs) -> int:
    return int(db.add_fact(uid, content, **kwargs))


def test_snapshot_second_authorization_and_version() -> int:
    uid = "f07-version"
    db.ensure_user(uid)
    fid = _fact(uid, "用户喜欢猫")
    secret = db.add_fact(uid, "用户不提的私事", surface_policy="never_surface")
    meta = ms.lifecycle_for_facts(uid, [fid, secret])
    assert fid in meta and secret not in meta, "never_surface 不露出（二次授权）"
    assert meta[fid]["version"], "应带版本指纹"
    # 版本随内容变化
    first = meta[fid]["version"]
    with db._lock:
        db.conn.execute("UPDATE facts SET content='用户很喜欢猫' WHERE id=?", (fid,))
        db.conn.commit()
    assert ms.lifecycle_for_facts(uid, [fid])[fid]["version"] != first
    # 删除后不再露出
    with db._lock:
        db.conn.execute("DELETE FROM facts WHERE id=?", (fid,))
        db.conn.commit()
    assert ms.lifecycle_for_facts(uid, [fid]) == {}
    print("[OK] 二次授权 + 版本指纹随变化更新 + 删除后不再露出")
    return 0


def test_versioned_api_conflict_and_gone() -> int:
    from backend.core.persona_profiles import active_user_id

    uid = active_user_id()  # API 走当前激活人格
    db.ensure_user(uid)
    fid = _fact(uid, "用户喜欢下雨天")
    app = create_app()
    with TestClient(app) as client:
        meta = ms.lifecycle_for_facts(uid, [fid])
        version = meta[fid]["version"]
        # 版本不符 → 409
        r = client.put(f"/api/memory/facts/{fid}",
                       json={"content": "用户喜欢阵雨", "expected_version": "stale"})
        assert r.status_code == 409, r.text
        # 正确版本 → 200
        r = client.put(f"/api/memory/facts/{fid}",
                       json={"content": "用户喜欢阵雨", "expected_version": version})
        assert r.status_code == 200, r.text
        # 删除：不存在 → 404
        r = client.delete(f"/api/memory/facts/999999")
        assert r.status_code == 404
        r = client.delete(f"/api/memory/facts/{fid}")
        assert r.status_code == 200
        r = client.delete(f"/api/memory/facts/{fid}")
        assert r.status_code == 404, "删除后再次删除应 404（历史气泡不再可取全文）"
    print("[OK] 版本化编辑 409/200；删除后 404，快照不可再曝光")
    return 0


def test_unpin_does_not_extend_expiry() -> int:
    uid = "f07-unpin"
    db.ensure_user(uid)
    deadline = (NOW + timedelta(days=3)).isoformat(timespec="seconds")
    fid = db.add_fact(uid, "用户这周在赶项目", expires_at=deadline)
    meta = ms.lifecycle_for_facts(uid, [fid])
    assert meta[fid]["expires_at"] == deadline
    from backend.core.userdb import update_fact_pinned

    update_fact_pinned(uid, fid, True)
    assert ms.lifecycle_for_facts(uid, [fid])[fid]["retention"] == "长期保留"
    update_fact_pinned(uid, fid, False)
    after = ms.lifecycle_for_facts(uid, [fid])[fid]
    assert after["expires_at"] == deadline, "取消固定不得凭空延长原 expires_at"
    print("[OK] 取消固定不凭空延长原 expires_at")
    return 0


def main() -> int:
    failed = (
        test_snapshot_second_authorization_and_version()
        + test_versioned_api_conflict_and_gone()
        + test_unpin_does_not_extend_expiry()
    )
    if failed:
        print(f"\n=== F07 记忆生命周期露出：{failed} 项失败 ===")
        return 1
    print("\n=== F07 记忆生命周期露出：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
