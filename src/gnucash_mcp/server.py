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
# FastMCP's per-tool Pydantic models inherit ``ArgModelBase``, whose
# config doesn't set ``extra`` — Pydantic falls back to "ignore", so
# typo'd or stale-spec parameter names silently no-op. Example:
# calling ``reconcile_account`` with ``except=[...]`` (the spec's
# name) instead of ``except_guids=[...]`` ran the tool with no
# exclusion at all, surfacing only as a balance mismatch downstream.
# Patching ``extra="forbid"`` in makes the arg models reject unknown
# fields loudly. Applied at import, before any tool module loads.
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
# MODULE_GROUPS — composition aliases expanding to underlying module
# keys; the role-aligned partition (core / bookkeeper / investor /
# business). Expansion is single-pass — groups don't reference other
# groups; add cycle detection if nesting ever lands.
# ---------------------------------------------------------------------------
MODULE_GROUPS: dict[str, list[str]] = {
    # ``core`` is always-on (force-added in _apply_module_filter).
    # ``reconciliation`` belongs here: reconciliation touches money
    # and every configuration touches money — excluding it from any
    # persona cut produces a server that can't reconcile statements.
    "core": [
        "summary", "accounts", "transactions", "slots",
        "audit", "backup", "balance_sheet", "diagnostic",
        "reconciliation",
    ],
    # Personal-finance cluster; members stay separately selectable.
    "bookkeeper": [
        "reporting", "budgets", "scheduling",
    ],
    # Both halves of the legacy ``investments`` module — tax-lot
    # accounting is meaningless without prices, but a multi-currency
    # household without a brokerage can still pick portfolio alone.
    "investor": [
        "tax_lots", "portfolio",
    ],
    # Small-business persona. The group must stay the SUPERSET of
    # both halves — loading only the vendor half would give
    # "small business workflow" users vendor management without the
    # ability to create or post a customer invoice.
    "business": [
        "freelancer", "business_complete",
    ],
}


# ---------------------------------------------------------------------------
# MODULE_BACKED_BY — per public module, the legacy tool-file / mixin
# names backing its tools. The public partition no longer maps 1:1
# onto files (e.g. ``transactions`` spans core.py + reconciliation.py);
# this dict tells ``_apply_module_filter`` which tool files to
# lazy-load and ``main()`` which mixins to compose. A missing entry
# means 1:1 with the module's own name.
# ---------------------------------------------------------------------------
MODULE_BACKED_BY: dict[str, set[str]] = {
    # diagnostic's one tool registers inline in server.py — no
    # backing file.
    "summary": {"core"},
    "accounts": {"core"},
    "transactions": {"core", "reconciliation"},
    "slots": {"admin"},
    "audit": {"admin"},
    "backup": {"backup"},
    "balance_sheet": {"reporting"},
    "diagnostic": set(),
    "portfolio": {"investments"},
    "tax_lots": {"investments"},
    # freelancer / business_complete are subsets of one underlying
    # tools/business.py registration. The ``business`` group alias
    # doesn't appear here — groups resolve via their members.
    "freelancer": {"business"},
    "business_complete": {"business"},
}


TOOL_MODULES: dict[str, list[str]] = {
    # ── Core ledger sub-modules (composed via MODULE_GROUPS["core"]) ──
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
        "create_transactions",
        "update_transaction",
        "delete_transaction",
        "replace_splits",
        "search_transactions",
        # Void / unvoid: the audit-preserving erasure path, paired
        # with delete_transaction.
        "void_transaction",
        "unvoid_transaction",
    ],
    "slots": [
        # Per-account metadata (APR, credit_limit, statement day).
        "get_account_slots",
        "set_account_slot",
        "delete_account_slot",
    ],
    "audit": [
        "get_audit_log",
    ],
    "backup": [
        # Manual control surface; the auto-snapshot hook is
        # always-on regardless.
        "create_backup",
        "list_backups",
        "prune_backups",
    ],
    "balance_sheet": [
        # THE canonical report; analytical reports stay in
        # ``reporting``.
        "balance_sheet",
    ],
    "diagnostic": [
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
    # ``investments`` split: portfolio = the multi-currency
    # primitive (commodities + prices); tax_lots = cost basis.
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
    # Placement rule for the freelancer/business_complete split:
    # polymorphic and shared-registry tools (invoice lifecycle,
    # taxtables, billterms, jobs, credit notes) live in freelancer
    # because the customer side is the dominant solo-consultant use
    # case; vendor-side use of the same tools is rejected at runtime
    # by _gate_owner_type (tools/_helpers.py) when
    # business_complete isn't loaded. business_complete owns the
    # vendor/employee ENTITIES and the workflows that don't make
    # sense without them.
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
        # Billterms, jobs, credit notes — placement rule above.
        "create_billterm",
        "list_billterms",
        "create_job",
        "list_jobs",
        "get_job",
        "update_job",
        "delete_job",
        "get_job_report",
        "create_credit_note",
        "add_credit_note_entry",
        "delete_credit_note",
        "apply_credit_note",
    ],
    # Vendor + employee surface — see the placement rule above.
    "business_complete": [
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
        # Voucher lifecycle (post/unpost/pay) flows through the
        # polymorphic invoice tools with owner_type='employee'.
        "create_voucher",
        "add_voucher_entry",
        "delete_voucher",
        "vendor_spending_report",
    ],
}


def _validate_module_groups() -> None:
    """Every member of a MODULE_GROUPS expansion must exist in
    TOOL_MODULES — a typo'd member would otherwise silently drop
    out of the expansion, surfacing only as "tool X not available"
    with no hint that the alias was the cause. Fires at startup
    alongside ``_validate_tool_modules``.
    """
    known = set(TOOL_MODULES.keys())
    bad: dict[str, list[str]] = {}
    for group, members in MODULE_GROUPS.items():
        missing = [m for m in members if m not in known]
        if missing:
            bad[group] = missing
    if bad:
        report = "; ".join(
            f"{g}={sorted(ms)}" for g, ms in sorted(bad.items())
        )
        raise RuntimeError(
            f"MODULE_GROUPS references unknown module(s) in "
            f"TOOL_MODULES: {report}. Add the modules to "
            f"TOOL_MODULES or correct the group definition."
        )


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

    # Phantom check scoped to modules whose backing files aren't
    # extracted (tools shipping at import time, not lazy-load).
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


# Tool files whose register() has run. Tracked explicitly — an
# "any tool from this module already registered" heuristic breaks
# because modules span files (void_transaction is in Core's list
# but lives in tools/reconciliation.py; with reconciliation loaded
# first, the heuristic would skip tools/core.py entirely).
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

    # Fail fast on unknown names — a stderr warning + partial load
    # is silent in practice (Claude Desktop buries MCP stderr), so
    # a typo'd ``--modules=bookeeper`` would just look like missing
    # tools. Same principle as ``extra="forbid"`` on tool kwargs.
    # Validation runs BEFORE the ``all`` check: ``all`` is a
    # loading instruction, not a validation bypass.
    known = set(TOOL_MODULES.keys()) | set(MODULE_GROUPS.keys())
    unknown = requested - known - {"all"}
    if unknown:
        # did-you-mean via difflib — close enough without a
        # Levenshtein dependency.
        import difflib
        lines = ["Unknown module name(s) on --modules / GNUCASH_MCP_MODULES:"]
        for bad in sorted(unknown):
            matches = difflib.get_close_matches(
                bad, sorted(known), n=1, cutoff=0.6,
            )
            if matches:
                lines.append(f"  - {bad!r}  (did you mean {matches[0]!r}?)")
            else:
                lines.append(f"  - {bad!r}")
        lines.append("")
        lines.append(
            f"Valid names: {', '.join(sorted(known))}, all"
        )
        lines.append("")
        lines.append(
            "Fix the typo and restart the server. Partial-load "
            "was the previous behavior; v1.3 fails fast so "
            "configuration errors surface at startup instead "
            "of as missing tools downstream."
        )
        message = "\n".join(lines)
        print(message, file=sys.stderr)
        raise SystemExit(2)

    if "all" in requested:
        enabled_modules = set(TOOL_MODULES.keys())
        groups_used: list[str] = ["all"]
    else:
        # core is always-on — force-add before group expansion.
        requested.add("core")

        # Single-pass group expansion (flat partition).
        enabled_modules: set[str] = set()
        groups_used = []
        for name in requested:
            if name in MODULE_GROUPS:
                enabled_modules.update(MODULE_GROUPS[name])
                groups_used.append(name)
            else:
                enabled_modules.add(name)

    # Drop names a stale group def may have introduced.
    enabled_modules &= set(TOOL_MODULES.keys())

    # Group-aware display string for get_server_config:
    # ``group[member1, ...]``, standalone sub-modules bare.
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

    # Lazy-load each enabled module's extracted backing files.
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

    # Snapshot for is_module_enabled; fully-loaded groups register
    # too so group and sub-module checks both work.
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


# Tool registration lives in gnucash_mcp/tools/<module>.py — every
# module has its own file and registers lazily via
# _apply_module_filter.


# ============== Resources ==============


@mcp.resource("gnucash://accounts")
def accounts_resource() -> str:
    """Full chart of accounts from the GnuCash book."""
    book = get_book()
    accounts = book.list_accounts(compact=False)
    return _json(accounts)


# ============== Server Diagnostic Tool ==============


def _get_server_config_impl() -> str:
    """Return current server configuration and runtime state.

    Reports loaded modules, tool count, book filename, debug mode,
    and version so the client can verify its own tool inventory.
    The book is shown as filename only — see
    ``_book_display_name`` for the privacy rationale.
    """
    from gnucash_mcp import __version__
    from gnucash_mcp._format import _book_display_name
    lines = [
        f"Modules loaded: {_server_state.get('modules', 'unknown')}",
        f"Tools available: {_server_state.get('tool_count', 'unknown')}",
        f"Book: {_book_display_name(_server_state.get('book_path'))}",
        f"Debug mode: {str(_server_state.get('debug', False)).lower()}",
        f"Version: {__version__}",
    ]
    dc_ok = _server_state.get("default_currency_ok")
    if dc_ok is False:
        lines.append("Warning: Book has no default currency set")
    return "\n".join(lines)


# Registered unconditionally at import so it survives
# _apply_module_filter's keep-set pass.
#
# Deliberately omits ``@audit_log`` — the omission IS the contract.
# This zero-side-effect config inspection is called reflexively
# during orientation; logging it adds noise without answering any
# bookkeeping question, cluttering the trail the bookkeeper reviews.
# A contributor adding @audit_log here should confirm a real reason
# to override.
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
                       Default: core (30 tools, always-on). Use "all"
                       for every module (107 tools).

                       Role-based selections (group aliases that
                       expand to underlying modules — start here):
                         core         Ledger primitives + reconciliation.
                                      Always on regardless. 30 tools.
                         bookkeeper   Reporting + budgets + scheduling.
                                      17 tools.
                         investor     tax_lots + portfolio (cost basis
                                      + prices). 12 tools.
                         freelancer   Customer invoicing + sales tax,
                                      plus billterms (payment terms),
                                      jobs (per-project P&L), and
                                      credit notes (customer refunds).
                                      The full solo-consultant
                                      toolkit. 31 tools.
                         business     Full small-business package:
                                      freelancer (invoicing) +
                                      business_complete (vendors,
                                      employees, bills, vouchers,
                                      vendor reports). 48 tools.

                       Leaf modules (pick individually for finer
                       control, or as members of the groups above):

                       Core sub-modules (9): summary, accounts,
                       transactions, slots, audit, backup,
                       balance_sheet, diagnostic, reconciliation.

                       Bookkeeper members (3): reporting, budgets,
                       scheduling.

                       Investor members (2): tax_lots, portfolio.

                       Business members (2): freelancer,
                       business_complete.

                       Example: --modules=freelancer for a solo
                       invoicer with no vendor activity;
                       --modules=bookkeeper for personal finance;
                       --modules=business for a complete small-
                       business workflow (invoices + vendor + employee
                       management). ``core`` is always added
                       regardless.
  --debug              Enable debug logging (MCP protocol traffic, timing)
  --noaudit            Disable audit logging
  -h, --help           Show this help message

Environment variables:
  GNUCASH_BOOK_PATH          Path to GnuCash SQLite book (required)
  GNUCASH_MCP_MODULES        Tool modules to load — same values as
                             --modules (e.g. "bookkeeper" or "core,reporting")
  GNUCASH_MCP_DEBUG=1        Enable debug logging
  GNUCASH_MCP_NOAUDIT=1      Disable audit logging
  GNUCASH_LOG_DIR            Override the .mcp storage directory (audit,
                             debug, backups). Default: {book_path}.mcp
  GNUCASH_REDACT_PATHS=1     Collapse absolute paths to basenames in tool
                             responses and error messages (safe to share)
  GNUCASH_FX_GUARD_DAYS      Refuse a cross-currency invoice/bill post or pay
                             when the chosen rate is this many days from the
                             document's own date (default 7; force=true
                             overrides, up to the staleness cap)
  GNUCASH_FX_STALENESS_DAYS  Max distance in days a price may sit from the
                             valuation/as-of date before it is rejected
                             (default 90; 0 or negative disables the cap)
  GNUCASH_WRITE_RATE_LIMIT   Write throttle in tokens/sec (token bucket).
                             Absent or non-positive disables limiting
  GNUCASH_WRITE_BURST        Token-bucket size for the write throttle
                             (default 10; applies only when the rate is set)

Logs and backups live under the .mcp directory — beside the book file by
default, or at GNUCASH_LOG_DIR if set:
  {book_path}.mcp/audit/YYYY-MM-DD.txt
  {book_path}.mcp/debug/YYYY-MM-DD.log   (when debug enabled)
  {book_path}.mcp/backups/               (auto + manual snapshots)
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
    _validate_module_groups()
    _validate_tool_modules()
    loaded_modules = _apply_module_filter(modules_value)

    # Build a GnuCashBook class that includes only the mixins for enabled
    # modules. If get_book() has already been called (tests / module
    # import), leave the existing instance alone; otherwise subsequent
    # get_book() calls will use this class.
    global _book_class
    # Expand each loaded module to its backing mixin set via
    # MODULE_BACKED_BY (default 1:1).
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

    # ``modules_display`` carries _apply_module_filter's group-aware
    # rendering; fall back to a bare join if unset.
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

    if debug_flag or _debug_mode:
        debug_log(f"Modules: {modules_display}")
        debug_log(f"Tools loaded: {tool_count}")

    mcp.run()


if __name__ == "__main__":
    main()
