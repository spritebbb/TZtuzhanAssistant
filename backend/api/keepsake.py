# -*- coding: utf-8 -*-
"""纪念册导出（D4）：把一段归档会话渲染成精排版的独立 HTML 页面。

浏览器打开即为成品：可直接截图存长图，或用浏览器「打印 → 另存为 PDF」。
纯服务端渲染 + 内联 CSS，无新依赖；全部文本经 HTML 转义防 XSS。
"""
from __future__ import annotations

import html
import re
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from ..session import store

router = APIRouter(prefix="/api/keepsake", tags=["keepsake"])

_SAFE_IMAGE_RE = re.compile(r"^/api/images/[\w.\-]+$")


def _fmt_ts(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _render_message(m: dict, persona_name: str = "菟菚") -> str:
    role = "user" if m.get("role") == "user" else "bot"
    who = "你" if role == "user" else persona_name
    content = html.escape(str(m.get("content") or "")).replace("\n", "<br>")
    image = str(m.get("image") or "")
    img_html = ""
    if _SAFE_IMAGE_RE.fullmatch(image):
        img_html = f'<img class="photo" src="{html.escape(image)}" alt="图片" loading="lazy">'
    time_str = _fmt_ts(m.get("ts"))
    time_html = f'<span class="time">{time_str}</span>' if time_str else ""
    return (
        f'<div class="msg {role}">'
        f'<div class="who">{who}{time_html}</div>'
        f'<div class="bubble">{content}{img_html}</div>'
        f"</div>"
    )


def render_keepsake(
    title: str,
    created_at: float,
    messages: list[dict],
    persona_name: str = "菟菚",
) -> str:
    title_html = html.escape(title or "一段对话")
    try:
        date_str = datetime.fromtimestamp(float(created_at)).strftime("%Y年%m月%d日")
    except (TypeError, ValueError, OSError):
        date_str = ""
    body = "\n".join(_render_message(m, persona_name) for m in messages)
    safe_persona_name = html.escape(persona_name)
    footer = (
        "菟丝子研究所 · 纪念册 · 她替你收着的这一段"
        if persona_name == "菟菚"
        else f"{safe_persona_name} · 纪念册 · 一起收着的这一段"
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_html} · {safe_persona_name}纪念册</title>
<style>
  :root {{ --ink: #3a4033; --soft: #8a917d; --paper: #f7f5ee; --card: #fffdf6;
           --vine: #7d9b5f; --vine-deep: #5c7a44; --user-bubble: #e8f0dc; --bot-bubble: #fffdf6; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 40px 16px 60px; background: var(--paper); color: var(--ink);
         font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
         display: flex; justify-content: center; }}
  .book {{ width: min(680px, 100%); }}
  header {{ text-align: center; margin-bottom: 34px; }}
  .vine {{ color: var(--vine); font-size: 20px; letter-spacing: 8px; }}
  h1 {{ margin: 12px 0 6px; font-size: 26px; font-weight: 600; color: var(--vine-deep); }}
  .date {{ color: var(--soft); font-size: 13px; letter-spacing: 2px; }}
  .rule {{ margin: 26px auto; width: 120px; border-top: 1px solid #d5d9c8; position: relative; }}
  .rule::after {{ content: "❀"; position: absolute; top: -11px; left: 50%; transform: translateX(-50%);
                  background: var(--paper); padding: 0 10px; color: var(--vine); font-size: 13px; }}
  .msg {{ margin-bottom: 16px; display: flex; flex-direction: column; }}
  .msg.user {{ align-items: flex-end; }}
  .msg.bot {{ align-items: flex-start; }}
  .who {{ font-size: 12px; color: var(--soft); margin: 0 6px 4px; }}
  .who .time {{ margin-left: 6px; opacity: .7; }}
  .bubble {{ max-width: 82%; padding: 10px 14px; border-radius: 14px; line-height: 1.75;
            font-size: 15px; word-break: break-word; box-shadow: 0 1px 3px rgba(90,100,70,.08); }}
  .user .bubble {{ background: var(--user-bubble); border-bottom-right-radius: 4px; }}
  .bot .bubble {{ background: var(--bot-bubble); border: 1px solid #e6e3d4; border-bottom-left-radius: 4px; }}
  .photo {{ display: block; max-width: min(320px, 100%); border-radius: 10px; margin-top: 8px; }}
  footer {{ margin-top: 44px; text-align: center; color: var(--soft); font-size: 12px; letter-spacing: 3px; }}
  @media print {{
    body {{ padding: 10mm 0; background: #fff; }}
    .msg {{ break-inside: avoid; }}
  }}
</style>
</head>
<body>
<div class="book">
  <header>
    <div class="vine">✿ ❀ ✿</div>
    <h1>{title_html}</h1>
    <div class="date">{date_str} · 共 {len(messages)} 条</div>
    <div class="rule"></div>
  </header>
  {body}
  <footer>{footer}</footer>
</div>
</body>
</html>"""


@router.get("/{archive_id}", response_class=HTMLResponse)
async def api_keepsake(archive_id: str):
    archive = await store.get_archive(archive_id)
    if not archive:
        return JSONResponse({"ok": False, "error": "这段归档不存在"}, status_code=404)
    # get_archive 已解析 messages_json → messages（list[dict]）
    messages = archive.get("messages")
    if not isinstance(messages, list):
        messages = []
    from ..core.persona_profiles import active_name

    return HTMLResponse(
        render_keepsake(
            str(archive.get("title") or "一段对话"),
            archive.get("created_at") or 0,
            [m for m in messages if isinstance(m, dict)],
            active_name(),
        )
    )
