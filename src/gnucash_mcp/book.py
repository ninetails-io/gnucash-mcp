"""GnuCash book wrapper using piecash."""

import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Generator

import piecash

# Debug logger - configured by logging_config.setup_logging()
debug_logger = logging.getLogger("gnucash_mcp.debug")


def _to_date(dt: date | datetime) -> date:
    """Convert a datetime or date to a date object.

    piecash may return either datetime or date for price dates depending
    on how the price was created. This normalizes to date.
    """
    if isinstance(dt, datetime):
        return dt.date()
    return dt


class GnuCashLockError(Exception):
    """Raised when the GnuCash book is locked by another process."""

    pass


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
        "reconcile_date": split.reconcile_date.isoformat() if split.reconcile_date else None,
        "lot_guid": split.lot.guid if split.lot else None,
    }


def _transaction_to_dict(transaction: piecash.Transaction) -> dict:
    """Convert a piecash Transaction to a serializable dict."""
    result = {
        "guid": transaction.guid,
        "date": transaction.post_date.isoformat(),
        "description": transaction.description,
        "currency": transaction.currency.mnemonic,
        "splits": [_split_to_dict(s) for s in transaction.splits],
    }
    if transaction.notes:
        result["notes"] = transaction.notes
    return result


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
    def open(
        self, readonly: bool = True, max_retries: int = 3, retry_delay: float = 0.5
    ) -> Generator[piecash.Book, None, None]:
        """Context manager for book access with retry logic for locked files.

        Args:
            readonly: If True, open in read-only mode. Default True for safety.
            max_retries: Number of retry attempts if file is locked. Default 3.
            retry_delay: Seconds to wait between retries. Default 0.5.

        Yields:
            piecash.Book instance.

        Raises:
            GnuCashLockError: If the book is locked after all retries.
            FileNotFoundError: If the book file doesn't exist.
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                book = piecash.open_book(str(self.book_path), readonly=readonly, do_backup=False)
                open_elapsed = (time.time() - start_time) * 1000
                debug_logger.debug(
                    f"Book opened (readonly={readonly}) in {open_elapsed:.0f}ms"
                )
                try:
                    yield book
                    return
                finally:
                    close_start = time.time()
                    book.close()
                    close_elapsed = (time.time() - close_start) * 1000
                    debug_logger.debug(f"Book closed in {close_elapsed:.0f}ms")
            except sqlite3.OperationalError as e:
                last_error = e
                error_msg = str(e).lower()
                if "locked" in error_msg or "busy" in error_msg:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                        continue
                    raise GnuCashLockError(
                        f"GnuCash book is locked (possibly by GnuCash or another process). "
                        f"Close GnuCash and try again. Details: {e}"
                    ) from e
                raise  # Re-raise non-lock errors immediately

        # Should not reach here, but just in case
        if last_error:
            raise last_error

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

    def _find_transaction(
        self, book: piecash.Book, guid: str
    ) -> piecash.Transaction | None:
        """Find a transaction by GUID.

        Args:
            book: Open piecash book.
            guid: Transaction GUID (32-character hex string).

        Returns:
            Transaction if found, None otherwise.
        """
        for transaction in book.transactions:
            if transaction.guid == guid:
                return transaction
        return None

    def _find_commodity(
        self, book: piecash.Book, mnemonic: str, namespace: str = "CURRENCY"
    ) -> piecash.Commodity | None:
        """Find a commodity by mnemonic and namespace.

        Args:
            book: Open piecash book.
            mnemonic: ISO currency code (e.g., "USD", "EUR") or stock symbol.
            namespace: Commodity namespace. Default "CURRENCY" for currencies.

        Returns:
            Commodity if found, None otherwise.
        """
        try:
            return book.commodities.get(mnemonic=mnemonic, namespace=namespace)
        except KeyError:
            return None

    def _get_or_create_currency(
        self, book: piecash.Book, mnemonic: str
    ) -> piecash.Commodity:
        """Get an existing currency or create it from ISO code.

        Uses book.currencies which has a built-in fallback that auto-creates
        currencies from the ISO 4217 table if they don't already exist.

        Args:
            book: Open piecash book.
            mnemonic: ISO 4217 currency code (e.g., "USD", "EUR", "GBP").

        Returns:
            Commodity for the currency.

        Raises:
            ValueError: If mnemonic is not a valid ISO 4217 currency code.
        """
        try:
            return book.currencies(mnemonic=mnemonic)
        except (KeyError, ValueError) as e:
            raise ValueError(f"Invalid currency code '{mnemonic}': {e}") from e

    def _find_budget(self, book: piecash.Book, name: str):
        """Find a budget by name.

        Args:
            book: Open piecash book.
            name: Budget name.

        Returns:
            Budget if found, None otherwise.
        """
        from piecash.budget import Budget

        for budget in book.session.query(Budget).all():
            if budget.name == name:
                return budget
        return None

    def _budget_to_dict(self, budget) -> dict:
        """Convert a piecash Budget to a serializable dict."""
        rec = budget.recurrence

        # Reverse-map recurrence to period_type string
        period_type = "monthly"
        if rec.recurrence_period_type == "month" and rec.recurrence_mult == 3:
            period_type = "quarterly"
        elif rec.recurrence_period_type == "week":
            period_type = "weekly"

        start = rec.recurrence_period_start
        if isinstance(start, datetime):
            start = start.date()

        return {
            "guid": budget.guid,
            "name": budget.name,
            "description": budget.description or "",
            "num_periods": budget.num_periods,
            "period_type": period_type,
            "start_date": start.isoformat(),
        }

    def _period_to_date_range(
        self, budget, period_num: int
    ) -> tuple[date, date]:
        """Convert a budget period number to a date range.

        Args:
            budget: piecash Budget object.
            period_num: Zero-indexed period number.

        Returns:
            Tuple of (period_start, period_end) as dates.

        Raises:
            ValueError: If period_num is out of range.
        """
        from dateutil.relativedelta import relativedelta

        if period_num < 0 or period_num >= budget.num_periods:
            raise ValueError(
                f"Period {period_num} out of range "
                f"(0-{budget.num_periods - 1})"
            )

        rec = budget.recurrence
        anchor = rec.recurrence_period_start
        if isinstance(anchor, datetime):
            anchor = anchor.date()

        period_type = rec.recurrence_period_type
        mult = rec.recurrence_mult

        if period_type == "month":
            delta = relativedelta(months=mult)
        elif period_type == "week":
            delta = relativedelta(weeks=mult)
        else:
            raise ValueError(f"Unsupported period type: {period_type}")

        start = anchor + delta * period_num
        end = anchor + delta * (period_num + 1) - timedelta(days=1)

        return start, end

    def _current_period(self, budget) -> int | None:
        """Get the current period number based on today's date.

        Args:
            budget: piecash Budget object.

        Returns:
            Period number (0-indexed), or None if today is outside budget range.
        """
        today = date.today()
        for p in range(budget.num_periods):
            start, end = self._period_to_date_range(budget, p)
            if start <= today <= end:
                return p
        return None

    def _resolve_periods(
        self, budget, period: int | str | None
    ) -> list[int]:
        """Resolve a period specifier to a list of period numbers.

        Args:
            budget: piecash Budget object.
            period: None/"all" for all, int for specific, "q1"-"q4" for quarter.

        Returns:
            List of period numbers.

        Raises:
            ValueError: If period is invalid or out of range.
        """
        num = budget.num_periods

        if period is None or period == "all":
            return list(range(num))

        if isinstance(period, int):
            if period < 0 or period >= num:
                raise ValueError(
                    f"Period {period} out of range (0-{num - 1})"
                )
            return [period]

        if isinstance(period, str):
            quarter_map = {
                "q1": (0, 3),
                "q2": (3, 6),
                "q3": (6, 9),
                "q4": (9, 12),
            }
            period_lower = period.lower()
            if period_lower in quarter_map:
                start, end = quarter_map[period_lower]
                result = [p for p in range(start, end) if p < num]
                if not result:
                    raise ValueError(
                        f"Quarter {period} has no periods in this budget "
                        f"(budget has {num} periods)"
                    )
                return result

        raise ValueError(
            f"Invalid period: {period}. "
            f"Use None, 'all', an integer, or 'q1'-'q4'."
        )

    def _collect_descendants(
        self, account, result: set
    ) -> None:
        """Recursively collect all descendant accounts."""
        for child in account.children:
            result.add(child)
            self._collect_descendants(child, result)

    # ============== Scheduled Transaction Helpers ==============

    VALID_FREQUENCIES = {
        "weekly", "biweekly", "monthly", "quarterly", "yearly",
    }

    FREQUENCY_TO_RECURRENCE = {
        "weekly": ("week", 1),
        "biweekly": ("week", 2),
        "monthly": ("month", 1),
        "quarterly": ("month", 3),
        "yearly": ("year", 1),
    }

    RECURRENCE_TO_FREQUENCY = {
        ("week", 1): "weekly",
        ("week", 2): "biweekly",
        ("month", 1): "monthly",
        ("month", 3): "quarterly",
        ("year", 1): "yearly",
    }

    def _next_occurrence(
        self,
        start_date: date,
        frequency: str,
        after: date | None = None,
        end_date: date | None = None,
    ) -> date | None:
        """Calculate the next occurrence of a scheduled transaction.

        Args:
            start_date: First occurrence date.
            frequency: One of VALID_FREQUENCIES.
            after: Find next occurrence after this date.
                   Defaults to today.
            end_date: If set, return None if next occurrence
                      would be past this date.

        Returns:
            Next occurrence date, or None if past end_date.
        """
        from dateutil.relativedelta import relativedelta

        if after is None:
            after = date.today()

        delta_map = {
            "weekly": relativedelta(weeks=1),
            "biweekly": relativedelta(weeks=2),
            "monthly": relativedelta(months=1),
            "quarterly": relativedelta(months=3),
            "yearly": relativedelta(years=1),
        }
        delta = delta_map[frequency]

        occurrence = start_date
        while occurrence <= after:
            occurrence += delta

        if end_date and occurrence > end_date:
            return None
        return occurrence

    def _sx_to_dict(self, sx, frequency: str | None = None) -> dict:
        """Serialize a ScheduledTransaction to a dict.

        Args:
            sx: piecash ScheduledTransaction object.
            frequency: Pre-computed frequency string. If None, derived
                       from recurrence.

        Returns:
            Dict with scheduled transaction details.
        """
        if frequency is None:
            rec = sx.recurrence
            key = (rec.recurrence_period_type, rec.recurrence_mult)
            frequency = self.RECURRENCE_TO_FREQUENCY.get(key, "unknown")

        start = sx.start_date
        if isinstance(start, datetime):
            start = start.date()

        end = sx.end_date
        if isinstance(end, datetime):
            end = end.date()

        last = sx.last_occur
        if isinstance(last, datetime):
            last = last.date()

        next_occ = self._next_occurrence(
            start, frequency, after=date.today() - timedelta(days=1),
            end_date=end,
        ) if frequency != "unknown" else None

        return {
            "guid": sx.guid,
            "name": sx.name,
            "enabled": bool(sx.enabled),
            "frequency": frequency,
            "start_date": start.isoformat(),
            "end_date": end.isoformat() if end else None,
            "last_occurrence": last.isoformat() if last else None,
            "next_occurrence": (
                next_occ.isoformat() if next_occ else None
            ),
            "instance_count": sx.instance_count,
            "auto_create": bool(sx.auto_create),
        }

    def _get_sx_splits(self, book, sx) -> list[dict]:
        """Read split templates from the scheduled transaction's slot.

        The split templates are stored as JSON in a slot named
        'splits-json' on the ScheduledTransaction.

        Args:
            book: Open piecash book.
            sx: ScheduledTransaction object.

        Returns:
            List of split dicts with 'account', 'amount', and
            optional 'memo'.
        """
        import json

        from sqlalchemy import text

        row = book.session.execute(
            text(
                "SELECT string_val FROM slots "
                "WHERE obj_guid = :guid AND name = :name"
            ),
            {"guid": sx.guid, "name": "splits-json"},
        ).first()
        if row:
            return json.loads(row[0])
        return []

    def list_commodities(self) -> dict:
        """List all commodities in the book with latest prices.

        Returns:
            Dict with commodities grouped by namespace, plus the default currency.
            Non-currency commodities include their latest price when available.
        """
        with self.open(readonly=True) as book:
            by_namespace: dict[str, list[dict]] = {}

            # Build a map of latest prices by commodity
            latest_prices: dict[str, tuple] = {}  # commodity_key -> (date, price)
            for p in book.prices:
                key = f"{p.commodity.namespace}:{p.commodity.mnemonic}"
                p_date = _to_date(p.date)
                if key not in latest_prices or p_date > latest_prices[key][0]:
                    latest_prices[key] = (p_date, p)

            for commodity in book.commodities:
                ns = commodity.namespace
                if ns not in by_namespace:
                    by_namespace[ns] = []

                entry: dict = {
                    "mnemonic": commodity.mnemonic,
                    "fullname": commodity.fullname,
                    "fraction": commodity.fraction,
                }

                # Add latest price for non-currency commodities
                key = f"{ns}:{commodity.mnemonic}"
                if key in latest_prices:
                    _, price = latest_prices[key]
                    entry["latest_price"] = {
                        "value": str(price.value),
                        "currency": price.currency.mnemonic,
                        "date": _to_date(price.date).isoformat(),
                    }

                by_namespace[ns].append(entry)

            # Sort each namespace's commodities
            for ns in by_namespace:
                by_namespace[ns].sort(key=lambda c: c["mnemonic"])

            return {
                "default_currency": book.default_currency.mnemonic,
                "commodities": by_namespace,
            }

    def create_commodity(
        self,
        mnemonic: str,
        fullname: str,
        namespace: str = "FUND",
        fraction: int = 10000,
        cusip: str | None = None,
    ) -> dict:
        """Create a new commodity (stock, mutual fund, etc.) in the book.

        Args:
            mnemonic: Symbol (e.g., "VTSAX", "AAPL"). Must be unique within namespace.
            fullname: Full name (e.g., "Vanguard Total Stock Market Index Fund").
            namespace: Grouping category. Common values: "FUND", "NASDAQ",
                       "NYSE", "AMEX", or any custom string. Default "FUND".
            fraction: Smallest fractional unit. Use 10000 for 4 decimal places
                      (standard for shares), 100 for 2, 1000000 for 6 (crypto).
                      Default 10000.
            cusip: Optional CUSIP/ISIN identifier for the security.

        Returns:
            Dict with mnemonic, namespace, fullname, fraction, and status.

        Raises:
            ValueError: If commodity already exists in that namespace.
        """
        with self.open(readonly=False) as book:
            # Check for duplicate
            existing = self._find_commodity(book, mnemonic, namespace)
            if existing:
                raise ValueError(
                    f"Commodity {namespace}:{mnemonic} already exists"
                )

            commodity = piecash.Commodity(
                namespace=namespace,
                mnemonic=mnemonic,
                fullname=fullname,
                fraction=fraction,
                cusip=cusip or "",
                book=book,
            )

            book.save()

            return {
                "mnemonic": commodity.mnemonic,
                "namespace": commodity.namespace,
                "fullname": commodity.fullname,
                "fraction": commodity.fraction,
                "status": "created",
            }

    def create_price(
        self,
        commodity: str,
        namespace: str,
        value: str,
        currency: str = "USD",
        price_date: date | None = None,
        price_type: str = "nav",
        source: str = "user:price",
    ) -> dict:
        """Record a price for a commodity (stock price, NAV, exchange rate).

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX", "AAPL").
            namespace: Namespace of the commodity (e.g., "FUND", "NASDAQ").
            value: Price per unit as decimal string (e.g., "250.45").
            currency: Currency the price is denominated in. Default "USD".
            price_date: Price date. Defaults to today.
            price_type: Type of price: "nav", "last", "bid", "ask", "unknown".
                        Default "nav".
            source: Source identifier. Default "user:price".

        Returns:
            Dict with commodity, date, value, type, and status.

        Raises:
            ValueError: If commodity not found or invalid currency.
        """
        if price_date is None:
            price_date = date.today()

        with self.open(readonly=False) as book:
            # Find the commodity
            comm = self._find_commodity(book, commodity, namespace)
            if not comm:
                raise ValueError(
                    f"Commodity not found: {namespace}:{commodity}"
                )

            # Find the currency
            curr = self._get_or_create_currency(book, currency)

            # Check for existing price (same commodity/currency/date/source)
            existing = None
            for p in book.prices:
                if (
                    p.commodity == comm
                    and p.currency == curr
                    and _to_date(p.date) == price_date
                    and p.source == source
                ):
                    existing = p
                    break

            if existing:
                # Update existing price
                existing.value = Decimal(value)
                existing.type = price_type
            else:
                # Create new price
                # piecash expects datetime.date, not datetime.datetime
                piecash.Price(
                    commodity=comm,
                    currency=curr,
                    date=price_date,
                    value=Decimal(value),
                    type=price_type,
                    source=source,
                )

            book.save()

            return {
                "commodity": commodity,
                "namespace": namespace,
                "currency": currency,
                "date": price_date.isoformat(),
                "value": value,
                "type": price_type,
                "status": "updated" if existing else "created",
            }

    def get_prices(
        self,
        commodity: str,
        namespace: str,
        start_date: date | None = None,
        end_date: date | None = None,
        currency: str | None = None,
    ) -> list[dict]:
        """Get price history for a commodity.

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX").
            namespace: Namespace of the commodity (e.g., "FUND").
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            currency: Optional currency filter (e.g., "USD").

        Returns:
            List of price dicts sorted by date descending (most recent first).

        Raises:
            ValueError: If commodity not found.
        """
        with self.open(readonly=True) as book:
            comm = self._find_commodity(book, commodity, namespace)
            if not comm:
                raise ValueError(
                    f"Commodity not found: {namespace}:{commodity}"
                )

            prices = []
            for p in book.prices:
                if p.commodity != comm:
                    continue
                if currency and p.currency.mnemonic != currency:
                    continue
                p_date = _to_date(p.date)
                if start_date and p_date < start_date:
                    continue
                if end_date and p_date > end_date:
                    continue

                prices.append({
                    "date": p_date.isoformat(),
                    "value": str(p.value),
                    "currency": p.currency.mnemonic,
                    "type": p.type,
                    "source": p.source,
                })

            # Sort by date descending
            prices.sort(key=lambda x: x["date"], reverse=True)

            return prices

    def get_latest_price(
        self,
        commodity: str,
        namespace: str,
        currency: str = "USD",
    ) -> dict | None:
        """Get the most recent price for a commodity.

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX").
            namespace: Namespace of the commodity (e.g., "FUND").
            currency: Currency for the price. Default "USD".

        Returns:
            Price dict with date, value, type, and source, or None if no price exists.

        Raises:
            ValueError: If commodity not found.
        """
        with self.open(readonly=True) as book:
            comm = self._find_commodity(book, commodity, namespace)
            if not comm:
                raise ValueError(
                    f"Commodity not found: {namespace}:{commodity}"
                )

            latest = None
            latest_date = None
            for p in book.prices:
                if p.commodity != comm:
                    continue
                if p.currency.mnemonic != currency:
                    continue
                p_date = _to_date(p.date)
                if latest_date is None or p_date > latest_date:
                    latest_date = p_date
                    latest = p

            if latest is None:
                return None

            return {
                "date": latest_date.isoformat(),
                "value": str(latest.value),
                "currency": latest.currency.mnemonic,
                "type": latest.type,
                "source": latest.source,
            }

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
                    balance += split.quantity

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
            transaction = self._find_transaction(book, guid)
            if transaction:
                return _transaction_to_dict(transaction)
            return None

    def create_transaction(
        self,
        description: str,
        splits: list[dict],
        trans_date: date | None = None,
        currency: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """Create a new transaction with splits.

        Args:
            description: Transaction description.
            splits: List of splits, each with:
                - 'account' (required): Full account path.
                - 'amount' (required): Value in transaction currency.
                - 'quantity' (optional): Amount in account's commodity.
                  Required if account commodity differs from transaction currency.
                - 'memo' (optional): Split memo.
            trans_date: Transaction date. Defaults to today.
            currency: ISO currency code for the transaction (e.g., "USD", "EUR").
                      Defaults to book's default currency.
            notes: Transaction notes (optional). Free-text annotation
                   stored separately from the description.

        Returns:
            Dict with 'guid' and 'status' keys.

        Raises:
            ValueError: If splits don't balance, fewer than 2 splits,
                       accounts don't exist, or cross-currency splits
                       are missing quantity.
        """
        if len(splits) < 2:
            raise ValueError("Transaction must have at least 2 splits")

        # Validate splits balance to zero (using "amount" as value)
        total = Decimal("0")
        for split in splits:
            total += Decimal(split["amount"])
        if total != Decimal("0"):
            raise ValueError(f"Splits do not balance: total is {total}")

        if trans_date is None:
            trans_date = date.today()

        with self.open(readonly=False) as book:
            # Determine transaction currency
            if currency is None:
                trans_currency = book.default_currency
            else:
                trans_currency = self._get_or_create_currency(book, currency)

            # Validate all accounts exist and build split list
            piecash_splits = []
            for split in splits:
                account = self._find_account(book, split["account"])
                if not account:
                    raise ValueError(f"Account not found: {split['account']}")

                value = Decimal(split["amount"])

                # Determine quantity
                if account.commodity == trans_currency:
                    # Same currency: quantity equals value
                    quantity = value
                elif "quantity" in split:
                    # Cross-currency: use provided quantity
                    quantity = Decimal(split["quantity"])
                    # Validate same sign (or zero)
                    if quantity * value < 0:
                        raise ValueError(
                            f"Split for '{split['account']}': quantity and value "
                            f"must have same sign "
                            f"(got value={value}, quantity={quantity})"
                        )
                else:
                    # Cross-currency but no quantity provided
                    raise ValueError(
                        f"Split for '{split['account']}' requires 'quantity' "
                        f"because account commodity "
                        f"({account.commodity.mnemonic}) differs from "
                        f"transaction currency ({trans_currency.mnemonic})"
                    )

                piecash_splits.append(
                    piecash.Split(
                        account=account,
                        value=value,
                        quantity=quantity,
                        memo=split.get("memo", ""),
                    )
                )

            # Create transaction
            transaction = piecash.Transaction(
                currency=trans_currency,
                description=description,
                notes=notes,
                post_date=trans_date,
                splits=piecash_splits,
            )

            book.save()
            return {"guid": transaction.guid, "status": "created"}

    def search_transactions(self, query: str, field: str = "description") -> list[dict]:
        """Search transactions by field.

        Args:
            query: Search string. For 'amount' field, supports:
                   - Exact: "100.00"
                   - Greater than: ">100"
                   - Less than: "<100"
                   - Range: "100-200"
            field: Field to search: 'description', 'memo', 'notes',
                   or 'amount'.

        Returns:
            List of matching transactions.

        Raises:
            ValueError: If field is not valid.
        """
        if field not in ("description", "memo", "notes", "amount"):
            raise ValueError(f"Invalid search field: {field}")

        with self.open(readonly=True) as book:
            results = []

            for transaction in book.transactions:
                if field == "description":
                    if query.lower() in transaction.description.lower():
                        results.append(_transaction_to_dict(transaction))

                elif field == "notes":
                    if transaction.notes and query.lower() in transaction.notes.lower():
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

        Raises:
            ValueError: If the amount query is malformed.
        """
        # Get absolute values of all splits
        amounts = [abs(split.value) for split in transaction.splits]

        # Parse query
        query = query.strip()

        try:
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

        except InvalidOperation as e:
            raise ValueError(f"Invalid amount query '{query}': {e}") from e

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
        commodity: str | None = None,
        commodity_namespace: str = "CURRENCY",
    ) -> dict:
        """Create a new account in the chart of accounts.

        Args:
            name: Account name (e.g., "AI Subscriptions").
            account_type: GnuCash account type (ASSET, EXPENSE, etc.).
            parent: Full path of parent account (e.g., "Expenses:Online Services").
            description: Optional description.
            placeholder: If True, account is container-only. Default False.
            commodity: ISO currency code (e.g., "USD", "EUR") or commodity mnemonic.
                       Defaults to book's default currency.
            commodity_namespace: Commodity namespace for non-currency commodities.
                                Default "CURRENCY".

        Returns:
            Dict with guid, fullname, and status.

        Raises:
            ValueError: If parent not found, invalid type, duplicate name,
                       or invalid commodity.
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

            # Determine commodity
            if commodity is None:
                account_commodity = book.default_currency
            elif commodity_namespace == "CURRENCY":
                account_commodity = self._get_or_create_currency(book, commodity)
            else:
                account_commodity = self._find_commodity(
                    book, commodity, commodity_namespace
                )
                if not account_commodity:
                    raise ValueError(
                        f"Commodity not found: {commodity_namespace}:{commodity}"
                    )

            # Create the account
            new_account = piecash.Account(
                name=name,
                type=account_type.upper(),
                parent=parent_account,
                commodity=account_commodity,
                description=description,
                placeholder=placeholder,
            )

            book.save()

            return {
                "guid": new_account.guid,
                "fullname": new_account.fullname,
                "status": "created",
            }

    def update_account(
        self,
        name: str,
        new_name: str | None = None,
        description: str | None = None,
        placeholder: bool | None = None,
    ) -> dict:
        """Update an existing account's properties.

        Args:
            name: Full account path to update (e.g., "Expenses:Groceries").
            new_name: New name for the account (just the name, not full path).
            description: New description.
            placeholder: New placeholder status.

        Returns:
            Dict with updated account details.

        Raises:
            ValueError: If account not found or new name conflicts.
        """
        with self.open(readonly=False) as book:
            account = self._find_account(book, name)
            if not account:
                raise ValueError(f"Account not found: {name}")

            # Check for name conflict if renaming
            if new_name and new_name != account.name:
                if account.parent:
                    for sibling in account.parent.children:
                        if sibling.name == new_name and sibling.guid != account.guid:
                            raise ValueError(
                                f"Account '{new_name}' already exists under "
                                f"'{account.parent.fullname}'"
                            )
                account.name = new_name

            if description is not None:
                account.description = description

            if placeholder is not None:
                account.placeholder = placeholder

            book.save()

            return _account_to_dict(account) | {"status": "updated"}

    def move_account(self, name: str, new_parent: str) -> dict:
        """Move an account to a new parent in the hierarchy.

        Args:
            name: Full account path to move (e.g., "Expenses:Old:Account").
            new_parent: Full path of the new parent account.

        Returns:
            Dict with updated account details including new fullname.

        Raises:
            ValueError: If account or parent not found, or would create cycle.
        """
        with self.open(readonly=False) as book:
            account = self._find_account(book, name)
            if not account:
                raise ValueError(f"Account not found: {name}")

            new_parent_account = self._find_account(book, new_parent)
            if not new_parent_account:
                raise ValueError(f"Parent account not found: {new_parent}")

            # Check for circular reference (can't move to self or descendant)
            check = new_parent_account
            while check:
                if check.guid == account.guid:
                    raise ValueError(
                        f"Cannot move account under itself or its descendants"
                    )
                check = check.parent

            # Check for name conflict in new location
            for sibling in new_parent_account.children:
                if sibling.name == account.name:
                    raise ValueError(
                        f"Account '{account.name}' already exists under '{new_parent}'"
                    )

            account.parent = new_parent_account

            book.save()

            return _account_to_dict(account) | {"status": "moved"}

    def delete_account(self, name: str) -> dict:
        """Delete an account from the chart of accounts.

        Args:
            name: Full account path to delete.

        Returns:
            Dict with deleted account info and status.

        Raises:
            ValueError: If account not found, has children, or has transactions.
        """
        with self.open(readonly=False) as book:
            account = self._find_account(book, name)
            if not account:
                raise ValueError(f"Account not found: {name}")

            # Safeguard: Check for children
            if account.children:
                child_names = [c.name for c in account.children]
                raise ValueError(
                    f"Cannot delete account with children: {', '.join(child_names)}"
                )

            # Safeguard: Check for transactions (splits)
            if account.splits:
                raise ValueError(
                    f"Cannot delete account with {len(account.splits)} transaction(s). "
                    f"Move or delete transactions first."
                )

            # Capture info before deletion
            result = {
                "guid": account.guid,
                "fullname": account.fullname,
                "status": "deleted",
            }

            book.session.delete(account)
            book.save()

            return result

    def delete_transaction(self, guid: str, force: bool = False) -> dict:
        """Delete a transaction by GUID.

        Args:
            guid: Transaction GUID (32-character hex string).
            force: If True, allow deleting transactions with reconciled splits.

        Returns:
            Dict with guid, description, and status.

        Raises:
            ValueError: If transaction not found, or has reconciled splits
                       and force is False.
        """
        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # Check for reconciled splits
            reconciled = [
                s for s in transaction.splits if s.reconcile_state == "y"
            ]
            if reconciled and not force:
                acct_names = ", ".join(s.account.fullname for s in reconciled)
                raise ValueError(
                    f"Transaction has reconciled splits in: {acct_names}. "
                    f"Deleting will break reconciliation. Use force=true to override."
                )

            # Capture info before deletion
            result = {
                "guid": guid,
                "description": transaction.description,
                "status": "deleted",
            }
            if reconciled:
                result["reconciled_splits_affected"] = len(reconciled)

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
        notes: str | None = None,
        force: bool = False,
    ) -> dict:
        """Update an existing transaction.

        Args:
            guid: Transaction GUID to update.
            description: New description (optional).
            trans_date: New transaction date (optional).
            splits: List of split updates with 'account', 'amount', and
                    optionally 'quantity' (optional). Must match existing
                    splits by account name. For cross-currency splits,
                    'quantity' is required when the account commodity differs
                    from the transaction currency.
            notes: New transaction notes (optional). Pass empty string
                   to clear existing notes.
            force: If True, allow modifying transactions with reconciled
                   splits. Only checked when splits are being updated.

        Returns:
            Dict with updated transaction details.

        Raises:
            ValueError: If transaction not found, splits don't balance,
                       account not found in splits, cross-currency split
                       missing quantity, or has reconciled splits and
                       force is False.
        """
        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # Check for reconciled splits when modifying splits
            if splits is not None:
                reconciled = [
                    s for s in transaction.splits if s.reconcile_state == "y"
                ]
                if reconciled and not force:
                    acct_names = ", ".join(
                        s.account.fullname for s in reconciled
                    )
                    raise ValueError(
                        f"Transaction has reconciled splits in: {acct_names}. "
                        f"Modifying will break reconciliation. "
                        f"Use force=true to override."
                    )

            # Update description if provided
            if description is not None:
                transaction.description = description

            # Update notes if provided
            if notes is not None:
                transaction.notes = notes if notes else None

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

                # Build a map of account -> split data
                split_updates = {s["account"]: s for s in splits}

                trans_currency = transaction.currency

                # Update existing splits
                for split in transaction.splits:
                    account_name = split.account.fullname
                    if account_name in split_updates:
                        update = split_updates[account_name]
                        new_value = Decimal(update["amount"])
                        split.value = new_value

                        # Determine quantity
                        if split.account.commodity == trans_currency:
                            split.quantity = new_value
                        elif "quantity" in update:
                            new_quantity = Decimal(update["quantity"])
                            if new_quantity * new_value < 0:
                                raise ValueError(
                                    f"Split for '{account_name}': quantity and value "
                                    f"must have same sign "
                                    f"(got value={new_value}, quantity={new_quantity})"
                                )
                            split.quantity = new_quantity
                        else:
                            raise ValueError(
                                f"Split for '{account_name}' requires 'quantity' "
                                f"because account commodity "
                                f"({split.account.commodity.mnemonic}) differs from "
                                f"transaction currency ({trans_currency.mnemonic})"
                            )

                        # Update memo if provided
                        if "memo" in update:
                            split.memo = update["memo"]

                        del split_updates[account_name]

                # Check if all provided accounts were found
                if split_updates:
                    missing = list(split_updates.keys())[0]
                    raise ValueError(f"Account not found in transaction: {missing}")

            book.save()

            return _transaction_to_dict(transaction) | {"status": "updated"}

    def _find_split(self, book: piecash.Book, guid: str) -> piecash.Split | None:
        """Find a split by GUID.

        Args:
            book: Open piecash book.
            guid: Split GUID.

        Returns:
            Split if found, None otherwise.
        """
        for transaction in book.transactions:
            for split in transaction.splits:
                if split.guid == guid:
                    return split
        return None

    # Valid reconcile states
    VALID_RECONCILE_STATES = {"n", "c", "y"}  # new, cleared, reconciled

    def set_reconcile_state(
        self,
        split_guid: str,
        state: str,
        reconcile_date: date | None = None,
    ) -> dict:
        """Set the reconciliation state for a split.

        Args:
            split_guid: GUID of the split to update.
            state: New reconcile state ('n'=new, 'c'=cleared, 'y'=reconciled).
            reconcile_date: Date of reconciliation. Required if state is 'y',
                           defaults to today if not provided.

        Returns:
            Dict with split details and status.

        Raises:
            ValueError: If split not found or invalid state.
        """
        state = state.lower()
        if state not in self.VALID_RECONCILE_STATES:
            raise ValueError(
                f"Invalid reconcile state: {state}. "
                f"Valid states: 'n' (new), 'c' (cleared), 'y' (reconciled)"
            )

        with self.open(readonly=False) as book:
            split = self._find_split(book, split_guid)
            if not split:
                raise ValueError(f"Split not found: {split_guid}")

            # Set the reconcile state
            split.reconcile_state = state

            # Handle reconcile date
            if state == "y":
                if reconcile_date:
                    split.reconcile_date = datetime.combine(
                        reconcile_date, datetime.min.time()
                    )
                else:
                    split.reconcile_date = datetime.now()
            elif state == "n":
                # Clear the reconcile date when unmarking
                split.reconcile_date = None

            book.save()

            return {
                "split_guid": split_guid,
                "account": split.account.fullname,
                "amount": str(split.quantity),
                "reconcile_state": state,
                "reconcile_date": split.reconcile_date.isoformat() if split.reconcile_date else None,
                "status": "updated",
            }

    def get_unreconciled_splits(
        self,
        account_name: str,
        as_of_date: date | None = None,
    ) -> dict:
        """Get all unreconciled splits for an account.

        Args:
            account_name: Full account path.
            as_of_date: Only include splits on or before this date.

        Returns:
            Dict with account info, splits list, and running totals.

        Raises:
            ValueError: If account not found.
        """
        with self.open(readonly=True) as book:
            account = self._find_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            unreconciled = []
            cleared_total = Decimal("0")
            uncleared_total = Decimal("0")

            # Get splits sorted by date
            splits = sorted(
                account.splits,
                key=lambda s: (s.transaction.post_date, s.transaction.enter_date)
            )

            for split in splits:
                # Apply date filter
                if as_of_date and split.transaction.post_date > as_of_date:
                    continue

                # Only include non-reconciled splits (n or c, not y)
                if split.reconcile_state != "y":
                    split_dict = {
                        "guid": split.guid,
                        "date": split.transaction.post_date.isoformat(),
                        "description": split.transaction.description,
                        "amount": str(split.quantity),
                        "reconcile_state": split.reconcile_state,
                        "memo": split.memo or "",
                    }
                    unreconciled.append(split_dict)

                    if split.reconcile_state == "c":
                        cleared_total += split.quantity
                    else:
                        uncleared_total += split.quantity

            return {
                "account": account_name,
                "as_of_date": as_of_date.isoformat() if as_of_date else None,
                "splits": unreconciled,
                "cleared_total": str(cleared_total),
                "uncleared_total": str(uncleared_total),
                "count": len(unreconciled),
            }

    def reconcile_account(
        self,
        account_name: str,
        statement_date: date,
        statement_balance: str,
        split_guids: list[str],
    ) -> dict:
        """Reconcile multiple splits against a statement balance.

        Args:
            account_name: Full account path.
            statement_date: Statement ending date.
            statement_balance: Expected balance from statement (as string).
            split_guids: List of split GUIDs to mark as reconciled.

        Returns:
            Dict with reconciliation results.

        Raises:
            ValueError: If account not found, split not found, or balance mismatch.
        """
        expected_balance = Decimal(statement_balance)

        with self.open(readonly=False) as book:
            account = self._find_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            # Calculate current reconciled balance
            reconciled_balance = Decimal("0")
            for split in account.splits:
                if split.reconcile_state == "y":
                    reconciled_balance += split.quantity

            # Find and validate all splits to reconcile
            splits_to_reconcile = []
            reconciling_total = Decimal("0")

            for guid in split_guids:
                split = self._find_split(book, guid)
                if not split:
                    raise ValueError(f"Split not found: {guid}")
                if split.account.fullname != account_name:
                    raise ValueError(
                        f"Split {guid} belongs to account '{split.account.fullname}', "
                        f"not '{account_name}'"
                    )
                if split.reconcile_state == "y":
                    raise ValueError(f"Split {guid} is already reconciled")

                splits_to_reconcile.append(split)
                reconciling_total += split.quantity

            # Check if balance will match
            new_balance = reconciled_balance + reconciling_total
            if new_balance != expected_balance:
                raise ValueError(
                    f"Balance mismatch: reconciled balance would be {new_balance}, "
                    f"but statement balance is {expected_balance}. "
                    f"Difference: {expected_balance - new_balance}"
                )

            # Perform reconciliation
            reconcile_datetime = datetime.combine(statement_date, datetime.min.time())
            for split in splits_to_reconcile:
                split.reconcile_state = "y"
                split.reconcile_date = reconcile_datetime

            book.save()

            return {
                "account": account_name,
                "statement_date": statement_date.isoformat(),
                "statement_balance": statement_balance,
                "splits_reconciled": len(splits_to_reconcile),
                "new_reconciled_balance": str(new_balance),
                "status": "reconciled",
            }

    def void_transaction(self, guid: str, reason: str) -> dict:
        """Void a transaction (proper accounting void, not delete).

        Voiding preserves the transaction for audit purposes but zeroes out
        all split values. Original values are stored in slots for potential
        unvoiding.

        Args:
            guid: Transaction GUID to void.
            reason: Reason for voiding (required for audit trail).

        Returns:
            Dict with transaction details and status.

        Raises:
            ValueError: If transaction not found or already voided.
        """
        if not reason or not reason.strip():
            raise ValueError("Void reason is required")

        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # Check if already voided (any split has 'v' state)
            if any(s.reconcile_state == "v" for s in transaction.splits):
                raise ValueError(f"Transaction {guid} is already voided")

            # Store void metadata in transaction slots
            # GnuCash uses these slot keys for void info
            transaction["void-reason"] = reason
            transaction["void-time"] = datetime.now().isoformat()

            # Store original values and zero out each split
            for split in transaction.splits:
                # Store original values in slots
                split["void-former-value"] = str(split.value)
                split["void-former-quantity"] = str(split.quantity)

                # Zero out the split
                split.value = Decimal("0")
                split.quantity = Decimal("0")

                # Set reconcile state to voided
                split.reconcile_state = "v"

            book.save()

            return {
                "guid": guid,
                "description": transaction.description,
                "void_reason": reason,
                "status": "voided",
            }

    def unvoid_transaction(self, guid: str) -> dict:
        """Restore a voided transaction.

        Restores original split values from stored slots and removes void markers.

        Args:
            guid: Transaction GUID to unvoid.

        Returns:
            Dict with transaction details and status.

        Raises:
            ValueError: If transaction not found or not voided.
        """
        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # Check if actually voided
            if not any(s.reconcile_state == "v" for s in transaction.splits):
                raise ValueError(f"Transaction {guid} is not voided")

            # Restore each split
            for split in transaction.splits:
                # Restore original values from slots
                former_value = split.get("void-former-value")
                former_quantity = split.get("void-former-quantity")

                if former_value is not None:
                    split.value = Decimal(former_value)
                    del split["void-former-value"]

                if former_quantity is not None:
                    split.quantity = Decimal(former_quantity)
                    del split["void-former-quantity"]

                # Reset reconcile state to new
                split.reconcile_state = "n"

            # Remove void metadata from transaction
            if "void-reason" in transaction:
                del transaction["void-reason"]
            if "void-time" in transaction:
                del transaction["void-time"]

            book.save()

            return _transaction_to_dict(transaction) | {"status": "unvoided"}

    # ============== Reporting Methods ==============

    def _get_account_depth(self, account: piecash.Account) -> int:
        """Get the depth of an account in the hierarchy (root = 0)."""
        depth = 0
        current = account
        while current.parent and current.parent.type != "ROOT":
            depth += 1
            current = current.parent
        return depth

    def _get_account_at_depth(
        self, account: piecash.Account, target_depth: int
    ) -> piecash.Account:
        """Get the ancestor of an account at a specific depth."""
        # First get to root and build path
        path = [account]
        current = account
        while current.parent and current.parent.type != "ROOT":
            current = current.parent
            path.append(current)
        path.reverse()  # Now path[0] is top-level, path[-1] is the account

        # Return account at target depth (0-indexed from top)
        if target_depth >= len(path):
            return account
        return path[target_depth]

    def spending_by_category(
        self,
        start_date: date,
        end_date: date,
        depth: int = 1,
    ) -> dict:
        """Get spending breakdown by expense category.

        Args:
            start_date: Start of period (inclusive).
            end_date: End of period (inclusive).
            depth: Hierarchy depth for grouping (1 = top-level, 2 = subcategories).

        Returns:
            Dict with period, total, and category breakdown.
        """
        with self.open(readonly=True) as book:
            totals: dict[str, Decimal] = {}

            for transaction in book.transactions:
                if not (start_date <= transaction.post_date <= end_date):
                    continue

                for split in transaction.splits:
                    if split.account.type != "EXPENSE":
                        continue

                    # Get the account at the requested depth
                    group_account = self._get_account_at_depth(
                        split.account, depth - 1
                    )
                    account_name = group_account.fullname

                    # Expense splits are positive when money is spent
                    amount = split.quantity
                    if amount > 0:
                        totals[account_name] = totals.get(
                            account_name, Decimal("0")
                        ) + amount

            # Calculate total and percentages
            total = sum(totals.values())
            categories = []
            for account_name, amount in sorted(
                totals.items(), key=lambda x: x[1], reverse=True
            ):
                percent = (
                    (amount / total * 100) if total > 0 else Decimal("0")
                )
                categories.append({
                    "account": account_name,
                    "amount": str(amount),
                    "percent": str(percent.quantize(Decimal("0.1"))),
                })

            return {
                "period": f"{start_date.isoformat()} to {end_date.isoformat()}",
                "total": str(total),
                "categories": categories,
            }

    def income_by_source(
        self,
        start_date: date,
        end_date: date,
        depth: int = 1,
    ) -> dict:
        """Get income breakdown by source.

        Args:
            start_date: Start of period (inclusive).
            end_date: End of period (inclusive).
            depth: Hierarchy depth for grouping (1 = top-level, 2 = subcategories).

        Returns:
            Dict with period, total, and source breakdown.
        """
        with self.open(readonly=True) as book:
            totals: dict[str, Decimal] = {}

            for transaction in book.transactions:
                if not (start_date <= transaction.post_date <= end_date):
                    continue

                for split in transaction.splits:
                    if split.account.type != "INCOME":
                        continue

                    # Get the account at the requested depth
                    group_account = self._get_account_at_depth(
                        split.account, depth - 1
                    )
                    account_name = group_account.fullname

                    # Income splits are negative (money coming in)
                    amount = -split.quantity
                    if amount > 0:
                        totals[account_name] = totals.get(
                            account_name, Decimal("0")
                        ) + amount

            # Calculate total and percentages
            total = sum(totals.values())
            sources = []
            for account_name, amount in sorted(
                totals.items(), key=lambda x: x[1], reverse=True
            ):
                percent = (
                    (amount / total * 100) if total > 0 else Decimal("0")
                )
                sources.append({
                    "account": account_name,
                    "amount": str(amount),
                    "percent": str(percent.quantize(Decimal("0.1"))),
                })

            return {
                "period": f"{start_date.isoformat()} to {end_date.isoformat()}",
                "total": str(total),
                "sources": sources,
            }

    def balance_sheet(self, as_of_date: date) -> dict:
        """Generate a balance sheet as of a specific date.

        Args:
            as_of_date: Date to calculate balances as of.

        Returns:
            Dict with assets, liabilities, equity sections and totals.
        """
        with self.open(readonly=True) as book:
            assets: dict[str, Decimal] = {}
            liabilities: dict[str, Decimal] = {}
            equity: dict[str, Decimal] = {}

            asset_types = {"ASSET", "BANK", "CASH", "STOCK", "MUTUAL"}
            liability_types = {"LIABILITY", "CREDIT"}
            equity_types = {"EQUITY"}

            for account in book.accounts:
                if account.type == "ROOT":
                    continue

                # Calculate balance as of date
                balance = Decimal("0")
                for split in account.splits:
                    if split.transaction.post_date <= as_of_date:
                        balance += split.quantity

                # Skip zero balances
                if balance == 0:
                    continue

                if account.type in asset_types:
                    assets[account.fullname] = balance
                elif account.type in liability_types:
                    # Liabilities are stored as negative, show as positive
                    liabilities[account.fullname] = -balance
                elif account.type in equity_types:
                    equity[account.fullname] = -balance

            # Also include net income (Income - Expenses) in equity
            net_income = Decimal("0")
            for account in book.accounts:
                if account.type in ("INCOME", "EXPENSE"):
                    for split in account.splits:
                        if split.transaction.post_date <= as_of_date:
                            net_income -= split.quantity  # Income negative, expense positive

            assets_total = sum(assets.values())
            liabilities_total = sum(liabilities.values())
            equity_total = sum(equity.values()) + net_income

            def format_accounts(accounts_dict: dict[str, Decimal]) -> list[dict]:
                return [
                    {"account": name, "balance": str(bal)}
                    for name, bal in sorted(accounts_dict.items())
                ]

            return {
                "as_of_date": as_of_date.isoformat(),
                "assets": {
                    "total": str(assets_total),
                    "accounts": format_accounts(assets),
                },
                "liabilities": {
                    "total": str(liabilities_total),
                    "accounts": format_accounts(liabilities),
                },
                "equity": {
                    "total": str(equity_total),
                    "accounts": format_accounts(equity) + (
                        [{"account": "Retained Earnings", "balance": str(net_income)}]
                        if net_income != 0 else []
                    ),
                },
                "balanced": assets_total == liabilities_total + equity_total,
            }

    def net_worth(
        self,
        end_date: date,
        start_date: date | None = None,
        interval: str | None = None,
    ) -> dict:
        """Calculate net worth (assets minus liabilities).

        Args:
            end_date: Calculate net worth as of this date.
            start_date: If provided with interval, calculate series over time.
            interval: 'month', 'quarter', or 'year' for time series.

        Returns:
            Dict with net worth value or time series.
        """
        from dateutil.relativedelta import relativedelta

        def calc_net_worth_at(book: piecash.Book, at_date: date) -> Decimal:
            """Calculate net worth at a specific date."""
            asset_types = {"ASSET", "BANK", "CASH", "STOCK", "MUTUAL"}
            liability_types = {"LIABILITY", "CREDIT"}

            total = Decimal("0")
            for account in book.accounts:
                if account.type in asset_types:
                    for split in account.splits:
                        if split.transaction.post_date <= at_date:
                            total += split.quantity
                elif account.type in liability_types:
                    for split in account.splits:
                        if split.transaction.post_date <= at_date:
                            total += split.quantity  # Already negative

            return total

        with self.open(readonly=True) as book:
            # Point-in-time calculation
            if not start_date or not interval:
                nw = calc_net_worth_at(book, end_date)
                return {
                    "as_of_date": end_date.isoformat(),
                    "net_worth": str(nw),
                }

            # Time series calculation
            if interval not in ("month", "quarter", "year"):
                raise ValueError(f"Invalid interval: {interval}. Use 'month', 'quarter', or 'year'")

            delta = {
                "month": relativedelta(months=1),
                "quarter": relativedelta(months=3),
                "year": relativedelta(years=1),
            }[interval]

            series = []
            current = start_date
            while current <= end_date:
                nw = calc_net_worth_at(book, current)
                series.append({
                    "date": current.isoformat(),
                    "net_worth": str(nw),
                })
                current += delta

            # Always include end_date if not already included
            if series and series[-1]["date"] != end_date.isoformat():
                nw = calc_net_worth_at(book, end_date)
                series.append({
                    "date": end_date.isoformat(),
                    "net_worth": str(nw),
                })

            return {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "interval": interval,
                "series": series,
            }

    def cash_flow(
        self,
        start_date: date,
        end_date: date,
        account: str | None = None,
    ) -> dict:
        """Calculate cash flow (inflows and outflows) for a period.

        Args:
            start_date: Start of period (inclusive).
            end_date: End of period (inclusive).
            account: Optional account to filter (e.g., specific bank account).

        Returns:
            Dict with inflows, outflows, and net cash flow.
        """
        with self.open(readonly=True) as book:
            # If account specified, only look at that account
            if account:
                target_account = self._find_account(book, account)
                if not target_account:
                    raise ValueError(f"Account not found: {account}")
                accounts_to_check = [target_account]
            else:
                # Default to cash/bank accounts
                cash_types = {"BANK", "CASH"}
                accounts_to_check = [
                    a for a in book.accounts if a.type in cash_types
                ]

            inflows = Decimal("0")
            outflows = Decimal("0")

            for acc in accounts_to_check:
                for split in acc.splits:
                    if not (start_date <= split.transaction.post_date <= end_date):
                        continue

                    if split.quantity > 0:
                        inflows += split.quantity
                    else:
                        outflows += -split.quantity

            return {
                "period": f"{start_date.isoformat()} to {end_date.isoformat()}",
                "account": account if account else "All cash/bank accounts",
                "inflows": str(inflows),
                "outflows": str(outflows),
                "net": str(inflows - outflows),
            }

    # ============== Budget Methods ==============

    VALID_BUDGET_PERIOD_TYPES = {"monthly", "quarterly", "weekly"}

    def list_budgets(self) -> list[dict]:
        """List all budgets in the book.

        Returns:
            List of budget dicts with guid, name, description,
            num_periods, period_type, and start_date.
        """
        from piecash.budget import Budget

        with self.open(readonly=True) as book:
            budgets = book.session.query(Budget).all()
            return [self._budget_to_dict(b) for b in budgets]

    def get_budget(self, name: str) -> dict | None:
        """Get full details of a budget including all budget amounts.

        Args:
            name: Budget name.

        Returns:
            Dict with budget info and all account/period amounts,
            or None if not found.
        """
        with self.open(readonly=True) as book:
            budget = self._find_budget(book, name)
            if not budget:
                return None

            result = self._budget_to_dict(budget)

            # Group amounts by account
            accounts: dict[str, dict[int, str]] = {}
            for ba in budget.amounts:
                acct_name = ba.account.fullname
                if acct_name not in accounts:
                    accounts[acct_name] = {}
                accounts[acct_name][ba.period_num] = str(ba.amount)

            result["accounts"] = [
                {
                    "account": acct_name,
                    "periods": periods,
                }
                for acct_name, periods in sorted(accounts.items())
            ]

            return result

    def create_budget(
        self,
        name: str,
        year: int | None = None,
        num_periods: int = 12,
        period_type: str = "monthly",
        description: str = "",
    ) -> dict:
        """Create a new budget.

        Args:
            name: Budget name (e.g., "2026 Budget").
            year: Budget year. Defaults to current year.
            num_periods: Number of periods. Default 12.
            period_type: "monthly" (default), "quarterly", or "weekly".
            description: Optional description.

        Returns:
            Dict with guid, name, and status.

        Raises:
            ValueError: If budget with same name already exists,
                       invalid period_type, or invalid num_periods.
        """
        import uuid

        from piecash._common import Recurrence
        from piecash.budget import Budget

        if period_type not in self.VALID_BUDGET_PERIOD_TYPES:
            raise ValueError(
                f"Invalid period_type: {period_type}. "
                f"Valid types: {', '.join(sorted(self.VALID_BUDGET_PERIOD_TYPES))}"
            )
        if num_periods < 1:
            raise ValueError("num_periods must be at least 1")

        if year is None:
            year = date.today().year

        recurrence_map = {
            "monthly": ("month", 1),
            "quarterly": ("month", 3),
            "weekly": ("week", 1),
        }
        rec_period_type, rec_mult = recurrence_map[period_type]

        with self.open(readonly=False) as book:
            existing = self._find_budget(book, name)
            if existing:
                raise ValueError(f"Budget already exists: {name}")

            budget_guid = uuid.uuid4().hex

            # Use direct table inserts — piecash blocks Budget
            # and Recurrence constructors (read-only ORM objects)
            book.session.execute(
                Budget.__table__.insert().values(
                    guid=budget_guid,
                    name=name,
                    description=description,
                    num_periods=num_periods,
                )
            )

            book.session.execute(
                Recurrence.__table__.insert().values(
                    obj_guid=budget_guid,
                    recurrence_mult=rec_mult,
                    recurrence_period_type=rec_period_type,
                    recurrence_period_start=date(year, 1, 1),
                    recurrence_weekend_adjust="none",
                )
            )

            book.save()

            return {
                "guid": budget_guid,
                "name": name,
                "status": "created",
            }

    def set_budget_amount(
        self,
        budget_name: str,
        account: str,
        amount: str,
        period: int | str | None = None,
    ) -> dict:
        """Set a budget target for an account.

        Args:
            budget_name: Name of the budget.
            account: Full account path (e.g., "Expenses:Groceries").
            amount: Budget amount as string (e.g., "500.00").
            period: Which period(s) to set:
                - None or "all": All periods (default)
                - Integer 0..N-1: Specific period
                - "q1", "q2", "q3", "q4": All periods in quarter

        Returns:
            Dict with budget, account, amount, periods set, and status.

        Raises:
            ValueError: If budget not found, account not found,
                       or invalid period.
        """
        from piecash.budget import BudgetAmount

        amount_decimal = Decimal(amount)

        with self.open(readonly=False) as book:
            budget = self._find_budget(book, budget_name)
            if not budget:
                raise ValueError(f"Budget not found: {budget_name}")

            acct = self._find_account(book, account)
            if not acct:
                raise ValueError(f"Account not found: {account}")

            periods = self._resolve_periods(budget, period)

            # Convert Decimal to num/denom for direct inserts
            # Multiply by 100 for 2 decimal places
            amount_denom = 100
            amount_num = int(amount_decimal * amount_denom)

            for p in periods:
                try:
                    existing = budget.amounts(
                        account=acct, period_num=p
                    )
                    # Update existing amount
                    existing.amount = amount_decimal
                except KeyError:
                    # No existing amount — insert via table
                    # (BudgetAmount constructor is blocked by piecash)
                    book.session.execute(
                        BudgetAmount.__table__.insert().values(
                            budget_guid=budget.guid,
                            account_guid=acct.guid,
                            period_num=p,
                            amount_num=amount_num,
                            amount_denom=amount_denom,
                        )
                    )

            book.save()

            return {
                "budget": budget_name,
                "account": account,
                "amount": amount,
                "periods_set": periods,
                "status": "updated",
            }

    def get_budget_report(
        self,
        budget_name: str,
        period: int | str | None = None,
        account: str | None = None,
        include_children: bool = True,
    ) -> dict:
        """Compare actual spending against budget.

        Args:
            budget_name: Name of the budget.
            period: Which period to report:
                - None: Current period based on today's date (default)
                - Integer 0-N: Specific period
                - "ytd": Year to date (all periods up to current)
                - "all": All periods
            account: Optional filter to specific account or parent.
            include_children: If True and account specified, include
                            child accounts. Default True.

        Returns:
            Dict with budget name, period info, account breakdown
            (budgeted, actual, remaining, percent_used), and totals.

        Raises:
            ValueError: If budget not found, invalid period, or
                       account not found.
        """
        with self.open(readonly=True) as book:
            budget = self._find_budget(book, budget_name)
            if not budget:
                raise ValueError(f"Budget not found: {budget_name}")

            # Resolve which periods to report on
            if period is None:
                current = self._current_period(budget)
                if current is None:
                    raise ValueError(
                        "Today's date is outside the budget period range"
                    )
                report_periods = [current]
            elif period == "ytd":
                current = self._current_period(budget)
                if current is None:
                    raise ValueError(
                        "Today's date is outside the budget period range"
                    )
                report_periods = list(range(current + 1))
            elif period == "all":
                report_periods = list(range(budget.num_periods))
            elif isinstance(period, int):
                if period < 0 or period >= budget.num_periods:
                    raise ValueError(
                        f"Period {period} out of range "
                        f"(0-{budget.num_periods - 1})"
                    )
                report_periods = [period]
            else:
                raise ValueError(f"Invalid period: {period}")

            # Calculate date range
            first_start, _ = self._period_to_date_range(
                budget, report_periods[0]
            )
            _, last_end = self._period_to_date_range(
                budget, report_periods[-1]
            )

            # Determine target accounts
            if account:
                filter_acct = self._find_account(book, account)
                if not filter_acct:
                    raise ValueError(f"Account not found: {account}")

                if include_children:
                    target_accounts = set()
                    self._collect_descendants(filter_acct, target_accounts)
                    target_accounts.add(filter_acct)
                else:
                    target_accounts = {filter_acct}
            else:
                target_accounts = None

            # Gather budgeted amounts
            budgeted: dict[str, Decimal] = {}
            for ba in budget.amounts:
                if ba.period_num not in report_periods:
                    continue
                acct_name = ba.account.fullname
                if target_accounts is not None and ba.account not in target_accounts:
                    continue
                budgeted[acct_name] = budgeted.get(
                    acct_name, Decimal("0")
                ) + ba.amount

            # Calculate actuals from transactions
            actuals: dict[str, Decimal] = {}
            for transaction in book.transactions:
                if not (first_start <= transaction.post_date <= last_end):
                    continue
                for split in transaction.splits:
                    acct_name = split.account.fullname
                    if acct_name not in budgeted:
                        continue
                    amount = split.quantity
                    if split.account.type == "EXPENSE" and amount > 0:
                        actuals[acct_name] = actuals.get(
                            acct_name, Decimal("0")
                        ) + amount
                    elif split.account.type == "INCOME" and amount < 0:
                        actuals[acct_name] = actuals.get(
                            acct_name, Decimal("0")
                        ) + (-amount)

            # Build results
            accounts_result = []
            total_budgeted = Decimal("0")
            total_actual = Decimal("0")

            for acct_name in sorted(budgeted.keys()):
                b = budgeted[acct_name]
                a = actuals.get(acct_name, Decimal("0"))
                remaining = b - a
                pct = (
                    (a / b * 100).quantize(Decimal("0.1"))
                    if b > 0
                    else Decimal("0")
                )

                accounts_result.append({
                    "account": acct_name,
                    "budgeted": str(b),
                    "actual": str(a),
                    "remaining": str(remaining),
                    "percent_used": str(pct),
                })

                total_budgeted += b
                total_actual += a

            total_remaining = total_budgeted - total_actual
            total_pct = (
                (total_actual / total_budgeted * 100).quantize(
                    Decimal("0.1")
                )
                if total_budgeted > 0
                else Decimal("0")
            )

            # Period info string
            if len(report_periods) == 1:
                p_start, p_end = self._period_to_date_range(
                    budget, report_periods[0]
                )
                period_info = (
                    f"Period {report_periods[0]} "
                    f"({p_start.isoformat()} to {p_end.isoformat()})"
                )
            else:
                period_info = (
                    f"Periods {report_periods[0]}-{report_periods[-1]} "
                    f"({first_start.isoformat()} to {last_end.isoformat()})"
                )

            return {
                "budget": budget_name,
                "period": period_info,
                "accounts": accounts_result,
                "totals": {
                    "budgeted": str(total_budgeted),
                    "actual": str(total_actual),
                    "remaining": str(total_remaining),
                    "percent_used": str(total_pct),
                },
            }

    def delete_budget(self, name: str) -> dict:
        """Delete a budget.

        Args:
            name: Budget name.

        Returns:
            Dict with name, guid, and status.

        Raises:
            ValueError: If budget not found.
        """
        with self.open(readonly=False) as book:
            budget = self._find_budget(book, name)
            if not budget:
                raise ValueError(f"Budget not found: {name}")

            result = {
                "name": name,
                "guid": budget.guid,
                "status": "deleted",
            }

            book.session.delete(budget)
            book.save()

            return result

    # ============== Scheduled Transaction Methods ==============

    def create_scheduled_transaction(
        self,
        name: str,
        description: str,
        splits: list[dict],
        start_date: str,
        frequency: str,
        end_date: str | None = None,
        enabled: bool = True,
    ) -> dict:
        """Create a recurring transaction template.

        Args:
            name: Name for the scheduled transaction.
            description: Transaction description when created.
            splits: List of splits, same format as create_transaction:
                [{"account": "Expenses:Rent", "amount": "1850.00"}, ...]
            start_date: First occurrence date (YYYY-MM-DD).
            frequency: How often: "weekly", "biweekly", "monthly",
                      "quarterly", "yearly".
            end_date: Optional last occurrence date (YYYY-MM-DD).
            enabled: Whether active. Default True.

        Returns:
            Dict with guid, name, next_occurrence, and status.

        Raises:
            ValueError: If invalid frequency, accounts not found,
                       or splits don't balance.
        """
        import json
        import uuid

        from piecash._common import Recurrence
        from piecash.core.transaction import ScheduledTransaction
        from piecash.kvp import KVP_Type, Slot

        if frequency not in self.VALID_FREQUENCIES:
            raise ValueError(
                f"Invalid frequency: {frequency}. "
                f"Valid: {', '.join(sorted(self.VALID_FREQUENCIES))}"
            )

        parsed_start = date.fromisoformat(start_date)
        parsed_end = (
            date.fromisoformat(end_date) if end_date else None
        )

        # Validate splits balance to zero
        total = Decimal("0")
        for s in splits:
            total += Decimal(s["amount"])
        if total != 0:
            raise ValueError(
                f"Splits must balance to zero (total: {total})"
            )

        rec_period_type, rec_mult = self.FREQUENCY_TO_RECURRENCE[
            frequency
        ]

        with self.open(readonly=False) as book:
            # Validate all account names exist
            for s in splits:
                acct = self._find_account(book, s["account"])
                if not acct:
                    raise ValueError(
                        f"Account not found: {s['account']}"
                    )

            # Check duplicate name
            for sx in book.session.query(
                ScheduledTransaction
            ).all():
                if sx.name == name:
                    raise ValueError(
                        f"Scheduled transaction already exists: "
                        f"{name}"
                    )

            sx_guid = uuid.uuid4().hex

            # Create template account under root_template
            template_acct = piecash.Account(
                name=name,
                type="BANK",
                parent=book.root_template,
                commodity=book.default_currency,
            )
            book.session.add(template_acct)
            book.session.flush()

            # Insert ScheduledTransaction (blocked constructor)
            book.session.execute(
                ScheduledTransaction.__table__.insert().values(
                    guid=sx_guid,
                    name=name,
                    enabled=1 if enabled else 0,
                    start_date=parsed_start,
                    end_date=parsed_end,
                    last_occur=None,
                    num_occur=0,
                    rem_occur=0,
                    auto_create=0,
                    auto_notify=0,
                    adv_creation=0,
                    adv_notify=0,
                    instance_count=0,
                    template_act_guid=template_acct.guid,
                )
            )

            # Insert Recurrence
            book.session.execute(
                Recurrence.__table__.insert().values(
                    obj_guid=sx_guid,
                    recurrence_mult=rec_mult,
                    recurrence_period_type=rec_period_type,
                    recurrence_period_start=parsed_start,
                    recurrence_weekend_adjust="none",
                )
            )

            # Store split templates as JSON in a slot
            splits_json = json.dumps([
                {
                    "account": s["account"],
                    "amount": s["amount"],
                    "memo": s.get("memo", ""),
                }
                for s in splits
            ])
            book.session.execute(
                Slot.__table__.insert().values(
                    obj_guid=sx_guid,
                    name="splits-json",
                    slot_type=KVP_Type.KVP_TYPE_STRING,
                    string_val=splits_json,
                )
            )

            book.save()

            next_occ = self._next_occurrence(
                parsed_start, frequency,
                after=date.today() - timedelta(days=1),
                end_date=parsed_end,
            )

            return {
                "guid": sx_guid,
                "name": name,
                "frequency": frequency,
                "next_occurrence": (
                    next_occ.isoformat() if next_occ else None
                ),
                "status": "created",
            }

    def list_scheduled_transactions(
        self,
        enabled_only: bool = True,
    ) -> list[dict]:
        """List all scheduled transactions.

        Args:
            enabled_only: If True, only show enabled schedules.
                         Default True.

        Returns:
            List of scheduled transaction dicts.
        """
        from piecash.core.transaction import ScheduledTransaction

        with self.open(readonly=True) as book:
            all_sx = book.session.query(
                ScheduledTransaction
            ).all()

            results = []
            for sx in all_sx:
                if enabled_only and not sx.enabled:
                    continue
                d = self._sx_to_dict(sx)
                d["splits"] = self._get_sx_splits(book, sx)
                results.append(d)

            return results

    def get_upcoming_transactions(
        self,
        days: int = 14,
    ) -> list[dict]:
        """Get scheduled transactions due within a time window.

        Args:
            days: Look ahead window in days. Default 14.

        Returns:
            List of upcoming occurrences sorted by date.
        """
        from piecash.core.transaction import ScheduledTransaction

        today = date.today()
        window_end = today + timedelta(days=days)

        with self.open(readonly=True) as book:
            all_sx = book.session.query(
                ScheduledTransaction
            ).all()

            upcoming = []
            for sx in all_sx:
                if not sx.enabled:
                    continue

                rec = sx.recurrence
                key = (
                    rec.recurrence_period_type,
                    rec.recurrence_mult,
                )
                frequency = self.RECURRENCE_TO_FREQUENCY.get(
                    key, None
                )
                if not frequency:
                    continue

                start = sx.start_date
                if isinstance(start, datetime):
                    start = start.date()

                end = sx.end_date
                if isinstance(end, datetime):
                    end = end.date()

                next_occ = self._next_occurrence(
                    start, frequency,
                    after=today - timedelta(days=1),
                    end_date=end,
                )

                if next_occ and next_occ <= window_end:
                    splits = self._get_sx_splits(book, sx)

                    # Calculate total amount (sum of positive splits)
                    total = Decimal("0")
                    for s in splits:
                        amt = Decimal(s["amount"])
                        if amt > 0:
                            total += amt

                    upcoming.append({
                        "guid": sx.guid,
                        "name": sx.name,
                        "occurrence_date": next_occ.isoformat(),
                        "days_until": (next_occ - today).days,
                        "amount": str(total),
                        "splits": splits,
                    })

            # Sort by occurrence date
            upcoming.sort(key=lambda x: x["occurrence_date"])
            return upcoming

    def create_transaction_from_scheduled(
        self,
        guid: str,
        transaction_date: str | None = None,
    ) -> dict:
        """Create an actual transaction from a scheduled template.

        Args:
            guid: Scheduled transaction GUID.
            transaction_date: Date for the transaction (YYYY-MM-DD).
                            Defaults to next occurrence date.

        Returns:
            Dict with created transaction guid and updated schedule.

        Raises:
            ValueError: If scheduled transaction not found or disabled.
        """
        from piecash.core.transaction import ScheduledTransaction

        with self.open(readonly=False) as book:
            sx = book.session.query(
                ScheduledTransaction
            ).filter_by(guid=guid).first()
            if not sx:
                raise ValueError(
                    f"Scheduled transaction not found: {guid}"
                )
            if not sx.enabled:
                raise ValueError(
                    "Scheduled transaction is disabled"
                )

            rec = sx.recurrence
            key = (
                rec.recurrence_period_type,
                rec.recurrence_mult,
            )
            frequency = self.RECURRENCE_TO_FREQUENCY.get(key)
            if not frequency:
                raise ValueError("Unknown recurrence frequency")

            start = sx.start_date
            if isinstance(start, datetime):
                start = start.date()

            end = sx.end_date
            if isinstance(end, datetime):
                end = end.date()

            # Determine transaction date
            if transaction_date:
                txn_date = date.fromisoformat(transaction_date)
            else:
                txn_date = self._next_occurrence(
                    start, frequency,
                    after=date.today() - timedelta(days=1),
                    end_date=end,
                )
                if not txn_date:
                    raise ValueError(
                        "No upcoming occurrence (past end date)"
                    )

            # Get split templates
            splits = self._get_sx_splits(book, sx)
            if not splits:
                raise ValueError(
                    "No split templates found for scheduled "
                    "transaction"
                )

            # Update schedule tracking
            sx.last_occur = txn_date
            sx.instance_count += 1

            # Capture values before session closes
            sx_name = sx.name
            instance_count = sx.instance_count

            book.save()

        # Create the actual transaction (separate session)
        txn_result = self.create_transaction(
            description=sx_name,
            splits=splits,
            trans_date=txn_date,
        )

        return {
            "transaction_guid": txn_result["guid"],
            "scheduled_transaction": sx_name,
            "transaction_date": txn_date.isoformat(),
            "instance_count": instance_count,
            "status": "created",
        }

    def update_scheduled_transaction(
        self,
        guid: str,
        enabled: bool | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Update a scheduled transaction.

        Args:
            guid: Scheduled transaction GUID.
            enabled: Enable or disable.
            end_date: Set end date (YYYY-MM-DD), or empty string
                     to clear.

        Returns:
            Dict with updated scheduled transaction details.

        Raises:
            ValueError: If not found.
        """
        from piecash.core.transaction import ScheduledTransaction

        with self.open(readonly=False) as book:
            sx = book.session.query(
                ScheduledTransaction
            ).filter_by(guid=guid).first()
            if not sx:
                raise ValueError(
                    f"Scheduled transaction not found: {guid}"
                )

            if enabled is not None:
                sx.enabled = 1 if enabled else 0

            if end_date is not None:
                if end_date == "":
                    sx.end_date = None
                else:
                    sx.end_date = date.fromisoformat(end_date)

            book.save()

            return self._sx_to_dict(sx)

    def delete_scheduled_transaction(self, guid: str) -> dict:
        """Delete a scheduled transaction.

        Does not affect transactions already created from this
        schedule.

        Args:
            guid: Scheduled transaction GUID.

        Returns:
            Dict with name, guid, and status.

        Raises:
            ValueError: If not found.
        """
        from sqlalchemy import text

        from piecash.core.transaction import ScheduledTransaction

        with self.open(readonly=False) as book:
            sx = book.session.query(
                ScheduledTransaction
            ).filter_by(guid=guid).first()
            if not sx:
                raise ValueError(
                    f"Scheduled transaction not found: {guid}"
                )

            result = {
                "name": sx.name,
                "guid": sx.guid,
                "status": "deleted",
            }

            # Delete the splits-json slot via raw SQL
            book.session.execute(
                text(
                    "DELETE FROM slots "
                    "WHERE obj_guid = :guid AND name = :name"
                ),
                {"guid": sx.guid, "name": "splits-json"},
            )

            # Delete the template account
            template_acct = sx.template_account
            book.session.delete(sx)
            if template_acct:
                book.session.delete(template_acct)

            book.save()

            return result

    # ── Lot (Cost Basis Tracking) Methods ─────────────────────────

    def _find_lot(self, book: piecash.Book, guid: str):
        """Find a lot by GUID.

        Args:
            book: Open piecash book.
            guid: Lot GUID.

        Returns:
            Lot if found, None otherwise.
        """
        from piecash.core.transaction import Lot

        return book.session.query(Lot).filter_by(guid=guid).first()

    def _lot_summary(self, lot) -> dict:
        """Compute current state of a lot from its splits.

        Returns:
            Dict with quantity, cost_basis, cost_per_share as strings.
        """
        purchase_quantity = Decimal(0)
        purchase_value = Decimal(0)
        sale_quantity = Decimal(0)

        for split in lot.splits:
            if split.quantity > 0:
                purchase_quantity += Decimal(str(split.quantity))
                purchase_value += Decimal(str(split.value))
            else:
                sale_quantity += abs(Decimal(str(split.quantity)))

        remaining = purchase_quantity - sale_quantity

        if purchase_quantity > 0:
            cost_per_share = purchase_value / purchase_quantity
            remaining_cost_basis = cost_per_share * remaining
        else:
            cost_per_share = Decimal(0)
            remaining_cost_basis = Decimal(0)

        return {
            "quantity": str(remaining),
            "cost_basis": str(remaining_cost_basis),
            "cost_per_share": str(cost_per_share),
            "is_closed": bool(lot.is_closed),
        }

    def create_lot(
        self,
        account: str,
        title: str,
        notes: str = "",
    ) -> dict:
        """Create a new lot for cost basis tracking.

        Lots group investment purchases for tracking cost basis and
        calculating capital gains when selling.

        Args:
            account: Full path of investment account (e.g., "Assets:Investments:VTSAX").
            title: Lot identifier (e.g., "VTSAX 2026-01-15 purchase").
            notes: Optional notes.

        Returns:
            Dict with guid, title, account, and status.

        Raises:
            ValueError: If account not found.
        """
        from piecash.core.transaction import Lot

        with self.open(readonly=False) as book:
            acct = self._find_account(book, account)
            if not acct:
                raise ValueError(f"Account not found: {account}")

            lot = Lot(
                title=title,
                account=acct,
                notes=notes,
                is_closed=0,
            )
            book.session.add(lot)
            book.save()

            return {
                "guid": lot.guid,
                "title": title,
                "account": account,
                "notes": notes,
                "status": "created",
            }

    def list_lots(
        self,
        account: str,
        include_closed: bool = False,
    ) -> list[dict]:
        """List all lots for an investment account.

        Args:
            account: Full path of investment account.
            include_closed: If True, include fully-sold lots. Default False.

        Returns:
            List of lot dicts with guid, title, notes, is_closed,
            quantity, cost_basis, cost_per_share.

        Raises:
            ValueError: If account not found.
        """
        with self.open(readonly=True) as book:
            acct = self._find_account(book, account)
            if not acct:
                raise ValueError(f"Account not found: {account}")

            results = []
            for lot in acct.lots:
                if not include_closed and lot.is_closed:
                    continue
                summary = self._lot_summary(lot)
                results.append({
                    "guid": lot.guid,
                    "title": lot.title,
                    "notes": lot.notes or "",
                    **summary,
                })

            return results

    def get_lot(self, guid: str) -> dict:
        """Get detailed information about a lot.

        Args:
            guid: Lot GUID.

        Returns:
            Dict with lot details including all splits and summary.

        Raises:
            ValueError: If lot not found.
        """
        with self.open(readonly=True) as book:
            lot = self._find_lot(book, guid)
            if not lot:
                raise ValueError(f"Lot not found: {guid}")

            splits = []
            for split in lot.splits:
                splits.append({
                    "guid": split.guid,
                    "date": split.transaction.post_date.isoformat(),
                    "description": split.transaction.description,
                    "quantity": str(split.quantity),
                    "value": str(split.value),
                })

            summary = self._lot_summary(lot)

            return {
                "guid": lot.guid,
                "title": lot.title,
                "account": lot.account.fullname,
                "notes": lot.notes or "",
                "is_closed": bool(lot.is_closed),
                "splits": splits,
                "summary": summary,
            }

    def assign_split_to_lot(
        self,
        split_guid: str,
        lot_guid: str,
    ) -> dict:
        """Assign a transaction split to a lot.

        Use after creating a buy/sell transaction to link the investment
        account split to its lot for cost basis tracking.

        Args:
            split_guid: GUID of the split (from transaction's investment account).
            lot_guid: GUID of the lot.

        Returns:
            Dict with status and updated lot summary.

        Raises:
            ValueError: If split or lot not found, split is in wrong account,
                       split already assigned to a lot, or lot is closed.
        """
        with self.open(readonly=False) as book:
            split = self._find_split(book, split_guid)
            if not split:
                raise ValueError(f"Split not found: {split_guid}")

            lot = self._find_lot(book, lot_guid)
            if not lot:
                raise ValueError(f"Lot not found: {lot_guid}")

            if lot.is_closed:
                raise ValueError("Cannot assign split to a closed lot")

            if split.account != lot.account:
                raise ValueError(
                    f"Split account ({split.account.fullname}) does not match "
                    f"lot account ({lot.account.fullname})"
                )

            if split.lot is not None:
                raise ValueError(
                    f"Split is already assigned to lot: {split.lot.guid}"
                )

            split.lot = lot
            book.save()

            summary = self._lot_summary(lot)

            # Auto-close if quantity reaches zero
            if Decimal(summary["quantity"]) == 0 and len(lot.splits) > 0:
                lot.is_closed = 1
                book.save()
                summary["is_closed"] = True

            return {
                "status": "assigned",
                "split_guid": split_guid,
                "lot_guid": lot_guid,
                **summary,
            }

    def calculate_lot_gain(
        self,
        lot_guid: str,
        shares: str | None = None,
        sale_price: str | None = None,
    ) -> dict:
        """Calculate potential or actual capital gain for a lot.

        If shares and sale_price provided, calculates hypothetical gain.
        Otherwise uses lot's current state and latest price.

        Args:
            lot_guid: Lot GUID.
            shares: Optional number of shares to calculate for.
                    Defaults to all remaining shares.
            sale_price: Optional sale price per share.
                        Defaults to latest price for the commodity.

        Returns:
            Dict with shares, cost_basis, sale_proceeds, capital_gain, gain_percent.

        Raises:
            ValueError: If lot not found, no shares remaining, or no price available.
        """
        with self.open(readonly=True) as book:
            lot = self._find_lot(book, lot_guid)
            if not lot:
                raise ValueError(f"Lot not found: {lot_guid}")

            summary = self._lot_summary(lot)
            remaining = Decimal(summary["quantity"])

            if remaining <= 0:
                raise ValueError("Lot has no remaining shares")

            # Determine shares to sell
            if shares is not None:
                shares_to_sell = Decimal(shares)
                if shares_to_sell > remaining:
                    raise ValueError(
                        f"Cannot sell {shares_to_sell}; lot has {remaining} shares"
                    )
            else:
                shares_to_sell = remaining

            # Determine sale price
            if sale_price is not None:
                price = Decimal(sale_price)
            else:
                # Look up latest price for the commodity (inline, since
                # we're already inside an open session)
                commodity = lot.account.commodity
                latest_price = None
                latest_date = None
                for p in book.prices:
                    if p.commodity != commodity:
                        continue
                    p_date = _to_date(p.date)
                    if latest_date is None or p_date > latest_date:
                        latest_date = p_date
                        latest_price = p
                if latest_price is None:
                    raise ValueError(
                        f"No price found for {commodity.mnemonic}. "
                        "Provide sale_price explicitly."
                    )
                price = Decimal(str(latest_price.value))

            cost_per_share = Decimal(summary["cost_per_share"])
            cost_basis = cost_per_share * shares_to_sell
            proceeds = price * shares_to_sell
            gain = proceeds - cost_basis
            gain_pct = (gain / cost_basis * 100) if cost_basis else Decimal(0)

            return {
                "shares": str(shares_to_sell),
                "cost_basis": str(cost_basis),
                "sale_proceeds": str(proceeds),
                "capital_gain": str(gain),
                "gain_percent": str(gain_pct),
            }

    def close_lot(self, guid: str) -> dict:
        """Mark a lot as closed.

        Use when a lot is fully sold but wasn't automatically marked closed,
        or to manually close a lot with zero shares.

        Args:
            guid: Lot GUID.

        Returns:
            Dict with status.

        Raises:
            ValueError: If lot not found or already closed.
        """
        with self.open(readonly=False) as book:
            lot = self._find_lot(book, guid)
            if not lot:
                raise ValueError(f"Lot not found: {guid}")

            if lot.is_closed:
                raise ValueError("Lot is already closed")

            lot.is_closed = 1
            book.save()

            return {
                "guid": guid,
                "title": lot.title,
                "status": "closed",
            }

    # ============== Account Slot Operations ==============

    def get_account_slots(
        self, account_name: str, key: str | None = None
    ) -> dict:
        """Read all slots (or a specific slot) from an account.

        Args:
            account_name: Full account path (e.g., "Liabilities:Credit Cards:Capital One").
            key: Specific slot key to retrieve. If None, return all slots.

        Returns:
            Dict with account name and slots dict.

        Raises:
            ValueError: If account not found.
        """
        with self.open(readonly=True) as book:
            account = self._find_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            if key is not None:
                try:
                    value = account[key]
                    slots = {key: str(value.value) if hasattr(value, 'value') else str(value)}
                except KeyError:
                    slots = {}
            else:
                slots = {}
                for k, v in account.iteritems():
                    slots[k] = str(v.value)

            return {
                "account": account_name,
                "slots": slots,
            }

    def set_account_slot(
        self, account_name: str, key: str, value: str
    ) -> dict:
        """Set a single key-value pair on an account.

        Args:
            account_name: Full account path.
            key: Slot key (e.g., "apr", "credit_limit").
            value: Slot value. Stored as string.

        Returns:
            Dict with account, key, value, and status ("created" or "updated").

        Raises:
            ValueError: If account not found.
        """
        with self.open(readonly=False) as book:
            account = self._find_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            # Check if key exists to determine created vs updated
            existing = False
            try:
                account[key]
                existing = True
            except KeyError:
                pass

            account[key] = value
            book.save()

            return {
                "account": account_name,
                "key": key,
                "value": value,
                "status": "updated" if existing else "created",
            }

    def delete_account_slot(self, account_name: str, key: str) -> dict:
        """Remove a slot from an account.

        Args:
            account_name: Full account path.
            key: Slot key to remove.

        Returns:
            Dict with account, key, and status.

        Raises:
            ValueError: If account not found or key not found.
        """
        with self.open(readonly=False) as book:
            account = self._find_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            try:
                account[key]
            except KeyError:
                raise ValueError(f"Slot key not found: {key}")

            del account[key]
            book.save()

            return {
                "account": account_name,
                "key": key,
                "status": "deleted",
            }
