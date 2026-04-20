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
    "backup": [
        "create_backup",
        "list_backups",
        "prune_backups",
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

    # `backup` is never optional — the auto-snapshot hook protects
    # every user against data loss regardless of what modules they
    # asked for, and the three manual tools (create_backup /
    # list_backups / prune_backups) are small enough to always
    # advertise. Treat it the same way as `core`.
    enabled_modules.add("backup")

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
# Logs are stored alongside the book file: {book_path}.mcp/audit/ and {book_path}.mcp/debug/
_debug_mode = os.environ.get("GNUCASH_MCP_DEBUG") == "1"
_audit_mode = os.environ.get("GNUCASH_MCP_NOAUDIT") != "1"
_book_path = os.environ.get("GNUCASH_BOOK_PATH")
if _book_path and (_audit_mode or _debug_mode):
    setup_logging(
        book_path=_book_path,
        debug=_debug_mode,
        audit=_audit_mode,
        get_book=get_book,
    )
    if _debug_mode:
        debug_log(f"Server module loaded. Book path: {_book_path}")


# safe_tool, _json, _strip_noise moved to gnucash_mcp/tools/_helpers.py


# ============== Tools ==============
# Core tools moved to gnucash_mcp/tools/core.py — every module now
# lives in its own file and registers lazily via _apply_module_filter.


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
  -h, --help           Show this help message

Environment variables:
  GNUCASH_BOOK_PATH          Path to GnuCash SQLite book (required)
  GNUCASH_MCP_MODULES        Tool modules to load (e.g., "core,reporting")
  GNUCASH_MCP_DEBUG=1        Enable debug logging
  GNUCASH_MCP_NOAUDIT=1      Disable audit logging

Logs are stored alongside the book file:
  {book_path}.mcp/audit/YYYY-MM-DD.txt
  {book_path}.mcp/debug/YYYY-MM-DD.log   (when debug enabled)
""")
        sys.exit(0)

    book_path = os.environ.get("GNUCASH_BOOK_PATH")

    # Parse CLI flags
    debug_flag = "--debug" in sys.argv
    noaudit_flag = "--noaudit" in sys.argv
    modules_value = None

    # Parse --key=value flags
    for arg in sys.argv[:]:
        if arg.startswith("--modules="):
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
    if debug_flag or noaudit_flag:
        audit_enabled = not noaudit_flag
        if book_path and (audit_enabled or debug_flag):
            setup_logging(
                book_path=book_path,
                debug=debug_flag,
                audit=audit_enabled,
                get_book=get_book,
            )
            if debug_flag:
                debug_log(f"Server starting via CLI. Book: {book_path}")
                debug_log(f"Debug logging enabled, audit={'enabled' if audit_enabled else 'disabled'}")

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
