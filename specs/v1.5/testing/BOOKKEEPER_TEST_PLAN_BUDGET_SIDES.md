# Bookkeeper live loop — budget report sides (fix/budget-report-sides)

Three checks, one bounce, ~10 minutes, no GnuCash desktop needed
(nothing on this branch touches storage). Branch under test:
`fix/budget-report-sides`. What it claims: `get_budget_report` keeps
income and expense targets on their own sides. Your round-1 note on
the sign branch — TOTAL summing a 5,000 income target with 300 of
expenses — is the finding. A budget with both sides now closes with
INCOME / EXPENSES / NET lines; the ⚠ marker fires on expense rows
and the EXPENSES line only (beating an income target is not a
warning). A single-side budget renders exactly as before. The
dashboard's Budget headline paces expense targets only.

Work on the production book's "Household Budget", which since
2026-09-09 carries four income lines beside its 41 expense rows —
the first real mixed budget in this household. Read-only except
step 3's scratch.

1. **Mixed budget closes on three lines.** `get_budget_report
   "Household Budget"` for the current period, compact. Expected:
   the last three lines are INCOME, EXPENSES, NET; no TOTAL line.
   INCOME's budgeted = 10,680 (10,000 + 400 + 100 + 180); EXPENSES'
   budgeted = what the old TOTAL showed minus 10,680; NET budgeted =
   income minus expenses. No ⚠ on any income row or the INCOME
   line however far actual income exceeds target; ⚠ on EXPENSES
   only if over 110%. Verbose: each account row carries `side`,
   `subtotals.income` / `subtotals.expenses` present, `totals.basis`
   = `net (income - expenses)`.
2. **Single side unchanged.** `get_budget_report "2025 Annual
   Budget"` on Alex, period 0. Expected byte-identical to develop:
   TOTAL 2,911 / 2,691.03 / 219.97 / 92.4%, no subtotals in verbose.
3. **Headline is spending pace.** On a scratch copy of Alex (or
   Alex itself, then delete), create a one-period budget covering
   this month with `Income:Salary` 5000 and `Expenses:Groceries`
   500, post one 250 grocery transaction dated today, and read the
   dashboard Budget line. Expected `50% used`, not 250 against
   5,500. Then set ONLY an income target on a second scratch budget
   covering today and confirm no Budget line renders for it.
   Delete both.

**Report:** pass/fail per step with the actual closing lines from
step 1 pasted; the standing routed-around question; and one
editorial call — does NET's `%Used` column (net actual ÷ net
budgeted) earn its place, or should the NET line carry only
budgeted / actual / remaining?

---

## Report — 2026-09-10, Abe VII

Filed at `BOOKKEEPER_REPORT_BUDGET_SIDES.md`. PASS 3/3 on 7f5244e.
Production Household Budget closed on INCOME 10,680 / EXPENSES 8,558
/ NET 2,122 with no TOTAL line, marker on one expense row only;
Alex's single-side report identical to develop except `side` on
every verbose row (changelog corrected, not the claim); dashboard
headline read 50% used against 500, not 5,500, and an income-only
budget rendered no Budget line. Editorial ruling adopted: NET
carries no %Used — net actual ÷ net budgeted is a difference
dressed as a ratio. Cell renders `—`; verbose `totals` on a mixed
budget omits `percent_used`. Merge word given pending that change.
