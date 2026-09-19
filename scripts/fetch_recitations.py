"""Fetch a benchmark corpus from published recitations.

The hand-made corpus is eleven recordings by one reciter, and Tier D found
that **two of the eleven were labelled with ayahs they do not contain** — the
labels are typed by hand, so they were wrong at a rate nobody was measuring.
Published recitations fix both at once: the recitation is correct by
construction, and the ground truth comes from the ayah number in the URL
rather than from someone's memory.

    python scripts/fetch_recitations.py --plan      # what it would fetch
    python scripts/fetch_recitations.py --agree     # fetch it
    python scripts/benchmark_recordings.py --from-recitations

**These are somebody else's recordings.** A research corpus is not a licence
to redistribute, so the audio never enters git: `--plan` writes a manifest of
ayah numbers, which is what is version controlled, and `--agree` fetches the
audio into a gitignored directory that can be deleted and rebuilt. The source
and its terms are printed before anything is downloaded and `--agree` is
required, the way `fetch_ctc_model.py` requires it for a model licence.

**And this corpus cannot measure the thing that matters most.** A correct
recitation contains no mistakes, so everything here moves coverage and false
alarms and nothing else. `DELIBERATE MISTAKES 2/2` still comes only from a
human reciting one wrong on purpose. The hand-made recordings do not become
less important when this lands; they become the only source of the one
measurement a corpus of correct recitation can never produce.

What is in it and why is in `recitation_corpus.py`, which is pure and needs
no network. This file is the download.
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.recitation_corpus import (
    JUZ30_MANIFEST_PATH, MANIFEST_PATH, RECITATIONS_DIR, RECITERS, RUN_GAP_MS,
    SEED, SOURCE, UNAVAILABLE_PATH, Case, all_cases, build_corpus,
    build_juz30_corpus, build_juz30_manifest, build_manifest, decodes,
    load_pcm16, write_manifest,
)
import json
from src.config import SAMPLE_RATE

TIMEOUT = 30
RETRIES = 3
WORKERS = 6


def part_url(reciter: str, surah: int, part: int) -> str:
    """everyayah serves `<surah:03d><ayah:03d>.mp3`; ayah 000 is the basmala."""
    return f"{SOURCE['base_url']}/{reciter}/{surah:03d}{part:03d}.mp3"


# ═══════════════════════════════════════════════════════════════════════
# The plan
# ═══════════════════════════════════════════════════════════════════════

def show_plan(strata, manifest_path: Path, flags: str = ""):
    cases = all_cases(strata)
    ayahs = sum(c.last_ayah - c.first_ayah + 1 for c in cases)
    parts = sum(max(1, len(c.parts)) for c in cases)

    print()
    print("=" * 78)
    print("  SOURCE")
    print("=" * 78)
    print(f"  {SOURCE['name']}  {SOURCE['base_url']}")
    print(f"  terms: {SOURCE['terms_url']}")
    for line in _wrap(SOURCE["terms"], 72):
        print(f"    {line}")

    print()
    print("=" * 78)
    print("  RECITERS — chosen for pace, because the whole hand-made corpus")
    print("             is one voice at one speed")
    print("=" * 78)
    for r in RECITERS:
        print(f"  {r.label:<26} {r.pace}")

    print()
    print("=" * 78)
    print("  STRATA — deliberately not uniform")
    print("=" * 78)
    # Wide enough for the longest stratum name there is; a name that
    # overflows its column pushes every number on that row out of line, and
    # the point of the table is comparing the numbers.
    name_w = max(12, *(len(s.name) for s in strata))
    print(f"  {'stratum':<{name_w}} {'files':>6} {'ayahs':>6} {'target':>7}   why")
    print("  " + "-" * 74)
    for s in strata:
        n_ayahs = sum(c.last_ayah - c.first_ayah + 1 for c in s.cases)
        why = _wrap(s.why, 40)
        print(f"  {s.name:<{name_w}} {len(s.cases):6} {n_ayahs:6} {s.target:7}   {why[0]}")
        for line in why[1:]:
            print(f"  {'':<{name_w}} {'':>6} {'':>6} {'':>7}   {line}")
    print("  " + "-" * 74)
    print(f"  {'TOTAL':<{name_w}} {len(cases):6} {ayahs:6}")
    print()
    print(f"  {parts} source files, roughly {parts * 90 // 1024} MB")
    print(f"  seed {SEED} (committed — the corpus is reproducible)")
    print(f"  runs joined with {RUN_GAP_MS} ms of silence between ayahs")
    print()
    print(f"  manifest written → {manifest_path.relative_to(PROJECT_ROOT)}")
    print(f"  audio would go   → {RECITATIONS_DIR.relative_to(PROJECT_ROOT)}/"
          f"<reciter>/  (gitignored)")
    print()
    print("  Read the terms above, then:")
    print(f"    python scripts/fetch_recitations.py {flags}--agree")
    print()


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out or [""]


# ═══════════════════════════════════════════════════════════════════════
# The download
# ═══════════════════════════════════════════════════════════════════════

class SourceCorrupt(RuntimeError):
    """The bytes arrived and are not audio. Retrying will not help."""


class Fetcher:
    def __init__(self, dest: Path, force: bool = False):
        import requests

        self.dest = dest
        self.force = force
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "hifz-benchmark-corpus (research; audio not redistributed)"
        )
        self.missing_basmala: list[str] = []
        self.failed: list[tuple[str, str]] = []
        #: Cases the source cannot supply. Distinct from `failed`, which is
        #: anything that might work next time.
        self.unavailable: dict[str, str] = {}
        self.skipped = 0
        self.written = 0

    def get(self, url: str) -> bytes | None:
        """The bytes, or None for a 404 — which is a normal answer here."""
        for attempt in range(RETRIES):
            try:
                r = self.session.get(url, timeout=TIMEOUT)
            except Exception as e:                      # network, not logic
                if attempt == RETRIES - 1:
                    raise RuntimeError(f"{url}: {e}") from e
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None
            if r.ok and r.content:
                return r.content
            if attempt == RETRIES - 1:
                raise RuntimeError(f"{url}: HTTP {r.status_code}")
            time.sleep(1.5 * (attempt + 1))
        return None

    def fetch_case(self, case: Case) -> str:
        out = self.dest / case.filename
        if out.exists() and not self.force:
            self.skipped += 1
            return "skip"

        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            blobs = []
            for part in case.parts:
                data = self.get(part_url(case.reciter, case.surah, part))
                if data is None:
                    if part == 0:
                        # Not every reciter has a separate basmala file:
                        # Al-Husary has none at all and Abdul Basit has 2:0
                        # but not 113:0. The expectation does not depend on
                        # it — the app shows the basmala and never scores it
                        # — so the case is still valid without it.
                        self.missing_basmala.append(f"{case.reciter} {case.surah}")
                        continue
                    raise RuntimeError(
                        f"{case.reciter} {case.surah}:{part} not found — the "
                        f"reciter may not cover it"
                    )
                blobs.append(data)

            if not blobs:
                raise RuntimeError("nothing fetched")

            if len(blobs) == 1 and out.suffix == ".mp3":
                out.write_bytes(blobs[0])
            else:
                self._join(blobs, out)

            # Downloading the bytes is not the same as having the audio.
            # Two of this source's own files are corrupt and arrive complete,
            # matching Content-Length — so the only way to know is to decode
            # it. A file kept without checking is a landmine that goes off in
            # the middle of a run an hour later.
            why = decodes(out)
            if why:
                out.unlink(missing_ok=True)
                raise SourceCorrupt(why)
        except SourceCorrupt as e:
            self.unavailable[case.id] = str(e)
            return "unavailable"
        except Exception as e:
            self.failed.append((case.id, str(e)))
            if out.exists():
                out.unlink()
            return "fail"

        self.written += 1
        return "ok"

    def _join(self, blobs: list[bytes], out: Path):
        """Decode each part and write one FLAC, with a pause between ayahs.

        Decoded and re-encoded rather than concatenated as mp3 frames: glued
        frames leave a click at every seam, and the VAD hears a click as
        speech onset.
        """
        import numpy as np
        import soundfile as sf

        tmp = out.parent / f".{out.name}.part"
        gap = np.zeros(int(SAMPLE_RATE * RUN_GAP_MS / 1000), dtype=np.int16)
        chunks = []
        for i, blob in enumerate(blobs):
            piece = out.parent / f".{out.name}.{i}.mp3"
            piece.write_bytes(blob)
            try:
                pcm = load_pcm16(piece, SAMPLE_RATE)
            finally:
                piece.unlink(missing_ok=True)
            if i:
                chunks.append(gap)
            chunks.append(np.frombuffer(pcm, dtype="<i2"))

        sf.write(str(tmp), np.concatenate(chunks), SAMPLE_RATE, format="FLAC")
        tmp.replace(out)


def fetch_all(cases: list[Case], dest: Path, force: bool) -> Fetcher:
    fetcher = Fetcher(dest, force)
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for _ in pool.map(fetcher.fetch_case, cases):
            done += 1
            if done % 10 == 0 or done == len(cases):
                print(f"\r  {done}/{len(cases)} cases  "
                      f"({fetcher.written} written, {fetcher.skipped} already "
                      f"there, {len(fetcher.unavailable)} unavailable, "
                      f"{len(fetcher.failed)} failed)", end="", flush=True)
    print()
    return fetcher


# ═══════════════════════════════════════════════════════════════════════

def _record_unavailable(fetcher: "Fetcher", dest: Path):
    """Merge this run's unavailable cases into the on-disk record.

    Merged rather than overwritten, because `--only` and `--limit` mean a run
    usually sees part of the corpus and a rewrite would forget the rest.
    """
    path = dest / UNAVAILABLE_PATH.name
    known = {}
    if path.exists():
        try:
            known = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            known = {}
    known.update(fetcher.unavailable)
    if known:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(known, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", action="store_true",
                    help="write the manifest and show what would be fetched")
    ap.add_argument("--agree", action="store_true",
                    help="confirm you have read the source's terms, and fetch")
    ap.add_argument("--only", help="substring filter on the case id "
                                   "(a stratum name, or a reciter)")
    ap.add_argument("--limit", type=int, help="fetch at most this many cases")
    ap.add_argument("--force", action="store_true",
                    help="re-download files that are already there")
    ap.add_argument("--seed", type=int, default=SEED,
                    help=f"corpus seed (default {SEED}, committed — changing "
                         f"it makes every stored number incomparable)")
    ap.add_argument("--dest", type=Path, default=RECITATIONS_DIR)
    ap.add_argument("--manifest", type=Path, default=None,
                    help="where to write the corpus definition (default: the "
                         "one belonging to the corpus being fetched)")
    ap.add_argument(
        "--juz30", action="store_true",
        help="fetch the juz 30 corpus instead: whole short surahs, each from "
             "its basmala, which is the shape of a real session rather than "
             "an ayah lifted out of the middle of one",
    )
    args = ap.parse_args()

    strata = (build_juz30_corpus(args.seed) if args.juz30
              else build_corpus(args.seed))
    manifest_of = build_juz30_manifest if args.juz30 else build_manifest
    if args.manifest is None:
        args.manifest = JUZ30_MANIFEST_PATH if args.juz30 else MANIFEST_PATH

    if not args.agree:
        write_manifest(manifest_of(args.seed), args.manifest)
        show_plan(strata, args.manifest, "--juz30 " if args.juz30 else "")
        if not args.plan:
            print("  Nothing was downloaded. Re-run with --agree.\n")
            return 1
        return 0

    write_manifest(manifest_of(args.seed), args.manifest)

    cases = all_cases(strata)
    if args.only:
        cases = [c for c in cases if args.only in c.id]
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        print("  no cases matched")
        return 1

    print(f"\n  {SOURCE['base_url']} → {args.dest.relative_to(PROJECT_ROOT)}")
    print(f"  {len(cases)} cases\n")
    fetcher = fetch_all(cases, args.dest, args.force)

    if fetcher.missing_basmala:
        print(f"\n  {len(fetcher.missing_basmala)} opening(s) have no separate "
              f"basmala file at this source;")
        print( "  fetched without it. The app never scores the basmala, so "
               "the expectation is unchanged.")
    _record_unavailable(fetcher, args.dest)
    if fetcher.unavailable:
        print(f"\n  {len(fetcher.unavailable)} case(s) the source cannot "
              f"supply — the file arrives complete and is not audio:")
        for case_id, why in sorted(fetcher.unavailable.items()):
            print(f"    {case_id}: {why}")
        print( "  Re-running will not fix these. They are recorded in "
               "unavailable.json so the")
        print( "  benchmark can say 'the corpus has a hole here' rather than "
               "'you have not fetched it'.")
    if fetcher.failed:
        print(f"\n  {len(fetcher.failed)} case(s) failed:")
        for case_id, why in fetcher.failed[:10]:
            print(f"    {case_id}: {why}")
        if len(fetcher.failed) > 10:
            print(f"    … and {len(fetcher.failed) - 10} more")

    measure = ("--from-recitations --juz30" if args.juz30
               else "--from-recitations")
    print(f"\n  Now measure it:\n"
          f"    python scripts/benchmark_recordings.py {measure}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
