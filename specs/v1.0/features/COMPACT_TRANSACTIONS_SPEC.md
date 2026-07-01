# Compact Transaction Listing Spec

**Status:** Proposed
**Date:** February 15, 2026
**Author:** Claude (Opus 4.6), with Steve

---

## Problem

`list_transactions` and `search_transactions` return full JSON — 32-char GUIDs, nested split dicts with 8 fields each, redundant keys. A typical 2-split transaction is ~400 bytes of JSON. A 50-transaction listing is ~20KB. The Claude accountant can't receive this much output without context pressure, and most of it is noise for a list view.

The `list_accounts` tool solved the same problem by adding a compact one-line-per-account format that cut output by ~90%. This spec does the same for transactions.

---

## Solution

### 1. Compact one-line format for `list_transactions`

Default output changes from verbose JSON to compact text, one line per transaction:

```
2026-02-15 a1b2c3d4  Safeway             Expenses:Groceries $47.50, Liabilities:Visa -$47.50
2026-02-14 e5f6a7b8  Monthly Rent        Expenses:Rent $1850.00, Assets:Checking -$1850.00
2026-02-13 c9d0e1f2  Spotify             Expenses:Subscriptions $10.99, Assets:Checking -$10.99
```

**Format:** `{date} {short_guid}  {description:<20} {split_summary}`

Where:
- **date**: ISO format `YYYY-MM-DD`
- **short_guid**: First 8 characters of the transaction GUID
- **description**: Transaction description, truncated to 20 chars
- **split_summary**: Compact split list: `Account $amount, Account $amount`
  - Account names are shortened: drop common prefixes when obvious (e.g., `Expenses:Groceries` not `Expenses:Food:Groceries:Weekly` — actually, keep full names for now, let the AI truncate if needed)
  - Amounts include sign
  - Multi-currency: show quantity if different from value (e.g., `EUR:Savings -$55.00/€-50.00`)

### 2. Verbose mode preserved

Add `verbose` parameter (like `list_accounts`):
- `verbose=false` (default): compact text format
- `verbose=true`: full JSON (current behavior, unchanged)

### 3. `search_transactions` gets the same treatment

Same compact default, same `verbose` flag.

### 4. Short GUIDs accepted everywhere

The 8-char GUIDs from compact output must work as input to all transaction/split tools:

- `get_transaction(guid="a1b2c3d4")` → resolves via `_resolve_guid`, returns full detail
- `update_transaction(guid="a1b2c3d4", ...)` → resolves, then updates
- `delete_transaction(guid="a1b2c3d4")` → resolves, then deletes
- `void_transaction(guid="a1b2c3d4", ...)` → resolves, then voids
- `unvoid_transaction(guid="a1b2c3d4")` → resolves, then unvoids
- `replace_splits(guid="a1b2c3d4", ...)` → resolves, then replaces

Split GUIDs (used in `set_reconcile_state`, `assign_split_to_lot`, etc.) don't need short-GUID support yet — they aren't shown in the compact listing.

---

## Implementation Plan

### Step 1: `_resolve_guid` (done)

Already implemented on `GnuCashBook`. Raw SQLite, 8-char minimum, 32-char fast path.

### Step 2: `_transaction_to_compact_line`

New helper function in `book.py`, parallel to `_account_to_compact_line`:

```python
def _transaction_to_compact_line(transaction: piecash.Transaction) -> str:
    """Convert a piecash Transaction to a compact one-line string.

    Format: "YYYY-MM-DD abcd1234  Description          Account $amount, Account -$amount"
    """
    date_str = transaction.post_date.isoformat()
    short_guid = transaction.guid[:8]
    desc = transaction.description[:20].ljust(20)

    parts = []
    for split in transaction.splits:
        account_name = split.account.fullname
        amount = split.quantity
        # Show quantity (account commodity), not value (transaction currency)
        parts.append(f"{account_name} {amount}")

    splits_str = ", ".join(parts)
    return f"{date_str} {short_guid}  {desc} {splits_str}"
```

### Step 3: Update `list_transactions` in `book.py`

Add `compact` parameter (default `True`):

```python
def list_transactions(
    self,
    account: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 50,
    compact: bool = True,
) -> list[dict] | str:
```

When `compact=True`, return `"\n".join(compact_lines)`.
When `compact=False`, return `[_transaction_to_dict(t) for t in filtered]` (current behavior).

### Step 4: Update `list_transactions` in `server.py`

Add `verbose` parameter, pass `compact=not verbose`. When verbose, return `json.dumps`. When compact, return the string directly.

### Step 5: Update `search_transactions` (same pattern)

Both `book.py` and `server.py` — add `compact`/`verbose` parameter.

### Step 6: Wire `_resolve_guid` into `_find_transaction`

Modify `_find_transaction` to resolve short GUIDs before searching:

```python
def _find_transaction(self, book: piecash.Book, guid: str) -> piecash.Transaction | None:
    full_guid = self._resolve_guid("transactions", guid)
    for transaction in book.transactions:
        if transaction.guid == full_guid:
            return transaction
    return None
```

This makes every tool that uses `_find_transaction` automatically accept short GUIDs, with no changes to those tools.

### Step 7: Update tool docstrings

Update `get_transaction`, `update_transaction`, `delete_transaction`, `void_transaction`, `unvoid_transaction`, `replace_splits` docstrings to note that partial GUIDs (8+ chars) are accepted.

### Step 8: Tests

1. **test_resolve_guid_full** — 32-char passes through
2. **test_resolve_guid_partial** — 8-char prefix resolves
3. **test_resolve_guid_too_short** — 7-char raises ValueError
4. **test_resolve_guid_no_match** — bad prefix raises ValueError
5. **test_resolve_guid_ambiguous** — contrived collision raises ValueError
6. **test_list_transactions_compact** — returns text, not JSON
7. **test_list_transactions_verbose** — returns full JSON (existing behavior)
8. **test_compact_line_format** — verify date, guid, description, splits in output
9. **test_search_transactions_compact** — same compact format
10. **test_get_transaction_short_guid** — 8-char guid works
11. **test_update_transaction_short_guid** — 8-char guid works

---

## Token Savings Estimate

Current verbose JSON for one 2-split transaction (~400 bytes):
```json
{
  "guid": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
  "date": "2026-02-15",
  "description": "Safeway",
  "currency": "USD",
  "splits": [
    {
      "guid": "11223344556677889900aabbccddeeff",
      "account": "Expenses:Groceries",
      "value": "47.50",
      "quantity": "47.50",
      "memo": "",
      "reconcile_state": "n",
      "reconcile_date": null,
      "lot_guid": null
    },
    {
      "guid": "ffeeddccbbaa00998877665544332211",
      "account": "Liabilities:Credit Card:Visa",
      "value": "-47.50",
      "quantity": "-47.50",
      "memo": "",
      "reconcile_state": "n",
      "reconcile_date": null,
      "lot_guid": null
    }
  ]
}
```

Compact line (~90 bytes):
```
2026-02-15 a1b2c3d4  Safeway              Expenses:Groceries 47.50, Liabilities:Credit Card:Visa -47.50
```

**~75-80% reduction per transaction. For 50 transactions: ~20KB → ~4.5KB.**

The full detail is still available via `get_transaction` when the AI needs split GUIDs, reconcile state, lot assignments, etc. The compact listing is for scanning and selecting.

---

## What This Spec Does NOT Cover

- Compact format for `get_unreconciled_splits` (could benefit, but different enough to be separate)
- Split-level short GUIDs (not shown in compact output, not needed yet)
- Compact format for `get_upcoming_transactions` or `get_budget_report` (future work)
