# poker_analysis

A poker tracker built from scratch — the analysis half of a Hand2Note, over
our own hand histories from **CoinPoker** and **Ignition**.

A tracker is a database, a stat engine, and a way to look at both. This is
those three things. There is no HUD overlay yet, and that is deliberate: the
overlay shows at the table what a report shows away from it, and the
analysis is where the money is.

## Why build one instead of buying one

Because of what these two sites happen to give away, and the fact that no
commercial tracker holds both at once.

|  | Ignition | CoinPoker |
|---|---|---|
| hole cards | **every player's, folds included** | hero's, plus showdowns (23%) |
| identity | none — a seat, and only within a session | **real names, persisting for months** |
| what it can measure | the pool's actual range | one named person's game |

Ignition shows you the whole deal. Not the hands people were willing to show
at showdown — which are not a fair sample of the hands they held — but every
folded hand too. That means a population's range at a spot can be *counted*
rather than inferred. Almost no site allows this.

CoinPoker gives what Ignition cannot: a name that is the same person next
week, at another table, at another stake. 51 opponents here have 150+ hands.

**Ignition measures the population. CoinPoker measures the person.** The
thing worth building — and the reason this is not a worse copy of software
that already exists — is using the first as the prior for the second.

## What is in here

```
ignition.py     load Ignition hand histories
coinpoker.py    load CoinPoker hand histories        --check
        v
spots.py        one row per player per hand          --check
decisions.py    one row per decision, 50 columns     --check
        v
stats.py        35 stats, defined declaratively      --check
profile.py      what one opponent does differently   --check
check.py        run every check, in dependency order
```

Plus an older layer, from before the engine existed, that still works and
still hardcodes its own SQL: `population.py`, `poptree.py`, `leaks.py`,
`bestresponse.py`, `postflop.py`, `walk.py`. These read GTO Wizard solutions
cached in the same database and price real decisions against a solver. They
are next in line to be rebuilt on the stat engine.

## The database, as it stands

| | |
|---|---|
| hands | 11,961 (May 2025 – Aug 2026) |
| decisions | 93,600 |
| CoinPoker / Ignition | 8,019 / 3,942 |
| named opponents with 150+ hands | 51 |
| cached solver nodes | 1,510 |

## Requirements

Python 3.11 and **nothing else** for everything above — the core is standard
library only, and the database is one SQLite file. Only the GTO Wizard
modules need anything installed (Playwright, and a GTO Wizard subscription).

## Quick start

```bash
python ignition.py  "path/to/ignition/histories"
python coinpoker.py "path/to/coinpoker/histories"
python spots.py
python decisions.py
python check.py
```

Then ask it something:

```bash
python stats.py --pool
python profile.py
python profile.py SomeOpponent
```

`USAGE.md` covers every command and what its output means.
`CONTRIBUTING.md` is the rules for changing any of it — read it before you
touch a derivation. `ROADMAP.md` is the run log: what each session set out
to do, and whether it did it. `the-augster.xml` is the operating framework
this project is developed under, and `CLAUDE.md` is the short version.

## What this is honest about

The binding constraint is **hands, not code**. 51 profileable opponents is
enough to rank a pool and not enough to know a person; a flop stat on 700
hands still carries a ±15 point interval. Every claim here gets sharper with
volume and with nothing else, which is why loading another session is often
worth more than another feature.

Every rate this project prints carries its `n` and a Wilson interval, and
`profile.py` will only call a player unusual when their interval and the
pool's do not overlap. That throws away a lot of true differences. It also
throws away every false one, which at these sample sizes is the trade worth
making.
