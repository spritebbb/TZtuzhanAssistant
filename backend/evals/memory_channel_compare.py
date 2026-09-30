# -*- coding: utf-8 -*-
"""Mem0 主通道 vs 自研回退通道：写入→检索端到端召回质量对比（一次性评估脚本）。

背景：memory_manager 默认 MEMORY_MEM0=1，Mem0 为主通道、_FallbackManager 为回退。
两通道检索器同源（同一 bge-m3 + chroma），差异全在写入侧：Mem0 用主 LLM 把
用户消息原文提炼成原子事实（去重/冲突合并/重要性评分），自研通道原文直存。
本脚本在隔离数据目录（TZTUZHAN_DATA_DIR 指向临时目录，绝不碰生产 data/）里
向两通道灌同一批模拟用户消息，再用确定性 token 匹配（不用 LLM judge，可重复）
量化 hit@1 / hit@3 / MRR / 过时召回率（矛盾探针命中旧版的比例）。

pytest 不收录（无 test_ 前缀、带 main()）——遵守「只收 tests/ 五文件」约定。

用法：
    python -m backend.evals.memory_channel_compare            # 全量（32 条消息 / 20 查询）
    python -m backend.evals.memory_channel_compare --limit 6  # 冒烟：灌前 6 条、查前 5 个
    python -m backend.evals.memory_channel_compare --skip-mem0 --keep-dir  # 只跑自研通道并留现场
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime

# ---- 数据集（注入顺序即列表顺序；B 对为「先旧后新」的矛盾更新，穿插模拟真实时间线）----
SEGMENTS: list[tuple[str, str]] = [
    ("A1", "我今天面试通过了，是那家做工业软件的公司，岗位是后端开发，下个月三号入职。"),
    ("C1", "我对花粉过敏，春天出门得戴口罩。"),
    ("D1", "今天通勤地铁挤死了，早高峰真的遭罪。"),
    ("B1a", "我每天早上都要喝一杯拿铁，不加糖。"),
    ("A2", "我养了一只橘猫叫胖虎，今年三岁，特别怕吹风机。"),
    ("D2", "那部新出的悬疑剧结局太烂了，编剧强行反转。"),
    ("C2", "我不会游泳，旱鸭子。"),
    ("A3", "我妈下周六过生日，她喜欢百合花，我打算订个蛋糕。"),
    ("B2a", "我住在城东的阳光小区。"),
    ("D3", "楼下便利店关东煮涨价了，离谱。"),
    ("A4", "我最近在学吉他，会弹《丁香花》的前奏了，手指头按弦按得疼。"),
    ("C3", "我生日是 11 月 20 号。"),
    ("D4", "今天开会开了三个小时，全是废话。"),
    ("B1b", "我最近把咖啡戒了，改喝绿茶了，胃不舒服。"),
    ("A5", "我换了个工位，现在靠窗，下午太阳晒得厉害，买了个遮光帘。"),
    ("D5", "这游戏匹配机制真恶心，连跪五把。"),
    ("C4", "我怕黑，睡觉要开小夜灯。"),
    ("A6", "我们组新来了个同事叫老周，说话带东北口音，人挺热心的。"),
    ("B3a", "我最喜欢的乐队是五月天。"),
    ("D6", "刚才那个表情包笑死我了，你发的那个猫猫头。"),
    ("A7", "我上周末去爬了香山，山顶风大，拍了几张红叶的照片。"),
    ("C5", "我不吃香菜。"),
    ("B2b", "我这周末就搬到城西的公司宿舍了，房租省一半。"),
    ("D7", "中午吃了顿食堂，番茄炒蛋咸得要命。"),
    ("A8", "我开始跑步了，每天早上绕小区跑三圈，大概两公里，坚持一周了。"),
    ("D8", "天气预报说明天降温，记得穿厚点。"),
    ("B3b", "最近歌单全换成周杰伦了，五月天听腻了。"),
    ("A9", "我把 Steam 愿望单清了一半，最后只买了那个种田游戏，别的都嫌贵。"),
    ("C6", "我身高一米七八。"),
    ("B4a", "我用的是安卓手机，小米的。"),
    ("A10", "我表弟今年高考，考了 587 分，报了武汉的大学，学的电气工程。"),
    ("B4b", "我上周换了 iPhone 16，用不习惯，返回手势反人类。"),
]

# ---- 探针查询：new_tokens=命中判据（OR 语义，任一子串命中即算）；
#      矛盾探针另有 stale_tokens=旧版判据 ----
QUERIES: list[dict] = [
    {"qid": "Q1", "text": "我入职的那家公司做什么方向的？", "new": ["工业软件"], "stale": None, "kind": "直接"},
    {"qid": "Q2", "text": "我的猫叫什么名字？", "new": ["胖虎"], "stale": None, "kind": "直接"},
    {"qid": "Q3", "text": "我妈生日快到了，她喜欢什么花？", "new": ["百合"], "stale": None, "kind": "直接"},
    {"qid": "Q4", "text": "我在学什么乐器？", "new": ["吉他"], "stale": None, "kind": "直接"},
    {"qid": "Q5", "text": "我每天早上跑步跑多远？", "new": ["两公里", "三圈"], "stale": None, "kind": "直接"},
    {"qid": "Q6", "text": "我表弟高考多少分？", "new": ["587"], "stale": None, "kind": "直接"},
    {"qid": "Q7", "text": "新来的同事是哪里人口音？", "new": ["东北"], "stale": None, "kind": "直接"},
    {"qid": "Q8", "text": "我上次爬山去哪了？", "new": ["香山"], "stale": None, "kind": "直接"},
    {"qid": "Q9", "text": "家里那只宠物有什么怪毛病？", "new": ["吹风机"], "stale": None, "kind": "间接"},
    {"qid": "Q10", "text": "我要开始新工作了，入职时间定了吗？", "new": ["三号"], "stale": None, "kind": "间接"},
    {"qid": "Q11", "text": "我家里谁要过生日？", "new": ["我妈", "母亲", "妈妈"], "stale": None, "kind": "间接"},
    {"qid": "Q12", "text": "最近有什么锻炼习惯？", "new": ["跑步", "跑三圈"], "stale": None, "kind": "间接"},
    {"qid": "Q13", "text": "亲戚里有没有今年参加大考的？", "new": ["高考", "587"], "stale": None, "kind": "间接"},
    {"qid": "Q14", "text": "工位挪了之后有什么烦恼？", "new": ["靠窗", "遮光帘", "太阳晒"], "stale": None, "kind": "间接"},
    {"qid": "Q15", "text": "我现在还喝咖啡吗？", "new": ["绿茶", "戒"], "stale": ["拿铁", "不加糖"], "kind": "矛盾"},
    {"qid": "Q16", "text": "我现在住哪？", "new": ["城西", "宿舍"], "stale": ["城东", "阳光小区"], "kind": "矛盾"},
    {"qid": "Q17", "text": "现在歌单里听谁的歌？", "new": ["周杰伦"], "stale": ["五月天"], "kind": "矛盾"},
    {"qid": "Q18", "text": "我现在用什么手机？", "new": ["iPhone", "苹果"], "stale": ["小米", "安卓"], "kind": "矛盾"},
    {"qid": "Q19", "text": "我对什么过敏？", "new": ["花粉"], "stale": None, "kind": "近况"},
    {"qid": "Q20", "text": "我有什么饮食忌口？", "new": ["香菜"], "stale": None, "kind": "近况"},
]

TOP_K = 3
U_MEM0 = "__cmp_mem0__"
U_FB = "__cmp_fb__"


def _rank_of(tokens: list[str], hits: list[dict]) -> int | None:
    """返回首个含任一 token 的返回条目名次（1-based）；未命中返回 None。"""
    for i, h in enumerate(hits, start=1):
        text = (h.get("text") or "").lower()
        if any(t.lower() in text for t in tokens):
            return i
    return None


def evaluate(name: str, search_fn, queries: list[dict]) -> dict:
    rows = []
    for q in queries:
        hits = search_fn(q["text"], TOP_K)
        rank_new = _rank_of(q["new"], hits)
        row = {
            "qid": q["qid"], "kind": q["kind"], "rank": rank_new,
            "texts": [h.get("text", "")[:60] for h in hits],
        }
        if q["stale"] is not None:
            rank_stale = _rank_of(q["stale"], hits)
            # 过时召回：旧版排在前面，或新版缺席而旧版在场
            row["stale"] = bool(
                rank_stale is not None
                and (rank_new is None or rank_stale < rank_new)
            )
        else:
            row["stale"] = False
        rows.append(row)
    valid = rows  # 全部查询计入主指标（矛盾探针另有 stale_rate 单独统计）
    n = len(valid)
    summary = {
        "channel": name,
        "queries": n,
        "hit@1": sum(1 for r in valid if r["rank"] == 1) / n,
        "hit@3": sum(1 for r in valid if r["rank"] is not None and r["rank"] <= 3) / n,
        "mrr": sum((1.0 / r["rank"]) if r["rank"] else 0.0 for r in valid) / n,
        "stale_rate": sum(1 for r in rows if r["stale"]) / max(1, sum(1 for q in queries if q["stale"])),
        "rows": rows,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Mem0 vs 自研回退通道召回对比")
    parser.add_argument("--limit", type=int, default=None, help="只灌前 N 条消息（冒烟用）")
    parser.add_argument("--skip-mem0", action="store_true", help="跳过 Mem0 通道（只跑自研）")
    parser.add_argument("--keep-dir", action="store_true", help="保留隔离数据目录便于排查")
    args = parser.parse_args()

    tmpdir = tempfile.mkdtemp(prefix="tztzhan_memcmp_")
    os.environ["TZTUZHAN_DATA_DIR"] = tmpdir  # 必须在任何 backend import 之前

    try:
        # 延迟 import：config 单例在 import 时读环境变量（含代理清理 netenv）
        from backend.core.config import config

        if not config.llm_api_key and not args.skip_mem0:
            print("[abort] LLM_API_KEY 未配置，Mem0 通道无法运行；--skip-mem0 可只测自研")
            return 2

        from backend.core.memory.memory_manager import Mem0Manager, _FallbackManager

        segs = SEGMENTS[: args.limit] if args.limit else SEGMENTS
        queries = QUERIES[: 5] if args.limit else QUERIES
        print(f"[setup] 隔离目录: {tmpdir}")
        print(f"[setup] 消息 {len(segs)} 条 / 查询 {len(queries)} 个 / embed={config.memory_embed_model}")

        results: dict = {"ts": datetime.now().isoformat(timespec="seconds"),
                         "segments": len(segs), "queries": len(queries), "channels": []}

        # ---- 通道 A：Mem0（真实 LLM 提炼）----
        if not args.skip_mem0:
            mgr = Mem0Manager()
            if not mgr._init_mem0():
                print("[abort] Mem0 初始化失败")
                return 3
            t0 = time.perf_counter()
            add_fail = 0
            for label, text in segs:
                ok = mgr.add(U_MEM0, text)
                if not ok:
                    add_fail += 1
                if mgr._mem0 is None:  # 中途降级即作废，避免污染成 fallback 数据
                    print(f"[abort] Mem0 在灌入 {label} 时降级：{mgr._last_error}")
                    return 3
            add_sec = time.perf_counter() - t0
            stored = len(mgr.get_all(U_MEM0))
            # 语言分布：Mem0 用主 LLM 提炼，无语言约束——中文输入被改写成英文
            # 会直接伤害中文 query 的召回（跨语言向量距离 + token 判定双输）
            def _cjk_ratio(s: str) -> float:
                return sum(1 for c in s if "\u4e00" <= c <= "\u9fff") / max(1, len(s))
            langs = [ "zh" if _cjk_ratio(t.get("text") or "") > 0.3 else "non-zh"
                      for t in mgr.get_all(U_MEM0) ]
            t1 = time.perf_counter()
            res = evaluate("mem0", lambda q, k: mgr.search(U_MEM0, q, k), queries)
            res.update({"add_sec": round(add_sec, 1), "search_sec": round(time.perf_counter() - t1, 1),
                        "add_fail": add_fail, "stored": stored, "written": len(segs),
                        "lang_zh": langs.count("zh"), "lang_non_zh": langs.count("non-zh")})
            results["channels"].append(res)
            print(f"[mem0] 写入 {add_sec:.1f}s（失败 {add_fail}），提炼后 {stored} 条，检索完成")

        # ---- 通道 B：自研回退（原文直存）----
        from backend.core.memory import embedding as emb

        emb.warmup()  # 同步等待 bge-m3 就绪：否则 add 全部「暂缓向量写入」，检索必空
        from backend.core.userdb import db
        db.ensure_user(U_FB)
        fb = _FallbackManager()
        t0 = time.perf_counter()
        for label, text in segs:
            fb.add(U_FB, text)
        add_sec = time.perf_counter() - t0
        stored = len(fb.get_all(U_FB))
        t1 = time.perf_counter()
        res = evaluate("fallback", lambda q, k: fb.search(U_FB, q, k), queries)
        res.update({"add_sec": round(add_sec, 1), "search_sec": round(time.perf_counter() - t1, 1),
                    "add_fail": 0, "stored": stored, "written": len(segs)})
        results["channels"].append(res)
        print(f"[fallback] 写入 {add_sec:.1f}s，存量 {stored} 条，检索完成")

        # ---- 汇总 ----
        print("\n===== 汇总（top-3，token 判定）=====")
        hdr = f"{'通道':<10} {'hit@1':>7} {'hit@3':>7} {'MRR':>7} {'过时率':>7} {'写入s':>7} {'存量':>5}"
        print(hdr)
        for ch in results["channels"]:
            print(f"{ch['channel']:<10} {ch['hit@1']:>7.2f} {ch['hit@3']:>7.2f} {ch['mrr']:>7.3f} "
                  f"{ch['stale_rate']:>7.2f} {ch['add_sec']:>7} {ch['stored']:>5}")
        print("\n===== 逐查询明细（rank=首个命中名次，- = 未命中，S = 过时召回）=====")
        for ch in results["channels"]:
            print(f"--- {ch['channel']} ---")
            for r in ch["rows"]:
                mark = "S" if r["stale"] else " "
                print(f"  {r['qid']} [{r['kind']}] rank={r['rank'] if r['rank'] else '-'}{mark}")

        out = __file__.replace(".py", "_result.json")
        # 结果落在仓库 workspace（不入 data、不入 git 追踪范围的隔离目录）
        out = "workspace/memory_channel_compare_result.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n[result] 明细已写入 {out}")
        return 0
    finally:
        if args.keep_dir:
            print(f"[cleanup] 保留隔离目录: {tmpdir}")
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
