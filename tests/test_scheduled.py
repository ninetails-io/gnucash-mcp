"""Tests for scheduled transaction tools."""

from datetime import date, timedelta
from decimal import Decimal
from dateutil.relativedelta import relativedelta
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

    def test_placeholder_leg_refused_at_create(self, scheduled_book):
        """A template leg on a placeholder account passes the split
        contract and then fails at every instantiation forever —
        creation must refuse it, naming the placeholder, exactly as
        create_transactions phase 1 does."""
        gb = GnuCashBook(str(scheduled_book))
        with pytest.raises(ValueError, match="placeholder"):
            gb.create_scheduled_transaction(
                name="Bad template",
                description="Rent",
                splits=[
                    {"account": "Expenses", "amount": "5.00"},
                    {"account": "Assets:Checking", "amount": "-5.00"},
                ],
                start_date="2026-01-01",
                frequency="monthly",
            )
        assert gb.list_scheduled_transactions(compact=False)[
            "scheduled_transactions"
        ] == []

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
        """A legacy recipe with no description slot instantiates
        under the SX name — what instantiation always used to do."""
        gb = GnuCashBook(str(scheduled_book))
        sx = gb.create_scheduled_transaction(
            name="Rent",
            description="Monthly Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        _make_legacy(scheduled_book, "Rent", description=False)
        result = gb.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-01-01",
        )
        assert result["description"] == "Rent"

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


class TestScheduledSplitAction:
    def test_template_action_replays_at_instantiation(
        self, multi_currency_book,
    ):
        """A DCA-style template stamps Buy on every instantiation."""
        gc = GnuCashBook(str(multi_currency_book))
        sx = gc.create_scheduled_transaction(
            name="EUR DCA", description="Monthly EUR feed",
            splits=[
                {"account": "Assets:Checking", "amount": "-110.00"},
                {"account": "Assets:Euro Savings", "amount": "110.00",
                 "quantity": "100.00", "action": "Buy"},
            ],
            start_date="2026-08-01", frequency="monthly",
        )
        r = gc.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-08-01",
        )
        txn = gc.get_transaction(r["transaction_guid"])
        eur = next(
            s for s in txn["splits"]
            if s["account"] == "Assets:Euro Savings"
        )
        assert eur["action"] == "Buy"


# ── Legacy-shape helper ─────────────────────────────────────────


def _make_legacy(book_path, sx_name, *, refs="guid", description=True,
                 notes=None, currency=None):
    """Rewrite a native schedule as the pre-native on-disk shape,
    byte-faithfully: template rows gone, recipe in a ``splits-json``
    slot on the SX row (account refs as GUIDs or paths), optional
    description / notes / currency slots. The state outlives the
    door that made it — real books carry it until their first
    write — so the guards that read it need tests that can still
    construct it."""
    import json
    from sqlalchemy import text
    gb = GnuCashBook(str(book_path))
    with gb.open(readonly=False) as book:
        sx = next(s for s in book.session.query(
            __import__("piecash").core.transaction.ScheduledTransaction
        ).all() if s.name == sx_name)
        recipe = gb._sx_recipe(book, sx)
        assert recipe["source"] == "native"
        legs = []
        for s in recipe["splits"]:
            leg = dict(s)
            if refs == "path":
                acct = book.session.query(
                    __import__("piecash").Account
                ).filter_by(guid=s["account"]).first()
                leg["account"] = acct.fullname
            legs.append(leg)
        desc = recipe["description"]
        tmpl = sx.template_account
        txns = gb._strip_template_recipe(book, tmpl, "legacy-ize")
        for t in txns:
            book.session.delete(t)
        book.session.flush()
        # Pre-native containers: named by the schedule, on the book
        # currency (what create used to make).
        book.session.execute(
            text("UPDATE accounts SET name = :n, commodity_guid = "
                 "(SELECT guid FROM commodities WHERE mnemonic = 'USD' "
                 "AND namespace = 'CURRENCY') WHERE guid = :g"),
            {"n": sx_name, "g": tmpl.guid},
        )
        rows = [("splits-json", json.dumps(legs))]
        if description and desc:
            rows.append(("description", desc))
        if notes:
            rows.append(("notes", notes))
        if currency:
            rows.append(("currency", currency))
        for name, val in rows:
            book.session.execute(
                text("INSERT INTO slots (obj_guid, name, slot_type, string_val) "
                     "VALUES (:g, :n, 4, :v)"),
                {"g": sx.guid, "n": name, "v": val},
            )
        book.save()


# ── Occurrence agreement (the _sx_next_due chokepoint) ─────────


def _rent(gb, start, name="Rent", **kw):
    return gb.create_scheduled_transaction(
        name=name,
        description=name,
        splits=[
            {"account": "Expenses:Rent", "amount": "1850.00"},
            {"account": "Assets:Checking", "amount": "-1850.00"},
        ],
        start_date=start.isoformat(),
        frequency="monthly",
        **kw,
    )


class TestOccurrenceAgreement:
    """Every surface that answers "which occurrence is next" reads
    _sx_next_due: the dashboard overdue warning, list and upcoming
    next_occurrence, the Scheduled summary line, and the default
    instantiation date. Pre-fix, four of the five searched from
    today and skipped missed periods; the fifth (dashboard) did
    not, so the dashboard flagged July while instantiation posted
    October and then refused July forever."""

    def test_missed_periods_agree_across_surfaces(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        start = date.today() - timedelta(days=70)  # three missed
        sx = _rent(gb, start)

        with gb.open(readonly=True) as book:
            overdue = gb._overdue_scheduled_warnings(book, date.today())
        assert len(overdue) == 1
        assert overdue[0]["msg"].endswith(f"due {start.isoformat()}")

        listed = gb.list_scheduled_transactions(compact=False)
        assert listed["scheduled_transactions"][0]["next_occurrence"] == start.isoformat()

        up = gb.get_upcoming_transactions(days=14, compact=False)
        assert up["upcoming_transactions"][0]["occurrence_date"] == start.isoformat()
        assert up["upcoming_transactions"][0]["days_until"] == -70

        created = gb.create_transaction_from_scheduled(guid=sx["guid"])
        assert created["transaction_date"] == start.isoformat()

    def test_default_date_walks_missed_periods_forward(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        start = date.today() - timedelta(days=70)
        sx = _rent(gb, start)
        expected = [
            start,
            start + relativedelta(months=1),
            start + relativedelta(months=2),
        ]
        for i, exp in enumerate(expected, start=1):
            r = gb.create_transaction_from_scheduled(guid=sx["guid"])
            assert r["transaction_date"] == exp.isoformat()
            assert r["instance_count"] == i
        # Everything missed is entered; the next default is ahead.
        r = gb.create_transaction_from_scheduled(guid=sx["guid"])
        assert date.fromisoformat(r["transaction_date"]) > date.today()

    def test_compact_lines_mark_overdue(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        start = date.today() - timedelta(days=10)
        _rent(gb, start)
        assert f"overdue:{start.isoformat()}" in gb.list_scheduled_transactions()
        assert "10 days overdue" in gb.get_upcoming_transactions(days=14)

    def test_summary_week_excludes_overdue(self, scheduled_book):
        """An overdue schedule is in the overdue bucket, not in "due
        in next 7 days" — the Scheduled line would double-count."""
        gb = GnuCashBook(str(scheduled_book))
        _rent(gb, date.today() - timedelta(days=10))
        _rent(gb, date.today() + timedelta(days=3), name="Soon")
        with gb.open(readonly=True) as book:
            week = gb._upcoming_within_days(book, days=7)
            overdue = gb._overdue_scheduled_warnings(book, date.today())
        assert week["count"] == 1
        assert [e["name"] for e in overdue] == ["Rent"]

    def test_today_is_due_not_overdue(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        _rent(gb, date.today())
        with gb.open(readonly=True) as book:
            assert gb._overdue_scheduled_warnings(book, date.today()) == []
            assert gb._upcoming_within_days(book, days=7)["count"] == 1
        assert "0 days" in gb.get_upcoming_transactions(days=7)


class TestFiniteSchedules:
    """num_occur / rem_occur are GnuCash's occurrence limit; a
    schedule at zero remaining has no next instance (its
    xaccSchedXactionGetNextInstance rule) and instantiation counts
    it down like desktop creation does."""

    def _make_finite(self, gb, n):
        from sqlalchemy import text
        sx = _rent(gb, date.today() - timedelta(days=100))
        with gb.open(readonly=False) as book:
            book.session.execute(
                text(
                    "UPDATE schedxactions SET num_occur=:n, "
                    "rem_occur=:n WHERE name='Rent'"
                ),
                {"n": n},
            )
            book.save()
        return sx

    def test_counts_down_and_stops(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = self._make_finite(gb, 2)
        r1 = gb.create_transaction_from_scheduled(guid=sx["guid"])
        assert r1["remaining_occurrences"] == 1
        r2 = gb.create_transaction_from_scheduled(guid=sx["guid"])
        assert r2["remaining_occurrences"] == 0
        with pytest.raises(ValueError, match="entered all 2 occurrences"):
            gb.create_transaction_from_scheduled(guid=sx["guid"])
        listed = gb.list_scheduled_transactions(compact=False)
        row = listed["scheduled_transactions"][0]
        assert row["next_occurrence"] is None
        assert row["remaining_occurrences"] == 0
        assert gb.get_upcoming_transactions(days=14, compact=False)["total"] == 0

    def test_unlimited_schedule_reports_no_remaining(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date.today())
        r = gb.create_transaction_from_scheduled(guid=sx["guid"])
        assert "remaining_occurrences" not in r
        row = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]
        assert "remaining_occurrences" not in row


class TestSplitRefStorage:
    """Template splits store the account GUID, as GnuCash's own
    template splits do. Pre-fix they stored the caller's path, and
    the first rename broke instantiation with "Account not found"
    on the due date."""

    def test_survives_account_rename(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date.today())
        gb.update_account("Expenses:Rent", new_name="Housing Rent")
        r = gb.create_transaction_from_scheduled(guid=sx["guid"])
        assert r["status"] == "created"
        txn = gb.get_transaction(r["transaction_guid"])
        assert {s["account"] for s in txn["splits"]} == {
            "Expenses:Housing Rent", "Assets:Checking",
        }

    def test_readers_render_paths_not_guids(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        _rent(gb, date.today())
        from sqlalchemy import text
        with gb.open(readonly=True) as book:
            assert book.session.execute(
                text("SELECT COUNT(*) FROM slots WHERE name='splits-json'")
            ).scalar() == 0
            guids = [r[0] for r in book.session.execute(text(
                "SELECT guid_val FROM slots WHERE name='sched-xaction/account'"
            )).fetchall()]
            rent_guid = next(
                a.guid for a in book.accounts if a.fullname == "Expenses:Rent"
            )
        assert rent_guid in guids  # stored as GUID
        listed = gb.list_scheduled_transactions(compact=False)
        paths = {s["account"] for s in listed["scheduled_transactions"][0]["splits"]}
        assert paths == {"Expenses:Rent", "Assets:Checking"}
        up = gb.get_upcoming_transactions(days=7, compact=False)
        paths = {s["account"] for s in up["upcoming_transactions"][0]["splits"]}
        assert paths == {"Expenses:Rent", "Assets:Checking"}

    def test_path_stored_legacy_rows_still_instantiate(self, scheduled_book):
        """Templates written before GUID storage hold paths; they
        keep working while the path lives."""
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date.today())
        _make_legacy(scheduled_book, "Rent", refs="path")
        r = gb.create_transaction_from_scheduled(guid=sx["guid"])
        assert r["status"] == "created"
        listed = gb.list_scheduled_transactions(compact=False)
        assert listed["scheduled_transactions"][0]["splits"][0]["account"] == "Expenses:Rent"


class TestTornWriteLate:
    def test_late_failure_persists_nothing(self, scheduled_book):
        """A failure AFTER the SX / recurrence / splits rows landed
        (here: the description-slot insert) must leave no SX row
        and no template account. Pre-fix the cleanup was
        delete-template-then-save, which commits the partial rows;
        it only looked clean because piecash's
        Account.scheduled_transaction cascade swept them out."""
        from sqlalchemy import text
        from piecash.kvp import Slot
        gb = GnuCashBook(str(scheduled_book))
        real_insert = Slot.__table__.insert
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated late failure")
            return real_insert(*a, **k)

        with patch.object(Slot.__table__, "insert", side_effect=flaky):
            with pytest.raises(RuntimeError, match="late"):
                _rent(gb, date.today(), name="TornLate")

        with gb.open(readonly=True) as book:
            rows = book.session.execute(
                text("SELECT COUNT(*) FROM schedxactions WHERE name='TornLate'")
            ).scalar()
            names = [a.name for a in book.root_template.children]
        assert rows == 0
        assert "TornLate" not in names


class TestLoopFollowUps:
    """Bookkeeper loop on fix/scheduled-occurrence-chokepoint,
    2026-09-08: the four items the plan's seven steps surfaced."""

    def test_create_response_reads_the_chokepoint(self, scheduled_book):
        """The first number a caller sees after creating a schedule.
        Pre-fix it searched from today: a schedule starting 70 days
        ago answered start+3 months while the list, one call later,
        said overdue:<start>. The production workaround was passing
        explicit dates to every instantiation."""
        gb = GnuCashBook(str(scheduled_book))
        start = date.today() - timedelta(days=70)
        created = _rent(gb, start)
        assert created["next_occurrence"] == start.isoformat()
        listed = gb.list_scheduled_transactions(compact=False)
        assert listed["scheduled_transactions"][0]["next_occurrence"] == created["next_occurrence"]

    def test_upcoming_amount_at_commodity_quantum(self, scheduled_book):
        """Stored amounts carry the caller's precision; the bill
        list renders at the currency's quantum regardless (Lin Wei
        showed 15000 beside Alex's 4200.00)."""
        gb = GnuCashBook(str(scheduled_book))
        gb.create_scheduled_transaction(
            name="Salary", description="Salary",
            splits=[
                {"account": "Assets:Checking", "amount": "15000"},
                {"account": "Income:Salary", "amount": "-15000"},
            ],
            start_date=date.today().isoformat(), frequency="monthly",
        )
        up = gb.get_upcoming_transactions(days=7, compact=False)
        assert up["upcoming_transactions"][0]["amount"] == "15000.00"

    def test_ended_schedule_refusal_names_end_and_next_move(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        start = date.today() - timedelta(days=40)
        sx = _rent(gb, start)
        gb.create_transaction_from_scheduled(guid=sx["guid"])
        # End ON the entered date: nothing after it is due, so the
        # refusal fires (ending yesterday would leave start+1 month
        # still due — and the server would rightly post it).
        gb.update_scheduled_transaction(sx["guid"], end_date=start.isoformat())
        with pytest.raises(ValueError) as exc:
            gb.create_transaction_from_scheduled(guid=sx["guid"])
        msg = str(exc.value)
        assert f"'Rent' ended {start.isoformat()}" in msg
        assert f"last entered {start.isoformat()}" in msg
        assert 'end_date=""' in msg
        assert "delete_scheduled_transaction" in msg

    def test_finished_finite_refusal_names_the_count(self, scheduled_book):
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date.today() - timedelta(days=40))
        with gb.open(readonly=False) as book:
            book.session.execute(
                text("UPDATE schedxactions SET num_occur=1, rem_occur=1 WHERE name='Rent'")
            )
            book.save()
        gb.create_transaction_from_scheduled(guid=sx["guid"])
        with pytest.raises(ValueError, match="entered all 1 occurrences"):
            gb.create_transaction_from_scheduled(guid=sx["guid"])


# ── Native template transactions (GnuCash's own recipe format) ──


def _slots_for(book, obj_guid):
    from sqlalchemy import text
    rows = book.session.execute(
        text("SELECT name, slot_type, string_val, guid_val, numeric_val_num, "
             "numeric_val_denom FROM slots WHERE obj_guid = :g"),
        {"g": obj_guid},
    ).fetchall()
    return {r[0]: tuple(r[1:]) for r in rows}


def _template_rows(book, sx_name):
    """(template account row, [split guids], template txn guids)."""
    from sqlalchemy import text
    sx = book.session.execute(
        text("SELECT guid, template_act_guid FROM schedxactions WHERE name = :n"),
        {"n": sx_name},
    ).first()
    acct = book.session.execute(
        text("SELECT name, account_type, commodity_guid FROM accounts WHERE guid = :g"),
        {"g": sx[1]},
    ).first()
    splits = [r[0] for r in book.session.execute(
        text("SELECT guid FROM splits WHERE account_guid = :a"), {"a": sx[1]},
    ).fetchall()]
    txns = [r[0] for r in book.session.execute(
        text("SELECT DISTINCT tx_guid FROM splits WHERE account_guid = :a"),
        {"a": sx[1]},
    ).fetchall()]
    return sx, acct, splits, txns


class TestNativeTemplates:
    """The recipe is stored the way GnuCash's SX editor stores it —
    Split.cpp's sched-xaction slots on zero-value splits of a template
    transaction — so Since-Last-Run sees what this server wrote and
    this server reads what desktop wrote. Pre-fix the recipe lived in
    a private splits-json slot: desktop advanced our schedules with
    nothing posted, and desktop-made schedules had no recipe here."""

    def test_round_trip_writes_the_split_cpp_shape(self, scheduled_book):
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))
        gb.create_scheduled_transaction(
            name="Rent", description="Monthly Rent", notes="lease 12",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00", "memo": "unit 4"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01", frequency="monthly",
        )
        with gb.open(readonly=True) as book:
            sx, acct, splits, txns = _template_rows(book, "Rent")
            # Template account as xaccSchedXactionInit makes it.
            assert acct[0] == sx[0] and acct[1] == "BANK"
            com = book.session.execute(
                text("SELECT namespace, mnemonic, fullname, cusip, fraction "
                     "FROM commodities WHERE guid = :g"), {"g": acct[2]},
            ).first()
            assert tuple(com) == ("template", "template", "template", "template", 1)
            # One template transaction carrying description + notes.
            assert len(txns) == 1
            desc, cur = book.session.execute(
                text("SELECT t.description, c.mnemonic FROM transactions t "
                     "JOIN commodities c ON c.guid = t.currency_guid WHERE t.guid = :g"),
                {"g": txns[0]},
            ).first()
            assert (desc, cur) == ("Monthly Rent", "USD")
            assert _slots_for(book, txns[0])["notes"][1] == "lease 12"
            # Each split: frame + five children, both sides written.
            rent_guid = next(a.guid for a in book.accounts if a.fullname == "Expenses:Rent")
            seen = {}
            for sg in splits:
                frame = _slots_for(book, sg)["sched-xaction"]
                assert frame[0] == 9
                ch = _slots_for(book, frame[2])
                assert set(ch) == {
                    "sched-xaction/account", "sched-xaction/credit-formula",
                    "sched-xaction/debit-formula", "sched-xaction/credit-numeric",
                    "sched-xaction/debit-numeric",
                }
                seen[ch["sched-xaction/account"][2]] = ch
            rent = seen[rent_guid]
            assert rent["sched-xaction/account"][0] == 5
            assert rent["sched-xaction/debit-formula"][1] == "1850.00"
            assert rent["sched-xaction/debit-numeric"][3:] == (185000, 100)
            assert rent["sched-xaction/credit-formula"][1] == ""
            assert rent["sched-xaction/credit-numeric"][3:] == (0, 100)
            # Nothing legacy on the SX row.
            assert _slots_for(book, sx[0]) == {}

    def test_desktop_shaped_rows_formula_only_instantiate(self, scheduled_book):
        """Older desktop templates carry formulas without numerics;
        a plain-number formula is parsed."""
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date.today())
        with gb.open(readonly=False) as book:
            book.session.execute(text(
                "DELETE FROM slots WHERE name IN "
                "('sched-xaction/credit-numeric', 'sched-xaction/debit-numeric')"
            ))
            book.save()
        r = gb.create_transaction_from_scheduled(guid=sx["guid"])
        assert r["status"] == "created"
        txn = gb.get_transaction(r["transaction_guid"])
        amounts = {s["account"]: Decimal(s["value"]) for s in txn["splits"]}
        assert amounts["Expenses:Rent"] == Decimal("1850.00")

    def test_formula_with_variables_is_refused_and_listed(self, scheduled_book):
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date.today())
        with gb.open(readonly=False) as book:
            book.session.execute(text(
                "UPDATE slots SET string_val = 'rent*2' "
                "WHERE name = 'sched-xaction/debit-formula' AND string_val <> ''"
            ))
            book.session.execute(text(
                "UPDATE slots SET numeric_val_num = 0 "
                "WHERE name = 'sched-xaction/debit-numeric'"
            ))
            book.save()
        row = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]
        assert any("rent*2" in p for p in row["problems"])
        with pytest.raises(ValueError, match="rent\\*2"):
            gb.create_transaction_from_scheduled(guid=sx["guid"])

    def test_legacy_recipe_migrates_on_update(self, scheduled_book):
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))
        _rent(gb, date.today())
        _make_legacy(scheduled_book, "Rent", notes="old notes", currency="USD")
        listed = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]
        assert listed["recipe"] == "legacy"
        guid = listed["guid"]
        r = gb.update_scheduled_transaction(guid, enabled=True)
        assert r.get("template_migrated") is True
        with gb.open(readonly=True) as book:
            sx, acct, splits, txns = _template_rows(book, "Rent")
            assert len(txns) == 1 and len(splits) == 2
            assert _slots_for(book, sx[0]) == {}  # four legacy slots gone
            assert _slots_for(book, txns[0])["notes"][1] == "old notes"
        r2 = gb.update_scheduled_transaction(guid, enabled=True)
        assert "template_migrated" not in r2
        assert gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]["recipe"] == "native"

    def test_legacy_recipe_migrates_on_instantiate(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date.today())
        _make_legacy(scheduled_book, "Rent", refs="path")
        r = gb.create_transaction_from_scheduled(guid=sx["guid"])
        assert r["status"] == "created" and r.get("template_migrated") is True
        with gb.open(readonly=True) as book:
            sx_row, _, splits, txns = _template_rows(book, "Rent")
            assert len(txns) == 1 and len(splits) == 2
            assert _slots_for(book, sx_row[0]) == {}

    def test_reads_never_migrate(self, scheduled_book):
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))
        _rent(gb, date.today())
        _make_legacy(scheduled_book, "Rent")
        gb.list_scheduled_transactions(compact=False)
        gb.get_upcoming_transactions(days=7, compact=False)
        gb.get_book_summary()
        with gb.open(readonly=True) as book:
            assert book.session.execute(
                text("SELECT COUNT(*) FROM slots WHERE name = 'splits-json'")
            ).scalar() == 1

    def test_update_notes_lands_on_template_transaction(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date.today())
        gb.update_scheduled_transaction(sx["guid"], notes="new")
        with gb.open(readonly=True) as book:
            sx_row, _, _, txns = _template_rows(book, "Rent")
            assert _slots_for(book, txns[0])["notes"][1] == "new"
            assert "notes" not in _slots_for(book, sx_row[0])
        assert gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]["notes"] == "new"

    def test_instance_is_stamped_and_its_delete_spares_the_schedule(self, scheduled_book):
        """Desktop stamps instances from-sched-xaction; so do we.
        Deleting the instance must not cascade through that GUID slot
        into the schedule (piecash's SlotGUID delete-orphan trap)."""
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date.today())
        r = gb.create_transaction_from_scheduled(guid=sx["guid"])
        with gb.open(readonly=True) as book:
            created = gb._find_transaction(book, r["transaction_guid"])
            stamp = _slots_for(book, created.guid)["from-sched-xaction"]
            sx_guid = _template_rows(book, "Rent")[0][0]
        assert stamp[0] == 5 and stamp[2] == sx_guid
        gb.delete_transaction(r["transaction_guid"])
        # Schedule intact: recipe still native, still instantiable.
        row = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]
        assert row["recipe"] == "native" and len(row["splits"]) == 2

    def test_delete_schedule_spares_target_account_slots(self, scheduled_book):
        """The landmine: sched-xaction/account is a GUID slot; an ORM
        delete of the template split would sweep every slot of the
        TARGET account. Strip first, then delete."""
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date.today())
        gb.set_account_slot("Expenses:Rent", "apr", "0")
        gb.set_account_slot("Assets:Checking", "statement_close_day", "15")
        gb.delete_scheduled_transaction(sx["guid"])
        assert gb.get_account_slots("Expenses:Rent")["slots"]["apr"] == "0"
        assert gb.get_account_slots("Assets:Checking")["slots"]["statement_close_day"] == "15"
        with gb.open(readonly=True) as book:
            from sqlalchemy import text
            assert book.session.execute(
                text("SELECT COUNT(*) FROM slots WHERE name LIKE 'sched-xaction%'")
            ).scalar() == 0
            assert book.session.execute(text("SELECT COUNT(*) FROM schedxactions")).scalar() == 0

    def test_template_commodity_once_and_filtered(self, scheduled_book):
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))
        _rent(gb, date.today())
        _rent(gb, date.today(), name="Rent 2")
        with gb.open(readonly=True) as book:
            assert book.session.execute(
                text("SELECT COUNT(*) FROM commodities WHERE namespace = 'template'")
            ).scalar() == 1
        assert "template" not in gb.list_commodities()

    def test_template_rows_do_not_trip_duplicate_detection(self, scheduled_book):
        """Template rows are real transactions on a hidden account; a
        real entry with the same description and amount on the
        schedule's start date must not be flagged against them."""
        gb = GnuCashBook(str(scheduled_book))
        today = date.today()
        gb.create_scheduled_transaction(
            name="Rent", description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date=today.isoformat(), frequency="monthly",
        )
        r = gb.create_transaction(
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            trans_date=today,
        )
        assert r["status"] == "created"


class TestNativeTemplatesFX:
    def _eur_schedule(self, gb, with_quantity=True):
        splits = [
            {"account": "Assets:Euro Savings", "amount": "-110.00",
             **({"quantity": "-100.00"} if with_quantity else {})},
            {"account": "Expenses:Groceries", "amount": "110.00"},
        ]
        return gb.create_scheduled_transaction(
            name="EU Groceries", description="EU Groceries", splits=splits,
            start_date=date.today().isoformat(), frequency="monthly",
        )

    def test_quantity_replays_from_namespaced_slot(self, multi_currency_book):
        from sqlalchemy import text
        gb = GnuCashBook(str(multi_currency_book))
        sx = self._eur_schedule(gb)
        with gb.open(readonly=True) as book:
            q = book.session.execute(text(
                "SELECT numeric_val_num, numeric_val_denom FROM slots "
                "WHERE name = 'gnc-mcp/quantity'"
            )).first()
        assert tuple(q) == (-10000, 100)
        r = gb.create_transaction_from_scheduled(guid=sx["guid"])
        txn = gb.get_transaction(r["transaction_guid"])
        eur = next(s for s in txn["splits"] if s["account"] == "Assets:Euro Savings")
        assert Decimal(eur["quantity"]) == Decimal("-100.00")

    def test_desktop_fx_leg_uses_rate_on_file_or_refuses(self, multi_currency_book):
        """A desktop-made cross-commodity leg has no fixed quantity;
        GnuCash asks for the rate. We answer from book.prices at the
        instance date, or refuse naming the leg."""
        from sqlalchemy import text
        gb = GnuCashBook(str(multi_currency_book))
        # Create requires the quantity (shared split contract); the
        # desktop shape is engineered by dropping our namespaced slot.
        sx = self._eur_schedule(gb)
        with gb.open(readonly=False) as book:
            book.session.execute(text("DELETE FROM prices"))
            book.session.execute(text(
                "DELETE FROM slots WHERE name IN ('gnc-mcp', 'gnc-mcp/quantity')"
            ))
            book.save()
        with pytest.raises(ValueError, match="Euro Savings.*EUR/USD rate"):
            gb.create_transaction_from_scheduled(guid=sx["guid"])
        gb.create_price(commodity="EUR", namespace="CURRENCY", value="1.10", currency="USD",
                        price_date=date.today())
        r = gb.create_transaction_from_scheduled(guid=sx["guid"])
        txn = gb.get_transaction(r["transaction_guid"])
        eur = next(s for s in txn["splits"] if s["account"] == "Assets:Euro Savings")
        assert Decimal(eur["quantity"]) == Decimal("-100.00")



# ── Recurrence engine (Recurrence.cpp, ported) ─────────────────


class TestRecurrenceEngine:
    """recurrenceNextInstance, line for line. Desktop anchors an
    occurrence on the recurrence row — period start, multiplier,
    period type, weekend adjustment — and a schedule may carry
    several rows. Pre-fix the server used start_date + its own
    frequency label: a desktop schedule "start 9 Sep, monthly on
    the 15th" posted on the 9th and wrote last_occur=9th, so
    desktop's next run posted the 15th again (bookkeeper, 2026-09-10)."""

    def _n(self, pt, mult, start, ref, wadj="none"):
        from gnucash_mcp.book.scheduling import _recurrence_next
        return _recurrence_next(pt, mult, start, wadj, ref)

    def test_ref_before_start_is_the_start(self):
        assert self._n("month", 1, date(2026, 9, 15), date(2026, 9, 8)) == date(2026, 9, 15)

    def test_monthly_anchor_day(self):
        assert self._n("month", 1, date(2026, 9, 15), date(2026, 9, 15)) == date(2026, 10, 15)
        assert self._n("month", 1, date(2026, 9, 15), date(2026, 9, 20)) == date(2026, 10, 15)

    def test_monthly_31st_clamps_and_recovers(self):
        assert self._n("month", 1, date(2026, 1, 31), date(2026, 1, 31)) == date(2026, 2, 28)
        assert self._n("month", 1, date(2026, 1, 31), date(2026, 2, 28)) == date(2026, 3, 31)

    def test_quarterly_from_anchor(self):
        assert self._n("month", 3, date(2025, 4, 15), date(2026, 4, 15)) == date(2026, 7, 15)

    def test_semiannual_is_just_a_multiplier(self):
        assert self._n("month", 6, date(2026, 1, 10), date(2026, 3, 1)) == date(2026, 7, 10)

    def test_yearly_leap_day(self):
        assert self._n("year", 1, date(2024, 2, 29), date(2024, 2, 29)) == date(2025, 2, 28)
        assert self._n("year", 1, date(2024, 2, 29), date(2027, 2, 28)) == date(2028, 2, 29)

    def test_biweekly_and_daily(self):
        assert self._n("week", 2, date(2025, 1, 10), date(2026, 7, 10)) == date(2026, 7, 24)
        assert self._n("day", 1, date(2026, 9, 1), date(2026, 9, 1)) == date(2026, 9, 2)
        assert self._n("day", 10, date(2026, 9, 1), date(2026, 9, 15)) == date(2026, 9, 21)

    def test_end_of_month(self):
        assert self._n("end of month", 1, date(2026, 1, 31), date(2026, 2, 28)) == date(2026, 3, 31)
        assert self._n("end of month", 1, date(2026, 1, 31), date(2026, 3, 15)) == date(2026, 3, 31)

    def test_nth_weekday(self):
        # 2nd Tuesday: Sep 8 2026 → Oct 13 2026.
        assert self._n("nth weekday", 1, date(2026, 9, 8), date(2026, 9, 8)) == date(2026, 10, 13)

    def test_last_weekday(self):
        # last Tuesday: Sep 29 2026 → Oct 27 2026.
        assert self._n("last weekday", 1, date(2026, 9, 29), date(2026, 9, 29)) == date(2026, 10, 27)

    def test_weekend_forward(self):
        # Aug 15 2026 is a Saturday → adjusted start Mon Aug 17.
        assert self._n("month", 1, date(2026, 8, 15), date(2026, 7, 20), "forward") == date(2026, 8, 17)
        assert self._n("month", 1, date(2026, 8, 15), date(2026, 8, 17), "forward") == date(2026, 9, 15)

    def test_weekend_back(self):
        assert self._n("month", 1, date(2026, 8, 15), date(2026, 7, 20), "back") == date(2026, 8, 14)
        # Nov 15 2026 is a Sunday → Fri Nov 13.
        assert self._n("month", 1, date(2026, 8, 15), date(2026, 10, 15), "back") == date(2026, 11, 13)

    def test_once(self):
        assert self._n("once", 1, date(2026, 9, 15), date(2026, 9, 1)) == date(2026, 9, 15)
        assert self._n("once", 1, date(2026, 9, 15), date(2026, 9, 15)) is None


class TestScheduleReadsRecurrenceRows:
    def _second_row(self, book_path, sx_name, period_start_yyyymmdd, pt="month", mult=1):
        from sqlalchemy import text
        gb = GnuCashBook(str(book_path))
        with gb.open(readonly=False) as book:
            book.session.execute(
                text("INSERT INTO recurrences (obj_guid, recurrence_mult, "
                     "recurrence_period_type, recurrence_period_start, "
                     "recurrence_weekend_adjust) VALUES ((SELECT guid FROM "
                     "schedxactions WHERE name = :n), :m, :t, :s, 'none')"),
                {"n": sx_name, "m": mult, "t": pt, "s": period_start_yyyymmdd},
            )
            book.save()

    def test_desktop_anchor_day_wins_over_start_date(self, scheduled_book):
        """Desktop: start 2026-09-09, monthly on the 15th."""
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date(2026, 9, 9))
        with gb.open(readonly=False) as book:
            book.session.execute(text(
                "UPDATE recurrences SET recurrence_period_start = '20260915'"
            ))
            book.save()
        row = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]
        assert row["next_occurrence"] == "2026-09-15"
        r = gb.create_transaction_from_scheduled(guid=sx["guid"])
        assert r["transaction_date"] == "2026-09-15"

    def test_composite_schedule_walks_both_rows(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date(2026, 9, 20))
        self._second_row(scheduled_book, "Rent", "20260905")
        row = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]
        assert row["frequency"] == "composite (2 rules)"
        assert row["next_occurrence"] == "2026-09-20"
        gb.create_transaction_from_scheduled(guid=sx["guid"])
        row = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]
        assert row["next_occurrence"] == "2026-10-05"
        gb.create_transaction_from_scheduled(guid=sx["guid"])
        row = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]
        assert row["next_occurrence"] == "2026-10-20"

    def test_unfamiliar_single_rows_are_labeled_and_scheduled(self, scheduled_book):
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))
        _rent(gb, date(2026, 1, 31))
        with gb.open(readonly=False) as book:
            book.session.execute(text(
                "UPDATE recurrences SET recurrence_period_type = 'end of month', "
                "recurrence_weekend_adjust = 'forward'"
            ))
            book.save()
        row = gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]
        assert row["frequency"] == "end of month, weekends forward"
        assert row["next_occurrence"] == "2026-02-02"  # Jan 31 2026 is a Saturday

    def test_delete_removes_every_recurrence_row(self, scheduled_book):
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date(2026, 9, 20))
        self._second_row(scheduled_book, "Rent", "20260905")
        gb.delete_scheduled_transaction(sx["guid"])
        with gb.open(readonly=True) as book:
            assert book.session.execute(text("SELECT COUNT(*) FROM recurrences")).scalar() == 0


class TestMigrationRebuildsContainer:
    def test_migrated_schedule_matches_a_fresh_one(self, scheduled_book):
        """The legacy template account (book currency, named by the
        schedule) crashed GnuCash's SX editor once a template
        transaction sat on it. Migration builds the container create
        builds and drops the old account."""
        from sqlalchemy import text
        gb = GnuCashBook(str(scheduled_book))
        sx = _rent(gb, date.today())
        _make_legacy(scheduled_book, "Rent")
        r = gb.update_scheduled_transaction(sx["guid"], enabled=True)
        assert r.get("template_migrated") is True
        with gb.open(readonly=True) as book:
            sx_row, acct, splits, txns = _template_rows(book, "Rent")
            assert acct[0] == sx_row[0] and acct[1] == "BANK"
            ns, frac = book.session.execute(
                text("SELECT namespace, fraction FROM commodities WHERE guid = :g"),
                {"g": acct[2]},
            ).first()
            assert (ns, frac) == ("template", 1)
            denoms = book.session.execute(
                text("SELECT DISTINCT quantity_denom FROM splits WHERE account_guid = :a"),
                {"a": sx_row[1]},
            ).fetchall()
            assert denoms == [(1,)]
            # Exactly one template account under the template root.
            assert len(book.root_template.children) == 1
            assert book.session.execute(text("SELECT COUNT(*) FROM schedxactions")).scalar() == 1
        assert gb.list_scheduled_transactions(compact=False)["scheduled_transactions"][0]["recipe"] == "native"


class TestLegacyCountOnDashboard:
    def test_scheduled_line_counts_legacy_recipes(self, scheduled_book):
        gb = GnuCashBook(str(scheduled_book))
        _rent(gb, date.today() + timedelta(days=3))
        _rent(gb, date.today() + timedelta(days=4), name="Legacy One")
        _make_legacy(scheduled_book, "Legacy One")
        line = next(l for l in gb.get_book_summary().splitlines() if l.startswith("Scheduled:"))
        assert "1 on legacy recipe (migrates on first write)" in line
