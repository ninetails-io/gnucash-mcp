"""The price a cross-commodity transaction implies, written as
GnuCash desktop writes it.

Cross-currency twin, 2026-09-30: in desktop a USD 100 → EUR 90
transfer left an empty split action and one price row — EUR/USD,
exactly ``10/9``, at the neutral time, type ``transaction``. piecash
wrote ``Buy`` into the action and a six-decimal half-even value at
local midnight. ``book/_piecash_shapes.py`` replaces piecash's
``Split.validate`` with ports of GnuCash's two writers:
``record_price`` (Transaction.cpp) for priced accounts and the
exchange dialog's ``create_price`` (dialog-transfer.cpp) for the rest.
"""

from datetime import date
from decimal import Decimal

import piecash
import pytest
from sqlalchemy import text

from gnucash_mcp.book import GnuCashBook


@pytest.fixture
def fx_book(tmp_path):
    path = tmp_path / "fx.gnucash"
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    usd = book.default_currency
    eur = piecash.factories.create_currency_from_ISO("EUR")
    gbp = piecash.factories.create_currency_from_ISO("GBP")
    jpy = piecash.factories.create_currency_from_ISO("JPY")
    book.session.add_all([eur, gbp, jpy])
    aapl = piecash.Commodity(
        namespace="NASDAQ", mnemonic="AAPL", fullname="Apple", fraction=10000,
    )
    book.session.add(aapl)
    root = book.root_account
    assets = piecash.Account(name="Assets", type="ASSET", parent=root,
                             commodity=usd, placeholder=1)
    piecash.Account(name="Checking", type="BANK", parent=assets, commodity=usd)
    piecash.Account(name="EUR Savings", type="BANK", parent=assets, commodity=eur)
    piecash.Account(name="GBP Savings", type="BANK", parent=assets, commodity=gbp)
    piecash.Account(name="JPY Cash", type="BANK", parent=assets, commodity=jpy)
    piecash.Account(name="AAPL", type="STOCK", parent=assets, commodity=aapl)
    book.save()
    book.close()
    return path


def _prices(path):
    with piecash.open_book(str(path), readonly=True, open_if_lock=True,
                           do_backup=False) as book:
        rows = book.session.execute(text(
            "SELECT c.mnemonic, u.mnemonic, p.date, p.source, p.type, "
            "p.value_num, p.value_denom FROM prices p "
            "JOIN commodities c ON c.guid = p.commodity_guid "
            "JOIN commodities u ON u.guid = p.currency_guid "
            "ORDER BY p.date, c.mnemonic"
        )).fetchall()
    return [tuple(str(v) if i == 2 else v for i, v in enumerate(r)) for r in rows]


def _transfer(gc, to_account, amount, quantity, day, frm="Assets:Checking",
              currency=None, **kw):
    return gc.create_transaction(
        "transfer",
        splits=[
            {"account": frm, "amount": f"-{amount}"},
            {"account": to_account, "amount": amount, "quantity": quantity},
        ],
        trans_date=day, currency=currency, check_duplicates=False, **kw,
    )


def test_currency_transfer_matches_the_desktop_specimen(fx_book):
    """The twin itself: USD 100 → EUR 90 on a USD book."""
    gc = GnuCashBook(str(fx_book))
    _transfer(gc, "Assets:EUR Savings", "100", "90", date(2026, 9, 29))
    assert _prices(fx_book) == [
        ("EUR", "USD", "2026-09-29 10:59:00", "user:xfer-dialog",
         "transaction", 10, 9),
    ]
    with gc.open(readonly=True) as book:
        actions = {
            r[0] for r in book.session.execute(text("SELECT action FROM splits"))
        }
    assert actions == {""}, "desktop stamps no Buy/Sell on a transfer"


def test_to_amount_transfer_matches_the_second_desktop_specimen(fx_book):
    """Desktop, 2026-09-30, a NEW transfer on a day with no price,
    typed as a to-amount (USD 70 → EUR 60): ``EUR/USD | 2026-09-21
    10:59:00 | user:xfer-dialog | transaction | 7/6``. This is the
    path the server's two-amount API corresponds to, source string
    included."""
    gc = GnuCashBook(str(fx_book))
    _transfer(gc, "Assets:EUR Savings", "70", "60", date(2026, 9, 21))
    assert _prices(fx_book) == [
        ("EUR", "USD", "2026-09-21 10:59:00", "user:xfer-dialog",
         "transaction", 7, 6),
    ]


def test_money_leaving_the_foreign_account_stores_the_same_direction(fx_book):
    """EUR 90 → USD 100: ``from`` is EUR, ``to`` is the default; the
    dialog stores commodity EUR, currency USD without a swap."""
    gc = GnuCashBook(str(fx_book))
    _transfer(gc, "Assets:EUR Savings", "100", "90", date(2026, 9, 28))
    gc.create_transaction(
        "back",
        splits=[
            {"account": "Assets:EUR Savings", "amount": "-55", "quantity": "-50"},
            {"account": "Assets:Checking", "amount": "55"},
        ],
        trans_date=date(2026, 9, 29), check_duplicates=False,
    )
    assert _prices(fx_book)[-1] == (
        "EUR", "USD", "2026-09-29 10:59:00", "user:xfer-dialog",
        "transaction", 11, 10,
    )


def test_stock_purchase_is_record_price(fx_book):
    """A priced account (STOCK) goes through Transaction.cpp's
    ``record_price``: value/amount rounded half-up to
    ``scu(currency) × 10000``, the denominator kept, source
    ``user:split-register``."""
    gc = GnuCashBook(str(fx_book))
    _transfer(gc, "Assets:AAPL", "1000", "3", date(2026, 9, 29))
    assert _prices(fx_book) == [
        ("AAPL", "USD", "2026-09-29 10:59:00", "user:split-register",
         "transaction", 333333333, 1000000),
    ]
    # 2000/3 = 666.6666666… rounds half-up to …667, not half-even.
    gc2 = GnuCashBook(str(fx_book))
    _transfer(gc2, "Assets:AAPL", "2000", "3", date(2026, 9, 30))
    assert _prices(fx_book)[-1][5:] == (666666667, 1000000)


def test_stock_register_action_is_buy_or_sell(fx_book):
    """Desktop's stock register sets an empty Action to Buy or Sell
    when shares are entered (``gnc_split_register_check_stock_shares``;
    the twin's ``AAPL 3`` row came back ``Buy``). Only the split in
    the priced account gets it, a caller's own action is kept, and a
    later reconcile does not stamp a split desktop left blank."""
    gc = GnuCashBook(str(fx_book))
    _transfer(gc, "Assets:AAPL", "1000", "3", date(2026, 9, 29))
    gc.create_transaction(
        "sell",
        splits=[
            {"account": "Assets:AAPL", "amount": "-400", "quantity": "-1"},
            {"account": "Assets:Checking", "amount": "400"},
        ],
        trans_date=date(2026, 9, 30), check_duplicates=False,
    )
    with gc.open(readonly=False) as book:
        rows = book.session.execute(text(
            "SELECT a.name, s.quantity_num, s.action FROM splits s "
            "JOIN accounts a ON a.guid = s.account_guid ORDER BY s.quantity_num"
        )).fetchall()
        assert [(n, act) for n, _q, act in rows if n == "AAPL"] == [
            ("AAPL", "Sell"), ("AAPL", "Buy"),
        ]
        assert {act for n, _q, act in rows if n == "Checking"} == {""}
        # A split desktop left blank (entered from the bank side).
        book.session.execute(text(
            "UPDATE splits SET action = '' WHERE action = 'Buy'"
        ))
        book.save()
    gc.reconcile_account(
        "Assets:AAPL", statement_date=date(2026, 9, 30),
        statement_balance="2", reconcile_all=True,
    )
    with gc.open(readonly=True) as book:
        assert book.session.execute(text(
            "SELECT COUNT(*) FROM splits WHERE action = 'Buy'"
        )).scalar() == 0


def test_same_day_preferred_source_is_left_alone(fx_book):
    """A quote from the price editor outranks the register: the
    implied price does not supersede it (``oldsource < source``)."""
    gc = GnuCashBook(str(fx_book))
    gc.create_price("AAPL", "NASDAQ", "340", price_date=date(2026, 9, 29),
                    source="user:price-editor")
    _transfer(gc, "Assets:AAPL", "1000", "3", date(2026, 9, 29))
    assert _prices(fx_book) == [
        ("AAPL", "USD", "2026-09-29 10:59:00", "user:price-editor",
         "last", 340, 1),
    ]


def test_second_purchase_the_same_day_updates_in_place(fx_book):
    """``record_price`` finds its own earlier row for the day and
    rewrites it — one row per day, the latest transaction's rate.
    piecash kept the first and ignored every later one."""
    gc = GnuCashBook(str(fx_book))
    _transfer(gc, "Assets:AAPL", "1000", "4", date(2026, 9, 29))
    _transfer(gc, "Assets:AAPL", "1020", "4", date(2026, 9, 29))
    assert _prices(fx_book) == [
        ("AAPL", "USD", "2026-09-29 10:59:00", "user:split-register",
         "transaction", 255000000, 1000000),
    ]


def test_dialog_price_is_not_dithered_by_a_sub_rounding_change(fx_book):
    """``update_price`` compares the two rates rounded
    (``round_price``: 1/10000 between currencies) and leaves the row
    when they agree; a real change rewrites the value, not the
    source."""
    gc = GnuCashBook(str(fx_book))
    _transfer(gc, "Assets:EUR Savings", "100", "90", date(2026, 9, 29))
    # 1000.00 / 900.01 rounds to the same 1.1111 as 10/9.
    _transfer(gc, "Assets:EUR Savings", "1000", "900.01", date(2026, 9, 29))
    assert _prices(fx_book)[0][5:] == (10, 9)
    _transfer(gc, "Assets:EUR Savings", "100", "80", date(2026, 9, 29))
    assert _prices(fx_book) == [
        ("EUR", "USD", "2026-09-29 10:59:00", "user:xfer-dialog",
         "transaction", 5, 4),
    ]


def test_two_foreign_currencies_keep_the_transfer_direction(fx_book):
    """Neither side is the default: ``new_price`` stores from → to
    as the money moved. EUR 90 → GBP 78 in a EUR-denominated
    transaction."""
    gc = GnuCashBook(str(fx_book))
    _transfer(gc, "Assets:EUR Savings", "100", "90", date(2026, 9, 28))
    _transfer(gc, "Assets:GBP Savings", "90", "78", date(2026, 9, 29),
              frm="Assets:EUR Savings", currency="EUR")
    assert _prices(fx_book)[-1] == (
        "EUR", "GBP", "2026-09-29 10:59:00", "user:xfer-dialog",
        "transaction", 13, 15,
    )


def test_reconciling_an_old_split_mints_no_price(fx_book):
    """The price is recorded when a split is entered or its amounts
    change. piecash wrote one on ANY change to the split, so
    reconciling an old cross-currency split whose price had been
    deleted put it back."""
    gc = GnuCashBook(str(fx_book))
    _transfer(gc, "Assets:EUR Savings", "100", "90", date(2026, 9, 1))
    with gc.open(readonly=False) as book:
        book.session.execute(text("DELETE FROM prices"))
        book.save()
    gc.reconcile_account(
        "Assets:EUR Savings", statement_date=date(2026, 9, 2),
        statement_balance="90", reconcile_all=True,
    )
    assert _prices(fx_book) == []


def test_valuation_reads_the_implied_rate(fx_book):
    """The row values the holding on the balance sheet at once."""
    gc = GnuCashBook(str(fx_book))
    _transfer(gc, "Assets:EUR Savings", "100", "90", date(2026, 9, 29))
    with gc.open(readonly=True) as book:
        eur = next(c for c in book.commodities if c.mnemonic == "EUR")
        rate = gc._rates_as_of(book, date(2026, 9, 30))[eur.guid]
    assert rate == Decimal(10) / Decimal(9)


def test_converter_restates_piecash_rows_and_keeps_register_denominators(fx_book):
    """Books written before 2026-09-30 hold piecash's rows: the
    split's own direction, six decimals, local midnight,
    ``user:split-register``. Engineered byte-faithfully, then
    converted: the currency row becomes the exchange dialog's exact
    ``10/9``; the stock row keeps ``record_price``'s fixed
    denominator (it is NOT reduced like a quote) and only moves to
    the neutral time."""
    from datetime import datetime, timezone

    gc = GnuCashBook(str(fx_book))
    # Equity-funded so the transaction currency is EUR and the USD
    # leg is the cross-commodity split: piecash stored USD/EUR.
    gc.create_transaction(
        "fund",
        splits=[
            {"account": "Assets:EUR Savings", "amount": "90"},
            {"account": "Assets:Checking", "amount": "-90", "quantity": "-100"},
        ],
        trans_date=date(2026, 9, 1), currency="EUR", check_duplicates=False,
    )
    _transfer(gc, "Assets:AAPL", "1000", "3", date(2026, 9, 1))
    midnight = datetime(2026, 9, 1).astimezone(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S")
    with gc.open(readonly=False) as book:
        usd = book.default_currency.guid
        eur = next(c.guid for c in book.commodities if c.mnemonic == "EUR")
        book.session.execute(text(
            "UPDATE prices SET commodity_guid = :usd, currency_guid = :eur, "
            "value_num = 900000, value_denom = 1000000, date = :m, "
            "source = 'user:split-register' WHERE source = 'user:xfer-dialog'"
        ), {"usd": usd, "eur": eur, "m": midnight})
        book.session.execute(text(
            "UPDATE prices SET date = :m WHERE source = 'user:split-register' "
            "AND value_denom = 1000000 AND value_num = 333333333"
        ), {"m": midnight})
        book.save()
    assert ("USD", "EUR", midnight, "user:split-register", "transaction",
            900000, 1000000) in _prices(fx_book)

    with gc.open(readonly=False) as book:
        out = gc._upgrade_book_shapes(book)
        book.save()
    assert out["implied_prices_restated"] == 1
    assert out["price_dates_normalized"] == 2
    assert "price_values_reduced" not in out
    assert _prices(fx_book) == [
        ("AAPL", "USD", "2026-09-01 10:59:00", "user:split-register",
         "transaction", 333333333, 1000000),
        ("EUR", "USD", "2026-09-01 10:59:00", "user:xfer-dialog",
         "transaction", 10, 9),
    ]
    # Idempotent: the rows are off midnight, so nothing is revisited.
    with gc.open(readonly=False) as book:
        again = gc._upgrade_book_shapes(book)
        book.save()
    assert not any(k.startswith(("price_", "implied_")) for k in again)


_STALE_FILLERS = (
    "SELECT COUNT(*) FROM slots WHERE "
    "(double_val = 0 AND slot_type <> 2) OR "
    "(timespec_val IS NULL AND slot_type <> 6)"
)


def _scalar(path, sql):
    with piecash.open_book(str(path), readonly=True, open_if_lock=True,
                           do_backup=False) as book:
        return book.session.execute(text(sql)).scalar()


def test_every_slot_the_server_writes_has_desktops_filler_columns(fx_book):
    """GnuCash's SQL backend leaves a slot's unused ``double_val``
    NULL and its ``timespec_val`` at the epoch; piecash's defaults
    were 0.0 and NULL, on every ORM-written slot (``date-posted`` on
    each transaction, notes, void slots). Desktop's own row, from
    the twin: ``date-posted | 10 | 0 | NULL | NULL | 1970-01-01
    00:00:00 | 0 | 1 | 20260929``."""
    gc = GnuCashBook(str(fx_book))
    made = _transfer(gc, "Assets:EUR Savings", "100", "90",
                     date(2026, 9, 29), notes="with a note")
    gc.void_transaction(made["guid"], reason="filler probe")
    gc.create_account("Noted", "BANK", parent="Assets", notes="n")
    assert _scalar(fx_book, "SELECT COUNT(*) FROM slots") >= 6
    assert _scalar(fx_book, _STALE_FILLERS) == 0
    with piecash.open_book(str(fx_book), readonly=True, open_if_lock=True,
                           do_backup=False) as book:
        row = book.session.execute(text(
            "SELECT slot_type, int64_val, string_val, double_val, "
            "timespec_val, numeric_val_num, numeric_val_denom, gdate_val "
            "FROM slots WHERE name = 'date-posted'"
        )).first()
    assert tuple(str(v) if i in (4, 7) else v for i, v in enumerate(row)) == (
        10, 0, None, None, "1970-01-01 00:00:00", 0, 1, "20260929",
    )


def test_converter_normalizes_piecash_fillers(fx_book):
    gc = GnuCashBook(str(fx_book))
    _transfer(gc, "Assets:EUR Savings", "100", "90", date(2026, 9, 29),
              notes="a note")
    with gc.open(readonly=False) as book:
        # piecash's shape, as every pre-2026-09-30 book holds it.
        book.session.execute(text(
            "UPDATE slots SET double_val = 0.0, timespec_val = NULL"
        ))
        book.save()
    stale = _scalar(fx_book, _STALE_FILLERS)
    assert stale >= 2
    with gc.open(readonly=False) as book:
        out = gc._upgrade_book_shapes(book)
        book.save()
    assert out["slot_fillers_normalized"] == stale
    assert _scalar(fx_book, _STALE_FILLERS) == 0
    with gc.open(readonly=False) as book:
        assert "slot_fillers_normalized" not in gc._upgrade_book_shapes(book)


def test_new_account_carries_the_balance_limit_frame(fx_book):
    """Desktop's account dialog leaves an empty ``balance-limit``
    frame on every account it saves; the twin's row was
    ``balance-limit | 9 | 0 | NULL | NULL | 1970-01-01 00:00:00 |
    <frame guid> | 0 | 1 | NULL`` with no children."""
    gc = GnuCashBook(str(fx_book))
    gc.create_account("EUR Savings 2", "ASSET", parent="Assets", commodity="EUR")
    with piecash.open_book(str(fx_book), readonly=True, open_if_lock=True,
                           do_backup=False) as book:
        rows = book.session.execute(text(
            "SELECT s.name, s.slot_type, s.int64_val, s.string_val, "
            "s.double_val, s.timespec_val, s.guid_val, s.numeric_val_num, "
            "s.numeric_val_denom, s.gdate_val FROM slots s "
            "JOIN accounts a ON a.guid = s.obj_guid WHERE a.name = 'EUR Savings 2'"
        )).fetchall()
        assert len(rows) == 1
        name, st, iv, sv, dv, tv, frame, nn, nd, gd = rows[0]
        assert (name, st, iv, sv, dv, str(tv), nn, nd, gd) == (
            "balance-limit", 9, 0, None, None, "1970-01-01 00:00:00", 0, 1, None,
        )
        assert len(frame) == 32
        assert book.session.execute(
            text("SELECT COUNT(*) FROM slots WHERE obj_guid = :g"), {"g": frame},
        ).scalar() == 0
