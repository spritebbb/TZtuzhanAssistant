# -*- coding: utf-8 -*-
"""从角色设定图中自动定位最大的立绘区域，裁剪并抠出透明背景。
思路：设定图多为白/浅底，人物是最大的非背景连通域；
先用阈值找非白像素，区域投影定位人物包围盒，再裁剪 + rembg 抠图。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("U2NET_HOME", str(Path(__file__).resolve().parent / "models"))

import numpy as np
from PIL import Image
from rembg import remove, new_session

ROOT = Path(__file__).resolve().parent.parent
U2NET = "u2net"


def _bbox_of_largest_figure(img: Image.Image, bg_thresh: int = 245) -> tuple[int, int, int, int]:
    """估算画面中最大人物立绘的包围盒 (left, top, right, bottom)。"""
    arr = np.asarray(img.convert("L"))
    # 非背景（较暗）像素
    mask = arr < bg_thresh
    if not mask.any():
        return 0, 0, img.width, img.height
    # 列投影：找人物水平范围（取累计占比，忽略噪声）
    col = mask.sum(axis=0)
    row = mask.sum(axis=1)
    # 用阈值截断尾部噪声
    cthr = col.max() * 0.05
    rthr = row.max() * 0.05
    cols = np.where(col > cthr)[0]
    rows = np.where(row > rthr)[0]
    if cols.size == 0 or rows.size == 0:
        return 0, 0, img.width, img.height
    left, right = int(cols.min()), int(cols.max())
    top, bottom = int(rows.min()), int(rows.max())
    return left, top, right, bottom


def cutout(src: Path, out: Path, crop: tuple[int, int, int, int] | None = None,
           name: str = "u2net") -> None:
    img = Image.open(src).convert("RGBA")
    if crop is not None:
        img = img.crop(crop)
    print(f"[*] {src.name} 裁剪区域 {crop} -> {img.size}")
    session = new_session(name)
    cut = remove(img, session=session, post_process_mask=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    cut.convert("RGBA").save(out, "PNG")
    print(f"[OK] {out} ({cut.size[0]}x{cut.size[1]})")


def main() -> int:
    jobs = [
        # (源图, 输出名, 是否自动定位裁剪)
        (ROOT / "assets" / "persona.png", "persona_cutout.png", False),
    ]
    # 两张设定图（会话附件）
    att = Path(r"C:\Users\sprite\.dsh\attachments\v1\objects")
    set1 = att / "94" / "944a8210aafb2334f504c68076574cc2974ec28cf997522aaea9496d5184ade0"
    set2 = att / "db" / "db6f38bd876c4f88488d63543205e33dd57a12d8e83aa6dd5bdb8d180ab2accc"
    jobs.append((set1, "tuzi_cutout_v1.png", True))
    jobs.append((set2, "tuzi_cutout_v2.png", True))

    for src, out_name, auto_crop in jobs:
        if not src.exists():
            print(f"[WARN] 跳过不存在: {src}")
            continue
        out = ROOT / "assets" / out_name
        if auto_crop:
            crop = _bbox_of_largest_figure(Image.open(src))
            # 适度外扩，避免裁掉发梢/裙摆
            l, t, r, b = crop
            pad_x = int((r - l) * 0.06)
            pad_y = int((b - t) * 0.06)
            crop = (max(0, l - pad_x), max(0, t - pad_y), min(src_w := Image.open(src).width, r + pad_x),
                    min(Image.open(src).height, b + pad_y))
        else:
            crop = None
        cutout(src, out, crop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
