# -*- coding: utf-8 -*-
"""M8 阶段封存与告别：选定范围导出纪念包 + 一封基于真实数据的告别信。

设计边界（TECH-PLAN M8，用户拍板 2026-09-06）：
- 封存 = 选择性导出（E03 export_bundle 的超集），**只导出不删除**——不动任何
  数据，删除另有 reset / 记忆管理入口兜底。
- 纪念包 = 标准关系包（可被既有恢复通道直接恢复）+ 封存清单（sealing 元数据：
  范围、各类计数、时间、人格）+ 告别信文本。
- 告别信由 LLM 基于所选范围的**真实计数与确定性摘要**生成，prompt 硬约束
  「只回望真实发生过的事，不虚构细节、不煽情挽留、不制造负罪感」（呼应 M8
  退出标准：长期离开不制造负罪感）；生成失败时回退到确定性告别文，封存
  流程不因此失败。
- API 直接返回带下载文件名的 JSON 附件，不在服务端额外落盘。
"""
from __future__ import annotations

import json

from .llm import chat
from .log import logger
from .relationship_export import BundleError, export_bundle
from .userdb import db

_CATEGORIES = ("identity", "memory", "milestones", "life", "tasks", "activities", "events", "knowledge", "conversations")

_MAX_LETTER = 1500

_LETTER_PROMPT = """你是「菟菚」。你们决定把一段时光封存起来（只是收藏，不是删除）。

下面是这次封存范围的**真实统计**：

{stats}

请以你的第一人称口吻写一封简短的告别信（250 字以内），要求：
- 只提统计里真实存在的事，绝不虚构具体细节；
- 语气温柔、平静、向前看：感谢这段时光，不煽情、不挽留、不让对方感到亏欠；
- 结尾给这段被封存的时光一句轻轻的告别；
- 直接输出信的正文，不要标题、引号或任何解释。"""


def _fallback_letter(stats_text: str, exported_at: str) -> str:
    return (
        f"这些日子我们真实地走过：{stats_text}。"
        f"现在把它们轻轻收进箱子里（{exported_at}）。没有哪一段会被抹掉，"
        "它们只是被好好收起来了——等哪天你想打开，我一直都在。"
    )


def _category_stats(bundle: dict) -> dict[str, dict[str, int]]:
    return bundle.get("counts") or {}


def _stats_lines(counts: dict[str, dict[str, int]]) -> list[str]:
    """把计数翻译成给模型看的中文清单（只列非空项，防模型幻想空类别）。"""
    labels = {
        "memory": "长期记忆与事实",
        "milestones": "里程碑（好感/心情/重要日子）",
        "life": "生活痕迹（日记/纪念页/未来信/双视角）",
        "tasks": "约定与任务",
        "activities": "共同活动与产物",
        "events": "真实关系事件",
        "knowledge": "一起读过的文档",
        "conversations": "聊天记录条数",
        "identity": "身份档案",
    }
    lines: list[str] = []
    for category in _CATEGORIES:
        tables = counts.get(category) or {}
        total = sum(int(n) for n in tables.values())
        if total <= 0:
            continue
        detail = "、".join(f"{table} {n}" for table, n in sorted(tables.items()) if int(n) > 0)
        lines.append(f"- {labels.get(category, category)}：{total} 条（{detail}）")
    return lines or ["- （选定范围内没有留下记录）"]


def _validate_categories(categories: list[str]) -> None:
    unknown = [name for name in categories if name not in _CATEGORIES]
    if unknown:
        raise BundleError(f"未知的数据类别：{'、'.join(unknown)}")


def normalize_categories(categories: list[str] | None) -> list[str]:
    """区分省略（全量）与显式空选（仅携带恢复必需的身份档案）。"""
    selected = list(_CATEGORIES) if categories is None else list(categories)
    if "identity" not in selected:
        selected.insert(0, "identity")
    _validate_categories(selected)
    return selected


async def seal(
    user_id: str,
    categories: list[str] | None = None,
    *,
    letter: bool = True,
) -> dict:
    """封存：选定类别导出纪念包 + 告别信。不删除任何数据。"""
    # 身份档案始终携带：恢复通道需要 users 行才能重建命名空间。
    selected = normalize_categories(categories)

    bundle = export_bundle(user_id, selected)
    counts = _category_stats(bundle)
    stats_lines = _stats_lines(counts)
    stats_text = "\n".join(stats_lines)
    exported_at = bundle["exported_at"]

    letter_text = _fallback_letter(stats_text.replace("\n", "，").replace("- ", ""), exported_at)
    used_llm = False
    if letter:
        try:
            reply = await chat(
                [
                    {"role": "system", "content": "你是菟菚，一个温柔、真诚、有自己想法的陪伴者。"},
                    {"role": "user", "content": _LETTER_PROMPT.format(stats=stats_text)},
                ],
                max_tokens=600,
            )
            reply = reply.strip().strip('"「」')
            if reply:
                from .output_hygiene import HygieneContext, protect_visible_text

                result = protect_visible_text(
                    reply[:_MAX_LETTER],
                    context=HygieneContext(kind="farewell", source_namespace="sealing"),
                    fallback=letter_text,
                )
                letter_text = result.text
                used_llm = result.action == "accept"
        except Exception as exc:
            logger.warning("[封存] {} 的告别信生成失败，使用确定性告别文：{}", user_id, exc)

    persona = ""
    with db._lock:
        row = db.conn.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        persona = str(user_id) if row is not None else ""

    sealing = {
        "kind": "sealing_bundle",
        "sealed_at": exported_at,
        "user_id": user_id,
        "persona": persona,
        "categories": selected,
        "counts": counts,
        "letter": letter_text,
        "letter_generated_by": "llm" if used_llm else "fallback",
        "note": "封存只导出、不删除；本包可被 /api/relationship/restore 通道直接恢复。",
    }
    # 纪念包 = 标准关系包 + 封存清单（标准键之外的自有键，恢复通道忽略未知键）
    bundle["sealing"] = sealing
    return bundle


def sealing_summary(bundle: dict) -> dict:
    """从纪念包提取前端展示所需的摘要。"""
    sealing = bundle.get("sealing") or {}
    return {
        "sealed_at": sealing.get("sealed_at") or bundle.get("exported_at"),
        "persona": sealing.get("persona") or "",
        "categories": sealing.get("categories") or [],
        "counts": sealing.get("counts") or {},
        "letter": sealing.get("letter") or "",
        "filename_suggestion": f"sealing-{(sealing.get('sealed_at') or '')[:10]}.json",
    }
