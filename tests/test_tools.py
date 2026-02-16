"""Tests for MCP server tools and resources."""

import json
import os
from pathlib import Path
from unittest.mock import patch

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


class TestGetBookSummaryTool:
    """Tests for get_book_summary tool."""

    def test_get_book_summary(self, setup_book_env):
        """Should return a text summary string."""
        result = server_module.get_book_summary()
        assert isinstance(result, str)
        assert "Book:" in result
        assert "Currency: USD" in result
        assert "Accounts:" in result
        assert "Net worth:" in result


class TestListAccountsTool:
    """Tests for list_accounts tool."""

    def test_list_accounts_compact_default(self, setup_book_env):
        """Default should return compact one-line-per-account format."""
        result = server_module.list_accounts()

        # Compact mode returns plain string, not JSON array
        assert not result.startswith("[")
        assert "Assets:Checking" in result
        assert "\n" in result

    def test_list_accounts_verbose(self, setup_book_env):
        """verbose=True should return full JSON."""
        result = server_module.list_accounts(verbose=True)

        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) > 0
        fullnames = {a["fullname"] for a in data}
        assert "Assets" in fullnames
        assert "Assets:Checking" in fullnames

    def test_list_accounts_root_filter(self, setup_book_env):
        """root parameter should filter accounts."""
        result = server_module.list_accounts(root="Expenses")
        lines = result.strip().split("\n")
        for line in lines:
            assert line.startswith("Expenses")

    def test_list_accounts_root_with_verbose(self, setup_book_env):
        """root + verbose should return filtered JSON."""
        result = server_module.list_accounts(root="Assets", verbose=True)
        data = json.loads(result)
        assert isinstance(data, list)
        for a in data:
            assert a["fullname"].startswith("Assets")


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
        result = server_module.list_transactions(verbose=True)

        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 3

    def test_list_transactions_by_account(self, setup_book_env):
        """Should filter by account."""
        result = server_module.list_transactions(account="Expenses:Groceries", verbose=True)

        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["description"] == "Weekly Groceries"

    def test_list_transactions_date_range(self, setup_book_env):
        """Should filter by date range."""
        result = server_module.list_transactions(
            start_date="2024-01-10", end_date="2024-01-18", verbose=True
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

    def test_create_transaction_placeholder_rejected(self, setup_book_env):
        """Should reject transaction targeting placeholder account."""
        result = server_module.create_transaction(
            description="Bad Transaction",
            splits=[
                {"account": "Expenses", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
        )

        data = json.loads(result)
        assert "error" in data
        assert "placeholder" in data["error"].lower()

    def test_create_transaction_duplicate_rejected(self, setup_book_env):
        """Should reject HIGH confidence duplicates via server layer."""
        result = server_module.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            transaction_date="2024-01-20",
        )

        data = json.loads(result)
        assert data["status"] == "rejected"
        assert data["reason"] == "duplicate_detected"

    def test_create_transaction_duplicate_force(self, setup_book_env):
        """Should create with force_create despite HIGH duplicate."""
        result = server_module.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            transaction_date="2024-01-20",
            force_create=True,
        )

        data = json.loads(result)
        assert data["status"] == "created"
        assert "guid" in data

    def test_create_transaction_dry_run(self, setup_book_env):
        """Should return proposal without writing in dry run mode."""
        result = server_module.create_transaction(
            description="Dry Run Server Test",
            splits=[
                {"account": "Expenses:Groceries", "amount": "75.00"},
                {"account": "Assets:Checking", "amount": "-75.00"},
            ],
            transaction_date="2024-03-01",
            dry_run=True,
        )

        data = json.loads(result)
        assert data["dry_run"] is True
        assert data["proposed_transaction"]["description"] == "Dry Run Server Test"
        assert "guid" not in data

    def test_create_transaction_auto_fill(self, setup_book_env):
        """Should auto-fill splits from matching transaction."""
        result = server_module.create_transaction(
            description="Weekly Groceries",
            transaction_date="2024-03-01",
            check_duplicates=False,
        )

        data = json.loads(result)
        assert data["status"] == "created"
        assert "auto_filled_from" in data
        assert data["auto_filled_from"]["description"] == "Weekly Groceries"


class TestSearchTransactionsTool:
    """Tests for search_transactions tool."""

    def test_search_by_description(self, setup_book_env):
        """Should find transactions by description."""
        result = server_module.search_transactions("Salary", verbose=True)

        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["description"] == "Salary Deposit"

    def test_search_by_amount(self, setup_book_env):
        """Should find transactions by amount range."""
        result = server_module.search_transactions(">500", field="amount", verbose=True)

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


class TestUpdateAccountTool:
    """Tests for update_account tool."""

    def test_update_account_rename(self, setup_book_env):
        """Should rename an account."""
        result = server_module.update_account(
            name="Expenses:Groceries",
            new_name="Food",
        )

        data = json.loads(result)
        assert data["status"] == "updated"
        assert data["name"] == "Food"

    def test_update_account_not_found(self, setup_book_env):
        """Should return error for non-existent account."""
        result = server_module.update_account(
            name="Nonexistent:Account",
            description="test",
        )

        data = json.loads(result)
        assert "error" in data


class TestMoveAccountTool:
    """Tests for move_account tool."""

    def test_move_account(self, setup_book_env):
        """Should move an account."""
        # First create a destination
        server_module.create_account(
            name="Daily",
            account_type="EXPENSE",
            parent="Expenses",
            placeholder=True,
        )

        result = server_module.move_account(
            name="Expenses:Groceries",
            new_parent="Expenses:Daily",
        )

        data = json.loads(result)
        assert data["status"] == "moved"
        assert "Daily:Groceries" in data["fullname"]

    def test_move_account_not_found(self, setup_book_env):
        """Should return error for non-existent account."""
        result = server_module.move_account(
            name="Nonexistent:Account",
            new_parent="Expenses",
        )

        data = json.loads(result)
        assert "error" in data


class TestDeleteAccountTool:
    """Tests for delete_account tool."""

    def test_delete_account(self, setup_book_env):
        """Should delete an empty account."""
        # Create an account to delete
        server_module.create_account(
            name="ToDelete",
            account_type="EXPENSE",
            parent="Expenses",
        )

        result = server_module.delete_account("Expenses:ToDelete")

        data = json.loads(result)
        assert data["status"] == "deleted"

    def test_delete_account_with_children(self, setup_book_env):
        """Should return error if account has children."""
        result = server_module.delete_account("Expenses")

        data = json.loads(result)
        assert "error" in data
        assert "children" in data["error"].lower()


class TestDeleteTransactionTool:
    """Tests for delete_transaction tool."""

    def test_delete_transaction(self, setup_book_env):
        """Should delete a transaction and return result."""
        # First get a transaction to delete
        transactions = json.loads(server_module.list_transactions(verbose=True))
        guid = transactions[0]["guid"]

        result = server_module.delete_transaction(guid)

        data = json.loads(result)
        assert data["status"] == "deleted"
        assert data["guid"] == guid

        # Verify it's gone
        get_result = server_module.get_transaction(guid)
        assert "error" in json.loads(get_result)

    def test_delete_reconciled_rejected(self, setup_book_env):
        """Should reject deleting a transaction with reconciled splits."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        split_guid = transactions[0]["splits"][0]["guid"]
        server_module.set_reconcile_state(split_guid, "y")
        guid = transactions[0]["guid"]

        result = server_module.delete_transaction(guid)

        data = json.loads(result)
        assert "error" in data
        assert "reconciled" in data["error"].lower()

    def test_delete_reconciled_force(self, setup_book_env):
        """Should allow deleting reconciled transaction with force=True."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        split_guid = transactions[0]["splits"][0]["guid"]
        server_module.set_reconcile_state(split_guid, "y")
        guid = transactions[0]["guid"]

        result = server_module.delete_transaction(guid, force=True)

        data = json.loads(result)
        assert data["status"] == "deleted"
        assert data["reconciled_splits_affected"] == 1

    def test_delete_nonexistent_transaction(self, setup_book_env):
        """Should return error for missing transaction."""
        result = server_module.delete_transaction("nonexistent_guid_12345")

        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()


class TestUpdateTransactionTool:
    """Tests for update_transaction tool."""

    def test_update_description(self, setup_book_env):
        """Should update transaction description."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        guid = transactions[0]["guid"]

        result = server_module.update_transaction(
            guid=guid,
            description="Updated Description",
        )

        data = json.loads(result)
        assert data["status"] == "updated"
        assert data["description"] == "Updated Description"

    def test_update_date(self, setup_book_env):
        """Should update transaction date."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        guid = transactions[0]["guid"]

        result = server_module.update_transaction(
            guid=guid,
            transaction_date="2024-06-15",
        )

        data = json.loads(result)
        assert data["status"] == "updated"
        assert data["date"] == "2024-06-15"

    def test_update_splits(self, setup_book_env):
        """Should update transaction split amounts."""
        # Find the groceries transaction which has known accounts
        transactions = json.loads(server_module.list_transactions(verbose=True))
        groceries_trans = next(
            t for t in transactions if t["description"] == "Weekly Groceries"
        )
        guid = groceries_trans["guid"]

        result = server_module.update_transaction(
            guid=guid,
            splits=[
                {"account": "Assets:Checking", "amount": "-200.00"},
                {"account": "Expenses:Groceries", "amount": "200.00"},
            ],
        )

        data = json.loads(result)
        assert data["status"] == "updated"

    def test_update_nonexistent_transaction(self, setup_book_env):
        """Should return error for missing transaction."""
        result = server_module.update_transaction(
            guid="nonexistent_guid_12345",
            description="New Description",
        )

        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_update_unbalanced_splits(self, setup_book_env):
        """Should return error for unbalanced splits."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        guid = transactions[0]["guid"]

        result = server_module.update_transaction(
            guid=guid,
            splits=[
                {"account": "Assets:Checking", "amount": "-100.00"},
                {"account": "Expenses:Groceries", "amount": "50.00"},
            ],
        )

        data = json.loads(result)
        assert "error" in data
        assert "balance" in data["error"].lower()

    def test_update_reconciled_splits_rejected(self, setup_book_env):
        """Should reject updating splits on a reconciled transaction."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        groceries_trans = next(
            t for t in transactions if t["description"] == "Weekly Groceries"
        )
        guid = groceries_trans["guid"]
        split_guid = groceries_trans["splits"][0]["guid"]
        server_module.set_reconcile_state(split_guid, "y")

        result = server_module.update_transaction(
            guid=guid,
            splits=[
                {"account": "Assets:Checking", "amount": "-200.00"},
                {"account": "Expenses:Groceries", "amount": "200.00"},
            ],
        )

        data = json.loads(result)
        assert "error" in data
        assert "reconciled" in data["error"].lower()

    def test_update_reconciled_force(self, setup_book_env):
        """Should allow updating reconciled splits with force=True."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        groceries_trans = next(
            t for t in transactions if t["description"] == "Weekly Groceries"
        )
        guid = groceries_trans["guid"]
        split_guid = groceries_trans["splits"][0]["guid"]
        server_module.set_reconcile_state(split_guid, "y")

        result = server_module.update_transaction(
            guid=guid,
            splits=[
                {"account": "Assets:Checking", "amount": "-200.00"},
                {"account": "Expenses:Groceries", "amount": "200.00"},
            ],
            force=True,
        )

        data = json.loads(result)
        assert data["status"] == "updated"


class TestReplaceSplitsTool:
    """Tests for replace_splits tool."""

    def test_replace_splits_basic(self, setup_book_env):
        """Should replace splits on a transaction with different accounts."""
        # Create a Dining account first
        server_module.create_account(
            name="Dining",
            account_type="EXPENSE",
            parent="Expenses",
        )

        # Find a transaction to replace splits on
        transactions = json.loads(server_module.list_transactions(verbose=True))
        grocery_txn = next(
            t for t in transactions if "Groceries" in t["description"]
        )
        guid = grocery_txn["guid"]

        # Replace splits
        result = server_module.replace_splits(
            guid=guid,
            splits=[
                {"account": "Expenses:Dining", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
        )

        data = json.loads(result)
        assert data["status"] == "splits_replaced"
        accounts = {s["account"] for s in data["splits"]}
        assert "Expenses:Dining" in accounts
        assert "Expenses:Groceries" not in accounts

    def test_replace_splits_returns_previous_splits(self, setup_book_env):
        """Should include previous_splits for audit trail."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        grocery_txn = next(
            t for t in transactions if "Groceries" in t["description"]
        )
        guid = grocery_txn["guid"]

        result = server_module.replace_splits(
            guid=guid,
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
        )

        data = json.loads(result)
        assert "previous_splits" in data
        assert len(data["previous_splits"]) == 2

    def test_replace_splits_unbalanced_error(self, setup_book_env):
        """Should return error for unbalanced splits."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        guid = transactions[0]["guid"]

        result = server_module.replace_splits(
            guid=guid,
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-100.00"},
            ],
        )

        data = json.loads(result)
        assert "error" in data
        assert "balance" in data["error"].lower()

    def test_replace_splits_placeholder_error(self, setup_book_env):
        """Should return error for placeholder account."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        guid = transactions[0]["guid"]

        result = server_module.replace_splits(
            guid=guid,
            splits=[
                {"account": "Expenses", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
        )

        data = json.loads(result)
        assert "error" in data
        assert "placeholder" in data["error"].lower()


class TestSetReconcileStateTool:
    """Tests for set_reconcile_state tool."""

    def test_set_reconcile_state(self, setup_book_env):
        """Should set reconcile state on a split."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        split_guid = transactions[0]["splits"][0]["guid"]

        result = server_module.set_reconcile_state(split_guid, "c")

        data = json.loads(result)
        assert data["status"] == "updated"
        assert data["reconcile_state"] == "c"

    def test_set_reconcile_state_invalid(self, setup_book_env):
        """Should return error for invalid state."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        split_guid = transactions[0]["splits"][0]["guid"]

        result = server_module.set_reconcile_state(split_guid, "x")

        data = json.loads(result)
        assert "error" in data


class TestGetUnreconciledSplitsTool:
    """Tests for get_unreconciled_splits tool."""

    def test_get_unreconciled_splits(self, setup_book_env):
        """Should return unreconciled splits for account."""
        result = server_module.get_unreconciled_splits("Assets:Checking", verbose=True)

        data = json.loads(result)
        assert "splits" in data
        assert data["account"] == "Assets:Checking"
        assert data["count"] > 0

    def test_get_unreconciled_splits_not_found(self, setup_book_env):
        """Should return error for non-existent account."""
        result = server_module.get_unreconciled_splits("Nonexistent:Account")

        data = json.loads(result)
        assert "error" in data


class TestReconcileAccountTool:
    """Tests for reconcile_account tool."""

    def test_reconcile_account(self, setup_book_env):
        """Should reconcile account when balance matches."""
        from decimal import Decimal

        # Get unreconciled splits
        unreconciled = json.loads(
            server_module.get_unreconciled_splits("Assets:Checking", verbose=True)
        )

        # Calculate expected balance
        total = Decimal("0")
        guids = []
        for split in unreconciled["splits"]:
            total += Decimal(split["amount"])
            guids.append(split["guid"])

        result = server_module.reconcile_account(
            account="Assets:Checking",
            statement_date="2024-01-31",
            statement_balance=str(total),
            split_guids=guids,
        )

        data = json.loads(result)
        assert data["status"] == "reconciled"

    def test_reconcile_account_balance_mismatch(self, setup_book_env):
        """Should return error when balance doesn't match."""
        unreconciled = json.loads(
            server_module.get_unreconciled_splits("Assets:Checking", verbose=True)
        )
        guids = [s["guid"] for s in unreconciled["splits"]]

        result = server_module.reconcile_account(
            account="Assets:Checking",
            statement_date="2024-01-31",
            statement_balance="9999999.99",
            split_guids=guids,
        )

        data = json.loads(result)
        assert "error" in data
        assert "mismatch" in data["error"].lower()


class TestVoidTransactionTool:
    """Tests for void_transaction tool."""

    def test_void_transaction(self, setup_book_env):
        """Should void a transaction."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        guid = transactions[0]["guid"]

        result = server_module.void_transaction(guid, "Test void reason")

        data = json.loads(result)
        assert data["status"] == "voided"
        assert data["void_reason"] == "Test void reason"

    def test_void_transaction_no_reason(self, setup_book_env):
        """Should return error if no reason provided."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        guid = transactions[0]["guid"]

        result = server_module.void_transaction(guid, "")

        data = json.loads(result)
        assert "error" in data


class TestUnvoidTransactionTool:
    """Tests for unvoid_transaction tool."""

    def test_unvoid_transaction(self, setup_book_env):
        """Should restore a voided transaction."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        guid = transactions[0]["guid"]

        # Void first
        server_module.void_transaction(guid, "Test void")

        # Then unvoid
        result = server_module.unvoid_transaction(guid)

        data = json.loads(result)
        assert data["status"] == "unvoided"

    def test_unvoid_not_voided(self, setup_book_env):
        """Should return error if transaction not voided."""
        transactions = json.loads(server_module.list_transactions(verbose=True))
        guid = transactions[0]["guid"]

        result = server_module.unvoid_transaction(guid)

        data = json.loads(result)
        assert "error" in data
        assert "not voided" in data["error"].lower()


class TestResources:
    """Tests for MCP resources."""

    def test_read_accounts_resource(self, setup_book_env):
        """Should read accounts resource."""
        content = server_module.accounts_resource()

        data = json.loads(content)
        assert isinstance(data, list)
        fullnames = {a["fullname"] for a in data}
        assert "Assets:Checking" in fullnames


class TestErrorHandling:
    """Tests for error handling and the safe_tool decorator."""

    def test_missing_env_variable(self, monkeypatch):
        """Should return error when GNUCASH_BOOK_PATH is not set."""
        monkeypatch.delenv("GNUCASH_BOOK_PATH", raising=False)
        server_module._book = None

        result = server_module.list_accounts()

        data = json.loads(result)
        assert "error" in data
        assert "error_type" in data
        assert data["error_type"] == "validation_error"
        assert "GNUCASH_BOOK_PATH" in data["error"]

    def test_file_not_found(self, monkeypatch):
        """Should return error when book file doesn't exist."""
        monkeypatch.setenv("GNUCASH_BOOK_PATH", "/nonexistent/path/book.gnucash")
        server_module._book = None

        result = server_module.list_accounts()

        data = json.loads(result)
        assert "error" in data
        assert data["error_type"] == "file_not_found"
        assert "suggestion" in data

    def test_invalid_date_format(self, setup_book_env):
        """Should return error for invalid date format."""
        result = server_module.get_balance("Assets:Checking", "not-a-date")

        data = json.loads(result)
        assert "error" in data
        # ValueError from date parsing should be caught
        assert "error_type" in data

    def test_invalid_amount_query(self, setup_book_env):
        """Should return error for invalid amount query."""
        result = server_module.search_transactions(">abc", field="amount")

        data = json.loads(result)
        assert "error" in data
        assert "Invalid amount query" in data["error"]

    def test_lock_error_handling(self, setup_book_env):
        """Should handle lock errors gracefully."""
        from gnucash_mcp.book import GnuCashLockError

        # Mock the book's list_accounts to raise GnuCashLockError
        with patch.object(
            server_module.get_book(),
            "list_accounts",
            side_effect=GnuCashLockError("Book is locked by another process"),
        ):
            result = server_module.list_accounts()

        data = json.loads(result)
        assert "error" in data
        assert data["error_type"] == "lock_error"
        assert "suggestion" in data
        assert "Close GnuCash" in data["suggestion"]

    def test_unexpected_error_handling(self, setup_book_env):
        """Should handle unexpected errors gracefully."""
        # Patch to raise an unexpected error
        with patch.object(
            server_module,
            "get_book",
            side_effect=RuntimeError("Unexpected error"),
        ):
            result = server_module.list_accounts()

        data = json.loads(result)
        assert "error" in data
        assert data["error_type"] == "unexpected_error"
        assert "RuntimeError" in data["error"]


class TestSpendingByCategoryTool:
    """Tests for spending_by_category tool."""

    def test_spending_by_category(self, setup_book_env):
        """Should return spending breakdown."""
        result = server_module.spending_by_category(
            start_date="2024-01-01",
            end_date="2024-12-31",
        )

        data = json.loads(result)
        assert "total" in data
        assert "categories" in data


class TestIncomeBySourcTool:
    """Tests for income_by_source tool."""

    def test_income_by_source(self, setup_book_env):
        """Should return income breakdown."""
        result = server_module.income_by_source(
            start_date="2024-01-01",
            end_date="2024-12-31",
        )

        data = json.loads(result)
        assert "total" in data
        assert "sources" in data


class TestBalanceSheetTool:
    """Tests for balance_sheet tool."""

    def test_balance_sheet(self, setup_book_env):
        """Should return balance sheet."""
        result = server_module.balance_sheet(as_of_date="2024-12-31")

        data = json.loads(result)
        assert "assets" in data
        assert "liabilities" in data
        assert "equity" in data


class TestNetWorthTool:
    """Tests for net_worth tool."""

    def test_net_worth_point_in_time(self, setup_book_env):
        """Should calculate net worth at a point in time."""
        result = server_module.net_worth(end_date="2024-12-31")

        data = json.loads(result)
        assert "net_worth" in data

    def test_net_worth_time_series(self, setup_book_env):
        """Should calculate net worth time series."""
        result = server_module.net_worth(
            start_date="2024-01-01",
            end_date="2024-12-31",
            interval="month",
        )

        data = json.loads(result)
        assert "series" in data


class TestCashFlowTool:
    """Tests for cash_flow tool."""

    def test_cash_flow(self, setup_book_env):
        """Should calculate cash flow."""
        result = server_module.cash_flow(
            start_date="2024-01-01",
            end_date="2024-12-31",
        )

        data = json.loads(result)
        assert "inflows" in data
        assert "outflows" in data
        assert "net" in data


class TestListCommoditiesTool:
    """Tests for list_commodities tool."""

    def test_list_commodities(self, setup_book_env):
        """Should return commodities grouped by namespace."""
        result = server_module.list_commodities(verbose=True)

        data = json.loads(result)
        assert "default_currency" in data
        assert data["default_currency"] == "USD"
        assert "commodities" in data
        assert "CURRENCY" in data["commodities"]

    def test_list_commodities_includes_usd(self, setup_book_env):
        """Should include USD in currency commodities."""
        result = server_module.list_commodities(verbose=True)

        data = json.loads(result)
        mnemonics = [c["mnemonic"] for c in data["commodities"]["CURRENCY"]]
        assert "USD" in mnemonics


class TestCreateAccountWithCommodityTool:
    """Tests for create_account tool with commodity parameter."""

    def test_create_account_with_eur(self, setup_book_env):
        """Should create account with EUR commodity."""
        result = server_module.create_account(
            name="Euro Account",
            account_type="BANK",
            parent="Assets",
            commodity="EUR",
        )

        data = json.loads(result)
        assert data["status"] == "created"

        # Verify account has EUR commodity
        account = json.loads(server_module.get_account("Assets:Euro Account"))
        assert account["commodity"] == "EUR"

    def test_create_account_default_commodity(self, setup_book_env):
        """Should default to book's currency when no commodity specified."""
        result = server_module.create_account(
            name="Default Account",
            account_type="BANK",
            parent="Assets",
        )

        data = json.loads(result)
        assert data["status"] == "created"

        account = json.loads(server_module.get_account("Assets:Default Account"))
        assert account["commodity"] == "USD"

    def test_create_account_invalid_commodity(self, setup_book_env):
        """Should return error for invalid commodity."""
        result = server_module.create_account(
            name="Bad",
            account_type="BANK",
            parent="Assets",
            commodity="INVALID",
        )

        data = json.loads(result)
        assert "error" in data


class TestCreateTransactionMultiCurrencyTool:
    """Tests for create_transaction tool with multi-currency support."""

    def test_create_transaction_with_currency(self, setup_book_env):
        """Should create transaction with explicit currency."""
        result = server_module.create_transaction(
            description="USD Transaction",
            splits=[
                {"account": "Expenses:Groceries", "amount": "25.00"},
                {"account": "Assets:Checking", "amount": "-25.00"},
            ],
            currency="USD",
        )

        data = json.loads(result)
        assert data["status"] == "created"

    def test_create_cross_currency_transaction(self, setup_book_env):
        """Should create cross-currency transaction via tool."""
        # Create EUR account first
        server_module.create_account(
            name="EUR Card",
            account_type="CREDIT",
            parent="Liabilities",
            commodity="EUR",
        )

        result = server_module.create_transaction(
            description="Paris Dinner",
            currency="USD",
            splits=[
                {"account": "Expenses:Groceries", "amount": "55.00"},
                {
                    "account": "Liabilities:EUR Card",
                    "amount": "-55.00",
                    "quantity": "-50.00",
                },
            ],
            transaction_date="2024-03-01",
        )

        data = json.loads(result)
        assert data["status"] == "created"
        assert "guid" in data

    def test_create_cross_currency_missing_quantity(self, setup_book_env):
        """Should return error when quantity missing for cross-currency."""
        # Create EUR account
        server_module.create_account(
            name="EUR Checking",
            account_type="BANK",
            parent="Assets",
            commodity="EUR",
        )

        result = server_module.create_transaction(
            description="Missing Quantity",
            currency="USD",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:EUR Checking", "amount": "-50.00"},
            ],
        )

        data = json.loads(result)
        assert "error" in data
        assert "quantity" in data["error"].lower()

    def test_create_transaction_backward_compatible(self, setup_book_env):
        """Should work without currency or quantity (backward compatible)."""
        result = server_module.create_transaction(
            description="Old Style",
            splits=[
                {"account": "Expenses:Groceries", "amount": "40.00"},
                {"account": "Assets:Checking", "amount": "-40.00"},
            ],
        )

        data = json.loads(result)
        assert data["status"] == "created"
