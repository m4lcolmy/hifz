import unittest

from src.core.arabic import normalize
from src.core.quran import QuranIndex


class QuranIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = QuranIndex()

    def test_partial_recitation_does_not_mark_next_word_missed(self):
        match = self.index.find_and_compare("الْحَمْدُ لِلَّهِ")
        self.assertIsNotNone(match)
        self.assertEqual((match.surah_id, match.ayah_id, match.start_offset), (1, 2, 0))
        self.assertEqual(
            [(word.recited, word.reference, word.is_correct) for word in match.words],
            [
                ("الْحَمْدُ", "الْحَمْدُ", True),
                ("لِلَّهِ", "لِلَّهِ", True),
            ],
        )

    def test_repeated_words_keep_real_start_offset(self):
        match = self.index.find_and_compare("الرَّحْمَٰنِ الرَّحِيمِ")
        self.assertIsNotNone(match)
        self.assertEqual((match.surah_id, match.ayah_id, match.start_offset), (1, 3, 0))

    def test_normalize_keeps_real_letters(self):
        self.assertEqual(normalize("ذَلِكَ"), normalize("ذَٰلِكَ"))
        self.assertEqual(normalize("هَذَا"), normalize("هَٰذَا"))
        self.assertEqual(normalize("الْعَالَمِينَ"), "العالمين")


if __name__ == "__main__":
    unittest.main()
