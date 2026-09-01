"""Tests for MCP server tools and resources."""

import inspect
import json
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from gnucash_mcp import server as server_module


@pytest.fixture(scope="module", autouse=True)
def _load_all_extracted_tool_modules():
    """Force-load all extracted tool modules and bind their tools to the server module.

    After the modularization refactor, most tool wrappers live inside
    register() closures in gnucash_mcp/tools/<module>.py and only appear
    in the FastMCP registry when --modules enables them. Tests in this
    file call tools directly via ``server_module.<tool_name>(...)``, so
    we force-load all modules and bind each registered tool's underlying
    function back onto the server module namespace.

    Teardown removes the tools we added from the FastMCP registry AND
    resets ``_loaded_tool_files`` so later test modules (notably
    test_modules.py) see a clean state where extracted tools are absent
    at import — preserving the lazy-loading assertions. Without the
    lazy-load reset, subsequent ``_apply_module_filter`` calls in other
    test modules would become no-ops while the registry sits empty.
    """
    pre_tools = dict(server_module.mcp._tool_manager._tools)
    server_module._reset_lazy_load_state()
    server_module._apply_module_filter("all")
    for name, tool in server_module.mcp._tool_manager._tools.items():
        if not hasattr(server_module, name):
            setattr(server_module, name, tool.fn)
    yield
    # Restore registry to the pre-fixture state (setattr bindings stay;
    # they're harmless module attributes no other test inspects)
    added = set(server_module.mcp._tool_manager._tools.keys()) - set(pre_tools.keys())
    for name in added:
        del server_module.mcp._tool_manager._tools[name]
    server_module._reset_lazy_load_state()


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
        # The bottom-line "Net worth:" line was retired in favor of
        # the trajectory section's "now" anchor — single source of
        # truth for the net-worth number.
        assert "Net worth trajectory:" in result


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
        """verbose=True should return the paginated envelope."""
        result = server_module.list_accounts(verbose=True)

        data = json.loads(result)
        assert isinstance(data, dict)
        assert data["showing"].startswith("Showing 1-")
        accounts = data["accounts"]
        assert len(accounts) > 0
        fullnames = {a["fullname"] for a in accounts}
        assert "Assets" in fullnames
        assert "Assets:Checking" in fullnames

    def test_list_accounts_root_filter(self, setup_book_env):
        """root parameter should filter accounts."""
        result = server_module.list_accounts(root="Expenses")
        # Skip the leading "Showing X-Y of Z accounts" indicator.
        lines = result.strip().split("\n")[1:]
        for line in lines:
            # Compact line format: '%shortguid<TAB>fullname [ANNOTATION]'.
            # Pull the path portion off before checking the prefix.
            path = line.split("\t", 1)[1] if "\t" in line else line
            assert path.startswith("Expenses")

    def test_list_accounts_root_with_verbose(self, setup_book_env):
        """root + verbose should return filtered JSON."""
        result = server_module.list_accounts(root="Assets", verbose=True)
        data = json.loads(result)
        for a in data["accounts"]:
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
        """Should return current balance with today's date as the resolved cutoff."""
        from datetime import date as date_cls

        result = server_module.get_balance("Assets:Checking")

        data = json.loads(result)
        assert data["account"] == "Assets:Checking"
        assert data["balance"] == "2850"
        # Resolved date is today's ISO string — future-dated transactions
        # would be excluded from the cutoff.
        assert data["as_of_date"] == date_cls.today().isoformat()

    def test_get_balance_as_of_date(self, setup_book_env):
        """Should return balance as of date."""
        result = server_module.get_balance("Assets:Checking", "2024-01-10")

        data = json.loads(result)
        assert data["balance"] == "1000"
        assert data["as_of_date"] == "2024-01-10"

    def test_get_balance_short_guid_input_echoes_canonical(
        self, setup_book_env
    ):
        """When called with a %short GUID, the response echoes the
        canonical full path — not the input string.

        Locks the bookkeeper-flagged inconsistency: prior to this fix,
        get_balance echoed whatever came in (so %xxxxxxx flowed back
        into the response), making it the odd one out among the tools
        that always returned the readable path.
        """
        # Find Checking's short GUID by pulling list_accounts and
        # picking the line with our path.
        listing = server_module.list_accounts()
        checking_line = next(
            line for line in listing.split("\n")
            if "Assets:Checking" in line and "[BANK]" in line
        )
        short = checking_line.split("\t", 1)[0]
        assert short.startswith("%")

        result = server_module.get_balance(short)
        data = json.loads(result)
        assert data["account"] == "Assets:Checking", (
            f"expected canonical fullname echo, got {data['account']!r}"
        )
        assert data["balance"] == "2850"


class TestListTransactionsTool:
    """Tests for list_transactions tool."""

    def test_list_all_transactions(self, setup_book_env):
        """Should return all transactions in a paginated envelope."""
        result = server_module.list_transactions(verbose=True)

        data = json.loads(result)
        assert isinstance(data, dict)
        assert len(data["transactions"]) == 3
        assert data["total"] == 3
        assert data["offset"] == 0
        assert data["showing"] == (
            "Showing 1-3 of 3 transactions (2024-01-01 to 2024-01-20)"
        )

    def test_list_transactions_by_account(self, setup_book_env):
        """Should filter by account."""
        result = server_module.list_transactions(account="Expenses:Groceries", verbose=True)

        data = json.loads(result)
        assert data["total"] == 1
        assert len(data["transactions"]) == 1
        assert data["transactions"][0]["description"] == "Weekly Groceries"

    def test_list_transactions_date_range(self, setup_book_env):
        """Should filter by date range."""
        result = server_module.list_transactions(
            start_date="2024-01-10", end_date="2024-01-18", verbose=True
        )

        data = json.loads(result)
        assert data["total"] == 1
        assert data["transactions"][0]["description"] == "Salary Deposit"

    def test_list_transactions_offset_pages(self, setup_book_env):
        """offset should page through the result set."""
        page2 = json.loads(
            server_module.list_transactions(limit=2, offset=2, verbose=True)
        )
        assert page2["offset"] == 2
        assert page2["total"] == 3
        assert len(page2["transactions"]) == 1
        assert page2["showing"].startswith("Showing 3-3 of 3 transactions")

    def test_list_transactions_count_only(self, setup_book_env):
        """limit=0 returns the count without rows."""
        data = json.loads(server_module.list_transactions(limit=0, verbose=True))
        assert data["transactions"] == []
        assert data["total"] == 3
        assert data["showing"].startswith("Showing 0 of 3 transactions")


class TestGetTransactionTool:
    """Tests for get_transaction tool."""

    def test_get_nonexistent_transaction(self, setup_book_env):
        """Should return error for missing transaction."""
        result = server_module.get_transaction("deadbeef00000000")

        data = json.loads(result)
        assert "error" in data


class TestSearchTransactionsTool:
    """Tests for search_transactions tool."""

    def test_search_by_description(self, setup_book_env):
        """Should find transactions by description."""
        result = server_module.search_transactions("Salary", verbose=True)

        data = json.loads(result)
        assert data["total"] == 1
        assert data["transactions"][0]["description"] == "Salary Deposit"

    def test_search_by_amount(self, setup_book_env):
        """Should find transactions by amount range."""
        result = server_module.search_transactions(">500", field="amount", verbose=True)

        data = json.loads(result)
        # Opening Balance (1000) and Salary (2000)
        assert data["total"] == 2
        assert len(data["transactions"]) == 2


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
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
        guid = transactions[0]["guid"]

        result = server_module.delete_transaction(guid)

        data = json.loads(result)
        assert data["status"] == "deleted"
        # Response is a short collision-safe prefix of the full guid.
        assert guid.startswith(data["guid"])

        # Verify it's gone
        get_result = server_module.get_transaction(guid)
        assert "error" in json.loads(get_result)

    def test_delete_list_of_guids(self, setup_book_env):
        """A list dispatches to the batch path; response is the
        envelope, and one call removes them all."""
        transactions = json.loads(
            server_module.list_transactions(verbose=True)
        )["transactions"]
        guids = [t["guid"] for t in transactions[:2]]

        result = server_module.delete_transaction(guids)

        data = json.loads(result)
        assert data["status"] == "deleted"
        assert data["count"] == 2
        assert len(data["transactions"]) == 2
        for guid in guids:
            assert "error" in json.loads(
                server_module.get_transaction(guid)
            )

    def test_delete_reconciled_rejected(self, setup_book_env):
        """Should reject deleting a transaction with reconciled splits."""
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
        split_guid = transactions[0]["splits"][0]["guid"]
        server_module.set_reconcile_state(split_guid, "y")
        guid = transactions[0]["guid"]

        result = server_module.delete_transaction(guid)

        data = json.loads(result)
        assert "error" in data
        assert "reconciled" in data["error"].lower()

    def test_delete_reconciled_force(self, setup_book_env):
        """Should allow deleting reconciled transaction with force=True."""
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
        split_guid = transactions[0]["splits"][0]["guid"]
        server_module.set_reconcile_state(split_guid, "y")
        guid = transactions[0]["guid"]

        result = server_module.delete_transaction(guid, force=True)

        data = json.loads(result)
        assert data["status"] == "deleted"
        assert data["reconciled_splits_affected"] == 1

    def test_delete_nonexistent_transaction(self, setup_book_env):
        """Should return error for missing transaction."""
        result = server_module.delete_transaction("deadbeef00000000")

        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()


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
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
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
        # Thin response — no splits echo. Verify via get_transaction.
        assert data["status"] == "splits_replaced"
        refreshed = json.loads(server_module.get_transaction(guid))
        accounts = {s["account"] for s in refreshed["splits"]}
        assert "Expenses:Dining" in accounts
        assert "Expenses:Groceries" not in accounts

    def test_replace_splits_returns_previous_splits(self, setup_book_env):
        """Should include previous_splits for audit trail."""
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
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
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
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
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
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
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
        split_guid = transactions[0]["splits"][0]["guid"]

        result = server_module.set_reconcile_state(split_guid, "c")

        data = json.loads(result)
        # `reconcile_state` echo dropped — verified through read-back.
        assert data["status"] == "updated"
        refreshed = json.loads(server_module.list_transactions(verbose=True))["transactions"]
        updated_split = next(
            s for t in refreshed for s in t["splits"] if s["guid"] == split_guid
        )
        assert updated_split["reconcile_state"] == "c"

    def test_set_reconcile_state_invalid(self, setup_book_env):
        """Should return error for invalid state."""
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
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
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
        guid = transactions[0]["guid"]

        result = server_module.void_transaction(guid, "Test void reason")

        data = json.loads(result)
        assert data["status"] == "voided"
        assert data["void_reason"] == "Test void reason"

    def test_void_transaction_no_reason(self, setup_book_env):
        """Should return error if no reason provided."""
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
        guid = transactions[0]["guid"]

        result = server_module.void_transaction(guid, "")

        data = json.loads(result)
        assert "error" in data


class TestUnvoidTransactionTool:
    """Tests for unvoid_transaction tool."""

    def test_unvoid_transaction(self, setup_book_env):
        """Should restore a voided transaction."""
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
        guid = transactions[0]["guid"]

        # Void first
        server_module.void_transaction(guid, "Test void")

        # Then unvoid
        result = server_module.unvoid_transaction(guid)

        data = json.loads(result)
        assert data["status"] == "unvoided"

    def test_unvoid_not_voided(self, setup_book_env):
        """Should return error if transaction not voided."""
        transactions = json.loads(server_module.list_transactions(verbose=True))["transactions"]
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
        """Should handle unexpected errors gracefully.

        Patches the underlying GnuCashBook.list_accounts method rather
        than server_module.get_book — after the modularization refactor,
        tool wrappers are closures in gnucash_mcp/tools/core.py and the
        ``get_book`` they call was captured at register time, so
        ``patch.object(server_module, "get_book", ...)`` no longer
        intercepts it. Patching the book method achieves the same end:
        the tool raises unexpectedly, safe_tool catches it.
        """
        book = server_module.get_book()
        with patch.object(
            type(book),
            "list_accounts",
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
        """Compact (default) returns aligned text table."""
        result = server_module.spending_by_category(
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        # New default is compact text — has TOTAL line.
        assert "TOTAL" in result

    def test_spending_by_category_verbose(self, setup_book_env):
        """Verbose returns the structured dict."""
        result = server_module.spending_by_category(
            start_date="2024-01-01",
            end_date="2024-12-31",
            verbose=True,
        )
        data = json.loads(result)
        assert "total" in data
        assert "categories" in data


class TestIncomeBySourcTool:
    """Tests for income_by_source tool."""

    def test_income_by_source(self, setup_book_env):
        """Compact (default) returns aligned text table."""
        result = server_module.income_by_source(
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert "TOTAL" in result

    def test_income_by_source_verbose(self, setup_book_env):
        """Verbose returns the structured dict."""
        result = server_module.income_by_source(
            start_date="2024-01-01",
            end_date="2024-12-31",
            verbose=True,
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

    def test_balance_sheet_defaults_to_today(self, setup_book_env):
        """Bookkeeper-flagged: cross-tool comparison silently broke
        because balance_sheet required an explicit date while
        get_book_summary used today implicitly. As of v1.3.0,
        balance_sheet defaults to today so the natural side-by-side
        ``balance_sheet()`` vs. ``get_book_summary()`` call agrees
        without threading the same date into both.
        """
        from datetime import date
        # No as_of_date provided.
        result = server_module.balance_sheet()
        data = json.loads(result)
        assert data["as_of_date"] == date.today().isoformat()

    def test_balance_sheet_rejects_empty_string_as_of_date(
        self, setup_book_env,
    ):
        """Copilot-flagged on PR #92 review: empty string is a
        caller bug (probably a stale-template / missing-variable
        substitution), not a "use today" signal. Pre-fix the falsy
        check treated ``as_of_date=""`` the same as ``None`` and
        silently substituted today, producing a wrong-dated
        report the caller had no way to notice. Strict-kwargs
        philosophy extends to the value: empty string → loud
        validation error.
        """
        result = server_module.balance_sheet(as_of_date="")
        data = json.loads(result)
        # safe_tool wraps the ValueError from date.fromisoformat("")
        # into a structured validation_error response.
        assert data.get("error_type") == "validation_error", (
            f"expected validation_error for empty as_of_date, got: "
            f"{data!r}"
        )


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
        # `net` dropped — derivable (inflows - outflows).
        assert "inflows" in data
        assert "outflows" in data


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


class TestNonAsciiJsonEncoding:
    """Bug 1 from the CNY cousin verification: ``_json`` (the
    serializer every tool wrapper uses) was hitting Python's default
    ``json.dumps(ensure_ascii=True)``, escaping non-ASCII characters
    like 贵州茅台 as ``\\uXXXX`` in tool responses. Storage was correct
    (the audit log renders Chinese natively); only the wire format
    was wrong, breaking human readability and downstream substring
    matching.
    """

    def test_chinese_commodity_name_returns_raw_utf8(
        self, setup_book_env,
    ):
        result = server_module.create_commodity(
            mnemonic="600519",
            fullname="贵州茅台 (Kweichow Moutai)",
            namespace="SSE",
            fraction=100,
        )
        # Raw Chinese characters, not \uXXXX escape sequences.
        assert "贵州茅台" in result, (
            f"non-ASCII characters were escaped:\n{result}"
        )
        # Still valid JSON.
        data = json.loads(result)
        assert data["fullname"] == "贵州茅台 (Kweichow Moutai)"

    def test_accented_european_characters_round_trip(
        self, setup_book_env,
    ):
        # Same bug, different alphabet — accented Latin (German
        # umlauts, French accents) gets escaped without ensure_ascii=False.
        result = server_module.create_commodity(
            mnemonic="MÜNCH",
            fullname="Münchener Rückversicherungs-Gesellschaft",
            namespace="XETRA",
            fraction=100,
        )
        assert "Münchener" in result
        assert "Rückversicherungs" in result
        data = json.loads(result)
        assert data["fullname"] == "Münchener Rückversicherungs-Gesellschaft"


class TestStrictToolKwargs:
    """Bookkeeper-found bug from PR #92 review: calling
    ``reconcile_account`` with ``except=[...]`` instead of the
    actual ``except_guids=[...]`` parameter ran the tool with no
    exclusion at all. FastMCP's per-tool Pydantic arg model
    inherited from ``ArgModelBase`` with no ``extra`` config,
    defaulting to ``"ignore"`` — unknown kwargs silently dropped.

    ``server.py`` monkey-patches ``ArgModelBase.model_config`` to
    ``extra="forbid"`` at import time so any unknown kwarg raises
    a clear validation error at the MCP boundary instead of
    surfacing as a downstream balance mismatch.
    """

    def _arg_model_for(self, tool_name: str):
        """Return the Pydantic arg model for a registered tool.

        Tests bind ``tool.fn`` directly to ``server_module`` (see
        the module-scoped ``_load_all_extracted_tool_modules``
        fixture), which bypasses Pydantic validation entirely.
        Grab the arg_model off the FastMCP tool entry to exercise
        the same validation path the MCP wire boundary uses.
        """
        tool = server_module.mcp._tool_manager._tools[tool_name]
        return tool.fn_metadata.arg_model

    def test_unknown_kwarg_is_rejected(self):
        from pydantic import ValidationError

        arg_model = self._arg_model_for("reconcile_account")
        with pytest.raises(ValidationError) as exc_info:
            arg_model.model_validate({
                "account": "Assets:Bank:Checking",
                "statement_date": "2026-04-30",
                "statement_balance": "1000.00",
                # Bookkeeper's typo: ``except`` instead of ``except_guids``.
                "except": ["deadbeef"],
                "reconcile_all": True,
            })
        # Pydantic's standard message for extra="forbid".
        assert "Extra inputs are not permitted" in str(exc_info.value)

    def test_known_kwargs_still_validate(self):
        """Sanity check: rejecting extras must not block legitimate calls."""
        arg_model = self._arg_model_for("reconcile_account")
        # Should not raise — every key is a real parameter.
        arg_model.model_validate({
            "account": "Assets:Bank:Checking",
            "statement_date": "2026-04-30",
            "statement_balance": "1000.00",
            "except_guids": ["deadbeef"],
            "reconcile_all": True,
        })

    def test_forbid_applies_to_all_tools(self):
        """Spot-check a sample of unrelated tools — patch is global,
        not per-tool. If a future FastMCP upgrade breaks the
        ``ArgModelBase`` monkey-patch (e.g. by switching to a
        non-inheriting model factory), this test fails loudly
        across the surface rather than only on one tool.
        """
        from pydantic import ValidationError

        for tool_name in ("get_balance", "create_transactions", "list_accounts"):
            arg_model = self._arg_model_for(tool_name)
            assert arg_model.model_config.get("extra") == "forbid", (
                f"{tool_name} arg model is not strict — extras would be ignored"
            )
            with pytest.raises(ValidationError):
                arg_model.model_validate({"this_is_not_a_real_kwarg": "x"})


# Compact line-1 indicator: "Showing 1-50 of 109 …" or "Showing 0 of 0 …".
_INDICATOR_RE = re.compile(r"^Showing (0|\d+-\d+) of \d+ [a-z]")

# (tool, runnable kwargs against the standard test book, verbose entity
# key). A None key marks a TSV-only tool with no verbose envelope.
_PAGINATED_TOOLS = [
    ("list_accounts", {}, "accounts"),
    ("list_transactions", {}, "transactions"),
    ("search_transactions", {"query": "a"}, "transactions"),
    ("get_unreconciled_splits", {"account": "Assets:Checking"}, "splits"),
    ("list_parties", {"party_type": "customer"}, "customers"),
    ("list_parties", {"party_type": "vendor"}, "vendors"),
    ("list_parties", {"party_type": "employee"}, "employees"),
    ("list_billterms", {}, "billterms"),
    ("list_taxtables", {}, "taxtables"),
    ("list_documents", {}, "invoices"),
    ("list_jobs", {}, "jobs"),
    ("get_outstanding_documents", {}, "invoices"),
    ("list_scheduled_transactions", {}, "scheduled_transactions"),
    ("get_upcoming_transactions", {}, "upcoming_transactions"),
    ("list_commodities", {}, "commodities"),
    ("get_prices", {"commodity": "USD", "namespace": "CURRENCY"}, "prices"),
    ("list_lots", {"account": "Assets:Checking"}, "lots"),
    ("list_budgets", {}, "budgets"),
    ("get_audit_log", {}, None),
    ("get_reconciliation_status", {}, None),
]


class TestPaginationCoverage:
    """Contract lock for the pagination rollout: every list-returning
    tool leads with a ``Showing X-Y of Z`` indicator, accepts
    ``offset``/``limit``, and (where it has a verbose mode) returns the
    uniform envelope. Mirrors TestToolFileVsModulesMapping — adding a
    list tool without pagination should fail loudly here."""

    @pytest.mark.parametrize(
        "tool_name", [t[0] for t in _PAGINATED_TOOLS]
    )
    def test_tool_accepts_offset_and_limit(self, tool_name):
        fn = getattr(server_module, tool_name)
        params = inspect.signature(fn).parameters
        assert "offset" in params, f"{tool_name} missing offset param"
        assert "limit" in params, f"{tool_name} missing limit param"

    @pytest.mark.parametrize(
        "tool_name,kwargs", [(t[0], t[1]) for t in _PAGINATED_TOOLS]
    )
    def test_compact_leads_with_indicator(
        self, setup_book_env, tool_name, kwargs
    ):
        result = getattr(server_module, tool_name)(**kwargs)
        line0 = result.split("\n")[0]
        # get_audit_log can't reach its indicator in this fixture: no
        # log dir is configured, so it returns the not-initialized /
        # no-file sentinel. Its indicator format is locked by the book
        # path that DOES have entries; here we only assert it doesn't
        # masquerade as a row list.
        if tool_name == "get_audit_log" and not line0.startswith("Showing"):
            assert "audit log" in line0.lower() or "error" in line0.lower()
            return
        assert _INDICATOR_RE.match(line0), (
            f"{tool_name} compact line 0 is not an indicator: {line0!r}"
        )

    @pytest.mark.parametrize(
        "tool_name,kwargs,key",
        [(t[0], t[1], t[2]) for t in _PAGINATED_TOOLS if t[2]],
    )
    def test_verbose_returns_uniform_envelope(
        self, setup_book_env, tool_name, kwargs, key
    ):
        result = getattr(server_module, tool_name)(verbose=True, **kwargs)
        data = json.loads(result)
        assert isinstance(data, dict), f"{tool_name} verbose is not a dict"
        for field in ("showing", "total", "offset", "count"):
            assert field in data, f"{tool_name} verbose missing {field!r}"
        assert _INDICATOR_RE.match(data["showing"]), (
            f"{tool_name} 'showing' is not an indicator: {data['showing']!r}"
        )
        assert key in data, f"{tool_name} verbose missing entity key {key!r}"

    def test_count_only_mode(self, setup_book_env):
        """limit=0 yields a zero-page indicator with the full total."""
        result = server_module.list_transactions(limit=0)
        assert result.startswith("Showing 0 of 3 transactions")


class TestConsolidatedBusinessSurface:
    """Behavior locks for the 48→27 consolidation (v1.5.0): routing
    correctness, the per-type ID-collision trap, employee-notes
    rejection, credit-note party_type requirement, the merged read
    patterns, and a full voucher lifecycle through the new names —
    the legacy-parity proof that every old workflow is reachable."""

    def test_create_party_routes_and_types(self, setup_book_env):
        c = json.loads(server_module.create_party(
            party_type="customer", name="Collision Co",
        ))
        v = json.loads(server_module.create_party(
            party_type="vendor", name="Collision Supplies",
        ))
        assert c["type"] == "customer" and v["type"] == "vendor"
        # Per-type counters collide by design — same ID, two parties.
        assert c["id"] == v["id"]
        got_c = json.loads(server_module.get_party(
            party_type="customer", id=c["id"],
        ))
        got_v = json.loads(server_module.get_party(
            party_type="vendor", id=v["id"],
        ))
        assert got_c["name"] == "Collision Co"
        assert got_v["name"] == "Collision Supplies"

    def test_create_party_employee_rejects_notes(self, setup_book_env):
        result = json.loads(server_module.create_party(
            party_type="employee", name="Jane Smith",
            notes="no such field",
        ))
        assert "no notes field" in result.get("error", "")

    def test_update_party_employee_rejects_notes(self, setup_book_env):
        e = json.loads(server_module.create_party(
            party_type="employee", name="Jan Novak",
        ))
        result = json.loads(server_module.update_party(
            party_type="employee", id=e["id"], notes="nope",
        ))
        assert "no notes field" in result.get("error", "")

    def test_create_document_credit_note_requires_party_type(
        self, setup_book_env,
    ):
        c = json.loads(server_module.create_party(
            party_type="customer", name="CN Customer",
        ))
        result = json.loads(server_module.create_document(
            document_type="credit_note", owner_id=c["id"],
        ))
        assert "party_type" in result.get("error", "")

    def test_list_parties_all_three_sections(self, setup_book_env):
        server_module.create_party(party_type="customer", name="C1")
        server_module.create_party(party_type="vendor", name="V1")
        server_module.create_party(party_type="employee", name="E1")
        out = server_module.list_parties()
        assert "CUSTOMERS:" in out and "VENDORS:" in out \
            and "EMPLOYEES:" in out

    def test_list_taxtables_name_lookup(self, setup_book_env):
        created = json.loads(server_module.create_taxtable(
            name="Sales Tax",
            entries=[{"account": "Liabilities",
                      "amount": "8.5", "type": "percentage"}],
        ))
        assert created.get("error") is None, created
        got = json.loads(server_module.list_taxtables(name="Sales Tax"))
        assert got["name"] == "Sales Tax"

    def test_voucher_lifecycle_via_new_names(self, setup_book_env):
        """The legacy-parity ride: employee → voucher → entry →
        post → rehearse payment → pay → outstanding shows none."""
        server_module.create_account(
            name="Accounts Payable", account_type="PAYABLE",
            parent="Liabilities",
        )
        e = json.loads(server_module.create_party(
            party_type="employee", name="Alex Reimbursee",
        ))
        doc = json.loads(server_module.create_document(
            document_type="voucher", owner_id=e["id"],
        ))
        assert doc["type"] == "voucher"
        entry = json.loads(server_module.add_document_entry(
            document_type="voucher", id=doc["id"],
            account="Expenses:Groceries",
            description="Conference travel", quantity="1",
            price="240.00",
        ))
        assert entry.get("error") is None, entry
        posted = json.loads(server_module.post_document(
            id=doc["id"], document_type="voucher",
            post_account="Liabilities:Accounts Payable",
        ))
        assert posted.get("error") is None, posted
        assert posted["status"] == "posted"
        rehearsal = json.loads(server_module.pay_document(
            id=doc["id"], document_type="voucher",
            payment_account="Assets:Checking", amount="240.00",
            dry_run=True,
        ))
        assert rehearsal["status"] == "would_pay"
        assert rehearsal["would_close_lot"] is True
        paid = json.loads(server_module.pay_document(
            id=doc["id"], document_type="voucher",
            payment_account="Assets:Checking", amount="240.00",
        ))
        assert paid["status"] == "paid"
        assert float(paid["remaining_balance"]) == 0.0

    def test_delete_document_unposted_invoice(self, setup_book_env):
        c = json.loads(server_module.create_party(
            party_type="customer", name="Ephemeral LLC",
        ))
        doc = json.loads(server_module.create_document(
            document_type="invoice", owner_id=c["id"],
        ))
        result = json.loads(server_module.delete_document(
            document_type="invoice", id=doc["id"],
        ))
        assert result["status"] == "deleted"

    def test_list_documents_doc_type_filter(self, setup_book_env):
        c = json.loads(server_module.create_party(
            party_type="customer", name="Filter Co",
        ))
        server_module.create_document(
            document_type="invoice", owner_id=c["id"],
        )
        out = server_module.list_documents(
            document_type="credit_note",
        )
        assert "Showing 0" in out.split("\n")[0]
        out = server_module.list_documents(document_type="invoice")
        assert "Showing 1-1 of 1" in out.split("\n")[0]


class TestCanonicalBatchToolsWire:
    """Tool-layer coverage for the canonical entry/update pair,
    replacing the removed singular tools' wire tests. Logic depth
    lives at the book layer; these lock the JSON/TSV envelopes."""

    def test_create_transactions_one_row(self, setup_book_env):
        result = server_module.create_transactions(
            transactions=(
                "ref\tdate\tdescription\tamt1\tacct1\tamt2\tacct2\n"
                "1\t2024-02-01\tWire Test\t-25.00\tAssets:Checking"
                "\t25.00\tExpenses:Groceries\n"
            ),
        )
        data = json.loads(result)
        rows = data["results"].splitlines()
        assert rows[0].startswith("ref\t")
        assert "\tcreated\t" in rows[1]

    def test_create_transactions_dry_run_envelope(self, setup_book_env):
        result = server_module.create_transactions(
            transactions=(
                "ref\tdate\tdescription\tamt1\tacct1\tamt2\tacct2\n"
                "1\t2024-03-01\tRehearsal Row\t-75.00\tAssets:Checking"
                "\t75.00\tExpenses:Groceries\n"
            ),
            dry_run=True,
        )
        data = json.loads(result)
        assert "would_create" in data["results"]
        assert "summary" in data  # the rehearsal header (ruling 7)

    def test_create_transactions_unbalanced_row_rejects(
        self, setup_book_env,
    ):
        result = server_module.create_transactions(
            transactions=(
                "ref\tdate\tdescription\tamt1\tacct1\tamt2\tacct2\n"
                "1\t2024-02-01\tUnbalanced\t-40.00\tAssets:Checking"
                "\t50.00\tExpenses:Groceries\n"
            ),
        )
        data = json.loads(result)
        assert "rejected" in data["results"]

    def test_update_transactions_annotates_and_clears(
        self, setup_book_env,
    ):
        created = json.loads(server_module.create_transactions(
            transactions=(
                "ref\tdate\tdescription\tamt1\tacct1\tamt2\tacct2\n"
                "1\t2024-02-02\tAnnotate Me\t-9.00\tAssets:Checking"
                "\t9.00\tExpenses:Groceries\n"
            ),
        ))
        guid = created["results"].splitlines()[1].split("\t")[2]
        upd = json.loads(server_module.update_transactions(
            updates=f"guid\tnotes\n{guid}\ttemp note\n",
        ))
        assert "updated" in upd["results"]
        cleared = json.loads(server_module.update_transactions(
            updates=f"guid\tclear\n{guid}\tnotes\n",
        ))
        assert "updated" in cleared["results"]
        txn = json.loads(server_module.get_transaction(guid=guid))
        assert not txn.get("notes")
