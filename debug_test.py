import sys
print("Starting application")
from PyQt5.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout

app = QApplication(sys.argv)
print("Created QApplication")
window = QWidget()
window.setWindowTitle("Test")
layout = QVBoxLayout()
layout.addWidget(QLabel("Hello World"))
window.setLayout(layout)
print("Created window")
window.show()
print("Showing window")
sys_exit_code = app.exec_()
print(f"Application exited with code: {sys_exit_code}")
sys.exit(sys_exit_code)