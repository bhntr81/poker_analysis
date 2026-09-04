"""
Package the tracker as one Windows executable.

The program is standard library only, so nothing here is needed to *run* it
-- PyInstaller is a build-time tool and never a dependency of the thing it
builds. `python app.py` works without any of this.

**The database is not bundled.** It is the user's data, it is eighty-odd
megabytes, and it changes every time a session is loaded; freezing a copy of
it inside the executable would ship a snapshot that silently goes stale. The
executable looks for `hands.db` beside itself instead, which is also what
makes the pair of them movable: copy the exe and the db to another machine
and it works with no Python installed at all.

    python build.py            build dist/poker_analysis.exe
    python build.py --check    is the toolchain here, and did it work
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
DIST = HERE / "dist"
EXE = DIST / "poker_analysis.exe"

# Every module the app reaches at runtime. PyInstaller finds imports by
# reading the code, and it reads these correctly -- they are listed anyway
# because a missed one produces an executable that starts and then fails on
# the first click, which is a worse way to find out.
MODULES = ["query", "stats", "equity", "decisions", "spots"]


def build():
    if shutil.which("pyinstaller") is None and not _module("PyInstaller"):
        print("PyInstaller is not installed. It is only needed to build:\n"
              "    python -m pip install pyinstaller")
        return False
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        # No console window behind the app. A GUI that drags a black
        # terminal around with it is the thing this was meant to stop.
        "--windowed",
        "--name", "poker_analysis",
        "--distpath", str(DIST),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
        "--noconfirm",
    ]
    for m in MODULES:
        cmd += ["--hidden-import", m]
    cmd.append(str(HERE / "app.py"))
    print(" ".join(cmd[:8]), "...")
    proc = subprocess.run(cmd, cwd=str(HERE))
    if proc.returncode:
        print("build failed")
        return False
    print(f"\nbuilt {EXE}  ({EXE.stat().st_size / 1e6:.1f} MB)")
    print("put hands.db next to it and double-click.")
    return True


def _module(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def check():
    """
    The toolchain is present, and anything already built actually starts.

    Starting is the whole of what is checked. A packaged GUI that fails on
    launch does so with no console and no message -- the window simply never
    appears -- so the one thing worth proving automatically is that the
    executable runs its own `--check` and exits zero.
    """
    fails = []
    have = _module("PyInstaller")
    print(f"pyinstaller available     {'yes' if have else 'no (build-time only)'}")

    if EXE.exists():
        proc = subprocess.run([str(EXE), "--check"], capture_output=True,
                              text=True, timeout=300, cwd=str(HERE))
        ok = proc.returncode == 0 and "PASS" in (proc.stdout or "")
        print(f"built exe runs its check  {'PASS' if ok else 'FAIL'}")
        if not ok:
            fails.append("the built executable does not pass its own check")
            print((proc.stdout or proc.stderr or "")[-400:])
        near = EXE.parent / "hands.db"
        print(f"database beside the exe   "
              f"{'yes' if near.exists() else 'no -- copy hands.db into dist/'}")
    else:
        print("built exe                 not built yet (python build.py)")

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    sys.exit(0 if build() else 1)
