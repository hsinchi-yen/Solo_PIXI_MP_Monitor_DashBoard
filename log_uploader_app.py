"""
Solo PIXI Module Test — Log Uploader App
Upload QCA9377 BT+WiFi production test logs directly to PostgreSQL.
Ubuntu Yaru-style desktop UI.
"""
import sys
import os
import importlib.util
from pathlib import Path

def get_runtime_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_bundle_dir() -> Path:
    if getattr(sys, '_MEIPASS', None):
        return Path(sys._MEIPASS)
    return get_runtime_dir()


def get_resource_path(*parts):
    return os.path.join(get_bundle_dir(), *parts)


def get_db_server_conf_paths() -> list[Path]:
    runtime_conf = get_runtime_dir() / 'dbservip.conf'
    bundle_conf = get_bundle_dir() / 'dbservip.conf'

    paths = [runtime_conf]
    if bundle_conf != runtime_conf:
        paths.append(bundle_conf)
    return paths


BASE_DIR = str(get_bundle_dir())
PARSER_PATH = get_resource_path('solo-pixi-essential', 'module_log_parser.py')
DB_SERVER_CONF_PATHS = get_db_server_conf_paths()
APP_ICON_PATH = get_resource_path('build_assets', 'icons', 'solo_pixi_uploader.ico')

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLineEdit, QLabel,
                             QFileDialog, QTextEdit, QGroupBox, QGridLayout,
                             QMessageBox, QProgressBar, QListWidget,
                             QAbstractItemView, QFrame, QSizePolicy)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QIcon

APP_VERSION = "1.2.0"
DB_CONNECT_TIMEOUT = 3
DB_HEARTBEAT_INTERVAL_MS = 15_000  # 15 seconds
DEFAULT_DB_HOST = "10.20.31.111"


def load_parser_module():
    if not os.path.exists(PARSER_PATH):
        raise ImportError("module_log_parser.py not found in solo-pixi-essential")
    spec = importlib.util.spec_from_file_location("solo_pixi_module_log_parser", PARSER_PATH)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError("Failed to create loader for module_log_parser.py")
    spec.loader.exec_module(module)
    return module


def load_default_db_host():
    for conf_path in DB_SERVER_CONF_PATHS:
        if not conf_path.exists():
            continue

        try:
            with conf_path.open('r', encoding='utf-8') as conf_file:
                for raw_line in conf_file:
                    line = raw_line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if ':' in line:
                        key, value = line.split(':', 1)
                        if key.strip().upper() == 'IP' and value.strip():
                            return value.strip()
                    if '=' in line:
                        key, value = line.split('=', 1)
                        if key.strip().upper() == 'IP' and value.strip():
                            return value.strip()
                    return line
        except OSError:
            continue

    return DEFAULT_DB_HOST

# ========================================================
# Ubuntu Yaru-inspired stylesheet
# ========================================================
YARU_STYLESHEET = """
QMainWindow {
    background-color: #f7f6f5;
}
QWidget {
    font-family: "Ubuntu", "Noto Sans", "Segoe UI", sans-serif;
    color: #241f31;
    font-size: 13px;
    line-height: 1.2;
}

QGroupBox {
    background-color: #ffffff;
    border: 1px solid #d6d3d1;
    border-radius: 14px;
    margin-top: 12px;
    padding: 20px 18px 16px 18px;
    font-weight: 600;
    font-size: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    color: #5e2750;
    font-weight: 600;
    font-size: 15px;
}

QLineEdit {
    border: 1px solid #c7c2bf;
    border-radius: 9px;
    padding: 9px 12px;
    background-color: #ffffff;
    selection-background-color: #e95420;
    color: #241f31;
    font-size: 13px;
    min-height: 22px;
}
QLineEdit:focus {
    border: 2px solid #e95420;
    padding: 7px 11px;
}
QLineEdit[readOnly="true"] {
    background-color: #f0eeec;
    color: #636363;
}

QPushButton {
    background-color: #e95420;
    color: white;
    border: 1px solid #c34113;
    border-radius: 10px;
    padding: 8px 18px;
    font-weight: 600;
    font-size: 13px;
    min-height: 20px;
}
QPushButton:hover {
    background-color: #ec6c3a;
}
QPushButton:pressed {
    background-color: #c34113;
}
QPushButton:disabled {
    background-color: #f0d0c5;
    color: #8a8886;
    border-color: #e2c8c0;
}

QPushButton#secondary {
    background-color: #f7f3f1;
    color: #241f31;
    border: 1px solid #d6d3d1;
}
QPushButton#secondary:hover {
    background-color: #efe8e4;
}
QPushButton#secondary:pressed {
    background-color: #e3dad5;
}

QPushButton#danger {
    background-color: #c01c28;
    color: white;
    border: 1px solid #9e1620;
}
QPushButton#danger:hover {
    background-color: #d73642;
}
QPushButton#danger:pressed {
    background-color: #9e1620;
}
QPushButton#danger:disabled {
    background-color: #e7c0c3;
    color: #8a8886;
}

QTextEdit {
    border: 1px solid #d6d3d1;
    border-radius: 12px;
    background-color: #2b2b2b;
    color: #f5f5f5;
    padding: 12px;
    font-family: "Ubuntu Mono", "Cascadia Code", Consolas, monospace;
    font-size: 13px;
    selection-background-color: #e95420;
}

QListWidget {
    border: 1px solid #d6d3d1;
    border-radius: 12px;
    background-color: #ffffff;
    color: #241f31;
    padding: 6px;
    font-size: 12px;
    font-family: "Ubuntu Mono", "Cascadia Code", Consolas, monospace;
    outline: none;
}
QListWidget::item {
    padding: 6px 10px;
    border-radius: 6px;
}
QListWidget::item:selected {
    background-color: #e95420;
    color: white;
}
QListWidget::item:hover {
    background-color: #f2eeeb;
}

QProgressBar {
    border: 1px solid #d6d3d1;
    border-radius: 6px;
    background-color: #efeae7;
    text-align: center;
    color: #241f31;
    font-size: 12px;
    min-height: 12px;
    max-height: 12px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #e95420, stop:1 #eb6536);
    border-radius: 5px;
}

QLabel {
    font-size: 13px;
    color: #241f31;
    background: transparent;
}

QFrame#separator {
    background-color: #d6d3d1;
    max-height: 1px;
}

QMessageBox {
    background-color: #f7f6f5;
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
            parser = load_parser_module()
        except Exception:
            self.error.emit("Unable to load module_log_parser.py. Confirm solo-pixi-essential exists.")
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
                self.log.emit("⛔ Upload cancelled by user")
                break

            fname = os.path.basename(fpath)
            try:
                rec = parser.parse_log_file(fpath)
                inserted = parser.insert_record(conn, rec)
                if inserted:
                    st['uploaded'] += 1
                    self.log.emit(
                        f"  [{i+1}/{total}] ✓ {fname} -> uploaded (unit_date={rec.get('unit_date')})"
                    )
                else:
                    st['skipped'] += 1
                    self.log.emit(f"  [{i+1}/{total}] ⏭ {fname} -> skipped (duplicate file_hash)")
            except Exception as e:
                st['failed'] += 1
                self.log.emit(f"  [{i+1}/{total}] ✗ {fname} -> error: {e}")

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

        summary = (f"Upload completed. Total {total} files - "
               f"uploaded: {st['uploaded']}  skipped: {st['skipped']}  failed: {st['failed']}")
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
        self.setMinimumSize(1120, 920)
        self.setStyleSheet(YARU_STYLESHEET)
        if os.path.exists(APP_ICON_PATH):
            self.setWindowIcon(QIcon(APP_ICON_PATH))

        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        layout = QVBoxLayout()
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(14)
        main_widget.setLayout(layout)

        # ── Title Bar + DB Status Indicator ───────────────────────
        title_row = QHBoxLayout()

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title_lbl = QLabel("Solo PIXI — Log Uploader")
        title_lbl.setStyleSheet(
            "color: #241f31; font-size: 24px; font-weight: 700;"
        )
        subtitle_lbl = QLabel("QCA9377 BT+WiFi Module Test")
        subtitle_lbl.setStyleSheet("color: #636363; font-size: 14px; font-weight: 500;")
        title_col.addWidget(title_lbl)
        title_col.addWidget(subtitle_lbl)
        title_row.addLayout(title_col)
        title_row.addStretch()

        # ── Database Online / Offline badge ───────────────────────
        self.db_status_dot = QLabel("●")
        self.db_status_dot.setStyleSheet("color: #8a8886; font-size: 18px;")
        self.db_status_label = QLabel("Database Offline")
        self.db_status_label.setStyleSheet(
            "color: #8a8886; font-size: 14px; font-weight: 700;"
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

        info_banner = QFrame()
        info_banner.setStyleSheet(
            "QFrame { background-color: #fff3ed; border: 1px solid #f1c7b8; border-radius: 12px; }"
        )
        info_layout = QHBoxLayout(info_banner)
        info_layout.setContentsMargins(16, 10, 16, 10)
        info_layout.setSpacing(6)
        info_title = QLabel("Upload Format:")
        info_title.setStyleSheet("color: #5e2750; font-size: 13px; font-weight: 700; white-space: nowrap;")
        info_desc = QLabel(
            "Preferred filename: WORKORDER_YYYYMMDD_HHMMSS_MAC1_MAC2_RESULT.txt. unit_date is derived from filename or Start time."
        )
        info_desc.setStyleSheet("color: #5c514b; font-size: 13px;")
        info_layout.addWidget(info_title, 0)
        info_layout.addWidget(info_desc, 1)
        layout.addWidget(info_banner)

        # ════════════════════════════════════════════════════════
        # 1. Database Connection (QGridLayout)
        # ════════════════════════════════════════════════════════
        conn_group = QGroupBox("Database Connection")
        conn_group.setMinimumHeight(200)
        conn_grid = QGridLayout()
        conn_grid.setHorizontalSpacing(14)
        conn_grid.setVerticalSpacing(14)
        conn_grid.setContentsMargins(16, 22, 16, 16)
        conn_grid.setColumnMinimumWidth(0, 72)
        conn_grid.setColumnMinimumWidth(2, 72)
        conn_grid.setColumnMinimumWidth(4, 78)
        conn_grid.setColumnStretch(1, 3)
        conn_grid.setColumnStretch(3, 2)
        conn_grid.setColumnStretch(5, 2)
        conn_grid.setRowMinimumHeight(0, 46)
        conn_grid.setRowMinimumHeight(1, 46)
        conn_grid.setRowMinimumHeight(2, 30)

        lbl_host = QLabel("Host:")
        lbl_port = QLabel("Port:")
        lbl_db   = QLabel("Database:")
        lbl_user = QLabel("User:")
        lbl_pw   = QLabel("Password:")
        for lbl in [lbl_host, lbl_port, lbl_db, lbl_user, lbl_pw]:
            lbl.setStyleSheet("color: #5c514b; font-size: 13px; font-weight: 600;")
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.inp_host = QLineEdit(load_default_db_host())
        self.inp_port = QLineEdit("5433")
        self.inp_db   = QLineEdit("pixi_test")
        self.inp_user = QLineEdit("pixi")
        self.inp_pass = QLineEdit("pixipass")
        self.inp_pass.setEchoMode(QLineEdit.Password)
        for inp in [self.inp_host, self.inp_port, self.inp_db, self.inp_user, self.inp_pass]:
            inp.setFixedHeight(40)
            inp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            inp.textChanged.connect(self._on_conn_changed)

        conn_grid.addWidget(lbl_host, 0, 0)
        conn_grid.addWidget(self.inp_host, 0, 1)
        conn_grid.addWidget(lbl_port, 0, 2)
        self.inp_port.setMinimumWidth(110)
        self.inp_port.setMaximumWidth(140)
        conn_grid.addWidget(self.inp_port, 0, 3)
        conn_grid.addWidget(lbl_pw, 0, 4)
        self.inp_pass.setMinimumWidth(140)
        self.inp_pass.setMaximumWidth(160)
        conn_grid.addWidget(self.inp_pass, 0, 5)

        conn_grid.addWidget(lbl_db, 1, 0)
        conn_grid.addWidget(self.inp_db, 1, 1)
        conn_grid.addWidget(lbl_user, 1, 2)
        conn_grid.addWidget(self.inp_user, 1, 3)
        self.btn_test_conn = QPushButton("Test Connection")
        self.btn_test_conn.setObjectName("secondary")
        self.btn_test_conn.setMinimumHeight(40)
        self.btn_test_conn.clicked.connect(self.test_connection)
        conn_grid.addWidget(self.btn_test_conn, 1, 4, 1, 2)

        self.lbl_conn_detail = QLabel("DB connection: checking...")
        self.lbl_conn_detail.setWordWrap(True)
        self.lbl_conn_detail.setStyleSheet("color: #8a8886; font-size: 13px;")
        conn_grid.addWidget(self.lbl_conn_detail, 2, 0, 1, 6)

        conn_group.setLayout(conn_grid)
        layout.addWidget(conn_group)

        # ════════════════════════════════════════════════════════
        # 2. Log Files
        # ════════════════════════════════════════════════════════
        file_group = QGroupBox("Log Files")
        file_layout = QVBoxLayout()
        file_layout.setContentsMargins(16, 22, 16, 16)
        file_layout.setSpacing(12)

        # Folder row
        folder_row = QHBoxLayout()
        folder_row.setSpacing(12)
        lbl_folder = QLabel("Log Folder:")
        lbl_folder.setStyleSheet("color: #5c514b; font-size: 13px; font-weight: 600;")
        self.inp_folder_display = QLineEdit()
        self.inp_folder_display.setReadOnly(True)
        self.inp_folder_display.setPlaceholderText("Select folder containing .txt log files")
        self.inp_folder_display.setMinimumHeight(40)
        self.btn_browse_folder = QPushButton("Browse")
        self.btn_browse_folder.setObjectName("secondary")
        self.btn_browse_folder.setMinimumHeight(40)
        self.btn_browse_folder.clicked.connect(self.browse_folder)
        folder_row.addWidget(lbl_folder)
        folder_row.addWidget(self.inp_folder_display, 1)
        folder_row.addWidget(self.btn_browse_folder)
        file_layout.addLayout(folder_row)

        # Buttons + count
        list_btn_row = QHBoxLayout()
        list_btn_row.setSpacing(12)
        self.btn_add_files = QPushButton("Add Files")
        self.btn_add_files.setObjectName("secondary")
        self.btn_add_files.setMinimumHeight(40)
        self.btn_add_files.clicked.connect(self.add_files)
        self.btn_clear_files = QPushButton("Clear")
        self.btn_clear_files.setObjectName("secondary")
        self.btn_clear_files.setMinimumHeight(40)
        self.btn_clear_files.clicked.connect(self.clear_files)
        self.lbl_file_count = QLabel("0 files selected")
        self.lbl_file_count.setStyleSheet("color: #636363; font-size: 13px; font-weight: 600;")
        list_btn_row.addWidget(self.btn_add_files)
        list_btn_row.addWidget(self.btn_clear_files)
        list_btn_row.addStretch()
        list_btn_row.addWidget(self.lbl_file_count)
        file_layout.addLayout(list_btn_row)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.file_list.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.file_list.setTextElideMode(Qt.ElideNone)
        self.file_list.setWordWrap(False)
        self.file_list.setMinimumHeight(150)
        self.file_list.setMaximumHeight(220)
        file_layout.addWidget(self.file_list)

        file_group.setLayout(file_layout)
        layout.addWidget(file_group)

        # ════════════════════════════════════════════════════════
        # 3. Upload Progress & Stats
        # ════════════════════════════════════════════════════════
        progress_group = QGroupBox("Upload")
        progress_group.setMinimumHeight(320)
        progress_layout = QVBoxLayout()
        progress_layout.setContentsMargins(16, 22, 16, 16)
        progress_layout.setSpacing(16)

        # Action buttons
        action_row = QHBoxLayout()
        action_row.setSpacing(14)
        self.btn_start = QPushButton("Upload to DB")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setMinimumWidth(180)
        self.btn_start.clicked.connect(self.start_upload)
        self.btn_start.setEnabled(False)

        self.btn_stop = QPushButton("Cancel")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_upload)

        self.btn_clear_stats = QPushButton("Clear Stats")
        self.btn_clear_stats.setObjectName("secondary")
        self.btn_clear_stats.setMinimumHeight(40)
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
        stats_row.setContentsMargins(0, 4, 0, 4)
        stats_row.setSpacing(16)
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
        self.lbl_upload_status.setWordWrap(True)
        self.lbl_upload_status.setMinimumHeight(24)
        self.lbl_upload_status.setStyleSheet("color: #8a8886; font-size: 13px; font-weight: 600; padding-top: 2px;")
        progress_layout.addWidget(self.lbl_upload_status)

        # Duplicate policy
        policy_lbl = QLabel(
            "Duplicate policy: skip same file_hash. Upload is compatible with the new unit_date-based schema."
        )
        policy_lbl.setWordWrap(True)
        policy_lbl.setMinimumHeight(24)
        progress_group.setLayout(progress_layout)
        layout.addWidget(progress_group)

    # ─── Stat Card Builder ───────────────────────────────────
    def _build_stat_card(self, label, value, color):
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #f7f3f1;
                border: 1px solid #e0dbd7;
                border-radius: 12px;
            }
        """)
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        frame.setFixedHeight(102)

        vl = QVBoxLayout(frame)
        vl.setContentsMargins(14, 12, 14, 12)
        vl.setSpacing(6)

        val_lbl = QLabel(value)
        val_lbl.setStyleSheet(
            f"font-weight: 700; font-size: 24px; color: {color}; background: transparent;"
        )
        val_lbl.setMinimumHeight(32)
        val_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        txt_lbl = QLabel(label)
        txt_lbl.setStyleSheet(
            "font-size: 13px; color: #6d635d; font-weight: 700; background: transparent;"
        )
        txt_lbl.setMinimumHeight(18)
        txt_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        vl.addWidget(val_lbl)
        vl.addWidget(txt_lbl)

        return {'frame': frame, 'value': val_lbl, 'label': txt_lbl}

    # ─── Build DSN ───────────────────────────────────────────
    def _build_dsn(self):
        return (
            f"postgresql://{self.inp_user.text()}:{self.inp_pass.text()}"
            f"@{self.inp_host.text()}:{self.inp_port.text()}/{self.inp_db.text()}"
        )

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
            detail = f"{count} records, host={self.inp_host.text()}"
            self._set_db_status("online", detail)
        except Exception as e:
            self._set_db_status("offline", str(e)[:80])

    def _set_db_status(self, state, detail=""):
        if state == "online":
            self.db_status_dot.setStyleSheet("color: #30d158; font-size: 33px;")
            self.db_status_dot.setStyleSheet("color: #0e8420; font-size: 18px;")
            self.db_status_label.setText("Database Online")
            self.db_status_label.setStyleSheet(
                "color: #0e8420; font-size: 14px; font-weight: 700;"
            )
            self.lbl_conn_detail.setText(
                f"DB connection: connected — {detail}" if detail else "DB connection: connected"
            )
            self.lbl_conn_detail.setStyleSheet("color: #0e8420; font-size: 13px;")
        elif state == "offline":
            self.db_status_dot.setStyleSheet("color: #c01c28; font-size: 18px;")
            self.db_status_label.setText("Database Offline")
            self.db_status_label.setStyleSheet(
                "color: #c01c28; font-size: 14px; font-weight: 700;"
            )
            self.lbl_conn_detail.setText(
                f"DB connection: offline — {detail}" if detail else "DB connection: offline"
            )
            self.lbl_conn_detail.setStyleSheet("color: #c01c28; font-size: 13px;")
        else:
            self.db_status_dot.setStyleSheet("color: #8a8886; font-size: 18px;")
            self.db_status_label.setText("Database —")
            self.db_status_label.setStyleSheet(
                "color: #8a8886; font-size: 14px; font-weight: 700;"
            )
            self.lbl_conn_detail.setText("DB connection: not checked")
            self.lbl_conn_detail.setStyleSheet("color: #8a8886; font-size: 13px;")

    # ─── Test Connection (manual button) ─────────────────────
    def test_connection(self):
        self._run_db_connection_check()

    # ─── File Selection ──────────────────────────────────────
    def browse_folder(self):
        dirname = QFileDialog.getExistingDirectory(self, "Select Log Folder")
        if not dirname:
            return
        self.inp_folder_display.setText(os.path.normpath(dirname))
        try:
            parser = load_parser_module()
        except Exception as e:
            QMessageBox.critical(self, "Parser Error", str(e))
            return
        added = 0
        existing = set(
            self.file_list.item(i).text() for i in range(self.file_list.count())
        )
        for fname in sorted(os.listdir(dirname)):
            if fname.endswith('.txt') and fname != 'summary.txt' and (
                parser.FILENAME_RE.match(fname) or parser.LEGACY_FILENAME_RE.match(fname)
            ):
                fpath = os.path.normpath(os.path.join(dirname, fname))
                if fpath not in existing:
                    self.file_list.addItem(fpath)
                    added += 1
        self._update_file_count()
        self._update_upload_btn_state()

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Log Files", "", "Text Files (*.txt);;All Files (*)"
        )
        if not files:
            return
        try:
            parser = load_parser_module()
        except Exception as e:
            QMessageBox.critical(self, "Parser Error", str(e))
            return
        existing = set(
            self.file_list.item(i).text() for i in range(self.file_list.count())
        )
        added = 0
        for f in files:
            fp = os.path.normpath(f)
            fname = os.path.basename(fp)
            if fp not in existing and (
                parser.FILENAME_RE.match(fname) or parser.LEGACY_FILENAME_RE.match(fname)
            ):
                self.file_list.addItem(fp)
                added += 1
        self._update_file_count()
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

    def _format_status_stats(self, st):
        return (
            f"PASS: {st.get('uploaded', 0)}  FAIL: {st.get('failed', 0)}"
        )

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

        self.worker = UploadWorkerThread(dsn, file_paths)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.stats.connect(self._on_stats)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def stop_upload(self):
        if self.worker:
            self.worker.stop()
            self.btn_stop.setEnabled(False)
            self.lbl_upload_status.setText("Upload status: cancellation requested...")
            self.lbl_upload_status.setStyleSheet("color: #c7162b; font-size: 13px; font-weight: 700;")

    def _on_stats(self, st):
        for key in self.stat_cards:
            self.stat_cards[key]['value'].setText(str(st[key]))
        self.lbl_upload_status.setText(f"Upload status: {self._format_status_stats(st)}")

    def _on_finished(self, msg):
        final_stats = {
            'queued': self.stat_cards['queued']['value'].text(),
            'uploaded': self.stat_cards['uploaded']['value'].text(),
            'skipped': self.stat_cards['skipped']['value'].text(),
            'failed': self.stat_cards['failed']['value'].text(),
        }
        self.lbl_upload_status.setText(
            f"Upload status: completed. {self._format_status_stats(final_stats)}"
        )
        self.lbl_upload_status.setStyleSheet("color: #0e8420; font-size: 13px; font-weight: 700;")
        self._enable_buttons()
        # Refresh DB record count
        QTimer.singleShot(500, self._run_db_connection_check)
        QMessageBox.information(self, "Upload Complete", msg)

    def _on_error(self, msg):
        self.lbl_upload_status.setText(f"Upload status: error — {msg}")
        self.lbl_upload_status.setStyleSheet("color: #c01c28; font-size: 13px; font-weight: 700;")
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
        self.lbl_upload_status.setText(
            "Upload status: PASS: 0  FAIL: 0"
        )
        self.lbl_upload_status.setStyleSheet("color: #8a8886; font-size: 13px; font-weight: 600;")

    def closeEvent(self, event):
        if self._db_heartbeat_timer is not None:
            self._db_heartbeat_timer.stop()
        super().closeEvent(event)


# ========================================================
# Entry point
# ========================================================
if __name__ == '__main__':
    app = QApplication(sys.argv)
    if os.path.exists(APP_ICON_PATH):
        app.setWindowIcon(QIcon(APP_ICON_PATH))
    window = LogUploaderApp()
    window.show()
    sys.exit(app.exec_())
