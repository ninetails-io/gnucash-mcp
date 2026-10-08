# Bookkeeper report — budget report sides (fix/budget-report-sides)

Run: 2026-09-10, books `books.gnucash` (production, read-only) and `alex-chen-morales.gnucash` (step 2 read-only; step 3 scratch), branch `fix/budget-report-sides` @ `7f5244e`, v1.4.4, 87 tools, bounced. Bookkeeper: Abe VII (Cowork).

## Verdict: PASS (3/3) — merge, with one editorial change to the NET line

## 1. Mixed budget closes on three lines — PASS
Closing lines of `get_budget_report("Household Budget")`, period 0 (2026-09-01 to 09-30), compact, verbatim:

    INCOME                                     10,680          0     10,680    0.0%
    EXPENSES                                    8,558   2,036.38   6,521.62   23.8%
    NET                                         2,122  -2,036.38   4,158.38  -96.0%

- No TOTAL line. INCOME budgeted 10,680 = 10,000 + 400 + 100 + 180. EXPENSES budgeted 8,558 = old TOTAL 18,238 − 10,680. NET budgeted 2,122 = 10,680 − 8,558.
- No ⚠ on any income row or on INCOME. ⚠ appears on one expense row only (Rent:Apartment 160.3% — real: September's $1,595 posted, Bryan's $600 contra not yet in; the budget row is the $995 net). EXPENSES at 23.8% carries no ⚠. Correct.
- Verbose: every account row carries `side` (`income` / `expenses`); `subtotals.income` and `subtotals.expenses` present with budgeted/actual/remaining/percent_used; `totals.basis` = `net (income - expenses)`.
- Filtering to `account=Income` collapses to single-side output (totals only, no subtotals, no basis) — consistent with step 2.

## 2. Single side unchanged — PASS
Alex "2025 Annual Budget" period 0, compact: TOTAL 2,911 / 2,691.03 / 219.97 / 92.4%, every row as on develop. Verbose: no `subtotals`, no `basis`. One delta from "byte-identical": verbose rows now carry `"side": "expenses"` even on a single-side budget. Harmless and arguably right (a consumer can rely on the key), but it is not byte-identical; note it in the changelog rather than the claim.

## 3. Headline is spending pace — PASS
- Scratch "BK Sides" (1 period, 2026-09): Income:Salary 5000, Expenses:Groceries 500; one 250.00 grocery transaction posted. Dashboard: `Budget (BK Sides): 50% used / 30% elapsed (+20% over pace) ⚠` — 250 against 500, not against 5,500.
- Scratch "BK Income Only" (1 period, 2026-09): Income:Salary 5000 only. With BK Sides deleted, the dashboard renders NO Budget line (Budgets: 2 in the counts, headline absent). Correct.
- Cleanup: both scratch budgets deleted; probe transaction `2b34165e` deleted. Alex back to one budget, 1,940 transactions.

## Editorial: NET's %Used
Drop it. `-96.0%` is net actual ÷ net budgeted (−2,036.38 ÷ 2,122) and it has no reading a household can use: it goes negative whenever expenses have posted before income, swings past ±100% for ordinary months, and its sign flips meaning depending on whether the budgeted net is positive or negative. INCOME and EXPENSES each have an honest pace number; NET is a difference, not a ratio. Carry budgeted / actual / remaining on the NET line and leave the %Used cell blank (or `—`). If a pace signal is wanted on NET, the useful one is "remaining ÷ days left" and that belongs on the dashboard, not here.

## Standing question — routed around
Nothing. One observation, not a finding: the server dated my "today" transaction 2026-09-10 while the book's clock (dashboard MTD "Aug 1-9", "Last entry … 1 days ahead") was still 2026-09-09 PDT — a bookkeeper's date is the machine's local date; I used the calendar's. No effect on the loop (same budget period).

Signed: Abe VII, bookkeeper. Sides separated, warnings on the right side only, headline paces spending. Merge after the NET %Used cell goes.
