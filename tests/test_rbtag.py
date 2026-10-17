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


class TestLastfmTags(unittest.TestCase):
    def test_parse_top_tags_sorts_and_caps(self):
        payload = {"toptags": {"tag": [
            {"name": "techno", "count": "40"},
            {"name": "Hardgroove", "count": "90"},
            {"name": "acid", "count": "10"},
        ]}}
        tags = rbtag.parse_top_tags(payload, limit=2)
        self.assertEqual([t["name"] for t in tags], ["Hardgroove", "techno"])
        self.assertEqual(tags[0]["count"], 90)

    def test_parse_top_tags_accepts_a_single_tag_object(self):
        payload = {"toptags": {"tag": {"name": "ambient", "count": "100"}}}
        self.assertEqual(rbtag.parse_top_tags(payload), [{"name": "ambient", "count": 100}])

    def test_parse_top_tags_unknown_artist_is_empty(self):
        self.assertEqual(rbtag.parse_top_tags({"error": 6, "message": "not found"}), [])

    def test_attach_tags_matches_artist_case_and_spacing(self):
        tracks = {
            "1": {"artist": "  Bonobo "},
            "2": {"artist": "Nobody"},
        }
        cache = {"artists": {
            rbtag.lastfm_key("Bonobo"): {
                "artist": "Bonobo", "tags": [{"name": "downtempo", "count": 100}],
            },
        }}
        tagged = rbtag.attach_tags(tracks, cache)
        self.assertEqual(tagged, 1)
        self.assertEqual(tracks["1"]["lastfm"]["tags"][0]["name"], "downtempo")
        self.assertEqual(tracks["2"]["lastfm"], {"artist": "", "tags": []})

    def test_cache_round_trip_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lastfm_cache.json"
            self.assertEqual(rbtag.load_cache(path), {"artists": {}, "tracks": {}})
            rbtag.save_cache(path, {"artists": {"a": {"artist": "A", "tags": []}}, "tracks": {}})
            self.assertIn("a", rbtag.load_cache(path)["artists"])
            path.write_text("{not json")
            self.assertEqual(rbtag.load_cache(path), {"artists": {}, "tracks": {}})

    def test_lastfm_key_collapses_whitespace(self):
        self.assertEqual(rbtag.lastfm_key("  Frank   & Tony "), "frank & tony")

    def test_dotenv_value_reads_quoted_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('# local\nOTHER=1\nLASTFM_API_KEY="abc123"\n')
            self.assertEqual(rbtag.dotenv_value(path, "LASTFM_API_KEY"), "abc123")
            self.assertIsNone(rbtag.dotenv_value(path, "MISSING"))
            self.assertIsNone(rbtag.dotenv_value(Path(tmp) / "absent", "LASTFM_API_KEY"))

    def test_track_key_normalises_both_sides(self):
        self.assertEqual(rbtag.track_key(" Massive  Attack ", " Teardrop "),
                         "massive attack\tteardrop")

    def test_attach_tags_adds_track_tags_when_requested(self):
        tracks = {
            "1": {"artist": "Massive Attack", "title": "Teardrop"},
            "2": {"artist": "Nobody", "title": "Nothing"},
        }
        cache = {
            "artists": {rbtag.lastfm_key("Massive Attack"): {
                "artist": "Massive Attack", "tags": [{"name": "trip-hop", "count": 100}]}},
            "tracks": {rbtag.track_key("Massive Attack", "Teardrop"): {
                "artist": "Massive Attack", "title": "Teardrop",
                "tags": [{"name": "chillout", "count": 70}]}},
        }
        rbtag.attach_tags(tracks, cache, with_track_tags=True)
        self.assertEqual(tracks["1"]["lastfm"]["track"]["tags"][0]["name"], "chillout")
        self.assertEqual(tracks["2"]["lastfm"]["track"]["tags"], [])

    def test_attach_tags_omits_track_tags_by_default(self):
        tracks = {"1": {"artist": "Massive Attack", "title": "Teardrop"}}
        cache = {"artists": {}, "tracks": {}}
        rbtag.attach_tags(tracks, cache)
        self.assertNotIn("track", tracks["1"]["lastfm"])

    def test_load_cache_accepts_artist_only_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lastfm_cache.json"
            path.write_text(json.dumps({"artists": {"a": {"artist": "A", "tags": []}}}))
            cache = rbtag.load_cache(path)
            self.assertIn("a", cache["artists"])
            self.assertEqual(cache["tracks"], {})

    def test_family_groups_keeps_unmapped_genres(self):
        tax = {"from": {"Techno": "Techno"}, "groups": [],
               "menu": {"Techno": {"tier2": {"A": "a"}, "tier3": ["B"]}}}
        tracks = {"1": {"genre": "Techno"}, "2": {"genre": "Junk"}}
        groups = rbtag.family_groups(tracks, tax)
        self.assertEqual(len(groups["Techno"]), 1)
        self.assertEqual(len(groups["Unmapped"]), 1)

    def test_unmapped_slice_menu_is_the_union(self):
        tax = {"from": {}, "menu": {}, "groups": [
            {"tier2": {"A": "a"}, "tier3": ["B"]},
            {"tier2": {"C": "c"}, "tier3": ["D"]},
        ]}
        menu, tier3 = rbtag.slice_menu(tax, rbtag.UNMAPPED)
        self.assertEqual(set(menu), {"A", "C"})
        self.assertEqual(set(tier3), {"B", "D"})
