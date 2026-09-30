# -*- coding: utf-8 -*-
"""无代理直连（出网统一策略）：出网请求一律直连，不消费任何代理配置。

背景（2026-09-27 实测）：本机环境常驻 https_proxy=http://127.0.0.1:10090 这类
随代理软件开关而失效的变量——代理进程没开时 TTS/LLM/联网工具整批挂掉，故障
表现随机（时好时坏）。菟菚的出网目标（微软 edge-tts、各 LLM 端点、hf-mirror
镜像、GPT-SoVITS 本地服务）在用户网络下均可直连，故统一策略为：无视代理环境
变量与系统代理设置，永远直连。确需代理的场景走显式配置（如 LLM_PROXY 指向
具体地址），不再从环境里捡。

`force_direct_network()` 必须在进程内任何 HTTP 客户端构造之前调用，故由
core/config（全项目最早的共享 import）在模块级执行：

- httpx 在 client 构造时按 trust_env 解析代理挂载（env 必须提前清）；
- urllib/requests/huggingface_hub 每次请求时读环境变量，Windows 上没有环境
  变量时还会回退读系统代理（注册表）——除清环境变量外，再设 NO_PROXY=* 让
  requests 系按「所有主机绕过代理」处理，并给 urllib 装空 ProxyHandler 的
  默认 opener 兜底；
- edge-tts 走 aiohttp（默认不读环境变量），调用方的显式 proxy 参数已移除。
"""
from __future__ import annotations

import os

_PROXY_KEYS = (
    "http_proxy", "https_proxy", "all_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
)


def force_direct_network() -> list[str]:
    """清除进程代理配置，保证后续出网全部直连；返回被清除的环境变量名。

    幂等：重复调用无副作用（config.reload 会再次调用）。
    """
    removed = [k for k in _PROXY_KEYS if os.environ.pop(k, None) is not None]
    # NO_PROXY=* 兜底：requests/urllib3 系（huggingface_hub 下载等）即使后来
    # 环境里又出现代理变量、或系统代理（注册表）存在，也按全主机绕过处理。
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    _install_direct_urllib_opener()
    return removed


def _install_direct_urllib_opener() -> None:
    """给 urllib.request 装无代理默认 opener（urlopen 全局生效）。

    空 ProxyHandler = 不用任何代理、也不回退注册表系统代理。带标记防重复装，
    避免覆盖其他代码在导入期安装的自定义 opener。
    """
    import urllib.request

    if getattr(urllib.request, "_tuzhan_direct_opener", False):
        return
    urllib.request.install_opener(
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
    )
    urllib.request._tuzhan_direct_opener = True  # type: ignore[attr-defined]
