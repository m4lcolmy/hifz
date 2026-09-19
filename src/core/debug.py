"""Session debug log — records the whole pipeline for post-mortem analysis.

On by default: every run writes ``logs/hifz-YYYYmmdd-HHMMSS.log`` so a session
that went wrong can always be looked at afterwards, rather than being lost
because nobody thought to pass a flag. The newest run is also linked as
``hifz-session.log``. Old logs are pruned to the most recent
``DEBUG_LOG_KEEP``. Turn it off with ``--no-debug`` or ``HIFZ_DEBUG=0``.

Every stage appends a line:

    [  12.431s] AUDIO    chunk=17 dur=3.00s dropped_stale=4
    [  12.980s] ASR      lat=549ms text="بسم الله الرحمن الرحيم"
    [  12.981s] MATCH    mode=discovery ctx=none -> NO MATCH
    [  15.204s] MATCH    mode=discovery ctx=none -> 1:4 offset=0
    [  15.204s] WORDS    1:4  ok=1 diac=2 wrong=0 gap=0
                   OK    1:4:0 recited='مَالِكِ' ref='مَالِكِ'
                   DIAC  1:4:1 recited='يوم' ref='يَوْمِ'
    [  15.205s] TRACKER  discovery -> tracking @ 1:4:2

The distinction that matters most is DIAC vs WRONG.  DIAC means the reciter
said the right word but the diacritics did not match — almost always the ASR
model failing to vocalize, not a recitation mistake.  WRONG means a genuinely
different word.  The end-of-session summary totals both, which is what tells
you whether a bad session is the model's fault or the matching logic's.
"""

import os
import threading
import time
from collections import Counter
from pathlib import Path

from src.config import DEBUG_LOG_DIR, DEBUG_LOG_KEEP
from src.core.arabic import normalize


class DebugLog:
    """Append-only session log. Thread-safe — the ASR worker writes too."""

    def __init__(self):
        self._enabled = False
        self._fh = None
        self._path: Path | None = None
        self._t0 = time.monotonic()
        self._lock = threading.Lock()
        self.counters: Counter = Counter()

    # ── Lifecycle ──────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def path(self) -> Path | None:
        return self._path

    def enable(self, path: str | Path):
        """Open the log file and start recording."""
        self._path = Path(path)
        self._fh = open(self._path, "w", encoding="utf-8", buffering=1)
        self._enabled = True
        self._t0 = time.monotonic()
        self.counters.clear()
        self._raw(f"# Hifz session log — {time.strftime('%Y-%m-%d %H:%M:%S')}")
        self._raw(f"# {'-' * 70}")

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._enabled = False

    # ── Writing ────────────────────────────────────────────────────────

    def _raw(self, line: str):
        if self._fh is None:
            return
        with self._lock:
            self._fh.write(line + "\n")

    def event(self, kind: str, message: str):
        """Write one timestamped event line."""
        if not self._enabled:
            return
        elapsed = time.monotonic() - self._t0
        self._raw(f"[{elapsed:8.3f}s] {kind:<8} {message}")

    def detail(self, message: str):
        """Write an indented continuation line under the previous event."""
        if not self._enabled:
            return
        self._raw(f"{'':>11} {message}")

    def count(self, name: str, n: int = 1):
        if self._enabled:
            self.counters[name] += n

    # ── Stage helpers ──────────────────────────────────────────────────

    def audio_chunk(self, index: int, num_bytes: int, sample_rate: int, dropped: int):
        self.count("audio_chunks")
        self.count("audio_dropped", dropped)
        duration = num_bytes / 2 / sample_rate
        self.event("AUDIO", f"chunk={index} dur={duration:.2f}s dropped_stale={dropped}")

    def asr(self, text: str, latency_ms: float, info=None):
        self.count("asr_calls")
        extra = ""
        if info is not None:
            prob = getattr(info, "language_probability", None)
            if prob is not None:
                extra = f" langp={prob:.2f}"
            duration = getattr(info, "duration", None)
            if duration is not None:
                extra += f" audio={duration:.2f}s"
        if not text:
            self.count("asr_empty")
            self.event("ASR", f"lat={latency_ms:.0f}ms <empty>{extra}")
        else:
            self.event("ASR", f'lat={latency_ms:.0f}ms text="{text}"{extra}')

    def match(self, mode: str, context, match, skipped_reason: str | None = None):
        ctx = "none" if context[0] is None else f"{context[0]}:{context[1]}:{context[2]}"
        if skipped_reason:
            self.count(f"{mode}_skipped")
            self.event("MATCH", f"mode={mode} ctx={ctx} -> SKIPPED ({skipped_reason})")
            return

        self.count(f"{mode}_attempts")
        if match is None:
            self.count(f"{mode}_misses")
            self.event("MATCH", f"mode={mode} ctx={ctx} -> NO MATCH")
            return

        self.count(f"{mode}_hits")
        self.event(
            "MATCH",
            f"mode={mode} ctx={ctx} -> {match.surah_id}:{match.ayah_id} "
            f"offset={match.start_offset}",
        )
        self._words(match)

    def _words(self, match):
        """Break down every word verdict, classifying wrong words."""
        verdicts = []
        for w in match.words:
            verdicts.append((self.classify(w), w))

        tally = Counter(v for v, _ in verdicts)
        for name in ("ok", "diac", "wrong", "missed", "gap", "extra"):
            self.count(f"word_{name}", tally.get(name, 0))

        self.event(
            "WORDS",
            f"{match.surah_id}:{match.ayah_id}  "
            + " ".join(f"{k}={tally[k]}" for k in sorted(tally)),
        )
        for verdict, w in verdicts:
            pos = (
                f"{w.surah_id}:{w.ayah_id}:{w.reference_index}"
                if w.surah_id is not None
                else "-"
            )
            self.detail(
                f"{verdict.upper():<6} {pos:<12} "
                f"recited={w.recited or '—'!r} ref={w.reference or '—'!r}"
            )

    @staticmethod
    def classify(word) -> str:
        """Label a WordResult so the log distinguishes ASR noise from mistakes.

        ok      — matched including diacritics
        diac    — same word, diacritics differ (usually the ASR under-vocalizing)
        wrong   — a genuinely different word
        gap     — reference word the reciter skipped over
        missed  — reference word with nothing recited against it
        extra   — something recited that is not in the Quran here
        """
        if word.is_gap_filled:
            return "gap"
        if word.is_correct:
            return "ok"
        if not word.recited:
            return "missed"
        if not word.reference:
            return "extra"
        if normalize(word.recited) == normalize(word.reference):
            return "diac"
        return "wrong"

    def tracker(self, message: str):
        self.event("TRACKER", message)

    def mushaf(self, message: str):
        self.event("MUSHAF", message)

    # ── Summary ────────────────────────────────────────────────────────

    def summary(self):
        """Write the end-of-session totals and a plain-language verdict."""
        if not self._enabled:
            return

        c = self.counters
        elapsed = time.monotonic() - self._t0
        scored = c["word_ok"] + c["word_diac"] + c["word_wrong"]

        self._raw("")
        self._raw("=" * 72)
        self._raw("  SESSION SUMMARY")
        self._raw("=" * 72)
        self._raw(f"  duration          {elapsed:.1f}s")
        self._raw(
            f"  audio             chunks={c['audio_chunks']} "
            f"stale_dropped={c['audio_dropped']}"
        )
        self._raw(
            f"  asr               calls={c['asr_calls']} empty={c['asr_empty']} "
            f"errors={c['asr_errors']}"
        )
        self._raw(
            f"  discovery         attempts={c['discovery_attempts']} "
            f"hits={c['discovery_hits']} misses={c['discovery_misses']} "
            f"skipped={c['discovery_skipped']}"
        )
        self._raw(
            f"  tracking          attempts={c['tracking_attempts']} "
            f"hits={c['tracking_hits']} misses={c['tracking_misses']} "
            f"skipped={c['tracking_skipped']}"
        )
        self._raw(
            f"  mode changes      locked={c['tracker_locked']} "
            f"fallbacks={c['tracker_fallback']}"
        )
        self._raw(
            f"  mushaf            pages={c['mushaf_pages']} "
            f"highlights={c['mushaf_hits']} OFF-PAGE={c['mushaf_misses']}"
        )
        self._raw("")

        if scored:
            self._raw(
                f"  words scored      {scored}   "
                f"ok={c['word_ok']} ({100 * c['word_ok'] / scored:.0f}%)  "
                f"diac={c['word_diac']} ({100 * c['word_diac'] / scored:.0f}%)  "
                f"wrong={c['word_wrong']} ({100 * c['word_wrong'] / scored:.0f}%)"
            )
            self._raw(
                f"  also              skipped={c['word_gap']} "
                f"missed={c['word_missed']} extra={c['word_extra']}"
            )
        else:
            self._raw("  words scored      0 — nothing was ever matched")

        self._raw("")
        for line in self._verdicts(scored):
            self._raw(f"  >>> {line}")
        self._raw("=" * 72)

    def _verdicts(self, scored: int) -> list[str]:
        """Turn the counters into the conclusions worth acting on."""
        c = self.counters
        out = []

        if c["asr_calls"] == 0:
            out.append("ASR never ran — no audio reached the transcriber (mic/VAD problem).")
            return out

        if c["asr_empty"] > c["asr_calls"] * 0.5:
            out.append(
                f"{c['asr_empty']}/{c['asr_calls']} transcriptions came back EMPTY — "
                "mic level too low, or VAD is emitting silence."
            )

        if scored == 0:
            if c["discovery_attempts"] and not c["discovery_hits"]:
                out.append(
                    "Discovery never found a unique position. Check the ASR text above — "
                    "if it looks like correct Quran, the phrases were ambiguous; "
                    "if it looks like nonsense, the model is the problem."
                )
            return out or ["Nothing was matched — see the ASR lines above."]

        if c["word_diac"] > c["word_ok"]:
            out.append(
                f"MOST words are diacritics-only mismatches ({c['word_diac']} vs {c['word_ok']} ok). "
                "The reciting was right; the model is not producing correct tashkeel. "
                "This is a MODEL problem, not a matching problem."
            )
        elif c["word_diac"] > scored * 0.15:
            out.append(
                f"{100 * c['word_diac'] / scored:.0f}% of words differ only in diacritics — "
                "the model is under-vocalizing. Expect false 'mistakes'."
            )

        if c["mushaf_misses"]:
            out.append(
                f"{c['mushaf_misses']} highlights fell OFF-PAGE — a word/position alignment bug. "
                "Search the log for 'OFF-PAGE' to see which ayahs."
            )

        if c["tracker_fallback"]:
            out.append(
                f"Tracking was lost {c['tracker_fallback']} time(s) and fell back to discovery. "
                "Look at the TRACKER lines for where."
            )

        if c["tracking_attempts"] and not c["tracker_locked"]:
            out.append("Tracking mode never engaged — the position pointer is not advancing.")

        if not out:
            out.append("Nothing anomalous — pipeline behaved as designed.")
        return out


# Module-level singleton — import and use directly.
log = DebugLog()


def _prune_old_logs(directory, keep: int):
    """Keep only the newest `keep` session logs."""
    try:
        logs = sorted(
            (p for p in directory.glob("hifz-*.log") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in logs[keep:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass  # logging must never take the app down


def enable_from_env_or_args(argv: list[str]) -> bool:
    """Start the session log unless it was explicitly turned off.

    Logging on by default is the point: the sessions worth investigating are
    the ones nobody expected, and asking for a flag after the fact means the
    evidence is already gone.

    Returns True if logging was enabled.
    """
    if "--no-debug" in argv or os.environ.get("HIFZ_DEBUG") == "0":
        return False

    # An explicit path always wins
    path = os.environ.get("HIFZ_DEBUG_LOG")
    if not path:
        for arg in argv:
            if arg.startswith("--debug="):
                path = arg.split("=", 1)[1]
                break

    if path:
        log.enable(path)
        return True

    directory = DEBUG_LOG_DIR
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        directory = Path(".")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = directory / f"hifz-{stamp}.log"
    log.enable(target)
    _prune_old_logs(directory, DEBUG_LOG_KEEP)

    # Convenience pointer to the newest run
    try:
        latest = Path("hifz-session.log")
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(target.resolve())
    except OSError:
        pass

    return True
