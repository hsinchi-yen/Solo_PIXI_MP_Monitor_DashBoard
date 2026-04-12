import sys
import os
import re
from datetime import datetime
from collections import Counter
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLineEdit, QLabel, 
                             QFileDialog, QTextEdit, QGroupBox, QGridLayout, 
                             QMessageBox, QFormLayout)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

# ========================================================
# Apple MacOS Style UI Stylesheet
# ========================================================
MAC_STYLESHEET = """
QWidget {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #1d1d1f;
    font-size: 13px;
}
QMainWindow {
    background-color: #f5f5f7;
}
QGroupBox {
    background-color: #ffffff;
    border: 1px solid #d2d2d7;
    border-radius: 10px;
    margin-top: 20px;
    padding-top: 15px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 5px;
    color: #1d1d1f;
    font-weight: 600;
    font-size: 14px;
}
QLineEdit {
    border: 1px solid #c7c7cc;
    border-radius: 6px;
    padding: 8px 12px;
    background-color: #ffffff;
    selection-background-color: #007aff;
    color: #1d1d1f;
}
QLineEdit:focus {
    border: 1px solid #007aff;
}
QLineEdit:disabled {
    background-color: #f2f2f7;
    color: #8e8e93;
}
QPushButton {
    background-color: #007aff;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #006ce6;
}
QPushButton:pressed {
    background-color: #005bb5;
}
QPushButton:disabled {
    background-color: #a1c6ea;
    color: #ffffff;
}
QPushButton#secondary {
    background-color: #ffffff;
    color: #1d1d1f;
    border: 1px solid #c7c7cc;
}
QPushButton#secondary:hover {
    background-color: #f2f2f7;
}
QPushButton#secondary:pressed {
    background-color: #e5e5ea;
}
QTextEdit {
    border: 1px solid #d2d2d7;
    border-radius: 8px;
    background-color: #ffffff;
    padding: 8px;
}
QLabel {
    font-size: 13px;
}
"""

class LogSplitterThread(QThread):
    progress = pyqtSignal(str)
    summary = pyqtSignal(dict)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, input_file, output_dir, work_order, smt_date):
        super().__init__()
        self.input_file = input_file
        self.output_dir = output_dir
        self.work_order = work_order
        self.smt_date = smt_date

    def run(self):
        try:
            self.progress.emit(f"開始處理來源: {self.input_file}")
            os.makedirs(self.output_dir, exist_ok=True)

            with open(self.input_file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            content = content.replace('\r\n', '\n').replace('\r', '\n')
            segments = re.split(r'(?=^MAC1:\t)', content, flags=re.MULTILINE)
            segments = [s for s in segments if s.strip().startswith("MAC1:")]
            
            total_segments = len(segments)
            self.progress.emit(f"偵測到總測試筆數: {total_segments}")
            
            success_count = 0
            skipped_count = 0
            results_list = []

            for i, seg in enumerate(segments):
                lines = seg.strip().split('\n')
                mac1 = mac2 = date_str = time_str = result = None

                for line in lines:
                    line_clean = line.strip()
                    if line_clean.startswith("MAC1:"):
                        mac1 = line_clean.split("\t")[-1].strip()
                    elif line_clean.startswith("MAC2:"):
                        mac2 = line_clean.split("\t")[-1].strip()
                    elif line_clean.startswith("Start:"):
                        dt_raw = line_clean.split("\t")[-1].strip()
                        try:
                            date_str = dt_raw[:10].replace("/", "")
                            time_str = dt_raw[11:].replace(":", "")
                        except:
                            pass
                    
                    if "**** P A S S ****" in line:
                        result = "PASS"
                    elif "**** F A I L ****" in line:
                        result = "FAIL"
                    elif "**** S T O P ****" in line:
                        result = "STOP"

                if all([mac1, mac2, date_str, time_str, result]):
                    filename = f"{self.work_order}_{date_str}_{time_str}_{mac1}_{mac2}_{result}.txt"
                    filepath = os.path.join(self.output_dir, filename)
                    
                    with open(filepath, "w", encoding="utf-8") as out_f:
                        out_f.write(seg.rstrip() + "\n\n")
                    success_count += 1
                    results_list.append(result)
                else:
                    self.progress.emit(f"跳過異常區塊: MAC1={mac1}, Result={result}")
                    skipped_count += 1

                if (i + 1) % 50 == 0 or (i + 1) == total_segments:
                    self.progress.emit(f"處理進度: {i+1}/{total_segments}...")

            summary_counts = Counter(results_list)
            
            summary_path = os.path.join(self.output_dir, "summary.txt")
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write("Log 分拆總結概況 (Overview)\n")
                f.write("="*40 + "\n")
                f.write(f"處理時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"工單 [Work Order]: {self.work_order}\n")
                f.write(f"SMT 日期 [SMT DATE]: {self.smt_date}\n")
                f.write(f"原始檔案: {self.input_file}\n")
                f.write(f"輸出主目錄: {self.output_dir}\n")
                f.write(f"總筆數 (Total Logs): {total_segments}\n")
                f.write(f"成功分拆筆數: {success_count}\n")
                f.write(f"異常忽略筆數: {skipped_count}\n")
                f.write("-" * 40 + "\n")
                f.write(f"PASS 數量: {summary_counts.get('PASS', 0)}\n")
                f.write(f"FAIL 數量: {summary_counts.get('FAIL', 0)}\n")
                f.write(f"STOP 數量: {summary_counts.get('STOP', 0)}\n")
                if total_segments > 0:
                    pass_rate = (summary_counts.get('PASS', 0) / total_segments) * 100
                    fail_rate = (summary_counts.get('FAIL', 0) / total_segments) * 100
                    f.write(f"成功率 (PASS): {pass_rate:.2f}%\n")
                    f.write(f"失敗率 (FAIL): {fail_rate:.2f}%\n")

            self.progress.emit(f"處理完成！已產出分拆檔案與 summary.txt 至:\n{self.output_dir}")
            
            stats = {
                "total": total_segments,
                "pass": summary_counts.get('PASS', 0),
                "fail": summary_counts.get('FAIL', 0),
                "stop": summary_counts.get('STOP', 0)
            }
            self.summary.emit(stats)
            self.finished.emit()
            
        except Exception as e:
            self.error.emit(f"解析發生錯誤: {str(e)}")

class LogSplitterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setWindowTitle('產線測試 Log 分拆分析工具')
        self.setMinimumSize(700, 600)
        self.setStyleSheet(MAC_STYLESHEET)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        main_widget.setLayout(layout)

        # ================================
        # 1. 執行資訊設定 (Form Layout)
        # ================================
        settings_group = QGroupBox("設定與資訊")
        form_layout = QFormLayout()
        form_layout.setContentsMargins(15, 20, 15, 15)
        form_layout.setSpacing(12)

        # (a) Source File
        self.file_input = QLineEdit()
        self.file_input.setPlaceholderText("請選擇 Log_All.txt")
        self.file_input.setReadOnly(True)
        btn_browse_file = QPushButton("瀏覽檔案")
        btn_browse_file.setObjectName("secondary")
        btn_browse_file.clicked.connect(self.browse_file)
        
        file_box = QHBoxLayout()
        file_box.addWidget(self.file_input)
        file_box.addWidget(btn_browse_file)
        file_box.setContentsMargins(0, 0, 0, 0)
        
        # (b) Output Path
        self.dir_input = QLineEdit()
        default_out = os.path.join(os.path.expanduser('~'), 'Documents', 'Log_Output')
        self.dir_input.setText(default_out)
        self.dir_input.setPlaceholderText("請選擇輸出資料夾")
        btn_browse_dir = QPushButton("選擇資料夾")
        btn_browse_dir.setObjectName("secondary")
        btn_browse_dir.clicked.connect(self.browse_dir)
        
        dir_box = QHBoxLayout()
        dir_box.addWidget(self.dir_input)
        dir_box.addWidget(btn_browse_dir)
        dir_box.setContentsMargins(0, 0, 0, 0)

        # (c) & (d) Metadata Inputs
        self.wo_input = QLineEdit()
        self.wo_input.setPlaceholderText("例如: 5101-2601102011")
        
        self.smt_input = QLineEdit()
        self.smt_input.setPlaceholderText("例如: 260105")

        # Organize into form
        form_layout.addRow(QLabel("來源檔案:"), file_box)
        form_layout.addRow(QLabel("輸出路徑:"), dir_box)
        form_layout.addRow(QLabel("工單 [Work Order]:"), self.wo_input)
        form_layout.addRow(QLabel("SMT 日期 [SMT DATE]:"), self.smt_input)
        
        settings_group.setLayout(form_layout)
        layout.addWidget(settings_group)

        # ================================
        # 2. 統計資訊顯示區塊
        # ================================
        stats_group = QGroupBox("統計資訊與進度")
        stats_layout = QGridLayout()
        stats_layout.setContentsMargins(15, 20, 15, 15)
        
        self.lbl_total = QLabel("總筆數: 0")
        self.lbl_pass = QLabel("PASS: 0")
        self.lbl_fail = QLabel("FAIL: 0")
        self.lbl_stop = QLabel("STOP: 0")
        self.lbl_pass_rate = QLabel("成功率: 0.00%")
        self.lbl_fail_rate = QLabel("失敗率: 0.00%")
        
        font_style = "font-weight: 600; font-size: 15px;"
        self.lbl_total.setStyleSheet(f"{font_style} color: #1d1d1f;")
        self.lbl_pass.setStyleSheet(f"{font_style} color: #34c759;") # Apple green
        self.lbl_fail.setStyleSheet(f"{font_style} color: #ff3b30;") # Apple red
        self.lbl_stop.setStyleSheet(f"{font_style} color: #ff9500;") # Apple orange
        
        stats_layout.addWidget(self.lbl_total, 0, 0)
        stats_layout.addWidget(self.lbl_pass, 0, 1)
        stats_layout.addWidget(self.lbl_fail, 0, 2)
        stats_layout.addWidget(self.lbl_stop, 1, 0)
        stats_layout.addWidget(self.lbl_pass_rate, 1, 1)
        stats_layout.addWidget(self.lbl_fail_rate, 1, 2)
        
        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        # ================================
        # 3. 執行日誌與按鍵
        # ================================
        self.btn_start = QPushButton("開始執行分拆")
        self.btn_start.setMinimumHeight(44)
        self.btn_start.clicked.connect(self.start_processing)
        layout.addWidget(self.btn_start)

        # Console
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setPlaceholderText("系統日誌將顯示於此...")
        layout.addWidget(self.console)

    def browse_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "選擇要分拆的 Log 檔案", "", "Text Files (*.txt);;All Files (*)")
        if filename:
            self.file_input.setText(os.path.normpath(filename))
            self.reset_stats()
            self.console.append(f"✓ 已選擇來源檔案: {filename}")

    def browse_dir(self):
        dirname = QFileDialog.getExistingDirectory(self, "選擇輸出目錄")
        if dirname:
            self.dir_input.setText(os.path.normpath(dirname))

    def reset_stats(self):
        self.lbl_total.setText("總筆數: 0")
        self.lbl_pass.setText("PASS: 0")
        self.lbl_fail.setText("FAIL: 0")
        self.lbl_stop.setText("STOP: 0")
        self.lbl_pass_rate.setText("成功率: 0.00%")
        self.lbl_fail_rate.setText("失敗率: 0.00%")

    def start_processing(self):
        input_file = self.file_input.text().strip()
        output_dir = self.dir_input.text().strip()
        work_order = self.wo_input.text().strip()
        smt_date = self.smt_input.text().strip()

        # 防呆機制 (e): A 與 B 未指定
        if not input_file:
            QMessageBox.critical(self, "錯誤", "來源檔案路徑未指定，請先選擇 Log_All.txt！", QMessageBox.Ok)
            return
        if not output_dir:
            QMessageBox.critical(self, "錯誤", "輸出路徑未指定，請設定要儲存結果的位置！", QMessageBox.Ok)
            return

        # 防呆機制 (f): C 與 D 沒有輸入
        if not work_order or not smt_date:
            reply = QMessageBox.warning(
                self, 
                "工單及 SMT 日期資訊尚未輸入", 
                "工單號碼或 SMT 日期留空，系統將自動採用 Dummy 方式輸出。\n是否繼續？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                if not work_order:
                    work_order = "XXXX-YYMMDDXXXX"
                if not smt_date:
                    smt_date = "YYMMDD"
            else:
                return

        # Disable UI and start
        self.btn_start.setEnabled(False)
        self.btn_start.setText("處理中...")
        self.console.clear()
        self.reset_stats()
        
        self.thread = LogSplitterThread(input_file, output_dir, work_order, smt_date)
        self.thread.progress.connect(self.log_message)
        self.thread.summary.connect(self.update_stats)
        self.thread.error.connect(self.handle_error)
        self.thread.finished.connect(self.processing_finished)
        self.thread.start()

    def log_message(self, msg):
        self.console.append(msg)
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

    def update_stats(self, stats):
        t = stats['total']
        p = stats['pass']
        f = stats['fail']
        s = stats['stop']
        
        self.lbl_total.setText(f"總筆數: {t}")
        self.lbl_pass.setText(f"PASS: {p}")
        self.lbl_fail.setText(f"FAIL: {f}")
        self.lbl_stop.setText(f"STOP: {s}")
        
        if t > 0:
            self.lbl_pass_rate.setText(f"成功率: {(p/t*100):.2f}%")
            self.lbl_fail_rate.setText(f"失敗率: {(f/t*100):.2f}%")

    def handle_error(self, err_msg):
        self.console.append(f"<font color='#ff3b30'>發生錯誤: {err_msg}</font>")
        self.btn_start.setEnabled(True)
        self.btn_start.setText("開始執行分拆")

    def processing_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_start.setText("開始執行分拆")

if __name__ == '__main__':
    # Enable high DPI scaling for better modern screens rendering
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    ex = LogSplitterApp()
    ex.show()
    sys.exit(app.exec_())
