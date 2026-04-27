"""Voice Activity Detection — continuous sliding window from a PCM stream.

Uses WebRTC VAD to ensure a window has speech before emitting.
Feed raw 16 kHz Int16 PCM bytes via feed(); sliding windows are returned
continuously.
"""

import webrtcvad

from src.config import (
    SAMPLE_RATE, VAD_AGGRESSIVENESS, VAD_FRAME_MS,
)


class SlidingWindowBuffer:
    """Accumulates PCM frames and emits overlapping sliding windows continuously."""

    # Bytes per VAD frame (16-bit = 2 bytes per sample)
    FRAME_BYTES = (SAMPLE_RATE * VAD_FRAME_MS // 1000) * 2

    def __init__(self, window_ms=3000, step_ms=300):
        self._vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self.window_bytes = (SAMPLE_RATE * window_ms // 1000) * 2
        self.step_bytes = (SAMPLE_RATE * step_ms // 1000) * 2
        self._buffer = bytearray()

    def feed(self, pcm: bytes) -> list[bytes]:
        """Feed raw PCM bytes. Returns list of sliding windows if enough data accumulated."""
        self._buffer.extend(pcm)
        chunks: list[bytes] = []

        while len(self._buffer) >= self.window_bytes:
            window_data = bytes(self._buffer[:self.window_bytes])
            
            # Simple VAD check: if any 30ms frame is speech, emit the window
            has_speech = False
            for i in range(0, len(window_data), self.FRAME_BYTES):
                frame = window_data[i:i + self.FRAME_BYTES]
                if len(frame) == self.FRAME_BYTES and self._vad.is_speech(frame, SAMPLE_RATE):
                    has_speech = True
                    break

            if has_speech:
                chunks.append(window_data)
            
            # Slide the window forward
            del self._buffer[:self.step_bytes]

        return chunks

    def flush(self) -> bytes | None:
        """Return any remaining buffer if it has speech."""
        if len(self._buffer) > 0:
            has_speech = False
            for i in range(0, len(self._buffer), self.FRAME_BYTES):
                frame = self._buffer[i:i + self.FRAME_BYTES]
                if len(frame) == self.FRAME_BYTES and self._vad.is_speech(frame, SAMPLE_RATE):
                    has_speech = True
                    break
            
            if has_speech:
                res = bytes(self._buffer)
                self._buffer.clear()
                return res
            
        self._buffer.clear()
        return None

    def reset(self):
        """Discard all state."""
        self._buffer.clear()
