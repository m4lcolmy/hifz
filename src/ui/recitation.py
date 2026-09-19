"""Recitation tracking — state machine for processing transcription results.

Two modes:
  - Discovery: accumulates words until a unique Quran position is found
  - Tracking: locked to current position, local matching only

Manages the current recitation position (surah, ayah, word index) and
renders results with color-coded HTML feedback. Also drives the Mushaf
view highlighting and page transitions.

Scoring is evidence-based rather than first-come.  Audio windows overlap, so
the same word is transcribed several times: once at the edge of a window where
the model only hears a fragment ("الصَّ"), and again in the middle of a later
window where it hears the whole thing ("الصَّلَاةَ").  The first verdict is the
worst one, so committing to it paints correct recitation red.  Instead every
observation is ranked and the best evidence wins:

    a word seen whole beats a word seen at a window edge,
    and within that, correct beats wrong beats missed.

A word may therefore turn from red to green as better evidence arrives, but
never the other way around on weaker evidence.
"""

from src.config import TRACKING_MAX_MISSES, EDGE_CONFIRMATIONS
from src.core.debug import log
from src.core.page_map import PageMap
from src.ui.mushaf_view import MushafView
from src.ui.style import CORRECT_COLOR, INCORRECT_COLOR, MISSED_COLOR, VERSE_REF_COLOR


def _rank(word) -> int:
    """How good a verdict is. Higher wins."""
    if word.is_gap_filled or not word.recited:
        return 1          # skipped / not heard
    if word.is_correct:
        return 3          # heard and correct
    return 2              # heard and wrong


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

        # word_id -> (evidence_quality, WordResult); best evidence wins
        self._scored: dict[str, tuple[tuple[int, int], object]] = {}
        # Wrong-at-the-edge verdicts held back pending a better look. If no
        # better look ever arrives they are committed, so a mistake in the
        # last word of a recitation is still reported.
        self._withheld: dict[str, tuple[int, object, int]] = {}
        self._observations = 0   # counts absorbed matches, to date the above
        self._order: list[str] = []          # first-seen order, for rendering
        self._surah_names: dict[int, str] = {}

        # Mode state machine
        self._mode: str = "discovery"
        self._misses: int = 0  # consecutive failed matches while tracking

    @property
    def mode(self) -> str:
        return self._mode

    def reset(self):
        self.last_surah = None
        self.last_ayah = None
        self.last_word_index = -1
        self._scored.clear()
        self._withheld.clear()
        self._observations = 0
        self._order.clear()
        self._surah_names.clear()
        self._mode = "discovery"
        self._misses = 0

    def set_position(self, surah: int, ayah: int, word_index: int = 0):
        """Manually set position (e.g. user tapped on Mushaf).

        Immediately switches to tracking mode — skips discovery.
        """
        self.last_surah = surah
        self.last_ayah = ayah
        self.last_word_index = word_index
        self._mode = "tracking"
        self._misses = 0

    # ── Result handling ────────────────────────────────────────────────

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

        # Discovery mode: show what we hear until the position is pinned down
        if self._mode == "discovery":
            if match is None:
                return (
                    f'<p align="right" dir="rtl" style="margin-top:0px; margin-bottom:6px; color:#888;">'
                    f'🔍 {text}'
                    f'</p>'
                )
            # Discovery succeeded — unique match found!
            self._mode = "tracking"
            self._misses = 0
            log.count("tracker_locked")
            log.tracker(f"discovery -> tracking @ {match.surah_id}:{match.ayah_id}")
            # Fall through to process the match

        if match is None:
            # Tracking mode but no local match. If this keeps happening the
            # reciter has moved somewhere we are not following — drop back to
            # discovery rather than staying stuck on a stale pointer.
            if result.get("attempted", True):
                self._misses += 1
                log.tracker(f"miss {self._misses}/{TRACKING_MAX_MISSES} "
                            f"@ {self.last_surah}:{self.last_ayah}:{self.last_word_index}")
                if self._misses >= TRACKING_MAX_MISSES:
                    log.count("tracker_fallback")
                    log.tracker("lost position -> falling back to discovery")
                    self._mode = "discovery"
                    self._misses = 0
                    self.last_surah = None
                    self.last_ayah = None
                    self.last_word_index = -1

            return self._render() + (
                f'<p align="right" dir="rtl" style="margin-top:0px; margin-bottom:6px;">{text}</p>'
            )

        self._misses = 0
        self._absorb(match)
        return self._render()

    def _absorb(self, match):
        """Merge one match's word verdicts into the running best-evidence map."""
        self._surah_names[match.surah_id] = match.surah_name
        self._observations += 1

        edge_ids = self._edge_word_ids(match)
        current_page = None

        for w in match.words:
            if w.surah_id is None or w.ayah_id is None or w.reference_index is None:
                continue
            if not w.recited and not w.reference:
                continue

            word_id = (w.surah_id, w.ayah_id, w.reference_index)
            at_edge = word_id in edge_ids

            # A word at the window edge was probably cut in half by the
            # boundary, so the model heard a fragment ("ِينَ" for "الَّذِينَ").
            # That is enough to confirm a word but never to condemn one —
            # leave it unpainted and wait for a window that contains it whole.
            if at_edge and _rank(w) == 2:
                if word_id not in self._scored:
                    seen = self._withheld.get(word_id, (0, None, 0))[0]
                    self._withheld[word_id] = (seen + 1, w, self._observations)
                    # Different windows cut at different points, so a word
                    # that comes back wrong at the edge of several of them is
                    # wrong, not truncated. Stop withholding it.
                    if seen + 1 >= EDGE_CONFIRMATIONS:
                        self._commit(w, (0, 2))
                        log.count("edge_word_confirmed")
                if w.recited:
                    self.last_surah = w.surah_id
                    self.last_ayah = w.ayah_id
                    self.last_word_index = w.reference_index
                log.count("edge_word_withheld")
                continue

            self._withheld.pop(word_id, None)

            # Evidence quality: a word seen whole outranks one seen at the
            # edge of the audio window, where the model only heard a fragment.
            quality = (0 if at_edge else 1, _rank(w))

            previous = self._scored.get(word_id)
            if previous is not None and previous[0] >= quality:
                continue  # we already have equal or better evidence

            # Load new page if recitation crosses a page boundary
            page_num = self._page_map.get(w.surah_id, w.ayah_id)
            if page_num and page_num != self._mushaf.current_page_num and page_num != current_page:
                log.count("mushaf_pages")
                log.mushaf(f"load page {page_num} (for {w.surah_id}:{w.ayah_id})")
                self._mushaf.load_page(page_num)
                current_page = page_num

            self._commit(w, quality, previous)

            # Keep the pointer in sync — this is what lets the next chunk use
            # local tracking instead of searching the whole Quran again.
            if w.recited:
                self.last_surah = w.surah_id
                self.last_ayah = w.ayah_id
                self.last_word_index = w.reference_index

    def _commit(self, w, quality, previous=None):
        """Record a verdict and paint it on the page."""
        word_id = (w.surah_id, w.ayah_id, w.reference_index)
        if previous is None:
            previous = self._scored.get(word_id)

        if previous is None:
            self._order.append(word_id)
        elif log.enabled and _rank(previous[1]) != _rank(w):
            log.tracker(
                f"revised {w.surah_id}:{w.ayah_id}:{w.reference_index} "
                f"{previous[1].recited or '—'!r} -> {w.recited or '—'!r} "
                f"({_rank(previous[1])}->{_rank(w)})"
            )

        self._scored[word_id] = (quality, w)
        self._withheld.pop(word_id, None)
        self._mushaf.update_recitation(
            w.surah_id, w.ayah_id, w.reference_index, self._status(w)
        )

    def finalize(self) -> str:
        """Commit verdicts still waiting for a better look.

        Called when the reciter stops. Only verdicts from the very last match
        are committed: those are the words no further audio window will ever
        cover, so withholding them means a mistake in the final word is never
        shown. Anything withheld earlier did get later windows and stayed
        unconvincing, so it is left unpainted rather than guessed at.
        """
        for word_id, (_seen, w, observed_at) in list(self._withheld.items()):
            if observed_at == self._observations and word_id not in self._scored:
                self._commit(w, (0, 2))
                log.count("edge_word_committed_on_stop")
        self._withheld.clear()
        return self._render()

    @staticmethod
    def _edge_word_ids(match) -> set:
        """Word ids sitting at the start/end of the audio window.

        The first and last words the model transcribed are the ones the window
        boundary is most likely to have cut in half, so their verdict is weak
        evidence — good enough to confirm a word, not to condemn one.
        """
        recited = [
            w for w in match.words
            if w.recited and w.surah_id is not None and w.reference_index is not None
        ]
        if len(recited) < 2:
            # A single word is all edge — nothing to compare it against.
            return {
                (w.surah_id, w.ayah_id, w.reference_index) for w in recited
            }
        first, last = recited[0], recited[-1]
        return {
            (first.surah_id, first.ayah_id, first.reference_index),
            (last.surah_id, last.ayah_id, last.reference_index),
        }

    @staticmethod
    def _status(word) -> bool | None:
        """Mushaf colour for a verdict: True green, False red, None amber."""
        if word.is_gap_filled or not word.recited:
            return None
        return bool(word.is_correct)

    # ── Rendering ──────────────────────────────────────────────────────

    def _render(self) -> str:
        """Build the whole output panel from the current best evidence."""
        if not self._order:
            return ""

        blocks = []
        current_key = None
        spans: list[str] = []

        for word_id in self._order:
            entry = self._scored.get(word_id)
            if entry is None:
                continue
            w = entry[1]
            key = (w.surah_id, w.ayah_id)
            if key != current_key:
                if spans:
                    blocks.append(self._paragraph(current_key, spans))
                current_key = key
                spans = []
            spans.append(self._span(w))

        if spans:
            blocks.append(self._paragraph(current_key, spans))
        return "".join(blocks)

    def _paragraph(self, key, spans: list[str]) -> str:
        surah_id, ayah_id = key
        name = self._surah_names.get(surah_id, str(surah_id))
        ref_html = (
            f'<span style="color:{VERSE_REF_COLOR}; font-size:12px;">'
            f'[{name} : {ayah_id}]'
            f'</span><br>'
        )
        return (
            f'<p align="right" dir="rtl" style="margin-top:0px; margin-bottom:6px;">'
            f'{ref_html}{" ".join(spans)}</p>'
        )

    @staticmethod
    def _span(w) -> str:
        if w.recited and w.is_correct:
            return f'<span style="color:{CORRECT_COLOR};">{w.recited}</span>'
        if w.recited and not w.is_correct:
            return f'<span style="color:{INCORRECT_COLOR};">{w.recited}</span>'
        return (
            f'<span style="color:{MISSED_COLOR}; text-decoration:underline;">'
            f'{w.reference}</span>'
        )
