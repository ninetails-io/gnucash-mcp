"""SQL-query primitives shared across mixins.

Composed into :class:`BaseGnuCashBook` unconditionally so every
module that needs to filter splits by date / account type / account
GUID gets the same indexed-query path, regardless of which
``--modules`` the user enabled.

Single source of truth for: "give me all splits matching
``(start_date, end_date, account_types, account_guids)`` as ORM
rows, ordered however the caller wants."

The function lives here rather than on ``ReportingMixin``
because budgets needs it too — and any future module
that wants date-range-filtered splits should reach for the same
primitive rather than rolling its own Python-side
``for txn in book.transactions: if date_match`` loop.
"""

from datetime import date, timedelta

import piecash
from sqlalchemy import and_, literal_column, or_, text


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
        """Build an indexed SQL query over ``(Split, Transaction,
        Account)`` rows matching the given filters.

        Every filter maps to an indexable WHERE clause — one query
        returns exactly the relevant rows instead of a Python-side
        scan over every transaction. The query yields ORM objects so
        callers aggregate ``split.quantity`` / ``split.value`` as
        exact ``Decimal`` in Python — SQL ``SUM(num * 1.0 / denom)``
        collapses to IEEE-754 floats, unacceptable for money.

        Null ``post_date`` rows (old-book artifact) are excluded.
        Each filter is disabled when its arg is ``None``.
        Both bounds are inclusive of the full day, by the date piecash
        decodes — see the note at the bounds below.
        ``order_by_post_date`` sorts ascending (required by
        ``net_worth``'s cumulative sum).

        Returns:
            A list of ``(Split, Transaction, Account)`` rows.
        """
        from piecash.core.account import Account
        from piecash.core.transaction import Split, Transaction

        q = (
            book.session.query(Split, Transaction, Account)
            .join(Transaction, Split.transaction_guid == Transaction.guid)
            .join(Account, Split.account_guid == Account.guid)
            .filter(Transaction.post_date.isnot(None))
        )
        # Defense-in-depth template exclusion. Dormant today (the
        # null-post_date filter above already drops SX templates),
        # but it closes the latent path of a future codepath posting
        # to a template account and matches the convention at every
        # other report iteration site.
        template_guids = self._template_account_guids(book)
        if template_guids:
            q = q.filter(Account.guid.notin_(list(template_guids)))
        # The date bounds are applied twice: loosely in SQL, to keep
        # the indexed range scan, and exactly in Python, on the date
        # piecash decodes — the date every other path in the server
        # compares (``get_balance``, the dashboard, search).
        #
        # SQL alone compares what is STORED. piecash binds a bare
        # date at 10:59:00, GnuCash's neutral time, and a row is
        # stored there too unless something else wrote it: a
        # transaction stamped at local midnight sits at 08:00 UTC
        # (Pacific) or on the previous day (anywhere east of
        # Greenwich), on the wrong side of a 10:59 bound, and reports
        # dropped or double-counted it at a period boundary. On
        # SQLite the column is text, and a row GnuCash 2.6 wrote in
        # the compact ``YYYYMMDDHHMMSS`` form (kept as it was when a
        # later GnuCash upgraded the table) sorts after every dashed
        # date of its year (adversarial review 2026-09-30, C63).
        #
        # So SQL takes two days of slack on each side (a local
        # midnight is at most 14 hours from UTC's), plus, on a SQLite
        # book that holds compact rows, all of those; Python keeps
        # the rows whose decoded date is inside the range.
        #
        # ``date.max`` as an end means "no upper bound", and the
        # slack arithmetic would overflow there.
        slack = timedelta(days=2)
        bounds = []
        if start_date is not None and start_date > date.min + slack:
            bounds.append(Transaction.post_date >= start_date - slack)
        if end_date is not None and end_date < date.max - slack:
            bounds.append(Transaction.post_date < end_date + slack)
        if bounds:
            in_range = and_(*bounds)
            if self._has_compact_post_dates(book):
                in_range = or_(
                    in_range,
                    literal_column("transactions.post_date").notlike(
                        "____-%"
                    ),
                )
            q = q.filter(in_range)
        if account_types is not None:
            q = q.filter(Account.type.in_(list(account_types)))
        if account_guids is not None:
            q = q.filter(Account.guid.in_(list(account_guids)))
        if order_by_post_date:
            q = q.order_by(Transaction.post_date)
        rows = [
            row for row in q
            if (start_date is None or row[1].post_date >= start_date)
            and (end_date is None or row[1].post_date <= end_date)
        ]
        if order_by_post_date:
            # By the decoded date: stored text does not sort a compact
            # row among dashed ones. Stable, so SQL's order holds
            # within a day.
            rows.sort(key=lambda row: row[1].post_date)
        return rows

    def _has_compact_post_dates(self, book: piecash.Book) -> bool:
        """Does this SQLite book hold a ``post_date`` not in the
        dashed ISO form? One scan per state of the file (keyed on
        ``_cache_token``); False on a database book, whose column is
        a real timestamp."""
        # Imported here: _base composes this mixin.
        from gnucash_mcp.book._base import _dialect_name

        if _dialect_name(book) != "sqlite":
            return False
        token = self._cache_token()
        cached = getattr(self, "_compact_dates_cache", None)
        if token is not None and cached is not None and cached[0] == token:
            return cached[1]
        found = book.session.execute(
            text(
                "SELECT 1 FROM transactions WHERE post_date IS NOT NULL "
                "AND post_date NOT LIKE '____-%' LIMIT 1"
            )
        ).first() is not None
        self._compact_dates_cache = (token, found)
        return found
