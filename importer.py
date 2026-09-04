"""
Work out what a hand history is before loading it, and then load it.

The site used to be decided by whoever ran the command -- `python acr.py`
meant these are ACR hands. That went wrong exactly once and expensively:
eight thousand hands were loaded, named, analysed and reported on as
CoinPoker, because a CoinPoker client happened to be installed on the
machine. The site was inferred from the computer instead of from the file,
and the file had said so in plain text from the first line.

So nothing here asks. Every file is sniffed, and a file that cannot be
identified is counted and skipped rather than guessed at.

    python importer.py --scan               where hand histories are
    python importer.py <folder-or-file>...  load them, whatever site they are
    python importer.py --merge other.db     take the hands from another database
    python importer.py --check              PASS or FAIL
"""

import os
import sqlite3
import sys
from pathlib import Path

import acr
import ignition

DB = Path(__file__).parent / "hands.db"

# How each site announces itself in the first line of a hand. These are the
# actual headers, not a guess about them: Ignition names itself, and the
# Winning Poker Network -- which is what ACR runs on -- writes a bare hand
# number followed by the game.
SIGNATURES = (
    ("ignition", lambda t: t.startswith("Ignition Hand #")),
    ("acr", lambda t: t.startswith("Hand #") and " - Holdem" in t[:80]),
)

# Where hand histories live when nobody has moved them. Checked in order and
# reported with what is actually in them, because a folder that exists and
# holds nothing is not a place to import from.
KNOWN_PLACES = (
    r"%USERPROFILE%\Ignition Casino Poker\Hand History",
    r"%USERPROFILE%\Bovada Poker\Hand History",
    r"%LOCALAPPDATA%\AmericasCardroom\handHistory",
    r"%LOCALAPPDATA%\BlackChipPoker\handHistory",
    r"%USERPROFILE%\Documents\AmericasCardroom",
    r"%USERPROFILE%\Downloads",
    r"%USERPROFILE%\Desktop",
)


def sniff(path):
    """Which site wrote this file, by reading it rather than by asking."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            # The header is at the top, but an export can begin with a blank
            # line or a stray byte-order mark, so a few lines are allowed.
            for _ in range(6):
                line = fh.readline()
                if not line:
                    break
                line = line.lstrip("﻿").strip()
                if not line:
                    continue
                for site, matches in SIGNATURES:
                    if matches(line):
                        return site
                return None
    except OSError:
        return None
    return None


def files_under(paths):
    """Every .txt beneath the given files and folders, without duplicates."""
    seen, out = set(), []
    for p in paths:
        p = Path(os.path.expandvars(str(p)))
        found = sorted(p.rglob("*.txt")) if p.is_dir() else [p]
        for f in found:
            key = str(f).lower()
            if key not in seen and f.is_file():
                seen.add(key)
                out.append(f)
    return out


def survey(paths):
    """What is in these places, by site, without loading anything."""
    counts = {"ignition": [], "acr": [], "unknown": []}
    for f in files_under(paths):
        counts[sniff(f) or "unknown"].append(f)
    return counts


def scan():
    """The places on this machine that actually hold hand histories."""
    found = []
    for raw in KNOWN_PLACES:
        place = Path(os.path.expandvars(raw))
        if not place.exists():
            continue
        got = survey([place])
        n = len(got["ignition"]) + len(got["acr"])
        if n:
            found.append({"path": place, "ignition": len(got["ignition"]),
                          "acr": len(got["acr"]),
                          "unknown": len(got["unknown"])})
    return found


def load(paths, db_path=DB, progress=None):
    """
    Load every hand history under these paths, each by its own site's parser.

    Files are grouped by site first so that a folder holding both -- which is
    what a Downloads folder is -- loads correctly rather than by whichever
    parser was asked for.
    """
    got = survey(paths)
    result = {"added": 0, "known": 0, "files": 0, "unknown": len(got["unknown"]),
              "by_site": {}}
    for site, module in (("ignition", ignition), ("acr", acr)):
        files = got[site]
        if not files:
            continue
        if progress:
            progress(f"{site}: {len(files)} files")
        n_files, added, skipped = module.build(None, db_path, files=files)
        result["by_site"][site] = added
        result["added"] += added
        result["known"] += skipped
        result["files"] += n_files
    return result


def merge(other, db_path=DB, progress=None):
    """
    Take the hands from another database of the same shape.

    Only hands this one has never seen are copied, keyed by the site's own
    hand id, so merging the same file twice adds nothing the second time.
    The derived tables are not copied -- they are rebuilt from the raw rows,
    because a `spots` row from another database was derived by whatever
    version of the derivation that machine was running.
    """
    other = Path(other)
    if not other.exists():
        raise SystemExit(f"no database at {other}")
    con = sqlite3.connect(db_path)
    acr.migrate(con)
    con.execute("ATTACH DATABASE ? AS src", (str(other),))
    have = {r[0] for r in con.execute("SELECT hand_id FROM hands")}
    incoming = [r[0] for r in con.execute("SELECT hand_id FROM src.hands")]
    new = [h for h in incoming if h not in have]
    if progress:
        progress(f"{len(new)} new of {len(incoming)}")
    added = 0
    for i in range(0, len(new), 500):
        chunk = new[i:i + 500]
        marks = ",".join("?" * len(chunk))
        cols = [r[1] for r in con.execute("PRAGMA table_info(hands)")]
        src_cols = {r[1] for r in con.execute("PRAGMA table_info(src.hands)")}
        shared = [c for c in cols if c in src_cols]
        con.execute(
            f"INSERT INTO hands ({','.join(shared)}) "
            f"SELECT {','.join(shared)} FROM src.hands "
            f"WHERE hand_id IN ({marks})", chunk)
        for table in ("seats", "actions"):
            tcols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            tsrc = {r[1] for r in con.execute(f"PRAGMA table_info(src.{table})")}
            sh = [c for c in tcols if c in tsrc]
            con.execute(
                f"INSERT INTO {table} ({','.join(sh)}) "
                f"SELECT {','.join(sh)} FROM src.{table} "
                f"WHERE hand_id IN ({marks})", chunk)
        added += len(chunk)
    con.commit()
    con.execute("DETACH DATABASE src")
    con.close()
    return {"added": added, "known": len(incoming) - len(new)}


def rebuild(progress=None):
    """
    Redo the derived tables, which is what makes new hands visible.

    Loading writes to `hands`, `seats` and `actions`; every question this
    program answers is asked of `spots` and `decisions`, which are derived
    from those. Skipping this leaves an import that appears to have done
    nothing.
    """
    import decisions
    import spots
    if progress:
        progress("deriving spots…")
    spots.build()
    if progress:
        progress("deriving decisions…")
    decisions.build()


def check(db_path=DB):
    """
    Sniffing is right on files whose site is already known.

    Every hand in the database came from a file, and the database records
    which site each came from -- so the detector can be held against the
    answer rather than against an opinion. A detector that is merely
    plausible is how eight thousand hands got loaded under the wrong name.
    """
    fails = []
    con = sqlite3.connect(db_path)
    known = con.execute(
        "SELECT source, site, COUNT(*) FROM hands WHERE source IS NOT NULL "
        "GROUP BY source, site").fetchall()
    con.close()

    # Find each recorded source file wherever it now lives, and re-sniff it.
    places = [Path(os.path.expandvars(p)) for p in KNOWN_PLACES]
    index = {}
    for place in places:
        if place.exists():
            for f in place.rglob("*.txt"):
                index.setdefault(f.name, f)

    tested = wrong = 0
    for source, site, _n in known:
        f = index.get(source)
        if f is None:
            continue
        tested += 1
        if sniff(f) != site:
            wrong += 1
            if wrong <= 3:
                print(f"    {source[:60]}: sniffed {sniff(f)}, recorded {site}")
    print(f"site detected correctly       {tested - wrong}/{tested} "
          f"files found on disk")
    if wrong:
        fails.append("the detector disagrees with the database")
    if tested == 0:
        print("    (no source files still on disk -- nothing to test against)")

    # A file of the wrong kind must be refused, not guessed at.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        junk = Path(tmp) / "notahand.txt"
        junk.write_text("this is not a poker hand\nnor is this\n")
        got = sniff(junk)
        print(f"nonsense is refused           {'yes' if got is None else 'NO -> ' + got}")
        if got is not None:
            fails.append("a non-hand-history file was identified as a site")

    places_found = scan()
    print(f"places holding hands          {len(places_found)}")
    for p in places_found:
        print(f"    {p['ignition']:>5} ignition  {p['acr']:>5} acr   {p['path']}")

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if "--scan" in argv:
        found = scan()
        if not found:
            print("no hand histories found in the usual places")
            return 0
        for p in found:
            print(f"{p['ignition']:>6} ignition {p['acr']:>6} acr   {p['path']}")
        return 0
    if "--merge" in argv:
        got = merge(argv[argv.index("--merge") + 1], progress=print)
        print(f"{got['added']} hands merged, {got['known']} already known")
        rebuild(progress=print)
        return 0
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 1
    got = load(paths, progress=print)
    print(f"\n{got['files']} files, {got['added']} hands added, "
          f"{got['known']} already known, {got['unknown']} unrecognised")
    for site, n in got["by_site"].items():
        print(f"  {site:10} {n}")
    if got["added"]:
        rebuild(progress=print)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
