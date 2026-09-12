# -*- coding: utf-8 -*-
"""P3-04 E：「启用加密」一键 API 全流程回归（一次性数据）。

场景：明文数据目录（真实 schema + 会话 + 资产 + 人格卡 + 用户配置）→
POST /api/encryption/enable → 迁移完成 → 数据经加密库可读、人格卡仍可加载、
用户配置随迁、向量重建被调度；cleanup 后明文目录删除。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = tempfile.mkdtemp(prefix="tztuzhan_enc_enable_")
os.environ["TZTUZHAN_DATA_DIR"] = DATA
os.environ.setdefault("MEMORY_V2", "0")
os.environ.setdefault("MEMORY_MEM0", "0")

PASS = "enable-flow 口令"


def _make_plaintext_data() -> None:
    from backend.session import store as session_store

    data = Path(DATA)
    data.mkdir(parents=True, exist_ok=True)
    from backend.core.userdb import db

    db._ensure_connected()
    db.ensure_user("assistant-main")
    db.add_fact("assistant-main", "启用加密前的事实")
    db.close()
    session_store.init()
    from backend.agent import session as agent_session

    agent_session._connect().close()
    # 运行配置（应随迁）
    (data / "feature_flags.json").write_text(json.dumps({"compact_ui_enabled": False}), encoding="utf-8")
    # 人格文件库（应明文随迁且保持可读）
    persona_dir = data / "personas" / "default"
    persona_dir.mkdir(parents=True)
    (persona_dir / "card.md").write_text("# E2E 人格卡", encoding="utf-8")
    (data / "documents").mkdir()
    (data / "documents" / "a.txt").write_text("资产", encoding="utf-8")


def main() -> None:
    _make_plaintext_data()

    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.core.key_broker import reset_for_testing

    reset_for_testing()
    with TestClient(create_app()) as client:
        # 启用前：明文、无槽
        st = client.get("/api/encryption/status").json()
        assert st["data_encrypted"] is False and st["slots_initialized"] is False, st

        # 口令不一致 → 400 且不产生任何半截状态
        r = client.post("/api/encryption/enable",
                        json={"passphrase": "a", "passphrase_repeat": "b"})
        assert r.status_code == 400, r.text

        # 一键启用（现场初始化密钥槽 + 迁移 + 向量重建调度）
        r = client.post("/api/encryption/enable",
                        json={"passphrase": PASS, "passphrase_repeat": PASS})
        assert r.status_code == 200, r.text
        journal = r.json()["journal"]
        assert journal["state"] == "cleanup_pending", journal
        assert journal.get("carried_over"), journal

        # 加密态生效：status、重复启用 409
        st = client.get("/api/encryption/status").json()
        assert st["data_encrypted"] is True and st["journal_state"] == "cleanup_pending"
        r = client.post("/api/encryption/enable",
                        json={"passphrase": PASS, "passphrase_repeat": PASS})
        assert r.status_code == 409

        # 数据经加密库可读（惰性重连）
        facts = client.get("/api/memory/facts?limit=10").json()["facts"]
        assert any(f["content"] == "启用加密前的事实" for f in facts), facts

        # 人格文件库明文随迁且可读（/api/personas 走文件读取端）
        personas = client.get("/api/personas").json()
        assert personas.get("active", {}).get("name"), personas

        # 运行配置随迁
        assert json.loads((Path(DATA) / "feature_flags.json").read_text(encoding="utf-8"))[
            "compact_ui_enabled"] is False

        # 迁移门已放、数据目录无明文 bot.db 残留
        assert client.get("/api/encryption/status").json()["gate_engaged"] is False
        assert not (Path(DATA) / "bot.db").read_bytes().startswith(b"SQLite format 3")

        # cleanup：用户确认后删明文目录
        r = client.post("/api/encryption/cleanup")
        assert r.status_code == 200, r.text
        assert not Path(json.dumps(journal["plaintext_keep"]).strip('"')).exists()
        st = client.get("/api/encryption/status").json()
        assert st["journal_state"] == "encrypted"

    reset_for_testing()
    shutil.rmtree(DATA, ignore_errors=True)
    print("\n=== P3-04 E 启用加密全流程：全部通过 ===")


if __name__ == "__main__":
    main()
