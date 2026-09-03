# -*- coding: utf-8 -*-
"""重新裁剪 v2 设定图左侧大立绘（含脚部）并抠背景。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("U2NET_HOME", str(Path(__file__).resolve().parent / "models"))

from PIL import Image
from rembg import remove, new_session

ROOT = Path(__file__).resolve().parent.parent
ATT = Path(r"C:\Users\sprite\.dsh\attachments\v1\objects")
V2 = ATT / "db" / "db6f38bd876c4f88488d63543205e33dd57a12d8e83aa6dd5bdb8d180ab2accc"


def main() -> int:
    if not V2.exists():
        print(f"[ERR] {V2}")
        return 1
    # 整图 1402x1122；左侧大立绘（含脚部）x150-470, y30-1090
    crop = (150, 30, 478, 1092)
    img = Image.open(V2).convert("RGBA").crop(crop)
    print(f"[*] crop={crop} -> {img.size}")
    session = new_session("u2net")
    cut = remove(img, session=session, post_process_mask=True)
    out = ROOT / "assets" / "hotaru_v2_full.png"
    cut.convert("RGBA").save(out, "PNG")
    print(f"[OK] {out} {cut.size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
