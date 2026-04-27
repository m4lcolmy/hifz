"""QCF4 font data loader — parses page JSONs and manages font registration.

Each Mushaf page has a dedicated QCF4 font where every word is a single
pre-drawn glyph accessed via its Unicode codepoint.  This module loads
the per-page JSON metadata, registers TTF fonts with Qt, and exposes
structured data for the rendering layer.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtGui import QFont, QFontDatabase

from src.config import QCF_FONTS_DIR, QCF_PAGES_DIR, QCF_FONT_SIZE


# ── Data classes ───────────────────────────────────────────────────────

@dataclass
class QCFWord:
    """A single renderable word/glyph on a Mushaf page."""
    code: int               # Unicode codepoint (e.g. 61696 → chr(61696))
    font_name: str          # Qt family name (e.g. "QCF4_Hafs_01_W")
    text: str               # Arabic text for display/matching
    word_type: str          # "word", "end", "surah_header", "bismillah"
    verse_key: str | None   # "surah:ayah" format, e.g. "1:1"
    position: int | None    # 1-based word position within the ayah
    line: int               # line number on the page (1-based)


@dataclass
class QCFPage:
    """Parsed data for a single Mushaf page."""
    page_num: int
    font_name: str                  # primary page font family
    surahs: list[dict]              # [{id, name, name_arabic, verse_start, verse_end}]
    lines: list[list[QCFWord]]      # lines[0] = list of words on line 1


# ── Font name mapping ─────────────────────────────────────────────────

# QCF JSON uses short names like "QCF4_Hafs_01"; TTF files are named
# "QCF4_Hafs_01_W.ttf" and register as "QCF4_Hafs_01_W" in Qt.
# QBSML font is used for surah headers and bismillah.

def _json_font_to_ttf_stem(json_font: str) -> str:
    """Map JSON font name → TTF file stem (without .ttf extension)."""
    if json_font == "QCF4_QBSML":
        return "QCF4_QBSML"
    # "QCF4_Hafs_01" → "QCF4_Hafs_01_W"
    return json_font + "_W"


# ── Loader ─────────────────────────────────────────────────────────────

class QCFDataLoader:
    """Loads QCF page JSONs and manages Qt font registration.

    Fonts are registered lazily on first use and cached for the session.
    Page data is parsed fresh each call (pages are small ~30KB JSON).
    """

    def __init__(self):
        # font_stem → Qt font family name (after registration)
        self._font_families: dict[str, str] = {}
        # font_stem → QFont object cache
        self._font_cache: dict[str, QFont] = {}

    def load_page(self, page_num: int) -> QCFPage | None:
        """Load and parse a QCF page JSON file."""
        path = QCF_PAGES_DIR / f"{page_num:03d}.json"
        if not path.exists():
            print(f"QCF page not found: {path}")
            return None

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        page_font_stem = _json_font_to_ttf_stem(raw["font"])
        self._ensure_font(page_font_stem)

        lines: list[list[QCFWord]] = []

        for line_data in raw.get("lines", []):
            line_num = line_data["line"]
            words: list[QCFWord] = []

            for w in line_data.get("words", []):
                font_stem = _json_font_to_ttf_stem(w["font"])
                self._ensure_font(font_stem)

                # Get Qt family name for this font
                family = self._font_families.get(font_stem, font_stem)

                words.append(QCFWord(
                    code=w["code"],
                    font_name=family,
                    text=w.get("text", ""),
                    word_type=w.get("type", "word"),
                    verse_key=w.get("verse_key"),
                    position=w.get("position"),
                    line=line_num,
                ))

            lines.append(words)

        return QCFPage(
            page_num=page_num,
            font_name=self._font_families.get(page_font_stem, page_font_stem),
            surahs=raw.get("surahs", []),
            lines=lines,
        )

    def get_font(self, family_name: str) -> QFont:
        """Return a cached QFont for the given Qt family name."""
        if family_name not in self._font_cache:
            font = QFont(family_name)
            font.setPointSize(QCF_FONT_SIZE)
            # Disable kerning to avoid "bearing" calculation warnings
            font.setKerning(False)
            font.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
            self._font_cache[family_name] = font
        return self._font_cache[family_name]

    def _ensure_font(self, font_stem: str):
        """Register a TTF font file with Qt if not already registered."""
        if font_stem in self._font_families:
            return

        ttf_path = QCF_FONTS_DIR / f"{font_stem}.ttf"
        if not ttf_path.exists():
            print(f"Warning: QCF font not found: {ttf_path}")
            self._font_families[font_stem] = font_stem
            return

        font_id = QFontDatabase.addApplicationFont(str(ttf_path))
        if font_id == -1:
            print(f"Warning: Failed to load font: {ttf_path}")
            self._font_families[font_stem] = font_stem
            return

        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            self._font_families[font_stem] = families[0]
        else:
            self._font_families[font_stem] = font_stem
