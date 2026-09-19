"""Comprehensive search algorithm evaluation for QuranIndex.

Tests simulate REAL Whisper output — no diacritics, phonetic errors,
merged chunks, continuous cross-ayah recitation.

Run:
    conda run -n hifz python scripts/test_search_algorithm.py
"""

from dataclasses import dataclass
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.quran import QuranIndex


@dataclass(frozen=True)
class DiscoverCase:
    label: str
    text: str
    expect_surah: int
    expect_ayah: int

@dataclass(frozen=True)
class DiscoverFailCase:
    label: str
    text: str

@dataclass(frozen=True)
class TrackCase:
    label: str
    text: str
    ctx_surah: int
    ctx_ayah: int
    ctx_word: int
    expect_surah: int
    expect_ayah: int

@dataclass(frozen=True)
class GapFillCase:
    label: str
    text: str
    ctx_surah: int
    ctx_ayah: int
    ctx_word: int
    expect_gap_min: int
    expect_surah: int
    expect_ayah: int

@dataclass(frozen=True)
class FlowStep:
    text: str
    expect_surah: int
    expect_ayah: int

@dataclass(frozen=True)
class FlowCase:
    label: str
    steps: list[FlowStep]


# ═══════════════════════════════════════════════════════════════════════
# 1. DISCOVERY — Whisper-style normalized input (no diacritics)
# ═══════════════════════════════════════════════════════════════════════
# NOTE: Bismillah alone is genuinely ambiguous (2 hits in Quran).
# "الحمد لله رب العالمين" alone has 4 hits. These CORRECTLY fail.
# Use cross-ayah text for unique discovery of these ayahs.

DISCOVERY_CASES = [
    # Fatiha — need cross-ayah context for unique match
    DiscoverCase("fatiha-bismillah+alhamd", "بسم الله الرحمن الرحيم الحمد لله", 1, 1),
    DiscoverCase("fatiha-alhamd+rahman", "الحمد لله رب العالمين الرحمن الرحيم", 1, 2),
    DiscoverCase("fatiha-maliki", "مالك يوم الدين", 1, 4),
    DiscoverCase("fatiha-iyyaka", "اياك نعبد واياك نستعين", 1, 5),
    DiscoverCase("fatiha-ihdina", "اهدنا الصراط المستقيم", 1, 6),
    DiscoverCase("fatiha-sirat", "صراط الذين انعمت عليهم", 1, 7),

    # Short Surahs
    DiscoverCase("ikhlas-full", "قل هو الله احد", 112, 1),
    DiscoverCase("ikhlas-samad", "الله الصمد", 112, 2),
    DiscoverCase("kawthar-inna", "انا اعطيناك الكوثر", 108, 1),
    DiscoverCase("kawthar-fasalli", "فصل لربك وانحر", 108, 2),

    # Al-Alaq (96) — user requested
    DiscoverCase("alaq-iqra", "اقرا باسم ربك الذي خلق", 96, 1),
    DiscoverCase("alaq-khalaqa", "خلق الانسان من علق", 96, 2),
    DiscoverCase("alaq-iqra2", "اقرا وربك الاكرم", 96, 3),

    # Medium / Long
    DiscoverCase("baqara-12", "الا انهم هم المفسدون ولكن لا يشعرون", 2, 12),
    DiscoverCase("baqara-18", "صم بكم عمي فهم لا يرجعون", 2, 18),
    DiscoverCase("baqara-43", "واقيموا الصلاة واتوا الزكاة واركعوا مع الراكعين", 2, 43),
    DiscoverCase("baqara-152", "فاذكروني اذكركم واشكروا لي ولا تكفرون", 2, 152),
    DiscoverCase("baqara-4", "والذين يؤمنون بما انزل اليك وما انزل من قبلك", 2, 4),
    DiscoverCase("baqara-21", "يا ايها الناس اعبدوا ربكم الذي خلقكم", 2, 21),
]


# ═══════════════════════════════════════════════════════════════════════
# 2. DISCOVERY SHOULD FAIL — ambiguous or garbage
# ═══════════════════════════════════════════════════════════════════════

DISCOVERY_FAIL_CASES = [
    DiscoverFailCase("single-word", "الله"),
    DiscoverFailCase("noise-hallucination", "اه اه اه"),
    DiscoverFailCase("english", "hello world"),
    DiscoverFailCase("empty", ""),
    DiscoverFailCase("common-pair", "قال الله"),
    # Bismillah alone is genuinely ambiguous — 2 positions in Quran
    DiscoverFailCase("bismillah-alone", "بسم الله الرحمن الرحيم"),
]


# ═══════════════════════════════════════════════════════════════════════
# 3. TRACKING — local matching from known position
# ═══════════════════════════════════════════════════════════════════════

TRACKING_CASES = [
    TrackCase("track-fatiha-2", "الحمد لله رب العالمين", 1, 1, 3, 1, 2),
    TrackCase("track-fatiha-3", "الرحمن الرحيم", 1, 2, 3, 1, 3),
    TrackCase("track-fatiha-5", "اياك نعبد واياك نستعين", 1, 4, 2, 1, 5),
    TrackCase("track-ikhlas-2", "الله الصمد", 112, 1, 3, 112, 2),
    TrackCase("track-ikhlas-3", "لم يلد ولم يولد", 112, 2, 1, 112, 3),
    TrackCase("track-asr-2", "ان الانسان لفي خسر", 103, 1, 0, 103, 2),
    TrackCase("track-kawthar-2", "فصل لربك وانحر", 108, 1, 2, 108, 2),
    TrackCase("track-fatiha-7-mid", "غير المغضوب عليهم ولا الضالين", 1, 7, 3, 1, 7),
    # Context at 96:1:0 — "خلق الانسان من علق" matches 96:2 perfectly (4/4 words)
    TrackCase("track-alaq-to-2", "خلق الانسان من علق", 96, 1, 0, 96, 2),
    # Context at end of 96:1 — should also advance to 96:2
    TrackCase("track-alaq-1to2", "خلق الانسان من علق", 96, 1, 4, 96, 2),
    # Whisper drops word: "لم ولم يولد" (missing يلد)
    TrackCase("noise-drop-word", "لم ولم يولد", 112, 2, 1, 112, 3),
    # Single-word tracking — critical for short ayahs
    TrackCase("track-1word-asr", "والعصر", 103, 1, 0, 103, 1),
    TrackCase("track-1word-next", "ان", 103, 1, 0, 103, 2),
]


# ═══════════════════════════════════════════════════════════════════════
# 4. CROSS-AYAH — continuous recitation spanning boundaries
# ═══════════════════════════════════════════════════════════════════════

CROSS_AYAH_CASES = [
    DiscoverCase("cross-asr-1-2", "والعصر ان الانسان لفي خسر", 103, 1),
    DiscoverCase("cross-kawthar-1-2", "انا اعطيناك الكوثر فصل لربك", 108, 1),
    DiscoverCase("cross-ikhlas-1-2", "قل هو الله احد الله الصمد", 112, 1),
    DiscoverCase("cross-fatiha-2-3", "الحمد لله رب العالمين الرحمن الرحيم", 1, 2),
    DiscoverCase("cross-fatiha-5-6", "اياك نعبد واياك نستعين اهدنا الصراط", 1, 5),
    DiscoverCase("cross-alaq-1-2", "اقرا باسم ربك الذي خلق خلق الانسان من علق", 96, 1),
    DiscoverCase("cross-alaq-2-3", "خلق الانسان من علق اقرا وربك الاكرم", 96, 2),
]


# ═══════════════════════════════════════════════════════════════════════
# 5. GAP FILL — tracker jumps ahead, intermediate words auto-filled
# ═══════════════════════════════════════════════════════════════════════

GAP_FILL_CASES = [
    GapFillCase("gap-fatiha-skip", "اياك نعبد واياك نستعين", 1, 2, 0, 3, 1, 5),
    GapFillCase("gap-ikhlas-skip", "لم يلد ولم يولد", 112, 1, 0, 3, 112, 3),
]


# ═══════════════════════════════════════════════════════════════════════
# 6. FULL SURAH FLOW — simulate entire surah recitation
# ═══════════════════════════════════════════════════════════════════════
# First step uses discovery with enough context for unique match.
# Remaining steps use tracking.

FLOW_CASES = [
    FlowCase("flow-fatiha", [
        FlowStep("بسم الله الرحمن الرحيم الحمد لله", 1, 1),  # cross-ayah for unique discovery
        FlowStep("رب العالمين", 1, 2),
        FlowStep("الرحمن الرحيم", 1, 3),
        FlowStep("مالك يوم الدين", 1, 4),
        FlowStep("اياك نعبد واياك نستعين", 1, 5),
        FlowStep("اهدنا الصراط المستقيم", 1, 6),
        FlowStep("صراط الذين انعمت عليهم غير المغضوب عليهم ولا الضالين", 1, 7),
    ]),
    FlowCase("flow-ikhlas", [
        FlowStep("قل هو الله احد", 112, 1),
        FlowStep("الله الصمد", 112, 2),
        FlowStep("لم يلد ولم يولد", 112, 3),
        FlowStep("ولم يكن له كفوا احد", 112, 4),
    ]),
    FlowCase("flow-asr", [
        FlowStep("والعصر ان الانسان لفي خسر", 103, 1),  # cross-ayah for unique discovery
        FlowStep("الا الذين امنوا وعملوا الصالحات وتواصوا بالحق وتواصوا بالصبر", 103, 3),
    ]),
    FlowCase("flow-kawthar", [
        FlowStep("انا اعطيناك الكوثر", 108, 1),
        FlowStep("فصل لربك وانحر", 108, 2),
        FlowStep("ان شانئك هو الابتر", 108, 3),
    ]),
]


# ═══════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════

class Stats:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def record(self, ok, label, detail):
        if ok:
            self.passed += 1
            print(f"  PASS  {label:35}  {detail}")
        else:
            self.failed += 1
            self.failures.append((label, detail))
            print(f"  FAIL  {label:35}  {detail}")

    @property
    def total(self):
        return self.passed + self.failed


def header(title):
    print(f"\n{'─'*56}")
    print(f"  {title}")
    print(f"{'─'*56}")


def run_discover(qi, s):
    header("DISCOVERY")
    for c in DISCOVERY_CASES:
        m = qi.discover(c.text)
        if m is None:
            s.record(False, c.label, "no match")
        elif (m.surah_id, m.ayah_id) != (c.expect_surah, c.expect_ayah):
            s.record(False, c.label, f"got {m.surah_id}:{m.ayah_id} expected {c.expect_surah}:{c.expect_ayah}")
        else:
            s.record(True, c.label, f"→ {m.surah_id}:{m.ayah_id}")


def run_discover_fail(qi, s):
    header("DISCOVERY REJECT")
    for c in DISCOVERY_FAIL_CASES:
        m = qi.discover(c.text)
        s.record(m is None, c.label, "rejected" if m is None else f"should be None got {m.surah_id}:{m.ayah_id}")


def run_tracking(qi, s):
    header("TRACKING")
    for c in TRACKING_CASES:
        m = qi.track(c.text, c.ctx_surah, c.ctx_ayah, c.ctx_word)
        if m is None:
            s.record(False, c.label, f"no match ctx={c.ctx_surah}:{c.ctx_ayah}:{c.ctx_word}")
        elif (m.surah_id, m.ayah_id) != (c.expect_surah, c.expect_ayah):
            s.record(False, c.label, f"got {m.surah_id}:{m.ayah_id} expected {c.expect_surah}:{c.expect_ayah}")
        else:
            s.record(True, c.label, f"→ {m.surah_id}:{m.ayah_id} words={len(m.words)}")


def run_cross_ayah(qi, s):
    header("CROSS-AYAH CONTINUOUS")
    for c in CROSS_AYAH_CASES:
        m = qi.discover(c.text)
        if m is None:
            s.record(False, c.label, "no match")
        elif (m.surah_id, m.ayah_id) != (c.expect_surah, c.expect_ayah):
            s.record(False, c.label, f"got {m.surah_id}:{m.ayah_id} expected {c.expect_surah}:{c.expect_ayah}")
        else:
            s.record(True, c.label, f"→ {m.surah_id}:{m.ayah_id}")


def run_gap_fill(qi, s):
    header("GAP FILL")
    for c in GAP_FILL_CASES:
        m = qi.track(c.text, c.ctx_surah, c.ctx_ayah, c.ctx_word)
        if m is None:
            s.record(False, c.label, "no match")
            continue
        gaps = sum(1 for w in m.words if w.is_gap_filled)
        ok = (m.surah_id, m.ayah_id) == (c.expect_surah, c.expect_ayah) and gaps >= c.expect_gap_min
        s.record(ok, c.label, f"→ {m.surah_id}:{m.ayah_id} gaps={gaps}")


def run_flows(qi, s):
    header("FULL SURAH FLOW")
    for flow in FLOW_CASES:
        print(f"\n  ── {flow.label} ──")
        last_s = last_a = None
        last_w = -1

        for i, step in enumerate(flow.steps):
            label = f"{flow.label}[{i}]"
            if i == 0:
                m = qi.discover(step.text)
                mode = "discover"
            else:
                m = qi.track(step.text, last_s, last_a, last_w)
                mode = "track"

            if m is None:
                s.record(False, label, f"no match ({mode})")
                last_s, last_a, last_w = step.expect_surah, step.expect_ayah, 0
                continue

            ok = (m.surah_id, m.ayah_id) == (step.expect_surah, step.expect_ayah)
            s.record(ok, label,
                f"→ {m.surah_id}:{m.ayah_id} ({mode})" +
                (f" expected {step.expect_surah}:{step.expect_ayah}" if not ok else ""))

            for w in reversed(m.words):
                if w.surah_id and w.ayah_id is not None and w.reference_index is not None:
                    last_s, last_a, last_w = w.surah_id, w.ayah_id, w.reference_index
                    break


def main():
    print("Loading QuranIndex...")
    qi = QuranIndex()
    print(f"Loaded: {len(qi._flat)} words, {len(qi._ngram_index)} N-gram keys\n")

    s = Stats()
    run_discover(qi, s)
    run_discover_fail(qi, s)
    run_tracking(qi, s)
    run_cross_ayah(qi, s)
    run_gap_fill(qi, s)
    run_flows(qi, s)

    print(f"\n{'='*56}")
    if s.failed:
        print(f"  TOTAL: {s.passed}/{s.total} passed ({s.failed} FAILED)")
        print("\n  Failures:")
        for label, detail in s.failures:
            print(f"    ✗ {label}: {detail}")
    else:
        print(f"  TOTAL: {s.passed}/{s.total} ✓ ALL PASSED")
    print(f"{'='*56}")
    return 0 if s.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
