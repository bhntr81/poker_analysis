"""
What hero's preflop decisions cost, measured against the solver.

Results cannot answer this. A fold that was right loses the pot as surely as
a fold that was wrong, and over a few thousand hands the noise swamps any
real edge -- the pool leak map carried error bars of +/-117bb/100 at n=100.
So nothing here looks at what happened. Each decision is looked up in the
solved game, and its cost is the gap between the best action's EV and the EV
of the action hero actually took. That number is exact, has no variance, and
is worth reading on the very first hand.

Three things keep the mapping honest rather than flattering:

  * Real players do not use solver sizings. The pool opens 2.5bb and 3.0bb;
    the node offers R2.5. Hero's action is snapped to the nearest sizing the
    solver has, and how far it had to move is measured and reported.
  * Some hands do not map at all. A solved 6-max game has no limp, so a
    limped pot has no node. Those are counted and shown -- a leak report that
    quietly scores only the convenient hands is worse than no report.
  * Loss can never be negative. Hero cannot beat the solver at a solved node,
    so a negative figure means the mapping is broken, not that hero found
    something. It is checked rather than assumed.

A short table is the same tree with the early seats folded: five handed is
the node "F", four handed "F-F". That falls out of naming short tables by
their late positions, and it is checked against the seat the solver says is
to act rather than trusted.

    python leaks.py            the report
    python leaks.py --wanted   record the nodes these hands need but lack
"""

import sqlite3
import sys
from pathlib import Path

from gtowizard import Solver
from walk import walk

DB = Path(__file__).parent / "hands.db"

PREFLOP_VIEW = """
DROP VIEW IF EXISTS bets_pf;
CREATE VIEW bets_pf AS
  SELECT a.hand_id, a.n, a.seat, a.position, a.action, a.amount, a.total,
         COALESCE(b.to_call, 0) AS to_call
  FROM actions a LEFT JOIN bets b ON b.hand_id=a.hand_id AND b.n=a.n
  WHERE a.street='preflop';
"""


def report(list_wanted=False):
    con = sqlite3.connect(str(DB))
    con.executescript(PREFLOP_VIEW)

    with Solver() as gto:
        print("{} nodes cached\n".format(len(gto.cached())))
        rows, tally, wanted = walk(con, gto, fetch=False,
                                   record_wanted=list_wanted)
        rows = [r for r in rows if r["is_hero"] and r["loss"] is not None]

        if list_wanted:
            print("nodes these hands need but lack ({}):".format(len(wanted)))
            for key, hits in sorted(wanted.items(), key=lambda kv: -kv[1])[:30]:
                print("  {:6d}  {}".format(hits, key or "(root)"))
            print("\nrecorded -- fetch them with:  python gtow.py wanted")
            con.close()
            return

    dec = tally["hero_decisions"]
    print("=" * 68)
    print("COVERAGE -- how much of hero's play could be priced at all")
    print("=" * 68)
    print("  hands with hero            {:6d}".format(tally["hands"]))
    print("  hero preflop decisions     {:6d}".format(dec))
    print("  priced against the solver  {:6d}   {:.1f}%".format(
        tally["hero_priced"], 100.0 * tally["hero_priced"] / max(dec, 1)))
    print("  lost: node not cached      {:6d}".format(tally["hero_no_node"]))
    print("  lost: no such action       {:6d}   (limps, mostly)".format(
        tally["hero_no_action"]))
    print("  lost: table too big        {:6d}".format(tally["too_many"]))
    print("  lost: tree disagreed       {:6d}".format(tally["wrong_seat"]))

    if not rows:
        print("\nNothing could be priced yet.")
        con.close()
        return

    # A decision is only as trustworthy as the path to it. If the actions in
    # front of the player had to be bent a long way to fit the solver's
    # sizings -- a min-3-bet priced as a full one -- the node is not the spot
    # they were really in, and its EV belongs to a different hand. Those are
    # kept in the totals and kept OUT of the rankings, because the worst-fit
    # decisions are exactly the ones that float to the top of a leak table.
    solid = [r for r in rows
             if (r.get("path_gap") or 0) <= 0.2 and (r.get("size_gap") or 0) <= 0.2]
    neg = [r for r in rows if r["loss"] < -1e-6]
    print("\n" + "=" * 68)
    print("SOUNDNESS -- hero cannot beat the solver")
    print("=" * 68)
    print("  decisions priced as a GAIN {:6d}   {}".format(
        len(neg), "OK" if not neg else "BROKEN -- the mapping is wrong"))

    total = sum(r["loss"] for r in rows)
    clean = sum(1 for r in rows if r["loss"] < 0.01)
    print("\n" + "=" * 68)
    print("WHAT IT COST")
    print("=" * 68)
    print("  total EV lost preflop  {:8.1f} bb over {} decisions".format(
        total, len(rows)))
    print("  per 100 hands          {:8.1f} bb/100".format(
        100.0 * total / max(tally["hands"], 1)))
    print("  decisions costing 0    {:8d}   ({:.1f}% played fine)".format(
        clean, 100.0 * clean / len(rows)))

    print("\n" + "=" * 68)
    print("BIGGEST LEAKS -- grouped by seat, what hero did, what was better")
    print("=" * 68)
    print("Only decisions the tree fits well: {} of {}. The rest are counted"
          .format(len(solid), len(rows)))
    print("in the totals above but not ranked here.\n")
    by = {}
    for r in solid:
        g = by.setdefault((r["position"], r["chose"], r["best"]),
                          {"n": 0, "loss": 0.0, "eg": []})
        g["n"] += 1
        g["loss"] += r["loss"]
        if r["loss"] > 0.5:
            g["eg"].append(r)
    print("{:5} {:>6} {:>7} {:>6} {:>9} {:>7}  {}".format(
        "pos", "did", "better", "n", "bb lost", "each", "worst hands"))
    for (pos, chose, best), g in sorted(by.items(), key=lambda kv: -kv[1]["loss"])[:15]:
        if g["loss"] < 0.5:
            continue
        eg = sorted(g["eg"], key=lambda r: -r["loss"])[:4]
        print("{:5} {:>6} {:>7} {:6d} {:9.1f} {:7.2f}  {}".format(
            pos, chose, best, g["n"], g["loss"], g["loss"] / g["n"],
            " ".join("{}({:.1f})".format(r["combo"], r["loss"]) for r in eg)))

    print("\n" + "=" * 68)
    print("WORST SINGLE DECISIONS")
    print("=" * 68)
    print("{:5} {:6} {:>7} {:>7} {:>8}  {}".format(
        "pos", "combo", "did", "better", "bb lost", "spot"))
    for r in sorted(solid, key=lambda r: -r["loss"])[:15]:
        print("{:5} {:6} {:>7} {:>7} {:8.2f}  {}".format(
            r["position"], r["combo"], r["chose"], r["best"], r["loss"],
            r["node"] or "(first to act)"))

    fits = [r["size_gap"] for r in rows if r["size_gap"]]
    if fits:
        print("\nsizing fit: {} raises snapped, {:.0f}% landed within 20% of a "
              "size the solver actually offers".format(
                  len(fits), 100.0 * sum(1 for g in fits if g <= 0.2) / len(fits)))
    con.close()


if __name__ == "__main__":
    report(list_wanted="--wanted" in sys.argv)
