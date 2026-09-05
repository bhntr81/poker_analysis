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
    python app.py --debug   also print the log to the terminal
    python app.py --check   the window and the command line agree

Anything that goes wrong is written to `poker_analysis.log` beside the
program, including the failures Tk would otherwise swallow. `python diag.py`
prints the end of it.

Hands come in through the Import menu -- a folder, a file, another database,
or whatever it can find on this computer. Nothing there asks which site the
hands are from; every file is identified by reading it.
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

import sqlite3

import diag
import importer
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

# The fonts a machine actually has. "Segoe UI" and "Consolas" ship with
# Windows and with nothing else; asking Tk for a font that is not installed
# does not fail, it silently substitutes, and what it substitutes on Linux
# is a bitmap face from the eighties. Chosen once, from what the system
# reports, so the same window is legible on all three.
UI, MONO = "Segoe UI", "Consolas"

POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
STREETS = ("preflop", "flop", "turn", "river")
POT_TYPES = ("unopened", "limped", "raised", "3bet", "4bet")
# What the hand became. Ordered as they beat each other, because a list of
# them sorted alphabetically is a list nobody can read down.
MADE = ("high card", "board pair", "weak pair", "under pair", "middle pair",
        "top pair", "overpair", "two pair", "trips", "set", "straight",
        "flush", "boat", "quads", "straight flush")
KICKERS = ("top", "good", "weak")
FLUSH_DRAWS = ("nut", "second", "weak", "backdoor")
STRAIGHT_DRAWS = ("oesd", "double gutshot", "gutshot")
# Who the other seat is. "The big blind against a button open" is the shape
# most real questions have, and it needs both halves of the matchup named.
VS_SIDE = [("--vs-hero", "vs me"), ("--vs-pool", "vs the pool")]
# What kind of player, on each side of the matchup. A class is only given to
# somebody there is enough evidence about; everybody else is "unknown" and
# is selected by neither of these, which is the point of them.
WHO = [("--reg", "the player is a reg"), ("--fish", "the player is a fish"),
       ("--vs-reg", "against a reg"), ("--vs-fish", "against a fish")]
SITUATIONS = [("--ip", "in position"), ("--oop", "out of position"),
              ("--pfa", "was the raiser"), ("--vs-pfa", "facing the raiser"),
              ("--multiway", "multiway"), ("--headsup", "heads up"),
              ("--allin", "all-in")]
# Turning one of these on turns its opposite off, or the filter selects
# nothing and looks broken rather than contradictory.
OPPOSITES = {"--hero": "--pool", "--pool": "--hero", "--ip": "--oop",
             "--oop": "--ip", "--multiway": "--headsup",
             "--headsup": "--multiway",
             "--reg": "--fish", "--fish": "--reg",
             "--vs-reg": "--vs-fish", "--vs-fish": "--vs-reg",
             "--vs-hero": "--vs-pool", "--vs-pool": "--vs-hero"}


def pick_fonts():
    """
    The best font on this machine, from the ones it says it has.

    Needs a root window: Tk cannot be asked what fonts exist before it has
    one. `TkDefaultFont` and `TkFixedFont` are the last resort and are the
    only two names guaranteed to resolve to something on every platform.
    """
    global UI, MONO
    have = set(tkfont.families())
    UI = next((f for f in ("Segoe UI", "SF Pro Text", "Helvetica Neue",
                           "Ubuntu", "Cantarell", "DejaVu Sans", "Arial")
               if f in have), "TkDefaultFont")
    MONO = next((f for f in ("Consolas", "SF Mono", "Menlo",
                             "DejaVu Sans Mono", "Ubuntu Mono", "Courier New")
                 if f in have), "TkFixedFont")
    return UI, MONO


def open_folder(path):
    """
    Show a folder in whatever file browser this machine has.

    `os.startfile` exists only on Windows -- on a Mac or a Linux box the Help
    menu would raise AttributeError, and in a windowed build that failure is
    invisible, which is precisely the class of bug `diag` was written for.
    """
    if sys.platform == "win32":
        os.startfile(path)                                  # noqa: S606
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, str(path)], check=False)


def dark(root):
    """Make Tk dark, which it does not want to be."""
    pick_fonts()
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
                    font=(UI, 8, "bold"))
    style.configure("Title.TLabel", background=BG, foreground=INK,
                    font=(UI, 11, "bold"))
    style.configure("TCheckbutton", background=BG, foreground=DIM)
    style.map("TCheckbutton",
              foreground=[("selected", ACCENT), ("active", INK)],
              background=[("active", BG)])
    style.configure("TButton", background=PANEL, foreground=INK,
                    bordercolor=EDGE, focusthickness=0, padding=(10, 4))
    style.map("TButton", background=[("active", EDGE)])
    style.configure("Accent.TButton", background=ACCENT, foreground="#08111f",
                    bordercolor=ACCENT, padding=(14, 5))
    style.map("Accent.TButton", background=[("active", "#5ea6ff")])
    style.configure("Big.TNotebook.Tab", padding=(20, 9),
                    font=(UI, 10))
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
                    relief="flat", font=(UI, 8, "bold"))
    style.map("Treeview.Heading", background=[("active", EDGE)])
    style.map("Treeview", background=[("selected", "#233047")],
              foreground=[("selected", INK)])
    style.configure("TSeparator", background=EDGE)
    style.configure("Vertical.TScrollbar", background=PANEL,
                    troughcolor=BG, bordercolor=BG, arrowcolor=DIM)
    style.configure("Horizontal.TScrollbar", background=PANEL,
                    troughcolor=BG, bordercolor=BG, arrowcolor=DIM)
    return style


class Progress(tk.Toplevel):
    """
    A window that says what the import is doing while it does it.

    Loading a season of hand histories takes minutes and rebuilding the
    derived tables takes minutes more. Without this the application simply
    stops responding, and the only available conclusion is that it has
    crashed.
    """

    def __init__(self, master, title):
        super().__init__(master)
        self.title(title)
        self.configure(background=BG)
        self.geometry("560x300")
        self.transient(master)
        self.text = tk.Text(self, background=BG, foreground=INK, borderwidth=0,
                            font=(MONO, 10), padx=14, pady=12,
                            insertbackground=BG)
        self.text.pack(fill="both", expand=True)
        self.close = ttk.Button(self, text="close", command=self.destroy,
                                state="disabled")
        self.close.pack(pady=(0, 10))
        self.protocol("WM_DELETE_WINDOW", lambda: None)

    def say(self, line):
        self.text.insert("end", line + "\n")
        self.text.see("end")
        self.update_idletasks()

    def done(self):
        self.close.configure(state="normal")
        self.protocol("WM_DELETE_WINDOW", self.destroy)


class ImportMixin:
    """Everything the Import menu does. Kept apart because none of it is UI."""

    def _menu(self, root):
        bar = tk.Menu(root, background=PANEL, foreground=INK,
                      activebackground=ACCENT, activeforeground=BG,
                      borderwidth=0)
        m = tk.Menu(bar, tearoff=0, background=PANEL, foreground=INK,
                    activebackground=ACCENT, activeforeground=BG)
        m.add_command(label="Find hands on this computer…",
                      command=self.import_autodetect)
        m.add_separator()
        m.add_command(label="Import a folder…", command=self.import_folder)
        m.add_command(label="Import a file…", command=self.import_file)
        m.add_command(label="Merge another database…", command=self.import_db)
        bar.add_cascade(label="Import", menu=m)

        h = tk.Menu(bar, tearoff=0, background=PANEL, foreground=INK,
                    activebackground=ACCENT, activeforeground=BG)
        h.add_command(label="Show the log…", command=self.show_log)
        h.add_command(label="Open the log folder",
                      command=lambda: open_folder(diag.LOG.parent))
        bar.add_cascade(label="Help", menu=h)
        root.configure(menu=bar)

    def show_log(self):
        win = tk.Toplevel(self.master)
        win.title("poker_analysis.log")
        win.configure(background=BG)
        win.geometry("900x560")
        text = tk.Text(win, background=BG, foreground=INK, borderwidth=0,
                       font=(MONO, 9), padx=12, pady=10, wrap="none")
        bar = ttk.Scrollbar(win, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=bar.set)
        text.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        text.insert("end", f"{diag.LOG}\n\n{diag.tail(400)}")
        text.see("end")

    def _run_import(self, title, work):
        """
        Every import runs on a thread, reporting into a window.

        The rebuild afterwards is not optional and is why it is here rather
        than left to the caller: loading writes to `hands`, `seats` and
        `actions`, and every question this program answers is asked of the
        tables derived from them. An import without the rebuild looks like an
        import that did nothing.
        """
        win = Progress(self.master, title)
        lines = queue.Queue()

        def go():
            try:
                got = work(lines.put)
                lines.put(("done", got))
            except Exception as e:
                lines.put(("failed", f"{type(e).__name__}: {e}"))

        threading.Thread(target=go, daemon=True).start()

        def pump():
            try:
                while True:
                    item = lines.get_nowait()
                    if isinstance(item, tuple):
                        kind, payload = item
                        if kind == "failed":
                            win.say("")
                            win.say(payload)
                        else:
                            win.say("")
                            win.say(str(payload))
                            self.con = sqlite3.connect(DB, check_same_thread=False)
                            self.load_options()
                            self.refresh()
                        win.done()
                        return
                    win.say(item)
            except queue.Empty:
                pass
            win.after(120, pump)
        win.after(120, pump)

    def import_autodetect(self):
        found = importer.scan()
        if not found:
            messagebox.showinfo(
                "nothing found",
                "No hand histories in the usual places.\n\n"
                "Use Import a folder… and point it at wherever your site "
                "writes them.")
            return
        summary = "\n".join(
            f"{p['ignition']:>5} ignition   {p['acr']:>5} acr   {p['path']}"
            for p in found)
        if not messagebox.askyesno(
                "found these", summary + "\n\nImport all of them?"):
            return
        paths = [p["path"] for p in found]
        self._run_import("importing", lambda say: self._do_load(paths, say))

    def import_folder(self):
        folder = filedialog.askdirectory(title="folder of hand histories")
        if folder:
            self._run_import("importing",
                             lambda say: self._do_load([folder], say))

    def import_file(self):
        files = filedialog.askopenfilenames(
            title="hand history files",
            filetypes=[("hand histories", "*.txt"), ("all files", "*.*")])
        if files:
            self._run_import("importing",
                             lambda say: self._do_load(list(files), say))

    def import_db(self):
        other = filedialog.askopenfilename(
            title="another hands.db", filetypes=[("database", "*.db")])
        if not other:
            return

        def work(say):
            say(f"merging {other}")
            got = importer.merge(other, DB, progress=say)
            importer.rebuild(progress=say)
            return f"{got['added']} hands merged, {got['known']} already known"
        self._run_import("merging", work)

    @staticmethod
    def _do_load(paths, say):
        say("looking at the files…")
        survey = importer.survey(paths)
        say(f"  {len(survey['ignition'])} ignition, {len(survey['acr'])} acr, "
            f"{len(survey['unknown'])} not recognised")
        if not survey["ignition"] and not survey["acr"]:
            return "nothing to import"
        got = importer.load(paths, DB, progress=say)
        say(f"{got['added']} hands added, {got['known']} already known")
        if got["added"]:
            importer.rebuild(progress=say)
        return (f"{got['added']} hands added"
                + (f", {got['unknown']} files unrecognised"
                   if got["unknown"] else ""))


class App(ImportMixin, ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.pack(fill="both", expand=True)
        self.con = sqlite3.connect(DB, check_same_thread=False)
        # Every switch the command line has, whether or not a control for
        # it has been built yet. These used to appear as a side effect of
        # drawing the rail, so deleting the rail silently emptied the filter.
        self.flags = {f: tk.BooleanVar() for f in query.SWITCHES}
        self.multi = {"pos": set(), "vs": set(), "street": set(),
                      "pot": set(), "board": set(), "quick": set(),
                      "made": set(), "kicker": set(), "fd": set(),
                      "sd": set(), "turn_card": set(), "river_card": set()}
        # The filter's values live here rather than on the widgets, because
        # the widgets belong to a dialog that is destroyed every time it is
        # closed and the filter is not.
        self.vals = {n: tk.StringVar() for n in
                     ("site", "stake", "player", "deep", "short",
                      "since", "until", "where",
                      "line", "node", "pre", "flop", "turn", "river")}
        self.options = {"sites": [], "stakes": [], "players": []}

        self.results = queue.Queue()
        self.pending = 0
        self._build()
        self.after(80, self._drain)
        self.refresh()

    # ---- layout -------------------------------------------------------
    def _build(self):
        head = ttk.Frame(self)
        head.pack(fill="x", padx=18, pady=(12, 8))
        ttk.Label(head, text="poker_analysis",
                  style="Title.TLabel").pack(side="left")
        self.sub = ttk.Label(head, text="", style="Dim.TLabel")
        self.sub.pack(side="left", padx=12)
        self.status = ttk.Label(head, text="", style="Dim.TLabel")
        self.status.pack(side="right")

        # The filter lives behind a button rather than down the side. A rail
        # wide enough for every filter this database supports is a rail that
        # leaves no room for the answer, and the filter is looked at far less
        # often than the thing it produces.
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=18, pady=(0, 8))
        ttk.Button(bar, text="＋  Filter", style="Accent.TButton",
                   command=self.open_filters).pack(side="left")
        self.clear_btn = ttk.Button(bar, text="clear", command=self.clear_filters)
        self.summary = ttk.Label(bar, text="all hands", style="Dim.TLabel")
        self.summary.pack(side="left", padx=12)
        ttk.Separator(self).pack(fill="x")

        right = ttk.Frame(self)
        right.pack(fill="both", expand=True)
        self._views(right)

    def open_filters(self):
        FilterDialog(self)

    def clear_filters(self):
        for v in self.flags.values():
            v.set(False)
        for g in self.multi:
            self.multi[g] = set()
        for v in self.vals.values():
            v.set("")
        self.refresh()


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
        argv = [fl for fl, v in self.flags.items() if v.get()]
        for group, flag in (("pos", "--pos"), ("vs", "--vs"),
                            ("street", "--street"), ("pot", "--pot"),
                            ("board", "--board"), ("quick", "--quick"),
                            ("made", "--made"), ("kicker", "--kicker"),
                            ("fd", "--fd"), ("sd", "--sd"),
                            ("turn_card", "--turn-card"),
                            ("river_card", "--river-card")):
            if self.multi.get(group):
                argv += [flag, ",".join(sorted(self.multi[group]))]
        for name, flag in (("site", "--site"), ("stake", "--stake"),
                           ("player", "--player"), ("deep", "--deep"),
                           ("short", "--short"), ("since", "--since"),
                           ("until", "--until"), ("where", "--where"),
                           ("line", "--line"), ("node", "--node"),
                           ("pre", "--pre"), ("flop", "--flop"),
                           ("turn", "--turn"), ("river", "--river")):
            v = self.vals[name].get().strip()
            if v and not v.startswith("any "):
                argv += [flag, v]
        return argv


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
        diag.event("refresh", view=view, filter=label)
        self.filter_line.configure(text="filter: " + label)
        self.summary.configure(text=self.describe_filter())
        if self.argv():
            self.clear_btn.pack(side="left", padx=(6, 0))
        else:
            self.clear_btn.pack_forget()
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
            diag.event("query failed", view=view, where=where, error=str(e))
            out = {"view": view, "error": f"SQL: {e}"}
        except Exception:
            # A worker dying quietly leaves the window up and unresponsive,
            # which is the hardest kind of failure to report from the outside.
            diag._report(f"worker ({view})", *sys.exc_info()[1:])
            out = {"view": view, "error": "something went wrong -- see the log"}
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
            c.create_text(w / 2, h / 2, fill=DIM, font=(UI, 10),
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
                          anchor="e", font=(UI, 8))
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
                          font=(UI, 9),
                          text=f"{key.replace('_', ' ')}  {s[key][-1]:+,.0f}")
        c.create_text((L + w - R) / 2, h - 14, fill=DIM,
                      font=(UI, 8), text=s.get("_note", ""))

    # ---- what this database holds -------------------------------------
    def load_options(self):
        """What this database holds, for the dialog to offer."""
        one = lambda sql: [r[0] for r in self.con.execute(sql)
                           if r[0] is not None]
        self.options["sites"] = one(
            "SELECT DISTINCT site FROM decisions ORDER BY 1")
        self.options["stakes"] = [f"{v:g}" for v in one(
            "SELECT DISTINCT bb FROM decisions ORDER BY 1")]
        self.options["players"] = one(
            "SELECT player FROM decisions WHERE player IS NOT NULL "
            "GROUP BY player HAVING COUNT(DISTINCT hand_id) >= 100 "
            "ORDER BY COUNT(DISTINCT hand_id) DESC LIMIT 300")
        hands = self.con.execute("SELECT COUNT(*) FROM hands").fetchone()[0]
        self.sub.configure(
            text=f"{' · '.join(self.options['sites'])}   {hands:,} hands")

    def describe_filter(self):
        """The active filter as a sentence, for the bar above the answer."""
        try:
            _w, label, _p = query.build(self.argv())
        except SystemExit:
            return "…"
        return "all hands" if label == "everything" else label


class FilterDialog(tk.Toplevel):
    """
    Every filter, laid out to be read rather than squeezed down one side.

    The rail this replaces could hold about a third of what the database can
    be asked, and it did it in a column narrow enough that each control was a
    guess at its own label. A filter is consulted far less often than the
    answer it produces, so it belongs behind a button and is worth giving the
    whole window when it is open.

    Nothing here defines a filter. Every control sets one of the variables
    the application already holds, and `query.build` turns those into a WHERE
    clause exactly as it does for the command line -- which is what stops the
    window and the terminal from drifting into meaning different things.
    """

    COLUMNS = 3

    def __init__(self, app):
        super().__init__(app.master)
        self.app = app
        self.title("Filter")
        self.configure(background=BG)
        self.geometry("1080x720")
        self.transient(app.master)
        self.grab_set()

        # Edit a copy. Cancel then means what it says, rather than leaving
        # behind whatever was clicked before somebody changed their mind.
        self.was = ({f: v.get() for f, v in app.flags.items()},
                    {g: set(v) for g, v in app.multi.items()},
                    {n: v.get() for n, v in app.vals.items()})

        nb = ttk.Notebook(self, style="Big.TNotebook")
        nb.pack(fill="both", expand=True, padx=16, pady=(14, 0))
        self._quick_tab(nb)
        self._positions_tab(nb)
        self._actions_tab(nb)
        self._cards_tab(nb)
        self._lines_tab(nb)
        self._general_tab(nb)

        foot = ttk.Frame(self)
        foot.pack(fill="x", padx=20, pady=14)
        ttk.Label(foot, style="Dim.TLabel",
                  text="closing this window keeps your choices — "
                       "CANCEL throws them away").pack(side="left")
        ttk.Button(foot, text="APPLY", style="Accent.TButton",
                   command=self.apply).pack(side="right", padx=(8, 0))
        ttk.Button(foot, text="RESET", command=self.reset).pack(side="right",
                                                                padx=8)
        ttk.Button(foot, text="CANCEL", command=self.cancel).pack(side="right")
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.apply())
        # Closing the window keeps what was clicked. The dialog edits the
        # filter in place, so shutting it with the title bar used to leave
        # every choice set and the view never redrawn -- which looks exactly
        # like a filter that does nothing, and was reported as one. CANCEL
        # is the way to throw the choices away, and it is a button.
        self.protocol("WM_DELETE_WINDOW", self.apply)

    # ---- the pieces a tab is made of ----------------------------------
    def _page(self, nb, title):
        outer = ttk.Frame(nb)
        nb.add(outer, text=title)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-e.delta // 120, "units"))
        return inner

    def _heading(self, parent, text):
        ttk.Label(parent, text=text.upper(), style="Head.TLabel").pack(
            anchor="w", padx=18, pady=(18, 6))

    def _pick(self, parent, label, on, off, is_on, note=""):
        """
        One clickable choice, drawn as text rather than as a checkbox.

        Tk's checkbutton indicator cannot be made to look like anything but a
        Tk checkbutton, and a page of them reads as a form. What is wanted
        here is a list of things you can pick, so the label itself is the
        control and being chosen is shown by its colour.
        """
        lab = tk.Label(parent, text=label, bg=BG, anchor="w", padx=10, pady=5,
                       font=(UI, 10), cursor="hand2")
        if note:
            self._tip(lab, note)

        def paint():
            lab.configure(fg=WARN if is_on() else INK,
                          font=(UI, 10, "bold" if is_on() else "normal"))

        def click(_e):
            (off if is_on() else on)()
            paint()
        lab.bind("<Button-1>", click)
        lab.bind("<Enter>", lambda e: lab.configure(bg=PANEL))
        lab.bind("<Leave>", lambda e: lab.configure(bg=BG))
        paint()
        lab.pack(fill="x", anchor="w")
        return lab

    def _tip(self, widget, text):
        """A note on hover, since a filter's name rarely says its definition."""
        tip = {"win": None}

        def show(_e):
            if tip["win"]:
                return
            w = tk.Toplevel(widget)
            w.wm_overrideredirect(True)
            w.configure(background=EDGE)
            tk.Label(w, text=text, bg=EDGE, fg=INK, font=(UI, 9),
                     padx=8, pady=4, wraplength=380, justify="left").pack()
            w.wm_geometry(f"+{widget.winfo_rootx() + 20}"
                          f"+{widget.winfo_rooty() + 26}")
            tip["win"] = w

        def hide(_e):
            if tip["win"]:
                tip["win"].destroy()
                tip["win"] = None
        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")

    def _grid(self, parent, items):
        """Items in columns, filled down then across, as the screenshot does."""
        holder = ttk.Frame(parent)
        holder.pack(fill="x", padx=8)
        cols = [ttk.Frame(holder) for _ in range(self.COLUMNS)]
        for c in cols:
            c.pack(side="left", fill="both", expand=True, anchor="n")
        per = (len(items) + self.COLUMNS - 1) // self.COLUMNS or 1
        for i, make in enumerate(items):
            make(cols[min(i // per, self.COLUMNS - 1)])

    def _set_item(self, group, value):
        m = self.app.multi[group]
        return (lambda: m.add(value), lambda: m.discard(value),
                lambda: value in m)

    def _flag_item(self, flag):
        v = self.app.flags[flag]

        def on():
            v.set(True)
            twin = OPPOSITES.get(flag)
            if twin:
                self.app.flags[twin].set(False)
        return on, (lambda: v.set(False)), (lambda: bool(v.get()))

    # ---- the tabs ------------------------------------------------------
    def _quick_tab(self, nb):
        page = self._page(nb, "Quick Filters")
        by_group = {}
        for f in query.quick_filters():
            by_group.setdefault(f["group"], []).append(f)
        for group, items in by_group.items():
            self._heading(page, group)
            self._grid(page, [
                (lambda parent, f=f: self._pick(
                    parent, f["label"], *self._set_item("quick", f["key"]),
                    note=f.get("note") or ""))
                for f in items])

    def _positions_tab(self, nb):
        page = self._page(nb, "Positions")
        self._heading(page, "my position")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("pos", v))) for v in POSITIONS])
        self._heading(page, "against  (heads-up pots only)")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("vs", v))) for v in POSITIONS])
        self._heading(page, "and that opponent is")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f))) for f, t in VS_SIDE])
        self._heading(page, "who is being measured")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f)))
            for f, t in (("--hero", "me"), ("--pool", "the pool"))])

        self._heading(page, "what kind of player")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f))) for f, t in WHO])
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="A reg plays a third of hands or fewer and raises at "
                       "least one in ten. A fish is loose or passive: over a "
                       "third of hands, or almost never raising while still "
                       "coming in often. Everybody there is not enough "
                       "evidence about is unknown and neither of these "
                       "selects them — which is why the two do not add up "
                       "to the whole pool.\n\nOnly ACR names people. An "
                       "Ignition ring seat is one player for as long as they "
                       "stay sat there and a different one after; Zone names "
                       "nobody at all."
                  ).pack(anchor="w", padx=18, pady=(4, 0))

    def _actions_tab(self, nb):
        page = self._page(nb, "Actions")
        self._heading(page, "street")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("street", v))) for v in STREETS])
        self._heading(page, "pot type")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("pot", v))) for v in POT_TYPES])
        self._heading(page, "situation")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f))) for f, t in SITUATIONS])
        self._heading(page, "flop texture")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("board", v)))
            for v in query.BOARDS])
        self._heading(page, "what the turn card did")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("turn_card", v)))
            for v in query.RUNOUT])
        self._heading(page, "what the river card did")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("river_card", v)))
            for v in query.RUNOUT])
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="A flush card is one that takes a suit to three on "
                       "the board. On the turn, straight means the board is "
                       "now one card off one; on the river it means that "
                       "card came. A brick did none of the four."
                  ).pack(anchor="w", padx=18, pady=(6, 0))

    def _cards_tab(self, nb):
        """
        What the hand is, which only exists where the cards were shown.

        Three quarters of decisions have no cards to name a hand from -- all
        of Ignition's are known and 23% of ACR's -- so everything on this tab
        narrows hard, and the note says so before an empty table does.
        """
        page = self._page(nb, "Cards")
        self._heading(page, "what the hand became")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("made", v))) for v in MADE])
        self._heading(page, "kicker, where a pair uses one")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("kicker", v))) for v in KICKERS])
        self._heading(page, "flush draw")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("fd", v))) for v in FLUSH_DRAWS])
        self._heading(page, "straight draw")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("sd", v))) for v in STRAIGHT_DRAWS])
        self._heading(page, "either, or both at once")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f)))
            for f, t in (("--drawing", "has a draw"),
                         ("--combo-draw", "a flush draw and a straight draw"),
                         ("--shown", "the cards are known"))])
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="Every filter here needs the cards, and the cards are "
                       "known for 20,465 postflop decisions out of 94,017 — "
                       "all of Ignition's hands, including the ones that "
                       "folded, and 23% of ACR's. So these narrow hard, and "
                       "a small n here is the data and not the filter.\n\n"
                       "A draw has to be the player's own: four hearts on "
                       "the board is not a flush draw, it is a board "
                       "everybody shares."
                  ).pack(anchor="w", padx=18, pady=(8, 0))

    def _lines_tab(self, nb):
        """
        The betting written out, which is the one thing checkboxes cannot say.

        Everything on the other tabs describes a single decision. This
        describes the shape of the hand -- "the flop went check, bet, call"
        -- and no list of named filters covers it, because the number of
        shapes a hand can have is the number of strings these letters spell.
        """
        page = self._page(nb, "Lines")
        self._heading(page, "how the betting went")
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="F fold   X check   C call   B bet   R raise   "
                       "A all-in        *  anything at all   ?  any one "
                       "action\n\nAdd a size letter after a bet to say how "
                       "big it was:   s  a third or less   m  half   "
                       "l  two-thirds to three-quarters   p  pot   "
                       "o  an overbet.\nSo XBC is a flop that went check, "
                       "bet, call at any size, and XBmC is the same flop "
                       "with a half-pot bet."
                  ).pack(anchor="w", padx=18, pady=(0, 4))

        for name, label, example in (
                ("pre", "preflop", "*R*R*   somebody 3-bet"),
                ("flop", "flop", "XBC   checked, bet, called"),
                ("turn", "turn", "XX   checked through"),
                ("river", "river", "*Bo*   somebody overbet")):
            row = ttk.Frame(page)
            row.pack(fill="x", padx=18, pady=3)
            ttk.Label(row, text=label, style="Dim.TLabel", width=9).pack(
                side="left")
            ttk.Entry(row, textvariable=self.app.vals[name], width=26).pack(
                side="left")
            ttk.Label(row, text=example, style="Dim.TLabel").pack(
                side="left", padx=14)

        self._heading(page, "the whole hand, streets separated by /")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18, pady=3)
        ttk.Label(row, text="line", style="Dim.TLabel", width=9).pack(
            side="left")
        ttk.Entry(row, textvariable=self.app.vals["line"], width=40).pack(
            side="left")
        ttk.Label(row, text="*R*R*/XBC/XX/*   3-bet pot, flop check-bet-call, "
                            "turn through", style="Dim.TLabel").pack(
            side="left", padx=14)

        self._heading(page, "where the player was standing when they acted")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18, pady=3)
        ttk.Label(row, text="node", style="Dim.TLabel", width=9).pack(
            side="left")
        ttk.Entry(row, textvariable=self.app.vals["node"], width=40).pack(
            side="left")
        ttk.Label(row, text="*/XB   it was checked to somebody, who bet, and "
                            "now it is this player's turn",
                  style="Dim.TLabel").pack(side="left", padx=14)
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="A node is the hand cut short at the moment this "
                       "player had to act, so it never contains what they "
                       "did next -- which is what makes it the right thing "
                       "to measure a decision against."
                  ).pack(anchor="w", padx=18, pady=(8, 0))

    def _general_tab(self, nb):
        page = self._page(nb, "General")
        self._heading(page, "site, stake, player")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18)
        for name, blank, values in (
                ("site", "any site", self.app.options["sites"]),
                ("stake", "any stake", self.app.options["stakes"]),
                ("player", "any player", self.app.options["players"])):
            box = ttk.Combobox(row, textvariable=self.app.vals[name],
                               values=[blank] + list(values), width=22)
            if not self.app.vals[name].get():
                self.app.vals[name].set(blank)
            box.pack(side="left", padx=(0, 14))

        self._heading(page, "stack depth, in big blinds")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18)
        for name, text in (("deep", "at least"), ("short", "less than")):
            ttk.Label(row, text=text, style="Dim.TLabel").pack(side="left")
            ttk.Entry(row, textvariable=self.app.vals[name], width=8).pack(
                side="left", padx=(6, 18))

        self._heading(page, "dates   (yyyy-mm-dd)")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18)
        for name, text in (("since", "from"), ("until", "to")):
            ttk.Label(row, text=text, style="Dim.TLabel").pack(side="left")
            ttk.Entry(row, textvariable=self.app.vals[name], width=14).pack(
                side="left", padx=(6, 18))

        self._heading(page, "anything else, as SQL over `decisions`")
        ttk.Entry(page, textvariable=self.app.vals["where"]).pack(
            fill="x", padx=18, pady=(0, 6))
        ttk.Label(page, style="Dim.TLabel", wraplength=900, justify="left",
                  text="For what the named filters cannot say. The columns "
                       "are the ones `decisions` has: pot_frac, eff_bb, spr, "
                       "n_live, fl_hi, size_bb, to_call_bb and the rest."
                  ).pack(anchor="w", padx=18)

    # ---- the three buttons ---------------------------------------------
    def apply(self):
        diag.event("filter applied", argv=self.app.argv())
        self.grab_release()
        self.destroy()
        self.app.refresh()

    def cancel(self):
        flags, multi, vals = self.was
        for f, v in flags.items():
            self.app.flags[f].set(v)
        for g, v in multi.items():
            self.app.multi[g] = set(v)
        for n, v in vals.items():
            self.app.vals[n].set(v)
        self.grab_release()
        self.destroy()

    def reset(self):
        self.app.clear_filters()
        self.grab_release()
        self.destroy()
        FilterDialog(self.app)


class HandWindow(tk.Toplevel):
    """One hand, replayed in a window of its own."""

    def __init__(self, master, con, hand_id, seat):
        super().__init__(master)
        self.configure(background=BG)
        self.title(f"hand {hand_id}")
        self.geometry("760x620")
        d = query.hand_detail(con, hand_id, seat)
        mono = tkfont.Font(family=MONO, size=10)
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
        # A quick filter is a named stat used as a filter, and it has to
        # reach uild by the same road every other control does.
        ({"flags": ["--hero"], "quick": ["cbet_flop"], "pot": ["raised"]},
         ["--hero", "--quick", "cbet_flop", "--pot", "raised"]),
        # A typed line, which reaches the filter by a different road from
        # every clickable one: it is a text box rather than a state a click
        # sets, and a box that is read into the wrong flag looks like a box
        # that does nothing.
        ({"flags": ["--hero"], "vals": {"flop": "XBC", "turn": "XX"}},
         ["--hero", "--flop", "XBC", "--turn", "XX"]),
        ({"flags": [], "vals": {"node": "*/xbm"}}, ["--node", "*/xbm"]),
    ]
    for state, argv in cases:
        for f, var in app.flags.items():
            var.set(f in state["flags"])
        for g in app.multi:
            app.multi[g] = set(state.get(g, []))
        for n, var in app.vals.items():
            var.set(state.get("vals", {}).get(n, ""))
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
    # The dialog must offer what the command line can express. A filter that
    # exists only as a flag is a filter nobody will find, and the window
    # quietly falling behind the engine is how that comes about.
    missing = [f for f in query.SWITCHES if f not in app.flags]
    dialog = FilterDialog(app)
    dialog.withdraw()
    dialog.update_idletasks()

    def clickable(w, out):
        for kid in w.winfo_children():
            if isinstance(kid, tk.Label) and kid.cget("cursor") == "hand2":
                out.append(kid.cget("text"))
            clickable(kid, out)
        return out

    # Closing the dialog must do something. It used to do nothing at all --
    # the choices stayed set and the view was never redrawn -- which is
    # indistinguishable from a filter that has no effect, and was reported
    # as exactly that.
    closer = dialog.protocol("WM_DELETE_WINDOW")
    print(f"closing the dialog          "
          f"{'applies the filter' if closer else 'DOES NOTHING'}")
    if not closer:
        fails.append("closing the filter dialog silently discards the redraw")

    offered = clickable(dialog, [])
    print(f"switches the window can set   "
          f"{len(query.SWITCHES) - len(missing)}/{len(query.SWITCHES)}")
    print(f"filters offered in the dialog {len(offered)}")
    if missing:
        fails.append(f"the window cannot set {missing}")
    if len(offered) < len(query.quick_filters()):
        fails.append("the dialog offers fewer filters than are defined")
    dialog.destroy()

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
    diag.setup(verbose="--debug" in argv)
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
    diag.watch_tk(root)
    app = App(root)
    app._menu(root)
    app.load_options()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
