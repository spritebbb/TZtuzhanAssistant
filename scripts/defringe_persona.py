# -*- coding: utf-8 -*-
"""去除抠图立绘边缘的白边（halo）。
做法：对 alpha 通道做轻微腐蚀（erode），去掉边缘一圈半透明白色残留，
再对颜色通道做 defringe（把边缘像素颜色向不透明区域收缩），并羽化边界。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

SRC = Path(__file__).resolve().parent.parent / "assets" / "hotaru_v1.png"
OUT = Path(__file__).resolve().parent.parent / "assets" / "hotaru_v1_nohalo.png"


def defringe(img: Image.Image, shrink: int = 2) -> Image.Image:
    """腐蚀 alpha 去掉白边，并轻微模糊让边缘自然。"""
    rgba = np.asarray(img).astype(np.float32)
    alpha = rgba[:, :, 3]

    # 1) 白色 halo 检测：alpha 半透明 AND RGB 都偏亮（接近白）→ 属边缘残留
    #    把这类像素的 alpha 压为 0（透明），彻底去白边
    r, g, b = rgba[:, :, 0], rgba[:, :, 1], rgba[:, :, 2]
    bright = (r > 200) & (g > 200) & (b > 200)  # 接近纯白
    halo = bright & (alpha > 8) & (alpha < 235)
    alpha[halo] = 0

    # 2) 对 alpha 做轻微腐蚀：把最外圈半透明像素向内收，减少边缘残留
    a_img = Image.fromarray(alpha.astype(np.uint8))
    a_img = a_img.filter(ImageFilter.MinFilter(size=shrink * 2 + 1))
    alpha_new = np.asarray(a_img).astype(np.float32)

    # 3) 边缘颜色 defringe：对半透明边缘，把颜色向不透明主体靠拢（防止透出浅色）
    #    用一个简单方法：把边缘像素颜色置为其不透明邻居的平均（这里用轻微模糊+按alpha混合近似）
    rgb = rgba[:, :, :3]
    soft = Image.fromarray(rgb.astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))
    rgb_soft = np.asarray(soft).astype(np.float32)
    edge = (alpha_new > 2) & (alpha_new < 220)
    rgb[edge] = rgb_soft[edge]

    out = np.dstack([rgb, alpha_new])
    out = np.clip(out, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def main() -> int:
    if not SRC.exists():
        print(f"[ERR] {SRC}")
        return 1
    img = Image.open(SRC).convert("RGBA")
    out = defringe(img, shrink=2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(OUT, "PNG")
    print(f"[OK] {OUT} {out.size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
