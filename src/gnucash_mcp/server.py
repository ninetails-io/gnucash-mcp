"""MCP server definition for GnuCash."""

import importlib
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from pydantic import Field

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

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
- Tools accept full paths ("Expenses:Groceries"), short GUIDs ("%xxxxxxx" form), or full 32-char GUIDs.
- list_accounts emits short GUIDs at line start: "%xxxxxxx\\tAssets:Savings [BANK]" (x's are a placeholder — copy real GUIDs from list_accounts output). Reuse them — ~80% smaller than paths.
- Paths are colon-delimited, case-sensitive. Use paths when naming new accounts or reasoning about hierarchy; short GUIDs for everything else.
- Account short GUIDs: 7+ hex chars with leading "%". Transaction/split GUIDs: 8+ bare hex prefix, no marker.
ANNOTATIONS (visibility order in GnuCash's register):
- description = clean payee/name. notes = what the purchase was, interpreted (double-line view — what humans read). Bank-leg split memo = raw statement line (provenance; only visible in expanded splits).
DOUBLE-ENTRY SIGN CONVENTION:
- Positive = debit (increases Asset/Expense, decreases Liability/Income/Equity).
- Negative = credit (reverse).
- Credit card payment: checking -200, card +200. Income: checking +3000, income -3000.
INVESTMENT FLOW: create_lot → create_transaction (with quantity/cost) → assign_split_to_lot → create_price → calculate_lot_gain.
SLOTS: get_account_slots / set_account_slot store per-account metadata (APR, credit limit, statement day) as strings.
OUTPUT: every tool's compact default is complete — verbose=true adds structure (JSON), not information. Compact is cheaper; prefer it unless you need machine-readable fields.
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
        "update_transactions",
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
        "get_reconciliation_status",
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
        "create_prices",
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


# Inline tools registered at import that are deliberately NOT part of
# the TOOL_MODULES partition. ``switch_book`` is a conditional
# meta-tool: registered inline like get_server_config, but kept only
# when 2+ books are configured (added to the keep set in
# _apply_module_filter), so it can't live in a static module list. The
# validators and the contract tests exclude it.
_INLINE_UNMAPPED_TOOLS: set[str] = {"switch_book"}


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
    unmapped = registered - all_mapped - _INLINE_UNMAPPED_TOOLS
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


def _module_tool_count(name: str) -> int:
    """Tool count for a module or group name — display/help only.

    ``"all"`` counts every mapped tool. Group names expand via
    MODULE_GROUPS; leaf names count their own TOOL_MODULES entry.
    Inline conditional tools (switch_book) are not included — they
    exist outside the module partition.
    """
    if name == "all":
        return sum(len(tools) for tools in TOOL_MODULES.values())
    members = MODULE_GROUPS.get(name, [name])
    return len({t for m in members for t in TOOL_MODULES.get(m, [])})


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

    # switch_book is an inline meta-tool outside the module partition;
    # keep it only when there are 2+ books to switch between. Otherwise
    # the removal pass below drops it (single-book sessions have
    # nothing to switch to).
    if multi_book_active():
        keep.add("switch_book")

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

    _apply_tool_annotations()

    return sorted(enabled_modules)


# ---------------------------------------------------------------------------
# MCP ToolAnnotations — derived from the @audit_log declaration each
# tool already carries, not from a second per-tool table that could
# drift. classification="read" → readOnlyHint; write operations map
# through _WRITE_VERB_HINTS. openWorldHint is False across the board:
# every tool operates on a closed local book, never the network.
# ---------------------------------------------------------------------------

# operation verb → (destructiveHint, idempotentHint).
# destructive means "modifies or removes EXISTING book data";
# purely additive writes (new entities, new transactions) are False.
# idempotent means "repeating the same call adds nothing further":
# overwrites and removals converge, additions accumulate.
_WRITE_VERB_HINTS: dict[str, tuple[bool, bool]] = {
    "create": (False, False),
    "create_batch": (False, False),
    "create_from_scheduled": (False, False),
    "delete": (True, True),
    "delete_slot": (True, True),
    "set_slot": (True, True),
    "update_batch": (True, True),
    "update": (True, True),
    "void": (True, True),
    "unvoid": (True, True),
    "unpost": (True, True),        # removes the posting transaction
    "set_state": (True, True),
    "replace_splits": (True, True),
    "reconcile": (True, True),
    "post": (False, True),         # additive; re-post errors, adds nothing
    "pay": (False, False),         # paying twice records two payments
    "apply": (False, False),
}

# Inline tools registered without @audit_log. switch_book mutates
# server state (the active book), not book data.
_INLINE_TOOL_ANNOTATIONS: dict[str, ToolAnnotations] = {
    "get_server_config": ToolAnnotations(
        readOnlyHint=True, openWorldHint=False,
    ),
    "switch_book": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False,
        idempotentHint=True, openWorldHint=False,
    ),
}


def _derive_tool_annotations(name: str, fn) -> "ToolAnnotations | None":
    """Annotations for one registered tool, or None if underivable."""
    if name in _INLINE_TOOL_ANNOTATIONS:
        return _INLINE_TOOL_ANNOTATIONS[name]
    meta = getattr(fn, "__audit_meta__", None)
    if meta is None:
        return None
    if meta["classification"] == "read":
        return ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    destructive, idempotent = _WRITE_VERB_HINTS.get(
        meta["operation"] or "", (True, False),  # unknown verb: safest hints
    )
    return ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=False,
    )


def _apply_tool_annotations() -> None:
    """Set ToolAnnotations on every registered tool.

    Runs at the end of _apply_module_filter — the chokepoint every
    enabled tool passes through — so lazy-loaded and inline tools
    alike are annotated before any client lists them. Tools without
    a derivable classification are left as-is; the contract test
    (TestToolAnnotations) is the loud gate for that.
    """
    for name, tool in mcp._tool_manager._tools.items():
        ann = _derive_tool_annotations(name, tool.fn)
        if ann is not None:
            tool.annotations = ann


# Runtime server state — populated by main(), read by get_server_config tool
_server_state: dict = {}

# ── Multi-book registry ────────────────────────────────────────────
# GNUCASH_BOOK_PATH may hold a single path OR an os.pathsep-separated
# list (``:`` on POSIX, ``;`` on Windows — the PATH convention).
# The server keeps one "current" book and switches between them
# in-session via the switch_book tool (no restart, no re-registration —
# every tool calls get_book() per operation, so repointing _book is
# transparent to all of them).
#
# - _book_paths:    resolved, validated paths in declared order.
# - _current_path:  the active book's resolved path.
# - _book_registry: lazy instance cache keyed by resolved-path str, so
#                   re-selecting a book reuses its instance (and its
#                   GUID-prefix caches).
# - _book:          the CURRENT instance. Resetting it to None is the
#                   re-init point tests rely on.
#
# _book is an UNLOCKED global read by get_book() and written by
# switch_book. That is safe only while every tool is a sync function
# and the installed MCP SDK runs sync tools inline on the event loop
# (mcp's FuncMetadata does; standalone fastmcp 2.x thread-pools them
# instead). Under that scheduling, a whole tool call — audit staging
# included — is atomic with respect to switch_book. If a tool ever
# becomes async, or the SDK changes, reads of _book mid-call can see
# a different book than the call started on (cross-book audit
# attribution, wrong-book writes). test_all_tools_are_sync in
# tests/test_modules.py pins our half of that contract.
_book_paths: list[Path] = []
_current_path: Path | None = None
_book_registry: dict[str, GnuCashBook] = {}
_book = None

# Effective logging mode. Seeded from env at import; main() may widen
# it via the --debug / --noaudit CLI flags. switch_book reads these to
# re-point logging at the newly-active book.
_logging_debug: bool = False
_logging_audit: bool = True

# Class used to construct the book — set by main() to match enabled modules.
# Defaults to the "all modules" class so tests and direct imports still work.
_book_class: type = GnuCashBook


class _BookPathError(FileNotFoundError):
    """Invalid GNUCASH_BOOK_PATH entry (missing file / not a file /
    duplicate filename).

    Subclasses FileNotFoundError so a missing book surfaces at runtime
    with the same ``file_not_found`` error_type the single-book path
    always produced (the book constructor raised FileNotFoundError).
    main() catches the specific type to fail fast with SystemExit at
    startup. The *unset/empty* env case is a plain ValueError instead
    — that path's "GNUCASH_BOOK_PATH ... not set" contract predates
    multi-book and is never reached from main() (which guards on a
    non-empty value first).
    """


def _parse_book_paths(value: str | None) -> list[Path]:
    """Parse GNUCASH_BOOK_PATH (one path, or an os.pathsep-separated list).

    The separator is ``os.pathsep`` — ``:`` on POSIX, ``;`` on Windows
    — the same convention PATH/PYTHONPATH use. Comma was rejected as a
    separator because it is a common filename character (a book named
    ``financials, invoicing.gnucash`` would have mis-split, and no
    shell escaping could prevent it — the shell can't signal "this
    separator byte is literal" to a downstream parser). The one
    residual limit, a literal ``os.pathsep`` inside a path, is rare and
    identical to PATH's own constraint.

    Splits on ``os.pathsep``, strips, drops empties, and resolves each
    entry to an absolute path that must exist and be a regular file.

    Raises:
        ValueError: value unset or empty.
        _BookPathError: any entry missing or not a regular file; or two
            entries sharing a filename (basename collisions make
            switch_book's prefix matching ambiguous).
    """
    if not value or not value.strip():
        raise ValueError(
            "GNUCASH_BOOK_PATH environment variable not set"
        )
    raw = [p.strip() for p in value.split(os.pathsep) if p.strip()]
    if not raw:
        # Separator-only (e.g. ":") is an invalid VALUE, not an unset
        # variable — _BookPathError so both fail-fast sites (import
        # block and main()) catch it instead of an uncaught
        # ValueError traceback.
        raise _BookPathError(
            "Invalid GNUCASH_BOOK_PATH: contains only separators, "
            "no paths"
        )
    return _validate_book_paths(raw)


def _validate_book_paths(
    raw: list[str], *, source: str = "GNUCASH_BOOK_PATH"
) -> list[Path]:
    """Resolve and validate a list of book path strings.

    The single chokepoint for inbound book lists — both the
    GNUCASH_BOOK_PATH env var (via _parse_book_paths) and the --book
    CLI argument land here, so existence, regular-file, and
    filename-stem uniqueness rules cannot diverge between the two
    interfaces. ``source`` names the interface in error messages.

    Empty entries are dropped rather than resolved — an MCPB host
    expanding an unset multi-file config could hand --book an empty
    string, and ``Path("").resolve(strict=True)`` would otherwise
    "find" the working directory.
    """
    raw = [p for p in (s.strip() for s in raw) if p]
    if not raw:
        raise _BookPathError(f"Invalid {source}: no paths given")
    paths: list[Path] = []
    errors: list[str] = []
    for p in raw:
        try:
            resolved = Path(p).resolve(strict=True)
        except (FileNotFoundError, OSError):
            errors.append(f"  - {p!r}: not found")
            continue
        if not resolved.is_file():
            errors.append(f"  - {p!r}: not a regular file")
            continue
        paths.append(resolved)

    # Filenames must be unique — switch_book matches on them, and it
    # matches case-INSENSITIVELY, so uniqueness must be checked the
    # same way: Ledger.gnucash + ledger.gnucash would make every
    # prefix ambiguous and the second book permanently unswitchable.
    # STEMS must be unique too (also case-insensitively): backup
    # state files, retention scoping, and backup filenames are all
    # keyed by stem, so ledger.gnucash + ledger.xac sharing a
    # GNUCASH_LOG_DIR would share .state-ledger.json and prune each
    # other's snapshots — the cross-book data-loss class the stem
    # scoping exists to prevent.
    seen: dict[str, Path] = {}
    for path in paths:
        stem_key = path.stem.lower()
        if stem_key in seen:
            errors.append(
                f"  - duplicate book filename stem {path.stem!r}: "
                f"{seen[stem_key]} and {path} (book filename stems "
                f"must be unique, case-insensitively — switch_book "
                f"matches by name and backups are scoped by stem)"
            )
        else:
            seen[stem_key] = path

    if errors:
        raise _BookPathError(
            f"Invalid {source}:\n" + "\n".join(errors)
        )
    return paths


def _apply_book_args(book_args: list[str]) -> None:
    """Install --book CLI paths as the active book list.

    Args win over GNUCASH_BOOK_PATH. The resolved list is mirrored
    back into the env var (os.pathsep-joined) because get_book()
    re-reads the env whenever ``_book`` is reset — without the
    mirror, a test-style reset would silently fall back to the
    env's books. Logging is (re-)pointed here too: when the books
    arrive via CLI only, the import-time logging block never ran
    (it reads the env, which was unset at import).
    """
    global _book_paths, _current_path, _book
    _book_paths = _validate_book_paths(book_args, source="--book")
    _current_path = _book_paths[0]
    _book = None
    os.environ["GNUCASH_BOOK_PATH"] = os.pathsep.join(
        str(p) for p in _book_paths
    )
    _activate_logging(_current_path)


_SQLITE_MAGIC = b"SQLite format 3\x00"


def _book_format_error(path: Path) -> str | None:
    """Detect a non-SQLite book at startup; return a message a
    non-developer can act on, or None when the header looks right.

    The #1 predictable mistake in the bundle's file picker is a book
    saved in GnuCash's XML format (uncompressed ``<?xml`` or the
    gzip default) — piecash would otherwise fail on it at first tool
    call with a DatabaseError a non-developer can't decode.
    Unreadable files return None: the real open surfaces its own
    error with the existing error_type contract.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(len(_SQLITE_MAGIC))
    except OSError:
        return None
    if head.startswith(_SQLITE_MAGIC):
        return None
    if head.startswith(b"\x1f\x8b") or head.lstrip().startswith(b"<?xml"):
        return (
            f"{path.name} is in GnuCash's XML format. Open it in "
            "GnuCash and use File > Save As with the sqlite3 format, "
            "then pick the new file."
        )
    return (
        f"{path.name} does not look like a GnuCash book in SQLite "
        "format (unrecognized file header). In GnuCash, use "
        "File > Save As with the sqlite3 format, then pick the "
        "new file."
    )


def _book_for(path: Path) -> GnuCashBook:
    """Get-or-create the cached book instance for a resolved path."""
    key = str(path)
    inst = _book_registry.get(key)
    if inst is None:
        inst = _book_class(key)
        _book_registry[key] = inst
    return inst


def multi_book_active() -> bool:
    """True when more than one valid book path is configured.

    Read by tool wrappers (e.g. get_book_summary) to decide whether to
    surface the current-book marker, and by _apply_module_filter to
    gate switch_book's visibility.
    """
    return len(_book_paths) > 1


def get_book():
    """Get or create the current GnuCashBook instance.

    Honors an os.pathsep-separated GNUCASH_BOOK_PATH: when no book is
    selected yet, the first valid path becomes current. switch_book
    repoints the selection mid-session. Resetting ``_book`` to None
    forces re-initialization from the environment — the reset point
    tests rely on.
    """
    global _book, _current_path, _book_paths
    if _book is None:
        # Re-read the env so a test that swapped GNUCASH_BOOK_PATH and
        # reset _book picks up the new value.
        _book_paths = _parse_book_paths(os.environ.get("GNUCASH_BOOK_PATH"))
        if _current_path not in _book_paths:
            _current_path = _book_paths[0]
        _book = _book_for(_current_path)
    return _book


def _activate_logging(path: Path) -> None:
    """(Re-)point audit/debug logging at ``path``.

    Used on book switch so each book's writes land in its own
    .mcp/audit trail. setup_logging clears its handlers on every call,
    so repeated invocations don't stack handlers.
    """
    if _logging_audit or _logging_debug:
        setup_logging(
            book_path=str(path),
            debug=_logging_debug,
            audit=_logging_audit,
            get_book=get_book,
        )


def _book_orientation(book_instance) -> str:
    """One-line snapshot of a book for post-switch reorientation:
    ``N transactions | N customers | N vendors | N employees | CUR
    base currency``.

    Transactions exclude scheduled-transaction template recipes (the
    same filter get_book_summary uses, so the counts agree). Business
    counts are omitted when zero so personal books stay uncluttered.
    Best-effort — a locked or unreadable book yields a soft fallback
    rather than failing the switch.
    """
    try:
        with book_instance.open(readonly=True) as book:
            template_guids = book_instance._template_account_guids(book)
            txns = sum(
                1 for t in book.transactions
                if not book_instance._is_template_transaction(
                    t, template_guids
                )
            )
            parts = [f"{txns:,} transaction{'s' if txns != 1 else ''}"]
            for label, coll in (
                ("customer", book.customers),
                ("vendor", book.vendors),
                ("employee", book.employees),
            ):
                n = len(coll)
                if n:
                    parts.append(f"{n} {label}{'s' if n != 1 else ''}")
            cur = book.default_currency
            parts.append(f"{cur.mnemonic if cur else '?'} base currency")
            return " | ".join(parts)
    except Exception:
        return "(book ready; orientation snapshot unavailable)"


def _switch_book_impl(name: str) -> str:
    """Make the book whose filename uniquely prefix-matches ``name``
    the current book. See the switch_book tool docstring.
    """
    global _book, _current_path
    if not _book_paths:
        # Cold start (direct single-call use): populate the registry.
        get_book()

    needle = name.strip().lower()
    if not needle:
        raise ValueError("switch_book requires a book name")

    matches = [p for p in _book_paths if p.name.lower().startswith(needle)]
    available = ", ".join(p.name for p in _book_paths)
    if not matches:
        raise ValueError(
            f"No book matches {name!r}. Available books: {available}"
        )
    if len(matches) > 1:
        ambiguous = ", ".join(p.name for p in matches)
        raise ValueError(
            f"Book name {name!r} is ambiguous — matches: {ambiguous}. "
            f"Use a longer prefix."
        )

    target = matches[0]
    previous = _current_path

    # No-op switch — already on this book. Don't emit the reset
    # banner (nothing changed); just reaffirm and reorient. The
    # identity check on _book (not just _current_path) matters: a
    # previously-failed switch may have left _current_path pointing
    # at a book _book never became, and trusting the path alone
    # would report success while every tool still operates on the
    # old book. A torn state falls through to the full switch,
    # which heals it.
    if (
        previous is not None
        and target == previous
        and _book is not None
        and _book is _book_registry.get(str(target))
    ):
        # Debug-visible, not audited: retries after client timeouts
        # are exactly what incident forensics needs to see, and this
        # branch was invisible during the 2026-07-10 investigation.
        logging.getLogger("gnucash_mcp.debug").info(
            f"switch_book: no-op, already on {target.name}"
        )
        return (
            f"Already on: {target.name}\n"
            f"{_book_orientation(_book)}"
        )

    # Transactional order: everything fallible runs BEFORE the
    # globals move, and they move together. _book_for can raise
    # (constructor resolves the file strictly — transiently missing
    # under cloud sync / external drives); _activate_logging can
    # raise (resolve_mcp_dir refuses unsafe log dirs). A failure at
    # either point must leave the server fully on the previous
    # book — never a half-switched state where reads, writes, and
    # audit logs disagree about which book is current.
    new_book = _book_for(target)

    # The switch is THE event a multi-book audit trail exists to
    # record (bookkeeper finding F1: silent switches turned a
    # five-minute question into an hour of forensics). Departure is
    # written to the PREVIOUS book's trail while logging still
    # points there — after the target proved constructible, so a
    # missing-file failure never forges a departure line — and
    # arrival to the NEW book's trail after activation.
    def _audit_line(text: str) -> None:
        if _logging_audit:
            stamp = datetime.now().astimezone().isoformat()
            stamp = stamp.split("T")[1][:8]
            logging.getLogger("gnucash_mcp.audit").info(
                f"{stamp}  SWITCH BOOK  {text}"
            )
        logging.getLogger("gnucash_mcp.debug").info(
            f"switch_book: {text}"
        )

    if previous is not None:
        _audit_line(f"→ {target.name}")
    try:
        _activate_logging(target)  # logs follow the active book
    except Exception:
        # This fallback is LOAD-BEARING, not defensive: setup_logging
        # clears the audit/debug handlers BEFORE its fallible steps
        # (mkdir, FileHandler open), so a failure there leaves logging
        # disabled entirely — re-asserting the previous book is what
        # keeps its writes audited after a failed switch.
        if previous is not None:
            try:
                _activate_logging(previous)
                # The departure line above is now false — correct
                # the record on the trail that carries it.
                _audit_line(f"FAILED → {target.name} (still on "
                            f"{previous.name})")
            except Exception:
                pass  # original error is the actionable one
        raise
    _audit_line(
        f"← now active (from "
        f"{previous.name if previous else 'startup'})"
    )

    _current_path = target
    _book = new_book
    _server_state["book_path"] = str(target)
    _server_state["current_book"] = target.name

    # Loud context-reset banner: the LLM may be carrying account
    # names, GUIDs, and entity refs from the previous book — all
    # invalid here, and a stale GUID prefix could even mis-resolve to
    # a DIFFERENT entity in this book. Naming the previous book makes
    # the boundary explicit. Snapshot reorients in one line.
    prev_name = previous.name if previous else "the previous book"
    return (
        f"⚠ CONTEXT RESET: All account names, GUIDs, and entity "
        f"references from the previous book ({prev_name}) are now "
        f"invalid. Do not reuse them.\n\n"
        f"Switched to: {target.name}\n"
        f"{_book_orientation(_book)}"
    )


# Initialize logging at module import time
# Use GNUCASH_MCP_DEBUG=1 env var to enable debug logging
# Use GNUCASH_MCP_NOAUDIT=1 env var to disable audit logging
# Logs are stored alongside the book file: {book_path}.mcp/audit/ and {book_path}.mcp/debug/
_debug_mode = os.environ.get("GNUCASH_MCP_DEBUG") == "1"
_audit_mode = os.environ.get("GNUCASH_MCP_NOAUDIT") != "1"
_logging_debug = _debug_mode
_logging_audit = _audit_mode
# Initial logging points at the first valid book. Best-effort at
# import (a bad path is left for main() to fail-fast on); switch_book
# repoints it per active book later.
_book_path = None
_raw_book_path = os.environ.get("GNUCASH_BOOK_PATH")
if _raw_book_path:
    try:
        _book_path = str(_parse_book_paths(_raw_book_path)[0])
    except _BookPathError:
        _book_path = None
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
    """Full chart of accounts from the GnuCash book.

    Resources are whole-snapshot reads, so this unwraps the paginated
    envelope and returns the bare account list (up to the server cap).
    """
    book = get_book()
    envelope = book.list_accounts(compact=False, limit=book.MAX_LIST_LIMIT)
    return _json(envelope["accounts"])


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
    # Multi-book sessions: name the current book and list the rest so
    # the client knows what switch_book can target. Single-book runs
    # keep the bare ``Book:`` line above and add nothing.
    book_paths = _server_state.get("book_paths") or []
    if len(book_paths) > 1:
        lines.append(
            f"Current book: {_server_state.get('current_book', 'unknown')}"
        )
        lines.append(f"Available books: {', '.join(book_paths)}")
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


# Registered inline at import, but OUTSIDE the TOOL_MODULES partition
# (see _INLINE_UNMAPPED_TOOLS). _apply_module_filter adds it to the
# keep set only when 2+ books are configured; single-book sessions
# have nothing to switch to, so the keep pass drops it.
#
# No @audit_log — the switch is session config, not a book write. And
# because logging follows the active book, every subsequent write is
# attributed to the correct book's trail without a marker here.
@mcp.tool()
@safe_tool
def switch_book(
    name: Annotated[
        str,
        Field(
            description="Book to activate, matched as a "
            "case-insensitive prefix of its filename. Must uniquely "
            "identify one configured book (see get_server_config for "
            "the list)."
        ),
    ],
) -> str:
    """Switch the active GnuCash book (multi-book sessions only).

    All subsequent tool calls — and the audit/debug logs — operate on
    the newly-selected book until the next switch. Only present when
    GNUCASH_BOOK_PATH lists 2+ books.
    """
    return _switch_book_impl(name)


# ============== Main ==============


class _CliParseError(ValueError):
    """Unusable command line. The message is the complete user-facing
    text; main() prints it and exits 2."""


def _parse_cli_argv(
    argv: list[str],
) -> tuple[list[str], bool, bool, str | None]:
    """Parse CLI arguments: (book_args, debug, noaudit, modules_value).

    ``--book`` consumes every following token up to the next option
    (the MCPB manifest expands a multi-file picker to ``--book A B``);
    it also repeats, and accepts the ``--book=PATH`` form. Unrecognized
    tokens are fatal: a silently ignored flag means the server runs
    with the wrong tool surface (``--modules all`` once passed
    unnoticed and served core-only while looking configured). Same
    fail-fast principle as the unknown-module-NAME check in
    _apply_module_filter and ``extra="forbid"`` on tool kwargs.
    """
    debug_flag = False
    noaudit_flag = False
    modules_value: str | None = None
    book_args: list[str] = []
    unknown_args: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--debug":
            debug_flag = True
        elif arg == "--noaudit":
            noaudit_flag = True
        elif arg.startswith("--modules="):
            modules_value = arg.split("=", 1)[1]
        elif arg.startswith("--book="):
            book_args.append(arg.split("=", 1)[1])
        elif arg == "--book":
            j = i + 1
            while j < len(argv) and not argv[j].startswith("--"):
                book_args.append(argv[j])
                j += 1
            if j == i + 1:
                raise _CliParseError(
                    "--book requires at least one path, e.g. "
                    "--book /path/to/ledger.gnucash"
                )
            i = j
            continue
        else:
            unknown_args.append(arg)
        i += 1
    if unknown_args:
        lines = [f"Unrecognized argument(s): {' '.join(unknown_args)}"]
        if "--modules" in unknown_args:
            lines.append(
                'The module list must be attached with "=", '
                "e.g. --modules=all or --modules=core,reporting."
            )
        lines.append(
            "Accepted options: --modules=MODULES, --book PATH ..., "
            "--debug, --noaudit, --help"
        )
        raise _CliParseError("\n".join(lines))
    return book_args, debug_flag, noaudit_flag, modules_value


# The MCPB bundle's only module interface: each manifest checkbox
# lands as one of these env vars, rendered "true"/"false" by the
# host app. Values map to module/group names in TOOL_MODULES /
# MODULE_GROUPS ("planning" spans two leaf modules).
_ENV_MODULE_TOGGLES: dict[str, tuple[str, ...]] = {
    "GNUCASH_ENABLE_PLANNING": ("budgets", "scheduling"),
    "GNUCASH_ENABLE_INVESTMENTS": ("investor",),
    "GNUCASH_ENABLE_FREELANCER": ("freelancer",),
    "GNUCASH_ENABLE_BUSINESS": ("business",),
}
_TOGGLE_TRUE = frozenset({"true", "1", "yes", "on"})
_TOGGLE_FALSE = frozenset({"false", "0", "no", "off", ""})


def _modules_from_env_toggles() -> str | None:
    """Compose a --modules value from the GNUCASH_ENABLE_* booleans.

    Returns None when no toggle variable is present at all, so CLI
    and GNUCASH_MCP_MODULES users (and the core-only default) are
    untouched. When any toggle is present, the selection is
    ``reporting`` plus every enabled toggle's modules — core is
    force-added downstream by _apply_module_filter, matching the
    bundle design where core + reporting are always on.

    Raises ValueError on an unparseable value: a typo'd toggle must
    not silently serve the wrong tool surface.
    """
    if not any(v in os.environ for v in _ENV_MODULE_TOGGLES):
        return None
    selected: list[str] = ["reporting"]
    for var, modules in _ENV_MODULE_TOGGLES.items():
        raw = os.environ.get(var)
        if raw is None:
            continue
        norm = raw.strip().lower()
        if norm in _TOGGLE_TRUE:
            selected.extend(modules)
        elif norm not in _TOGGLE_FALSE:
            raise ValueError(
                f"Invalid {var}={raw!r}: expected true/false "
                "(also accepted: 1/0, yes/no, on/off)"
            )
    return ",".join(selected)


def _build_help_text() -> str:
    """Render ``--help`` output.

    Tool counts derive from TOOL_MODULES / MODULE_GROUPS at call
    time so this text cannot drift from the registry (the counts
    were literals once and went stale: "107 tools" shipped while
    the server served 110).
    """
    core = _module_tool_count("core")
    bookkeeper = _module_tool_count("bookkeeper")
    investor = _module_tool_count("investor")
    freelancer = _module_tool_count("freelancer")
    business = _module_tool_count("business")
    total = _module_tool_count("all")
    n_core = len(MODULE_GROUPS["core"])
    n_bookkeeper = len(MODULE_GROUPS["bookkeeper"])
    n_investor = len(MODULE_GROUPS["investor"])
    n_business = len(MODULE_GROUPS["business"])
    return f"""GnuCash MCP Server

Usage: gnucash-mcp [OPTIONS]

Options:
  --book PATH [PATH ...]
                       GnuCash SQLite book(s) to serve. Overrides
                       GNUCASH_BOOK_PATH; repeatable, and multiple
                       paths may follow one flag. Two or more books
                       add the switch_book tool. Filename stems must
                       be unique (switch_book matches by name).
  --modules=MODULES    Tool modules to load (comma-separated).
                       Default: core ({core} tools, always-on). Use "all"
                       for every module ({total} tools; configuring
                       multiple books adds switch_book on top).

                       Role-based selections (group aliases that
                       expand to underlying modules — start here):
                         core         Ledger primitives + reconciliation.
                                      Always on regardless. {core} tools.
                         bookkeeper   Reporting + budgets + scheduling.
                                      {bookkeeper} tools.
                         investor     tax_lots + portfolio (cost basis
                                      + prices). {investor} tools.
                         freelancer   Customer invoicing + sales tax,
                                      plus billterms (payment terms),
                                      jobs (per-project P&L), and
                                      credit notes (customer refunds).
                                      The full solo-consultant
                                      toolkit. {freelancer} tools.
                         business     Full small-business package:
                                      freelancer (invoicing) +
                                      business_complete (vendors,
                                      employees, bills, vouchers,
                                      vendor reports). {business} tools.

                       Leaf modules (pick individually for finer
                       control, or as members of the groups above):

                       Core sub-modules ({n_core}): summary, accounts,
                       transactions, slots, audit, backup,
                       balance_sheet, diagnostic, reconciliation.

                       Bookkeeper members ({n_bookkeeper}): reporting, budgets,
                       scheduling.

                       Investor members ({n_investor}): tax_lots, portfolio.

                       Business members ({n_business}): freelancer,
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
  GNUCASH_BOOK_PATH          Path to GnuCash SQLite book (required). May
                             be an os.pathsep-separated list of books
                             (":" on macOS/Linux, ";" on Windows — same
                             as PATH); the first is current at startup
                             and switch_book changes the active one
                             in-session. Filenames must be unique
                             (switch_book matches by name).
  GNUCASH_MCP_MODULES        Tool modules to load — same values as
                             --modules (e.g. "bookkeeper" or "core,reporting")
  GNUCASH_ENABLE_PLANNING    Boolean module toggles (true/false) — the
  GNUCASH_ENABLE_INVESTMENTS interface the MCPB bundle's checkboxes use.
  GNUCASH_ENABLE_FREELANCER  When any is set, modules = core + reporting
  GNUCASH_ENABLE_BUSINESS    plus each enabled toggle's modules (planning
                             = budgets + scheduling; investments =
                             investor; business supersedes freelancer).
                             --modules / GNUCASH_MCP_MODULES win when set.
  GNUCASH_MCP_DEBUG=1        Enable debug logging
  GNUCASH_MCP_NOAUDIT=1      Disable audit logging
  GNUCASH_LOG_DIR            Relocate .mcp storage (audit, debug, backups).
                             Each book gets its own subdirectory:
                             {{GNUCASH_LOG_DIR}}/{{book}}.mcp
                             Default location: {{book_path}}.mcp
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
default, or per-book under GNUCASH_LOG_DIR if set:
  {{book_path}}.mcp/audit/YYYY-MM-DD.txt
  {{book_path}}.mcp/debug/YYYY-MM-DD.log   (when debug enabled)
  {{book_path}}.mcp/backups/               (auto + manual snapshots)
"""


def main() -> None:
    """Run the MCP server."""
    if "--help" in sys.argv or "-h" in sys.argv:
        print(_build_help_text())
        sys.exit(0)

    global _book_paths, _current_path, _logging_debug, _logging_audit
    global _book_class

    # Parse CLI flags first — --book must win over GNUCASH_BOOK_PATH
    # below. Fail-fast rationale lives on _parse_cli_argv.
    try:
        book_args, debug_flag, noaudit_flag, modules_value = (
            _parse_cli_argv(sys.argv[1:])
        )
    except _CliParseError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    del sys.argv[1:]

    # Resolve the book list: --book args win; otherwise
    # GNUCASH_BOOK_PATH (one path or an os.pathsep-separated list).
    # Fail fast on any invalid/duplicate entry (neither set is
    # tolerated — tools error at call time, matching the prior
    # behavior). ``book_path`` below is the CURRENT book, used for
    # logging / health / display.
    raw_book_path = os.environ.get("GNUCASH_BOOK_PATH")
    if book_args or (raw_book_path and raw_book_path.strip()):
        try:
            if book_args:
                _apply_book_args(book_args)
            else:
                _book_paths = _parse_book_paths(raw_book_path)
                _current_path = _book_paths[0]
        except _BookPathError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from None
        book_path = str(_current_path)

        # Startup format check: an XML-format book (the #1
        # predictable file-picker mistake) fails here with a message
        # a non-developer can act on, instead of a piecash
        # DatabaseError at first tool call.
        format_errors = [
            msg for p in _book_paths
            if (msg := _book_format_error(p)) is not None
        ]
        if format_errors:
            print("\n".join(format_errors), file=sys.stderr)
            raise SystemExit(2)
    else:
        book_path = None

    # Module selection precedence: --modules, then GNUCASH_MCP_MODULES,
    # then the MCPB bundle's GNUCASH_ENABLE_* checkbox toggles.
    if modules_value is None:
        modules_value = os.environ.get("GNUCASH_MCP_MODULES")
    if modules_value is None:
        try:
            modules_value = _modules_from_env_toggles()
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from None

    # Re-init logging if CLI flags override env vars. Update the
    # effective-mode globals so switch_book repoints with the same
    # debug/audit settings.
    if debug_flag or noaudit_flag:
        audit_enabled = not noaudit_flag
        _logging_debug = debug_flag or _debug_mode
        _logging_audit = audit_enabled and _audit_mode
        if book_path and (_logging_audit or _logging_debug):
            setup_logging(
                book_path=book_path,
                debug=_logging_debug,
                audit=_logging_audit,
                get_book=get_book,
            )
            if _logging_debug:
                debug_log(f"Server starting via CLI. Book: {book_path}")
                debug_log(f"Debug logging enabled, audit={'enabled' if _logging_audit else 'disabled'}")

    # Validate and apply module filter (also lazy-loads extracted modules)
    _validate_module_groups()
    _validate_tool_modules()
    loaded_modules = _apply_module_filter(modules_value)

    # Build a GnuCashBook class that includes only the mixins for enabled
    # modules. If get_book() has already been called (tests / module
    # import), leave the existing instance alone; otherwise subsequent
    # get_book() calls will use this class.
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
        "book_paths": [p.name for p in _book_paths],
        "current_book": _current_path.name if _current_path else None,
        "debug": debug_flag,
        "default_currency_ok": currency_ok,
    })

    if debug_flag or _debug_mode:
        debug_log(f"Modules: {modules_display}")
        debug_log(f"Tools loaded: {tool_count}")

    mcp.run()


if __name__ == "__main__":
    main()
