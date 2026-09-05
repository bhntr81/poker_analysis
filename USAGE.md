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

python lines.py            # how the betting went          (~15 s)
python lines.py --check

python strength.py         # what each hand is              (~20 s)
python strength.py --check

python players.py          # who each player is             (~15 s)
python players.py --check

python decisions.py --index   # just the indexes, without rebuilding
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

### Comparing two populations

```bash
python query.py --reg --regs-only --versus "--reg --with-fish"
python query.py --reg --regs-only --versus "--reg --with-fish" --show fold_to_river_bet
```

Prints both rates, the **difference**, and an interval on that difference —
which is not the same question as whether the two rates' own intervals
overlap, and is far less strict. It also corrects the p-values for how many
stats were compared at once, because thirty comparisons will produce one at
p < 0.05 with nothing going on.

**Name the stat with `--show` and it is a test. Compare all thirty and the
best one is a hypothesis.** The correction charges by the size of the
family, so one named question is worth much more than the pick of thirty.

Where nothing survives, the footer says how big a difference the sample
*could* have found — "no difference" and "not enough hands to see one" are
different findings.

---

### Filtering by what the turn or river did

```bash
python query.py --turn-card flush --street turn      # the turn brought a flush card
python query.py --river-card pair --pot 3bet         # the river paired the board
python query.py --turn-card brick --board dry        # a dry flop and a turn that changed nothing
```

`over` · `pair` · `flush` · `straight` · `brick`

A **flush card** is one that takes some suit to three on the board. On the
turn, **straight** means the board has come to one card off a straight; on
the river it means that card arrived. Those are the events that matter on
each street, and one definition covering both would describe neither. A
**brick** did none of the four.

`--board` still describes the flop, which arrives all at once. These
describe a single card landing on a board that was already there.

---

### What the range actually held

```bash
python query.py --pool --street river --facing bet --range
python query.py --pool --street flop --facing bet --range
```

A frequency is a number about somebody; a range is a number about what to
do. "He bets the river 40%" tells you nothing until you know how much of
that 40% is a hand that cannot call.

```
  two pair        30.9%     264        ###############
  middle pair     16.9%     144        ########
  board pair      12.4%     106  weak  ######
  high card       10.4%      89  weak  #####

  WEAK            25.4%   -- hands that cannot call
  STRONG          74.6%
```

The line between weak and strong is drawn under **middle pair**, and it is
one list in `strength.WEAK` precisely so that disagreeing with it is a line
changed rather than an argument.

**It is the range that was *seen*.** Ignition shows every hand at showdown
including the folds; ACR shows 23%. So on an ACR-heavy filter this describes
the hands that reached showdown, which is the stronger half of what was
really there. The header says what fraction of the selection had cards to
read, every time.

---

### Filtering by what the hand is

```bash
python query.py --made "top pair" --kicker weak     # top pair, bad kicker
python query.py --made set,trips --street river     # sets and trips
python query.py --fd nut --sd gutshot               # the nut flush draw with a gutshot
python query.py --combo-draw --street flop          # both draws at once
python query.py --drawing --pot 3bet                # any draw in a 3-bet pot
python strength.py --common                         # what the pool turns up with
```

| | |
|---|---|
| `--made` | high card, board pair, weak pair, under pair, middle pair, top pair, overpair, two pair, trips, set, straight, flush, boat, quads, straight flush |
| `--kicker` | top, good, weak — only where a pair uses a hole card |
| `--fd` | nut, second, weak, backdoor |
| `--sd` | oesd, double gutshot, gutshot |

Four columns rather than one, so a combo draw is a flush draw and a straight
draw *at once* instead of a fourteenth category, and "pair plus a flush
draw" is `--made "top pair" --fd nut`.

**These need the cards**, and the cards are known for 20,465 postflop
decisions out of 94,017 — every Ignition hand including the ones that
folded, and 23% of ACR's. So they narrow hard, and a small `n` here is the
data rather than the filter. A `board pair` is a pair on the board that the
player does not hold; four hearts on the board is not a flush draw.

---

### Filtering by what kind of player

```bash
python query.py --reg --vs-reg          # how regulars play each other
python query.py --reg --vs-fish         # and how they play a recreational
python query.py --vs-player NAME        # against one named opponent
python players.py --list reg            # who the regs are
python players.py NAME                  # one player in full
```

A **reg** plays a third of hands or fewer and raises at least one in ten. A
**fish** is loose or passive — over a third of hands, or almost never
raising while still coming in often. Everybody there is not enough evidence
about is **unknown**, and neither filter selects them, so the two do not add
up to the pool.

`--vs-reg` and `--vs-fish` only mean anything while one opponent is left:
in a three-way pot there is no "the other player". They select heads-up
decisions by construction, which is 17% of the database.

`--regs-only` and `--with-fish` ask the same thing of a pot of any size —
everybody still in is a reg, or at least one of them is a fish — and between
them cover 63%. Use these unless the matchup really has to be heads up.

Only ACR names people. An Ignition ring identity lasts as long as somebody
stays in the seat, and Zone names nobody.

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

### `population.py`

Written before the stat engine and still hardcoding its own SQL. It does one
thing the engine cannot: split-half validation of a finding about the pool.

```bash
python population.py            # the pool's leak map and revealed ranges
python population.py --check    # split-half validation, PASS or FAIL
```

The solver modules that used to be documented here — `poptree.py`,
`leaks.py`, `bestresponse.py`, `walk.py`, `postflop.py` and `gtowizard/` —
were removed on 5 Sep 2026. They priced play against cached GTO Wizard
solutions, which is answering "what is correct" and is a different program.
`git checkout 4927a11 -- walk.py gtowizard/` brings any of them back.

---

## Keeping it up to date

The window checks GitHub on every launch, on a worker thread, and says
something only when there is something to say. **Update → Update from
GitHub now** does it on demand; **Update → What version is this?** says
which commit is running and where the database is.

```bash
python update.py            check, and fast-forward if there is one
python app.py --no-update   start without checking
```

Three rules it does not bend:

* **Fast-forward only.** It refuses to merge, refuses to rebase, and refuses
  outright if anything local would have to be reconciled. Uncommitted work
  is never touched — the update simply does not happen, and says why.
* **It never restarts anything.** Python has already imported its modules by
  the time the pull finishes, so new code on disk is not new code in memory.
  The window says an update arrived; a restart uses it.
* **It never blocks.** No git, no network, no remote, a detached head — each
  is an ordinary answer rather than an error. Being unable to check is not a
  problem with the program.

A packaged .exe cannot replace itself while running, so there it tells you a
newer commit exists rather than pretending to update.

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
