"""
Ask the database anything, about anybody, in any spot.

The stat engine has always taken an arbitrary filter -- every `rate()` call
ends in a WHERE clause -- and nothing has ever been able to supply one. So
the database could answer "how often does this pool fold to a cbet on a
monotone flop, in a 3-bet pot, a hundred big blinds deep" and there was no
way to ask it. This is the asking.

Three questions, one filter:

    --stats     what everybody did in the spot the filter describes
    --hands     which hands those were
    --results   what the money did in them

The third needs care and is the reason this module exists rather than
another flag on `stats.py`. Money is a property of a HAND; "in position on a
monotone flop" is a property of a DECISION. Filtering the money table by a
decision's conditions is not possible, and filtering it loosely instead --
dropping the conditions it cannot express -- would answer a different
question under the same heading. So the filter selects decisions, and the
money is summed over the hands those decisions happened in.

    python query.py --pool --pot 3bet --street flop --ip
    python query.py --player dblj32 --pos BTN --stats
    python query.py --hero --board mono --results
    python query.py --hero --pot 3bet --hands
    python query.py --where "eff_bb > 150 AND fl_paired=1" --stats
    python query.py --help
"""

import sqlite3
import sys
from pathlib import Path

from stats import STATS, fmt, rate

DB = Path(__file__).parent / "hands.db"

STREETS = ("preflop", "flop", "turn", "river")
POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
POT_TYPES = ("unopened", "limped", "raised", "3bet", "4bet", "5bet+")
FACINGS = ("unopened", "open", "3bet", "4bet", "5bet+",
           "check", "bet", "raise")

# Each flag becomes a predicate over `decisions`. Keeping them here rather
# than scattered through the argument parsing means the vocabulary is one
# readable list, and `--help` can print it without drifting from the truth.
#
# A value flag takes an argument; a switch does not.
VALUE_FLAGS = {
    "--site": "site = {v}",
    "--player": "player = {v}",
    "--pos": "position IN ({list})",
    "--street": "street IN ({list})",
    "--pot": "pot_type IN ({list})",
    "--facing": "facing IN ({list})",
    "--combo": "combo IN ({list})",
    "--stake": "bb = {n}",
    "--deep": "eff_bb >= {n}",
    "--short": "eff_bb < {n}",
    "--players": "n_players = {n}",
    "--live": "n_live = {n}",
    "--since": "played_at >= {v}",
    "--until": "played_at <= {v}",
    "--board": None,        # handled separately: named textures
    "--where": None,        # raw SQL escape hatch
}

SWITCHES = {
    "--hero": "is_hero = 1",
    "--pool": "is_hero = 0",
    "--ip": "is_ip = 1",
    "--oop": "is_ip = 0",
    "--pfa": "is_pfa = 1",
    "--not-pfa": "is_pfa = 0",
    "--allin": "allin = 1",
    "--aggressive": "agg = 1",
    "--multiway": "n_live > 2",
    "--headsup": "n_live = 2",
    "--vs-pfa": "vs_pfa = 1",
    "--standard": "standard = 1",
}

BOARDS = {
    "mono": "fl_mono = 1",
    "twotone": "fl_twotone = 1",
    "rainbow": "fl_mono = 0 AND fl_twotone = 0",
    "paired": "fl_paired = 1",
    "unpaired": "fl_paired = 0",
    "connected": "fl_conn = 1",
    "dry": "fl_conn = 0 AND fl_paired = 0",
    "ace": "fl_hi = 'A'",
    "broadway": "fl_hi IN ('A','K','Q','J','T')",
    "low": "fl_hi IN ('2','3','4','5','6','7','8','9')",
}


def q(value):
    """A value as a SQL literal, with quotes doubled so a name cannot break out."""
    return "'" + str(value).replace("'", "''") + "'"


def build(argv):
    """
    The command line as one WHERE clause over `decisions`.

    Returns the clause and a human description of it, because a report whose
    heading does not say what was filtered is a report that will eventually
    be read as though it covered everything.
    """
    parts, described = [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in SWITCHES:
            parts.append(SWITCHES[a])
            described.append(a.lstrip("-"))
            i += 1
            continue
        if a in VALUE_FLAGS:
            if i + 1 >= len(argv):
                raise SystemExit(f"{a} needs a value")
            v = argv[i + 1]
            i += 2
            if a == "--where":
                parts.append("(" + v + ")")
                described.append(v)
                continue
            if a == "--board":
                for name in v.split(","):
                    if name not in BOARDS:
                        raise SystemExit(
                            f"unknown board texture {name!r} -- "
                            f"one of: {', '.join(BOARDS)}")
                    parts.append(BOARDS[name])
                described.append("board " + v)
                continue
            tpl = VALUE_FLAGS[a]
            if "{list}" in tpl:
                items = ", ".join(q(x.strip()) for x in v.split(","))
                parts.append(tpl.format(list=items))
            elif "{n}" in tpl:
                parts.append(tpl.format(n=float(v)))
            else:
                parts.append(tpl.format(v=q(v)))
            described.append(f"{a.lstrip('-')} {v}")
            continue
        raise SystemExit(f"unknown option {a!r} -- try --help")
    return (" AND ".join(parts) if parts else "1=1",
            ", ".join(described) if described else "everything")


def show_stats(con, where, label):
    """Every stat that has anything to say under this filter."""
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    n_dec = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    print(f"{n_dec} decisions match\n")
    if not n_dec:
        return

    last, shown = None, 0
    for s in STATS:
        # A spots-sourced stat cannot see a decision's conditions -- there is
        # no street or position-to-act in a per-hand row -- so it is skipped
        # rather than silently answered over a different population.
        if s.source == "s":
            continue
        n, k, p, lo, hi = rate(con, s, where)
        if not n:
            continue
        if s.group != last:
            print(f"  [{s.group}]")
            last = s.group
        print(f"  {s.label:22} {fmt(n, k, p, lo, hi)}")
        shown += 1
    if not shown:
        print("  no stat has a chance to occur inside this filter.")
        print("  (asking for a preflop stat inside --street flop does this)")


def matching_seats(con, where):
    """The (hand, seat) pairs that had a decision matching the filter."""
    return con.execute(
        f"SELECT DISTINCT hand_id, seat FROM decisions WHERE {where}"
    ).fetchall()


def show_results(con, where, label):
    """
    What the money did in the hands this filter selects.

    Summed over whole hands, not over the filtered decisions, and the
    difference matters: a player who was in position on a monotone flop won
    or lost the WHOLE pot, not the part of it that happened after the flop.
    """
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    pairs = matching_seats(con, where)
    if not pairs:
        print("nothing matches")
        return
    con.execute("CREATE TEMP TABLE IF NOT EXISTS _sel (hand_id TEXT, seat INT)")
    con.execute("DELETE FROM _sel")
    con.executemany("INSERT INTO _sel VALUES (?,?)", pairs)

    row = con.execute(
        "SELECT COUNT(*), SUM(s.net_bb), SUM(s.won - s.put_in), "
        "       SUM(s.saw_flop), SUM(s.wtsd), SUM(s.wwsf) "
        "FROM spots s JOIN _sel ON _sel.hand_id = s.hand_id "
        "AND _sel.seat = s.seat WHERE s.fmt <> 'MTT'").fetchone()
    n, net_bb, money, saw, wtsd, wwsf = row
    if not n:
        print("nothing matches outside tournaments")
        return
    print(f"  hands            {n:8d}")
    print(f"  net              {net_bb or 0:+8.1f} bb   (${money or 0:+.2f})")
    print(f"  per 100 hands    {100 * (net_bb or 0) / n:+8.1f} bb/100")
    # A win rate over a few hundred hands is noise wearing a number's
    # clothing: one hand's result has a standard deviation around 11.7bb, so
    # the error on bb/100 is 1170/sqrt(n) and it is usually larger than
    # anything being compared.
    print(f"  error on that    {1170 / max(1, n) ** 0.5:8.0f} bb/100"
          f"   <- and this is why")
    if saw:
        print(f"  saw a flop       {saw:8d}   ({100 * saw / n:.1f}%)")
        print(f"  won at showdown  {wtsd or 0:8d}")
        print(f"  won after flop   {100 * (wwsf or 0) / saw:8.1f}%")


def show_hands(con, where, label, limit=40):
    """The hands themselves, most recent first."""
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    # The filter names bare columns, and `spots` shares several of them
    # with `decisions` -- is_hero, position, combo -- so it is applied inside
    # a subquery where there is only one table for a name to mean.
    rows = con.execute(
        f"SELECT DISTINCT d.hand_id, d.seat, d.played_at, d.site, d.bb, "
        f"       d.position, d.combo, d.board, s.net_bb "
        f"FROM (SELECT * FROM decisions WHERE {where}) d "
        f"LEFT JOIN spots s "
        f"  ON s.hand_id = d.hand_id AND s.seat = d.seat "
        f"ORDER BY d.played_at DESC").fetchall()
    print(f"{len(rows)} hands match; showing up to {limit}\n")
    print(f"  {'when':17} {'site':10} {'bb':>5} {'pos':4} {'hand':5} "
          f"{'net bb':>7}  board")
    print("  " + "-" * 74)
    for hid, seat, when, site, bb, pos, combo, board, net in rows[:limit]:
        print(f"  {when[:16]:17} {site:10} {bb or 0:5.2f} {pos or '?':4} "
              f"{combo or '--':5} {net if net is not None else 0:7.1f}  "
              f"{board or ''}")


def usage():
    print(__doc__)
    print("FILTERS -- combine as many as you like\n")
    print("  switches:")
    for k, v in SWITCHES.items():
        print(f"    {k:14} {v}")
    print("\n  taking a value (comma-separate lists):")
    for k, v in VALUE_FLAGS.items():
        if v:
            print(f"    {k:14} {v}")
    print(f"    {'--board':14} one of: {', '.join(BOARDS)}")
    print(f"    {'--where':14} raw SQL over `decisions`, for anything above")
    print("\n  positions: " + ", ".join(POSITIONS))
    print("  streets:   " + ", ".join(STREETS))
    print("  pot types: " + ", ".join(POT_TYPES))
    print("  facing:    " + ", ".join(FACINGS))


def check(db_path=DB):
    """
    Every filter is valid SQL, and every filter actually filters.

    The second half is the one that matters. A predicate with a typo in a
    column name raises, and gets noticed. A predicate that is merely WRONG
    -- comparing a text column to a number, naming a value that never
    occurs, or accidentally being always-true -- returns a row count and no
    complaint, and every figure computed under it silently describes a
    different population than its heading claims. So each filter must both
    run and select strictly fewer rows than no filter at all.
    """
    con = sqlite3.connect(db_path)
    total = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    fails, checked = [], 0

    # A representative value for each value-taking flag, chosen to be one
    # that really occurs, so "selects nothing" means the predicate is wrong
    # rather than the value being absent.
    # The date sample is taken from the middle of the corpus rather than
    # written in. A date outside the data passes "runs without error" and
    # fails "actually narrows", which is a fault in the test rather than in
    # the filter -- and a test that cries wolf is one that stops being read.
    midpoint = con.execute(
        "SELECT played_at FROM decisions ORDER BY played_at "
        "LIMIT 1 OFFSET (SELECT COUNT(*) / 2 FROM decisions)").fetchone()[0]
    samples = {
        "--site": "coinpoker", "--pos": "BTN", "--street": "flop",
        "--pot": "3bet", "--facing": "bet", "--combo": "AKs",
        "--stake": "0.1", "--deep": "50", "--short": "200",
        "--players": "6", "--live": "2",
        "--since": midpoint, "--until": midpoint,
    }
    cases = [(k, [k]) for k in SWITCHES]
    cases += [(k, [k, v]) for k, v in samples.items()]
    cases += [("--board " + b, ["--board", b]) for b in BOARDS]
    cases.append(("--where", ["--where", "eff_bb > 100"]))
    cases.append(("--player", ["--player", con.execute(
        "SELECT player FROM decisions WHERE player IS NOT NULL LIMIT 1"
    ).fetchone()[0]]))

    for name, argv in cases:
        where, _ = build(argv)
        checked += 1
        try:
            n = con.execute(
                f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
        except sqlite3.Error as e:
            fails.append(f"{name}: {e}")
            continue
        if n == 0:
            fails.append(f"{name}: selects nothing")
        elif n == total:
            fails.append(f"{name}: selects everything -- it is not filtering")

    print(f"filters that run and narrow  {checked - len(fails)}/{checked}")
    for f in fails:
        print(f"    {f}")

    # The three modes must survive a filter that legitimately matches nothing,
    # since a user will type one within a day of being given the tool.
    empty, _ = build(["--pos", "BTN", "--street", "preflop",
                      "--facing", "check"])
    for mode, fn in (("stats", show_stats), ("results", show_results),
                     ("hands", show_hands)):
        try:
            import io
            import contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                fn(con, empty, "empty")
        except Exception as e:
            fails.append(f"--{mode} on an empty filter: {e}")
    print(f"modes survive an empty result "
          f"{'yes' if not any('empty filter' in f for f in fails) else 'NO'}")

    con.close()
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if not argv or "--help" in argv or "-h" in argv:
        usage()
        return 0
    if "--check" in argv:
        return 0 if check() else 1
    mode = "--stats"
    for m in ("--stats", "--hands", "--results"):
        if m in argv:
            mode = m
            argv = [a for a in argv if a != m]
    where, label = build(argv)
    con = sqlite3.connect(DB)
    if mode == "--hands":
        show_hands(con, where, label)
    elif mode == "--results":
        show_results(con, where, label)
    else:
        show_stats(con, where, label)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
