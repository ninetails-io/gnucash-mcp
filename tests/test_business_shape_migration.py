"""``_migrate_business_shapes``: every pre-2026-09-29 business row
shape the parity twin found, engineered the way the server used to
write it, converted on the next converting write, amounts untouched.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from gnucash_mcp.book import GnuCashBook


def _rows(path, sql, *args):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _flow(gc):
    """A customer, a posted invoice, a posted credit note applied to
    it, and a payment — all in the current (desktop) shape."""
    gc.create_customer(name="Acme", currency="USD")
    gc.create_invoice(customer_id="000001", date_opened="2026-08-01")
    gc.add_invoice_entry(
        invoice_id="000001", account="Income:Sales",
        description="Work", quantity="1", price="1000",
    )
    gc.post_invoice(
        invoice_id="000001", post_account="Assets:Accounts Receivable",
        post_date="2026-08-01",
    )
    cn = gc.create_credit_note(
        owner_id="000001", owner_type="customer", date_opened="2026-08-05",
    )
    gc.add_credit_note_entry(
        credit_note_id=cn["id"], account="Income:Sales",
        description="Adjustment", quantity="1", price="200",
    )
    gc.post_invoice(
        invoice_id=cn["id"], post_account="Assets:Accounts Receivable",
        owner_type="customer", post_date="2026-08-05",
    )
    gc.apply_credit_note(
        credit_note_id=cn["id"], applies_to_invoice_id="000001",
        owner_type="customer",
    )
    gc.pay_invoice(
        invoice_id="000001", payment_account="Assets:Checking",
        amount="800", payment_date="2026-08-10", memo="check 12",
    )
    return cn["id"]


def _make_legacy(gc, cn_id):
    """Rewrite the flow's rows into the server's old shapes."""
    with gc.open(readonly=False) as book:
        s = book.session
        # credit-note entries stored positive
        s.execute(text(
            "UPDATE entries SET quantity_num = ABS(quantity_num) "
            "WHERE invoice = (SELECT guid FROM invoices WHERE id = :id)"
        ), {"id": cn_id})
        # entry defaults blank, dates at 10:59 UTC and local midnight
        s.execute(text(
            "UPDATE entries SET i_disc_type = '', i_disc_how = '', b_paytype = 0, "
            "billto_type = 0, b_taxable = 0, date = '2026-08-01 10:59:00'"
        ))
        s.execute(text(
            "UPDATE invoices SET billto_type = 0, "
            "date_opened = '2026-08-01 07:00:00', date_posted = '2026-08-01 07:00:00'"
        ))
        # the application as a payment-typed transaction with the old memos
        link = s.execute(text(
            "SELECT obj_guid FROM slots WHERE name = 'trans-txn-type' AND string_val = 'L'"
        )).scalar()
        s.execute(text(
            "UPDATE slots SET string_val = 'P' WHERE obj_guid = :g AND name = 'trans-txn-type'"
        ), {"g": link})
        s.execute(text(
            "UPDATE transactions SET description = 'Credit applied: X → 000001' WHERE guid = :g"
        ), {"g": link})
        s.execute(text(
            "UPDATE splits SET action = 'Payment', memo = 'Net against 000001' WHERE tx_guid = :g"
        ), {"g": link})
        # closed flags stored, payment memo on the bank leg only, empty lot notes
        s.execute(text("UPDATE lots SET is_closed = 1"))
        s.execute(text(
            "UPDATE splits SET memo = '' WHERE memo = 'check 12' AND account_guid IN "
            "(SELECT guid FROM accounts WHERE account_type = 'RECEIVABLE')"
        ))
        for lot_guid, in s.execute(text("SELECT guid FROM lots")).fetchall():
            s.execute(text(
                "INSERT INTO slots (obj_guid, name, slot_type, string_val) "
                "VALUES (:g, 'notes', 4, '')"
            ), {"g": lot_guid})
        book.save()


def test_every_old_shape_converts_on_the_next_write(business_book):
    gc = GnuCashBook(str(business_book))
    cn_id = _flow(gc)
    _make_legacy(gc, cn_id)

    # The next converting write: a schedule-free, budget-free book,
    # so any business write — here, a billterm.
    with gc.open(readonly=False) as book:
        out = gc._upgrade_book_shapes(book)
        book.save()

    assert out["credit_note_entries_migrated"] == 1
    assert out["credit_applications_migrated"] == 1
    assert out["document_dates_normalized"] == 2
    assert out["entries_normalized"] == 2
    assert out["lot_flags_reset"] == 2
    assert out["payment_memos_completed"] == 1
    assert out["lot_notes_pruned"] == 2
    assert "credit_note_entries_unresolved" not in out

    p = str(business_book)
    # entries: credit note negated, invoice untouched, defaults set, date at local noon
    cn_q, = _rows(p, "SELECT quantity_num FROM entries WHERE invoice = "
                     "(SELECT guid FROM invoices WHERE id = ?)", cn_id)[0]
    inv_q, = _rows(p, "SELECT quantity_num FROM entries WHERE invoice = "
                      "(SELECT guid FROM invoices WHERE id = '000001')")[0]
    assert cn_q == -1 and inv_q == 1
    for disc_type, disc_how, paytype, billto, b_tax, d in _rows(
        p, "SELECT i_disc_type, i_disc_how, b_paytype, billto_type, b_taxable, date FROM entries"
    ):
        assert (disc_type, disc_how, paytype, billto, b_tax) == ("PERCENT", "PRETAX", 1, None, 1)
        assert d != "2026-08-01 10:59:00"
    # documents: neutral time, no bill-to
    for opened, posted, billto in _rows(p, "SELECT date_opened, date_posted, billto_type FROM invoices"):
        assert opened.endswith("10:59:00") and posted.endswith("10:59:00")
        assert billto is None
    # the application is a lot link
    (ttype, desc) = _rows(
        p, "SELECT s.string_val, t.description FROM slots s JOIN transactions t "
           "ON t.guid = s.obj_guid WHERE s.name = 'trans-txn-type' AND s.string_val = 'L'"
    )[0]
    assert desc == "Acme"
    memos = {m for m, in _rows(
        p, "SELECT memo FROM splits WHERE tx_guid = (SELECT obj_guid FROM slots "
           "WHERE name = 'trans-txn-type' AND string_val = 'L')"
    )}
    assert memos == {f"Offset between documents: Credit Note {cn_id} - Invoice 000001"}
    assert {a for a, in _rows(p, "SELECT action FROM splits WHERE tx_guid = (SELECT obj_guid "
                                 "FROM slots WHERE name = 'trans-txn-type' AND string_val = 'L')")} == {"Lot Link"}
    # lots: flags back to -1, no empty notes
    assert {f for f, in _rows(p, "SELECT is_closed FROM lots")} == {-1}
    assert _rows(p, "SELECT COUNT(*) FROM slots WHERE name = 'notes' AND string_val = '' "
                    "AND obj_guid IN (SELECT guid FROM lots)")[0][0] == 0
    # payment memo on both legs, no date-posted on P/L transactions
    assert _rows(p, "SELECT COUNT(*) FROM splits WHERE memo = 'check 12'")[0][0] == 2
    assert _rows(p, "SELECT COUNT(*) FROM slots WHERE name = 'date-posted' AND obj_guid IN "
                    "(SELECT obj_guid FROM slots WHERE name = 'trans-txn-type' "
                    "AND string_val IN ('P', 'L'))")[0][0] == 0
    # amounts never moved: the settlement reads as before
    doc = gc.get_invoice("000001")
    assert Decimal(doc["amount_due"]) == Decimal("0.00")
    # nothing left on a second pass
    with gc.open(readonly=False) as book:
        assert gc._upgrade_book_shapes(book) == {}


def _unposted_credit_note(gc, lines):
    gc.create_customer(name="Acme", currency="USD")
    cn = gc.create_credit_note(owner_id="000001", owner_type="customer")
    for desc, qty, price in lines:
        gc.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description=desc, quantity=qty, price=price,
        )
    return cn["id"]


def _stored_quantities(gc):
    with gc.open(readonly=True) as book:
        return [
            (d, n) for d, n in book.session.execute(text(
                "SELECT description, quantity_num FROM entries "
                "ORDER BY description"
            ))
        ]


def _as_written_by_1_4(gc):
    """An unposted credit note as server 1.2-1.4 stored it: the
    document quantity un-negated, and the blank entry defaults that
    are the old server's fingerprint."""
    with gc.open(readonly=False) as book:
        book.session.execute(text(
            "UPDATE entries SET quantity_num = -quantity_num, "
            "i_disc_type = '', i_disc_how = '', b_paytype = 0"
        ))
        book.save()


def _upgrade(gc):
    with gc.open(readonly=False) as book:
        out = gc._upgrade_book_shapes(book)
        book.save()
    return out


def test_unposted_credit_note_converts_by_fingerprint_not_by_sign(business_book):
    """A pre-1.5 credit note with a negative line beside a positive
    one: the first cut could not tell it from a desktop credit note
    and left it unconverted. The row says who wrote it."""
    gc = GnuCashBook(str(business_book))
    _unposted_credit_note(gc, [("a", "1", "10"), ("b", "-1", "3")])
    desktop_shape = _stored_quantities(gc)
    assert desktop_shape == [("a", -1), ("b", 1)]
    _as_written_by_1_4(gc)
    assert _stored_quantities(gc) == [("a", 1), ("b", -1)]

    out = _upgrade(gc)

    assert out.get("credit_note_entries_migrated") == 1
    assert "credit_note_entries_unresolved" not in out
    assert _stored_quantities(gc) == desktop_shape
    # Once: the fingerprint went with the same save.
    again = _upgrade(gc)
    assert "credit_note_entries_migrated" not in again
    assert _stored_quantities(gc) == desktop_shape


@pytest.mark.parametrize("lines", [
    [("fee", "-1", "40")],                      # all entered negative
    [("a", "1", "10")],                         # all entered positive
    [("a", "1", "10"), ("b", "-1", "3")],       # mixed
])
def test_current_credit_note_is_never_reconverted(business_book, lines):
    """Review C9. A credit note line entered as -1 is stored +1, by
    this server and by desktop (gncEntrySetDocQuantity). "Every
    stored quantity positive" was read as the old shape, and the
    next unrelated business write negated it: a 40 restocking charge
    became a 40 credit."""
    gc = GnuCashBook(str(business_book))
    cn = _unposted_credit_note(gc, lines)
    before = _stored_quantities(gc)
    total_before = gc.get_invoice(cn, owner_type="customer")["total"]

    # An unrelated business write runs the converters.
    other = gc.create_invoice(customer_id="000001")["id"]
    assert other != cn      # credit notes share the invoice numbering
    gc.add_invoice_entry(
        invoice_id=other, account="Income:Sales",
        description="zz other", quantity="1", price="5",
    )
    posted = gc.post_invoice(
        invoice_id=other, post_account="Assets:Accounts Receivable",
    )

    assert "credit_note_entries_migrated" not in posted
    assert [q for q in _stored_quantities(gc) if q[0] != "zz other"] == before
    assert gc.get_invoice(cn, owner_type="customer")["total"] == total_before


def test_desktop_written_credit_note_is_left_alone(business_book):
    """Desktop writes PERCENT / PRETAX on every entry."""
    gc = GnuCashBook(str(business_book))
    _unposted_credit_note(gc, [("fee", "-1", "40")])
    with gc.open(readonly=False) as book:
        book.session.execute(text(
            "UPDATE entries SET i_disc_type = 'PERCENT', "
            "i_disc_how = 'PRETAX', b_paytype = 1"
        ))
        book.save()
    before = _stored_quantities(gc)
    out = _upgrade(gc)
    assert "credit_note_entries_migrated" not in out
    assert _stored_quantities(gc) == before


def test_new_splits_carry_the_epoch_reconcile_date_and_nulls_convert(test_book):
    """The plain-transaction twin (2026-09-29, "Twin probe"): desktop
    stores an unreconciled split's reconcile_date as time64 0; the
    server left it NULL. New splits carry the epoch; a converting
    write fills every NULL and reports the count."""
    gc = GnuCashBook(str(test_book))
    p = str(test_book)
    nulls_before = _rows(p, "SELECT COUNT(*) FROM splits WHERE reconcile_date IS NULL")[0][0]
    assert nulls_before > 0  # the fixture predates the convention
    gc.create_transaction(
        description="Twin probe",
        splits=[{"account": "Expenses:Groceries", "amount": "100"},
                {"account": "Assets:Checking", "amount": "-100"}],
        trans_date=date(2026, 9, 29),
    )
    assert {r for r, in _rows(
        p, "SELECT quote(reconcile_date) FROM splits WHERE tx_guid = "
           "(SELECT guid FROM transactions WHERE description = 'Twin probe')"
    )} == {"'1970-01-01 00:00:00'"}
    with gc.open(readonly=False) as book:
        out = gc._upgrade_book_shapes(book)
        book.save()
    assert out["split_reconcile_dates_filled"] == nulls_before
    assert _rows(p, "SELECT COUNT(*) FROM splits WHERE reconcile_date IS NULL")[0][0] == 0
    with gc.open(readonly=False) as book:
        assert "split_reconcile_dates_filled" not in gc._upgrade_book_shapes(book)


def test_terms_refcount_and_credit_note_flag_follow_desktop(business_book):
    """Billterm twin (2026-09-29): desktop's refcount on a term is the
    number of documents (and customers, vendors) referencing it, and
    every plain document carries credit-note 0."""
    gc = GnuCashBook(str(business_book))
    p = str(business_book)
    gc.create_billterm(name="Net 30", due_days=30)
    gc.create_customer(name="Acme")
    gc.create_invoice(customer_id="000001", term="Net 30")
    gc.create_invoice(customer_id="000001", term="Net 30")
    assert _rows(p, "SELECT refcount FROM billterms WHERE name = 'Net 30'")[0][0] == 2
    assert _rows(p, "SELECT int64_val FROM slots WHERE name = 'credit-note' AND obj_guid = "
                    "(SELECT guid FROM invoices WHERE id = '000001')")[0][0] == 0
    gc.delete_invoice("000002")
    assert _rows(p, "SELECT refcount FROM billterms WHERE name = 'Net 30'")[0][0] == 1
    # Old shapes: refcount 0 and no flag; the converter recounts and completes.
    with gc.open(readonly=False) as book:
        book.session.execute(text("UPDATE billterms SET refcount = 0"))
        book.session.execute(text("DELETE FROM slots WHERE name = 'credit-note'"))
        book.save()
    with gc.open(readonly=False) as book:
        out = gc._upgrade_book_shapes(book)
        book.save()
    assert out["billterm_refcounts_recomputed"] == 1
    assert out["credit_note_flags_completed"] == 1
    assert _rows(p, "SELECT refcount FROM billterms WHERE name = 'Net 30'")[0][0] == 1
    assert _rows(p, "SELECT COUNT(*) FROM slots WHERE name = 'credit-note' AND int64_val = 0")[0][0] == 1


def test_prices_carry_gnucash_source_and_neutral_time(test_book):
    """Price twin (2026-09-29): desktop's price editor shows any source
    string outside gnc-pricedb.h's list as Invalid — every generator
    price (``user:market_data``) did — and stores the date at the
    neutral time. The writer validates and maps; the converter
    rewrites old rows."""
    import pytest
    gc = GnuCashBook(str(test_book))
    p = str(test_book)
    gc.create_commodity(mnemonic="AAPL", fullname="Apple", namespace="NASDAQ")
    gc.create_price(commodity="AAPL", namespace="NASDAQ", value="200",
                    price_date=date(2026, 9, 29))
    gc.create_price(commodity="AAPL", namespace="NASDAQ", value="178.70",
                    price_date=date(2026, 9, 28), source="user:market_data")
    rows = _rows(p, "SELECT source, type, date, value_num, value_denom "
                    "FROM prices ORDER BY date")
    # 178.70 is stored reduced, 1787/10, as the editor wrote it.
    assert rows == [("Finance::Quote", "last", "2026-09-28 10:59:00", 1787, 10),
                    ("user:price", "last", "2026-09-29 10:59:00", 200, 1)]
    # Update in place keeps the shape.
    gc.create_price(commodity="AAPL", namespace="NASDAQ", value="199.50",
                    price_date=date(2026, 9, 29))
    assert _rows(p, "SELECT value_num, value_denom, date FROM prices "
                    "WHERE source = 'user:price'") == [(399, 2, "2026-09-29 10:59:00")]
    with pytest.raises(ValueError, match="Invalid in the price editor"):
        gc.create_price(commodity="AAPL", namespace="NASDAQ", value="1",
                        price_date=date(2026, 9, 27), source="user:whatever")
    with pytest.raises(ValueError, match="not one GnuCash recognizes"):
        gc.create_price(commodity="AAPL", namespace="NASDAQ", value="1",
                        price_date=date(2026, 9, 27), price_type="close")
    # Old shapes: an unknown source, the server's local-midnight
    # date, an unreduced value — plus a row desktop stamped at some
    # other time, which the converter must leave alone.
    from datetime import datetime, timezone
    midnight = datetime(2026, 9, 29).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with gc.open(readonly=False) as book:
        book.session.execute(text(
            "UPDATE prices SET source = 'user:market-data', date = :m, "
            "value_num = 17870, value_denom = 100"
        ), {"m": midnight})
        book.session.execute(text(
            "INSERT INTO prices (guid, commodity_guid, currency_guid, date, "
            "source, type, value_num, value_denom) SELECT 'feedfacefeedfacefeedfacefeedface', "
            "commodity_guid, currency_guid, '2026-09-29 20:44:14', "
            "'user:price-editor', 'last', 0, 1 FROM prices LIMIT 1"
        ))
        book.save()
    with gc.open(readonly=False) as book:
        out = gc._upgrade_book_shapes(book)
        book.save()
    assert out["price_sources_normalized"] == 2
    assert out["price_dates_normalized"] == 2
    assert out["price_values_reduced"] == 2
    assert sorted(_rows(p, "SELECT source, date, value_num, value_denom FROM prices")) == [
        ("Finance::Quote", "2026-09-29 10:59:00", 1787, 10),
        ("Finance::Quote", "2026-09-29 10:59:00", 1787, 10),
        ("user:price-editor", "2026-09-29 20:44:14", 0, 1),
    ]
