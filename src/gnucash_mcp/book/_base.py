"""Base class and shared helpers for GnuCashBook.

BaseGnuCashBook holds the helpers every module needs: the open()
context manager with lock retries, GUID resolution via read-only
SQLite, the universal finders (account / transaction / split),
default-currency access, and the serializers that convert piecash
objects to plain dicts and compact text lines.

Module-specific finders and dict converters (lots, budgets,
commodities, customers, vendors, invoices) live in the module they
belong to — not here.
"""

import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Generator, Iterable
from urllib.parse import quote

import piecash
from piecash._common import GnucashException
from sqlalchemy import exc as sa_exc

# Re-exported for callers that still import these from ``_base``.
# Canonical definitions live in ``_currency`` alongside the
# CurrencyMixin that uses them; keeping the import path stable avoids
# churn across the codebase.
from gnucash_mcp.book._currency import (  # noqa: F401
    CurrencyMixin,
    _to_date,
)
# Imported for its side effect: replaces piecash's Price and Split
# validators with ones that write GnuCash desktop's shapes. Must
# hold whichever modules are enabled, hence here.
from gnucash_mcp.book import _piecash_shapes  # noqa: F401,E402
from gnucash_mcp.book._query import QueryMixin
from gnucash_mcp._format import (
    _book_display_name,
    _one_line,
    _parse_book_url,
    _tsv_cell,
)

# GnuCash stores GUIDs as lowercase hex (via uuid4().hex). We accept both
# cases on input for ergonomics — users pasting from external tools may
# have uppercase — and normalize to lowercase before hitting SQLite
# (which is case-sensitive on LIKE by default).
_HEX_GUID_RE = re.compile(r"^[0-9a-fA-F]+$")

# Debug logger - configured by logging_config.setup_logging()
debug_logger = logging.getLogger("gnucash_mcp.debug")


# ── Module-level helpers ───────────────────────────────────────────


def _slot_value_str(value) -> str:
    """Stringify a piecash slot value to a stable ``str``.

    piecash returns either a typed wrapper with a ``.value``
    attribute (``SlotString``, ``SlotInt64``, etc.) or the raw
    value depending on slot type. Lives in ``_base`` because both
    AdminMixin and BusinessMixin consume slots, and a sideways
    import between them wouldn't reflect the dependency direction.
    """
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


# ── Budget sign convention ──────────────────────────────────────────
# GnuCash 3.8+ stores budget amounts in the account's NATURAL sign
# and stamps the book with a feature to say so: a 5,000 income target
# is -5000 on disk, and the budget editor negates on entry and on
# display for credit-normal types (gnc_reverse_balance under the
# default "credit accounts" preference). A book without the stamp
# gets a one-time heuristic scrub the next time GnuCash opens it
# (libgnucash/engine/ScrubBudget.c). This server's surface is
# magnitudes — what a default-preference user types and sees — and
# the sign is applied at the storage boundary in exactly one place:
# _budget_targets reads, _budget_stored_sign writes. Before this,
# every type was stored as a magnitude, so an income target read as
# "-5,000 / 0%" after GnuCash scrubbed it, and displayed as -5,000
# in GnuCash when we wrote it.
# The KEY is GNC_FEATURE_BUDGET_UNREVERSED from gnc-features.h,
# verbatim; GnuCash looks features up by key and refuses to open a
# book carrying one it doesn't know ("features not supported by this
# version"). The description is what it stores as the value. A test
# pins the key against a copy of the #define.
_BUDGET_UNREVERSED_FEATURE = "Use natural signs in budget amounts"
_BUDGET_UNREVERSED_KEY = f"features/{_BUDGET_UNREVERSED_FEATURE}"
# Written by this branch before merge under a key GnuCash did not
# recognize (the bookkeeper's production book would not open).
# Never released; migrated away on the next budget write.
_BUDGET_UNREVERSED_BOGUS_KEY = "features/Budgets: sign reversal fixed"
_BUDGET_UNREVERSED_DESCRIPTION = (
    "Store budget amounts unreversed (i.e. natural) signs "
    "(requires at least Gnucash 3.8)"
)
# gnc_reverse_balance's set under the default preference.
_BUDGET_CREDIT_NORMAL_TYPES = frozenset(
    {"INCOME", "LIABILITY", "PAYABLE", "EQUITY", "CREDIT"}
)
# ScrubBudget.c's three policies and what each flips. Deliberately
# narrower than the display set (PAYABLE / CREDIT rows are left
# alone) — mirrored exactly, so a book scrubbed here lands in the
# state GnuCash's own open-time scrub would have produced.
_BUDGET_SCRUB_FLIP = {
    "INC_EXP": frozenset({"INCOME", "EXPENSE"}),
    "CREDIT_ACC": frozenset({"LIABILITY", "EQUITY", "INCOME"}),
    "NONE": frozenset(),
}


# Lives here, not in scheduling.py, because budget period
# boundaries read it too (spec B5) and the budgets module must
# work without scheduling loaded.
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



def _day_end(d: date) -> datetime:
    """``gnc_time64_get_day_end``: 23:59:59 local on the date, what
    desktop stores for a reconciled split's reconcile_date and the
    account's reconcile-info last-date (twin, 2026-09-29)."""
    return datetime.combine(d, datetime.max.time()).replace(microsecond=0).astimezone()


def _neutral_time(d: date) -> datetime:
    """GnuCash's neutral time of day for a date-valued timestamp
    (``gnc_time64_get_day_neutral``), the convention behind
    ``transactions.post_date`` and, on desktop, a document's
    ``date_opened`` / ``date_posted``. Timezone-aware so piecash's
    local→UTC conversion is a no-op.

    10:59:00 UTC, which is the same calendar day in every zone from
    UTC-10 to UTC+13 — and ported with the adjustment GnuCash makes
    outside that band (gnc-datetime.cpp, ``LDT_from_date_daypart``):

        auto offset = lt.local_time() - lt.utc_time();
        if (offset < hours(-10)) lt -= hours(offset.hours() + 10);
        if (offset > hours(13))  lt += hours(13 - offset.hours());

    so the stamp still reads as the intended day locally: 11:59 UTC
    in Pago Pago (UTC-11), 09:59 UTC on Kiritimati (UTC+14). A flat
    10:59 there dated an invoice a day early and rewrote desktop's
    own rows (adversarial review 2026-09-30, C25)."""
    from datetime import timedelta, timezone

    stamp = datetime(d.year, d.month, d.day, 10, 59, 0, tzinfo=timezone.utc)
    offset = stamp.astimezone().utcoffset() or timedelta(0)
    # boost's time_duration::hours() truncates toward zero.
    hours = int(offset.total_seconds() / 3600)
    if offset < timedelta(hours=-10):
        stamp -= timedelta(hours=hours + 10)
    if offset > timedelta(hours=13):
        stamp += timedelta(hours=13 - hours)
    return stamp


def _future_statement_warning(statement_date: date) -> str | None:
    """A statement is not dated in the future; one that is, is a
    typo. The reconcile goes through (desktop accepts the date too)
    and the response says so — the one sentence ``reconcile_account``
    and ``enter_statement`` both use (maintainer ruling, 2026-09-29).
    ``None`` for today or earlier."""
    today = date.today()
    if statement_date <= today:
        return None
    return (
        f"statement_date {statement_date.isoformat()} is after today "
        f"({today.isoformat()}) — a statement is not dated in the "
        f"future; check the transcription"
    )


def _budget_period_bounds(budget) -> "list[tuple[date, date]] | None":
    """``[(start, end)]`` for every period of a budget, from its
    recurrence row through ``_recurrence_next`` — the Recurrence.cpp
    port — so every GnuCash period type (month, week, day, year,
    end of month, nth/last weekday, with mult) paces the same way
    desktop lays the columns out. ``None`` when the port yields
    nothing for the type; callers say so rather than omit the
    budget silently (spec B5).
    """
    rec = budget.recurrence
    start = rec.recurrence_period_start
    if isinstance(start, datetime):
        start = start.date()
    pt = rec.recurrence_period_type
    mult = rec.recurrence_mult or 1
    wadj = getattr(rec, "recurrence_weekend_adjust", None) or "none"
    bounds = []
    cursor = start
    for _ in range(budget.num_periods):
        nxt = _recurrence_next(pt, mult, start, wadj, cursor)
        if nxt is None or nxt <= cursor:
            return None
        bounds.append((cursor, nxt - timedelta(days=1)))
        cursor = nxt
    return bounds


def _budget_stored_sign(account) -> int:
    """+1 or -1: the factor between a user-facing magnitude and the
    on-disk amount for this account, per GnuCash's natural-sign
    storage."""
    return -1 if account.type in _BUDGET_CREDIT_NORMAL_TYPES else 1


def _budget_unreversed(book) -> bool:
    """Has this book been stamped with GnuCash's natural-sign
    budget feature? piecash raises KeyError on an absent slot path."""
    try:
        return book[_BUDGET_UNREVERSED_KEY] is not None
    except KeyError:
        return False


def _budget_scrub_policy(budget) -> str:
    """Port of ScrubBudget.c heuristics_on_budget: which rows an
    un-stamped budget's signs imply need flipping. Per account, the
    sign of its total across set periods (-1 / 0 / +1) is tallied
    by type; any negative expense total means the book was kept
    under the "income & expense" reversal, a negative income total
    means it is already natural, anything else means the default
    "credit accounts" reversal."""
    totals: dict[str, list] = {}
    for ba in budget.amounts:
        entry = totals.setdefault(
            ba.account.guid, [ba.account.type, Decimal("0")],
        )
        entry[1] += Decimal(str(ba.amount))
    tally = {"EXPENSE": 0, "INCOME": 0}
    for acct_type, total in totals.values():
        if acct_type in tally:
            tally[acct_type] += (total > 0) - (total < 0)
    if tally["EXPENSE"] < 0:
        return "INC_EXP"
    if tally["INCOME"] < 0:
        return "NONE"
    return "CREDIT_ACC"


def _budget_targets(book, budget) -> list[tuple[object, Decimal]]:
    """THE reader for budget amounts: ``[(BudgetAmount, magnitude)]``
    in the surface convention, whatever the book's storage state.
    A stamped book is read as natural sign; an un-stamped one is
    read through the same heuristic GnuCash will apply when it
    opens the book, so the numbers don't change underneath the user
    at that moment. Readers never write — the scrub itself happens
    on the write path (``_ensure_budget_unreversed``)."""
    flip = (
        frozenset() if _budget_unreversed(book)
        else _BUDGET_SCRUB_FLIP[_budget_scrub_policy(budget)]
    )
    out = []
    for ba in budget.amounts:
        natural = Decimal(str(ba.amount))
        if ba.account.type in flip:
            natural = -natural
        out.append((ba, natural * _budget_stored_sign(ba.account)))
    return out


def _commodity_quantum(commodity) -> Decimal:
    """Smallest representable unit of a commodity, as a Decimal quantum.

    Derived from ``commodity.fraction`` (piecash stores 100 for USD,
    1 for JPY, 1000 for BHD, 10000 for shares). A hardcoded
    ``Decimal("0.01")`` would silently corrupt non-2-decimal
    currencies — half-yen amounts on JPY, lost mils on BHD/KWD.
    """
    fraction = getattr(commodity, "fraction", 100)
    if fraction <= 1:
        return Decimal(1)
    return Decimal(1) / Decimal(fraction)


def _account_unit(account) -> Decimal:
    """An account's smallest quantity, as a Decimal quantum.

    ``xaccAccountGetCommoditySCU``, ported: the account's own
    ``commodity_scu`` only when it is marked non-standard (or has no
    commodity), otherwise its commodity's fraction.
    """
    if account.non_std_scu or account.commodity is None:
        scu = account.commodity_scu or 1
        return Decimal(1) if scu <= 1 else Decimal(1) / Decimal(scu)
    return _commodity_quantum(account.commodity)


def _split_amounts(value, quantity, currency, account) -> tuple:
    """A split's value and quantity the way GnuCash stores them.

    ``xaccSplitSetValue`` converts the value to the transaction
    currency's fraction and ``xaccSplitSetAmount`` the quantity to the
    account's unit, both rounding half up. Every split this server
    writes goes through here: piecash stores a Decimal over its own
    exponent, so "12" became 12/1 and "12.345" dollars 12345/1000 — a
    sub-cent amount desktop can never hold. Rounding here covers
    amounts the server computes (FX, tax) and share quantities; money
    typed in finer than its unit is refused earlier, by
    ``_money_precision_error`` in the validator.
    """
    return (
        Decimal(value).quantize(_commodity_quantum(currency), ROUND_HALF_UP),
        Decimal(quantity).quantize(_account_unit(account), ROUND_HALF_UP),
    )


# GnuCash stores an unreconciled split's reconcile_date as time64 0.
_EPOCH = datetime(1970, 1, 1, tzinfo=__import__("datetime").timezone.utc)


def _new_split(account, value, quantity, currency, **fields):
    """The one constructor of a ``piecash.Split``: amounts through
    ``_split_amounts`` first. ``currency`` is the transaction's, which
    may not exist yet when the split is built. Locked by
    ``test_amount_precision.py``: no ``piecash.Split(`` elsewhere."""
    value, quantity = _split_amounts(value, quantity, currency, account)
    # An unreconciled split's reconcile_date is the epoch on desktop
    # (time64 0), never NULL — the one column a plain transaction
    # twin found different (2026-09-29).
    fields.setdefault("reconcile_date", _EPOCH)
    return piecash.Split(
        account=account, value=value, quantity=quantity, **fields,
    )


def _set_split_amounts(split, value, quantity) -> None:
    """The one writer of an existing split's amounts — GnuCash's
    ``xaccSplitSetValue`` / ``xaccSplitSetAmount`` together: round
    through ``_split_amounts``, then ``mark_split``'s lot reset
    (``_lot_forget_flag``), since the lot's cached answer described
    the old amounts."""
    split.value, split.quantity = _split_amounts(
        value, quantity, split.transaction.currency, split.account,
    )
    _lot_forget_flag(split.lot)


def _money_precision_error(amount, commodity, what: str) -> ValueError | None:
    """The refusal for money typed finer than its currency's unit
    (maintainer ruling, 2026-09-27): "12.345" dollars is a typo to
    catch, not a value to round. None when the amount fits."""
    quantum = _commodity_quantum(commodity)
    if amount == amount.quantize(quantum, ROUND_HALF_UP):
        return None
    places = max(-quantum.as_tuple().exponent, 0)
    return ValueError(
        f"{what}: {amount} carries finer precision than "
        f"{commodity.mnemonic} allows ({places} decimals) — re-check "
        f"the transcription"
    )


def _all_slot_columns():
    """Loader target that fetches every Slot subclass column in one
    query. piecash's Slot is single-table polymorphic (``SlotString``,
    ``SlotInt64``, ... share the ``slots`` table); a plain relationship
    load fetches only the base columns and defers each row's typed
    value (``string_val``, ``int64_val``) to a SELECT on first read,
    one per slot. Used by both preload helpers so a slot-backed
    property such as ``txn.notes`` resolves from memory after the
    bulk load."""
    from piecash.kvp import Slot
    from sqlalchemy.orm import with_polymorphic

    return with_polymorphic(Slot, "*")


def _is_voided(split) -> bool:
    """True iff ``split`` carries GnuCash's voided marker.

    GnuCash's void preserves the split for audit trail —
    ``reconcile_state="v"``, value/quantity zeroed. Balance sums,
    unreconciled counts, and lot validation must filter these
    zombies out; this predicate is the single source of truth
    (ad-hoc per-site checks drift).

    Intentionally state-only, covering both corruption directions:
    ``state="v"`` with non-zero values (partial void) still reads
    as voided; ``value=0`` with another state is NOT voided
    (legitimate zero-value splits exist).
    """
    return split.reconcile_state == "v"


def _is_unreconciled(split) -> bool:
    """True iff ``split`` counts as pending reconciliation work.

    The shared predicate behind ``get_unreconciled_splits`` (detail
    tool) and ``_account_reconciliation_status`` (dashboard count)
    — chokepointed so the two surfaces agree by construction.

    ``"n"`` (new) and ``"c"`` (cleared) both count — cleared is the
    bookkeeper's tentative state before a final ``"y"``. Reconciled
    and voided splits are excluded.

    Scope: this is the **as-of-today** predicate. The detail tool's
    ``as_of_date`` filter is a separate scoping concern layered on
    top, not a reason to thread a date into the chokepoint; the
    dashboard intentionally has no historical-tie-out parameter.
    """
    return split.reconcile_state != "y" and not _is_voided(split)


def _looks_like_guid_ref(value) -> bool:
    """True iff ``value`` is a string worth resolving via
    ``_resolve_account`` — short GUID (``%xxxxxxx``) or 32-char hex
    full GUID. Paths are already canonical and skip the resolve.
    Module-level so display surfaces can check before opening a
    session.
    """
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("%"):
        return True
    if len(value) == 32:
        try:
            int(value, 16)
            return True
        except ValueError:
            return False
    return False


def _gnc_bool(value) -> int:
    """Render a Python truth value as GnuCash stores it: an integer.

    GnuCash's schema types every flag column — ``accounts.placeholder``
    and ``.hidden``, ``schedxactions.enabled``, ``lots.is_closed`` — as
    INTEGER, never BOOLEAN. SQLite has no boolean type and silently
    coerces, so passing a Python ``bool`` worked by accident for as
    long as SQLite was the only backend. PostgreSQL is strictly typed
    and rejects it:

        DatatypeMismatch: column "placeholder" is of type integer
        but expression is of type boolean

    Every write to one of those columns goes through here, so the
    coercion can't be remembered at some sites and forgotten at
    others (locked by ``TestBackendPortabilityChokepoints``).
    """
    return 1 if value else 0


# libpq's PQTRANS_INERROR. psycopg2 exposes it as
# ``extensions.TRANSACTION_STATUS_INERROR`` and psycopg 3 as
# ``pq.TransactionStatus.INERROR``; both are this value.
_PG_TRANSACTION_INERROR = 3


def _dialect_name(book) -> str:
    """The SQLAlchemy dialect serving an open book: ``sqlite``,
    ``postgresql``, or ``mysql``.

    The one question a raw-SQL site may ask before emitting a
    statement only one backend accepts. ``BookSource.backend`` answers
    it from the connection string before a book is open; this reads
    it off the live session, for the static helpers that receive a
    piecash Book and nothing else.
    """
    return book.session.get_bind().dialect.name


def _rollback_if_aborted(session) -> bool:
    """Clear an aborted PostgreSQL transaction after a swallowed error.

    PostgreSQL marks the transaction aborted after any failed
    statement and answers everything that follows with
    ``InFailedSqlTransaction`` — so a best-effort block that catches
    its own error and moves on hands the NEXT statement a failure
    naming the wrong culprit (that is how ``_find_invoice``'s
    self-heal hid behind the SELECT below it). SQLite and MySQL have
    no such state; a failed statement there leaves the connection
    usable, and this returns False without touching the session.

    Rolls back only when the driver reports the aborted state, so a
    block that failed harmlessly (a readonly session refusing to
    flush) keeps whatever the caller has pending. Never raises: it
    runs inside ``except`` blocks, where its own failure would
    replace the original. Returns True when it rolled back.

    The driver connection is read through the pool proxy's
    ``dbapi_connection`` — the proxy's own ``.info`` is SQLAlchemy's
    per-connection dict, not psycopg2's status object.
    """
    try:
        proxy = session.connection().connection
        raw = getattr(proxy, "dbapi_connection", None)
        if raw is None:
            raw = proxy.connection
        status = raw.info.transaction_status
    except Exception:
        return False
    if status != _PG_TRANSACTION_INERROR:
        return False
    try:
        session.rollback()
    except Exception:
        return False
    return True


# GnuCash's own column widths for free text (the SQL backend's
# CT_STRING lengths: gnc-transaction-sql.cpp, gnc-account-sql.cpp,
# gnc-tax-table-sql.cpp, gnc-slots-sql.cpp). SQLite ignores them;
# PostgreSQL and MySQL enforce them, so a longer value was a raw
# DataError there and an unbounded one — a 5 MB account name was
# accepted — everywhere else.
_TEXT_WIDTH = 2048
_SLOT_TEXT_WIDTH = 4096
_TAXTABLE_NAME_WIDTH = 50


def _check_text(value, width: int, what: str) -> None:
    """Refuse text GnuCash's schema cannot hold: longer than the
    column, or carrying a NUL (PostgreSQL rejects one outright, and
    SQLite stores the string but shows it cut off at the NUL).
    ``None`` and non-strings pass. Adversarial review 2026-09-30,
    C65 / IV-19 / IV-20."""
    if not isinstance(value, str):
        return
    if "\x00" in value:
        raise ValueError(f"{what} contains a NUL character")
    if len(value) > width:
        raise ValueError(
            f"{what} is {len(value)} characters; GnuCash stores at "
            f"most {width}"
        )


_PLAIN_NUMBER = re.compile(
    r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)


def _to_decimal(value) -> Decimal:
    """Safe Decimal construction for user-supplied monetary values.

    Routes through ``Decimal(str(value))`` so a float that slipped
    past the pydantic boundary decimalizes via shortest-repr
    (``str(94.87) == "94.87"``) instead of embedding the IEEE-754
    epsilon and breaking the sum-to-zero check. Exact inputs
    (str/int/Decimal) round-trip unchanged.

    Use everywhere user-supplied money hits ``Decimal(...)`` —
    direct callers (tests, scripts) bypass the pydantic coercion.

    Raises ``ValueError`` — never ``decimal.InvalidOperation`` — on
    text that isn't a number, and on NaN / infinity. InvalidOperation
    is an ``ArithmeticError``, so it slipped past every ``except
    ValueError`` on the write paths: one ``$10`` cell sank a whole
    ``create_transactions`` batch as ``unexpected_error`` (and
    ``on_error="skip"`` could not rescue it) while ``create_prices``
    happened to catch ``ArithmeticError`` and rejected the same cell
    per row (whole-tree review, 2026-09-04, class 2). Converting here
    retires the class at every caller and lets ``safe_tool`` report
    it as the ``validation_error`` it is.
    """
    if isinstance(value, Decimal):
        d = value
        if not d.is_finite():
            raise ValueError(
                f"amount must be a finite number, got {value!r}"
            )
        return d
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(
            f"not a valid decimal amount: {value!r}"
        ) from None
    if not d.is_finite():
        raise ValueError(f"amount must be a finite number, got {value!r}")
    # Python's Decimal reads more than a ledger should accept:
    # ``5_000`` (underscores), ``٥`` and ``５`` (any Unicode digit).
    # An amount is plain ASCII digits; anything else is a paste
    # artifact to look at, not to guess through (adversarial review
    # 2026-09-30, IV-27).
    if isinstance(value, str) and not _PLAIN_NUMBER.fullmatch(value.strip()):
        raise ValueError(
            f"not a valid decimal amount: {value!r} (use plain digits "
            f"and a decimal point, e.g. 1234.56)"
        )
    # GnuCash stores an amount as a 64-bit numerator over a power of
    # ten. Beyond that range piecash raised mid-save — OverflowError,
    # or a 40-second hang building 10**1000000 — past every per-row
    # handler (C39, BL-24, C58).
    if d != 0 and d.adjusted() >= 17:
        raise ValueError(
            f"amount {value!r} is too large to store (the limit is "
            f"below 10^17)"
        )
    if d.as_tuple().exponent < -18:
        raise ValueError(
            f"amount {value!r} has more decimal places than can be "
            f"stored (18 at most)"
        )
    return d


def _verify_write(session, table, guid: str, label: str) -> None:
    """Verify a raw SQL INSERT persisted by reading back the primary key.

    Must be called within the same session, before book.save().
    Raises RuntimeError if the inserted row cannot be found.
    """
    from sqlalchemy import select, func

    count = session.execute(
        select(func.count()).select_from(table).where(table.c.guid == guid)
    ).scalar()
    if count != 1:
        debug_logger.error(
            f"Write verification FAILED: {label} guid={guid} count={count}"
        )
        raise RuntimeError(
            f"Write verification failed: {label} with guid "
            f"{guid} not found after INSERT (count={count})"
        )


def _verify_composite_write(
    session, table, conditions: dict, label: str
) -> None:
    """Verify a raw SQL INSERT for a table with composite primary key.

    Must be called within the same session, before book.save().
    Raises RuntimeError if the inserted row cannot be found.
    """
    from sqlalchemy import select, func, and_

    where_clause = and_(
        *(table.c[col] == val for col, val in conditions.items())
    )
    count = session.execute(
        select(func.count()).select_from(table).where(where_clause)
    ).scalar()
    if count != 1:
        debug_logger.error(
            f"Write verification FAILED: {label} conditions={conditions} "
            f"count={count}"
        )
        raise RuntimeError(
            f"Write verification failed: {label} not found "
            f"after INSERT (count={count})"
        )


def _verify_none_remaining(left: int, label: str) -> None:
    """Verification for a bulk UPDATE: the count of rows still in the
    old state must be zero afterwards."""
    if left:
        raise RuntimeError(f"{label}: {left} row(s) still in the old state")


def _verify_delete(
    session, table, conditions: dict, label: str
) -> None:
    """Verify a SQL DELETE removed the expected row(s).

    Must be called within the same session, before book.save().
    Raises RuntimeError if matching rows still exist.

    Shape-matches ``_verify_composite_write``: the ``table`` argument
    is a SQLAlchemy Core Table (``Entity.__table__``), so the helper
    pairs deletes-with-verification for any table — slots, Entry,
    Invoice, Customer, and Vendor cleanup alike.
    """
    from sqlalchemy import select, func, and_

    where_clause = and_(
        *(table.c[col] == val for col, val in conditions.items())
    )
    count = session.execute(
        select(func.count()).select_from(table).where(where_clause)
    ).scalar()
    if count != 0:
        debug_logger.error(
            f"Delete verification FAILED: {label} still has {count} rows"
        )
        raise RuntimeError(
            f"Delete verification failed: {label} still exists "
            f"after DELETE ({count} rows remain)"
        )


# ── GUID prefix protection ────────────────────────────────────────
#
# Compact formatters emit GUID prefixes the LLM feeds back through
# _resolve_guid. Blanket `guid[:8]` truncation is unsafe at scale —
# the birthday problem puts collisions at ~1-2% by ~10k entries, and
# a colliding prefix fails the ambiguity check on the next reference.
# _guid_prefix_map gives each GUID its shortest unique prefix (≥ 8;
# only collisions extend further).
#
# Callers must pass the FULL relevant table, not the filtered batch —
# emitted prefixes must be unambiguous against _resolve_guid's
# table-wide LIKE search, not just within the current response.


def _lcp_length(a: str, b: str) -> int:
    """Length of the longest common prefix between two strings."""
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def _guid_prefix_map(
    guids: Iterable[str], min_len: int = 8
) -> dict[str, str]:
    """Map each GUID to its shortest prefix (>= ``min_len``) that is
    unique within the set; colliding GUIDs extend until they diverge.

    One sort + one linear pass: the minimum unique prefix length is
    max(LCP with either sorted neighbor) + 1, clamped to
    [min_len, len(guid)]. ``min_len`` defaults to 8, matching
    ``_resolve_guid``'s minimum input length.
    """
    unique_guids = sorted(set(guids))
    result: dict[str, str] = {}
    for i, g in enumerate(unique_guids):
        lcp_left = _lcp_length(g, unique_guids[i - 1]) if i > 0 else 0
        lcp_right = (
            _lcp_length(g, unique_guids[i + 1])
            if i < len(unique_guids) - 1
            else 0
        )
        required = max(lcp_left, lcp_right) + 1
        prefix_len = max(required, min_len)
        prefix_len = min(prefix_len, len(g))
        result[g] = g[:prefix_len]
    return result


def _unique_prefix(
    guid: str, siblings: Iterable[str], min_len: int = 8
) -> str:
    """Shortest prefix of ``guid`` unique among ``siblings``
    (>= ``min_len`` chars, lowercase).

    Single-GUID counterpart to ``_guid_prefix_map`` for write-tool
    responses — pass the relevant table's other GUIDs as
    ``siblings`` (safe to include ``guid`` itself; it's filtered).
    Fast path returns the ``min_len`` slice when nothing shares it.
    """
    guid_lower = guid.lower()
    min_prefix = guid_lower[:min_len]
    collision_candidates = [
        s.lower() for s in siblings
        if s.lower() != guid_lower
        and s.lower().startswith(min_prefix)
    ]
    if not collision_candidates:
        return min_prefix
    max_lcp = max(_lcp_length(guid_lower, s) for s in collision_candidates)
    prefix_len = max(max_lcp + 1, min_len)
    return guid_lower[: min(prefix_len, len(guid_lower))]


class GnuCashLockError(Exception):
    """Raised when the GnuCash book is locked by another process."""

    pass


# Errors ``open()`` retries through. Three shapes, because a locked
# book announces itself differently per backend:
#
#   sqlite3.OperationalError       — "database is locked", raised by
#                                    the DBAPI on a file book.
#   sqlalchemy.exc.OperationalError — the same, wrapped, plus every
#                                    PostgreSQL connection failure.
#   GnucashException                — piecash's own check of the
#                                    ``gnclock`` table, which GnuCash
#                                    populates on BOTH backends. This
#                                    is what a book open in GnuCash
#                                    desktop raises; before it was
#                                    mapped here it surfaced as a bare
#                                    "Lock on the file" with no hint
#                                    that closing GnuCash fixes it.
#
# Non-lock members of these classes are re-raised untouched — see
# ``_is_lock_error``.
_OPEN_ERRORS: tuple[type[Exception], ...] = (
    sqlite3.OperationalError,
    sa_exc.OperationalError,
    GnucashException,
)


_LOCK_PHRASES = (
    "database is locked",        # SQLITE_BUSY
    "database table is locked",  # SQLITE_LOCKED
    "lock on the file",          # piecash, off the gnclock table
    "lock wait timeout",         # MySQL / MariaDB
    "could not obtain lock",     # PostgreSQL
    "deadlock",
)


def _is_lock_error(exc: Exception) -> bool:
    """True when ``exc`` means "someone else has this book open".

    Message-shape matching is the only option available: piecash
    raises a bare ``GnucashException`` and the DBAPIs don't carry a
    portable "locked" code. Kept narrow on purpose — a PostgreSQL
    connection refusal is an OperationalError too, and retrying it
    three times just delays the real error.
    """
    msg = str(exc).lower()
    # The phrases a lock is announced with — SQLite's two, piecash's
    # gnclock check, MySQL's and PostgreSQL's. A bare "lock" also
    # matched ``no such table: gnclock`` (a SQLite file that is not a
    # GnuCash book was "locked by GnuCash") and any path with "lock"
    # in it (a missing book under ``sherlock/``) — adversarial review
    # 2026-09-30, FC-13 / DS-13.
    return any(phrase in msg for phrase in _LOCK_PHRASES)


class StaleFXRateError(ValueError):
    """Raised when posting/paying a foreign-currency document would
    etch a stale exchange rate.

    Carries a structured ``fx_detail`` so the tool layer
    (``safe_tool``) can surface a machine-parseable ``error_type:
    "stale_fx_rate"`` response with the currency, rate, rate date,
    and age — the inputs the caller needs to either ``create_price``
    or retry with ``force=True``. Subclasses ``ValueError`` so any
    generic ``except ValueError`` path degrades it to a plain
    validation error rather than dropping it.
    """

    def __init__(self, message: str, fx_detail: dict):
        super().__init__(message)
        self.fx_detail = fx_detail


# ── Serializers ────────────────────────────────────────────────────


def _account_to_dict(account: piecash.Account) -> dict:
    """Convert a piecash Account to a serializable dict."""
    result = {
        "guid": account.guid,
        "name": account.name,
        "fullname": account.fullname,
        "type": account.type,
        "commodity": account.commodity.mnemonic if account.commodity else None,
        "description": account.description or "",
        "placeholder": bool(account.placeholder),
    }
    # Account notes live in the "notes" slot — the same key GnuCash
    # desktop's account editor reads/writes. Conditional so
    # note-less accounts keep their original shape.
    try:
        notes = _slot_value_str(account["notes"])
    except KeyError:
        notes = ""
    if notes:
        result["notes"] = notes
    return result


# Default type per conventional top-level name — used by
# ``_account_to_compact_line`` to suppress ``[TYPE]`` annotations
# except where the type departs from convention
# (``Assets:Old Loan [LIABILITY]``).
#
# Localization: keys are GnuCash's English defaults; non-English
# charts ("Activos", "資産") don't match and get the redundant
# annotation everywhere. Acceptable until a localization pass keys
# by account type instead.
_DEFAULT_TYPES = {
    "Assets": {"ASSET"},
    "Liabilities": {"LIABILITY"},
    "Income": {"INCOME"},
    "Expenses": {"EXPENSE"},
    "Equity": {"EQUITY"},
}


_SLOT_BOOL_TRUE = frozenset({"1", "true", "yes", "y", "on"})
_SLOT_BOOL_FALSE = frozenset({"0", "false", "no", "n", "off", ""})


def _slot_bool(entity, key: str) -> bool | None:
    """Tri-state boolean slot read: True, False, or None (absent /
    unrecognized).

    THE convention for boolean slots — extracted per the standing
    backlog note when the third boolean slot (``no_reconcile``)
    arrived: ``credit-note`` parsed strictly (== "1") while
    ``is_retirement`` parsed leniently, and a third private
    convention was the trigger to consolidate. Lenient wins because
    slot values are user-typed through set_account_slot; an
    unrecognized value returns None so each caller keeps its own
    fallback semantics instead of this helper guessing.
    """
    try:
        raw = entity[key]
    except KeyError:
        return None
    val = (_slot_value_str(raw) or "").strip().lower()
    if val in _SLOT_BOOL_TRUE:
        return True
    if val in _SLOT_BOOL_FALSE:
        return False
    return None


def _account_to_compact_line(account: piecash.Account) -> str:
    """Convert a piecash Account to a compact one-line string.

    Format: "fullname [ANNOTATION]" where annotation is shown only when
    the account type is non-obvious or the account is a placeholder.

    Examples:
        "Assets:Checking [BANK]"
        "Expenses:Groceries [PLACEHOLDER]"
        "Expenses:Groceries:Bakery"
        "Assets:Investments [ASSET, PLACEHOLDER]"
    """
    fullname = account.fullname
    annotations = []

    top_level = fullname.split(":")[0]
    default_types = _DEFAULT_TYPES.get(top_level, set())

    if account.type not in default_types:
        annotations.append(account.type)
    if account.placeholder:
        annotations.append("PLACEHOLDER")

    # Book text goes into a one-line row: escaped, so a name holding
    # a newline or tab cannot start a row of its own, and left
    # otherwise exactly as written so the path still resolves when a
    # caller copies it back (_one_line).
    shown = _one_line(fullname)
    if annotations:
        return f"{shown} [{', '.join(annotations)}]"
    return shown


# GnuCash's lot flag is a tri-state, from libgnucash/engine/gnc-lot.cpp:
#
#     #define LOT_CLOSED_UNKNOWN (-1)
#
# ``gnc_lot_is_closed`` recomputes whenever the stored value is
# negative, and ``gnc_lot_get_balance`` caches the answer: no splits →
# FALSE; balance zero → TRUE; otherwise FALSE. Desktop resets a lot to
# UNKNOWN every time it adds or removes a split, so a book that has
# been through desktop carries -1 on most of its lots. Reading -1 as
# "closed" (which this server did, believing -1 was GnuCash's TRUE)
# hid every open lot desktop had touched.
_LOT_CLOSED_UNKNOWN = -1
_LOT_OPEN = 0
_LOT_CLOSED = 1


def _lot_is_closed(lot) -> bool:
    """``gnc_lot_is_closed``, ported: the one reader of ``lot.is_closed``.

    A stored 0 or 1 is the answer. Anything negative means compute it
    the way GnuCash does — a lot with no splits is open; a lot whose
    quantities sum to zero is closed; anything else is open.
    """
    flag = lot.is_closed
    if flag is not None and flag >= 0:
        return bool(flag)
    splits = list(lot.splits)
    if not splits:
        return False
    return sum((s.quantity for s in splits), Decimal("0")) == 0


def _lot_cache_flag(lot) -> None:
    """Cache the computed flag before adding a split to a lot.

    What ``gnc_lot_is_closed`` does on every read: an UNKNOWN flag is
    resolved and stored. Needed on the write side because piecash's
    own guard (``check_no_change_if_lot_is_close``) tests the raw
    column for truth, and -1 is true — so a desktop-touched open lot
    would refuse a payment or an assignment until someone wrote the
    real answer down. Call it before every ``split.lot = lot``.
    """
    if lot.is_closed is not None and lot.is_closed >= 0:
        return
    if _lot_is_closed(lot):
        lot.is_closed = _LOT_CLOSED
    else:
        lot.is_closed = _LOT_OPEN


def _lot_hold_open(lot) -> None:
    """Mark a lot open while a port of GnuCash's lot arithmetic moves
    splits through it, where ``_lot_cache_flag`` cannot be used: in
    the middle of ``gncOwnerReduceSplitTo`` the lot's splits may sum
    to zero for one statement (the reduced split is in, its remainder
    not yet), and a computed "closed" would make piecash refuse the
    very split that reopens it. The caller leaves every lot it held
    open at UNKNOWN once the session has flushed, as desktop does."""
    lot.is_closed = _LOT_OPEN


def _lot_forget_flag(lot) -> None:
    """Mark a lot's flag UNKNOWN after a split leaves it or changes.

    What GnuCash does in ``gnc_lot_remove_split`` and in
    ``mark_split`` (every ``xaccSplitSetAmount`` / ``SetValue``, so
    void and unvoid too): the cached answer described the old
    splits, so the next read recomputes. Without it a sold-out lot
    that loses or voids its sell keeps reading closed with shares in
    it. Call it with ``split.lot`` beside every write of a split's
    amount; a split in no lot passes None and nothing happens.
    """
    if lot is not None:
        lot.is_closed = _LOT_CLOSED_UNKNOWN


def _ordered_splits(transaction) -> list:
    """A transaction's splits in GnuCash's own order, on every backend.

    ``Transaction.cpp`` (``xaccTransSortSplits``, run on every commit)
    sorts with ``split_sign_cmp``: splits whose value is not negative
    first, negative after, and nothing else — a stable sort keeps the
    rest as loaded. "As loaded" is the part that differs by backend:
    SQLite hands rows back in insertion order, InnoDB in primary-key
    order, so the same book listed its splits credit-first on MySQL
    and debit-first on the file. Debits first is GnuCash's rule; the
    account path and then the split GUID break ties the same way
    everywhere. Every renderer of a transaction's legs reads this.
    """
    return sorted(
        transaction.splits,
        key=lambda s: (s.value < 0, s.account.fullname, s.guid),
    )


def _txn_sort_key(transaction) -> tuple:
    """Newest-first ordering key for transaction listings.

    ``post_date`` alone leaves same-day transactions in storage order,
    which is a different order per backend. ``enter_date`` is the
    timestamp GnuCash stamps at entry — the order the book was
    written in — and the GUID settles anything left.
    """
    return (
        transaction.post_date or date.min,
        transaction.enter_date or datetime.min,
        transaction.guid,
    )


def _split_to_dict(
    split: piecash.Split,
    split_prefixes: dict[str, str] | None = None,
    lot_prefixes: dict[str, str] | None = None,
) -> dict:
    """Convert a piecash Split to a full serializable dict.

    With ``split_prefixes`` / ``lot_prefixes``, the emitted ``guid``
    and ``lot_guid`` are collision-safe short forms; omitted, full
    32-char GUIDs (back-compat for unmigrated callers).
    """
    rec_date = split.reconcile_date
    if rec_date and rec_date.year <= 1970:
        rec_date = None
    split_guid = (
        split_prefixes.get(split.guid, split.guid)
        if split_prefixes is not None
        else split.guid
    )
    lot_guid = None
    if split.lot is not None:
        lot_guid = (
            lot_prefixes.get(split.lot.guid, split.lot.guid)
            if lot_prefixes is not None
            else split.lot.guid
        )
    return {
        "guid": split_guid,
        "account": split.account.fullname,
        "value": str(split.value),
        "quantity": str(split.quantity),
        "memo": split.memo or "",
        "action": split.action or "",
        "reconcile_state": split.reconcile_state,
        "reconcile_date": rec_date.isoformat() if rec_date else None,
        "lot_guid": lot_guid,
    }


def _split_to_compact_dict(split: piecash.Split) -> dict:
    """Tight serialization of a Split for history / audit contexts
    (~40-60 chars vs ~140 for the full dict).

    Emits ``account`` and ``value`` always; ``quantity`` only when
    cross-currency, ``memo`` / ``action`` / ``reconcile_state`` only
    when non-default — for replace_splits' ``previous_splits`` this is
    the audit log's only record of the deleted legs. Omits ``guid`` (the described splits are gone —
    unaddressable), ``reconcile_date``, and ``lot_guid``. Compatible
    with the audit formatter's ``_format_splits_text``.
    """
    result = {
        "account": split.account.fullname,
        "value": str(split.value),
    }
    if split.quantity != split.value:
        result["quantity"] = str(split.quantity)
    if split.memo:
        result["memo"] = split.memo
    if split.action:
        result["action"] = split.action
    if split.reconcile_state and split.reconcile_state != "n":
        result["reconcile_state"] = split.reconcile_state
    return result


def _transaction_to_dict(
    transaction: piecash.Transaction,
    txn_prefixes: dict[str, str] | None = None,
    split_prefixes: dict[str, str] | None = None,
    lot_prefixes: dict[str, str] | None = None,
) -> dict:
    """Convert a piecash Transaction to a serializable dict.

    With the prefix maps (from the BaseGnuCashBook mtime-keyed
    caches), emitted transaction and split GUIDs are collision-safe
    short forms; omitted, full GUIDs (back-compat).
    """
    txn_guid = (
        txn_prefixes.get(transaction.guid, transaction.guid)
        if txn_prefixes is not None
        else transaction.guid
    )
    result = {
        "guid": txn_guid,
        # Null post_date is a legal old-book artifact; render
        # as None rather than crashing the whole listing.
        "date": (
            transaction.post_date.isoformat()
            if transaction.post_date else None
        ),
        "description": transaction.description,
        "currency": transaction.currency.mnemonic,
        "splits": [
            _split_to_dict(
                s,
                split_prefixes=split_prefixes,
                lot_prefixes=lot_prefixes,
            )
            for s in _ordered_splits(transaction)
        ],
    }
    if transaction.notes:
        result["notes"] = transaction.notes
    return result


def _commodity_to_compact_line(namespace: str, entry: dict) -> str:
    """Convert a commodity dict to a compact tab-separated line.

    Format: "NAMESPACE:MNEMONIC\\tfullname\\tprice_info"
    """
    prefix = f"{namespace}:{entry['mnemonic']}"
    name = _tsv_cell(entry.get("fullname", ""))
    parts = [prefix, name]
    lp = entry.get("latest_price")
    if entry.get("default_currency"):
        parts.append("— (default currency)")
    elif lp:
        parts.append(f"{lp['value']} {lp['currency']} ({lp['date']})")
    # Work-list markers, present only under the stale_days filter.
    if entry.get("no_price"):
        parts.append("no price on file")
    elif entry.get("days_stale") is not None:
        parts.append(f"{entry['days_stale']}d stale")
    return "\t".join(parts)


def _short_guid(full_guid: str, prefixes: dict[str, str] | None) -> str:
    """Resolve a GUID to its emitted prefix.

    When `prefixes` contains the GUID, use that (the caller pre-computed
    a collision-safe prefix map via `_guid_prefix_map`). Otherwise fall
    back to the raw 8-char truncation — safe for backward compat with
    direct callers that don't build a map.
    """
    if prefixes is not None and full_guid in prefixes:
        return prefixes[full_guid]
    return full_guid[:8]


def _unreconciled_split_to_compact_line(
    split_dict: dict, prefixes: dict[str, str] | None = None,
) -> str:
    """Convert an unreconciled split dict to a compact tab-separated line.

    Format: "short_guid\\tYYYY-MM-DD\\tdescription\\tamount\\tstate"

    Args:
        split_dict: Split dict with guid/date/description/amount/reconcile_state/memo.
        prefixes: Optional map from full split GUID to collision-safe prefix
                  (built via `_guid_prefix_map`). Defaults to raw 8-char
                  truncation when absent.
    """
    short = _short_guid(split_dict["guid"], prefixes)
    d = split_dict["date"]
    desc = _tsv_cell(split_dict["description"])
    amount = split_dict["amount"]
    state = split_dict["reconcile_state"]
    return f"{short}\t{d}\t{desc}\t{amount}\t{state}"


# Split-list collapse threshold: transactions with more than this many
# splits (in the column that would be rendered) get truncated to
# "top-K by |value| + '+N more'". Keeps paycheck-style multi-leg
# transactions (~17 splits: gross, taxes, 401k, insurance, net) from
# flooding the compact view. Full breakdown is always one step away
# via ``get_transaction(guid)``.
_SPLIT_COLLAPSE_THRESHOLD = 4
_SPLIT_COLLAPSE_KEEP = 3


def _format_one_split(split: piecash.Split, transaction: piecash.Transaction) -> str:
    """Render one split as ``account amount``, with cross-currency annotation.

    Shared between the full and collapsed split-list paths so the
    per-split rendering stays consistent with history.
    """
    account_name = _one_line(split.account.fullname)
    amount = split.quantity
    if split.quantity != split.value:
        currency = transaction.currency.mnemonic
        commodity = split.account.commodity.mnemonic
        return f"{account_name} {amount} {commodity} (={split.value} {currency})"
    return f"{account_name} {amount}"


def _format_splits_collapsed(
    splits: list[piecash.Split], transaction: piecash.Transaction,
) -> str:
    """Render a split list, collapsing long tails.

    <= ``_SPLIT_COLLAPSE_THRESHOLD`` splits render in full; longer
    lists render the top ``_SPLIT_COLLAPSE_KEEP`` by ``|value|``
    plus ``+N more``. Ranking by ``|value|`` (transaction currency),
    not ``|quantity|`` — quantities across commodities are
    incommensurable and produce misleading orderings.
    """
    if len(splits) <= _SPLIT_COLLAPSE_THRESHOLD:
        return ", ".join(_format_one_split(s, transaction) for s in splits)

    ranked = sorted(splits, key=lambda s: abs(s.value), reverse=True)
    kept = ranked[:_SPLIT_COLLAPSE_KEEP]
    more = len(splits) - _SPLIT_COLLAPSE_KEEP
    shown = ", ".join(_format_one_split(s, transaction) for s in kept)
    return f"{shown}, +{more} more"


def _transaction_to_compact_line(
    transaction: piecash.Transaction,
    focus_account: str | None = None,
    prefixes: dict[str, str] | None = None,
) -> str:
    """Convert a piecash Transaction to a compact tab-separated line.

    Two output shapes:

    - **Unfiltered** (``focus_account is None``)::

          YYYY-MM-DD<TAB>guid<TAB>Description<TAB>Account amount[, ...][, +N more]

    - **Register** (``focus_account`` set)::

          YYYY-MM-DD<TAB>guid<TAB>±Amount<TAB>Description<TAB>Other splits[, +N more]

      The checking-register view: column 3 is the signed impact on
      the filtered account (what a reconciler reads), whose own
      splits are summed into it and dropped from the split list.

    Both shapes collapse long split lists via
    ``_format_splits_collapsed``; the full breakdown is always one
    ``get_transaction(guid)`` away. ``prefixes`` defaults to raw
    8-char truncation when absent.
    """
    # Null post_date is a legal old-book artifact.
    date_str = (
        transaction.post_date.isoformat()
        if transaction.post_date else "(no date)"
    )
    short = _short_guid(transaction.guid, prefixes)
    # Description and notes are book text — a bank import's, a
    # counterparty's, anyone's — and they land in a row the model
    # reads beside the server's own banners. Written raw, a newline
    # in one started a new "row" (or a line in the server's voice);
    # _tsv_cell renders it as a visible \n inside its own cell.
    desc = _tsv_cell(transaction.description)
    splits = _ordered_splits(transaction)

    if focus_account is not None:
        focus_splits = [
            s for s in splits if s.account.fullname == focus_account
        ]
        other_splits = [
            s for s in splits if s.account.fullname != focus_account
        ]
        focus_amt = sum(
            (s.quantity for s in focus_splits), Decimal("0")
        )
        if focus_amt > 0:
            amt_str = f"+{focus_amt}"
        elif focus_amt < 0:
            amt_str = str(focus_amt)
        else:
            amt_str = "0"
        splits_str = _format_splits_collapsed(other_splits, transaction)
        line = f"{date_str}\t{short}\t{amt_str}\t{desc}\t{splits_str}"
    else:
        splits_str = _format_splits_collapsed(splits, transaction)
        line = f"{date_str}\t{short}\t{desc}\t{splits_str}"

    if transaction.notes:
        line += f"\t{_tsv_cell(transaction.notes)}"
    return line


def _lot_to_compact_line(
    lot_dict: dict, prefixes: dict[str, str] | None = None,
) -> str:
    """Convert a lot dict to a compact tab-separated line.

    Args:
        lot_dict: Lot dict with guid/title/quantity/cost_basis/is_closed.
        prefixes: Optional map from full lot GUID to collision-safe prefix
                  (built via `_guid_prefix_map`). Defaults to raw 8-char
                  truncation when absent.
    """
    short = _short_guid(lot_dict["guid"], prefixes)
    title = _tsv_cell(lot_dict["title"])
    qty = lot_dict["quantity"]
    basis = lot_dict["cost_basis"]
    parts = [short, title, f"{qty} shares", f"{basis} basis"]
    if lot_dict.get("is_closed"):
        parts.append("CLOSED")
    return "\t".join(parts)


def _sx_to_compact_line(
    sx_dict: dict, prefixes: dict[str, str] | None = None,
) -> str:
    """Convert a scheduled transaction dict to a compact tab-separated line.

    Args:
        sx_dict: Scheduled transaction dict.
        prefixes: Optional map from full scheduled-transaction GUID to
                  collision-safe prefix (built via `_guid_prefix_map`).
                  Defaults to raw 8-char truncation when absent.
    """
    short = _short_guid(sx_dict["guid"], prefixes)
    name = _tsv_cell(sx_dict["name"])
    freq = sx_dict["frequency"]
    if not sx_dict.get("enabled"):
        status = "disabled"
    elif sx_dict.get("next_occurrence"):
        # next_occurrence is the oldest un-entered date; one in the
        # past is overdue and reads that way (ISO strings compare
        # as dates).
        nxt = sx_dict["next_occurrence"]
        label = "overdue" if nxt < date.today().isoformat() else "next"
        status = f"{label}:{nxt}"
    else:
        status = "no upcoming"
    return f"{short}\t{name}\t{freq}\t{status}"


def _upcoming_to_compact_line(
    entry: dict, prefixes: dict[str, str] | None = None,
) -> str:
    """Convert an upcoming transaction dict to a compact tab-separated line.

    Args:
        entry: Upcoming-transaction dict (guid refers to the scheduled
               transaction, not the yet-to-be-instantiated real one).
        prefixes: Optional map from full scheduled-transaction GUID to
                  collision-safe prefix (built via `_guid_prefix_map`).
                  Defaults to raw 8-char truncation when absent.
    """
    short = _short_guid(entry["guid"], prefixes)
    name = _tsv_cell(entry["name"])
    occ_date = entry["occurrence_date"]
    days = entry["days_until"]
    amount = entry["amount"]
    # Foreign-currency templates label their amount — an unlabeled
    # "2000" from an HKD schedule reads as the book currency.
    if entry.get("currency"):
        amount = f"{amount} {entry['currency']}"
    due = (
        f"{-days} day{'s' if days != -1 else ''} overdue" if days < 0
        else f"{days} day{'s' if days != 1 else ''}"
    )
    return f"{short}\t{name}\t{occ_date}\t{due}\t{amount}"


# ── Book source ────────────────────────────────────────────────────


# The snapshot command per SQLAlchemy backend name, for the backup
# refusals. MariaDB ships ``mariadb-dump`` and keeps ``mysqldump`` as a
# compatibility alias, so the MySQL row names what works on both.
_DB_DUMP_TOOLS = {
    "postgresql": "pg_dump",
    "mysql": "mysqldump (mariadb-dump on MariaDB)",
    "mariadb": "mariadb-dump",
    "sqlite": "sqlite3 .backup",
}


@dataclass(frozen=True)
class BookSource:
    """Where a book lives, and what that implies about its features.

    A GnuCash book reaches this server one of two ways: as a SQLite
    FILE (the only shape this server accepted before the DB backend)
    or as a SQLAlchemy CONNECTION URI naming a Postgres/MySQL
    database GnuCash itself wrote. Everything downstream of
    :meth:`BaseGnuCashBook.open` is backend-agnostic — piecash maps
    the same schema either way — but four things genuinely are not,
    and all four ask this object instead of testing for a path
    themselves (the chokepoint rule from CLAUDE.md, applied to a new
    axis):

      1. Backups — SQLite's online-backup API copies a *file*.
      2. The GUID-prefix caches — keyed on the file's mtime.
      3. The audit/backup directory — derived from the book's parent.
      4. The startup format sniff — reads the file's magic bytes.

    ``is_file`` is the question all four ask. Nothing else in the
    codebase should branch on ``path is not None``.

    Attributes:
        path: The book file, or None for a URI-backed book.
        uri: SQLAlchemy URL for the read-only GUID engine. For file
            books this is the read-only ``file:`` form; for URI books
            it's the user's connection string verbatim.
        display_name: What a human sees. Filename for file books; the
            password-redacted URI for DB books.
        log_name: The name audit/debug/backup storage keys on. Ends in
            the usual filename for file books so existing ``.mcp``
            directories keep working untouched.
    """

    path: Path | None
    uri: str
    display_name: str
    log_name: str

    @property
    def backend(self) -> str:
        """SQLAlchemy backend name: ``sqlite``, ``postgresql``, ``mysql``.

        File books are ``sqlite`` by construction; URI books answer
        whatever dialect the connection string names (``mariadb``
        for the ``mariadb+`` scheme).
        """
        return _parse_book_url(self.uri).get_backend_name()

    @property
    def dump_tool(self) -> str:
        """The native snapshot command for this book's database.

        Every backup refusal and the server-config backend line name
        it, so a MySQL user is not told to run ``pg_dump``. One table,
        one reader: a new dialect adds a row here and nowhere else.
        """
        return _DB_DUMP_TOOLS.get(self.backend, "your database's dump tool")

    @property
    def is_file(self) -> bool:
        """True when a real file backs this book.

        The single question the file-shaped features ask. Backups,
        the mtime cache token, and the startup format check are all
        gated on it.
        """
        return self.path is not None

    @classmethod
    def from_path(cls, book_path: str | Path) -> "BookSource":
        """Build a source for a SQLite book file.

        Resolves to an absolute path: audit/debug/backup dirs derive
        from ``path.parent``, and an unresolved ``..`` would write
        them outside the intended directory. ``resolve(strict=True)``
        also covers the existence check.

        Raises:
            FileNotFoundError: missing, or not a regular file.
        """
        try:
            resolved = Path(book_path).resolve(strict=True)
        except FileNotFoundError:
            raise FileNotFoundError(f"GnuCash book not found: {book_path}")
        if not resolved.is_file():
            raise FileNotFoundError(
                f"GnuCash book path is not a regular file: {book_path}"
            )
        # ``quote`` matters, and fixes a real bug: sqlite3 URI mode
        # percent-DECODES the path, so the pre-DB-backend
        # ``f"file:{path}?mode=ro"`` looked for
        # ``budget 100%25 final.gnucash`` under the name
        # ``budget 100% final.gnucash`` and every GUID-prefix lookup
        # died with "unable to open database file" — on a book
        # piecash otherwise opens fine. Encoding it is also what lets
        # one URI form serve both backends. (A ``?`` or ``#`` in the
        # name is unreachable here: piecash's own build_uri
        # interpolates the path unescaped, so such a book never opens
        # at all.)
        return cls(
            path=resolved,
            uri=f"sqlite:///file:{quote(str(resolved))}?mode=ro&uri=true",
            display_name=_book_display_name(str(resolved)),
            log_name=resolved.name,
        )

    @classmethod
    def from_uri(cls, uri: str) -> "BookSource":
        """Build a source for a database-backed book.

        ``log_name`` is derived from the URI's database name rather
        than a file, so ``GNUCASH_LOG_DIR`` gives DB books the same
        per-book audit/debug subdirectory layout file books get.
        Falls back to ``"book"`` for a URI with no database component.

        Raises:
            ValueError: the URI doesn't parse as a SQLAlchemy URL.
        """
        url = _parse_book_url(uri)
        db_name = (url.database or "").strip("/") or "book"
        # Same ``.gnucash`` suffix file books carry: resolve_mcp_dir
        # appends ``.mcp``, so a DB book's storage reads
        # ``mybook.gnucash.mcp`` exactly like a file book's.
        return cls(
            path=None,
            uri=uri,
            display_name=_book_display_name(uri),
            log_name=f"{db_name}.gnucash",
        )


# ── Base class ─────────────────────────────────────────────────────


class BaseGnuCashBook(CurrencyMixin, QueryMixin):
    """Thread-safe wrapper for piecash book operations.

    Holds the universal helpers used by every mixin; module-specific
    mixins combine with this base via ``build_book_class``.

    Inherits :class:`CurrencyMixin` (cross-commodity helpers) and
    :class:`QueryMixin` (indexed SQL split query) unconditionally —
    they're cross-cutting infrastructure needed regardless of which
    ``--modules`` are enabled.
    """

    # Tables that support GUID resolution, each with its full
    # prefix-lookup SQL — no f-string table interpolation in
    # ``_resolve_guid`` (safe under an allowlist, fragile when a
    # future table skips re-validation; no entry → no lookup).
    # ``slots`` is intentionally absent: slots have no primary GUID,
    # only ``obj_guid`` + name.
    #
    # ``:prefix`` rather than ``?``: these run through SQLAlchemy so
    # one query text serves SQLite and PostgreSQL alike (the DBAPIs
    # disagree on positional paramstyle — psycopg2 wants ``%s``).
    _GUID_TABLE_QUERIES: dict[str, str] = {
        "transactions": "SELECT guid FROM transactions WHERE guid LIKE :prefix",
        "splits": "SELECT guid FROM splits WHERE guid LIKE :prefix",
        "accounts": "SELECT guid FROM accounts WHERE guid LIKE :prefix",
        "lots": "SELECT guid FROM lots WHERE guid LIKE :prefix",
        "schedxactions": "SELECT guid FROM schedxactions WHERE guid LIKE :prefix",
        "commodities": "SELECT guid FROM commodities WHERE guid LIKE :prefix",
        "budgets": "SELECT guid FROM budgets WHERE guid LIKE :prefix",
        "customers": "SELECT guid FROM customers WHERE guid LIKE :prefix",
        "vendors": "SELECT guid FROM vendors WHERE guid LIKE :prefix",
        "invoices": "SELECT guid FROM invoices WHERE guid LIKE :prefix",
        "prices": "SELECT guid FROM prices WHERE guid LIKE :prefix",
        "entries": "SELECT guid FROM entries WHERE guid LIKE :prefix",
    }
    _GUID_TABLES = frozenset(_GUID_TABLE_QUERIES.keys())

    def __init__(self, book: "str | Path | BookSource"):
        """Initialize from a book file path or a :class:`BookSource`.

        A bare string or Path still means a SQLite file, so every
        pre-existing caller — ``_book_for``, the test fixtures, any
        embedding of ``GnuCashBook(path)`` — keeps working unchanged.
        Database-backed books are constructed by passing
        ``BookSource.from_uri(...)``.

        Args:
            book: Path to the GnuCash SQLite file, or a BookSource.

        Raises:
            FileNotFoundError: If a file book's path doesn't exist.
        """
        self.source = (
            book if isinstance(book, BookSource)
            else BookSource.from_path(book)
        )
        # ``book_path`` stays the name the whole codebase reads, and
        # stays a Path for file books. It is None for a DB book —
        # but nothing should test it for None: ask
        # ``self.source.is_file`` instead, so the "does this book
        # have a file?" rule lives in exactly one place.
        self.book_path = self.source.path
        # Thread-local staging buffer for audit-log before_state:
        # write methods stage on their open session; @audit_log
        # consumes after the tool returns — no second book open.
        self._audit_tls = threading.local()
        # GUID-prefix-map caches, ``(token, dict)`` — see
        # ``_cache_token`` for what the token is and why DB-backed
        # books don't get one.
        self._txn_prefix_cache: tuple[int | None, dict[str, str]] | None = None
        self._split_prefix_cache: tuple[int | None, dict[str, str]] | None = None
        self._lot_prefix_cache: tuple[int | None, dict[str, str]] | None = None
        # Read-only engine for GUID-prefix lookups, built on first use
        # and reused for the life of the book instance.
        self._guid_engine = None

    def _stage_audit_before(self, state: dict | None) -> None:
        """Stage a before-state dict for the next audit-log consume.

        Called by write book methods before mutating — using the same
        piecash session they already have open, so no extra open cost.
        The `@audit_log` decorator consumes this after the tool returns.

        Passing None is a no-op; passing a dict overwrites any
        previously-staged state (shouldn't happen in a well-formed call
        chain, but is safe).
        """
        self._audit_tls.before_state = state

    def _consume_audit_before(self) -> dict | None:
        """Return the staged before-state dict (if any) and clear it.

        Called by `@audit_log` after the wrapped tool returns, or in its
        exception handler to discard leftover state from a failed write.
        Always clears on read so stale values can't leak across calls.
        """
        state = getattr(self._audit_tls, "before_state", None)
        self._audit_tls.before_state = None
        return state

    def _resolve_guid(
        self, table: str, partial: str, min_len: int = 8
    ) -> str:
        """Resolve a partial GUID prefix to a full 32-character GUID.

        Validates (length min_len..32, hex only, uppercase
        normalized) before touching the database. Read-only, and
        session-independent — callers reach it both inside and
        outside an open book.

        ``min_len`` defaults to 8; the accounts table uses 7 (paired
        with the ``%`` marker; ~1k accounts keeps 7-char collisions
        below 0.2%).

        Returns:
            Full 32-character lowercase-hex GUID.

        Raises:
            ValueError: invalid table, malformed prefix, no match,
                or multiple matches.
        """
        if table not in self._GUID_TABLES:
            raise ValueError(f"Invalid table: {table}")

        n = len(partial)
        if n < min_len:
            raise ValueError(
                f"GUID prefix too short (minimum {min_len} chars): {partial!r}"
            )
        if n > 32:
            raise ValueError(
                f"GUID too long (maximum 32 chars): {partial!r}"
            )
        if not _HEX_GUID_RE.fullmatch(partial):
            raise ValueError(
                f"GUID contains non-hex characters: {partial!r}. "
                f"GUIDs are hex [0-9a-f]."
            )

        # Normalize to lowercase — GnuCash stores GUIDs as lowercase hex
        # and SQLite LIKE is case-sensitive by default.
        partial = partial.lower()

        # Fast path: already a full GUID
        if n == 32:
            return partial

        rows = self._guid_prefix_rows(table, partial)

        if len(rows) == 0:
            raise ValueError(f"No {table[:-1]} found matching GUID prefix: {partial}")
        if len(rows) > 1:
            matches = [r[0] for r in rows]
            raise ValueError(
                f"Ambiguous GUID prefix '{partial}' matches {len(rows)} {table}: "
                f"{', '.join(m[:12] + '...' for m in matches)}"
            )
        return rows[0][0]

    def _guid_prefix_rows(self, table: str, partial: str) -> list:
        """Run one allowlisted GUID-prefix query, read-only.

        The single place a GUID lookup reaches storage, for both
        backends. File books connect through SQLite's ``mode=ro`` URI
        so the query cannot write even in principle; DB books use the
        book's own connection string, where read-only-ness is the
        server's grant to make.

        ``NullPool`` is deliberate: the pre-DB-backend code opened and
        closed a ``sqlite3`` connection per call, and a pooled handle
        lingering on the book file would change behavior GnuCash
        desktop and the documented restore procedure depend on.

        ``table`` is assumed already validated against
        ``_GUID_TABLES`` — ``_resolve_guid`` is the only caller and
        checks first.
        """
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        if self._guid_engine is None:
            self._guid_engine = create_engine(
                self.source.uri, poolclass=NullPool
            )
        with self._guid_engine.connect() as conn:
            return conn.execute(
                text(self._GUID_TABLE_QUERIES[table]),
                {"prefix": partial + "%"},
            ).fetchall()

    @contextmanager
    def open(
        self, readonly: bool = True, max_retries: int = 3, retry_delay: float = 0.5
    ) -> Generator[piecash.Book, None, None]:
        """Context manager for book access with retry logic for locked files.

        Args:
            readonly: If True, open in read-only mode. Default True for safety.
            max_retries: Number of retry attempts if file is locked. Default 3.
            retry_delay: Seconds to wait between retries (exponential backoff).

        Yields:
            piecash.Book instance.

        Raises:
            GnuCashLockError: If the book is locked after all retries.
            FileNotFoundError: If the book file doesn't exist.
        """
        # Only the OPEN is retried. The yield sits outside the retry
        # loop deliberately: a lock-shaped error raised by the *body*
        # used to re-enter the loop and yield a second time, which
        # @contextmanager turns into "generator didn't stop after
        # throw()" — the real error replaced by a confusing one. The
        # DB backend widens the catch (below), so the hazard had to
        # close before it could bite more often.
        book = None
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                book = piecash.open_book(
                    readonly=readonly, do_backup=False,
                    **self.source_open_kwargs()
                )
                open_elapsed = (time.time() - start_time) * 1000
                debug_logger.debug(
                    f"Book opened (readonly={readonly}) in {open_elapsed:.0f}ms"
                )
                break
            except _OPEN_ERRORS as e:
                if _is_lock_error(e):
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (attempt + 1))
                        continue
                    raise GnuCashLockError(
                        f"GnuCash book is locked (possibly by GnuCash or another process). "
                        f"Close GnuCash and try again."
                        f"{self._lock_holder_note()} Details: {e}"
                    ) from e
                raise

        if book is None:
            # Unreachable with the default max_retries: the loop
            # either breaks with a book or raises. Guards a caller
            # passing max_retries=0, which would otherwise yield None
            # and fail somewhere far from the cause.
            raise ValueError("max_retries must be at least 1")

        try:
            yield book
        finally:
            close_start = time.time()
            book.close()
            # piecash binds a fresh engine to every open_book and
            # never disposes it, so the pool keeps a connection
            # checked in after close. On PostgreSQL that is one
            # server slot per tool call until the cyclic GC happens
            # to reclaim the engine (measured: 15 opens, 15 open
            # connections). Dispose explicitly; the engine is
            # single-use by construction here.
            book.session.get_bind().dispose()
            close_elapsed = (time.time() - close_start) * 1000
            debug_logger.debug(f"Book closed in {close_elapsed:.0f}ms")

    def _lock_holder_note(self) -> str:
        """Who holds a file book's lock, read from its ``gnclock``
        row: the host and process GnuCash recorded, and whether that
        process is still running when it is this machine's. A crash
        leaves the row behind, and "close GnuCash and try again" is
        no help when GnuCash is not open (adversarial review
        2026-09-30, FC-15). Empty for a database book, or when the
        row cannot be read. Nothing is changed: clearing a stale
        lock is the user's call, in GnuCash ("Open Anyway")."""
        if not self.source.is_file:
            return ""
        import os
        import socket

        try:
            con = sqlite3.connect(
                f"file:{self.book_path}?mode=ro", uri=True, timeout=1,
            )
            try:
                rows = con.execute("SELECT * FROM gnclock").fetchall()
            finally:
                con.close()
        except Exception:
            return ""
        if not rows:
            return ""
        host, pid = str(rows[0][0]), rows[0][1]
        note = f" The lock was taken on {host}, process {pid}"
        try:
            local = host.split(".")[0].lower() == (
                socket.gethostname().split(".")[0].lower()
            )
            if local:
                try:
                    os.kill(int(pid), 0)
                    note += ", which is still running."
                except ProcessLookupError:
                    note += (
                        ", which is no longer running: a stale lock "
                        "left by a crash. Open the book in GnuCash, "
                        "choose \"Open Anyway\", and close it again "
                        "to clear it."
                    )
                except (PermissionError, ValueError, OverflowError):
                    note += "."
            else:
                note += " (another machine)."
        except Exception:
            note += "."
        return note

    def source_open_kwargs(self) -> dict:
        """The piecash ``open_book`` argument naming this book.

        File books pass ``sqlite_file`` — the bare path, exactly as
        before the DB backend existed. piecash's ``build_uri``
        interpolates that into a URI unescaped, so handing it a
        pre-built URI instead would change behavior for any path it
        mangles; the read-only GUID engine builds its own escaped URI
        and leaves this one alone.
        """
        if self.source.is_file:
            return {"sqlite_file": str(self.source.path)}
        return {"uri_conn": self.source.uri}

    def _find_account(self, book: piecash.Book, fullname: str) -> piecash.Account | None:
        """Find an account by its full name path.

        Path-only leaf that ``_resolve_account`` falls through to;
        user-supplied refs go through ``_resolve_account``.

        Template accounts are never returned — callers that
        legitimately touch templates (the scheduled-transaction
        CRUD) use piecash's typed relationships, not name lookup.
        """
        template_guids = self._template_account_guids(book)
        for account in book.accounts:
            if account.guid in template_guids:
                continue
            if account.fullname == fullname:
                return account
        return None

    def _top_level_account_of_type(
        self, book: piecash.Book, acct_type: str
    ) -> "tuple[piecash.Account | None, dict | None]":
        """Find the top-level account of a given ``GNCAccountType``.

        "Top-level" = a direct child of the real root account. This is
        the locale-invariant replacement for English-name parent
        lookups such as ``_find_account(book, "Income")``: it keys off
        ``type`` and ``parent is root``, never a name, so it works on a
        localized book (German "Erträge", etc.) **and** survives a user
        renaming the account. Account *types* are never localized;
        names always are.

        Returns ``(account, notice)``:

        - exactly one match → ``(account, None)``
        - several → the lowest-``fullname`` pick plus an
          ``ambiguous_top_level_account`` notice (mirrors the
          ``ambiguous_fx_account`` convention so callers can surface it)
        - none → ``(None, None)``

        The scheduled-transaction template subtree is excluded, as
        everywhere else accounts are surfaced.
        """
        root = book.root_account
        template_guids = self._template_account_guids(book)
        candidates = sorted(
            (
                a
                for a in book.accounts
                if a.guid not in template_guids
                and a.type == acct_type
                and a.parent is not None
                and a.parent.guid == root.guid
            ),
            key=lambda a: a.fullname,
        )
        if not candidates:
            return None, None
        if len(candidates) == 1:
            return candidates[0], None
        chosen = candidates[0]
        names = ", ".join(a.fullname for a in candidates)
        notice = {
            "type": "ambiguous_top_level_account",
            "account_type": acct_type,
            "candidates": [a.fullname for a in candidates],
            "chosen": chosen.fullname,
            "message": (
                f"Found {len(candidates)} top-level {acct_type} "
                f"accounts ({names}); using {chosen.fullname!r}. Pass "
                f"an explicit account to override."
            ),
        }
        return chosen, notice

    # GnuCash names its auto-created balancing accounts via gettext —
    # ``_("Imbalance")-<CUR>`` and ``_("Orphan")-<CUR>`` (Scrub.cpp) —
    # so the leading word is localized. An English-only prefix check
    # misses them on a localized book and the data-integrity warning
    # goes dark. These are the catalog translations of "Imbalance" and
    # "Orphan" for every shipped GnuCash locale, extracted from
    # po/<lang>.po (stable branch); fuzzy and untranslated entries are
    # excluded — GnuCash's runtime ignores fuzzy, and an untranslated
    # locale falls back to the English forms, which are listed. Lower-
    # cased; a leaf name that STARTS WITH any of them is a balancing
    # account (the "-<CUR>" suffix, when present, follows the word).
    # Regenerate via the recipe in specs/gnucash-account-naming-i18n.md.
    _BALANCING_ACCOUNT_NAME_PREFIXES = frozenset(
        s.lower()
        for s in (
            # "Imbalance" — every shipped GnuCash locale
            'Ausgleichskonto', 'Açık', 'Chưa cân bằng', 'Debalans',
            'Descuadre', 'Desequilibri', 'Desequilibrio',
            'Desequilíbrio', 'Deskoadratzea', 'Dezechilibru',
            'Disbalansas', 'Epätasapaino', 'Imbalance',
            'Kiegyenlítés', 'Nerovnováha', 'Nevyváženost',
            'Niet in balans', 'Niezrównoważenie', 'Non soldé',
            'Obalans', 'Sbilancio', 'Starpība', 'Tak-seimbang',
            'Ubalance', 'Ubalanse', 'osomotolon', 'Χωρίς ισοζύγιο',
            'Дебаланс', 'Дисбаланс', 'Невідповідність', 'Неравнена',
            'חוסר איזון', 'حساب عدم التوازن', 'عدم تناسب', 'ناتراز',
            'असंतुलन', 'असंतुलित', 'असन्तुलित', 'गॊर मसावात',
            'समानथायगैयै', 'অসমতা', 'ইমবেলেন্স', 'અસંતુલિત',
            'சமநிலையில்லாத', 'అసమతుల్యం', 'ಅಸಮತೋಲನ', '不平衡的', '失調',
            '貸借不一致', 'ꯏꯝꯕꯦꯂꯦꯟꯁ', '대차 불일치',
            # "Orphan" — every shipped GnuCash locale
            'Apleistas', 'Ausbuchungskonto', 'Açık', 'Egyedülálló',
            'Foreldreløs', 'Föräldralös', 'Hittebarn', 'Huérfano',
            'Nepovezano', 'Nesaistīts', 'Orfan', 'Orfano', 'Orfe',
            'Orphan', 'Orphelin', 'Orpo', 'Osierocone', 'Sirota',
            'Sirotek', 'Terlantar', 'Thừa', 'Umezurtza', 'Verweesd',
            'onath', 'Órfã', 'Órfão', 'Ορφανό', 'Занедбаний',
            'Изоставена', 'Напуштено', 'Упущенный', 'יתומים',
            'حساب الأيتام', 'یتیم', 'अनाथ', 'आरफन', 'बेवारिसी',
            'मावरिया', 'लावारस', 'लावारिस', 'অনাথ', 'ওর্ফান',
            'આધાર વિનાનું', 'கைவிடப்பட்டது', 'అనాథ', 'ಆರ್ಫನ್', '不明',
            '孤立的', '無主的', 'ꯑꯣꯔꯐꯥꯟ', '고아',
        )
    )

    def _is_auto_balancing_account(
        self, account: piecash.Account, root: piecash.Account
    ) -> bool:
        """True iff ``account`` is a GnuCash auto-created Imbalance or
        Orphan balancing account.

        A non-zero balance on one of these is a structural defect the
        dashboard surfaces. Locale-robust: match by **structure** —
        type ``BANK``, a direct child of root (both invariants of how
        GnuCash hangs these accounts) — plus the NAME SHAPE GnuCash
        actually emits: a catalog word exactly, or the word plus a
        ``-<CUR>`` mnemonic suffix (Scrub.cpp writes
        ``_("Imbalance")-<currency>``). A bare prefix match is too
        loose across ~100 pooled locale words: a legitimate root
        BANK account named with an ordinary word from ANY locale
        ("Açık Hesap", "Thừa kế…") was misclassified as suspense —
        warned about on the dashboard and silently excluded from
        runway/low-cash liquidity. The suffix is shape-checked
        (short, no spaces), not compared to the account's own
        commodity: real books contain e.g. an EUR-book
        ``Imbalance-USD``. (``Orphaned Gains`` is deliberately
        excluded — it is type ``INCOME``, a legitimate account, not
        a defect.)
        """
        if account.type != "BANK":
            return False
        if account.parent is None or account.parent.guid != root.guid:
            return False
        leaf = account.name.strip().lower()
        for p in self._BALANCING_ACCOUNT_NAME_PREFIXES:
            if leaf == p:
                return True
            if leaf.startswith(p + "-"):
                suffix = leaf[len(p) + 1:]
                if 0 < len(suffix) <= 10 and suffix.isalnum():
                    return True
        return False

    # ── Book-locale inference + localized account names (§6.3) ────────
    #
    # When we auto-create an FX/discount account on a localized book we
    # give it a localized leaf name so it reads naturally in the user's
    # language. This is purely cosmetic: resolution after first use is
    # GUID-based (the Layer-0 designated-account slot), so the leaf name
    # never participates in finding the account again — an English
    # fallback is always safe and never blocks.
    #
    # gettext (po/<lang>.po) translations of the five structural type
    # words, keyed by GNCAccountType, used ONLY to infer the book locale
    # from its top-level accounts (voting; >=2 matches win). Extracted
    # from every shipped GnuCash po catalog that has all five words AND a
    # Realized Gain/Loss name (47 locales); inference and naming move
    # together, so a detected locale always has a leaf name. Locale keys
    # are normalized codes (pt_BR→pt, zh_CN→zh); variant files that would
    # collide on a key are skipped. Regenerate per gnucash-account-
    # naming-i18n.md.
    _STRUCTURAL_TYPE_NAMES = {
        "ar": {"ASSET": "الأصول", "LIABILITY": "الالتزامات", "INCOME": "الدخل", "EXPENSE": "المصروفات", "EQUITY": "حقوق الملكية"},
        "as": {"ASSET": "সম্পত্তিবোৰ", "LIABILITY": "বিশ্বাসযোগ্যতাবোৰ", "INCOME": "উপাৰ্জন", "EXPENSE": "ব্যয়বোৰ", "EQUITY": "সাধাৰণ অংশ"},
        "bg": {"ASSET": "Активи", "LIABILITY": "Пасиви", "INCOME": "Доход", "EXPENSE": "Разходи", "EQUITY": "Собствен капитал"},
        "brx": {"ASSET": "सम्पति", "LIABILITY": "दाहार", "INCOME": "आय", "EXPENSE": "खरसा", "EQUITY": "बन्दक"},
        "ca": {"ASSET": "Actiu", "LIABILITY": "Passiu", "INCOME": "Ingressos", "EXPENSE": "Despeses", "EQUITY": "Patrimoni"},
        "cs": {"ASSET": "Aktiva", "LIABILITY": "Pasiva", "INCOME": "Příjmy", "EXPENSE": "Náklady", "EQUITY": "Vlastní jmění"},
        "da": {"ASSET": "Aktiver", "LIABILITY": "Passiver", "INCOME": "Indtægt", "EXPENSE": "Udgifter", "EQUITY": "Egenkapital"},
        "de": {"ASSET": "Aktiva", "LIABILITY": "Fremdkapital", "INCOME": "Ertrag", "EXPENSE": "Aufwand", "EQUITY": "Eigenkapital"},
        "doi": {"ASSET": "जैदाद", "LIABILITY": "देनदारियां", "INCOME": "आमदन", "EXPENSE": "खर्चे", "EQUITY": "इक्विटी"},
        "el": {"ASSET": "Ενεργητικό", "LIABILITY": "Παθητικό", "INCOME": "Έσοδα", "EXPENSE": "Έξοδα", "EQUITY": "Καθαρή θέση"},
        "es": {"ASSET": "Activos", "LIABILITY": "Pasivos", "INCOME": "Ingreso", "EXPENSE": "Gastos", "EQUITY": "Patrimonio"},
        "fi": {"ASSET": "Vastaavaa", "LIABILITY": "Vieras pääoma", "INCOME": "Tulo", "EXPENSE": "Menot", "EQUITY": "Oma pääoma"},
        "fr": {"ASSET": "Actifs (avoirs)", "LIABILITY": "Passifs (dettes)", "INCOME": "Revenus", "EXPENSE": "Dépenses", "EQUITY": "Capitaux propres"},
        "gu": {"ASSET": "સંપત્તિઓ", "LIABILITY": "જવાબદારી", "INCOME": "આવક", "EXPENSE": "ખર્ચ", "EQUITY": "હિસ્સો"},
        "he": {"ASSET": "נכסים", "LIABILITY": "התחייבויות", "INCOME": "הכנסות", "EXPENSE": "הוצאות", "EQUITY": "הון"},
        "hi": {"ASSET": "संपत्तियां", "LIABILITY": "देयताएं", "INCOME": "आय", "EXPENSE": "खर्चे", "EQUITY": "इक्विटी"},
        "hr": {"ASSET": "Imovina", "LIABILITY": "Obveze", "INCOME": "Prihod", "EXPENSE": "Rashod", "EQUITY": "Kapital"},
        "hu": {"ASSET": "Eszközök", "LIABILITY": "Kötelezettségek", "INCOME": "Bevétel", "EXPENSE": "Kiadások", "EQUITY": "Saját tőke"},
        "id": {"ASSET": "Aset", "LIABILITY": "Liabilitas", "INCOME": "Pendapatan", "EXPENSE": "Pengeluaran", "EQUITY": "Ekuitas"},
        "it": {"ASSET": "Attività", "LIABILITY": "Passività", "INCOME": "Entrate", "EXPENSE": "Uscite", "EQUITY": "Patrimonio netto"},
        "ja": {"ASSET": "資産", "LIABILITY": "負債", "INCOME": "収益", "EXPENSE": "費用", "EQUITY": "純資産"},
        "kn": {"ASSET": "ಆಸ್ತಿಗಳು", "LIABILITY": "ಹೊಣೆಗಾರಿಕೆಗಳು", "INCOME": "ಆದಾಯ", "EXPENSE": "ಖರ್ಚುಗಳು", "EQUITY": "ಈಕ್ವಿಟಿ"},
        "ko": {"ASSET": "자산", "LIABILITY": "부채", "INCOME": "수입", "EXPENSE": "비용", "EQUITY": "자기자본"},
        "kok": {"ASSET": "एसेट्स", "LIABILITY": "देणी", "INCOME": "उत्पन्न", "EXPENSE": "खर्च", "EQUITY": "समभाग"},
        "ks": {"ASSET": "एिसीट", "LIABILITY": "लायबोलटी", "INCOME": "ईनकम", "EXPENSE": "खरचो", "EQUITY": "बराबरी"},
        "lt": {"ASSET": "Turtas", "LIABILITY": "Įsipareigojimai", "INCOME": "Pajamos", "EXPENSE": "Sąnaudos", "EQUITY": "Nuosavybė"},
        "lv": {"ASSET": "Aktīvi", "LIABILITY": "Pasīvi", "INCOME": "Ieņēmumi", "EXPENSE": "izdevumi", "EQUITY": "Pašu kapitāls"},
        "mai": {"ASSET": "संपत्ति", "LIABILITY": "देयता", "INCOME": "आय", "EXPENSE": "खर्च", "EQUITY": "इक्विटी"},
        "mni": {"ASSET": "ꯂꯟ-ꯊꯨꯝ", "LIABILITY": "ꯂꯥꯏꯌꯕꯤꯂꯤꯇꯤꯁ", "INCOME": "ꯏꯟꯀꯝ", "EXPENSE": "ꯆꯥꯗꯤꯡ", "EQUITY": "ꯏꯀꯨꯏꯇꯤ"},
        "mr": {"ASSET": "मालमत्ता", "LIABILITY": "दायित्व", "INCOME": "मिळकत", "EXPENSE": "खर्च", "EQUITY": "इक्विटी"},
        "nb": {"ASSET": "Eiendeler", "LIABILITY": "Gjeld", "INCOME": "Inntekt", "EXPENSE": "Kostnader", "EQUITY": "Egenkapital"},
        "ne": {"ASSET": "सम्पत्ति", "LIABILITY": "दायित्व", "INCOME": "आम्दानी", "EXPENSE": "खर्चहरु", "EQUITY": "इक्युटी"},
        "nl": {"ASSET": "Activa", "LIABILITY": "Vreemd vermogen", "INCOME": "Opbrengsten", "EXPENSE": "Kosten", "EQUITY": "Eigen vermogen"},
        "pl": {"ASSET": "Aktywa", "LIABILITY": "Pasywa", "INCOME": "Przychody", "EXPENSE": "Wydatki", "EQUITY": "Kapitał własny"},
        "pt": {"ASSET": "Ativos", "LIABILITY": "Passivos", "INCOME": "Receita", "EXPENSE": "Despesas", "EQUITY": "Patrimônio líquido"},
        "ro": {"ASSET": "Active", "LIABILITY": "Pasive", "INCOME": "Venituri", "EXPENSE": "Cheltuieli", "EQUITY": "Capital propriu"},
        "ru": {"ASSET": "Активы", "LIABILITY": "Обязательства", "INCOME": "Приход", "EXPENSE": "Расходы", "EQUITY": "Собственные средства"},
        "sk": {"ASSET": "Aktíva", "LIABILITY": "Pasíva", "INCOME": "Príjem", "EXPENSE": "Výdavky", "EQUITY": "Vlastné imanie"},
        "sr": {"ASSET": "Добра", "LIABILITY": "Дуговања", "INCOME": "Приход", "EXPENSE": "Расходи", "EQUITY": "Акција"},
        "sv": {"ASSET": "Tillgångar", "LIABILITY": "Skulder", "INCOME": "Inkomst", "EXPENSE": "Utgifter", "EQUITY": "Eget kapital"},
        "ta": {"ASSET": "சொத்துக்கள்", "LIABILITY": "பொறுப்பீடுகள்", "INCOME": "ஊதியம்", "EXPENSE": "செலவுகள்", "EQUITY": "உறுப்பு"},
        "te": {"ASSET": "ఆస్తులు", "LIABILITY": "అప్పులు", "INCOME": "ఆదాయం", "EXPENSE": "వ్యయాలు", "EQUITY": "ఈక్విటీ"},
        "tr": {"ASSET": "Varlıklar", "LIABILITY": "Y.Kaynaklar", "INCOME": "Gelir", "EXPENSE": "Gider", "EQUITY": "Özkaynak"},
        "uk": {"ASSET": "Активи", "LIABILITY": "Зобов'язання", "INCOME": "Надходження", "EXPENSE": "Видатки", "EQUITY": "Маржа"},
        "ur": {"ASSET": "مالیات", "LIABILITY": "ادائیگی", "INCOME": "آمدنی", "EXPENSE": "خرچ", "EQUITY": "اكویٹی"},
        "vi": {"ASSET": "Tài sản", "LIABILITY": "Tài sản nợ", "INCOME": "Thu nhập", "EXPENSE": "Phí tổn", "EQUITY": "Cổ phần"},
        "zh": {"ASSET": "资产", "LIABILITY": "负债", "INCOME": "收入", "EXPENSE": "支出", "EQUITY": "所有者权益"},
    }

    # Localized leaf names for the accounts we auto-create, keyed by an
    # internal concept slug then normalized locale code. A missing
    # concept or locale degrades to the caller's English default. The
    # fx_gain_loss row is the "Realized Gain/Loss" translation for every
    # shipped locale that also has a complete structural-word set (47,
    # from po/<lang>.po), kept in lockstep with _STRUCTURAL_TYPE_NAMES.
    # The discount concepts have no shipped GnuCash translation, so they
    # stay English.
    _LOCALIZED_ACCOUNT_NAMES = {
        "fx_gain_loss": {
            "ar": "مكسب/خسارة محقَّقة",
            "as": "লাভ/লোচকান বুজি লোৱা হল",
            "bg": "Реализирана печалба/загуба",
            "brx": "आदाय खालामनाय मुलाम्फा/खहा",
            "ca": "Guanys/pèrdues realitzats",
            "cs": "Realizovaný zisk/ztráta",
            "da": "Realiseret overskud/tab",
            "de": "Realisierter Gewinn/Verlust",
            "doi": "स्वीकृत नऱफा/ नुक्सान",
            "el": "Πραγματοποιηθέντα Κέρδη/Ζημιές",
            "es": "Ganancias/Pérdidas Ocurridas",
            "fi": "Toteutuneet tulot/menot",
            "fr": "Gains/pertes réalisés",
            "gu": "વાસ્તવિક લાભ/નુક્શાન",
            "he": "רוח/הפסד ממומש",
            "hi": "वास्तविक लाभ/हानि",
            "hr": "Ostvarena dobit/gubitak",
            "hu": "Realizált nyereség/veszteség",
            "id": "Keuntungan/Kerugian Direalisasikan",
            "it": "Profitti e perdite realizzati",
            "ja": "実現損益",
            "kn": "ನಗದುಗೊಳಿಸಲಾದ ಗಳಿಕೆ/ನಷ್ಟ",
            "ko": "실제 이익/손실",
            "kok": "मेळिल्लो नफो / तोटो",
            "ks": "रीयालायज़ीड फॊयदी /नुकसान",
            "lt": "Patirtas pelnas/nuostolis",
            "lv": "Realizētie ieņēmumi/zaudējumi",
            "mai": "वास्तविक लाभ/हानि",
            "mni": "ꯐꯪꯂꯕ ꯑꯇꯣꯡꯕ/ꯑꯃꯥꯡꯕ",
            "mr": "विक्री करून आलेला नफा/तोटा",
            "nb": "Realisert over-/underskudd",
            "ne": "असूल गरिएको नाफा/नोक्सान",
            "nl": "Gerealiseerde winst/verlies",
            "pl": "Zyski/straty zrealizowane",
            "pt": "Ganhos e perdas realizados",
            "ro": "Câștiguri/pierderi realizate",
            "ru": "Реализованная прибыль/убыток",
            "sk": "Realizované Zisky/Straty",
            "sr": "Остварени добитак/губитак",
            "sv": "Reavinst/-förlust",
            "ta": "விவரிக்கப்பட்ட இலாபம்/இழப்பு",
            "te": "గ్రహించిన లాభం/నష్టం",
            "tr": "Gerçekleşmiş Kazanç/Kayıp",
            "uk": "Отримані прибутки/втрати",
            "ur": "حقیقی نفع/ نقصان",
            "vi": "Gia tăng/giảm thực xảy ra",
            "zh": "已实现获利(亏损)",
        },
    }

    # English structural names, for the AFFIRMATIVE check below.
    # Deliberately not a row in _STRUCTURAL_TYPE_NAMES: the locale
    # vote's None already means "no locale proven", and English must
    # stay distinguishable from undetermined for fail-safe gates.
    _ENGLISH_TYPE_NAMES = {
        "ASSET": "Assets",
        "LIABILITY": "Liabilities",
        "INCOME": "Income",
        "EXPENSE": "Expenses",
        "EQUITY": "Equity",
    }

    def _top_level_type_names(
        self, book: piecash.Book
    ) -> dict[str, list[str]]:
        """Top-level (root-child) account names grouped by
        GNCAccountType, normalized (strip + lower), template accounts
        excluded. Shared traversal for the locale vote and the
        affirmative-English check."""
        root = book.root_account
        template_guids = self._template_account_guids(book)
        names_by_type: dict[str, list[str]] = {}
        for acct in book.accounts:
            if acct.guid in template_guids:
                continue
            if acct.parent is None or acct.parent.guid != root.guid:
                continue
            names_by_type.setdefault(acct.type, []).append(
                acct.name.strip().lower()
            )
        return names_by_type

    def _book_reads_english(self, book: piecash.Book) -> bool:
        """True when the chart's top-level type accounts affirmatively
        match the English structural names (>= 2 exact matches — the
        same threshold as the locale vote).

        Exists for fail-safe creation gates (battery ruling 4(b);
        the bookkeeper's Sabine repro, 2026-09-01): ``_infer_book_locale``
        returning None means UNDETERMINED, not English. A DATEV/SKR03
        chart's numbered top-levels ("Aufwendungen 2/4") match no
        locale's exact words, so a gate that reads that None as
        English auto-creates English accounts into a German book.
        Cosmetic naming may keep treating None as English; anything
        that CREATES must require this affirmative check instead.
        (The GNUCASH_LOCALE override is not consulted here — when it
        is set, ``_infer_book_locale`` returns non-None and gates
        never reach the undetermined branch.)
        """
        names_by_type = self._top_level_type_names(book)
        score = sum(
            1
            for atype, word in self._ENGLISH_TYPE_NAMES.items()
            if any(
                n == word.lower() for n in names_by_type.get(atype, ())
            )
        )
        return score >= 2

    def _infer_book_locale(self, book: piecash.Book) -> str | None:
        """Infer the book's locale (a normalized 2-letter language
        code) for naming auto-created accounts. Decided source of
        truth (§6.3):

        1. ``GNUCASH_LOCALE`` env override, reduced to its language
           code (``de_DE.UTF-8`` → ``de``).
        2. else **vote**: match the book's top-level type accounts
           against the gettext structural-word catalog; the language
           with the most matches wins (>= 2, so a single coincidental
           hit doesn't drive inference).
        3. else ``None`` → UNDETERMINED. Cosmetic callers (leaf
           naming) fall back to English; creation gates must not —
           they require ``_book_reads_english`` instead, because a
           numbered chart (SKR03/DATEV "Aufwendungen 2/4") matches
           too few words to trigger ANY locale while being plainly
           non-English.

        Voting (not a single-account lookup) sidesteps the two-
        translation-sources trap: a German book's top-level income is
        the template word "Erträge", which does NOT equal the gettext
        "Ertrag" — but Assets/Expenses/Equity ("Aktiva"/"Aufwand"/
        "Eigenkapital") match exactly, so German still resolves.
        """
        import os
        override = os.environ.get("GNUCASH_LOCALE")
        if override:
            code = override.strip().split(".")[0].split("_")[0].lower()
            return code or None

        names_by_type = self._top_level_type_names(book)
        if not names_by_type:
            return None

        best_lang, best_score = None, 0
        for lang, type_words in self._STRUCTURAL_TYPE_NAMES.items():
            # Normalize BOTH sides the same way (strip + lower) —
            # account names are stripped above; a table entry with
            # trailing WHITESPACE would otherwise be unmatchable in
            # every book, silently weakening that locale's vote.
            # strip() does NOT remove punctuation residue (a trailing
            # ':' from po-label extraction) — that must be fixed in
            # the table data itself, per the regeneration recipe.
            score = sum(
                1
                for atype, word in type_words.items()
                if any(
                    n == word.strip().lower()
                    for n in names_by_type.get(atype, ())
                )
            )
            if score > best_score:
                best_lang, best_score = lang, score
        return best_lang if best_score >= 2 else None

    def _locale_account_name(
        self, concept: str, english_default: str, locale: str | None,
    ) -> str:
        """Localized leaf name for an auto-created-account ``concept``,
        or ``english_default`` when no localization applies (``locale``
        is None/unknown, or the concept has no translation). Cosmetic
        only — resolution is GUID-based, so the fallback never blocks.
        """
        if locale is None:
            return english_default
        return self._LOCALIZED_ACCOUNT_NAMES.get(concept, {}).get(
            locale, english_default
        )

    # ── Short account GUIDs ───────────────────────────────────────────
    #
    # Format "%XXXXXXX" (literal "%" + ≥7 hex chars) — cheap on the
    # wire, collision-safe at typical chart sizes. The "%" marker
    # distinguishes short account GUIDs from paths and from bare-hex
    # transaction prefixes; accounts are the one entity with a
    # path-vs-GUID disambiguation problem at the input boundary.
    # Tools that accept account refs call _resolve_account, which
    # handles all three shapes.

    _SHORT_ACCOUNT_GUID_PREFIX = "%"
    _SHORT_ACCOUNT_GUID_MIN_LEN = 7

    def _account_short_guid(
        self, book: piecash.Book, account: piecash.Account
    ) -> str:
        """Return a collision-safe short GUID for ``account``.

        Format: ``"%" + 7+ hex chars``. The hex suffix is the shortest
        prefix of ``account.guid`` unique among all account GUIDs in
        the book (≥ 7). Most accounts get exactly 7; only collisions
        push out further, per :func:`_unique_prefix`.

        Use this for compact emit. To go the other direction (resolve
        a short or path back to an Account), call :meth:`_resolve_account`.
        """
        siblings = (a.guid for a in book.accounts)
        suffix = _unique_prefix(
            account.guid, siblings, min_len=self._SHORT_ACCOUNT_GUID_MIN_LEN
        )
        return self._SHORT_ACCOUNT_GUID_PREFIX + suffix

    def _account_short_guid_map(
        self, book: piecash.Book
    ) -> dict[str, str]:
        """Map every account.guid → '%shortguid' for batch rendering.

        Cheaper than calling :meth:`_account_short_guid` once per account
        when emitting multiple lines (e.g., ``list_accounts``). Single
        sort + linear pass via :func:`_guid_prefix_map`.
        """
        guids = [a.guid for a in book.accounts]
        raw = _guid_prefix_map(
            guids, min_len=self._SHORT_ACCOUNT_GUID_MIN_LEN
        )
        return {g: self._SHORT_ACCOUNT_GUID_PREFIX + p for g, p in raw.items()}

    def _cache_token(self) -> int | None:
        """Invalidation token for the GUID-prefix caches, or None to
        disable caching for this book.

        The ONLY place a prefix cache may decide it is still valid —
        a new cached map must call this rather than stat the book
        itself (locked by ``TestCacheTokenChokepoint``).

        File books: the file's ``st_mtime_ns``. SQLite touches the
        file on every commit, so it invalidates on any write by this
        server or another process — GnuCash desktop included.

        DB books: None, which every caller reads as "always rebuild".
        A shared database has no equivalent cheap, universally-bumped
        token: ``pg_stat`` counters aren't durable across restarts
        and a ``max(...)`` probe is its own query. Since another
        client can commit between two of our reads, a stale map here
        would emit short GUIDs that collide — wrong output, not just
        slow output. Rebuilding costs one indexed GUID scan per call;
        correctness wins.
        """
        if not self.source.is_file:
            return None
        return self.source.path.stat().st_mtime_ns

    def _transaction_prefix_map(
        self, book: piecash.Book
    ) -> dict[str, str]:
        """Return the full-table transaction-GUID prefix map, cached.

        Several read paths emit short prefixes that must be
        collision-safe against ``_resolve_guid``'s table-wide LIKE
        lookup; this shares one build. Cache invariant: correct
        until any transaction mutates — see ``_cache_token``.
        """
        token = self._cache_token()
        if (
            token is not None
            and self._txn_prefix_cache is not None
            and self._txn_prefix_cache[0] == token
        ):
            return self._txn_prefix_cache[1]
        prefix_map = _guid_prefix_map(t.guid for t in book.transactions)
        self._txn_prefix_cache = (token, prefix_map)
        return prefix_map

    def _split_prefix_map(
        self, book: piecash.Book
    ) -> dict[str, str]:
        """Return the full-table split-GUID prefix map, cached.

        Same mtime-keyed pattern as ``_transaction_prefix_map``.
        """
        token = self._cache_token()
        if (
            token is not None
            and self._split_prefix_cache is not None
            and self._split_prefix_cache[0] == token
        ):
            return self._split_prefix_cache[1]
        # One indexed query for the guid column — the relationship
        # walk (book.transactions → t.splits) lazy-loaded one splits
        # collection PER TRANSACTION on every cold cache, i.e. after
        # every book write (release-review finding 8's third head).
        from piecash.core.transaction import Split

        prefix_map = _guid_prefix_map(
            guid for (guid,) in book.session.query(Split.guid)
        )
        self._split_prefix_cache = (token, prefix_map)
        return prefix_map

    def _lot_prefix_map(
        self, book: piecash.Book
    ) -> dict[str, str]:
        """Return the full-table lot-GUID prefix map, cached.

        Same mtime-keyed pattern as ``_transaction_prefix_map``.
        """
        token = self._cache_token()
        if (
            token is not None
            and self._lot_prefix_cache is not None
            and self._lot_prefix_cache[0] == token
        ):
            return self._lot_prefix_cache[1]
        prefix_map = _guid_prefix_map(
            lot.guid for acct in book.accounts for lot in acct.lots
        )
        self._lot_prefix_cache = (token, prefix_map)
        return prefix_map

    def _resolve_account(
        self, book: piecash.Book, ref: str
    ) -> piecash.Account | None:
        """Resolve a path, ``%short``, or full 32-hex GUID to an Account.

        Three input shapes: ``"%XXXXXXX"`` → ``_resolve_guid`` (min
        7 hex chars); 32-char hex → direct lookup; anything else →
        path via ``_find_account``.

        Returns ``None`` for a well-formed ref that matches nothing
        OR resolves into the template subtree. Raises ``ValueError``
        on malformed or ambiguous short GUIDs.

        Template-filter chokepoint: filtering only on the path
        branch would let ``%short`` / full-GUID input bypass it and
        silently mutate template-tree rows; the post-dispatch check
        applies the filter uniformly regardless of input shape.
        """
        if ref.startswith(self._SHORT_ACCOUNT_GUID_PREFIX):
            suffix = ref[len(self._SHORT_ACCOUNT_GUID_PREFIX):]
            try:
                full_guid = self._resolve_guid(
                    "accounts",
                    suffix,
                    min_len=self._SHORT_ACCOUNT_GUID_MIN_LEN,
                )
            except ValueError as e:
                # No-match on a well-formed prefix degrades to None,
                # mirroring _find_account's contract. Validation errors
                # (too short, non-hex, ambiguous) propagate.
                if "No account" in str(e):
                    return None
                raise
            from piecash.core.account import Account
            acct = book.session.query(Account).filter_by(guid=full_guid).first()
        elif len(ref) == 32 and _HEX_GUID_RE.fullmatch(ref):
            from piecash.core.account import Account
            acct = (
                book.session.query(Account)
                .filter_by(guid=ref.lower())
                .first()
            )
        else:
            acct = self._find_account(book, ref)

        # Template-filter chokepoint — where every input shape
        # converges (redundant for the path branch; cost is one
        # set-membership check).
        if acct is not None and acct.guid in self._template_account_guids(book):
            return None
        # The root is not an account anyone posts to, renames, or
        # annotates; it has no path, so only a GUID reaches it. A
        # batch row that did posted to ROOT and the amount left every
        # report (net worth 10,000 → 9,877), and the slot tools could
        # delete the designated-account markers the server keeps
        # there (adversarial review 2026-09-30, C43).
        if acct is not None and acct.type == "ROOT":
            return None
        return acct

    def _normalize_account_refs(
        self,
        params: dict,
        keys_to_normalize: set[str] | frozenset[str],
    ) -> dict:
        """Resolve any short / full-GUID account refs in ``params``
        to canonical full paths.

        For display surfaces — the audit log is the human-facing
        one, and a reviewer shouldn't have to look up ``%2e78c86``
        to know what got reconciled.

        The book layer provides the *mechanics*; the caller provides
        the *config*: ``keys_to_normalize`` names the top-level keys
        carrying refs (caller-specific), while ``splits`` is ALWAYS
        walked when present (its dicts universally carry ``account``
        refs).

        Returns:
            A new dict (non-destructive) with resolved refs replaced
            by fullnames; unresolvable refs stay in place so
            rendering still has something to show.
        """
        if not params:
            return params

        # Pass 1: collect every unique ref worth a lookup.
        refs: set[str] = set()
        for key, value in params.items():
            if key in keys_to_normalize and _looks_like_guid_ref(value):
                refs.add(value)
            elif key == "splits" and isinstance(value, list):
                for split in value:
                    if isinstance(split, dict):
                        acct_ref = split.get("account")
                        if _looks_like_guid_ref(acct_ref):
                            refs.add(acct_ref)
        if not refs:
            return params

        # Pass 2: one book open, resolve everything.
        resolved: dict[str, str] = {}
        from gnucash_mcp.logging_config import DEBUG_LOGGER_NAME
        debug_logger = logging.getLogger(DEBUG_LOGGER_NAME)
        try:
            with self.open(readonly=True) as book:
                for ref in refs:
                    try:
                        account = self._resolve_account(book, ref)
                        if account is not None:
                            resolved[ref] = account.fullname
                    except Exception as e:
                        # Stale/ambiguous/malformed — leave the raw
                        # ref; the log line is still useful, and the
                        # debug entry explains the raw %xxxxxxx.
                        debug_logger.warning(
                            f"Account ref normalization: could not "
                            f"resolve {ref!r} for canonical rendering "
                            f"({type(e).__name__}: {e})"
                        )
                        continue
        except Exception as e:
            debug_logger.warning(
                f"Account ref normalization: book unavailable "
                f"({type(e).__name__}: {e})"
            )

        if not resolved:
            return params

        def _replace(s):
            return resolved.get(s, s) if isinstance(s, str) else s

        # Pass 3: rewrite, non-destructively.
        out: dict = {}
        for key, value in params.items():
            if key in keys_to_normalize:
                out[key] = _replace(value)
            elif key == "splits" and isinstance(value, list):
                new_splits = []
                for split in value:
                    if isinstance(split, dict) and "account" in split:
                        new_splits.append(
                            {**split, "account": _replace(split["account"])}
                        )
                    else:
                        new_splits.append(split)
                out[key] = new_splits
            else:
                out[key] = value
        return out

    def _upgrade_book_shapes(self, book) -> dict:
        """Write path only: convert every pre-1.5 private shape in the
        book to GnuCash's own, posting nothing, and say what it did.

        Three shapes shipped between 1.2 and 1.4.4 that only this
        server could read — schedule recipes in a `splits-json` slot
        (desktop's editor crashes on them), the invoice link under a
        child key desktop never reads, and budget amounts stored as
        magnitudes instead of GnuCash's natural sign. Each has its
        own converter on its mixin; this is the one caller, so a
        budget write converts the schedules too and the release note
        can say one thing: the first schedule, budget, or business
        write after upgrading converts the book. Reads never write
        (maintainer ruling, 2026-09-10). Modules that aren't loaded
        contribute nothing.

        Returns the non-zero counts / flags, keyed the way each
        module's response already reports them: ``templates_migrated``,
        ``invoice_links_migrated``, ``due_dates_backfilled``,
        ``voids_migrated``, ``book_stamped``, ``book_scrubbed``.
        """
        out: dict = {}
        # First, and before anything is converted: a snapshot of the
        # book as it stands (once per book; refuses the write if a
        # file book cannot be snapshotted). Absent when the backup
        # module is not loaded.
        snapshot = getattr(self, "_ensure_pre_upgrade_snapshot", None)
        if snapshot is not None:
            out.update(snapshot())
        sweep = getattr(self, "_migrate_all_legacy", None)
        if sweep is not None:
            n = sweep(book)
            if n:
                out["templates_migrated"] = n
        rename = getattr(self, "_migrate_invoice_link_keys", None)
        if rename is not None:
            n = rename(book)
            if n:
                out["invoice_links_migrated"] = n
        backfill = getattr(self, "_backfill_due_dates", None)
        if backfill is not None:
            n = backfill(book)
            if n:
                out["due_dates_backfilled"] = n
        voids = getattr(self, "_migrate_void_shapes", None)
        if voids is not None:
            n = voids(book)
            if n:
                out["voids_migrated"] = n
        biz = getattr(self, "_migrate_business_shapes", None)
        if biz is not None:
            out.update(biz(book))
        n = self._migrate_split_reconcile_dates(book)
        if n:
            out["split_reconcile_dates_filled"] = n
        out.update(self._migrate_reconcile_conventions(book))
        n = self._migrate_slot_fillers(book)
        if n:
            out["slot_fillers_normalized"] = n
        prices = getattr(self, "_migrate_price_shapes", None)
        if prices is not None:
            out.update(prices(book))
        stamp = getattr(self, "_ensure_budget_unreversed", None)
        if stamp is not None:
            from piecash.budget import Budget
            # GnuCash stamps only a book that has budgets (and
            # unstamps one that has none); mirror that.
            if book.session.query(Budget.guid).first() is not None:
                st = stamp(book)
                if st.get("stamped"):
                    out["book_stamped"] = _BUDGET_UNREVERSED_FEATURE
                if st.get("scrubbed"):
                    out["book_scrubbed"] = True
        return out

    # ── Desktop's reconcile-info frame ─────────────────────────────
    # Key names verbatim from libgnucash/engine/Account.cpp (stable,
    # read 2026-09-28): KEY_RECONCILE_INFO("reconcile-info");
    # xaccAccountSetReconcileLastDate → {"reconcile-info","last-date"}
    # (int64 time64); xaccAccountSetReconcileLastInterval →
    # {"reconcile-info","last-interval","months"} and {...,"days"}
    # (int64). Pinned by tests/test_reconcile_info.py. The SQL
    # backend stores a frame as a FRAME slot on the owner whose
    # guid_val names the frame, and each child on that frame guid
    # under its full path name — the shape the desktop-gated
    # sched-xaction and gncInvoice frames already follow.
    _RECONCILE_INFO_FRAME = "reconcile-info"
    _RECONCILE_LAST_DATE = "last-date"
    _RECONCILE_LAST_INTERVAL = "last-interval"
    _RECONCILE_INTERVAL_MONTHS = "months"
    _RECONCILE_INTERVAL_DAYS = "days"

    @staticmethod
    def _reconcile_interval(
        prev_statement_date: date, statement_date: date,
        prev_interval: "tuple[int, int] | None",
    ) -> "tuple[int, int] | None":
        """``gnc_save_reconcile_interval`` (gnucash/gnome/
        window-reconcile.cpp), ported verbatim: the ``(months,
        days)`` desktop remembers after a reconcile, or ``None``
        when it would remember nothing.

        days = whole days between the two statement dates. Exactly
        28 is ambiguous (four weeks or one month) and keeps the
        previous answer's shape: months if the last interval was
        one month (the default when none is stored), else 28 days.
        More than 28 is counted in calendar months, days 0. A
        negative result is not remembered.
        """
        days = (statement_date - prev_statement_date).days
        months = 0
        if days == 28:
            prev_months = 1 if prev_interval is None else prev_interval[0]
            if prev_months == 1:
                months, days = 1, 0
        elif days > 28:
            months = (
                (12 * statement_date.year + statement_date.month)
                - (12 * prev_statement_date.year + prev_statement_date.month)
            )
            days = 0
        if months >= 0 and days >= 0:
            return months, days
        return None

    @staticmethod
    def _read_reconcile_info_all(book) -> dict:
        """``{account_guid: {"last_date": date | None, "months": int
        | None, "days": int | None}}`` for every account carrying a
        ``reconcile-info`` frame — three portable queries for the
        whole book, never one per account. ``last-date`` is a
        time64; it reads back as the local calendar day."""
        from sqlalchemy import text

        frames = {
            r[1]: r[0] for r in book.session.execute(
                text(
                    "SELECT obj_guid, guid_val FROM slots "
                    "WHERE name = :f AND slot_type = 9 "
                    "AND guid_val IS NOT NULL"
                ),
                {"f": BaseGnuCashBook._RECONCILE_INFO_FRAME},
            ).fetchall()
        }
        if not frames:
            return {}
        out = {
            acct: {"last_date": None, "months": None, "days": None}
            for acct in frames.values()
        }
        f = BaseGnuCashBook._RECONCILE_INFO_FRAME
        for r in book.session.execute(
            text(
                "SELECT obj_guid, int64_val FROM slots "
                "WHERE name = :n AND int64_val IS NOT NULL"
            ),
            {"n": f"{f}/{BaseGnuCashBook._RECONCILE_LAST_DATE}"},
        ).fetchall():
            acct = frames.get(r[0])
            if acct is not None:
                out[acct]["last_date"] = datetime.fromtimestamp(
                    int(r[1])
                ).date()
        sub = f"{f}/{BaseGnuCashBook._RECONCILE_LAST_INTERVAL}"
        subframes = {
            r[1]: frames[r[0]] for r in book.session.execute(
                text(
                    "SELECT obj_guid, guid_val FROM slots "
                    "WHERE name = :n AND slot_type = 9 "
                    "AND guid_val IS NOT NULL"
                ),
                {"n": sub},
            ).fetchall()
            if r[0] in frames
        }
        if subframes:
            for r in book.session.execute(
                text(
                    "SELECT obj_guid, name, int64_val FROM slots "
                    "WHERE name IN (:m, :d) AND int64_val IS NOT NULL"
                ),
                {
                    "m": f"{sub}/{BaseGnuCashBook._RECONCILE_INTERVAL_MONTHS}",
                    "d": f"{sub}/{BaseGnuCashBook._RECONCILE_INTERVAL_DAYS}",
                },
            ).fetchall():
                acct = subframes.get(r[0])
                if acct is None:
                    continue
                key = "months" if r[1].endswith("/months") else "days"
                out[acct][key] = int(r[2])
        return out

    @staticmethod
    def _migrate_slot_fillers(book) -> int:
        """Write path only: every slot the ORM wrote before
        2026-09-30 carries piecash's filler columns (``double_val``
        0.0, ``timespec_val`` NULL); GnuCash's SQL backend writes
        NULL and the epoch (``_piecash_shapes`` has the story). Two
        portable UPDATEs over the columns a slot's type does not
        use, verified by re-count. Returns the rows brought along."""
        from sqlalchemy import text

        # KVP_TYPE_DOUBLE = 2 and KVP_TYPE_TIMESPEC = 6 own those
        # columns; their values are data, not filler.
        stale = (
            "SELECT COUNT(*) FROM slots WHERE "
            "(double_val = 0 AND slot_type <> 2) OR "
            "(timespec_val IS NULL AND slot_type <> 6)"
        )
        n = book.session.execute(text(stale)).scalar()
        if not n:
            return 0
        book.session.execute(text(
            "UPDATE slots SET double_val = NULL "
            "WHERE double_val = 0 AND slot_type <> 2"
        ))
        book.session.execute(
            text(
                "UPDATE slots SET timespec_val = :epoch "
                "WHERE timespec_val IS NULL AND slot_type <> 6"
            ),
            {"epoch": "1970-01-01 00:00:00"},
        )
        left = book.session.execute(text(stale)).scalar()
        _verify_none_remaining(left, f"slot filler columns ({n} rows)")
        book.session.expire_all()
        return int(n)

    @staticmethod
    def _write_balance_limit_frame(book, account_guid: str) -> None:
        """The empty ``balance-limit`` frame desktop's account dialog
        leaves on every account it saves: ``gnc_ui_to_account``
        (dialog-account.c) always calls
        ``xaccAccountSetIncludeSubAccountBalances``, which creates
        the frame, and with no limits set nothing goes in it
        (cross-currency twin, 2026-09-30). The account row must be
        flushed first."""
        import uuid

        from piecash.kvp import KVP_Type, Slot

        book.session.execute(
            Slot.__table__.insert().values(
                obj_guid=account_guid, name="balance-limit",
                slot_type=KVP_Type.KVP_TYPE_FRAME, guid_val=uuid.uuid4().hex,
            )
        )
        _verify_composite_write(
            book.session, Slot.__table__,
            {"obj_guid": account_guid, "name": "balance-limit"},
            "balance-limit frame",
        )

    def _write_reconcile_info(self, book, account, statement_date: date) -> None:
        """Record a reconcile the way desktop's window does on
        Finish: remember the interval since the previous statement
        (``_reconcile_interval``; nothing when there was no previous
        date), then set ``last-date`` to the statement date. Rows
        take desktop's frame shape and are updated in place when the
        frame already exists, so a book reconciled from both sides
        keeps one frame. Every raw write is verified."""
        import uuid

        from piecash.kvp import KVP_Type, Slot
        from sqlalchemy import text

        info = self._read_reconcile_info_all(book).get(account.guid)
        prev_date = info["last_date"] if info else None
        prev_interval = (
            (info["months"], info["days"])
            if info and info["months"] is not None and info["days"] is not None
            else None
        )
        label = f"reconcile-info for {account.fullname}"

        def frame_guid(owner: str, name: str) -> str:
            row = book.session.execute(
                text(
                    "SELECT guid_val FROM slots WHERE obj_guid = :o "
                    "AND name = :n AND slot_type = 9"
                ),
                {"o": owner, "n": name},
            ).first()
            if row and row[0]:
                return row[0]
            guid = uuid.uuid4().hex
            book.session.execute(
                Slot.__table__.insert().values(
                    obj_guid=owner, name=name,
                    slot_type=KVP_Type.KVP_TYPE_FRAME, guid_val=guid,
                )
            )
            _verify_composite_write(
                book.session, Slot.__table__,
                {"obj_guid": owner, "name": name}, label,
            )
            return guid

        def put_int64(owner: str, name: str, value: int) -> None:
            exists = book.session.execute(
                text(
                    "SELECT 1 FROM slots WHERE obj_guid = :o AND name = :n"
                ),
                {"o": owner, "n": name},
            ).first()
            if exists:
                book.session.execute(
                    Slot.__table__.update()
                    .where(
                        (Slot.__table__.c.obj_guid == owner)
                        & (Slot.__table__.c.name == name)
                    )
                    .values(slot_type=KVP_Type.KVP_TYPE_GINT64, int64_val=value)
                )
            else:
                book.session.execute(
                    Slot.__table__.insert().values(
                        obj_guid=owner, name=name,
                        slot_type=KVP_Type.KVP_TYPE_GINT64, int64_val=value,
                    )
                )
            _verify_composite_write(
                book.session, Slot.__table__,
                {"obj_guid": owner, "name": name, "int64_val": value}, label,
            )

        f = self._RECONCILE_INFO_FRAME
        frame = frame_guid(account.guid, f)
        # recnFinishCB clears a postponed reconcile first
        # (xaccAccountClearReconcilePostpone): the date and balance a
        # user parked with "Postpone" are spent once the statement is
        # finished. Left in place, desktop's next reconcile window
        # opened preloaded with them (adversarial review 2026-09-30,
        # C23).
        postpone = f"{f}/postpone"
        parked = book.session.execute(
            text(
                "SELECT guid_val FROM slots WHERE obj_guid = :o "
                "AND name = :n AND slot_type = 9"
            ),
            {"o": frame, "n": postpone},
        ).first()
        if parked is not None:
            if parked[0]:
                book.session.execute(
                    Slot.__table__.delete().where(
                        Slot.__table__.c.obj_guid == parked[0]
                    )
                )
                _verify_delete(
                    book.session, Slot.__table__, {"obj_guid": parked[0]},
                    f"postponed {label}",
                )
            book.session.execute(
                Slot.__table__.delete().where(
                    (Slot.__table__.c.obj_guid == frame)
                    & (Slot.__table__.c.name == postpone)
                )
            )
            _verify_delete(
                book.session, Slot.__table__,
                {"obj_guid": frame, "name": postpone},
                f"postponed {label}",
            )
        if prev_date is not None:
            interval = self._reconcile_interval(
                prev_date, statement_date, prev_interval,
            )
            if interval is not None:
                sub = f"{f}/{self._RECONCILE_LAST_INTERVAL}"
                subframe = frame_guid(frame, sub)
                put_int64(
                    subframe, f"{sub}/{self._RECONCILE_INTERVAL_MONTHS}",
                    interval[0],
                )
                put_int64(
                    subframe, f"{sub}/{self._RECONCILE_INTERVAL_DAYS}",
                    interval[1],
                )
        # Desktop stores the statement date as a day-end time64
        # (gnc_time64_get_day_end_gdate); local time, as it does.
        put_int64(
            frame, f"{f}/{self._RECONCILE_LAST_DATE}",
            int(_day_end(statement_date).timestamp()),
        )
        # Finish also records the include-children status (0 unless
        # the dialog's box was ticked); never overwrite a user's 1.
        children_key = f"{f}/include-children"
        if not book.session.execute(
            text("SELECT 1 FROM slots WHERE obj_guid = :o AND name = :n"),
            {"o": frame, "n": children_key},
        ).first():
            put_int64(frame, children_key, 0)

    @staticmethod
    def _migrate_reconcile_conventions(book) -> dict:
        """Write path only: reconciled splits the server dated at local
        midnight move to the statement date's local day-end (desktop's
        reconcile_date), and reconcile-info frames the server wrote
        without ``include-children`` get desktop's 0. Server-dated
        rows are recognized by their time of day; one UPDATE per
        distinct old value, verified by re-count."""
        from datetime import timezone

        from piecash.core.transaction import Split
        from piecash.kvp import KVP_Type, Slot
        from sqlalchemy import text

        out: dict = {}
        olds = [
            r[0] for r in book.session.execute(
                text(
                    "SELECT DISTINCT reconcile_date FROM splits "
                    "WHERE reconcile_state = 'y' AND reconcile_date IS NOT NULL"
                ),
            ).fetchall()
        ]
        moved = 0
        for old in olds:
            if isinstance(old, str):
                old_dt = datetime.fromisoformat(old)
            elif isinstance(old, datetime):
                old_dt = old
            else:
                continue
            as_utc = old_dt if old_dt.tzinfo else old_dt.replace(tzinfo=timezone.utc)
            local = as_utc.astimezone()
            if local.time() != datetime.min.time():
                continue  # not the server's midnight shape
            new = _day_end(local.date())
            book.session.execute(
                Split.__table__.update()
                .where(Split.__table__.c.reconcile_state == "y")
                .where(Split.__table__.c.reconcile_date == as_utc)  # tz-aware: no local shift
                .values(reconcile_date=new)
            )
            left = book.session.execute(
                text(
                    "SELECT COUNT(*) FROM splits WHERE reconcile_state = 'y' "
                    "AND reconcile_date = :old"
                ),
                {"old": old},
            ).scalar()
            _verify_none_remaining(left, f"reconcile_date {old} → day end")
            moved += 1
        if moved:
            out["reconcile_dates_normalized"] = moved

        frames = [
            r[0] for r in book.session.execute(
                text(
                    "SELECT guid_val FROM slots WHERE name = 'reconcile-info' "
                    "AND slot_type = 9 AND guid_val NOT IN ("
                    "SELECT obj_guid FROM slots WHERE name = 'reconcile-info/include-children')"
                ),
            ).fetchall()
        ]
        for frame in frames:
            book.session.execute(
                Slot.__table__.insert().values(
                    obj_guid=frame, name="reconcile-info/include-children",
                    slot_type=KVP_Type.KVP_TYPE_GINT64, int64_val=0,
                )
            )
            _verify_composite_write(
                book.session, Slot.__table__,
                {"obj_guid": frame, "name": "reconcile-info/include-children"},
                "include-children on a reconcile-info frame",
            )
        if frames:
            out["reconcile_frames_completed"] = len(frames)
        return out

    @staticmethod
    def _migrate_split_reconcile_dates(book) -> int:
        """Write path only: an unreconciled split's reconcile_date is
        the epoch (time64 0) on desktop. Two shapes the server left:

        * NULL — every split written before 2026-09-29.
        * The epoch at LOCAL midnight — the receivable/payable split
          of every posted document until 2026-09-30, from a naive
          ``datetime(1970, 1, 1)`` that piecash localized
          (``1970-01-01 08:00:00`` on a Pacific machine; the
          cross-currency invoice twin's one differing column).

        Portable UPDATEs, verified by re-count."""
        from piecash.core.transaction import Split
        from sqlalchemy import text

        # Within a day of the epoch but not the epoch, on a split
        # that is not reconciled: no real reconcile date lives there.
        near = (
            "SELECT COUNT(*) FROM splits WHERE reconcile_state <> 'y' "
            "AND reconcile_date > :lo AND reconcile_date < :hi "
            "AND reconcile_date <> :epoch"
        )
        bounds = {
            "lo": "1969-12-31 00:00:00", "hi": "1970-01-02 00:00:00",
            "epoch": "1970-01-01 00:00:00",
        }
        nulls = book.session.execute(
            text("SELECT COUNT(*) FROM splits WHERE reconcile_date IS NULL")
        ).scalar()
        shifted = book.session.execute(text(near), bounds).scalar()
        if not nulls and not shifted:
            return 0
        if nulls:
            book.session.execute(
                Split.__table__.update()
                .where(Split.__table__.c.reconcile_date.is_(None))
                .values(reconcile_date=_EPOCH)
            )
            left = book.session.execute(
                text("SELECT COUNT(*) FROM splits WHERE reconcile_date IS NULL")
            ).scalar()
            _verify_none_remaining(
                left, f"split reconcile_date fill ({nulls} rows)",
            )
        if shifted:
            book.session.execute(
                text(
                    "UPDATE splits SET reconcile_date = :epoch WHERE "
                    "reconcile_state <> 'y' AND reconcile_date > :lo "
                    "AND reconcile_date < :hi AND reconcile_date <> :epoch"
                ),
                bounds,
            )
            left = book.session.execute(text(near), bounds).scalar()
            _verify_none_remaining(
                left, f"split reconcile_date epoch ({shifted} rows)",
            )
        return int(nulls) + int(shifted)

    def _strip_guid_slots(
        self, book, obj_guids: list[str], label: str, objects=(),
    ) -> None:
        """Delete, by raw SQL, every GUID-valued slot and every frame
        subtree hanging off ``obj_guids`` — BEFORE the owning rows
        are ORM-deleted.

        piecash's ``SlotGUID`` inherits ``SlotFrame.slots``, a
        delete-orphan relation joined on ``obj_guid == guid_val``.
        For a frame that's its children; for a GUID slot it is every
        slot of the REFERENCED entity. ORM-deleting a transaction
        that carries ``from-sched-xaction`` sweeps the schedule's
        slots; deleting a template split that carries
        ``sched-xaction/account`` sweeps the target ACCOUNT's slots
        (notes, apr, designated-account markers). The credit-note
        incident was this cascade through ``invoice-guid``. Stripping
        the GUID and frame rows first leaves the ORM nothing to
        cascade through. Plain string/numeric slots are left to the
        normal cascade. ``objects`` are the ORM owners whose ``slots``
        collections may already be loaded (reading ``txn.notes``
        loads every slot of the transaction, GUID ones included);
        they are expired after the strip so the cascade re-reads an
        empty collection instead of a deleted object.
        """
        from piecash.kvp import KVP_Type, Slot
        from sqlalchemy import text

        frames: list[str] = []
        pending = list(obj_guids)
        while pending:
            rows = book.session.execute(
                text(
                    "SELECT guid_val FROM slots WHERE slot_type = 9 "
                    "AND guid_val IS NOT NULL AND obj_guid IN ("
                    + ",".join(f":g{i}" for i in range(len(pending)))
                    + ")"
                ),
                {f"g{i}": g for i, g in enumerate(pending)},
            ).fetchall()
            pending = [r[0] for r in rows if r[0] not in frames]
            frames.extend(pending)
        for owner in frames:
            book.session.execute(
                Slot.__table__.delete().where(
                    Slot.__table__.c.obj_guid == owner
                )
            )
            _verify_delete(
                book.session, Slot.__table__, {"obj_guid": owner},
                f"{label}: frame children",
            )
        for owner in obj_guids:
            book.session.execute(
                Slot.__table__.delete().where(
                    (Slot.__table__.c.obj_guid == owner)
                    & (Slot.__table__.c.slot_type.in_(
                        [KVP_Type.KVP_TYPE_GUID, KVP_Type.KVP_TYPE_FRAME]
                    ))
                )
            )
            # _verify_delete matches equality filters only; the
            # type-restricted delete is verified by count.
            left = book.session.execute(
                text(
                    "SELECT COUNT(*) FROM slots WHERE obj_guid = :o "
                    "AND slot_type IN (5, 9)"
                ),
                {"o": owner},
            ).scalar()
            if left:
                raise RuntimeError(
                    f"{label}: {left} GUID/frame slot(s) survived delete"
                )
        for obj in objects:
            book.session.expire(obj, ["slots"])

    # xaccTransSetReadOnly's reason on a posting transaction
    # (gncInvoicePostToAccount, gncInvoice.c) — the string desktop
    # shows when a user tries to edit one in the register.
    _POSTING_READ_ONLY_REASON = (
        "Generated from an invoice. Try unposting the invoice."
    )

    @staticmethod
    def _posting_document_id(book, transaction) -> str | None:
        """ID of the document ``transaction`` is the posting record
        of, or None. Read from ``invoices.post_txn``, not from the
        ``trans-read-only`` slot: the column is the link itself, and
        the slot has been overwritten by a void and deleted by an
        unvoid on books that met those paths before they were
        guarded."""
        from sqlalchemy import text
        row = book.session.execute(
            text("SELECT id FROM invoices WHERE post_txn = :guid"),
            {"guid": transaction.guid},
        ).fetchone()
        return row[0] if row else None

    def _refuse_posting_record(self, book, transaction, action: str) -> None:
        """A document's posting transaction is read-only: the one
        refusal every transaction-changing path shares (delete, void,
        replace_splits, every update form).

        The posting transaction IS the document's booked state — its
        A/R or A/P split sits in the document's lot and is what paid
        and due are measured from. Voiding it or rewriting its splits
        left the invoice reading "paid" with no payment on file;
        re-dating it left the document and the ledger disagreeing
        about when it was posted; deleting it stranded the document
        ("posted" yet un-re-postable). GnuCash refuses all of these:
        the transaction carries ``trans-read-only`` and
        ``xaccTransVoid`` ("Refusing to void a read-only
        transaction!") and the register both honor it. There is no
        force override, as there is none in desktop: the way to
        change a posted document is to unpost it. (Adversarial review
        2026-09-30, C4a / C4b / C4c; the delete half dates from 1.4.)
        """
        doc_id = self._posting_document_id(book, transaction)
        if doc_id is not None:
            raise ValueError(
                f"Cannot {action} this transaction: it is the posting "
                f"record for invoice {doc_id}, and read-only. Use "
                f"unpost_document first, change the document, and "
                f"post it again."
            )

    def _require_editable(
        self, book, transaction, key: str, action: str, then: str,
    ) -> None:
        """Gate for every path that EDITS a transaction in place
        (update, replace_splits). Two states are immutable, neither
        with a force override:

        - voided: writing into state='v' splits moves balance sums
          while staying invisible to cash_flow/lots/reconciliation,
          and a later re-void overwrites the void-former-* slots,
          destroying the originals;
        - a document's posting record (``_refuse_posting_record``).
        """
        if any(_is_voided(s) for s in transaction.splits):
            raise ValueError(
                f"Transaction {key} is voided. Use "
                f"unvoid_transaction first, then {then}."
            )
        self._refuse_posting_record(book, transaction, action)

    def _find_transaction(
        self, book: piecash.Book, guid: str
    ) -> piecash.Transaction | None:
        """Find a transaction by GUID or partial GUID prefix.

        Args:
            book: Open piecash book.
            guid: Transaction GUID (full 32-char or 8+ char prefix).

        Raises:
            ValueError: If partial GUID is ambiguous.
        """
        try:
            full_guid = self._resolve_guid("transactions", guid)
        except ValueError as e:
            if "No transaction" in str(e):
                return None
            raise
        # Indexed SQL lookup via SQLAlchemy — the `guid` column is the
        # primary key on the transactions table. Replaces an O(N) Python
        # scan of `book.transactions`.
        from piecash.core.transaction import Transaction

        return (
            book.session.query(Transaction).filter_by(guid=full_guid).first()
        )

    def _find_split(self, book: piecash.Book, guid: str) -> piecash.Split | None:
        """Find a split by GUID or partial GUID prefix.

        Raises:
            ValueError: If partial GUID is ambiguous.
        """
        try:
            full_guid = self._resolve_guid("splits", guid)
        except ValueError as e:
            if "No split" in str(e):
                return None
            raise
        # Indexed SQL lookup — replaces an O(N*M) scan over every
        # transaction's splits list.
        from piecash.core.transaction import Split

        return book.session.query(Split).filter_by(guid=full_guid).first()

    @staticmethod
    def _require_default_currency(book: piecash.Book) -> piecash.Commodity:
        """Get the book's default currency, raising a clear error if missing."""
        dc = book.default_currency
        if dc is None:
            raise ValueError(
                "Book has no default currency set. In GnuCash desktop, "
                "set the default currency under Preferences > Accounts "
                "(Edit menu on Linux/Windows, GnuCash menu on macOS), "
                "or pass the currency parameter explicitly."
            )
        return dc

    def _find_commodity(
        self, book: piecash.Book, mnemonic: str, namespace: str = "CURRENCY"
    ) -> piecash.Commodity | None:
        """Find a commodity by mnemonic and namespace.

        Shared finder — used by create_account (core) and by the full
        commodities/prices surface in InvestmentsMixin.
        """
        try:
            return book.commodities.get(mnemonic=mnemonic, namespace=namespace)
        except KeyError:
            return None

    def _get_or_create_currency(
        self, book: piecash.Book, mnemonic: str
    ) -> piecash.Commodity:
        """Get an existing currency or create it from ISO code.

        Uses book.currencies which has a built-in fallback that auto-creates
        currencies from the ISO 4217 table if they don't already exist.

        Raises:
            ValueError: If mnemonic is not a valid ISO 4217 currency code.
        """
        try:
            return book.currencies(mnemonic=mnemonic)
        except (KeyError, ValueError) as e:
            raise ValueError(f"Invalid currency code '{mnemonic}': {e}") from e

    def _collect_descendants(self, account, result: set) -> None:
        """Recursively collect all descendant accounts."""
        for child in account.children:
            result.add(child)
            self._collect_descendants(child, result)

    def _template_account_guids(self, book: piecash.Book) -> set[str]:
        """GUIDs for every account in the scheduled-transaction
        template subtree (``book.root_template`` and descendants).

        GnuCash persists SX split templates as real Account rows that
        piecash surfaces in ``book.accounts`` — scaffolding, not the
        user's chart. Every user-facing account iteration filters
        this set out. Empty set when the book has no template root.
        """
        rt = book.root_template
        if rt is None:
            return set()
        guids = {rt.guid}
        descendants: set = set()
        self._collect_descendants(rt, descendants)
        guids.update(a.guid for a in descendants)
        return guids

    def _account_suggestions(
        self, book: piecash.Book, ref: str, limit: int = 3,
    ) -> list[str]:
        """Closest real account fullnames to a failed ref.

        Prefix/substring hits rank first — the common near-miss is
        the right branch with a wrong or truncated leaf
        ("Expenses:Insurance:Auto" for
        "Expenses:Insurance:Auto Insurance") — then difflib
        similarity for typos. Error-path only: costs nothing on
        successful resolution.
        """
        import difflib

        template_guids = self._template_account_guids(book)
        names = [
            a.fullname for a in book.accounts
            if a.guid not in template_guids
        ]
        ref_l = ref.lower()
        subs = [
            n for n in names
            if n.lower().startswith(ref_l) or ref_l in n.lower()
        ]
        close = difflib.get_close_matches(ref, names, n=limit, cutoff=0.6)
        out: list[str] = []
        for n in subs + close:
            if n not in out:
                out.append(n)
        return out[:limit]

    def _account_not_found_error(
        self, book: piecash.Book, ref: str,
    ) -> ValueError:
        """`Account not found` with did-you-mean suggestions.

        Every wrong path guess in a batch is a rejected row and a
        retry round-trip (live bookkeeper friction, 2026-07-24);
        three candidates turn the retry into a one-shot correction.
        """
        msg = f"Account not found: {ref}"
        suggestions = self._account_suggestions(book, ref)
        if suggestions:
            listed = ", ".join(f"'{s}'" for s in suggestions)
            msg += (
                f". Did you mean: {listed}? "
                f"(list_accounts(query=...) to browse.)"
            )
        return ValueError(msg)

    @staticmethod
    def _placeholder_error(account) -> ValueError:
        """Placeholder rejection that names postable children."""
        kids = [
            c.fullname for c in account.children if not c.placeholder
        ]
        msg = (
            f"Account '{account.fullname}' is a placeholder and "
            f"cannot receive splits"
        )
        if kids:
            shown = ", ".join(f"'{k}'" for k in kids[:3])
            more = f" (+{len(kids) - 3} more)" if len(kids) > 3 else ""
            msg += f" — post to one of its children: {shown}{more}"
        return ValueError(msg)

    @staticmethod
    def _preload_split_graph(book, *, account_splits: bool = True) -> None:
        """Bulk-load accounts, transactions and their split and slot
        collections, so that later traversals of ``txn.splits``,
        ``txn.notes`` (slot-backed), ``split.transaction`` and
        ``split.account`` resolve in memory instead of lazy-loading per
        row. Intended for whole-book reports; a single-account lookup
        would load rows it never touches.

        ``account_splits=False`` skips the ``Account.splits`` pass — a
        second full read of the splits table that only a caller
        walking ``account.splits`` needs (``get_book_summary`` does;
        a transaction listing or search does not). A later call in
        the same open that does need it upgrades the parked graph
        rather than returning early.

        The loaded rows are parked on the book deliberately: SQLAlchemy's
        identity map holds them only weakly, so without a strong
        reference they would be collected immediately and every traversal
        would query again. The reference lives as long as the book, i.e.
        one ``open()`` context.

        Deliberate tradeoff: the whole split graph is held in memory
        for the duration of the call — tens of MB on a tens-of-
        thousands-of-splits book. That is the price of the report
        completing at all at that scale (lazy loading was a query per
        transaction), and the biggest books are exactly the ones that
        need it, so there is no size cutoff.
        """
        from piecash.core.account import Account
        from piecash.core.transaction import Transaction
        from sqlalchemy.orm import selectinload

        parked = getattr(book, "_gnucash_mcp_split_graph", None)
        if parked is not None:
            _accounts, transactions, has_account_splits = parked
            if has_account_splits or not account_splits:
                return
            accounts = (
                book.session.query(Account)
                .options(selectinload(Account.splits))
                .all()
            )
            book._gnucash_mcp_split_graph = (accounts, transactions, True)
            return

        acct_q = book.session.query(Account)
        if account_splits:
            acct_q = acct_q.options(selectinload(Account.splits))
        accounts = acct_q.all()
        # Slots ride along: ``txn.notes`` is slot-backed, and the
        # compact renderer and the notes search read it per row.
        # ``of_type(with_polymorphic)`` matters: piecash's Slot is
        # single-table polymorphic, and a plain ``selectinload``
        # fetches only the base columns, leaving one SELECT per
        # slot for its typed value (``string_val``). Loading every
        # subclass column in the IN-query makes the walk free.
        transactions = (
            book.session.query(Transaction)
            .options(
                selectinload(Transaction.splits),
                selectinload(Transaction.slots.of_type(_all_slot_columns())),
            )
            .all()
        )
        book._gnucash_mcp_split_graph = (
            accounts, transactions, account_splits,
        )

    @staticmethod
    def _preload_account_transactions(book, account) -> list:
        """Warm ONE account's transaction rows (with their split
        and slot collections) in three indexed queries, so a later walk over
        ``account.splits`` that touches ``split.transaction`` or
        ``txn.splits`` resolves in memory instead of one SELECT per
        row. The account-scoped sibling of
        ``_preload_split_graph`` (which loads the whole book and is
        wrong for a single-account tool).

        Returns the loaded rows; THE CALLER MUST HOLD THE RETURNED
        LIST for as long as the walk runs — the identity map holds
        rows only weakly, and without a strong reference they are
        collected immediately and every access queries again. First
        built inline for enter_statement (release-review finding 8);
        hoisted when the whole-tree review found the reconciliation
        tools paying the same per-split SELECT.
        """
        from piecash.core.transaction import Split, Transaction
        from sqlalchemy.orm import selectinload

        # selectinload: the register renderer and the statement
        # candidate scan read txn.splits per transaction, and
        # ``txn.notes`` is slot-backed (one slots SELECT per row) —
        # two IN-queries here instead of two SELECTs per row there.
        return (
            book.session.query(Transaction)
            .join(Split, Split.transaction_guid == Transaction.guid)
            .filter(Split.account_guid == account.guid)
            .options(
                selectinload(Transaction.splits),
                selectinload(Transaction.slots.of_type(_all_slot_columns())),
            )
            .distinct()
            .all()
        )

    @staticmethod
    def _is_template_transaction(txn, template_guids: set) -> bool:
        """True iff ``txn`` is a scheduled-transaction template recipe.

        GnuCash desktop persists each SX recipe as a real Transaction
        whose splits post under ``root_template`` (our own SX path
        uses a splits-json slot instead). Unfiltered, they render
        indistinguishably from real events. Single predicate —
        companion to ``_template_account_guids``.
        """
        return any(
            s.account.guid in template_guids for s in txn.splits
        )

    @staticmethod
    def _own_splits_balance(account, as_of: "date | None" = None):
        """Balance of the account's OWN splits in its own commodity.

        The one rule every own-splits sum shares:

        - voided splits are excluded by **state**, not value. A
          well-formed void contributes 0 either way; the corrupted
          partial-void shape (``state='v'`` with non-zero values,
          producible by legacy data or desktop edits) must not move
          balances when the same split is invisible to cash_flow /
          lots / reconciliation counts.
        - ``as_of`` (inclusive) caps to posted-by-then transactions;
          ``None`` means no date bound — callers that intentionally
          include future-dated transactions pass nothing.
        - null ``post_date`` rows (an old-book artifact) are
          excluded — same rule ``_query_filtered_splits`` applies,
          so this sum agrees with the SQL-backed reports.
        """
        balance = Decimal("0")
        for split in account.splits:
            if _is_voided(split):
                continue
            post_date = split.transaction.post_date
            if post_date is None:
                continue
            if as_of is not None and post_date > as_of:
                continue
            balance += split.quantity
        return balance
