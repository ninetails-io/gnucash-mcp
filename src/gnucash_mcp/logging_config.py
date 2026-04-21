"""Logging configuration for audit and debug logs.

Logs are stored alongside the GnuCash book file:
  /path/to/book.gnucash.mcp/
    audit/YYYY-MM-DD.jsonl
    debug/YYYY-MM-DD.log  (when --debug enabled)
"""

import json
import logging
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable

AUDIT_LOGGER_NAME = "gnucash_mcp.audit"
DEBUG_LOGGER_NAME = "gnucash_mcp.debug"

# Module-level reference to get_book, set during setup
_get_book_func: Callable | None = None

# Module-level reference to log directory, set during setup
_log_dir: Path | None = None

# Module-level reference to book path for text header
_book_path_str: str | None = None


def get_log_dir() -> Path | None:
    """Get the configured log directory path."""
    return _log_dir


def setup_logging(
    book_path: str | None = None,
    debug: bool = False,
    audit: bool = True,
    get_book: Callable | None = None,
) -> None:
    """Configure audit and debug logging.

    Logs are stored alongside the GnuCash book file for data locality:
    - Audit logs contain sensitive financial data and belong with the book
    - Users backing up their GnuCash folder will include the audit trail
    - When a book is deleted, its logs can be cleaned up easily

    Args:
        book_path: Path to the GnuCash book file. Logs will be created in
                   {book_path}.mcp/ directory alongside the book.
        debug: Enable debug-level MCP protocol logging.
        audit: Enable audit logging. Default True. Use --noaudit to disable.
        get_book: Function to get the GnuCashBook instance (for state capture).

    Raises:
        ValueError: If book_path is not provided and either audit or debug is enabled.
    """
    global _get_book_func, _log_dir, _book_path_str
    _get_book_func = get_book
    _book_path_str = book_path

    # If both audit and debug are disabled, no logging setup needed
    if not audit and not debug:
        _log_dir = None
        # Disable both loggers
        audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)
        audit_logger.handlers.clear()
        audit_logger.setLevel(logging.CRITICAL + 1)
        debug_logger = logging.getLogger(DEBUG_LOGGER_NAME)
        debug_logger.handlers.clear()
        debug_logger.setLevel(logging.CRITICAL + 1)
        return

    if not book_path:
        raise ValueError(
            "book_path is required for logging setup. "
            "Set GNUCASH_BOOK_PATH environment variable."
        )

    # Log directory lives alongside the book file
    # e.g., /path/to/finances.gnucash -> /path/to/finances.gnucash.mcp/
    book_path_obj = Path(book_path)
    log_dir = book_path_obj.parent / f"{book_path_obj.name}.mcp"
    _log_dir = log_dir

    now_local = datetime.now().astimezone()
    today = now_local.strftime("%Y-%m-%d")
    tz_name = now_local.strftime("%Z") or now_local.strftime("%z")

    # Audit log - unless --noaudit
    audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)
    audit_logger.handlers.clear()

    if audit:
        audit_dir = log_dir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)

        audit_logger.setLevel(logging.INFO)
        audit_logger.propagate = False

        audit_file = audit_dir / f"{today}.txt"

        # Write header if file is new
        write_header = not audit_file.exists()

        audit_handler = logging.FileHandler(audit_file)
        audit_handler.setFormatter(logging.Formatter("%(message)s"))
        audit_handler.stream.reconfigure(line_buffering=True)
        audit_logger.addHandler(audit_handler)

        # Write header if needed
        if write_header:
            header = _format_text_header(today, book_path, tz_name)
            audit_logger.info(header)
            _flush_logger(audit_logger)
    else:
        # Disable audit logging
        audit_logger.setLevel(logging.CRITICAL + 1)

    # Debug log - only when --debug
    debug_logger = logging.getLogger(DEBUG_LOGGER_NAME)
    debug_logger.handlers.clear()

    if debug:
        debug_dir = log_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)

        debug_logger.setLevel(logging.DEBUG)
        debug_logger.propagate = False

        debug_handler = logging.FileHandler(debug_dir / f"{today}.log")
        debug_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        debug_logger.addHandler(debug_handler)
    else:
        # Set to a level that effectively disables it
        debug_logger.setLevel(logging.CRITICAL + 1)


def _format_text_header(date_str: str, book_path: str, tz_name: str = "") -> str:
    """Format the header for a text audit log file."""
    line = "═" * 64
    tz_line = f"\nTimezone: {tz_name}" if tz_name else ""
    return f"""{line}
GNUCASH MCP AUDIT LOG — {date_str}
Book: {book_path}{tz_line}
{line}"""


def _format_amount(amount: str | None) -> str:
    """Format an amount string with commas and alignment."""
    if amount is None:
        return "0.00"
    try:
        from decimal import Decimal
        val = Decimal(amount)
        # Format with commas and 2 decimal places
        sign = "-" if val < 0 else ""
        abs_val = abs(val)
        formatted = f"{abs_val:,.2f}"
        return f"{sign}{formatted}"
    except Exception:
        return str(amount)


def _format_splits_text(splits: list[dict], indent: str = "          ") -> str:
    """Format a list of splits for text output."""
    if not splits:
        return ""

    lines = []
    # Find max account name length for alignment
    max_name_len = max(len(s.get("account", "").split(":")[-1]) for s in splits)
    max_name_len = min(max_name_len, 40)  # Cap at 40 chars

    for split in splits:
        # Use short account name (last component)
        account = split.get("account", "Unknown")
        short_name = account.split(":")[-1]
        amount = _format_amount(split.get("amount") or split.get("value"))
        lines.append(f"{indent}{short_name:<{max_name_len}}  {amount:>12}")

    return "\n".join(lines)


def _resolve_entry_field(
    entry: dict, field: str, params_key: str | None = None
):
    """Look up a field in an audit entry, falling back through sources.

    Write tool responses are trimmed for LLM token efficiency — several
    fields the human-readable audit log wants to display (splits,
    description, date) may not appear in ``after_state``. This helper
    unifies the lookup order:

    1. ``after_state`` — the tool's response, richest when present
    2. ``params`` — the tool's inputs; for most write ops this is the
       same information the LLM would have put in the response
    3. ``before_state`` — the pre-write snapshot captured by the
       audit decorator (useful for REPLACE_SPLITS where description /
       date aren't in response OR params)

    A falsy value ("", [], {}, None) in any source is treated as "not
    present" and falls through to the next. Callers that need to
    distinguish "really empty" from "missing" should consult the
    individual sources directly.

    Args:
        entry: The audit entry dict.
        field: Key to look up in ``after_state`` and ``before_state``.
        params_key: Alternate key in ``params`` when the response and
                    params use different names (e.g. response: "date",
                    params: "transaction_date"). Defaults to ``field``.

    Returns:
        The resolved value, or None if not found in any source.
    """
    sources = (
        (entry.get("after_state") or {}, field),
        (entry.get("params") or {}, params_key or field),
        (entry.get("before_state") or {}, field),
    )
    for src, key in sources:
        value = src.get(key)
        if value:
            return value
    return None


# ── Audit text-format dispatcher ───────────────────────────────────
#
# Write operations render to the human-readable audit log as short
# multi-line blocks. Each (entity_type, operation) pair has its own
# tiny handler that pulls what it needs from the entry (params,
# before_state, after_state) and returns a list of lines.
#
# Adding a new entity type is a dict entry, not another ``elif`` in a
# 380-line chain. Unknown keys degrade to empty output so new audit
# classifications added in book code don't crash log rendering before
# a handler lands.

_INDENT = "          "  # 10 spaces; every handler indents its detail lines here
_INDENT_SPLITS = _INDENT + "  "  # nested indent for split blocks


def _extract_time(entry: dict) -> str:
    """Pull HH:MM:SS from an ISO-ish timestamp, defensively."""
    timestamp = entry.get("timestamp", "")
    if "T" in timestamp:
        return timestamp.split("T")[1][:8]
    return timestamp[:8] if len(timestamp) >= 8 else timestamp


def _transaction_guid(entry: dict) -> str:
    """GUID for a transaction log line — short prefix if upstream supplied one.

    Book methods emit collision-safe prefixes via ``_unique_prefix`` on
    write responses. We display whatever was provided — re-truncating
    here would undo any birthday-problem extension (e.g., collapse a
    9-char safe prefix back to a colliding 8).
    """
    params = entry.get("params") or {}
    return entry.get("entity_guid") or params.get("guid", "")


# ── Transaction handlers ──────────────────────────────────────────


def _fmt_transaction_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    guid = _transaction_guid(entry)

    lines = [f"{time_part}  CREATE TRANSACTION  guid:{guid}"]
    desc = after.get("description") or params.get("description", "")
    date_str = after.get("date") or params.get("transaction_date", "")
    lines.append(f'{_INDENT}"{desc}" ({date_str})')

    # after_state preferred; fall back to params (thin-response case)
    splits = after.get("splits") or params.get("splits") or []
    if splits:
        lines.append(_format_splits_text(splits, _INDENT_SPLITS))
    return lines


def _fmt_transaction_update(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    before = entry.get("before_state")
    guid = _transaction_guid(entry)

    lines = [f"{time_part}  UPDATE TRANSACTION  guid:{guid}"]
    if not before:
        return lines

    # update_transaction's response is thin (no description/date/splits
    # echo). Resolve through after_state → params → before_state so
    # the text log keeps the full diff readable.
    old_desc = before.get("description", "")
    new_desc = _resolve_entry_field(entry, "description") or old_desc
    old_date = before.get("date", "")
    new_date = (
        _resolve_entry_field(entry, "date", params_key="transaction_date")
        or old_date
    )
    old_splits = before.get("splits") or []
    new_splits = _resolve_entry_field(entry, "splits") or old_splits

    if old_desc != new_desc:
        lines.append(f'{_INDENT}Description: "{old_desc}" → "{new_desc}"')
    else:
        lines.append(f'{_INDENT}Description: "{old_desc}"')

    if old_date != new_date:
        lines.append(f"{_INDENT}Date: {old_date} → {new_date}")
    else:
        lines.append(f"{_INDENT}Date: {old_date} (unchanged)")

    if old_splits != new_splits:
        lines.append(f"{_INDENT}Splits (before):")
        lines.append(_format_splits_text(old_splits, _INDENT_SPLITS))
        lines.append(f"{_INDENT}Splits (after):")
        lines.append(_format_splits_text(new_splits, _INDENT_SPLITS))
    return lines


def _fmt_transaction_void(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state")
    guid = _transaction_guid(entry)

    lines = [
        f"{time_part}  VOID TRANSACTION  guid:{guid}",
        f'{_INDENT}Reason: "{params.get("reason", "")}"',
    ]
    if before:
        desc = before.get("description", "")
        date_str = before.get("date", "")
        lines.append(f'{_INDENT}Was: "{desc}" ({date_str})')
        if before.get("splits"):
            lines.append(_format_splits_text(before["splits"], _INDENT_SPLITS))
    return lines


def _fmt_transaction_unvoid(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    after = entry.get("after_state")
    guid = _transaction_guid(entry)

    lines = [f"{time_part}  UNVOID TRANSACTION  guid:{guid}"]
    if after:
        desc = after.get("description", "")
        date_str = after.get("date", "")
        lines.append(f'{_INDENT}Restored: "{desc}" ({date_str})')
        if after.get("splits"):
            lines.append(_format_splits_text(after["splits"], _INDENT_SPLITS))
    return lines


def _fmt_transaction_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    before = entry.get("before_state")
    guid = _transaction_guid(entry)

    lines = [f"{time_part}  DELETE TRANSACTION  guid:{guid}"]
    if before:
        desc = before.get("description", "")
        date_str = before.get("date", "")
        lines.append(f'{_INDENT}Was: "{desc}" ({date_str})')
        if before.get("splits"):
            lines.append(_format_splits_text(before["splits"], _INDENT_SPLITS))
    return lines


def _fmt_transaction_replace_splits(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    after = entry.get("after_state") or {}
    guid = _transaction_guid(entry)

    lines = [f"{time_part}  REPLACE SPLITS  guid:{guid}"]

    # description / date don't change on this op, but show them from
    # before_state so a reviewer has context for which transaction.
    desc = _resolve_entry_field(entry, "description") or ""
    date_str = _resolve_entry_field(entry, "date") or ""
    if desc or date_str:
        lines.append(f'{_INDENT}"{desc}" ({date_str})')

    # previous_splits is the piece the LLM doesn't already know.
    prev_splits = after.get("previous_splits", [])
    if prev_splits:
        lines.append(f"{_INDENT}Splits (before):")
        lines.append(_format_splits_text(prev_splits, _INDENT_SPLITS))

    # New splits fall through after_state → params (the LLM's input).
    new_splits = _resolve_entry_field(entry, "splits")
    if new_splits:
        lines.append(f"{_INDENT}Splits (after):")
        lines.append(_format_splits_text(new_splits, _INDENT_SPLITS))

    for w in after.get("warnings", []) or []:
        lines.append(f"{_INDENT}Warning: {w}")
    return lines


# ── Account handlers ──────────────────────────────────────────────


def _fmt_account_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state")

    lines = [f"{time_part}  CREATE ACCOUNT"]
    if after:
        lines.append(f"{_INDENT}{after.get('fullname', params.get('name', ''))}")
        lines.append(
            f"{_INDENT}Type: {after.get('type', params.get('account_type', ''))}"
        )
        desc = after.get("description", params.get("description", ""))
        if desc:
            lines.append(f'{_INDENT}Description: "{desc}"')
    return lines


def _fmt_account_update(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state")
    after = entry.get("after_state")

    lines = [
        f"{time_part}  UPDATE ACCOUNT",
        f"{_INDENT}{params.get('name', '')}",
    ]
    if before and after:
        old_name = before.get("name", "")
        new_name = after.get("name", "")
        if old_name != new_name:
            lines.append(f'{_INDENT}Name: "{old_name}" → "{new_name}"')
        old_desc = before.get("description", "")
        new_desc = after.get("description", "")
        if old_desc != new_desc:
            lines.append(f'{_INDENT}Description: "{old_desc}" → "{new_desc}"')
    return lines


def _fmt_account_move(entry: dict) -> list[str]:
    """MOVE is logged as UPDATE with ``new_parent`` in params — the
    dispatcher remaps the operation key before lookup so this handler
    fires cleanly."""
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state")

    lines = [
        f"{time_part}  MOVE ACCOUNT",
        f"{_INDENT}{params.get('name', '')}",
    ]
    if before:
        old_parent = (
            ":".join(before.get("fullname", "").split(":")[:-1]) or "(root)"
        )
        lines.append(f"{_INDENT}From: {old_parent}")
    lines.append(f"{_INDENT}To: {params.get('new_parent', '')}")
    return lines


def _fmt_account_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state")

    lines = [f"{time_part}  DELETE ACCOUNT"]
    if before:
        lines.append(
            f"{_INDENT}Was: {before.get('fullname', params.get('name', ''))}"
        )
        lines.append(f"{_INDENT}Type: {before.get('type', '')}")
        desc = before.get("description", "")
        if desc:
            lines.append(f'{_INDENT}Description: "{desc}"')
    return lines


# ── Split handlers ────────────────────────────────────────────────


def _fmt_split_reconcile(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state")
    split_details = (before or {}).get("splits", []) if before else []
    split_guids = params.get("split_guids", []) or []

    lines = [
        f"{time_part}  RECONCILE  {params.get('account', '')}",
        f"{_INDENT}Statement date: {params.get('statement_date', '')}",
        f"{_INDENT}Statement balance: {_format_amount(params.get('statement_balance'))}",
        f"{_INDENT}Splits reconciled ({len(split_guids)}):",
    ]

    # Show first 10 reconciled splits with description + amount if the
    # before_state carried the per-split context. Prefix/full GUID
    # tolerance: params may be an 8+-char prefix, before_state carries
    # full GUIDs from the book method's staged snapshot. Match either way.
    for guid in split_guids[:10]:
        split_info = next(
            (
                s for s in split_details
                if s and (
                    s.get("guid") == guid
                    or s.get("guid", "").startswith(guid)
                    or guid.startswith(s.get("guid", ""))
                )
            ),
            None,
        )
        if split_info:
            desc = split_info.get("transaction_description", "")
            amount = _format_amount(split_info.get("amount"))
            lines.append(f'{_INDENT}  guid:{guid}  "{desc}"  {amount:>10}')
        else:
            lines.append(f"{_INDENT}  guid:{guid}")
    if len(split_guids) > 10:
        lines.append(f"{_INDENT}  ... and {len(split_guids) - 10} more")
    return lines


def _fmt_split_set_state(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state")

    split_guid = params.get("split_guid", "")
    state = params.get("state", "")
    old_state = (before or {}).get("reconcile_state", "n") if before else "n"

    lines = [
        f"{time_part}  SET RECONCILE STATE",
        f"{_INDENT}guid:{split_guid} (split)",
    ]
    if before:
        account = before.get("account", "").split(":")[-1]
        lines.append(f"{_INDENT}Account: {account}")
        desc = before.get("transaction_description", "")
        amount = _format_amount(before.get("amount"))
        if desc:
            lines.append(f'{_INDENT}"{desc}"  {amount}')
    lines.append(f"{_INDENT}State: {old_state} → {state}")
    return lines


# ── Account-slot handlers ─────────────────────────────────────────


def _fmt_account_slot_set(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state")

    account = params.get("account", "")
    key = params.get("key", "")
    value = params.get("value", "")
    status = (after or {}).get("status", "") if after else ""

    return [
        f"{time_part}  SET ACCOUNT SLOT  account:{account}",
        f'{_INDENT}key: "{key}"  value: "{value}"  ({status})',
    ]


def _fmt_account_slot_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    return [
        f"{time_part}  DELETE ACCOUNT SLOT  account:{params.get('account', '')}",
        f'{_INDENT}key: "{params.get("key", "")}"',
    ]


# ── Business handlers ─────────────────────────────────────────────


def _fmt_person_create(entry: dict, type_label: str) -> list[str]:
    """Shared CREATE renderer for customer / vendor / employee.

    The three entity types share CRUD shape (see
    _create_business_person in business.py). Their audit rendering is
    the same shape too; only the label differs.
    """
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}

    person_id = after.get("id", "")
    person_name = after.get("name", params.get("name", ""))
    currency = after.get("currency") or params.get("currency", "") or ""

    return [
        f"{time_part}  CREATE {type_label.upper()}  id:{person_id}",
        f'{_INDENT}name: "{person_name}"  currency: {currency}',
    ]


def _fmt_person_delete(
    entry: dict, type_label: str, id_param: str,
) -> list[str]:
    """Shared DELETE renderer for customer / vendor / employee."""
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}

    person_id = params.get(id_param, "")
    lines = [f"{time_part}  DELETE {type_label.upper()}  id:{person_id}"]
    person_name = after.get("name", "") if after else ""
    if person_name:
        lines.append(f'{_INDENT}name: "{person_name}"')
    return lines


def _fmt_customer_create(entry: dict) -> list[str]:
    return _fmt_person_create(entry, "customer")


def _fmt_customer_delete(entry: dict) -> list[str]:
    return _fmt_person_delete(entry, "customer", "customer_id")


def _fmt_vendor_create(entry: dict) -> list[str]:
    return _fmt_person_create(entry, "vendor")


def _fmt_vendor_delete(entry: dict) -> list[str]:
    return _fmt_person_delete(entry, "vendor", "vendor_id")


def _fmt_billterm_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    name = after.get("name", params.get("name", ""))
    due_days = after.get("due_days", params.get("due_days", ""))
    return [
        f"{time_part}  CREATE BILLTERM",
        f'{_INDENT}name: "{name}"  due: {due_days} days',
    ]


def _fmt_invoice_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    inv_id = after.get("id", "")
    customer_id = after.get("customer_id", params.get("customer_id", ""))
    return [
        f"{time_part}  CREATE INVOICE  id:{inv_id}",
        f"{_INDENT}customer: {customer_id}",
    ]


def _fmt_invoice_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state")

    lines = [f"{time_part}  DELETE INVOICE  id:{params.get('invoice_id', '')}"]
    if after:
        entries = after.get("entries_deleted", 0)
        if entries:
            lines.append(f"{_INDENT}entries removed: {entries}")
    return lines


def _fmt_invoice_post(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state")

    lines = [f"{time_part}  POST INVOICE  id:{params.get('id', '')}"]
    if after:
        total = after.get("total", "")
        post_date = after.get("post_date", "")
        txn_guid = after.get("transaction_guid") or ""
        lines.append(f"{_INDENT}total: {total}  date: {post_date}")
        lines.append(
            f"{_INDENT}account: {params.get('post_account', '')}  txn:{txn_guid}"
        )
    return lines


def _fmt_invoice_pay(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state")

    lines = [f"{time_part}  PAY INVOICE  id:{params.get('id', '')}"]
    if after:
        amount = after.get("amount_paid", "")
        remaining = after.get("remaining_balance", "")
        txn_guid = after.get("transaction_guid") or ""
        lines.append(f"{_INDENT}paid: {amount}  remaining: {remaining}")
        lines.append(
            f"{_INDENT}from: {params.get('payment_account', '')}  txn:{txn_guid}"
        )
    return lines


def _fmt_bill_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    bill_id = after.get("id", "")
    vendor_id = after.get("vendor_id", params.get("vendor_id", ""))
    return [
        f"{time_part}  CREATE BILL  id:{bill_id}",
        f"{_INDENT}vendor: {vendor_id}",
    ]


def _fmt_bill_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state")

    lines = [f"{time_part}  DELETE BILL  id:{params.get('bill_id', '')}"]
    if after:
        entries = after.get("entries_deleted", 0)
        if entries:
            lines.append(f"{_INDENT}entries removed: {entries}")
    return lines


def _fmt_entry_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    desc = after.get("description", params.get("description", ""))
    total = after.get("total", "")
    inv_id = params.get("invoice_id", "") or params.get("bill_id", "")
    return [
        f"{time_part}  CREATE ENTRY",
        f'{_INDENT}"{desc}"  total: {total}  on: {inv_id}',
    ]


# ── Dispatch table ────────────────────────────────────────────────
#
# Key shape: (entity_type, operation). Both in their canonical forms —
# entity_type lowercase, operation UPPERCASE. Adding a new entity type
# is one row here plus one handler function above.

_AUDIT_HANDLERS: dict[str, Callable[[dict], list[str]]] = {
    ("transaction", "CREATE"): _fmt_transaction_create,
    ("transaction", "UPDATE"): _fmt_transaction_update,
    ("transaction", "VOID"): _fmt_transaction_void,
    ("transaction", "UNVOID"): _fmt_transaction_unvoid,
    ("transaction", "DELETE"): _fmt_transaction_delete,
    ("transaction", "REPLACE_SPLITS"): _fmt_transaction_replace_splits,
    ("account", "CREATE"): _fmt_account_create,
    ("account", "UPDATE"): _fmt_account_update,
    ("account", "MOVE"): _fmt_account_move,
    ("account", "DELETE"): _fmt_account_delete,
    ("split", "RECONCILE"): _fmt_split_reconcile,
    ("split", "SET_STATE"): _fmt_split_set_state,
    ("account_slot", "SET_SLOT"): _fmt_account_slot_set,
    ("account_slot", "DELETE_SLOT"): _fmt_account_slot_delete,
    ("customer", "CREATE"): _fmt_customer_create,
    ("customer", "DELETE"): _fmt_customer_delete,
    ("vendor", "CREATE"): _fmt_vendor_create,
    ("vendor", "DELETE"): _fmt_vendor_delete,
    ("billterm", "CREATE"): _fmt_billterm_create,
    ("invoice", "CREATE"): _fmt_invoice_create,
    ("invoice", "DELETE"): _fmt_invoice_delete,
    ("invoice", "POST"): _fmt_invoice_post,
    ("invoice", "PAY"): _fmt_invoice_pay,
    ("bill", "CREATE"): _fmt_bill_create,
    ("bill", "DELETE"): _fmt_bill_delete,
    ("entry", "CREATE"): _fmt_entry_create,
}


def _format_audit_entry_text(entry: dict) -> str:
    """Format an audit entry as human-readable text.

    Only formats write operations (mutations). Reads and unmapped
    (entity_type, operation) combos return the empty string so a new
    classification added in book code but not yet wired to a handler
    degrades silently rather than crashing log rendering.

    The account ``MOVE`` operation is logged upstream as ``UPDATE``
    with ``new_parent`` in params — we remap the key here before
    lookup so the dedicated move handler fires instead of the update
    one.
    """
    if entry.get("classification") != "write":
        return ""

    entity_type = entry.get("entity_type") or ""
    operation = (entry.get("operation") or "").upper()

    # Account update with new_parent → MOVE
    if (
        entity_type == "account"
        and operation == "UPDATE"
        and "new_parent" in (entry.get("params") or {})
    ):
        operation = "MOVE"

    handler = _AUDIT_HANDLERS.get((entity_type, operation))
    if handler is None:
        return ""
    lines = handler(entry)
    return "\n".join(lines) if lines else ""


def _extract_after_state(result: str, entity_type: str | None) -> dict | None:
    """Extract entity state from tool result JSON.

    Args:
        result: JSON string returned by tool
        entity_type: "transaction", "account", or "split"

    Returns:
        State dict with guid, or None.
    """
    try:
        data = json.loads(result)

        # Error responses don't have after state
        if "error" in data:
            return None

        # Most write tools return the entity directly or with a guid field
        if "guid" in data:
            return data

        # reconcile_account returns a summary
        if "reconciled_splits" in data:
            return data

        return data if data else None

    except (json.JSONDecodeError, TypeError):
        return None


def audit_log(
    classification: str = "read",
    operation: str | None = None,
    entity_type: str | None = None,
):
    """Decorator that logs tool calls to the audit log.

    Args:
        classification: "read" or "write"
        operation: For writes: "create", "update", "delete", "void", "unvoid",
                   "reconcile", "set_state"
        entity_type: "transaction", "account", or "split"
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> str:
            logger = logging.getLogger(AUDIT_LOGGER_NAME)
            debug_logger = logging.getLogger(DEBUG_LOGGER_NAME)
            timestamp = datetime.now().astimezone().isoformat()

            entry = {
                "timestamp": timestamp,
                "tool": func.__name__,
                "classification": classification,
                "params": kwargs,
            }

            if classification == "write":
                entry["operation"] = operation
                entry["entity_type"] = entity_type

            debug_logger.debug(
                f"MCP request: tool={func.__name__} params={json.dumps(kwargs)}"
            )

            # Before the first write of each process, give the backup
            # system a chance to snapshot. BackupMixin's own flag makes
            # subsequent calls no-op, so the cost is negligible after
            # the first hit. Silent on failure — auto-backup must
            # never break a user's write. Reads never trigger.
            if classification == "write" and _get_book_func is not None:
                try:
                    book = _get_book_func()
                    if book is not None and hasattr(
                        book, "_maybe_auto_backup"
                    ):
                        book._maybe_auto_backup()
                except Exception as e:  # noqa: BLE001 — must swallow
                    debug_logger.warning(f"Auto-backup check failed: {e}")

            start_time = time.time()

            try:
                result = func(*args, **kwargs)
                elapsed_ms = (time.time() - start_time) * 1000

                # Consume any before-state the book method staged while
                # its session was open. Always consume (even on read /
                # create) to clear any stray value; helper returns None
                # when nothing staged.
                before = None
                if _get_book_func is not None:
                    try:
                        book = _get_book_func()
                        if book is not None:
                            before = book._consume_audit_before()
                    except Exception:
                        # Never let consumption failures break a tool.
                        before = None
                if classification == "write" and before:
                    entry["before_state"] = before

                # Check if result indicates an error (JSON with "error" key)
                try:
                    result_data = json.loads(result)
                    if "error" in result_data:
                        entry["result"] = "error"
                        entry["error"] = result_data["error"]
                    else:
                        entry["result"] = "success"
                        if classification == "write":
                            after = _extract_after_state(result, entity_type)
                            if after:
                                entry["entity_guid"] = after.get("guid")
                                entry["after_state"] = after
                except (json.JSONDecodeError, TypeError):
                    entry["result"] = "success"

                debug_logger.debug(
                    f"MCP response: tool={func.__name__} status={entry['result']} "
                    f"elapsed={elapsed_ms:.0f}ms size={len(result)}bytes"
                )

                # Only log write operations; reads are noise in the audit trail.
                text_entry = _format_audit_entry_text(entry)
                if text_entry:
                    logger.info(text_entry)
                    logger.info("")  # Blank line between entries
                _flush_logger(logger)
                return result

            except Exception as e:
                elapsed_ms = (time.time() - start_time) * 1000
                entry["result"] = "error"
                entry["error"] = str(e)

                # Drop any staged before-state so it can't leak into the
                # next call. Failed writes don't render before_state in
                # the error line anyway.
                if _get_book_func is not None:
                    try:
                        book = _get_book_func()
                        if book is not None:
                            book._consume_audit_before()
                    except Exception:
                        pass

                debug_logger.debug(
                    f"MCP response: tool={func.__name__} status=error "
                    f"elapsed={elapsed_ms:.0f}ms error={e}"
                )

                # Log a simple error line
                time_part = timestamp.split("T")[1][:8] if "T" in timestamp else timestamp[:8]
                error_text = f"{time_part}  ERROR  {func.__name__}: {e}"
                logger.info(error_text)
                logger.info("")
                _flush_logger(logger)
                raise

        return wrapper

    return decorator


def debug_log(message: str) -> None:
    """Log a debug message if debug logging is enabled."""
    logging.getLogger(DEBUG_LOGGER_NAME).debug(message)


def _flush_logger(logger: logging.Logger) -> None:
    """Flush all handlers for a logger."""
    for handler in logger.handlers:
        handler.flush()
