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
   IMPLEMENTATION NOTE (audit finding): the txn-dict streams
   (`gen_*`) filter trivially by date, but the book-WRITING
   functions (`run_business`, `run_investments`,
   `run_vtsax_sweep`) loop over their own date plans and write
   directly — each gains a `since: date` cutoff parameter and
   skips events ≤ cutoff, or continuation double-creates the
   prefix's invoices and lots. Same one-line guard in each loop.
3. **Advance scheduled-transaction metadata** (audit finding —
   this is pathology #4): the SX templates' `last_occur` fields
   are stale on the frozen books, which is exactly why the
   dashboards scream "14 overdue" — the transactions exist (the
   recurring stream wrote them) but the templates never learned.
   Continuation sets each enabled SX's `last_occur` to its
   latest realized occurrence ≤ through, so a freshly continued
   book opens with a CLEAN dashboard: no overdue-scheduled
   noise, no stale-price wall (the cache-priced continuation
   refreshes prices too). The demo book's first impression is
   the whole point of the program.
4. At each month boundary, run the policy pass (2.1) against the
   book as written so far. Card statement days come from each
   card's `statement_close_day` slot where set (the existing
   slot convention), else month-end; payment posts 3–7 seeded
   days later.
5. Verify (2.4), save.

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

The repair pass also covers BOOK-QUALITY debts, not just
financial ones: Sabine's €48.50 Ausgleichskonto imbalance clears
with a dated reclassification in the repair month ("Korrektur —
ungeklärte Differenz aufgelöst") — the i18n book loses its one ⚠
honestly, in its own language. Measured balances, per-persona
prescriptions, and the derived policy constants live in
DRIFT_ANALYSIS.md beside this spec; implementers start there.

OPEN QUESTION (maintainer ruling wanted): does repair extend to
the SAVINGS piles (Alex $90k, Lin Wei ¥150k — accumulated by the
same open-loop sweep constants), or does policy govern only
checking-surplus flows going forward? Options: (a) leave savings
as-is, policy manages new surplus only (smallest repair); (b) a
slow rebalance drain (quarterly savings→investment transfers at
persona-plausible size) until savings hits a target multiple of
the buffer. (b) reads more like a person, but it is a persona
call, not an engineering one.

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

## 3. Sequencing and the endgame (RULED 2026-08-30)

**They are DEMO BOOKS, not frozen oracles** — the maintainer's
correction, and it re-scopes the whole program. A demo book's one
job is to be alive and current on first open; "frozen" was the
testing frame (byte-stable capture fixtures), and it retires with
this work. The endgame:

- **Clones carry the frozen state, plus the updater** (refined
  ruling, same evening). The committed blobs STAY tracked at
  their current bytes forever — the permanent starting prefix,
  never re-committed. `continue_book` doubles as the UPDATER: any
  clone runs it to bring its working copies current through
  today (working-tree drift, never staged — the drift-by-design
  policy already covers it). Zero new blob bytes ever enter
  history.
- **The rates cache is the one living artifact in git**: a
  small, textual PR each month extends
  `market_data_cache.json`'s tail so the updater stays
  realistic offline. (A natural scheduled ritual once the price
  auto-updater lands — same data source, opposite direction.)
- **MCPBs carry CURRENT books.** The CI bundle job runs the SAME
  updater a clone user runs — frozen prefix + continuation
  through build date, `--skip-refresh` off the committed cache —
  and packs the result (design settled 2026-08-27). Release
  assets can carry standalone copies. The bundle exercising the
  updater on every build is its own regression test.
- **Glama consequence, planned not re-litigated:** the sandbox
  Dockerfile gains the same generate-at-build RUN step,
  validated locally first and shipped with a release it was
  riding anyway (the discipline from the evangelism window).
- Post-1.4.4-tag work, all of it: Tuesday's bundle ships the
  committed books one last time; this program lands as one
  September arc (policy layer → repaired+current books →
  untrack → CI generation → docs), targeting the v1.5.0 bundle
  as the first fully generated one.
- Alex first (richest policy surface), then Lin Wei (revolver
  policy), then Sabine (simplest). Verify per-persona.
- Market data: committed cache; `--skip-refresh` offline
  determinism preserved. Cache tail must cover the continued
  range — refresh stays a separate, network-touching step.
- Capture rigs and test plans that referenced committed books
  re-anchor on generated output pinned by (generator version,
  cache version, --through date) — determinism replaces
  byte-identity as the oracle property.

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
