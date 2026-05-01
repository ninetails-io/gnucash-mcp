"""Tests for lot (cost basis tracking) functionality."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook


# ── Helpers ──────────────────────────────────────────────────


def _buy_shares(book: GnuCashBook, shares: int, price: Decimal, txn_date: date) -> str:
    """Create a buy transaction and return the VTSAX split GUID.

    Accounting: cash decreases (checking -total), investment increases (VTSAX +total in USD, +shares in quantity).
    """
    total = price * shares
    result = book.create_transaction(
        description=f"Buy {shares} VTSAX @ {price}",
        splits=[
            {"account": "Assets:Investments:VTSAX", "amount": str(total), "quantity": str(shares)},
            {"account": "Assets:Checking", "amount": str(-total)},
        ],
        trans_date=txn_date,
    )
    txn = book.get_transaction(result["guid"])
    for s in txn["splits"]:
        if s["account"] == "Assets:Investments:VTSAX":
            return s["guid"]
    raise RuntimeError("VTSAX split not found")


def _sell_shares(book: GnuCashBook, shares: int, price: Decimal, txn_date: date) -> str:
    """Create a sell transaction and return the VTSAX split GUID.

    Accounting: cash increases (checking +proceeds), investment decreases (VTSAX -proceeds in USD, -shares in quantity).
    """
    proceeds = price * shares
    result = book.create_transaction(
        description=f"Sell {shares} VTSAX @ {price}",
        splits=[
            {"account": "Assets:Investments:VTSAX", "amount": str(-proceeds), "quantity": str(-shares)},
            {"account": "Assets:Checking", "amount": str(proceeds)},
        ],
        trans_date=txn_date,
    )
    txn = book.get_transaction(result["guid"])
    for s in txn["splits"]:
        if s["account"] == "Assets:Investments:VTSAX":
            return s["guid"]
    raise RuntimeError("VTSAX split not found")


# ── TestCreateLot ────────────────────────────────────────────


class TestCreateLot:
    def test_create_lot_success(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        result = book.create_lot(
            account="Assets:Investments:VTSAX",
            title="VTSAX 2026-01-15",
        )
        assert result["status"] == "created"
        assert result["title"] == "VTSAX 2026-01-15"
        assert result["account"] == "Assets:Investments:VTSAX"
        # Response now emits a short collision-safe prefix (>=8 chars).
        assert len(result["guid"]) >= 8

    def test_create_lot_with_notes(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        result = book.create_lot(
            account="Assets:Investments:VTSAX",
            title="VTSAX Feb purchase",
            notes="Bought at $125/share",
        )
        assert result["notes"] == "Bought at $125/share"

    def test_create_lot_account_not_found(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        with pytest.raises(ValueError, match="Account not found"):
            book.create_lot(account="Assets:Nope", title="Bad lot")


# ── TestListLots ─────────────────────────────────────────────


class TestListLots:
    def test_list_lots_empty(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        result = book.list_lots(account="Assets:Investments:VTSAX", compact=False)
        assert result == []

    def test_list_lots_with_lots(self, investment_book: Path):
        """The default view shows lots with active positions. Empty
        lots (no splits assigned, or all splits voided) are filtered
        out — see ``test_list_lots_skips_empty_lots_in_default_view``."""
        book = GnuCashBook(str(investment_book))
        book.create_lot(account="Assets:Investments:VTSAX", title="Lot A")
        book.create_lot(account="Assets:Investments:VTSAX", title="Lot B")
        # Empty lots have zero remaining position, so the default
        # (open-positions) view skips them. ``include_closed=True``
        # surfaces them for callers who need the audit-trail view.
        result = book.list_lots(
            account="Assets:Investments:VTSAX",
            include_closed=True, compact=False,
        )
        assert len(result) == 2
        titles = {r["title"] for r in result}
        assert titles == {"Lot A", "Lot B"}

    def test_list_lots_skips_empty_lots_in_default_view(
        self, investment_book: Path,
    ):
        """Empty lots (no splits, or all splits zeroed by void) appear
        as "0 shares, 0 cost basis" rows that add noise to a
        portfolio listing. The bookkeeper flagged this as confusing
        when managing a real portfolio. The default ``include_closed
        =False`` view skips them; ``include_closed=True`` surfaces
        them."""
        book = GnuCashBook(str(investment_book))
        book.create_lot(account="Assets:Investments:VTSAX", title="Empty A")
        book.create_lot(account="Assets:Investments:VTSAX", title="Empty B")

        # Default view: zero empty lots show up.
        result = book.list_lots(
            account="Assets:Investments:VTSAX", compact=False,
        )
        assert result == []

        # Audit view: both surface.
        result = book.list_lots(
            account="Assets:Investments:VTSAX",
            include_closed=True, compact=False,
        )
        assert len(result) == 2

    def test_list_lots_exclude_closed(self, investment_book: Path):
        """Closed lots are filtered out by default; ``include_closed
        =True`` surfaces them. With the empty-lot filter, an "Open"
        lot needs an actual position to appear in the default view —
        so assign a non-zero buy split to it."""
        book = GnuCashBook(str(investment_book))
        lot_a = book.create_lot(
            account="Assets:Investments:VTSAX", title="Open Lot",
        )
        lot_b = book.create_lot(
            account="Assets:Investments:VTSAX", title="Closed Lot",
        )
        # Give Open Lot a real position so it survives the empty
        # filter — buy 10 shares and assign the buy split to lot_a.
        buy_guid = _buy_shares(book, 10, Decimal("125"), date(2026, 1, 15))
        book.assign_split_to_lot(
            split_guid=buy_guid, lot_guid=lot_a["guid"],
        )
        book.close_lot(guid=lot_b["guid"])

        # Default: exclude closed AND empty.
        result = book.list_lots(
            account="Assets:Investments:VTSAX", compact=False,
        )
        assert len(result) == 1
        assert result[0]["title"] == "Open Lot"

        # Include closed → all lots surface, even empty/closed ones.
        result = book.list_lots(
            account="Assets:Investments:VTSAX",
            include_closed=True, compact=False,
        )
        assert len(result) == 2


# ── TestGetLot ───────────────────────────────────────────────


class TestGetLot:
    def test_get_lot_not_found(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        with pytest.raises(ValueError, match="Lot not found"):
            book.get_lot(guid="00000000000000000000000000000000")

    def test_get_lot_empty(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="Empty Lot")
        result = book.get_lot(guid=lot["guid"])
        assert result["title"] == "Empty Lot"
        assert result["splits"] == []
        # _format_number renders quantities at 4 decimals (share-style)
        # and currency at 2 — empty lot is zero either way.
        assert result["summary"]["quantity"] == "0.0000"
        assert result["summary"]["cost_basis"] == "0.00"

    def test_get_lot_with_splits(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="Test Lot")
        split_guid = _buy_shares(book, 10, Decimal("125"), date(2026, 1, 15))
        book.assign_split_to_lot(split_guid=split_guid, lot_guid=lot["guid"])

        result = book.get_lot(guid=lot["guid"])
        assert len(result["splits"]) == 1
        assert Decimal(result["summary"]["quantity"]) == Decimal("10")
        assert Decimal(result["summary"]["cost_basis"]) == Decimal("1250")
        assert Decimal(result["summary"]["cost_per_share"]) == Decimal("125")


# ── TestAssignSplitToLot ─────────────────────────────────────


class TestAssignSplitToLot:
    def test_assign_purchase_split(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="Buy Lot")
        split_guid = _buy_shares(book, 8, Decimal("125"), date(2026, 1, 15))

        result = book.assign_split_to_lot(split_guid=split_guid, lot_guid=lot["guid"])
        assert result["status"] == "assigned"
        assert Decimal(result["quantity"]) == Decimal("8")
        assert Decimal(result["cost_basis"]) == Decimal("1000")

    def test_assign_sale_split(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="Sell Lot")

        # Buy 10 shares
        buy_guid = _buy_shares(book, 10, Decimal("125"), date(2026, 1, 15))
        book.assign_split_to_lot(split_guid=buy_guid, lot_guid=lot["guid"])

        # Sell 3 shares
        sell_guid = _sell_shares(book, 3, Decimal("130"), date(2026, 2, 1))
        result = book.assign_split_to_lot(split_guid=sell_guid, lot_guid=lot["guid"])

        assert result["status"] == "assigned"
        assert Decimal(result["quantity"]) == Decimal("7")

    def test_assign_split_not_found(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="Test")
        with pytest.raises(ValueError, match="Split not found"):
            book.assign_split_to_lot(
                split_guid="00000000000000000000000000000000",
                lot_guid=lot["guid"],
            )

    def test_assign_lot_not_found(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        split_guid = _buy_shares(book, 5, Decimal("125"), date(2026, 1, 15))
        with pytest.raises(ValueError, match="Lot not found"):
            book.assign_split_to_lot(
                split_guid=split_guid,
                lot_guid="00000000000000000000000000000000",
            )

    def test_assign_wrong_account(self, investment_book: Path):
        """Split from checking account can't be assigned to VTSAX lot."""
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="Test")

        # Get the checking split from a buy transaction
        txn_result = book.create_transaction(
            description="Buy VTSAX",
            splits=[
                {"account": "Assets:Investments:VTSAX", "amount": "1250", "quantity": "10"},
                {"account": "Assets:Checking", "amount": "-1250"},
            ],
            trans_date=date(2026, 1, 15),
        )
        txn = book.get_transaction(txn_result["guid"])
        checking_split_guid = None
        for s in txn["splits"]:
            if s["account"] == "Assets:Checking":
                checking_split_guid = s["guid"]
                break

        with pytest.raises(ValueError, match="does not match"):
            book.assign_split_to_lot(
                split_guid=checking_split_guid,
                lot_guid=lot["guid"],
            )


# ── TestCalculateLotGain ─────────────────────────────────────


class TestCalculateLotGain:
    def test_gain_with_explicit_price(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="Gain Test")
        split_guid = _buy_shares(book, 10, Decimal("125"), date(2026, 1, 15))
        book.assign_split_to_lot(split_guid=split_guid, lot_guid=lot["guid"])

        result = book.calculate_lot_gain(lot_guid=lot["guid"], sale_price="130")
        assert Decimal(result["shares"]) == Decimal("10")
        assert Decimal(result["cost_basis"]) == Decimal("1250")
        assert Decimal(result["sale_proceeds"]) == Decimal("1300")
        assert Decimal(result["capital_gain"]) == Decimal("50")

    def test_gain_with_latest_price(self, investment_book: Path):
        """Uses the $125 price from fixture when no explicit price given."""
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="Price Test")
        split_guid = _buy_shares(book, 10, Decimal("125"), date(2026, 1, 15))
        book.assign_split_to_lot(split_guid=split_guid, lot_guid=lot["guid"])

        result = book.calculate_lot_gain(lot_guid=lot["guid"])
        # Price is $125, cost is $125 → zero gain
        assert Decimal(result["capital_gain"]) == Decimal("0")
        assert Decimal(result["sale_proceeds"]) == Decimal("1250")

    def test_gain_partial_sale(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="Partial")
        split_guid = _buy_shares(book, 10, Decimal("125"), date(2026, 1, 15))
        book.assign_split_to_lot(split_guid=split_guid, lot_guid=lot["guid"])

        result = book.calculate_lot_gain(
            lot_guid=lot["guid"], shares="4", sale_price="130",
        )
        assert Decimal(result["shares"]) == Decimal("4")
        assert Decimal(result["cost_basis"]) == Decimal("500")
        assert Decimal(result["sale_proceeds"]) == Decimal("520")
        assert Decimal(result["capital_gain"]) == Decimal("20")

    def test_cost_basis_recovers_exactness_for_indivisible_per_share_cost(
        self, investment_book: Path,
    ):
        """A lot bought at a non-round per-share cost should recover
        the exact total cost basis when all shares are sold.

        Pre-fix, ``cost_per_share`` was formatted to 4 decimals via
        ``_lot_summary``, then re-parsed in ``calculate_lot_gain``
        and multiplied by shares — losing precision. A $100 / 3 share
        lot showed cost_basis $99.99 on a 3-share sale instead of
        $100.

        The prorate formula
        ``cost_basis = purchase_value * shares / purchase_quantity``
        recovers exactness for the all-shares case. Tax-relevant.
        """
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(
            account="Assets:Investments:VTSAX", title="Indivisible cost",
        )
        # Buy 3 shares at $33.3333.../share (total $100)
        result = book.create_transaction(
            description="Buy 3 VTSAX with non-round per-share cost",
            splits=[
                {
                    "account": "Assets:Investments:VTSAX",
                    "amount": "100",
                    "quantity": "3",
                },
                {"account": "Assets:Checking", "amount": "-100"},
            ],
            trans_date=date(2026, 1, 15),
        )
        txn = book.get_transaction(result["guid"])
        buy_guid = next(
            s["guid"] for s in txn["splits"]
            if s["account"] == "Assets:Investments:VTSAX"
        )
        book.assign_split_to_lot(
            split_guid=buy_guid, lot_guid=lot["guid"],
        )

        # Sell all 3 shares at $50/share. Cost basis MUST be exactly
        # $100, gain MUST be exactly $50 — not $99.99 / $50.01.
        gain = book.calculate_lot_gain(
            lot_guid=lot["guid"], sale_price="50",
        )
        assert Decimal(gain["cost_basis"]) == Decimal("100.00")
        assert Decimal(gain["sale_proceeds"]) == Decimal("150.00")
        assert Decimal(gain["capital_gain"]) == Decimal("50.00")

    def test_gain_no_remaining_shares(self, investment_book: Path):
        """Empty lot raises error."""
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="Empty")
        with pytest.raises(ValueError, match="no remaining shares"):
            book.calculate_lot_gain(lot_guid=lot["guid"], sale_price="130")

    def test_gain_no_remaining_shares_due_to_voided_buy(
        self, investment_book: Path,
    ):
        """When the lot's purchase was voided, the generic "no
        remaining shares" message hides the *cause*. The error
        should call out the voided splits explicitly so the caller
        knows to unvoid the underlying transaction (or pick a
        different lot) instead of staring at a vague error."""
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(
            account="Assets:Investments:VTSAX", title="Voided Buy Lot",
        )
        buy_guid = _buy_shares(book, 10, Decimal("125"), date(2026, 1, 15))
        book.assign_split_to_lot(
            split_guid=buy_guid, lot_guid=lot["guid"],
        )
        # Find and void the buy transaction.
        with book.open(readonly=True) as b:
            from piecash.core.transaction import Split
            split = b.session.query(Split).filter_by(guid=buy_guid).first()
            buy_txn_guid = split.transaction.guid
        book.void_transaction(
            guid=buy_txn_guid, reason="Mistaken purchase",
        )

        with pytest.raises(ValueError) as exc_info:
            book.calculate_lot_gain(
                lot_guid=lot["guid"], sale_price="130",
            )
        msg = str(exc_info.value)
        assert "no remaining shares" in msg
        # The improved error explicitly points at voided splits.
        assert "voided" in msg.lower()


# ── TestCloseLot ─────────────────────────────────────────────


class TestCloseLot:
    def test_close_lot_success(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="To Close")
        result = book.close_lot(guid=lot["guid"])
        assert result["status"] == "closed"

    def test_close_lot_already_closed(self, investment_book: Path):
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="Already")
        book.close_lot(guid=lot["guid"])
        with pytest.raises(ValueError, match="already closed"):
            book.close_lot(guid=lot["guid"])


# ── TestFullLotWorkflow ──────────────────────────────────────


class TestFullLotWorkflow:
    def test_buy_assign_sell_autoclose(self, investment_book: Path):
        """Full lifecycle: buy → assign → sell → assign → auto-close."""
        book = GnuCashBook(str(investment_book))

        # Create lot
        lot = book.create_lot(
            account="Assets:Investments:VTSAX",
            title="VTSAX 2026-01-15",
            notes="Full lifecycle test",
        )

        # Buy 10 shares at $125
        buy_guid = _buy_shares(book, 10, Decimal("125"), date(2026, 1, 15))
        result = book.assign_split_to_lot(split_guid=buy_guid, lot_guid=lot["guid"])
        assert Decimal(result["quantity"]) == Decimal("10")
        assert Decimal(result["cost_basis"]) == Decimal("1250")
        assert result["is_closed"] is False

        # Check gain at $130
        gain = book.calculate_lot_gain(lot_guid=lot["guid"], sale_price="130")
        assert Decimal(gain["capital_gain"]) == Decimal("50")

        # Sell all 10 shares at $130
        sell_guid = _sell_shares(book, 10, Decimal("130"), date(2026, 2, 1))
        result = book.assign_split_to_lot(split_guid=sell_guid, lot_guid=lot["guid"])
        assert Decimal(result["quantity"]) == Decimal("0")
        assert result["is_closed"] is True

        # Verify lot shows as closed in get_lot
        lot_detail = book.get_lot(guid=lot["guid"])
        assert lot_detail["is_closed"] is True
        assert len(lot_detail["splits"]) == 2

        # Verify excluded from default list
        lots = book.list_lots(account="Assets:Investments:VTSAX", compact=False)
        assert len(lots) == 0

        # Verify included when include_closed=True
        lots = book.list_lots(account="Assets:Investments:VTSAX", include_closed=True, compact=False)
        assert len(lots) == 1
        assert lots[0]["is_closed"] is True


class TestSplitToDictLotGuid:
    """Verify that _split_to_dict includes lot_guid."""

    def test_split_has_lot_guid_none(self, investment_book: Path):
        """Splits without lots should have lot_guid=None."""
        book = GnuCashBook(str(investment_book))
        txn_result = book.create_transaction(
            description="No lot test",
            splits=[
                {"account": "Assets:Checking", "amount": "-100"},
                {"account": "Assets:Investments:VTSAX", "amount": "100", "quantity": "0.8000"},
            ],
            trans_date=date(2026, 1, 20),
        )
        txn = book.get_transaction(txn_result["guid"])
        for s in txn["splits"]:
            assert "lot_guid" in s
            assert s["lot_guid"] is None

    def test_split_has_lot_guid_assigned(self, investment_book: Path):
        """Splits assigned to lots should show the lot GUID."""
        book = GnuCashBook(str(investment_book))
        lot = book.create_lot(account="Assets:Investments:VTSAX", title="GUID test")
        split_guid = _buy_shares(book, 5, Decimal("125"), date(2026, 1, 15))
        book.assign_split_to_lot(split_guid=split_guid, lot_guid=lot["guid"])

        # Re-read the transaction to check _split_to_dict output
        txn_guid = None
        # Get the transaction that contains this split
        lot_detail = book.get_lot(guid=lot["guid"])
        txn_date = lot_detail["splits"][0]["date"]
        txn_desc = lot_detail["splits"][0]["description"]

        # Search for the transaction
        txns = book.search_transactions(query=txn_desc, field="description", compact=False)
        assert len(txns) > 0
        txn = book.get_transaction(txns[0]["guid"])

        for s in txn["splits"]:
            if s["account"] == "Assets:Investments:VTSAX":
                # Split's lot_guid is the full 32-char guid (read-side
                # serializer); the create_lot response has emitted a
                # short collision-safe prefix. The full guid must start
                # with that prefix.
                assert s["lot_guid"].startswith(lot["guid"])
