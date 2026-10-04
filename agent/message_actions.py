"""消息级操作（右键菜单）。

实测结论（这些细节都是踩出来的）：

1. **右键必须带指针微移**：纯 press+release 无论按多久微信都不弹菜单，
   「按下 → 移动 2px → 抬起」才弹。见 input.click_at 的 button==3 分支。

2. **右键位置必须落在气泡上**：无障碍给的 list item 矩形是**整行宽度**，
   行中央往往是空白。所以要在行内按颜色找出气泡的实际范围再点。

3. **菜单在无障碍树里**：弹出后是一个独立 X11 窗口，里面的
   `menu item` 节点带名字（复制 / 转发... / 收藏 / 引用 / 提醒 / 翻译 / 多选 / 删除），
   所以可以按名字点击，不需要记坐标。

4. **复制的结果要从剪贴板取**：点「复制」后微信会把正文放进 CLIPBOARD。
"""
import subprocess
import time

from . import geometry, screen
from . import input as inputmod
from .ui import synchronized

# 菜单项的稳定名字（微信 4.1.13 实测）
ACTIONS = ("复制", "放大阅读", "翻译", "搜一搜", "转发...", "收藏",
           "多选", "提醒", "引用", "删除")

# 危险动作：默认拒绝，避免自动化误删用户消息
DANGEROUS_ACTIONS = ("删除", "撤回")


def _background_of_row(img, X, Y, W):
    """取该行出现最多的颜色作为背景。"""
    from collections import Counter
    c = Counter(img.getpixel((x, Y + 3)) for x in range(X, X + W, 4))
    return c.most_common(1)[0][0]


def _content_runs(img, X, Y, W, H, bg, threshold=25):
    """行内「与背景不同」的列，切成连续区间。"""
    def differs(p):
        return sum(abs(a - b) for a, b in zip(p, bg)) > threshold

    cols = []
    for dx in range(W):
        hit = any(differs(img.getpixel((X + dx, Y + dy)))
                  for dy in range(3, H - 3, 2))
        cols.append(hit)

    runs, start = [], None
    for i, hit in enumerate(cols):
        if hit and start is None:
            start = i
        elif not hit and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(cols) - 1))
    return runs


def bubble_rect(chat, index):
    """返回第 index 条消息**气泡**的物理矩形 (x, y, w, h)。

    无障碍给的是整行，这里在行内按像素找出真正的气泡范围。
    头像是一段紧贴行边的窄块，会被排除。
    """
    from . import chat as chatmod

    chatmod.open_chat(chat)
    time.sleep(0.5)
    lst = chatmod._node("list", chatmod.MESSAGE_LIST)
    items = chatmod._children(lst)
    if index < 0 or index >= len(items):
        return None
    import pyatspi
    e = items[index].queryComponent().getExtents(pyatspi.DESKTOP_COORDS)

    t = geometry.global_transform()
    if t is None:
        return None
    sx, ox, sy, oy = t
    X, Y = int(e.x * sx + ox), int(e.y * sy + oy)
    W, H = int(e.width * sx), int(e.height * sy)

    # 用**窗口自身**内容做分析：root 抓取会把盖在上面的弹层一起拍进来，
    # 那会让气泡检测算出错误的点击位置 —— 安全性问题，不只是准确性问题。
    img, (win_x, win_y) = screen.capture_window()
    img = img.convert("RGB")
    lx, ly = X - win_x, Y - win_y          # root 坐标 → 窗口内坐标

    bg = _background_of_row(img, lx, ly, W)
    runs = _content_runs(img, lx, ly, W, H, bg)
    if not runs:
        return None

    # 排除紧贴行边、宽度像头像（30~60px）的窄块
    def is_avatar(run):
        a, b = run
        w = b - a + 1
        return 30 <= w <= 60 and (a < 30 or b > W - 30)

    keeps = [r for r in runs if not is_avatar(r)] or runs
    a, b = max(keeps, key=lambda r: r[1] - r[0])
    return (X + a, Y, b - a + 1, H)        # 返回 root 坐标，供点击使用


@synchronized
def invoke(chat, index, action):
    """对第 index 条消息执行右键菜单动作。返回 (ok, detail, text)。"""
    if action in DANGEROUS_ACTIONS:
        return False, f"拒绝执行危险动作 {action!r}", ""

    from . import chat as chatmod

    if chatmod._popup_guard():
        return False, chatmod._popup_guard(), ""

    rect = bubble_rect(chat, index)
    if rect is None:
        return False, "找不到该消息的气泡", ""
    x, y, w, h = rect

    inputmod.click_at(x + w // 2, y + h // 2, button=3)
    time.sleep(1.4)

    # 菜单是独立窗口；用无障碍按名字找菜单项
    item = geometry.node_physical_rect("menu item", action)
    if item is None:
        inputmod.tap_key("Escape")
        return False, f"菜单里没有 {action!r}（可能消息类型不支持）", ""
    inputmod.click_at(item[0] + item[2] // 2, item[1] + item[3] // 2)
    time.sleep(0.8)

    text = ""
    if action == "复制":
        try:
            out = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                                 capture_output=True, text=True, timeout=10)
            text = out.stdout
        except Exception as e:
            return False, f"已点复制但读剪贴板失败: {e}", ""
    return True, f"已对第 {index} 条消息执行 {action}", text
