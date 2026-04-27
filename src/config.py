"""Application-wide constants and configuration."""

from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATASET_DIR = DATA_DIR / "Quran_Dataset"
QURAN_JSON = DATA_DIR / "quran.json"

# ── Model ──────────────────────────────────────────────────────────────
MODEL_DIR = str(PROJECT_ROOT / "models")
DEVICE = "cpu"
USE_FP16 = False  # half-precision for faster inference on GPU

# ── Audio ──────────────────────────────────────────────────────────────
SAMPLE_RATE = 16000  # Whisper expects 16 kHz
CHANNELS = 1

# ── Voice Activity Detection ──────────────────────────────────────────
VAD_AGGRESSIVENESS = 2       # 0–3, higher = filters more noise
VAD_FRAME_MS = 30            # frame size in ms (10, 20, or 30)
SILENCE_THRESHOLD_MS = 400   # silence after speech → finalize chunk
MIN_SPEECH_MS = 300          # ignore speech segments shorter than this
PRE_SPEECH_MS = 300          # prepend this much audio before speech onset

# ── Quran Matching ────────────────────────────────────────────────────
MATCH_MIN_WORDS = 2          # minimum transcription words to attempt matching
