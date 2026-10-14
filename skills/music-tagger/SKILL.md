---
name: music-tagger
description: Tagging a rekordbox library — build a tiered genre typology, classify tracks from evidence, write genres / My Tags / playlists into master.db, and diagnose writes rekordbox ignores (blank genre column, empty playlist, invisible tags).
---

# music-tagger

Turn a library whose genre field is flat, half-empty or full of catch-alls into a typology:

| Tier | Lives in | Cardinality | Example |
|---|---|---|---|
| 1 — general genre | rekordbox `Genre` field | one per track | `Techno`, `World`, `Folk & Roots` |
| 2 — subgenre | My Tag column `Subgenre` | one per track | `Raw / Deep / Hypnotic`, `Maghreb & Mashriq` |
| 3 — niche | My Tag column `Niche` | zero or one | `Hypnotic Tool`, `Raï` |

Two or three tags per track, every one of them filterable. The design goal is to kill blobs: a tier-1 family that swallows distinct musics. `World` at 251 tracks is fine as tier 1 *if* tier 2 and tier 3 split it into `Maghreb & Mashriq` / `Raï`, `Sub-Saharan Africa` / `Tuareg Desert Blues`, and so on. A blob is a family that is the *only* level.

Work the five steps in order. `scripts/rbtag.py --help` lists the commands; `taxonomy.json` ships a vocabulary built against a real 1,551-track library as the starting point, not a default to keep.

## 1. Survey

`rbtag.py survey` (read-only) prints: track/genre/label/artist counts, per-genre profiles with BPM range and top labels, the playlist inventory with attributes, the existing My Tag tree, and two audits — primary-key formats per table, and dangling references. `rbtag.py dump` writes the per-track evidence corpus the later steps consume.

Completion: the report is printed and the two audits are clean, or their defects are understood. No writes happen before a backup exists.

## 2. Design the typology

Adapt `taxonomy.json` to this library. Tier 1 is the general layer, so a value that is really a subgenre (`Techno (Raw / Deep / Hypnotic)`, `Minimal / Deep Tech`) belongs under its family, not beside it. Give each family a tier-2 menu whose entries are the distinctions a DJ would actually filter on, and a tier-3 candidate list of scenes, regions, traditions and production flavours that sit *below* those.

Rules that the applier enforces:

- every existing genre value maps to exactly one tier-1 family (the `from` lists);
- the two columns are flat, so a name means one thing across the whole column; the same name may sit in two families' menus, and a tier-3 name never repeats a tier-2 name inside one family;
- names stay in the library's language and casing style, 2-4 words, Title Case;
- a tier-2 entry may name its exemplar artists or labels in its description: that steers the classifiers far better than an abstract definition.

Completion: the coverage assertion passes (the survey's genre list is fully mapped), there are no name collisions, and you can state each family's blob risk in one line.

## 3. Classify

One verdict per track: `tier1`, `tier2`, `tier3` (nullable), `confidence`, `why` (≤80 chars, the reason, citing the evidence).

Evidence per track: artist, album, label, year, BPM, the *original* file tag string, path, plus any research notes from earlier rounds. Where the evidence decides mechanically, decide mechanically. Where it needs judgement, slice by family and delegate — one family per slice keeps the menu small and the answers coherent; `reference/delegation.md` holds the slice format, the subagent task template and the verdict schema.

Evidence sources that answer these agents: MusicBrainz `ws/2`, the iTunes and Deezer search APIs, individual `*.bandcamp.com` subdomains, and Beatport release pages read through the `r.jina.ai` text proxy. Discogs, Bandcamp *search* and Resident Advisor answer 403/Cloudflare — pointing agents at them wastes a budget. Assign genre from the release, never from tempo alone.

Vocabulary discipline: tier 2 comes verbatim from the family menu; tier 3 only where it genuinely narrows the track (target 60-70% coverage); `PROPOSE: <Name>` when nothing fits, then merge near-duplicates centrally. Same release ⇒ same verdict; let album majority (≥60%) settle disagreement.

Completion: every live track id has exactly one verdict, all names validate against the global vocabulary, the id set is identical to the library's, and duplicate/unknown ids are zero. Read the `confidence: low` list out — it is the honest edge of the run.

## 4. Apply

`rbtag.py apply --taxonomy taxonomy.json --verdicts out/` — dry run by default; `--apply` writes.

The script stops nothing: quit rekordbox first and say so, because the app rewrites `master.db` from memory on exit. It then takes the backup, rebuilds the two My Tag columns, inserts the tag links, remaps the `Genre` field to the tier-1 families, drops genre rows left unreferenced, patches smart playlists that filter a renamed genre name, and writes the per-track audit CSV next to the backups.

Completion: the post-write checks report the expected counts, tags-per-track 2-3, one tag per column, and zero dangling references.

## 5. Verify

Restart the app, then `rbtag.py verify`: primary-key formats, dangling references, tracks resolved to a genre, tags per column, per-family counts, playlist integrity, and each smart playlist's condition evaluating to a sane count. `rbtag.py smartlist --playlist <name>` re-checks one condition set end to end.

The agent cannot photograph the app: DB-level checks plus a human glance at the panel is the full verification. Ask for the specific thing to look at — the genre column on a named track, the two tag columns, one playlist's track count — and say plainly which parts only the human can confirm.

## Reference

- Primary keys: `djmdContent`, `djmdArtist`, `djmdAlbum`, `djmdLabel`, `djmdGenre`, `djmdPlaylist`, `djmdMyTag` are entities and take numeric keys (random uint32, like rekordbox's own). `djmdSongPlaylist` and `djmdSongMyTag` are link tables and take UUID strings. A UUID-keyed entity row reads back perfectly through SQL while the app shows a blank field for it — blank genres on a subset of tracks, tags that never appear — so write numeric keys from the start, and reach for `rbtag.py rekey` when one slips through.
- `reference/database.md` — schema notes, insert templates, write order, orphan cleanup, backup layout, guarded fields.
- `reference/smartlists.md` — smart playlist XML, the operator/property vocabulary, how tag conditions encode, escaping.
- `reference/delegation.md` — slice and verdict schemas, subagent task template, normalisation rules.
- `taxonomy.json` — the shipped vocabulary: 15 families, 106 subgenre candidates, 133 niche candidates.
