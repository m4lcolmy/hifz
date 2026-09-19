# Whole surahs, in your voice

Drop a recording of a complete surah in here and the benchmark scores it.
Nothing else to edit — no `EXPECTATIONS` entry, no ayah range.

```
093 ad-duhaa.flac
109 al-kafirun mistake 3:2.flac
112.flac
```

The number at the start is the surah. Everything after it is for you, except
two words the parser looks for:

| in the name | what it means |
|---|---|
| `mistake <ayah>:<word>` | a word you recited wrong on purpose. The word counted from 1, the way you would count it. Repeat for several. |
| `partial` | you stopped before the end, so coverage is not charged for the words you never recited. |

Then:

```bash
python scripts/benchmark_recordings.py
```

They are scored beside the hand-made recordings in the directory above and
broken out as their own row, `my own recording`.

## Why the label cannot be wrong

A whole surah runs from ayah 1 to its last ayah, and how many ayahs a surah
has is something the app already knows. So the surah number is the entire
label. Two of the eleven original hand-made recordings named ayahs their
audio does not contain — `surah ala 11` is actually At-Tariq 86:11, and
`mutaffifin 1-19` skips three ayahs — because the range was typed from
memory. There is no range to type here.

The one thing still typed is a deliberate mistake, because no file can know
what you meant to do.

## Why these recordings matter more than the other 275

Same code, same day:

| audio | wrong verdicts |
|---|---|
| fetched corpus, 273 cases, 5 qāriʾ | **2.2%** |
| this reciter's own microphone | **25–48%** |

Every file in `tests/recitations/` was recorded to be published: a studio, a
close microphone, a qāriʾ at a measured pace. The fetched juz 30 corpus fixes
the *shape* of a test — a whole surah, opened cold on its basmala — and
cannot fix the conditions. Whatever costs those thirty points is not in any
downloaded corpus, so nothing downloaded can be used to find it.

Recite normally. Do not slow down for the app, do not move closer to the
microphone, and do not re-record one that went badly — the one that went
badly is the case worth having.

## The easier way

```bash
./run.sh --record
```

Every session becomes a `logs/session-<timestamp>/` directory with the audio,
one clip per ayah, and the app's own verdicts. `full.flac` in there is a whole
surah if that is what you recited; rename it and move it here.

**Listen to a clip before trusting its label.** Automation makes a wrong
label cheaper, not rarer.
