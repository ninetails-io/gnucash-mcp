# Lots (Cost Basis Tracking) Specification
## GnuCash MCP Server Enhancement

**Author:** the bookkeeper  
**Date:** 2026-02-07  
**Status:** Draft  
**Version:** 1.0 Roadmap  
**Prerequisites:** Investment support (implemented — commodities, prices, MUTUAL/STOCK accounts)

---

## Executive Summary

Lots complete the investment tracking story. A **Lot** groups purchase splits together for cost basis tracking. When you sell shares, you specify which lot to sell from, enabling accurate capital gain/loss calculation.

Without lots: "I own 100 shares of VTSAX worth $12,500."
With lots: "I own 100 shares across 3 purchases. Selling the January lot would realize a $200 gain. Selling the March lot would realize a $50 loss."

---

## Background: How GnuCash Models Lots

A Lot is simply a container that groups related splits.

**From piecash:**

```python
Lot(
    title="VTSAX 2026-01-15 purchase",
    account=vtsax_account,        # The investment account
    notes="Bought at $125.00/share",
    is_closed=False,              # True when fully sold
)

# Splits have a lot attribute
split.lot = lot  # Assigns this split to the lot
```

**The Lot Lifecycle:**

1. **Create lot** — When buying shares, create a lot to track that purchase
2. **Assign split** — Link the purchase split to the lot
3. **Track holdings** — Lot knows its quantity (sum of split quantities)
4. **Sell** — Create sale split assigned to same lot (negative quantity)
5. **Close** — When quantity reaches zero, lot is closed

**Key Insight:**

Lots don't store amounts directly. They're just containers. The cost basis comes from summing the `split.value` of all splits in the lot. The share count comes from summing `split.quantity`.

```python
# Lot with one purchase and one partial sale:
# Split 1: Buy 10 shares at $125 → quantity=10, value=$1250
# Split 2: Sell 3 shares at $130 → quantity=-3, value=-$390

total_quantity = 10 + (-3) = 7 shares remaining
total_value = 1250 + (-390) = $860 remaining cost basis
cost_per_share = 860 / 7 = $122.86
```

Wait, that math is wrong for cost basis. Let me reconsider.

**Correct Cost Basis Tracking:**

The cost basis of remaining shares should be proportional to original purchase, not reduced by sale proceeds. Let me recalculate:

```python
# Purchase: 10 shares at $125 = $1250 cost basis
# Sale: 3 shares at $130 = $390 proceeds

# Cost basis of shares sold: (3/10) × $1250 = $375
# Capital gain: $390 - $375 = $15

# Remaining: 7 shares
# Remaining cost basis: (7/10) × $1250 = $875
# Cost per share: still $125 (unchanged)
```

The sale split's `value` represents proceeds, not cost. Cost basis must be calculated from the original purchase proportionally.

---

## Proposed Tools

### 1. create_lot

```python
@mcp.tool()
def create_lot(
    account: str,
    title: str,
    notes: str = "",
) -> str:
    """Create a new lot for cost basis tracking.

    Lots group investment purchases for tracking cost basis and
    calculating capital gains when selling.

    Args:
        account: Full path of investment account (e.g., "Assets:Investments:VTSAX").
        title: Lot identifier (e.g., "VTSAX 2026-01-15 purchase").
        notes: Optional notes.

    Returns:
        JSON with guid, title, account, and status.
    """
```

### 2. list_lots

```python
@mcp.tool()
def list_lots(
    account: str,
    include_closed: bool = False,
) -> str:
    """List all lots for an investment account.

    Args:
        account: Full path of investment account.
        include_closed: If True, include fully-sold lots. Default False.

    Returns:
        JSON list of lots with:
        - guid, title, notes, is_closed
        - quantity (shares remaining)
        - cost_basis (original cost of remaining shares)
        - cost_per_share

    Example response:
        [
            {
                "guid": "abc123...",
                "title": "VTSAX 2026-01-15 purchase",
                "is_closed": false,
                "quantity": "7.0000",
                "cost_basis": "875.00",
                "cost_per_share": "125.00"
            }
        ]
    """
```

### 3. get_lot

```python
@mcp.tool()
def get_lot(
    guid: str,
) -> str:
    """Get detailed information about a lot.

    Args:
        guid: Lot GUID.

    Returns:
        JSON with lot details including all splits:
        - title, notes, is_closed
        - splits: list of all splits with date, quantity, value
        - summary: total quantity, cost basis, cost per share
    """
```

### 4. assign_split_to_lot

```python
@mcp.tool()
def assign_split_to_lot(
    split_guid: str,
    lot_guid: str,
) -> str:
    """Assign a transaction split to a lot.

    Use after creating a buy/sell transaction to link the investment
    account split to its lot for cost basis tracking.

    Args:
        split_guid: GUID of the split (from transaction's investment account).
        lot_guid: GUID of the lot.

    Returns:
        JSON with status and updated lot summary.

    Workflow:
        1. create_lot("Assets:VTSAX", "VTSAX Jan 2026")
        2. create_transaction(...buy 10 shares...)
        3. assign_split_to_lot(investment_split_guid, lot_guid)
    """
```

### 5. calculate_lot_gain

```python
@mcp.tool()
def calculate_lot_gain(
    lot_guid: str,
    shares: str | None = None,
    sale_price: str | None = None,
) -> str:
    """Calculate potential or actual capital gain for a lot.

    If shares and sale_price provided, calculates hypothetical gain.
    Otherwise uses lot's current state and latest price.

    Args:
        lot_guid: Lot GUID.
        shares: Optional number of shares to calculate for.
                Defaults to all remaining shares.
        sale_price: Optional sale price per share.
                    Defaults to latest price for the commodity.

    Returns:
        JSON with:
        - shares
        - cost_basis (for those shares)
        - sale_proceeds (shares × price)
        - capital_gain (proceeds - basis)
        - gain_percent

    Example:
        Lot has 10 shares at $125 cost. Current price $130.
        {
            "shares": "10.0000",
            "cost_basis": "1250.00",
            "sale_proceeds": "1300.00",
            "capital_gain": "50.00",
            "gain_percent": "4.00"
        }
    """
```

### 6. close_lot

```python
@mcp.tool()
def close_lot(
    guid: str,
) -> str:
    """Mark a lot as closed.

    Use when a lot is fully sold but wasn't automatically marked closed,
    or to manually close a lot with zero shares.

    Args:
        guid: Lot GUID.

    Returns:
        JSON with status.

    Note:
        Lots are automatically marked closed when their quantity reaches zero
        through assigned splits. This tool is for manual cleanup.
    """
```

---

## Implementation Notes

### Lot Calculations

```python
def calculate_lot_summary(lot: Lot) -> dict:
    """Calculate current state of a lot from its splits."""
    
    purchase_quantity = Decimal(0)
    purchase_value = Decimal(0)
    sale_quantity = Decimal(0)
    
    for split in lot.splits:
        if split.quantity > 0:
            # Purchase split
            purchase_quantity += split.quantity
            purchase_value += split.value
        else:
            # Sale split
            sale_quantity += abs(split.quantity)
    
    remaining_quantity = purchase_quantity - sale_quantity
    
    if purchase_quantity > 0:
        cost_per_share = purchase_value / purchase_quantity
        remaining_cost_basis = cost_per_share * remaining_quantity
    else:
        cost_per_share = Decimal(0)
        remaining_cost_basis = Decimal(0)
    
    return {
        "quantity": remaining_quantity,
        "cost_basis": remaining_cost_basis,
        "cost_per_share": cost_per_share,
        "is_closed": remaining_quantity == 0,
    }
```

### Capital Gain Calculation

```python
def calculate_gain(lot: Lot, shares_to_sell: Decimal, sale_price: Decimal) -> dict:
    """Calculate capital gain for selling shares from a lot."""
    
    summary = calculate_lot_summary(lot)
    
    if shares_to_sell > summary["quantity"]:
        raise ValueError(f"Cannot sell {shares_to_sell}; lot has {summary['quantity']}")
    
    cost_basis = summary["cost_per_share"] * shares_to_sell
    proceeds = sale_price * shares_to_sell
    gain = proceeds - cost_basis
    
    return {
        "shares": shares_to_sell,
        "cost_basis": cost_basis,
        "sale_proceeds": proceeds,
        "capital_gain": gain,
        "gain_percent": (gain / cost_basis * 100) if cost_basis else Decimal(0),
    }
```

### Assigning Splits to Lots

In piecash, it's straightforward:

```python
split = book.splits(guid=split_guid)
lot = book.lots(guid=lot_guid)
split.lot = lot
book.save()
```

### Auto-Close Detection

After assigning a split, check if lot should close:

```python
summary = calculate_lot_summary(lot)
if summary["quantity"] == 0:
    lot.is_closed = True
```

---

## Usage Examples

### Buy Shares with Lot Tracking

```
User: I bought $1,000 of VTSAX at $125/share. Track the cost basis.

Claude: 
[calls create_lot] → "VTSAX 2026-02-07"
[calls create_price] → $125.00 NAV
[calls create_transaction] → $1,000 buys 8 shares
[calls assign_split_to_lot] → links purchase to lot

Created lot "VTSAX 2026-02-07":
- 8 shares at $125.00/share
- Cost basis: $1,000.00
```

### Check Potential Gain

```
User: What would I make if I sold my VTSAX at $130?

Claude:
[calls calculate_lot_gain with sale_price="130.00"]

If you sell at $130/share:
- 8 shares × $130 = $1,040 proceeds
- Cost basis: $1,000
- Capital gain: $40 (4.0%)
```

### Record a Sale

```
User: I sold 3 shares of VTSAX at $130.

Claude:
[calls create_price] → $130.00 (if not already recorded)
[calls create_transaction] → sell 3 shares for $390
[calls assign_split_to_lot] → links sale to the lot

Sold 3 shares from lot "VTSAX 2026-02-07":
- Proceeds: $390.00
- Cost basis: $375.00 (3 × $125)
- Capital gain: $15.00
- Remaining: 5 shares, cost basis $625.00
```

### Multiple Lots at Different Prices

```
User: Show me my VTSAX lots.

Claude:
[calls list_lots("Assets:Investments:VTSAX")]

VTSAX Holdings (2 lots):

1. "VTSAX 2026-01-15" 
   - 5 shares at $125/share
   - Cost basis: $625
   - Current value: $650 (at $130)
   - Unrealized gain: $25 (4.0%)

2. "VTSAX 2026-02-01"
   - 10 shares at $128/share  
   - Cost basis: $1,280
   - Current value: $1,300 (at $130)
   - Unrealized gain: $20 (1.6%)

Total: 15 shares, $1,905 basis, $1,950 value, $45 gain (2.4%)
```

---

## Test Plan

### Setup
Use the VTSAX commodity and investment account from previous investment testing, or create fresh ones.

### Tests

| # | Test | Action | Expected |
|---|------|--------|----------|
| 1 | list_lots (empty) | List lots for investment account | Empty list |
| 2 | create_lot | Create "VTSAX Test Lot" | Returns guid, title |
| 3 | list_lots (shows lot) | List lots again | Shows the new lot with zero quantity |
| 4 | Buy shares | create_transaction buying 10 shares at $125 | Transaction created |
| 5 | assign_split_to_lot | Assign purchase split to lot | Status success |
| 6 | get_lot | Get lot details | Shows split, quantity=10, cost=$1250 |
| 7 | calculate_lot_gain | Hypothetical sale at $130 | Gain = $50 (4%) |
| 8 | Sell shares | create_transaction selling 4 shares at $130 | Transaction created |
| 9 | Assign sale split | assign_split_to_lot for sale | Status success |
| 10 | get_lot after sale | Check lot state | Quantity=6, cost=$750, shows both splits |
| 11 | Sell remaining | Sell 6 shares | Transaction created |
| 12 | Lot auto-closes | get_lot or list_lots | is_closed=true |
| 13 | Cleanup | Delete test transactions and lot | Clean state |

---

## Tool Summary

| Tool | Classification | Purpose |
|------|----------------|---------|
| create_lot | write | Create cost basis lot |
| list_lots | read | List lots for account |
| get_lot | read | Lot details with splits |
| assign_split_to_lot | write | Link split to lot |
| calculate_lot_gain | read | Compute gain/loss |
| close_lot | write | Manual lot close |

**Total: 6 tools**

---

## Future Considerations (Out of Scope)

1. **FIFO/LIFO automatic selection** — Auto-pick lots when selling
2. **Wash sale detection** — Flag sales that trigger wash sale rules
3. **Long-term vs short-term** — Classify gains by holding period
4. **Tax lot optimizer** — Suggest which lots to sell for tax efficiency
5. **Brokerage import** — Parse statements to create lots automatically
6. **Realized gains report** — Summary of all closed lots and gains

---

## Appendix: Piecash Lot API

```python
# Create a lot
lot = Lot(
    title="VTSAX 2026-01-15",
    account=account,
    notes="Bought at $125"
)
book.add(lot)
book.save()

# Assign split to lot
split.lot = lot
book.save()

# Query lots for an account
lots = [lot for lot in book.lots if lot.account == account]

# Check if closed
lot.is_closed  # boolean

# Get splits in lot
lot.splits  # list of Split objects
```

The piecash API is clean for lots — much simpler than scheduled transactions. Direct attribute assignment, no slot gymnastics.
