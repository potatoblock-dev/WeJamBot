"""Native PyQt6 desktop admin for the WeJam host-side chat bot."""

from __future__ import annotations

import sys

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bot.admin import env_settings, log_view, store, theme, wejam_status
from bot.config import load_settings


class _ScriptWorker(QObject):
    """Run start/stop off the UI thread."""

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
    """Main window: run, login QR, whitelist, API, persona, policy, impressions."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WeJam 闲聊控制台")
        self.resize(980, 720)
        self._power_thread: QThread | None = None
        self._persona_id = ""
        self._persona_file = ""
        self._imp_key = ""
        tabs = QTabWidget()
        tabs.addTab(self._build_run_tab(), "运行")
        tabs.addTab(self._build_login_tab(), "登录")
        tabs.addTab(self._build_bot_tab(), "Bot")
        tabs.addTab(self._build_api_tab(), "API")
        tabs.addTab(self._build_persona_tab(), "人设")
        tabs.addTab(self._build_policy_tab(), "回复策略")
        tabs.addTab(self._build_impression_tab(), "印象")
        self.setCentralWidget(tabs)
        self._refresh_run()
        self._reload_personas()
        self._reload_policy()
        self._reload_impressions()
        self._reload_allow()
        self._reload_api()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_run)
        self._timer.start(2000)

    def _build_run_tab(self) -> QWidget:
        """Start/stop runner, WeJam probe, and log tail."""
        page = QWidget()
        layout = QVBoxLayout(page)
        self._run_label = QLabel()
        self._wejam_label = QLabel()
        row = QHBoxLayout()
        self._start_btn = QPushButton("启动")
        self._stop_btn = QPushButton("停止")
        self._start_btn.clicked.connect(lambda: self._power("start"))
        self._stop_btn.clicked.connect(lambda: self._power("stop"))
        row.addWidget(self._start_btn)
        row.addWidget(self._stop_btn)
        row.addStretch()
        self._log_view = QTextEdit()
        self._log_view.setObjectName("logView")
        self._log_view.setReadOnly(True)
        clear = QPushButton("清空日志")
        clear.clicked.connect(self._clear_logs)
        layout.addWidget(self._run_label)
        layout.addWidget(self._wejam_label)
        layout.addLayout(row)
        layout.addWidget(self._log_view, 1)
        layout.addWidget(clear, alignment=Qt.AlignmentFlag.AlignLeft)
        return page

    def _build_login_tab(self) -> QWidget:
        """Show WeJam login QR and one-click submit."""
        page = QWidget()
        layout = QVBoxLayout(page)
        self._qr_label = QLabel("尚未拉取二维码")
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setMinimumHeight(280)
        self._login_hint = QLabel()
        refresh = QPushButton("刷新二维码")
        submit = QPushButton("一键登录")
        refresh.clicked.connect(self._refresh_qr)
        submit.clicked.connect(self._submit_login)
        layout.addWidget(self._login_hint)
        layout.addWidget(self._qr_label, 1)
        row = QHBoxLayout()
        row.addWidget(refresh)
        row.addWidget(submit)
        row.addStretch()
        layout.addLayout(row)
        return page

    def _build_bot_tab(self) -> QWidget:
        """Whitelist chats and group speak mode."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("白名单会话（一行一个；空则不回复。默认仅文件传输助手）"))
        self._allow_edit = QPlainTextEdit()
        layout.addWidget(self._allow_edit, 1)
        save = QPushButton("保存白名单")
        save.clicked.connect(self._save_allow)
        self._speak_btn = QPushButton()
        self._speak_btn.clicked.connect(self._cycle_speak)
        row = QHBoxLayout()
        row.addWidget(save)
        row.addWidget(self._speak_btn)
        row.addStretch()
        layout.addLayout(row)
        return page

    def _build_api_tab(self) -> QWidget:
        """Edit LLM / wejam .env keys with secrets masked."""
        page = QWidget()
        form = QFormLayout(page)
        self._api_fields: dict[str, QLineEdit] = {}
        labels = {
            "WEJAM_TARGET": "WeJam 地址",
            "BOT_NAME": "Bot 点名",
            "LLM_BASE_URL": "LLM Base URL",
            "LLM_API_KEY": "LLM API Key",
            "LLM_MODEL": "LLM 模型",
            "LLM_TIMEOUT_SECONDS": "超时秒",
        }
        for key in env_settings.ALL_FORM_KEYS:
            field = QLineEdit()
            if key in env_settings.SECRET_KEYS:
                field.setEchoMode(QLineEdit.EchoMode.Password)
            self._api_fields[key] = field
            form.addRow(labels.get(key, key), field)
        save = QPushButton("保存 .env")
        save.clicked.connect(self._save_api)
        form.addRow(save)
        return page

    def _build_persona_tab(self) -> QWidget:
        """Select and edit persona txt packs."""
        page = QWidget()
        split = QSplitter()
        left = QWidget()
        left_l = QVBoxLayout(left)
        self._persona_list = QListWidget()
        self._persona_list.currentItemChanged.connect(self._on_persona_pack)
        left_l.addWidget(self._persona_list, 1)
        enable = QPushButton("启用选中")
        enable.clicked.connect(self._enable_persona)
        add = QPushButton("新建人设")
        add.clicked.connect(self._new_persona)
        left_l.addWidget(enable)
        left_l.addWidget(add)
        right = QWidget()
        right_l = QVBoxLayout(right)
        self._persona_files = QListWidget()
        self._persona_files.currentItemChanged.connect(self._on_persona_file)
        self._persona_edit = QPlainTextEdit()
        save = QPushButton("保存当前 txt")
        save.clicked.connect(self._save_persona_file)
        right_l.addWidget(self._persona_files)
        right_l.addWidget(self._persona_edit, 1)
        right_l.addWidget(save)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 2)
        wrap = QVBoxLayout(page)
        wrap.addWidget(split)
        return page

    def _build_policy_tab(self) -> QWidget:
        """Edit reply_policy.toml frequency body and prompt guards."""
        page = QWidget()
        layout = QVBoxLayout(page)
        self._policy_body = QPlainTextEdit()
        self._policy_anti = QPlainTextEdit()
        self._policy_stay = QPlainTextEdit()
        layout.addWidget(QLabel("开关与频率"))
        layout.addWidget(self._policy_body, 2)
        layout.addWidget(QLabel("防注入（一行一条）"))
        layout.addWidget(self._policy_anti, 1)
        layout.addWidget(QLabel("不得脱离提示词（一行一条）"))
        layout.addWidget(self._policy_stay, 1)
        save = QPushButton("保存策略")
        save.clicked.connect(self._save_policy)
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignLeft)
        return page

    def _build_impression_tab(self) -> QWidget:
        """Browse and edit impression JSON records."""
        page = QWidget()
        split = QSplitter()
        self._imp_list = QListWidget()
        self._imp_list.currentItemChanged.connect(self._on_impression)
        right = QWidget()
        form = QFormLayout(right)
        self._imp_key_edit = QLineEdit()
        self._imp_name_edit = QLineEdit()
        self._imp_body = QPlainTextEdit()
        form.addRow("会话键", self._imp_key_edit)
        form.addRow("昵称", self._imp_name_edit)
        form.addRow("印象", self._imp_body)
        save = QPushButton("保存印象")
        save.clicked.connect(self._save_impression)
        form.addRow(save)
        split.addWidget(self._imp_list)
        split.addWidget(right)
        wrap = QVBoxLayout(page)
        wrap.addWidget(split)
        return page

    def _power(self, action: str) -> None:
        """Start or stop the runner on a worker thread."""
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        thread = QThread(self)
        worker = _ScriptWorker(action)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(lambda err: self._power_done(err, thread, worker))
        self._power_thread = thread
        thread.start()

    def _power_done(self, err: object, thread: QThread, worker: _ScriptWorker) -> None:
        """Re-enable buttons after start/stop finishes."""
        thread.quit()
        thread.wait(2000)
        worker.deleteLater()
        thread.deleteLater()
        self._power_thread = None
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        if err is not None:
            QMessageBox.warning(self, "启停失败", str(err))
        self._refresh_run()

    def _refresh_run(self) -> None:
        """Refresh process status, WeJam probe, and logs."""
        snap = store.bot_snapshot()
        state = "运行中" if snap.running else "已停止"
        pid = f" pid={snap.pid}" if snap.pid else ""
        llm = "已配置" if snap.llm_configured else "未配置 LLM_API_KEY"
        self._run_label.setText(f"闲聊进程：{state}{pid} · 模型 {snap.llm_model} · {llm}")
        view = wejam_status.probe(snap.wejam_target)
        if view.reachable:
            self._wejam_label.setText(
                f"WeJam {snap.wejam_target}：可达 ready={view.ready} "
                f"phase={view.phase} version={view.version or '-'}"
            )
        else:
            self._wejam_label.setText(
                f"WeJam {snap.wejam_target}：未连通（先 bash docker/run.sh） {view.error}"
            )
        self._log_view.setHtml(log_view.format_logs_html(store.tail_log_lines()))

    def _clear_logs(self) -> None:
        """Truncate the runner log file."""
        store.clear_monitor_logs()
        self._refresh_run()

    def _refresh_qr(self) -> None:
        """Fetch the current login QR from wejam."""
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
        self._qr_label.setPixmap(
            pix.scaled(280, 280, Qt.AspectRatioMode.KeepAspectRatio)
        )
        self._login_hint.setText(f"revision={revision or '-'}")

    def _submit_login(self) -> None:
        """Call SubmitLogin on the WeJam driver."""
        try:
            phase = wejam_status.submit_login(load_settings().wejam_target)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "登录", str(exc))
            return
        QMessageBox.information(self, "登录", f"当前阶段：{phase}")

    def _reload_allow(self) -> None:
        """Load whitelist and speak-mode button label."""
        self._allow_edit.setPlainText("\n".join(store.read_allow_chats()))
        mode = store.read_speak_mode()
        label = dict(store.speak_mode_options()).get(mode, mode)
        self._speak_btn.setText(f"群发言模式：{label}")

    def _save_allow(self) -> None:
        """Persist the whitelist editor."""
        store.write_allow_chats(self._allow_edit.toPlainText())
        QMessageBox.information(self, "白名单", "已保存。正在运行的 runner 会在下一条消息重读。")

    def _cycle_speak(self) -> None:
        """Advance speak_mode in reply_policy.toml."""
        store.cycle_speak_mode()
        self._reload_allow()
        self._reload_policy()

    def _reload_api(self) -> None:
        """Fill API form from .env (secrets masked)."""
        view = env_settings.read_api_settings()
        for key, field in self._api_fields.items():
            if key in env_settings.SECRET_KEYS:
                field.setPlaceholderText(view.secret_masks.get(key, "") or "未设置")
                field.setText("")
            else:
                field.setText(view.values.get(key, ""))

    def _save_api(self) -> None:
        """Write API form values into .env."""
        updates = {key: field.text() for key, field in self._api_fields.items()}
        env_settings.save_api_settings(updates)
        QMessageBox.information(self, "API", "已写入 .env。重启闲聊进程后 LLM 配置生效。")
        self._reload_api()

    def _reload_personas(self) -> None:
        """Refresh the persona pack list."""
        self._persona_list.clear()
        for row in store.list_persona_packs():
            mark = "● " if row.get("active") else ""
            item = QListWidgetItem(f"{mark}{row['id']}  {row['title']}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self._persona_list.addItem(item)

    def _on_persona_pack(self, current: QListWidgetItem | None) -> None:
        """Show txt files for the selected pack."""
        self._persona_files.clear()
        self._persona_edit.clear()
        if current is None:
            self._persona_id = ""
            return
        self._persona_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        for name in store.list_persona_files(self._persona_id):
            self._persona_files.addItem(name)

    def _on_persona_file(self, current: QListWidgetItem | None) -> None:
        """Load one txt into the editor."""
        if current is None or not self._persona_id:
            return
        self._persona_file = current.text()
        self._persona_edit.setPlainText(
            store.read_persona_file(self._persona_id, self._persona_file)
        )

    def _save_persona_file(self) -> None:
        """Write the current persona txt."""
        if not self._persona_id or not self._persona_file:
            return
        store.save_persona_file(
            self._persona_id, self._persona_file, self._persona_edit.toPlainText()
        )

    def _enable_persona(self) -> None:
        """Mark the selected pack as active."""
        if not self._persona_id:
            return
        store.set_persona_active(self._persona_id)
        self._reload_personas()

    def _new_persona(self) -> None:
        """Create a pack named from a simple dialog-less default counter."""
        existing = {row["id"] for row in store.list_persona_packs()}
        index = 1
        while f"pack{index}" in existing:
            index += 1
        store.new_persona_pack(f"pack{index}", f"人设{index}")
        self._reload_personas()

    def _reload_policy(self) -> None:
        """Load reply policy editors from disk."""
        body, anti, stay = store.read_reply_policy_parts()
        self._policy_body.setPlainText(body)
        self._policy_anti.setPlainText(anti)
        self._policy_stay.setPlainText(stay)

    def _save_policy(self) -> None:
        """Write reply_policy.toml from the three editors."""
        store.save_reply_policy_parts(
            self._policy_body.toPlainText(),
            self._policy_anti.toPlainText(),
            self._policy_stay.toPlainText(),
        )
        QMessageBox.information(self, "策略", "已保存。下一条消息生效。")

    def _reload_impressions(self) -> None:
        """Refresh the impression list."""
        self._imp_list.clear()
        for row in store.list_impressions():
            key = str(row.get("user_key") or "")
            name = str(row.get("username") or "")
            item = QListWidgetItem(f"{name or key}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._imp_list.addItem(item)

    def _on_impression(self, current: QListWidgetItem | None) -> None:
        """Load one impression into the form."""
        if current is None:
            return
        key = str(current.data(Qt.ItemDataRole.UserRole) or "")
        record = store.load_impression(key)
        self._imp_key = key
        self._imp_key_edit.setText(str(record.get("user_key") or ""))
        self._imp_name_edit.setText(str(record.get("username") or ""))
        self._imp_body.setPlainText(str(record.get("impression") or ""))

    def _save_impression(self) -> None:
        """Write the impression form to disk."""
        key = self._imp_key_edit.text().strip() or self._imp_key
        if not key:
            return
        store.save_impression(
            key, self._imp_name_edit.text(), self._imp_body.toPlainText()
        )
        self._reload_impressions()


def main() -> int:
    """Launch the desktop admin window."""
    app = QApplication(sys.argv)
    theme.apply_dark_theme(app)
    window = AdminWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
