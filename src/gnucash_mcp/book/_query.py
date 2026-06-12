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
        ``end_date`` is inclusive of the full day — see the boundary
        note below on piecash's ``_DateAsDateTime`` storage.
        ``order_by_post_date`` sorts ascending (required by
        ``net_worth``'s cumulative sum).

        Returns:
            A SQLAlchemy ``Query`` the caller can iterate.
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
        if start_date is not None:
            q = q.filter(Transaction.post_date >= start_date)
        if end_date is not None and end_date < date.max:
            # piecash's ``_DateAsDateTime`` TypeDecorator stores
            # ``post_date`` as a DateTime with a 10:59:00
            # neutral-time component (see
            # ``piecash.sa_extra._DateAsDateTime.process_bind_param``).
            # A bare-date upper bound coerces to midnight in the SQL
            # comparison, so ``post_date <= as_of`` would exclude
            # same-day transactions whose stored time is 10:59 —
            # ``balance_sheet(2025-12-31)`` returning a balance that
            # excluded December 31 activity, while ``get_balance``
            # (which compares Python-side, post-``process_result_value``,
            # where the time has already been stripped) showing the
            # correct number. Using the day after as a
            # strict upper bound includes the full as_of date
            # regardless of stored time component.
            #
            # ``end_date == date.max`` is treated as "no upper bound"
            # — ``date.max + timedelta(days=1)`` overflows. A caller
            # passing ``date.max`` semantically wants every row,
            # which is what dropping the filter does.
            q = q.filter(
                Transaction.post_date < end_date + timedelta(days=1)
            )
        if account_types is not None:
            q = q.filter(Account.type.in_(list(account_types)))
        if account_guids is not None:
            q = q.filter(Account.guid.in_(list(account_guids)))
        if order_by_post_date:
            q = q.order_by(Transaction.post_date)
        return q
