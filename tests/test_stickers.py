# -*- coding: utf-8 -*-
"""B2 自制表情包：场景、克制策略、收藏复用与角色锚点。"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.stickers import (
    build_sticker_prompt,
    infer_sticker_scene,
    maybe_attach_sticker,
    should_attach_sticker,
)


class FakeStore:
    def __init__(self, existing: list[dict] | None = None, size: int = 0):
        self.existing = existing or []
        self.size = size
        self.used: list[int] = []
        self.saved: list[tuple[str, str]] = []
        self.sent: list[int] = []

    def current_message_id(self, user_id: str) -> int: return 30
    def last_sent_message_id(self, user_id: str) -> int: return 10
    def find(self, user_id: str, emotion: str) -> list[dict]: return self.existing
    def collection_size(self, user_id: str, limit: int) -> int: return self.size
    def save(self, user_id: str, path: str, scene) -> int:
        self.saved.append((path, scene.emotion))
        return 7
    def mark_used(self, sticker_id: int) -> None: self.used.append(sticker_id)
    def mark_sent(self, user_id: str, message_id: int) -> None: self.sent.append(message_id)


def test_scene_and_policy() -> None:
    comfort = infer_sticker_scene("今天好累，快撑不住了", "先喘口气", 60)
    assert comfort and comfort.key == "comfort"
    assert infer_sticker_scene("普通问题", "普通回答", 60) is None
    assert infer_sticker_scene("随便聊聊", "行", 20).key == "sulky"
    assert not should_attach_sticker(
        scene=comfort, stage="恋人", current_message_id=15, last_message_id=10,
        min_gap=10, chance_percent=100, roll=1,
    )
    shy = infer_sticker_scene("我喜欢你", "知道了", 70)
    assert not should_attach_sticker(
        scene=shy, stage="初识", current_message_id=30, last_message_id=10,
        min_gap=10, chance_percent=100, roll=1,
    )
    assert should_attach_sticker(
        scene=comfort, stage="亲密", current_message_id=30, last_message_id=10,
        min_gap=10, chance_percent=100, roll=1,
    )
    assert not should_attach_sticker(
        scene=comfort, stage="亲密", current_message_id=30, last_message_id=10,
        min_gap=10, chance_percent=100, roll=1, explicit_image=True,
    )


def test_visual_anchor() -> None:
    scene = infer_sticker_scene("代码终于搞定了", "还算没白熬", 80)
    prompt = build_sticker_prompt(scene)
    for anchor in ("成年女性研究员", "绿色长发", "圆框眼镜", "白色实验风外套", "绿色领带"):
        assert anchor in prompt
    assert "不要文字" in prompt and "不要额外人物" in prompt


async def test_reuse_and_generate() -> None:
    delivered: list[str] = []
    tmp_root = ROOT / ".tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sticker-test-", dir=tmp_root) as tmp:
        existing = Path(tmp) / "existing.png"
        existing.write_bytes(b"png")
        store = FakeStore(existing=[{"id": 3, "file": str(existing)}])

        async def deliver(path: str) -> None: delivered.append(path)
        async def should_not_generate(prompt: str) -> str | None:
            raise AssertionError("有匹配收藏时不应重新生图")

        result = await maybe_attach_sticker(
            "u", "代码终于搞定了", "总算修好了", stage="亲密", mood=75,
            image_cb=deliver, roll=1, store=store, generator=should_not_generate,
            enabled=True, chance_percent=100, min_gap=10, collection_max=24,
        )
        assert result == str(existing)
        assert delivered == [str(existing)] and store.used == [3] and store.sent == [30]

        generated = Path(tmp) / "new.png"
        async def generate(prompt: str) -> str | None:
            assert "菟菚本人" in prompt
            generated.write_bytes(b"png")
            return str(generated)

        fresh = FakeStore()
        result = await maybe_attach_sticker(
            "u", "代码终于搞定了", "总算修好了", stage="亲密", mood=75,
            image_cb=deliver, roll=1, store=fresh, generator=generate,
            enabled=True, chance_percent=100, min_gap=10, collection_max=24,
        )
        assert result == str(generated)
        assert fresh.saved == [(str(generated), "开心")]
        assert fresh.sent == [30]


async def main() -> None:
    test_scene_and_policy()
    test_visual_anchor()
    await test_reuse_and_generate()
    print("[OK] B2 贴纸场景、频率、收藏复用与视觉锚点")


if __name__ == "__main__":
    asyncio.run(main())
