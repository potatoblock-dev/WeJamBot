"""弹出菜单的「自适应」定位。

实测结论：微信的**会话列表右键菜单不在无障碍树里**（消息右键菜单在）。
所以只能靠像素找菜单项。

这里能 100% 自适应的部分：
    * **行位置** —— 扫菜单窗口自身内容里的文字行，不含任何固定偏移。
      菜单在屏幕哪个位置、多高、几项，全都不需要预先知道。
    * **危险行** —— 「删除」是红色的。红色是像素能给出的唯一语义，
      拿它当安全带：标红的行默认拒绝点击。

做不到 100% 的部分：
    * **行身份** —— 哪一行是「独立窗口显示」不知道。菜单不可读、又不做 OCR，
      就只能依赖**条目顺序**，而顺序会变：
        - 「置顶」会变成「取消置顶」
        - 「消息免打扰」会变成「取消消息免打扰」
        - 「删除」只在有权限时出现
      我上次就是靠数行号点偏了一行，把会话「不显示」掉了。

所以定位策略是「**位置自适应 + 顺序假设 + 点后验证**」，
而不是假装能认字。想真正认字只有两条路：OCR，或者菜单暴露到无障碍树。
"""
import time

from . import geometry, screen
from .ui import synchronized

# 一行文字至少要有这么多「与背景不同」的像素，才算有文字。
# 不能用「深色」判定：红色「删除」的 RGB 和是 390，会被当成非文字漏掉。
MIN_TEXT_PIXELS = 3
MIN_LINE_HEIGHT = 6
BG_DIFF = 45


def _row_is_red(img, x0, x1, y0, y1, bg):
    """该文字行是否以红色为主（微信的「删除」是红的）。"""
    red = other = 0
    for y in range(y0, y1 + 1):
        for x in range(x0, x1, 2):
            p = img.getpixel((x, y))
            if sum(abs(a - b) for a, b in zip(p, bg)) <= BG_DIFF:
                continue
            r, g, b = p
            if r > 120 and r - max(g, b) > 40:
                red += 1
            else:
                other += 1
    return red > 0 and red >= other


@synchronized
def menu_rows(win=None, x_pad_left=8, x_pad_right=6):
    """列出弹出菜单里的文字行。

    返回 [{"y": 行中心, "x": 左边界, "dangerous": 是否红色行}, ...]，
    以及菜单窗口矩形。找不到菜单时返回 ([], None)。

    行位置全部由像素推出，与屏幕坐标无关；配合点击时用窗口矩形换算，
    菜单挪到哪都能用。
    """
    if win is None:
        wins = geometry.x11_windows()
        # 弹出菜单：可见窗口里最小的那几个之一，且不是主窗口
        if len(wins) <= 1:
            return [], None
        wechat = [w for w in wins if (w.get("cls") or "") == "wechat"]
        main = max(wechat or wins, key=lambda w: w["w"] * w["h"])
        cands = [w for w in wins if w["id"] != main["id"]]
        if not cands:
            return [], None
        win = min(cands, key=lambda w: w["w"] * w["h"])

    # 抓**菜单窗口自己**：Composite 会把菜单从主窗口内容里排除掉
    img, (ox, oy) = screen.capture_window(win)
    img = img.convert("RGB")

    # 菜单窗口在「窗口捕获」坐标系里的位置
    mx0, my0 = win["x"] - ox, win["y"] - oy
    mx1, my1 = mx0 + win["w"], my0 + win["h"]
    mx0, my0 = max(0, mx0), max(0, my0)
    mx1 = min(img.width, mx1)
    my1 = min(img.height, my1)
    if mx1 - mx0 < 20 or my1 - my0 < 20:
        return [], win

    # 背景色 = 菜单里出现最多的颜色
    from collections import Counter
    bg = Counter(img.getpixel((x, y))
                 for y in range(my0, my1, 2)
                 for x in range(mx0 + 2, mx1 - 2, 3)).most_common(1)[0][0]

    xs = range(mx0 + x_pad_left, mx1 - x_pad_right)

    def is_text(p):
        return sum(abs(a - b) for a, b in zip(p, bg)) > BG_DIFF

    raw = []
    for y in range(my0, my1):
        raw.append((y, sum(1 for x in xs if is_text(img.getpixel((x, y))))))

    rows, start = [], None
    for y, dark in raw:
        if dark >= MIN_TEXT_PIXELS and start is None:
            start = y
        elif dark < MIN_TEXT_PIXELS and start is not None:
            if y - start >= MIN_LINE_HEIGHT:
                rows.append((start, y - 1))
            start = None
    if start is not None and raw and raw[-1][0] - start >= MIN_LINE_HEIGHT:
        rows.append((start, raw[-1][0]))

    out = []
    for y0, y1 in rows:
        out.append({
            # 转回 root 坐标：capture_window 给的是窗口局部坐标系
            "y": (y0 + y1) // 2 + oy,
            "x": mx0 + x_pad_left + ox,
            "dangerous": _row_is_red(img, mx0 + x_pad_left, mx1 - x_pad_right,
                                     y0, y1, bg),
        })
    return out, win


def click_row(row):
    """点菜单里的某一行。row 里的坐标已是 root 坐标系。"""
    from . import input as inputmod
    if row.get("dangerous"):
        return False
    inputmod.click_at(row["x"] + 20, row["y"])
    time.sleep(0.6)
    return True
