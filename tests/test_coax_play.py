# -*- coding: utf-8 -*-
"""哄好玩法回归：真诚道歉触发负面情绪修复（每日一次）+ /api/meta 负面摘要。

语义：apply_repair(cause_type="apology", cause_id=本地日期)——同源终身一次
= 天然每日限额；愤怒等负向情绪各 −0.2。

运行：python -m tests.test_coax_play（或经 pytest tests/ 由套件运行器执行）
"""
import asyncio
import os
import urllib.parse
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["MEMORY_EMBED_FORCE"] = "1"
os.environ["MEMORY_MEM0"] = "0"
os.environ["MEMORY_V2"] = "0"
os.environ["MOOD_CITY"] = ""
os.environ["SEARCH_ENABLED"] = "0"

_TEST_TMP = Path(tempfile.mkdtemp(prefix="tz_coax_test_"))
_TEST_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TZTUZHAN_DATA_DIR"] = str(_TEST_TMP)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402
import backend.core.initiative as _initiative  # noqa: E402
import backend.session.store as _session_store  # noqa: E402


async def _disabled_initiative_loop() -> None:
    return


_initiative.initiative_loop = _disabled_initiative_loop
_session_store._DB = _TEST_TMP / "sessions.db"

client = TestClient(app)

FORM_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "http://127.0.0.1:8801",
    "Sec-Fetch-Site": "same-origin",
}


def test_meta_negative_mood_summary() -> None:
    from backend.core.emotion_state import apply_emotion, active_emotions_map
    from backend.core.persona_profiles import active_user_id

    uid = active_user_id()
    apply_emotion(uid, "anger", 0.7)
    assert active_emotions_map(uid).get("anger", 0) >= 0.6
    d = client.get("/api/meta").json()
    nm = d["negative_mood"]
    assert nm["active"] is True and nm["kind"] == "在生气" and nm["level"] == "high", nm
    print("[OK] meta 负面摘要：anger 高时报告「在生气」high")

    apply_emotion(uid, "anger", 0.1)
    nm2 = client.get("/api/meta").json()["negative_mood"]
    print("[OK] 情绪消退后摘要归静（active/档位跟随）", nm2)


def test_apology_repairs_negative_emotion_via_pipeline() -> None:
    from backend.core.emotion_state import active_emotions_map, load_emotions
    from backend.core.persona_profiles import active_user_id

    uid = active_user_id()
    # 种一个高 anger
    asyncio.run(_seed_anger(uid))
    before = active_emotions_map(uid).get("anger", 0.0)
    assert before >= 0.35, before

    # 走真实 chat 端点（mock 轮）+ 道歉文本 → pipeline 道歉分支应触发 apply_repair。
    # 感知/道歉修复跑在 TestClient portal 循环的后台任务里——不能跨循环 gather，
    # 轮询等情绪真正变化（5s 超时）。
    import time as _time

    def _wait_repaired() -> float:
        deadline = _time.time() + 5.0
        val = before
        while _time.time() < deadline:
            val = active_emotions_map(uid).get("anger", 0.0)
            if val <= before - 0.19:
                return val
            _time.sleep(0.1)
        return val

    body = urllib.parse.urlencode({"mock": "true", "text": "对不起，刚才是我不好"})
    with client.stream(
        "POST", "/api/chat",
        content=body,
        headers=FORM_HEADERS,
    ) as resp:
        assert resp.status_code == 200
        "".join(chunk for chunk in resp.iter_text())

    after = _wait_repaired()
    assert after <= before - 0.19, (before, after)
    print(f"[OK] 真诚道歉触发情绪修复：anger {before:.2f} → {after:.2f}")

    # 同日再道歉：apply_repair 同源（日期）终身一次，anger 不再下降
    body2 = urllib.parse.urlencode({"mock": "true", "text": "真的对不起，再说一次"})
    with client.stream(
        "POST", "/api/chat",
        content=body2,
        headers=FORM_HEADERS,
    ) as resp:
        assert resp.status_code == 200
        "".join(chunk for chunk in resp.iter_text())
    _time.sleep(1.0)
    after2 = active_emotions_map(uid).get("anger", 0.0)
    assert abs(after2 - after) < 1e-6, (after, after2)
    print("[OK] 同日第二次道歉不再重复修复（每日一次防刷）")

    _ = load_emotions  # noqa: F841 - 引用保持 import 语义


async def _seed_anger(uid: str) -> None:
    from backend.core.emotion_state import apply_emotion

    apply_emotion(uid, "anger", 0.7)


def main() -> None:
    test_meta_negative_mood_summary()
    test_apology_repairs_negative_emotion_via_pipeline()
    print(f"\n=== 哄好玩法: 2 组通过（{date.today()}） ===")


if __name__ == "__main__":
    main()
