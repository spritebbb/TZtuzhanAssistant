# -*- coding: utf-8 -*-
"""人格卡热切换：卡片/设置持久化与会话、记忆命名空间隔离。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="tz_personas_") as tmp:
        os.environ["TZTUZHAN_DATA_DIR"] = tmp
        os.environ["PERSONA_FILE"] = str(ROOT / "persona-菟菚.md")
        os.environ["MEMORY_V2"] = "0"
        os.environ["MEMORY_MEM0"] = "0"

        from backend.core import persona_profiles as profiles
        from backend.core import persona as persona_runtime
        from backend.core.userdb import db, list_facts
        from backend.session import store

        profiles.ensure_library()
        assert profiles.active_id() == "default"
        assert profiles.active_user_id() == "assistant-main", "默认人格必须复用旧数据命名空间"
        assert (Path(tmp) / "personas" / "default" / "persona.md").exists()
        assert (Path(tmp) / "personas" / "default" / "settings.json").exists()

        default_uid = profiles.active_user_id()
        db.add_fact(default_uid, "默认人格记忆")
        await store.append_messages("current", [{"role": "user", "content": "默认人格会话", "ts": 1.0}])

        card = b"""---\nname: Luna\ntheme: light\nvoice: zh-CN-XiaoyiNeural\nsubtitle: moon companion\n---\n\n# Luna\n\nYou are Luna.\n"""
        luna = profiles.import_card("persona-Luna.md", card)
        assert luna["name"] == "Luna"
        assert luna["theme"] == "light"
        assert luna["voice"] == "zh-CN-XiaoyiNeural"
        profiles.activate(luna["id"])

        luna_uid = profiles.active_user_id()
        assert luna_uid != default_uid
        persona_runtime._persona_cache = None
        prompt = persona_runtime.build_system_prompt(
            stage="熟悉",
            address=None,
            lover_confirm=False,
            first_chat=False,
            user_id=luna_uid,
        )
        assert "You are Luna." in prompt, "导入卡正文必须直接进入 system prompt"
        assert "腹黑" not in prompt and "病娇" not in prompt, "不得把原人格性格注入新人格"
        assert "菟丝子意象" not in prompt, "默认人格专属约束不得泄漏到新人格"
        assert list_facts(luna_uid) == [], "新人格不得读到旧人格记忆"
        assert await store.get_messages("current") == [], "新人格不得读到旧人格会话"
        db.add_fact(luna_uid, "Luna 独立记忆")
        await store.append_messages("current", [{"role": "user", "content": "Luna 会话", "ts": 2.0}])

        profiles.activate("default")
        assert [row["content"] for row in list_facts(default_uid)] == ["默认人格记忆"]
        default_messages = await store.get_messages("current")
        assert default_messages and default_messages[0]["content"] == "默认人格会话"

        profiles.activate(luna["id"])
        assert [row["content"] for row in list_facts(luna_uid)] == ["Luna 独立记忆"]
        luna_messages = await store.get_messages("current")
        assert luna_messages and luna_messages[0]["content"] == "Luna 会话"
        db.conn.close()
        # persona runtime 会初始化文件日志；Windows 下需先关闭 handler，临时目录才能删除。
        from backend.core.log import logger

        logger.remove()

    print("[OK] 人格卡热切换、设置持久化、会话与记忆隔离")


if __name__ == "__main__":
    asyncio.run(main())
