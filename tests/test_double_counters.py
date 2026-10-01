"""ID counters GnuCash 5.0/5.1 stored as doubles (bug 798930).

Desktop reads them anyway (``qof_book_get_counter`` casts) and stores
an integer back on its next ID. The server put the float into an
integer format and every auto-numbered create raised (review FC-2).
"""

import sqlite3

import pytest

from gnucash_mcp.book import GnuCashBook

_DOUBLE, _INT64 = 2, 1


def _q(book_path, sql, params=()):
    con = sqlite3.connect(str(book_path))
    try:
        rows = con.execute(sql, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def _as_double(book_path, key, value):
    """The row 5.0/5.1's Book Options dialog left: slot_type 2, the
    count in double_val."""
    changed = _q(
        book_path,
        "UPDATE slots SET slot_type = ?, double_val = ?, int64_val = 0 "
        "WHERE name = ? RETURNING id",
        (_DOUBLE, float(value), key),
    )
    assert changed, f"no {key} counter in the fixture yet"


def _counter(book_path, key):
    return _q(
        book_path,
        "SELECT slot_type, int64_val FROM slots WHERE name = ?", (key,),
    )


@pytest.fixture
def gb(business_book):
    book = GnuCashBook(str(business_book))
    # One of each so every counter slot exists.
    book.create_customer(name="First Customer")
    book.create_vendor(name="First Vendor")
    book.create_employee(name="First Employee")
    book.create_invoice(customer_id="000001")
    book.create_bill(vendor_id="000001")
    book.create_job(owner_id="000001", owner_type="customer", name="J")
    book.path = business_book
    return book


class TestDoubleCounters:
    def test_customer(self, gb):
        _as_double(gb.path, "counters/gncCustomer", 46)
        made = gb.create_customer(name="Dbl Co")
        assert made["id"] == "000047"
        # Repaired to what desktop stores after its next increment.
        assert _counter(gb.path, "counters/gncCustomer") == [(_INT64, 47)]

    def test_vendor_and_employee(self, gb):
        _as_double(gb.path, "counters/gncVendor", 9)
        _as_double(gb.path, "counters/gncEmployee", 3)
        assert gb.create_vendor(name="V")["id"] == "000010"
        assert gb.create_employee(name="E")["id"] == "000004"

    def test_invoice_and_bill(self, gb):
        _as_double(gb.path, "counters/gncInvoice", 46)
        _as_double(gb.path, "counters/gncBill", 12)
        assert gb.create_invoice(customer_id="000001")["id"] == "000047"
        assert gb.create_bill(vendor_id="000001")["id"] == "000013"
        assert _counter(gb.path, "counters/gncInvoice") == [(_INT64, 47)]
        assert _counter(gb.path, "counters/gncBill") == [(_INT64, 13)]

    def test_job(self, gb):
        _as_double(gb.path, "counters/gncJob", 5)
        made = gb.create_job(
            owner_id="000001", owner_type="customer", name="Next",
        )
        assert made["id"] == "000006"

    def test_one_counter_per_key_afterwards(self, gb):
        _as_double(gb.path, "counters/gncCustomer", 46)
        gb.create_customer(name="A")
        gb.create_customer(name="B")
        assert _q(
            gb.path,
            "SELECT COUNT(*) FROM slots WHERE name = 'counters/gncCustomer'",
        ) == [(1,)]
        assert _counter(gb.path, "counters/gncCustomer") == [(_INT64, 48)]

    def test_integer_counters_are_left_alone(self, gb):
        before = _q(gb.path, "SELECT id, slot_type, int64_val FROM slots "
                             "WHERE name LIKE 'counters/%' ORDER BY id")
        with gb.open(readonly=False) as book:
            assert gb._repair_double_counters(book) == 0
            book.save()
        assert _q(gb.path, "SELECT id, slot_type, int64_val FROM slots "
                           "WHERE name LIKE 'counters/%' ORDER BY id") == before
