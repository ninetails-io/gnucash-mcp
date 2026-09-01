# Scheduled Transactions & Budgets Specification
## GnuCash MCP Server Enhancement

**Author:** the bookkeeper  
**Date:** 2026-02-07  
**Status:** Draft  
**Version:** 1.0 Roadmap  
**Prerequisites:** Core transaction support (implemented)

---

## Executive Summary

Scheduled transactions and budgets are the planning layer of personal finance. Scheduled transactions answer "what's coming up?" — recurring bills, paychecks, subscriptions. Budgets answer "how much should I spend?" — targets per category that actual spending is measured against.

Together they enable:
- "What bills are due this week?"
- "Am I on track with my grocery budget?"
- "How much of my paycheck is already spoken for?"

---

## Part 1: Scheduled Transactions

### Background: How GnuCash Models Scheduled Transactions

A scheduled transaction is a template that generates real transactions on a schedule.

**Core attributes (from piecash):**

```python
ScheduledTransaction(
    name="Monthly Rent",
    enabled=True,
    
    # Schedule
    start_date=datetime(2026, 1, 1),
    end_date=None,                    # Or a specific end date
    
    # Frequency (via recurrence)
    # GnuCash uses a separate Recurrence object, but we can simplify
    
    # Occurrence limits (alternative to end_date)
    num_occur=0,                      # Total occurrences (0 = unlimited)
    rem_occur=0,                      # Remaining occurrences
    
    # Automation
    auto_create=True,                 # Auto-create transactions
    auto_notify=False,                # Notify when due
    adv_creation=0,                   # Days to create in advance
    adv_notify=7,                     # Days to notify in advance
    
    # Tracking
    last_occur=datetime(2026, 1, 1),  # Last occurrence date
    instance_count=1,                 # How many have been created
    
    # Template
    template_account=Account(...),    # Special account holding split templates
)
```

**The Template Account:**

GnuCash stores scheduled transaction splits in a special "template" account hierarchy. Each scheduled transaction has a `template_account` containing `SplitTemplate` objects with:
- Account reference
- Value (as a formula or fixed amount)
- Memo

### Proposed Tools

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
    num_occurrences: int | None = None,
    auto_create: bool = False,
    notify_days: int = 7,
    enabled: bool = True,
) -> str:
    """Create a recurring transaction template.

    Args:
        name: Name for the scheduled transaction (e.g., "Monthly Rent").
        description: Transaction description when created.
        splits: List of splits, same format as create_transaction.
            Each split has 'account', 'amount', and optional 'memo'.
        start_date: First occurrence date (YYYY-MM-DD).
        frequency: How often it recurs:
            - "daily"
            - "weekly"
            - "biweekly" (every 2 weeks)
            - "monthly"
            - "quarterly" (every 3 months)
            - "yearly"
        end_date: Optional end date (YYYY-MM-DD). Mutually exclusive with num_occurrences.
        num_occurrences: Optional total number of occurrences. Mutually exclusive with end_date.
        auto_create: If True, transactions are created automatically when due.
        notify_days: Days in advance to flag as upcoming. Default 7.
        enabled: Whether the schedule is active. Default True.

    Returns:
        JSON with guid, name, next_occurrence, and status.

    Example:
        Monthly rent of $1,850:
        {
            "name": "Monthly Rent",
            "description": "Rent payment",
            "splits": [
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"}
            ],
            "start_date": "2026-02-01",
            "frequency": "monthly"
        }
    """
```

#### 2. list_scheduled_transactions

```python
@mcp.tool()
def list_scheduled_transactions(
    enabled_only: bool = True,
    include_upcoming: bool = True,
    days_ahead: int = 30,
) -> str:
    """List all scheduled transactions.

    Args:
        enabled_only: If True, only show enabled schedules. Default True.
        include_upcoming: If True, calculate next occurrence dates. Default True.
        days_ahead: For upcoming calculation, how many days to look ahead. Default 30.

    Returns:
        JSON list of scheduled transactions with:
        - guid, name, description
        - frequency, start_date, end_date
        - enabled, auto_create
        - last_occurrence, next_occurrence
        - instance_count (how many have been created)
        - upcoming flag (True if next_occurrence within days_ahead)
    """
```

#### 3. get_scheduled_transaction

```python
@mcp.tool()
def get_scheduled_transaction(
    guid: str,
) -> str:
    """Get details of a specific scheduled transaction.

    Args:
        guid: Scheduled transaction GUID.

    Returns:
        JSON with full details including split templates.
    """
```

#### 4. get_upcoming_transactions

```python
@mcp.tool()
def get_upcoming_transactions(
    days: int = 14,
    account: str | None = None,
) -> str:
    """Get scheduled transactions due within a time window.

    This is the "what bills are coming up?" query.

    Args:
        days: Look ahead window in days. Default 14.
        account: Optional filter by account (e.g., "Assets:Checking").

    Returns:
        JSON list of upcoming occurrences with:
        - scheduled_transaction guid and name
        - occurrence_date
        - description
        - amount (net flow for filtered account, or total if no filter)
        - splits summary

    Example response:
        [
            {
                "name": "Monthly Rent",
                "occurrence_date": "2026-02-01",
                "amount": "-1850.00",
                "days_until": 3
            },
            {
                "name": "Spotify",
                "occurrence_date": "2026-02-05",
                "amount": "-10.99",
                "days_until": 7
            }
        ]
    """
```

#### 5. create_transaction_from_scheduled

```python
@mcp.tool()
def create_transaction_from_scheduled(
    guid: str,
    transaction_date: str | None = None,
    adjust_amounts: dict | None = None,
) -> str:
    """Create an actual transaction from a scheduled template.

    Use this to manually trigger a scheduled transaction, optionally
    adjusting amounts (e.g., for variable bills like utilities).

    Args:
        guid: Scheduled transaction GUID.
        transaction_date: Date for the transaction. Defaults to today.
        adjust_amounts: Optional dict mapping account names to new amounts.
            Unspecified accounts use template amounts.
            Must still balance to zero.

    Returns:
        JSON with created transaction guid and updated schedule info.

    Example:
        Create this month's electric bill (variable amount):
        {
            "guid": "abc123...",
            "adjust_amounts": {
                "Expenses:Utilities:Electric": "87.50",
                "Assets:Checking": "-87.50"
            }
        }
    """
```

#### 6. update_scheduled_transaction

```python
@mcp.tool()
def update_scheduled_transaction(
    guid: str,
    name: str | None = None,
    description: str | None = None,
    enabled: bool | None = None,
    end_date: str | None = None,
    auto_create: bool | None = None,
    notify_days: int | None = None,
) -> str:
    """Update a scheduled transaction's settings.

    Args:
        guid: Scheduled transaction GUID.
        name: New name.
        description: New description.
        enabled: Enable or disable the schedule.
        end_date: Set or clear end date (empty string to clear).
        auto_create: Change auto-create setting.
        notify_days: Change notification window.

    Returns:
        JSON with updated scheduled transaction.

    Note:
        To modify splits or frequency, delete and recreate.
    """
```

#### 7. delete_scheduled_transaction

```python
@mcp.tool()
def delete_scheduled_transaction(
    guid: str,
) -> str:
    """Delete a scheduled transaction.

    Does not affect transactions already created from this schedule.

    Args:
        guid: Scheduled transaction GUID.

    Returns:
        JSON with status.
    """
```

---

## Part 2: Budgets

### Background: How GnuCash Models Budgets

A budget is a named collection of target amounts per account per period.

**Core objects (from piecash):**

```python
Budget(
    name="2026 Budget",
    description="Annual household budget",
    num_periods=12,           # Number of budget periods
    recurrence=Recurrence(),  # Period length (typically monthly)
)

BudgetAmount(
    budget=budget,
    account=account,
    period_num=0,             # 0-indexed period (0 = first month)
    amount=Decimal("500.00"), # Budgeted amount for this account/period
)
```

**Period Model:**

GnuCash budgets divide time into periods (usually 12 months). Each `BudgetAmount` specifies a target for one account in one period.

### Proposed Tools

#### 1. create_budget

```python
@mcp.tool()
def create_budget(
    name: str,
    year: int | None = None,
    num_periods: int = 12,
    period_type: str = "monthly",
    description: str = "",
) -> str:
    """Create a new budget.

    Args:
        name: Budget name (e.g., "2026 Budget").
        year: Budget year. Defaults to current year. Used to set start date.
        num_periods: Number of periods. Default 12 (monthly for a year).
        period_type: Period length:
            - "monthly" (default)
            - "quarterly"
            - "weekly"
        description: Optional description.

    Returns:
        JSON with guid, name, and status.
    """
```

#### 2. set_budget_amount

```python
@mcp.tool()
def set_budget_amount(
    budget_name: str,
    account: str,
    amount: str,
    period: int | str | None = None,
) -> str:
    """Set a budget target for an account.

    Args:
        budget_name: Name of the budget.
        account: Full account path (e.g., "Expenses:Groceries").
        amount: Monthly budget amount as string (e.g., "500.00").
        period: Which period(s) to set:
            - None or "all": Set same amount for all periods (default)
            - Integer 0-11: Set specific period (0 = January for yearly budget)
            - "q1", "q2", "q3", "q4": Set all periods in quarter

    Returns:
        JSON with account, amount, and periods affected.

    Example:
        Set $500/month grocery budget for all of 2026:
        {
            "budget_name": "2026 Budget",
            "account": "Expenses:Groceries",
            "amount": "500.00"
        }

        Set higher December budget for gifts:
        {
            "budget_name": "2026 Budget",
            "account": "Expenses:Gifts",
            "amount": "300.00",
            "period": 11
        }
    """
```

#### 3. get_budget_report

```python
@mcp.tool()
def get_budget_report(
    budget_name: str,
    period: int | str | None = None,
    account: str | None = None,
    include_children: bool = True,
) -> str:
    """Compare actual spending against budget.

    This is the "how am I doing?" query.

    Args:
        budget_name: Name of the budget.
        period: Which period to report:
            - None: Current period based on today's date (default)
            - Integer 0-11: Specific period
            - "ytd": Year to date (all periods up to current)
            - "all": All periods
        account: Optional filter to specific account or parent account.
        include_children: If True and account specified, include child accounts.

    Returns:
        JSON with:
        - period_info: which period(s) covered
        - accounts: list of {account, budgeted, actual, remaining, percent_used}
        - totals: aggregate budgeted, actual, remaining

    Example response:
        {
            "period": "2026-02 (February)",
            "accounts": [
                {
                    "account": "Expenses:Groceries",
                    "budgeted": "500.00",
                    "actual": "327.50",
                    "remaining": "172.50",
                    "percent_used": 65.5
                },
                {
                    "account": "Expenses:Dining",
                    "budgeted": "200.00",
                    "actual": "245.00",
                    "remaining": "-45.00",
                    "percent_used": 122.5
                }
            ],
            "totals": {
                "budgeted": "700.00",
                "actual": "572.50",
                "remaining": "127.50",
                "percent_used": 81.8
            }
        }
    """
```

#### 4. list_budgets

```python
@mcp.tool()
def list_budgets() -> str:
    """List all budgets in the book.

    Returns:
        JSON list of budgets with guid, name, description,
        num_periods, and period_type.
    """
```

#### 5. get_budget

```python
@mcp.tool()
def get_budget(
    name: str,
) -> str:
    """Get full details of a budget including all budget amounts.

    Args:
        name: Budget name.

    Returns:
        JSON with budget info and all account/period amounts.
    """
```

#### 6. delete_budget

```python
@mcp.tool()
def delete_budget(
    name: str,
) -> str:
    """Delete a budget.

    Args:
        name: Budget name.

    Returns:
        JSON with status.
    """
```

---

## Part 3: Combined Workflows

### "What's my financial picture this month?"

```
User: What's coming up this month and how am I doing on my budget?

Claude: [calls get_upcoming_transactions(days=30)]
        [calls get_budget_report(budget_name="2026 Budget")]

You have 5 bills coming up in the next 30 days totaling $2,847:
- Feb 1: Rent ($1,850)
- Feb 5: Spotify ($10.99)
- Feb 10: Electric (~$85 estimated)
- Feb 15: Car Insurance ($156)
- Feb 28: Internet ($75)

Budget status for February:
- Groceries: $327 of $500 (65%) — $173 remaining
- Dining: $245 of $200 (122%) — $45 over budget ⚠️
- Entertainment: $45 of $100 (45%) — $55 remaining

You're on track overall but watch the dining spending.
```

### "Set up my recurring bills"

```
User: Set up my recurring bills: rent is $1,850 on the 1st, 
      car insurance is $156 quarterly starting March 1.

Claude: [calls create_scheduled_transaction for rent]
        [calls create_scheduled_transaction for insurance]

Created 2 scheduled transactions:
1. "Monthly Rent" - $1,850 monthly, next: Feb 1
2. "Car Insurance" - $156 quarterly, next: Mar 1
```

### "Create my annual budget"

```
User: Create a 2026 budget. Groceries $500/month, dining $200/month,
      entertainment $100/month, gifts $50/month but $300 in December.

Claude: [calls create_budget(name="2026 Budget", year=2026)]
        [calls set_budget_amount for each category]
        [calls set_budget_amount for December gifts override]

Created "2026 Budget" with:
- Groceries: $500/month ($6,000/year)
- Dining: $200/month ($2,400/year)
- Entertainment: $100/month ($1,200/year)
- Gifts: $50/month, $300 in December ($850/year)

Total annual budget: $10,450
```

---

## Implementation Notes

### Scheduled Transaction Complexity

GnuCash's scheduled transaction model is complex:
- Splits are stored in a special template account hierarchy
- Recurrence is a separate object with multiplier and period type
- Supports formulas in amounts (we'll skip this for v1)

**Simplification for MCP:**
- Store frequency as a simple string, map to GnuCash recurrence internally
- Fixed amounts only (no formulas)
- Hide template account complexity from users

### Budget Period Mapping

GnuCash budgets are period-indexed (0, 1, 2...). We need to map to human-friendly periods:

```python
def period_to_date_range(budget, period_num):
    """Convert period number to date range."""
    # For monthly budget starting Jan 1, 2026:
    # period 0 = Jan 1-31, period 1 = Feb 1-28, etc.
    start = budget.start_date + relativedelta(months=period_num)
    end = start + relativedelta(months=1) - timedelta(days=1)
    return start, end

def current_period(budget):
    """Get current period number based on today."""
    today = date.today()
    delta = relativedelta(today, budget.start_date)
    return delta.months + (delta.years * 12)
```

### Budget vs Actual Calculation

The `get_budget_report` tool needs to:
1. Get budgeted amounts from BudgetAmount table
2. Calculate actual spending from transactions in the period
3. Compare and compute remaining/percent

This reuses logic from `spending_by_category` but filtered to budget periods.

---

## Testing Strategy

### Scheduled Transaction Tests

1. **test_create_scheduled_monthly** — Create rent, verify recurrence
2. **test_create_scheduled_biweekly** — Paycheck every 2 weeks
3. **test_create_scheduled_with_end_date** — 12-month lease
4. **test_create_scheduled_with_occurrences** — "10 payments"
5. **test_list_scheduled_transactions** — Returns all with next dates
6. **test_get_upcoming_transactions** — Filters by days ahead
7. **test_create_from_scheduled** — Generate real transaction
8. **test_create_from_scheduled_adjusted** — Variable amount bill
9. **test_update_scheduled_disable** — Disable a schedule
10. **test_delete_scheduled** — Remove schedule, txns remain

### Budget Tests

1. **test_create_budget** — Create 2026 monthly budget
2. **test_set_budget_all_periods** — $500 groceries all year
3. **test_set_budget_single_period** — December gifts override
4. **test_get_budget_report_current** — This month's status
5. **test_get_budget_report_ytd** — Year to date rollup
6. **test_get_budget_report_over_budget** — Shows negative remaining
7. **test_get_budget_report_no_transactions** — Empty period
8. **test_list_budgets** — Multiple budgets
9. **test_delete_budget** — Remove budget

### Integration Tests

1. Full workflow: create schedule → time passes → create from schedule → verify in budget report
2. Budget with child accounts: set on parent, verify children roll up
3. Multiple budgets for same year (personal vs household)

---

## Tool Summary

### Scheduled Transactions (7 tools)

| Tool | Classification | Purpose |
|------|----------------|---------|
| create_scheduled_transaction | write | Create recurring template |
| list_scheduled_transactions | read | List all schedules |
| get_scheduled_transaction | read | Get schedule details |
| get_upcoming_transactions | read | What's due soon? |
| create_transaction_from_scheduled | write | Trigger a scheduled txn |
| update_scheduled_transaction | write | Modify settings |
| delete_scheduled_transaction | write | Remove schedule |

### Budgets (6 tools)

| Tool | Classification | Purpose |
|------|----------------|---------|
| create_budget | write | Create annual/periodic budget |
| set_budget_amount | write | Set target for account/period |
| get_budget_report | read | Compare actual vs budget |
| list_budgets | read | List all budgets |
| get_budget | read | Get budget details |
| delete_budget | write | Remove budget |

**Total new tools:** 13

---

## Future Considerations (Out of Scope)

1. **Formula-based amounts** — GnuCash supports formulas like "last month + 5%"
2. **Auto-create daemon** — Background process to create due transactions
3. **Budget alerts** — Push notification when over budget
4. **Budget templates** — Pre-built budget structures (50/30/20, etc.)
5. **Forecast** — Project future balances based on schedules
6. **Recurring transfers** — Scheduled transactions between accounts (savings goals)

---

## Appendix: Piecash Recurrence Mapping

GnuCash stores recurrence as:
- `mult`: multiplier (e.g., 2 for "every 2 weeks")
- `period_type`: "month", "week", "day", "year"
- `period_start`: anchor date

Our frequency strings map to:

| Frequency | mult | period_type |
|-----------|------|-------------|
| daily | 1 | day |
| weekly | 1 | week |
| biweekly | 2 | week |
| monthly | 1 | month |
| quarterly | 3 | month |
| yearly | 1 | year |

The `period_start` is derived from `start_date`.
