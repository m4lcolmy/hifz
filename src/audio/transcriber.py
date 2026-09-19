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
import time
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.config import (
    SAMPLE_RATE, ASR_BEAM_SIZE, ASR_VAD_FILTER, ASR_NO_SPEECH_THRESHOLD,
    ASR_MIN_AVG_LOGPROB, ASR_MAX_COMPRESSION_RATIO,
)
from src.core.debug import log
from src.core.matching import match_for_mode


def transcribe_window(model, audio) -> str:
    """Transcribe one audio window. `model` is an Engine, or a bare model.

    The engine seam lives in src/audio/engines.py. This function stays because
    it is the boundary every caller already uses — the app, the benchmark and
    the offline harnesses — and a bare faster-whisper model is still accepted
    so nothing that predates the seam has to change at once.
    """
    from src.audio.engines import Engine, WhisperEngine

    if isinstance(model, Engine):
        return model.transcribe(audio)

    # A bare WhisperModel: wrap it in the engine that knows how to drive it.
    engine = WhisperEngine.__new__(WhisperEngine)
    engine.model = model
    return engine.transcribe(audio)


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
        self._chunk_index = 0
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
                dropped = 0
                while not self._queue.empty():
                    try:
                        fresher_data = self._queue.get_nowait()
                        if fresher_data is not None:
                            data = fresher_data
                            dropped += 1
                    except queue.Empty:
                        break

                self._chunk_index += 1
                if log.enabled:
                    log.audio_chunk(
                        self._chunk_index, len(data["audio"]), SAMPLE_RATE, dropped
                    )

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
            
            t_start = time.monotonic()
            text = transcribe_window(self.model, audio)
            latency_ms = (time.monotonic() - t_start) * 1000

            if log.enabled:
                log.asr(text, latency_ms)

            if not text:
                return

            # Mode-aware Quran matching (shared with the offline benchmark)
            context = (context_surah, context_ayah, context_word_index)
            decision = match_for_mode(self.quran_index, text, mode, context)
            log.match(decision.mode, context, decision.match, decision.skip_reason)

            self.result_ready.emit({
                "text": text,
                "match": decision.match,
                "mode": mode,
                # Where this window sat in the session's audio, so a recorded
                # session can cut the audio at the verdicts it produced.
                "window": data.get("window"),
                # False when the chunk was too short to even try — the tracker
                # must not count those towards its re-discovery fallback.
                "attempted": decision.attempted,
            })

        except Exception as e:
            log.count("asr_errors")
            log.event("ERROR", f"transcribe/match failed: {e!r}")
            self.error.emit(str(e))
