# Roadmap

Work is grouped into tiers. **Each tier ends with a measurement, and the
result decides what the next tier should be.** Do not start a tier before
running the previous tier's gate — the numbers have already changed the plan
twice, and both times the change was one nobody predicted from reading code.

Every tier below lists:

- **Why** — the evidence that makes it worth doing
- **Work** — the actual changes
- **Gate** — the exact command and the number that has to move
- **What the result changes** — how the next tiers get re-planned

Two numbers must be reported together, always:

| Number | Meaning | Direction |
|---|---|---|
| **Coverage** | recited words given any verdict | up |
| **False alarm rate** | correct words painted red | down |
| **Deliberate mistakes caught** | real errors still detected | must stay 2/2 |

A change that improves one by damaging another is a regression. The benchmark
prints all three for this reason, coverage first — it is the one that was
missing, and its absence made every earlier number optimistic.

**Baseline today:** 85.1% coverage, 0.6% false alarms, 2/2 caught, 56 unit
tests, 63 search cases.

**Report coverage first.** The false alarm rate is a fraction of the words the
app *scored*, so a word never shown costs nothing and a change that shows
fewer words scores better. Tier 3 found a recording the benchmark called clean
that had silently dropped 24 of its 93 words. Both numbers, always, or the
rate can be improved by doing less.

---

## The evidence this plan is built on

A full recitation of Al-Fatiha, `logs/hifz-20260919-122520.log`, 55.9s:

| | |
|---|---|
| Ayahs shown on the page | **1:4, 1:5, 1:7** |
| Ayahs never shown at all | **1:1, 1:2, 1:3, 1:6** |
| Words given a verdict | **16 of 29** |
| First word → first highlight | **18.6s** |
| Tracking lost and re-discovered | **3 times in 56s** |
| Discovery attempts / hits | 47 / 4 |

The 11-recording benchmark reports 4.6% false alarms on the same code. Both
are true: the recordings are short, clean and forward-only, so they never
exercise pausing between ayahs or running long enough for the pointer to
drift. **A real session is a harder test than the entire benchmark.** Fixing
that blindness is Tier 1's real purpose.

---

# Tier 1 — Stop losing the reciter ✅ DONE

**Gate result** (same session replayed with `scripts/replay_session.py`):

| Measure | Before | After | Target |
|---|---|---|---|
| Al-Fatiha ayahs shown | 3 / 7 | **4 / 7** | 7/7 — *blocked on Tier 2* |
| Words given a verdict | 16 / 29 | **20 / 29** | ≥27 |
| Tracking losses | 3 | **0** ✅ | 0 |
| 1:6 (vanished ayah) | missing | **3/3 shown** ✅ | shown |
| Benchmark false alarms | 4.6% | **2.0%** ✅ | ≤4.6% |
| Mistakes caught | 2/2 | **2/2** ✅ | 2/2 |
| Unit tests | 27 | **32** ✅ | pass |

Every ayah *after* lock-on is now complete. The three still missing —
1:1, 1:2, 1:3 — are the ones recited *before* discovery could lock, and
first-highlight is still 18.6s. **That is exactly Tier 2, confirmed necessary
and now the entire remaining gap.**

Also added: `scripts/replay_session.py`, which re-runs a session log through
matching, tracking and scoring without the audio. Any past log is now a
repeatable test of everything downstream of the model.

*Three of the five were regressions introduced by the previous round of work.*

### Why

**1.1 The tracking pointer stops advancing over already-scored words.**
`src/ui/recitation.py`, `_absorb()`: when a word already has equal or better
evidence we `continue`, and that skips the pointer update at the bottom of the
loop. Re-hearing a word you have already scored does not move the pointer.

```
[33.283s] MATCH mode=tracking ctx=1:4:0 -> 1:4 offset=1
[33.560s] MATCH mode=tracking ctx=1:4:0 -> NO MATCH
[34.453s] MATCH mode=tracking ctx=1:4:0 -> NO MATCH
[34.758s] TRACKER lost position -> falling back to discovery
```

The same stale context is reused 4–6 times in a row all through the log. This
causes **all three** lost positions.

**1.2 "Missed" overrides a word already confirmed correct.**

```
[49.486s] ASR   text="الم"
[49.486s] TRACKER revised 1:7:8 'الضَّالِّينَ' -> '—' (3->1)
```

الضالين was recited correctly. The next chunk matched the opening of
Al-Baqarah, gap-fill marked the word as skipped, and that erased the correct
verdict. Rank 1 means *no evidence*; the quality tuple currently lets it beat
rank 3.

**1.3 A confirmed-correct word can be downgraded to wrong.**
`revised 1:7:5 'الْمَغْضُوبِ' -> 'الْمَغْرُوبِ' (3->2)`. Same root cause as 1.2.

**1.4 Ayahs skipped during re-discovery are never marked.** 1:6 was recited
and never appeared: tracking died at 1:5, re-discovered at 1:7, and the ayah
between was dropped. Gap-fill only runs inside `track()`, not across a
discovery.

**1.5 A pause between ayahs kills tracking.** Pausing between ayahs is correct
recitation. During the pause the model emits breath fragments (`ِيَاكَ`,
`يَّاكَنَا`, `مِنْ دِينٍ`) that fail to match; three in a row at 300ms apart is
under a second, far shorter than a normal breath.

### Work

- Move the pointer update before every `continue` — it records where the
  reciter *is*, not what has been scored
- Absence of evidence never overrides evidence; make "ok" sticky
- Gap-fill across a re-discovery within the same surah
- Do not count a miss for a chunk that looks like silence or a fragment;
  consider making `TRACKING_MAX_MISSES` time-based rather than count-based
- **Add long, pausing, real-session cases to the benchmark** — without these
  the gate cannot see any of the above

### Gate

```bash
python -m pytest tests/ -q
python scripts/benchmark_recordings.py
./run.sh                       # recite Al-Fatiha, then read logs/
```

| Measure | Now | Target |
|---|---|---|
| Al-Fatiha ayahs shown | 3 / 7 | **7 / 7** |
| Words given a verdict | 16 / 29 | **≥ 27 / 29** |
| Tracking losses in one session | 3 | **0** |
| Benchmark false alarms | 4.6% | **≤ 4.6%** |
| Mistakes caught | 2/2 | **2/2** |

### What the result changes

- **If ayahs are all shown but first highlight is still slow** → Tier 2 is
  confirmed necessary and should be done in full.
- **If tracking still dies on pauses** → promote 1.5 to its own tier and treat
  the VAD/windowing as the problem rather than the matcher.
- **If the benchmark still says ~4.6% while the live session is visibly bad**
  → the benchmark is the problem. **Do Tier 3 before Tier 2.** Everything
  after depends on being able to measure what actually happens.

---

# Tier 1.5 — Score what was recited before the lock ✅ DONE

*Unplanned. Found by reciting, not by testing — the gap Tier 3 exists to close.*

### Gate result

| Measure | Before | After | Target |
|---|---|---|---|
| Benchmark false alarms | 2.0% | **0.6%** ✅ | ≤ 2.0% |
| Mistakes caught | 2/2 | **2/2** ✅ | 2/2 |
| Al-Fatiha ayahs shown (`122520`) | 4 / 7 | **7 / 7** ✅ | 7/7 |
| Al-Fatiha words scored (`122520`) | 19 / 29 | **26 / 29** | ≥ 27 |
| Al-Fatiha words scored (`125436`) | 26 / 29 | **29 / 29** ✅ | ≥ 27 |
| 1:1 `بسم الله الرحمن الرحيم` | 1 word, 1 red | **4/4, all green** ✅ | 4/4 |
| 2:1 `الم` | never shown at all | **shown, green** ✅ | shown |
| Unit tests | 32 | **42** ✅ | pass |
| Search cases | 63 | **63** ✅ | pass |

Both word counts are `scripts/replay_session.py` on the two session logs in
`logs/`, before and after, not the figures quoted in Tier 1 — that row counted
slightly differently and does not reproduce.

Tier 1's remaining gap — *"1:1, 1:2, 1:3 are the ones recited before discovery
could lock"* — is closed, and closed without any of Tier 2's priors.

### Why

**1.5.1 An ayah can be one word, and discovery demanded two.**

```
[22.048s] ASR   text="الم"
[22.049s] MATCH mode=discovery ctx=none -> SKIPPED (only 1 word(s), need 2)
[22.348s] ASR   text="الم"
[22.348s] MATCH mode=discovery ctx=none -> SKIPPED (only 1 word(s), need 2)
```

Transcribed perfectly, twice, and thrown away both times — `DISCOVERY_MIN_WORDS
= 2` can never be satisfied by an ayah that is one word long. There are 28 such
ayahs. Ten of them are unique in the whole Quran and were simply unreachable:
`المص` `كهيعص` `طه` `يس` `عسق` `وَالطُّورِ` `مُدْهَامَّتَانِ` `وَالْفَجْرِ`
`وَالضُّحَىٰ` `وَالْعَصْرِ`.

The other eighteen are genuinely ambiguous and must stay refused. `الم` opens
six surahs, and normalization also merges it with the `أَلَمْ` of `أَلَمْ تَرَ`
— 84 occurrences. Hearing `الم` really does not say where the reciter is.

**1.5.2 Everything recited before the lock was discarded.** Discovery answers
only when a phrase is unique, so the opening of a surah — the hardest phrase to
place, and the one every session begins with — is transcribed, refused and
dropped. But the chunk *was* understood; only the position was unknown:

```
[119.096s] ASR   text="سَمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ"
[119.097s] MATCH mode=discovery ctx=none -> NO MATCH
[120.268s] MATCH mode=discovery ctx=none -> 1:1 offset=2
```

Locked **inside** 1:1 at word 2, so `بِسْمِ` and `اللَّهِ` had no verdict and
never could. Same shape in Al-Baqarah: locked at 2:2, so 2:1 `الم` was stepped
over and the ayah never appeared. Gap-fill only ever looked forwards.

**1.5.3 A clipped word was condemned as a wrong one.** `سَمِ` is not a
different word from `بِسْمِ`, it is part of it — the window boundary landed
mid-word. Withholding it did not help: several windows clip the same word the
same way, `EDGE_CONFIRMATIONS` reads that as agreement, and the word goes red
anyway. Every session starts this way, because recitation starts before the
microphone is listening.

### Work

- `QuranIndex._solo_ayah_index` — one-word ayahs, admitted only where that word
  occurs exactly once in the whole Quran
- `QuranIndex.rematch_near()` — align a transcription *backwards* from a known
  position; fills no gaps, claims only what aligns, never crosses into the
  previous surah
- `RecitationTracker._remember_unplaced()` / `_fill_prelock()` — keep refused
  transcriptions for `PRELOCK_BUFFER_SECONDS`, then re-read them once the
  position is known
- `_is_fragment()` — a recited word that is a prefix or suffix of the reference
  is credited, not condemned, and at gap-fill strength so any window that hears
  the word whole replaces it

No new audio and no priors: the same evidence, read with the one piece of
context it was missing.

### What the result changes

- **Tier 2 is now only about latency.** Its two symptoms have come apart: the
  *words* lost before the lock are recovered, the *wait* is not. A reciter
  still faces a blank page for 18.6s and then sees Al-Fatiha appear at once.
  That is worth fixing, but it is no longer a correctness bug, so Tier 2 drops
  below Tier 3 in priority.
- **Tier 4.1 `heardRatio` is now partly redundant.** `_is_fragment` removed
  both fragment false alarms in the benchmark (`يُهَا`/`يَاأَيُّهَا` and
  `رَحْمَنِ`/`الرَّحْمَٰنِ`). Measure what is left before implementing it.
- **One benchmark false alarm remains**, `أَلِيمٌ` for `عَظِيمٌ` in 2:7:11 —
  `عَذَابٌ أَلِيمٌ` is the commoner phrase and the model prefers it. That is an
  ASR substitution, not a matcher bug, which is Tier 5's argument.

---

# Tier 2 — Lock on immediately

*Re-scoped by Tier 1.5. The words lost before the lock are now recovered, so
what is left here is **latency and wrong lock-ons**, not missing verdicts.
That makes it less urgent than Tier 3, which is why Tier 3 should come first.*

### Why

Every opening phrase of Al-Fatiha is ambiguous, so the uniqueness gate
correctly refuses all of them:

| Phrase | Occurrences |
|---|---|
| `بسم الله الرحمن الرحيم` | 114 |
| `الرحمن الرحيم` | ~115 |
| `الحمد لله رب العالمين` | 4 |

The app is *correct* and *useless* — it waits for `مالك يوم الدين`, 18.6s into
the most-recited surah in the Quran, with a blank page. Since Tier 1.5 those
18.6s are no longer *lost* — the whole opening appears at once when the lock
finally lands — but a page that stays blank while you recite still reads as a
broken app. The fix is not better
matching, it is context. Tilawa reached the same conclusion independently:
their remaining errors need "the tracker's `hint` (previous ayah / surah
continuity), not more model training" (`lab/EXPERIMENTS.md`, finding 17).

### Work — in this order, measuring after each

1. **Prefer the page already on screen.** Among ambiguous candidates, pick the
   one on the displayed page. The app opens on page 1, so Al-Fatiha locks on
   the first phrase. Nearly free, and it makes "open the page, then recite"
   behave the way a person expects.
2. **Prefer a surah opening.** `الحمد لله رب العالمين` occurs 4 times; only one
   starts a surah. People start at surah starts.
3. **Tap a word on the Mushaf to start there.** `set_position()` is already
   written and tested in `recitation.py` and has never had a caller.
4. **Do not lock onto the isti'adha.** `أعوذ بالله من الشيطان الرجيم` is not
   Quran, but 16:98 is `فَاسْتَعِذْ بِاللَّهِ مِنَ الشَّيْطَانِ الرَّجِيمِ`,
   so discovery locks on it confidently and lands 278 pages away:

   ```
   [16.195s] ASR   text="أَعُذْ بِاللَّهِ مِنَ الشِّيطَانِ الرَّجِيمِ"
   [16.195s] MATCH mode=discovery ctx=none -> 16:98 offset=3
   [16.196s] MUSHAF load page 278 (for 16:98)
   [21.729s] TRACKER lost position -> falling back to discovery
   [25.052s] TRACKER discovery -> tracking @ 2:2
   ```

   Cost in `logs/hifz-20260919-125436.log`: 9 seconds, a page load the reciter
   never asked for, and six words painted in 16:98–99 that were never recited.
   **The benchmark scores only the expected surah, so it cannot see any of
   this** — `qafirun with auzu basmala` passes while the same isti'adha
   derails a live session. Fixing the blindness matters more than the bug.

### Gate

```bash
./run.sh     # recite Al-Fatiha from page 1
./run.sh     # recite something in the middle of Al-Baqarah, page not open
python scripts/benchmark_recordings.py
```

| Measure | Now | Target |
|---|---|---|
| First word → first highlight (Al-Fatiha) | 18.6s | **< 3s** |
| Discovery hit rate | 4 / 47 | **> 1 in 5** |
| Wrong lock-ons (locked to the wrong ayah) | 1 (isti'adha → 16:98) | **0** |
| Words scored outside the surah recited | 6 | **0** |
| Benchmark false alarms / caught | 0.6% / 2-2 | **no worse** |

The mid-Baqarah run matters: it checks the page prior *helps* when right
without *hurting* when the reciter is somewhere else entirely.

### What the result changes

- **If step 1 alone hits the target** → skip step 2. Fewer priors is better;
  each one is a chance to lock onto the wrong place.
- **If step 1 causes wrong lock-ons** → it is too strong. Make it a tiebreak
  among otherwise-equal candidates rather than an override.
- **If discovery is still slow even with priors** → the bottleneck is the
  uniqueness gate itself, and Tier 5's architecture change moves up.

---

# Tier 3 — Make every session produce test data ✅ DONE

### Gate result

| Measure | Result | Target |
|---|---|---|
| Clips produced | **one per ayah the tracker entered** ✅ | one per ayah recited |
| Clip labels | **match what was recited** ✅ (Al-Mutaffifin: 16/16 correct) | match |
| Replay reproduces the live verdicts | **approximately — see below** | yes |
| Benchmark corpus size | 11 → **11 + every session from now on** | 30+ |
| Unit tests | 43 → **56** ✅ | pass |
| Benchmark false alarms / caught | **0.6% / 2-2**, unchanged ✅ | no worse |

### What it does

`./run.sh --record`:

```
logs/session-20260919-131925/
  session.log                     symlink to the run's debug log
  full.flac                       the whole session, uncut
  000_before-lock.flac            everything recited before discovery answered
  001_83-1_ويل-للمطففين.flac       one clip per ayah the tracker believed
  002_83-2_الذين-اذا-اكتالوا.flac
  manifest.json                   labels, spans, verdicts, transcriptions
```

```bash
python scripts/benchmark_recordings.py --from-session logs/session-*/
```

- Clips are cut on the tracker's **own** ayah transitions and carry the label
  the app believed. A wrong label is a failing case already isolated and
  already carrying the wrong answer.
- **Clips overlap on purpose.** A window is 3s, so the tracker enters the next
  ayah while the current one is still being recited; cutting each clip where
  the next began lopped the end off every ayah — Al-Qadr 1 came out 0.9s long
  for five words. Each clip spans the windows attributed to it, end to end.
- **The audio before the lock is kept as its own clip.** Discovery struggles
  most there, and no hand-made recording contains it: a person recording a
  test case starts cleanly.
- `manifest.json` stores each verdict with its **evidence tuple**, so a replay
  can tell a word that was heard whole from one credited off a clipped edge.
- PyAV already decodes the benchmark recordings, so recording adds nothing new
  to install.

### What the result changed — read this before Tier 4

**The benchmark was measuring the wrong thing, and recording a session is what
showed it.** `surah mutaffifin 1-19` reports a clean run — 69 ok, 0 wrong, 0
missed, 0% false alarms. Cutting the same recording into clips showed
**83:15, 83:16 and 83:17 were never scored at all**, along with 24 of the
recording's 93 words.

The false alarm rate is a fraction of the words *scored*. A word never shown
is not in the denominator, so **a change that quietly shows fewer words scores
better.** Every number in this file above Tier 1.5 was read without knowing
that.

The report now leads with coverage:

```
COVERAGE          154/181 = 85.1% of recited words given a verdict
FALSE ALARM RATE  1/154 = 0.6%
                  (a fraction of words SCORED — showing fewer words
                   flatters it, so coverage must hold)
```

**85.1%, not 99.4%.** One word in seven is never given a verdict at all. The
per-recording column `unseen` says where:

| recording | unseen |
|---|---|
| `surah mutaffifin 1-19` | **24** (all of 83:15–17) |
| `surah ala 11` | **2** — position FOUND, *zero* words scored |
| `fatiha first 2 ayahs` | 1 |

`surah nisa 11 from the middle` is marked `partial=True` and left out of the
total: it starts mid-ayah, so most of 4:11 was never recited and cannot be
counted against the app.

**This re-plans Tier 4 before it starts.** Tier 4 is a list of ways to reduce
false alarms, and false alarms are now 1 word in the whole corpus. Coverage is
27 words. **Fixing what the app never shows is worth more than fixing what it
shows wrongly** — and `surah ala 11` finding the position and scoring nothing
is the place to start.

### Caveat on the round-trip

"Replay reproduces the live verdicts" holds only approximately, and the reason
is worth writing down. A clip replayed on its own gets a different set of
windows than it had inside the session — different boundaries, no stale-window
drops — so a word at a window edge can come back with a different verdict.
Al-Qadr 2:4 الْقَدْرِ was `wrong` live and `ok` from its clip.

That is not a defect in the recording. It is a measurement of how much a
verdict depends on where the window boundaries fell, which was invisible
before. Treat a disagreement as the thing to look at, not as a broken manifest.

---

# Tier 4 — Accuracy, from Tilawa

*`/home/hashus/code/tilawa`, cloned and inspected. Each item is independently
measurable — implement and gate them **one at a time**, in whatever order
Tier 3's corpus says matters.*

> **Tier 3 says: not yet.** False alarms are 1 word in 154; coverage is 27
> words never shown at all. This whole tier reduces false alarms, so it is
> now optimising the smaller of the two numbers. Do the coverage work first
> and re-read this list afterwards — 4.1 `heardRatio` is already partly
> redundant since `_is_fragment` landed in Tier 1.5.

### Why

Our remaining false alarms fall into two families, and Tilawa has a
better-designed answer to both.

**4.1 `heardRatio`** — heard length ÷ expected length. A direct measure of
truncation, replacing our positional window-edge heuristic, which is a proxy
for the same thing and a worse one. Expected to remove the fragment errors:
`يُهَا`/`يَاأَيُّهَا`, `تَعْرَفُوا`/`لِتَعَارَفُوا`, `الذَّكَرِ`/`لِلذَّكَرِ`.

**4.2 Phoneme confusion costs** (`packages/core/src/recitation/phonemeCost.ts`).
Weighted edit distance where acoustically similar letters cost less, with
explicit groups `ذدضتط`, `ظزذصسث`, `قكغ`, `فبم` and pairs `ه/ح`, `ء/ع`, `ن/م`.
Several of our residual errors sit exactly in those groups —
`كَالُوحٌ`/`كَالُوهُمْ` is their `ه/ح`; `أَظِيمٍ`/`عَظِيمٍ` is their `ء/ع`.
**Risk:** this makes the matcher more forgiving, so it is the item most likely
to cost mistake detection. Gate it hard.

**4.3 Only flag a word sitting between two confidently-correct words**
(`correction.ts`). A stronger, better-principled version of our edge rule:
never condemn a word in a region you do not trust.

**4.4 Full pausal (waqf) modelling** (`verdicts.ts`, `pausalPhonemes`).
Ours drops the final vowel; theirs computes the actual pausal pronunciation
including tanween → long alef (`لَغْوًا` → `لَغْوَا`).

**4.5 Never grade tajweed, only word errors.** Worth adopting as a stated
principle so the app does not drift into judging pronunciation quality.

### Gate — after each item separately

```bash
python -m pytest tests/ -q
python scripts/benchmark_recordings.py
```

| Measure | Rule |
|---|---|
| False alarms | must drop |
| Mistakes caught | **must stay at 2/2** — no exceptions |
| Unit tests | all pass |

Keep the change only if both hold. Revert otherwise and write down why — a
rejected idea with a recorded reason is worth more than an unmeasured one.

### What the result changes

- **If 4.1 alone gets false alarms near zero** → stop. Do not add 4.2; a more
  forgiving matcher that is not needed is pure risk to mistake detection.
- **If false alarms stay above ~3% after all of 4.1–4.4** → the matcher is no
  longer the bottleneck. Go to Tier 5.
- **If mistake detection drops on any item** → that item is wrong for this app
  regardless of the false alarm number.

---

# Tier 5 — Architecture

*Only if Tier 4 plateaus. Largest win, largest change, and it invalidates
several tiers above it — so it goes last, not first.*

### Why

Tilawa benchmarked this exhaustively and concluded **ASR quality is the
bottleneck and all Whisper-style approaches fail on the same samples**
(`lab/EXPERIMENTS.md`, findings 1–3). Their answer was to stop using Whisper.

Our own data already points the same way: `whisper-small-quran` measured
*worse* than our base model (23.4% vs 4.6% false alarms) because generative
decoding degrades on 3-second windows. The problem is the architecture, not
the size.

| Model | Size | Why it differs |
|---|---|---|
| `nvidia/stt_ar_fastconformer_hybrid_large_pcd_v1.0` (Tilawa's ONNX) | 88 MB | **CTC, not generative** — structurally cannot hallucinate `تَعْمَى` out of silence. MIT / CC-BY-4.0. |
| `Quran-Lab/zipformer_p-arabic-v3` | 66 MB | Streaming phoneme CTC, 100% recall on Tilawa's corpus. **NPL-1.2 — non-commercial only.** |
| `quran-dev/wav2vec2-ctc-quran-phoneme-…` | large | Phoneme CTC tuned for **mispronunciation detection** — our exact task |

A streaming CTC model would remove, rather than mitigate, three things this
codebase currently works around: silence hallucination, window-boundary word
fragments, and the whole overlapping-window evidence mechanism.

### Work

Bake-off first, port second. Add a second engine behind the existing
`transcribe_window()` seam and measure it on the same corpus before changing
anything else.

### Gate

```bash
python scripts/benchmark_recordings.py --model <candidate>
```

Must beat the current model on false alarms **and** keep 2/2, on a corpus of
at least 30 recordings from Tier 3.

### What the result changes

If a CTC engine wins clearly, Tier 4's matcher work is partly obsolete — the
edge/fragment machinery exists to paper over Whisper's behaviour on short
windows. Check which parts can then be deleted rather than kept.

---

# Recordings still wanted

The benchmark can only catch what is in it. The `لا` bug in 78:35 was found by
reciting, not by testing. **Tier 3 makes most of this automatic** — until then,
by hand.

**Orthography** — where hand-written rules can be silently wrong:
- 78:35–36 `لَّا يَسْمَعُونَ فِيهَا لَغْوًا وَلَا كِذَّابًا` (the shadda bug)
- heavy shadda: 112:1–4
- tanween with a stop, and the same word continued: `لَغْوًا`, `عَظِيمًا`
- alef maddah / dagger alef: `ءَامَنُوا`, `ذَٰلِكَ`, `الرَّحْمَٰنِ`

**Deliberate mistakes**, one per kind (we have 2, want ~8): similar-sounding
letter (`ذ`→`د`, `ح`→`ه`); wrong vowel only (`كَتَبَ`/`كُتِبَ`); skipped word;
skipped ayah; repeated word; stop mid-word and restart; jump to a similar ayah
elsewhere (one `كَلَّا` ayah to another).

**Real conditions** — all 11 current recordings start cleanly and run forward:
- long pauses (5–10s) mid-ayah, then resuming
- coughing, throat-clearing, background noise
- very slow with stretched madd; and very fast
- starting mid-ayah in a *long* surah (Al-Baqarah)
- last ayah of a surah straight into the next surah

**Other voices.** Everything is tuned on one reciter.

Name files `surah X ayahs A-B <what happens>.flac`, drop in `tests/records/`,
then add the expectation to `EXPECTATIONS` in
`scripts/benchmark_recordings.py`.
