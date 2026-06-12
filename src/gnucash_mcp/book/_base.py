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
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Generator, Iterable

import piecash

# Re-exported for callers that still import these from ``_base``.
# Canonical definitions live in ``_currency`` alongside the
# CurrencyMixin that uses them; keeping the import path stable avoids
# churn across the codebase.
from gnucash_mcp.book._currency import (  # noqa: F401
    CurrencyMixin,
    _is_market_price,
    _to_date,
)
from gnucash_mcp.book._query import QueryMixin

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


def _to_decimal(value) -> Decimal:
    """Safe Decimal construction for user-supplied monetary values.

    Routes through ``Decimal(str(value))`` so a float that slipped
    past the pydantic boundary decimalizes via shortest-repr
    (``str(94.87) == "94.87"``) instead of embedding the IEEE-754
    epsilon and breaking the sum-to-zero check. Exact inputs
    (str/int/Decimal) round-trip unchanged.

    Use everywhere user-supplied money hits ``Decimal(...)`` —
    direct callers (tests, scripts) bypass the pydantic coercion.
    """
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


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
    return {
        "guid": account.guid,
        "name": account.name,
        "fullname": account.fullname,
        "type": account.type,
        "commodity": account.commodity.mnemonic if account.commodity else None,
        "description": account.description or "",
        "placeholder": bool(account.placeholder),
    }


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

    if annotations:
        return f"{fullname} [{', '.join(annotations)}]"
    return fullname


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
        "reconcile_state": split.reconcile_state,
        "reconcile_date": rec_date.isoformat() if rec_date else None,
        "lot_guid": lot_guid,
    }


def _split_to_compact_dict(split: piecash.Split) -> dict:
    """Tight serialization of a Split for history / audit contexts
    (~40-60 chars vs ~140 for the full dict).

    Emits ``account`` and ``value`` always; ``quantity`` only when
    cross-currency, ``memo`` / ``reconcile_state`` only when
    non-default. Omits ``guid`` (the described splits are gone —
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
            for s in transaction.splits
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
    name = entry.get("fullname", "")
    parts = [prefix, name]
    lp = entry.get("latest_price")
    if lp:
        parts.append(f"{lp['value']} {lp['currency']} ({lp['date']})")
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
    desc = split_dict["description"]
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
    account_name = split.account.fullname
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
    desc = transaction.description
    splits = list(transaction.splits)

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
        line += f"\t{transaction.notes}"
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
    title = lot_dict["title"]
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
    name = sx_dict["name"]
    freq = sx_dict["frequency"]
    if not sx_dict.get("enabled"):
        status = "disabled"
    elif sx_dict.get("next_occurrence"):
        status = f"next:{sx_dict['next_occurrence']}"
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
    name = entry["name"]
    occ_date = entry["occurrence_date"]
    days = entry["days_until"]
    amount = entry["amount"]
    return f"{short}\t{name}\t{occ_date}\t{days} days\t{amount}"


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
    _GUID_TABLE_QUERIES: dict[str, str] = {
        "transactions": "SELECT guid FROM transactions WHERE guid LIKE ?",
        "splits": "SELECT guid FROM splits WHERE guid LIKE ?",
        "accounts": "SELECT guid FROM accounts WHERE guid LIKE ?",
        "lots": "SELECT guid FROM lots WHERE guid LIKE ?",
        "schedxactions": "SELECT guid FROM schedxactions WHERE guid LIKE ?",
        "commodities": "SELECT guid FROM commodities WHERE guid LIKE ?",
        "budgets": "SELECT guid FROM budgets WHERE guid LIKE ?",
        "customers": "SELECT guid FROM customers WHERE guid LIKE ?",
        "vendors": "SELECT guid FROM vendors WHERE guid LIKE ?",
        "invoices": "SELECT guid FROM invoices WHERE guid LIKE ?",
        "prices": "SELECT guid FROM prices WHERE guid LIKE ?",
        "entries": "SELECT guid FROM entries WHERE guid LIKE ?",
    }
    _GUID_TABLES = frozenset(_GUID_TABLE_QUERIES.keys())

    def __init__(self, book_path: str):
        """Initialize with path to GnuCash SQLite book.

        Args:
            book_path: Path to the GnuCash SQLite file.

        Raises:
            FileNotFoundError: If the book path doesn't exist.
        """
        # Resolve to an absolute path: audit/debug/backup dirs derive
        # from book_path.parent, and an unresolved ``..`` would write
        # them outside the intended directory. resolve(strict=True)
        # also covers the existence check.
        try:
            self.book_path = Path(book_path).resolve(strict=True)
        except FileNotFoundError:
            raise FileNotFoundError(f"GnuCash book not found: {book_path}")
        if not self.book_path.is_file():
            raise FileNotFoundError(
                f"GnuCash book path is not a regular file: {book_path}"
            )
        # Thread-local staging buffer for audit-log before_state:
        # write methods stage on their open session; @audit_log
        # consumes after the tool returns — no second book open.
        self._audit_tls = threading.local()
        # GUID-prefix-map caches, ``(mtime_ns, dict)`` — SQLite
        # touches the file on every commit, so mtime_ns invalidates
        # on any write by this server or another process.
        self._txn_prefix_cache: tuple[int, dict[str, str]] | None = None
        self._split_prefix_cache: tuple[int, dict[str, str]] | None = None
        self._lot_prefix_cache: tuple[int, dict[str, str]] | None = None

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
        normalized) before touching the database. Raw read-only
        SQLite — no piecash session needed.

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

        conn = sqlite3.connect(f"file:{self.book_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                self._GUID_TABLE_QUERIES[table],
                (partial + "%",),
            ).fetchall()
        finally:
            conn.close()

        if len(rows) == 0:
            raise ValueError(f"No {table[:-1]} found matching GUID prefix: {partial}")
        if len(rows) > 1:
            matches = [r[0] for r in rows]
            raise ValueError(
                f"Ambiguous GUID prefix '{partial}' matches {len(rows)} {table}: "
                f"{', '.join(m[:12] + '...' for m in matches)}"
            )
        return rows[0][0]

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
        last_error = None
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                book = piecash.open_book(str(self.book_path), readonly=readonly, do_backup=False)
                open_elapsed = (time.time() - start_time) * 1000
                debug_logger.debug(
                    f"Book opened (readonly={readonly}) in {open_elapsed:.0f}ms"
                )
                try:
                    yield book
                    return
                finally:
                    close_start = time.time()
                    book.close()
                    close_elapsed = (time.time() - close_start) * 1000
                    debug_logger.debug(f"Book closed in {close_elapsed:.0f}ms")
            except sqlite3.OperationalError as e:
                last_error = e
                error_msg = str(e).lower()
                if "locked" in error_msg or "busy" in error_msg:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (attempt + 1))
                        continue
                    raise GnuCashLockError(
                        f"GnuCash book is locked (possibly by GnuCash or another process). "
                        f"Close GnuCash and try again. Details: {e}"
                    ) from e
                raise

        if last_error:
            raise last_error

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

    def _transaction_prefix_map(
        self, book: piecash.Book
    ) -> dict[str, str]:
        """Return the full-table transaction-GUID prefix map, cached.

        Several read paths emit short prefixes that must be
        collision-safe against ``_resolve_guid``'s table-wide LIKE
        lookup; this shares one build. Cache invariant: correct
        until any transaction mutates — SQLite bumps the file mtime
        on every commit, so ``st_mtime_ns`` is a sufficient proxy
        for writes by this process or any other.
        """
        mtime_ns = self.book_path.stat().st_mtime_ns
        if (
            self._txn_prefix_cache is not None
            and self._txn_prefix_cache[0] == mtime_ns
        ):
            return self._txn_prefix_cache[1]
        prefix_map = _guid_prefix_map(t.guid for t in book.transactions)
        self._txn_prefix_cache = (mtime_ns, prefix_map)
        return prefix_map

    def _split_prefix_map(
        self, book: piecash.Book
    ) -> dict[str, str]:
        """Return the full-table split-GUID prefix map, cached.

        Same mtime-keyed pattern as ``_transaction_prefix_map``.
        """
        mtime_ns = self.book_path.stat().st_mtime_ns
        if (
            self._split_prefix_cache is not None
            and self._split_prefix_cache[0] == mtime_ns
        ):
            return self._split_prefix_cache[1]
        prefix_map = _guid_prefix_map(
            s.guid for t in book.transactions for s in t.splits
        )
        self._split_prefix_cache = (mtime_ns, prefix_map)
        return prefix_map

    def _lot_prefix_map(
        self, book: piecash.Book
    ) -> dict[str, str]:
        """Return the full-table lot-GUID prefix map, cached.

        Same mtime-keyed pattern as ``_transaction_prefix_map``.
        """
        mtime_ns = self.book_path.stat().st_mtime_ns
        if (
            self._lot_prefix_cache is not None
            and self._lot_prefix_cache[0] == mtime_ns
        ):
            return self._lot_prefix_cache[1]
        prefix_map = _guid_prefix_map(
            lot.guid for acct in book.accounts for lot in acct.lots
        )
        self._lot_prefix_cache = (mtime_ns, prefix_map)
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
