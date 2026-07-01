# Bookkeeper Test Plan — PR #92 Re-Review

**Branch:** `feat/v1.3-blockers`
**Status at signoff time:** stale (your previous review)
**Status now:** ~7 substantive fixes landed after signoff
**Goal of this pass:** verify the fixes don't break your workflows
and that the cross-tool numbers tie

---

## Context: What's Changed Since Your Last Signoff

Your previous review covered the original two items (FX correctness
in `spending_by_category` / `income_by_source`, and the dashboard's
overdue counts + jobs line). Stephen had a foreboding sense
afterward and an audit pass surfaced more. Things that landed after
your signoff:

1. **`balance_sheet` now actually balances (A = L + E).** RECEIVABLE
   and PAYABLE account types are now included in the asset and
   liability totals — Alex's $15.5K of A/R was previously invisible.
   A synthetic "Unrealized Gain/Loss" equity row absorbs investment
   market drift and FX translation effects.
2. **Cross-tool agreement.** `get_book_summary`'s "Assets:" /
   "Liabilities:" headline totals and the "now" trajectory anchor
   all changed to match `balance_sheet`. Net worth on Alex shifted
   from ~$189K to ~$204K — that's correct, A/R now counts.
3. **Multi-currency aggregation sweep.** Four more helpers had the
   same FX bug as `spending_by_category`. Fixed in
   `_monthly_net_income`, `_budget_headline`, `_daily_expense_burn`
   (runway divisor), and `vendor_spending_report`. Monthly nets
   shifted on Alex by single-digit percentages reflecting EUR/CAD
   revenue revalued at current rates instead of mixed face values.
4. **Strict argument validation.** Unknown kwargs on any tool now
   raise a clear `ValidationError` instead of silently no-opping.
   `balance_sheet(as_of_date="")` also rejects (empty string is
   not the same as "use today").
5. **`balance_sheet` defaults to today** when called with no args
   — matching `get_book_summary`'s implicit cutoff so side-by-side
   comparison agrees without threading the same date through both.
6. **`id` alias on delete tools.** `delete_invoice`, `delete_bill`,
   `delete_voucher`, `delete_credit_note` accept both `id`
   (preferred, matches the rest of the invoice surface) and the
   legacy `<entity>_id`.
7. **Book path redacted** in `get_server_config` and the first
   line of `get_book_summary`. Both now show filename only, not
   the full absolute path.
8. **`except_guids` parameter** on `reconcile_account` (this was
   the one you tested originally, but worth one more spot check
   against the strict-validation behavior).
9. **Module group aliases.** `--modules=bookkeeper` and
   `--modules=investor` now work as group aliases.

---

## Pre-Flight: Confirm You're on the Right Code

Run `get_server_config`. You should see:

```
Modules loaded: all
Tools available: 106
Book: <your-book>.gnucash      ← filename only, no directory
Debug mode: ...
Version: 1.2.1                  ← still 1.2.1; bumps at release
```

If `Book:` shows a full path, the server is on stale code — ask
Stephen to bounce it.

---

## Cross-Tool Consistency Block (Highest Value)

These are the bug class the recent fixes targeted. Run on Alex's
book (or whichever book you use that has both investments and
A/R activity).

### Test 1: A = L + E

Call `balance_sheet()` with no arguments. Compute:

```
A − (L + E)
```

**Expected:** exactly zero. If not, the synthetic
"Unrealized Gain/Loss" line is supposed to be the balancing
residual. Numbers won't round to zero — they'll BE zero.

### Test 2: balance_sheet vs net_worth vs get_book_summary

Call all three in succession with no date arguments (so they all
default to today):

- `balance_sheet()` → note `A − L`
- `net_worth(end_date=<today's ISO date>)` → note `net_worth`
- `get_book_summary()` → find the "now:" line under
  "Net worth trajectory"

**Expected:** all three agree to the cent (or to formatting
precision — summary rounds to whole dollars).

### Test 3: Receivables count consistency

In `get_book_summary`:
- "Assets: N accounts, USD X" headline — note N and X
- "Receivables: M accounts, USD Y" breakout — note M and Y
- Asset section lists some accounts beneath the headline. Count
  the lines listed. Call that L.

**Expected:**
- N = L + M (headline count includes both the listed asset
  accounts and the receivables broken out separately)
- X − Y = sum of the L listed asset balances
- `balance_sheet()`'s assets total = X

If the bookkeeper's headline count doesn't tie to the displayed
lines (e.g. "Assets: 15 accounts" but only 12 listed and no
Receivables section visible), flag it.

### Test 4: Monthly net column

In `get_book_summary` find the "Monthly net (last 6 months)"
section. Each line shows whole-dollar net income.

**Expected:** numbers reflect EUR/CAD invoice activity converted
to the book's default currency. If you have a clear month with
known foreign-currency invoices, sanity-check that the dollar
figure is in the right ballpark (USD-equivalent at current
rates), not the raw foreign amount.

---

## Per-Area Smoke Tests

### Dashboard (`get_book_summary`)

- [ ] Receivables line includes parenthesized `(N invoices, M overdue)`
- [ ] Payables line shows similarly if A/P balances exist
- [ ] `Jobs: N active` line appears if jobs are in use
- [ ] First line is `Book: <filename>` only (no path)
- [ ] Net worth trajectory's "now" anchor includes A/R / A/P
      (will differ from prior runs that you remember by the A/R
      delta — this is the deliberate restatement)
- [ ] Stale price warnings still surface when appropriate

### Balance Sheet (`balance_sheet`)

- [ ] No args → defaults to today
- [ ] Equity section shows "Unrealized Gain/Loss" line if any
      investments have moved from cost basis (or any
      foreign-currency cash/A/R has had FX drift)
- [ ] Receivable accounts appear under Assets
- [ ] Payable accounts appear under Liabilities (if any)
- [ ] `balance_sheet(as_of_date="")` → loud validation error
      (NOT a today-cutoff report)

### Reconciliation (`reconcile_account`)

- [ ] `reconcile_all=True, except_guids=[...]` works as documented
- [ ] `reconcile_all=True, except=[...]` (the wrong name) → clear
      validation error (NOT a silent no-op)

### Business Tools

- [ ] `delete_invoice(id="...")` works (preferred parameter name)
- [ ] `delete_invoice(invoice_id="...")` also works (legacy alias)
- [ ] `delete_invoice(id="x", invoice_id="y")` → clear mutex error
- [ ] `delete_invoice()` with no args → clear missing-parameter error
- [ ] Same patterns work for `delete_bill`, `delete_voucher`,
      `delete_credit_note`
- [ ] `vendor_spending_report` totals are in default currency
      across mixed-currency bills

### Module Groups

(Each requires server restart with the flag — Stephen does the
bounce.)

- [ ] `--modules=bookkeeper` loads ~20 tools (reconciliation +
      reporting + budgets + scheduling, with core always-on)
- [ ] `--modules=investor` loads ~12 tools (portfolio + tax_lots)
- [ ] `get_server_config` after each restart confirms what's
      loaded

---

## Edge Cases Worth Probing

These are the kinds of cases the test suite covers but live MCP
verification adds confidence:

1. **A book with NO investments and NO A/R.** Does
   `balance_sheet` still close without rendering an empty
   "Unrealized Gain/Loss" line? (Expected: line omitted when
   unrealized = 0.)

2. **A reconciliation where statement balance is wrong by $0.01.**
   The fix should reject with a precise discrepancy amount.

3. **Calling a tool with a typo in a parameter name** (e.g.,
   `get_balance(account_naem="...")`). Expected: validation
   error naming the unknown field. Pre-fix this would have
   silently failed with a less informative error.

4. **`get_book_summary` on a book where investments have lost
   value below cost basis.** Unrealized line should show a
   NEGATIVE number, not just positive.

---

## What This Pass Is NOT Asking

Per the test-plan principle: bookkeeper validation confirms the
server works in the real world on base cases. It does NOT:

- Independently calculate financial figures from primary sources
  to verify the math
- Audit code paths that aren't exercised by the workflows you
  normally run
- Catch edge cases not specifically directed in this plan

If you spot something that feels wrong but isn't in the plan,
flag it — Stephen's foreboding instinct has been right twice on
this PR. But the absence of a finding from your pass is NOT
sufficient signal that the math is right; that's been verified
through code review.

---

## Reporting Back

Format your signoff or findings as:

**If all green:**
- Confirm cross-tool consistency block (tests 1-4) all pass
- Confirm at least one item from each per-area section was
  exercised
- Note anything you tested beyond this plan
- "Ready to merge to develop" or equivalent

**If findings:**
- One section per finding, including:
  - Reproducer (exact tool call + inputs)
  - Expected behavior
  - Actual behavior
  - Severity hunch (blocking vs follow-up vs cosmetic)
- Plus the all-green confirmations for what DID pass

Either format Stephen can paste back to the implementation
session for triage.
