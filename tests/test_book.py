"""Tests for GnuCashBook wrapper."""

from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook


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
            # Verify we can read accounts
            assert book.root_account is not None

    def test_open_readwrite(self, test_book: Path):
        """Should open book in read-write mode."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=False) as book:
            assert book is not None


# TODO: Add tests for each book method as they are implemented
# class TestListAccounts:
# class TestGetAccount:
# class TestGetBalance:
# class TestListTransactions:
# class TestGetTransaction:
# class TestCreateTransaction:
# class TestSearchTransactions:
