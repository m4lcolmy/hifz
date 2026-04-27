"""Audio recording via QtMultimedia and PCM-to-numpy conversion.

Uses the OS-native audio backend (PulseAudio/PipeWire on Linux,
WASAPI on Windows, CoreAudio on macOS) — no PortAudio dependency.
"""

import struct
import numpy as np

from PyQt6.QtMultimedia import QAudioFormat, QMediaDevices

from src.config import SAMPLE_RATE, CHANNELS


def default_audio_format() -> QAudioFormat:
    """Return the standard audio format for Whisper (16 kHz, mono, Int16)."""
    fmt = QAudioFormat()
    fmt.setSampleRate(SAMPLE_RATE)
    fmt.setChannelCount(CHANNELS)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return fmt


def get_input_device():
    """Return the default input device, or None if unavailable."""
    device = QMediaDevices.defaultAudioInput()
    return None if device.isNull() else device


def pcm_int16_to_float32(raw_bytes: bytes) -> np.ndarray:
    """Convert raw Int16 PCM bytes to a float32 numpy array in [-1, 1]."""
    sample_count = len(raw_bytes) // 2
    samples = struct.unpack(f"<{sample_count}h", raw_bytes[: sample_count * 2])
    return np.array(samples, dtype=np.float32) / 32768.0
