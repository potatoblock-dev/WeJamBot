"""Native PyQt6 desktop admin modeled on the 0x01 QQ console."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QCloseEvent, QGuiApplication, QIcon, QKeySequence, QMouseEvent
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
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
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bot.admin import bot_control, env_settings, log_view, status_light, store, theme, wejam_status
from bot.admin.collapsible import CollapsibleSection
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


class AdminWindow(QMainWindow):
    """Main window: run, WeChat login, Bot 设置, API, persona, policy, impressions."""

    def __init__(self) -> None:
        super().__init__()
        self._persona_id = ""
        self._persona_file = ""
        self._persona_dirty = False
        self._persona_loading = False
        self._policy_dirty = False
        self._policy_loading = False
        self._imp_dirty = False
        self._imp_loading = False
        self._imp_key = ""
        self._imp_editing_new = False
        self._records: list[dict] = []
        self._log_clear_armed = False
        self._power_thread: QThread | None = None
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
        self.refresh_status()
        self.refresh_logs()

    def _build_ui(self) -> None:
        """Lay out tabs and controls like the 0x01 console."""
        self.setWindowTitle(status_light.window_title(status_light.LIGHT_OFF))
        icon_path = bot_control.project_root() / "assets" / "app-icon.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(960, 680)

        tabs = QTabWidget()
        tabs.addTab(self._build_run_tab(), "运行")
        tabs.addTab(self._build_login_tab(), "登录")
        tabs.addTab(self._build_features_tab(), "Bot 设置")
        tabs.addTab(self._build_api_tab(), "API")
        tabs.addTab(self._build_persona_tab(), "人设")
        tabs.addTab(self._build_policy_tab(), "回复策略")
        tabs.addTab(self._build_impressions_tab(), "印象")
        self.setCentralWidget(tabs)

        save_act = QAction("保存", self)
        save_act.setShortcut(QKeySequence.StandardKey.Save)
        save_act.triggered.connect(self._save_visible)
        self.addAction(save_act)

    def _build_run_tab(self) -> QWidget:
        """Bot start/stop and log tail."""
        root = QWidget()
        layout = QVBoxLayout(root)
        box = QGroupBox("机器人状态")
        row = QHBoxLayout(box)
        self._status_label = QLabel("检查中…")
        self._status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self._status_label, stretch=1)
        self._btn_toggle = QPushButton("启动")
        self._btn_refresh = QPushButton("刷新")
        row.addWidget(self._btn_toggle)
        row.addWidget(self._btn_refresh)
        layout.addWidget(box)

        self._wejam_hint = QLabel("")
        self._wejam_hint.setWordWrap(True)
        self._wejam_hint.setProperty("role", "warning")
        layout.addWidget(self._wejam_hint)

        hint = QLabel(
            "人设与 reply_policy 保存后下一条消息即生效。"
            "改代码后先点停止再启动。微信容器仍用 bash docker/run.sh。"
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        layout.addWidget(QLabel("消息监控日志"))
        self._log_view = QTextEdit()
        self._log_view.setObjectName("logView")
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._log_view.setPlaceholderText("启动后应出现 inbound → 正在回复 → 正在发送 → replied")
        layout.addWidget(self._log_view, stretch=1)

        log_actions = QHBoxLayout()
        self._btn_log = QPushButton("立即刷新日志")
        self._btn_clear_log = QPushButton("清空日志")
        self._btn_clear_log.setToolTip("按一次进入确认，再按一次清空 data/bot.log")
        log_actions.addWidget(self._btn_log)
        log_actions.addWidget(self._btn_clear_log)
        layout.addLayout(log_actions)
        return root

    def _build_login_tab(self) -> QWidget:
        """WeJam QR login and one-click submit."""
        root = QWidget()
        layout = QVBoxLayout(root)
        hint = QLabel("从 WeJam gRPC 拉取当前登录二维码。容器未启动时这里会失败。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)
        self._login_hint = QLabel("")
        layout.addWidget(self._login_hint)
        self._qr_label = QLabel("尚未拉取二维码")
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setMinimumHeight(280)
        layout.addWidget(self._qr_label, stretch=1)
        row = QHBoxLayout()
        self._btn_qr = QPushButton("刷新二维码")
        self._btn_submit_login = QPushButton("一键登录")
        row.addWidget(self._btn_qr)
        row.addWidget(self._btn_submit_login)
        row.addStretch()
        layout.addLayout(row)
        return root

    def _build_features_tab(self) -> QWidget:
        """Speak-mode picker, whitelist, and folder shortcuts."""
        root = QWidget()
        layout = QVBoxLayout(root)
        speak = QGroupBox("回复模式")
        feat = QVBoxLayout(speak)
        mode_row = QHBoxLayout()
        self._speak_buttons: dict[str, QPushButton] = {}
        self._speak_group = QButtonGroup(self)
        self._speak_group.setExclusive(True)
        tips = {
            "auto": "沿用未点名规则和工作日高峰",
            "all": "白名单会话里每条都尝试回复",
            "mention_quote": "仅在正文出现 bot 名时回复",
        }
        for mode, label in store.speak_mode_options():
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(tips.get(mode, ""))
            self._speak_group.addButton(btn)
            self._speak_buttons[mode] = btn
            mode_row.addWidget(btn)
        feat.addLayout(mode_row)
        feat_hint = QLabel("点选一项即写入 reply_policy.toml，下一条消息生效，不必重启。")
        feat_hint.setWordWrap(True)
        feat_hint.setProperty("role", "muted")
        feat.addWidget(feat_hint)
        layout.addWidget(speak)

        allow = QGroupBox("白名单会话")
        allow_l = QVBoxLayout(allow)
        allow_hint = QLabel("一行一个微信会话名。空则不回复。默认仅文件传输助手。")
        allow_hint.setWordWrap(True)
        allow_hint.setProperty("role", "muted")
        allow_l.addWidget(allow_hint)
        self._allow_edit = QPlainTextEdit()
        self._allow_edit.setMinimumHeight(140)
        allow_l.addWidget(self._allow_edit)
        self._btn_save_allow = QPushButton("保存白名单")
        allow_l.addWidget(self._btn_save_allow, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(allow)

        files = QGroupBox("本地文件")
        file_row = QHBoxLayout(files)
        self._btn_personas_folder = QPushButton("打开人设")
        self._btn_project_folder = QPushButton("打开项目根")
        file_row.addWidget(self._btn_personas_folder)
        file_row.addWidget(self._btn_project_folder)
        layout.addWidget(files)
        layout.addStretch(1)
        return root

    def _build_api_tab(self) -> QWidget:
        """Edit project .env with secrets masked."""
        root = QWidget()
        layout = QVBoxLayout(root)
        hint = QLabel(
            "写入项目根目录 .env（不进 git）。密钥框留空表示保持原值不改写；"
            "保存后需在「运行」页重启闲聊进程才会生效。"
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)
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
        form.addRow("WEJAM_TARGET", self._api_target)
        form.addRow("BOT_NAME", self._api_bot_name)
        form.addRow("LLM_BASE_URL", self._api_llm_base)
        form.addRow("LLM_API_KEY", self._api_llm_key)
        form.addRow("", self._api_key_hint)
        form.addRow("LLM_MODEL", self._api_llm_model)
        form.addRow("LLM_TIMEOUT_SECONDS", self._api_timeout)
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
        """Persona pack list plus one txt editor."""
        root = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        hint = QLabel("点选只用来编辑。点「启用」才切换线上口吻。改 txt 后须保存才会写入文件。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        left_layout.addWidget(hint)
        self._persona_list = QListWidget()
        left_layout.addWidget(self._persona_list)
        pack_actions = QHBoxLayout()
        self._btn_new_persona = QPushButton("新建")
        self._btn_enable_persona = QPushButton("启用")
        self._btn_delete_persona = QPushButton("删除")
        pack_actions.addWidget(self._btn_new_persona)
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
        self._persona_editor.setPlaceholderText("选中人设和文件后编辑…")
        right_layout.addWidget(self._persona_editor, stretch=1)
        self._btn_save_persona_file = QPushButton("保存本文件")
        right_layout.addWidget(self._btn_save_persona_file)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        outer = QVBoxLayout(root)
        outer.addWidget(split)
        return root

    def _build_policy_tab(self) -> QWidget:
        """Scrollable reply-policy page with collapsible guard editors."""
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel("上方改回复开关与频率；下方两块可折叠。保存后下一条消息生效。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)
        switches = QGroupBox("回复开关与频率（reply_policy.toml）")
        switches_layout = QVBoxLayout(switches)
        self._policy = QPlainTextEdit()
        self._policy.setMinimumHeight(220)
        switches_layout.addWidget(self._policy)
        layout.addWidget(switches)

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

    def _build_impressions_tab(self) -> QWidget:
        """Speaker impression list and editor."""
        root = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self._imp_search = QLineEdit()
        self._imp_search.setPlaceholderText("按昵称或会话键搜索…")
        left_layout.addWidget(self._imp_search)
        self._imp_list = QListWidget()
        left_layout.addWidget(self._imp_list)
        imp_actions = QHBoxLayout()
        self._btn_new_imp = QPushButton("新建印象")
        self._btn_import_imp = QPushButton("导入印象")
        imp_actions.addWidget(self._btn_new_imp)
        imp_actions.addWidget(self._btn_import_imp)
        left_layout.addLayout(imp_actions)
        split.addWidget(left)
        right = QWidget()
        form = QFormLayout(right)
        self._imp_key_edit = QLineEdit()
        self._imp_name_edit = QLineEdit()
        self._imp_body = QPlainTextEdit()
        form.addRow("会话键", self._imp_key_edit)
        form.addRow("昵称", self._imp_name_edit)
        form.addRow("印象", self._imp_body)
        self._btn_save_imp = QPushButton("保存印象")
        form.addRow(self._btn_save_imp)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        outer = QVBoxLayout(root)
        outer.addWidget(split)
        return root

    def _wire_actions(self) -> None:
        """Connect buttons and list selection."""
        self._btn_toggle.clicked.connect(self.on_toggle_power)
        self._btn_refresh.clicked.connect(self.refresh_status)
        self._btn_log.clicked.connect(self.refresh_logs)
        self._btn_clear_log.clicked.connect(self.on_clear_logs)
        self._btn_qr.clicked.connect(self._refresh_qr)
        self._btn_submit_login.clicked.connect(self._submit_login)
        for mode, btn in self._speak_buttons.items():
            btn.clicked.connect(lambda checked, m=mode: self.on_select_speak_mode(m, checked))
        self._btn_save_allow.clicked.connect(self.on_save_allow)
        self._btn_personas_folder.clicked.connect(
            lambda: store.reveal_in_file_manager(store.personas_dir())
        )
        self._btn_project_folder.clicked.connect(
            lambda: store.reveal_in_file_manager(bot_control.project_root())
        )
        self._btn_reload_api.clicked.connect(self.load_api_settings)
        self._btn_save_api.clicked.connect(self.on_save_api_settings)
        self._btn_new_persona.clicked.connect(self.on_new_persona)
        self._btn_enable_persona.clicked.connect(self.on_enable_persona)
        self._btn_delete_persona.clicked.connect(self.on_delete_persona)
        self._btn_add_persona_file.clicked.connect(self.on_add_persona_file)
        self._btn_save_persona_file.clicked.connect(self.on_save_persona_file)
        self._persona_list.currentItemChanged.connect(self.on_persona_selected)
        self._persona_file_combo.currentTextChanged.connect(self.on_persona_file_changed)
        self._persona_editor.textChanged.connect(self._mark_persona_dirty)
        self._btn_save_policy.clicked.connect(self.on_save_policy)
        self._policy.textChanged.connect(self._mark_policy_dirty)
        self._policy_anti.textChanged.connect(self._mark_policy_dirty)
        self._policy_stay.textChanged.connect(self._mark_policy_dirty)
        self._btn_save_imp.clicked.connect(self.on_save_impression)
        self._btn_new_imp.clicked.connect(self.on_new_impression)
        self._btn_import_imp.clicked.connect(self.on_import_impressions)
        self._imp_search.textChanged.connect(self.render_impressions)
        self._imp_list.currentItemChanged.connect(self.on_impression_selected)

    def refresh_all(self) -> None:
        """Reload every editor from disk."""
        self.refresh_status()
        self.refresh_logs()
        self.load_api_settings()
        self.load_personas()
        self.load_policy()
        self.load_impressions()
        self._allow_edit.setPlainText("\n".join(store.read_allow_chats()))
        self._sync_speak_buttons()

    def refresh_status(self) -> None:
        """Update run label, WeJam probe, title light, and toggle caption."""
        snap = store.bot_snapshot()
        lines = store.tail_log_lines()
        light = status_light.title_light(
            running=snap.running, healthy=snap.healthy, log_lines=lines
        )
        self.setWindowTitle(status_light.window_title(light))
        state = "运行中" if snap.running else "已停止"
        pid = f" pid={snap.pid}" if snap.pid else ""
        llm = "已配置" if snap.llm_configured else "未配置 LLM_API_KEY"
        self._status_label.setText(f"{state}{pid} · 模型 {snap.llm_model} · {llm}")
        self._btn_toggle.setText("停止" if snap.running else "启动")
        view = wejam_status.probe(snap.wejam_target)
        if view.reachable:
            self._wejam_hint.setText(
                f"WeJam {snap.wejam_target}：可达 ready={view.ready} phase={view.phase}"
            )
            self._wejam_hint.setProperty("role", "accent")
        else:
            self._wejam_hint.setText(
                f"WeJam {snap.wejam_target} 未连通。先 bash docker/run.sh。 {view.error}"
            )
            self._wejam_hint.setProperty("role", "warning")
        self._wejam_hint.style().unpolish(self._wejam_hint)
        self._wejam_hint.style().polish(self._wejam_hint)

    def refresh_logs(self) -> None:
        """Reload filtered runner logs into the monitor view."""
        self._log_view.setHtml(log_view.format_logs_html(store.tail_log_lines()))

    def on_toggle_power(self) -> None:
        """Start or stop the runner on a worker thread."""
        snap = store.bot_snapshot()
        self._power("stop" if snap.running else "start")

    def _power(self, action: str) -> None:
        """Dispatch start/stop without freezing the UI."""
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
            self._btn_clear_log.setText("再按一次确认清空")
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
        """Fetch the current login QR from wejam."""
        from PyQt6.QtGui import QPixmap

        target = load_settings().wejam_target
        try:
            png, revision = wejam_status.fetch_qr_png(target)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "二维码", str(exc))
            return
        if not png:
            self._qr_label.setText("当前没有二维码内容（可能已登录）")
            return
        pix = QPixmap()
        pix.loadFromData(png)
        self._qr_label.setPixmap(pix.scaled(280, 280, Qt.AspectRatioMode.KeepAspectRatio))
        self._login_hint.setText(f"revision={revision or '-'}")

    def _submit_login(self) -> None:
        """Call SubmitLogin on the WeJam driver."""
        try:
            phase = wejam_status.submit_login(load_settings().wejam_target)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "登录", str(exc))
            return
        QMessageBox.information(self, "登录", f"当前阶段：{phase}")

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

    def on_save_allow(self) -> None:
        """Persist the whitelist editor."""
        store.write_allow_chats(self._allow_edit.toPlainText())
        QMessageBox.information(self, "白名单", "已保存。runner 会在下一条消息重读。")

    def load_api_settings(self) -> None:
        """Fill API form from .env (secrets masked)."""
        view = env_settings.read_api_settings()
        self._api_target.setText(view.values.get("WEJAM_TARGET", ""))
        self._api_bot_name.setText(view.values.get("BOT_NAME", ""))
        self._api_llm_base.setText(view.values.get("LLM_BASE_URL", ""))
        self._api_llm_model.setText(view.values.get("LLM_MODEL", ""))
        self._api_timeout.setText(view.values.get("LLM_TIMEOUT_SECONDS", ""))
        self._api_llm_key.setText("")
        mask = view.secret_masks.get("LLM_API_KEY", "")
        self._api_key_hint.setText(f"已保存：{mask}" if mask else "未设置")

    def on_save_api_settings(self) -> None:
        """Write API form values into .env."""
        updates = {
            "WEJAM_TARGET": self._api_target.text(),
            "BOT_NAME": self._api_bot_name.text(),
            "LLM_BASE_URL": self._api_llm_base.text(),
            "LLM_API_KEY": self._api_llm_key.text(),
            "LLM_MODEL": self._api_llm_model.text(),
            "LLM_TIMEOUT_SECONDS": self._api_timeout.text(),
        }
        env_settings.save_api_settings(updates)
        self.load_api_settings()
        QMessageBox.information(self, "API", "已写入 .env。重启闲聊进程后生效。")

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
        """Load reply policy editors from disk."""
        self._policy_loading = True
        body, anti, stay = store.read_reply_policy_parts()
        self._policy.setPlainText(body)
        self._policy_anti.setPlainText(anti)
        self._policy_stay.setPlainText(stay)
        self._policy_loading = False
        self._policy_dirty = False
        self._sync_speak_buttons()

    def _mark_policy_dirty(self) -> None:
        """Remember unsaved policy edits."""
        if not self._policy_loading:
            self._policy_dirty = True

    def on_save_policy(self) -> None:
        """Write reply_policy.toml from the three editors."""
        store.save_reply_policy_parts(
            self._policy.toPlainText(),
            self._policy_anti.toPlainText(),
            self._policy_stay.toPlainText(),
        )
        self._policy_dirty = False
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
        self._imp_list.clear()
        for row in self._records:
            key = str(row.get("user_key") or "")
            name = str(row.get("username") or "")
            hay = f"{name} {key}".casefold()
            if needle and needle not in hay:
                continue
            item = QListWidgetItem(name or key)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._imp_list.addItem(item)

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
        self._imp_body.setPlainText(str(record.get("impression") or ""))
        self._imp_loading = False
        self._imp_dirty = False

    def on_new_impression(self) -> None:
        """Clear the form for a new session key."""
        self._imp_editing_new = True
        self._imp_key = ""
        self._imp_key_edit.setReadOnly(False)
        self._imp_key_edit.clear()
        self._imp_name_edit.clear()
        self._imp_body.clear()

    def on_save_impression(self) -> None:
        """Write the impression form to disk."""
        key = self._imp_key_edit.text().strip() or self._imp_key
        if not key:
            QMessageBox.warning(self, "缺少会话键", "请填写会话键，例如 文件传输助手 或 群名:昵称")
            return
        store.save_impression(key, self._imp_name_edit.text(), self._imp_body.toPlainText())
        self._imp_key_edit.setReadOnly(True)
        self._imp_dirty = False
        self.load_impressions()

    def on_import_impressions(self) -> None:
        """Import one or more impression JSON files."""
        paths, _filter = QFileDialog.getOpenFileNames(
            self, "导入印象 JSON", str(bot_control.project_root()), "JSON (*.json)"
        )
        if not paths:
            return
        imported, skipped, errors = store.import_impression_files([Path(p) for p in paths])
        self.load_impressions()
        extra = "\n".join(errors[:8])
        QMessageBox.information(
            self, "导入印象", f"导入 {imported}，跳过 {skipped}。\n{extra}"
        )

    def _save_visible(self) -> None:
        """⌘S / Ctrl+S saves the editor that currently has unsaved work."""
        if self._persona_dirty:
            self.on_save_persona_file()
        if self._policy_dirty:
            self.on_save_policy()
        if self._imp_dirty:
            self.on_save_impression()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop polling and stop the runner when the window closes."""
        self._poll.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        try:
            if store.bot_snapshot().running:
                store.stop_bot()
        except Exception as exc:  # noqa: BLE001
            _admin_log(f"stop on close failed: {exc!r}")
        super().closeEvent(event)


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
