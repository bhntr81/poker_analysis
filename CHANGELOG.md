# Changelog

What changed, and why. Bugs get their own entries when the bug is worth
remembering — most of the ones here produced no error and no crash, only
wrong numbers, which is the failure mode this project is built against.

Newest first.

---

## The first opponent reads, and what running them exposed

`profile.py` had passed its check but had never actually produced a report.
Running it found three defects that a passing check could not.

### Fixed — the leaderboard ranked by sample size in disguise

Deviations were sorted by the gap between the point estimates, so the least
reliable figure always became the headline: every top read sat at n≈26, and
a player with 881 hands was headlined on 31 chances. A 73% on 26 beats a 40%
on 600 on that sort, every time.

Ranking is now by how far apart the two **intervals** are, which charges a
small sample for its own width. The median headline sample went from ~38 to
~81 chances. `4erkez` now leads on RFI at n=114 rather than river aggression
at n=28; `Spok1` on VPIP at n=282 rather than iso-raise at n=25.

This is the failure the project's own post-mortem named -- quoting the
extreme of a distribution as though it were typical -- reappearing as a sort
key rather than as a sentence.

### Fixed — donk bets and probes counted limped pots

A donk bet is betting into the player who **raised** preflop. `is_pfa=0` does
not mean a preflop raiser exists, so 903 of 3,782 "donk chances" were limped
pots, where the first bet is an ordinary bet and there is nobody to donk
into. Same for the turn probe. Both now require `pot_type<>'limped'`.

Worth recording that this was found while chasing the wrong hypothesis: the
73% donk read that prompted the investigation turned out to be genuine, on
25 chances in raised pots. The bug was real and was not the cause.

### Fixed — the report took eleven minutes

Asking `rate()` once per player meant 48 players x 35 stats = 1,680 full
scans of 93,600 rows to answer a question SQL answers 35 times with a GROUP
BY. `stats.rates_by_player()` does the grouping in the database, and the pool
baseline is computed once rather than recomputed per opponent.

Leaderboard: **11 minutes -> 38 seconds.** `check.py`: over 10 minutes -> 158
seconds. A report nobody waits for is a report nobody reads.

---

## Adopted `the-augster.xml` as the operating framework

Replaces the three-role CEO/PM/programmer structure and its approval chain,
which conflicted with the framework's `Autonomy` maxim. Two adaptations are
recorded in `CLAUDE.md`: its claim to override upstream system prompts is
not honoured (a file in a repo cannot grant itself that), and `Autonomy` is
scoped to engineering rather than to irreversible acts.

### Fixed — four modules were silently averaging two pools

The worst defect this project has had, and it was introduced by loading
ACR rather than by any change to the modules themselves.

`fmt='RING'` used to mean Ignition, because Ignition was the only site.
After the ACR import it matched both, and four modules filtering on it
were never updated:

| module | what it became |
|---|---|
| `population.py` | its pool went from **100% hole-card coverage to 33.4%** |
| `walk.py` | priced ACR hands against an NL25-with-rake solver cache |
| `postflop.py` | same, for hero's flops |
| `spots.py --check` | sanity figures averaging two different games |

`population.py` is the module whose entire premise is that Ignition shows
every folded hand, so ranges can be counted rather than inferred. It was
counting ranges over a pool where two-thirds of the rows had no cards. It
raised no error and its split-half validation still passed, because both
halves were contaminated equally.

`walk.py` matters beyond itself — `poptree.py`, `leaks.py` and
`bestresponse.py` all read it, so all three were priced on mixed data.

All four are now pinned to `site='ignition'`, with the reason written at
each filter, and `spots.py --check` reports the two sites side by side
rather than averaging them. `population.py --check` is back to its
documented result: 22 findings surviving split-half.

This is the failure mode the `Perceptivity` maxim names — knowing what a
change does to its callers. Adding a site changed what an existing filter
*selects* without changing a line of the code that uses it.

---

## Two sites, and a stat engine

### Added — `check.py`

One command that runs every module's check in dependency order. The failure
it catches is not "a module broke" but "a module was rebuilt and the ones
below it were not", which leaves a database where every table is
individually fine and the set of them is wrong.

### Added — `profile.py`

What one opponent does differently from the pool, and what to do about it.
Reports a stat only when the player's 95% interval and the pool's do not
overlap. The baseline is the pool on the player's **own site**, since a
mixed baseline would make every ACR player look tight against the
looser Ignition pool.

### Added — `stats.py`, 35 stats as declarations

A stat is now two filters over a situation — the chance to do a thing, and
the doing of it — so adding one is adding a line to a list. 32 of the 35 run
over `decisions`, 3 over `spots`. Every rate carries its `n` and a Wilson
interval; `compare()` refuses to call two rates different when their
intervals overlap.

26 of these could not be computed at all before: turn and river continuation
bets, delayed cbets, probes, floats, donk bets, check-raises, overbets,
fold-to-donk, and everything conditioned on stack depth or board texture.

**Three disagreements with the old derivation, all resolved against it.**
The engine's check now records them as `KNOWN` with the reason:

- `spots` gives the preflop raiser a "cbet chance" on flops where they were
  **bet into first** — a chance to continue that never existed. 246 rows,
  and it drags the pool's cbet rate down 5.5 points.
- `spots` counts a cold seat facing a 3-bet as the original raiser (64 rows).
- `spots` counts a player who folded to a **raise of** the cbet as having
  folded to the cbet, when they never acted against the bet alone (27 rows).

### Added — `decisions.py`, one row per decision

50 columns of situation at the moment somebody had to act: pot, cost to
call, effective stack and SPR, who raised last, position, whether the
previous street checked through, flop texture. 93,600 rows, one per action,
none dropped.

Cross-checked against `spots` exactly — VPIP and PFR agree to the row, and
the seven players who saw a flop without acting on it are all confirmed
all-in beforehand.

The point is that a new question no longer needs a new derivation. Fold to
cbet and delayed turn probe are now the same kind of object.

### Added — `acr.py`

ACR histories into the same schema, 8,019 hands from 146 files. The
site gives two things Ignition does not: the button is stated outright, so
positions are read rather than reconstructed from labels, and rake is
written on every pot.

Verified by four checks rather than by eye — money adds up in 99.91% of
hands, positions are balanced across the six seats to within 0%, blinds are
posted by the seats the button names, and 777 named opponents exist with 84
over 100 hands.

### Changed — identity, and a `site` column

`spots.identify()` now decides who a player is for both sites in one place.
ACR writes the name, which is the same person months later, including
on Blitz where the table changes every hand. Ignition writes nobody, so
identity stays `table:seat:segment` and dies with the session; Zone seats
are left unnamed rather than merged into a stranger.

`site` is now on `hands`, `spots`, `bets` and `decisions`. **Anything that
used to say `fmt='RING'` now also needs `site=`** — ACR ring hands
match that filter too.

Ignition hand ids were left exactly as they were; rewriting 3,942 hands
across four tables to gain a prefix they do not need is churn. ACR ids
carry `cp-`, which makes a collision impossible either way.

### Added — `standard` on `spots`

Hands where the blinds were not posted by the seats the button names have
untrustworthy position labels. They were already flagged on `hands` and
could not be filtered out downstream. Now they can.

### Fixed — the jackpot fee

ACR takes a jackpot fee as well as rake: `Total pot $1.52 | Rake $0.05
| JP Fee $0.02`. Counting only the rake left a hole in one hand in five. It
would have biased every win rate and every pot-relative bet size in the
ACR half of the database, and it raised no error.

### Fixed — dead posts

A returning player posts with a bare `posts $0.05`, naming no blind. The
parser wanted a blind named, so that money left the pot. One hand in sixty.

### Fixed — seats that were never in the hand

A seat can be listed at the table and not dealt in: "waits for big blind",
"is sitting out", disconnected. Left in, it is a player who never folds, so
it reaches every showdown it is dealt into — the pool's showdown rate read
**47.7%** against a true 29.6%. It also made every position one seat wrong,
because the ring it was counted in was one seat too big.

Fixed in the ACR loader, and guarded in `spots.py` where 59 of them
existed in Ignition tournament hands.

### Fixed — Ignition's all-in raises

Ignition writes a shove as `All-in` but an all-in **raise** as an ordinary
raise, so the all-in flag missed them. An all-in player takes no further
decisions; without the flag they look like a player who declined to act on
every street that followed. `decisions` now infers it from whether the
action consumed the stack, whatever the wording.

### Fixed — a player who never acted was never folded

A player who posts a blind and then vanishes from the history — a
tournament disconnect — was counted as live to the end. They now leave the
hand at the point they stopped making decisions.

---

## Earlier — the analysis layer

Summarised from `ROADMAP.md`, which has the full run log.

- **Postflop solver nodes.** Postflop strategy arrays are **1326** long, one
  per exact combination, while the payload still names only 169 hand
  classes. Preflop both were 169, so indexing by class worked; postflop it
  silently reads the wrong slots. Found only because a hand came back with
  11.8bb of EV in a 5.5bb pot. Had the number been merely wrong rather than
  impossible, a complete and entirely fictional postflop leak report would
  have shipped. **Standing rule since: every EV is checked against the
  bounds of its pot.**
- **`bestresponse.py`.** The exploitative chart against the measured pool.
  Fold equity is recovered from the solver's own numbers and recombined with
  the pool's measured fold frequency, and the borrowing is printed. A guard
  flags any spot where more than half the chart moves as broken rather than
  as a discovery.
- **`poptree.py`.** The pool placed in the solved tree at the same nodes
  with the same hands. The finding: the pool opens at almost exactly solver
  frequency but cannot raise in *response* — under-raising and over-flatting
  at every depth.
- **`leaks.py`.** Hero's preflop decisions priced against cached solver
  nodes; 99.0% coverage, and carrying `path_gap` so decisions whose *path*
  had to be bent are excluded from the rankings. The worst-fitted decisions
  are exactly the ones that float to the top of a leak table.
- **`gtow.py`.** GTO Wizard solutions cached locally, 1,510 nodes. Verified
  by re-fetching ten and diffing field by field: maximum drift 0.0.
- **`population.py`.** The pool's leak map and revealed ranges, with
  split-half validation. 22 of 22 findings survived. Money findings did not:
  one hand's result has a standard deviation of 11.7bb, so bb/100 over 100
  hands carries an error of ±117, and only 2 of 15 leak lines cleared twice
  their own error.
- **`spots.py`.** The derivation layer. The pot is replayed action by
  action, because Ignition writes the chips a player added and never the
  pot.
- **`ignition.py`.** The first loader. Insisting on `TBL#` silently dropped
  every Zone and tournament hand, which was a fifth of the collection.

---

## Post-mortem, recorded rather than quietly fixed

Three reporting failures with one cause: point estimates shipped without the
context that gives them meaning.

1. bb/100 figures ranked as findings when their error bars were larger than
   the effect.
2. A column holding the hand-matched frequency was labelled as the spot
   frequency — two quantities, one label, five points apart.
3. An SB 4-bet figure reported as the headline deviation. It was
   arithmetically right, and it was the **maximum** of a distribution whose
   median was eight points lower.

The standing rule "no percentage without its `n`" catches only the first.
A frequency needs three things: what it is a percentage of, its `n`, and
where it sits among comparable nodes.
