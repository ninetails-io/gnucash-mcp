# Account Slots Spec

**Status:** Proposed  
**Date:** February 10, 2026  
**Author:** Abe (Claude instance), with Steve

---

## Overview

GnuCash stores arbitrary key-value metadata on objects (accounts, transactions, etc.) using a "slots" system. Piecash exposes these as dictionaries on the object. This spec adds two tools to read and write slots on accounts, enabling storage of metadata like APR, credit limit, and other account-specific data that GnuCash doesn't have native fields for.

---

## Motivation

Credit card accounts need APR tracking for debt payoff strategy (avalanche vs. snowball). GnuCash has no native APR field. Slots are the GnuCash-native way to attach custom metadata to accounts without breaking the schema.

Other use cases:
- Credit limits per card
- Reward rates or categories
- Minimum payment amounts
- Account nicknames or shorthand
- Any per-account metadata the LLM or user wants to persist in the book

---

## New Tools

### `get_account_slots`

Read all slots (or a specific slot) from an account.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `account` | string | yes | Full account path (e.g., "Liabilities:Credit Cards:Capital One") |
| `key` | string | no | Specific slot key to retrieve. If omitted, return all slots. |

**Returns:**
```json
{
  "account": "Liabilities:Credit Cards:Capital One",
  "slots": {
    "apr": "24.99",
    "credit_limit": "15000",
    "reward_rate": "1.5% cash back"
  }
}
```

If `key` is specified and not found, return the account with an empty slots object.

**Errors:**
- Account not found → ValueError

### `set_account_slot`

Set a single key-value pair on an account.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `account` | string | yes | Full account path |
| `key` | string | yes | Slot key (e.g., "apr", "credit_limit") |
| `value` | string | yes | Slot value. Always stored as string. |

**Returns:**
```json
{
  "account": "Liabilities:Credit Cards:Capital One",
  "key": "apr",
  "value": "24.99",
  "status": "created"
}
```

Status is `"created"` for new keys, `"updated"` for existing keys that were overwritten.

**Errors:**
- Account not found → ValueError

### `delete_account_slot`

Remove a slot from an account.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `account` | string | yes | Full account path |
| `key` | string | yes | Slot key to remove |

**Returns:**
```json
{
  "account": "Liabilities:Credit Cards:Capital One",
  "key": "apr",
  "status": "deleted"
}
```

**Errors:**
- Account not found → ValueError
- Key not found → ValueError

---

## Implementation Notes

Piecash exposes slots as a dict-like interface on Account objects. Reading is straightforward:

```python
account = book.accounts.get(fullname="Liabilities:Credit Cards:Capital One")
slots = {key: str(val) for key, val in account.slots.items()}
```

Writing:

```python
account["apr"] = "24.99"
book.save()
```

Slot values in GnuCash can be strings, integers, floats, dates, or nested frames. For simplicity, this spec stores and returns everything as strings. The LLM can parse "24.99" into a number when it needs to do math. Keeping the interface string-only avoids type negotiation at the MCP layer.

---

## Scope

This spec covers account slots only. GnuCash also supports slots on transactions, splits, and other objects. Those can be added later if needed, using the same pattern.

---

## Audit Log

All `set_account_slot` and `delete_account_slot` calls should be logged:

```
01:14:19  SET ACCOUNT SLOT  account:Liabilities:Credit Cards:Capital One
          key: "apr"  value: "24.99"  (created)
```

Read operations (`get_account_slots`) are logged as reads per existing audit conventions.
