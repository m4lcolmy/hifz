"""Background thread for loading the Whisper model and processor."""

from PyQt6.QtCore import QThread, pyqtSignal

from src.config import MODEL_DIR, DEVICE, USE_FP16
from src.core.debug import log
from src.core.device import resolve_device


class ModelLoaderThread(QThread):
    """Load the Whisper model on a background thread so the UI stays responsive."""

    finished = pyqtSignal(object, object)  # (processor, model)
    error = pyqtSignal(str)

    def run(self):
        try:
            from faster_whisper import WhisperModel

            device, compute_type = resolve_device(DEVICE, USE_FP16)
            log.event("MODEL", f"loading on {device}/{compute_type}")

            model = WhisperModel(
                MODEL_DIR,
                device=device,
                compute_type=compute_type,
                local_files_only=True,
            )

            # In faster_whisper, the model object handles both processing and generation
            # We'll pass it as both for compatibility or adjust the transcriber
            self.finished.emit(None, model)
        except Exception as e:
            self.error.emit(str(e))
