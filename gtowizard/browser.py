"""
The browser half: our own Chromium, logged in, and the token it carries.

Everything here exists because GTO Wizard has no public API key. The account
is a person's, held behind a normal web login, and the access token that
proves it lives in the page's memory rather than in storage where it could
simply be read. So the approach is to run a real browser, let the person log
into it once, and afterwards take the Authorization header off a request the
app makes for itself.

The profile is kept in a folder of its own, so the login survives between
runs and shares nothing with the browser the user browses with.

Logging in is the user's alone. Nothing here reads, fills or stores a
password, whichever provider the account sits behind -- it only watches the
address bar to know when the login finished.
"""

import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

HOME = "https://app.gtowizard.com/"


def say(*parts):
    """
    Print, and actually flush it.

    These runs sit waiting for a person to log in, so their output is read
    while they are still going. Python buffers stdout when it is not a
    terminal -- a background run, a log file -- and the message asking for
    the login is then held back until the process ends, which is exactly
    when it is no longer any use.
    """
    print(*parts, flush=True)


DEFAULT_PROFILE = Path(__file__).resolve().parent.parent / ".gtow_profile"


def release_profile(profile):
    """
    Shut any browser still holding the profile folder.

    Chromium locks its profile, and a run that is interrupted -- Ctrl-C, a
    crash, a window left open -- leaves processes behind still holding it.
    The next launch then fails with "already in use", which reads as a broken
    program rather than a stale window. Only processes whose command line
    names THIS profile are touched.
    """
    profile = Path(profile)
    if sys.platform.startswith("win"):
        count = ("@(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                 "Where-Object {{ $_.CommandLine -like '*{}*' }}).Count"
                 ).format(profile.name)
        kill = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                "Where-Object {{ $_.CommandLine -like '*{}*' }} | "
                "ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force "
                "-ErrorAction SilentlyContinue }}").format(profile.name)

        def alive():
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", count],
                    capture_output=True, timeout=30, text=True)
                return int((out.stdout or "0").strip() or 0)
            except Exception:
                return 0

        # Killing and moving straight on is what caused "profile already in
        # use" to keep coming back: the processes take a moment to actually
        # go, and the next launch arrives while they are still holding the
        # directory. So wait until they are really gone.
        for _ in range(10):
            if not alive():
                break
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command", kill],
                               capture_output=True, timeout=30)
            except Exception:
                break
            time.sleep(1.0)

    # Chromium refuses to start on a profile whose singleton lock is still
    # lying about, even with nothing running -- a killed browser never gets
    # to tidy up after itself. These are safe to remove once nothing holds
    # the profile, and Chromium recreates them.
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / name).unlink()
        except (OSError, FileNotFoundError):
            pass


STATE = Path(__file__).resolve().parent.parent / ".gtow_state.json"

# Chromium only writes its cookie store on a clean shutdown. Every run that
# is interrupted -- a timeout, a Ctrl-C, the profile lock being cleared by
# force -- therefore loses the login, and the next run sits on the sign-in
# page waiting for a person who is not watching. Fifteen minutes of one run
# went that way. So the session is saved deliberately to a file the moment it
# is known good, and handed back at the next launch, which survives a kill.
LAUNCH_ARGS = [
    # Sites that spot the automation flag sometimes serve a degraded page or
    # refuse the login form. This is the user's own account in the user's own
    # browser; the flag only gets in the way.
    "--disable-blink-features=AutomationControlled",
    # This machine has under four gigabytes and a browser of the user's own
    # already running in it. Chromium's renderer crashed outright on one run
    # and refused to start on another, so it is asked for as little as it can
    # be given and still show a login form.
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-background-networking",
    "--renderer-process-limit=1",
    "--js-flags=--max-old-space-size=256",
]


def launch(play, profile=DEFAULT_PROFILE, use_chrome=False, headless=False):
    """
    A browser and a context, with any saved session restored.

    Deliberately NOT a persistent profile. A profile keeps the login in a
    directory Chromium owns and only flushes it when it exits politely; this
    keeps it in a file we write ourselves, so an interrupted run costs nothing
    and the next one starts logged in.
    """
    Path(profile).mkdir(exist_ok=True)
    release_profile(profile)
    kwargs = {
        "user_data_dir": str(profile),
        "headless": headless,
        "viewport": {"width": 1500, "height": 950},
        "args": list(LAUNCH_ARGS),
    }
    if use_chrome:
        kwargs["channel"] = "chrome"
    # Back to the persistent profile, which is where the login has actually
    # been living all along. Switching to a bare context seeded from
    # `storage_state` threw that away: the state file had never been written
    # successfully, so every launch afterwards started with no cookies and
    # went straight to the sign-in page. The state file is kept as a second
    # copy, not as the only one.
    return play.chromium.launch_persistent_context(**kwargs)


def save_state(ctx, log=None):
    """Write the session down while it is known to be good."""
    try:
        ctx.storage_state(path=str(STATE))
        if log:
            log("session saved to {}".format(STATE.name))
        return True
    except Exception as exc:
        if log:
            log("could not save session: {}".format(type(exc).__name__))
        return False


TOKEN = Path(__file__).resolve().parent.parent / ".gtow_token.txt"


def save_token(auth, log=None):
    """
    Keep the bearer token, so the next run needs no browser at all.

    This is the point the whole browser exists for. Once the token is in
    hand every request is an ordinary HTTPS GET with a header -- no Chromium,
    no renderer to crash on a machine with two hundred megabytes free, no
    fifteen minutes lost to a login page nobody is watching. Keeping a
    browser alive to do urllib's job was the mistake underneath four failed
    runs.
    """
    value = next(iter((auth or {}).values()), None)
    if not value:
        return False
    try:
        TOKEN.write_text(value, encoding="utf-8")
        if log:
            log("token saved to {}".format(TOKEN.name))
        return True
    except OSError:
        return False


def load_token():
    """The saved bearer token, if there is one. Expiry shows up as a 401."""
    try:
        value = TOKEN.read_text(encoding="utf-8").strip()
    except (OSError, FileNotFoundError):
        return None
    return {"authorization": value} if value else None


def is_logged_in(url):
    """
    Whether the browser is on the real app rather than on the way to it.

    "Not on the login page" is not the same thing: signing in with Google
    leaves the site entirely, and accounts.google.com has no "/login" in its
    address either. Being logged in means being back on GTO Wizard's own
    host, on a page that is not the login page.
    """
    u = urlparse(url)
    if u.netloc != urlparse(HOME).netloc:
        return False
    return not (u.path.startswith("/login") or u.path.startswith("/signup"))


def wait_for_login(page, timeout=1800, log=say):
    """
    Wait for the person to log in, without needing anything from the terminal.

    The sign-in bounces through the identity provider and lands briefly back
    on /login before the app takes over, so the answer has to hold still for
    two polls running before it is believed.
    """
    waited, settled, last = 0, 0, None
    while waited < timeout:
        url = page.url
        if url != last:
            log("  now at {}".format(url[:110]))
            last = url
        settled = settled + 1 if is_logged_in(url) else 0
        if settled >= 2:
            log("logged in -- {}".format(url))
            return True
        if waited % 30 == 0:
            log("waiting for you to log in... ({}s)".format(waited))
        page.wait_for_timeout(5000)
        waited += 5
    log("gave up waiting after {}s".format(timeout))
    return False


def capture_auth(ctx, page, url=None, timeout=25000):
    """
    Take the Authorization header off a request the app makes for itself.

    Reimplementing the sign-in would mean owning their refresh flow forever.
    Letting the app authenticate the way it always does, and noting the
    header it sends, costs one page load and keeps working when they change
    it. The token is the user's, is used only for the user's own account,
    and never leaves the machine.
    """
    found = {}

    def usable(value):
        """
        Whether a header is a real token rather than a placeholder.

        The app fires requests before it has authenticated, carrying
        "Bearer null" -- eleven characters that satisfy any check for the
        header being present. Taking the first Authorization header seen
        therefore captures a token that fails every request afterwards with
        a 401, and the run reports "authorised" while being nothing of the
        kind. A real token here is a JWT of a few hundred characters.
        """
        v = (value or "").strip()
        if not v.lower().startswith("bearer "):
            return False
        token = v[7:].strip()
        return len(token) >= 40 and token.lower() not in ("null", "undefined")

    def on_req(req):
        if "api.gtowizard.com" in req.url and not found:
            for k, v in (req.headers or {}).items():
                if k.lower() == "authorization" and usable(v):
                    found[k] = v
    ctx.on("request", on_req)
    page.goto(url or HOME, wait_until="domcontentloaded", timeout=60000)
    waited = 0
    while not found and waited < timeout:
        page.wait_for_timeout(1000)
        waited += 1000
    return found


class Browser:
    """
    A browser that is only opened if something actually needs it.

    Work that runs off the cache -- scoring hands, reading ranges -- should
    not open a window, need a login or touch the network. Making the browser
    lazy is the difference between a library you can call in a loop and one
    that has to be babysat.
    """

    def __init__(self, profile=DEFAULT_PROFILE, use_chrome=False, log=say,
                 headless=False):
        self.profile, self.use_chrome, self.log = profile, use_chrome, log
        # Headless once the profile is logged in. A visible Chromium wants a
        # few hundred MB it does not need for fetching, and on a small
        # machine already running a browser that is the difference between
        # working and a MemoryError at startup.
        self.headless = headless
        self._play = self._ctx = self._page = None
        self.auth = None

    def open(self, url=None):
        """
        The browser, opened if needed and logged in if it has lapsed.

        Sessions expire. When one does the app quietly serves its login page
        instead of what was asked for, and every request after that comes back
        without a token -- so the useful thing is to notice and wait for the
        person to sign in again.

        Headless changes that entirely: nobody can log into a window that is
        not drawn, and telling them to is worse than useless. So a headless
        run that finds itself logged out reopens ON SCREEN and asks there,
        rather than waiting half an hour for a keystroke into the void.
        """
        self._launch()
        if not self.auth:
            self.auth = capture_auth(self._ctx, self._page, url)
        if not self.auth and not is_logged_in(self._page.url):
            if self.headless:
                self.log("session has expired and this run is headless -- "
                         "reopening on screen so you can log in")
                self.close()
                self.headless = False
                self._launch()
                self.auth = capture_auth(self._ctx, self._page, url)
            if not self.auth and not is_logged_in(self._page.url):
                self.log("\n>>> The GTO Wizard session has expired.")
                self.log(">>> Log in again in the window that is open.\n")
                if wait_for_login(self._page, log=self.log):
                    self.auth = capture_auth(self._ctx, self._page, url)
        if self.auth and not getattr(self, "_saved", False):
            self._saved = True
            save_state(self._ctx, self.log)
            save_token(self.auth, self.log)
        if self.auth and not getattr(self, "_said", False):
            self._said = True
            self.log("authorised ({} char token)".format(
                len(next(iter(self.auth.values())))))
        return self._ctx, self._page

    def _launch(self):
        if self._ctx is None:
            self._play = sync_playwright().start()
            self._ctx = launch(self._play, self.profile, self.use_chrome,
                               headless=self.headless)
            self._page = (self._ctx.pages[0] if self._ctx.pages
                          else self._ctx.new_page())

    def reauth(self, url=None):
        """Tokens expire mid-run; ask the app for a fresh one."""
        ctx, page = self.open(url)
        self.auth = capture_auth(ctx, page, url)
        return self.auth

    @property
    def opened(self):
        return self._ctx is not None

    def close(self):
        if self._ctx is not None:
            try:
                # Save on the way out as well: a run that ends normally has
                # the freshest session of all, and it costs nothing.
                if self.auth:
                    save_state(self._ctx)
                self._ctx.close()
            except Exception:
                pass
            finally:
                self._ctx = self._page = None
        if self._play is not None:
            try:
                self._play.stop()
            finally:
                self._play = None
