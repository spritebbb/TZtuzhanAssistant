# -*- coding: utf-8 -*-
"""菟菚桌面助手后端启动入口。

用法：
    python -m backend.main --host 127.0.0.1 --port 8801
    python -m backend.main --debug
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path（backend 是包，需从项目根运行）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402


def main() -> None:
    # Windows 下 stdout/stderr 可能为 GBK，无法编码非 ASCII。统一用 UTF-8，错误替换
    if sys.stdout.encoding and sys.stdout.encoding.upper() != "UTF-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    if sys.stderr.encoding and sys.stderr.encoding.upper() != "UTF-8":
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    ap = argparse.ArgumentParser(description="菟菚桌面助手后端")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（0.0.0.0 供局域网访问）")
    ap.add_argument("--port", type=int, default=8801, help="端口")
    ap.add_argument("--debug", action="store_true", help="调试模式（热重载）")
    args = ap.parse_args()

    print(f"🌿 菟菚桌面助手后端: http://{args.host}:{args.port}")
    # 把绑定地址告知运行时（remote.py 的空 token 防护需要据此收紧）
    os.environ["TZT_BIND_HOST"] = args.host.strip().lower() or "127.0.0.1"
    if args.host not in ("127.0.0.1", "::1", "localhost"):
        if not os.getenv("AGENT_REMOTE_TOKEN"):
            print(
                "⚠️  警告：当前绑定非回环地址（局域网可达）且未配置 AGENT_REMOTE_TOKEN，\n"
                "   除健康探针外的全部 /api/*、/mcp/*、/plugins/* 及人物图片\n"
                "   对局域网设备一律拒绝（来源 IP 语义：仅本机回环免 token）。\n"
                "   如需局域网设备访问，请在 .env 中配置 AGENT_REMOTE_TOKEN 并在调用时携带。"
            )
    uvicorn.run(
        "backend.app:app",
        host=args.host,
        port=args.port,
        reload=args.debug,
    )


if __name__ == "__main__":
    main()
