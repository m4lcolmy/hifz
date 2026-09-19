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

# Tier A — The basmala ✅ DONE

| Measure | Before | After |
|---|---|---|
| Wrong lock-ons in `144348` | 1 (Al-Qasas 28:16) | **0** ✅ |
| Words scored outside the surah recited | 3 | **0** ✅ |
| Discovery episodes in that session | 5 | **4** ✅ |
| Basmala visible to the reciter | nothing at all | **shown** ✅ |
| Coverage / false alarms / caught | no worse ✅ | |

Recognised on three of its four words, so a passing `الرحمن الرحيم` — which is
55:1 on its own and part of 1:3 — cannot trigger it. Shown but **not scored**:
outside Al-Fatiha it is not part of any ayah, so it says *heard*, not
*correct*.

The expensive half was the wrong lock-on. Discovery trusted a two-word phrase
found anywhere in a short chunk, and the basmala's last word followed by the
first word of what came next read as `الرحيم قال` — unique in the Quran across
the 28:16/28:17 boundary. A two-word phrase is now trusted **only at the tail**
of a transcription, where it is what the reciter is saying now rather than
leftovers. Longer n-grams are unaffected and may still span an ayah boundary,
which is what continuous recitation looks like.

**Two approaches that do not work**, so they are not tried again: stripping a
basmala prefix before discovery breaks Al-Fatiha, which locks on
`رحمن الرحيم الحمد` where those words really are 1:1; and refusing *every*
cross-ayah bigram breaks the same case. The discriminator is tail-versus-not,
not inside-ayah-versus-across.

---

# Tier B — Long ayahs ❌ PREMISE REFUTED

**There is no long-ayah problem.** The tier was built on An-Nisa 4:11 scoring
29 of 71 words, and that number was mine, not the app's.

Two measurements killed it:

1. **The window is not the constraint.** Replaying `140605` with
   `TRACKING_WINDOW` at 25 / 40 / 60 / 90 scored 143, 138, 138, 138 words.
   Widening it is neutral to slightly worse.
2. **4:11 was never recited from the start.** The lock landed at
   `offset=42` on `فَإِنْ كَانَ لَهُ` — the reciter jumped into the middle of
   the ayah. The 42 unscored words were never said.

And the control case settles it: **An-Nisa 4:12 is 88 words, was recited in
full, and scored 87 of 88.** The longest ayah in the sessions is also one of
the best covered.

This is the same trap Tier 3 built the `partial` flag for, and I walked into it
anyway while reading a session by hand. **Coverage is only meaningful against
what was actually recited.**

The 17 red words in that session are real, but they are ASR garbage on dense
text — `فَلِأُمِّهِ` heard as `فَلَكُورٌ`, `السُّدُسُ` as `بُهُ`. That is Tier F,
not the matcher.

*A proximity tie-break for `track()`'s anchor was implemented and reverted: it
moved no number on any session or on the benchmark. Unmeasured complexity is
not kept.*

---

# Tier D — The words the benchmark never showed ✅ DONE

**Both gaps were errors in the ground truth, not in the app.** Coverage
85.1% → **99.4%**, with no change to the app at all.

**`surah ala 11` — 0 words scored, position FOUND.** The audio is
`وَالسَّمَاءِ ذَاتِ الرَّجْعِ`, which is **At-Tariq 86:11**, not Al-A'la 87:11
(`وَيَتَجَنَّبُهَا الْأَشْقَى`). The app had been finding 86:11 correctly all
along and being scored against the wrong surah for it. Adjacent surah numbers;
nobody checked. Renamed to `surah tariq 11.flac`, expectation corrected, and
it now scores 3/3.

**`surah mutaffifin 1-19` — 24 words.** The audio does not contain 83:15, 83:16
or 83:17: none of `مَحْجُوب` / `لَصَالُو` / `الْجَحِيم` / `تُكَذِّبُون` is ever
transcribed, and 83:14 and 83:18 are **0.3 seconds apart**. All five of those
ayahs open `كَلَّا`, which is how the skip was made and how it was missed.
Marked `partial`.

`partial` now contributes a recording's *scored* words to the coverage total
rather than dropping it entirely — otherwise excluding Al-Mutaffifin threw away
69 real words and made the percentage jump for reasons unrelated to the app.

**One word is still unshown**, in `fatiha first 2 ayahs`. Small enough to leave.

### What this changes

**Ground truth is now a thing to verify, not assume.** Two of eleven recordings
were wrong, both in the direction of making the app look worse, and both
survived every gate for months because nothing ever checked the audio against
the label. `./run.sh --record` makes new cases cheap — it also makes wrong
labels cheap, so a captured clip is a *candidate* until someone listens to it.

---

# Tier E — Accuracy, from Tilawa ✅ DONE (one adopted, two refuted)

**One of the five helped. Two were refuted by measurement on our own data,
and the way they failed is the strongest argument yet for Tier F.**

### E.3 — Never condemn a word in a region you do not trust ✅ ADOPTED

A wrong verdict between words that were themselves mis-heard is far more
likely to be the alignment slipping than the reciter erring. A word is now
shown as wrong only when **at least one neighbour in the same ayah is
confidently correct**; otherwise it goes amber — *something happened here we
could not read*.

Painting moved out of `_commit` into a `_repaint` pass, because a word's
colour can now change without new evidence about that word.

| | An-Nisa 4:11–12 | Al-Mu'minun | Al-Hijr | benchmark |
|---|---|---|---|---|
| red before | 17 | 5 | 4 | 0.6%, 2/2 |
| red after | **14** | **3** | 4 | **0.6%, 2/2** |

No words lost. **Tilawa requires *both* neighbours clear; we require one.**
Measured, the strict version takes false alarms to 0.0% and drops mistakes
caught to **1/2** — it loses the Al-Hujurat `وَأُنثَىٰ` mistake, which sits in a
stretch with five missed words. A drop in mistake detection disqualifies an
item regardless of the false alarm number.

### E.1 `heardRatio` and E.2 phoneme costs ❌ BOTH REFUTED

Neither was implemented, because computing what they *would* decide on the
words the app actually paints red settles it in one table. Tilawa's cost model
(`phonemeCost.ts`: groups `ذدضتط` `ظزذصسث` `جزش` `ةهت` `قكغ` `فبم`, pairs
`ه/ح` `غ/خ` `ء/ع` `ن/م` `ن/ل` `ظ/ض`), length-normalised:

| heard | reference | distance | heardRatio | what it is |
|---|---|---|---|---|
| `فَقَالُوا` | `وَقَالُوا` | **0.17** | 1.00 | false alarm |
| `فَلَهُمْ` | `وَلَهُمْ` | **0.25** | 1.00 | **deliberate mistake** |
| `خُوبًا` | `حُوبًا` | **0.25** | 1.00 | false alarm |
| `فُصِّدَتْ` | `فُصِّلَتْ` | **0.25** | 1.00 | false alarm |
| `يَتَامَى` | `الْيَتَامَىٰ` | 0.29 | 0.71 | **real error** (dropped `ال`) |
| `أَوْ` | `وَأُنثَىٰ` | 0.80 | **0.40** | **deliberate mistake** |
| `بُهُ` | `السُّدُسُ` | — | **0.40** | false alarm |
| `أَلِيمٌ` | `عَظِيمٌ` | 0.50 | 1.00 | false alarm |

**There is no threshold that separates them.** The deliberate mistake sits at
0.25, the same distance as two false alarms and *above* a third at 0.17. On
`heardRatio` the deliberate mistake and a false alarm are both exactly 0.40.
Any cut that forgives the false alarms forgives the mistake.

That is not a flaw in Tilawa's design. It is what the numbers mean: **our
remaining false alarms are acoustically identical to our real errors.** One
letter wrong in a short word is one letter wrong in a short word, whether the
reciter said it or the model misheard it. No function of the two strings can
tell them apart.

### E.4 waqf ✅ / E.5 never grade tajweed ✅

Both settled in Tier 3.6, which stopped grading short vowels — waqf endings
fall out for free, and not grading pronunciation quality is now the rule.

### What this changes

**Tier F is no longer optional, and this tier is the reason.** Every avenue for
telling a real error from a mis-hearing *by comparing strings* is now closed:
E.1 and E.2 are refuted arithmetically, E.3 is adopted and takes the remaining
reds from 26 to 21 across three sessions. What is left needs either
**agreement across windows** — which Tier 3.5 showed does not separate them
either, since the windows agree on the wrong reading — or **an acoustic model
that does not mishear in the first place.**

Only the second is left.

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

**Phoneme models need a mapping.** `zipformer` and `wav2vec2-quran` emit
phonemes, not Arabic script, so they cannot be scored until phonemes map back
to words. Try a character-level model first (`wav2vec2-arabic`) — it is the
cheapest way to find out whether CTC helps *at all* before paying for the
mapping.

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

# Tier H — A minimal UI that exposes the choices

*Planned, not started.*

### Why

Two things are now settings and neither is reachable without editing Python:
**which engine runs** (Tier F) and **whether the session is recorded**
(Tier 3). A bake-off you can only run from a terminal will be run once; one
you can flip mid-session gets run every day, and the app already reloads the
engine on a background thread, so switching is a supported operation rather
than a restart.

The constraint is that this app is a Mushaf. The page is the interface and
everything else should stay out of its way — the current floating pill is
right, and the work is to extend it without turning it into a settings screen.

### Work

1. **Engine picker in the pill.** A single control showing the current
   engine's label; tapping it lists `engine_choices()` with the one-line
   description each engine already carries. Selecting one starts a new
   `ModelLoaderThread(engine_name=…)` — already supported — and the pill shows
   *loading* until it is ready. An engine whose model is missing is listed but
   disabled, with `EngineUnavailable`'s message as the tooltip, since that
   message already says what to do about it.
2. **Record toggle**, replacing `--record`. It is a per-session decision, and
   deciding it before launch is the reason most sessions are not captured.
3. **Live state, not chrome.** The pill has room for what the reciter cannot
   otherwise know: whether the app is *searching* or *following*, and where it
   thinks it is. One line, only while it matters.
4. **Keep settings out of the page.** No preferences window. If a control does
   not need to be touched during a session, it belongs in `config.py`.
5. **The basmala line and the amber "unclear" verdict need a legend once** —
   three colours now mean three different things (correct, wrong, *not sure*)
   and nothing tells the reciter that.

### Gate

Not a numbers tier; it is judged by use.

| Measure | Target |
|---|---|
| Switch engine without restarting | **works, mid-session** |
| Sessions recorded | goes up, because the toggle is in reach |
| Mushaf area lost to controls | **none** |
| Benchmark coverage / false alarms / caught | **unchanged — this tier touches no scoring** |

A UI change that moves a scoring number means something is wired wrong.

### What the result changes

If switching engines mid-session is easy, the Tier F bake-off stops being a
one-off and becomes the normal way to compare — which is how the model choice
should have been made from the start.

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

**Tier 3.5 — "wrong" should need agreement.** Proposed, then **dropped**. The
idea was that a word whose windows disagree is a word we failed to hear rather
than a mistake. Tallying the real sessions killed it: the surviving reds are
words where the windows *agree* on the wrong reading — `فُصِّلَتْ` heard as
`فُصِّدَتْ` by 7 windows of 11. Agreement would not have caught them, and the
premise was wrong. Recorded here so it is not re-proposed.
