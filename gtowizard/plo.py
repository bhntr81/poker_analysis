"""
GTO Wizard's Pot Limit Omaha solutions, as the same kind of object as the
Hold'em ones.

The endpoint, the spot key and the action codes are shared with Hold'em --
`R3.5-F-C` means the same thing in either game -- so most of `solver.py`
carries over untouched. Three things do not, and they are the whole of this
file.

**The gametype.** A PLO solution is named for the number of hole cards, the
table size, whether an ante is posted and which rake profile applies:
`PLO4Cash6m100zSimpleAI`. A table that posts an ante and is priced without
one is a different game, so the ante is read from the hand history rather
than assumed.

**The depth.** Hold'em work here fixed 100bb and moved on. PLO stacks arrive
short far more often -- pots grow faster, so a table that started at 100bb is
frequently not there by the time the decision matters -- and the solution is
only solved at certain depths. A hand is priced at the nearest of them and
how far it had to move is reported.

**The hand index.** Hold'em preflop arrays are 169 long and the payload names
their order in `simple_hand_counters`, so nothing has to be assumed. A
four-card game has no 169-cell grid to name, so where the payload does not
name its own order, the order has to be established -- and it is established
the way the Hold'em postflop ordering was, by finding the arrangement under
which no weight sits on a card that is already face up. Until that check has
passed against a real board, `PLOSolver` refuses to decode.

That refusal is the point. A PLO hand looked up under the wrong ordering does
not fail; it returns some other hand's strategy, and every number downstream
is then wrong in a way nothing later can detect.
"""

from math import comb

from .solver import DECK, Node, Solver, cards


def build_gametype(ante=False, stake=100, hole_cards=4, seats=6):
    """
    The gametype string for a PLO game, from what the game actually is.

    The two naming schemes are not parallel. Classic profiles are 100z and
    500z; the ante ones on offer are PLO100 and PLO1k, with no 500 among
    them, so a 500nl ante table takes PLO100 as the nearer of the two.
    Confirmed against three real solver URLs:

        Ante,    100nl -> PLO4Cash6mAntePLO100SimpleAI
        Classic, 100nl -> PLO4Cash6m100zSimpleAI
        Classic, 500nl -> PLO4Cash6m500zSimpleAI
    """
    if ante:
        rake = "Ante" + ("PLO1k" if stake >= 1000 else "PLO100")
    else:
        rake = "500z" if stake >= 500 else "100z"
    return "PLO{}Cash{}m{}SimpleAI".format(hole_cards, seats, rake)


PLO100 = build_gametype(ante=False, stake=100)          # the usual micro game
PLO100_ANTE = build_gametype(ante=True, stake=100)

# Depths GTO Wizard solves PLO at. Asking for one it does not have returns
# nothing at all, which reads as "the spot is missing" rather than "the depth
# is wrong", so the snap happens here where it can be said out loud.
DEPTHS = (20, 30, 40, 50, 75, 100, 150, 200)


def nearest_depth(eff_bb, depths=DEPTHS):
    """
    The solved depth closest to a real effective stack, and how far it moved.

    The gap is returned rather than swallowed for the same reason
    `nearest_raise` returns one: a 62bb hand priced at 75bb is a different
    game, and that has to be visible in the report rather than only in the
    code.
    """
    if not eff_bb or eff_bb <= 0:
        return None, None
    best = min(depths, key=lambda d: abs(d - eff_bb))
    return best, abs(best - eff_bb) / eff_bb


CARD_INDEX = {c: i for i, c in enumerate(DECK)}

# Every four-card hand there is. Enumerated on demand, never stored: 270,725
# frozensets is a few hundred megabytes to answer a question that is
# arithmetic, on a machine that has under four gigabytes.
N_COMBOS_4 = comb(52, 4)


def _lex_rank(idxs, n=52):
    """Where a strictly increasing tuple falls in lexicographic order."""
    rank, prev, k = 0, -1, len(idxs)
    for pos, c in enumerate(idxs):
        for a in range(prev + 1, c):
            rank += comb(n - a - 1, k - pos - 1)
        prev = c
    return rank


def hand_cards(hand):
    """'AhKd7s2c', 'Ah Kd 7s 2c' or a list -> ['Ah', 'Kd', '7s', '2c']."""
    if isinstance(hand, (list, tuple)):
        got = [str(c) for c in hand]
    else:
        got = cards(hand)
    return got if len(got) == 4 else None


def combo_index(hand):
    """
    Which slot a four-card hand occupies, under the Hold'em convention.

    Hold'em's 1326 array runs every i<j pair in lexicographic order over a
    rank-descending deck and then reverses the list. This is the same rule
    with four cards rather than two. It is a CONVENTION, not a fact, until
    `check_order` has confirmed it against a real board.
    """
    got = hand_cards(hand)
    if got is None:
        return None
    try:
        idxs = sorted(CARD_INDEX[c] for c in got)
    except KeyError:
        return None
    if len(set(idxs)) != 4:
        return None
    return N_COMBOS_4 - 1 - _lex_rank(idxs)


def _quads():
    """Every i<j<k<l in lexicographic order -- the order `combo_index` ranks."""
    for i in range(52):
        for j in range(i + 1, 52):
            for k in range(j + 1, 52):
                for m in range(k + 1, 52):
                    yield i, j, k, m


def check_order(node, board):
    """
    Whether `combo_index` is the ordering this payload actually uses.

    A card on the board cannot also be in somebody's hand, so under the right
    ordering every combo containing a board card carries exactly zero weight,
    and under a wrong one a great many of them do not. Three flop cards block
    74,120 of the 270,725 slots; an ordering that puts no weight on any of
    them is not doing so by luck.

    Returns (passed, detail), so a caller can print why and not merely
    whether. `passed` is None when the check could not be run at all.
    """
    face_up = {CARD_INDEX[c] for c in cards(board) if c in CARD_INDEX}
    if not face_up:
        return None, "no board -- this check needs cards that are face up"

    weights = None
    for a in node.actions:
        s = a.get("strategy")
        if s and len(s) == N_COMBOS_4:
            weights = list(s) if weights is None else [
                x + y for x, y in zip(weights, s)]
    if weights is None:
        return None, "no {}-long strategy array here".format(N_COMBOS_4)

    blocked = live = 0
    on_blocked = 0.0
    worst = 0.0
    for rank, quad in enumerate(_quads()):
        if face_up.intersection(quad):
            blocked += 1
            w = weights[N_COMBOS_4 - 1 - rank]
            on_blocked += w
            worst = max(worst, w)
        else:
            live += 1
    ok = on_blocked < 1e-6
    return ok, ("{} slots blocked by {}, {} live; total weight on blocked "
                "slots {:.8f}, worst single slot {:.8f}"
                .format(blocked, "".join(cards(board)), live, on_blocked, worst))


class PLONode(Node):
    """
    One solved PLO spot.

    A PLO hand is four known cards, always. There is no asking by class the
    way `AKs` names four combinations in Hold'em: the four-card classes are
    not what these arrays are indexed by, and averaging over a class would
    average over hands that are not each other -- on a heart flop, AhKh7s2c
    and AsKs7h2c are different holdings, not two spellings of one.
    """

    def _slots(self, hand):
        # A payload that names its own order settles the question, exactly as
        # in Hold'em, and is preferred over any convention.
        if self._index:
            key = hand if isinstance(hand, str) else "".join(hand)
            i = self._index.get(key)
            return [] if i is None else [i]
        width = len(self.actions[0]["strategy"] or []) if self.actions else 0
        if width != N_COMBOS_4:
            return []
        i = combo_index(hand)
        return [] if i is None or i >= width else [i]


class PLOSolver(Solver):
    """
    Solved PLO spots, fetched one at a time and not kept.

    The two defaults that differ from Hold'em are both about size. A PLO spot
    carries one array slot per exact four-card hand -- 270,725 of them per
    action -- so keeping the reply on disk would be megabytes a node and
    gigabytes a tree, and keeping 250 of them in memory would not fit on this
    machine at all. Both are therefore off: a caller reads the hands it cares
    about out of the reply and keeps only those.

    A caller that genuinely wants the arrays kept can still say `store=True`,
    and should expect the size that implies.
    """

    node_class = PLONode

    def __init__(self, gametype=PLO100, depth="100", store=False,
                 memory_nodes=1, **kw):
        Solver.__init__(self, gametype=gametype, depth=depth, store=store,
                        memory_nodes=memory_nodes, **kw)


def describe(node):
    """What a payload actually contains -- the report `omaha.py probe` prints."""
    width = len(node.actions[0]["strategy"] or []) if node.actions else 0
    lines = ["hero to act:      {}".format(node.hero or "?"),
             "actions:          {}".format(", ".join(node.codes) or "none"),
             "strategy width:   {}".format(width),
             "names its order:  {}".format(
                 "yes, {} entries".format(len(node.order))
                 if node.order else "no")]
    if width == N_COMBOS_4:
        lines.append("that is C(52,4) -- one slot per exact four-card hand")
    elif width == 16432:
        lines.append("that is 16432 -- one slot per suit-isomorphic class, "
                     "an order this payload does not name and this module "
                     "cannot derive")
    elif width == 169:
        lines.append("that is 169 -- a HOLD'EM array. The gametype asked for "
                     "is not the one that answered")
    elif width:
        lines.append("that is not a width this module knows how to read")
    for a in node.actions:
        lines.append("  {:>6}  size {:>7}  taken by {:>6.2f}% of the range"
                     .format(a["code"], a["betsize"] or "-",
                             100 * (a["freq"] or 0)))
    return "\n".join(lines)
