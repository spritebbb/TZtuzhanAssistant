# -*- coding: utf-8 -*-
"""安全校验：路径白名单 + 危险命令黑名单（语义级）。

被本机操控工具（run_command / file 系列 / 进程 / 应用）复用：
- check_path(path)：目标路径必须在允许的根目录内（config.agent_allowed_roots，
  为空时默认项目 workspace 目录 + 项目根），越界返回错误信息
- check_command(cmd)：命中危险命令黑名单（config.agent_block_cmds）直接拒绝，
  不弹确认
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from ..core.config import config

# 默认允许根目录：项目根 + workspace（不存在则用项目根）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE = _PROJECT_ROOT / "workspace"
_DEFAULT_ROOTS = [str(_PROJECT_ROOT), str(_WORKSPACE)]


def allowed_roots() -> list[Path]:
    """返回允许操作的根目录列表（resolve 后）。"""
    raw = config.agent_allowed_roots or []
    roots: list[Path] = []
    for r in raw:
        try:
            roots.append(Path(r).expanduser().resolve())
        except (ValueError, OSError):
            continue
    if not roots:
        roots = [Path(p).resolve() for p in _DEFAULT_ROOTS]
    return roots


def check_path(path: str | Path) -> tuple[bool, str]:
    """校验目标路径是否在允许根目录内。

    Returns:
        (ok, error_msg)
    """
    if not path:
        return False, "（缺少路径）"
    try:
        p = Path(path).expanduser().resolve()
    except (ValueError, OSError):
        return False, "（路径无效）"
    p_norm = str(p).casefold()  # Windows 路径大小写不敏感，统一小写比较
    for root in allowed_roots():
        try:
            r_norm = str(root).casefold().rstrip("\\/")
            if p_norm == r_norm or p_norm.startswith(r_norm + os.sep):
                return True, ""
        except Exception:
            continue
    roots_str = "、".join(str(r) for r in allowed_roots())
    return False, f"（路径不在允许目录内：仅可操作 {roots_str}）"


# 危险命令黑名单（小写匹配，语义级；命中直接拒绝）
# 拆成多个片段正则，避免误伤（如 "format" 单独出现可能是别的意思，
# 但 "format c:" 等组合必然危险）
_BLOCK_PATTERNS = [
    re.compile(r"format\s+[a-z]:", re.I),          # format c:
    re.compile(r"rd\s+/s", re.I),                   # rd /s
    re.compile(r"rd\s+/q", re.I),                   # rd /q（常与 /s 组合）
    re.compile(r"rmdir\s+/s", re.I),                # rmdir /s /q（rm -rf 的 Windows 等价）
    re.compile(r"rm\s+-rf", re.I),                  # rm -rf
    re.compile(r"rm\s+-r\b", re.I),                 # rm -r（递归删除）
    re.compile(r"del\s+/f", re.I),                  # del /f
    re.compile(r"del\s+/q\s*/f", re.I),             # del /q /f（顺序颠倒变体）
    re.compile(r"del\s+/q(?:\s|$)", re.I),          # del /q（静默删除）
    re.compile(r"del\s+/s(?:\s|$)", re.I),          # del /s（递归删除，未带 /q 变体）
    re.compile(r"erase\b", re.I),                   # erase（del 的别名）
    re.compile(r"del\s+/s\s*/q", re.I),             # del /s /q
    re.compile(r"shutdown\b", re.I),                # 关机
    re.compile(r"reg\s+delete", re.I),              # 注册表删除
    re.compile(r"diskpart\b", re.I),                # 磁盘分区
    re.compile(r"net\s+user\b", re.I),              # 用户管理
    re.compile(r"takeown\b", re.I),                 # 获取所有权
    re.compile(r"icacls\b", re.I),                  # ACL 修改
    re.compile(r"taskkill\s+/f", re.I),             # 强杀进程（任务管理器关键进程）
    re.compile(r"powershell\s+-(?:enc|encod)", re.I),  # 编码命令（混淆）
    re.compile(r"cipher\s+/w", re.I),               # 覆写磁盘
    re.compile(r"Remove-Item\s+.*-Recurse", re.I),  # PS 递归删除
    re.compile(r"Remove-Item\s+.*-r\b", re.I),      # PS -r 别名递归
    re.compile(r"rm\s+-recurse", re.I),             # PowerShell rm -recurse
    re.compile(r"Clear-Content\s+[A-Za-z]:\\Windows", re.I),  # 清系统目录
]

# 额外关键词（更宽松的兜底）
_BLOCK_KEYWORDS = ("sudo rm", "fsutil", "bcdedit", "gpedit", "net stop", "sc delete",
                   "wmic", "vssadmin", "attrib -r -s -h /s /d")


def check_command(command: str) -> tuple[bool, str]:
    """校验命令是否命中危险黑名单。

    Returns:
        (ok, error_msg)；ok=False 表示直接拒绝（不弹确认）。
    """
    if not command:
        return False, "（缺少命令）"
    lower = command.lower()
    for pat in _BLOCK_PATTERNS:
        if pat.search(command):
            return False, f"（命令被安全策略拒绝：命中危险模式 {pat.pattern}）"
    for kw in _BLOCK_KEYWORDS:
        if kw in lower:
            return False, f"（命令被安全策略拒绝：包含危险关键词 {kw}）"
    # 用户可配置黑名单（AGENT_BLOCK_CMDS，分号分隔；命中即拒，不弹确认）
    for extra in getattr(config, "agent_block_cmds", []):
        if extra and extra in lower:
            return False, f"（命令被安全策略拒绝：命中配置黑名单 [{extra}]）"
    return True, ""


def check_cwd(cwd: str | None) -> tuple[bool, str]:
    """校验命令工作目录是否在允许根目录内（空则放行，用默认工作目录）。"""
    if not cwd:
        return True, ""
    return check_path(cwd)


def _ip_private(ip) -> bool:
    """判断 IP 是否为内网/本机/保留等不可信地址。"""
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def check_url(url: str) -> tuple[bool, str]:
    """校验 URL 是否为可安全访问的公网 http(s) 地址（SSRF 防护）。

    返回 (ok, error)。拒绝：非 http/https、缺少主机名、本机/内网/保留地址；
    域名会解析一次，任一解析结果为内网地址也拒绝。
    调用方必须对每个重定向跳转目标重新调用本函数复检。
    """
    import ipaddress
    import socket
    import urllib.parse

    if not url:
        return False, "（缺少 URL）"
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False, "（URL 无法解析）"
    if parsed.scheme not in ("http", "https"):
        return False, "（仅支持 http/https 链接）"
    host = (parsed.hostname or "").strip().strip("[]").lower()
    if not host:
        return False, "（链接缺少主机名）"
    if host in ("localhost", "localhost.localdomain"):
        return False, "（出于安全考虑，不支持访问本地/内网地址）"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _ip_private(ip):
            return False, "（出于安全考虑，不支持访问本地/内网地址）"
        return True, ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False, "（域名解析失败，按内网地址拒绝）"
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _ip_private(addr):
            return False, "（出于安全考虑，不支持访问本地/内网地址）"
    return True, ""


# ---- 远程/受控端点鉴权（统一"来源 IP"语义，供 /mcp/*、/api/mcp/*、/api/agent/*、
#      /api/* 写端点、/plugins/* 复用）----


def is_loopback_peer(peer_ip: str | None) -> bool:
    """判断请求来源 IP 是否为本机回环。

    语义定义在"请求来自哪个进程"而非"服务绑在哪"：绑 0.0.0.0 时本机走
    127.0.0.1 的请求来源仍是回环，应当免 token。判定只用 socket 来源
    （request.client.host），不信任 X-Forwarded-For 等可伪造代理头。
    TestClient 的 client.host 是 "testclient"，一并视为本机。
    """
    import ipaddress

    if not peer_ip:
        # 无来源信息时按本机处理（正常 HTTP 请求都会有 client；仅防御性兜底）
        return True
    ip = peer_ip.strip().lower()
    if ip in ("localhost", "127.0.0.1", "::1", "[::1]", "testclient"):
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    # IPv4 映射的 IPv6（::ffff:127.0.0.1）也按回环处理
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped is not None and bool(mapped.is_loopback)


def remote_token_ok_by_peer(token: str, peer_ip: str | None) -> bool:
    """统一鉴权（来源 IP 语义）：

    - 回环来源（127.0.0.1 / ::1）→ 免 token（本机进程信任，无论服务绑在哪）；
    - 非回环来源（局域网/公网）→ 必须携带与 AGENT_REMOTE_TOKEN 匹配的 token，
      未配置 token 时一律拒绝（不放开"局域网裸调"）。
    """
    if is_loopback_peer(peer_ip):
        return True
    if not config.agent_remote_token:
        return False
    return secrets.compare_digest(token or "", config.agent_remote_token)


def request_token(request) -> str:
    """从 Authorization: Bearer <token> 头或 ?token= query 提取凭据。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.query_params.get("token") or "").strip()
