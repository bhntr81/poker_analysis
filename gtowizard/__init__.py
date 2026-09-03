"""
GTO Wizard, as a library.

Solved poker spots, fetched once through a real logged-in browser and kept on
disk afterwards, so any program can ask what the solver does in a spot without
knowing anything about browsers, tokens or endpoints.

    from gtowizard import Solver

    with Solver() as gto:
        spot = gto.node("F-F")       # folded to the cutoff
        spot.hero                    # 'CO'
        spot.freq("AKs")             # what the solver does with it
        spot.loss("AA", "F")         # bb thrown away folding aces there

Reading cached spots needs no browser, no login and no network -- only a spot
never seen before opens one, and then once for the whole session. That is
what makes it usable inside a loop over thousands of hands.

The pieces, if a program needs them directly:

    Solver        ask for spots; cache first, site second
    Node          one spot: who acts, what they may do, frequencies and EVs
    Cache         the sqlite store, and the record of spots still wanted
    Browser       the logged-in Chromium and the token it carries
    discover      find the endpoint again if GTO Wizard ever moves it

Logging in belongs to the user. Nothing here reads, fills or stores a
password; it watches the address bar and takes the Authorization header from
a request the app makes for itself.
"""

from .browser import Browser, HOME, is_logged_in, wait_for_login
from .cache import Cache, DEFAULT_DB
from .solver import API, NL25_6MAX, Node, SEATS, Solver, start_node, trim
from .plo import (DEPTHS, PLO100, PLO100_ANTE, PLONode, PLOSolver,
                  build_gametype, check_order, combo_index, describe,
                  nearest_depth)

__all__ = [
    "Solver", "Node", "Cache", "Browser",
    "NL25_6MAX", "SEATS", "API", "HOME", "DEFAULT_DB",
    "start_node", "trim", "is_logged_in", "wait_for_login",
    # Pot Limit Omaha: the same tree, a different game in it.
    "PLOSolver", "PLONode", "PLO100", "PLO100_ANTE", "DEPTHS",
    "build_gametype", "nearest_depth", "combo_index", "check_order",
    "describe",
]
