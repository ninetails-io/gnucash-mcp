# Transaction Creation Pipeline Spec

**Status:** Proposed  
**Date:** February 10, 2026  
**Author:** Abe (Claude instance), with Steve

---

## Overview

The current `create_transaction` method validates split balancing and account existence, then writes immediately. This spec adds duplicate detection, dry run mode, and additional safety checks to the creation pipeline.

The design principle: the MCP server is a clean, reliable write layer. The LLM handles format parsing, categorization, and intent. The server handles validation, safety, and persistence. Don't build the brain into the server. Build the safety net.

---

## Current Validation (Already Implemented)

- Minimum 2 splits
- Splits balance to zero
- All referenced accounts exist
- Cross-currency splits require `quantity` field
- Quantity and value signs must agree

---

## Proposed Pipeline

Every call to `create_transaction` runs through four stages:

### Stage 1: Validate

All current validation, plus:

**Placeholder account check:**  
If any split references an account with `placeholder=True`, reject the transaction with a clear error message naming the placeholder account and suggesting its children.

This is a one-line check against `account.placeholder` on each split's target account. Placeholder accounts exist specifically to force transactions into subcategories.

**Date sanity warnings:**  
- Future dates: warn but allow (scheduled payments are legitimate)
- Dates more than 365 days in the past: warn but allow (back-dating happens)
- These are warnings in the return payload, not hard rejections

**Account type coherence warnings:**  
Flag unusual sign/type combinations:
- Negative value to an Expense account (unusual but valid — expense reversals)
- Positive value to an Income account (unusual but valid — refunds)
- Any value to a placeholder account (hard reject, see above)

These warnings appear in the response alongside the transaction. They don't block creation. They give the LLM or human a chance to catch mistakes before the next transaction.

### Stage 2: Duplicate Check

Search existing transactions for potential matches using three signals:

1. **Description match:** Case-insensitive substring match on vendor/description. "Fred Meyer" matches "Fred Meyer Issaquah" and "FRED MEYER #1234".

2. **Amount match:** Any split with an absolute value within ±$1.00 of any proposed split's absolute value. This catches rounding differences and slight price variations.

3. **Date match:** Transaction date within ±2 days of the proposed date. Covers posting lag and date-entry uncertainty.

**Scoring:**
- 3/3 signals match → HIGH confidence duplicate (almost certainly the same transaction)
- 2/3 signals match → MEDIUM confidence (likely duplicate, needs human review)
- 1/3 signals match → LOW confidence (probably not a duplicate, include for reference)

**Return format:**
```json
{
  "duplicates": [
    {
      "confidence": "HIGH",
      "existing_transaction": { ...transaction dict... },
      "match_signals": {
        "description": true,
        "amount": true,
        "date": true
      }
    }
  ]
}
```

**Behavior:**
- In normal mode: if any HIGH confidence match exists, reject the transaction and return the candidates. The caller must either confirm with `force_create=true` or abandon.
- In dry run mode: return candidates without blocking.
- `check_duplicates=false` parameter skips this stage entirely (for batch imports where dedup is handled upstream).

### Stage 3: Dry Run

When `dry_run=true`:

- Run Stage 1 (validate) and Stage 2 (duplicate check)
- Build the complete transaction dict as it would be written
- Return the proposed transaction, any warnings, and any duplicate candidates
- Do NOT call `book.save()`
- Do NOT open the book in write mode

**Return format:**
```json
{
  "dry_run": true,
  "proposed_transaction": {
    "description": "Fred Meyer Issaquah",
    "date": "2026-02-09",
    "currency": "USD",
    "splits": [
      {"account": "Assets:Current Assets:Checking Account", "amount": "-41.06"},
      {"account": "Expenses:Groceries:Pantry", "amount": "5.00"},
      {"account": "Expenses:Groceries:Bakery & Baking", "amount": "35.90"},
      {"account": "Expenses:Miscellaneous", "amount": "0.16"}
    ]
  },
  "warnings": [],
  "duplicates": []
}
```

### Stage 4: Write

If validation passes, duplicates are clear (or forced), and `dry_run` is not set:

- Open book in write mode
- Create the transaction (existing logic)
- `book.save()`
- Return the transaction GUID and any warnings from Stage 1

---

## New Parameters on `create_transaction`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `dry_run` | bool | `false` | Run validation and dupe check, return proposal without writing |
| `check_duplicates` | bool | `true` | Run duplicate detection against existing transactions |
| `force_create` | bool | `false` | Create even if HIGH confidence duplicates are found |

---

## Reconciled Split Protection

Separate from the creation pipeline, but related safety work:

**`delete_transaction`** and **`update_transaction`** should check whether any splits in the target transaction have `reconcile_state == 'y'` (reconciled).

- If reconciled splits exist: reject by default with a message explaining that modifying reconciled transactions will break reconciliation.
- Add a `force=true` parameter to override.
- The audit log entry should note that reconciled splits were affected.

This prevents accidentally undoing reconciliation work, which is expensive to redo.

---

## Implementation Notes

**Placeholder check:** `account.placeholder` is a boolean on the piecash Account object. Check in the existing account validation loop.

**Duplicate search:** Can reuse existing `search_transactions` and `list_transactions` logic internally. The description search is already case-insensitive substring. Amount search already supports ranges. Date filtering already exists. This is orchestration of existing capabilities, not new query logic.

**Dry run:** The simplest implementation opens the book readonly, runs validation and dupe check, and constructs the return dict. No write mode needed. If account validation requires the book (it does), this already opens readonly.

**Performance:** Duplicate checking adds one full transaction scan per creation. For books with thousands of transactions, this could be slow. Consider limiting the dupe search window to ±30 days of the proposed date unless the caller specifies otherwise. Most duplicates are recent.

---

## What This Spec Does NOT Cover

- Batch transaction creation (future work, would use `check_duplicates=false`)
- Fuzzy vendor name matching beyond substring (e.g., "FREDMEYER" vs "Fred Meyer")
- Transaction templates or recurring transaction detection

These are out of scope for this change.

**A note on import pipelines:** There is no OFX/CSV/QIF import tool on the roadmap. The LLM reads these formats natively — hand it an OFX file, a CSV, a PDF bank statement, a pasted screenshot, or a spoken description, and it understands all of them. The server doesn't need parsers for formats the agent already comprehends. The server's job is validation and persistence, not parsing.
