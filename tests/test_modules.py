"""Tests for tool module filtering and server configuration."""

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
        and ``investor`` are new public modules that don't have
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

    def test_core_group_resolves_to_26_tools(self):
        """The ``core`` group expands to 26 tools across its eight
        sub-modules (summary 1 + accounts 7 + transactions 9 + slots
        3 + audit 1 + backup 3 + balance_sheet 1 + diagnostic 1)."""
        assert len(_core_tool_names()) == 26

    def test_total_tool_count(self):
        """Total tools across all sub-modules should be 88 — same
        as pre-Core-chop, just partitioned across more keys."""
        total = sum(len(tools) for tools in TOOL_MODULES.values())
        assert total == 88

    def test_expected_modules_exist(self):
        """All expected sub-module names should be present after the
        Core-chop. ``core`` is no longer a TOOL_MODULES key — it's
        a MODULE_GROUPS alias expanding to the eight sub-modules."""
        expected = {
            # Core sub-modules
            "summary", "accounts", "transactions", "slots",
            "audit", "backup", "balance_sheet", "diagnostic",
            # Optional modules
            "reconciliation", "reporting", "budgets",
            "scheduling", "portfolio", "investor",
            "freelancer", "business",
        }
        assert set(TOOL_MODULES.keys()) == expected
        # And ``core`` is the group alias.
        assert set(MODULE_GROUPS["core"]) == {
            "summary", "accounts", "transactions", "slots",
            "audit", "backup", "balance_sheet", "diagnostic",
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
    restructure broke that bijection — Core's 26 tools span
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
        """--modules=all should keep all 87 tools."""
        _apply_module_filter("all")
        assert len(self._tool_names()) == 88

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
        assert len(self._tool_names()) == 88

    def test_all_in_list_keeps_everything(self):
        """'all' mixed with other modules should keep all 87 tools."""
        _apply_module_filter("scheduling,reconciliation,all")
        assert len(self._tool_names()) == 88

    def test_unknown_module_warns(self, capsys):
        """Unknown module names should produce a warning on stderr."""
        _apply_module_filter("core,nonexistent")
        captured = capsys.readouterr()
        assert "nonexistent" in captured.err
        assert "Unknown module" in captured.err

    def test_unknown_module_still_loads_core(self, capsys):
        """Unknown modules should not prevent the ``core`` group from
        loading."""
        _apply_module_filter("nonexistent")
        remaining = self._tool_names()
        assert _core_tool_names().issubset(remaining)

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

    def test_portfolio_and_investor_split(self):
        """``portfolio`` (commodities + prices) and ``investor``
        (tax lots) used to be one ``investments`` module. After the
        split they're independently selectable: a multi-currency
        household without a brokerage picks portfolio alone."""
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

        # investor alone — lot tools yes, price tools no
        _reset_lazy_load_state()
        mcp._tool_manager._tools.clear()
        _apply_module_filter("investor")
        remaining = self._tool_names()
        assert "create_lot" in remaining
        assert "calculate_lot_gain" in remaining
        assert "list_commodities" not in remaining
        assert "create_price" not in remaining

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
        """Unknown module names should not appear in return value.
        The ``core`` group's sub-modules are always present even
        when the user-supplied list contained only garbage."""
        result = _apply_module_filter("reporting,nonexistent")
        assert "nonexistent" not in result
        # One representative Core sub-module is enough.
        assert "accounts" in result
        assert "reporting" in result


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
        assert "Book path: /tmp/test.gnucash" in output
        assert "Debug mode: true" in output
        assert "Version:" in output

    def test_impl_defaults_when_state_empty(self):
        """Output should handle missing state gracefully."""
        _server_state.clear()
        output = _get_server_config_impl()
        assert "Modules loaded: unknown" in output
        assert "Tools available: unknown" in output
        assert "Book path: not set" in output
        assert "Debug mode: false" in output

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
        """With business enabled, _gate_owner_type returns its input
        unchanged — both halves of the polymorphic dispatch work."""
        from gnucash_mcp.server import _LOADED_MODULES
        from gnucash_mcp.tools._helpers import _gate_owner_type

        _LOADED_MODULES.clear()
        _LOADED_MODULES.update({"core", "freelancer", "business"})
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
        with pytest.raises(ValueError, match="requires the Business module"):
            _gate_owner_type("vendor")
