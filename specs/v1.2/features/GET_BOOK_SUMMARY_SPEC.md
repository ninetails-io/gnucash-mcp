# Spec: get_book_summary improvements

## Context

`get_book_summary` is the orientation tool an LLM calls at session start to understand what's in a GnuCash book and what state it's in. The current output is informational — accounts, balances, counts. This spec adds **operational signals** (what needs attention before bookkeeping can proceed safely) and **trajectory signals** (how things are changing over time).

Design principle: every addition should answer either "what does Claude need to know to avoid making mistakes in this book?" or "what does the user care about that Claude should reference proactively?" Additions that don't pass one of those tests should be cut.

Token efficiency principle: each section should give the *headline* of its concern with a clear pointer to the dedicated tool that returns detail. Counts and exception flags, not enumeration.

## Six additions

### 1. Reconciliation status by account type

Replace the current "N unreconciled" count (which counts splits across all account types and is misleadingly large) with per-account last-reconciled-date for the account types where reconciliation actually applies.

**Account types to include:** BANK, CREDIT, LIABILITY
**Account types to exclude:** EQUITY, INCOME, EXPENSE, STOCK, MUTUAL, TRADING, CASH, A/R, A/P, ASSET (unless it has cleared/reconciled splits — see note), PLACEHOLDER

For each included account, compute the most recent reconciliation date (latest `post_date` among splits where `reconcile_state` = 'y'). If no reconciled splits exist, mark as "never reconciled."

**Output format:**
```
Reconciliation:
  Checking: through 2026-03-31
  Savings: through 2026-03-31
  Chase Sapphire: through 2025-12-15 (4 months behind) ⚠
  Business Amex: never reconciled ⚠
  Mortgage: through 2026-04-01
```

**Warning threshold:** flag accounts where last reconciliation is more than 45 days old, or where the account has never been reconciled despite having transactions. The 45-day threshold accommodates monthly statement cycles plus a grace period.

**Note on ASSET type:** some users put brokerage cash, escrow, or prepaid accounts under ASSET. If an ASSET account has any splits with `reconcile_state` = 'y' or 'c' in its history, treat it as reconcilable. Otherwise skip.

**Why this matters:** the existing "unreconciled" count is operationally useless because it includes income/expense/equity splits that conceptually can't be reconciled. The replacement gives Claude actionable per-account state and lets it warn the user about books that are drifting out of sync with reality.

### 2. Net worth trajectory

Five data points showing how net worth has evolved: now, 1 month ago, 3 months ago, 6 months ago, 12 months ago.

**Computation:** for each historical date D, compute net worth as of D using the same logic as the current "Net worth" line, but with prices and balances as of D rather than as of today. Use the most recent price prior to D for each commodity.

**Output format:**
```
Net worth trajectory:
  12mo ago: USD 148,221
   6mo ago: USD 164,503
   3mo ago: USD 172,887
   1mo ago: USD 176,114
       now: USD 179,015
```

**Edge cases:**
- If the book has less than 12 months of data, omit the data points before the start of the data range. Still include all points within range.
- If no transactions exist before a given historical date, that data point is the same as the opening balance state. Include it anyway; the LLM will draw correct conclusions from a flat trajectory.

**Why this matters:** a slope number (Marco's KPI) gives a single signal but loses information about acceleration. A chart (GnuDash) is too expensive in tokens. Five data points is the sweet spot — costs ~30 tokens, lets the LLM see whether recent months broke the trend, supports comparative reasoning.

### 3. Cash flow monthly (last 6 months)

Net income (income minus expenses) for each of the last 6 calendar months.

**Computation:** for each of the last 6 calendar months, sum all transactions where the offsetting account is in the Income hierarchy (positive contribution) or Expense hierarchy (negative contribution). Net is income - expenses.

**Output format:**
```
Monthly net (last 6 months):
  Apr 2026: +1,247
  Mar 2026: +890
  Feb 2026: -234
  Jan 2026: +2,108
  Dec 2025: +1,455
  Nov 2025: +987
```

**Currency:** report in book's base currency. Don't break out by currency in this section; if multi-currency activity exists, use the book's commodity conversion at month-end prices.

**Edge cases:**
- Current month (partial): include with note "(MTD)" suffix on the month label
- Months with no income/expense activity: report as 0, don't omit

**Why this matters:** identifies recent anomalies, surplus vs. deficit pattern, and seasonality without requiring a separate tool call. The LLM can reference these numbers conversationally ("I notice February was negative — want me to look at what happened?").

### 4. Runway

Number of days the household could survive at current burn rate if income stopped today.

**Computation:**
- `liquid_assets` = sum of balances in account types BANK, CASH, ASSET (where ASSET appears to be cash-equivalent — has commodity = book currency, no investment characteristics). Exclude STOCK, MUTUAL, real estate, vehicles, retirement accounts, HSA.
- `daily_burn` = total expenses over last 180 days / 180
- `runway_days` = liquid_assets / daily_burn

**Output format:**
```
Runway: 47 days ⚠ (USD 22,173 liquid / USD 471/day burn)
```

**Warning thresholds:**
- ⚠ if runway < 60 days
- No warning if runway ≥ 60 days (don't add a green checkmark; absence of warning is the signal)

**Edge cases:**
- If `daily_burn` is zero or negative (income exceeded expenses), report as "unlimited (income exceeds expenses)"
- If `liquid_assets` is negative (overdrafts exceed positive cash positions), report as "0 days — liquid position is negative" with ⚠

**Why this matters:** the single most actionable personal-finance number that doesn't appear on standard financial statements. Banks don't show it, brokerages don't show it. It's the answer to "should I be panicked right now."

### 5. Anomaly/warnings section

Consolidated section for everything that needs attention before normal bookkeeping work proceeds. This is the section the LLM should scan first.

**Conditions to check and flag:**

| Condition | Message format |
|-----------|---------------|
| Imbalance-{currency} account has non-zero balance | `Imbalance-USD: $X.XX (data integrity issue)` |
| Orphan-{currency} account has non-zero balance | `Orphan-USD: $X.XX (data integrity issue)` |
| Any commodity has no price within last 30 days | `Stale price: {ticker} last updated {N} days ago` |
| Scheduled transaction is past its due date | `Overdue scheduled: {name} due {date}` |
| Posted invoice past its due date with non-zero balance | `Past due invoice: {customer} {N} days overdue, $X.XX` |
| Posted bill past its due date with non-zero balance | `Past due bill: {vendor} {N} days overdue, $X.XX` |
| Reconcilable account >45 days behind on reconciliation | `Reconciliation behind: {account} ({N} days)` |

**Output format:**
```
Warnings:
  ⚠ Stale price: GBP last updated 621 days ago
  ⚠ Overdue scheduled: Mortgage due 2026-04-25
  ⚠ Past due invoice: Acme Corp 14 days overdue, USD 5,000
  ⚠ Reconciliation behind: Business Amex (never reconciled)
```

**Omit the section entirely if no warnings.** Don't print "Warnings: none" — absence is the signal.

**Sort order:** data integrity issues first (most critical), then stale prices, then overdue items, then reconciliation. Within each category, most-overdue first.

**Why this matters:** an LLM reading the summary should know in one scan whether to proceed normally or to address issues first. Without a consolidated section, these signals are scattered across multiple tool calls and the LLM has to remember to check each one.

### 6. Budget vs actual headline

If the book has at least one budget marked active, include a one-line summary for the most recently updated budget.

**Computation:**
- Use the most recently updated budget (by `last_modified` or equivalent timestamp; if not tracked, use the budget whose period range includes the current date)
- `period_elapsed_pct` = (current date - period start) / (period end - period start) × 100
- `budget_used_pct` = sum of actual expenses in budgeted categories / sum of budget targets × 100
- `variance_pct` = budget_used_pct - period_elapsed_pct

**Output format:**
```
Budget (Annual 2026): 73% used / 62% elapsed (+11% over pace) ⚠
```

**Warning thresholds:**
- ⚠ if variance > +10% (overspending)
- No warning if variance ≤ +10% (on pace or underspending)

**Output if no active budget:** omit the section entirely.

**Output if multiple budgets exist:** show only the most recently updated one. Note in tool description that users with multiple budgets can call `get_budget_report` for full detail.

**Why this matters:** if the user has set up a budget at all, they care about it. Surfacing the headline in the summary lets the LLM proactively reference it ("I notice you're 11% over pace on this year's budget — want me to identify which categories are driving it?") without an extra tool call.

## Implementation notes

### Section ordering in output

Suggested order, top to bottom:
1. Book metadata (path, currency, date range) — existing
2. Warnings (NEW — section 5) — most critical, should be seen first
3. Account counts and balances — existing
4. Reconciliation status (NEW — section 1) — operational
5. Net worth trajectory (NEW — section 2)
6. Monthly cash flow (NEW — section 3)
7. Runway (NEW — section 4)
8. Budget headline (NEW — section 6) — if present
9. Existing tail content (transactions, scheduled count, business counts, commodities, net worth)

The Warnings section goes near the top because if there's data integrity trouble, every other number in the summary is suspect. The user/LLM should see the trouble before they see the data that depends on it.

### Performance considerations

The trajectory and monthly cash flow additions require historical balance computation, which can be expensive on large books. Consider:

- Caching computed trajectory data with a short TTL (e.g., 1 hour) keyed on book modification time
- For books over some threshold (e.g., >50,000 transactions), an opt-in flag to skip historical computation
- For books over some threshold of accounts (e.g., >500), defaulting to fewer trajectory points (3 instead of 5)

The runway and monthly cash flow only need 6 months of expense aggregation, which is bounded and fast even on large books.

### Error handling

Each new section should fail gracefully if its data is unavailable:

- If trajectory can't be computed (no historical prices, etc.), omit the section silently
- If runway can't be computed (no expense data), omit the section silently
- If a single warning condition can't be checked (table missing, query fails), skip that warning but emit the others
- Never raise an exception that prevents the rest of the summary from being returned

The summary is *always* better than no summary; partial output is acceptable.

### Backward compatibility

This is additive — no existing fields are removed or renamed. Existing consumers continue to see the data they were seeing, plus new sections.

If a `compact=true` flag exists or is desired, it could omit the new sections to preserve token-minimal output for callers that need it. Not required for this change but worth considering.

### Testing

Verify against:
- Empty book (newly created, no transactions) — most sections should omit gracefully
- Alex Chen-Morales testbook — full feature exercise
- Single-currency simple book — no multi-currency complications
- Multi-currency book with stale prices — warnings should fire correctly
- Book with active budget — budget section appears
- Book with no budget — budget section absent
- Book with intentional imbalance (testing only) — imbalance warning fires

The "absence is the signal" pattern should be tested specifically: confirm that the Warnings section is *omitted* (not "Warnings: none") when there are no warnings.
