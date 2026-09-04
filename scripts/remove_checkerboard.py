"""把生图模型烘焙进 RGB 的透明棋盘格恢复为真实 alpha。

算法只把“与画布边缘连通的高亮近中性色区域”视为背景，避免全局色键
误删角色的白色研究服；随后仅在背景边缘 3px 内按亮度/色度做轻量羽化。
"""
from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def _edge_connected(mask: np.ndarray) -> np.ndarray:
    """返回二值图中与任一画布边缘连通的区域（四邻域）。"""
    h, w = mask.shape
    flat = mask.reshape(-1)
    seen = bytearray(h * w)
    queue: deque[int] = deque()
    for x in range(w):
        if mask[0, x]: queue.append(x)
        if mask[h - 1, x]: queue.append((h - 1) * w + x)
    for y in range(1, h - 1):
        if mask[y, 0]: queue.append(y * w)
        if mask[y, w - 1]: queue.append(y * w + w - 1)
    while queue:
        idx = queue.popleft()
        if seen[idx] or not flat[idx]:
            continue
        seen[idx] = 1
        x = idx % w
        if x: queue.append(idx - 1)
        if x + 1 < w: queue.append(idx + 1)
        if idx >= w: queue.append(idx - w)
        if idx + w < h * w: queue.append(idx + w)
    return np.frombuffer(seen, dtype=np.uint8).reshape(h, w).astype(bool)


def remove_checkerboard(source: Path, target: Path) -> tuple[int, int]:
    rgb = Image.open(source).convert("RGB")
    arr = np.asarray(rgb, dtype=np.int16)
    hi = arr.max(axis=2)
    lo = arr.min(axis=2)
    chroma = hi - lo
    light = arr.mean(axis=2)

    # 棋盘底色会因每次生成落在约 208~255 的不同灰阶；二值化后相邻方格
    # 仍连成同一片。人物的白衣即便命中颜色条件，也会被深色轮廓隔开。
    candidate = (light >= 185) & (chroma <= 14)
    connected = _edge_connected(candidate)
    h, w = candidate.shape
    alpha = np.full((h, w), 255, dtype=np.uint8)
    alpha[connected] = 0

    # 只处理紧贴已确认背景的 3px 边缘；颜色越像亮灰背景，透明度越低。
    core = Image.fromarray(np.where(connected, 255, 0).astype(np.uint8), mode="L")
    near = np.asarray(core.filter(ImageFilter.MaxFilter(7))) > 0
    fringe = near & ~connected
    if fringe.any():
        color_opacity = np.clip((chroma - 3) / 30, 0, 1)
        dark_opacity = np.clip((225 - light) / 48, 0, 1)
        opacity = np.maximum(color_opacity, dark_opacity)
        alpha[fringe] = np.minimum(alpha[fringe], (opacity[fringe] * 255).astype(np.uint8))

    rgba = np.dstack((arr.astype(np.uint8), alpha))
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(target, optimize=True)
    return int((alpha == 0).sum()), int(((alpha > 0) & (alpha < 255)).sum())


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove baked checkerboard background")
    parser.add_argument("pairs", nargs="+", help="SOURCE=TARGET pairs")
    args = parser.parse_args()
    for pair in args.pairs:
        if "=" not in pair:
            parser.error(f"invalid pair: {pair}")
        src, dst = pair.split("=", 1)
        transparent, feathered = remove_checkerboard(Path(src), Path(dst))
        print(f"{dst}: transparent={transparent}, feathered={feathered}")


if __name__ == "__main__":
    main()
