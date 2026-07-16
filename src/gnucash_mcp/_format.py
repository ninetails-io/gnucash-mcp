"""Cross-layer formatting helpers.

These shape numeric and list output the same way regardless of who's
emitting them — the book layer producing display-ready dicts, the tool
layer wrapping responses, the audit log rendering text. Both layers
import from here; ``book/`` and ``tools/`` stay decoupled.

Two pieces:

- :func:`_format_number` — single chokepoint for currency / percentage
  / share-quantity rounding. Ad-hoc ``str(decimal)`` calls leak
  26-digit Decimal arithmetic into responses.
- :func:`_apply_limit` — generalized truncation + notice helper.
  Same contract as ``CoreMixin._truncation_notice`` but
  parameterized so any tool can
  plug in its own entity name.
"""

import calendar
import os
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import TypeVar


# ── Period bucketing (group_by) ────────────────────────────────────

# Valid group_by sub-period granularities for the period breakdowns
# (spending_by_category, income_by_source, cash_flow,
# vendor_spending_report). Shared so every consumer rejects the same
# set and the column-label vocabulary stays in one place.
_GROUP_BY_VALUES = ("month", "quarter", "year")


def _period_label(d: date, group_by: str) -> str:
    """Map a date to its sub-period column label.

    ``month`` → ``YYYY-MM``; ``quarter`` → ``YYYY-Q#``; ``year`` →
    ``YYYY``. Used both to enumerate columns and to bucket each
    record by its date, so the two always agree.
    """
    if group_by == "month":
        return f"{d.year:04d}-{d.month:02d}"
    if group_by == "quarter":
        return f"{d.year:04d}-Q{(d.month - 1) // 3 + 1}"
    return f"{d.year:04d}"


def _enumerate_periods(
    start_date: date, end_date: date, group_by: str
) -> list[tuple[str, date]]:
    """Ordered ``(label, anchor_date)`` pairs covering the range.

    Every sub-period that overlaps ``[start_date, end_date]`` gets a
    column, including empty ones (so a gap month still renders a
    ``0.00`` column). The anchor date is the period's natural
    calendar end clamped to ``end_date`` — FX/commodity rates value
    each period at its own close, never at today's rates.
    """
    periods: list[tuple[str, date]] = []
    if group_by == "month":
        y, m = start_date.year, start_date.month
        while (y, m) <= (end_date.year, end_date.month):
            last = date(y, m, calendar.monthrange(y, m)[1])
            periods.append((f"{y:04d}-{m:02d}", min(last, end_date)))
            m += 1
            if m > 12:
                m, y = 1, y + 1
    elif group_by == "quarter":
        y = start_date.year
        q = (start_date.month - 1) // 3 + 1
        end_q = (end_date.month - 1) // 3 + 1
        while (y, q) <= (end_date.year, end_q):
            last_month = q * 3
            last = date(y, last_month, calendar.monthrange(y, last_month)[1])
            periods.append((f"{y:04d}-Q{q}", min(last, end_date)))
            q += 1
            if q > 4:
                q, y = 1, y + 1
    else:  # year
        for y in range(start_date.year, end_date.year + 1):
            periods.append((f"{y:04d}", min(date(y, 12, 31), end_date)))
    return periods


def _partial_period_labels(
    start_date: date, end_date: date, group_by: str
) -> set[str]:
    """Labels of sub-periods the report range only partially covers.

    Only the first and last enumerated periods can be partial.
    Derived from ``_period_label`` itself instead of a third copy of
    the calendar-boundary ladder: the range starts mid-period exactly
    when the day BEFORE ``start_date`` still carries the same label,
    and ends mid-period exactly when the day AFTER ``end_date`` does.
    Provably consistent with the bucketing function by construction —
    a new ``group_by`` granularity added to ``_period_label`` is
    covered here with no edit. Rendered with a ``*`` marker so a
    half-month column isn't silently read as a small full month (and
    so the Avg column's drag from a partial period is visible).
    """
    partial: set[str] = set()
    if _period_label(start_date - timedelta(days=1), group_by) \
            == _period_label(start_date, group_by):
        partial.add(_period_label(start_date, group_by))
    if _period_label(end_date + timedelta(days=1), group_by) \
            == _period_label(end_date, group_by):
        partial.add(_period_label(end_date, group_by))
    return partial


def _mark_partial(
    period_labels: list[str], partial_labels: set[str] | None
) -> list[str]:
    """Header labels with ``*`` appended to partial periods."""
    if not partial_labels:
        return list(period_labels)
    return [
        f"{pl}*" if pl in partial_labels else pl
        for pl in period_labels
    ]


_PARTIAL_FOOTNOTE = "(* period only partially covered by the date range)"


def _strip_common_prefix(names: list[str]) -> list[str]:
    """Drop a shared top-level ``Expenses:`` / ``Income:`` prefix.

    Leaf labels stay readable when every row shares a prefix; full
    paths survive a heterogeneous mix (or names without a ``:`` at
    all, e.g. vendor names).
    """
    if names and ":" in names[0]:
        candidate = names[0].split(":")[0] + ":"
        if all(n.startswith(candidate) for n in names):
            return [n[len(candidate):] for n in names]
    return list(names)


def _format_grouped_tsv(
    *,
    period_labels: list[str],
    displayed_names: list[str],
    totals: dict[str, dict[str, Decimal]],
    row_totals: dict[str, Decimal],
    period_totals: dict[str, Decimal],
    grand_total: Decimal,
    excluded: list[tuple[str, Decimal]],
    label: str,
    partial_labels: set[str] | None = None,
) -> str:
    """Render an entity × sub-period breakdown as a TSV table.

    Columns: the leading ``label``, one per sub-period, then ``Total``
    (sum across periods) and ``Avg`` (Total / number of periods). The
    trailing ``TOTAL`` row sums every entity per period —
    net-negative-overall entities are netted in here even though they
    get no row of their own, keeping the column totals tied to
    single-period mode. ``excluded`` (entities dropped from the rows)
    drives an explanatory footnote when non-empty.

    Shared by the category/source breakdowns and vendor spending; the
    cash-flow trend uses its own fixed-row formatter.
    """
    num_periods = len(period_labels)
    leaves = _strip_common_prefix(displayed_names)
    header_labels = _mark_partial(period_labels, partial_labels)
    lines = ["\t".join([label, *header_labels, "Total", "Avg"])]
    for name, leaf in zip(displayed_names, leaves):
        per = totals.get(name, {})
        cells = [leaf]
        cells += [f"{per.get(pl, Decimal('0')):.2f}" for pl in period_labels]
        tot = row_totals[name]
        avg = tot / num_periods if num_periods else Decimal("0")
        cells += [f"{tot:.2f}", f"{avg:.2f}"]
        lines.append("\t".join(cells))

    avg_total = grand_total / num_periods if num_periods else Decimal("0")
    total_cells = ["TOTAL"]
    total_cells += [f"{period_totals[pl]:.2f}" for pl in period_labels]
    total_cells += [f"{grand_total:.2f}", f"{avg_total:.2f}"]
    lines.append("\t".join(total_cells))

    out = "\n".join(lines)
    if partial_labels:
        out += f"\n{_PARTIAL_FOOTNOTE}"
    if excluded:
        netted = ", ".join(
            f"{n} {t:,.2f}"
            for n, t in sorted(excluded, key=lambda x: x[1])
        )
        out += (
            f"\n({len(excluded)} net-negative netted into TOTAL: {netted})"
        )
    return out


# ── Batch-entry TSV layout ─────────────────────────────────────────
#
# Shared by the tool-layer parser (tools/core.py) and the audit-log
# display parser (logging_config.py) so the two can never drift on
# what a submitted batch means.


# Canonical names for split-column header tokens. Trailing digits
# are stripped before lookup (``amt1`` → ``amt``), so numbered and
# unnumbered headers both work.
_BATCH_SPLIT_TOKENS = {
    "amt": "amount", "amount": "amount",
    "acct": "account", "account": "account", "acc": "account",
    "memo": "memo",
    "qty": "quantity", "quantity": "quantity",
}

# Fallback shape for the audit log's display parser when a stored
# submission's header no longer validates (it renders rows as
# pairs rather than crashing log rendering).
_BATCH_LEGACY_GROUP = ("amount", "account")


def _batch_tsv_layout(header_line: str) -> dict:
    """Column layout of a batch-entry TSV, derived from its header.

    Fixed prefix ``ref, date, description``, an optional ``notes``
    column, then split columns. The split-group field sequence is
    whatever the header's FIRST group declares (``amt, acct`` pairs;
    ``amt, acct, memo`` triples; ``amt, acct, qty``;
    ``amt, acct, memo, qty`` — in any intra-group order): the header
    is the schema, for field order too.

    EVERY column name is validated. Now that the header is
    load-bearing, an unknown or typo'd token (``meno1``,
    ``currency2``) must fail on the format — silently falling back
    to positional pairs would misparse the row and surface a raw
    decimal error on whatever landed in an amount slot (bookkeeper
    finding, 1.4.1 validation round).

    Returns ``{"has_notes": bool, "group": tuple[str, ...]}`` with
    ``group`` in canonical names (``amount`` / ``account`` /
    ``memo`` / ``quantity``).

    Raises ValueError naming the offending column on any unknown
    token, a wrong fixed prefix, or a first group missing
    amount/account.
    """
    raw_tokens = [t.strip().lower() for t in header_line.split("\t")]
    # Tolerate trailing empty cells (a trailing tab on the header).
    while raw_tokens and not raw_tokens[-1]:
        raw_tokens.pop()
    tokens = [t.rstrip("0123456789") for t in raw_tokens]

    if len(tokens) < 3 or tokens[0] != "ref" or tokens[1] != "date" \
            or tokens[2] not in ("description", "desc"):
        raise ValueError(
            "batch header must start with ref, date, description "
            f"— got {', '.join(raw_tokens[:3]) or '(empty header)'}"
        )
    has_notes = len(tokens) > 3 and tokens[3] == "notes"
    start = 4 if has_notes else 3

    canonical: list[str] = []
    for raw, token in zip(raw_tokens[start:], tokens[start:]):
        name = _BATCH_SPLIT_TOKENS.get(token)
        if name is None:
            raise ValueError(
                f"unrecognized column {raw!r} in batch header — "
                f"columns are ref, date, description, notes, then "
                f"amt, acct, memo, qty split groups"
            )
        canonical.append(name)

    # The first group runs until a field repeats; later groups are
    # not order-checked (rows are chunked by the first group's
    # shape), but every token was validated above.
    group: list[str] = []
    for name in canonical:
        if name in group:
            break
        group.append(name)
    if "amount" not in group or "account" not in group:
        raise ValueError(
            "split columns must include both an amount and an "
            "account column"
        )
    return {"has_notes": has_notes, "group": tuple(group)}


def _batch_row_splits(rest: list[str], group: tuple[str, ...]) -> list[dict]:
    """Chunk a batch row's trailing fields into split dicts.

    ``group`` is the per-split field sequence from
    ``_batch_tsv_layout``. ``amount`` and ``account`` are always
    present; ``memo`` / ``quantity`` cells may be empty (the key is
    included only when non-empty, so plain splits keep their shape
    downstream — and an empty qty means "account commodity equals
    the transaction currency", the same-currency default).

    Raises ValueError when the count doesn't divide evenly; the
    message names the declared shape and calls out the likely
    culprit (a dropped trailing tab on an empty final cell).
    """
    width = len(group)
    if len(rest) % width != 0:
        shape = ", ".join(group)
        hint = (
            " A split with an empty trailing cell still needs "
            "its tab." if width > 2 else ""
        )
        raise ValueError(
            f"trailing fields must be ({shape}) groups per the "
            f"header — got {len(rest)} fields.{hint}"
        )
    splits: list[dict] = []
    for j in range(0, len(rest), width):
        split: dict = {}
        for k, field in enumerate(group):
            cell = rest[j + k]
            if field in ("amount", "account"):
                split[field] = cell
            elif cell.strip():
                split[field] = cell.strip()
        splits.append(split)
    return splits


# ── Numeric formatting ─────────────────────────────────────────────


def _format_number(
    value,
    decimals: int = 2,
    strip_trailing: bool = False,
) -> str:
    """Format a numeric value with consistent precision, e.g.
    ``_format_number(Decimal("1234.567"))`` → ``'1234.57'``.

    Args:
        value: Decimal, str, int, float, or None. None and empty
            strings render as zero so omission and zero look
            identical in the response.
        decimals: 2 for currency/percentages, 4 for share
            quantities, 6 for crypto.
        strip_trailing: Drop trailing zeros (``"1.50"`` → ``"1.5"``).
            Default False — currency-style fixed precision.

    Returns:
        Decimal string ready for JSON/TSV. Non-numeric input passes
        through as ``str(value)`` so callers needn't pre-validate.
    """
    if value is None or value == "":
        if strip_trailing:
            return "0"
        return f"{Decimal(0):.{decimals}f}"

    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        # Free-form strings ("N/A") pass through unchanged.
        return str(value)

    quantum = Decimal(1).scaleb(-decimals)  # 0.01 for decimals=2
    rounded = d.quantize(quantum, rounding=ROUND_HALF_UP)

    if strip_trailing:
        s = format(rounded, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"

    return format(rounded, "f")


# ── Path display ───────────────────────────────────────────────────


def _book_display_name(book_path) -> str:
    """Render a book path as filename only — no directory leakage.

    Routine LLM-visible responses must not carry the full book path:
    it leaks username and home-directory layout into every
    transcript, and the filename alone verifies *which* book is
    loaded. Always-on, unlike the opt-in ``redact_paths`` (which
    targets error-message paths where the directory can be
    load-bearing debugging signal). Falsy input → ``"not set"``.
    """
    if not book_path:
        return "not set"
    return os.path.basename(str(book_path))


# ── Limit enforcement ──────────────────────────────────────────────


T = TypeVar("T")


def _apply_limit(
    items: list[T],
    limit: int | None,
    default: int = 50,
    max_cap: int = 250,
    entity_name: str = "items",
    suggest_narrow: bool = False,
) -> tuple[list[T], str | None]:
    """Truncate ``items`` to a server-safe limit and emit a notice.

    Three notice cases:

    1. **Truncated** — return the slice with a
       ``[Showing N of M ...]`` message.
    2. **Capped only** — ``limit > max_cap`` but everything fits;
       the ``[Limit capped at N]`` notice says the over-limit had
       no effect.
    3. **Fits naturally** — everything, no notice.

    Args:
        limit: Falsy values fall back to ``default`` (50, the
            list_transactions convention); values above ``max_cap``
            (250) clamp with the cap notice.
        entity_name: Plural noun for the notice ("splits",
            "invoices").
        suggest_narrow: Mention "narrow filters" first in the hint —
            for tools where narrowing beats raising the limit.

    Returns:
        ``(truncated, notice)``; ``notice`` is None only in case 3.
    """
    if not limit or limit < 1:
        limit = default
    capped = limit > max_cap
    effective = min(limit, max_cap)
    total = len(items)

    if total > effective:
        truncated = items[:effective]
        shown = len(truncated)
        if capped:
            return truncated, (
                f"[Showing {shown} of {total} {entity_name} — "
                f"limit capped at {effective}; narrow filters for "
                f"complete results]"
            )
        hint = (
            "narrow filters or set limit= higher"
            if suggest_narrow
            else "set limit= higher"
        )
        return truncated, (
            f"[Showing {shown} of {total} {entity_name} — {hint}]"
        )

    if capped:
        return items, f"[Limit capped at {effective}]"

    return items, None


# ── Pagination ─────────────────────────────────────────────────────


def _iso_date(value) -> str | None:
    """Normalize a ``date`` / ``datetime`` / ISO-string to ``YYYY-MM-DD``.

    The pagination indicator's date range needs day precision only.
    piecash hands back ``datetime`` for some columns (``post_date``) and
    ``date`` for others, and several callers already carry ISO strings
    (split dicts, backup timestamps) — all normalize here. ``None``
    passes through so absent dates drop out of a range.
    """
    if value is None:
        return None
    if isinstance(value, str):
        # Already ISO (possibly with a time component) — keep the date.
        return value[:10]
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _range_suffix(rows, date_key) -> str:
    """Build the `` (earliest to latest)`` parenthetical over ``rows``.

    Empty when ``date_key`` is None (undated entity) or no row carries a
    date. The caller decides which rows to span — the current page (for
    navigation) or the full set (for the count-only "scope" query).
    """
    if date_key is None:
        return ""
    isos = [_iso_date(date_key(r)) for r in rows]
    isos = [d for d in isos if d]
    if not isos:
        return ""
    return f" ({min(isos)} to {max(isos)})"


def _paginate(
    items: list[T],
    offset: int = 0,
    limit: int | None = None,
    default: int = 50,
    max_cap: int = 250,
    entity_name: str = "items",
    date_key=None,
) -> tuple[list[T], str]:
    """Slice ``items`` to one page and build a ``Showing X-Y of Z`` line.

    Unlike :func:`_apply_limit`, the indicator is **always** returned
    (never None) and is meant to be the FIRST line of the response — the
    LLM learns the view is partial *before* reading any rows. A silent
    truncation is exactly the failure this prevents (a reconciliation
    session once missed 16 of 109 transactions to a hidden cap).

    The date range spans the **current page**, not the full set: on a
    chronological list it's the actionable navigation signal ("rows
    251-500 cover Nov–Dec; I need May, so jump ahead"). The exception is
    count-only / overshoot mode, where there is no page and the range
    spans the full set — the "what's the scope?" query.

    Args:
        items: The FULL filtered, sorted result set. Slicing happens
            here; ``len(items)`` is the authoritative total — count
            before paginating, never after.
        offset: 0-indexed first row. Negative clamps to 0.
        limit: Page size. Falsy/None falls back to ``default`` (50);
            ``limit == 0`` is count-only mode (empty page, total still
            reported); values above ``max_cap`` (250) clamp with a
            ``limit capped at N`` note appended to the indicator.
        entity_name: Plural noun for the indicator ("transactions",
            "accounts", "splits").
        date_key: Optional ``row -> date/datetime/ISO-string`` callable.
            Provided for dated entities so the indicator carries the
            range; omit for undated ones (no parens). The range is
            computed from the page (or the full set in count-only mode).

    Returns:
        ``(page, indicator)``. ``page`` is the sliced rows; ``indicator``
        is the always-present header line.
    """
    total = len(items)

    # Count-only mode — one cheap call to learn the size before paging.
    # No page, so the range spans the full set (the scope question).
    if limit == 0:
        return [], (
            f"Showing 0 of {total} {entity_name}"
            f"{_range_suffix(items, date_key)}"
        )

    if not limit or limit < 1:
        limit = default
    capped = limit > max_cap
    effective = min(limit, max_cap)

    if offset < 0:
        offset = 0

    if total == 0:
        return [], f"Showing 0 of 0 {entity_name}"

    # Overshot the end — surface the total (and full-set scope) so the
    # LLM can correct.
    if offset >= total:
        return [], (
            f"Showing 0 of {total} {entity_name} "
            f"(offset {offset} exceeds result count)"
            f"{_range_suffix(items, date_key)}"
        )

    page = items[offset:offset + effective]
    cap_note = f"; limit capped at {effective}" if capped else ""
    indicator = (
        f"Showing {offset + 1}-{offset + len(page)} of {total} "
        f"{entity_name}{cap_note}{_range_suffix(page, date_key)}"
    )
    return page, indicator
