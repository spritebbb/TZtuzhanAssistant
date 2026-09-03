# -*- coding: utf-8 -*-
"""Initialize fresh Tuzhan Assistant databases (idempotent, never wipes data).

Creates (or completes) the runtime schema under <package root>/data:
  - bot.db          (users / affection / memory / facts / tasks / kv ...)
  - sessions.db     (current session + archives)
  - agent_tasks.db  (long-running agent tasks)

Safe to run repeatedly: all schema creation is CREATE TABLE IF NOT EXISTS.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    data = ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)

    # bot.db: UserDB() runs the full schema on import
    from backend.core.userdb import db  # noqa: F401

    # sessions.db: module import calls init()
    from backend.session import store as session_store  # noqa: F401

    # agent_tasks.db: module import calls _init()
    from backend.agent import session as agent_session  # noqa: F401

    print("Databases ready under:", data)
    for p in sorted(data.glob("*.db")):
        print("  -", p.name, p.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
