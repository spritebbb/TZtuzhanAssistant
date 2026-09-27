# -*- coding: utf-8 -*-
"""NP-07 花费人话化回归：单价可经设置页（config API）修改并即时生效。

背景：金额估算与真实 prompt/completion 拆分早已存在（usage_log），
本切片补的是「单价 UI 可改」——config API 白名单 + 校验 + 默认价对齐
deepseek-chat 参考价（入 2 / 出 8 元每百万 tokens）。

覆盖：默认价、POST 单价后 usage cost 按新价重算、非法单价 400、
GET 回读单价字段。

运行：python -m tests.test_usage_cost（或经 pytest tests/ 由套件运行器执行）
"""
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
# 不继承外部价格配置，锁定本测试的默认价断言
os.environ.pop("LLM_PRICE_INPUT_PER_MTOK", None)
os.environ.pop("LLM_PRICE_OUTPUT_PER_MTOK", None)

_TEST_TMP = Path(tempfile.mkdtemp(prefix="tz_usage_cost_test_"))
_TEST_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TZTUZHAN_DATA_DIR"] = str(_TEST_TMP)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402
from backend.core.config import config  # noqa: E402

# 注意：不 patch PROJECT_ROOT——config.reload() 会连带把人格文件等路径解析搬到
# 临时目录（2026-09-27 实证 ensure_library 崩）。POST 成功路径改为直接对 config
# 属性赋值（金额随单价变化是本切片本质）；POST 端点的「写 .env + reload」通用
# 机制不在本切片重复端到端，非法值 400 的校验发生在写文件之前，可安全端到端。

client = TestClient(app)

JSON_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:8801",
    "Sec-Fetch-Site": "same-origin",
}


def test_default_prices_aligned() -> None:
    assert config.llm_price_input_per_mtok == 2.0, config.llm_price_input_per_mtok
    assert config.llm_price_output_per_mtok == 8.0, config.llm_price_output_per_mtok
    print("[OK] 默认价对齐 deepseek-chat 参考价（入 2 / 出 8 元每百万）")


def test_config_get_exposes_prices() -> None:
    r = client.get("/api/config")
    assert r.status_code == 200
    c = r.json()["config"]
    assert c["llm_price_input_per_mtok"] == 2.0
    assert c["llm_price_output_per_mtok"] == 8.0
    print("[OK] GET /api/config 返回单价字段")


def test_cost_follows_price_change() -> None:
    # 单价热生效的本质：金额随 config 单价即时变化（POST /config 成功路径的
    # 写 .env + config.reload() 属于既有通用机制，此处直接属性赋值等价模拟）
    config.llm_price_input_per_mtok = 3.0
    config.llm_price_output_per_mtok = 15.0
    from backend.core.persona_profiles import active_user_id
    from backend.core.userdb import log_usage

    log_usage(active_user_id(), "reply", "test-model", 1000, 500)
    data = client.get("/api/usage/summary?days=1").json()["usage"]
    expect = round(1000 * 3.0 / 1_000_000 + 500 * 15.0 / 1_000_000, 4)
    assert data["today"]["cost"] == expect, (data["today"], expect)
    assert data["prices"] == {"input_per_mtok": 3.0, "output_per_mtok": 15.0}
    print("[OK] 单价变更后 usage cost 按新价重算")


def test_post_invalid_price_rejected() -> None:
    for bad in ("-1", "0", "2000", "abc"):
        r = client.post(
            "/api/config",
            json={"llm_price_input_per_mtok": bad},
            headers=JSON_HEADERS,
        )
        assert r.status_code == 400, (bad, r.status_code)
    # 非数字 "abc" 落入 float() 的 ValueError 分支；0/负/超上限落入区间分支
    print("[OK] 非法单价（0/负数/超上限/非数字）一律 400")


def main() -> None:
    test_default_prices_aligned()
    test_config_get_exposes_prices()
    test_cost_follows_price_change()
    test_post_invalid_price_rejected()
    print("\n=== NP-07 花费人话化: 4 项全部通过 ===")


if __name__ == "__main__":
    main()
