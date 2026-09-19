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

    # The two-word floor, except for an ayah that *is* one word.
    #
    # `discover()` has had a solo-ayah path since Tier 1.5, with a comment
    # saying why: "hold out for a second word and يس or وَالْعَصْرِ can never be
    # found at all, only stepped over". It was unreachable. This function
    # refused the chunk first, so the path below it never ran — from the app
    # or from the benchmark — and DISCOVERY_SOLO_AYAH was a setting connected
    # to nothing. The unit tests missed it because they call discover()
    # directly, which is precisely the seam this file exists to cover.
    #
    # Found by Tier G: 23 of 30 muqatta'at cases scored LOST with zero words
    # shown, and the logs said "SKIPPED (only 1 word(s), need 2)" 111 times.
    if len(words) < DISCOVERY_MIN_WORDS:
        solo = len(words) == 1 and quran_index.is_solo_ayah(words[0])
        if not solo:
            return MatchDecision(
                None, False, "discovery",
                f"only {len(words)} word(s), need {DISCOVERY_MIN_WORDS}",
            )
    return MatchDecision(quran_index.discover(text), True, "discovery")
