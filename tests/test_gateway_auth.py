# -*- coding: utf-8 -*-
"""L12 网关 Access JWT 校验回归（fake JWKS，用 cryptography 现场造 RSA 密钥对）。

覆盖：
1. 合法 JWT（RS256 签名/aud/iss/exp 全对）→ 返回 claims；
2. 签名不匹配 / 过期 / iss 不符 / aud 不符 / alg 不符 / 结构坏 → 全部拒绝；
3. JWKS 不可用 → fail closed；
4. JwksCache：新鲜命中、拉取失败未过期缓存续用、超窗 fail closed。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_gwa_"))

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

from backend.gateway.access_auth import (  # noqa: E402
    AccessAuthError,
    JwksCache,
    validate_access_jwt,
)

TEAM = "tuzhan.example.cloudflareaccess.com"
AUD = "app-aud-123"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_jwt(key: rsa.RSAPrivateKey, *, iss=f"https://{TEAM}", aud=AUD, exp_offset=600,
             alg="RS256") -> str:
    header = {"alg": alg, "typ": "JWT"}
    claims = {"sub": "user-1", "iss": iss, "aud": aud,
              "exp": int(time.time()) + exp_offset}
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    sig = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(sig)}"


def make_jwks(pub: rsa.RSAPublicKey) -> dict:
    nums = pub.public_numbers()
    return {"keys": [{
        "kty": "RSA",
        "n": _b64url(nums.n.to_bytes((nums.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(nums.e.to_bytes((nums.e.bit_length() + 7) // 8, "big")),
    }]}


def _expect_reject(token: str, jwks, why: str) -> None:
    try:
        validate_access_jwt(token, jwks=jwks, team_domain=TEAM, application_aud=AUD)
        raise AssertionError(f"应拒绝：{why}")
    except AccessAuthError:
        pass


def test_valid_and_rejected_tokens() -> None:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = make_jwks(priv.public_key())
    token = make_jwt(priv)
    claims = validate_access_jwt(token, jwks=jwks, team_domain=TEAM, application_aud=AUD)
    assert claims["sub"] == "user-1"

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _expect_reject(make_jwt(other), jwks, "签名不匹配（非信任公钥）")
    _expect_reject(make_jwt(priv, iss="https://evil.example"), jwks, "iss 不符")
    _expect_reject(make_jwt(priv, aud="other-app"), jwks, "aud 不符")
    _expect_reject(make_jwt(priv, exp_offset=-3600), jwks, "已过期")
    _expect_reject(make_jwt(priv, alg="HS256"), jwks, "alg 不符")
    _expect_reject("not.a.jwt.x.y", jwks, "结构坏")
    _expect_reject(token, None, "JWKS 不可用 fail closed")
    print("[OK] JWT 校验：合法通过；签名/iss/aud/exp/alg/结构/JWKS 缺失全拒")


def test_jwks_cache_fail_closed() -> None:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    good = make_jwks(priv.public_key())
    state = {"available": True}
    cache = JwksCache(lambda: good if state["available"] else None,
                      fresh_sec=100, stale_sec=50)
    t0 = 1_000_000.0
    assert cache.get(now=t0) is good
    # 新鲜期内直接命中
    assert cache.get(now=t0 + 50) is good
    # 过新鲜期 + 拉取失败：stale 窗内续用
    state["available"] = False
    assert cache.get(now=t0 + 120) is good, "stale 窗内允许续用未过期缓存"
    # 超 stale 窗：fail closed
    assert cache.get(now=t0 + 151) is None, "超窗必须 fail closed"
    # 恢复后重新可用
    state["available"] = True
    assert cache.get(now=t0 + 200) is good
    print("[OK] JwksCache：新鲜命中、stale 续用、超窗 fail closed、恢复自愈")


def main() -> None:
    test_valid_and_rejected_tokens()
    test_jwks_cache_fail_closed()
    print("\n=== L12 网关鉴权（fake）：2 组全部通过 ===")


if __name__ == "__main__":
    main()
