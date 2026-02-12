"""Tests for GnuCashBook wrapper."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook, GnuCashLockError


class TestGnuCashBookInit:
    """Tests for GnuCashBook initialization."""

    def test_init_with_valid_path(self, test_book: Path):
        """Should initialize successfully with valid book path."""
        book = GnuCashBook(str(test_book))
        assert book.book_path == test_book

    def test_init_with_invalid_path(self):
        """Should raise FileNotFoundError for non-existent path."""
        with pytest.raises(FileNotFoundError):
            GnuCashBook("/nonexistent/path/book.gnucash")


class TestGnuCashBookOpen:
    """Tests for book open context manager."""

    def test_open_readonly(self, test_book: Path):
        """Should open book in readonly mode."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            assert book is not None
            assert book.root_account is not None

    def test_open_readwrite(self, test_book: Path):
        """Should open book in read-write mode."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=False) as book:
            assert book is not None


class TestListAccounts:
    """Tests for list_accounts method."""

    def test_list_accounts_returns_all(self, test_book: Path):
        """Should return all non-root accounts."""
        gc_book = GnuCashBook(str(test_book))
        accounts = gc_book.list_accounts()

        # Should have our test accounts
        fullnames = {a["fullname"] for a in accounts}
        assert "Assets" in fullnames
        assert "Assets:Checking" in fullnames
        assert "Expenses:Groceries" in fullnames
        assert "Income:Salary" in fullnames

    def test_list_accounts_sorted(self, test_book: Path):
        """Should return accounts sorted by fullname."""
        gc_book = GnuCashBook(str(test_book))
        accounts = gc_book.list_accounts()

        fullnames = [a["fullname"] for a in accounts]
        assert fullnames == sorted(fullnames)

    def test_list_accounts_structure(self, test_book: Path):
        """Should return proper account dict structure."""
        gc_book = GnuCashBook(str(test_book))
        accounts = gc_book.list_accounts()

        account = accounts[0]
        assert "guid" in account
        assert "name" in account
        assert "fullname" in account
        assert "type" in account
        assert "commodity" in account
        assert "description" in account
        assert "placeholder" in account


class TestGetAccount:
    """Tests for get_account method."""

    def test_get_existing_account(self, test_book: Path):
        """Should return account details for existing account."""
        gc_book = GnuCashBook(str(test_book))
        account = gc_book.get_account("Assets:Checking")

        assert account is not None
        assert account["name"] == "Checking"
        assert account["fullname"] == "Assets:Checking"
        assert account["type"] == "BANK"

    def test_get_nonexistent_account(self, test_book: Path):
        """Should return None for non-existent account."""
        gc_book = GnuCashBook(str(test_book))
        account = gc_book.get_account("Nonexistent:Account")

        assert account is None


class TestGetBalance:
    """Tests for get_balance method."""

    def test_get_balance_all_time(self, test_book: Path):
        """Should return correct balance for all time."""
        gc_book = GnuCashBook(str(test_book))

        # Checking: +1000 (opening) +2000 (salary) -150 (groceries) = 2850
        balance = gc_book.get_balance("Assets:Checking")
        assert balance == Decimal("2850")

    def test_get_balance_as_of_date(self, test_book: Path):
        """Should return correct balance as of specific date."""
        gc_book = GnuCashBook(str(test_book))

        # As of Jan 10, only opening balance: 1000
        balance = gc_book.get_balance("Assets:Checking", as_of_date=date(2024, 1, 10))
        assert balance == Decimal("1000")

        # As of Jan 15, opening + salary: 3000
        balance = gc_book.get_balance("Assets:Checking", as_of_date=date(2024, 1, 15))
        assert balance == Decimal("3000")

    def test_get_balance_nonexistent_account(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.get_balance("Nonexistent:Account")


class TestListTransactions:
    """Tests for list_transactions method."""

    def test_list_all_transactions(self, test_book: Path):
        """Should return all transactions."""
        gc_book = GnuCashBook(str(test_book))
        transactions = gc_book.list_transactions()

        assert len(transactions) == 3
        descriptions = {t["description"] for t in transactions}
        assert "Opening Balance" in descriptions
        assert "Salary Deposit" in descriptions
        assert "Weekly Groceries" in descriptions

    def test_list_transactions_by_account(self, test_book: Path):
        """Should filter transactions by account."""
        gc_book = GnuCashBook(str(test_book))

        # Groceries account only has one transaction
        transactions = gc_book.list_transactions(account="Expenses:Groceries")
        assert len(transactions) == 1
        assert transactions[0]["description"] == "Weekly Groceries"

    def test_list_transactions_date_range(self, test_book: Path):
        """Should filter transactions by date range."""
        gc_book = GnuCashBook(str(test_book))

        # Only Jan 10-18 should get salary deposit
        transactions = gc_book.list_transactions(
            start_date=date(2024, 1, 10),
            end_date=date(2024, 1, 18),
        )
        assert len(transactions) == 1
        assert transactions[0]["description"] == "Salary Deposit"

    def test_list_transactions_limit(self, test_book: Path):
        """Should respect limit parameter."""
        gc_book = GnuCashBook(str(test_book))
        transactions = gc_book.list_transactions(limit=2)

        assert len(transactions) == 2

    def test_list_transactions_sorted_descending(self, test_book: Path):
        """Should return transactions sorted by date descending."""
        gc_book = GnuCashBook(str(test_book))
        transactions = gc_book.list_transactions()

        dates = [t["date"] for t in transactions]
        assert dates == sorted(dates, reverse=True)

    def test_list_transactions_nonexistent_account(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.list_transactions(account="Nonexistent:Account")


class TestGetTransaction:
    """Tests for get_transaction method."""

    def test_get_existing_transaction(self, test_book: Path):
        """Should return transaction details for existing GUID."""
        gc_book = GnuCashBook(str(test_book))

        # First get a valid GUID
        transactions = gc_book.list_transactions()
        guid = transactions[0]["guid"]

        # Then fetch by GUID
        transaction = gc_book.get_transaction(guid)
        assert transaction is not None
        assert transaction["guid"] == guid
        assert "date" in transaction
        assert "description" in transaction
        assert "splits" in transaction

    def test_get_nonexistent_transaction(self, test_book: Path):
        """Should return None for non-existent GUID."""
        gc_book = GnuCashBook(str(test_book))
        transaction = gc_book.get_transaction("nonexistent_guid_12345")

        assert transaction is None


class TestCreateTransaction:
    """Tests for create_transaction method."""

    def test_create_simple_transaction(self, test_book: Path):
        """Should create a simple two-split transaction."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="Test Transaction",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=date(2024, 2, 1),
        )

        assert result["status"] == "created"
        guid = result["guid"]
        assert len(guid) == 32  # GnuCash GUID format

        # Verify transaction was created
        transaction = gc_book.get_transaction(guid)
        assert transaction["description"] == "Test Transaction"
        assert transaction["date"] == "2024-02-01"
        assert len(transaction["splits"]) == 2

    def test_create_transaction_with_memo(self, test_book: Path):
        """Should create transaction with split memos."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="Transaction with Memo",
            splits=[
                {"account": "Expenses:Groceries", "amount": "25.00", "memo": "Weekly shop"},
                {"account": "Assets:Checking", "amount": "-25.00", "memo": "Debit"},
            ],
        )

        transaction = gc_book.get_transaction(result["guid"])
        memos = {s["memo"] for s in transaction["splits"]}
        assert "Weekly shop" in memos
        assert "Debit" in memos

    def test_create_transaction_unbalanced(self, test_book: Path):
        """Should raise ValueError for unbalanced splits."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="do not balance"):
            gc_book.create_transaction(
                description="Unbalanced",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-40.00"},
                ],
            )

    def test_create_transaction_single_split(self, test_book: Path):
        """Should raise ValueError for single split."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="at least 2 splits"):
            gc_book.create_transaction(
                description="Single Split",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                ],
            )

    def test_create_transaction_invalid_account(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.create_transaction(
                description="Invalid Account",
                splits=[
                    {"account": "Nonexistent:Account", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-50.00"},
                ],
            )

    def test_create_transaction_placeholder_rejected(self, test_book: Path):
        """Should reject transaction targeting a placeholder account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="placeholder") as exc_info:
            gc_book.create_transaction(
                description="Bad Transaction",
                splits=[
                    {"account": "Expenses", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-50.00"},
                ],
            )
        # Error should suggest child accounts
        assert "Expenses:Groceries" in str(exc_info.value)

    def test_create_transaction_placeholder_suggests_children(self, test_book: Path):
        """Error message should list the placeholder's child accounts."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Use one of:"):
            gc_book.create_transaction(
                description="Bad Transaction",
                splits=[
                    {"account": "Assets", "amount": "-50.00"},
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                ],
            )

    def test_create_transaction_with_notes(self, test_book: Path):
        """Should create transaction with notes field."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="Safeway",
            splits=[
                {"account": "Expenses:Groceries", "amount": "75.00", "memo": "Meats"},
                {"account": "Assets:Checking", "amount": "-75.00"},
            ],
            notes="P2W1 groceries",
        )

        transaction = gc_book.get_transaction(result["guid"])
        assert transaction["description"] == "Safeway"
        assert transaction["notes"] == "P2W1 groceries"
        # Verify memo is separate from notes
        memos = {s["memo"] for s in transaction["splits"]}
        assert "Meats" in memos

    def test_create_transaction_without_notes(self, test_book: Path):
        """Transaction without notes should not include notes key."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="No Notes",
            splits=[
                {"account": "Expenses:Groceries", "amount": "10.00"},
                {"account": "Assets:Checking", "amount": "-10.00"},
            ],
        )

        transaction = gc_book.get_transaction(result["guid"])
        assert "notes" not in transaction


class TestCreateTransactionWarnings:
    """Tests for transaction creation warnings."""

    def test_future_date_warning(self, test_book: Path):
        """Should warn about future-dated transactions but still create them."""
        gc_book = GnuCashBook(str(test_book))
        future = date.today() + timedelta(days=30)
        result = gc_book.create_transaction(
            description="Future Payment",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=future,
        )
        assert result["status"] == "created"
        assert any(w["type"] == "future_date" for w in result["warnings"])

    def test_old_date_warning(self, test_book: Path):
        """Should warn about dates more than 365 days in the past."""
        gc_book = GnuCashBook(str(test_book))
        old = date.today() - timedelta(days=400)
        result = gc_book.create_transaction(
            description="Old Payment",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=old,
        )
        assert result["status"] == "created"
        assert any(w["type"] == "old_date" for w in result["warnings"])

    def test_normal_date_no_warning(self, test_book: Path):
        """Should not warn about normal dates."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Normal Payment",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=date.today(),
        )
        assert result["status"] == "created"
        assert "warnings" not in result

    def test_negative_expense_warning(self, test_book: Path):
        """Should warn about negative amounts to expense accounts."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Expense Reversal",
            splits=[
                {"account": "Expenses:Groceries", "amount": "-25.00"},
                {"account": "Assets:Checking", "amount": "25.00"},
            ],
        )
        assert result["status"] == "created"
        assert any(w["type"] == "negative_expense" for w in result["warnings"])

    def test_positive_income_warning(self, test_book: Path):
        """Should warn about positive amounts to income accounts."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Income Reversal",
            splits=[
                {"account": "Income:Salary", "amount": "100.00"},
                {"account": "Assets:Checking", "amount": "-100.00"},
            ],
        )
        assert result["status"] == "created"
        assert any(w["type"] == "positive_income" for w in result["warnings"])

    def test_warnings_dont_block_creation(self, test_book: Path):
        """Warnings should not prevent transaction creation."""
        gc_book = GnuCashBook(str(test_book))
        future = date.today() + timedelta(days=30)
        result = gc_book.create_transaction(
            description="Warned but Created",
            splits=[
                {"account": "Expenses:Groceries", "amount": "-10.00"},
                {"account": "Assets:Checking", "amount": "10.00"},
            ],
            trans_date=future,
        )
        assert result["status"] == "created"
        assert result["guid"]
        # Should have both future_date and negative_expense warnings
        warning_types = {w["type"] for w in result["warnings"]}
        assert "future_date" in warning_types
        assert "negative_expense" in warning_types


class TestDuplicateDetection:
    """Tests for duplicate transaction detection."""

    def test_high_duplicate_rejected(self, test_book: Path):
        """Should reject when all 3 signals match (description, amount, date)."""
        gc_book = GnuCashBook(str(test_book))
        # Existing: "Weekly Groceries", $150, 2024-01-20
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
        )
        assert result["status"] == "rejected"
        assert result["reason"] == "duplicate_detected"
        assert len(result["duplicates"]) > 0
        assert result["duplicates"][0]["confidence"] == "HIGH"

    def test_high_duplicate_force_create(self, test_book: Path):
        """Should create when force_create overrides HIGH duplicate."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
            force_create=True,
        )
        assert result["status"] == "created"
        assert "guid" in result
        assert len(result["duplicates"]) > 0

    def test_medium_duplicate_allowed(self, test_book: Path):
        """Should allow creation with MEDIUM confidence (2/3 signals)."""
        gc_book = GnuCashBook(str(test_book))
        # Same description and date, different amount
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "200.00"},
                {"account": "Assets:Checking", "amount": "-200.00"},
            ],
            trans_date=date(2024, 1, 20),
        )
        assert result["status"] == "created"
        assert any(
            d["confidence"] == "MEDIUM" for d in result.get("duplicates", [])
        )

    def test_low_duplicate_included(self, test_book: Path):
        """LOW confidence duplicates should be included for reference."""
        gc_book = GnuCashBook(str(test_book))
        # Only amount matches (~$150), different description, different date
        result = gc_book.create_transaction(
            description="Totally Different Store",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.50"},
                {"account": "Assets:Checking", "amount": "-150.50"},
            ],
            trans_date=date(2024, 1, 25),
        )
        assert result["status"] == "created"
        # Should have LOW confidence match (amount only)
        if result.get("duplicates"):
            assert any(
                d["confidence"] == "LOW" for d in result["duplicates"]
            )

    def test_check_duplicates_false_skips(self, test_book: Path):
        """Should skip duplicate check entirely when check_duplicates=False."""
        gc_book = GnuCashBook(str(test_book))
        # Exact duplicate but check disabled
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
            check_duplicates=False,
        )
        assert result["status"] == "created"
        assert "duplicates" not in result

    def test_substring_description_match(self, test_book: Path):
        """Substring matching should work in both directions."""
        gc_book = GnuCashBook(str(test_book))
        # "Groceries" is substring of "Weekly Groceries"
        result = gc_book.create_transaction(
            description="Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
        )
        assert result["status"] == "rejected"
        high = [d for d in result["duplicates"] if d["confidence"] == "HIGH"]
        assert len(high) > 0
        assert high[0]["match_signals"]["description"] is True

    def test_amount_tolerance(self, test_book: Path):
        """Amount match should use ±$1.00 tolerance."""
        gc_book = GnuCashBook(str(test_book))
        # $150.99 is within $1.00 of $150.00
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.99"},
                {"account": "Assets:Checking", "amount": "-150.99"},
            ],
            trans_date=date(2024, 1, 20),
        )
        assert result["status"] == "rejected"
        assert result["duplicates"][0]["match_signals"]["amount"] is True

    def test_date_window(self, test_book: Path):
        """Date match should use ±2 day window."""
        gc_book = GnuCashBook(str(test_book))
        # 2 days after existing (2024-01-20), should still match
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 22),
        )
        assert result["status"] == "rejected"
        assert result["duplicates"][0]["match_signals"]["date"] is True

    def test_no_duplicates_distant_date(self, test_book: Path):
        """Should find no duplicates when date is far from existing."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2025, 6, 15),
        )
        assert result["status"] == "created"
        assert "duplicates" not in result


class TestDryRun:
    """Tests for dry run mode on create_transaction."""

    def test_dry_run_returns_proposal(self, test_book: Path):
        """Should return proposed transaction without writing."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Dry Run Test",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=date(2024, 3, 1),
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert result["proposed_transaction"]["description"] == "Dry Run Test"
        assert result["proposed_transaction"]["date"] == "2024-03-01"
        assert result["proposed_transaction"]["currency"] == "USD"
        assert len(result["proposed_transaction"]["splits"]) == 2

    def test_dry_run_no_write(self, test_book: Path):
        """Dry run should not create a transaction in the book."""
        gc_book = GnuCashBook(str(test_book))
        before = gc_book.list_transactions()
        gc_book.create_transaction(
            description="Ghost Transaction",
            splits=[
                {"account": "Expenses:Groceries", "amount": "99.99"},
                {"account": "Assets:Checking", "amount": "-99.99"},
            ],
            dry_run=True,
        )
        after = gc_book.list_transactions()
        assert len(after) == len(before)

    def test_dry_run_includes_warnings(self, test_book: Path):
        """Dry run should include warnings."""
        gc_book = GnuCashBook(str(test_book))
        future = date.today() + timedelta(days=30)
        result = gc_book.create_transaction(
            description="Future Dry Run",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=future,
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert any(w["type"] == "future_date" for w in result["warnings"])

    def test_dry_run_includes_duplicates(self, test_book: Path):
        """Dry run should include duplicate candidates."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert len(result["duplicates"]) > 0

    def test_dry_run_validation_errors_raised(self, test_book: Path):
        """Dry run should still raise validation errors."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="do not balance"):
            gc_book.create_transaction(
                description="Unbalanced Dry Run",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-40.00"},
                ],
                dry_run=True,
            )

    def test_dry_run_placeholder_rejected(self, test_book: Path):
        """Dry run should reject placeholder accounts."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="placeholder"):
            gc_book.create_transaction(
                description="Placeholder Dry Run",
                splits=[
                    {"account": "Expenses", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-50.00"},
                ],
                dry_run=True,
            )

    def test_dry_run_unknown_currency(self, test_book: Path):
        """Dry run should raise error for unknown currency."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="not found"):
            gc_book.create_transaction(
                description="Bad Currency Dry Run",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-50.00"},
                ],
                currency="XYZ",
                dry_run=True,
            )


class TestAutoFillTransaction:
    """Tests for auto-fill splits from previous transactions."""

    def test_auto_fill_from_description(self, test_book: Path):
        """Should auto-fill splits from most recent matching transaction."""
        gc_book = GnuCashBook(str(test_book))
        # "Weekly Groceries" exists in fixture: $150 groceries / -$150 checking
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            trans_date=date(2024, 3, 1),
            check_duplicates=False,
        )
        assert result["status"] == "created"
        # Verify the transaction was created with auto-filled splits
        txn = gc_book.get_transaction(result["guid"])
        accounts = {s["account"] for s in txn["splits"]}
        assert "Expenses:Groceries" in accounts
        assert "Assets:Checking" in accounts

    def test_auto_fill_result_includes_source(self, test_book: Path):
        """Should include auto_filled_from with source transaction info."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            trans_date=date(2024, 3, 1),
            check_duplicates=False,
        )
        assert "auto_filled_from" in result
        assert result["auto_filled_from"]["description"] == "Weekly Groceries"
        assert "guid" in result["auto_filled_from"]
        assert "date" in result["auto_filled_from"]

    def test_auto_fill_no_match(self, test_book: Path):
        """Should raise ValueError when no matching transaction found."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="No matching transaction found"):
            gc_book.create_transaction(
                description="Never Seen Before XYZ123",
            )

    def test_auto_fill_with_dry_run(self, test_book: Path):
        """Should auto-fill and return proposal in dry run mode."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            dry_run=True,
            check_duplicates=False,
        )
        assert result["dry_run"] is True
        assert "auto_filled_from" in result
        # Proposed splits should come from the auto-fill
        proposed_splits = result["proposed_transaction"]["splits"]
        accounts = {s["account"] for s in proposed_splits}
        assert "Expenses:Groceries" in accounts

    def test_auto_fill_preserves_memo(self, test_book: Path):
        """Auto-fill should preserve memo from source transaction."""
        gc_book = GnuCashBook(str(test_book))
        # First create a transaction with a memo
        gc_book.create_transaction(
            description="Coffee Shop",
            splits=[
                {"account": "Expenses:Groceries", "amount": "5.00", "memo": "Latte"},
                {"account": "Assets:Checking", "amount": "-5.00"},
            ],
            trans_date=date(2024, 2, 1),
            check_duplicates=False,
        )
        # Auto-fill from it
        result = gc_book.create_transaction(
            description="Coffee Shop",
            trans_date=date(2024, 3, 1),
            check_duplicates=False,
        )
        txn = gc_book.get_transaction(result["guid"])
        memos = {s["memo"] for s in txn["splits"]}
        assert "Latte" in memos

    def test_explicit_splits_no_auto_fill(self, test_book: Path):
        """Providing explicit splits should bypass auto-fill."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "200.00"},
                {"account": "Assets:Checking", "amount": "-200.00"},
            ],
            trans_date=date(2024, 3, 1),
            check_duplicates=False,
        )
        assert result["status"] == "created"
        assert "auto_filled_from" not in result
        # Verify the explicit amount was used, not auto-filled
        txn = gc_book.get_transaction(result["guid"])
        for s in txn["splits"]:
            if s["account"] == "Expenses:Groceries":
                assert s["value"] == "200"


class TestSearchTransactions:
    """Tests for search_transactions method."""

    def test_search_by_description(self, test_book: Path):
        """Should find transactions by description."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("Salary", field="description")
        assert len(results) == 1
        assert results[0]["description"] == "Salary Deposit"

    def test_search_by_description_case_insensitive(self, test_book: Path):
        """Should search case-insensitively."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("salary", field="description")
        assert len(results) == 1

    def test_search_by_amount_exact(self, test_book: Path):
        """Should find transactions by exact amount."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("150", field="amount")
        assert len(results) == 1
        assert results[0]["description"] == "Weekly Groceries"

    def test_search_by_amount_greater_than(self, test_book: Path):
        """Should find transactions with amount greater than threshold."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions(">500", field="amount")
        # Opening (1000) and Salary (2000) transactions
        assert len(results) == 2

    def test_search_by_amount_less_than(self, test_book: Path):
        """Should find transactions with amount less than threshold."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("<200", field="amount")
        assert len(results) == 1
        assert results[0]["description"] == "Weekly Groceries"

    def test_search_by_amount_range(self, test_book: Path):
        """Should find transactions with amount in range."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("100-500", field="amount")
        assert len(results) == 1
        assert results[0]["description"] == "Weekly Groceries"

    def test_search_by_notes(self, test_book: Path):
        """Should find transactions by notes field."""
        gc_book = GnuCashBook(str(test_book))

        # Create a transaction with notes
        gc_book.create_transaction(
            description="Safeway",
            splits=[
                {"account": "Expenses:Groceries", "amount": "30.00"},
                {"account": "Assets:Checking", "amount": "-30.00"},
            ],
            notes="P2W1 groceries",
        )

        results = gc_book.search_transactions("P2W1", field="notes")
        assert len(results) == 1
        assert results[0]["description"] == "Safeway"
        assert results[0]["notes"] == "P2W1 groceries"

    def test_search_by_notes_no_match(self, test_book: Path):
        """Should return empty list when no notes match."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("nonexistent", field="notes")
        assert len(results) == 0

    def test_search_invalid_field(self, test_book: Path):
        """Should raise ValueError for invalid field."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Invalid search field"):
            gc_book.search_transactions("test", field="invalid")

    def test_search_by_amount_invalid_query(self, test_book: Path):
        """Should raise ValueError for malformed amount query."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Invalid amount query"):
            gc_book.search_transactions(">notanumber", field="amount")


class TestCreateAccount:
    """Tests for create_account method."""

    def test_create_account_success(self, test_book: Path):
        """Should create a new account under existing parent."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_account(
            name="Test Category",
            account_type="EXPENSE",
            parent="Expenses",
            description="A test expense category",
        )

        assert result["status"] == "created"
        assert result["fullname"] == "Expenses:Test Category"
        assert len(result["guid"]) == 32

        # Verify account exists
        account = gc_book.get_account("Expenses:Test Category")
        assert account is not None
        assert account["description"] == "A test expense category"

    def test_create_account_nested(self, test_book: Path):
        """Should create account under nested parent."""
        gc_book = GnuCashBook(str(test_book))

        # First create a parent
        gc_book.create_account(
            name="Online Services",
            account_type="EXPENSE",
            parent="Expenses",
            placeholder=True,
        )

        # Then create child
        result = gc_book.create_account(
            name="AI Subscriptions",
            account_type="EXPENSE",
            parent="Expenses:Online Services",
            description="Claude, ChatGPT, etc.",
        )

        assert result["fullname"] == "Expenses:Online Services:AI Subscriptions"

    def test_create_account_placeholder(self, test_book: Path):
        """Should create placeholder account."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_account(
            name="Placeholder Category",
            account_type="EXPENSE",
            parent="Expenses",
            placeholder=True,
        )

        account = gc_book.get_account("Expenses:Placeholder Category")
        assert account["placeholder"] is True

    def test_create_account_parent_not_found(self, test_book: Path):
        """Should raise ValueError if parent doesn't exist."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Parent account not found"):
            gc_book.create_account(
                name="Test",
                account_type="EXPENSE",
                parent="Nonexistent:Parent",
            )

    def test_create_account_duplicate(self, test_book: Path):
        """Should raise ValueError if account with same name exists under parent."""
        gc_book = GnuCashBook(str(test_book))

        # Groceries already exists under Expenses
        with pytest.raises(ValueError, match="already exists"):
            gc_book.create_account(
                name="Groceries",
                account_type="EXPENSE",
                parent="Expenses",
            )

    def test_create_account_invalid_type(self, test_book: Path):
        """Should raise ValueError for invalid account type."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Invalid account type"):
            gc_book.create_account(
                name="Test",
                account_type="INVALID",
                parent="Expenses",
            )

    def test_create_account_type_case_insensitive(self, test_book: Path):
        """Should accept lowercase account types."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_account(
            name="Lowercase Type Test",
            account_type="expense",
            parent="Expenses",
        )

        assert result["status"] == "created"


class TestUpdateAccount:
    """Tests for update_account method."""

    def test_update_account_rename(self, test_book: Path):
        """Should rename an account."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.update_account(
            name="Expenses:Groceries",
            new_name="Food & Groceries",
        )

        assert result["status"] == "updated"
        assert result["name"] == "Food & Groceries"

        # Verify old name doesn't exist
        assert gc_book.get_account("Expenses:Groceries") is None
        # Verify new name exists
        assert gc_book.get_account("Expenses:Food & Groceries") is not None

    def test_update_account_description(self, test_book: Path):
        """Should update account description."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.update_account(
            name="Expenses:Groceries",
            description="Weekly grocery shopping",
        )

        assert result["status"] == "updated"
        assert result["description"] == "Weekly grocery shopping"

    def test_update_account_placeholder(self, test_book: Path):
        """Should update placeholder status."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.update_account(
            name="Expenses",
            placeholder=True,
        )

        assert result["status"] == "updated"
        assert result["placeholder"] is True

    def test_update_account_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.update_account(
                name="Nonexistent:Account",
                description="test",
            )

    def test_update_account_name_conflict(self, test_book: Path):
        """Should raise ValueError if new name conflicts with sibling."""
        gc_book = GnuCashBook(str(test_book))

        # Create another expense account
        gc_book.create_account(
            name="Dining",
            account_type="EXPENSE",
            parent="Expenses",
        )

        # Try to rename Groceries to Dining
        with pytest.raises(ValueError, match="already exists"):
            gc_book.update_account(
                name="Expenses:Groceries",
                new_name="Dining",
            )


class TestMoveAccount:
    """Tests for move_account method."""

    def test_move_account_success(self, test_book: Path):
        """Should move an account to new parent."""
        gc_book = GnuCashBook(str(test_book))

        # Create a new parent category
        gc_book.create_account(
            name="Daily Expenses",
            account_type="EXPENSE",
            parent="Expenses",
            placeholder=True,
        )

        # Move Groceries under Daily Expenses
        result = gc_book.move_account(
            name="Expenses:Groceries",
            new_parent="Expenses:Daily Expenses",
        )

        assert result["status"] == "moved"
        assert result["fullname"] == "Expenses:Daily Expenses:Groceries"

    def test_move_account_not_found(self, test_book: Path):
        """Should raise ValueError if account not found."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.move_account(
                name="Nonexistent:Account",
                new_parent="Expenses",
            )

    def test_move_account_parent_not_found(self, test_book: Path):
        """Should raise ValueError if new parent not found."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Parent account not found"):
            gc_book.move_account(
                name="Expenses:Groceries",
                new_parent="Nonexistent:Parent",
            )

    def test_move_account_circular_reference(self, test_book: Path):
        """Should raise ValueError if move would create circular reference."""
        gc_book = GnuCashBook(str(test_book))

        # Create a child under Groceries
        gc_book.create_account(
            name="Organic",
            account_type="EXPENSE",
            parent="Expenses:Groceries",
        )

        # Try to move Groceries under its own child
        with pytest.raises(ValueError, match="Cannot move account under itself"):
            gc_book.move_account(
                name="Expenses:Groceries",
                new_parent="Expenses:Groceries:Organic",
            )

    def test_move_account_name_conflict(self, test_book: Path):
        """Should raise ValueError if name conflicts in new location."""
        gc_book = GnuCashBook(str(test_book))

        # Create an account under Assets with same name as one under Expenses
        gc_book.create_account(
            name="Groceries",
            account_type="ASSET",
            parent="Assets",
        )

        # Try to move Expenses:Groceries to Assets (conflict with Assets:Groceries)
        with pytest.raises(ValueError, match="already exists"):
            gc_book.move_account(
                name="Expenses:Groceries",
                new_parent="Assets",
            )


class TestDeleteAccount:
    """Tests for delete_account method."""

    def test_delete_account_success(self, test_book: Path):
        """Should delete an empty account."""
        gc_book = GnuCashBook(str(test_book))

        # Create a new account to delete
        gc_book.create_account(
            name="To Delete",
            account_type="EXPENSE",
            parent="Expenses",
        )

        result = gc_book.delete_account("Expenses:To Delete")

        assert result["status"] == "deleted"
        assert gc_book.get_account("Expenses:To Delete") is None

    def test_delete_account_not_found(self, test_book: Path):
        """Should raise ValueError if account not found."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.delete_account("Nonexistent:Account")

    def test_delete_account_with_children(self, test_book: Path):
        """Should raise ValueError if account has children."""
        gc_book = GnuCashBook(str(test_book))

        # Expenses has Groceries as a child
        with pytest.raises(ValueError, match="Cannot delete account with children"):
            gc_book.delete_account("Expenses")

    def test_delete_account_with_transactions(self, test_book: Path):
        """Should raise ValueError if account has transactions."""
        gc_book = GnuCashBook(str(test_book))

        # Groceries has transactions
        with pytest.raises(ValueError, match="Cannot delete account with"):
            gc_book.delete_account("Expenses:Groceries")


class TestDeleteTransaction:
    """Tests for delete_transaction method."""

    def test_delete_transaction_success(self, test_book: Path):
        """Should delete an existing transaction."""
        gc_book = GnuCashBook(str(test_book))

        # Get a transaction to delete
        transactions = gc_book.list_transactions()
        guid = transactions[0]["guid"]
        description = transactions[0]["description"]

        result = gc_book.delete_transaction(guid)

        assert result["status"] == "deleted"
        assert result["guid"] == guid
        assert result["description"] == description

        # Verify transaction is gone
        assert gc_book.get_transaction(guid) is None

    def test_delete_transaction_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent transaction."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Transaction not found"):
            gc_book.delete_transaction("nonexistent_guid_12345")

    def test_delete_reconciled_transaction_rejected(self, test_book: Path):
        """Should reject deletion of transaction with reconciled splits."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions()
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        guid = transactions[0]["guid"]
        with pytest.raises(ValueError, match="reconciled splits"):
            gc_book.delete_transaction(guid)

    def test_delete_reconciled_transaction_force(self, test_book: Path):
        """Should allow deletion with force=True despite reconciled splits."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions()
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        guid = transactions[0]["guid"]
        result = gc_book.delete_transaction(guid, force=True)

        assert result["status"] == "deleted"
        assert result["reconciled_splits_affected"] == 1
        assert gc_book.get_transaction(guid) is None


class TestUpdateTransaction:
    """Tests for update_transaction method."""

    def test_update_description_only(self, test_book: Path):
        """Should update only the description."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions()
        guid = transactions[0]["guid"]

        result = gc_book.update_transaction(
            guid=guid,
            description="Updated Description",
        )

        assert result["status"] == "updated"
        assert result["description"] == "Updated Description"

    def test_update_date_only(self, test_book: Path):
        """Should update only the date."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions()
        guid = transactions[0]["guid"]

        result = gc_book.update_transaction(
            guid=guid,
            trans_date=date(2024, 6, 15),
        )

        assert result["status"] == "updated"
        assert result["date"] == "2024-06-15"

    def test_update_splits(self, test_book: Path):
        """Should update split amounts."""
        gc_book = GnuCashBook(str(test_book))

        # Get the groceries transaction (150.00)
        transactions = gc_book.search_transactions("Groceries")
        guid = transactions[0]["guid"]

        result = gc_book.update_transaction(
            guid=guid,
            splits=[
                {"account": "Expenses:Groceries", "amount": "175.00"},
                {"account": "Assets:Checking", "amount": "-175.00"},
            ],
        )

        assert result["status"] == "updated"
        # Verify new amounts
        updated = gc_book.get_transaction(guid)
        for split in updated["splits"]:
            if split["account"] == "Expenses:Groceries":
                assert split["value"] == "175"

    def test_update_everything(self, test_book: Path):
        """Should update description, date, and splits together."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Groceries")
        guid = transactions[0]["guid"]

        result = gc_book.update_transaction(
            guid=guid,
            description="Safeway Groceries",
            trans_date=date(2024, 1, 21),
            splits=[
                {"account": "Expenses:Groceries", "amount": "160.00"},
                {"account": "Assets:Checking", "amount": "-160.00"},
            ],
        )

        assert result["status"] == "updated"
        assert result["description"] == "Safeway Groceries"
        assert result["date"] == "2024-01-21"

    def test_update_transaction_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent transaction."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Transaction not found"):
            gc_book.update_transaction(
                guid="nonexistent_guid",
                description="Test",
            )

    def test_update_splits_unbalanced(self, test_book: Path):
        """Should raise ValueError for unbalanced splits."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions()
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="do not balance"):
            gc_book.update_transaction(
                guid=guid,
                splits=[
                    {"account": "Expenses:Groceries", "amount": "100.00"},
                    {"account": "Assets:Checking", "amount": "-90.00"},
                ],
            )

    def test_update_splits_account_not_found(self, test_book: Path):
        """Should raise ValueError if split account not in transaction."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Groceries")
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="Account not found in transaction"):
            gc_book.update_transaction(
                guid=guid,
                splits=[
                    {"account": "Expenses:Nonexistent", "amount": "100.00"},
                    {"account": "Assets:Checking", "amount": "-100.00"},
                ],
            )

    def test_update_notes(self, test_book: Path):
        """Should add notes to an existing transaction."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Groceries")
        guid = transactions[0]["guid"]

        result = gc_book.update_transaction(
            guid=guid,
            notes="P2W1 groceries",
        )

        assert result["status"] == "updated"
        assert result["notes"] == "P2W1 groceries"

        # Verify persistence
        updated = gc_book.get_transaction(guid)
        assert updated["notes"] == "P2W1 groceries"

    def test_clear_notes(self, test_book: Path):
        """Should clear notes when empty string is passed."""
        gc_book = GnuCashBook(str(test_book))

        # Create transaction with notes
        create_result = gc_book.create_transaction(
            description="With Notes",
            splits=[
                {"account": "Expenses:Groceries", "amount": "20.00"},
                {"account": "Assets:Checking", "amount": "-20.00"},
            ],
            notes="Some notes",
        )
        guid = create_result["guid"]

        # Clear notes
        result = gc_book.update_transaction(guid=guid, notes="")
        assert "notes" not in result

        # Verify persistence
        updated = gc_book.get_transaction(guid)
        assert "notes" not in updated

    def test_update_reconciled_splits_rejected(self, test_book: Path):
        """Should reject split updates on transactions with reconciled splits."""
        gc_book = GnuCashBook(str(test_book))

        # Get the groceries transaction and reconcile a split
        transactions = gc_book.search_transactions("Groceries")
        guid = transactions[0]["guid"]
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        with pytest.raises(ValueError, match="reconciled splits"):
            gc_book.update_transaction(
                guid=guid,
                splits=[
                    {"account": "Expenses:Groceries", "amount": "175.00"},
                    {"account": "Assets:Checking", "amount": "-175.00"},
                ],
            )

    def test_update_reconciled_splits_force(self, test_book: Path):
        """Should allow split updates with force=True despite reconciled splits."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Groceries")
        guid = transactions[0]["guid"]
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        result = gc_book.update_transaction(
            guid=guid,
            splits=[
                {"account": "Expenses:Groceries", "amount": "175.00"},
                {"account": "Assets:Checking", "amount": "-175.00"},
            ],
            force=True,
        )
        assert result["status"] == "updated"

    def test_update_description_on_reconciled_ok(self, test_book: Path):
        """Should allow description/date/notes changes without force on reconciled."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Groceries")
        guid = transactions[0]["guid"]
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        # Description change should work without force
        result = gc_book.update_transaction(
            guid=guid, description="Updated Groceries"
        )
        assert result["status"] == "updated"
        assert result["description"] == "Updated Groceries"


class TestSetReconcileState:
    """Tests for set_reconcile_state method."""

    def test_set_reconcile_cleared(self, test_book: Path):
        """Should set split to cleared state."""
        gc_book = GnuCashBook(str(test_book))

        # Get a split guid
        transactions = gc_book.list_transactions()
        split_guid = transactions[0]["splits"][0]["guid"]

        result = gc_book.set_reconcile_state(split_guid, "c")

        assert result["status"] == "updated"
        assert result["reconcile_state"] == "c"

    def test_set_reconcile_reconciled(self, test_book: Path):
        """Should set split to reconciled state with date."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions()
        split_guid = transactions[0]["splits"][0]["guid"]

        result = gc_book.set_reconcile_state(
            split_guid, "y", reconcile_date=date(2024, 1, 31)
        )

        assert result["status"] == "updated"
        assert result["reconcile_state"] == "y"
        assert result["reconcile_date"] is not None

    def test_set_reconcile_new(self, test_book: Path):
        """Should reset split to new state."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions()
        split_guid = transactions[0]["splits"][0]["guid"]

        # First set to cleared
        gc_book.set_reconcile_state(split_guid, "c")

        # Then reset to new
        result = gc_book.set_reconcile_state(split_guid, "n")

        assert result["reconcile_state"] == "n"
        assert result["reconcile_date"] is None

    def test_set_reconcile_invalid_state(self, test_book: Path):
        """Should raise ValueError for invalid state."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions()
        split_guid = transactions[0]["splits"][0]["guid"]

        with pytest.raises(ValueError, match="Invalid reconcile state"):
            gc_book.set_reconcile_state(split_guid, "x")

    def test_set_reconcile_split_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent split."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Split not found"):
            gc_book.set_reconcile_state("nonexistent_guid", "c")


class TestGetUnreconciledSplits:
    """Tests for get_unreconciled_splits method."""

    def test_get_unreconciled_splits(self, test_book: Path):
        """Should return unreconciled splits for account."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.get_unreconciled_splits("Assets:Checking")

        assert "splits" in result
        assert "cleared_total" in result
        assert "uncleared_total" in result
        assert result["account"] == "Assets:Checking"
        # All splits should be unreconciled initially
        assert result["count"] > 0

    def test_get_unreconciled_splits_with_date(self, test_book: Path):
        """Should filter splits by date."""
        gc_book = GnuCashBook(str(test_book))

        # Get splits before a specific date
        result = gc_book.get_unreconciled_splits(
            "Assets:Checking", as_of_date=date(2024, 1, 10)
        )

        # All returned splits should be on or before the date
        for split in result["splits"]:
            assert split["date"] <= "2024-01-10"

    def test_get_unreconciled_splits_account_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.get_unreconciled_splits("Nonexistent:Account")


class TestReconcileAccount:
    """Tests for reconcile_account method."""

    def test_reconcile_account_success(self, test_book: Path):
        """Should reconcile splits when balance matches."""
        gc_book = GnuCashBook(str(test_book))

        # Get unreconciled splits
        unreconciled = gc_book.get_unreconciled_splits("Assets:Checking")

        # Calculate what the balance should be
        total = Decimal("0")
        guids = []
        for split in unreconciled["splits"]:
            total += Decimal(split["amount"])
            guids.append(split["guid"])

        # Reconcile all splits
        result = gc_book.reconcile_account(
            account_name="Assets:Checking",
            statement_date=date(2024, 1, 31),
            statement_balance=str(total),
            split_guids=guids,
        )

        assert result["status"] == "reconciled"
        assert result["splits_reconciled"] == len(guids)

    def test_reconcile_account_balance_mismatch(self, test_book: Path):
        """Should raise ValueError when balance doesn't match."""
        gc_book = GnuCashBook(str(test_book))

        unreconciled = gc_book.get_unreconciled_splits("Assets:Checking")
        guids = [s["guid"] for s in unreconciled["splits"]]

        with pytest.raises(ValueError, match="Balance mismatch"):
            gc_book.reconcile_account(
                account_name="Assets:Checking",
                statement_date=date(2024, 1, 31),
                statement_balance="9999999.99",  # Wrong balance
                split_guids=guids,
            )

    def test_reconcile_account_split_wrong_account(self, test_book: Path):
        """Should raise ValueError if split belongs to different account."""
        gc_book = GnuCashBook(str(test_book))

        # Get a split from a different account
        transactions = gc_book.list_transactions()
        expense_split = None
        for split in transactions[0]["splits"]:
            if "Expenses" in split["account"]:
                expense_split = split["guid"]
                break

        if expense_split:
            with pytest.raises(ValueError, match="belongs to account"):
                gc_book.reconcile_account(
                    account_name="Assets:Checking",
                    statement_date=date(2024, 1, 31),
                    statement_balance="0",
                    split_guids=[expense_split],
                )


class TestVoidTransaction:
    """Tests for void_transaction method."""

    def test_void_transaction_success(self, test_book: Path):
        """Should void a transaction."""
        gc_book = GnuCashBook(str(test_book))

        # Get a transaction to void
        transactions = gc_book.list_transactions()
        guid = transactions[0]["guid"]

        result = gc_book.void_transaction(guid, reason="Entered in error")

        assert result["status"] == "voided"
        assert result["void_reason"] == "Entered in error"

        # Verify the transaction is voided (splits have 0 value and 'v' state)
        voided = gc_book.get_transaction(guid)
        for split in voided["splits"]:
            assert split["value"] == "0"
            assert split["reconcile_state"] == "v"

    def test_void_transaction_no_reason(self, test_book: Path):
        """Should raise ValueError if no reason provided."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions()
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="reason is required"):
            gc_book.void_transaction(guid, reason="")

    def test_void_transaction_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent transaction."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Transaction not found"):
            gc_book.void_transaction("nonexistent_guid", reason="Test")

    def test_void_transaction_already_voided(self, test_book: Path):
        """Should raise ValueError if already voided."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions()
        guid = transactions[0]["guid"]

        # Void once
        gc_book.void_transaction(guid, reason="First void")

        # Try to void again
        with pytest.raises(ValueError, match="already voided"):
            gc_book.void_transaction(guid, reason="Second void")


class TestUnvoidTransaction:
    """Tests for unvoid_transaction method."""

    def test_unvoid_transaction_success(self, test_book: Path):
        """Should restore a voided transaction."""
        gc_book = GnuCashBook(str(test_book))

        # Get original transaction values
        transactions = gc_book.list_transactions()
        guid = transactions[0]["guid"]
        original = gc_book.get_transaction(guid)
        original_values = {s["account"]: s["value"] for s in original["splits"]}

        # Void it
        gc_book.void_transaction(guid, reason="Test void")

        # Unvoid it
        result = gc_book.unvoid_transaction(guid)

        assert result["status"] == "unvoided"

        # Verify values are restored
        for split in result["splits"]:
            assert split["value"] == original_values[split["account"]]
            assert split["reconcile_state"] == "n"

    def test_unvoid_transaction_not_voided(self, test_book: Path):
        """Should raise ValueError if transaction is not voided."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions()
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="not voided"):
            gc_book.unvoid_transaction(guid)

    def test_unvoid_transaction_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent transaction."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Transaction not found"):
            gc_book.unvoid_transaction("nonexistent_guid")


class TestSpendingByCategory:
    """Tests for spending_by_category method."""

    def test_spending_by_category(self, test_book: Path):
        """Should return spending breakdown."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.spending_by_category(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        assert "period" in result
        assert "total" in result
        assert "categories" in result
        assert Decimal(result["total"]) > 0

    def test_spending_by_category_empty_period(self, test_book: Path):
        """Should return zero for period with no transactions."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.spending_by_category(
            start_date=date(2020, 1, 1),
            end_date=date(2020, 1, 31),
        )

        assert result["total"] == "0"
        assert result["categories"] == []


class TestIncomeBySource:
    """Tests for income_by_source method."""

    def test_income_by_source(self, test_book: Path):
        """Should return income breakdown."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.income_by_source(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        assert "period" in result
        assert "total" in result
        assert "sources" in result
        assert Decimal(result["total"]) > 0


class TestBalanceSheet:
    """Tests for balance_sheet method."""

    def test_balance_sheet(self, test_book: Path):
        """Should return balance sheet with assets, liabilities, equity."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.balance_sheet(as_of_date=date(2024, 12, 31))

        assert "as_of_date" in result
        assert "assets" in result
        assert "liabilities" in result
        assert "equity" in result
        assert "total" in result["assets"]
        assert "accounts" in result["assets"]


class TestNetWorth:
    """Tests for net_worth method."""

    def test_net_worth_point_in_time(self, test_book: Path):
        """Should calculate net worth at a point in time."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.net_worth(end_date=date(2024, 12, 31))

        assert "as_of_date" in result
        assert "net_worth" in result

    def test_net_worth_time_series(self, test_book: Path):
        """Should calculate net worth time series."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.net_worth(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            interval="month",
        )

        assert "series" in result
        assert len(result["series"]) > 0
        assert "date" in result["series"][0]
        assert "net_worth" in result["series"][0]

    def test_net_worth_invalid_interval(self, test_book: Path):
        """Should raise ValueError for invalid interval."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Invalid interval"):
            gc_book.net_worth(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
                interval="invalid",
            )


class TestCashFlow:
    """Tests for cash_flow method."""

    def test_cash_flow(self, test_book: Path):
        """Should calculate cash flow."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.cash_flow(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        assert "period" in result
        assert "inflows" in result
        assert "outflows" in result
        assert "net" in result

    def test_cash_flow_specific_account(self, test_book: Path):
        """Should calculate cash flow for specific account."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.cash_flow(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            account="Assets:Checking",
        )

        assert result["account"] == "Assets:Checking"

    def test_cash_flow_invalid_account(self, test_book: Path):
        """Should raise ValueError for invalid account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.cash_flow(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
                account="Nonexistent:Account",
            )


class TestListCommodities:
    """Tests for list_commodities method."""

    def test_list_commodities(self, test_book: Path):
        """Should return commodities grouped by namespace."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_commodities()

        assert "default_currency" in result
        assert result["default_currency"] == "USD"
        assert "commodities" in result
        assert "CURRENCY" in result["commodities"]

        # Should include USD at minimum
        mnemonics = [c["mnemonic"] for c in result["commodities"]["CURRENCY"]]
        assert "USD" in mnemonics

    def test_list_commodities_structure(self, test_book: Path):
        """Should return proper structure for each commodity."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_commodities()

        currencies = result["commodities"]["CURRENCY"]
        for commodity in currencies:
            assert "mnemonic" in commodity
            assert "fullname" in commodity
            assert "fraction" in commodity


class TestCreateAccountWithCurrency:
    """Tests for create_account with commodity parameter."""

    def test_create_account_with_currency(self, test_book: Path):
        """Should create account with specified currency."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_account(
            name="EUR Savings",
            account_type="BANK",
            parent="Assets",
            commodity="EUR",
            description="Euro savings account",
        )

        assert result["status"] == "created"
        assert result["fullname"] == "Assets:EUR Savings"

        # Verify the account commodity is EUR
        account = gc_book.get_account("Assets:EUR Savings")
        assert account is not None
        assert account["commodity"] == "EUR"

    def test_create_account_default_currency(self, test_book: Path):
        """Should use default currency when commodity is None."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_account(
            name="Default Currency Account",
            account_type="BANK",
            parent="Assets",
        )

        assert result["status"] == "created"

        account = gc_book.get_account("Assets:Default Currency Account")
        assert account["commodity"] == "USD"  # Book default

    def test_create_account_invalid_currency(self, test_book: Path):
        """Should raise ValueError for invalid currency code."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Invalid currency code"):
            gc_book.create_account(
                name="Bad Currency",
                account_type="BANK",
                parent="Assets",
                commodity="INVALID",
            )

    def test_create_account_creates_currency_commodity(self, test_book: Path):
        """Should auto-create currency commodity if it doesn't exist in book."""
        gc_book = GnuCashBook(str(test_book))

        # GBP shouldn't exist in the book initially (only USD)
        result = gc_book.create_account(
            name="GBP Account",
            account_type="BANK",
            parent="Assets",
            commodity="GBP",
        )

        assert result["status"] == "created"

        # Verify GBP is now in the commodities list
        commodities = gc_book.list_commodities()
        mnemonics = [c["mnemonic"] for c in commodities["commodities"]["CURRENCY"]]
        assert "GBP" in mnemonics


class TestCreateTransactionMultiCurrency:
    """Tests for create_transaction with multi-currency support."""

    def test_create_transaction_with_explicit_currency(self, test_book: Path):
        """Should create transaction with specified currency."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="Explicit USD Transaction",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=date(2024, 2, 1),
            currency="USD",
        )

        guid = result["guid"]
        transaction = gc_book.get_transaction(guid)
        assert transaction["currency"] == "USD"

    def test_create_transaction_default_currency(self, test_book: Path):
        """Should use default currency when none specified."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="Default Currency Transaction",
            splits=[
                {"account": "Expenses:Groceries", "amount": "30.00"},
                {"account": "Assets:Checking", "amount": "-30.00"},
            ],
        )

        transaction = gc_book.get_transaction(result["guid"])
        assert transaction["currency"] == "USD"

    def test_create_cross_currency_transaction(self, test_book: Path):
        """Should create cross-currency transaction with quantity."""
        gc_book = GnuCashBook(str(test_book))

        # First create a EUR account
        gc_book.create_account(
            name="EUR Card",
            account_type="CREDIT",
            parent="Liabilities",
            commodity="EUR",
        )

        # Create a cross-currency transaction: USD transaction with EUR split
        result = gc_book.create_transaction(
            description="Dinner in Paris",
            currency="USD",
            splits=[
                {"account": "Expenses:Groceries", "amount": "55.00"},
                {
                    "account": "Liabilities:EUR Card",
                    "amount": "-55.00",
                    "quantity": "-50.00",
                },
            ],
            trans_date=date(2024, 3, 1),
        )

        guid = result["guid"]
        transaction = gc_book.get_transaction(guid)
        assert transaction["currency"] == "USD"

        # Verify the EUR split has different value and quantity
        for split in transaction["splits"]:
            if split["account"] == "Liabilities:EUR Card":
                assert split["value"] == "-55"
                assert split["quantity"] == "-50"

    def test_create_cross_currency_missing_quantity(self, test_book: Path):
        """Should raise ValueError when quantity is required but missing."""
        gc_book = GnuCashBook(str(test_book))

        # Create a EUR account
        gc_book.create_account(
            name="EUR Checking",
            account_type="BANK",
            parent="Assets",
            commodity="EUR",
        )

        with pytest.raises(ValueError, match="requires 'quantity'"):
            gc_book.create_transaction(
                description="Missing quantity",
                currency="USD",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                    {"account": "Assets:EUR Checking", "amount": "-50.00"},
                ],
            )

    def test_create_cross_currency_sign_mismatch(self, test_book: Path):
        """Should raise ValueError when quantity and value have different signs."""
        gc_book = GnuCashBook(str(test_book))

        # Create a EUR account
        gc_book.create_account(
            name="EUR Savings",
            account_type="BANK",
            parent="Assets",
            commodity="EUR",
        )

        with pytest.raises(ValueError, match="same sign"):
            gc_book.create_transaction(
                description="Sign mismatch",
                currency="USD",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                    {
                        "account": "Assets:EUR Savings",
                        "amount": "-50.00",
                        "quantity": "45.00",  # Wrong sign!
                    },
                ],
            )

    def test_create_transaction_backward_compatible(self, test_book: Path):
        """Existing single-currency workflow should work unchanged."""
        gc_book = GnuCashBook(str(test_book))

        # No currency, no quantity - original API
        result = gc_book.create_transaction(
            description="Backward Compatible",
            splits=[
                {"account": "Expenses:Groceries", "amount": "85.00"},
                {"account": "Assets:Checking", "amount": "-85.00"},
            ],
        )

        guid = result["guid"]
        transaction = gc_book.get_transaction(guid)
        assert transaction["description"] == "Backward Compatible"

        # Value and quantity should be equal for same-currency
        for split in transaction["splits"]:
            assert split["value"] == split["quantity"]


class TestMultiCurrencyBalances:
    """Tests that balances and reports use split.quantity (account commodity)
    rather than split.value (transaction currency)."""

    def test_get_balance_uses_quantity(self, multi_currency_book: Path):
        """EUR account balance should be in EUR (quantity), not USD (value)."""
        gc_book = GnuCashBook(str(multi_currency_book))
        balance = gc_book.get_balance("Assets:Euro Savings")
        # The EUR savings account received 1000 EUR (quantity),
        # NOT 1100 USD (value)
        assert balance == Decimal("1000")

    def test_get_balance_usd_account_unaffected(self, multi_currency_book: Path):
        """USD account balance should still be correct."""
        gc_book = GnuCashBook(str(multi_currency_book))
        balance = gc_book.get_balance("Assets:Checking")
        # 5000 (opening) + 3000 (salary) - 1100 (transfer) - 200 (groceries)
        assert balance == Decimal("6700")

    def test_balance_sheet_uses_quantity(self, multi_currency_book: Path):
        """Balance sheet should report account balances in their own commodity."""
        gc_book = GnuCashBook(str(multi_currency_book))
        result = gc_book.balance_sheet(as_of_date=date(2024, 12, 31))

        asset_accounts = {
            a["account"]: Decimal(a["balance"])
            for a in result["assets"]["accounts"]
        }
        assert asset_accounts["Assets:Checking"] == Decimal("6700")
        # EUR savings should show 1000 (EUR quantity), not 1100 (USD value)
        assert asset_accounts["Assets:Euro Savings"] == Decimal("1000")

    def test_net_worth_uses_quantity(self, multi_currency_book: Path):
        """Net worth should use quantity for each account."""
        gc_book = GnuCashBook(str(multi_currency_book))
        result = gc_book.net_worth(end_date=date(2024, 12, 31))
        net = Decimal(result["net_worth"])
        # Assets: Checking 6700 + Euro Savings 1000 = 7700
        # (Note: mixing currencies, but that's the current behavior —
        # the important thing is we use quantity, not value)
        assert net == Decimal("7700")

    def test_cash_flow_uses_quantity(self, multi_currency_book: Path):
        """Cash flow should use quantity for account splits."""
        gc_book = GnuCashBook(str(multi_currency_book))
        result = gc_book.cash_flow(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        inflows = Decimal(result["inflows"])
        outflows = Decimal(result["outflows"])
        # Checking inflows: 5000 + 3000 = 8000
        # Checking outflows: 1100 + 200 = 1300
        # EUR Savings inflows: 1000 (quantity, not 1100 value)
        assert inflows == Decimal("9000")
        assert outflows == Decimal("1300")

    def test_spending_by_category_uses_quantity(self, multi_currency_book: Path):
        """Expense reporting should use quantity."""
        gc_book = GnuCashBook(str(multi_currency_book))
        result = gc_book.spending_by_category(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            depth=2,
        )
        assert result["total"] == "200"
        assert len(result["categories"]) == 1
        assert result["categories"][0]["account"] == "Expenses:Groceries"

    def test_income_by_source_uses_quantity(self, multi_currency_book: Path):
        """Income reporting should use quantity."""
        gc_book = GnuCashBook(str(multi_currency_book))
        result = gc_book.income_by_source(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            depth=2,
        )
        assert result["total"] == "3000"
        assert len(result["sources"]) == 1
        assert result["sources"][0]["account"] == "Income:Salary"


class TestCreateCommodity:
    """Tests for create_commodity method."""

    def test_create_commodity(self, test_book: Path):
        """Should create a new commodity and return it in list_commodities."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market Index Fund Admiral",
            namespace="FUND",
            fraction=10000,
            cusip="922908728",
        )

        assert result["status"] == "created"
        assert result["mnemonic"] == "VTSAX"
        assert result["namespace"] == "FUND"
        assert result["fullname"] == "Vanguard Total Stock Market Index Fund Admiral"
        assert result["fraction"] == 10000

        # Verify it appears in list_commodities
        commodities = gc_book.list_commodities()
        assert "FUND" in commodities["commodities"]
        mnemonics = [c["mnemonic"] for c in commodities["commodities"]["FUND"]]
        assert "VTSAX" in mnemonics

    def test_create_commodity_duplicate(self, test_book: Path):
        """Should raise ValueError when commodity already exists in namespace."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        with pytest.raises(ValueError, match="already exists"):
            gc_book.create_commodity(
                mnemonic="VTSAX",
                fullname="Duplicate",
                namespace="FUND",
            )

    def test_create_commodity_different_namespace(self, test_book: Path):
        """Should allow same mnemonic in different namespaces."""
        gc_book = GnuCashBook(str(test_book))

        result1 = gc_book.create_commodity(
            mnemonic="TEST",
            fullname="Test Fund",
            namespace="FUND",
        )
        result2 = gc_book.create_commodity(
            mnemonic="TEST",
            fullname="Test Stock",
            namespace="NASDAQ",
        )

        assert result1["status"] == "created"
        assert result2["status"] == "created"

        commodities = gc_book.list_commodities()
        assert "FUND" in commodities["commodities"]
        assert "NASDAQ" in commodities["commodities"]


class TestCreateAccountWithCommodity:
    """Tests for create_account with non-currency commodities."""

    def test_create_account_with_fund_commodity(self, test_book: Path):
        """Should create MUTUAL account with FUND commodity."""
        gc_book = GnuCashBook(str(test_book))

        # First create the commodity
        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        # Create a parent for investments
        gc_book.create_account(
            name="Investments",
            account_type="ASSET",
            parent="Assets",
            placeholder=True,
        )

        # Create the fund account
        result = gc_book.create_account(
            name="VTSAX",
            account_type="MUTUAL",
            parent="Assets:Investments",
            commodity="VTSAX",
            commodity_namespace="FUND",
        )

        assert result["status"] == "created"
        assert result["fullname"] == "Assets:Investments:VTSAX"

        # Verify the account commodity
        account = gc_book.get_account("Assets:Investments:VTSAX")
        assert account is not None
        assert account["commodity"] == "VTSAX"

    def test_create_account_missing_commodity(self, test_book: Path):
        """Should raise ValueError when commodity doesn't exist."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Commodity not found"):
            gc_book.create_account(
                name="Missing Fund",
                account_type="MUTUAL",
                parent="Assets",
                commodity="NONEXISTENT",
                commodity_namespace="FUND",
            )


class TestPrices:
    """Tests for price management methods."""

    def test_create_price(self, test_book: Path):
        """Should record a price and retrieve it."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        result = gc_book.create_price(
            commodity="VTSAX",
            namespace="FUND",
            value="127.50",
            currency="USD",
            price_date=date(2026, 2, 7),
            price_type="nav",
        )

        assert result["status"] == "created"
        assert result["value"] == "127.50"
        assert result["date"] == "2026-02-07"

        # Verify via get_prices
        prices = gc_book.get_prices(commodity="VTSAX", namespace="FUND")
        assert len(prices) == 1
        assert Decimal(prices[0]["value"]) == Decimal("127.50")
        assert prices[0]["type"] == "nav"

    def test_create_price_update(self, test_book: Path):
        """Should update existing price with same date/source."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        # Create initial price
        gc_book.create_price(
            commodity="VTSAX",
            namespace="FUND",
            value="127.50",
            price_date=date(2026, 2, 7),
        )

        # Update with new value (same date and source)
        result = gc_book.create_price(
            commodity="VTSAX",
            namespace="FUND",
            value="128.75",
            price_date=date(2026, 2, 7),
        )

        assert result["status"] == "updated"
        assert result["value"] == "128.75"

        # Should still be only 1 price, not 2
        prices = gc_book.get_prices(commodity="VTSAX", namespace="FUND")
        assert len(prices) == 1
        assert prices[0]["value"] == "128.75"

    def test_get_prices_filtered(self, test_book: Path):
        """Should filter prices by date range."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        # Create prices on multiple dates
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="125.00", price_date=date(2026, 2, 1),
        )
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="126.50", price_date=date(2026, 2, 5),
        )
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="128.75", price_date=date(2026, 2, 10),
        )

        # Filter to middle date
        prices = gc_book.get_prices(
            commodity="VTSAX",
            namespace="FUND",
            start_date=date(2026, 2, 3),
            end_date=date(2026, 2, 8),
        )
        assert len(prices) == 1
        assert Decimal(prices[0]["value"]) == Decimal("126.50")

        # All prices, should be descending
        all_prices = gc_book.get_prices(commodity="VTSAX", namespace="FUND")
        assert len(all_prices) == 3
        assert all_prices[0]["date"] == "2026-02-10"  # Most recent first
        assert all_prices[2]["date"] == "2026-02-01"  # Oldest last

    def test_get_latest_price(self, test_book: Path):
        """Should return the most recent price."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="125.00", price_date=date(2026, 2, 1),
        )
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="128.75", price_date=date(2026, 2, 10),
        )
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="126.50", price_date=date(2026, 2, 5),
        )

        result = gc_book.get_latest_price(
            commodity="VTSAX", namespace="FUND", currency="USD",
        )
        assert result is not None
        assert result["value"] == "128.75"
        assert result["date"] == "2026-02-10"

    def test_get_latest_price_no_prices(self, test_book: Path):
        """Should return None when no prices exist."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        result = gc_book.get_latest_price(
            commodity="VTSAX", namespace="FUND", currency="USD",
        )
        assert result is None

    def test_create_price_commodity_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent commodity."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Commodity not found"):
            gc_book.create_price(
                commodity="NONEXISTENT",
                namespace="FUND",
                value="100.00",
            )


class TestInvestmentWorkflow:
    """Integration tests for the full investment workflow."""

    def test_full_investment_workflow(self, test_book: Path):
        """Full workflow: create fund, account, price, buy shares, check balance."""
        gc_book = GnuCashBook(str(test_book))

        # 1. Create commodity
        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
            fraction=10000,
        )

        # 2. Create account hierarchy
        gc_book.create_account(
            name="Investments",
            account_type="ASSET",
            parent="Assets",
            placeholder=True,
        )
        gc_book.create_account(
            name="401k",
            account_type="ASSET",
            parent="Assets:Investments",
            placeholder=True,
        )
        gc_book.create_account(
            name="VTSAX",
            account_type="MUTUAL",
            parent="Assets:Investments:401k",
            commodity="VTSAX",
            commodity_namespace="FUND",
        )

        # 3. Record price
        gc_book.create_price(
            commodity="VTSAX",
            namespace="FUND",
            value="127.50",
            price_date=date(2026, 2, 7),
        )

        # 4. Buy shares: $500 at $127.50/share = 3.9216 shares
        result = gc_book.create_transaction(
            description="VTSAX purchase",
            splits=[
                {
                    "account": "Assets:Investments:401k:VTSAX",
                    "amount": "500.00",
                    "quantity": "3.9216",
                },
                {
                    "account": "Assets:Checking",
                    "amount": "-500.00",
                },
            ],
            trans_date=date(2026, 2, 7),
            currency="USD",
        )
        assert result["status"] == "created"

        # 5. Check balance — should show share count
        balance = gc_book.get_balance("Assets:Investments:401k:VTSAX")
        assert balance == Decimal("3.9216")

        # 6. Get latest price
        price = gc_book.get_latest_price(
            commodity="VTSAX", namespace="FUND", currency="USD",
        )
        assert Decimal(price["value"]) == Decimal("127.50")

    def test_sell_shares(self, test_book: Path):
        """Should handle selling shares (negative quantity)."""
        gc_book = GnuCashBook(str(test_book))

        # Setup: create fund, account, buy shares
        gc_book.create_commodity(
            mnemonic="VTSAX", fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )
        gc_book.create_account(
            name="Investments", account_type="ASSET",
            parent="Assets", placeholder=True,
        )
        gc_book.create_account(
            name="VTSAX", account_type="MUTUAL",
            parent="Assets:Investments",
            commodity="VTSAX", commodity_namespace="FUND",
        )

        # Buy 10 shares at $100
        gc_book.create_transaction(
            description="Buy VTSAX",
            splits=[
                {"account": "Assets:Investments:VTSAX", "amount": "1000.00", "quantity": "10"},
                {"account": "Assets:Checking", "amount": "-1000.00"},
            ],
            trans_date=date(2026, 1, 15),
            currency="USD",
        )

        # Sell 3 shares at $110
        gc_book.create_transaction(
            description="Sell VTSAX",
            splits=[
                {"account": "Assets:Investments:VTSAX", "amount": "-330.00", "quantity": "-3"},
                {"account": "Assets:Checking", "amount": "330.00"},
            ],
            trans_date=date(2026, 2, 7),
            currency="USD",
        )

        # Balance should be 7 shares
        balance = gc_book.get_balance("Assets:Investments:VTSAX")
        assert balance == Decimal("7")

    def test_multiple_funds(self, test_book: Path):
        """Should handle multiple funds in same account hierarchy."""
        gc_book = GnuCashBook(str(test_book))

        # Create two funds
        gc_book.create_commodity(
            mnemonic="VTSAX", fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )
        gc_book.create_commodity(
            mnemonic="VTIAX", fullname="Vanguard Total International",
            namespace="FUND",
        )

        # Create accounts
        gc_book.create_account(
            name="Investments", account_type="ASSET",
            parent="Assets", placeholder=True,
        )
        gc_book.create_account(
            name="VTSAX", account_type="MUTUAL",
            parent="Assets:Investments",
            commodity="VTSAX", commodity_namespace="FUND",
        )
        gc_book.create_account(
            name="VTIAX", account_type="MUTUAL",
            parent="Assets:Investments",
            commodity="VTIAX", commodity_namespace="FUND",
        )

        # Buy both
        gc_book.create_transaction(
            description="Buy VTSAX",
            splits=[
                {"account": "Assets:Investments:VTSAX", "amount": "500.00", "quantity": "4"},
                {"account": "Assets:Checking", "amount": "-500.00"},
            ],
            trans_date=date(2026, 2, 7),
            currency="USD",
        )
        gc_book.create_transaction(
            description="Buy VTIAX",
            splits=[
                {"account": "Assets:Investments:VTIAX", "amount": "300.00", "quantity": "10"},
                {"account": "Assets:Checking", "amount": "-300.00"},
            ],
            trans_date=date(2026, 2, 7),
            currency="USD",
        )

        # Check balances
        vtsax_bal = gc_book.get_balance("Assets:Investments:VTSAX")
        vtiax_bal = gc_book.get_balance("Assets:Investments:VTIAX")
        assert vtsax_bal == Decimal("4")
        assert vtiax_bal == Decimal("10")

    def test_list_commodities_with_prices(self, test_book: Path):
        """Enhanced list_commodities should show latest prices."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX", fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        # Add prices
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="125.00", price_date=date(2026, 2, 1),
        )
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="128.75", price_date=date(2026, 2, 7),
        )

        commodities = gc_book.list_commodities()
        fund_entries = commodities["commodities"]["FUND"]
        vtsax = next(c for c in fund_entries if c["mnemonic"] == "VTSAX")

        assert "latest_price" in vtsax
        assert vtsax["latest_price"]["value"] == "128.75"
        assert vtsax["latest_price"]["date"] == "2026-02-07"
        assert vtsax["latest_price"]["currency"] == "USD"
