"""Persona profile library and hot-switch state.

Each imported Markdown persona is stored under ``data/personas/<id>/`` together
with its UI/runtime settings.  Conversation and memory data stay in their
existing databases, but are isolated by a stable persona-scoped user id.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from pathlib import Path

from .config import config

DEFAULT_PERSONA_ID = "default"
DEFAULT_USER_ID = "assistant-main"
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
_MAX_CARD_BYTES = 1024 * 1024
_ROOT = config.data_dir / "personas"
_STATE = _ROOT / "active.json"
_lock = threading.RLock()

# active_id() 进程内缓存：chat 主链路每轮会经 active_user_id / scoped_user_id /
# session_storage_id / persona_name_for_user_id 多次调用 active_id()，每次都
# ensure_library（多次 stat）+ 读盘解析。用 _STATE 的 mtime 做失效——人格切换
# （activate/update_profile 写 active.json）会让 mtime 变化，缓存随之自动刷新。
# 无需显式清缓存，也避免缓存陈旧。
_cached_active_id: str | None = None
_cached_state_mtime = 0.0


class PersonaProfileError(ValueError):
    """A user-facing persona profile validation error."""


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _decode_card(data: bytes) -> str:
    if not data:
        raise PersonaProfileError("人格卡是空的")
    if len(data) > _MAX_CARD_BYTES:
        raise PersonaProfileError("人格卡不能超过 1MB")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding).strip()
            break
        except UnicodeDecodeError:
            continue
    else:
        raise PersonaProfileError("人格卡编码无法识别（支持 UTF-8 / GB18030）")
    if not text:
        raise PersonaProfileError("人格卡没有可读内容")
    return text + "\n"


def _name_from_card(text: str, filename: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^\s*#{1,3}\s+(.+?)\s*$", line)
        if match:
            value = match.group(1).strip().strip("#").strip()
            value = re.sub(r"^(?:人格卡|角色卡)\s*[·:：-]?\s*", "", value)
            value = re.sub(r"[（(][^）)]*[）)]\s*$", "", value).strip()
            value = re.sub(r"(?:人格|角色)?设定$", "", value).strip()
            if value:
                return value[:40]
    stem = Path(filename).stem
    stem = re.sub(r"^(?:persona|人格卡|角色卡)[-_\s]*", "", stem, flags=re.I).strip()
    return (stem or "未命名人格")[:40]


def _metadata_from_card(text: str) -> dict[str, str]:
    """Read a small optional Markdown front matter block without a YAML dependency."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, str] = {}
    for line in lines[1:30]:
        if line.strip() == "---":
            break
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$", line)
        if match:
            result[match.group(1).lower()] = match.group(2).strip().strip("\"'")
    return result


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", normalized, flags=re.UNICODE).strip("-_")
    return slug[:48] or f"persona-{int(time.time())}"


def _settings_path(profile_id: str) -> Path:
    return _ROOT / profile_id / "settings.json"


def _card_path(profile_id: str) -> Path:
    return _ROOT / profile_id / "persona.md"


def _validate_id(profile_id: str) -> str:
    if not re.fullmatch(r"[\w\u4e00-\u9fff-]{1,64}", profile_id or "", flags=re.UNICODE):
        raise PersonaProfileError("人格标识无效")
    return profile_id


def _default_settings(profile_id: str, name: str) -> dict:
    return {
        "id": profile_id,
        "name": name,
        "subtitle": "独立人格档案",
        "theme": "dark",
        "voice": DEFAULT_VOICE,
        "created_at": int(time.time()),
        "data_key": scoped_user_id(DEFAULT_USER_ID, profile_id),
    }


def ensure_library() -> None:
    """Create the library and migrate the repository's original card once."""
    with _lock:
        _ROOT.mkdir(parents=True, exist_ok=True)
        default_card = _card_path(DEFAULT_PERSONA_ID)
        if not default_card.exists():
            source = config.persona_file
            if not source.exists():
                raise FileNotFoundError(f"人格文件不存在: {source}")
            text = source.read_text(encoding="utf-8")
            default_card.parent.mkdir(parents=True, exist_ok=True)
            default_card.write_text(text, encoding="utf-8")
        default_settings = _settings_path(DEFAULT_PERSONA_ID)
        if not default_settings.exists():
            text = default_card.read_text(encoding="utf-8")
            _write_json(
                default_settings,
                _default_settings(DEFAULT_PERSONA_ID, _name_from_card(text, config.persona_file.name)),
            )
        if not _STATE.exists():
            _write_json(_STATE, {"active_id": DEFAULT_PERSONA_ID})


def active_id() -> str:
    """当前激活人格 id（带 mtime 进程内缓存，避免主链路重复读盘）。

    缓存键是 _STATE（active.json）的 mtime：activate/update_profile 写该文件时
    mtime 变化，缓存自动失效，无需显式清理也不陈旧。ensure_library 只在缓存
    失效分支执行一次，缓存命中时不触发（这是相对旧实现的主要 IO 削减点）。
    """
    global _cached_active_id, _cached_state_mtime
    with _lock:
        # 缓存命中（含 default 无状态文件场景：mtime 恒 -1 且已缓存）→ 直接返回
        if _cached_active_id is not None:
            try:
                cur = _STATE.stat().st_mtime
            except OSError:
                cur = -1.0
            if cur == _cached_state_mtime:
                return _cached_active_id
        # 缓存失效或首调：确保库就绪后重算（写入会更新 mtime，此处重新 stat）
        ensure_library()
        try:
            mtime = _STATE.stat().st_mtime
        except OSError:
            mtime = -1.0
        try:
            value = json.loads(_STATE.read_text(encoding="utf-8")).get("active_id", DEFAULT_PERSONA_ID)
        except (OSError, json.JSONDecodeError, TypeError):
            value = DEFAULT_PERSONA_ID
        value = str(value)
        if not re.fullmatch(r"[\w\u4e00-\u9fff-]{1,64}", value, flags=re.UNICODE):
            value = DEFAULT_PERSONA_ID
        elif not _settings_path(value).exists():
            value = DEFAULT_PERSONA_ID
        _cached_active_id = value
        _cached_state_mtime = mtime
        return value


def scoped_user_id(base: str = DEFAULT_USER_ID, profile_id: str | None = None) -> str:
    """Return the stable data namespace for a persona; preserve legacy default data."""
    pid = profile_id or active_id()
    return base if pid == DEFAULT_PERSONA_ID else f"{base}::persona::{pid}"


def active_user_id(base: str = DEFAULT_USER_ID) -> str:
    return scoped_user_id(base, active_id())


def session_storage_id(public_id: str) -> str:
    """Map the public single-session id to the active persona's private row id."""
    if public_id != "current":
        return public_id
    pid = active_id()
    return public_id if pid == DEFAULT_PERSONA_ID else f"current::persona::{pid}"


def load_profile(profile_id: str) -> dict:
    ensure_library()
    _validate_id(profile_id)
    path = _settings_path(profile_id)
    if not path.exists():
        raise PersonaProfileError("人格不存在")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PersonaProfileError("人格设置已损坏") from exc
    value["active"] = profile_id == active_id()
    return value


def active_profile() -> dict:
    return load_profile(active_id())


def active_card_path() -> Path:
    return _card_path(active_id())


def list_profiles() -> list[dict]:
    ensure_library()
    profiles: list[dict] = []
    for folder in _ROOT.iterdir():
        if not folder.is_dir() or not (folder / "settings.json").exists() or not (folder / "persona.md").exists():
            continue
        try:
            profiles.append(load_profile(folder.name))
        except PersonaProfileError:
            continue
    return sorted(profiles, key=lambda p: (not p.get("active", False), p.get("created_at", 0)))


def import_card(filename: str, data: bytes) -> dict:
    if Path(filename).suffix.lower() != ".md":
        raise PersonaProfileError("请加载与原人格卡一致的 .md 文件")
    text = _decode_card(data)
    metadata = _metadata_from_card(text)
    name = (metadata.get("name") or _name_from_card(text, filename))[:40]
    base_id = _slug(name)
    with _lock:
        ensure_library()
        profile_id = base_id
        index = 2
        while (_ROOT / profile_id).exists():
            profile_id = f"{base_id}-{index}"
            index += 1
        folder = _ROOT / profile_id
        folder.mkdir(parents=True)
        (folder / "persona.md").write_text(text, encoding="utf-8")
        settings = _default_settings(profile_id, name)
        if metadata.get("subtitle"):
            settings["subtitle"] = metadata["subtitle"][:80]
        if metadata.get("theme") in {"dark", "light"}:
            settings["theme"] = metadata["theme"]
        if metadata.get("voice"):
            settings["voice"] = metadata["voice"][:120]
        _write_json(folder / "settings.json", settings)
    return load_profile(profile_id)


def update_profile(profile_id: str, updates: dict) -> dict:
    allowed = {"name", "subtitle", "theme", "voice"}
    with _lock:
        profile = load_profile(profile_id)
        for key in allowed:
            if key not in updates:
                continue
            value = str(updates[key]).strip()
            if key == "name" and not value:
                raise PersonaProfileError("人格名称不能为空")
            if key == "theme" and value not in {"dark", "light"}:
                raise PersonaProfileError("主题只能是 dark 或 light")
            if len(value) > (80 if key != "voice" else 120):
                raise PersonaProfileError(f"{key} 过长")
            profile[key] = value
        profile.pop("active", None)
        _write_json(_settings_path(profile_id), profile)
    return load_profile(profile_id)


def activate(profile_id: str) -> dict:
    _validate_id(profile_id)
    with _lock:
        profile = load_profile(profile_id)
        _write_json(_STATE, {"active_id": profile_id})
    profile["active"] = True
    return profile


def active_voice() -> str:
    return str(active_profile().get("voice") or DEFAULT_VOICE)


def active_name() -> str:
    return str(active_profile().get("name") or "助手")


def profile_id_from_user_id(user_id: str) -> str:
    marker = "::persona::"
    return user_id.split(marker, 1)[1] if marker in user_id else DEFAULT_PERSONA_ID


def persona_name_for_user_id(user_id: str) -> str:
    try:
        return str(load_profile(profile_id_from_user_id(user_id)).get("name") or "助手")
    except PersonaProfileError:
        return "助手"
