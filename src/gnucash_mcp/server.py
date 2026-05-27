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

# Strict tool-argument validation: reject unknown kwargs at the MCP
# boundary instead of silently ignoring them.
#
# FastMCP generates a Pydantic model per tool from the function
# signature, all inheriting from ``ArgModelBase``. The base class's
# default config doesn't set ``extra``, so Pydantic falls back to
# ``"ignore"`` — typo'd or stale-spec parameter names silently
# no-op. Bookkeeper-found bug on PR #92 review: calling
# ``reconcile_account`` with ``except=[...]`` (the spec's name)
# instead of ``except_guids=[...]`` (the actual Python-safe param)
# ran the tool with no exclusion at all, surfacing only as a
# balance mismatch downstream.
#
# Patching ``ArgModelBase.model_config`` to include
# ``extra="forbid"`` makes the dynamically-created arg models
# reject unknown fields with a clear ``"Extra inputs are not
# permitted"`` error. Applied at import time, before any tool
# module loads.
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase

ArgModelBase.model_config = {
    **ArgModelBase.model_config,
    "extra": "forbid",
}

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
- Call get_book_summary first. It returns structure, currency, balances, warnings, and reconciliation status.
- Use list_accounts to get exact paths and short GUIDs before writing.
- Use search_transactions before creating to avoid duplicates.
- Every transaction has splits that MUST sum to zero.
ACCOUNT REFERENCES:
- Tools accept full paths ("Expenses:Groceries"), short GUIDs ("%2e78c86"), or full 32-char GUIDs.
- list_accounts emits short GUIDs at line start: "%2e78c86\\tAssets:Savings [BANK]". Reuse them — ~80% smaller than paths.
- Paths are colon-delimited, case-sensitive. Use paths when naming new accounts or reasoning about hierarchy; short GUIDs for everything else.
- Account short GUIDs: 7+ hex chars with leading "%". Transaction/split GUIDs: 8+ bare hex prefix, no marker.
DOUBLE-ENTRY SIGN CONVENTION:
- Positive = debit (increases Asset/Expense, decreases Liability/Income/Equity).
- Negative = credit (reverse).
- Credit card payment: checking -200, card +200. Income: checking +3000, income -3000.
INVESTMENT FLOW: create_lot → create_transaction (with quantity/cost) → assign_split_to_lot → create_price → calculate_lot_gain.
SLOTS: get_account_slots / set_account_slot store per-account metadata (APR, credit limit, statement day) as strings.
SAFETY: Reconciled splits are protected (use force=true to override). Prefer void_transaction over delete for audit trail. delete_account is blocked if account has children or transactions.
""",
)

# ---------------------------------------------------------------------------
# Tool module definitions — controls which tools are advertised via --modules
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# MODULE_GROUPS — composition aliases that expand to one or more modules.
#
# Lets ``--modules=core`` (or any group name) expand to a set of underlying
# module keys. Today the dict is empty — no behavior change vs. the
# pre-restructure baseline. Populated in subsequent commits as the
# role-aligned partition (Core / Personal / Portfolio / Investor /
# Freelancer / Business) takes shape.
#
# Expansion is single-pass (groups don't reference other groups). The
# partition is deliberately flat; if nesting becomes useful later we'll
# add cycle detection then.
# ---------------------------------------------------------------------------
MODULE_GROUPS: dict[str, list[str]] = {
    # ``core`` expands to its eight ledger sub-modules. Always-on
    # (force-added in _apply_module_filter), so a user with no
    # --modules flag gets all eight. Users can ALSO pick individual
    # sub-modules — e.g. ``--modules=accounts`` is valid but doesn't
    # change the fact that core is loaded too.
    "core": [
        "summary", "accounts", "transactions", "slots",
        "audit", "backup", "balance_sheet", "diagnostic",
    ],
    # ``bookkeeper`` bundles the personal-finance management
    # cluster: reconcile bank statements, run reports, manage
    # budgets, schedule recurring transactions. The four
    # underlying modules stay separately selectable for users
    # who want a finer cut.
    "bookkeeper": [
        "reconciliation", "reporting", "budgets", "scheduling",
    ],
    # ``investor`` bundles the two halves of the legacy
    # ``investments`` module: ``tax_lots`` (cost-basis tracking)
    # and ``portfolio`` (commodities + prices, the multi-currency
    # primitive). A user typing ``--modules=investor`` wants both
    # because tax-lot accounting is meaningless without prices.
    # The split is preserved at the leaf-module level so a
    # multi-currency household without a brokerage can still
    # pick ``portfolio`` alone.
    "investor": [
        "tax_lots", "portfolio",
    ],
}


# ---------------------------------------------------------------------------
# MODULE_BACKED_BY — per public-module, the set of legacy tool-file /
# mixin names needed to back its tools.
#
# Pre-restructure the mapping was 1:1 (module ``X`` → tool file
# ``tools/X.py`` → mixin ``XMixin``). The restructure breaks that:
# Core's 26 tools include void/unvoid (from ``reconciliation.py``),
# the slot tools + audit log (from ``admin.py``), and the backup tools
# (from ``backup.py``). The mixin classes still live in their original
# files; this dict tells ``_apply_module_filter`` which tool files to
# lazy-load AND ``main()`` which mixins to compose for the requested
# module set.
#
# An entry missing from this dict means "1:1 — uses the legacy name
# of the same module."
# ---------------------------------------------------------------------------
MODULE_BACKED_BY: dict[str, set[str]] = {
    # Core sub-modules — each maps to the legacy tool-file / mixin
    # name(s) that host its tools. summary/accounts/transactions/
    # balance_sheet are largely served by ``core.py`` and
    # ``reporting.py`` (balance_sheet specifically); slots/audit by
    # ``admin.py``; backup by ``backup.py``; the void/unvoid pair in
    # transactions by ``reconciliation.py``. The diagnostic
    # sub-module's one tool registers inline in server.py, so it
    # needs no backing file.
    "summary": {"core"},
    "accounts": {"core"},
    "transactions": {"core", "reconciliation"},
    "slots": {"admin"},
    "audit": {"admin"},
    "backup": {"backup"},
    "balance_sheet": {"reporting"},
    "diagnostic": set(),
    # ``portfolio`` (prices / commodities) and ``tax_lots`` (cost-
    # basis tracking) are the two halves of what used to be the
    # ``investments`` module. The ``investor`` group alias in
    # MODULE_GROUPS pulls them both in for users who want the full
    # surface.
    "portfolio": {"investments"},
    "tax_lots": {"investments"},
    # ``freelancer`` (customer-facing invoicing) and ``business``
    # (vendor + employee management, vendor bills) split the legacy
    # ``business`` module along persona lines.
    "freelancer": {"business"},
    "business": {"business"},
}


TOOL_MODULES: dict[str, list[str]] = {
    # ── Core ledger sub-modules (composed via MODULE_GROUPS["core"]) ──
    # Each is independently selectable via --modules but normally all
    # eight are loaded together because the ``core`` group alias is
    # always force-added.
    "summary": [
        "get_book_summary",
    ],
    "accounts": [
        "list_accounts",
        "get_account",
        "get_balance",
        "create_account",
        "update_account",
        "move_account",
        "delete_account",
    ],
    "transactions": [
        "list_transactions",
        "get_transaction",
        "create_transaction",
        "update_transaction",
        "delete_transaction",
        "replace_splits",
        "search_transactions",
        # Void / unvoid live here — they're the audit-preserving
        # erasure path for transactions, paired with delete_transaction
        # (which refuses posted documents and points at void).
        "void_transaction",
        "unvoid_transaction",
    ],
    "slots": [
        # Per-account metadata used by multiple modules — APR,
        # credit_limit, statement-close-day, minimum_payment.
        "get_account_slots",
        "set_account_slot",
        "delete_account_slot",
    ],
    "audit": [
        "get_audit_log",
    ],
    "backup": [
        # Auto-snapshot hook is always-on regardless; these expose
        # the manual control surface for inspecting / pruning the
        # snapshot history.
        "create_backup",
        "list_backups",
        "prune_backups",
    ],
    "balance_sheet": [
        # THE canonical accounting report — Assets, Liabilities,
        # Equity reconciling to zero. Analytical reports (cash flow,
        # spending breakdowns, net worth time series) stay in the
        # ``reporting`` module.
        "balance_sheet",
    ],
    "diagnostic": [
        # Server config introspection — loaded modules, tool count,
        # version. Always available regardless of --debug.
        "get_server_config",
    ],
    "reconciliation": [
        "get_unreconciled_splits",
        "set_reconcile_state",
        "reconcile_account",
    ],
    "reporting": [
        "spending_by_category",
        "income_by_source",
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
    # ``investments`` split into ``portfolio`` (the multi-currency
    # primitive: commodities + prices) and ``investor`` (tax-lot
    # management). A multi-currency household without a brokerage
    # picks portfolio without investor; a single-currency investor
    # picks the inverse.
    "portfolio": [
        "list_commodities",
        "create_commodity",
        "create_price",
        "get_prices",
        "get_latest_price",
        "delete_price",
    ],
    "tax_lots": [
        "create_lot",
        "list_lots",
        "get_lot",
        "assign_split_to_lot",
        "calculate_lot_gain",
        "close_lot",
    ],
    # ``business`` split into ``freelancer`` (customer-facing
    # invoicing — the natural surface for a solo consultant) and
    # ``business`` (vendor + employee management, vendor bills —
    # additive to freelancer for full small-business workflow). The
    # shared-lifecycle tools (post/unpost/pay_invoice, list/get_invoice,
    # get_outstanding_invoices) live in freelancer because customer
    # invoicing is the dominant use case; runtime owner_type gating
    # (see _gate_owner_type in tools/_helpers.py) rejects vendor-side
    # use when business isn't loaded.
    #
    # Taxtables live in freelancer because customer-facing sales
    # tax (Canadian GST, UK VAT, US state sales tax on services)
    # is a primary freelancer concern — a solo consultant
    # collecting tax on invoices needs the CRUD surface without
    # also pulling in vendor management. The polymorphic
    # add_*_entry tools share the taxtable parameter; runtime
    # use on a vendor bill or voucher reads the same taxtable
    # registry whether ``business`` is loaded or not.
    "freelancer": [
        "create_customer",
        "list_customers",
        "get_customer",
        "update_customer",
        "delete_customer",
        "create_invoice",
        "add_invoice_entry",
        "list_invoices",
        "get_invoice",
        "post_invoice",
        "unpost_invoice",
        "pay_invoice",
        "delete_invoice",
        "get_outstanding_invoices",
        "create_taxtable",
        "list_taxtables",
        "get_taxtable",
        "update_taxtable",
        "delete_taxtable",
    ],
    "business": [
        "create_vendor",
        "list_vendors",
        "get_vendor",
        "update_vendor",
        "delete_vendor",
        "create_bill",
        "add_bill_entry",
        "delete_bill",
        "create_employee",
        "list_employees",
        "get_employee",
        "update_employee",
        "delete_employee",
        # Employee expense vouchers (v1.3). Lifecycle (post /
        # unpost / pay) flows through the polymorphic invoice
        # tools in freelancer with owner_type='employee'.
        "create_voucher",
        "add_voucher_entry",
        "delete_voucher",
        # Credit notes (v1.3). Customer and vendor only —
        # employees deliberately excluded (no GnuCash desktop
        # equivalent). Lifecycle (post / unpost / pay / apply)
        # flows through the polymorphic invoice tools in
        # freelancer, detecting the credit-note slot to reverse
        # posting direction.
        "create_credit_note",
        "add_credit_note_entry",
        "delete_credit_note",
        "apply_credit_note",
        # Jobs (v1.3). Project-level grouping over invoices/bills
        # for a single customer or vendor. No posted state — only
        # active/inactive. Linked invoices route through the
        # polymorphic owner_type=3 dispatch (see create_invoice's
        # job_id parameter).
        "create_job",
        "list_jobs",
        "get_job",
        "update_job",
        "delete_job",
        "get_job_report",
        "create_billterm",
        "list_billterms",
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

    # Phantom check is scoped to modules whose backing tool files
    # aren't extracted (i.e., tools ship at server.py import time
    # rather than lazy-load). A module's backing files come from
    # MODULE_BACKED_BY when set, otherwise default 1:1 to the module
    # name itself. ``portfolio`` and ``investor`` back onto the
    # extracted ``investments`` file, so their tools are lazy-loaded
    # despite the module names being new.
    extracted = extracted_modules()
    expected_now = set()
    for mod_name, tools in TOOL_MODULES.items():
        backing = MODULE_BACKED_BY.get(mod_name, {mod_name})
        if not (backing & extracted):
            # No backing file is extracted → tools must register at
            # import time; expect them to be present already.
            expected_now.update(tools)
    phantom = expected_now - registered
    if phantom:
        raise RuntimeError(
            f"Tools in TOOL_MODULES but not registered: {sorted(phantom)}. "
            f"Remove them from TOOL_MODULES or register the tools."
        )


# Track which ``gnucash_mcp.tools.<file>`` modules have already had
# their ``register()`` called. The old heuristic — "skip if any tool
# from this module is already registered" — broke after the
# restructure: post-rebucket, ``void_transaction`` is in Core's tool
# list but lives in ``tools/reconciliation.py``. With reconciliation
# loaded first, ``any(t in registered for t in TOOL_MODULES['core'])``
# returns True (because void_transaction is registered) — so
# ``tools/core.py`` would never load, and create_transaction et al.
# would never register. Tracking files explicitly avoids the false
# positive.
_loaded_tool_files: set[str] = set()


def _lazy_load_tool_module(module_name: str) -> None:
    """Import and register an extracted tool module if not already loaded.

    ``module_name`` is the tool-file name under ``gnucash_mcp.tools``
    (matches the legacy module name; the public TOOL_MODULES keys may
    differ post-restructure).
    """
    if module_name in _loaded_tool_files:
        return
    tool_mod = importlib.import_module(f"gnucash_mcp.tools.{module_name}")
    tool_mod.register(mcp, get_book)
    _loaded_tool_files.add(module_name)


def _reset_lazy_load_state() -> None:
    """Reset the ``_loaded_tool_files`` tracker.

    Test-only helper. Production code never calls this. Tests that
    manipulate ``mcp._tool_manager._tools`` directly (e.g. clearing it
    between cases) must also reset the lazy-load tracker, otherwise
    subsequent ``_lazy_load_tool_module`` calls become no-ops while
    the registry is empty.
    """
    _loaded_tool_files.clear()


# Snapshot of which public module names are enabled in the current
# run. Populated by ``_apply_module_filter``; read by tool wrappers
# that need to gate behavior on module availability (e.g., the
# Freelancer-side shared-lifecycle invoice tools reject
# ``owner_type='vendor'`` when ``business`` isn't loaded).
_LOADED_MODULES: set[str] = set()


def is_module_enabled(name: str) -> bool:
    """True iff the given public module is in the current run's
    enabled set. Tool wrappers call this to gate per-module behavior.
    """
    return name in _LOADED_MODULES


def _apply_module_filter(modules_str: str | None) -> list[str]:
    """Enable the requested modules and remove tools not in that set.

    For extracted modules this also lazy-imports the matching
    ``gnucash_mcp/tools/<name>.py`` and calls its ``register()``
    function — so disabled extracted modules never parse their tool
    definitions.

    Args:
        modules_str: Comma-separated module/group names, ``"all"``,
            or ``None`` (core only).

    Returns:
        Sorted list of TOOL_MODULES sub-module names that were
        actually loaded. ``_LOADED_MODULES`` (the global) holds the
        same set plus the group names that expanded into it, so
        ``is_module_enabled("core")`` works alongside
        ``is_module_enabled("transactions")``.
    """
    if modules_str is None:
        requested = {"core"}
    else:
        requested = {m.strip() for m in modules_str.split(",")}

    if "all" in requested:
        enabled_modules = set(TOOL_MODULES.keys())
        groups_used: list[str] = ["all"]
    else:
        # ``core`` is always-on. Force-add before group expansion so
        # the eight core sub-modules come along even when the user
        # passes only e.g. ``--modules=reporting``.
        requested.add("core")

        # Expand groups single-pass. Groups don't reference other
        # groups (deliberate flat partition); if that changes,
        # cycle-detection lands here.
        enabled_modules: set[str] = set()
        groups_used = []
        for name in requested:
            if name in MODULE_GROUPS:
                enabled_modules.update(MODULE_GROUPS[name])
                groups_used.append(name)
            else:
                enabled_modules.add(name)

        # Warn on names that don't resolve to a known sub-module.
        known = set(TOOL_MODULES.keys()) | set(MODULE_GROUPS.keys())
        all_referenced = requested | enabled_modules
        unknown = all_referenced - known - {"all"}
        if unknown:
            print(
                f"Warning: Unknown module(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(sorted(known))}, all",
                file=sys.stderr,
            )

    # Keep only known sub-module names. Group expansion above may
    # have introduced names that aren't TOOL_MODULES keys if the
    # group def is stale; filter them out before lookup.
    enabled_modules &= set(TOOL_MODULES.keys())

    # Build a group-aware display string for get_server_config.
    # Groups render as ``group[member1, member2, ...]``; standalone
    # sub-modules (not covered by any group the user requested)
    # render bare.
    accounted_for: set[str] = set()
    display_parts: list[str] = []
    for group_name in sorted(groups_used):
        if group_name == "all":
            display_parts.append("all")
            accounted_for.update(enabled_modules)
            continue
        members = sorted(MODULE_GROUPS.get(group_name, []))
        display_parts.append(f"{group_name}[{', '.join(members)}]")
        accounted_for.update(members)
    standalone = sorted(enabled_modules - accounted_for)
    display_parts.extend(standalone)
    _server_state["modules_display"] = ", ".join(display_parts)

    # Expand each enabled module to its backing tool-files (per
    # MODULE_BACKED_BY; default 1:1). Lazy-load any backing files
    # that are extracted.
    backing_files: set[str] = set()
    for mod_name in enabled_modules:
        backing_files.update(
            MODULE_BACKED_BY.get(mod_name, {mod_name})
        )
    extracted = extracted_modules()
    for file_name in sorted(backing_files):
        if file_name in extracted:
            _lazy_load_tool_module(file_name)

    # Build the set of tool names to keep
    keep: set[str] = set()
    for mod_name in sorted(enabled_modules):
        keep.update(TOOL_MODULES[mod_name])

    # Remove tools not in the keep set (covers server.py-registered tools
    # that belong to non-enabled modules, and any stale entries)
    for tool_name in list(mcp._tool_manager._tools.keys()):
        if tool_name not in keep:
            mcp.remove_tool(tool_name)

    # Snapshot the enabled set for tool wrappers that gate behavior
    # on module availability (e.g., owner_type='vendor' on Freelancer
    # tools when Business isn't loaded). Also register any group
    # whose members are fully loaded — that way
    # ``is_module_enabled("core")`` works alongside the
    # individual sub-module checks.
    _LOADED_MODULES.clear()
    _LOADED_MODULES.update(enabled_modules)
    for group_name, members in MODULE_GROUPS.items():
        if set(members) <= enabled_modules:
            _LOADED_MODULES.add(group_name)

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


# ============== Server Diagnostic Tool ==============


def _get_server_config_impl() -> str:
    """Return current server configuration and runtime state.

    Reports loaded modules, tool count, book path, debug mode,
    and version so the client can verify its own tool inventory.
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


# Register get_server_config unconditionally at import time so it
# survives _apply_module_filter's keep-set pass (Core's tool list
# includes it). Previously gated behind --debug; now always
# available as a diagnostic surface.
@mcp.tool()
@safe_tool
def get_server_config() -> str:
    """Get the server's loaded configuration.

    Returns loaded modules, tool count, book path, debug mode,
    and version. Use this to verify which tools are available in
    this session.
    """
    return _get_server_config_impl()


# ============== Main ==============


def main() -> None:
    """Run the MCP server."""
    # Handle --help
    if "--help" in sys.argv or "-h" in sys.argv:
        print("""GnuCash MCP Server

Usage: gnucash-mcp [OPTIONS]

Options:
  --modules=MODULES    Tool modules to load (comma-separated).
                       Default: core (26 tools, always-on). Use "all"
                       for every module (106 tools).

                       Role-based selections (group aliases that
                       expand to underlying modules — start here):
                         core         Ledger primitives. Always on
                                      regardless. 26 tools.
                         bookkeeper   Reconciliation + reporting +
                                      budgets + scheduling. 20 tools.
                         investor    tax_lots + portfolio (cost basis
                                      + prices). 12 tools.
                         freelancer  Customer invoicing + sales tax
                                      (taxtables for GST / VAT /
                                      US state sales tax). 19 tools.
                         business     Additive to freelancer:
                                      vendors, employees, bills,
                                      vouchers, credit notes, jobs,
                                      billterms. 29 tools.

                       Leaf modules (pick individually for finer
                       control, or as members of the groups above):

                       Core sub-modules (8): summary, accounts,
                       transactions, slots, audit, backup,
                       balance_sheet, diagnostic.

                       Bookkeeper members (4): reconciliation,
                       reporting, budgets, scheduling.

                       Investor members (2): tax_lots, portfolio.

                       Example: --modules=freelancer for a solo
                       invoicer; --modules=bookkeeper for personal
                       finance; --modules=freelancer,business for a
                       small business with vendor management.
                       ``core`` is always added regardless.
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
    # Expand each loaded module to its backing mixin set. With the
    # Core restructure (void/unvoid + admin tools + backups migrated
    # into Core), the mixin layer still owns those methods in their
    # original files — Core needs CoreMixin + ReconciliationMixin +
    # AdminMixin + BackupMixin composed together to back its 26
    # tools. ``MODULE_BACKED_BY`` provides the mapping; modules not
    # listed there default to 1:1 (the legacy convention).
    backing_mixins: set[str] = set()
    for mod_name in loaded_modules:
        backing_mixins.update(
            MODULE_BACKED_BY.get(mod_name, {mod_name})
        )
    _book_class = build_book_class(backing_mixins)

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

    # Populate runtime state and conditionally register debug tool.
    # ``modules_display`` was set by ``_apply_module_filter`` with
    # group-aware rendering (``core[summary, accounts, ...], reporting``
    # rather than the flat list of 8+ sub-modules). Fall back to a
    # bare join if the display key wasn't set for some reason.
    tool_count = len(mcp._tool_manager._tools)
    modules_display = _server_state.get(
        "modules_display", ", ".join(loaded_modules)
    )
    _server_state.update({
        "modules": modules_display,
        "tool_count": tool_count,
        "book_path": book_path or "not set",
        "debug": debug_flag,
        "default_currency_ok": currency_ok,
    })

    # get_server_config is now registered unconditionally at module
    # import time (see above), so no per-run conditional registration
    # is needed here.
    if debug_flag or _debug_mode:
        debug_log(f"Modules: {modules_display}")
        debug_log(f"Tools loaded: {tool_count}")

    mcp.run()


if __name__ == "__main__":
    main()
