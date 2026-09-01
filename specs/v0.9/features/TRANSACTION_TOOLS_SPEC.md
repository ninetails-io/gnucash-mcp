# Transaction Management Tools Specification

## Overview

Two new tools to complement `create_transaction`: the ability to delete transactions and update existing transactions. Both operations are supported by piecash.

---

## delete_transaction

### Purpose
Remove a transaction from the book by GUID.

### piecash API
```python
from piecash import open_book

with open_book(book_path, readonly=False) as book:
    transaction = book.transactions.get(guid=guid)
    book.delete(transaction)
    # or: book.session.delete(transaction)
    book.save()
```

### Tool Parameters
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| guid | string | yes | Transaction GUID (32-character hex string) |

### Return Format
```json
{
  "guid": "abc123...",
  "description": "The deleted transaction description",
  "status": "deleted"
}
```

### Error Cases
- Transaction not found: `{"error": "Transaction not found", "guid": "..."}`
- Book locked: `{"error": "Could not open book for writing"}`

### Implementation Notes
- Deleting a transaction automatically removes all associated splits
- No confirmation prompt — the MCP client (Claude) is responsible for confirming with user
- Consider returning the full transaction data before deletion for logging/undo purposes

---

## update_transaction

### Purpose
Modify an existing transaction's properties and/or split values.

### piecash API
```python
from piecash import open_book
from datetime import date

with open_book(book_path, readonly=False) as book:
    transaction = book.transactions.get(guid=guid)
    
    # Update basic properties
    transaction.description = "New description"
    transaction.post_date = date(2026, 1, 15)
    
    # Update split values
    for split in transaction.splits:
        if split.account.fullname == "Assets:Current Assets:Checking Account":
            split.value = Decimal("-100.00")
            split.quantity = Decimal("-100.00")
    
    book.save()
```

### Tool Parameters
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| guid | string | yes | Transaction GUID to update |
| description | string | no | New transaction description |
| transaction_date | string | no | New date (ISO format YYYY-MM-DD) |
| splits | array | no | Array of split updates (see below) |

### Split Update Format
```json
{
  "splits": [
    {
      "account": "Assets:Current Assets:Checking Account",
      "amount": "-150.00"
    },
    {
      "account": "Expenses:Groceries", 
      "amount": "150.00"
    }
  ]
}
```

When splits are provided:
- Match existing splits by account name
- Update the value/quantity to the new amount
- Splits must still balance to zero
- Cannot add or remove splits (use delete + create for restructuring)

### Return Format
```json
{
  "guid": "abc123...",
  "description": "Updated description",
  "date": "2026-01-15",
  "splits": [
    {"account": "...", "value": "..."},
    {"account": "...", "value": "..."}
  ],
  "status": "updated"
}
```

### Error Cases
- Transaction not found: `{"error": "Transaction not found", "guid": "..."}`
- Splits don't balance: `{"error": "Splits must balance to zero", "sum": "..."}`
- Account not found: `{"error": "Account not found", "account": "..."}`
- Book locked: `{"error": "Could not open book for writing"}`

### Example Usage

**Change description only:**
```python
update_transaction(
    guid="abc123...",
    description="Corrected: Safeway Groceries"
)
```

**Change date only:**
```python
update_transaction(
    guid="abc123...",
    transaction_date="2026-01-14"
)
```

**Change amount (correcting a typo):**
```python
update_transaction(
    guid="abc123...",
    splits=[
        {"account": "Assets:Current Assets:Checking Account", "amount": "-42.50"},
        {"account": "Expenses:Groceries", "amount": "42.50"}
    ]
)
```

**Change everything:**
```python
update_transaction(
    guid="abc123...",
    description="Fred Meyer Groceries",
    transaction_date="2026-01-14",
    splits=[
        {"account": "Assets:Current Assets:Checking Account", "amount": "-42.50"},
        {"account": "Expenses:Groceries", "amount": "42.50"}
    ]
)
```

---

## Implementation Priority

1. **delete_transaction** — simpler, immediately useful for cleanup
2. **update_transaction** — more complex but covers common corrections

---

## Notes

- Both tools require the book to be closed in GnuCash before use
- piecash handles backup automatically on save
- The "simple update" approach (no split add/remove) covers most real-world corrections
- For complex restructuring (changing number of splits or accounts), delete and recreate

---

*Spec written by the bookkeeper*
*January 30, 2026*
