"""Arabic text utilities — diacritics stripping and normalization.

Two levels of normalization:
  1. strip_diacritics() — removes tashkeel only (for fuzzy search)
  2. normalize()        — strips diacritics + normalizes Uthmani↔Imla'i differences
"""

import re

# ── Arabic diacritics (tashkeel) ───────────────────────────────────────
# Standard diacritics
_FATHAH = "\u064E"
_DAMMAH = "\u064F"
_KASRAH = "\u0650"
_SHADDA = "\u0651"
_SUKUN = "\u0652"
_FATHATAN = "\u064B"
_DAMMATAN = "\u064C"
_KASRATAN = "\u064D"
_SUPERSCRIPT_ALEF = "\u0670"   # ٰ  (Uthmani dagger alef)
_MADDAH = "\u0653"
_HAMZA_ABOVE = "\u0654"
_HAMZA_BELOW = "\u0655"

# Uthmani-specific marks
_SMALL_HIGH_SEEN = "\u06DC"
_SMALL_HIGH_ROUNDED_ZERO = "\u06DF"
_SMALL_HIGH_UPRIGHT_RECT = "\u06E0"
_SMALL_HIGH_DOTLESS_HEAD_KHAH = "\u06E1"  # ۡ (Uthmani sukun)
_SMALL_HIGH_MEEM_INIT = "\u06E2"
_SMALL_LOW_SEEN = "\u06E3"
_SMALL_WAW = "\u06E5"
_SMALL_YEH = "\u06E6"
_SMALL_HIGH_NOON = "\u06E8"
_EMPTY_CENTRE_LOW_STOP = "\u06EA"
_EMPTY_CENTRE_HIGH_STOP = "\u06EB"
_ROUNDED_HIGH_STOP_WITH_FILLED_CENTRE = "\u06EC"
_SMALL_LOW_MEEM = "\u06ED"

# Quranic stop signs / ornaments
_RUB_EL_HIZB = "\u06DE"        # ۞
_SAJDAH = "\u06E9"
_END_OF_AYAH = "\u06DD"

# Compile all diacritical marks to strip
_DIACRITICS = re.compile(
    "["
    "\u064B-\u0655"      # standard tashkeel range
    "\u0670"             # superscript alef
    "\u06D6-\u06ED"      # Quranic annotation marks
    "\u0610-\u061A"      # more combining marks
    "\u08D3-\u08E1"      # extended Arabic marks
    "\u08E3-\u08FF"      # more extended marks
    "\uFE70-\uFE7F"      # presentation forms
    "]"
)

# ── Alef normalization ─────────────────────────────────────────────────
_ALEF_WASLA = "\u0671"   # ٱ
_ALEF = "\u0627"          # ا
_ALEF_MADDA = "\u0622"    # آ
_ALEF_HAMZA_ABOVE = "\u0623"  # أ
_ALEF_HAMZA_BELOW = "\u0625"  # إ

_TATWEEL = "\u0640"       # ـ (kashida)


def strip_diacritics(text: str) -> str:
    """Remove all Arabic diacritical marks (tashkeel + Quranic marks)."""
    return _DIACRITICS.sub("", text)


def normalize(text: str) -> str:
    """Normalize Arabic text for fuzzy matching.

    - Strips all diacritics
    - Normalizes alef variants (أ إ آ ٱ → ا)
    - Normalizes a few common orthographic variants
    - Removes tatweel (kashida)
    - Removes Quranic ornaments (۞ etc.)
    """
    t = strip_diacritics(text)
    t = t.replace(_ALEF_WASLA, _ALEF)
    t = t.replace(_ALEF_MADDA, _ALEF)
    t = t.replace(_ALEF_HAMZA_ABOVE, _ALEF)
    t = t.replace(_ALEF_HAMZA_BELOW, _ALEF)
    t = t.replace(_TATWEEL, "")
    t = t.replace(_RUB_EL_HIZB, "")
    t = t.replace(_END_OF_AYAH, "")
    # أَأَنذَرْتَهُمْ collapses to the same skeleton as أَنذَرْتَهُمْ. This has to
    # happen here as well as in the diacritic comparison, or the two spellings
    # never get aligned to each other and the word is scored as a substitution.
    t = re.sub(_ALEF + "{2,}", _ALEF, t)
    # Normalize teh marbuta to heh for matching
    t = t.replace("\u0629", "\u0647")  # ة → ه
    t = t.replace("\u0649", "\u064A")  # ى → ي
    return t.strip()


# ── Word segmentation ──────────────────────────────────────────────────
# quran.json splits on spaces, but the QCF Mushaf data uses its own word
# positions.  Two systematic differences make the two disagree:
#   1. Standalone waqf/ornament marks (ۛ ۖ ۗ …) are separate tokens in
#      quran.json but are not words on the page.
#   2. The vocative يَا / وَيَا is written joined to the following word in
#      Uthmani script, so the Mushaf counts it as one word, not two.
# Aligning them here keeps the search index word positions identical to the
# QCF `position` field, which is what the Mushaf highlighting is keyed on.

_JOIN_PREFIXES = {"يا", "ويا", "ها"}


def split_words(text: str) -> list[str]:
    """Split Arabic text into words aligned with QCF Mushaf word positions.

    Drops tokens that carry no letters (standalone waqf marks) and joins
    the vocative particle to the word it precedes.

    Used for both the reference verses and the transcription, so the two
    tokenize identically — Whisper writes "يا ايها" as two tokens where the
    Mushaf has one word.
    """
    tokens = [w for w in text.split() if normalize(w)]

    words: list[str] = []
    i = 0
    while i < len(tokens):
        if normalize(tokens[i]) in _JOIN_PREFIXES and i + 1 < len(tokens):
            words.append(tokens[i] + tokens[i + 1])
            i += 2
        else:
            words.append(tokens[i])
            i += 1
    return words
