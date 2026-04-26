import sys
import os
import re
from datetime import datetime
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

FILENAME_PATTERN = re.compile(
    r'^(?:([A-Za-z0-9]+-\d{8,})_)?(\d{8})_(\d{6})_([0-9A-F]+)_([0-9A-F]+)_(PASS|FAIL|STOP)\.txt$',
    re.IGNORECASE
)


class LogFileInfo:
    def __init__(self, filepath, date, time):
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.date = date
        self.time = time
        self.sort_key = (date, time)


def parse_filename(filepath):
    filename = os.path.basename(filepath)
    match = FILENAME_PATTERN.match(filename)
    if match:
        return LogFileInfo(
            filepath=filepath,
            date=match.group(2),
            time=match.group(3)
        )
    return None


def sort_files_chronologically(file_list):
    return sorted(file_list, key=lambda f: f.sort_key)


def format_datetime_from_filename(date_str, time_str):
    try:
        dt = datetime.strptime(date_str + time_str, '%Y%m%d%H%M%S')
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"


class LogConcatenateThread(QThread):
    progress = pyqtSignal(str)
    summary = pyqtSignal(dict)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, input_files, output_path):
        super().__init__()
        self.input_files = input_files
        self.output_path = output_path

    def run(self):
        try:
            self.progress.emit(f"Validating {len(self.input_files)} files...")

            valid_files = []
            skipped = 0
            first_datetime = None
            last_datetime = None

            for filepath in self.input_files:
                parsed = parse_filename(filepath)
                if parsed:
                    valid_files.append(parsed)
                    dt_str = format_datetime_from_filename(parsed.date, parsed.time)
                    if first_datetime is None:
                        first_datetime = dt_str
                    last_datetime = dt_str
                else:
                    skipped += 1
                    self.progress.emit(f"Skipped (invalid filename): {os.path.basename(filepath)}")

            if not valid_files:
                self.error.emit("No valid split log files found.")
                return

            sorted_files = sort_files_chronologically(valid_files)
            self.progress.emit(f"Processing {len(sorted_files)} files in chronological order...")

            total_size = 0
            for i, file_info in enumerate(sorted_files):
                try:
                    with open(file_info.filepath, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    total_size += len(content.encode('utf-8'))
                except Exception as e:
                    self.progress.emit(f"Warning: Could not read {file_info.filename}: {e}")

                if (i + 1) % 50 == 0 or (i + 1) == len(sorted_files):
                    self.progress.emit(f"Progress: {i+1}/{len(sorted_files)}...")

            self.progress.emit(f"Writing combined output to: {self.output_path}")

            with open(self.output_path, 'w', encoding='utf-8') as outfile:
                for i, file_info in enumerate(sorted_files):
                    try:
                        with open(file_info.filepath, 'r', encoding='utf-8', errors='replace') as infile:
                            content = infile.read().rstrip()
                        outfile.write(content)
                        if i < len(sorted_files) - 1:
                            outfile.write('\n\n')
                    except Exception as e:
                        self.progress.emit(f"Warning: Could not process {file_info.filename}: {e}")

            stats = {
                'total_selected': len(self.input_files),
                'valid_files': len(sorted_files),
                'skipped': skipped,
                'first_datetime': first_datetime or '-',
                'last_datetime': last_datetime or '-',
                'output_size': total_size,
                'output_path': self.output_path
            }

            self.progress.emit(f"Concatenation complete! Created: {self.output_path}")
            self.summary.emit(stats)
            self.finished.emit()

        except Exception as e:
            self.error.emit(f"Concatenation failed: {str(e)}")


class LogConcatenateApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.thread = None
        self.selected_files = []
        self.initUI()

    def initUI(self):
        self.setWindowTitle('Production Log Concatenate')
        self.setMinimumSize(800, 650)
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
        lbl_title = QLabel("Production Log Concatenate")
        lbl_title.setObjectName("windowTitle")
        lbl_subtitle = QLabel("Combine split log files into a single consolidated file")
        lbl_subtitle.setObjectName("windowSubtitle")
        lbl_subtitle.setWordWrap(True)
        title_layout.addWidget(lbl_title)
        title_layout.addWidget(lbl_subtitle)
        header_layout.addLayout(title_layout, 1)

        self.btn_start = QPushButton("Start Combine")
        self.btn_start.setMinimumHeight(42)
        self.btn_start.clicked.connect(self.start_processing)
        self.btn_start.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.btn_start.setIconSize(QSize(18, 18))
        header_layout.addWidget(self.btn_start)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("secondary")
        self.btn_clear.setMinimumHeight(42)
        self.btn_clear.clicked.connect(self.clear_selection)
        self.btn_clear.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.btn_clear.setIconSize(QSize(18, 18))
        header_layout.addWidget(self.btn_clear)

        layout.addWidget(header_bar)

        input_group = QGroupBox("Source Files")
        input_layout = QFormLayout()
        input_layout.setContentsMargins(16, 18, 16, 14)
        input_layout.setSpacing(10)

        self.file_input = QLineEdit()
        self.file_input.setPlaceholderText("Select multiple files or a directory containing split log files")
        self.file_input.setReadOnly(True)

        btn_browse_files = QPushButton("Browse Files")
        btn_browse_files.setObjectName("iconSecondary")
        btn_browse_files.clicked.connect(self.browse_files)
        btn_browse_files.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        btn_browse_files.setIconSize(QSize(16, 16))

        btn_browse_dir = QPushButton("Browse Directory")
        btn_browse_dir.setObjectName("iconSecondary")
        btn_browse_dir.clicked.connect(self.browse_directory)
        btn_browse_dir.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        btn_browse_dir.setIconSize(QSize(16, 16))

        file_box = QHBoxLayout()
        file_box.addWidget(self.file_input)
        file_box.addWidget(btn_browse_files)
        file_box.addWidget(btn_browse_dir)
        file_box.setContentsMargins(0, 0, 0, 0)

        input_layout.addRow(QLabel("Source:"), file_box)
        input_group.setLayout(input_layout)
        layout.addWidget(input_group)

        output_group = QGroupBox("Output")
        output_layout = QFormLayout()
        output_layout.setContentsMargins(16, 18, 16, 14)
        output_layout.setSpacing(10)

        self.output_dir_input = QLineEdit()
        default_out = os.path.join(os.path.expanduser('~'), 'Documents', 'Log_Output')
        self.output_dir_input.setText(default_out)
        self.output_dir_input.setPlaceholderText("Select output folder")

        btn_browse_output_dir = QPushButton("Browse")
        btn_browse_output_dir.setObjectName("iconSecondary")
        btn_browse_output_dir.clicked.connect(self.browse_output_dir)
        btn_browse_output_dir.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        btn_browse_output_dir.setIconSize(QSize(16, 16))

        output_dir_box = QHBoxLayout()
        output_dir_box.addWidget(self.output_dir_input)
        output_dir_box.addWidget(btn_browse_output_dir)
        output_dir_box.setContentsMargins(0, 0, 0, 0)

        self.output_filename_input = QLineEdit()
        self.output_filename_input.setText("Log_ALL.txt")
        self.output_filename_input.setPlaceholderText("Output filename")

        output_layout.addRow(QLabel("Output Folder:"), output_dir_box)
        output_layout.addRow(QLabel("Output Filename:"), self.output_filename_input)

        output_group.setLayout(output_layout)
        layout.addWidget(output_group)

        self.stats_group = QGroupBox("Statistics")
        stats_layout = QGridLayout()
        stats_layout.setContentsMargins(16, 18, 16, 14)
        stats_layout.setHorizontalSpacing(18)
        stats_layout.setVerticalSpacing(12)

        self.card_total = self.create_stat_card("Total Selected", "0", "Files selected")
        self.card_valid = self.create_stat_card("Valid Files", "0", "Files to combine")
        self.card_skipped = self.create_stat_card("Skipped", "0", "Invalid filenames")
        self.card_daterange = self.create_stat_card("Date Range", "-", "First to last", accent=True)

        stats_layout.addWidget(self.card_total["frame"], 0, 0)
        stats_layout.addWidget(self.card_valid["frame"], 0, 1)
        stats_layout.addWidget(self.card_skipped["frame"], 0, 2)
        stats_layout.addWidget(self.card_daterange["frame"], 0, 3)

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

    def browse_files(self):
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "Select split log files",
            "",
            "Text Files (*.txt);;All Files (*)"
        )
        if filenames:
            self.selected_files = [os.path.normpath(f) for f in filenames]
            self.file_input.setText(f"{len(self.selected_files)} files selected")
            self.console.append(f"Selected {len(self.selected_files)} files")
            self.validate_and_update_stats()

    def browse_directory(self):
        dirname = QFileDialog.getExistingDirectory(self, "Select directory containing split log files")
        if dirname:
            self.selected_files = []
            for entry in os.listdir(dirname):
                if entry.lower().endswith('.txt'):
                    self.selected_files.append(os.path.join(dirname, entry))
            self.selected_files = [os.path.normpath(f) for f in self.selected_files]
            self.file_input.setText(f"{len(self.selected_files)} files from directory")
            self.console.append(f"Found {len(self.selected_files)} files in directory")
            self.validate_and_update_stats()

    def browse_output_dir(self):
        dirname = QFileDialog.getExistingDirectory(self, "Select output folder")
        if dirname:
            self.output_dir_input.setText(os.path.normpath(dirname))

    def validate_and_update_stats(self):
        if not self.selected_files:
            return

        valid_count = 0
        skipped_count = 0
        first_dt = None
        last_dt = None

        for filepath in self.selected_files:
            parsed = parse_filename(filepath)
            if parsed:
                valid_count += 1
                dt_str = format_datetime_from_filename(parsed.date, parsed.time)
                if first_dt is None:
                    first_dt = dt_str
                last_dt = dt_str
            else:
                skipped_count += 1

        self.stats_group.setVisible(True)
        self.card_total["value"].setText(str(len(self.selected_files)))
        self.card_valid["value"].setText(str(valid_count))
        self.card_skipped["value"].setText(str(skipped_count))

        if first_dt and last_dt:
            if first_dt == last_dt:
                self.card_daterange["value"].setText(first_dt)
            else:
                self.card_daterange["value"].setText(f"{first_dt[:10]}...")
        else:
            self.card_daterange["value"].setText("-")

        self.btn_start.setEnabled(valid_count > 0)
        if hasattr(self, 'console'):
            if valid_count > 0:
                self.console.append(f"Found {valid_count} valid file(s). 'Start Combine' button enabled.")
            else:
                self.console.append(f"No valid files found.")

    def clear_selection(self):
        self.selected_files = []
        self.file_input.clear()
        self.stats_group.setVisible(False)
        self.btn_start.setEnabled(False)
        self.console.append("Selection cleared.")

    def set_processing_state(self, is_processing):
        self.btn_start.setEnabled(not is_processing)
        self.btn_clear.setEnabled(not is_processing)
        self.btn_start.setText("Processing..." if is_processing else "Start Combine")

    def start_processing(self):
        if not self.selected_files:
            QMessageBox.warning(self, "No Files", "Please select files to combine.", QMessageBox.Ok)
            return

        output_dir = self.output_dir_input.text().strip()
        output_filename = self.output_filename_input.text().strip()

        if not output_dir:
            QMessageBox.critical(self, "Error", "Output folder is not set.", QMessageBox.Ok)
            return

        if not output_filename:
            QMessageBox.critical(self, "Error", "Output filename is not set.", QMessageBox.Ok)
            return

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, output_filename)

        if os.path.exists(output_path):
            reply = QMessageBox.question(
                self,
                "File Exists",
                f"Output file already exists.\n\n{output_path}\n\nOverwrite?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.No:
                return

        self.set_processing_state(True)
        self.console.clear()

        self.thread = LogConcatenateThread(self.selected_files, output_path)
        self.thread.progress.connect(self.log_message)
        self.thread.summary.connect(self.handle_summary)
        self.thread.error.connect(self.handle_error)
        self.thread.finished.connect(self.processing_finished)
        self.thread.start()

    def log_message(self, msg):
        self.console.append(msg)
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

    def handle_summary(self, stats):
        msg = (
            f"Concatenation Complete!\n\n"
            f"Total selected: {stats['total_selected']}\n"
            f"Valid files: {stats['valid_files']}\n"
            f"Skipped: {stats['skipped']}\n"
            f"Date range: {stats['first_datetime']} - {stats['last_datetime']}\n"
            f"Output: {stats['output_path']}"
        )
        QMessageBox.information(self, "Complete", msg, QMessageBox.Ok)

    def handle_error(self, err_msg):
        self.console.append(f"<font color='#ff3b30'>Error: {err_msg}</font>")
        self.set_processing_state(False)

    def processing_finished(self):
        self.set_processing_state(False)


if __name__ == '__main__':
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    if os.path.exists(APP_ICON_PATH):
        app.setWindowIcon(QIcon(APP_ICON_PATH))
    ex = LogConcatenateApp()
    ex.show()
    sys.exit(app.exec_())
