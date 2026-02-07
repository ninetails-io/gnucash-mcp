"""MCP server definition for GnuCash."""

import json
import logging
import os
import sys
import traceback
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Callable

from mcp.server.fastmcp import FastMCP

from gnucash_mcp.book import GnuCashBook, GnuCashLockError
from gnucash_mcp.logging_config import audit_log, debug_log, get_log_dir, setup_logging

# Set up logging
logger = logging.getLogger(__name__)

# Create FastMCP server
mcp = FastMCP("gnucash-mcp")

# Global book instance - initialized on first use
_book: GnuCashBook | None = None


def get_book() -> GnuCashBook:
    """Get or create the GnuCashBook instance."""
    global _book
    if _book is None:
        path = os.environ.get("GNUCASH_BOOK_PATH")
        if not path:
            raise ValueError("GNUCASH_BOOK_PATH environment variable not set")
        _book = GnuCashBook(path)
    return _book


# Initialize logging at module import time
# Use GNUCASH_MCP_DEBUG=1 env var to enable debug logging
# Use GNUCASH_MCP_NOAUDIT=1 env var to disable audit logging
# Use GNUCASH_MCP_AUDIT_FORMAT=json|text env var to set audit format (default: text)
# Logs are stored alongside the book file: {book_path}.mcp/audit/ and {book_path}.mcp/debug/
_debug_mode = os.environ.get("GNUCASH_MCP_DEBUG") == "1"
_audit_mode = os.environ.get("GNUCASH_MCP_NOAUDIT") != "1"
_audit_format = os.environ.get("GNUCASH_MCP_AUDIT_FORMAT", "text")
_book_path = os.environ.get("GNUCASH_BOOK_PATH")
if _book_path and (_audit_mode or _debug_mode):
    setup_logging(
        book_path=_book_path,
        debug=_debug_mode,
        audit=_audit_mode,
        audit_format=_audit_format,
        get_book=get_book,
    )
    if _debug_mode:
        debug_log(f"Server module loaded. Book path: {_book_path}")


def safe_tool(func: Callable) -> Callable:
    """Decorator that wraps tool functions with comprehensive error handling.

    Catches all exceptions and returns them as JSON error responses instead of
    crashing the MCP server.
    """

    @wraps(func)
    def wrapper(*args, **kwargs) -> str:
        try:
            return func(*args, **kwargs)
        except GnuCashLockError as e:
            logger.warning(f"Lock error in {func.__name__}: {e}")
            return json.dumps(
                {
                    "error": str(e),
                    "error_type": "lock_error",
                    "suggestion": "Close GnuCash application and try again.",
                }
            )
        except FileNotFoundError as e:
            logger.error(f"File not found in {func.__name__}: {e}")
            return json.dumps(
                {
                    "error": str(e),
                    "error_type": "file_not_found",
                    "suggestion": "Check that GNUCASH_BOOK_PATH is set correctly.",
                }
            )
        except ValueError as e:
            logger.warning(f"Validation error in {func.__name__}: {e}")
            return json.dumps({"error": str(e), "error_type": "validation_error"})
        except Exception as e:
            # Catch-all for unexpected errors
            logger.error(
                f"Unexpected error in {func.__name__}: {e}\n{traceback.format_exc()}"
            )
            return json.dumps(
                {
                    "error": f"Unexpected error: {type(e).__name__}: {e}",
                    "error_type": "unexpected_error",
                }
            )

    return wrapper


# ============== Tools ==============


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_accounts() -> str:
    """List all accounts in the GnuCash chart of accounts."""
    book = get_book()
    result = book.list_accounts()
    return json.dumps(result, indent=2)


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


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="commodity")
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
    """
    book = get_book()
    result = book.create_commodity(
        mnemonic=mnemonic,
        fullname=fullname,
        namespace=namespace,
        fraction=fraction,
        cusip=cusip,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="price")
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

    Note:
        If a price already exists for the same commodity/currency/date/source,
        it will be updated rather than creating a duplicate.
    """
    book = get_book()
    price_date = None
    if date:
        from datetime import date as date_type

        price_date = date_type.fromisoformat(date)

    result = book.create_price(
        commodity=commodity,
        namespace=namespace,
        value=value,
        currency=currency,
        price_date=price_date,
        price_type=price_type,
        source=source,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
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
        JSON with list of prices sorted by date descending (most recent first).
    """
    book = get_book()
    from datetime import date as date_type

    start = date_type.fromisoformat(start_date) if start_date else None
    end = date_type.fromisoformat(end_date) if end_date else None

    result = book.get_prices(
        commodity=commodity,
        namespace=namespace,
        start_date=start,
        end_date=end,
        currency=currency,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
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
    book = get_book()
    result = book.get_latest_price(
        commodity=commodity,
        namespace=namespace,
        currency=currency,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_account(name: str) -> str:
    """Get details for a specific account by name.

    Args:
        name: Full account name (e.g., 'Assets:Bank:Checking')
    """
    book = get_book()
    result = book.get_account(name)
    if result is None:
        return json.dumps({"error": f"Account not found: {name}"})
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_balance(account_name: str, as_of_date: str | None = None) -> str:
    """Get the balance of an account, optionally as of a specific date.

    Args:
        account_name: Full account name (e.g., 'Assets:Bank:Checking')
        as_of_date: Date in ISO format (YYYY-MM-DD). Defaults to current date.
    """
    book = get_book()
    date_obj = date.fromisoformat(as_of_date) if as_of_date else None
    balance = book.get_balance(account_name, date_obj)
    result = {
        "account": account_name,
        "balance": str(balance),
        "as_of_date": as_of_date if as_of_date else "current",
    }
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_transactions(
    account: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 50,
) -> str:
    """List transactions with optional filters.

    Args:
        account: Filter by account name
        start_date: Start date in ISO format (YYYY-MM-DD)
        end_date: End date in ISO format (YYYY-MM-DD)
        limit: Maximum number of transactions to return (default 50)
    """
    book = get_book()
    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    result = book.list_transactions(account, start, end, limit)
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_transaction(guid: str) -> str:
    """Get details for a specific transaction by GUID.

    Args:
        guid: Transaction GUID (32-character hex string)
    """
    book = get_book()
    result = book.get_transaction(guid)
    if result is None:
        return json.dumps({"error": f"Transaction not found: {guid}"})
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="transaction")
def create_transaction(
    description: str,
    splits: list[dict],
    transaction_date: str | None = None,
    currency: str | None = None,
    notes: str | None = None,
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
        notes: Transaction notes (optional). Free-text annotation stored
               separately from description.
    """
    book = get_book()
    trans_date = date.fromisoformat(transaction_date) if transaction_date else None
    guid = book.create_transaction(
        description=description,
        splits=splits,
        trans_date=trans_date,
        currency=currency,
        notes=notes,
    )
    return json.dumps({"guid": guid, "status": "created"}, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def search_transactions(query: str, field: str = "description") -> str:
    """Search transactions by description, memo, notes, or amount.

    Args:
        query: Search query string. For amount, supports: exact ("100"), greater (">100"), less ("<100"), range ("100-200")
        field: Field to search: 'description', 'memo', 'notes', or 'amount'
    """
    book = get_book()
    result = book.search_transactions(query, field)
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="account")
def create_account(
    name: str,
    account_type: str,
    parent: str,
    description: str = "",
    placeholder: bool = False,
    commodity: str | None = None,
    commodity_namespace: str = "CURRENCY",
) -> str:
    """Create a new account in the chart of accounts.

    Args:
        name: Account name (e.g., "AI Subscriptions")
        account_type: GnuCash account type (ASSET, BANK, CASH, CREDIT, EQUITY, EXPENSE, INCOME, LIABILITY, MUTUAL, STOCK)
        parent: Full path of parent account (e.g., "Expenses:Online Services")
        description: Optional description
        placeholder: If true, account is container-only. Default: false
        commodity: Symbol for the account's commodity:
            - For currencies: ISO code (e.g., "USD", "EUR")
            - For investments: Fund/stock symbol (e.g., "VTSAX", "AAPL")
            Defaults to book's default currency.
        commodity_namespace: Namespace of the commodity:
            - "CURRENCY" (default) for currencies
            - "FUND" for mutual funds
            - "NASDAQ", "NYSE", etc. for stocks
            Required when commodity is not a currency.
    """
    book = get_book()
    result = book.create_account(
        name=name,
        account_type=account_type,
        parent=parent,
        description=description,
        placeholder=placeholder,
        commodity=commodity,
        commodity_namespace=commodity_namespace,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="update", entity_type="account")
def update_account(
    name: str,
    new_name: str | None = None,
    description: str | None = None,
    placeholder: bool | None = None,
) -> str:
    """Update an existing account's properties.

    Args:
        name: Full account path to update (e.g., "Expenses:Groceries")
        new_name: New name for the account (just the leaf name, not full path)
        description: New description
        placeholder: New placeholder status (true = container only)
    """
    book = get_book()
    result = book.update_account(
        name=name,
        new_name=new_name,
        description=description,
        placeholder=placeholder,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="update", entity_type="account")
def move_account(name: str, new_parent: str) -> str:
    """Move an account to a new parent in the hierarchy.

    Args:
        name: Full account path to move (e.g., "Expenses:Old:Account")
        new_parent: Full path of the new parent account (e.g., "Expenses:New")
    """
    book = get_book()
    result = book.move_account(name=name, new_parent=new_parent)
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="delete", entity_type="account")
def delete_account(name: str) -> str:
    """Delete an account from the chart of accounts.

    Safeguards prevent deletion if the account has children or transactions.

    Args:
        name: Full account path to delete (e.g., "Expenses:Old Category")
    """
    book = get_book()
    result = book.delete_account(name=name)
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="delete", entity_type="transaction")
def delete_transaction(guid: str) -> str:
    """Delete a transaction by GUID.

    Args:
        guid: Transaction GUID (32-character hex string)
    """
    book = get_book()
    result = book.delete_transaction(guid)
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="update", entity_type="transaction")
def update_transaction(
    guid: str,
    description: str | None = None,
    transaction_date: str | None = None,
    splits: list[dict] | None = None,
    notes: str | None = None,
) -> str:
    """Update an existing transaction.

    Args:
        guid: Transaction GUID to update
        description: New transaction description (optional)
        transaction_date: New date in ISO format YYYY-MM-DD (optional)
        splits: List of split updates with 'account' and 'amount' (optional).
                Must match existing splits by account name and balance to zero.
                For cross-currency splits, include 'quantity' (amount in account's commodity).
        notes: New transaction notes (optional). Pass empty string to clear.
    """
    book = get_book()
    trans_date = date.fromisoformat(transaction_date) if transaction_date else None
    result = book.update_transaction(
        guid=guid,
        description=description,
        trans_date=trans_date,
        splits=splits,
        notes=notes,
    )
    return json.dumps(result, indent=2)


# ============== Reconciliation Tools ==============


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="set_state", entity_type="split")
def set_reconcile_state(
    split_guid: str,
    state: str,
    reconcile_date: str | None = None,
) -> str:
    """Set the reconciliation state for a split.

    Args:
        split_guid: GUID of the split to update
        state: New reconcile state: 'n' (new), 'c' (cleared), 'y' (reconciled)
        reconcile_date: Date in ISO format (YYYY-MM-DD). Required for 'y', defaults to today.
    """
    book = get_book()
    rec_date = date.fromisoformat(reconcile_date) if reconcile_date else None
    result = book.set_reconcile_state(
        split_guid=split_guid,
        state=state,
        reconcile_date=rec_date,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_unreconciled_splits(
    account: str,
    as_of_date: str | None = None,
) -> str:
    """Get all unreconciled splits for an account.

    Args:
        account: Full account name (e.g., 'Assets:Bank:Checking')
        as_of_date: Only include splits on or before this date (YYYY-MM-DD)
    """
    book = get_book()
    date_obj = date.fromisoformat(as_of_date) if as_of_date else None
    result = book.get_unreconciled_splits(
        account_name=account,
        as_of_date=date_obj,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="reconcile", entity_type="split")
def reconcile_account(
    account: str,
    statement_date: str,
    statement_balance: str,
    split_guids: list[str],
) -> str:
    """Reconcile multiple splits against a statement balance.

    Marks all specified splits as reconciled if the resulting balance matches
    the statement balance. This is an atomic operation - either all splits are
    reconciled or none are.

    Args:
        account: Full account name (e.g., 'Assets:Bank:Checking')
        statement_date: Statement ending date (YYYY-MM-DD)
        statement_balance: Expected balance from statement (as string, e.g., '1234.56')
        split_guids: List of split GUIDs to mark as reconciled
    """
    book = get_book()
    stmt_date = date.fromisoformat(statement_date)
    result = book.reconcile_account(
        account_name=account,
        statement_date=stmt_date,
        statement_balance=statement_balance,
        split_guids=split_guids,
    )
    return json.dumps(result, indent=2)


# ============== Void Tools ==============


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="void", entity_type="transaction")
def void_transaction(guid: str, reason: str) -> str:
    """Void a transaction (proper accounting void, not delete).

    Voiding preserves the transaction for audit purposes but zeroes out
    all split values. Use this instead of delete when you need to maintain
    an audit trail.

    Args:
        guid: Transaction GUID to void
        reason: Reason for voiding (required for audit trail)
    """
    book = get_book()
    result = book.void_transaction(guid=guid, reason=reason)
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="unvoid", entity_type="transaction")
def unvoid_transaction(guid: str) -> str:
    """Restore a voided transaction.

    Restores original split values and removes void markers.

    Args:
        guid: Transaction GUID to unvoid
    """
    book = get_book()
    result = book.unvoid_transaction(guid=guid)
    return json.dumps(result, indent=2)


# ============== Reporting Tools ==============


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def spending_by_category(
    start_date: str,
    end_date: str,
    depth: int = 1,
) -> str:
    """Get spending breakdown by expense category for a period.

    Args:
        start_date: Start of period (YYYY-MM-DD)
        end_date: End of period (YYYY-MM-DD)
        depth: Hierarchy depth for grouping (1 = top-level categories, 2 = subcategories)
    """
    book = get_book()
    result = book.spending_by_category(
        start_date=date.fromisoformat(start_date),
        end_date=date.fromisoformat(end_date),
        depth=depth,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def income_by_source(
    start_date: str,
    end_date: str,
    depth: int = 1,
) -> str:
    """Get income breakdown by source for a period.

    Args:
        start_date: Start of period (YYYY-MM-DD)
        end_date: End of period (YYYY-MM-DD)
        depth: Hierarchy depth for grouping (1 = top-level categories, 2 = subcategories)
    """
    book = get_book()
    result = book.income_by_source(
        start_date=date.fromisoformat(start_date),
        end_date=date.fromisoformat(end_date),
        depth=depth,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def balance_sheet(as_of_date: str) -> str:
    """Generate a balance sheet as of a specific date.

    Shows assets, liabilities, and equity with account breakdowns.

    Args:
        as_of_date: Date to calculate balances as of (YYYY-MM-DD)
    """
    book = get_book()
    result = book.balance_sheet(as_of_date=date.fromisoformat(as_of_date))
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def net_worth(
    end_date: str,
    start_date: str | None = None,
    interval: str | None = None,
) -> str:
    """Calculate net worth (assets minus liabilities).

    Can calculate a single point-in-time value or a time series.

    Args:
        end_date: Calculate net worth as of this date (YYYY-MM-DD)
        start_date: Optional start date for time series (YYYY-MM-DD)
        interval: Optional interval for time series: 'month', 'quarter', or 'year'
    """
    book = get_book()
    result = book.net_worth(
        end_date=date.fromisoformat(end_date),
        start_date=date.fromisoformat(start_date) if start_date else None,
        interval=interval,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def cash_flow(
    start_date: str,
    end_date: str,
    account: str | None = None,
) -> str:
    """Calculate cash flow (inflows and outflows) for a period.

    Args:
        start_date: Start of period (YYYY-MM-DD)
        end_date: End of period (YYYY-MM-DD)
        account: Optional specific account to analyze (defaults to all cash/bank accounts)
    """
    book = get_book()
    result = book.cash_flow(
        start_date=date.fromisoformat(start_date),
        end_date=date.fromisoformat(end_date),
        account=account,
    )
    return json.dumps(result, indent=2)


# ============== Resources ==============


@mcp.resource("gnucash://accounts")
def accounts_resource() -> str:
    """Full chart of accounts from the GnuCash book."""
    book = get_book()
    accounts = book.list_accounts()
    return json.dumps(accounts, indent=2)


# ============== Audit Log Tool ==============


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_audit_log(
    log_date: str | None = None,
    tool_filter: str | None = None,
    classification: str | None = None,
    limit: int = 50,
) -> str:
    """Read audit log entries.

    Args:
        log_date: Date to read (YYYY-MM-DD). Defaults to today.
        tool_filter: Filter by tool name.
        classification: Filter by "read" or "write".
        limit: Maximum entries to return (default 50).
    """
    from datetime import datetime, timezone

    log_dir = get_log_dir()
    if not log_dir:
        return json.dumps({"error": "Logging not initialized (no book path configured)"})

    audit_dir = log_dir / "audit"
    target_date = log_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = audit_dir / f"{target_date}.jsonl"

    if not log_file.exists():
        return json.dumps({"entries": [], "message": f"No audit log for {target_date}"})

    entries = []
    for line in log_file.read_text().strip().split("\n"):
        if not line:
            continue
        entry = json.loads(line)
        if tool_filter and entry.get("tool") != tool_filter:
            continue
        if classification and entry.get("classification") != classification:
            continue
        entries.append(entry)

    # Return most recent entries up to limit
    return json.dumps(
        {"entries": entries[-limit:], "total_count": len(entries)}, indent=2
    )


# ============== Main ==============


def main() -> None:
    """Run the MCP server."""
    # Handle --help
    if "--help" in sys.argv or "-h" in sys.argv:
        print("""GnuCash MCP Server

Usage: gnucash-mcp [OPTIONS]

Options:
  --debug                Enable debug logging (MCP protocol traffic, timing)
  --noaudit              Disable audit logging
  --audit-format=FORMAT  Audit log format: "text" (default) or "json"
  -h, --help             Show this help message

Environment variables:
  GNUCASH_BOOK_PATH          Path to GnuCash SQLite book (required)
  GNUCASH_MCP_DEBUG=1        Enable debug logging
  GNUCASH_MCP_NOAUDIT=1      Disable audit logging
  GNUCASH_MCP_AUDIT_FORMAT   Audit format: "text" or "json"

Logs are stored alongside the book file:
  {book_path}.mcp/audit/YYYY-MM-DD.txt   (or .jsonl for JSON format)
  {book_path}.mcp/debug/YYYY-MM-DD.log   (when debug enabled)
""")
        sys.exit(0)

    book_path = os.environ.get("GNUCASH_BOOK_PATH")

    # Parse CLI flags
    debug_flag = "--debug" in sys.argv
    noaudit_flag = "--noaudit" in sys.argv
    audit_format = "text"  # default

    # Parse --audit-format=json or --audit-format=text
    for arg in sys.argv[:]:
        if arg.startswith("--audit-format="):
            audit_format = arg.split("=", 1)[1]
            sys.argv.remove(arg)

    if "--debug" in sys.argv:
        sys.argv.remove("--debug")
    if "--noaudit" in sys.argv:
        sys.argv.remove("--noaudit")

    # Re-init logging if CLI flags override env vars
    if debug_flag or noaudit_flag or audit_format != "text":
        audit_enabled = not noaudit_flag
        if book_path and (audit_enabled or debug_flag):
            setup_logging(
                book_path=book_path,
                debug=debug_flag,
                audit=audit_enabled,
                audit_format=audit_format,
                get_book=get_book,
            )
            if debug_flag:
                debug_log(f"Server starting via CLI. Book: {book_path}")
                debug_log(f"Debug logging enabled, audit={'enabled' if audit_enabled else 'disabled'}, format={audit_format}")

    mcp.run()


if __name__ == "__main__":
    main()
