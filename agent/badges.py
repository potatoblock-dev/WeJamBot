"""未读徽标（小红点 / 数字徽标）的读取。

为什么不能只靠无障碍树：
    导航图标上的徽标是**绘制出来的装饰**。实测每个导航按钮的无障碍结构
    完全相同（`push button '微信'` 下挂 filler + filler），有徽标与没徽标
    在 role/name/state 上没有任何差别 —— 树里根本没有这个信息。

所以走「无障碍定位置 + 像素颜色判定」：
    * 位置：每个导航按钮的**最后一个子节点**的矩形，恰好是右上角徽标位
      （按钮 60x36，徽标位约在 (208,211) 17x17），无静态坐标；
    * 判定：该矩形内红色像素占比。既不是 OCR，也不依赖分辨率。

数字读不出来（那是 OCR 的活），但会话级未读数是结构化的，见 ListChats。
"""
from .ui import synchronized

# 导航栏条目（微信客户端固定顺序）；定位一律按名字，不按下标
NAV_ITEMS = (
    "微信", "通讯录", "收藏", "朋友圈", "视频号",
    "搜一搜", "小程序面板", "手机", "更多",
)

MIN_RED_PIXELS = 20      # 低于此值认为没有徽标（抗抗锯齿噪点）


def _is_red(r, g, b):
    """微信徽标是饱和红（实测约 #EC0E0D / #FA5151）。"""
    return r > 150 and g < 110 and b < 110


@synchronized
def scan_badges():
    """返回 (badges, unread_chats, unread_messages)。"""
    import pyatspi

    from . import geometry, screen

    # 用窗口自身内容：弹层盖住导航区时 root 抓取会把弹层像素当成徽标
    img, (win_x, win_y) = screen.capture_window()
    img = img.convert("RGB")
    transform = geometry.global_transform()

    badges = []
    for label in NAV_ITEMS:
        node, _frame = geometry.find_node("push button", label)
        entry = {"label": label, "has_badge": False,
                 "red_pixels": 0, "area_pixels": 0}
        if node is None or transform is None:
            badges.append(entry)
            continue
        try:
            # 最后一个子节点 = 右上角徽标位
            if node.childCount == 0:
                badges.append(entry)
                continue
            child = node.getChildAtIndex(node.childCount - 1)
            ext = child.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        except Exception:
            badges.append(entry)
            continue

        sx, ox, sy, oy = transform
        # root 坐标 → 窗口内坐标
        px, py = int(ext.x * sx + ox) - win_x, int(ext.y * sy + oy) - win_y
        pw = max(1, int(ext.width * sx))
        ph = max(1, int(ext.height * sy))
        px = max(0, min(px, img.width - 1))
        py = max(0, min(py, img.height - 1))
        crop = img.crop((px, py, min(px + pw, img.width), min(py + ph, img.height)))
        pixels = list(crop.getdata())
        red = sum(1 for r, g, b in pixels if _is_red(r, g, b))

        entry["red_pixels"] = red
        entry["area_pixels"] = len(pixels)
        entry["has_badge"] = red >= MIN_RED_PIXELS
        badges.append(entry)

    # 会话级未读：结构化数据，不需要像素
    unread_chats = 0
    unread_messages = 0
    try:
        from . import chat as chatmod
        for c in chatmod.list_chats():
            if c["unread_count"] > 0:
                unread_chats += 1
                unread_messages += c["unread_count"]
    except Exception:
        pass

    return badges, unread_chats, unread_messages
