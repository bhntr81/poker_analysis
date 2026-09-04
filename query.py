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
    --graph     the four-line results graph, written as an HTML file

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

from stats import BY_KEY, STATS, fmt, rate, rates_by, wilson

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

# Not filters -- they change what is shown, not what is selected.
OPTIONS = ("--by", "--show", "--min", "--out")

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

# What a report can be split by. A tracker's value is mostly here: one
# number for "how often do I 3-bet" is a fact, the same number broken down
# by position is a plan.
#
# Each is an SQL expression over `decisions`, plus how to order the rows,
# since a report sorted alphabetically puts August before February and the
# big blind before the button.
DIMENSIONS = {
    "position": ("position", lambda k: (
        ["UTG", "HJ", "CO", "BTN", "SB", "BB"].index(k)
        if k in ("UTG", "HJ", "CO", "BTN", "SB", "BB") else 99)),
    "stake": ("bb", lambda k: float(k or 0)),
    "site": ("site", str),
    "player": ("player", str),
    "month": ("substr(played_at, 1, 7)", str),
    "day": ("substr(played_at, 1, 10)", str),
    "pot": ("pot_type", lambda k: (
        ["unopened", "limped", "raised", "3bet", "4bet", "5bet+"].index(k)
        if k in ("unopened", "limped", "raised", "3bet", "4bet", "5bet+")
        else 99)),
    "street": ("street", lambda k: (
        ["preflop", "flop", "turn", "river"].index(k)
        if k in ("preflop", "flop", "turn", "river") else 99)),
    "facing": ("facing", str),
    "players": ("n_players", lambda k: float(k or 0)),
    "hi": ("fl_hi", str),
}

# Eight columns is what fits and what gets read. Anything else is available
# with --show.
DEFAULT_COLUMNS = ["vpip", "pfr", "rfi", "threebet", "fold_to_3bet",
                   "cbet_flop", "fold_to_cbet", "flop_agg"]

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
        if a in OPTIONS:
            i += 2
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


# The four lines every tracker draws, and what each one is for.
#
#   green   every big blind won or lost. The bottom line.
#   blue    the part of it won at showdown.
#   red     the part won without one -- pots taken by betting.
#   yellow  green again, with all-in pots scored by what they were worth
#           rather than by what the deck did afterwards.
#
# Blue and red add up to green exactly: a hand either reached a showdown or
# it did not. Read apart they say different things -- a winning red line
# with a losing blue one is somebody who takes pots away and pays off when
# called, and the reverse is somebody too passive to win without a hand.
LINES = [
    ("total", "#22a35a", "every bb won or lost"),
    ("showdown", "#2f7fd6", "won at showdown"),
    ("nonshowdown", "#d1443c", "won without a showdown"),
    ("allin_ev", "#e0b020", "all-in pots at their equity"),
]

# A graph is drawn once and looked at; half a point of sampling error on one
# preflop all-in cannot move a line anybody can see, so preflop runouts are
# sampled more coarsely here than `equity`'s own default.
GRAPH_SAMPLES = 5000


def adjusted(con, pairs):
    """
    Each hand's result, and its result had the all-ins run at their equity.

    Only the clean case is adjusted: two players left, both hands known, and
    chips in with cards still to come. Side pots and three-handed all-ins are
    left at their actual result and COUNTED, because an EV line that quietly
    drops the hands it cannot price is an EV line about a different set of
    hands than the one beside it.
    """
    from equity import equity

    rows = con.execute(
        "SELECT s.hand_id, s.seat, s.played_at, s.net_bb, s.wtsd, s.bb, "
        "       s.put_in, h.board "
        "FROM spots s JOIN hands h USING(hand_id) JOIN _sel "
        "  ON _sel.hand_id = s.hand_id AND _sel.seat = s.seat "
        "WHERE s.fmt <> 'MTT' AND s.net_bb IS NOT NULL "
        "ORDER BY s.played_at, s.hand_id").fetchall()

    # Which hands had an all-in with cards to come, and on what street.
    allin_street = dict(con.execute(
        "SELECT hand_id, street FROM decisions d WHERE allin = 1 "
        "AND d.n = (SELECT MAX(n) FROM decisions x WHERE x.hand_id = d.hand_id)"
    ).fetchall())

    out, adjusted_n, skipped = [], 0, 0
    cache = {}
    for hid, seat, when, net_bb, wtsd, bb, put_in, board in rows:
        ev_bb = net_bb
        street = allin_street.get(hid)
        if street and street != "river" and bb:
            live = con.execute(
                "SELECT seat, cards, won FROM spots WHERE hand_id=? "
                "AND folded_on IS NULL AND cards IS NOT NULL", (hid,)).fetchall()
            if len(live) == 2 and all(len(c.split()) == 2 for _, c, _ in live):
                take = {"preflop": 0, "flop": 3, "turn": 4}[street]
                at_allin = " ".join((board or "").split()[:take])
                pot = sum(r[2] or 0 for r in live) or 0.0
                key = (tuple(sorted(c for _, c, _ in live)), at_allin)
                if key not in cache:
                    cache[key] = equity([c for _, c, _ in live], at_allin,
                                        samples=GRAPH_SAMPLES)
                shares = cache[key]
                mine = next((i for i, (st, _, _) in enumerate(live)
                             if st == seat), None)
                if mine is not None and pot:
                    ev_bb = round((shares[mine] * pot - put_in) / bb, 3)
                    adjusted_n += 1
            else:
                skipped += 1
        out.append((when, net_bb, bool(wtsd), ev_bb))
    return out, adjusted_n, skipped


def svg(series, label, note):
    """The four lines as one standalone SVG, no library and no dependency."""
    W, H, L, R, T, B = 960, 440, 70, 210, 46, 40
    n = len(series["total"])
    if n < 2:
        return "<p>not enough hands to draw a line</p>"
    lo = min(min(v) for v in series.values())
    hi = max(max(v) for v in series.values())
    lo, hi = min(lo, 0.0), max(hi, 0.0)
    span = (hi - lo) or 1.0

    def x(i):
        return L + (W - L - R) * i / (n - 1)

    def y(v):
        return T + (H - T - B) * (1 - (v - lo) / span)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="100%" style="max-width:{W}px;font-family:system-ui,sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#fbfbfa"/>',
        f'<text x="{L}" y="24" font-size="15" font-weight="600">{label}</text>',
        f'<text x="{L}" y="40" font-size="11" fill="#666">{note}</text>',
    ]
    # Horizontal guides, and the zero line drawn darker because crossing it
    # is the only thing on this chart that changes the answer.
    for frac in range(5):
        v = lo + span * frac / 4
        parts.append(
            f'<line x1="{L}" y1="{y(v):.1f}" x2="{W - R}" y2="{y(v):.1f}" '
            f'stroke="#e6e6e3"/>'
            f'<text x="{L - 8}" y="{y(v) + 4:.1f}" font-size="11" fill="#888" '
            f'text-anchor="end">{v:,.0f}</text>')
    parts.append(f'<line x1="{L}" y1="{y(0):.1f}" x2="{W - R}" y2="{y(0):.1f}" '
                 f'stroke="#999" stroke-dasharray="3,3"/>')
    for i, (key, colour, why) in enumerate(LINES):
        pts = " ".join(f"{x(j):.1f},{y(v):.1f}" for j, v in
                       enumerate(series[key]))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{colour}" '
                     f'stroke-width="1.8"/>')
        end = series[key][-1]
        ly = T + 14 + i * 34
        parts.append(
            f'<line x1="{W - R + 6}" y1="{ly - 4}" x2="{W - R + 26}" '
            f'y2="{ly - 4}" stroke="{colour}" stroke-width="2.5"/>'
            f'<text x="{W - R + 32}" y="{ly}" font-size="12">'
            f'{key.replace("_", " ")}  <tspan font-weight="600">{end:+,.0f}'
            f'</tspan></text>'
            f'<text x="{W - R + 32}" y="{ly + 14}" font-size="10" fill="#777">'
            f'{why}</text>')
    parts.append(f'<text x="{(L + W - R) / 2}" y="{H - 10}" font-size="11" '
                 f'fill="#888" text-anchor="middle">{n:,} hands</text>')
    parts.append(f'<text x="{L - 52}" y="{(T + H - B) / 2}" font-size="11" '
                 f'fill="#888" transform="rotate(-90 {L - 52} '
                 f'{(T + H - B) / 2})" text-anchor="middle">big blinds</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def show_graph(con, where, label, out_path="graph.html"):
    """The results graph, over whatever the filter selected."""
    pairs = matching_seats(con, where)
    if not pairs:
        print("nothing matches")
        return
    con.execute("CREATE TEMP TABLE IF NOT EXISTS _sel (hand_id TEXT, seat INT)")
    con.execute("DELETE FROM _sel")
    con.executemany("INSERT INTO _sel VALUES (?,?)", pairs)

    hands, adj, skipped = adjusted(con, pairs)
    if len(hands) < 2:
        print("not enough hands to draw a line")
        return

    series = {k: [] for k, _, _ in LINES}
    total = sd = nsd = ev = 0.0
    for _when, net, was_sd, ev_net in hands:
        total += net or 0.0
        ev += ev_net or 0.0
        if was_sd:
            sd += net or 0.0
        else:
            nsd += net or 0.0
        series["total"].append(total)
        series["showdown"].append(sd)
        series["nonshowdown"].append(nsd)
        series["allin_ev"].append(ev)

    note = (f"{len(hands):,} hands  ·  {adj} all-in pots scored at equity"
            + (f"  ·  {skipped} left unadjusted (side pots or three-handed)"
               if skipped else ""))
    body = svg(series, label, note)
    path = Path(out_path)
    path.write_text(
        "<!doctype html><meta charset='utf-8'>"
        f"<title>results: {label}</title>"
        "<body style='margin:24px;background:#fff'>" + body + "</body>",
        encoding="utf-8")

    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    print(f"  hands              {len(hands):>10,}")
    for key, _c, why in LINES:
        print(f"  {key.replace('_', ' '):18} {series[key][-1]:>+10,.1f} bb"
              f"   {why}")
    print(f"\n  {adj} all-in pots scored at their equity"
          + (f"; {skipped} left alone (side pots or three-handed)"
             if skipped else ""))
    print(f"  written to {path.resolve()}")
    return str(path.resolve())


def show_report(con, where, label, dim, columns, min_n=30):
    """
    One row per value of the dimension, one column per stat.

    Cells below `min_n` chances are printed but marked, because dropping
    them would hide that the split ran out of data and leaving them unmarked
    would let a 100% on four hands be read as a tendency.
    """
    expr, order = DIMENSIONS[dim]
    print(f"\nfilter: {label}")
    print(f"by {dim}")
    print("=" * (len(label) + 8))

    stats = [BY_KEY[c] for c in columns]
    grid = {s.key: rates_by(con, s, expr, where) for s in stats}
    counts = rates_by(con, BY_KEY["vpip"], expr, where)
    keys = sorted({k for g in grid.values() for k in g},
                  key=lambda k: order(k) if k is not None else "")
    if not keys:
        print("nothing matches")
        return

    width = max(12, min(22, max(len(str(k)) for k in keys) + 1))
    head = f"  {dim[:width - 1]:<{width}}" + "".join(
        f"{BY_KEY[c].label[:9]:>11}" for c in columns)
    print(head)
    print("  " + "-" * (len(head) - 2))
    for k in keys:
        cells = []
        for c in columns:
            n, kk = grid[c].get(k, (0, 0))
            if not n:
                cells.append(f"{'--':>11}")
            else:
                mark = "?" if n < min_n else " "
                cells.append(f"{100 * kk / n:9.1f}%{mark}")
        print(f"  {str(k)[:width - 1]:<{width}}" + "".join(cells))

    # The denominators, on their own line rather than beside every cell:
    # a percentage without its n is not a number anybody should act on, and
    # a table with n beside every cell is a table nobody can read.
    print("\n  chances behind each row (VPIP's denominator):")
    for k in keys:
        n = counts.get(k, (0, 0))[0]
        print(f"    {str(k)[:width - 1]:<{width}} n={n}")
    print("\n  '?' marks a cell measured on fewer than "
          f"{min_n} chances -- ignore it.")


def show_results_by(con, where, label, dim):
    """Money, split by the dimension. The tracking half of a tracker."""
    expr, order = DIMENSIONS[dim]
    print(f"\nfilter: {label}")
    print(f"by {dim}")
    print("=" * (len(label) + 8))
    values = [r[0] for r in con.execute(
        f"SELECT DISTINCT {expr} FROM decisions WHERE ({where}) "
        f"AND ({expr}) IS NOT NULL")]
    if not values:
        print("nothing matches")
        return
    print(f"  {dim:<14}{'hands':>8}{'net bb':>11}{'bb/100':>10}"
          f"{'+/-':>8}")
    print("  " + "-" * 50)
    for v in sorted(values, key=order):
        lit = q(v) if isinstance(v, str) else str(v)
        pairs = matching_seats(con, f"({where}) AND ({expr}) = {lit}")
        if not pairs:
            continue
        con.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _sel (hand_id TEXT, seat INT)")
        con.execute("DELETE FROM _sel")
        con.executemany("INSERT INTO _sel VALUES (?,?)", pairs)
        n, net = con.execute(
            "SELECT COUNT(*), SUM(s.net_bb) FROM spots s JOIN _sel "
            "ON _sel.hand_id = s.hand_id AND _sel.seat = s.seat "
            "WHERE s.fmt <> 'MTT'").fetchone()
        if not n:
            continue
        # 1170/sqrt(n) is the 95% error on a win rate, from one hand's
        # standard deviation of about 11.7bb. It is printed beside every row
        # because it is usually larger than the differences between them.
        print(f"  {str(v)[:14]:<14}{n:>8}{net or 0:>11.1f}"
              f"{100 * (net or 0) / n:>10.1f}{1170 / n ** 0.5:>8.0f}")


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
    print("\n  reports (not filters):")
    print(f"    {'--by':14} split into a table by one of: "
          f"{', '.join(DIMENSIONS)}")
    print(f"    {'--show':14} which stats are the columns "
          f"(default: {','.join(DEFAULT_COLUMNS)})")
    print(f"    {'--min':14} mark cells below this many chances (default 30)")
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
    for m in ("--stats", "--hands", "--results", "--graph"):
        if m in argv:
            mode = m
            argv = [a for a in argv if a != m]

    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    dim = opt("--by")
    if dim is not None and dim not in DIMENSIONS:
        raise SystemExit(f"unknown dimension {dim!r} -- "
                         f"one of: {', '.join(DIMENSIONS)}")
    columns = (opt("--show") or ",".join(DEFAULT_COLUMNS)).split(",")
    for c in columns:
        if c not in BY_KEY:
            raise SystemExit(f"unknown stat {c!r} -- see `stats.py --list`")
    min_n = int(opt("--min", "30"))

    where, label = build(argv)
    con = sqlite3.connect(DB)
    if mode == "--graph":
        show_graph(con, where, label, opt("--out", "graph.html"))
    elif mode == "--hands":
        show_hands(con, where, label)
    elif mode == "--results":
        if dim:
            show_results_by(con, where, label, dim)
        else:
            show_results(con, where, label)
    elif dim:
        show_report(con, where, label, dim, columns, min_n)
    else:
        show_stats(con, where, label)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
