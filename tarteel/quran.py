"""Quran text search and word-by-word comparison engine.

Loads the full Quran text and provides:
  - Free-mode verse detection from transcription
  - Word-by-word comparison with diacritics
"""

import json
import difflib
from dataclasses import dataclass
from pathlib import Path

from tarteel.arabic import normalize

# ── Data path ──────────────────────────────────────────────────────────
_DATA_DIR = Path(__file__).parent / "data"
_QURAN_JSON = _DATA_DIR / "quran.json"


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
    """Searchable index of the entire Quran text."""

    def __init__(self):
        self._surahs: list[dict] = []
        # Flat word list: [(normalized_word, original_word, surah_idx, ayah_idx, word_idx)]
        self._flat: list[tuple[str, str, int, int, int]] = []
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
                for word_idx, w in enumerate(words):
                    norm = normalize(w)
                    if norm:
                        self._flat.append((norm, w, s_id, a_id, word_idx))

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

        # Gather reference words across ayahs
        num_ref_words = len(trans_words) + 5
        end_pos = min(len(self._flat), best_pos + num_ref_words)
        
        ref_data = []
        for i in range(best_pos, end_pos):
            _, w, s_id, a_id, w_idx = self._flat[i]
            ref_data.append((w, s_id, a_id, w_idx))

        # Word-by-word comparison
        words = self._compare_words(trans_words, ref_data)

        # Base verse match info
        _, _, start_s_id, start_a_id, start_offset = self._flat[best_pos]
        surah = self._surahs[start_s_id - 1]

        return VerseMatch(
            surah_id=start_s_id,
            surah_name=surah["name"],
            ayah_id=start_a_id,
            start_offset=start_offset,
            words=words,
        )

    def _find_best_position(self, trans_norm: list[str], context_surah: int | None = None, context_ayah: int | None = None) -> int | None:
        """Find the position in the flat word list that best matches."""
        if not hasattr(self, '_word_index'):
            self._word_index = {}
            for i, (norm, _, _, _, _) in enumerate(self._flat):
                self._word_index.setdefault(norm, []).append(i)

        candidate_positions = set()
        
        # Pick the most "rare" words in the transcription to use as anchors
        word_rarity = []
        for i, tw in enumerate(trans_norm):
            if tw in self._word_index:
                word_rarity.append((len(self._word_index[tw]), i, tw))
        
        word_rarity.sort(key=lambda x: x[0])
        
        # Take the top 3 rarest words as anchors
        anchors = word_rarity[:3]
        
        for count, i, tw in anchors:
            for pos in self._word_index[tw]:
                start_pos = max(0, pos - i)
                candidate_positions.add(start_pos)
                candidate_positions.add(max(0, start_pos - 1))
                candidate_positions.add(max(0, start_pos - 2))
                candidate_positions.add(min(len(self._flat) - 1, start_pos + 1))
                candidate_positions.add(min(len(self._flat) - 1, start_pos + 2))

        if not candidate_positions:
            return None

        candidates = {}
        for pos in candidate_positions:
            end_pos = min(len(self._flat), pos + len(trans_norm) + 3)
            window = [x[0] for x in self._flat[pos:end_pos]]
            sm = difflib.SequenceMatcher(None, trans_norm, window)
            blocks = sm.get_matching_blocks()
            if blocks and blocks[0].size > 0:
                # blocks[0].b is the offset in the window where the match actually starts
                # blocks[0].a is the offset in trans_norm where the match starts
                # If the user skipped the first word (a > 0), the actual start in the Quran should correspond to where trans_norm[0] WOULD be.
                # So actual_start = pos + blocks[0].b - blocks[0].a
                actual_start = pos + blocks[0].b - blocks[0].a
                actual_start = max(0, actual_start)
                
                match_count = sum(b.size for b in blocks)
                if actual_start not in candidates or match_count > candidates[actual_start]:
                    candidates[actual_start] = match_count

        candidates_list = []
        for pos, score in candidates.items():
            if score >= min(2.0, len(trans_norm) * 0.4):
                candidates_list.append((pos, score))

        if not candidates_list:
            return None

        # Prioritize candidates near context
        if context_surah is not None and context_ayah is not None:
            best_candidate = None
            best_score = -1
            
            for pos, score in candidates_list:
                _, _, s_id, a_id, _ = self._flat[pos]
                
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
        candidates_list.sort(key=lambda x: x[1], reverse=True)
        return candidates_list[0][0]

    def _score_match(self, trans_norm: list[str], start_pos: int) -> float:
        """Score using SequenceMatcher for robust alignment (handles skipped/extra words)."""
        end_pos = min(len(self._flat), start_pos + len(trans_norm) + 3)
        window = [x[0] for x in self._flat[start_pos:end_pos]]
        sm = difflib.SequenceMatcher(None, trans_norm, window)
        return sum(block.size for block in sm.get_matching_blocks())

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
        
        # Strip Shadda and Sukun to ignore Tajweed-specific orthography differences
        # Whisper often misses Shaddas or hallucinates Sukuns, and Tajweed rules 
        # add/remove them dynamically. We still verify the core vowels.
        t = t.replace("\u0651", "") # Shadda
        t = t.replace("\u0652", "") # Sukun
        
        return t
