# I/O efficiency review — v1.3 pre-release

Captured from deep-read agent run on 2026-06-02.

## Summary

Compact-by-default discipline is nearly universal. One systematic
deviation: `tools/business.py` uses `json.dumps(..., indent=2)`
instead of `_json(...)` in seven places, adding 40-60% whitespace and
skipping `_strip_noise`. Plus a small extra-query echo in
`get_balance`, and a token-heavy `create_transaction` docstring.

## High-impact findings

### 1. `tools/business.py` uses `json.dumps(..., indent=2)` instead of `_json(...)`
Sites: lines **59, 122, 186, 345, 409, 970, 1316** — `list_customers`,
`list_vendors`, `list_employees`, `list_invoices`, `list_bills`,
`list_credit_notes`, `get_credit_note`.

```python
# Current
if verbose:
    return json.dumps(result, indent=2)
# Should be
if verbose:
    return _json(result)
```

**Impact:** 40-60% byte overhead from indentation/newlines + skips the
`_strip_noise()` pass that removes `None`/empty values. Inconsistent
with every other module. Seven mechanical fixes; no test churn.

## Medium-priority findings

### 2. `get_balance` does an extra fetch to echo the canonical name
`tools/core.py:89-107`. After computing the balance, fetches the full
account record to echo `fullname` back in the response.

```python
account_dict = book.get_account(account_name)  # extra query
canonical_name = account_dict["fullname"]
result = {"account": canonical_name, "balance": str(balance), "as_of_date": resolved_date}
```

The echo is a design call — useful for LLM confirmation when the
caller passed a `%short` guid. Trade-off, not a clear win to remove.
Could drop the second query and just return the originally-supplied
ref + balance + as_of.

### 3. `create_transaction` docstring is token-heavy
`tools/core.py:177-197` — ~450 tokens of duplicate-detection signal
spec, emitted on every tool discovery. The detail is valuable, but
moving the signal-code table to project docs and leaving a pointer
saves ~300 tokens per discovery.

## Low-priority polish

### 4. `balance_sheet` has no compact mode
`tools/reporting.py`. Always returns structured JSON. Other reports
offer a compact text rendering. Defensible — balance sheet is
inherently structured — but could ship a TSV-style compact form.

### 5. Lint rule for `json.dumps` in `tools/*.py`
After fixing #1, a one-line grep gate in tests would prevent
regressions.

## What looks right

- `_format.py` (194 LOC) is focused and clean. `_format_number`
  precision rules correct (2 decimal for currency, 4 for shares,
  6 for crypto). `_apply_limit` emits proper truncation notices.
- Short-GUID contracts locked by `TestWriteResponseShape` and
  `TestShortGuidRoundTripClosure`.
- Compact-by-default pattern universal across list/report tools.
- `_strip_noise()` applied consistently via `_json()` (~95% of returns).
- Error responses uniform via `safe_tool` — `{error, error_type,
  suggestion}`. `write_verification_failed` bucket separated.
- Pydantic `extra="forbid"` (per predecessor letters) still in place;
  `SplitInput` uses `extra="ignore"` defensively (line 83-86 of
  `_helpers.py`).

## Recommendations

- **Pre-release:** Fix #1 (seven `json.dumps` → `_json` rewrites).
- **Nice-to-have:** Lint rule (#5); shorter `create_transaction` docstring (#3).
- **Defer:** `get_balance` echo question (#2); `balance_sheet` compact mode (#4).
