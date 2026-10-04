#!/usr/bin/env python3
"""在指定 X 显示上抓根窗口（或某个窗口）的像素，存 PNG。

用法:
    python xgrab.py <输出.png> [显示号] [窗口id(十六进制)]
例:
    python xgrab.py shot.png :77
    python xgrab.py win.png :77 0xe00004
"""
import sys

from Xlib import X, display
from PIL import Image

OUT = sys.argv[1]
DISP = sys.argv[2] if len(sys.argv) > 2 else ":77"
WID = int(sys.argv[3], 16) if len(sys.argv) > 3 else None

d = display.Display(DISP)
root = d.screen().root
win = d.create_resource_object("window", WID) if WID else root

g = win.get_geometry()
raw = win.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
data = raw.data
if isinstance(data, str):
    data = data.encode("latin-1")

# X 的 ZPixmap 一律按 4 字节/像素扫描线对齐，不能按 depth 推
npix = g.width * g.height
bpp = len(data) // npix
if bpp == 4:
    mode, raw_mode = ("RGBA", "BGRA") if g.depth == 32 else ("RGB", "BGRX")
elif bpp == 3:
    mode, raw_mode = "RGB", "BGR"
else:
    raise SystemExit(f"意外的像素宽度 bpp={bpp} depth={g.depth} len={len(data)}")

img = Image.frombytes(mode, (g.width, g.height), data[: npix * bpp], "raw", raw_mode)
img.save(OUT)

# 判据：统计非黑像素，区分「真画面」和「一片黑」
small = img.convert("RGB")
px = list(small.getdata())
nonblack = sum(1 for p in px if p != (0, 0, 0))
print(f"OK {OUT} {g.width}x{g.height} depth={g.depth} "
      f"非黑像素 {nonblack}/{len(px)} ({nonblack / len(px):.1%})")
