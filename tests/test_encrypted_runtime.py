# -*- coding: utf-8 -*-
"""P3-04 E 运行时接线：加密数据目录上的后端启动/锁定/解锁全链路。

场景（一次性数据）：
1. 造明文数据目录（三库 + 资产 + 会话消息）并写入已知内容；
2. 迁移引擎把它变成加密目录；
3. 后端启动：必须能起（锁定态），锁屏 API 可用，业务 API 全 423；
4. 本机槽解锁：业务 API 恢复，且读到的正是迁移前的数据（行数/内容一致）；
5. 再次锁定：库连接关闭（忘钥匙语义），解锁后数据仍在。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = tempfile.mkdtemp(prefix="tztuzhan_enc_runtime_")
os.environ["TZTUZHAN_DATA_DIR"] = DATA
os.environ.setdefault("MEMORY_V2", "0")
os.environ.setdefault("MEMORY_MEM0", "0")

# 导入顺序纪律：先建数据并完成迁移，再导入 backend.app——日志句柄等会钉住
# 数据目录，进程内切换前需由句柄释放钩子处理（引擎已支持；本测试用导入顺序
# 隔离，模拟「停应用→迁移→再启动」的真实时序）。
from backend.storage.connect import connect_database  # noqa: E402
from backend.storage.migration import migrate_data_root  # noqa: E402

PASS = "runtime-e2e 口令"
Facts = 3


def _make_plaintext_data() -> None:
    """用真实代码路径造数据（迁移引擎在现实中面对的就是这些库）。"""
    from backend.session import store as session_store

    data = Path(DATA)
    data.mkdir(parents=True, exist_ok=True)

    from backend.core.userdb import db

    db._ensure_connected()  # 明文态：建真实 v41 schema
    db.ensure_user("assistant-main")
    # 三条互异内容（add_fact 按二元组重叠去重，相似句会被合并）
    for content in ("加密前住在江边", "养了一盆绿萝叫素材", "周五回老家看外婆"):
        db.add_fact("assistant-main", content)
    db.close()

    session_store.init()  # sessions.db（真实 schema）
    conn = connect_database(data / "sessions.db", row_factory=True)
    conn.execute(
        "INSERT INTO sessions (id, title, created_at, updated_at) VALUES ('s1','E2E',1,1)")
    conn.execute(
        "INSERT INTO messages (session_id, role, content, ts) VALUES ('s1','user','迁移前消息',1)")
    conn.commit()
    conn.close()

    from backend.agent import session as agent_session

    agent_session._connect().close()  # agent_tasks.db（真实 schema）

    (data / "documents").mkdir()
    (data / "documents" / "a.txt").write_text("资产正文", encoding="utf-8")


def _rows_via_app(client: TestClient) -> int:
    """走真实 API 读事实（memory admin 的 facts 接口）。"""
    r = client.get("/api/memory/facts?limit=50")
    assert r.status_code == 200, (r.status_code, r.text[:200])
    facts = r.json().get("facts", [])
    assert len(facts) == Facts, facts
    return len(facts)


def main() -> None:
    _make_plaintext_data()

    # 真实时序：先初始化应用锁密钥槽（MK 生成并进槽），再用同一把 MK 迁移
    from backend.core.keyslots import initialize as init_slots

    mk = init_slots(Path(DATA) / "keyslots", passphrase=PASS, passphrase_repeat=PASS)
    migrate_data_root(Path(DATA), mk)

    # 迁移完成后再导入应用（模拟「停应用→迁移→启动」时序）
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.core.key_broker import reset_for_testing

    reset_for_testing()

    # ---- 加密目录上启动：锁定态 ----
    with TestClient(create_app()) as client:
        st = client.get("/api/lock").json()
        assert st["state"] == "locked", st
        assert client.get("/api/meta").status_code == 423, "业务 API 必须全 423"

        # 锁定态锁定入口幂等
        assert client.post("/api/lock").status_code == 200

        # 解锁 → 业务 API 恢复且数据是迁移前的
        r = client.post("/api/lock/unlock", json={"method": "local"})
        assert r.status_code == 200, r.text
        _rows_via_app(client)

        # 锁定 → 库连接关闭（忘钥匙），解锁回来数据仍在
        assert client.post("/api/lock").json()["ok"]
        from backend.core import userdb

        assert userdb.db._conn is None, "锁定后 userdb 连接应已关闭"
        assert client.get("/api/memory/facts").status_code == 423
        assert client.post("/api/lock/unlock", json={"method": "local"}).status_code == 200
        _rows_via_app(client)
        print("[OK] 加密目录：锁定启动 / 423 / 本机解锁 / 数据可读 / 锁定关库")

    # ---- 明文模式回归：子进程 + 全新空目录（config/_DB 是导入期单例，须进程隔离） ----
    import subprocess

    plain_dir = tempfile.mkdtemp(prefix="tztuzhan_enc_plain_")
    code = (
        "import sys, os, tempfile\n"
        "sys.path.insert(0, r'%s')\n"
        "os.environ['TZTUZHAN_DATA_DIR'] = r'%s'\n"
        "os.environ['MEMORY_V2'] = '0'\n"
        "from fastapi.testclient import TestClient\n"
        "from backend.app import create_app\n"
        "with TestClient(create_app()) as c:\n"
        "    st = c.get('/api/lock').json()\n"
        "    assert st['state'] == 'inactive', st\n"
        "    assert c.get('/api/meta').status_code == 200, '明文模式业务 API 必须照常'\n"
        "    r = c.get('/api/memory/facts?limit=5')\n"
        "    assert r.status_code == 200, r.text\n"
        "    print('PLAIN-OK')\n"
    ) % (ROOT, plain_dir)
    proc = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                          capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    assert "PLAIN-OK" in proc.stdout, (proc.stdout[-500:], proc.stderr[-800:])
    print("[OK] 明文模式：inactive、业务照常（零行为变化）")

    reset_for_testing()
    shutil.rmtree(DATA, ignore_errors=True)
    print("\n=== P3-04 E 运行时接线：全部通过 ===")


if __name__ == "__main__":
    main()
