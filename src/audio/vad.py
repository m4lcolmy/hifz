"""Voice Activity Detection — continuous sliding window from a PCM stream.

Uses WebRTC VAD to ensure a window has speech before emitting.
Feed raw 16 kHz Int16 PCM bytes via feed(); sliding windows are returned
continuously.
"""

from dataclasses import dataclass

import webrtcvad

from src.config import (
    SAMPLE_RATE, VAD_AGGRESSIVENESS, VAD_FRAME_MS, WINDOW_MS,
    WINDOW_STEP_MS, WINDOW_STEP_MS_CPU, DEVICE,
)
from src.core.device import resolve_device


def default_step_ms() -> int:
    """Window step matched to how fast this machine can transcribe.

    A smaller step means more overlapping looks at each word, which is what
    lets a fragment verdict get corrected — but only if inference keeps up.
    """
    device, _ = resolve_device(DEVICE)
    return WINDOW_STEP_MS if device == "cuda" else WINDOW_STEP_MS_CPU


@dataclass(frozen=True)
class Window:
    """One window of audio, and where it sat in the stream.

    The offsets are what lets a session be cut back up afterwards: a verdict
    can be traced to the exact audio that produced it, which is the whole
    point of recording a session as test data.
    """

    data: bytes
    start_sample: int
    end_sample: int

    @property
    def start_s(self) -> float:
        return self.start_sample / SAMPLE_RATE

    @property
    def end_s(self) -> float:
        return self.end_sample / SAMPLE_RATE


class SlidingWindowBuffer:
    """Accumulates PCM frames and emits overlapping sliding windows continuously."""

    # Bytes per VAD frame (16-bit = 2 bytes per sample)
    FRAME_BYTES = (SAMPLE_RATE * VAD_FRAME_MS // 1000) * 2

    def __init__(self, window_ms=WINDOW_MS, step_ms=None):
        if step_ms is None:
            step_ms = default_step_ms()
        self._vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self.window_bytes = (SAMPLE_RATE * window_ms // 1000) * 2
        self.step_bytes = (SAMPLE_RATE * step_ms // 1000) * 2
        self._buffer = bytearray()
        # Absolute byte offset of _buffer[0] within the whole stream.
        self._consumed = 0

    def feed(self, pcm: bytes) -> list[Window]:
        """Feed raw PCM bytes. Returns list of sliding windows if enough data accumulated."""
        self._buffer.extend(pcm)
        chunks: list[Window] = []

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
                chunks.append(Window(
                    window_data,
                    self._consumed // 2,
                    (self._consumed + self.window_bytes) // 2,
                ))

            # Slide the window forward
            del self._buffer[:self.step_bytes]
            self._consumed += self.step_bytes

        return chunks

    def flush(self) -> "Window | None":
        """Return any remaining buffer if it has speech."""
        if len(self._buffer) > 0:
            has_speech = False
            for i in range(0, len(self._buffer), self.FRAME_BYTES):
                frame = self._buffer[i:i + self.FRAME_BYTES]
                if len(frame) == self.FRAME_BYTES and self._vad.is_speech(frame, SAMPLE_RATE):
                    has_speech = True
                    break
            
            if has_speech:
                res = Window(
                    bytes(self._buffer),
                    self._consumed // 2,
                    (self._consumed + len(self._buffer)) // 2,
                )
                self._consumed += len(self._buffer)
                self._buffer.clear()
                return res

        self._consumed += len(self._buffer)
        self._buffer.clear()
        return None

    def reset(self):
        """Discard all state."""
        self._buffer.clear()
        self._consumed = 0
