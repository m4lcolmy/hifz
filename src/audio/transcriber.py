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
    """Transcribe one audio window, dropping anything the model is unsure of.

    Whisper hallucinates confidently on silence, so three gates are applied:
    Silero VAD removes the silence before the model sees it, and any segment
    that still comes back as probable-silence, low-confidence or degenerately
    repetitive is discarded rather than fed to the matcher.
    """
    segments, _info = model.transcribe(
        audio,
        beam_size=ASR_BEAM_SIZE,
        language="ar",
        task="transcribe",
        initial_prompt="Quran recitation, classical Arabic, Uthmani script",
        vad_filter=ASR_VAD_FILTER,
        no_speech_threshold=ASR_NO_SPEECH_THRESHOLD,
        log_prob_threshold=ASR_MIN_AVG_LOGPROB,
        compression_ratio_threshold=ASR_MAX_COMPRESSION_RATIO,
        # Each window is independent; conditioning on the previous one makes
        # the model repeat itself across overlapping windows.
        condition_on_previous_text=False,
    )

    kept = []
    for seg in segments:
        if _rejected(seg):
            continue
        kept.append(seg.text)
    return " ".join(kept).strip()


def _rejected(seg) -> str | None:
    """Return a reason to drop this segment, or None to keep it."""
    no_speech = getattr(seg, "no_speech_prob", 0.0)
    avg_logprob = getattr(seg, "avg_logprob", 0.0)
    compression = getattr(seg, "compression_ratio", 0.0)

    if no_speech > ASR_NO_SPEECH_THRESHOLD:
        reason = f"silence (no_speech={no_speech:.2f})"
    elif avg_logprob < ASR_MIN_AVG_LOGPROB:
        reason = f"low confidence (avg_logprob={avg_logprob:.2f})"
    elif compression > ASR_MAX_COMPRESSION_RATIO:
        reason = f"degenerate (compression={compression:.2f})"
    else:
        return None

    log.count("asr_rejected")
    log.event("ASR", f'rejected "{seg.text.strip()}" — {reason}')
    return reason


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
                # False when the chunk was too short to even try — the tracker
                # must not count those towards its re-discovery fallback.
                "attempted": decision.attempted,
            })

        except Exception as e:
            log.count("asr_errors")
            log.event("ERROR", f"transcribe/match failed: {e!r}")
            self.error.emit(str(e))
