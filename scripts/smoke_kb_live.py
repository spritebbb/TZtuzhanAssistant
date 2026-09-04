# -*- coding: utf-8 -*-
"""D2 知识库真实链路冒烟：真 BGE embedding + 真 Chroma，不 mock 向量层。

数据目录用临时目录，不碰真实 bot.db / chroma，不依赖运行中的后端。
运行：.venv/Scripts/python.exe -X utf8 scripts/smoke_kb_live.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["TZTUZHAN_DATA_DIR"] = tempfile.mkdtemp(prefix="tztuzhan_kb_live_")

def _build_minimal_pdf(text: str) -> bytes:
    """程序化生成带正确 xref 偏移的单页 PDF（英文文本验证 pypdf 解析链路）。"""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(stream)).encode() + b">>\nstream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R>>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


_MINIMAL_PDF = _build_minimal_pdf(
    "Photosynthesis converts sunlight into chemical energy in plants."
)


def main() -> None:
    from backend.core import knowledge
    from backend.core.userdb import db

    uid = "assistant-main"
    db.ensure_user(uid)

    print("== 1. md 文档真实入库 + 真实向量检索 ==")
    md = (
        "菟丝子是旋花科菟丝子属的一年生寄生草本植物。"
        "它没有叶绿素，无法光合作用，依靠吸器刺入宿主茎部获取水分和养分。"
        "宿主多为豆科、菊科植物。菟丝子种子可入药，味辛甘，性平。"
    ) * 8
    doc = knowledge.ingest_document(uid, "菟丝子野外观察.md", md.encode("utf-8"))
    assert doc["indexed"] == doc["chunk_count"] > 0, f"向量化失败: {doc}"
    print(f"   入库 {doc['chunk_count']} 块，向量化 {doc['indexed']} 块")

    hits = knowledge.recall_knowledge(uid, "菟丝子怎么获取营养")
    assert hits, "相关查询应命中"
    print(f"   相关查询命中 {len(hits)} 段，距离 {[round(h['distance'], 3) for h in hits]}")
    assert all(h["distance"] <= 0.55 for h in hits)

    miss = knowledge.recall_knowledge(uid, "量子计算机的原理")
    print(f"   无关查询注入 {len(miss)} 段（应为 0）")
    assert miss == [], f"无关查询应被门控拦截: {miss}"

    print("== 2. pdf 解析链路（pypdf）==")
    pdf_doc = knowledge.ingest_document(uid, "photosynthesis.pdf", _MINIMAL_PDF)
    pdf_hits = knowledge.recall_knowledge(uid, "how do plants use sunlight")
    print(f"   pdf 入库 {pdf_doc['chunk_count']} 块；英文查询命中 {len(pdf_hits)} 段")
    assert pdf_doc["chunk_count"] >= 1

    print("== 3. 删除后检索不到 ==")
    assert knowledge.delete_document(uid, doc["id"])
    assert knowledge.delete_document(uid, pdf_doc["id"])
    after = knowledge.recall_knowledge(uid, "菟丝子怎么获取营养")
    assert after == [], "删除后不应再命中"
    print("   删除后无残留命中")

    print("\n=== 真实链路冒烟：全部通过 ===")


if __name__ == "__main__":
    main()
