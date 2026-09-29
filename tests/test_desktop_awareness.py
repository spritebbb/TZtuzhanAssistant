# -*- coding: utf-8 -*-
"""NP-14 桌面感知 v1 回归：类别归类矩阵、隐私默认（关=不探测）、
全屏门控主动性、pipeline 注入行。

红线断言：DESKTOP_AWARENESS 关闭时 /api/desktop/foreground 不调用探测；
类别人话只到「写代码/浏览/离开」级别，不含进程名/窗口标题。

运行：python -m tests.test_desktop_awareness（或经 pytest tests/ 由套件运行器执行）
"""
import asyncio
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
# 锁定默认关闭断言：必须用 setdefault 而非 pop——本机 .env 若写了
# DESKTOP_AWARENESS=1，import 时 load_dotenv（不覆盖已存在变量）会保留
# 我们设的 0；用 pop 则 .env 的 1 原样进入 config，用户机上此用例恒红。
os.environ.setdefault("DESKTOP_AWARENESS", "0")

_TEST_TMP = Path(tempfile.mkdtemp(prefix="tz_aware_test_"))
_TEST_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TZTUZHAN_DATA_DIR"] = str(_TEST_TMP)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402
from backend.core.config import config  # noqa: E402
from backend.core.desktop_probe import categorize  # noqa: E402
import backend.core.initiative as _initiative  # noqa: E402
import backend.session.store as _session_store  # noqa: E402


async def _disabled_initiative_loop() -> None:
    return


_initiative.initiative_loop = _disabled_initiative_loop
_session_store._DB = _TEST_TMP / "sessions.db"

client = TestClient(app)


def test_categorize_matrix() -> None:
    cases = [
        # (process_name, fullscreen, idle_seconds, expect)
        ("", False, 400.0, "idle"),
        ("Code.exe", False, 5.0, "code"),
        ("WindowsTerminal.exe", False, 0.0, "code"),
        ("chrome.exe", False, 1.0, "browse"),
        ("msedge.exe", True, 1.0, "fullscreen"),  # DF-6：全屏最优先——F11 看视频不许归 browse 照发主动
        ("Stellaris.exe", True, 2.0, "fullscreen"),  # 未知全屏 → fullscreen（不猜游戏）
        ("Spotify.exe", False, 2.0, "other"),
        ("", False, 0.0, "other"),
        ("PotPlayer.exe", True, 500.0, "fullscreen"),  # DF-6：全屏挂机也静默（「全屏信号已覆盖她该闭嘴」）
        ("PotPlayer.exe", False, 500.0, "idle"),  # 非全屏的空闲仍归 idle
    ]
    for name, fs, idle, expect in cases:
        got = categorize(name, fullscreen=fs, idle_seconds=idle)
        assert got == expect, (name, fs, idle, got, expect)
    print("[OK] 类别归类矩阵：10 例全中（全屏最优先 / 不猜游戏 / 未知归 other）")


def test_api_off_by_default_and_no_probe() -> None:
    assert config.desktop_awareness is False, "默认必须关闭（隐私默认）"
    probed = {"n": 0}

    import backend.api.desktop as _desktop

    real = _desktop.probe_foreground

    def counting():
        probed["n"] += 1
        return real()

    _desktop.probe_foreground = counting
    try:
        r = client.get("/api/desktop/foreground")
        assert r.status_code == 200
        d = r.json()
        assert d == {"enabled": False}, d
        assert probed["n"] == 0, "关闭时不得调用探测"
    finally:
        _desktop.probe_foreground = real
    print("[OK] 隐私默认：开关关闭时端点不探测，只返回 enabled=false")


def test_foreground_enabled_shape() -> None:
    config.desktop_awareness = True
    try:
        r = client.get("/api/desktop/foreground")
        assert r.status_code == 200
        d = r.json()
        assert d["enabled"] is True
        assert d["category"] in {"idle", "code", "browse", "fullscreen", "other"}
        assert "title" not in d and "window_text" not in d
    finally:
        config.desktop_awareness = False
    print("[OK] 开启后返回类别/空闲/全屏三字段，无标题无内容")


def test_initiative_fullscreen_gate(monkeypatch_like=None) -> None:
    import backend.core.initiative as initiative

    config.desktop_awareness = True
    calls = {"n": 0}

    def fake_probe():
        calls["n"] += 1
        return {"fullscreen": True, "category": "fullscreen", "idle_seconds": 1.0}

    real_probe = None
    import backend.core.desktop_probe as _probe_mod

    real_probe = _probe_mod.probe_foreground
    initiative_probe_holder = initiative

    # initiative 里是函数内 from .desktop_probe import probe_foreground——
    # 打模块属性即可被导入拿到
    _probe_mod.probe_foreground = fake_probe
    # 缩短全局冷却窗口让 _tick_once 真正走到门
    initiative._last_global_run = 0.0
    try:
        n = asyncio.run(initiative._tick_once())
    finally:
        _probe_mod.probe_foreground = real_probe
        config.desktop_awareness = False
    assert calls["n"] == 1, calls
    assert n == 0, "全屏时本轮主动应整体静默"
    print("[OK] 全屏门控：主动检查本轮静默（探测恰好一次）")


def test_awareness_line_semantics() -> None:
    from backend.core import pipeline as _pipeline

    # 关闭时恒空（不探测）
    assert config.desktop_awareness is False
    assert _pipeline._awareness_line() == ""
    # 开启 + code 类别 → 有分寸提示；fullscreen → 恒空（她该闭嘴而非提及）
    config.desktop_awareness = True
    real = _pipeline.probe_foreground if hasattr(_pipeline, "probe_foreground") else None
    import backend.core.desktop_probe as _probe_mod

    real_probe = _probe_mod.probe_foreground
    try:
        _probe_mod.probe_foreground = lambda: {"category": "code", "idle_seconds": 1.0}
        line = _pipeline._awareness_line()
        assert "写代码" in line and "进程" not in line
        _probe_mod.probe_foreground = lambda: {"category": "fullscreen", "idle_seconds": 1.0}
        assert _pipeline._awareness_line() == ""
    finally:
        _probe_mod.probe_foreground = real_probe
        config.desktop_awareness = False
    print("[OK] 注入行：开启才注入、类别级话术、全屏不提")


def main() -> None:
    test_categorize_matrix()
    test_api_off_by_default_and_no_probe()
    test_foreground_enabled_shape()
    test_initiative_fullscreen_gate()
    test_awareness_line_semantics()
    print("\n=== NP-14 桌面感知: 5 项全部通过 ===")


if __name__ == "__main__":
    main()
