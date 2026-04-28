"""Recitation tracking — state machine for processing transcription results.

Two modes:
  - Discovery: accumulates words until a unique Quran position is found
  - Tracking: locked to current position, local matching only

Manages the current recitation position (surah, ayah, word index) and
renders results with color-coded HTML feedback. Also drives the Mushaf
view highlighting and page transitions.
"""

from src.core.page_map import PageMap
from src.core.arabic import normalize
from src.ui.mushaf_view import MushafView
from src.ui.style import CORRECT_COLOR, INCORRECT_COLOR, MISSED_COLOR, VERSE_REF_COLOR


class RecitationTracker:
    """Tracks recitation progress and renders results to the UI.

    Owns the state of which surah/ayah/word the user last recited,
    handles page transitions on the Mushaf, and generates styled HTML
    for the transcription output panel.

    Mode state machine:
      - "discovery": waiting for unique verse identification
      - "tracking": locked to position, local matching only
    """

    def __init__(self, mushaf_view: MushafView, page_map: PageMap):
        self._mushaf = mushaf_view
        self._page_map = page_map

        # Tracking recitation progress
        self.last_surah: int | None = None
        self.last_ayah: int | None = None
        self.last_word_index: int = -1
        self._processed_word_ids = set()
        self._committed_html = ""
        self._current_ayah_words = []
        self._current_surah_name = ""
        self._current_ayah_id = None

        # Mode state machine
        self._mode: str = "discovery"
        self._discovery_buffer: list[str] = []  # accumulated words during discovery

    @property
    def mode(self) -> str:
        return self._mode

    def reset(self):
        self.last_surah = None
        self.last_ayah = None
        self.last_word_index = -1
        self._processed_word_ids.clear()
        self._committed_html = ""
        self._current_ayah_words = []
        self._current_surah_name = ""
        self._current_ayah_id = None
        self._mode = "discovery"
        self._discovery_buffer.clear()

    def set_position(self, surah: int, ayah: int, word_index: int = 0):
        """Manually set position (e.g. user tapped on Mushaf).

        Immediately switches to tracking mode — skips discovery.
        """
        self.last_surah = surah
        self.last_ayah = ayah
        self.last_word_index = word_index
        self._mode = "tracking"
        self._discovery_buffer.clear()

    def on_result(self, result: dict) -> str:
        """Process a transcription result and return styled HTML for display.

        Also updates the Mushaf view highlighting and handles page transitions.

        Args:
            result: Dict with 'text' (str), 'match' (VerseMatch or None),
                    and 'mode' (str).

        Returns:
            HTML string to append to the output panel.
        """
        text = result["text"]
        match = result["match"]

        # Discovery mode: accumulate words for display, wait for match
        if self._mode == "discovery":
            if match is None:
                # No match yet — just show transcribed text
                return (
                    f'<p align="right" dir="rtl" style="margin-top:0px; margin-bottom:6px; color:#888;">'
                    f'🔍 {text}'
                    f'</p>'
                )
            else:
                # Discovery succeeded — unique match found!
                self._mode = "tracking"
                self._discovery_buffer.clear()
                # Fall through to process the match

        if match is None:
            # Tracking mode but no local match — show text, don't jump
            return self._committed_html + (
                f'<p align="right" dir="rtl" style="margin-top:0px; margin-bottom:6px;">{text}</p>'
            )

        new_words = []
        # Highlight words on Mushaf View and track state
        current_page = None
        for w in match.words:
            if w.surah_id is None or w.ayah_id is None or w.reference_index is None:
                continue

            word_id = f"{w.surah_id}:{w.ayah_id}:{w.reference_index}"
            if word_id in self._processed_word_ids:
                continue

            # Load new page if recitation crosses a page boundary (only once per result)
            page_num = self._page_map.get(w.surah_id, w.ayah_id)
            if page_num and page_num != self._mushaf.current_page_num and page_num != current_page:
                self._mushaf.load_page(page_num)
                current_page = page_num

            if w.recited and w.is_correct:
                status = True
            elif w.reference:
                status = False
            else:
                continue

            self._mushaf.update_recitation(w.surah_id, w.ayah_id, w.reference_index, status)
            self._processed_word_ids.add(word_id)
            new_words.append(w)

        for w in new_words:
            if self._current_ayah_id is None:
                self._current_ayah_id = w.ayah_id
                self._current_surah_name = match.surah_name
            
            if w.ayah_id != self._current_ayah_id:
                self._flush_current_ayah()
                self._current_ayah_id = w.ayah_id
                self._current_surah_name = match.surah_name
            
            self._current_ayah_words.append(w)

        # Build current ayah HTML
        current_html = ""
        if self._current_ayah_words:
            ref_html = (
                f'<span style="color:{VERSE_REF_COLOR}; font-size:12px;">'
                f'[{self._current_surah_name} : {self._current_ayah_id}]'
                f'</span><br>'
            )
            word_spans = []
            for w in self._current_ayah_words:
                if w.recited and w.is_correct:
                    word_spans.append(f'<span style="color:{CORRECT_COLOR};">{w.recited}</span>')
                elif w.recited and not w.is_correct:
                    word_spans.append(f'<span style="color:{INCORRECT_COLOR};">{w.recited}</span>')
                elif not w.recited and w.reference:
                    word_spans.append(f'<span style="color:{MISSED_COLOR}; text-decoration:underline;">{w.reference}</span>')
            
            words_html = " ".join(word_spans)
            current_html = f'<p align="right" dir="rtl" style="margin-top:0px; margin-bottom:6px;">{ref_html}{words_html}</p>'

        return self._committed_html + current_html

    def _flush_current_ayah(self):
        if not self._current_ayah_words:
            return
        
        ref_html = (
            f'<span style="color:{VERSE_REF_COLOR}; font-size:12px;">'
            f'[{self._current_surah_name} : {self._current_ayah_id}]'
            f'</span><br>'
        )
        word_spans = []
        for w in self._current_ayah_words:
            if w.recited and w.is_correct:
                word_spans.append(f'<span style="color:{CORRECT_COLOR};">{w.recited}</span>')
            elif w.recited and not w.is_correct:
                word_spans.append(f'<span style="color:{INCORRECT_COLOR};">{w.recited}</span>')
            elif not w.recited and w.reference:
                word_spans.append(f'<span style="color:{MISSED_COLOR}; text-decoration:underline;">{w.reference}</span>')
        
        words_html = " ".join(word_spans)
        self._committed_html += f'<p align="right" dir="rtl" style="margin-top:0px; margin-bottom:6px;">{ref_html}{words_html}</p>'
        self._current_ayah_words = []


