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
| **Retracted reds** | words red mid-session, not red at the end | down |
| **Deliberate mistakes caught** | real errors still detected | must stay 2/2 |

Retracted reds is the fourth, added after the reciter reported that "most of
the words seem red but while reciting they're gone" and every number above
read clean. The other three score the page as it is *left*; this is the only
one that can see something the reciter watched happen and then unhappen. It
measured 24.0% against a 6.1% false alarm rate — the page was retracting four
times as many reds as it kept.

**Report coverage first.** The false alarm rate is a fraction of the words the
app *scored*, so a word never shown costs nothing and a change that shows
fewer words scores better. Tier 3 found a recording the benchmark called clean
that had silently dropped 24 of its 93 words. All three numbers, always.

```bash
python -m pytest tests/ -q                      # 61 tests
python scripts/test_search_algorithm.py         # 63 search cases
python scripts/benchmark_recordings.py          # the real one
python scripts/replay_session.py logs/<log>     # one past session, no audio
./run.sh --record                               # make the next session a test
```

**Baseline today:** 99.4% coverage, 0.6% false alarms, 2/2 caught, 61 unit
tests, 63 search cases.

Coverage was 85.1% until Tier D found that two of the eleven recordings were
labelled with ayahs they do not contain. **Before believing a number about the
app, check the ground truth it is measured against.**

---

# What the evidence says now

Five real sessions, all recited correctly, all replayable from `logs/`:

| session | what was recited | coverage | painted red | lock-ons |
|---|---|---|---|---|
| `135032` | Al-Mu'minun 23:1–12, in order | good | 5 | 1 |
| `135237` | Fussilat 41:2–5, in order | good | 2 | 1 |
| `131925` | An-Nisa 4:1–4, in order | 85/86 | 5 | 2 |
| `144348` | **Al-Hijr, jumping about** | every located ayah **100%** | 4 | **5, one wrong** |
| `140605` | An-Nur 24:28–29, **An-Nisa 4:11–12** | 4:12 **87/88** | **17** | 4 |

Two things fall out of that table. A third — "long ayahs collapse" — was read
out of it and **turned out to be false**; see Tier B, and note that the claim
came from counting words the reciter never recited.

**1. Ordinary recitation is close to solved.** Short, medium *and long* ayahs
recited in sequence come back complete and almost entirely green. An-Nisa 4:12
is 88 words and scored 87 of them. Tiers 1, 1.5, 3.6 and A did their job;
nothing below proposes to improve this case further.

**2. Jumping costs a lock-on every time.** Reciting *randomly* — a few ayahs
here, a few there, which is how hifz is actually revised — produced five
separate discovery episodes in 110 seconds of Al-Hijr and roughly **17 of those
110 seconds with a blank page**. Tier A removed the worst of it (one episode
locked onto Al-Qasas, 150 pages away) but four remain.

*Following the reciter automatically is the product. Manual aids — tapping a
word to start, preferring the page on screen — were considered as Tier C and
**deliberately dropped**: they solve the symptom by asking the reciter to do
the app's job. Revisit only if the automatic path plateaus.*

**3. What survives is the model, not the matcher.** Every red word left in
every session is one Whisper never heard correctly in any window — `فُصِّلَتْ`
as `فُصِّدَتْ` in 7 windows of 11, `فَلِأُمِّهِ` as `فَلَكُورٌ`. No matcher change
reaches those. That is Tier F.

---

# Tier F — A second engine ✅ SEAM DONE · bake-off RUN · CTC not adopted

### Bake-off result

`rabah2026/wav2vec2-large-xlsr-53-arabic-quran-v_final`, same corpus, same
matching, same scoring, one engine swapped:

| | Whisper | CTC | |
|---|---|---|---|
| **Coverage** | 99.4% | **100.0%** | CTC better |
| **False alarms** | **0.6%** (1/157) | 4.4% (7/160) | **CTC 7× worse** |
| **Mistakes caught** | 2/2 | 2/2 | equal |
| Latency per window | 104 ms | **42 ms** | CTC 2.5× faster |

**The gate says both, not one. CTC fails it, so Whisper stays the default.**
`./run.sh --engine ctc` runs it; nothing about the default changed.

### What CTC gets wrong, and why it is interesting

Every one of its seven false alarms is a single-letter confusion, and they
fall into two classes:

| heard | reference | |
|---|---|---|
| `سَبَاءٌ` | `سَوَاءٌ` | ب/و |
| `بَيْلٌ` | `وَيْلٌ` | ب/و |
| `فَمَا` | `وَمَا` | ف/و |
| `أَظِيمٍ` | `عَظِيمٍ` | ء/ع |
| `الْكَثَرَ` | `الْكَوْثَرَ` | dropped و |

These are **exactly Tilawa's phoneme-neighbour groups** — `فبم` and the pair
`ء/ع`. Tier E refuted phoneme costs against *Whisper*, whose errors are
scattered; against CTC the errors are systematic and in precisely the classes
that cost model was built for. **The refutation was model-specific, and this
reopens E.2 for this engine.**

**But not yet, and the reason is the same trap.** The deliberate mistake
`فَلَهُمْ`/`وَلَهُمْ` is a ف/و confusion — the same class as the false alarms
`فَمَا`/`وَمَا` and `بَيْلٌ`/`وَيْلٌ`. Forgiving the class forgives the mistake.

### A defect found by running it

`CTC_MIN_CONFIDENCE` is **inert**. Sweeping 0.35 → 0.50 → 0.65 → 0.80 gives
byte-identical results, so the mean argmax probability over non-blank frames
is always above 0.80 and the gate never fires. The measure needs replacing —
likely entropy, or the margin between the top two symbols — before it is worth
tuning. As written it is a knob connected to nothing.

### The juz 30 corpus flips the bake-off, and that is the point

Same engines, same matching, same scoring, a different corpus — whole short
surahs instead of the twelve hand-made recordings:

| | Whisper | CTC | |
|---|---|---|---|
| **Coverage** | **98.1%** | 97.8% | Whisper barely |
| **False alarms** | 0.8% (5/657) | **0.3%** (2/658) | **CTC better** |
| Latency per window | 97 ms | **54 ms** | CTC 1.8× faster |
| Mistakes caught | — | — | **this corpus has none** |

On the hand-made corpus CTC was **7× worse** on false alarms. Here it is
**2.5× better**, at the same coverage. Neither number is wrong; they are
measurements of different audio, and the reading is that the corpus decides
the answer — which is the argument for getting a microphone into one.

**Do not read 0.3% as "CTC is better".** This corpus contains no mistakes, so
it cannot say whether CTC still catches one, and a corpus that only measures
false alarms rewards an engine that says nothing. What makes it more than a
draw is *where* each engine fails:

| case | Whisper | CTC |
|---|---|---|
| An-Nasr 110 | **5 unscored** (locked onto 43:38) | 19/19, clean |
| Al-Fil 105 | clean | **5 unscored, 1 red** |
| An-Nas 114 | **2 red** (`النَّاسِ` confusions) | 3 unscored, 0 red |
| Al-Falaq 113 | **1 red** | clean |
| Al-Qari'ah 101 | 7 unscored | 7 unscored |

Nine cases have a problem under one engine or the other and **seven of them
have it under only one**. That is the precondition for Tier F's second
proposal being worth anything: two engines that failed on the same cases
could not help each other, and these do not. CTC even dissolves the wrong
lock entirely — it never mis-hears `إِذَا جَاءَ نَصْرُ` as `إِذَا جَاءَنَا`, so
discovery is never offered Az-Zukhruf in the first place, which is a second
route at Tier K from the other end.

### What to do next with this

1. **Replace the confidence measure**, then re-sweep. A gate that never fires
   cannot be said to have been tested.
2. **Try CTC as a second opinion rather than a replacement.** Now measured,
   and promoted to the top of this list: seven of nine problem cases in juz
   30 fail under exactly one engine. A word painted red only where both agree
   would drop Whisper's `النَّاسِ` confusions and CTC's `الْفِيلِكِ`; a position
   taken where either engine finds a *longer* unique n-gram would have saved
   An-Nasr. Measure it as its own change, and on the hand-made corpus too —
   the deliberate mistakes are the half of the question juz 30 cannot ask.
   Cost is two engines resident at once; CTC is 54 ms a window against a
   200 ms step, so there is room.
3. **Do not adopt phoneme costs for CTC** until there is a deliberate mistake
   in the corpus that is *not* a ف/و confusion, or the gate cannot tell you
   anything.

### Seam



**The seam is in and Whisper is untouched.** Which engine runs is a setting,
not a rebuild, and nothing downstream learns which one produced the text.

```bash
python scripts/fetch_ctc_model.py                      # candidates + licences
python scripts/fetch_ctc_model.py --model wav2vec2-arabic --agree
python scripts/benchmark_recordings.py --engine ctc    # the bake-off
```

`src/audio/engines.py`. `WhisperEngine` is a *move, not a rewrite* — same
three gates, same parameters — so a bake-off compares against the app as it
really behaves. `CtcEngine` loads any transformers `AutoModelForCTC` and uses
CTC's own blank symbol and mean symbol probability rather than Whisper's
`avg_logprob`, which is produced by the same mechanism that does the
hallucinating. `transcribe_window()` keeps its signature and still accepts a
bare faster-whisper model, so every existing caller works unchanged.
Whisper path verified byte-identical: **99.4% / 0.6% / 2-2**.

### Why a second engine is the only avenue left

Tier E closed the rest. Our false alarms are *acoustically identical* to our
real errors — `فَلَهُمْ`/`وَلَهُمْ` (deliberate mistake) and `خُوبًا`/`حُوبًا`
(false alarm) are the same edit at the same distance, 0.25 — so no comparison
of strings separates them. Tier 3.5 showed agreement across windows does not
either: the windows agree on the *wrong* reading.

Every red word left in every session is one Whisper never heard correctly in
any window:

| reference | heard | windows agreeing |
|---|---|---|
| `فُصِّلَتْ` | `فُصِّدَتْ` | 7 of 11 |
| `حُوبًا` | `خُوبًا` | 7 of 19 |
| `حم` (41:1) | `خَامٍ` | never transcribed cleanly |

A CTC model emits one symbol per audio frame and has no decoder free to
continue a plausible sentence — the mechanism behind invented words, confident
window-edge fragments and echoed tails, all of which this codebase has code to
work around. Tilawa reached the same conclusion and stopped using Whisper
(`lab/EXPERIMENTS.md`, findings 1–3).

### The bake-off, when a model is on disk

| Measure | Whisper today | A CTC engine must |
|---|---|---|
| Coverage | 99.4% | **≥ 99.4%** |
| False alarms | 0.6% | **< 0.6%** |
| Deliberate mistakes | 2/2 | **2/2** |
| Latency per window | ~100 ms | **< 300 ms** (the step is 300 ms) |

Both of the first two, not one. An engine that is quieter but blinder is worse.

**The model to try is `quran-ctc`** —
`rabah2026/wav2vec2-large-xlsr-53-arabic-quran-v_final`, Apache-2.0,
`Wav2Vec2ForCTC`, fine-tuned on Quran recitation, and its vocabulary is fully
diacritized (tanween, shadda, dagger alef, alef wasla). It feeds the matcher
directly: no phoneme mapping, no format conversion.

`zipformer` emits phonemes and is non-commercial; `fastconformer` needs
conversion out of NeMo format. *`quran-dev/wav2vec2-ctc-quran-phoneme-…`,
listed in earlier versions of this file, does not exist — it was never
checked.*

**Licences differ and one candidate is non-commercial only.** `fetch_ctc_model`
prints each licence and refuses to download without `--agree`.

### What the result changes

- **If CTC wins clearly** → much of the compensating machinery can go:
  `_is_fragment`, the echo rule, `EDGE_CONFIRMATIONS`, the withhold/commit
  dance. Check what can be *deleted*, not kept alongside.
- **If CTC loses on coverage but wins on false alarms** → it is a second
  opinion, not a replacement. Consider running both and only painting red
  where they agree — but measure that as its own change.
- **If neither wins** → the ceiling is the audio, and the next move is better
  windowing or a Quran-tuned fine-tune, not another off-the-shelf model.

---

# Tier G — Build the corpus from recitations, not from recording sessions

*Planned, not started.*

### Why

The corpus is 11 hand-made recordings by one reciter. Everything is tuned on
that one voice, and Tier D found that **two of the eleven were labelled with
ayahs they do not contain** — the labels are typed by hand, so they are wrong
at a rate nobody was measuring.

Published recitations by established qāriʾ fix both at once. The recitation is
correct by construction, and **the ground truth comes from the URL rather than
from someone's memory**: if you fetch surah 2 ayah 1, that is what it is.

They also fix the gaps the current corpus cannot reach, because a person
recording test cases records what is easy to recite:

| gap now | what fetching fixes |
|---|---|
| one voice | many reciters, different speeds and styles |
| longest ayah is 17 words | Al-Baqarah 282 is 129 words |
| no muqatta'at at all | every `الم` `حم` `المص` `كهيعص` `طه` `يس` `عسق` opening |
| 11 cases | hundreds, at no human cost |

### The one thing this cannot do

**A correct recitation contains no mistakes.** This corpus can move *coverage*
and *false alarms* and nothing else — `DELIBERATE MISTAKES 2/2` still comes
only from a human deliberately reciting one wrong. So Tier G **enlarges the
denominator, it does not replace the human recordings**, and the deliberate
mistake list under *Recordings still wanted* stays exactly as urgent.

Keeping that straight matters: a corpus that only measures false alarms
rewards an app that says nothing.

### Work

1. **`scripts/fetch_recitations.py`** — per-ayah audio from a published source
   (EveryAyah-style per-ayah files, or the Quran.com audio API), into
   `tests/recitations/<reciter>/<surah>_<ayah>.mp3`.
   - **Check the terms of use of whichever source is chosen and record them in
     the script**, the way `fetch_ctc_model.py` records model licences. These
     are somebody's recordings; a research corpus is not a licence to
     redistribute, so the audio stays out of git — the script fetches, and a
     manifest under version control says what to fetch.
   - Several reciters, deliberately different in pace: a slow muratal and a
     fast one at least, since *very slow with stretched madd* and *very fast*
     are both on the wanted list.
2. **Stratified selection, not uniform random.** Uniform sampling over 6,236
   ayahs gives mostly medium ayahs from the middle of the Quran and would miss
   every case that has ever broken this app. Sample deliberately:

   | stratum | why | roughly |
   |---|---|---|
   | muqatta'at openings | `الم` was invisible for months; `حم` still is | all 28 one-word ayahs |
   | surah openings + basmala | the hardest phrase to place, every session starts with one | 30 |
   | very long ayahs (>50 words) | 2:282, 4:11-12, 5:6 | 20 |
   | very short ayahs (≤3 words) | juz 30, Ar-Rahman's refrain | 40 |
   | near-identical neighbours | 83:14–18 `كَلَّا`, 55's refrain, 26's refrain | 30 |
   | uniform random | the ordinary case, as a control | 100 |

   Seed the sampler and commit the seed, so the corpus is reproducible and a
   regression can be traced to a specific ayah rather than to luck.
3. **Runs of consecutive ayahs, not only single ones.** Half the app's
   behaviour is transitions — gap-fill, page turns, tracking across a
   boundary. Fetch some 3–5 ayah runs and concatenate them.
4. **`--from-recitations` in the benchmark**, alongside `--from-session`.
   Expectations are generated, not typed, so the Tier D class of error cannot
   recur.
5. **Report per stratum.** One overall number will hide a category failing
   completely — which is exactly how `surah ala 11` scored zero for months.

### Gate

```bash
python scripts/fetch_recitations.py --plan        # what it would fetch
python scripts/fetch_recitations.py --agree
python scripts/benchmark_recordings.py --from-recitations
```

| Measure | Now | Target |
|---|---|---|
| Corpus size | 11 | **200+ ayahs, 3+ reciters** |
| Muqatta'at cases | 0 | **all 28** |
| Ayahs over 50 words | 0 | **20** |
| Coverage, whole corpus | 99.4% (on 11) | **≥ 95% on the enlarged one** |
| False alarms | 0.6% | **report per stratum** |
| Deliberate mistakes | 2/2 | **2/2 — unchanged, and still from humans** |

Expect coverage and false alarms to get *worse* on first contact. That is the
point: 99.4% on eleven easy recordings is not a measurement of the app, it is
a measurement of the corpus.

### What the result changes

- **If a stratum fails badly** → that stratum becomes the next tier, and it
  will be a real failure rather than one inferred from a session by hand.
- **If other reciters score much worse than the one this was tuned on** →
  the tuning is overfitted, and the constants in `config.py` need re-deriving
  against the wider corpus before anything else is attempted.
- **If CTC (Tier F) wins on this corpus but not the old one** → trust this
  one. It is bigger, its labels are machine-derived, and it contains the
  cases that actually break things.

---

# Tier H — One page, one bar

*Planned, not started.*

### Why

**Three things are wrong with the window, and they are the same thing.**

The pill covers the Mushaf. It is a child of `central` positioned absolutely
— `main_window.py`, `resizeEvent()` — 240×48 at bottom centre, 40px up.
Nothing reserves that space, so the page is laid out as if the pill were not
there and the bottom of it is hidden underneath.

The right 400px of a 1000px window is a `QTextEdit` printing the same
verdicts the page is already showing in colour. Two renderings of one thing,
and the duplicate gets 40% of the window while the Mushaf is squeezed into
600px.

And **three decisions are made before launch that belong inside a session** —
the engine (Tier F), recording (Tier 3), and now strictness (below). A
bake-off you can only start from a terminal gets run once; one you can flip
mid-session gets run daily.

All three are the same mistake: chrome taking space and decisions from the
page. The page is the interface.

### The shape

Square. Two regions, and nothing else:

```
┌──────────────────────────────────────────┐
│                                          │
│              the Mushaf page             │
│          (everything above the bar)      │
│                                          │
├──────────────────────────────────────────┤
│  ⚙            ▶ play            state    │
└──────────────────────────────────────────┘
```

- **The page fills everything above the bar.** No splitter, no right panel.
- **One bar along the bottom.** Play dead centre, settings at one end, live
  state at the other.

**A bar, not a pill with reserved space.** The earlier plan was to give the
Mushaf a bottom margin the height of the floating pill. A bar *is* that
space: there is no geometry to keep in sync, no `resizeEvent` arithmetic, and
nothing that can drift back over the page at an unusual window size. The
floating look was chosen to keep the page uninterrupted; a bar keeps it
uninterrupted by construction.

### Work — in this order

1. **Delete the right panel and the splitter.** `output_text`, `QSplitter`,
   `QTextEdit#output` in `style.py`, and the `setHtml` calls at
   `main_window.py:273` and `:338`. `RecitationTracker.on_result()` returns
   HTML that nothing will consume any more — that return value goes too, and
   the page becomes the only channel. *The transcriptions are not lost: they
   are in the session log, which is on by default and is where anyone
   actually reads them.*

2. **Replace the floating pill with the bottom bar.** A real widget in the
   `QVBoxLayout` under the Mushaf, not an absolutely positioned child. The
   drop shadow and the pill's proportions are right and should survive the
   move; what should not survive is `resizeEvent()` positioning it by hand.
   *Check at the minimum window size — 800×480 — where a fixed-width pill
   was already close to the edges.*

3. **A settings menu on the bar.** One button, one popup. It holds the
   engine picker (`engine_choices()` already carries a line of description
   per engine; an engine whose model is missing is listed but disabled, with
   `EngineUnavailable`'s message as the tooltip, because that message already
   says what to do about it), the record toggle, and the strictness level.
   Selecting an engine starts a new `ModelLoaderThread(engine_name=…)` and
   the bar says *loading* until it is ready — switching is already a
   supported operation.

4. **Strictness levels.** The substantive one — see below.

5. **A legend, once.** Four things are shown and nothing explains any of
   them: correct, wrong, *not sure* (Tier E's amber untrusted-region rule),
   and the basmala line, which is displayed but never scored.

6. **Live state, not chrome.** Whether the app is *searching* or *following*
   is the one thing the reciter cannot otherwise know, and it is what
   explains the blank page during discovery. One word at the end of the bar,
   only while it matters.

### Strictness — the reciter's decision, not the model's

Measured across all 19 session logs: 4,792 `WRONG` verdicts, **2,652
distinct** heard/reference pairs, of which **347 (13%) are one letter apart**
and the rest are whole words misheard. The largest single-letter class is
`و↔ف` at 21 distinct pairs — which is the class the deliberate mistake
فَلَهُمْ/وَلَهُمْ belongs to.

So **forgiving a letter class is not available as a setting.** On the
hand-made corpus it costs more than it buys:

| | what it is | if single-letter differences are forgiven |
|---|---|---|
| `2:7:9` فَلَهُمْ / وَلَهُمْ | real mistake | 1 letter → **no longer caught** |
| `49:13:6` أَوْ / وَأُنثَىٰ | real mistake | still caught |
| `2:7:11` أَلِيمٌ / عَظِيمٌ | false alarm | 2 letters → **still a false alarm** |

Removes 0 of 1 false alarms, loses 1 of 2 caught mistakes. This is Tier E's
result again: the mistake and the false alarm are the same edit at the same
distance, so no comparison of strings can separate them.

What *is* available is letting the reciter say which error they would rather
have. Ḥifẓ review wants everything flagged; fluency practice wants to be
stopped only for a real error. The app cannot know which; the person can.

Candidate levels — **each one ships with its measured numbers or it does not
ship**, because a level named "lenient" that nobody has benchmarked is a knob
connected to nothing, and this codebase already shipped one of those
(`CTC_MIN_CONFIDENCE`, inert, found in Tier F):

| level | rule | status |
|---|---|---|
| **strict** | today's behaviour: any letter difference is wrong | the baseline — 0.6% false alarms, 2/2 caught |
| **confirmed** | a word is painted red only once N independent windows agree | **hypothesis.** Trades latency for confidence and forgives no letter class, so the mistakes should survive. Generalizes `EDGE_CONFIRMATIONS`, which already does this at window edges |
| **words** | only a whole-word difference is wrong | **known cost: loses فَلَهُمْ/وَلَهُمْ.** Include it if it is measured, label it honestly |
| **follow** | colour nothing, just keep the page | for reading along. The honest version of "stop grading letters" |

`config.py` gets the thresholds; the bar gets the choice. The benchmark gets
`--strictness all` and reports every level in one table, because the only way
to name a level is to know what it costs.

### What stays out

No preferences window. If a control does not need to be touched during a
session it belongs in `config.py`. Every pixel spent on chrome is a pixel of
Mushaf.

### Gate

Judged by use, not by numbers — except that one number must not move, and one
new table must exist.

| Measure | Target |
|---|---|
| Mushaf text hidden behind chrome | **none**, at any window size |
| Window width given to the Mushaf | 600/1000 → **all of it** |
| Switch engine without restarting | **works, mid-session** |
| Sessions recorded | goes up, because the toggle is in reach |
| Every strictness level | **has measured coverage / false alarms / caught** |
| Benchmark at `strict` | **unchanged** — 99.4% / 0.6% / 2-of-2 |

A UI change that moves a scoring number means something is wired wrong. A
strictness level with no number beside it means the level is a guess.

---
# Recordings still wanted

**Tier G will fetch the bulk of a corpus** — published recitations by
established qāriʾ, with ground truth from the URL rather than typed by hand.
What follows is what fetching *cannot* produce.

### Deliberate mistakes — the only thing a human can record

A correct recitation contains no mistakes, so `DELIBERATE MISTAKES 2/2` can
only ever come from someone reciting one wrong on purpose. We have 2 and want
~8. **This list does not get easier when Tier G lands; it gets more important**,
because a corpus that only measures false alarms rewards an app that says
nothing.

- **Wrong vowel only** (`كَتَبَ`/`كُتِبَ`) — **record this first.** Tier 3.6
  stopped grading short vowels and there is no case in the corpus to measure
  what that cost.
- Similar-sounding letter (`ذ`→`د`, `ح`→`ه`)
- Dropped definite article (`الْيَتَامَىٰ`→`يَتَامَى`) — happened for real in
  `logs/hifz-20260919-131925.log` and was caught; it needs to stay caught
- Skipped word; skipped ayah; repeated word
- Stop mid-word and restart
- Jump to a similar ayah elsewhere (one `كَلَّا` ayah to another)

### Real conditions — now the top of this list, and measured

**The corpus measures a condition the reciter never experiences.** Same code,
same day, same machine:

| audio | wrong verdicts |
|---|---|
| fetched corpus, 273 cases, 5 qāriʾ | **2.2%** false alarms |
| studio 4:11, Alafasy, 71 dense words | **6.6%** (4 of 61) |
| this reciter, 17:23 / 18:19 / 15:2 | **25–33%** |
| this reciter, 4:12 | **48%** (60 of 88 words wrong at least once) |

A ten-fold gap between the corpus and the person the app is for. Tier G was
right that eleven easy recordings measured the corpus rather than the app —
and 275 studio recordings measure the corpus too, just a bigger one. Nothing
in the fetched corpus has a microphone in it.

Two measurable differences, neither yet tested:

- **pace.** 70 words/min against Alafasy's 48 — 46% faster, so fewer
  acoustic frames per word inside the same 3-second window.
- **the microphone and the room.** Every recording in both corpora was made
  to be published.

What is wanted, and why it is now urgent rather than nice to have:

- long pauses (5–10s) mid-ayah, then resuming
- coughing, throat-clearing, background noise
- a phone on a table across the room
- **the same ayah recited twice, once close to the mic and once across the
  room** — the cheapest way to separate "the ayah is hard" from "the audio
  is hard", which is currently unanswerable

### Already captured, worth promoting

- `logs/session-20260919-144355/` — random revision across Al-Hijr, four
  discovery episodes, 15:75/15:77 confused with 15:76 dropped
- An-Nisa 4:11–12 from `logs/hifz-20260919-140605.log` — long dense ayahs

`./run.sh --record` captures a session and `--from-session` turns it into
cases. **A captured clip is a candidate until someone listens to it** — Tier D
found two hand-typed labels wrong, and automation makes wrong labels cheaper,
not rarer.

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

**Tier A — The basmala.** It is a numbered ayah only at 1:1, so opening any
other surah gave a blank page. Recognised on three of its four words and shown
but never scored. The expensive half was a wrong lock-on: discovery trusted a
two-word phrase found anywhere in a short chunk, and the basmala's last word
plus the first word of what followed read as `الرحيم قال` — unique across the
28:16/28:17 boundary — sending the app 150 pages into Al-Qasas. Two-word
phrases are now trusted only at the tail of a transcription.
*Two approaches that do not work: stripping a basmala prefix before discovery,
and refusing every cross-ayah bigram. Both break Al-Fatiha, which locks on
`رحمن الرحيم الحمد` where those words really are 1:1.*

**Tier B — Long ayahs. Premise refuted, nothing implemented.** Built on An-Nisa
4:11 scoring 29 of 71 words, which was a counting error: the lock landed at
`offset=42` because the reciter jumped into the middle of the ayah, so those
words were never said. 4:12 is 88 words, was recited in full, and scored 87 of
88. Sweeping `TRACKING_WINDOW` 25/40/60/90 scored 143/138/138/138, so the
window was not it either. *A proximity tie-break for `track()`'s anchor was
written and reverted — it moved no number anywhere.*

**Tier D — The words the benchmark never showed. Both were ground-truth
errors.** `surah ala 11` contains `وَالسَّمَاءِ ذَاتِ الرَّجْعِ` — At-Tariq 86:11,
not Al-A'la 87:11 — so the app had been finding 86:11 correctly and being
scored against the wrong surah, reported as *position FOUND, zero words*.
`surah mutaffifin 1-19` does not contain 83:15–17: none of their distinctive
words is ever transcribed and 83:14 and 83:18 are 0.3s apart. Coverage
85.1% → **99.4% with no change to the app.** *Ground truth is a thing to
verify, not assume.*

**Tier E — Accuracy, from Tilawa. One adopted, two refuted.** E.3 adopted: a
word is shown wrong only when at least one neighbour in the same ayah is
confidently correct. Tilawa requires both; measured, that reaches 0.0% false
alarms and drops mistakes to 1/2. E.1 `heardRatio` and E.2 phoneme costs were
never implemented because computing what they *would* decide settles it: the
deliberate mistake `فَلَهُمْ`/`وَلَهُمْ` scores 0.25, the same as false alarms
`خُوبًا`/`حُوبًا` and `فُصِّدَتْ`/`فُصِّلَتْ`, and above `فَقَالُوا`/`وَقَالُوا` at
0.17; on `heardRatio` the mistake `أَوْ`/`وَأُنثَىٰ` and the false alarm
`بُهُ`/`السُّدُسُ` are both 0.40. **Our false alarms are acoustically identical
to our real errors**, which is why Tier F is the only avenue left.
`scripts/check_phoneme_costs.py` keeps the computation.

**Tier 3.5 — "wrong" should need agreement.** Proposed, then **dropped**. The
idea was that a word whose windows disagree is a word we failed to hear rather
than a mistake. Tallying the real sessions killed it: the surviving reds are
words where the windows *agree* on the wrong reading — `فُصِّلَتْ` heard as
`فُصِّدَتْ` by 7 windows of 11. Agreement would not have caught them, and the
premise was wrong. Recorded here so it is not re-proposed.

**Tier I — A wrong word is not a skip.** `difflib` will walk past any number
of reference words to reach one that matches, because matching more is all it
is trying to do. In the Quran, where a small vocabulary repeats, the word a
reciter gets wrong is very often a word that occurs again a line or two down
— so the aligner jumped to it rather than call it a mistake. 74:22 is
`ثُمَّ عَبَسَ وَبَسَرَ` and 74:23 is `ثُمَّ أَدْبَرَ وَاسْتَكْبَرَ`; reciting
"ثم عبس واستكبر" stepped over `وَبَسَرَ`, `ثُمَّ` and `أَدْبَرَ` to land on
`وَاسْتَكْبَرَ`, which scored the one real mistake **correct**, invented three
skipped words nobody had touched, and left the pointer an ayah ahead of the
reciter — where nothing they said next could match, so the position was then
lost as well. A skipped run of two or more is now believed only when
`SKIP_RESUME_WORDS` reference words line up consecutively after it; a lone one
is coincidence, and the heard word is charged against the word that was due.
A run of one skipped word is always believed — dropping a word is the
commonest thing a reciter does.
*Measured: the benchmark does not move at all — coverage 100.0%, false alarms
14/262 = 5.3%, mistakes 2/2, the same sixteen words flagged. On the twenty
recorded sessions four change, all in the same direction: `172206` loses five
phantom skips and gains seven correct words, `185444` loses one position and
so one re-lock, `152312` moves one word from "not sure" to "wrong".*

**Tier J — The page as a printed page.** Three display defects with one cause:
nothing was measuring where the ink actually is.
- *Highlights.* A QCF glyph's box is 85 units tall and the letters in it
  occupy about 45, so every verdict was painted on a rectangle twice the
  height of its word and sitting high of it. Outlining the glyph
  (`QPainterPath.addText`) gives the real extent. Per-line extremes were tried
  first and rejected: the tallest line measures 71 units against a line step
  of 71, so neighbouring lines' highlights met. The median across the page,
  clamped to leave a gap, is uniform and hugs the text.
- *Surah names.* QBSML holds only the name, and at the page's point size it
  measures 34 units against a body line of 85 — drawn as if it were a word it
  came out as a small mark adrift on an empty line. It is a banner now, ruled
  across the text column with the name set inside it. Drawn *above* the masks:
  glyph boxes overlap the line above, so their masks cut a pale line across
  the frame otherwise.
- *The name in the bar.* `VerseMatch.surah_name` is the Arabic name, which is
  right for a right-to-left page and wrong for a left-to-right status bar —
  "المدثر 74:22" is reordered by the bidi algorithm into something that reads
  as neither. `QuranIndex.surah_label()` gives the transliteration.

---

# Tier K — A wrong lock is never reconsidered

*Found by the juz 30 corpus. Not started.*

### Why

The juz 30 corpus is whole short surahs — Ad-Duhaa to An-Nas, each recited
from its basmala, which is the shape of a session rather than an ayah lifted
out of the middle of one:

```bash
python scripts/fetch_recitations.py --juz30 --agree
python scripts/benchmark_recordings.py --from-recitations --juz30
```

```
surah-from-basmala     9 cases   97.7% coverage   0.8% false alarms
surah-from-ayah-1     13 cases   98.4% coverage   0.7% false alarms
22/22 located          657 words scored, 5 painted red
```

The basmala costs nothing — worth knowing, since it is four words of audio
before anything scorable *and* the one phrase discovery is guaranteed to
refuse, and it moves neither column.

**The two worst cases are the same failure, and it is not on this roadmap.**

| case | unscored | what happened |
|---|---|---|
| An-Nasr 110 | 5 of 19 | `إِذَا جَاءَ نَصْرُ` heard as `إِذَا جَاءَنَا` — unique, to **43:38**. |
| Al-Qari'ah 101 | 7 of 36 | `وَمَا أَدْرَاكَ` cut to `وَمَا أَدْرَى` — unique, to **46:9**. |

Discovery did nothing wrong: both phrases really do occur exactly once, and
refusing to guess is the rule it is built on. What is wrong is what happens
next. From `logs` of the 110 run:

```
7.086  ASR  "إِذَا جَاءَنَا"          discovery -> 43:38
7.174  ASR  "إِذَا جَاءَ نَصْفِ"       tracking ctx=43:38:2 -> 43:38
7.656  ASR  "إِذَا جَاءَ نَصْرُ اللَّهِ"  tracking ctx=43:38:3 -> 43:38
...    twelve windows, then a re-lock at 110:2
```

By 7.656s the transcription is `إِذَا جَاءَ نَصْرُ اللَّهِ` — four words that
occur in exactly one place in the Quran, and it is not Az-Zukhruf. The app
held the wrong surah anyway, and showed the reciter page 503.

**Every escape hatch the tracker has counts misses.** `TRACKING_MAX_MISSES`,
`TRACKING_MAX_MISS_SECONDS`, `REDISCOVERY_MAX_GAP` — all of them fire when
tracking *fails*. A confident wrong lock never fails: `إِذَا جَاءَ` genuinely
is inside 43:38, so the local search succeeds every single window and the
counter never moves. The one condition that cannot trigger re-discovery is
being wrong and sure of it.

### Work

1. **Keep discovering while tracking.** `discover()` already runs on every
   transcription in discovery mode and costs an n-gram lookup. Run it in
   tracking mode too, and compare the two answers instead of ignoring one.
2. **Overrule the pointer on better evidence, not on failure.** This is the
   rule `recitation.py` already applies to words — best evidence wins, and a
   verdict may be revised — applied to *position*. Take the discovered
   position when it rests on a longer unique n-gram than the local match
   used: 4 words unique to 110:1 beat 2 words found inside 43:38.
3. **Do not let it cost a page turn per window.** The comparison must be
   hysteretic: only switch when the discovered n-gram is strictly longer, and
   only after it has said the same thing twice — the same `confirmations`
   idea `STRICTNESS` already uses.
4. **Re-place what was scored under the wrong pointer.** The pre-lock
   machinery (`_fill_prelock`, `PRELOCK_BUFFER_SECONDS`) already re-matches
   held transcriptions against a position once it is known. A corrected lock
   is the same problem with a different trigger.

### Gate

```bash
python scripts/benchmark_recordings.py --from-recitations --juz30
python scripts/benchmark_recordings.py            # must not move
```

| Measure | Now | Target |
|---|---|---|
| Coverage, juz 30 | 98.1% | **≥ 99.5%** |
| An-Nasr 110 unscored | 5 of 19 | **0** |
| Al-Qari'ah 101 unscored | 7 of 36 | **0** |
| False alarms, juz 30 | 0.8% | **no worse** |
| Deliberate mistakes | 2/2 | **2/2** |
| Wrong-surah page loads | **at least** 2 of 22 | **0** |

"At least": only 110 and 101 were traced, because they are the two cases
with unscored words. A mis-lock that is recovered quickly leaves no trace in
the summary table at all, so the real count needs a run with `--debug-log`
over all 22 before and after. Measure it first; a target of 0 against an
unknown baseline is not a gate.

A page turn per window is the way this fails. Count them: `log.mushaf` already
records every page load, and a fix that trades two wrong locks for forty
flickers is worse than the defect.

### What the result changes

- **If it works** → re-run the 275-case stratified corpus. The `neighbours`
  stratum was built for exactly this class and samples one ayah at a time,
  which cannot exhibit it: the failure is *choosing between two places while
  tracking*, and a single-ayah case has nothing to choose.
- **If page turns spike** → the hysteresis is the tier, not the comparison.
- **If it changes nothing on the hand-made corpus** → expected. Those twelve
  are recited in order from a clean start and never mis-lock.

---

# Tier L — A fragment is not a mistake

*Found by the juz 30 corpus. Not started. Small.*

### Why

Three of the five words juz 30 painted red are not wrong words, they are
truncated ones:

| heard | reference | |
|---|---|---|
| `هُ` | `أَنزَلْنَاهُ` | 1 letter against 8 |
| `ضَرٌّ` | `ضَالًّا` | 4 against 6 |
| `الْخَنْرِ` | `الْخَنَّاسِ` | cut short |

A window boundary cut the word in half. `EDGE_CONFIRMATIONS` exists for this
and did not catch them, because it asks *where the word sat in the window*
and these did not sit at the edge of the window the model was given — the
model simply stopped early.

**This is not a letter-class exemption and does not walk into Tier E's trap.**
Tier E established that a forgiven letter class forgives the deliberate
mistake with it, because `فَلَهُمْ`/`وَلَهُمْ` is the same edit as the
commonest false alarm. Length is a different axis: both deliberate mistakes
in the corpus are the same length as the word they replace —
`فَلَهُمْ`/`وَلَهُمْ`, `وَأُنثَىٰ` recited as `و انثا` — so a rule that withholds
a verdict when the heard token is a *fraction* of the reference cannot touch
either one.

### Work

Withhold rather than forgive. A heard token below some fraction of the
reference's length is not evidence of anything; it is the same "wait for a
better window" the edge rule already does, triggered by a different symptom.
It must stay a `_withhold`, not a pass — a genuinely dropped syllable is a
real mistake and the reciter has to see it when no better window comes.

### Gate

Sweep the fraction. A threshold ships with measured numbers or it does not
ship.

| Measure | Now | Target |
|---|---|---|
| False alarms, juz 30 | 5/657 = 0.8% | **≤ 2/657** |
| Coverage, juz 30 | 98.1% | **no worse** |
| Deliberate mistakes | 2/2 | **2/2, or the rule is wrong** |

If the mistakes stop being caught at any threshold that helps, the axis is
not as independent as it looks and this goes in the Refuted list beside the
phoneme costs.
