"""Stylesheet and theming constants."""

from PyQt6.QtGui import QFont

# ── Color palette (Zinc dark theme) ────────────────────────────────────
BG_PRIMARY = "#18181B"       # deep zinc background
BG_SURFACE = "#27272A"       # elements: text area, status bar
BORDER = "#3F3F46"           # subtle 1px borders
TEXT_PRIMARY = "#F4F4F5"     # off-white text
TEXT_SECONDARY = "#A1A1AA"   # muted zinc text
ACCENT = "#3B82F6"           # modern blue
ACCENT_HOVER = "#2563EB"    # blue hover
ACCENT_PRESSED = "#1D4ED8"  # blue pressed
RECORD_ACTIVE = "#EF4444"   # red for recording
RECORD_HOVER = "#DC2626"    # red hover

# ── Quran verification colors ─────────────────────────────────────────
CORRECT_COLOR = "#10B981"    # emerald green — correct recitation
INCORRECT_COLOR = "#EF4444"  # red — wrong word or diacritics
MISSED_COLOR = "#FBBF24"     # amber — word skipped by reciter
VERSE_REF_COLOR = "#6B7280"  # gray — surah:ayah label


STYLESHEET = f"""
    QMainWindow {{
        background-color: {BG_PRIMARY};
    }}

    QWidget#central {{
        background-color: {BG_PRIMARY};
    }}

    QLabel#title {{
        color: {TEXT_PRIMARY};
        font-size: 18px;
        font-weight: 600;
        padding: 4px 0;
    }}

    QLabel#status {{
        color: {TEXT_SECONDARY};
        font-size: 13px;
        padding: 8px 14px;
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER};
        border-radius: 6px;
    }}

    QLabel#status[state="recording"] {{
        color: {RECORD_ACTIVE};
        border-color: #7F1D1D;
        background-color: #1C1012;
    }}

    QLabel#status[state="transcribing"] {{
        color: {ACCENT};
        border-color: #1E3A5F;
        background-color: #111827;
    }}

    QLabel#status[state="error"] {{
        color: #F87171;
        border-color: #7F1D1D;
        background-color: #1C1012;
    }}

    QPushButton#record {{
        background-color: {ACCENT};
        color: white;
        font-size: 14px;
        font-weight: 600;
        padding: 12px 16px;
        border: none;
        border-radius: 8px;
        min-height: 20px;
    }}

    QPushButton#record:hover {{
        background-color: {ACCENT_HOVER};
    }}

    QPushButton#record:pressed {{
        background-color: {ACCENT_PRESSED};
    }}

    QPushButton#record:disabled {{
        background-color: {BG_SURFACE};
        color: {TEXT_SECONDARY};
        border: 1px solid {BORDER};
    }}

    QPushButton#record[recording="true"] {{
        background-color: {RECORD_ACTIVE};
    }}

    QPushButton#record[recording="true"]:hover {{
        background-color: {RECORD_HOVER};
    }}

    QTextEdit#output {{
        background-color: {BG_SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 14px;
        selection-background-color: #1E3A5F;
    }}

    QFrame#divider {{
        background-color: {BORDER};
        max-height: 1px;
        border: none;
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
