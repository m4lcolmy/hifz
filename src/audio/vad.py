"""Voice Activity Detection — automatic speech chunking from a PCM stream.

Uses WebRTC VAD to split continuous audio into speech segments.
Feed raw 16 kHz Int16 PCM bytes via feed(); complete speech chunks
are returned when enough trailing silence is detected.
"""

import collections
import webrtcvad

from src.config import (
    SAMPLE_RATE, VAD_AGGRESSIVENESS, VAD_FRAME_MS,
    SILENCE_THRESHOLD_MS, MIN_SPEECH_MS, PRE_SPEECH_MS,
)


class ChunkDetector:
    """Accumulates PCM frames and emits complete speech segments."""

    # Bytes per VAD frame (16-bit = 2 bytes per sample)
    FRAME_BYTES = (SAMPLE_RATE * VAD_FRAME_MS // 1000) * 2

    def __init__(self):
        self._vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self._silence_limit = SILENCE_THRESHOLD_MS // VAD_FRAME_MS
        self._min_frames = MIN_SPEECH_MS // VAD_FRAME_MS
        self._pre_count = PRE_SPEECH_MS // VAD_FRAME_MS

        self._raw = bytearray()                                    # unprocessed bytes
        self._pre_roll = collections.deque(maxlen=self._pre_count) # recent silence frames
        self._speech = bytearray()                                 # current speech segment
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0

    @property
    def is_speaking(self) -> bool:
        return self._in_speech

    # ── Public API ─────────────────────────────────────────────────────

    def feed(self, pcm: bytes) -> list[bytes]:
        """Feed raw PCM bytes. Returns list of finalized speech chunks (may be empty)."""
        self._raw.extend(pcm)
        chunks: list[bytes] = []

        while len(self._raw) >= self.FRAME_BYTES:
            frame = bytes(self._raw[:self.FRAME_BYTES])
            del self._raw[:self.FRAME_BYTES]

            if self._vad.is_speech(frame, SAMPLE_RATE):
                self._on_speech(frame)
            else:
                chunk = self._on_silence(frame)
                if chunk is not None:
                    chunks.append(chunk)

        return chunks

    def flush(self) -> bytes | None:
        """Return any remaining speech buffer (call when stopping)."""
        if self._in_speech and self._speech_frames >= self._min_frames:
            result = bytes(self._speech)
            self._reset()
            return result
        self._reset()
        return None

    def reset(self):
        """Discard all state."""
        self._raw.clear()
        self._pre_roll.clear()
        self._reset()

    # ── Internal ───────────────────────────────────────────────────────

    def _on_speech(self, frame: bytes):
        if not self._in_speech:
            self._in_speech = True
            self._speech_frames = 0
            self._silence_frames = 0
            # Prepend pre-roll for a cleaner onset
            for f in self._pre_roll:
                self._speech.extend(f)
            self._pre_roll.clear()

        self._silence_frames = 0
        self._speech.extend(frame)
        self._speech_frames += 1

    def _on_silence(self, frame: bytes) -> bytes | None:
        if not self._in_speech:
            self._pre_roll.append(frame)
            return None

        self._silence_frames += 1
        self._speech.extend(frame)  # include trailing silence

        if self._silence_frames >= self._silence_limit:
            if self._speech_frames >= self._min_frames:
                chunk = bytes(self._speech)
                self._reset()
                return chunk
            self._reset()
        return None

    def _reset(self):
        self._speech = bytearray()
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
