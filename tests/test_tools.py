"""Tests for MCP tool implementations."""

import json
import os
from datetime import date
from pathlib import Path

import pytest

from gnucash_mcp.tools import TOOLS
from gnucash_mcp import server as server_module


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


@pytest.fixture
def setup_book_env(test_book: Path, monkeypatch):
    """Set up environment with test book path."""
    monkeypatch.setenv("GNUCASH_BOOK_PATH", str(test_book))
    # Reset the global book instance
    server_module._book = None
    yield
    server_module._book = None


class TestListAccountsTool:
    """Tests for list_accounts tool."""

    @pytest.mark.asyncio
    async def test_list_accounts(self, setup_book_env):
        """Should return all accounts."""
        result = await server_module.call_tool("list_accounts", {})

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert len(data) > 0

        fullnames = {a["fullname"] for a in data}
        assert "Assets" in fullnames
        assert "Assets:Checking" in fullnames


class TestGetAccountTool:
    """Tests for get_account tool."""

    @pytest.mark.asyncio
    async def test_get_existing_account(self, setup_book_env):
        """Should return account details."""
        result = await server_module.call_tool(
            "get_account", {"name": "Assets:Checking"}
        )

        data = json.loads(result[0].text)
        assert data["fullname"] == "Assets:Checking"
        assert data["type"] == "BANK"

    @pytest.mark.asyncio
    async def test_get_nonexistent_account(self, setup_book_env):
        """Should return error for missing account."""
        result = await server_module.call_tool(
            "get_account", {"name": "Nonexistent:Account"}
        )

        data = json.loads(result[0].text)
        assert "error" in data


class TestGetBalanceTool:
    """Tests for get_balance tool."""

    @pytest.mark.asyncio
    async def test_get_balance_current(self, setup_book_env):
        """Should return current balance."""
        result = await server_module.call_tool(
            "get_balance", {"account_name": "Assets:Checking"}
        )

        data = json.loads(result[0].text)
        assert data["account"] == "Assets:Checking"
        assert data["balance"] == "2850"
        assert data["as_of_date"] == "current"

    @pytest.mark.asyncio
    async def test_get_balance_as_of_date(self, setup_book_env):
        """Should return balance as of date."""
        result = await server_module.call_tool(
            "get_balance",
            {"account_name": "Assets:Checking", "as_of_date": "2024-01-10"},
        )

        data = json.loads(result[0].text)
        assert data["balance"] == "1000"
        assert data["as_of_date"] == "2024-01-10"


class TestListTransactionsTool:
    """Tests for list_transactions tool."""

    @pytest.mark.asyncio
    async def test_list_all_transactions(self, setup_book_env):
        """Should return all transactions."""
        result = await server_module.call_tool("list_transactions", {})

        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert len(data) == 3

    @pytest.mark.asyncio
    async def test_list_transactions_by_account(self, setup_book_env):
        """Should filter by account."""
        result = await server_module.call_tool(
            "list_transactions", {"account": "Expenses:Groceries"}
        )

        data = json.loads(result[0].text)
        assert len(data) == 1
        assert data[0]["description"] == "Weekly Groceries"

    @pytest.mark.asyncio
    async def test_list_transactions_date_range(self, setup_book_env):
        """Should filter by date range."""
        result = await server_module.call_tool(
            "list_transactions",
            {"start_date": "2024-01-10", "end_date": "2024-01-18"},
        )

        data = json.loads(result[0].text)
        assert len(data) == 1
        assert data[0]["description"] == "Salary Deposit"


class TestGetTransactionTool:
    """Tests for get_transaction tool."""

    @pytest.mark.asyncio
    async def test_get_nonexistent_transaction(self, setup_book_env):
        """Should return error for missing transaction."""
        result = await server_module.call_tool(
            "get_transaction", {"guid": "nonexistent_guid"}
        )

        data = json.loads(result[0].text)
        assert "error" in data


class TestCreateTransactionTool:
    """Tests for create_transaction tool."""

    @pytest.mark.asyncio
    async def test_create_transaction(self, setup_book_env):
        """Should create a transaction and return GUID."""
        result = await server_module.call_tool(
            "create_transaction",
            {
                "description": "Test Purchase",
                "date": "2024-02-01",
                "splits": [
                    {"account": "Expenses:Groceries", "amount": "25.00"},
                    {"account": "Assets:Checking", "amount": "-25.00"},
                ],
            },
        )

        data = json.loads(result[0].text)
        assert "guid" in data
        assert data["status"] == "created"
        assert len(data["guid"]) == 32

    @pytest.mark.asyncio
    async def test_create_unbalanced_transaction(self, setup_book_env):
        """Should return error for unbalanced splits."""
        result = await server_module.call_tool(
            "create_transaction",
            {
                "description": "Unbalanced",
                "splits": [
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-40.00"},
                ],
            },
        )

        data = json.loads(result[0].text)
        assert "error" in data
        assert "balance" in data["error"].lower()


class TestSearchTransactionsTool:
    """Tests for search_transactions tool."""

    @pytest.mark.asyncio
    async def test_search_by_description(self, setup_book_env):
        """Should find transactions by description."""
        result = await server_module.call_tool(
            "search_transactions", {"query": "Salary"}
        )

        data = json.loads(result[0].text)
        assert len(data) == 1
        assert data[0]["description"] == "Salary Deposit"

    @pytest.mark.asyncio
    async def test_search_by_amount(self, setup_book_env):
        """Should find transactions by amount range."""
        result = await server_module.call_tool(
            "search_transactions", {"query": ">500", "field": "amount"}
        )

        data = json.loads(result[0].text)
        assert len(data) == 2  # Opening Balance (1000) and Salary (2000)


class TestResources:
    """Tests for MCP resources."""

    @pytest.mark.asyncio
    async def test_list_resources(self, setup_book_env):
        """Should list available resources."""
        resources = await server_module.list_resources()

        assert len(resources) >= 1
        uris = {str(r.uri) for r in resources}
        assert "gnucash://accounts" in uris

    @pytest.mark.asyncio
    async def test_read_accounts_resource(self, setup_book_env):
        """Should read accounts resource."""
        content = await server_module.read_resource("gnucash://accounts")

        data = json.loads(content)
        assert isinstance(data, list)
        fullnames = {a["fullname"] for a in data}
        assert "Assets:Checking" in fullnames

    @pytest.mark.asyncio
    async def test_read_balance_resource(self, setup_book_env):
        """Should read balance resource."""
        content = await server_module.read_resource(
            "gnucash://balance/Assets%3AChecking"
        )

        data = json.loads(content)
        assert data["account"] == "Assets:Checking"
        assert data["balance"] == "2850"
