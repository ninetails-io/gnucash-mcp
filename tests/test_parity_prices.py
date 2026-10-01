"""Same-day prices, against GnuCash's own engine.

The twin commissioned by ruling 2 on finding C24 of the pre-release
adversarial review (``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``).
The review read ``gnc_pricedb_add_price`` as "one price per pair per
day"; the desktop gate had shown two same-day rows on screen. Both
were looking at one rule from two sides. Driving ``add_price`` through
the engine (``fixtures/engine_twin.py``) and reading the SQL table:

* a new price whose source ranks EQUAL OR BETTER than the day's
  existing price REPLACES it — the old row is deleted, whatever its
  source, and in whichever direction of the pair it was quoted;
* a new price whose source ranks WORSE is rejected by the price
  database in memory ("Better price already in DB") — but the price
  was committed before ``add_price`` was asked, so its row is already
  in the SQL table and stays there. The next load reads both.

So a desktop-written SQL book does hold several same-day prices for a
pair, and never holds a worse-ranked price that was entered FIRST.

``create_price`` replaces within a source only. Where that leaves
different rows than the engine, the scenario is marked: the ruling is
that the server's rule does not move until this twin has been read
and ruled on, so those cases are strict expected-failures — visible
here, and red the day someone changes the rule without saying so.

Regenerate after a GnuCash upgrade with
``uv run python tests/test_parity_prices.py record``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook

_HERE = Path(__file__).resolve().parent
_RECORDED = _HERE / "fixtures" / "parity_prices_engine.json"
sys.path.insert(0, str(_HERE / "fixtures"))
from engine_twin import engine_run, find_gnucash_cli  # noqa: E402

# (commodity, quote currency, day of June 2026, value, source, type)
EDITOR = "user:price-editor"
QUOTE = "Finance::Quote"
REGISTER = "user:split-register"
USER = "user:price"

SCENARIOS = {
    "same_source_twice": [
        ("EUR", "USD", 1, "1.10", EDITOR, "last"),
        ("EUR", "USD", 1, "1.12", EDITOR, "last"),
    ],
    "better_source_after_worse": [
        ("EUR", "USD", 1, "1.09", REGISTER, "transaction"),
        ("EUR", "USD", 1, "1.11", QUOTE, "last"),
    ],
    "worse_source_after_better": [
        ("EUR", "USD", 1, "1.11", QUOTE, "last"),
        ("EUR", "USD", 1, "1.10", USER, "last"),
    ],
    "three_sources_best_first": [
        ("EUR", "USD", 1, "1.10", EDITOR, "last"),
        ("EUR", "USD", 1, "1.11", QUOTE, "last"),
        ("EUR", "USD", 1, "1.09", REGISTER, "transaction"),
    ],
    "other_direction_same_day": [
        ("EUR", "USD", 1, "1.10", EDITOR, "last"),
        ("USD", "EUR", 1, "0.90", EDITOR, "last"),
    ],
    "different_days": [
        ("EUR", "USD", 1, "1.10", EDITOR, "last"),
        ("EUR", "USD", 2, "1.12", EDITOR, "last"),
    ],
}

# Where create_price leaves different rows than the engine today, and
# what the difference is.
SERVER_DIFFERS = {
    "better_source_after_worse":
        "the engine deletes the worse-ranked row; the server keeps both",
    "other_direction_same_day":
        "the engine deletes the opposite-direction row; the server keeps both",
}


def _book(path: Path) -> GnuCashBook:
    gb = GnuCashBook(str(path))
    # An account in EUR puts the currency in the book's commodity table.
    gb.create_account(
        name="Euro Cash", account_type="BANK", parent="Assets",
        commodity="EUR",
    )
    return gb


def _rows(path: Path) -> list[list]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select c.mnemonic, q.mnemonic, p.date, p.source, p.type, "
            "p.value_num, p.value_denom from prices p "
            "join commodities c on c.guid = p.commodity_guid "
            "join commodities q on q.guid = p.currency_guid"
        ).fetchall()
    finally:
        con.close()
    return sorted(
        [c, q, when, source, kind, str(Fraction(num, denom))]
        for c, q, when, source, kind, num, denom in rows
    )


def _engine_actions(scenario: str) -> list[str]:
    return [
        f"price|CURRENCY|{c}|{q}|{day}|6|2026|{Fraction(value)}|{source}|{kind}"
        for c, q, day, value, source, kind in SCENARIOS[scenario]
    ]


def _recorded() -> dict:
    return json.loads(_RECORDED.read_text())


def _server_params():
    for scenario in sorted(SCENARIOS):
        marks = []
        if scenario in SERVER_DIFFERS:
            marks.append(pytest.mark.xfail(
                strict=True,
                reason=f"C24, ruling 2 pending: {SERVER_DIFFERS[scenario]}",
            ))
        yield pytest.param(scenario, marks=marks)


@pytest.mark.parametrize("scenario", _server_params())
def test_create_price_leaves_the_rows_the_engine_leaves(
    business_book, scenario,
):
    gb = _book(business_book)
    for c, q, day, value, source, kind in SCENARIOS[scenario]:
        gb.create_price(
            commodity=c, namespace="CURRENCY", value=value, currency=q,
            price_date=date(2026, 6, day), price_type=kind, source=source,
        )
    assert _rows(business_book) == _recorded()[scenario]["rows"]


@pytest.mark.skipif(
    find_gnucash_cli() is None, reason="gnucash-cli not on this machine",
)
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_recording_is_what_the_engine_writes_today(business_book, scenario):
    _book(business_book)
    results = engine_run(business_book, _engine_actions(scenario))
    assert {"results": results, "rows": _rows(business_book)} == (
        _recorded()[scenario]
    )


def test_a_rejected_price_is_still_in_the_table():
    """The finding itself, read off the recording: the engine said no
    to the worse-ranked price, and its row is there anyway."""
    got = _recorded()["worse_source_after_better"]
    assert got["results"] == ["ok", "rejected"]
    assert [r[3] for r in got["rows"]] == [QUOTE, USER]
    # …while a better-ranked one takes the day.
    got = _recorded()["better_source_after_worse"]
    assert got["results"] == ["ok", "ok"]
    assert [r[3] for r in got["rows"]] == [QUOTE]


def record() -> None:
    import tempfile

    import tests.conftest as conftest

    out = {}
    for scenario in sorted(SCENARIOS):
        with tempfile.TemporaryDirectory() as tmp:
            path = conftest.business_book.__wrapped__(Path(tmp))
            _book(path)
            results = engine_run(path, _engine_actions(scenario))
            out[scenario] = {"results": results, "rows": _rows(path)}
        print(f"recorded {scenario}: {results}")
    _RECORDED.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["record"]:
        sys.path.insert(0, str(_HERE.parent))
        record()
