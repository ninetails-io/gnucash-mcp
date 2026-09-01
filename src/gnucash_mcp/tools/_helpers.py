"""Shared helpers for MCP tool wrappers.

Shared across every tool-registration module under
gnucash_mcp/tools/.
"""

import json
import logging
import traceback
from datetime import date
from functools import wraps
from typing import Annotated, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from gnucash_mcp.book import GnuCashLockError, StaleFXRateError


def _parse_iso_date(s: str | None) -> date | None:
    """Parse an optional ISO-format date string.

    Returns ``None`` for falsy input (``None`` / ``""``); otherwise
    delegates to ``date.fromisoformat`` whose error is good enough
    to surface at the MCP boundary unchanged
    (``ValueError: Invalid isoformat string: '2025-01-XX'``).

    Chokepoints the ``date.fromisoformat(x) if x else None``
    pattern that recurs across the tool wrappers. Required
    dates (where the caller has already guaranteed non-None) keep
    calling ``date.fromisoformat`` directly — the distinction is
    explicit at the call site.
    """
    if not s:
        return None
    return date.fromisoformat(s)

# Re-exports from the layer-neutral format module. Tool wrappers can
# keep importing from ``tools._helpers`` (the historical home) without
# concern for which file the implementation lives in. Book-layer code
# imports directly from ``gnucash_mcp._format`` to preserve the
# one-way ``tools → book`` dependency.
from gnucash_mcp._format import (  # noqa: F401
    _apply_limit,
    _format_number,
    _paginate,
)

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


# ── Business-entity free-text caps ────────────────────────────────
#
# The book-layer byte check runs INSIDE the tool body, AFTER
# @audit_log fires _maybe_auto_backup — so an oversize ``notes``
# value looks like a hang while a first-write backup runs, then
# rejects. Pydantic Field constraints validate at the schema layer,
# BEFORE any decorator, rejecting in milliseconds. Caps are in
# characters (max_length semantics; worst-case UTF-8 byte ceiling
# ~4×); the book-layer byte check stays for direct callers that
# bypass the MCP boundary.

BusinessNotes = Annotated[
    str,
    Field(
        default="",
        max_length=4096,
        description=(
            "Optional notes. Capped at 4096 characters at the MCP "
            "boundary; oversize input rejects with a clear error."
        ),
    ),
]

BusinessNotesOptional = Annotated[
    str | None,
    Field(
        default=None,
        max_length=4096,
        description=(
            "New notes value (capped at 4096 characters). Pass "
            "``None`` (default) to leave existing notes unchanged; "
            "pass ``\"\"`` to clear."
        ),
    ),
]


class BusinessAddressInput(BaseModel):
    """Address sub-fields for business entities (customer / vendor /
    employee). All sub-fields are optional strings capped at 1024
    characters at the MCP boundary.

    Same MP-5 rationale as ``BusinessNotes``: the cap fires at the
    schema layer so an oversize value rejects fast without auto-
    backup running first.
    """

    model_config = ConfigDict(
        # Match the server-global ``extra="forbid"`` on
        # ``ArgModelBase`` — typo'd address keys (``adr1`` instead of
        # ``addr1``) should reject rather than silently drop.
        extra="forbid",
    )

    name: Annotated[str, Field(default="", max_length=1024)] = ""
    addr1: Annotated[str, Field(default="", max_length=1024)] = ""
    addr2: Annotated[str, Field(default="", max_length=1024)] = ""
    addr3: Annotated[str, Field(default="", max_length=1024)] = ""
    addr4: Annotated[str, Field(default="", max_length=1024)] = ""
    phone: Annotated[str, Field(default="", max_length=1024)] = ""
    fax: Annotated[str, Field(default="", max_length=1024)] = ""
    email: Annotated[str, Field(default="", max_length=1024)] = ""


# ── Split payload schema ──────────────────────────────────────────
#
# A bare ``splits: list[dict]`` signature is the trap: pydantic
# doesn't descend into bare dict, so a client sending a JSON number
# (``"amount": 94.87``) hands the book method a float whose IEEE-754
# epsilon breaks the sum-to-zero check. SplitInput enforces strings;
# ``coerce_numbers_to_str=True`` routes stray numbers through
# shortest-repr str() first. Book methods also use ``_to_decimal``
# as belt-and-suspenders for callers that bypass this layer.


class SplitInput(BaseModel):
    """One split in a transaction-creating tool call.

    Fields mirror the dict shape the book methods already consume:
    ``account`` (required), ``amount`` (required, string, in
    transaction currency), ``quantity`` (optional, string, required
    only when the account commodity differs from the transaction
    currency), and ``memo`` (optional).
    """

    # extra="forbid" matches the server-global ArgModelBase setting
    # — "ignore" would silently drop a typo'd ``quantitiy`` key and
    # post a cross-currency value/quantity mismatch.
    model_config = ConfigDict(
        coerce_numbers_to_str=True,
        extra="forbid",
    )

    account: Annotated[
        str,
        Field(
            description=(
                "Account ref: full path (e.g. 'Expenses:Rent'), "
                "%short GUID (e.g. '%xxxxxxx' — 7+ hex chars copied "
                "from list_accounts), or full 32-char GUID"
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
    action: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Optional split action — GnuCash's typed movement "
                "tag (free text; register conventions: 'Buy', "
                "'Sell', 'Dividend' on investment legs, 'Wire', "
                "'ATM', 'Interest' on bank legs)."
            ),
        ),
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


def _strip_noise(obj: object) -> object:
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
    """
    return json.dumps(
        _strip_noise(obj), separators=(",", ":"), ensure_ascii=False,
    )


def _gate_owner_type(owner_type: str | None) -> str | None:
    """Enforce the Freelancer/Business module split at the
    ``owner_type`` boundary.

    The shared-lifecycle invoice tools live in Freelancer, but
    vendor bills and employee vouchers travel through them via
    owner_type dispatch — and Business owns both vendor and
    employee management.

    Three cases:

    - Explicit ``'vendor'`` / ``'employee'`` without Business:
      reject with a clear error.
    - Omitted or ``'customer'`` without Business: coerce to
      ``'customer'`` — the tool only sees customer entities.
    - Business loaded: pass through unchanged.

    Returns the (possibly coerced) owner_type for the book method.
    Imports server lazily to avoid an import-time cycle.
    """
    from gnucash_mcp.server import is_module_enabled

    # Gate on the business_complete LEAF (not the group alias) so an
    # explicit business_complete-only selection also unlocks.
    if is_module_enabled("business_complete"):
        return owner_type  # All three halves available; no gating.

    if owner_type == "vendor":
        raise ValueError(
            "owner_type='vendor' requires the business module. "
            "Restart the server with --modules=business (or add "
            "business_complete to your current selection) to access "
            "vendor bills, or omit owner_type to operate on customer "
            "invoices only."
        )
    if owner_type == "employee":
        raise ValueError(
            "owner_type='employee' requires the business module. "
            "Employee expense vouchers live with employee "
            "management. Restart the server with --modules=business "
            "(or add business_complete to your current selection) "
            "or omit owner_type to operate on customer invoices only."
        )
    # Only None / 'customer' coerce; anything else (typos, unknown
    # future types) rejects loudly rather than masquerading as
    # "searched customer invoices, found nothing".
    if owner_type is not None and owner_type != "customer":
        raise ValueError(
            f"Invalid owner_type {owner_type!r}. Must be 'customer' "
            f"(or omit). 'vendor' and 'employee' require the "
            f"Business module."
        )
    return "customer"


# The consolidated business surface's two type axes. Literal types
# → the enum lands in the JSON schema, so capable clients constrain
# at decode time and a wrong value never reaches the server.
PartyType = Literal["customer", "vendor", "employee"]
DocumentType = Literal["invoice", "bill", "voucher", "credit_note"]

# Which module side each species belongs to. The polymorphic tools
# live in Freelancer; the vendor/employee species unlock when
# business_complete is loaded (same split _gate_owner_type enforces
# for the lifecycle tools).
_CUSTOMER_SIDE_DOCS = {"invoice"}


def _gate_party_type(party_type: str | None) -> str | None:
    """Enforce the Freelancer/Business split on ``party_type``.

    Freelancer owns the customer side; vendor and employee
    management requires business_complete. ``None`` (where a tool
    allows it, e.g. list_parties) coerces to 'customer' without
    Business and passes through (= all types) with it.
    """
    from gnucash_mcp.server import is_module_enabled

    if is_module_enabled("business_complete"):
        return party_type
    if party_type in ("vendor", "employee"):
        raise ValueError(
            f"party_type={party_type!r} requires the business "
            f"module. Restart the server with --modules=business "
            f"(or add business_complete) to manage vendors and "
            f"employees; Freelancer covers customers."
        )
    return "customer"


def _gate_document_type(document_type: str | None) -> str | None:
    """Enforce the Freelancer/Business split on ``document_type``.

    Customer invoices (and credit notes, which carry their own
    party side) stay Freelancer; vendor bills and employee
    vouchers require business_complete.
    """
    from gnucash_mcp.server import is_module_enabled

    if is_module_enabled("business_complete"):
        return document_type
    if document_type in ("bill", "voucher"):
        raise ValueError(
            f"document_type={document_type!r} requires the "
            f"business module. Restart the server with "
            f"--modules=business (or add business_complete) for "
            f"vendor bills and employee vouchers; Freelancer "
            f"covers customer invoices and credit notes."
        )
    return document_type if document_type is not None else "invoice"


def _resolve_id_alias(
    id: str | None,
    legacy: str | None,
    legacy_name: str,
) -> str:
    """Resolve the ``id`` / ``<entity>_id`` parameter pair on the
    document delete path (now ``delete_document``; before the
    business consolidation, delete_invoice / delete_bill /
    delete_voucher / delete_credit_note).

    The standard parameter across the document tool surface
    (get_document, post_document, unpost_document, pay_document)
    is ``id``. The retired delete tools used ``<entity>_id``. To
    converge without breaking older callers, both names are
    accepted on the delete path — but exactly one must be set.

    - Both omitted → ``ValueError`` (caller forgot the ID)
    - Both provided → ``ValueError`` (ambiguous; pick one)
    - One provided → return it

    Args:
        id: Value passed under the preferred ``id`` parameter.
        legacy: Value passed under the legacy ``<entity>_id``
            parameter.
        legacy_name: The legacy parameter name (``"invoice_id"``,
            ``"bill_id"``, etc.) — used only in the error message.
    """
    if id is not None and legacy is not None:
        raise ValueError(
            f"Pass exactly one of 'id' or {legacy_name!r}, not both. "
            f"'id' is the standard parameter name; {legacy_name!r} is "
            f"a legacy alias kept for back-compat."
        )
    if id is None and legacy is None:
        raise ValueError(
            f"Missing required parameter: pass 'id' (preferred) or "
            f"{legacy_name!r} (legacy alias)."
        )
    return id if id is not None else legacy  # type: ignore[return-value]


def safe_tool(func: Callable) -> Callable:
    """Decorator that wraps tool functions with comprehensive error handling.

    Catches all exceptions and returns them as JSON error responses instead of
    crashing the MCP server.

    Also the restart-safety chokepoint (Sabine battery ruling 6):
    every tool result passes through here, so this is where the
    one-time startup notice attaches, where write-classified tools
    hit the multi-book disarm gate, and where successful mutating
    responses get their ``book`` stamp. Write-ness comes from the
    ``__audit_meta__`` that @audit_log exposes and @wraps propagates
    outward — the same single declaration the audit log and MCP
    ToolAnnotations already trust. The gate runs BEFORE the wrapped
    @audit_log layer, so a refused write never consumes rate-limit
    tokens, never triggers auto-backup, and never logs an audit
    entry (nothing happened to the book).
    """

    # Path redaction is applied at the MCP boundary (response
    # going out to the LLM) but NOT to the internal logger.error
    # calls — local logs benefit from full paths for debugging,
    # MCP responses are the surface that gets shared externally.
    # Helper imported lazily to avoid a circular import; the
    # logging_config module is the foundational layer.
    from gnucash_mcp.logging_config import redact_paths

    is_write = (
        getattr(func, "__audit_meta__", None) or {}
    ).get("classification") == "write"

    def _restart_guards(invoke: Callable[[], str]) -> str:
        """Run ``invoke`` behind the ruling-6 guards. Guard failures
        must never break a tool — every server-state consultation is
        wrapped; the degraded mode is simply pre-ruling behavior."""
        # Lazy import: server imports this module at import time (the
        # inline switch_book uses safe_tool); by the time any tool
        # RUNS, the server module is fully loaded.
        from gnucash_mcp import server as _server

        if is_write:
            try:
                gate_msg = _server._write_gate_message()
            except Exception:
                gate_msg = None
            if gate_msg:
                logger.warning(
                    f"Write refused (book unconfirmed): {func.__name__}"
                )
                result = _json({
                    "error": gate_msg,
                    "error_type": "active_book_unconfirmed",
                    "suggestion": (
                        "Call switch_book with the intended book, "
                        "then retry."
                    ),
                })
                return _attach_notice(_server, result)

        result = invoke()

        if is_write:
            try:
                stamp = _server._mutation_book_stamp()
                if stamp:
                    data = json.loads(result)
                    if (
                        isinstance(data, dict)
                        and "error" not in data
                        and "book" not in data
                    ):
                        data["book"] = stamp
                        result = _json(data)
            except Exception:
                pass  # non-JSON write response — skip the stamp
        return _attach_notice(_server, result)

    def _attach_notice(_server, result: str) -> str:
        try:
            notice = _server._consume_startup_notice()
        except Exception:
            notice = None
        return f"{notice}\n\n{result}" if notice else result

    @wraps(func)
    def wrapper(*args, **kwargs) -> str:
        return _restart_guards(lambda: _invoke(*args, **kwargs))

    def _invoke(*args, **kwargs) -> str:
        try:
            return func(*args, **kwargs)
        except GnuCashLockError as e:
            logger.warning(f"Lock error in {func.__name__}: {e}")
            return _json(
                {
                    "error": redact_paths(str(e)),
                    "error_type": "lock_error",
                    "suggestion": "Close GnuCash application and try again.",
                }
            )
        except FileNotFoundError as e:
            logger.error(f"File not found in {func.__name__}: {e}")
            return _json(
                {
                    "error": redact_paths(str(e)),
                    "error_type": "file_not_found",
                    "suggestion": "Check that GNUCASH_BOOK_PATH is set correctly.",
                }
            )
        except StaleFXRateError as e:
            # Subclass of ValueError — must be caught BEFORE the
            # generic ValueError handler below, or the structured
            # fx_detail + dedicated error_type collapse into a plain
            # validation_error. The caller uses error_type to decide
            # between create_price-then-retry and force=true.
            logger.warning(f"Stale FX rate in {func.__name__}: {e}")
            return _json({
                "error": redact_paths(str(e)),
                "error_type": "stale_fx_rate",
                "fx_detail": e.fx_detail,
            })
        except ValueError as e:
            logger.warning(f"Validation error in {func.__name__}: {e}")
            return _json({
                "error": redact_paths(str(e)),
                "error_type": "validation_error",
            })
        except RuntimeError as e:
            # The _verify_* helpers raise RuntimeError for "the
            # write didn't land" — a correctness signal that must
            # not collapse into the generic unexpected_error bucket,
            # or callers can't tell a failed write from a KeyError.
            msg = str(e)
            if "verification failed" in msg.lower():
                logger.error(
                    f"Write verification failed in {func.__name__}: "
                    f"{e}\n{traceback.format_exc()}"
                )
                return _json(
                    {
                        "error": redact_paths(
                            f"Write verification failed: {e}"
                        ),
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
                    "error": redact_paths(
                        f"Unexpected error: {type(e).__name__}: {e}"
                    ),
                    "error_type": "unexpected_error",
                }
            )
        except Exception as e:
            logger.error(
                f"Unexpected error in {func.__name__}: {e}\n{traceback.format_exc()}"
            )
            return _json(
                {
                    "error": redact_paths(
                        f"Unexpected error: {type(e).__name__}: {e}"
                    ),
                    "error_type": "unexpected_error",
                }
            )

    return wrapper
