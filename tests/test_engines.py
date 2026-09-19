"""The engine seam. A second engine must be addable without touching anything
downstream, and selecting one must never be able to happen by accident."""

import unittest
from pathlib import Path

import numpy as np

from src.audio.engines import (
    DEFAULT_ENGINE, ENGINES, CtcEngine, Engine, EngineUnavailable,
    WhisperEngine, engine_choices, load_engine,
)
from src.audio.transcriber import transcribe_window


class FakeWhisperModel:
    """Stands in for faster-whisper: segments with the gate attributes."""

    class _Seg:
        def __init__(self, text, no_speech=0.0, logprob=0.0, ratio=1.0):
            self.text = text
            self.no_speech_prob = no_speech
            self.avg_logprob = logprob
            self.compression_ratio = ratio

    def __init__(self, segments):
        self.segments = segments

    def transcribe(self, audio, **kwargs):
        return self.segments, None


class RegistryTests(unittest.TestCase):
    def test_whisper_is_the_default(self):
        """A second engine is something you select and measure, never a
        silent swap — the working version has to stay the one that runs."""
        self.assertEqual(DEFAULT_ENGINE, "whisper")
        self.assertIn("whisper", ENGINES)

    def test_every_engine_is_describable_for_the_picker(self):
        for name, label, description in engine_choices():
            with self.subTest(engine=name):
                self.assertTrue(label)
                self.assertTrue(description, "the UI shows this to the user")

    def test_an_unknown_engine_fails_loudly(self):
        with self.assertRaises(EngineUnavailable):
            load_engine("does-not-exist")

    def test_every_engine_implements_the_seam(self):
        for cls in ENGINES.values():
            with self.subTest(engine=cls.name):
                self.assertTrue(issubclass(cls, Engine))
                for method in ("load", "transcribe"):
                    self.assertIsNot(getattr(cls, method),
                                     getattr(Engine, method),
                                     f"{cls.name} does not implement {method}")


class WhisperEngineTests(unittest.TestCase):
    """The gates tuned against this corpus must survive being moved."""

    def _engine(self, segments):
        engine = WhisperEngine.__new__(WhisperEngine)
        engine.model = FakeWhisperModel(segments)
        return engine

    def test_it_returns_the_kept_segments(self):
        engine = self._engine([FakeWhisperModel._Seg("قُلْ هُوَ اللَّهُ أَحَدٌ")])
        self.assertEqual(engine.transcribe(np.zeros(16000)), "قُلْ هُوَ اللَّهُ أَحَدٌ")

    def test_probable_silence_is_still_dropped(self):
        """Whisper invents words out of silence; this project kept getting تَعْمَى."""
        engine = self._engine([FakeWhisperModel._Seg("تَعْمَى", no_speech=0.95)])
        self.assertEqual(engine.transcribe(np.zeros(16000)), "")

    def test_low_confidence_is_still_dropped(self):
        engine = self._engine([FakeWhisperModel._Seg("شَيْء", logprob=-5.0)])
        self.assertEqual(engine.transcribe(np.zeros(16000)), "")

    def test_degenerate_repetition_is_still_dropped(self):
        engine = self._engine([FakeWhisperModel._Seg("لا لا لا", ratio=9.9)])
        self.assertEqual(engine.transcribe(np.zeros(16000)), "")


class CompatibilityTests(unittest.TestCase):
    def test_transcribe_window_still_accepts_a_bare_model(self):
        """Callers that predate the seam must keep working unchanged."""
        model = FakeWhisperModel([FakeWhisperModel._Seg("الْحَمْدُ لِلَّهِ")])
        self.assertEqual(transcribe_window(model, np.zeros(16000)), "الْحَمْدُ لِلَّهِ")

    def test_transcribe_window_delegates_to_an_engine(self):
        class Echo(Engine):
            name = label = "echo"
            def load(self): return self
            def transcribe(self, audio): return "بِسْمِ اللَّهِ"

        self.assertEqual(transcribe_window(Echo(), np.zeros(16000)), "بِسْمِ اللَّهِ")


class CtcAvailabilityTests(unittest.TestCase):
    def test_a_missing_model_says_what_to_do_about_it(self):
        """The common case is a model not downloaded yet, and the app should
        say so rather than fail obscurely."""
        engine = CtcEngine(model_dir=Path("/nonexistent/ctc-model"))
        with self.assertRaises(EngineUnavailable) as caught:
            engine.load()
        self.assertIn("fetch_ctc_model", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
