"""Replay a session log through the matcher and tracker.

A session log records every transcription the model produced and when. That is
enough to re-run everything downstream of the model — matching, tracking,
scoring, page highlighting — without the audio. So a session that went wrong
can be replayed against changed code and the difference measured, instead of
being re-recited and hoped for.

It does not exercise the microphone, the VAD or Whisper. For that use
scripts/benchmark_recordings.py, which needs the audio.

    python scripts/replay_session.py logs/hifz-20260919-122520.log
    python scripts/replay_session.py logs/latest.log --expect 1:1-7
"""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.matching import match_for_mode
from src.core.page_map import PageMap
from src.core.quran import QuranIndex
from src.ui.recitation import RecitationTracker

ASR_LINE = re.compile(r'^\[\s*([\d.]+)s\]\s+ASR\s+.*?text="(.*)"$')
ASR_EMPTY = re.compile(r'^\[\s*([\d.]+)s\]\s+ASR\s+.*<empty>')


class ReplayClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Board:
    """Stands in for the Mushaf view; keeps the final colour per word."""

    def __init__(self):
        self.current_page_num = None
        self.page_serial = 0
        self.final: dict[tuple[int, int, int], bool | None] = {}
        self.pages: list[int] = []
        self.revealed: list[tuple[int, int, int]] = []
        self.basmalas: list[int] = []

    def load_page(self, page_num: int):
        self.current_page_num = page_num
        self.page_serial += 1
        self.pages.append(page_num)

    def update_recitation(self, surah, ayah, word_index, status):
        self.final[(surah, ayah, word_index)] = status

    def reveal(self, surah: int, ayah: int, word_index: int) -> bool:
        """Shown, with no verdict attached — and any verdict it was carrying
        comes off the page with it, as the real view does."""
        self.revealed.append((surah, ayah, word_index))
        self.final.pop((surah, ayah, word_index), None)
        return True

    def paint_basmala(self, surah: int, status: bool = True) -> bool:
        """The real view answers False where the page carries no bismillah
        line for that surah; here every page is assumed to carry one, so
        callers are exercised on the path that does something."""
        self.basmalas.append(surah)
        return True


def read_transcriptions(path: Path) -> list[tuple[float, str]]:
    """Every transcription in the log, with its timestamp."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ASR_LINE.match(line)
        if m:
            out.append((float(m.group(1)), m.group(2)))
            continue
        m = ASR_EMPTY.match(line)
        if m:
            out.append((float(m.group(1)), ""))
    return out


def parse_expect(text: str | None):
    """'1:1-7' -> (1, 1, 7)."""
    if not text:
        return None
    m = re.fullmatch(r"(\d+):(\d+)(?:-(\d+))?", text)
    if not m:
        raise SystemExit(f"--expect must look like 1:1-7, got {text!r}")
    surah, first = int(m.group(1)), int(m.group(2))
    return surah, first, int(m.group(3) or first)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("--expect", help="ayah range that was recited, e.g. 1:1-7")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not args.log.exists():
        raise SystemExit(f"no such log: {args.log}")

    transcriptions = read_transcriptions(args.log)
    if not transcriptions:
        raise SystemExit("no ASR lines in that log")

    index = QuranIndex()
    board = Board()
    clock = ReplayClock()
    tracker = RecitationTracker(board, PageMap(), index, clock=clock)

    locks = fallbacks = attempts = hits = 0
    first_highlight_at = None
    first_speech_at = None
    previous_mode = tracker.mode

    for timestamp, text in transcriptions:
        clock.now = timestamp
        if text and first_speech_at is None:
            first_speech_at = timestamp
        if not text:
            continue

        context = (tracker.last_surah, tracker.last_ayah, tracker.last_word_index)
        decision = match_for_mode(index, text, tracker.mode, context)
        if decision.attempted:
            attempts += 1
        if decision.match is not None:
            hits += 1

        before = len(board.revealed) + len(board.final)
        tracker.on_result({
            "text": text,
            "match": decision.match,
            "mode": tracker.mode,
            "attempted": decision.attempted,
        })
        # The app's settle timer, on the same simulated clock. Without it a
        # replay would hold verdicts the live session had already shown.
        tracker.tick()
        # Anything appearing on the page, not only a colour: a word is
        # uncovered as soon as it is heard and coloured once its verdict can
        # no longer change, so counting colours alone would now report the
        # page as blank for the first several seconds of every session.
        if first_highlight_at is None and len(board.revealed) + len(board.final) > before:
            first_highlight_at = timestamp

        if previous_mode == "discovery" and tracker.mode == "tracking":
            locks += 1
        elif previous_mode == "tracking" and tracker.mode == "discovery":
            fallbacks += 1
        previous_mode = tracker.mode

        if args.verbose:
            where = ("-" if decision.match is None
                     else f"{decision.match.surah_id}:{decision.match.ayah_id}")
            print(f"  [{timestamp:7.3f}s] {tracker.mode:<9} {where:>7}  {text}")

    tracker.finalize()

    print()
    print("=" * 72)
    print(f"  REPLAY  {args.log.name}")
    print("=" * 72)
    print(f"  transcriptions      {len(transcriptions)} "
          f"({sum(1 for _, t in transcriptions if not t)} empty)")
    print(f"  match attempts      {attempts}, hits {hits}")
    print(f"  locked / lost       {locks} / {fallbacks}")
    if first_speech_at is not None and first_highlight_at is not None:
        print(f"  first word          {first_speech_at:.1f}s")
        print(f"  first highlight     {first_highlight_at:.1f}s "
              f"({first_highlight_at - first_speech_at:.1f}s later)")
    elif first_highlight_at is None:
        print("  first highlight     NEVER")

    by_ayah: dict[tuple[int, int], int] = {}
    for (surah, ayah, _), _status in board.final.items():
        by_ayah[(surah, ayah)] = by_ayah.get((surah, ayah), 0) + 1

    print(f"  words shown         {len(board.final)}")
    print(f"  pages loaded        {board.pages or '-'}")

    expect = parse_expect(args.expect)
    if expect:
        surah, first, last = expect
        print()
        print(f"  expected {surah}:{first}-{last}")
        missing = []
        for ayah in range(first, last + 1):
            total = sum(1 for k in index._pos_map if k[0] == surah and k[1] == ayah)
            shown = by_ayah.get((surah, ayah), 0)
            mark = "ok " if shown else "NOT SHOWN"
            if not shown:
                missing.append(ayah)
            print(f"    {surah}:{ayah:<3} {shown:>3}/{total:<3} words   {mark}")
        print()
        if missing:
            print(f"  >>> {len(missing)} ayah(s) recited but never shown: "
                  + ", ".join(f"{surah}:{a}" for a in missing))
        else:
            print("  >>> every expected ayah appeared")
    else:
        print()
        for (surah, ayah), n in sorted(by_ayah.items()):
            print(f"    {surah}:{ayah:<4} {n} words")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
