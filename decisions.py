"""
One row per decision, carrying everything that was true when it was made.

`spots` answers questions somebody thought of in advance. It has a column
for fold-to-cbet because fold-to-cbet was on the list the day it was
written, and no column for a delayed turn probe because that was not. Every
new question means editing the derivation and rebuilding tens of thousands
of rows -- which is why seven analysis modules each hardcode their own SQL,
and why none of them can be asked something new.

A tracker works the other way round. It records the *situation* -- street,
pot type, who raised last, what it costs, who is in position, how many are
left, what the flop looks like -- and a statistic is then nothing but two
filters over those situations: the times the spot arose, and the times the
player did the thing. Fold-to-cbet and delayed-turn-probe become the same
kind of object, and a new stat is a line of definition instead of a rebuild.

This table is deliberately wider than any one question needs, because its
columns are the vocabulary every future question has to be phrased in.

    python decisions.py            build, or rebuild
    python decisions.py --check    cross-check against spots, PASS or FAIL
"""

import sqlite3
import sys
from pathlib import Path

from spots import combo_of, is_aggressive, with_pot

DB = Path(__file__).parent / "hands.db"

RANKS = "23456789TJQKA"

SCHEMA = """
DROP TABLE IF EXISTS decisions;
CREATE TABLE decisions (
  hand_id TEXT, n INT, street TEXT,
  seat INT, player TEXT, is_hero INT, site TEXT,
  table_id TEXT, fmt TEXT, bb REAL, played_at TEXT, n_players INT,
  standard INT, position TEXT, cards TEXT, combo TEXT, board TEXT,

  -- the state in front of the player when it was their turn
  pot_before REAL, to_call REAL, pot_bb REAL, to_call_bb REAL,
  stack_before REAL, eff_stack REAL, eff_bb REAL, spr REAL,
  n_live INT, n_opp INT,

  -- who they are in this hand
  pot_type TEXT, is_pfa INT, prev_agg INT, is_ip INT,
  first_in INT, checked_to INT, was_agg INT, acted_before INT,
  vs_pfa INT,

  -- what they are answering
  facing TEXT, street_agg INT,

  -- what they did
  action TEXT, agg INT, allin INT, amount REAL, total REAL,
  pot_frac REAL, size_bb REAL,

  -- the flop, described the way a filter wants to ask about it
  fl_paired INT, fl_mono INT, fl_twotone INT, fl_conn INT, fl_hi TEXT,

  PRIMARY KEY (hand_id, n));

CREATE INDEX dec_player ON decisions(player, site);
CREATE INDEX dec_spot ON decisions(street, pot_type, facing);
CREATE INDEX dec_pos ON decisions(site, fmt, position, street);
CREATE INDEX dec_hand ON decisions(hand_id);
"""


def flop_texture(board):
    """The three flop cards as four flags and a high card, or nothing."""
    cards = (board or "").split()
    if len(cards) < 3:
        return (None,) * 5
    ranks = [c[0] for c in cards[:3]]
    suits = [c[1] for c in cards[:3]]
    if any(r not in RANKS for r in ranks):
        return (None,) * 5
    idx = sorted(RANKS.index(r) for r in ranks)
    paired = int(len(set(ranks)) < 3)
    # Connected in the sense that changes how a flop plays: three cards
    # inside a five-card window can make a straight with two more. A pair on
    # the board is not connectedness.
    return (paired, int(len(set(suits)) == 1), int(len(set(suits)) == 2),
            int(not paired and idx[2] - idx[0] <= 4), RANKS[idx[2]])


def board_to(board, street):
    """The board as it stood on that street -- not the runout after it."""
    cards = (board or "").split()
    return " ".join(cards[:{"preflop": 0, "flop": 3, "turn": 4, "river": 5}[street]])


def build(db_path=DB):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)

    hands = con.execute(
        "SELECT * FROM hands WHERE game='HOLDEM' ORDER BY played_at, hand_id"
    ).fetchall()
    seats_by, acts_by = {}, {}
    for r in con.execute("SELECT * FROM seats ORDER BY hand_id, seat"):
        seats_by.setdefault(r["hand_id"], []).append(dict(r))
    for r in con.execute("SELECT * FROM actions ORDER BY hand_id, n"):
        acts_by.setdefault(r["hand_id"], []).append(dict(r))
    # Identity is settled once, in spots.identify, for both sites. Deriving
    # it a second time here would be a second answer to the same question,
    # and the two would drift apart the first time either changed.
    who = {(r[0], r[1]): r[2]
           for r in con.execute("SELECT hand_id, seat, player FROM spots")}

    rows = []
    for h in hands:
        hid, bb = h["hand_id"], h["bb"] or 0.0
        seats, acts = seats_by.get(hid, []), acts_by.get(hid, [])
        if not seats or not acts:
            continue
        site = (h["site"] if "site" in h.keys() else None) or "ignition"
        by_seat = {s["seat"]: s for s in seats}
        actions = with_pot(seats, acts)
        texture = flop_texture(h["board"])

        # Acting order after the flop. Taken from the flop itself when there
        # was one, because that is what happened rather than a rule about
        # what usually happens; rotated from the preflop order otherwise,
        # the blinds acting first once the cards are out.
        pf_order = list(dict.fromkeys(
            a["seat"] for a in actions if a["street"] == "preflop"))
        fl_order = list(dict.fromkeys(
            a["seat"] for a in actions if a["street"] == "flop"))
        order = fl_order or (pf_order[-2:] + pf_order[:-2])
        rank_of = {s: i for i, s in enumerate(order)}

        put_in = {s["seat"]: (s["posted"] or 0.0) for s in seats}
        live = {s["seat"] for s in seats}
        street, street_agg, last_agg = "preflop", 0, None
        # Who has already acted on this street, and who has already been
        # aggressive on it. Preflop there is no "preflop aggressor" yet --
        # that only exists once the street is over -- so the original raiser
        # facing a 3bet has to be identified as somebody who already raised.
        acted_this, agg_this = set(), set()
        pf_raises, pfa = 0, None
        prev_street_agg, checked_through, first_of_street = None, 0, True

        for a in actions:
            if a["street"] != street:
                if street == "preflop":
                    pfa = last_agg
                prev_street_agg = last_agg
                checked_through = int(street_agg == 0 and street != "preflop")
                street, street_agg, last_agg = a["street"], 0, None
                acted_this, agg_this = set(), set()
                first_of_street = True

            seat = a["seat"]
            s = by_seat.get(seat, {})
            pot, call = a["pot_before"], a["to_call"]
            contributed = put_in.get(seat, 0.0)
            stack = (s.get("stack") or 0.0) - contributed
            opp = [x for x in live if x != seat]
            # The stack that matters is the one that can actually be lost,
            # so it is capped by the biggest opponent still in the hand.
            eff = min(stack, max(((by_seat[x].get("stack") or 0.0)
                                  - put_in.get(x, 0.0)) for x in opp)) \
                if opp else stack

            # What they are answering. Preflop a blind is not aggression, so
            # the ladder counts raises: unopened, an open, a 3bet, a 4bet.
            # After the flop the first bet IS aggression and the ladder
            # starts one rung lower.
            if street == "preflop":
                facing = ("unopened", "open", "3bet", "4bet")[street_agg] \
                    if street_agg < 4 else "5bet+"
            else:
                facing = ("check", "bet", "raise", "3bet")[min(street_agg, 3)]

            if street != "preflop" and pf_raises == 0:
                pot_type = "limped"
            else:
                pot_type = ("unopened", "raised", "3bet", "4bet")[pf_raises] \
                    if pf_raises < 4 else "5bet+"

            agg = int(is_aggressive(a))
            amount, total = a["amount"] or 0.0, a["total"]
            # Whether this action put the last chip in. CoinPoker says so
            # outright; Ignition writes an all-in RAISE as an ordinary raise
            # and reserves "All-in" for its shoves, so on that site it has to
            # be worked out -- and it must be, because an all-in player takes
            # no further decisions and otherwise looks like one who declined
            # to act on every street that followed.
            is_raise = a["action"] in ("R", "A") and total is not None
            went_in = (total - contributed) if is_raise else amount
            said = a["allin"] if "allin" in a and a["allin"] is not None else 0
            allin = int(bool(said) or stack - went_in <= 0.005)
            pot_frac = None
            if agg:
                if call > 0:
                    # A raise measured the way a solver states one: what goes
                    # in on top of the call, over the pot you would call into.
                    top = (total or (amount + call)) - call
                    pot_frac = top / (pot + call) if pot + call else None
                else:
                    pot_frac = amount / pot if pot else None

            rows.append((
                hid, a["n"], street, seat, who.get((hid, seat)),
                s.get("is_hero"), site, h["table_id"], h["fmt"], h["bb"],
                h["played_at"], h["n_players"], h["standard"], a["position"],
                s.get("cards"), combo_of(s.get("cards"))[0],
                board_to(h["board"], street),

                pot, call,
                round(pot / bb, 3) if bb else None,
                round(call / bb, 3) if bb else None,
                round(stack, 4), round(eff, 4),
                round(eff / bb, 2) if bb else None,
                round(eff / pot, 3) if pot else None,
                len(live), len(opp),

                pot_type,
                None if street == "preflop" else int(
                    pfa is not None and seat == pfa),
                int(prev_street_agg is not None and seat == prev_street_agg),
                None if street == "preflop" else int(
                    rank_of.get(seat, -1) == max(
                        (rank_of.get(x, -1) for x in live), default=-1)),
                int(first_of_street), checked_through,
                int(seat in agg_this), int(seat in acted_this),
                # Whether the bet in front of them is the preflop raiser's.
                # It is the difference between folding to a continuation bet
                # and folding to a donk bet, and those are not one stat.
                int(last_agg is not None and pfa is not None
                    and last_agg == pfa),

                facing, street_agg,

                a["action"], agg, allin, amount, total,
                round(pot_frac, 4) if pot_frac is not None else None,
                round((total or amount) / bb, 3) if bb and (total or amount) else None,
                *texture))

            first_of_street = False
            acted_this.add(seat)
            if agg:
                agg_this.add(seat)
            if a["action"] == "F":
                live.discard(seat)
            elif a["amount"]:
                # A raise names the street total; everything else names what
                # was added. Getting this backwards makes every later pot in
                # the hand the wrong size.
                if total is not None and a["action"] in ("R", "A"):
                    put_in[seat] = max(total, contributed + amount)
                else:
                    put_in[seat] = contributed + amount
            if agg:
                street_agg += 1
                last_agg = seat
                if street == "preflop":
                    pf_raises += 1

    n = len(con.execute("SELECT * FROM decisions LIMIT 0").description)
    con.executemany(
        "INSERT INTO decisions VALUES ({})".format(",".join("?" * n)), rows)
    con.commit()
    con.close()
    return len(rows)


def check(db_path=DB):
    """
    Does the new table say the same things the old one says?

    Only the stats `spots` already knows can be checked this way, and that
    is the point -- the ones it does not know are exactly what this table is
    for, and they have nothing to be checked against. So the agreement on
    the overlap is the whole of the evidence, and it has to be exact rather
    than close.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    fails = []

    n_dec = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    n_act = con.execute(
        "SELECT COUNT(*) FROM actions a JOIN hands h USING(hand_id) "
        "WHERE h.game='HOLDEM'").fetchone()[0]
    print(f"every action is a decision   {n_dec}/{n_act}"
          f"{'' if n_dec == n_act else '   <-- rows lost'}")
    if n_dec != n_act:
        fails.append("coverage")

    # A player VPIPs once per hand however many times they act, so the
    # comparison is per (hand, seat), not per row.
    # An all-in is voluntary money too. Ignition writes every all-in as one
    # verb whether it was a shove or a call for the last of a stack, so a
    # VPIP filter that only looks for "C" misses the calls.
    pairs = [
        ("VPIP", "SUM(vpip)", "street='preflop'", "agg=1 OR action IN ('C','A')"),
        ("PFR", "SUM(pfr)", "street='preflop'", "agg=1"),
    ]
    for name, spots_expr, where, act in pairs:
        a = con.execute(f"SELECT {spots_expr} FROM spots").fetchone()[0] or 0
        b = con.execute(
            f"SELECT COUNT(*) FROM (SELECT DISTINCT hand_id, seat FROM decisions "
            f"WHERE {where} AND ({act}))").fetchone()[0]
        same = a == b
        print(f"{name:28} spots {a:>7}   decisions {b:>7}   "
              f"{'OK' if same else 'DISAGREE'}")
        if not same:
            fails.append(name)

    # Seeing a flop is not a decision -- a player already all in sees one
    # and has nothing to decide -- so the two tables are expected to differ
    # here, by exactly the number of players who were all in beforehand.
    # Ring only. The 50 tournament hands include histories that begin part
    # way through a hand, so a player can have a turn decision and no preflop
    # one; that is a property of the export, not of this derivation, and
    # letting it sit in the check would mean the check never goes green and
    # therefore never gets read.
    RING = "fmt IN ('RING','BLITZ','ZONE')"
    saw = con.execute(
        f"SELECT SUM(saw_flop) FROM spots WHERE {RING}").fetchone()[0] or 0
    acted = con.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT hand_id, seat FROM decisions "
        f"WHERE street='flop' AND {RING})").fetchone()[0]
    allin = con.execute(
        f"SELECT COUNT(*) FROM spots s WHERE s.saw_flop=1 AND {RING} "
        "AND NOT EXISTS ("
        "SELECT 1 FROM decisions d WHERE d.hand_id=s.hand_id AND d.seat=s.seat "
        "AND d.street='flop') AND EXISTS (SELECT 1 FROM decisions d WHERE "
        "d.hand_id=s.hand_id AND d.seat=s.seat AND d.street='preflop' "
        "AND d.allin=1)").fetchone()[0]
    same = (saw - acted) == allin
    print(f"{'saw flop (ring)':28} spots {saw:>7}   decisions {acted:>7}   "
          f"{'OK' if same else 'DISAGREE'}  (gap {saw - acted}, "
          f"all-in preflop {allin})")
    if not same:
        fails.append("saw flop")

    print()
    print("what the new columns can express that spots cannot:")
    for label, where in [
            ("turn cbet chance", "street='turn' AND prev_agg=1 AND facing='check'"),
            ("delayed cbet chance",
             "street='turn' AND is_pfa=1 AND checked_to=1 AND facing='check'"),
            ("donk bet chance",
             "street='flop' AND first_in=1 AND is_pfa=0 AND is_ip=0"),
            ("float chance", "street='turn' AND prev_agg=0 AND is_ip=1"),
            ("check-raise chance", "street='flop' AND facing='bet' AND first_in=0"),
            ("3bet-pot flop, IP", "street='flop' AND pot_type='3bet' AND is_ip=1"),
            ("river, 100bb+ deep", "street='river' AND eff_bb>=100"),
            ("monotone flop", "street='flop' AND fl_mono=1"),
            ("facing an overbet",
             "to_call > pot_before - to_call AND street!='preflop'"),
            ("shortstacked open",
             "street='preflop' AND facing='unopened' AND eff_bb<40"),
    ]:
        n = con.execute(f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
        print(f"  {label:24} {n:>7}")

    con.close()
    print()
    print("FAIL: " + ", ".join(fails) if fails else "PASS")
    return not fails


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    print(f"{build()} decisions written\n")
    check()
