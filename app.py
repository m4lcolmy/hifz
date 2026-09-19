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

from src.core.debug import log, enable_from_env_or_args
from src.ui.style import STYLESHEET, app_font
from src.ui import MainWindow


def main():
    # Session log — on by default, silenced with --no-debug
    if enable_from_env_or_args(sys.argv):
        print(f"Session log → {log.path}")

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

    # --record keeps the session's audio and cuts it into benchmark cases on
    # stop. Every bug fixed so far was found by reciting and then could not be
    # turned into a test, because the audio was gone.
    record = "--record" in sys.argv
    if record:
        print("Recording this session → logs/session-*/")

    # --engine picks the speech engine (see src/audio/engines.py). Omitted,
    # the app runs the configured default, which is Whisper.
    engine = None
    if "--engine" in sys.argv:
        position = sys.argv.index("--engine") + 1
        if position >= len(sys.argv):
            from src.audio.engines import engine_choices
            names = ", ".join(name for name, _, _ in engine_choices())
            print(f"--engine needs a name. Available: {names}")
            return 2
        engine = sys.argv[position]
        print(f"Engine: {engine}")

    window = MainWindow(record=record, engine=engine)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
