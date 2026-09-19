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

import time

from src.config import (
    TRACKING_MAX_MISSES, TRACKING_MAX_MISS_SECONDS, EDGE_CONFIRMATIONS,
    REDISCOVERY_MAX_GAP, PRELOCK_BUFFER_SECONDS, PRELOCK_MAX_CHUNKS,
)
from src.core.arabic import normalize, split_words
from src.core.quran import BASMALA_TEXT, basmala_prefix
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


def _quality(word, at_edge: bool) -> tuple[int, int, int]:
    """How much this observation is worth, highest wins.

    Ordered deliberately:

    1. Was the word actually heard? A gap-fill means the matcher inferred
       the reciter skipped it — that is the *absence* of evidence, and it
       must never overwrite a word some window actually heard. (1:7:8
       الضَّالِّينَ was recited correctly, then erased by a gap-fill when the
       next chunk matched the start of Al-Baqarah.)
    2. The verdict itself. Correct outranks wrong, so once any window has
       heard a word correctly it stays correct — a later window that
       mis-hears it cannot take that away. (1:7:5 الْمَغْضُوبِ was confirmed
       correct and then downgraded to الْمَغْرُوبِ.)
    3. Only then, whether the word sat at a window edge where the boundary
       may have cut it in half.
    """
    heard = 0 if (word.is_gap_filled or not word.recited) else 1
    return (heard, _rank(word), 0 if at_edge else 1)


def _is_fragment(word) -> bool:
    """Did the window cut this word in half rather than the reciter get it wrong?

    A window boundary lands mid-word and the model transcribes what it can
    hear: "سَمِ" of "بِسْمِ", "يُهَا" of "يَاأَيُّهَا". What comes back is a
    piece of the right word, not a different word — so it is not evidence of a
    mistake, however many windows repeat it. Every session starts this way,
    because recitation starts before the microphone is listening.
    """
    if not word.recited or not word.reference:
        return False
    heard = normalize(word.recited)
    whole = normalize(word.reference)
    if len(heard) < 2 or len(heard) >= len(whole):
        return False
    return whole.startswith(heard) or whole.endswith(heard)


class _FragmentWord:
    """The whole word, credited from a piece of it heard at a window edge.

    Committed at gap-fill strength, so it shows on the page but any window
    that hears the word entire — right or wrong — replaces it. A guess about
    clipped audio must never outrank something actually heard.
    """

    __slots__ = ("surah_id", "ayah_id", "reference_index", "reference",
                 "recited", "is_correct", "is_gap_filled")

    def __init__(self, word):
        self.surah_id = word.surah_id
        self.ayah_id = word.ayah_id
        self.reference_index = word.reference_index
        self.reference = word.reference
        self.recited = word.reference
        self.is_correct = True
        self.is_gap_filled = False


class _GapWord:
    """A reference word the reciter covered while the tracker was lost."""

    __slots__ = ("surah_id", "ayah_id", "reference_index", "reference",
                 "recited", "is_correct", "is_gap_filled")

    def __init__(self, surah_id, ayah_id, reference_index, reference):
        self.surah_id = surah_id
        self.ayah_id = ayah_id
        self.reference_index = reference_index
        self.reference = reference
        self.recited = ""
        self.is_correct = False
        self.is_gap_filled = True


class RecitationTracker:
    """Tracks recitation progress and renders results to the UI.

    Owns the state of which surah/ayah/word the user last recited,
    handles page transitions on the Mushaf, and generates styled HTML
    for the transcription output panel.

    Mode state machine:
      - "discovery": waiting for unique verse identification
      - "tracking": locked to position, local matching only
    """

    def __init__(self, mushaf_view: MushafView, page_map: PageMap,
                 quran_index=None, clock=time.monotonic):
        self._mushaf = mushaf_view
        self._page_map = page_map
        # Only needed to fill in an ayah recited while the tracker was lost.
        self._quran_index = quran_index
        # Injectable so the offline benchmark can drive its own virtual clock
        # and reproduce the live timing instead of running at wall speed.
        self._clock = clock

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
        self._first_miss_at: float | None = None
        # Survives a fallback, so a re-lock knows where the reciter was last
        # seen and can fill in whatever they recited while we were lost.
        self._last_known: tuple[int, int, int] | None = None
        # (heard_at, text) for transcriptions discovery could not place. Kept
        # until the position is known, then re-matched against it.
        self._pending: list[tuple[float, str]] = []
        # The basmala is a numbered ayah only at 1:1, so for every other surah
        # there is no reference word to paint and the reciter opens to a blank
        # page. Recognising it is the difference between "the app is thinking"
        # and "the app is broken".
        self._basmala_heard = False

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
        self._first_miss_at = None
        self._last_known = None
        self._pending.clear()
        self._basmala_heard = False

    def set_position(self, surah: int, ayah: int, word_index: int = 0):
        """Manually set position (e.g. user tapped on Mushaf).

        Immediately switches to tracking mode — skips discovery.
        """
        self.last_surah = surah
        self.last_ayah = ayah
        self.last_word_index = word_index
        self._mode = "tracking"
        self._misses = 0
        self._first_miss_at = None
        self._last_known = (surah, ayah, word_index)
        self._pending.clear()

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
                self._note_basmala(text)
                self._remember_unplaced(text)
                return (
                    f'<p align="right" dir="rtl" style="margin-top:0px; margin-bottom:6px; color:#888;">'
                    f'🔍 {text}'
                    f'</p>'
                )
            # Discovery succeeded — unique match found!
            self._mode = "tracking"
            self._misses = 0
            self._first_miss_at = None
            log.count("tracker_locked")
            log.tracker(f"discovery -> tracking @ {match.surah_id}:{match.ayah_id}")
            self._fill_prelock(match)
            self._fill_rediscovery_gap(match)
            # Fall through to process the match

        if match is None:
            # Tracking mode but no local match. If this keeps happening the
            # reciter has moved somewhere we are not following — drop back to
            # discovery rather than staying stuck on a stale pointer.
            if result.get("attempted", True):
                now = self._clock()
                if self._first_miss_at is None:
                    self._first_miss_at = now
                self._misses += 1
                silent_for = now - self._first_miss_at

                log.tracker(
                    f"miss {self._misses}/{TRACKING_MAX_MISSES} "
                    f"({silent_for:.1f}s/{TRACKING_MAX_MISS_SECONDS:.0f}s) "
                    f"@ {self.last_surah}:{self.last_ayah}:{self.last_word_index}"
                )

                # Both must be true. A burst of breath fragments between two
                # ayahs trips the count in under a second; only sustained
                # failure means the reciter really has gone somewhere else.
                if (self._misses >= TRACKING_MAX_MISSES
                        and silent_for >= TRACKING_MAX_MISS_SECONDS):
                    log.count("tracker_fallback")
                    log.tracker("lost position -> falling back to discovery")
                    if self.last_surah is not None:
                        self._last_known = (
                            self.last_surah, self.last_ayah, self.last_word_index
                        )
                    self._mode = "discovery"
                    self._misses = 0
                    self._first_miss_at = None
                    self.last_surah = None
                    self.last_ayah = None
                    self.last_word_index = -1

            return self._render() + (
                f'<p align="right" dir="rtl" style="margin-top:0px; margin-bottom:6px;">{text}</p>'
            )

        self._misses = 0
        self._first_miss_at = None
        self._absorb(match)
        if self.last_surah is not None:
            self._last_known = (self.last_surah, self.last_ayah, self.last_word_index)
        return self._render()

    def _note_basmala(self, text: str):
        """Did the reciter just open with the basmala?

        Three of its four words, so a passing الرحمن الرحيم — which is an ayah
        of its own at 55:1 and part of 1:3 — cannot trigger it.
        """
        if self._basmala_heard or not text:
            return
        words = [normalize(w) for w in split_words(text)]
        if basmala_prefix(words) >= 3:
            self._basmala_heard = True
            log.count("basmala_heard")

    def _remember_unplaced(self, text: str):
        """Hold a transcription discovery could not place.

        Being unable to say *where* a phrase is does not mean it was not
        understood — بسم الله الرحمن الرحيم is refused precisely because it is
        recited 114 times. Keep it until something pins the position down.
        """
        if not text or self._quran_index is None:
            return
        now = self._clock()
        self._pending.append((now, text))
        cutoff = now - PRELOCK_BUFFER_SECONDS
        self._pending = [p for p in self._pending if p[0] >= cutoff]
        if len(self._pending) > PRELOCK_MAX_CHUNKS:
            del self._pending[:-PRELOCK_MAX_CHUNKS]

    def _fill_prelock(self, match):
        """Score what was recited before discovery could say where it was.

        The opening of a surah is the hardest phrase to place and the one
        every session begins with, so the words shown first are never the
        words recited first: Al-Fatiha locked on الرحمن الرحيم and lost
        بسم الله, Al-Baqarah locked on ذلك الكتاب and lost الم entirely.

        Those chunks are still in hand. Now that the position is known they
        are re-matched backwards from it — no new audio, no priors, just the
        same evidence read with the one piece of context it was missing.
        """
        if self._quran_index is None or not self._pending:
            return

        first = next(
            (w for w in match.words
             if w.surah_id is not None and w.reference_index is not None),
            None,
        )
        # Trimming on append alone is not enough: if the reciter falls silent
        # and the next thing to arrive is the lock itself, nothing ever
        # appended and a minutes-old phrase would be placed against it.
        cutoff = self._clock() - PRELOCK_BUFFER_SECONDS
        pending = [p for p in self._pending if p[0] >= cutoff]
        self._pending = []
        if first is None:
            return

        filled = 0
        for _heard_at, text in pending:
            earlier = self._quran_index.rematch_near(
                text, first.surah_id, first.ayah_id, first.reference_index
            )
            if earlier is None:
                continue
            self._absorb(earlier)
            filled += 1

        if filled:
            log.count("prelock_rematched", filled)
            log.tracker(
                f"placed {filled} of {len(pending)} transcription(s) "
                f"recited before the lock"
            )

    def _fill_rediscovery_gap(self, match):
        """Mark words recited while the tracker had lost the reciter.

        Tracking dies, discovery re-locks further along, and everything in
        between was recited but never scored — a whole ayah simply never
        appeared on the page. (1:6 اهدنا الصراط المستقيم vanished this way.)
        """
        if self._quran_index is None or self._last_known is None:
            return
        if self._last_known[0] != match.surah_id:
            return  # a jump to another surah is not a dropped ayah

        first = next(
            (w for w in match.words
             if w.surah_id is not None and w.reference_index is not None),
            None,
        )
        if first is None:
            return

        gap = self._quran_index.words_between(
            self._last_known,
            (first.surah_id, first.ayah_id, first.reference_index),
            limit=REDISCOVERY_MAX_GAP,
        )
        if not gap:
            return

        log.tracker(f"filling {len(gap)} word(s) recited while position was lost")
        log.count("rediscovery_gap_filled", len(gap))
        for surah_id, ayah_id, word_index, text in gap:
            word_id = (surah_id, ayah_id, word_index)
            if word_id in self._scored:
                continue
            self._surah_names.setdefault(surah_id, match.surah_name)
            self._commit(_GapWord(surah_id, ayah_id, word_index, text),
                         (0, 1, 1))

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

            # The pointer records where the reciter IS, not what has been
            # scored. It used to be updated at the bottom of this loop, after
            # several `continue`s — so re-hearing a word already scored left
            # it behind, track() kept searching from a position the reciter
            # had left seconds ago, and three failures dropped us back into
            # discovery. That is what lost the position three times in one
            # recitation of Al-Fatiha.
            if w.recited:
                self.last_surah = w.surah_id
                self.last_ayah = w.ayah_id
                self.last_word_index = w.reference_index

            # A word at the window edge was probably cut in half by the
            # boundary, so the model heard a fragment ("ِينَ" for "الَّذِينَ").
            # That is enough to confirm a word but never to condemn one —
            # leave it unpainted and wait for a window that contains it whole.
            if at_edge and _rank(w) == 2:
                if word_id not in self._scored:
                    # A piece of the right word is not a wrong word. Credit it
                    # and move on — withholding it would only hand it to the
                    # confirmation count below, which cannot tell repeated
                    # clipping apart from a repeated mistake.
                    if _is_fragment(w):
                        self._withheld.pop(word_id, None)
                        self._commit(_FragmentWord(w), (0, 1, 1))
                        log.count("edge_word_fragment")
                        continue
                    seen = self._withheld.get(word_id, (0, None, 0))[0]
                    self._withheld[word_id] = (seen + 1, w, self._observations)
                    # Different windows cut at different points, so a word
                    # that comes back wrong at the edge of several of them is
                    # wrong, not truncated. Stop withholding it.
                    if seen + 1 >= EDGE_CONFIRMATIONS:
                        self._commit(w, _quality(w, at_edge=True))
                        log.count("edge_word_confirmed")
                log.count("edge_word_withheld")
                continue

            self._withheld.pop(word_id, None)

            quality = _quality(w, at_edge)

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
                self._commit(w, _quality(w, at_edge=True))
                log.count("edge_word_committed_on_stop")
        self._withheld.clear()
        return self._render()

    def verdicts(self) -> list[dict]:
        """Every word scored this session, in recitation order.

        The record a session leaves behind: what the app decided, word by
        word, so the same audio can be replayed later and the decision
        compared against it rather than re-judged by hand.
        """
        out = []
        for word_id in self._order:
            entry = self._scored.get(word_id)
            if entry is None:
                continue
            quality, w = entry
            out.append({
                "surah": w.surah_id,
                "ayah": w.ayah_id,
                "word": w.reference_index,
                "reference": w.reference,
                "recited": w.recited,
                "verdict": ("ok" if self._status(w) is True
                            else "missed" if self._status(w) is None
                            else "wrong"),
                # (heard, rank, whole-word) — how good the evidence was.
                # A word credited from a clipped edge scores (0, 1, 1), the
                # same as a gap-fill, and that is the flag to look for when a
                # clip disagrees with what the app believed.
                "evidence": list(quality),
            })
        return out

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
            return self._basmala_html()

        blocks = [self._basmala_html()]
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

    def _basmala_html(self) -> str:
        """Shown once the basmala is recognised, and kept for the session.

        It is not scored: outside Al-Fatiha it is not part of any ayah, so
        there is nothing to be right or wrong against. This says "heard", not
        "correct" — and 1:1 is painted normally by the ayah machinery, so a
        reciter of Al-Fatiha sees it twice over, which is honest.
        """
        if not self._basmala_heard:
            return ""
        return (
            f'<p align="right" dir="rtl" '
            f'style="margin-top:0px; margin-bottom:6px; color:{CORRECT_COLOR};">'
            f'{BASMALA_TEXT}</p>'
        )

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
