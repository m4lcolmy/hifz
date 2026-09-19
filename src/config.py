"""Application-wide constants and configuration."""

from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATASET_DIR = DATA_DIR / "Quran_Dataset"
QURAN_JSON = DATA_DIR / "quran.json"

# ── Session logs ───────────────────────────────────────────────────────
# Logging is on by default: the sessions worth investigating are the ones
# nobody expected, and a flag you have to remember beforehand is no use then.
LOGS_DIR = PROJECT_ROOT / "logs"
DEBUG_LOG_DIR = LOGS_DIR
DEBUG_LOG_KEEP = 20          # keep the newest N runs, delete older ones

# ── QCF Assets ─────────────────────────────────────────────────────────
QCF_ASSETS_DIR = DATA_DIR / "qcf_assets"
QCF_FONTS_DIR = QCF_ASSETS_DIR / "fonts"
QCF_PAGES_DIR = QCF_ASSETS_DIR / "pages"
QCF_FONT_SIZE = 28          # pt — tunable glyph size
QCF_PAGE_LINES = 8          # lines per standard Mushaf page
QCF_LINE_SPACING = 1.4      # multiplier for vertical line spacing
QCF_WORD_SPACING = 2        # px gap between glyphs on a line

# ── Model ──────────────────────────────────────────────────────────────
MODEL_DIR = str(PROJECT_ROOT / "models")
# "auto" uses the GPU when one is usable and falls back to CPU otherwise.
# Measured on an RTX 3050 Ti: 75 ms per window on GPU vs 591 ms on CPU.
DEVICE = "auto"              # "auto" | "cuda" | "cpu"
USE_FP16 = True              # half-precision; ignored on CPU

# ── Audio ──────────────────────────────────────────────────────────────
SAMPLE_RATE = 16000  # Whisper expects 16 kHz
CHANNELS = 1

# ── Sliding window ────────────────────────────────────────────────────
# The step must not be far below the model's inference time or most windows
# are built only to be dropped — but every extra window is another look at
# each word, and more looks mean fewer words condemned on a bad fragment.
# So the step tracks inference speed: ~75 ms/window on GPU, ~600 ms on CPU.
WINDOW_MS = 3000             # audio window handed to Whisper
WINDOW_STEP_MS = 300         # on GPU
WINDOW_STEP_MS_CPU = 600     # fallback when running on CPU

# ── ASR quality gates ─────────────────────────────────────────────────
# Whisper invents words out of silence (this project kept getting "تَعْمَى").
# Silero VAD strips the silence before it reaches the model, and the
# thresholds below drop segments the model itself is not confident about.
ASR_BEAM_SIZE = 5
ASR_VAD_FILTER = True
ASR_NO_SPEECH_THRESHOLD = 0.6      # drop segment if it is probably silence
ASR_MIN_AVG_LOGPROB = -1.0         # drop low-confidence segments
ASR_MAX_COMPRESSION_RATIO = 2.4    # drop degenerate repeated text

# ── Voice Activity Detection ──────────────────────────────────────────
VAD_AGGRESSIVENESS = 2       # 0–3, higher = filters more noise
VAD_FRAME_MS = 30            # frame size in ms (10, 20, or 30)
SILENCE_THRESHOLD_MS = 400   # silence after speech → finalize chunk
MIN_SPEECH_MS = 300          # ignore speech segments shorter than this
PRE_SPEECH_MS = 300          # prepend this much audio before speech onset

# ── Quran Matching ────────────────────────────────────────────────────
MATCH_MIN_WORDS = 1          # minimum transcription words to attempt matching

# ── Discovery / Tracking Modes ────────────────────────────────────────
DISCOVERY_MIN_WORDS = 2      # min words before attempting discovery
DISCOVERY_NGRAM_SIZES = [5, 4, 3, 2]  # n-gram sizes to try (largest first)
# 2-grams are only consulted for short transcriptions. A unique bigram is a
# strong signal for a 2-word ayah, but on a long noisy transcription it is an
# easy way to jump somewhere wrong.
DISCOVERY_SHORT_NGRAM = 2            # n-gram size considered "short"
DISCOVERY_SHORT_MAX_WORDS = 3        # max transcription length allowed to use it

TRACKING_WINDOW = 25         # max words ahead of pointer in tracking mode
TRACKING_MAX_GAP = 20        # max words the reciter may skip and still be tracked
# Minimum words that must line up for a local match to be accepted. Tracking
# is already anchored to a known pointer and only looks in a small window, so
# demanding several matches mainly throws away the chunks that contain a
# mistake — which are exactly the chunks worth keeping.
TRACKING_MIN_MATCHES = 1
# A single matching word is weak evidence. Accept it only when the match sits
# right where the reciter was already pointing — i.e. they simply carried on.
# Further away, one word is coincidence and following it derails the tracker.
TRACKING_WEAK_EVIDENCE = 2     # matches at or below this count count as weak
TRACKING_WEAK_MAX_DRIFT = 4    # words a weak match may sit from the pointer
TRACKING_MAX_MISSES = 3      # consecutive failed matches before re-discovery
# ...but only once this much time has also passed. Pausing between ayahs is
# correct recitation, and during the pause the model emits breath fragments
# that cannot match. Three of those at 300 ms apart is under a second — far
# shorter than a normal breath — so a count alone threw the position away
# every time the reciter stopped to breathe.
TRACKING_MAX_MISS_SECONDS = 4.0
# After re-discovery, if the new position is ahead of where we lost the
# reciter in the same surah, the words in between were recited but never
# shown. Fill them in, up to this many words.
REDISCOVERY_MAX_GAP = 40

# A word transcribed wrong at the edge of an audio window was probably cut in
# half, so its verdict is withheld. Seen wrong at the edge of this many
# different windows, it is treated as genuinely wrong.
EDGE_CONFIRMATIONS = 2

# ── Before the lock ───────────────────────────────────────────────────
# Discovery refuses ambiguous openings, so the first seconds of a session are
# transcribed, rejected and thrown away — بسم الله الرحمن الرحيم matches 114
# places and الم matches six surahs plus every ألم. The words were recited and
# the audio was understood; only the *position* was unknown. Once it is known,
# those transcriptions are re-matched against the words just before the lock.
PRELOCK_BUFFER_SECONDS = 12.0   # how far back a re-match may reach
PRELOCK_MAX_CHUNKS = 40         # and at most this many transcriptions
PRELOCK_LOOKBACK_WORDS = 25     # reference words before the lock to search

# A word the reciter said before the lock is only claimed when the whole
# transcription lines up, or when at least this many words do. Discovery has
# already refused these chunks, so there is no pointer vouching for them.
PRELOCK_MIN_MATCHES = 2

# ── One-word ayahs ────────────────────────────────────────────────────
# الٓمٓ, كٓهيعٓصٓ, طه, يس, وَالْعَصْرِ — an ayah can be a single word, and the
# two-word discovery floor means such an ayah can never be found, only stepped
# over. Where that one word occurs exactly once in the whole Quran it is the
# least ambiguous phrase there is, so one word is enough.
DISCOVERY_SOLO_AYAH = True
