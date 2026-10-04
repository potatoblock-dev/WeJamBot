"""登录状态机 —— 由服务持有，客户端只消费。

职责边界（这是本模块存在的意义）：
  - 轮询界面状态（读无障碍树，不截图）
  - 维护二维码内容与指纹（内容变了 = 微信刷新了码）
  - 在「记住账号」界面自动推进登录
  - 把状态变化推给订阅者

客户端拿到的是 LoginPhase + 二维码内容，看不到 a11y / zbar / XTEST 任何一个词。
"""
import hashlib
import threading
import time
from collections import deque

from . import accessibility, screen
from .ui import synchronized

# 语义标记：靠界面上的文案识别状态，不依赖窗口尺寸或坐标
MARK_WAIT_PHONE = ("需在手机上完成登录",)
MARK_ONE_CLICK = ("当前登录用户", "进入微信")
MARK_QR = ("扫码登录", "二维码")
MARK_LOGGED_IN = ("搜索", "通讯录", "聊天")

PHASE_UNSPECIFIED = 0
PHASE_STARTING = 1
PHASE_QR_READY = 2
PHASE_ONE_CLICK_READY = 3
PHASE_WAIT_PHONE = 4
PHASE_LOGGED_IN = 5
PHASE_UNKNOWN = 6

PHASE_NAMES = {
    PHASE_UNSPECIFIED: "UNSPECIFIED",
    PHASE_STARTING: "STARTING",
    PHASE_QR_READY: "QR_READY",
    PHASE_ONE_CLICK_READY: "ONE_CLICK_READY",
    PHASE_WAIT_PHONE: "WAIT_PHONE",
    PHASE_LOGGED_IN: "LOGGED_IN",
    PHASE_UNKNOWN: "UNKNOWN",
}


def classify(nodes):
    """把节点列表分类成 (phase, markers)。"""
    names = [n["name"] for n in nodes if n["name"].strip()]
    bodies = [n["text"] for n in nodes if n["text"].strip()]
    pool = names + bodies

    def has(mark):
        return any(mark in s for s in pool)

    if any(has(m) for m in MARK_WAIT_PHONE):
        return PHASE_WAIT_PHONE, [m for m in MARK_WAIT_PHONE if has(m)]
    if any(has(m) for m in MARK_ONE_CLICK) or ("登录" in names and "切换账号" in names):
        hit = [m for m in MARK_ONE_CLICK if has(m)] or ["登录/切换账号"]
        return PHASE_ONE_CLICK_READY, hit
    if any(has(m) for m in MARK_QR):
        return PHASE_QR_READY, [m for m in MARK_QR if has(m)]
    if any(has(m) for m in MARK_LOGGED_IN):
        return PHASE_LOGGED_IN, [m for m in MARK_LOGGED_IN if has(m)]
    if not pool:
        return PHASE_STARTING, []
    return PHASE_UNKNOWN, []


class LoginMachine:
    def __init__(self, poll_interval=1.0, auto_login=True):
        self.poll_interval = poll_interval
        self.auto_login = auto_login
        self._lock = threading.Lock()
        self._phase = PHASE_STARTING
        self._markers = []
        self._detail = "服务启动"
        self._since = int(time.time() * 1000)
        self._qr_payload = None
        self._qr_revision = None
        self._qr_fetched = 0
        self._subscribers = []
        self._stop = threading.Event()
        self._thread = None
        self._started_at = time.time()
        # 进入某状态后是否已执行过自动动作，避免重复点
        self._auto_done_for = None

    # ---------- 对外只读视图 ----------
    def snapshot(self):
        with self._lock:
            return {
                "phase": self._phase,
                "markers": list(self._markers),
                "detail": self._detail,
                "since_unix_ms": self._since,
            }

    def qr(self):
        with self._lock:
            return {
                "payload": self._qr_payload,
                "revision": self._qr_revision,
                "fetched_unix_ms": self._qr_fetched,
            }

    def uptime_ms(self):
        return int((time.time() - self._started_at) * 1000)

    # ---------- 订阅 ----------
    def subscribe(self):
        q = deque(maxlen=32)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _publish(self):
        snap = self.snapshot()
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            q.append(snap)

    # ---------- 生命周期 ----------
    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _set_phase(self, phase, markers, detail):
        with self._lock:
            changed = phase != self._phase
            if changed:
                self._phase = phase
                self._since = int(time.time() * 1000)
                self._auto_done_for = None
            self._markers = markers
            self._detail = detail
        if changed:
            self._publish()

    # ---------- 主循环 ----------
    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:  # 服务不能因为一次探测失败就死
                self._set_phase(PHASE_UNKNOWN, [], f"探测失败: {type(e).__name__}: {e}")
            time.sleep(self.poll_interval)

    def _tick(self):
        nodes = accessibility.walk_all(only_visible=True)
        phase, markers = classify(nodes)
        detail = ",".join(markers) if markers else f"{len(nodes)} 个可见节点"

        if phase == PHASE_QR_READY:
            payload = screen.decode_qr()
            qr_changed = False
            if payload:
                rev = hashlib.sha256(payload.encode()).hexdigest()[:12]
                with self._lock:
                    qr_changed = rev != self._qr_revision
                    self._qr_payload = payload
                    self._qr_revision = rev
                    self._qr_fetched = int(time.time() * 1000)
                if qr_changed:
                    detail = f"二维码已就绪 ({rev})"
            else:
                detail = "二维码界面，但未解出内容"
            # 先按 phase 变化决定是否推送（_set_phase 内部处理）
            self._set_phase(phase, markers, detail)
            # 二维码刷新时 phase 不变，但客户端必须收到通知才能重新取码
            if qr_changed:
                self._publish()
            return

        self._set_phase(phase, markers, detail)

        # 「记住账号」界面：服务自动推进。
        # 这里刻意只把「动作」放进 UI 锁，而不是整个 _tick ——
        # _tick 每次要走一遍无障碍树（约 1 秒），整体持锁会把所有 RPC 饿死。
        # 动作前重新定位节点，保证点的是当下真实位置。
        if phase == PHASE_ONE_CLICK_READY and self.auto_login:
            with self._lock:
                already = self._auto_done_for == PHASE_ONE_CLICK_READY
            if not already:
                from . import geometry
                from . import input as inputmod
                from .ui import UI_LOCK
                with UI_LOCK:
                    rect = (geometry.node_physical_rect("push button", "登录")
                            or geometry.node_physical_rect("push button", "进入微信"))
                    if rect:
                        x, y, w, h = rect
                        inputmod.click_at(x + w // 2, y + h // 2)
                        with self._lock:
                            self._auto_done_for = PHASE_ONE_CLICK_READY
                        self._set_phase(phase, markers, "已自动点击登录，等待手机确认")

    def submit_login(self):
        """客户端请求推进登录（一键登录场景）。"""
        from . import geometry, input as inputmod
        rect = geometry.node_physical_rect("push button", "登录")
        if rect is None:
            rect = geometry.node_physical_rect("push button", "进入微信")
        if rect is None:
            return False, "界面上没有可用的「登录」按钮"
        x, y, w, h = rect
        inputmod.click_at(x + w // 2, y + h // 2)
        return True, "已点击登录"
