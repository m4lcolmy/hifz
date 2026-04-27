"""Recitation tracking — state machine for processing transcription results.

Manages the current recitation position (surah, ayah, word index) and
renders results with color-coded HTML feedback. Also drives the Mushaf
view highlighting and page transitions.
"""

from src.core.page_map import PageMap
from src.ui.mushaf_view import MushafView
from src.ui.style import CORRECT_COLOR, INCORRECT_COLOR, MISSED_COLOR, VERSE_REF_COLOR


class RecitationTracker:
    """Tracks recitation progress and renders results to the UI.

    Owns the state of which surah/ayah/word the user last recited,
    handles page transitions on the Mushaf, and generates styled HTML
    for the transcription output panel.
    """

    def __init__(self, mushaf_view: MushafView, page_map: PageMap):
        self._mushaf = mushaf_view
        self._page_map = page_map

        # Tracking recitation progress
        self.last_surah: int | None = None
        self.last_ayah: int | None = None
        self.last_word_index: int = -1

    def on_result(self, result: dict) -> str:
        """Process a transcription result and return styled HTML for display.

        Also updates the Mushaf view highlighting and handles page transitions.

        Args:
            result: Dict with 'text' (str) and 'match' (VerseMatch or None).

        Returns:
            HTML string to append to the output panel.
        """
        text = result["text"]
        match = result["match"]
        prev_surah = self.last_surah
        prev_ayah = self.last_ayah
        prev_word_index = self.last_word_index

        if match is None:
            # No Quran match — show plain text
            return f'<div dir="rtl" style="text-align:right;">{text}</div>'

        # Build HTML for the verse reference
        ref_html = (
            f'<div dir="rtl" style="text-align:right; margin-bottom:4px;">'
            f'<span style="color:{VERSE_REF_COLOR}; font-size:12px;">'
            f'[{match.surah_name} : {match.ayah_id}]'
            f'</span></div>'
        )

        # Build HTML for colored words
        word_spans = []
        for w in match.words:
            if w.recited and w.is_correct:
                word_spans.append(
                    f'<span style="color:{CORRECT_COLOR};">{w.recited}</span>'
                )
            elif w.recited and not w.is_correct:
                word_spans.append(
                    f'<span style="color:{INCORRECT_COLOR};">{w.recited}</span>'
                )
            elif not w.recited and w.reference:
                word_spans.append(
                    f'<span style="color:{MISSED_COLOR}; '
                    f'text-decoration:underline;">{w.reference}</span>'
                )

        words_html = (
            f'<div dir="rtl" style="text-align:right; line-height:2;">'
            + " ".join(word_spans)
            + "</div>"
        )

        html = ref_html + words_html

        # ── Mushaf view updates ───────────────────────────────────────

        # Determine the first valid word to handle gaps
        first_valid_word = next((w for w in match.words if w.surah_id is not None), None)

        # Load Mushaf page if needed
        if first_valid_word:
            page_num = self._page_map.get(first_valid_word.surah_id, first_valid_word.ayah_id)
            if page_num and page_num != self._mushaf.current_page_num:
                self._mushaf.load_page(page_num)

            # Detect and highlight skipped words from previous chunk within the same ayah
            if prev_surah == first_valid_word.surah_id and prev_ayah == first_valid_word.ayah_id:
                expected_next = prev_word_index + 1
                if first_valid_word.reference_index > expected_next:
                    for missed_idx in range(expected_next, first_valid_word.reference_index):
                        self._mushaf.update_recitation(
                            first_valid_word.surah_id, first_valid_word.ayah_id,
                            missed_idx, False
                        )

        # Highlight words on Mushaf View and track state
        for w in match.words:
            if w.surah_id is None or w.ayah_id is None or w.reference_index is None:
                continue

            # Load new page if recitation crosses a page boundary
            page_num = self._page_map.get(w.surah_id, w.ayah_id)
            if page_num and page_num != self._mushaf.current_page_num:
                self._mushaf.load_page(page_num)

            if w.recited and w.is_correct:
                status = True
            elif w.reference:
                status = False
            else:
                continue

            self._mushaf.update_recitation(w.surah_id, w.ayah_id, w.reference_index, status)

            # Keep tracking state
            self.last_surah = w.surah_id
            self.last_ayah = w.ayah_id
            self.last_word_index = w.reference_index

        return html
