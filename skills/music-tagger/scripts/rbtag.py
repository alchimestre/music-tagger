#!/usr/bin/env python3
"""rbtag - build and apply a tiered genre typology in a rekordbox library.

    rbtag.py survey
    rbtag.py dump      --out tracks.json [--notes audit.csv ...]
    rbtag.py slices    --taxonomy taxonomy.json --tracks tracks.json --outdir slices [--max-per-slice 130]
    rbtag.py apply     --taxonomy taxonomy.json --verdicts out [--audit-dir DIR] [--apply]
    rbtag.py verify
    rbtag.py rekey     [--tables djmdGenre,djmdMyTag] [--apply]
    rbtag.py smartlist --playlist NAME [--genre G] [--tag T] [--not-tag T] [--any] [--apply]

Reading works while the app runs. Every write needs the app quit and takes a backup of
master.db first. Mutating commands are dry runs until --apply.

Tiers: 1 = the app's Genre field (general family), 2 = My Tag column "Subgenre" (one per
track), 3 = My Tag column "Niche" (zero or one). See the skill for the full procedure.
"""
import argparse
import csv
import glob
import json
import random
import shutil
import subprocess
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

LIBDIR = Path.home() / "Library/Pioneer/rekordbox"
COLUMNS = ("Subgenre", "Niche")
UTF = "%Y-%m-%d %H:%M:%S.%f"


# --------------------------------------------------------------------------- pure helpers

def stamp() -> str:
    return datetime.now(timezone.utc).strftime(UTF)[:-3] + " +00:00"


def new_id(used: set) -> str:
    """A numeric primary key in the range rekordbox itself generates."""
    while True:
        v = str(random.randint(100_000_000, 4_200_000_000))
        if v not in used:
            used.add(v)
            return v


def left_bitshift(x: int, nbit: int = 32) -> int:
    """Unsigned id -> the signed form rekordbox stores in smart playlist XML."""
    return int(x - 2 ** nbit)


def right_bitshift(x: int, nbit: int = 32) -> int:
    return int(x + 2 ** nbit)


def load_taxonomy(path) -> dict:
    """Normalise taxonomy.json into the lookups the pipeline uses."""
    with open(path) as fh:
        raw = json.load(fh)
    groups = raw["groups"]
    from_map, menu, t2_all, t3_all = {}, {}, set(), set()
    for g in groups:
        for src in g["from"]:
            if src in from_map:
                raise ValueError(f"genre {src!r} mapped twice")
            from_map[src] = g["tier1"]
    for g in groups:
        # a family name is its own source, so the taxonomy stays usable on a library that
        # already carries the family names from an earlier run
        tier1 = g["tier1"]
        if tier1 in from_map and from_map[tier1] != tier1:
            raise ValueError(f"{tier1!r} is a family and also mapped into {from_map[tier1]!r}")
        from_map[tier1] = tier1
        menu[g["tier1"]] = g
        t2_all |= set(g["tier2"])
        t3_all |= set(g["tier3"])
    for g in groups:
        clash = set(g["tier2"]) & set(g["tier3"])
        if clash:
            raise ValueError(f"{g['tier1']}: tier3 names repeat tier2 names: {sorted(clash)}")
    return {"groups": groups, "from": from_map, "menu": menu,
            "tier1": [g["tier1"] for g in groups], "tier2_all": t2_all, "tier3_all": t3_all}


def family_of(tracks: dict, tax: dict) -> dict:
    return {cid: tax["from"].get(t["genre"]) for cid, t in tracks.items()}


def validate_verdict(cid, r, track_family, tax) -> str:
    """Return a problem string, or '' when the verdict is usable."""
    if r["tier1"] not in tax["menu"]:
        return f"unknown tier1 {r['tier1']!r}"
    if r["tier2"] not in tax["tier2_all"]:
        return f"tier2 {r['tier2']!r} outside the vocabulary"
    t3 = (r.get("tier3") or "").strip() or None
    if t3:
        if t3.startswith("PROPOSE:"):
            t3 = t3[8:].strip()
        if t3 in tax["tier2_all"]:
            return f"tier3 {t3!r} repeats a tier2 name"
        if t3.lower() in {x.lower() for x in tax["tier3_all"]} and t3 not in tax["tier3_all"]:
            return f"tier3 {t3!r} case-duplicates a menu name"
    return ""


def normalise_verdicts(verdicts: dict, tracks: dict, tax: dict, merges: dict = None):
    """Validate verdicts, normalise proposals, settle releases by album majority."""
    merges = merges or {}
    problems, warnings = [], []
    out = {}
    fam = family_of(tracks, tax)
    for cid, r in verdicts.items():
        if cid not in tracks:
            problems.append(f"{cid}: unknown track id")
            continue
        problem = validate_verdict(cid, r, fam.get(cid), tax)
        if problem:
            problems.append(f"{cid}: {problem}")
            continue
        t3 = (r.get("tier3") or "").strip() or None
        if t3:
            t3 = t3[8:].strip() if t3.startswith("PROPOSE:") else t3
            t3 = " ".join(w.capitalize() if w.islower() else w for w in t3.split())
            t3 = merges.get(t3, t3)
        if r["tier1"] == fam.get(cid) and r["tier2"] not in tax["menu"][r["tier1"]]["tier2"]:
            warnings.append(f"{cid}: stayed in {r['tier1']} but tier2 {r['tier2']!r} is another family's")
        out[cid] = dict(tier1=r["tier1"], tier2=r["tier2"], tier3=t3,
                        confidence=r.get("confidence", ""), why=r.get("why", ""),
                        moved=r["tier1"] != fam.get(cid), was=fam.get(cid))
    missing = sorted(set(tracks) - set(out))
    if missing:
        problems.append(f"{len(missing)} tracks without a verdict: {missing[:10]}")

    fixed = 0
    releases = defaultdict(list)
    for cid, r in out.items():
        if tracks[cid].get("album"):
            releases[(tracks[cid]["artist"].lower(), tracks[cid]["album"].lower())].append(cid)
    for ids in releases.values():
        if len(ids) < 2:
            continue
        for key in ("tier2", "tier3"):
            top, n = Counter(out[i][key] for i in ids).most_common(1)[0]
            if top and n / len(ids) >= 0.6:
                for i in ids:
                    if out[i][key] != top:
                        out[i][key] = top
                        fixed += 1
    return out, problems, warnings, fixed


# --------------------------------------------------------------------------- db access

def connect(db_path=None):
    import pyrekordbox
    if db_path:
        return pyrekordbox.Rekordbox6Database(path=str(Path(db_path).parent))
    return pyrekordbox.Rekordbox6Database()


def db_file(db_path=None) -> Path:
    return Path(db_path) if db_path else LIBDIR / "master.db"


def require_stopped():
    if subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).stdout.strip():
        sys.exit("rekordbox is running: quit it first (it rewrites master.db from memory on exit)")


def backup(label: str, db_path=None) -> Path:
    src = db_file(db_path)
    dest = src.parent / f"backup_pre_{label}_{datetime.now():%Y%m%d_%H%M%S}"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest / "master.db")
    return dest


def used_ids(con) -> set:
    from sqlalchemy import text
    used = set()
    tables = con.execute(text(
        "select name from sqlite_master where type='table' and name like 'djmd%'")).fetchall()
    for (t,) in tables:
        try:
            used |= {str(i) for (i,) in con.execute(text(f'select ID from "{t}"'))}
        except Exception:
            pass          # djmdProperty and similar have no ID column
    return used


# --------------------------------------------------------------------------- commands

def cmd_survey(args):
    from sqlalchemy import text
    db = connect(args.db)
    with db.engine.connect() as con:
        live = con.execute(text("select count(*) from djmdContent where rb_local_deleted=0")).scalar()
        print(f"library: {db_file(args.db)}\nlive tracks: {live}\n")
        print("per genre (tracks | bpm p10-p90 | top labels | top artists):")
        rows = con.execute(text("""
            select coalesce(g.Name,'(none)') genre, c.BPM, coalesce(l.Name,''), coalesce(a.Name,'')
            from djmdContent c
            left join djmdGenre g on g.ID=c.GenreID left join djmdLabel l on l.ID=c.LabelID
            left join djmdArtist a on a.ID=c.ArtistID where c.rb_local_deleted=0""")).fetchall()
        by = defaultdict(list)
        for r in rows:
            by[r[0]].append(r)
        for genre, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
            bpm = sorted(x[1] for x in rs if x[1])
            lo, hi = (bpm[len(bpm) // 10], bpm[len(bpm) * 9 // 10]) if bpm else (0, 0)
            labs = Counter(x[2] for x in rs if x[2]).most_common(4)
            arts = Counter(x[3] for x in rs if x[3]).most_common(3)
            print(f"  {genre[:30]:<32}{len(rs):>5} | {lo / 100:.0f}-{hi / 100:.0f}"
                  f" | {', '.join(f'{k} ({v})' for k, v in labs)}"
                  f" | {', '.join(f'{k} ({v})' for k, v in arts)}")
        print("\nplaylists (attribute 0=playlist 1=folder 4=smart):")
        for r in con.execute(text("""select p.ID, p.Name, p.Attribute,
                (select count(*) from djmdSongPlaylist s where s.PlaylistID=p.ID and s.rb_local_deleted=0) n
                from djmdPlaylist p where p.rb_local_deleted=0 order by p.Attribute, n desc""")).fetchall():
            print(f"  {r[1][:38]:<40} attr={r[2]} tracks={r[3]}")
        print("\nMy Tag tree:")
        for r in con.execute(text("""select r.Name, r.Attribute,
                (select count(*) from djmdMyTag c where c.ParentID=r.ID) n
                from djmdMyTag r where r.ParentID='root' order by r.Seq""")).fetchall():
            print(f"  {r[0]:<20} attr={r[1]} children={r[2]}")
        print("\nprimary-key audit (non-numeric keys in entity tables):")
        for t in ("djmdContent", "djmdArtist", "djmdAlbum", "djmdLabel", "djmdGenre",
                  "djmdPlaylist", "djmdMyTag", "djmdSongPlaylist", "djmdSongMyTag"):
            n = con.execute(text(f"select count(*) from {t} where ID not glob '[0-9]*'")).scalar()
            total = con.execute(text(f"select count(*) from {t}")).scalar()
            kind = "link (uuid ok)" if t in ("djmdSongPlaylist", "djmdSongMyTag") else "entity (numeric)"
            flag = "" if n == 0 or t.startswith("djmdSong") else "   <-- the app cannot resolve these"
            print(f"  {t:<18}{kind:<18} non-numeric: {n}/{total}{flag}")
        print("\ndangling references:")
        print("  tracks with an unresolvable genre :",
              con.execute(text("""select count(*) from djmdContent c where c.rb_local_deleted=0
                  and not exists (select 1 from djmdGenre g where g.ID=c.GenreID)""")).scalar())
        print("  tag links with no tag row         :",
              con.execute(text("""select count(*) from djmdSongMyTag s
                  where not exists (select 1 from djmdMyTag m where m.ID=s.MyTagID)""")).scalar())


def cmd_dump(args):
    from sqlalchemy import text
    db = connect(args.db)
    with db.engine.connect() as con:
        rows = con.execute(text("""
            select c.ID, coalesce(g.Name,''), c.BPM, coalesce(a.Name,''), coalesce(al.Name,''),
                   coalesce(l.Name,''), c.ReleaseYear, coalesce(c.FolderPath,''), coalesce(c.Commnt,''),
                   c.DateCreated, coalesce(c.Title,'')
            from djmdContent c
            left join djmdGenre g on g.ID=c.GenreID left join djmdArtist a on a.ID=c.ArtistID
            left join djmdAlbum al on al.ID=c.AlbumID left join djmdLabel l on l.ID=c.LabelID
            where c.rb_local_deleted=0 order by cast(c.ID as integer)""")).fetchall()
    notes = defaultdict(dict)
    for path in args.notes:
        with open(path) as fh:
            for row in csv.DictReader(fh):
                if "id" not in row:
                    continue
                notes[str(row["id"])].update({k: v for k, v in row.items() if k != "id" and v})
    tracks = {}
    for r in rows:
        cid = str(r[0])
        note = notes.get(cid, {})
        tracks[cid] = dict(id=cid, genre=r[1], bpm=(r[2] or 0) / 100, artist=r[3], album=r[4],
                           label=r[5], year=r[6], path=r[7], comment=r[8],
                           created=(r[9] or "")[:10], title=r[10],
                           orig=note.get("old_genre", "") or note.get("genre_before", ""),
                           note=" | ".join(x for x in [note.get("reason", ""), note.get("evidence", "")] if x)[:300],
                           src=note.get("source", ""))
    json.dump(tracks, open(args.out, "w"), ensure_ascii=False)
    print(f"{len(tracks)} tracks -> {args.out} "
          f"(with original tag strings: {sum(1 for t in tracks.values() if t['orig'])}, "
          f"with research notes: {sum(1 for t in tracks.values() if t['note'])} )")


def cmd_slices(args):
    tax = load_taxonomy(args.taxonomy)
    with open(args.tracks) as fh:
        tracks = json.load(fh)
    by_family = defaultdict(list)
    for cid, t in tracks.items():
        fam = tax["from"].get(t["genre"])
        if not fam:
            sys.exit(f"track {cid}: genre {t['genre']!r} is not mapped to a family in the taxonomy")
        by_family[fam].append(t)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    made = 0
    for fam, ts in sorted(by_family.items()):
        ts.sort(key=lambda t: (t["artist"].lower(), t["album"], t["title"]))
        chunks = [ts[i:i + args.max_per_slice] for i in range(0, len(ts), args.max_per_slice)]
        for chunk in chunks:
            path = outdir / f"slice_{made:02d}.json"
            json.dump({"slice": path.stem, "family": fam, "tier1_vocabulary": tax["tier1"],
                       "tier2_menu": tax["menu"][fam]["tier2"],
                       "tier3_menu": tax["menu"][fam]["tier3"], "tracks": chunk},
                      open(path, "w"), ensure_ascii=False, indent=1)
            print(f"  {path.name}  {fam:<24}{len(chunk):>5} tracks")
            made += 1
    print(f"{made} slices, {sum(len(v) for v in by_family.values())} tracks -> {outdir}/")


def cmd_apply(args):
    from sqlalchemy import text
    tax = load_taxonomy(args.taxonomy)
    with open(args.tracks) as fh:
        tracks = json.load(fh)
    if tracks is None:
        sys.exit("--tracks <dump.json> is required (rbtag.py dump)")

    verdicts, unusable = {}, []
    for path in sorted(glob.glob(str(Path(args.verdicts) / "*.json"))):
        with open(path) as fh:
            for n, r in enumerate(json.load(fh)["results"]):
                cid = str(r.get("id") or "")
                if not cid:
                    unusable.append(f"{Path(path).name}: result {n} has no id")
                    continue
                verdicts[cid] = r
    if unusable:
        sys.exit("every result needs an id:\n  " + "\n  ".join(unusable[:10]))
    merges = dict(m.split("=", 1) for m in args.merge)
    results, problems, warnings, fixed = normalise_verdicts(verdicts, tracks, tax, merges)
    print(f"verdicts {len(results)}/{len(tracks)} | album normalised {fixed} | "
          f"tier1 moves {sum(1 for r in results.values() if r['moved'])}")
    if warnings:
        print(f"warnings ({len(warnings)}):")
        for w in warnings[:10]:
            print("   ", w)
    if problems:
        print(f"PROBLEMS ({len(problems)}):")
        for p in problems[:20]:
            print("   ", p)
        if args.apply:
            sys.exit("refusing to write with validation problems")
    t3 = Counter(r["tier3"] for r in results.values() if r["tier3"])
    print(f"tier2 values: {len({r['tier2'] for r in results.values()})} | "
          f"tier3 coverage: {sum(1 for r in results.values() if r['tier3'])} "
          f"({len({*t3})} distinct names)")
    if not args.apply:
        print("dry run; pass --apply to write")
        return

    require_stopped()
    print("backup ->", backup("typology", args.db))
    db = connect(args.db)
    now = stamp()
    with db.engine.connect() as con:
        used = used_ids(con)
        usn = con.execute(text("select max(rb_local_usn) from djmdContent")).scalar() + 1
        seq0 = con.execute(text("select max(Seq) from djmdMyTag where ParentID='root'")).scalar()

        for name in COLUMNS:                                   # wipe our own previous build
            cols = [r[0] for r in con.execute(text(
                "select ID from djmdMyTag where Name=:n and ParentID='root'"), {"n": name}).fetchall()]
            for col in cols:
                con.execute(text("delete from djmdSongMyTag where MyTagID in "
                                 "(select ID from djmdMyTag where ParentID=:c or ID=:c)"), {"c": col})
                con.execute(text("delete from djmdMyTag where ParentID=:c"), {"c": col})
                con.execute(text("delete from djmdMyTag where ID=:c"), {"c": col})
        con.commit()

        tag_id = {}
        for offset, col in enumerate(COLUMNS, start=1):
            col_id = new_id(used)
            con.execute(text("""insert into djmdMyTag (ID,Seq,Name,Attribute,ParentID,UUID,rb_data_status,
                rb_local_data_status,rb_local_deleted,rb_local_synced,usn,rb_local_usn,created_at,updated_at)
                values (:id,:seq,:name,1,'root',:uuid,0,0,0,0,null,:usn,:now,:now)"""),
                {"id": col_id, "seq": seq0 + offset, "name": col, "uuid": str(uuid.uuid4()),
                 "usn": usn, "now": now})
            usn += 1
            key = "tier2" if col == "Subgenre" else "tier3"
            wanted = {r[key] for r in results.values() if r[key]}
            order = [v for g in tax["groups"] for v in g[key] if v in wanted]
            values = order + sorted(wanted - set(order))
            for i, name in enumerate(values, start=1):
                tid = new_id(used)
                con.execute(text("""insert into djmdMyTag (ID,Seq,Name,Attribute,ParentID,UUID,rb_data_status,
                    rb_local_data_status,rb_local_deleted,rb_local_synced,usn,rb_local_usn,created_at,updated_at)
                    values (:id,:seq,:name,0,:parent,:uuid,0,0,0,0,null,:usn,:now,:now)"""),
                    {"id": tid, "seq": i, "name": name, "parent": col_id, "uuid": str(uuid.uuid4()),
                     "usn": usn, "now": now})
                usn += 1
                tag_id[(key, name)] = tid
        con.commit()

        for cid, r in results.items():
            for key in ("tier2", "tier3"):
                if r[key]:
                    con.execute(text("""insert into djmdSongMyTag (ID,MyTagID,ContentID,TrackNo,UUID,
                        rb_data_status,rb_local_data_status,rb_local_deleted,rb_local_synced,usn,
                        rb_local_usn,created_at,updated_at)
                        values (:id,:tag,:content,0,:id,0,0,0,0,null,:usn,:now,:now)"""),
                        {"id": str(uuid.uuid4()), "tag": tag_id[(key, r[key])], "content": cid,
                         "usn": usn, "now": now})
                    usn += 1
        con.commit()

        gid = {r[1]: str(r[0]) for r in con.execute(text("select ID, Name from djmdGenre")).fetchall()}
        for name in tax["tier1"]:
            if name not in gid:
                new = new_id(used)
                con.execute(text("""insert into djmdGenre (ID,Name,UUID,rb_data_status,rb_local_data_status,
                    rb_local_deleted,rb_local_synced,usn,rb_local_usn,created_at,updated_at)
                    values (:id,:name,:uuid,0,0,0,0,null,:usn,:now,:now)"""),
                    {"id": new, "name": name, "uuid": str(uuid.uuid4()), "usn": usn, "now": now})
                usn += 1
                gid[name] = new
        con.commit()
        for cid, r in results.items():
            con.execute(text("update djmdContent set GenreID=:g, updated_at=:now where ID=:id"),
                        {"g": gid[r["tier1"]], "id": cid, "now": now})
        con.commit()

        rename = {src: fam for src, fam in tax["from"].items() if src != fam}
        for pid, name, smart in con.execute(text(
                "select ID,Name,SmartList from djmdPlaylist where SmartList is not null")).fetchall():
            new_smart = smart
            for old, new in rename.items():
                new_smart = new_smart.replace(
                    f'PropertyName="genre" Operator="1" ValueUnit="" ValueLeft="{old}"',
                    f'PropertyName="genre" Operator="1" ValueUnit="" ValueLeft="{escape(new)}"')
            if new_smart != smart:
                con.execute(text("update djmdPlaylist set SmartList=:s, updated_at=:now where ID=:p"),
                            {"s": new_smart, "now": now, "p": pid})
                print(f"  smart playlist {name!r}: genre reference remapped")
        con.commit()

        orphans = con.execute(text("""select ID,Name from djmdGenre where ID not in
            (select distinct GenreID from djmdContent where GenreID is not null and rb_local_deleted=0)""")).fetchall()
        for oid, name in orphans:
            con.execute(text("delete from djmdGenre where ID=:g"), {"g": oid})
        con.commit()
        print(f"  dropped {len(orphans)} unreferenced genre rows")

        audit_dir = Path(args.audit_dir) if args.audit_dir else db_file(args.db).parent
        path = audit_dir / f"tag_typology_{datetime.now():%Y%m%d_%H%M%S}.csv"
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "artist", "title", "album", "tier1", "tier2", "tier3",
                        "confidence", "why", "tier1_moved_from"])
            for cid, r in sorted(results.items(), key=lambda kv: (kv[1]["tier1"], kv[1]["tier2"])):
                t = tracks[cid]
                w.writerow([cid, t["artist"], t["title"], t["album"], r["tier1"], r["tier2"],
                            r["tier3"] or "", r["confidence"], r["why"], r["was"] if r["moved"] else ""])
        print("audit ->", path)
    cmd_verify(argparse.Namespace(db=args.db))


def cmd_verify(args):
    from sqlalchemy import text, select
    db = connect(args.db)
    ok = True
    with db.engine.connect() as con:
        for label, q, expect in (
            ("unresolvable tracks (dangling genre)",
             """select count(*) from djmdContent c where c.rb_local_deleted=0
                and not exists (select 1 from djmdGenre g where g.ID=c.GenreID)""", 0),
            ("dangling tag links",
             """select count(*) from djmdSongMyTag s
                where not exists (select 1 from djmdMyTag m where m.ID=s.MyTagID)""", 0),
            ("tracks with 2+ tags in one column",
             """select count(*) from (select s.ContentID, m.ParentID, count(*) n from djmdSongMyTag s
                join djmdMyTag m on m.ID=s.MyTagID join djmdMyTag p on p.ID=m.ParentID
                where p.Name in ('Subgenre','Niche') group by s.ContentID, m.ParentID having n>1)""", 0),
            ("non-numeric keys in entity tables",
             """select (select count(*) from djmdGenre where ID not glob '[0-9]*')
                + (select count(*) from djmdMyTag where ID not glob '[0-9]*')
                + (select count(*) from djmdPlaylist where ID not glob '[0-9]*')""", 0),
        ):
            n = con.execute(text(q)).scalar()
            flag = "ok" if n == expect else "FAIL"
            ok &= n == expect
            print(f"  {flag:<5}{label:<38}{n}")
        live = con.execute(text("select count(*) from djmdContent where rb_local_deleted=0")).scalar()
        resolved = con.execute(text("""select count(*) from djmdContent c join djmdGenre g on g.ID=c.GenreID
            where c.rb_local_deleted=0""")).scalar()
        print(f"  {'ok' if live == resolved else 'FAIL':<5}tracks resolving to a genre{'':<12}{resolved}/{live}")
        print("\n  families:")
        for r in con.execute(text("""select g.Name, count(*) c from djmdContent c join djmdGenre g on g.ID=c.GenreID
                where c.rb_local_deleted=0 group by g.Name order by c desc""")).fetchall():
            print(f"    {r[1]:>5}  {r[0]}")
        print("  tag columns:")
        for r in con.execute(text("""select p.Name,
                (select count(*) from djmdMyTag m where m.ParentID=p.ID) tags,
                (select count(distinct s.ContentID) from djmdSongMyTag s join djmdMyTag m on m.ID=s.MyTagID
                 where m.ParentID=p.ID) tracks
                from djmdMyTag p where p.ParentID='root' and p.Name in ('Subgenre','Niche')""")).fetchall():
            print(f"    {r[0]:<10}{r[2]:>6} tracks   {r[1]:>4} tags")
        print("  playlists with track counts:",
              con.execute(text("""select count(*) from djmdPlaylist p where p.Attribute=0 and p.rb_local_deleted=0
                  and exists (select 1 from djmdSongPlaylist s where s.PlaylistID=p.ID)""")).scalar())
        for pid, name, smart in con.execute(text(
                "select ID,Name,SmartList from djmdPlaylist where SmartList is not null")).fetchall():
            try:
                from pyrekordbox.db6.smartlist import SmartList
                from pyrekordbox.db6.tables import DjmdContent
                sl = SmartList()
                sl.parse(smart)
                n = len(con.execute(select(DjmdContent.ID).where(sl.filter_clause())).fetchall())
                print(f"  smart playlist {name!r}: {n} tracks  "
                      f"[{', '.join(f'{c.property} {c.operator} {c.value_left}' for c in sl.conditions)}]")
            except Exception as exc:
                ok = False
                print(f"  FAIL smart playlist {name!r} does not evaluate: {exc}")
    print("\nverified" if ok else "\nproblems found")
    return 0 if ok else 1


def cmd_rekey(args):
    from sqlalchemy import text
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    db = connect(args.db)
    with db.engine.connect() as con:
        used = used_ids(con)
        for table in tables:
            bad = con.execute(text(f"select ID, Name from {table} where ID not glob '[0-9]*'")).fetchall()
            if not bad:
                print(f"{table}: nothing to do")
                continue
            print(f"{table}: {len(bad)} rows to re-key")
            if not args.apply:
                continue
            now, mapping = stamp(), {old: new_id(used) for old, _ in bad}
            for old, name in bad:
                con.execute(text(f"""insert into {table} (ID,Seq,Name,Attribute,ParentID,UUID,
                    rb_data_status,rb_local_data_status,rb_local_deleted,rb_local_synced,usn,
                    rb_local_usn,created_at,updated_at)
                    select :new,Seq,Name,Attribute,ParentID,:uuid,rb_data_status,rb_local_data_status,
                    rb_local_deleted,rb_local_synced,usn,rb_local_usn,:now,:now from {table} where ID=:old"""),
                    {"new": mapping[old], "old": old, "uuid": str(uuid.uuid4()), "now": now})
            con.commit()
            if table == "djmdMyTag":
                for old, new in mapping.items():
                    con.execute(text("update djmdMyTag set ParentID=:new where ParentID=:old"),
                                {"new": new, "old": old})
                    con.execute(text("update djmdSongMyTag set MyTagID=:new where MyTagID=:old"),
                                {"new": new, "old": old})
            elif table == "djmdGenre":
                for old, new in mapping.items():
                    con.execute(text("update djmdContent set GenreID=:new where GenreID=:old"),
                                {"new": new, "old": old})
            con.commit()
            for old in mapping:
                con.execute(text(f"delete from {table} where ID=:old"), {"old": old})
            con.commit()
            print(f"{table}: re-keyed {len(mapping)} rows")
    if not args.apply:
        print("dry run; pass --apply to write")


def cmd_smartlist(args):
    from sqlalchemy import text, select
    from pyrekordbox.db6.smartlist import SmartList, LogicalOperator, Property, Operator
    from pyrekordbox.db6.tables import DjmdContent
    db = connect(args.db)
    with db.engine.connect() as con:
        pid = con.execute(text("select ID from djmdPlaylist where Name=:n"), {"n": args.playlist}).scalar()
        if not pid:
            sys.exit(f"no playlist named {args.playlist!r}")
        tags = {r[1]: str(r[0]) for r in con.execute(text("select ID, Name from djmdMyTag where Attribute=0")).fetchall()}
        sl = SmartList(logical_operator=LogicalOperator.ANY if args.any else LogicalOperator.ALL)
        sl.playlist_id = pid
        if args.genre:
            sl.add_condition(Property.GENRE, Operator.EQUAL, args.genre)
        for name in args.tag:
            sl.add_condition(Property.MYTAG, Operator.CONTAINS, str(left_bitshift(int(tags[name]))))
        for name in args.not_tag:
            sl.add_condition(Property.MYTAG, Operator.NOT_CONTAINS, str(left_bitshift(int(tags[name]))))
        xml = sl.to_xml()
        back = SmartList()
        back.parse(xml)
        matched = {r[0] for r in con.execute(select(DjmdContent.ID).where(back.filter_clause())).fetchall()}
        print(f"playlist {args.playlist!r} (id {pid})\n{xml}\n")
        print("conditions:", [(c.property, c.operator, c.value_left) for c in back.conditions])
        print("matches now:", len(matched))
        print("was:", con.execute(text("select SmartList from djmdPlaylist where ID=:i"), {"i": pid}).scalar())
        if not args.apply:
            print("dry run; pass --apply to write")
            return
        require_stopped()
        print("backup ->", backup("smartlist", args.db))
        now = stamp()
        usn = con.execute(text("select max(rb_local_usn) from djmdContent")).scalar() + 1
        con.execute(text("update djmdPlaylist set SmartList=:s, updated_at=:now, rb_local_usn=:u where ID=:i"),
                    {"s": xml, "now": now, "u": usn, "i": pid})
        con.commit()
        print("written")


def escape(value: str) -> str:
    from xml.sax.saxutils import escape as _escape
    return _escape(value)


def main(argv=None):
    p = argparse.ArgumentParser(prog="rbtag", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="library directory or master.db path (default: the standard rekordbox location)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("survey", help="read-only library report and audits").set_defaults(func=cmd_survey)
    sub.add_parser("verify", help="check the state after a write").set_defaults(func=cmd_verify)

    d = sub.add_parser("dump", help="write the per-track evidence corpus")
    d.add_argument("--out", required=True)
    d.add_argument("--notes", nargs="*", default=[], help="audit CSVs carrying id + old_genre/reason/evidence")
    d.set_defaults(func=cmd_dump)

    s = sub.add_parser("slices", help="build per-family work files for classifiers")
    s.add_argument("--taxonomy", required=True)
    s.add_argument("--tracks", required=True)
    s.add_argument("--outdir", required=True)
    s.add_argument("--max-per-slice", type=int, default=130)
    s.set_defaults(func=cmd_slices)

    a = sub.add_parser("apply", help="validate verdicts and write the typology")
    a.add_argument("--taxonomy", required=True)
    a.add_argument("--tracks", required=True)
    a.add_argument("--verdicts", required=True, help="directory of classifier output files")
    a.add_argument("--audit-dir")
    a.add_argument("--merge", action="append", default=[], metavar="OLD=NEW",
                   help="merge a near-duplicate niche name (repeatable)")
    a.add_argument("--apply", action="store_true")
    a.set_defaults(func=cmd_apply)

    r = sub.add_parser("rekey", help="rewrite UUID-keyed entity rows with numeric keys")
    r.add_argument("--tables", default="djmdGenre,djmdMyTag")
    r.add_argument("--apply", action="store_true")
    r.set_defaults(func=cmd_rekey)

    m = sub.add_parser("smartlist", help="build or repair a smart playlist's conditions")
    m.add_argument("--playlist", required=True)
    m.add_argument("--genre")
    m.add_argument("--tag", action="append", default=[])
    m.add_argument("--not-tag", action="append", default=[], dest="not_tag")
    m.add_argument("--any", action="store_true", help="match any condition instead of all")
    m.add_argument("--apply", action="store_true")
    m.set_defaults(func=cmd_smartlist)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
