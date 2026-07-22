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

    def test_bimonthly_electric(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        result = gb.create_scheduled_transaction(
            name="Electric Bill",
            description="Seattle City Light",
            splits=[
                {"account": "Expenses:Rent", "amount": "400.00"},
                {"account": "Assets:Checking", "amount": "-400.00"},
            ],
            start_date="2026-03-10",
            frequency="bimonthly",
        )
        assert result["status"] == "created"
        assert result["frequency"] == "bimonthly"

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

    def test_torn_write_cleans_up_template_account(self, scheduled_book):
        """If the SX-row insert fails after the template account
        has already been flushed, the template account must be
        deleted in cleanup. Pre-fix a "ghost" template account
        with no scheduled-transaction owner persisted forever
        under ``root_template``."""
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))

        # Patch the SX __table__.insert step to raise mid-sequence.
        # The template account has been flushed at that point but
        # the SX row hasn't landed.
        from piecash.core.transaction import ScheduledTransaction

        real_insert = ScheduledTransaction.__table__.insert
        with patch.object(
            ScheduledTransaction.__table__, "insert",
            side_effect=RuntimeError("simulated mid-sequence failure"),
        ):
            with pytest.raises(RuntimeError, match="simulated"):
                gb.create_scheduled_transaction(
                    name="DoomedSX",
                    description="Should not survive",
                    splits=[
                        {"account": "Expenses:Rent", "amount": "100.00"},
                        {"account": "Assets:Checking", "amount": "-100.00"},
                    ],
                    start_date="2026-01-01",
                    frequency="monthly",
                )

        # Template account must NOT be on disk under root_template.
        with gb.open(readonly=True) as book:
            template_names = [
                a.name for a in book.root_template.children
            ]
        assert "DoomedSX" not in template_names, (
            f"Template account survived torn-write: {template_names}"
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
        result = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"]
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
        result = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"]
        assert len(result) == 1
        assert result[0]["name"] == "Rent"
        assert result[0]["frequency"] == "monthly"
        assert result[0]["splits"] == [
            {"account": "Expenses:Rent", "amount": "1850.00", "memo": ""},
            {"account": "Assets:Checking", "amount": "-1850.00", "memo": ""},
        ]
        # description == name → suppressed as noise.
        assert "description" not in result[0]

    def test_verbose_list_shows_distinct_description(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        gb.create_scheduled_transaction(
            name="Monthly Rent",
            description="Rent payment to Lakeview Property Mgmt",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        result = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"]
        assert result[0]["description"] == (
            "Rent payment to Lakeview Property Mgmt"
        )

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
        enabled = gb.list_scheduled_transactions(enabled_only=True, compact=False)["scheduled_transactions"]
        assert len(enabled) == 1
        assert enabled[0]["name"] == "Utils"

        # All
        all_sx = gb.list_scheduled_transactions(enabled_only=False, compact=False)["scheduled_transactions"]
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
        result = gb.get_upcoming_transactions(days=14, compact=False)["upcoming_transactions"]
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
        result = gb.get_upcoming_transactions(days=14, compact=False)["upcoming_transactions"]
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
        result = gb.get_upcoming_transactions(days=14, compact=False)["upcoming_transactions"]
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

        # Verify the transaction actually exists, carrying the
        # template's stored description (not the SX name).
        txn = gb.get_transaction(result["transaction_guid"])
        assert txn is not None
        assert txn["description"] == "Rent"

    def test_falls_back_to_name_without_description_slot(
        self, scheduled_book,
    ):
        """Templates created before the description slot existed
        instantiate with the SX name, as they always did."""
        from sqlalchemy import text

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
        # Simulate a pre-slot template by removing the slot.
        with gb.open(readonly=False) as book:
            book.session.execute(
                text("DELETE FROM slots WHERE name = 'description'")
            )
            book.save()
            sx_guid = book.session.execute(
                text("SELECT guid FROM schedxactions")
            ).first()[0]

        result = gb.create_transaction_from_scheduled(
            guid=sx_guid, transaction_date="2026-02-01",
        )
        txn = gb.get_transaction(result["transaction_guid"])
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

    def test_duplicate_occurrence_rejected(self, scheduled_book):
        """Cannot instantiate the same date twice.

        Guards against double-billing when the bookkeeper thread and
        GnuCash desktop both try to run the same occurrence.
        """
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
        gb.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-02-01",
        )
        with pytest.raises(ValueError, match="not after last occurrence"):
            gb.create_transaction_from_scheduled(
                guid=sx["guid"], transaction_date="2026-02-01",
            )

    def test_backfill_before_last_occur_rejected(self, scheduled_book):
        """Cannot instantiate a date earlier than last_occur.

        Desktop's adv_creation may create occurrences months ahead;
        backfilling a gap that's already been run would duplicate.
        """
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
        gb.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-03-01",
        )
        with pytest.raises(ValueError, match="not after last occurrence"):
            gb.create_transaction_from_scheduled(
                guid=sx["guid"], transaction_date="2026-02-01",
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
        listed = gb.list_scheduled_transactions(enabled_only=False, compact=False)["scheduled_transactions"]
        assert len(listed) == 0

        # No orphaned SX slots (splits-json, description) remain.
        from sqlalchemy import text
        with gb.open(readonly=True) as book:
            count = book.session.execute(
                text(
                    "SELECT COUNT(*) FROM slots "
                    "WHERE name IN ('splits-json', 'description')"
                )
            ).first()[0]
        assert count == 0

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

    def test_last_occur_raises_threshold(self, scheduled_book):
        """last_occur past `after` pushes the search forward.

        Prevents returning an occurrence that desktop (or a prior run)
        has already instantiated.
        """
        gb = GnuCashBook(str(scheduled_book))
        # Without last_occur: after=2026-03-15 → next monthly is 2026-04-01.
        # With last_occur=2026-05-15 (desktop ran ahead): next is 2026-06-01.
        result = gb._next_occurrence(
            start_date=date(2026, 1, 1),
            frequency="monthly",
            after=date(2026, 3, 15),
            last_occur=date(2026, 5, 15),
        )
        assert result == date(2026, 6, 1)

    def test_last_occur_earlier_than_after_ignored(self, scheduled_book):
        """last_occur older than `after` doesn't lower the threshold."""
        gb = GnuCashBook(str(scheduled_book))
        result = gb._next_occurrence(
            start_date=date(2026, 1, 1),
            frequency="monthly",
            after=date(2026, 3, 15),
            last_occur=date(2026, 2, 1),
        )
        assert result == date(2026, 4, 1)

    def test_monthly_31st_does_not_drift_after_february(self, scheduled_book):
        """A monthly schedule starting Jan 31 must hit Mar 31, Apr 30,
        May 31, ... — not drift to the 28th forever after Feb.

        Pre-fix, ``occurrence += relativedelta(months=1)`` chained the
        clamping: Jan 31 → Feb 28 (clamped) → Mar 28 → Apr 28 → ...
        Anchoring to ``start_date + relativedelta(months=n)`` recovers
        the original day-of-month intent.
        """
        gb = GnuCashBook(str(scheduled_book))
        # After Feb 1: should be Feb 28 (clamped, no Feb 31).
        assert gb._next_occurrence(
            start_date=date(2026, 1, 31), frequency="monthly",
            after=date(2026, 2, 1),
        ) == date(2026, 2, 28)
        # After Feb 28: should be MARCH 31, not March 28.
        assert gb._next_occurrence(
            start_date=date(2026, 1, 31), frequency="monthly",
            after=date(2026, 2, 28),
        ) == date(2026, 3, 31)
        # After Mar 31: should be April 30 (April has 30 days).
        assert gb._next_occurrence(
            start_date=date(2026, 1, 31), frequency="monthly",
            after=date(2026, 3, 31),
        ) == date(2026, 4, 30)
        # After Apr 30: should be MAY 31, not May 30.
        assert gb._next_occurrence(
            start_date=date(2026, 1, 31), frequency="monthly",
            after=date(2026, 4, 30),
        ) == date(2026, 5, 31)
        # 12 months later: should be Jan 31 of the next year.
        assert gb._next_occurrence(
            start_date=date(2026, 1, 31), frequency="monthly",
            after=date(2026, 12, 31),
        ) == date(2027, 1, 31)

    def test_yearly_leap_day_does_not_drift(self, scheduled_book):
        """A yearly schedule starting Feb 29 (leap day) must stay on
        Feb 29 in subsequent leap years, even though intervening
        non-leap years clamp to Feb 28."""
        gb = GnuCashBook(str(scheduled_book))
        # 2024 is a leap year; next yearly from Feb 29, 2024 falls on
        # Feb 28 in 2025/2026/2027 (clamped) but recovers to Feb 29
        # in 2028 (next leap year).
        assert gb._next_occurrence(
            start_date=date(2024, 2, 29), frequency="yearly",
            after=date(2027, 6, 1),
        ) == date(2028, 2, 29)


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
        listed = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"]
        assert len(listed) == 1
        assert listed[0]["name"] == "Monthly Rent"
        assert listed[0]["enabled"] is True

        # Create real transaction
        txn = gb.create_transaction_from_scheduled(
            guid=sx["guid"],
            transaction_date="2026-01-01",
        )
        assert txn["status"] == "created"

        # Verify transaction exists with correct details — the
        # stored description, not the SX name.
        real_txn = gb.get_transaction(txn["transaction_guid"])
        assert real_txn["description"] == "Rent payment"
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
        listed = gb.list_scheduled_transactions(enabled_only=False, compact=False)["scheduled_transactions"]
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

        listed = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"]
        assert len(listed) == 5
        freqs = {sx["frequency"] for sx in listed}
        assert freqs == {"weekly", "biweekly", "monthly", "quarterly", "yearly"}


class TestScheduledNotes:
    """Notes ride the template: stored as a slot at create, applied
    to every instantiated transaction, editable three-state via
    update. Templates without the slot behave exactly as before."""

    RENT_SPLITS = [
        {"account": "Expenses:Rent", "amount": "1850.00"},
        {"account": "Assets:Checking", "amount": "-1850.00"},
    ]

    def _create(self, gb, **kwargs):
        return gb.create_scheduled_transaction(
            name="Monthly Rent",
            description="Rent",
            splits=self.RENT_SPLITS,
            start_date="2026-01-01",
            frequency="monthly",
            **kwargs,
        )

    def test_notes_stored_and_applied_at_instantiation(
        self, scheduled_book,
    ):
        gb = GnuCashBook(str(scheduled_book))
        sx = self._create(
            gb, notes="Apartment 4B, includes water surcharge",
        )
        result = gb.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-02-01",
        )
        txn = gb.get_transaction(result["transaction_guid"])
        assert txn["notes"] == "Apartment 4B, includes water surcharge"

    def test_without_notes_instantiates_clean(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = self._create(gb)
        result = gb.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-02-01",
        )
        txn = gb.get_transaction(result["transaction_guid"])
        assert not txn.get("notes")

    def test_list_verbose_shows_notes_only_when_present(
        self, scheduled_book,
    ):
        gb = GnuCashBook(str(scheduled_book))
        self._create(gb, notes="Apartment 4B")
        gb.create_scheduled_transaction(
            name="Electric",
            description="Seattle City Light",
            splits=self.RENT_SPLITS,
            start_date="2026-01-10",
            frequency="monthly",
        )
        listed = {
            sx["name"]: sx
            for sx in gb.list_scheduled_transactions(
                compact=False,
            )["scheduled_transactions"]
        }
        assert listed["Monthly Rent"]["notes"] == "Apartment 4B"
        assert "notes" not in listed["Electric"]

    def test_update_sets_clears_and_leaves_notes(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = self._create(gb)

        # Set on a template that never had notes.
        gb.update_scheduled_transaction(
            guid=sx["guid"], notes="Lease renews each June",
        )
        result = gb.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-02-01",
        )
        txn = gb.get_transaction(result["transaction_guid"])
        assert txn["notes"] == "Lease renews each June"

        # Omitting notes leaves them unchanged.
        gb.update_scheduled_transaction(guid=sx["guid"], enabled=True)
        listed = gb.list_scheduled_transactions(
            compact=False,
        )["scheduled_transactions"]
        assert listed[0]["notes"] == "Lease renews each June"

        # Empty string clears.
        gb.update_scheduled_transaction(guid=sx["guid"], notes="")
        result = gb.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-03-01",
        )
        txn = gb.get_transaction(result["transaction_guid"])
        assert not txn.get("notes")

    def test_replacing_existing_notes(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = self._create(gb, notes="old annotation")
        gb.update_scheduled_transaction(
            guid=sx["guid"], notes="new annotation",
        )
        result = gb.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-02-01",
        )
        txn = gb.get_transaction(result["transaction_guid"])
        assert txn["notes"] == "new annotation"


class TestScheduledCurrency:
    """Templates denominate their instantiations.

    Pre-fix wedge: an all-foreign-leg template (Lin Wei's USD-to-USD
    card payment in a CNY book) CREATED fine — the manual account
    loop never checked quantity rules — then failed at every
    instantiation forever. Creation now runs the shared split
    validator against the template's currency, and a ``currency``
    slot denominates instantiated transactions."""

    def _eur_accounts(self, gc):
        gc.create_account(
            name="EUR Checking", account_type="BANK",
            parent="Assets", commodity="EUR",
        )
        # Fixture already has Assets:Euro Savings (EUR).

    def test_foreign_pair_template_instantiates_in_currency(
        self, multi_currency_book,
    ):
        gc = GnuCashBook(str(multi_currency_book))
        self._eur_accounts(gc)
        sx = gc.create_scheduled_transaction(
            name="EUR Sweep", description="Monthly EUR sweep",
            splits=[
                {"account": "Assets:EUR Checking", "amount": "-25.00"},
                {"account": "Assets:Euro Savings", "amount": "25.00"},
            ],
            start_date="2026-08-01", frequency="monthly",
            currency="EUR",
        )
        r = gc.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-08-01",
        )
        assert r["status"] == "created"
        txn = gc.get_transaction(r["transaction_guid"])
        assert txn["currency"] == "EUR"
        sav = next(
            s for s in txn["splits"]
            if s["account"] == "Assets:Euro Savings"
        )
        assert sav["value"] == "25" and sav["quantity"] == "25"

    def test_all_foreign_template_without_currency_rejects_at_create(
        self, multi_currency_book,
    ):
        gc = GnuCashBook(str(multi_currency_book))
        self._eur_accounts(gc)
        with pytest.raises(ValueError, match="quantity"):
            gc.create_scheduled_transaction(
                name="Broken Sweep", description="x",
                splits=[
                    {"account": "Assets:EUR Checking",
                     "amount": "-25.00"},
                    {"account": "Assets:Euro Savings",
                     "amount": "25.00"},
                ],
                start_date="2026-08-01", frequency="monthly",
            )
        # Nothing half-created: template list is empty.
        assert gc.list_scheduled_transactions(
            enabled_only=False, compact=False,
        )["total"] == 0

    def test_cross_commodity_template_replays_quantity(
        self, multi_currency_book,
    ):
        """Default-frame template with a EUR leg + qty: stored and
        replayed at instantiation (paycheck-with-401k shape)."""
        gc = GnuCashBook(str(multi_currency_book))
        sx = gc.create_scheduled_transaction(
            name="EUR Savings Feed", description="Monthly EUR feed",
            splits=[
                {"account": "Assets:Checking", "amount": "-110.00"},
                {"account": "Assets:Euro Savings", "amount": "110.00",
                 "quantity": "100.00"},
            ],
            start_date="2026-08-01", frequency="monthly",
        )
        r = gc.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-08-01",
        )
        assert r["status"] == "created"
        txn = gc.get_transaction(r["transaction_guid"])
        assert txn["currency"] == "USD"
        eur_leg = next(
            s for s in txn["splits"]
            if s["account"] == "Assets:Euro Savings"
        )
        assert eur_leg["value"] == "110"
        assert eur_leg["quantity"] == "100"

    def test_currency_shown_in_verbose_list(self, multi_currency_book):
        gc = GnuCashBook(str(multi_currency_book))
        self._eur_accounts(gc)
        gc.create_scheduled_transaction(
            name="EUR Sweep", description="x",
            splits=[
                {"account": "Assets:EUR Checking", "amount": "-25.00"},
                {"account": "Assets:Euro Savings", "amount": "25.00"},
            ],
            start_date="2026-08-01", frequency="monthly",
            currency="EUR",
        )
        listed = gc.list_scheduled_transactions(
            enabled_only=False, compact=False,
        )["scheduled_transactions"]
        assert listed[0]["currency"] == "EUR"
        # Stored splits carry no quantity keys (same-currency legs).
        assert all("quantity" not in s for s in listed[0]["splits"])

    def test_upcoming_labels_foreign_template_amounts(
        self, multi_currency_book,
    ):
        """A foreign template's bill-list amount carries its
        currency code — '25.00' from an EUR schedule must not read
        as a book-default amount."""
        from datetime import date as _date, timedelta as _td

        gc = GnuCashBook(str(multi_currency_book))
        self._eur_accounts(gc)
        gc.create_scheduled_transaction(
            name="EUR Sweep", description="x",
            splits=[
                {"account": "Assets:EUR Checking", "amount": "-25.00"},
                {"account": "Assets:Euro Savings", "amount": "25.00"},
            ],
            start_date=(_date.today() + _td(days=2)).isoformat(),
            frequency="monthly", currency="EUR",
        )
        verbose = gc.get_upcoming_transactions(days=7, compact=False)
        entry = verbose["upcoming_transactions"][0]
        assert entry["currency"] == "EUR"
        compact = gc.get_upcoming_transactions(days=7, compact=True)
        assert "25 EUR" in compact or "25.00 EUR" in compact

    def test_summary_window_converts_or_flags_foreign(
        self, multi_currency_book,
    ):
        """Dashboard 7-day total: foreign templates convert at the
        latest market rate; with no rate on file they're counted
        but flagged unrated instead of silently mixed in."""
        from datetime import date as _date, timedelta as _td

        gc = GnuCashBook(str(multi_currency_book))
        self._eur_accounts(gc)
        gc.create_scheduled_transaction(
            name="EUR Sweep", description="x",
            splits=[
                {"account": "Assets:EUR Checking", "amount": "-25.00"},
                {"account": "Assets:Euro Savings", "amount": "25.00"},
            ],
            start_date=(_date.today() + _td(days=2)).isoformat(),
            frequency="monthly", currency="EUR",
        )
        # The fixture's only EUR price is piecash's auto
        # type='transaction' placeholder, which the market-rate
        # chokepoint skips → unrated.
        with gc.open(readonly=True) as book:
            stats = gc._upcoming_within_days(book, days=7)
        assert stats["count"] == 1
        assert stats["unrated"] == 1
        assert stats["total"] == 0

        # A real market rate converts the total.
        gc.create_price(
            commodity="EUR", namespace="CURRENCY", value="1.08",
            price_date=_date.today(),
        )
        with gc.open(readonly=True) as book:
            stats = gc._upcoming_within_days(book, days=7)
        assert stats["unrated"] == 0
        assert stats["total"] == Decimal("25.00") * Decimal("1.08")
