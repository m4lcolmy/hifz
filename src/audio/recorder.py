"""Turn a live session into benchmark cases.

The benchmark has eleven recordings because eleven were recorded by hand, and
every bug fixed so far was found by reciting rather than by testing: the
evidence was sitting in a log that could not be replayed with its audio, so no
test could be written and the bug shipped. A session that is not recorded is a
test case thrown away.

What this writes:

    logs/session-20260919-131925/
      session.log                        symlink to the run's debug log
      full.flac                          the whole session, uncut
      000_before-lock.flac               everything before discovery answered
      001_4-1_ياايها-الناس.flac          one clip per ayah the tracker believed
      002_4-2_واتوا-اليتامي.flac
      manifest.json                      labels, spans, verdicts, transcriptions

Clips are cut on the tracker's **own** ayah transitions, so each one is
labelled with the ayah the app believed it was in. Where that belief was wrong
the clip is worth more, not less — it is the failing case, already isolated and
already labelled with the wrong answer the app gave.

The segment before the first lock gets its own clip. Discovery struggles most
there and it is the part no hand-made recording ever captures, because a person
recording a test case starts the recitation cleanly.
"""

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from src.config import SAMPLE_RATE
from src.core.debug import log


def _slug(text: str, words: int = 3) -> str:
    """A short, filename-safe tag from Arabic text.

    Diacritics are dropped and spaces become dashes. The tag is for a human
    scanning a directory listing; the manifest carries the exact reference.
    """
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(c)
    )
    keep = [w for w in stripped.split()[:words] if w]
    out = "-".join(keep)
    for bad in '/\\:*?"<>|':
        out = out.replace(bad, "")
    return out or "ayah"


@dataclass
class _Segment:
    """One stretch of audio the tracker attributed to one place."""

    start_sample: int
    surah: int | None
    ayah: int | None
    end_sample: int = 0
    transcriptions: list[tuple[float, str]] = field(default_factory=list)


class SessionRecorder:
    """Keeps the session's audio and cuts it where the tracker moved.

    Audio is held in memory and written once on stop. A session is minutes of
    16 kHz mono — about 2 MB per minute — so this costs less than the risk of
    interleaving disk writes with the microphone callback.
    """

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self._pcm = bytearray()
        self._segments: list[_Segment] = []
        self._clips: list[dict] = []

    # ── Recording ──────────────────────────────────────────────────────

    @property
    def samples(self) -> int:
        return len(self._pcm) // 2

    def feed(self, raw: bytes):
        """Every byte from the microphone, before the VAD sees it."""
        self._pcm.extend(raw)

    def note(self, window, surah, ayah, text: str):
        """Where the tracker believed it was after hearing `window`.

        Called once per transcription, with the tracker's position *after* the
        result was absorbed. A change of ayah opens a new segment starting at
        the window that caused it, so a clip begins with the audio that first
        convinced the app, not with the moment it finished deciding.
        """
        here = (surah, ayah)
        if not self._segments or (self._segments[-1].surah,
                                  self._segments[-1].ayah) != here:
            start = window.start_sample if window is not None else self.samples
            # Adjacent clips overlap, and that is deliberate. A window is 3s
            # wide, so the tracker steps into the next ayah while the current
            # one is still being recited — cutting each clip where the next
            # begins lopped the end off every ayah (97:1 came out 0.9s long
            # for five words). Each clip instead spans the windows attributed
            # to it, end to end, and no ayah is truncated.
            if surah is not None and not any(
                    seg.surah is not None for seg in self._segments):
                # The first ayah located is also the one recited during
                # discovery, and the pre-lock re-match scores it back to its
                # first word — so its audio starts where the session does.
                start = 0
            self._segments.append(_Segment(start, surah, ayah))

        seg = self._segments[-1]
        if window is not None:
            seg.end_sample = max(seg.end_sample, window.end_sample)
            if text:
                seg.transcriptions.append((round(window.start_s, 3), text))

    # ── Writing ────────────────────────────────────────────────────────

    def finish(self, verdicts: list[dict], log_path=None) -> Path | None:
        """Write the audio, the clips and the manifest. Returns the directory."""
        if not self._pcm:
            return None

        self.dir.mkdir(parents=True, exist_ok=True)
        total = self.samples
        if self._segments:
            self._segments[-1].end_sample = max(
                self._segments[-1].end_sample, total
            )

        self._write_flac(self.dir / "full.flac", 0, total)

        by_ayah: dict[tuple[int, int], list[dict]] = {}
        for v in verdicts:
            by_ayah.setdefault((v["surah"], v["ayah"]), []).append(v)

        self._clips = []
        for i, seg in enumerate(self._segments):
            start = max(0, min(seg.start_sample, total))
            end = max(start, min(seg.end_sample, total))
            if end - start < SAMPLE_RATE // 4:      # under 250 ms is not a clip
                continue

            if seg.surah is None:
                name = f"{i:03d}_before-lock.flac"
                words = []
            else:
                ref = " ".join(v["reference"] for v in
                               by_ayah.get((seg.surah, seg.ayah), []))
                name = (f"{i:03d}_{seg.surah}-{seg.ayah}"
                        f"_{_slug(ref)}.flac")
                words = by_ayah.get((seg.surah, seg.ayah), [])

            self._write_flac(self.dir / name, start, end)
            self._clips.append({
                "file": name,
                "surah": seg.surah,
                "ayah": seg.ayah,
                "start_s": round(start / SAMPLE_RATE, 3),
                "end_s": round(end / SAMPLE_RATE, 3),
                "transcriptions": seg.transcriptions,
                "verdicts": words,
            })

        if log_path is not None:
            link = self.dir / "session.log"
            try:
                if link.is_symlink() or link.exists():
                    link.unlink()
                link.symlink_to(Path(log_path).resolve())
            except OSError:
                pass

        manifest = {
            "sample_rate": SAMPLE_RATE,
            "duration_s": round(total / SAMPLE_RATE, 3),
            "log": str(log_path) if log_path else None,
            "clips": self._clips,
        }
        (self.dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.event("RECORD", f"{len(self._clips)} clip(s) -> {self.dir}")
        return self.dir

    def _write_flac(self, path: Path, start_sample: int, end_sample: int):
        """Encode [start, end) of the session to FLAC.

        PyAV is already a dependency for decoding the benchmark recordings, so
        recording adds nothing new to install.
        """
        import av
        import numpy as np

        pcm = np.frombuffer(
            bytes(self._pcm[start_sample * 2:end_sample * 2]), dtype="<i2"
        )
        if pcm.size == 0:
            return

        with av.open(str(path), "w") as container:
            stream = container.add_stream("flac", rate=SAMPLE_RATE)
            stream.layout = "mono"
            stream.format = "s16"
            # One frame per second keeps peak memory flat on a long session.
            for offset in range(0, pcm.size, SAMPLE_RATE):
                block = pcm[offset:offset + SAMPLE_RATE]
                frame = av.AudioFrame.from_ndarray(
                    block.reshape(1, -1), format="s16", layout="mono"
                )
                frame.rate = SAMPLE_RATE
                frame.pts = offset
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
