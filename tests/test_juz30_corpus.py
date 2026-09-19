"""The juz 30 corpus: whole short surahs, recited the way a reciter recites.

Every other corpus in this project samples *ayahs*, and nobody recites an
ayah. They open a surah, say the basmala and go to the end. So the unit here
is the surah, and what these tests hold is the two things that makes the
corpus worth having separately from the stratified one:

  - a case is a **whole** surah — first ayah to last, nothing sampled out of
    the middle, or it stops being the shape of a session;
  - a case that says `basmala` **has** one. Thirteen of these twenty-two do
    not, because three of the five reciters have no basmala file at the
    source, and the first cut of this corpus asked for one anyway and let the
    fetcher shrug at the 404 — leaving thirteen manifest entries and thirteen
    filenames saying `basmala` about audio with none. Wrong labels are the
    exact failure the fetched corpora exist to rule out, and a corpus can
    produce one about itself just as easily as a person can type one.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.recitation_corpus import (
    BASMALA_RECITERS, JUZ30_MANIFEST_PATH, JUZ30_WHOLE_SURAH_FROM,
    JUZ30_WHOLE_SURAH_TO, MANIFEST_PATH, RECITERS, SEED, all_cases,
    build_corpus, build_juz30_corpus, build_juz30_manifest, load_ayahs,
)

AYAHS = load_ayahs()
STRATA = build_juz30_corpus(SEED, ayahs=AYAHS)
CASES = all_cases(STRATA)
BY_NAME = {s.name: s for s in STRATA}

SURAH_LENGTH: dict[int, int] = {}
WORDS: dict[int, int] = {}
for _a in AYAHS:
    SURAH_LENGTH[_a.surah] = max(SURAH_LENGTH.get(_a.surah, 0), _a.ayah)
    WORDS[_a.surah] = WORDS.get(_a.surah, 0) + _a.words


class WholeSurahTests(unittest.TestCase):
    """A case is a session, and a session is a whole surah."""

    def test_every_case_runs_from_the_first_ayah_to_the_last(self):
        """Half a surah is a sampled run, which the other corpus already has.
        What this one is for is the shape no sample produces: one cold start,
        one last word, and every ayah boundary in between."""
        for case in CASES:
            with self.subTest(case=case.id):
                self.assertEqual(case.first_ayah, 1)
                self.assertEqual(case.last_ayah, SURAH_LENGTH[case.surah])

    def test_the_parts_are_every_ayah_in_order_with_nothing_missing(self):
        """A gap here would be Al-Mutaffifin all over again: a case named for
        ayahs it does not contain, charging the app for words nobody
        recited."""
        for case in CASES:
            with self.subTest(case=case.id):
                ayah_parts = [p for p in case.parts if p != 0]
                self.assertEqual(ayah_parts,
                                 list(range(1, SURAH_LENGTH[case.surah] + 1)))

    def test_the_word_count_is_the_whole_surah(self):
        for case in CASES:
            with self.subTest(case=case.id):
                self.assertEqual(case.words, WORDS[case.surah])

    def test_it_is_the_whole_tail_and_not_a_selection(self):
        """Ad-Duhaa to An-Nas, every one. A range rather than a hand-picked
        list, so the app cannot be shown only the surahs it is good at."""
        wanted = set(range(JUZ30_WHOLE_SURAH_FROM, JUZ30_WHOLE_SURAH_TO + 1))
        self.assertEqual({c.surah for c in CASES}, wanted)
        self.assertEqual(len(CASES), len(wanted))

    def test_every_case_is_inside_juz_30(self):
        for case in CASES:
            with self.subTest(case=case.id):
                self.assertGreaterEqual(case.surah, 78)

    def test_the_long_surahs_of_juz_30_are_deliberately_left_out(self):
        """An-Naba is 40 ayahs and An-Nazi'at 46 — either one costs more
        audio than the whole tail put together, and a corpus nobody runs
        because it takes an afternoon measures nothing."""
        for case in CASES:
            with self.subTest(case=case.id):
                self.assertLessEqual(SURAH_LENGTH[case.surah], 19)


class BasmalaTests(unittest.TestCase):
    """A case that says basmala has one, and one that has none says so."""

    def test_the_two_strata_are_exactly_the_basmala_split(self):
        for case in BY_NAME["surah-from-basmala"].cases:
            with self.subTest(case=case.id):
                self.assertIn(case.reciter, BASMALA_RECITERS)
        for case in BY_NAME["surah-from-ayah-1"].cases:
            with self.subTest(case=case.id):
                self.assertNotIn(case.reciter, BASMALA_RECITERS)

    def test_a_case_asks_for_the_basmala_only_where_the_source_has_one(self):
        """The 404 used to be absorbed by the fetcher, which meant the
        manifest claimed audio that was never downloaded. A corpus definition
        that is wrong about its own audio cannot be used to catch a label
        that is wrong about someone else's."""
        for case in CASES:
            with self.subTest(case=case.id):
                self.assertEqual(0 in case.parts,
                                 case.reciter in BASMALA_RECITERS)

    def test_the_filename_says_which_it_is(self):
        """`112_001-004.flac` and `112_001-004_basmala.flac` are the same
        four ayahs with and without four words in front, and the four words
        are the whole point of one of them."""
        for case in CASES:
            with self.subTest(case=case.id):
                self.assertEqual(case.filename.endswith("_basmala.flac"),
                                 0 in case.parts)

    def test_both_strata_are_big_enough_to_compare(self):
        """The split only earns its place if the per-stratum table can
        actually answer what those four words cost."""
        for stratum in STRATA:
            with self.subTest(stratum=stratum.name):
                self.assertGreaterEqual(len(stratum.cases), 5)


class ReciterTests(unittest.TestCase):
    def test_every_voice_is_used(self):
        """Dropping the three reciters with no basmala file would cost the
        slow and very slow end of the pace range, which is where a 4-second
        window has the least to work with."""
        self.assertEqual({c.reciter for c in CASES}, {r.id for r in RECITERS})

    def test_no_voice_is_left_with_too_few_cases_to_read(self):
        """A per-reciter row computed from two cases says nothing about the
        voice."""
        counts: dict[str, int] = {}
        for case in CASES:
            counts[case.reciter] = counts.get(case.reciter, 0) + 1
        for reciter, n in counts.items():
            with self.subTest(reciter=reciter):
                self.assertGreaterEqual(n, 4)


class SeparateFromTheStratifiedCorpusTests(unittest.TestCase):
    """Why this is a second manifest and not a seventh stratum."""

    def test_the_stratified_corpus_is_untouched_by_this_one_existing(self):
        """Inserting a whole-surah stratum would have re-rolled the shared
        seed, changing all 275 case ids and orphaning the audio already
        downloaded. This is the assertion that says it did not."""
        import json
        if not MANIFEST_PATH.exists():
            self.skipTest("no committed manifest yet")
        committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        from scripts.recitation_corpus import build_manifest
        self.assertEqual(committed["cases"], build_manifest(SEED)["cases"])

    def test_no_case_writes_over_a_stratified_corpus_file(self):
        """Both corpora land in the same directory. A collision would have
        one corpus silently scoring the other's audio."""
        mine = {c.filename for c in CASES}
        theirs = {c.filename for c in all_cases(build_corpus(SEED, ayahs=AYAHS))}
        self.assertEqual(mine & theirs, set())

    def test_no_two_cases_here_write_to_the_same_file(self):
        self.assertEqual(len(CASES), len({c.filename for c in CASES}))

    def test_it_deliberately_re_uses_ayahs_the_other_corpus_took(self):
        """Not an accident, and the reason this is a separate manifest: the
        stratified corpus's strata exclude each other so a per-stratum number
        belongs to one stratum, and a whole surah cannot obey that rule —
        112:1 is an `openings` case already."""
        theirs = set()
        for case in all_cases(build_corpus(SEED, ayahs=AYAHS)):
            for ayah in range(case.first_ayah, case.last_ayah + 1):
                theirs.add((case.surah, ayah))
        mine = {(c.surah, a) for c in CASES
                for a in range(c.first_ayah, c.last_ayah + 1)}
        self.assertTrue(mine & theirs)


class ManifestTests(unittest.TestCase):
    def test_the_same_seed_gives_the_same_corpus(self):
        again = all_cases(build_juz30_corpus(SEED, ayahs=AYAHS))
        self.assertEqual([c.id for c in CASES], [c.id for c in again])

    def test_it_is_written_in_the_same_format_the_benchmark_reads(self):
        """One writer for both corpora, so the two manifests cannot drift
        into two shapes."""
        manifest = build_juz30_manifest(SEED)
        for key in ("version", "seed", "run_gap_ms", "source", "reciters",
                    "strata", "cases"):
            self.assertIn(key, manifest)

    def test_no_case_claims_a_deliberate_mistake(self):
        """These are correct recitations, so this corpus moves coverage and
        false alarms and nothing else. DELIBERATE MISTAKES still comes only
        from a human reciting one wrong on purpose."""
        for entry in build_juz30_manifest(SEED)["cases"]:
            self.assertNotIn("mistakes", entry)

    def test_the_committed_manifest_is_the_one_the_committed_seed_produces(self):
        """The manifest is the corpus. If it drifts from the seed, every
        number measured against it is measured against something nobody can
        reproduce."""
        import json
        if not JUZ30_MANIFEST_PATH.exists():
            self.skipTest("juz 30 corpus not generated yet")
        committed = json.loads(JUZ30_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(committed, build_juz30_manifest(SEED))

    def test_the_audio_stays_out_of_git(self):
        """Somebody else's recordings. The definition is committed; the
        audio is a cache."""
        rules = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("tests/recitations/*", rules)
        self.assertIn("!tests/recitations/juz30.json", rules)


if __name__ == "__main__":
    unittest.main()
