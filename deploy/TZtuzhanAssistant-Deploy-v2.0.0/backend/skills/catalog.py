# -*- coding: utf-8 -*-
"""技能目录（Skills Catalog）：对标 Harness 的按需技能加载。

技能 = 一段可复用的"专家指令"，用 markdown 写在 skills/ 目录下，
通过描述/关键词自动匹配，命中后注入 LLM 上下文，指导菟菚用特定方式完成任务。

技能文件格式（UTF-8 markdown，带 frontmatter）：
```
---
name: 技能名
description: 一句话描述什么时候用这个技能（LLM 靠它判断）
triggers: [关键词1, 关键词2]
---
技能正文（给 LLM 的指令，随命中注入）
```
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# 技能目录：项目根下 skills/（与 persona 同级）
SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"


@dataclass
class Skill:
    name: str
    description: str
    content: str
    triggers: list[str] = field(default_factory=list)
    file: str = ""


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """解析 markdown frontmatter（--- 包裹的键值对），返回 (元数据, 正文)。"""
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    meta: dict = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val.startswith("[") and val.endswith("]"):
                # [a, b, c] → 列表
                val = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
            meta[key] = val
    return meta, parts[2].strip()


def load_skill_file(path: Path) -> Skill | None:
    """从单个 markdown 文件加载技能。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    meta, body = _parse_frontmatter(raw)
    name = str(meta.get("name") or path.stem).strip()
    if not name or not body:
        return None
    return Skill(
        name=name,
        description=str(meta.get("description", "")).strip(),
        content=body,
        triggers=meta.get("triggers") or [],
        file=str(path),
    )


def load_catalog(skills_dir: Path | None = None) -> list[Skill]:
    """加载 skills/ 目录下所有技能。"""
    d = skills_dir or SKILLS_DIR
    if not d.exists():
        return []
    skills: list[Skill] = []
    for path in sorted(d.glob("*.md")):
        if path.name.startswith("_"):  # 忽略下划线开头（模板/草稿）
            continue
        skill = load_skill_file(path)
        if skill:
            skills.append(skill)
    return skills


def match_skills(text: str, skills: list[Skill] | None = None) -> list[Skill]:
    """按关键词匹配技能（简单实现：命中 trigger 即返回，正文注入交给 LLM 判断）。"""
    if not skills:
        skills = load_catalog()
    text_lower = text.lower()
    hits: list[Skill] = []
    for s in skills:
        for trig in s.triggers:
            if trig and trig.lower() in text_lower:
                hits.append(s)
                break
    return hits
