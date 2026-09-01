"""
What hero's postflop decisions cost, measured against the solver.

Preflop turned out to be nearly clean -- -1.3 bb/100, with 92.5% of decisions
costing exactly nothing. Only a fifth of hands see a flop, but that is where
the pots are large, so if hero's money is going anywhere it is going here.

The method is the same one that worked preflop, and it is worth restating why
it is worth the trouble. Hero's actual result over 3,802 hands is +1.0 bb/100
with an error of +/-19: unreadable, and it will stay unreadable for tens of
thousands of hands. The EV difference between the action hero took and the
best one available is exact and has no variance at all. Measuring against a
solver is about twenty times more powerful than measuring against results.

Postflop differs from preflop in three ways that matter:

  * **The board is a fact about the hand, not the solution.** A node knows an
    action turned a card; it cannot know which. The runout comes from the
    hand history, which is what `next_street` is for.
  * **Hands are combinations, not classes.** The strategy arrays are 1326
    long here. On a two-heart flop Th9h and Ts9s are different hands worth
    different money, and hero's exact cards are known, so the exact
    combination is what gets looked up.
  * **EV is chips expected from the pot**, not a difference from folding.
    Actions at one node are still comparable to each other, which is all a
    leak needs, but the numbers are not differences from anything.

    python postflop.py --fetch    walk hero's hands, fetching what is missing
    python postflop.py            score what is already cached
"""

import sqlite3
import sys
from pathlib import Path

from gtowizard import Solver, start_node
from gtowizard.solver import canon_board, cards
from walk import PREFLOP_VIEW, snap

DB = Path(__file__).parent / "hands.db"

# Past this the bets leading to a spot have been bent so far to fit the
# solver's sizings that the node is somebody else's hand.
MAX_PATH_GAP = 0.25

STREETS = ("flop", "turn", "river")


def hand_rows(con):
    """Hero's hands that saw a flop, with the board and everyone's actions."""
    con.executescript(PREFLOP_VIEW)
    hero = {}
    for hid, seat, cardstr, bb, n, board in con.execute(
            "SELECT s.hand_id, s.seat, s.cards, s.bb, s.n_players, h.board "
            "FROM spots s JOIN hands h USING(hand_id) "
            "WHERE s.is_hero=1 AND s.saw_flop=1 AND s.fmt IN ('RING','ZONE') "
            "AND s.bb IS NOT NULL AND h.standard=1 AND s.cards IS NOT NULL "
            "AND h.board!=''"):
        hero[hid] = {"seat": seat, "cards": cardstr, "bb": bb,
                     "n_players": n, "board": board}
    acts = {}
    for r in con.execute(
            "SELECT a.hand_id, a.n, a.seat, a.position, a.street, a.action, "
            "a.amount, a.total, COALESCE(b.to_call,0) "
            "FROM actions a LEFT JOIN bets b "
            "ON b.hand_id=a.hand_id AND b.n=a.n "
            "ORDER BY a.hand_id, a.n"):
        acts.setdefault(r[0], []).append(
            {"n": r[1], "seat": r[2], "position": r[3], "street": r[4],
             "action": r[5], "amount": r[6], "total": r[7], "to_call": r[8]})
    return hero, acts


def board_to(board, street):
    """The cards that are face up on a given street."""
    c = cards(board.replace(" ", ""))
    want = {"flop": 3, "turn": 4, "river": 5}[street]
    return "".join(c[:want]) if len(c) >= want else None


def price_hand(gto, me, actions, fetch):
    """
    Walk one hand down the tree and price every decision hero made.

    The walk stops the moment the hand leaves the tree. That is deliberate:
    once the path diverges, every node after it belongs to a different hand,
    and pricing decisions against them would be worse than not pricing them.
    """
    out, missing = [], []
    key = start_node(me["n_players"])
    if key is None:
        return out, missing, "table too big"
    spot = (key, "", "", "", "")
    path_gap = 0.0

    for a in actions:
        # Moving to a new street needs the real card, which only the hand knows.
        if a["street"] != "preflop":
            board = board_to(me["board"], a["street"])
            if board is None:
                return out, missing, "board too short"
            if canon_board(board) != spot[1]:
                spot = (spot[0], canon_board(board), spot[2], spot[3], spot[4])

        node = gto.node(spot, fetch=fetch)
        if node is None:
            missing.append(spot)
            return out, missing, "no node"
        if node.hero and a["position"] != node.hero:
            return out, missing, "tree disagreed"

        mine = a["seat"] == me["seat"]
        code, gap = snap(a, node, me["bb"])
        if code is None:
            return out, missing, "no such action"

        if mine and path_gap <= MAX_PATH_GAP:
            ev = node.ev(me["cards"])
            live = {k: v for k, v in ev.items() if v is not None}
            if live and ev.get(code) is not None:
                out.append({
                    "hand_id": a["n"] and me.get("hand_id"), "street": a["street"],
                    "spot": spot, "cards": me["cards"], "chose": code,
                    "best": max(live, key=live.get),
                    "loss": max(live.values()) - ev[code],
                    "pot_ceiling": max(live.values()),
                    "position": a["position"], "path_gap": path_gap})

        path_gap = max(path_gap, gap or 0.0)
        nxt = node.child(code)
        if nxt is None:
            # Either the hand ended, or a card is about to come. The loop's
            # next action carries the street, so the board is applied there.
            act = node.action(code)
            if act is None or act["ends_hand"]:
                return out, missing, "hand ended"
            street_i = len(cards(spot[1]))
            field = {0: 0, 3: 2, 4: 3, 5: 4}.get(street_i)
            if field is None:
                return out, missing, "odd board"
            parts = list(spot)
            parts[field] = (parts[field] + "-" + code) if parts[field] else code
            spot = tuple(parts)
        else:
            spot = nxt
    return out, missing, "ok"


def run(fetch=False, limit=None):
    con = sqlite3.connect(str(DB))
    hero, acts = hand_rows(con)
    print("hero hands that saw a flop: {}".format(len(hero)))

    priced, why = [], {}
    # Headless while fetching: the profile is already logged in, and a
    # visible window is memory this machine does not have to spare.
    with Solver(headless=fetch) as gto:
        before = len(gto.cache.keys(gto.gametype, gto.depth))
        for i, (hid, me) in enumerate(sorted(hero.items())):
            if limit and i >= limit:
                break
            me = dict(me, hand_id=hid)
            rows, missing, reason = price_hand(gto, me, acts.get(hid, []), fetch)
            priced += rows
            why[reason] = why.get(reason, 0) + 1
            if fetch and i and i % 25 == 0:
                print("  {}/{} hands, {} decisions priced, {} spots cached"
                      .format(i, len(hero), len(priced),
                              len(gto.cache.keys(gto.gametype, gto.depth))),
                      flush=True)
        after = len(gto.cache.keys(gto.gametype, gto.depth))
    con.close()

    print("\nwhere each hand's walk ended:")
    for reason, n in sorted(why.items(), key=lambda kv: -kv[1]):
        print("   {:16} {}".format(reason, n))
    if fetch:
        print("\nspots cached: {} -> {}".format(before, after))

    if not priced:
        print("\nNothing priced yet. Run: python postflop.py --fetch")
        return

    # A leak cannot be worth more than the pot it happened in. This is the
    # check that would have caught the 1326-vs-169 misalignment on its own.
    absurd = [r for r in priced if r["loss"] > r["pot_ceiling"] + 1e-6]
    negative = [r for r in priced if r["loss"] < -1e-6]
    print("\n" + "=" * 66)
    print("SOUNDNESS")
    print("=" * 66)
    print("  decisions priced           {:6d}".format(len(priced)))
    print("  losses larger than the pot {:6d}   {}".format(
        len(absurd), "OK" if not absurd else "BROKEN"))
    print("  losses below zero          {:6d}   {}".format(
        len(negative), "OK" if not negative else "BROKEN"))

    total = sum(r["loss"] for r in priced)
    hands = len({r["hand_id"] for r in priced})
    clean = sum(1 for r in priced if r["loss"] < 0.01)
    print("\n" + "=" * 66)
    print("WHAT POSTFLOP COST")
    print("=" * 66)
    print("  total EV lost   {:9.1f} bb over {} decisions in {} hands"
          .format(total, len(priced), hands))
    print("  per 100 hands   {:9.1f} bb/100   (preflop was -1.3)".format(
        100.0 * total / max(hands, 1)))
    print("  decisions costing nothing {:5d}  ({:.0f}%)".format(
        clean, 100.0 * clean / len(priced)))

    print("\nby street:")
    for st in STREETS:
        sel = [r for r in priced if r["street"] == st]
        if sel:
            print("   {:6} {:5d} decisions  {:8.1f} bb lost  {:6.2f} each"
                  .format(st, len(sel), sum(r["loss"] for r in sel),
                          sum(r["loss"] for r in sel) / len(sel)))

    print("\n" + "=" * 66)
    print("WORST DECISIONS")
    print("=" * 66)
    print("{:6} {:6} {:>7} {:>7} {:>8}  {}".format(
        "street", "hand", "did", "better", "bb lost", "spot"))
    for r in sorted(priced, key=lambda r: -r["loss"])[:20]:
        pre, board, f, t, rv = r["spot"]
        line = " ".join(x for x in (board, f, t, rv) if x)
        print("{:6} {:6} {:>7} {:>7} {:8.2f}  {}".format(
            r["street"], r["cards"].replace(" ", ""), r["chose"], r["best"],
            r["loss"], line[:44]))


if __name__ == "__main__":
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
    run(fetch="--fetch" in sys.argv, limit=n)
