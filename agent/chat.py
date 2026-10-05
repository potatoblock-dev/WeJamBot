"""会话与消息：把无障碍树解析成结构化数据（服务内部实现）。

界面里的两个关键容器（都是按名字定位，不靠坐标、不靠索引）：
    [list] '会话'   -> 每个 [list item] 是一个会话
    [list] '消息'   -> 每个 [list item] 要么是时间分隔，要么是一条消息

会话项的文本形如（\\n 分隔）：
    文件传输助手\\n剪贴板方案验证：…\\n13:32\\n
    1145\\n已置顶\\n[10条] \\n杨依菁 11.16: 国企单位坐落于此\\n15:12\\n
    土豆方块纯生存服\\n已置顶\\n\\n\\n消息免打扰\\n
所以解析要能容忍空行（空行本身表示「没有预览」）。
"""
import hashlib
import re
import time

from . import geometry, input as inputmod
from . import selflog
from .ui import synchronized

TIME_RE = re.compile(r"^\s*(\d{1,2}:\d{2}|昨天|星期[一二三四五六日天]|\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月\d{1,2}日)\s*$")
UNREAD_RE = re.compile(r"\[(\d+)条\]")
FLAG_PINNED = "已置顶"
FLAG_MUTED = "消息免打扰"

CHAT_LIST = "会话"
MESSAGE_LIST = "消息"
SEARCH_BOX = "搜索"
NAV_CHAT_TAB = "微信"   # 左侧导航的第一个页签，会话列表只在它下面

# 每个会话上一次观测到的消息条数，用于判断列表是否还在渲染
_last_count = {}


# ---------------------------------------------------------------------------
# 底层：按角色+名字取节点
# ---------------------------------------------------------------------------
def _node(role, name_needle, want_visible=True):
    node, _frame = geometry.find_node(role, name_needle, want_visible)
    return node


def _children(node):
    out = []
    if node is None:
        return out
    try:
        for i in range(node.childCount):
            out.append(node.getChildAtIndex(i))
    except Exception:
        pass
    return out


def _item_text(node):
    """list item 的文本：name 和 Text 接口都可能承载，取信息量大的那个。"""
    from .accessibility import text_of
    name = ""
    try:
        name = node.name or ""
    except Exception:
        pass
    body = text_of(node)
    return body if len(body) > len(name) else name


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
def parse_chat_item(raw):
    parts = [p.strip() for p in raw.split("\n")]
    while parts and not parts[-1]:
        parts.pop()
    if not parts:
        return None
    name = parts[0]
    rest = parts[1:]

    pinned = any(FLAG_PINNED in p for p in parts)
    muted = any(FLAG_MUTED in p for p in parts)

    unread = 0
    time_text = ""
    preview_lines = []
    for p in rest:
        if not p or p in (FLAG_PINNED, FLAG_MUTED):
            continue
        m = UNREAD_RE.search(p)
        if m:
            unread = int(m.group(1))
            p = UNREAD_RE.sub("", p).strip()
            if not p:
                continue
        if TIME_RE.match(p) and not time_text:
            time_text = p.strip()
            continue
        preview_lines.append(p)

    return {
        "name": name,
        "last_message": " ".join(preview_lines).strip(),
        "last_time": time_text,
        "unread_count": unread,
        "pinned": pinned,
        "muted": muted,
    }


@synchronized
def list_chats():
    """会话列表。

    注意：会话列表只在「微信」页签下才存在。如果用户（或上一次调用）
    切到了通讯录/收藏等页面，这里会**自动切回微信页**再读，
    而不是静默返回空列表 —— 静默返回空是排查成本最高的一类错误。
    """
    container = _node("list", CHAT_LIST)
    if container is None:
        if not _activate_chat_tab():
            return []
        container = _node("list", CHAT_LIST)
        if container is None:
            return []

    chats = []
    for it in _children(container):
        try:
            if it.getRoleName() != "list item":
                continue
        except Exception:
            continue
        parsed = parse_chat_item(_item_text(it))
        if parsed:
            chats.append(parsed)
    return chats


def _activate_chat_tab():
    """切回「微信」页签（会话列表只在它下面）。返回是否成功。"""
    rect = geometry.node_physical_rect("push button", NAV_CHAT_TAB)
    if rect is None:
        return False
    x, y, w, h = rect
    inputmod.click_at(x + w // 2, y + h // 2)
    time.sleep(0.6)
    return True


# ---------------------------------------------------------------------------
# 内容类型：微信把非文本消息渲染成**带标记的占位文本**，据此判定类型。
# 消息列表里有些是纯占位（没有方括号），所以做全等匹配，
# 避免把用户真的打出「图片」两个字误判成图片消息。
# ---------------------------------------------------------------------------
CONTENT_MARKERS = (
    ("[图片]", "image"), ("[视频]", "video"), ("[文件]", "file"),
    ("[语音]", "voice"), ("[位置]", "location"), ("[链接]", "link"),
    ("[音乐]", "music"), ("[动画表情]", "emotion"), ("[表情]", "emotion"),
    ("[合并转发]", "merge"), ("[聊天记录]", "chatrecord"),
    ("[个人名片]", "card"), ("[名片]", "card"), ("[笔记]", "note"),
    ("[小程序]", "miniapp"), ("[转账]", "transfer"), ("[红包]", "redpacket"),
    ("[引用]", "quote"), ("[视频号]", "channels"), ("[接龙]", "solitaire"),
    ("[直播]", "live"),
)

PLAIN_CONTENT = (
    ("动画表情", "emotion"), ("图片", "image"), ("视频", "video"),
    ("语音", "voice"), ("位置", "location"), ("文件", "file"),
    ("音乐", "music"), ("链接", "link"), ("名片", "card"), ("笔记", "note"),
)


def classify_content(text):
    """返回内容类型字符串（默认 text）。"""
    t = (text or "").strip()
    for mark, kind in CONTENT_MARKERS:
        if t.startswith(mark):
            return kind
    for mark, kind in PLAIN_CONTENT:
        if t == mark:
            return kind
    return "text"


# ---------------------------------------------------------------------------
# 消息
# ---------------------------------------------------------------------------
@synchronized
def current_chat():
    """当前打开的会话名：输入框是「可编辑文本节点」且其名字就是会话名。

    用 find_first 提前退出，避免为此展开整棵树 —— walk_all 一次要几百毫秒，
    而这里只关心一个节点。
    """
    from .accessibility import find_first

    def is_input(n):
        return (n.getRoleName() == "text"
                and "editable" in _states(n)
                and (n.name or "") not in ("", SEARCH_BOX))

    node = find_first(is_input)
    return (node.name or "") if node is not None else ""


def _states(node):
    from .accessibility import state_flags
    return state_flags(node)


def _message_items():
    container = _node("list", MESSAGE_LIST)
    out = []
    for it in _children(container):
        try:
            if it.getRoleName() != "list item":
                continue
        except Exception:
            continue
        out.append(_item_text(it))
    return out


@synchronized
def list_messages(chat="", limit=0):
    """解析指定会话的消息列表。

    若指定了 chat 且它不是当前打开的会话，会先打开它 —— 微信的界面
    一次只渲染一个会话，不先打开就读不到。这是读取的必要前置动作。
    """
    if chat and chat != current_chat():
        ok, detail = open_chat(chat)
        if not ok:
            # 绝不能忽略失败：否则会把「当前会话」的消息贴上目标会话名返回，
            # 调用方据此操作就可能在错误的会话里发消息。
            raise RuntimeError(f"打开会话 {chat!r} 失败：{detail}")
    chat = chat or current_chat()
    items = _wait_message_list()
    # index 必须是**绝对位置**：它会用于后续的消息级操作（右键复制等），
    # 而 bubble_rect 按绝对位置取节点。这里若用截断后的序号，limit 一小于
    # 总数就会错位 —— 表现为「操作到了另一条消息」，实测踩到过。
    offset = 0
    if limit and len(items) > limit:
        offset = len(items) - limit
        items = items[offset:]

    messages = []
    bucket = ""          # 最近一个时间分隔，作为消息的时间归属
    seen_in_bucket = {}  # 同一时间桶内同文消息的去重计数
    for i, raw in enumerate(items):
        idx = offset + i
        raw = raw.replace("\u00a0", " ")
        if not raw.strip():
            continue
        if TIME_RE.match(raw) and "\n" not in raw:
            kind = "TIME"
            text, sender = raw.strip(), ""
            bucket = text
        elif raw.strip() in ("以下为新消息",) or raw.strip().startswith("你撤回"):
            kind = "SYSTEM"
            text, sender = raw.strip(), ""
        else:
            kind = "TEXT"
            sender, text = "", raw.strip()
            # 群聊里 item 常形如 "发送者\n正文"
            if "\n" in raw:
                first, rest = raw.split("\n", 1)
                if 0 < len(first.strip()) <= 24 and rest.strip():
                    sender, text = first.strip(), rest.strip()
        # ID 刻意不用序号：序号会随列表滚动而漂移，导致「全部变成新消息」。
        # 用 会话+时间桶+正文+桶内出现次数，窗口不变时保持稳定。
        key = (bucket, text)
        n = seen_in_bucket.get(key, 0)
        seen_in_bucket[key] = n + 1
        mid = hashlib.sha1(f"{chat}|{bucket}|{text}|{n}".encode()).hexdigest()[:16]
        messages.append({
            "id": mid, "chat": chat, "sender": sender, "text": text,
            "time_text": raw.strip() if kind == "TIME" else "",
            "kind": kind, "index": idx,
            "content_type": classify_content(text) if kind == "TEXT" else "system",
        })
    return chat, messages


# 群聊头部形如 "名称(14)"；单聊只有名称
HEADER_MEMBER_RE = re.compile(r"^(.+?)\((\d+)\)$")


def chat_header_info(chat):
    """当前会话的类型与成员数。

    群聊头部会显示「名称(成员数)」—— 这是白拿的数据，之前一直没取。
    非群聊没有这个后缀，返回 chat_type='single'、member_count=0。
    """
    info = {"chat_type": "single", "member_count": 0}
    try:
        from .accessibility import walk_all
        for node in walk_all(only_visible=True):
            nm = (node.get("name") or "").strip()
            m = HEADER_MEMBER_RE.match(nm)
            if m and m.group(1).strip() == (chat or "").strip():
                info["member_count"] = int(m.group(2))
                info["chat_type"] = "group"
                return info
    except Exception:
        pass
    # 公众号会话名是服务号名，无法从头部区分；这里如实返回 single
    return info


# ---------------------------------------------------------------------------
# Escape 是**危险键**
#
# 实测：在微信主窗口上按 Escape 会弹出「退出登录后将无法收到新消息，确定退出登录？」
# 确认框，再按一次又收起 —— 它是个开关。整场调试中反复冒出来的退出登录框，
# 根因就是各处用 Escape 关弹层。
#
# 所以按 Escape 之后必须立刻检查并点「取消」，绝不能假定它只是「关闭」。
# ---------------------------------------------------------------------------
LOGOUT_W, LOGOUT_H = 294, 177      # 退出登录确认框的窗口尺寸（实测）


def logout_dialog_open():
    """退出登录确认框是否开着。"""
    for w in geometry.x11_windows():
        if abs(w["w"] - LOGOUT_W) <= 6 and abs(w["h"] - LOGOUT_H) <= 6:
            return True
    return False


def dismiss_logout_dialog():
    """点「取消」关掉退出登录确认框。返回是否关掉了。"""
    if not logout_dialog_open():
        return False
    r = geometry.node_physical_rect("push button", "取消")
    if r is None:
        return False
    inputmod.click_at(r[0] + r[2] // 2, r[1] + r[3] // 2)
    time.sleep(0.4)
    return not logout_dialog_open()


def safe_escape():
    """按 Escape 关弹层，但兜住它可能触发的退出登录框。"""
    inputmod.tap_key("Escape")
    time.sleep(0.45)
    if logout_dialog_open():
        dismiss_logout_dialog()


POPUP_MIN_AREA = 120 * 80      # 弹出层判据：与主窗口不同的可见窗口

def _foreign_windows():
    """除主窗口外的可见 X11 窗口（菜单 / 对话框 / 设置面板等）。

    主窗口按 **WM_CLASS == 'wechat' 里最大的那个** 来认，不能只按面积取最大：
    搜一搜窗口（class 为空）可能比主窗口还大，那样主窗口会被当成外来弹层，
    结果任何操作都拒绝执行 —— 实测踩到过。
    """
    try:
        wins = geometry.x11_windows()
    except Exception:
        return []
    if len(wins) <= 1:
        return []
    wechat = [w for w in wins if (w.get("cls") or "") == "wechat"]
    pool = wechat or wins
    main = max(pool, key=lambda w: w["w"] * w["h"])
    return [w for w in wins
            if w["id"] != main["id"] and w["w"] * w["h"] >= POPUP_MIN_AREA]


def _popup_guard():
    """确保界面处于「只有主窗口」的已知状态，否则返回错误说明。

    为什么必须这样：点击坐标是按无障碍节点算的，但**弹层是独立窗口且盖在上面**，
    X 会把点击投递给最顶层窗口 —— 于是「点会话列表」会打到弹层上的某个位置。
    实测就是这样从设置面板里点到过「退出登录」。
    """
    extra = _foreign_windows()
    if not extra:
        return None
    for _ in range(2):                       # 先试着用 Esc 关掉
        safe_escape()                        # Escape 会开关「退出登录」，必须兜住
        extra = _foreign_windows()
        if not extra:
            return None
    labels = ", ".join(f"0x{w['id']:x}({w['w']}x{w['h']})" for w in extra[:3])
    return f"界面有挡路弹层，已放弃操作：{labels}"


def _modal_present():
    """界面上是否有挡路的模态确认框。

    有弹窗时继续点击/输入会把操作打到弹窗上 —— 轻则无效，重则误触
    （实测遇到过「确定退出登录？」）。所以宁可直接失败并如实报告。
    """
    try:
        from .accessibility import walk_all
        names = {n["name"] for n in walk_all(only_visible=True)}
        return "确定" in names and "取消" in names
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 动作
# ---------------------------------------------------------------------------
@synchronized
def open_chat(name):
    """点开指定会话。返回 (ok, detail)。"""
    blocked = _popup_guard()
    if blocked:
        return False, blocked
    if name and name == current_chat():
        return True, "已经是当前会话"
    container = _node("list", CHAT_LIST)
    for it in _children(container):
        try:
            if it.getRoleName() != "list item":
                continue
        except Exception:
            continue
        parsed = parse_chat_item(_item_text(it))
        if parsed and parsed["name"] == name:
            rect = geometry.node_physical_rect("list item", name)
            if rect is None:
                continue
            x, y, w, h = rect
            inputmod.click_at(x + w // 2, y + h // 2)
            time.sleep(0.6)
            return True, f"已打开 {name}"
    return False, f"未找到会话 {name!r}"


@synchronized
def input_box_node():
    """消息输入框节点：可编辑文本节点，且名字不是搜索框。

    微信把「当前会话名」作为输入框的 a11y name，搜索框则固定叫「搜索」。
    """
    name = current_chat()
    if not name:
        return None
    node, _frame = geometry.find_node("text", name)
    return node


@synchronized
def input_text():
    """输入框里当前的内容（用于发送后校验）。"""
    from .accessibility import text_of
    node = input_box_node()
    return "" if node is None else text_of(node)


@synchronized
def send_text(chat, text):
    """发送文本，并**校验是否真的发出去了**。

    微信按回车发送失败时不会有任何提示（例如焦点丢失、回车被吞），
    所以不能发完就当成功：回车后检查输入框是否已清空，没清空就重试一次。
    """
    blocked = _popup_guard()
    if blocked:
        return False, blocked

    if chat:
        ok, detail = open_chat(chat)
        if not ok:
            return False, detail

    target = chat or current_chat()

    # 定位输入框并聚焦（用比例映射，无静态坐标）
    rect = geometry.node_physical_rect("text", target)
    if rect is None:
        return False, "找不到输入框"
    x, y, w, h = rect
    inputmod.click_at(x + w // 2, y + h // 2)
    time.sleep(0.3)

    # 打字是逐字符注入的，快的时候可能**静默丢字**（实测全零延迟时丢过）。
    # 所以打完必须回读输入框比对，不符就清空重来一次。
    typed = ""
    for attempt in range(2):
        inputmod.type_text(text)
        time.sleep(0.35)
        typed = input_text()
        if typed.strip() == text.strip():
            break
        for _ in range(len(typed) + 8):
            inputmod.tap_key("BackSpace")
        time.sleep(0.3)
    else:
        return False, f"输入框内容与预期不符（第 {attempt+1} 次）：{typed[:40]!r}"
    time.sleep(0.3)

    for attempt in (1, 2):
        inputmod.tap_key("Return")
        # 给界面一点时间提交
        for _ in range(6):
            time.sleep(0.3)
            if not input_text().strip():
                selflog.remember(target, text)   # 供监听端溯源「这是自己发的」
                return True, f"已发送 {len(text)} 个字符到 {target}"

    return False, f"消息似乎没有发出（输入框仍有内容），目标 {target}"


@synchronized
def _wait_message_list(timeout=6.0, settle=2):
    """等消息列表渲染稳定后再解析。

    刚打开会话时列表是渐进渲染的，立刻解析会读到半截 ——
    实测中这会让订阅者把历史消息当成新消息。

    优化：记住每个会话上一次的条数，稳态下第一次读就能判定「没在变」，
    省掉重复的整树遍历（每次遍历要几百毫秒）。
    """
    chat = current_chat()
    last = None
    stable = 0
    t0 = time.time()
    while time.time() - t0 < timeout:
        items = _message_items()
        if last is None and _last_count.get(chat) == len(items):
            # 与上次观测一致，说明没在渲染
            _last_count[chat] = len(items)
            return items
        if len(items) == last:
            stable += 1
            if stable >= settle:
                _last_count[chat] = len(items)
                return items
        else:
            stable = 0
            last = len(items)
        time.sleep(0.25)
    items = _message_items()
    _last_count[chat] = len(items)
    return items
