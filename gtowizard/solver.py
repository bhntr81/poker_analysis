"""
The part other programs actually use: ask for a spot, get the strategy back.

    from gtowizard import Solver

    with Solver() as gto:
        spot = gto.node("F-F")          # folded to the cutoff
        spot.hero                       # 'CO'
        spot.freq("AKs")                # {'F': 0.0, 'R2.5': 1.0, 'RAI': 0.0}
        spot.ev("AKs")                  # {'F': 0.0, 'R2.5': 3.05, ...}
        spot.best("55")                 # the highest-EV action
        spot.loss("AA", "F")            # bb thrown away folding aces

Cached nodes are answered from disk with no browser and no network. Only a
node never seen before opens the browser, and then only once.

A short table is the same tree with the early seats folded, which is what
`start_node` is for: five handed is "F", four handed "F-F". That falls out of
naming short tables by their late positions rather than their early ones.
"""

import json as _json
import urllib.error
import urllib.parse
import urllib.request

from .browser import Browser, load_token, say
from .cache import Cache, DEFAULT_DB, as_spot

API = "https://api.gtowizard.com/v4/solutions/spot-solution/"

# Six-max No Limit Hold'em cash at 25NL, solved WITH rake. Rake matters: a
# rake-free solution opens wider than is correct against a raked pool.
NL25_6MAX = "Cash6mGeneral_6mNL25R25"

SEATS = 6                       # the solved tree is six handed

RANK_ORDER = {r: i for i, r in enumerate("23456789TJQKA")}

# Postflop, the strategy arrays are 1326 long -- one entry per exact two-card
# combination -- while `simple_hand_counters` still lists only the 169 hand
# classes. Nothing in the payload names the 1326 ordering, so it was
# recovered and then PROVEN against three independent constraints on two
# different boards: the summed range weight of each class matches its
# `total_combos`, the count of unblocked combos matches
# `total_combos_available`, and no combo containing a board card carries any
# weight. Exactly one ordering satisfies all three; every other suit
# permutation puts weight on a card that is face up.
#
# Cards run rank-descending, four suits each in the order s h d c, so index 0
# is As and index 51 is 2c. Combos are every i<j pair, reversed.
DECK = [r + s for r in "AKQJT98765432" for s in "shdc"]
COMBOS = [(DECK[i], DECK[j])
          for i in range(52) for j in range(i + 1, 52)][::-1]
COMBO_INDEX = {frozenset(c): i for i, c in enumerate(COMBOS)}


def combo_key(hand):
    """'Th 9h', 'Th9h' or ('Th','9h') -> the key the index is built on."""
    if isinstance(hand, (list, tuple)):
        parts = list(hand)
    else:
        h = (hand or "").replace(" ", "")
        parts = [h[i:i + 2] for i in range(0, len(h), 2)]
    return frozenset(parts) if len(parts) == 2 else None

# Which street a spot is on, from how many cards are down. The board is the
# only thing that says this: an empty board is preflop however long the
# action sequence, and five cards is the river however short it is.
STREET_OF = {0: "preflop_actions", 3: "flop_actions",
             4: "turn_actions", 5: "river_actions"}


def cards(board):
    """'Jh3h2s' or 'Jh 3h 2s' -> ['Jh', '3h', '2s']."""
    b = (board or "").replace(" ", "")
    return [b[i:i + 2] for i in range(0, len(b), 2)]


def canon_board(board):
    """
    One spelling per board, so the cache holds one row per flop.

    GTO Wizard accepts the cards in any order, which means the same flop can
    be asked for four ways and cached four times. The flop is sorted by rank
    descending -- the order their own URLs use -- and the turn and river
    follow in the order they were dealt, because those are not
    interchangeable with anything.
    """
    c = cards(board)
    if len(c) < 3:
        return "".join(c)
    flop = sorted(c[:3], key=lambda x: RANK_ORDER.get(x[0].upper(), 0),
                  reverse=True)
    return "".join(flop + c[3:])


def street_field(board):
    """The spot field that this street's actions belong in."""
    return STREET_OF.get(len(cards(board)))


def start_node(n_players):
    """The node a table of this size begins at: the early seats, folded."""
    if not 2 <= n_players <= SEATS:
        return None
    return "-".join(["F"] * (SEATS - n_players))


def trim(payload):
    """
    Keep the strategy and throw away the furniture.

    A whole reply is 68KB, most of it blocker tables and player state nothing
    uses. What matters is which hands the solver plays each way, what each is
    worth, and the order the 169 numbers are in -- which the reply states
    itself in `simple_hand_counters`, so nothing here has to assume it.
    """
    hero = next((p for p in payload.get("players_info", [])
                 if p.get("player", {}).get("is_hero")), None)
    return {
        "hero": (hero or {}).get("player", {}).get("position"),
        "order": list((hero or {}).get("simple_hand_counters", {}).keys()),
        "actions": [{
            "code": a["action"]["code"],
            "type": a["action"]["type"],
            "betsize": a["action"].get("betsize"),
            "pot_frac": a["action"].get("betsize_by_pot"),
            "ends_hand": bool(a["action"].get("is_hand_end")),
            "next_street": bool(a["action"].get("next_street")),
            "freq": a.get("total_frequency"),
            "strategy": a.get("strategy"),
            "evs": a.get("evs"),
        } for a in payload.get("action_solutions", [])],
    }


def _class_of(combo):
    """('Th','9h') -> 'T9s'."""
    (r1, s1), (r2, s2) = combo
    hi, lo = ((r1, r2) if RANK_ORDER[r1] >= RANK_ORDER[r2] else (r2, r1))
    if r1 == r2:
        return hi + lo
    return hi + lo + ("s" if s1 == s2 else "o")


class Node:
    """One solved spot: who is to act, what they may do, and with what."""

    def __init__(self, key, data):
        self.key = as_spot(key)
        self.preflop, self.board = self.key[0], self.key[1]
        self.hero = data.get("hero")
        self.order = data.get("order") or []
        self.actions = data.get("actions") or []
        # Preflop the arrays are 169 long and indexed by hand class; postflop
        # they are 1326 and indexed by exact combination. The array says
        # which, so nothing has to assume it from the board.
        width = len(self.actions[0]["strategy"] or []) if self.actions else 0
        self.per_combo = width == 1326
        self._index = ({} if self.per_combo
                       else {h: i for i, h in enumerate(self.order)})

    def __repr__(self):
        where = self.preflop or "(first to act)"
        if self.board:
            where += " on " + self.board
            for part in self.key[2:]:
                if part:
                    where += " " + part
        return "<Node {} hero={} actions={}>".format(where, self.hero, self.codes)

    @property
    def codes(self):
        return [a["code"] for a in self.actions]

    def action(self, code):
        return next((a for a in self.actions if a["code"] == code), None)

    def _slots(self, hand):
        """
        Which array positions a hand occupies here.

        Preflop that is one slot for the class. Postflop it is one slot for
        an exact pair of cards, or every live combination of a class if only
        the class is known -- which is worth avoiding, because on a heart
        flop Th9h and Ts9s are not the same hand.
        """
        if not self.per_combo:
            i = self._index.get(hand)
            return [] if i is None else [i]
        key = combo_key(hand)
        if key is not None:
            i = COMBO_INDEX.get(key)
            return [] if i is None else [i]
        return [i for i, c in enumerate(COMBOS) if _class_of(c) == hand]

    def freq(self, combo):
        """How often the solver takes each action with this hand."""
        slots = self._slots(combo)
        if not slots:
            return {}
        return {a["code"]: (sum(a["strategy"][i] for i in slots) / len(slots)
                            if a["strategy"] else 0.0)
                for a in self.actions}

    def ev(self, combo):
        """
        What each action is worth with this hand, in bb.

        Preflop these are measured against folding, which is exactly zero.
        Postflop they are the chips the hand expects to end the pot with, so
        they are comparable between actions at the same node but are not
        differences from anything.
        """
        slots = self._slots(combo)
        if not slots:
            return {}
        out = {}
        for a in self.actions:
            if not a["evs"]:
                out[a["code"]] = None
                continue
            out[a["code"]] = sum(a["evs"][i] for i in slots) / len(slots)
        return out

    def best(self, combo):
        evs = {k: v for k, v in self.ev(combo).items() if v is not None}
        return max(evs, key=evs.get) if evs else None

    def loss(self, combo, code):
        """bb given up by taking this action instead of the best one."""
        evs = {k: v for k, v in self.ev(combo).items() if v is not None}
        if not evs or code not in evs:
            return None
        return max(evs.values()) - evs[code]

    def child(self, code):
        """
        The spot after this action, within the same street.

        None when the hand ends, and None when the street does -- because the
        next street needs a card this solution cannot know. The caller has the
        real board and supplies it with `next_street`.
        """
        a = self.action(code)
        if a is None or a["ends_hand"] or a["next_street"]:
            return None
        field = street_field(self.board)
        if field is None:
            return None
        i = ("preflop_actions", "board", "flop_actions", "turn_actions",
             "river_actions").index(field)
        parts = list(self.key)
        parts[i] = (parts[i] + "-" + code) if parts[i] else code
        return tuple(parts)

    def next_street(self, code, board):
        """
        The spot after an action that turned a card, given the real board.

        The runout is a fact about the hand, not about the solution, so it
        comes from the hand history rather than from here.
        """
        a = self.action(code)
        if a is None or a["ends_hand"] or not a["next_street"]:
            return None
        field = street_field(self.board)
        if field is None:
            return None
        i = ("preflop_actions", "board", "flop_actions", "turn_actions",
             "river_actions").index(field)
        parts = list(self.key)
        parts[i] = (parts[i] + "-" + code) if parts[i] else code
        parts[1] = canon_board(board)
        return tuple(parts)

    def nearest_raise(self, size_bb):
        """
        The solver sizing closest to a real one, and how far it had to move.

        Real players do not use solver sizings -- a pool that opens 3bb has
        to be scored at a node offering 2.5bb. Snapping is unavoidable; the
        gap is returned so that a 5bb open being priced as a 2.5bb one is
        visible rather than silent.
        """
        best, gap = None, None
        for a in self.actions:
            if a["type"] != "RAISE":
                continue
            try:
                size = float(a["betsize"])
            except (TypeError, ValueError):
                continue
            d = abs(size - size_bb)
            if gap is None or d < gap:
                best, gap = a["code"], d
        if best is None:
            return None, None
        return best, gap / max(size_bb, 1e-9)


class Solver:
    """Solved spots, from the cache when possible and the site when not."""

    # Which class a fetched payload is wrapped in. Named here rather than
    # written into `node` so another game can supply its own reader without
    # copying the caching, fetching and re-authorising around it.
    node_class = Node

    def __init__(self, gametype=NL25_6MAX, depth="100", db=DEFAULT_DB,
                 use_chrome=False, log=say, polite_ms=350, headless=False,
                 memory_nodes=250, store=True):
        self.gametype, self.depth = gametype, depth
        # Whether a fetched payload is written to the cache. It always is for
        # Hold'em, where a node is four 169-float arrays and keeping it is
        # what turns scoring a database into a lookup. It is not always right
        # for a game whose arrays are a thousand times longer: there, the
        # caller reads what it needs out of the reply and keeps that instead,
        # and storing the reply as well would be gigabytes to no purpose.
        self.store = store
        self.cache = Cache(db)
        self.browser = Browser(use_chrome=use_chrome, log=log,
                               headless=headless)
        # Postflop nodes carry four 1326-float arrays each. Keeping every one
        # ever touched is how a run over a few thousand spots eats the
        # machine; they are all on disk anyway, so only the recent ones stay.
        self.memory_nodes = memory_nodes
        self.log, self.polite_ms = log, polite_ms
        self.fetched = 0
        self._nodes = {}
        self._auth = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.browser.close()
        self.cache.close()

    def cached(self):
        return self.cache.keys(self.gametype, self.depth)

    def node(self, key="", fetch=True):  # key: preflop string, or a 5-part spot
        """
        A solved spot. Cached ones cost nothing; the rest are fetched.

        With `fetch=False` a missing node is recorded as wanted and None
        comes back, which is how a program can read a whole database of hands
        offline and afterwards fetch exactly the spots those hands reached.
        """
        key = as_spot(key)
        if key in self._nodes:
            return self._nodes[key]
        data = self.cache.get(self.gametype, self.depth, key)
        if data is None:
            if not fetch:
                return None
            data = self._fetch(key)
            if data is None:
                return None
        node = self.node_class(key, data)
        if len(self._nodes) >= self.memory_nodes:
            self._nodes.clear()
        self._nodes[key] = node
        return node

    def _fetch(self, key):
        """
        One spot from the site, over plain HTTP wherever possible.

        A browser is opened only to obtain a token, and only when there is
        not already a good one saved. Everything else is a GET with a header,
        which costs no memory, needs no window, and cannot crash a renderer.
        """
        spot = as_spot(key)
        params = {"gametype": self.gametype, "depth": self.depth, "stacks": "",
                  "preflop_actions": spot[0], "board": spot[1],
                  "flop_actions": spot[2], "turn_actions": spot[3],
                  "river_actions": spot[4]}
        payload = self._get(params)
        if payload is None:
            return None
        data = trim(payload)
        if self.store:
            self.cache.put(self.gametype, self.depth, spot, data)
        self.fetched += 1
        return data

    def _get(self, params, retry=True):
        if self._auth is None:
            self._auth = load_token()
        if self._auth is None and not self._browser_token():
            return None
        url = API + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=dict(self._auth))
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code in (204, 403, 404):
                return None            # a spot the solution does not have
            if err.code == 401 and retry:
                # Expired. Get a fresh one the only way there is, then carry
                # on -- one browser start per token, not per request.
                self.log("token expired, re-authorising")
                self._auth = None
                if self._browser_token():
                    return self._get(params, retry=False)
            return None
        except (urllib.error.URLError, ValueError, TimeoutError):
            return None

    def _browser_token(self):
        self.browser.open(
            "https://app.gtowizard.com/solutions?gametype={}&depth={}"
            .format(self.gametype, self.depth))
        if not self.browser.auth:
            self.log("no token -- run: python gtow.py login")
            return False
        self._auth = dict(self.browser.auth)
        self.browser.close()          # the token is what mattered, not the window
        return True

    def want(self, key, hits=1):
        self.cache.want(self.gametype, self.depth, key, hits)

    def fetch_wanted(self, limit=None):
        """Fetch the nodes that reading real hands turned out to need."""
        todo = self.cache.wanted(self.gametype, self.depth)[:limit]
        for i, (key, hits) in enumerate(todo, 1):
            self.node(key)
            if i % 10 == 0:
                self.log("  {}/{} wanted nodes fetched".format(i, len(todo)))
        return len(todo)

    def walk(self, start="", max_nodes=250):
        """
        Breadth-first over the tree, fetching as it goes.

        The replies say where to stop: an action that ends the hand or turns
        a card has no preflop child worth asking for.
        """
        queue, seen, done = [start], {start}, 0
        while queue and done < max_nodes:
            key = queue.pop(0)
            node = self.node(key)
            done += 1
            if node is None:
                continue
            for code in node.codes:
                child = node.child(code)
                if child and child not in seen:
                    seen.add(child)
                    queue.append(child)
        return done
