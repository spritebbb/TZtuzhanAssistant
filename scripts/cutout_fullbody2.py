# -*- coding: utf-8 -*-
"""用 _v2_clip（320x1040，头脚完整）抠图，关闭 post_process 避免头脚丢失。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("U2NET_HOME", str(Path(__file__).resolve().parent / "models"))

from PIL import Image
from rembg import remove, new_session

CLIP = Path(__file__).resolve().parent.parent / "assets" / "_v2_clip.png"
OUT = Path(__file__).resolve().parent.parent / "assets" / "hotaru_v2_full.png"


def main() -> int:
    img = Image.open(CLIP).convert("RGBA")
    session = new_session("u2net")
    cut = remove(img, session=session, post_process_mask=False)
    cut.convert("RGBA").save(OUT, "PNG")
    print(f"[OK] {OUT} {cut.size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
