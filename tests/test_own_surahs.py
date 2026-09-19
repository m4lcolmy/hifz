"""Whole-surah recordings, labelled by the surah number in the filename.

Tier D found two of eleven hand-made labels naming ayahs their audio does not
contain, because the ayah range was typed by a person from memory. A whole
surah has no range to type: it runs from ayah 1 to its last ayah, and how
many ayahs a surah has is a fact the app already holds.

So these tests are about the one thing left that can go wrong — the parse —
and in particular about the off-by-one. A person counting words in an ayah
starts at one; the tracker paints from zero. A label silently out by one is
the same class of error as a wrong ayah range and is harder to notice,
because everything still runs.
"""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.benchmark_recordings import OWN_SURAHS_DIR, cases_from_own_surahs
from src.core.quran import QuranIndex

INDEX = QuranIndex()


class LastAyahTests(unittest.TestCase):
    """The whole label rests on this."""

    def test_it_knows_how_long_a_surah_is(self):
        for surah, last in ((1, 7), (2, 286), (93, 11), (112, 4), (114, 6)):
            with self.subTest(surah=surah):
                self.assertEqual(INDEX.last_ayah(surah), last)

    def test_a_surah_that_does_not_exist_says_so(self):
        """Not 0, and not an exception: a filename is user input, and `115`
        has to come back as "that is not a surah" rather than as a case
        claiming an empty range."""
        for surah in (0, -3, 115, 200):
            with self.subTest(surah=surah):
                self.assertIsNone(INDEX.last_ayah(surah))


class FilenameTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, *names):
        for name in names:
            (self.dir / name).write_bytes(b"")
        return {p.name: e for p, e in cases_from_own_surahs(self.dir, INDEX)}

    def test_the_surah_number_fixes_the_whole_range(self):
        cases = self.load("093 ad-duhaa.flac", "112.flac")
        self.assertEqual(cases["093 ad-duhaa.flac"].ayahs, (1, 11))
        self.assertEqual(cases["112.flac"].ayahs, (1, 4))

    def test_a_deliberate_mistake_is_converted_from_human_counting(self):
        """`mistake 3:2` means the second word of ayah 3. The tracker paints
        word indices from zero, so exactly one conversion happens — here,
        where the 1-based half is a filename a person wrote."""
        cases = self.load("109 al-kafirun mistake 3:2.flac")
        self.assertEqual(cases["109 al-kafirun mistake 3:2.flac"].mistakes,
                         ((109, 3, 1),))

    def test_the_first_word_of_an_ayah_is_word_one(self):
        """The off-by-one that would be invisible: `mistake 1:1` must be word
        index 0, not -1 and not 1."""
        cases = self.load("112 mistake 1:1.flac")
        self.assertEqual(cases["112 mistake 1:1.flac"].mistakes, ((112, 1, 0),))

    def test_several_mistakes_in_one_recording(self):
        cases = self.load("109 mistake 2:3 mistake 4:1.flac")
        self.assertEqual(cases["109 mistake 2:3 mistake 4:1.flac"].mistakes,
                         ((109, 2, 2), (109, 4, 0)))

    def test_a_recording_with_no_mistake_claims_none(self):
        """A corpus that invents a caught mistake says the opposite of the
        truth about the only number a corpus of correct recitation cannot
        produce."""
        cases = self.load("099 az-zalzalah.flac")
        self.assertEqual(cases["099 az-zalzalah.flac"].mistakes, ())
        self.assertEqual(cases["099 az-zalzalah.flac"].mistake_count, 0)

    def test_partial_means_coverage_is_not_charged_for_what_was_never_said(self):
        cases = self.load("114 partial.flac", "114 whole.flac")
        self.assertTrue(cases["114 partial.flac"].partial)
        self.assertFalse(cases["114 whole.flac"].partial)

    def test_a_file_with_no_surah_number_is_reported_not_guessed(self):
        """Guessing a surah from the words in a filename is how a wrong label
        gets in. Skipping it loudly is the whole point."""
        self.assertEqual(self.load("ad-duhaa.flac"), {})

    def test_a_number_that_is_not_a_surah_is_refused(self):
        self.assertEqual(self.load("200 nope.flac"), {})

    def test_things_that_are_not_recordings_are_ignored(self):
        """The directory has a README in it."""
        self.assertEqual(self.load("README.md", "notes.txt"), {})

    def test_the_usual_recording_formats_are_all_accepted(self):
        names = ["093.flac", "094.wav", "095.mp3", "096.m4a", "097.ogg"]
        self.assertEqual(len(self.load(*names)), len(names))

    def test_a_missing_directory_is_not_an_error(self):
        """Most people will never put anything in it."""
        self.assertEqual(cases_from_own_surahs(self.dir / "nothing", INDEX), [])


class ReportingTests(unittest.TestCase):
    """These must not be averaged into the studio recordings."""

    def test_they_are_their_own_stratum_and_their_own_voice(self):
        """2.2% false alarms across 273 fetched cases against 25–48% on this
        reciter's own microphone. One overall number would bury a thirty
        point gap, which is the gap the app actually has to close."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "112.flac"
            path.write_bytes(b"")
            (_p, exp), = cases_from_own_surahs(Path(tmp), INDEX)
            self.assertTrue(exp.stratum)
            self.assertTrue(exp.reciter)
            self.assertNotEqual(exp.stratum, "")

    def test_the_directory_documents_itself(self):
        readme = OWN_SURAHS_DIR / "README.md"
        self.assertTrue(readme.exists(), "the directory explains how to use it")
        self.assertIn("mistake", readme.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
