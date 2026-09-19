"""Background thread for loading the speech engine."""

from PyQt6.QtCore import QThread, pyqtSignal

from src.config import ASR_ENGINE
from src.core.debug import log


class ModelLoaderThread(QThread):
    """Load the engine on a background thread so the UI stays responsive.

    Which engine is a setting, not a rebuild — see src/audio/engines.py. The
    thread carries the name so the UI can switch engines by starting a new
    loader rather than restarting the app.
    """

    finished = pyqtSignal(object, object)   # (unused, engine)
    error = pyqtSignal(str)

    def __init__(self, engine_name: str | None = None, parent=None):
        super().__init__(parent)
        self.engine_name = engine_name or ASR_ENGINE

    def run(self):
        try:
            from src.audio.engines import load_engine

            engine = load_engine(self.engine_name)
            log.event("MODEL", f"{engine.label} engine ready")
            self.finished.emit(None, engine)
        except Exception as e:
            self.error.emit(str(e))
