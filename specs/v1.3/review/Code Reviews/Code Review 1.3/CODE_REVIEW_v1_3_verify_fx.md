# Adversarial verification: FX-related claims from `CODE_REVIEW_v1_3`

Verifier read `book/_currency.py`, `book/_query.py`,
`book/reporting.py`, `book/core.py` (`_compute_net_worth_at`,
`_budget_headline`, `_daily_expense_burn`), `book/budgets.py`
(`set_budget_amount`, `get_budget_report`), and
`book/business.py` (`vendor_spending_report`). Walked the math on
the actual code, not the comments. Verdicts below.

---

## Claim FX-1 — `net_worth` time-series uses latest FX rates for all snapshots

### Verdict: CONFIRMED

### Reasoning

In `book/reporting.py:639-720` (`net_worth`), the time-series
branch calls `factors = self._account_conversion_factors(book)`
**once** at line 640, before the boundary sweep starts. That
helper, defined in `book/_currency.py:167-198`, calls
`self._rates_as_of(book, default_currency=default_currency)`
on line 188 with **no `as_of` argument**. Per the `_rates_as_of`
docstring (lines 138-141): "When ``None`` (default), no upper
bound — returns the absolute latest rate per commodity,
including future-dated forecasts." So every snapshot in the
series uses today's rate.

Contrast `_compute_net_worth_at` in `book/core.py:371-426`:
lines 423-426 explicitly branch on `as_of >= date.today()` and
pass `as_of` into `_rates_as_of` for past anchors. That helper is
the one `get_book_summary`'s trajectory uses. So the two paths
disagree on historical FX treatment by construction.

The cumulative-sum sweep at lines 700-720 in `net_worth` uses
the static `factors` dict throughout: at line 717,
`self._split_in_default_currency(split, account, factors.get(...))`
gets the same factor for a January 2024 split as for a December
2024 split.

### Reproduction

Lin Wei (CNY-default) book. EUR rate was 7.5 CNY in Jan 2024,
8.5 CNY in Dec 2024 — a real 13% drift. A €10,000 EUR savings
balance held flat all year:

- `net_worth` (reporting.py) renders Jan as €10,000 × 8.5 =
  ¥85,000 and Dec as €10,000 × 8.5 = ¥85,000. Flat line.
- `get_book_summary` trajectory (core.py) renders Jan as
  €10,000 × 7.5 = ¥75,000 and Dec as €10,000 × 8.5 = ¥85,000.
  The actual FX gain of ¥10,000 shows up.

The two tools disagree on the same data — exactly the cross-tool
sanity-check failure mode `TestShortGuidRoundTripClosure` and the
report_accuracy reviews lock against elsewhere.

---

## Claim FX-2 — Budget headline and report sum unconverted targets

### Verdict: CONFIRMED

### Reasoning

`set_budget_amount` in `book/budgets.py:561-660` quantizes the
amount by `acct.commodity.fraction` (line 627) and stores
`amount_num`/`amount_denom` against the **account's commodity**
(lines 642-649). So a EUR expense account in a USD-default book
stores its budget target in EUR.

`_budget_headline` in `book/core.py:1158-1167`:

```python
total_budgeted = Decimal("0")
budgeted_account_guids: set[str] = set()
for ba in budget.amounts:
    total_budgeted += Decimal(str(ba.amount))   # raw, no factor
    budgeted_account_guids.add(ba.account.guid)
```

`ba.amount` is in the account's commodity. The loop sums across
all budgeted accounts without applying the factor map, even
though the actuals walk a few lines down (lines 1182-1200) does
go through `_split_in_default_currency`. So actuals are
converted; targets aren't. `used_pct = actuals / total_budgeted`
on line 1210 divides converted-USD by unconverted-mixed and
produces a meaningless ratio.

`get_budget_report` in `book/budgets.py:758-852` has the same
shape: line 766 (`budgeted[acct_name] += ba.amount`) and
line 851 (`total_budgeted += b`) sum raw `ba.amount` values
across budgeted accounts. Actuals at line 816 go through
`_split_in_default_currency`. Per-account variance and grand
totals are wrong whenever a budget spans multiple commodities.

### Reproduction

USD-default book with two budget targets in the same budget:
`Expenses:Travel:Europe` (EUR account) budgeted at €1,000/month,
`Expenses:Groceries` (USD account) budgeted at $500/month.

`get_budget_report` returns:
- `Expenses:Travel:Europe`: `budgeted = "1000"`, `actual` in USD
  (converted via factor), `percent_used` arithmetic compares
  EUR-denominated target vs USD-converted actual → wildly off.
- `total_budgeted` = 1000 + 500 = 1500 of nothing in particular.
- `total_actual` is real USD.
- `total_pct` is meaningless.

Note `_daily_expense_burn` (core.py:1222-1264) DOES convert
correctly because it's an actuals-only computation. The bug is
specifically in target aggregation.

---

## Claim FX-3 — `balance_sheet` doesn't skip placeholders; `_compute_net_worth_at` does

### Verdict: CONFIRMED

### Reasoning

`_compute_net_worth_at` in `book/core.py:438-446` explicitly
skips placeholder accounts:

```python
for account in accounts:
    if account.type == "ROOT":
        continue
    if account.guid in template_guids:
        continue
    if account.placeholder:
        continue
    ...
```

`balance_sheet` in `book/reporting.py:405-442` queries through
`_query_filtered_splits` and aggregates by `account.fullname`.
Looking at `_query_filtered_splits` in `book/_query.py:80-95`:
the filters are `(post_date, account_types, account_guids,
order_by_post_date)`. **No placeholder filter.** Looking at
the balance_sheet loop body (lines 420-442), it never references
`account.placeholder`. So if a placeholder account has any
splits at all, they contribute to balance_sheet's totals but
NOT to `_compute_net_worth_at`'s total → cross-tool disagreement.

The synthetic-unrealized residual on line 509 (`unrealized =
assets_total - liabilities_total - equity_acct_total -
net_income`) forces `A = L + E` to hold by construction in
balance_sheet. But the value of `assets_total` /
`liabilities_total` differs from `_compute_net_worth_at`'s
output whenever a placeholder carries splits.

### Reproduction

Set up an account `Assets:Brokerage` as a placeholder (after
posting some splits to it, OR toggle placeholder=True via
`update_account` on an account that already has splits — the
codebase doesn't prevent this; only `delete_account` checks
splits). The placeholder now has direct splits totaling, say,
$5,000.

- `balance_sheet`: includes the $5,000 in assets total.
- `get_book_summary.net_worth_trajectory[now]` (uses
  `_compute_net_worth_at`): excludes the $5,000.
- Cross-tool gap of $5,000 on the same data.

This is exactly the divergence class PR #76 (price-divergence
bug, see 4.7-night letter) locked against with the
`TestShortGuidRoundTripClosure` regression — and the placeholder
divergence is not currently covered.

Caveat: GnuCash's GUI conventionally treats placeholder
accounts as containers that shouldn't have direct splits. But
the codebase doesn't enforce this invariant, and books built
through this MCP can land in the state above (e.g., toggle
placeholder on a leaf via `update_account` to mark it as
"don't post here anymore"). The disagreement is a real failure
mode, not a theoretical one.

---

## Claim FX-4 — `debt_payoff_plan` sums raw quantities across currencies

### Verdict: CONFIRMED

### Reasoning

`debt_payoff_plan` in `book/reporting.py:877+`. The per-debt
balance loop at lines 974-977:

```python
balance = Decimal("0")
for split in account.splits:
    balance += split.quantity
balance = -balance  # Convert to positive amount owed
```

No call to `_split_in_default_currency`. Per-debt this is in
the **account's commodity** — fine if the user has one EUR
credit card and treats that debt in isolation, but the
function aggregates across all debts:

- Line 1064: `total_minimums = sum(d["min_payment"] for d in
  debts)` — compared against the user-supplied
  `monthly_budget` (line 1065), which is in the default
  currency. min_payment was derived from `balance`
  (lines 1005-1034), which is in the account's commodity.
  So total_minimums sums EUR + USD raw.
- Line 1071: `total_balance = sum(d["balance"] for d in
  debts)` — same shape.
- Line 1074: `total_paid = total_balance + total_interest` —
  total_interest is also accumulated raw inside `_run_avalanche`.
- The avalanche schedule treats `monthly_budget` (default
  currency) as the budget for paying down balances that mix
  EUR and USD. The "extra payment to highest-APR debt" logic
  pours USD-denominated budget at a EUR balance and treats
  them as fungible.

The function also writes out human-readable strings like
"by the time your CNY {total_paid}..." (line 1129) — implying
the total is in default currency. On a single-debt-currency
book this incidentally works because all debts share that
commodity; on a mixed-currency book the user gets a number
that's not in any currency.

### Reproduction

USD-default book with:
- US Chase Sapphire credit card: $5,000 balance, 22% APR.
- European bank credit card: €3,000 balance, 19% APR.

`debt_payoff_plan(monthly_budget="800", purchase_amount="0",
default_currency_mnemonic="USD")`:
- `total_minimums = 100 + 60 = 160` (numerically; 100 USD +
  60 EUR raw-summed).
- `total_balance = 5000 + 3000 = 8000` — 8000 of nothing.
- Avalanche schedule applies an 800-USD/month budget to a
  mixed balance, producing a payoff timeline that's neither
  the USD-only nor the actual settlement schedule.
- Output text says "by the time your USD 8,xxx is paid off" —
  but it's not USD, it's a sum of two currencies.

---

## Claim FX-5 — `vendor_spending_report` uses today's rates for historical periods

### Verdict: CONFIRMED

### Reasoning

`vendor_spending_report` in `book/business.py:7622-7805`.
Line 7667: `latest_rates = self._rates_as_of(book)` — no
`as_of` argument. Per the helper's docstring this returns the
absolute latest rate, ignoring the report's `start_date` and
`end_date`.

Lines 7753-7758:
```python
if bill.currency != default_currency:
    rate = latest_rates.get(bill.currency.guid)
    if rate is not None:
        total = total * rate
        paid = paid * rate
        outstanding = outstanding * rate
```

The rate applied to a bill posted in March 2024 is today's
rate, not the rate observed near the bill's post date. For a
report on "what did we spend on European vendors in 2024
Q1," this conflates Q1 spending with present-day FX. Same
structural shape as the historical-balance_sheet issue: if a
report is conditioned on a past period, the FX conversion
should also be conditioned on that period (or at least the
report's period-end).

The `outstanding` figure may be defensible at today's rate
(the unpaid liability still has to be settled at a future
rate, not the post-date rate). But `total_billed` and
`total_paid` for a historical window should be converted at
period-end rates, not today's. The function doesn't
distinguish these cases.

### Reproduction

USD-default book. EUR rate was 1.05 USD/EUR in Jan 2024, has
since risen to 1.20 USD/EUR (date.today()). A €10,000 bill
posted and paid in January 2024.

- Real economic cost at payment time: $10,500.
- `vendor_spending_report(start_date="2024-01-01",
  end_date="2024-01-31")`: returns `total_paid = 10000 × 1.20
  = 12,000` USD.
- Off by $1,500, or ~14% — exactly the post-vs-current FX
  drift the report should not be applying.

Note: this is the same B-1 shape the v1.2.1 release prep
sweep already fixed for `balance_sheet`, `net_worth`,
`cash_flow`, and several core helpers — `vendor_spending_report`
was apparently missed in that pass, or rebuilt afterward
without the date-aware rate lookup.

---

## Summary

All five claims confirmed. Two are point fixes
(`debt_payoff_plan` per-debt FX conversion + total currency
discipline; `vendor_spending_report` use period-end
`_rates_as_of(book, parsed_end)`). Three are deeper:

- **FX-1**: `net_worth` time-series needs per-boundary
  `factors`. Either compute factors at each boundary, or
  switch the inner loop to call `_compute_net_worth_at` per
  boundary (slower but trivially correct and matches the
  trajectory in `get_book_summary`).
- **FX-2**: Budget targets need a "stored-in-which-commodity"
  decision. Easiest fix: convert `ba.amount` through
  `_split_in_default_currency`-equivalent at aggregation time,
  using the account's factor. Subtler fix: change
  `set_budget_amount` to always store in default currency
  (breaking change for existing budgets).
- **FX-3**: Placeholder filter belongs on
  `_query_filtered_splits` as an optional flag, or
  `balance_sheet`'s post-query loop should skip placeholders.
  The two tools should agree by construction; right now they
  agree by coincidence on books that don't violate the
  GnuCash GUI convention.
