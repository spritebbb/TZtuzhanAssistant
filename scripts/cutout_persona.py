# -*- coding: utf-8 -*-
"""抠除 persona.png 背景，输出透明立绘。
为避开联网下载失败，优先用已缓存的模型；首次运行 rembg 会自动下载 u2net 到 U2NET_HOME。
"""
import os
import sys
from pathlib import Path

# 模型缓存放到工作区，避免写用户目录被沙箱限制
os.environ.setdefault("U2NET_HOME", str(Path(__file__).resolve().parent / "models"))

from PIL import Image
from rembg import remove, new_session

# 项目根目录（scripts/ 的上一级）
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "persona.png"
OUT = ROOT / "assets" / "persona_cutout.png"


def main() -> int:
    if not SRC.exists():
        print(f"[ERR] 源图不存在: {SRC}")
        return 1
    print(f"[*] 读取 {SRC}")
    img = Image.open(SRC).convert("RGBA")

    print("[*] 使用 u2net 抠图（首次运行会下载模型到 models/）...")
    session = new_session("u2net")
    cutout = remove(img, session=session, post_process_mask=True)

    # 适度羽化边缘，让抠图更自然
    out = cutout.convert("RGBA")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(OUT, "PNG")
    print(f"[OK] 透明立绘已保存: {OUT} ({out.size[0]}x{out.size[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
