"""The fetched corpus: what is in it, why those ayahs, and how it is scored.

Tier G exists because the hand-made corpus is eleven recordings by one voice
and **two of the eleven were labelled with ayahs they do not contain**. So
the tests here are not about download plumbing. They are about the two things
that made the old corpus untrustworthy:

  - the *labels*, which were typed by a person and were wrong at a rate
    nobody was measuring — here they are generated from the ayah number in
    the URL, and these tests hold them to that;
  - the *contents*, which were whatever was easy to recite — here they are
    stratified deliberately, and these tests hold each stratum to the failure
    it was put in for.

One more, because it is the easiest thing to get wrong: a corpus of correct
recitations can only ever measure coverage and false alarms, and a corpus
that only measures false alarms rewards an app that says nothing.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.recitation_corpus import (
    MANIFEST_PATH, MANIFEST_VERSION, MUQATTAAT, RECITATIONS_DIR, RECITERS,
    SEED, UNAVAILABLE_PATH, Ayah, Case, all_cases, build_corpus,
    build_manifest, case_from_entry, decodes, load_ayahs, muqattaat_openings,
    near_identical_neighbours, one_word_ayahs, read_manifest, write_manifest,
)

AYAHS = load_ayahs()
STRATA = build_corpus(SEED, ayahs=AYAHS)
CASES = all_cases(STRATA)
BY_NAME = {s.name: s for s in STRATA}
WORDS = {(a.surah, a.ayah): a.words for a in AYAHS}
SURAH_LENGTH: dict[int, int] = {}
for _a in AYAHS:
    SURAH_LENGTH[_a.surah] = max(SURAH_LENGTH.get(_a.surah, 0), _a.ayah)


def ayahs_of(case: Case) -> list[tuple[int, int]]:
    return [(case.surah, a) for a in range(case.first_ayah, case.last_ayah + 1)]


# ═══════════════════════════════════════════════════════════════════════
# The gate
# ═══════════════════════════════════════════════════════════════════════

class GateTests(unittest.TestCase):
    """Tier G's targets, as assertions rather than as prose."""

    def test_the_corpus_is_the_size_the_tier_asked_for(self):
        """11 recordings was never a measurement of the app; it was a
        measurement of the corpus."""
        ayahs = {k for c in CASES for k in ayahs_of(c)}
        self.assertGreaterEqual(len(ayahs), 200, "target is 200+ ayahs")

    def test_more_than_one_voice(self):
        """Everything is tuned on one reciter. If the tuning is overfitted,
        only other voices can show it."""
        self.assertGreaterEqual(len({c.reciter for c in CASES}), 3)

    def test_every_stratum_reached_its_target(self):
        for s in STRATA:
            with self.subTest(stratum=s.name):
                self.assertEqual(len(s.cases), s.target)

    def test_the_deliberate_mistakes_are_not_in_here_and_must_not_appear(self):
        """A correct recitation contains no mistakes. This corpus moves
        coverage and false alarms and nothing else — and a corpus that only
        measures false alarms rewards an app that says nothing, so the
        hand-made recordings stay exactly as important."""
        manifest = build_manifest(SEED)
        for entry in manifest["cases"]:
            self.assertNotIn("mistakes", entry)


# ═══════════════════════════════════════════════════════════════════════
# What is in each stratum, and why
# ═══════════════════════════════════════════════════════════════════════

class MuqattaatTests(unittest.TestCase):
    """الم was invisible for months and حم still is."""

    def test_all_thirty_openings_are_here(self):
        """29 surahs open with muqatta'at and Ash-Shura does it twice —
        42:1 is حم and 42:2 is عسق. The roadmap said "28", which is the count
        of one-word ayahs; those are a different set that overlaps in 20."""
        found = {(a.surah, a.ayah) for a in muqattaat_openings(AYAHS)}
        self.assertEqual(len(found), 30)
        self.assertIn((42, 1), found)
        self.assertIn((42, 2), found)

    def test_a_muqattaat_buried_in_a_longer_ayah_counts_too(self):
        """الٓر is not a standalone ayah — it opens 10:1 as the first word of
        الر تِلْكَ آيَاتُ الْكِتَابِ. That is a different failure from "cannot
        find a one-word ayah": it is a word silently dropped."""
        found = {(a.surah, a.ayah) for a in muqattaat_openings(AYAHS)}
        for opening in ((10, 1), (11, 1), (13, 1), (27, 1), (38, 1), (50, 1), (68, 1)):
            self.assertIn(opening, found)

    def test_alam_tara_is_not_mistaken_for_alif_lam_mim(self):
        """normalize() folds الم together with the أَلَمْ of أَلَمْ تَرَ — that
        is exactly why discovery refuses them — so matching on normalized
        text would sweep in 94:1 and 105:1 as muqatta'at. Matching is on the
        raw text, which the dataset writes without diacritics."""
        found = {(a.surah, a.ayah) for a in muqattaat_openings(AYAHS)}
        self.assertNotIn((94, 1), found)     # أَلَمْ نَشْرَحْ
        self.assertNotIn((105, 1), found)    # أَلَمْ تَرَ كَيْفَ

    def test_the_stratum_fetches_them_without_a_basmala(self):
        """الم on its own is the diagnostic case. Prepending a basmala gives
        the matcher four extra words to find the position with, which is the
        opposite of what this stratum measures."""
        for case in BY_NAME["muqattaat"].cases:
            with self.subTest(case=case.id):
                self.assertNotIn(0, case.parts)


class ShortAyahTests(unittest.TestCase):
    def test_every_one_word_ayah_in_the_quran_is_in_the_corpus(self):
        """There are exactly 28, they were unreachable behind a two-word
        discovery floor for months, and eight of them are ordinary words
        (وَالْعَصْرِ, الْحَاقَّةُ, مُدْهَامَّتَانِ) that the muqatta'at stratum
        does not cover."""
        solo = {(a.surah, a.ayah) for a in one_word_ayahs(AYAHS)}
        self.assertEqual(len(solo), 28)
        covered = {k for c in CASES for k in ayahs_of(c)}
        self.assertEqual(solo - covered, set())

    def test_the_short_stratum_is_actually_short(self):
        for case in BY_NAME["short"].cases:
            with self.subTest(case=case.id):
                self.assertLessEqual(WORDS[(case.surah, case.first_ayah)], 3)


class LongAyahTests(unittest.TestCase):
    def test_every_long_case_is_longer_than_anything_hand_recorded(self):
        """The longest hand-made recording is 17 words and nothing has ever
        tested the tracker over a longer distance."""
        self.assertTrue(BY_NAME["long"].cases)
        for case in BY_NAME["long"].cases:
            with self.subTest(case=case.id):
                self.assertGreater(case.words, 50)

    def test_the_longest_ayah_in_the_quran_is_reachable(self):
        """2:282 is 129 words. It does not have to be sampled, but it has to
        be in the pool — if the filter excluded it the stratum is wrong."""
        pool = [a for a in AYAHS if a.words > 50]
        self.assertIn((2, 282), {(a.surah, a.ayah) for a in pool})


class NeighbourTests(unittest.TestCase):
    def test_the_stratum_contains_the_failure_it_is_named_for(self):
        """83:14 and 83:18 both open كَلَّا and sit four ayahs apart. Tracking
        anchored on the shared opening word and dragged the alignment onto
        the wrong ayah — that is the bug this stratum exists to keep
        measuring."""
        near = {(a.surah, a.ayah) for a in near_identical_neighbours(AYAHS)}
        self.assertIn((83, 14), near)
        self.assertIn((83, 18), near)

    def test_a_shared_common_opener_is_not_enough(self):
        """إِنَّ opens 269 ayahs. If a shared إِنَّ qualified, the stratum would
        be most of the Quran and would measure nothing in particular."""
        near = near_identical_neighbours(AYAHS)
        self.assertLess(len(near), len(AYAHS) // 4)

    def test_ar_rahmans_refrain_is_in_the_pool(self):
        """فَبِأَيِّ آلَاءِ رَبِّكُمَا تُكَذِّبَانِ, 31 times over."""
        near = {(a.surah, a.ayah) for a in near_identical_neighbours(AYAHS)}
        self.assertIn((55, 13), near)


class RunTests(unittest.TestCase):
    """Half the app's behaviour is transitions, and single ayahs see none."""

    def test_runs_are_consecutive_and_stay_inside_one_surah(self):
        for case in BY_NAME["runs"].cases:
            with self.subTest(case=case.id):
                self.assertGreater(case.last_ayah, case.first_ayah)
                self.assertEqual(
                    list(case.parts),
                    list(range(case.first_ayah, case.last_ayah + 1)),
                )

    def test_a_run_never_falls_off_the_end_of_its_surah(self):
        """A 404 here is not a missing file, it is a corpus that claims an
        ayah the Quran does not have."""
        for case in BY_NAME["runs"].cases:
            with self.subTest(case=case.id):
                self.assertLessEqual(case.last_ayah, SURAH_LENGTH[case.surah])

    def test_runs_are_three_to_five_ayahs(self):
        for case in BY_NAME["runs"].cases:
            with self.subTest(case=case.id):
                self.assertIn(case.last_ayah - case.first_ayah + 1, (3, 4, 5))


class OpeningTests(unittest.TestCase):
    def test_openings_ask_for_the_basmala_first(self):
        """Every session starts with one and discovery refuses it outright —
        بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ matches 114 places."""
        cases = BY_NAME["openings"].cases
        self.assertTrue(cases)
        for case in cases:
            with self.subTest(case=case.id):
                self.assertEqual(case.first_ayah, 1)
                if case.surah != 1:
                    self.assertEqual(case.parts[0], 0)

    def test_at_tawbah_is_never_given_a_basmala(self):
        """At-Tawbah has none, and inventing one would be a wrong label of
        exactly the kind this corpus exists to rule out."""
        for case in CASES:
            with self.subTest(case=case.id):
                if case.surah == 9:
                    self.assertNotIn(0, case.parts)

    def test_al_fatiha_is_not_given_a_second_basmala(self):
        """1:1 *is* the basmala. Prepending another would put the phrase in
        the audio twice and charge the app for words nobody recited."""
        for case in CASES:
            if case.surah == 1 and case.first_ayah == 1:
                with self.subTest(case=case.id):
                    self.assertNotIn(0, case.parts)


# ═══════════════════════════════════════════════════════════════════════
# The sampler
# ═══════════════════════════════════════════════════════════════════════

class SamplerTests(unittest.TestCase):
    def test_the_same_seed_gives_the_same_corpus(self):
        """Committed, so a regression traces to a specific ayah rather than
        to luck."""
        again = all_cases(build_corpus(SEED, ayahs=AYAHS))
        self.assertEqual([c.id for c in CASES], [c.id for c in again])

    def test_a_different_seed_gives_a_different_corpus(self):
        other = all_cases(build_corpus(SEED + 1, ayahs=AYAHS))
        self.assertNotEqual([c.id for c in CASES], [c.id for c in other])

    def test_no_ayah_is_fetched_twice(self):
        """Strata exclude what earlier ones took, so a per-stratum number is
        attributable to one stratum and the download is not paid for twice."""
        seen: set[tuple[int, int]] = set()
        for case in CASES:
            for key in ayahs_of(case):
                self.assertNotIn(key, seen, f"{key} appears twice ({case.id})")
                seen.add(key)

    def test_every_case_names_a_real_ayah(self):
        for case in CASES:
            with self.subTest(case=case.id):
                for surah, ayah in ayahs_of(case):
                    self.assertIn((surah, ayah), WORDS)

    def test_the_reciters_are_evenly_spread(self):
        """A per-reciter comparison is worthless if one voice has six cases.
        Perfectly even is not required; badly lopsided is a bug."""
        counts: dict[str, int] = {}
        for case in CASES:
            counts[case.reciter] = counts.get(case.reciter, 0) + 1
        fair = len(CASES) / len(RECITERS)
        for reciter, n in counts.items():
            with self.subTest(reciter=reciter):
                self.assertGreater(n, fair * 0.6)

    def test_every_stratum_uses_several_reciters(self):
        """Otherwise per-stratum and per-reciter are the same cut of the data
        and neither breakdown means anything."""
        for s in STRATA:
            with self.subTest(stratum=s.name):
                self.assertGreaterEqual(len({c.reciter for c in s.cases}), 3)

    def test_word_counts_agree_with_the_app_that_will_score_them(self):
        """`Case.words` is the denominator in the plan; the benchmark uses
        `QuranIndex.words_in_range()`. Two different word counts would make
        coverage mean two different things."""
        from src.core.quran import QuranIndex
        index = QuranIndex()
        for case in CASES[::17]:          # every 17th — the index is not cheap
            with self.subTest(case=case.id):
                self.assertEqual(
                    case.words,
                    index.words_in_range(case.surah, case.first_ayah, case.last_ayah),
                )


# ═══════════════════════════════════════════════════════════════════════
# The manifest — the version-controlled half
# ═══════════════════════════════════════════════════════════════════════

class ManifestTests(unittest.TestCase):
    def test_it_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            write_manifest(build_manifest(SEED), path)
            self.assertEqual(read_manifest(path)["cases"],
                             build_manifest(SEED)["cases"])

    def test_a_manifest_this_code_cannot_read_is_refused(self):
        """Silently misreading a corpus definition would produce numbers that
        look fine and mean nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps({"version": MANIFEST_VERSION + 99}))
            with self.assertRaises(SystemExit):
                read_manifest(path)

    def test_a_missing_manifest_says_what_to_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                read_manifest(Path(tmp) / "nothing.json")
            self.assertIn("fetch_recitations", str(caught.exception))

    def test_the_committed_manifest_is_the_one_the_committed_seed_produces(self):
        """The manifest is the corpus. If it drifts from the seed — hand
        edited, or generated with a different seed and committed by accident
        — then every stored number is measured against something nobody can
        reproduce."""
        if not MANIFEST_PATH.exists():
            self.skipTest("no committed manifest yet")
        committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(committed, build_manifest(SEED))

    def test_it_records_where_the_audio_came_from_and_on_what_terms(self):
        """These are somebody else's recordings. `fetch_ctc_model.py` records
        a model's licence for the same reason."""
        manifest = build_manifest(SEED)
        self.assertTrue(manifest["source"]["terms"])
        self.assertTrue(manifest["source"]["terms_url"])
        self.assertTrue(manifest["source"]["base_url"])


class AudioStaysOutOfGitTests(unittest.TestCase):
    """A research corpus is not a licence to redistribute."""

    def test_every_file_lands_under_the_gitignored_directory(self):
        for case in CASES:
            with self.subTest(case=case.id):
                path = (RECITATIONS_DIR / case.filename).resolve()
                self.assertTrue(path.is_relative_to(RECITATIONS_DIR.resolve()))

    def test_gitignore_excludes_the_audio_and_keeps_the_manifest(self):
        rules = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("tests/recitations/*", rules)
        self.assertIn("!tests/recitations/manifest.json", rules)

    def test_concatenated_audio_is_flac_and_single_ayahs_are_left_alone(self):
        """Gluing mp3 frames is not decoding them and the seams come out as
        clicks, which the VAD then hears as speech onset. A single ayah needs
        no joining, so it keeps the bytes that were served."""
        for case in CASES:
            with self.subTest(case=case.id):
                suffix = Path(case.filename).suffix
                self.assertEqual(suffix, ".flac" if len(case.parts) > 1 else ".mp3")

    def test_no_two_cases_write_to_the_same_file(self):
        names = [c.filename for c in CASES]
        self.assertEqual(len(names), len(set(names)))


# ═══════════════════════════════════════════════════════════════════════
# Expectations are generated, not typed
# ═══════════════════════════════════════════════════════════════════════

class ExpectationTests(unittest.TestCase):
    """Tier D found two of eleven hand-typed labels naming ayahs the audio
    does not contain. Here the label comes from the ayah number in the URL,
    and this is what holds it there."""

    def setUp(self):
        from scripts.benchmark_recordings import cases_from_recitations
        self.load = cases_from_recitations
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.manifest_path = self.root / "manifest.json"
        self.manifest = build_manifest(SEED)
        write_manifest(self.manifest, self.manifest_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _materialise(self, entries):
        for entry in entries:
            path = self.root / entry["file"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")

    def test_the_label_is_the_manifest_entry_not_the_filename(self):
        entries = self.manifest["cases"][:5]
        self._materialise(entries)
        loaded = self.load(self.manifest_path, self.root)
        self.assertEqual(len(loaded), 5)
        for (_path, expectation), entry in zip(loaded, entries):
            self.assertEqual(expectation.surah, entry["surah"])
            self.assertEqual(list(expectation.ayahs), entry["ayahs"])

    def test_a_fetched_case_never_claims_a_deliberate_mistake(self):
        """Published recitation by an established qāriʾ is correct by
        construction. Anything flagged here is a false alarm, and calling one
        a caught mistake would make the corpus say the opposite of the
        truth."""
        self._materialise(self.manifest["cases"][:10])
        for _path, expectation in self.load(self.manifest_path, self.root):
            self.assertEqual(expectation.mistakes, ())
            self.assertEqual(expectation.mistake_count, 0)

    def test_a_case_carries_its_stratum_and_its_reciter(self):
        """Without both, the report cannot break a total down and a stratum
        failing completely stays invisible."""
        self._materialise(self.manifest["cases"][:10])
        for _path, expectation in self.load(self.manifest_path, self.root):
            self.assertTrue(expectation.stratum)
            self.assertTrue(expectation.reciter)

    def test_audio_that_was_not_fetched_is_skipped_not_invented(self):
        """A manifest entry with no file on disk is a download that has not
        happened. Scoring it as zero coverage would blame the app for it."""
        self._materialise(self.manifest["cases"][:3])
        self.assertEqual(len(self.load(self.manifest_path, self.root)), 3)

    def test_only_can_pick_a_whole_stratum_or_a_whole_reciter(self):
        """The per-stratum breakdown's whole purpose is to send you back to
        look at one stratum, and the filenames are `002_001.mp3` — filtering
        on the name alone cannot express "the muqatta'at cases"."""
        from scripts.benchmark_recordings import selected
        self._materialise(self.manifest["cases"][:10])
        loaded = self.load(self.manifest_path, self.root)
        for needle in ("muqattaat", loaded[0][1].reciter):
            with self.subTest(needle=needle):
                picked = [e for p, e in loaded if selected(needle, p, e)]
                self.assertTrue(picked)
                self.assertLess(len(picked), len(loaded) + 1)

    def test_a_case_is_never_partial(self):
        """`partial` exists because two hand-made recordings do not contain
        all the ayahs their names claim. A fetched file contains exactly the
        ayah it was fetched for, so every word is chargeable — and a fetched
        case must never be allowed to opt out of coverage."""
        self._materialise(self.manifest["cases"][:10])
        for _path, expectation in self.load(self.manifest_path, self.root):
            self.assertFalse(expectation.partial)


# ═══════════════════════════════════════════════════════════════════════
# Reporting per stratum
# ═══════════════════════════════════════════════════════════════════════

class PerStratumReportTests(unittest.TestCase):
    """One overall number hides a category failing completely — which is
    exactly how "surah ala 11" scored zero for months."""

    def _result(self, stratum, reciter, expected, ok, false_alarms=0,
                found=True, partial=False):
        from scripts.benchmark_recordings import Expectation, Result
        expectation = Expectation(
            pattern=stratum, surah=1, ayahs=(1, 1), stratum=stratum,
            reciter=reciter, partial=partial,
        )
        r = Result(name=f"{stratum}-{reciter}", expectation=expectation)
        r.expected_words = expected
        r.ok = ok
        r.wrong = false_alarms
        r.found_position = found
        r.flagged = {(1, 1, i) for i in range(false_alarms)}
        return r

    def test_a_stratum_scoring_zero_is_visible_while_the_total_looks_fine(self):
        from scripts.benchmark_recordings import group_by, totals_for
        results = [self._result("random", "a", 100, 100) for _ in range(9)]
        results.append(self._result("muqattaat", "b", 30, 0, found=False))

        overall = totals_for(results, "all")
        self.assertGreater(overall.coverage, 95)     # the total says "fine"

        rows = {t.label: t for t in group_by(results, lambda r: r.expectation.stratum)}
        self.assertEqual(rows["muqattaat"].coverage, 0.0)
        self.assertEqual(rows["random"].coverage, 100.0)

    def test_a_reciter_scoring_badly_is_visible(self):
        """If the tuning is overfitted to one voice, this is where it shows."""
        from scripts.benchmark_recordings import group_by
        results = [self._result("random", "tuned-on", 100, 100) for _ in range(5)]
        results += [self._result("random", "other", 100, 40) for _ in range(5)]
        rows = {t.label: t for t in group_by(results, lambda r: r.expectation.reciter)}
        self.assertEqual(rows["tuned-on"].coverage, 100.0)
        self.assertEqual(rows["other"].coverage, 40.0)

    def test_false_alarms_are_a_fraction_of_what_was_scored(self):
        """Showing fewer words flatters the rate, so coverage is reported
        beside it and both are computed from the same slice."""
        from scripts.benchmark_recordings import totals_for
        results = [self._result("random", "a", 100, 90, false_alarms=10)]
        t = totals_for(results, "all")
        self.assertEqual(t.scored, 100)
        self.assertEqual(t.false_alarm_rate, 10.0)
        self.assertEqual(t.coverage, 100.0)

    def test_a_partial_case_is_not_charged_for_words_never_recited(self):
        """Al-Mutaffifin 1-19 does not contain 83:15-17, and charging the app
        24 words it was never given is how a correct tracker was made to look
        broken."""
        from scripts.benchmark_recordings import totals_for
        results = [self._result("", "", 93, 69, partial=True)]
        t = totals_for(results, "all")
        self.assertEqual(t.expected, 69)
        self.assertEqual(t.coverage, 100.0)


# ═══════════════════════════════════════════════════════════════════════
# Audio that is not audio
# ═══════════════════════════════════════════════════════════════════════

class CorruptSourceTests(unittest.TestCase):
    """Two of this source's own mp3s are corrupt — 11:23 and 33:50 in the
    muʿallim set. Both download byte-for-byte complete, matching
    Content-Length, so nothing about the transfer is wrong and retrying does
    not help. The first full run died on one of them sixty cases in, after
    ten minutes, with an av error that named no file."""

    def test_a_file_that_is_not_audio_is_reported_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not-audio.mp3"
            path.write_bytes(b"\xff\xfb" + b"garbage" * 500)
            self.assertTrue(decodes(path), "a non-audio file must be rejected")

    def test_an_empty_file_is_not_mistaken_for_silence(self):
        """Zero bytes decodes without raising. Treating that as a valid
        recording would score the app as covering none of an ayah nobody
        played."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.mp3"
            path.write_bytes(b"")
            self.assertTrue(decodes(path))

    def test_real_audio_passes(self):
        records = PROJECT_ROOT / "tests" / "records"
        flacs = sorted(records.glob("*.flac"))
        if not flacs:
            self.skipTest("no hand-made recordings on disk")
        self.assertEqual(decodes(flacs[0]), "")

    def test_the_record_of_unavailable_cases_is_not_committed(self):
        """It is a fact about a server on a given day, not about the corpus.
        Committing it would freeze today's outage into the definition."""
        rules = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("tests/recitations/*", rules)
        self.assertNotIn(f"!{UNAVAILABLE_PATH.name}", rules)


if __name__ == "__main__":
    unittest.main()
