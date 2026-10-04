"""输入注入（服务内部实现）。

坐标一律来自运行时定位（见 geometry.node_physical_rect），本模块只负责
把已经算好的坐标点下去，不含任何静态坐标。
"""
import time

from Xlib import X, display
from Xlib.ext import xtest
from .ui import synchronized


@synchronized
def click_at(x, y, button=1, settle=0.08):
    """点击。

    右键（button=3）**必须带一点指针移动**，这是实测出来的：
    纯 press+release（无论按多久）微信都不弹菜单，
    三步「按下 → 移动 2px → 抬起」才会弹。所以这里对右键特殊处理。
    """
    d = display.Display()
    root = d.screen().root
    x, y = int(x), int(y)
    root.warp_pointer(x, y)
    d.sync()
    time.sleep(settle)

    xtest.fake_input(d, X.ButtonPress, button)
    d.sync()
    if button == 3:
        time.sleep(0.15)
        root.warp_pointer(x + 2, y + 2)
        d.sync()
        time.sleep(0.10)
    else:
        time.sleep(settle)
    xtest.fake_input(d, X.ButtonRelease, button)
    d.sync()


@synchronized
def tap_key(keysym_name):
    from Xlib import XK
    d = display.Display()
    ks = XK.string_to_keysym(keysym_name)
    if ks == 0:
        raise ValueError(f"未知键名: {keysym_name}")
    kc = d.keysym_to_keycode(ks)
    xtest.fake_input(d, X.KeyPress, kc)
    d.sync()
    time.sleep(0.02)
    xtest.fake_input(d, X.KeyRelease, kc)
    d.sync()


@synchronized
def type_text(text):
    """输入任意 Unicode 文本，不依赖输入法、不依赖剪贴板。

    做法：临时把一个空闲键码重映射成目标码点的 keysym。
    Chromium/Qt 认 0x01000000|codepoint 这种 Unicode keysym 约定，
    所以中文、emoji 都能直接打进去，且不经过任何 IME。
    """
    d = display.Display()
    mn = d.display.info.min_keycode
    mx = d.display.info.max_keycode
    mapping = d.get_keyboard_mapping(mn, mx - mn + 1)
    spare, ncols = None, 1
    for i, syms in enumerate(mapping):
        ncols = len(syms)
        if all(s == 0 for s in syms):
            spare = mn + i
            break
    if spare is None:
        raise RuntimeError("找不到空闲键码，无法做 Unicode 输入")

    for ch in text:
        cp = ord(ch)
        ks = cp if cp < 0x100 else (0x01000000 | cp)
        d.change_keyboard_mapping(spare, [[ks] * ncols])
        d.sync()
        time.sleep(0.02)
        xtest.fake_input(d, X.KeyPress, spare)
        d.sync()
        time.sleep(0.012)
        xtest.fake_input(d, X.KeyRelease, spare)
        d.sync()
        time.sleep(0.03)

    d.change_keyboard_mapping(spare, [[0] * ncols])
    d.sync()
