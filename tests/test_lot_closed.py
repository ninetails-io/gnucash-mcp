"""``lots.is_closed`` is GnuCash's tri-state, read GnuCash's way.

From ``libgnucash/engine/gnc-lot.cpp`` (stable, fetched 2026-09-10)::

    #define LOT_CLOSED_UNKNOWN (-1)

``gnc_lot_is_closed`` recomputes when the stored flag is negative;
``gnc_lot_get_balance`` caches FALSE for a lot with no splits, TRUE
for a zero balance, FALSE otherwise. Desktop resets a lot to UNKNOWN
every time it adds or removes a split, so a book that has been
through desktop carries -1 on most lots. The server used to read -1
as closed (and write it to mean closed) — every open lot desktop had
touched disappeared from ``list_lots``. Found on the MariaDB loop,
where a desktop-saved copy of Alex had 63 of 117 lots at -1.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import piecash
import pytest

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.book._base import (
    _LOT_CLOSED,
    _LOT_CLOSED_UNKNOWN,
    _LOT_OPEN,
    _lot_is_closed,
)


class _FakeSplit:
    def __init__(self, quantity: str):
        self.quantity = Decimal(quantity)


class _FakeLot:
    def __init__(self, flag, *quantities: str):
        self.is_closed = flag
        self.splits = [_FakeSplit(q) for q in quantities]


class TestGnuCashTriState:
    def test_constants_match_gnc_lot_cpp(self):
        assert _LOT_CLOSED_UNKNOWN == -1
        assert _LOT_OPEN == 0
        assert _LOT_CLOSED == 1

    def test_stored_flag_is_the_answer(self):
        assert _lot_is_closed(_FakeLot(1, "10")) is True
        assert _lot_is_closed(_FakeLot(0)) is False

    def test_unknown_with_no_splits_is_open(self):
        assert _lot_is_closed(_FakeLot(-1)) is False

    def test_unknown_with_zero_balance_is_closed(self):
        assert _lot_is_closed(_FakeLot(-1, "10", "-10")) is True

    def test_unknown_with_a_balance_is_open(self):
        """The bug: desktop-touched open lots read as closed."""
        assert _lot_is_closed(_FakeLot(-1, "3.5511")) is False

    def test_none_flag_computes(self):
        assert _lot_is_closed(_FakeLot(None, "5", "-5")) is True


@pytest.fixture
def desktop_touched_book(tmp_path: Path) -> Path:
    """One open lot and one sold-out lot, both flagged -1 the way
    desktop leaves them after touching a split."""
    path = tmp_path / "lots.gnucash"
    book = piecash.create_book(
        sqlite_file=str(path), currency="USD", keep_foreign_keys=False,
    )
    usd = book.default_currency
    root = book.root_account
    stk = piecash.Commodity(
        namespace="NASDAQ", mnemonic="STK", fullname="Stock", fraction=10000,
    )
    assets = piecash.Account(
        name="Assets", type="ASSET", parent=root, commodity=usd, placeholder=1,
    )
    cash = piecash.Account(name="Cash", type="BANK", parent=assets, commodity=usd)
    holding = piecash.Account(
        name="STK", type="STOCK", parent=assets, commodity=stk,
    )
    book.flush()
    # piecash refuses splits on a lot whose flag is truthy — -1
    # included — so build the lots open and flip the flag afterwards,
    # which is the state desktop leaves behind.
    open_lot = piecash.Lot(title="open lot", account=holding, is_closed=_LOT_OPEN)
    sold_lot = piecash.Lot(title="sold lot", account=holding, is_closed=_LOT_OPEN)
    piecash.Transaction(
        currency=usd, description="buy open", post_date=date(2026, 1, 5),
        splits=[
            piecash.Split(account=holding, value=Decimal("100"), quantity=Decimal("10"), lot=open_lot),
            piecash.Split(account=cash, value=Decimal("-100")),
        ],
    )
    piecash.Transaction(
        currency=usd, description="buy sold", post_date=date(2026, 1, 6),
        splits=[
            piecash.Split(account=holding, value=Decimal("50"), quantity=Decimal("5"), lot=sold_lot),
            piecash.Split(account=cash, value=Decimal("-50")),
        ],
    )
    piecash.Transaction(
        currency=usd, description="sell sold", post_date=date(2026, 2, 6),
        splits=[
            piecash.Split(account=holding, value=Decimal("-60"), quantity=Decimal("-5"), lot=sold_lot),
            piecash.Split(account=cash, value=Decimal("60")),
        ],
    )
    book.save()
    for lot in (open_lot, sold_lot):
        lot.is_closed = _LOT_CLOSED_UNKNOWN
    book.save()
    book.close()
    return path


class TestDesktopTouchedLots:
    def test_list_lots_keeps_the_open_lot(self, desktop_touched_book):
        gb = GnuCashBook(str(desktop_touched_book))
        lots = gb.list_lots(account="Assets:STK", compact=False)["lots"]
        assert [lot["title"] for lot in lots] == ["open lot"]
        assert lots[0]["is_closed"] is False

    def test_get_lot_computes_from_the_balance(self, desktop_touched_book):
        gb = GnuCashBook(str(desktop_touched_book))
        lots = gb.list_lots(
            account="Assets:STK", include_closed=True, compact=False,
        )["lots"]
        by_title = {lot["title"]: lot for lot in lots}
        assert gb.get_lot(by_title["open lot"]["guid"])["is_closed"] is False
        assert gb.get_lot(by_title["sold lot"]["guid"])["is_closed"] is True

    def test_assign_to_a_desktop_touched_open_lot(self, desktop_touched_book):
        """Without the cache step piecash raises 'Lot is closed' on
        the -1 flag."""
        gb = GnuCashBook(str(desktop_touched_book))
        lots = gb.list_lots(account="Assets:STK", compact=False)["lots"]
        txn = gb.create_transaction(
            description="buy more",
            splits=[
                {"account": "Assets:STK", "amount": "20", "quantity": "2"},
                {"account": "Assets:Cash", "amount": "-20"},
            ],
            trans_date=date(2026, 3, 1),
            check_duplicates=False,
        )
        stk_split = next(
            s for s in gb.get_transaction(txn["guid"])["splits"]
            if s["account"] == "Assets:STK"
        )
        result = gb.assign_split_to_lot(
            split_guid=stk_split["guid"], lot_guid=lots[0]["guid"],
        )
        assert result["status"] == "assigned"
        assert Decimal(result["quantity"]) == Decimal("12")
        assert result["is_closed"] is False

    def test_close_lot_refuses_a_balance(self, desktop_touched_book):
        gb = GnuCashBook(str(desktop_touched_book))
        lots = gb.list_lots(account="Assets:STK", compact=False)["lots"]
        with pytest.raises(ValueError, match="still holds 10.0000 STK"):
            gb.close_lot(lots[0]["guid"])

    def test_close_lot_caches_one_on_a_zero_balance(self, desktop_touched_book):
        gb = GnuCashBook(str(desktop_touched_book))
        lots = gb.list_lots(
            account="Assets:STK", include_closed=True, compact=False,
        )["lots"]
        sold = next(lot for lot in lots if lot["title"] == "sold lot")
        # Reads as closed already (computed), so close_lot refuses as
        # "already closed" — flip the stored flag to open first to
        # exercise the write.
        with gb.open(readonly=False) as b:
            lot = b.session.query(piecash.Lot).filter_by(guid=sold["guid"]).first()
            lot.is_closed = _LOT_OPEN
            b.save()
        assert gb.close_lot(sold["guid"])["status"] == "closed"
        with gb.open(readonly=True) as b:
            lot = b.session.query(piecash.Lot).filter_by(guid=sold["guid"]).first()
            assert lot.is_closed == _LOT_CLOSED


class TestChokepointLock:
    """Grep-the-source: ``lot.is_closed`` is read in one place and
    never written as a literal."""

    BOOK_DIR = Path(__file__).resolve().parent.parent / "src" / "gnucash_mcp" / "book"

    def test_no_reads_outside_the_chokepoint(self):
        offenders = []
        for path in sorted(self.BOOK_DIR.glob("*.py")):
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or "``" in stripped:
                    continue
                if re.search(r"\.is_closed\b", line) and not re.search(
                    r"\.is_closed\s*=[^=]", line
                ):
                    if path.name == "_base.py":  # the two helpers live here
                        continue
                    if "is_closed=" in line:  # constructor kwarg, a write
                        continue
                    offenders.append(f"{path.name}:{lineno}: {stripped}")
        assert not offenders, "\n".join(offenders)

    def test_every_split_assignment_caches_the_flag_first(self):
        """piecash's guard reads the raw column for truth, so each
        ``x.lot = lot`` must be preceded by ``_lot_cache_flag(lot)``
        within a few lines."""
        offenders = []
        for path in sorted(self.BOOK_DIR.glob("*.py")):
            lines = path.read_text().splitlines()
            for i, line in enumerate(lines):
                m = re.search(r"^\s*\w+\.lot = (\w+)\s*$", line)
                if not m:
                    continue
                window = "\n".join(lines[max(0, i - 6):i])
                if f"_lot_cache_flag({m.group(1)})" not in window:
                    offenders.append(f"{path.name}:{i + 1}: {line.strip()}")
        assert not offenders, "\n".join(offenders)

    def test_the_assignment_scanner_is_not_vacuous(self):
        text = (self.BOOK_DIR / "investments.py").read_text()
        assert re.search(r"^\s*split\.lot = lot\s*$", text, re.M)

    def test_no_literal_writes(self):
        offenders = []
        for path in sorted(self.BOOK_DIR.glob("*.py")):
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"is_closed\s*=\s*-?\d", line):
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        assert not offenders, "\n".join(offenders)
