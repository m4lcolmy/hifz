"""Persistent transcription worker — uses LIFO queue for low latency.

Uses a background thread with queue.LifoQueue to drop stale audio frames
if processing falls behind, ensuring we always transcribe the freshest sliding window.

Mode-aware matching:
  - "discovery": calls quran_index.discover() — only returns unique matches
  - "tracking": calls quran_index.track() — local search only
"""

import struct
import queue
import threading
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.config import MATCH_MIN_WORDS, DISCOVERY_MIN_WORDS


class TranscriberWorker(QObject):
    """Receives audio chunks via a slot, transcribes, compares with Quran."""

    result_ready = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, processor, model, quran_index, parent=None):
        super().__init__(parent)
        self.processor = processor
        self.model = model
        self.quran_index = quran_index
        
        # Threading for LIFO
        self._queue = queue.LifoQueue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    @pyqtSlot(object)
    def process_chunk(self, data: dict):
        """Queue raw Int16 PCM chunk. UI calls this slot."""
        self._queue.put(data)
        
    def stop(self):
        """Stop the background thread cleanly."""
        self._stop_event.set()
        self._queue.put(None)
        self._thread.join()

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                # Wait for data
                data = self._queue.get(timeout=0.1)
                if data is None:
                    continue
                
                # Drain queue to drop stale overlapping frames
                while not self._queue.empty():
                    try:
                        fresher_data = self._queue.get_nowait()
                        if fresher_data is not None:
                            data = fresher_data
                    except queue.Empty:
                        break

                self._transcribe_and_match(data)
            except queue.Empty:
                continue

    def _transcribe_and_match(self, data: dict):
        """Transcribe a raw Int16 PCM chunk, then match against Quran."""
        try:
            audio_bytes = data["audio"]
            mode = data.get("mode", "discovery")
            context_surah = data.get("context_surah")
            context_ayah = data.get("context_ayah")
            context_word_index = data.get("context_word_index")

            # Convert Int16 PCM bytes → float32 numpy in [-1, 1]
            n = len(audio_bytes) // 2
            samples = struct.unpack(f"<{n}h", audio_bytes[:n * 2])
            audio = np.array(samples, dtype=np.float32) / 32768.0
            
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

            # Mode-aware Quran matching
            words = text.split()
            match = None

            if self.quran_index is not None:
                if mode == "tracking" and context_surah is not None and context_ayah is not None and context_word_index is not None:
                    # Tracking: local search only, never global
                    if len(words) >= MATCH_MIN_WORDS:
                        match = self.quran_index.track(text, context_surah, context_ayah, context_word_index)
                else:
                    # Discovery: exact N-gram lookup, uniqueness gated
                    if len(words) >= DISCOVERY_MIN_WORDS:
                        match = self.quran_index.discover(text)

            self.result_ready.emit({
                "text": text,
                "match": match,
                "mode": mode,
            })

        except Exception as e:
            self.error.emit(str(e))
