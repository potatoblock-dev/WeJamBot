"""屏幕抓取与二维码解码（服务内部实现）。

刻意**不**去猜「哪个窗口是登录窗」：整个 root 抓下来交给解码器，
由解码器在全图范围内找码。因此不选窗口、不算坐标、不依赖分辨率。
"""
import subprocess
import tempfile
import time

from PIL import Image
from Xlib import X, display
from .ui import synchronized


@synchronized
def capture_root():
    """抓整个 root，返回 PIL.Image。"""
    d = display.Display()
    root = d.screen().root
    g = root.get_geometry()
    raw = root.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
    data = raw.data
    if isinstance(data, str):
        data = data.encode("latin-1")
    bpp = len(data) // (g.width * g.height)
    mode, rawmode = ("RGBA", "BGRA") if (bpp == 4 and g.depth == 32) else ("RGB", "BGRX")
    return Image.frombytes(mode, (g.width, g.height),
                           data[: g.width * g.height * bpp], "raw", rawmode)


@synchronized
def capture_window(win=None):
    """捕获窗口**自身**的内容，忽略盖在它上面的窗口。

    默认抓微信主窗口；传入 geometry.x11_windows() 里的某个窗口字典
    就能抓那个窗口 —— 抓弹出菜单必须这样，因为 Composite 会正确地把
    菜单从主窗口内容里排除掉（这正是它的价值所在）。

    为什么需要它：root 抓取会把盖在上面的弹层一起拍进来。
    对「按颜色找消息气泡」这类分析来说，弹层像素会直接导致**算出错误的
    点击位置**，进而可能点到别的东西 —— 这是安全性问题，不只是准确性问题。

    走 X11 Composite 扩展：把窗口重定向到离屏 pixmap 再读，
    其他窗口画不到这个 pixmap 上。

    返回 (image, (origin_x, origin_y))，origin 是窗口左上角在 root 中的坐标，
    便于把 root 坐标系的节点矩形换算进来。失败则回退到 capture_root()。
    """
    try:
        from Xlib.ext.composite import (NameWindowPixmap, RedirectWindow,
                                        RedirectAutomatic, UnredirectWindow)

        if win is not None:
            d0 = display.Display()
            obj = d0.create_resource_object("window", win["id"])
            g = obj.get_geometry()
            holder = (obj, g)
        else:
            holder = _main_window()
        if holder is None:
            raise RuntimeError("找不到窗口")
        win_obj, g = holder

        d = display.Display()
        low = d.display
        op = d.query_extension("Composite").major_opcode
        if op is None:
            raise RuntimeError("无 Composite 扩展")

        RedirectWindow(display=low, opcode=op, window=win_obj, update=RedirectAutomatic)
        d.sync()
        # 协议本身只要几毫秒；实测固定 sleep 曾占到 400ms 中的 400ms。
        # 留一点余量等重定向生效，但不做无谓等待。
        time.sleep(0.03)
        try:
            pid = low.allocate_resource_id()
            NameWindowPixmap(display=low, opcode=op, window=win_obj, pixmap=pid)
            d.sync()
            time.sleep(0.02)
            pix = d.create_resource_object("pixmap", pid)
            raw = pix.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
            data = raw.data
            if isinstance(data, str):
                data = data.encode("latin-1")
            bpp = len(data) // (g.width * g.height)
            mode, rawmode = (("RGBA", "BGRA") if bpp == 4 else ("RGB", "BGRX"))
            img = Image.frombytes(mode, (g.width, g.height),
                                  data[: g.width * g.height * bpp], "raw", rawmode)
            return img, (g.x, g.y)
        finally:
            UnredirectWindow(display=low, opcode=op, window=win_obj,
                             update=RedirectAutomatic)
            d.sync()
    except Exception:
        return capture_root(), (0, 0)


def _main_window():
    """最大的那个可见窗口 = 微信主窗口。"""
    d = display.Display()
    root = d.screen().root
    best = None
    for w in root.query_tree().children:
        try:
            if w.get_attributes().map_state != X.IsViewable:
                continue
            g = w.get_geometry()
            if g.width < 300 or g.height < 300:
                continue
            if best is None or g.width * g.height > best[1].width * best[1].height:
                best = (w, g)
        except Exception:
            continue
    return best


@synchronized
def decode_qr(image=None):
    """在图像中解码二维码，返回内容字符串；没有则返回 None。"""
    img = image if image is not None else capture_root()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    img.save(tmp)
    try:
        out = subprocess.run(["zbarimg", "--quiet", "--raw", tmp],
                             capture_output=True, text=True, timeout=25)
        payload = out.stdout.strip()
        return payload or None
    except Exception:
        return None
