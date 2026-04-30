"""Tests for tool module filtering and server configuration."""

import pytest

from gnucash_mcp.book import extracted_modules
from gnucash_mcp.server import (
    TOOL_MODULES,
    _apply_module_filter,
    _get_server_config_impl,
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

    def test_every_module_is_extracted(self):
        """Every TOOL_MODULES key has a corresponding mixin + tools file.

        Before the final core extraction some tools shipped at module level
        in server.py (non-extracted modules). That transition state is done —
        every module is now lazy-loaded via gnucash_mcp/tools/<name>.py.
        """
        assert extracted_modules() == set(TOOL_MODULES.keys())

    def test_no_duplicate_tools_across_modules(self):
        """No tool should appear in more than one module."""
        seen = set()
        for tools in TOOL_MODULES.values():
            for tool in tools:
                assert tool not in seen, f"{tool} appears in multiple modules"
                seen.add(tool)

    def test_core_module_count(self):
        """Core module should have 15 tools."""
        assert len(TOOL_MODULES["core"]) == 15

    def test_total_tool_count(self):
        """Total tools across all modules should be 84.

        84 = 78 pre-Employee + 4 Employee CRUD (1.2.0) +
        2 from the 1.2.1 patch (``unpost_invoice``, ``delete_price``).
        """
        total = sum(len(tools) for tools in TOOL_MODULES.values())
        assert total == 84

    def test_expected_modules_exist(self):
        """All expected module names should be present."""
        expected = {
            "core", "reconciliation", "reporting", "budgets",
            "scheduling", "investments", "admin", "business",
            "backup",
        }
        assert set(TOOL_MODULES.keys()) == expected

    def test_validate_tool_modules_passes(self):
        """Validation should pass with the current mapping."""
        _validate_tool_modules()  # Should not raise


class TestApplyModuleFilter:
    """Tests for _apply_module_filter."""

    @pytest.fixture(autouse=True)
    def save_and_restore_tools(self):
        """Save tool state before test, restore after."""
        original = dict(mcp._tool_manager._tools)
        yield
        mcp._tool_manager._tools.clear()
        mcp._tool_manager._tools.update(original)

    def _tool_names(self):
        return set(mcp._tool_manager._tools.keys())

    def test_all_keeps_everything(self):
        """--modules=all should keep all 84 tools."""
        _apply_module_filter("all")
        assert len(self._tool_names()) == 84

    def test_none_defaults_to_core_only(self):
        """No --modules flag defaults to core + backup.

        ``backup`` is always added (alongside ``core``) so every
        user's book gets auto-snapshot protection and on-demand
        backup tools regardless of their ``--modules`` choice.
        """
        _apply_module_filter(None)
        remaining = self._tool_names()
        expected = set(TOOL_MODULES["core"]) | set(TOOL_MODULES["backup"])
        assert remaining == expected

    def test_core_plus_reporting(self):
        """--modules=core,reporting loads both + backup (always)."""
        _apply_module_filter("core,reporting")
        remaining = self._tool_names()
        expected = (
            set(TOOL_MODULES["core"])
            | set(TOOL_MODULES["reporting"])
            | set(TOOL_MODULES["backup"])
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
        assert len(self._tool_names()) == 84

    def test_all_in_list_keeps_everything(self):
        """'all' mixed with other modules should keep all 84 tools."""
        _apply_module_filter("scheduling,reconciliation,all")
        assert len(self._tool_names()) == 84

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
            | set(TOOL_MODULES["backup"])
        )
        assert remaining == expected

    def test_investments_module_tools(self):
        """Investments module should include commodity and lot tools."""
        _apply_module_filter("investments")
        remaining = self._tool_names()
        assert "list_commodities" in remaining
        assert "create_lot" in remaining
        assert "calculate_lot_gain" in remaining
        # Core should also be present
        assert "list_accounts" in remaining
        # Non-selected modules should not be present
        assert "spending_by_category" not in remaining

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
        # core and backup are both always added; result is sorted.
        assert result == ["backup", "budgets", "core", "reporting"]

    def test_returns_all_modules_for_all(self):
        """'all' should return all module names sorted."""
        result = _apply_module_filter("all")
        assert result == sorted(TOOL_MODULES.keys())

    def test_returns_core_for_none(self):
        """None returns core + backup (both are unconditional)."""
        result = _apply_module_filter(None)
        assert result == ["backup", "core"]

    def test_returns_excludes_unknown_modules(self):
        """Unknown module names should not appear in return value."""
        result = _apply_module_filter("reporting,nonexistent")
        assert "nonexistent" not in result
        assert "core" in result
        assert "reporting" in result


class TestExtractedModuleLazyLoading:
    """Verify that extracted module tools are lazy-loaded on demand.

    Extracted modules (see `extracted_modules()` — 'admin' initially)
    are NOT registered at server.py import time. Their tools only
    appear in the FastMCP registry after `_apply_module_filter` enables
    them or `_lazy_load_tool_module` is called directly.
    """

    @pytest.fixture(autouse=True)
    def save_and_restore_tools(self):
        original = dict(mcp._tool_manager._tools)
        yield
        mcp._tool_manager._tools.clear()
        mcp._tool_manager._tools.update(original)

    def test_extracted_modules_are_not_registered_at_import(self):
        """Tools from extracted modules must not be present at import time."""
        for mod_name in extracted_modules():
            for tool_name in TOOL_MODULES[mod_name]:
                assert tool_name not in mcp._tool_manager._tools, (
                    f"{tool_name} from extracted module '{mod_name}' "
                    f"was registered at import — defeats lazy loading."
                )

    def test_enabling_extracted_module_registers_its_tools(self):
        """Enabling 'admin' via _apply_module_filter registers all admin tools."""
        _apply_module_filter("admin")
        for tool_name in TOOL_MODULES["admin"]:
            assert tool_name in mcp._tool_manager._tools

    def test_all_loads_every_extracted_module(self):
        """--modules=all lazy-loads all extracted modules."""
        _apply_module_filter("all")
        all_expected = set()
        for tools in TOOL_MODULES.values():
            all_expected.update(tools)
        assert set(mcp._tool_manager._tools.keys()) == all_expected

    def test_lazy_load_is_idempotent(self):
        """Enabling an extracted module twice does not duplicate tools."""
        _apply_module_filter("admin")
        count_after_first = len(mcp._tool_manager._tools)
        _apply_module_filter("admin")
        assert len(mcp._tool_manager._tools) == count_after_first


class TestGetServerConfig:
    """Tests for the debug-only get_server_config tool."""

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

    def test_not_registered_by_default(self):
        """get_server_config should not be in TOOL_MODULES or registered at import time."""
        all_module_tools = set()
        for tools in TOOL_MODULES.values():
            all_module_tools.update(tools)
        assert "get_server_config" not in all_module_tools
