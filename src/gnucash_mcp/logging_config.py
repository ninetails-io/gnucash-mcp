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


def _format_audit_entry_text(entry: dict) -> str:
    """Format an audit entry as human-readable text.

    Only formats write operations (mutations). Read operations return empty string.
    """
    if entry.get("classification") != "write":
        return ""  # Don't log read operations in text format

    timestamp = entry.get("timestamp", "")
    # Extract just the time portion (HH:MM:SS)
    if "T" in timestamp:
        time_part = timestamp.split("T")[1][:8]
    else:
        time_part = timestamp[:8] if len(timestamp) >= 8 else timestamp

    operation = entry.get("operation", "").upper()
    entity_type = entry.get("entity_type", "")
    params = entry.get("params", {})
    before = entry.get("before_state")
    after = entry.get("after_state")

    lines = []
    indent = "          "

    if entity_type == "transaction":
        # Book methods already emit collision-safe short prefixes via
        # `_unique_prefix` when returning a guid. We display whatever was
        # provided — truncating here would reverse any birthday-problem
        # extension (e.g., 9-char prefix back to a colliding 8).
        guid_short = entry.get("entity_guid") or params.get("guid", "")

        if operation == "CREATE":
            lines.append(f"{time_part}  CREATE TRANSACTION  guid:{guid_short}")
            desc = params.get("description", "")
            date_str = params.get("transaction_date", "")
            if after:
                desc = after.get("description", desc)
                date_str = after.get("date", date_str)
            lines.append(f'{indent}"{desc}" ({date_str})')
            # Get splits from after_state or params
            splits = after.get("splits") if after else None
            if not splits:
                splits = params.get("splits", [])
            if splits:
                lines.append(_format_splits_text(splits, indent + "  "))

        elif operation == "UPDATE":
            lines.append(f"{time_part}  UPDATE TRANSACTION  guid:{guid_short}")
            if before:
                # Description / date / splits may be trimmed out of the
                # response to save LLM tokens — pull from params (tool
                # inputs) as a fallback, then before_state to show
                # "unchanged" when the caller didn't touch that field.
                old_desc = before.get("description", "")
                new_desc = (
                    _resolve_entry_field(entry, "description")
                    or old_desc
                )
                old_date = before.get("date", "")
                new_date = (
                    _resolve_entry_field(
                        entry, "date", params_key="transaction_date"
                    )
                    or old_date
                )
                old_splits = before.get("splits") or []
                new_splits = (
                    _resolve_entry_field(entry, "splits") or old_splits
                )

                if old_desc != new_desc:
                    lines.append(f'{indent}Description: "{old_desc}" → "{new_desc}"')
                else:
                    lines.append(f'{indent}Description: "{old_desc}"')

                if old_date != new_date:
                    lines.append(f"{indent}Date: {old_date} → {new_date}")
                else:
                    lines.append(f"{indent}Date: {old_date} (unchanged)")

                if old_splits != new_splits:
                    lines.append(f"{indent}Splits (before):")
                    lines.append(_format_splits_text(old_splits, indent + "  "))
                    lines.append(f"{indent}Splits (after):")
                    lines.append(_format_splits_text(new_splits, indent + "  "))

        elif operation == "VOID":
            lines.append(f"{time_part}  VOID TRANSACTION  guid:{guid_short}")
            reason = params.get("reason", "")
            lines.append(f'{indent}Reason: "{reason}"')
            if before:
                desc = before.get("description", "")
                date_str = before.get("date", "")
                lines.append(f'{indent}Was: "{desc}" ({date_str})')
                if before.get("splits"):
                    lines.append(_format_splits_text(before["splits"], indent + "  "))

        elif operation == "UNVOID":
            lines.append(f"{time_part}  UNVOID TRANSACTION  guid:{guid_short}")
            if after:
                desc = after.get("description", "")
                date_str = after.get("date", "")
                lines.append(f'{indent}Restored: "{desc}" ({date_str})')
                if after.get("splits"):
                    lines.append(_format_splits_text(after["splits"], indent + "  "))

        elif operation == "DELETE":
            lines.append(f"{time_part}  DELETE TRANSACTION  guid:{guid_short}")
            if before:
                desc = before.get("description", "")
                date_str = before.get("date", "")
                lines.append(f'{indent}Was: "{desc}" ({date_str})')
                if before.get("splits"):
                    lines.append(_format_splits_text(before["splits"], indent + "  "))

        elif operation == "REPLACE_SPLITS":
            lines.append(f"{time_part}  REPLACE SPLITS  guid:{guid_short}")
            # Description and date aren't echoed in the thin response
            # (they didn't change anyway). Pull from before_state for
            # the "transaction header" line.
            desc = _resolve_entry_field(entry, "description") or ""
            date_str = _resolve_entry_field(entry, "date") or ""
            if desc or date_str:
                lines.append(f'{indent}"{desc}" ({date_str})')

            # previous_splits stays in after_state — it's the piece the
            # LLM doesn't already know.
            prev_splits = (after or {}).get("previous_splits", [])
            if prev_splits:
                lines.append(f"{indent}Splits (before):")
                lines.append(_format_splits_text(prev_splits, indent + "  "))

            # New splits: after_state may echo them back, or may be
            # trimmed — fall back to params (what the LLM submitted).
            new_splits = _resolve_entry_field(entry, "splits")
            if new_splits:
                lines.append(f"{indent}Splits (after):")
                lines.append(_format_splits_text(new_splits, indent + "  "))

            # Warnings live only in after_state (genuinely new info).
            warnings = (after or {}).get("warnings", [])
            if warnings:
                for w in warnings:
                    lines.append(f"{indent}Warning: {w}")

    elif entity_type == "account":
        if operation == "CREATE":
            lines.append(f"{time_part}  CREATE ACCOUNT")
            if after:
                lines.append(f"{indent}{after.get('fullname', params.get('name', ''))}")
                lines.append(f"{indent}Type: {after.get('type', params.get('account_type', ''))}")
                desc = after.get("description", params.get("description", ""))
                if desc:
                    lines.append(f'{indent}Description: "{desc}"')

        elif operation == "UPDATE":
            lines.append(f"{time_part}  UPDATE ACCOUNT")
            account_name = params.get("name", "")
            lines.append(f"{indent}{account_name}")
            if before and after:
                old_name = before.get("name", "")
                new_name = after.get("name", "")
                if old_name != new_name:
                    lines.append(f'{indent}Name: "{old_name}" → "{new_name}"')
                old_desc = before.get("description", "")
                new_desc = after.get("description", "")
                if old_desc != new_desc:
                    lines.append(f'{indent}Description: "{old_desc}" → "{new_desc}"')

        elif operation == "DELETE":
            lines.append(f"{time_part}  DELETE ACCOUNT")
            if before:
                lines.append(f"{indent}Was: {before.get('fullname', params.get('name', ''))}")
                lines.append(f"{indent}Type: {before.get('type', '')}")
                desc = before.get("description", "")
                if desc:
                    lines.append(f'{indent}Description: "{desc}"')

    elif entity_type == "split":
        if operation == "RECONCILE":
            account = params.get("account", "")
            lines.append(f"{time_part}  RECONCILE  {account}")
            lines.append(f"{indent}Statement date: {params.get('statement_date', '')}")
            lines.append(f"{indent}Statement balance: {_format_amount(params.get('statement_balance'))}")
            # Get split details from before_state if available
            split_details = before.get("splits", []) if before else []
            split_guids = params.get("split_guids", [])
            lines.append(f"{indent}Splits reconciled ({len(split_guids)}):")
            for i, guid in enumerate(split_guids[:10]):  # Limit to first 10
                # Use the param as-is — truncating here would undo any
                # collision-safe extension applied upstream.
                guid_short = guid
                # Detail lookup tolerates full-vs-prefix mismatch: params
                # carries whatever the LLM passed (often an 8+-char prefix),
                # but `before_state` entries carry full GUIDs from the book
                # method's staged snapshot. Match by startswith so either
                # side can be a prefix of the other.
                split_info = next(
                    (
                        s
                        for s in split_details
                        if s and (s.get("guid") == guid
                                  or s.get("guid", "").startswith(guid)
                                  or guid.startswith(s.get("guid", "")))
                    ),
                    None,
                )
                if split_info:
                    desc = split_info.get("transaction_description", "")
                    amount = _format_amount(split_info.get("amount"))
                    lines.append(f'{indent}  guid:{guid_short}  "{desc}"  {amount:>10}')
                else:
                    lines.append(f"{indent}  guid:{guid_short}")
            if len(split_guids) > 10:
                lines.append(f"{indent}  ... and {len(split_guids) - 10} more")

        elif operation == "SET_STATE":
            # As-is: caller may have passed a collision-safe 9+-char prefix.
            split_guid = params.get("split_guid", "")
            lines.append(f"{time_part}  SET RECONCILE STATE")
            lines.append(f"{indent}guid:{split_guid} (split)")
            if before:
                account = before.get("account", "").split(":")[-1]  # Short name
                lines.append(f"{indent}Account: {account}")
                desc = before.get("transaction_description", "")
                amount = _format_amount(before.get("amount"))
                if desc:
                    lines.append(f'{indent}"{desc}"  {amount}')
            state = params.get("state", "")
            old_state = before.get("reconcile_state", "n") if before else "n"
            lines.append(f"{indent}State: {old_state} → {state}")

    elif entity_type == "account_slot":
        account = params.get("account", "")
        key = params.get("key", "")

        if operation == "SET_SLOT":
            value = params.get("value", "")
            status = ""
            if after:
                status = after.get("status", "")
            lines.append(f"{time_part}  SET ACCOUNT SLOT  account:{account}")
            lines.append(f'{indent}key: "{key}"  value: "{value}"  ({status})')

        elif operation == "DELETE_SLOT":
            lines.append(f"{time_part}  DELETE ACCOUNT SLOT  account:{account}")
            lines.append(f'{indent}key: "{key}"')

    elif entity_type == "customer":
        if operation == "CREATE":
            cust_id = ""
            cust_name = params.get("name", "")
            if after:
                cust_id = after.get("id", "")
                cust_name = after.get("name", cust_name)
            lines.append(f"{time_part}  CREATE CUSTOMER  id:{cust_id}")
            currency = params.get("currency", "")
            if after:
                currency = after.get("currency", currency) or ""
            lines.append(f'{indent}name: "{cust_name}"  currency: {currency}')
        elif operation == "DELETE":
            cust_id = params.get("customer_id", "")
            lines.append(f"{time_part}  DELETE CUSTOMER  id:{cust_id}")
            if after:
                cust_name = after.get("name", "")
                if cust_name:
                    lines.append(f'{indent}name: "{cust_name}"')

    elif entity_type == "vendor":
        if operation == "CREATE":
            vend_id = ""
            vend_name = params.get("name", "")
            if after:
                vend_id = after.get("id", "")
                vend_name = after.get("name", vend_name)
            lines.append(f"{time_part}  CREATE VENDOR  id:{vend_id}")
            currency = params.get("currency", "")
            if after:
                currency = after.get("currency", currency) or ""
            lines.append(f'{indent}name: "{vend_name}"  currency: {currency}')
        elif operation == "DELETE":
            vend_id = params.get("vendor_id", "")
            lines.append(f"{time_part}  DELETE VENDOR  id:{vend_id}")
            if after:
                vend_name = after.get("name", "")
                if vend_name:
                    lines.append(f'{indent}name: "{vend_name}"')

    elif entity_type == "billterm":
        if operation == "CREATE":
            bt_name = params.get("name", "")
            due_days = params.get("due_days", "")
            if after:
                bt_name = after.get("name", bt_name)
                due_days = after.get("due_days", due_days)
            lines.append(f"{time_part}  CREATE BILLTERM")
            lines.append(f'{indent}name: "{bt_name}"  due: {due_days} days')

    elif entity_type == "invoice":
        if operation == "CREATE":
            inv_id = ""
            customer_id = params.get("customer_id", "")
            if after:
                inv_id = after.get("id", "")
                customer_id = after.get("customer_id", customer_id)
            lines.append(f"{time_part}  CREATE INVOICE  id:{inv_id}")
            lines.append(f'{indent}customer: {customer_id}')
        elif operation == "DELETE":
            inv_id = params.get("invoice_id", "")
            lines.append(f"{time_part}  DELETE INVOICE  id:{inv_id}")
            if after:
                entries = after.get("entries_deleted", 0)
                if entries:
                    lines.append(f"{indent}entries removed: {entries}")
        elif operation == "POST":
            inv_id = params.get("id", "")
            post_account = params.get("post_account", "")
            lines.append(f"{time_part}  POST INVOICE  id:{inv_id}")
            if after:
                total = after.get("total", "")
                post_date = after.get("post_date", "")
                # As-is — upstream emits a collision-safe prefix.
                txn_guid = after.get("transaction_guid") or ""
                lines.append(f'{indent}total: {total}  date: {post_date}')
                lines.append(f'{indent}account: {post_account}  txn:{txn_guid}')
        elif operation == "PAY":
            inv_id = params.get("id", "")
            lines.append(f"{time_part}  PAY INVOICE  id:{inv_id}")
            if after:
                amount = after.get("amount_paid", "")
                remaining = after.get("remaining_balance", "")
                pay_acct = params.get("payment_account", "")
                # As-is — upstream emits a collision-safe prefix.
                txn_guid = after.get("transaction_guid") or ""
                lines.append(f'{indent}paid: {amount}  remaining: {remaining}')
                lines.append(f'{indent}from: {pay_acct}  txn:{txn_guid}')

    elif entity_type == "bill":
        if operation == "CREATE":
            bill_id = ""
            vendor_id = params.get("vendor_id", "")
            if after:
                bill_id = after.get("id", "")
                vendor_id = after.get("vendor_id", vendor_id)
            lines.append(f"{time_part}  CREATE BILL  id:{bill_id}")
            lines.append(f'{indent}vendor: {vendor_id}')
        elif operation == "DELETE":
            bill_id = params.get("bill_id", "")
            lines.append(f"{time_part}  DELETE BILL  id:{bill_id}")
            if after:
                entries = after.get("entries_deleted", 0)
                if entries:
                    lines.append(f"{indent}entries removed: {entries}")

    elif entity_type == "entry":
        if operation == "CREATE":
            desc = params.get("description", "")
            total = ""
            if after:
                desc = after.get("description", desc)
                total = after.get("total", "")
            inv_id = params.get("invoice_id", "") or params.get("bill_id", "")
            lines.append(f"{time_part}  CREATE ENTRY")
            lines.append(f'{indent}"{desc}"  total: {total}  on: {inv_id}')

    # Handle move_account specially (it's logged as "update" but is conceptually a move)
    if entity_type == "account" and "new_parent" in params:
        # This is actually a MOVE operation
        lines = [f"{time_part}  MOVE ACCOUNT"]
        account_name = params.get("name", "")
        new_parent = params.get("new_parent", "")
        lines.append(f"{indent}{account_name}")
        if before:
            old_parent = ":".join(before.get("fullname", "").split(":")[:-1]) or "(root)"
            lines.append(f"{indent}From: {old_parent}")
        lines.append(f"{indent}To: {new_parent}")

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
