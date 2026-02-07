# Multi-Currency Support — Live Test Plan

**Branch:** `feat/multi-currency`  
**Prerequisites:** A GnuCash book in SQLite format with USD as default currency, connected via Claude Desktop or Claude Code.

---

## Test 1: Verify list_commodities tool works

**Prompt:**
```
List all commodities in my book.
```

**Expected:** Returns JSON with `default_currency` (should be your book's default) and `commodities` grouped by `CURRENCY` namespace. At minimum, your default currency should appear.

---

## Test 2: Create a EUR account

**Prompt:**
```
Create a new bank account called "Euro Savings" under Assets with EUR as the commodity.
```

**Expected:** Account created successfully. Then verify:

```
What commodity does the Euro Savings account use?
```

Should show `"commodity": "EUR"`. Also confirm EUR now appears in `list_commodities`.

---

## Test 3: Create account with default currency (backward compat)

**Prompt:**
```
Create a new expense account called "Coffee" under Expenses.
```

**Expected:** Account created with your book's default currency (no `commodity` parameter needed). Existing behavior unchanged.

---

## Test 4: Reject invalid currency

**Prompt:**
```
Create a bank account called "Bad Account" under Assets with commodity "FAKECURRENCY".
```

**Expected:** Error response mentioning "Invalid currency code".

---

## Test 5: Same-currency transaction (backward compat)

**Prompt:**
```
Create a transaction: $25 from Assets:Checking to Expenses:Coffee, dated today.
```

**Expected:** Transaction created normally. No `currency` or `quantity` needed. This is the existing workflow — should work identically to before.

---

## Test 6: Cross-currency transaction

**Prompt:**
```
I spent €50 on my Euro Savings account for groceries. The USD equivalent is $55. Create this as a USD transaction dated 2024-03-15.
```

**What Claude should do:** Call `create_transaction` with:
- `currency: "USD"`
- Expenses split: `amount: "55.00"` (USD account, no quantity needed)
- Euro Savings split: `amount: "-55.00"`, `quantity: "-50.00"` (EUR account needs quantity)

**Expected:** Transaction created. Then verify:

```
Show me the transaction you just created.
```

The EUR split should show `value: "-55"` and `quantity: "-50"`.

---

## Test 7: Cross-currency missing quantity (error path)

**Prompt:**
```
Create a USD transaction: $50 from Assets:Euro Savings to Expenses:Coffee.
```

**Expected:** Error mentioning that the Euro Savings split "requires 'quantity'" because its commodity (EUR) differs from the transaction currency (USD). Claude should then ask for the EUR amount and retry with `quantity` included.

---

## Test 8: Sign mismatch (error path)

Ask Claude to create a transaction where you deliberately provide a quantity with the wrong sign. This is harder to trigger conversationally since Claude will likely get the signs right, but you can try:

```
Create a USD transaction for $50 groceries paid from Euro Savings. Use quantity "45.00" for the Euro Savings split (not negative).
```

**Expected:** Error about quantity and value needing the same sign.

---

## Test 9: Verify balances after cross-currency transactions

**Prompt:**
```
What's the balance of Assets:Euro Savings?
```

**Expected:** Balance reflects the `quantity` values (EUR amounts), not the `value` fields (USD amounts). This is how GnuCash works — account balances are in the account's commodity.

> ⚠️ **Known Issue:** The current `get_balance()` implementation uses `split.value` instead of `split.quantity`. This will return incorrect results for multi-currency accounts. The fix is to change line ~267 in book.py from `balance += split.value` to `balance += split.quantity`.

---

## Test 10: Multi-step workflow

**Prompt:**
```
I'm going on a trip to London. Set me up with a GBP credit card account under Liabilities, then record a £30 dinner that cost me $38 in USD.
```

**Expected:** Claude should:
1. Create `Liabilities:GBP Credit Card` with `commodity: "GBP"`
2. Create a USD transaction with a GBP split including `quantity`
3. GBP should auto-create as a commodity in the book

---

## What to Watch For

- **Backward compatibility:** Tests 3 and 5 should work exactly like before — no regressions
- **Claude's reasoning:** Does Claude correctly identify when `quantity` is needed vs not?
- **Error recovery:** When Test 7 fails, does Claude understand the error and retry correctly?
- **Price records:** After Test 6, check in GnuCash GUI whether a Price entry was auto-created for the EUR/USD exchange rate (piecash does this automatically)

---

## Known Issues to Fix

1. **get_balance() uses value instead of quantity** — Will return wrong balance for multi-currency accounts. Change `split.value` to `split.quantity` in `get_balance()`.

2. **Reports may have similar issues** — `spending_by_category()`, `income_by_source()`, `balance_sheet()`, `net_worth()`, and `cash_flow()` all iterate over splits. Audit each for `value` vs `quantity` usage. For single-currency books this doesn't matter, but for multi-currency it's critical.
