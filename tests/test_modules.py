"""Tests for tool module filtering and server configuration."""

import pytest

from gnucash_mcp.book import extracted_modules
from gnucash_mcp.server import (
    MODULE_BACKED_BY,
    TOOL_MODULES,
    _apply_module_filter,
    _get_server_config_impl,
    _loaded_tool_files,
    _reset_lazy_load_state,
    _server_state,
    _validate_tool_modules,
    mcp,
)


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

    def test_core_module_count(self):
        """Core module should have 26 tools after the restructure
        (15 original + 2 void/unvoid migrated from reconciliation +
        3 slot tools + audit log from admin + 3 backup tools)."""
        assert len(TOOL_MODULES["core"]) == 26

    def test_total_tool_count(self):
        """Total tools across all modules should be 88 — 87 pre-
        restructure + ``get_server_config`` (promoted from
        ``--debug``-only conditional to unconditional Core tool)."""
        total = sum(len(tools) for tools in TOOL_MODULES.values())
        assert total == 88

    def test_expected_modules_exist(self):
        """All expected module names should be present after the
        restructure. ``admin`` and ``backup`` have dissolved (their
        tools migrated into Core)."""
        expected = {
            "core", "reconciliation", "reporting", "budgets",
            "scheduling", "portfolio", "investor", "business",
        }
        assert set(TOOL_MODULES.keys()) == expected

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
        """No --modules flag defaults to core. Backups, audit log,
        and slot tools all live in Core post-restructure, so the
        force-add of a separate backup module is gone."""
        _apply_module_filter(None)
        remaining = self._tool_names()
        expected = set(TOOL_MODULES["core"])
        assert remaining == expected

    def test_core_plus_reporting(self):
        """--modules=core,reporting loads both."""
        _apply_module_filter("core,reporting")
        remaining = self._tool_names()
        expected = (
            set(TOOL_MODULES["core"])
            | set(TOOL_MODULES["reporting"])
        )
        assert remaining == expected

    def test_core_always_included(self):
        """Even if only 'reporting' is specified, core is always included."""
        _apply_module_filter("reporting")
        remaining = self._tool_names()
        assert set(TOOL_MODULES["core"]).issubset(remaining)
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
        """Unknown modules should not prevent core from loading."""
        _apply_module_filter("nonexistent")
        remaining = self._tool_names()
        assert set(TOOL_MODULES["core"]).issubset(remaining)

    def test_whitespace_in_module_names(self):
        """Whitespace around module names should be stripped."""
        _apply_module_filter("core , reporting")
        remaining = self._tool_names()
        expected = (
            set(TOOL_MODULES["core"])
            | set(TOOL_MODULES["reporting"])
        )
        assert remaining == expected

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
        """Return value should be sorted list of actually loaded modules."""
        result = _apply_module_filter("reporting,budgets")
        # core is always added; result is sorted.
        assert result == ["budgets", "core", "reporting"]

    def test_returns_all_modules_for_all(self):
        """'all' should return all module names sorted."""
        result = _apply_module_filter("all")
        assert result == sorted(TOOL_MODULES.keys())

    def test_returns_core_for_none(self):
        """None returns just core. Backup/admin tools migrated INTO
        core, so no separate force-load is needed."""
        result = _apply_module_filter(None)
        assert result == ["core"]

    def test_returns_excludes_unknown_modules(self):
        """Unknown module names should not appear in return value."""
        result = _apply_module_filter("reporting,nonexistent")
        assert "nonexistent" not in result
        assert "core" in result
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
        """Tools from extracted modules must not be present at import
        time, excluding the always-registered-inline set. Iterates
        only the modules that still have a TOOL_MODULES entry (admin
        and backup dissolved as module names; their tool files
        survive)."""
        for mod_name in extracted_modules():
            if mod_name not in TOOL_MODULES:
                continue
            for tool_name in TOOL_MODULES[mod_name]:
                if tool_name in self._ALWAYS_REGISTERED_INLINE:
                    continue
                assert tool_name not in mcp._tool_manager._tools, (
                    f"{tool_name} from extracted module '{mod_name}' "
                    f"was registered at import — defeats lazy loading."
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

    def test_registered_in_core_unconditionally(self):
        """get_server_config moved into Core during the restructure
        and is registered at server import time, regardless of
        --debug. Always available as a diagnostic surface."""
        assert "get_server_config" in TOOL_MODULES["core"]
        assert "get_server_config" in mcp._tool_manager._tools
