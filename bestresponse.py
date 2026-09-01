"""
The chart the solver will not give you: how to play against THIS pool.

A solver answers "what is unexploitable against an opponent who is also
unexploitable". That is not the question. These opponents are not
unexploitable and we have measured exactly how, so the useful question is
"what makes the most money against the players actually sitting here" -- and
the answer is a different chart.

The important thing is that this needs no solving. Solving is expensive
because both players keep adjusting to each other. When the opponent's
strategy is FIXED -- and ours is, because it was counted rather than assumed
-- the best response is a single walk: at every node, take the action with
the highest EV against their measured frequencies. No iteration, no
convergence, no CFR.

What makes it honest is the decomposition. The solver's EV for an action
already contains a fold-equity term and a play-on term, mixed together:

    EV_gto(3bet) = P_gto(they fold) * pot + (1 - P_gto(they fold)) * V

V, the value of the hand once they continue, is the part we cannot measure
and do not try to. So it is recovered from the solver's own numbers --

    V = (EV_gto(3bet) - P_gto(fold) * pot) / (1 - P_gto(fold))

-- and then recombined with the POOL's measured fold frequency instead of
the solver's. The fold-equity half is measured; the play-on half is
borrowed. That borrowing is the one assumption in this file, and it is
stated in the output rather than buried here.

Version one covers a single spot: hero facing one open, which is where the
pool data is thickest (4,499 decisions). Deeper spots need the pot rebuilt
through multiple betting rounds and the samples are thinner.

    python bestresponse.py            the exploitative chart
    python bestresponse.py --ev       show the EV behind each decision
"""

import sqlite3
import sys
from pathlib import Path

from gtowizard import Solver
from walk import kind, walk

DB = Path(__file__).parent / "hands.db"
RANKS = "AKQJT98765432"
ORDER = ["UTG", "HJ", "CO", "BTN", "SB", "BB"]

# Below this many observations the pool's frequency at a node is not a
# measurement and the whole exercise is just the solver with noise on top.
MIN_NODE = 25

# How far the bets leading to a node may have been bent to fit the solver's
# sizings before the observation is thrown out.
#
# This is not a nicety. Folding to a 3-bet is enormously size-elastic: this
# pool folds to a 7bb 3-bet 22% of the time and to a 12bb one 69%. Pooling
# both into the node that models a 13.5bb 3-bet says they fold 49% and makes
# a disciplined pool look like a station -- which is how "they under-fold, so
# 3-bet less" got reported when at matched sizes they fold 69.1% against a
# 70.7% baseline.
#
# 25% rather than something tighter, because the two mismatches are not
# comparable. The tree opens for 2.5bb and a fifth of this pool opens for
# 3.0bb -- a 20% gap that moves the pot by half a blind. The 3-bet gap runs
# from 7bb to 15bb against a 13.5bb node and moves the fold rate by nearly
# fifty points. Tightening below 20% throws away every 3bb open, which is
# most of the data, to fix the smaller of the two errors.
MAX_PATH_GAP = 0.25


def pot_before(node):
    """
    Chips in the middle when the player at this node acts, in big blinds.

    Only sound while the action is still in its first orbit -- every code in
    the sequence belongs to the next seat round the table -- which is why
    this file sticks to spots where one raise has gone in. Past that the pot
    has to be replayed properly, as `spots.py` does for real hands.
    """
    put = {p: 0.0 for p in ORDER}
    put["SB"], put["BB"] = 0.5, 1.0
    for i, code in enumerate([c for c in node.split("-") if c]):
        if i >= len(ORDER):
            return None                  # second orbit: not our case
        seat = ORDER[i]
        if code == "F":
            continue
        if code == "C":
            put[seat] = max(put.values())
        elif code.startswith("R"):
            try:
                put[seat] = float(code[1:])
            except ValueError:
                return None              # RAI: size depends on the stack
    return sum(put.values())


def already_in(position):
    return {"SB": 0.5, "BB": 1.0}.get(position, 0.0)


def pool_response(rows, node):
    """
    What the pool does at a node, from the hands that reached it.

    Fold, call and raise only -- the sizes they choose are somebody else's
    problem; what matters for fold equity is whether the hand ends.
    """
    got = {"fold": 0, "call": 0, "raise": 0}
    for r in rows:
        if r["node"] != node or r["is_hero"]:
            continue
        if (r.get("path_gap") or 0) > MAX_PATH_GAP:
            continue          # they were facing a different bet than we model
        got[kind(r["chose"])] += 1
    n = sum(got.values())
    if not n:
        return None, 0
    return {k: v / n for k, v in got.items()}, n


def fold_through(rows, gto, start):
    """
    The chance EVERY remaining player folds, pool and solver, from here.

    Raising does not win the pot when the next player folds -- it wins when
    the last of them does. When hero 3-bets a button open from the small
    blind, the big blind acts first and the button after; reading the fold
    rate of whoever happens to be next and calling it fold equity credits
    hero with a pot that is still contested, and the best response that
    follows says to 3-bet thirty-two offsuit.

    Where the pool has been seen often enough, its own fold rate is used.
    Where it has not, the solver's stands in rather than the whole spot being
    thrown away -- and the count of links actually measured comes back, so a
    chart resting mostly on the solver cannot be mistaken for one resting on
    the data.
    """
    p_pool, p_gto, worst_n = 1.0, 1.0, None
    key, seats, measured = start, 0, 0
    while True:
        node = gto.node(key, fetch=False)
        if node is None:
            return None, None, 0, seats, measured
        gto_fold = sum(a["freq"] or 0 for a in node.actions if a["code"] == "F")
        pool, n = pool_response(rows, key)
        if pool is not None and n >= MIN_NODE:
            p_pool *= pool["fold"]
            measured += 1
            worst_n = n if worst_n is None else min(worst_n, n)
        else:
            p_pool *= gto_fold          # unmeasured: assume they play it right
        p_gto *= gto_fold
        seats += 1
        nxt = node.child("F")
        if nxt is None:
            return p_pool, p_gto, (worst_n or 0), seats, measured
        key = nxt


def exploit(hero_node, hero, rows, gto, show_ev=False):
    """The best action per combo at one spot, against the measured pool."""
    node = gto.node(hero_node, fetch=False)
    if node is None:
        return None
    pot = pot_before(hero_node)
    if pot is None:
        return None
    mine = already_in(node.hero)

    # The raise hero could make here, and the node it leads to.
    raises = [a for a in node.actions
              if a["type"] == "RAISE" and a["code"] != "RAI"]
    if not raises:
        return None
    rz = raises[0]["code"]
    after = node.child(rz)
    if after is None:
        return None
    pool_fold, gto_fold, n_pool, seats, measured = fold_through(rows, gto, after)
    if pool_fold is None or measured == 0:
        return None      # nothing about this spot came from the data at all

    out = {}
    for combo in node.order:
        ev = node.ev(combo)
        if ev.get(rz) is None:
            continue
        # Recover the value of playing on from the solver's own mixture,
        # then recombine it with the pool's fold frequency.
        if gto_fold >= 0.999:
            continue
        played_on = (ev[rz] - gto_fold * pot) / (1.0 - gto_fold)
        ev_raise = pool_fold * pot + (1.0 - pool_fold) * played_on
        ev_call = ev.get("C")
        options = {"fold": 0.0, rz: ev_raise}
        if ev_call is not None:
            options["call"] = ev_call
        best = max(options, key=options.get)
        out[combo] = {
            "best": best, "ev": options,
            "gto": node.best(combo), "gto_ev_raise": ev[rz],
            "gain": options[best] - (ev.get(node.best(combo)) or 0.0),
        }
    return {"pot": pot, "raise": rz, "after": after, "pool_fold": pool_fold,
            "n_pool": n_pool, "gto_fold": gto_fold, "hero": node.hero,
            "seats": seats, "measured": measured, "combos": out}


def grid(res, field="best"):
    """The 13x13 chart of what to do."""
    mark = {"fold": " . ", "call": " c "}
    print("      " + " ".join("{:>3}".format(r) for r in RANKS))
    for i, hi in enumerate(RANKS):
        row = []
        for j, lo in enumerate(RANKS):
            combo = (hi + hi if i == j else
                     (hi + lo + "s" if i < j else lo + hi + "o"))
            c = res["combos"].get(combo)
            if not c:
                row.append("  ?")
            else:
                row.append(mark.get(c[field], " R "))
        print("  {:>2}  {}".format(hi, "".join(row)))
    print("      R = raise    c = call    . = fold")


def spot_label(key):
    """Who opened, for how much, and who is now deciding."""
    opener, open_size = None, "?"
    for i, code in enumerate([c for c in key.split("-") if c]):
        if code.startswith("R"):
            opener, open_size = ORDER[i], code.lstrip("R")
    return opener, open_size


def report(show_ev=False):
    con = sqlite3.connect(str(DB))
    with Solver() as gto:
        rows, tally, _ = walk(con, gto, fetch=False)

        found = []
        skipped = 0
        for key in sorted(gto.cached(), key=len):
            if sum(1 for p in key.split("-") if p.startswith("R")) != 1:
                continue
            if len(key.split("-")) > 6:
                continue
            res = exploit(key, None, rows, gto, show_ev)
            if res:
                res["key"] = key
                found.append(res)
            else:
                skipped += 1

        print("=" * 78)
        print("YOUR SPOTS -- what happens when you 3-bet, at the size you use")
        print("=" * 78)
        print("Fold equity is measured where this pool has been seen enough")
        print("({}+ decisions at a node); the solver stands in for the rest, and".format(MIN_NODE))
        print("the 'links' column says how much of each figure is really yours.")
        print("Observations whose bet sizes were more than {:.0f}% off the node"
              .format(100 * MAX_PATH_GAP))
        print("are discarded -- folding to a 3-bet is highly size-elastic.\n")

        print("{:4} {:>4} {:>6}  {:>6} {:>8} {:>8} {:>7} {:>6} {:>6}".format(
            "you", "vs", "open", "3bet", "they fold", "solver", "edge",
            "n", "links"))
        for r in sorted(found, key=lambda r: -(r["pool_fold"] - r["gto_fold"])):
            opener, open_size = spot_label(r["key"])
            print("{:4} {:>4} {:>5}bb {:>5}bb {:>8.0f}% {:>7.0f}% {:>+6.0f} "
                  "{:>6} {:>3}/{}".format(
                      r["hero"], opener or "?", open_size,
                      r["raise"].lstrip("R"), 100 * r["pool_fold"],
                      100 * r["gto_fold"],
                      100 * (r["pool_fold"] - r["gto_fold"]),
                      r["n_pool"] or "-", r["measured"], r["seats"]))
        print("\n{} spot(s) with at least one measured link; {} with none."
              .format(len(found), skipped))

        for r in sorted(found, key=lambda r: -abs(r["pool_fold"] - r["gto_fold"])):
            changed = [c for c, v in r["combos"].items()
                       if (v["best"] == r["raise"]) != (v["gto"] == r["raise"])]
            if not changed:
                continue
            opener, open_size = spot_label(r["key"])
            print("\n" + "-" * 78)
            print("{} 3-betting to {}bb over a {}bb {} open   "
                  "(pot {:.1f}bb, {} to act)".format(
                      r["hero"], r["raise"].lstrip("R"), open_size, opener,
                      r["pot"], r["seats"]))
            print("  they all fold {:.0f}% vs the solver's {:.0f}%   "
                  "({} of {} links measured, thinnest n={})".format(
                      100 * r["pool_fold"], 100 * r["gto_fold"],
                      r["measured"], r["seats"], r["n_pool"]))
            print("  {} of {} hands change\n".format(len(changed), len(r["combos"])))
            if len(changed) > 0.5 * max(len(r["combos"]), 1):
                print("  !! more than half the chart moved -- treat as broken\n")
            grid(r)
            more = sorted(c for c in changed
                          if r["combos"][c]["best"] == r["raise"])
            less = sorted(c for c in changed
                          if r["combos"][c]["best"] != r["raise"])
            if more:
                print("\n3-bet these MORE than the solver: " + " ".join(more[:24]))
            if less:
                print("\n3-bet these LESS than the solver: " + " ".join(less[:24]))
    con.close()


if __name__ == "__main__":
    report("--ev" in sys.argv)
