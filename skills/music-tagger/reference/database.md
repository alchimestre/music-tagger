# Database notes

## Where and how

- macOS library: `~/Library/Pioneer/rekordbox/master.db`; the app also keeps `master.backup.*.db` files and its own `backup/` folders.
- The file is SQLCipher-encrypted. `sqlite3 master.db` answers `file is not a database`; open it with pyrekordbox (`pyrekordbox.Rekordbox6Database()`), which resolves the key, and run SQL through `db.engine.connect()`.
- Reading while rekordbox runs is safe. **Writing requires the app to be quit**: it holds the library in memory and rewrites `master.db` on exit, and pyrekordbox's own `commit()` refuses while the process is alive. Quitting someone's running app is a visible side effect — say when you are about to do it.
- Units: `djmdContent.BPM` is stored as an integer ×100 (128 BPM = `12800`); `Length` is milliseconds.

## Primary keys

| Kind | Tables | Key |
|---|---|---|
| Entity | `djmdContent`, `djmdArtist`, `djmdAlbum`, `djmdLabel`, `djmdGenre`, `djmdPlaylist`, `djmdMyTag` | decimal string, random uint32 (rekordbox's own values run ~1e8-4.2e9) |
| Link | `djmdSongPlaylist`, `djmdSongMyTag` | UUID string |

The app resolves entity rows by numeric key. A UUID-keyed entity row is fully readable through SQL while the app renders the referring field blank (genre column empty on those tracks, tags absent from the panel, a smart playlist filtering that name matching nothing). Generate keys with `rbtag.new_id()`; repair existing ones with `rbtag.py rekey`.

## Row bookkeeping

Every `djmd*` row carries: `rb_data_status=0`, `rb_local_data_status=0`, `rb_local_deleted=0`, `rb_local_synced=0`, `usn=NULL`, `rb_local_usn=<monotonic int, take max(rb_local_usn)+1 and increment>`, `created_at`/`updated_at` as `YYYY-MM-DD HH:MM:SS.mmm +00:00`, `UUID=<uuid4 for entities; for link rows use a fresh uuid4 as the ID too>`.

## Attributes

- `djmdPlaylist.Attribute`: `0` playlist, `1` folder, `4` smart playlist.
- `djmdMyTag.Attribute`: `1` column (root columns have `ParentID='root'`), `0` tag. A My Tag column behaves as a single-select field: one tag per column per track.

## Write order for a typology

1. Quit the app. Copy `master.db` to `backup_pre_<change>_<timestamp>/master.db` beside the library.
2. Wipe any previous build of the columns you own — link rows first, then the tag rows, then the columns:

```sql
delete from djmdSongMyTag where MyTagID in
  (select ID from djmdMyTag where ParentID=:col or ID=:col);
delete from djmdMyTag where ParentID=:col;
delete from djmdMyTag where ID=:col;
```

3. Insert the columns (numeric key, `Attribute=1`, `ParentID='root'`, `Seq` past the factory columns), then their tags (`Attribute=0`, `ParentID=<column>`, `Seq` in menu order):

```sql
insert into djmdMyTag (ID,Seq,Name,Attribute,ParentID,UUID,rb_data_status,rb_local_data_status,
  rb_local_deleted,rb_local_synced,usn,rb_local_usn,created_at,updated_at)
values (:id,:seq,:name,:attr,:parent,:uuid,0,0,0,0,null,:usn,:now,:now)
```

4. Insert one `djmdSongMyTag` link per column per track (`MyTagID`, `ContentID`, `TrackNo=0`).
5. Remap the genre field: create any missing tier-1 `djmdGenre` row (numeric key), then
   `update djmdContent set GenreID=:new, updated_at=:now where GenreID=:old`.
6. Patch smart playlists that filter a renamed genre name — `SmartList` stores the name as text, and the replacement must be XML-escaped (`&` → `&amp;`). See `smartlists.md`.
7. Drop genre rows nothing references any more, *after* the remap, and only those:

```sql
select ID,Name from djmdGenre where ID not in
  (select distinct GenreID from djmdContent where GenreID is not null and rb_local_deleted=0)
```

8. Write the audit CSV (id, artist, title, album, tier1, tier2, tier3, confidence, why, moved-from) next to the backups, so a later disagreement can be traced to the evidence that produced it.

## State checks worth running before and after any write

```sql
-- entity rows that the app cannot resolve
select 'djmdGenre', count(*) from djmdGenre where ID not glob '[0-9]*'
union all select 'djmdMyTag', count(*) from djmdMyTag where ID not glob '[0-9]*';
-- dangling references
select count(*) from djmdContent c where c.rb_local_deleted=0
  and not exists (select 1 from djmdGenre g where g.ID=c.GenreID);
select count(*) from djmdSongMyTag s
  where not exists (select 1 from djmdMyTag m where m.ID=s.MyTagID);
-- more than one tag in a column for a track (must be zero)
select count(*) from (select s.ContentID, p.Name col, count(*) n from djmdSongMyTag s
  join djmdMyTag m on m.ID=s.MyTagID join djmdMyTag p on p.ID=m.ParentID
  where p.Name in ('Subgenre','Niche') group by s.ContentID, m.ParentID having n>1);
```

`rbtag.py survey` and `rbtag.py verify` run all of these and print them with the distributions around them.

## Other facts that save a round trip

- Playlist membership lives only in `djmdSongPlaylist` (no companion ordering table); `TrackNo` orders it.
- Smart playlists keep their rules in `djmdPlaylist.SmartList` and have zero `djmdSongPlaylist` rows.
- The My Tag panel reads `djmdMyTag`; a track's tags are the `djmdSongMyTag` links.
- `djmdContent.Title` exists, but artist/album/label names come from their own tables: join `djmdArtist`, `djmdAlbum`, `djmdLabel` on the respective ID columns.
