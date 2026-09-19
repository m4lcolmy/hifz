"""The fetched corpus: which ayahs, in what proportion, and why those.

Split from `fetch_recitations.py` on purpose. Everything here is pure — it
reads `data/quran.json`, takes a seed, and returns a list of cases. Nothing
touches the network, so the corpus can be built, diffed and tested offline,
and `benchmark_recordings.py --from-recitations` can read the same definition
without knowing how the audio got there.

**The manifest is the corpus; the audio is a cache.** The manifest is version
controlled and the audio is not, because these are somebody else's recordings
and a research corpus is not a licence to redistribute. Deleting
`tests/recitations/*/` costs a download, not a corpus.

Why stratified and not uniform: uniform sampling over 6,236 ayahs gives mostly
medium ayahs from the middle of the Quran and would miss every case that has
ever broken this app. `الم` was invisible for months; 83:14-18 all open
`كَلَّا` and the app confused them; the longest ayah in the hand-made corpus is
17 words while 2:282 is 129. None of those is a rare event in the strata
below and all of them are rare under uniform sampling.

Ground truth comes from the ayah number in the URL rather than from someone
typing a filename. Tier D found two of eleven hand-made labels wrong — 86:11
recorded as "ala 11", and three ayahs missing from a file named "1-19" — so
the labels were wrong at a rate nobody was measuring. That class of error
cannot recur here, because nobody types a label.

**What this cannot measure.** A correct recitation contains no mistakes, so
this corpus moves coverage and false alarms and nothing else. `DELIBERATE
MISTAKES 2/2` still comes only from a human reciting one wrong on purpose.
Keep that straight: a corpus that only measures false alarms rewards an app
that says nothing.
"""

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import QURAN_JSON
from src.core.arabic import normalize, split_words

RECITATIONS_DIR = PROJECT_ROOT / "tests" / "recitations"
MANIFEST_PATH = RECITATIONS_DIR / "manifest.json"
#: Cases the source cannot supply — written by the fetcher, read by the
#: benchmark, and *not* version controlled, because it is a fact about a
#: server on a given day rather than about the corpus. It exists so that
#: "not downloaded yet" and "will never download" stay different things: the
#: first is a command you have not run, the second is a hole in the corpus.
UNAVAILABLE_PATH = RECITATIONS_DIR / "unavailable.json"

#: Committed, so the corpus is reproducible and a regression traces to a
#: specific ayah rather than to luck. Changing it changes the corpus, which
#: makes every stored number incomparable — treat it as a constant.
SEED = 1446

MANIFEST_VERSION = 1

#: Silence inserted between the ayahs of a concatenated run. The source clips
#: are cut tight at each ayah end, so gluing them at zero would run the last
#: syllable of one ayah into the first of the next — which no reciter does,
#: and which would quietly remove the between-ayah pause that Tier 1 found
#: was killing tracking. Recorded in the manifest, because it is part of what
#: the corpus *is*.
RUN_GAP_MS = 350


# ═══════════════════════════════════════════════════════════════════════
# Where the audio comes from
# ═══════════════════════════════════════════════════════════════════════

#: everyayah.com serves one mp3 per ayah at `<surah:03d><ayah:03d>.mp3`, and
#: `<surah:03d>000.mp3` for the basmala where a reciter has one.
SOURCE = {
    "name": "everyayah",
    "base_url": "https://everyayah.com/data",
    "terms_url": "https://everyayah.com/",
    "terms": (
        "everyayah.com republishes recordings gathered from several sources "
        "and offers them for free download; it publishes no single "
        "machine-readable licence, and the rights in each reciter's "
        "recording remain with whoever made it. Read the site before doing "
        "anything with the audio beyond running this benchmark locally. "
        "Nothing fetched here is redistributed: the audio is gitignored and "
        "only this manifest — ayah numbers, which is to say facts — is "
        "committed."
    ),
}


@dataclass(frozen=True)
class Reciter:
    id: str                 # the everyayah directory name
    label: str
    pace: str               # why this one is in the list


#: Deliberately spread across pace, because "very slow with stretched madd"
#: and "very fast" are both on the wanted list and the hand-made corpus is
#: eleven recordings by one voice at one speed. If the app was tuned to that
#: voice, this is what will show it.
RECITERS = (
    Reciter("Husary_Muallim_128kbps", "Al-Husary (muʿallim)",
            "very slow teaching pace, every madd stretched to its full count"),
    Reciter("Abdul_Basit_Murattal_64kbps", "Abdul Basit (murattal)",
            "slow murattal, long pauses between ayahs"),
    Reciter("Husary_64kbps", "Al-Husary (murattal)",
            "measured murattal — the closest to the hand-made corpus"),
    Reciter("Alafasy_64kbps", "Mishary Alafasy",
            "medium, the voice most people have heard"),
    Reciter("Abdurrahmaan_As-Sudais_192kbps", "As-Sudais",
            "fast, and the hardest on a 3-second window"),
)


# ═══════════════════════════════════════════════════════════════════════
# The Quran, as the sampler needs to see it
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Ayah:
    surah: int
    ayah: int
    text: str
    words: int
    norm: str


def load_ayahs(path: Path = QURAN_JSON) -> list[Ayah]:
    """Every ayah, with the word count the benchmark will score against.

    `split_words` is the app's own splitter, so `words` is the same number
    `QuranIndex.words_in_range()` produces. A test holds them together.
    """
    with open(path, encoding="utf-8") as f:
        surahs = json.load(f)

    out = []
    for surah in surahs:
        for verse in surah["verses"]:
            words = split_words(verse["text"])
            out.append(Ayah(
                surah=surah["id"],
                ayah=verse["id"],
                text=verse["text"],
                words=len(words),
                norm=" ".join(normalize(w) for w in words),
            ))
    return out


#: The fourteen muqatta'at, normalized the way the index normalizes them.
MUQATTAAT = (
    "الم", "المص", "الر", "المر", "كهيعص", "طه", "طسم", "طس",
    "يس", "ص", "حم", "عسق", "ق", "ن",
)


def muqattaat_openings(ayahs: list[Ayah]) -> list[Ayah]:
    """Every ayah that *opens* with muqatta'at — 30 of them, not 28.

    The roadmap called this "all 28 one-word ayahs" and those are two
    different sets that overlap in 20. There are exactly 28 one-word ayahs,
    but eight of them are ordinary words (`وَالْعَصْرِ`, `الْحَاقَّةُ`,
    `مُدْهَامَّتَانِ` …), and ten muqatta'at are *not* a standalone ayah —
    `الٓر` opens 10:1 as the first word of `الر تِلْكَ آيَاتُ الْكِتَابِ`.

    Both halves matter and for different reasons. A standalone `حم` is a
    whole ayah the app must locate from one word. An embedded `الر` is a word
    the app must hear at the start of a longer ayah, which is a different
    failure: not "cannot find it" but "silently drops it". So this stratum
    takes all 30 openings — 29 surahs, and Ash-Shura twice because 42:1 is
    `حم` and 42:2 is `عسق` — and the eight one-word ayahs that are not
    muqatta'at are guaranteed by the `short` stratum instead.

    Matched on the raw text, not the normalized text, and deliberately:
    `normalize()` folds `الم` together with the `أَلَمْ` of `أَلَمْ تَرَ`,
    which is the very ambiguity that makes discovery refuse them — so
    matching on normalized text would sweep in 94:1 and 105:1. The dataset
    writes the muqatta'at with no diacritics at all, so exact equality on the
    first word is precise: it finds 30 ayahs and nothing else.
    """
    return [a for a in ayahs
            if a.text and split_words(a.text)[:1] and
            split_words(a.text)[0] in MUQATTAAT]


def one_word_ayahs(ayahs: list[Ayah]) -> list[Ayah]:
    return [a for a in ayahs if a.words == 1]


def repeated_ayahs(ayahs: list[Ayah]) -> list[Ayah]:
    """Ayahs whose exact text occurs elsewhere in the Quran.

    280 ayahs, headed by Ar-Rahman's refrain 31 times over and
    `وَيْلٌ يَوْمَئِذٍ لِّلْمُكَذِّبِينَ` 11 times. Discovery refuses to answer on
    an ambiguous phrase, so these are where a session stays lost the longest,
    and tracking is where it lands on the wrong twin.
    """
    seen: dict[str, int] = {}
    for a in ayahs:
        seen[a.norm] = seen.get(a.norm, 0) + 1
    return [a for a in ayahs if seen[a.norm] > 1]


def ayah_opener_counts(ayahs: list[Ayah]) -> dict[str, int]:
    """How many ayahs in the Quran open with each word."""
    counts: dict[str, int] = {}
    for a in ayahs:
        first = a.norm.split(" ")[0]
        if first:
            counts[first] = counts.get(first, 0) + 1
    return counts


def near_identical_neighbours(ayahs: list[Ayah], radius: int = 4,
                              rare_opener_max: int = 40) -> list[Ayah]:
    """Ayahs that nearly repeat one close by — the 83:14-18 failure.

    An exact repeat is easy to find by equality; the one that actually broke
    this app was not exact. 83:14 and 83:18 both open `كَلَّا` and sit four
    ayahs apart, and tracking anchored on the shared opening word dragged the
    alignment onto the wrong one. What made it dangerous is not that the two
    ayahs are similar overall — they are not, past the first word — but that
    they *start* the same, which is all a 3-second window at an ayah boundary
    ever sees.

    So the test is on the shared opening and nothing else:

      - two or more leading words shared with a neighbour, which is what a
        refrain looks like (26's `وَإِنَّ رَبَّكَ لَهُوَ الْعَزِيزُ الرَّحِيمُ`
        eight times over), or
      - one leading word shared, where that word opens at most
        `rare_opener_max` ayahs in the whole Quran. `كَلَّا` opens 28 and is
        the 40th commonest opener; `إِنَّ` opens 269 and a shared `إِنَّ` says
        nothing at all.
    """
    openers = ayah_opener_counts(ayahs)
    by_surah: dict[int, list[Ayah]] = {}
    for a in ayahs:
        by_surah.setdefault(a.surah, []).append(a)

    out = []
    for surah_ayahs in by_surah.values():
        for i, a in enumerate(surah_ayahs):
            a_words = a.norm.split(" ")
            if not a_words[0]:
                continue
            for j in range(max(0, i - radius), min(len(surah_ayahs), i + radius + 1)):
                if j == i:
                    continue
                b_words = surah_ayahs[j].norm.split(" ")
                shared = 0
                for x, y in zip(a_words, b_words):
                    if x != y:
                        break
                    shared += 1
                if shared >= 2 or (
                        shared == 1 and openers.get(a_words[0], 0) <= rare_opener_max):
                    out.append(a)
                    break
    return out


# ═══════════════════════════════════════════════════════════════════════
# Cases
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Case:
    """One audio file to fetch and one expectation to score it against."""

    stratum: str
    reciter: str
    surah: int
    first_ayah: int
    last_ayah: int
    #: Ayah numbers to fetch and concatenate, in order. `0` is the basmala,
    #: which everyayah serves as `<surah>000.mp3` for some reciters and not
    #: others — it is dropped silently when missing, because the app never
    #: scores the basmala either way.
    parts: tuple[int, ...] = ()
    words: int = 0
    note: str = ""

    @property
    def is_run(self) -> bool:
        return self.last_ayah > self.first_ayah

    @property
    def id(self) -> str:
        span = (f"{self.first_ayah:03d}" if not self.is_run
                else f"{self.first_ayah:03d}-{self.last_ayah:03d}")
        return f"{self.stratum}/{self.reciter}/{self.surah:03d}_{span}"

    @property
    def filename(self) -> str:
        """Where the audio lands, relative to `tests/recitations/`.

        A single ayah keeps the source mp3 untouched. Anything concatenated —
        a run, or an opening with its basmala — is written as FLAC, because
        gluing mp3 frames together is not decoding them and the seams show up
        as clicks the VAD then treats as speech.
        """
        stem = f"{self.reciter}/{self.surah:03d}_{self.first_ayah:03d}"
        if self.is_run:
            return f"{stem}-{self.last_ayah:03d}.flac"
        if len(self.parts) > 1:
            return f"{stem}_basmala.flac"
        return f"{stem}.mp3"


@dataclass
class Stratum:
    name: str
    target: int
    why: str
    cases: list[Case] = field(default_factory=list)


def build_corpus(seed: int = SEED,
                 reciters: tuple[Reciter, ...] = RECITERS,
                 ayahs: list[Ayah] | None = None) -> list[Stratum]:
    """The whole corpus, deterministically, from a seed.

    Strata are filled in order and each one excludes the ayahs already taken,
    so no ayah is fetched twice and the per-stratum numbers stay attributable
    to one stratum.
    """
    ayahs = ayahs if ayahs is not None else load_ayahs()
    by_key = {(a.surah, a.ayah): a for a in ayahs}
    surah_length: dict[int, int] = {}
    for a in ayahs:
        surah_length[a.surah] = max(surah_length.get(a.surah, 0), a.ayah)

    rng = random.Random(seed)
    used: set[tuple[int, int]] = set()

    def take(pool: list[Ayah], n: int | None) -> list[Ayah]:
        """`n` of `pool`, seeded, skipping anything already claimed."""
        free = sorted({(a.surah, a.ayah) for a in pool} - used)
        if n is not None and n < len(free):
            free = sorted(rng.sample(free, n))
        used.update(free)
        return [by_key[k] for k in free]

    strata: list[Stratum] = []

    def singles(name: str, why: str, picked: list[Ayah], target: int,
                parts_of=lambda a: (a.ayah,), note: str = "") -> Stratum:
        s = Stratum(name, target, why)
        order = list(reciters)
        rng.shuffle(order)
        for i, a in enumerate(picked):
            s.cases.append(Case(
                stratum=name, reciter=order[i % len(order)].id,
                surah=a.surah, first_ayah=a.ayah, last_ayah=a.ayah,
                parts=parts_of(a), words=a.words, note=note,
            ))
        strata.append(s)
        return s

    # ── Muqatta'at, all of them ────────────────────────────────────────
    singles(
        "muqattaat",
        "الم was invisible for months and حم still is; an ayah the app "
        "cannot even locate scores zero silently",
        take(muqattaat_openings(ayahs), None),
        target=30,
    )

    # ── Surah openings, with the basmala where the reciter has one ─────
    # Every session starts with one of these and discovery refuses the
    # basmala outright — it matches 114 places — so the opening is the
    # phrase the app is worst at and hears most often.
    opening_pool = [by_key[(s, 1)] for s in sorted(surah_length)
                    if (s, 1) in by_key and (s, 1) not in used and s != 9]
    singles(
        "openings",
        "every session begins with one, and discovery refuses the basmala "
        "because it matches 114 places",
        take(opening_pool, 30),
        target=30,
        # At-Tawbah has no basmala and 1:1 *is* the basmala, so neither
        # gets one prepended. Everywhere else the basmala is part 0, which
        # everyayah serves for some reciters and not others — Al-Husary has
        # none at all and Abdul Basit has 2:0 but not 113:0. It is dropped
        # silently when missing, because the expectation does not depend on
        # it: the app shows the basmala line and never scores it.
        parts_of=lambda a: (a.ayah,) if a.surah == 1 else (0, a.ayah),
        note="basmala first where the reciter has one",
    )

    # ── Very long ayahs ────────────────────────────────────────────────
    # The longest hand-made recording is 17 words. 2:282 is 129.
    singles(
        "long",
        "the hand-made corpus tops out at 17 words; 2:282 is 129, and "
        "nothing has ever tested the tracker over that distance",
        take([a for a in ayahs if a.words > 50], 20),
        target=20,
    )

    # ── Very short ayahs, including every one-word ayah not yet taken ──
    short_pool = [a for a in ayahs if a.words <= 3]
    solo = take([a for a in one_word_ayahs(ayahs)], None)      # all that remain
    rest = take([a for a in short_pool], max(0, 40 - len(solo)))
    s = Stratum("short", 40,
                "a one-word ayah was unreachable behind a two-word discovery "
                "floor for months; juz 30 is full of them")
    order = list(reciters)
    rng.shuffle(order)
    for i, a in enumerate(sorted(solo + rest, key=lambda x: (x.surah, x.ayah))):
        s.cases.append(Case(stratum="short", reciter=order[i % len(order)].id,
                            surah=a.surah, first_ayah=a.ayah, last_ayah=a.ayah,
                            parts=(a.ayah,), words=a.words))
    strata.append(s)

    # ── Near-identical neighbours ──────────────────────────────────────
    twins = {(a.surah, a.ayah) for a in repeated_ayahs(ayahs)}
    twins |= {(a.surah, a.ayah) for a in near_identical_neighbours(ayahs)}
    singles(
        "neighbours",
        "83:14 and 83:18 both open كَلَّا and the tracker anchored on the "
        "wrong one; Ar-Rahman's refrain repeats 31 times",
        take([by_key[k] for k in sorted(twins)], 30),
        target=30,
    )

    # ── Runs of consecutive ayahs ──────────────────────────────────────
    # Half the app's behaviour is transitions: gap-fill, page turns, tracking
    # across a boundary. A corpus of single ayahs cannot see any of it.
    runs = Stratum("runs", 25,
                   "half the app's behaviour is transitions — gap-fill, page "
                   "turns, tracking across an ayah boundary")
    order = list(reciters)
    rng.shuffle(order)
    attempts = 0
    while len(runs.cases) < 25 and attempts < 5000:
        attempts += 1
        surah = rng.randrange(1, 115)
        length = rng.randint(3, 5)
        last_possible = surah_length.get(surah, 0) - length + 1
        if last_possible < 1:
            continue
        start = rng.randint(1, last_possible)
        span = [(surah, start + k) for k in range(length)]
        if any(k in used for k in span):
            continue
        used.update(span)
        runs.cases.append(Case(
            stratum="runs", reciter=order[len(runs.cases) % len(order)].id,
            surah=surah, first_ayah=start, last_ayah=start + length - 1,
            parts=tuple(range(start, start + length)),
            words=sum(by_key[k].words for k in span),
        ))
    strata.append(runs)

    # ── Uniform random, as the control ─────────────────────────────────
    singles(
        "random",
        "the ordinary ayah, as a control — if this stratum is the only one "
        "that scores well, the app only handles the ordinary ayah",
        take(list(ayahs), 100),
        target=100,
    )

    return strata


def all_cases(strata: list[Stratum]) -> list[Case]:
    return [c for s in strata for c in s.cases]


# ═══════════════════════════════════════════════════════════════════════
# The manifest — the version-controlled half
# ═══════════════════════════════════════════════════════════════════════

def build_manifest(seed: int = SEED,
                   reciters: tuple[Reciter, ...] = RECITERS) -> dict:
    strata = build_corpus(seed, reciters)
    return {
        "version": MANIFEST_VERSION,
        "seed": seed,
        "run_gap_ms": RUN_GAP_MS,
        "generated_by": "scripts/fetch_recitations.py",
        "source": SOURCE,
        "reciters": [{"id": r.id, "label": r.label, "pace": r.pace}
                     for r in reciters],
        "strata": [{"name": s.name, "target": s.target, "why": s.why,
                    "chosen": len(s.cases)} for s in strata],
        "cases": [
            {
                "id": c.id,
                "stratum": c.stratum,
                "reciter": c.reciter,
                "surah": c.surah,
                "ayahs": [c.first_ayah, c.last_ayah],
                "parts": list(c.parts),
                "words": c.words,
                "file": c.filename,
                **({"note": c.note} if c.note else {}),
            }
            for c in all_cases(strata)
        ],
    }


def write_manifest(manifest: dict, path: Path = MANIFEST_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def read_manifest(path: Path = MANIFEST_PATH) -> dict:
    if not path.exists():
        raise SystemExit(
            f"No corpus manifest at {path}.\n"
            f"  python scripts/fetch_recitations.py --plan"
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("version") != MANIFEST_VERSION:
        raise SystemExit(
            f"Manifest is version {manifest.get('version')}, this code reads "
            f"{MANIFEST_VERSION}. Regenerate it: "
            f"python scripts/fetch_recitations.py --plan"
        )
    return manifest


def case_from_entry(entry: dict) -> Case:
    first, last = entry["ayahs"]
    return Case(
        stratum=entry["stratum"], reciter=entry["reciter"],
        surah=entry["surah"], first_ayah=first, last_ayah=last,
        parts=tuple(entry.get("parts", ())), words=entry.get("words", 0),
        note=entry.get("note", ""),
    )


# ═══════════════════════════════════════════════════════════════════════
# Audio
# ═══════════════════════════════════════════════════════════════════════

def decodes(path) -> str:
    """"" if the file decodes to audio, else why it does not.

    Two of everyayah's own mp3s are corrupt — 11:23 and 33:50 in the
    muʿallim set — and the downloads of both are byte-for-byte what the
    server sent, matching Content-Length. So this is not a truncated
    transfer and retrying does not help: the file on the server is bad.

    It has to be checked, because an undecodable file is a landmine. The
    first full run died on one sixty cases in, after ten minutes, with an
    av error and no indication which file caused it.
    """
    try:
        pcm = load_pcm16(path)
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    if not pcm:
        return "decodes to no audio at all"
    return ""


def load_pcm16(path, target_sr: int | None = None) -> bytes:
    """Decode any audio file to mono Int16 PCM bytes at `target_sr`.

    The hand-made FLACs have unreliable headers and the fetched files are
    22.05 kHz mp3, so both go through the same packet-level decode rather
    than through anything that trusts a header.
    """
    import av
    import numpy as np

    if target_sr is None:
        from src.config import SAMPLE_RATE
        target_sr = SAMPLE_RATE

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
