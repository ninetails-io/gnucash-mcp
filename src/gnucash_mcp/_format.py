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
