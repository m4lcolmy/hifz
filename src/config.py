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
# Measured 2026-09-19 against the first real-conditions recording in the
# corpus — a whole Mushaf page of An-Nisa (4:12-14, 119 words of dense
# inheritance law) recited at ~70 words/min into a room microphone:
#
#   window  step   coverage   false alarms   caught   room-mic false alarms
#   3000    300      99.3%        6.8%        2/2          17.0%
#   3000    150      99.6%        7.5%        2/2          17.7%
#   4000    200     100.0%        5.3%        2/2          10.1%
#
# 4000/200 is 42% more looks at every word (1134 windows against 801), and
# more looks is the whole mechanism by which this app survives a bad
# transcription: a word heard correctly in any one window stays unmarked.
# Whole-file transcription of that recording matches only 50% of the
# reference words, and the app still scores it at 10% false alarms.
#
# The trade: on the eleven older recordings the rate goes 0.6% -> 2.5%
# (1 false alarm -> 4). Those eleven are short, easy surahs and were what
# 3000/300 was tuned against; coverage on them improves, 99.4% -> 100%.
# Revert both numbers to 3000/300 to get that 0.6% back, and accept 17% on
# a real page.
#
# ASR p90 is 141 ms at a 4000 ms window, against a 200 ms step, so no window
# is dropped. The step must stay above the model's inference time or windows
# are built only to be discarded — that is why it is not lower still.
WINDOW_MS = 4000             # audio window handed to Whisper
WINDOW_STEP_MS = 200         # on GPU
WINDOW_STEP_MS_CPU = 800     # fallback when running on CPU — ~790 ms/window

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
# How far past the last recognised word the pointer may follow an alignment.
# A word the matcher could not recognise still gets paired with some reference
# word, because the aligner always pairs what it is given — but which one it
# landed on is arithmetic, not evidence. Following those pairings is how the
# pointer runs ahead of the reciter, and a pointer ahead of the reciter can
# match nothing he says until the miss timer gives up and re-discovers.
#
# Al-Fath 48:2 was being recited a second time. Three chunks running ended in
# a mis-transcribed token — مَرٌّ, تَقَ, تَعْمَلُونَ — and each was charged
# against whatever reference word the alignment reached: 48:2:11 -> 48:2:14 ->
# 48:3:3 -> 48:4:4, two ayahs past a reciter who had not moved. Then twenty-two
# transcriptions in a row matched nothing, the position was lost, and the page
# jumped.
#
# Zero is too strict — it stalls the pointer on the ordinary run of bad
# transcription and costs coverage. A small slack keeps up with recitation
# while still refusing a seven-word leap.
POINTER_SLACK = 2

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

# ── Skipping, or getting a word wrong ─────────────────────────────────
# The aligner is free to walk past reference words to reach a word that
# matches. That is right when the reciter really did skip ahead, and wrong
# when they simply said the wrong word and the word they said happens to
# occur a little further on — which in a Quran is most wrong words.
#
# 74:22 is ثُمَّ عَبَسَ وَبَسَرَ and 74:23 is ثُمَّ أَدْبَرَ وَاسْتَكْبَرَ. Recite
# "ثم عبس واستكبر" and the aligner steps over وَبَسَرَ, ثُمَّ and أَدْبَرَ to
# land on وَاسْتَكْبَرَ in the next ayah: the one real mistake is reported as
# correct, three untouched words are reported as skipped, and the pointer
# ends up an ayah further on than the reciter, where nothing they say next
# can match.
#
# So a skipped run must be paid for. This many reference words have to line
# up *consecutively* after the skip before it is believed; a lone word is
# coincidence, and the heard word is charged against the word that was
# actually due instead. A run of one skipped word is always believed —
# dropping a word is the commonest thing a reciter does and the alignment
# barely moves.
SKIP_RESUME_WORDS = 2

# A word transcribed wrong at the edge of an audio window was probably cut in
# half, so its verdict is withheld. Seen wrong at the edge of this many
# different windows, it is treated as genuinely wrong.
EDGE_CONFIRMATIONS = 2

# ── Waiting for the looks that have not arrived yet ───────────────────
# The whole mechanism of this app is that a word is seen by many windows and
# the best look wins: at a 4000 ms window and a 200 ms step, a word recited
# at 70 wpm sits inside about twenty of them. Whisper is roughly half right
# on one look, so several of those twenty come back wrong — and since a
# correct look permanently outranks a wrong one, the word goes red and then,
# a second later, goes back.
#
# Measured on tests/records before this constant existed: 16 words ended red,
# and 63 more had been red at some point and were retracted. 24% of every
# word scored. Al-Mutaffifin — a clean recitation, a clean final page — put
# 25 of its 69 words through red on the way. That is what "most of the words
# seem red but while reciting they're gone" describes, and the benchmark
# could not see any of it, because it scores the page as it is LEFT.
#
# So no colour goes on a word until the looks are finished. A word stays
# inside the sliding window for exactly one window's length after it is
# recited, so once nothing has mentioned it for that long, no further window
# ever will and its verdict is final. Until then the word is *uncovered and
# left plain* — not green, not amber: the app has heard it and has not
# finished deciding, and saying nothing is the honest way to say that.
#
# The cost is that a red arrives about this many seconds after the mistake,
# roughly four or five words further on. The word stays red on the page, so
# nothing is lost but immediacy — and immediacy was what was wrong.
SETTLE_SECONDS = WINDOW_MS / 1000.0

# How often the app asks whether anything has settled while no window is
# arriving — i.e. while the reciter is pausing. It only bounds how late a
# colour can be beyond SETTLE_SECONDS, so it is a fraction of it rather than
# a number in its own right.
SETTLE_TICK_MS = 400

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

# Re-matching recovers a verdict only where the transcription can be lined up
# with the reference. Where it cannot, the words are still gone from the page:
# discovery needs several words to say where the reciter is, so the run it
# spent getting there is exactly the run that never appears, and every session
# opens with its first phrase missing. Those words are *revealed* — shown in
# black, never coloured — back to the start of the ayah the lock landed in,
# because the app knows they were recited and does not know whether they were
# right. Capped, so locking deep into an 88-word ayah does not uncover the
# whole thing.
PRELOCK_REVEAL_WORDS = 12

# ── One-word ayahs ────────────────────────────────────────────────────
# الٓمٓ, كٓهيعٓصٓ, طه, يس, وَالْعَصْرِ — an ayah can be a single word, and the
# two-word discovery floor means such an ayah can never be found, only stepped
# over. Where that one word occurs exactly once in the whole Quran it is the
# least ambiguous phrase there is, so one word is enough.
DISCOVERY_SOLO_AYAH = True

# ── Echoed words ──────────────────────────────────────────────────────
# At a window tail the model repeats text it has already heard: 23:7 ends
# الْعَادُونَ and the next window came back "...الْعَادُونَ فَمَنِ ابْتَغَى" —
# 23:7's own opening again. Paired off positionally that echo condemned two
# words of 23:8 and dragged the pointer past the other three. A recited word
# matching a reference word this far back is treated as an echo and reported
# as not heard rather than as the word it lined up against.
ECHO_LOOKBACK = 25

# ── How much evidence before a word is painted red ────────────────────
# Whose decision this is: not the model's. Measured across 19 session logs —
# 4,792 wrong verdicts, 2,652 distinct heard/reference pairs — only 13% are a
# single letter apart, and the largest single-letter class is و↔ف, which is
# the class the deliberate mistake فَلَهُمْ/وَلَهُمْ belongs to. So forgiving a
# letter class is not a setting the app can offer honestly: Tier E established
# that the mistake and the false alarm are the same edit at the same distance.
#
# What the reciter CAN choose is which error they would rather have. Hifz
# review wants everything flagged; fluency practice wants to be stopped only
# for a real error. Each level below is a different answer to that, and each
# must carry measured numbers:
#
#     python scripts/benchmark_recordings.py --strictness all
#
#   strict     any letter difference is wrong, on one observation.
#   confirmed  the same, but a word must be heard wrong by this many separate
#              audio windows before it is painted red. Forgives no letter
#              class — it asks for more evidence, not for a smaller mistake —
#              so the deliberate mistakes should survive it. This generalizes
#              EDGE_CONFIRMATIONS, which already does exactly this, but only
#              for words sitting at a window boundary.
#   words      single-letter differences are not condemned. Known cost: this
#              is the class فَلَهُمْ/وَلَهُمْ belongs to, so it stops catching
#              that mistake. Labelled honestly rather than called "lenient".
#   follow     nothing is painted wrong. For reading along.
STRICTNESS = "confirmed"

STRICTNESS_LEVELS = {
    #                  windows that must    smallest difference   paint
    #                  agree it is wrong    worth condemning      red at all
    "strict":    {"confirmations": 1, "min_letter_diff": 1, "paint_wrong": True},
    "confirmed": {"confirmations": 2, "min_letter_diff": 1, "paint_wrong": True},
    "words":     {"confirmations": 1, "min_letter_diff": 2, "paint_wrong": True},
    "follow":    {"confirmations": 0, "min_letter_diff": 0, "paint_wrong": False},
}

#: One line each, for the settings menu.
STRICTNESS_LABELS = {
    "strict": "Strict — flag every difference",
    "confirmed": "Confirmed — flag once two windows agree",
    "words": "Words only — misses one-letter mistakes",
    "follow": "Follow along — never flag anything",
}


# ── Speech engine ─────────────────────────────────────────────────────
# Whisper is the default and the tuned one. A second engine is something you
# select and measure against the same corpus, never a silent swap:
#
#     python scripts/benchmark_recordings.py --engine ctc
#
# Tier E established why a second engine is the remaining avenue: the app's
# false alarms are acoustically identical to its real errors, so no comparison
# of strings can separate them. What is left is to mishear less often.
ASR_ENGINE = "whisper"                      # "whisper" | "ctc"
CTC_MODEL_DIR = PROJECT_ROOT / "models" / "ctc"
# Mean probability of the chosen symbols over non-blank frames. CTC's blank is
# a real "nothing here", so this is not produced by the same mechanism that
# does the hallucinating — unlike Whisper's avg_logprob.
CTC_MIN_CONFIDENCE = 0.35
