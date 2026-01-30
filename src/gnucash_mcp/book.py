"""GnuCash book wrapper using piecash."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Generator

import piecash


class GnuCashBook:
    """Thread-safe wrapper for piecash book operations."""

    def __init__(self, book_path: str):
        """Initialize with path to GnuCash SQLite book.

        Args:
            book_path: Path to the GnuCash SQLite file.

        Raises:
            FileNotFoundError: If the book path doesn't exist.
            ValueError: If the file is not a valid SQLite GnuCash book.
        """
        self.book_path = Path(book_path)
        if not self.book_path.exists():
            raise FileNotFoundError(f"GnuCash book not found: {book_path}")

    @contextmanager
    def open(self, readonly: bool = True) -> Generator[piecash.Book, None, None]:
        """Context manager for book access.

        Args:
            readonly: If True, open in read-only mode. Default True for safety.

        Yields:
            piecash.Book instance.
        """
        book = piecash.open_book(str(self.book_path), readonly=readonly)
        try:
            yield book
        finally:
            book.close()

    def list_accounts(self) -> list[dict]:
        """List all accounts in the chart of accounts."""
        # TODO: Implement
        raise NotImplementedError

    def get_account(self, name: str) -> dict | None:
        """Get details for a specific account by full name."""
        # TODO: Implement
        raise NotImplementedError

    def get_balance(self, account_name: str, as_of_date: date | None = None) -> Decimal:
        """Get balance for an account, optionally as of a specific date."""
        # TODO: Implement
        raise NotImplementedError

    def list_transactions(
        self,
        account: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List transactions with optional filters."""
        # TODO: Implement
        raise NotImplementedError

    def get_transaction(self, guid: str) -> dict | None:
        """Get details for a specific transaction by GUID."""
        # TODO: Implement
        raise NotImplementedError

    def create_transaction(
        self,
        description: str,
        splits: list[dict],
        trans_date: date | None = None,
        memo: str | None = None,
    ) -> str:
        """Create a new transaction with splits.

        Args:
            description: Transaction description.
            splits: List of splits, each with 'account' and 'amount' keys.
            trans_date: Transaction date. Defaults to today.
            memo: Optional memo for the transaction.

        Returns:
            GUID of the created transaction.

        Raises:
            ValueError: If splits don't balance or accounts don't exist.
        """
        # TODO: Implement
        raise NotImplementedError

    def search_transactions(self, query: str, field: str = "description") -> list[dict]:
        """Search transactions by field.

        Args:
            query: Search string.
            field: Field to search: 'description', 'memo', or 'amount'.

        Returns:
            List of matching transactions.
        """
        # TODO: Implement
        raise NotImplementedError
