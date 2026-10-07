"""A cross-currency payment is booked the way GnuCash desktop books it:
in the TRANSFER account's currency.

Payment twin, 2026-09-30. Desktop settled a EUR 900 receivable with
USD 1,000 from Checking and wrote a USD transaction: Checking
1000.00/1000.00, the receivable value -1000.00 (USD) and quantity
-900.00 (EUR), both action ``Payment``, one ``trans-txn-type = P``
slot, and the price ``EUR/USD 10/9 user:xfer-dialog``.
``gncOwnerCreatePaymentLotSecs`` (gncOwner.c) sets a new payment
transaction's currency to the transfer account's commodity. The
server booked the same payment in the invoice's currency, with
realized FX as a zero-value split; and, reading desktop's row, summed
USD into a EUR lot and called the invoice overpaid by 100.
"""

from datetime import date
from decimal import Decimal

import piecash
import pytest
from sqlalchemy import text

from gnucash_mcp.book import GnuCashBook


def _book(business_book, *, quotes, extra_currencies=()):
    """USD book with a EUR receivable and payable, a EUR customer and
    vendor, and entered EUR/USD quotes ``{day: rate}``."""
    gb = GnuCashBook(str(business_book))
    with gb.open(readonly=False) as bk:
        eur = piecash.Commodity(
            namespace="CURRENCY", mnemonic="EUR", fullname="Euro", fraction=100,
        )
        bk.session.add(eur)
        others = {}
        for code in extra_currencies:
            others[code] = piecash.Commodity(
                namespace="CURRENCY", mnemonic=code, fullname=code, fraction=100,
            )
            bk.session.add(others[code])
        bk.flush()
        assets = next(a for a in bk.accounts if a.fullname == "Assets")
        liabilities = next(a for a in bk.accounts if a.fullname == "Liabilities")
        bk.session.add(piecash.Account(
            name="AR EUR", type="RECEIVABLE", parent=assets, commodity=eur,
        ))
        bk.session.add(piecash.Account(
            name="AP EUR", type="PAYABLE", parent=liabilities, commodity=eur,
        ))
        bk.session.add(piecash.Account(
            name="EUR Bank", type="BANK", parent=assets, commodity=eur,
        ))
        for code, comm in others.items():
            bk.session.add(piecash.Account(
                name=f"{code} Bank", type="BANK", parent=assets, commodity=comm,
            ))
        bk.save()
    for day, rate in quotes.items():
        gb.create_price("EUR", "CURRENCY", rate, price_date=day)
    gb.create_customer(name="Berlin Digital GmbH", currency="EUR")
    gb.create_vendor(name="Hamburg Hosting AG", currency="EUR")
    return gb


def _invoice(gb, amount="900", post_date="2026-09-22"):
    inv = gb.create_invoice(customer_id="000001", date_opened=post_date)
    gb.add_invoice_entry(
        invoice_id=inv["id"], account="Income:Sales",
        description="Twin invoice", quantity="1", price=amount,
    )
    gb.post_invoice(
        invoice_id=inv["id"], post_account="Assets:AR EUR", post_date=post_date,
    )
    return inv["id"]


def _payment_rows(path, doc_id):
    """The payment transaction(s) in a document's lot, as desktop's
    rows were read: (currency, [(account, action, value, quantity)])."""
    with piecash.open_book(str(path), readonly=True, open_if_lock=True,
                           do_backup=False) as book:
        txns = book.session.execute(text(
            "SELECT DISTINCT t.guid, c.mnemonic, t.post_date FROM transactions t "
            "JOIN commodities c ON c.guid = t.currency_guid "
            "JOIN splits s ON s.tx_guid = t.guid "
            "JOIN invoices i ON i.post_lot = s.lot_guid "
            "WHERE i.id = :id AND t.guid <> i.post_txn ORDER BY t.post_date"
        ), {"id": doc_id}).fetchall()
        out = []
        for guid, cur, _d in txns:
            splits = book.session.execute(text(
                "SELECT a.name, s.action, s.value_num, s.value_denom, "
                "s.quantity_num, s.quantity_denom FROM splits s "
                "JOIN accounts a ON a.guid = s.account_guid "
                "WHERE s.tx_guid = :g ORDER BY a.name"
            ), {"g": guid}).fetchall()
            slots = book.session.execute(text(
                "SELECT name, string_val FROM slots WHERE obj_guid = :g"
            ), {"g": guid}).fetchall()
            out.append((cur, [
                (n, act, Decimal(vn) / vd, Decimal(qn) / qd)
                for n, act, vn, vd, qn, qd in splits
            ], sorted(tuple(r) for r in slots)))
    return out


def _prices_on(path, day):
    with piecash.open_book(str(path), readonly=True, open_if_lock=True,
                           do_backup=False) as book:
        return [tuple(r) for r in book.session.execute(text(
            "SELECT c.mnemonic, u.mnemonic, p.source, p.type, p.value_num, "
            "p.value_denom FROM prices p "
            "JOIN commodities c ON c.guid = p.commodity_guid "
            "JOIN commodities u ON u.guid = p.currency_guid "
            "WHERE p.date LIKE :d ORDER BY p.source"
        ), {"d": f"{day}%"}).fetchall()]


def test_payment_at_the_posting_rate_is_desktops_row(business_book):
    """The specimen itself: no drift, so no FX split, and every row
    is what desktop wrote."""
    gb = _book(business_book, quotes={
        date(2026, 9, 21): "1.111111", date(2026, 9, 29): "1.111111",
    })
    inv = _invoice(gb)
    result = gb.pay_invoice(
        invoice_id=inv, payment_account="Assets:Checking", amount="900",
        payment_date="2026-09-30",
    )
    assert result["status"] == "paid"
    assert "fx_realized" not in result
    assert _payment_rows(business_book, inv) == [(
        "USD",
        [
            ("AR EUR", "Payment", Decimal("-1000"), Decimal("-900")),
            ("Checking", "Payment", Decimal("1000"), Decimal("1000")),
        ],
        [("trans-txn-type", "P")],
    )]
    assert _prices_on(business_book, "2026-09-30") == [
        ("EUR", "USD", "user:xfer-dialog", "transaction", 10, 9),
    ]
    doc = gb.get_invoice(inv)
    assert (doc["amount_paid"], doc["amount_due"]) == ("900.00", "0.00")
    assert doc["payments"][0]["amount"] == "900.00"


def test_rate_drift_is_a_real_balanced_gain(business_book):
    """Posted at 10/9, paid at 1.20: USD 1,080 arrives for a
    receivable carried at USD 1,000. The receivable leg is relieved
    at its carrying amount, the gain is an ordinary USD credit, and
    the day's price is the rate PAID — not the posting rate the
    receivable leg's value/quantity still shows."""
    gb = _book(business_book, quotes={
        date(2026, 9, 21): "1.111111", date(2026, 9, 29): "1.20",
    })
    inv = _invoice(gb)
    result = gb.pay_invoice(
        invoice_id=inv, payment_account="Assets:Checking", amount="900",
        payment_date="2026-09-30",
    )
    assert result["fx_realized"] == {
        "amount": "80.00", "currency": "USD", "direction": "gain",
        "account": "Income:Foreign Exchange Gain/Loss",
    }
    ((currency, splits, slots),) = _payment_rows(business_book, inv)
    assert currency == "USD"
    assert splits == [
        ("AR EUR", "Payment", Decimal("-1000"), Decimal("-900")),
        ("Checking", "Payment", Decimal("1080"), Decimal("1080")),
        ("Foreign Exchange Gain/Loss", "Payment", Decimal("-80"), Decimal("-80")),
    ]
    assert sum(v for _n, _a, v, _q in splits) == 0
    assert slots == [("trans-txn-type", "P")]
    assert _prices_on(business_book, "2026-09-30") == [
        ("EUR", "USD", "user:xfer-dialog", "transaction", 6, 5),
    ]
    doc = gb.get_invoice(inv)
    assert (doc["status"], doc["amount_due"]) == ("paid", "0.00")


def test_partial_payments_settle_in_the_documents_currency(business_book):
    """Two payments at two rates: the lot's balance is read in EUR
    (the receivable splits' quantities), whatever each payment's USD
    value was."""
    gb = _book(business_book, quotes={
        date(2026, 9, 21): "1.111111", date(2026, 9, 24): "1.20",
        date(2026, 9, 28): "1.00",
    })
    inv = _invoice(gb)
    first = gb.pay_invoice(
        invoice_id=inv, payment_account="Assets:Checking", amount="300",
        payment_date="2026-09-24",
    )
    assert first["remaining_balance"] == "600.00"
    doc = gb.get_invoice(inv)
    assert (doc["amount_paid"], doc["amount_due"]) == ("300.00", "600.00")
    second = gb.pay_invoice(
        invoice_id=inv, payment_account="Assets:Checking", amount="600",
        payment_date="2026-09-28",
    )
    assert second["status"] == "paid"
    assert second["fx_realized"]["direction"] == "loss"
    doc = gb.get_invoice(inv)
    assert (doc["amount_paid"], doc["amount_due"]) == ("900.00", "0.00")
    assert [p["amount"] for p in doc["payments"]] == ["300.00", "600.00"]
    rows = _payment_rows(business_book, inv)
    assert [cur for cur, _s, _sl in rows] == ["USD", "USD"]
    for _cur, splits, _slots in rows:
        assert sum(v for _n, _a, v, _q in splits) == 0
    # Over-paying is still refused, measured in EUR.
    with pytest.raises(ValueError, match="has no outstanding|exceeds|already"):
        gb.pay_invoice(
            invoice_id=inv, payment_account="Assets:Checking", amount="1",
            payment_date="2026-09-28",
        )


def test_vendor_bill_mirrors_the_receipt(business_book):
    """A EUR bill paid from USD: cash leaves, the payable is debited
    at its carrying amount, and paying more USD than it was carried
    at is a loss (a debit)."""
    gb = _book(business_book, quotes={
        date(2026, 9, 21): "1.111111", date(2026, 9, 29): "1.20",
    })
    bill = gb.create_bill(vendor_id="000001", date_opened="2026-09-22")
    gb.add_bill_entry(
        bill_id=bill["id"], account="Expenses:Office Supplies",
        description="hosting", quantity="1", price="900",
    )
    gb.post_invoice(
        invoice_id=bill["id"], post_account="Liabilities:AP EUR",
        post_date="2026-09-22", owner_type="vendor",
    )
    result = gb.pay_invoice(
        invoice_id=bill["id"], payment_account="Assets:Checking",
        amount="900", payment_date="2026-09-30", owner_type="vendor",
    )
    assert result["fx_realized"]["direction"] == "loss"
    assert result["fx_realized"]["amount"] == "80.00"
    with piecash.open_book(str(business_book), readonly=True,
                           open_if_lock=True, do_backup=False) as book:
        rows = book.session.execute(text(
            "SELECT c.mnemonic, a.name, s.value_num, s.quantity_num "
            "FROM splits s JOIN accounts a ON a.guid = s.account_guid "
            "JOIN transactions t ON t.guid = s.tx_guid "
            "JOIN commodities c ON c.guid = t.currency_guid "
            "WHERE s.action = 'Payment' ORDER BY a.name"
        )).fetchall()
    assert [tuple(r) for r in rows] == [
        ("USD", "AP EUR", 100000, 90000),
        ("USD", "Checking", -108000, -108000),
        ("USD", "Foreign Exchange Gain/Loss", 8000, 8000),
    ]
    assert _prices_on(business_book, "2026-09-30") == [
        ("EUR", "USD", "user:xfer-dialog", "transaction", 6, 5),
    ]


def test_same_currency_payment_is_unchanged(business_book):
    """Paid from a EUR account: nothing crosses a currency, the
    transaction is EUR, no price is written."""
    gb = _book(business_book, quotes={date(2026, 9, 21): "1.111111"})
    inv = _invoice(gb)
    gb.pay_invoice(
        invoice_id=inv, payment_account="Assets:EUR Bank", amount="900",
        payment_date="2026-09-30",
    )
    assert _payment_rows(business_book, inv) == [(
        "EUR",
        [
            ("AR EUR", "Payment", Decimal("-900"), Decimal("-900")),
            ("EUR Bank", "Payment", Decimal("900"), Decimal("900")),
        ],
        [("trans-txn-type", "P")],
    )]
    assert _prices_on(business_book, "2026-09-30") == []


def test_third_currency_pay_account(business_book):
    """EUR invoice, USD book, paid from a GBP account: the
    transaction is GBP; the FX account is USD, so its split carries
    the drift as a GBP value and a USD quantity."""
    gb = _book(
        business_book, extra_currencies=("GBP",),
        quotes={date(2026, 9, 21): "1.10", date(2026, 9, 29): "1.20"},
    )
    # 1 GBP = 1.25 USD throughout; EUR/GBP 0.88 at posting, 0.96 at
    # payment (the pair the payment converts by).
    for day in (date(2026, 9, 21), date(2026, 9, 29)):
        gb.create_price("GBP", "CURRENCY", "1.25", price_date=day)
    gb.create_price("EUR", "CURRENCY", "0.88", currency="GBP",
                    price_date=date(2026, 9, 21))
    gb.create_price("EUR", "CURRENCY", "0.96", currency="GBP",
                    price_date=date(2026, 9, 29))
    inv = _invoice(gb, amount="1000")
    result = gb.pay_invoice(
        invoice_id=inv, payment_account="Assets:GBP Bank", amount="1000",
        payment_date="2026-09-30",
    )
    assert result["status"] == "paid"
    ((currency, splits, _slots),) = _payment_rows(business_book, inv)
    assert currency == "GBP"
    by_name = {n: (v, q) for n, _a, v, q in splits}
    # GBP 960 received for a receivable carried at GBP 880: a GBP 80
    # gain, USD 100 in the FX account's own currency.
    assert by_name == {
        "GBP Bank": (Decimal("960"), Decimal("960")),
        "AR EUR": (Decimal("-880"), Decimal("-1000")),
        "Foreign Exchange Gain/Loss": (Decimal("-80"), Decimal("-100")),
    }
    assert result["fx_realized"]["amount"] == "100.00"
    assert gb.get_invoice(inv)["amount_due"] == "0.00"


def test_lot_balance_reads_a_mixed_lot(business_book):
    """One lot, three shapes: the posting (EUR), an old server
    payment (EUR transaction, as every pre-2026-09-30 book holds
    it), and a new one (USD transaction). The balance is EUR
    throughout — and summing VALUES, the old read, is not."""
    gb = _book(business_book, quotes={
        date(2026, 9, 21): "1.111111", date(2026, 9, 29): "1.111111",
    })
    inv = _invoice(gb)
    gb.pay_invoice(
        invoice_id=inv, payment_account="Assets:Checking", amount="300",
        payment_date="2026-09-25",
    )
    # Re-express that payment the old way: a EUR transaction, the
    # receivable split -300/-300, the bank split 300 EUR / 333.33 USD.
    with gb.open(readonly=False) as book:
        eur = next(c.guid for c in book.commodities if c.mnemonic == "EUR")
        tx = book.session.execute(text(
            "SELECT s.tx_guid FROM splits s JOIN invoices i ON "
            "i.post_lot = s.lot_guid WHERE s.tx_guid <> i.post_txn"
        )).scalar()
        book.session.execute(text(
            "UPDATE transactions SET currency_guid = :e WHERE guid = :t"
        ), {"e": eur, "t": tx})
        book.session.execute(text(
            "UPDATE splits SET value_num = -30000, value_denom = 100 "
            "WHERE tx_guid = :t AND lot_guid IS NOT NULL"
        ), {"t": tx})
        book.session.execute(text(
            "UPDATE splits SET value_num = 30000, value_denom = 100 "
            "WHERE tx_guid = :t AND lot_guid IS NULL"
        ), {"t": tx})
        book.save()
    doc = gb.get_invoice(inv)
    assert (doc["amount_paid"], doc["amount_due"]) == ("300.00", "600.00")
    gb.pay_invoice(
        invoice_id=inv, payment_account="Assets:Checking", amount="600",
        payment_date="2026-09-30",
    )
    doc = gb.get_invoice(inv)
    assert (doc["status"], doc["amount_paid"], doc["amount_due"]) == (
        "paid", "900.00", "0.00",
    )
    assert [p["amount"] for p in doc["payments"]] == ["300.00", "600.00"]
    assert [cur for cur, _s, _sl in _payment_rows(business_book, inv)] == [
        "EUR", "USD",
    ]


def test_discount_settlement_balances_in_the_pay_currency(business_book):
    """A 2% early-payment discount on a EUR 1,000 invoice paid from
    USD after the rate moved: bank, discount, realized FX and the
    receivable's carrying amount all sit in one USD transaction that
    balances in money, and the lot closes in EUR."""
    gb = _book(business_book, quotes={
        date(2026, 9, 21): "1.10", date(2026, 9, 24): "1.20",
    })
    gb.create_billterm(
        name="2/10 Net 30", due_days=30, discount_days=10,
        discount_percent="2",
    )
    inv = gb.create_invoice(
        customer_id="000001", date_opened="2026-09-22", term="2/10 Net 30",
    )
    gb.add_invoice_entry(
        invoice_id=inv["id"], account="Income:Sales",
        description="work", quantity="1", price="1000",
    )
    gb.post_invoice(
        invoice_id=inv["id"], post_account="Assets:AR EUR",
        post_date="2026-09-22",
    )
    result = gb.pay_invoice(
        invoice_id=inv["id"], payment_account="Assets:Checking",
        amount="980", payment_date="2026-09-25", apply_discount=True,
    )
    assert result["status"] == "paid"
    ((currency, splits, _slots),) = _payment_rows(business_book, inv["id"])
    assert currency == "USD"
    by_name = {n: (v, q) for n, _a, v, q in splits}
    # EUR 980 at 1.20 = USD 1,176 in; EUR 20 discount at 1.20 =
    # USD 24; the receivable was carried at EUR 1,000 × 1.10 =
    # USD 1,100; the difference, USD 100, is the realized gain.
    assert by_name["Checking"] == (Decimal("1176"), Decimal("1176"))
    assert by_name["AR EUR"] == (Decimal("-1100"), Decimal("-1000"))
    assert by_name["Foreign Exchange Gain/Loss"] == (
        Decimal("-100"), Decimal("-100"),
    )
    discount = next(
        v for n, v in by_name.items()
        if n not in ("Checking", "AR EUR", "Foreign Exchange Gain/Loss")
    )
    assert discount == (Decimal("24"), Decimal("24"))
    assert sum(v for v, _q in by_name.values()) == 0
    assert gb.get_invoice(inv["id"])["amount_due"] == "0.00"


def test_customer_credit_note_refund_sends_the_pay_currency(business_book):
    """Refunding a EUR credit note from USD: cash LEAVES, so the
    dialog's direction is USD → EUR, and the transaction is still
    the pay account's currency."""
    gb = _book(business_book, quotes={
        date(2026, 9, 21): "1.111111", date(2026, 9, 29): "1.111111",
    })
    cn = gb.create_credit_note(
        owner_id="000001", owner_type="customer", date_opened="2026-09-22",
    )
    gb.add_invoice_entry(
        invoice_id=cn["id"], account="Income:Sales",
        description="returned", quantity="1", price="900",
    )
    gb.post_invoice(
        invoice_id=cn["id"], post_account="Assets:AR EUR",
        post_date="2026-09-22", owner_type="customer",
    )
    result = gb.pay_invoice(
        invoice_id=cn["id"], payment_account="Assets:Checking",
        amount="900", payment_date="2026-09-30", owner_type="customer",
    )
    assert (result["status"], result["type"]) == ("paid", "credit_note")
    ((currency, splits, _slots),) = _payment_rows(business_book, cn["id"])
    assert currency == "USD"
    assert splits == [
        ("AR EUR", "Payment", Decimal("1000"), Decimal("900")),
        ("Checking", "Payment", Decimal("-1000"), Decimal("-1000")),
    ]
    assert _prices_on(business_book, "2026-09-30") == [
        ("EUR", "USD", "user:xfer-dialog", "transaction", 10, 9),
    ]


def test_posting_rate_ignores_implied_and_temporary_rows(business_book):
    """The rate the server chooses for a new payment comes from
    quotes somebody entered or fetched. Neither a transaction's
    implied rate nor the ``temporary`` row desktop leaks on a
    cross-currency post (twin: ``USD/EUR 9/10 temporary last``, the
    same instant as the real price) is one: with only those on file
    near the pay date the guard sees the real quote's age."""
    from gnucash_mcp.book import StaleFXRateError

    gb = _book(business_book, quotes={date(2026, 9, 1): "1.111111"})
    inv = _invoice(gb, post_date="2026-09-02")
    with gb.open(readonly=False) as book:
        usd = book.default_currency.guid
        eur = next(c.guid for c in book.commodities if c.mnemonic == "EUR")
        for guid, comm, curr, source, ptype, num, den in (
            ("a" * 32, eur, usd, "user:xfer-dialog", "transaction", 10, 9),
            ("b" * 32, usd, eur, "temporary", "last", 9, 10),
        ):
            book.session.execute(text(
                "INSERT INTO prices (guid, commodity_guid, currency_guid, "
                "date, source, type, value_num, value_denom) VALUES "
                "(:g, :c, :u, '2026-09-29 10:59:00', :s, :t, :n, :d)"
            ), {"g": guid, "c": comm, "u": curr, "s": source, "t": ptype,
                "n": num, "d": den})
        book.save()
    with pytest.raises(StaleFXRateError) as exc:
        gb.pay_invoice(
            invoice_id=inv, payment_account="Assets:Checking", amount="900",
            payment_date="2026-09-30",
        )
    assert "2026-09-01" in str(exc.value)


def test_default_currency_has_no_latest_price_of_its_own(business_book):
    """Bookkeeper ruling B10 (2026-09-30): a price row stored the
    other way round (USD/EUR, the way an older writer or desktop's
    leaked ``temporary`` row stores it) must not read as "the latest
    price of USD" in a USD book. ``list_commodities`` prints the
    default-currency cell as such, and ``get_latest_price`` answers
    null, as it always did."""
    gb = _book(business_book, quotes={date(2026, 9, 21): "1.111111"})
    with gb.open(readonly=False) as book:
        usd = book.default_currency.guid
        eur = next(c.guid for c in book.commodities if c.mnemonic == "EUR")
        book.session.execute(text(
            "INSERT INTO prices (guid, commodity_guid, currency_guid, date, "
            "source, type, value_num, value_denom) VALUES "
            "('c' || substr('0123456789abcdef0123456789abcdef', 2), :u, :e, "
            "'2026-09-23 10:59:00', 'temporary', 'last', 9, 10)"
        ), {"u": usd, "e": eur})
        book.save()
    lines = gb.list_commodities().splitlines()
    usd_line = next(ln for ln in lines if ln.startswith("CURRENCY:USD"))
    assert usd_line.split("\t")[2:] == ["— (default currency)"]
    eur_line = next(ln for ln in lines if ln.startswith("CURRENCY:EUR"))
    assert "1.111111 USD (2026-09-21)" in eur_line
    verbose = gb.list_commodities(compact=False)
    usd_entry = next(
        e for e in verbose["commodities"]["CURRENCY"] if e["mnemonic"] == "USD"
    )
    assert usd_entry["latest_price"] is None
    assert usd_entry["default_currency"] is True
    assert gb.get_latest_price("USD", "CURRENCY") is None
