"""Stylesheet and theming constants.

Two surfaces, and they are deliberately different. The Mushaf is a *sheet* —
a cream page on a neutral backdrop, with a border and a soft shadow, because
that is what the reciter is looking at and it should read as paper. The bar
under it is application chrome: flat, quiet, and the same white as the window.

Anything painted on top of the page (the mask that hides an unrecited word)
must use PAGE_BG, not white — the two are no longer the same colour, and a
white mask on a cream page is a visible rectangle.
"""

from PyQt6.QtGui import QFont

# ── Chrome ─────────────────────────────────────────────────────────────
BG_PRIMARY = "#FFFFFF"       # window and bar
BG_SURFACE = "#F9FAFB"       # soft gray surface
BORDER = "#E4E4E7"           # subtle 1px borders
BORDER_STRONG = "#D4D4D8"    # page edge, where a hairline would disappear
HOVER_BG = "#F4F4F5"         # button hover wash
PRESSED_BG = "#E4E4E7"       # button pressed wash
TEXT_PRIMARY = "#18181B"     # near-black: pure #000 reads as harsh on paper
TEXT_SECONDARY = "#71717A"   # muted gray text
TEXT_MUTED = "#A1A1AA"       # disabled / placeholder

ACCENT = "#0F9D76"           # deep emerald — the app's one colour
ACCENT_HOVER = "#0B8564"
ACCENT_PRESSED = "#096B51"
RECORD_ACTIVE = ACCENT
RECORD_HOVER = ACCENT_HOVER
RECORD_ERROR = "#DC2626"

# ── The page ───────────────────────────────────────────────────────────
PAGE_BACKDROP = "#E8E6E1"    # what the sheet sits on
PAGE_BG = "#FDFCF8"          # the sheet itself — warm, barely off-white
PAGE_BORDER = "#DAD6CC"      # the sheet's edge
AYAH_MARK_COLOR = "#A98B4F"  # muted gold, as ayah markers are printed
BANNER_BG = "#F7F2E6"        # inside the surah-title frame

# ── Quran verification colors ─────────────────────────────────────────
# There is no colour for correct recitation, and that is deliberate: a word
# recited correctly is uncovered and left in the Mushaf's own ink. Only the
# exceptions are coloured, so the two words worth looking at are not hidden
# inside a page-wide wash of the colour that means "fine".
INCORRECT_COLOR = "#DC2626"  # red — wrong word
MISSED_COLOR = "#E0921A"     # amber — not sure, or skipped
VERSE_REF_COLOR = "#9CA3AF"  # light gray — surah:ayah label
CORRECT_COLOR = ACCENT       # kept for the unused HTML transcript only

# How strongly a verdict tints the glyph underneath it. The glyph has to stay
# readable through the colour: this is a highlighter, not a fill.
TINT_ALPHA = 80


STYLESHEET = f"""
    QMainWindow {{
        background-color: {BG_PRIMARY};
    }}

    QWidget#central {{
        background-color: {BG_PRIMARY};
    }}

    /* The bar. Square, full width, and part of the layout — so the page
       above it is laid out in the space that is actually left, which is the
       whole point of it not being a floating pill any more. */
    QWidget#bar {{
        background-color: {BG_PRIMARY};
        border-top: 1px solid {BORDER};
    }}

    QWidget#bar_zone {{
        background-color: transparent;
    }}

    /* Live state, at the end of the bar. Muted by default: it is there to
       be glanced at, not read. */
    QLabel#status {{
        color: {TEXT_SECONDARY};
        font-size: 12px;
        font-weight: 500;
        background-color: transparent;
        border: none;
    }}

    QLabel#status[state="recording"] {{
        color: {TEXT_PRIMARY};
    }}

    QLabel#status[state="transcribing"] {{
        color: {TEXT_SECONDARY};
    }}

    QLabel#status[state="error"] {{
        color: {RECORD_ERROR};
    }}

    /* The settings popup. The only menu in the app, and the only place a
       mid-session decision lives. */
    QMenu {{
        background-color: {BG_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 12px;
        padding: 6px;
    }}

    QMenu::item {{
        padding: 7px 28px 7px 14px;
        border-radius: 8px;
        color: {TEXT_PRIMARY};
    }}

    QMenu::item:selected {{
        background-color: {HOVER_BG};
    }}

    QMenu::item:disabled {{
        color: {TEXT_MUTED};
    }}

    QMenu::item:checked {{
        font-weight: 600;
    }}

    QMenu::separator {{
        height: 1px;
        background-color: {BORDER};
        margin: 6px 10px;
    }}

    QToolTip {{
        background-color: {TEXT_PRIMARY};
        color: {BG_PRIMARY};
        border: none;
        border-radius: 6px;
        padding: 5px 8px;
        font-size: 12px;
    }}
"""

# ── Fonts ──────────────────────────────────────────────────────────────

# General UI (English): 13px
UI_FONT_FAMILIES = [
    "Inter",
    "Roboto",
    "Segoe UI",          # Windows
    "Ubuntu",            # Ubuntu Linux
    "Noto Sans",         # Linux fallback
    "sans-serif",
]
UI_FONT_SIZE = 10  # pt ≈ 13px

# Transcription area (Arabic): 20px
ARABIC_FONT_FAMILIES = [
    "Cairo",
    "Tajawal",
    "Noto Sans Arabic",
    "Segoe UI",
    "sans-serif",
]
ARABIC_FONT_SIZE = 15  # pt ≈ 20px


def app_font() -> QFont:
    """Return the application-wide UI font."""
    font = QFont()
    font.setFamilies(UI_FONT_FAMILIES)
    font.setPointSize(UI_FONT_SIZE)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


def arabic_font() -> QFont:
    """Return the font for the Arabic transcription area."""
    font = QFont()
    font.setFamilies(ARABIC_FONT_FAMILIES)
    font.setPointSize(ARABIC_FONT_SIZE)
    return font
