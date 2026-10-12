# Delegating classification

Judgement per track does not scale in one head: 1,551 tracks × (subgenre, niche) is 3,000+ decisions. The pattern that worked: fix the vocabulary centrally, slice the library by family, and give each subagent one coherent menu to choose from.

## Slicing

`rbtag.py slices` builds the work files: group tracks by tier-1 family, one slice per family, splitting families above ~130 tracks. One family per slice is the point — the subagent receives exactly the tier-2 menu and tier-3 candidate list that may apply to its tracks, so vocabulary drift has nowhere to start.

```json
{"slice": "slice_00",
 "family": "Techno",
 "tier1_vocabulary": ["Techno", "House", "..."],
 "tier2_menu": {"Raw / Deep / Hypnotic": "loopy, deep, hypnotic club techno (Hypnus, WSNWG)",
                "...": "..."},
 "tier3_menu": ["Hypnotic Tool", "Dubwise / Deepchord", "..."],
 "tracks": [{"id": "...", "title": "...", "artist": "...", "album": "...", "label": "...",
             "year": 2017, "bpm": 128.0, "genre": "Techno", "orig": "Techno",
             "path": "/Users/.../file.aiff", "note": "prior research evidence, <=300 chars"}]}
```

`orig` is the track's original file-tag genre (recover it from earlier audit CSVs before overwriting anything) and `note` is whatever earlier research rounds concluded about that release — label blurbs, Bandcamp tags, Discogs styles. Both are the strongest cheap evidence a classifier gets.

## Task template

Shared context (rules that apply to every slice):

> Build the tier-2/tier-3 half of a three-tier genre typology.
> - **tier1** = the app's genre field, one of the listed families.
> - **tier2** = My Tag column `Subgenre`, **exactly one**, verbatim from your slice's `tier2_menu`.
> - **tier3** = My Tag column `Niche`, **zero or one**, from `tier3_menu`; target ~60-70% coverage, and a wrong niche is worse than none.
> - Decide from the evidence in each track record. Same album ⇒ same verdict (note genuine outliers). Tier 1 defaults to your family; change it only when the track clearly belongs elsewhere, with the reason.
> - At most 8 web searches for the whole slice, prefer reading the artist's Bandcamp or label page directly, never retry a failing search. Undecidable after that ⇒ best menu value, `confidence: low`.
> - If nothing in the menu fits a niche, use `"tier3": "PROPOSE: <Name>"`, 2-4 Title-Case words, sparingly — proposals are merged centrally.
> - Sanity check: if more than half the slice lands in one tier2, re-examine; that usually means the evidence was ignored.
> - Write `/…/out/<slice>.json` and reply with one line: slice, tier3 set/null counts, proposals, tier1 moves.

Per slice: read the slice file, tag every track, write the output file. Repeat the acceptance in the task: all input ids exactly once, every tier-2 verbatim from the menu, one-line summary at the end.

## Verdicts

```json
{"id": "...", "tier1": "Techno", "tier2": "Raw / Deep / Hypnotic", "tier3": "Hypnotic Tool",
 "confidence": "high|medium|low", "why": "<=80 chars citing the evidence"}
```

Confidence is meaning, not decoration: `high` when the release page, label or the artist states the genre; `medium` for strong indirect evidence (label roster, artist bio, platform genre); `low` for plausible-from-context only. Report the low list to the human — it is the honest boundary of the run, and it is where their taste should override the machine.

## What the applier does to those verdicts

- Validates against the **global** vocabulary rather than the slice menu: a track may legitimately move family, and then it carries the target family's wording. A track that *kept* its family but used another family's tier-2 wording is reported as a warning.
- Strips `PROPOSE: `, normalises casing, merges proposals that duplicate an existing name, and expects you to review near-duplicates by hand (`Rai Club` → `Raï`, `Arabic Indie` → `Levantine Indie`, `Oriental Funk` → `Middle Eastern Funk`).
- Settles disagreement within one release by album majority (≥60% for tier-2 and tier-3 per artist+album).
- Refuses to continue on: duplicate ids across slices, unknown ids, tier-2 outside the vocabulary, tier-3 equal to a tier-2 name.

## Scale

19 slices of ~100 tracks covered 1,551 tracks in about 5-9 minutes per agent, run concurrently. The work is judgement on supplied evidence, not research — most slices used 0-4 searches, and the ones that burned a full budget were chasing obscure artists whose releases are absent from every database.
