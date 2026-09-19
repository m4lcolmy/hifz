"""What the page paints for each verdict — and what it deliberately does not.

Correct recitation carries no colour. The temptation when that rule is
implemented is to paint nothing at all for a correct word, which is wrong in
a way no screenshot would catch: a word first heard wrong and then heard
properly in a later window has to have its red *taken off*. Leaving it means
the one thing this app exists to show — a mistake — stays on the page after
the app has decided it was not one.

So the rule is: correct paints a transparent tint, not no tint.
"""

import unittest

from src.ui.mushaf_view import CLEAR, _verdict_tint
from src.ui.style import INCORRECT_COLOR, MISSED_COLOR, TINT_ALPHA


class VerdictTintTest(unittest.TestCase):

    def test_correct_is_not_coloured(self):
        self.assertEqual(_verdict_tint(True).alpha(), 0)

    def test_correct_clears_rather_than_skipping(self):
        """A revision from wrong to correct must remove the red."""
        self.assertEqual(_verdict_tint(True), CLEAR)

    def test_wrong_is_red(self):
        tint = _verdict_tint(False)
        self.assertEqual(tint.name(), INCORRECT_COLOR.lower())
        self.assertEqual(tint.alpha(), TINT_ALPHA)

    def test_unsure_is_amber(self):
        tint = _verdict_tint(None)
        self.assertEqual(tint.name(), MISSED_COLOR.lower())
        self.assertEqual(tint.alpha(), TINT_ALPHA)

    def test_the_two_marked_verdicts_stay_readable_through_the_tint(self):
        """A highlighter, not a fill — the glyph has to show through."""
        for status in (False, None):
            with self.subTest(status=status):
                self.assertLess(_verdict_tint(status).alpha(), 128)


if __name__ == "__main__":
    unittest.main()
