import sys
import os
import re
from datetime import datetime
from collections import Counter
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLineEdit, QLabel, 
                             QFileDialog, QTextEdit, QGroupBox, QGridLayout, 
                             QMessageBox, QFormLayout, QFrame, QStyle)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt5.QtGui import QIcon


def get_base_dir():
    if getattr(sys, '_MEIPASS', None):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_path(*parts):
    return os.path.join(get_base_dir(), *parts)


APP_ICON_PATH = get_resource_path('build_assets', 'icons', 'solo_pixi_splitter.ico')

# ========================================================
# Ubuntu / Yaru Style UI Stylesheet
# ========================================================
UBUNTU_STYLESHEET = """
QWidget {
    font-family: Ubuntu, "Noto Sans", "Segoe UI", sans-serif;
    color: #2e3436;
    font-size: 12px;
}
QMainWindow {
    background-color: #f7f5f4;
}
QFrame#headerBar {
    background-color: #2c001e;
    border: 1px solid #4f233f;
    border-radius: 12px;
}
QLabel#windowTitle {
    color: #fff7f3;
    font-size: 16px;
    font-weight: 700;
}
QLabel#windowSubtitle {
    color: #d8c8d2;
    font-size: 11px;
}
QGroupBox {
    background-color: #ffffff;
    border: 1px solid #d8d4cf;
    border-radius: 8px;
    margin-top: 18px;
    padding-top: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: #5e2750;
    font-weight: 600;
    font-size: 13px;
}
QLineEdit {
    border: 1px solid #c9c4be;
    border-radius: 6px;
    padding: 7px 10px;
    background-color: #ffffff;
    selection-background-color: #77216f;
    color: #2e3436;
}
QLineEdit:focus {
    border: 1px solid #77216f;
}
QLineEdit:disabled {
    background-color: #f3f1ef;
    color: #8d8882;
}
QPushButton {
    background-color: #e95420;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #d84d1e;
}
QPushButton:pressed {
    background-color: #c8471b;
}
QPushButton:disabled {
    background-color: #efb7a2;
    color: #fff8f5;
}
QPushButton#secondary {
    background-color: #f1eeeb;
    color: #2e3436;
    border: 1px solid #c9c4be;
}
QPushButton#secondary:hover {
    background-color: #e7e2dd;
}
QPushButton#secondary:pressed {
    background-color: #ddd7d1;
}
QPushButton#summary {
    background-color: #77216f;
}
QPushButton#summary:hover {
    background-color: #651b5d;
}
QPushButton#summary:pressed {
    background-color: #57164f;
}
QPushButton#iconSecondary {
    background-color: #f8f6f4;
    color: #2e3436;
    border: 1px solid #d2ccc5;
    text-align: left;
    padding: 8px 12px;
}
QPushButton#iconSecondary:hover {
    background-color: #ece7e2;
}
QPushButton#iconSecondary:pressed {
    background-color: #e0dad4;
}
QTextEdit {
    border: 1px solid #d8d4cf;
    border-radius: 8px;
    background-color: #ffffff;
    padding: 8px;
    selection-background-color: #77216f;
}
QLabel {
    font-size: 12px;
}
QFrame#statCard {
    background-color: #fffefe;
    border: 1px solid #dfd8d2;
    border-radius: 10px;
}
QFrame#statCardAccent {
    background-color: #fff7f4;
    border: 1px solid #efc3b0;
    border-radius: 10px;
}
QLabel#statLabel {
    color: #6b5f68;
    font-size: 11px;
    font-weight: 600;
}
QLabel#statValue {
    color: #2e3436;
    font-size: 20px;
    font-weight: 700;
}
QLabel#statHint {
    color: #7b727a;
    font-size: 11px;
}
"""

SEGMENT_PATTERN = re.compile(r'(?=^MAC1:\s*)', re.MULTILINE)
START_PATTERN = re.compile(r'(\d{4})/(\d{2})/(\d{2}).*?(\d{2}):(\d{2}):(\d{2})')
WORKORDER_FROM_PATH_PATTERN = re.compile(r'([A-Za-z0-9]+-\d{8,})')
SPLIT_FILENAME_PATTERN = re.compile(r'^([A-Za-z0-9]+-\d{8,})_(\d{8})_(\d{6})_([0-9A-F]+)_([0-9A-F]+)_(PASS|FAIL|STOP)\.txt$', re.IGNORECASE)
DUMMY_WORK_ORDER = "XXXX-XXXXXXXXX"


def split_log_content(content):
    normalized_content = content.replace('\r\n', '\n').replace('\r', '\n')
    return [segment for segment in SEGMENT_PATTERN.split(normalized_content) if segment.strip().startswith("MAC1:")]


def parse_segment(segment):
    mac1 = mac2 = date_str = time_str = result = None

    for line in segment.strip().split('\n'):
        line_clean = line.strip()
        if line_clean.startswith("MAC1:"):
            mac1 = line_clean.split("\t")[-1].strip()
        elif line_clean.startswith("MAC2:"):
            mac2 = line_clean.split("\t")[-1].strip()
        elif line_clean.startswith("Start:"):
            start_match = START_PATTERN.search(line_clean)
            if start_match:
                date_str = ''.join(start_match.group(1, 2, 3))
                time_str = ''.join(start_match.group(4, 5, 6))

        if "**** P A S S ****" in line:
            result = "PASS"
        elif "**** F A I L ****" in line:
            result = "FAIL"
        elif "**** S T O P ****" in line:
            result = "STOP"

    return {
        "mac1": mac1,
        "mac2": mac2,
        "date": date_str,
        "time": time_str,
        "result": result,
    }


def infer_workorder_from_source_path(path_text):
    """Infer work order from source path/filename (e.g. 5101-25122302)."""
    normalized = os.path.normpath(path_text)
    for part in normalized.split(os.sep):
        match = WORKORDER_FROM_PATH_PATTERN.search(part)
        if match:
            return match.group(1)
    match = WORKORDER_FROM_PATH_PATTERN.search(normalized)
    return match.group(1) if match else None


def build_stats(total, success_count, skipped_count, results_list):
    summary_counts = Counter(results_list)
    return {
        "total": total,
        "success": success_count,
        "skipped": skipped_count,
        "pass": summary_counts.get('PASS', 0),
        "fail": summary_counts.get('FAIL', 0),
        "stop": summary_counts.get('STOP', 0),
    }


def ensure_unique_output_path(output_dir, filename):
    file_root, file_ext = os.path.splitext(filename)
    filepath = os.path.join(output_dir, filename)
    index = 1
    while os.path.exists(filepath):
        filepath = os.path.join(output_dir, f"{file_root}_{index}{file_ext}")
        index += 1
    return filepath


def format_stats_message(stats):
    total = stats['total']
    pass_rate = (stats['pass'] / total * 100) if total else 0
    fail_rate = (stats['fail'] / total * 100) if total else 0
    return (
        f"Total logs: {total}\n"
        f"Split files created: {stats['success']}\n"
        f"Skipped entries: {stats['skipped']}\n"
        f"PASS: {stats['pass']}\n"
        f"FAIL: {stats['fail']}\n"
        f"STOP: {stats['stop']}\n"
        f"Pass rate: {pass_rate:.2f}%\n"
        f"Fail rate: {fail_rate:.2f}%"
    )


def create_summary_file(output_dir, stats):
    summary_path = os.path.join(output_dir, "summary.txt")
    pass_rate = (stats['pass'] / stats['total'] * 100) if stats['total'] else 0
    fail_rate = (stats['fail'] / stats['total'] * 100) if stats['total'] else 0

    with open(summary_path, "w", encoding="utf-8") as summary_file:
        summary_file.write("Log Split Summary\n")
        summary_file.write("=" * 40 + "\n")
        summary_file.write(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        summary_file.write(f"Output directory: {output_dir}\n")
        summary_file.write(f"Total logs: {stats['total']}\n")
        summary_file.write(f"Split files created: {stats['success']}\n")
        summary_file.write(f"Skipped entries: {stats['skipped']}\n")
        summary_file.write("-" * 40 + "\n")
        summary_file.write(f"PASS count: {stats['pass']}\n")
        summary_file.write(f"FAIL count: {stats['fail']}\n")
        summary_file.write(f"STOP count: {stats['stop']}\n")
        summary_file.write(f"Pass rate: {pass_rate:.2f}%\n")
        summary_file.write(f"Fail rate: {fail_rate:.2f}%\n")

    return summary_path


def collect_output_summary(output_dir):
    if not os.path.isdir(output_dir):
        raise FileNotFoundError("The output directory does not exist. Please verify the selected folder.")

    results_list = []
    skipped_count = 0

    for entry in os.listdir(output_dir):
        entry_path = os.path.join(output_dir, entry)
        if not os.path.isfile(entry_path) or entry.lower() == "summary.txt":
            continue

        match = SPLIT_FILENAME_PATTERN.match(entry)
        if match:
            results_list.append(match.group(6).upper())
        else:
            skipped_count += 1

    total = len(results_list)
    return build_stats(total, total, skipped_count, results_list)


class LogSplitterThread(QThread):
    progress = pyqtSignal(str)
    summary = pyqtSignal(dict)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, input_file, output_dir, work_order):
        super().__init__()
        self.input_file = input_file
        self.output_dir = output_dir
        self.work_order = (work_order or "").strip()

    def run(self):
        try:
            self.progress.emit(f"Processing source file: {self.input_file}")
            os.makedirs(self.output_dir, exist_ok=True)

            work_order = self.work_order if self.work_order else DUMMY_WORK_ORDER
            self.progress.emit(f"Using work order: {work_order}")

            with open(self.input_file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            segments = split_log_content(content)
            
            total_segments = len(segments)
            self.progress.emit(f"Detected test entries: {total_segments}")
            
            success_count = 0
            skipped_count = 0
            results_list = []

            for i, seg in enumerate(segments):
                parsed = parse_segment(seg)
                mac1 = parsed['mac1']
                mac2 = parsed['mac2']
                date_str = parsed['date']
                time_str = parsed['time']
                result = parsed['result']

                if all([mac1, mac2, date_str, time_str, result]):
                    filename = f"{work_order}_{date_str}_{time_str}_{mac1}_{mac2}_{result}.txt"
                    filepath = ensure_unique_output_path(self.output_dir, filename)
                    
                    with open(filepath, "w", encoding="utf-8") as out_f:
                        out_f.write(seg.rstrip() + "\n\n")
                    success_count += 1
                    results_list.append(result)
                else:
                    self.progress.emit(f"Skipped invalid block: MAC1={mac1}, Result={result}")
                    skipped_count += 1

                if (i + 1) % 50 == 0 or (i + 1) == total_segments:
                    self.progress.emit(f"Progress: {i+1}/{total_segments}...")

            stats = build_stats(total_segments, success_count, skipped_count, results_list)
            self.progress.emit(f"Split complete. Created {success_count} files in:\n{self.output_dir}")
            self.summary.emit(stats)
            self.finished.emit()
            
        except Exception as e:
            self.error.emit(f"Split process failed: {str(e)}")


class SummaryThread(QThread):
    progress = pyqtSignal(str)
    summary = pyqtSignal(dict)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, output_dir):
        super().__init__()
        self.output_dir = output_dir

    def run(self):
        try:
            self.progress.emit(f"Scanning output directory: {self.output_dir}")
            stats = collect_output_summary(self.output_dir)
            summary_path = create_summary_file(self.output_dir, stats)
            self.progress.emit(f"Summary created: {summary_path}")
            self.summary.emit(stats)
            self.finished.emit(summary_path)
        except Exception as e:
            self.error.emit(f"Summary generation failed: {str(e)}")

class LogSplitterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.thread = None
        self.summary_thread = None
        self.last_summary_path = "Not generated yet"
        self.last_summary_time = "-"
        self.initUI()

    def initUI(self):
        self.setWindowTitle('Production Log Splitter')
        self.setMinimumSize(860, 700)
        self.setStyleSheet(UBUNTU_STYLESHEET)
        if os.path.exists(APP_ICON_PATH):
            self.setWindowIcon(QIcon(APP_ICON_PATH))

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)
        main_widget.setLayout(layout)

        header_bar = QFrame()
        header_bar.setObjectName("headerBar")
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(14)
        header_bar.setLayout(header_layout)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        lbl_title = QLabel("Production Log Splitter")
        lbl_title.setObjectName("windowTitle")
        lbl_subtitle = QLabel("Ubuntu-inspired log processing workspace with split and summary actions")
        lbl_subtitle.setObjectName("windowSubtitle")
        lbl_subtitle.setWordWrap(True)
        title_layout.addWidget(lbl_title)
        title_layout.addWidget(lbl_subtitle)
        header_layout.addLayout(title_layout, 1)

        self.btn_start = QPushButton("Start Split")
        self.btn_start.setMinimumHeight(42)
        self.btn_start.clicked.connect(self.start_processing)
        self.btn_start.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.btn_start.setIconSize(QSize(18, 18))
        header_layout.addWidget(self.btn_start)

        self.btn_summary = QPushButton("Build Summary")
        self.btn_summary.setObjectName("summary")
        self.btn_summary.setMinimumHeight(42)
        self.btn_summary.clicked.connect(self.start_summary_generation)
        self.btn_summary.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self.btn_summary.setIconSize(QSize(18, 18))
        header_layout.addWidget(self.btn_summary)

        layout.addWidget(header_bar)

        settings_group = QGroupBox("Input and Output")
        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 14)
        form_layout.setSpacing(10)

        self.file_input = QLineEdit()
        self.file_input.setPlaceholderText("Select Log_All.txt")
        self.file_input.setReadOnly(True)
        btn_browse_file = QPushButton("Browse File")
        btn_browse_file.setObjectName("iconSecondary")
        btn_browse_file.clicked.connect(self.browse_file)
        btn_browse_file.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        btn_browse_file.setIconSize(QSize(16, 16))
        
        file_box = QHBoxLayout()
        file_box.addWidget(self.file_input)
        file_box.addWidget(btn_browse_file)
        file_box.setContentsMargins(0, 0, 0, 0)
        
        self.dir_input = QLineEdit()
        default_out = os.path.join(os.path.expanduser('~'), 'Documents', 'Log_Output')
        self.dir_input.setText(default_out)
        self.dir_input.setPlaceholderText("Select output folder")
        btn_browse_dir = QPushButton("Browse Folder")
        btn_browse_dir.setObjectName("iconSecondary")
        btn_browse_dir.clicked.connect(self.browse_dir)
        btn_browse_dir.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        btn_browse_dir.setIconSize(QSize(16, 16))
        
        dir_box = QHBoxLayout()
        dir_box.addWidget(self.dir_input)
        dir_box.addWidget(btn_browse_dir)
        dir_box.setContentsMargins(0, 0, 0, 0)

        self.workorder_input = QLineEdit()
        self.workorder_input.setPlaceholderText(f"Optional. Leave blank to use {DUMMY_WORK_ORDER}")

        form_layout.addRow(QLabel("Source file:"), file_box)
        form_layout.addRow(QLabel("Output folder:"), dir_box)
        form_layout.addRow(QLabel("WORKORDER:"), self.workorder_input)
        
        settings_group.setLayout(form_layout)
        layout.addWidget(settings_group)

        status_group = QGroupBox("Session Status")
        status_layout = QFormLayout()
        status_layout.setContentsMargins(16, 18, 16, 14)
        status_layout.setSpacing(8)

        self.lbl_pattern = QLabel(f"Filename pattern: WORKORDER_DATE_TIME_MAC1_MAC2_RESULT.txt (default WORKORDER: {DUMMY_WORK_ORDER})")
        self.lbl_last_summary = QLabel(f"Last summary file: {self.last_summary_path}")
        self.lbl_last_summary_time = QLabel(f"Last summary time: {self.last_summary_time}")
        self.lbl_pattern.setWordWrap(True)
        self.lbl_last_summary.setWordWrap(True)
        self.lbl_last_summary_time.setWordWrap(True)

        status_layout.addRow(self.lbl_pattern)
        status_layout.addRow(self.lbl_last_summary)
        status_layout.addRow(self.lbl_last_summary_time)
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        self.stats_group = QGroupBox("Statistics")
        stats_layout = QGridLayout()
        stats_layout.setContentsMargins(16, 18, 16, 14)
        stats_layout.setHorizontalSpacing(18)
        stats_layout.setVerticalSpacing(12)

        self.card_total = self.create_stat_card("Total Logs", "0", "Scanned entries", accent=True)
        self.card_success = self.create_stat_card("Split Files", "0", "Created from source log")
        self.card_skipped = self.create_stat_card("Skipped", "0", "Invalid or unmatched blocks")
        self.card_pass = self.create_stat_card("PASS", "0", "Successful test results")
        self.card_fail = self.create_stat_card("FAIL", "0", "Failed test results")
        self.card_stop = self.create_stat_card("STOP", "0", "Stopped test results")
        self.card_pass_rate = self.create_stat_card("Pass Rate", "0.00%", "PASS / total", accent=True)
        self.card_fail_rate = self.create_stat_card("Fail Rate", "0.00%", "FAIL / total")

        stats_layout.addWidget(self.card_total["frame"], 0, 0)
        stats_layout.addWidget(self.card_success["frame"], 0, 1)
        stats_layout.addWidget(self.card_skipped["frame"], 0, 2)
        stats_layout.addWidget(self.card_pass["frame"], 1, 0)
        stats_layout.addWidget(self.card_fail["frame"], 1, 1)
        stats_layout.addWidget(self.card_stop["frame"], 1, 2)
        stats_layout.addWidget(self.card_pass_rate["frame"], 2, 0)
        stats_layout.addWidget(self.card_fail_rate["frame"], 2, 1)
        
        self.stats_group.setLayout(stats_layout)
        self.stats_group.setVisible(False)
        layout.addWidget(self.stats_group)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setPlaceholderText("Progress messages and status updates appear here...")
        layout.addWidget(self.console)

    def create_stat_card(self, title, value, hint, accent=False):
        card = QFrame()
        card.setObjectName("statCardAccent" if accent else "statCard")
        card.setMinimumHeight(92)
        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(4)
        card.setLayout(card_layout)

        title_label = QLabel(title)
        title_label.setObjectName("statLabel")
        title_label.setStyleSheet("color: #6b5f68; font-size: 11px; font-weight: 700;")
        value_label = QLabel(value)
        value_label.setObjectName("statValue")
        value_label.setStyleSheet("color: #2e3436; font-size: 20px; font-weight: 700;")
        hint_label = QLabel(hint)
        hint_label.setObjectName("statHint")
        hint_label.setStyleSheet("color: #7b727a; font-size: 11px;")
        hint_label.setWordWrap(True)

        card_layout.addWidget(title_label)
        card_layout.addWidget(value_label)
        card_layout.addWidget(hint_label)
        card_layout.addStretch(1)

        return {
            "frame": card,
            "title": title_label,
            "value": value_label,
            "hint": hint_label,
        }

    def browse_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Select source log file", "", "Text Files (*.txt);;All Files (*)")
        if filename:
            self.file_input.setText(os.path.normpath(filename))
            self.console.append(f"Selected source file: {filename}")

    def browse_dir(self):
        dirname = QFileDialog.getExistingDirectory(self, "Select output folder")
        if dirname:
            self.dir_input.setText(os.path.normpath(dirname))

    def reset_stats(self):
        self.card_total["value"].setText("0")
        self.card_success["value"].setText("0")
        self.card_skipped["value"].setText("0")
        self.card_pass["value"].setText("0")
        self.card_fail["value"].setText("0")
        self.card_stop["value"].setText("0")
        self.card_pass_rate["value"].setText("0.00%")
        self.card_fail_rate["value"].setText("0.00%")

    def validate_paths(self, require_input_file=False):
        input_file = self.file_input.text().strip()
        output_dir = self.dir_input.text().strip()

        if require_input_file and not input_file:
            QMessageBox.critical(self, "Error", "Source file is not set. Please select Log_All.txt first.", QMessageBox.Ok)
            return None, None

        if require_input_file and input_file and not os.path.isfile(input_file):
            QMessageBox.critical(self, "Error", "Source file does not exist. Please select a valid file.", QMessageBox.Ok)
            return None, None

        if not output_dir:
            QMessageBox.critical(self, "Error", "Output folder is not set. Please select a destination folder.", QMessageBox.Ok)
            return None, None

        return input_file, output_dir

    def set_processing_state(self, is_processing):
        self.btn_start.setEnabled(not is_processing)
        self.btn_summary.setEnabled(not is_processing)
        self.btn_start.setText("Processing..." if is_processing else "Start Split")
        self.btn_summary.setText("Working..." if is_processing else "Build Summary")

    def start_processing(self):
        input_file, output_dir = self.validate_paths(require_input_file=True)
        if not input_file:
            return

        self.set_processing_state(True)
        self.console.clear()
        self.stats_group.setVisible(False)
        
        self.thread = LogSplitterThread(input_file, output_dir, self.workorder_input.text())
        self.thread.progress.connect(self.log_message)
        self.thread.summary.connect(self.handle_split_summary)
        self.thread.error.connect(self.handle_error)
        self.thread.finished.connect(self.processing_finished)
        self.thread.start()

    def start_summary_generation(self):
        _, output_dir = self.validate_paths(require_input_file=False)
        if not output_dir:
            return

        self.set_processing_state(True)
        self.console.append("Generating summary...")
        self.summary_thread = SummaryThread(output_dir)
        self.summary_thread.progress.connect(self.log_message)
        self.summary_thread.summary.connect(self.update_stats)
        self.summary_thread.error.connect(self.handle_error)
        self.summary_thread.finished.connect(self.summary_finished)
        self.summary_thread.start()

    def log_message(self, msg):
        self.console.append(msg)
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

    def handle_split_summary(self, stats):
        QMessageBox.information(self, "Split Complete", format_stats_message(stats), QMessageBox.Ok)

    def update_stats(self, stats):
        t = stats['total']
        success = stats['success']
        skipped = stats['skipped']
        p = stats['pass']
        f = stats['fail']
        s = stats['stop']
        
        self.stats_group.setVisible(True)
        self.card_total["value"].setText(str(t))
        self.card_success["value"].setText(str(success))
        self.card_skipped["value"].setText(str(skipped))
        self.card_pass["value"].setText(str(p))
        self.card_fail["value"].setText(str(f))
        self.card_stop["value"].setText(str(s))
        
        pass_rate = (p / t * 100) if t else 0
        fail_rate = (f / t * 100) if t else 0
        self.card_pass_rate["value"].setText(f"{pass_rate:.2f}%")
        self.card_fail_rate["value"].setText(f"{fail_rate:.2f}%")

    def handle_error(self, err_msg):
        self.console.append(f"<font color='#ff3b30'>Error: {err_msg}</font>")
        self.set_processing_state(False)

    def summary_finished(self, summary_path):
        self.set_processing_state(False)
        self.last_summary_path = summary_path
        self.last_summary_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.lbl_last_summary.setText(f"Last summary file: {self.last_summary_path}")
        self.lbl_last_summary_time.setText(f"Last summary time: {self.last_summary_time}")
        QMessageBox.information(self, "Summary Complete", f"summary.txt was created at:\n{summary_path}", QMessageBox.Ok)

    def processing_finished(self):
        self.set_processing_state(False)

if __name__ == '__main__':
    # Enable high DPI scaling for better modern screens rendering
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    if os.path.exists(APP_ICON_PATH):
        app.setWindowIcon(QIcon(APP_ICON_PATH))
    ex = LogSplitterApp()
    ex.show()
    sys.exit(app.exec_())
