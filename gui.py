"""
The tracker with a face on it: filters down the side, answers in the middle.

Everything this shows, `query.py` could already answer. What it could not do
is let you change one thing and look again, which is most of what using a
tracker actually is -- you do not know the question until you have seen the
answer to a nearby one.

It is a local page rather than a desktop window for two reasons, and neither
is fashion. The graph is already an SVG, so a browser draws it for free and
a widget toolkit would need it rewritten. And the standard library's only
GUI is Tk, which fights every attempt to make it dark and loses.

Nothing is installed and nothing leaves the machine: the server binds to
127.0.0.1, serves one page and a handful of JSON endpoints, and stops when
you close the terminal.

**The filter is built by `query.build`, from the same flags the command line
takes.** That is deliberate and it is the only thing here that really
matters: two front ends that each assemble their own WHERE clause will
disagree eventually, and the disagreement will be silent.

    python gui.py              open it
    python gui.py --port 9000  somewhere else
    python gui.py --check      the GUI and the CLI agree, PASS or FAIL
"""

import json
import sqlite3
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import query
from stats import BY_KEY, STATS

DB = Path(__file__).parent / "hands.db"
PORT = 8765

# Form field -> the command-line flag it stands for. Written this way so the
# page cannot invent a filter the command line does not have, and so adding
# a flag to `query.py` is the only place a filter is ever defined.
SWITCH_FIELDS = {
    "hero": "--hero", "pool": "--pool", "ip": "--ip", "oop": "--oop",
    "pfa": "--pfa", "multiway": "--multiway", "headsup": "--headsup",
    "vs_pfa": "--vs-pfa", "standard": "--standard", "allin": "--allin",
}
VALUE_FIELDS = {
    "site": "--site", "player": "--player", "pos": "--pos",
    "street": "--street", "pot": "--pot", "facing": "--facing",
    "combo": "--combo", "stake": "--stake", "deep": "--deep",
    "short": "--short", "board": "--board", "since": "--since",
    "until": "--until", "where": "--where",
}


def argv_from(params):
    """A form's fields as the argument list `query.build` already understands."""
    argv = []
    for field, flag in SWITCH_FIELDS.items():
        if params.get(field, [""])[0] in ("1", "true", "on"):
            argv.append(flag)
    for field, flag in VALUE_FIELDS.items():
        v = (params.get(field, [""])[0] or "").strip()
        if v:
            argv += [flag, v]
    return argv


def payload(con, params):
    """Whatever the page asked for, as plain data."""
    view = params.get("view", ["stats"])[0]
    where, label = query.build(argv_from(params))

    if view == "stats":
        n_dec, rows = query.stats_of(con, where)
        return {"label": label, "decisions": n_dec, "rows": rows}

    if view == "report":
        dim = params.get("by", ["position"])[0]
        if dim not in query.DIMENSIONS:
            return {"error": f"unknown dimension {dim}"}
        expr, order = query.DIMENSIONS[dim]
        cols = [c for c in (params.get("show", [""])[0] or "").split(",") if c]
        cols = [c for c in cols if c in BY_KEY] or query.DEFAULT_COLUMNS
        grid = {c: query.rates_by(con, BY_KEY[c], expr, where) for c in cols}
        counts = query.rates_by(con, BY_KEY["vpip"], expr, where)
        keys = sorted({k for g in grid.values() for k in g},
                      key=lambda k: order(k) if k is not None else "")
        return {
            "label": label, "dim": dim,
            "columns": [{"key": c, "label": BY_KEY[c].label} for c in cols],
            "rows": [{
                "key": str(k), "n": counts.get(k, (0, 0))[0],
                "cells": [
                    None if not grid[c].get(k, (0, 0))[0] else {
                        "pct": 100 * grid[c][k][1] / grid[c][k][0],
                        "n": grid[c][k][0]}
                    for c in cols]} for k in keys]}

    if view == "results":
        dim = params.get("by", [""])[0]
        if dim and dim in query.DIMENSIONS:
            expr, order = query.DIMENSIONS[dim]
            values = [r[0] for r in con.execute(
                f"SELECT DISTINCT {expr} FROM decisions WHERE ({where}) "
                f"AND ({expr}) IS NOT NULL")]
            out = []
            for v in sorted(values, key=order):
                lit = query.q(v) if isinstance(v, str) else str(v)
                got = query.results_of(con, query.matching_seats(
                    con, f"({where}) AND ({expr}) = {lit}"))
                if got:
                    out.append(dict(got, key=str(v)))
            return {"label": label, "dim": dim, "rows": out}
        got = query.results_of(con, query.matching_seats(con, where))
        return {"label": label, "totals": got}

    if view == "hands":
        rows = con.execute(
            f"SELECT DISTINCT d.hand_id, d.seat, d.played_at, d.site, d.bb, "
            f"       d.position, d.combo, d.board, s.net_bb "
            f"FROM (SELECT * FROM decisions WHERE {where}) d "
            f"LEFT JOIN spots s ON s.hand_id = d.hand_id AND s.seat = d.seat "
            f"ORDER BY d.played_at DESC LIMIT 300").fetchall()
        return {"label": label, "rows": [
            {"when": r[2], "site": r[3], "bb": r[4], "pos": r[5],
             "combo": r[6], "board": r[7], "net": r[8], "id": r[0]}
            for r in rows]}

    if view == "graph":
        pairs = query.matching_seats(con, where)
        if len(pairs) < 2:
            return {"label": label, "svg": None}
        query.select_into(con, pairs)
        hands, adj, skipped = query.adjusted(con, pairs)
        if len(hands) < 2:
            return {"label": label, "svg": None}
        series = {k: [] for k, _, _ in query.LINES}
        total = sd = nsd = ev = 0.0
        for _when, net, was_sd, ev_net in hands:
            total += net or 0.0
            ev += ev_net or 0.0
            if was_sd:
                sd += net or 0.0
            else:
                nsd += net or 0.0
            series["total"].append(total)
            series["showdown"].append(sd)
            series["nonshowdown"].append(nsd)
            series["allin_ev"].append(ev)
        note = (f"{len(hands):,} hands  ·  {adj} all-in pots scored at equity"
                + (f"  ·  {skipped} unadjusted" if skipped else ""))
        return {"label": label,
                "svg": query.svg(series, label, note, dark=True)}

    return {"error": f"unknown view {view}"}


def options(con):
    """What this particular database actually contains, for the dropdowns."""
    one = lambda sql: [r[0] for r in con.execute(sql) if r[0] is not None]
    return {
        "sites": one("SELECT DISTINCT site FROM decisions ORDER BY 1"),
        "stakes": one("SELECT DISTINCT bb FROM decisions ORDER BY 1"),
        "players": one(
            "SELECT player FROM decisions WHERE player IS NOT NULL "
            "GROUP BY player HAVING COUNT(DISTINCT hand_id) >= 100 "
            "ORDER BY COUNT(DISTINCT hand_id) DESC LIMIT 200"),
        "boards": list(query.BOARDS),
        "dimensions": list(query.DIMENSIONS),
        "stats": [{"key": s.key, "label": s.label, "group": s.group,
                   "note": s.note} for s in STATS],
        "defaults": query.DEFAULT_COLUMNS,
    }


PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>poker_analysis</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{
  --bg:#14161a; --panel:#1b1e24; --edge:#2a2f38; --ink:#d8dbe0;
  --dim:#8b929c; --accent:#4c9aff; --good:#22a35a; --bad:#d1443c;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:13px/1.5 ui-sans-serif,system-ui,'Segoe UI',sans-serif}
header{padding:12px 18px;border-bottom:1px solid var(--edge);
  display:flex;align-items:baseline;gap:14px}
header h1{margin:0;font-size:14px;font-weight:600;letter-spacing:.02em}
header .sub{color:var(--dim);font-size:12px}
main{display:flex;align-items:flex-start;min-height:calc(100vh - 47px)}
aside{width:260px;flex:none;padding:14px;border-right:1px solid var(--edge);
  position:sticky;top:0;max-height:100vh;overflow:auto}
section{flex:1;padding:18px 22px;min-width:0;overflow-x:auto}
fieldset{border:none;padding:0;margin:0 0 14px}
legend{color:var(--dim);font-size:11px;text-transform:uppercase;
  letter-spacing:.08em;margin-bottom:6px}
label{display:block;margin:0 0 6px}
select,input{width:100%;background:#0f1115;color:var(--ink);
  border:1px solid var(--edge);border-radius:5px;padding:5px 7px;font:inherit}
select[multiple]{height:88px}
input:focus,select:focus{outline:1px solid var(--accent);border-color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chip{border:1px solid var(--edge);border-radius:20px;padding:3px 10px;
  cursor:pointer;color:var(--dim);user-select:none;font-size:12px}
.chip.on{background:var(--accent);border-color:var(--accent);color:#08111f;
  font-weight:600}
nav{display:flex;gap:4px;margin-bottom:14px;flex-wrap:wrap}
nav button{background:none;border:1px solid var(--edge);color:var(--dim);
  padding:5px 13px;border-radius:6px;cursor:pointer;font:inherit}
nav button.on{background:var(--panel);color:var(--ink);border-color:var(--accent)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:5px 9px;border-bottom:1px solid var(--edge);
  white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;
  letter-spacing:.05em}
tbody tr:hover{background:var(--panel)}
.thin{color:var(--dim)}
.thin::after{content:' ?';color:#b8892a}
.n{color:var(--dim);font-size:11px}
.pos{color:var(--good)} .neg{color:var(--bad)}
.filter{color:var(--dim);margin:0 0 14px;font-size:12px}
.group{color:var(--dim);font-size:11px;text-transform:uppercase;
  letter-spacing:.06em;padding-top:12px}
.empty{color:var(--dim);padding:26px 0}
.bar{height:5px;background:var(--accent);border-radius:3px;opacity:.5}
</style>
<header>
  <h1>poker_analysis</h1>
  <span class="sub" id="sub">loading…</span>
</header>
<main>
<aside>
  <fieldset><legend>who</legend>
    <div class="chips" id="who">
      <span class="chip" data-f="hero">hero</span>
      <span class="chip" data-f="pool">pool</span>
    </div>
  </fieldset>
  <fieldset><legend>site &amp; stake</legend>
    <label><select id="site"><option value="">any site</option></select></label>
    <label><select id="stake"><option value="">any stake</option></select></label>
    <label><select id="player"><option value="">any player</option></select></label>
  </fieldset>
  <fieldset><legend>position</legend>
    <div class="chips" id="pos"></div>
  </fieldset>
  <fieldset><legend>street</legend>
    <div class="chips" id="street"></div>
  </fieldset>
  <fieldset><legend>pot type</legend>
    <div class="chips" id="pot"></div>
  </fieldset>
  <fieldset><legend>situation</legend>
    <div class="chips" id="sit">
      <span class="chip" data-f="ip">in position</span>
      <span class="chip" data-f="oop">out of position</span>
      <span class="chip" data-f="pfa">was the raiser</span>
      <span class="chip" data-f="vs_pfa">facing the raiser</span>
      <span class="chip" data-f="multiway">multiway</span>
      <span class="chip" data-f="headsup">heads up</span>
    </div>
  </fieldset>
  <fieldset><legend>flop texture</legend>
    <div class="chips" id="board"></div>
  </fieldset>
  <fieldset><legend>stack depth (bb)</legend>
    <label>at least <input id="deep" type="number" min="0" step="10"></label>
    <label>less than <input id="short" type="number" min="0" step="10"></label>
  </fieldset>
  <fieldset><legend>dates</legend>
    <label>from <input id="since" type="date"></label>
    <label>to <input id="until" type="date"></label>
  </fieldset>
  <fieldset><legend>raw sql over decisions</legend>
    <label><input id="where" placeholder="eff_bb > 150 AND fl_paired=1"></label>
  </fieldset>
</aside>
<section>
  <nav id="tabs">
    <button data-v="stats" class="on">stats</button>
    <button data-v="report">report</button>
    <button data-v="results">results</button>
    <button data-v="graph">graph</button>
    <button data-v="hands">hands</button>
  </nav>
  <div id="byrow" style="display:none;margin-bottom:14px">
    <select id="by" style="width:auto;min-width:150px"></select>
  </div>
  <p class="filter" id="filter"></p>
  <div id="out"><p class="empty">…</p></div>
</section>
</main>
<script>
const $ = s => document.querySelector(s);
const state = {view:'stats', by:'position', flags:{}, multi:{}};
let OPT = {};

const POSITIONS = ['UTG','HJ','CO','BTN','SB','BB'];
const STREETS   = ['preflop','flop','turn','river'];
const POTS      = ['unopened','limped','raised','3bet','4bet'];

function chips(host, items, group){
  $('#'+host).innerHTML = items.map(v =>
    `<span class="chip" data-g="${group}" data-v="${v}">${v}</span>`).join('');
}
function paintChips(){
  document.querySelectorAll('.chip').forEach(c => {
    if (c.dataset.f) c.classList.toggle('on', !!state.flags[c.dataset.f]);
    else c.classList.toggle('on',
      (state.multi[c.dataset.g]||[]).includes(c.dataset.v));
  });
}
document.addEventListener('click', e => {
  const c = e.target.closest('.chip');
  if (!c) return;
  if (c.dataset.f){
    // hero and pool are opposites, as are in and out of position; turning
    // one on has to turn its twin off or the filter selects nothing.
    const twins = {hero:'pool', pool:'hero', ip:'oop', oop:'ip',
                   multiway:'headsup', headsup:'multiway'};
    state.flags[c.dataset.f] = !state.flags[c.dataset.f];
    if (state.flags[c.dataset.f] && twins[c.dataset.f])
      state.flags[twins[c.dataset.f]] = false;
  } else {
    const g = c.dataset.g, v = c.dataset.v;
    const cur = state.multi[g] || [];
    state.multi[g] = cur.includes(v) ? cur.filter(x=>x!==v) : cur.concat([v]);
  }
  paintChips(); load();
});
$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  state.view = b.dataset.v;
  document.querySelectorAll('#tabs button').forEach(x =>
    x.classList.toggle('on', x === b));
  $('#byrow').style.display =
    (state.view === 'report' || state.view === 'results') ? 'block' : 'none';
  load();
});
['site','stake','player','deep','short','since','until','where','by']
  .forEach(id => $('#'+id).addEventListener('change', () => {
    if (id === 'by') state.by = $('#by').value;
    load();
  }));
$('#where').addEventListener('keydown', e => { if (e.key === 'Enter') load(); });

function params(){
  const p = new URLSearchParams();
  p.set('view', state.view);
  if (state.view === 'report' || state.view === 'results') p.set('by', state.by);
  for (const [k,v] of Object.entries(state.flags)) if (v) p.set(k,'1');
  for (const [g,vs] of Object.entries(state.multi))
    if (vs.length) p.set(g, vs.join(','));
  for (const id of ['site','stake','player','deep','short','since','until','where']){
    const v = $('#'+id).value.trim();
    if (v) p.set(id, v);
  }
  return p;
}
const money = v => `<span class="${v>=0?'pos':'neg'}">${v>=0?'+':''}${
  v.toLocaleString(undefined,{maximumFractionDigits:1})}</span>`;

function render(d){
  const out = $('#out');
  $('#filter').textContent = 'filter: ' + (d.label || 'everything');
  if (d.error){ out.innerHTML = `<p class="empty">${d.error}</p>`; return; }

  if (state.view === 'stats'){
    if (!d.rows.length){
      out.innerHTML = `<p class="empty">${d.decisions
        ? 'no stat can occur inside this filter — asking for a preflop stat inside street=flop does this'
        : 'nothing matches'}</p>`; return; }
    let g = null, h = `<p class="n">${d.decisions.toLocaleString()} decisions match</p><table><tbody>`;
    for (const r of d.rows){
      if (r.group !== g){ g = r.group;
        h += `<tr><td colspan="4" class="group">${g}</td></tr>`; }
      h += `<tr><td title="${r.note||''}">${r.label}</td>`
        + `<td class="${r.n<30?'thin':''}">${r.pct.toFixed(1)}%</td>`
        + `<td class="n">±${r.band.toFixed(0)}</td>`
        + `<td class="n">n=${r.n.toLocaleString()}</td></tr>`;
    }
    out.innerHTML = h + '</tbody></table>';

  } else if (state.view === 'report'){
    if (!d.rows.length){ out.innerHTML = '<p class="empty">nothing matches</p>'; return; }
    let h = '<table><thead><tr><th>'+d.dim+'</th>'
      + d.columns.map(c=>`<th>${c.label}</th>`).join('') + '<th>n</th></tr></thead><tbody>';
    for (const r of d.rows){
      h += `<tr><td>${r.key}</td>` + r.cells.map(c => c === null
        ? '<td class="n">–</td>'
        : `<td class="${c.n<30?'thin':''}" title="n=${c.n}">${c.pct.toFixed(1)}%</td>`
      ).join('') + `<td class="n">${r.n.toLocaleString()}</td></tr>`;
    }
    out.innerHTML = h + '</tbody></table>';

  } else if (state.view === 'results'){
    if (d.rows){
      if (!d.rows.length){ out.innerHTML='<p class="empty">nothing matches</p>'; return; }
      let h = `<table><thead><tr><th>${d.dim}</th><th>hands</th><th>net bb</th>`
        + `<th>bb/100</th><th>± error</th></tr></thead><tbody>`;
      for (const r of d.rows)
        h += `<tr><td>${r.key}</td><td class="n">${r.hands.toLocaleString()}</td>`
          + `<td>${money(r.net_bb)}</td><td>${money(r.bb100)}</td>`
          + `<td class="n">${r.error.toFixed(0)}</td></tr>`;
      out.innerHTML = h + '</tbody></table>';
    } else if (!d.totals){
      out.innerHTML = '<p class="empty">nothing matches</p>';
    } else {
      const t = d.totals;
      out.innerHTML = `<table><tbody>
        <tr><td>hands</td><td>${t.hands.toLocaleString()}</td></tr>
        <tr><td>net</td><td>${money(t.net_bb)} bb</td></tr>
        <tr><td>in money</td><td>${money(t.money)}</td></tr>
        <tr><td>per 100 hands</td><td>${money(t.bb100)} bb/100</td></tr>
        <tr><td>error on that</td><td class="n">±${t.error.toFixed(0)} bb/100</td></tr>
        <tr><td>saw a flop</td><td class="n">${t.saw_flop.toLocaleString()}</td></tr>
        <tr><td>won at showdown</td><td class="n">${t.wtsd.toLocaleString()}</td></tr>
        </tbody></table>
        <p class="n" style="margin-top:14px;max-width:56ch">One hand's result has a
        standard deviation around 11.7bb, so the error on a win rate is about
        1170/√n. Where it is wider than the rate, the rate is not telling you
        anything.</p>`;
    }

  } else if (state.view === 'graph'){
    out.innerHTML = d.svg || '<p class="empty">not enough hands to draw a line</p>';

  } else {
    if (!d.rows.length){ out.innerHTML='<p class="empty">nothing matches</p>'; return; }
    let h = '<table><thead><tr><th>when</th><th>site</th><th>bb</th><th>pos</th>'
      + '<th>hand</th><th>net bb</th><th>board</th></tr></thead><tbody>';
    for (const r of d.rows)
      h += `<tr><td>${(r.when||'').slice(0,16)}</td><td>${r.site}</td>`
        + `<td class="n">${r.bb??''}</td><td>${r.pos||''}</td>`
        + `<td>${r.combo||'–'}</td><td>${r.net==null?'':money(r.net)}</td>`
        + `<td class="n">${r.board||''}</td></tr>`;
    out.innerHTML = h + '</tbody></table>';
  }
}

let seq = 0;
async function load(){
  const mine = ++seq;
  $('#out').innerHTML = '<div class="bar"></div>';
  const r = await fetch('/api?' + params().toString());
  const d = await r.json();
  if (mine === seq) render(d);
}

(async function start(){
  OPT = await (await fetch('/api/options')).json();
  $('#site').innerHTML = '<option value="">any site</option>'
    + OPT.sites.map(s=>`<option>${s}</option>`).join('');
  $('#stake').innerHTML = '<option value="">any stake</option>'
    + OPT.stakes.map(s=>`<option value="${s}">$${s}</option>`).join('');
  $('#player').innerHTML = '<option value="">any player</option>'
    + OPT.players.map(s=>`<option>${s}</option>`).join('');
  $('#by').innerHTML = OPT.dimensions.map(d=>
    `<option${d==='position'?' selected':''}>${d}</option>`).join('');
  chips('pos', POSITIONS, 'pos');
  chips('street', STREETS, 'street');
  chips('pot', POTS, 'pot');
  chips('board', OPT.boards, 'board');
  $('#sub').textContent = OPT.sites.join(' · ');
  paintChips();
  load();
})();
</script>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype):
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._send(PAGE, "text/html; charset=utf-8")
        # One connection per request: sqlite objects belong to the thread
        # that made them, and this server answers on several.
        con = sqlite3.connect(DB)
        try:
            if url.path == "/api/options":
                return self._send(json.dumps(options(con)), "application/json")
            if url.path == "/api":
                try:
                    body = payload(con, parse_qs(url.query))
                except SystemExit as e:
                    body = {"error": str(e)}
                except sqlite3.Error as e:
                    # A raw SQL box invites bad SQL, and the honest answer is
                    # the database's own complaint rather than a blank page.
                    body = {"error": f"SQL: {e}"}
                return self._send(json.dumps(body, default=float),
                                  "application/json")
        finally:
            con.close()
        self.send_error(404)

    def log_message(self, *_a):
        pass          # a request log on every keystroke is not useful here


def check(db_path=DB):
    """
    The page and the command line must build the same filter.

    They are two front ends over one database, and the way that goes wrong is
    not a crash -- it is the page quietly meaning something slightly different
    by "3-bet pots in position" than the command line does, so two answers
    disagree and neither looks wrong. So the check is an equality: a form's
    fields, and the flags they stand for, must produce the same WHERE clause.
    """
    fails = []
    cases = [
        ({"hero": ["1"], "pot": ["3bet"], "street": ["flop"], "ip": ["1"]},
         ["--hero", "--ip", "--pot", "3bet", "--street", "flop"]),
        ({"pool": ["1"], "site": ["acr"], "pos": ["BTN,CO"]},
         ["--pool", "--site", "acr", "--pos", "BTN,CO"]),
        ({"board": ["mono,paired"], "deep": ["100"]},
         ["--deep", "100", "--board", "mono,paired"]),
        ({"where": ["eff_bb > 150"]}, ["--where", "eff_bb > 150"]),
        ({}, []),
    ]
    for form, argv in cases:
        a, _ = query.build(argv_from(form))
        b, _ = query.build(argv)
        if sorted(a.split(" AND ")) != sorted(b.split(" AND ")):
            fails.append(f"{form} -> {a!r} but CLI gives {b!r}")
    print(f"page and command line agree  {len(cases) - len(fails)}/{len(cases)}")
    for f in fails:
        print(f"    {f}")

    # Every view must answer, including on a filter that matches nothing --
    # which a user will type within a minute of being handed a form.
    con = sqlite3.connect(db_path)
    views = ("stats", "report", "results", "hands", "graph")
    broke = []
    for v in views:
        for form in ({"view": [v]},
                     {"view": [v], "pos": ["BTN"], "street": ["preflop"],
                      "facing": ["check"]}):
            try:
                got = payload(con, form)
                if "error" in got and form.get("pos"):
                    pass
            except Exception as e:
                broke.append(f"{v}: {type(e).__name__}: {e}")
    print(f"every view answers           {len(views) * 2 - len(broke)}"
          f"/{len(views) * 2}")
    for b in broke:
        print(f"    {b}")
    fails += broke

    # The dropdowns must offer things this database actually has, or the
    # first click produces an empty page and looks broken.
    opt = options(con)
    for name in ("sites", "stakes", "players", "boards", "dimensions", "stats"):
        if not opt[name]:
            fails.append(f"no {name} offered")
    print(f"dropdowns are populated      "
          f"{opt['sites']} · {len(opt['players'])} players · "
          f"{len(opt['stats'])} stats")
    con.close()
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else PORT
    if not DB.exists():
        print(f"no database at {DB} -- load some hands first")
        return 1
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"poker_analysis  ->  {url}")
    print("ctrl-c to stop")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
