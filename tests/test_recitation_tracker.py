import unittest

from src.config import TRACKING_MAX_MISSES, TRACKING_MAX_MISS_SECONDS
from src.core.quran import QuranIndex, VerseMatch, WordResult
from src.core.page_map import PageMap
from src.ui.recitation import RecitationTracker


class FakeClock:
    """Controllable clock, so timing rules are tested without sleeping."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def advance(self, seconds: float):
        self.now += seconds

    def __call__(self) -> float:
        return self.now


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

    def test_sustained_failure_falls_back_to_discovery(self):
        mushaf = DummyMushafView()
        clock = FakeClock()
        tracker = RecitationTracker(mushaf, self.page_map, clock=clock)

        text = "قل هو الله احد"
        tracker.on_result({"text": text, "match": self.index.discover(text), "mode": "discovery"})
        self.assertEqual(tracker.mode, "tracking")

        for _ in range(TRACKING_MAX_MISSES):
            clock.advance(TRACKING_MAX_MISS_SECONDS)
            tracker.on_result(
                {"text": "كلام غير معروف", "match": None, "mode": "tracking", "attempted": True}
            )

        self.assertEqual(tracker.mode, "discovery")
        self.assertIsNone(tracker.last_surah)

    def test_a_breath_between_ayahs_does_not_lose_the_position(self):
        """Pausing between ayahs is correct recitation.

        During the pause the model emits breath fragments that cannot match.
        Three of those arrive in under a second — far shorter than a real
        breath — and a count-only rule threw the position away every time.
        """
        mushaf = DummyMushafView()
        clock = FakeClock()
        tracker = RecitationTracker(mushaf, self.page_map, clock=clock)

        text = "قل هو الله احد"
        tracker.on_result({"text": text, "match": self.index.discover(text), "mode": "discovery"})

        # Breath fragments, one per audio window, for well under a breath
        for fragment in ("ِيَاكَ", "يَّاكَنَا", "مِنْ دِينٍ", "اه"):
            clock.advance(0.3)
            tracker.on_result(
                {"text": fragment, "match": None, "mode": "tracking", "attempted": True}
            )

        self.assertEqual(tracker.mode, "tracking")
        self.assertEqual(tracker.last_surah, 112)

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


class AlFatihaSessionTests(unittest.TestCase):
    """Regressions from a real recitation of Al-Fatiha.

    logs/hifz-20260919-122520.log: ayahs 1:1, 1:2, 1:3 and 1:6 never appeared
    on the page at all, only 16 of 29 words were ever given a verdict, and the
    position was lost three times in 56 seconds. Every unit test and all 11
    benchmark recordings passed on that same code — the recordings are short,
    clean and forward-only, so none of this was reachable from them.
    """

    @classmethod
    def setUpClass(cls):
        cls.index = QuranIndex()
        cls.page_map = PageMap()

    def _tracker(self, mushaf=None, clock=None):
        return RecitationTracker(
            mushaf or DummyMushafView(), self.page_map, self.index,
            clock=clock or FakeClock(),
        )

    def test_pointer_advances_over_already_scored_words(self):
        """Overlapping windows re-hear scored words; the pointer must move.

        It used to be updated after several `continue`s, so re-hearing a word
        already scored left it behind. track() then searched from a position
        the reciter had left seconds ago, failed, and the position was lost.
        """
        tracker = self._tracker()
        tracker.set_position(1, 4, 0)

        # Two overlapping windows, the second reaching further into the ayah
        for text in ("مالك يوم", "مالك يوم الدين"):
            match = self.index.track(
                text, tracker.last_surah, tracker.last_ayah, tracker.last_word_index
            )
            self.assertIsNotNone(match, f"no match for {text!r}")
            tracker.on_result({"text": text, "match": match,
                               "mode": "tracking", "attempted": True})

        self.assertEqual((tracker.last_surah, tracker.last_ayah), (1, 4))
        self.assertGreater(
            tracker.last_word_index, 0,
            "pointer stuck at the first word — track() will search from a "
            "position the reciter has already left",
        )

    def test_a_gap_fill_cannot_erase_a_confirmed_word(self):
        """1:7:8 الضَّالِّينَ was recited correctly, then erased.

        The next chunk matched the opening of Al-Baqarah and gap-fill marked
        the word as skipped, turning it from green to amber.
        """
        mushaf = DummyMushafView()
        tracker = self._tracker(mushaf)
        tracker.set_position(1, 7, 0)

        word = WordResult(recited="الضَّالِّينَ", reference="الضَّالِّينَ",
                          is_correct=True, surah_id=1, ayah_id=7,
                          reference_index=8)
        filler = WordResult(recited="", reference="الضَّالِّينَ", is_correct=False,
                            surah_id=1, ayah_id=7, reference_index=8,
                            is_gap_filled=True)
        padding = WordResult(recited="عَلَيْهِمْ", reference="عَلَيْهِمْ",
                             is_correct=True, surah_id=1, ayah_id=7,
                             reference_index=6)

        for words in ([padding, word], [padding, filler]):
            tracker.on_result({
                "text": "x", "mode": "tracking", "attempted": True,
                "match": VerseMatch(surah_id=1, surah_name="Al-Fatiha",
                                    ayah_id=7, start_offset=0, words=words),
            })

        self.assertIs(
            mushaf.final()[(1, 7, 8)], True,
            "a word with no evidence overwrote one that was actually heard",
        )

    def test_a_later_mishearing_cannot_erase_a_confirmed_word(self):
        """1:7:5 الْمَغْضُوبِ was confirmed correct, then downgraded."""
        mushaf = DummyMushafView()
        tracker = self._tracker(mushaf)
        tracker.set_position(1, 7, 0)

        padding = WordResult(recited="عَلَيْهِمْ", reference="عَلَيْهِمْ",
                             is_correct=True, surah_id=1, ayah_id=7,
                             reference_index=3)
        good = WordResult(recited="الْمَغْضُوبِ", reference="الْمَغْضُوبِ",
                          is_correct=True, surah_id=1, ayah_id=7,
                          reference_index=5)
        misheard = WordResult(recited="الْمَغْرُوبِ", reference="الْمَغْضُوبِ",
                              is_correct=False, surah_id=1, ayah_id=7,
                              reference_index=5)
        tail = WordResult(recited="عَلَيْهِمْ", reference="عَلَيْهِمْ",
                          is_correct=True, surah_id=1, ayah_id=7,
                          reference_index=6)

        for words in ([padding, good, tail], [padding, misheard, tail]):
            tracker.on_result({
                "text": "x", "mode": "tracking", "attempted": True,
                "match": VerseMatch(surah_id=1, surah_name="Al-Fatiha",
                                    ayah_id=7, start_offset=0, words=words),
            })

        self.assertIs(mushaf.final()[(1, 7, 5)], True)

    def test_ayah_recited_while_position_was_lost_is_still_shown(self):
        """1:6 اهدنا الصراط المستقيم was recited and never appeared.

        Tracking died at 1:5, discovery re-locked at 1:7, and the ayah in
        between was simply dropped: gap-fill only ran inside track().
        """
        mushaf = DummyMushafView()
        clock = FakeClock()
        tracker = self._tracker(mushaf, clock)

        first = "اياك نعبد واياك نستعين"
        tracker.on_result({"text": first, "match": self.index.discover(first),
                           "mode": "discovery", "attempted": True})
        self.assertEqual((tracker.last_surah, tracker.last_ayah), (1, 5))

        # Lose the position the way a long silence does
        for _ in range(TRACKING_MAX_MISSES):
            clock.advance(TRACKING_MAX_MISS_SECONDS)
            tracker.on_result({"text": "اه", "match": None,
                               "mode": "tracking", "attempted": True})
        self.assertEqual(tracker.mode, "discovery")

        # Re-lock further along — 1:6 was recited in between
        resume = "صراط الذين انعمت عليهم"
        tracker.on_result({"text": resume, "match": self.index.discover(resume),
                           "mode": "discovery", "attempted": True})

        final = mushaf.final()
        missing = [i for i in range(3) if (1, 6, i) not in final]
        self.assertEqual(
            missing, [],
            "1:6 was recited but never shown on the page",
        )
        self.assertTrue(all(final[(1, 6, i)] is None for i in range(3)),
                        "skipped words should be amber, not green or red")


if __name__ == "__main__":
    unittest.main()
