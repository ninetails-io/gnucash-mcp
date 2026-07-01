# Final Adversarial Review — gnucash-mcp v1.3.1 (pre-release)

**Branch reviewed:** `feat/release-prep` @ `5ead2e8` (diffed against `develop`)
**Date:** 2026-06-10
**Disposition:** **ship-with-fixes**

## Method

Five parallel invariant-coverage sweeps (template/voided filters, FX
aggregation, raw-SQL/audit/owner_type contracts, dates/prices/staleness,
business-module money math) plus a direct line-by-line read of `reporting.py`,
`_currency.py`, and the `core.py` dashboard paths, plus live cross-surface
probes against copies of both sample books — including **mutation probes** that
create the legal-but-unexercised account/transaction shapes and watch the
report surfaces diverge.

Every finding below was adversarially verified: either **manifested live** with
real numbers, or confirmed by direct read of the cited lines. Prior-review
items (H1/H2, M1–M5, L1–L4, and the income/spending contra-netting fix in
`a34867c`) were re-verified as fixed in the tree and are **not** re-reported.

Review ran in an isolated git worktree (since removed); the working tree,
tracked sample books, and live server book were never opened for write.

The unifying observation: **every confirmed defect lives in a shape the
synthetic books structurally cannot produce** — parents/placeholders with
direct splits, overpayments, voided-then-touched transactions, native-GnuCash
SX template rows, foreign-denominated A/R. The bookkeeper loop and the sample
corpus can't reach them. A small "pathological shapes" fixture book would
convert this entire review into regression tests.

---

## Compound / systemic findings

### C1 — HIGH: net-worth surfaces disagree on *which splits count*; the equity residual hides it

**Chain:** leaf-only iteration (benign display choice) + the SB-8 placeholder
skip (guards a double-count that this code doesn't actually produce) + the
balancing-residual equity line (benign construction) ⇒ silently wrong totals
that still satisfy "does it balance?".

- `book/core.py:539` — `_compute_net_worth_at` skips every **parent** account's
  own splits (`parent_guids`); `book/core.py:1954` (`is_leaf`) does the same in
  `_collect_summary_balance_sheet`. A parent's *direct* splits are not
  represented by its children — they are dropped.
- `book/reporting.py:489` — `balance_sheet` skips placeholder accounts' splits.
  There is no roll-up in this code, so the SB-8 double-count rationale does not
  hold; the skip deletes real money. Because `unrealized` (`reporting.py:589`)
  is the balancing residual, **A = L + E still holds after an asset is
  dropped** — the error is invisible to the sheet's own consistency check.
- `book/reporting.py:732` — `net_worth` skips neither. Three surfaces, three
  scopes.

**Live (Alex):** creating one *empty* sub-account under Checking dropped the
dashboard "now" net worth from 216,300 → **191,287** (exactly Checking's
25,013.53) while `balance_sheet`/`net_worth` stayed correct. Marking Checking a
placeholder made `balance_sheet` A−L = 191,286.55 vs `net_worth` = 216,300.08.

**Fix shape:** one shared rule — sum *own* splits per account (no parent skip),
and include-or-exclude placeholder direct splits consistently across all three.
Lock with a `TestCrossToolPriceAgreement` case for parent-with-direct-splits and
placeholder-with-splits.

### C2 — HIGH: `pay_invoice` has no overpayment guard; `abs()` inverts the sign of the relationship on three surfaces

`book/business.py:5817ff` never compares `amount` to remaining balance (only the
discount branch does, `:6155`). Overpayment drives the lot negative; lot-close
fires only at exactly zero; `abs()` then launders the sign at `:6328`
(`remaining_balance`), `:7592–7603`/`:7677` (`get_outstanding_invoices`), `:7789`
(`get_job_report`). `amount_paid` is derived as `grand_total − abs(balance)`.

**Live (Alex):** paying $4,500 on $3,500 invoice 000026 returned
`status: "paid", remaining_balance: "1000"`; `get_outstanding_invoices` then
showed `amount_paid: 2500.00, amount_due: 1000, days_past_due: 40`. A customer
who **overpaid by $1,000** is shown as still owing $1,000 — inviting a
double-collection that drives the lot further negative.

**Related:** on the untouched sample, unapplied credit note 000039 renders in the
outstanding list as `amount_due: 500, days_past_due: 238` — money the business
*owes the customer*, displayed in a collections list with an aging clock.

**Fix shape:** reject (or explicitly book as credit) `amount > remaining`; never
`abs()` a possibly-negative lot balance — surface direction; derive
`amount_paid` from actual payment splits; section credit notes out of the
collections list (no `days_past_due`).

### C3 — MEDIUM-HIGH: budget actuals still use the per-split gross filter that `a34867c` retired for the spending/income reports

`book/budgets.py:881–888` (`get_budget_report`) and `book/core.py:1356–1361`
(dashboard budget headline): `if EXPENSE and amount > 0` / `elif INCOME and
amount < 0` drops contra splits **per split** — the exact shape the netting fix
removed from `income_by_source`/`spending_by_category` in the same release.

**Live (Alex):** spend 200 + refund 120 on Groceries → true net 80; the budget
report shows `actual: 200, percent_used: 44.4`. The budget surfaces now
contradict the just-fixed reports on the same data.

**Fix shape:** accumulate signed per rollup-target, decide after aggregation —
the `a34867c` pattern verbatim.

### C4 — MEDIUM-HIGH: default `cash_flow` is blind to A/R settlements, and FX drift inconsistently rescues them

`book/reporting.py:1012–1041`: a transaction counts as cash flow iff it has a
non-voided INCOME/EXPENSE split. An invoice payment is A/R↔bank (income was
recognized at post, which never touches cash), so it is filtered as an internal
transfer — **unless** the rate drifted, in which case `pay_invoice` adds a
realized-FX split to an INCOME account, which rescues that one payment into
inflows.

**Live (Alex):** after a $3,500 payment, `cash_flow` for that day reported
`inflows: 0, transfers_excluded: 1` (vs 3,500 with `include_transfers=true`).
For the freelancer persona, default cash-flow inflows omit revenue receipts, and
whether a given receipt appears depends on whether its rate happened to drift.

**Fix shape:** treat lot-linked A/R/A-P settlements as real flow (or at minimum
exclude the FX gain/loss account from the rescue set so classification is
deterministic), and surface what was excluded.

### C5 — MEDIUM: future-dated transactions included in three "now" surfaces (the H1 M-of-N shape, date edition)

The convention (future TRANSACTIONS excluded from "now") is honored in
`_compute_net_worth_at`, `_collect_summary_balance_sheet`, `get_balance`, and
violated at:

- `book/core.py:1524–1526` — runway's liquid pass sums all splits with no
  `post_date <= today` filter, while **its own cost-basis fallback ten lines
  down** (`:1547–1554`) filters future splits with a comment explaining why.
  Live: a rent payment dated +10 days moved runway 157 → 126 days while
  `net_worth` and `get_balance` correctly did not move.
- `book/core.py:870–872` — critically-low-cash warning, same unfiltered sum (a
  future deposit suppresses a real warning; a future payment fires a premature
  one).
- `book/reporting.py:1234–1239` — `debt_payoff_plan` balances (skews minimums,
  the feasibility gate, and YETI).

### C6 — MEDIUM: the contra-netting fix's "drop net≤0 after aggregation" makes report totals depth-dependent and erases real signals

`book/reporting.py:312, 394`. Netting happens at the *grouped* account, then
net-negative groups are dropped — so the same period's TOTAL changes with the
`depth` parameter (documented as a grouping knob only).

**Live (Alex):** with a 900 refund subcategory under Travel, spending total =
97,448.95 at depth 1–2 but **98,348.95 at depth 3** (the net-negative leaf is
dropped rather than netting against siblings). Same mechanism: an income source
that nets to a loss in a period vanishes entirely, so `income − spending` no
longer ties to net income on `balance_sheet`/dashboard (which accumulate fully
signed).

**Fix shape:** net at the grouping level but carry an explicit
"N net-negative categories excluded (−X)" line, or keep negative rows — either
way make the TOTAL depth-invariant.

### C7 — MEDIUM: historical-anchor reports can value a holding at a *future* rate via the chain pass

`book/_currency.py:302–316` → `:394–426` → `:693–800`. The direct pass
hard-filters `p_date > anchor` for past anchors, but a commodity with no
on-or-before price falls into the issue-#94 chain pass, whose legs run
`respect_staleness_cap=False` and fall back to **after-`as_of`** prices with no
upper bound. A `balance_sheet(2025-06-30)` for a commodity first priced
2025-09-01 uses the September rate; a sibling with a before-price honors the
historical convention. (Cosmetic relative: `_rate_provenance` is computed at the
*unfolded* anchor — `core.py:2434` vs `:2438` — so the `(via …)` note can name a
different path than produced the rate.)

**Fix shape:** for past anchors, suppress the after-`as_of` fallback in the chain
legs (e.g. `allow_after=False` when `anchor < today`).

### C8 — MEDIUM: voided splits are protected by chokepoints that three write paths never consult

The `_is_voided`/`_is_unreconciled` chokepoints exist; these entry points bypass
them (all code-verified, none manifested live):

- `book/reconciliation.py:387–389, 400–416` — `reconcile_account` (bulk and
  targeted) flips voided splits to `'y'`, the exact mutation `set_reconcile_state`
  rejects with a written rationale (`:88–99`). Worst chain: void → bulk-reconcile
  → `unvoid` reports "not voided" → re-`void` overwrites `void-former-*` slots
  with zeros — original amounts unrecoverable in three legitimate calls.
- `book/core.py:3078–3079` — auto-fill takes the most recent description match
  with no voided check; the void-and-re-enter workflow this server recommends
  makes the voided txn the most recent match, and its zero-value splits build a
  **silent $0 transaction** that passes validation.
- `book/core.py:4385–4397, 4568–4601` — `update_transaction`/`replace_splits`
  guard only `'y'`: updating a voided txn writes non-zero values into
  `state='v'` splits — the "partial-void corruption" state `_is_voided`'s
  docstring names, visible to balance sums but invisible to
  cash_flow/lots/reconciliation counts.

**Fix shape:** refuse when `any(_is_voided(s) for s in splits)` at each site;
add a voided exclusion to `_collect_create_signals`.

### C9 — MEDIUM (native-GnuCash books only): SX template transactions surface as real transactions

GnuCash desktop persists SX recipes as real Transaction rows against
`root_template` accounts. Server-built books (both samples) use the splits-json
design instead, so the bookkeeper loop cannot catch this. Gaps, all
code-verified:

- `book/core.py:2751` (`list_transactions`, unfiltered path) and `core.py:3675`
  (`search_transactions`, all four field modes) iterate `book.transactions` with
  **no template filter** — a stale "$2,485 Mortgage Payment" recipe renders
  identically to a real event. The sibling `_collect_create_signals` filter at
  `:3066` shows the known fix shape.
- `core.py:2455–2464` — dashboard `total_txns`/`first_date`/`last_date` count
  templates (also stretches the SB-7 burn clamp).
- `delete_scheduled_transaction` (`scheduling.py:934–937`) deletes the template
  account assuming no splits — untrue for desktop-created SXs.
- Probable (flagged, unverified — sandbox lacked a native book): GnuCash's
  `template` pseudo-commodity leaking into `list_commodities` / stale-price
  warnings (`core.py:1023–1026`).

### C10 — MEDIUM mechanism / narrow exposure: cross-commodity A/R relieved at pay-date rate, leaving permanent phantom A/R

`book/business.py:6046–6048` computes the A/R-relief quantity by converting the
payment at the **pay date**, but post (`:5516–5528`) carried the A/R quantity in
at the **post-date** rate. When the A/R account commodity differs from the
invoice currency (supported here; `apply_credit_note` refuses the same config at
`:6551`), FX drift lands twice — as residual A/R quantity *and* as the explicit
FX split — leaving e.g. a permanent −10 USD A/R balance on a fully-settled 100
EUR invoice. Companion: an early-payment discount on a cross-currency invoice
feeds `_compute_fx_gain_loss` only `payment_amount` (`:6282–6293`), so the
discount leg's drift (discount × rate-delta) is booked nowhere and assets ≠
equity by that amount.

---

## Atomic findings

| # | Severity | Finding |
|---|----------|---------|
| A1 | MEDIUM | `calculate_lot_gain` subtracts cost basis summed from raw `split.value` (transaction currency, `investments.py:615–628`) from proceeds in default currency (`:1039–1045`) — a tax-relevant gain wrong by the full FX factor when purchases were foreign-denominated and a default-currency quote exists. |
| A2 | LOW-MED | `update_taxtable(force=True)` on an in-use table silently rewrites *derived* totals of already-posted documents (reads resolve `tt.entries` live), so `get_outstanding_invoices` computes phantom `amount_paid` = recomputed_total − lot balance. Force-gated; the gate message under-warns (`business.py:3366–3374`). |
| A3 | LOW-MED | `debt_payoff_plan`'s `minimum_payment`/`credit_limit` slots are currency-ambiguous; the M1 balance conversion de-synced them — a foreign-currency debt's account-currency minimum is treated as default-currency (`reporting.py:1244–1255`; feasibility gate `:1339–1344`). |
| A4 | LOW | `fx_realized.direction` in the pay response uses `is_bill` instead of `effective_is_bill` (`business.py:6346–6350`) — cross-currency credit-note refunds label a booked FX *loss* as `"gain"`. Ledger correct; label inverted. One-token fix. |
| A5 | LOW | Early-payment-discount tolerance admits a 1-quantum mismatch the splits then can't balance — validator-blessed input dies with an opaque `GncImbalanceError` (`business.py:6164` vs `:6199–6204`). Nothing wrong persists. |
| A6 | LOW | `delete_credit_note` raw-SQL delete orphans the note's `credit-note` / `gnc-mcp/applies-to-invoice` slot rows (`business.py:6890–6900`; contrast the person-delete cleanup at `:6970–6984`). Hygiene, not corruption. |
| A7 | LOW | `depth=1` (the tool default) collapses the whole report to a single `Expenses 100.0%` row on the canonical chart layout (live-verified); docstring promises "top-level buckets (`Expenses:Food`)". Doc/behavior off-by-one. |
| A8 | LOW | `get_lot` lists voided splits as unmarked 0-rows its own summary excludes (`investments.py:843–850`); dashboard reconciliation counts voided splits as "activity" (`core.py:380–381`). Cosmetic. |
| A9 | LOW | `list_transactions`/`search_transactions`/`_collect_create_signals` sort/compare `post_date` with no None guard; null `post_date` rows are a documented old-book artifact (`_query.py:52–53`) and would raise `TypeError`. |
| A10 | DOCS | Stale prose contradicting now-correct code: `core.py:495–505` (claims liabilities unconverted, A/R-A/P excluded), `core.py:1479–1484` (claims burn sums raw), `core.py:1120–1122`; doubled `@staticmethod` at `core.py:1708–1709`; dead `_find_recent_description_matches` (`core.py:2902`) lacking both filters if revived. |

---

## Verified clean (probed and cleared)

- **All 11 prior-review fixes confirmed in the tree** — H1 across all five type
  branches, H2 at both prune sites via `_resolve_backup_path`, M3 anchor
  folding, M5's rename chokepoint (`core.py:3980`).
- **Cross-surface agreement on both sample books** — A = L + E exact;
  `balance_sheet` ≡ `net_worth` ≡ dashboard at today and three year-ends;
  income − spending ≡ retained earnings; A/R rows ≡ outstanding invoices — to
  the penny, USD and CNY.
- **Date off-by-one class** — fully funneled through `_query_filtered_splits`'
  `< end+1day` chokepoint; no surviving SQL `post_date <=`; all Python
  comparisons date-vs-date.
- **Market-price filter (`type='transaction'`)** — zero gaps; every valuation
  read routes through `_is_market_price`/`_find_prices`; unfiltered sites
  (`get_prices`, `delete_price`, duplicate detection) are deliberately
  non-valuation.
- **FX staleness cap** — coherently posting-only; valuation uniformly cap-free;
  the 7/90-day bands fired correctly in live testing.
- **Raw-SQL verification contract** — 13/13 scanner-visible DML sites verified
  with precise conditions; exactly two `text()` DML sites, both documented (L3).
- **Audit dispatch** — 62/62 write decorations have formatters; the 10-pair
  polymorphic exemption set is real and remapped in the decorator; no write tool
  lacks `@audit_log`.
- **`owner_type`** — all 12 tool-layer and all book-layer entry points route
  through `_gate_owner_type`/`_parse_owner_type`.
- **Taxtable posting math** — all four taxable × tax-included quadrants tie
  exactly (residual-to-largest per line; splits sum to zero incl. credit notes
  and negative lines); unpost symmetric; `apply_credit_note` guard set tight.
- **Budget FX** (SB-6), **vendor_spending quarantine** (M2), **net-worth
  time-series per-boundary rates** (SB-1), **reconciliation single-commodity
  math**, **scheduling window math**, **budget period boundaries** — all traced
  clean.

---

## Release recommendation: ship-with-fixes

No CRITICAL; nothing corrupts the live book, and the sample corpus agrees
everywhere. But three findings put wrong money on first-class surfaces with
triggers a normal user hits without trying:

- **C1** — create a sub-account → dashboard net worth silently drops by the
  parent's balance.
- **C2** — overpay an invoice → phantom receivable, double-collection invitation.
- **C3** — budget report contradicts the netting fix shipped in this same
  release.

Recommend holding v1.3.1 for **C1, C2, C3** plus the one-token **A4**. C4–C8 can
ride the same branch or open v1.3.2. **C9** bites only native-GnuCash books,
which neither sample exercises — add a desktop-created fixture before it is
marked fixed.

Suggested follow-up independent of the fixes: a "pathological shapes" fixture
book (parents/placeholders with direct splits, an overpaid invoice, a
voided-then-touched transaction, a desktop SX template, foreign-denominated A/R)
that turns this review into regression tests.
