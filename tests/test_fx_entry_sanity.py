"""Cross-commodity implied-rate sanity warning.

Entry paths (create_transaction, create_transactions, replace_splits,
update_transaction) flag — NON-BLOCKING — when a cross-commodity split's
implied rate (|value| / |quantity|) is grossly off the latest price on
file, catching decimal slips and inverted pairs at the source. The user
authored the rate, so it warns rather than refuses.
"""

from datetime import date

import piecash
from piecash import factories

from gnucash_mcp.book import GnuCashBook

_SLIP = [
    {"account": "Assets:Checking", "amount": "-1100"},
    {"account": "Assets:EUR Savings", "amount": "1100", "quantity": "10000"},
]
_GOOD = [
    {"account": "Assets:Checking", "amount": "-1100"},
    {"account": "Assets:EUR Savings", "amount": "1100", "quantity": "1000"},
]


def _usd_eur_book(tmp_path, *, with_price=True):
    """USD-default book, a USD checking and a EUR savings account, and
    (optionally) a EUR/USD price of 1.10 — so the in-band rate is 1.10
    USD/EUR and the slip (×10 quantity) implies 0.11."""
    path = tmp_path / "fx.gnucash"
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    usd = book.default_currency
    eur = factories.create_currency_from_ISO("EUR")
    assets = piecash.Account(
        name="Assets", type="ASSET", commodity=usd,
        parent=book.root_account, placeholder=True,
    )
    piecash.Account(name="Checking", type="BANK", commodity=usd, parent=assets)
    piecash.Account(
        name="EUR Savings", type="BANK", commodity=eur, parent=assets,
    )
    if with_price:
        book.session.add(piecash.Price(
            commodity=eur, currency=usd,
            date=date.today(), value="1.10", type="last",
        ))
    book.save()
    book.close()
    return str(path)


def _fx_warns(result):
    """Pull fx_rate_sanity warnings from a dict response (warnings are
    dicts on create/update, plain strings on replace_splits)."""
    out = []
    for w in result.get("warnings", []):
        if isinstance(w, dict) and w.get("type") == "fx_rate_sanity":
            out.append(w["message"])
        elif isinstance(w, str) and "implied rate" in w:
            out.append(w)
    return out


def test_create_transaction_flags_decimal_slip(tmp_path):
    gb = GnuCashBook(_usd_eur_book(tmp_path))
    res = gb.create_transaction(
        description="slip", check_duplicates=False, splits=_SLIP,
    )
    warns = _fx_warns(res)
    assert warns and "10.0x" in warns[0]
    # Non-blocking: the transaction is still created.
    assert res["status"] == "created"


def test_create_transaction_quiet_on_in_band_rate(tmp_path):
    gb = GnuCashBook(_usd_eur_book(tmp_path))
    res = gb.create_transaction(
        description="good", check_duplicates=False, splits=_GOOD,
    )
    assert not _fx_warns(res)


def test_no_warning_without_reference_price(tmp_path):
    # Nothing to compare against -> silent (don't nag price-free books).
    gb = GnuCashBook(_usd_eur_book(tmp_path, with_price=False))
    res = gb.create_transaction(
        description="slip", check_duplicates=False, splits=_SLIP,
    )
    assert not _fx_warns(res)


def test_dry_run_surfaces_the_warning(tmp_path):
    # The whole point: catch the slip on the preview, before writing.
    gb = GnuCashBook(_usd_eur_book(tmp_path))
    res = gb.create_transaction(
        description="slip", check_duplicates=False, splits=_SLIP,
        dry_run=True,
    )
    assert res["dry_run"] is True
    assert _fx_warns(res)


def test_batch_surfaces_slip_in_warnings_table(tmp_path):
    gb = GnuCashBook(_usd_eur_book(tmp_path))
    res = gb.create_transactions(transactions=[
        {"ref": "r1", "date": date.today(), "description": "good",
         "splits": _GOOD},
        {"ref": "r2", "date": date.today(), "description": "slip",
         "splits": _SLIP},
    ])
    table = res.get("warnings", "")
    assert "r2" in table and "implied rate" in table
    # The in-band row isn't flagged.
    assert "\nr1\t" not in table


def test_replace_splits_flags_slip(tmp_path):
    gb = GnuCashBook(_usd_eur_book(tmp_path))
    created = gb.create_transaction(
        description="orig", check_duplicates=False, splits=_GOOD,
    )
    res = gb.replace_splits(guid=created["guid"], splits=_SLIP)
    warns = _fx_warns(res)
    assert warns and "implied rate" in warns[0]
