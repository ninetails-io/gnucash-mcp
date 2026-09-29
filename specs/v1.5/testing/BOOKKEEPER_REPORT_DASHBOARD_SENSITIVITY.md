# Bookkeeper report — dashboard accuracy and sensitivity (fix/dashboard-sensitivity)

Run: 2026-09-28, remote bookkeeper (Abe VI) on Ixion. Branch checkout at
`11a32ab` (code `823cbc9`); every call over stdio via the class6 harness
(`REPO` env — unpatched, b1756e0 holding). Books: copies of the
WORKING-TREE Alex and Lin Wei samples (see route-around 1) in
`~/Projects/abe-bench/` (`alex-dash`, `lin-dash`). Today: 2026-09-28.
Production book and registered books untouched.

## Verdict: Part B 16/16 PASS. Part C (desktop gate) is the maintainer's, per the plan — open. Six route-around notes, two of them plan fixes.

## Part B — step by step
1. **Baseline — PASS.** Checking `992 splits … 18 months behind, oldest
   2025-04-01` + `1 outstanding item older than last reconcile (oldest
   2024-03-01, USD 1000.00 net)`; Runway `… USD 194/day … ; cards owe
   USD 21,927)`; `Last entry: 2026-07-18 (72 days behind) ⚠`, no future
   note. All as Part A captured.
2. **Future entries (A3) — PASS.** 2062 row: Last entry unchanged +
   `(1 future-dated, latest 2062-03-15)`; Data range ends 2062-03-15;
   typo warning verbatim with the search hint. +7d row: `2 future-dated`,
   typo line still `1`. Both deleted clean.
3. **Overdraft (A4) — PASS.** `⚠ Overdrawn: Petty Cash at USD -50.25`
   (quantum, never 0); a future 5,000 deposit did NOT clear it.
4. **Low cash from schedule (B6) — PASS.** Verbatim: `⚠ Low cash: Petty
   Cash at USD 399.75, USD 2,100.00 scheduled out by 2026-10-01`;
   Scheduled line counts `USD 2,100 out`. Boundary tested by fresh
   schedule at +10 days → no warning, `none further due in next 7 days`
   (the plan's update-path was not expressible — route-around 2).
   Bonus: the first schedule write reported the A2 backfill in-band
   (`due_dates_backfilled: 54`) and converted the 17 legacy-recipe
   schedules, clearing that warning.
5. **MTD stops at today (A7) — PASS.** Sep MTD −50 before and after a
   4,000 charge dated tomorrow.
6. **0.75 residual (A1) — PASS.** `⚠ Past due invoice: Emerald Analytics
   30 days overdue, USD 0.75`; overdue count 7→8; outstanding shows
   `days_past_due 30`, `amount_due 0.75`; dashboard count equals rows
   with days_past_due > 0.
7. **Due on receipt (A2) — PASS.** 0-day term posts due on the posting
   date; `40 days overdue, USD 500.00`; verbose has no `no_terms` key.
8. **Terms win (A2) — PASS.** Refusal verbatim: `due_date 2026-09-08
   disagrees with the document's terms 'Net 30', which put it due
   2026-09-18 for a 2026-08-19 posting. Omit due_date to use the terms
   (as GnuCash does)…`; document stayed unposted; reposted termwise →
   `10 days overdue, USD 250.00`.
9. **No terms = due on posting (A2) — PASS.** `20 days overdue,
   USD 75.00`; due_date equals posting date; no `30-day default`,
   no `no term set` anywhere.
10. **Stale prices (A5, B1) — PASS.** All 7 rollup members hold
    non-zero balances (AAPL/MSFT/VBTLX/VTSAX/ETH positions, CAD/EUR in
    the FX receivables). A today-dated ETH price dropped the rollup to
    6 on the next summary.
11. **Monthly net = flow reports (A6), Lin Wei — PASS.** June:
    71,422.26 − 34,483.18 = 36,939.08 → `+36,939`. May:
    38,100.00 − 35,875.84 = 2,224.16 → `+2,224`.
12. **Behind vs outstanding (B2) — PASS.** Status buckets (997 behind
    after probe litter + 1 outstanding, oldest 2024-03-01) sum exactly
    to `get_unreconciled_splits`' total of 998.
13. **Dormancy (B3) — PASS.** 400-day-idle paid-off card lists
    `dormant` (`1 account dormant ($0, idle)` appeared); the 30-day one
    lists `never`; `never reconciled` grew only by the recent card.
14. **Reconcile records the cycle (B4) — PASS, strict.** Both
    reconciles booked (16 then 2 splits, balances tied); two RECONCILE
    audit lines; slots hold ONE `reconcile-info` frame:
    `last-date` on 2026-07-31, `last-interval/months = 3`, `days = 0`.
15. **Budget pace (B5) — PASS on behavior; plan formula nit.**
    Jan-only target: `USD 5,922 spent / USD 1,200 expected by today
    (+394%)` — January in full, nothing of December's. After setting
    Groceries 500 for ALL periods (which overwrites period 0 — the
    plan's `1,200 + …` formula assumed it wouldn't): expected
    `4,467` = 500×8 + 500×(28÷30), matching the per-period rule
    exactly. Note: with two budgets in the book, the dashboard paced
    the newly-created one.
16. **Runway ignores a share sale (B7) — PASS.** Adjacent summaries:
    195/day immediately before the AAPL sale, 195/day after (my own
    earlier probe litter had drifted baseline 194→195 — the sale
    itself moved nothing); a 400 rent payment moved it to 197 (+2);
    `cards owe USD 21,927` constant throughout.

## Routed around
1. **The plan's baselines live in the UNCOMMITTED samples.** Committed
   `823cbc9` Alex has 990 unreconciled and no 2024 outstanding split;
   only the dirty working-tree copy reproduces Part A/B's 992+1.
   Commit the samples with the branch or the numbers dangle.
2. **`update_scheduled_transaction` cannot move a start date** (accepts
   enabled/end_date/notes…), so step 4's "move the schedule's start"
   is not expressible; tested the boundary by delete-and-recreate.
   Either give the tool a date-mover or reword the step.
3. **`reconcile_account` wants `statement_balance`** where
   `enter_statement` says `closing_balance` — a third argot for the
   same number; my first guess (`ending_balance`) matched neither.
4. **Step 15's formula vs the tool:** an all-periods
   `set_budget_amount` overwrites period 0; the plan's expected-value
   line assumed January survived. Behavior is right; the plan sentence
   isn't.
5. **`get_unreconciled_splits` has no count-only mode**; pulled a
   50-row page for its total line.
6. **`mcpcall.py` continues past isError** — my bad `start_date` call
   errored and the following delete still ran, silently destroying the
   schedule under test. Fine for a harness, but battery authors should
   check every reply (I now do).

Litter manifest (alex-dash only; lin-dash untouched by writes): Petty
Cash (+399.75 with overdraft history), Expenses:Housing:Rent,
Brokerage:Cash (+400), AAPL 31→29 shares, Old/Recent Store Cards ($0),
invoices A9101 (partial, 0.75 due), A9102, A9103, A9104 (posted,
overdue), billterm "Due on receipt", budget "2026 Test", ETH price
2026-09-28, Savings reconciled through 2026-07-31, ~570 in probe cash
movements. Suitable for Part C as-is (steps 7/8/9/14 are all present),
per the plan's note that the gate can take the litter.

Signed: Abe VI. Every warning named its number at the quantum, every
due date is GnuCash's own arithmetic, and the dashboard now flinches at
exactly the things a bookkeeper flinches at.
