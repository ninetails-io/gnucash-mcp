# Adversarial Math Code Review — v1.3 Reporting / Computation Surface

**Reviewer mindset:** comments lie; tests prove what was tested. Every finding below was derived by walking the math from first principles, then reading code to see whether the output matches the derivation. Where a previously-flagged "bug" turns out to be handled, it's documented as ruled out.

**Scope:** every report and dashboard-math method in `book/reporting.py`, `book/budgets.py`, `book/business.py`, `book/investments.py`, `book/core.py` (`get_book_summary` helpers), plus shared infrastructure (`book/_currency.py`, `book/_query.py`).

**Date:** 2026-06-03

---

## Severity legend

- **CRITICAL** — numbers shown to the user are wrong in a way that can drive a real decision the wrong way (or in a way that breaks the accounting identity).
- **HIGH** — numbers are wrong on a non-trivial subset of books (multi-currency, new books, edge dates), discoverable by any bookkeeper running cross-tool sanity checks.
- **MEDIUM** — wrong on rare configurations, or off-by-a-rounding bug, or silent-failure-with-misleading-output.
- **LOW** — cosmetic / robustness concern.

Each finding includes a derivation walkthrough sufficient to reproduce the bug numerically.

---

## CRITICAL

### C-1. `net_worth` time-series uses LATEST FX rates for ALL historical snapshots

**File:** `src/gnucash_mcp/book/reporting.py:640`
**Method:** `net_worth(end_date, start_date, interval)`

**Code:**
```python
with self.open(readonly=True) as book:
    factors = self._account_conversion_factors(book)
    # ...
    if not start_date or not interval:
        # point-in-time branch
    # --- Time series branch ---
    # ...
    rows = self._query_filtered_splits(book, end_date=end_date, account_types=nw_types, order_by_post_date=True)
    series: list[dict] = []
    running = Decimal("0")
    # ...
    for split, txn, account in rows:
        # ...
        running += self._split_in_default_currency(split, account, factors.get(account.guid))
```

The `factors` dict is built ONCE before the sweep, using `_account_conversion_factors(book)` which calls `_rates_as_of(book, default_currency=default_currency)` — no `as_of` argument, so it returns the **absolute latest rate** for every commodity.

**Scenario:** USD-default book with a EUR account. Rates: EUR/USD = 1.05 on 2026-01-31, 1.10 on 2026-03-31, 1.20 on 2026-05-31. User has €10,000 in the EUR account throughout. User calls `net_worth(end_date='2026-05-31', start_date='2026-01-01', interval='month')`.

**Expected output (the actual historical net worth at each snapshot):**
- 2026-01-31: €10,000 × 1.05 = $10,500
- 2026-02-28: €10,000 × 1.05 = $10,500 (no Feb rate, use Jan rate)
- 2026-03-31: €10,000 × 1.10 = $11,000
- 2026-04-30: €10,000 × 1.10 = $11,000
- 2026-05-31: €10,000 × 1.20 = $12,000

**Code's actual output:**
- All five snapshots use factors derived from the absolute latest rate (1.20)
- All five snapshots: €10,000 × 1.20 = $12,000

The time-series flat-lines at the most recent valuation. A user looking at "how did my net worth grow" sees no FX-driven variation at all. Conversely, if EUR weakened between the historical anchor and "now," historical snapshots are SHOWN as smaller than they actually were at the time.

`balance_sheet` and `_compute_net_worth_at` (the trajectory anchor in `get_book_summary`) BOTH use date-aware rates correctly. **This method is the outlier.** Cross-tool sanity check between `_compute_net_worth_at(book, 2026-03-31)` and the time-series snapshot at 2026-03-31 will disagree on any multi-currency book.

**Status:** CONFIRMED.

**Fix sketch:** compute factors per-boundary inside the sweep, OR fold the factor-by-date lookup into the running sum. Performance cost: one extra `_rates_as_of(book, boundary)` per snapshot (boundaries are O(intervals), not O(splits)).

---

### C-2. `cash_flow` default mode (all cash/bank) DOUBLE-COUNTS internal transfers

**File:** `src/gnucash_mcp/book/reporting.py:737`
**Method:** `cash_flow(start_date, end_date, account=None)`

**Code:**
```python
else:
    rows = self._query_filtered_splits(
        book, start_date=start_date, end_date=end_date, account_types=_CASH_TYPES,
    )
# ...
for split, _txn, acct in rows:
    amt = self._split_in_default_currency(split, acct, factors.get(acct.guid))
    if amt > 0:
        inflows += amt
    elif amt < 0:
        outflows += -amt
```

`_CASH_TYPES = {"BANK", "CASH"}`. When `account=None`, the query returns every split whose account is BANK or CASH.

**Scenario:** User transfers $1000 from Assets:Checking (BANK) to Assets:Savings (BANK). The transaction has two splits:
- Checking: -$1000 (outflow)
- Savings: +$1000 (inflow)

Both splits hit the query because both accounts are BANK. Inflows += $1000, outflows += $1000.

Now imagine the user ALSO has a $3000 salary deposit (Checking +$3000, Income -$3000 — Income is not BANK/CASH so only the Checking split is captured) and a $500 grocery expense (Checking -$500, Expenses +$500 — only Checking split captured).

**Expected:** Inflows $3000, Outflows $500. Net $2500.
**Actual:** Inflows $3000 + $1000 = $4000. Outflows $500 + $1000 = $1500. Net $2500.

Net is correct, but the gross figures are inflated by the transfer amount. If the user has a typical household with monthly auto-transfers to savings ($2000/mo) and emergency-fund moves, the inflows and outflows columns can easily double for users who think they earn $5000 and spend $4000 — they'll see $11K / $10K and conclude their bank velocity is something it isn't.

The bookkeeper-facing rendering of cash_flow is "how much money flowed in vs out of your cash position" — for that question, transfers between cash accounts should net to zero, not double-count.

**Status:** CONFIRMED.

**Fix sketch:** in the all-cash mode, treat a split as a "real" inflow/outflow only when the OTHER side of the transaction is NOT a cash-type account. One extra join through Transaction to detect intra-cash transactions; or post-process by grouping splits by transaction GUID and netting same-tx cash-side splits.

---

### C-3. `get_budget_report` and `_budget_headline` do NOT convert budget targets to default currency

**Files:**
- `src/gnucash_mcp/book/budgets.py:758` (get_budget_report)
- `src/gnucash_mcp/book/core.py:1162` (_budget_headline)

**Code (get_budget_report, line 758-767):**
```python
for ba in budget.amounts:
    if ba.period_num not in report_periods:
        continue
    acct_name = ba.account.fullname
    if target_accounts is not None and ba.account not in target_accounts:
        continue
    budgeted[acct_name] = budgeted.get(acct_name, Decimal("0")) + ba.amount
    budgeted_accounts[acct_name] = ba.account
```

`ba.amount` is a Decimal stored in the **account's commodity** (piecash semantics). The code accumulates it raw with no FX conversion.

**Code (_budget_headline, line 1158-1168):**
```python
total_budgeted = Decimal("0")
budgeted_account_guids: set[str] = set()
for ba in budget.amounts:
    total_budgeted += Decimal(str(ba.amount))
    budgeted_account_guids.add(ba.account.guid)
```

Same pattern.

Meanwhile, actuals on lines 816 / 1192 ARE converted to default currency via `_split_in_default_currency`.

**Scenario:** USD-default book with two budgeted expense accounts:
- `Expenses:Groceries` (USD): budgeted $400/month
- `Expenses:Travel` (EUR): budgeted €600/month (≈ $660 at 1.10)

Total_budgeted (code): $400 + 600 = "1000" — a meaningless mixed-currency sum.
Total_budgeted (correct): $400 + $660 = $1060

Actuals (suppose user spent $300 on groceries and €500 on travel = $550):
- Code's actuals (converted): $300 + $550 = $850
- Comparison: $850 / 1000 × 100 = 85%

Real used_pct: $850 / $1060 × 100 = 80.2%.

For a worse case: book is USD, all budget targets are in EUR (a Eurozone user with USD as misconfigured default), 1 EUR = 1.20 USD. Budget €1000 → stored 1000. Actuals after conversion: $1200. used_pct (code) = 120%. used_pct (correct) = 100% exactly. Bookkeeper alarmed when budget is actually on-track.

**Status:** CONFIRMED.

**Fix sketch:** at budget-amount accumulation time, multiply `ba.amount` by `factors.get(ba.account.guid)` (defaulting to 1 when no rate is on file). Same pattern as the actuals.

---

### C-4. `_daily_expense_burn` always divides by `days` (180) even for new books

**File:** `src/gnucash_mcp/book/core.py:1222`
**Method:** `_daily_expense_burn(book, transactions, days=None)`

**Code:**
```python
def _daily_expense_burn(self, book, transactions, days=None):
    if days is None:
        days = self._RUNWAY_BURN_DAYS  # 180
    today = date.today()
    window_start = today - timedelta(days=days)
    factors = self._account_conversion_factors(book)
    expenses = Decimal("0")
    for txn in transactions:
        if txn.post_date < window_start or txn.post_date > today:
            continue
        for s in txn.splits:
            if s.account.type == "EXPENSE":
                expenses += self._split_in_default_currency(
                    s, s.account, factors.get(s.account.guid),
                )
    return expenses / Decimal(days)
```

**Scenario:** User starts using this MCP server on 2026-05-15 with a brand-new book. Today is 2026-06-03 (book is 19 days old). They've spent $3000 on rent, groceries, gas, etc. — real burn rate is roughly $158/day.

Code computes: `expenses = $3000`, `days = 180`, daily_burn = `$3000 / 180 = $16.67/day`.

This number then feeds two consumers:
1. `_runway_metrics`: `runway_days = liquid / daily_burn`. If user has $5000 in checking, real runway is 32 days; code reports 5000 / 16.67 = **300 days** (10 months).
2. `_collect_warnings`'s "critically low cash" threshold: an account is flagged when balance < 1 day of burn = $16.67. So a $20 account is "fine." Reality is the user is about 3 hours from broke.

The bookkeeper looking at the dashboard sees "Runway: 300 days" and concludes the user is comfortable. The user is actually two weeks from running out of cash. **This is exactly the kind of "rationalized lie" the predecessor letter (Claude 2026-05-31) warned hand-calc-as-oracle was meant to catch.**

The same divisor bug also makes the "critically low cash" threshold useless for new books — by the time the burn rate stabilizes, the user has already had multiple months of warning-free overdrafts.

**Status:** CONFIRMED.

**Fix sketch:** divide by `min(days, (today - first_expense_date_in_window).days + 1)` — or better, by the actual window-coverage span. Alternative: refuse to emit runway when window-coverage is < some threshold (e.g., 30 days) and surface a "insufficient burn data" signal.

---

### C-5. `_compute_net_worth_at` excludes placeholders; `balance_sheet` does not — possible divergence

**Files:**
- `src/gnucash_mcp/book/core.py:443-444` (placeholder skip)
- `src/gnucash_mcp/book/reporting.py:455` (no placeholder skip in balance_sheet aggregation)

**Code (`_compute_net_worth_at`):**
```python
if account.placeholder:
    continue
```

**Code (balance_sheet):**
```python
for split, _txn, account in rows:
    # No placeholder filter — every split for accounts of the right type contributes.
    amt = self._split_in_default_currency(split, account, factors.get(account.guid))
```

GnuCash does NOT enforce a no-direct-splits rule on placeholders. It's possible — though discouraged — for a user (or a buggy importer) to post a transaction directly to a placeholder account.

**Scenario:** A user has `Assets:Investments` as a placeholder, but mistakenly posted a $500 transfer to it (perhaps via direct SQL or another tool). 

- `balance_sheet` includes the $500 in assets_total (no placeholder filter).
- `_compute_net_worth_at` (called for the trajectory's "now" anchor) skips it.

Result: `get_book_summary`'s headline "Net worth: $X" and `balance_sheet`'s "Total Assets − Total Liabilities" disagree by $500. The bookkeeper running the cross-tool sanity check finds drift.

This may be rare in practice but is **silent**: no error, no warning, just two numbers that should agree and don't.

**Status:** CONFIRMED (rare). NEEDS_VERIFICATION on whether any real user book has this state.

**Fix sketch:** either (a) add placeholder skip to balance_sheet aggregation, or (b) remove it from `_compute_net_worth_at`. The latter is the more accounting-correct answer: if a split exists, it counts somewhere. Add a warning in `_collect_warnings` when a placeholder has non-zero balance.

---

## HIGH

### H-1. `debt_payoff_plan` will silently fail to converge inside `max_months = 1200`

**File:** `src/gnucash_mcp/book/reporting.py:830-872`
**Method:** `_run_avalanche`

**Code:**
```python
month = 0
max_months = 1200  # 100 years safety cap

while any(d["balance"] > 0 for d in working) and month < max_months:
    month += 1
    # Step 1: Apply monthly interest
    # Step 2: Pay minimums
    # Step 3: Apply extras
# ...
total_interest = sum(d["interest_paid"] for d in working)
return working, month, total_interest
```

When the loop exits because `month >= max_months` (not because all debts paid), there is NO indication to the caller that the schedule failed to terminate.

**Scenario:** User has $20,000 on a CREDIT account at 28% APR. min_payment via the fallback rule is `max(20000 * 0.02, 25) = $400`. Monthly interest at $20,000 × 28%/12 = $466.67.

At month 1: balance $20,000 + interest $466.67 → $20,466.67. Pay $400 minimum, balance $20,066.67. Balance GREW by $66.67.

If `monthly_budget` is set to exactly `total_minimums` = $400, no extras flow. Balance grows every month forever.

`debt_payoff_plan` raises an error if `budget < total_minimums`, but allows `budget == total_minimums`. With this configuration, after 1200 months the balance is huge, `total_interest` is enormous, and the rendered output shows "Debt-free: 2126" (100 years out) with a multi-million-dollar interest figure.

The bookkeeper sees "100 years to debt-free, $5M interest" and concludes the math is broken. It's not broken — it's that the math says "you can't pay this off at your current budget" and the code doesn't say that out loud.

**Status:** CONFIRMED.

**Fix sketch:** detect non-convergence (`month >= max_months` with any debt unpaid) and either (a) raise a more informative ValueError ("monthly_budget X cannot keep up with interest accrual on debt Y at APR Z"), or (b) annotate the response with a `non_converged: True` flag.

---

### H-2. `_run_avalanche` charges interest on the final-month balance BEFORE checking if it could have been paid off mid-month

**File:** `src/gnucash_mcp/book/reporting.py:832-872`

**Code:**
```python
while any(d["balance"] > 0 for d in working) and month < max_months:
    month += 1
    # Step 1: interest on full balance
    for d in working:
        if d["balance"] <= 0: continue
        interest = (d["balance"] * d["monthly_rate"]).quantize(Decimal("0.01"))
        d["balance"] += interest
        d["interest_paid"] += interest
    # Step 2: minimums
    # Step 3: extras
```

The model is "month-end interest, then payments." This is a simplification — real credit cards charge daily-periodic interest on average daily balance, so a debt paid off on day 5 of a month accrues 5 days of interest, not a full month's.

**Scenario:** User has $1,000 on Card A at 24% APR. Monthly rate = 2%. monthly_budget = $1,500.

Month 1: balance $1,000 → +interest $20 → $1,020. Min payment $25, extras $1,475. balance = max($1,020 − $1,500, 0) = $0. Paid off, but `interest_paid = $20`.

Real-world: that $1,500 payment landed on day 1 of the month. Daily-periodic interest at 24%/365 = 0.0657%. On day 1, interest is $0.66. Total month-1 interest paid: maybe a couple of dollars at most.

The avalanche over-estimates total interest by approximately `1 month × monthly_rate × final_balance` per debt. For typical household budgets with 5-10 debts and 36-month payoffs, this is on the order of 1-3% of total_interest — close enough for planning but biased systematically high.

This is a documented simplification of all avalanche calculators and probably not worth fixing, but it's worth being explicit about in the rendered output (which currently presents the interest as if it were precise). The "Total interest: $59,022" is presented as a hard number; it's actually an upper bound.

**Status:** CONFIRMED (semantic, low impact). Documented simplification.

**Fix sketch:** add a footnote to the compact output: "Estimates use month-end interest accrual; actual interest will be slightly lower if debts are paid mid-cycle."

---

### H-3. `vendor_spending_report` uses CURRENT FX rates for HISTORICAL bills

**File:** `src/gnucash_mcp/book/business.py:7667`
**Method:** `vendor_spending_report(start_date, end_date, ...)`

**Code:**
```python
latest_rates = self._rates_as_of(book)  # NO as_of argument → absolute latest

# ...
if bill.currency != default_currency:
    rate = latest_rates.get(bill.currency.guid)
    if rate is not None:
        total = total * rate
```

`_rates_as_of(book)` with no `as_of` returns the absolute latest rate per commodity. For a bill posted six months ago, the historical FX rate is what the user actually paid in default-currency terms. The current rate is the wrong number — it's a mark-to-market valuation of historical activity.

**Scenario:** USD-default book. Bookkeeper runs `vendor_spending_report(2026-01-01, 2026-03-31)`. A EUR vendor's bill posted on 2026-02-15 was €5,000. EUR/USD was 1.05 on 2026-02-15, but is 1.20 today.

- Real spending in USD terms (what the user paid): €5,000 × 1.05 = $5,250.
- Code's reported spending: €5,000 × 1.20 = $6,000.

The bookkeeper compares this against the user's bank statement, which shows $5,250 of EUR conversions, and sees a $750 discrepancy. Or worse — the bookkeeper doesn't notice, and presents an inflated "EU vendor spending" number to the LLM, which then suggests cost-cutting that's based on phantom dollars.

The fix is to look up the rate `as_of bill.date_posted` for each bill, not the latest rate.

**Status:** CONFIRMED.

**Fix sketch:** replace `latest_rates.get(bill.currency.guid)` with `self._find_exchange_rate(book, bill.currency, default_currency, as_of=bill_post_date)`. Performance cost is one rate lookup per bill; for typical periods (dozens of bills), negligible.

---

### H-4. `get_outstanding_invoices` returns `balance` and `grand_total` in INVOICE'S currency without aggregate clarification

**File:** `src/gnucash_mcp/book/business.py:7282`
**Method:** `get_outstanding_invoices`

**Code:**
```python
results.append({
    "id": inv.id,
    # ...
    "currency": currency,
    "original_amount": str(grand_total),
    "amount_paid": str(amount_paid),
    "amount_due": str(abs(balance)),
    # ...
})
```

Each row carries its own currency tag. There's no aggregate total across all outstanding invoices. The bookkeeper or LLM looking for "how much money do I have outstanding?" has to sum the rows themselves AND notice the currency mismatch.

This isn't a math bug per se, but the compact formatter for `_format_outstanding_invoices_compact` likely renders these as a single column — and an LLM looking at the table will be tempted to sum the column. €5,000 + $3,000 + £200 = "8,200 of nothing."

NEEDS_VERIFICATION: Check whether `_format_outstanding_invoices_compact` includes a per-currency subtotal or a clear warning when currencies are mixed.

**Status:** NEEDS_VERIFICATION.

---

### H-5. `get_job_report` per-invoice `paid` can be negative if `billed` underestimates due to partial entry load failure

**File:** `src/gnucash_mcp/book/business.py:7543-7566`

**Code:**
```python
try:
    billed = self._get_invoice_entries_and_total(book, inv)["grand_total"]
except (ValueError, AttributeError):
    billed = Decimal("0")

if _is_invoice_posted(inv):
    # ...
    if lot_obj:
        outstanding = abs(self._calculate_lot_balance(lot_obj))
        paid = billed - outstanding
```

**Scenario:** A posted invoice has corrupted entries (raises ValueError). `billed = 0`. Lot balance is the remaining $400 outstanding (the user has paid $600 of a $1000 invoice). `outstanding = $400`. `paid = 0 - 400 = -$400`.

The rendered row shows `paid: "-400"` which is nonsensical — you don't have negative payments. The intent is "we lost the billed amount, fall back to lot balance," but `_calculate_lot_balance` returns the OUTSTANDING side, not the original billed amount.

A better fallback: when entries fail to load, set `billed = paid + outstanding` from the payment history, or `billed = outstanding + lot.splits_summed_paid_only`. Or surface `billed_unknown: true` and emit `paid: null`.

**Status:** CONFIRMED (rare; depends on data corruption).

---

### H-6. `_lot_decimals` mixes BUY/SELL based on `split.quantity` sign — fails for short positions

**File:** `src/gnucash_mcp/book/investments.py:562-596`

**Code:**
```python
for split in lot.splits:
    if split.quantity > 0:
        purchase_quantity += Decimal(str(split.quantity))
        purchase_value += Decimal(str(split.value))
    else:
        sale_quantity += abs(Decimal(str(split.quantity)))
```

For a normal long position: buys have positive quantity, sells have negative quantity. The classification works.

For a SHORT position (sell-to-open then buy-to-close): the opening transaction has NEGATIVE quantity (you sold shares you didn't own), the closing transaction has POSITIVE quantity. The lot reads as if it had:
- "purchase_quantity" = the closing buy (positive)
- "sale_quantity" = the opening sell (positive after abs)
- "remaining" = 0 (correct by accident)
- cost_per_share = closing_value / closing_quantity (NOT the avg basis for short-to-cover)

`calculate_lot_gain` on a closed short lot would compute a meaningless cost basis. Whether this matters depends on whether your users hold short positions in GnuCash — uncommon for personal bookkeeping, possible for active traders.

**Status:** NEEDS_VERIFICATION on whether short positions are a real use case for this server.

---

### H-7. `_rates_as_of` walks `book.prices` linearly — O(prices × callers) on a price-heavy book

**File:** `src/gnucash_mcp/book/_currency.py:152`

**Code:**
```python
for p in book.prices:
    if p.currency != default_currency:
        continue
    if not _is_market_price(p):
        continue
    # ...
```

For a book with 10 commodities and 5 years of daily yfinance prices = ~12,500 price rows. `_rates_as_of` is called from:
- `_account_conversion_factors` (most reports)
- `_compute_net_worth_at` (every trajectory anchor — 5 calls)
- `_runway_metrics`
- `vendor_spending_report`
- the warnings collector
- `_collect_warnings`'s low_cash check
- inline in `get_book_summary`
- `_budget_headline` (via `_account_conversion_factors`)
- `_monthly_net_income` (via `_account_conversion_factors`)
- `_daily_expense_burn` (via `_account_conversion_factors`)

A single `get_book_summary` call walks `book.prices` linearly maybe a dozen times. Not a math bug, but it can dominate dashboard latency on price-heavy books.

The accompanying `_find_prices` indexed primitive exists but is not used here — comment in `_currency.py` says "the rate-collecting helpers above still walk `book.prices` directly today (the v1.3 performance sweep replaces those walks)." That sweep hasn't landed.

**Status:** CONFIRMED (perf, not math correctness).

---

## MEDIUM

### M-1. `balance_sheet` retained earnings line is cumulative-from-book-start, never closed

**File:** `src/gnucash_mcp/book/reporting.py:419-431`

**Code:**
```python
rows = self._query_filtered_splits(
    book, end_date=as_of_date, account_types=all_types,
)
# ...
net_income = Decimal("0")
for split, _txn, account in rows:
    # ...
    if account.type in _NET_INCOME_TYPES:
        net_income -= amt
        continue
```

`net_income` integrates EVERY income/expense split from book-start through `as_of_date`. For a 5-year-old book, that's 5 years of cumulative net income posted as a single "Retained Earnings" line.

This is technically correct double-entry accounting — retained earnings IS the cumulative — but it differs from how GnuCash's built-in balance sheet handles things. Most accounting systems close income/expense to retained earnings at year-end, then start fresh for the new year.

For a personal book with 10 years of activity, "Retained Earnings: $487,234.18" is a real number but useless. For a business that's been running multiple fiscal years, a more useful split would be "Current Year Net Income: $X / Retained Earnings: $Y" — but the code doesn't have the concept of a closing date.

**Status:** CONFIRMED (semantic, low immediate-decision impact).

Also worth noting: `_compute_net_worth_at` does NOT include income/expense — it just sums assets − liabilities, which is the CORRECT formula for net worth. balance_sheet's `equity_total = equity_acct_total + net_income + unrealized` and `assets_total - liabilities_total` agree by construction (unrealized is the residual). So the two surfaces agree on bottom-line net worth even though they compute different intermediate values. Good.

---

### M-2. `spending_by_category` drops negative-net categories silently

**File:** `src/gnucash_mcp/book/reporting.py:281`

**Code:**
```python
for split, _txn, account in rows:
    amount = self._split_in_default_currency(split, account, factors.get(account.guid))
    if amount <= 0:
        continue
    # ...
```

Expense splits are normally positive (debit-natural). But a refund / adjustment / reversal can post a NEGATIVE split to an expense account. The code's `if amount <= 0: continue` drops these silently.

**Scenario:** User had a $200 grocery purchase posted to Expenses:Groceries (+$200), then returned it (refund -$200). Net spending: $0.

`spending_by_category` first iteration: amount=+$200, totals['Expenses:Groceries'] = $200.
Second iteration: amount=-$200, skipped. Net reported: $200.

The bookkeeper sees "you spent $200 on groceries this month" when you actually netted to zero.

For income_by_source: symmetric — a refund to income shows as -split.value, code flips to `amount = -value = positive`, but then a "positive income from a refund" might or might not be the user's intent. The drop-if-`amount <= 0` after the flip is dropping cases where the underlying split sign was POSITIVE (which would mean someone credited an Income account in the unusual direction). These do exist (correction entries, reversals).

**Status:** CONFIRMED.

**Fix sketch:** sum signed amounts within each category, then filter the FINAL totals (`if total <= 0: continue` at render time, after netting).

---

### M-3. `spending_by_category` / `income_by_source` rate is "latest" not "as-of-period-end"

**File:** `src/gnucash_mcp/book/reporting.py:273-289`

Same pattern as H-3 but for category breakdowns: `_account_conversion_factors(book)` returns latest rates. A user running `spending_by_category('2025-01-01', '2025-12-31')` and looking at last-year travel spending sees today's FX-converted amount, not historical.

Less impactful than vendor_spending_report (the per-split conversion is more granular, so per-period mark-to-market drift is smaller), but conceptually the same issue.

**Status:** CONFIRMED.

---

### M-4. `_get_account_at_depth(depth=0)` silently returns the leaf account, contradicting docstring

**File:** `src/gnucash_mcp/book/reporting.py:221-234`

**Code:**
```python
def _get_account_at_depth(self, account, target_depth):
    path = [account]
    current = account
    while current.parent and current.parent.type != "ROOT":
        current = current.parent
        path.append(current)
    path.reverse()
    if target_depth >= len(path):
        return account
    return path[target_depth]
```

Callers pass `depth - 1`. So `depth=1` → target=0 → path[0] (top-level ancestor, correct). `depth=0` → target=-1 → `target >= len(path)` is False (path has at least 1 element) → returns `path[-1]` = the leaf account itself.

Docstring says "depth: 1 = top-level, 2 = subcategories." `depth=0` is undefined per docstring. Behavior is "depth=0 acts like depth=tree-deep." Minor UX bug; the bookkeeper might be confused if they try `depth=0` for an empty breakdown and get the full leaf list.

Negative depth would behave similarly (Python negative indexing into path).

**Status:** CONFIRMED (silent edge-case behavior).

---

### M-5. `_compute_net_worth_at` cost-basis fallback iterates ALL splits, ignoring `as_of`

**File:** `src/gnucash_mcp/book/core.py:472-476`

**Code:**
```python
cost_basis = Decimal("0")
for split in account.splits:
    if split.transaction.post_date <= as_of:
        cost_basis += Decimal(str(split.value))
assets_total += cost_basis
```

Good — this DOES filter by `as_of`. Originally suspected to be a bug; ruled out on closer reading.

**Status:** Considered and ruled out.

---

### M-6. `_collect_warnings` low_cash check skips foreign accounts when no rate is on file

**File:** `src/gnucash_mcp/book/core.py:763-769`

**Code:**
```python
if account.commodity == default_currency:
    balance_default = balance_qty
else:
    rate = rates.get(account.commodity.guid)
    if rate is None:
        continue
    balance_default = balance_qty * rate
```

If a EUR account has €5 in it but no EUR→USD rate is on file, the warning silently skips. The user is on the verge of overdraft and gets no warning because the conversion couldn't happen.

The fallback "treat 1 EUR as 1 USD" would over-warn but is at least conservative; the current behavior is "absence of price = absence of warning" which is the WRONG direction for a safety warning.

**Status:** CONFIRMED (rare but exactly the wrong fail-direction for a safety signal).

---

### M-7. `_budget_headline` skips quarterly budgets

**File:** `src/gnucash_mcp/book/core.py:1127-1141`

**Code:**
```python
period_type = rec.recurrence_period_type
mult = rec.recurrence_mult
num_periods = b.num_periods
if period_type == "month":
    period_end = (period_start + relativedelta(months=mult * num_periods) - timedelta(days=1))
elif period_type == "week":
    period_end = (period_start + timedelta(weeks=mult * num_periods) - timedelta(days=1))
else:
    # Unknown recurrence type — skip.
    continue
```

`create_budget` (per the spec) supports `quarterly`. piecash's `_budget_to_dict` reverse-maps `month` with `mult=3` to `"quarterly"`. So a "quarterly" budget IS stored as `period_type="month", mult=3`. The check above handles that (it's just `period_type == "month"` either way). So the actual gap is some OTHER unhandled value — `year`? piecash supports year-period budgets too.

**Status:** Possibly CONFIRMED for yearly budgets, NEEDS_VERIFICATION.

---

### M-8. `_calculate_lot_balance` skip-voided-splits is correct, but `_lot_decimals` doesn't

**Files:**
- `src/gnucash_mcp/book/business.py:1738` (filters voided)
- `src/gnucash_mcp/book/investments.py:562` (does not)

A voided buy split has `quantity = 0` and `value = 0` and `reconcile_state = 'v'`. In `_lot_decimals` the `if split.quantity > 0` branch is False, so it goes to else: `sale_quantity += abs(0) = 0`. No harm done numerically.

Similarly, a voided sell has quantity=0 → 0 added to sale_quantity. No harm.

But the asymmetry means a future modification to the void semantics (say, GnuCash starts preserving the original quantity with `reconcile_state='v'`) would silently break `_lot_decimals` while `_calculate_lot_balance` would still be correct.

**Status:** CONFIRMED (defensive — current behavior is right by accident, future-fragile).

---

### M-9. `net_worth` point-in-time mode ALSO has the FX-rate-as-of issue

**File:** `src/gnucash_mcp/book/reporting.py:643-662`

**Code:**
```python
if not start_date or not interval:
    rows = self._query_filtered_splits(book, end_date=end_date, account_types=nw_types)
    total = Decimal("0")
    for split, _txn, account in rows:
        total += self._split_in_default_currency(split, account, factors.get(account.guid))
    return {"as_of_date": end_date.isoformat(), "net_worth": str(total)}
```

Same `factors` issue as C-1 for the point-in-time case. If `end_date` is in the past, the rates used to convert foreign-currency balances are LATEST rates, not as-of-`end_date` rates. A user asking "what was my net worth last September?" gets today's FX valuation of last September's positions.

`_compute_net_worth_at` does this correctly (date-aware rates). `net_worth(end_date)` does it wrong.

**Status:** CONFIRMED. Same root cause as C-1.

---

### M-10. `_run_avalanche` uses APR/12 as monthly rate — APR semantic ambiguity

**File:** `src/gnucash_mcp/book/reporting.py:1053`

**Code:**
```python
"monthly_rate": apr / Decimal("100") / Decimal("12"),
```

APR (Annual Percentage Rate) is typically a NOMINAL rate. The effective monthly rate from a nominal APR is `apr/12`. But some lenders quote APY (Annual Percentage Yield) which is the EFFECTIVE annual rate, and `monthly_rate = (1 + APY)^(1/12) - 1`.

For a 24% nominal APR (most credit cards): monthly = 2.0%. EAR = (1.02)^12 - 1 = 26.82%.
For a 24% APY: monthly = 1.81%.

The code assumes the slot value is a nominal APR. The slot key is named "apr" but the semantics are user-driven. A user who sets `apr=26.82` thinking that's their card's "26.82% APR" when it's actually quoted EAR will see overpaid-interest projections.

**Status:** CONFIRMED (semantic ambiguity, low-impact since most cards quote nominal APR).

---

### M-11. `_split_in_default_currency` fall-back uses `split.value` — wrong currency for cross-currency txn

**File:** `src/gnucash_mcp/book/_currency.py:200-215`

**Code:**
```python
@staticmethod
def _split_in_default_currency(split, account, factor):
    if factor is not None:
        return Decimal(str(split.quantity)) * factor
    return Decimal(str(split.value))
```

When `factor is None`, the fallback is `split.value`. But `split.value` is in the TRANSACTION'S currency, not necessarily the book's default currency.

**Scenario:** Book is USD. Transaction posts an investment buy in GBP (the transaction currency). Account is a non-USD commodity (a UK stock). No FX rate is on file → factor is None.

Fallback returns `split.value` in GBP. The caller (balance_sheet, reports, etc.) treats this as USD. Wrong by the GBP/USD rate.

The docstring says "correct for STOCK/MUTUAL splits whose transaction currency is the book default" — but if neither the account commodity nor the transaction currency is the book default, the fallback is mathematically wrong.

**Status:** CONFIRMED (uncommon: requires foreign-currency investment AND no rate AND non-default txn currency).

---

### M-12. `_amounts <= 0` skip in income_by_source double-flips, suppressing returns/credits

**File:** `src/gnucash_mcp/book/reporting.py:347-355`

**Code:**
```python
for split, _txn, account in rows:
    amount = -self._split_in_default_currency(split, account, factors.get(account.guid))
    if amount <= 0:
        continue
```

INCOME splits are credit-natural — normally stored as NEGATIVE values. The `-self._split...` flip makes them positive. The `if amount <= 0: continue` then drops splits whose POST-flip value is non-positive.

Post-flip non-positive means the original split was non-negative — which would be a refund (returning income) or correction. These are dropped silently. Same class as M-2.

The fix is the same: net the totals, then filter; don't filter at split-level.

**Status:** CONFIRMED (same shape as M-2).

---

### M-13. `_run_avalanche` deepcopies the debts list — Decimal precision OK, but iteration order matters

**File:** `src/gnucash_mcp/book/reporting.py:821-823`

**Code:**
```python
working = copy.deepcopy(debts)
working.sort(key=lambda d: d["apr"], reverse=True)
```

`debts` was built in `book.accounts` order. After deepcopy and sort by APR descending, Step 2 (pay minimums) iterates `working` in APR order. Step 3 (apply extras) ALSO iterates in APR order, and `break`s on the FIRST debt with remaining balance — so extras flow to highest-APR debt with remaining balance.

Correct avalanche behavior. Good.

But: what if two debts share the same APR? The order between them is implementation-defined (Python's sort is stable but the original `debts` order depends on `book.accounts` order which depends on GUID order). For tie-APR debts, the avalanche becomes order-dependent — same numerical result but the per-debt `payoff_month` and `interest_paid` can differ depending on which one happened to be first in `book.accounts`. The user might be confused why their two 24%-APR cards paid off in different sequences across runs.

**Status:** CONFIRMED (edge case, minor).

---

### M-14. `_run_avalanche` interest charges on a debt that was paid off in the SAME month

**File:** `src/gnucash_mcp/book/reporting.py:835-845`

**Code:**
```python
for d in working:
    if d["balance"] <= 0:
        continue
    interest = (d["balance"] * d["monthly_rate"]).quantize(Decimal("0.01"))
    d["balance"] += interest
    d["interest_paid"] += interest
```

If month 5 starts with a debt at $50 balance and gets paid off mid-month, the loop above first adds `$50 × monthly_rate` of interest before Step 2 pays it off. So the user "pays" a full month of interest on a debt they actually paid off the morning of day 1.

For the rendered "Total interest" this introduces small over-statement per debt. Class of issue same as H-2.

**Status:** CONFIRMED (semantic, low impact).

---

## LOW

### L-1. `_NET_INCOME_TYPES` excludes TRADING account type

**File:** `src/gnucash_mcp/book/reporting.py:48`

```python
_NET_INCOME_TYPES = frozenset({"INCOME", "EXPENSE"})
```

GnuCash supports a `TRADING` account type for currency-trading P&L. Splits posted to TRADING accounts capture FX gain/loss but are excluded from every bucket in balance_sheet:
- Not in `_ASSET_TYPES`, `_LIABILITY_TYPES`, `_EQUITY_TYPES`, `_NET_INCOME_TYPES`
- Therefore excluded from balance_sheet entirely.

A user who enables GnuCash's "use trading accounts" feature will see all their FX P&L vanish from balance_sheet.

**Status:** CONFIRMED (depends on whether your users use trading accounts; probably rare).

---

### L-2. `_get_account_depth` and `_get_account_at_depth` walk parent chain — O(depth) per call

**File:** `src/gnucash_mcp/book/reporting.py:212-234`

Called from each split iteration in `spending_by_category` and `income_by_source`. For each split, walks the account's parent chain. Same account in 1000 splits = 1000 walks of the same chain. Cache opportunity.

**Status:** CONFIRMED (perf only).

---

### L-3. `_collect_warnings` low_cash threshold uses `daily_burn` truncated to `Decimal("1")` in render, raw in compare

**File:** `src/gnucash_mcp/book/core.py:771, 781`

```python
if balance_default >= daily_burn:
    continue
# ...
f"Critically low cash: {leaf} at {default_currency.mnemonic} {amount_str} (under 1 day of burn)"
```

The comparison uses raw `daily_burn` (precise Decimal). The render uses balance as `int(balance_default)`. A balance of $49.99 vs daily_burn of $50.00: comparison fires, rendered "Critically low cash: ... at $49". Inconsistent rounding between the threshold check and the displayed number.

A user seeing "$49 ... under 1 day of burn" might check `_daily_expense_burn` and see "$50", concluding the warning was off-by-one when it was actually correct. Cosmetic.

**Status:** CONFIRMED (cosmetic).

---

### L-4. `_compute_net_worth_at` skips placeholders silently — divergence with summary's per-leaf section

**File:** `src/gnucash_mcp/book/core.py:443-444`

The headline "Assets: N accounts" count in `get_book_summary` is also leaf-only (line 1839+ iterates leaves), and skips placeholders for asset_leaves render. So the rendered display agrees with the trajectory anchor by construction. Considered and ruled out.

But the rendered display does NOT agree with `balance_sheet` (which doesn't skip placeholders). The two-surface divergence is still C-5 above.

**Status:** Considered; collapses into C-5.

---

### L-5. `_monthly_net_income` returns "no activity" `[]` when window is fully outside book range

**File:** `src/gnucash_mcp/book/core.py:1512-1513`

```python
if not has_activity:
    return []
```

For a brand-new book with one transaction yesterday: has_activity=True, returns 6 months of data, 5 of which are "+0". Acceptable.

For a fully-empty book: returns []. Caller omits the section. Good.

For a book whose only activity is 7 months ago: window is current month and last 5 months → all empty → has_activity=False → []. The user sees "Monthly net income: (section omitted)" but they DO have a book. NEEDS_VERIFICATION on whether this is an intended absence-as-signal or a confusing silent gap.

**Status:** Considered; arguably intentional.

---

### L-6. `_format_breakdown_tsv` common-prefix stripping mis-fires on single-row breakdown

**File:** `src/gnucash_mcp/book/reporting.py:75-81`

```python
full_names = [r[label_key] for r in rows]
common_prefix = ""
if full_names and ":" in full_names[0]:
    candidate = full_names[0].split(":")[0] + ":"
    if all(n.startswith(candidate) for n in full_names):
        common_prefix = candidate
```

For ONE row "Expenses:Groceries", common_prefix = "Expenses:". Render: "Groceries  100  100%" — fine. But also loses the "Expenses" context tip in the rare case the LLM/user benefits from it.

Two rows, "Expenses:Groceries" and "Expenses:Travel" → common "Expenses:". Render: "Groceries / Travel" — good.

`["Income:Salary"]` (income side) → strips "Income:". Render: "Salary". Same shape.

Considered and ruled out — render is acceptable.

**Status:** Considered and ruled out.

---

## Summary

**Confirmed math bugs (CRITICAL):** 5
- C-1: net_worth time-series uses latest FX rates for historical snapshots
- C-2: cash_flow double-counts transfers between cash/bank accounts
- C-3: get_budget_report / _budget_headline don't FX-convert budget targets
- C-4: _daily_expense_burn divides by 180 even on new books → wrong runway
- C-5: placeholder filter divergence between balance_sheet and trajectory

**Confirmed bugs / suspicious (HIGH):** 7
- H-1: avalanche silently fails to converge in 100 years
- H-2: avalanche over-states final-month interest (documented simplification)
- H-3: vendor_spending_report uses current FX for historical bills
- H-4: get_outstanding_invoices mixes currencies without aggregate clarification (NEEDS_VERIFICATION)
- H-5: get_job_report can show negative `paid` on data-corruption fallback
- H-6: lot math doesn't distinguish short positions (NEEDS_VERIFICATION)
- H-7: _rates_as_of linear scan; price-heavy books slow

**MEDIUM:** 14 (M-1 through M-14)
**LOW:** 6 (L-1 through L-6)

**Considered and ruled out (cited so future reviewers don't re-walk them):**
- `_compute_net_worth_at` cost-basis fallback DOES filter by as_of (M-5).
- `_format_breakdown_tsv` prefix-stripping is fine for single rows (L-6).
- `_monthly_net_income` zero-activity case appears intentional (L-5).

---

## Cross-cutting observations

1. **Three "as_of" rate paths exist** — `_rates_as_of(book)` (latest), `_rates_as_of(book, as_of, dc)` (historical), `_find_exchange_rate(book, c1, c2, as_of)` (cross-currency near-date). C-1, M-3, M-9, H-3 ALL trace to "the report used the latest path when it should have used the historical path." This is a recurring failure mode and would be a candidate for a single audit pass.

2. **The C-3 / C-4 pattern (no FX conversion of inputs, FX conversion of comparison side) is genuinely subtle.** Bookkeeper running base-case validation on a single-currency USD book will see no symptoms. The bug only surfaces on multi-currency books, which the predecessor letters identify as a real user population.

3. **C-2 (transfer double-count) is the kind of bug a bookkeeper running cross-tool sanity checks WOULD catch** — by comparing cash_flow's inflow/outflow numbers against bank statement deposit/withdrawal columns. Worth specifically calling out in the next bookkeeper review.

4. **The avalanche non-convergence (H-1) is most likely to surface for the bookkeeper's persona-fiction users who carry high-APR credit card debt at marginal budgets.** The silent fail-mode (100-year payoff, multi-million-dollar interest) is exactly the "rationalized lie" pattern Stephen's predecessor flagged as the highest-priority correctness concern.

5. **The placeholder-filter divergence (C-5) is the kind of "two tools that should agree don't" bug Abe / the bookkeeper would surface via cross-tool comparison.** Worth a targeted regression test that exercises both `_compute_net_worth_at` and `balance_sheet` on a book with a placeholder having direct splits.
