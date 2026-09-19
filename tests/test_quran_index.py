import unittest

from src.core.arabic import normalize, split_words
from src.core.quran import QuranIndex


class QuranIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = QuranIndex()

    # ── Discovery ──────────────────────────────────────────────────────

    def test_discovery_finds_short_unique_ayah(self):
        """Three-word ayahs must be discoverable — they used to be rejected."""
        match = self.index.discover("مالك يوم الدين")
        self.assertIsNotNone(match)
        self.assertEqual((match.surah_id, match.ayah_id), (1, 4))

    def test_discovery_finds_two_word_unique_ayah(self):
        match = self.index.discover("الله الصمد")
        self.assertIsNotNone(match)
        self.assertEqual((match.surah_id, match.ayah_id), (112, 2))

    def test_discovery_rejects_ambiguous_phrase(self):
        """A phrase occurring more than once must not pin a position."""
        self.assertIsNone(self.index.discover("بسم الله الرحمن الرحيم"))
        self.assertIsNone(self.index.discover("قال الله"))

    # ── Tracking ───────────────────────────────────────────────────────

    def test_partial_recitation_does_not_mark_next_word_missed(self):
        """Stopping mid-ayah must not score the words not yet recited."""
        # Context: end of 1:1 (bismillah, 4 words) → next expected word is 1:2:0
        match = self.index.track("الْحَمْدُ لِلَّهِ", 1, 1, 3)
        self.assertIsNotNone(match)
        self.assertEqual((match.surah_id, match.ayah_id, match.start_offset), (1, 2, 0))
        # Exactly the two recited words — the rest of the ayah is not scored
        self.assertEqual(len(match.words), 2)
        self.assertTrue(all(w.is_correct for w in match.words))
        self.assertTrue(all(w.recited and w.reference for w in match.words))

    def test_repeated_words_keep_real_start_offset(self):
        """'الرحمن الرحيم' repeats all over the Quran; context must decide."""
        match = self.index.track("الرَّحْمَٰنِ الرَّحِيمِ", 1, 2, 3)
        self.assertIsNotNone(match)
        self.assertEqual((match.surah_id, match.ayah_id, match.start_offset), (1, 3, 0))

    def test_skipped_words_are_reported_as_gap_filled(self):
        """Jumping forward must keep tracking and flag the skipped words."""
        # Context at start of 1:2, reciter jumps to 1:5
        match = self.index.track("اياك نعبد واياك نستعين", 1, 2, 0)
        self.assertIsNotNone(match)
        self.assertEqual((match.surah_id, match.ayah_id), (1, 5))
        gaps = [w for w in match.words if w.is_gap_filled]
        self.assertGreaterEqual(len(gaps), 3)
        # Gap words are reference-only — nothing was recited for them
        self.assertTrue(all(w.reference and not w.recited for w in gaps))

    # ── Word alignment ─────────────────────────────────────────────────

    def test_split_words_drops_waqf_marks(self):
        """Standalone pause marks are not words on the Mushaf page."""
        words = split_words("ذَٰلِكَ الْكِتَابُ لَا رَيْبَ ۛ فِيهِ ۛ هُدًى")
        self.assertEqual(len(words), 6)

    def test_split_words_joins_vocative(self):
        """يَا is written joined in Uthmani script, so it is one word."""
        words = split_words("يَا أَيُّهَا النَّاسُ")
        self.assertEqual(len(words), 2)

    def test_word_index_matches_qcf_positions(self):
        """Index word_idx + 1 must equal the QCF word position it highlights."""
        # 2:21 — "يَا أَيُّهَا النَّاسُ اعْبُدُوا ..." has 11 words on the page
        count = sum(1 for key in self.index._pos_map if key[0] == 2 and key[1] == 21)
        self.assertEqual(count, 11)

    # ── Normalization ──────────────────────────────────────────────────

    def test_normalize_keeps_real_letters(self):
        self.assertEqual(normalize("ذَلِكَ"), normalize("ذَٰلِكَ"))
        self.assertEqual(normalize("هَذَا"), normalize("هَٰذَا"))
        self.assertEqual(normalize("الْعَالَمِينَ"), "العالمين")


class OrthographyTests(unittest.TestCase):
    """Spelling differences that are not recitation mistakes."""

    @staticmethod
    def same(a: str, b: str) -> bool:
        return QuranIndex._to_imlai(a) == QuranIndex._to_imlai(b)

    def test_hamza_seat_is_not_a_mistake(self):
        """Whisper places the hamza seat unreliably."""
        self.assertTrue(self.same("إُولَئِكَ", "أُولَٰئِكَ"))

    def test_doubled_alef_is_not_a_mistake(self):
        """أَأَنذَرْتَهُمْ and أَنذَرْتَهُمْ are the same word."""
        self.assertTrue(self.same("أَنْذَرْتَهُمْ", "أَأَنذَرْتَهُمْ"))
        # The alignment layer must agree, or the two never get paired up
        self.assertEqual(normalize("أَنْذَرْتَهُمْ"), normalize("أَأَنذَرْتَهُمْ"))

    def test_final_vowel_is_waqf_not_a_mistake(self):
        """Stopping on a word drops its case ending — correct tajweed."""
        self.assertTrue(self.same("يَوْمِ", "يَوْمَ"))
        self.assertTrue(self.same("عَلَيْهِمَ", "عَلَيْهِمْ"))

    def test_shadda_does_not_block_the_sequence_rules(self):
        """78:35 is spelled لَّا — lam, fathah, SHADDA, alef.

        The shadda sits between the fathah and the alef, so a rule looking
        for "fathah followed by alef" never fired while it was still there.
        Shadda is discarded either way, so it has to go first. 28% of Quran
        words carry one, so the ordering matters everywhere.
        """
        self.assertTrue(self.same("لَا", "لَّا"))

    def test_every_shadda_word_matches_its_plain_spelling(self):
        """Whisper usually omits the shadda; that must never be a mistake."""
        index = QuranIndex()
        shadda = "\u0651"
        bad = [
            w for _, w, *_ in index._flat
            if shadda in w
            and QuranIndex._to_imlai(w.replace(shadda, "")) != QuranIndex._to_imlai(w)
        ]
        self.assertEqual(bad, [], f"{len(bad)} words fail without their shadda")

    def test_real_substitutions_are_still_mistakes(self):
        """The tolerances above must not swallow an actual wrong word."""
        self.assertFalse(self.same("فَلَهُمْ", "وَلَهُمْ"))    # و → ف
        self.assertFalse(self.same("أَظِيمٍ", "عَظِيمٍ"))      # ع → أ
        self.assertFalse(self.same("غِشَابَةٌ", "غِشَاوَةٌ"))   # و → ب
        self.assertFalse(self.same("أَلِيمٌ", "عَظِيمٌ"))


class TrackingAnchorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = QuranIndex()

    def test_anchors_on_longest_run_not_first_word(self):
        """83:14 and 83:18 both open with كَلَّا.

        Anchoring on the first matching word let that one shared word drag
        the whole alignment onto the wrong ayah, so every following word was
        compared against the wrong reference and painted red.
        """
        match = self.index.track("كلا ان كتاب الابرار لفي عليين", 83, 15, 0)
        self.assertIsNotNone(match)
        self.assertEqual((match.surah_id, match.ayah_id), (83, 18))

    def test_out_of_reach_match_is_refused_not_guessed(self):
        """When the real ayah is beyond the window, refuse rather than
        anchor on a shared opening word and mis-align everything after it."""
        self.assertIsNone(
            self.index.track("كلا ان كتاب الابرار لفي عليين", 83, 14, 0)
        )


class MatchForModeTests(unittest.TestCase):
    """The seam between the dispatcher and the index.

    Every other test in this file calls `discover()` or `track()` directly.
    That is why a whole search path could sit unreachable for months: the
    dispatcher refused the chunk before the index ever saw it, and nothing
    tested the two together. The live app and the benchmark both go through
    `match_for_mode`, so this is the only place that covers what they do.
    """

    @classmethod
    def setUpClass(cls):
        cls.index = QuranIndex()

    def _discover(self, text: str):
        from src.core.matching import match_for_mode
        return match_for_mode(self.index, text, "discovery", (None, None, None))

    def test_a_one_word_ayah_reaches_the_index(self):
        """Tier G found 23 of 30 muqatta'at cases scoring LOST with zero
        words shown, while Whisper was transcribing يس and طه perfectly. The
        two-word discovery floor was refusing them before `discover()` ran,
        so DISCOVERY_SOLO_AYAH was a setting connected to nothing — and the
        logs had been saying "SKIPPED (only 1 word(s), need 2)" 111 times."""
        for word, expected in (("يس", (36, 1)), ("طه", (20, 1)),
                               ("المص", (7, 1)), ("كهيعص", (19, 1)),
                               ("عسق", (42, 2)), ("والعصر", (103, 1))):
            with self.subTest(word=word):
                decision = self._discover(word)
                self.assertTrue(decision.attempted,
                                "refused before the index was consulted")
                self.assertIsNotNone(decision.match)
                self.assertEqual(
                    (decision.match.surah_id, decision.match.ayah_id), expected)

    def test_an_ambiguous_muqattaat_is_still_refused(self):
        """الم opens six surahs and normalization merges it with the أَلَمْ of
        أَلَمْ تَرَ, so hearing it really does not say where you are. The floor
        dropping for solo ayahs must not turn into the floor dropping."""
        for word in ("الم", "حم", "طسم", "الر"):
            with self.subTest(word=word):
                self.assertIsNone(self._discover(word).match)

    def test_an_ordinary_single_word_is_still_refused(self):
        """قل opens 173 ayahs. One ordinary word places nothing, and letting
        it through is how a session jumps to the wrong surah."""
        for word in ("قل", "الله", "ان"):
            with self.subTest(word=word):
                decision = self._discover(word)
                self.assertFalse(decision.attempted)
                self.assertIsNone(decision.match)

    def test_two_words_still_go_through_the_normal_path(self):
        decision = self._discover("الله الصمد")
        self.assertTrue(decision.attempted)
        self.assertEqual(
            (decision.match.surah_id, decision.match.ayah_id), (112, 2))

    def test_the_index_agrees_with_itself_about_solo_ayahs(self):
        """`is_solo_ayah` gates the dispatcher and `discover` acts on it. If
        they disagreed, a word would be let through and then dropped."""
        for word in ("يس", "طه", "والعصر", "مدهامتان"):
            with self.subTest(word=word):
                self.assertTrue(self.index.is_solo_ayah(word))
                self.assertIsNotNone(self.index.discover(word))
        for word in ("الم", "قل", "حم"):
            with self.subTest(word=word):
                self.assertFalse(self.index.is_solo_ayah(word))


if __name__ == "__main__":
    unittest.main()
