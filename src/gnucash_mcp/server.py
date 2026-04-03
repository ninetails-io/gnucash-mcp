"""MCP server definition for GnuCash."""

import json
import logging
import os
import sys
import traceback
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Annotated, Callable

from pydantic import Field

from mcp.server.fastmcp import FastMCP

from gnucash_mcp.book import GnuCashBook, GnuCashLockError
from gnucash_mcp.logging_config import audit_log, debug_log, get_audit_format, get_log_dir, setup_logging


def _json(obj) -> str:
    """Serialize to minified JSON, stripping noise values."""
    return json.dumps(_strip_noise(obj), separators=(",", ":"))


def _strip_noise(obj):
    """Recursively remove keys with None or empty-string values from dicts."""
    if isinstance(obj, dict):
        return {k: _strip_noise(v) for k, v in obj.items()
                if v is not None and v != ""}
    if isinstance(obj, list):
        return [_strip_noise(item) for item in obj]
    return obj

# Set up logging
logger = logging.getLogger(__name__)

# Create FastMCP server
mcp = FastMCP(
    "gnucash-mcp",
    instructions="""GnuCash MCP Server — Double-Entry Accounting Tools

ORIENTATION:
- Call get_book_summary first to understand the book's structure, currency, and account hierarchy.
- Use list_accounts to find exact account paths before creating transactions.
- Use search_transactions before creating to avoid duplicates.

DOUBLE-ENTRY BASICS:
- Every transaction has splits that MUST balance to zero.
- Debits are positive for Asset/Expense accounts, negative for Liability/Income/Equity.
- Credit card payment example: checking -200 (credit), credit card +200 (debit) — reduces both balances.
- Income received: checking +3000 (debit), income -3000 (credit).

ACCOUNT PATHS:
- Always use full paths: "Expenses:Groceries" not "Groceries".
- Paths are colon-delimited and case-sensitive.
- When unsure of a path, use list_accounts or get_account to verify.

GUID PREFIXES:
- All tools accepting GUIDs also accept 8+ character prefixes.
- Use the short prefix from list_transactions output — no need to look up full GUIDs.

RECONCILIATION WORKFLOW:
1. list_transactions for the account and date range
2. Match each statement line to an existing transaction or create missing ones
3. Verify balance matches statement with get_balance
4. Use reconcile_account to mark splits as reconciled

INVESTMENT WORKFLOW:
1. create_lot for each purchase
2. create_transaction with quantity (shares) and amount (cost)
3. assign_split_to_lot to link the investment split
4. create_price to record the share price
5. calculate_lot_gain to check performance

SLOTS (CUSTOM METADATA):
- Use get_account_slots / set_account_slot to store per-account data like APR, credit limit, statement close day.
- Values are stored as strings. Store numbers as strings: set_account_slot("...", "apr", "23.49").

SAFETY:
- Reconciled splits are protected. Use force=true only when intentionally modifying reconciled data.
- void_transaction preserves audit trail. Prefer void over delete for posted transactions.
- delete_account is blocked if account has children or transactions.
""",
)

# ---------------------------------------------------------------------------
# Tool module definitions — controls which tools are advertised via --modules
# ---------------------------------------------------------------------------
TOOL_MODULES: dict[str, list[str]] = {
    "core": [
        "get_book_summary",
        "list_accounts",
        "get_account",
        "get_balance",
        "create_account",
        "update_account",
        "move_account",
        "delete_account",
        "list_transactions",
        "get_transaction",
        "create_transaction",
        "update_transaction",
        "delete_transaction",
        "replace_splits",
        "search_transactions",
    ],
    "reconciliation": [
        "get_unreconciled_splits",
        "set_reconcile_state",
        "reconcile_account",
        "void_transaction",
        "unvoid_transaction",
    ],
    "reporting": [
        "spending_by_category",
        "income_by_source",
        "balance_sheet",
        "net_worth",
        "cash_flow",
        "debt_payoff_plan",
    ],
    "budgets": [
        "list_budgets",
        "get_budget",
        "create_budget",
        "set_budget_amount",
        "get_budget_report",
        "delete_budget",
    ],
    "scheduling": [
        "create_scheduled_transaction",
        "list_scheduled_transactions",
        "get_upcoming_transactions",
        "create_transaction_from_scheduled",
        "update_scheduled_transaction",
        "delete_scheduled_transaction",
    ],
    "investments": [
        "list_commodities",
        "create_commodity",
        "create_price",
        "get_prices",
        "get_latest_price",
        "create_lot",
        "list_lots",
        "get_lot",
        "assign_split_to_lot",
        "calculate_lot_gain",
        "close_lot",
    ],
    "admin": [
        "get_account_slots",
        "set_account_slot",
        "delete_account_slot",
        "get_audit_log",
    ],
    "business": [
        "create_customer",
        "list_customers",
        "get_customer",
        "create_vendor",
        "list_vendors",
        "get_vendor",
        "create_billterm",
        "list_billterms",
        "create_invoice",
        "create_bill",
        "add_invoice_entry",
        "add_bill_entry",
        "list_invoices",
        "get_invoice",
        "post_invoice",
        "pay_invoice",
        "delete_invoice",
        "delete_bill",
        "delete_customer",
        "delete_vendor",
        "get_outstanding_invoices",
        "vendor_spending_report",
    ],
}


def _validate_tool_modules() -> None:
    """Verify every registered tool belongs to exactly one module.

    Developer guard — catches the case where a tool is added but not
    placed in TOOL_MODULES, or a module lists a tool that doesn't exist.
    """
    all_mapped: set[str] = set()
    for tools in TOOL_MODULES.values():
        all_mapped.update(tools)

    registered = set(mcp._tool_manager._tools.keys())
    unmapped = registered - all_mapped
    if unmapped:
        raise RuntimeError(
            f"Tools registered but not in TOOL_MODULES: {sorted(unmapped)}. "
            f"Add them to the appropriate module."
        )
    phantom = all_mapped - registered
    if phantom:
        raise RuntimeError(
            f"Tools in TOOL_MODULES but not registered: {sorted(phantom)}. "
            f"Remove them from TOOL_MODULES or register the tools."
        )


def _apply_module_filter(modules_str: str | None) -> list[str]:
    """Remove tools not in the selected modules from the FastMCP registry.

    Args:
        modules_str: Comma-separated module names, "all", or None (core only).

    Returns:
        Sorted list of module names that were actually loaded.
    """
    if modules_str is None:
        enabled_modules = {"core"}
    else:
        enabled_modules = {m.strip() for m in modules_str.split(",")}
        if "all" in enabled_modules:
            return sorted(TOOL_MODULES.keys())
        # Validate module names
        unknown = enabled_modules - set(TOOL_MODULES.keys())
        if unknown:
            print(
                f"Warning: Unknown module(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(sorted(TOOL_MODULES.keys()))}, all",
                file=sys.stderr,
            )
        # core is always included
        enabled_modules.add("core")

    # Build the set of tool names to keep
    keep: set[str] = set()
    valid_modules: list[str] = []
    for mod_name in sorted(enabled_modules):
        if mod_name in TOOL_MODULES:
            keep.update(TOOL_MODULES[mod_name])
            valid_modules.append(mod_name)

    # Remove tools not in the keep set
    all_registered = list(mcp._tool_manager._tools.keys())
    for tool_name in all_registered:
        if tool_name not in keep:
            mcp.remove_tool(tool_name)

    return valid_modules


# Runtime server state — populated by main(), read by get_server_config tool
_server_state: dict = {}

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
            return _json(
                {
                    "error": str(e),
                    "error_type": "lock_error",
                    "suggestion": "Close GnuCash application and try again.",
                }
            )
        except FileNotFoundError as e:
            logger.error(f"File not found in {func.__name__}: {e}")
            return _json(
                {
                    "error": str(e),
                    "error_type": "file_not_found",
                    "suggestion": "Check that GNUCASH_BOOK_PATH is set correctly.",
                }
            )
        except ValueError as e:
            logger.warning(f"Validation error in {func.__name__}: {e}")
            return _json({"error": str(e), "error_type": "validation_error"})
        except Exception as e:
            # Catch-all for unexpected errors
            logger.error(
                f"Unexpected error in {func.__name__}: {e}\n{traceback.format_exc()}"
            )
            return _json(
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
def get_book_summary() -> str:
    """Get a compact overview of the entire GnuCash book.

    Returns book path, currency, account structure, transaction counts,
    key balances, net worth, commodities, and scheduled transactions
    in a single text response. Use this first to orient yourself.
    """
    book = get_book()
    return book.get_book_summary()


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_accounts(
    root: str | None = None,
    verbose: bool = False,
) -> str:
    """List all accounts in the GnuCash chart of accounts.

    Returns a compact one-line-per-account format by default.
    Use verbose=true for full JSON with guid, type, commodity, etc.

    Args:
        root: Filter to a subtree (e.g., "Expenses" for expense accounts only).
        verbose: If true, return full JSON details for each account.
    """
    book = get_book()
    result = book.list_accounts(root=root, compact=not verbose)
    if verbose:
        return _json(result)
    return result


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_commodities(verbose: bool = False) -> str:
    """List all commodities (currencies, stocks, etc.) in the book.

    Returns a compact one-line-per-commodity format by default.
    Use verbose=true for full JSON with fraction, latest prices, etc.

    Args:
        verbose: If true, return full JSON details for each commodity.
    """
    book = get_book()
    result = book.list_commodities(compact=not verbose)
    if verbose:
        return _json(result)
    return result


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
    return _json(result)


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
    return _json(result)


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
    return _json(result)


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
    return _json(result)


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
        return _json({"error": f"Account not found: {name}"})
    return _json(result)


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
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_transactions(
    account: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 50,
    verbose: bool = False,
) -> str:
    """List transactions with optional filters.

    Returns a compact one-line-per-transaction format by default.
    Use verbose=true for full JSON with GUIDs, splits, reconcile state, etc.

    Args:
        account: Filter by account name
        start_date: Start date in ISO format (YYYY-MM-DD)
        end_date: End date in ISO format (YYYY-MM-DD)
        limit: Maximum number of transactions to return (default 50)
        verbose: If true, return full JSON details for each transaction.
    """
    book = get_book()
    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    result = book.list_transactions(account, start, end, limit, compact=not verbose)
    if verbose:
        return _json(result)
    return result


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_transaction(
    guid: Annotated[str, Field(description="Transaction GUID (32-character hex string, or 8+ char prefix)")],
) -> str:
    """Get details for a specific transaction by GUID.

    Args:
        guid: Transaction GUID (32-character hex string, or 8+ char prefix)
    """
    book = get_book()
    result = book.get_transaction(guid)
    if result is None:
        return _json({"error": f"Transaction not found: {guid}"})
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="transaction")
def create_transaction(
    description: str,
    splits: list[dict] | None = None,
    transaction_date: str | None = None,
    currency: str | None = None,
    notes: str | None = None,
    check_duplicates: bool = True,
    force_create: bool = False,
    dry_run: bool = False,
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
        check_duplicates: Run duplicate detection against existing transactions.
        force_create: Create even if HIGH confidence duplicates are found.
        dry_run: Run validation and dupe check, return proposal without writing.
    """
    book = get_book()
    trans_date = date.fromisoformat(transaction_date) if transaction_date else None
    result = book.create_transaction(
        description=description,
        splits=splits,
        trans_date=trans_date,
        currency=currency,
        notes=notes,
        check_duplicates=check_duplicates,
        force_create=force_create,
        dry_run=dry_run,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def search_transactions(query: str, field: str = "description", verbose: bool = False) -> str:
    """Search transactions by description, memo, notes, or amount.

    Returns a compact one-line-per-transaction format by default.
    Use verbose=true for full JSON with GUIDs, splits, reconcile state, etc.

    Args:
        query: Search query string. For amount, supports: exact ("100"), greater (">100"), less ("<100"), range ("100-200")
        field: Field to search: 'description', 'memo', 'notes', or 'amount'
        verbose: If true, return full JSON details for each transaction.
    """
    book = get_book()
    result = book.search_transactions(query, field, compact=not verbose)
    if verbose:
        return _json(result)
    return result


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="account")
def create_account(
    name: str,
    account_type: str,
    parent: str | None = None,
    description: str = "",
    placeholder: bool = False,
    commodity: str | None = None,
    commodity_namespace: str = "CURRENCY",
) -> str:
    """Create a new account in the chart of accounts.

    Args:
        name: Account name (e.g., "AI Subscriptions")
        account_type: GnuCash account type (ASSET, BANK, CASH, CREDIT, EQUITY, EXPENSE, INCOME, LIABILITY, MUTUAL, STOCK, RECEIVABLE, PAYABLE)
        parent: Full path of parent account (e.g., "Expenses:Online Services").
            If omitted, creates a top-level account at the book root.
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
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="update", entity_type="account")
def update_account(
    name: str,
    new_name: str | None = None,
    description: str | None = None,
    placeholder: bool | None = None,
    account_type: str | None = None,
) -> str:
    """Update an existing account's properties.

    Args:
        name: Full account path to update (e.g., "Expenses:Groceries")
        new_name: New name for the account (just the leaf name, not full path)
        description: New description
        placeholder: New placeholder status (true = container only)
        account_type: New account type (e.g., "CREDIT", "BANK"). Only changes
            within the same debit/credit polarity are allowed — e.g.,
            LIABILITY to CREDIT, ASSET to BANK. Cross-polarity changes
            (e.g., ASSET to LIABILITY) are blocked.
    """
    book = get_book()
    result = book.update_account(
        name=name,
        new_name=new_name,
        description=description,
        placeholder=placeholder,
        account_type=account_type,
    )
    return _json(result)


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
    return _json(result)


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
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="delete", entity_type="transaction")
def delete_transaction(
    guid: Annotated[str, Field(description="Transaction GUID (32-character hex string, or 8+ char prefix)")],
    force: bool = False,
) -> str:
    """Delete a transaction by GUID.

    Safeguards prevent deletion if the transaction has reconciled splits.
    Use force=true to override.

    Args:
        guid: Transaction GUID (32-character hex string, or 8+ char prefix)
        force: Allow deleting transactions with reconciled splits
    """
    book = get_book()
    result = book.delete_transaction(guid, force=force)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="update", entity_type="transaction")
def update_transaction(
    guid: Annotated[str, Field(description="Transaction GUID to update (32-character hex string, or 8+ char prefix)")],
    description: str | None = None,
    transaction_date: str | None = None,
    splits: list[dict] | None = None,
    notes: str | None = None,
    force: bool = False,
) -> str:
    """Update an existing transaction.

    Args:
        guid: Transaction GUID to update (32-character hex string, or 8+ char prefix)
        description: New transaction description (optional)
        transaction_date: New date in ISO format YYYY-MM-DD (optional)
        splits: List of split updates with 'account' and 'amount' (optional).
                Must match existing splits by account name and balance to zero.
                For cross-currency splits, include 'quantity' (amount in account's commodity).
        notes: New transaction notes (optional). Pass empty string to clear.
        force: Allow modifying transactions with reconciled splits
    """
    book = get_book()
    trans_date = date.fromisoformat(transaction_date) if transaction_date else None
    result = book.update_transaction(
        guid=guid,
        description=description,
        trans_date=trans_date,
        splits=splits,
        notes=notes,
        force=force,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="replace_splits", entity_type="transaction")
def replace_splits(
    guid: Annotated[str, Field(description="Transaction GUID (32-character hex string, or 8+ char prefix)")],
    splits: list[dict],
    force: bool = False,
) -> str:
    """Replace all splits in a transaction with a new set.

    Replace all splits in a transaction with a completely new set.
    The transaction's currency, description, date, and notes are preserved.
    New splits must balance to zero.

    Args:
        guid: Transaction GUID (32-character hex string, or 8+ char prefix)
        splits: Complete new set of splits. Each split needs:
            - 'account' (required): Full account path
            - 'amount' (required): Value in transaction currency
            - 'quantity' (optional): Amount in account's commodity.
              Required if account commodity differs from transaction currency.
            - 'memo' (optional): Split memo
        force: Allow replacing reconciled splits or splits in lots
    """
    book = get_book()
    result = book.replace_splits(
        guid=guid,
        splits=splits,
        force=force,
    )
    return _json(result)


# ============== Reconciliation Tools ==============


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="set_state", entity_type="split")
def set_reconcile_state(
    split_guid: Annotated[str, Field(description="GUID of the split to update (32-character hex string, or 8+ char prefix)")],
    state: str,
    reconcile_date: str | None = None,
) -> str:
    """Set the reconciliation state for a split.

    Args:
        split_guid: GUID of the split to update (32-character hex string, or 8+ char prefix)
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
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_unreconciled_splits(
    account: str,
    as_of_date: str | None = None,
    verbose: bool = False,
) -> str:
    """Get all unreconciled splits for an account.

    Returns a compact one-line-per-split format by default with a summary footer.
    Use verbose=true for full JSON with split GUIDs, amounts, and totals.

    Args:
        account: Full account name (e.g., 'Assets:Bank:Checking')
        as_of_date: Only include splits on or before this date (YYYY-MM-DD)
        verbose: If true, return full JSON details. Default compact one-line format.
    """
    book = get_book()
    date_obj = date.fromisoformat(as_of_date) if as_of_date else None
    result = book.get_unreconciled_splits(
        account_name=account,
        as_of_date=date_obj,
        compact=not verbose,
    )
    if verbose:
        return _json(result)
    return result


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="reconcile", entity_type="split")
def reconcile_account(
    account: str,
    statement_date: str,
    statement_balance: str,
    split_guids: Annotated[list[str], Field(description="List of split GUIDs to mark as reconciled (8+ char prefixes accepted)")],
) -> str:
    """Reconcile multiple splits against a statement balance.

    Marks all specified splits as reconciled if the resulting balance matches
    the statement balance. This is an atomic operation - either all splits are
    reconciled or none are.

    Args:
        account: Full account name (e.g., 'Assets:Bank:Checking')
        statement_date: Statement ending date (YYYY-MM-DD)
        statement_balance: Expected balance from statement (as string, e.g., '1234.56')
        split_guids: List of split GUIDs to mark as reconciled (8+ char prefixes accepted)
    """
    book = get_book()
    stmt_date = date.fromisoformat(statement_date)
    result = book.reconcile_account(
        account_name=account,
        statement_date=stmt_date,
        statement_balance=statement_balance,
        split_guids=split_guids,
    )
    return _json(result)


# ============== Void Tools ==============


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="void", entity_type="transaction")
def void_transaction(
    guid: Annotated[str, Field(description="Transaction GUID to void (32-character hex string, or 8+ char prefix)")],
    reason: str,
) -> str:
    """Void a transaction (proper accounting void, not delete).

    Voiding preserves the transaction for audit purposes but zeroes out
    all split values. Use this instead of delete when you need to maintain
    an audit trail.

    Args:
        guid: Transaction GUID to void (32-character hex string, or 8+ char prefix)
        reason: Reason for voiding (required for audit trail)
    """
    book = get_book()
    result = book.void_transaction(guid=guid, reason=reason)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="unvoid", entity_type="transaction")
def unvoid_transaction(
    guid: Annotated[str, Field(description="Transaction GUID to unvoid (32-character hex string, or 8+ char prefix)")],
) -> str:
    """Restore a voided transaction.

    Restores original split values and removes void markers.

    Args:
        guid: Transaction GUID to unvoid (32-character hex string, or 8+ char prefix)
    """
    book = get_book()
    result = book.unvoid_transaction(guid=guid)
    return _json(result)


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
    return _json(result)


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
    return _json(result)


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
    return _json(result)


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
    return _json(result)


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
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def debt_payoff_plan(
    monthly_budget: str,
    additional_purchase: str | None = None,
) -> str:
    """Calculate an avalanche-method debt payoff schedule with YETI multiplier.

    Auto-discovers CREDIT/LIABILITY accounts that have an 'apr' slot set.
    Set APRs via set_account_slot (e.g., set_account_slot("Liabilities:Visa", "apr", "23.49")).

    YETI (Your Expense's True Impact) shows the true cost of a purchase when
    carrying debt: "A $1.00 purchase will cost you $1.68 by the time your
    debt is paid off."

    Args:
        monthly_budget: Total monthly amount available for all debt payments combined
        additional_purchase: Dollar amount to calculate YETI for (default "1.00")
    """
    book = get_book()
    result = book.debt_payoff_plan(
        monthly_budget=monthly_budget,
        additional_purchase=additional_purchase,
    )
    return _json(result)


# ============== Budget Tools ==============


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_budgets() -> str:
    """List all budgets in the book.

    Returns:
        JSON list of budgets with guid, name, description,
        num_periods, and period_type.
    """
    book = get_book()
    result = book.list_budgets()
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_budget(name: str) -> str:
    """Get full details of a budget including all budget amounts.

    Args:
        name: Budget name.

    Returns:
        JSON with budget info and all account/period amounts.
    """
    book = get_book()
    result = book.get_budget(name)
    if result is None:
        return _json({"error": f"Budget not found: {name}"})
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="budget")
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
    """
    book = get_book()
    result = book.create_budget(
        name=name,
        year=year,
        num_periods=num_periods,
        period_type=period_type,
        description=description,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="update", entity_type="budget")
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
    """
    book = get_book()
    result = book.set_budget_amount(
        budget_name=budget_name,
        account=account,
        amount=amount,
        period=period,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_budget_report(
    budget_name: str,
    period: int | str | None = None,
    account: str | None = None,
    include_children: bool = True,
) -> str:
    """Compare actual spending against budget.

    Args:
        budget_name: Name of the budget.
        period: Which period to report:
            - None: Current period based on today's date (default)
            - Integer 0-11: Specific period
            - "ytd": Year to date (all periods up to current)
            - "all": All periods
        account: Optional filter to specific account or parent account.
        include_children: If True and account specified, include child accounts.
    """
    book = get_book()
    result = book.get_budget_report(
        budget_name=budget_name,
        period=period,
        account=account,
        include_children=include_children,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="delete", entity_type="budget")
def delete_budget(name: str) -> str:
    """Delete a budget.

    Args:
        name: Budget name.
    """
    book = get_book()
    result = book.delete_budget(name=name)
    return _json(result)


# ============== Scheduled Transaction Tools ==============


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="scheduled_transaction")
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
            - "bimonthly" (every 2 months)
            - "quarterly" (every 3 months)
            - "yearly"
        end_date: Optional last occurrence date (YYYY-MM-DD).
        enabled: Whether the schedule is active. Default True.
    """
    book = get_book()
    result = book.create_scheduled_transaction(
        name=name,
        description=description,
        splits=splits,
        start_date=start_date,
        frequency=frequency,
        end_date=end_date,
        enabled=enabled,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_scheduled_transactions(
    enabled_only: bool = True,
    verbose: bool = False,
) -> str:
    """List all scheduled transactions.

    Returns a compact one-line-per-schedule format by default.
    Use verbose=true for full JSON with GUIDs, splits, dates, etc.

    Args:
        enabled_only: If True, only show enabled schedules. Default True.
        verbose: If true, return full JSON details for each scheduled transaction.
    """
    book = get_book()
    result = book.list_scheduled_transactions(
        enabled_only=enabled_only,
        compact=not verbose,
    )
    if verbose:
        return _json(result)
    return result


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_upcoming_transactions(
    days: int = 14,
    verbose: bool = False,
) -> str:
    """Get scheduled transactions due within a time window.

    This is the "what bills are coming up?" query.

    Args:
        days: Look ahead window in days. Default 14.
        verbose: If true, return full JSON with splits. Default compact one-line format.
    """
    book = get_book()
    result = book.get_upcoming_transactions(days=days, compact=not verbose)
    if verbose:
        return _json(result)
    return result


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="transaction")
def create_transaction_from_scheduled(
    guid: Annotated[str, Field(description="Scheduled transaction GUID (or 8+ char prefix)")],
    transaction_date: str | None = None,
) -> str:
    """Create an actual transaction from a scheduled template.

    Args:
        guid: Scheduled transaction GUID (or 8+ char prefix).
        transaction_date: Date for the transaction. Defaults to next occurrence.
    """
    book = get_book()
    result = book.create_transaction_from_scheduled(
        guid=guid,
        transaction_date=transaction_date,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="update", entity_type="scheduled_transaction")
def update_scheduled_transaction(
    guid: Annotated[str, Field(description="Scheduled transaction GUID (or 8+ char prefix)")],
    enabled: bool | None = None,
    end_date: str | None = None,
) -> str:
    """Update a scheduled transaction.

    Args:
        guid: Scheduled transaction GUID (or 8+ char prefix).
        enabled: Enable or disable.
        end_date: Set end date (empty string to clear).
    """
    book = get_book()
    result = book.update_scheduled_transaction(
        guid=guid,
        enabled=enabled,
        end_date=end_date,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="delete", entity_type="scheduled_transaction")
def delete_scheduled_transaction(
    guid: Annotated[str, Field(description="Scheduled transaction GUID (or 8+ char prefix)")],
) -> str:
    """Delete a scheduled transaction.

    Does not affect transactions already created from this schedule.

    Args:
        guid: Scheduled transaction GUID (or 8+ char prefix).
    """
    book = get_book()
    result = book.delete_scheduled_transaction(guid=guid)
    return _json(result)


# ============== Lot (Cost Basis) Tools ==============


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="lot")
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
    """
    book = get_book()
    result = book.create_lot(account=account, title=title, notes=notes)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_lots(
    account: str,
    include_closed: bool = False,
    verbose: bool = False,
) -> str:
    """List all lots for an investment account.

    Returns a compact one-line-per-lot format by default.
    Use verbose=true for full JSON with guid, title, notes, etc.

    Args:
        account: Full path of investment account.
        include_closed: If True, include fully-sold lots. Default False.
        verbose: If true, return full JSON details for each lot.
    """
    book = get_book()
    result = book.list_lots(account=account, include_closed=include_closed, compact=not verbose)
    if verbose:
        return _json(result)
    return result


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_lot(
    guid: Annotated[str, Field(description="Lot GUID (or 8+ char prefix)")],
) -> str:
    """Get detailed information about a lot.

    Args:
        guid: Lot GUID (or 8+ char prefix).

    Returns:
        JSON with lot details including all splits:
        - title, notes, is_closed
        - splits: list of all splits with date, quantity, value
        - summary: total quantity, cost basis, cost per share
    """
    book = get_book()
    result = book.get_lot(guid=guid)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="update", entity_type="lot")
def assign_split_to_lot(
    split_guid: Annotated[str, Field(description="GUID of the split (from transaction's investment account). 8+ char prefix accepted.")],
    lot_guid: Annotated[str, Field(description="GUID of the lot (or 8+ char prefix)")],
) -> str:
    """Assign a transaction split to a lot.

    Use after creating a buy/sell transaction to link the investment
    account split to its lot for cost basis tracking.

    Args:
        split_guid: GUID of the split (from transaction's investment account). 8+ char prefix accepted.
        lot_guid: GUID of the lot (or 8+ char prefix).

    Workflow:
        1. create_lot("Assets:VTSAX", "VTSAX Jan 2026")
        2. create_transaction(...buy 10 shares...)
        3. assign_split_to_lot(investment_split_guid, lot_guid)
    """
    book = get_book()
    result = book.assign_split_to_lot(split_guid=split_guid, lot_guid=lot_guid)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def calculate_lot_gain(
    lot_guid: Annotated[str, Field(description="Lot GUID (or 8+ char prefix)")],
    shares: str | None = None,
    sale_price: str | None = None,
) -> str:
    """Calculate potential or actual capital gain for a lot.

    If shares and sale_price provided, calculates hypothetical gain.
    Otherwise uses lot's current state and latest price.

    Args:
        lot_guid: Lot GUID (or 8+ char prefix).
        shares: Optional number of shares to calculate for.
                Defaults to all remaining shares.
        sale_price: Optional sale price per share.
                    Defaults to latest price for the commodity.
    """
    book = get_book()
    result = book.calculate_lot_gain(
        lot_guid=lot_guid, shares=shares, sale_price=sale_price,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="update", entity_type="lot")
def close_lot(
    guid: Annotated[str, Field(description="Lot GUID (or 8+ char prefix)")],
) -> str:
    """Mark a lot as closed.

    Use when a lot is fully sold but wasn't automatically marked closed,
    or to manually close a lot with zero shares.

    Args:
        guid: Lot GUID (or 8+ char prefix).

    Note:
        Lots are automatically marked closed when their quantity reaches zero
        through assigned splits. This tool is for manual cleanup.
    """
    book = get_book()
    result = book.close_lot(guid=guid)
    return _json(result)


# ============== Account Slot Tools ==============


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_account_slots(
    account: str,
    key: str | None = None,
) -> str:
    """Read slots (custom metadata) from an account.

    Slots are key-value pairs stored on accounts for metadata like APR,
    credit limit, reward rates, or any custom data.

    Args:
        account: Full account path (e.g., "Liabilities:Credit Cards:Capital One").
        key: Specific slot key to retrieve. If omitted, returns all slots.
    """
    book = get_book()
    result = book.get_account_slots(account_name=account, key=key)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="set_slot", entity_type="account_slot")
def set_account_slot(
    account: str,
    key: str,
    value: str,
) -> str:
    """Set a custom metadata slot on an account.

    Stores a key-value pair on the account. Values are stored as strings.
    Use for APR, credit limits, reward rates, or any per-account metadata.

    Args:
        account: Full account path (e.g., "Liabilities:Credit Cards:Capital One").
        key: Slot key (e.g., "apr", "credit_limit").
        value: Slot value (always stored as string).
    """
    book = get_book()
    result = book.set_account_slot(account_name=account, key=key, value=value)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="delete_slot", entity_type="account_slot")
def delete_account_slot(
    account: str,
    key: str,
) -> str:
    """Remove a custom metadata slot from an account.

    Args:
        account: Full account path (e.g., "Liabilities:Credit Cards:Capital One").
        key: Slot key to remove.
    """
    book = get_book()
    result = book.delete_account_slot(account_name=account, key=key)
    return _json(result)


# ============== Business Tools ==============


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="customer")
def create_customer(
    name: str,
    currency: str | None = None,
    notes: str = "",
    address: dict | None = None,
) -> str:
    """Create a new customer.

    Args:
        name: Customer name (e.g., "Acme Corp").
        currency: ISO currency code (e.g., "USD", "EUR").
                  Defaults to book's default currency.
        notes: Optional notes.
        address: Optional address with keys: name, addr1, addr2,
                 addr3, addr4, phone, fax, email.
    """
    book = get_book()
    result = book.create_customer(
        name=name, currency=currency, notes=notes, address=address,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_customers(
    active_only: bool = True,
    verbose: bool = False,
) -> str:
    """List all customers.

    Returns a compact one-line-per-customer format by default.
    Use verbose=true for full JSON with guid, address, notes, etc.

    Args:
        active_only: If True, only show active customers. Default True.
        verbose: If true, return full JSON details for each customer.
    """
    book = get_book()
    result = book.list_customers(active_only=active_only, compact=not verbose)
    if verbose:
        return json.dumps(result, indent=2)
    return result


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_customer(
    id: str,
) -> str:
    """Get details for a specific customer by ID.

    Args:
        id: Customer ID (e.g., "000001"). This is the human-readable
            ID shown in GnuCash, not the internal GUID.
    """
    book = get_book()
    result = book.get_customer(customer_id=id)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="vendor")
def create_vendor(
    name: str,
    currency: str | None = None,
    notes: str = "",
    address: dict | None = None,
) -> str:
    """Create a new vendor.

    Args:
        name: Vendor name (e.g., "Office Depot").
        currency: ISO currency code (e.g., "USD", "EUR").
                  Defaults to book's default currency.
        notes: Optional notes.
        address: Optional address with keys: name, addr1, addr2,
                 addr3, addr4, phone, fax, email.
    """
    book = get_book()
    result = book.create_vendor(
        name=name, currency=currency, notes=notes, address=address,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_vendors(
    active_only: bool = True,
    verbose: bool = False,
) -> str:
    """List all vendors.

    Returns a compact one-line-per-vendor format by default.
    Use verbose=true for full JSON with guid, address, notes, etc.

    Args:
        active_only: If True, only show active vendors. Default True.
        verbose: If true, return full JSON details for each vendor.
    """
    book = get_book()
    result = book.list_vendors(active_only=active_only, compact=not verbose)
    if verbose:
        return json.dumps(result, indent=2)
    return result


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_vendor(
    id: str,
) -> str:
    """Get details for a specific vendor by ID.

    Args:
        id: Vendor ID (e.g., "000001"). This is the human-readable
            ID shown in GnuCash, not the internal GUID.
    """
    book = get_book()
    result = book.get_vendor(vendor_id=id)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="billterm")
def create_billterm(
    name: str,
    due_days: int = 30,
    description: str = "",
    discount_days: int = 0,
    discount_percent: str = "0",
) -> str:
    """Create a new billing term.

    Args:
        name: Billterm name (e.g., "Net 30").
        due_days: Number of days until payment is due. Default 30.
        description: Optional description.
        discount_days: Days within which early discount applies.
        discount_percent: Early payment discount percentage (e.g., "2" for 2%).
    """
    book = get_book()
    result = book.create_billterm(
        name=name, due_days=due_days, description=description,
        discount_days=discount_days, discount_percent=discount_percent,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_billterms(
    verbose: bool = False,
) -> str:
    """List all billing terms.

    Returns a compact one-line-per-term format by default.
    Use verbose=true for full JSON with guid, discount details, etc.

    Args:
        verbose: If true, return full JSON details for each billing term.
    """
    book = get_book()
    result = book.list_billterms(compact=not verbose)
    if verbose:
        return json.dumps(result, indent=2)
    return result


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="invoice")
def create_invoice(
    customer_id: str,
    date_opened: str | None = None,
    notes: str = "",
    currency: str | None = None,
    term: str | None = None,
    invoice_id: str | None = None,
) -> str:
    """Create a customer invoice.

    Args:
        customer_id: Customer ID (e.g., "000001").
        date_opened: Date in ISO format (YYYY-MM-DD). Defaults to today.
        notes: Optional notes.
        currency: ISO currency code. Defaults to book's default currency.
        term: Billterm name (e.g., "Net 30"). Optional.
        invoice_id: Custom invoice number (e.g., "INV-2026-001"). If omitted,
            auto-generates from the book's invoice counter.
    """
    book = get_book()
    result = book.create_invoice(
        customer_id=customer_id, date_opened=date_opened,
        notes=notes, currency=currency, term=term,
        invoice_id=invoice_id,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="bill")
def create_bill(
    vendor_id: str,
    date_opened: str | None = None,
    notes: str = "",
    currency: str | None = None,
    term: str | None = None,
    bill_id: str | None = None,
) -> str:
    """Create a vendor bill.

    Args:
        vendor_id: Vendor ID (e.g., "000001").
        date_opened: Date in ISO format (YYYY-MM-DD). Defaults to today.
        notes: Optional notes.
        currency: ISO currency code. Defaults to book's default currency.
        term: Billterm name (e.g., "Net 30"). Optional.
        bill_id: Custom bill number (e.g., "BILL-2026-001"). If omitted,
            auto-generates from the book's bill counter.
    """
    book = get_book()
    result = book.create_bill(
        vendor_id=vendor_id, date_opened=date_opened,
        notes=notes, currency=currency, term=term,
        bill_id=bill_id,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="entry")
def add_invoice_entry(
    invoice_id: str,
    account: str,
    description: str,
    quantity: str,
    price: str,
) -> str:
    """Add a line item to a customer invoice.

    The invoice must not be posted yet. Entries represent individual
    goods or services being billed.

    Args:
        invoice_id: Invoice ID (e.g., "000001").
        account: Income account path (e.g., "Income:Sales").
        description: Line item description.
        quantity: Quantity as decimal string (e.g., "1", "2.5").
        price: Unit price as decimal string (e.g., "100.00").
    """
    book = get_book()
    result = book.add_invoice_entry(
        invoice_id=invoice_id, account=account,
        description=description, quantity=quantity, price=price,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="create", entity_type="entry")
def add_bill_entry(
    bill_id: str,
    account: str,
    description: str,
    quantity: str,
    price: str,
) -> str:
    """Add a line item to a vendor bill.

    The bill must not be posted yet. Entries represent individual
    goods or services being billed.

    Args:
        bill_id: Bill ID (e.g., "000001").
        account: Expense account path (e.g., "Expenses:Office Supplies").
        description: Line item description.
        quantity: Quantity as decimal string (e.g., "1", "2.5").
        price: Unit price as decimal string (e.g., "50.00").
    """
    book = get_book()
    result = book.add_bill_entry(
        bill_id=bill_id, account=account,
        description=description, quantity=quantity, price=price,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def list_invoices(
    owner_type: str | None = None,
    status: str | None = None,
    verbose: bool = False,
) -> str:
    """List invoices and/or vendor bills.

    Returns a compact one-line-per-invoice format by default.
    Use verbose=true for full JSON with GUIDs, dates, notes, etc.

    Args:
        owner_type: Filter by type: "customer" for invoices,
                    "vendor" for bills, or omit for all.
        status: Filter by status: "posted" or "open", or omit for all.
        verbose: If true, return full JSON details.
    """
    book = get_book()
    result = book.list_invoices(
        owner_type=owner_type, status=status, compact=not verbose,
    )
    if verbose:
        return json.dumps(result, indent=2)
    return result


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_invoice(
    id: str,
    owner_type: str | None = None,
) -> str:
    """Get full details for an invoice or bill, including line items.

    Works for both customer invoices and vendor bills. Returns all
    entries with quantities, prices, and totals.

    Args:
        id: Invoice or bill ID (e.g., "000001"). This is the
            human-readable ID, not the internal GUID.
        owner_type: Filter by type: "customer" for invoices,
                    "vendor" for bills. Useful when an invoice and
                    bill share the same ID (independent counters).
    """
    book = get_book()
    result = book.get_invoice(invoice_id=id, owner_type=owner_type)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="post", entity_type="invoice")
def post_invoice(
    id: str,
    post_account: str,
    post_date: str | None = None,
    due_date: str | None = None,
    description: str | None = None,
    owner_type: str | None = None,
) -> str:
    """Post a customer invoice or vendor bill.

    Posting creates a transaction in the A/R or A/P account and makes
    the invoice official. Once posted, entries cannot be added.

    Args:
        id: Invoice or bill ID (e.g., "000001").
        post_account: A/R or A/P account path (e.g., "Assets:Accounts Receivable").
        post_date: Date in ISO format (YYYY-MM-DD). Defaults to today.
        due_date: Payment due date (YYYY-MM-DD). Optional.
        description: Description for the posting transaction. Optional.
        owner_type: "customer" or "vendor" for disambiguation when IDs collide.
    """
    book = get_book()
    result = book.post_invoice(
        invoice_id=id,
        post_account=post_account,
        post_date=post_date,
        due_date=due_date,
        description=description,
        owner_type=owner_type,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="pay", entity_type="invoice")
def pay_invoice(
    id: str,
    payment_account: str,
    amount: str,
    payment_date: str | None = None,
    description: str | None = None,
    owner_type: str | None = None,
) -> str:
    """Record a payment against a posted invoice or bill.

    Creates a payment transaction from the specified bank/cash account
    to the invoice's A/R or A/P account. Partial payments are supported.

    Args:
        id: Invoice or bill ID (e.g., "000001").
        payment_account: Bank or cash account for payment (e.g., "Assets:Checking").
        amount: Payment amount as decimal string (e.g., "500.00").
        payment_date: Payment date (YYYY-MM-DD). Defaults to today.
        description: Description for the payment transaction. Optional.
        owner_type: "customer" or "vendor" for disambiguation.
    """
    book = get_book()
    result = book.pay_invoice(
        invoice_id=id,
        payment_account=payment_account,
        amount=amount,
        payment_date=payment_date,
        description=description,
        owner_type=owner_type,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="delete", entity_type="invoice")
def delete_invoice(invoice_id: str) -> str:
    """Delete an unposted customer invoice.

    Automatically removes associated entries (line items). Posted invoices
    cannot be deleted — void them or issue a credit note instead.

    Args:
        invoice_id: Invoice ID (e.g., "000001" or "INV-2026-001").
    """
    book = get_book()
    result = book.delete_invoice(invoice_id=invoice_id)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="delete", entity_type="bill")
def delete_bill(bill_id: str) -> str:
    """Delete an unposted vendor bill.

    Automatically removes associated entries (line items). Posted bills
    cannot be deleted — void them or issue a credit note instead.

    Args:
        bill_id: Bill ID (e.g., "000001" or "BILL-2026-001").
    """
    book = get_book()
    result = book.delete_bill(bill_id=bill_id)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="delete", entity_type="customer")
def delete_customer(customer_id: str) -> str:
    """Delete a customer with no invoices.

    Customers with any invoices (posted or unposted) cannot be deleted.
    Delete the invoices first, then delete the customer.

    Args:
        customer_id: Customer ID (e.g., "000001").
    """
    book = get_book()
    result = book.delete_customer(customer_id=customer_id)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="write", operation="delete", entity_type="vendor")
def delete_vendor(vendor_id: str) -> str:
    """Delete a vendor with no bills.

    Vendors with any bills (posted or unposted) cannot be deleted.
    Delete the bills first, then delete the vendor.

    Args:
        vendor_id: Vendor ID (e.g., "000001").
    """
    book = get_book()
    result = book.delete_vendor(vendor_id=vendor_id)
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_outstanding_invoices(
    owner_type: str | None = None,
    customer_id: str | None = None,
    vendor_id: str | None = None,
) -> str:
    """Get all posted invoices/bills with outstanding balances.

    Args:
        owner_type: Filter by "customer" or "vendor". Omit for all.
        customer_id: Filter by specific customer ID.
        vendor_id: Filter by specific vendor ID.
    """
    book = get_book()
    result = book.get_outstanding_invoices(
        owner_type=owner_type,
        customer_id=customer_id,
        vendor_id=vendor_id,
    )
    return _json(result)


@mcp.tool()
@safe_tool
@audit_log(classification="read")
def vendor_spending_report(
    start_date: str,
    end_date: str,
    vendor_id: str | None = None,
) -> str:
    """Get spending breakdown by vendor for a period.

    Analyzes posted vendor bills to show total billed, total paid,
    and outstanding amounts per vendor.

    Args:
        start_date: Start of period (YYYY-MM-DD).
        end_date: End of period (YYYY-MM-DD).
        vendor_id: Optional filter to a specific vendor.
    """
    book = get_book()
    result = book.vendor_spending_report(
        start_date=start_date,
        end_date=end_date,
        vendor_id=vendor_id,
    )
    return _json(result)


# ============== Resources ==============


@mcp.resource("gnucash://accounts")
def accounts_resource() -> str:
    """Full chart of accounts from the GnuCash book."""
    book = get_book()
    accounts = book.list_accounts(compact=False)
    return _json(accounts)


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
        return _json({"error": "Logging not initialized (no book path configured)"})

    audit_dir = log_dir / "audit"
    target_date = log_date or datetime.now().astimezone().strftime("%Y-%m-%d")

    # Try the configured format first, then fall back to the other
    fmt = get_audit_format()
    primary_ext = "jsonl" if fmt == "json" else "txt"
    fallback_ext = "txt" if fmt == "json" else "jsonl"

    log_file = audit_dir / f"{target_date}.{primary_ext}"
    used_fallback = False
    if not log_file.exists():
        log_file = audit_dir / f"{target_date}.{fallback_ext}"
        used_fallback = True

    if not log_file.exists():
        if fmt == "json":
            return _json({"entries": [], "message": f"No audit log for {target_date}"})
        else:
            return f"No audit log for {target_date}"

    # Reading a .txt file
    if log_file.suffix == ".txt":
        content = log_file.read_text()
        lines = content.strip().split("\n")
        if len(lines) > limit:
            lines = lines[-limit:]
        text_content = "\n".join(lines)

        # If user configured json but we fell back to txt, wrap in JSON with note
        if fmt == "json":
            return _json({
                "content": text_content,
                "format": "text",
                "note": (
                    "No .jsonl file found for this date. Returning .txt fallback. "
                    "Ensure GNUCASH_MCP_AUDIT_FORMAT=json is set when starting the server."
                ),
            })
        else:
            # User wants text, return raw text
            return text_content

    # Reading a .jsonl file
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

    # If user configured text but we fell back to jsonl, format as text
    if fmt == "text":
        # Convert JSON entries to simple text representation
        lines = []
        for entry in entries[-limit:]:
            ts = entry.get("timestamp", "")[:19]  # Trim to datetime
            tool = entry.get("tool", "unknown")
            result = entry.get("result", "")
            lines.append(f"{ts}  {tool}  {result}")
        return "\n".join(lines) if lines else "No entries"

    # User wants JSON, return JSON
    return _json({"entries": entries[-limit:], "total_count": len(entries)})


# ============== Debug Tool (conditionally registered) ==============


def _get_server_config_impl() -> str:
    """Return current server configuration and runtime state.

    Only available when the server is started with --debug.
    Reports loaded modules, tool count, book path, and version
    so the client can verify its own tool inventory.
    """
    from gnucash_mcp import __version__
    lines = [
        f"Modules loaded: {_server_state.get('modules', 'unknown')}",
        f"Tools available: {_server_state.get('tool_count', 'unknown')}",
        f"Book path: {_server_state.get('book_path', 'not set')}",
        f"Debug mode: {str(_server_state.get('debug', False)).lower()}",
        f"Version: {__version__}",
    ]
    dc_ok = _server_state.get("default_currency_ok")
    if dc_ok is False:
        lines.append("Warning: Book has no default currency set")
    return "\n".join(lines)


# ============== Main ==============


def main() -> None:
    """Run the MCP server."""
    # Handle --help
    if "--help" in sys.argv or "-h" in sys.argv:
        print("""GnuCash MCP Server

Usage: gnucash-mcp [OPTIONS]

Options:
  --modules=MODULES    Tool modules to load (comma-separated).
                       Default: core (15 tools). Use "all" for all 70 tools.
                       Available: core, reconciliation, reporting, budgets,
                       scheduling, investments, business, admin
  --debug              Enable debug logging (MCP protocol traffic, timing)
  --noaudit            Disable audit logging
  --audit-format=FORMAT  Audit log format: "text" (default) or "json"
  -h, --help           Show this help message

Environment variables:
  GNUCASH_BOOK_PATH          Path to GnuCash SQLite book (required)
  GNUCASH_MCP_MODULES        Tool modules to load (e.g., "core,reporting")
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
    modules_value = None

    # Parse --key=value flags
    for arg in sys.argv[:]:
        if arg.startswith("--audit-format="):
            audit_format = arg.split("=", 1)[1]
            sys.argv.remove(arg)
        elif arg.startswith("--modules="):
            modules_value = arg.split("=", 1)[1]
            sys.argv.remove(arg)

    if "--debug" in sys.argv:
        sys.argv.remove("--debug")
    if "--noaudit" in sys.argv:
        sys.argv.remove("--noaudit")

    # Env var fallback for modules (CLI flag takes precedence)
    if modules_value is None:
        modules_value = os.environ.get("GNUCASH_MCP_MODULES")

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

    # Validate and apply module filter
    _validate_tool_modules()
    loaded_modules = _apply_module_filter(modules_value)

    # Check book health (non-fatal)
    currency_ok = None
    if book_path:
        try:
            b = get_book()
            with b.open(readonly=True) as bk:
                dc = bk.default_currency
                if dc is None:
                    currency_ok = False
                    print(
                        "Warning: Book has no default currency. "
                        "Set one in GnuCash under Preferences > Accounts "
                        "(Edit menu on Linux/Windows, GnuCash menu on macOS), "
                        "or pass the currency parameter explicitly to each tool.",
                        file=sys.stderr,
                    )
                else:
                    currency_ok = True
        except Exception:
            pass  # Book may be locked — don't block startup

    # Populate runtime state and conditionally register debug tool
    tool_count = len(mcp._tool_manager._tools)
    modules_display = ", ".join(loaded_modules)
    _server_state.update({
        "modules": modules_display,
        "tool_count": tool_count,
        "book_path": book_path or "not set",
        "debug": debug_flag,
        "default_currency_ok": currency_ok,
    })

    if debug_flag:
        # Register the debug-only diagnostic tool
        @mcp.tool()
        @safe_tool
        def get_server_config() -> str:
            """Get the server's loaded configuration.

            Returns loaded modules, tool count, book path, debug mode,
            and version. Only available when server is started with --debug.
            Use this to verify which tools are available in this session.
            """
            return _get_server_config_impl()

        # Update tool count to include the newly registered tool
        _server_state["tool_count"] = len(mcp._tool_manager._tools)
        debug_log(f"Modules: {modules_display}")
        debug_log(f"Tools loaded: {_server_state['tool_count']}")
    else:
        if _debug_mode:
            debug_log(f"Modules: {modules_display}")
            debug_log(f"Tools loaded: {tool_count}")

    mcp.run()


if __name__ == "__main__":
    main()
