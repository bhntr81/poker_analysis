# Usage

Every command, what it does, and how to read what comes back.

---

## Loading hands

Both loaders are safe to re-run. Hands are keyed by the site's own hand id,
so pointing them at a folder that overlaps one already loaded contributes
only what is new. That is the intended way to use them as you keep playing.

```bash
python ignition.py  "C:/path/to/ignition/HH"      # load, or top up
python ignition.py  --stats                       # what is in there

python coinpoker.py "C:/path/to/coinpoker/HH"     # load, or top up
python coinpoker.py --stats                       # per-site breakdown
python coinpoker.py --check                       # prove the import
```

Both walk the folder recursively for `*.txt`. Omaha files are skipped.
CoinPoker hand ids are prefixed `cp-` so the two sites can never collide.

### Reading `--stats`

```
hands by site and format:
  coinpoker  RING     $0.10    3697
  coinpoker  BLITZ    $0.25     795
  ignition   ZONE     $0.25     749

coverage of what each site actually shows:
  ignition     18803 seats,   18802 with cards (100.0%),      9 distinct names
  coinpoker    43666 seats,   10132 with cards ( 23.2%),    777 distinct names
```

`fmt` is `RING`, `BLITZ` (CoinPoker's fast-fold), `ZONE` (Ignition's) or
`MTT`. The coverage block is the point of having both sites: Ignition's
100% is every folded hand; CoinPoker's 777 names are identity.

Your own results are shown as `won - posted - invested`, which is profit.
Beware any tracker — including an earlier version of this one — that reports
the pot you collected as your winnings; a player who builds a big pot and
wins it can still have lost money on the hand.

---

## Building the derived tables

Order matters. Each reads the one above it.

```bash
python spots.py            # one row per player per hand   (~1 min)
python spots.py --check

python decisions.py        # one row per decision          (~2 min)
python decisions.py --check
```

`spots` holds what is true of a whole hand: the cards, the money, whether
they saw a flop, whether they reached showdown. `decisions` holds what was
true at each moment somebody had to act — 50 columns of situation: the pot,
what it cost to call, who raised last, whether they are in position, how
deep, what the flop looks like.

The split is not arbitrary. Seeing a flop is a property of a hand — a player
all-in before the flop sees it and never acts again — so it cannot live in a
table of decisions.

---

## Asking questions

### `stats.py` — the stat engine

```bash
python stats.py --list              # all 35 stats and their definitions
python stats.py --pool              # both pools, side by side
python stats.py --player NAME       # one opponent, every stat
python stats.py --check             # engine vs. the old derivation
```

`--list` prints each stat as the two filters that define it:

```
threebet           3bet   [preflop, per decision, decisions]
  chance: street='preflop' AND facing='open'
  action: agg=1
```

That is the whole design — a stat is a pair of filters over a situation, so
adding one is adding a definition, not writing a module.

Output looks like:

```
  [preflop]
  VPIP                     22.7%    +/-3   n=759
  RFI                      26.2%    +/-4   n=404
  3bet                     13.7%    +/-4   n=285
  fold to steal            77.4%    +/-7   n=146
  [flop]
  cbet flop                37.5%   +/-14   n=40
  fold to cbet             33.3%   +/-17 ? n=27
```

Read it like this:

- **`+/-N`** is half the width of the 95% Wilson interval, in percentage
  points. `13.7% +/-4` means the true figure is very likely between 10 and
  18. It is not decoration — it is the difference between a read and a guess.
- **`?`** marks fewer than 30 chances. Ignore the number.
- **`n=`** is what the percentage is a percentage *of*. `fold to steal
  n=146` means 146 chances to fold to a steal, not 146 hands.

The pattern above is typical and worth internalising: **preflop stats are
usable at a few hundred hands, postflop stats are not.** A player with 780
hands gives you a solid 3-bet number and a meaningless cbet number.

### `profile.py` — what one opponent does differently

```bash
python profile.py                   # everyone worth a plan, ranked
python profile.py NAME              # one opponent in full
python profile.py --check
```

A page of somebody's stats is not a read — most of the numbers on it are
what the whole pool does. So this prints only the stats where the player's
95% interval and the pool's do not overlap, biggest gap first, with what to
do about it:

```
  stat                     them     pool   read
  ------------------------------------------------------------------
  fold to steal           77.4%    69.5% ^  folds blinds to steals -- open every button
                           n=146
  limp                     0.0%     5.4% v  never limps
                           n=404
```

`^` is above the pool, `v` is below. The baseline is the pool **on their own
site**, since comparing a CoinPoker player against a mixed baseline would
make every one of them look tight.

A player with nothing listed is not a failure of the tool. It means that on
this many hands they are indistinguishable from the pool, which is itself
worth knowing before you invent a read.

The leaderboard ranks by how many stats clear the bar, on the reasoning that
someone unusual in six ways is both more exploitable and more reliably
measured than someone unusual in one.

### `population.py` and the solver modules

Written before the stat engine and still hardcoding their own SQL. They work
and they do things the engine cannot yet:

```bash
python population.py            # the pool's leak map and revealed ranges
python population.py --check    # split-half validation, PASS or FAIL
python poptree.py               # the pool beside the solver, same nodes
python leaks.py                 # hero's EV loss per preflop decision
python bestresponse.py          # the exploitative chart vs. the measured pool
```

These need GTO Wizard solutions cached in the database (1,510 nodes are
already there). Fetching more needs Playwright and a subscription.

---

## Verifying everything

```bash
python check.py                 # every check, in dependency order
python check.py --quiet         # just the verdicts
```

```
  PASS  coinpoker
  PASS  spots
  PASS  decisions
  PASS  stats
  PASS  profile

PASS: all 5 checks
```

Run this after any change to a loader or a derivation, and after loading new
hands. The failure it exists to catch is not "a module broke" — it is "a
module was rebuilt and the ones below it were not", which leaves a database
where every table is individually fine and the set of them is wrong.

If something fails, **fix the earliest one first**; the later ones read its
tables and will fail as a consequence.

---

## Backing up

`hands.db` is gitignored and is the only copy of the parsed corpus.

```bash
cp hands.db hands.db.bak
```

Do this before any schema change. Re-parsing from the original histories is
possible but slow, and only works if you still have them.
