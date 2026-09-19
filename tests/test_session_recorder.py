"""A recorded session has to be worth replaying, or Tier 3 buys nothing."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.audio.recorder import SessionRecorder, _slug
from src.audio.vad import SlidingWindowBuffer, Window
from src.config import SAMPLE_RATE


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


def speech(seconds: float, seed: int = 0) -> bytes:
    """Something the VAD keeps accepting — silence never reaches the window.

    It has to be non-stationary. WebRTC VAD adapts its noise estimate, so a
    steady tone is heard as speech for about a second and as background
    afterwards, which made a two-feed test look like a broken offset.
    """
    rng = np.random.default_rng(seed)
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n) / SAMPLE_RATE
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)          # syllable rate
    tone = np.sin(2 * np.pi * 140 * t) + 0.5 * np.sin(2 * np.pi * 430 * t)
    return (tone * envelope * 7000 + rng.normal(0, 800, n)).astype("<i2").tobytes()


def window(start_s: float, end_s: float) -> Window:
    return Window(b"", int(start_s * SAMPLE_RATE), int(end_s * SAMPLE_RATE))


class SlugTests(unittest.TestCase):
    def test_diacritics_are_dropped_from_the_filename(self):
        """Including the hamza seat, which is a combining mark too — the tag
        is for reading a directory listing, the manifest holds the exact text."""
        self.assertEqual(_slug("إِنَّا أَنزَلْنَاهُ فِي لَيْلَةِ"), "انا-انزلناه-في")

    def test_path_separators_never_reach_the_filename(self):
        self.assertNotIn("/", _slug("a/b c:d e"))

    def test_empty_text_still_yields_a_name(self):
        self.assertTrue(_slug(""))


class SegmentationTests(unittest.TestCase):
    """Where the session gets cut, and why it is cut there."""

    def _recorder(self, seconds=10.0):
        rec = SessionRecorder(Path(tempfile.mkdtemp()) / "session")
        rec.feed(silence(seconds))
        return rec

    def test_a_clip_is_not_truncated_by_the_next_ayah_starting(self):
        """Windows are 3s, so the tracker enters the next ayah early.

        Cutting each clip where the next one begins lopped the end off every
        ayah — 97:1 came out 0.9s long for five words. Clips overlap instead.
        """
        rec = self._recorder()
        rec.note(window(0.0, 3.0), 97, 1, "a")
        rec.note(window(2.4, 5.4), 97, 1, "b")
        rec.note(window(2.7, 5.7), 97, 2, "c")   # tracker steps ahead early
        rec.note(window(5.0, 8.0), 97, 2, "d")

        first, second = rec._segments[0], rec._segments[1]
        self.assertGreaterEqual(
            first.end_sample, int(5.4 * SAMPLE_RATE),
            "the clip ended where the next ayah began, cutting the ayah short",
        )
        self.assertLess(second.start_sample, first.end_sample,
                        "adjacent clips are expected to overlap")

    def test_the_first_located_ayah_starts_where_the_session_does(self):
        """Discovery needs a second or two, and the pre-lock re-match scores
        the words recited during it — so the audio belongs to that ayah."""
        rec = self._recorder()
        rec.note(window(0.0, 3.0), None, None, "unplaceable")
        rec.note(window(1.5, 4.5), 97, 1, "located")

        located = [s for s in rec._segments if s.surah is not None][0]
        self.assertEqual(located.start_sample, 0)

    def test_the_audio_before_the_lock_is_kept_as_its_own_clip(self):
        rec = self._recorder()
        rec.note(window(0.0, 3.0), None, None, "unplaceable")
        rec.note(window(1.5, 4.5), 97, 1, "located")

        self.assertIsNone(rec._segments[0].surah)

    def test_re_entering_an_ayah_opens_a_new_clip(self):
        """Going back is what a repeated ayah looks like, and it is worth
        keeping as a separate case rather than merging into the first pass."""
        rec = self._recorder()
        rec.note(window(0.0, 3.0), 97, 1, "a")
        rec.note(window(3.0, 6.0), 97, 2, "b")
        rec.note(window(6.0, 9.0), 97, 1, "c")

        self.assertEqual([(s.surah, s.ayah) for s in rec._segments],
                         [(97, 1), (97, 2), (97, 1)])


class ManifestTests(unittest.TestCase):
    """The manifest is the part a later run reads, so it has to be complete."""

    def _run(self):
        directory = Path(tempfile.mkdtemp()) / "session-test"
        rec = SessionRecorder(directory)
        rec.feed(silence(8.0))
        rec.note(window(0.0, 3.0), None, None, "unplaceable")
        rec.note(window(1.0, 4.0), 97, 1, "إنا أنزلناه")
        rec.note(window(4.0, 7.0), 97, 2, "وما أدراك")
        verdicts = [
            {"surah": 97, "ayah": 1, "word": 0, "reference": "إِنَّا",
             "recited": "إِنَّا", "verdict": "ok", "evidence": [1, 3, 1]},
            {"surah": 97, "ayah": 2, "word": 0, "reference": "وَمَا",
             "recited": "وَمَا", "verdict": "wrong", "evidence": [1, 2, 1]},
        ]
        rec.finish(verdicts, None)
        return directory

    def test_every_clip_named_in_the_manifest_exists(self):
        directory = self._run()
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["clips"])
        for clip in manifest["clips"]:
            self.assertTrue((directory / clip["file"]).exists(), clip["file"])
        self.assertTrue((directory / "full.flac").exists())

    def test_the_manifest_records_what_the_app_decided(self):
        """Without the verdicts a clip can only be re-judged by hand, which is
        the cost Tier 3 exists to remove."""
        directory = self._run()
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        located = [c for c in manifest["clips"] if c["surah"] == 97]
        self.assertTrue(located)
        wrong = [v for c in located for v in c["verdicts"]
                 if v["verdict"] == "wrong"]
        self.assertEqual(len(wrong), 1)
        self.assertEqual(wrong[0]["reference"], "وَمَا")

    def test_clips_are_scoped_to_their_own_ayah(self):
        directory = self._run()
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        for clip in manifest["clips"]:
            for v in clip["verdicts"]:
                self.assertEqual((v["surah"], v["ayah"]),
                                 (clip["surah"], clip["ayah"]))

    def test_a_session_with_no_audio_writes_nothing(self):
        directory = Path(tempfile.mkdtemp()) / "empty"
        self.assertIsNone(SessionRecorder(directory).finish([], None))
        self.assertFalse(directory.exists())


class WindowOffsetTests(unittest.TestCase):
    """Cutting a session back up depends on knowing where each window sat."""

    def test_windows_report_their_place_in_the_stream(self):
        buffer = SlidingWindowBuffer(window_ms=1000, step_ms=500)
        windows = buffer.feed(speech(3.0))

        self.assertTrue(windows)
        self.assertEqual(windows[0].start_sample, 0)
        for earlier, later in zip(windows, windows[1:]):
            self.assertEqual(later.start_sample - earlier.start_sample,
                             SAMPLE_RATE // 2)
            self.assertEqual(later.end_sample - later.start_sample, SAMPLE_RATE)

    def test_offsets_continue_across_feeds(self):
        buffer = SlidingWindowBuffer(window_ms=1000, step_ms=500)
        first = buffer.feed(speech(2.0, seed=1))
        second = buffer.feed(speech(2.0, seed=2))

        self.assertTrue(first and second)
        self.assertGreater(second[0].start_sample, first[-1].start_sample)


if __name__ == "__main__":
    unittest.main()
