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

A word may therefore lose its red as better evidence arrives, but never gain
one on weaker evidence.
"""

import time

from src.config import (
    TRACKING_MAX_MISSES, TRACKING_MAX_MISS_SECONDS, EDGE_CONFIRMATIONS,
    STRICTNESS, STRICTNESS_LEVELS,
    REDISCOVERY_MAX_GAP, PRELOCK_BUFFER_SECONDS, PRELOCK_MAX_CHUNKS,
    POINTER_SLACK,
    PRELOCK_REVEAL_WORDS,
    SETTLE_SECONDS,
)
from src.core.arabic import normalize, split_words
from src.core.quran import BASMALA_TEXT, basmala_prefix
from src.core.debug import log
from src.core.page_map import PageMap
from src.ui.mushaf_view import MushafView
from src.ui.style import (CORRECT_COLOR, INCORRECT_COLOR, MISSED_COLOR,
                          TEXT_PRIMARY, VERSE_REF_COLOR)


class _Pending:
    """Heard, not yet decided — the fourth thing a word can be.

    True, False and None are verdicts: plain ink, red, amber. This is the
    absence of one, and it needs its own value because the page must be able
    to tell "no colour because the word was right" apart from "no colour yet
    because the windows that will judge it have not all arrived". The first
    is a decision and the second is a promise to decide.

    It never reaches the Mushaf as a status. _repaint() turns it into a
    reveal() — the word uncovered and left alone — which is what the app
    already does for words it knows were recited and cannot judge.
    """

    __slots__ = ()

    def __repr__(self):
        return "PENDING"

    def __bool__(self):
        # Guards against `if status:` quietly treating it as a verdict.
        raise TypeError("PENDING is not a verdict; compare with `is PENDING`")


PENDING = _Pending()


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


def _places(word) -> bool:
    """Does this verdict say anything about where the reciter is?

    Only a word whose letters actually line up with the reference does. A
    word the matcher could not recognise lines up with *something* — the
    aligner always pairs what it is given — but which reference word it
    landed on is an artefact of the alignment, not a fact about the reciter.

    This is the same argument _compare_words already makes about echoes, and
    it matters for the same reason. Al-Fath 48:2 was being repeated from its
    start; the window could not see that far back, so three chunks running
    ended in a mis-transcribed token — مَرٌّ, تَقَ, تَعْمَلُونَ — that the
    aligner charged against the word it happened to reach. Each one carried
    the pointer forward: 48:2:11 -> 48:2:14 -> 48:3:3 -> 48:4:4, two ayahs
    past a reciter who had not moved. From there nothing he said could match,
    and four seconds later the position was lost and re-discovered.

    Diacritics do not come into it. عَلِيكَ heard for عَلَيْكَ is the right
    word in the wrong voweling — wrong on the page, but exactly right about
    where he is.
    """
    if not word.recited or not word.reference:
        return False
    if word.is_correct:
        return True
    return normalize(word.recited) == normalize(word.reference) or _is_fragment(word)


def letter_distance(word) -> int:
    """How many letters apart the reciter's word is from the reference.

    Levenshtein on the normalized skeleton, so short vowels do not count —
    Tier 3.6 stopped grading those and this must not quietly bring them back.
    It is what the `words` strictness level thresholds on, and the reason that
    level is labelled by its cost rather than called "lenient":
    فَلَهُمْ/وَلَهُمْ is distance 1, and so is the false alarm it is meant to
    suppress.
    """
    if not word.recited or not word.reference:
        return 0
    a, b = normalize(word.recited), normalize(word.reference)
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


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
                 quran_index=None, clock=time.monotonic,
                 strictness: str | None = None):
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
        # When it was last heard, so it can be attributed to the surah that
        # follows it rather than to every surah for the rest of the session.
        self._basmala_at: float | None = None
        # Surahs the reciter has been judged to have opened with it. The
        # Mushaf prints that line above every surah but At-Tawbah and it
        # belongs to no ayah, so nothing was ever going to colour it word by
        # word — and a reciter watching the line they just recited stay black
        # while the rest of the page fills in reads that as the app having
        # missed it. Sticky, and re-applied on every page turn: it is a
        # decision about the session, not a colour on one page.
        self._basmala_surahs: set[int] = set()
        # What the Mushaf currently shows, so a repaint only pushes changes,
        # and which page that belief is about. load_page() rebuilds the scene,
        # so a belief carried across a page turn is a belief about hitboxes
        # that no longer exist — every word would be skipped as "already this
        # colour" and the new page would stay blank.
        self._painted: dict[tuple[int, int, int], object] = {}
        self._painted_page = mushaf_view.page_serial
        # Words shown without a verdict: recited, never placed. See
        # _reveal_head().
        self._revealed: set[tuple[int, int, int]] = set()

        # How much evidence is needed before a word is painted red. The
        # reciter's decision, not the model's — see STRICTNESS in config.py.
        self._strictness = ""
        self._rules: dict = {}
        self.set_strictness(strictness or STRICTNESS)
        # How many separate audio windows have heard each word wrong, and
        # which observation last counted, so one match cannot count twice.
        self._wrong_count: dict[tuple[int, int, int], int] = {}
        self._wrong_last: dict[tuple[int, int, int], int] = {}
        # Words committed on stop, exempt from the confirmation requirement.
        # See finalize().
        self._no_more_windows: set[tuple[int, int, int]] = set()
        # When each word was last inside an audio window, so a colour is held
        # back until no further window can revise it. See _settled().
        self._seen_at: dict[tuple[int, int, int], float] = {}
        # Set by finalize(): the reciter has stopped, so every word has had
        # every look it is ever going to get.
        self._session_over = False

    # ── Strictness ─────────────────────────────────────────────────────

    @property
    def strictness(self) -> str:
        return self._strictness

    def set_strictness(self, level: str):
        """Change the level, mid-session if you like.

        A repaint follows, so the page immediately reflects the new rule
        rather than only applying it to words not yet recited — the point of
        putting this in reach is to be able to try it on what is on screen.
        """
        if level not in STRICTNESS_LEVELS:
            raise ValueError(
                f"unknown strictness {level!r}; "
                f"choose from {', '.join(STRICTNESS_LEVELS)}"
            )
        self._strictness = level
        self._rules = STRICTNESS_LEVELS[level]
        log.event("STRICTNESS", level)
        if self._painted:
            self._repaint()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def last_surah_name(self) -> str | None:
        """The name of the surah being recited, for the bar to say.

        "An-Nisa 4:12" is a place; "4:12" is a coordinate you have to look
        up. In Latin letters, because the bar is set left to right and the
        Arabic name beside an ayah number comes out reordered — see
        QuranIndex.surah_label().
        """
        if self.last_surah is None:
            return None
        if self._quran_index is not None:
            return self._quran_index.surah_label(self.last_surah)
        return self._surah_names.get(self.last_surah)

    def reset(self):
        self.last_surah = None
        self.last_ayah = None
        self.last_word_index = -1
        self._scored.clear()
        self._wrong_count.clear()
        self._wrong_last.clear()
        self._no_more_windows.clear()
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
        self._basmala_at = None
        self._basmala_surahs.clear()
        self._painted.clear()
        self._revealed.clear()
        self._seen_at.clear()
        self._session_over = False

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
            just_locked = True
            # Fall through to process the match
        else:
            just_locked = False

        if match is None:
            # Tracking mode but no local match. If this keeps happening the
            # reciter has moved somewhere we are not following — drop back to
            # discovery rather than staying stuck on a stale pointer.
            # A basmala is the commonest reason a tracked chunk fails to
            # match: it belongs to no ayah, so the reciter finishing one surah
            # and opening the next reads as a miss. Hearing it here is what
            # lets the next surah's bismillah line be painted.
            self._note_basmala(text)
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
        if just_locked:
            # After _absorb, not before: the page the words live on is loaded
            # there, and there is nothing to reveal on a page that is not up.
            self._reveal_head(match)
        if self.last_surah is not None:
            self._last_known = (self.last_surah, self.last_ayah, self.last_word_index)
        return self._render()

    def _note_basmala(self, text: str):
        """Did the reciter just open with the basmala?

        Three of its four words, so a passing الرحمن الرحيم — which is an ayah
        of its own at 55:1 and part of 1:3 — cannot trigger it.

        Recorded every time, not only the first. A session is not one surah:
        the reciter finishes one and opens the next, and the basmala that
        opens the fourth surah must be credited to the fourth surah and not
        still be sitting there from the first.
        """
        if not text:
            return
        words = [normalize(w) for w in split_words(text)]
        if basmala_prefix(words) < 3:
            return
        if not self._basmala_heard:
            log.count("basmala_heard")
        self._basmala_heard = True
        self._basmala_at = self._clock()

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
        now = self._clock()

        edge_ids = self._edge_word_ids(match)
        current_page = None

        # How far into this match the alignment is still vouched for. Past
        # the last word whose letters actually line up, the pairing is the
        # aligner's arithmetic and nothing more — see _places().
        placed = [i for i, w in enumerate(match.words) if _places(w)]
        pointer_limit = (placed[-1] + POINTER_SLACK) if placed else -1

        for position, w in enumerate(match.words):
            if w.surah_id is None or w.ayah_id is None or w.reference_index is None:
                continue
            if not w.recited and not w.reference:
                continue

            word_id = (w.surah_id, w.ayah_id, w.reference_index)
            at_edge = word_id in edge_ids

            # This window contained the word, so the clock on how long it has
            # been out of the windows restarts — whatever the verdict was and
            # whether or not it wins the quality contest below. A word being
            # re-recited restarts it too, which is right: the reciter going
            # back over a line is exactly when a verdict should be reopened.
            self._seen_at[word_id] = now

            # Count how many separate windows have heard this word wrong.
            # Counted before the edge branch and before the quality contest,
            # because what the `confirmed` level asks is "how much evidence
            # is there", not "which single observation won". A fragment is a
            # piece of the right word, not a wrong one, so it never counts.
            if _rank(w) == 2 and not _is_fragment(w):
                if self._wrong_last.get(word_id) != self._observations:
                    self._wrong_last[word_id] = self._observations
                    self._wrong_count[word_id] = self._wrong_count.get(word_id, 0) + 1

            # The pointer records where the reciter IS, not what has been
            # scored. It used to be updated at the bottom of this loop, after
            # several `continue`s — so re-hearing a word already scored left
            # it behind, track() kept searching from a position the reciter
            # had left seconds ago, and three failures dropped us back into
            # discovery. That is what lost the position three times in one
            # recitation of Al-Fatiha.
            if w.recited and position <= pointer_limit:
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

            # After the page is up, so the line exists to be painted.
            self._claim_basmala(w.surah_id, w.ayah_id)
            self._commit(w, quality, previous)

        # A repaint even when nothing was committed. Under `confirmed` the
        # second window to hear a word wrong changes its colour without
        # changing the winning evidence — the quality contest rejects it as
        # "equal or better already held" and returns early, so no _commit
        # fires and the page would keep showing amber for a word that is now
        # confirmed wrong. The same reason _repaint() exists for neighbours.
        self._repaint()

    def _claim_basmala(self, surah_id: int, ayah_id: int):
        """Paint the bismillah line the reciter opened this surah with.

        The reciter is the only evidence there is. The line is part of no
        ayah outside 1:1, so there is nothing to compare a transcription
        against word by word — but a basmala heard shortly before the first
        ayah of a surah arrives is, near enough always, that surah's basmala,
        and it is four short words nobody gets wrong. So it is accepted on
        the reciter's word — which, now that correct recitation carries no
        colour, means the line is left as printed rather than marked.

        Three things keep that from becoming a lie:

        - only at ayah 1. Landing mid-surah says nothing about how the surah
          was opened, and may be a re-lock long after it was.
        - only if the basmala was heard within the pre-lock window. Otherwise
          one basmala at the start of a session would go on crediting every
          surah recited for the rest of it.
        - At-Tawbah has no bismillah line and Al-Fatiha's *is* ayah 1:1,
          scored word by word like anything else. Neither is in the Mushaf's
          bismillah map, so both decline themselves.
        """
        if ayah_id != 1 or surah_id in self._basmala_surahs:
            return
        if self._basmala_at is None:
            return
        if self._clock() - self._basmala_at > PRELOCK_BUFFER_SECONDS:
            return
        if not self._mushaf.paint_basmala(surah_id, True):
            return
        self._basmala_surahs.add(surah_id)
        log.count("basmala_painted")
        log.mushaf(f"painted the bismillah of surah {surah_id}")

    def _reveal_head(self, match):
        """Show the words recited before discovery could say where they were.

        Discovery needs several words before a position is unique, so the run
        it spends getting there is exactly the run that never reaches the
        page: every session opens with its first phrase missing, and the
        reciter sees the Mushaf start filling in from the middle of the ayah
        they began. _fill_prelock() recovers a verdict wherever the held
        transcriptions can be lined up against the reference; this is what is
        left when they cannot.

        These words are revealed, not coloured. The app knows they were
        recited — that is what the lock landing here means — and does not
        know whether they were right. Green would be a claim it never made,
        amber would call correct recitation a mistake.
        """
        first = next(
            (w for w in match.words
             if w.surah_id is not None and w.reference_index is not None),
            None,
        )
        if first is None or first.reference_index <= 0:
            return
        start = max(0, first.reference_index - PRELOCK_REVEAL_WORDS)
        shown = 0
        for index in range(start, first.reference_index):
            word_id = (first.surah_id, first.ayah_id, index)
            if word_id in self._scored or word_id in self._revealed:
                continue
            self._revealed.add(word_id)
            if self._mushaf.reveal(*word_id):
                shown += 1
        if shown:
            log.count("prelock_revealed", shown)
            log.tracker(
                f"revealed {shown} unscored word(s) before the lock at "
                f"{first.surah_id}:{first.ayah_id}:{first.reference_index}"
            )

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
        # Painting is deferred to _repaint(): whether a word may be shown as
        # wrong depends on its neighbours, which may not have been heard yet.
        self._repaint()

    # ── Is this verdict safe to show as an error? ──────────────────────

    def _neighbours_clear(self, word_id) -> bool:
        """Is this word surrounded by words we are confident about?

        Tilawa's rule (`correction.ts`): never condemn a word in a region you
        do not trust. A wrong verdict between two words that were themselves
        mis-heard is far more likely to be the alignment slipping than the
        reciter erring — and both neighbours must be in the *same ayah*, so a
        word at an ayah edge is never condemned on the strength of whatever
        the neighbouring ayah happened to do.
        """
        surah, ayah, index = word_id
        clear = 0
        for neighbour in ((surah, ayah, index - 1), (surah, ayah, index + 1)):
            entry = self._scored.get(neighbour)
            if entry is not None and self._status(entry[1]) is True:
                clear += 1
        return clear >= 1

    def _settled(self, word_id) -> bool:
        """Has this word had every look it is ever going to get?

        A word sits inside the sliding window for one window's length after
        it is recited, and is transcribed afresh by each of the ~20 windows
        that contain it. Until the last of them has been and gone, the
        verdict on the page is provisional — and since a correct look
        permanently outranks a wrong one, "provisional" in practice means
        "may be about to lose its red".

        So the word is settled once nothing has mentioned it for SETTLE_
        SECONDS, or once the reciter has stopped and no window is coming at
        all. `_no_more_windows` says the same thing about one word: it is
        set by finalize() for verdicts that reached the end of the audio.
        """
        if self._session_over or word_id in self._no_more_windows:
            return True
        seen = self._seen_at.get(word_id)
        if seen is None:
            # Never observed directly — a gap-fill or a pre-lock recovery.
            # No window was ever going to look at it again anyway.
            return True
        return (self._clock() - seen) >= SETTLE_SECONDS

    def _effective_status(self, word_id):
        """The colour a word actually gets, after the neighbour rule.

        True, False, None — or PENDING, meaning the word has been heard and
        the app is not finished deciding. PENDING is returned *before* any
        verdict is considered, because a word still inside the windows can
        have its verdict changed by any of them: painting it amber while
        waiting would only trade a red that flickers for an amber that does.
        """
        entry = self._scored.get(word_id)
        if entry is None:
            return None
        if not self._settled(word_id):
            return PENDING
        status = self._status(entry[1])
        if status is not False:
            return status

        # Wrong — but how much has to be true before the page says so is the
        # reciter's choice. Everything below declines to condemn by returning
        # amber, never by returning True: "I could not read this" is honest,
        # "you said it correctly" would not be.
        rules = self._rules
        if not rules["paint_wrong"]:
            return None

        # Both gates below are written so that a threshold of 1 means *no
        # gate at all*, not "a gate that happens to pass". `strict` sets both
        # to 1 and must therefore be bit-identical to the behaviour this
        # codebase had before strictness existed — otherwise the benchmark
        # baseline moves and every number ever recorded becomes incomparable.
        if rules["min_letter_diff"] > 1:
            # Distance is measured on the normalized skeleton, so a
            # diacritics-only error is distance 0. That is deliberate: a
            # level called "words only" forgives it along with single-letter
            # errors. It must never reach `strict`, which would switch off
            # diacritic grading entirely without anyone asking.
            if letter_distance(entry[1]) < rules["min_letter_diff"]:
                return None
        if rules["confirmations"] > 1:
            if (word_id not in self._no_more_windows
                    and self._wrong_count.get(word_id, 0) < rules["confirmations"]):
                return None

        # Wrong, and only sayable as wrong in a region we trust. Otherwise it
        # is amber: something happened here that we could not read.
        return False if self._neighbours_clear(word_id) else None

    def _repaint(self):
        """Push every verdict whose colour has changed since last time.

        A word's colour can change without new evidence about that word: its
        neighbour being confirmed is what licenses it to be shown as wrong.

        A page turn is the other way a colour changes without evidence:
        load_page() rebuilds the scene, so every hitbox this cache is about
        has been destroyed and the new page is blank. Believing otherwise
        makes each word "already that colour" and nothing is ever pushed —
        which is why a page turn used to leave the page it turned to empty.
        """
        if self._mushaf.page_serial != self._painted_page:
            self._painted_page = self._mushaf.page_serial
            self._painted.clear()
            self._revealed.clear()
            for surah_id in self._basmala_surahs:
                self._mushaf.paint_basmala(surah_id, True)

        for word_id in self._order:
            if word_id not in self._scored:
                continue
            status = self._effective_status(word_id)
            if self._painted.get(word_id, "unset") is status:
                continue
            self._painted[word_id] = status
            if status is PENDING:
                # Uncover it and say nothing. The reciter sees the word they
                # just recited appear, which is the feedback that matters
                # while reciting; the verdict follows when it is one.
                self._mushaf.reveal(*word_id)
                self._revealed.add(word_id)
            else:
                self._mushaf.update_recitation(*word_id, status)

    def tick(self):
        """Repaint on the clock alone, with no new audio to absorb.

        Every other repaint is driven by a window arriving, but settling is a
        fact about elapsed time. A reciter who pauses — to breathe, to think,
        to find their place — stops producing windows, and without this the
        last words they said would sit uncoloured until they either resumed
        or stopped listening altogether. Cheap to call often: _repaint()
        pushes only the words whose colour actually changed.
        """
        if self._scored:
            self._repaint()

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
                # Exempt from the strictness confirmation count. That rule
                # says "wait for another window to agree"; here the reciter
                # has stopped and another window is never coming, so waiting
                # is waiting forever. Without this, a mistake in the final
                # word of a recitation is silently dropped at every level
                # above `strict` — and the final word is exactly where the
                # evidence is thinnest by construction.
                self._no_more_windows.add(word_id)
                self._commit(w, _quality(w, at_edge=True))
                log.count("edge_word_committed_on_stop")
        self._withheld.clear()
        # Every word is settled now, by definition: the audio has stopped, so
        # the window that would have revised one is never built. Without this
        # a session that ends within SETTLE_SECONDS of its last mistake would
        # leave that mistake uncoloured — the page would go quiet holding a
        # verdict it had already reached.
        self._session_over = True
        self._repaint()
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
        """Mushaf verdict: True plain ink, False red, None amber."""
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
            spans.append(self._span(w, self._effective_status(word_id)))

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
    def _span(w, status=None) -> str:
        if status is True:
            return f'<span style="color:{CORRECT_COLOR};">{w.recited}</span>'
        if status is False:
            return f'<span style="color:{INCORRECT_COLOR};">{w.recited}</span>'
        if status is PENDING:
            # Heard, undecided. Shown as recited and left uncoloured, the
            # same thing the page does — never underlined as missed, which
            # would report a word the reciter said as one they did not.
            return f'<span style="color:{TEXT_PRIMARY};">{w.recited}</span>'
        return (
            f'<span style="color:{MISSED_COLOR}; text-decoration:underline;">'
            f'{w.reference}</span>'
        )
