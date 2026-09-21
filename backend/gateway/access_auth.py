# -*- coding: utf-8 -*-
"""L12 公网网关 Access JWT 校验（离线骨架，无 pyjwt——用 cryptography 实现 RS256）。

契约（docs/Zcode技术指导.md §16 L12）：

- 源站必须校验 Cloudflare Access JWT 的签名、aud、iss、exp——不能因为
  Tunnel 到源站的 peer 是 loopback 就沿用「loopback 免认证」分支；
- JWKS 缓存轮换失败时只允许**未过期缓存**短暂续用，超窗 fail closed；
- 本模块纯函数化：JWKS 由调用方注入（gateway 进程负责拉取与缓存），
  便于离线 fake 测试；任何校验失败抛 AccessAuthError（fail closed）。
"""
from __future__ import annotations

import base64
import json
import time
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class AccessAuthError(ValueError):
    """Access JWT 校验失败（fail closed；message 不含 token 原文）。"""


def _b64url_decode(data: str) -> bytes:
    padding_len = (-len(data)) % 4
    return base64.urlsafe_b64decode(data + "=" * padding_len)


def jwks_to_public_key(jwks: dict) -> rsa.RSAPublicKey:
    """JWKS 的 RSA 公钥（kty 必须 RSA；只取第一把）。"""
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not keys or not isinstance(keys, list):
        raise AccessAuthError("JWKS 缺少 keys")
    for jwk in keys:
        if jwk.get("kty") == "RSA":
            n = int.from_bytes(_b64url_decode(str(jwk["n"])), "big")
            e = int.from_bytes(_b64url_decode(str(jwk["e"])), "big")
            return rsa.RSAPublicNumbers(e, n).public_key()
    raise AccessAuthError("JWKS 无 RSA 公钥")


def validate_access_jwt(
    token: str,
    *,
    jwks: dict | None,
    team_domain: str,
    application_aud: str,
    now: float | None = None,
    leeway_sec: int = 30,
) -> dict:
    """校验一条 Access JWT，通过返回 claims（不含 token 原文）。

    jwks=None 表示 JWKS 暂不可得——fail closed 直接拒绝
    （网关进程的缓存续用策略由调用方实现，见 JwksCache）。
    """
    if jwks is None:
        raise AccessAuthError("JWKS 不可用：fail closed")
    parts = token.split(".")
    if len(parts) != 3:
        raise AccessAuthError("JWT 结构非法")
    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(sig_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AccessAuthError("JWT 编码非法") from exc
    if header.get("alg") != "RS256":
        raise AccessAuthError(f"不支持的算法：{header.get('alg')}")
    key = jwks_to_public_key(jwks)
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise AccessAuthError("签名不匹配") from exc
    moment = time.time() if now is None else float(now)
    if float(claims.get("exp", 0)) + leeway_sec < moment:
        raise AccessAuthError("JWT 已过期")
    expected_iss = f"https://{team_domain.strip().rstrip('/')}"
    if str(claims.get("iss")) != expected_iss:
        raise AccessAuthError("iss 不匹配")
    aud = claims.get("aud")
    aud_list = aud if isinstance(aud, list) else [aud]
    if application_aud not in [str(a) for a in aud_list]:
        raise AccessAuthError("aud 不匹配")
    return claims


class JwksCache:
    """JWKS 获取与缓存：轮换失败只允许未过期缓存短暂续用，超窗 fail closed。"""

    def __init__(self, fetch: Callable[[], dict | None], *,
                 fresh_sec: int = 3600, stale_sec: int = 600) -> None:
        self._fetch = fetch
        self._fresh_sec = fresh_sec
        self._stale_sec = stale_sec
        self._cached: dict | None = None
        self._cached_at: float = 0.0

    def get(self, *, now: float | None = None) -> dict | None:
        moment = time.time() if now is None else float(now)
        age = moment - self._cached_at
        if self._cached is not None and age < self._fresh_sec:
            return self._cached
        fresh = self._fetch()  # 失败由 fetch 返回 None（不抛）
        if fresh is not None:
            self._cached = fresh
            self._cached_at = moment
            return fresh
        # 拉取失败：未过期缓存短暂续用（stale 窗），超窗 fail closed
        if self._cached is not None and age < self._fresh_sec + self._stale_sec:
            return self._cached
        return None


__all__ = ["AccessAuthError", "JwksCache", "jwks_to_public_key", "validate_access_jwt"]
