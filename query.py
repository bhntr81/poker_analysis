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

and one hand on its own:

    python query.py --hand cp-2459218653

The third needs care and is the reason this module exists rather than
another flag on `stats.py`. Money is a property of a HAND; "in position on a
monotone flop" is a property of a DECISION. Filtering the money table by a
decision's conditions is not possible, and filtering it loosely instead --
dropping the conditions it cannot express -- would answer a different
question under the same heading. So the filter selects decisions, and the
money is summed over the hands those decisions happened in.

    python query.py --pool --pos BB --vs BTN --pot 3bet
    python query.py --hero --pos BB --vs BTN --vs-pool --pot 3bet
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
    # The matchup. `--pos BB --vs BTN --pot 3bet` is "big blind against a
    # button open, in a 3-bet pot", which is the shape most real questions
    # about a pool actually have. Add `--vs-hero` or `--vs-pool` to say
    # which side of the table the other seat is.
    "--vs": "vs_pos IN ({list})",
    "--opener": "opener_pos IN ({list})",
    "--raiser": "pfa_pos IN ({list})",
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
    "--quick": None,        # one or more named filters from `quick_filters`
    "--where": None,        # raw SQL escape hatch
}

# Not filters -- they change what is shown, not what is selected.
OPTIONS = ("--by", "--show", "--min", "--out", "--hand")

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
    "--vs-hero": "vs_hero = 1",
    "--vs-pool": "vs_hero = 0",
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
    "vs": ("vs_pos", lambda k: (
        ["UTG", "HJ", "CO", "BTN", "SB", "BB"].index(k)
        if k in ("UTG", "HJ", "CO", "BTN", "SB", "BB") else 99)),
    "opener": ("opener_pos", lambda k: (
        ["UTG", "HJ", "CO", "BTN", "SB", "BB"].index(k)
        if k in ("UTG", "HJ", "CO", "BTN", "SB", "BB") else 99)),
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


# A statistic and a hand filter are the same object seen twice. A `Stat` is
# a chance and an action -- "the times a continuation bet was possible" and
# "the times one was made" -- so the filter "hands where a continuation bet
# was made" is simply both of them at once. Every stat in the registry is
# therefore a one-click filter already, named the way a player names it, and
# a new stat brings a new filter with it for nothing.
#
# The negatives are worth naming separately because "did not" is a different
# question from "did", and a player asking about missed continuation bets is
# not asking about continuation bets.
NEGATIVES = {
    "cbet_flop": "Missed Continuation Bet Flop",
    "cbet_turn": "Missed 2nd Barrel Turn",
    "threebet": "Did Not 3-Bet",
    "steal": "Did Not Steal",
    "fold_to_cbet": "Continued vs Continuation Bet",
    "fold_to_3bet": "Continued vs 3-Bet",
}


def quick_filters():
    """Named one-click filters, in the order they should be shown."""
    out = []
    for st in STATS:
        if st.source != "d":
            continue
        out.append({"group": st.group, "label": st.label, "key": st.key,
                    "sql": f"({st.chance}) AND ({st.action})",
                    "note": st.note})
        if st.key in NEGATIVES:
            out.append({"group": st.group, "label": NEGATIVES[st.key],
                        "key": st.key + "_not",
                        "sql": f"({st.chance}) AND NOT ({st.action})",
                        "note": f"had the chance and did not"})
    return out


QUICK_BY_KEY = {q["key"]: q for q in quick_filters()}


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
            if a == "--quick":
                for name in v.split(","):
                    if name not in QUICK_BY_KEY:
                        raise SystemExit(f"unknown quick filter {name!r}")
                    parts.append("(" + QUICK_BY_KEY[name]["sql"] + ")")
                    described.append(QUICK_BY_KEY[name]["label"])
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
            ", ".join(described) if described else "everything",
            list(zip(described, parts)))


# Columns that do not exist before the flop, and the reason each one does
# not. A filter combining any of these with "preflop" is not broken -- it is
# asking for something that cannot have happened -- but it returns nothing
# and looks broken, so the reason is kept here to be said out loud.
# Columns that are null when the pot is not heads up, which is a
# legitimate emptiness rather than a mistake and so gets its reason too.
MULTIWAY_NULL = {
    "vs_pos": "there is only an 'opponent' while one player is left in the "
              "pot -- a multiway decision has no single other seat",
    "vs_hero": "there is only an 'opponent' while one player is left in the "
               "pot -- a multiway decision has no single other seat",
    "opener_pos": "nobody opened -- that is a limped pot",
}
ONLY_POSTFLOP = {
    "is_ip": "who acts last is only settled once the flop is out",
    "is_pfa": "there is no preflop aggressor until preflop is over",
    "fl_mono": "the flop had not come",
    "fl_twotone": "the flop had not come",
    "fl_paired": "the flop had not come",
    "fl_conn": "the flop had not come",
    "fl_hi": "the flop had not come",
}
# The two ladders use different words, and a word from one never appears in
# the other.
PREFLOP_FACING = ("unopened", "open", "3bet", "4bet", "5bet+")
POSTFLOP_FACING = ("check", "bet", "raise")


def why_empty(con, parts):
    """
    Why a filter matched nothing, in a sentence.

    An empty table is the least informative thing an interface can show, and
    most of the empty ones here are not mistakes: a preflop decision has no
    position-to-act and no flop texture, and "facing a bet" is a postflop
    word where "facing an open" is a preflop one. Every one of those is a
    reasonable thing to click and none of them can return a row. So rather
    than a blank page, the filter is taken apart and the first term or pair
    that kills it is named.
    """
    if not parts:
        return "the database is empty"
    count = lambda w: con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {w}").fetchone()[0]

    alone = [(label, sql, count(sql)) for label, sql in parts]
    dead = [f"'{label}'" for label, _sql, n in alone if not n]
    if dead:
        return f"{', '.join(dead)} matches nothing at all in this database"

    for i, (l1, s1, _n1) in enumerate(alone):
        for l2, s2, _n2 in alone[i + 1:]:
            if count(f"({s1}) AND ({s2})"):
                continue
            hint = ""
            pre = "preflop" in (s1 + s2)
            for col, reason in MULTIWAY_NULL.items():
                if col in s1 + s2:
                    hint = f" -- {reason}"
                    break
            for col, reason in ONLY_POSTFLOP.items():
                if col in s1 + s2 and pre:
                    hint = f" -- {reason}"
                    break
            if not hint and "pot_type" in s1 + s2 and pre:
                # A pot is only "limped" once it is settled that nobody
                # raised, which is a fact about the pot AFTER preflop. While
                # preflop is still happening the pot is "unopened".
                hint = (" -- a pot is not limped until preflop is over; "
                        "during it the pot type is 'unopened'")
            if not hint and "facing" in s1 + s2:
                if any(f"'{w}'" in s1 + s2 for w in POSTFLOP_FACING) and pre:
                    hint = (" -- 'bet', 'raise' and 'check' describe postflop "
                            "streets; preflop uses 'open', '3bet', '4bet'")
                elif any(f"'{w}'" in s1 + s2 for w in PREFLOP_FACING):
                    hint = (" -- 'open', '3bet' and '4bet' describe preflop; "
                            "after the flop it is 'bet', 'raise', 'check'")
            return f"'{l1}' and '{l2}' never occur together{hint}"

    return ("no two of these conflict on their own, but together they select "
            "nothing -- drop one at a time to find the pair that does")


def stats_of(con, where):
    """
    Every stat that has anything to say under this filter, as data.

    Separated from the printing because a second front end wants the same
    numbers in a different shape, and two front ends computing them their own
    way is how they come to disagree.
    """
    n_dec = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    rows = []
    for s in STATS:
        # A spots-sourced stat cannot see a decision's conditions -- there is
        # no street or position-to-act in a per-hand row -- so it is skipped
        # rather than silently answered over a different population.
        if s.source == "s":
            continue
        n, k, p, lo, hi = rate(con, s, where)
        if not n:
            continue
        rows.append({"key": s.key, "label": s.label, "group": s.group,
                     "note": s.note, "n": n, "k": k, "pct": 100 * p,
                     "band": 100 * (hi - lo) / 2})
    return n_dec, rows


def show_stats(con, where, label, parts=()):
    """Every stat that has anything to say under this filter."""
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    n_dec, rows = stats_of(con, where)
    print(f"{n_dec} decisions match\n")
    if not n_dec:
        print("  " + why_empty(con, parts))
        return
    last = None
    for r in rows:
        if r["group"] != last:
            print(f"  [{r['group']}]")
            last = r["group"]
        thin = " ?" if r["n"] < 30 else "  "
        print(f"  {r['label']:22} {r['pct']:6.1f}% "
              f"{'+/-%.0f' % r['band']:>7}{thin} n={r['n']:<6d}")
    if not rows:
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
GRAPH_SAMPLES = 2000


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

    # An all-in's equity is a fact about a hand that happened: the cards are
    # dealt, the board is known, and nothing about it will ever be different.
    # Recomputing it every time a graph is drawn cost twenty seconds a view,
    # which is most of what the graph cost at all, so it is worked out once
    # and kept. Rebuilding the derived tables does not invalidate it, because
    # what it measures is in the hand history rather than in the derivation.
    con.execute("CREATE TABLE IF NOT EXISTS hand_ev ("
                "hand_id TEXT, seat INT, ev_bb REAL, "
                "PRIMARY KEY (hand_id, seat))")
    known_ev = {(h, st): v for h, st, v in con.execute(
        "SELECT hand_id, seat, ev_bb FROM hand_ev")}
    fresh = []

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
        if (hid, seat) in known_ev:
            ev_bb = known_ev[(hid, seat)]
            adjusted_n += 1 if ev_bb != net_bb else 0
            out.append((when, net_bb, bool(wtsd), ev_bb))
            continue
        if street and street != "river" and bb:
            live = con.execute(
                "SELECT seat, cards, won FROM spots WHERE hand_id=? "
                "AND folded_on IS NULL AND cards IS NOT NULL", (hid,)).fetchall()
            if len(live) == 2 and all(len(c.split()) == 2 for _, c, _ in live):
                # Whether this player is one of the two comes first. They may
                # have folded long before the other two got it in, and pricing
                # a runout they were not in is work thrown away -- work that
                # was being redone on every view, because a result that is
                # discarded is never cached.
                mine = next((i for i, (st, _, _) in enumerate(live)
                             if st == seat), None)
                pot = sum(r[2] or 0 for r in live) or 0.0
                if mine is not None and pot:
                    take = {"preflop": 0, "flop": 3, "turn": 4}[street]
                    at_allin = " ".join((board or "").split()[:take])
                    key = (tuple(sorted(c for _, c, _ in live)), at_allin)
                    if key not in cache:
                        cache[key] = equity([c for _, c, _ in live], at_allin,
                                            samples=GRAPH_SAMPLES)
                    ev_bb = round((cache[key][mine] * pot - put_in) / bb, 3)
                    adjusted_n += 1
                    fresh.append((hid, seat, ev_bb))
            else:
                skipped += 1
        out.append((when, net_bb, bool(wtsd), ev_bb))
    if fresh:
        con.executemany("INSERT OR REPLACE INTO hand_ev VALUES (?,?,?)", fresh)
        con.commit()
    return out, adjusted_n, skipped


def svg(series, label, note, dark=False):
    """The four lines as one standalone SVG, no library and no dependency."""
    W, H, L, R, T, B = 960, 440, 70, 210, 46, 40
    # The line colours read on either ground; everything around them does not.
    ink, grid, dim, paper = (("#d8dbe0", "#2a2f38", "#8b929c", "#14161a")
                             if dark else
                             ("#111111", "#e6e6e3", "#888888", "#fbfbfa"))
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
        f'<rect width="{W}" height="{H}" fill="{paper}"/>',
        f'<text x="{L}" y="24" font-size="15" font-weight="600" fill="{ink}">{label}</text>',
        f'<text x="{L}" y="40" font-size="11" fill="{dim}">{note}</text>',
    ]
    # Horizontal guides, and the zero line drawn darker because crossing it
    # is the only thing on this chart that changes the answer.
    for frac in range(5):
        v = lo + span * frac / 4
        parts.append(
            f'<line x1="{L}" y1="{y(v):.1f}" x2="{W - R}" y2="{y(v):.1f}" '
            f'stroke="{grid}"/>'
            f'<text x="{L - 8}" y="{y(v) + 4:.1f}" font-size="11" fill="{dim}" '
            f'text-anchor="end">{v:,.0f}</text>')
    parts.append(f'<line x1="{L}" y1="{y(0):.1f}" x2="{W - R}" y2="{y(0):.1f}" '
                 f'stroke="{dim}" stroke-dasharray="3,3"/>')
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
            f'<text x="{W - R + 32}" y="{ly}" font-size="12" fill="{ink}">'
            f'{key.replace("_", " ")}  <tspan font-weight="600">{end:+,.0f}'
            f'</tspan></text>'
            f'<text x="{W - R + 32}" y="{ly + 14}" font-size="10" fill="{dim}">'
            f'{why}</text>')
    parts.append(f'<text x="{(L + W - R) / 2}" y="{H - 10}" font-size="11" '
                 f'fill="{dim}" text-anchor="middle">{n:,} hands</text>')
    parts.append(f'<text x="{L - 52}" y="{(T + H - B) / 2}" font-size="11" '
                 f'fill="{dim}" transform="rotate(-90 {L - 52} '
                 f'{(T + H - B) / 2})" text-anchor="middle">big blinds</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def show_graph(con, where, label, out_path="graph.html"):
    """The results graph, over whatever the filter selected."""
    pairs = matching_seats(con, where)
    if not pairs:
        print("nothing matches")
        return
    select_into(con, pairs)
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


# What each action code means when it is read back rather than counted.
VERBS = {"F": "folds", "X": "checks", "C": "calls", "B": "bets",
         "R": "raises to", "A": "all-in"}


def hand_detail(con, hand_id, seat=None):
    """
    One hand, replayed: who sat where, what they held, and what they did.

    A list of hands you cannot open is a dead end, and filtering down to
    fourteen interesting hands is only useful if the fourteenth can then be
    looked at. The pot before each action comes from `decisions` rather than
    being replayed again here -- there is one pot reconstruction in this
    project and this is not a second one.

    Ignition shows every player's cards including the folded ones, so on
    those hands this is the whole deal. ACR shows them at showdown only, and
    the difference is visible: a seat with no cards was not seen, rather than
    dealt nothing.
    """
    con.row_factory = sqlite3.Row
    h = con.execute("SELECT * FROM hands WHERE hand_id=?", (hand_id,)).fetchone()
    if h is None:
        return None
    seats = [dict(r) for r in con.execute(
        "SELECT * FROM seats WHERE hand_id=? ORDER BY seat", (hand_id,))]
    pots = {r["n"]: (r["pot_before"], r["to_call"], r["pot_bb"])
            for r in con.execute(
                "SELECT n, pot_before, to_call, pot_bb FROM decisions "
                "WHERE hand_id=?", (hand_id,))}
    by_seat = {r["seat"]: r for r in seats}

    streets, order = [], {"preflop": 0, "flop": 1, "turn": 2, "river": 3}
    board = (h["board"] or "").split()
    shown = {"preflop": "", "flop": " ".join(board[:3]),
             "turn": " ".join(board[:4]), "river": " ".join(board[:5])}
    for st in ("preflop", "flop", "turn", "river"):
        acts = [dict(r) for r in con.execute(
            "SELECT * FROM actions WHERE hand_id=? AND street=? ORDER BY n",
            (hand_id, st))]
        if not acts:
            continue
        lines = []
        for a in acts:
            pot, to_call, pot_bb = pots.get(a["n"], (None, None, None))
            who = by_seat.get(a["seat"], {})
            size = a["total"] if a["action"] in ("R", "A") and a["total"] \
                else a["amount"]
            lines.append({
                "seat": a["seat"], "position": a["position"],
                "name": who.get("label"), "is_hero": who.get("is_hero"),
                "verb": VERBS.get(a["action"], a["action"]),
                "action": a["action"], "amount": size,
                "pot_before": pot, "to_call": to_call, "pot_bb": pot_bb})
        streets.append({"street": st, "board": shown[st], "actions": lines})

    con.row_factory = None
    return {
        "hand_id": hand_id, "site": h["site"], "played_at": h["played_at"],
        "table": h["table_id"], "fmt": h["fmt"], "sb": h["sb"], "bb": h["bb"],
        "n_players": h["n_players"], "board": h["board"], "pot": h["pot"],
        "rake": (h["rake"] if "rake" in h.keys() else None),
        "focus": seat,
        "seats": [{"seat": r["seat"], "name": r["label"],
                   "position": r["position"], "stack": r["stack"],
                   "cards": r["cards"], "is_hero": r["is_hero"],
                   "won": r["won"], "put_in": (r["posted"] or 0) + (r["invested"] or 0)}
                  for r in seats],
        "streets": streets}


def show_hand(con, hand_id, seat=None):
    """The same hand, for a terminal."""
    d = hand_detail(con, hand_id, seat)
    if d is None:
        print(f"no hand {hand_id!r}")
        return
    stake = f"${d['sb']}/${d['bb']}" if d["bb"] else "-"
    print(f"\n{d['hand_id']}   {d['site']}  {d['fmt']}  {stake}  "
          f"{d['played_at']}  ({d['table']})")
    print("=" * 78)
    for s in d["seats"]:
        mark = "*" if s["seat"] == seat else (">" if s["is_hero"] else " ")
        net = (s["won"] or 0) - s["put_in"]
        print(f" {mark} {s['position'] or '?':4} {(s['name'] or '')[:16]:16} "
              f"{s['stack'] or 0:9.2f}  {s['cards'] or '--':>7}  "
              f"{net:+8.2f}")
    for st in d["streets"]:
        head = st["street"].upper()
        if st["board"]:
            head += f"  [{st['board']}]"
        first = st["actions"][0] if st["actions"] else None
        if first and first["pot_before"] is not None:
            head += f"   pot {first['pot_before']:.2f}"
        print(f"\n{head}")
        for a in st["actions"]:
            amt = f" {a['amount']:.2f}" if a["amount"] else ""
            print(f"    {a['position'] or '?':4} {a['verb']}{amt}")
    if d["pot"]:
        rake = f"  rake {d['rake']:.2f}" if d["rake"] else ""
        print(f"\nTOTAL POT {d['pot']:.2f}{rake}")


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


def select_into(con, pairs):
    """
    Park a set of (hand, seat) pairs in a temp table to join against.

    The index is not an optimisation, it is the difference between the graph
    taking half a second and taking half a minute: without it every join
    against this table is a scan, and the graph joins it to spots and hands
    for every one of eleven thousand rows.
    """
    con.execute("CREATE TEMP TABLE IF NOT EXISTS _sel (hand_id TEXT, seat INT)")
    con.execute("DELETE FROM _sel")
    con.executemany("INSERT INTO _sel VALUES (?,?)", pairs)
    con.execute("CREATE INDEX IF NOT EXISTS _sel_ix ON _sel(hand_id, seat)")
    con.execute("ANALYZE _sel")


def results_of(con, pairs):
    """The money over a set of (hand, seat) pairs, tournaments excluded."""
    select_into(con, pairs)
    n, net_bb, money, saw, wtsd, wwsf = con.execute(
        "SELECT COUNT(*), SUM(s.net_bb), SUM(s.won - s.put_in), "
        "       SUM(s.saw_flop), SUM(s.wtsd), SUM(s.wwsf) "
        "FROM spots s JOIN _sel ON _sel.hand_id = s.hand_id "
        "AND _sel.seat = s.seat WHERE s.fmt <> 'MTT'").fetchone()
    if not n:
        return None
    return {"hands": n, "net_bb": net_bb or 0.0, "money": money or 0.0,
            "saw_flop": saw or 0, "wtsd": wtsd or 0, "wwsf": wwsf or 0,
            "bb100": 100 * (net_bb or 0.0) / n,
            "error": 1170 / n ** 0.5}


def show_results(con, where, label, parts=()):
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
        print("nothing matches -- " + why_empty(con, parts))
        return
    got = results_of(con, pairs)
    if not got:
        print("nothing matches outside tournaments")
        return
    n, net_bb, money, saw, wtsd, wwsf = (
        got["hands"], got["net_bb"], got["money"], got["saw_flop"],
        got["wtsd"], got["wwsf"])
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


def show_hands(con, where, label, limit=40, parts=()):
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
    if not rows:
        print("  " + why_empty(con, parts))
        return
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
    print(f"    {'--quick':14} named filters: "
          f"{', '.join(sorted(QUICK_BY_KEY)[:6])}, ... (see --quick-list)")
    print(f"    {'--where':14} raw SQL over `decisions`, for anything above")
    print("\n  reports (not filters):")
    print(f"    {'--by':14} split into a table by one of: "
          f"{', '.join(DIMENSIONS)}")
    print(f"    {'--show':14} which stats are the columns "
          f"(default: {','.join(DEFAULT_COLUMNS)})")
    print(f"    {'--min':14} mark cells below this many chances (default 30)")
    print(f"    {'--hand':14} replay one hand by id, ignoring every filter")
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
        "--site": "acr", "--pos": "BTN", "--street": "flop",
        "--pot": "3bet", "--facing": "bet", "--combo": "AKs",
        "--stake": "0.1", "--deep": "50", "--short": "200",
        "--players": "6", "--live": "2",
        "--since": midpoint, "--until": midpoint,
    }
    cases = [(k, [k]) for k in SWITCHES]
    cases += [(k, [k, v]) for k, v in samples.items()]
    cases += [("--board " + b, ["--board", b]) for b in BOARDS]
    cases.append(("--where", ["--where", "eff_bb > 100"]))
    cases += [("--quick " + f["key"], ["--quick", f["key"]])
              for f in quick_filters()]
    cases.append(("--player", ["--player", con.execute(
        "SELECT player FROM decisions WHERE player IS NOT NULL LIMIT 1"
    ).fetchone()[0]]))

    for name, argv in cases:
        where, _, _p = build(argv)
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

    # A replayed hand must show every action the hand had. A viewer that
    # drops one is worse than no viewer: the reader sees a complete-looking
    # hand and reasons about a line that nobody took.
    sample = [r[0] for r in con.execute(
        "SELECT hand_id FROM hands WHERE game='HOLDEM' "
        "ORDER BY RANDOM() LIMIT 200")]
    lost = []
    for hid in sample:
        d = hand_detail(con, hid)
        shown = sum(len(st["actions"]) for st in d["streets"])
        real = con.execute(
            "SELECT COUNT(*) FROM actions WHERE hand_id=?", (hid,)).fetchone()[0]
        if shown != real:
            lost.append(f"{hid}: {shown} shown of {real}")
    print(f"replayed hands keep every action  {len(sample) - len(lost)}"
          f"/{len(sample)}")
    for x in lost[:4]:
        print(f"    {x}")
    if lost:
        fails.append("hand viewer drops actions")

    # The three modes must survive a filter that legitimately matches nothing,
    # since a user will type one within a day of being given the tool.
    empty, _, _ep = build(["--pos", "BTN", "--street", "preflop",
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
    if "--quick-list" in argv:
        group = None
        for f in quick_filters():
            if f["group"] != group:
                group = f["group"]
                print(f"\n[{group}]")
            print(f"  {f['key']:22} {f['label']}")
        return 0
    if "--check" in argv:
        return 0 if check() else 1
    mode = "--stats"
    for m in ("--stats", "--hands", "--results", "--graph"):
        if m in argv:
            mode = m
            argv = [a for a in argv if a != m]

    def opt(name, default=None):
        if name not in argv:
            return default
        i = argv.index(name) + 1
        if i >= len(argv):
            raise SystemExit(f"{name} needs a value")
        return argv[i]

    dim = opt("--by")
    if dim is not None and dim not in DIMENSIONS:
        raise SystemExit(f"unknown dimension {dim!r} -- "
                         f"one of: {', '.join(DIMENSIONS)}")
    columns = (opt("--show") or ",".join(DEFAULT_COLUMNS)).split(",")
    for c in columns:
        if c not in BY_KEY:
            raise SystemExit(f"unknown stat {c!r} -- see `stats.py --list`")
    min_n = int(opt("--min", "30"))

    if opt("--hand"):
        con = sqlite3.connect(DB)
        show_hand(con, opt("--hand"))
        con.close()
        return 0

    where, label, _parts = build(argv)
    con = sqlite3.connect(DB)
    if mode == "--graph":
        show_graph(con, where, label, opt("--out", "graph.html"))
    elif mode == "--hands":
        show_hands(con, where, label, parts=_parts)
    elif mode == "--results":
        if dim:
            show_results_by(con, where, label, dim)
        else:
            show_results(con, where, label, _parts)
    elif dim:
        show_report(con, where, label, dim, columns, min_n)
    else:
        show_stats(con, where, label, _parts)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
