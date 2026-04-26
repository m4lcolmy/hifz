"""Stylesheet and theming constants."""

from PyQt6.QtGui import QFont

# ── Color palette (Light Modern Theme) ────────────────────────────────────
BG_PRIMARY = "#FFFFFF"       # white background
BG_SURFACE = "#F9FAFB"       # soft gray for right panel
BORDER = "#E5E7EB"           # subtle 1px borders
TEXT_PRIMARY = "#000000"     # pure black text
TEXT_SECONDARY = "#6B7280"   # muted gray text
ACCENT = "#10B981"           # emerald green (from sketch)
ACCENT_HOVER = "#059669"     # green hover
ACCENT_PRESSED = "#047857"   # green pressed
RECORD_ACTIVE = "#10B981"    # emerald green for recording
RECORD_HOVER = "#059669"     # darker green hover
RECORD_ERROR = "#EF4444"     # red for error state

# ── Quran verification colors ─────────────────────────────────────────
CORRECT_COLOR = "#10B981"    # emerald green — correct recitation
INCORRECT_COLOR = "#EF4444"  # red — wrong word or diacritics
MISSED_COLOR = "#F59E0B"     # amber — word skipped by reciter
VERSE_REF_COLOR = "#9CA3AF"  # light gray — surah:ayah label


STYLESHEET = f"""
    QMainWindow {{
        background-color: {BG_PRIMARY};
    }}

    QWidget#central {{
        background-color: {BG_PRIMARY};
    }}

    QSplitter::handle {{
        background-color: {TEXT_PRIMARY};
        width: 2px;
    }}

    /* The Magic Pill */
    QWidget#pill {{
        background-color: {BG_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 24px;
    }}

    QLabel#status {{
        color: {TEXT_PRIMARY};
        font-size: 13px;
        font-weight: 500;
        padding-left: 8px;
        background-color: transparent;
        border: none;
    }}

    QLabel#status[state="recording"] {{
        color: {RECORD_ACTIVE};
    }}

    QLabel#status[state="transcribing"] {{
        color: {ACCENT};
    }}

    QLabel#status[state="error"] {{
        color: {RECORD_ERROR};
    }}

    QPushButton#record {{
        background-color: transparent;
        color: {TEXT_PRIMARY};
        font-size: 14px;
        font-weight: 700;
        border: 1.5px solid {TEXT_PRIMARY};
        border-radius: 18px;
        padding-top: 1px; /* Center the icon */
    }}

    QPushButton#record:hover {{
        background-color: #F3F4F6;
    }}

    QPushButton#record:pressed {{
        background-color: #E5E7EB;
    }}

    QPushButton#record:disabled {{
        color: {TEXT_SECONDARY};
        border-color: {TEXT_SECONDARY};
    }}

    QPushButton#record[recording="true"] {{
        color: white;
        background-color: {RECORD_ACTIVE};
        border-color: {RECORD_ACTIVE};
    }}

    QPushButton#record[recording="true"]:hover {{
        background-color: {RECORD_HOVER};
    }}

    QTextEdit#output {{
        background-color: {BG_PRIMARY};
        color: {TEXT_PRIMARY};
        border: none;
        padding: 32px;
        selection-background-color: #D1FAE5;
        selection-color: #065F46;
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
    return font


def arabic_font() -> QFont:
    """Return the font for the Arabic transcription area."""
    font = QFont()
    font.setFamilies(ARABIC_FONT_FAMILIES)
    font.setPointSize(ARABIC_FONT_SIZE)
    return font
