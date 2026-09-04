"""菟菚自制表情包：识别情绪场景，低频复用或生成一张角色贴纸。"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from .config import config
from .log import logger


@dataclass(frozen=True)
class StickerScene:
    key: str
    emotion: str
    description: str
    pose: str


_SCENES = {
    "comfort": StickerScene("comfort", "安慰", "安静递纸巾的安慰贴纸", "神情认真又有点嘴硬，伸手递出一张纸巾"),
    "annoyed": StickerScene("annoyed", "无语", "抱臂无语的吐槽贴纸", "抱着手臂，半眯眼，一脸无语但不凶狠"),
    "celebrate": StickerScene("celebrate", "开心", "克制庆祝的开心贴纸", "眼睛发亮，小幅握拳庆祝，笑得得意"),
    "shy": StickerScene("shy", "害羞", "别过脸害羞的贴纸", "微微脸红，别过脸推眼镜，嘴角藏不住笑"),
    "research": StickerScene("research", "研究", "研究员灵光一闪贴纸", "拿着记录板，镜片一闪，像刚想到解决办法"),
    "sulky": StickerScene("sulky", "低落", "趴桌闷闷不乐的贴纸", "趴在实验桌边，神情低落但仍保持倔强"),
}

_KEYWORDS = (
    ("comfort", ("难过", "伤心", "想哭", "崩溃", "失败了", "好累", "撑不住", "安慰")),
    ("annoyed", ("无语", "离谱", "烦死", "生气", "气死", "滚", "蠢", "傻")),
    ("celebrate", ("恭喜", "成功", "搞定", "赢了", "通过了", "过了", "哈哈", "笑死", "开心")),
    ("shy", ("喜欢你", "想你", "可爱", "害羞", "心动")),
    ("research", ("论文", "实验", "研究", "代码", "bug", "报错", "调试", "算法")),
)


def infer_sticker_scene(text: str, reply: str, mood: int) -> StickerScene | None:
    """用当前对话与心情挑一个场景；无明显情绪时不硬塞贴纸。"""
    sample = f"{text}\n{reply}".lower()
    for key, words in _KEYWORDS:
        if any(word in sample for word in words):
            return _SCENES[key]
    if mood <= 25:
        return _SCENES["sulky"]
    if mood >= 88:
        return _SCENES["celebrate"]
    return None


def should_attach_sticker(
    *, scene: StickerScene | None, stage: str, current_message_id: int,
    last_message_id: int, min_gap: int, chance_percent: int, roll: int,
    explicit_image: bool = False,
) -> bool:
    """纯策略判断，便于稳定测试概率、关系分寸和频率约束。"""
    if scene is None or explicit_image or chance_percent <= 0:
        return False
    if current_message_id - last_message_id < min_gap:
        return False
    if scene.key == "shy" and stage not in ("亲密", "恋人"):
        return False
    stage_factor = {"初识": 0.35, "熟悉": 0.65, "亲密": 1.0, "恋人": 1.15}.get(stage, 0.5)
    threshold = min(100, round(chance_percent * stage_factor))
    return 1 <= roll <= threshold


def build_sticker_prompt(scene: StickerScene) -> str:
    """固定角色视觉锚点，避免收藏逐渐画成不同的人。"""
    return (
        "菟菚本人，一位成年女性研究员，绿色长发，圆框眼镜，白色实验风外套，"
        "绿色领带，头肩比例的Q版聊天表情贴纸，"
        f"{scene.pose}，单人，正方形构图，纯净浅色背景，粗细适中的白色贴纸描边，"
        "表情清晰，适合聊天软件使用，不要文字，不要对话框，不要水印，不要额外人物"
    )


class StickerStore(Protocol):
    def current_message_id(self, user_id: str) -> int: ...
    def last_sent_message_id(self, user_id: str) -> int: ...
    def find(self, user_id: str, emotion: str) -> list[dict]: ...
    def collection_size(self, user_id: str, limit: int) -> int: ...
    def save(self, user_id: str, path: str, scene: StickerScene) -> int: ...
    def mark_used(self, sticker_id: int) -> None: ...
    def mark_sent(self, user_id: str, message_id: int) -> None: ...


class UserDbStickerStore:
    _LAST_KEY = "sticker:last_message_id"

    def current_message_id(self, user_id: str) -> int:
        from .userdb import db
        return db.max_message_id(user_id)

    def last_sent_message_id(self, user_id: str) -> int:
        from .userdb import kv_get
        try:
            return int(kv_get(user_id, self._LAST_KEY) or -1_000_000)
        except (TypeError, ValueError):
            return -1_000_000

    def find(self, user_id: str, emotion: str) -> list[dict]:
        from .userdb import get_sticker_by_emotion
        return get_sticker_by_emotion(user_id, emotion, limit=5)

    def collection_size(self, user_id: str, limit: int) -> int:
        from .userdb import get_stickers
        return len(get_stickers(user_id, limit=limit + 1))

    def save(self, user_id: str, path: str, scene: StickerScene) -> int:
        from .userdb import save_sticker
        url = f"/api/images/{Path(path).name}"
        return save_sticker(user_id, path, url, scene.description, scene.emotion)

    def mark_used(self, sticker_id: int) -> None:
        from .userdb import mark_sticker_used
        mark_sticker_used(sticker_id)

    def mark_sent(self, user_id: str, message_id: int) -> None:
        from .userdb import kv_set
        kv_set(user_id, self._LAST_KEY, str(message_id))


async def maybe_attach_sticker(
    user_id: str,
    text: str,
    reply: str,
    *,
    stage: str,
    mood: int,
    image_cb: Callable[[str], Awaitable[None]],
    explicit_image: bool = False,
    roll: int | None = None,
    store: StickerStore | None = None,
    generator: Callable[[str], Awaitable[str | None]] | None = None,
    enabled: bool | None = None,
    chance_percent: int | None = None,
    min_gap: int | None = None,
    collection_max: int | None = None,
) -> str | None:
    """满足克制策略时附一张贴纸；失败静默退化成纯文字。"""
    if enabled is None:
        enabled = config.sticker_enabled
    if not enabled:
        return None
    scene = infer_sticker_scene(text, reply, mood)
    repo = store or UserDbStickerStore()
    current_id = repo.current_message_id(user_id)
    if not should_attach_sticker(
        scene=scene,
        stage=stage,
        current_message_id=current_id,
        last_message_id=repo.last_sent_message_id(user_id),
        min_gap=min_gap if min_gap is not None else config.sticker_min_message_gap,
        chance_percent=chance_percent if chance_percent is not None else config.sticker_chance_percent,
        roll=roll if roll is not None else random.randint(1, 100),
        explicit_image=explicit_image,
    ):
        return None
    assert scene is not None
    try:
        for item in repo.find(user_id, scene.emotion):
            path = str(item.get("file") or "")
            if path and Path(path).is_file():
                await image_cb(path)
                repo.mark_used(int(item.get("id") or 0))
                repo.mark_sent(user_id, current_id)
                return path

        cap = collection_max if collection_max is not None else config.sticker_collection_max
        if repo.collection_size(user_id, cap) >= cap:
            return None
        if generator is None:
            from .imagegen import generate
            generator = generate
        path = await generator(build_sticker_prompt(scene))
        if not path:
            return None
        sticker_id = repo.save(user_id, path, scene)
        await image_cb(path)
        repo.mark_sent(user_id, current_id)
        logger.info("[贴纸] 新收藏 #{}：{}", sticker_id, scene.emotion)
        return path
    except Exception:
        logger.exception("[贴纸] 选择或生成失败，退化为纯文字")
        return None
