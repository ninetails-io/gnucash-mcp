# Bookkeeper report — budget sign convention (fix/budget-sign-convention)

Run: 2026-09-09, books `alex-chen-morales.gnucash` (steps 1–2, raw rows) and `books.gnucash` (production; desktop verification), branch `fix/budget-sign-convention` @ `03d9c9b`, v1.4.4, 87 tools. Bookkeeper: Abe VII (Cowork).

## Verdict: PASS after fix `0dad458` — merge

First run (`03d9c9b`) was BLOCKED: sign convention correct, stamp written under a key GnuCash does not recognize. Re-run on `0dad458` below (§3–4 re-run).

## 1. Expense-only untouched — PASS
- `get_budget_report("2025 Annual Budget", period=0)`: TOTAL 2,911 budgeted / 2,691.03 actual / 92.4% used; every row as on develop.
- Dashboard Budget headline absent for Alex (his only budget is 2025; today is 2026) — same as before the branch.

## 2. Income round trip — PASS
- `create_budget("BK Signs", monthly, 12, start 2026-01-01)`; `set_budget_amount` Income:Salary 5000, Expenses:Groceries 300, all periods.
- `get_budget` compact: `Expenses:Groceries 300/mo (all periods)`, `Income:Salary 5000/mo (all periods)`. Verbose: every period `"5000"` / `"300"`, positive.
- `get_budget_report` period 8 (Sep): Salary 5,000 / 0 / 0.0% — Alex's book ends 2026-07-18, so September actuals are zero by fixture, not by bug. Period 6 (Jul): Salary 5,000 budget, 3,597.23 actual, 71.9%; Groceries 300 / 204.34 / 68.1%. Positive, sane.
- Raw rows (sqlite): Salary p0/p8 `-500000/100`, Groceries `30000/100`. Negative on disk, magnitudes on the surface — branch fingerprint confirmed.
- Note (pre-existing, sharper now): report TOTAL sums income and expense budget lines together (5,300). Once income rows exist, TOTAL as "spending" misleads. Consider separate subtotals or a net line.

## 3–4. GnuCash desktop — FAIL (blocker)
Run on the production book after writing four income lines to "Household Budget" (Salary 10000, 401k Match 400, Reimbursements 100, VA Benefits 180 — all periods; server read them back positive).

GnuCash 5.12 refused to open the book:
> This Dataset contains features not supported by this version of GnuCash. You must use a newer version of GnuCash in order to support the following features:
> * Store budget amounts unreversed (i.e. natural) signs (requires at least Gnucash 3.8)

Root cause: the branch writes the stamp as slot `features/Budgets: sign reversal fixed` with GnuCash's description as the value. GnuCash keys features by the KEY, and the key in `libgnucash/engine/gnc-features.h` is:

    #define GNC_FEATURE_BUDGET_UNREVERSED "Use natural signs in budget amounts"

`gnc-features.cpp` treats any key not in its table as unknown (`features_table.find(feature) == end()`) and prints the stored description — hence a message that reads like a version complaint. It is not version-dependent; 5.12 is 3.8+.

Also found: the production book ALREADY carried `features/Use natural signs in budget amounts` (written by desktop earlier). The branch did not recognize it and added its own key beside it — so the legacy-detect/scrub path is keyed on the wrong string as well.

Live repair (GnuCash closed, `create_backup` label `pre-feature-key-rename` taken first): production — bogus row deleted (real key present); Alex — bogus row renamed to the real key. `PRAGMA integrity_check` ok on both. GnuCash 5.12 then opened production cleanly; Household Budget shows Salary 10,000 / 401k Match 400 / Reimbursements 100 / VA Benefits 180 positive. So the sign convention itself is right; only the stamp key was wrong.

Step 4 (desktop-written income/liability lines read back by the server) not yet run — do it on the corrected branch.

## 3–4 re-run on `0dad458` (2026-09-09/10) — PASS
- Stripped the (hand-repaired) stamp from Alex, then `set_budget_amount` on BK Signs: response now carries `book_stamped: "Use natural signs in budget amounts"`; audit UPDATE BUDGET entry ends with `book stamped: "Use natural signs in budget amounts" (GnuCash natural-sign budget storage)`; slot on disk is the real key with GnuCash's description.
- Step 3, GnuCash 5.12 opened Alex with no dialog; "BK Signs" showed Income:Salary 5,000 and Expenses:Groceries 300 (Steve's eyes).
- Step 4, Steve keyed Income:Investment Income:Interest 120 and Liabilities:Credit Card:Chase Sapphire 400 (all 12 periods) in the GUI and quit. `get_budget` read back `120/mo` and `400/mo`, positive. Raw rows: `-120/1`, `-400/1` (desktop writes denom 1; server writes /100; reader indifferent). Desktop added its usual three feature stamps beside the server's; no complaint.
- Cleanup: `delete_budget("BK Signs")` done. Alex back to one budget. Stamp stays (intended).
- Single-purpose stamp-removal tool: vetoed by Steve; the audit line + response field are the visibility instead.

## 5. Legacy scrub — SKIPPED
Needs a second server on develop plus a bounce cycle; over the ten-minute line. Unit-locked per plan. Re-run after the key fix, because the scrub path must now also recognize a pre-existing real stamp.

## Required before merge
1. Write the stamp under `"Use natural signs in budget amounts"`, copied verbatim from `gnc-features.h`; keep the description as GnuCash's.
2. Read side: treat the real key as the stamp. Migration: if the bogus key is present, rename it (or delete it when the real key already exists).
3. Test that pins the key string against a verbatim copy of the GnuCash define. The description-only match is how this slipped past 100% green.
4. Audit-log every book-level slot write (there was no entry for the stamp), and provide a doctor-class tool to list/remove server-written feature stamps. Recovery this time was raw SQLite from a native process — no user has that.
5. Acceptance gate for any branch touching book slots or storage conventions: the book opens cleanly in GnuCash desktop. It was the only check that caught this.

## Standing questions
- Routed around: the desktop lock (closed GnuCash before the repair); SQLite writes through the Cowork device mount fail with "disk I/O error" — used Desktop Commander (native) instead. Nothing routed around in the server's own surface.
- Population: no book kept by this household has ever carried an income or liability budget line before today. Production "Household Budget" was 41 expense rows; Alex/Lin Wei budget work by earlier Abes was expense-only. The four income lines written 2026-09-09 are the first, and they are stored under the corrected convention.

## Cleanup
Alex: `delete_budget("BK Signs")` still owed (left in place for step 4 re-run on the corrected branch). Production: income lines are intentional and stay. Backup: `books.gnucash.mcp/backups/books-20260909T164508683454-manual-pre-feature-key-rename.gnucash`.

Signed: Abe VII, bookkeeper. Sign convention correct, stamp under the real key, desktop opens a freshly stamped book and both parties read each other's rows. Merge.
