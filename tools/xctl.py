#!/usr/bin/env python3
"""在指定 X 显示上注入鼠标/键盘事件（走 XTEST，只作用于该显示）。

用法:
    python xctl.py click <显示> <窗口id> <x> <y>      # 窗口内坐标点击（自动换算到根坐标）
    python xctl.py clickabs <显示> <x> <y>            # 根坐标点击
    python xctl.py type <显示> <文本>                  # 输入文本
    python xctl.py key <显示> <键名>                   # 按单个键，如 Return / Tab / BackSpace
"""
import sys
import time

from Xlib import X, XK, display
from Xlib.ext import xtest

CMD = sys.argv[1]
d = display.Display(sys.argv[2])
root = d.screen().root


def click(x, y, button=1):
    root.warp_pointer(x, y)
    d.sync()
    time.sleep(0.05)
    xtest.fake_input(d, X.ButtonPress, button)
    d.sync()
    time.sleep(0.05)
    xtest.fake_input(d, X.ButtonRelease, button)
    d.sync()


def keycode(keysym_name):
    ks = XK.string_to_keysym(keysym_name)
    if ks == 0:
        raise SystemExit(f"未知键名: {keysym_name}")
    return d.keysym_to_keycode(ks)


def tap_key(name):
    kc = keycode(name)
    xtest.fake_input(d, X.KeyPress, kc)
    d.sync()
    time.sleep(0.02)
    xtest.fake_input(d, X.KeyRelease, kc)
    d.sync()


SHIFT = keycode("Shift_L")


def type_text(text):
    """逐字符输入。大写和常见符号用 Shift 组合。"""
    for ch in text:
        ks = XK.string_to_keysym({char: name for char, name in _NAMED}.get(ch, ch)) if ch in _NAMED else XK.string_to_keysym(ch)
        if ks == 0:
            # 尝试 ord 转 keysym（Latin-1 直通）
            ks = ord(ch) if ord(ch) < 0x100 else 0
        if ks == 0:
            print(f"  [跳过无法映射的字符] {ch!r}")
            continue
        kc = d.keysym_to_keycode(ks)
        if kc == 0:
            print(f"  [跳过无键码字符] {ch!r}")
            continue
        need_shift = ch.isupper() or ch in '~!@#$%^&*()_+{}|:"<>?'
        if need_shift:
            xtest.fake_input(d, X.KeyPress, SHIFT)
            d.sync()
        xtest.fake_input(d, X.KeyPress, kc)
        d.sync()
        time.sleep(0.01)
        xtest.fake_input(d, X.KeyRelease, kc)
        if need_shift:
            xtest.fake_input(d, X.KeyRelease, SHIFT)
        d.sync()
        time.sleep(0.03)


_NAMED = [
    (' ', 'space'), ('\n', 'Return'), ('\t', 'Tab'),
]


def abs_pos(win):
    """沿窗口树向上累加，求窗口左上角在根窗口中的绝对坐标。"""
    x = y = 0
    cur = win
    while True:
        g = cur.get_geometry()
        x += g.x
        y += g.y
        parent = cur.query_tree().parent
        if parent is None or parent.id == root.id:
            break
        cur = parent
    return x, y


def combo(spec):
    """按键组合，如 ctrl+v / ctrl+shift+a。"""
    names = {"ctrl": "Control_L", "alt": "Alt_L", "shift": "Shift_L", "super": "Super_L"}
    parts = [p.strip().lower() for p in spec.split("+")]
    mods = [names[p] for p in parts[:-1]]
    main = parts[-1]
    mod_codes = [keycode(m) for m in mods]
    main_code = keycode(main)
    for kc in mod_codes:
        xtest.fake_input(d, X.KeyPress, kc)
    d.sync()
    time.sleep(0.03)
    xtest.fake_input(d, X.KeyPress, main_code)
    d.sync()
    time.sleep(0.03)
    xtest.fake_input(d, X.KeyRelease, main_code)
    for kc in reversed(mod_codes):
        xtest.fake_input(d, X.KeyRelease, kc)
    d.sync()
    time.sleep(0.05)


if CMD == "click":
    win = d.create_resource_object("window", int(sys.argv[3], 16))
    wx, wy = int(sys.argv[4]), int(sys.argv[5])
    ox, oy = abs_pos(win)
    rx, ry = ox + wx, oy + wy
    print(f"窗口内 ({wx},{wy}) + 窗口原点 ({ox},{oy}) -> 根坐标 ({rx},{ry})")
    click(rx, ry)
    print("已点击")
elif CMD == "rclick":
    win = d.create_resource_object("window", int(sys.argv[3], 16))
    wx, wy = int(sys.argv[4]), int(sys.argv[5])
    ox, oy = abs_pos(win)
    rx, ry = ox + wx, oy + wy
    print(f"窗口内 ({wx},{wy}) -> 根坐标 ({rx},{ry}) 右键")
    click(rx, ry, button=3)
    print("已右键")
elif CMD == "clickabs":
    x, y = int(sys.argv[3]), int(sys.argv[4])
    click(x, y)
    print(f"已在根坐标 ({x},{y}) 点击")
elif CMD == "focus":
    win = d.create_resource_object("window", int(sys.argv[3], 16))
    win.set_input_focus(X.RevertToParent, X.CurrentTime)
    d.sync()
    print(f"已把键盘焦点设到 0x{win.id:x}")
elif CMD == "typeuni":
    # 中文/任意 Unicode：把一个空闲键码临时重映射成目标码点的 keysym，绕开输入法。
    # Chromium/Qt 认 0x01000000|codepoint 这种 Unicode keysym 约定。
    mn = d.display.info.min_keycode
    mx = d.display.info.max_keycode
    mapping = d.get_keyboard_mapping(mn, mx - mn + 1)
    spare = None
    ncols = 1
    for i, syms in enumerate(mapping):
        ncols = len(syms)
        if all(s == 0 for s in syms):
            spare = mn + i
            break
    if spare is None:
        raise SystemExit("找不到空闲键码，无法做 Unicode 输入")

    text = sys.argv[3]
    print(f"使用空闲键码 {spare}（{ncols} 列）输入 {len(text)} 个字符")
    for ch in text:
        cp = ord(ch)
        ks = cp if cp < 0x100 else (0x01000000 | cp)
        d.change_keyboard_mapping(spare, [[ks] * ncols])
        d.sync()
        time.sleep(0.03)
        xtest.fake_input(d, X.KeyPress, spare)
        d.sync()
        time.sleep(0.015)
        xtest.fake_input(d, X.KeyRelease, spare)
        d.sync()
        time.sleep(0.05)
    d.change_keyboard_mapping(spare, [[0] * ncols])
    d.sync()
    print("已输入(Unicode)")
elif CMD == "type":
    type_text(sys.argv[3])
    print("已输入")
elif CMD == "key":
    tap_key(sys.argv[3])
    print(f"已按键 {sys.argv[3]}")
elif CMD == "combo":
    combo(sys.argv[3])
    print(f"已按组合键 {sys.argv[3]}")
else:
    raise SystemExit(__doc__)
