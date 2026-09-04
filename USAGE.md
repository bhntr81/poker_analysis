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

python acr.py "C:/path/to/acr/HH"     # load, or top up
python acr.py --stats                       # per-site breakdown
python acr.py --check                       # prove the import
```

Both walk the folder recursively for `*.txt`. Omaha files are skipped.
ACR hand ids are prefixed `cp-` so the two sites can never collide.

### Reading `--stats`

```
hands by site and format:
  acr  RING     $0.10    3697
  acr  BLITZ    $0.25     795
  ignition   ZONE     $0.25     749

coverage of what each site actually shows:
  ignition     18803 seats,   18802 with cards (100.0%),      9 distinct names
  acr    43666 seats,   10132 with cards ( 23.2%),    777 distinct names
```

`fmt` is `RING`, `BLITZ` (ACR's fast-fold), `ZONE` (Ignition's) or
`MTT`. The coverage block is the point of having both sites: Ignition's
100% is every folded hand; ACR's 777 names are identity.

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

python lines.py            # how the betting went          (~10 s)
python lines.py --check
```

`lines` writes onto `decisions`, so it goes last and it goes again every
time `decisions` is rebuilt. Skip it and the line filters match nothing,
without complaining.

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

### Filtering by how the betting went

The betting on each street is written down as a short string, and you filter
it with a pattern:

```bash
python query.py --flop XBC              # checked, bet, called
python query.py --flop "XB*"            # checked to somebody who bet
python query.py --turn XX               # checked through
python query.py --pre "*R*R*"           # somebody 3-bet
python query.py --line "*R*R*/XBC/XX/*" # all of it at once
python query.py --node "*/XB"           # it is your turn, facing a bet
```

| | |
|---|---|
| `F` fold | `X` check |
| `C` call | `B` bet |
| `R` raise | `A` all-in |
| `*` anything | `?` any one action |

A `/` separates streets. Add a size letter after a bet to say how big:
`s` a third or less, `m` half, `l` two-thirds to three-quarters, `p` pot,
`o` an overbet. So `--flop XBmC` is a flop that went check, half-pot bet,
call. Sizes are buckets rather than percentages because a filter written
against an exact percentage matches almost nothing.

`--node` is the one to reach for when measuring a decision: it is the hand
cut short at the moment the player had to act, so it never contains what
they did next. `--line` is the whole hand and includes it.

`python lines.py --common flop` lists the lines that actually occur.

---

### `query.py` — the one you will use most

Every stat has always taken a filter; nothing could supply one. This can.

```bash
python query.py --help                                  every filter there is
python query.py --pool --pot 3bet --street flop --ip    what the pool does there
python query.py --hero --board mono --results           what it made you
python query.py --player dblj32 --pos BTN --hands       which hands they were
python query.py --where "eff_bb > 150 AND fl_paired=1" --stats
```

Filters combine freely, and there are three things to ask for.

**`--stats`** (the default) runs every stat that *can* occur inside the
filter, and silently drops the ones that cannot — asking for a preflop stat
inside `--street flop` gives nothing, which is correct rather than a bug.

```
filter: pool, site acr, pot 3bet, street flop, ip
524 decisions match

  [flop]
  cbet flop                70.7%    +/-7   n=184
  fold to cbet             42.6%    +/-7   n=190
  [sizing]
  bets a third or less     50.6%    +/-7   n=170
```

**`--results`** is the money, and it works differently on purpose. Money is
a property of a whole **hand**; "in position on a monotone flop" is a
property of a **decision**. So the filter selects decisions, and the money
is then summed over the hands those decisions occurred in — because a player
in position on a monotone flop won or lost the whole pot, not the part of it
after the flop.

```
filter: hero, site acr, board mono
  hands                  69
  net                +307.4 bb   ($+47.34)
  per 100 hands      +445.6 bb/100
  error on that         141 bb/100   <- and this is why
```

That last line is not decoration. One hand's result has a standard deviation
around 11.7bb, so the error on a win rate is roughly `1170/sqrt(n)`. At 69
hands it is ±141bb/100, which is wider than the +445 it is qualifying.
**Win rates need thousands of hands. Frequencies need hundreds.** Filter
hard and you will be looking at frequencies, which is the right thing to
look at anyway.

**`--hands`** lists the hands themselves, newest first, with the board and
what each one made. In the interface a row opens; on the command line you
open one by id:

```bash
python query.py --hand 5331315698
```

That replays it — who sat where, what they held, and the action street by
street with the pot before each decision. On Ignition every player's cards
are there including the folded ones, because the site shows them. On ACR a
seat reads `--` when the hand was never shown, which is different from
having been dealt nothing.

### When nothing matches

Some perfectly reasonable filters cannot match anything, and rather than a
blank page you get the reason:

```
$ python query.py --ip --street preflop
0 decisions match
  'ip' and 'street preflop' never occur together -- who acts last is only
  settled once the flop is out
```

The ones worth knowing, because they will all be clicked eventually:

| filter | why it is empty |
|---|---|
| `--ip` / `--oop` with `--street preflop` | who acts last is only settled once the flop is out |
| `--pfa` with `--street preflop` | there is no preflop aggressor until preflop is over |
| `--board ...` with `--street preflop` | the flop had not come |
| `--facing bet/raise/check` with `--street preflop` | those are postflop words; preflop uses `open`, `3bet`, `4bet` |
| `--facing open/3bet/4bet` with a postflop street | and the reverse |
| `--pot limped` with `--street preflop` | a pot is not limped until preflop is over; during it, it is `unopened` |

None of these is a bug. They are the filter asking for something that could
not have happened, and the point of the message is that you can tell the
difference without having to guess.

Filters worth knowing: `--hero`/`--pool`, `--site`, `--player`, `--pos`,
`--street`, `--pot`, `--facing`, `--ip`/`--oop`, `--deep N`/`--short N`,
`--board mono,paired,connected,...`, `--combo`, `--multiway`/`--headsup`,
`--since`/`--until`, and `--where` for raw SQL over `decisions` when the
named flags run out. `--help` prints the full list with the SQL each becomes.

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

### `opponents.py` — what one opponent does differently

```bash
python opponents.py                   # everyone worth a plan, ranked
python opponents.py NAME              # one opponent in full
python opponents.py --check
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
site**, since comparing a ACR player against a mixed baseline would
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
  PASS  acr
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
