"""SQL-statement budgets for the per-account tools.

Whole-tree review (2026-09-04), class 4: release-review finding 8
made ``_split_prefix_map`` one indexed, mtime-cached query, but three
callers kept building the map by walking ``book.transactions →
t.splits`` — one lazy SELECT per transaction — and
``get_unreconciled_splits`` paid a second head on its sort key
(``split.transaction`` per split). Measured on the Alex sample
(1,940 transactions): ~1,940 extra statements per call at each site.

Each test seeds an account with N transactions and asserts the call
stays well under N statements; the pre-fix shape scales with N, the
fixed shape does not. Thresholds were calibrated by MEASURING the
fixed and mutated counts with N=80 — get_unreconciled_splits 7 vs
172 statements, list_transactions(account) 15 vs 179, set_reconcile_state 14 vs 96, get_lot 12 vs 92, and
bulk reconcile_account 1 vs 82 SELECTs on the transactions table
(its total is dominated by legitimate per-row writes and piecash's
flush validation) — and sit between them. The structural test keeps the sites on the helpers.
"""

from __future__ import annotations

import inspect
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import piecash
import pytest
from sqlalchemy import event

from gnucash_mcp.book import GnuCashBook

N = 80


def _seed(book_path: Path, account_name: str, other_name: str) -> None:
    """Append N transactions between two accounts."""
    with piecash.open_book(
        str(book_path), readonly=False, do_backup=False,
    ) as b:
        usd = b.default_currency
        acct = next(a for a in b.accounts if a.fullname == account_name)
        other = next(a for a in b.accounts if a.fullname == other_name)
        for i in range(N):
            b.session.add(piecash.Transaction(
                currency=usd, description=f"Seed {i}",
                post_date=date(2024, 3, 1) + timedelta(days=i),
                splits=[
                    piecash.Split(account=acct, value=Decimal("-5")),
                    piecash.Split(account=other, value=Decimal("5")),
                ],
            ))
        b.save()


class _Counter:
    """Count SQL statements across every session a call opens."""

    def __init__(self, gb: GnuCashBook):
        self.gb = gb
        self.statements: list[str] = []

    def __enter__(self):
        real_open = self.gb.open
        counter = self

        class _Ctx:
            def __init__(self, ctx):
                self._ctx = ctx

            def __enter__(self):
                book = self._ctx.__enter__()
                self._engine = book.session.get_bind()

                def _record(conn, cursor, statement, parameters,
                            context, executemany):
                    counter.statements.append(statement)

                self._record = _record
                event.listen(self._engine, "before_cursor_execute", _record)
                return book

            def __exit__(self, *exc):
                event.remove(
                    self._engine, "before_cursor_execute", self._record,
                )
                return self._ctx.__exit__(*exc)

        self._real_open = real_open
        self.gb.open = lambda **kw: _Ctx(real_open(**kw))
        return self

    def __exit__(self, *exc):
        self.gb.open = self._real_open
        return False

    def __len__(self):
        return len(self.statements)

    def selects_from(self, table: str) -> int:
        """SELECTs whose first column is on ``table`` — the shape a
        lazy many-to-one load has. A bulk write legitimately issues
        one UPDATE per row and piecash's flush validation one splits
        SELECT per touched transaction, so the write budgets are on
        the lazy loads alone."""
        return sum(
            1 for st in self.statements
            if st.lstrip().upper().startswith(f"SELECT {table.upper()}.")
        )


class TestPerAccountQueryBudgets:

    def test_get_unreconciled_splits_compact(self, test_book):
        _seed(test_book, "Assets:Checking", "Expenses:Groceries")
        gb = GnuCashBook(str(test_book))
        with _Counter(gb) as c:
            out = gb.get_unreconciled_splits("Assets:Checking", limit=250)
        assert out.count("\n") >= N
        assert len(c) < N, (
            f"{len(c)} statements for get_unreconciled_splits on a "
            f"{N}-transaction account — per-transaction lazy loads "
            f"are back (prefix walk or split.transaction sort key)"
        )

    def test_list_transactions_register(self, test_book):
        """The account-filtered register view."""
        _seed(test_book, "Assets:Checking", "Expenses:Groceries")
        gb = GnuCashBook(str(test_book))
        with _Counter(gb) as c:
            out = gb.list_transactions(account="Assets:Checking", limit=250)
        assert out.count("\n") >= N
        assert len(c) < N, (
            f"{len(c)} statements for list_transactions(account) on a "
            f"{N}-transaction account — split.transaction is lazy again"
        )

    def test_set_reconcile_state(self, test_book):
        _seed(test_book, "Assets:Checking", "Expenses:Groceries")
        gb = GnuCashBook(str(test_book))
        with gb.open() as b:
            guid = next(
                s.guid for a in b.accounts if a.fullname == "Assets:Checking"
                for s in a.splits if s.reconcile_state != "y"
            )
        with _Counter(gb) as c:
            gb.set_reconcile_state(guid, "c")
        assert len(c) < N, (
            f"{len(c)} statements for set_reconcile_state on a "
            f"{N}-transaction book — the split prefix is walking "
            f"book.transactions again"
        )

    def test_reconcile_account_bulk(self, test_book):
        _seed(test_book, "Assets:Checking", "Expenses:Groceries")
        gb = GnuCashBook(str(test_book))
        # opening 1000 + salary 2000 − groceries 150 − N × 5
        expected = Decimal("2850") - Decimal(5) * N
        with _Counter(gb) as c:
            gb.reconcile_account(
                "Assets:Checking", date(2024, 12, 31), str(expected),
                reconcile_all=True,
            )
        n_txn = c.selects_from("transactions")
        assert n_txn < N // 4, (
            f"{n_txn} transactions SELECTs for bulk reconcile_account "
            f"on a {N}-transaction account — split.transaction is "
            f"lazy again (expected ~1: the account preload)"
        )

    def test_get_lot(self, investment_book):
        _seed(investment_book, "Assets:Checking", "Income:Capital Gains")
        gb = GnuCashBook(str(investment_book))
        lot = gb.create_lot("Assets:Investments:VTSAX", title="L")
        with _Counter(gb) as c:
            gb.get_lot(lot["guid"])
        assert len(c) < N, (
            f"{len(c)} statements for get_lot on a {N}-transaction "
            f"book — the split prefix is walking book.transactions again"
        )


class TestPrefixAndPreloadChokepoints:
    """Structural lock: the sites stay on the shared helpers."""

    def test_no_split_walks_at_the_fixed_sites(self):
        from gnucash_mcp.book import investments, reconciliation

        walk = "for txn in book.transactions for s in txn.splits"
        for method in (
            reconciliation.ReconciliationMixin.set_reconcile_state,
            reconciliation.ReconciliationMixin.get_unreconciled_splits,
            investments.InvestmentsMixin.get_lot,
        ):
            src = inspect.getsource(method)
            assert walk not in src, method.__name__
            assert "_split_prefix_map" in src, method.__name__

    def test_account_walks_preload_transactions(self):
        from gnucash_mcp.book import core, reconciliation

        for method in (
            reconciliation.ReconciliationMixin.get_unreconciled_splits,
            reconciliation.ReconciliationMixin.reconcile_account,
            core.CoreMixin.enter_statement,
            core.CoreMixin.list_transactions,
        ):
            assert "_preload_account_transactions" in inspect.getsource(
                method
            ), method.__name__
