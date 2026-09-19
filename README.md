# Hifz

A desktop app for practising Quran memorisation. You recite from memory; it
listens, finds your place in the Quran by itself, and shows you on a real
Mushaf page which words you got right and which you did not.

Words start hidden behind a white mask and are revealed as you recite them —
green for correct, red for a mistake, amber for a word you skipped. So the
page is a memory test, not something to read from.

Everything runs locally. No audio leaves the machine.

---

## Running it

```bash
./run.sh                          # Whisper, the tuned default
./run.sh --record                 # also capture the session as test data
./run.sh --engine ctc             # a second engine, once one is downloaded
```

Press ▶, recite, press ■ when you are done.

The first few seconds show grey text while the app works out where you are.
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
therefore go from red to green as a better look arrives. It never goes the
other way on weaker evidence.

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

Three numbers, and they have to be read together:

```
COVERAGE          154/181 = 85.1% of recited words given a verdict
FALSE ALARM RATE  1/154 = 0.6%
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

Current: **85.1% coverage, 0.6% false alarms, 2/2 caught** — down from 38.6%
false alarms and 1/2 caught.

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

The manual way — record yourself, name the file after what you recited, and
note any deliberate mistake:

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
scripts/
  benchmark_recordings.py   end-to-end benchmark (--from-session for captures)
  test_search_algorithm.py  search engine evaluation
```

`src/config.py` is the place to start if you want to change behaviour. Every
constant there has a comment explaining why it is the value it is.
