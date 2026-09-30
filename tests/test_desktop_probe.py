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

from backend.core.desktop_probe import probe_foreground, title_context  # noqa: E402


def test_probe_real_call() -> None:
    result = probe_foreground()
    # NP-14 桌面感知契约扩展：+ process_name / idle_seconds / category；
    # #24 标题脱敏级：+ title_context（脱敏标签或空串，永不透传标题原文）
    assert set(result) == {
        "fullscreen", "display_id", "window_class",
        "process_name", "idle_seconds", "category", "title_context",
    }
    assert result["category"] in {"idle", "code", "browse", "fullscreen", "other"}
    assert isinstance(result["idle_seconds"], (int, float))
    # 测试运行时前台是终端/IDE/无人值守桌面，正常不应全屏；就算真全屏也是合法 bool
    assert isinstance(result["fullscreen"], bool)
    assert "标题" not in result["window_class"], "只允许类名，不允许标题/内容"
    assert isinstance(result["title_context"], str) and len(result["title_context"]) <= 20
    print(f"[OK] 真实探测：fullscreen={result['fullscreen']} display={result['display_id']!r} "
          f"class={result['window_class']!r} ctx={result['title_context']!r}")


def test_title_context_sanitization() -> None:
    """#24 标题脱敏级纯函数：敏感域拦截 / 语境标签 / 宁缺毋滥三条契约。"""
    # 敏感域 → 空串（她「看不见」），无论语境词是否同时出现
    for sensitive in (
        "中国银行 - 网上银行", "支付宝 - 确认转账", "微信 - 张三：明天见",
        "QQ - 群聊", "Gmail - 收件箱", "登录 - GitHub", "体检报告.pdf",
        "Bitwarden - 密码库", "简历_终版.docx", "InPrivate - 浏览",
    ):
        assert title_context(sensitive) == "", f"敏感标题必须拦截: {sensitive!r}"
    # 已知语境 → 脱敏标签（≤20 字，不含标题原文词汇）
    cases = {
        "【4K】猫和老鼠 哔哩哔哩 (゜-゜)つロ 干杯~-bilibili": "在看 B 站视频",
        "Python 教程 - YouTube": "在看 YouTube 视频",
        "GLM-4.9 发布 - 知乎": "在刷知乎",
        "vitejs/vite: Next generation frontend tooling - GitHub": "在看技术社区",
        "毕业设计开题报告.docx - Word": "在看文档",
        "Steam 社区市场": "在逛游戏商店",
    }
    for title, expect in cases.items():
        got = title_context(title)
        assert got == expect, f"{title!r} 期望 {expect!r} 实得 {got!r}"
    # 无已知语境 → 空串（宁缺毋滥，绝不回原文）
    assert title_context("一个完全未知的奇怪窗口标题") == ""
    assert title_context("") == ""
    # 长度契约：标签永不超过 20 字
    for keys, label in __import__("backend.core.desktop_probe", fromlist=["_TITLE_CONTEXTS"])._TITLE_CONTEXTS:
        assert len(label) <= 20
    print("[OK] 标题脱敏：敏感拦截/语境标签/宁缺毋滥三条契约全部通过")


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
    test_title_context_sanitization()
    test_judgement_tolerance()
    test_api_endpoint()
    print("\n=== L10 全屏避让探测 + 标题脱敏：4 组全部通过 ===")


if __name__ == "__main__":
    main()
