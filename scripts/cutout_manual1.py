# -*- coding: utf-8 -*-
"""用人工确认的视觉坐标，裁剪设定图中的左上大立绘并抠背景。
坐标基于对设定图的视觉检查（归一化百分比）。
"""
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
    img = Image.open(src).convert("RGBA")
    img = img.crop(crop)
    print(f"[*] {src.name} crop={crop} -> {img.size}")
    session = new_session("u2net")
    cut = remove(img, session=session, post_process_mask=True, alpha_matting=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    cut.convert("RGBA").save(out, "PNG")
    print(f"[OK] {out.name} {cut.size}")


def main() -> int:
    v1 = ATT / "94" / "944a8210aafb2334f504c68076574cc2974ec28cf997522aaea9496d5184ade0"  # 1086x1448 竖版
    v2 = ATT / "db" / "db6f38bd876c4f88488d63543205e33dd57a12d8e83aa6dd5bdb8d180ab2accc"  # 1402x1122 横版

    # v1：左上大立绘（全身，深棕发深绿裙）。像素坐标基于视觉检查。
    cutout(v1, ROOT / "assets" / "hotaru_stand_v1.png", (20, 110, 490, 1120))
    # v2：左上大立绘。需看 v2 后再定，先用相近比例。
    return 0


if __name__ == "__main__":
    sys.exit(main())
