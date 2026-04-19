"""SchedulingMixin — recurring transaction templates.

ScheduledTransaction + Recurrence rows describe the template, and a
`splits-json` Slot holds the split template as JSON (because piecash's
Slot ORM has polymorphic issues with composite primary keys).

create_transaction_from_scheduled calls self.create_transaction
(core, via MRO) to instantiate an actual transaction from the template.

Depends on shared helpers from BaseGnuCashBook:
  - self.open, self._resolve_guid, self._find_account,
    self._require_default_currency
  - _sx_to_compact_line, _upcoming_to_compact_line (module-level)
  - _verify_write, _verify_composite_write, _verify_delete
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import piecash

from gnucash_mcp.book._base import (
    _sx_to_compact_line,
    _upcoming_to_compact_line,
    _verify_composite_write,
    _verify_delete,
    _verify_write,
)


class SchedulingMixin:
    """Scheduled transaction CRUD and instantiation."""

    VALID_FREQUENCIES = {
        "weekly", "biweekly", "monthly", "bimonthly", "quarterly", "yearly",
    }

    FREQUENCY_TO_RECURRENCE = {
        "weekly": ("week", 1),
        "biweekly": ("week", 2),
        "monthly": ("month", 1),
        "bimonthly": ("month", 2),
        "quarterly": ("month", 3),
        "yearly": ("year", 1),
    }

    RECURRENCE_TO_FREQUENCY = {
        ("week", 1): "weekly",
        ("week", 2): "biweekly",
        ("month", 1): "monthly",
        ("month", 2): "bimonthly",
        ("month", 3): "quarterly",
        ("year", 1): "yearly",
    }

    # ── Helpers ───────────────────────────────────────────────────

    def _next_occurrence(
        self,
        start_date: date,
        frequency: str,
        after: date | None = None,
        end_date: date | None = None,
    ) -> date | None:
        """Calculate the next occurrence of a scheduled transaction.

        Args:
            start_date: First occurrence date.
            frequency: One of VALID_FREQUENCIES.
            after: Find next occurrence after this date. Defaults to today.
            end_date: If set, return None if next occurrence past this date.

        Returns:
            Next occurrence date, or None if past end_date.
        """
        from dateutil.relativedelta import relativedelta

        if after is None:
            after = date.today()

        delta_map = {
            "weekly": relativedelta(weeks=1),
            "biweekly": relativedelta(weeks=2),
            "monthly": relativedelta(months=1),
            "bimonthly": relativedelta(months=2),
            "quarterly": relativedelta(months=3),
            "yearly": relativedelta(years=1),
        }
        delta = delta_map[frequency]

        occurrence = start_date
        while occurrence <= after:
            occurrence += delta

        if end_date and occurrence > end_date:
            return None
        return occurrence

    def _sx_to_dict(self, sx, frequency: str | None = None) -> dict:
        """Serialize a ScheduledTransaction to a dict.

        Args:
            sx: piecash ScheduledTransaction object.
            frequency: Pre-computed frequency string. If None, derived
                       from recurrence.
        """
        if frequency is None:
            rec = sx.recurrence
            key = (rec.recurrence_period_type, rec.recurrence_mult)
            frequency = self.RECURRENCE_TO_FREQUENCY.get(key, "unknown")

        start = sx.start_date
        if isinstance(start, datetime):
            start = start.date()

        end = sx.end_date
        if isinstance(end, datetime):
            end = end.date()

        last = sx.last_occur
        if isinstance(last, datetime):
            last = last.date()

        next_occ = self._next_occurrence(
            start, frequency, after=date.today() - timedelta(days=1),
            end_date=end,
        ) if frequency != "unknown" else None

        return {
            "guid": sx.guid,
            "name": sx.name,
            "enabled": bool(sx.enabled),
            "frequency": frequency,
            "start_date": start.isoformat(),
            "end_date": end.isoformat() if end else None,
            "last_occurrence": last.isoformat() if last else None,
            "next_occurrence": (
                next_occ.isoformat() if next_occ else None
            ),
            "instance_count": sx.instance_count,
            "auto_create": bool(sx.auto_create),
        }

    def _get_sx_splits(self, book, sx) -> list[dict]:
        """Read split templates from the scheduled transaction's slot.

        The split templates are stored as JSON in a slot named
        'splits-json' on the ScheduledTransaction.
        """
        import json

        from sqlalchemy import text

        row = book.session.execute(
            text(
                "SELECT string_val FROM slots "
                "WHERE obj_guid = :guid AND name = :name"
            ),
            {"guid": sx.guid, "name": "splits-json"},
        ).first()
        if row:
            return json.loads(row[0])
        return []

    def _find_scheduled_transaction(self, book, guid: str):
        """Find a scheduled transaction by GUID (supports partial GUIDs, 8+ chars)."""
        from piecash.core.transaction import ScheduledTransaction

        try:
            full_guid = self._resolve_guid("schedxactions", guid)
        except ValueError as e:
            if "No schedxaction" in str(e):
                return None
            raise
        return book.session.query(ScheduledTransaction).filter_by(guid=full_guid).first()

    # ── CRUD + instantiation ──────────────────────────────────────

    def create_scheduled_transaction(
        self,
        name: str,
        description: str,
        splits: list[dict],
        start_date: str,
        frequency: str,
        end_date: str | None = None,
        enabled: bool = True,
    ) -> dict:
        """Create a recurring transaction template.

        Args:
            name: Name for the scheduled transaction.
            description: Transaction description when created.
            splits: List of splits, same format as create_transaction:
                [{"account": "Expenses:Rent", "amount": "1850.00"}, ...]
            start_date: First occurrence date (YYYY-MM-DD).
            frequency: How often: "weekly", "biweekly", "monthly",
                      "quarterly", "yearly".
            end_date: Optional last occurrence date (YYYY-MM-DD).
            enabled: Whether active. Default True.

        Returns:
            Dict with guid, name, next_occurrence, and status.

        Raises:
            ValueError: If invalid frequency, accounts not found,
                       or splits don't balance.
        """
        import json
        import uuid

        from piecash._common import Recurrence
        from piecash.core.transaction import ScheduledTransaction
        from piecash.kvp import KVP_Type, Slot

        if frequency not in self.VALID_FREQUENCIES:
            raise ValueError(
                f"Invalid frequency: {frequency}. "
                f"Valid: {', '.join(sorted(self.VALID_FREQUENCIES))}"
            )

        parsed_start = date.fromisoformat(start_date)
        parsed_end = (
            date.fromisoformat(end_date) if end_date else None
        )

        total = Decimal("0")
        for s in splits:
            total += Decimal(s["amount"])
        if total != 0:
            raise ValueError(
                f"Splits must balance to zero (total: {total})"
            )

        rec_period_type, rec_mult = self.FREQUENCY_TO_RECURRENCE[
            frequency
        ]

        with self.open(readonly=False) as book:
            for s in splits:
                acct = self._find_account(book, s["account"])
                if not acct:
                    raise ValueError(
                        f"Account not found: {s['account']}"
                    )

            for sx in book.session.query(
                ScheduledTransaction
            ).all():
                if sx.name == name:
                    raise ValueError(
                        f"Scheduled transaction already exists: "
                        f"{name}"
                    )

            sx_guid = uuid.uuid4().hex

            # Create template account under root_template
            template_acct = piecash.Account(
                name=name,
                type="BANK",
                parent=book.root_template,
                commodity=self._require_default_currency(book),
            )
            book.session.add(template_acct)
            book.session.flush()

            # Insert ScheduledTransaction (blocked constructor)
            book.session.execute(
                ScheduledTransaction.__table__.insert().values(
                    guid=sx_guid,
                    name=name,
                    enabled=1 if enabled else 0,
                    start_date=parsed_start,
                    end_date=parsed_end,
                    last_occur=None,
                    num_occur=0,
                    rem_occur=0,
                    auto_create=0,
                    auto_notify=0,
                    adv_creation=0,
                    adv_notify=0,
                    instance_count=0,
                    template_act_guid=template_acct.guid,
                )
            )
            _verify_write(
                book.session, ScheduledTransaction.__table__, sx_guid,
                f"ScheduledTransaction '{name}'",
            )

            book.session.execute(
                Recurrence.__table__.insert().values(
                    obj_guid=sx_guid,
                    recurrence_mult=rec_mult,
                    recurrence_period_type=rec_period_type,
                    recurrence_period_start=parsed_start,
                    recurrence_weekend_adjust="none",
                )
            )
            _verify_composite_write(
                book.session, Recurrence.__table__,
                {"obj_guid": sx_guid},
                f"Recurrence for scheduled transaction '{name}'",
            )

            # Store split templates as JSON in a slot
            splits_json = json.dumps([
                {
                    "account": s["account"],
                    "amount": s["amount"],
                    "memo": s.get("memo", ""),
                }
                for s in splits
            ])
            book.session.execute(
                Slot.__table__.insert().values(
                    obj_guid=sx_guid,
                    name="splits-json",
                    slot_type=KVP_Type.KVP_TYPE_STRING,
                    string_val=splits_json,
                )
            )
            _verify_composite_write(
                book.session, Slot.__table__,
                {"obj_guid": sx_guid, "name": "splits-json"},
                f"Splits slot for scheduled transaction '{name}'",
            )

            book.save()

            next_occ = self._next_occurrence(
                parsed_start, frequency,
                after=date.today() - timedelta(days=1),
                end_date=parsed_end,
            )

            return {
                "guid": sx_guid,
                "name": name,
                "frequency": frequency,
                "next_occurrence": (
                    next_occ.isoformat() if next_occ else None
                ),
                "status": "created",
            }

    def list_scheduled_transactions(
        self,
        enabled_only: bool = True,
        compact: bool = True,
    ) -> list[dict] | str:
        """List all scheduled transactions.

        Args:
            enabled_only: If True, only show enabled schedules. Default True.
            compact: If True (default), return a compact newline-separated
                     string with one line per scheduled transaction.

        Returns:
            If compact: newline-separated string of scheduled transaction lines.
            If not compact: list of scheduled transaction dicts.
        """
        from piecash.core.transaction import ScheduledTransaction

        with self.open(readonly=True) as book:
            all_sx = book.session.query(
                ScheduledTransaction
            ).all()

            results = []
            for sx in all_sx:
                if enabled_only and not sx.enabled:
                    continue
                d = self._sx_to_dict(sx)
                if not compact:
                    d["splits"] = self._get_sx_splits(book, sx)
                results.append(d)

            if compact:
                lines = [_sx_to_compact_line(d) for d in results]
                return "\n".join(lines)
            else:
                return results

    def get_upcoming_transactions(
        self,
        days: int = 14,
        compact: bool = True,
    ) -> list[dict] | str:
        """Get scheduled transactions due within a time window.

        Args:
            days: Look ahead window in days. Default 14.
            compact: If True, return compact one-line format.
        """
        from piecash.core.transaction import ScheduledTransaction

        today = date.today()
        window_end = today + timedelta(days=days)

        with self.open(readonly=True) as book:
            all_sx = book.session.query(
                ScheduledTransaction
            ).all()

            upcoming = []
            for sx in all_sx:
                if not sx.enabled:
                    continue

                rec = sx.recurrence
                key = (
                    rec.recurrence_period_type,
                    rec.recurrence_mult,
                )
                frequency = self.RECURRENCE_TO_FREQUENCY.get(
                    key, None
                )
                if not frequency:
                    continue

                start = sx.start_date
                if isinstance(start, datetime):
                    start = start.date()

                end = sx.end_date
                if isinstance(end, datetime):
                    end = end.date()

                next_occ = self._next_occurrence(
                    start, frequency,
                    after=today - timedelta(days=1),
                    end_date=end,
                )

                if next_occ and next_occ <= window_end:
                    splits = self._get_sx_splits(book, sx)

                    # Calculate total amount (sum of positive splits)
                    total = Decimal("0")
                    for s in splits:
                        amt = Decimal(s["amount"])
                        if amt > 0:
                            total += amt

                    entry = {
                        "guid": sx.guid,
                        "name": sx.name,
                        "occurrence_date": next_occ.isoformat(),
                        "days_until": (next_occ - today).days,
                        "amount": str(total),
                    }
                    if not compact:
                        entry["splits"] = splits
                    upcoming.append(entry)

            upcoming.sort(key=lambda x: x["occurrence_date"])

            if compact:
                lines = [_upcoming_to_compact_line(e) for e in upcoming]
                return "\n".join(lines)
            else:
                return upcoming

    def create_transaction_from_scheduled(
        self,
        guid: str,
        transaction_date: str | None = None,
    ) -> dict:
        """Create an actual transaction from a scheduled template.

        Note: calls self.create_transaction (core, resolved via MRO)
        in a separate session after the schedule update commits.

        Args:
            guid: Scheduled transaction GUID.
            transaction_date: Date for the transaction (YYYY-MM-DD).
                            Defaults to next occurrence date.

        Returns:
            Dict with created transaction guid and updated schedule.

        Raises:
            ValueError: If scheduled transaction not found or disabled.
        """
        with self.open(readonly=False) as book:
            sx = self._find_scheduled_transaction(book, guid)
            if not sx:
                raise ValueError(
                    f"Scheduled transaction not found: {guid}"
                )
            if not sx.enabled:
                raise ValueError(
                    "Scheduled transaction is disabled"
                )

            rec = sx.recurrence
            key = (
                rec.recurrence_period_type,
                rec.recurrence_mult,
            )
            frequency = self.RECURRENCE_TO_FREQUENCY.get(key)
            if not frequency:
                raise ValueError("Unknown recurrence frequency")

            start = sx.start_date
            if isinstance(start, datetime):
                start = start.date()

            end = sx.end_date
            if isinstance(end, datetime):
                end = end.date()

            if transaction_date:
                txn_date = date.fromisoformat(transaction_date)
            else:
                txn_date = self._next_occurrence(
                    start, frequency,
                    after=date.today() - timedelta(days=1),
                    end_date=end,
                )
                if not txn_date:
                    raise ValueError(
                        "No upcoming occurrence (past end date)"
                    )

            splits = self._get_sx_splits(book, sx)
            if not splits:
                raise ValueError(
                    "No split templates found for scheduled "
                    "transaction"
                )

            sx.last_occur = txn_date
            sx.instance_count += 1

            sx_name = sx.name
            instance_count = sx.instance_count

            book.save()

        # Create the actual transaction (separate session)
        txn_result = self.create_transaction(
            description=sx_name,
            splits=splits,
            trans_date=txn_date,
        )

        return {
            "transaction_guid": txn_result["guid"],
            "scheduled_transaction": sx_name,
            "transaction_date": txn_date.isoformat(),
            "instance_count": instance_count,
            "status": "created",
        }

    def update_scheduled_transaction(
        self,
        guid: str,
        enabled: bool | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Update a scheduled transaction.

        Args:
            guid: Scheduled transaction GUID.
            enabled: Enable or disable.
            end_date: Set end date (YYYY-MM-DD), or empty string to clear.

        Returns:
            Dict with updated scheduled transaction details.

        Raises:
            ValueError: If not found.
        """
        with self.open(readonly=False) as book:
            sx = self._find_scheduled_transaction(book, guid)
            if not sx:
                raise ValueError(
                    f"Scheduled transaction not found: {guid}"
                )

            if enabled is not None:
                sx.enabled = 1 if enabled else 0

            if end_date is not None:
                if end_date == "":
                    sx.end_date = None
                else:
                    sx.end_date = date.fromisoformat(end_date)

            book.save()

            return self._sx_to_dict(sx)

    def delete_scheduled_transaction(self, guid: str) -> dict:
        """Delete a scheduled transaction.

        Does not affect transactions already created from this schedule.
        """
        from sqlalchemy import text

        with self.open(readonly=False) as book:
            sx = self._find_scheduled_transaction(book, guid)
            if not sx:
                raise ValueError(
                    f"Scheduled transaction not found: {guid}"
                )

            result = {
                "name": sx.name,
                "guid": sx.guid,
                "status": "deleted",
            }

            # Delete the splits-json slot via raw SQL
            book.session.execute(
                text(
                    "DELETE FROM slots "
                    "WHERE obj_guid = :guid AND name = :name"
                ),
                {"guid": sx.guid, "name": "splits-json"},
            )
            _verify_delete(
                book.session,
                {"obj_guid": sx.guid, "name": "splits-json"},
                f"Splits slot for scheduled transaction '{result['name']}'",
            )

            template_acct = sx.template_account
            book.session.delete(sx)
            if template_acct:
                book.session.delete(template_acct)

            book.save()

            return result
