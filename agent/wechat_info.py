"""微信版本探测（服务内部实现）。

优先级（从「语义来源」到「反工程兜底」）：
  1. dpkg   —— 镜像从官方 .deb 安装时最准，且零副作用。
  2. GUI    —— 打开「设置 → 关于微信」，版本号是界面上的一个 label，直接读。
               这是首选语义来源：不猜、不扫二进制，界面写什么就是什么。
               读完会把面板关掉还原界面。
  3. 进程/二进制 —— 仅作最后兜底（二进制里会混入 3.442.175.867 这类伪版本串，
               所以判据要三重过滤，不到万不得已不依赖它）。

结果带 TTL 缓存：GUI 探测会开关面板，不能每次调用都做。
"""
import os
import re
import subprocess
import threading

from .ui import UI_LOCK
import time

_TTL = 60.0          # dpkg/二进制类来源的缓存
_GUI_TTL = 3600.0    # GUI 探测会动界面，缓存久一点
_cache = {"at": 0.0, "value": "", "source": ""}
_ui_lock = UI_LOCK

# 同时喂给 Python re 和 GNU grep -E，所以用 [0-9] 而非 \d
VER_RE = re.compile(r"\b([1-9][0-9]?)\.([0-9]{1,2})\.([0-9]{1,3})\.([0-9]{1,5})\b")
PLAIN_VER_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?")


# ---------------------------------------------------------------------------
# 1. dpkg
# ---------------------------------------------------------------------------
def _from_dpkg():
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f=${Version}", "wechat"],
                             capture_output=True, text=True, timeout=10)
        v = out.stdout.strip()
        if v and VER_RE.fullmatch(v):
            return v
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# 2. GUI：设置 → 关于微信
# ---------------------------------------------------------------------------
def _visible_texts():
    from .accessibility import walk_all
    out = []
    for n in walk_all(only_visible=True):
        for t in (n["name"], n["text"]):
            if t and t.strip():
                out.append(t.strip())
    return out


def _read_about_panel():
    ts = _visible_texts()
    if "版本信息" not in ts:
        return ""
    for t in ts:
        if PLAIN_VER_RE.fullmatch(t):
            return t
    return ""


# 这些按钮一旦误点会造成不可逆后果，一律拒绝点击
DANGEROUS = ("退出", "注销", "清空", "删除", "撤回", "解散")


def _click(role, name):
    from . import geometry
    from . import input as inputmod
    if any(bad in name for bad in DANGEROUS):
        return False
    rect = geometry.node_physical_rect(role, name)
    if not rect:
        return False
    x, y, w, h = rect
    inputmod.click_at(x + w // 2, y + h // 2)
    return True


def _clear_confirm_dialogs(max_rounds=3):
    """清掉挡路的模态确认框（例如「确定退出登录？」）。

    自动化工具必须能应付意外弹窗：一次误弹就会让后续所有点击失效，
    而工具却浑然不觉 —— 实测中就踩到过这个坑。
    """
    from . import input as inputmod
    for _ in range(max_rounds):
        ts = _visible_texts()
        modal = ("确定" in ts and "取消" in ts
                 and not any("关于微信" in t for t in ts))
        if not modal:
            return
        if not _click("push button", "取消"):
            inputmod.tap_key("Escape")
        time.sleep(0.8)


def _from_gui():
    """打开设置面板读版本，读完还原界面。"""
    from . import input as inputmod
    with _ui_lock:
        try:
            v = _read_about_panel()
            if v:
                return v

            _clear_confirm_dialogs()

            ts = _visible_texts()
            in_settings = any("关于微信" in t for t in ts)
            if not in_settings:
                # 「更多」是开关：已开再点会关掉，所以先判断
                if not any("设置" == t for t in ts):
                    if not _click("push button", "更多"):
                        return ""
                    time.sleep(1.0)
                _click("push button", "设置")
                time.sleep(1.5)

            # 面板可能因上一次异常而错位，再清一次弹窗
            _clear_confirm_dialogs()
            if not _click("push button", "关于微信"):
                return ""
            time.sleep(1.5)
            v = _read_about_panel()

            # 还原：关掉关于面板与设置面板
            for _ in range(2):
                inputmod.tap_key("Escape")
                time.sleep(0.5)
            _clear_confirm_dialogs()
            return v
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# 3. 进程参数兜底（微信没起来、又没走包管理时）
# ---------------------------------------------------------------------------
def _from_process():
    try:
        out = subprocess.run(
            ["bash", "-c",
             "cat /proc/[0-9]*/cmdline 2>/dev/null | tr '\\0' '\\n' "
             "| grep -aoE '\"version\":\"[0-9]+\"' | sort -u | head -1"],
            capture_output=True, text=True, timeout=15)
        m = re.search(r'"version":"(\d+)"', out.stdout)
        if m:
            return f"client_version={m.group(1)}"
    except Exception:
        pass
    return ""


def detect(force=False):
    """返回 (版本字符串, 来源)。

    默认**只走无副作用的来源**（dpkg / 进程参数）。
    GUI 探测会开关设置面板、点击界面控件，属于自主导航 ——
    实测中出现过弹出「退出登录」确认框的情况。虽然不确定是哪一步点偏的，
    但结论很明确：读个版本号不值得让服务去动设置面板。

    因此 GUI 路径默认关闭，仅在显式设置 WEJAM_VERSION_VIA_GUI=1 时启用。
    """
    now = time.time()
    if not force and _cache["value"] and now - _cache["at"] < _TTL:
        return _cache["value"], _cache["source"]

    sources = [("dpkg", _from_dpkg), ("process", _from_process)]
    if os.environ.get("WEJAM_VERSION_VIA_GUI") == "1":
        sources.append(("gui", _from_gui))

    for source, fn in sources:
        try:
            v = fn()
        except Exception:
            v = ""
        if v:
            # GUI 来源给长缓存：它要开关面板，不该频繁重探
            _cache.update(at=now + (_GUI_TTL if source == "gui" else 0.0),
                          value=v, source=source)
            return v, source

    _cache.update(at=now, value="", source="")
    return "", ""
