"""Persistent transcription worker — lives on a dedicated QThread.

Uses the QObject/moveToThread pattern so Qt's event loop naturally
queues chunks: while one chunk is being transcribed, the next ones
wait in the event queue and are processed in order.

After transcription, matches against the Quran and emits a structured
comparison result (VerseMatch) instead of plain text.
"""

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from tarteel.config import SAMPLE_RATE, DEVICE, USE_FP16, MATCH_MIN_WORDS


class TranscriberWorker(QObject):
    """Receives audio chunks via a slot, transcribes, compares with Quran."""

    # Emits (raw_text, VerseMatch_or_None)
    result_ready = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, processor, model, quran_index, parent=None):
        super().__init__(parent)
        self.processor = processor
        self.model = model
        self.quran_index = quran_index

    @pyqtSlot(object)
    def process_chunk(self, data: dict):
        """Transcribe a raw Int16 PCM chunk, then match against Quran."""
        try:
            audio_bytes = data["audio"]
            context_surah = data.get("context_surah")
            context_ayah = data.get("context_ayah")
            import struct

            # Convert Int16 PCM bytes → float32 numpy in [-1, 1]
            n = len(audio_bytes) // 2
            samples = struct.unpack(f"<{n}h", audio_bytes[:n * 2])
            audio = np.array(samples, dtype=np.float32) / 32768.0

            # faster-whisper expects float32 numpy array in [-1, 1]
            # No need for torch or input_features manual prep
            
            segments, info = self.model.transcribe(
                audio,
                beam_size=5,
                language="ar",
                task="transcribe",
                initial_prompt="Quran recitation, classical Arabic, Uthmani script"
            )

            text = " ".join([segment.text for segment in segments]).strip()

            if not text:
                return

            # Attempt Quran matching
            words = text.split()
            match = None
            if len(words) >= MATCH_MIN_WORDS and self.quran_index is not None:
                match = self.quran_index.find_and_compare(text, context_surah, context_ayah)

            self.result_ready.emit({
                "text": text,
                "match": match,
            })

        except Exception as e:
            self.error.emit(str(e))
