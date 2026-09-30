# -*- coding: utf-8 -*-
"""无代理直连策略回归（core/netenv.py + tts/llm 接入点）。

覆盖五件事：
1. force_direct_network：清 *_proxy 环境变量、设 NO_PROXY=*、幂等；
2. config 导入即生效：子进程带 https_proxy 导入 backend.core.config 后环境已净；
3. TTS：edge-tts 调用不再接收 proxy 参数（环境里埋死代理也不影响）；
4. LLM：_build_http_client 默认直连（trust_env=False），LLM_PROXY 逃生门语义不变；
5. urllib/requests 行为级验证：运行期重新埋死代理后，本地 HTTP 服务仍直连可达。

脚本 + main() 风格（pytest 不收集，见 pytest.ini python_files 白名单）。
入口：python tests/test_direct_network.py
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.netenv import _PROXY_KEYS, force_direct_network  # noqa: E402


def test_force_direct_network_cleans_env() -> None:
    planted = {"http_proxy": "http://127.0.0.1:1", "https_proxy": "http://127.0.0.1:1",
               "all_proxy": "socks5://127.0.0.1:1"}
    os.environ.update(planted)
    os.environ.pop("NO_PROXY", None)
    removed = force_direct_network()
    assert removed, f"应报告被清除的键: {removed}"
    # Windows 环境变量大小写不敏感（https_proxy 与 HTTPS_PROXY 同槽位），
    # 按变量名逐一验证「环境里已无任何代理变量」
    for key in _PROXY_KEYS:
        assert key not in os.environ, f"{key} 应已被清除"
    assert os.environ.get("NO_PROXY") == "*", "NO_PROXY=* 应被设置（requests 系注册表兜底）"
    # 幂等：二次调用无清除、无异常
    assert force_direct_network() == []
    print("[OK] netenv：清代理变量 + NO_PROXY=* + 幂等")


def test_config_import_enforces_direct() -> None:
    code = (
        "import os, sys; sys.path.insert(0, '.');"
        "from backend.core import config;"
        "print(os.environ.get('https_proxy', '<clean>'),"
        " os.environ.get('NO_PROXY', '<none>'),"
        " os.environ.get('no_proxy', '<none>'))"
    )
    env = {**os.environ, "https_proxy": "http://127.0.0.1:1"}
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(PROJECT_ROOT),
                          env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"config 导入失败: {proc.stderr[-400:]}"
    out = proc.stdout.strip()
    assert "<clean> * *" in out, f"导入 config 后代理变量应被清掉: {out}"
    print("[OK] config：模块导入即执行无代理直连（子进程实测）")


def test_tts_never_passes_proxy() -> None:
    # 假 edge_tts：记录 Communicate 收到的参数；save 落一个假 mp3
    import backend.core.tts as tts

    fake = types.ModuleType("edge_tts")
    calls: list[dict] = []

    class FakeCommunicate:
        def __init__(self, text: str, voice: str = "", **kwargs) -> None:
            calls.append({"text": text, "voice": voice, **kwargs})

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"RIFF-fake-mp3")

    fake.Communicate = FakeCommunicate
    original = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = fake
    try:
        with tempfile.TemporaryDirectory() as td:
            saved_dir = tts._TTS_DIR
            tts._TTS_DIR = Path(td)
            planted = {k: "http://127.0.0.1:1" for k in ("http_proxy", "https_proxy", "HTTPS_PROXY")}
            os.environ.update(planted)
            try:
                result = asyncio.run(tts.synth_async("代理不进合成参数"))
                assert result is not None and result.exists(), f"合成应成功落盘: {result}"
                assert calls and "proxy" not in calls[0], f"不得向 edge-tts 传 proxy: {calls[0]}"
                assert calls[0]["voice"] == tts.DEFAULT_VOICE
                assert "rate" not in calls[0] and "pitch" not in calls[0], "中性心情不带韵律参数"
            finally:
                for key in planted:
                    os.environ.pop(key, None)
                tts._TTS_DIR = saved_dir
    finally:
        if original is not None:
            sys.modules["edge_tts"] = original
        else:
            sys.modules.pop("edge_tts", None)
    print("[OK] tts：环境埋死代理，edge-tts 调用参数里也没有 proxy")


def test_llm_client_default_direct() -> None:
    from backend.core import config as cfg
    from backend.core.llm import _build_http_client

    async def _close(client) -> None:
        await client.aclose()

    original = cfg.config.llm_proxy
    try:
        cfg.config.llm_proxy = ""
        client = _build_http_client()
        assert client._trust_env is False and not client._mounts, "默认应无代理直连"
        asyncio.run(_close(client))

        cfg.config.llm_proxy = "off"
        client = _build_http_client()
        assert client._trust_env is False and not client._mounts, "off/direct/none 同直连"
        asyncio.run(_close(client))

        cfg.config.llm_proxy = "http://127.0.0.1:9"
        client = _build_http_client()
        assert client._mounts, "LLM_PROXY 给具体地址时走该代理（逃生门）"
        asyncio.run(_close(client))
    finally:
        cfg.config.llm_proxy = original
    print("[OK] llm：默认直连，LLM_PROXY 逃生门语义保留")


class _Hello(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802（BaseHTTPRequestHandler 约定）
        body = b"tuzhan-direct"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 静默
        pass


def test_urllib_and_requests_go_direct() -> None:
    server = HTTPServer(("127.0.0.1", 0), _Hello)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # 先按公开入口清理，再模拟「运行期又被塞进死代理」（如某工具later set env）
        force_direct_network()
        dead = {"http_proxy": "http://127.0.0.1:1", "https_proxy": "http://127.0.0.1:1"}
        os.environ.update(dead)
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            assert resp.read() == b"tuzhan-direct", "urlopen 应直连本地服务（空 ProxyHandler opener）"
        import requests

        r = requests.get(f"http://127.0.0.1:{port}/", timeout=5)
        assert r.status_code == 200 and r.content == b"tuzhan-direct", \
            "requests 应经 NO_PROXY=* 绕过死代理直连"
    finally:
        for key in dead:
            os.environ.pop(key, None)
        server.shutdown()
        server.server_close()
    print("[OK] urllib/requests：运行期死代理不劫持本地直连请求")


def main() -> None:
    test_force_direct_network_cleans_env()
    test_config_import_enforces_direct()
    test_tts_never_passes_proxy()
    test_llm_client_default_direct()
    test_urllib_and_requests_go_direct()
    print(f"\n=== 无代理直连策略：5 组全部通过（清理键集：{len(_PROXY_KEYS)} 个） ===")


if __name__ == "__main__":
    main()
