# Smart playlists

A smart playlist is a normal `djmdPlaylist` row with `Attribute=4` and its rules in the `SmartList` text column; it has no `djmdSongPlaylist` rows because the track list is computed. Editing `SmartList` changes the playlist.

## XML shape

```xml
<NODE Id="-983922375" LogicalOperator="1" AutomaticUpdate="0">
  <CONDITION PropertyName="genre" Operator="1" ValueUnit="" ValueLeft="Trance &amp; Psy" ValueRight=""/>
  <CONDITION PropertyName="myTag" Operator="9" ValueUnit="" ValueLeft="-3110985210" ValueRight=""/>
</NODE>
```

- `Id` is the playlist id as a signed 32-bit value: `left_bitshift(id) = id - 2**32`. For playlist `3311044921` that is `-983922375`.
- `LogicalOperator`: `1` all conditions, `2` any condition — one operator for the whole node, and nesting is not part of the format, so a filter that mixes AND with OR has to be re-expressed.
- `AutomaticUpdate` is preserved from the existing row.
- Values are XML-escaped. A raw `&` in `ValueLeft` makes the document unparseable and the playlist silently matches nothing — this is the failure that looks exactly like "the playlist is empty".

## Conditions

Operators: `1` equal, `2` not equal, `3` greater, `4` less, `5` in range, `6` in last, `7` not in last, `8` contains, `9` not contains, `10` starts with, `11` ends with.

Properties: `artist`, `album`, `albumArtist`, `originalArtist`, `bpm`, `grouping`, `comments`, `producer`, `stockDate`, `dateCreated`, `counter`, `fileName`, `genre`, `key`, `label`, `mixName`, `myTag`, `rating`, `dateReleased`, `remixedBy`, `duration`, `name`, `year`.

`myTag` accepts only `contains` / `not contains`, and its `ValueLeft` is the tag id as a signed 32-bit value, the same `left_bitshift` encoding as `Id`. `contains` means "this track carries this tag" — membership, not substring — so a tag named `Hard / Industrial Trance` is untouched by a `not contains` on `Hard Trance`.

## Build it through pyrekordbox

Constructing the XML by hand is how the escaping bug happens. Build it with the shipped handler, which escapes for you, then verify by parsing it back and evaluating it against the library:

```python
from pyrekordbox.db6.smartlist import SmartList, LogicalOperator, Property, Operator, left_bitshift

sl = SmartList(logical_operator=LogicalOperator.ALL)
sl.playlist_id = playlist_id                      # to_xml applies left_bitshift to Id
sl.add_condition(Property.GENRE, Operator.EQUAL, 'Trance & Psy')
sl.add_condition(Property.MYTAG, Operator.NOT_CONTAINS, str(left_bitshift(int(tag_id))))
xml = sl.to_xml()

back = SmartList(); back.parse(xml)               # round-trip check
rows = con.execute(select(DjmdContent.ID).where(back.filter_clause())).fetchall()
```

`filter_clause()` is pyrekordbox's reading of the same semantics the app uses, so the count it returns is the count the playlist should show. `rbtag.py smartlist --playlist <name> [--apply]` does this, printing the match count and a sample.

## Worked example — `PsyTrance`

The playlist filtered `genre = "Psy-Trance"`. The typology replaced that genre value with the family `Trance & Psy`, which also holds 7 neo-/hard-trance tracks, so the faithful translation is the family minus those two subgenres:

```
LogicalOperator 1 (ALL)
  genre equal "Trance & Psy"                      -> 86 tracks
  myTag not contains "Neo-Trance / Trance-Techno" -> drops 5
  myTag not contains "Hard Trance"                -> drops 2
                                                  = 79, the playlist's original count
```

An `any`-of-the-five-psy-subgenre-tags form gives 84: `Psybient / Psychill` is also a Downtempo subgenre, and 5 of those tracks carry it. Tag values are shared across families by design, so check what a tag actually selects before using it as a boundary.

## Renaming a genre

When a genre value is renamed, patch every smart playlist that filters the old name (`PropertyName="genre" ... ValueLeft="<old>"`) and write the *escaped* new name. `rbtag.py apply` does this during the genre remap; skipping it leaves the playlist pointing at a value that no longer exists, which reads as an empty playlist.
