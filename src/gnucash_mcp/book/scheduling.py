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

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import uuid

import piecash
from dateutil.relativedelta import relativedelta
from piecash._common import Recurrence
from piecash.core.transaction import ScheduledTransaction
from piecash.kvp import KVP_Type, Slot
from sqlalchemy import text
from sqlalchemy.orm import object_session

from gnucash_mcp.book._base import (
    _HEX_GUID_RE,
    _commodity_quantum,
    _gnc_bool,
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


# ── GnuCash's recurrence engine, ported from Recurrence.cpp ──────
# The occurrence anchor is the RECURRENCE row (period type, mult,
# period start, weekend adjust), never the schedule's start_date and
# never a frequency label: desktop lets "start 9 Sep, monthly on the
# 15th" exist, and a schedule may carry several rows (monthly on the
# 5th AND the 20th). recurrenceNextInstance is ported line for line,
# including the weekend-adjust Friday special case; string tables
# verbatim from period_type_strings / weekend_adj_strings.
_PT_MONTHISH = frozenset(
    {"year", "month", "end of month", "nth weekday", "last weekday"}
)
_PT_WEEKEND_ADJUSTED = frozenset({"year", "month", "end of month"})


def _add_months(d: date, n: int) -> date:
    """g_date_add_months: day clamped to the target month's length."""
    total = d.month - 1 + n
    y, m = d.year + total // 12, total % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def _is_last_of_month(d: date) -> bool:
    return d.day == monthrange(d.year, d.month)[1]


def _nth_weekday_compare(start: date, nxt: date, pt: str) -> int:
    nd, sd = nxt.day, start.day
    week = 3 if sd // 7 > 3 else sd // 7
    if week > 0 and sd % 7 == 0 and sd != 28:
        week -= 1
    matchday = 7 * week + (
        nd - nxt.isoweekday() + start.isoweekday() + 7
    ) % 7
    dim = monthrange(nxt.year, nxt.month)[1]
    if (dim - matchday) >= 7 and pt == "last weekday":
        matchday += 7
    if pt == "nth weekday" and matchday % 7 == 0:
        matchday += 7
    return matchday - nd


def _adjust_for_weekend(pt: str, wadj: str, d: date) -> date:
    if pt in _PT_WEEKEND_ADJUSTED and d.isoweekday() in (6, 7):
        sat = d.isoweekday() == 6
        if wadj == "back":
            return d - timedelta(days=1 if sat else 2)
        if wadj == "forward":
            return d + timedelta(days=2 if sat else 1)
    return d


def _recurrence_next(
    pt: str, mult: int, start: date, wadj: str, ref: date,
) -> date | None:
    """First occurrence strictly after ``ref``; None when the
    recurrence yields nothing (``once`` already past, unknown type)."""
    mult = max(int(mult or 1), 1)
    adjusted_start = _adjust_for_weekend(pt, wadj, start)
    if ref < adjusted_start:
        return adjusted_start
    nxt = ref
    if pt == "once":
        return None
    if pt in _PT_MONTHISH:
        m = mult * 12 if pt == "year" else mult
        # Step 1: forward one period, passing exactly one occurrence.
        if (wadj == "back" and pt in _PT_WEEKEND_ADJUSTED
                and nxt.isoweekday() in (6, 7)):
            nxt -= timedelta(days=1 if nxt.isoweekday() == 6 else 2)
        if (wadj == "back" and pt in _PT_WEEKEND_ADJUSTED
                and nxt.isoweekday() == 5):
            tmp_sat, tmp_sun = nxt + timedelta(days=1), nxt + timedelta(days=2)
            if pt == "end of month":
                if (_is_last_of_month(nxt) or _is_last_of_month(tmp_sat)
                        or _is_last_of_month(tmp_sun)):
                    nxt = _add_months(nxt, m)
                else:
                    nxt = _add_months(nxt, m - 1)
            else:
                if tmp_sat.day == start.day:
                    nxt = _add_months(tmp_sat, m)
                elif tmp_sun.day == start.day:
                    nxt = _add_months(tmp_sun, m)
                elif nxt.day >= start.day:
                    nxt = _add_months(nxt, m)
                elif _is_last_of_month(nxt):
                    nxt = _add_months(nxt, m)
                elif _is_last_of_month(tmp_sat):
                    nxt = _add_months(tmp_sat, m)
                elif _is_last_of_month(tmp_sun):
                    nxt = _add_months(tmp_sun, m)
                else:
                    nxt = _add_months(nxt, m - 1)
        elif (_is_last_of_month(nxt)
              or (pt in ("month", "year") and nxt.day >= start.day)
              or (pt in ("nth weekday", "last weekday")
                  and _nth_weekday_compare(start, nxt, pt) <= 0)):
            nxt = _add_months(nxt, m)
        else:
            nxt = _add_months(nxt, m - 1)
        # Step 2: back up to the base phase, then align the day.
        n_months = 12 * (nxt.year - start.year) + (nxt.month - start.month)
        nxt = _add_months(nxt, -(n_months % m))
        dim = monthrange(nxt.year, nxt.month)[1]
        if pt in ("nth weekday", "last weekday"):
            nxt += timedelta(days=_nth_weekday_compare(start, nxt, pt))
        elif pt == "end of month" or start.day >= dim:
            nxt = nxt.replace(day=dim)
        else:
            nxt = nxt.replace(day=start.day)
        return _adjust_for_weekend(pt, wadj, nxt)
    if pt in ("week", "day"):
        step = mult * 7 if pt == "week" else mult
        nxt = nxt + timedelta(days=step)
        return nxt - timedelta(days=(nxt - start).days % step)
    return None


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
        ref = last if last is not None else start - timedelta(days=1)
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
                piecash.Split(
                    account=template_acct,
                    value=Decimal("0"), quantity=Decimal("0"),
                    memo=leg.get("memo") or "",
                    action=leg.get("action") or "",
                )
                for leg in legs
            ],
        )
        book.session.flush()
        denom = currency.fraction
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
                self._slot_insert(
                    book, frame_guid, f"{self._SX_FRAME}/{side}-numeric",
                    KVP_Type.KVP_TYPE_NUMERIC, label,
                    numeric_val_num=int(val * denom),
                    numeric_val_denom=denom,
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
                    numeric_val_num=int(q * q_denom),
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
    def _slot_amount(children, numeric_key, formula_key):
        """Debit or credit side of a template split: the numeric when
        present and non-zero (what Since-Last-Run prefers), else the
        formula parsed as a plain number, else None (a formula with
        variables — GnuCash prompts; we refuse)."""
        num = children.get(numeric_key)
        if num and num[3] is not None and num[4] and num[3] != 0:
            value = Decimal(num[3]) / Decimal(num[4])
            # 4250/100 is 42.50, not 42.5 — keep the fraction's
            # precision so amounts round-trip as typed.
            return value.quantize(Decimal(1) / Decimal(num[4]))
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
                )
                credit = self._slot_amount(
                    ch, self._SX_CREDIT_NUMERIC, self._SX_CREDIT_FORMULA,
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
                    leg["quantity"] = str(
                        (Decimal(q[3]) / Decimal(q[4])).quantize(
                            Decimal(1) / Decimal(q[4])
                        )
                    )
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

    def _upcoming_within_days(
        self, book, days: int = 7,
    ) -> dict:
        """Summary stats for scheduled transactions due within
        ``days`` days: ``{"count": int, "total": Decimal,
        "unrated": int}``.

        Total = sum of positive split amounts per occurrence (same
        convention as ``get_upcoming_transactions``), in the BOOK
        DEFAULT currency: foreign-currency templates convert at the
        latest market rate; templates whose currency has no rate on
        file are counted but excluded from the total (``unrated``
        reports how many, so the summary line can say so instead of
        silently understating). Feeds the get_book_summary Scheduled
        line; lives here so a book class built without scheduling
        lacks the method and the summary skips the line via
        ``hasattr``.
        """

        today = date.today()
        window_end = today + timedelta(days=days)

        default_currency = self._require_default_currency(book)
        rates = self._rates_as_of(book, today, default_currency)

        count = 0
        total = Decimal("0")
        unrated = 0
        legacy = 0
        for sx in book.session.query(ScheduledTransaction).all():
            # Recipes still in the pre-native shape are invisible
            # to desktop until their first write migrates them.
            if self._sx_recipe(book, sx)["source"] == "legacy":
                legacy += 1
            if not sx.enabled:
                continue

            # An overdue occurrence belongs to the dashboard's
            # overdue bucket (same _sx_next_due), not to "due in
            # next N days" — counting it here too would double it.
            next_occ = self._sx_next_due(sx)
            if not next_occ or next_occ < today or next_occ > window_end:
                continue

            count += 1
            # splits-json amounts are denominated in the template's
            # currency (the ``currency`` slot; absent = book
            # default). Foreign templates convert at the latest
            # market rate; no rate on file → counted, excluded from
            # the total, reported via ``unrated``.
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
            for s in recipe["splits"]:
                amt = _to_decimal(s["amount"])
                if amt > 0:
                    total += amt * rate
        return {
            "count": count, "total": total, "unrated": unrated,
            "legacy": legacy,
        }

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
            rates = None
            for s in splits:
                if s.get("quantity") is not None:
                    continue
                acct = self._resolve_account(book, s["account"])
                if acct is None or acct.commodity == txn_currency:
                    continue
                if rates is None:
                    rates = self._rates_as_of(book, txn_date, txn_currency)
                rate = rates.get(acct.commodity.guid)
                if not rate:
                    raise ValueError(
                        f"Cannot instantiate '{sx.name}': the leg on "
                        f"{acct.fullname} is in {acct.commodity.mnemonic} "
                        f"and no {acct.commodity.mnemonic}/"
                        f"{txn_currency.mnemonic} rate is on file for "
                        f"{txn_date.isoformat()}. create_price, or run "
                        f"it from GnuCash desktop."
                    )
                s["quantity"] = str(
                    (_to_decimal(s["amount"]) / rate).quantize(
                        _commodity_quantum(acct.commodity)
                    )
                )

            sx_name = sx.name
            sx_description = recipe["description"] or sx.name
            sx_notes = recipe["notes"]
            recipe_source = recipe["source"]

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
        template_migrated = False
        with self.open(readonly=False) as book:
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
                # A legacy recipe becomes native on the first write
                # that touches its schedule.
                if recipe_source == "legacy":
                    template_migrated = self._migrate_sx_recipe(
                        book, sx, self._sx_recipe(book, sx),
                    )
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
        if template_migrated:
            response["template_migrated"] = True
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
            notes: Instantiation notes applied to future created
                transactions. Same three-state convention: text to
                set, ``""`` to clear, ``None`` to leave unchanged.
                Does not touch transactions already created.

        Raises:
            ValueError: If not found.
        """
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
                "end_date": (
                    sx.end_date.isoformat() if sx.end_date else None
                ),
                "notes": recipe["notes"],
            })
            # A legacy recipe becomes native on the first write that
            # touches its schedule; notes then live on the template
            # transaction, where desktop reads them.
            template_migrated = self._migrate_sx_recipe(book, sx, recipe)
            if template_migrated:
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
                    sx.end_date = date.fromisoformat(end_date)

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
            if template_migrated:
                out["template_migrated"] = True
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
