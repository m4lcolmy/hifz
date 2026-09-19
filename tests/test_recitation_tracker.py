import unittest

from src.config import (
    TRACKING_MAX_MISSES, TRACKING_MAX_MISS_SECONDS, EDGE_CONFIRMATIONS,
    PRELOCK_BUFFER_SECONDS,
)
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
                self._word(8, 3, 0, "فُقِيمُونَ", "يُقِيمُونَ", False),   # edge
                self._word(8, 3, 2, "الصَّلَاةَ", "الصَّلَاةَ", True),
                self._word(8, 3, 3, "فَمِمَّا", "وَمِمَّا", False),        # edge
            ])})
        final = mushaf.final()
        self.assertNotIn((8, 3, 0), final)   # withheld, not painted red
        self.assertNotIn((8, 3, 3), final)
        self.assertEqual(final[(8, 3, 2)], True)

    def test_a_clipped_word_at_the_edge_is_credited_not_condemned(self):
        """"ُونَ" is not a different word from "يُقِيمُونَ", it is part of it.

        A window boundary landing mid-word is the one thing that makes the
        model return a piece of a word, and the piece is of the *right* word.
        Withholding it instead only defers the problem: several windows clip
        the same word the same way, EDGE_CONFIRMATIONS counts that as
        agreement, and the word goes red anyway.
        """
        mushaf = DummyMushafView()
        tracker = RecitationTracker(mushaf, self.page_map)
        tracker.set_position(8, 3, 0)

        for _ in range(EDGE_CONFIRMATIONS + 1):
            tracker.on_result({"text": "x", "mode": "tracking", "attempted": True,
                "match": self._match([
                    self._word(8, 3, 0, "ُونَ", "يُقِيمُونَ", False),      # edge
                    self._word(8, 3, 2, "الصَّلَاةَ", "الصَّلَاةَ", True),
                    self._word(8, 3, 3, "وَمِمَّ", "وَمِمَّا", False),      # edge
                ])})

        final = mushaf.final()
        self.assertIs(final[(8, 3, 0)], True)
        self.assertIs(final[(8, 3, 3)], True)

    def test_a_whole_word_look_overrides_a_clipped_one(self):
        """The credit is a placeholder, not a verdict — it must give way.

        Otherwise crediting a fragment would make a word permanently green and
        hide a real mistake in it.
        """
        mushaf = DummyMushafView()
        tracker = RecitationTracker(mushaf, self.page_map)
        tracker.set_position(8, 3, 0)

        # Clipped at the edge, then heard whole and wrong in the middle.
        tracker.on_result({"text": "x", "mode": "tracking", "attempted": True,
            "match": self._match([
                self._word(8, 3, 3, "وَمِمَّ", "وَمِمَّا", False),        # edge
                self._word(8, 3, 4, "رَزَقْنَاهُمْ", "رَزَقْنَاهُمْ", True),
            ])})
        self.assertIs(mushaf.final()[(8, 3, 3)], True)

        tracker.on_result({"text": "x", "mode": "tracking", "attempted": True,
            "match": self._match([
                self._word(8, 3, 2, "الصَّلَاةَ", "الصَّلَاةَ", True),
                self._word(8, 3, 3, "فَمِمَّا", "وَمِمَّا", False),        # heard whole
                self._word(8, 3, 4, "رَزَقْنَاهُمْ", "رَزَقْنَاهُمْ", True),
            ])})
        self.assertIs(
            mushaf.final()[(8, 3, 3)], False,
            "a guess about clipped audio outranked a word heard whole",
        )

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


class OneWordAyahTests(unittest.TestCase):
    """An ayah can be a single word, and discovery demanded two.

    So الٓمٓ, يس, وَالْعَصْرِ could never be found — only stepped over on the
    way to the next ayah. Reciting Al-Baqarah from the top, الم was
    transcribed cleanly twice and skipped both times.
    """

    @classmethod
    def setUpClass(cls):
        cls.index = QuranIndex()

    def test_a_unique_one_word_ayah_is_found_from_that_one_word(self):
        for word, expected in (("يس", (36, 1)),
                               ("طه", (20, 1)),
                               ("كهيعص", (19, 1)),
                               ("والعصر", (103, 1))):
            with self.subTest(word=word):
                match = self.index.discover(word)
                self.assertIsNotNone(match, f"{word} was not found")
                self.assertEqual((match.surah_id, match.ayah_id), expected)

    def test_an_ambiguous_one_word_ayah_is_still_refused(self):
        """الم opens six surahs, and normalization merges it with أَلَمْ تَرَ.

        Hearing it genuinely does not say where the reciter is, so refusing is
        correct — the pre-lock re-match is what eventually scores it.
        """
        for word in ("الم", "حم", "طسم"):
            with self.subTest(word=word):
                self.assertIsNone(self.index.discover(word))

    def test_one_word_is_not_a_licence_to_match_anything(self):
        """Only a word that is an entire ayah, and unique, may lock on one word."""
        for word in ("الله", "قل", "الرحمن", "الذين"):
            with self.subTest(word=word):
                self.assertIsNone(self.index.discover(word))


class BeforeTheLockTests(unittest.TestCase):
    """Discovery refuses ambiguous openings — and every session opens with one.

    بسم الله الرحمن الرحيم matches 114 places, so it is transcribed, rejected
    and thrown away. The words were recited and understood; only the position
    was unknown. Once it is known they can be placed after all.
    """

    @classmethod
    def setUpClass(cls):
        cls.index = QuranIndex()
        cls.page_map = PageMap()

    def _tracker(self, mushaf):
        return RecitationTracker(mushaf, self.page_map, self.index,
                                 clock=FakeClock())

    def _recite(self, tracker, *chunks):
        for text in chunks:
            ctx = (tracker.last_surah, tracker.last_ayah, tracker.last_word_index)
            match = (self.index.track(text, *ctx) if tracker.mode == "tracking"
                     else self.index.discover(text))
            tracker.on_result({"text": text, "match": match,
                               "mode": tracker.mode, "attempted": True})

    def test_the_basmala_is_scored_once_al_fatiha_is_identified(self):
        """Al-Fatiha locked on الرحمن الرحيم and lost بسم الله every time.

        The transcriptions are verbatim from logs/hifz-20260919-125436.log,
        clipped first word and all: capture starts after the reciter does, so
        the opening word of a session is the one most likely to be cut.
        """
        mushaf = DummyMushafView()
        tracker = self._tracker(mushaf)

        self._recite(tracker,
                     "سْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ",   # 114 places — refused
                     "رَحْمَنِ الرَّحِيمِ الْحَمْدُ")          # locks, at 1:1:2
        tracker.finalize()

        final = mushaf.final()
        self.assertEqual((tracker.last_surah, tracker.last_ayah), (1, 2))
        for i in range(4):
            self.assertIn((1, 1, i), final,
                          f"1:1:{i} was recited but never shown")
        self.assertTrue(all(final[(1, 1, i)] for i in range(4)),
                        "the basmala was recited correctly, so it is green")

    def test_alif_lam_mim_is_scored_once_al_baqarah_is_identified(self):
        """الم is ambiguous alone, but not once ذلك الكتاب says which surah."""
        mushaf = DummyMushafView()
        tracker = self._tracker(mushaf)

        self._recite(tracker, "الم", "ذلك الكتاب لا ريب فيه")

        final = mushaf.final()
        self.assertEqual(tracker.last_surah, 2)
        self.assertIs(final.get((2, 1, 0)), True,
                      "الم was recited, transcribed cleanly, and never shown")

    def test_the_lock_word_itself_can_be_recovered(self):
        """The lock word is the one most likely to need this.

        Discovery locks on the first phrase it can tell apart, and that
        phrase's own opening word sits at the window edge where it was
        clipped. 4:1:0 يَاأَيُّهَا was heard as وَيُّهَا exactly once, withheld,
        and never scored — while a chunk from a second earlier had it
        perfectly and had been refused only because three surahs open
        يَاأَيُّهَا النَّاسُ اتَّقُوا رَبَّكُمْ.

        Transcriptions verbatim from logs/hifz-20260919-131925.log.
        """
        mushaf = DummyMushafView()
        tracker = self._tracker(mushaf)

        self._recite(tracker,
                     "يَا أَيُّهَا النَّاسُ اتَّقُوا رَبِّكُمْ",        # 4:1, 22:1, 31:33
                     "وَيُّهَا النَّاسُ اتَّقُوا رَبِّكُمُ الَّذِي")    # locks at 4:1:0
        tracker.finalize()

        self.assertEqual((tracker.last_surah, tracker.last_ayah), (4, 1))
        self.assertIs(
            mushaf.final().get((4, 1, 0)), True,
            "the word discovery locked on was never given a verdict",
        )

    def test_the_re_match_does_not_reach_into_the_surah_before(self):
        """Al-Fatiha sits immediately before Al-Baqarah in the flat text.

        Looking backwards from 2:2 reaches it within a few words, so the
        window has to stop at the surah boundary. Someone who opens with
        Al-Baqarah did not recite the end of Al-Fatiha first.
        """
        self.assertIsNone(
            self.index.rematch_near("غير المغضوب عليهم ولا الضالين", 2, 2, 0),
            "a re-match anchored in Al-Baqarah claimed words in Al-Fatiha",
        )

    def test_noise_before_the_lock_is_not_turned_into_verdicts(self):
        """Breath and throat-clearing must not become words on the page."""
        mushaf = DummyMushafView()
        tracker = self._tracker(mushaf)

        self._recite(tracker, "اه اه", "همم", "ذلك الكتاب لا ريب فيه")

        self.assertEqual(tracker.last_surah, 2)
        self.assertFalse(
            [k for k in mushaf.final() if k < (2, 2, 0)],
            "noise recited before the lock was scored as recitation",
        )

    def test_the_buffer_does_not_outlive_its_window(self):
        """A phrase from minutes ago must not be placed against a later lock."""
        mushaf = DummyMushafView()
        clock = FakeClock()
        tracker = RecitationTracker(mushaf, self.page_map, self.index,
                                    clock=clock)

        tracker.on_result({"text": "بسم الله الرحمن الرحيم", "match": None,
                           "mode": "discovery", "attempted": True})
        clock.advance(PRELOCK_BUFFER_SECONDS + 1)

        text = "الرحمن الرحيم الحمد لله"
        tracker.on_result({"text": text, "match": self.index.discover(text),
                           "mode": "discovery", "attempted": True})

        self.assertNotIn((1, 1, 0), mushaf.final(),
                         "a stale transcription was placed against a new lock")


if __name__ == "__main__":
    unittest.main()
