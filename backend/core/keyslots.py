# -*- coding: utf-8 -*-
"""P3-04 D 片：主密钥层级——本机 DPAPI 槽 + Argon2id 恢复口令槽。

设计（docs/Zcode技术指导.md §21.1，用户已拍板「本机便捷解锁 + 独立恢复口令」）：

- 首次初始化生成随机 256-bit master key（MK）；MK 只经进程内存或当前用户
  ACL 的路径传递，不写命令行、环境变量或日志。
- 本机槽：Windows DPAPI CurrentUser 加密 MK，落盘 ``keyslots/local.dpapi``。
- 恢复槽：用户恢复口令经 Argon2id（每槽随机 16-byte salt，参数记录版本）
  派生 KEK，AES-256-GCM 包装 MK；落盘 salt/参数/nonce/ciphertext，
  **不保存口令或 KEK**。nonce 每次随机；错误口令统一走 GCM 验签失败，
  不区分内部原因。
- 口令创建时要求二次输入确认；恢复槽写入后立即用实际解包验证，验证不过
  就删除该槽（不留「看起来存在其实解不开」的槽）。
- 轮换恢复口令 = 用旧口令（或本机槽）解出 MK，再以新口令重建恢复槽；
  MK 不变，已加密数据不受影响。

边界（诚实声明）：不承诺抵御已登录且能控制同一 Windows 用户的恶意进程
（DPAPI CurrentUser 对同用户进程可解）；Python 字节串只能尽力缩短生命周期，
不宣称绝对内存清零。

本模块是纯库：不碰真实 ``data/``，一切以调用方传入的目录为准（测试用临时
目录；E 片迁移才把 keyslots 接进真实数据目录）。
"""
from __future__ import annotations

import base64
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:  # Argon2id：cryptography >= 42 提供；缺版本时导入失败由需求锁保证
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
except ImportError as exc:  # pragma: no cover - 依赖缺失在启动即暴露
    raise ImportError("cryptography>=42 需要提供 Argon2id（检查 requirements.txt 锁版本）") from exc

# DPAPI 在非 Windows 上不可用；keyslots 模块保持可导入（测试可跳过 DPAPI 用例），
# 只有真正调用本机槽时才要求 win32crypt。
try:
    import win32crypt  # type: ignore[import-not-found]
except ImportError:  # 非 Windows / 未装 pywin32
    win32crypt = None

MK_BYTES = 32                 # 256-bit master key
KEK_BYTES = 32                # 256-bit key-encryption key（Argon2id 输出）
SALT_BYTES = 16               # 恢复槽随机 salt
NONCE_BYTES = 12              # AES-GCM nonce
LOCAL_SLOT = "local.dpapi"
RECOVERY_SLOT = "recovery.json"
RECOVERY_SLOT_VERSION = 1

# Argon2id 参数：目标单次派生约 500ms（§21.1「参数按目标机器实测约500ms且记录版本」）。
# 在开发机（Python 3.12 / cryptography 46）实测标定：t=3,m=65536,p=4 ≈ 90-140ms，
# t=4,m=131072,p=4 ≈ 250-320ms——不同机器差异大，落盘时记录实际参数，验证按记录执行。
_ARGON2_TIME_COST = 4
_ARGON2_MEMORY_KIB = 131072
_ARGON2_PARALLELISM = 4
_ARGON2_MIN_TIME, _ARGON2_MIN_MEMORY, _ARGON2_MIN_PAR = 1, 8192, 1
_ARGON2_MAX_TIME, _ARGON2_MAX_MEMORY, _ARGON2_MAX_PAR = 16, 1048576, 8


class KeySlotError(ValueError):
    """密钥槽不存在、格式非法或解包失败（错误口令同此，不区分内部原因）。"""


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


@dataclass(frozen=True)
class KeySlots:
    """初始化后的密钥槽集合视图（不含任何密钥材料本体）。"""

    root: Path
    local_slot: bool
    recovery_slot: bool

    @property
    def any_slot(self) -> bool:
        return self.local_slot or self.recovery_slot


def generate_master_key() -> bytes:
    """生成随机 256-bit MK（os.urandom）。"""
    return os.urandom(MK_BYTES)


def master_key_id(master_key: bytes) -> str:
    """MK 的短标识（key_id）：非保密，供容器 header 与备份 manifest 关联。"""
    import hashlib

    return hashlib.sha256(master_key).hexdigest()[:16]


def _slot_root(root: Path) -> Path:
    root = Path(root)
    if not root.exists():
        raise KeySlotError(f"keyslots 目录不存在：{root}")
    return root


def write_local_slot(root: Path, master_key: bytes) -> Path:
    """用 DPAPI CurrentUser 加密 MK 写本机槽；返回槽文件路径。

    DPAPI 不可用（非 Windows / 缺 pywin32）时明确报错，不降级成明文。
    """
    if win32crypt is None:
        raise KeySlotError("本机槽需要 Windows DPAPI（pywin32）；当前环境不可用")
    if not isinstance(master_key, bytes) or len(master_key) != MK_BYTES:
        raise KeySlotError(f"master key 必须为 {MK_BYTES} 字节")
    directory = _slot_root(root)
    # CRYPTPROTECT_UI_FORBIDDEN：服务上下文禁止弹 UI
    blob = win32crypt.CryptProtectData(
        master_key, "tztuzhan-mk", None, None, None, 0x01
    )
    target = directory / LOCAL_SLOT
    tmp = directory / f".{LOCAL_SLOT}.tmp"
    tmp.write_bytes(blob)
    os.replace(tmp, target)  # 原子替换，避免半截槽
    return target


def read_local_slot(root: Path) -> bytes:
    """从本机槽解出 MK。失败（无槽/DPAPI 拒绝/长度异常）抛 KeySlotError。"""
    if win32crypt is None:
        raise KeySlotError("本机槽需要 Windows DPAPI（pywin32）；当前环境不可用")
    target = _slot_root(root) / LOCAL_SLOT
    if not target.is_file():
        raise KeySlotError("本机槽不存在")
    blob = target.read_bytes()
    try:
        # pywin32：CryptProtectData 返回 bytes；CryptUnprotectData 返回 (desc, bytes)
        plain = win32crypt.CryptUnprotectData(blob, None, None, None, 0x01)[1]
    except Exception as exc:
        raise KeySlotError("本机槽解包失败（不是当前用户或数据损坏）") from exc
    if not isinstance(plain, bytes) or len(plain) != MK_BYTES:
        raise KeySlotError("本机槽内容长度异常")
    return plain


def _derive_recovery_kek(
    passphrase: str, salt: bytes, *, time_cost: int, memory_kib: int, parallelism: int
) -> bytes:
    if not passphrase or not passphrase.encode("utf-8"):
        raise KeySlotError("恢复口令不能为空")
    kdf = Argon2id(
        salt=salt,
        length=KEK_BYTES,
        iterations=time_cost,
        lanes=parallelism,
        memory_cost=memory_kib,  # cryptography 以 KiB 计
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _argon2_params(time_cost: int, memory_kib: int, parallelism: int) -> None:
    if not (_ARGON2_MIN_TIME <= time_cost <= _ARGON2_MAX_TIME):
        raise KeySlotError(f"argon2 time_cost 越界：{time_cost}")
    if not (_ARGON2_MIN_MEMORY <= memory_kib <= _ARGON2_MAX_MEMORY):
        raise KeySlotError(f"argon2 memory 越界：{memory_kib}")
    if not (_ARGON2_MIN_PAR <= parallelism <= _ARGON2_MAX_PAR):
        raise KeySlotError(f"argon2 parallelism 越界：{parallelism}")


def write_recovery_slot(
    root: Path,
    master_key: bytes,
    passphrase: str,
    passphrase_repeat: str,
    *,
    time_cost: int = _ARGON2_TIME_COST,
    memory_kib: int = _ARGON2_MEMORY_KIB,
    parallelism: int = _ARGON2_PARALLELISM,
) -> Path:
    """以恢复口令包装 MK 写恢复槽；写入后立即实际解包自检。

    - 口令与确认必须一致（二次输入，§21.1）；
    - nonce 随机；salt 每次随机；
    - 自检失败删除槽文件，不留坏槽。
    """
    if passphrase != passphrase_repeat:
        raise KeySlotError("两次输入的恢复口令不一致")
    if not isinstance(master_key, bytes) or len(master_key) != MK_BYTES:
        raise KeySlotError(f"master key 必须为 {MK_BYTES} 字节")
    _argon2_params(time_cost, memory_kib, parallelism)
    directory = _slot_root(root)
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    kek = _derive_recovery_kek(passphrase, salt, time_cost=time_cost,
                               memory_kib=memory_kib, parallelism=parallelism)
    aad = struct.pack(">I", RECOVERY_SLOT_VERSION) + b"tztuzhan:recovery-slot:v1"
    ciphertext = AESGCM(kek).encrypt(nonce, master_key, aad)
    payload = {
        "slot_version": RECOVERY_SLOT_VERSION,
        "kdf": {
            "name": "argon2id",
            "time_cost": time_cost,
            "memory_kib": memory_kib,
            "parallelism": parallelism,
        },
        "key_id": master_key_id(master_key),
        "salt": _b64e(salt),
        "nonce": _b64e(nonce),
        "ciphertext": _b64e(ciphertext),
    }
    target = directory / RECOVERY_SLOT
    tmp = directory / f".{RECOVERY_SLOT}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)
    try:
        # 写入后实际解包自检：解不开的槽不如没有槽
        recovered = read_recovery_slot(root, passphrase)
        if recovered != master_key:
            raise KeySlotError("恢复槽自检不一致")
    except Exception:
        try:
            target.unlink()
        finally:
            raise KeySlotError("恢复槽自检失败，已删除（请重试）")
    return target


def read_recovery_slot(root: Path, passphrase: str) -> bytes:
    """用恢复口令解出 MK。错误口令/损坏/参数越界统一 KeySlotError，不区分原因。"""
    target = _slot_root(root) / RECOVERY_SLOT
    if not target.is_file():
        raise KeySlotError("恢复槽不存在")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KeySlotError("恢复槽损坏") from exc
    if not isinstance(payload, dict) or payload.get("slot_version") != RECOVERY_SLOT_VERSION:
        raise KeySlotError("恢复槽版本不支持")
    kdf = payload.get("kdf") or {}
    if kdf.get("name") != "argon2id":
        raise KeySlotError("恢复槽 KDF 不支持")
    try:
        salt = _b64d(payload["salt"])
        nonce = _b64d(payload["nonce"])
        ciphertext = _b64d(payload["ciphertext"])
        time_cost = int(kdf["time_cost"])
        memory_kib = int(kdf["memory_kib"])
        parallelism = int(kdf["parallelism"])
    except (KeyError, TypeError, ValueError) as exc:
        raise KeySlotError("恢复槽字段非法") from exc
    _argon2_params(time_cost, memory_kib, parallelism)
    if len(salt) != SALT_BYTES or len(nonce) != NONCE_BYTES:
        raise KeySlotError("恢复槽字段长度异常")
    kek = _derive_recovery_kek(passphrase, salt, time_cost=time_cost,
                               memory_kib=memory_kib, parallelism=parallelism)
    aad = struct.pack(">I", RECOVERY_SLOT_VERSION) + b"tztuzhan:recovery-slot:v1"
    try:
        plain = AESGCM(kek).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise KeySlotError("恢复口令不正确或数据已损坏") from exc
    if len(plain) != MK_BYTES:
        raise KeySlotError("恢复槽解出的主密钥长度异常")
    return plain


def recovery_slot_key_id(root: Path) -> str | None:
    """读恢复槽的 key_id（非保密元数据，供 UI 显示「密钥已绑定」而不解密）。"""
    target = Path(root) / RECOVERY_SLOT
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return str(payload["key_id"])
    except (OSError, ValueError, KeyError):
        return None


def rotate_recovery_passphrase(
    root: Path, new_passphrase: str, new_repeat: str, *, current_mk: bytes
) -> Path:
    """轮换恢复口令：MK 不变（已加密数据不受影响），只重建恢复槽。"""
    return write_recovery_slot(root, current_mk, new_passphrase, new_repeat)


def describe_slots(root: Path) -> KeySlots:
    """列出槽的存在性（不含密钥材料）。"""
    directory = Path(root)
    if not directory.exists():
        raise KeySlotError(f"keyslots 目录不存在：{directory}")
    return KeySlots(
        root=directory,
        local_slot=(directory / LOCAL_SLOT).is_file(),
        recovery_slot=(directory / RECOVERY_SLOT).is_file(),
    )


def initialize(root: Path, *, passphrase: str | None, passphrase_repeat: str | None) -> bytes:
    """首次初始化：生成 MK 并按可用材料写槽。

    - 只传 passphrase（与确认）→ 同时写本机槽（Windows）与恢复槽；
    - Windows DPAPI 不可用时仍可只建恢复槽（跨平台测试路径）；
    - 已存在任意槽时拒绝重复初始化（先 rotate/迁移，不能静默换 MK）。
    返回 MK（仅初始化这一次交给调用方，之后经 key broker 持有）。
    """
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    slots = describe_slots(directory)
    if slots.any_slot:
        raise KeySlotError("已存在密钥槽；初始化会换掉主密钥，请走显式迁移/轮换")
    if passphrase is not None and passphrase_repeat is None:
        raise KeySlotError("创建恢复口令需要二次确认输入")
    master_key = generate_master_key()
    if passphrase is not None:
        write_recovery_slot(directory, master_key, passphrase, passphrase_repeat or "")
    if win32crypt is not None:
        write_local_slot(directory, master_key)
    return master_key
