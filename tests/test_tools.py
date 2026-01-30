"""Tests for MCP tool implementations."""

import pytest

from gnucash_mcp.tools import TOOLS


class TestToolDefinitions:
    """Tests for tool definitions."""

    def test_all_tools_defined(self):
        """Should have all expected tools defined."""
        tool_names = {t.name for t in TOOLS}
        expected = {
            "list_accounts",
            "get_account",
            "get_balance",
            "list_transactions",
            "get_transaction",
            "create_transaction",
            "search_transactions",
        }
        assert tool_names == expected

    def test_tools_have_descriptions(self):
        """All tools should have descriptions."""
        for tool in TOOLS:
            assert tool.description, f"Tool {tool.name} missing description"

    def test_tools_have_valid_schemas(self):
        """All tools should have valid input schemas."""
        for tool in TOOLS:
            assert tool.inputSchema is not None
            assert tool.inputSchema.get("type") == "object"


# TODO: Add integration tests for tool execution as they are implemented
# class TestListAccountsTool:
# class TestGetAccountTool:
# class TestGetBalanceTool:
# class TestListTransactionsTool:
# class TestGetTransactionTool:
# class TestCreateTransactionTool:
# class TestSearchTransactionsTool:
