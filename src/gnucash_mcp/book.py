"""GnuCash book wrapper using piecash."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Generator

import piecash


def _account_to_dict(account: piecash.Account) -> dict:
    """Convert a piecash Account to a serializable dict."""
    return {
        "guid": account.guid,
        "name": account.name,
        "fullname": account.fullname,
        "type": account.type,
        "commodity": account.commodity.mnemonic if account.commodity else None,
        "description": account.description or "",
        "placeholder": bool(account.placeholder),
    }


def _split_to_dict(split: piecash.Split) -> dict:
    """Convert a piecash Split to a serializable dict."""
    return {
        "guid": split.guid,
        "account": split.account.fullname,
        "value": str(split.value),
        "quantity": str(split.quantity),
        "memo": split.memo or "",
        "reconcile_state": split.reconcile_state,
    }


def _transaction_to_dict(transaction: piecash.Transaction) -> dict:
    """Convert a piecash Transaction to a serializable dict."""
    return {
        "guid": transaction.guid,
        "date": transaction.post_date.isoformat(),
        "description": transaction.description,
        "currency": transaction.currency.mnemonic,
        "splits": [_split_to_dict(s) for s in transaction.splits],
    }


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

    def _find_account(self, book: piecash.Book, fullname: str) -> piecash.Account | None:
        """Find an account by its full name path.

        Args:
            book: Open piecash book.
            fullname: Full account path (e.g., 'Assets:Bank:Checking').

        Returns:
            Account if found, None otherwise.
        """
        for account in book.accounts:
            if account.fullname == fullname:
                return account
        return None

    def list_accounts(self) -> list[dict]:
        """List all accounts in the chart of accounts.

        Returns:
            Flat list of account dicts with full paths.
        """
        with self.open(readonly=True) as book:
            accounts = []
            for account in book.accounts:
                # Skip the root template account
                if account.type == "ROOT":
                    continue
                accounts.append(_account_to_dict(account))
            return sorted(accounts, key=lambda a: a["fullname"])

    def get_account(self, name: str) -> dict | None:
        """Get details for a specific account by full name.

        Args:
            name: Full account path (e.g., 'Assets:Bank:Checking').

        Returns:
            Account dict if found, None otherwise.
        """
        with self.open(readonly=True) as book:
            account = self._find_account(book, name)
            if account:
                return _account_to_dict(account)
            return None

    def get_balance(self, account_name: str, as_of_date: date | None = None) -> Decimal:
        """Get balance for an account, optionally as of a specific date.

        Returns raw GnuCash balance (accounting sign convention).

        Args:
            account_name: Full account path.
            as_of_date: Date to calculate balance as of. Defaults to all time.

        Returns:
            Account balance as Decimal.

        Raises:
            ValueError: If account not found.
        """
        with self.open(readonly=True) as book:
            account = self._find_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            balance = Decimal("0")
            for split in account.splits:
                if as_of_date is None or split.transaction.post_date <= as_of_date:
                    balance += split.value

            return balance

    def list_transactions(
        self,
        account: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List transactions with optional filters.

        Args:
            account: Filter by account full name.
            start_date: Filter transactions on or after this date.
            end_date: Filter transactions on or before this date.
            limit: Maximum number of transactions to return.

        Returns:
            List of transaction dicts, most recent first.

        Raises:
            ValueError: If specified account not found.
        """
        with self.open(readonly=True) as book:
            # If filtering by account, get transactions through that account's splits
            if account:
                acct = self._find_account(book, account)
                if not acct:
                    raise ValueError(f"Account not found: {account}")
                transactions = {split.transaction for split in acct.splits}
            else:
                transactions = set(book.transactions)

            # Apply date filters
            filtered = []
            for trans in transactions:
                if start_date and trans.post_date < start_date:
                    continue
                if end_date and trans.post_date > end_date:
                    continue
                filtered.append(trans)

            # Sort by date descending
            filtered.sort(key=lambda t: t.post_date, reverse=True)

            # Apply limit
            filtered = filtered[:limit]

            return [_transaction_to_dict(t) for t in filtered]

    def get_transaction(self, guid: str) -> dict | None:
        """Get details for a specific transaction by GUID.

        Args:
            guid: Transaction GUID (32-character hex string).

        Returns:
            Transaction dict if found, None otherwise.
        """
        with self.open(readonly=True) as book:
            for transaction in book.transactions:
                if transaction.guid == guid:
                    return _transaction_to_dict(transaction)
            return None

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
            splits: List of splits, each with 'account' and 'amount' keys,
                    and optional 'memo' key.
            trans_date: Transaction date. Defaults to today.
            memo: Optional memo (currently unused, per-split memos preferred).

        Returns:
            GUID of the created transaction.

        Raises:
            ValueError: If splits don't balance, fewer than 2 splits,
                       or accounts don't exist.
        """
        if len(splits) < 2:
            raise ValueError("Transaction must have at least 2 splits")

        # Validate splits balance to zero
        total = Decimal("0")
        for split in splits:
            total += Decimal(split["amount"])
        if total != Decimal("0"):
            raise ValueError(f"Splits do not balance: total is {total}")

        if trans_date is None:
            trans_date = date.today()

        with self.open(readonly=False) as book:
            # Validate all accounts exist and build split list
            piecash_splits = []
            for split in splits:
                account = self._find_account(book, split["account"])
                if not account:
                    raise ValueError(f"Account not found: {split['account']}")

                piecash_splits.append(
                    piecash.Split(
                        account=account,
                        value=Decimal(split["amount"]),
                        memo=split.get("memo", ""),
                    )
                )

            # Create transaction
            transaction = piecash.Transaction(
                currency=book.default_currency,
                description=description,
                post_date=trans_date,
                splits=piecash_splits,
            )

            book.save()
            return transaction.guid

    def search_transactions(self, query: str, field: str = "description") -> list[dict]:
        """Search transactions by field.

        Args:
            query: Search string. For 'amount' field, supports:
                   - Exact: "100.00"
                   - Greater than: ">100"
                   - Less than: "<100"
                   - Range: "100-200"
            field: Field to search: 'description', 'memo', or 'amount'.

        Returns:
            List of matching transactions.

        Raises:
            ValueError: If field is not valid.
        """
        if field not in ("description", "memo", "amount"):
            raise ValueError(f"Invalid search field: {field}")

        with self.open(readonly=True) as book:
            results = []

            for transaction in book.transactions:
                if field == "description":
                    if query.lower() in transaction.description.lower():
                        results.append(_transaction_to_dict(transaction))

                elif field == "memo":
                    # Search across all split memos
                    for split in transaction.splits:
                        if split.memo and query.lower() in split.memo.lower():
                            results.append(_transaction_to_dict(transaction))
                            break

                elif field == "amount":
                    # Parse amount query for ranges/comparisons
                    if self._match_amount(transaction, query):
                        results.append(_transaction_to_dict(transaction))

            return results

    def _match_amount(self, transaction: piecash.Transaction, query: str) -> bool:
        """Check if any split amount matches the query.

        Args:
            transaction: Transaction to check.
            query: Amount query (exact, >N, <N, or N-M range).

        Returns:
            True if any split matches.
        """
        # Get absolute values of all splits
        amounts = [abs(split.value) for split in transaction.splits]

        # Parse query
        query = query.strip()

        # Greater than: >100
        if query.startswith(">"):
            threshold = Decimal(query[1:])
            return any(amt > threshold for amt in amounts)

        # Less than: <100
        if query.startswith("<"):
            threshold = Decimal(query[1:])
            return any(amt < threshold for amt in amounts)

        # Range: 100-200
        if "-" in query and not query.startswith("-"):
            parts = query.split("-")
            if len(parts) == 2:
                low = Decimal(parts[0])
                high = Decimal(parts[1])
                return any(low <= amt <= high for amt in amounts)

        # Exact match
        target = Decimal(query)
        return any(amt == target for amt in amounts)

    # Valid GnuCash account types
    VALID_ACCOUNT_TYPES = {
        "ASSET",
        "BANK",
        "CASH",
        "CREDIT",
        "EQUITY",
        "EXPENSE",
        "INCOME",
        "LIABILITY",
        "MUTUAL",
        "STOCK",
    }

    def create_account(
        self,
        name: str,
        account_type: str,
        parent: str,
        description: str = "",
        placeholder: bool = False,
    ) -> dict:
        """Create a new account in the chart of accounts.

        Args:
            name: Account name (e.g., "AI Subscriptions").
            account_type: GnuCash account type (ASSET, EXPENSE, etc.).
            parent: Full path of parent account (e.g., "Expenses:Online Services").
            description: Optional description.
            placeholder: If True, account is container-only. Default False.

        Returns:
            Dict with guid, fullname, and status.

        Raises:
            ValueError: If parent not found, invalid type, or duplicate name.
        """
        # Validate account type
        if account_type.upper() not in self.VALID_ACCOUNT_TYPES:
            raise ValueError(
                f"Invalid account type: {account_type}. "
                f"Valid types: {', '.join(sorted(self.VALID_ACCOUNT_TYPES))}"
            )

        with self.open(readonly=False) as book:
            # Find parent account
            parent_account = self._find_account(book, parent)
            if not parent_account:
                raise ValueError(f"Parent account not found: {parent}")

            # Check for duplicate - same name under same parent
            for child in parent_account.children:
                if child.name == name:
                    raise ValueError(
                        f"Account '{name}' already exists under '{parent}'"
                    )

            # Create the account
            new_account = piecash.Account(
                name=name,
                type=account_type.upper(),
                parent=parent_account,
                commodity=book.default_currency,
                description=description,
                placeholder=placeholder,
            )

            book.save()

            return {
                "guid": new_account.guid,
                "fullname": new_account.fullname,
                "status": "created",
            }

    def delete_transaction(self, guid: str) -> dict:
        """Delete a transaction by GUID.

        Args:
            guid: Transaction GUID (32-character hex string).

        Returns:
            Dict with guid, description, and status.

        Raises:
            ValueError: If transaction not found.
        """
        with self.open(readonly=False) as book:
            # Find the transaction
            transaction = None
            for t in book.transactions:
                if t.guid == guid:
                    transaction = t
                    break

            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # Capture info before deletion
            result = {
                "guid": guid,
                "description": transaction.description,
                "status": "deleted",
            }

            # Delete the transaction
            book.session.delete(transaction)
            book.save()

            return result

    def update_transaction(
        self,
        guid: str,
        description: str | None = None,
        trans_date: date | None = None,
        splits: list[dict] | None = None,
    ) -> dict:
        """Update an existing transaction.

        Args:
            guid: Transaction GUID to update.
            description: New description (optional).
            trans_date: New transaction date (optional).
            splits: List of split updates with 'account' and 'amount' (optional).
                    Must match existing splits by account name.

        Returns:
            Dict with updated transaction details.

        Raises:
            ValueError: If transaction not found, splits don't balance,
                       or account not found in splits.
        """
        with self.open(readonly=False) as book:
            # Find the transaction
            transaction = None
            for t in book.transactions:
                if t.guid == guid:
                    transaction = t
                    break

            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # Update description if provided
            if description is not None:
                transaction.description = description

            # Update date if provided
            if trans_date is not None:
                transaction.post_date = trans_date

            # Update splits if provided
            if splits is not None:
                # Validate splits balance to zero
                total = Decimal("0")
                for split in splits:
                    total += Decimal(split["amount"])
                if total != Decimal("0"):
                    raise ValueError(f"Splits do not balance: total is {total}")

                # Build a map of account -> new amount
                split_updates = {s["account"]: Decimal(s["amount"]) for s in splits}

                # Update existing splits
                for split in transaction.splits:
                    account_name = split.account.fullname
                    if account_name in split_updates:
                        new_value = split_updates[account_name]
                        split.value = new_value
                        split.quantity = new_value
                        del split_updates[account_name]

                # Check if all provided accounts were found
                if split_updates:
                    missing = list(split_updates.keys())[0]
                    raise ValueError(f"Account not found in transaction: {missing}")

            book.save()

            return _transaction_to_dict(transaction) | {"status": "updated"}
