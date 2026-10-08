# Bookkeeper live loop — budget signs (fix/budget-sign-convention)

Five checks, one bounce, ~15 minutes, and this one needs GnuCash
desktop open beside the server for two of them. Branch under test:
`fix/budget-sign-convention`. What it claims: budget amounts are now
stored the way GnuCash 3.8+ stores them — income, liability,
payable, equity, and credit-card targets negative on disk, book
stamped with GnuCash's `Use natural signs in budget amounts` feature — while the
tool surface stays magnitudes for every account type. Before, every
type was stored positive; an income target read as `-5,000 / 0%`
once GnuCash scrubbed the book, and showed as -5,000 in GnuCash when
written here. Expense-only budgets (Alex, Lin Wei, your real book)
are byte-identical before and after.

Work on Alex. Alex has one expense-only budget, "2025 Annual
Budget". Everything you write goes on a scratch budget you create
and delete. Fingerprint that you're on the branch: after step 2,
`get_budget` on the scratch budget shows the income target as a
positive number while the raw row (step 3) is negative.

1. **Expense-only is untouched.** `get_budget_report` on "2025
   Annual Budget", period 0, and the dashboard's Budget line.
   Expected identical to what develop shows: TOTAL 2,911 budgeted,
   92.4% used. This is the no-regression check for every existing
   user.
2. **Income target round trip.** `create_budget` "BK Signs"
   (monthly, 12 periods, start 2026-01-01). `set_budget_amount` on
   `Income:Salary` 5000 (all periods) and on `Expenses:Groceries`
   300. Then `get_budget` (verbose and compact) and
   `get_budget_report` for period 8 (September). Expected: Salary
   budgeted 5000, Groceries 300, both positive, Salary's used-% a
   sane positive number against his September salary actuals.
   Pre-fix a native-sign income row here read -5,000 at 0%.
3. **GnuCash sees what we wrote.** Open Alex in GnuCash desktop
   (do NOT click OK on Since-Last-Run if it appears — cancel it;
   that's the separate scheduling ruling). Actions → Budget → open
   "BK Signs". Expected: Income:Salary shows 5,000 and
   Expenses:Groceries 300, both positive, with the default Reverse
   Balanced Accounts preference. No "This book has budgets" notice
   at open — the stamp is already there. If GnuCash shows -5,000
   on Salary, the sign is wrong and the branch fails.
4. **We read what GnuCash wrote.** Still in GnuCash, in "BK Signs"
   set `Income:Interest` (or any income leaf) to 120 for one
   period, and `Liabilities:Credit Card:Chase Sapphire` to 400 for
   one period. Close GnuCash (SQLite writes immediately; closing
   releases the lock). Then `get_budget` here. Expected: 120 and
   400, positive. Pre-fix both read negative.
5. **The scrub on a legacy book, if you can stage it.** Optional.
   On a scratch COPY of Alex from develop (pre-branch server) with
   an income target written the old way, switch to this branch:
   `get_budget` must already read it positive (heuristic read),
   and the first `set_budget_amount` must leave the raw row
   negative and the book stamped. If staging a second server
   copy is more than ten minutes, skip and say so — this path is
   unit-locked both ways.

Cleanup: `delete_budget` "BK Signs". Alex back to one budget. The
feature stamp stays on the book — that is intended; GnuCash would
have written the same one.

**Not live-testable:** the income-and-expense reversal policy
(needs a book kept under that preference — unit-locked), and the
PAYABLE/CREDIT narrowness note in the review doc.

**Report:** pass/fail per step with the actual numbers; for step 3
and 4 the exact figures GnuCash displayed; the standing routed-
around question — and specifically whether any book you keep has
ever had an income or liability budget line, since that is the
population this bug lived in.


---

## Round 1 report — 2026-09-09, Abe VII

Filed separately at `BOOKKEEPER_REPORT_BUDGET_SIGNS.md`. Verdict:
BLOCKED. Sign convention correct (steps 1–2 PASS, raw rows negative
for income, positive for expense, surfaces magnitudes). Step 3 FAIL:
the stamp was written under `features/Budgets: sign reversal fixed`,
a key GnuCash does not know; GnuCash 5.12 refused to open the
production book. Bookkeeper repaired both books by raw SQLite after
a backup. Required: the verbatim key from `gnc-features.h`, read-side
recognition of the real key, migration of the bogus one, a test that
pins the string, audit visibility for book-level slot writes, and a
standing gate — the book opens in GnuCash desktop.

## Round 2 — what changed, and the re-run

Fixed on the branch: key is `Use natural signs in budget amounts`,
copied from the header and pinned in
`TestBudgetFeatureKey::test_key_matches_gnc_features_h` against a
verbatim copy of the `#define`. The next budget write on a book
carrying the bogus key deletes that row (and leaves a real stamp
alone if desktop already wrote one — the production case). Write
responses carry `book_stamped` / `book_scrubbed` when the call
touched the book-level slot, so the audit entry shows it. The
doctor-class tool for listing/removing server-written stamps is
declined (maintainer ruling, 2026-09-09): the bogus key was never
released and migrates itself on the next write, and removing a real
stamp would make GnuCash re-scrub and flip every income row — the
one thing that should stay behind raw SQL.

Re-run, four steps:

1. **Fresh stamp opens.** On Alex (the bogus row was renamed by
   hand — confirm `SELECT name FROM slots WHERE name LIKE
   'features/%'` shows exactly the real key), `set_budget_amount`
   on "BK Signs" `Income:Salary` 5100 for period 0. Response must
   NOT carry `book_stamped` (already stamped). Open Alex in GnuCash
   desktop: opens with no features complaint, "BK Signs" shows
   Salary 5,100 / 5,000 / Groceries 300, positive.
2. **Step 4 from round 1.** In GnuCash, set `Income:Interest` 120
   and `Liabilities:Credit Card:Chase Sapphire` 400 in "BK Signs"
   for one period; close GnuCash. `get_budget` here: 120 and 400,
   positive.
3. **Stamp is audited.** On a scratch copy of Alex with the
   `features/%` rows deleted (`_unstamp`-style, raw SQL), run
   `create_budget "BK Stamp"`. Response carries `book_stamped: Use
   natural signs in budget amounts`; `get_audit_log` shows it on
   the CREATE line. Open the copy in GnuCash desktop: opens clean.
   Delete the copy.
4. **Cleanup.** `delete_budget "BK Signs"` on Alex. Production:
   nothing to do — the four income lines stay, the real stamp is
   the only one.

Acceptance: steps 1 and 3 each end with GnuCash desktop opening the
book. That is the gate.
