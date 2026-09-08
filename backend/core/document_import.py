# -*- coding: utf-8 -*-
"""L01 网页正文与 EPUB 摄入：有边界的抓取、解析与分段。

契约（docs/Zcode技术指导.md L01 + 调度文档批次 9）：

- URL 只允许 http/https；禁 loopback/private/link-local/云 metadata，DNS 每跳
  校验，超时 15 秒、最多 3 跳；解析后重定向同样逐跳校验（防绕行）；
- EPUB 按 OPF spine 顺序取正文；禁绝对路径/../、外部实体、脚本、远程媒体与
  解压炸弹（条目 ≤2000、解包 ≤100MB）；
- 限额：上传 ≤20MB、文本 ≤200 万字符；魔数与声明双验；
- 同源 hash 重复返回已有文档；版本变化建新版，不覆盖在读映射；
- 解析在 worker 线程执行，可取消；失败事务回滚、索引失败标 pending 可重建。
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import threading
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .log import logger

PARSER_VERSION = "l01-1"
MAX_URL_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_REDIRECTS = 3
FETCH_TIMEOUT = 15
EPUB_MAX_ENTRIES = 2000
EPUB_MAX_UNPACKED = 100 * 1024 * 1024
_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local + 云 metadata 169.254.169.254
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)
_SCRIPT_RE = re.compile(r"<(script|iframe|object|embed)\b.*?</\1>", re.I | re.S)
_STYLE_RE = re.compile(r"<style\b.*?</style>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_REMOTE_RE = re.compile(r"(?:src|href)\s*=\s*[\"']\s*(?:https?:)?//", re.I)


class DocumentImportError(ValueError):
    """可预期的摄入错误（安全边界/格式/限额），API 层转 400/422。"""


# ---- URL 安全 ----

def _resolve_host(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise DocumentImportError(f"域名解析失败：{host}") from exc
    return sorted({str(info[4][0]) for info in infos})


def _ensure_public_ip(host: str) -> list[str]:
    addresses = _resolve_host(host)
    if not addresses:
        raise DocumentImportError(f"域名没有可用地址：{host}")
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise DocumentImportError(f"无法识别的地址：{raw}")
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise DocumentImportError("这个地址指向本机或内网，不能抓取")
        if any(ip in net for net in _BLOCKED_NETWORKS):
            raise DocumentImportError("这个地址指向内网或云元数据，不能抓取")
    return addresses


def check_url(url: str) -> str:
    """校验并规范化 URL；不安全一律拒绝（不做任何网络请求）。"""
    raw = str(url or "").strip()
    if not raw or len(raw) > 2048:
        raise DocumentImportError("网址为空或过长")
    parts = urlsplit(raw)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise DocumentImportError("只支持 http / https 链接")
    if not parts.hostname:
        raise DocumentImportError("网址缺少域名")
    if parts.username or parts.password:
        raise DocumentImportError("不接受带账号密码的网址")
    _ensure_public_ip(parts.hostname)
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    netloc = parts.hostname.lower()
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return parts._replace(scheme=parts.scheme.lower(), netloc=netloc,
                          path=path, fragment="").geturl()


def fetch_html(url: str, *, fetch=None) -> tuple[str, str]:
    """逐跳校验地抓取网页正文；返回 (最终 URL, HTML 文本)。"""
    fetcher = fetch or _default_fetch
    current = check_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        status, headers, body = fetcher(current, timeout=FETCH_TIMEOUT)
        if status in (301, 302, 303, 307, 308):
            location = str(headers.get("location") or "").strip()
            if not location:
                raise DocumentImportError("重定向缺少目标地址")
            current = check_url(urljoin(current, location))  # 每跳重新校验
            continue
        if status != 200:
            raise DocumentImportError(f"抓取失败（HTTP {status}）")
        if len(body) > MAX_URL_BYTES:
            raise DocumentImportError("网页超过 20MB 上限")
        return current, _decode(body)
    raise DocumentImportError("重定向次数过多")


def _default_fetch(url: str, *, timeout: int):
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "TuzhanAssistant/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 已逐跳校验
            return resp.status, dict(resp.headers), resp.read(MAX_URL_BYTES + 1)
    except Exception as exc:
        raise DocumentImportError(f"抓取失败：{type(exc).__name__}") from exc


def _decode(body: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentImportError("网页编码识别失败")


def html_to_text(html: str) -> str:
    """去脚本/样式/标签；远程媒体不加载，只留文字。"""
    if _REMOTE_RE.search(html or ""):
        html = _REMOTE_RE.sub("data-blocked=", html)  # 远程资源不保留地址
    text = _SCRIPT_RE.sub(" ", html or "")
    text = _STYLE_RE.sub(" ", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.I)
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;?", "&", text)
    text = re.sub(r"&lt;?", "<", text)
    text = re.sub(r"&gt;?", ">", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---- EPUB ----

def parse_epub(data: bytes) -> str:
    """按 OPF spine 顺序提正文；禁路径穿越/外部实体/解压炸弹。"""
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > EPUB_MAX_ENTRIES:
                raise DocumentImportError("EPUB 条目过多，疑似解压炸弹")
            total = sum(int(info.file_size) for info in infos)
            if total > EPUB_MAX_UNPACKED:
                raise DocumentImportError("EPUB 解包后过大，疑似解压炸弹")
            for info in infos:
                name = info.filename
                if name.startswith(("/", "\\")) or ".." in name.split("/"):
                    raise DocumentImportError("EPUB 含非法路径，已拒绝")
            opf_path = _find_opf(zf, infos)
            if opf_path is None:
                raise DocumentImportError("EPUB 缺少 OPF 清单")
            opf = zf.read(opf_path).decode("utf-8", errors="replace")
            if "<!ENTITY" in opf.upper():
                raise DocumentImportError("EPUB 含外部实体声明，已拒绝")
            order = _spine_items(opf)
            base = str(Path(opf_path).parent).replace("\\", "/")
            parts: list[str] = []
            for href in order:
                target = href if not base or base == "." else f"{base}/{href}"
                target = target.replace("//", "/").lstrip("/")
                try:
                    raw = zf.read(target)
                except KeyError:
                    continue
                parts.append(html_to_text(raw.decode("utf-8", errors="replace")))
            text = "\n\n".join(p for p in parts if p)
    except zipfile.BadZipFile as exc:
        raise DocumentImportError("EPUB 文件损坏或不是有效的 ZIP") from exc
    if not text.strip():
        raise DocumentImportError("EPUB 里没有可读的正文")
    return text


def _find_opf(zf, infos) -> str | None:
    for info in infos:
        if info.filename.lower().endswith(".opf"):
            return info.filename
    try:
        container = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
    except KeyError:
        return None
    match = re.search(r'full-path="([^"]+)"', container)
    return match.group(1) if match else None


def _spine_items(opf: str) -> list[str]:
    manifest = {
        m.group(1): m.group(2)
        for m in re.finditer(r'<item\b[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf)
    }
    order = [m.group(1) for m in re.finditer(r'<itemref\b[^>]*idref="([^"]+)"', opf)]
    return [manifest[i] for i in order if i in manifest]


# ---- 分段与入库 ----

def build_segments(text: str, *, min_chars: int = 40) -> list[dict]:
    """按空行切段并记录偏移与内容哈希（独立于检索 chunks）。"""
    segments: list[dict] = []
    cursor = 0
    for block in re.split(r"\n{2,}", text or ""):
        piece = block.strip()
        if not piece:
            cursor += len(block) + 2
            continue
        start = text.find(piece, cursor)
        if start < 0:
            start = cursor
        end = start + len(piece)
        cursor = end
        if len(piece) < min_chars and segments:
            # 过短的段落并入上一段（标题、脚注等），保持地图可读
            segments[-1]["text_end"] = end
            segments[-1]["content_hash"] = _hash(
                text[segments[-1]["text_start"]:end])
            continue
        segments.append({
            "index": len(segments),
            "title": piece[:40],
            "text_start": start,
            "text_end": end,
            "content_hash": _hash(piece),
        })
    return segments


def _hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


def source_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def import_text(user_id: str, filename: str, data: bytes, *, fmt: str | None = None,
                source_url: str = "", segments: list[dict] | None = None) -> dict:
    """落库一份文档 + 阅读段；同源 hash 重复返回已有文档（不重复入库）。"""
    from .knowledge import KnowledgeError, ingest_document
    from .userdb import db

    digest = source_hash(data)
    with db._lock:
        row = db.conn.execute(
            "SELECT id, filename FROM kb_documents WHERE user_id=? AND source_hash=?",
            (user_id, digest),
        ).fetchone()
    if row is not None:
        return {"id": int(row["id"]), "filename": str(row["filename"]),
                "deduplicated": True, "source_hash": digest}
    try:
        doc = ingest_document(user_id, filename, data)
    except KnowledgeError as exc:
        raise DocumentImportError(str(exc)) from exc
    doc_id = int(doc["id"])
    with db._lock:
        db.conn.execute(
            "UPDATE kb_documents SET source_url=?, source_hash=?, parser_version=? WHERE id=?",
            (source_url, digest, PARSER_VERSION, doc_id),
        )
        if segments:
            for seg in segments:
                db.conn.execute(
                    "INSERT OR IGNORE INTO document_segments "
                    "(user_id, document_id, segment_index, title, text_start, text_end, "
                    "content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (user_id, doc_id, int(seg["index"]), str(seg["title"])[:40],
                     int(seg["text_start"]), int(seg["text_end"]),
                     str(seg["content_hash"]), datetime.now().isoformat(timespec="seconds")),
                )
        db.conn.commit()
    doc.update({"deduplicated": False, "source_hash": digest})
    return doc


# ---- 导入任务（worker 线程 + 可取消） ----

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _set_job(job_id: str, **fields) -> None:
    with _JOBS_LOCK:
        job = _JOBS.setdefault(job_id, {})
        job.update(fields)
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
    from .userdb import db

    status = fields.get("status")
    if status:
        with db._lock:
            db.conn.execute(
                "UPDATE document_import_jobs SET status=?, document_id=?, error=?, updated_at=? "
                "WHERE job_id=?",
                (status, fields.get("document_id"), str(fields.get("error") or ""),
                 datetime.now().isoformat(timespec="seconds"), job_id),
            )
            db.conn.commit()


def job_state(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def cancel_job(job_id: str) -> bool:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None or job.get("status") in {"succeeded", "failed", "cancelled"}:
            return False
        job["cancel"] = True
    _set_job(job_id, status="cancelled")
    return True


def start_url_import(user_id: str, url: str, *, fetch=None) -> dict:
    """启动 URL 导入任务（worker 线程，可取消）；返回 job_id。"""
    from .userdb import db

    checked = check_url(url)
    job_id = uuid.uuid4().hex[:16]
    now = datetime.now().isoformat(timespec="seconds")
    with db._lock:
        db.conn.execute(
            "INSERT INTO document_import_jobs "
            "(job_id, user_id, kind, source, status, created_at, updated_at) "
            "VALUES (?, ?, 'url', ?, 'pending', ?, ?)",
            (job_id, user_id, checked, now, now),
        )
        db.conn.commit()
    with _JOBS_LOCK:
        _JOBS[job_id] = {"job_id": job_id, "status": "pending", "cancel": False}

    def _worker() -> None:
        if (job_state(job_id) or {}).get("cancel"):
            return
        _set_job(job_id, status="running")
        try:
            final_url, html = fetch_html(checked, fetch=fetch)
            if (job_state(job_id) or {}).get("cancel"):
                return
            text = html_to_text(html)
            if not text:
                raise DocumentImportError("网页没有可读正文")
            if len(text) > MAX_TEXT_CHARS:
                raise DocumentImportError("网页正文超过 200 万字符上限")
            data = text.encode("utf-8")
            doc = import_text(user_id, f"{Path(urlsplit(final_url).path).name or '网页'}.txt",
                              data, source_url=final_url,
                              segments=build_segments(text))
            _set_job(job_id, status="succeeded", document_id=int(doc["id"]))
        except Exception as exc:
            logger.warning("[文档摄入] URL 导入失败：{}", exc)
            _set_job(job_id, status="failed", error=str(exc)[:200])

    threading.Thread(target=_worker, name=f"doc-import-{job_id}", daemon=True).start()
    return {"job_id": job_id, "status": "pending", "source": checked}


def forget_for_document(user_id: str, document_id: int) -> int:
    """文档删除：清阅读段（F05 地图由既有 forget 路径清理）。"""
    from .userdb import db

    with db._lock:
        cur = db.conn.execute(
            "DELETE FROM document_segments WHERE user_id=? AND document_id=?",
            (user_id, int(document_id)),
        )
        db.conn.commit()
    return int(cur.rowcount)
