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

# Tier F — A second engine ✅ SEAM DONE, bake-off pending

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

# Tier H — Give the page back, and expose the two choices

*Planned, not started.*

### Why

**The pill covers the Mushaf.** It is a child of `central` positioned
absolutely — `main_window.py`, `resizeEvent()` — 240×48 at bottom centre, 40px
up. The Mushaf takes the left 600px of a 1000px window, so the pill's left
half sits on top of the page and hides the bottom of it. Nothing reserves that
space; the page is laid out as if the pill were not there.

This is the interface. A Mushaf you cannot read the bottom of is worse than
one with a plain toolbar, and the floating pill was chosen for the opposite
reason.

**And two things are now settings that no one can reach.** Tier F made the
engine selectable and Tier 3 made recording a flag, but both are decided
before launch:

```bash
./run.sh --engine ctc --record
```

A bake-off you can only start from a terminal gets run once. One you can flip
mid-session gets run daily — and `ModelLoaderThread` already takes an engine
name, so switching is a supported operation rather than a restart.

### Work — in this order

1. **Stop the pill covering the page.** Reserve its footprint: give the
   Mushaf view a bottom margin of the pill's height plus its gap, so the page
   lays out above it and the pill floats over nothing. Keep the floating look
   — it is right — but make the space real instead of borrowed.
   *Check at small window sizes and after dragging the splitter, which is
   where absolute positioning usually breaks.*
2. **Engine picker in the pill.** One control showing the current engine's
   label; tapping it lists `engine_choices()`, which already carries a
   one-line description per engine. Selecting one starts a new
   `ModelLoaderThread(engine_name=…)` and the pill shows *loading* until
   ready. An engine whose model is missing is listed but disabled, with
   `EngineUnavailable`'s message as the tooltip — that message already says
   what to do about it.
3. **Record toggle**, replacing `--record`. It is a per-session decision, and
   having to decide it before launch is why most sessions are not captured.
4. **A legend, once.** Three colours now mean three different things —
   correct, wrong, and *not sure* (the amber from Tier E's untrusted-region
   rule) — plus the basmala line, which is shown but never scored. Nothing
   tells the reciter any of that.
5. **Live state, not chrome.** Whether the app is *searching* or *following*
   is the one thing the reciter cannot otherwise know, and it explains the
   blank page during discovery. One line, only while it matters.

### What stays out

No preferences window. If a control does not need to be touched during a
session it belongs in `config.py`. The page is the interface; every pixel
spent on chrome is a pixel of Mushaf.

### Gate

Judged by use, not by numbers — but one number must not move.

| Measure | Target |
|---|---|
| Mushaf text hidden behind the pill | **none**, at any window size |
| Switch engine without restarting | **works, mid-session** |
| Sessions recorded | goes up, because the toggle is in reach |
| Benchmark coverage / false alarms / caught | **unchanged** |

A UI change that moves a scoring number means something is wired wrong.

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

### Real conditions

A fetched recitation is studio-clean and so are all 11 hand-made ones:

- long pauses (5–10s) mid-ayah, then resuming
- coughing, throat-clearing, background noise
- a phone on a table across the room

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
