# Closed-loop generators — continuation, policy, and drift repair

Status: **spec written 2026-08-30 (Stephen's directive, same
evening); implementation begins on this branch. Post-1.4.4 cargo:
regenerated books are a deliberate, capture-invalidating event and
feed the September bundle-regeneration + Abe's fresh corpus — not
Tuesday's tag.**

## 1. The disease, diagnosed in the source

`build_alex.py`'s own docstring confesses it: the original phase
scripts paid cards from the LIVE running balance; the deterministic
rebuild replaced those queries with CONSTANTS ("$650 pay-in-full",
"$375/mo", `MONTHLY_SAVINGS_SWEEP`). Meanwhile the daily/seasonal
generators charge variable amounts. Fixed outflows against variable
inflows drift by construction — cash piles in Checking, "pay-in-
full" cards ratchet upward (the Chase Sapphire $23k-vs-$12k-limit
scar), and no one ever invests the surplus, because no generator
ever LOOKS at the book it is writing.

## 2. The cure: one engine, closed loop

### 2.1 The policy layer (the new part)

A `PersonaPolicy` object per book. At each month-end decision date
the policy OPENS THE BOOK (via `GnuCashBook` — the generators
dogfood the server's own reporting, the same way the bookkeeper
does) and derives flows from actual state:

- **Card payment = f(statement balance)**, not a constant.
  Pay-in-full personas (Alex, both cards; Sabine) pay the true
  balance as of the statement day. The bounded revolver (Lin Wei —
  her interest burden is load-bearing for the debt-payoff demos)
  pays minimum-plus, with a HARD invariant: utilization stays
  under the card's `credit_limit` slot.
- **Sweep = f(actual surplus)**: `max(0, checking − buffer)` at
  month end, split per persona (Alex: savings share monthly,
  VTSAX share quarterly via the existing lot-per-sweep path;
  Sabine: EUR savings; Lin Wei: thin sweeps — she is deliberately
  cash-tight). Never overdrafts: the sweep is computed AFTER the
  month's outflows post.
- Buffers, shares, minimums, and revolver bounds are per-persona
  policy constants — the ONLY constants left; everything they act
  on is read from the book.

### 2.2 The continuation engine (mostly already exists)

Key insight: the `gen_*(through)` stream generators are
deterministic over their whole range. Continuation = run them with
the NEW `through`, keep only transactions dated AFTER the book's
current cutoff, and write via the existing `write_bulk`. The same
per-(persona, year, month) determinism that makes rebuilds
reproducible makes continuations idempotent — re-running a month
emits identical transactions.

`continue_book.py <persona> --through DATE`:
1. Open the existing book; find `last_date` (the frozen prefix's
   edge — NEVER rewritten; regenerated books keep the committed
   history as a byte-stable prefix).
2. Emit the deterministic streams for (cutoff, through] —
   recurring, daily/weekly, seasonal, income, business events.
3. At each month boundary, run the policy pass (2.1) against the
   book as written so far: card payments, sweeps, investment lots.
4. Verify (2.4), save.

Full rebuild = today's prefix build through its cliff date, then
THE SAME continuation engine for every month after. One code path
for "fix the drift going forward" and "generate better forever" —
the disposition-chokepoint pattern applied to book generation.
`gen_savings_sweep` and the fixed-amount arms of
`gen_credit_cards` are retired; their 2025 narrative arcs (Chase
payoff Jan–Jun, the Amex August late-fee story) stay in the
prefix as scripted HISTORY, which is what they always were.

### 2.3 Drift repair — in narrative, on first contact

On a drifted book, the policy's first pass finds out-of-band
state (cash far above buffer; a "pay-in-full" card carrying five
figures). It repairs IN NARRATIVE, dated in the first continued
month, with honest descriptions a bookkeeper would believe:

- "Chase Sapphire — balance payoff (catching up after the
  summer)" — the full statement balance, exactly what a human
  with $100k liquid does the day they notice 24% APR.
- "Brokerage sweep — accumulated surplus" — staged over 1–3
  months if the pile is large (nobody moves $80k in one panic).
- Lin Wei's repair is DIFFERENT by policy: pay DOWN to her
  revolver bound, not to zero — the persona keeps her deliberate
  debt profile, under the limit.

History is never rewritten; the correction is appended story.
The audit trail reads like a person got their act together,
because that is the simulation being run.

### 2.4 Verification (extends rebuild_all's verify step)

Post-run invariants, per book, month-by-month over the continued
range:
- No overdraft: checking ≥ 0 at every month end.
- Buffer band: month-end checking within [0.5×, 3×] of the
  persona buffer (drift alarm, not exactness).
- PIF cards ≈ 0 after each statement payment (± one cycle's
  charges); revolver under `credit_limit`, always.
- Books still open via piecash; txn floors; recency (existing
  checks).
`rebuild_all.py` gains `--continue-only` (repair+extend existing
books without prefix rebuild) and runs these invariants either
way.

## 3. Sequencing and blast radius

- Post-1.4.4-tag work. Regeneration invalidates capture oracles
  (standing policy: deliberate event). The repaired books ship to
  the repo ONCE (frozen-prefix policy: this is a "date-decay +
  new-coverage" regeneration, the sanctioned kind), then the CI
  bundle-regeneration picks up `--continue-only` freshness later.
- Alex first (richest policy surface), then Lin Wei (revolver
  policy), then Sabine (simplest). Ship per-persona, verify
  per-persona.
- Market data: existing committed cache; `--skip-refresh`
  offline determinism preserved. Cache tail must cover the
  continued range — refresh is a separate, network-touching step
  (existing pipeline).

## 4. What this is not

- Not a rewrite of the prefix builders — the 2025 narrative
  phases stay as scripted history.
- Not personality simulation beyond finance: policies decide
  MONEY FLOWS only; merchants/cadence/seasonality stay with the
  existing stream generators.
- Not release-blocking for v1.4.4, and not October's headline
  (that's the price auto-updater). This is the 1.5.x persona work
  Stephen slated, pulled forward to spec because the drift
  offended its author on a Sunday evening.
