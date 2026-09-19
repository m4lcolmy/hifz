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
