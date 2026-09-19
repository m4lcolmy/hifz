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
from src.config import MODEL_DIR, DEVICE, USE_FP16, SAMPLE_RATE
from src.core.device import resolve_device
from src.core.matching import match_for_mode
from src.core.page_map import PageMap
from src.core.quran import QuranIndex
from src.ui.recitation import RecitationTracker

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

    @property
    def mistake_count(self) -> int:
        return len(self.mistakes)


EXPECTATIONS = [
    Expectation("fatiha first 2 ayahs", 1, (1, 2), (), "starts with bismillah"),
    Expectation("surah ala 11", 87, (11, 11), (), "single short ayah"),
    Expectation("baqarah ayahs 6-7", 2, (6, 7), ((2, 7, 9),), "وَلَهُمْ recited with ف"),
    Expectation("hujurat 13", 49, (13, 13), ((49, 13, 6),), "mistake in وَأُنثَىٰ"),
    Expectation("insan 7", 76, (7, 7), (), ""),
    Expectation("kawthar 1", 108, (1, 1), (), "3-word ayah, cold start"),
    Expectation("muddathir 8-10", 74, (8, 10), (), ""),
    Expectation("mutaffifin 1-19", 83, (1, 19), (), "long, pauses between ayahs"),
    Expectation("nisa 11 from the middle", 4, (11, 11), (), "starts mid-ayah"),
    Expectation("qadr 1-2", 97, (1, 2), (), ""),
    Expectation("qafirun with auzu basmala", 109, (1, 1), (), "isti'adha + basmala first"),
]


# ═══════════════════════════════════════════════════════════════════════
# Audio loading — these FLACs have unreliable headers, so decode packets
# ═══════════════════════════════════════════════════════════════════════

def load_pcm16(path: Path, target_sr: int = SAMPLE_RATE) -> bytes:
    """Decode any audio file to mono 16 kHz Int16 PCM bytes."""
    import av

    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=target_sr
        )
        parts = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                parts.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None) or []:
            parts.append(out.to_ndarray().reshape(-1))

    if not parts:
        return b""
    return np.concatenate(parts).astype(np.int16).tobytes()


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
        self.final: dict[tuple[int, int, int], bool | None] = {}
        self.writes = 0
        self.pages: list[int] = []

    def load_page(self, page_num: int):
        self.current_page_num = page_num
        self.pages.append(page_num)

    def update_recitation(self, surah: int, ayah: int, word_index: int, status):
        self.final[(surah, ayah, word_index)] = status
        self.writes += 1


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

    @property
    def scored(self) -> int:
        return self.ok + self.wrong

    flagged: set = field(default_factory=set)   # positions painted red

    @property
    def caught(self) -> int:
        """Deliberate mistakes the app actually flagged."""
        return len(self.flagged & set(self.expectation.mistakes))

    @property
    def false_alarms(self) -> int:
        """Red words that were NOT a deliberate mistake."""
        return len(self.flagged - set(self.expectation.mistakes))


# ═══════════════════════════════════════════════════════════════════════
# The run
# ═══════════════════════════════════════════════════════════════════════

def run_recording(model, index, page_map, path: Path, exp: Expectation,
                  verbose: bool = False) -> Result:
    """Push one recording through the pipeline, simulating real-time drops."""
    res = Result(name=path.name, expectation=exp)

    pcm = load_pcm16(path)
    res.audio_s = len(pcm) / 2 / SAMPLE_RATE

    board = ScoreBoard()
    # The tracker's timing rules must see the simulated clock, not wall time,
    # or the benchmark stops reproducing the live app.
    virtual = VirtualClock()
    tracker = RecitationTracker(board, page_map, index, clock=virtual)
    buffer = SlidingWindowBuffer()

    # Feed the buffer in 100 ms increments like the mic callback does, and
    # collect (audio_time, window) so we can simulate the live drop policy.
    feed_bytes = SAMPLE_RATE // 10 * 2
    pending: list[tuple[float, bytes]] = []
    for offset in range(0, len(pcm), feed_bytes):
        block = pcm[offset:offset + feed_bytes]
        audio_t = (offset + len(block)) / 2 / SAMPLE_RATE
        for window in buffer.feed(block):
            pending.append((audio_t, window))
    leftover = buffer.flush()
    if leftover:
        pending.append((res.audio_s, leftover))

    # Virtual clock: the model is busy while it transcribes, and any window
    # that arrived during that time is stale — the live LIFO queue keeps only
    # the newest one. This reproduces the app's real drop rate.
    t0 = time.monotonic()
    clock = 0.0
    virtual.now = 0.0
    i = 0
    while i < len(pending):
        arrive, window = pending[i]
        clock = max(clock, arrive)

        newest = i
        while newest + 1 < len(pending) and pending[newest + 1][0] <= clock:
            newest += 1
        res.dropped += newest - i
        arrive, window = pending[newest]
        i = newest + 1

        text, asr_ms = transcribe(model, window)
        res.chunks += 1
        res.asr_ms.append(asr_ms)
        clock += asr_ms / 1000.0
        virtual.now = clock

        if not text:
            res.empty += 1
            continue

        res.transcripts.append(text)
        context = (tracker.last_surah, tracker.last_ayah, tracker.last_word_index)
        decision = match_for_mode(index, text, tracker.mode, context)
        from src.core.debug import log as _dbg
        if _dbg.enabled:
            _dbg.asr(text, asr_ms)
            _dbg.match(decision.mode, context, decision.match, decision.skip_reason)
        tracker.on_result({
            "text": text,
            "match": decision.match,
            "mode": tracker.mode,
            "attempted": decision.attempted,
        })
        if decision.match is not None:
            res.found_position = True

    tracker.finalize()
    res.wall_s = time.monotonic() - t0

    # Score the final state of the page
    lo, hi = exp.ayahs
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
                    f"{surah}:{ayah}:{word_index}  heard={w.recited!r} ref={w.reference!r}"
                )
            else:
                res.wrong_words.append(f"{surah}:{ayah}:{word_index}")
        else:
            res.missed += 1

    if verbose:
        print(f"\n    transcripts ({len(res.transcripts)}):")
        for t in res.transcripts:
            print(f"      {t}")

    return res


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

def report(results: list[Result]):
    print()
    print("=" * 96)
    print("  RESULTS")
    print("=" * 96)
    print(f"  {'recording':<40} {'position':>9} {'ok':>5} {'caught':>7} "
          f"{'miss':>5} {'false':>6} {'drop%':>6}")
    print("  " + "-" * 92)

    for r in results:
        pos = "FOUND" if r.found_position else "LOST"
        total_windows = r.chunks + r.dropped
        drop = 100 * r.dropped / total_windows if total_windows else 0
        flag = "  <-- FALSE ALARMS" if r.false_alarms else ""
        if r.expectation.mistakes and r.caught < r.expectation.mistake_count:
            flag += "  <-- MISSED A REAL MISTAKE"
        name = r.name[:38].replace(".flac", "")
        caught = (f"{r.caught}/{r.expectation.mistake_count}"
                  if r.expectation.mistakes else "-")
        print(f"  {name:<40} {pos:>9} {r.ok:5} {caught:>7} {r.missed:5} "
              f"{r.false_alarms:6} {drop:5.0f}%{flag}")

    print("  " + "-" * 92)

    flagged = [r for r in results if r.wrong_words]
    if flagged:
        print()
        print("  WORDS MARKED WRONG:")
        for r in flagged:
            tag = (f"(expects {r.expectation.mistake_count} real)"
                   if r.expectation.mistakes else "")
            print(f"    {r.name.replace('.flac','')[:52]:<54}{tag}")
            for w in r.wrong_words:
                print(f"       {w}")

    total_ok = sum(r.ok for r in results)
    total_wrong = sum(r.wrong for r in results)
    total_missed = sum(r.missed for r in results)
    total_false = sum(r.false_alarms for r in results)
    total_scored = total_ok + total_wrong
    found = sum(1 for r in results if r.found_position)
    expected_mistakes = sum(r.expectation.mistake_count for r in results)
    caught_mistakes = sum(r.caught for r in results)
    all_asr = [ms for r in results for ms in r.asr_ms]
    windows = sum(r.chunks + r.dropped for r in results)
    dropped = sum(r.dropped for r in results)
    audio = sum(r.audio_s for r in results)
    wall = sum(r.wall_s for r in results)

    print()
    print(f"  position found          {found}/{len(results)} recordings")
    print(f"  words scored            {total_scored}  (ok={total_ok} wrong={total_wrong} "
          f"missed={total_missed})")
    if total_scored:
        print(f"  FALSE ALARM RATE        {total_false}/{total_scored} = "
              f"{100 * total_false / total_scored:.1f}%   <-- the number to drive down")
    print(f"  DELIBERATE MISTAKES     {caught_mistakes}/{expected_mistakes} caught"
          f"   <-- must stay at {expected_mistakes}/{expected_mistakes}")
    if all_asr:
        print(f"  asr latency             mean={np.mean(all_asr):.0f}ms "
              f"median={np.median(all_asr):.0f}ms")
    if windows:
        print(f"  windows                 {windows} produced, {dropped} dropped "
              f"({100 * dropped / windows:.0f}%)")
    print(f"  audio {audio:.0f}s processed in {wall:.0f}s "
          f"({wall / audio:.2f}x real time)" if audio else "")
    print("=" * 96)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="substring filter on the recording name")
    ap.add_argument("--verbose", action="store_true", help="print every transcript")
    ap.add_argument("--debug-log", help="write a full session log to this path")
    ap.add_argument("--model", default=MODEL_DIR, help="model directory to benchmark")
    args = ap.parse_args()

    if args.debug_log:
        from src.core.debug import log as debug_log
        debug_log.enable(args.debug_log)

    if not RECORDS_DIR.exists():
        print(f"No recordings at {RECORDS_DIR}")
        return 1

    print("Loading Quran index…")
    index = QuranIndex()
    page_map = PageMap()

    device, compute_type = resolve_device(DEVICE, USE_FP16)
    print(f"Loading Whisper from {args.model} on {device}/{compute_type} …")
    from faster_whisper import WhisperModel
    model = WhisperModel(
        args.model,
        device=device,
        compute_type=compute_type,
        local_files_only=True,
    )

    results = []
    for exp in EXPECTATIONS:
        matches = [p for p in sorted(RECORDS_DIR.glob("*.flac"))
                   if exp.pattern in p.name]
        if not matches:
            print(f"  (no file for '{exp.pattern}')")
            continue
        path = matches[0]
        if args.only and args.only not in path.name:
            continue

        label = f"{exp.surah}:{exp.ayahs[0]}"
        if exp.ayahs[0] != exp.ayahs[1]:
            label += f"-{exp.ayahs[1]}"
        print(f"  {path.name[:60]:<62} expect {label}", flush=True)
        results.append(run_recording(model, index, page_map, path, exp, args.verbose))

    if results:
        report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
