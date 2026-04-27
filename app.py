"""Entry point for Hifz — Quran Recitation Trainer."""

import sys
import os

import warnings
# Suppress webrtcvad pkg_resources warning
warnings.filterwarnings("ignore", category=UserWarning, module="webrtcvad")

# Suppress Qt multimedia and font debug/info messages
os.environ["QT_LOGGING_RULES"] = "*.debug=false;*.info=false;qt.gui.fonts=false;qt.text.font.warning=false;qt.gui.text.warning=false"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QLoggingCategory

from src.ui.style import STYLESHEET, app_font
from src.ui import MainWindow


def main():
    # Suppress warnings via QLoggingCategory
    QLoggingCategory.setFilterRules("*.warning=false")
    
    def message_handler(mode, context, message):
        if "minimum bearings" in message:
            return
        # Print other messages to original handler (stderr)
        sys.stderr.write(f"{message}\n")

    from PyQt6.QtCore import qInstallMessageHandler
    qInstallMessageHandler(message_handler)

    app = QApplication(sys.argv)
    app.setFont(app_font())
    app.setStyleSheet(STYLESHEET)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
