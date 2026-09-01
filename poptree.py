"""
The population's own preflop tree: what this pool does, beside what is right.

GTO Wizard will show you a grid for every preflop spot. This builds the same
grid for the players actually sitting at these tables, and puts the two side
by side. That comparison is the whole point -- a solver chart tells you how to
play against opponents who do not exist, and the difference between the two
charts is where the money is.

It is possible only because of what Ignition gives away. Every player's hole
cards are in the hand history, including the ones they folded, so the pool's
range in a spot can be COUNTED rather than guessed at from the few hands that
reached showdown. Every other site's data leaves you inferring ranges from
biased samples; here the deal is simply on the table.

Each spot is described by how many raises stand in front of the player, which
is what makes a spot the spot it is:

    facing 0    nobody has raised    -- open or limp
    facing 1    one raise in front   -- 3-bet, flat or fold
    facing 2    a 3-bet in front     -- 4-bet, flat or fold

There are two different solver numbers here and they must not be confused,
because they differ by five points in the spot where it matters most:

  the spot frequency   what the solver does at this node across its whole
                       range. This is what a player means by "the solver
                       4-bets 26% here", and it is what the tables show.
  the hand-matched     what the solver would do with the exact hands this
                       pool held. A truer like-for-like comparison, but on a
                       few dozen observations it is noise, and reporting it
                       under the other's name overstated an SB 4-bet figure
                       as 31.4% when the spot answer is 26.0%.

Both are kept per combo; the width tables use the spot frequency.

    python poptree.py                the report
    python poptree.py --hero         hero's own tree instead of the pool's
    python poptree.py --grids        add the 13x13 charts and the difference
    python poptree.py --player ID    one seat, if it has enough hands
"""

import sqlite3
import sys
from pathlib import Path

from gtowizard import Solver
from walk import kind, walk

DB = Path(__file__).parent / "hands.db"
RANKS = "AKQJT98765432"

FACING = {0: "unopened pot -- open or limp",
          1: "facing an open -- 3-bet, flat or fold",
          2: "facing a 3-bet -- 4-bet, flat or fold",
          3: "facing a 4-bet"}

VERB = {0: ("open", "limp"), 1: ("3bet", "flat"),
        2: ("4bet", "flat"), 3: ("5bet", "flat")}

# A combo seen this few times is an anecdote. Grids blank below it, and the
# bluff hunt ignores it, because "they fold A5s 100%" off two hands is noise
# dressed as a read.
MIN_COMBO = 6


def combos():
    """The 169 hands, in chart order."""
    out = []
    for i, hi in enumerate(RANKS):
        for j, lo in enumerate(RANKS):
            out.append(hi + hi if i == j else
                       (hi + lo + "s" if i < j else lo + hi + "o"))
    return out


def collect(rows, facing, position=None, who=None, role=None):
    """
    Per combo, what the pool did here and what the solver would have.

    The solver figure is accumulated per observation rather than read off once
    per node, so it is weighted by how often the pool actually stood in each
    spot. A node the pool reached twice should not count as much as one they
    reached two hundred times.
    """
    cells = {}
    for r in rows:
        if r["facing"] != facing:
            continue
        if role and r.get("role") != role:
            continue
        # Facing an all-in is not a 4-bet spot: there is nothing to raise to.
        if facing >= 2 and not r.get("can_raise", True):
            continue
        if position and r["position"] != position:
            continue
        if who == "hero" and not r["is_hero"]:
            continue
        if who == "pool" and r["is_hero"]:
            continue
        if who and who not in ("hero", "pool") and r["player"] != who:
            continue
        c = cells.setdefault(r["combo"], {
            "n": 0, "raise": 0, "call": 0, "fold": 0,
            "s_raise": 0.0, "s_call": 0.0, "s_fold": 0.0,
            "node_raise": 0.0, "node_call": 0.0, "node_fold": 0.0})
        c["n"] += 1
        c[kind(r["chose"])] += 1
        for code, f in (r["solver"] or {}).items():
            c["s_" + kind(code)] += f or 0.0
        for code, f in (r.get("node_freq") or {}).items():
            c["node_" + kind(code)] += f or 0.0
    return cells


def widths(cells):
    """Overall frequencies, pool and solver, over every observation."""
    n = sum(c["n"] for c in cells.values())
    if not n:
        return None
    out = {"n": n}
    for k in ("raise", "call", "fold"):
        out[k] = 100.0 * sum(c[k] for c in cells.values()) / n
        out["s_" + k] = 100.0 * sum(c["s_" + k] for c in cells.values()) / n
        out["node_" + k] = 100.0 * sum(c["node_" + k] for c in cells.values()) / n
    return out


def print_widths(rows, who, positions, dropped=None):
    """
    The width tables, split by the thing that changes the spot.

    Facing a 3-bet is two different spots depending on who you are. The
    player who opened may flat it; a cold seat behind them may not -- the
    solved tree offers them fold or 4-bet and nothing else. Reported as one
    row, the cold seats drag the flat column to zero and it reads as a fact
    about the pool rather than a fact about the tree.
    """
    for facing, role in ((0, None), (1, None), (2, "opener"), (2, "cold")):
        print("\n" + "=" * 74)
        title = FACING[facing].upper()
        if role:
            title += "  [{}]".format("as the opener" if role == "opener"
                                     else "cold, not the opener")
        print("{}   ({})".format(title, who))
        print("=" * 74)
        if facing == 2 and role == "cold":
            print("The solved tree gives a cold seat facing a 3-bet only fold")
            print("or 4-bet, so the flat column is empty by construction --")
            print("not because nobody calls.")
        if dropped and dropped.get(facing):
            print("{} decisions at this depth took an action the tree does "
                  "not have,\nand were dropped rather than counted."
                  .format(dropped[facing]))
        raise_word, call_word = VERB[facing]
        print("{:5} {:>6}   {:>7} {:>7} {:>6}   {:>7} {:>7} {:>6}".format(
            "pos", "n", raise_word, "solver", "diff", call_word, "solver", "diff"))
        print("{:5} {:>6}   {:>7} {:>7} {:>6}   {:>7} {:>7} {:>6}"
              .format("", "", "", "(spot)", "", "", "(spot)", ""))
        for pos in positions:
            w = widths(collect(rows, facing, pos, who, role))
            if not w or w["n"] < 40:
                continue
            print("{:5} {:6d}   {:6.1f}% {:6.1f}% {:+6.1f}   "
                  "{:6.1f}% {:6.1f}% {:+6.1f}".format(
                      pos, w["n"], w["raise"], w["node_raise"],
                      w["raise"] - w["node_raise"],
                      w["call"], w["node_call"], w["call"] - w["node_call"]))
        w = widths(collect(rows, facing, None, who, role))
        if w:
            print("{:5} {:6d}   {:6.1f}% {:6.1f}% {:+6.1f}   "
                  "{:6.1f}% {:6.1f}% {:+6.1f}".format(
                      "ALL", w["n"], w["raise"], w["node_raise"],
                      w["raise"] - w["node_raise"], w["call"], w["node_call"],
                      w["call"] - w["node_call"]))


def print_grid(cells, field, solver=False, min_n=MIN_COMBO):
    """The 13x13 chart: suited above the diagonal, offsuit below."""
    print("      " + " ".join("{:>3}".format(r) for r in RANKS))
    for i, hi in enumerate(RANKS):
        row = []
        for j, lo in enumerate(RANKS):
            combo = (hi + hi if i == j else
                     (hi + lo + "s" if i < j else lo + hi + "o"))
            c = cells.get(combo)
            if not c or c["n"] < min_n:
                row.append("  .")
            else:
                v = c[("s_" if solver else "") + field] / c["n"]
                row.append("{:>3.0f}".format(100 * v))
        print("  {:>2}  {}".format(hi, " ".join(row)))


def print_diff(cells, field, min_n=MIN_COMBO):
    """Pool minus solver, in points. Negative means the pool does it too little."""
    print("      " + " ".join("{:>3}".format(r) for r in RANKS))
    for i, hi in enumerate(RANKS):
        row = []
        for j, lo in enumerate(RANKS):
            combo = (hi + hi if i == j else
                     (hi + lo + "s" if i < j else lo + hi + "o"))
            c = cells.get(combo)
            if not c or c["n"] < min_n:
                row.append("   .")
            else:
                d = 100 * (c[field] - c["s_" + field]) / c["n"]
                row.append("{:>+4.0f}".format(d))
        print("  {:>2} {}".format(hi, " ".join(row)))


def missing_bluffs(cells, min_n=MIN_COMBO, gap=0.25):
    """
    Hands the solver raises and this pool will not.

    These are the exploitable half of a range: if the pool never 3-bets a
    hand the solver 3-bets a quarter of the time, their 3-bets are that much
    more honest than they should be, and every one of them can be believed.
    """
    out = []
    for combo, c in cells.items():
        if c["n"] < min_n:
            continue
        pool, sol = c["raise"] / c["n"], c["s_raise"] / c["n"]
        if sol - pool >= gap:
            out.append((combo, c["n"], 100 * pool, 100 * sol))
    return sorted(out, key=lambda r: -(r[3] - r[2]))


def overplayed(cells, min_n=MIN_COMBO, gap=0.20):
    """Hands this pool raises that the solver will not."""
    out = []
    for combo, c in cells.items():
        if c["n"] < min_n:
            continue
        pool, sol = c["raise"] / c["n"], c["s_raise"] / c["n"]
        if pool - sol >= gap:
            out.append((combo, c["n"], 100 * pool, 100 * sol))
    return sorted(out, key=lambda r: -(r[2] - r[3]))


def report(who="pool", show_grids=False):
    con = sqlite3.connect(str(DB))
    with Solver() as gto:
        print("{} solver nodes cached".format(len(gto.cached())))
        rows, tally, wanted = walk(con, gto, fetch=False)
    con.close()

    print("{} decisions placed in the tree, {} hands, {} lost to uncached "
          "nodes".format(tally["priced"], tally["hands"], tally["no_node"]))
    if wanted:
        print("({} nodes still wanted -- python leaks.py --wanted, then "
              "python gtow.py wanted)".format(len(wanted)))

    print_widths(rows, who, ("UTG", "HJ", "CO", "BTN", "SB", "BB"),
                 tally.get("dropped_by_facing"))

    for facing, label in ((1, "3-BET"), (0, "OPEN"), (2, "4-BET")):
        cells = collect(rows, facing, None, who)
        miss = missing_bluffs(cells)
        over = overplayed(cells)
        if not miss and not over:
            continue
        print("\n" + "=" * 74)
        print("{} RANGE -- where this pool differs hand by hand".format(label))
        print("=" * 74)
        if miss:
            print("\nhands the solver {}s and they do NOT (their {}s are "
                  "honest):".format(label.lower(), label.lower()))
            print("  {:6} {:>5} {:>8} {:>8}".format("hand", "n", "them", "solver"))
            for combo, n, pool, sol in miss[:18]:
                print("  {:6} {:5d} {:7.0f}% {:7.0f}%".format(combo, n, pool, sol))
        if over:
            print("\nhands they {} that the solver will not:".format(label.lower()))
            print("  {:6} {:>5} {:>8} {:>8}".format("hand", "n", "them", "solver"))
            for combo, n, pool, sol in over[:12]:
                print("  {:6} {:5d} {:7.0f}% {:7.0f}%".format(combo, n, pool, sol))

    if show_grids:
        for facing, label in ((1, "3-bet"), (0, "open")):
            cells = collect(rows, facing, None, who)
            print("\n" + "=" * 74)
            print("{} FREQUENCY -- {} (%)".format(label.upper(), who))
            print("=" * 74)
            print_grid(cells, "raise")
            print("\nthe solver, at the same spots with the same hands (%)")
            print_grid(cells, "raise", solver=True)
            print("\nthem minus the solver, in points "
                  "(negative = they do it too little)")
            print_diff(cells, "raise")


if __name__ == "__main__":
    who = "pool"
    if "--hero" in sys.argv:
        who = "hero"
    if "--player" in sys.argv:
        who = sys.argv[sys.argv.index("--player") + 1]
    report(who, "--grids" in sys.argv)
