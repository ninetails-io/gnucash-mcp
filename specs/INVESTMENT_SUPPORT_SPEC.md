# Investment Support Specification
## GnuCash MCP Server Enhancement

**Author:** Abe Raham Jr.  
**Date:** 2026-02-07  
**Status:** Draft  
**Version:** 0.9  
**Prerequisites:** Multi-currency support (implemented)

---

## Executive Summary

The multi-currency infrastructure provides the foundation for investment tracking. Stocks and mutual funds are simply "commodities that aren't currencies" — share counts go in `quantity`, dollar values go in `value`, and prices track the exchange rate over time.

This spec adds the tools needed to create investment commodities, manage prices, and track holdings. The goal is to support the most common retail investment scenario: mutual funds in retirement accounts.

---

## Background: How GnuCash Models Investments

### The Commodity/Price Model

GnuCash treats all non-base-currency holdings uniformly:

| Concept | Currency Example | Investment Example |
|---------|------------------|-------------------|
| Commodity | EUR | VTSAX |
| Account commodity | EUR | VTSAX |
| Transaction currency | USD | USD |
| Split.value | USD amount | USD amount |
| Split.quantity | EUR amount | Share count |
| Price | EUR/USD rate | NAV per share |

A mutual fund purchase is structurally identical to a currency exchange:

```python
# Buying €100 worth of euros
Split(account=eur_account, value=100, quantity=92.50)  # $100 = €92.50

# Buying $100 worth of VTSAX at $250/share
Split(account=vtsax_account, value=100, quantity=0.4)  # $100 = 0.4 shares
```

### Commodity Attributes

From piecash:

```python
Commodity(
    namespace="FUND",           # Grouping: CURRENCY, FUND, NASDAQ, NYSE, etc.
    mnemonic="VTSAX",           # Symbol
    fullname="Vanguard Total Stock Market Index Fund",
    fraction=10000,             # Smallest unit: 10000 = 4 decimal places for shares
    cusip="922908728",          # Optional: CUSIP/ISIN identifier
    quote_flag=1,               # 1 = fetch online quotes (GnuCash GUI feature)
    quote_source="yahoo_json",  # Quote source (GnuCash GUI feature)
)
```

### Price Attributes

```python
Price(
    commodity=vtsax,            # The fund/stock
    currency=usd,               # Price denominated in...
    date=datetime(2026, 2, 7),  # As of date
    value=Decimal("250.45"),    # Price per share/unit
    type="nav",                 # nav, last, bid, ask, unknown
    source="user:price",        # Source identifier
)
```

### Common Namespaces

| Namespace | Use |
|-----------|-----|
| CURRENCY | ISO currencies (handled by existing code) |
| FUND | Mutual funds |
| NASDAQ | NASDAQ-listed stocks |
| NYSE | NYSE-listed stocks |
| AMEX | AMEX-listed stocks |
| MUTUALFUND | Alternative for mutual funds |
| CUSTOM | User-defined commodities |

GnuCash is flexible — namespace is just a string for grouping.

---

## Current State

### What Works Today

1. **list_commodities** — Already shows all commodities by namespace
2. **create_account** — Has `commodity` parameter but only handles CURRENCY namespace
3. **create_transaction** — Has `quantity` parameter for cross-commodity splits
4. **get_balance** — Returns quantity (share count) for investment accounts
5. **Multi-currency validation** — Same-sign check, quantity-required check

### What's Missing

1. **create_commodity** — No way to create stocks/funds
2. **create_account with non-currency commodity** — Limited to CURRENCY namespace
3. **Price management** — No tools to record or retrieve prices
4. **Holdings report** — No way to see portfolio value

---

## Proposed Changes

### 1. New Tool: create_commodity

```python
@mcp.tool()
def create_commodity(
    mnemonic: str,
    fullname: str,
    namespace: str = "FUND",
    fraction: int = 10000,
    cusip: str | None = None,
) -> str:
    """Create a new commodity (stock, mutual fund, etc.) in the book.

    Args:
        mnemonic: Symbol (e.g., "VTSAX", "AAPL"). Must be unique within namespace.
        fullname: Full name (e.g., "Vanguard Total Stock Market Index Fund").
        namespace: Grouping category. Common values:
            - "FUND" for mutual funds (default)
            - "NASDAQ", "NYSE", "AMEX" for stocks
            - Any custom string for other assets
        fraction: Smallest fractional unit. Use:
            - 1 for whole units only
            - 100 for 2 decimal places
            - 10000 for 4 decimal places (default, standard for shares)
            - 1000000 for 6 decimal places (crypto)
        cusip: Optional CUSIP/ISIN identifier for the security.

    Returns:
        JSON with mnemonic, namespace, fullname, and status.

    Raises:
        ValueError: If commodity already exists in that namespace.
    """
```

**Implementation in book.py:**

```python
def create_commodity(
    self,
    mnemonic: str,
    fullname: str,
    namespace: str = "FUND",
    fraction: int = 10000,
    cusip: str | None = None,
) -> dict:
    """Create a new commodity."""
    with self.open(readonly=False) as book:
        # Check for duplicate
        existing = self._find_commodity(book, mnemonic, namespace)
        if existing:
            raise ValueError(
                f"Commodity {namespace}:{mnemonic} already exists"
            )

        commodity = piecash.Commodity(
            namespace=namespace,
            mnemonic=mnemonic,
            fullname=fullname,
            fraction=fraction,
            cusip=cusip or "",
            book=book,
        )

        book.save()

        return {
            "mnemonic": commodity.mnemonic,
            "namespace": commodity.namespace,
            "fullname": commodity.fullname,
            "fraction": commodity.fraction,
            "status": "created",
        }
```

### 2. Enhanced create_account

Extend the existing `commodity` parameter to accept non-currency commodities:

**Current signature** (already has commodity parameter):
```python
def create_account(
    name: str,
    account_type: str,
    parent: str,
    description: str = "",
    placeholder: bool = False,
    commodity: str | None = None,  # Currently only handles currencies
) -> dict:
```

**New signature:**
```python
def create_account(
    name: str,
    account_type: str,
    parent: str,
    description: str = "",
    placeholder: bool = False,
    commodity: str | None = None,
    commodity_namespace: str = "CURRENCY",  # NEW
) -> dict:
```

**Implementation change:**

```python
# Determine commodity
if commodity is None:
    account_commodity = book.default_currency
elif commodity_namespace == "CURRENCY":
    # Existing behavior: auto-create from ISO
    account_commodity = self._get_or_create_currency(book, commodity)
else:
    # New behavior: look up existing commodity
    account_commodity = self._find_commodity(book, commodity, commodity_namespace)
    if not account_commodity:
        raise ValueError(
            f"Commodity not found: {commodity_namespace}:{commodity}. "
            f"Create it first with create_commodity."
        )
```

**Docstring update:**
```python
"""Create a new account in the chart of accounts.

Args:
    name: Account name (e.g., "Vanguard 401k").
    account_type: GnuCash account type. For investments, use:
        - STOCK for individual stocks
        - MUTUAL for mutual funds
        - ASSET for general investment containers
    parent: Full path of parent account.
    description: Optional description.
    placeholder: If True, account is container-only.
    commodity: Symbol for the account's commodity:
        - For currencies: ISO code (e.g., "USD", "EUR")
        - For investments: Fund/stock symbol (e.g., "VTSAX", "AAPL")
        Defaults to book's default currency.
    commodity_namespace: Namespace of the commodity:
        - "CURRENCY" (default) for currencies
        - "FUND" for mutual funds
        - "NASDAQ", "NYSE", etc. for stocks
        Required when commodity is not a currency.

Returns:
    Dict with guid, fullname, and status.
"""
```

### 3. New Tool: create_price

```python
@mcp.tool()
def create_price(
    commodity: str,
    namespace: str,
    value: str,
    currency: str = "USD",
    date: str | None = None,
    price_type: str = "nav",
    source: str = "user:price",
) -> str:
    """Record a price for a commodity (stock price, NAV, exchange rate).

    Args:
        commodity: Symbol of the commodity (e.g., "VTSAX", "AAPL").
        namespace: Namespace of the commodity (e.g., "FUND", "NASDAQ").
        value: Price per unit as decimal string (e.g., "250.45").
        currency: Currency the price is denominated in. Default "USD".
        date: Price date in ISO format (YYYY-MM-DD). Defaults to today.
        price_type: Type of price:
            - "nav" for mutual fund net asset value (default)
            - "last" for last trade price
            - "bid" / "ask" for bid/ask prices
            - "unknown" for unspecified
        source: Source identifier. Default "user:price".

    Returns:
        JSON with commodity, date, value, and status.

    Note:
        If a price already exists for the same commodity/currency/date/source,
        it will be updated rather than creating a duplicate.
    """
```

**Implementation in book.py:**

```python
def create_price(
    self,
    commodity: str,
    namespace: str,
    value: str,
    currency: str = "USD",
    price_date: date | None = None,
    price_type: str = "nav",
    source: str = "user:price",
) -> dict:
    """Record a price for a commodity."""
    if price_date is None:
        price_date = date.today()

    with self.open(readonly=False) as book:
        # Find the commodity
        comm = self._find_commodity(book, commodity, namespace)
        if not comm:
            raise ValueError(f"Commodity not found: {namespace}:{commodity}")

        # Find the currency
        curr = self._get_or_create_currency(book, currency)

        # Check for existing price (same commodity/currency/date/source)
        existing = None
        for p in book.prices:
            if (p.commodity == comm and 
                p.currency == curr and 
                p.date.date() == price_date and
                p.source == source):
                existing = p
                break

        if existing:
            # Update existing price
            existing.value = Decimal(value)
            existing.type = price_type
        else:
            # Create new price
            piecash.Price(
                commodity=comm,
                currency=curr,
                date=datetime.combine(price_date, datetime.min.time()),
                value=Decimal(value),
                type=price_type,
                source=source,
            )

        book.save()

        return {
            "commodity": commodity,
            "namespace": namespace,
            "currency": currency,
            "date": price_date.isoformat(),
            "value": value,
            "type": price_type,
            "status": "updated" if existing else "created",
        }
```

### 4. New Tool: get_prices

```python
@mcp.tool()
def get_prices(
    commodity: str,
    namespace: str,
    start_date: str | None = None,
    end_date: str | None = None,
    currency: str | None = None,
) -> str:
    """Get price history for a commodity.

    Args:
        commodity: Symbol of the commodity (e.g., "VTSAX").
        namespace: Namespace of the commodity (e.g., "FUND").
        start_date: Optional start date filter (YYYY-MM-DD).
        end_date: Optional end date filter (YYYY-MM-DD).
        currency: Optional currency filter (e.g., "USD").

    Returns:
        JSON with list of prices, each containing date, value, currency,
        type, and source. Sorted by date descending (most recent first).
    """
```

**Implementation in book.py:**

```python
def get_prices(
    self,
    commodity: str,
    namespace: str,
    start_date: date | None = None,
    end_date: date | None = None,
    currency: str | None = None,
) -> list[dict]:
    """Get price history for a commodity."""
    with self.open(readonly=True) as book:
        comm = self._find_commodity(book, commodity, namespace)
        if not comm:
            raise ValueError(f"Commodity not found: {namespace}:{commodity}")

        prices = []
        for p in book.prices:
            if p.commodity != comm:
                continue
            if currency and p.currency.mnemonic != currency:
                continue
            if start_date and p.date.date() < start_date:
                continue
            if end_date and p.date.date() > end_date:
                continue

            prices.append({
                "date": p.date.date().isoformat(),
                "value": str(p.value),
                "currency": p.currency.mnemonic,
                "type": p.type,
                "source": p.source,
            })

        # Sort by date descending
        prices.sort(key=lambda x: x["date"], reverse=True)

        return prices
```

### 5. New Tool: get_latest_price

Convenience tool for getting the most recent price:

```python
@mcp.tool()
def get_latest_price(
    commodity: str,
    namespace: str,
    currency: str = "USD",
) -> str:
    """Get the most recent price for a commodity.

    Args:
        commodity: Symbol of the commodity (e.g., "VTSAX").
        namespace: Namespace of the commodity (e.g., "FUND").
        currency: Currency for the price. Default "USD".

    Returns:
        JSON with date, value, type, and source of most recent price.
        Returns null if no price exists.
    """
```

### 6. Enhanced list_commodities

The existing tool already works but could benefit from showing price info:

```python
def list_commodities(self) -> dict:
    """List all commodities with their latest prices."""
    with self.open(readonly=True) as book:
        by_namespace: dict[str, list[dict]] = {}

        for commodity in book.commodities:
            ns = commodity.namespace
            if ns not in by_namespace:
                by_namespace[ns] = []

            # Find latest price
            latest_price = None
            latest_date = None
            for p in book.prices:
                if p.commodity == commodity:
                    if latest_date is None or p.date > latest_date:
                        latest_date = p.date
                        latest_price = p

            entry = {
                "mnemonic": commodity.mnemonic,
                "fullname": commodity.fullname,
                "fraction": commodity.fraction,
            }

            if latest_price:
                entry["latest_price"] = {
                    "value": str(latest_price.value),
                    "currency": latest_price.currency.mnemonic,
                    "date": latest_price.date.date().isoformat(),
                }

            by_namespace[ns].append(entry)

        # Sort each namespace
        for ns in by_namespace:
            by_namespace[ns].sort(key=lambda c: c["mnemonic"])

        return {
            "default_currency": book.default_currency.mnemonic,
            "commodities": by_namespace,
        }
```

---

## Usage Examples

### Example 1: Set Up a Mutual Fund

```
User: Create a commodity for the Vanguard Total Stock Market fund, symbol VTSAX.

Claude: [calls create_commodity]
{
  "mnemonic": "VTSAX",
  "fullname": "Vanguard Total Stock Market Index Fund Admiral",
  "namespace": "FUND",
  "fraction": 10000
}

User: Now create an account for my 401k holdings in VTSAX.

Claude: [calls create_account]
{
  "name": "VTSAX",
  "account_type": "MUTUAL",
  "parent": "Assets:Investments:401k",
  "commodity": "VTSAX",
  "commodity_namespace": "FUND"
}
```

### Example 2: Record a Purchase

```
User: I bought $500 worth of VTSAX today at $127.50 per share.

Claude: [calls create_price]
{
  "commodity": "VTSAX",
  "namespace": "FUND",
  "value": "127.50",
  "price_type": "nav"
}

Claude: [calls create_transaction]
{
  "description": "VTSAX purchase",
  "currency": "USD",
  "splits": [
    {"account": "Assets:Investments:401k:VTSAX", "amount": "500.00", "quantity": "3.9216"},
    {"account": "Assets:Current Assets:Checking Account", "amount": "-500.00"}
  ]
}

Created transaction: $500 purchase = 3.9216 shares at $127.50/share
```

### Example 3: Check Holdings

```
User: How many shares of VTSAX do I have?

Claude: [calls get_balance]
{
  "account": "Assets:Investments:401k:VTSAX",
  "balance": "3.9216"
}

You have 3.9216 shares of VTSAX.

User: What's that worth today?

Claude: [calls get_latest_price]
{
  "commodity": "VTSAX",
  "namespace": "FUND",
  "currency": "USD"
}

At $127.50/share, your 3.9216 shares are worth approximately $500.00.
```

### Example 4: Record Updated NAV

```
User: VTSAX closed at $128.75 today.

Claude: [calls create_price]
{
  "commodity": "VTSAX",
  "namespace": "FUND",
  "value": "128.75",
  "date": "2026-02-07",
  "price_type": "nav"
}

Recorded VTSAX NAV of $128.75 for 2026-02-07.
Your 3.9216 shares are now worth approximately $504.90.
```

---

## Testing Strategy

### Unit Tests

1. **test_create_commodity** — Create VTSAX, verify in book.commodities
2. **test_create_commodity_duplicate** — Error on duplicate mnemonic in same namespace
3. **test_create_commodity_different_namespace** — Same mnemonic OK in different namespace
4. **test_create_account_with_fund** — Account with FUND commodity
5. **test_create_account_missing_commodity** — Error if commodity doesn't exist
6. **test_create_price** — Record NAV, verify in book.prices
7. **test_create_price_update** — Same date/source updates existing
8. **test_get_prices** — Returns history sorted descending
9. **test_get_prices_filtered** — Date range filtering works
10. **test_get_latest_price** — Returns most recent price
11. **test_transaction_with_shares** — Buy shares, quantity correct
12. **test_balance_shows_shares** — get_balance returns share count

### Integration Tests

1. Full workflow: create fund → create account → record prices → buy shares → check balance
2. Multiple funds in same account hierarchy
3. Sell shares (negative quantity)
4. Price history over time

---

## Future Considerations (Out of Scope for v0.9)

1. **Lot tracking** — Track individual purchase lots for cost basis
2. **Capital gains** — Calculate realized/unrealized gains
3. **Dividend reinvestment** — DRIP transaction support
4. **Portfolio report** — Aggregate holdings, value, allocation percentages
5. **Online quotes** — Auto-fetch prices from data providers
6. **Stock splits** — Handle share count adjustments
7. **Brokerage import** — Parse OFX/QFX transaction files

---

## Tool Summary

| Tool | Classification | Purpose |
|------|----------------|---------|
| create_commodity | write | Create stock/fund commodity |
| create_account (enhanced) | write | Create account with any commodity |
| create_price | write | Record price/NAV |
| get_prices | read | Price history |
| get_latest_price | read | Most recent price |
| list_commodities (enhanced) | read | All commodities with latest prices |

**Total new tools:** 4 (create_commodity, create_price, get_prices, get_latest_price)  
**Enhanced tools:** 2 (create_account, list_commodities)

---

## Appendix: Mutual Fund Specifics

### NAV (Net Asset Value)

Mutual funds price once daily after market close. The NAV is the per-share value:

```
NAV = (Total Fund Assets - Liabilities) / Shares Outstanding
```

When recording mutual fund transactions:
- Use `price_type="nav"`
- Prices should be recorded for the trade date
- Shares are typically quoted to 3-4 decimal places (hence `fraction=10000`)

### Common Fund Families and Namespaces

| Provider | Typical Namespace | Example Symbols |
|----------|-------------------|-----------------|
| Vanguard | FUND | VTSAX, VTIAX, VBTLX |
| Fidelity | FUND | FXAIX, FSKAX, FTBFX |
| Schwab | FUND | SWTSX, SWPPX |
| Generic ETF | NYSE/NASDAQ | VTI, SPY, QQQ |

For simplicity, this spec uses "FUND" as the default namespace for all mutual funds. Users can choose more specific namespaces if desired.

### Fraction Values

| Asset Type | Fraction | Decimal Places |
|------------|----------|----------------|
| Currency | 100 | 2 |
| Mutual Fund | 10000 | 4 |
| Stock | 10000 | 4 |
| Crypto | 1000000 | 6 |
