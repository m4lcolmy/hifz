"""Background thread for loading the Whisper model and processor."""

from PyQt6.QtCore import QThread, pyqtSignal

from tarteel.config import MODEL_NAME, DEVICE, USE_FP16


class ModelLoaderThread(QThread):
    """Load the Whisper model on a background thread so the UI stays responsive."""

    finished = pyqtSignal(object, object)  # (processor, model)
    error = pyqtSignal(str)

    def run(self):
        try:
            from faster_whisper import WhisperModel

            if DEVICE == "cpu":
                compute_type = "int8"
            else:
                compute_type = "float16" if USE_FP16 else "float32"
            
            # Note: faster_whisper handles device and compute_type internally
            model = WhisperModel(
                MODEL_NAME, 
                device=DEVICE, 
                compute_type=compute_type,
                local_files_only=True
            )

            # In faster_whisper, the model object handles both processing and generation
            # We'll pass it as both for compatibility or adjust the transcriber
            self.finished.emit(None, model)
        except Exception as e:
            self.error.emit(str(e))
