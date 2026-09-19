"""Speech engines, behind one seam, so a second one can be measured.

The app has always had exactly one way to turn audio into text: Whisper,
through `transcribe_window()`. Tier E closed every remaining way of telling a
real recitation error from a mis-hearing by comparing strings — the deliberate
mistake فَلَهُمْ/وَلَهُمْ and the false alarm خُوبًا/حُوبًا are the same edit at the
same distance — so what is left is to mishear less often.

This is the seam for trying that, not the answer to it. **Whisper stays the
default and is untouched**; a second engine is something you select and
measure against the same corpus:

    python scripts/benchmark_recordings.py --engine ctc

Why a CTC engine is the candidate: Whisper decodes generatively, so it can
emit fluent text that was never said — this project spent months working
around "تَعْمَى" invented out of silence, word fragments at window boundaries,
and the echoed-tail problem where a window repeats what it already heard.
A CTC model emits one symbol per audio frame and structurally cannot do any
of that. Tilawa benchmarked this exhaustively and stopped using Whisper
(`lab/EXPERIMENTS.md`, findings 1-3).

Adding an engine means subclassing `Engine`, implementing `load()` and
`transcribe()`, and registering it in `ENGINES`. Nothing else changes: the
windowing, matching, tracking and scoring all sit downstream and never learn
which engine produced the text.
"""

from pathlib import Path

import numpy as np

from src.config import (
    SAMPLE_RATE, MODEL_DIR, DEVICE, USE_FP16, CTC_MODEL_DIR,
    CTC_MIN_CONFIDENCE,
    ASR_BEAM_SIZE, ASR_VAD_FILTER, ASR_NO_SPEECH_THRESHOLD,
    ASR_MIN_AVG_LOGPROB, ASR_MAX_COMPRESSION_RATIO,
)
from src.core.debug import log
from src.core.device import resolve_device


class EngineUnavailable(RuntimeError):
    """The engine is known but cannot run here — usually a missing model.

    Carries a message that says what to do about it, because the common case
    is a model that has not been downloaded yet and the app should say so
    rather than fail obscurely.
    """


class Engine:
    """Audio in, Arabic text out. Everything downstream is engine-agnostic."""

    name = "engine"
    label = "Engine"
    #: One line for the model picker in the UI.
    description = ""

    def load(self) -> "Engine":
        raise NotImplementedError

    def transcribe(self, audio: np.ndarray) -> str:
        """One window of float32 mono at SAMPLE_RATE → text, or "" for nothing.

        Returning "" is a normal answer and means *nothing worth matching*.
        An engine is expected to do its own filtering: the caller cannot tell
        a confident hallucination from a real transcription.
        """
        raise NotImplementedError


class WhisperEngine(Engine):
    """faster-whisper, exactly as the app has always run it.

    The three gates are the ones that were tuned against this corpus and they
    are deliberately left alone — this class is a move, not a rewrite, so that
    a bake-off compares a new engine against the app as it really behaves.
    """

    name = "whisper"
    label = "Whisper"
    description = "Generative. The tuned default — accurate, and it invents."

    def __init__(self, model_dir: str = MODEL_DIR):
        self.model_dir = str(model_dir)
        self.model = None

    def load(self) -> "WhisperEngine":
        from faster_whisper import WhisperModel

        device, compute_type = resolve_device(DEVICE, USE_FP16)
        log.event("MODEL", f"loading whisper on {device}/{compute_type}")
        self.model = WhisperModel(
            self.model_dir, device=device, compute_type=compute_type,
            local_files_only=True,
        )
        return self

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _info = self.model.transcribe(
            audio,
            beam_size=ASR_BEAM_SIZE,
            language="ar",
            task="transcribe",
            initial_prompt="Quran recitation, classical Arabic, Uthmani script",
            vad_filter=ASR_VAD_FILTER,
            no_speech_threshold=ASR_NO_SPEECH_THRESHOLD,
            log_prob_threshold=ASR_MIN_AVG_LOGPROB,
            compression_ratio_threshold=ASR_MAX_COMPRESSION_RATIO,
            # Each window is independent; conditioning on the previous one
            # makes the model repeat itself across overlapping windows.
            condition_on_previous_text=False,
        )
        return " ".join(
            seg.text for seg in segments if not _rejected_segment(seg)
        ).strip()


class CtcEngine(Engine):
    """A frame-synchronous CTC model, via transformers `AutoModelForCTC`.

    CTC assigns one symbol per audio frame, so there is no decoder free to
    continue a plausible sentence. Silence produces blanks, and a window that
    cuts a word in half produces half a word rather than a confident guess at
    the whole one. Those are the two behaviours most of this codebase exists
    to compensate for.

    Confidence is the mean probability of the chosen symbols over non-blank
    frames, which is a real per-window number — unlike Whisper's avg_logprob,
    it is not produced by the same mechanism that does the hallucinating.
    """

    name = "ctc"
    label = "CTC"
    description = "Frame-synchronous. Cannot invent words out of silence."

    def __init__(self, model_dir=CTC_MODEL_DIR):
        self.model_dir = Path(model_dir)
        self.model = None
        self.processor = None
        self.device = "cpu"

    def load(self) -> "CtcEngine":
        if not self.model_dir.exists():
            raise EngineUnavailable(
                f"No CTC model at {self.model_dir}.\n"
                f"  python scripts/fetch_ctc_model.py --help"
            )
        try:
            import torch
            from transformers import AutoModelForCTC, AutoProcessor
        except ImportError as e:      # pragma: no cover - environment issue
            raise EngineUnavailable(f"CTC needs torch and transformers: {e}")

        device, _ = resolve_device(DEVICE, USE_FP16)
        self.device = "cuda" if device == "cuda" else "cpu"
        log.event("MODEL", f"loading ctc on {self.device}")
        self.processor = AutoProcessor.from_pretrained(
            str(self.model_dir), local_files_only=True
        )
        self.model = AutoModelForCTC.from_pretrained(
            str(self.model_dir), local_files_only=True
        ).to(self.device).eval()
        self._torch = torch
        return self

    def transcribe(self, audio: np.ndarray) -> str:
        torch = self._torch
        inputs = self.processor(
            audio, sampling_rate=SAMPLE_RATE, return_tensors="pt"
        )
        values = inputs.input_values.to(self.device)
        with torch.inference_mode():
            logits = self.model(values).logits[0]

        probs = logits.softmax(-1)
        ids = probs.argmax(-1)

        # Blank is CTC's "nothing here", and a window of silence is almost all
        # blank. Whisper needed three gates to reach the same conclusion.
        blank = getattr(self.model.config, "pad_token_id", 0) or 0
        voiced = ids != blank
        if not bool(voiced.any()):
            return ""
        confidence = float(probs.max(-1).values[voiced].mean())
        if confidence < CTC_MIN_CONFIDENCE:
            log.count("ctc_low_confidence")
            return ""

        text = self.processor.batch_decode(ids.unsqueeze(0))[0]
        return text.strip()


ENGINES = {e.name: e for e in (WhisperEngine, CtcEngine)}
DEFAULT_ENGINE = WhisperEngine.name


def engine_choices() -> list[tuple[str, str, str]]:
    """(name, label, description) for every engine — for the UI and --help."""
    return [(e.name, e.label, e.description) for e in ENGINES.values()]


def load_engine(name: str | None = None, **kwargs) -> Engine:
    """Build and load an engine by name. Unknown names fail loudly."""
    name = (name or DEFAULT_ENGINE).lower()
    if name not in ENGINES:
        raise EngineUnavailable(
            f"Unknown engine {name!r}. Available: {', '.join(sorted(ENGINES))}"
        )
    return ENGINES[name](**kwargs).load()


def _rejected_segment(seg) -> bool:
    """Whisper invents words out of silence, confidently.

    Silero VAD removes the silence before the model sees it; anything that
    still comes back as probable-silence, low-confidence or degenerately
    repetitive is dropped rather than fed to the matcher.
    """
    if getattr(seg, "no_speech_prob", 0.0) > ASR_NO_SPEECH_THRESHOLD:
        log.count("asr_dropped_no_speech")
        return True
    if getattr(seg, "avg_logprob", 0.0) < ASR_MIN_AVG_LOGPROB:
        log.count("asr_dropped_low_logprob")
        return True
    if getattr(seg, "compression_ratio", 0.0) > ASR_MAX_COMPRESSION_RATIO:
        log.count("asr_dropped_repetitive")
        return True
    return False

