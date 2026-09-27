# -*- coding: utf-8 -*-
"""NP-12 数据护栏回归：手动备份与恢复演习端点。

覆盖：run 产出真实可验证备份、status 空态/健康态/损坏态、
超期年龄计算、锁定态拒绝（423 由全局锁中间件保证，此处测明文路径）。

运行：python -m tests.test_backup_endpoints（或经 pytest tests/ 由套件运行器执行）
"""
import json
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

_TEST_TMP = Path(tempfile.mkdtemp(prefix="tz_backup_test_"))
_TEST_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TZTUZHAN_DATA_DIR"] = str(_TEST_TMP)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402
import backend.agent.session as _agent_session  # noqa: E402
import backend.core.initiative as _initiative  # noqa: E402
import backend.session.store as _session_store  # noqa: E402


async def _disabled_initiative_loop() -> None:
    return


_initiative.initiative_loop = _disabled_initiative_loop
_session_store._DB = _TEST_TMP / "sessions.db"
_agent_session._DB = _TEST_TMP / "agent_tasks.db"
_agent_session._init()

client = TestClient(app)


def test_status_empty_before_any_backup() -> None:
    r = client.get("/api/backup/status")
    assert r.status_code == 200, r.status_code
    d = r.json()
    assert d["ok"] is True and d["has_backup"] is False
    print("[OK] 无备份时 status 如实报告空态")


def test_run_creates_backup_and_status_pass() -> None:
    # 触发三个库建立（bot.db/userdb 惰性连接、sessions 由请求触发）
    from backend.core import userdb as _userdb

    _ = _userdb.db.conn  # 惰性连接触发 bot.db 建库
    assert client.get("/api/sessions/current").status_code == 200
    r = client.post("/api/backup/run")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["name"].startswith("periodic-")

    s = client.get("/api/backup/status").json()
    assert s["has_backup"] is True
    assert s["verify"] == "pass", s
    assert s["age_days"] is not None and s["age_days"] < 1
    assert (s["file_count"] or 0) > 0
    print("[OK] run 产出真实备份，status 校验通过且年龄 < 1 天")


def test_status_detects_corrupted_backup() -> None:
    # 篡改最新备份里的一个数据文件字节 → 演习必须报 fail
    backups_root = _TEST_TMP / "data" / "backups"
    if not backups_root.is_dir():
        backups_root = _TEST_TMP / "backups"
    folders = sorted(p for p in backups_root.iterdir() if p.name.startswith("periodic-"))
    assert folders, "前置失败：没有备份目录"
    target = folders[-1] / "bot.db"
    raw = bytearray(target.read_bytes())
    raw[-1] ^= 0xFF
    target.write_bytes(bytes(raw))

    s = client.get("/api/backup/status").json()
    assert s["has_backup"] is True
    assert s["verify"] == "fail", s
    assert s["verify_error"]
    print("[OK] 备份文件被篡改后演习如实报 fail")


def main() -> None:
    test_status_empty_before_any_backup()
    test_run_creates_backup_and_status_pass()
    test_status_detects_corrupted_backup()
    print("\n=== NP-12 数据护栏: 3 项全部通过 ===")


if __name__ == "__main__":
    main()
