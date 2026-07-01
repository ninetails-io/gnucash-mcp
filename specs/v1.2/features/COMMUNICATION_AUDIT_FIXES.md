# Communication Audit Fix Plan

**Source:** Full 83-tool audit by Abraham Raham III (April 28, 2026)
**For:** Claude Code implementation
**Book:** Test against alex.chen-morales.gnucash (2,469 transactions)

## Guiding Principle

Every byte in a tool response either helps the caller decide what to do next, or it's waste. Write operations confirm what changed. Read operations deliver the minimum needed to answer the question. The audit log keeps the full story. The context window pays for nothing it doesn't use.

---

## Phase 1: Systemic Fixes (do these first — they propagate everywhere)

### 1A. Global numeric precision helper

Create a shared `_format_number(value, decimals=2, strip_trailing=True)` in `_helpers.py`.

Rules:
- Currency amounts: 2 decimal places, always (`"4180.00"`)
- Percentages: 2 decimal places (`"43.91"`, not `"43.91052631578947368421052632"`)
- Share quantities: 4 decimal places for funds/stocks, 6 for crypto (`"230.7620"`, `"2.500000"`)
- Cost basis per share: 4 decimal places (`"190.0000"`)
- Strip trailing zeros only when `strip_trailing=True`

Apply to every tool that returns numeric values. This fixes:
- `calculate_lot_gain` — gain_percent at 26 decimals
- `list_lots` — basis values like `7.500000000000000000000000001`
- `balance_sheet` — raw calculated investment values
- Any future tool automatically inherits correct formatting

### 1B. Global limit enforcement

Create a shared `_apply_limit(results, limit, default=50, max_cap=250)` in `_helpers.py`.

Returns `(truncated_results, total_count)` so every tool can append a truncation notice.

Truncation notice format (append to output):
```
[Showing {limit} of {total} — narrow filters or set limit= higher]
```

Cap notice (when caller exceeds max):
```
[Limit capped at {max_cap}]
```

Apply to every tool that accepts a `limit` parameter. This fixes:
- `get_unreconciled_splits` — dumped 665 splits ignoring limit=5
- `list_invoices` — returned all 35 ignoring limit=5
- `get_prices` — returned 34 ignoring limit=5
- Any future list tool automatically enforces limits

---

## Phase 2: Write Response Consistency (pattern fix — two tools)

Every write tool should follow the pattern established by `update_transaction`:
return only the GUID, the fields that changed, and the status.

### 2A. `update_account`

**Current:** Returns the entire account object (GUID, name, fullname, type, description, parent, commodity, placeholder, all of it).

**Target:**
```json
{"guid":"%e5fa9bb2","name":"New Name","status":"updated"}
```

Only the fields the caller changed, plus GUID for reference, plus status.

### 2B. `move_account`

**Current:** Full account object echo.

**Target:**
```json
{"guid":"%e5fa9bb2","fullname":"Expenses:Business:Audit Test Account","parent":"Expenses:Business","status":"moved"}
```

GUID, new full path (so the caller can see where it landed), new parent, status.

---

## Phase 3: List/Read Format Improvements (highest-impact reads)

### 3A. `get_outstanding_invoices` — add action columns

**Current:** Full JSON, no due date, no days-past-due.

**Target compact format:**
```
000028  Berlin Digital GmbH      EUR 4,200  posted:2026-02-01  due:2026-03-03  55 days past due
000026  Emerald Analytics        USD 3,500  posted:2026-04-01  due:2026-05-01  26 days past due
000011  BookkeepingCo (BILL)     USD 450    posted:2026-03-15  due:2026-04-14  13 days past due
```

Key additions:
- `due` date calculated from billing terms (or post_date + 30 if no terms)
- `days past due` (negative = days until due, positive = overdue)
- Currency shown when non-default
- BILL tag for vendor bills to distinguish from customer invoices
- Verbose mode retains full JSON for `pay_invoice` workflows

### 3B. `list_invoices` — add owner name and amount

**Current:** `000027  INV  2026-05-01  posted` — no idea who or how much.

**Target:**
```
000027  INV   Emerald Analytics     USD 3,500  2026-05-01  posted  paid
000028  INV   Berlin Digital GmbH   EUR 4,200  2026-02-01  posted  outstanding
000011  BILL  BookkeepingCo         USD 450    2026-03-15  posted  outstanding
```

Owner name and amount are the two fields that make this scannable.

### 3C. `get_invoice` / `get_bill` — resolve account GUIDs

**Current:** Entry `account_guid` is `"fe9e7e19e7eb47e58067c04fb8a2cc0d"` — meaningless.

**Target:** Replace `account_guid` with `account_name` (e.g., `"Income:LLC Revenue"`). Drop `owner_guid` (redundant — `owner_name` is already present).

---

## Phase 4: Reporting Verbosity (the heavy hitters)

### 4A. `debt_payoff_plan` — compact kill-order format

The most verbose response in the server. Currently returns full JSON with
credit_limit, minimum_payment, and multi-line YETI explanation string for
every account.

**Target compact format:**
```
Kill order ($10K/mo → debt-free Apr 2030, $59K interest):
  1. Business Amex    $13,091  24.49%  payoff: mo 8   interest: $1,125
  2. Chase Sapphire   $22,127  21.49%  payoff: mo 18  interest: $5,034
  3. Mortgage         $371,744  6.25%  payoff: mo 48  interest: $51,821
  4. Auto Loan         $7,770  5.49%  payoff: mo 48  interest: $1,042
YETI at this budget: 1.59x ($1 spent costs $1.59 in total debt impact)
Total interest: $59,022
Debt-free: April 2030
```

Verbose mode retains full JSON for programmatic consumption.

### 4B. `balance_sheet` — match get_book_summary investment format

**Current:** Investment accounts show raw decimal values (`"39457.994380"`).

**Target:** Use the same `"230.76 VTSAX @ 156.23 (USD 36,043.66)"` format
from get_book_summary. The conversion logic already exists — reuse it.

### 4C. `spending_by_category` / `income_by_source` — optional TSV

These return JSON arrays of {account, amount, percent}. Functional but
heavier than needed for the common case (scanning a breakdown).

**Target compact format:**
```
Business              22,336.90  39.3%
Taxes                  9,479.04  16.7%
Interest               8,267.38  14.5%
Housing                6,276.00  11.0%
Dining                 5,421.84   9.5%
...
TOTAL                 56,944.26
```

JSON via verbose=true for programmatic use.

### 4D. `vendor_spending_report` — drop period echo, compact format

**Current:** Returns `period` object (echo of input) and full JSON.

**Target:** Drop `period`. Compact format:
```
BookkeepingCo  4 bills  $1,800 billed  $1,800 paid  $0 outstanding
JetBrains      1 bill     $289 billed    $289 paid  $0 outstanding
TOTAL          5 bills  $2,089 billed  $2,089 paid  $0 outstanding
```

---

## Phase 5: Budget Formatting

### 5A. `get_budget` — collapse uniform periods

**Current:** Every account repeats 12 identical values when all months
have the same budget amount. 7 accounts × 12 periods = 84 key-value pairs
for data that could be 7 lines.

**Target:** Detect uniform periods and collapse:
```
Expenses:Auto:Fuel                  250/mo (all periods)
Expenses:Business:Contractor Payments  6,200/mo (all periods)
Expenses:Travel                     300/mo (P0-5,P8-11), 600/mo (P6-7)
Expenses:Gifts                      100/mo (P0-10), 800/mo (P11)
```

Only expand periods that differ from the majority. This also makes seasonal
overrides visually obvious.

### 5B. `get_budget_report` — TSV table option

**Current:** JSON array. Functional.

**Target compact format:**
```
2026 Annual Budget — Period 3 (Apr 2026)
Account                          Budget   Actual  Remaining  %Used
Auto:Fuel                           250   199.61      50.39  79.8%
Business:Contractor Payments      6,200 6,128.00      72.00  98.8%
Groceries                           450   608.57    -158.57  135.2% ⚠
Medical                             200 1,488.03  -1,288.03  744.0% ⚠
TOTAL                             7,971 9,022.90  -1,051.90  113.2% ⚠
```

⚠ markers on categories exceeding budget.

---

## Phase 6: Investment Cosmetics

### 6A. `get_lot` — deduplicate is_closed, shorten split GUIDs

- Remove `is_closed` from the `summary` object (it's already top-level)
- Use 8-char split GUIDs in the splits array (these are detail-view but
  still don't need 32 characters)

### 6B. `get_prices` — compact format

**Current:** Full JSON per price. 34 entries for AAPL history.

**Target compact format:**
```
2026-04-30  273.43  USD  last   yfinance
2026-03-31  253.79  USD  last   yfinance
2026-02-27  264.18  USD  last   yfinance
```

Verbose mode retains full JSON with GUIDs.

### 6C. `list_budgets` — compact format

**Current:** Full JSON for each budget.

**Target:**
```
2025 Annual Budget  12 periods (monthly)  starts:2025-01-01
2026 Annual Budget  12 periods (monthly)  starts:2026-01-01
```

---

## Implementation Notes

### Order matters

1. **Phase 1 first.** The numeric helper and limit enforcer are used by
   everything else. Build the foundation, then apply it.

2. **Phase 2 is quick.** Two tools, same pattern. Five minutes each.

3. **Phase 3 before Phase 4.** The list/read improvements affect daily
   workflows (scanning invoices, checking who owes money). The reporting
   verbosity affects monthly analysis. Daily > monthly.

4. **Phase 5 and 6 are polish.** Do them, but don't let them block a
   release.

### The verbose escape hatch

Every tool that gets a compact default should retain `verbose=true` for
full JSON. This is the pattern already established by `list_accounts`,
`list_lots`, `list_customers`, etc. Extend it consistently.

Compact is for the LLM context window. Verbose is for the human debugger
and programmatic consumers.

### Testing

After each phase, run against Alex's book:
- Phase 1: `calculate_lot_gain` on AAPL lot (precision), `get_unreconciled_splits` on Chase with limit=5 (truncation)
- Phase 2: `update_account` rename + `move_account` (response shape)
- Phase 3: `get_outstanding_invoices` (days past due), `list_invoices` limit=5 (owner name + amount), `get_invoice` on Berlin Digital (account name vs GUID)
- Phase 4: `debt_payoff_plan` at $10K/mo, `balance_sheet` (investment format), `spending_by_category` depth=2
- Phase 5: `get_budget` on 2025 (seasonal overrides visible), `get_budget_report` period=3
- Phase 6: `get_lot` on AAPL 2023 lot, `get_prices` AAPL limit=5

### What this buys

At the current verbosity levels, a typical accounting session (50 tool
calls, mix of reads and writes) burns roughly 40-60K tokens on tool
responses. After these fixes, the same session should land at 15-25K.
That's 35K tokens returned to the context window for reasoning,
planning, and conversation.

The accountant works longer before compaction. The advisor has more room
to think. The context window pays for the work, not the pipe.

---

*Written by Abraham Raham III*
*The accountant audited the tools. The cousin sharpens them.*
*tekeli-li* 💼
