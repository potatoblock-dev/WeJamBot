"""Native PyQt6 desktop admin modeled on the 0x01 QQ console."""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QCloseEvent,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPixmap,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bot.admin import (
    bot_control,
    env_settings,
    log_view,
    status_light,
    store,
    theme,
    wechat_view,
    wejam_status,
)
from bot.admin.collapsible import CollapsibleSection
from bot.admin.docker_control import DockerSnapshot
from bot.admin.drop_import import enable_file_drop
from bot.admin import file_import as archive_import
from bot.admin.nav import ConsoleNav, NavPage, NavSection
from bot.allowlist import merge_chat_names_for_picker
from bot.config import load_settings


class _BotPowerWorker(QObject):
    """Run start/stop scripts off the UI thread so the window stays responsive."""

    finished = pyqtSignal(object)

    def __init__(self, action: str) -> None:
        super().__init__()
        self._action = action

    def run(self) -> None:
        """Execute store.start_bot or store.stop_bot."""
        try:
            if self._action == "start":
                store.start_bot()
            else:
                store.stop_bot()
            self.finished.emit(None)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            self.finished.emit(exc)


class _DockerPowerWorker(QObject):
    """Start/stop wejam-session or open Docker Desktop off the UI thread."""

    finished = pyqtSignal(object)

    def __init__(self, action: str) -> None:
        super().__init__()
        self._action = action

    def run(self) -> None:
        """Execute container start/stop or open Docker Desktop."""
        try:
            if self._action == "start":
                store.start_wejam_container()
            elif self._action == "stop":
                store.stop_wejam_container()
            else:
                store.open_docker_app()
            self.finished.emit(None)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            self.finished.emit(exc)


class _QrFetchWorker(QObject):
    """Fetch the login QR off the UI thread so the button can show progress."""

    finished = pyqtSignal(object, str, str)

    def __init__(self, target: str) -> None:
        """Remember the gRPC target for this fetch."""
        super().__init__()
        self._target = target

    def run(self) -> None:
        """Load QR PNG bytes. The third signal field is an error, or empty."""
        try:
            png, revision = wejam_status.fetch_qr_png(self._target)
        except Exception as exc:  # noqa: BLE001 — show driver errors on the label
            self.finished.emit(None, "", str(exc))
            return
        if not png:
            self.finished.emit(None, revision or "", "当前没有二维码内容（可能已登录，或不在扫码界面）")
            return
        self.finished.emit(bytes(png), revision or "", "")


class _AccountRefreshWorker(QObject):
    """Fetch GetAccountSummary off the UI thread.

    That RPC scrapes the chat list and badges and takes a few seconds.
    Running it on the poll timer froze the whole console.
    """

    finished = pyqtSignal(object)

    def __init__(self, target: str) -> None:
        """Remember the gRPC target for the background fetch."""
        super().__init__()
        self._target = target

    def run(self) -> None:
        """Load the account summary; errors stay on the returned view."""
        self.finished.emit(wejam_status.account_summary(self._target))


class _ChatListWorker(QObject):
    """Pull live WeChat session names off the UI thread."""

    finished = pyqtSignal(object, str)

    def run(self) -> None:
        """Emit the conversation name list, or None plus an error string."""
        try:
            names = store.list_wechat_conversations()
        except Exception as exc:  # noqa: BLE001 — surface to the lists page
            self.finished.emit(None, str(exc))
            return
        self.finished.emit(list(names), "")


class _WeChatViewBridge(QObject):
    """Marshal background grab/input results onto the UI thread."""

    frame_ready = pyqtSignal(object, str)
    # message, failed, refresh
    input_done = pyqtSignal(str, bool, bool)


class _WeChatViewLabel(QLabel):
    """Centered frame label that forwards mouse presses for remote clicks."""

    clicked = pyqtSignal(float, float, int)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        """Forward left/right clicks as label-local coordinates."""
        if event is None:
            return
        button = event.button()
        if button == Qt.MouseButton.LeftButton:
            code = 1
        elif button == Qt.MouseButton.RightButton:
            code = 3
        else:
            super().mousePressEvent(event)
            return
        pos = event.position()
        self.clicked.emit(float(pos.x()), float(pos.y()), code)


class _WeChatViewDialog(QDialog):
    """Non-modal window that shows the WeChat screen and can click/type into it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("微信画面（可操作）")
        self.setModal(False)
        self.resize(960, 680)
        self._png = b""
        self._frame_w = 0
        self._frame_h = 0
        self._grab_busy = False
        self._input_busy = False
        self._pending_refresh = False
        self._closed = False
        self._grab_started_at = 0.0
        self._input_started_at = 0.0
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wechat-view")
        self._bridge = _WeChatViewBridge(self)
        self._bridge.frame_ready.connect(self._on_frame_ready)
        self._bridge.input_done.connect(self._on_input_done)
        self._status = QLabel(
            "点击画面即可操作；左键后若出现链接等弹层会自动 Esc。"
            "右键不自动关（菜单）。也可点「关闭弹层」或按 Esc。"
        )
        self._status.setProperty("role", "muted")
        self._status.setWordWrap(True)
        self._btn_dismiss = QPushButton("关闭弹层")
        self._btn_dismiss.setToolTip(
            "向容器连按 Escape，关闭误开的链接页/对话框。"
        )
        self._btn_dismiss.clicked.connect(self._inject_dismiss)
        top = QHBoxLayout()
        top.addWidget(self._status, stretch=1)
        top.addWidget(self._btn_dismiss)
        self._label = _WeChatViewLabel("正在抓取…")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setMinimumSize(640, 400)
        self._label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._label.clicked.connect(self._on_label_click)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._label, stretch=1)
        self._timer = QTimer(self)
        self._timer.setInterval(750)
        self._timer.timeout.connect(self._grab)
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(3000)
        self._watchdog.timeout.connect(self._reset_if_stuck)
        self._timer.start()
        self._watchdog.start()
        self._grab()

    def keyPressEvent(self, event: object) -> None:
        """Map a few keys onto the container X display."""
        from PyQt6.QtGui import QKeyEvent

        if not isinstance(event, QKeyEvent):
            super().keyPressEvent(event)  # type: ignore[arg-type]
            return
        key = event.key()
        mapping = {
            Qt.Key.Key_Escape: "Escape",
            Qt.Key.Key_Return: "Return",
            Qt.Key.Key_Enter: "Return",
            Qt.Key.Key_Backspace: "BackSpace",
            Qt.Key.Key_Tab: "Tab",
        }
        keysym = mapping.get(key)
        if keysym is None:
            super().keyPressEvent(event)
            return
        self._inject_key(keysym)

    def _on_label_click(self, lx: float, ly: float, button: int) -> None:
        """Map label coordinates onto the Xvfb frame and inject a click."""
        mapped = wechat_view.map_label_to_frame(
            lx,
            ly,
            self._label.width(),
            self._label.height(),
            self._frame_w,
            self._frame_h,
        )
        if mapped is None:
            self._status.setText("点在画面外，未发送")
            return
        fx, fy = mapped
        self._inject_click(fx, fy, button)

    def _inject_click(self, x: int, y: int, button: int) -> None:
        """Queue one remote click without blocking the UI."""
        if self._input_busy or self._closed:
            return
        self._input_busy = True
        self._input_started_at = time.monotonic()
        self._status.setText(f"点击 ({x}, {y})…")
        self._ensure_pool()

        def work() -> None:
            try:
                err = wechat_view.click_at(x, y, button=button)
            except Exception as exc:  # noqa: BLE001 — keep the dialog alive
                err = str(exc)
            if self._closed:
                return
            if err:
                self._bridge.input_done.emit(err, True, False)
                return
            status = "点击完成"
            # Left-click may open a link overlay; try Escape dismiss. Skip right-click.
            if button == 1:
                time.sleep(0.4)
                try:
                    before, count_err = wechat_view.extra_popup_count()
                    if count_err:
                        status = f"点击完成 · {count_err}"
                    else:
                        dismiss_err = wechat_view.dismiss_popups()
                        if dismiss_err:
                            status = f"点击完成 · {dismiss_err}"
                        elif before > 0:
                            status = "点击完成 · 已关闭弹层"
                except Exception as exc:  # noqa: BLE001 — keep the dialog alive
                    status = f"点击完成 · {exc}"
            if not self._closed:
                self._bridge.input_done.emit(status, False, True)

        self._pool.submit(work)

    def _inject_key(self, keysym: str) -> None:
        """Queue one remote key tap without blocking the UI."""
        if self._input_busy or self._closed:
            return
        self._input_busy = True
        self._input_started_at = time.monotonic()
        self._status.setText(f"按键 {keysym}…")
        self._ensure_pool()

        def work() -> None:
            try:
                err = wechat_view.tap_key(keysym)
            except Exception as exc:  # noqa: BLE001 — keep the dialog alive
                err = str(exc)
            if not self._closed:
                self._bridge.input_done.emit(err, bool(err), not err)

        self._pool.submit(work)

    def _inject_dismiss(self) -> None:
        """Queue Escape-based popup dismiss without blocking the UI."""
        if self._input_busy or self._closed:
            return
        self._input_busy = True
        self._input_started_at = time.monotonic()
        self._status.setText("正在关闭弹层…")
        self._ensure_pool()

        def work() -> None:
            try:
                err = wechat_view.dismiss_popups()
            except Exception as exc:  # noqa: BLE001 — keep the dialog alive
                err = str(exc)
            if self._closed:
                return
            if err:
                self._bridge.input_done.emit(err, True, True)
            else:
                self._bridge.input_done.emit("已关闭弹层（或无需关闭）", False, True)

        self._pool.submit(work)

    def _on_input_done(self, message: str, failed: bool, refresh: bool) -> None:
        """Clear input busy state and optionally refresh the frame."""
        self._input_busy = False
        if self._closed:
            return
        if message:
            self._status.setText(message)
        elif not failed:
            self._status.setText("已发送，正在刷新画面…")
        if failed and not refresh:
            return
        if refresh:
            self._grab(force=True)

    def _grab(self, *, force: bool = False) -> None:
        """Start one capture if the previous grab has finished."""
        if self._closed:
            return
        if self._grab_busy and not force:
            return
        if self._grab_busy and force:
            # A click just happened: allow a queued refresh after the in-flight grab.
            self._pending_refresh = True
            return
        self._pending_refresh = False
        self._grab_busy = True
        self._grab_started_at = time.monotonic()
        self._ensure_pool()

        def work() -> None:
            try:
                png, err = wechat_view.capture_png()
            except Exception as exc:  # noqa: BLE001 — keep the dialog alive
                png, err = None, str(exc)
            if not self._closed:
                self._bridge.frame_ready.emit(png, err or "")

        self._pool.submit(work)

    def _on_frame_ready(self, png: object, err: str) -> None:
        """Show the new frame or a short status line; never block the UI thread."""
        self._grab_busy = False
        if self._closed:
            return
        pending = self._pending_refresh
        self._pending_refresh = False
        if not isinstance(png, (bytes, bytearray)) or not png:
            self._png = b""
            self._frame_w = 0
            self._frame_h = 0
            self._label.setPixmap(QPixmap())
            self._label.setText(err or "容器未开启")
            if pending:
                self._grab()
            return
        self._png = bytes(png)
        self._frame_w, self._frame_h = wechat_view.png_size(self._png)
        self._label.setText("")
        self._paint()
        if not self._input_busy and (
            self._status.text().startswith("已发送")
            or self._status.text().endswith("…")
        ):
            self._status.setText(
                "点击画面即可操作；左键后若出现链接等弹层会自动 Esc。"
                "右键不自动关。也可点「关闭弹层」或按 Esc。"
            )
        if pending:
            self._grab()

    def _ensure_pool(self) -> None:
        """Recreate the worker pool after the dialog was closed and reopened."""
        pool = self._pool
        if pool is not None and not getattr(pool, "_shutdown", False):
            return
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wechat-view")

    def _reset_if_stuck(self) -> None:
        """Unstick grab/input flags if a worker vanished without callback."""
        if self._closed:
            return
        now = time.monotonic()
        if self._grab_busy and self._grab_started_at and now - self._grab_started_at > 10:
            self._grab_busy = False
            self._status.setText("抓图超时已复位，继续刷新…")
            self._grab()
        if self._input_busy and self._input_started_at and now - self._input_started_at > 10:
            self._input_busy = False
            self._status.setText("操作超时已复位，可再点一次")

    def _paint(self) -> None:
        """Scale the last PNG into the label without changing aspect ratio."""
        if not self._png:
            return
        pix = QPixmap()
        if not pix.loadFromData(self._png):
            self._label.setText("画面数据无法显示")
            return
        target = self._label.size()
        if target.width() < 2 or target.height() < 2:
            return
        self._label.setPixmap(
            pix.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event: object) -> None:
        """Refit the current frame when the dialog is resized."""
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._paint()

    def showEvent(self, event: object) -> None:
        """Resume grabbing each time the window is shown again."""
        super().showEvent(event)  # type: ignore[arg-type]
        self._closed = False
        self._grab_busy = False
        self._input_busy = False
        self._ensure_pool()
        if not self._timer.isActive():
            self._timer.start()
        if not self._watchdog.isActive():
            self._watchdog.start()
        self._grab(force=True)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop timers/workers so Qt does not abort when the dialog is destroyed."""
        self._closed = True
        self._timer.stop()
        self._watchdog.stop()
        pool = self._pool
        try:
            pool.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=True)
        except Exception:  # noqa: BLE001 — still close the window
            pass
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wechat-view")
        super().closeEvent(event)


class AdminWindow(QMainWindow):
    """Main window with primary/secondary nav for ops, chats, files, and config."""

    def __init__(self) -> None:
        super().__init__()
        self._persona_id = ""
        self._persona_file = ""
        self._persona_dirty = False
        self._persona_loading = False
        self._policy_dirty = False
        self._policy_loading = False
        self._lists_dirty = False
        self._lists_loading = False
        self._allow_live_names: list[str] = []
        self._imp_dirty = False
        self._imp_loading = False
        self._imp_key = ""
        self._imp_editing_new = False
        self._records: list[dict] = []
        self._culture_dirty = False
        self._culture_loading = False
        self._culture_chat = ""
        self._culture_editing_new = False
        self._culture_records: list[dict] = []
        self._transcript_rows: list[dict] = []
        self._evo_pending_id = ""
        self._evo_active_chat = ""
        self._evo_pending_rows: list[dict] = []
        self._evo_active_rows: list[dict] = []
        self._log_clear_armed = False
        self._power_thread: QThread | None = None
        self._docker_thread: QThread | None = None
        self._account_thread: QThread | None = None
        self._account_quiet = True
        self._qr_thread: QThread | None = None
        self._qr_worker: _QrFetchWorker | None = None
        self._chat_list_thread: QThread | None = None
        self._live_chats_error = ""
        self._view_dialog: _WeChatViewDialog | None = None
        self._docker_poll_tick = 0
        self._login_poll_tick = 0
        self._last_login_phase = ""
        self._last_docker_snap = store.docker_snapshot()
        self._log_watermark = store.log_file_size()
        self._build_ui()
        self._wire_actions()
        self.refresh_all()
        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._on_poll)
        self._poll.start()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        """Cancel armed clear-log when the user clicks anywhere except that button."""
        if (
            self._log_clear_armed
            and event.type() == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
        ):
            clicked = QApplication.widgetAt(event.globalPosition().toPoint())
            if not self._widget_is_clear_log_button(clicked):
                self._disarm_clear_logs()
        return super().eventFilter(watched, event)

    def _widget_is_clear_log_button(self, widget: QWidget | None) -> bool:
        """True when widget is the clear-log button or one of its children."""
        while widget is not None:
            if widget is self._btn_clear_log:
                return True
            widget = widget.parentWidget()
        return False

    def _on_poll(self) -> None:
        """Periodically refresh run-tab status and message logs."""
        self._docker_poll_tick += 1
        # Docker CLI inspect is heavier than gRPC probe; refresh every 3s.
        force_docker = self._docker_poll_tick % 3 == 0
        self.refresh_status(refresh_docker=force_docker)
        self.refresh_logs()

    def _build_ui(self) -> None:
        """Lay out primary/secondary navigation and page stack."""
        self.setWindowTitle(status_light.window_title(status_light.LIGHT_OFF))
        icon_path = bot_control.project_root() / "assets" / "app-icon.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1040, 720)

        sections = (
            NavSection("live", "现场"),
            NavSection(
                "chats",
                "会话",
                (NavPage("lists", "名单"), NavPage("transcript", "记录")),
            ),
            NavSection(
                "files",
                "档案",
                (
                    NavPage("persona", "人设"),
                    NavPage("evolution", "进化"),
                    NavPage("culture", "群文化"),
                    NavPage("impression", "印象"),
                ),
            ),
            NavSection(
                "config",
                "配置",
                (NavPage("policy", "策略"), NavPage("api", "API")),
            ),
        )
        self._nav = ConsoleNav(sections)
        self._nav.register("live", "live", self._build_live_page())
        self._nav.register("chats", "lists", self._build_lists_page())
        self._nav.register("chats", "transcript", self._build_transcript_tab())
        self._nav.register("files", "persona", self._build_persona_tab())
        self._nav.register("files", "evolution", self._build_prompt_evolution_tab())
        self._nav.register("files", "culture", self._build_group_culture_tab())
        self._nav.register("files", "impression", self._build_impressions_tab())
        self._nav.register("config", "policy", self._build_policy_tab())
        self._nav.register("config", "api", self._build_api_tab())
        self._nav.set_section_footer("config", self._build_folder_bar())
        self._nav.page_changed.connect(self._on_nav_page_changed)
        self.setCentralWidget(self._nav)

        save_act = QAction("保存", self)
        save_act.setShortcut(QKeySequence.StandardKey.Save)
        save_act.triggered.connect(self._save_visible)
        self.addAction(save_act)

    def _build_live_page(self) -> QWidget:
        """Ops strip, optional login side panel, and message log."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        status = QGroupBox("状态")
        status_col = QVBoxLayout(status)
        bot_row = QHBoxLayout()
        self._status_label = QLabel("检查中…")
        self._status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        bot_row.addWidget(self._status_label, stretch=1)
        self._btn_mode = QPushButton("模式：监听")
        self._btn_mode.setToolTip(
            "监听：只旁听/写档，不回复（白名单会话也会改走旁听）。"
            "回复：按白名单正常回复。下一条消息生效，不必重启。"
        )
        self._btn_toggle = QPushButton("启动")
        self._btn_refresh = QPushButton("刷新")
        bot_row.addWidget(self._btn_mode)
        bot_row.addWidget(self._btn_toggle)
        bot_row.addWidget(self._btn_refresh)
        status_col.addLayout(bot_row)

        self._docker_label = QLabel("检查中…")
        self._docker_label.setWordWrap(True)
        self._docker_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._docker_label.setProperty("role", "muted")
        status_col.addWidget(self._docker_label)
        docker_row = QHBoxLayout()
        self._btn_docker_toggle = QPushButton("启动容器")
        self._btn_docker_toggle.setToolTip(
            "复用已有容器与数据卷（不删容器），保留登录态。"
        )
        self._btn_docker_open = QPushButton("打开 Docker Desktop")
        self._btn_wechat_view = QPushButton("打开画面")
        self._btn_wechat_view.setToolTip("交互容器内微信画面。")
        docker_row.addWidget(self._btn_docker_toggle)
        docker_row.addWidget(self._btn_docker_open)
        docker_row.addWidget(self._btn_wechat_view)
        docker_row.addStretch()
        status_col.addLayout(docker_row)
        layout.addWidget(status)

        hints = QHBoxLayout()
        self._wejam_hint = QLabel("")
        self._wejam_hint.setWordWrap(True)
        self._wejam_hint.setProperty("role", "warning")
        self._login_hint = QLabel("")
        self._login_hint.setProperty("role", "muted")
        hints.addWidget(self._wejam_hint, stretch=1)
        hints.addWidget(self._login_hint)
        layout.addLayout(hints)

        self._account_box = QGroupBox("已登录账号")
        account_l = QVBoxLayout(self._account_box)
        self._account_label = QLabel("")
        self._account_label.setWordWrap(True)
        self._account_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        account_l.addWidget(self._account_label)
        account_row = QHBoxLayout()
        self._btn_refresh_account = QPushButton("刷新账号信息")
        self._btn_logout = QPushButton("退出登录")
        self._btn_logout.setToolTip("操作容器里微信的设置面板退出登录。")
        account_row.addWidget(self._btn_refresh_account)
        account_row.addWidget(self._btn_logout)
        account_row.addStretch()
        account_l.addLayout(account_row)
        self._account_box.setVisible(False)
        layout.addWidget(self._account_box)

        body = QHBoxLayout()
        body.setSpacing(8)
        log_col = QVBoxLayout()
        log_col.addWidget(QLabel("消息监控日志"))
        self._log_view = QTextEdit()
        self._log_view.setObjectName("logView")
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._log_view.setPlaceholderText(
            "启动后应出现 inbound → 正在回复 → 正在发送 → replied"
        )
        log_col.addWidget(self._log_view, stretch=1)
        log_actions = QHBoxLayout()
        self._btn_log = QPushButton("立即刷新日志")
        self._btn_clear_log = QPushButton("清空日志")
        self._btn_clear_log.setToolTip("按一次进入确认，再按一次清空 data/bot.log")
        log_actions.addWidget(self._btn_log)
        log_actions.addWidget(self._btn_clear_log)
        log_actions.addStretch()
        log_col.addLayout(log_actions)
        body.addLayout(log_col, stretch=1)

        self._login_side = QWidget()
        self._login_side.setMinimumWidth(300)
        login_l = QVBoxLayout(self._login_side)
        login_l.setContentsMargins(0, 0, 0, 0)
        self._qr_label = QLabel("尚未拉取二维码")
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setMinimumHeight(280)
        self._qr_label.setToolTip("未登录时从 Callus 拉取屏幕上的扫码登录二维码。")
        login_l.addWidget(self._qr_label, stretch=1)
        qr_row = QHBoxLayout()
        self._btn_qr = QPushButton("刷新二维码")
        self._btn_submit_login = QPushButton("一键登录")
        qr_row.addWidget(self._btn_qr)
        qr_row.addWidget(self._btn_submit_login)
        login_l.addLayout(qr_row)
        body.addWidget(self._login_side)
        layout.addLayout(body, stretch=1)
        return root

    def _build_lists_page(self) -> QWidget:
        """Speak mode plus one chat table: observe ⊇ reply."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        speak = QGroupBox("回复模式")
        feat = QVBoxLayout(speak)
        mode_row = QHBoxLayout()
        self._speak_buttons: dict[str, QPushButton] = {}
        self._speak_group = QButtonGroup(self)
        self._speak_group.setExclusive(True)
        tips = {
            "auto": "沿用未点名规则和工作日高峰；写入 reply_policy.toml，下一条消息生效。",
            "all": "回复勾选的会话里每条都尝试回复；写入 reply_policy.toml，下一条消息生效。",
            "mention_quote": "仅在正文出现 bot 名时回复；写入 reply_policy.toml，下一条消息生效。",
        }
        for mode, label in store.speak_mode_options():
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(tips.get(mode, ""))
            self._speak_group.addButton(btn)
            self._speak_buttons[mode] = btn
            mode_row.addWidget(btn)
        mode_row.addStretch()
        feat.addLayout(mode_row)
        layout.addWidget(speak)

        chats = QGroupBox("会话名单")
        chats.setToolTip(
            "旁听：写印象/群文化（及可选提示词进化）。"
            "回复：在旁听之上按白名单回复。勾选回复会自动旁听；取消旁听会取消回复。"
        )
        chats_l = QVBoxLayout(chats)
        self._chat_table = QTableWidget(0, 3)
        self._chat_table.setHorizontalHeaderLabels(["会话", "旁听", "回复"])
        self._chat_table.verticalHeader().setVisible(False)
        self._chat_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._chat_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._chat_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self._chat_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        chats_l.addWidget(self._chat_table)
        self._lists_summary = QLabel("")
        self._lists_summary.setWordWrap(True)
        self._lists_summary.setProperty("role", "muted")
        chats_l.addWidget(self._lists_summary)
        row = QHBoxLayout()
        self._btn_refresh_lists = QPushButton("刷新会话列表")
        self._btn_save_lists = QPushButton("保存名单")
        self._btn_save_lists.setToolTip(
            "同时写入旁听名单与回复白名单；runner 下一条消息重读。"
        )
        row.addWidget(self._btn_refresh_lists)
        row.addWidget(self._btn_save_lists)
        row.addStretch()
        chats_l.addLayout(row)
        layout.addWidget(chats, stretch=1)
        return root

    def _build_folder_bar(self) -> QWidget:
        """Config-section footer that opens local data folders."""
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(6)
        self._btn_personas_folder = QPushButton("打开人设")
        self._btn_knowledge_folder = QPushButton("打开知识库")
        self._btn_project_folder = QPushButton("打开项目根")
        row.addWidget(self._btn_personas_folder)
        row.addWidget(self._btn_knowledge_folder)
        row.addWidget(self._btn_project_folder)
        row.addStretch()
        return bar

    def _build_api_tab(self) -> QWidget:
        """Edit project .env with secrets masked."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._api_target = QLineEdit()
        self._api_bot_name = QLineEdit()
        self._api_llm_base = QLineEdit()
        self._api_llm_key = QLineEdit()
        self._api_llm_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_llm_key.setPlaceholderText("已保存则留空不改")
        self._api_llm_model = QLineEdit()
        self._api_timeout = QLineEdit()
        self._api_key_hint = QLabel("")
        self._api_key_hint.setProperty("role", "muted")
        self._api_target.setPlaceholderText("推荐 127.0.0.1:7701（Callus GUI）")
        self._api_target.setToolTip(
            "wejam.v1 桥地址。主路径 Callus GUI :7701（可进个人群）；"
            "openclaw 私聊旁路 :7702；遗留 WeJam :7700。微信画面仅 GUI 桥可用。"
        )
        self._api_llm_key.setToolTip("留空表示保持原值不改写；写入项目根 .env（不进 git）。")
        form.addRow("WEJAM_TARGET", self._api_target)
        form.addRow("BOT_NAME", self._api_bot_name)
        form.addRow("LLM_BASE_URL", self._api_llm_base)
        form.addRow("LLM_API_KEY", self._api_llm_key)
        form.addRow("", self._api_key_hint)
        form.addRow("LLM_MODEL", self._api_llm_model)
        form.addRow("LLM_TIMEOUT_SECONDS", self._api_timeout)
        self._api_tools_enabled = QCheckBox("启用")
        self._api_tools_enabled.setToolTip(
            "LLM_TOOLS_ENABLED：回复时可调用北京时间与联网搜索工具。默认开。"
        )
        self._api_web_search = QCheckBox("启用")
        self._api_web_search.setToolTip(
            "WEB_SEARCH_ENABLED：允许 web_search（维基→Bing→DuckDuckGo）。"
            "关则只保留北京时间工具。默认开。"
        )
        form.addRow("LLM_TOOLS_ENABLED", self._api_tools_enabled)
        form.addRow("WEB_SEARCH_ENABLED", self._api_web_search)
        self._api_archive_base = QLineEdit()
        self._api_archive_key = QLineEdit()
        self._api_archive_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_archive_key.setPlaceholderText("留空=沿用 LLM_API_KEY / 不改已保存")
        self._api_archive_key.setToolTip(
            "留空沿用 LLM_API_KEY / 不改已保存。保存后需在现场页重启闲聊进程。"
        )
        self._api_archive_model = QLineEdit()
        self._api_archive_model.setPlaceholderText("留空=沿用 LLM_MODEL")
        self._api_archive_base.setPlaceholderText("留空=沿用 LLM_BASE_URL")
        self._api_archive_key_hint = QLabel("")
        self._api_archive_key_hint.setProperty("role", "muted")
        form.addRow("ARCHIVE_LLM_BASE_URL", self._api_archive_base)
        form.addRow("ARCHIVE_LLM_API_KEY", self._api_archive_key)
        form.addRow("", self._api_archive_key_hint)
        form.addRow("ARCHIVE_LLM_MODEL", self._api_archive_model)
        layout.addLayout(form)
        layout.addStretch(1)
        actions = QHBoxLayout()
        self._btn_reload_api = QPushButton("重新加载")
        self._btn_save_api = QPushButton("保存 API 配置")
        actions.addWidget(self._btn_reload_api)
        actions.addWidget(self._btn_save_api)
        layout.addLayout(actions)
        return root

    def _build_persona_tab(self) -> QWidget:
        """Persona pack list plus one txt editor, filling the page under the hint."""
        root = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._persona_list = QListWidget()
        self._persona_list.setToolTip(
            "点选只用来编辑。点「启用」才切换线上口吻。改 txt 后须保存才会写入文件。"
        )
        left_layout.addWidget(self._persona_list)
        pack_actions = QHBoxLayout()
        self._btn_new_persona = QPushButton("新建")
        self._btn_import_persona = QPushButton("导入")
        self._btn_import_persona.setToolTip(
            "选择或拖入 .txt / .md / .json；普通文本按文件名建人设包并写入 persona.txt。"
        )
        self._btn_enable_persona = QPushButton("启用")
        self._btn_enable_persona.setToolTip("切换线上口吻；点选列表只用于编辑。")
        self._btn_delete_persona = QPushButton("删除")
        pack_actions.addWidget(self._btn_new_persona)
        pack_actions.addWidget(self._btn_import_persona)
        pack_actions.addWidget(self._btn_enable_persona)
        pack_actions.addWidget(self._btn_delete_persona)
        left_layout.addLayout(pack_actions)
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("文件"))
        self._persona_file_combo = QComboBox()
        file_row.addWidget(self._persona_file_combo, stretch=1)
        self._btn_add_persona_file = QPushButton("+ txt")
        file_row.addWidget(self._btn_add_persona_file)
        right_layout.addLayout(file_row)
        self._persona_editor = QPlainTextEdit()
        self._persona_editor.setPlaceholderText(
            "选中人设和文件后编辑…也可把文本文件拖到本页导入。"
        )
        right_layout.addWidget(self._persona_editor, stretch=1)
        self._btn_save_persona_file = QPushButton("保存本文件")
        right_layout.addWidget(self._btn_save_persona_file)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("支持「导入」或拖放 .txt / .md / .json 到本页。")
        hint.setProperty("role", "muted")
        hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        outer.addWidget(hint)
        outer.addWidget(split, stretch=1)
        enable_file_drop(root, self._on_persona_files_dropped)
        enable_file_drop(self._persona_list, self._on_persona_files_dropped)
        enable_file_drop(self._persona_editor, self._on_persona_files_dropped)
        return root

    def _build_policy_tab(self) -> QWidget:
        """Scrollable reply-policy page: labeled fields plus collapsible guard editors."""
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        self._build_policy_switches(layout)

        ui = store.read_admin_ui()
        anti_body = QWidget()
        anti_layout = QVBoxLayout(anti_body)
        anti_layout.setContentsMargins(0, 0, 0, 0)
        self._policy_anti = QPlainTextEdit()
        self._policy_anti.setMinimumHeight(160)
        anti_layout.addWidget(self._policy_anti)
        self._policy_anti_section = CollapsibleSection(
            "防注入",
            anti_body,
            expanded=ui.policy_anti_injection_expanded,
            content_min_height=200,
        )
        self._policy_anti_section.toggled.connect(self._persist_policy_fold_state)
        layout.addWidget(self._policy_anti_section)

        stay_body = QWidget()
        stay_layout = QVBoxLayout(stay_body)
        stay_layout.setContentsMargins(0, 0, 0, 0)
        self._policy_stay = QPlainTextEdit()
        self._policy_stay.setMinimumHeight(180)
        stay_layout.addWidget(self._policy_stay)
        self._policy_stay_section = CollapsibleSection(
            "不得脱离人设",
            stay_body,
            expanded=ui.policy_stay_on_prompt_expanded,
            content_min_height=220,
        )
        self._policy_stay_section.toggled.connect(self._persist_policy_fold_state)
        layout.addWidget(self._policy_stay_section)
        layout.addStretch(1)
        scroll.setWidget(page)
        outer.addWidget(scroll, stretch=1)
        self._btn_save_policy = QPushButton("保存策略")
        outer.addWidget(self._btn_save_policy)
        return root

    def _build_policy_switches(self, layout: QVBoxLayout) -> None:
        """Labeled checkboxes, spin boxes, and line edits for reply_policy.toml.

        The raw body stays in memory so save rewrites assignment lines only.
        Prompt-evolution keys are edited on 档案/进化 and are not duplicated here.
        """
        from bot.reply_policy import SPEAK_MODE_LABELS, SPEAK_MODES

        switches = QGroupBox("回复开关与频率")
        switches.setToolTip(
            "写入 reply_policy.toml，保存后下一条消息生效。下方两块可折叠。"
            "进化开关在「档案 / 进化」。"
        )
        box = QVBoxLayout(switches)
        self._policy_body = ""
        self._policy_checks: dict[str, QCheckBox] = {}
        self._policy_spins: dict[str, QSpinBox] = {}
        self._policy_lines: dict[str, QLineEdit] = {}

        check_specs = (
            ("enabled", "总开关", "关掉后不回复任何会话。"),
            ("c2c", "私聊", "是否回复私聊。"),
            ("group", "群聊", "是否回复群聊。"),
            ("on_text", "文本", "是否处理文本消息。"),
            ("on_image", "图片", "是否处理图片消息。"),
            ("on_voice", "语音", "是否处理语音消息。"),
            ("group_unmentioned", "未点名也回复", "群消息没出现 bot 名时是否仍回复。"),
            ("unmentioned_need_hook", "未点名要有钩子", "未点名时还要像在接话才回复。"),
            ("unmentioned_off_peak_only", "未点名仅非高峰", "未点名回复只在非工作日高峰进行。"),
            ("group_culture_auto", "自动写群文化", "群聊成功回复后重写本群氛围备忘。"),
            ("knowledge_auto", "自动写知识库", "群聊成功回复后更新本地知识库词条。"),
        )
        checks = QGridLayout()
        checks.setHorizontalSpacing(12)
        checks.setVerticalSpacing(4)
        for index, (key, label, tip) in enumerate(check_specs):
            widget = QCheckBox(label)
            widget.setToolTip(tip)
            widget.stateChanged.connect(self._mark_policy_dirty)
            self._policy_checks[key] = widget
            checks.addWidget(widget, index // 3, index % 3)
        box.addLayout(checks)

        number_specs = (
            ("min_interval_seconds", "同会话最少间隔（秒）", 0, 86400, "同一会话两次回复的最短间隔。"),
            ("max_per_session_per_minute", "每分钟最多条数", 0, 1000, "同一会话一分钟内最多回复几条。"),
            ("unmentioned_cooldown_seconds", "未点名冷却（秒）", 0, 86400, "未点名回复之后的冷却。"),
            ("unmentioned_thread_minutes", "未点名接话（分钟）", 0, 10080, "视为还在同一串对话的时间窗。"),
            ("context_message_limit", "上下文条数", 0, 500, "回复前读取的上下文窗口。0 表示只读当前可见窗。"),
            ("context_recent_focus", "优先依据条数", 0, 500, "窗口里最近这么多条标成优先依据。"),
            ("observe_min_interval_seconds", "旁听写档间隔（秒）", 0, 86400, "旁听消化的最短间隔。"),
            ("observe_digest_min_messages", "旁听累积条数", 1, 10000, "累积这么多条也会触发旁听写档。"),
            ("transcript_max_messages", "本地缓冲条数", 10, 100000, "每个会话在本地环缓冲里保留的条数。"),
        )
        numbers = QGridLayout()
        numbers.setHorizontalSpacing(8)
        numbers.setVerticalSpacing(4)
        for index, (key, label, low, high, tip) in enumerate(number_specs):
            row, col = divmod(index, 2)
            col *= 2
            caption = QLabel(label)
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setAccelerated(True)
            spin.setMinimumWidth(88)
            spin.setToolTip(tip + " 可直接键入数字。")
            caption.setToolTip(spin.toolTip())
            spin.valueChanged.connect(self._mark_policy_dirty)
            self._policy_spins[key] = spin
            numbers.addWidget(caption, row, col)
            numbers.addWidget(spin, row, col + 1)
        box.addLayout(numbers)
        self._context_limit = self._policy_spins["context_message_limit"]

        mode_row = QHBoxLayout()
        mode_label = QLabel("发言模式")
        self._policy_speak = QComboBox()
        for mode in SPEAK_MODES:
            self._policy_speak.addItem(SPEAK_MODE_LABELS[mode], mode)
        self._policy_speak.setToolTip(
            "自动 / 全部尝试回复 / 仅点名。会话页的按钮写的是同一项。"
        )
        mode_label.setToolTip(self._policy_speak.toolTip())
        self._policy_speak.currentIndexChanged.connect(self._mark_policy_dirty)
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self._policy_speak, stretch=1)
        box.addLayout(mode_row)

        line_specs = (
            ("bot_names", "点名识别名", "正文里出现这些名字算点名。多个用逗号分隔。"),
            ("require_keywords", "必须包含", "消息要含其中一词才回复。留空表示不限制。"),
            ("skip_keywords", "跳过这些词", "消息含其中一词则不回复。多个用逗号分隔。"),
        )
        for key, label, tip in line_specs:
            row = QHBoxLayout()
            caption = QLabel(label)
            edit = QLineEdit()
            edit.setPlaceholderText("多个用逗号分隔")
            edit.setToolTip(tip)
            caption.setToolTip(tip)
            edit.textChanged.connect(self._mark_policy_dirty)
            self._policy_lines[key] = edit
            row.addWidget(caption)
            row.addWidget(edit, stretch=1)
            box.addLayout(row)

        layout.addWidget(switches)

    def _build_impressions_tab(self) -> QWidget:
        """Speaker impression list/editor with multi-select merge and alias edits."""
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self._imp_search = QLineEdit()
        self._imp_search.setPlaceholderText("按昵称、别名或 person_id 搜索…")
        self._imp_search.setToolTip(
            "同一 person_id 跨群共用一份印象。多选后「合并选中」对齐；GUI 无微信号，不按同名自动合并。"
        )
        left_layout.addWidget(self._imp_search)
        self._imp_list = QListWidget()
        self._imp_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._imp_list.setToolTip(
            "单击编辑；⌘/Ctrl 或 Shift 多选后点「合并选中」。保留档为当前高亮项。"
        )
        left_layout.addWidget(self._imp_list)
        imp_actions = QHBoxLayout()
        self._btn_new_imp = QPushButton("新建印象")
        self._btn_import_imp = QPushButton("导入")
        self._btn_import_imp.setToolTip(
            "JSON（含 user_key）或 .txt（文件名→昵称，正文→印象）；也可拖放到本页。"
        )
        self._btn_merge_imp = QPushButton("合并选中")
        self._btn_merge_imp.setToolTip(
            "将其它选中档案并入当前高亮项：正文拼接、别名改挂、删除源档。"
        )
        self._btn_delete_imp = QPushButton("删除")
        self._btn_delete_imp.setToolTip("删除选中档案的印象文件与全部别名。")
        imp_actions.addWidget(self._btn_new_imp)
        imp_actions.addWidget(self._btn_import_imp)
        imp_actions.addWidget(self._btn_merge_imp)
        imp_actions.addWidget(self._btn_delete_imp)
        left_layout.addLayout(imp_actions)
        split.addWidget(left)
        right = QWidget()
        form = QFormLayout(right)
        self._imp_key_edit = QLineEdit()
        self._imp_name_edit = QLineEdit()
        self._imp_alias_list = QListWidget()
        self._imp_alias_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._imp_alias_list.setMaximumHeight(120)
        self._imp_alias_list.setToolTip("此人已绑定的「会话:昵称」。可多选后删除。")
        self._btn_unlink_alias = QPushButton("删除所选别名")
        self._btn_unlink_alias.setToolTip("只摘别名，不删印象正文。")
        alias_col = QVBoxLayout()
        alias_col.setContentsMargins(0, 0, 0, 0)
        alias_col.addWidget(self._imp_alias_list)
        alias_col.addWidget(self._btn_unlink_alias)
        alias_wrap = QWidget()
        alias_wrap.setLayout(alias_col)
        self._imp_body = QPlainTextEdit()
        form.addRow("person_id", self._imp_key_edit)
        form.addRow("当前昵称", self._imp_name_edit)
        form.addRow("别名", alias_wrap)
        form.addRow("印象", self._imp_body)
        self._btn_save_imp = QPushButton("保存印象")
        form.addRow(self._btn_save_imp)
        link_box = QGroupBox("手动添加别名")
        link_form = QFormLayout(link_box)
        self._imp_link_chat = QLineEdit()
        self._imp_link_chat.setPlaceholderText("会话显示名，如 99+")
        self._imp_link_nick = QLineEdit()
        self._imp_link_nick.setPlaceholderText("群昵称；私聊可留空")
        self._btn_link_alias = QPushButton("绑定别名到此人")
        self._btn_link_alias.setToolTip(
            "把该会话+昵称指向当前 person_id；之后该群入站读写同一份印象。"
            "若别名已挂在别人身上，会改挂到此人（对方其它别名不动）。"
        )
        link_form.addRow("会话名", self._imp_link_chat)
        link_form.addRow("昵称", self._imp_link_nick)
        link_form.addRow(self._btn_link_alias)
        form.addRow(link_box)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        outer.addWidget(split)
        enable_file_drop(root, self._on_impression_files_dropped)
        enable_file_drop(self._imp_list, self._on_impression_files_dropped)
        enable_file_drop(self._imp_body, self._on_impression_files_dropped)
        return root

    def _build_transcript_tab(self) -> QWidget:
        """Browse local listen/reply transcript rings stored in bot.sqlite3."""
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        self._transcript_path_label = QLabel("")
        self._transcript_path_label.setWordWrap(True)
        self._transcript_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._transcript_path_label.setProperty("role", "muted")
        self._transcript_path_label.setToolTip(
            "环缓冲只读浏览。回复用 context_message_limit 窗口；清空需自行处理 SQLite。"
        )
        outer.addWidget(self._transcript_path_label)
        tools = QHBoxLayout()
        self._btn_transcript_refresh = QPushButton("刷新")
        self._btn_transcript_folder = QPushButton("打开数据目录")
        tools.addWidget(self._btn_transcript_refresh)
        tools.addWidget(self._btn_transcript_folder)
        tools.addWidget(QLabel("每会话条数"))
        self._transcript_limit = QSpinBox()
        self._transcript_limit.setRange(20, 2000)
        self._transcript_limit.setSingleStep(20)
        self._transcript_limit.setValue(200)
        tools.addWidget(self._transcript_limit)
        tools.addStretch(1)
        outer.addLayout(tools)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self._transcript_search = QLineEdit()
        self._transcript_search.setPlaceholderText("按会话名筛选…")
        left_layout.addWidget(self._transcript_search)
        self._transcript_list = QListWidget()
        left_layout.addWidget(self._transcript_list)
        self._transcript_summary = QLabel("尚未加载")
        self._transcript_summary.setProperty("role", "muted")
        left_layout.addWidget(self._transcript_summary)
        split.addWidget(left)
        self._transcript_body = QPlainTextEdit()
        self._transcript_body.setReadOnly(True)
        self._transcript_body.setPlaceholderText("左侧选一个会话查看记录。")
        split.addWidget(self._transcript_body)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        outer.addWidget(split)
        return root

    def _build_prompt_evolution_tab(self) -> QWidget:
        """Pending prompt-evolution proposals and approved per-group overlays."""
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self._evo_auto = QCheckBox("自动提案")
        self._evo_auto.setToolTip(
            "群聊成功回复或旁听写档后起草本群进化提示词；须批准后才注入。"
            "不改全局人设。默认关闭。"
        )
        controls.addWidget(self._evo_auto)
        controls.addWidget(QLabel("同群最少收到消息数"))
        self._evo_interval = QSpinBox()
        self._evo_interval.setRange(0, 100_000)
        self._evo_interval.setSingleStep(10)
        self._evo_interval.setValue(50)
        self._evo_interval.setToolTip(
            "两次提案之间，bot 至少再收到这么多条该群消息；0 表示不限条数（仍受 pending 阻挡）。"
        )
        controls.addWidget(self._evo_interval)
        self._btn_evo_save_policy = QPushButton("保存开关")
        controls.addWidget(self._btn_evo_save_policy)
        controls.addStretch(1)
        outer.addLayout(controls)

        split = QSplitter(Qt.Orientation.Horizontal)
        pending_box = QGroupBox("待审批")
        pending_layout = QVBoxLayout(pending_box)
        self._evo_pending_list = QListWidget()
        pending_layout.addWidget(self._evo_pending_list)
        pending_btns = QHBoxLayout()
        self._btn_evo_approve = QPushButton("批准")
        self._btn_evo_reject = QPushButton("拒绝")
        self._btn_evo_refresh = QPushButton("刷新")
        pending_btns.addWidget(self._btn_evo_approve)
        pending_btns.addWidget(self._btn_evo_reject)
        pending_btns.addWidget(self._btn_evo_refresh)
        pending_layout.addLayout(pending_btns)
        split.addWidget(pending_box)

        detail_box = QGroupBox("提案详情 / 已生效叠加")
        detail_form = QFormLayout(detail_box)
        self._evo_meta = QLabel("选择左侧待审，或下方已生效项")
        self._evo_meta.setWordWrap(True)
        self._evo_meta.setProperty("role", "muted")
        detail_form.addRow(self._evo_meta)
        self._evo_rationale = QPlainTextEdit()
        self._evo_rationale.setReadOnly(True)
        self._evo_rationale.setMaximumHeight(80)
        self._evo_rationale.setPlaceholderText("提案理由…")
        detail_form.addRow("理由", self._evo_rationale)
        self._evo_body = QPlainTextEdit()
        self._evo_body.setReadOnly(True)
        self._evo_body.setPlaceholderText("进化正文…")
        detail_form.addRow("正文", self._evo_body)
        self._evo_active_list = QListWidget()
        detail_form.addRow("已生效", self._evo_active_list)
        self._btn_evo_disable = QPushButton("停用选中已生效")
        detail_form.addRow(self._btn_evo_disable)
        split.addWidget(detail_box)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        outer.addWidget(split)
        return root

    def _build_group_culture_tab(self) -> QWidget:
        """Per-group culture / meme note list and editor."""
        root = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._culture_search = QLineEdit()
        self._culture_search.setPlaceholderText("按备注名或会话名搜索…")
        self._culture_search.setToolTip(
            "成功回复或旁听写档后可自动更新（reply_policy.toml 的 group_culture_auto）。"
            "只记群梗与风向，不记个人隐私。"
        )
        left_layout.addWidget(self._culture_search)
        self._culture_list = QListWidget()
        left_layout.addWidget(self._culture_list)
        culture_actions = QHBoxLayout()
        self._btn_new_culture = QPushButton("新建")
        self._btn_import_culture = QPushButton("导入")
        self._btn_import_culture.setToolTip(
            " .txt 文件名→会话名、正文→群文化；或含 chat 字段的 JSON。可拖放。"
        )
        culture_actions.addWidget(self._btn_new_culture)
        culture_actions.addWidget(self._btn_import_culture)
        left_layout.addLayout(culture_actions)
        split.addWidget(left)
        right = QWidget()
        form = QFormLayout(right)
        self._culture_chat_edit = QLineEdit()
        self._culture_chat_edit.setPlaceholderText("微信会话名（须与会话列表一致）")
        self._culture_label_edit = QLineEdit()
        self._culture_label_edit.setPlaceholderText("可选备注名，便于列表识别")
        self._culture_body = QPlainTextEdit()
        self._culture_body.setPlaceholderText("群氛围、梗、近期话题…也可拖入文本文件导入。")
        form.addRow("会话名", self._culture_chat_edit)
        form.addRow("备注名", self._culture_label_edit)
        form.addRow("群文化", self._culture_body)
        culture_btns = QHBoxLayout()
        self._btn_save_culture = QPushButton("保存群文化")
        self._btn_clear_culture = QPushButton("清空正文")
        culture_btns.addWidget(self._btn_save_culture)
        culture_btns.addWidget(self._btn_clear_culture)
        form.addRow(culture_btns)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        outer = QVBoxLayout(root)
        outer.addWidget(split)
        enable_file_drop(root, self._on_culture_files_dropped)
        enable_file_drop(self._culture_list, self._on_culture_files_dropped)
        enable_file_drop(self._culture_body, self._on_culture_files_dropped)
        return root

    def _wire_actions(self) -> None:
        """Connect buttons and list selection."""
        self._btn_toggle.clicked.connect(self.on_toggle_power)
        self._btn_mode.clicked.connect(self.on_toggle_bot_mode)
        self._btn_refresh.clicked.connect(lambda: self.refresh_status(refresh_docker=True))
        self._btn_docker_toggle.clicked.connect(self.on_toggle_docker)
        self._btn_docker_open.clicked.connect(lambda: self._docker_power("open"))
        self._btn_wechat_view.clicked.connect(self._open_wechat_view)
        self._btn_log.clicked.connect(self.refresh_logs)
        self._btn_clear_log.clicked.connect(self.on_clear_logs)
        self._btn_qr.clicked.connect(self._refresh_qr)
        self._btn_submit_login.clicked.connect(self._submit_login)
        self._btn_refresh_account.clicked.connect(self._refresh_account_info)
        self._btn_logout.clicked.connect(self._logout_wechat)
        for mode, btn in self._speak_buttons.items():
            btn.clicked.connect(lambda checked, m=mode: self.on_select_speak_mode(m, checked))
        self._btn_save_lists.clicked.connect(self.on_save_chat_lists)
        self._btn_refresh_lists.clicked.connect(self.on_refresh_chat_lists)
        self._chat_table.itemChanged.connect(self._on_chat_list_item_changed)
        self._btn_personas_folder.clicked.connect(
            lambda: store.reveal_in_file_manager(store.personas_dir())
        )
        self._btn_knowledge_folder.clicked.connect(
            lambda: store.reveal_in_file_manager(store.knowledge_dir())
        )
        self._btn_project_folder.clicked.connect(
            lambda: store.reveal_in_file_manager(bot_control.project_root())
        )
        self._btn_reload_api.clicked.connect(self.load_api_settings)
        self._btn_save_api.clicked.connect(self.on_save_api_settings)
        self._btn_new_persona.clicked.connect(self.on_new_persona)
        self._btn_import_persona.clicked.connect(self.on_import_personas)
        self._btn_enable_persona.clicked.connect(self.on_enable_persona)
        self._btn_delete_persona.clicked.connect(self.on_delete_persona)
        self._btn_add_persona_file.clicked.connect(self.on_add_persona_file)
        self._btn_save_persona_file.clicked.connect(self.on_save_persona_file)
        self._persona_list.currentItemChanged.connect(self.on_persona_selected)
        self._persona_file_combo.currentTextChanged.connect(self.on_persona_file_changed)
        self._persona_editor.textChanged.connect(self._mark_persona_dirty)
        self._btn_save_policy.clicked.connect(self.on_save_policy)
        self._policy_anti.textChanged.connect(self._mark_policy_dirty)
        self._policy_stay.textChanged.connect(self._mark_policy_dirty)
        self._btn_save_imp.clicked.connect(self.on_save_impression)
        self._btn_new_imp.clicked.connect(self.on_new_impression)
        self._btn_import_imp.clicked.connect(self.on_import_impressions)
        self._btn_merge_imp.clicked.connect(self.on_merge_impressions)
        self._btn_delete_imp.clicked.connect(self.on_delete_impressions)
        self._btn_link_alias.clicked.connect(self.on_link_impression_alias)
        self._btn_unlink_alias.clicked.connect(self.on_unlink_impression_aliases)
        self._imp_search.textChanged.connect(self.render_impressions)
        self._imp_list.currentItemChanged.connect(self.on_impression_selected)
        self._btn_save_culture.clicked.connect(self.on_save_group_culture)
        self._btn_clear_culture.clicked.connect(self.on_clear_group_culture)
        self._btn_new_culture.clicked.connect(self.on_new_group_culture)
        self._btn_import_culture.clicked.connect(self.on_import_group_cultures)
        self._culture_search.textChanged.connect(self.render_group_cultures)
        self._culture_list.currentItemChanged.connect(self.on_group_culture_selected)
        self._culture_chat_edit.textChanged.connect(self._mark_culture_dirty)
        self._culture_label_edit.textChanged.connect(self._mark_culture_dirty)
        self._culture_body.textChanged.connect(self._mark_culture_dirty)
        self._btn_evo_save_policy.clicked.connect(self.on_save_prompt_evolution_policy)
        self._btn_evo_approve.clicked.connect(self.on_approve_prompt_evolution)
        self._btn_evo_reject.clicked.connect(self.on_reject_prompt_evolution)
        self._btn_evo_refresh.clicked.connect(self.load_prompt_evolution)
        self._btn_evo_disable.clicked.connect(self.on_disable_prompt_evolution)
        self._evo_pending_list.currentItemChanged.connect(
            self.on_prompt_evolution_pending_selected
        )
        self._evo_active_list.currentItemChanged.connect(
            self.on_prompt_evolution_active_selected
        )
        self._btn_transcript_refresh.clicked.connect(self.load_transcripts)
        self._btn_transcript_folder.clicked.connect(
            lambda: store.reveal_in_file_manager(store.transcript_db_path().parent)
        )
        self._transcript_search.textChanged.connect(self.render_transcript_chats)
        self._transcript_list.currentItemChanged.connect(self.on_transcript_selected)
        self._transcript_limit.valueChanged.connect(self._reload_selected_transcript)

    def refresh_all(self) -> None:
        """Reload every editor from disk."""
        self.refresh_status()
        self.refresh_logs()
        self.load_api_settings()
        self.load_personas()
        self.load_policy()
        self.load_impressions()
        self.load_group_cultures()
        self.load_prompt_evolution()
        self.load_transcripts()
        if not self._lists_dirty:
            self._reload_chat_lists(live=self._allow_live_names)
            self._schedule_live_chat_refresh(quiet=True)
        self._sync_speak_buttons()

    def refresh_status(self, *, refresh_docker: bool = True) -> None:
        """Update run label, Docker panel, WeJam probe, title light, and toggles."""
        snap = store.bot_snapshot()
        lines = store.tail_log_lines()
        light = status_light.title_light(
            running=snap.running, healthy=snap.healthy, log_lines=lines
        )
        self.setWindowTitle(status_light.window_title(light))
        state = "运行中" if snap.running else "已停止"
        pid = f" pid={snap.pid}" if snap.pid else ""
        llm = "已配置" if snap.llm_configured else "未配置 LLM_API_KEY"
        mode_label = store.bot_mode_button_label().removeprefix("模式：")
        self._status_label.setText(
            f"{state}{pid} · {mode_label} · 模型 {snap.llm_model} · {llm}"
        )
        self._btn_mode.setText(store.bot_mode_button_label())
        self._btn_toggle.setText("停止" if snap.running else "启动")
        if refresh_docker:
            self._last_docker_snap = store.docker_snapshot()
        docker = self._last_docker_snap
        self._docker_label.setText(self._format_docker_summary(docker))
        self._docker_label.setProperty(
            "role", "accent" if docker.container_running else "warning"
        )
        self._docker_label.style().unpolish(self._docker_label)
        self._docker_label.style().polish(self._docker_label)
        busy = self._docker_thread is not None
        self._btn_docker_toggle.setText(
            "停止容器" if docker.container_running else "启动容器"
        )
        self._btn_docker_toggle.setEnabled(not busy and docker.daemon_ok)
        self._btn_docker_open.setEnabled(not busy)
        view = wejam_status.probe(snap.wejam_target)
        if view.reachable:
            self._wejam_hint.setText(
                f"WeJam {snap.wejam_target}：可达 ready={view.ready} phase={view.phase}"
            )
            self._wejam_hint.setProperty("role", "accent")
        else:
            self._wejam_hint.setText(
                self._wejam_unreachable_hint(snap.wejam_target, docker, view.error)
            )
            self._wejam_hint.setProperty("role", "warning")
        self._wejam_hint.style().unpolish(self._wejam_hint)
        self._wejam_hint.style().polish(self._wejam_hint)
        self._sync_login_tab(view)

    def _sync_login_tab(self, view: wejam_status.WejamView) -> None:
        """Toggle account strip vs QR side panel; refresh summary on phase change."""
        logged_in = bool(view.reachable and view.phase == "LOGGED_IN")
        self._account_box.setVisible(logged_in)
        self._btn_logout.setVisible(logged_in)
        self._btn_refresh_account.setVisible(logged_in)
        self._login_side.setVisible(not logged_in)
        self._qr_label.setVisible(not logged_in)
        self._btn_qr.setVisible(not logged_in)
        self._btn_submit_login.setVisible(not logged_in)
        if view.reachable and view.phase and not logged_in:
            self._login_hint.setText(f"登录阶段：{view.phase}")
            self._login_hint.setProperty("role", "muted")
            self._login_hint.style().unpolish(self._login_hint)
            self._login_hint.style().polish(self._login_hint)
        phase_changed = view.phase != self._last_login_phase
        self._last_login_phase = view.phase
        self._login_poll_tick += 1
        if not logged_in:
            return
        if phase_changed or self._login_poll_tick % 5 == 0:
            self._refresh_account_info(quiet=True)

    def _format_uptime(self, seconds: int) -> str:
        """Format elapsed seconds as 年/月/日/h/m/s, omitting units that are still zero.

        A month is 30 days and a year is 365 days so the label does not need a calendar date.
        """
        remain = max(0, int(seconds))
        units = (
            (365 * 24 * 3600, "年"),
            (30 * 24 * 3600, "月"),
            (24 * 3600, "日"),
            (3600, "h"),
            (60, "m"),
            (1, "s"),
        )
        pieces: list[str] = []
        for size, label in units:
            count, remain = divmod(remain, size)
            if count:
                pieces.append(f"{count}{label}")
        return "".join(pieces) if pieces else "0s"

    def _format_account_text(self, account: wejam_status.AccountView) -> str:
        """Format account summary lines for the login tab."""
        if account.error:
            return f"读取失败：{account.error}"
        uptime_s = max(0, account.uptime_ms // 1000)
        hint = account.account_hint or "（界面未读到昵称）"
        return "\n".join(
            [
                f"显示名：{hint}",
                f"登录阶段：{account.phase}",
                f"微信版本：{account.version or '-'}",
                f"登录详情：{account.detail or '-'}",
                f"会话数：{account.chat_count}",
                f"未读会话 / 未读消息：{account.unread_chats} / {account.unread_messages}",
                f"服务运行：{self._format_uptime(uptime_s)}",
            ]
        )

    def _refresh_account_info(self, *, quiet: bool = False) -> None:
        """Reload GetAccountSummary on a worker so the window stays responsive."""
        if self._account_thread is not None:
            return
        self._account_quiet = quiet
        thread = QThread(self)
        worker = _AccountRefreshWorker(load_settings().wejam_target)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(
            lambda account, th=thread, wk=worker: self._account_refresh_done(account, th, wk)
        )
        self._account_thread = thread
        thread.start()

    def _account_refresh_done(
        self,
        account: object,
        thread: QThread,
        worker: _AccountRefreshWorker,
    ) -> None:
        """Apply a finished account summary and drop the worker thread."""
        thread.quit()
        thread.wait(2000)
        worker.deleteLater()
        thread.deleteLater()
        self._account_thread = None
        if not isinstance(account, wejam_status.AccountView):
            return
        self._account_label.setText(self._format_account_text(account))
        if account.error and not self._account_quiet:
            QMessageBox.warning(self, "账号信息", account.error)
        elif account.logged_in:
            self._login_hint.setText("已登录")
            self._login_hint.setProperty("role", "accent")
            self._login_hint.style().unpolish(self._login_hint)
            self._login_hint.style().polish(self._login_hint)

    def _logout_wechat(self) -> None:
        """Confirm and call WeJam Logout; then refresh the login tab."""
        answer = QMessageBox.question(
            self,
            "退出登录",
            "将在容器里的微信中执行「退出登录」。确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._btn_logout.setEnabled(False)
        try:
            phase = wejam_status.logout(load_settings().wejam_target)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "退出登录", str(exc))
            self._btn_logout.setEnabled(True)
            return
        self._btn_logout.setEnabled(True)
        QMessageBox.information(self, "退出登录", f"当前阶段：{phase}")
        self.refresh_status(refresh_docker=False)

    def _format_docker_summary(self, docker: DockerSnapshot) -> str:
        """One-line Docker summary; leads with container on/off for the run tab."""
        if not docker.daemon_ok:
            power = "容器：未知（引擎未就绪）"
        elif docker.container_running:
            power = f"容器：已开启（{docker.container}）"
        elif docker.container_status in {"", "absent", "unknown"}:
            power = "容器：已关闭"
        else:
            power = f"容器：已关闭（{docker.container_status}）"
        engine = "引擎 ✓" if docker.daemon_ok else "引擎 ✗"
        if docker.image_present:
            img = f"镜像 {docker.image}"
            if docker.image_id:
                img += f" ({docker.image_id})"
        else:
            img = f"镜像缺失 {docker.image}"
        parts = [power, engine, img]
        if docker.detail and not docker.daemon_ok:
            parts.append(docker.daemon_error or docker.detail)
        return " · ".join(parts)

    def _wejam_unreachable_hint(
        self, target: str, docker: DockerSnapshot, error: str
    ) -> str:
        """Explain gRPC failure using Docker layer status instead of raw shell only."""
        if not docker.daemon_ok:
            tip = "Docker 引擎未就绪：先打开 Docker Desktop，或点下方「打开 Docker Desktop」。"
        elif not docker.image_present:
            tip = f"本地没有镜像 {docker.image}，需先按文档构建后再点「启动容器」。"
        elif not docker.container_running:
            tip = "容器未开启：点下方「启动容器」（复用已有容器，不删登录数据）。"
        else:
            tip = "容器已开启但 gRPC 未就绪，稍等或点刷新；仍失败再点「停止容器」后重新启动。"
        err = f" {error}" if error else ""
        return f"WeJam {target} 未连通。{tip}{err}"

    def on_toggle_docker(self) -> None:
        """Start or stop the WeChat container from the single toggle button."""
        docker = self._last_docker_snap
        self._docker_power("stop" if docker.container_running else "start")

    def _open_wechat_view(self) -> None:
        """Show the view-only WeChat frame window."""
        dialog = self._view_dialog
        if dialog is None:
            dialog = _WeChatViewDialog(self)
            self._view_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _docker_power(self, action: str) -> None:
        """Dispatch container start/stop or open Docker Desktop without freezing UI."""
        if self._docker_thread is not None:
            return
        self._btn_docker_toggle.setEnabled(False)
        self._btn_docker_open.setEnabled(False)
        thread = QThread(self)
        worker = _DockerPowerWorker(action)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(
            lambda err, act=action: self._docker_power_done(err, act, thread, worker)
        )
        self._docker_thread = thread
        thread.start()

    def _docker_power_done(
        self,
        err: object,
        action: str,
        thread: QThread,
        worker: _DockerPowerWorker,
    ) -> None:
        """Re-enable Docker buttons after a container/Desktop action finishes."""
        thread.quit()
        thread.wait(2000)
        worker.deleteLater()
        thread.deleteLater()
        self._docker_thread = None
        if err is not None:
            title = {
                "start": "启动容器失败",
                "stop": "停止容器失败",
                "open": "打开 Docker Desktop 失败",
            }.get(action, "Docker 操作失败")
            QMessageBox.warning(self, title, str(err))
        self.refresh_status(refresh_docker=True)

    def refresh_logs(self) -> None:
        """Reload runner logs with newest lines at the top (same as 0x01)."""
        lines = list(reversed(store.tail_log_lines()))
        self._log_view.setHtml(log_view.format_logs_html(lines))

    def on_toggle_power(self) -> None:
        """Start or stop the runner on a worker thread."""
        snap = store.bot_snapshot()
        self._power("stop" if snap.running else "start")

    def on_toggle_bot_mode(self) -> None:
        """Flip global listen/reply mode; next inbound message picks it up."""
        store.cycle_bot_mode()
        self.refresh_status(refresh_docker=False)

    def _power(self, action: str) -> None:
        """Dispatch start/stop without freezing the UI; ignore re-entry while busy."""
        if self._power_thread is not None:
            return
        self._btn_toggle.setEnabled(False)
        thread = QThread(self)
        worker = _BotPowerWorker(action)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(lambda err: self._power_done(err, thread, worker))
        self._power_thread = thread
        thread.start()

    def _power_done(self, err: object, thread: QThread, worker: _BotPowerWorker) -> None:
        """Re-enable the toggle after start/stop finishes."""
        thread.quit()
        thread.wait(2000)
        worker.deleteLater()
        thread.deleteLater()
        self._power_thread = None
        self._btn_toggle.setEnabled(True)
        if err is not None:
            QMessageBox.warning(self, "启停失败", str(err))
        self.refresh_status()
        self.refresh_logs()

    def on_clear_logs(self) -> None:
        """Two-step truncate of data/bot.log."""
        if not self._log_clear_armed:
            self._log_clear_armed = True
            self._btn_clear_log.setText("确认清空")
            self._btn_clear_log.setObjectName("dangerConfirm")
            self._btn_clear_log.style().unpolish(self._btn_clear_log)
            self._btn_clear_log.style().polish(self._btn_clear_log)
            return
        store.clear_monitor_logs()
        self._disarm_clear_logs()
        self.refresh_logs()

    def _disarm_clear_logs(self) -> None:
        """Reset the clear-log button after confirm or cancel."""
        self._log_clear_armed = False
        self._btn_clear_log.setText("清空日志")
        self._btn_clear_log.setObjectName("")
        self._btn_clear_log.style().unpolish(self._btn_clear_log)
        self._btn_clear_log.style().polish(self._btn_clear_log)

    def _refresh_qr(self) -> None:
        """Start a QR fetch and show progress on the label immediately."""
        if self._qr_thread is not None:
            return
        self._qr_label.setPixmap(QPixmap())
        self._qr_label.setText("正在获取二维码…")
        self._btn_qr.setEnabled(False)
        thread = QThread(self)
        worker = _QrFetchWorker(load_settings().wejam_target)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Bound method queues onto the UI thread; a lambda would run on the worker.
        worker.finished.connect(self._qr_fetch_done)
        self._qr_thread = thread
        self._qr_worker = worker
        thread.start()

    def _qr_fetch_done(self, png: object, revision: str, err: str) -> None:
        """Show the QR image or the failure text, then drop the worker."""
        thread = self._qr_thread
        worker = self._qr_worker
        self._qr_thread = None
        self._qr_worker = None
        if thread is not None:
            thread.quit()
            thread.wait(2000)
            thread.deleteLater()
        if worker is not None:
            worker.deleteLater()
        self._btn_qr.setEnabled(True)
        if err or not isinstance(png, (bytes, bytearray)) or not png:
            self._qr_label.setPixmap(QPixmap())
            self._qr_label.setText(self._qr_failure_text(err))
            self._login_hint.setText("")
            return
        pix = QPixmap()
        if not pix.loadFromData(bytes(png)):
            self._qr_label.setText("二维码数据无法显示")
            self._login_hint.setText("")
            return
        self._qr_label.setPixmap(
            pix.scaled(280, 280, Qt.AspectRatioMode.KeepAspectRatio)
        )
        self._login_hint.setText(f"revision={revision or '-'}")

    def _qr_failure_text(self, err: str) -> str:
        """Short label copy for a failed QR fetch."""
        if "NOT_FOUND" in err or "没有可用的二维码" in err or "没有二维码" in err:
            return "当前没有可用的二维码（微信不在扫码界面）"
        line = err.strip().splitlines()[0] if err.strip() else "获取二维码失败"
        if len(line) > 180:
            return line[:179] + "…"
        return line

    def _submit_login(self) -> None:
        """Call SubmitLogin on the WeJam driver."""
        try:
            phase = wejam_status.submit_login(load_settings().wejam_target)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "登录", str(exc))
            return
        QMessageBox.information(self, "登录", f"当前阶段：{phase}")
        self.refresh_status(refresh_docker=False)

    def _sync_speak_buttons(self) -> None:
        """Check the button that matches reply_policy.toml."""
        mode = store.read_speak_mode()
        btn = self._speak_buttons.get(mode)
        if btn is not None:
            btn.setChecked(True)

    def on_select_speak_mode(self, mode: str, checked: bool) -> None:
        """Write speak_mode when the user picks an exclusive button."""
        if not checked:
            return
        store.set_speak_mode(mode)
        self.load_policy()

    def _on_nav_page_changed(self, section: str, page: str) -> None:
        """When opening 会话/名单, try to fill rows from the live WeChat list."""
        if section == "chats" and page == "lists" and not self._lists_dirty:
            self._schedule_live_chat_refresh(quiet=True)

    def _schedule_live_chat_refresh(self, *, quiet: bool = True) -> None:
        """Fetch WeChat conversations in the background and merge into the table."""
        if self._chat_list_thread is not None:
            return
        if quiet:
            self._lists_summary.setText("正在拉取微信会话列表…")
        thread = QThread(self)
        worker = _ChatListWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(
            lambda names, err, th=thread, wk=worker, q=quiet: self._live_chat_refresh_done(
                names, err, th, wk, quiet=q
            )
        )
        self._chat_list_thread = thread
        thread.start()

    def _live_chat_refresh_done(
        self,
        names: object,
        error: str,
        thread: QThread,
        worker: _ChatListWorker,
        *,
        quiet: bool,
    ) -> None:
        """Apply a finished live chat list pull and drop the worker thread."""
        thread.quit()
        thread.wait(2000)
        worker.deleteLater()
        thread.deleteLater()
        self._chat_list_thread = None
        if isinstance(names, list):
            self._allow_live_names = [str(n).strip() for n in names if str(n).strip()]
            self._live_chats_error = ""
            if not self._lists_dirty:
                self._reload_chat_lists(live=self._allow_live_names)
            if not quiet:
                QMessageBox.information(
                    self,
                    "刷新会话列表",
                    f"已加载 {len(self._allow_live_names)} 个会话。"
                    "勾选旁听/回复后点「保存名单」。",
                )
            return
        self._live_chats_error = error or "无法读取会话列表"
        self._update_lists_summary()
        if not quiet:
            QMessageBox.warning(
                self,
                "刷新会话列表",
                f"无法读取会话列表（容器未开或 WeJam 未连通）：\n{self._live_chats_error}",
            )

    def on_refresh_chat_lists(self) -> None:
        """Pull live session names from WeChat and rebuild the chat table."""
        if self._lists_dirty:
            answer = QMessageBox.question(
                self,
                "刷新会话列表",
                "名单有未保存勾选。刷新会先按磁盘名单重建再合并现场会话，未保存勾选会丢失。继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._lists_dirty = False
        self._schedule_live_chat_refresh(quiet=False)

    def on_save_chat_lists(self) -> None:
        """Persist observe and reply columns into the two on-disk lists."""
        observe = self._checked_observe_names()
        allow = self._checked_allow_names()
        store.write_observe_chat_names(observe)
        store.write_allow_chat_names(allow)
        self._lists_dirty = False
        self._reload_chat_lists(live=self._allow_live_names)
        QMessageBox.information(
            self,
            "会话名单",
            "已保存旁听与回复名单。runner 会在下一条消息重读。",
        )

    def _reload_chat_lists(self, *, live: list[str] | None = None) -> None:
        """Rebuild the single table from live names plus saved allow/observe."""
        allowed = set(store.read_allow_chats())
        observed = set(store.read_observe_chats())
        # Reply implies observe in the UI even if the allow file was ahead.
        names = merge_chat_names_for_picker(live or [], allowed | observed)
        self._lists_loading = True
        self._chat_table.setRowCount(0)
        for name in names:
            row = self._chat_table.rowCount()
            self._chat_table.insertRow(row)
            title = QTableWidgetItem(name)
            title.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            title.setData(Qt.ItemDataRole.UserRole, name)
            observe_item = QTableWidgetItem()
            observe_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            reply_item = QTableWidgetItem()
            reply_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            on_reply = name in allowed
            on_observe = name in observed or on_reply
            observe_item.setCheckState(
                Qt.CheckState.Checked if on_observe else Qt.CheckState.Unchecked
            )
            reply_item.setCheckState(
                Qt.CheckState.Checked if on_reply else Qt.CheckState.Unchecked
            )
            self._chat_table.setItem(row, 0, title)
            self._chat_table.setItem(row, 1, observe_item)
            self._chat_table.setItem(row, 2, reply_item)
        self._lists_loading = False
        self._update_lists_summary()

    def _checked_allow_names(self) -> list[str]:
        """Return session names with the reply column checked."""
        return self._checked_column_names(2)

    def _checked_observe_names(self) -> list[str]:
        """Return session names with the observe column checked."""
        return self._checked_column_names(1)

    def _checked_column_names(self, column: int) -> list[str]:
        """Collect chat titles whose checkbox column is checked."""
        names: list[str] = []
        for row in range(self._chat_table.rowCount()):
            check = self._chat_table.item(row, column)
            title = self._chat_table.item(row, 0)
            if check is None or title is None:
                continue
            if check.checkState() != Qt.CheckState.Checked:
                continue
            text = str(title.data(Qt.ItemDataRole.UserRole) or title.text()).strip()
            if text:
                names.append(text)
        return names

    def _update_lists_summary(self) -> None:
        """Show live count plus observe/reply selections that will be saved."""
        live_n = len(self._allow_live_names)
        row_n = self._chat_table.rowCount()
        if self._live_chats_error and live_n == 0:
            head = f"现场会话：未能拉取（{self._live_chats_error}）· 表中 {row_n} 行"
        elif live_n:
            head = f"现场会话：{live_n} · 表中 {row_n} 行（含已保存但不在现场列表的项）"
        else:
            head = f"现场会话：尚未拉取 · 表中 {row_n} 行（点「刷新会话列表」或稍候自动拉取）"
        observe = self._checked_observe_names()
        allow = self._checked_allow_names()
        observe_text = "、".join(observe) if observe else "无"
        allow_text = "、".join(allow) if allow else "无"
        self._lists_summary.setText(
            f"{head}\n旁听：{observe_text}\n回复：{allow_text}"
        )

    def _on_chat_list_item_changed(self, item: QTableWidgetItem | None) -> None:
        """Keep reply ⊆ observe when a checkbox changes, then mark dirty."""
        if self._lists_loading or item is None:
            return
        row = item.row()
        col = item.column()
        observe_item = self._chat_table.item(row, 1)
        reply_item = self._chat_table.item(row, 2)
        if observe_item is None or reply_item is None:
            return
        self._lists_loading = True
        if col == 2 and item.checkState() == Qt.CheckState.Checked:
            observe_item.setCheckState(Qt.CheckState.Checked)
        elif col == 1 and item.checkState() == Qt.CheckState.Unchecked:
            reply_item.setCheckState(Qt.CheckState.Unchecked)
        self._lists_loading = False
        self._lists_dirty = True
        self._update_lists_summary()

    def load_api_settings(self) -> None:
        """Fill API form from .env (secrets masked)."""
        view = env_settings.read_api_settings()
        self._api_target.setText(view.values.get("WEJAM_TARGET", ""))
        self._api_bot_name.setText(view.values.get("BOT_NAME", ""))
        self._api_llm_base.setText(view.values.get("LLM_BASE_URL", ""))
        self._api_llm_model.setText(view.values.get("LLM_MODEL", ""))
        self._api_timeout.setText(view.values.get("LLM_TIMEOUT_SECONDS", ""))
        self._api_tools_enabled.setChecked(
            self._env_flag_checked(view.values.get("LLM_TOOLS_ENABLED", ""), default=True)
        )
        self._api_web_search.setChecked(
            self._env_flag_checked(view.values.get("WEB_SEARCH_ENABLED", ""), default=True)
        )
        self._api_llm_key.setText("")
        mask = view.secret_masks.get("LLM_API_KEY", "")
        self._api_key_hint.setText(f"已保存：{mask}" if mask else "未设置")
        self._api_archive_base.setText(view.values.get("ARCHIVE_LLM_BASE_URL", ""))
        self._api_archive_model.setText(view.values.get("ARCHIVE_LLM_MODEL", ""))
        self._api_archive_key.setText("")
        archive_mask = view.secret_masks.get("ARCHIVE_LLM_API_KEY", "")
        self._api_archive_key_hint.setText(
            f"已保存：{archive_mask}" if archive_mask else "未设置（将沿用 LLM_API_KEY）"
        )

    def on_save_api_settings(self) -> None:
        """Write API form values into .env."""
        updates = {
            "WEJAM_TARGET": self._api_target.text(),
            "BOT_NAME": self._api_bot_name.text(),
            "LLM_BASE_URL": self._api_llm_base.text(),
            "LLM_API_KEY": self._api_llm_key.text(),
            "LLM_MODEL": self._api_llm_model.text(),
            "LLM_TIMEOUT_SECONDS": self._api_timeout.text(),
            "LLM_TOOLS_ENABLED": "1" if self._api_tools_enabled.isChecked() else "0",
            "WEB_SEARCH_ENABLED": "1" if self._api_web_search.isChecked() else "0",
            "ARCHIVE_LLM_BASE_URL": self._api_archive_base.text(),
            "ARCHIVE_LLM_API_KEY": self._api_archive_key.text(),
            "ARCHIVE_LLM_MODEL": self._api_archive_model.text(),
        }
        env_settings.save_api_settings(updates)
        self.load_api_settings()
        QMessageBox.information(self, "API", "已写入 .env。重启闲聊进程后生效。")

    def _env_flag_checked(self, raw: str, *, default: bool) -> bool:
        """Map a .env 1/0-style flag to checkbox state (empty uses default)."""
        text = (raw or "").strip().lower()
        if not text:
            return default
        if text in {"0", "false", "no", "off"}:
            return False
        if text in {"1", "true", "yes", "on"}:
            return True
        return default

    def load_personas(self) -> None:
        """Refresh the persona pack list."""
        self._persona_list.clear()
        for row in store.list_persona_packs():
            mark = "● " if row.get("active") else ""
            item = QListWidgetItem(f"{mark}{row['id']}  {row['title']}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self._persona_list.addItem(item)

    def on_persona_selected(self, current: QListWidgetItem | None) -> None:
        """Show txt files for the selected pack."""
        self._persona_file_combo.clear()
        self._persona_editor.clear()
        if current is None:
            self._persona_id = ""
            return
        self._persona_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        self._persona_loading = True
        for name in store.list_persona_files(self._persona_id):
            self._persona_file_combo.addItem(name)
        self._persona_loading = False
        if self._persona_file_combo.count():
            self.on_persona_file_changed(self._persona_file_combo.currentText())

    def on_persona_file_changed(self, name: str) -> None:
        """Load one txt into the editor."""
        if self._persona_loading or not self._persona_id or not name:
            return
        self._persona_file = name
        self._persona_loading = True
        self._persona_editor.setPlainText(store.read_persona_file(self._persona_id, name))
        self._persona_loading = False
        self._persona_dirty = False

    def _mark_persona_dirty(self) -> None:
        """Remember unsaved persona txt edits."""
        if not self._persona_loading:
            self._persona_dirty = True

    def on_save_persona_file(self) -> None:
        """Write the current persona txt."""
        if not self._persona_id or not self._persona_file:
            return
        store.save_persona_file(
            self._persona_id, self._persona_file, self._persona_editor.toPlainText()
        )
        self._persona_dirty = False

    def on_enable_persona(self) -> None:
        """Mark the selected pack as active."""
        if not self._persona_id:
            return
        store.set_persona_active(self._persona_id)
        self.load_personas()

    def on_new_persona(self) -> None:
        """Ask for a pack id and create it."""
        name, ok = QInputDialog.getText(self, "新建人设", "id（小写字母数字）")
        if not ok or not name.strip():
            return
        try:
            store.new_persona_pack(name.strip(), name.strip())
        except ValueError as exc:
            QMessageBox.warning(self, "新建失败", str(exc))
            return
        self.load_personas()

    def on_delete_persona(self) -> None:
        """Delete a non-active pack."""
        if not self._persona_id:
            return
        try:
            store.delete_persona_pack(self._persona_id)
        except ValueError as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self.load_personas()

    def on_add_persona_file(self) -> None:
        """Create an extra txt in the selected pack."""
        if not self._persona_id:
            return
        name, ok = QInputDialog.getText(self, "新 txt", "文件名")
        if not ok or not name.strip():
            return
        try:
            store.add_persona_file(self._persona_id, name.strip())
        except ValueError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        self.on_persona_selected(self._persona_list.currentItem())

    def load_policy(self) -> None:
        """Load reply-policy fields and guard editors from disk."""
        from bot.reply_policy import read_policy_form

        self._policy_loading = True
        body, anti, stay = store.read_reply_policy_parts()
        self._policy_body = body
        self._fill_policy_form(read_policy_form(body))
        self._policy_anti.setPlainText(anti)
        self._policy_stay.setPlainText(stay)
        self._policy_loading = False
        self._policy_dirty = False
        self._sync_speak_buttons()

    def _fill_policy_form(self, values: dict[str, object]) -> None:
        """Push parsed policy values into the labeled controls."""
        for key, widget in self._policy_checks.items():
            widget.setChecked(bool(values.get(key)))
        for key, widget in self._policy_spins.items():
            widget.setValue(int(values.get(key) or 0))
        for key, widget in self._policy_lines.items():
            items = values.get(key) or []
            widget.setText(", ".join(str(item) for item in items))
        mode = str(values.get("speak_mode") or "")
        index = self._policy_speak.findData(mode)
        if index >= 0:
            self._policy_speak.setCurrentIndex(index)

    def _policy_form_values(self) -> dict[str, object]:
        """Read the labeled controls back into toml field values."""
        values: dict[str, object] = {
            key: widget.isChecked() for key, widget in self._policy_checks.items()
        }
        values.update(
            {key: widget.value() for key, widget in self._policy_spins.items()}
        )
        values.update(
            {
                key: self._split_policy_list(widget.text())
                for key, widget in self._policy_lines.items()
            }
        )
        values["speak_mode"] = str(self._policy_speak.currentData() or "")
        return values

    def _split_policy_list(self, text: str) -> list[str]:
        """Split a comma-separated policy field. Chinese commas count too."""
        return [
            part.strip()
            for part in (text or "").replace("，", ",").split(",")
            if part.strip()
        ]

    def _mark_policy_dirty(self, *_args: object) -> None:
        """Remember unsaved policy edits. Extra signal arguments are ignored."""
        if not self._policy_loading:
            self._policy_dirty = True

    def on_save_policy(self) -> None:
        """Write the form back into reply_policy.toml without dropping comments.

        Evolution keys are copied from the file on disk first, because that page
        saves them on its own and this form does not edit them.
        """
        from bot.reply_policy import (
            apply_policy_form,
            upsert_prompt_evolution_auto_text,
            upsert_prompt_evolution_min_messages_text,
        )

        auto, count = store.prompt_evolution_settings()
        body = upsert_prompt_evolution_auto_text(self._policy_body, auto)
        body = upsert_prompt_evolution_min_messages_text(body, count)
        body = apply_policy_form(body, self._policy_form_values())
        store.save_reply_policy_parts(
            body,
            self._policy_anti.toPlainText(),
            self._policy_stay.toPlainText(),
        )
        self._policy_body = body
        self._policy_dirty = False
        self._sync_speak_buttons()
        QMessageBox.information(self, "策略", "已保存。下一条消息生效。")

    def _persist_policy_fold_state(self, _expanded: bool) -> None:
        """Remember which policy guard sections are open."""
        store.save_admin_ui(
            store.AdminUiState(
                policy_anti_injection_expanded=self._policy_anti_section.is_expanded,
                policy_stay_on_prompt_expanded=self._policy_stay_section.is_expanded,
            )
        )

    def load_impressions(self) -> None:
        """Refresh impression records from disk."""
        self._records = store.list_impressions()
        self.render_impressions()

    def render_impressions(self) -> None:
        """Fill the impression list, applying the search box."""
        needle = self._imp_search.text().strip().casefold()
        keep = str(self._imp_key or "")
        self._imp_list.clear()
        select_item: QListWidgetItem | None = None
        for row in self._records:
            key = str(row.get("user_key") or "")
            name = str(row.get("username") or "")
            aliases = " ".join(str(x) for x in (row.get("alias_labels") or []))
            hay = f"{name} {key} {aliases}".casefold()
            if needle and needle not in hay:
                continue
            short = key[:8] if len(key) > 8 else key
            label = name or aliases or short
            if aliases and name and aliases != name:
                label = f"{name}（{aliases}）"
            elif aliases and not name:
                label = aliases
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._imp_list.addItem(item)
            if keep and key == keep:
                select_item = item
        if select_item is not None:
            self._imp_list.setCurrentItem(select_item)

    def _fill_impression_aliases(self, aliases: list[dict] | None) -> None:
        """Populate the editable alias list for the selected person."""
        self._imp_alias_list.clear()
        rows = aliases or []
        if not rows:
            empty = QListWidgetItem("（无别名）")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._imp_alias_list.addItem(empty)
            return
        for row in rows:
            chat = str(row.get("chat") or "").strip()
            nick = str(row.get("nick") or "").strip()
            label = f"{chat}:{nick}" if nick else chat
            item = QListWidgetItem(label or "（空）")
            item.setData(Qt.ItemDataRole.UserRole, {"chat": chat, "nick": nick})
            self._imp_alias_list.addItem(item)

    def on_impression_selected(self, current: QListWidgetItem | None) -> None:
        """Load one impression into the form."""
        if current is None:
            return
        key = str(current.data(Qt.ItemDataRole.UserRole) or "")
        record = store.load_impression(key)
        self._imp_loading = True
        self._imp_key = key
        self._imp_editing_new = False
        self._imp_key_edit.setText(str(record.get("user_key") or ""))
        self._imp_key_edit.setReadOnly(True)
        self._imp_name_edit.setText(str(record.get("username") or ""))
        self._fill_impression_aliases(list(record.get("aliases") or []))
        self._imp_body.setPlainText(str(record.get("impression") or ""))
        self._imp_loading = False
        self._imp_dirty = False

    def on_new_impression(self) -> None:
        """Clear the form for a new person_id / legacy key."""
        self._imp_editing_new = True
        self._imp_key = ""
        self._imp_key_edit.setReadOnly(False)
        self._imp_key_edit.clear()
        self._imp_name_edit.clear()
        self._imp_alias_list.clear()
        hint = QListWidgetItem("（新建后可由旁听自动登记别名）")
        hint.setFlags(Qt.ItemFlag.NoItemFlags)
        self._imp_alias_list.addItem(hint)
        self._imp_body.clear()

    def on_save_impression(self) -> None:
        """Write the impression form to disk."""
        key = self._imp_key_edit.text().strip() or self._imp_key
        if not key:
            QMessageBox.warning(
                self,
                "缺少 person_id",
                "请填写 person_id（UUID），或等旁听自动建档后再编辑。",
            )
            return
        store.save_impression(key, self._imp_name_edit.text(), self._imp_body.toPlainText())
        self._imp_key_edit.setReadOnly(True)
        self._imp_dirty = False
        self.load_impressions()

    def on_link_impression_alias(self) -> None:
        """Bind chat+nick to the currently selected person_id for cross-group share."""
        person_id = self._imp_key_edit.text().strip() or self._imp_key
        chat = self._imp_link_chat.text().strip()
        nick = self._imp_link_nick.text().strip()
        if not person_id:
            QMessageBox.warning(
                self,
                "跨群绑定",
                "请先在左侧选中一个档案（或填写 person_id）。",
            )
            return
        if not chat:
            QMessageBox.warning(self, "跨群绑定", "请填写会话显示名。")
            return
        try:
            store.link_impression_alias(person_id, chat, nick)
        except ValueError as exc:
            QMessageBox.warning(self, "绑定失败", str(exc))
            return
        self._imp_link_chat.clear()
        self._imp_link_nick.clear()
        self.load_impressions()
        # Reselect the same person so alias labels refresh in the form.
        for i in range(self._imp_list.count()):
            item = self._imp_list.item(i)
            if item is not None and str(item.data(Qt.ItemDataRole.UserRole) or "") == person_id:
                self._imp_list.setCurrentItem(item)
                break
        QMessageBox.information(
            self,
            "跨群绑定",
            f"已绑定。之后「{chat}"
            + (f"：{nick}" if nick else "")
            + "」入站会读写此印象。",
        )

    def on_merge_impressions(self) -> None:
        """Merge other selected impressions into the current highlighted person."""
        selected = self._imp_list.selectedItems()
        current = self._imp_list.currentItem()
        ids = [
            str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            for item in selected
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        ]
        ids = list(dict.fromkeys(ids))
        if len(ids) < 2:
            QMessageBox.warning(self, "合并印象", "请至少选中两个档案再合并。")
            return
        if current is None:
            QMessageBox.warning(self, "合并印象", "请先点选要保留的档案（高亮项）。")
            return
        target = str(current.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not target or target not in ids:
            QMessageBox.warning(
                self,
                "合并印象",
                "保留档必须是当前高亮项，且须包含在选中集合内。",
            )
            return
        sources = [pid for pid in ids if pid != target]
        target_label = current.text()
        reply = QMessageBox.question(
            self,
            "确认合并",
            f"将把 {len(sources)} 个档案并入「{target_label}」：\n"
            "正文拼入保留档、别名改挂、源档删除。此操作不可自动撤销，继续？",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            for source in sources:
                store.merge_impressions(source, target)
        except ValueError as exc:
            QMessageBox.warning(self, "合并失败", str(exc))
            self.load_impressions()
            return
        self._imp_key = target
        self.load_impressions()
        QMessageBox.information(
            self,
            "合并印象",
            "已合并。之后旧昵称入站会读写保留档的整合印象。",
        )

    def on_delete_impressions(self) -> None:
        """Delete selected person impressions and all of their aliases."""
        selected = self._imp_list.selectedItems()
        ids = [
            str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            for item in selected
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        ]
        ids = list(dict.fromkeys(ids))
        if not ids:
            QMessageBox.warning(self, "删除印象", "请先选中要删除的档案。")
            return
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"将删除 {len(ids)} 个档案的印象正文与全部别名。不可自动撤销，继续？",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for person_id in ids:
            store.delete_impression_person(person_id)
        if self._imp_key in ids:
            self._imp_key = ""
            self.on_new_impression()
        self.load_impressions()
        QMessageBox.information(self, "删除印象", "已删除所选档案。")

    def on_unlink_impression_aliases(self) -> None:
        """Remove selected alias rows from the current person without deleting the file."""
        person_id = self._imp_key_edit.text().strip() or self._imp_key
        if not person_id:
            QMessageBox.warning(self, "删除别名", "请先选中一个档案。")
            return
        selected = [
            item
            for item in self._imp_alias_list.selectedItems()
            if isinstance(item.data(Qt.ItemDataRole.UserRole), dict)
        ]
        if not selected:
            QMessageBox.warning(self, "删除别名", "请先在别名列表中选中要删除的项。")
            return
        removed = 0
        for item in selected:
            payload = item.data(Qt.ItemDataRole.UserRole)
            chat = str(payload.get("chat") or "").strip()
            nick = str(payload.get("nick") or "").strip()
            if store.unlink_impression_alias(chat, nick):
                removed += 1
        self.load_impressions()
        for i in range(self._imp_list.count()):
            row = self._imp_list.item(i)
            if row is not None and str(row.data(Qt.ItemDataRole.UserRole) or "") == person_id:
                self._imp_list.setCurrentItem(row)
                break
        QMessageBox.information(self, "删除别名", f"已删除 {removed} 个别名。")

    def on_import_personas(self) -> None:
        """Pick text/json files and import them as persona packs."""
        paths, _filter = QFileDialog.getOpenFileNames(
            self,
            "导入人设",
            str(store.personas_dir()),
            "文本/JSON (*.txt *.md *.json);;All (*)",
        )
        if paths:
            self._apply_persona_import([Path(p) for p in paths])

    def _on_persona_files_dropped(self, paths: object) -> None:
        """Handle drag-and-drop onto the persona page."""
        if isinstance(paths, list) and paths:
            self._apply_persona_import([Path(p) for p in paths])

    def _apply_persona_import(self, paths: list[Path]) -> None:
        """Ask for pack id/title when needed, then import and refresh."""
        into = self._persona_id
        files = archive_import.collect_import_paths(paths)
        if not files:
            self._show_import_result("导入人设", 0, 0, ["没有可导入的文本/JSON 文件"])
            return
        overrides: dict[str, tuple[str, str]] = {}
        for path in files:
            if not archive_import.persona_import_needs_identity(
                path, into_persona_id=into
            ):
                continue
            suggested_id, suggested_title = archive_import.default_persona_identity(path)
            result = self._ask_persona_import_identity(
                path.name, suggested_id, suggested_title
            )
            if result is None:
                self._show_import_result(
                    "导入人设", 0, 1, [f"{path.name}: 已取消"]
                )
                return
            overrides[str(path.resolve())] = result
        imported, skipped, errors = archive_import.import_persona_paths(
            files, into_persona_id=into, pack_overrides=overrides
        )
        self.load_personas()
        self._show_import_result("导入人设", imported, skipped, errors)

    def _ask_persona_import_identity(
        self, filename: str, default_id: str, default_title: str
    ) -> tuple[str, str] | None:
        """Dialog: edit persona title and folder id before import. None = cancel."""
        from bot.personas.catalog import normalize_persona_id

        dialog = QDialog(self)
        dialog.setWindowTitle("导入人设")
        form = QFormLayout(dialog)
        hint = QLabel(
            f"文件：{filename}\n"
            "显示名可中文；id 须小写字母/数字/_/-（用作文件夹名）。"
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        form.addRow(hint)
        title_edit = QLineEdit(default_title)
        id_edit = QLineEdit(default_id)
        form.addRow("显示名", title_edit)
        form.addRow("id", id_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        title = title_edit.text().strip() or default_title
        pack_id = normalize_persona_id(id_edit.text())
        if not pack_id:
            QMessageBox.warning(
                self,
                "导入人设",
                "id 不合法。请使用小写字母、数字、下划线或短横线，且以字母/数字开头。",
            )
            return self._ask_persona_import_identity(filename, default_id, title)
        return pack_id, title

    def on_import_impressions(self) -> None:
        """Import impression JSON and/or plain text files."""
        paths, _filter = QFileDialog.getOpenFileNames(
            self,
            "导入印象",
            str(bot_control.project_root()),
            "文本/JSON (*.txt *.md *.json);;JSON (*.json);;All (*)",
        )
        if paths:
            self._apply_impression_import([Path(p) for p in paths])

    def _on_impression_files_dropped(self, paths: object) -> None:
        """Handle drag-and-drop onto the impressions page."""
        if isinstance(paths, list) and paths:
            self._apply_impression_import([Path(p) for p in paths])

    def _apply_impression_import(self, paths: list[Path]) -> None:
        """Run impression import and refresh the list."""
        imported, skipped, errors = archive_import.import_impression_paths(paths)
        self.load_impressions()
        self._show_import_result("导入印象", imported, skipped, errors)

    def on_import_group_cultures(self) -> None:
        """Pick text/json files and import group-culture records."""
        paths, _filter = QFileDialog.getOpenFileNames(
            self,
            "导入群文化",
            str(bot_control.project_root()),
            "文本/JSON (*.txt *.md *.json);;All (*)",
        )
        if paths:
            self._apply_culture_import([Path(p) for p in paths])

    def _on_culture_files_dropped(self, paths: object) -> None:
        """Handle drag-and-drop onto the group-culture page."""
        if isinstance(paths, list) and paths:
            self._apply_culture_import([Path(p) for p in paths])

    def _apply_culture_import(self, paths: list[Path]) -> None:
        """Run group-culture import and refresh the list."""
        imported, skipped, errors = archive_import.import_group_culture_paths(paths)
        self.load_group_cultures()
        self._show_import_result("导入群文化", imported, skipped, errors)

    def _show_import_result(
        self, title: str, imported: int, skipped: int, errors: list[str]
    ) -> None:
        """Show a short import summary dialog."""
        extra = "\n".join(errors[:8])
        if len(errors) > 8:
            extra += f"\n…另有 {len(errors) - 8} 条"
        QMessageBox.information(
            self, title, f"导入 {imported}，跳过 {skipped}。\n{extra}".rstrip()
        )

    def load_transcripts(self) -> None:
        """Refresh chat list from the local inbound_transcript SQLite ring."""
        db = store.transcript_db_path()
        self._transcript_path_label.setText(f"存放位置：{db}（表 inbound_transcript）")
        self._transcript_rows = store.list_transcript_chats()
        self.render_transcript_chats()

    def render_transcript_chats(self) -> None:
        """Fill the transcript chat list, applying the search box."""
        needle = self._transcript_search.text().strip().casefold()
        current = ""
        item = self._transcript_list.currentItem()
        if item is not None:
            current = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self._transcript_list.clear()
        shown = 0
        restore: QListWidgetItem | None = None
        for row in self._transcript_rows:
            chat = str(row.get("chat") or "")
            if not chat:
                continue
            if needle and needle not in chat.casefold():
                continue
            count = int(row.get("count") or 0)
            latest = float(row.get("latest_at") or 0.0)
            stamp = (
                datetime.fromtimestamp(latest).strftime("%m-%d %H:%M")
                if latest > 0
                else "—"
            )
            entry = QListWidgetItem(f"{chat}  ({count} · {stamp})")
            entry.setData(Qt.ItemDataRole.UserRole, chat)
            self._transcript_list.addItem(entry)
            shown += 1
            if chat == current:
                restore = entry
        total = len(self._transcript_rows)
        self._transcript_summary.setText(f"显示 {shown} / 共 {total} 个会话")
        if restore is not None:
            self._transcript_list.setCurrentItem(restore)
        elif shown == 0:
            self._transcript_body.setPlainText(
                "暂无记录。开着 bot 并命中旁听/白名单后，消息会出现在这里。"
                if total == 0
                else "没有匹配的会话名。"
            )

    def on_transcript_selected(self, current: QListWidgetItem | None) -> None:
        """Show bubbles for the selected chat title."""
        if current is None:
            return
        chat = str(current.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not chat:
            return
        self._show_transcript_chat(chat)

    def _reload_selected_transcript(self) -> None:
        """Re-read the open chat when the per-chat limit spinner changes."""
        item = self._transcript_list.currentItem()
        if item is None:
            return
        chat = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if chat:
            self._show_transcript_chat(chat)

    def _show_transcript_chat(self, chat: str) -> None:
        """Render one chat's transcript ring into the read-only pane."""
        limit = int(self._transcript_limit.value())
        rows = store.load_transcript_messages(chat, limit)
        if not rows:
            self._transcript_body.setPlainText(f"「{chat}」暂无本地记录。")
            return
        lines: list[str] = [f"# {chat}（最近 {len(rows)} 条）", ""]
        for row in rows:
            stamp = datetime.fromtimestamp(row.created_at).strftime("%Y-%m-%d %H:%M:%S")
            if row.from_self:
                who = "我"
            else:
                who = row.sender or "对方"
            src = row.source or ""
            head = f"[{stamp}] {who}"
            if src:
                head = f"{head} · {src}"
            lines.append(head)
            lines.append(row.text)
            lines.append("")
        self._transcript_body.setPlainText("\n".join(lines).rstrip() + "\n")

    def _mark_culture_dirty(self) -> None:
        """Remember unsaved group-culture edits."""
        if not self._culture_loading:
            self._culture_dirty = True

    def load_group_cultures(self) -> None:
        """Refresh group-culture records from disk."""
        self._culture_records = store.list_group_cultures()
        self.render_group_cultures()

    def render_group_cultures(self) -> None:
        """Fill the group-culture list, applying the search box."""
        needle = self._culture_search.text().strip().casefold()
        self._culture_list.clear()
        for row in self._culture_records:
            chat = str(row.get("chat") or "")
            label = store.group_culture_display_label(row)
            hay = f"{label} {chat}".casefold()
            if needle and needle not in hay:
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, chat)
            self._culture_list.addItem(item)

    def on_group_culture_selected(self, current: QListWidgetItem | None) -> None:
        """Load one group culture into the form."""
        if current is None:
            return
        chat = str(current.data(Qt.ItemDataRole.UserRole) or "")
        record = store.load_group_culture(chat)
        self._culture_loading = True
        self._culture_chat = chat
        self._culture_editing_new = False
        self._culture_chat_edit.setText(str(record.get("chat") or ""))
        self._culture_chat_edit.setReadOnly(True)
        self._culture_label_edit.setText(str(record.get("label") or ""))
        self._culture_body.setPlainText(str(record.get("culture") or ""))
        self._culture_loading = False
        self._culture_dirty = False

    def on_new_group_culture(self) -> None:
        """Clear the form for a new WeChat chat title."""
        self._culture_editing_new = True
        self._culture_chat = ""
        self._culture_chat_edit.setReadOnly(False)
        self._culture_chat_edit.clear()
        self._culture_label_edit.clear()
        self._culture_body.clear()

    def on_save_group_culture(self) -> None:
        """Write the group-culture form to disk."""
        chat = self._culture_chat_edit.text().strip() or self._culture_chat
        if not chat:
            QMessageBox.warning(self, "缺少会话名", "请填写微信会话名（与会话列表一致）")
            return
        store.save_group_culture(
            chat, self._culture_label_edit.text(), self._culture_body.toPlainText()
        )
        self._culture_chat_edit.setReadOnly(True)
        self._culture_dirty = False
        self.load_group_cultures()

    def on_clear_group_culture(self) -> None:
        """Wipe culture body for the selected chat."""
        chat = self._culture_chat_edit.text().strip() or self._culture_chat
        if not chat:
            return
        store.clear_group_culture(chat)
        self._culture_body.clear()
        self._culture_dirty = False
        self.load_group_cultures()

    def load_prompt_evolution(self) -> None:
        """Reload pending proposals, active overlays, and policy switches."""
        auto, interval = store.prompt_evolution_settings()
        self._evo_auto.setChecked(auto)
        self._evo_interval.setValue(int(interval))
        self._evo_pending_rows = store.list_prompt_evolution_pending()
        self._evo_active_rows = store.list_prompt_evolution_active()
        self._evo_pending_list.clear()
        for row in self._evo_pending_rows:
            chat = str(row.get("chat") or "?")
            created = str(row.get("created_at") or "")
            rationale = str(row.get("rationale") or "").strip()
            title = f"{chat} · {created}"
            if rationale:
                title = f"{title} — {rationale[:40]}"
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, str(row.get("id") or ""))
            self._evo_pending_list.addItem(item)
        self._evo_active_list.clear()
        for row in self._evo_active_rows:
            label = store.prompt_evolution_display_label(row)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(row.get("chat") or ""))
            self._evo_active_list.addItem(item)
        self._evo_pending_id = ""
        self._evo_active_chat = ""
        self._evo_meta.setText("选择左侧待审，或下方已生效项")
        self._evo_rationale.clear()
        self._evo_body.clear()

    def on_prompt_evolution_pending_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        """Show the selected pending proposal in the detail pane."""
        if current is None:
            return
        proposal_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        row = store.load_prompt_evolution_pending(proposal_id)
        if row is None:
            return
        self._evo_pending_id = proposal_id
        self._evo_active_chat = ""
        self._evo_active_list.clearSelection()
        self._evo_meta.setText(
            f"待审 · {row.get('chat') or '?'} · {row.get('created_at') or ''}"
        )
        self._evo_rationale.setPlainText(str(row.get("rationale") or ""))
        body = str(row.get("proposed") or "")
        source = str(row.get("source_turn") or "").strip()
        if source:
            body = f"{body}\n\n——\n本轮摘要：\n{source}"
        self._evo_body.setPlainText(body)

    def on_prompt_evolution_active_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        """Show an approved overlay in the detail pane."""
        if current is None:
            return
        chat = str(current.data(Qt.ItemDataRole.UserRole) or "")
        row = next(
            (r for r in self._evo_active_rows if str(r.get("chat") or "") == chat),
            None,
        )
        if row is None:
            return
        self._evo_active_chat = chat
        self._evo_pending_id = ""
        self._evo_pending_list.clearSelection()
        self._evo_meta.setText(
            f"已生效 · {chat} · {row.get('updated_at') or ''} · from {row.get('from_proposal_id') or '?'}"
        )
        self._evo_rationale.clear()
        self._evo_body.setPlainText(str(row.get("prompt") or ""))

    def on_save_prompt_evolution_policy(self) -> None:
        """Persist auto-proposal switch and interval into reply_policy.toml."""
        store.set_prompt_evolution_auto(self._evo_auto.isChecked())
        store.set_prompt_evolution_min_messages(int(self._evo_interval.value()))
        QMessageBox.information(
            self, "已保存", "进化开关已写入 reply_policy.toml，下一条消息生效。"
        )

    def on_approve_prompt_evolution(self) -> None:
        """Approve the selected pending proposal into an active overlay."""
        proposal_id = self._evo_pending_id
        if not proposal_id:
            QMessageBox.information(self, "未选择", "请先选中一条待审提案。")
            return
        active = store.approve_prompt_evolution(proposal_id)
        if active is None:
            QMessageBox.warning(self, "失败", "批准失败：提案可能已被处理。")
            self.load_prompt_evolution()
            return
        self.load_prompt_evolution()

    def on_reject_prompt_evolution(self) -> None:
        """Delete the selected pending proposal without applying it."""
        proposal_id = self._evo_pending_id
        if not proposal_id:
            QMessageBox.information(self, "未选择", "请先选中一条待审提案。")
            return
        store.reject_prompt_evolution(proposal_id)
        self.load_prompt_evolution()

    def on_disable_prompt_evolution(self) -> None:
        """Remove the selected approved overlay for a group."""
        chat = self._evo_active_chat
        if not chat:
            QMessageBox.information(self, "未选择", "请先在「已生效」列表中选中一项。")
            return
        store.disable_prompt_evolution(chat)
        self.load_prompt_evolution()

    def _save_visible(self) -> None:
        """⌘S / Ctrl+S saves the editor that currently has unsaved work."""
        if self._persona_dirty:
            self.on_save_persona_file()
        if self._policy_dirty:
            self.on_save_policy()
        if self._imp_dirty:
            self.on_save_impression()
        if self._culture_dirty:
            self.on_save_group_culture()
        if self._lists_dirty:
            self.on_save_chat_lists()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Prompt for unsaved chat lists, then stop UI polling and worker threads."""
        if self._lists_dirty:
            answer = QMessageBox.question(
                self,
                "未保存的会话名单",
                "旁听/回复勾选有未保存变更，是否保存？",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                store.write_observe_chat_names(self._checked_observe_names())
                store.write_allow_chat_names(self._checked_allow_names())
                self._lists_dirty = False
        self._poll.stop()
        view = self._view_dialog
        if view is not None:
            view.close()
        self._shutdown_background_threads()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)

    def _shutdown_background_threads(self) -> None:
        """Quit in-flight QThreads so closing the window does not SIGABRT."""
        for attr in (
            "_power_thread",
            "_docker_thread",
            "_account_thread",
            "_qr_thread",
            "_chat_list_thread",
        ):
            thread = getattr(self, attr, None)
            if thread is None:
                continue
            setattr(self, attr, None)
            try:
                if thread.isRunning():
                    thread.quit()
                    if not thread.wait(1500):
                        thread.terminate()
                        thread.wait(500)
            except RuntimeError:
                pass
        self._qr_worker = None


def main() -> None:
    """Launch the native admin application."""
    _configure_qt_paths()
    app = QApplication(sys.argv)
    app.setApplicationName("WeJam 闲聊控制台")
    app.setApplicationDisplayName("WeJam 闲聊控制台")
    app.setOrganizationName("Potatoblock")
    if sys.platform == "darwin":
        app.setStyle("Fusion")
    theme.apply_dark_theme(app)
    if not _acquire_single_instance():
        QMessageBox.information(None, "WeJam 闲聊控制台", "管理窗口已在运行。")
        raise SystemExit(0)
    window = AdminWindow()
    _center_on_screen(window)
    window.showNormal()
    window.raise_()
    window.activateWindow()
    raise SystemExit(app.exec())


def _configure_qt_paths() -> None:
    """Set Qt plugin search path before QApplication starts (macOS)."""
    if sys.platform != "darwin":
        return
    import PyQt6

    plugins = Path(PyQt6.__file__).resolve().parent / "Qt6" / "plugins"
    if plugins.is_dir():
        os.environ.setdefault("QT_PLUGIN_PATH", str(plugins))


def _acquire_single_instance() -> bool:
    """Return False when another admin window is already running."""
    key = "wejam-bot-admin"
    probe = QLocalSocket()
    probe.connectToServer(key)
    if probe.waitForConnected(300):
        probe.close()
        return False
    server = QLocalServer()
    if not server.listen(key):
        server.removeServer(key)
        return server.listen(key)
    _acquire_single_instance._server = server  # type: ignore[attr-defined]
    return True


def _center_on_screen(window: AdminWindow) -> None:
    """Place the window on the primary display so it is not off-screen."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return
    area = screen.availableGeometry()
    frame = window.frameGeometry()
    frame.moveCenter(area.center())
    window.move(frame.topLeft())


def _admin_log(message: str) -> None:
    """Append one admin lifecycle line for desktop.app troubleshooting."""
    log_path = bot_control.project_root() / "data" / "admin-desktop.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


if __name__ == "__main__":
    main()
