"""SQL-query primitives shared across mixins.

Composed into :class:`BaseGnuCashBook` unconditionally so every
module that needs to filter splits by date / account type / account
GUID gets the same indexed-query path, regardless of which
``--modules`` the user enabled.

Single source of truth for: "give me all splits matching
``(start_date, end_date, account_types, account_guids)`` as ORM
rows, ordered however the caller wants."

The function lives here rather than on ``ReportingMixin`` (where it
originated) because budgets needs it too — and any future module
that wants date-range-filtered splits should reach for the same
primitive rather than rolling its own Python-side
``for txn in book.transactions: if date_match`` loop.
"""

from datetime import date

import piecash


class QueryMixin:
    """Indexed-SQL query primitives, composed into BaseGnuCashBook."""

    def _query_filtered_splits(
        self,
        book: piecash.Book,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        account_types: frozenset[str] | set[str] | None = None,
        account_guids: frozenset[str] | set[str] | None = None,
        order_by_post_date: bool = False,
    ):
        """Build an indexed SQL query over ``(Split, Transaction, Account)``
        rows matching the given filters.

        Every filter maps to an indexable WHERE clause against the
        SQLite backing store — one query returns exactly the rows the
        caller needs, replacing the Python-side
        ``for txn in book.transactions: if date_match`` pattern that
        used to touch every row in the book regardless of relevance.

        The query yields ORM objects (not raw num/denom pairs) so
        callers can aggregate ``split.quantity`` / ``split.value`` as
        exact ``Decimal`` values in Python. Aggregating in SQL via
        ``SUM(num * 1.0 / denom)`` would collapse to IEEE-754 floats,
        which is unacceptable for financial arithmetic.

        Null ``post_date`` rows (an old-book artifact) are excluded so
        they don't show up in date-ranged reports.

        Args:
            book: An open piecash session.
            start_date: Include only transactions with
                ``post_date >= start_date``. ``None`` disables the
                lower bound.
            end_date: Include only transactions with
                ``post_date <= end_date``. ``None`` disables the upper
                bound.
            account_types: Restrict to accounts whose
                ``type in account_types`` (e.g., the reporting module's
                ``_ASSET_TYPES``). ``None`` disables the filter.
            account_guids: Restrict to accounts whose ``guid`` is in
                the given set. Used by ``cash_flow`` when filtering to
                a single named account.
            order_by_post_date: When ``True``, rows come back sorted
                ascending by ``post_date``. Required for the
                cumulative-sum trick used by the ``net_worth``
                time-series.

        Returns:
            A SQLAlchemy ``Query`` object the caller can iterate.
        """
        from piecash.core.account import Account
        from piecash.core.transaction import Split, Transaction

        q = (
            book.session.query(Split, Transaction, Account)
            .join(Transaction, Split.transaction_guid == Transaction.guid)
            .join(Account, Split.account_guid == Account.guid)
            .filter(Transaction.post_date.isnot(None))
        )
        if start_date is not None:
            q = q.filter(Transaction.post_date >= start_date)
        if end_date is not None:
            q = q.filter(Transaction.post_date <= end_date)
        if account_types is not None:
            q = q.filter(Account.type.in_(list(account_types)))
        if account_guids is not None:
            q = q.filter(Account.guid.in_(list(account_guids)))
        if order_by_post_date:
            q = q.order_by(Transaction.post_date)
        return q
