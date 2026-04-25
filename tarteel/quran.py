"""Quran text search and word-by-word comparison engine.

Loads the full Quran text (Uthmani script with diacritics) and provides:
  - Free-mode verse detection from transcription
  - Word-by-word comparison with diacritics
"""

import json
import difflib
import os
from dataclasses import dataclass
from pathlib import Path

from tarteel.arabic import normalize, strip_diacritics

# ── Data path ──────────────────────────────────────────────────────────
_DATA_DIR = Path(__file__).parent / "data"
_QURAN_JSON = _DATA_DIR / "quran.json"


@dataclass
class WordResult:
    """Result for a single word comparison."""
    recited: str          # what the user said
    reference: str        # what the Quran says (may be empty for extra words)
    is_correct: bool      # True = exact match including diacritics


@dataclass
class VerseMatch:
    """A matched verse with word-by-word comparison results."""
    surah_id: int
    surah_name: str
    ayah_id: int
    start_offset: int     # Word index where the chunk started matching
    words: list[WordResult]


class QuranIndex:
    """Searchable index of the entire Quran text."""

    def __init__(self):
        self._surahs: list[dict] = []
        # Flat word list: [(normalized_word, original_word, surah_idx, ayah_idx)]
        self._flat: list[tuple[str, str, int, int]] = []
        # N-gram index: normalized bigram → list of positions in _flat
        self._bigrams: dict[tuple[str, str], list[int]] = {}

        self._load()

    def _load(self):
        """Load Quran JSON and build search index."""
        with open(_QURAN_JSON, encoding="utf-8") as f:
            self._surahs = json.load(f)

        # Build flat word list and bigram index
        for surah in self._surahs:
            s_id = surah["id"]
            for verse in surah["verses"]:
                a_id = verse["id"]
                words = verse["text"].split()
                for w in words:
                    norm = normalize(w)
                    if norm:
                        self._flat.append((norm, w, s_id, a_id))

        # Build bigram index for fast lookup
        for i in range(len(self._flat) - 1):
            if self._flat[i][2] == self._flat[i + 1][2]:  # same surah
                key = (self._flat[i][0], self._flat[i + 1][0])
                if key not in self._bigrams:
                    self._bigrams[key] = []
                self._bigrams[key].append(i)

    def find_and_compare(self, transcription: str, context_surah: int | None = None, context_ayah: int | None = None) -> VerseMatch | None:
        """Find the best matching verse and compare word-by-word.

        Returns None if no match is found.
        """
        trans_words = transcription.split()
        if len(trans_words) < 1:
            return None

        trans_norm = [normalize(w) for w in trans_words]
        trans_norm = [w for w in trans_norm if w]
        if len(trans_norm) < 1:
            return None

        # Find candidate starting positions using bigram lookup
        best_pos = self._find_best_position(trans_norm, context_surah, context_ayah)
        if best_pos is None:
            return None

        # Get the matched verse
        _, _, s_id, a_id = self._flat[best_pos]
        surah = self._surahs[s_id - 1]
        verse = surah["verses"][a_id - 1]

        # Determine where in the verse the recitation starts
        ref_words_full = verse["text"].split()
        ref_norm_full = [normalize(w) for w in ref_words_full]

        # Find the starting offset within the verse
        start_offset = 0
        if trans_norm and ref_norm_full:
            for i, rn in enumerate(ref_norm_full):
                if rn == trans_norm[0]:
                    start_offset = i
                    break

        # Trim ref to only the portion the user recited (+1 for tolerance)
        end_offset = min(start_offset + len(trans_words) + 1, len(ref_words_full))
        ref_words = ref_words_full[start_offset:end_offset]

        # Word-by-word comparison
        words = self._compare_words(trans_words, ref_words)

        return VerseMatch(
            surah_id=s_id,
            surah_name=surah["name"],
            ayah_id=a_id,
            start_offset=start_offset,
            words=words,
        )

    def _find_best_position(self, trans_norm: list[str], context_surah: int | None = None, context_ayah: int | None = None) -> int | None:
        """Find the position in the flat word list that best matches."""
        candidates: list[tuple[int, int]] = []  # (position, score)

        # Try bigram lookup with first words
        for start in range(min(3, len(trans_norm) - 1)):
            key = (trans_norm[start], trans_norm[start + 1])
            if key in self._bigrams:
                for pos in self._bigrams[key]:
                    adjusted = pos - start
                    if adjusted >= 0:
                        score = self._score_match(trans_norm, adjusted)
                        candidates.append((adjusted, score))

        # If no bigram match, try single-word fallback
        if not candidates:
            first = trans_norm[0]
            for i, (norm, _, _, _) in enumerate(self._flat):
                if norm == first:
                    score = self._score_match(trans_norm, i)
                    if score >= 2:
                        candidates.append((i, score))

        if not candidates:
            return None

        # Prioritize candidates near context
        if context_surah is not None and context_ayah is not None:
            best_candidate = None
            best_score = -1
            
            for pos, score in candidates:
                _, _, s_id, a_id = self._flat[pos]
                
                is_near = False
                if s_id == context_surah and context_ayah <= a_id <= context_ayah + 5:
                    is_near = True
                elif s_id == context_surah + 1 and a_id <= 5:
                    is_near = True
                    
                boosted_score = score + (100 if is_near else 0)
                
                if boosted_score > best_score:
                    best_score = boosted_score
                    best_candidate = pos
                    
            if best_candidate is not None:
                return best_candidate

        # Default behavior: Return position with highest score
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    def _score_match(self, trans_norm: list[str], start_pos: int) -> int:
        """Score how many consecutive words match from start_pos."""
        score = 0
        for i, tw in enumerate(trans_norm):
            pos = start_pos + i
            if pos >= len(self._flat):
                break
            if self._flat[pos][0] == tw:
                score += 1
        return score

    def _compare_words(
        self, trans_words: list[str], ref_words: list[str]
    ) -> list[WordResult]:
        """Word-by-word comparison using diacritics.

        Uses SequenceMatcher on normalized words for alignment,
        then compares original words with diacritics for correctness.
        """
        trans_norm = [normalize(w) for w in trans_words]
        ref_norm = [normalize(w) for w in ref_words]

        matcher = difflib.SequenceMatcher(None, trans_norm, ref_norm)
        results: list[WordResult] = []

        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    # Normalized match — now check with diacritics
                    correct = self._diacritics_match(trans_words[i], ref_words[j])
                    results.append(WordResult(
                        recited=trans_words[i],
                        reference=ref_words[j],
                        is_correct=correct,
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
                    ))
            elif op == "delete":
                # Extra words the user said (not in Quran)
                for i in range(i1, i2):
                    results.append(WordResult(
                        recited=trans_words[i],
                        reference="",
                        is_correct=False,
                    ))
            elif op == "insert":
                # Words the user missed
                for j in range(j1, j2):
                    results.append(WordResult(
                        recited="",
                        reference=ref_words[j],
                        is_correct=False,
                    ))

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
        # This handles the Uthmani↔Imla'i difference where one uses
        # dagger alef and the other uses full alef for the same sound.
        t = re.sub("\u064E\u0627", "\u064E", t)  # فَا → فَ
        # Remove Quranic annotation marks that Whisper won't produce
        t = re.sub("[\u06D6-\u06E0\u06E2-\u06ED\u06DE]", "", t)
        # Remove tatweel
        t = t.replace("\u0640", "")
        # ۜ and similar
        t = re.sub("[\u06DC\u06DF]", "", t)
        # Canonicalize combining character order (fixes fathah/shadda ordering)
        t = unicodedata.normalize("NFC", t)
        return t

