# -*- coding: utf-8 -*-
"""裁剪设定图左侧大立绘并抠背景（坐标基于视觉确认）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("U2NET_HOME", str(Path(__file__).resolve().parent / "models"))

from PIL import Image
from rembg import remove, new_session

ROOT = Path(__file__).resolve().parent.parent
ATT = Path(r"C:\Users\sprite\.dsh\attachments\v1\objects")


def cutout(src: Path, out: Path, crop: tuple[int, int, int, int]) -> None:
    img = Image.open(src).convert("RGBA").crop(crop)
    print(f"[*] {src.name} crop={crop} -> {img.size}")
    session = new_session("u2net")
    cut = remove(img, session=session, post_process_mask=True, alpha_matting=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    cut.convert("RGBA").save(out, "PNG")
    print(f"[OK] {out.name} {cut.size}")


def main() -> int:
    v1 = ATT / "94" / "944a8210aafb2334f504c68076574cc2974ec28cf997522aaea9496d5184ade0"  # 1086x1448 竖版
    v2 = ATT / "db" / "db6f38bd876c4f88488d63543205e33dd57a12d8e83aa6dd5bdb8d180ab2accc"  # 1402x1122 横版

    # v2 左侧大立绘（全身长发站姿）：x150-470, y20-1000
    if v2.exists():
        cutout(v2, ROOT / "assets" / "hotaru_v2.png", (150, 20, 470, 1000))
    # v1 左上大立绘（Q版）：x20-490, y110-1120
    if v1.exists():
        cutout(v1, ROOT / "assets" / "hotaru_v1.png", (20, 110, 490, 1120))
    return 0


if __name__ == "__main__":
    sys.exit(main())
