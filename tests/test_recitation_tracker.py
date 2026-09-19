import unittest

from src.config import TRACKING_MAX_MISSES
from src.core.quran import QuranIndex
from src.core.page_map import PageMap
from src.ui.recitation import RecitationTracker


class DummyMushafView:
    def __init__(self):
        self.current_page_num = 1
        self.loaded_pages = []
        self.updates = []

    def load_page(self, page_num: int):
        self.current_page_num = page_num
        self.loaded_pages.append(page_num)

    def update_recitation(self, surah: int, ayah: int, word_index: int, status):
        self.updates.append((surah, ayah, word_index, status))

    def final(self) -> dict:
        """Last colour written per word — what the reciter actually sees."""
        out = {}
        for surah, ayah, word_index, status in self.updates:
            out[(surah, ayah, word_index)] = status
        return out


class RecitationTrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = QuranIndex()
        cls.page_map = PageMap()

    def _tracker(self):
        return DummyMushafView(), None

    def test_first_match_locks_position_for_tracking(self):
        """This is the regression that silently disabled tracking mode."""
        mushaf = DummyMushafView()
        tracker = RecitationTracker(mushaf, self.page_map)

        text = "قل هو الله احد"
        match = self.index.discover(text)
        self.assertIsNotNone(match)

        tracker.on_result({"text": text, "match": match, "mode": "discovery"})

        self.assertEqual(tracker.mode, "tracking")
        # Pointer must sit on the last recited word, or track() gets no context
        self.assertEqual(
            (tracker.last_surah, tracker.last_ayah, tracker.last_word_index),
            (112, 1, 3),
        )

    def test_tracking_advances_pointer_across_chunks(self):
        mushaf = DummyMushafView()
        tracker = RecitationTracker(mushaf, self.page_map)

        first = "قل هو الله احد"
        tracker.on_result({"text": first, "match": self.index.discover(first), "mode": "discovery"})

        second = "الله الصمد"
        match = self.index.track(
            second, tracker.last_surah, tracker.last_ayah, tracker.last_word_index
        )
        self.assertIsNotNone(match)
        tracker.on_result({"text": second, "match": match, "mode": "tracking"})

        self.assertEqual((tracker.last_surah, tracker.last_ayah), (112, 2))

    def test_repeated_misses_fall_back_to_discovery(self):
        mushaf = DummyMushafView()
        tracker = RecitationTracker(mushaf, self.page_map)

        text = "قل هو الله احد"
        tracker.on_result({"text": text, "match": self.index.discover(text), "mode": "discovery"})
        self.assertEqual(tracker.mode, "tracking")

        for _ in range(TRACKING_MAX_MISSES):
            tracker.on_result(
                {"text": "كلام غير معروف", "match": None, "mode": "tracking", "attempted": True}
            )

        self.assertEqual(tracker.mode, "discovery")
        self.assertIsNone(tracker.last_surah)

    def test_short_chunks_do_not_trigger_fallback(self):
        """Chunks too short to even attempt a match must not count as misses."""
        mushaf = DummyMushafView()
        tracker = RecitationTracker(mushaf, self.page_map)

        text = "قل هو الله احد"
        tracker.on_result({"text": text, "match": self.index.discover(text), "mode": "discovery"})

        for _ in range(TRACKING_MAX_MISSES + 2):
            tracker.on_result(
                {"text": "و", "match": None, "mode": "tracking", "attempted": False}
            )

        self.assertEqual(tracker.mode, "tracking")

    def test_skipped_words_are_highlighted_as_missed(self):
        """Gap-filled words go on the page amber (None), not red (False)."""
        mushaf = DummyMushafView()
        tracker = RecitationTracker(mushaf, self.page_map)
        tracker.set_position(1, 2, 0)

        text = "اياك نعبد واياك نستعين"
        match = self.index.track(text, 1, 2, 0)
        self.assertIsNotNone(match)
        tracker.on_result({"text": text, "match": match, "mode": "tracking"})

        # 1:2 and 1:3 were skipped → missed (None).
        # 1:5 was recited → scored as a bool, never None.
        skipped = [s for su, a, _, s in mushaf.updates if (su, a) in {(1, 2), (1, 3), (1, 4)}]
        recited = [s for su, a, _, s in mushaf.updates if (su, a) == (1, 5)]

        self.assertTrue(skipped)
        self.assertTrue(all(s is None for s in skipped))
        self.assertTrue(recited)
        self.assertTrue(all(s is not None for s in recited))


class EvidenceScoringTests(unittest.TestCase):
    """A word's colour must follow the best evidence, not the first look."""

    @classmethod
    def setUpClass(cls):
        cls.index = QuranIndex()
        cls.page_map = PageMap()

    def _word(self, surah, ayah, idx, recited, reference, correct,
              gap=False):
        from src.core.quran import WordResult
        return WordResult(recited=recited, reference=reference,
                          is_correct=correct, surah_id=surah, ayah_id=ayah,
                          reference_index=idx, is_gap_filled=gap)

    def _match(self, words, surah=8, ayah=3):
        from src.core.quran import VerseMatch
        return VerseMatch(surah_id=surah, surah_name="Al-Anfal", ayah_id=ayah,
                          start_offset=0, words=words)

    def test_fragment_verdict_is_upgraded_by_a_later_full_look(self):
        """The window cuts 'الصَّلَاةَ' into 'الصَّ'; a later window sees it whole."""
        mushaf = DummyMushafView()
        tracker = RecitationTracker(mushaf, self.page_map)
        tracker.set_position(8, 3, 0)

        # First window: the word is in the middle, heard as a fragment
        tracker.on_result({"text": "x", "mode": "tracking", "attempted": True,
            "match": self._match([
                self._word(8, 3, 0, "يُقِيمُونَ", "يُقِيمُونَ", True),
                self._word(8, 3, 2, "الصَّ", "الصَّلَاةَ", False),
                self._word(8, 3, 3, "وَمِمَّا", "وَمِمَّا", True),
            ])})
        self.assertEqual(mushaf.final()[(8, 3, 2)], False)

        # Later window hears the whole word
        tracker.on_result({"text": "x", "mode": "tracking", "attempted": True,
            "match": self._match([
                self._word(8, 3, 0, "يُقِيمُونَ", "يُقِيمُونَ", True),
                self._word(8, 3, 2, "الصَّلَاةَ", "الصَّلَاةَ", True),
                self._word(8, 3, 3, "وَمِمَّا", "وَمِمَّا", True),
            ])})
        self.assertEqual(mushaf.final()[(8, 3, 2)], True)

    def test_correct_is_never_downgraded_by_a_fragment(self):
        """Evidence only improves a verdict; it must not spoil a good one."""
        mushaf = DummyMushafView()
        tracker = RecitationTracker(mushaf, self.page_map)
        tracker.set_position(8, 3, 0)

        good = [self._word(8, 3, 0, "يُقِيمُونَ", "يُقِيمُونَ", True),
                self._word(8, 3, 2, "الصَّلَاةَ", "الصَّلَاةَ", True),
                self._word(8, 3, 3, "وَمِمَّا", "وَمِمَّا", True)]
        tracker.on_result({"text": "x", "mode": "tracking", "attempted": True,
                           "match": self._match(good)})
        self.assertEqual(mushaf.final()[(8, 3, 2)], True)

        bad = [self._word(8, 3, 0, "يُقِيمُونَ", "يُقِيمُونَ", True),
               self._word(8, 3, 2, "الصَّ", "الصَّلَاةَ", False),
               self._word(8, 3, 3, "وَمِمَّا", "وَمِمَّا", True)]
        tracker.on_result({"text": "x", "mode": "tracking", "attempted": True,
                           "match": self._match(bad)})
        self.assertEqual(mushaf.final()[(8, 3, 2)], True)

    def test_wrong_word_at_window_edge_is_withheld(self):
        """First/last word of a window is the one the boundary cut."""
        mushaf = DummyMushafView()
        tracker = RecitationTracker(mushaf, self.page_map)
        tracker.set_position(8, 3, 0)

        tracker.on_result({"text": "x", "mode": "tracking", "attempted": True,
            "match": self._match([
                self._word(8, 3, 0, "ُونَ", "يُقِيمُونَ", False),      # edge
                self._word(8, 3, 2, "الصَّلَاةَ", "الصَّلَاةَ", True),
                self._word(8, 3, 3, "وَمِمَّ", "وَمِمَّا", False),      # edge
            ])})
        final = mushaf.final()
        self.assertNotIn((8, 3, 0), final)   # withheld, not painted red
        self.assertNotIn((8, 3, 3), final)
        self.assertEqual(final[(8, 3, 2)], True)

    def test_finalize_commits_the_last_window(self):
        """A mistake in the final word must still be reported on stop."""
        mushaf = DummyMushafView()
        tracker = RecitationTracker(mushaf, self.page_map)
        tracker.set_position(8, 3, 0)

        tracker.on_result({"text": "x", "mode": "tracking", "attempted": True,
            "match": self._match([
                self._word(8, 3, 2, "الصَّلَاةَ", "الصَّلَاةَ", True),
                self._word(8, 3, 3, "فَمِمَّا", "وَمِمَّا", False),   # edge + wrong
            ])})
        self.assertNotIn((8, 3, 3), mushaf.final())

        tracker.finalize()
        self.assertEqual(mushaf.final()[(8, 3, 3)], False)


if __name__ == "__main__":
    unittest.main()
