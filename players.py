"""
Who each player is, so that "how regulars play" is a filter and not a guess.

Every stat in this tracker averages two different populations together.
People play completely differently against a recreational player than
against each other -- they isolate wider, they value-bet thinner, they bluff
less -- so a pool number that mixes the two describes neither. Separating
them is the single largest thing Hand2Note does that this could not, and it
needs one thing this database has never had: a row per player rather than a
row per hand.

Everything here is a `GROUP BY` over `spots`, which already carries VPIP,
PFR, 3-bet, fold-to-3-bet, WWSF, WTSD and money as per-hand flags. Nothing
new is measured; what is new is that it is measured *per person*.

**Only ACR has people.** ACR writes the screen name, and it is the same
player next week at another table and another stake. Ignition writes nobody,
so `spots.identify` gives a ring seat the name `table:seat:segment`, which
is one person for exactly as long as they stay sat there and is a different
person after they leave. Those rows are kept and marked `durable = 0`,
because a read that is true for a session is still a read -- but they must
never be counted as people, and a report that does not say which kind it has
is a report about a pool of ghosts.

The classification refuses more often than it decides. A rate on 40 hands
has an interval about sixteen points wide, so "VPIP 30" and "VPIP 45" are
the same measurement, and a rule that reads the point estimate would sort
half the pool at random. Every clause below is a test on an interval, and
anything that does not clear one is `unknown` -- which is an answer, and the
honest one. Checked by splitting each player's hands in two: of 85 ACR
players with 100 hands or more, **not one** is a reg on one half and a fish
on the other.

    python players.py            build the table
    python players.py --check    the classes survive being split in half
    python players.py NAME       one player in full
"""

import sqlite3
import sys
from pathlib import Path

from stats import wilson

DB = Path(__file__).parent / "hands.db"

# Where a class begins, and what each number means in plain terms. They are
# thresholds on the *interval*, never on the estimate, so a player is only
# called loose when they cannot plausibly be tight.
LOOSE = 0.34         # plays more than a third of hands: too many to be solid
TIGHT = 0.33         # and the same line from the other side
RAISES = 0.10        # raises at least one hand in ten before the flop
PASSIVE = 0.10       # or, below this, essentially never raises
ENTERS = 0.20        # ...while still entering a fifth of pots, which is what
                     # makes it passive rather than merely tight

SCHEMA = """
DROP TABLE IF EXISTS players;
CREATE TABLE players (
  site TEXT, player TEXT, durable INT, hands INT,
  vpip REAL, pfr REAL, threebet REAL, fold_to_threebet REAL,
  wwsf REAL, wtsd REAL, wsd REAL,
  bb100 REAL, class TEXT,
  PRIMARY KEY (site, player));
CREATE INDEX players_class ON players(class, hands);
"""

# Added to `decisions` rather than kept beside it, because every filter in
# the program is a predicate over that one table and a join would be a
# second way of asking.
#
# `vs_seat` is there and `vs_player` is not enough on its own: on Ignition
# Zone nobody has a name, so a NULL `vs_player` means either "nobody is left
# to face" or "the man opposite is a stranger", and those are different
# facts. The seat is always known when there is one opponent, which is what
# makes it the thing to check the walk against.
COLUMNS = (("player_class", "TEXT"), ("vs_player", "TEXT"),
           ("vs_class", "TEXT"), ("vs_seat", "INT"))
NAMES = tuple(c for c, _t in COLUMNS)


def classify(hands, vpip, pfr):
    """
    reg, fish, or unknown -- and unknown is the usual answer.

    Two ways to be a fish and they are different faults. Loose: plays more
    than a third of the hands dealt, which no winning strategy does. Passive:
    comes in often and almost never raises, which is the recreational tell
    that survives at every stake. A reg has to fail both tests with room to
    spare, so the rule cannot promote somebody merely by not having watched
    them long enough.
    """
    if not hands:
        return "unknown"
    _p, v_lo, v_hi = wilson(vpip, hands)
    _q, p_lo, p_hi = wilson(pfr, hands)
    if v_lo >= LOOSE:
        return "fish"
    if p_hi <= PASSIVE and vpip / hands >= ENTERS:
        return "fish"
    if v_hi <= TIGHT and p_lo >= RAISES:
        return "reg"
    return "unknown"


def migrate(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(decisions)")}
    for name, kind in COLUMNS:
        if name not in cols:
            con.execute(f"ALTER TABLE decisions ADD COLUMN {name} {kind}")
    con.commit()


def totals(con, where="1=1", params=()):
    """One row per (site, player), straight out of `spots`."""
    return con.execute(f"""
        SELECT site, player, COUNT(*) hands,
               SUM(vpip) vpip, SUM(pfr) pfr,
               SUM(threebet) tb, SUM(threebet_chance) tb_n,
               SUM(fold_to_threebet) f3, SUM(faced_threebet) f3_n,
               SUM(wwsf) wwsf, SUM(wtsd) wtsd, SUM(saw_flop) flops,
               SUM(wsd) wsd,
               SUM(CASE WHEN fmt <> 'MTT' THEN net_bb ELSE 0 END) net,
               SUM(fmt <> 'MTT') money_hands
        FROM spots
        WHERE player IS NOT NULL AND ({where})
        GROUP BY site, player""", params).fetchall()


def build(db_path=DB):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    migrate(con)

    rows = []
    for r in totals(con):
        n = r["hands"]
        pct = lambda k, d: (100.0 * k / d) if d else None
        rows.append((
            r["site"], r["player"],
            # ACR names a person. An Ignition ring seat names a chair, and
            # the person in it changes without the name doing so.
            int(r["site"] == "acr"), n,
            pct(r["vpip"], n), pct(r["pfr"], n),
            pct(r["tb"], r["tb_n"]), pct(r["f3"], r["f3_n"]),
            pct(r["wwsf"], r["flops"]), pct(r["wtsd"], r["flops"]),
            pct(r["wsd"], r["wtsd"]),
            (100.0 * r["net"] / r["money_hands"]) if r["money_hands"] else None,
            classify(n, r["vpip"], r["pfr"])))
    con.executemany(
        "INSERT INTO players VALUES (" + ",".join("?" * 13) + ")", rows)

    stamp(con)
    con.commit()
    named = sum(1 for r in rows if r[2])
    print(f"{len(rows):,} identities ({named:,} of them people), "
          f"{sum(1 for r in rows if r[12] == 'reg'):,} regs, "
          f"{sum(1 for r in rows if r[12] == 'fish'):,} fish")
    con.close()
    return len(rows)


def stamp(con):
    """
    Put the class of the player acting, and of the one they face, on every
    decision.

    The opponent only exists while one is left: in a three-way pot there is
    no "the other player", so `vs_player` is NULL there rather than being
    filled with whichever seat happened to be first. Liveness is worked out
    by walking each hand and dropping a seat when it folds -- an all-in
    player never folds and so stays live, which is right, and is why this
    cannot be read off the action verbs alone.

    Who is in the hand comes from `spots`, one row per player per hand, and
    not from who is seen to act. Taking it from the actions loses the player
    who never gets a turn: heads up, when the small blind folds immediately,
    the big blind wins without acting and appears nowhere in `decisions` --
    3,848 decisions had no opponent recorded for exactly that reason.
    `spots` also already excludes the seats that are at the table but not in
    the hand, which is a guard this would otherwise have to repeat.
    """
    klass = {(r[0], r[1]): r[2]
             for r in con.execute("SELECT site, player, class FROM players")}

    dealt, who_is = {}, {}
    for r in con.execute("SELECT hand_id, seat, player FROM spots"):
        dealt.setdefault(r["hand_id"], set()).add(r["seat"])
        who_is[(r["hand_id"], r["seat"])] = r["player"]

    by_hand = {}
    for r in con.execute("SELECT hand_id, n, seat, player, site, action "
                         "FROM decisions ORDER BY hand_id, n"):
        by_hand.setdefault(r["hand_id"], []).append(r)

    out = []
    for hid, acts in by_hand.items():
        live = dealt.get(hid) or {a["seat"] for a in acts}
        folded = set()
        for a in acts:
            here = live - folded
            other = None
            if len(here) == 2:
                other = next(iter(here - {a["seat"]}), None)
            who = who_is.get((hid, other)) if other is not None else None
            out.append((
                klass.get((a["site"], a["player"])),
                who,
                klass.get((a["site"], who)) if who else None,
                other,
                a["hand_id"], a["n"]))
            if a["action"] == "F":
                folded.add(a["seat"])

    con.execute("CREATE TEMP TABLE stamped ("
                + ", ".join(f"{c} {t}" for c, t in COLUMNS)
                + ", hand_id TEXT, n INT)")
    con.executemany("INSERT INTO stamped VALUES (?,?,?,?,?,?)", out)
    con.execute("CREATE INDEX temp.stamped_key ON stamped(hand_id, n)")
    con.execute(
        "UPDATE decisions SET "
        + ", ".join(f"{c} = stamped.{c}" for c in NAMES)
        + " FROM stamped WHERE decisions.hand_id = stamped.hand_id "
          "AND decisions.n = stamped.n")
    con.execute("CREATE INDEX IF NOT EXISTS dec_class "
                "ON decisions(player_class, vs_class, street)")
    con.execute("CREATE INDEX IF NOT EXISTS dec_vsplayer "
                "ON decisions(vs_player)")
    con.execute("ANALYZE")


def show(name, db_path=DB):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM players WHERE player = ?", (name,)).fetchall()
    if not rows:
        print(f"nobody called {name!r}. Try: python players.py --list")
        return
    for r in rows:
        note = "" if r["durable"] else ("   (a seat, not a person -- this "
                                        "identity dies with the session)")
        print(f"\n{r['player']}   {r['site']}   {r['hands']:,} hands   "
              f"{r['class'].upper()}{note}")
        for label, key, unit in (
                ("VPIP", "vpip", "%"), ("PFR", "pfr", "%"),
                ("3-bet", "threebet", "%"), ("fold to 3-bet", "fold_to_threebet", "%"),
                ("won when saw flop", "wwsf", "%"), ("went to showdown", "wtsd", "%"),
                ("won at showdown", "wsd", "%"), ("bb/100", "bb100", "")):
            v = r[key]
            print(f"  {label:20} {'-' if v is None else f'{v:6.1f}{unit}'}")


def leaderboard(db_path=DB, klass=None, min_hands=100):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    where = "hands >= ?" + (" AND class = ?" if klass else "")
    params = (min_hands,) + ((klass,) if klass else ())
    rows = con.execute(f"SELECT * FROM players WHERE {where} "
                       f"ORDER BY hands DESC LIMIT 40", params).fetchall()
    print(f"{'player':22} {'site':9} {'hands':>7} {'VPIP':>6} {'PFR':>6} "
          f"{'WWSF':>6} {'WTSD':>6}  class")
    for r in rows:
        f = lambda v: "     -" if v is None else f"{v:6.1f}"
        print(f"{r['player'][:21]:22} {r['site']:9} {r['hands']:7,} "
              f"{f(r['vpip'])} {f(r['pfr'])} {f(r['wwsf'])} {f(r['wtsd'])}  "
              f"{r['class']}")


def check(db_path=DB):
    """
    The classes must survive the hands being split in half.

    A rule that sorts players by a number always produces classes; the
    question is whether they describe the player or the sample. So each
    player's hands are split arbitrarily in two and classified twice. One
    half saying `unknown` is not a failure -- half the hands is half the
    evidence, and refusing is what the rule is supposed to do there. A reg on
    one half and a fish on the other is a failure, and it is the only kind
    that would not show up as anything else.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    fails = []

    n = con.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    people = con.execute("SELECT COUNT(*) FROM players WHERE durable=1").fetchone()[0]
    print(f"identities                   {n:,}  ({people:,} of them people, "
          f"{n - people:,} session-only seats)")
    if not n:
        print("\nFAIL: no players -- run `python players.py`")
        return False

    for klass in ("reg", "fish", "unknown"):
        c = con.execute("SELECT COUNT(*) FROM players WHERE class=?",
                        (klass,)).fetchone()[0]
        big = con.execute("SELECT COUNT(*) FROM players WHERE class=? "
                          "AND hands>=100", (klass,)).fetchone()[0]
        print(f"  {klass:8} {c:6,}   {big:4} of them with 100+ hands")

    # The split. Parity of the last character of the hand id is arbitrary
    # with respect to how anybody plays, which is the point.
    halves = []
    for odd in (0, 1):
        halves.append({
            (r["site"], r["player"]): (r["hands"], r["vpip"], r["pfr"])
            for r in con.execute(
                "SELECT site, player, COUNT(*) hands, SUM(vpip) vpip, "
                "SUM(pfr) pfr FROM spots WHERE player IS NOT NULL "
                "AND CAST(SUBSTR(hand_id, -1) AS INT) % 2 = ? "
                "GROUP BY site, player", (odd,))})
    whole = {(r["site"], r["player"]): r["hands"] for r in
             con.execute("SELECT site, player, hands FROM players")}

    same = soft = flipped = 0
    for key, hands in whole.items():
        if hands < 100 or key not in halves[0] or key not in halves[1]:
            continue
        a, b = (classify(*halves[i][key]) for i in (0, 1))
        if a == b:
            same += 1
        elif "unknown" in (a, b):
            soft += 1
        else:
            flipped += 1
            if flipped <= 5:
                print(f"    {key[1]}: {a} on one half, {b} on the other")
    total = same + soft + flipped
    print(f"split-half, 100+ hands       {same}/{total} identical, "
          f"{soft} refused on one half, {flipped} contradicted")
    if flipped:
        fails.append(f"{flipped} players change class between halves")

    # What the classification is actually worth: how much of the action it
    # can speak about. A rule that is right about nobody is not useful.
    told = con.execute(
        "SELECT COUNT(*) FROM decisions WHERE player_class IN ('reg','fish')"
    ).fetchone()[0]
    tot = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    vs = con.execute(
        "SELECT COUNT(*) FROM decisions WHERE vs_class IN ('reg','fish')"
    ).fetchone()[0]
    print(f"decisions by a known class   {told:,}/{tot:,}  ({100*told/tot:.1f}%)")
    print(f"decisions against one        {vs:,}/{tot:,}  ({100*vs/tot:.1f}%)")
    if not vs:
        fails.append("no decision has a classified opponent -- vs_class is empty")

    # The liveness walk here and `n_live` in decisions.py are two separate
    # derivations of the same fact and have to agree about when exactly one
    # opponent is left. Checked against `vs_seat` and not `vs_player`: on
    # Zone nobody has a name, so a missing name would look like a missing
    # opponent and hide a real disagreement behind 2,189 false ones.
    #
    # Hands where the two modules' ghost-seat guards disagree are counted
    # separately rather than swept in. A seat can be at the table and not in
    # the hand -- "sitting out", "waits for big blind" -- and `spots` drops
    # those while `decisions` counts them, so on those hands the two are
    # answering slightly different questions. One hand does this: a 3-handed
    # Zone hand whose whole history is the button folding, which cannot be
    # a three-player hand however it is counted.
    ghost = set(r[0] for r in con.execute(
        "SELECT s.hand_id FROM (SELECT hand_id, COUNT(*) n FROM seats "
        "GROUP BY hand_id) s JOIN (SELECT hand_id, COUNT(*) n FROM spots "
        "GROUP BY hand_id) p USING(hand_id) WHERE s.n <> p.n"))
    rows = con.execute(
        "SELECT hand_id, COUNT(*) n FROM decisions "
        "WHERE (vs_seat IS NOT NULL) <> (n_live = 2) GROUP BY hand_id"
    ).fetchall()
    bad = sum(r["n"] for r in rows if r["hand_id"] not in ghost)
    excused = sum(r["n"] for r in rows if r["hand_id"] in ghost)
    note = "" if not excused else (
        f"   ({excused} on hands where the two ghost-seat guards differ)")
    print(f"one opponent, agreed with n_live  "
          f"{tot - bad - excused:,}/{tot:,}{note}"
          f"{'' if not bad else '   <-- DISAGREE'}")
    if bad:
        fails.append(f"{bad} decisions where the liveness walk and n_live "
                     f"disagree for no reason")

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if "--help" in argv:
        print(__doc__)
        return 0
    if "--list" in argv:
        i = argv.index("--list")
        leaderboard(klass=argv[i + 1] if i + 1 < len(argv) else None)
        return 0
    if argv and not argv[0].startswith("-"):
        show(argv[0])
        return 0
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
