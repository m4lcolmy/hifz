"""Download a CTC speech model so it can be benchmarked against Whisper.

Nothing here changes the app. It puts a model on disk; whether it is any good
is decided by the same corpus and the same three numbers as everything else:

    python scripts/fetch_ctc_model.py --model <name>
    python scripts/benchmark_recordings.py --engine ctc

**Licences differ and they matter.** One of the strongest candidates is
non-commercial only. The licence of each is printed before anything is
downloaded and you have to pass --agree to proceed, because picking a model
here is a licensing decision as much as a technical one.

Why CTC at all: Tier E established that the app's remaining false alarms are
acoustically identical to its real errors — the deliberate mistake
فَلَهُمْ/وَلَهُمْ and the false alarm خُوبًا/حُوبًا are the same edit at the same
distance — so no comparison of strings can separate them. What is left is an
engine that mishears less often. A CTC model emits one symbol per audio frame
and has no decoder free to continue a plausible sentence, which is the
mechanism behind the invented words, the confident window-edge fragments and
the echoed tails this codebase works around.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import CTC_MODEL_DIR


CANDIDATES = {
    "fastconformer": {
        "repo": "nvidia/stt_ar_fastconformer_hybrid_large_pcd_v1.0",
        "licence": "CC-BY-4.0 — commercial use allowed",
        "size": "~88 MB",
        "note": "Tilawa's choice. Hybrid CTC; general Arabic, not Quran-tuned. "
                "Needs conversion from NeMo format — see --help-convert.",
    },
    "zipformer": {
        "repo": "Quran-Lab/zipformer_p-arabic-v3",
        "licence": "NPL-1.2 — NON-COMMERCIAL ONLY",
        "size": "~66 MB",
        "note": "Streaming phoneme CTC. 100% recall on Tilawa's corpus. "
                "Phoneme output needs a mapping back to Arabic script.",
    },
    "quran-ctc": {
        "repo": "rabah2026/wav2vec2-large-xlsr-53-arabic-quran-v_final",
        "licence": "Apache-2.0 — commercial use allowed",
        "size": "~1.2 GB",
        "note": "THE ONE TO TRY. Wav2Vec2ForCTC fine-tuned on Quran recitation, "
                "and its vocabulary is fully diacritized — tanween, shadda, "
                "dagger alef, alef wasla — so it feeds the matcher directly "
                "with no phoneme mapping and no conversion.",
    },
    "wav2vec2-arabic": {
        "repo": "jonatasgrosman/wav2vec2-large-xlsr-53-arabic",
        "licence": "Apache-2.0",
        "size": "~1.2 GB",
        "note": "General Arabic, character-level CTC. Not Quran-tuned, but it "
                "is the cheapest way to find out whether CTC helps at all.",
    },
}


def show():
    print("\n  Candidate CTC models\n  " + "-" * 68)
    for name, m in CANDIDATES.items():
        print(f"\n  {name}")
        print(f"    repo     {m['repo']}")
        print(f"    licence  {m['licence']}")
        print(f"    size     {m['size']}")
        print(f"    {m['note']}")
    print("\n  " + "-" * 68)
    print("  Read the licence before downloading, then:")
    print("    python scripts/fetch_ctc_model.py --model quran-ctc --agree")
    print("    python scripts/benchmark_recordings.py --engine ctc\n")
    print("  A model only stays if it beats Whisper on coverage AND false")
    print("  alarms while keeping 2/2 deliberate mistakes caught.\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", choices=sorted(CANDIDATES),
                    help="which candidate (start with quran-ctc)")
    ap.add_argument("--agree", action="store_true",
                    help="confirm you have read and accept the model's licence")
    ap.add_argument("--dest", type=Path, default=CTC_MODEL_DIR)
    args = ap.parse_args()

    if not args.model:
        show()
        return 0

    chosen = CANDIDATES[args.model]
    print(f"\n  {args.model}: {chosen['repo']}")
    print(f"  LICENCE: {chosen['licence']}")
    if not args.agree:
        print("\n  Re-run with --agree once you have read the licence "
              "and accept it.\n")
        return 1

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("\n  Needs huggingface_hub:  pip install huggingface_hub\n")
        return 1

    args.dest.mkdir(parents=True, exist_ok=True)
    print(f"  downloading → {args.dest} …")
    snapshot_download(repo_id=chosen["repo"], local_dir=str(args.dest))
    print(f"\n  Done. Now measure it against the same corpus:\n"
          f"    python scripts/benchmark_recordings.py --engine ctc\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
