"""Tests for MCP server tools and resources."""

import json
import os
from pathlib import Path

import pytest

from gnucash_mcp import server as server_module


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

    def test_list_accounts(self, setup_book_env):
        """Should return all accounts."""
        result = server_module.list_accounts()

        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) > 0

        fullnames = {a["fullname"] for a in data}
        assert "Assets" in fullnames
        assert "Assets:Checking" in fullnames


class TestGetAccountTool:
    """Tests for get_account tool."""

    def test_get_existing_account(self, setup_book_env):
        """Should return account details."""
        result = server_module.get_account("Assets:Checking")

        data = json.loads(result)
        assert data["fullname"] == "Assets:Checking"
        assert data["type"] == "BANK"

    def test_get_nonexistent_account(self, setup_book_env):
        """Should return error for missing account."""
        result = server_module.get_account("Nonexistent:Account")

        data = json.loads(result)
        assert "error" in data


class TestGetBalanceTool:
    """Tests for get_balance tool."""

    def test_get_balance_current(self, setup_book_env):
        """Should return current balance."""
        result = server_module.get_balance("Assets:Checking")

        data = json.loads(result)
        assert data["account"] == "Assets:Checking"
        assert data["balance"] == "2850"
        assert data["as_of_date"] == "current"

    def test_get_balance_as_of_date(self, setup_book_env):
        """Should return balance as of date."""
        result = server_module.get_balance("Assets:Checking", "2024-01-10")

        data = json.loads(result)
        assert data["balance"] == "1000"
        assert data["as_of_date"] == "2024-01-10"


class TestListTransactionsTool:
    """Tests for list_transactions tool."""

    def test_list_all_transactions(self, setup_book_env):
        """Should return all transactions."""
        result = server_module.list_transactions()

        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 3

    def test_list_transactions_by_account(self, setup_book_env):
        """Should filter by account."""
        result = server_module.list_transactions(account="Expenses:Groceries")

        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["description"] == "Weekly Groceries"

    def test_list_transactions_date_range(self, setup_book_env):
        """Should filter by date range."""
        result = server_module.list_transactions(
            start_date="2024-01-10", end_date="2024-01-18"
        )

        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["description"] == "Salary Deposit"


class TestGetTransactionTool:
    """Tests for get_transaction tool."""

    def test_get_nonexistent_transaction(self, setup_book_env):
        """Should return error for missing transaction."""
        result = server_module.get_transaction("nonexistent_guid")

        data = json.loads(result)
        assert "error" in data


class TestCreateTransactionTool:
    """Tests for create_transaction tool."""

    def test_create_transaction(self, setup_book_env):
        """Should create a transaction and return GUID."""
        result = server_module.create_transaction(
            description="Test Purchase",
            splits=[
                {"account": "Expenses:Groceries", "amount": "25.00"},
                {"account": "Assets:Checking", "amount": "-25.00"},
            ],
            transaction_date="2024-02-01",
        )

        data = json.loads(result)
        assert "guid" in data
        assert data["status"] == "created"
        assert len(data["guid"]) == 32

    def test_create_unbalanced_transaction(self, setup_book_env):
        """Should return error for unbalanced splits."""
        result = server_module.create_transaction(
            description="Unbalanced",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-40.00"},
            ],
        )

        data = json.loads(result)
        assert "error" in data
        assert "balance" in data["error"].lower()


class TestSearchTransactionsTool:
    """Tests for search_transactions tool."""

    def test_search_by_description(self, setup_book_env):
        """Should find transactions by description."""
        result = server_module.search_transactions("Salary")

        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["description"] == "Salary Deposit"

    def test_search_by_amount(self, setup_book_env):
        """Should find transactions by amount range."""
        result = server_module.search_transactions(">500", field="amount")

        data = json.loads(result)
        assert len(data) == 2  # Opening Balance (1000) and Salary (2000)


class TestCreateAccountTool:
    """Tests for create_account tool."""

    def test_create_account(self, setup_book_env):
        """Should create account and return result."""
        result = server_module.create_account(
            name="Test Category",
            account_type="EXPENSE",
            parent="Expenses",
            description="A test category",
        )

        data = json.loads(result)
        assert data["status"] == "created"
        assert data["fullname"] == "Expenses:Test Category"

    def test_create_account_invalid_parent(self, setup_book_env):
        """Should return error for invalid parent."""
        result = server_module.create_account(
            name="Test",
            account_type="EXPENSE",
            parent="Nonexistent:Parent",
        )

        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()


class TestResources:
    """Tests for MCP resources."""

    def test_read_accounts_resource(self, setup_book_env):
        """Should read accounts resource."""
        content = server_module.accounts_resource()

        data = json.loads(content)
        assert isinstance(data, list)
        fullnames = {a["fullname"] for a in data}
        assert "Assets:Checking" in fullnames
