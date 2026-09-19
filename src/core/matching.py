"""Mode-aware matching decision — shared by the live app and the benchmark.

The rule for which search runs (and whether one runs at all) lives here rather
than inside the Qt worker, so the offline benchmark exercises exactly the code
the app does.  If these two ever diverge, the benchmark stops meaning anything.
"""

from dataclasses import dataclass

from src.config import MATCH_MIN_WORDS, DISCOVERY_MIN_WORDS


@dataclass
class MatchDecision:
    """What the matcher did with one transcription."""
    match: object | None          # VerseMatch or None
    attempted: bool               # False when the chunk was too short to try
    mode: str                     # the mode the decision was made under
    skip_reason: str | None = None


def match_for_mode(quran_index, text: str, mode: str, context) -> MatchDecision:
    """Run the search appropriate to the current mode.

    `context` is (surah, ayah, word_index) — the pointer from the tracker.
    Tracking is only possible with a complete pointer; otherwise we fall back
    to discovery regardless of the requested mode.
    """
    if quran_index is None:
        return MatchDecision(None, False, mode, "no index")

    words = text.split()
    has_context = mode == "tracking" and all(c is not None for c in context)

    if has_context:
        if len(words) < MATCH_MIN_WORDS:
            return MatchDecision(
                None, False, "tracking",
                f"only {len(words)} word(s), need {MATCH_MIN_WORDS}",
            )
        return MatchDecision(
            quran_index.track(text, *context), True, "tracking"
        )

    if len(words) < DISCOVERY_MIN_WORDS:
        return MatchDecision(
            None, False, "discovery",
            f"only {len(words)} word(s), need {DISCOVERY_MIN_WORDS}",
        )
    return MatchDecision(quran_index.discover(text), True, "discovery")
