"""Tests for tool module filtering and server configuration."""

import os
import re
from pathlib import Path

import pytest

from gnucash_mcp.book import extracted_modules
from gnucash_mcp.server import (
    MODULE_BACKED_BY,
    MODULE_GROUPS,
    TOOL_MODULES,
    _apply_module_filter,
    _get_server_config_impl,
    _loaded_tool_files,
    _reset_lazy_load_state,
    _server_state,
    _validate_module_groups,
    _validate_tool_modules,
    mcp,
)


def _core_tool_names() -> set[str]:
    """Union of tool names across the eight ``core`` sub-modules.

    Pre-Core-chop, tests asserted against ``TOOL_MODULES["core"]``
    directly. Post-chop, ``core`` is a MODULE_GROUPS alias — there is
    no flat list any more. This helper preserves the assertion
    shape without re-spelling the eight sub-module names at every
    callsite.
    """
    names: set[str] = set()
    for sub in MODULE_GROUPS["core"]:
        names.update(TOOL_MODULES[sub])
    return names


class TestToolModulesMapping:
    """Tests for the TOOL_MODULES constant."""

    def test_registered_tools_are_a_subset_of_mapped(self):
        """Every tool registered at import must appear in TOOL_MODULES.

        Every module is now extracted and lazy-loaded, so no tools are
        registered at import time. The reverse direction (every tool
        in TOOL_MODULES can be loaded) is covered by
        test_all_modules_load via _apply_module_filter("all").
        """
        all_mapped = set()
        for tools in TOOL_MODULES.values():
            all_mapped.update(tools)
        # switch_book is an inline meta-tool deliberately outside the
        # partition (registered at import, kept only in multi-book
        # sessions); exclude it like the server validator does.
        from gnucash_mcp.server import _INLINE_UNMAPPED_TOOLS
        registered = set(mcp._tool_manager._tools.keys())
        registered -= _INLINE_UNMAPPED_TOOLS
        assert registered.issubset(all_mapped)

    def test_every_tool_module_backs_onto_extracted_files(self):
        """Every TOOL_MODULES key must back onto extracted-module
        files. After the restructure the relationship is no longer
        ``TOOL_MODULES.keys() ⊆ extracted_modules()``: ``portfolio``
        and ``tax_lots`` are new public modules that don't have
        their own tool files (they're slices of the legacy
        ``investments`` file, mapped via MODULE_BACKED_BY).
        """
        for mod_name in TOOL_MODULES:
            backing = MODULE_BACKED_BY.get(mod_name, {mod_name})
            assert backing <= extracted_modules(), (
                f"TOOL_MODULES[{mod_name!r}] backs onto "
                f"{sorted(backing - extracted_modules())} which "
                f"aren't extracted modules"
            )

    def test_no_duplicate_tools_across_modules(self):
        """No tool should appear in more than one module."""
        seen = set()
        for tools in TOOL_MODULES.values():
            for tool in tools:
                assert tool not in seen, f"{tool} appears in multiple modules"
                seen.add(tool)

    def test_core_group_resolves_to_32_tools(self):
        """The ``core`` group expands to 32 tools across its nine
        sub-modules (summary 1 + accounts 7 + transactions 11 + slots
        3 + audit 1 + backup 3 + balance_sheet 1 + diagnostic 1 +
        reconciliation 4). Reconciliation joined core in v1.3.1
        per the bookkeeper-driven principle that any configuration
        which handles money must include reconciliation.
        """
        assert len(_core_tool_names()) == 32

    def test_total_tool_count(self):
        """Total tools across all sub-modules should be 110 —
        88 post-module-restructure + 3 voucher tools +
        4 credit-note tools + 5 job CRUD tools +
        1 get_job_report + 5 taxtable CRUD tools added in v1.3."""
        total = sum(len(tools) for tools in TOOL_MODULES.values())
        assert total == 110

    def test_expected_modules_exist(self):
        """All expected leaf-module names should be present.
        ``core``, ``bookkeeper``, and ``investor`` are group
        aliases (in MODULE_GROUPS) — not TOOL_MODULES keys."""
        expected = {
            # Core sub-modules (reconciliation joined core in v1.3.1)
            "summary", "accounts", "transactions", "slots",
            "audit", "backup", "balance_sheet", "diagnostic",
            "reconciliation",
            # Bookkeeper-cluster leaves
            "reporting", "budgets", "scheduling",
            # Investor-cluster leaves
            "portfolio", "tax_lots",
            # Business-cluster leaves (``business`` is now a group
            # alias expanding to these two; the standalone of the
            # same name was the pre-v1.3 design, retired because it
            # left small-business users without invoice tools).
            "freelancer", "business_complete",
        }
        assert set(TOOL_MODULES.keys()) == expected
        # Group aliases — ``core`` always-on plus the three role
        # groups (bookkeeper, investor, business) landing in v1.3.
        # core grew to 9 in v1.3.1 — reconciliation moved here
        # from bookkeeper so it loads in every configuration that
        # handles money (which is all of them).
        assert set(MODULE_GROUPS["core"]) == {
            "summary", "accounts", "transactions", "slots",
            "audit", "backup", "balance_sheet", "diagnostic",
            "reconciliation",
        }
        assert set(MODULE_GROUPS["bookkeeper"]) == {
            "reporting", "budgets", "scheduling",
        }
        assert set(MODULE_GROUPS["investor"]) == {
            "tax_lots", "portfolio",
        }
        assert set(MODULE_GROUPS["business"]) == {
            "freelancer", "business_complete",
        }

    def test_validate_tool_modules_passes(self):
        """Validation should pass with the current mapping."""
        _validate_tool_modules()  # Should not raise


class TestToolFileVsModulesMapping:
    """The union of every ``@mcp.tool()`` decoration across every
    ``tools/<file>.py`` must equal the union of every tool name in
    ``TOOL_MODULES``.

    Bug class this prevents: a tool added with ``@mcp.tool()`` in
    ``tools/<file>.py`` but missing from any ``TOOL_MODULES`` entry
    gets *registered* during lazy-load, then immediately *removed* by
    ``_apply_module_filter``'s "drop anything not in the keep set"
    step. The tool is invisible at runtime even though the decorator
    fired and the function exists. The reverse — a name in
    ``TOOL_MODULES`` that no file actually defines — is just as bad:
    silently kept in the ``keep`` set, never registered, "this tool
    is in the docs but doesn't work."

    Pre-restructure this was a per-module 1:1 check (each
    ``tools/<X>.py`` matched ``TOOL_MODULES[X]`` exactly). The Core
    restructure broke that bijection — Core's 30 tools span
    ``tools/core.py`` + ``tools/reconciliation.py`` +
    ``tools/admin.py`` + ``tools/backup.py``. The contract is now
    bidirectional-totality: every decorated tool maps to some
    TOOL_MODULES entry, every TOOL_MODULES entry maps to some
    decorated tool. Many-to-many.
    """

    @pytest.fixture(autouse=True)
    def save_and_restore_tools(self):
        original = dict(mcp._tool_manager._tools)
        original_loaded = set(_loaded_tool_files)
        yield
        mcp._tool_manager._tools.clear()
        mcp._tool_manager._tools.update(original)
        _reset_lazy_load_state()
        _loaded_tool_files.update(original_loaded)

    def test_every_decorated_tool_is_mapped(self):
        """Load every tools/<file>.py and verify that each registered
        tool name appears in at least one ``TOOL_MODULES`` entry.
        Catches @mcp.tool() decorations the developer forgot to file
        in the public mapping."""
        from gnucash_mcp.server import _lazy_load_tool_module

        # Load every extracted module on a clean slate.
        mcp._tool_manager._tools.clear()
        _reset_lazy_load_state()
        for file_name in sorted(extracted_modules()):
            _lazy_load_tool_module(file_name)
        registered = set(mcp._tool_manager._tools.keys())

        all_mapped: set[str] = set()
        for tools in TOOL_MODULES.values():
            all_mapped.update(tools)

        unmapped = registered - all_mapped
        assert not unmapped, (
            f"Tools decorated in tools/*.py but missing from any "
            f"TOOL_MODULES entry: {sorted(unmapped)}. "
            f"Add them to the appropriate module or "
            f"_apply_module_filter will silently remove them after "
            f"registration."
        )

    # Tools registered inline in server.py at import time (not via
    # the lazy-load path through tools/<file>.py). Currently just the
    # server-diagnostic surface; if more inline tools are added in
    # the future they need to be listed here for the
    # bidirectional-totality check to pass.
    _ALWAYS_REGISTERED_INLINE = {"get_server_config"}

    def test_every_mapped_tool_is_decorated(self):
        """Every name in TOOL_MODULES must be defined as
        ``@mcp.tool()`` somewhere under ``tools/`` OR be in the
        always-registered-inline set (currently just
        ``get_server_config``). Catches the reverse error —
        TOOL_MODULES references a tool name that doesn't exist."""
        from gnucash_mcp.server import _lazy_load_tool_module

        mcp._tool_manager._tools.clear()
        _reset_lazy_load_state()
        for file_name in sorted(extracted_modules()):
            _lazy_load_tool_module(file_name)
        registered = set(mcp._tool_manager._tools.keys())
        # Inline-registered tools are gone from the registry after
        # the clear above. Re-add them logically for the comparison.
        registered |= self._ALWAYS_REGISTERED_INLINE

        all_mapped: set[str] = set()
        for tools in TOOL_MODULES.values():
            all_mapped.update(tools)

        phantom = all_mapped - registered
        assert not phantom, (
            f"TOOL_MODULES lists tools that aren't defined in any "
            f"tools/*.py: {sorted(phantom)}. "
            f"Either add the @mcp.tool() in the appropriate file "
            f"or remove the name from TOOL_MODULES."
        )


class TestApplyModuleFilter:
    """Tests for _apply_module_filter."""

    @pytest.fixture(autouse=True)
    def save_and_restore_tools(self):
        """Save tool state before test, restore after."""
        original = dict(mcp._tool_manager._tools)
        original_loaded = set(_loaded_tool_files)
        yield
        mcp._tool_manager._tools.clear()
        mcp._tool_manager._tools.update(original)
        _reset_lazy_load_state()
        _loaded_tool_files.update(original_loaded)

    def _tool_names(self):
        return set(mcp._tool_manager._tools.keys())

    def test_all_keeps_everything(self):
        """--modules=all should keep all 108 tools (88 + 3 vouchers + 4 credit notes + 5 job CRUD + 1 job report + 5 taxtables + 1 batch prices)."""
        _apply_module_filter("all")
        assert len(self._tool_names()) == 110

    def test_none_defaults_to_core_only(self):
        """No --modules flag defaults to the ``core`` group, which
        expands to all eight ledger sub-modules. Backups, audit log,
        and slot tools all live in their own sub-modules under
        ``core``."""
        _apply_module_filter(None)
        remaining = self._tool_names()
        expected = _core_tool_names()
        assert remaining == expected

    def test_core_plus_reporting(self):
        """--modules=core,reporting loads the eight core sub-modules
        plus reporting."""
        _apply_module_filter("core,reporting")
        remaining = self._tool_names()
        expected = _core_tool_names() | set(TOOL_MODULES["reporting"])
        assert remaining == expected

    def test_core_always_included(self):
        """Even if only 'reporting' is specified, the ``core`` group
        is force-added — all eight ledger sub-modules come along."""
        _apply_module_filter("reporting")
        remaining = self._tool_names()
        assert _core_tool_names().issubset(remaining)
        assert set(TOOL_MODULES["reporting"]).issubset(remaining)

    def test_all_modules_combined(self):
        """Specifying every module individually should equal 'all'."""
        all_names = ",".join(TOOL_MODULES.keys())
        _apply_module_filter(all_names)
        assert len(self._tool_names()) == 110

    def test_all_in_list_keeps_everything(self):
        """'all' mixed with other modules should keep all 108 tools."""
        _apply_module_filter("scheduling,reconciliation,all")
        assert len(self._tool_names()) == 110

    def test_unknown_module_fails_fast(self, capsys):
        """Unknown module names fail-fast at startup with SystemExit.

        Bookkeeper-found on PR #92 review: pre-fix, a typo'd module
        name (e.g. ``--modules=bookeeper`` missing the 'k') printed
        a warning to stderr and then silently partial-loaded. Claude
        Desktop captures MCP server stderr into a log file the user
        never sees, so the warning was effectively invisible — the
        user observed "the tools I expected aren't there" and could
        not tell whether it was a typo or a server bug. v1.3 fails
        fast so configuration errors surface immediately.
        """
        import pytest
        with pytest.raises(SystemExit) as exc_info:
            _apply_module_filter("core,nonexistent")
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "nonexistent" in captured.err
        assert "Unknown module" in captured.err
        assert "Valid names:" in captured.err

    def test_unknown_module_did_you_mean_suggestion(self, capsys):
        """Typos close to a known name should surface a did-you-mean
        hint so the user can self-correct on the next restart without
        scrolling through the full valid-names list.
        """
        import pytest
        with pytest.raises(SystemExit):
            _apply_module_filter("bookeeper")  # missing 'k'
        captured = capsys.readouterr()
        assert "bookeeper" in captured.err
        assert "did you mean 'bookkeeper'" in captured.err

    def test_unknown_module_alongside_valid_still_fails(self, capsys):
        """Mixed valid + invalid input fails the whole startup. No
        partial-load mode where the valid modules quietly load and
        the invalid one is dropped — that was the silent-failure
        regime fail-fast replaces.
        """
        import pytest
        with pytest.raises(SystemExit) as exc_info:
            _apply_module_filter("bookeeper,investor")
        assert exc_info.value.code == 2

    def test_freelancer_carries_jobs_credit_notes_billterms(self):
        """v1.3.1 redistribution: billterms, jobs, and credit notes
        moved from business_complete to freelancer.

        Principle: polymorphic-on-owner_type tools + shared
        infrastructure live in freelancer; vendor-specific surface
        stays in business_complete. A solo freelancer setting
        payment terms on customer invoices, running per-project
        P&L, or issuing customer refunds needs these without
        pulling in vendor management. The polymorphic gate in
        _gate_owner_type still restricts vendor-side use of the
        polymorphic tools (jobs / credit notes) to business mode.
        """
        _apply_module_filter("freelancer")
        remaining = self._tool_names()
        # Billterms (shared infrastructure).
        assert "create_billterm" in remaining
        assert "list_billterms" in remaining
        # Jobs (polymorphic; customer-side usable in freelancer).
        assert "create_job" in remaining
        assert "list_jobs" in remaining
        assert "get_job_report" in remaining
        # Credit notes (polymorphic; customer refunds usable).
        assert "create_credit_note" in remaining
        assert "apply_credit_note" in remaining
        # Vendor-specific surface must remain absent.
        assert "create_vendor" not in remaining
        assert "create_bill" not in remaining
        assert "create_employee" not in remaining
        assert "create_voucher" not in remaining
        assert "vendor_spending_report" not in remaining

    def test_unknown_module_alongside_all_still_fails(self, capsys):
        """``all`` is a loading instruction, not a validation bypass.

        Bookkeeper-found bug post-PR #92 merge: when ``all`` was
        present alongside a typo'd module name (e.g.
        ``--modules=bookkeper,all``), the server happily loaded
        every tool — the ``all`` branch short-circuited past the
        unknown-name check. The typo would only surface later if
        the user removed ``all`` and got a different failure mode.

        Validation must run on every supplied name regardless of
        whether ``all`` is also present. A typo is still a signal
        that something's wrong — maybe the user is testing module
        isolation and accidentally left ``all`` in, or they'll
        later remove ``all`` and be surprised the misspelled one
        silently does nothing.
        """
        import pytest
        # Single typo + all → reject.
        with pytest.raises(SystemExit) as exc_info:
            _apply_module_filter("bookkeper,all")
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "bookkeper" in captured.err
        assert "did you mean 'bookkeeper'" in captured.err

    def test_multiple_typos_plus_all_still_fails(self, capsys):
        """Even with valid modules AND all present, any typo
        rejects."""
        import pytest
        with pytest.raises(SystemExit) as exc_info:
            _apply_module_filter("business,bookkeper,all")
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "bookkeper" in captured.err

    def test_whitespace_in_module_names(self):
        """Whitespace around module names should be stripped."""
        _apply_module_filter("core , reporting")
        remaining = self._tool_names()
        expected = _core_tool_names() | set(TOOL_MODULES["reporting"])
        assert remaining == expected

    def test_individual_core_submodule_selectable(self):
        """A user can pick a single Core sub-module by name. The
        ``core`` group is still force-added, so all eight ledger
        sub-modules end up loaded — but the test confirms the
        sub-module name is a valid input that doesn't trip the
        unknown-module warning."""
        _apply_module_filter("accounts")
        remaining = self._tool_names()
        # accounts is in the group anyway; just verify it loaded.
        assert "list_accounts" in remaining
        assert "create_account" in remaining

    def test_portfolio_and_tax_lots_split(self):
        """``portfolio`` (commodities + prices) and ``tax_lots``
        (cost-basis tracking) are the two leaves of what used to
        be the ``investments`` module — independently selectable
        for a finer cut. The ``investor`` group bundles them; see
        test_investor_group_bundles_both below for that path."""
        # portfolio alone — price tools yes, lot tools no
        _apply_module_filter("portfolio")
        remaining = self._tool_names()
        assert "list_commodities" in remaining
        assert "create_price" in remaining
        assert "create_lot" not in remaining
        assert "calculate_lot_gain" not in remaining
        # Core always loaded
        assert "list_accounts" in remaining
        # Non-selected modules not present
        assert "spending_by_category" not in remaining

        # tax_lots alone — lot tools yes, price tools no
        _reset_lazy_load_state()
        mcp._tool_manager._tools.clear()
        _apply_module_filter("tax_lots")
        remaining = self._tool_names()
        assert "create_lot" in remaining
        assert "calculate_lot_gain" in remaining
        assert "list_commodities" not in remaining
        assert "create_price" not in remaining

    def test_investor_group_bundles_both(self):
        """``--modules=investor`` (the group alias) loads
        tax_lots + portfolio together. Tax-lot accounting needs
        market prices to compute gains, so the bundle is the
        useful unit; the leaf modules exist for fine-grained
        users."""
        _apply_module_filter("investor")
        remaining = self._tool_names()
        # Both halves present.
        assert "create_lot" in remaining
        assert "calculate_lot_gain" in remaining
        assert "list_commodities" in remaining
        assert "create_price" in remaining

    def test_bookkeeper_group_bundles_three_modules(self):
        """``--modules=bookkeeper`` loads reporting + budgets +
        scheduling — the personal-finance management cluster.
        Reconciliation moved to core in v1.3.1 and is now
        always-on regardless of group selection."""
        _apply_module_filter("bookkeeper")
        remaining = self._tool_names()
        # One probe per bookkeeper member module.
        assert "spending_by_category" in remaining    # reporting
        assert "create_budget" in remaining           # budgets
        assert "create_scheduled_transaction" in remaining
        # reconciliation is now always-on via core.
        assert "reconcile_account" in remaining
        # Core always loaded; non-bookkeeper modules absent.
        assert "list_accounts" in remaining
        assert "create_invoice" not in remaining      # freelancer
        assert "create_lot" not in remaining          # tax_lots

    def test_reconciliation_loads_with_core_by_default(self):
        """v1.3.1 invariant: any configuration loads reconciliation.

        The bookkeeper-flagged principle: reconciliation touches
        money and every configuration touches money. Moved from
        the bookkeeper group to core so a freelancer / investor /
        business persona doesn't ship without statement-
        reconciliation tools.
        """
        for modules in ("freelancer", "investor", "business", None):
            _apply_module_filter(modules)
            remaining = self._tool_names()
            assert "reconcile_account" in remaining, (
                f"reconcile_account missing for --modules={modules}"
            )
            assert "set_reconcile_state" in remaining
            assert "get_unreconciled_splits" in remaining

    def test_filter_is_subtractive(self):
        """Filtering should only remove tools, never add non-existent ones."""
        _apply_module_filter("core")
        remaining = self._tool_names()
        registered = set(dict(mcp._tool_manager._tools).keys())
        # All remaining tools should be valid registered tools
        assert remaining == registered

    def test_returns_loaded_modules_sorted(self):
        """Return value is a sorted list of actually loaded
        sub-modules. The ``core`` group is always force-added, so all
        eight ledger sub-modules + the requested ones are present.
        Group names themselves don't appear in the return value —
        callers derive groupings via ``MODULE_GROUPS`` if needed."""
        result = _apply_module_filter("reporting,budgets")
        # Eight Core sub-modules + budgets + reporting = 10 entries.
        expected = sorted(set(MODULE_GROUPS["core"]) | {"budgets", "reporting"})
        assert result == expected

    def test_returns_all_modules_for_all(self):
        """'all' should return all module names sorted."""
        result = _apply_module_filter("all")
        assert result == sorted(TOOL_MODULES.keys())

    def test_returns_core_submodules_for_none(self):
        """None returns the eight ledger sub-modules — the result of
        expanding the always-on ``core`` group."""
        result = _apply_module_filter(None)
        assert result == sorted(MODULE_GROUPS["core"])

    def test_returns_excludes_unknown_modules(self):
        """Unknown module names trigger fail-fast (v1.3 behavior).

        Pre-v1.3 the function returned a sorted list excluding
        unknown names. After the fail-fast change, the function
        raises SystemExit instead — the "excluded" behavior is now
        "rejected at startup" so it can't surface downstream as
        missing tools. Test repurposed to lock the new contract.
        """
        import pytest
        with pytest.raises(SystemExit):
            _apply_module_filter("reporting,nonexistent")


class TestExtractedModuleLazyLoading:
    """Verify that extracted module tools are lazy-loaded on demand.

    Extracted modules (see `extracted_modules()`) are NOT registered
    at server.py import time. Their tools only appear in the FastMCP
    registry after `_apply_module_filter` enables them or
    `_lazy_load_tool_module` is called directly.
    """

    @pytest.fixture(autouse=True)
    def save_and_restore_tools(self):
        original = dict(mcp._tool_manager._tools)
        original_loaded = set(_loaded_tool_files)
        yield
        mcp._tool_manager._tools.clear()
        mcp._tool_manager._tools.update(original)
        _reset_lazy_load_state()
        _loaded_tool_files.update(original_loaded)

    # See TestToolFileVsModulesMapping above. ``get_server_config``
    # is registered inline at server.py import (server diagnostic;
    # always available regardless of module set). All other Core
    # tools are lazy-loaded through tools/<file>.py.
    _ALWAYS_REGISTERED_INLINE = {"get_server_config"}

    def test_extracted_modules_are_not_registered_at_import(self):
        """No tool from any TOOL_MODULES entry — except the
        always-registered-inline set — should be present at import
        time. Defeats lazy loading if any extracted-file tool sneaks
        a registration into module-import side effects."""
        all_tools: set[str] = set()
        for tools in TOOL_MODULES.values():
            all_tools.update(tools)
        for tool_name in sorted(all_tools - self._ALWAYS_REGISTERED_INLINE):
            assert tool_name not in mcp._tool_manager._tools, (
                f"{tool_name} was registered at import time — "
                f"defeats lazy loading."
            )

    def test_enabling_extracted_module_registers_its_tools(self):
        """Enabling 'reporting' via _apply_module_filter registers
        all reporting tools."""
        _reset_lazy_load_state()
        mcp._tool_manager._tools.clear()
        _apply_module_filter("reporting")
        for tool_name in TOOL_MODULES["reporting"]:
            assert tool_name in mcp._tool_manager._tools

    def test_all_loads_every_extracted_module(self):
        """--modules=all lazy-loads all extracted modules. The
        always-registered-inline set is excluded from the comparison
        — those tools were registered at server import, gone after
        the registry clear, and not re-registered by lazy-load."""
        _reset_lazy_load_state()
        mcp._tool_manager._tools.clear()
        _apply_module_filter("all")
        all_expected = set()
        for tools in TOOL_MODULES.values():
            all_expected.update(tools)
        all_expected -= self._ALWAYS_REGISTERED_INLINE
        assert set(mcp._tool_manager._tools.keys()) == all_expected

    def test_lazy_load_is_idempotent(self):
        """Enabling an extracted module twice does not duplicate tools."""
        _reset_lazy_load_state()
        mcp._tool_manager._tools.clear()
        _apply_module_filter("reporting")
        count_after_first = len(mcp._tool_manager._tools)
        _apply_module_filter("reporting")
        assert len(mcp._tool_manager._tools) == count_after_first


class TestGetServerConfig:
    """Tests for get_server_config — promoted from --debug-only to
    an unconditional Core diagnostic tool during the module
    restructure."""

    @pytest.fixture(autouse=True)
    def save_and_restore_state(self):
        """Save server state before test, restore after."""
        original = dict(_server_state)
        yield
        _server_state.clear()
        _server_state.update(original)

    def test_impl_returns_all_fields(self):
        """Output should contain all five diagnostic fields."""
        _server_state.update({
            "modules": "core,reporting",
            "tool_count": 20,
            "book_path": "/tmp/test.gnucash",
            "debug": True,
        })
        output = _get_server_config_impl()
        assert "Modules loaded: core,reporting" in output
        assert "Tools available: 20" in output
        # Book line shows filename only — see _book_display_name
        # for the privacy rationale. Path leak hardened in v1.3.0.
        assert "Book: test.gnucash" in output
        assert "Debug mode: true" in output
        assert "Version:" in output

    def test_impl_defaults_when_state_empty(self):
        """Output should handle missing state gracefully."""
        _server_state.clear()
        output = _get_server_config_impl()
        assert "Modules loaded: unknown" in output
        assert "Tools available: unknown" in output
        assert "Book: not set" in output
        assert "Debug mode: false" in output

    def test_impl_does_not_leak_directory_path(self):
        """The book directory must not appear in the response.

        Privacy hardening shipped in v1.3.0: routine LLM-visible
        responses used to include the full absolute path to the
        GnuCash book, leaking username and home directory layout
        into every transcript and screenshot. The filename alone is
        sufficient for the LLM to confirm which book is loaded.
        """
        _server_state.update({
            "modules": "core",
            "tool_count": 10,
            "book_path": "/Users/alice/Finances/personal-2026.gnucash",
            "debug": False,
        })
        output = _get_server_config_impl()
        # Username and directory must NOT appear.
        assert "/Users/" not in output, (
            f"directory path leaked into output:\n{output}"
        )
        assert "alice" not in output
        assert "Finances" not in output
        # But the filename SHOULD appear so the caller can verify
        # which book is loaded.
        assert "personal-2026.gnucash" in output

    def test_impl_output_is_plain_text(self):
        """Output should be plain text, not JSON."""
        _server_state.update({
            "modules": "all",
            "tool_count": 53,
            "book_path": "/tmp/test.gnucash",
            "debug": True,
        })
        output = _get_server_config_impl()
        assert not output.startswith("{")
        lines = output.strip().split("\n")
        assert len(lines) == 5

    def test_registered_in_diagnostic_unconditionally(self):
        """get_server_config lives in the ``diagnostic`` sub-module
        (always loaded via the ``core`` group alias) and is registered
        at server import time, regardless of --debug. Always available
        as a diagnostic surface."""
        assert "get_server_config" in TOOL_MODULES["diagnostic"]
        assert "get_server_config" in mcp._tool_manager._tools


class TestOwnerTypeGating:
    """The Freelancer module hosts the shared-lifecycle invoice
    tools (post/unpost/pay_invoice, list/get_invoice,
    get_outstanding_invoices). Those tools dispatch on owner_type to
    handle both customer invoices AND vendor bills — but the Business
    module owns vendor management. Runtime gating in
    ``_gate_owner_type`` enforces the split: a Freelancer-only user
    can't reach vendor bills through the shared tools.
    """

    @pytest.fixture(autouse=True)
    def save_and_restore_modules(self):
        from gnucash_mcp.server import _LOADED_MODULES
        original = set(_LOADED_MODULES)
        yield
        _LOADED_MODULES.clear()
        _LOADED_MODULES.update(original)

    def test_business_loaded_passes_through(self):
        """With business_complete enabled (whether explicitly or via
        the ``business`` group alias), _gate_owner_type returns its
        input unchanged — both halves of the polymorphic dispatch work.
        """
        from gnucash_mcp.server import _LOADED_MODULES
        from gnucash_mcp.tools._helpers import _gate_owner_type

        _LOADED_MODULES.clear()
        _LOADED_MODULES.update({
            "core", "freelancer", "business_complete", "business",
        })
        assert _gate_owner_type("customer") == "customer"
        assert _gate_owner_type("vendor") == "vendor"
        assert _gate_owner_type(None) is None

    def test_business_absent_coerces_to_customer(self):
        """Without business, omitted or 'customer' owner_type is
        coerced to 'customer' explicitly so the book-layer lookup
        filters out vendor bills."""
        from gnucash_mcp.server import _LOADED_MODULES
        from gnucash_mcp.tools._helpers import _gate_owner_type

        _LOADED_MODULES.clear()
        _LOADED_MODULES.update({"core", "freelancer"})
        assert _gate_owner_type(None) == "customer"
        assert _gate_owner_type("customer") == "customer"

    def test_business_absent_rejects_explicit_vendor(self):
        """Without business, an explicit owner_type='vendor' raises
        a clear error rather than silently coercing or returning
        not-found from the book layer."""
        import pytest
        from gnucash_mcp.server import _LOADED_MODULES
        from gnucash_mcp.tools._helpers import _gate_owner_type

        _LOADED_MODULES.clear()
        _LOADED_MODULES.update({"core", "freelancer"})
        with pytest.raises(ValueError, match="requires the business module"):
            _gate_owner_type("vendor")

    def test_business_absent_rejects_explicit_employee(self):
        """Symmetric with the vendor rejection — employee gating
        was added with vouchers (v1.3) and needs the same
        guardrail. Without business, owner_type='employee' raises
        with the Business-module-required message. (Copilot PR
        #86 review found this coverage gap.)"""
        import pytest
        from gnucash_mcp.server import _LOADED_MODULES
        from gnucash_mcp.tools._helpers import _gate_owner_type

        _LOADED_MODULES.clear()
        _LOADED_MODULES.update({"core", "freelancer"})
        with pytest.raises(ValueError, match="requires the business module"):
            _gate_owner_type("employee")

    def test_business_absent_rejects_typo(self):
        """Without business, a typo like 'venddor' must fail fast
        — pre-fix the gate silently coerced unknown strings to
        'customer', masking the typo behind a confusing "invoice
        not found" downstream error. The gate now rejects anything
        outside {None, 'customer'} (or the two business-gated
        names which raise their own messages). (Copilot PR #86
        review.)"""
        import pytest
        from gnucash_mcp.server import _LOADED_MODULES
        from gnucash_mcp.tools._helpers import _gate_owner_type

        _LOADED_MODULES.clear()
        _LOADED_MODULES.update({"core", "freelancer"})
        with pytest.raises(ValueError, match="Invalid owner_type"):
            _gate_owner_type("venddor")


class TestJsonDumpsForbiddenInTools:
    """L-4: ``tools/*.py`` must serialize via the project's
    ``_json()`` helper, never ``json.dumps``.

    Why: ``_json`` enforces compact separators, strips noise
    (None / empty-string fields), and disables ASCII escaping —
    the wire format the bookkeeper review loop locked into
    place during PR #92. Bare ``json.dumps`` would re-introduce
    indented output (40-60% bloat), reintroduce noise fields,
    or escape non-ASCII commodity names (CNY / Russian / etc.)
    into ``\\uXXXX`` sequences.

    Allowed exceptions:
      - The ``_json`` definition itself in ``_helpers.py``.
      - Comment text that mentions ``json.dumps`` for
        explanatory purposes.
    """

    _CALL_RE = re.compile(r"\bjson\.dumps\s*\(")

    def test_no_bare_json_dumps_calls_in_tools_files(self):
        tools_dir = (
            Path(__file__).parent.parent
            / "src" / "gnucash_mcp" / "tools"
        )
        offending: list[str] = []
        for py in sorted(tools_dir.glob("*.py")):
            for i, raw in enumerate(py.read_text().splitlines(), 1):
                line = raw.split("#", 1)[0]  # drop comments
                if py.name == "_helpers.py" and "return json.dumps(" in line:
                    # The _json() implementation is the canonical
                    # call site; everything else in tools/* must
                    # go through it.
                    continue
                if self._CALL_RE.search(line):
                    offending.append(f"{py.name}:{i}: {raw.strip()}")
        assert not offending, (
            "tools/*.py must use _json(); found bare json.dumps():\n  "
            + "\n  ".join(offending)
        )


class TestModuleGroupsValidation:
    """MP-10: every member of MODULE_GROUPS must be a key in
    TOOL_MODULES. A typo (``"reconcilation"``) would otherwise
    produce a silently-empty expansion at runtime — the user
    types ``--modules=core`` and the misspelled member just
    doesn't load.
    """

    def test_current_mapping_passes(self):
        # Should not raise.
        _validate_module_groups()

    def test_typo_in_member_raises(self):
        bogus_groups = {"core": ["accounts", "reconcilation"]}
        from gnucash_mcp import server as srv

        original = srv.MODULE_GROUPS
        srv.MODULE_GROUPS = bogus_groups
        try:
            with pytest.raises(
                RuntimeError,
                match="MODULE_GROUPS references unknown module",
            ):
                _validate_module_groups()
        finally:
            srv.MODULE_GROUPS = original


# ── Multi-book accounting ──────────────────────────────────────────


def _make_min_book(path: Path) -> Path:
    """Create a minimal valid USD book with one funded account."""
    import piecash
    from datetime import date
    from decimal import Decimal

    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    usd = book.default_currency
    root = book.root_account
    assets = piecash.Account(
        name="Assets", type="ASSET", parent=root, commodity=usd,
        placeholder=True,
    )
    checking = piecash.Account(
        name="Checking", type="BANK", parent=assets, commodity=usd,
    )
    equity = piecash.Account(
        name="Equity", type="EQUITY", parent=root, commodity=usd,
        placeholder=True,
    )
    opening = piecash.Account(
        name="Opening Balance", type="EQUITY", parent=equity,
        commodity=usd,
    )
    book.save()
    book.session.add(piecash.Transaction(
        currency=usd, description="Opening Balance",
        post_date=date(2026, 1, 1),
        splits=[
            piecash.Split(account=checking, value=Decimal("1000")),
            piecash.Split(account=opening, value=Decimal("-1000")),
        ],
    ))
    book.save()
    book.close()
    return path


class TestMultiBook:
    """Comma-separated GNUCASH_BOOK_PATH, the switch_book tool, and the
    current-book surfacing in get_server_config / get_book_summary."""

    @pytest.fixture
    def two_books(self, tmp_path: Path):
        """Two distinct-named books: alex.gnucash and beast-man.gnucash."""
        alex = _make_min_book(tmp_path / "alex.gnucash")
        beast = _make_min_book(tmp_path / "beast-man.gnucash")
        return alex, beast

    @pytest.fixture(autouse=True)
    def restore_state(self):
        """Snapshot and restore every global the multi-book paths touch."""
        import gnucash_mcp.server as srv
        import gnucash_mcp.logging_config as logcfg

        saved = {
            "_book": srv._book,
            "_book_paths": list(srv._book_paths),
            "_current_path": srv._current_path,
            "_book_registry": dict(srv._book_registry),
            "_logging_debug": srv._logging_debug,
            "_logging_audit": srv._logging_audit,
        }
        server_state_copy = dict(_server_state)
        tools_copy = dict(mcp._tool_manager._tools)
        loaded_copy = set(_loaded_tool_files)
        log_saved = (
            logcfg._book_path_str, logcfg._log_dir, logcfg._get_book_func,
        )
        yield
        srv._book = saved["_book"]
        srv._book_paths = saved["_book_paths"]
        srv._current_path = saved["_current_path"]
        srv._book_registry = saved["_book_registry"]
        srv._logging_debug = saved["_logging_debug"]
        srv._logging_audit = saved["_logging_audit"]
        _server_state.clear()
        _server_state.update(server_state_copy)
        mcp._tool_manager._tools.clear()
        mcp._tool_manager._tools.update(tools_copy)
        _reset_lazy_load_state()
        _loaded_tool_files.update(loaded_copy)
        logcfg._book_path_str, logcfg._log_dir, logcfg._get_book_func = log_saved

    # ── _parse_book_paths ──────────────────────────────────────────

    def test_parse_single_path(self, two_books):
        from gnucash_mcp.server import _parse_book_paths
        alex, _ = two_books
        assert _parse_book_paths(str(alex)) == [alex.resolve()]

    def test_parse_multi_path_preserves_order(self, two_books):
        from gnucash_mcp.server import _parse_book_paths
        alex, beast = two_books
        result = _parse_book_paths(f"{alex}{os.pathsep}{beast}")
        assert result == [alex.resolve(), beast.resolve()]

    def test_parse_strips_whitespace_and_empties(self, two_books):
        from gnucash_mcp.server import _parse_book_paths
        alex, beast = two_books
        sep = os.pathsep
        result = _parse_book_paths(f" {alex} {sep} {sep} {beast} ")
        assert result == [alex.resolve(), beast.resolve()]

    def test_parse_allows_comma_in_filename(self, tmp_path):
        """Regression guard: a single book whose name contains a comma
        (the rejected separator) must parse as ONE path."""
        from gnucash_mcp.server import _parse_book_paths
        name = "financials, invoicing, and metrics.gnucash"
        book = _make_min_book(tmp_path / name)
        assert _parse_book_paths(str(book)) == [book.resolve()]

    def test_parse_missing_path_fails_fast(self, two_books, tmp_path):
        from gnucash_mcp.server import _parse_book_paths, _BookPathError
        alex, _ = two_books
        missing = tmp_path / "nope.gnucash"
        with pytest.raises(_BookPathError, match="not found"):
            _parse_book_paths(f"{alex}{os.pathsep}{missing}")

    def test_book_path_error_is_file_not_found(self):
        """Missing-book errors must subclass FileNotFoundError so the
        runtime error_type stays ``file_not_found``."""
        from gnucash_mcp.server import _BookPathError
        assert issubclass(_BookPathError, FileNotFoundError)

    def test_parse_duplicate_basename_fails_fast(self, tmp_path):
        from gnucash_mcp.server import _parse_book_paths, _BookPathError
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        b1 = _make_min_book(d1 / "dup.gnucash")
        b2 = _make_min_book(d2 / "dup.gnucash")
        with pytest.raises(_BookPathError, match="duplicate book filename"):
            _parse_book_paths(f"{b1}{os.pathsep}{b2}")

    def test_parse_unset_raises_valueerror(self):
        from gnucash_mcp.server import _parse_book_paths
        with pytest.raises(ValueError, match="GNUCASH_BOOK_PATH"):
            _parse_book_paths(None)
        with pytest.raises(ValueError, match="GNUCASH_BOOK_PATH"):
            _parse_book_paths("   ")

    def test_parse_separator_only_fails_fast(self):
        """A separator-only value (":") is an invalid VALUE, not an
        unset variable — it must raise _BookPathError so main()'s
        fail-fast catches it instead of an uncaught traceback."""
        from gnucash_mcp.server import _parse_book_paths, _BookPathError
        with pytest.raises(_BookPathError, match="only separators"):
            _parse_book_paths(os.pathsep)
        with pytest.raises(_BookPathError, match="only separators"):
            _parse_book_paths(f" {os.pathsep} {os.pathsep} ")

    def test_parse_duplicate_basename_case_insensitive(self, tmp_path):
        """switch_book matches names case-insensitively, so uniqueness
        must be checked the same way — otherwise Ledger.gnucash +
        ledger.gnucash validate but every prefix is ambiguous and the
        second book is permanently unswitchable."""
        from gnucash_mcp.server import _parse_book_paths, _BookPathError
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        b1 = _make_min_book(d1 / "Ledger.gnucash")
        b2 = _make_min_book(d2 / "ledger.gnucash")
        with pytest.raises(_BookPathError, match="duplicate book filename"):
            _parse_book_paths(f"{b1}{os.pathsep}{b2}")

    def test_parse_duplicate_stem_across_extensions_fails_fast(
        self, tmp_path,
    ):
        """Backup state files, retention scoping, and backup filenames
        are all keyed by the filename STEM — ledger.gnucash +
        ledger.xac would share .state-ledger.json under a shared
        GNUCASH_LOG_DIR and prune each other's snapshots. Uniqueness
        must therefore be enforced on the stem, not the full name."""
        from gnucash_mcp.server import _parse_book_paths, _BookPathError
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        b1 = _make_min_book(d1 / "ledger.gnucash")
        b2 = _make_min_book(d2 / "ledger.xac")
        with pytest.raises(_BookPathError, match="duplicate book filename"):
            _parse_book_paths(f"{b1}{os.pathsep}{b2}")

    # ── switch_book visibility ─────────────────────────────────────

    def test_switch_book_present_with_two_books(self, two_books):
        import gnucash_mcp.server as srv
        alex, beast = two_books
        srv._book_paths = [alex.resolve(), beast.resolve()]
        _apply_module_filter("all")
        assert "switch_book" in mcp._tool_manager._tools

    def test_switch_book_absent_with_one_book(self, two_books):
        import gnucash_mcp.server as srv
        alex, _ = two_books
        srv._book_paths = [alex.resolve()]
        _apply_module_filter("all")
        assert "switch_book" not in mcp._tool_manager._tools

    def test_switch_book_absent_when_unconfigured(self):
        import gnucash_mcp.server as srv
        srv._book_paths = []
        _apply_module_filter("all")
        assert "switch_book" not in mcp._tool_manager._tools

    def test_tool_count_single_book(self, two_books):
        import gnucash_mcp.server as srv
        alex, _ = two_books
        srv._book_paths = [alex.resolve()]
        _apply_module_filter("all")
        assert len(mcp._tool_manager._tools) == 110

    def test_tool_count_multi_book(self, two_books):
        """The lone runtime delta from single-book: switch_book (109).
        Each filter call relies on switch_book being registered at
        import; the production flow filters once at startup."""
        import gnucash_mcp.server as srv
        alex, beast = two_books
        srv._book_paths = [alex.resolve(), beast.resolve()]
        _apply_module_filter("all")
        assert len(mcp._tool_manager._tools) == 111

    # ── switch_book matching ───────────────────────────────────────

    def _select(self, srv, two_books):
        alex, beast = two_books
        srv._book = None
        srv._current_path = None
        srv._book_registry = {}
        srv._book_paths = [alex.resolve(), beast.resolve()]
        srv._current_path = alex.resolve()
        srv._book = srv._book_for(alex.resolve())

    def test_switch_by_unique_prefix(self, two_books):
        import gnucash_mcp.server as srv
        self._select(srv, two_books)
        msg = srv._switch_book_impl("beast")
        assert "Switched to: beast-man.gnucash" in msg
        assert srv._current_path.name == "beast-man.gnucash"
        assert srv.get_book().book_path.name == "beast-man.gnucash"

    def test_switch_is_case_insensitive(self, two_books):
        import gnucash_mcp.server as srv
        self._select(srv, two_books)
        assert "beast-man.gnucash" in srv._switch_book_impl("BEAST")

    def test_switch_emits_context_reset_banner(self, two_books):
        """The response must loudly invalidate the previous book's refs
        (naming it) and reorient with a one-line snapshot — the
        bookkeeper-flagged guard against silent cross-book collisions."""
        import gnucash_mcp.server as srv
        self._select(srv, two_books)  # current = alex
        msg = srv._switch_book_impl("beast")
        assert "CONTEXT RESET" in msg
        assert "alex.gnucash" in msg          # names the PREVIOUS book
        assert "invalid" in msg.lower()
        assert "Switched to: beast-man.gnucash" in msg
        assert "base currency" in msg          # orientation snapshot

    def test_switch_noop_skips_reset_banner(self, two_books):
        """Switching to the already-current book is a no-op — it must
        NOT claim a context reset."""
        import gnucash_mcp.server as srv
        self._select(srv, two_books)  # current = alex
        msg = srv._switch_book_impl("alex")
        assert msg.startswith("Already on: alex.gnucash")
        assert "CONTEXT RESET" not in msg

    def test_failed_switch_leaves_state_consistent_and_heals(
        self, two_books, tmp_path,
    ):
        """A switch that fails mid-flight must leave the server fully
        on the previous book, and a retry after the cause clears must
        perform a REAL switch — not a no-op that trusts the torn
        ``_current_path`` and reports "Already on" while every tool
        still operates on the old book (silent wrong-book writes)."""
        import gnucash_mcp.server as srv
        alex, beast = two_books
        self._select(srv, two_books)  # current = alex

        # Transient failure: the book file vanishes (cloud-sync
        # placeholder / unmounted drive) between validation and switch.
        beast_bytes = beast.read_bytes()
        beast.unlink()
        with pytest.raises(FileNotFoundError):
            srv._switch_book_impl("beast")

        # Fully on the previous book — both globals agree.
        assert srv._current_path == alex.resolve()
        assert srv.get_book().book_path.name == "alex.gnucash"

        # Cause clears; retry must really switch (banner, not noop).
        beast.write_bytes(beast_bytes)
        msg = srv._switch_book_impl("beast")
        assert "Switched to: beast-man.gnucash" in msg
        assert srv.get_book().book_path.name == "beast-man.gnucash"

    def test_switch_activation_failure_rolls_back(
        self, two_books, monkeypatch,
    ):
        """If logging activation fails AFTER the target book is
        buildable, the switch must not half-happen: reads/writes and
        the reported outcome must agree that the server is still on
        the previous book."""
        import gnucash_mcp.server as srv
        self._select(srv, two_books)  # current = alex

        real_activate = srv._activate_logging

        def boom(path):
            if path.name == "beast-man.gnucash":
                raise ValueError("unsafe log dir")
            return real_activate(path)

        monkeypatch.setattr(srv, "_activate_logging", boom)
        with pytest.raises(ValueError, match="unsafe log dir"):
            srv._switch_book_impl("beast")
        assert srv._current_path == two_books[0].resolve()
        assert srv.get_book().book_path.name == "alex.gnucash"

    def test_orientation_reports_currency_and_count(self, two_books):
        import gnucash_mcp.server as srv
        alex, _ = two_books
        snap = srv._book_orientation(srv._book_for(alex.resolve()))
        # _make_min_book seeds one transaction in a USD book.
        assert "1 transaction" in snap
        assert "USD base currency" in snap

    def test_switch_no_match_lists_available(self, two_books):
        import gnucash_mcp.server as srv
        self._select(srv, two_books)
        with pytest.raises(ValueError, match="No book matches"):
            srv._switch_book_impl("zzz")

    def test_switch_ambiguous_prefix_raises(self, tmp_path):
        import gnucash_mcp.server as srv
        a = _make_min_book(tmp_path / "report-q1.gnucash")
        b = _make_min_book(tmp_path / "report-q2.gnucash")
        srv._book = None
        srv._book_registry = {}
        srv._book_paths = [a.resolve(), b.resolve()]
        srv._current_path = a.resolve()
        srv._book = srv._book_for(a.resolve())
        with pytest.raises(ValueError, match="ambiguous"):
            srv._switch_book_impl("report")

    def test_switch_empty_name_raises(self, two_books):
        import gnucash_mcp.server as srv
        self._select(srv, two_books)
        with pytest.raises(ValueError, match="requires a book name"):
            srv._switch_book_impl("   ")

    def test_all_tools_are_sync(self):
        """Every registered tool must be a plain sync function. The
        multi-book design keeps ``_book`` an unlocked global; that is
        safe only while the MCP SDK runs sync tools inline on the
        event loop, making each tool call atomic with respect to
        switch_book. An async tool would break that atomicity —
        see the comment on ``_book`` in server.py."""
        import inspect
        _apply_module_filter("all")
        async_tools = [
            name for name, tool in mcp._tool_manager._tools.items()
            if inspect.iscoroutinefunction(tool.fn)
        ]
        assert async_tools == [], (
            f"Async tools break the unlocked-_book atomicity "
            f"assumption: {async_tools}"
        )

    # ── get_book cold-start selection ──────────────────────────────

    def test_get_book_selects_first_as_current(self, two_books, monkeypatch):
        import gnucash_mcp.server as srv
        alex, beast = two_books
        monkeypatch.setenv("GNUCASH_BOOK_PATH", f"{alex}{os.pathsep}{beast}")
        srv._book = None
        srv._current_path = None
        srv._book_registry = {}
        srv._book_paths = []
        book = srv.get_book()
        assert book.book_path.name == "alex.gnucash"
        assert srv._current_path.name == "alex.gnucash"
        assert srv.multi_book_active() is True

    # ── get_server_config ──────────────────────────────────────────

    def test_server_config_lists_books_when_multi(self):
        _server_state.clear()
        _server_state.update({
            "modules": "core", "tool_count": 108,
            "book_path": "/secret/dir/alex.gnucash",
            "book_paths": ["alex.gnucash", "beast-man.gnucash"],
            "current_book": "alex.gnucash",
            "debug": False,
        })
        out = _get_server_config_impl()
        assert "Current book: alex.gnucash" in out
        assert "Available books: alex.gnucash, beast-man.gnucash" in out
        assert "/secret/dir" not in out  # no directory leakage

    def test_server_config_no_book_list_when_single(self):
        _server_state.clear()
        _server_state.update({
            "modules": "core", "tool_count": 107,
            "book_path": "/dir/alex.gnucash",
            "book_paths": ["alex.gnucash"],
            "current_book": "alex.gnucash",
            "debug": False,
        })
        out = _get_server_config_impl()
        assert "Available books:" not in out
        assert "Current book:" not in out
        assert "Book: alex.gnucash" in out

    # ── logging follows the active book ────────────────────────────

    def test_logging_follows_switch(self, two_books, monkeypatch):
        import gnucash_mcp.server as srv
        import gnucash_mcp.logging_config as logcfg
        alex, beast = two_books
        monkeypatch.setenv("GNUCASH_BOOK_PATH", f"{alex}{os.pathsep}{beast}")
        srv._book = None
        srv._current_path = None
        srv._book_registry = {}
        srv._book_paths = []
        srv._logging_audit = True
        srv._logging_debug = False
        srv.get_book()  # cold-start: current = alex
        srv._switch_book_impl("beast")
        assert logcfg._book_path_str.endswith("beast-man.gnucash")

    def test_switch_is_audited_in_both_books(
        self, two_books, monkeypatch,
    ):
        """Bookkeeper finding F1: the switch is THE event a
        multi-book audit trail exists to record. Departure must land
        in the previous book's audit file, arrival in the new
        book's — a silent switch turns audit forensics into
        guesswork."""
        import gnucash_mcp.server as srv
        alex, beast = two_books
        monkeypatch.delenv("GNUCASH_LOG_DIR", raising=False)
        monkeypatch.setenv(
            "GNUCASH_BOOK_PATH", f"{alex}{os.pathsep}{beast}",
        )
        srv._book = None
        srv._current_path = None
        srv._book_registry = {}
        srv._book_paths = []
        srv._logging_audit = True
        srv._logging_debug = False
        srv.get_book()
        srv._activate_logging(alex.resolve())  # audit trail live on alex
        srv._switch_book_impl("beast")

        alex_audit = next(
            (alex.parent / f"{alex.name}.mcp" / "audit").glob("*.txt")
        ).read_text()
        beast_audit = next(
            (beast.parent / f"{beast.name}.mcp" / "audit").glob("*.txt")
        ).read_text()
        assert "SWITCH BOOK  → beast-man.gnucash" in alex_audit
        assert "SWITCH BOOK  ← now active (from alex.gnucash)" \
            in beast_audit
        # And no arrival line polluting the departed book's trail.
        assert "now active" not in alex_audit

    # ── get_book_summary current-book marker ───────────────────────

    def test_summary_marks_current_book_when_multi(self, two_books):
        import gnucash_mcp.server as srv
        alex, beast = two_books
        srv._book = None
        srv._current_path = None
        srv._book_registry = {}
        srv._book_paths = [alex.resolve(), beast.resolve()]
        srv._current_path = alex.resolve()
        srv._book = srv._book_for(alex.resolve())
        _apply_module_filter("all")
        summary = mcp._tool_manager._tools["get_book_summary"].fn()
        first = summary.split("\n", 1)[0]
        assert first.startswith("Current book: alex.gnucash")
        assert "2 books available" in first

    def test_summary_unmarked_when_single(self, two_books):
        import gnucash_mcp.server as srv
        alex, _ = two_books
        srv._book = None
        srv._current_path = None
        srv._book_registry = {}
        srv._book_paths = [alex.resolve()]
        srv._current_path = alex.resolve()
        srv._book = srv._book_for(alex.resolve())
        _apply_module_filter("all")
        summary = mcp._tool_manager._tools["get_book_summary"].fn()
        assert summary.split("\n", 1)[0].startswith("Book: alex.gnucash")


class TestToolAnnotations:
    """Every registered tool carries derived MCP ToolAnnotations.

    The behavior hints are derived from each tool's @audit_log
    declaration at the _apply_module_filter chokepoint — this class
    is the loud gate for a tool that reaches the registry without a
    derivable classification (which would ship annotations=None and
    push the full behavioral burden back onto its description).
    """

    @pytest.fixture(autouse=True)
    def save_and_restore_tools(self):
        original = dict(mcp._tool_manager._tools)
        original_loaded = set(_loaded_tool_files)
        yield
        mcp._tool_manager._tools.clear()
        mcp._tool_manager._tools.update(original)
        _reset_lazy_load_state()
        _loaded_tool_files.update(original_loaded)

    def test_every_tool_annotated_closed_world(self):
        _apply_module_filter("all")
        missing = [
            n for n, t in mcp._tool_manager._tools.items()
            if t.annotations is None
        ]
        assert missing == [], f"tools without annotations: {missing}"
        open_world = [
            n for n, t in mcp._tool_manager._tools.items()
            if t.annotations.openWorldHint is not False
        ]
        # Local book, no network: openWorldHint must be False on all.
        assert open_world == []

    def test_read_only_hint_agrees_with_audit_classification(self):
        _apply_module_filter("all")
        for name, tool in mcp._tool_manager._tools.items():
            meta = getattr(tool.fn, "__audit_meta__", None)
            if meta is None:
                continue  # inline tools, covered by the table test
            expected = meta["classification"] == "read"
            assert tool.annotations.readOnlyHint is expected, (
                f"{name}: readOnlyHint disagrees with "
                f"audit classification {meta['classification']!r}"
            )

    def test_every_write_verb_has_explicit_hints(self):
        """A new operation verb must get a _WRITE_VERB_HINTS row —
        otherwise it silently ships the safest-default hints."""
        from gnucash_mcp.server import _WRITE_VERB_HINTS
        _apply_module_filter("all")
        verbs = {
            meta["operation"]
            for tool in mcp._tool_manager._tools.values()
            if (meta := getattr(tool.fn, "__audit_meta__", None))
            and meta["classification"] == "write"
        }
        unmapped = verbs - set(_WRITE_VERB_HINTS)
        assert unmapped == set(), (
            f"write verbs without explicit hints: {sorted(unmapped)}"
        )

    def test_inline_tools_have_table_entries(self):
        """get_server_config and switch_book register without
        @audit_log; both must be in _INLINE_TOOL_ANNOTATIONS
        (switch_book is filtered out in single-book runs, so the
        registry sweep above never checks it)."""
        from gnucash_mcp.server import (
            _INLINE_TOOL_ANNOTATIONS,
            _INLINE_UNMAPPED_TOOLS,
            _derive_tool_annotations,
        )
        assert "get_server_config" in _INLINE_TOOL_ANNOTATIONS
        assert _INLINE_UNMAPPED_TOOLS <= set(_INLINE_TOOL_ANNOTATIONS)
        ann = _derive_tool_annotations("switch_book", None)
        assert ann is not None and ann.readOnlyHint is False

    def test_hint_spot_checks(self):
        _apply_module_filter("all")
        tools = mcp._tool_manager._tools
        a = tools["delete_budget"].annotations
        assert (a.readOnlyHint, a.destructiveHint, a.idempotentHint) == (
            False, True, True,
        )
        a = tools["create_transaction"].annotations
        assert (a.readOnlyHint, a.destructiveHint, a.idempotentHint) == (
            False, False, False,
        )
        a = tools["list_accounts"].annotations
        assert a.readOnlyHint is True


class TestVerboseDocstringConvention:
    """Every ``verbose:`` Args entry across tools/*.py uses the one
    canonical sentence (default named first, compact framed as
    token-efficient, JSON gated to machine-readable needs).

    Bug class: verbose-first phrasings like "If true, return full
    JSON details" read as an upgrade — a cross-model test showed a
    foreign LLM flipping verbose=true on every call because "full"
    implies the default is lossy. The convention is prose, so the
    lock is a source grep: a new tool's verbose doc either matches
    the canonical opening or this fails.
    """

    CANONICAL = (
        "verbose: If false (default), compact text output — optimized"
    )

    def test_every_verbose_arg_entry_is_canonical(self):
        import glob, os
        tools_dir = os.path.join(
            os.path.dirname(__file__), "..", "src", "gnucash_mcp", "tools",
        )
        offenders = []
        for path in sorted(glob.glob(os.path.join(tools_dir, "*.py"))):
            for n, line in enumerate(open(path), 1):
                stripped = line.strip()
                if stripped.startswith("verbose: ") and \
                        not stripped.startswith("verbose: bool"):
                    if not stripped.startswith(self.CANONICAL):
                        offenders.append(
                            f"{os.path.basename(path)}:{n}: {stripped[:60]}"
                        )
        assert offenders == [], (
            "non-canonical verbose docstring entries (see class "
            f"docstring for the required sentence): {offenders}"
        )
class TestCliArgStrictness:
    """main() must fail fast on unrecognized argv tokens.

    A silently ignored flag means the server runs with the wrong
    tool surface — ``--modules all`` (space-separated) once passed
    unnoticed and served core-only while looking fully configured.
    Same principle as the unknown-module-NAME check and
    ``extra="forbid"`` on tool kwargs.
    """

    def _run_main(self, monkeypatch, argv):
        import sys as _sys
        from gnucash_mcp.server import main
        monkeypatch.delenv("GNUCASH_BOOK_PATH", raising=False)
        monkeypatch.setattr(_sys, "argv", ["gnucash-mcp", *argv])
        with pytest.raises(SystemExit) as excinfo:
            main()
        return excinfo.value.code

    def test_space_separated_modules_fails_fast(self, monkeypatch, capsys):
        code = self._run_main(monkeypatch, ["--modules", "all"])
        assert code == 2
        err = capsys.readouterr().err
        assert "Unrecognized argument(s): --modules all" in err
        assert "--modules=all" in err  # the corrective hint

    def test_unknown_flag_fails_fast(self, monkeypatch, capsys):
        code = self._run_main(monkeypatch, ["--modlues=all"])
        assert code == 2
        err = capsys.readouterr().err
        assert "--modlues=all" in err
        assert "Accepted options" in err

    def test_stray_positional_fails_fast(self, monkeypatch, capsys):
        code = self._run_main(monkeypatch, ["all"])
        assert code == 2
        assert "all" in capsys.readouterr().err

    def test_help_still_exits_zero(self, monkeypatch, capsys):
        code = self._run_main(monkeypatch, ["--help"])
        assert code == 0
        assert "GnuCash MCP Server" in capsys.readouterr().out


class TestBookCliArg:
    """--book CLI argument — the MCPB bundle's book interface.

    Multi-value (``--book A B``, the shape a manifest multi-file
    picker expands to), repeatable, ``--book=PATH``. Args win over
    GNUCASH_BOOK_PATH, and both interfaces share the
    _validate_book_paths chokepoint so validation cannot diverge.
    """

    @pytest.fixture(autouse=True)
    def restore_book_state(self):
        import gnucash_mcp.server as srv
        import gnucash_mcp.logging_config as logcfg

        saved = (
            srv._book, list(srv._book_paths), srv._current_path,
            dict(srv._book_registry),
        )
        log_saved = (
            logcfg._book_path_str, logcfg._log_dir, logcfg._get_book_func,
        )
        yield
        srv._book, srv._book_paths, srv._current_path = saved[:3]
        srv._book_registry = saved[3]
        logcfg._book_path_str, logcfg._log_dir, logcfg._get_book_func = log_saved

    # ── _parse_cli_argv ────────────────────────────────────────────

    def test_multi_value_form(self):
        from gnucash_mcp.server import _parse_cli_argv
        books, debug, noaudit, modules = _parse_cli_argv(
            ["--book", "/a.gnucash", "/b.gnucash", "--debug"]
        )
        assert books == ["/a.gnucash", "/b.gnucash"]
        assert debug is True
        assert noaudit is False
        assert modules is None

    def test_repeat_and_equals_forms(self):
        from gnucash_mcp.server import _parse_cli_argv
        books, *_ = _parse_cli_argv(["--book=/a", "--book", "/b"])
        assert books == ["/a", "/b"]

    def test_book_without_value_fails(self):
        from gnucash_mcp.server import _CliParseError, _parse_cli_argv
        with pytest.raises(_CliParseError, match="--book requires"):
            _parse_cli_argv(["--book", "--debug"])

    def test_unknown_flag_still_fails(self):
        from gnucash_mcp.server import _CliParseError, _parse_cli_argv
        with pytest.raises(_CliParseError, match="Unrecognized"):
            _parse_cli_argv(["--books=/a"])

    # ── _apply_book_args ───────────────────────────────────────────

    def test_apply_overrides_env_and_mirrors_it(self, tmp_path, monkeypatch):
        import gnucash_mcp.server as srv
        alex = _make_min_book(tmp_path / "alex.gnucash")
        beast = _make_min_book(tmp_path / "beast-man.gnucash")
        other = _make_min_book(tmp_path / "other.gnucash")
        monkeypatch.setenv("GNUCASH_BOOK_PATH", str(other))
        srv._apply_book_args([str(alex), str(beast)])
        assert srv._book_paths == [alex.resolve(), beast.resolve()]
        assert srv._current_path == alex.resolve()
        # Mirrored into the env: get_book() re-reads it on reset.
        assert os.environ["GNUCASH_BOOK_PATH"] == os.pathsep.join(
            [str(alex.resolve()), str(beast.resolve())]
        )
        assert srv._book is None

    def test_apply_missing_file_names_the_flag(self, tmp_path):
        import gnucash_mcp.server as srv
        with pytest.raises(srv._BookPathError, match=r"Invalid --book"):
            srv._apply_book_args([str(tmp_path / "nope.gnucash")])

    # ── main() integration (failure exits before the module filter,
    #    so the tool registry is untouched) ─────────────────────────

    def test_main_book_flag_missing_file_fails_fast(
        self, tmp_path, monkeypatch, capsys
    ):
        import sys as _sys
        from gnucash_mcp.server import main
        monkeypatch.delenv("GNUCASH_BOOK_PATH", raising=False)
        monkeypatch.setattr(
            _sys, "argv",
            ["gnucash-mcp", "--book", str(tmp_path / "nope.gnucash")],
        )
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 2
        assert "Invalid --book" in capsys.readouterr().err


class TestEnvModuleToggles:
    """GNUCASH_ENABLE_* booleans — the MCPB manifest's checkbox
    interface. Composed only when at least one toggle var is present;
    --modules / GNUCASH_MCP_MODULES win in main()."""

    @pytest.fixture(autouse=True)
    def clear_toggles(self, monkeypatch):
        from gnucash_mcp.server import _ENV_MODULE_TOGGLES
        for var in _ENV_MODULE_TOGGLES:
            monkeypatch.delenv(var, raising=False)

    def test_absent_returns_none(self):
        from gnucash_mcp.server import _modules_from_env_toggles
        assert _modules_from_env_toggles() is None

    def test_planning_only(self, monkeypatch):
        from gnucash_mcp.server import _modules_from_env_toggles
        monkeypatch.setenv("GNUCASH_ENABLE_PLANNING", "true")
        assert _modules_from_env_toggles() == "reporting,budgets,scheduling"

    def test_all_false_still_gets_reporting(self, monkeypatch):
        from gnucash_mcp.server import (
            _ENV_MODULE_TOGGLES, _modules_from_env_toggles,
        )
        for var in _ENV_MODULE_TOGGLES:
            monkeypatch.setenv(var, "false")
        assert _modules_from_env_toggles() == "reporting"

    def test_mcpb_style_selection(self, monkeypatch):
        from gnucash_mcp.server import _modules_from_env_toggles
        monkeypatch.setenv("GNUCASH_ENABLE_PLANNING", "false")
        monkeypatch.setenv("GNUCASH_ENABLE_INVESTMENTS", "true")
        monkeypatch.setenv("GNUCASH_ENABLE_FREELANCER", "false")
        monkeypatch.setenv("GNUCASH_ENABLE_BUSINESS", "true")
        assert _modules_from_env_toggles() == "reporting,investor,business"

    def test_invalid_value_fails_fast(self, monkeypatch):
        from gnucash_mcp.server import _modules_from_env_toggles
        monkeypatch.setenv("GNUCASH_ENABLE_PLANNING", "ture")
        with pytest.raises(ValueError, match="GNUCASH_ENABLE_PLANNING"):
            _modules_from_env_toggles()

    def test_toggle_targets_exist_in_registry(self):
        """Contract lock: every toggle target (and the always-on
        ``reporting``) must be a real module or group name, so a
        module rename cannot silently strand the bundle's checkboxes
        on an unknown-module startup error."""
        from gnucash_mcp.server import _ENV_MODULE_TOGGLES
        valid = set(TOOL_MODULES) | set(MODULE_GROUPS)
        assert "reporting" in valid
        for modules in _ENV_MODULE_TOGGLES.values():
            for name in modules:
                assert name in valid, name


class TestBookFormatSniff:
    """Startup rejects non-SQLite books with a message a
    non-developer can act on (the XML-format file-picker mistake)."""

    @pytest.fixture(autouse=True)
    def restore_book_state(self):
        import gnucash_mcp.server as srv
        saved = (srv._book, list(srv._book_paths), srv._current_path)
        yield
        srv._book, srv._book_paths, srv._current_path = saved

    def test_sqlite_book_passes(self, tmp_path):
        from gnucash_mcp.server import _book_format_error
        book = _make_min_book(tmp_path / "ok.gnucash")
        assert _book_format_error(book) is None

    def test_xml_book_gets_save_as_message(self, tmp_path):
        from gnucash_mcp.server import _book_format_error
        p = tmp_path / "old.gnucash"
        p.write_bytes(b'<?xml version="1.0" encoding="utf-8" ?>\n<gnc-v2>')
        msg = _book_format_error(p)
        assert msg is not None
        assert "XML format" in msg
        assert "Save As" in msg
        assert "old.gnucash" in msg

    def test_gzipped_xml_book_gets_save_as_message(self, tmp_path):
        import gzip
        from gnucash_mcp.server import _book_format_error
        p = tmp_path / "compressed.gnucash"
        p.write_bytes(gzip.compress(b'<?xml version="1.0"?><gnc-v2>'))
        msg = _book_format_error(p)
        assert msg is not None
        assert "XML format" in msg

    def test_unrecognized_header_gets_generic_message(self, tmp_path):
        from gnucash_mcp.server import _book_format_error
        p = tmp_path / "mystery.gnucash"
        p.write_bytes(b"\x00\x01 definitely not a book")
        msg = _book_format_error(p)
        assert msg is not None
        assert "does not look like" in msg
        assert "sqlite3" in msg

    def test_main_xml_book_fails_fast(self, tmp_path, monkeypatch, capsys):
        import sys as _sys
        from gnucash_mcp.server import main
        p = tmp_path / "old.gnucash"
        p.write_bytes(b'<?xml version="1.0"?>\n<gnc-v2>')
        monkeypatch.setenv("GNUCASH_BOOK_PATH", str(p))
        monkeypatch.setattr(_sys, "argv", ["gnucash-mcp"])
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 2
        assert "XML format" in capsys.readouterr().err


class TestHelpTextCounts:
    """--help tool counts derive from the registry, so they can
    never again drift the way the hardcoded "107 tools" did while
    the server served 110."""

    def test_module_tool_count_agrees_with_registry(self):
        from gnucash_mcp.server import _module_tool_count
        assert _module_tool_count("all") == sum(
            len(tools) for tools in TOOL_MODULES.values()
        )
        assert _module_tool_count("core") == len(_core_tool_names())
        for group, members in MODULE_GROUPS.items():
            assert _module_tool_count(group) == len(
                {t for m in members for t in TOOL_MODULES[m]}
            )

    def test_help_text_embeds_derived_counts(self):
        from gnucash_mcp.server import _build_help_text, _module_tool_count
        text = _build_help_text()
        assert f"core ({_module_tool_count('core')} tools" in text
        assert f"({_module_tool_count('all')} tools" in text
        for group in ("bookkeeper", "investor", "freelancer", "business"):
            assert f"{_module_tool_count(group)} tools." in text

    def test_help_text_mentions_conditional_switch_book(self):
        """switch_book sits outside the module partition (inline,
        multi-book only), so the total line must flag it rather
        than fold it into the count."""
        from gnucash_mcp.server import _build_help_text
        assert "switch_book" in _build_help_text()

    def test_help_text_has_no_unrendered_placeholders(self):
        """The help block is an f-string with literal {book_path}
        examples that must stay doubled — a missed brace renders
        as a stray Python expression or eats the example."""
        from gnucash_mcp.server import _build_help_text
        text = _build_help_text()
        assert "{book_path}.mcp" in text
        assert "{GNUCASH_LOG_DIR}" in text
