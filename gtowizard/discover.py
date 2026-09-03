"""
Find the strategy endpoint again, if GTO Wizard ever moves it.

This is the tool that found it in the first place, kept because it will be
needed again: front ends get rewritten and endpoints get versioned, and when
that happens the fix should be half an hour of watching rather than a
rewrite. It opens a browser, waits to be logged in, walks the app's own
links, and writes down every reply -- then ranks them by how much each looks
like a strategy.

The ranking is the trick. Rather than looking for a keyword or a known URL,
it counts how many of the 169 starting hands each reply names: a strategy has
to name the hands it is a strategy for, whatever the fields around them are
called. That works without knowing the shape of the answer in advance.
"""

import json
from pathlib import Path

from .browser import HOME, Browser, say, wait_for_login

CAPTURE = Path(__file__).resolve().parent.parent / "gtow_capture.json"

# Replies that are plainly not strategy. Filtering on content type alone
# still leaves telemetry and feature flags, which are JSON and useless.
SKIP_TYPES = ("image/", "font/", "text/css", "text/html", "video/",
              "application/javascript", "text/javascript")

RANKS = "AKQJT98765432"
COMBOS = {(hi + hi if i == j else
           (hi + lo + "s" if i < j else lo + hi + "o"))
          for i, hi in enumerate(RANKS) for j, lo in enumerate(RANKS)}


def combo_score(text):
    """How many of the 169 hands a reply names."""
    if not text:
        return 0
    return sum(1 for c in COMBOS if '"' + c + '"' in text or "'" + c + "'" in text)


def recorder(seen):
    """A listener that writes down every reply worth keeping."""
    def on_response(resp):
        ctype = (resp.headers or {}).get("content-type", "")
        if any(t in ctype for t in SKIP_TYPES):
            return
        try:
            body = resp.text()
        except Exception:
            return                      # streamed, or already gone
        if not body or len(body) > 4_000_000:
            return
        # A compressed POST body is not text and asking for it as text
        # raises, which would otherwise dump a stack trace per request.
        try:
            post = resp.request.post_data
        except Exception:
            post = None
        seen.append({"method": resp.request.method, "url": resp.url,
                     "status": resp.status, "type": ctype.split(";")[0],
                     "bytes": len(body), "combos": combo_score(body),
                     "post": post, "body": body})
    return on_response


def run(use_chrome=False, seconds=180, log=say):
    """Open, wait to be logged in, look around, and report what was seen."""
    seen = []
    br = Browser(use_chrome=use_chrome, log=log)
    br.open()                                   # opens and captures any auth
    ctx, page = br._ctx, br._page
    ctx.on("response", recorder(seen))
    page.goto(HOME, wait_until="domcontentloaded", timeout=60000)

    log("\n>>> A browser window has opened. Log in to GTO Wizard there.")
    log(">>> Nothing else is needed from you.\n")
    if not wait_for_login(page, log=log):
        br.close()
        log("Never got past the login page.")
        return []

    page.wait_for_timeout(4000)
    try:
        links = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))") or []
    except Exception:
        links = []
    # Only the app's own pages. Following a help-site link wastes a visit and
    # lands back on the home page having learned nothing.
    here = [h for h in sorted({l for l in links if l})
            if ("solution" in h or "range" in h)
            and (h.startswith("/") or "app.gtowizard.com" in h)]
    log("\nlinks the app offers that mention solutions or ranges:")
    for h in here[:25]:
        log("    " + h)

    for url in ["https://app.gtowizard.com/solutions"] + [
            h if h.startswith("http") else HOME.rstrip("/") + h for h in here[:6]]:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            log("visited {} ({} replies so far)".format(url[:70], len(seen)))
        except Exception as exc:
            log("could not open {}: {}".format(url[:60], exc))

    log("\nRecording for another {}s. Click through to a No Limit Hold'em "
        "6-max\npreflop spot -- anything you open is captured too.".format(seconds))
    waited = 0
    while waited < seconds:
        page.wait_for_timeout(10000)
        waited += 10
        best = max((r["combos"] for r in seen), default=0)
        log("  {}s  {} replies, best names {} of 169 hands".format(
            waited, len(seen), best))
        if best >= 100:
            log("  that is a strategy -- done, closing the browser")
            break
    log("\nfinished on: {}".format(page.url))
    br.close()
    report(seen, log)
    return seen


def report(seen, log=say):
    """Rank what was recorded and name the likely endpoint."""
    if not seen:
        log("\nNothing was recorded.")
        return
    best = {}
    for r in seen:
        key = (r["method"], r["url"].split("?")[0])
        if key not in best or r["combos"] > best[key]["combos"]:
            best[key] = r
    ranked = sorted(best.values(), key=lambda r: -r["combos"])

    log("\n{} replies, {} distinct endpoints.\n".format(len(seen), len(best)))
    log("{:>7} {:>6} {:>9}  {}".format("combos", "status", "bytes", "endpoint"))
    for r in ranked[:25]:
        log("{:>7} {:>6} {:>9}  {} {}".format(
            r["combos"], r["status"], r["bytes"], r["method"], r["url"][:96]))

    hit = [r for r in ranked if r["combos"] >= 20]
    log("\n" + "=" * 66)
    if hit:
        log("LIKELY THE STRATEGY ENDPOINT -- it names {} of the 169 hands"
            .format(hit[0]["combos"]))
        log("=" * 66)
        log("{} {}".format(hit[0]["method"], hit[0]["url"]))
    else:
        log("No reply named enough hands to be a strategy.")
        log("=" * 66)
        log("Either no preflop range was opened, or the strategy is not")
        log("carried as text. The full capture is saved either way.")

    CAPTURE.write_text(json.dumps(seen, indent=1), encoding="utf-8")
    log("\nfull capture written to {}".format(CAPTURE.name))
    log("It can contain session tokens, so it is gitignored -- keep it local.")
