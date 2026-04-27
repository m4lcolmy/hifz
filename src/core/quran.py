"""Quran text search and word-by-word comparison engine.

Loads the full Quran text and provides:
  - Discovery mode: N-gram inverted index with uniqueness gating
  - Tracking mode: local pointer-locked word matching
  - Word-by-word comparison with diacritics
"""

import json
import difflib
from dataclasses import dataclass
from pathlib import Path

from src.core.arabic import normalize
from src.config import (
    QURAN_JSON, DISCOVERY_MIN_WORDS, DISCOVERY_NGRAM_SIZES, TRACKING_WINDOW,
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
                words = verse["text"].split()
                for word_idx, w in enumerate(words):
                    norm = normalize(w)
                    if norm:
                        pos = len(self._flat)
                        self._flat.append((norm, w, s_id, a_id, word_idx))
                        self._word_index.setdefault(norm, []).append(pos)

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
        trans_words = transcription.split()
        trans_norm = [normalize(w) for w in trans_words]
        trans_norm = [w for w in trans_norm if w]

        if len(trans_norm) < DISCOVERY_MIN_WORDS:
            return None

        # Try N-grams from largest to smallest, using the TAIL of transcription
        # (freshest/most recent words are most reliable)
        for n in DISCOVERY_NGRAM_SIZES:
            if len(trans_norm) < n:
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
        trans_words = transcription.split()
        if len(trans_words) < 1:
            return None

        trans_norm = [normalize(w) for w in trans_words]
        trans_norm = [w for w in trans_norm if w]
        if len(trans_norm) < 1:
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
        if match_count < min(2, len(trans_norm) * 0.4):
            return None

        # Compute best start position in flat array
        best_start = window_start + blocks[0].b - blocks[0].a
        best_start = max(0, best_start)

        return self._build_match(best_start, trans_words)

    def _find_context_pos(self, surah: int, ayah: int, word_index: int) -> int | None:
        """Find the flat index for a given surah/ayah/word position.

        Uses word_index to quickly narrow down, then linear scan.
        Returns the position of the NEXT expected word (context + 1).
        """
        # Get the first word of this ayah via word_index lookup
        for i in range(len(self._flat)):
            s, a, w = self._flat[i][2], self._flat[i][3], self._flat[i][4]
            if s == surah and a == ayah and w == word_index:
                return i + 1  # Next expected word
        return None

    # ── Shared helpers ─────────────────────────────────────────────────

    def _build_match(self, flat_pos: int, trans_words: list[str]) -> "VerseMatch | None":
        """Build a VerseMatch from a flat position and transcription words."""
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
        """Convert Uthmani word to comparable Imla'i form.

        Keeps standard diacritics but normalizes Uthmani-specific marks
        and canonicalizes combining character ordering via NFC.
        """
        import re
        import unicodedata

        t = word
        # ٱ (alef wasla) → ا
        t = t.replace("\u0671", "\u0627")  # ٱ → ا
        # آ (alef with maddah above) → ا
        t = t.replace("\u0622", "\u0627")  # آ → ا
        # ءَا (hamza + fathah + alef) -> ا
        t = t.replace("\u0621\u064e\u0627", "\u0627") # ءَا -> ا
        # ءٰ (hamza + dagger alef) -> ا
        t = t.replace("\u0621\u064e\u0670", "\u0627") # ءٰ -> ا 
        # Remove standalone maddah (may appear as combining mark)
        t = t.replace("\u0653", "")
        # ۡ (Uthmani small high dotless head of khah = sukun) → standard sukun
        t = t.replace("\u06E1", "\u0652")  # ۡ → ْ
        # ٰ (superscript alef / dagger alef) → remove
        t = t.replace("\u0670", "")
        
        # Map Uthmani sequential/tajweed tanween to standard Imla'i tanween
        t = t.replace("\u0656", "\u064D") # ٖ -> ٍ (Kasratan)
        t = t.replace("\u0657", "\u064B") # ٗ -> ً (Fathatan)
        t = t.replace("\u065E", "\u064C") # ٞ -> ٌ (Dammatan)
        
        # Remove silent/long alef after fathah (العَالَمِينَ → العَلَمِينَ)
        t = re.sub("\u064E\u0627", "\u064E", t)  # فَا → فَ
        # Remove Quranic annotation marks that Whisper won't produce
        t = re.sub("[\u06D6-\u06E0\u06E2-\u06ED\u06DE]", "", t)
        # Remove tatweel
        t = t.replace("\u0640", "")
        # ۜ and similar
        t = re.sub("[\u06DC\u06DF]", "", t)
        # Canonicalize combining character order (fixes fathah/shadda ordering)
        t = unicodedata.normalize("NFC", t)
        
        # Strip Shadda and Sukun to ignore Tajweed-specific orthography differences
        # Whisper often misses Shaddas or hallucinates Sukuns, and Tajweed rules 
        # add/remove them dynamically. We still verify the core vowels.
        t = t.replace("\u0651", "") # Shadda
        t = t.replace("\u0652", "") # Sukun
        
        return t
