"""a11y 逻辑坐标 → X11 物理坐标的比例映射。

GTK 的 a11y extents 用逻辑像素，和 X11 物理像素之间存在未知缩放，
且该缩放随 DPI 变化，不能写成常量。这里用**归一化比例**绕开它：

    rel_x = (node.x - frame.x) / frame.w      # 0..1，与缩放无关
    phys_x = win.x + rel_x * win.w            # 套到 X11 窗口真实几何上

因此不依赖 DPI、分辨率、窗口位置与大小，模块内也无静态坐标常量。
"""
import pyatspi
from Xlib import X, display
from .ui import synchronized


@synchronized
def x11_windows():
    d = display.Display()
    root = d.screen().root
    out = []
    for w in root.query_tree().children:
        try:
            if w.get_attributes().map_state != X.IsViewable:
                continue
            g = w.get_geometry()
            if g.width <= 1 or g.height <= 1:
                continue
            cls = w.get_wm_class() or ("", "")
            out.append({"id": w.id, "x": g.x, "y": g.y,
                        "w": g.width, "h": g.height,
                        "inst": cls[0], "cls": cls[1]})
        except Exception:
            continue
    return out


@synchronized
def a11y_frames():
    out = []

    def walk(node, depth=0):
        if depth > 40:
            return
        try:
            if node.getRoleName() == "frame":
                ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                if ext.width > 1 and ext.height > 1:
                    out.append({"node": node, "name": node.name or "",
                                "x": ext.x, "y": ext.y,
                                "w": ext.width, "h": ext.height})
        except Exception:
            pass
        try:
            for i in range(node.childCount):
                walk(node.getChildAtIndex(i), depth + 1)
        except Exception:
            pass

    d = pyatspi.Registry.getDesktop(0)
    for i in range(d.childCount):
        try:
            walk(d.getChildAtIndex(i))
        except Exception:
            pass
    return out


@synchronized
def pair_frames_with_windows():
    """按宽高比一致性把 a11y frame 与 X11 窗口配对。"""
    frames = a11y_frames()
    wins = x11_windows()
    pairs, used = [], set()
    for f in frames:
        best, best_score = None, None
        for w in wins:
            if w["id"] in used:
                continue
            score = abs(f["w"] / f["h"] - w["w"] / w["h"]) / (f["w"] / f["h"])
            if best_score is None or score < best_score:
                best, best_score = w, score
        if best is not None:
            used.add(best["id"])
            pairs.append((f, best, best_score))
    return pairs


def visible(node):
    try:
        st = node.getState()
        return (st.contains(pyatspi.STATE_VISIBLE)
                and st.contains(pyatspi.STATE_SHOWING))
    except Exception:
        return False


@synchronized
def find_node(role_needle, name_needle, want_visible=True):
    """返回 (node, frame)。

    角色匹配规则：**精确匹配优先，子串匹配兜底**。

    为什么必须这样：纯子串匹配时 `"list" in "list item"` 为真，于是找会话列表
    容器（`list '会话'`）会先命中某个正文里含「会话」二字的消息列表项，
    导致 ListChats 直接返回空 —— 实测踩过，且只在消息文本恰好含该词时复现。
    而精确优先又不牺牲逃生舱接口的便利（仍可写 "button" 匹配 "push button"）。
    """
    exact = []
    loose = []

    def walk(node, frame, depth=0):
        if depth > 45 or exact:
            return
        try:
            role = node.getRoleName() or ""
            name = node.name or ""
            if name_needle in name and (not want_visible or visible(node)):
                if role == role_needle:
                    exact.append((node, frame))
                    return
                if not exact and role_needle and role_needle in role:
                    loose.append((node, frame))
            is_frame = role == "frame"
            for i in range(node.childCount):
                walk(node.getChildAtIndex(i),
                     node if is_frame else frame, depth + 1)
        except Exception:
            pass

    d = pyatspi.Registry.getDesktop(0)
    for i in range(d.childCount):
        try:
            walk(d.getChildAtIndex(i), None)
        except Exception:
            if exact:
                break
        if exact:
            break
    if exact:
        return exact[0]
    return loose[0] if loose else (None, None)


# global_transform 的缓存：键是「所有 X11 窗口的几何」，任一窗口移动/缩放就失效。
_TRANSFORM_CACHE = {}


@synchronized
def global_transform():
    """从一次成功的 frame↔窗口配对推出全局 a11y→物理 变换 (sx, ox, sy, oy)。

    GTK 的 a11y 坐标与 X11 物理坐标之间是纯缩放关系（实测偏移≈0）。
    菜单这类弹出层在 a11y 里没有 frame 祖先，按 frame 做比例映射会失效，
    这时就用这个全局变换兜底。

    **缓存**：算一次要 pair_frames_with_windows()，也就是枚举所有 a11y 帧
    （实测 567ms），而它只在**窗口移动或缩放**时才变 —— 移动窗口是我们自己
    干的。所以拿便宜的 X11 窗口几何（2ms）当失效判据：任一窗口的位置或
    尺寸变了，键就变了，自动重算。
    """
    wins = x11_windows()
    key = tuple(sorted((w["id"], w["x"], w["y"], w["w"], w["h"]) for w in wins))
    if _TRANSFORM_CACHE.get("key") == key:
        return _TRANSFORM_CACHE.get("value")

    pairs = pair_frames_with_windows()
    if not pairs:
        _TRANSFORM_CACHE.update(key=key, value=None)
        return None
    # 取面积最大的那对（主窗口）最稳
    f, w, _score = max(pairs, key=lambda p: p[0]["w"] * p[0]["h"])
    if f["w"] <= 1 or f["h"] <= 1:
        _TRANSFORM_CACHE.update(key=key, value=None)
        return None
    sx = w["w"] / f["w"]
    sy = w["h"] / f["h"]
    ox = w["x"] - f["x"] * sx
    oy = w["y"] - f["y"] * sy
    val = (sx, ox, sy, oy)
    _TRANSFORM_CACHE.update(key=key, value=val)
    return val


@synchronized
def node_physical_rect(role_needle, name_needle):
    """节点在屏幕上的物理矩形 (x, y, w, h)；找不到返回 None。

    两条路：
      1. 节点有 frame 祖先 -> 用「节点相对 frame 的归一化位置」映射到该窗口，
         对客户端装饰（CSD 边距）天然免疫。
      2. 没有 frame 祖先（弹出菜单等）-> 用全局仿射变换。
    两条路都不含静态坐标。
    """
    node, frame = find_node(role_needle, name_needle)
    if node is None:
        return None
    try:
        next_ = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
    except Exception:
        return None

    # 路径 1：有 frame
    if frame is not None:
        try:
            fext = frame.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        except Exception:
            fext = None
        if fext and fext.width > 1 and fext.height > 1:
            for f, w, _score in pair_frames_with_windows():
                same = (f["node"] is frame
                        or (f["x"] == fext.x and f["y"] == fext.y
                            and f["w"] == fext.width and f["h"] == fext.height))
                if not same:
                    continue
                rel_x = (next_.x - fext.x) / fext.width
                rel_y = (next_.y - fext.y) / fext.height
                return (int(round(w["x"] + rel_x * w["w"])),
                        int(round(w["y"] + rel_y * w["h"])),
                        int(round(next_.width / fext.width * w["w"])),
                        int(round(next_.height / fext.height * w["h"])))

    # 路径 2：全局仿射兜底
    t = global_transform()
    if t is None:
        return None
    sx, ox, sy, oy = t
    return (int(round(next_.x * sx + ox)), int(round(next_.y * sy + oy)),
            max(1, int(round(next_.width * sx))), max(1, int(round(next_.height * sy))))
