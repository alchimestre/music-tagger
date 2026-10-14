# AGENTS.md

This repository's tool rewrites a live DJ library in place.

## Before touching a library

1. Read `skills/music-tagger/SKILL.md` — it is the procedure, and the reference files under `skills/music-tagger/reference/` answer most questions from there.
2. Quit rekordbox before any write and tell the human you did. The app holds the library in memory and rewrites `master.db` on exit; a write against a running app is invisible at best and lost at worst.
3. Dry run first: `rbtag.py apply`, `rekey` and `smartlist` are read-only until `--apply`, and they print the counts worth checking.
4. Backups are per-write directories (`backup_pre_<change>_<timestamp>/master.db`). Check one exists before reporting a change as done.

## Repo conventions

- `skills/music-tagger/taxonomy.json` is data, not code: vocabulary changes are edits to that file, and the script validates the shape (every genre mapped once, no family mapped into another, no tier-3 name repeating a tier-2 name inside a family).
- Entity tables take numeric primary keys, link tables take UUIDs; `reference/database.md` explains why the app renders UUID-keyed entity rows as blank fields, and `rbtag.py rekey` repairs them.
- Keep `reference/` and `scripts/` in sync: a change to how the applier behaves belongs in `reference/database.md` in the same change, and the smart playlist encoding in `reference/smartlists.md` must match `rbtag.py`.
- Verification is DB-level plus a human glance — there is no way to screenshot the app. Report which specific thing the human should look at, and never claim a visible result you did not see confirmed.
- `python3 -m unittest discover -s tests` must pass, and it runs without a library: keep the pure logic (ids, encoding, validation, normalisation) importable without pyrekordbox.
