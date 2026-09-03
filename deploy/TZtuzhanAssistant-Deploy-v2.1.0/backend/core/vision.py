# -*- coding: utf-8 -*-
"""图片理解：用视觉模型（OpenAI 兼容的多模态端点）描述图片内容。

- describe_bytes(image_bytes, filename) → str | None
  把图片转 base64 data URL 发给视觉模型，返回图片内容描述；失败返回 None。
- 配置：VISION_BASE_URL / VISION_API_KEY / VISION_MODEL；
  未配置时按顺序回落到 IMAGE_*（SiliconFlow 生图 key + 真实可用的 VL 模型），
  最后才是 LLM_*（部分端点本身支持视觉时才可用）。
"""
from __future__ import annotations

import base64

from .config import config
from .log import logger

# 回落 SiliconFlow 时的默认视觉模型（真实存在，替代 DeepSeek 端点不存在的
# deepseek-v4-flash-vision-exp，避免识图 403 Model disabled）
_DEFAULT_VL_MODEL = "Qwen/Qwen2.5-VL-72B-Instruct"


def enabled() -> bool:
    return bool(config.vision_api_key or config.image_api_key or config.llm_api_key)


def _vision_conf() -> tuple[str, str, str]:
    """返回 (base_url, api_key, model)。

    优先级：
    1. VISION_*（用户显式配置的视觉端点）
    2. IMAGE_*（SiliconFlow 生图 key，配默认 VL 模型）——最常见的可用组合
    3. LLM_*（最后兜底，仅当该端点支持视觉）
    """
    if config.vision_api_key and config.vision_base_url:
        return (
            config.vision_base_url,
            config.vision_api_key,
            config.vision_model or _DEFAULT_VL_MODEL,
        )
    if config.image_api_key and config.image_base_url:
        return (
            config.image_base_url,
            config.image_api_key,
            config.vision_model or _DEFAULT_VL_MODEL,
        )
    return (
        config.llm_base_url,
        config.llm_api_key,
        config.vision_model or _DEFAULT_VL_MODEL,
    )


async def describe_bytes(image_bytes: bytes, filename: str = "image.png") -> str | None:
    """描述一张图片的内容。"""
    if not image_bytes:
        return None
    base, key, model = _vision_conf()
    if not key:
        logger.warning("[识图] 未配置视觉模型 key（VISION_* 或 IMAGE_*）")
        return None
    if len(image_bytes) > 8 * 1024 * 1024:
        logger.warning("[识图] 图片过大（>8MB），拒绝")
        return None

    # 推断 mime
    mime = "image/png"
    low = filename.lower()
    if low.endswith((".jpg", ".jpeg")):
        mime = "image/jpeg"
    elif low.endswith(".gif"):
        mime = "image/gif"
    elif low.endswith(".webp"):
        mime = "image/webp"

    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "你是「菟菚」，一个说话干脆利落、带点腹黑毒舌的女孩子。"
                        "现在有人发来一张图片，你要用自己的语气把看到的东西说出来。"
                        "要求：\n"
                        "1. 先讲清楚图片里的主体、场景、明显的文字和情绪（信息别丢）。\n"
                        "2. 语气像你在随口跟人聊天，带点你的毒舌或调侃，不要写成干巴巴的说明书。\n"
                        "3. 用中文，3~6 句话，别太长，直接说，不要思考过程、不要括号旁白。"
                    ),
                },
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=base, api_key=key, timeout=60, max_retries=1)
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=1000,  # 给 reasoning 留足空间
        )
        text = (resp.choices[0].message.content or "").strip()
        # 若 content 仍为空，尝试用 reasoning_content 兜底
        if not text:
            rc = getattr(resp.choices[0].message, "reasoning_content", None)
            if rc:
                text = rc.strip()
        return text[:600] or None
    except Exception as e:
        logger.warning(f"[识图] 视觉模型调用失败: {type(e).__name__}: {e}")
        return None


async def describe_file(path: str) -> str | None:
    """描述本地图片文件。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        logger.warning(f"[识图] 读文件失败 {path}: {e}")
        return None
    return await describe_bytes(data, filename=path.split("/")[-1].split("\\")[-1])