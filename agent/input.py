"""输入注入（服务内部实现）。

坐标一律来自运行时定位（见 geometry.node_physical_rect），本模块只负责
把已经算好的坐标点下去，不含任何静态坐标。
"""
import subprocess
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

    # 时延实测：原 0.02+0.012+0.03（63ms/字）**大部分是白等**。
    #   0.010/0.006/0.015 -> 32ms/字 ✅
    #   0.004/0.003/0.006 -> 14ms/字 ✅
    #   全零              -> 丢字 ❌
    # 留一点余量用 18ms/字。注意：丢字是**静默**的，所以调用方必须回读校验，
    # 见 chat.send_text。
    try:
        for ch in text:
            cp = ord(ch)
            ks = cp if cp < 0x100 else (0x01000000 | cp)
            d.change_keyboard_mapping(spare, [[ks] * ncols])
            d.sync()
            time.sleep(0.006)
            xtest.fake_input(d, X.KeyPress, spare)
            d.sync()
            time.sleep(0.004)
            xtest.fake_input(d, X.KeyRelease, spare)
            d.sync()
            time.sleep(0.008)
    finally:
        # 必须恢复：中途异常也要把那个键码还原，否则它会一直留在映射表里
        d.change_keyboard_mapping(spare, [[0] * ncols])
        d.sync()


def _ctrl_key(letter):
    """按一次 Ctrl+<letter>。"""
    from Xlib import XK
    d = display.Display()
    ctrl = d.keysym_to_keycode(XK.string_to_keysym("Control_L"))
    key = d.keysym_to_keycode(XK.string_to_keysym(letter))
    xtest.fake_input(d, X.KeyPress, ctrl)
    d.sync()
    xtest.fake_input(d, X.KeyPress, key)
    d.sync()
    time.sleep(0.05)
    xtest.fake_input(d, X.KeyRelease, key)
    d.sync()
    xtest.fake_input(d, X.KeyRelease, ctrl)
    d.sync()


@synchronized
def paste_text(text, settle=0.35):
    """用剪贴板粘贴文本。

    为什么主力改成剪贴板（而不是逐字符注入键码）：

      * **多行**：逐字符注入时 ord('\\n')=10 就是 Return，而 Return 在微信里
        是「发送」—— 多行文本会把前半句直接发出去。剪贴板天然没这问题。
      * **速度**：粘贴是瞬时的，逐字符注入要 19ms/字（500 字就是 9.5 秒）。

    为什么不担心「污染用户的剪贴板」：本工具是**容器原生**的，
    容器里的 Xvfb 有自己独立的剪贴板，跟宿主完全隔离，也没有剪贴板管理器。
    只有「不跑容器、直接占用宿主 X」才会碰到用户剪贴板 —— 而那种部署方式
    本身比这粗暴得多，正是设计要避免的。

    xclip 必须在粘贴完成前一直活着（X11 剪贴板是拥有者模式）。
    """
    proc = subprocess.Popen(
        ["xclip", "-selection", "clipboard", "-i"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    try:
        proc.stdin.write(text.encode("utf-8"))
        proc.stdin.close()
        time.sleep(0.15)          # 等内容就绪
        _ctrl_key("v")
        time.sleep(settle)        # 等微信取完剪贴板
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


@synchronized
def clear_input():
    """清空输入框。

    必须用 Ctrl+A 全选再删：**逐个退格删不掉换行**（实测残留 '\\n第三行'
    清不掉，导致下一次输入叠加上去）。
    """
    _ctrl_key("a")
    time.sleep(0.25)
    tap_key("BackSpace")
    time.sleep(0.3)


def raise_window(wid):
    """把某个窗口抬到最前。

    为什么需要：独立聊天窗口会盖住主窗口的会话列表区域，
    点会话列表时点击会被投递给盖在上面的独立窗口 —— 实测导致右键菜单
    根本弹不出来。所以点主窗口的控件之前要先把它抬起来。
    """
    d = display.Display()
    w = d.create_resource_object("window", wid)
    w.configure(stack_mode=X.Above)
    d.sync()
    time.sleep(0.25)
