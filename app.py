"""Entry point for Hifz — Quran Recitation Trainer."""

import sys
import os

# Suppress Qt multimedia debug/info messages before any Qt import.
os.environ["QT_LOGGING_RULES"] = "qt.multimedia.ffmpeg=false"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from PyQt6.QtWidgets import QApplication

from src.ui.style import STYLESHEET, app_font
from src.ui import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setFont(app_font())
    app.setStyleSheet(STYLESHEET)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
