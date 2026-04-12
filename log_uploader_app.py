"""
Solo PIXI Module Test — Log Uploader App
Upload QCA9377 BT+WiFi production test logs directly to PostgreSQL.
macOS Application Dark Mode UI.
"""
import sys
import os

# Add solo-pixi-essential to path so we can import module_log_parser
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'solo-pixi-essential'))

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLineEdit, QLabel,
                             QFileDialog, QTextEdit, QGroupBox, QGridLayout,
                             QMessageBox, QProgressBar, QListWidget,
                             QAbstractItemView, QFrame, QSizePolicy)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont

APP_VERSION = "1.1.0"
DB_CONNECT_TIMEOUT = 3
DB_HEARTBEAT_INTERVAL_MS = 15_000  # 15 seconds

# ========================================================
# macOS Ventura / Sonoma Dark Mode Stylesheet
# ========================================================
MAC_DARK_STYLESHEET = """
QMainWindow {
    background-color: #1e1e1e;
}
QWidget {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
                 "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #f5f5f7;
    font-size: 22px;
}

/* ─── GroupBox ─────────────────────────────────────────── */
QGroupBox {
    background-color: #2a2a2c;
    border: 1px solid #3a3a3c;
    border-radius: 14px;
    margin-top: 10px;
    padding: 20px 18px 14px 18px;
    font-weight: 600;
    font-size: 22px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    color: #e5e5ea;
    font-weight: 600;
    font-size: 22px;
}

/* ─── LineEdit ─────────────────────────────────────────── */
QLineEdit {
    border: 1px solid #48484a;
    border-radius: 8px;
    padding: 8px 14px;
    background-color: #3a3a3c;
    selection-background-color: #0a84ff;
    color: #f5f5f7;
    font-size: 22px;
}
QLineEdit:focus {
    border: 2px solid #0a84ff;
    padding: 7px 13px;
}
QLineEdit[readOnly="true"] {
    background-color: #2c2c2e;
    color: #98989d;
}

/* ─── Primary Button (Blue) ────────────────────────────── */
QPushButton {
    background-color: #0a84ff;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 9px 22px;
    font-weight: 600;
    font-size: 22px;
}
QPushButton:hover {
    background-color: #409cff;
}
QPushButton:pressed {
    background-color: #0071e3;
}
QPushButton:disabled {
    background-color: #1c3a5e;
    color: #636366;
}

/* ─── Secondary Button ─────────────────────────────────── */
QPushButton#secondary {
    background-color: #48484a;
    color: #f5f5f7;
    border: none;
}
QPushButton#secondary:hover {
    background-color: #636366;
}
QPushButton#secondary:pressed {
    background-color: #3a3a3c;
}

/* ─── Danger Button (Red) ──────────────────────────────── */
QPushButton#danger {
    background-color: #ff453a;
    color: white;
    border: none;
}
QPushButton#danger:hover {
    background-color: #ff6961;
}
QPushButton#danger:pressed {
    background-color: #d70015;
}
QPushButton#danger:disabled {
    background-color: #3a1a18;
    color: #636366;
}

/* ─── Console ──────────────────────────────────────────── */
QTextEdit {
    border: 1px solid #3a3a3c;
    border-radius: 10px;
    background-color: #161617;
    color: #a1a1a6;
    padding: 12px;
    font-family: "SF Mono", "Cascadia Code", "Menlo", Consolas, monospace;
    font-size: 20px;
    selection-background-color: #0a84ff;
}

/* ─── ListWidget ───────────────────────────────────────── */
QListWidget {
    border: 1px solid #3a3a3c;
    border-radius: 10px;
    background-color: #2c2c2e;
    color: #f5f5f7;
    padding: 6px;
    font-size: 20px;
    font-family: "SF Mono", "Cascadia Code", "Menlo", Consolas, monospace;
}
QListWidget::item {
    padding: 5px 10px;
    border-radius: 6px;
}
QListWidget::item:selected {
    background-color: #0a84ff;
    color: white;
}
QListWidget::item:hover {
    background-color: #3a3a3c;
}

/* ─── ProgressBar ──────────────────────────────────────── */
QProgressBar {
    border: none;
    border-radius: 5px;
    background-color: #48484a;
    text-align: center;
    color: #f5f5f7;
    font-size: 18px;
    min-height: 10px;
    max-height: 10px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #30d158, stop:1 #34c759);
    border-radius: 5px;
}

/* ─── Label ────────────────────────────────────────────── */
QLabel {
    font-size: 22px;
    color: #f5f5f7;
    background: transparent;
}

/* ─── Separator ────────────────────────────────────────── */
QFrame#separator {
    background-color: #48484a;
    max-height: 1px;
}
"""


# ========================================================
# Upload Worker Thread
# ========================================================
class UploadWorkerThread(QThread):
    progress = pyqtSignal(int)
    stats = pyqtSignal(dict)
    log = pyqtSignal(str)
    finished_signal = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, dsn, file_paths):
        super().__init__()
        self.dsn = dsn
        self.file_paths = file_paths
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            import psycopg2
        except ImportError:
            self.error.emit("缺少 psycopg2 模組。請執行: pip install psycopg2-binary")
            return

        try:
            import module_log_parser as parser
        except ImportError:
            self.error.emit("無法載入 module_log_parser.py，請確認 solo-pixi-essential/ 資料夾存在。")
            return

        try:
            conn = psycopg2.connect(self.dsn, connect_timeout=DB_CONNECT_TIMEOUT)
            self.log.emit("✓ Database connected")
        except Exception as e:
            self.error.emit(f"Database connection failed: {e}")
            return

        total = len(self.file_paths)
        st = {'queued': total, 'uploaded': 0, 'skipped': 0, 'failed': 0}
        self.stats.emit(dict(st))

        for i, fpath in enumerate(self.file_paths):
            if self._stop:
                self.log.emit("⛔ 使用者中止上傳")
                break

            fname = os.path.basename(fpath)
            try:
                rec = parser.parse_log_file(fpath)
                inserted = parser.insert_record(conn, rec)
                if inserted:
                    st['uploaded'] += 1
                    self.log.emit(f"  [{i+1}/{total}] ✓ {fname} → 已上傳")
                else:
                    st['skipped'] += 1
                    self.log.emit(f"  [{i+1}/{total}] ⏭ {fname} → 已跳過 (重複)")
            except Exception as e:
                st['failed'] += 1
                self.log.emit(f"  [{i+1}/{total}] ✗ {fname} → 錯誤: {e}")

            if (i + 1) % 10 == 0:
                try:
                    conn.commit()
                except Exception:
                    pass

            pct = int((i + 1) / total * 100)
            self.progress.emit(pct)
            self.stats.emit(dict(st))

        try:
            conn.commit()
            conn.close()
        except Exception:
            pass

        summary = (f"上傳完成！共 {total} 筆 — "
                   f"上傳: {st['uploaded']}  跳過: {st['skipped']}  失敗: {st['failed']}")
        self.finished_signal.emit(summary)


# ========================================================
# Main Window
# ========================================================
class LogUploaderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self._db_heartbeat_timer = None
        self.initUI()
        # Auto-check DB on startup
        QTimer.singleShot(300, self._run_db_connection_check)
        self._start_db_heartbeat()

    def initUI(self):
        self.setWindowTitle(f'Solo PIXI — Log Uploader  (v{APP_VERSION})')
        self.setMinimumSize(1020, 920)
        self.setStyleSheet(MAC_DARK_STYLESHEET)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        layout = QVBoxLayout()
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(14)
        main_widget.setLayout(layout)

        # ── Title Bar + DB Status Indicator ───────────────────────
        title_row = QHBoxLayout()

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_lbl = QLabel("Solo PIXI — Log Uploader")
        title_lbl.setStyleSheet(
            "color: #f5f5f7; font-size: 33px; font-weight: 700; letter-spacing: -0.4px;"
        )
        subtitle_lbl = QLabel("QCA9377 BT+WiFi Module Test")
        subtitle_lbl.setStyleSheet("color: #98989d; font-size: 21px; font-weight: 400;")
        title_col.addWidget(title_lbl)
        title_col.addWidget(subtitle_lbl)
        title_row.addLayout(title_col)
        title_row.addStretch()

        # ── Database Online / Offline badge ───────────────────────
        self.db_status_dot = QLabel("●")
        self.db_status_dot.setStyleSheet("color: #636366; font-size: 22px;")
        self.db_status_label = QLabel("Database Offline")
        self.db_status_label.setStyleSheet(
            "color: #636366; font-size: 22px; font-weight: 600;"
        )
        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)
        badge_row.addWidget(self.db_status_dot)
        badge_row.addWidget(self.db_status_label)
        title_row.addLayout(badge_row)

        layout.addLayout(title_row)

        # Separator
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # ════════════════════════════════════════════════════════
        # 1. Database Connection (QGridLayout)
        # ════════════════════════════════════════════════════════
        conn_group = QGroupBox("Database Connection")
        conn_grid = QGridLayout()
        conn_grid.setSpacing(12)
        conn_grid.setContentsMargins(14, 20, 14, 14)

        lbl_host = QLabel("Host:")
        lbl_port = QLabel("Port:")
        lbl_db   = QLabel("Database:")
        lbl_user = QLabel("User:")
        lbl_pw   = QLabel("Password:")
        for lbl in [lbl_host, lbl_port, lbl_db, lbl_user, lbl_pw]:
            lbl.setStyleSheet("color: #98989d; font-size: 21px;")

        self.inp_host = QLineEdit("localhost")
        self.inp_port = QLineEdit("5433")
        self.inp_db   = QLineEdit("pixi_test")
        self.inp_user = QLineEdit("pixi")
        self.inp_pass = QLineEdit("pixipass")
        self.inp_pass.setEchoMode(QLineEdit.Password)
        for inp in [self.inp_host, self.inp_port, self.inp_db, self.inp_user, self.inp_pass]:
            inp.setMinimumHeight(36)
            inp.textChanged.connect(self._on_conn_changed)

        conn_grid.addWidget(lbl_host, 0, 0)
        conn_grid.addWidget(self.inp_host, 0, 1)
        conn_grid.addWidget(lbl_port, 0, 2)
        self.inp_port.setMaximumWidth(90)
        conn_grid.addWidget(self.inp_port, 0, 3)

        conn_grid.addWidget(lbl_db, 1, 0)
        conn_grid.addWidget(self.inp_db, 1, 1)
        conn_grid.addWidget(lbl_user, 1, 2)
        conn_grid.addWidget(self.inp_user, 1, 3)

        conn_grid.addWidget(lbl_pw, 2, 0)
        self.inp_pass.setMaximumWidth(220)
        conn_grid.addWidget(self.inp_pass, 2, 1)

        self.btn_test_conn = QPushButton("Test Connection")
        self.btn_test_conn.setObjectName("secondary")
        self.btn_test_conn.setMinimumHeight(36)
        self.btn_test_conn.clicked.connect(self.test_connection)
        conn_grid.addWidget(self.btn_test_conn, 2, 2, 1, 2)

        self.lbl_conn_detail = QLabel("DB connection: checking...")
        self.lbl_conn_detail.setStyleSheet("color: #636366; font-size: 20px;")
        conn_grid.addWidget(self.lbl_conn_detail, 3, 0, 1, 4)

        conn_group.setLayout(conn_grid)
        layout.addWidget(conn_group)

        # ════════════════════════════════════════════════════════
        # 2. Log Files
        # ════════════════════════════════════════════════════════
        file_group = QGroupBox("Log Files")
        file_layout = QVBoxLayout()
        file_layout.setContentsMargins(14, 20, 14, 14)
        file_layout.setSpacing(10)

        # Folder row
        folder_row = QHBoxLayout()
        folder_row.setSpacing(10)
        lbl_folder = QLabel("Log Folder:")
        lbl_folder.setStyleSheet("color: #98989d; font-size: 21px;")
        self.inp_folder_display = QLineEdit()
        self.inp_folder_display.setReadOnly(True)
        self.inp_folder_display.setPlaceholderText("Select folder containing .txt log files")
        self.inp_folder_display.setMinimumHeight(36)
        self.btn_browse_folder = QPushButton("Browse")
        self.btn_browse_folder.setObjectName("secondary")
        self.btn_browse_folder.setMinimumHeight(36)
        self.btn_browse_folder.clicked.connect(self.browse_folder)
        folder_row.addWidget(lbl_folder)
        folder_row.addWidget(self.inp_folder_display, 1)
        folder_row.addWidget(self.btn_browse_folder)
        file_layout.addLayout(folder_row)

        # Buttons + count
        list_btn_row = QHBoxLayout()
        list_btn_row.setSpacing(10)
        self.btn_add_files = QPushButton("Add Files")
        self.btn_add_files.setObjectName("secondary")
        self.btn_add_files.clicked.connect(self.add_files)
        self.btn_clear_files = QPushButton("Clear")
        self.btn_clear_files.setObjectName("secondary")
        self.btn_clear_files.clicked.connect(self.clear_files)
        self.lbl_file_count = QLabel("0 files selected")
        self.lbl_file_count.setStyleSheet("color: #98989d; font-size: 21px; font-weight: 500;")
        list_btn_row.addWidget(self.btn_add_files)
        list_btn_row.addWidget(self.btn_clear_files)
        list_btn_row.addStretch()
        list_btn_row.addWidget(self.lbl_file_count)
        file_layout.addLayout(list_btn_row)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setMinimumHeight(90)
        self.file_list.setMaximumHeight(140)
        file_layout.addWidget(self.file_list)

        file_group.setLayout(file_layout)
        layout.addWidget(file_group)

        # ════════════════════════════════════════════════════════
        # 3. Upload Progress & Stats
        # ════════════════════════════════════════════════════════
        progress_group = QGroupBox("Upload")
        progress_layout = QVBoxLayout()
        progress_layout.setContentsMargins(14, 20, 14, 14)
        progress_layout.setSpacing(12)

        # Action buttons
        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        self.btn_start = QPushButton("Upload to DB")
        self.btn_start.setMinimumHeight(42)
        self.btn_start.setMinimumWidth(180)
        self.btn_start.clicked.connect(self.start_upload)
        self.btn_start.setEnabled(False)

        self.btn_stop = QPushButton("Cancel")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setMinimumHeight(42)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_upload)

        self.btn_clear_stats = QPushButton("Clear Stats")
        self.btn_clear_stats.setObjectName("secondary")
        self.btn_clear_stats.setMinimumHeight(42)
        self.btn_clear_stats.clicked.connect(self.clear_stats)

        action_row.addWidget(self.btn_start)
        action_row.addWidget(self.btn_stop)
        action_row.addStretch()
        action_row.addWidget(self.btn_clear_stats)
        progress_layout.addLayout(action_row)

        # Thin progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        progress_layout.addWidget(self.progress_bar)

        # Stat cards
        stats_row = QHBoxLayout()
        stats_row.setSpacing(14)
        self.stat_cards = {}
        for key, label, color in [
            ('queued',   'Queued',   '#98989d'),
            ('uploaded', 'Uploaded', '#30d158'),
            ('skipped',  'Skipped',  '#ff9f0a'),
            ('failed',   'Failed',   '#ff453a'),
        ]:
            card = self._build_stat_card(label, "0", color)
            stats_row.addWidget(card['frame'])
            self.stat_cards[key] = card
        progress_layout.addLayout(stats_row)

        # Upload status
        self.lbl_upload_status = QLabel("Upload status: idle")
        self.lbl_upload_status.setStyleSheet("color: #636366; font-size: 20px;")
        progress_layout.addWidget(self.lbl_upload_status)

        # Duplicate policy
        policy_lbl = QLabel(
            "Duplicate policy: SKIP (same file_hash will not overwrite existing DB row)"
        )
        policy_lbl.setStyleSheet("color: #48484a; font-size: 18px;")
        progress_layout.addWidget(policy_lbl)

        progress_group.setLayout(progress_layout)
        layout.addWidget(progress_group)

        # ════════════════════════════════════════════════════════
        # 4. Console Log
        # ════════════════════════════════════════════════════════
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setPlaceholderText("Upload log will appear here...")
        self.console.setMinimumHeight(90)
        self.console.setMaximumHeight(160)
        layout.addWidget(self.console)

    # ─── Stat Card Builder ───────────────────────────────────
    def _build_stat_card(self, label, value, color):
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #3a3a3c;
                border-radius: 12px;
            }
        """)
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        frame.setFixedHeight(88)

        vl = QVBoxLayout(frame)
        vl.setContentsMargins(14, 10, 14, 10)
        vl.setSpacing(2)

        val_lbl = QLabel(value)
        val_lbl.setStyleSheet(
            f"font-weight: 700; font-size: 39px; color: {color}; background: transparent;"
        )
        val_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        txt_lbl = QLabel(label)
        txt_lbl.setStyleSheet(
            "font-size: 20px; color: #98989d; font-weight: 500; background: transparent;"
        )
        txt_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        vl.addWidget(val_lbl)
        vl.addWidget(txt_lbl)

        return {'frame': frame, 'value': val_lbl, 'label': txt_lbl}

    # ─── Build DSN ───────────────────────────────────────────
    def _build_dsn(self):
        host = self.inp_host.text().strip() or 'localhost'
        port = self.inp_port.text().strip() or '5433'
        db   = self.inp_db.text().strip()   or 'pixi_test'
        user = self.inp_user.text().strip()  or 'pixi'
        pw   = self.inp_pass.text().strip()  or 'pixipass'
        return f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    # ─── DB Heartbeat (auto-check every 15s) ─────────────────
    def _start_db_heartbeat(self):
        if self._db_heartbeat_timer is None:
            self._db_heartbeat_timer = QTimer(self)
            self._db_heartbeat_timer.timeout.connect(self._run_db_connection_check)
        self._db_heartbeat_timer.start(DB_HEARTBEAT_INTERVAL_MS)

    def _on_conn_changed(self, _=None):
        self._set_db_status("unknown")

    def _run_db_connection_check(self):
        try:
            import psycopg2
        except ImportError:
            self._set_db_status("offline", "psycopg2 missing")
            return

        dsn = self._build_dsn()
        try:
            conn = psycopg2.connect(dsn, connect_timeout=DB_CONNECT_TIMEOUT)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM module_test")
            count = cur.fetchone()[0]
            cur.close()
            conn.close()
            self._set_db_status("online", f"{count} records")
        except Exception as e:
            self._set_db_status("offline", str(e)[:80])

    def _set_db_status(self, state, detail=""):
        if state == "online":
            self.db_status_dot.setStyleSheet("color: #30d158; font-size: 33px;")
            self.db_status_label.setText("Database Online")
            self.db_status_label.setStyleSheet(
                "color: #30d158; font-size: 22px; font-weight: 600;"
            )
            self.lbl_conn_detail.setText(
                f"DB connection: connected — {detail}" if detail else "DB connection: connected"
            )
            self.lbl_conn_detail.setStyleSheet("color: #30d158; font-size: 20px;")
        elif state == "offline":
            self.db_status_dot.setStyleSheet("color: #ff453a; font-size: 33px;")
            self.db_status_label.setText("Database Offline")
            self.db_status_label.setStyleSheet(
                "color: #ff453a; font-size: 22px; font-weight: 600;"
            )
            self.lbl_conn_detail.setText(
                f"DB connection: offline — {detail}" if detail else "DB connection: offline"
            )
            self.lbl_conn_detail.setStyleSheet("color: #ff453a; font-size: 20px;")
        else:
            self.db_status_dot.setStyleSheet("color: #636366; font-size: 33px;")
            self.db_status_label.setText("Database —")
            self.db_status_label.setStyleSheet(
                "color: #636366; font-size: 22px; font-weight: 600;"
            )
            self.lbl_conn_detail.setText("DB connection: not checked")
            self.lbl_conn_detail.setStyleSheet("color: #636366; font-size: 20px;")

    # ─── Test Connection (manual button) ─────────────────────
    def test_connection(self):
        self._run_db_connection_check()
        self.console.append(f"[Connection] {self.lbl_conn_detail.text()}")

    # ─── File Selection ──────────────────────────────────────
    def browse_folder(self):
        dirname = QFileDialog.getExistingDirectory(self, "Select Log Folder")
        if not dirname:
            return
        self.inp_folder_display.setText(os.path.normpath(dirname))
        added = 0
        existing = set(
            self.file_list.item(i).text() for i in range(self.file_list.count())
        )
        for fname in sorted(os.listdir(dirname)):
            if fname.endswith('.txt') and fname != 'summary.txt':
                fpath = os.path.normpath(os.path.join(dirname, fname))
                if fpath not in existing:
                    self.file_list.addItem(fpath)
                    added += 1
        self._update_file_count()
        self.console.append(f"[Files] Added {added} files from {dirname}")
        self._update_upload_btn_state()

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Log Files", "", "Text Files (*.txt);;All Files (*)"
        )
        if not files:
            return
        existing = set(
            self.file_list.item(i).text() for i in range(self.file_list.count())
        )
        added = 0
        for f in files:
            fp = os.path.normpath(f)
            if fp not in existing:
                self.file_list.addItem(fp)
                added += 1
        self._update_file_count()
        self.console.append(f"[Files] Added {added} files")
        self._update_upload_btn_state()

    def clear_files(self):
        self.file_list.clear()
        self.inp_folder_display.clear()
        self._update_file_count()
        self._update_upload_btn_state()

    def _update_file_count(self):
        n = self.file_list.count()
        self.lbl_file_count.setText(f"{n} file{'s' if n != 1 else ''} selected")

    def _update_upload_btn_state(self):
        self.btn_start.setEnabled(self.file_list.count() > 0)

    # ─── Upload ──────────────────────────────────────────────
    def start_upload(self):
        if self.file_list.count() == 0:
            QMessageBox.warning(self, "Warning", "Please select log files first.")
            return

        dsn = self._build_dsn()
        file_paths = [
            self.file_list.item(i).text() for i in range(self.file_list.count())
        ]

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_browse_folder.setEnabled(False)
        self.btn_add_files.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_upload_status.setText(
            f"Upload status: uploading {len(file_paths)} files..."
        )
        self.lbl_upload_status.setStyleSheet("color: #0a84ff; font-size: 20px;")
        self.console.append(f"\n{'─'*50}")
        self.console.append(f"Starting upload: {len(file_paths)} files")

        self.worker = UploadWorkerThread(dsn, file_paths)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.stats.connect(self._on_stats)
        self.worker.log.connect(self.console.append)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def stop_upload(self):
        if self.worker:
            self.worker.stop()
            self.btn_stop.setEnabled(False)
            self.lbl_upload_status.setText("Upload status: cancellation requested...")
            self.lbl_upload_status.setStyleSheet("color: #ff9f0a; font-size: 20px;")

    def _on_stats(self, st):
        for key in self.stat_cards:
            self.stat_cards[key]['value'].setText(str(st[key]))

    def _on_finished(self, msg):
        self.console.append(f"\n{msg}")
        self.console.append(f"{'─'*50}")
        self.lbl_upload_status.setText(f"Upload status: {msg}")
        self.lbl_upload_status.setStyleSheet("color: #30d158; font-size: 20px;")
        self._enable_buttons()
        # Refresh DB record count
        QTimer.singleShot(500, self._run_db_connection_check)
        QMessageBox.information(self, "Upload Complete", msg)

    def _on_error(self, msg):
        self.console.append(f"✗ Error: {msg}")
        self.lbl_upload_status.setText(f"Upload status: error — {msg}")
        self.lbl_upload_status.setStyleSheet("color: #ff453a; font-size: 20px;")
        self._enable_buttons()
        QMessageBox.critical(self, "Error", msg)

    def _enable_buttons(self):
        self.btn_start.setEnabled(self.file_list.count() > 0)
        self.btn_stop.setEnabled(False)
        self.btn_browse_folder.setEnabled(True)
        self.btn_add_files.setEnabled(True)

    def clear_stats(self):
        self.progress_bar.setValue(0)
        for key in self.stat_cards:
            self.stat_cards[key]['value'].setText("0")
        self.lbl_upload_status.setText("Upload status: idle")
        self.lbl_upload_status.setStyleSheet("color: #636366; font-size: 20px;")
        self.console.clear()

    def closeEvent(self, event):
        if self._db_heartbeat_timer is not None:
            self._db_heartbeat_timer.stop()
        super().closeEvent(event)


# ========================================================
# Entry point
# ========================================================
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = LogUploaderApp()
    window.show()
    sys.exit(app.exec_())
