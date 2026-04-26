import sys
from PyQt5.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout

app = QApplication(sys.argv)
window = QWidget()
window.setWindowTitle("Test")
layout = QVBoxLayout()
layout.addWidget(QLabel("Hello World"))
window.setLayout(layout)
window.show()
sys.exit(app.exec_())