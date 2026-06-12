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

import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import TypeVar


# ── Numeric formatting ─────────────────────────────────────────────


def _format_number(
    value,
    decimals: int = 2,
    strip_trailing: bool = False,
) -> str:
    """Format a numeric value with consistent precision.

    Args:
        value: Decimal, str, int, float, or None. None and empty
            strings render as ``"0"`` (or ``"0.00"`` etc., per
            ``decimals``) so omission and zero are visually identical
            in the response.
        decimals: Number of decimal places. Common settings:
            - 2 for currency amounts and percentages
            - 4 for share quantities (mutual funds, stocks)
            - 6 for crypto (sub-satoshi precision matters)
        strip_trailing: When True, drop trailing zeros after the
            decimal point. ``"1.50"`` → ``"1.5"``, ``"1.00"`` → ``"1"``.
            Default False — currency-style fixed precision.

    Returns:
        Decimal string suitable for direct inclusion in a JSON or TSV
        response. Inputs that don't parse as numbers are returned
        ``str(value)`` unchanged so callers don't have to pre-validate.

    Examples:
        >>> _format_number(Decimal("1234.567"))
        '1234.57'
        >>> _format_number(Decimal("0.123456789"), decimals=2)
        '0.12'
        >>> _format_number(Decimal("230.762"), decimals=4)
        '230.7620'
        >>> _format_number(Decimal("1.50"), decimals=2, strip_trailing=True)
        '1.5'
    """
    if value is None or value == "":
        if strip_trailing:
            return "0"
        return f"{Decimal(0):.{decimals}f}"

    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        # Not a number — pass through unchanged. Defensive: callers
        # can hand us free-form strings (e.g. "N/A") for fields that
        # are usually numeric and we shouldn't crash the tool.
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

    Routine LLM-visible responses (``get_server_config``,
    ``get_book_summary``'s orientation line) must not include the full
    absolute path to the GnuCash book. That would leak the user's
    username, home directory layout, and any custom organization
    (``~/Finances/``, ``~/Documents/Books/``) into every transcript
    — a privacy concern for a tool that gets used on real personal
    financial data and a security concern for screenshots / shared
    sessions.

    The filename alone is sufficient to verify *which* book is
    loaded ("yes, this is Alex's book, not Lin Wei's"); the path
    components leading to it are the sensitive bit.

    This is an always-on redaction for the book path specifically,
    distinct from :func:`gnucash_mcp.logging_config.redact_paths`
    which is opt-in via ``GNUCASH_REDACT_PATHS=1`` and aimed at
    error-message paths (where the directory may be load-bearing
    debugging signal).

    Returns ``"not set"`` for falsy input so the orientation reads
    sensibly when ``GNUCASH_BOOK_PATH`` was never set.
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

    1. **Truncated.** ``total > effective`` → return the slice with a
       ``[Showing N of M ...]`` message.
    2. **Capped only.** Caller passed ``limit > max_cap`` but the full
       result still fits — return everything with a ``[Limit capped at
       N]`` message so the caller knows their over-limit had no effect.
    3. **Fits naturally.** ``total <= effective`` and ``limit <= max_cap``
       → return everything, no notice.

    Args:
        items: Full list, already filtered/sorted by the caller.
        limit: Caller-supplied limit. Falsy values fall back to
            ``default`` so omitting the parameter still gives a
            reasonable response size.
        default: Fallback limit (50 matches the ``list_transactions``
            convention).
        max_cap: Server-side ceiling. Larger limits are silently
            clamped and signaled via the cap-notice.
        entity_name: Plural noun in notice strings — e.g. ``"splits"``,
            ``"invoices"``, ``"prices"``. Renders as
            ``"Showing 5 of 35 invoices"``.
        suggest_narrow: When True, the over-limit hint mentions
            ``"narrow filters"`` first. Useful for tools that take
            date-range / account-filter parameters where narrowing is
            the better path than raising the limit.

    Returns:
        Tuple ``(truncated, notice)``. ``notice`` is ``None`` only when
        nothing was truncated and no cap was applied.
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
