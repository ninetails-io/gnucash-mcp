"""Tests for scheduled transaction tools."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from gnucash_mcp.book import GnuCashBook


# ── Create ──────────────────────────────────────────────────


class TestCreateScheduled:
    """Tests for create_scheduled_transaction."""

    def test_monthly_rent(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        result = gb.create_scheduled_transaction(
            name="Monthly Rent",
            description="Rent payment",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        assert result["status"] == "created"
        assert result["name"] == "Monthly Rent"
        assert result["frequency"] == "monthly"
        assert result["guid"]

    def test_biweekly_paycheck(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        result = gb.create_scheduled_transaction(
            name="Paycheck",
            description="Bi-weekly salary",
            splits=[
                {"account": "Assets:Checking", "amount": "2500.00"},
                {"account": "Income:Salary", "amount": "-2500.00"},
            ],
            start_date="2026-01-09",
            frequency="biweekly",
        )
        assert result["status"] == "created"
        assert result["frequency"] == "biweekly"

    def test_with_end_date(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        result = gb.create_scheduled_transaction(
            name="Lease Payment",
            description="Office lease",
            splits=[
                {"account": "Expenses:Rent", "amount": "500.00"},
                {"account": "Assets:Checking", "amount": "-500.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
            end_date="2026-12-31",
        )
        assert result["status"] == "created"

    def test_duplicate_name_error(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        gb.create_scheduled_transaction(
            name="Monthly Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        with pytest.raises(ValueError, match="already exists"):
            gb.create_scheduled_transaction(
                name="Monthly Rent",
                description="Rent again",
                splits=[
                    {"account": "Expenses:Rent", "amount": "1850.00"},
                    {"account": "Assets:Checking", "amount": "-1850.00"},
                ],
                start_date="2026-02-01",
                frequency="monthly",
            )

    def test_invalid_frequency_error(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        with pytest.raises(ValueError, match="Invalid frequency"):
            gb.create_scheduled_transaction(
                name="Bad Schedule",
                description="Nope",
                splits=[
                    {"account": "Expenses:Rent", "amount": "100.00"},
                    {"account": "Assets:Checking", "amount": "-100.00"},
                ],
                start_date="2026-01-01",
                frequency="daily",
            )

    def test_unbalanced_splits_error(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        with pytest.raises(ValueError, match="balance to zero"):
            gb.create_scheduled_transaction(
                name="Unbalanced",
                description="Nope",
                splits=[
                    {"account": "Expenses:Rent", "amount": "100.00"},
                    {"account": "Assets:Checking", "amount": "-50.00"},
                ],
                start_date="2026-01-01",
                frequency="monthly",
            )

    def test_invalid_account_error(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        with pytest.raises(ValueError, match="Account not found"):
            gb.create_scheduled_transaction(
                name="Bad Account",
                description="Nope",
                splits=[
                    {"account": "Expenses:Nonexistent", "amount": "100.00"},
                    {"account": "Assets:Checking", "amount": "-100.00"},
                ],
                start_date="2026-01-01",
                frequency="monthly",
            )


# ── List ────────────────────────────────────────────────────


class TestListScheduled:
    """Tests for list_scheduled_transactions."""

    def test_empty_list(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        result = gb.list_scheduled_transactions(compact=False)
        assert result == []

    def test_lists_created(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        gb.create_scheduled_transaction(
            name="Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        result = gb.list_scheduled_transactions(compact=False)
        assert len(result) == 1
        assert result[0]["name"] == "Rent"
        assert result[0]["frequency"] == "monthly"
        assert result[0]["splits"] == [
            {"account": "Expenses:Rent", "amount": "1850.00", "memo": ""},
            {"account": "Assets:Checking", "amount": "-1850.00", "memo": ""},
        ]

    def test_enabled_only_filter(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        r1 = gb.create_scheduled_transaction(
            name="Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        gb.create_scheduled_transaction(
            name="Utils",
            description="Utils",
            splits=[
                {"account": "Expenses:Utilities", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        # Disable one
        gb.update_scheduled_transaction(r1["guid"], enabled=False)

        # Default: enabled_only=True
        enabled = gb.list_scheduled_transactions(enabled_only=True, compact=False)
        assert len(enabled) == 1
        assert enabled[0]["name"] == "Utils"

        # All
        all_sx = gb.list_scheduled_transactions(enabled_only=False, compact=False)
        assert len(all_sx) == 2


# ── Get Upcoming ────────────────────────────────────────────


class TestGetUpcoming:
    """Tests for get_upcoming_transactions."""

    def test_within_window(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        # Start date = tomorrow, so next occurrence is tomorrow
        tomorrow = date.today() + timedelta(days=1)
        gb.create_scheduled_transaction(
            name="Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date=tomorrow.isoformat(),
            frequency="monthly",
        )
        result = gb.get_upcoming_transactions(days=14, compact=False)
        assert len(result) == 1
        assert result[0]["name"] == "Rent"
        assert result[0]["amount"] == "1850.00"
        assert result[0]["days_until"] >= 0

    def test_outside_window(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        # Start date far in the future
        future = date.today() + timedelta(days=60)
        gb.create_scheduled_transaction(
            name="Future Rent",
            description="Far out",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date=future.isoformat(),
            frequency="monthly",
        )
        result = gb.get_upcoming_transactions(days=14, compact=False)
        assert len(result) == 0

    def test_disabled_excluded(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        tomorrow = date.today() + timedelta(days=1)
        r = gb.create_scheduled_transaction(
            name="Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date=tomorrow.isoformat(),
            frequency="monthly",
        )
        gb.update_scheduled_transaction(r["guid"], enabled=False)
        result = gb.get_upcoming_transactions(days=14, compact=False)
        assert len(result) == 0


# ── Create From Scheduled ──────────────────────────────────


class TestCreateFromScheduled:
    """Tests for create_transaction_from_scheduled."""

    def test_creates_real_transaction(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = gb.create_scheduled_transaction(
            name="Monthly Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        result = gb.create_transaction_from_scheduled(
            guid=sx["guid"],
            transaction_date="2026-02-01",
        )
        assert result["status"] == "created"
        assert result["transaction_guid"]
        assert result["transaction_date"] == "2026-02-01"
        assert result["instance_count"] == 1

        # Verify the transaction actually exists
        txn = gb.get_transaction(result["transaction_guid"])
        assert txn is not None
        assert txn["description"] == "Monthly Rent"

    def test_updates_tracking(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = gb.create_scheduled_transaction(
            name="Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        # Create twice
        gb.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-01-01",
        )
        r2 = gb.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-02-01",
        )
        assert r2["instance_count"] == 2

    def test_disabled_error(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = gb.create_scheduled_transaction(
            name="Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        gb.update_scheduled_transaction(sx["guid"], enabled=False)
        with pytest.raises(ValueError, match="disabled"):
            gb.create_transaction_from_scheduled(
                guid=sx["guid"],
                transaction_date="2026-02-01",
            )

    def test_not_found_error(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        with pytest.raises(ValueError, match="not found"):
            gb.create_transaction_from_scheduled(
                guid="a" * 32,
                transaction_date="2026-02-01",
            )


# ── Update ──────────────────────────────────────────────────


class TestUpdateScheduled:
    """Tests for update_scheduled_transaction."""

    def test_disable(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = gb.create_scheduled_transaction(
            name="Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        result = gb.update_scheduled_transaction(
            sx["guid"], enabled=False,
        )
        assert result["enabled"] is False

    def test_set_end_date(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = gb.create_scheduled_transaction(
            name="Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        result = gb.update_scheduled_transaction(
            sx["guid"], end_date="2026-12-31",
        )
        assert result["end_date"] == "2026-12-31"

    def test_not_found_error(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        with pytest.raises(ValueError, match="not found"):
            gb.update_scheduled_transaction(
                "b" * 32, enabled=False,
            )


# ── Delete ──────────────────────────────────────────────────


class TestDeleteScheduled:
    """Tests for delete_scheduled_transaction."""

    def test_delete_existing(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = gb.create_scheduled_transaction(
            name="Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        result = gb.delete_scheduled_transaction(sx["guid"])
        assert result["status"] == "deleted"
        assert result["name"] == "Rent"

        # Verify gone
        listed = gb.list_scheduled_transactions(enabled_only=False, compact=False)
        assert len(listed) == 0

    def test_delete_nonexistent_error(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        with pytest.raises(ValueError, match="not found"):
            gb.delete_scheduled_transaction("c" * 32)


# ── Next Occurrence Helper ──────────────────────────────────


class TestNextOccurrence:
    """Tests for _next_occurrence helper."""

    def test_monthly_from_past(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        result = gb._next_occurrence(
            start_date=date(2026, 1, 1),
            frequency="monthly",
            after=date(2026, 3, 15),
        )
        assert result == date(2026, 4, 1)

    def test_weekly(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        result = gb._next_occurrence(
            start_date=date(2026, 1, 5),  # Monday
            frequency="weekly",
            after=date(2026, 1, 5),  # same day
        )
        assert result == date(2026, 1, 12)

    def test_respects_end_date(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        result = gb._next_occurrence(
            start_date=date(2026, 1, 1),
            frequency="monthly",
            after=date(2026, 11, 15),
            end_date=date(2026, 12, 1),
        )
        assert result == date(2026, 12, 1)

    def test_past_end_date_returns_none(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        result = gb._next_occurrence(
            start_date=date(2026, 1, 1),
            frequency="monthly",
            after=date(2026, 12, 15),
            end_date=date(2026, 12, 31),
        )
        assert result is None

    def test_biweekly(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        result = gb._next_occurrence(
            start_date=date(2026, 1, 9),
            frequency="biweekly",
            after=date(2026, 1, 9),
        )
        assert result == date(2026, 1, 23)

    def test_yearly(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        result = gb._next_occurrence(
            start_date=date(2025, 1, 1),
            frequency="yearly",
            after=date(2026, 6, 1),
        )
        assert result == date(2027, 1, 1)


# ── Integration ─────────────────────────────────────────────


class TestScheduledIntegration:
    """Full workflow tests."""

    def test_full_lifecycle(self, scheduled_book):
        """Create → list → create_from → verify → delete."""
        gb = GnuCashBook(str(scheduled_book))

        # Create
        sx = gb.create_scheduled_transaction(
            name="Monthly Rent",
            description="Rent payment",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )

        # List
        listed = gb.list_scheduled_transactions(compact=False)
        assert len(listed) == 1
        assert listed[0]["name"] == "Monthly Rent"
        assert listed[0]["enabled"] is True

        # Create real transaction
        txn = gb.create_transaction_from_scheduled(
            guid=sx["guid"],
            transaction_date="2026-01-01",
        )
        assert txn["status"] == "created"

        # Verify transaction exists with correct details
        real_txn = gb.get_transaction(txn["transaction_guid"])
        assert real_txn["description"] == "Monthly Rent"
        assert len(real_txn["splits"]) == 2

        # Check amounts in splits
        amounts = {s["account"]: s["value"] for s in real_txn["splits"]}
        assert Decimal(amounts["Expenses:Rent"]) == Decimal("1850")
        assert Decimal(amounts["Assets:Checking"]) == Decimal("-1850")

        # Verify balance changed
        balance = gb.get_balance("Assets:Checking")
        assert balance == Decimal("8150")  # 10000 - 1850

        # Delete
        gb.delete_scheduled_transaction(sx["guid"])
        listed = gb.list_scheduled_transactions(enabled_only=False, compact=False)
        assert len(listed) == 0

    def test_multiple_frequencies(self, scheduled_book):
        """Create scheduled transactions with different frequencies."""
        gb = GnuCashBook(str(scheduled_book))

        for freq in ["weekly", "biweekly", "monthly", "quarterly", "yearly"]:
            gb.create_scheduled_transaction(
                name=f"Test {freq}",
                description=f"Test {freq}",
                splits=[
                    {"account": "Expenses:Utilities", "amount": "100.00"},
                    {"account": "Assets:Checking", "amount": "-100.00"},
                ],
                start_date="2026-01-01",
                frequency=freq,
            )

        listed = gb.list_scheduled_transactions(compact=False)
        assert len(listed) == 5
        freqs = {sx["frequency"] for sx in listed}
        assert freqs == {"weekly", "biweekly", "monthly", "quarterly", "yearly"}
