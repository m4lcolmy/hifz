"""End-to-end benchmark over the recordings in tests/records.

Runs real audio through the real pipeline — VAD windowing, Whisper, the
mode-aware matcher, and the recitation tracker — then scores the result
against the ground truth encoded in each filename.

The number that matters is FALSE ALARMS: words the reciter said correctly
that the app painted red.  Every recording tagged "no mistake" should produce
zero.  The two recordings with a deliberate mistake should produce exactly one
detection.

Real-time behaviour is simulated, not ignored: windows arrive every
VAD step, transcription takes as long as it takes, and any window that is
already stale when the model frees up is dropped exactly as the live LIFO
queue drops it.  So the benchmark reproduces the app's real drop rate.

Run:
    conda run -n hifz python scripts/benchmark_recordings.py
    conda run -n hifz python scripts/benchmark_recordings.py --only kawthar
    conda run -n hifz python scripts/benchmark_recordings.py --verbose
"""

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.audio.transcriber import transcribe_window
from src.audio.vad import SlidingWindowBuffer
from src.config import (
    MODEL_DIR, DEVICE, USE_FP16, SAMPLE_RATE, STRICTNESS, STRICTNESS_LEVELS,
)
from src.core.device import resolve_device
from src.core.matching import match_for_mode
from src.core.page_map import PageMap
from src.core.quran import QuranIndex
from src.ui.recitation import RecitationTracker
from scripts.recitation_corpus import (
    JUZ30_MANIFEST_PATH, MANIFEST_PATH, RECITATIONS_DIR, UNAVAILABLE_PATH,
    case_from_entry, load_pcm16, read_manifest,
)

RECORDS_DIR = PROJECT_ROOT / "tests" / "records"


# ═══════════════════════════════════════════════════════════════════════
# Ground truth — derived from the filenames the reciter recorded under
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Expectation:
    pattern: str                 # substring identifying the file
    surah: int
    ayahs: tuple[int, int]       # inclusive range
    mistakes: tuple = ()         # (surah, ayah, word_index) of deliberate errors
    note: str = ""
    # True when the recording covers only part of the ayah range — starting
    # mid-ayah, say. Coverage cannot be judged against words that were never
    # recited, so such a case is left out of the coverage total rather than
    # counted as words the app failed to show.
    partial: bool = False
    # Which stratum of the fetched corpus this case came from, and whose
    # voice it is. Empty for the hand-made recordings, which are one stratum
    # and one voice by definition. One overall number hides a category
    # failing completely — which is exactly how "surah ala 11" scored zero
    # for months — so the report breaks the totals down by both.
    stratum: str = ""
    reciter: str = ""

    @property
    def mistake_count(self) -> int:
        return len(self.mistakes)


EXPECTATIONS = [
    Expectation("fatiha first 2 ayahs", 1, (1, 2), (), "starts with bismillah"),
    # Recorded as "ala 11", but the audio is وَالسَّمَاءِ ذَاتِ الرَّجْعِ —
    # At-Tariq 86:11, not Al-A'la 87:11 (which is وَيَتَجَنَّبُهَا الْأَشْقَى).
    # The app had been finding 86:11 correctly all along and being scored
    # against the wrong surah for it: 0 ok, 2 words "never shown". Adjacent
    # surah numbers, and nobody checked.
    Expectation("tariq 11", 86, (11, 11), (), "single short ayah"),
    Expectation("baqarah ayahs 6-7", 2, (6, 7), ((2, 7, 9),), "وَلَهُمْ recited with ف"),
    Expectation("hujurat 13", 49, (13, 13), ((49, 13, 6),), "mistake in وَأُنثَىٰ"),
    Expectation("insan 7", 76, (7, 7), (), ""),
    Expectation("kawthar 1", 108, (1, 1), (), "3-word ayah, cold start"),
    Expectation("muddathir 8-10", 74, (8, 10), (), ""),
    # Named "1-19", but 83:15, 83:16 and 83:17 are not in the audio: none of
    # مَحْجُوب / لَصَالُو / الْجَحِيم / تُكَذِّبُون is ever transcribed, and 83:14
    # and 83:18 are 0.3s apart — all five of those ayahs open كَلَّا, so the
    # skip is easy to make and easy to miss. The app was tracking it correctly
    # and being charged 24 words it was never given.
    Expectation("mutaffifin 1-19", 83, (1, 19), (),
                "long, pauses between ayahs; audio skips 83:15-17",
                partial=True),
    Expectation("nisa 11 from the middle", 4, (11, 11), (), "starts mid-ayah",
                partial=True),
    Expectation("qadr 1-2", 97, (1, 2), (), ""),
    Expectation("qafirun with auzu basmala", 109, (1, 1), (), "isti'adha + basmala first"),
    # The first case in this corpus recorded in real conditions rather than
    # for publication: a room, a C-Media analog mic, and 70 words/min against
    # the 48 of the studio murattal the app scores 2.2% against. It is the
    # whole of a Mushaf page — 4:12 is 88 words of dense inheritance law,
    # which is the hardest thing in the corpus by a wide margin.
    #
    # It is here because measuring it was impossible: the same page recited
    # live scored 48% of verdicts wrong and the audio was gone, so nothing
    # could be replayed, compared or fixed. Now it can.
    #
    # The label is the reciter's own — they recited the page and say it was
    # correct — and has not been checked by a second listen. Tier D found two
    # hand-typed labels wrong, so treat a false alarm here as a question
    # before treating it as a defect.
    Expectation("nisa 12-14 whole page", 4, (12, 14), (),
                "real conditions: room mic, fast pace, densest page in the Quran"),
]


# `load_pcm16` now lives in scripts/recitation_corpus.py: the fetcher decodes
# the same way to concatenate runs, and two decoders that must agree are one
# decoder waiting to drift.


# ═══════════════════════════════════════════════════════════════════════
# Collectors
# ═══════════════════════════════════════════════════════════════════════

class VirtualClock:
    """The simulated session clock, handed to the tracker."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScoreBoard:
    """Stands in for the Mushaf view and keeps the FINAL status per word.

    The live view paints a word and may repaint it later; what the reciter
    ends up seeing is the last colour written, so that is what we score.
    """

    def __init__(self):
        self.current_page_num = None
        self.page_serial = 0
        self.final: dict[tuple[int, int, int], bool | None] = {}
        self.writes = 0
        self.pages: list[int] = []
        self.revealed: list[tuple[int, int, int]] = []
        self.basmalas: list[int] = []
        # Every word that was red at any point in the session, whether or not
        # it still is. The final state is what this board scores, so a word
        # painted red and then cleared costs nothing here — but the reciter
        # saw the red. See `flashes`.
        self.ever_red: set[tuple[int, int, int]] = set()

    def load_page(self, page_num: int):
        self.current_page_num = page_num
        self.page_serial += 1
        self.pages.append(page_num)

    def update_recitation(self, surah: int, ayah: int, word_index: int, status):
        self.final[(surah, ayah, word_index)] = status
        self.writes += 1
        if status is False:
            self.ever_red.add((surah, ayah, word_index))

    @property
    def flashes(self) -> set[tuple[int, int, int]]:
        """Words shown red during the session that are not red at the end.

        A false alarm the final-state score cannot see. The reciter watching
        the page saw the word go red and then go back — and a page that
        reddens words it will itself retract is a page you learn to distrust,
        which costs more than the retracted words.
        """
        return {w for w in self.ever_red if self.final.get(w) is not False}

    def reveal(self, surah: int, ayah: int, word_index: int) -> bool:
        """Shown, with no verdict attached.

        Any verdict the word was carrying comes off the page with it, so the
        board keeps mirroring what the reciter can actually see. A word left
        in this state at the end of the run is scored as never given a
        verdict — which is what it is.
        """
        self.revealed.append((surah, ayah, word_index))
        self.final.pop((surah, ayah, word_index), None)
        return True

    def paint_basmala(self, surah: int, status: bool = True) -> bool:
        """The real view answers False where the page carries no bismillah
        line for that surah; here every page is assumed to carry one, so
        callers are exercised on the path that does something."""
        self.basmalas.append(surah)
        return True


@dataclass
class Result:
    name: str
    expectation: Expectation
    ok: int = 0
    wrong: int = 0
    missed: int = 0
    off_range: int = 0
    chunks: int = 0
    dropped: int = 0
    empty: int = 0
    asr_ms: list[float] = field(default_factory=list)
    wall_s: float = 0.0
    audio_s: float = 0.0
    found_position: bool = False
    transcripts: list[str] = field(default_factory=list)
    wrong_words: list[str] = field(default_factory=list)
    expected_words: int = 0     # words the recited range actually contains

    @property
    def scored(self) -> int:
        return self.ok + self.wrong

    @property
    def unshown(self) -> int:
        """Words of the recitation that never got a verdict at all.

        The false alarm rate is a fraction of the words scored, so a word
        never shown costs nothing — and a change that quietly shows fewer
        words scores *better*. Al-Mutaffifin 1-19 read as a clean 0% while
        24 of its 93 words, including all of 83:15-17, never appeared.
        Coverage has to be reported next to the rate or the rate can be
        improved by doing less.
        """
        if self.expectation.partial:
            return 0        # most of the range was never recited
        return max(0, self.expected_words - self.ok - self.wrong - self.missed)

    flagged: set = field(default_factory=set)   # positions painted red
    flashed: set = field(default_factory=set)   # painted red, then cleared

    @property
    def caught(self) -> int:
        """Deliberate mistakes the app actually flagged."""
        return len(self.flagged & set(self.expectation.mistakes))

    @property
    def false_alarms(self) -> int:
        """Red words that were NOT a deliberate mistake."""
        return len(self.flagged - set(self.expectation.mistakes))

    @property
    def flashes(self) -> int:
        """Words the reciter saw go red mid-session and then go back.

        Not counted in `false_alarms`, which scores the page as it is left.
        This is the cost the final-state score cannot charge: a retracted red
        is a red the reciter still had to read, stop at and doubt.
        """
        return len(self.flashed - set(self.expectation.mistakes))


# ═══════════════════════════════════════════════════════════════════════
# The run
# ═══════════════════════════════════════════════════════════════════════

def run_recording(model, index, page_map, path: Path, exp: Expectation,
                  verbose: bool = False, strictness: str | None = None) -> Result:
    """One recording, one strictness level."""
    return run_levels(model, index, page_map, path, exp, verbose,
                      [strictness])[strictness]


def run_levels(model, index, page_map, path: Path, exp: Expectation,
               verbose: bool, levels: list) -> dict:
    """Push one recording through the pipeline once, scored at every level.

    The audio and the ASR are shared. Strictness changes only what colour a
    verdict is painted — it cannot change which window arrives when, what the
    model hears, or where the tracker thinks the reciter is — so running the
    model once per level would burn four times the GPU for four identical
    transcriptions. On the fetched corpus that is the difference between half
    an hour and two hours, which is the difference between measuring every
    level and not bothering.

    Each level still gets its own tracker, its own board and its own match
    decisions computed from its own pointer. Only `transcribe()` is shared.
    If strictness ever did come to affect tracking, the levels would simply
    diverge here rather than silently share a wrong answer.
    """
    results = {level: Result(name=path.name, expectation=exp) for level in levels}
    expected_words = index.words_in_range(exp.surah, *exp.ayahs)

    pcm = load_pcm16(path)
    audio_s = len(pcm) / 2 / SAMPLE_RATE

    runs = {}
    for level in levels:
        board = ScoreBoard()
        # The tracker's timing rules must see the simulated clock, not wall
        # time, or the benchmark stops reproducing the live app.
        virtual = VirtualClock()
        runs[level] = (board, virtual, RecitationTracker(
            board, page_map, index, clock=virtual, strictness=level))
        results[level].expected_words = expected_words
        results[level].audio_s = audio_s

    buffer = SlidingWindowBuffer()

    # Feed the buffer in 100 ms increments like the mic callback does, and
    # collect (audio_time, window) so we can simulate the live drop policy.
    feed_bytes = SAMPLE_RATE // 10 * 2
    pending: list[tuple[float, bytes]] = []
    for offset in range(0, len(pcm), feed_bytes):
        block = pcm[offset:offset + feed_bytes]
        audio_t = (offset + len(block)) / 2 / SAMPLE_RATE
        for window in buffer.feed(block):
            pending.append((audio_t, window.data))
    leftover = buffer.flush()
    if leftover:
        pending.append((audio_s, leftover.data))

    # Virtual clock: the model is busy while it transcribes, and any window
    # that arrived during that time is stale — the live LIFO queue keeps only
    # the newest one. This reproduces the app's real drop rate.
    t0 = time.monotonic()
    clock = 0.0
    for _board, virtual, _tracker in runs.values():
        virtual.now = 0.0
    i = 0
    while i < len(pending):
        arrive, window = pending[i]
        clock = max(clock, arrive)

        newest = i
        while newest + 1 < len(pending) and pending[newest + 1][0] <= clock:
            newest += 1
        dropped = newest - i
        arrive, window = pending[newest]
        i = newest + 1

        text, asr_ms = transcribe(model, window)
        clock += asr_ms / 1000.0

        from src.core.debug import log as _dbg
        logged = False

        for level, (_board, virtual, tracker) in runs.items():
            res = results[level]
            res.dropped += dropped
            res.chunks += 1
            res.asr_ms.append(asr_ms)
            virtual.now = clock

            if not text:
                res.empty += 1
                continue

            res.transcripts.append(text)
            context = (tracker.last_surah, tracker.last_ayah,
                       tracker.last_word_index)
            decision = match_for_mode(index, text, tracker.mode, context)
            if _dbg.enabled and not logged:
                logged = True
                _dbg.asr(text, asr_ms)
                _dbg.match(decision.mode, context, decision.match,
                           decision.skip_reason)
            tracker.on_result({
                "text": text,
                "match": decision.match,
                "mode": tracker.mode,
                "attempted": decision.attempted,
            })
            if decision.match is not None:
                res.found_position = True

            # The app runs a timer that asks the tracker whether anything has
            # settled, because a verdict held back until no window can revise
            # it needs *something* to ask once the windows stop arriving.
            # Here the windows are the clock, so asking after each one is the
            # same question at the same simulated times.
            tracker.tick()

    wall = time.monotonic() - t0
    lo, hi = exp.ayahs
    for level, (board, _virtual, tracker) in runs.items():
        res = results[level]
        tracker.finalize()
        res.wall_s = wall / len(runs)

        # Score the final state of the page
        for (surah, ayah, word_index), status in board.final.items():
            in_range = surah == exp.surah and lo <= ayah <= hi
            if not in_range:
                res.off_range += 1
                continue
            if status is True:
                res.ok += 1
            elif status is False:
                res.wrong += 1
                res.flagged.add((surah, ayah, word_index))
                entry = tracker._scored.get((surah, ayah, word_index))
                if entry is not None:
                    w = entry[1]
                    res.wrong_words.append(
                        f"{surah}:{ayah}:{word_index}  heard={w.recited!r} "
                        f"ref={w.reference!r}"
                    )
                else:
                    res.wrong_words.append(f"{surah}:{ayah}:{word_index}")
            else:
                res.missed += 1

        res.flashed = {w for w in board.flashes
                       if w[0] == exp.surah and lo <= w[1] <= hi}

    if verbose:
        first = results[levels[0]]
        print(f"\n    transcripts ({len(first.transcripts)}):")
        for t in first.transcripts:
            print(f"      {t}")

    return results


def transcribe(model, window: bytes) -> tuple[str, float]:
    """Transcribe one PCM window through the app's own ASR path."""
    n = len(window) // 2
    audio = np.frombuffer(window[: n * 2], dtype="<i2").astype(np.float32) / 32768.0

    start = time.monotonic()
    text = transcribe_window(model, audio)
    return text, (time.monotonic() - start) * 1000


# ═══════════════════════════════════════════════════════════════════════
# Reporting
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Totals:
    """The three numbers, over any slice of the results.

    Always computed together. A lower false alarm rate that also misses real
    mistakes is a regression, and a higher coverage bought by scoring fewer
    words is not an improvement — so nothing here is reportable on its own.
    """

    label: str
    cases: int = 0
    found: int = 0
    ok: int = 0
    wrong: int = 0
    missed: int = 0
    false_alarms: int = 0
    flashes: int = 0
    expected: int = 0
    unshown: int = 0
    mistakes: int = 0
    caught: int = 0

    @property
    def scored(self) -> int:
        return self.ok + self.wrong

    @property
    def coverage(self) -> float | None:
        if not self.expected:
            return None
        return 100 * (self.expected - self.unshown) / self.expected

    @property
    def false_alarm_rate(self) -> float | None:
        if not self.scored:
            return None
        return 100 * self.false_alarms / self.scored

    @property
    def flash_rate(self) -> float | None:
        """Retracted reds as a fraction of words scored.

        Reported beside the false alarm rate because the two trade against
        each other: anything that waits longer before painting red lowers
        this and can only raise `unshown` — never the other way round."""
        if not self.scored:
            return None
        return 100 * self.flashes / self.scored


def totals_for(results: list[Result], label: str) -> Totals:
    t = Totals(label=label, cases=len(results))
    for r in results:
        t.found += 1 if r.found_position else 0
        t.ok += r.ok
        t.wrong += r.wrong
        t.missed += r.missed
        t.false_alarms += r.false_alarms
        t.flashes += r.flashes
        t.unshown += r.unshown
        t.mistakes += r.expectation.mistake_count
        t.caught += r.caught
        # A partial recording still contributes the words it did cover — it
        # is only the words never recited that cannot be charged to the app.
        t.expected += ((r.ok + r.wrong + r.missed) if r.expectation.partial
                       else r.expected_words)
    return t


def group_by(results: list[Result], key) -> list[Totals]:
    """Totals per distinct value of `key`, ordered by that value."""
    buckets: dict[str, list[Result]] = {}
    for r in results:
        buckets.setdefault(key(r), []).append(r)
    return [totals_for(rs, name) for name, rs in sorted(buckets.items())]


def print_breakdown(title: str, rows: list[Totals], first_col: str, note: str = ""):
    """One overall number hides a category failing completely.

    That is exactly how "surah ala 11" scored zero for months: it was two
    words in a corpus of nine hundred, so the total never moved.
    """
    if len(rows) < 2:
        return
    print()
    print(f"  {title}")
    if note:
        print(f"  {note}")
    print(f"  {first_col:<28} {'cases':>5} {'found':>6} {'scored':>7} "
          f"{'cover':>7} {'false':>7} {'flash':>7}")
    print("  " + "-" * 74)
    for t in rows:
        cov = f"{t.coverage:.1f}%" if t.coverage is not None else "-"
        far = f"{t.false_alarm_rate:.1f}%" if t.false_alarm_rate is not None else "-"
        flr = f"{t.flash_rate:.1f}%" if t.flash_rate is not None else "-"
        flag = ""
        if t.coverage is not None and t.coverage < 95:
            flag = "  <-- COVERAGE"
        if t.false_alarm_rate is not None and t.false_alarm_rate >= 5:
            flag += "  <-- FALSE ALARMS"
        print(f"  {t.label[:28]:<28} {t.cases:5} {t.found:6} {t.scored:7} "
              f"{cov:>7} {far:>7} {flr:>7}{flag}")
    print("  " + "-" * 74)


def report(results: list[Result], engine_label: str = ""):
    print()
    print("=" * 96)
    print("  RESULTS")
    print("=" * 96)

    # With a fetched corpus this table is hundreds of rows and nobody reads
    # it. Show every row for the hand-made corpus, where each recording is
    # individually meaningful, and only the failing rows for a large one.
    big = len(results) > 30
    rows = [r for r in results
            if r.false_alarms or r.flashes or r.unshown or not r.found_position
            or r.caught < r.expectation.mistake_count] if big else results
    if big:
        print(f"  {len(results)} cases; showing the {len(rows)} with something "
              f"to look at")

    print(f"  {'recording':<40} {'position':>9} {'ok':>5} {'caught':>7} "
          f"{'miss':>5} {'false':>6} {'flash':>6} {'unscored':>9} {'drop%':>6}")
    print("  " + "-" * 99)

    for r in rows:
        pos = "FOUND" if r.found_position else "LOST"
        total_windows = r.chunks + r.dropped
        drop = 100 * r.dropped / total_windows if total_windows else 0
        flag = "  <-- FALSE ALARMS" if r.false_alarms else ""
        if r.expectation.mistakes and r.caught < r.expectation.mistake_count:
            flag += "  <-- MISSED A REAL MISTAKE"
        if r.unshown:
            # "unscored", not "never shown": `reveal()` uncovers a word on
            # the page without attaching a verdict to it, and since correct
            # recitation stopped being tinted those words look exactly like
            # correct ones. The reciter may well have seen every one of them.
            # What the app never did was say anything about them.
            flag += f"  <-- {r.unshown} WORD(S) NEVER SCORED"
        name = r.name[:38].replace(".flac", "")
        caught = (f"{r.caught}/{r.expectation.mistake_count}"
                  if r.expectation.mistakes else "-")
        print(f"  {name:<40} {pos:>9} {r.ok:5} {caught:>7} {r.missed:5} "
              f"{r.false_alarms:6} {r.flashes:6} {r.unshown:7} {drop:5.0f}%{flag}")

    print("  " + "-" * 99)

    flagged = [r for r in results if r.wrong_words]
    if flagged and not big:
        print()
        print("  WORDS MARKED WRONG:")
        for r in flagged:
            tag = (f"(expects {r.expectation.mistake_count} real)"
                   if r.expectation.mistakes else "")
            print(f"    {r.name.replace('.flac','')[:52]:<54}{tag}")
            for w in r.wrong_words:
                print(f"       {w}")

    # ── Per stratum, then per reciter ─────────────────────────────────
    if any(r.expectation.stratum for r in results):
        print_breakdown(
            "PER STRATUM — read this before the total",
            group_by(results, lambda r: r.expectation.stratum or "(hand-made)"),
            "stratum",
            "a stratum failing completely is invisible in one overall number",
        )
    if any(r.expectation.reciter for r in results):
        print_breakdown(
            "PER RECITER — the app was tuned on one voice",
            group_by(results, lambda r: r.expectation.reciter or "(hand-made)"),
            "reciter",
            "if the others score much worse, the tuning is overfitted and "
            "config.py needs re-deriving",
        )

    # ── The totals ────────────────────────────────────────────────────
    t = totals_for(results, "all")
    all_asr = [ms for r in results for ms in r.asr_ms]
    windows = sum(r.chunks + r.dropped for r in results)
    dropped = sum(r.dropped for r in results)
    audio = sum(r.audio_s for r in results)
    wall = sum(r.wall_s for r in results)

    print()
    if engine_label:
        print(f"  engine                  {engine_label}")
    print(f"  position found          {t.found}/{t.cases} recordings")
    print(f"  words scored            {t.scored}  (ok={t.ok} wrong={t.wrong} "
          f"missed={t.missed})")
    if t.coverage is not None:
        print(f"  COVERAGE                {t.expected - t.unshown}/{t.expected} = "
              f"{t.coverage:.1f}% of recited words given a verdict"
              f"   <-- read this first")
    if t.false_alarm_rate is not None:
        print(f"  FALSE ALARM RATE        {t.false_alarms}/{t.scored} = "
              f"{t.false_alarm_rate:.1f}%   <-- the number to drive down")
        print( "                          (a fraction of words SCORED — showing "
               "fewer words flatters it, so coverage must hold)")
    if t.flash_rate is not None:
        print(f"  RETRACTED REDS          {t.flashes}/{t.scored} = "
              f"{t.flash_rate:.1f}% of words went red and came back")
        print( "                          (invisible above: the page is scored "
               "as it is LEFT, and the reciter watched it)")
    if t.mistakes:
        print(f"  DELIBERATE MISTAKES     {t.caught}/{t.mistakes} caught"
              f"   <-- must stay at {t.mistakes}/{t.mistakes}")
    else:
        print( "  DELIBERATE MISTAKES     none in this corpus — a correct "
               "recitation contains no mistakes,")
        print( "                          so this run cannot tell you whether "
               "the app still catches them.")
        print( "                          Run the hand-made corpus too.")
    if all_asr:
        print(f"  asr latency             mean={np.mean(all_asr):.0f}ms "
              f"median={np.median(all_asr):.0f}ms")
    if windows:
        print(f"  windows                 {windows} produced, {dropped} dropped "
              f"({100 * dropped / windows:.0f}%)")
    print(f"  audio {audio:.0f}s processed in {wall:.0f}s "
          f"({wall / audio:.2f}x real time)" if audio else "")
    print("=" * 96)


# ═══════════════════════════════════════════════════════════════════════
# Whole surahs, recited by whoever owns the app
# ═══════════════════════════════════════════════════════════════════════

#: Where a recording of a whole surah goes. One file, one surah, recited
#: from its first word to its last.
OWN_SURAHS_DIR = RECORDS_DIR / "surahs"

#: `mistake 4:7` — ayah 4, seventh word, counted the way a person counts.
_MISTAKE = re.compile(r"mistake\s+(\d+)\s*[:.]\s*(\d+)", re.IGNORECASE)


def cases_from_own_surahs(directory: Path = OWN_SURAHS_DIR,
                          index: "QuranIndex | None" = None,
                          ) -> list[tuple[Path, Expectation]]:
    """Whole-surah recordings, labelled by the surah number in the filename.

    **This is the only corpus in the project that is both the right shape and
    the right conditions**, and it is the only one that cannot be downloaded.
    The fetched corpora fix the shape — a session is a whole surah, opened
    cold — but every file in them was recorded to be published, in a studio,
    by a qāriʾ. The measured gap is not small: the same code scores 2.2% false
    alarms across 273 fetched cases and 25–48% on this reciter's own
    microphone. Whatever is costing those thirty points is not in any
    corpus here, so nothing here can be used to fix it.

    **The label cannot be typed wrong, because there is nothing to type.** A
    whole surah runs from ayah 1 to its last ayah, and how many ayahs a surah
    has is a fact the app already knows — so naming the file after the surah
    fixes the ayah range completely. That closes the failure Tier D found:
    two of eleven hand-made recordings named ayahs their audio does not
    contain, because the range was typed by a person from memory.

        tests/records/surahs/
            093 ad-duhaa.flac
            109 al-kafirun mistake 3:2.flac
            112.flac

    Everything after the number is for the human reading the directory, with
    two exceptions the parser looks for:

      - `mistake <ayah>:<word>` — a word deliberately recited wrong, the word
        counted from 1 the way a person counts. Repeat it for several. This
        is the one thing that still has to be typed, because a deliberate
        mistake is a fact about what the reciter did and no file can know it.
      - `partial` — the recording stops early. Coverage is then not charged
        for the words that were never recited, the way it is not charged for
        `surah mutaffifin 1-19`, whose audio is missing three ayahs.
    """
    if not directory.exists():
        return []
    if index is None:
        index = QuranIndex()

    cases: list[tuple[Path, Expectation]] = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in (".flac", ".wav", ".mp3", ".m4a", ".ogg"):
            continue
        digits = re.match(r"\s*(\d{1,3})", path.stem)
        if not digits:
            print(f"  ({path.name}: no surah number at the start of the name)")
            continue
        surah = int(digits.group(1))
        last = index.last_ayah(surah)
        if not last:
            print(f"  ({path.name}: {surah} is not a surah)")
            continue

        mistakes = tuple(
            # 0-based on the way in, because that is what the tracker paints
            # with and what `EXPECTATIONS` already stores. Converted exactly
            # once, here, where the 1-based half is a filename a human wrote.
            (surah, int(ayah), int(word) - 1)
            for ayah, word in _MISTAKE.findall(path.stem)
        )
        cases.append((path, Expectation(
            pattern=path.name,
            surah=surah,
            ayahs=(1, last),
            mistakes=mistakes,
            note="whole surah, recited by the app's own user",
            partial="partial" in path.stem.lower(),
            stratum="my own recording",
            reciter="me",
        )))
    return cases


# ═══════════════════════════════════════════════════════════════════════
# Cases captured from a live session
# ═══════════════════════════════════════════════════════════════════════

def cases_from_session(directory: Path) -> list[tuple[Path, Expectation]]:
    """Read a `./run.sh --record` directory as benchmark cases.

    The labels are what the app *believed* at the time, not verified ground
    truth, so a case built this way inherits any mistake the app made. That is
    the point: a clip the app labelled wrongly is a failing case that has
    already been isolated and already carries the wrong answer, which is more
    than a hand-recorded file ever gives you. Check a clip before trusting it,
    then move it into tests/records/ with a proper name.

    The before-lock clip is skipped — it has no ayah to be scored against.
    """
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"no manifest.json in {directory}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases: list[tuple[Path, Expectation]] = []

    for clip in manifest.get("clips", ()):
        if clip.get("surah") is None:
            continue
        path = directory / clip["file"]
        if not path.exists():
            continue
        wrong = tuple(
            (v["surah"], v["ayah"], v["word"])
            for v in clip.get("verdicts", ())
            if v.get("verdict") == "wrong"
        )
        cases.append((path, Expectation(
            pattern=clip["file"],
            surah=clip["surah"],
            ayahs=(clip["ayah"], clip["ayah"]),
            # What the app called a mistake last time. Replaying should
            # reproduce it; a difference is the thing worth looking at.
            mistakes=wrong,
            note=f"from {directory.name}",
        )))
    return cases


def selected(needle: str, path: Path, exp: Expectation) -> bool:
    """Whether `--only` picks this case.

    The name alone is not enough for the fetched corpus: its filenames are
    `002_001.mp3` and the thing you want to re-run is "every muqatta'at case"
    or "everything As-Sudais recited" — which is what the per-stratum and
    per-reciter breakdowns send you looking for.
    """
    return (needle in path.name
            or needle in str(path.parent.name)
            or needle in exp.stratum
            or needle in exp.reciter)


def cases_from_recitations(manifest_path: Path,
                           root: Path) -> list[tuple[Path, Expectation]]:
    """Read the fetched corpus manifest as benchmark cases.

    **The expectations are generated, not typed.** The surah and ayah come
    from the ayah number in the URL the audio was fetched from, so the Tier D
    class of error — two of eleven hand-made labels naming ayahs the audio
    does not contain — cannot recur. Nobody types a label here.

    `mistakes` is empty for every case and stays empty: these are correct
    recitations by established qāriʾ, so the corpus can move coverage and
    false alarms and nothing else. DELIBERATE MISTAKES still comes only from
    the hand-made recordings.
    """
    manifest = read_manifest(manifest_path)
    cases: list[tuple[Path, Expectation]] = []
    missing = 0

    for entry in manifest.get("cases", ()):
        case = case_from_entry(entry)
        path = root / entry["file"]
        if not path.exists():
            missing += 1
            continue
        cases.append((path, Expectation(
            pattern=entry["file"],
            surah=case.surah,
            ayahs=(case.first_ayah, case.last_ayah),
            mistakes=(),
            note=case.note,
            stratum=case.stratum,
            reciter=case.reciter,
        )))

    if missing:
        # Two different things, and conflating them would be the same error
        # the coverage metric exists to prevent: a case nobody can fetch is a
        # hole in the corpus, while a case nobody *has* fetched is a command
        # you have not run.
        unavailable = {}
        record = root / UNAVAILABLE_PATH.name
        if record.exists():
            try:
                unavailable = json.loads(record.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                unavailable = {}
        if unavailable:
            print(f"  {len(unavailable)} case(s) the source cannot supply "
                  f"(see {record.name}) — a hole in the corpus, not a "
                  f"missing download")
        not_fetched = missing - len(unavailable)
        if not_fetched > 0:
            print(f"  {not_fetched} case(s) not fetched yet — "
                  f"python scripts/fetch_recitations.py --agree")
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only",
                    help="substring filter on the recording name — or, for "
                         "--from-recitations, on the stratum or the reciter, "
                         "which is how you go and look at a stratum the "
                         "report says is failing")
    ap.add_argument("--verbose", action="store_true", help="print every transcript")
    ap.add_argument(
        "--strictness", default=None, metavar="LEVEL",
        help="how much evidence before a word is painted red: "
             + ", ".join(STRICTNESS_LEVELS) + ", or 'all' to run every level "
             "and print them side by side. A level ships with measured "
             "numbers or it does not ship.",
    )
    ap.add_argument("--debug-log", help="write a full session log to this path")
    ap.add_argument("--model", default=MODEL_DIR, help="model directory to benchmark")
    ap.add_argument(
        "--engine", default=None, metavar="NAME",
        help="speech engine to benchmark (default: the configured one). "
             "This is how a bake-off is run: same corpus, same matching, "
             "same scoring, one engine swapped.",
    )
    ap.add_argument(
        "--from-session", type=Path, metavar="DIR",
        help="score a ./run.sh --record directory instead of tests/records/",
    )
    ap.add_argument(
        "--from-recitations", action="store_true",
        help="score the fetched corpus of published recitations instead of "
             "tests/records/. Expectations come from the manifest, which "
             "took them from the ayah number in the URL — so they are "
             "generated, not typed.",
    )
    ap.add_argument(
        "--juz30", action="store_true",
        help="with --from-recitations, score the juz 30 corpus: whole short "
             "surahs, each recited from its basmala, which is the shape of a "
             "session rather than an ayah lifted out of the middle of one",
    )
    ap.add_argument(
        "--manifest", type=Path, default=None,
        help="corpus manifest for --from-recitations (default: the stratified "
             "corpus, or the juz 30 one with --juz30)",
    )
    ap.add_argument(
        "--recitations-dir", type=Path, default=RECITATIONS_DIR,
        help="where the fetched audio lives",
    )
    args = ap.parse_args()

    if args.manifest is None:
        args.manifest = JUZ30_MANIFEST_PATH if args.juz30 else MANIFEST_PATH
    if args.juz30 and not args.from_recitations:
        # --juz30 names a corpus, and a corpus is only read by
        # --from-recitations. Silently scoring tests/records/ instead would
        # print a full report for the wrong twelve recordings.
        print("  --juz30 selects a fetched corpus; add --from-recitations")
        return 1

    if args.debug_log:
        from src.core.debug import log as debug_log
        debug_log.enable(args.debug_log)

    if (args.from_session is None and not args.from_recitations
            and not RECORDS_DIR.exists()):
        print(f"No recordings at {RECORDS_DIR}")
        return 1

    print("Loading Quran index…")
    index = QuranIndex()
    page_map = PageMap()

    from src.audio.engines import EngineUnavailable, WhisperEngine, load_engine

    try:
        if args.engine in (None, WhisperEngine.name):
            # --model only means anything for Whisper; keep honouring it so a
            # Whisper-vs-Whisper comparison still works the way it always did.
            model = WhisperEngine(args.model).load()
        else:
            model = load_engine(args.engine)
    except EngineUnavailable as e:
        print(f"\n  {e}\n")
        return 1
    print(f"Engine: {model.label} — {model.description}")

    if args.from_recitations:
        cases = cases_from_recitations(args.manifest, args.recitations_dir)
        if not cases:
            print(f"\n  No fetched audio under {args.recitations_dir}.\n"
                  f"    python scripts/fetch_recitations.py --plan\n"
                  f"    python scripts/fetch_recitations.py --agree\n")
            return 1
        print(f"  {len(cases)} case(s) from the fetched corpus\n")
    elif args.from_session is not None:
        cases = cases_from_session(args.from_session)
        if not cases:
            print(f"  no labelled clips in {args.from_session}")
            return 1
        print(f"  {len(cases)} clip(s) from {args.from_session.name}\n")
    else:
        cases = []
        for exp in EXPECTATIONS:
            matches = [p for p in sorted(RECORDS_DIR.glob("*.flac"))
                       if exp.pattern in p.name]
            if not matches:
                print(f"  (no file for '{exp.pattern}')")
                continue
            cases.append((matches[0], exp))
        # Whole surahs recorded by whoever owns the app. Scored beside the
        # hand-made cases and broken out as their own stratum, because they
        # are the only audio in this project with a real microphone in it
        # and averaging them into studio recordings would hide exactly the
        # gap they exist to measure.
        own = cases_from_own_surahs(index=index)
        if own:
            print(f"  {len(own)} whole surah(s) from "
                  f"{OWN_SURAHS_DIR.relative_to(PROJECT_ROOT)}")
        cases.extend(own)

    levels = (list(STRICTNESS_LEVELS) if args.strictness == "all"
              else [args.strictness or STRICTNESS])
    for level in levels:
        if level not in STRICTNESS_LEVELS:
            print(f"  unknown strictness {level!r}; choose from "
                  f"{', '.join(STRICTNESS_LEVELS)} or 'all'")
            return 1

    by_level = run_all_levels(model, index, page_map, cases, args, levels)

    if len(levels) > 1:
        # The per-stratum view is the most useful thing this report produces
        # and comparing levels must not cost it. Shown for the configured
        # default, since that is the one the app actually runs.
        shown = STRICTNESS if STRICTNESS in by_level else levels[0]
        breakdown_only(by_level[shown], shown)
        compare_levels(by_level, model.label)
    else:
        results = by_level[levels[0]]
        if results:
            report(results, f"{model.label} @ {levels[0]}")
    return 0


def run_all_levels(model, index, page_map, cases, args, levels) -> dict:
    by_level: dict[str, list[Result]] = {level: [] for level in levels}
    unreadable: list[tuple[str, str]] = []
    for path, exp in cases:
        if args.only and not selected(args.only, path, exp):
            continue

        label = f"{exp.surah}:{exp.ayahs[0]}"
        if exp.ayahs[0] != exp.ayahs[1]:
            label += f"-{exp.ayahs[1]}"
        print(f"  {path.name[:60]:<62} expect {label}", flush=True)
        try:
            scored = run_levels(model, index, page_map, path, exp,
                                args.verbose, levels)
            for level, result in scored.items():
                by_level[level].append(result)
        except Exception as e:
            # A 275-case run takes half an hour and one bad file used to end
            # it at case 60 with an av error naming no file. Unreadable audio
            # is reported by name and the run carries on — but it is reported,
            # never silently dropped, because a case quietly removed from the
            # denominator is exactly how a corpus flatters an app.
            unreadable.append((path.name, f"{type(e).__name__}: {e}"))
            print(f"    UNREADABLE — {type(e).__name__}: {e}", flush=True)

    if unreadable:
        print(f"\n  {len(unreadable)} file(s) could not be read and were not "
              f"scored:")
        for name, why in unreadable:
            print(f"    {name}: {why}")
        print( "  Delete them and re-fetch — the fetcher now refuses to keep "
               "a file that does not decode.")
    return by_level


def breakdown_only(results: list[Result], level: str):
    """The per-stratum and per-reciter tables, without the per-case list."""
    if any(r.expectation.stratum for r in results):
        print_breakdown(
            f"PER STRATUM at '{level}' — read this before the total",
            group_by(results, lambda r: r.expectation.stratum or "(hand-made)"),
            "stratum",
            "a stratum failing completely is invisible in one overall number",
        )
    if any(r.expectation.reciter for r in results):
        print_breakdown(
            f"PER RECITER at '{level}' — the app was tuned on one voice",
            group_by(results, lambda r: r.expectation.reciter or "(hand-made)"),
            "reciter",
            "if the others score much worse, the tuning is overfitted",
        )


def compare_levels(by_level: dict[str, list[Result]], engine_label: str):
    """Every level, side by side. The point of the exercise.

    A level is named by what it costs, not by how it sounds — "lenient" that
    nobody has measured is a knob connected to nothing, and this codebase has
    already shipped one of those.
    """
    print()
    print("=" * 96)
    print(f"  STRICTNESS LEVELS — {engine_label}")
    print("=" * 96)
    print(f"  {'level':<12} {'scored':>7} {'coverage':>9} {'false alarms':>13} "
          f"{'caught':>8}   read every row together")
    print("  " + "-" * 92)
    for level, results in by_level.items():
        t = totals_for(results, level)
        cov = f"{t.coverage:.1f}%" if t.coverage is not None else "-"
        far = (f"{t.false_alarms}/{t.scored} = {t.false_alarm_rate:.1f}%"
               if t.false_alarm_rate is not None else "-")
        caught = f"{t.caught}/{t.mistakes}" if t.mistakes else "-"
        flag = ""
        if t.mistakes and t.caught < t.mistakes:
            flag = "  <-- MISSES A REAL MISTAKE"
        print(f"  {level:<12} {t.scored:7} {cov:>9} {far:>13} {caught:>8}{flag}")
    print("  " + "-" * 92)
    print( "  A lower false alarm rate that also catches fewer mistakes is a")
    print( "  regression, not an improvement. Neither column means anything")
    print( "  without the other, and neither means anything without coverage.")
    print("=" * 96)


if __name__ == "__main__":
    sys.exit(main())
