# Feature Request: create_account Tool

## Summary

Add a `create_account` tool to gnucash-mcp that allows creating new accounts in the GnuCash chart of accounts.

## Why

When entering transactions, new expense/income categories are frequently needed. Currently, the user must open GnuCash manually to create accounts. This breaks the workflow.

## piecash API

piecash fully supports account creation:

```python
from piecash import Account

# Get the parent account object
parent = book.accounts.get(fullname="Expenses:Online Services")

# Create the new account
new_account = Account(
    name="AI Subscriptions",
    type="EXPENSE",
    parent=parent,
    commodity=book.default_currency,
    description="Claude, ChatGPT, Midjourney, etc.",
    placeholder=False
)

book.save()
```

## Tool Specification

### Name
`create_account`

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| name | string | yes | Account name (e.g., "AI Subscriptions") |
| account_type | string | yes | GnuCash account type (see valid types below) |
| parent | string | yes | Full path of parent account (e.g., "Expenses:Online Services") |
| description | string | no | Optional description |
| placeholder | boolean | no | If true, account is container-only, cannot hold transactions. Default: false |

### Valid Account Types

- ASSET
- BANK
- CASH
- CREDIT
- EQUITY
- EXPENSE
- INCOME
- LIABILITY
- MUTUAL
- STOCK

### Return Value

```json
{
  "guid": "generated-guid-here",
  "fullname": "Expenses:Online Services:AI Subscriptions",
  "status": "created"
}
```

### Error Cases

1. **Parent not found**: Return error if parent path doesn't exist
2. **Duplicate name**: Return error if account with same name already exists under parent
3. **Invalid type**: Return error if account_type not in valid list
4. **Type mismatch**: Warn if type doesn't match parent's hierarchy (e.g., INCOME under Expenses)

## Example Usage

```python
# Create a sub-account for AI subscriptions
create_account(
    name="AI Subscriptions",
    account_type="EXPENSE",
    parent="Expenses:Online Services",
    description="Claude, ChatGPT, etc."
)

# Create a placeholder category with sub-accounts
create_account(
    name="Cannabis",
    account_type="EXPENSE", 
    parent="Expenses",
    description="Medical cannabis",
    placeholder=True
)

create_account(
    name="Flower",
    account_type="EXPENSE",
    parent="Expenses:Cannabis"
)

create_account(
    name="Edibles", 
    account_type="EXPENSE",
    parent="Expenses:Cannabis"
)
```

## Implementation Notes

1. Use `book.accounts.get(fullname=parent)` to find parent account
2. Use `book.default_currency` for the commodity (USD in this book)
3. The `commodity_scu` (smallest currency unit) can use default
4. Don't forget `book.save()` after creating

## Testing

After implementation, verify:

```
create_account(name="Test Category", account_type="EXPENSE", parent="Expenses")
list_accounts()  # Should show new account
# Then delete via GnuCash UI to clean up
```

---

*Spec written by Abe Raham, The Accountant*
*For: Claude Code*
*Date: 2026-01-30*
