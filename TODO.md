# Roadmap

Work is grouped into tiers. **Each tier ends with a measurement, and the
result decides what the next tier should be.** Do not start a tier before
running the previous tier's gate — the numbers have already changed the plan
four times, and every time the change was one nobody predicted from reading
code.

Every tier lists **Why** (the evidence), **Work**, **Gate** (the command and
the number that has to move), and **What the result changes**.

## The three numbers

| Number | Meaning | Direction |
|---|---|---|
| **Coverage** | recited words given any verdict | up |
| **False alarm rate** | correct words painted red | down |
| **Deliberate mistakes caught** | real errors still detected | must stay 2/2 |

**Report coverage first.** The false alarm rate is a fraction of the words the
app *scored*, so a word never shown costs nothing and a change that shows
fewer words scores better. Tier 3 found a recording the benchmark called clean
that had silently dropped 24 of its 93 words. All three numbers, always.

```bash
python -m pytest tests/ -q                      # 56 tests
python scripts/test_search_algorithm.py         # 63 search cases
python scripts/benchmark_recordings.py          # the real one
python scripts/replay_session.py logs/<log>     # one past session, no audio
./run.sh --record                               # make the next session a test
```

**Baseline today:** 85.1% coverage, 0.6% false alarms, 2/2 caught, 56 unit
tests, 63 search cases.

---

# What the evidence says now

Five real sessions, all recited correctly, all replayable from `logs/`:

| session | what was recited | coverage | painted red | lock-ons |
|---|---|---|---|---|
| `135032` | Al-Mu'minun 23:1–12, in order | good | 5 | 1 |
| `135237` | Fussilat 41:2–5, in order | good | 2 | 1 |
| `131925` | An-Nisa 4:1–4, in order | 85/86 | 5 | 2 |
| `144348` | **Al-Hijr, jumping about** | every located ayah **100%** | 4 | **5, one wrong** |
| `140605` | An-Nur 24:28–29, **An-Nisa 4:11–12** | 4:11 **41%**, 24:29 **29%** | **17** | 4 |

Three things fall out of that table, and they are the whole plan.

**1. Ordinary recitation is close to solved.** Short and medium ayahs recited
in sequence come back complete and almost entirely green. Tiers 1, 1.5 and 3.6
did their job; nothing below proposes to improve this case further.

**2. Long ayahs collapse.** An-Nisa 4:11 is 71 words and 4:12 is 88 — the
inheritance ayahs, dense with repeated formulas. 4:11 scored **29 of 71
words** and the session painted **17 red**, the worst of any session recorded.
This is a different failure from anything measured so far: the corpus's
longest ayah is 17 words.

**3. Jumping costs a lock-on every time, and can land in the wrong surah.**
Reciting *randomly* — a few ayahs here, a few there, which is how hifz is
actually revised — produced five separate discovery episodes in 110 seconds
and roughly **17 of those 110 seconds with a blank page**. One of the five
locked onto **Al-Qasas 28:16, 150 pages from Al-Hijr**.

---

# Tier A — The basmala

**The oldest complaint and now the most expensive bug.** Do this first: it is
small, it is the thing the reciter notices every single session, and it is the
direct cause of a wrong lock-on.

### Why

The basmala is a numbered ayah only at **1:1**. For every other surah the
Quran text has no words for it, so the app shows nothing while the reciter
opens — which is most of the reason it "looks broken at the start".

That was the known half. The new half is worse. `logs/hifz-20260919-144348.log`
opens with the basmala, and its untethered tail glued itself to the first word
of what followed:

```
[11.800s] ASR   text="وَالَحْمَنِ الرَّحِيمِ"
[11.800s] MATCH mode=discovery ctx=none -> NO MATCH
[12.089s] ASR   text="الرَّحِيم قَالَ هَ"
[12.089s] MATCH mode=discovery ctx=none -> 28:16 offset=12
[12.089s] MUSHAF load page 387 (for 28:16)
```

28:16 ends `الرَّحِيمُ` and 28:17 opens `قَالَ رَبِّ`. The trigram
`الرحيم / قال / ه` is unique in the Quran — **across an ayah boundary** — so
discovery took it, confidently, and sat in Al-Qasas for 6.6 seconds before
giving up. Three words of a surah the reciter never touched are still painted
on the page at the end of the session.

Both halves have one cause: **the app has no idea the basmala is being said.**

### Work

1. Carry the basmala as reference text that belongs to a surah's *opening* but
   is not a scorable ayah. Recognising it is what matters; painting it green
   is a bonus, and it must never be scored against an ayah.
2. When a transcription is recognised as the basmala, use it as a **prior, not
   a match**: the reciter is about to start a surah, so the next phrase should
   be matched against surah openings first.
3. Refuse a discovery n-gram that spans an ayah boundary **when it is the only
   evidence** — a phrase straddling two ayahs is a coincidence of adjacency
   far more often than it is a real position.

### Gate

```bash
./run.sh                    # basmala, then any surah
python scripts/replay_session.py logs/hifz-20260919-144348.log
python scripts/benchmark_recordings.py
```

| Measure | Now | Target |
|---|---|---|
| Wrong lock-ons in `144348` | 1 (28:16) | **0** |
| Words scored outside the surah recited | 3 | **0** |
| Basmala visible to the reciter | nothing at all | **shown** |
| Coverage / false alarms / caught | 85.1% / 0.6% / 2-2 | **no worse** |

### What the result changes

- **If step 3 alone removes the wrong lock-on** → do it and stop. It is the
  cheapest of the three and needs no data change.
- **If recognising the basmala also cuts time-to-first-highlight** → it
  subsumes most of Tier C, because every session starts with one.

---

# Tier B — Long ayahs

### Why

An-Nisa 4:11 (71 words) scored **29 of 71**. 4:12 (88 words) scored 87 of 88
but the pair produced **17 red words**. Nothing in the benchmark is longer
than 17 words, so none of this has ever been measured.

Two mechanisms are suspected and they are separable — **measure before
fixing**:

- **`TRACKING_WINDOW = 25`.** The tracker looks 25 reference words ahead of
  the pointer. That is three ayahs in Al-Mulk and **a third of one ayah** in
  An-Nisa. A pause mid-ayah, or one skipped clause, can put the reciter
  outside the window entirely.
- **Repeated formulas inside one ayah.** `مِن بَعْدِ وَصِيَّةٍ يُوصِي بِهَا أَوْ
  دَيْنٍ` occurs several times inside 4:11–12, and `تَرَك` appears at word
  indexes 3, 17, 28 and 41 of 4:12 alone. `track()` anchors on the longest
  matching run, and with four identical candidates the longest run is a
  coin toss. The reds read exactly like this: `عَلَيْكُمْ` heard as `تَرَكَ`,
  `جُنَاحٌ` heard as `أَزْوَاجُكُمْ` — words that are genuinely in the ayah,
  attached to the wrong position.

### Work

1. **First, measure which one it is.** Replay `140605` with `TRACKING_WINDOW`
   at 25, 40 and 60 and record coverage each time. That is one number and it
   settles the question.
2. If the window is the problem, scale it with the current ayah's length
   rather than fixing it in words.
3. If repeated formulas are the problem, break the tie by **distance from the
   pointer** rather than run length — the reciter is far more likely to be
   where they just were.
4. Add `surah nisa 11-12` to `tests/records/` so this is never re-measured by
   hand. `./run.sh --record` already produces it.

### Gate

```bash
python scripts/replay_session.py logs/hifz-20260919-140605.log
python scripts/benchmark_recordings.py
```

| Measure | Now | Target |
|---|---|---|
| 4:11 words scored | 29 / 71 | **≥ 60 / 71** |
| 24:29 words scored | 5 / 17 | **≥ 14 / 17** |
| Red words in that session | 17 | **< 6** |
| Benchmark coverage / false alarms / caught | 85.1% / 0.6% / 2-2 | **no worse** |

### What the result changes

- **If widening the window fixes it** → the fix is one constant and Tier B is
  done in an hour. Check it does not slow tracking on short surahs.
- **If it is the repeated formulas** → the same tie-break helps everywhere,
  because near-identical ayahs are the other half of Tier C.

---

# Tier C — Stop paying for every jump

### Why

`144348` is the first recording of how hifz is actually revised: a few ayahs
from one place, a few from another. Al-Hijr 15:72–77, then 15:52–56, then
15:67–70.

Every located ayah was scored **completely** — 9/9, 7/7, 8/8, 5/5. The matcher
is not the problem. **Finding the place again is.**

| | |
|---|---|
| Discovery episodes in 110s | **5** |
| Seconds with a blank page | **~17 of 110** |
| Wrong lock-ons | 1 (Tier A) |
| Re-lock cost after a jump | 1.9s – 3.9s |

The recorded session shows the other half, on disk, already isolated:
`006_15-75_ان-في-ذلك.flac` and `007_15-77_ان-في-ذلك.flac` — two clips with the
**same name**, because 15:75 and 15:77 both open `إِنَّ فِي ذَٰلِكَ لَآيَاتٍ`.
15:76 was skipped between them. Clips 009/011 and 010/012 show 15:52 and 15:53
each entered twice.

### Work

1. **Prefer the page already on screen**, then the surah already being
   recited. A jump within Al-Hijr should not be a global search. (This is the
   old Tier 2 step 1, now justified by a session rather than by argument.)
2. **Tap a word on the Mushaf to start there.** `set_position()` has been
   written and tested since the beginning and has never had a caller. For
   deliberate random revision this is the honest answer: the reciter knows
   where they are going and the app does not.
3. Break ties between near-identical ayahs by proximity to the last known
   position — shared with Tier B step 3.

### Gate

```bash
python scripts/replay_session.py logs/hifz-20260919-144348.log
./run.sh                    # recite a few ayahs, jump, recite a few more
python scripts/benchmark_recordings.py
```

| Measure | Now | Target |
|---|---|---|
| Seconds unlocked in `144348` | ~17 / 110 | **< 7 / 110** |
| Re-lock after a jump within the same surah | 1.9–3.9s | **< 1.5s** |
| 15:76 (skipped between two near-identical ayahs) | missing | **shown** |
| Wrong lock-ons | 1 | **0** |
| Benchmark coverage / false alarms / caught | 85.1% / 0.6% / 2-2 | **no worse** |

### What the result changes

- **If the page prior causes a wrong lock-on anywhere** → it is too strong;
  make it a tie-break among otherwise-equal candidates, never an override.
- **If tapping to start removes most of the pain** → deprioritise the priors.
  A reciter who can say where they are is better evidence than any heuristic.

---

# Tier D — The 27 words the benchmark never shows

*Small, already isolated, and the corpus for it exists. Good work to fill gaps
between the larger tiers.*

| recording | unseen | note |
|---|---|---|
| `surah mutaffifin 1-19` | **24** | all of 83:15–17 — the tracker went 83:14 → 83:18, and all five of those ayahs open `كَلَّا`. Same root cause as Tier B step 3. |
| `surah ala 11` | **2** | **position FOUND, zero words scored.** Finding where the reciter is and then showing nothing is the worst outcome the app has. |
| `fatiha first 2 ayahs` | 1 | |

### Gate

```bash
python scripts/benchmark_recordings.py
```

Coverage up from 85.1%; false alarms and 2/2 no worse.

---

# Tier E — Accuracy, from Tilawa

*`/home/hashus/code/tilawa`, cloned and inspected. Implement and gate **one at
a time**.*

> **Deprioritised twice over.** False alarms are 1 word in 154 on the
> benchmark, and the reds left in real sessions are words the model never
> heard correctly in *any* window — `فُصِّلَتْ` came back `فُصِّدَتْ` from 7
> windows of 11. No matcher change fixes that; see Tier F. Come back here only
> if Tiers B and C leave false alarms as the largest number.

- **E.1 `heardRatio`** — heard length ÷ expected length. **Largely redundant
  already**: `_is_fragment` (Tier 1.5) removed both fragment false alarms.
- **E.2 Phoneme confusion costs** — weighted edit distance over `ذدضتط`,
  `ظزذصسث`, `قكغ`, `فبم`, `ه/ح`, `ء/ع`, `ن/م`. Would cover `حُوبًا`/`خُوبًا`
  and `فُصِّلَتْ`/`فُصِّدَتْ`. **Risk: this makes the matcher more forgiving and
  is the item most likely to cost mistake detection. Gate it hard.**
- **E.3 Only flag a word sitting between two confidently-correct words.**
- **E.4 Full pausal (waqf) modelling.** Partly settled — Tier 3.6 stopped
  grading short vowels, which handles waqf endings for free.
- **E.5 Never grade tajweed, only word errors.** **Adopted** in Tier 3.6.

---

# Tier F — Architecture

*Only if B, C and D plateau. Largest win, largest change, invalidates several
tiers above it.*

### Why

Tilawa benchmarked this exhaustively and concluded **ASR quality is the
bottleneck and all Whisper-style approaches fail on the same samples**
(`lab/EXPERIMENTS.md`, findings 1–3).

Our own sessions now say the same thing independently. The reds that survive
every matcher fix are words Whisper never heard right in any window:

| reference | heard | windows agreeing |
|---|---|---|
| `فُصِّلَتْ` | `فُصِّدَتْ` | 7 of 11 |
| `حُوبًا` | `خُوبًا` | 7 of 19 |
| `حم` (41:1) | `خَامٍ` | never transcribed cleanly |
| `وَرَاءَ` | `ابْتَغَابَرَاءَ` | 6 windows, 5 different answers |

`whisper-small-quran` measured *worse* than our base model (23.4% vs 4.6%
false alarms) because generative decoding degrades on 3-second windows. The
problem is the architecture, not the size.

| Model | Size | Why it differs |
|---|---|---|
| `nvidia/stt_ar_fastconformer_hybrid_large_pcd_v1.0` | 88 MB | **CTC, not generative** — structurally cannot hallucinate. MIT / CC-BY-4.0. |
| `Quran-Lab/zipformer_p-arabic-v3` | 66 MB | Streaming phoneme CTC, 100% recall on Tilawa's corpus. **NPL-1.2 — non-commercial only.** |
| `quran-dev/wav2vec2-ctc-quran-phoneme-…` | large | Phoneme CTC tuned for **mispronunciation detection** — our exact task |

A streaming CTC model would remove, rather than mitigate, three things this
codebase works around: silence hallucination, window-boundary fragments, and
the echoed-word problem Tier 3.6 had to patch.

### Work

Bake-off first, port second. Add a second engine behind the existing
`transcribe_window()` seam and measure it on the same corpus before changing
anything else.

### Gate

```bash
python scripts/benchmark_recordings.py --model <candidate>
```

Must beat the current model on coverage **and** false alarms, and keep 2/2, on
a corpus of at least 30 recordings.

---

# Recordings still wanted

`./run.sh --record` now captures a session automatically and
`benchmark_recordings.py --from-session <dir>` turns it into cases, so most of
this is a matter of reciting rather than editing files. Check a clip's label
before trusting it, then move it into `tests/records/` with a proper name.

**Already captured and worth promoting into the corpus:**
- `logs/session-20260919-144355/` — random revision across Al-Hijr, five
  discovery episodes, and 15:75/15:77 confused with 15:76 dropped
- An-Nisa 4:11–12 from `logs/hifz-20260919-140605.log` — the long-ayah case

**Deliberate mistakes**, one per kind (we have 2, want ~8): similar-sounding
letter (`ذ`→`د`, `ح`→`ه`); **wrong vowel only** (`كَتَبَ`/`كُتِبَ`) — Tier 3.6
stopped grading short vowels and there is no case in the corpus to measure
what that cost, so **record this one first**; skipped word; skipped ayah;
repeated word; stop mid-word and restart; jump to a similar ayah elsewhere.

**Orthography**, where hand-written rules can be silently wrong:
- 78:35–36 `لَّا يَسْمَعُونَ فِيهَا لَغْوًا وَلَا كِذَّابًا` (the shadda bug)
- heavy shadda: 112:1–4
- alef maddah / dagger alef: `ءَامَنُوا`, `ذَٰلِكَ`, `الرَّحْمَٰنِ`

**Real conditions** — the 11 hand-made recordings all start cleanly and run
forward:
- long pauses (5–10s) mid-ayah, then resuming
- coughing, throat-clearing, background noise
- very slow with stretched madd; and very fast

**Other voices.** Everything is still tuned on one reciter.

---

# Done

Compressed. The evidence is in the commit messages; what matters here is the
order things were learned, because each one changed the plan.

**Tier 1 — Stop losing the reciter.** The tracking pointer did not advance over
already-scored words, so re-hearing a word left the pointer behind and three
failures dropped back to discovery. Also: "missed" could overwrite a word
already confirmed correct; ayahs skipped during re-discovery were never marked;
a pause between ayahs killed tracking. Al-Fatiha 3/7 ayahs → 4/7, tracking
losses 3 → 0, false alarms 4.6% → 2.0%. Added `scripts/replay_session.py`.

**Tier 1.5 — Score what was recited before the lock.** Discovery only answers
when a phrase is unique, so the opening of a surah — the phrase every session
begins with — was understood and thrown away. Al-Fatiha locked inside 1:1 at
word 2; Al-Baqarah locked at 2:2 and `الم` never appeared. Refused
transcriptions are now kept and re-read backwards once the position is known.
Also: ten one-word ayahs (`المص` `كهيعص` `طه` `يس` `عسق` `وَالطُّورِ`
`مُدْهَامَّتَانِ` `وَالْفَجْرِ` `وَالضُّحَىٰ` `وَالْعَصْرِ`) were unreachable
behind a two-word floor; and a clipped word (`سَمِ` of `بِسْمِ`) was condemned
as a wrong one. False alarms 2.0% → 0.6%. *Ambiguous muqatta'at stay refused —
`الم` opens six surahs and normalization merges it with `أَلَمْ تَرَ`.*

**Tier 2 — Lock on immediately.** Superseded. Its "missing verdicts" half was
solved by Tier 1.5; its "latency and wrong lock-ons" half is now Tiers A and C,
justified by real sessions instead of by argument.

**Tier 3 — Every session produces test data.** `./run.sh --record` writes
`logs/session-<timestamp>/`: `full.flac`, one clip per ayah the tracker
believed, the pre-lock audio as its own clip, and a manifest of every span,
transcription and verdict. `--from-session` turns it into benchmark cases.
Clips overlap deliberately — a 3s window makes the tracker enter the next ayah
early, and cutting at the transition lopped the end off every ayah.

**It immediately showed the benchmark was measuring the wrong thing.**
`surah mutaffifin 1-19` reported 69 ok / 0 wrong / 0% false alarms while
83:15–17 were never scored at all, 24 of 93 words. The rate is a fraction of
words *scored*, so showing fewer words scores better. The report now leads
with coverage: **85.1%, not 99.4%.**

**Tier 3.6 — Echoed words and Whisper's vowels.** The model repeats text it
has already heard at a window tail: 23:7 ends `الْعَادُونَ` and the next window
returned `...الْعَادُونَ فَمَنِ ابْتَغَى`, 23:7's own opening again. Aligned
positionally that condemned two words of 23:8 and dragged the pointer past the
other three. And `بَشِيرًا` came back `بِشِيرًا` from **eight windows of eight**
— letters right every time — so short vowels are no longer graded, Whisper's
tashkeel being its language model's rather than anything it heard.
*Cost: a wrong-vowel-only mistake can no longer be caught, and there is no
case in the corpus to measure it. See Recordings still wanted.*

**Tier 3.5 — "wrong" should need agreement.** Proposed, then **dropped**. The
idea was that a word whose windows disagree is a word we failed to hear rather
than a mistake. Tallying the real sessions killed it: the surviving reds are
words where the windows *agree* on the wrong reading — `فُصِّلَتْ` heard as
`فُصِّدَتْ` by 7 windows of 11. Agreement would not have caught them, and the
premise was wrong. Recorded here so it is not re-proposed.
