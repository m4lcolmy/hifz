"""Quran text search and word-by-word comparison engine.

Loads the full Quran text and provides:
  - Discovery mode: N-gram inverted index with uniqueness gating
  - Tracking mode: local pointer-locked word matching
  - Word-by-word comparison with diacritics
"""

import json
import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from src.core.arabic import normalize, split_words
from src.config import (
    QURAN_JSON, DISCOVERY_MIN_WORDS, DISCOVERY_NGRAM_SIZES,
    DISCOVERY_SHORT_NGRAM, DISCOVERY_SHORT_MAX_WORDS,
    TRACKING_WINDOW, TRACKING_MAX_GAP, TRACKING_MIN_MATCHES,
    TRACKING_WEAK_EVIDENCE, TRACKING_WEAK_MAX_DRIFT,
    DISCOVERY_SOLO_AYAH, PRELOCK_LOOKBACK_WORDS, PRELOCK_MIN_MATCHES,
    ECHO_LOOKBACK, SKIP_RESUME_WORDS,
)


@dataclass
class WordResult:
    """Result for a single word comparison."""
    recited: str          # what the user said
    reference: str        # what the Quran says (may be empty for extra words)
    is_correct: bool      # True = exact match including diacritics
    surah_id: int | None = None
    ayah_id: int | None = None
    reference_index: int | None = None  # word position inside the ayah
    is_gap_filled: bool = False  # reference word the reciter skipped over


@dataclass
class VerseMatch:
    """A matched verse with word-by-word comparison results."""
    surah_id: int
    surah_name: str
    ayah_id: int
    start_offset: int     # Word index where the chunk started matching
    words: list[WordResult]


# The basmala, normalized. It is a numbered ayah only at 1:1, but it is
# recited before every surah, so the app hears it constantly with nothing to
# match it against — and its loose tail is worse than useless. In
# logs/hifz-20260919-144348.log "الرَّحِيم قَالَ هَ" matched a trigram spanning
# the 28:16/28:17 boundary, and the app jumped 150 pages into Al-Qasas.
BASMALA = ("بسم", "الله", "الرحمن", "الرحيم")
BASMALA_TEXT = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"


def _boundary_span(trans_norm: list[str], ref_norm: list[str],
                   i1: int, i2: int, j1: int, j2: int,
                   limit: int = 3) -> tuple[int, int] | None:
    """Is the head of this mismatch a misplaced space rather than a mistake?

    Returns (heard_end, ref_end) when the heard tokens `trans_norm[i1:end]`
    and the reference words `ref_norm[j1:end]` are the same letters with the
    spaces in different places — one token covering several words, or several
    covering one. Returns None otherwise.

    Equality is exact on the normalized skeleton and on the *whole* joined
    run, so there is exactly one way to read it. That is what makes this safe
    to forgive: it says nothing about which letters were recited, only about
    where the model chose to break them up.
    """
    # One heard token, several reference words: أَوْدَيْنٍ ← أَوْ دَيْنٍ
    if i2 - i1 >= 1:
        heard = trans_norm[i1]
        joined = ""
        for n in range(1, min(limit, j2 - j1) + 1):
            joined += ref_norm[j1 + n - 1]
            if n >= 2 and joined == heard:
                return i1 + 1, j1 + n
    # Several heard tokens, one reference word: فَلِ كُلِّ ← فَلِكُلِّ
    if j2 - j1 >= 1:
        whole = ref_norm[j1]
        joined = ""
        for n in range(1, min(limit, i2 - i1) + 1):
            joined += trans_norm[i1 + n - 1]
            if n >= 2 and joined == whole:
                return i1 + n, j1 + 1
    return None


def _charge_lone_resumes(opcodes: list, min_resume: int) -> list:
    """Make the aligner pay for the reference words it walks past.

    `difflib` will skip any number of reference words to reach one that
    matches, because matching more characters is all it is trying to do. In
    ordinary text that is the right instinct. In the Quran it is not: ayahs
    are built from a small, repeating vocabulary, so the wrong word a reciter
    says is very often a word that occurs again a line or two down — and the
    aligner would rather jump to it than call it a mistake.

    74:22 ثُمَّ عَبَسَ وَبَسَرَ, 74:23 ثُمَّ أَدْبَرَ وَاسْتَكْبَرَ. Recite
    "ثم عبس واستكبر" and it steps over وَبَسَرَ, ثُمَّ and أَدْبَرَ to reach
    وَاسْتَكْبَرَ, which costs three inventions and a lie: the mistake is
    scored correct, three words nobody touched are scored skipped, and the
    pointer lands an ayah ahead of the reciter.

    So a skipped run of two or more is believed only when `min_resume`
    reference words line up consecutively after it. A single one is
    coincidence, and the skip is rewritten as a substitution — the heard word
    charged against the reference word that was actually due. A run of one is
    always believed: dropping a word is the commonest thing a reciter does,
    and nothing is claimed by letting the alignment shift by one.
    """
    out = []
    i = 0
    while i < len(opcodes):
        op, i1, i2, j1, j2 = opcodes[i]
        if op == "insert" and j2 - j1 >= 2 and i + 1 < len(opcodes):
            next_op, _ni1, ni2, _nj1, nj2 = opcodes[i + 1]
            if next_op == "equal" and ni2 - i2 < min_resume:
                # The insert consumes no heard words, so its i-range is the
                # empty span the following equal starts from: the two are
                # adjacent on both sides and fuse into one substitution.
                out.append(("replace", i1, ni2, j1, nj2))
                i += 2
                continue
        out.append((op, i1, i2, j1, j2))
        i += 1
    return out


def _same_word(heard: str, expected: str) -> bool:
    """Equal, or one clipped by a window boundary — سم for بسم."""
    if heard == expected:
        return True
    shorter, longer = sorted((heard, expected), key=len)
    return (len(shorter) >= 2
            and (longer.startswith(shorter) or longer.endswith(shorter)))


def basmala_prefix(trans_norm: list[str]) -> int:
    """How many leading words of a transcription are part of a basmala.

    The reciter may be heard from partway in — a window that opens mid-phrase
    gives "رحمن الرحيم" — so the transcription is aligned against every
    starting point in the basmala, not just the first.

    Two words are required. One is not enough: اللَّهِ alone is the commonest
    word in the Quran, and الرَّحْمَٰنِ is an ayah of its own at 55:1.
    """
    best = 0
    for start in range(len(BASMALA)):
        k = 0
        while (k < len(trans_norm) and start + k < len(BASMALA)
               and _same_word(trans_norm[k], BASMALA[start + k])):
            k += 1
        if k >= 2:
            best = max(best, k)
    return best


class QuranIndex:
    """Searchable index of the entire Quran text.

    Two search modes:
      - discover(): exact N-gram lookup, only returns when match is unique
      - track(): local search around current pointer, never global
    """

    def __init__(self):
        self._surahs: list[dict] = []
        # Flat word list: [(normalized_word, original_word, surah_idx, ayah_idx, word_idx)]
        self._flat: list[tuple[str, str, int, int, int]] = []

        # N-gram inverted index: tuple of N normalized words → list of flat positions
        self._ngram_index: dict[tuple[str, ...], list[int]] = {}

        # Single-word index for fallback
        self._word_index: dict[str, list[int]] = {}

        # Position map: (surah, ayah, word_idx) -> flat_pos
        self._pos_map: dict[tuple[int, int, int], int] = {}

        # Ayahs that are a single word, keyed by that word — but only where
        # the word occurs nowhere else in the Quran. See discover().
        self._solo_ayah_index: dict[str, int] = {}

        self._load()

    def _load(self):
        """Load Quran JSON and build search indices."""
        with open(QURAN_JSON, encoding="utf-8") as f:
            self._surahs = json.load(f)

        # Build flat word list
        for surah in self._surahs:
            s_id = surah["id"]
            for verse in surah["verses"]:
                a_id = verse["id"]
                # split_words() keeps word_idx aligned with the QCF Mushaf
                # word positions used for on-page highlighting.
                for word_idx, w in enumerate(split_words(verse["text"])):
                    norm = normalize(w)
                    if norm:
                        pos = len(self._flat)
                        self._flat.append((norm, w, s_id, a_id, word_idx))
                        self._word_index.setdefault(norm, []).append(pos)
                        self._pos_map[(s_id, a_id, word_idx)] = pos

        # Ayahs made of exactly one word, where that word appears nowhere
        # else. Uniqueness is checked against the whole Quran and not just
        # against other solo ayahs: الم is a one-word ayah six times over, and
        # normalization also merges it with the ألم of أَلَمْ تَرَ, so it is
        # excluded — correctly, since hearing الم really does not say which of
        # the six surahs was opened.
        for surah in self._surahs:
            for verse in surah["verses"]:
                words = [w for w in split_words(verse["text"]) if normalize(w)]
                if len(words) != 1:
                    continue
                norm = normalize(words[0])
                if len(self._word_index.get(norm, ())) == 1:
                    self._solo_ayah_index[norm] = self._word_index[norm][0]

        # Build N-gram inverted index for discovery
        for n in DISCOVERY_NGRAM_SIZES:
            for i in range(len(self._flat) - n + 1):
                # Only index within same surah (don't span surah boundaries)
                if self._flat[i][2] != self._flat[i + n - 1][2]:
                    continue
                key = tuple(self._flat[i + k][0] for k in range(n))
                if key not in self._ngram_index:
                    self._ngram_index[key] = []
                self._ngram_index[key].append(i)

    # ── Discovery Mode ─────────────────────────────────────────────────

    def surah_label(self, surah_id: int) -> str:
        """The surah's name in Latin letters, for a left-to-right caption.

        The status bar is set left to right, and an Arabic name placed next
        to an ayah number comes out reordered by the bidi algorithm — النساء
        4:12 reads with the number on the wrong side of the name. The
        transliteration has no direction of its own to fight with.

        Falls back to the Arabic name if the JSON has no transliteration, and
        to the bare number if there is no such surah.
        """
        if not 1 <= surah_id <= len(self._surahs):
            return str(surah_id)
        surah = self._surahs[surah_id - 1]
        return surah.get("transliteration") or surah.get("name") or str(surah_id)

    def is_solo_ayah(self, word: str) -> bool:
        """Is this one word an entire ayah, and found nowhere else?

        Asked by `match_for_mode` before it applies the two-word discovery
        floor. يس and وَالْعَصْرِ are whole ayahs; refusing them for being one
        word means they can never be found, only stepped over.
        """
        return DISCOVERY_SOLO_AYAH and normalize(word) in self._solo_ayah_index

    def discover(self, transcription: str) -> "VerseMatch | None":
        """Find where the user is reciting using exact N-gram matching.

        Returns a match ONLY when the phrase uniquely identifies a single
        position in the Quran. Returns None if ambiguous or too few words.
        """
        # Tokenized like the index, so "يا ايها" becomes the single word the
        # Mushaf has. Keeps trans_words and trans_norm index-aligned too.
        trans_words = split_words(transcription)
        trans_norm = [normalize(w) for w in trans_words]

        if len(trans_norm) < DISCOVERY_MIN_WORDS:
            # One word is normally far too little to place, but an ayah that
            # *is* one word has no longer phrase to wait for: hold out for a
            # second word and يس or وَالْعَصْرِ can never be found at all, only
            # stepped over on the way to the next ayah.
            if (DISCOVERY_SOLO_AYAH and len(trans_norm) == 1
                    and trans_norm[0] in self._solo_ayah_index):
                return self._build_match(
                    self._solo_ayah_index[trans_norm[0]], trans_words
                )
            return None

        # Try N-grams from largest to smallest, using the TAIL of transcription
        # (freshest/most recent words are most reliable)
        for n in DISCOVERY_NGRAM_SIZES:
            if len(trans_norm) < n:
                continue

            # Short n-grams are only trustworthy on short transcriptions —
            # on a long noisy one they are an easy way to jump somewhere wrong.
            if n <= DISCOVERY_SHORT_NGRAM and len(trans_norm) > DISCOVERY_SHORT_MAX_WORDS:
                continue

            # Try multiple sliding positions from the tail
            best_candidates = None
            best_ngram_start = None

            for offset in range(min(len(trans_norm) - n + 1, 4)):
                start = len(trans_norm) - n - offset
                if start < 0:
                    break
                # A two-word phrase is only trusted where it is freshest: the
                # tail, which is what the reciter is saying now. Further back
                # in a short noisy chunk it is leftovers, and leftovers land
                # somewhere real — the basmala's last word followed by the
                # first word of what came next read as "الرحيم قال", unique
                # across the 28:16/28:17 boundary, and sent the app 150 pages
                # into Al-Qasas while the reciter was opening Al-Hijr.
                if n <= DISCOVERY_SHORT_NGRAM and offset > 0:
                    break
                ngram = tuple(trans_norm[start:start + n])
                candidates = self._ngram_index.get(ngram, [])

                if len(candidates) == 1:
                    # Unique match! Compute actual start position
                    # The ngram matched at flat position candidates[0],
                    # but the transcription started `start` words before the ngram
                    flat_pos = max(0, candidates[0] - start)
                    return self._build_match(flat_pos, trans_words)

                if len(candidates) > 0 and (best_candidates is None or len(candidates) < len(best_candidates)):
                    best_candidates = candidates
                    best_ngram_start = start

            # If we found candidates but > 1, check if adding more context
            # from the transcription can disambiguate
            if best_candidates and len(best_candidates) <= 5 and best_ngram_start is not None:
                # Try to disambiguate by checking surrounding words
                unique_pos = self._disambiguate(trans_norm, best_candidates, best_ngram_start)
                if unique_pos is not None:
                    flat_pos = max(0, unique_pos - best_ngram_start)
                    return self._build_match(flat_pos, trans_words)

        return None

    def _disambiguate(self, trans_norm: list[str], candidates: list[int], ngram_start: int) -> int | None:
        """Try to narrow multiple candidates to one by checking surrounding words."""
        best_pos = None
        best_score = -1
        unique = True

        for cand_pos in candidates:
            # Align: the ngram starts at trans_norm[ngram_start], flat[cand_pos]
            # So transcription start aligns to flat[cand_pos - ngram_start]
            aligned_start = cand_pos - ngram_start
            if aligned_start < 0:
                continue

            score = 0
            for i, tw in enumerate(trans_norm):
                flat_i = aligned_start + i
                if 0 <= flat_i < len(self._flat) and self._flat[flat_i][0] == tw:
                    score += 1

            if score > best_score:
                best_score = score
                best_pos = cand_pos
                unique = True
            elif score == best_score:
                unique = False

        # Only return if one candidate clearly wins
        if unique and best_score >= len(trans_norm) * 0.8:
            return best_pos
        return None

    # ── Tracking Mode ──────────────────────────────────────────────────

    def track(self, transcription: str, context_surah: int, context_ayah: int, context_word_index: int) -> "VerseMatch | None":
        """Match transcription against local context only.

        Searches ONLY within ±TRACKING_WINDOW words of the current pointer.
        Never does global search — if no local match, returns None.
        """
        trans_words = split_words(transcription)
        trans_norm = [normalize(w) for w in trans_words]
        if not trans_norm:
            return None

        # Find the flat index for current context position
        context_pos = self._find_context_pos(context_surah, context_ayah, context_word_index)
        if context_pos is None:
            return None

        # Search window: from current position backwards (overlap) to ahead
        window_start = max(0, context_pos - len(trans_norm))
        window_end = min(len(self._flat), context_pos + TRACKING_WINDOW + 1)

        if window_start >= window_end:
            return None

        # Extract local window of normalized words
        window_norm = [self._flat[i][0] for i in range(window_start, window_end)]

        # Use SequenceMatcher on this small local window only
        sm = difflib.SequenceMatcher(None, trans_norm, window_norm)
        blocks = sm.get_matching_blocks()

        if not blocks or blocks[0].size == 0:
            return None

        match_count = sum(b.size for b in blocks)
        if match_count < TRACKING_MIN_MATCHES:
            return None

        # Anchor on the LONGEST run of matching words, not the first one.
        # Ayahs share opening words — 83:14 and 83:18 both start with كَلَّا —
        # so anchoring on the first match lets a single common word drag the
        # whole alignment onto the wrong ayah, and every following word is
        # then compared against the wrong reference.
        anchor = max(
            (b for b in blocks if b.size > 0),
            key=lambda b: (b.size, -b.a),
            default=None,
        )
        if anchor is None:
            return None

        best_start = max(0, window_start + anchor.b - anchor.a)

        # A chunk full of mis-transcribed words may still align by coincidence
        # somewhere in the window. Only trust a thin match if it lands where
        # the reciter already was; otherwise it drags the pointer off course.
        if (match_count <= TRACKING_WEAK_EVIDENCE
                and abs(best_start - context_pos) > TRACKING_WEAK_MAX_DRIFT):
            return None

        # If the match starts ahead of the pointer, the reciter skipped words.
        # Report them as gap-filled so the caller can mark them as missed and
        # keep the pointer in sync instead of losing the position entirely.
        gap_start = None
        if context_pos < best_start <= context_pos + TRACKING_MAX_GAP:
            gap_start = context_pos

        return self._build_match(best_start, trans_words, gap_start=gap_start)

    def rematch_near(self, transcription: str, surah: int, ayah: int,
                     word_index: int,
                     lookback: int = PRELOCK_LOOKBACK_WORDS) -> "VerseMatch | None":
        """Align a transcription against the words leading up to a position.

        Discovery only answers when a phrase is unique, so everything recited
        before the lock is understood and then discarded — the opening of
        Al-Fatiha matches 114 places, and الم six surahs. Once a later phrase
        pins the position down, those earlier transcriptions can be placed
        after all, because the question was never what was said but where.

        Unlike track() this looks *backwards* and fills no gaps: it claims
        only the words it can actually align, and never past the start of the
        surah the lock landed in.
        """
        trans_words = split_words(transcription)
        trans_norm = [normalize(w) for w in trans_words]
        if not trans_norm:
            return None

        anchor = self._pos_map.get((surah, ayah, word_index))
        if anchor is None:
            return None

        window_start = max(0, anchor - lookback)
        window_end = min(len(self._flat), anchor + len(trans_norm))
        if window_start >= window_end:
            return None

        window_norm = [self._flat[i][0] for i in range(window_start, window_end)]
        sm = difflib.SequenceMatcher(None, trans_norm, window_norm)
        blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
        if not blocks:
            return None

        # No pointer vouches for these chunks — discovery has already refused
        # them once — so the alignment has to carry the whole weight. Several
        # words lining up is enough; so is a short transcription that lines up
        # completely, which is the only evidence a one-word ayah can offer.
        match_count = sum(b.size for b in blocks)
        if match_count < PRELOCK_MIN_MATCHES and match_count < len(trans_norm):
            return None

        best = max(blocks, key=lambda b: (b.size, -b.a))
        best_start = max(0, window_start + best.b - best.a)
        # `> anchor`, not `>= anchor`: the lock word itself is usually the one
        # that needs this most. Discovery locks on the first phrase it can tell
        # apart, and that phrase's own opening word is at the window edge where
        # it was clipped — 4:1:0 يَاأَيُّهَا was heard as وَيُّهَا exactly once
        # and withheld, while a chunk from a second earlier had it perfectly.
        if best_start > anchor:
            return None                              # nothing at or before the lock
        if self._flat[best_start][2] != surah:
            return None                              # do not reach into the surah before

        return self._build_match(best_start, trans_words)

    def last_ayah(self, surah_id: int) -> int | None:
        """The number of the surah's final ayah, or None if there is no such
        surah.

        Which is all it takes to label a recording of a whole surah: the
        range is 1 to this, so a file named after its surah cannot carry a
        wrong ayah range, because it carries none.
        """
        if not 1 <= surah_id <= len(self._surahs):
            return None
        verses = self._surahs[surah_id - 1]["verses"]
        return verses[-1]["id"] if verses else None

    def words_in_range(self, surah: int, first_ayah: int, last_ayah: int) -> int:
        """How many words a stretch of recitation contains.

        The denominator the benchmark was missing. A false alarm rate counted
        over the words that happened to get a verdict says nothing about the
        words that never did, and those are the more serious failure.
        """
        return sum(
            1 for _n, _w, s_id, a_id, _i in self._flat
            if s_id == surah and first_ayah <= a_id <= last_ayah
        )

    def words_between(self, start: tuple[int, int, int], end: tuple[int, int, int],
                      limit: int = 40) -> list[tuple[int, int, int, str]]:
        """Reference words strictly between two positions, in order.

        Used to fill in an ayah the reciter said while the tracker had lost
        them: discovery lands ahead of where tracking died, and everything
        between was recited but never scored.

        Returns [] if either end is unknown, if they run backwards, or if the
        gap is implausibly large (a real jump, not a dropped ayah).
        """
        start_pos = self._pos_map.get(start)
        end_pos = self._pos_map.get(end)
        if start_pos is None or end_pos is None:
            return []
        if not 0 < end_pos - start_pos - 1 <= limit:
            return []

        out = []
        for i in range(start_pos + 1, end_pos):
            _, word, s_id, a_id, w_idx = self._flat[i]
            out.append((s_id, a_id, w_idx, word))
        return out

    def _find_context_pos(self, surah: int, ayah: int, word_index: int) -> int | None:
        """Find the flat index for a given surah/ayah/word position.

        Returns the position of the NEXT expected word (context + 1).
        """
        pos = self._pos_map.get((surah, ayah, word_index))
        if pos is not None:
            return pos + 1
        return None

    # ── Shared helpers ─────────────────────────────────────────────────

    def _build_match(
        self,
        flat_pos: int,
        trans_words: list[str],
        gap_start: int | None = None,
    ) -> "VerseMatch | None":
        """Build a VerseMatch from a flat position and transcription words.

        If `gap_start` is given, the reference words in [gap_start, flat_pos)
        are prepended as gap-filled results — words the reciter skipped over.
        """
        if flat_pos < 0 or flat_pos >= len(self._flat):
            return None

        # Gather reference words
        num_ref_words = len(trans_words) + 5
        end_pos = min(len(self._flat), flat_pos + num_ref_words)

        ref_data = []
        for i in range(flat_pos, end_pos):
            _, w, s_id, a_id, w_idx = self._flat[i]
            ref_data.append((w, s_id, a_id, w_idx))

        if not ref_data:
            return None

        # The reference words just before this match. The model echoes words
        # it has already heard at a window tail, and the echo is usually of
        # text that sits *before* where the window aligned — so the window's
        # own reference is not enough to recognise one.
        preceding = [
            self._flat[i][0] for i in range(max(0, flat_pos - ECHO_LOOKBACK), flat_pos)
        ]
        words = self._compare_words(trans_words, ref_data, preceding)

        # Prepend skipped reference words, oldest first
        if gap_start is not None and gap_start < flat_pos:
            gap_words = []
            for i in range(gap_start, flat_pos):
                _, w, s_id, a_id, w_idx = self._flat[i]
                gap_words.append(WordResult(
                    recited="",
                    reference=w,
                    is_correct=False,
                    surah_id=s_id,
                    ayah_id=a_id,
                    reference_index=w_idx,
                    is_gap_filled=True,
                ))
            words = gap_words + words

        _, _, start_s_id, start_a_id, start_offset = self._flat[flat_pos]
        surah = self._surahs[start_s_id - 1]

        return VerseMatch(
            surah_id=start_s_id,
            surah_name=surah["name"],
            ayah_id=start_a_id,
            start_offset=start_offset,
            words=words,
        )

    def find_and_compare(self, transcription: str, context_surah: int | None = None, context_ayah: int | None = None, context_word_index: int | None = None) -> VerseMatch | None:
        """Legacy method — kept for backward compatibility.

        New code should use discover() or track() directly.
        """
        if context_surah is not None and context_ayah is not None and context_word_index is not None:
            return self.track(transcription, context_surah, context_ayah, context_word_index)
        return self.discover(transcription)

    def _compare_words(
        self, trans_words: list[str], ref_data: list[tuple[str, int, int, int]],
        preceding: list[str] | None = None,
    ) -> list[WordResult]:
        """Word-by-word comparison using diacritics.

        Uses SequenceMatcher on normalized words for alignment,
        then compares original words with diacritics for correctness.
        Trailing reference-only words are ignored so stopping mid-ayah
        is not treated as a mistake.
        """
        ref_words = [x[0] for x in ref_data]
        trans_norm = [normalize(w) for w in trans_words]
        ref_norm = [normalize(w) for w in ref_words]

        matcher = difflib.SequenceMatcher(None, trans_norm, ref_norm)
        results: list[WordResult] = []
        opcodes = _charge_lone_resumes(
            list(matcher.get_opcodes()), SKIP_RESUME_WORDS)

        for op, i1, i2, j1, j2 in opcodes:
            if op == "equal":
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    # Normalized match — now check with diacritics
                    correct = self._diacritics_match(trans_words[i], ref_words[j])
                    results.append(WordResult(
                        recited=trans_words[i],
                        reference=ref_words[j],
                        is_correct=correct,
                        surah_id=ref_data[j][1],
                        ayah_id=ref_data[j][2],
                        reference_index=ref_data[j][3],
                    ))
            elif op == "replace":
                # One heard token spanning two reference words, or two heard
                # tokens spanning one — a word *boundary* the model put in
                # the wrong place, not a word it got wrong.
                #
                # The reciter says أَوْ دَيْنٍ and Whisper writes أَوْدَيْنٍ as a
                # single token. Paired off positionally that token is charged
                # against أَوْ, and دَيْنٍ against whatever follows, so one
                # misplaced space costs two red words on a page where the
                # reciter made no mistake at all. Measured on a real-
                # conditions recording of 4:12, this happened three times in
                # seventeen false alarms.
                #
                # Only an *exact* match on the joined skeleton is accepted.
                # أَوْدَيْنٍ can be read as أَوْ + دَيْنٍ and as nothing else, so
                # this forgives no letter and weakens no verdict — unlike a
                # distance threshold, which would also forgive فَلَهُمْ for
                # وَلَهُمْ. A merge is a fact about spacing; a substitution is
                # a fact about letters.
                span = _boundary_span(trans_norm, ref_norm, i1, i2, j1, j2)
                if span is not None:
                    ti_hi, tj_hi = span
                    heard = "".join(trans_words[i1:ti_hi])
                    whole = "".join(ref_words[j1:tj_hi])
                    correct = self._diacritics_match(heard, whole)
                    for j in range(j1, tj_hi):
                        results.append(WordResult(
                            recited=trans_words[i1] if i2 - i1 == 1 else heard,
                            reference=ref_words[j],
                            is_correct=correct,
                            surah_id=ref_data[j][1],
                            ayah_id=ref_data[j][2],
                            reference_index=ref_data[j][3],
                        ))
                    if ti_hi >= i2 and tj_hi >= j2:
                        continue
                    i1, j1 = ti_hi, tj_hi

                # Words already heard earlier in this same window. The model
                # echoes them at a window tail — 23:7 ends الْعَادُونَ and the
                # next window came back "...الْعَادُونَ فَمَنِ ابْتَغَى", which is
                # 23:7's *own opening* repeated. Paired off positionally that
                # echo became "وَالَّذِينَ recited as فَمَنِ" and "هُمْ recited as
                # ابْتَغَى": two words of 23:8 painted red, the pointer dragged
                # to 23:8:1, and the ayah's remaining three words never scored.
                # A word we have already heard is not evidence about the word
                # it happens to line up against.
                echoed = set(ref_norm[:j1]) | set(preceding or ())

                for k in range(max(i2 - i1, j2 - j1)):
                    ti = i1 + k if i1 + k < i2 else None
                    tj = j1 + k if j1 + k < j2 else None
                    if (ti is not None and tj is not None
                            and trans_norm[ti] in echoed):
                        # Report the reference word as not heard rather than
                        # as recited wrongly: amber, and the pointer stays put.
                        ti = None
                    results.append(WordResult(
                        recited=trans_words[ti] if ti is not None else "",
                        reference=ref_words[tj] if tj is not None else "",
                        is_correct=False,
                        surah_id=ref_data[tj][1] if tj is not None else None,
                        ayah_id=ref_data[tj][2] if tj is not None else None,
                        reference_index=ref_data[tj][3] if tj is not None else None,
                    ))
            elif op == "delete":
                # Extra words the user said (not in Quran)
                for i in range(i1, i2):
                    results.append(WordResult(
                        recited=trans_words[i],
                        reference="",
                        is_correct=False,
                        surah_id=None,
                        ayah_id=None,
                        reference_index=None,
                    ))
            elif op == "insert":
                # Words the user missed
                for j in range(j1, j2):
                    results.append(WordResult(
                        recited="",
                        reference=ref_words[j],
                        is_correct=False,
                        surah_id=ref_data[j][1],
                        ayah_id=ref_data[j][2],
                        reference_index=ref_data[j][3],
                    ))

        # Remove trailing missed words (reference words with no recited counterpart).
        # We only care about words the user actually recited; if they stop, we shouldn't
        # mark the rest of the buffer as "missed", since they'll say them in the next chunk.
        while results and not results[-1].recited:
            results.pop()

        return results

    def _diacritics_match(self, recited: str, reference: str) -> bool:
        """Check if two words match including diacritics.

        Normalizes Uthmani-specific characters before comparison so that
        Imla'i output from Whisper can match Uthmani reference text.
        """
        # Strip Uthmani-only marks that Whisper won't produce
        # but keep standard tashkeel (fathah, dammah, kasrah, etc.)
        r = self._to_imlai(recited)
        q = self._to_imlai(reference)
        return r == q

    @staticmethod
    def _to_imlai(word: str) -> str:
        """Convert an Uthmani word to a form comparable with ASR output.

        Order matters here. Shadda and sukun are discarded either way, but
        they sit *between* the characters the later rules try to match — the
        reference spelling of لَّا is lam + fathah + shadda + alef, so a rule
        looking for "fathah followed by alef" silently fails to fire while the
        shadda is still in place. Everything that carries no vowel information
        is therefore removed first, and the sequence rules run on a string
        where the vowels really are adjacent.
        """
        import unicodedata

        t = word

        # ── 1. Letters that are the same letter written differently ──────
        t = t.replace("\u0671", "\u0627")  # ٱ alef wasla  → ا
        t = t.replace("\u0622", "\u0627")  # آ alef maddah → ا
        t = t.replace("\u0623", "\u0627")  # أ → ا  (Whisper places the
        t = t.replace("\u0625", "\u0627")  # إ → ا   hamza seat unreliably)
        t = t.replace("\u0640", "")         # tatweel

        # ── 2. Marks that carry no vowel information ─────────────────────
        # Dropped now rather than at the end, so the rules in step 3 see
        # adjacent characters instead of ones separated by a shadda.
        t = t.replace("\u06E1", "")          # ۡ Uthmani sukun
        t = t.replace("\u0652", "")          # ْ sukun
        t = t.replace("\u0651", "")          # ّ shadda
        t = t.replace("\u0653", "")          # ٓ maddah
        t = t.replace("\u0670", "")          # ٰ dagger alef
        t = re.sub("[\u06D6-\u06E0\u06E2-\u06ED]", "", t)  # recitation marks
        t = unicodedata.normalize("NFC", t)

        # ── 3. Spelling equivalences, on adjacent characters ─────────────
        # Uthmani tanween variants → the standard forms Whisper emits
        t = t.replace("\u0656", "\u064D")   # ٖ → ٍ
        t = t.replace("\u0657", "\u064B")   # ٗ → ً
        t = t.replace("\u065E", "\u064C")   # ٞ → ٌ

        t = t.replace("\u0621\u064E\u0627", "\u0627")   # ءَا → ا
        # أَأَنذَرْتَهُمْ and أَنذَرْتَهُمْ are the same word written two ways
        t = re.sub("\u0627[\u064B-\u0650]*\u0627", "\u0627", t)
        # Long alef after a fathah is the same sound as the fathah alone
        t = re.sub("\u064E\u0627", "\u064E", t)

        # ── 4. Short vowels are the model's guess, not a measurement ─────
        # Whisper emits text; the tashkeel on it is largely produced by its
        # language model rather than heard, and it is not stable — بَشِيرًا came
        # back as بِشِيرًا from eight windows out of eight, the letters right
        # every time. Painting a word red over a vowel mark is grading the
        # model's spelling, not the recitation.
        #
        # This also settles waqf for free: stopping on a word drops its case
        # ending and continuing pronounces it, both correct, and which one is
        # heard depends only on where the audio window cut.
        #
        # Tanween stays — it is a letter's worth of sound, not a vowel mark,
        # and dropping it would hide a real ending error.
        t = re.sub("[\u064E\u064F\u0650]", "", t)

        return t
