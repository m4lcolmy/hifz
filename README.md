# Hifz

A desktop app for practising Quran memorisation. You recite from memory; it
listens, finds your place in the Quran by itself, and shows you on a real
Mushaf page which words you got right and which you did not.

Words start hidden and are uncovered as you recite them. **Correct
recitation is not marked.** A word you got right simply appears, in the
Mushaf's own ink — because that is what a correctly recited page looks like,
a page. Only the exceptions carry a colour: red for a word you got wrong,
amber for one it could not vouch for or that you skipped. So the page is a
memory test rather than something to read from, and the two words worth
looking at are not buried inside a page-wide wash of the colour that means
"fine".

A word shown in plain ink can also be one the app heard but never managed to
place — the run before it works out where you are. It says nothing about
those words, and now it looks like it is saying nothing.

What is never hidden is the page's furniture: the surah title in its banner,
the ayah numbers — so you can see the shape of the page before you start —
and the bismillah line, which belongs to no ayah and so can never be scored
word by word.

Say the wrong word and it is marked where you said it. That sounds obvious
and is not: the word a reciter gets wrong is usually a word that occurs again
a line or two down, and an aligner left to itself would rather jump to it
than call it a mistake — taking your place on the page with it.

Everything runs locally. No audio leaves the machine.

---

## Running it

```bash
./run.sh                          # Whisper, the tuned default
./run.sh --record                 # also capture the session as test data
./run.sh --engine ctc             # a second engine, once one is downloaded
```

Press play, recite, press stop when you are done.

The first few seconds are spent working out where you are; the bar says
`Searching…` while that is happening, and then names the place. The words you
recited while it was searching are not lost — they are re-matched against the
position once it is known, and whatever still cannot be placed is at least
uncovered, so the ayah does not appear to begin halfway through.
This is normal: an opening like `بسم الله الرحمن الرحيم` appears in many
places, so the app waits for a phrase that identifies one spot before it
commits. Once it locks on, it follows you word by word.

## Installing

```bash
conda create -n hifz python=3.12 && conda activate hifz
pip install -r requirements.txt
```

You also need the speech model in `models/` — a CTranslate2 build of
[`tarteel-ai/whisper-base-ar-quran`](https://huggingface.co/tarteel-ai/whisper-base-ar-quran),
which is Whisper fine-tuned on Quran recitation. A general Whisper model will
technically run but will not produce reliable tashkeel, and the app compares
diacritics.

**A bigger model is not better here.** `whisper-small-quran` was measured
against this one on the same recordings and came out far worse — 23.4% false
alarms against 4.6%, and it caught only one of the two deliberate mistakes.
On a complete recording the two transcribe almost identically, but this app
feeds the model 3-second windows, and the small model degrades badly on
partial context. It is also five times slower, which drops 45% of the audio
windows, and dropped windows are exactly what the scoring relies on to
correct a bad first impression of a word. Benchmark before swapping:

```bash
python scripts/benchmark_recordings.py --model path/to/other-model
```

**GPU (optional, strongly recommended):**

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Nothing else to configure. Inference goes from ~600 ms per window to ~100 ms,
so feedback appears as you recite instead of a beat behind. The app detects
the GPU and falls back to CPU on its own.

---

## How it works

```
microphone → VAD → Whisper → search → Mushaf highlighting
```

Audio is cut into 3-second windows that overlap heavily. Each window is
transcribed, and the text is matched against the Quran in one of two modes:

**Discovery** — you just started and the app has no idea where you are. It
looks your words up in an n-gram index of every word in the Quran and accepts
a position *only* when the phrase occurs exactly once. If the phrase is
ambiguous it stays silent rather than guessing.

**Tracking** — your position is known. It now searches only a small window
around where you are, which is both faster and far more accurate. If it loses
you for several chunks in a row it drops back to discovery.

### Why a word can change colour

The same word is transcribed several times, because the audio windows
overlap. Sometimes a window boundary cuts a word in half and the model only
hears a fragment — `الصَّ` instead of `الصَّلَاةَ`. Judging a word on that first
partial hearing marks correct recitation as wrong; on real recitations it did
so for **more than a third of all words**.

So verdicts are ranked by how good the evidence is, and the best evidence
wins: a word heard whole outranks one heard at a window edge, and a word at a
window edge can *confirm* correct recitation but never condemn it. A word may
therefore lose its red as a better look arrives. It never gains one on weaker
evidence.

### Why the red arrives a few seconds late

A word recited at 70 wpm sits inside about twenty overlapping windows, and
Whisper is roughly half right on any one of them. Several of those twenty
come back wrong — and since a correct look permanently outranks a wrong one,
the word went red and then, a second later, went back. Measured on
`tests/records`: 16 words ended red, and **63 more had been red on the way
and were retracted**, 24% of every word scored. A clean recitation of
Al-Mutaffifin put 25 of its 69 words through red and finished with a clean
page.

None of that was visible in the numbers below, because they score the page as
it is *left*. It was extremely visible to the reciter, for whom most of the
page seemed to redden and then clear.

So no colour goes on a word until the looks are finished. A word stays inside
the sliding window for one window's length (`SETTLE_SECONDS`, 4 s) after it
is recited; once nothing has mentioned it for that long, no further window
ever will and the verdict is final. Until then the word is uncovered and left
plain — not green, not amber: it has been heard and the app has not finished
deciding, and saying nothing is the honest way to say that. Stopping settles
everything at once, since the window that would have revised a verdict is
never built.

The cost is that a red appears about four seconds after the mistake, four or
five words further on, and it then stays on the page. The final page is
unchanged: the same 16 words red, the same 100% coverage, the same 2/2
deliberate mistakes. Retracted reds went 24.0% → 0.4%.

### What is not treated as a mistake

Some differences are spelling or pronunciation conventions rather than
memorisation errors, and flagging them would just be noise:

| Difference | Example |
|---|---|
| Hamza seat | `إُولَئِكَ` vs `أُولَٰئِكَ` |
| Doubled alef | `أَنذَرْتَهُمْ` vs `أَأَنذَرْتَهُمْ` |
| Final vowel (waqf) | `يَوْمِ` vs `يَوْمَ` |

The last one matters most: when you stop on a word you pronounce it with
sukun, and when you continue you pronounce its case ending. Both are correct
tajweed, and which one the app hears depends only on where the audio window
happened to cut.

Actual wrong words — `فَلَهُمْ` for `وَلَهُمْ`, `عَظِيمٌ` for `أَلِيمٌ` — are still
reported.

---

## Testing

```bash
python -m pytest tests/ -q
```

Unit tests for the search engine, the scoring rules and the Arabic handling.

```bash
python scripts/test_search_algorithm.py
```

63 cases over the search engine alone: discovery, tracking, cross-ayah
recitation, skipped words, and phrases that *should* be rejected as ambiguous.

```bash
python scripts/benchmark_recordings.py
```

The real one. It plays the recordings in `tests/records/` through the actual
pipeline — same VAD, same model, same matching — and scores the result against
the ground truth written in each filename. It also simulates the real-time
drop behaviour, so its numbers reflect the live app rather than an idealised
offline run.

Four numbers, and they have to be read together:

```
COVERAGE          154/181 = 85.1% of recited words given a verdict
FALSE ALARM RATE  1/154 = 0.6%
RETRACTED REDS    1/154 = 0.6% of words went red and came back
DELIBERATE MISTAKES  2/2 caught
```

**Coverage first.** The false alarm rate is a fraction of the words the app
*scored*, so a word it never showed at all costs nothing — which means a
change that quietly shows fewer words scores better. That is not a
hypothetical: `surah mutaffifin 1-19` reported a clean 0% while never scoring
83:15, 83:16 or 83:17, 24 of the recording's 93 words. The per-recording
`unseen` column says where the gaps are.

The **false alarm rate** is words you recited correctly that the app painted
red. The two recordings with a deliberate mistake must keep catching it, so a
change that "improves" the rate by quietly missing real mistakes cannot pass
unnoticed.

**Retracted reds** are words that were red at some point during the session
and are not red at the end. The other three numbers all score the page as it
is *left*, so this is the only one that can see anything the reciter watched
happen and then unhappen. It was added because nothing else could: the page
was reddening a quarter of every word scored and then clearing it, and every
number here read clean throughout. Anything that waits longer before painting
red drives this down and can only push coverage down with it — never the
other way round, which is why the two are read together.

Current: **100% coverage, 5.3% false alarms, 2/2 caught** over 12 recordings.

The rate went up when the corpus got honest. Eleven of those twelve are short
surahs recorded for testing and score 2.5%; the twelfth is a whole Mushaf page
of An-Nisa recited into a room microphone at speaking pace, and it scores 10%.
That one recording is worth more than the other eleven, because it is the only
one recorded in the conditions the app is actually used in.

### The fetched corpus

Eleven hand-made recordings by one reciter is not a measurement of the app, it
is a measurement of the corpus — and two of the eleven turned out to be
labelled with ayahs they do not contain, because the labels were typed by
hand. Published recitations fix both: the recitation is correct by
construction, and the ground truth comes from the ayah number in the URL.

```bash
python scripts/fetch_recitations.py --plan      # what it would fetch, and on what terms
python scripts/fetch_recitations.py --agree
python scripts/benchmark_recordings.py --from-recitations
```

275 cases, 347 ayahs, five reciters from a very slow teaching pace to a fast
one. The ayahs are chosen deliberately rather than at random, because uniform
sampling over 6,236 ayahs gives mostly medium ayahs from the middle of the
Quran and would miss every case that has ever broken this app:

| stratum | why |
|---|---|
| `muqattaat` | all 30 — `الم` was invisible for months and `حم` still is |
| `openings` | every session starts with one, and discovery refuses the basmala |
| `long` | the hand-made corpus tops out at 17 words; 2:282 is 129 |
| `short` | includes every one-word ayah in the Quran |
| `neighbours` | 83:14 and 83:18 both open `كَلَّا`, and the tracker took the wrong one |
| `runs` | 3–5 consecutive ayahs — gap-fill, page turns, tracking across a boundary |
| `random` | the ordinary ayah, as a control |

The report breaks every number down **per stratum and per reciter**. One
overall number hides a category failing completely, which is exactly how
`surah ala 11` scored zero for months.

**What this corpus cannot do.** A correct recitation contains no mistakes, so
it moves coverage and false alarms and nothing else — `DELIBERATE MISTAKES`
still comes only from a human reciting one wrong on purpose. A corpus that
only measures false alarms rewards an app that says nothing, so run both.

The manifest (`tests/recitations/manifest.json`) is version controlled and the
audio is not: these are somebody else's recordings, and a research corpus is
not a licence to redistribute. The seed is committed, so the corpus is
reproducible and a regression traces to a specific ayah rather than to luck.

### Juz 30, as a session rather than as ayahs

```bash
python scripts/fetch_recitations.py --juz30 --plan
python scripts/fetch_recitations.py --juz30 --agree
python scripts/benchmark_recordings.py --from-recitations --juz30
```

Every corpus above samples *ayahs*, and nobody recites an ayah. They open a
surah, say the basmala and go to the end. That shape is what the app has to
survive — one cold start, one last word, and every ayah boundary in between
— and a corpus of sampled ayahs measures the two hardest moments over and
over and the ordinary twenty seconds between them not at all.

So this one is whole surahs: Ad-Duhaa to An-Nas, all 22, one case each, a
range rather than a selection so the app is not being shown the surahs it is
good at. The fifteen before Ad-Duhaa are out on length alone — An-Naba is 40
ayahs and An-Nazi'at 46, either one more audio than the whole tail.

Only two of the five reciters have a basmala file at this source, so the
corpus says which is which instead of pretending, and the split is the
measurement:

```
surah-from-basmala     9 cases   97.7% coverage   0.8% false alarms
surah-from-ayah-1     13 cases   98.4% coverage   0.7% false alarms
                      22 found / 22            0.8% over 657 words scored
```

**The basmala costs nothing.** That was worth knowing: it is four words of
audio before anything scorable and it is the one phrase discovery is
guaranteed to refuse, and neither shows up in either column.

**What it found instead.** Every failure in juz 30 is the same two defects,
and both are visible in the two worst cases:

| case | what happened |
|---|---|
| An-Nasr 110 | `إِذَا جَاءَ نَصْرُ` heard as `إِذَا جَاءَنَا`, which is unique — to **43:38**. Discovery took it, and tracking then held Az-Zukhruf for twelve windows while the transcription read `إِذَا جَاءَ نَصْرُ اللَّهِ`, a phrase that occurs in exactly one place and is not that one. 110:1 never scored. |
| Al-Qari'ah 101 | `وَمَا أَدْرَاكَ` cut to `وَمَا أَدْرَى`, unique to **46:9**. Page 503 loaded, six words revealed under the wrong ayah. |

A wrong lock is never reconsidered. Every escape hatch the tracker has counts
*misses* — `TRACKING_MAX_MISSES`, `TRACKING_MAX_MISS_SECONDS` — and a
confident wrong lock produces no misses at all: `إِذَا جَاءَ` really is inside
43:38, so tracking succeeds every window and never doubts itself. Those two
are the cases that lost words, so they are the two that were traced; a
mis-lock recovered quickly leaves no mark on the summary table, so the real
count is unknown and is at least two. See Tier K.

Running the same corpus through the second engine says something neither
corpus said before. CTC scores **0.3% false alarms against Whisper's 0.8%**
at the same coverage and half the latency — on the twelve hand-made
recordings it was *seven times worse*. Neither number is wrong; the corpus
decides the answer. And the two engines fail in different places: of the nine
cases with a problem, seven have it under only one engine, and CTC never
mis-hears `إِذَا جَاءَ نَصْرُ`, so An-Nasr comes back 19/19. That is the case
for running both and believing a red word only where they agree — measured,
now, rather than proposed. It is still not a case for switching: this corpus
has no mistakes in it, so it cannot say whether CTC still catches one.

Three of the five words painted red are a fragment, not a mistake: `هُ`
against `أَنزَلْنَاهُ`, `ضَرٌّ` against `ضَالًّا`. The other two are the surah's
own refrain coming back in the wrong slot — `النَّاسِ` where
`النَّفَّاثَاتِ` is due, `النَّارِ` for `النَّاسِ` — which is the model, not the
matcher.

### Recording your own cases

The easy way — every session becomes test data:

```bash
./run.sh --record
```

On stop this writes `logs/session-<timestamp>/`: the whole session as
`full.flac`, one clip per ayah the tracker entered, and a `manifest.json` with
the labels, the time spans, every transcription and every verdict.

```bash
python scripts/benchmark_recordings.py --from-session logs/session-20260919-131925/
```

Clips are cut on the tracker's **own** ayah transitions, so each is labelled
with the ayah the app *believed* it was in. Where that belief was wrong the
clip is worth more, not less: it is the failing case, already isolated and
already carrying the wrong answer. Check a clip before trusting its label,
then move it into `tests/records/` with a proper name.

The audio recited before discovery works out where you are is kept as its own
clip. That is where discovery struggles, and no hand-made recording contains
it, because a person recording a test case starts cleanly.

**A whole surah needs no label at all.** Drop it in
`tests/records/surahs/` named after its surah and the benchmark works the
rest out:

```
093 ad-duhaa.flac
109 al-kafirun mistake 3:2.flac
112.flac
```

A surah runs from ayah 1 to its last ayah and the app knows how many ayahs a
surah has, so the number in the name is the entire ground truth. That closes
the failure Tier D found — two of eleven hand-made recordings named ayahs
their audio does not contain, because the range was typed from memory. The
only thing still typed is `mistake <ayah>:<word>`, counting the word from 1,
because no file can know what you meant to do. `partial` in the name says you
stopped early, so coverage is not charged for what you never recited.

They are scored beside the hand-made recordings and broken out as their own
row, because they are the only audio here with a real microphone in it and
averaging them into studio recitation hides exactly the gap they exist to
measure. See `tests/records/surahs/README.md`.

The older way, for a recording that is not a whole surah — name the file
after what you recited and note any deliberate mistake:

```
surah muddathir 8-10 no mistake.flac
surah baqarah ayahs 6-7 mistake at the and used ف letter instead of و (ولهم).flac
```

Then add a line to `EXPECTATIONS` in `scripts/benchmark_recordings.py` with
the surah, the ayah range, and the position of any deliberate mistake. Mark it
`partial=True` if the recording covers only part of the ayah range, so the
words you never recited are not counted against coverage.

---

## Debugging a bad session

**Every run is logged**, so you never have to reproduce a problem to
investigate it. Logs go to `logs/hifz-<timestamp>.log`, the newest run is
linked as `hifz-session.log`, and only the last 20 are kept. Turn it off with
`./run.sh --no-debug`.

A log records every audio chunk, everything Whisper heard, every match
attempt, and a verdict for every single word. Press ■ to stop — that is what
writes the summary at the bottom:

```
words scored      618   ok=450 (73%)  diac=20 (3%)  wrong=148 (24%)
mushaf            pages=3 highlights=154 OFF-PAGE=0
>>> Tracking was lost 6 time(s) and fell back to discovery.
```

`diac` means the right word with the wrong diacritics — usually the model
failing to vocalise rather than a recitation mistake. If your log is mostly
`diac`, the model is the problem; if it is mostly `wrong`, the matching is.
The summary says which in plain words.

---

## Layout

```
app.py                      entry point
run.sh                      launcher (activates the conda env)
src/
  config.py                 every tunable constant, with the reasoning
  core/
    quran.py                the search engine — n-gram index, discovery, tracking
    arabic.py               normalisation and word splitting
    matching.py             which search runs, shared by app and benchmark
    device.py               GPU detection and CUDA library loading
    page_map.py             surah/ayah → Mushaf page
    qcf_data.py             Mushaf page fonts and glyph data
    debug.py                the session log
  audio/
    capture.py              microphone
    vad.py                  speech detection and windowing
    recorder.py             --record: session audio cut into benchmark cases
    transcriber.py          Whisper, plus the gates that drop hallucinations
    model_loader.py         loads the model off the UI thread
  ui/
    main_window.py          the window
    mushaf_view.py          page rendering and word highlighting
    recitation.py           the scoring rules
data/
  quran.json                the text
  qcf_assets/               604 Mushaf pages + fonts
logs/                       one log per run, newest 20 kept
tests/
  records/                  hand-made recordings, ground truth in the filename
    surahs/                 a whole surah in your own voice — name it after the surah
  recitations/              fetched corpora (audio gitignored, manifests committed)
scripts/
  benchmark_recordings.py   end-to-end benchmark (--from-session, --from-recitations, --juz30)
  recitation_corpus.py      which ayahs each fetched corpus holds, and why those
  fetch_recitations.py      downloads them (audio gitignored, manifest committed)
  test_search_algorithm.py  search engine evaluation
```

`src/config.py` is the place to start if you want to change behaviour. Every
constant there has a comment explaining why it is the value it is.
