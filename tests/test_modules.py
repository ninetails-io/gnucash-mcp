"""Tests for tool module filtering and server configuration."""

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
        registered = set(mcp._tool_manager._tools.keys())
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

    def test_core_group_resolves_to_29_tools(self):
        """The ``core`` group expands to 29 tools across its nine
        sub-modules (summary 1 + accounts 7 + transactions 9 + slots
        3 + audit 1 + backup 3 + balance_sheet 1 + diagnostic 1 +
        reconciliation 3). Reconciliation joined core in v1.3.1
        per the bookkeeper-driven principle that any configuration
        which handles money must include reconciliation.
        """
        assert len(_core_tool_names()) == 29

    def test_total_tool_count(self):
        """Total tools across all sub-modules should be 106 —
        88 post-module-restructure + 3 voucher tools +
        4 credit-note tools + 5 job CRUD tools +
        1 get_job_report + 5 taxtable CRUD tools added in v1.3."""
        total = sum(len(tools) for tools in TOOL_MODULES.values())
        assert total == 106

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
    restructure broke that bijection — Core's 29 tools span
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
        """--modules=all should keep all 106 tools (88 + 3 vouchers + 4 credit notes + 5 job CRUD + 1 job report + 5 taxtables)."""
        _apply_module_filter("all")
        assert len(self._tool_names()) == 106

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
        assert len(self._tool_names()) == 106

    def test_all_in_list_keeps_everything(self):
        """'all' mixed with other modules should keep all 106 tools."""
        _apply_module_filter("scheduling,reconciliation,all")
        assert len(self._tool_names()) == 106

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
