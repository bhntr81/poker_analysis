"""
The tracker as a desktop window: no browser, no server, no terminal.

`gui.py` served the same thing as a local page, and it worked, but a page is
not an application -- it has no window of its own, it needs a terminal left
running behind it, and it arrives in a tab beside everything else. This is
the same tool as a program you open.

Tk rather than anything better looking, because the alternative is a
dependency and this project has none. Tk fights being made dark: its default
Windows themes hand their drawing to native controls that ignore colour
settings, so the whole interface is built on `clam`, the one bundled theme
that draws its own widgets and therefore does as it is told.

**The filter is built by `query.build`, from the flags the command line
takes.** Three front ends now share one definition of what "3-bet pots in
position" means. They would not stay agreeing if any of them assembled its
own WHERE clause, and the disagreement would be silent -- so `--check`
asserts the equality rather than trusting it.

    python app.py           open it
    python app.py --check   the window and the command line agree
"""

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

import sqlite3

import query
from stats import BY_KEY, STATS

# Packaged as a single executable, PyInstaller unpacks the code into a
# temporary directory and deletes it afterwards, so `__file__` points
# somewhere that will not exist tomorrow. The database is not part of the
# program -- it is the user's data -- and lives beside the executable.
HERE = (Path(sys.executable).parent if getattr(sys, "frozen", False)
        else Path(__file__).parent)
DB = HERE / "hands.db"
query.DB = DB

# One palette, so a colour is changed in one place. The line colours are the
# same four the graph has always used.
BG, PANEL, EDGE = "#14161a", "#1b1e24", "#2a2f38"
INK, DIM, ACCENT = "#d8dbe0", "#8b929c", "#4c9aff"
GOOD, BAD, WARN = "#22a35a", "#d1443c", "#b8892a"
LINE = {"total": "#22a35a", "showdown": "#2f7fd6",
        "nonshowdown": "#d1443c", "allin_ev": "#e0b020"}

POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
STREETS = ("preflop", "flop", "turn", "river")
POT_TYPES = ("unopened", "limped", "raised", "3bet", "4bet")
# Who the other seat is. "The big blind against a button open" is the shape
# most real questions have, and it needs both halves of the matchup named.
VS_SIDE = [("--vs-hero", "vs me"), ("--vs-pool", "vs the pool")]
SITUATIONS = [("--ip", "in position"), ("--oop", "out of position"),
              ("--pfa", "was the raiser"), ("--vs-pfa", "facing the raiser"),
              ("--multiway", "multiway"), ("--headsup", "heads up"),
              ("--allin", "all-in")]
# Turning one of these on turns its opposite off, or the filter selects
# nothing and looks broken rather than contradictory.
OPPOSITES = {"--hero": "--pool", "--pool": "--hero", "--ip": "--oop",
             "--oop": "--ip", "--multiway": "--headsup",
             "--headsup": "--multiway",
             "--vs-hero": "--vs-pool", "--vs-pool": "--vs-hero"}


def dark(root):
    """Make Tk dark, which it does not want to be."""
    style = ttk.Style(root)
    # `clam` draws its own widgets. `vista` and `winnative` delegate to the
    # operating system, which draws them light whatever it is told.
    style.theme_use("clam")
    root.configure(background=BG)
    style.configure(".", background=BG, foreground=INK, fieldbackground=PANEL,
                    bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE,
                    troughcolor=BG, focuscolor=ACCENT, insertcolor=INK)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=INK)
    style.configure("Dim.TLabel", background=BG, foreground=DIM)
    style.configure("Head.TLabel", background=BG, foreground=DIM,
                    font=("Segoe UI", 8, "bold"))
    style.configure("Title.TLabel", background=BG, foreground=INK,
                    font=("Segoe UI", 11, "bold"))
    style.configure("TCheckbutton", background=BG, foreground=DIM)
    style.map("TCheckbutton",
              foreground=[("selected", ACCENT), ("active", INK)],
              background=[("active", BG)])
    style.configure("TButton", background=PANEL, foreground=INK,
                    bordercolor=EDGE, focusthickness=0, padding=(10, 4))
    style.map("TButton", background=[("active", EDGE)])
    style.configure("TEntry", fieldbackground=PANEL, foreground=INK,
                    bordercolor=EDGE, insertcolor=INK)
    style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                    foreground=INK, arrowcolor=DIM, bordercolor=EDGE)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
              foreground=[("readonly", INK)])
    # The dropdown LIST inside a combobox is a Tk listbox, not a ttk widget,
    # so ttk styling never reaches it and it has to be coloured by option.
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", BG)
    style.configure("TNotebook", background=BG, bordercolor=EDGE)
    style.configure("TNotebook.Tab", background=BG, foreground=DIM,
                    padding=(14, 6), bordercolor=EDGE)
    style.map("TNotebook.Tab", background=[("selected", PANEL)],
              foreground=[("selected", INK)])
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                    foreground=INK, bordercolor=EDGE, rowheight=22)
    style.configure("Treeview.Heading", background=BG, foreground=DIM,
                    relief="flat", font=("Segoe UI", 8, "bold"))
    style.map("Treeview.Heading", background=[("active", EDGE)])
    style.map("Treeview", background=[("selected", "#233047")],
              foreground=[("selected", INK)])
    style.configure("TSeparator", background=EDGE)
    style.configure("Vertical.TScrollbar", background=PANEL,
                    troughcolor=BG, bordercolor=BG, arrowcolor=DIM)
    style.configure("Horizontal.TScrollbar", background=PANEL,
                    troughcolor=BG, bordercolor=BG, arrowcolor=DIM)
    return style


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.pack(fill="both", expand=True)
        self.con = sqlite3.connect(DB, check_same_thread=False)
        self.flags = {}
        self.multi = {"pos": set(), "vs": set(), "street": set(),
                      "pot": set(), "board": set()}
        self.results = queue.Queue()
        self.pending = 0
        self._build()
        self.after(80, self._drain)
        self.refresh()

    # ---- layout -------------------------------------------------------
    def _build(self):
        head = ttk.Frame(self)
        head.pack(fill="x", padx=14, pady=(10, 6))
        ttk.Label(head, text="poker_analysis", style="Title.TLabel").pack(side="left")
        self.sub = ttk.Label(head, text="", style="Dim.TLabel")
        self.sub.pack(side="left", padx=10)
        self.status = ttk.Label(head, text="", style="Dim.TLabel")
        self.status.pack(side="right")
        ttk.Separator(self).pack(fill="x")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        rail = ttk.Frame(body, width=250)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        ttk.Separator(body, orient="vertical").pack(side="left", fill="y")
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)

        self._rail(rail)
        self._views(right)

    def _section(self, parent, title):
        ttk.Label(parent, text=title.upper(), style="Head.TLabel").pack(
            anchor="w", padx=12, pady=(12, 3))
        f = ttk.Frame(parent)
        f.pack(fill="x", padx=12)
        return f

    def _toggles(self, parent, items, per_row=2):
        row = None
        for i, (flag, label) in enumerate(items):
            if i % per_row == 0:
                row = ttk.Frame(parent)
                row.pack(fill="x")
            var = tk.BooleanVar()
            self.flags[flag] = var
            ttk.Checkbutton(row, text=label, variable=var,
                            command=lambda f=flag: self._toggled(f)).pack(
                side="left", padx=(0, 8))

    def _chips(self, parent, group, values, per_row=4):
        row = None
        for i, v in enumerate(values):
            if i % per_row == 0:
                row = ttk.Frame(parent)
                row.pack(fill="x")
            var = tk.BooleanVar()
            ttk.Checkbutton(
                row, text=v, variable=var,
                command=lambda g=group, val=v, x=var: self._chip(g, val, x)
            ).pack(side="left", padx=(0, 6))

    def _rail(self, rail):
        canvas = tk.Canvas(rail, bg=BG, highlightthickness=0, width=246)
        bar = ttk.Scrollbar(rail, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", width=232)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        f = self._section(inner, "who")
        self._toggles(f, [("--hero", "hero"), ("--pool", "pool")])

        f = self._section(inner, "site & stake")
        self.site = self._combo(f, ["any site"])
        self.stake = self._combo(f, ["any stake"])
        self.player = self._combo(f, ["any player"])

        f = self._section(inner, "my position")
        self._chips(f, "pos", POSITIONS)

        f = self._section(inner, "against  (heads-up pots only)")
        self._chips(f, "vs", POSITIONS)
        self._toggles(f, VS_SIDE)
        f = self._section(inner, "street")
        self._chips(f, "street", STREETS)
        f = self._section(inner, "pot type")
        self._chips(f, "pot", POT_TYPES, per_row=3)
        f = self._section(inner, "situation")
        self._toggles(f, SITUATIONS)
        f = self._section(inner, "flop texture")
        self._chips(f, "board", list(query.BOARDS), per_row=3)

        f = self._section(inner, "stack depth (bb)")
        self.deep = self._entry(f, "at least")
        self.short = self._entry(f, "less than")
        f = self._section(inner, "dates  (yyyy-mm-dd)")
        self.since = self._entry(f, "from")
        self.until = self._entry(f, "to")
        f = self._section(inner, "raw sql over decisions")
        self.where = self._entry(f, "", width=26)
        ttk.Frame(inner, height=16).pack()

    def _combo(self, parent, values):
        c = ttk.Combobox(parent, values=values, state="readonly")
        c.current(0)
        c.pack(fill="x", pady=2)
        c.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        return c

    def _entry(self, parent, label, width=10):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        if label:
            ttk.Label(row, text=label, style="Dim.TLabel", width=9).pack(side="left")
        e = ttk.Entry(row, width=width)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.refresh())
        e.bind("<FocusOut>", lambda ev: self.refresh())
        return e

    def _views(self, right):
        bar = ttk.Frame(right)
        bar.pack(fill="x", padx=12, pady=(10, 4))
        self.by = ttk.Combobox(bar, values=list(query.DIMENSIONS),
                               state="readonly", width=12)
        self.by.current(0)
        self.by.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.by_label = ttk.Label(bar, text="split by", style="Dim.TLabel")

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh())
        self.bar = bar

        self.filter_line = ttk.Label(right, text="", style="Dim.TLabel")
        self.filter_line.pack(anchor="w", padx=14, pady=(0, 8))

        self.tabs = {}
        for name in ("stats", "report", "results", "graph", "hands"):
            frame = ttk.Frame(self.nb)
            self.nb.add(frame, text=name)
            self.tabs[name] = frame
        self.tree = {}
        for name in ("stats", "report", "results", "hands"):
            self.tree[name] = self._table(self.tabs[name])
        self.canvas = tk.Canvas(self.tabs["graph"], bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw_graph())
        self.series = None
        self.tree["hands"].bind("<Double-1>", self._open_hand)
        self.tree["hands"].bind("<Return>", self._open_hand)

    def _table(self, parent):
        wrap = ttk.Frame(parent)
        wrap.pack(fill="both", expand=True)
        tv = ttk.Treeview(wrap, show="headings", selectmode="browse")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vs.set)
        tv.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        tv.tag_configure("group", foreground=DIM)
        tv.tag_configure("thin", foreground=WARN)
        tv.tag_configure("pos", foreground=GOOD)
        tv.tag_configure("neg", foreground=BAD)
        tv.tag_configure("note", foreground=DIM)
        return tv

    # ---- filter -------------------------------------------------------
    def _toggled(self, flag):
        other = OPPOSITES.get(flag)
        if other and self.flags[flag].get():
            self.flags[other].set(False)
        self.refresh()

    def _chip(self, group, value, var):
        (self.multi[group].add if var.get() else self.multi[group].discard)(value)
        self.refresh()

    def argv(self):
        """The window's state as the argument list `query.build` understands."""
        argv = [f for f, v in self.flags.items() if v.get()]
        for group, flag in (("pos", "--pos"), ("vs", "--vs"),
                            ("street", "--street"), ("pot", "--pot"),
                            ("board", "--board")):
            if self.multi[group]:
                argv += [flag, ",".join(sorted(self.multi[group]))]
        for widget, flag, blank in ((self.site, "--site", "any site"),
                                    (self.stake, "--stake", "any stake"),
                                    (self.player, "--player", "any player")):
            v = widget.get()
            if v and v != blank:
                argv += [flag, v]
        for widget, flag in ((self.deep, "--deep"), (self.short, "--short"),
                             (self.since, "--since"), (self.until, "--until"),
                             (self.where, "--where")):
            v = widget.get().strip()
            if v:
                argv += [flag, v]
        return argv

    # ---- running the query, off the interface thread -------------------
    def refresh(self):
        view = self.nb.tab(self.nb.select(), "text") if self.tabs else "stats"
        if view in ("report", "results"):
            self.by_label.pack(side="left", padx=(0, 6))
            self.by.pack(side="left")
        else:
            self.by_label.pack_forget()
            self.by.pack_forget()
        try:
            argv = self.argv()
            where, label, parts = query.build(argv)
        except SystemExit as e:
            self.filter_line.configure(text=str(e))
            return
        self.filter_line.configure(text="filter: " + label)
        self.pending += 1
        token = self.pending
        self.status.configure(text="working…")
        threading.Thread(target=self._work, daemon=True,
                         args=(token, view, where, label, parts,
                               self.by.get())).start()

    def _work(self, token, view, where, label, parts, dim):
        """
        Every query runs here, never on the interface thread.

        A window that stops repainting while it thinks looks broken, and some
        of these take seconds: the graph prices all-ins the first time it sees
        them. So the work happens on a thread and the answer is posted back
        through a queue, with a token so that a slow answer to a filter the
        user has already changed is discarded rather than drawn.
        """
        con = sqlite3.connect(DB)
        try:
            out = {"view": view}
            if view == "stats":
                out["n"], out["rows"] = query.stats_of(con, where)
            elif view == "report":
                expr, order = query.DIMENSIONS[dim]
                cols = query.DEFAULT_COLUMNS
                grid = {c: query.rates_by(con, BY_KEY[c], expr, where)
                        for c in cols}
                counts = query.rates_by(con, BY_KEY["vpip"], expr, where)
                keys = sorted({k for g in grid.values() for k in g},
                              key=lambda k: order(k) if k is not None else "")
                out.update(dim=dim, cols=cols, grid=grid, counts=counts,
                           keys=keys)
            elif view == "results":
                pairs = query.matching_seats(con, where)
                out["totals"] = query.results_of(con, pairs) if pairs else None
            elif view == "hands":
                out["rows"] = con.execute(
                    f"SELECT DISTINCT d.hand_id, d.seat, d.played_at, d.site,"
                    f" d.bb, d.position, d.combo, d.board, s.net_bb "
                    f"FROM (SELECT * FROM decisions WHERE {where}) d "
                    f"LEFT JOIN spots s ON s.hand_id=d.hand_id "
                    f"AND s.seat=d.seat ORDER BY d.played_at DESC LIMIT 500"
                ).fetchall()
            elif view == "graph":
                out["series"] = self._series(con, where)
            if not self._any(out):
                out["why"] = query.why_empty(con, parts)
        except sqlite3.Error as e:
            out = {"view": view, "error": f"SQL: {e}"}
        finally:
            con.close()
        self.results.put((token, out))

    @staticmethod
    def _any(out):
        return bool(out.get("n") or out.get("rows") or out.get("totals")
                    or out.get("keys") or out.get("series"))

    def _series(self, con, where):
        pairs = query.matching_seats(con, where)
        if len(pairs) < 2:
            return None
        query.select_into(con, pairs)
        hands, adj, skipped = query.adjusted(con, pairs)
        if len(hands) < 2:
            return None
        s = {k: [] for k in LINE}
        total = sd = nsd = ev = 0.0
        for _when, net, was_sd, ev_net in hands:
            total += net or 0.0
            ev += ev_net or 0.0
            if was_sd:
                sd += net or 0.0
            else:
                nsd += net or 0.0
            s["total"].append(total)
            s["showdown"].append(sd)
            s["nonshowdown"].append(nsd)
            s["allin_ev"].append(ev)
        s["_note"] = (f"{len(hands):,} hands · {adj} all-in pots at equity"
                      + (f" · {skipped} unadjusted" if skipped else ""))
        return s

    def _drain(self):
        try:
            while True:
                token, out = self.results.get_nowait()
                if token == self.pending:
                    self.status.configure(text="")
                    self._render(out)
        except queue.Empty:
            pass
        self.after(80, self._drain)

    # ---- drawing ------------------------------------------------------
    def _render(self, out):
        view = out["view"]
        if view == "graph":
            self.series = out.get("series")
            self._draw_graph(out.get("why") or out.get("error"))
            return
        tv = self.tree[view]
        tv.delete(*tv.get_children())
        if out.get("error") or (out.get("why") and view != "report"):
            tv.configure(columns=("msg",))
            tv.heading("msg", text="")
            tv.column("msg", width=900, anchor="w")
            msg = out.get("error") or "nothing matches"
            tv.insert("", "end", values=(msg,), tags=("neg",))
            if out.get("why"):
                tv.insert("", "end", values=(out["why"],), tags=("note",))
            return
        getattr(self, "_render_" + view)(tv, out)

    def _cols(self, tv, cols, widths, anchors=None):
        tv.configure(columns=cols)
        for i, c in enumerate(cols):
            tv.heading(c, text=c)
            tv.column(c, width=widths[i], anchor=(anchors or {}).get(c, "e"),
                      stretch=(i == 0))

    def _render_stats(self, tv, out):
        self._cols(tv, ("stat", "value", "±", "n"), (300, 90, 70, 110),
                   {"stat": "w"})
        group = None
        for r in out["rows"]:
            if r["group"] != group:
                group = r["group"]
                tv.insert("", "end", values=(group.upper(), "", "", ""),
                          tags=("group",))
            tv.insert("", "end", tags=("thin",) if r["n"] < 30 else (),
                      values=(r["label"], f"{r['pct']:.1f}%",
                              f"±{r['band']:.0f}", f"{r['n']:,}"))

    def _render_report(self, tv, out):
        cols = ["by"] + [BY_KEY[c].label for c in out["cols"]] + ["n"]
        self._cols(tv, tuple(cols), [130] + [95] * len(out["cols"]) + [80],
                   {"by": "w"})
        if not out["keys"]:
            tv.insert("", "end", values=["nothing matches"] + [""] * len(cols[1:]),
                      tags=("neg",))
            if out.get("why"):
                tv.insert("", "end",
                          values=[out["why"]] + [""] * len(cols[1:]),
                          tags=("note",))
            return
        for k in out["keys"]:
            row = [str(k)]
            thin = False
            for c in out["cols"]:
                n, kk = out["grid"][c].get(k, (0, 0))
                row.append("–" if not n else f"{100 * kk / n:.1f}%")
                thin = thin or (0 < n < 30)
            row.append(f"{out['counts'].get(k, (0, 0))[0]:,}")
            tv.insert("", "end", values=row, tags=("thin",) if thin else ())

    def _render_results(self, tv, out):
        self._cols(tv, ("figure", "value"), (320, 220), {"figure": "w"})
        t = out["totals"]
        rows = [("hands", f"{t['hands']:,}"),
                ("net", f"{t['net_bb']:+,.1f} bb"),
                ("in money", f"{t['money']:+,.2f}"),
                ("per 100 hands", f"{t['bb100']:+.1f} bb/100"),
                ("error on that", f"±{t['error']:.0f} bb/100"),
                ("saw a flop", f"{t['saw_flop']:,}"),
                ("won at showdown", f"{t['wtsd']:,}")]
        for name, val in rows:
            tag = ()
            if name in ("net", "per 100 hands"):
                tag = ("pos",) if val.startswith("+") else ("neg",)
            tv.insert("", "end", values=(name, val), tags=tag)
        tv.insert("", "end", values=("", ""))
        tv.insert("", "end", tags=("note",), values=(
            "one hand's result has a standard deviation near 11.7bb, so the "
            "error on a win rate is about 1170/√n", ""))

    def _render_hands(self, tv, out):
        self._cols(tv, ("when", "site", "bb", "pos", "hand", "net bb", "board"),
                   (140, 90, 60, 60, 70, 90, 200),
                   {"when": "w", "site": "w", "pos": "w", "hand": "w",
                    "board": "w"})
        self._hand_ids = {}
        for hid, seat, when, site, bb, pos, combo, board, net in out["rows"]:
            iid = tv.insert("", "end", values=(
                (when or "")[:16], site, f"{bb:g}" if bb else "",
                pos or "", combo or "–",
                f"{net:+.1f}" if net is not None else "",
                board or ""),
                tags=("pos",) if (net or 0) > 0 else
                     ("neg",) if (net or 0) < 0 else ())
            self._hand_ids[iid] = (hid, seat)
        if out["rows"]:
            tv.insert("", "end", values=("", "", "", "", "", "", ""))
            tv.insert("", "end", tags=("note",),
                      values=("double-click a hand to replay it", "", "", "",
                              "", "", ""))

    def _open_hand(self, _event):
        tv = self.tree["hands"]
        sel = tv.selection()
        if not sel or sel[0] not in getattr(self, "_hand_ids", {}):
            return
        hid, seat = self._hand_ids[sel[0]]
        HandWindow(self, self.con, hid, seat)

    # ---- the graph, drawn rather than served ---------------------------
    def _draw_graph(self, message=None):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        s = self.series
        if not s:
            c.create_text(w / 2, h / 2, fill=DIM, font=("Segoe UI", 10),
                          text=message or "not enough hands to draw a line")
            return
        L, R, T, B = 70, 210, 30, 40
        n = len(s["total"])
        lo = min(min(v) for k, v in s.items() if k in LINE)
        hi = max(max(v) for k, v in s.items() if k in LINE)
        lo, hi = min(lo, 0.0), max(hi, 0.0)
        span = (hi - lo) or 1.0
        x = lambda i: L + (w - L - R) * i / max(1, n - 1)
        y = lambda v: T + (h - T - B) * (1 - (v - lo) / span)

        for f in range(5):
            v = lo + span * f / 4
            c.create_line(L, y(v), w - R, y(v), fill=EDGE)
            c.create_text(L - 8, y(v), text=f"{v:,.0f}", fill=DIM,
                          anchor="e", font=("Segoe UI", 8))
        c.create_line(L, y(0), w - R, y(0), fill=DIM, dash=(3, 3))
        for i, (key, colour) in enumerate(LINE.items()):
            pts = []
            for j, v in enumerate(s[key]):
                pts += [x(j), y(v)]
            if len(pts) >= 4:
                c.create_line(*pts, fill=colour, width=2, smooth=False)
            ly = T + 16 + i * 30
            c.create_line(w - R + 8, ly, w - R + 30, ly, fill=colour, width=3)
            c.create_text(w - R + 38, ly, anchor="w", fill=INK,
                          font=("Segoe UI", 9),
                          text=f"{key.replace('_', ' ')}  {s[key][-1]:+,.0f}")
        c.create_text((L + w - R) / 2, h - 14, fill=DIM,
                      font=("Segoe UI", 8), text=s.get("_note", ""))

    # ---- what this database holds -------------------------------------
    def load_options(self):
        one = lambda sql: [r[0] for r in self.con.execute(sql)
                           if r[0] is not None]
        sites = one("SELECT DISTINCT site FROM decisions ORDER BY 1")
        self.site.configure(values=["any site"] + sites)
        self.stake.configure(values=["any stake"] + [
            f"{v:g}" for v in one("SELECT DISTINCT bb FROM decisions ORDER BY 1")])
        self.player.configure(values=["any player"] + one(
            "SELECT player FROM decisions WHERE player IS NOT NULL "
            "GROUP BY player HAVING COUNT(DISTINCT hand_id) >= 100 "
            "ORDER BY COUNT(DISTINCT hand_id) DESC LIMIT 200"))
        hands = self.con.execute("SELECT COUNT(*) FROM hands").fetchone()[0]
        self.sub.configure(text=f"{' · '.join(sites)}   {hands:,} hands")


class HandWindow(tk.Toplevel):
    """One hand, replayed in a window of its own."""

    def __init__(self, master, con, hand_id, seat):
        super().__init__(master)
        self.configure(background=BG)
        self.title(f"hand {hand_id}")
        self.geometry("760x620")
        d = query.hand_detail(con, hand_id, seat)
        mono = tkfont.Font(family="Consolas", size=10)
        text = tk.Text(self, background=BG, foreground=INK, borderwidth=0,
                       font=mono, padx=16, pady=12, wrap="none",
                       insertbackground=BG)
        bar = ttk.Scrollbar(self, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=bar.set)
        text.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        for name, colour in (("dim", DIM), ("hi", ACCENT), ("good", GOOD),
                             ("bad", BAD), ("head", INK)):
            text.tag_configure(name, foreground=colour)
        text.tag_configure("head", font=(mono.cget("family"), 10, "bold"))

        if d is None:
            text.insert("end", "hand not found")
            text.configure(state="disabled")
            return
        stake = f"${d['sb']}/${d['bb']}" if d["bb"] else "-"
        text.insert("end", f"{d['hand_id']}\n", "head")
        text.insert("end", f"{d['site']}  {d['fmt']}  {stake}  "
                           f"{d['played_at']}  {d['table']}\n\n", "dim")
        for s in d["seats"]:
            net = (s["won"] or 0) - (s["put_in"] or 0)
            mark = "*" if s["seat"] == seat else (">" if s["is_hero"] else " ")
            text.insert("end", f" {mark} {s['position'] or '?':4} "
                               f"{(s['name'] or '')[:18]:18} "
                               f"{s['stack'] or 0:9.2f}  "
                               f"{s['cards'] or 'not shown':>10}  ")
            text.insert("end", f"{net:+9.2f}\n", "good" if net > 0 else "bad")
        for st in d["streets"]:
            head = st["street"].upper()
            if st["board"]:
                head += f"  [{st['board']}]"
            first = st["actions"][0] if st["actions"] else None
            if first and first["pot_before"] is not None:
                head += f"   pot {first['pot_before']:.2f}"
            text.insert("end", f"\n{head}\n", "head")
            for a in st["actions"]:
                amt = f" {a['amount']:.2f}" if a["amount"] else ""
                text.insert("end", f"    {a['position'] or '?':4} "
                                   f"{(a['name'] or '')[:16]:16} "
                                   f"{a['verb']}{amt}\n")
        if d["pot"]:
            rake = f"   rake {d['rake']:.2f}" if d["rake"] else ""
            text.insert("end", f"\nTOTAL POT {d['pot']:.2f}{rake}\n", "dim")
        text.configure(state="disabled")


def check(db_path=DB):
    """
    The window and the command line must build the same filter.

    Same argument as `gui.py`'s check and for the same reason: three front
    ends over one database stay agreeing only if they share one definition of
    what a filter means. Here the window's own widgets are driven, so what is
    tested is the thing the user actually manipulates rather than a
    reimplementation of it.
    """
    fails = []
    root = tk.Tk()
    root.withdraw()
    dark(root)
    app = App(root)
    app.load_options()

    cases = [
        ({"flags": ["--hero", "--ip"], "pos": [], "vs": [],
          "street": ["flop"], "pot": ["3bet"], "board": []},
         ["--hero", "--ip", "--street", "flop", "--pot", "3bet"]),
        ({"flags": ["--pool"], "pos": ["BTN", "CO"], "vs": [], "street": [],
          "pot": [], "board": []},
         ["--pool", "--pos", "BTN,CO"]),
        # The matchup, which is the reason these columns exist.
        ({"flags": ["--pool", "--vs-pool"], "pos": ["BTN"], "vs": ["BB"],
          "street": [], "pot": ["3bet"], "board": []},
         ["--pool", "--vs-pool", "--pos", "BTN", "--vs", "BB",
          "--pot", "3bet"]),
        ({"flags": [], "pos": [], "vs": [], "street": [], "pot": [],
          "board": ["mono", "paired"]},
         ["--board", "mono,paired"]),
        ({"flags": [], "pos": [], "vs": [], "street": [], "pot": [],
          "board": []}, []),
    ]
    for state, argv in cases:
        for f, var in app.flags.items():
            var.set(f in state["flags"])
        for g in app.multi:
            app.multi[g] = set(state[g])
        a, _la, _pa = query.build(app.argv())
        b, _lb, _pb = query.build(argv)
        if sorted(a.split(" AND ")) != sorted(b.split(" AND ")):
            fails.append(f"{state} -> {a!r} but CLI gives {b!r}")
    print(f"window and command line agree  {len(cases) - len(fails)}/{len(cases)}")
    for f in fails:
        print(f"    {f}")

    # Every view must build its rows without raising, including on a filter
    # that matches nothing -- which is one click away at all times.
    con = sqlite3.connect(db_path)
    broke = []
    for view in ("stats", "report", "results", "hands", "graph"):
        for argv in ([], ["--ip", "--street", "preflop"]):
            where, _l, parts = query.build(argv)
            try:
                app._work(app.pending, view, where, _l, parts, "position")
                app.results.get_nowait()
            except Exception as e:
                broke.append(f"{view}: {type(e).__name__}: {e}")
    print(f"every view answers             {10 - len(broke)}/10")
    for b in broke:
        print(f"    {b}")
    fails += broke

    # The dark theme is only dark if `clam` is the theme in use; the others
    # hand their drawing to Windows and ignore every colour set here.
    theme = ttk.Style(root).theme_use()
    print(f"theme in use                   {theme}")
    if theme != "clam":
        fails.append(f"theme is {theme}, which will not honour dark colours")

    con.close()
    root.destroy()
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if not DB.exists():
        print(f"no database at {DB} -- load some hands first")
        return 1
    root = tk.Tk()
    root.title("poker_analysis")
    root.geometry("1360x880")
    root.minsize(1050, 640)
    dark(root)
    app = App(root)
    app.load_options()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
