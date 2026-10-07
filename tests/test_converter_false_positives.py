"""The one-time converter changes only rows the old server wrote.

``_upgrade_book_shapes`` brings a book written by server 1.2 to 1.4
to the shapes GnuCash writes. It runs ahead of every schedule,
budget, business, price, void and reconcile write, on every book,
forever — so on a book that never needed it, and on rows GnuCash
desktop wrote, it must change NOTHING.

It did not always. Twice a pass decided a row was the old server's
from what its values looked like, and rewrote rows desktop had made:

* C9 (adversarial review, 2026-09-30): an unposted credit note whose
  lines were all entered negative is stored all positive, by desktop
  and by 1.5 — and "all positive" was read as the pre-1.5 shape and
  negated.
* G-1 (GUI gate, 2026-10-01): desktop's Duplicate Invoice stores the
  copied lines' dates at 10:59 UTC — and "10:59" was read as the old
  server's entry date and moved to local noon, on eight lines of a
  book the old server had never touched.

The rule since: a pass identifies the old server's rows by a mark
only the old server left (``_migrate_business_shapes`` lists them),
and a row without one is not touched. This file is the test the
review asked for (§9, item 8): run every converter over a book 1.5
wrote, over desktop's shapes set down byte for byte, and — where
GnuCash is installed — over rows its own engine has just written,
and compare every row of every table before and after.
"""

from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from engine_twin import engine_run, find_gnucash_cli, guid_of  # noqa: E402

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"
CHECKING = "Assets:Checking"
EPOCH = "1970-01-01 00:00:00"


def _q(path, sql, params=()):
    con = sqlite3.connect(str(path))
    try:
        rows = con.execute(sql, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def _every_row(path) -> dict:
    """Every row of every table, keyed by table."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [
            t for (t,) in con.execute(
                "select name from sqlite_master where type = 'table'"
            )
        ]
        return {
            t: sorted(map(repr, con.execute(f"select * from {t}")))
            for t in tables
        }
    finally:
        con.close()


def _convert(gb) -> dict:
    with gb.open(readonly=False) as book:
        report = gb._upgrade_book_shapes(book)
        book.save()
    return report


def _assert_untouched(gb, path):
    before = _every_row(path)
    report = _convert(gb)
    after = _every_row(path)
    changed = {
        table: sorted(set(before[table]) ^ set(after[table]))[:4]
        for table in before if before[table] != after[table]
    }
    assert report == {}, report
    assert not changed, changed


def _business(gb):
    """Most of what the business module writes, through the server."""
    gb.create_account(
        name="GST Payable", account_type="LIABILITY", parent="Liabilities",
    )
    gb.create_taxtable(name="T5", entries=[{
        "type": "percentage", "amount": "5",
        "account": "Liabilities:GST Payable",
    }])
    gb.create_billterm(name="Net 30", due_days=30)
    gb.create_customer(name="Acme Corp")
    ids = []
    for price, day in (("100.00", "2026-01-15"), ("50.00", "2026-01-16"),
                       ("70.00", "2026-01-17")):
        inv = gb.create_invoice(customer_id="000001", term="Net 30")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price=price, taxtable="T5",
        )
        gb.post_invoice(invoice_id=inv["id"], post_account=AR, post_date=day)
        ids.append(inv["id"])
    return ids


class TestABook15WroteIsLeftAlone:
    def test_every_business_path_then_the_converter(self, business_book):
        gb = GnuCashBook(str(business_book))
        first, second, third = _business(gb)
        gb.pay_invoice(
            invoice_id=first, payment_account=CHECKING, amount="150.00",
            payment_date="2026-01-20", memo="chk 12", allow_prepayment=True,
        )
        gb.pay_invoice(invoice_id=second, from_prepayment=True)
        gb.pay_invoice(
            invoice_id=third, payment_account=CHECKING, amount="73.50",
            payment_date="2026-01-21", memo="chk 13",
        )
        gb.unpost_invoice(invoice_id=third, owner_type="customer")
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Restocking fee", quantity="-1", price="10.00",
        )
        gb.create_invoice(customer_id="000001")  # a draft with no lines
        gb.create_vendor(name="Supplier Ltd")
        bill = gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id=bill["id"], account="Expenses:Services",
            description="Hosting", quantity="1", price="80.00",
        )
        gb.post_invoice(invoice_id=bill["id"], post_account=AP,
                        post_date="2026-01-18", owner_type="vendor")
        voided = gb.create_transaction(
            description="Lunch", trans_date=date(2026, 1, 10),
            splits=[
                {"account": "Expenses:Services", "amount": "12.00"},
                {"account": CHECKING, "amount": "-12.00"},
            ],
        )
        gb.void_transaction(voided["guid"], "mistake")
        gb.create_account(
            name="Euro Cash", account_type="BANK", parent="Assets",
            commodity="EUR",
        )
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value="1.10",
            price_date=date(2026, 1, 15),
        )

        _assert_untouched(gb, business_book)


class TestDesktopsShapesAreLeftAlone:
    """Each is a shape GnuCash writes that a content-reading pass
    took, or could take, for the old server's."""

    @pytest.fixture
    def book(self, business_book):
        gb = GnuCashBook(str(business_book))
        ids = _business(gb)
        gb.pay_invoice(
            invoice_id=ids[0], payment_account=CHECKING, amount="105.00",
            payment_date="2026-01-20", memo="chk 12",
        )
        return gb, business_book, ids

    def test_lines_dated_at_the_neutral_time(self, book):
        """G-1 itself: Duplicate Invoice writes a line's date at
        10:59 UTC, with the discount defaults filled in."""
        gb, path, _ = book
        _q(path, "update entries set date = '2026-10-01 10:59:00'")
        _assert_untouched(gb, path)

    def test_a_credit_note_entered_all_negative(self, book):
        """C9 itself: stored all positive, by desktop and by 1.5."""
        gb, path, _ = book
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        for description in ("Restocking fee", "Handling"):
            gb.add_credit_note_entry(
                credit_note_id=cn["id"], account="Income:Sales",
                description=description, quantity="-1", price="10.00",
            )
        assert {q for (q,) in _q(
            path, "select quantity_num from entries where description in "
            "('Restocking fee', 'Handling')",
        )} == {1}
        _assert_untouched(gb, path)

    def test_a_bill_term_count_that_runs_high(self, book):
        """GnuCash adds one to a term's stored count for every
        reference it loads and saves the sum. The gate book's
        "Net 30" read 1 with nothing on the live term."""
        gb, path, _ = book
        _q(path, "update billterms set refcount = 7 where parent is null")
        _assert_untouched(gb, path)

    def test_a_payment_made_from_a_register_transaction(self, book):
        """Assign as Payment: the transaction keeps the date-posted
        slot the register gave it and whatever memos were typed —
        here, none on the receivable leg."""
        gb, path, _ = book
        txn = _q(
            path, "select obj_guid from slots where name = 'trans-txn-type' "
            "and string_val = 'P'",
        )[0][0]
        _q(
            path,
            "insert into slots (obj_guid, name, slot_type, int64_val, "
            "double_val, timespec_val, numeric_val_num, numeric_val_denom, "
            "gdate_val) values (?, 'date-posted', 10, 0, NULL, ?, 0, 1, "
            "'20260120')", (txn, EPOCH),
        )
        _q(
            path,
            "update splits set memo = '' where tx_guid = ? and account_guid "
            "in (select guid from accounts where account_type = 'RECEIVABLE')",
            (txn,),
        )
        _assert_untouched(gb, path)

    def test_a_document_lot_with_a_stored_flag_and_cleared_notes(self, book):
        """The lot viewer caches the closed flag and saves an empty
        note when one is cleared."""
        gb, path, _ = book
        lots = [g for (g,) in _q(path, "select guid from lots")]
        _q(path, "update lots set is_closed = 1 where guid = ?", (lots[0],))
        _q(path, "update lots set is_closed = 0 where guid = ?", (lots[1],))
        _q(
            path,
            "insert into slots (obj_guid, name, slot_type, int64_val, "
            "string_val, double_val, timespec_val, numeric_val_num, "
            "numeric_val_denom) values (?, 'notes', 4, 0, '', NULL, ?, 0, 1)",
            (lots[1], EPOCH),
        )
        _assert_untouched(gb, path)

    def test_document_dates_at_another_time_of_day(self, book):
        """GnuCash before 4 stamped a document at local noon."""
        gb, path, _ = book
        _q(
            path,
            "update invoices set date_opened = '2026-01-15 20:00:00', "
            "date_posted = '2026-01-15 20:00:00' where id = '000001'",
        )
        _assert_untouched(gb, path)

    def test_a_document_with_no_credit_note_flag(self, book):
        """One created by an import, or before GnuCash 2.5."""
        gb, path, _ = book
        _q(
            path,
            "delete from slots where name = 'credit-note' and obj_guid = "
            "(select guid from invoices where id = '000002')",
        )
        _assert_untouched(gb, path)


class TestTheOldServersRowsStillConvert:
    """The other direction, in one place: the mark is what converts a
    row, so the same values WITH the mark still move. (The full set
    is ``test_business_shape_migration.py``.)"""

    def test_the_same_line_date_with_the_old_fingerprint_moves(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        _business(gb)
        _q(
            business_book,
            "update entries set date = '2026-10-01 10:59:00', "
            "i_disc_type = '', i_disc_how = '', b_paytype = 0 "
            "where rowid = (select min(rowid) from entries)",
        )
        report = _convert(gb)
        assert report["entries_normalized"] == 1
        dates = {d for (d,) in _q(business_book, "select date from entries")}
        assert "2026-10-01 10:59:00" not in dates
        assert _convert(gb) == {}


@pytest.mark.skipif(
    find_gnucash_cli() is None, reason="gnucash-cli not on this machine",
)
class TestRowsGnuCashsEngineJustWrote:
    def test_post_pay_unpost_and_a_price_then_the_converter(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gb.create_billterm(name="Net 30", due_days=30)
        gb.create_customer(name="Acme Corp")
        for price in ("100.00", "50.00"):
            inv = gb.create_invoice(customer_id="000001", term="Net 30")
            gb.add_invoice_entry(
                invoice_id=inv["id"], account="Income:Sales",
                description="Work", quantity="1", price=price,
            )
        gb.create_account(
            name="Euro Cash", account_type="BANK", parent="Assets",
            commodity="EUR",
        )
        # The fixture's opening balance is piecash's own writing (a
        # NULL reconcile date, which GnuCash never stores); let the
        # converter have it now, so what follows is the engine's rows.
        _convert(gb)
        doc = "select guid from invoices where id = ?"
        one = guid_of(business_book, doc, "000001")
        two = guid_of(business_book, doc, "000002")
        account = "select guid from accounts where name = ?"
        ar = guid_of(business_book, account, "Accounts Receivable")
        chk = guid_of(business_book, account, "Checking")
        engine_run(business_book, [
            f"post|{one}|{ar}|15|1|2026|14|2|2026|",
            f"post|{two}|{ar}|16|1|2026|15|2|2026|",
            f"pay|{one}|{chk}|120|1|20|1|2026|chk 12|",
            f"autoapply|{two}",
            f"unpost|{one}",
            "price|CURRENCY|EUR|USD|1|6|2026|110/100|user:price-editor|last",
        ])

        _assert_untouched(gb, business_book)
