"""Shared helpers for MCP tool wrappers.

These used to live in server.py; they are now shared across every
tool-registration module under gnucash_mcp/tools/.
"""

import json
import logging
import traceback
from functools import wraps
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from gnucash_mcp.book import GnuCashLockError

# Re-exports from the layer-neutral format module. Tool wrappers can
# keep importing from ``tools._helpers`` (the historical home) without
# concern for which file the implementation lives in. Book-layer code
# imports directly from ``gnucash_mcp._format`` to preserve the
# one-way ``tools → book`` dependency.
from gnucash_mcp._format import _apply_limit, _format_number  # noqa: F401

logger = logging.getLogger(__name__)


# ── Shared GUID parameter annotations ──────────────────────────────
#
# Tools that accept GUIDs all describe the format the same way (full
# 32-char hex or a prefix of ≥8 chars, case-insensitive — validated
# in _resolve_guid). Centralizing the Annotated types here means one
# place to update the description and slightly shorter schema payloads
# across the ~15 GUID parameters the tool catalog advertises.

TransactionGuid = Annotated[
    str, Field(description="Transaction GUID (32-char hex or 8+ char prefix)")
]
SplitGuid = Annotated[
    str, Field(description="Split GUID (32-char hex or 8+ char prefix)")
]
LotGuid = Annotated[
    str, Field(description="Lot GUID (32-char hex or 8+ char prefix)")
]
ScheduledTransactionGuid = Annotated[
    str,
    Field(description="Scheduled transaction GUID (32-char hex or 8+ char prefix)"),
]


# ── Split payload schema ──────────────────────────────────────────
#
# Transaction-creating tools (create_transaction, update_transaction,
# replace_splits, create_scheduled_transaction) all take a list of
# split dicts. Before this model existed, the tool signature was
# ``splits: list[dict]`` — pydantic doesn't descend into bare ``dict``,
# so the inner ``amount`` / ``quantity`` values round-tripped through
# whatever type the JSON parser produced. When a client sent a bare
# JSON number (``"amount": 94.87``), the parser emitted a float, and
# ``Decimal(split["amount"])`` inside the book method inherited the
# IEEE-754 epsilon ( ``0.8699999999999999955591...`` ) — causing
# spurious "splits do not balance" errors on non-dyadic decimals.
#
# ``SplitInput`` enforces ``amount`` / ``quantity`` as strings at the
# MCP boundary. With ``coerce_numbers_to_str=True`` pydantic routes a
# stray JSON number through Python's ``str()`` (shortest-repr) before
# we ever construct a Decimal — so ``94.87`` becomes ``"94.87"``, not
# a noisy float. Internal book methods also wrap every
# user-derived ``Decimal(...)`` call with ``_to_decimal(...)`` as
# belt-and-suspenders for direct callers (tests, scripts) that
# bypass this layer.


class SplitInput(BaseModel):
    """One split in a transaction-creating tool call.

    Fields mirror the dict shape the book methods already consume:
    ``account`` (required), ``amount`` (required, string, in
    transaction currency), ``quantity`` (optional, string, required
    only when the account commodity differs from the transaction
    currency), and ``memo`` (optional).
    """

    model_config = ConfigDict(
        coerce_numbers_to_str=True,
        extra="ignore",
    )

    account: Annotated[
        str,
        Field(
            description=(
                "Account ref: full path (e.g. 'Expenses:Rent'), "
                "%short GUID (e.g. '%2e78c86'), or full 32-char GUID"
            )
        ),
    ]
    amount: Annotated[
        str,
        Field(description="Value in transaction currency, as a decimal string (e.g. '94.87')"),
    ]
    quantity: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Amount in the account's commodity, as a decimal string. Required "
                "when the account's commodity differs from the transaction currency."
            ),
        ),
    ] = None
    memo: Annotated[
        str | None,
        Field(default=None, description="Optional split memo."),
    ] = None


def _splits_to_dicts(
    splits: list | None,
) -> list[dict] | None:
    """Normalize a splits list to plain dicts the book methods expect.

    In production the MCP layer decodes each entry through
    ``SplitInput`` (pydantic) and we receive a list of models. Tests
    call the tool callable directly with raw dicts and never hit
    pydantic. Accept both: models go through ``model_dump``, dicts
    are coerced through ``SplitInput`` so the test path gets the same
    ``amount`` / ``quantity`` stringification the production path
    does.

    ``exclude_none=True`` preserves the "key present iff value set"
    contract — so ``"quantity" in split`` and ``split.get("memo", "")``
    keep behaving the way they did when splits arrived as loose dicts.

    ``None`` passes through unchanged (auto-fill path in
    ``create_transaction``).
    """
    if splits is None:
        return None
    result: list[dict] = []
    for s in splits:
        if isinstance(s, SplitInput):
            model = s
        else:
            model = SplitInput.model_validate(s)
        result.append(model.model_dump(exclude_none=True))
    return result


def _strip_noise(obj):
    """Recursively remove keys with None or empty-string values from dicts.

    Empty strings are treated as absent — the convention across the
    book layer is that an empty memo / description / notes field
    means "no value", not "value=empty". A future caller that needs
    to preserve a deliberately-empty string (e.g. to override an
    inherited default) should use a sentinel value or set the field
    on the after-state explicitly via the audit log's params trail
    rather than relying on this serializer to retain ``""``.

    Lists are passed through (their elements are recursed into);
    dict values that recurse to empty dicts/lists are kept (only
    the explicit None / "" cases are stripped).
    """
    if isinstance(obj, dict):
        return {k: _strip_noise(v) for k, v in obj.items()
                if v is not None and v != ""}
    if isinstance(obj, list):
        return [_strip_noise(item) for item in obj]
    return obj


def _json(obj) -> str:
    """Serialize to minified JSON, stripping noise values.

    ``ensure_ascii=False`` so non-ASCII strings (Chinese commodity
    names like "贵州茅台", customer names with accented characters,
    etc.) round-trip as raw UTF-8 instead of being escaped to
    ``\\uXXXX`` form. The escape behavior is technically valid JSON
    but makes the wire format unreadable for human reviewers and
    breaks any downstream substring match on the original text.
    Bookkeeper-flagged on a CNY-default test book where every
    SSE/SZSE commodity name came back as escape sequences.
    """
    return json.dumps(
        _strip_noise(obj), separators=(",", ":"), ensure_ascii=False,
    )


def _gate_owner_type(owner_type: str | None) -> str | None:
    """Enforce the Freelancer/Business module split at the
    ``owner_type`` boundary.

    Shared-lifecycle invoice/bill/voucher tools (post_invoice,
    unpost_invoice, pay_invoice, list_invoices, get_invoice,
    get_outstanding_invoices) live in the Freelancer module
    because customer-facing invoicing is the natural Freelancer
    surface. Vendor bills (``owner_type='vendor'``) and employee
    expense vouchers (``owner_type='employee'``) travel through
    the same tools via owner_type dispatch — but the Business
    module owns BOTH vendor and employee management. A user with
    only Freelancer loaded should never be able to touch either.

    Three cases:

    - **Explicit ``owner_type='vendor'`` or ``'employee'`` without
      Business loaded:** reject with a clear error. The user is
      explicitly asking for behavior they didn't enable.
    - **``owner_type`` omitted or ``'customer'`` without Business:**
      coerce to ``'customer'``. The tool only sees customer
      entities. Any vendor/employee-ID collision is treated as
      "not found" at the lookup layer (book methods filter on
      owner_type).
    - **Business loaded:** pass through unchanged; all three halves
      available.

    Returns the (possibly coerced) owner_type the book method should
    receive. Caller passes the return value through to ``book.X(...,
    owner_type=...)``.

    Imports server lazily to avoid an import-time cycle (server.py
    imports from tools.* via lazy-load).
    """
    from gnucash_mcp.server import is_module_enabled

    if is_module_enabled("business"):
        return owner_type  # All three halves available; no gating.

    if owner_type == "vendor":
        raise ValueError(
            "owner_type='vendor' requires the Business module. "
            "Restart the server with --modules=...,Business to access "
            "vendor bills, or omit owner_type to operate on customer "
            "invoices only."
        )
    if owner_type == "employee":
        raise ValueError(
            "owner_type='employee' requires the Business module. "
            "Employee expense vouchers live with employee "
            "management, which the Business module owns. Restart "
            "the server with --modules=...,Business or omit "
            "owner_type to operate on customer invoices only."
        )
    return "customer"


def safe_tool(func: Callable) -> Callable:
    """Decorator that wraps tool functions with comprehensive error handling.

    Catches all exceptions and returns them as JSON error responses instead of
    crashing the MCP server.
    """

    @wraps(func)
    def wrapper(*args, **kwargs) -> str:
        try:
            return func(*args, **kwargs)
        except GnuCashLockError as e:
            logger.warning(f"Lock error in {func.__name__}: {e}")
            return _json(
                {
                    "error": str(e),
                    "error_type": "lock_error",
                    "suggestion": "Close GnuCash application and try again.",
                }
            )
        except FileNotFoundError as e:
            logger.error(f"File not found in {func.__name__}: {e}")
            return _json(
                {
                    "error": str(e),
                    "error_type": "file_not_found",
                    "suggestion": "Check that GNUCASH_BOOK_PATH is set correctly.",
                }
            )
        except ValueError as e:
            logger.warning(f"Validation error in {func.__name__}: {e}")
            return _json({"error": str(e), "error_type": "validation_error"})
        except RuntimeError as e:
            # ``_verify_write`` / ``_verify_composite_write`` /
            # ``_verify_delete`` and the per-method
            # ``_verify_transaction_state`` raise ``RuntimeError``
            # specifically for "the write didn't land" — a critical
            # correctness signal that should NOT collapse into the
            # generic "unexpected_error" bucket. Pre-fix this
            # masked write-verification failures behind the same
            # error_type as e.g. ``KeyError`` lookup failures, so
            # callers couldn't tell "the write failed" from "we
            # tried to read a missing key."
            msg = str(e)
            if "verification failed" in msg.lower():
                logger.error(
                    f"Write verification failed in {func.__name__}: "
                    f"{e}\n{traceback.format_exc()}"
                )
                return _json(
                    {
                        "error": f"Write verification failed: {e}",
                        "error_type": "write_verification_failed",
                    }
                )
            # Other RuntimeErrors fall through to the generic
            # handler below.
            logger.error(
                f"Unexpected error in {func.__name__}: {e}\n{traceback.format_exc()}"
            )
            return _json(
                {
                    "error": f"Unexpected error: {type(e).__name__}: {e}",
                    "error_type": "unexpected_error",
                }
            )
        except Exception as e:
            logger.error(
                f"Unexpected error in {func.__name__}: {e}\n{traceback.format_exc()}"
            )
            return _json(
                {
                    "error": f"Unexpected error: {type(e).__name__}: {e}",
                    "error_type": "unexpected_error",
                }
            )

    return wrapper
