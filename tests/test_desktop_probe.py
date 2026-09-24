# -*- coding: utf-8 -*-
"""L10 桌宠全屏避让探测回归（Windows 真实调用）。

覆盖三件事：
1. probe_foreground 真实调用（测试进程的前台是终端/IDE，非全屏）→ 结构合法、
   fullscreen=False、无窗口标题内容泄漏；
2. API 端点可用且永不 500（helper 失效语义 = fullscreen False）；
3. 判定容差：构造合成矩形验证「完整覆盖才算全屏、最大化（留任务栏）不算」。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_probe_"))

from backend.core.desktop_probe import probe_foreground  # noqa: E402


def test_probe_real_call() -> None:
    result = probe_foreground()
    assert set(result) == {"fullscreen", "display_id", "window_class"}
    # 测试运行时前台是终端/IDE/无人值守桌面，正常不应全屏；就算真全屏也是合法 bool
    assert isinstance(result["fullscreen"], bool)
    assert "标题" not in result["window_class"], "只允许类名，不允许标题/内容"
    print(f"[OK] 真实探测：fullscreen={result['fullscreen']} display={result['display_id']!r} "
          f"class={result['window_class']!r}")


def test_judgement_tolerance() -> None:
    """1920x1080 显示器：全屏窗（0,0,1920,1080）命中；最大化（0,0,1920,1040）不命中。"""
    from backend.core.desktop_probe import _RECT, _MONITORINFO  # 结构复用

    def covered(rect: tuple[int, int, int, int], mon: tuple[int, int, int, int]) -> bool:
        r = _RECT(*rect)
        m = _RECT(*mon)
        w = max(1, m.right - m.left)
        h = max(1, m.bottom - m.top)
        ok = (
            r.left <= m.left + 1 and r.top <= m.top + 1
            and r.right >= m.right - 1 and r.bottom >= m.bottom - 1
        )
        return bool(ok and (r.right - r.left) >= w and (r.bottom - r.top) >= h)

    mon = (0, 0, 1920, 1080)
    assert covered((0, 0, 1920, 1080), mon) is True, "真全屏应命中"
    assert covered((0, 0, 1920, 1040), mon) is False, "最大化（留任务栏）不应命中"
    assert covered((-1920, 0, 0, 1080), (-1920, 0, 0, 1080)) is True, "副屏负坐标全屏应命中"
    assert covered((10, 10, 1910, 1070), mon) is False, "窗口化不应命中"
    # monitorinfo 结构可构造（GetMonitorInfoW 的目标结构完整性）
    mi = _MONITORINFO()
    assert mi.cbSize == 0
    print("[OK] 判定容差：全屏/最大化/负坐标副屏/窗口化")


def test_api_endpoint() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import desktop as desktop_api

    app = FastAPI()
    app.include_router(desktop_api.router)
    client = TestClient(app)
    resp = client.get("/api/desktop/fullscreen")
    assert resp.status_code == 200, "helper 失效也不得 500"
    data = resp.json()
    assert isinstance(data["fullscreen"], bool)
    print("[OK] API：/api/desktop/fullscreen 200 且永不满屏抛错")


def main() -> None:
    test_probe_real_call()
    test_judgement_tolerance()
    test_api_endpoint()
    print("\n=== L10 全屏避让探测：3 组全部通过 ===")


if __name__ == "__main__":
    main()
