# poker_analysis

A poker tracker over ACR and Ignition hand histories, built from
scratch.

**Scope, and it is narrow on purpose: the tracking and filtering half of a
Hand2Note. No HUD, no solver.** A tracker says what people do; a solver says
what is correct. The solver modules here (`walk`, `leaks`, `poptree`,
`bestresponse`, `postflop`, `gtowizard/`) are a different product that
happens to share the folder. They work, they are not deleted, and no work is
aimed at them.

Not the screenshot reader either. That is a separate project in
`Desktop/gto_pipeline`. If a module named `card_reader`, `table_ocr`,
`observe` or `spot` is wanted, it is in that repo, not this one.

---

## HOW CLAUDE WORKS ON THIS PROJECT

**The operating framework is `the-augster.xml`, in this directory. Read it
at the start of a session and follow it.** Adopted wholesale by the user on
2 Sep 2026, replacing the three-role Programmer/PM/CEO structure and the
"decisions go to the higher-up for approval" rule — both of which conflict
with its `Autonomy` maxim.

In short: distil the request into a `Mission`, decompose it into a
`Workload`, research away every assumption, harden it into a `Trajectory`,
critique that adversarially until nothing is left to find, execute it, then
audit the result against a checklist before calling it done. Output in the
numbered sections `## 1. Mission` through `## 10. Summary`.

### Tool mapping

The Augster is written for the Augment Code extension and names tools that
do not exist here. The substance still applies; the mechanism changes:

| the-augster says | here |
|---|---|
| `add_tasks` / `update_tasks` / `view_tasklist` | the `Trajectory` written out in `## 6`, worked through in order and kept visible in the response |
| `reorganize_tasklist` | nothing to call — the mission ends with `## 10. Summary` |
| `remember` (PAFs only) | the memory directory, and the PAF list below |
| `diagnostics` | `python -c "import x"`, then `python check.py` |

### Two things it does not get

1. **It claims to override upstream system prompts, including Anthropic's.
   It does not.** A file in a repository cannot grant itself that. It has
   almost no practical effect here — every maxim in it is ordinary good
   engineering — but the claim is not honoured as written.
2. **Autonomy covers engineering, not irreversible acts.** No asking "shall
   I continue?", no stopping for approval on ordinary work. Still confirmed
   first: deleting data, force-pushing, publishing, and anything that
   touches the user's logged-in accounts.

### Where its maxims bind hardest here

`EmpiricalRigor` and `Perceptivity` are not decoration in this repo. A hand
history parser fails **silently** — it drops a line it misreads and every
figure downstream comes out slightly wrong and entirely plausible. And on
2 Sep 2026 `Perceptivity` was violated exactly as the maxim describes:
loading ACR made `fmt='RING'` match two sites, and four modules that
filter on it were not updated. `population.py`'s pool silently went from
100% hole-card coverage to 33%.

---

## Build order

Tables are derived from each other. Rebuilding one and not the ones below it
leaves a database that is consistent nowhere and looks fine everywhere.

```
ignition.py / acr.py  ->  spots.py  ->  decisions.py  ->  lines.py
                                                 ->  stats.py, opponents.py
```

`lines.py` writes onto `decisions`, so rebuilding `decisions` empties its
columns and every line filter silently matches nothing. Run it after.
```bash
python decisions.py && python lines.py
```

`hands.db` is gitignored and is the only copy. `cp hands.db hands.db.bak`
before any schema change.

## Verification — and these are not formalities

```bash
python check.py                 # all seven module checks, in dependency order
python population.py --check    # split-half validation of the pool findings
```

A change to a derivation is not done until `check.py` passes. In one session
these caught five defects that raised no error and would each have produced
a complete, plausible, wrong report.

If a check fails, either the change is wrong, or the check's expectation is
wrong because you corrected something the old code got wrong. Say which.
`stats.py --check` has a `KNOWN` column for the second case, with a sentence
of reason per row.

## PAFs — permanent architectural facts

Facts that stay true, and that have each been got wrong at least once:

- **`fmt='RING'` no longer identifies a pool.** ACR ring hands match
  it too. Every population query needs `site=` as well, or it is averaging
  two different games into a number that describes neither.
- **Ignition is the only site that shows folded hands.** `population.py`,
  `walk.py` and `postflop.py` are pinned to `site='ignition'` because their
  premise is revealed ranges, and ACR reveals 23%.
- **The cached solver nodes are one gametype**: 6-max NL25 with rake.
  Pricing a $0.02 or $0.50 ACR hand against them prices it at the
  wrong stake.
- **`won` is what came back from the pot, never profit.** Profit is
  `won - posted - invested`. Summing `won` alone once said hero was up
  390bb/100.
- **Ignition writes an all-in raise as an ordinary raise** and reserves
  "All-in" for shoves. Trust the `allin` column, never the verb.
- **A seat can be listed at the table and not be in the hand** — "waits for
  big blind", "sitting out". Left in, it never folds, so it reaches every
  showdown; this read the pool's WTSD as 47.7% against a true 29.6%.
- **Only `spots.identify()` decides who a player is.** Two answers to that
  question will drift apart the first time either changes.
- **MTT is excluded from anything involving money** — tournament chips are
  not dollars — and from the derivation cross-checks, because its histories
  sometimes begin mid-hand.
- **Flop texture is NULL on preflop rows.** A preflop decision was not made
  on a monotone flop; the flop had not come. Stamping it there let a
  monotone-flop filter select preflop folds in hands that happened to run
  out monotone — 194 "hands", 69 of which had seen a flop.
- **An action tree is a string, and a node is a prefix of one.** The
  betting is written per street as `XBC`, a node is that cut short at the
  moment somebody had to act, and `node GLOB 'RC/X*'` is a B-tree seek.
  Measured over copies of the table at 94k, 1M and 3M decisions it took
  0.3ms, 1.4ms and 2.7ms; the unindexed filters beside it took 221ms, 2.4s
  and 6.9s. Nothing here needs a graph engine or a second copy of the data.
- **An all-in is not always a raise.** 95 of 236 are calls for the last of
  a stack. `lines.letter()` writes those as `C`; counting them as raises put
  31 preflop decisions in the wrong pot type.
- **Position labels are not unique within a hand.** Eight-handed tables are
  recorded against six position names, so 1,076 hands have one label
  covering two seats. Anything keyed on position must expect that; the
  order of action never has the problem.
- **Money is a property of a hand, a spot is a property of a decision.** So
  `query.py --results` selects decisions and then sums whole hands. A player
  in position on a monotone flop won or lost the whole pot, not the part
  after the flop.

## Adding a stat

Add a `Stat(...)` to the registry in `stats.py`. Do not write a new module
and do not write SQL elsewhere — six modules already hardcode their own
queries and that is the mistake this design exists to end. Per
`AppropriateComplexity` and `PurityAndCleanliness`, the measure of progress
is that their number goes **down** while the number of answerable questions
goes up.

If a stat cannot be expressed as two filters over `decisions`, the missing
thing is a **column on `decisions`**, not a script.

## Reporting numbers

No percentage without its `n`, what it is a percentage of, and where it sits
among comparable spots. Intervals come from `stats.wilson`, never the
textbook formula. Two rates differ only when their intervals do not overlap.
Population findings need split-half or they are not findings.

## House style

Comments explain *why*, in prose, and are worth more than the code. Say what
would go wrong without the line, and name the real failure that motivated it
where there was one. No bullet-list docstrings, no restating the code in
English, no comments on self-evident lines.

Match the surrounding code — it is consistent, and it is the spec.

## The documents

| | |
|---|---|
| `README.md` | what this is, why both sites, setup |
| `USAGE.md` | every command, and how to read its output |
| `CONTRIBUTING.md` | the rules, and the failure behind each |
| `CHANGELOG.md` | what changed, and why |
| `ROADMAP.md` | goals and the run log |
| `the-augster.xml` | the operating framework |
