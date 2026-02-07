# Multi-Currency Support Specification
## GnuCash MCP Server Enhancement

**Author:** Abe Raham Jr.  
**Date:** 2026-02-06  
**Status:** Draft  
**Based on:** piecash 1.2.0 documentation, existing gnucash-mcp codebase

---

## Executive Summary

The GnuCash MCP server currently hardcodes `book.default_currency` for all new transactions and accounts. This prevents users with multi-currency books (e.g., EUR income, USD expenses, GBP credit cards) from creating transactions that cross currency boundaries.

This spec defines the minimal changes needed to support multi-currency transaction and account creation while maintaining backward compatibility.

---

## Background: How GnuCash Handles Multi-Currency

### Core Concepts

1. **Commodity**: Any tradeable unit — currencies (USD, EUR), stocks (AAPL), mutual funds, cryptocurrencies. Currencies have `namespace="CURRENCY"`.

2. **Account.commodity**: Each account tracks one commodity. A EUR checking account has `commodity=EUR`.

3. **Transaction.currency**: The currency in which a transaction balances. All split `value` fields are denominated in this currency. The sum of all `value` fields must equal zero.

4. **Split.value**: The amount in the *transaction's currency*. Used for balancing.

5. **Split.quantity**: The amount in the *account's commodity*. 
   - If `account.commodity == transaction.currency`: quantity equals value (automatically set by piecash)
   - If `account.commodity != transaction.currency`: quantity must be provided separately

### Example: Cross-Currency Transaction

A EUR book with a USD credit card account:

```python
# Transaction: Paid $30 USD for dinner, worth €27.50 at today's rate
Transaction(
    currency=EUR,  # Transaction balances in EUR
    description="Dinner in New York",
    splits=[
        Split(account="Expenses:Dining", value=27.50),  # +€27.50 expense (EUR account)
        Split(account="Liabilities:USD Credit Card", value=-27.50, quantity=-30.00)  # -€27.50 value = -$30 USD
    ]
)
```

The `value` fields balance (27.50 + -27.50 = 0), but the USD account records 30 USD in `quantity`.

### Validation Rules (from piecash source)

1. If `transaction.currency == account.commodity`:
   - `quantity` must equal `value` (piecash enforces this)
   
2. If `transaction.currency != account.commodity`:
   - `quantity` is **required** (cannot be None)
   - `quantity * value >= 0` (same sign, or one is zero)

3. Piecash automatically creates a `Price` record when value ≠ quantity, capturing the implied exchange rate.

---

## Current Implementation

### create_transaction (book.py lines 263-318)

```python
def create_transaction(
    self,
    description: str,
    splits: list[dict],
    trans_date: date | None = None,
    memo: str | None = None,
) -> str:
    # ...
    with self.open(readonly=False) as book:
        piecash_splits = []
        for split in splits:
            account = self._find_account(book, split["account"])
            piecash_splits.append(
                piecash.Split(
                    account=account,
                    value=Decimal(split["amount"]),
                    memo=split.get("memo", ""),
                )
            )

        transaction = piecash.Transaction(
            currency=book.default_currency,  # <-- HARDCODED
            description=description,
            post_date=trans_date,
            splits=piecash_splits,
        )
```

**Problem**: No way to specify transaction currency or split quantities.

### create_account (book.py lines 428-488)

```python
def create_account(
    self,
    name: str,
    account_type: str,
    parent: str,
    description: str = "",
    placeholder: bool = False,
) -> dict:
    # ...
    new_account = piecash.Account(
        name=name,
        type=account_type.upper(),
        parent=parent_account,
        commodity=book.default_currency,  # <-- HARDCODED
        description=description,
        placeholder=placeholder,
    )
```

**Problem**: No way to create accounts in non-default currencies.

---

## Proposed Changes

### 1. New Helper Method: _find_or_create_commodity

Add to `GnuCashBook` class:

```python
def _find_commodity(self, book: piecash.Book, mnemonic: str, namespace: str = "CURRENCY") -> piecash.Commodity | None:
    """Find a commodity by mnemonic and namespace.
    
    Args:
        book: Open piecash book.
        mnemonic: ISO currency code (e.g., "USD", "EUR") or stock symbol.
        namespace: Commodity namespace. Default "CURRENCY" for currencies.
    
    Returns:
        Commodity if found, None otherwise.
    """
    try:
        return book.commodities.get(mnemonic=mnemonic, namespace=namespace)
    except KeyError:
        return None


def _get_or_create_currency(self, book: piecash.Book, mnemonic: str) -> piecash.Commodity:
    """Get an existing currency or create it from ISO code.
    
    Args:
        book: Open piecash book.
        mnemonic: ISO 4217 currency code (e.g., "USD", "EUR", "GBP").
    
    Returns:
        Commodity for the currency.
    
    Raises:
        ValueError: If mnemonic is not a valid ISO 4217 currency code.
    """
    # Try to find existing
    commodity = self._find_commodity(book, mnemonic, "CURRENCY")
    if commodity:
        return commodity
    
    # Create from ISO (piecash validates the code)
    from piecash.core.factories import create_currency_from_ISO
    try:
        return create_currency_from_ISO(mnemonic, book)
    except Exception as e:
        raise ValueError(f"Invalid currency code '{mnemonic}': {e}") from e
```

### 2. Enhanced create_account

**New signature:**

```python
def create_account(
    self,
    name: str,
    account_type: str,
    parent: str,
    description: str = "",
    placeholder: bool = False,
    commodity: str | None = None,  # NEW: ISO currency code or commodity mnemonic
    commodity_namespace: str = "CURRENCY",  # NEW: namespace for non-currency commodities
) -> dict:
```

**Implementation changes:**

```python
with self.open(readonly=False) as book:
    # Find parent account
    parent_account = self._find_account(book, parent)
    if not parent_account:
        raise ValueError(f"Parent account not found: {parent}")

    # Determine commodity
    if commodity is None:
        account_commodity = book.default_currency
    elif commodity_namespace == "CURRENCY":
        account_commodity = self._get_or_create_currency(book, commodity)
    else:
        account_commodity = self._find_commodity(book, commodity, commodity_namespace)
        if not account_commodity:
            raise ValueError(f"Commodity not found: {commodity_namespace}:{commodity}")

    # ... rest of validation ...

    new_account = piecash.Account(
        name=name,
        type=account_type.upper(),
        parent=parent_account,
        commodity=account_commodity,  # Now uses resolved commodity
        description=description,
        placeholder=placeholder,
    )
```

**Backward compatibility**: `commodity=None` uses `book.default_currency`, preserving existing behavior.

### 3. Enhanced create_transaction

**New signature:**

```python
def create_transaction(
    self,
    description: str,
    splits: list[dict],
    trans_date: date | None = None,
    memo: str | None = None,
    currency: str | None = None,  # NEW: ISO currency code for transaction
) -> str:
```

**New split dict schema:**

```python
# Current (still supported):
{"account": "Expenses:Dining", "amount": "27.50"}

# Enhanced (for cross-currency):
{"account": "Liabilities:USD Card", "amount": "-27.50", "quantity": "-30.00"}

# Full options:
{
    "account": str,      # Required: Full account path
    "amount": str,       # Required: Value in transaction currency (was "amount", keeps name for compatibility)
    "quantity": str,     # Optional: Amount in account's commodity (required if different from transaction currency)
    "memo": str          # Optional: Split memo
}
```

**Implementation changes:**

```python
def create_transaction(
    self,
    description: str,
    splits: list[dict],
    trans_date: date | None = None,
    memo: str | None = None,
    currency: str | None = None,
) -> str:
    if len(splits) < 2:
        raise ValueError("Transaction must have at least 2 splits")

    # Validate splits balance to zero (using "amount" as value)
    total = Decimal("0")
    for split in splits:
        total += Decimal(split["amount"])
    if total != Decimal("0"):
        raise ValueError(f"Splits do not balance: total is {total}")

    if trans_date is None:
        trans_date = date.today()

    with self.open(readonly=False) as book:
        # Determine transaction currency
        if currency is None:
            trans_currency = book.default_currency
        else:
            trans_currency = self._get_or_create_currency(book, currency)

        # Build splits with proper value/quantity handling
        piecash_splits = []
        for split in splits:
            account = self._find_account(book, split["account"])
            if not account:
                raise ValueError(f"Account not found: {split['account']}")

            value = Decimal(split["amount"])
            
            # Determine quantity
            if account.commodity == trans_currency:
                # Same currency: quantity equals value
                quantity = value
            elif "quantity" in split:
                # Cross-currency: use provided quantity
                quantity = Decimal(split["quantity"])
                # Validate same sign (or zero)
                if quantity * value < 0:
                    raise ValueError(
                        f"Split for '{split['account']}': quantity and value must have same sign "
                        f"(got value={value}, quantity={quantity})"
                    )
            else:
                # Cross-currency but no quantity provided
                raise ValueError(
                    f"Split for '{split['account']}' requires 'quantity' because account commodity "
                    f"({account.commodity.mnemonic}) differs from transaction currency ({trans_currency.mnemonic})"
                )

            piecash_splits.append(
                piecash.Split(
                    account=account,
                    value=value,
                    quantity=quantity,
                    memo=split.get("memo", ""),
                )
            )

        transaction = piecash.Transaction(
            currency=trans_currency,
            description=description,
            post_date=trans_date,
            splits=piecash_splits,
        )

        book.save()
        return transaction.guid
```

**Backward compatibility**: 
- `currency=None` uses `book.default_currency`
- Splits without `quantity` work if account commodity matches transaction currency
- Existing single-currency workflows unchanged

### 4. Server Layer Changes (server.py)

Update the tool definitions to expose new parameters:

```python
@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="transaction")
def create_transaction(
    description: str,
    splits: list[dict],
    transaction_date: str | None = None,
    currency: str | None = None,  # NEW
) -> str:
    """Create a new transaction with splits. Splits must balance to zero.

    Args:
        description: Transaction description
        splits: List of splits. Each split has:
            - 'account' (required): Full account path
            - 'amount' (required): Value in transaction currency
            - 'quantity' (optional): Amount in account's commodity. Required if 
              account commodity differs from transaction currency.
            - 'memo' (optional): Split memo
        transaction_date: Transaction date in ISO format (YYYY-MM-DD). Defaults to today.
        currency: ISO currency code for transaction (e.g., "USD", "EUR"). 
                  Defaults to book's default currency.
    """
    book = get_book()
    trans_date = date.fromisoformat(transaction_date) if transaction_date else None
    guid = book.create_transaction(
        description=description,
        splits=splits,
        trans_date=trans_date,
        currency=currency,
    )
    return json.dumps({"guid": guid, "status": "created"}, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="account")
def create_account(
    name: str,
    account_type: str,
    parent: str,
    description: str = "",
    placeholder: bool = False,
    commodity: str | None = None,  # NEW
) -> str:
    """Create a new account in the chart of accounts.

    Args:
        name: Account name (e.g., "AI Subscriptions")
        account_type: GnuCash account type (ASSET, BANK, CASH, CREDIT, EQUITY, EXPENSE, INCOME, LIABILITY, MUTUAL, STOCK)
        parent: Full path of parent account (e.g., "Expenses:Online Services")
        description: Optional description
        placeholder: If true, account is container-only. Default: false
        commodity: ISO currency code (e.g., "USD", "EUR"). Defaults to book's default currency.
    """
    book = get_book()
    result = book.create_account(
        name=name,
        account_type=account_type,
        parent=parent,
        description=description,
        placeholder=placeholder,
        commodity=commodity,
    )
    return json.dumps(result, indent=2)
```

### 5. New Tool: list_commodities

Add a read-only tool to discover available commodities:

```python
@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_commodities() -> str:
    """List all commodities (currencies, stocks, etc.) in the book.
    
    Returns commodities grouped by namespace with their mnemonic, fullname, and fraction.
    """
    book = get_book()
    result = book.list_commodities()
    return json.dumps(result, indent=2)
```

**Implementation in book.py:**

```python
def list_commodities(self) -> dict:
    """List all commodities in the book.
    
    Returns:
        Dict with commodities grouped by namespace.
    """
    with self.open(readonly=True) as book:
        by_namespace: dict[str, list[dict]] = {}
        
        for commodity in book.commodities:
            ns = commodity.namespace
            if ns not in by_namespace:
                by_namespace[ns] = []
            
            by_namespace[ns].append({
                "mnemonic": commodity.mnemonic,
                "fullname": commodity.fullname,
                "fraction": commodity.fraction,
            })
        
        # Sort each namespace's commodities
        for ns in by_namespace:
            by_namespace[ns].sort(key=lambda c: c["mnemonic"])
        
        return {
            "default_currency": book.default_currency.mnemonic,
            "commodities": by_namespace,
        }
```

---

## Usage Examples

### Example 1: Create a USD Credit Card Account (in a EUR book)

```json
{
  "name": "Chase Sapphire",
  "account_type": "CREDIT",
  "parent": "Liabilities:Credit Card",
  "commodity": "USD",
  "description": "US credit card for travel"
}
```

### Example 2: Cross-Currency Transaction

Recording a $50 USD purchase on a USD card, in a EUR-denominated book where €1 = $1.08:

```json
{
  "description": "Coffee in NYC",
  "currency": "EUR",
  "splits": [
    {"account": "Expenses:Dining", "amount": "46.30"},
    {"account": "Liabilities:Credit Card:Chase Sapphire", "amount": "-46.30", "quantity": "-50.00"}
  ]
}
```

The expense account (EUR) gets €46.30. The USD credit card gets $50.00 (quantity), valued at €46.30.

### Example 3: Same-Currency Transaction (backward compatible)

```json
{
  "description": "Groceries",
  "splits": [
    {"account": "Expenses:Groceries", "amount": "85.00"},
    {"account": "Assets:Checking", "amount": "-85.00"}
  ]
}
```

No `currency` specified (uses default), no `quantity` needed (accounts match transaction currency).

---

## Testing Strategy

### Unit Tests

1. **test_create_account_with_currency**: Create EUR account in USD book
2. **test_create_account_default_currency**: Verify None uses default
3. **test_create_account_invalid_currency**: Verify error on "INVALID"
4. **test_create_transaction_cross_currency**: EUR transaction with USD split
5. **test_create_transaction_missing_quantity**: Error when quantity required but missing
6. **test_create_transaction_sign_mismatch**: Error when quantity/value signs differ
7. **test_create_transaction_backward_compat**: Existing single-currency still works
8. **test_list_commodities**: Returns all commodities grouped by namespace

### Integration Tests

1. Create multi-currency account structure
2. Book cross-currency transactions
3. Verify balances in native commodities
4. Verify Price records created for exchange rates

---

## Future Considerations (Out of Scope)

These are NOT part of this spec but noted for future work:

1. **Price Management**: Tools to view/create/update exchange rates
2. **Currency Conversion in Reports**: `spending_by_category` etc. converting to base currency
3. **Trading Accounts**: Support for `book.use_trading_accounts` mode
4. **Stock/Mutual Fund Support**: Non-currency commodities with prices
5. **Scheduled Transactions**: Multi-currency recurring transactions

---

## Migration Notes

- No database migration required
- No breaking changes to existing API
- Existing single-currency books continue working unchanged
- Multi-currency books created in GnuCash GUI can now be modified via MCP

---

## Appendix: piecash Reference

### Key Classes

- `piecash.Commodity`: Currency or stock
- `piecash.Account`: Links to one Commodity
- `piecash.Transaction`: Has currency, contains splits
- `piecash.Split`: Has value (transaction currency) and quantity (account commodity)
- `piecash.Price`: Exchange rate between two commodities on a date

### Key Functions

- `book.commodities.get(mnemonic="USD")`: Find existing commodity
- `factories.create_currency_from_ISO("USD")`: Create currency from ISO code
- `commodity.currency_conversion(other)`: Get exchange rate

### Validation (from piecash.core.transaction.Split.validate)

```python
if self.transaction.currency == self.account.commodity:
    if self.quantity != self.value:
        raise GncValidationError("quantity different from value for same currency")
else:
    if self.quantity is None:
        raise GncValidationError("quantity required for cross-currency split")
    if self.quantity * self.value < 0:
        raise GncValidationError("quantity and value must have same sign")
```
