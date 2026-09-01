# Scheduled Transactions & Lots Specification
## GnuCash MCP Server Enhancement

**Author:** the bookkeeper  
**Date:** 2026-02-07  
**Status:** Draft  
**Version:** 1.0 Roadmap  
**Prerequisites:** Core transaction support, Investment support (implemented)

---

## Executive Summary

This spec covers two distinct features:

1. **Scheduled Transactions** — Recurring transaction templates for bills, paychecks, and subscriptions. Answers "what's coming up?"

2. **Lots** — Cost basis tracking for investments. Links purchase and sale transactions to calculate capital gains. Completes the investment story.

---

## Part 1: Scheduled Transactions

### Background: How GnuCash Models Scheduled Transactions

A scheduled transaction is a template that generates real transactions on a schedule.

**The Complexity Problem:**

GnuCash's scheduled transaction implementation is notoriously complex:
- Splits stored in a hidden "template" account hierarchy
- Recurrence is a separate `Recurrence` object with multiplier, period type, and weekend adjustment
- Template splits use slots for values instead of normal split fields
- `ScheduledTransaction` object has ~15 attributes

**From piecash source:**

```python
ScheduledTransaction(
    name="Monthly Rent",
    enabled=True,
    
    # Schedule
    start_date=datetime(2026, 1, 1),
    end_date=None,
    
    # Recurrence (separate object)
    # recurrence.mult = 1
    # recurrence.period_type = "month"
    # recurrence.period_start = start_date
    
    # Occurrence limits
    num_occur=0,          # Total (0 = unlimited)
    rem_occur=0,          # Remaining
    
    # Automation
    auto_create=True,
    auto_notify=False,
    adv_creation=0,       # Days to create in advance
    adv_notify=7,         # Days to notify in advance
    
    # Tracking
    last_occur=None,
    instance_count=0,
    
    # Template (the hard part)
    template_account=Account(...),  # Special account with SplitTemplates
)
```

**The Template Account Problem:**

GnuCash stores scheduled transaction splits in a special account hierarchy under a root "Template Root" account. Each scheduled transaction gets its own template account containing `Split` objects where the actual values are stored in slots (key-value pairs), not in the normal `value`/`quantity` fields.

This is... architectural. Piecash exposes it but doesn't simplify it.

### Recommended Approach: Pragmatic Simplification

Rather than fully replicating GnuCash's scheduled transaction complexity, implement a simplified version that covers 90% of use cases:

**Option A: Full GnuCash Compatibility (Hard)**
- Create template account hierarchy
- Store split values in slots
- Handle all recurrence edge cases
- ~500 lines of code, high bug risk

**Option B: Simplified Model (Recommended)**
- Store schedule metadata + serialized split templates as JSON in a slot
- Calculate next occurrence from start_date + frequency
- Generate transactions on demand via `create_transaction_from_scheduled`
- ~150 lines of code, lower risk

**Option C: External Tracking (Simplest)**
- Don't use GnuCash's scheduled transaction tables at all
- Store schedules in a separate JSON file or SQLite table managed by the MCP server
- Full control, zero GnuCash complexity
- Won't appear in GnuCash GUI (acceptable tradeoff?)

### Proposed Tools (Option B)

#### 1. create_scheduled_transaction

```python
@mcp.tool()
def create_scheduled_transaction(
    name: str,
    description: str,
    splits: list[dict],
    start_date: str,
    frequency: str,
    end_date: str | None = None,
    enabled: bool = True,
) -> str:
    """Create a recurring transaction template.

    Args:
        name: Name for the scheduled transaction (e.g., "Monthly Rent").
        description: Transaction description when created.
        splits: List of splits, same format as create_transaction:
            [{"account": "Expenses:Rent", "amount": "1850.00"}, ...]
        start_date: First occurrence date (YYYY-MM-DD).
        frequency: How often it recurs:
            - "weekly"
            - "biweekly" (every 2 weeks)
            - "monthly"
            - "quarterly"
            - "yearly"
        end_date: Optional last occurrence date (YYYY-MM-DD).
        enabled: Whether the schedule is active. Default True.

    Returns:
        JSON with guid, name, next_occurrence, and status.
    """
```

#### 2. list_scheduled_transactions

```python
@mcp.tool()
def list_scheduled_transactions(
    enabled_only: bool = True,
) -> str:
    """List all scheduled transactions.

    Args:
        enabled_only: If True, only show enabled schedules. Default True.

    Returns:
        JSON list with guid, name, frequency, next_occurrence, enabled.
    """
```

#### 3. get_upcoming_transactions

```python
@mcp.tool()
def get_upcoming_transactions(
    days: int = 14,
) -> str:
    """Get scheduled transactions due within a time window.

    This is the "what bills are coming up?" query.

    Args:
        days: Look ahead window in days. Default 14.

    Returns:
        JSON list of upcoming occurrences:
        [
            {
                "name": "Monthly Rent",
                "occurrence_date": "2026-02-01",
                "amount": "1850.00",
                "days_until": 3
            }
        ]
    """
```

#### 4. create_transaction_from_scheduled

```python
@mcp.tool()
def create_transaction_from_scheduled(
    guid: str,
    transaction_date: str | None = None,
) -> str:
    """Create an actual transaction from a scheduled template.

    Args:
        guid: Scheduled transaction GUID.
        transaction_date: Date for the transaction. Defaults to next occurrence.

    Returns:
        JSON with created transaction guid.
    """
```

#### 5. update_scheduled_transaction

```python
@mcp.tool()
def update_scheduled_transaction(
    guid: str,
    enabled: bool | None = None,
    end_date: str | None = None,
) -> str:
    """Update a scheduled transaction.

    Args:
        guid: Scheduled transaction GUID.
        enabled: Enable or disable.
        end_date: Set end date (empty string to clear).

    Returns:
        JSON with updated details.
    """
```

#### 6. delete_scheduled_transaction

```python
@mcp.tool()
def delete_scheduled_transaction(
    guid: str,
) -> str:
    """Delete a scheduled transaction.

    Args:
        guid: Scheduled transaction GUID.

    Returns:
        JSON with status.
    """
```

### Implementation Notes

**Recurrence Calculation:**

```python
from dateutil.relativedelta import relativedelta

def next_occurrence(start_date: date, frequency: str, after: date = None) -> date:
    """Calculate next occurrence after a given date."""
    if after is None:
        after = date.today()
    
    delta = {
        "weekly": relativedelta(weeks=1),
        "biweekly": relativedelta(weeks=2),
        "monthly": relativedelta(months=1),
        "quarterly": relativedelta(months=3),
        "yearly": relativedelta(years=1),
    }[frequency]
    
    occurrence = start_date
    while occurrence <= after:
        occurrence += delta
    return occurrence
```

**GnuCash Recurrence Mapping:**

| Our Frequency | GnuCash mult | GnuCash period_type |
|---------------|--------------|---------------------|
| weekly | 1 | week |
| biweekly | 2 | week |
| monthly | 1 | month |
| quarterly | 3 | month |
| yearly | 1 | year |

---

## Part 2: Lots (Cost Basis Tracking)

### Background: How GnuCash Models Lots

A **Lot** groups related splits together, typically for tracking the cost basis of investment purchases. When you buy shares, those shares go into a lot. When you sell, you pull from a specific lot to calculate gain/loss.

**From piecash:**

```python
Lot(
    title="VTSAX 2026-01-15 purchase",
    account=vtsax_account,        # The investment account
    notes="Bought at $125.00/share",
    is_closed=False,              # True when fully sold
    splits=[...],                 # Splits that belong to this lot
)
```

**How Lots Work:**

1. **Purchase:** Create a transaction buying 10 shares. The split goes into a new Lot.
2. **Another Purchase:** Buy 5 more shares at a different price. New Lot.
3. **Sale:** Sell 8 shares. Must specify which lot(s) to pull from:
   - FIFO (First In, First Out): Sell from oldest lot first
   - LIFO (Last In, First Out): Sell from newest lot first
   - Specific Identification: Choose which lot

4. **Gain Calculation:**
   - Sale proceeds: 8 shares × $130 = $1,040
   - Cost basis (from lot): 8 shares × $125 = $1,000
   - Capital gain: $40

**Lot Attributes:**

| Attribute | Type | Purpose |
|-----------|------|---------|
| title | str | Human identifier ("VTSAX 2026-01-15") |
| account | Account | Investment account this lot belongs to |
| notes | str | Optional notes |
| is_closed | bool | True when all shares sold |
| splits | list[Split] | All splits in this lot |

### Proposed Tools

#### 1. create_lot

```python
@mcp.tool()
def create_lot(
    account: str,
    title: str,
    notes: str = "",
) -> str:
    """Create a new lot for cost basis tracking.

    Lots group investment purchases together. When you sell shares,
    you specify which lot to sell from to calculate capital gains.

    Args:
        account: Full path of investment account (e.g., "Assets:Investments:401k:VTSAX").
        title: Lot identifier (e.g., "VTSAX 2026-01-15 purchase").
        notes: Optional notes about the purchase.

    Returns:
        JSON with guid, title, account, and status.

    Note:
        After creating a lot, use assign_split_to_lot to add the purchase
        split to this lot.
    """
```

#### 2. list_lots

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
        - guid, title, notes
        - is_closed
        - quantity (shares remaining)
        - cost_basis (total cost of remaining shares)
        - cost_per_share (average cost)
    """
```

#### 3. get_lot

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
        - Purchase splits (positive quantity)
        - Sale splits (negative quantity)
        - Current quantity and cost basis
    """
```

#### 4. assign_split_to_lot

```python
@mcp.tool()
def assign_split_to_lot(
    split_guid: str,
    lot_guid: str,
) -> str:
    """Assign a transaction split to a lot.

    Use this after creating a purchase transaction to assign
    the investment split to its cost basis lot.

    Args:
        split_guid: GUID of the split (from the transaction).
        lot_guid: GUID of the lot to assign to.

    Returns:
        JSON with status and updated lot quantity.

    Example workflow:
        1. create_lot("Assets:401k:VTSAX", "VTSAX Jan 2026")
        2. create_transaction(...) # Buy 10 shares
        3. assign_split_to_lot(purchase_split_guid, lot_guid)
    """
```

#### 5. sell_from_lot

```python
@mcp.tool()
def sell_from_lot(
    account: str,
    lot_guid: str,
    shares: str,
    proceeds_account: str,
    sale_date: str | None = None,
    description: str = "Sale",
) -> str:
    """Sell shares from a specific lot.

    Creates a sale transaction and calculates the capital gain/loss
    based on the lot's cost basis.

    Args:
        account: Investment account (e.g., "Assets:401k:VTSAX").
        lot_guid: Lot to sell from.
        shares: Number of shares to sell (as string, e.g., "5.0").
        proceeds_account: Where sale proceeds go (e.g., "Assets:Checking").
        sale_date: Date of sale. Defaults to today.
        description: Transaction description.

    Returns:
        JSON with:
        - transaction_guid
        - shares_sold
        - proceeds (calculated from current price)
        - cost_basis (from lot)
        - gain_loss
        - lot_is_closed (True if lot fully sold)

    Note:
        Requires a current price for the commodity. Use create_price
        to record the sale price first if needed.
    """
```

#### 6. calculate_unrealized_gains

```python
@mcp.tool()
def calculate_unrealized_gains(
    account: str,
) -> str:
    """Calculate unrealized gains/losses for an investment account.

    Compares current market value (quantity × latest price) against
    cost basis (sum of lot costs) for all open lots.

    Args:
        account: Investment account path.

    Returns:
        JSON with:
        - total_shares
        - total_cost_basis
        - current_price
        - current_value
        - unrealized_gain_loss
        - unrealized_gain_loss_percent
        - lots: breakdown by lot
    """
```

### Implementation Notes

**Lot-Split Relationship:**

In piecash, `Split.lot` is a direct relationship. Assigning a split to a lot:

```python
split.lot = lot
book.save()
```

**Cost Basis Calculation:**

```python
def lot_cost_basis(lot: Lot) -> tuple[Decimal, Decimal]:
    """Calculate remaining shares and cost basis for a lot."""
    total_quantity = Decimal(0)
    total_value = Decimal(0)
    
    for split in lot.splits:
        total_quantity += split.quantity
        total_value += split.value
    
    return total_quantity, total_value
```

**Gain/Loss on Sale:**

```python
def calculate_gain(lot: Lot, shares_sold: Decimal, sale_proceeds: Decimal) -> Decimal:
    """Calculate capital gain/loss for a sale."""
    remaining_shares, remaining_cost = lot_cost_basis(lot)
    
    if shares_sold > remaining_shares:
        raise ValueError(f"Cannot sell {shares_sold} shares; lot only has {remaining_shares}")
    
    # Cost basis of shares being sold (proportional)
    cost_per_share = remaining_cost / remaining_shares
    cost_basis_sold = cost_per_share * shares_sold
    
    return sale_proceeds - cost_basis_sold
```

---

## Usage Examples

### Scheduled Transactions

**Set up monthly rent:**
```
User: Set up my rent as a recurring bill. $1,850 on the 1st of every month.

Claude: [calls create_scheduled_transaction]
{
    "name": "Monthly Rent",
    "description": "Rent payment",
    "splits": [
        {"account": "Expenses:Rent", "amount": "1850.00"},
        {"account": "Assets:Current Assets:Checking Account", "amount": "-1850.00"}
    ],
    "start_date": "2026-02-01",
    "frequency": "monthly"
}

Created scheduled transaction "Monthly Rent" — $1,850 monthly, next due Feb 1.
```

**What's coming up?**
```
User: What bills are coming up in the next two weeks?

Claude: [calls get_upcoming_transactions(days=14)]

Upcoming in the next 14 days:
- Feb 1 (3 days): Monthly Rent — $1,850
- Feb 5 (7 days): Spotify — $10.99
- Feb 10 (12 days): Electric (estimated) — $85
Total: $1,945.99
```

### Lots (Cost Basis)

**Buy shares with lot tracking:**
```
User: I bought $1,000 of VTSAX at $125/share in my 401k. Track the cost basis.

Claude: [calls create_price] — Record $125 NAV
        [calls create_lot] — "VTSAX 2026-02-07 purchase"
        [calls create_transaction] — $1,000 purchase = 8 shares
        [calls assign_split_to_lot] — Link purchase to lot

Created lot "VTSAX 2026-02-07 purchase":
- 8 shares at $125.00/share
- Cost basis: $1,000.00
```

**Check unrealized gains:**
```
User: How is my VTSAX doing?

Claude: [calls calculate_unrealized_gains("Assets:Investments:401k:VTSAX")]

VTSAX Holdings:
- 8 shares
- Cost basis: $1,000.00 ($125.00/share)
- Current price: $128.75
- Current value: $1,030.00
- Unrealized gain: +$30.00 (+3.0%)
```

**Sell shares:**
```
User: Sell 3 shares of VTSAX from that lot.

Claude: [calls sell_from_lot]

Sold 3 shares of VTSAX:
- Proceeds: $386.25 (3 × $128.75)
- Cost basis: $375.00 (3 × $125.00)
- Capital gain: +$11.25
- Remaining in lot: 5 shares
```

---

## Testing Strategy

### Scheduled Transaction Tests

1. **test_create_scheduled_monthly** — Monthly rent
2. **test_create_scheduled_biweekly** — Biweekly paycheck
3. **test_create_scheduled_with_end** — 12-month lease
4. **test_list_scheduled** — Returns all with next dates
5. **test_get_upcoming** — Filters by days ahead
6. **test_create_from_scheduled** — Generate real transaction
7. **test_update_scheduled_disable** — Disable a schedule
8. **test_delete_scheduled** — Remove schedule

### Lot Tests

1. **test_create_lot** — Create lot for VTSAX
2. **test_list_lots** — Shows open lots only by default
3. **test_assign_split_to_lot** — Purchase goes into lot
4. **test_lot_quantity** — Tracks share count correctly
5. **test_sell_from_lot** — Sale reduces lot quantity
6. **test_sell_from_lot_gain** — Calculates gain correctly
7. **test_sell_from_lot_loss** — Calculates loss correctly
8. **test_lot_closes** — Lot marked closed when fully sold
9. **test_unrealized_gains** — Calculates from price difference
10. **test_multiple_lots** — Different cost bases tracked separately

---

## Tool Summary

### Scheduled Transactions (6 tools)

| Tool | Classification | Purpose |
|------|----------------|---------|
| create_scheduled_transaction | write | Create recurring template |
| list_scheduled_transactions | read | List all schedules |
| get_upcoming_transactions | read | What's due soon? |
| create_transaction_from_scheduled | write | Trigger a scheduled txn |
| update_scheduled_transaction | write | Enable/disable |
| delete_scheduled_transaction | write | Remove schedule |

### Lots (6 tools)

| Tool | Classification | Purpose |
|------|----------------|---------|
| create_lot | write | Create cost basis lot |
| list_lots | read | List lots for account |
| get_lot | read | Lot details with splits |
| assign_split_to_lot | write | Link purchase to lot |
| sell_from_lot | write | Sell shares, calc gain |
| calculate_unrealized_gains | read | Portfolio performance |

**Total new tools:** 12

---

## Future Considerations (Out of Scope)

### Scheduled Transactions
- Auto-create daemon (background job)
- Variable amount templates (formulas)
- Weekend adjustment (skip/advance)
- GnuCash GUI sync (full template account compatibility)

### Lots
- FIFO/LIFO automatic lot selection
- Wash sale tracking
- Long-term vs short-term gain classification
- Tax lot optimization suggestions
- Import from brokerage statements

---

## Appendix: GnuCash Scheduled Transaction Internals

For reference, here's the full GnuCash data model. This spec intentionally simplifies it.

**Tables involved:**
- `schedxactions` — Main scheduled transaction record
- `recurrences` — Recurrence rules (can be multiple per schedule)
- `slots` — Template split values stored as key-value pairs
- `accounts` — Template accounts under hidden "Template Root"
- `splits` — Template splits (values in slots, not split fields)

**Why it's complex:**
1. Template splits don't use normal split.value — they use slots
2. Recurrence supports complex rules (every 3rd Tuesday, etc.)
3. Multiple recurrence objects per schedule (for exceptions)
4. GnuCash tracks "since last run" state for auto-create

Our simplified model covers: fixed amounts, simple frequencies, manual or on-demand creation. That's 90% of personal finance use cases.
