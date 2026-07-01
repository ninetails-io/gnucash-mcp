# Pagination: offset + limit + result count indicator

## Implementation status — shipped

Implemented on `feat/pagination`. The chokepoint is
`_paginate(items, offset, limit, default=50, max_cap=250,
entity_name, date_key)` in `gnucash_mcp/_format.py`, which retired
both prior truncation mechanisms (`_apply_limit` and the bespoke
`_truncation_notice`). Dated callers pass a `date_key` callable
(`row -> date/datetime/ISO-string`); `_paginate` computes the range
itself — over the **current page** for paged calls, over the full set
for count-only/overshoot. The `_iso_date` normalizer lives in
`_format.py` (layer-neutral, so the book layer doesn't reach upward).

Scope landed broader than the named subset below: **all 20
list-returning tools** carry the indicator + `offset`, not just the
"must / should have" lists. Dated tools (`list_transactions`,
`search_transactions`, `get_unreconciled_splits`, `list_invoices`,
`get_outstanding_invoices`, `get_prices`, `get_upcoming_transactions`,
`list_backups`) render the `(earliest to latest)` range; undated ones
omit the parens. Verbose mode returns a uniform envelope
`{<entity>, showing, total, offset, count}` everywhere.

Two tools deviate by necessity, both documented in code:
`list_backups` paginates at the tool layer (its book method stays a
full list for internal callers), and `get_audit_log` uses a
recency-anchored window (offset pages *backward* from the newest
entry) since a log is read newest-first. The contract is locked by
`tests/test_tools.py::TestPaginationCoverage`.

## Problem

List tools (`list_transactions`, `search_transactions`, `get_unreconciled_splits`, etc.) silently truncate results at a default limit. The LLM has no way to know it's looking at a partial view. This caused a real miss: 93 of 109 checking transactions were returned, the LLM assumed completeness, and 16 missing entries went undetected for an entire reconciliation session.

## Solution

Add `offset` parameter and a "Showing X-Y of Z" indicator to every tool that returns a list of results.

## Indicator format

First line of the response, before any TSV data:

```
Showing 1-50 of 109 transactions (2026-05-01 to 2026-06-12)
```

Components:
- `1-50`: the row range in this response (1-indexed for readability)
- `of 109`: total matching rows (the number the LLM needs to decide whether to paginate)
- `transactions`: the entity type (transactions, splits, accounts, etc.)
- `(2026-05-01 to 2026-06-12)`: date range of **this page** — on a
  chronological list it's the actionable navigation signal ("rows
  251-500 cover Oct–Dec; I need May, jump ahead"). The exception is
  count-only mode (`limit=0`), which has no page and so spans the FULL
  result set — the "what's the scope?" query. (Design refined from
  full-set-everywhere after the bookkeeper pointed out the page range
  is what enables offset arithmetic.)

When all results fit in one page:
```
Showing 1-23 of 23 transactions (2026-06-01 to 2026-06-18)
```

The LLM sees `1-23 of 23` and knows the set is complete. No pagination needed.

## Parameters

Add to every listing tool:

```python
offset: int = 0    # 0-indexed starting row. Default 0 (first page).
limit: int = 50    # Maximum rows to return. Existing parameter, already present on most tools.
```

Behavior:
- `offset=0, limit=50` → rows 0-49 (first page, default)
- `offset=50, limit=50` → rows 50-99 (second page)
- `offset=100, limit=50` → rows 100-108 (last page, partial)
- `offset=0, limit=200` → rows 0-108 (all results in one call if under limit)

The total count `Z` in the indicator is ALWAYS the full result count regardless of offset/limit. This is computed once via `COUNT(*)` or `len()` on the full query, before slicing.

## Tools to update

### Must have (high-volume listing tools)
1. **list_transactions** — the primary surface. Most likely to truncate silently on active accounts.
2. **search_transactions** — same result format, same truncation risk.
3. **get_unreconciled_splits** — used for reconciliation. Missing splits = missed reconciliation items.

### Should have (lower volume but same pattern)
4. **list_accounts** — usually small enough to fit, but placeholder-heavy books could exceed limits.
5. **list_invoices** — a business with 100+ invoices needs pagination.
6. **list_customers / list_vendors / list_employees** — unlikely to exceed limits but should be consistent.

### Don't need
- `get_book_summary`, `balance_sheet`, `net_worth` — single-result tools, not lists.
- `get_transaction`, `get_invoice`, `get_account` — single-entity lookups.
- `spending_by_category`, `income_by_source` — aggregated reports, not row listings.

## Implementation notes

### Count query
The total count must be computed from the SAME filter as the data query — same account, same date range, same search term. Don't count all transactions when the user asked for a specific account's transactions.

```python
# Pseudocode
total = query.count()  # or len(results) before slicing
page = results[offset:offset+limit]
indicator = f"Showing {offset+1}-{offset+len(page)} of {total} transactions"
```

### Performance
For SQLAlchemy/piecash: `query.count()` is a separate SQL query but it's fast (index scan). The cost is one extra query per call, not per row. On a 50,000-transaction book, this adds ~1ms.

For very large result sets, the offset-based approach may slow down at high offsets (SQL has to skip N rows). This is acceptable for MCP usage — an LLM isn't going to page through 10,000 transactions one page at a time. If it needs that many, it should use a tighter date range or search filter.

### Existing limit parameter
Most listing tools already have a `limit` parameter. Add `offset` alongside it. Default offset to 0. Default limit stays at its current value (usually 50).

### Backward compatibility
The indicator line is PREPENDED to the response. Existing consumers that parse TSV starting from line 1 (the header) would need to adjust to start from line 2. Since the consumers are LLMs (not rigid parsers), this is a non-issue — the LLM reads the indicator naturally.

If strict backward compatibility is needed, the indicator could go AFTER the TSV data as a footer. But header position is better — the LLM reads it first and knows whether to ask for more before processing the rows.

## Edge cases

### Zero results
```
Showing 0 of 0 transactions
```
No TSV data follows. The LLM knows the query returned nothing.

### Offset beyond total
```
Showing 0 of 109 transactions (offset 200 exceeds result count)
```
Empty page with the total count visible. The LLM knows it overshot and can adjust.

### limit=0 (count-only mode)
Optional but useful: if `limit=0`, return ONLY the indicator line with no data. Lets the LLM check how many results exist before deciding how to page.

```
list_transactions(account="Checking", start_date="2026-01-01", limit=0)
→ "Showing 0 of 245 transactions (2026-01-01 to 2026-06-18)"
```

One call to know the size, then a targeted `limit=245` call to get everything. Cheaper than discovering truncation after processing 50 rows.

## Tests
1. Default call returns indicator showing `1-50 of N` when N > 50
2. `offset=50` returns `51-100 of N` with correct rows
3. Last page shows partial range: `101-109 of 109`
4. Full result set (N ≤ limit) shows `1-N of N`
5. Zero results shows `0 of 0`
6. Offset beyond total shows error/empty with count
7. `limit=0` returns count-only indicator (if implemented)
8. Total count matches actual query filter (not all-transactions count)
9. Date range in indicator reflects full result set, not current page
10. Indicator is first line, TSV header is second line, data follows
