# music-tagger

A skill plus a CLI for turning a DJ library's flat, half-empty or catch-all genre field into a **tiered typology** inside rekordbox:

- **tier 1** — the app's `Genre` field: ~15 general families (`Techno`, `World`, `Folk & Roots`).
- **tier 2** — My Tag column `Subgenre`: one tag per track (`Raw / Deep / Hypnotic`, `Maghreb & Mashriq`).
- **tier 3** — My Tag column `Niche`: zero or one per track (`Hypnotic Tool`, `Raï`).

Two or three tags per track, every level filterable, and no single family left as a blob.

## Layout

```
skills/music-tagger/
  SKILL.md                 the procedure: survey -> design -> classify -> apply -> verify
  taxonomy.json            shipped vocabulary (15 families, 106 subgenres, 133 niche candidates)
  reference/database.md    schema notes, insert templates, write order, state checks
  reference/smartlists.md  smart playlist XML, myTag encoding, escaping, worked example
  reference/delegation.md  slice/verdict schemas and the classifier task template
  scripts/rbtag.py         the CLI
tests/test_rbtag.py        unit tests for id generation, encoding, validation, normalisation
```

The shipped `taxonomy.json` is not a default to keep: it is the vocabulary designed against a real 1,551-track collection (French/Arabic/global electronic and roots), kept as a worked starting point.

## Requirements

- macOS with rekordbox (`~/Library/Pioneer/rekordbox/master.db`) — the library is SQLCipher-encrypted, so `sqlite3` cannot read it.
- Python 3.11+, `pyrekordbox`, `sqlalchemy`:

```bash
uv run --with pyrekordbox --with sqlalchemy python skills/music-tagger/scripts/rbtag.py survey
```

## Pipeline

```bash
R="uv run --with pyrekordbox --with sqlalchemy python skills/music-tagger/scripts/rbtag.py"

$R survey                                   # read-only: distributions, playlists, tag tree, audits
$R dump    --out work/tracks.json --notes ~/Library/Pioneer/rekordbox/genre_changes_*.csv
$R slices  --taxonomy skills/music-tagger/taxonomy.json --tracks work/tracks.json --outdir work/slices
#   classify each work/slices/slice_NN.json -> work/out/slice_NN.json (task template: reference/delegation.md)
$R apply   --taxonomy skills/music-tagger/taxonomy.json --tracks work/tracks.json --verdicts work/out
$R apply   ... --apply                       # after reviewing the dry run; quits nothing, so quit rekordbox first
$R verify                                    # after restarting the app
$R rekey                                     # repair entity rows that ended up with UUID keys
$R smartlist --playlist PsyTrance --genre "Trance & Psy" --not-tag "Hard Trance" --apply
```

Every write path refuses to run while rekordbox is open, takes `backup_pre_<change>_<timestamp>/master.db` next to the library, and writes a per-track audit CSV (tier1/tier2/tier3, confidence, reason, moved-from) beside it.

## Using the skill

The skill lives in `skills/music-tagger/` and is discovered from this repository the same way any project skill is; on another machine, symlink it into the global directory:

```bash
ln -s "$PWD/skills/music-tagger" ~/.agents/skills/music-tagger
```

Agent asks that should reach it: "tag my rekordbox library", "my genres are a mess / full of World", "add a genre hierarchy", "why is this playlist empty after you tagged things".

## Tests

```bash
python3 -m unittest discover -s tests
```

They cover what decides the write: numeric id generation, the signed-32-bit encoding smart playlists use for ids, taxonomy validation (double-mapped genres, tier-3 names shadowing tier-2, family names mapping to themselves so re-runs work), verdict validation, and the normalisation the applier applies (album majority, proposal merging, family moves). No library needed.
