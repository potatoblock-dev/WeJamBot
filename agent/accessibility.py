"""无障碍树的读取与语义分类（服务内部实现）。"""
import pyatspi
from .ui import synchronized

MAX_DEPTH = 45


def visible(node):
    try:
        st = node.getState()
        return (st.contains(pyatspi.STATE_VISIBLE)
                and st.contains(pyatspi.STATE_SHOWING))
    except Exception:
        return False


def state_flags(node):
    try:
        st = node.getState()
    except Exception:
        return []
    flags = []
    for name, bit in (("visible", pyatspi.STATE_VISIBLE),
                      ("showing", pyatspi.STATE_SHOWING),
                      ("enabled", pyatspi.STATE_ENABLED),
                      ("focused", pyatspi.STATE_FOCUSED),
                      ("selected", pyatspi.STATE_SELECTED),
                      ("editable", pyatspi.STATE_EDITABLE)):
        try:
            if st.contains(bit):
                flags.append(name)
        except Exception:
            pass
    return flags


def text_of(node):
    try:
        t = node.queryText()
        n = t.characterCount
        if n > 0:
            s = t.getText(0, n)
            if s and s.strip():
                return s
    except Exception:
        pass
    return ""


def actions_of(node):
    out = []
    try:
        a = node.queryAction()
        for i in range(a.nActions):
            out.append(a.getName(i))
    except Exception:
        pass
    return out


@synchronized
def find_first(predicate, max_depth=MAX_DEPTH):
    """找到第一个满足条件的节点就**立即返回**，不收集整棵树。

    对比 walk_all：后者每次要把全树展开（实测数百毫秒到 1 秒），
    而多数查询只需要一个节点（如「当前会话名」所在的输入框）。
    """
    result = []

    def rec(node, depth):
        if depth > max_depth or result:
            return
        try:
            if predicate(node):
                result.append(node)
                return
        except Exception:
            pass
        try:
            for i in range(node.childCount):
                rec(node.getChildAtIndex(i), depth + 1)
        except Exception:
            pass

    d = pyatspi.Registry.getDesktop(0)
    for i in range(d.childCount):
        try:
            rec(d.getChildAtIndex(i), 0)
        except Exception:
            pass
    return result[0] if result else None


@synchronized
def walk_all(only_visible=True, max_depth=MAX_DEPTH):
    """遍历所有应用，产出节点字典列表。"""
    out = []

    def rec(node, depth):
        if depth > max_depth:
            return
        try:
            if (not only_visible) or visible(node):
                out.append({
                    "depth": depth,
                    "role": node.getRoleName() or "",
                    "name": node.name or "",
                    "text": text_of(node),
                    "states": state_flags(node),
                })
        except Exception:
            pass
        try:
            for i in range(node.childCount):
                rec(node.getChildAtIndex(i), depth + 1)
        except Exception:
            pass

    d = pyatspi.Registry.getDesktop(0)
    for i in range(d.childCount):
        try:
            rec(d.getChildAtIndex(i), 0)
        except Exception:
            pass
    return out
