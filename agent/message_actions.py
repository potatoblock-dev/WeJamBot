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

from . import geometry, screen, selflog
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


# ---------------------------------------------------------------------------
# 群聊消息的发送者
#
# 难点：消息列表项在无障碍树里是**扁平纯文本**，没有发送者节点。
# 界面上那条「黑百合的花语」确实画在气泡上方，但全树 1471 个节点里搜这个
# 字符串，只在**会话列表预览**里命中一次 —— 消息区就是没有。
# 悬停也不出提示。
#
# 可行路径：**点发送者头像**会弹出资料卡，卡片里的节点是暴露给无障碍的。
# 于是「点头像 → 在卡片窗口范围内取最靠上的文本」就能拿到群昵称。
# ---------------------------------------------------------------------------
# 资料卡上不是昵称的固定字段（不同客户端版本/不同资料的布局并不一致）
FIELD_LABELS = {
    "地区：", "地区", "来源：", "来源", "朋友圈", "添加到通讯录", "备注",
    "标签", "共同群聊", "个性签名", "微信号", "群昵称：", "更多信息",
    "发消息", "音视频通话", "资料设置",
}


def _pick_nickname(hits):
    """从资料卡的文本节点里挑出昵称。

    两种实测布局：
      A) 顶部直接是昵称（可能同时以 push button 与 label 出现）
      B) 先一行「群昵称：」字段标签，昵称在它之后
    所以规则是「按位置排序，跳过字段标签，取第一个其余文本」。
    """
    for _y, _x, txt in sorted(hits):
        t = (txt or "").strip()
        if not t or t.endswith("：") or t.endswith(":"):
            continue
        if t in FIELD_LABELS:
            continue
        return t
    return ""


AVATAR_MIN_W, AVATAR_MAX_W = 28, 64      # 头像宽度区间（物理像素）


def _avatar_point(chat, index):
    """返回 (x, y, 行矩形)。头像是紧贴行边的窄色块。"""
    import pyatspi
    from . import chat as chatmod

    chatmod.open_chat(chat)
    time.sleep(0.4)
    lst = chatmod._node("list", chatmod.MESSAGE_LIST)
    items = chatmod._children(lst)
    if index < 0 or index >= len(items):
        return None, f"序号 {index} 超出当前消息列表（共 {len(items)} 项）"
    e = items[index].queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
    t = geometry.global_transform()
    if t is None:
        return None, "拿不到坐标映射"
    sx, ox, sy, oy = t
    X, Y = int(e.x * sx + ox), int(e.y * sy + oy)
    W, H = int(e.width * sx), int(e.height * sy)

    # 被滚出可视区的行点不到（点了会打到聊天头部等别处）——明确拒绝，
    # 不要让调用方以为是「这条消息没有发送者」。
    le = lst.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
    list_top = int(le.y * sy + oy)
    list_bottom = list_top + int(le.height * sy)
    if Y + 20 < list_top or Y + 20 > list_bottom:
        return None, "该消息已被滚出可视区域，无法点头像（请先读一次消息列表）"

    img, (wx, wy) = screen.capture_window()
    img = img.convert("RGB")
    lx, ly = X - wx, Y - wy
    bg = _background_of_row(img, lx, ly, W)
    for a, b in _content_runs(img, lx, ly, W, H, bg):
        w = b - a + 1
        # 头像：宽度像头像，且紧贴行的左边缘或右边缘
        if AVATAR_MIN_W <= w <= AVATAR_MAX_W and (a < 30 or b > W - 30):
            # 头像在**行的顶部**，不是垂直居中 —— 图片/视频这类高行的
            # 头像如果在 H//2 处就会点到内容上（实测图片消息取不到名字）。
            return (X + (a + b) // 2, Y + min(H // 2, 20), (X, Y, W, H)), ""
    # 兜底：收消息头像在左，自己的在右
    if selflog.is_own(chat, (items[index].name or "")):
        return (X + W - 26, Y + min(H // 2, 20), (X, Y, W, H)), ""
    return (X + 26, Y + min(H // 2, 20), (X, Y, W, H)), ""


@synchronized
def sender_of(chat, index):
    """取某条消息的发送者。返回 (ok, name, fields, detail)。

    代价：每查一条要开一次资料卡再关掉（约 1.5~2 秒），所以不适合对整屏消息
    批量调用 —— 它就是给「需要知道这条是谁发的」这种按需场景用的。
    """
    from . import chat as chatmod

    blocked = chatmod._popup_guard()
    if blocked:
        return False, "", [], blocked

    pt, why = _avatar_point(chat, index)
    if pt is None:
        return False, "", [], why or "找不到该消息"
    ax, ay, _row = pt

    before = {w["id"] for w in geometry.x11_windows()}
    inputmod.click_at(ax, ay)
    time.sleep(1.0)

    # 资料卡是**新出现的窗口**，只在它的矩形范围内找文本，
    # 这样不会把主界面或别的浮层上的文字误当成昵称。
    card = None
    deadline = time.time() + 2.0
    while time.time() < deadline and card is None:
        for w in geometry.x11_windows():
            if w["id"] not in before:
                card = w
                break
        if card is None:
            time.sleep(0.2)

    name, fields = "", []
    if card is not None:
        import pyatspi
        rx, ry, rw, rh = card["x"], card["y"], card["w"], card["h"]
        hits = []

        def walk(n, depth=0):
            if depth > 60:
                return
            try:
                nm = (n.name or "").strip()
                if nm and n.childCount == 0:
                    e2 = n.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                    if rx <= e2.x <= rx + rw and ry <= e2.y <= ry + rh:
                        hits.append((e2.y, e2.x, nm))
            except Exception:
                pass
            try:
                for i in range(n.childCount):
                    walk(n.getChildAtIndex(i), depth + 1)
            except Exception:
                pass

        desc = pyatspi.Registry.getDesktop(0)
        for i in range(desc.childCount):
            try:
                walk(desc.getChildAtIndex(i))
            except Exception:
                pass
        hits.sort(key=lambda h: (h[0], h[1]))
        name = _pick_nickname(hits)
        seen = {name}
        for _y, _x, txt in hits:
            t = (txt or "").strip()
            if t and t not in seen and len(fields) < 12:
                seen.add(t)
                fields.append(t)

    inputmod.tap_key("Escape")          # 关掉资料卡，恢复原状
    time.sleep(0.4)
    if not name:
        return False, "", [], "资料卡里没读到名字（可能不是群聊或头像定位失败）"
    return True, name, fields, ""
