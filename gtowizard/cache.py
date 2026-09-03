"""
Where solved nodes are kept, so each one is only ever asked for once.

Preflop at six handed is a small tree -- a few hundred distinct spots. Asking
the solver per hand would mean thousands of requests to answer questions that
have a few hundred distinct answers between them. Fetched once and stored,
scoring a whole database of hands becomes a lookup.

The store is a table in whatever SQLite file it is pointed at, keyed by game,
depth and the action sequence, so several games and stack depths can live
side by side without colliding.
"""

import json
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "hands.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS solutions (
  gametype TEXT, depth TEXT, preflop_actions TEXT,
  board TEXT DEFAULT '', flop_actions TEXT DEFAULT '',
  turn_actions TEXT DEFAULT '', river_actions TEXT DEFAULT '',
  hero_position TEXT, payload TEXT, fetched_at TEXT,
  PRIMARY KEY (gametype, depth, preflop_actions, board,
               flop_actions, turn_actions, river_actions));
CREATE TABLE IF NOT EXISTS wanted_nodes (
  gametype TEXT, depth TEXT, preflop_actions TEXT, hits INT,
  PRIMARY KEY (gametype, depth, preflop_actions));
"""


# The five things that identify a spot. Preflop-only spots leave the last
# four empty, which is why the preflop cache built before postflop existed
# still addresses correctly.
PARTS = ("preflop_actions", "board", "flop_actions", "turn_actions",
         "river_actions")


def as_spot(node):
    """
    A spot key, however it was written.

    Preflop-only callers pass the action string they always passed, and get
    the same row back, because a preflop spot is one with no board.
    """
    if isinstance(node, str):
        return (node, "", "", "", "")
    node = tuple(node)
    return node + ("",) * (5 - len(node))


class Cache:
    """Solved nodes on disk, addressed by game, depth and the road to them."""

    def __init__(self, db=DEFAULT_DB):
        self.con = sqlite3.connect(str(db))
        self._migrate()
        self.con.executescript(SCHEMA)

    def _migrate(self):
        """
        Widen an older cache keyed on the preflop line alone.

        The first version of this table predates postflop and its primary key
        is (gametype, depth, preflop_actions), which would collide the moment
        two boards share a preflop line. SQLite cannot alter a primary key, so
        the table is rebuilt -- and the rows already fetched are kept, because
        they are a thousand nodes that cost a browser session to gather.
        """
        cur = self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='solutions'").fetchone()
        if not cur:
            return
        cols = [c[1] for c in self.con.execute("PRAGMA table_info(solutions)")]
        if "board" in cols:
            return
        self.con.executescript("""
        ALTER TABLE solutions RENAME TO solutions_preflop_only;
        """ + SCHEMA + """
        INSERT INTO solutions (gametype, depth, preflop_actions, board,
            flop_actions, turn_actions, river_actions, hero_position,
            payload, fetched_at)
          SELECT gametype, depth, preflop_actions, '', '', '', '',
                 hero_position, payload, fetched_at
          FROM solutions_preflop_only;
        DROP TABLE solutions_preflop_only;
        """)
        self.con.commit()

    def get(self, gametype, depth, node):
        spot = as_spot(node)
        row = self.con.execute(
            "SELECT payload FROM solutions WHERE gametype=? AND depth=? AND "
            + " AND ".join(p + "=?" for p in PARTS),
            (gametype, depth) + spot).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, gametype, depth, node, data):
        spot = as_spot(node)
        self.con.execute(
            "INSERT OR REPLACE INTO solutions VALUES (?,?,?,?,?,?,?,?,?,"
            "datetime('now'))",
            (gametype, depth) + spot + (data.get("hero"), json.dumps(data)))
        self.con.commit()

    def keys(self, gametype, depth, preflop_only=False):
        """Every cached spot, as 5-part keys."""
        sql = ("SELECT " + ",".join(PARTS) + " FROM solutions "
               "WHERE gametype=? AND depth=?")
        if preflop_only:
            sql += " AND board=''"
        return {tuple(r) for r in self.con.execute(sql, (gametype, depth))}

    def all(self, gametype, depth, preflop_only=True):
        """
        Every cached spot at once, for work that reads the whole tree.

        Preflop only by default: postflop is one entry per board and reading
        it all into memory is a different order of thing.
        """
        sql = ("SELECT " + ",".join(PARTS) + ", payload FROM solutions "
               "WHERE gametype=? AND depth=?")
        if preflop_only:
            sql += " AND board=''"
        return {tuple(r[:5]): json.loads(r[5])
                for r in self.con.execute(sql, (gametype, depth))}

    def want(self, gametype, depth, node, hits=1):
        """
        Note a node something asked for and did not find.

        This is what makes fetching demand-driven: rather than walking the
        whole tree on the chance it is needed, the programs that read hands
        record the spots those hands actually reached, and only those get
        fetched.
        """
        self.con.execute(
            "INSERT INTO wanted_nodes VALUES (?,?,?,?) "
            "ON CONFLICT(gametype,depth,preflop_actions) DO UPDATE SET hits=?",
            (gametype, depth, node, hits, hits))
        self.con.commit()

    def wanted(self, gametype, depth):
        """Nodes asked for but never fetched, most-wanted first."""
        return [(n, h) for n, h in self.con.execute(
            "SELECT w.preflop_actions, w.hits FROM wanted_nodes w "
            "LEFT JOIN solutions s ON s.gametype=w.gametype AND s.depth=w.depth "
            "AND s.preflop_actions=w.preflop_actions "
            "WHERE w.gametype=? AND w.depth=? AND s.preflop_actions IS NULL "
            "ORDER BY w.hits DESC", (gametype, depth))]

    def close(self):
        self.con.close()
