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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction
import uuid

import piecash
from dateutil.relativedelta import relativedelta
from piecash._common import Recurrence
from piecash.core.transaction import ScheduledTransaction
from piecash.kvp import KVP_Type, Slot
from sqlalchemy import text
from sqlalchemy.orm import object_session

from gnucash_mcp.book._base import (  # noqa: F401 — re-exported ports
    _HEX_GUID_RE,
    _PT_MONTHISH,
    _PT_WEEKEND_ADJUSTED,
    _add_months,
    _adjust_for_weekend,
    _is_last_of_month,
    _nth_weekday_compare,
    _recurrence_next,
    _commodity_quantum,
    _gnc_bool,
    _guid_prefix_map,
    _new_split,
    _sx_to_compact_line,
    _to_decimal,
    _unique_prefix,
    _upcoming_to_compact_line,
    _verify_composite_write,
    _verify_delete,
    _verify_write,
    _check_text,
    _TEXT_WIDTH,
    _SLOT_TEXT_WIDTH,
    _check_one_line,
    _check_ledger_date,
)
from gnucash_mcp._format import _paginate


def _gdate(v) -> date | None:
    """GDATE columns come back as ``YYYYMMDD`` strings on SQLite and
    as dates on PostgreSQL; date.fromisoformat rejects the former on
    3.10."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = "".join(ch for ch in str(v) if ch.isdigit())
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


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
        """Next occurrence of a label-frequency schedule after
        ``after`` (default today), raised to ``last_occur`` when that
        is later; None past ``end_date``. A thin face on the
        recurrence engine for the six labels this server creates;
        the schedule-level rule is ``_sx_next_due``, which reads the
        recurrence rows themselves.
        """
        if after is None:
            after = date.today()
        if last_occur is not None and last_occur > after:
            after = last_occur
        pt, mult = self.FREQUENCY_TO_RECURRENCE[frequency]
        occurrence = _recurrence_next(pt, mult, start_date, "none", after)
        if occurrence is None:
            return None
        if end_date and occurrence > end_date:
            return None
        return occurrence

    def _sx_schedule(
        self, sx,
    ) -> tuple[str | None, date, date | None, date | None]:
        """Normalized schedule fields for a ScheduledTransaction:
        ``(frequency, start_date, end_date, last_occur)``.

        ``frequency`` is None for recurrence shapes this module
        doesn't model (daily, semiannual, end-of-month, nth-weekday,
        composite schedules) — callers skip those. The date columns
        arrive as date or datetime depending on the piecash column
        path; normalized here once instead of at every reader.
        """
        rows = self._sx_recurrences(sx)
        return (
            self._describe_recurrences(rows),
            _gdate(sx.start_date), _gdate(sx.end_date), _gdate(sx.last_occur),
        )

    def _sx_recurrences(self, sx) -> list[tuple[str, int, date, str]]:
        """Every recurrence row of a schedule as ``(period_type,
        mult, period_start, weekend_adjust)``. Raw SQL: piecash's
        ``recurrence`` relation is ``uselist=False`` and shows one
        row of a composite schedule."""
        session = object_session(sx)
        rows = session.execute(
            text(
                "SELECT recurrence_period_type, recurrence_mult, "
                "recurrence_period_start, recurrence_weekend_adjust "
                "FROM recurrences WHERE obj_guid = :g ORDER BY id"
            ),
            {"g": sx.guid},
        ).fetchall()
        return [
            (r[0], int(r[1] or 1), _gdate(r[2]), r[3] or "none")
            for r in rows
            if _gdate(r[2]) is not None
        ]

    def _describe_recurrences(self, rows) -> str | None:
        """A frequency label honest about the rows: the six names
        this server creates when one row matches, a plain
        description for any other single row, ``composite (N
        rules)`` for several. None with no rows."""
        if not rows:
            return None
        if len(rows) > 1:
            return f"composite ({len(rows)} rules)"
        pt, mult, _start, wadj = rows[0]
        label = self.RECURRENCE_TO_FREQUENCY.get((pt, mult))
        if label is None:
            unit = {
                "day": "day", "week": "week", "month": "month",
                "year": "year",
            }.get(pt)
            if pt == "once":
                label = "once"
            elif unit:
                label = f"every {mult} {unit}s" if mult != 1 else f"every {unit}"
            elif pt == "end of month":
                label = "end of month" + (f" (every {mult} months)" if mult != 1 else "")
            elif pt in ("nth weekday", "last weekday"):
                label = pt + (f" (every {mult} months)" if mult != 1 else "")
            else:
                label = pt
        if wadj in ("back", "forward"):
            label += f", weekends {wadj}"
        return label

    def _sx_next_due(self, sx) -> date | None:
        """The oldest occurrence this schedule has not yet produced —
        GnuCash's own Since-Last-Run rule.

        One rule for every surface: the dashboard's overdue warning,
        ``next_occurrence`` on list/upcoming, the Scheduled summary
        line, and the default instantiation date all read this, so
        an overdue period can't be flagged by one and skipped by
        another. Searching from ``last_occur`` (or the start date)
        rather than from today is the whole point: a schedule that
        missed July answers July. Searching from today answers the
        next date after today — a future-dated posting that moves
        ``last_occur`` past July and, through the backfill guard,
        locks July out for good.

        Reads the recurrence ROWS (all of them) through the ported
        engine — the anchor day, multiplier, period type, and
        weekend adjustment are theirs, and a composite schedule's
        next is the earliest across its rows
        (recurrenceListNextInstance). ``start_date`` only seeds the
        reference for a schedule that has never run. None when the
        schedule has no rows, the end date has passed, or a finite
        schedule (``num_occur > 0``) has no occurrences remaining —
        the stops GnuCash applies in xaccSchedXactionGetNextInstance.
        """
        _label, start, end, last = self._sx_schedule(sx)
        rows = self._sx_recurrences(sx)
        if not rows or start is None:
            return None
        if sx.num_occur > 0 and sx.rem_occur <= 0:
            return None
        ref = last if last is not None else (
            start - timedelta(days=1) if start > date.min else start
        )
        candidates = [
            _recurrence_next(pt, mult, anchor, wadj, ref)
            for pt, mult, anchor, wadj in rows
        ]
        candidates = [c for c in candidates if c is not None]
        if not candidates:
            return None
        nxt = min(candidates)
        if end and nxt > end:
            return None
        return nxt

    def _sx_to_dict(self, sx, frequency: str | None = None) -> dict:
        """Serialize a ScheduledTransaction to a dict.

        Args:
            sx: piecash ScheduledTransaction object.
            frequency: Pre-computed frequency string. If None, derived
                       from recurrence.
        """
        freq, start, end, last = self._sx_schedule(sx)
        if frequency is None:
            frequency = freq or "unknown"
        next_occ = self._sx_next_due(sx)

        d = {
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
        if sx.num_occur > 0:
            d["remaining_occurrences"] = sx.rem_occur
        return d

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

    # ── Native template recipes ───────────────────────────────────
    # GnuCash stores a schedule's recipe as real Transaction rows on
    # the schedule's template account, one split per leg, with the
    # target account and the amounts in KVP slots on each split.
    # Keys verbatim from libgnucash/engine/Split.cpp (GNC_SX_ID +
    # GNC_SX_ACCOUNT / *_FORMULA / *_NUMERIC), Transaction.cpp
    # (GNC_SX_FROM) and gnc-commodity.h (GNC_COMMODITY_NS_TEMPLATE).
    # Since-Last-Run reads sched-xaction/account by GUID, prefers
    # the numeric when it's non-zero and no variables are bound, and
    # takes debit − credit as the signed value. Before this the
    # recipe lived in a private splits-json slot GnuCash never read,
    # so desktop's Since-Last-Run advanced our schedules with nothing
    # posted, and desktop-made schedules had no recipe here.
    _SX_FRAME = "sched-xaction"
    _SX_ACCOUNT = "sched-xaction/account"
    _SX_CREDIT_FORMULA = "sched-xaction/credit-formula"
    _SX_DEBIT_FORMULA = "sched-xaction/debit-formula"
    _SX_CREDIT_NUMERIC = "sched-xaction/credit-numeric"
    _SX_DEBIT_NUMERIC = "sched-xaction/debit-numeric"
    # Ours, namespaced: the fixed quantity this server replays on a
    # cross-commodity leg. GnuCash asks the user for a rate instead
    # and ignores this key.
    _MCP_FRAME = "gnc-mcp"
    _MCP_QUANTITY = "gnc-mcp/quantity"
    # Stamped on an instantiated transaction, as desktop does.
    _SX_FROM = "from-sched-xaction"
    _TEMPLATE_NS = "template"
    _LEGACY_SX_SLOTS = ("splits-json", "description", "notes", "currency")

    def _ensure_template_commodity(self, book):
        """GnuCash's template pseudo-commodity, exactly the row
        gnc_commodity_table_add_default_data creates: namespace
        ``template``, mnemonic/fullname/cusip ``template``, fraction
        1. piecash never creates it; books GnuCash has opened have
        it. list_commodities already filters the namespace."""
        c = book.session.query(piecash.Commodity).filter_by(
            namespace=self._TEMPLATE_NS, mnemonic="template",
        ).first()
        if c is not None:
            return c
        c = piecash.Commodity(
            namespace=self._TEMPLATE_NS, mnemonic="template",
            fullname="template", fraction=1, cusip="template",
            quote_flag=0, quote_source="user", book=book,
        )
        book.session.flush()
        return c

    def _slot_insert(self, book, obj_guid, name, slot_type, label, **cols):
        book.session.execute(
            Slot.__table__.insert().values(
                obj_guid=obj_guid, name=name, slot_type=slot_type, **cols,
            )
        )
        _verify_composite_write(
            book.session, Slot.__table__,
            {"obj_guid": obj_guid, "name": name}, label,
        )

    def _write_template_recipe(
        self, book, template_acct, currency, description, notes,
        start, legs: list[dict],
    ):
        """Write the recipe the way the SX editor does: one template
        Transaction on the template account, one zero-value Split per
        leg carrying the six ``sched-xaction`` slots (both sides
        always written, zero on the unused one). ``legs`` items:
        ``account`` (Account), ``amount`` (Decimal, transaction
        currency), ``memo``, ``action``, ``quantity`` (Decimal|None).
        Returns the template transaction."""
        txn = piecash.Transaction(
            currency=currency,
            description=description,
            notes=notes or None,
            post_date=start,
            splits=[
                _new_split(
                    template_acct, Decimal("0"), Decimal("0"), currency,
                    memo=leg.get("memo") or "",
                    action=leg.get("action") or "",
                )
                for leg in legs
            ],
        )
        book.session.flush()
        for split, leg in zip(txn.splits, legs):
            label = f"template split for {leg['account'].fullname}"
            frame_guid = uuid.uuid4().hex
            self._slot_insert(
                book, split.guid, self._SX_FRAME,
                KVP_Type.KVP_TYPE_FRAME, label, guid_val=frame_guid,
            )
            self._slot_insert(
                book, frame_guid, self._SX_ACCOUNT,
                KVP_Type.KVP_TYPE_GUID, label,
                guid_val=leg["account"].guid,
            )
            amount = leg["amount"]
            debit = amount if amount > 0 else Decimal("0")
            credit = -amount if amount < 0 else Decimal("0")
            for side, val in (("credit", credit), ("debit", debit)):
                self._slot_insert(
                    book, frame_guid, f"{self._SX_FRAME}/{side}-formula",
                    KVP_Type.KVP_TYPE_STRING, label,
                    string_val=format(val, "f") if val else "",
                )
                # The SX editor stores what gnc_exp_parser returns,
                # a reduced fraction (gnc_numeric_reduce): 4200.00 is
                # 4200/1, 42.50 is 85/2, and the unused side is 0/1.
                reduced = Fraction(val)
                self._slot_insert(
                    book, frame_guid, f"{self._SX_FRAME}/{side}-numeric",
                    KVP_Type.KVP_TYPE_NUMERIC, label,
                    numeric_val_num=reduced.numerator,
                    numeric_val_denom=reduced.denominator,
                )
            if leg.get("quantity") is not None:
                q = leg["quantity"]
                q_denom = leg["account"].commodity.fraction
                mcp_frame = uuid.uuid4().hex
                self._slot_insert(
                    book, split.guid, self._MCP_FRAME,
                    KVP_Type.KVP_TYPE_FRAME, label, guid_val=mcp_frame,
                )
                self._slot_insert(
                    book, mcp_frame, self._MCP_QUANTITY,
                    KVP_Type.KVP_TYPE_NUMERIC, label,
                    # Rounded, as a split's quantity is at storage
                    # (it was truncated: 1.23456 kept as 1.2345 where
                    # create_transaction stores 1.2346 — MM-15).
                    numeric_val_num=int(
                        (Decimal(str(q)) * q_denom).quantize(
                            Decimal(1), rounding=ROUND_HALF_UP,
                        )
                    ),
                    numeric_val_denom=q_denom,
                )
        return txn

    def _split_frame_children(self, book, split_guid, frame_name):
        """``{path: (slot_type, string_val, guid_val, num, denom)}``
        for the children of one frame on a split. Raw SQL: the
        polymorphic Slot ORM is unsafe to query, and loading a
        SlotGUID into the session arms the delete cascade."""
        frame = book.session.execute(
            text(
                "SELECT guid_val FROM slots WHERE obj_guid = :s "
                "AND name = :n AND slot_type = 9"
            ),
            {"s": split_guid, "n": frame_name},
        ).first()
        if not frame:
            return {}
        rows = book.session.execute(
            text(
                "SELECT name, slot_type, string_val, guid_val, "
                "numeric_val_num, numeric_val_denom FROM slots "
                "WHERE obj_guid = :f"
            ),
            {"f": frame[0]},
        ).fetchall()
        return {r[0]: tuple(r[1:]) for r in rows}

    @staticmethod
    def _rational_amount(num: int, denom: int, fraction: int) -> Decimal:
        """A stored ``num/denom`` as a Decimal.

        A decimal denominator keeps its precision as typed (4250/100
        is 42.50, not 42.5), exactly, at any magnitude. Any OTHER
        denominator is a formula desktop evaluated and stored as the
        exact rational — "100/3" is 100 over 3, "1234/12" is 617 over
        6 (gnc-exp-parser divides with GNC_HOW_DENOM_EXACT and
        reduces) — and has no finite decimal form: it is rounded
        half-up to ``fraction``, the template currency's, which is
        what GnuCash's split setters do when Since-Last-Run turns
        the template into a transaction. Quantizing to ``1/denom``
        instead raised ``decimal.InvalidOperation`` (a third needs
        more digits than the context has) and took ``get_book_
        summary`` down with it, on any book holding such a schedule,
        enabled or not (adversarial review 2026-09-30, C19).
        """
        from gnucash_mcp.book import _entry_math

        places = len(str(denom)) - 1
        if denom == 10 ** places:
            # Never fewer places than the currency has: the editor
            # stores 4200.00 reduced, as 4200/1. Never more either:
            # desktop stores "1.001" typed into a USD schedule as
            # 1001/1000 and Since-Last-Run rounds it half-up to the
            # currency; the server refused the instance (scoped
            # review 2026-10-05, M-4).
            unit = len(str(fraction)) - 1
            if fraction == 10 ** unit and places < unit:
                return Decimal(num).scaleb(-places).quantize(
                    Decimal(1).scaleb(-unit)
                )
            if fraction == 10 ** unit and places > unit:
                return _entry_math.round_half_up(Fraction(num, denom), fraction)
            return Decimal(num).scaleb(-places)
        return _entry_math.round_half_up(Fraction(num, denom), fraction)

    @staticmethod
    def _slot_amount(children, numeric_key, formula_key, fraction=100):
        """Debit or credit side of a template split: the numeric when
        present and non-zero (what Since-Last-Run prefers), else the
        formula parsed as a plain number, else None (a formula with
        variables — GnuCash prompts; we refuse). ``fraction`` is the
        template currency's, for a numeric with no decimal form
        (``_rational_amount``)."""
        num = children.get(numeric_key)
        if num and num[3] is not None and num[4] and num[3] != 0:
            return SchedulingMixin._rational_amount(
                int(num[3]), int(num[4]), fraction,
            )
        formula = children.get(formula_key)
        text_val = (formula[1] or "").strip() if formula else ""
        if not text_val:
            return Decimal("0")
        try:
            return Decimal(text_val.replace(",", ""))
        except InvalidOperation:
            return None

    def _sx_recipe(self, book, sx) -> dict:
        """THE reader for a schedule's recipe. Native template rows
        first (what GnuCash wrote, or what this server writes since
        native storage); ``splits-json`` and the three SX slots as
        the legacy fallback. Readers never write.

        ``{"source": "native"|"legacy"|"none", "splits": [...],
        "description", "notes", "currency", "template_txn_count",
        "problems": [...]}``. Split dicts are the shared contract
        (``account`` = GUID, ``amount``, ``memo``, ``action``,
        ``quantity``) and go straight to create_transaction.
        ``problems`` names anything instantiation must refuse: a
        formula with variables, a split without an account, more
        than one template transaction.
        """
        import json

        recipe = {
            "source": "none", "splits": [], "description": None,
            "notes": None, "currency": None, "template_txn_count": 0,
            "problems": [],
        }
        tmpl = sx.template_account
        rows = []
        if tmpl is not None:
            rows = book.session.execute(
                text(
                    "SELECT s.guid, s.tx_guid, s.memo, s.action "
                    "FROM splits s WHERE s.account_guid = :a "
                    "ORDER BY s.tx_guid, s.guid"
                ),
                {"a": tmpl.guid},
            ).fetchall()
        if rows:
            tx_guids = list(dict.fromkeys(r[1] for r in rows))
            recipe["source"] = "native"
            recipe["template_txn_count"] = len(tx_guids)
            if len(tx_guids) > 1:
                recipe["problems"].append(
                    f"{len(tx_guids)} template transactions (this "
                    f"server instantiates one)"
                )
            txn = book.session.query(piecash.Transaction).filter_by(
                guid=tx_guids[0],
            ).first()
            recipe["description"] = txn.description or None
            recipe["notes"] = txn.notes or None
            recipe["currency"] = txn.currency.mnemonic
            for split_guid, tx_guid, memo, action in rows:
                if tx_guid != tx_guids[0]:
                    continue
                ch = self._split_frame_children(
                    book, split_guid, self._SX_FRAME,
                )
                acct = ch.get(self._SX_ACCOUNT)
                if not acct or not acct[2]:
                    recipe["problems"].append(
                        "a template split names no account"
                    )
                    continue
                debit = self._slot_amount(
                    ch, self._SX_DEBIT_NUMERIC, self._SX_DEBIT_FORMULA,
                    txn.currency.fraction,
                )
                credit = self._slot_amount(
                    ch, self._SX_CREDIT_NUMERIC, self._SX_CREDIT_FORMULA,
                    txn.currency.fraction,
                )
                if debit is None or credit is None:
                    bad = (ch.get(self._SX_DEBIT_FORMULA) or ch.get(
                        self._SX_CREDIT_FORMULA) or ("", ""))[1]
                    recipe["problems"].append(
                        f"formula with variables: {bad!r} (GnuCash "
                        f"prompts for these; run it from the desktop)"
                    )
                    continue
                leg = {
                    "account": acct[2],
                    "amount": str(debit - credit),
                    "memo": memo or "",
                }
                if action:
                    leg["action"] = action
                q = self._split_frame_children(
                    book, split_guid, self._MCP_FRAME,
                ).get(self._MCP_QUANTITY)
                if q and q[4]:
                    leg["quantity"] = str(self._rational_amount(
                        int(q[3]), int(q[4]), int(q[4]),
                    ))
                recipe["splits"].append(leg)
            # The splits table has no sequence column, so the
            # caller's order is not recoverable; ledger order
            # instead — debits first, then by account path — which
            # is stable across backends (no rowid on PostgreSQL).
            names = {}
            for leg in recipe["splits"]:
                a = book.session.query(piecash.Account).filter_by(
                    guid=leg["account"],
                ).first()
                names[leg["account"]] = a.fullname if a else leg["account"]
            recipe["splits"].sort(
                key=lambda l: (
                    _to_decimal(l["amount"]) < 0, names[l["account"]],
                )
            )
            return recipe

        raw = self._get_sx_slot_string(book, sx.guid, "splits-json")
        if raw:
            recipe["source"] = "legacy"
            recipe["splits"] = json.loads(raw)
            recipe["description"] = self._get_sx_slot_string(
                book, sx.guid, "description",
            )
            recipe["notes"] = self._get_sx_slot_string(
                book, sx.guid, "notes",
            )
            recipe["currency"] = self._get_sx_slot_string(
                book, sx.guid, "currency",
            )
        return recipe

    def _get_sx_splits(self, book, sx) -> list[dict]:
        """The recipe's splits — see ``_sx_recipe``."""
        return self._sx_recipe(book, sx)["splits"]

    def _migrate_sx_recipe(self, book, sx, recipe: dict) -> bool:
        """Write path only: rewrite a legacy ``splits-json`` recipe as
        native template rows and drop the four legacy slots. A legacy
        ref that no longer resolves leaves the schedule as it is (the
        next instantiation will name the account). Returns True when
        it migrated."""
        if recipe["source"] != "legacy" or not recipe["splits"]:
            return False
        legs = []
        for s in recipe["splits"]:
            acct = self._resolve_account(book, s["account"])
            if acct is None:
                return False
            legs.append({
                "account": acct,
                "amount": _to_decimal(s["amount"]),
                "memo": s.get("memo", ""),
                "action": s.get("action"),
                "quantity": (
                    _to_decimal(s["quantity"])
                    if s.get("quantity") is not None else None
                ),
            })
        currency = (
            self._find_commodity(book, recipe["currency"])
            if recipe["currency"] else None
        ) or self._require_default_currency(book)
        start = _gdate(sx.start_date)
        # A fresh container, exactly as create makes it — the legacy
        # template account is a book-currency account named by the
        # schedule; a template transaction on it crashes GnuCash's
        # SX editor (bookkeeper, 2026-09-10). Repoint, then drop the
        # old account.
        old_acct = sx.template_account
        new_acct = piecash.Account(
            name=sx.guid, type="BANK", parent=book.root_template,
            commodity=self._ensure_template_commodity(book),
        )
        book.session.flush()
        self._write_template_recipe(
            book, new_acct, currency,
            recipe["description"] or sx.name, recipe["notes"],
            start, legs,
        )
        sx.template_account = new_acct
        book.session.flush()
        if old_acct is not None and old_acct.guid != new_acct.guid:
            # Account.scheduled_transaction cascades delete-orphan;
            # re-read it after the repoint so it finds nothing.
            book.session.expire(old_acct, ["scheduled_transaction"])
            for t in self._strip_template_recipe(
                book, old_acct, f"legacy template of '{sx.name}'",
            ):
                book.session.delete(t)
            book.session.delete(old_acct)
            book.session.flush()
        for key in self._LEGACY_SX_SLOTS:
            book.session.execute(
                Slot.__table__.delete().where(
                    (Slot.__table__.c.obj_guid == sx.guid)
                    & (Slot.__table__.c.name == key)
                )
            )
            _verify_delete(
                book.session, Slot.__table__,
                {"obj_guid": sx.guid, "name": key},
                f"legacy slot {key} on '{sx.name}'",
            )
        return True

    def _migrate_all_legacy(self, book) -> int:
        """Write path only: convert EVERY legacy recipe in the book to
        native rows, posting nothing. Called by every schedule write,
        so the first write of any kind after the upgrade converts the
        whole book in one step — a real book's schedules are all
        exposed to desktop's Since-Last-Run until then, and
        converting them one instantiation at a time would post early
        (bookkeeper addendum, 2026-09-10). A no-change
        update_scheduled_transaction is the deliberate one-call
        conversion. Returns how many migrated."""
        migrated = 0
        for sx in book.session.query(ScheduledTransaction).all():
            recipe = self._sx_recipe(book, sx)
            if recipe["source"] == "legacy" and self._migrate_sx_recipe(
                book, sx, recipe,
            ):
                migrated += 1
        return migrated

    def _strip_template_recipe(self, book, template_acct, label):
        """Before ORM-deleting a template account's recipe rows: strip
        the GUID/frame slots off every template split and transaction
        so the delete can't cascade into the TARGET accounts' slots.
        Returns the recipe transactions to delete."""
        splits = list(template_acct.splits)
        txns = {s.transaction for s in splits}
        owners = [s.guid for s in splits] + [t.guid for t in txns]
        if owners:
            self._strip_guid_slots(
                book, owners, label, objects=[*splits, *txns],
            )
        return txns

    def _sx_splits_for_display(
        self, book, splits: list[dict],
    ) -> list[dict]:
        """Stored split refs rendered for a reader: GUID-stored
        accounts become full paths; a GUID whose account is gone
        stays as-is with ``account_missing: True`` so the reader
        sees why the next instantiation will fail. Path-stored rows
        (templates from before GUID storage) pass through untouched.
        """
        out = []
        for s in splits:
            ref = s.get("account", "")
            if len(ref) == 32 and _HEX_GUID_RE.fullmatch(ref):
                acct = book.session.query(
                    piecash.Account
                ).filter_by(guid=ref).first()
                s = dict(s)
                if acct is not None:
                    s["account"] = acct.fullname
                else:
                    s["account_missing"] = True
            out.append(s)
        return out

    def _get_sx_description(self, book, sx) -> str:
        """Instantiation description: the recipe's, else the SX name
        (what instantiation always used to use)."""
        return self._sx_recipe(book, sx)["description"] or sx.name

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
        notes: str | None = None,
        currency: str | None = None,
    ) -> dict:
        """Create a recurring transaction template.

        Args:
            name: Name for the scheduled transaction.
            description: Transaction description when created.
            splits: List of splits, same format as create_transaction:
                [{"account": "Expenses:Rent", "amount": "1850.00"}, ...]
                ``quantity`` per the shared contract — required when
                an account's commodity differs from the template's
                transaction currency; stored and replayed at every
                instantiation.
            start_date: First occurrence date (YYYY-MM-DD).
            frequency: How often: "weekly", "biweekly", "monthly",
                      "quarterly", "yearly".
            end_date: Optional last occurrence date (YYYY-MM-DD).
            enabled: Whether active. Default True.
            notes: Transaction-level notes applied to every
                instantiated transaction (what the purchase is —
                visible in GnuCash's double-line register view).
            currency: ISO code denominating every instantiated
                transaction; defaults to the book default. A
                template whose legs are all in one foreign currency
                (Lin Wei's USD-to-USD card payment in a CNY book)
                needs this — amounts are then that currency's, with
                no fabricated conversions. Deliberately not
                updatable after creation: stored amounts are
                denominated in it, so changing it would silently
                re-denominate the schedule — delete and recreate
                instead.

        Returns:
            Dict with guid, name, next_occurrence, and status.

        Raises:
            ValueError: If invalid frequency, accounts not found,
                       or splits don't balance.
        """
        # Every free-text argument through the one text gate (scoped
        # review 2026-10-05, I-4): no control characters, GnuCash's
        # column width.
        for _field in ("name", "description", "notes", "title", "reference", "fullname", "mnemonic", "memo", "action"):
            _check_text(
                locals().get(_field),
                _SLOT_TEXT_WIDTH if _field == "notes" else _TEXT_WIDTH, _field,
            )
            if _field in ("name", "title", "reference", "fullname", "mnemonic", "action"):
                _check_one_line(locals().get(_field), _field)
        if frequency not in self.VALID_FREQUENCIES:
            raise ValueError(
                f"Invalid frequency: {frequency}. "
                f"Valid: {', '.join(sorted(self.VALID_FREQUENCIES))}"
            )

        for leg in splits or []:
            if isinstance(leg, dict):
                _check_text(leg.get("memo"), _TEXT_WIDTH, "memo")
        parsed_start = date.fromisoformat(start_date)
        parsed_end = (
            date.fromisoformat(end_date) if end_date else None
        )
        _check_ledger_date(parsed_start, "start_date")
        _check_ledger_date(parsed_end, "end_date")
        # A schedule that ends before it starts has no occurrence and
        # was accepted with ``next_occurrence: None`` (C42).
        if parsed_end is not None and parsed_end < parsed_start:
            raise ValueError(
                f"end_date {parsed_end.isoformat()} is before "
                f"start_date {parsed_start.isoformat()}"
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
            sx_currency = None
            if currency:
                sx_currency = self._find_commodity(book, currency)
                if not sx_currency:
                    raise ValueError(
                        f"Currency '{currency}' not found in book — "
                        f"create it first (create_commodity) or "
                        f"record a transaction in it once"
                    )
            frame = sx_currency or self._require_default_currency(book)
            # Shared split contract (resolution, sum-to-zero,
            # quantity rules) at CREATE time — a template must
            # reject here anything instantiation couldn't book.
            # Pre-fix, an all-foreign-leg template created fine
            # and then failed at every instantiation forever.
            validated = self._validate_transaction_splits(
                book, splits, frame,
            )
            # A placeholder leg passes the split contract (it
            # resolves, it sums) and then fails at every
            # instantiation forever — refuse it here, the same
            # gate create_transactions applies in phase 1.
            for v in validated:
                if v["account"].placeholder:
                    raise self._placeholder_error(v["account"])

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
            # As xaccSchedXactionInit makes it: named by the SX GUID,
            # BANK, on the template pseudo-commodity.
            template_acct = piecash.Account(
                name=sx_guid,
                type="BANK",
                parent=book.root_template,
                commodity=self._ensure_template_commodity(book),
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
                        enabled=_gnc_bool(enabled),
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

                # The recipe, in GnuCash's own shape (see the
                # constants above). Accounts by GUID from the
                # validated splits — a stored path broke at the
                # first rename; cross-commodity legs keep their
                # replay quantity in our namespaced slot.
                self._write_template_recipe(
                    book, template_acct, frame,
                    description or name, notes, parsed_start,
                    [
                        {
                            "account": v["account"],
                            "amount": _to_decimal(s["amount"]),
                            "memo": s.get("memo", ""),
                            "action": s.get("action"),
                            "quantity": (
                                _to_decimal(s["quantity"])
                                if s.get("quantity") is not None
                                else None
                            ),
                        }
                        for s, v in zip(splits, validated)
                    ],
                )
                shapes = self._upgrade_book_shapes(book)

                book.save()
            except Exception:
                # Roll back everything staged in this session: the
                # flushed template account and any SX / recurrence /
                # slot rows the raw inserts already landed. A
                # delete-then-save here would COMMIT those partial
                # rows; it only ever looked clean because piecash's
                # Account.scheduled_transaction cascade happened to
                # sweep them out with the account.
                book.cancel()
                raise

            # The first number a caller sees after creating a
            # schedule — read from the same rule as every other
            # surface. This site used to search from today and told
            # the bookkeeper "October" for a schedule starting in
            # July, which is why explicit dates were being passed
            # to every instantiation on the production book.
            sx_row = book.session.query(
                ScheduledTransaction
            ).filter_by(guid=sx_guid).first()
            next_occ = self._sx_next_due(sx_row)

            all_sx_guids = [
                row[0]
                for row in book.session.query(ScheduledTransaction.guid).all()
            ]
            short_guid = _unique_prefix(sx_guid, all_sx_guids)
            out = {
                "guid": short_guid,
                "name": name,
                "frequency": frequency,
                "next_occurrence": (
                    next_occ.isoformat() if next_occ else None
                ),
                "status": "created",
            }
            out.update(shapes)
            return out

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
            # Soonest due first, then name, then GUID. The query's
            # own order is the backend's (insertion on SQLite,
            # primary key on InnoDB) and was leaking into the list.
            all_sx = sorted(
                book.session.query(ScheduledTransaction).all(),
                key=lambda sx: (
                    self._sx_next_due(sx) or date.max, sx.name, sx.guid,
                ),
            )
            default_mnemonic = self._require_default_currency(book).mnemonic

            results = []
            for sx in all_sx:
                if enabled_only and not sx.enabled:
                    continue
                d = self._sx_to_dict(sx)
                if not compact:
                    recipe = self._sx_recipe(book, sx)
                    # Echoing the name back as "description" would
                    # just be noise; a foreign currency is worth
                    # naming, the book default is not.
                    desc = recipe["description"]
                    if desc and desc != sx.name:
                        d["description"] = desc
                    if recipe["notes"]:
                        d["notes"] = recipe["notes"]
                    if recipe["currency"] and recipe["currency"] != default_mnemonic:
                        d["currency"] = recipe["currency"]
                    d["splits"] = self._sx_splits_for_display(
                        book, recipe["splits"],
                    )
                    d["recipe"] = recipe["source"]
                    if recipe["problems"]:
                        d["problems"] = recipe["problems"]
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

    def _upcoming_cash_legs(self, book, days: int = 7) -> dict:
        """One pass over the schedules due within ``days`` days —
        the shared source for the Scheduled line
        (``_upcoming_within_days``) and the low-cash scheduled-
        outflow trigger (``_scheduled_cash_out_by_account``), so the
        two can't drift (spec B6)::

            {"count": int, "unrated": int, "legacy": int,
             "occurrences": [{"due": date,
                              "legs": [(account_guid, amount)]}]}

        ``legs`` are each occurrence's CASH legs — splits into
        BANK/CASH accounts outside a retirement subtree, the same
        accounts the low-cash check reads — in the BOOK DEFAULT
        currency: foreign-currency templates convert at the latest
        market rate; templates whose currency has no rate on file
        are counted but carry no legs (``unrated`` reports how
        many). An overdue occurrence belongs to the dashboard's
        overdue bucket (same ``_sx_next_due``), not here.
        """
        today = date.today()
        window_end = today + timedelta(days=days)

        default_currency = self._require_default_currency(book)
        rates = self._rates_as_of(book, today, default_currency)

        count = 0
        unrated = 0
        legacy = 0
        occurrences: list[dict] = []
        for sx in book.session.query(ScheduledTransaction).all():
            # Recipes still in the pre-native shape are invisible
            # to desktop until their first write migrates them.
            if self._sx_recipe(book, sx)["source"] == "legacy":
                legacy += 1
            if not sx.enabled:
                continue

            next_occ = self._sx_next_due(sx)
            if not next_occ or next_occ < today or next_occ > window_end:
                continue

            count += 1
            # splits-json amounts are denominated in the template's
            # currency (the ``currency`` slot; absent = book
            # default).
            rate = Decimal("1")
            recipe = self._sx_recipe(book, sx)
            sx_cur = recipe["currency"]
            if sx_cur and sx_cur != default_currency.mnemonic:
                commodity = self._find_commodity(book, sx_cur)
                rate = (
                    rates.get(commodity.guid) if commodity else None
                )
                if rate is None:
                    unrated += 1
                    continue
            legs = []
            for s in recipe["splits"]:
                acct = self._sx_split_cash_account(
                    book, s.get("account", ""),
                )
                if acct is not None:
                    legs.append((acct.guid, _to_decimal(s["amount"]) * rate))
            occurrences.append({"due": next_occ, "legs": legs})
        return {
            "count": count, "unrated": unrated, "legacy": legacy,
            "occurrences": occurrences,
        }

    def _upcoming_within_days(
        self, book, days: int = 7,
    ) -> dict:
        """Summary stats for scheduled transactions due within
        ``days`` days: ``{"count": int, "cash_out": Decimal,
        "cash_in": Decimal, "unrated": int, "legacy": int}``.

        Money is measured on each occurrence's cash legs
        (``_upcoming_cash_legs``), netted per occurrence: negative
        lands in ``cash_out``, positive in ``cash_in``. A signless
        sum of positive splits (the old ``total``) added a
        paycheck's gross to the week's bills — USD 6,777 "due" on
        a live book whose real outflow was USD 1,931. Netting per
        occurrence also keeps a checking→savings sweep out of both
        columns, and a paycheck counts only what reaches checking,
        not the 401k/FSA/tax legs. A schedule with no cash leg (a
        charge to a card) still counts toward ``count`` but moves
        no cash this week. Feeds the get_book_summary Scheduled
        line; lives here so a book class built without scheduling
        lacks the method and the summary skips the line via
        ``hasattr``.
        """
        legs = self._upcoming_cash_legs(book, days)
        cash_out = Decimal("0")
        cash_in = Decimal("0")
        for occ in legs["occurrences"]:
            net = sum((amt for _g, amt in occ["legs"]), Decimal("0"))
            if net < 0:
                cash_out += -net
            else:
                cash_in += net
        return {
            "count": legs["count"], "cash_out": cash_out, "cash_in": cash_in,
            "unrated": legs["unrated"], "legacy": legs["legacy"],
        }

    def _scheduled_cash_out_by_account(
        self, book, days: int = 7,
    ) -> dict[str, tuple[Decimal, date]]:
        """``{account_guid: (cash_out, latest_due)}`` — each cash
        account's scheduled net outflow over the next ``days`` days,
        in the book default, and the last date it lands. Per
        occurrence an account's legs net (a sweep in and out of the
        same account is nothing); a negative net is outflow. The
        low-cash check's second trigger (spec B6): a balance below
        the week's scheduled bills is low, however healthy the
        account's average pace looks.
        """
        out: dict[str, tuple[Decimal, date]] = {}
        for occ in self._upcoming_cash_legs(book, days)["occurrences"]:
            net: dict[str, Decimal] = {}
            for guid, amt in occ["legs"]:
                net[guid] = net.get(guid, Decimal("0")) + amt
            for guid, amt in net.items():
                if amt >= 0:
                    continue
                prev = out.get(guid)
                out[guid] = (
                    (prev[0] if prev else Decimal("0")) - amt,
                    max(prev[1], occ["due"]) if prev else occ["due"],
                )
        return out

    def _sx_split_cash_account(self, book, ref: str):
        """The recipe split's account when it is spendable cash:
        BANK/CASH, outside a retirement subtree — the low-cash
        check's notion of cash; else ``None``. ``ref`` is a stored
        recipe account: a full GUID, or a path on pre-GUID
        templates. A vanished account is not cash.
        """
        if len(ref) == 32 and _HEX_GUID_RE.fullmatch(ref):
            acct = book.session.query(
                piecash.Account
            ).filter_by(guid=ref).first()
        else:
            acct = self._find_account(book, ref)
        if acct is None or acct.type not in ("BANK", "CASH"):
            return None
        if self._is_in_retirement_subtree(acct):
            return None
        return acct

    def _sx_split_is_cash(self, book, ref: str) -> bool:
        """True when a recipe split's account is spendable cash —
        see ``_sx_split_cash_account``."""
        return self._sx_split_cash_account(book, ref) is not None

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
        Overdue occurrences — due date passed, never entered — lead
        the list with a negative ``days_until``; each schedule
        appears once, at its oldest un-entered date (``_sx_next_due``),
        which is also the date ``create_transaction_from_scheduled``
        posts by default.

        Args:
            days: Look ahead window in days. Default 14.
            compact: If True, return the indicator + compact one-line
                format; otherwise the verbose envelope.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.
        """

        today = date.today()
        # ``days=3285000`` raised OverflowError out of the date
        # arithmetic (IV-18). Ten years ahead is already a forecast.
        if not 0 <= days <= 3660:
            raise ValueError(
                f"days must be between 0 and 3660, got {days}"
            )
        window_end = today + timedelta(days=days)

        with self.open(readonly=True) as book:
            all_sx = book.session.query(
                ScheduledTransaction
            ).all()
            default_mnemonic = self._require_default_currency(book).mnemonic

            upcoming = []
            for sx in all_sx:
                if not sx.enabled:
                    continue

                next_occ = self._sx_next_due(sx)
                if next_occ and next_occ <= window_end:
                    recipe = self._sx_recipe(book, sx)
                    splits = recipe["splits"]

                    # Calculate total amount (sum of positive splits).
                    # _to_decimal is defensive for any older slots whose
                    # JSON may still carry a numeric literal.
                    total = Decimal("0")
                    for s in splits:
                        amt = _to_decimal(s["amount"])
                        if amt > 0:
                            total += amt
                    # Rendered at the template currency's quantum
                    # (15000.00, not 15000): stored amounts carry
                    # whatever precision the caller typed, and the
                    # bill list shouldn't pad by book.
                    sx_cur = recipe["currency"]
                    amount_commodity = (
                        self._find_commodity(book, sx_cur)
                        if sx_cur else None
                    ) or self._require_default_currency(book)
                    total = total.quantize(
                        _commodity_quantum(amount_commodity)
                    )

                    entry = {
                        "guid": sx.guid,
                        "name": sx.name,
                        "occurrence_date": next_occ.isoformat(),
                        "days_until": (next_occ - today).days,
                        "amount": str(total),
                    }
                    # Amounts are denominated in the template's
                    # currency — label foreign ones so the bill
                    # list never reads HKD numbers as book-default.
                    if sx_cur and sx_cur != default_mnemonic:
                        entry["currency"] = sx_cur
                    if not compact:
                        entry["splits"] = self._sx_splits_for_display(
                            book, splits,
                        )
                    upcoming.append(entry)

            # Same-day occurrences by name: the backend's row order
            # is not a sort.
            upcoming.sort(key=lambda x: (x["occurrence_date"], x["name"]))

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
            transaction_date: Defaults to the oldest occurrence not
                yet entered (``_sx_next_due``) — overdue first, the
                same date the dashboard reports.

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

            _label, _start, end, last = self._sx_schedule(sx)
            if not self._sx_recurrences(sx):
                raise ValueError(
                    f"Cannot instantiate '{sx.name}': it has no "
                    f"recurrence rule"
                )

            if transaction_date:
                txn_date = date.fromisoformat(transaction_date)
                # The schedule's own stops bind a date the caller
                # names as much as one the server picks: an explicit
                # date used to post an instance after the schedule
                # had ended (adversarial review 2026-09-30,
                # side-finding 3).
                if sx.num_occur > 0 and sx.rem_occur <= 0:
                    raise ValueError(
                        f"'{sx.name}' has entered all {sx.num_occur} "
                        f"occurrences. delete_scheduled_transaction "
                        f"if it's finished, or enter this one with "
                        f"create_transactions."
                    )
                if end and txn_date > end:
                    raise ValueError(
                        f"Transaction date {txn_date.isoformat()} is "
                        f"after '{sx.name}' ended "
                        f"({end.isoformat()}). Clear the end date with "
                        f"update_scheduled_transaction(end_date=\"\") "
                        f"to resume it, or enter this one with "
                        f"create_transactions."
                    )
            else:
                # Oldest un-entered occurrence — the date the
                # dashboard calls overdue, if one is. Never "the next
                # date after today": that posts a future transaction
                # and strands every missed period behind the guard
                # below.
                txn_date = self._sx_next_due(sx)
                if not txn_date:
                    # The server knows which stop applied; say so,
                    # and say what to do — "past end date, or a
                    # finite schedule..." was the server declining
                    # to read its own row.
                    if sx.num_occur > 0 and sx.rem_occur <= 0:
                        raise ValueError(
                            f"No occurrence due: '{sx.name}' has "
                            f"entered all {sx.num_occur} occurrences. "
                            f"delete_scheduled_transaction if it's "
                            f"finished."
                        )
                    last_note = (
                        f" (last entered {last.isoformat()})"
                        if last else ""
                    )
                    raise ValueError(
                        f"No occurrence due: '{sx.name}' ended "
                        f"{end.isoformat()}{last_note}. Clear the end "
                        f"date with update_scheduled_transaction("
                        f"end_date=\"\") to resume, or "
                        f"delete_scheduled_transaction if it's "
                        f"finished."
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

            recipe = self._sx_recipe(book, sx)
            if recipe["problems"]:
                raise ValueError(
                    f"Cannot instantiate '{sx.name}': "
                    + "; ".join(recipe["problems"])
                )
            splits = [dict(s) for s in recipe["splits"]]
            if not splits:
                raise ValueError(
                    "No split templates found for scheduled "
                    "transaction"
                )
            sx_currency = recipe["currency"]
            txn_currency = (
                self._find_commodity(book, sx_currency)
                if sx_currency else None
            ) or self._require_default_currency(book)
            # A desktop-made cross-commodity leg carries no fixed
            # quantity (GnuCash asks for the rate at Since-Last-Run).
            # Answer the one variable we can: the rate on file at
            # the instance date. No rate → refuse, naming the leg.
            #
            # The rate goes into a stored quantity, so it is chosen
            # the way a POSTING chooses one, not the way a report
            # values a holding: from quotes somebody entered or
            # fetched (never a transaction's own implied rate — an
            # instance priced off the last instance's echo refreshes
            # that echo forever and the stale-price warning goes
            # quiet), and within the staleness window. This read used
            # ``_rates_as_of``, which has no age limit and counts
            # implied and forecast rows: a schedule booked 100 EUR at
            # a 984-day-old rate without a word while post_document
            # on the same book refused (adversarial review
            # 2026-09-30, C18).
            from gnucash_mcp.book._currency import (
                _fx_guard_days,
                _fx_staleness_days,
            )

            rate_notes: list[str] = []
            for s in splits:
                if s.get("quantity") is not None:
                    continue
                acct = self._resolve_account(book, s["account"])
                if acct is None or acct.commodity == txn_currency:
                    continue
                pair = (
                    f"{acct.commodity.mnemonic}/{txn_currency.mnemonic}"
                )
                with self._market_prices_only(book):
                    aged = self._find_exchange_rate_aged(
                        book, acct.commodity, txn_currency, txn_date,
                    )
                if aged is None:
                    cap = _fx_staleness_days()
                    window = f" within {cap} days of" if cap > 0 else " for"
                    raise ValueError(
                        f"Cannot instantiate '{sx.name}': the leg on "
                        f"{acct.fullname} is in {acct.commodity.mnemonic} "
                        f"and no {pair} quote is on file{window} "
                        f"{txn_date.isoformat()}. Add one with "
                        f"create_price(commodity="
                        f"'{acct.commodity.mnemonic}', "
                        f"namespace='{acct.commodity.namespace}', "
                        f"currency='{txn_currency.mnemonic}', "
                        f"value='...', date='{txn_date.isoformat()}'), "
                        f"or run it from GnuCash desktop, which asks "
                        f"for the rate."
                    )
                rate, age_days, price_date = aged
                guard = _fx_guard_days()
                if guard > 0 and age_days > guard:
                    rate_notes.append(
                        f"{acct.fullname}: converted at the {pair} "
                        f"quote of {price_date.isoformat()}, "
                        f"{age_days} days from the transaction date"
                    )
                s["quantity"] = str(
                    (_to_decimal(s["amount"]) / rate).quantize(
                        _commodity_quantum(acct.commodity),
                        rounding=ROUND_HALF_UP,
                    )
                )

            sx_name = sx.name
            sx_description = recipe["description"] or sx.name
            sx_notes = recipe["notes"]

        # ── Phase 2: create the transaction (see docstring). ─────
        txn_result = self.create_transaction(
            description=sx_description,
            splits=splits,
            trans_date=txn_date,
            notes=sx_notes,
            currency=sx_currency,
        )

        # ── Phase 3: advance the schedule. ──────────────────────
        # Re-find by guid — the phase-1 ORM object detached when
        # its session closed.
        shapes: dict = {}
        closed = None
        try:
            with self.open(readonly=False) as book:
                closed = self._read_only_period_note(
                    book, [txn_date], "",
                    dialog="Since Last Run assistant",
                )
                sx = self._find_scheduled_transaction(book, guid)
                if not sx:
                    # SX deleted concurrently between phases — the
                    # transaction exists; respond cleanly rather than
                    # crash. Practically unreachable single-threaded.
                    instance_count = None
                    remaining = None
                else:
                    # Desktop stamps every instance with its schedule;
                    # so do we, by raw SQL — an ORM SlotGUID in the
                    # session arms the delete cascade.
                    if txn_result.get("guid"):
                        created = self._find_transaction(
                            book, txn_result["guid"],
                        )
                        if created is not None:
                            self._slot_insert(
                                book, created.guid, self._SX_FROM,
                                KVP_Type.KVP_TYPE_GUID,
                                f"from-sched-xaction on {created.guid[:8]}",
                                guid_val=sx.guid,
                            )
                    # Every pre-1.5 shape in the book converts on this
                    # write; the other schedules are converted without
                    # being posted.
                    shapes = self._upgrade_book_shapes(book)
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
                        # Finite schedules count down, as GnuCash's
                        # own creation does; at zero _sx_next_due
                        # answers None and the schedule is finished.
                        if sx.num_occur > 0 and sx.rem_occur > 0:
                            sx.rem_occur -= 1
                    book.save()
                    instance_count = sx.instance_count
                    remaining = sx.rem_occur if sx.num_occur > 0 else None
        except Exception as exc:
            # The instance is committed and the schedule is not. Left
            # like that, the transaction carries no schedule stamp,
            # the schedule still calls the period due, and a retry is
            # refused as a duplicate (adversarial review 2026-09-30,
            # C27). Take the instance back out, so a failure here
            # changes nothing and the retry is clean.
            created_guid = txn_result.get("guid")
            if not created_guid or txn_result.get("status") == "rejected":
                raise
            try:
                self.delete_transaction(created_guid, force=True)
            except Exception:
                raise RuntimeError(
                    f"'{sx_name}' was entered as transaction "
                    f"{created_guid}, but the schedule could not be "
                    f"advanced ({type(exc).__name__}: {exc}) and the "
                    f"transaction could not be removed again. It "
                    f"carries no schedule stamp and the schedule "
                    f"still shows this occurrence as due: delete "
                    f"transaction {created_guid}, then retry."
                ) from exc
            raise RuntimeError(
                f"'{sx_name}' could not be advanced "
                f"({type(exc).__name__}: {exc}). The transaction that "
                f"had just been entered for it was removed again, so "
                f"nothing changed. Retry."
            ) from exc

        # ── Build response. ─────────────────────────────────────
        response = {
            "transaction_guid": txn_result.get("guid"),
            "scheduled_transaction": sx_name,
            "description": sx_description,
            "transaction_date": txn_date.isoformat(),
            "instance_count": instance_count,
            "status": txn_result.get("status", "created"),
        }
        if remaining is not None:
            response["remaining_occurrences"] = remaining
        if rate_notes:
            response["warnings"] = rate_notes
        response.update(shapes)
        if closed and response["status"] != "rejected":
            response["read_only_period"] = closed
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
        notes: str | None = None,
        start_date: str | None = None,
    ) -> dict:
        """Update a scheduled transaction.

        Args:
            guid: Scheduled transaction GUID.
            enabled: Enable or disable.
            start_date: ``"YYYY-MM-DD"`` to move the schedule's start.
                What desktop's editor does on OK: the recurrence rows
                take the new date as their period start
                (``gnc_sx_set_schedule``) and the schedule's own
                ``start_date`` follows (``xaccSchedXactionSetStartDate``);
                ``last_occur`` is untouched. The start is the
                PHASE anchor — monthly from the 15th becomes monthly
                from the 3rd for every future occurrence — which is
                the point: it is a mover, not a relabel.
            end_date: ``"YYYY-MM-DD"`` to set, ``""`` to clear,
                ``None`` (default) to leave unchanged. The
                empty-string sentinel exists because ``None``
                already means "no change" and MCP schemas don't
                express three-state strings cleanly.
            notes: Instantiation notes applied to future created
                transactions. Same three-state convention: text to
                set, ``""`` to clear, ``None`` to leave unchanged.
                Does not touch transactions already created.

        Raises:
            ValueError: If not found.
        """
        # Every free-text argument through the one text gate (scoped
        # review 2026-10-05, I-4): no control characters, GnuCash's
        # column width.
        for _field in ("name", "description", "notes", "title", "reference", "fullname", "mnemonic", "memo", "action"):
            _check_text(
                locals().get(_field),
                _SLOT_TEXT_WIDTH if _field == "notes" else _TEXT_WIDTH, _field,
            )
            if _field in ("name", "title", "reference", "fullname", "mnemonic", "action"):
                _check_one_line(locals().get(_field), _field)
        with self.open(readonly=False) as book:
            sx = self._find_scheduled_transaction(book, guid)
            if not sx:
                raise ValueError(
                    f"Scheduled transaction not found: {guid}"
                )

            recipe = self._sx_recipe(book, sx)
            # Audit before-state — without it the log only knows
            # the new state.
            self._stage_audit_before({
                "name": sx.name,
                "enabled": bool(sx.enabled),
                "start_date": (
                    sx.start_date.isoformat() if sx.start_date else None
                ),
                "end_date": (
                    sx.end_date.isoformat() if sx.end_date else None
                ),
                "notes": recipe["notes"],
            })
            # Every pre-1.5 shape in the book converts on this write
            # (a no-change update is the one-call conversion); notes
            # then live on the template transaction, where desktop
            # reads them.
            shapes = self._upgrade_book_shapes(book)
            if shapes.get("templates_migrated"):
                recipe = self._sx_recipe(book, sx)
            notes_owner = sx.guid
            if recipe["source"] == "native":
                notes_owner = book.session.execute(
                    text(
                        "SELECT tx_guid FROM splits WHERE "
                        "account_guid = :a ORDER BY tx_guid LIMIT 1"
                    ),
                    {"a": sx.template_account.guid},
                ).scalar()

            if enabled is not None:
                sx.enabled = _gnc_bool(enabled)

            if end_date is not None:
                if end_date == "":
                    sx.end_date = None
                else:
                    new_end = date.fromisoformat(end_date)
                    current_start = sx.start_date
                    if isinstance(current_start, datetime):
                        current_start = current_start.date()
                    if (
                        start_date is None and current_start is not None
                        and new_end < current_start
                    ):
                        raise ValueError(
                            f"end_date {new_end.isoformat()} is before "
                            f"the schedule's start date "
                            f"{current_start.isoformat()}"
                        )
                    sx.end_date = new_end

            if start_date is not None:
                new_start = date.fromisoformat(start_date)
                if sx.end_date and new_start > sx.end_date:
                    raise ValueError(
                        f"start_date {new_start.isoformat()} is after the "
                        f"schedule's end date {sx.end_date.isoformat()}"
                    )
                # Every recurrence row moves with the start, as the
                # editor rebuilds them from one start date.
                book.session.execute(
                    Recurrence.__table__.update()
                    .where(Recurrence.__table__.c.obj_guid == sx.guid)
                    .values(recurrence_period_start=new_start)
                )
                _verify_composite_write(
                    book.session, Recurrence.__table__,
                    {"obj_guid": sx.guid, "recurrence_period_start": new_start},
                    f"recurrence start for scheduled transaction '{sx.name}'",
                )
                sx.start_date = new_start

            if notes is not None:
                # Upsert as delete-then-insert: the slot table has
                # no unique constraint to UPSERT against, and the
                # polymorphic Slot ORM can't be queried directly.
                book.session.execute(
                    Slot.__table__.delete().where(
                        (Slot.__table__.c.obj_guid == notes_owner)
                        & (Slot.__table__.c.name == "notes")
                    )
                )
                _verify_delete(
                    book.session, Slot.__table__,
                    {"obj_guid": notes_owner, "name": "notes"},
                    f"Notes slot for scheduled transaction "
                    f"'{sx.name}'",
                )
                if notes != "":
                    self._slot_insert(
                        book, notes_owner, "notes",
                        KVP_Type.KVP_TYPE_STRING,
                        f"Notes slot for scheduled transaction "
                        f"'{sx.name}'",
                        string_val=notes,
                    )

            book.save()


            all_sx_guids = [
                row[0]
                for row in book.session.query(ScheduledTransaction.guid).all()
            ]
            short_guid = _unique_prefix(sx.guid, all_sx_guids)
            out = self._sx_to_dict(sx) | {"guid": short_guid}
            out.update(shapes)
            return out

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

            # Every pre-1.5 shape converts on this write too (the
            # schedule being deleted included — it goes right after).
            result.update(self._upgrade_book_shapes(book))

            template_acct = sx.template_account
            sx_guid_full = sx.guid
            book.session.delete(sx)
            book.session.flush()
            # piecash's uselist=False relation cascaded one row; a
            # composite schedule has more. Every row goes.
            book.session.execute(
                Recurrence.__table__.delete().where(
                    Recurrence.__table__.c.obj_guid == sx_guid_full
                )
            )
            _verify_delete(
                book.session, Recurrence.__table__,
                {"obj_guid": sx_guid_full},
                f"recurrence rows of '{result['name']}'",
            )
            if template_acct:
                # The recipe is real Transaction rows on the template
                # account. Strip their GUID/frame slots first: the
                # sched-xaction/account SlotGUID would otherwise
                # cascade the delete into the TARGET account's slots.
                # Then the rows, then the account (or the account
                # delete orphans their splits / fails the FK check).
                recipe_txns = self._strip_template_recipe(
                    book, template_acct,
                    f"recipe of scheduled transaction '{result['name']}'",
                )
                for txn in recipe_txns:
                    book.session.delete(txn)
                book.session.delete(template_acct)

            book.save()

            return result
