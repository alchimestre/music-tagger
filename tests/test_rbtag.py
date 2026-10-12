"""Unit tests for the parts of rbtag that decide what reaches the database.

No library or pyrekordbox needed: these cover id generation, the smart-playlist id encoding,
vocabulary validation and the normalisation the applier applies to classifier output.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/music-tagger/scripts"))
import rbtag  # noqa: E402


TAXONOMY = {
    "groups": [
        {"tier1": "Techno", "from": ["Techno", "Hard Techno"],
         "tier2": {"Raw / Deep / Hypnotic": "hypnotic", "Hard Techno": "hard"},
         "tier3": ["Hypnotic Tool", "Schranz / Hard"]},
        {"tier1": "World", "from": ["World"],
         "tier2": {"Maghreb & Mashriq": "north africa"},
         "tier3": ["Raï"]},
    ]
}


def write_taxonomy() -> str:
    path = Path(tempfile.mkdtemp()) / "taxonomy.json"
    path.write_text(json.dumps(TAXONOMY))
    return str(path)


def track(cid, **kw):
    base = dict(id=cid, title="T", artist="A", album="", label="", year=2020, bpm=128.0,
                genre="Techno", orig="Techno", path="/x", note="")
    base.update(kw)
    return base


def verdict(tier1="Techno", tier2="Raw / Deep / Hypnotic", tier3=None, **kw):
    return dict(tier1=tier1, tier2=tier2, tier3=tier3, confidence="high", why="evidence", **kw)


class TestIdsAndEncoding(unittest.TestCase):
    def test_new_id_is_numeric_uint32_range_and_unique(self):
        used = set()
        ids = [rbtag.new_id(used) for _ in range(200)]
        self.assertEqual(len(set(ids)), 200)
        for i in ids:
            self.assertTrue(i.isdigit())
            self.assertLessEqual(int(i), 4_200_000_000)

    def test_new_id_avoids_existing(self):
        used = {str(n) for n in range(100_000_000, 100_000_050)}
        for _ in range(20):
            self.assertNotIn(rbtag.new_id(used), range(100_000_000, 100_000_050))

    def test_bitshift_round_trip(self):
        # rekordbox stores unsigned tag/playlist ids as signed 32-bit values in smart playlists
        for value in (3311044921, 1601472755, 1733455448):
            self.assertEqual(rbtag.right_bitshift(rbtag.left_bitshift(value)), value)
        self.assertEqual(rbtag.left_bitshift(3311044921), -983922375)

    def test_escape_ampersand(self):
        self.assertEqual(rbtag.escape("Trance & Psy"), "Trance &amp; Psy")


class TestTaxonomy(unittest.TestCase):
    def test_loads_and_indexes(self):
        tax = rbtag.load_taxonomy(write_taxonomy())
        self.assertEqual(tax["from"]["Hard Techno"], "Techno")
        self.assertEqual(tax["tier1"], ["Techno", "World"])
        self.assertIn("Maghreb & Mashriq", tax["tier2_all"])
        self.assertIn("Raï", tax["tier3_all"])

    def test_rejects_genre_mapped_twice(self):
        bad = {"groups": [dict(TAXONOMY["groups"][0]), {"tier1": "House", "from": ["Techno"],
                                                        "tier2": {"Tech House": ""}, "tier3": []}]}
        path = Path(tempfile.mkdtemp()) / "t.json"
        path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            rbtag.load_taxonomy(str(path))

    def test_rejects_tier3_repeating_tier2(self):
        bad = {"groups": [{"tier1": "Techno", "from": ["Techno"],
                           "tier2": {"Dub Techno": "dub"}, "tier3": ["Dub Techno"]}]}
        path = Path(tempfile.mkdtemp()) / "t.json"
        path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            rbtag.load_taxonomy(str(path))

    def test_family_name_is_its_own_source(self):
        # a library that already carries the family names must still map, so re-runs work
        tax = rbtag.load_taxonomy(write_taxonomy())
        self.assertEqual(tax["from"]["Techno"], "Techno")
        self.assertEqual(tax["from"]["World"], "World")

    def test_rejects_family_claimed_by_another_group(self):
        bad = {"groups": [{"tier1": "Techno", "from": ["Techno", "World"],
                           "tier2": {"Dub Techno": "dub"}, "tier3": []},
                          {"tier1": "World", "from": ["World"],
                           "tier2": {"Maghreb": "x"}, "tier3": []}]}
        path = Path(tempfile.mkdtemp()) / "t.json"
        path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            rbtag.load_taxonomy(str(path))


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.tax = rbtag.load_taxonomy(write_taxonomy())

    def test_accepts_a_clean_verdict(self):
        self.assertEqual(rbtag.validate_verdict("1", verdict(), "Techno", self.tax), "")

    def test_rejects_unknown_tier2(self):
        bad = verdict(tier2="Neurofunk")
        self.assertIn("vocabulary", rbtag.validate_verdict("1", bad, "Techno", self.tax))

    def test_rejects_tier3_that_is_a_tier2_name(self):
        bad = verdict(tier3="Hard Techno")
        self.assertIn("repeats a tier2", rbtag.validate_verdict("1", bad, "Techno", self.tax))

    def test_allows_a_proposed_niche(self):
        self.assertEqual(rbtag.validate_verdict("1", verdict(tier3="PROPOSE: Tekno / Free Party"),
                                                "Techno", self.tax), "")


class TestNormalisation(unittest.TestCase):
    def setUp(self):
        self.tax = rbtag.load_taxonomy(write_taxonomy())

    def test_reports_missing_and_unknown_tracks(self):
        tracks = {"1": track("1"), "2": track("2")}
        out, problems, _, _ = rbtag.normalise_verdicts({"1": verdict(), "9": verdict()}, tracks, self.tax)
        self.assertEqual(set(out), {"1"})
        self.assertTrue(any("unknown track id" in p for p in problems))
        self.assertTrue(any("without a verdict" in p for p in problems))

    def test_proposal_is_cleaned_and_merged(self):
        tracks = {"1": track("1", genre="World"), "2": track("2", genre="World")}
        raw = {"1": verdict("World", "Maghreb & Mashriq", "PROPOSE: Rai Club"),
               "2": verdict("World", "Maghreb & Mashriq", "PROPOSE: Rai Club")}
        out, problems, _, _ = rbtag.normalise_verdicts(
            raw, tracks, self.tax, merges={"Rai Club": "Raï"})
        self.assertEqual([], problems)
        self.assertEqual({out["1"]["tier3"], out["2"]["tier3"]}, {"Raï"})

    def test_album_majority_settles_a_release(self):
        tracks = {"1": track("1", album="LP"), "2": track("2", album="LP"),
                  "3": track("3", album="LP"), "4": track("4", album="LP")}
        raw = {"1": verdict(tier2="Hard Techno"), "2": verdict(tier2="Hard Techno"),
               "3": verdict(tier2="Hard Techno"), "4": verdict(tier2="Raw / Deep / Hypnotic")}
        out, _, _, fixed = rbtag.normalise_verdicts(raw, tracks, self.tax)
        self.assertEqual(out["4"]["tier2"], "Hard Techno")
        self.assertGreaterEqual(fixed, 1)

    def test_moving_family_keeps_the_target_wording_without_warning(self):
        tracks = {"1": track("1", genre="World")}
        raw = {"1": verdict("Techno", "Raw / Deep / Hypnotic")}
        out, problems, warnings, _ = rbtag.normalise_verdicts(raw, tracks, self.tax)
        self.assertEqual([], problems)
        self.assertEqual([], warnings)
        self.assertTrue(out["1"]["moved"])
        self.assertEqual(out["1"]["was"], "World")

    def test_staying_in_family_with_foreign_wording_warns(self):
        tracks = {"1": track("1", genre="Techno")}
        raw = {"1": verdict("Techno", "Maghreb & Mashriq")}
        _, problems, warnings, _ = rbtag.normalise_verdicts(raw, tracks, self.tax)
        self.assertEqual([], problems)
        self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()
