"""
Walk real hands down the solver's tree, and say what each player did.

Both the leak finder and the population profile need the same awkward thing:
take a hand that happened and find the solved spot it corresponds to. That is
not a lookup, it is a walk -- the node says which actions exist, the real
action is snapped to one of them, the tree moves on, and any hand that leaves
the tree is abandoned from that point because a node reached by the wrong
path prices the wrong spot.

Doing that twice, in two files, is how the leak report and the population
report end up quietly disagreeing about what a 3-bet is. So it lives here
once and both import it.

What comes back is one row per decision per player -- not just hero -- since
the population profile needs every seat and the leak finder simply filters.
"""

import sqlite3

from gtowizard import start_node

PREFLOP_VIEW = """
DROP VIEW IF EXISTS bets_pf;
CREATE VIEW bets_pf AS
  SELECT a.hand_id, a.n, a.seat, a.position, a.action, a.amount, a.total,
         COALESCE(b.to_call, 0) AS to_call
  FROM actions a LEFT JOIN bets b ON b.hand_id=a.hand_id AND b.n=a.n
  WHERE a.street='preflop';
"""


def snap(act, node, bb):
    """
    The solver action closest to what the player actually did.

    Folds and calls carry over as themselves when the node offers them, and
    when it does not that is the answer: a solved 6-max game has no limp, so
    a call into an unopened pot has nowhere to go and is reported unmapped
    rather than bent into a raise.
    """
    codes = set(node.codes)
    if act["action"] == "F":
        return ("F", 0.0) if "F" in codes else (None, None)

    # Checking is its own action and is not a call of nothing. Preflop it
    # barely arises -- only the big blind can check -- so this was missed
    # until postflop, where checks are everywhere and their absence sent
    # eleven of twelve hands out of the tree at the first flop.
    if act["action"] == "X":
        return ("X", 0.0) if "X" in codes else (None, None)

    # An all-in is written the same way whether it raised or merely called,
    # so what it cost to call is what tells them apart.
    aggressive = act["action"] in ("B", "R") or (
        act["action"] == "A"
        and (act["amount"] or 0) > (act["to_call"] or 0) + 1e-9)
    if not aggressive:
        return ("C", 0.0) if "C" in codes else (None, None)
    return node.nearest_raise((act["total"] or act["amount"] or 0) / bb)


def _seat_facts(con):
    """Everything about a seat that a decision needs to carry with it."""
    out = {}
    for hid, seat, combo, bb, n, hero, player, pos in con.execute(
            "SELECT hand_id, seat, combo, bb, n_players, is_hero, player, "
            # Ignition only. The solutions cached in this database are for
            # one gametype -- 6-max NL25 with rake -- and ACR hands run
            # from $0.02 to $0.50, so pricing them against this tree would be
            # pricing them at the wrong stake. poptree, leaks and
            # bestresponse all read this, so the filter belongs here.
            "position FROM spots WHERE fmt IN ('RING','ZONE') "
            "AND site='ignition' AND bb IS NOT NULL"):
        out[(hid, seat)] = {"combo": combo, "bb": bb, "n_players": n,
                            "is_hero": hero, "player": player, "position": pos}
    return out


def walk(con, gto, fetch=False, record_wanted=False):
    """
    Every preflop decision in the database, placed in the solved tree.

    `fetch=False` keeps this offline: a node that was never cached stops that
    hand and is counted as wanted, so a whole database can be read with no
    browser and the spots it actually reached fetched afterwards.
    """
    con.executescript(PREFLOP_VIEW)
    facts = _seat_facts(con)

    acts_by = {}
    for r in con.execute(
            "SELECT hand_id, n, seat, position, action, amount, total, to_call "
            "FROM bets_pf ORDER BY hand_id, n"):
        acts_by.setdefault(r[0], []).append(
            {"n": r[1], "seat": r[2], "position": r[3], "action": r[4],
             "amount": r[5], "total": r[6], "to_call": r[7]})

    rows, wanted, dropped = [], {}, {}
    # Counted twice over: once for everybody, which is what the population
    # profile reads, and once for hero alone, which is what the leak report
    # reads. Reporting one under the other's name was how "hero made 15,443
    # preflop decisions" got printed for a player with 3,802 hands.
    tally = dict(hands=0, decisions=0, priced=0, no_node=0, no_action=0,
                 too_many=0, wrong_seat=0, no_combo=0,
                 hero_decisions=0, hero_priced=0, hero_no_action=0,
                 hero_no_node=0)

    for hid, acts in acts_by.items():
        first = facts.get((hid, acts[0]["seat"])) if acts else None
        if not first:
            continue
        tally["hands"] += 1
        key = start_node(first["n_players"])
        if key is None:
            tally["too_many"] += 1
            continue

        first_raiser, path_gap = None, 0.0
        for a in acts:
            node = gto.node(key, fetch=fetch)
            if node is None:
                tally["no_node"] += 1
                if facts.get((hid, a["seat"]), {}).get("is_hero"):
                    tally["hero_no_node"] += 1
                wanted[key] = wanted.get(key, 0) + 1
                break
            # The solver naming the seat to act is a free check that the
            # short-table prefix lined the two trees up correctly.
            if node.hero and a["position"] != node.hero:
                # The two trees have come apart: the solver expects a
                # different seat to act than the hand history shows. Kept
                # with its details rather than merely counted, because an
                # exclusion nobody can explain is not an exclusion, it is an
                # unknown sitting inside every number downstream.
                tally["wrong_seat"] += 1
                tally.setdefault("wrong_seat_cases", []).append(
                    (hid, key, a["position"], node.hero))
                break

            me = facts.get((hid, a["seat"])) or {}
            mine = bool(me.get("is_hero"))
            tally["decisions"] += 1
            tally["hero_decisions"] += mine
            facing = sum(1 for p in preflop_of(key).split("-")
                         if p.startswith("R"))
            code, gap = snap(a, node, me.get("bb") or 1.0)
            if code is None:
                # The action the player took does not exist in the solved
                # tree -- a limp, or a cold call of a 3-bet, which GTO Wizard
                # does not offer. Counted by spot rather than lumped together,
                # because a frequency computed over the survivors of a silent
                # drop is a frequency about the wrong population.
                tally["no_action"] += 1
                tally["hero_no_action"] += mine
                dropped[facing] = dropped.get(facing, 0) + 1
                break

            combo = me.get("combo")
            if not combo:
                tally["no_combo"] += 1
            else:
                freq = node.freq(combo)
                rows.append({
                    "hand_id": hid, "seat": a["seat"], "node": key,
                    "position": a["position"], "combo": combo, "chose": code,
                    "size_gap": gap,
                    # How badly the actions BEFORE this one had to be bent to
                    # fit the tree. A min-3-bet snapped to a full one puts the
                    # player in a node that is not the spot they were really
                    # in, and the EV read off it is the wrong spot's EV -- so
                    # a decision is only as trustworthy as the path to it.
                    "path_gap": path_gap, "is_hero": me.get("is_hero", 0),
                    "player": me.get("player"),
                    # How many raises stand in front of the player: 0 is an
                    # unopened pot, 1 is facing an open, 2 is facing a 3-bet.
                    "facing": facing,
                    # Whether this is the player who opened. It changes the
                    # spot entirely: the opener facing a 3-bet may flat, a
                    # cold seat facing the same 3-bet may not.
                    "role": ("opener" if a["position"] == first_raiser
                             else "cold"),
                    "loss": node.loss(combo, code),
                    "best": node.best(combo),
                    "solver": freq,
                    # Two different solver numbers, both wanted. `solver` is
                    # what the solver does with THIS hand -- the like-for-like
                    # comparison. `node_freq` is what the solver does at this
                    # spot across its whole range, which is what a player
                    # means by "the solver 4-bets 26% here". Reporting the
                    # first under the second's name overstated an SB 4-bet
                    # figure by five points.
                    "node_freq": {a["code"]: (a["freq"] or 0.0)
                                  for a in node.actions},
                    # Whether raising is even on the table. Facing an all-in
                    # there is nothing to raise WITH, so the solver's raise
                    # frequency is 0% by force. Averaged in with real 3-bet
                    # spots that zero is not a strategy, it is a constraint,
                    # and it drags the comparison somewhere meaningless.
                    "can_raise": any(a["type"] == "RAISE"
                                     for a in node.actions),
                })
                tally["priced"] += 1
                tally["hero_priced"] += mine

            path_gap = max(path_gap, gap or 0.0)
            if first_raiser is None and kind(code) == "raise":
                first_raiser = a["position"]
            child = node.child(code)
            if child is None:
                break
            key = child

    if record_wanted:
        for key, hits in wanted.items():
            gto.want(key, hits)
    tally["dropped_by_facing"] = dropped
    return rows, tally, wanted


def preflop_of(key):
    """
    The preflop actions out of a node key, whichever shape the key is.

    A node key used to be the preflop action string and nothing else. Adding
    postflop solutions made it a tuple -- preflop, board, flop, turn, river --
    because a preflop line no longer identifies a node once a board is
    involved. `start_node` still hands back a bare string for the root, so
    both shapes are live in one walk, and code that counts the raises in
    front of a player has to accept either.

    This is why `leaks.py` stopped running: it called `.split` on a key that
    had quietly become a tuple, and nothing had run it since.
    """
    return key[0] if isinstance(key, tuple) else key


def kind(code):
    """Fold, call or raise -- the shape of an action, ignoring its size."""
    if code == "F":
        return "fold"
    if code == "C":
        return "call"
    return "raise"
