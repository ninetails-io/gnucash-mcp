"""Tests for tool module filtering."""

import pytest

from gnucash_mcp.server import TOOL_MODULES, _apply_module_filter, _validate_tool_modules, mcp


class TestToolModulesMapping:
    """Tests for the TOOL_MODULES constant."""

    def test_all_registered_tools_are_mapped(self):
        """Every registered tool must appear in TOOL_MODULES."""
        all_mapped = set()
        for tools in TOOL_MODULES.values():
            all_mapped.update(tools)
        registered = set(mcp._tool_manager._tools.keys())
        assert all_mapped == registered

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
        """Total tools across all modules should be 52."""
        total = sum(len(tools) for tools in TOOL_MODULES.values())
        assert total == 52

    def test_expected_modules_exist(self):
        """All expected module names should be present."""
        expected = {
            "core", "reconciliation", "reporting", "budgets",
            "scheduling", "investments", "admin",
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
        """--modules=all should keep all 52 tools."""
        _apply_module_filter("all")
        assert len(self._tool_names()) == 52

    def test_none_defaults_to_core_only(self):
        """No --modules flag should default to core only."""
        _apply_module_filter(None)
        remaining = self._tool_names()
        assert remaining == set(TOOL_MODULES["core"])
        assert len(remaining) == 15

    def test_core_plus_reporting(self):
        """--modules=core,reporting should load both modules."""
        _apply_module_filter("core,reporting")
        remaining = self._tool_names()
        expected = set(TOOL_MODULES["core"]) | set(TOOL_MODULES["reporting"])
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
        assert len(self._tool_names()) == 52

    def test_all_in_list_keeps_everything(self):
        """'all' mixed with other modules should keep all 52 tools."""
        _apply_module_filter("scheduling,reconciliation,all")
        assert len(self._tool_names()) == 52

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
        expected = set(TOOL_MODULES["core"]) | set(TOOL_MODULES["reporting"])
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
