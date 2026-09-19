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

        # Word-by-word comparison
        words = self._compare_words(trans_words, ref_data)

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
        self, trans_words: list[str], ref_data: list[tuple[str, int, int, int]]
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
        opcodes = list(matcher.get_opcodes())

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
                # Pair up replaced words
                for k in range(max(i2 - i1, j2 - j1)):
                    ti = i1 + k if i1 + k < i2 else None
                    tj = j1 + k if j1 + k < j2 else None
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

        # ── 4. The ending depends on where the reciter stopped ───────────
        # Stopping on a word (waqf) drops its case ending; continuing (wasl)
        # pronounces it. Both are correct, and which one is heard depends on
        # where the audio window cut. Tanween stays: it is part of the word.
        t = re.sub("[\u064E\u064F\u0650]+$", "", t)

        return t
