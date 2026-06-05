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

        # Anchor each occurrence to ``start_date + (n × period)`` rather
        # than chaining ``occurrence += delta``. ``relativedelta`` clamps
        # to month-end on day-of-month overflow, so a monthly schedule
        # starting Jan 31 chained as Jan 31 → Feb 28 → Mar 28 → … (drift
        # never recovers). Anchored from start_date: Feb 28 → Mar 31 →
        # Apr 30 → May 31, preserving the bookkeeper's "31st of every
        # month, falling back to month-end where needed" intent.
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

    def _get_sx_splits(self, book, sx) -> list[dict]:
        """Read split templates from the scheduled transaction's slot.

        The split templates are stored as JSON in a slot named
        'splits-json' on the ScheduledTransaction.
        """
        import json

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

        # _to_decimal routes any stray float (direct caller bypassing the
        # tool-layer SplitInput model) through Python's shortest-repr so
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

            # Create template account under root_template. We flush
            # this immediately because the SX row references its
            # GUID via ``template_act_guid``. If any of the
            # subsequent inserts (SX row, Recurrence, Slot) fail,
            # the template account is already on disk — pre-fix the
            # caller would retry with the same name and hit the
            # duplicate-name check fine, but a "ghost" template
            # account with no scheduled-transaction owner would sit
            # under ``root_template`` forever.
            #
            # Wrap the whole sequence in try/except so partial-
            # failure cleans up the orphan template account before
            # propagating the error.
            template_acct = piecash.Account(
                name=name,
                type="BANK",
                parent=book.root_template,
                commodity=self._require_default_currency(book),
            )
            # MP-11: ``book.session.add(template_acct)`` is
            # redundant — piecash's Account auto-registers via
            # the parent relationship. Documented in CLAUDE.md
            # under "piecash gotchas." The flush is kept because
            # the next block does a raw SQL INSERT against the
            # scheduled-transaction table and needs the template
            # account row to exist first.
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

                # Store split templates as JSON in a slot. Normalize
                # `amount` through _to_decimal → str so the persisted
                # JSON is always a clean decimal string, even if the
                # caller handed us a float. Otherwise a float would
                # survive json.dumps as a numeric literal, and every
                # future instantiation would replay the IEEE-754
                # epsilon.
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

                book.save()
            except Exception:
                # Cleanup the orphan template account so a retry
                # doesn't accumulate unowned template scaffolding.
                # Swallow cleanup failures — the original error is
                # what the caller needs to see.
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
                # Prefix uniqueness across all scheduled transactions
                prefixes = _guid_prefix_map(sx.guid for sx in all_sx)
                lines = [
                    _sx_to_compact_line(d, prefixes=prefixes) for d in results
                ]
                return "\n".join(lines)
            else:
                return results

    def _upcoming_within_days(
        self, book, days: int = 7,
    ) -> dict:
        """Summary stats for scheduled transactions due within
        ``days`` days from today.

        Returns ``{"count": int, "total": Decimal}``. The total is
        the sum of positive split amounts across each upcoming
        occurrence — same convention ``get_upcoming_transactions``
        uses for its per-row ``amount`` field.

        Designed for the ``get_book_summary`` orientation line
        ("Scheduled: 13 recurring, 3 due in next 7 days (CNY
        15,650)"). Single pass over scheduled transactions; cheap
        enough to compute on every summary call. Lives in
        SchedulingMixin so a book class built without the
        scheduling module simply doesn't have the method, and
        ``get_book_summary`` skips the upcoming-line render via
        ``hasattr`` (no cross-mixin tight coupling).
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
            for s in self._get_sx_splits(book, sx):
                amt = _to_decimal(s["amount"])
                if amt > 0:
                    total += amt
        return {"count": count, "total": total}

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
                    # _to_decimal is defensive for any legacy slots whose
                    # pre-fix JSON may still carry a numeric literal.
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

            if compact:
                # Prefix uniqueness across all scheduled transactions
                prefixes = _guid_prefix_map(sx.guid for sx in all_sx)
                lines = [
                    _upcoming_to_compact_line(e, prefixes=prefixes)
                    for e in upcoming
                ]
                return "\n".join(lines)
            else:
                return upcoming

    def create_transaction_from_scheduled(
        self,
        guid: str,
        transaction_date: str | None = None,
    ) -> dict:
        """Create an actual transaction from a scheduled template.

        Three-phase write to keep the schedule advance and the
        transaction in lockstep (SB-10):

        1. **Read-only** session: resolve the scheduled-transaction
           row, compute the target ``txn_date``, validate
           preflight (frequency known, date past ``last_occur``,
           splits non-empty). Captures everything needed for the
           write without mutating anything.
        2. ``self.create_transaction(...)`` runs in its own session.
           On success this lands a transaction; on a raise the
           schedule has not advanced and the caller's retry is
           safe; on ``status="rejected"`` the duplicate detector
           found an equivalent prior transaction (so an
           equivalent transaction DOES exist for this period —
           we treat that as a successful no-op and still advance
           the schedule).
        3. **Read-write** session: advance ``last_occur`` and
           ``instance_count``. Reached only when phase 2 returned
           without raising — so the schedule-advance-vs-transaction-
           existence invariant holds in both the success and
           duplicate-detected branches.

        Pre-fix the schedule advanced BEFORE the transaction call;
        any raise in phase 2 left the schedule moved with no
        transaction posted, and a re-run would skip the period
        entirely.

        Args:
            guid: Scheduled transaction GUID.
            transaction_date: Date for the transaction (YYYY-MM-DD).
                            Defaults to next occurrence date.

        Returns:
            Dict with ``transaction_guid``, ``scheduled_transaction``
            name, ``transaction_date``, ``instance_count``, and
            ``status``. When the duplicate detector caught an
            equivalent transaction, ``status="rejected"`` and a
            ``reason="duplicate_exists"`` field is included so
            downstream LLMs have explicit evidence to stop and
            move on rather than retry the call. The duplicate
            detector's ``duplicates`` TSV is forwarded too when
            present, naming the matching prior transaction(s).

        Raises:
            ValueError: If scheduled transaction not found,
                disabled, no upcoming occurrence, txn_date not
                past last_occur, or splits empty. None of these
                paths advance the schedule.
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

            # Preflight: refuse to instantiate an occurrence on or
            # before last_occur. GnuCash desktop's "Since Last Run"
            # updates last_occur when it auto-creates transactions;
            # running this tool with a prior date would silently
            # create a duplicate.
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

            # Capture the few fields the later phases need so we
            # don't have to re-resolve.
            sx_name = sx.name

        # ── Phase 2: create the transaction. ────────────────────
        # On raise: schedule is unchanged (phase 3 not reached).
        # On status="rejected": duplicate detector caught an
        # equivalent prior transaction — for SB-10 purposes that
        # transaction IS the one for this period, so we proceed
        # to advance the schedule and forward the rejection signal
        # to the caller in the response.
        txn_result = self.create_transaction(
            description=sx_name,
            splits=splits,
            trans_date=txn_date,
        )

        # ── Phase 3: advance the schedule. ──────────────────────
        # Re-resolve under a read-write session and apply both
        # mutations in one commit. We re-find by guid (the prior
        # ORM object was detached when phase-1's session closed).
        with self.open(readonly=False) as book:
            sx = self._find_scheduled_transaction(book, guid)
            if not sx:
                # Edge case: schedule deleted concurrently between
                # phases 1 and 3. The transaction exists; we
                # surface a clean response noting the orphan
                # rather than crash. Single-threaded MCP makes
                # this practically unreachable but the defense
                # is cheap.
                instance_count = None
            else:
                # ``last`` was captured under the readonly session;
                # if desktop pre-created ahead while phase 2 ran,
                # use whatever's current. Never rewind.
                current_last = sx.last_occur
                if isinstance(current_last, datetime):
                    current_last = current_last.date()
                # Only advance + increment when ``txn_date`` is
                # actually beyond the current marker. If a
                # concurrent writer covered this period between
                # phases 2 and 3 (desktop's "Since Last Run", or
                # another tool invocation), the schedule already
                # registered the period — incrementing
                # ``instance_count`` again would double-count.
                # Copilot-flagged on PR #97. The MCP server runs
                # single-threaded so this is practically
                # unreachable today, but the gate is cheap and the
                # invariant ("instance_count equals the number of
                # distinct periods this schedule has produced")
                # holds under any future multi-writer scenario.
                if current_last is None or txn_date > current_last:
                    sx.last_occur = txn_date
                    sx.instance_count += 1
                    book.save()
                instance_count = sx.instance_count

        # ── Build response. ─────────────────────────────────────
        # Not a write phase — the docstring describes three
        # write phases; this is the response-shaping step.
        response = {
            "transaction_guid": txn_result.get("guid"),
            "scheduled_transaction": sx_name,
            "transaction_date": txn_date.isoformat(),
            "instance_count": instance_count,
            "status": txn_result.get("status", "created"),
        }
        if txn_result.get("status") == "rejected":
            # Explicit evidence for downstream LLMs (including
            # dumber models that might retry status="rejected"
            # by default) that the rejection is the *correct*
            # outcome — a transaction for this period already
            # exists. Without this, the natural retry instinct
            # would either re-trigger the dupe detector (best
            # case) or, with --force_create, create the very
            # duplicate the detector was preventing.
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
            end_date: Set end date (YYYY-MM-DD), or empty string
                (``""``) to clear an existing end date back to None.
                The empty-string sentinel is unusual — Python's
                idiomatic ``None`` would mean "no change" here, so
                we needed a second sentinel for "clear it." MCP
                tool schemas don't easily express tagged unions or
                three-state strings, so the empty-string convention
                is the path of least friction. Pass ``"YYYY-MM-DD"``
                to set, ``""`` to clear, omit / pass ``None`` to
                leave unchanged.

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

            # Stage prior state for the audit log so the bookkeeper
            # can see what changed (enable/disable; end-date set/clear).
            # Without this, the log only knows the new state.
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

            # Stage SX snapshot for the audit log BEFORE delete so the
            # bookkeeper can recover the schedule's identity from the
            # log if the delete was a mistake. _sx_to_dict captures
            # frequency / start_date / end_date / instance_count.
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

            # Delete the splits-json slot via SQLAlchemy Core — column /
            # table renames surface as AttributeError at import, not as
            # silent runtime failures.
            book.session.execute(
                Slot.__table__.delete().where(
                    (Slot.__table__.c.obj_guid == sx.guid)
                    & (Slot.__table__.c.name == "splits-json")
                )
            )
            _verify_delete(
                book.session,
                Slot.__table__,
                {"obj_guid": sx.guid, "name": "splits-json"},
                f"Splits slot for scheduled transaction '{result['name']}'",
            )

            template_acct = sx.template_account
            book.session.delete(sx)
            if template_acct:
                book.session.delete(template_acct)

            book.save()

            return result
