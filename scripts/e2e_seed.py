# -*- coding: utf-8 -*-
"""E2E 种子脚本：往当前运行的隔离后端数据目录里直接写入测试数据。

背景：facts / user_style_map 没有生产性 POST 端点（它们是提炼链路的产物），
浏览器 e2e 需要这几张表里有数据才能验证「记忆纠偏」「表达观察删除」的
完整 UI 链路。种子写入发生在 webServer 健康检查通过之后，表结构已就绪。

用法（stdout 输出 JSON 供 spec 解析）：
    python scripts/e2e_seed.py --data-dir <dir> --facts 1 --style-map 1

只写 TZTUZHAN_DATA_DIR 指定的隔离库，绝不触碰开发机真实数据。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sqlite3
import sys
from pathlib import Path


def seed(data_dir: Path, want_facts: bool, want_style_map: bool) -> dict:
    db_path = data_dir / "bot.db"
    if not db_path.exists():
        raise SystemExit(f"bot.db not found under {data_dir}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    out: dict = {}

    # 默认人格命名空间（persona id = default → user_id 原样 assistant-main）
    user_id = "assistant-main"

    if want_facts:
        now = _dt.datetime.now().isoformat(timespec="seconds")
        rows = [
            # (content, confidence, pinned, expires_at)
            ("E2E 用户住在江城，喜欢雨天散步。", 0.7, 0, None),
            ("E2E 用户在养一盆名叫「素材」的绿萝。", 0.7, 0,
             (_dt.date.today() + _dt.timedelta(days=30)).isoformat()),
        ]
        ids = []
        existing = {
            r["content"].strip()
            for r in conn.execute(
                "SELECT content FROM facts WHERE user_id = ?", (user_id,)
            ).fetchall()
        }
        for content, confidence, pinned, expires_at in rows:
            if content in existing:  # 幂等：同 run 多用例重复调用不重复插
                continue
            cur = conn.execute(
                "INSERT INTO facts (user_id, content, ts, source_type, source_message_ids, "
                "confidence, verified_at, expires_at, pinned, surface_policy, status, "
                "conflicts_with_fact_id) VALUES (?, ?, ?, 'conversation_inference', '[]', "
                "?, NULL, ?, ?, 'normal', 'active', NULL)",
                (user_id, content, now, confidence, expires_at, pinned),
            )
            ids.append(cur.lastrowid)
        conn.commit()
        out["fact_ids"] = ids

    if want_style_map:
        entries = []
        for situation, style, count in (
            ("倾诉烦恼时", "E2E 喜欢用短句加省略号", 2),
            ("聊到晚饭时", "E2E 会先报菜名再说吃过了", 1),
        ):
            existing = conn.execute(
                "SELECT id FROM user_style_map WHERE user_id = ? AND situation = ?",
                (user_id, situation),
            ).fetchone()
            if existing:
                entries.append(existing["id"])
                continue
            cur = conn.execute(
                "INSERT INTO user_style_map (user_id, situation, style, count, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, situation, style, count,
                 _dt.datetime.now().isoformat(timespec="seconds")),
            )
            entries.append(cur.lastrowid)
        conn.commit()
        out["style_map_ids"] = entries

    conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--facts", type=int, default=0)
    ap.add_argument("--style-map", type=int, default=0)
    args = ap.parse_args()
    result = seed(Path(args.data_dir), bool(args.facts), bool(args.style_map))
    json.dump(result, sys.stdout, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
