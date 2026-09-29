"""Due dates follow GnuCash's billterm math — dashboard-accuracy
spec A2 (ruled 2026-09-28: the way desktop does).

``_billterm_due_date`` is a verbatim port of ``compute_time`` /
``compute_monthyear`` in ``libgnucash/engine/gncBillTerm.c``
(stable). Every expected value below is worked through the C by
hand and the step named; the C file's own worked example (cutoff
19, due day 20) is the first table row.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.book.business import BusinessMixin

DAYS = "GNC_TERM_TYPE_DAYS"
PROXIMO = "GNC_TERM_TYPE_PROXIMO"


def _term(type_, duedays, cutoff=0):
    return SimpleNamespace(type=type_, duedays=duedays, cutoff=cutoff, name="t")


class TestPort:
    def test_type_strings_pinned_to_gnucash(self):
        """billterms.type is a string column (gnc-bill-term-sql.cpp)
        holding the enum's name; the sample books carry
        GNC_TERM_TYPE_DAYS."""
        assert BusinessMixin._TERM_TYPE_DAYS == DAYS
        assert BusinessMixin._TERM_TYPE_PROXIMO == PROXIMO

    @pytest.mark.parametrize(
        "term, posted, expected, why",
        [
            # compute_time, GNC_TERM_TYPE_DAYS: res += SECS_PER_DAY * days.
            (_term(DAYS, 30), date(2026, 1, 15), date(2026, 2, 14), "days: +30"),
            (_term(DAYS, 0), date(2026, 1, 15), date(2026, 1, 15),
             "due on receipt: duedays 0 is a real term, +0 days"),
            # The C comment's example 1: cutoff 19, due day 20, posted
            # 14-06-2010 -> 14 <= 19 -> next month -> 20-07-2010.
            (_term(PROXIMO, 20, 19), date(2010, 6, 14), date(2010, 7, 20),
             "proximo, before cutoff: next month"),
            # Example 2: posted 22-06-2010 -> 22 > 19 -> month after
            # next (imonth += 2) -> the 20th of August. (The comment
            # says 20-02-2010; the code says August.)
            (_term(PROXIMO, 20, 19), date(2010, 6, 22), date(2010, 8, 20),
             "proximo, after cutoff: the month after next"),
            # On the cutoff: iday <= cutoff is inclusive -> next month.
            (_term(PROXIMO, 20, 19), date(2010, 6, 19), date(2010, 7, 20),
             "proximo, on the cutoff: next month"),
            # cutoff 0: cutoff += last_mday(June) = 30, so every
            # June posting is <= 30 -> next month.
            (_term(PROXIMO, 15, 0), date(2010, 6, 30), date(2010, 7, 15),
             "cutoff 0 means the posting month's last day"),
            # cutoff -3 in February 2026 (28 days): 28 - 3 = 25.
            (_term(PROXIMO, 15, -3), date(2026, 2, 25), date(2026, 3, 15),
             "negative cutoff, on it: next month"),
            (_term(PROXIMO, 15, -3), date(2026, 2, 26), date(2026, 4, 15),
             "negative cutoff, past it: the month after next"),
            # due_days 31 into February: day = last_mday(Feb) = 28;
            # 31 < 28 is false, so day stays 28.
            (_term(PROXIMO, 31, 10), date(2026, 1, 5), date(2026, 2, 28),
             "due day clamps to the due month's last day"),
            (_term(PROXIMO, 31, 10), date(2028, 1, 5), date(2028, 2, 29),
             "... and a leap February has 29"),
            # December rollover: imonth 12 + 2 = 14 -> iyear++, 14 - 12 = 2.
            (_term(PROXIMO, 20, 10), date(2026, 12, 15), date(2027, 2, 20),
             "December, after cutoff: February next year"),
            (_term(PROXIMO, 20, 10), date(2026, 12, 5), date(2027, 1, 20),
             "December, before cutoff: January next year"),
            (_term(PROXIMO, 20, 10), date(2026, 11, 15), date(2027, 1, 20),
             "November, after cutoff: 11 + 2 = 13 -> January next year"),
            # duedays 0 on a proximo term: day = min(0, 30) = 0, and
            # gnc_dmy2time64_neutral(0, April) normalizes like mktime
            # to the last day of March.
            (_term(PROXIMO, 0, 10), date(2026, 3, 5), date(2026, 3, 31),
             "proximo due day 0: the day before the due month"),
        ],
    )
    def test_table(self, term, posted, expected, why):
        assert BusinessMixin._billterm_due_date(term, posted) == expected, why

    def test_no_terms_is_the_posting_date(self):
        """gncBillTermComputeDueDate: ``if (!term) return post_date;``"""
        assert BusinessMixin._billterm_due_date(None, date(2026, 5, 9)) == date(2026, 5, 9)

    def test_unknown_type_is_the_posting_date(self):
        """The switch has no default; res stays the neutral post date."""
        assert BusinessMixin._billterm_due_date(
            _term("GNC_TERM_TYPE_FUTURE", 30), date(2026, 5, 9),
        ) == date(2026, 5, 9)


def _slot(gc, invoice_id):
    """The due date desktop would read: the trans-date-due row must
    be a slot_type 6 timespec at 10:59:00 (GnuCash's neutral clock
    time), and there must be exactly one."""
    with gc.open(readonly=True) as book:
        rows = book.session.execute(
            text(
                "SELECT s.slot_type, s.timespec_val, s.gdate_val "
                "FROM slots s JOIN invoices i "
                "ON s.obj_guid = i.post_txn WHERE i.id = :id "
                "AND s.name = 'trans-date-due'"
            ),
            {"id": invoice_id},
        ).fetchall()
    if not rows:
        return None
    assert len(rows) == 1, rows
    slot_type, ts, gd = rows[0]
    assert int(slot_type) == 6 and gd is None, rows[0]
    assert str(ts).endswith("10:59:00"), rows[0]
    return str(ts)[:10]


def _posted_invoice(gc, customer_id="000001", term=None, price="100"):
    inv = gc.create_invoice(customer_id=customer_id, term=term)
    gc.add_invoice_entry(
        invoice_id=inv["id"], account="Income:Sales",
        description="Work", quantity="1", price=price,
    )
    return inv["id"]


def _make_proximo(gc, name, due_day, cutoff):
    gc.create_billterm(name=name, due_days=due_day)
    with gc.open(readonly=False) as book:
        book.session.execute(
            text(
                "UPDATE billterms SET type = :t, cutoff = :c "
                "WHERE name = :n"
            ),
            {"t": PROXIMO, "c": cutoff, "n": name},
        )
        book.save()


class TestPostWritesDueDate:
    """Every post leaves trans-date-due on the posting transaction,
    as desktop's gncInvoicePostToAccount does."""

    def test_no_terms_no_due_date_is_the_posting_date(self, business_book):
        gc = GnuCashBook(str(business_book))
        gc.create_customer(name="Acme")
        iid = _posted_invoice(gc)
        gc.post_invoice(
            invoice_id=iid, post_account="Assets:Accounts Receivable",
            post_date="2026-03-10",
        )
        assert _slot(gc, iid) == "2026-03-10"
        row = gc.get_outstanding_invoices(compact=False)["invoices"][0]
        assert row["due_date"] == "2026-03-10"
        assert "no_terms" not in row

    def test_no_terms_caller_due_date_stands(self, business_book):
        gc = GnuCashBook(str(business_book))
        gc.create_customer(name="Acme")
        iid = _posted_invoice(gc)
        gc.post_invoice(
            invoice_id=iid, post_account="Assets:Accounts Receivable",
            post_date="2026-03-10", due_date="2026-04-01",
        )
        assert _slot(gc, iid) == "2026-04-01"

    def test_days_term_sets_the_due_date(self, business_book):
        gc = GnuCashBook(str(business_book))
        gc.create_billterm(name="Net 30", due_days=30)
        gc.create_customer(name="Acme")
        iid = _posted_invoice(gc, term="Net 30")
        gc.post_invoice(
            invoice_id=iid, post_account="Assets:Accounts Receivable",
            post_date="2026-03-10",
        )
        assert _slot(gc, iid) == "2026-04-09"

    def test_proximo_term_sets_the_due_date(self, business_book):
        """Cutoff 19, due day 20, posted the 22nd: the month after
        next (the C file's example 2)."""
        gc = GnuCashBook(str(business_book))
        _make_proximo(gc, "20th prox", due_day=20, cutoff=19)
        gc.create_customer(name="Acme")
        iid = _posted_invoice(gc, term="20th prox")
        gc.post_invoice(
            invoice_id=iid, post_account="Assets:Accounts Receivable",
            post_date="2026-06-22",
        )
        assert _slot(gc, iid) == "2026-08-20"
        row = gc.get_outstanding_invoices(compact=False)["invoices"][0]
        assert row["due_date"] == "2026-08-20"

    def test_due_on_receipt_term_is_the_posting_date(self, business_book):
        """duedays = 0 used to be falsy and fall to the 30-day
        default, labeled '(no term set)'."""
        gc = GnuCashBook(str(business_book))
        gc.create_billterm(name="Due on receipt", due_days=0)
        gc.create_customer(name="Acme")
        iid = _posted_invoice(gc, term="Due on receipt")
        gc.post_invoice(
            invoice_id=iid, post_account="Assets:Accounts Receivable",
            post_date="2026-03-10",
        )
        assert _slot(gc, iid) == "2026-03-10"

    def test_terms_win_disagreeing_due_date_is_refused(self, business_book):
        gc = GnuCashBook(str(business_book))
        gc.create_billterm(name="Net 30", due_days=30)
        gc.create_customer(name="Acme")
        iid = _posted_invoice(gc, term="Net 30")
        with pytest.raises(ValueError, match="2026-04-09"):
            gc.post_invoice(
                invoice_id=iid, post_account="Assets:Accounts Receivable",
                post_date="2026-03-10", due_date="2026-04-15",
            )
        # Still unposted; an agreeing date goes through.
        gc.post_invoice(
            invoice_id=iid, post_account="Assets:Accounts Receivable",
            post_date="2026-03-10", due_date="2026-04-09",
        )
        assert _slot(gc, iid) == "2026-04-09"


class TestBackfill:
    def test_business_write_backfills_missing_slots(self, business_book):
        """A server-posted document without trans-date-due is a
        shape desktop never writes; the next business write adds
        the slot (terms over posting date) and reports the count."""
        gc = GnuCashBook(str(business_book))
        gc.create_billterm(name="Net 30", due_days=30)
        gc.create_customer(name="Acme")
        termed = _posted_invoice(gc, term="Net 30")
        gc.post_invoice(
            invoice_id=termed, post_account="Assets:Accounts Receivable",
            post_date="2026-03-10",
        )
        bare = _posted_invoice(gc)
        gc.post_invoice(
            invoice_id=bare, post_account="Assets:Accounts Receivable",
            post_date="2026-03-12",
        )
        # Strip both slots, as a pre-1.5 post left them.
        with gc.open(readonly=False) as book:
            book.session.execute(
                text("DELETE FROM slots WHERE name = 'trans-date-due'")
            )
            book.save()
        assert _slot(gc, termed) is None
        # Reads never write.
        gc.get_outstanding_invoices()
        assert _slot(gc, termed) is None
        # The next business write converts.
        third = _posted_invoice(gc)
        result = gc.post_invoice(
            invoice_id=third, post_account="Assets:Accounts Receivable",
            post_date="2026-03-15",
        )
        assert result["due_dates_backfilled"] == 2
        assert _slot(gc, termed) == "2026-04-09"
        assert _slot(gc, bare) == "2026-03-12"


class TestDesktopShape:
    def test_row_matches_the_desktop_specimen(self, business_book):
        """Desktop, posting a Net-30 invoice on 2026-09-28, wrote
        ``slot_type 6 | timespec_val 2026-10-28 10:59:00`` (looped
        copy, 2026-09-28). The server's row must be byte-for-byte
        that shape; the GDate row it used to write rendered as no
        due date at all in desktop."""
        gc = GnuCashBook(str(business_book))
        gc.create_billterm(name="Net 30", due_days=30)
        gc.create_customer(name="Acme")
        iid = _posted_invoice(gc, term="Net 30")
        gc.post_invoice(
            invoice_id=iid, post_account="Assets:Accounts Receivable",
            post_date="2026-09-28",
        )
        with gc.open(readonly=True) as book:
            row = book.session.execute(
                text(
                    "SELECT s.slot_type, s.timespec_val, s.gdate_val, "
                    "s.int64_val, s.double_val, s.numeric_val_num, "
                    "s.numeric_val_denom FROM slots s JOIN invoices i "
                    "ON s.obj_guid = i.post_txn WHERE i.id = :id "
                    "AND s.name = 'trans-date-due'"
                ),
                {"id": iid},
            ).fetchall()
        # The specimen row, every column: 6 | 0 | NULL | NULL |
        # '2026-10-28 10:59:00' | NULL | 0 | 1 | NULL.
        assert [tuple(r) for r in row] == [
            (6, "2026-10-28 10:59:00", None, 0, None, 0, 1)
        ]

    def test_backfill_rewrites_old_gdate_rows_in_place(self, business_book):
        """A pre-fix book holds GDate rows (slot_type 10, YYYYMMDD).
        The next business write converts each to the timespec row,
        keeping the date it held, and counts it."""
        gc = GnuCashBook(str(business_book))
        gc.create_customer(name="Acme")
        iid = _posted_invoice(gc)
        gc.post_invoice(
            invoice_id=iid, post_account="Assets:Accounts Receivable",
            post_date="2026-03-10", due_date="2026-04-01",
        )
        with gc.open(readonly=False) as book:
            book.session.execute(
                text(
                    "UPDATE slots SET slot_type = 10, timespec_val = NULL, "
                    "gdate_val = '20260401' WHERE name = 'trans-date-due'"
                )
            )
            book.save()
        # Reads still understand the old row ...
        row = gc.get_outstanding_invoices(compact=False)["invoices"][0]
        assert row["due_date"] == "2026-04-01"
        # ... and the next write rewrites it.
        second = _posted_invoice(gc)
        result = gc.post_invoice(
            invoice_id=second, post_account="Assets:Accounts Receivable",
            post_date="2026-03-12",
        )
        assert result["due_dates_backfilled"] == 1
        assert _slot(gc, iid) == "2026-04-01"


class TestDashboardAgrees:
    def test_no_terms_document_is_overdue_from_its_posting_date(
        self, business_book,
    ):
        """Behavior change: with no terms and no due date, a
        document is due on its posting date, not 30 days later."""
        gc = GnuCashBook(str(business_book))
        gc.create_customer(name="No Terms Co")
        iid = _posted_invoice(gc, price="1500")
        gc.post_invoice(
            invoice_id=iid, post_account="Assets:Accounts Receivable",
            post_date=(date.today() - timedelta(days=50)).isoformat(),
        )
        result = gc.get_book_summary()
        assert "Past due invoice: No Terms Co 50 days overdue, USD 1,500.00" in result
        assert "30-day default" not in result
        assert "no term set" not in result
        compact = gc.get_outstanding_invoices()
        assert "50 days past due" in compact
