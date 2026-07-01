# Bookkeeper Test Plan — Adversarial Pass-2 Closure

**Branch:** `fix/adversarial-pass-2`
**Scope:** all 20 findings from the final adversarial review
(`specs/Code Reviews/CODE_REVIEW_v1_3_1_ADVERSARIAL_PASS_2.md`) are
now fixed — C1–C10 and A1–A10. Your last signoff
(`RELEASE_PREP_BOOKKEEPER_VALIDATION.md`) predates all of it.
**Goal of this pass:** confirm the three surfaces whose numbers
deliberately changed read right, that the cross-tool numbers still
tie on both books, and that the new refusal behaviors say what they
mean. The PR opens after your signoff, not before.

**Unit-side coverage you don't need to re-prove:** a new
"pathological shapes" fixture book (parents/placeholders with direct
splits, an overpaid lot, a voided-then-touched transaction, a
desktop-created SX template, foreign-denominated A/R, a future-dated
entry) runs every report surface against the combination — 18 tests,
plus per-fix regressions that were each verified to fail on the
pre-fix code. 1,580 tests passing. Your job is the live-workflow
layer the unit suite can't see.

---

## Context: What Changed Since Your Last Signoff

Three buckets. **Bucket 1 changes numbers you will see on Alex and
Lin Wei.** Bucket 2 changes behavior only on shapes the sample books
don't contain (desktop templates, corrupted voids, foreign A/R
mismatches). Bucket 3 is new refusals — write paths that now say no.

### Bucket 1 — visible number changes (deliberate; don't flag as bugs)

1. **`spending_by_category` / `income_by_source` at default
   `depth=1` now return real top-level buckets.** Pre-fix, an
   off-by-one collapsed the whole default report into a single
   `Expenses 100.0%` row (the docstring always promised
   `Expenses:Food`-level buckets). Depth 2 now means one level
   below those buckets.
2. **Report TOTALs are depth-invariant and net-signed.** A category
   that nets negative for the period (refund-heavy) drops out of
   the line items but its net stays in the TOTAL, surfaced as an
   explicit `(N net-refunded netted into TOTAL: …)` line (compact)
   or `net_negative_netted` rows (verbose). Pre-fix the TOTAL
   changed when you changed `depth` — same period, different
   totals. Now `income − spending` ties to net income again.
3. **Default `cash_flow` counts invoice/bill settlements as real
   flow.** A payment is A/R ↔ bank — structurally a transfer, but
   it's your revenue receipt; pre-fix it vanished from default
   inflows unless FX drift happened to rescue it. Settlements are
   keyed on the lot link, so the classification is deterministic.

### Bucket 2 — fixed but invisible on the sample books

- Desktop-GnuCash SX template rows and the `template`
  pseudo-commodity are filtered from `list_transactions`,
  `search_transactions`, the dashboard stats/warnings, and
  `list_commodities`.
- "Now" surfaces (runway, low-cash warning, `debt_payoff_plan`)
  exclude future-dated transactions, matching `net_worth` /
  `get_balance`.
- Historical reports never value a holding via a rate first quoted
  AFTER the report date (the intermediate-currency chain pass now
  honors the same convention as direct quotes).
- Cross-commodity A/R (EUR invoice on a USD-denominated receivable
  account) settles clean — no phantom A/R residue; the FX drift
  books once, in the FX split. The early-payment discount leg's FX
  drift books too.
- `calculate_lot_gain` converts a foreign-denominated purchase's
  cost basis before comparing to default-currency proceeds.
- Debt-plan `minimum_payment` / `credit_limit` slots convert from
  the account's currency (a ¥2,000 minimum is no longer treated as
  $2,000).
- Null-`post_date` rows (old-book artifact) render as `(no date)`
  instead of crashing listings.

### Bucket 3 — new refusals (the error text is part of the product)

- `update_transaction` / `replace_splits` on a **voided**
  transaction → refuse, point at `unvoid_transaction`. No force
  override.
- `reconcile_account` with an explicitly-targeted voided split →
  refuse; the bulk sweep silently skips voided splits.
- Auto-fill (create with no splits) whose only description match is
  voided → "no match" error instead of cloning a silent $0
  transaction.
- `pay_invoice` overpayment → refuse with credit-note guidance
  (you saw this one land; re-touch it once on this build).
- `apply_discount` with a payment one cent off the exact discount →
  now **succeeds** and books the discount at the actual shortfall
  (pre-fix the validator blessed it and the write then died with an
  opaque imbalance error).

---

## Pre-Flight

Run `get_server_config` and confirm Stephen has bounced the server
onto this branch (if any Bucket 3 probe sails through without its
refusal, stop — you're on stale code).

---

## Track A — Cross-Tool Consistency (highest value)

Run on BOTH books. Expected values below were captured from the
committed sample books on this branch today (2026-06-10); "now"
anchors drift with the calendar, the 2025 figures don't.

### A1. The identity, both books

`balance_sheet()` with no arguments:

|        | Assets | Liabilities | Equity | A − L − E |
|--------|--------|-------------|--------|-----------|
| Alex   | 631,839.63 | 415,539.55 | 216,300.08 | **0.00 exactly** |
| Lin Wei | 4,978,440.00 | 2,859,482.04 | 2,118,957.96 | **0.00 exactly** |

Then `net_worth(end_date=today)` and the dashboard "now:" line —
all three agree (216,300 / 2,118,958). These are UNCHANGED from
your last pass: the pass-2 fixes target shapes these books mostly
lack, so headline stability is itself the regression check.

### A2. income − spending ties (new invariant, Bucket 1 item 2)

For calendar 2025, verbose mode (`compact=false`):

|        | income total | spending total |
|--------|--------------|----------------|
| Alex   | 265,031.76 | 239,549.02 |
| Lin Wei | 634,520.00 | 321,501.02 |

Re-run both at `depth=2` and `depth=4`: **the TOTALs must not
move.** Only the grouping granularity changes. If any
`net_negative_netted` rows appear, they should name real
refund-shaped categories, not noise.

### A3. New depth-1 breakdown reads as YOUR categories

`spending_by_category` 2025, default depth, compact. Expected top
of table:

- Alex: `Business 69,241.36 (28.9%)`, `Interest 55,643.15`,
  `Taxes 38,347.82`, … 19 rows, TOTAL 239,549.02.
- Lin Wei: `Interest 113,271.00 (35.2%)`, `Taxes 57,200.00`, …
  17 rows, TOTAL 321,501.02.

Judgment call for you: does this granularity match what you'd want
from the default call? (Pre-fix you'd have gotten one `Expenses
100.0%` row.)

### A4. cash_flow now shows revenue receipts (Bucket 1 item 3)

`cash_flow` for 2025, default:

|        | inflows | outflows | transfers_excluded |
|--------|---------|----------|--------------------|
| Alex   | 241,304.30 | 191,890.66 | 78 |
| Lin Wei | 597,120 | 367,070 | 22 |

Sanity: Alex's 2025 invoice payments are IN the inflows now. With
`include_transfers=true`, Alex reads 296,944.30 / 248,947.44 —
the delta vs default is pure pocket-shuffling (savings moves, CC
payments), which is the point of the filter.

### A5. Outstanding invoices, mixed shapes

`get_outstanding_invoices` on Alex: six rows — five aging
receivables (EUR 4,200 Berlin at 99 days past leads) plus
`000039 … USD 500 … credit available` with **no aging clock**.
Credit notes never tick days-past-due.

---

## Track B — Refusal Probes (run on a THROWAWAY COPY of a book)

These mutate. Ask Stephen to point the server at a copy first.

1. **Void → touch chain.** Create a small expense, void it. Then:
   - `update_transaction` with new splits → expect refusal naming
     `unvoid_transaction`.
   - `replace_splits` → same refusal.
   - `reconcile_account` targeting the voided split's GUID →
     refusal; a bulk `reconcile_all` on that account → succeeds and
     leaves the voided split at `v`.
   - `unvoid_transaction` → original amounts restored intact (the
     point of the guards is that nothing got to corrupt them).
2. **Void → re-enter.** After voiding, call `create_transaction`
   with the same description and NO splits (auto-fill). Expect "no
   match found for auto-fill", not a silent $0 transaction. Then
   re-enter it properly with explicit splits — duplicate detection
   should NOT flag the voided one as a duplicate.
3. **Overpay re-touch.** Post a small invoice, pay it in full, try
   to pay $0.01 more → refusal with credit-note guidance.
4. **Discount, one cent off.** Invoice $1,000 on 2/10 Net 30 terms,
   pay $980.01 with `apply_discount=true` inside the window →
   succeeds, `remaining_balance: 0`, discount account shows
   $19.99. (Pre-fix: opaque imbalance error.)
5. **Future-dated entry.** Date a $1,000 expense 10 days out.
   `get_balance`, `balance_sheet`, dashboard net worth, runway, and
   `debt_payoff_plan` must NOT move; `list_transactions` still
   shows it. Delete it after.

## Track C — Regression Spot-Checks

Quick re-touches of flows you've validated before, on this build:

- `get_budget_report` on your usual budget — contra netting intact
  (a refund still nets the actual, percent unchanged from your
  PR-92-era validation).
- One `reconcile_account` round with `except_guids` on a copy.
- `vendor_spending_report` and `debt_payoff_plan` render with sane
  currency prefixes on Lin Wei (CNY everywhere, no `$`).
- `get_book_summary` end-to-end read: anything that smells off in
  the warnings/reconciliation sections, flag it. (Known artifact:
  Lin Wei's runway reads 10,213 days because her book's expense
  data ends in May and the 180-day burn window is nearly empty —
  pre-existing display quirk, not a pass-2 regression. Flag it if
  it bothers you and we'll file it separately.)

---

## Signoff

Same convention as always: findings back through Stephen; fixes land
on this branch; the PR to develop opens only after your signoff.
