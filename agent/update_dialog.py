"""识别微信「版本更新日志」弹层上的关闭按钮（纯数据，无 GUI 依赖）。

主窗口标题栏也有同名「关闭」，且在无障碍树里排在前面；点错会触发退出登录。
选按钮时必须：先看到版本标题，再取其后第一个矩形非空的「关闭」。
"""
import re

# 标题形如「微信 4.1.13」
UPDATE_TITLE_RE = re.compile(r"^微信\s+\d+\.\d+")


def pick_update_close_rect(nodes):
    """从按树序的 (role, name, rect) 里选出更新日志弹层的「关闭」矩形。

    rect 为 (x, y, w, h) 或 None；找不到可点目标时返回 None。
    """
    update_seen = False
    for role, name, rect in nodes:
        label = (name or "").strip()
        if role == "label" and UPDATE_TITLE_RE.match(label):
            update_seen = True
            continue
        if not (update_seen and role == "push button" and label == "关闭"):
            continue
        if rect and rect[2] > 0 and rect[3] > 0:
            return rect
    return None
