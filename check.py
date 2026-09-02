"""
Run every module's check, in the order the tables depend on each other.

Each module already knows how to prove itself. What was missing was one
command that asks all of them at once, which matters because the failure
this catches is not "a module broke" -- it is "a module was rebuilt and the
ones downstream of it were not". A `spots` rebuilt after a schema change
with `decisions` left alone is a database where every table is individually
fine and the set of them is wrong.

    python check.py            run everything, exit non-zero if anything fails
    python check.py --quiet    just the verdicts
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent

# In dependency order, so the first failure is the earliest cause rather
# than the loudest symptom.
CHECKS = [
    ("coinpoker", "the CoinPoker import: money, positions, blinds, identity"),
    ("spots", "the per-hand derivation: figures with a known shape"),
    ("decisions", "the per-decision derivation, against spots exactly"),
    ("stats", "the stat engine, against spots where they overlap"),
    ("profile", "opponent profiles: deviations really clear their intervals"),
]


def run(module, quiet):
    proc = subprocess.run(
        [sys.executable, str(HERE / f"{module}.py"), "--check"],
        capture_output=True, text=True, cwd=str(HERE))
    out = (proc.stdout or "") + (proc.stderr or "")
    if not quiet:
        print(out.rstrip())
    return ("PASS" if proc.returncode == 0 else "FAIL"), out


def main(argv):
    quiet = "--quiet" in argv
    results = []
    for module, what in CHECKS:
        if not quiet:
            print(f"\n{'=' * 70}\n{module}.py -- {what}\n{'=' * 70}")
        verdict, _ = run(module, quiet)
        results.append((module, verdict))

    print(f"\n{'=' * 70}")
    for module, verdict in results:
        print(f"  {verdict:5} {module}")
    bad = [m for m, v in results if v == "FAIL"]
    print()
    if bad:
        print(f"FAIL: {', '.join(bad)}")
        print("Fix the earliest one first -- the later ones read its tables.")
        return 1
    print(f"PASS: all {len(results)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
