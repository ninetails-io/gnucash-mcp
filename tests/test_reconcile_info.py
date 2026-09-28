"""The reconciliation threshold follows the account's statement
cycle — dashboard-accuracy spec B4 (ruled 2026-09-28).

Desktop's reconcile window records, on Finish, the interval since
the previous statement and the statement date, under the account's
``reconcile-info`` KVP frame (Account.cpp). ``reconcile_account``
and ``enter_statement`` now write the same rows; the dashboard
reads them and warns at interval + 15 days. Key names are pinned
verbatim; the interval port is table-tested against
``gnc_save_reconcile_interval`` (window-reconcile.cpp).
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import piecash
import pytest
from sqlalchemy import text

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.book._base import BaseGnuCashBook as B


class TestKeys:
    def test_names_pinned_to_account_cpp(self):
        assert B._RECONCILE_INFO_FRAME == "reconcile-info"
        assert B._RECONCILE_LAST_DATE == "last-date"
        assert B._RECONCILE_LAST_INTERVAL == "last-interval"
        assert B._RECONCILE_INTERVAL_MONTHS == "months"
        assert B._RECONCILE_INTERVAL_DAYS == "days"


class TestIntervalPort:
    @pytest.mark.parametrize(
        "prev, cur, prev_interval, expected, why",
        [
            (date(2026, 1, 31), date(2026, 2, 14), None, (0, 14),
             "14 days: days, not months"),
            (date(2026, 1, 31), date(2026, 2, 28), None, (1, 0),
             "28 days with no previous interval: prev_months defaults "
             "to 1, so one month"),
            (date(2026, 1, 31), date(2026, 2, 28), (0, 14), (0, 28),
             "28 days after a 14-day cycle: four weeks"),
            (date(2026, 1, 31), date(2026, 2, 28), (1, 0), (1, 0),
             "28 days after a monthly cycle: one month"),
            (date(2026, 1, 15), date(2026, 4, 15), None, (3, 0),
             "> 28 days: calendar months, days 0"),
            (date(2025, 11, 30), date(2026, 1, 31), None, (2, 0),
             "> 28 days across a year end: 12*year+month arithmetic"),
            (date(2026, 3, 31), date(2026, 3, 1), None, None,
             "negative days are not remembered"),
            (date(2026, 3, 1), date(2026, 3, 1), None, (0, 0),
             "same day: zero, which is >= 0 and remembered"),
        ],
    )
    def test_table(self, prev, cur, prev_interval, expected, why):
        assert B._reconcile_interval(prev, cur, prev_interval) == expected, why


def _rows(gc, account_path):
    """Every reconcile-info row hanging off the account, as
    ``{name: (slot_type, int64_val)}``, resolved through the frames."""
    with gc.open(readonly=True) as book:
        acct = gc._find_account(book, account_path)
        out = {}
        owners = [acct.guid]
        while owners:
            owner = owners.pop()
            for name, st, i64, gv in book.session.execute(
                text(
                    "SELECT name, slot_type, int64_val, guid_val FROM slots "
                    "WHERE obj_guid = :o AND name LIKE 'reconcile-info%'"
                ),
                {"o": owner},
            ).fetchall():
                out[name] = (st, i64)
                if st == 9 and gv:
                    owners.append(gv)
        return out


def _reconcile(gc, statement_date, description):
    gc.create_transaction(
        description=description,
        splits=[
            {"account": "Assets:Checking", "amount": "10"},
            {"account": "Income:Salary", "amount": "-10"},
        ],
        trans_date=statement_date, check_duplicates=False,
    )
    balance = gc.get_balance("Assets:Checking", as_of_date=statement_date)
    gc.reconcile_account(
        account_name="Assets:Checking", statement_date=statement_date,
        statement_balance=str(balance), reconcile_all=True,
    )


class TestReconcileWritesDesktopFrame:
    def test_first_reconcile_writes_last_date_only(self, test_book):
        gc = GnuCashBook(str(test_book))
        _reconcile(gc, date(2026, 1, 31), "jan")
        rows = _rows(gc, "Assets:Checking")
        assert rows["reconcile-info"][0] == 9
        st, i64 = rows["reconcile-info/last-date"]
        assert st == 1  # KVP_TYPE_INT64: a time64
        assert datetime.fromtimestamp(i64).date() == date(2026, 1, 31)
        # No previous statement date: desktop records no interval.
        assert "reconcile-info/last-interval" not in rows

    def test_second_reconcile_records_the_interval(self, test_book):
        gc = GnuCashBook(str(test_book))
        _reconcile(gc, date(2026, 1, 31), "jan")
        _reconcile(gc, date(2026, 4, 30), "apr")
        rows = _rows(gc, "Assets:Checking")
        assert rows["reconcile-info/last-interval"][0] == 9
        assert rows["reconcile-info/last-interval/months"] == (1, 3)
        assert rows["reconcile-info/last-interval/days"] == (1, 0)
        assert datetime.fromtimestamp(
            rows["reconcile-info/last-date"][1]
        ).date() == date(2026, 4, 30)
        # A third reconcile updates in place: still one frame.
        _reconcile(gc, date(2026, 5, 14), "may")
        rows = _rows(gc, "Assets:Checking")
        assert rows["reconcile-info/last-interval/days"] == (1, 14)
        assert rows["reconcile-info/last-interval/months"] == (1, 0)
        with gc.open(readonly=True) as book:
            n = book.session.execute(
                text(
                    "SELECT COUNT(*) FROM slots WHERE name = 'reconcile-info'"
                )
            ).scalar()
        assert n == 1

    def test_reader_round_trips(self, test_book):
        gc = GnuCashBook(str(test_book))
        _reconcile(gc, date(2026, 1, 31), "jan")
        _reconcile(gc, date(2026, 4, 30), "apr")
        with gc.open(readonly=True) as book:
            acct = gc._find_account(book, "Assets:Checking")
            info = gc._read_reconcile_info_all(book)[acct.guid]
        assert info == {"last_date": date(2026, 4, 30), "months": 3, "days": 0}


class TestThresholdFollowsCycle:
    @staticmethod
    def _write_interval(gc, months, days, last):
        with gc.open(readonly=False) as book:
            acct = gc._find_account(book, "Assets:Checking")
            # Seed a previous date, then let the port compute.
            gc._write_reconcile_info(book, acct, last)
            book.save()
        return

    def _reconciled_days_ago(self, gc, days_ago):
        when = date.today() - timedelta(days=days_ago)
        _reconcile(gc, when, "stmt")
        return when

    @staticmethod
    def _checking_line(gc):
        recon = gc.get_book_summary().split("Reconciliation:")[1].split("\nNet worth")[0]
        return recon

    def test_quarterly_cycle_is_not_behind_at_80_days(self, test_book):
        gc = GnuCashBook(str(test_book))
        # Two statements a quarter apart, the last 80 days ago.
        self._reconciled_days_ago(gc, 172)
        self._reconciled_days_ago(gc, 80)
        # No backlog since: nothing entered after the statement.
        recon = self._checking_line(gc)
        assert "1 account current" in recon, recon
        assert "behind" not in recon
        status = gc.get_reconciliation_status()
        assert "Assets:Checking\tcurrent\t" in status

    def test_quarterly_cycle_is_behind_past_interval_plus_grace(self, test_book):
        gc = GnuCashBook(str(test_book))
        self._reconciled_days_ago(gc, 200)
        self._reconciled_days_ago(gc, 108)  # 92-day cycle -> 3 months
        recon = self._checking_line(gc)  # 108 > 90 + 15
        assert "behind" in recon and "⚠" in recon, recon

    def test_no_recorded_cycle_keeps_the_45_day_default(self, test_book):
        gc = GnuCashBook(str(test_book))
        when = self._reconciled_days_ago(gc, 80)
        # Strip the frame: a book reconciled only by a pre-B4 server.
        with gc.open(readonly=False) as book:
            book.session.execute(
                text("DELETE FROM slots WHERE name LIKE 'reconcile-info%'")
            )
            book.save()
        recon = self._checking_line(gc)
        assert f"Checking: through {when.isoformat()}" in recon
        assert "behind" in recon and "⚠" in recon

    def test_monthly_cycle_matches_the_default(self, test_book):
        gc = GnuCashBook(str(test_book))
        self._reconciled_days_ago(gc, 76)
        self._reconciled_days_ago(gc, 46)  # 30 days -> 1 month -> 45
        assert "behind" in self._checking_line(gc)
