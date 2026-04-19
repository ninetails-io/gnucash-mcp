"""MCP server definition for GnuCash."""

import importlib
import json
import logging
import os
import sys
from datetime import date
from typing import Annotated

from pydantic import Field

from mcp.server.fastmcp import FastMCP

from gnucash_mcp.book import GnuCashBook, build_book_class, extracted_modules
from gnucash_mcp.logging_config import audit_log, debug_log, setup_logging
from gnucash_mcp.tools._helpers import _json, safe_tool

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
    """Verify every registered tool belongs to a module in TOOL_MODULES.

    Developer guard: catches the case where a tool is added in server.py
    or a tools/<module>.py file but not placed in TOOL_MODULES, and the
    case where TOOL_MODULES lists a tool that isn't defined anywhere.

    With lazy loading, extracted modules register their tools only when
    enabled — so 'phantom' (unregistered) tools from an extracted module
    are expected unless that module has been loaded. The 'unmapped'
    check still catches real typos.
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

    # Phantom check is scoped to modules that ship their tools in server.py
    # (the non-extracted ones). Extracted modules' tools are loaded lazily.
    extracted = extracted_modules()
    expected_now = set()
    for mod_name, tools in TOOL_MODULES.items():
        if mod_name not in extracted:
            expected_now.update(tools)
    phantom = expected_now - registered
    if phantom:
        raise RuntimeError(
            f"Tools in TOOL_MODULES but not registered: {sorted(phantom)}. "
            f"Remove them from TOOL_MODULES or register the tools."
        )


def _lazy_load_tool_module(module_name: str) -> None:
    """Import and register an extracted tool module if not already loaded."""
    expected_tools = TOOL_MODULES.get(module_name, [])
    # Idempotent: skip if any tool from this module is already registered
    if any(t in mcp._tool_manager._tools for t in expected_tools):
        return
    tool_mod = importlib.import_module(f"gnucash_mcp.tools.{module_name}")
    tool_mod.register(mcp, get_book)


def _apply_module_filter(modules_str: str | None) -> list[str]:
    """Enable the requested modules and remove tools not in that set.

    For extracted modules (admin, ...) this also lazy-imports the matching
    gnucash_mcp/tools/<name>.py and calls its register() function — so
    disabled extracted modules never parse their tool definitions.

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
            enabled_modules = set(TOOL_MODULES.keys())
        else:
            unknown = enabled_modules - set(TOOL_MODULES.keys())
            if unknown:
                print(
                    f"Warning: Unknown module(s): {', '.join(sorted(unknown))}. "
                    f"Available: {', '.join(sorted(TOOL_MODULES.keys()))}, all",
                    file=sys.stderr,
                )
            enabled_modules.add("core")

    # Keep only known module names
    enabled_modules &= set(TOOL_MODULES.keys())

    # Lazy-load any enabled extracted modules
    extracted = extracted_modules()
    for mod_name in sorted(enabled_modules):
        if mod_name in extracted:
            _lazy_load_tool_module(mod_name)

    # Build the set of tool names to keep
    keep: set[str] = set()
    for mod_name in sorted(enabled_modules):
        keep.update(TOOL_MODULES[mod_name])

    # Remove tools not in the keep set (covers server.py-registered tools
    # that belong to non-enabled modules, and any stale entries)
    for tool_name in list(mcp._tool_manager._tools.keys()):
        if tool_name not in keep:
            mcp.remove_tool(tool_name)

    return sorted(enabled_modules)


# Runtime server state — populated by main(), read by get_server_config tool
_server_state: dict = {}

# Global book instance - initialized on first use
_book = None

# Class used to construct the book — set by main() to match enabled modules.
# Defaults to the "all modules" class so tests and direct imports still work.
_book_class: type = GnuCashBook


def get_book():
    """Get or create the GnuCashBook instance."""
    global _book
    if _book is None:
        path = os.environ.get("GNUCASH_BOOK_PATH")
        if not path:
            raise ValueError("GNUCASH_BOOK_PATH environment variable not set")
        _book = _book_class(path)
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


# safe_tool, _json, _strip_noise moved to gnucash_mcp/tools/_helpers.py


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


# Investments tools (commodities/prices/lots) moved to gnucash_mcp/tools/investments.py.


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


# Reconciliation/reporting/budgets/scheduling/lots tools moved to gnucash_mcp/tools/<module>.py.


# Admin tools (get_account_slots, set_account_slot, delete_account_slot,
# get_audit_log) moved to gnucash_mcp/tools/admin.py — registered on
# demand via register() when the 'admin' module is enabled.


# ============== Business Tools ==============
# Business tools (22) moved to gnucash_mcp/tools/business.py — registered
# on demand when the 'business' module is enabled.


# ============== Resources ==============


@mcp.resource("gnucash://accounts")
def accounts_resource() -> str:
    """Full chart of accounts from the GnuCash book."""
    book = get_book()
    accounts = book.list_accounts(compact=False)
    return _json(accounts)


# get_audit_log moved to gnucash_mcp/tools/admin.py.


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

    # Validate and apply module filter (also lazy-loads extracted modules)
    _validate_tool_modules()
    loaded_modules = _apply_module_filter(modules_value)

    # Build a GnuCashBook class that includes only the mixins for enabled
    # modules. If get_book() has already been called (tests / module
    # import), leave the existing instance alone; otherwise subsequent
    # get_book() calls will use this class.
    global _book_class
    _book_class = build_book_class(set(loaded_modules))

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
