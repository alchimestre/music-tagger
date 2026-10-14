# music-tagger

A skill plus a CLI that turn a DJ library's flat, half-empty or catch-all genre field into a tiered typology
inside rekordbox.

| Tier | Lives in | Per track | Examples |
|---|---|---|---|
| 1 — general genre | rekordbox `Genre` field | one | `Techno`, `World`, `Folk & Roots` |
| 2 — subgenre | My Tag column `Subgenre` | one | `Raw / Deep / Hypnotic`, `Maghreb & Mashriq` |
| 3 — niche | My Tag column `Niche` | zero or one | `Hypnotic Tool`, `Raï` |

Two or three tags per track, every level filterable, and no tier-1 family that is the only level a track has.

## Requirements

- macOS with rekordbox. Its library at `~/Library/Pioneer/rekordbox/master.db` is SQLCipher-encrypted, so
  `sqlite3` cannot read it; the CLI goes through `pyrekordbox`.
- Python 3.11+.

## Pipeline

```bash
R="uv run --with pyrekordbox --with sqlalchemy python skills/music-tagger/scripts/rbtag.py"

$R survey                                   # read-only: distributions, playlists, tag tree, audits
$R dump    --out work/tracks.json --notes ~/Library/Pioneer/rekordbox/genre_changes_*.csv
$R slices  --taxonomy skills/music-tagger/taxonomy.json --tracks work/tracks.json --outdir work/slices
#   classify each work/slices/slice_NN.json -> work/out/slice_NN.json (task template: reference/delegation.md)
$R apply   --taxonomy skills/music-tagger/taxonomy.json --tracks work/tracks.json --verdicts work/out
$R apply   ... --apply                       # after reviewing the dry run; quit rekordbox first
$R verify                                    # after restarting the app
$R rekey                                     # repair entity rows that ended up with UUID keys
$R smartlist --playlist PsyTrance --genre "Trance & Psy" --not-tag "Hard Trance" --apply
```

Every write path refuses to run while rekordbox is open, takes `backup_pre_<change>_<timestamp>/master.db` next
to the library, and writes a per-track audit CSV (tier1/tier2/tier3, confidence, reason, moved-from) beside it.

## Layout

```
skills/music-tagger/
  SKILL.md                 the procedure: survey -> design -> classify -> apply -> verify
  taxonomy.json            the vocabulary: 15 families, 106 subgenres, 133 niche candidates
  reference/database.md    schema notes, insert templates, write order, state checks
  reference/smartlists.md  smart playlist XML, myTag encoding, escaping, worked example
  reference/delegation.md  slice and verdict schemas, the classifier task template
  scripts/rbtag.py         the CLI
tests/test_rbtag.py        unit tests for id generation, encoding, validation, normalisation
```

`taxonomy.json` is a worked starting point, not a default to keep: it describes a 1,551-track collection of
French, Arabic, global electronic and roots music. Adapt it, or replace it.

## Using the skill

`skills/music-tagger/SKILL.md` is an agent skill: a `name`, a `description` carrying the trigger branches, and
the reference files behind it. Point a skills directory at `skills/music-tagger`, or symlink it:

```bash
ln -s "$PWD/skills/music-tagger" ~/.agents/skills/music-tagger
```

## Tests

```bash
python3 -m unittest discover -s tests
```

They run without a library.

## License

MIT — see [LICENSE](LICENSE).
