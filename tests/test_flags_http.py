# -*- coding: utf-8 -*-
"""功能开关面板 HTTP 层回归：GET/POST /api/flags 必须按前端真实调用方式工作。

历史缺口：features.set_flag 写入端一直存在但全仓库无调用点（无 API、无前端），
output_hygiene / context_registry 两个开关因此长期停在默认关闭、无法从产品层激活。
本文件复刻 SettingsPanel.vue 的真实请求形态（JSON body POST）验证读写闭环。

运行：python -m tests.test_flags_http
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 开关持久化路径在 features 模块 import 期已捕获，须在导入被测模块前重定向到
# 临时目录，避免测试读写真实 data/feature_flags.json。
_TEST_TMP = Path(tempfile.mkdtemp(prefix="tz_flags_test_"))

import backend.core.features as _features

_features._FLAGS_PATH = _TEST_TMP / "feature_flags.json"
_features._cache = {"data": {}, "ts": 0.0}

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.config_api import router

app = FastAPI()
app.include_router(router)

# Electron 生产前端（同源 http://127.0.0.1:8801）的真实请求头
_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:8801",
    "Sec-Fetch-Site": "same-origin",
}


async def test_flags_get_lists_all() -> None:
    with TestClient(app) as client:
        r = client.get("/api/flags")
        assert r.status_code == 200, f"GET /api/flags 应 200: {r.status_code}"
        d = r.json()
        assert d["ok"] is True, f"应返回 ok=True: {d}"
        for key in ("output_hygiene_enabled", "context_registry_enabled", "profile_enabled"):
            assert key in d["flags"], f"flags 应含 {key}: {d['flags']}"
            assert key in d["labels"], f"labels 应含 {key}: {d['labels']}"
        assert isinstance(d["flags"]["output_hygiene_enabled"], bool)
    print("[OK] GET /api/flags 返回全部开关与说明")


async def test_flags_post_roundtrip_and_persistence() -> None:
    with TestClient(app) as client:
        # 前端方式：JSON body（SettingsPanel.toggleFlag）
        r = client.post("/api/flags", headers=_HEADERS,
                        json={"name": "output_hygiene_enabled", "value": False})
        assert r.status_code == 200, f"POST /api/flags 应 200: {r.status_code} {r.text}"
        d = r.json()
        assert d["ok"] is True and d["value"] is False
        assert d["flags"]["output_hygiene_enabled"] is False

        # 写盘验证：新实例（缓存清零）读到的值应与已写值一致
        _features._cache = {"data": {}, "ts": 0.0}
        assert _features.flag("output_hygiene_enabled") is False

        # 再切回 True
        r2 = client.post("/api/flags", headers=_HEADERS,
                         json={"name": "output_hygiene_enabled", "value": True})
        assert r2.status_code == 200 and r2.json()["value"] is True
    print("[OK] POST /api/flags 读写闭环且持久化")


async def test_flags_post_validation() -> None:
    with TestClient(app) as client:
        # 未知开关名
        r = client.post("/api/flags", headers=_HEADERS,
                        json={"name": "nonexistent_flag", "value": True})
        assert r.status_code == 400, f"未知开关应 400: {r.status_code}"
        # 非布尔 value
        r2 = client.post("/api/flags", headers=_HEADERS,
                         json={"name": "output_hygiene_enabled", "value": "yes"})
        assert r2.status_code == 400, f"非布尔 value 应 400: {r2.status_code}"
        # 缺字段
        r3 = client.post("/api/flags", headers=_HEADERS, json={"name": "profile_enabled"})
        assert r3.status_code == 400, f"缺 value 应 400: {r3.status_code}"
    print("[OK] POST /api/flags 校验拒绝未知开关/非法值")


async def test_every_flag_has_label() -> None:
    """面板按 flags 的键遍历渲染，缺标签的开关会显示英文键名且切不动。

    POST 白名单用的就是 _FLAG_LABELS，所以新开关漏登记 = 默认开启却关不掉。
    """
    from backend.api.config_api import _FLAG_LABELS
    from backend.core.features import FLAG_DEFAULTS

    missing = sorted(set(FLAG_DEFAULTS) - set(_FLAG_LABELS))
    assert not missing, f"缺少中文标签、用户无法切换的开关：{missing}"
    extra = sorted(set(_FLAG_LABELS) - set(FLAG_DEFAULTS))
    assert not extra, f"标签表残留已废弃的开关：{extra}"
    print("[OK] 开关全覆盖：每个开关都有中文标签且可切换")


async def main() -> None:
    await test_flags_get_lists_all()
    await test_flags_post_roundtrip_and_persistence()
    await test_flags_post_validation()
    await test_every_flag_has_label()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
