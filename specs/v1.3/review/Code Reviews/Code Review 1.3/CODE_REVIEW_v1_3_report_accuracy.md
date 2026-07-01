# Report accuracy review — v1.3 pre-release

Captured from deep-read agent run on 2026-06-02.

## Summary

Reporting suite is mostly production-ready. One ship-blocker
(`balance_sheet` uses future-dated prices unconditionally, breaking
A = L + E on historical dates), plus several defensive gaps that
don't block release.

## Critical findings

### 1. `balance_sheet` uses future-dated prices unconditionally
`src/gnucash_mcp/book/reporting.py:450`:

```python
latest_rates = self._rates_as_of(book)  # no date filter
```

Compare with the correct pattern in `_compute_net_worth_at`
(`core.py:423-426`):

```python
if as_of >= date.today():
    rates = self._rates_as_of(book)                       # latest, future OK
else:
    rates = self._rates_as_of(book, as_of, default_currency)  # filtered
```

**Bug:** `balance_sheet(as_of_date=date(2024, 12, 31))` with a
2025-01-15 forecast price picks the forecast rate. `get_book_summary`
correctly filters via `_compute_net_worth_at`. Breaks A = L + E vs.
the cost-basis equity row.

**Why the existing test misses it:** `test_summary_and_balance_sheet_agree_on_latest_price` (line 7992)
only checks `as_of_date=today()`, where both branches converge.

**Fix:** Mirror `_compute_net_worth_at`'s branching at `reporting.py:450`:

```python
if as_of_date >= date.today():
    latest_rates = self._rates_as_of(book)
else:
    latest_rates = self._rates_as_of(book, as_of_date, default_currency)
```

**Test to add:** `test_balance_sheet_historical_filters_future_prices`.

## High-priority findings

### 2. `net_worth` time-series boundary uses strict `>` (correct, but fragile)
`reporting.py:704-716`. The `txn.post_date > boundaries[b_idx]` choice
makes boundaries inclusive of their own date. Correct per docstring.
Risk: a future refactor to `>=` would silently invert it. Add a
boundary-inclusion comment.

### 3. `debt_payoff_plan` error message misleads on bookless-in-debt
`reporting.py:1058-1062`. When the book has zero CREDIT/LIABILITY
accounts, the "set APR via set_account_slot" message is confusing
because the accounts don't exist. Distinguish "no such accounts" from
"accounts exist, no APR".

### 4. `get_budget_report` parent rollup picks "nearest" via string length
`budgets.py:786-789`:

```python
if existing is None or len(acct_name) > len(existing):
    rollup_map[desc.fullname] = acct_name
```

Longer path != deeper depth in all naming schemes. Safer:

```python
acct_depth = acct_name.count(':')
existing_depth = existing.count(':') if existing else -1
if existing is None or acct_depth > existing_depth:
    rollup_map[desc.fullname] = acct_name
```

## Medium-priority findings

### 5. `cash_flow` outflow-as-positive convention is undocumented
`reporting.py:778-785`. Behavior is correct (standard accounting
convention); docstring should say so.

### 6. `spending_by_category` silently skips negative amounts
`reporting.py:281-282` (`if amount <= 0: continue`). Correct — credits
and reversals shouldn't show as spending — but it's undocumented.

## Cross-tool agreement risks

- **A:** `balance_sheet` vs. `net_worth` on historical dates. Root
  cause = finding #1; fix resolves it.
- **B:** `get_budget_report` "ytd" vs. summary trajectory anchors —
  intentional logic difference (budget-relative vs. calendar). Worth
  documenting.
- **C:** `balance_sheet` "Unrealized Gain/Loss" line vs. `net_worth`
  total — the synthetic equity row balances A = L + E but isn't
  mirrored in `net_worth`'s presentation. Numbers agree; presentation
  differs. Document.

## What looks right

- Template-account filtering — consistent across reports via
  `_template_account_guids()`.
- Voided splits — value=0, excluded from reconciliation counts.
- Currency conversion — centralized via `_split_in_default_currency()`.
- Date boundaries — inclusive at both ends, documented.
- Future transactions — excluded from "now" balances via SQL filter.
- Decimal precision preserved end-to-end.

## Test coverage gaps

- ✗ `balance_sheet` on historical date with future prices (**critical**)
- ✗ `net_worth` time-series on month boundaries with transactions posted ON the boundary
- ✗ `get_budget_report` parent rollup with deeply-nested account names
- ✓ `calculate_lot_gain` with voided splits — covered.
- ✓ Multi-currency aggregations — covered.
- ✓ Template-account filtering — covered.
