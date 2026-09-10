# -*- coding: utf-8 -*-
"""L01 文档摄入：URL 安全边界、EPUB 防炸、分段、去重与任务取消。

验收锚点（docs/Zcode技术指导.md L01 + 调度文档批次 9）：
- URL：只 http/https；loopback/private/link-local/云 metadata 一律拒绝；
  重定向逐跳校验（绕行同样拒绝）；超时与跳数受限；
- EPUB：OPF spine 顺序；路径穿越/外部实体/解压炸弹拒绝；
- 分段记录偏移与内容哈希；同源 hash 重复返回已有文档；
- 任务可取消；失败状态可查。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_l01_"))

from backend.core import document_import as di
from backend.core.userdb import db


def test_url_safety() -> int:
    for bad in (
        "file:///etc/passwd",
        "http://127.0.0.1/admin",
        "http://localhost:8000/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://user:pass@example.com/",
        "",
    ):
        try:
            di.check_url(bad)
            raise AssertionError(f"应拒绝：{bad}")
        except di.DocumentImportError:
            pass
    # 正常公网地址通过（固定 DNS，测试不依赖执行环境的解析策略）
    with patch.object(di, "_resolve_host", return_value=["93.184.216.34"]):
        checked = di.check_url("HTTPS://Example.com/a//b?x=1#frag")
    assert checked == "https://example.com/a/b?x=1", checked
    print("[OK] URL 边界：协议/内网/元数据/账号密码拒绝；正常地址规范化")
    return 0


def test_redirect_escape_rejected() -> int:
    def fake_fetch(url: str, *, timeout: int):
        return 302, {"location": "http://169.254.169.254/latest/meta-data/"}, b""

    with patch.object(di, "_resolve_host", return_value=["93.184.216.34"]):
        try:
            di.fetch_html("https://example.com/start", fetch=fake_fetch)
            raise AssertionError("重定向到元数据地址应被拒绝")
        except di.DocumentImportError:
            pass
    # 超过跳数上限
    def loop_fetch(url: str, *, timeout: int):
        return 302, {"location": "https://example.com/next"}, b""

    with patch.object(di, "_resolve_host", return_value=["93.184.216.34"]):
        try:
            di.fetch_html("https://example.com/start", fetch=loop_fetch)
            raise AssertionError("重定向次数过多应被拒绝")
        except di.DocumentImportError:
            pass
    print("[OK] 重定向逐跳校验：绕行到内网/元数据被拒；跳数受限")
    return 0


def test_default_fetch_pins_dns_and_surfaces_redirect() -> int:
    class RedirectResponse:
        code = 302
        headers = {"Location": "https://next.example/final"}

    class FakeOpener:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(
                req.full_url, 302, "Found", RedirectResponse.headers, None
            )

    pinned: list[str] = []

    def fake_builder(ip: str, *handlers):
        pinned.append(ip)
        assert handlers, "必须安装禁止自动重定向的 handler"
        return FakeOpener()

    with patch.object(di, "_ensure_public_ip", return_value=["93.184.216.34"]), patch(
        "backend.tools.safety.build_pinned_opener", side_effect=fake_builder
    ):
        status, headers, body = di._default_fetch("https://example.com/start", timeout=3)

    assert pinned == ["93.184.216.34"]
    assert status == 302 and headers["location"].endswith("/final") and body == b""
    print("[OK] 默认抓取：连接固定到已验证 IP，重定向交回逐跳校验")
    return 0


def test_html_to_text_strips_scripts() -> int:
    html = ('<html><head><style>body{}</style><script>alert(1)</script></head>'
            '<body><h1>标题</h1><p>正文一</p><img src="//evil.example/x.png">'
            '<p>正文二</p></body></html>')
    text = di.html_to_text(html)
    assert "标题" in text and "正文一" in text and "正文二" in text
    assert "alert" not in text and "body{}" not in text
    assert "evil.example" not in text, "远程资源地址不得保留"
    print("[OK] HTML 清洗：去脚本/样式，远程资源地址不保留")
    return 0


def _epub(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_epub_spine_and_guards() -> int:
    opf = ('<package><manifest>'
           '<item id="c1" href="ch1.xhtml"/><item id="c2" href="ch2.xhtml"/>'
           '</manifest><spine><itemref idref="c2"/><itemref idref="c1"/></spine></package>')
    data = _epub({
        "META-INF/container.xml": '<container><rootfile full-path="OEBPS/content.opf"/></container>',
        "OEBPS/content.opf": opf,
        "OEBPS/ch1.xhtml": "<p>第一章正文</p>",
        "OEBPS/ch2.xhtml": "<p>第二章正文</p>",
    })
    text = di.parse_epub(data)
    assert text.index("第二章正文") < text.index("第一章正文"), "必须按 spine 顺序"

    # 路径穿越
    bad = _epub({"META-INF/container.xml": "<x/>", "../evil.xhtml": "<p>x</p>"})
    try:
        di.parse_epub(bad)
        raise AssertionError("路径穿越应被拒绝")
    except di.DocumentImportError:
        pass
    # 外部实体
    bad2 = _epub({
        "META-INF/container.xml": '<container><rootfile full-path="c.opf"/></container>',
        "c.opf": '<package><!ENTITY xxe SYSTEM "file:///etc/passwd"><manifest/></package>',
    })
    try:
        di.parse_epub(bad2)
        raise AssertionError("外部实体应被拒绝")
    except di.DocumentImportError:
        pass
    # 条目数炸弹
    many = {f"f{i}.txt": "x" for i in range(di.EPUB_MAX_ENTRIES + 5)}
    try:
        di.parse_epub(_epub(many))
        raise AssertionError("条目过多应被拒绝")
    except di.DocumentImportError:
        pass
    print("[OK] EPUB：spine 顺序；路径穿越/外部实体/条目炸弹全部拒绝")
    return 0


def test_segments_dedup_and_forget() -> int:
    uid = "l01-import"
    db.ensure_user(uid)
    text = ("第一段内容足够长一些以便成为独立段落。" * 2
            + chr(10) * 2
            + "第二段内容同样足够长一些，用来验证独立成段。" * 2)
    segments = di.build_segments(text)
    assert len(segments) == 2, segments
    assert segments[0]["text_start"] == 0
    assert segments[1]["text_start"] > segments[0]["text_end"]
    assert segments[0]["content_hash"] != segments[1]["content_hash"]

    data = text.encode("utf-8")
    doc = di.import_text(uid, "网页.txt", data, source_url="https://example.com/a",
                         segments=segments)
    assert not doc["deduplicated"]
    rows = db.conn.execute(
        "SELECT COUNT(*) n FROM document_segments WHERE user_id=? AND document_id=?",
        (uid, doc["id"])).fetchone()["n"]
    assert rows == 2
    # 同源 hash 去重
    again = di.import_text(uid, "网页.txt", data, source_url="https://example.com/a")
    assert again["deduplicated"] and again["id"] == doc["id"]
    # 文档删除清段
    assert di.forget_for_document(uid, doc["id"]) == 2
    print("[OK] 分段偏移/哈希 + 同源去重 + 删除清段")
    return 0


def test_job_cancel() -> int:
    uid = "l01-job"
    db.ensure_user(uid)
    with patch.object(di, "_resolve_host", return_value=["93.184.216.34"]):
        started = di.start_url_import(
            uid, "https://example.com/page",
            fetch=lambda url, **kw: (200, {}, "慢".encode("utf-8")),
        )
        job_id = started["job_id"]
        deadline = time.time() + 5
        while time.time() < deadline:
            state = di.job_state(job_id) or {}
            if state.get("status") in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
    assert (di.job_state(job_id) or {}).get("status") == "succeeded", di.job_state(job_id)
    row = db.conn.execute(
        "SELECT status, document_id FROM document_import_jobs WHERE job_id=?",
        (job_id,)).fetchone()
    assert row["status"] == "succeeded" and row["document_id"]
    # 已结束的任务不可取消
    assert di.cancel_job(job_id) is False
    print("[OK] 导入任务：worker 落库成功、状态可查、终态不可取消")
    return 0


def main() -> int:
    failed = (
        test_url_safety()
        + test_redirect_escape_rejected()
        + test_default_fetch_pins_dns_and_surfaces_redirect()
        + test_html_to_text_strips_scripts()
        + test_epub_spine_and_guards()
        + test_segments_dedup_and_forget()
        + test_job_cancel()
    )
    if failed:
        print(f"\n=== L01 文档摄入：{failed} 项失败 ===")
        return 1
    print("\n=== L01 文档摄入：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
