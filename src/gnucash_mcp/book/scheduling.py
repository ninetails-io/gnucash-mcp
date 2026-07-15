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
from dateutil.relativedelta import relativedelta
from piecash._common import Recurrence
from piecash.core.transaction import ScheduledTransaction
from piecash.kvp import KVP_Type, Slot
from sqlalchemy import text

from gnucash_mcp.book._base import (
    _guid_prefix_map,
    _sx_to_compact_line,
    _to_decimal,
    _unique_prefix,
    _upcoming_to_compact_line,
    _verify_composite_write,
    _verify_delete,
    _verify_write,
)
from gnucash_mcp._format import _paginate


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
        last_occur: date | None = None,
    ) -> date | None:
        """Calculate the next occurrence of a scheduled transaction.

        Args:
            start_date: First occurrence date.
            frequency: One of VALID_FREQUENCIES.
            after: Find next occurrence after this date. Defaults to today.
            end_date: If set, return None if next occurrence past this date.
            last_occur: Last instantiation date. If set and greater than
                        `after`, the search threshold is raised to
                        `last_occur` so already-instantiated occurrences
                        aren't returned (e.g., when GnuCash desktop has
                        run the schedule ahead).

        Returns:
            Next occurrence date, or None if past end_date.
        """
        if after is None:
            after = date.today()

        if last_occur is not None and last_occur > after:
            after = last_occur

        # Anchor each occurrence to ``start_date + (n × period)``,
        # never chained ``occurrence += delta``: relativedelta clamps
        # on month-end overflow, so a Jan-31 monthly chain drifts
        # Jan 31 → Feb 28 → Mar 28 → … and never recovers. Anchored:
        # Feb 28 → Mar 31 → Apr 30, preserving "31st, falling back
        # to month-end".
        delta_for = {
            "weekly": lambda n: relativedelta(weeks=n),
            "biweekly": lambda n: relativedelta(weeks=2 * n),
            "monthly": lambda n: relativedelta(months=n),
            "bimonthly": lambda n: relativedelta(months=2 * n),
            "quarterly": lambda n: relativedelta(months=3 * n),
            "yearly": lambda n: relativedelta(years=n),
        }[frequency]

        n = 0
        while True:
            occurrence = start_date + delta_for(n)
            if occurrence > after:
                break
            n += 1

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
            end_date=end, last_occur=last,
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

    def _get_sx_slot_string(
        self, book, obj_guid: str, name: str,
    ) -> str | None:
        """Read a string slot off a ScheduledTransaction via raw SQL.

        Raw SQL because the Slot ORM has polymorphic-relationship
        conflicts on reads (see the piecash gotchas in CLAUDE.md).
        """
        row = book.session.execute(
            text(
                "SELECT string_val FROM slots "
                "WHERE obj_guid = :guid AND name = :name"
            ),
            {"guid": obj_guid, "name": name},
        ).first()
        return row[0] if row else None

    def _get_sx_splits(self, book, sx) -> list[dict]:
        """Read split templates from the scheduled transaction's slot.

        The split templates are stored as JSON in a slot named
        'splits-json' on the ScheduledTransaction.
        """
        import json

        raw = self._get_sx_slot_string(book, sx.guid, "splits-json")
        if raw:
            return json.loads(raw)
        return []

    def _get_sx_description(self, book, sx) -> str:
        """Instantiation description for a scheduled transaction.

        MCP-created templates store it in a ``description`` slot
        (bare key — universal financial concept). Templates from
        before the slot existed have no row and fall back to the
        SX name, which is what instantiation always used to use.
        """
        return (
            self._get_sx_slot_string(book, sx.guid, "description")
            or sx.name
        )

    def _find_scheduled_transaction(self, book, guid: str):
        """Find a scheduled transaction by GUID (supports partial GUIDs, 8+ chars)."""

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


        if frequency not in self.VALID_FREQUENCIES:
            raise ValueError(
                f"Invalid frequency: {frequency}. "
                f"Valid: {', '.join(sorted(self.VALID_FREQUENCIES))}"
            )

        parsed_start = date.fromisoformat(start_date)
        parsed_end = (
            date.fromisoformat(end_date) if end_date else None
        )

        # _to_decimal rescues stray floats from direct callers so
        # the balance check doesn't fail on IEEE-754 noise.
        total = Decimal("0")
        for s in splits:
            total += _to_decimal(s["amount"])
        if total != 0:
            raise ValueError(
                f"Splits must balance to zero (total: {total})"
            )

        rec_period_type, rec_mult = self.FREQUENCY_TO_RECURRENCE[
            frequency
        ]

        with self.open(readonly=False) as book:
            for s in splits:
                acct = self._resolve_account(book, s["account"])
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

            # Template account flushed first (the SX row references
            # its GUID). If a later insert fails, the account is
            # already on disk — the try/except below cleans up the
            # orphan, or a ghost template sits under root_template
            # forever.
            template_acct = piecash.Account(
                name=name,
                type="BANK",
                parent=book.root_template,
                commodity=self._require_default_currency(book),
            )
            # No session.add — piecash Accounts auto-register via
            # the parent relationship. The flush is needed: the raw
            # SQL INSERT below requires the template row on disk.
            book.session.flush()

            try:
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

                # amount normalized via _to_decimal → str so the
                # persisted JSON is a clean decimal string — a float
                # surviving json.dumps would replay its IEEE-754
                # epsilon on every future instantiation.
                splits_json = json.dumps([
                    {
                        "account": s["account"],
                        "amount": str(_to_decimal(s["amount"])),
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

                # Instantiation description — read back by
                # _get_sx_description, which falls back to the SX
                # name when the slot is absent (pre-slot templates).
                if description:
                    book.session.execute(
                        Slot.__table__.insert().values(
                            obj_guid=sx_guid,
                            name="description",
                            slot_type=KVP_Type.KVP_TYPE_STRING,
                            string_val=description,
                        )
                    )
                    _verify_composite_write(
                        book.session, Slot.__table__,
                        {"obj_guid": sx_guid, "name": "description"},
                        f"Description slot for scheduled "
                        f"transaction '{name}'",
                    )

                book.save()
            except Exception:
                # Clean up the orphan template; swallow cleanup
                # failures — the original error is what matters.
                try:
                    book.session.delete(template_acct)
                    book.save()
                except Exception:
                    pass
                raise

            next_occ = self._next_occurrence(
                parsed_start, frequency,
                after=date.today() - timedelta(days=1),
                end_date=parsed_end,
            )


            all_sx_guids = [
                row[0]
                for row in book.session.query(ScheduledTransaction.guid).all()
            ]
            short_guid = _unique_prefix(sx_guid, all_sx_guids)
            return {
                "guid": short_guid,
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
        limit: int = 50,
        offset: int = 0,
    ) -> dict | str:
        """List all scheduled transactions.

        Leads with a ``Showing X-Y of Z scheduled transactions``
        indicator; page with ``offset``.

        Args:
            enabled_only: If True, only show enabled schedules. Default True.
            compact: If True (default), return the indicator + a compact
                     newline-separated string with one line per schedule.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.

        Returns:
            If compact: indicator + newline-separated lines.
            If not compact: envelope ``{showing, total, offset, count,
            scheduled_transactions}``.
        """

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
                    # Only when a slot exists — echoing the name
                    # back as "description" would just be noise.
                    desc = self._get_sx_slot_string(
                        book, sx.guid, "description",
                    )
                    if desc and desc != sx.name:
                        d["description"] = desc
                    d["splits"] = self._get_sx_splits(book, sx)
                results.append(d)

            page, indicator = _paginate(
                results, offset=offset, limit=limit,
                entity_name="scheduled transactions",
            )
            if compact:
                # Prefix uniqueness across all scheduled transactions
                prefixes = _guid_prefix_map(sx.guid for sx in all_sx)
                lines = [indicator]
                lines += [
                    _sx_to_compact_line(d, prefixes=prefixes) for d in page
                ]
                return "\n".join(lines)
            else:
                return {
                    "showing": indicator,
                    "total": len(results),
                    "offset": offset,
                    "count": len(page),
                    "scheduled_transactions": page,
                }

    def _upcoming_within_days(
        self, book, days: int = 7,
    ) -> dict:
        """Summary stats for scheduled transactions due within
        ``days`` days: ``{"count": int, "total": Decimal}``.

        Total = sum of positive split amounts per occurrence (same
        convention as ``get_upcoming_transactions``). Feeds the
        get_book_summary Scheduled line; lives here so a book class
        built without scheduling lacks the method and the summary
        skips the line via ``hasattr``.
        """

        today = date.today()
        window_end = today + timedelta(days=days)

        count = 0
        total = Decimal("0")
        for sx in book.session.query(ScheduledTransaction).all():
            if not sx.enabled:
                continue

            rec = sx.recurrence
            if rec is None:
                continue
            key = (rec.recurrence_period_type, rec.recurrence_mult)
            frequency = self.RECURRENCE_TO_FREQUENCY.get(key)
            if not frequency:
                continue

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
                start, frequency,
                after=today - timedelta(days=1),
                end_date=end, last_occur=last,
            )
            if not next_occ or next_occ > window_end:
                continue

            count += 1
            # splits-json amounts are in the book default currency
            # by construction (create pins the default; instantiate
            # passes no override), so no FX conversion is needed. A
            # foreign-currency SX would be a native-GnuCash template
            # with no splits-json slot — it contributes nothing here.
            for s in self._get_sx_splits(book, sx):
                amt = _to_decimal(s["amount"])
                if amt > 0:
                    total += amt
        return {"count": count, "total": total}

    def get_upcoming_transactions(
        self,
        days: int = 14,
        compact: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> dict | str:
        """Get scheduled transactions due within a time window.

        Leads with a ``Showing X-Y of Z upcoming transactions (date
        range)`` indicator, soonest first; page with ``offset``.

        Args:
            days: Look ahead window in days. Default 14.
            compact: If True, return the indicator + compact one-line
                format; otherwise the verbose envelope.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.
        """

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

                last = sx.last_occur
                if isinstance(last, datetime):
                    last = last.date()

                next_occ = self._next_occurrence(
                    start, frequency,
                    after=today - timedelta(days=1),
                    end_date=end, last_occur=last,
                )

                if next_occ and next_occ <= window_end:
                    splits = self._get_sx_splits(book, sx)

                    # Calculate total amount (sum of positive splits).
                    # _to_decimal is defensive for any older slots whose
                    # JSON may still carry a numeric literal.
                    total = Decimal("0")
                    for s in splits:
                        amt = _to_decimal(s["amount"])
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

            page, indicator = _paginate(
                upcoming, offset=offset, limit=limit,
                entity_name="upcoming transactions",
                date_key=lambda e: e["occurrence_date"],
            )
            if compact:
                # Prefix uniqueness across all scheduled transactions
                prefixes = _guid_prefix_map(sx.guid for sx in all_sx)
                lines = [indicator]
                lines += [
                    _upcoming_to_compact_line(e, prefixes=prefixes)
                    for e in page
                ]
                return "\n".join(lines)
            else:
                return {
                    "showing": indicator,
                    "total": len(upcoming),
                    "offset": offset,
                    "count": len(page),
                    "upcoming_transactions": page,
                }

    def create_transaction_from_scheduled(
        self,
        guid: str,
        transaction_date: str | None = None,
    ) -> dict:
        """Create an actual transaction from a scheduled template.

        Three-phase write keeping the schedule advance and the
        transaction in lockstep:

        1. **Read-only**: resolve the SX, compute ``txn_date``,
           validate preflight. No mutation.
        2. ``self.create_transaction(...)`` in its own session. A
           raise leaves the schedule unadvanced (retry-safe);
           ``status="rejected"`` means an equivalent transaction
           already exists for this period — a successful no-op,
           and the schedule still advances.
        3. **Read-write**: advance ``last_occur`` /
           ``instance_count``, reached only when phase 2 didn't
           raise.

        Advancing BEFORE the transaction call is the trap: a raise
        would leave the schedule moved with nothing posted, and a
        re-run skips the period.

        Args:
            transaction_date: Defaults to the next occurrence date.

        Returns:
            ``{transaction_guid, scheduled_transaction,
            transaction_date, instance_count, status}``. On a
            duplicate rejection, ``reason="duplicate_exists"`` (and
            the ``duplicates`` TSV) is included — explicit evidence
            for downstream LLMs to stop rather than retry.

        Raises:
            ValueError: SX not found, disabled, no upcoming
                occurrence, txn_date not past last_occur, or splits
                empty. None of these advance the schedule.
        """
        # ── Phase 1: read-only resolution. No mutation. ─────────
        with self.open(readonly=True) as book:
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

            last = sx.last_occur
            if isinstance(last, datetime):
                last = last.date()

            if transaction_date:
                txn_date = date.fromisoformat(transaction_date)
            else:
                txn_date = self._next_occurrence(
                    start, frequency,
                    after=date.today() - timedelta(days=1),
                    end_date=end, last_occur=last,
                )
                if not txn_date:
                    raise ValueError(
                        "No upcoming occurrence (past end date)"
                    )

            # Refuse dates on or before last_occur — desktop's
            # "Since Last Run" may have advanced it, and a prior
            # date would silently duplicate.
            if last and txn_date <= last:
                raise ValueError(
                    f"Transaction date {txn_date.isoformat()} is not "
                    f"after last occurrence {last.isoformat()}. The "
                    f"schedule has already been run through that date "
                    f"(possibly by GnuCash desktop). Use a later date."
                )

            splits = self._get_sx_splits(book, sx)
            if not splits:
                raise ValueError(
                    "No split templates found for scheduled "
                    "transaction"
                )

            sx_name = sx.name
            sx_description = self._get_sx_description(book, sx)

        # ── Phase 2: create the transaction (see docstring). ─────
        txn_result = self.create_transaction(
            description=sx_description,
            splits=splits,
            trans_date=txn_date,
        )

        # ── Phase 3: advance the schedule. ──────────────────────
        # Re-find by guid — the phase-1 ORM object detached when
        # its session closed.
        with self.open(readonly=False) as book:
            sx = self._find_scheduled_transaction(book, guid)
            if not sx:
                # SX deleted concurrently between phases — the
                # transaction exists; respond cleanly rather than
                # crash. Practically unreachable single-threaded.
                instance_count = None
            else:
                current_last = sx.last_occur
                if isinstance(current_last, datetime):
                    current_last = current_last.date()
                # Advance + increment only when txn_date is beyond
                # the current marker — a concurrent writer may have
                # registered the period already, and a second
                # increment would break "instance_count = distinct
                # periods produced". Never rewind.
                if current_last is None or txn_date > current_last:
                    sx.last_occur = txn_date
                    sx.instance_count += 1
                    book.save()
                instance_count = sx.instance_count

        # ── Build response. ─────────────────────────────────────
        response = {
            "transaction_guid": txn_result.get("guid"),
            "scheduled_transaction": sx_name,
            "description": sx_description,
            "transaction_date": txn_date.isoformat(),
            "instance_count": instance_count,
            "status": txn_result.get("status", "created"),
        }
        if txn_result.get("status") == "rejected":
            # Evidence that the rejection is the CORRECT outcome —
            # without it, the natural retry instinct re-triggers the
            # detector or (with force_create) creates the duplicate.
            response["reason"] = "duplicate_exists"
            if "duplicates" in txn_result:
                response["duplicates"] = txn_result["duplicates"]
        return response

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
            end_date: ``"YYYY-MM-DD"`` to set, ``""`` to clear,
                ``None`` (default) to leave unchanged. The
                empty-string sentinel exists because ``None``
                already means "no change" and MCP schemas don't
                express three-state strings cleanly.

        Raises:
            ValueError: If not found.
        """
        with self.open(readonly=False) as book:
            sx = self._find_scheduled_transaction(book, guid)
            if not sx:
                raise ValueError(
                    f"Scheduled transaction not found: {guid}"
                )

            # Audit before-state — without it the log only knows
            # the new state.
            self._stage_audit_before({
                "name": sx.name,
                "enabled": bool(sx.enabled),
                "end_date": (
                    sx.end_date.isoformat() if sx.end_date else None
                ),
            })

            if enabled is not None:
                sx.enabled = 1 if enabled else 0

            if end_date is not None:
                if end_date == "":
                    sx.end_date = None
                else:
                    sx.end_date = date.fromisoformat(end_date)

            book.save()


            all_sx_guids = [
                row[0]
                for row in book.session.query(ScheduledTransaction.guid).all()
            ]
            short_guid = _unique_prefix(sx.guid, all_sx_guids)
            return self._sx_to_dict(sx) | {"guid": short_guid}

    def delete_scheduled_transaction(self, guid: str) -> dict:
        """Delete a scheduled transaction.

        Does not affect transactions already created from this schedule.
        """

        with self.open(readonly=False) as book:
            sx = self._find_scheduled_transaction(book, guid)
            if not sx:
                raise ValueError(
                    f"Scheduled transaction not found: {guid}"
                )

            # Snapshot BEFORE delete so a mistaken delete is
            # recoverable from the audit log.
            try:
                self._stage_audit_before(self._sx_to_dict(sx))
            except Exception:
                # Audit staging must never block the delete.
                self._stage_audit_before({"name": sx.name})


            all_sx_guids = [
                row[0]
                for row in book.session.query(ScheduledTransaction.guid).all()
            ]
            short_guid = _unique_prefix(sx.guid, all_sx_guids)
            result = {
                "name": sx.name,
                "guid": short_guid,
                "status": "deleted",
            }

            # All SX-owned slots (splits-json, description) deleted
            # via Core — the parent row is going away, so anything
            # left keyed on its GUID would be an orphan.
            book.session.execute(
                Slot.__table__.delete().where(
                    Slot.__table__.c.obj_guid == sx.guid
                )
            )
            _verify_delete(
                book.session,
                Slot.__table__,
                {"obj_guid": sx.guid},
                f"Slots for scheduled transaction '{result['name']}'",
            )

            template_acct = sx.template_account
            book.session.delete(sx)
            if template_acct:
                # Desktop SXs store the recipe as real Transactions
                # on the template account (ours leave it empty).
                # Delete those first or the account delete orphans
                # their splits / fails the FK check.
                recipe_txns = {
                    s.transaction for s in list(template_acct.splits)
                }
                for txn in recipe_txns:
                    book.session.delete(txn)
                book.session.delete(template_acct)

            book.save()

            return result
