"""独立聊天窗口：把会话开进自己的窗口，避免「切换会话」的代价。

为什么值得做（实测）：
    主窗口里切换会话再读/发，要 **3~7 秒**（重新渲染 + 稳定期）；
    而会话已经在眼前时只要 **0.7~1 秒**。
    把常用会话常驻独立窗口，每次操作就省下这几秒。

能做什么、不能做什么：
    * **读**：可以同时读多个窗口 —— a11y 不依赖焦点
    * **写**：仍然必须串行 —— X11 只有**一个**键盘焦点。
      实测用 XSendEvent 想绕过焦点直接投递给目标窗口也不行：
      应用按自己内部的焦点状态路由，无视事件地址。

如何开：右键会话列表项 → 菜单里的「独立窗口显示」。
这个菜单**不在无障碍树里**，所以靠 menu.menu_rows 用像素找行
（行位置自适应，红色行「删除」自动识别并拒绝）。
菜单里危险项紧邻目标项，所以定位规则是「**从红色的删除往上数**」，
而不是数正数 —— 正数会因「置顶/取消置顶」而变化。
"""
import time

from . import geometry, menu
from .ui import synchronized

FRAME_MAIN = "微信"


def _all_frames():
    """返回 [(frame_name, frame_node)]。"""
    import pyatspi
    desc = pyatspi.Registry.getDesktop(0)
    out = []

    def walk(n, depth=0):
        if depth > 25:
            return
        try:
            if n.getRoleName() == "frame":
                out.append((n.name or "", n))
                return
        except Exception:
            pass
        try:
            for i in range(n.childCount):
                walk(n.getChildAtIndex(i), depth + 1)
        except Exception:
            pass

    for i in range(desc.childCount):
        try:
            walk(desc.getChildAtIndex(i))
        except Exception:
            pass
    return out


def list_open():
    """已开独立窗口的会话名列表（frame 名字就是会话名）。"""
    return [name for name, _ in _all_frames() if name and name != FRAME_MAIN]


def _find_in_frame(frame_node, role, name_needle):
    """在指定 frame 子树里找节点（by role + name 子串）。"""
    found = []

    def walk(n, depth=0):
        if depth > 45 or found:
            return
        try:
            if n.getRoleName() == role and name_needle in (n.name or ""):
                found.append(n)
                return
        except Exception:
            pass
        try:
            for i in range(n.childCount):
                walk(n.getChildAtIndex(i), depth + 1)
        except Exception:
            pass

    walk(frame_node)
    return found[0] if found else None


def message_list(chat):
    """独立窗口里该会话的消息列表节点；没有则 None。"""
    for name, node in _all_frames():
        if name == chat:
            return _find_in_frame(node, "list", "消息")
    return None


def input_box(chat):
    """独立窗口里该会话的输入框节点；没有则 None。"""
    for name, node in _all_frames():
        if name == chat:
            return _find_in_frame(node, "text", "")
    return None


# ---------------------------------------------------------------------------
# 打开 / 关闭
# ---------------------------------------------------------------------------
@synchronized
def open(chat):
    """把会话开进独立窗口。返回 (ok, detail)。"""
    from . import chat as chatmod

    if chat in list_open():
        return True, "已经在独立窗口里"

    blocked = chatmod._popup_guard()
    if blocked:
        return False, blocked

    lst = chatmod._node("list", chatmod.CHAT_LIST)
    if lst is None:
        return False, "拿不到会话列表"
    item = None
    for it in chatmod._children(lst):
        parsed = chatmod.parse_chat_item(chatmod._item_text(it))
        if parsed and parsed["name"] == chat:
            item = it
            break
    if item is None:
        return False, f"会话列表里没有 {chat!r}"

    import pyatspi
    e = item.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
    t = geometry.global_transform()
    if t is None:
        return False, "拿不到坐标映射"
    sx, ox, sy, oy = t
    px, py = int(e.x * sx + ox), int(e.y * sy + oy)
    pw, ph = int(e.width * sx), int(e.height * sy)

    wins_before = geometry.x11_windows()
    before = {w["id"] for w in wins_before}
    from . import input as inputmod

    # 先抬起主窗口：独立聊天窗口会盖住会话列表区域，
    # 不抬起来的话右键会被投递给盖在上面的独立窗口，菜单根本弹不出来。
    cands = [w for w in wins_before if (w.get("cls") or "") == "wechat"] or wins_before
    if cands:
        main = max(cands, key=lambda w: w["w"] * w["h"])
        inputmod.raise_window(main["id"])

    inputmod.click_at(px + pw // 2, py + ph // 2, button=3)
    time.sleep(1.4)

    rows, win = menu.menu_rows()
    if not rows:
        chatmod.safe_escape()
        return False, "右键菜单没弹出来"

    # **从红色的「删除」往上数**：倒数第 1 是删除，倒数第 2 是「不显示」，
    # 倒数第 3 才是「独立窗口显示」。不能数正数（置顶/免打扰会变）。
    danger = [i for i, r in enumerate(rows) if r["dangerous"]]
    if not danger:
        chatmod.safe_escape()
        return False, "菜单里没识别出「删除」（红色行），不敢盲点"
    idx = danger[-1] - 2
    if idx < 0:
        chatmod.safe_escape()
        return False, "菜单项数与预期不符，放弃"

    menu.click_row(rows[idx])
    time.sleep(2.2)

    after = geometry.x11_windows()
    if not any(w["id"] not in before for w in after):
        chatmod.safe_escape()
        return False, "点了但没出现新窗口"
    if chat not in list_open():
        return False, "新窗口出现了，但 frame 名字不是目标会话"

    # 立刻平铺，避免与主窗口重叠（重叠会导致点击打错窗口、资料卡串内容）
    arrange()
    return True, "已开独立窗口（已平铺）"


@synchronized
def close(chat):
    """关掉某会话的独立窗口（点它自己的关闭按钮，按窗口几何定位）。"""
    from . import input as inputmod
    for name, _node in _all_frames():
        if name != chat:
            continue
        for w in geometry.x11_windows():
            # 独立窗口比主窗口小，且不是菜单
            if w["w"] > 400 and w["id"] != 0x200011 and w["w"] < 900:
                inputmod.click_at(w["x"] + w["w"] - 12, w["y"] + 23)
                time.sleep(1.0)
                return (chat not in list_open()), "已关闭"
    return False, f"{chat!r} 没有独立窗口"


# ---------------------------------------------------------------------------
# 在独立窗口里操作（不碰主窗口，因此不需要切换会话）
# ---------------------------------------------------------------------------
@synchronized
def read_messages(chat, limit=0):
    """从独立窗口读消息，**不切换主窗口**。返回消息字典列表。"""
    from . import chat as chatmod
    node = message_list(chat)
    if node is None:
        return None
    texts = [chatmod._item_text(node.getChildAtIndex(i))
             for i in range(node.childCount)]
    parsed = chatmod._parse_messages(chat, texts)
    if limit:
        parsed = parsed[-limit:]
    return parsed


@synchronized
def arrange(main_ratio=0.52):
    """把主窗口与所有独立聊天窗口**横向平铺**，互不重叠。

    为什么必须平铺（实测结论）：窗口一重叠就出两类问题 ——
      * 点击被投递给**最顶层窗口**：点「独立窗口里的头像」可能落到盖在上面的
        主窗口的会话列表上（会莫名打开别的会话）；
      * 资料卡的矩形会覆盖别的窗口，按矩形取文本时会读到**被压住的列表正文**
        （实测把消息正文「动画表情」和别的会话名当成了昵称）。

    虚拟桌面的尺寸和窗口位置都是我们自己定的，没理由让它们叠在一起。
    """
    from Xlib import X, display

    wins = geometry.x11_windows()
    if len(wins) <= 1:
        return []
    wechat = [w for w in wins if (w.get("cls") or "") == "wechat"] or wins
    main = max(wechat, key=lambda w: w["w"] * w["h"])
    chats = [w for w in wins if w["id"] != main["id"]]

    d = display.Display()
    sw = d.screen().width_in_pixels
    sh = d.screen().height_in_pixels

    n = len(chats)
    main_w = int(sw * main_ratio) if n else sw
    each = (sw - main_w) // n if n else 0

    def place(wid, x, y, w, h):
        obj = d.create_resource_object("window", wid)
        obj.configure(x=int(x), y=int(y), width=int(w), height=int(h),
                      stack_mode=X.Above)
        d.sync()

    place(main["id"], 0, 0, main_w, sh)
    time.sleep(0.35)
    out = [(main["id"], 0, 0, main_w, sh)]
    for i, w in enumerate(chats):
        x = main_w + i * each
        place(w["id"], x, 0, each, sh)
        time.sleep(0.35)
        out.append((w["id"], x, 0, each, sh))
    return out
