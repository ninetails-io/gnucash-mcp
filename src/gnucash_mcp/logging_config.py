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

# Module-level reference to audit format ("text" or "json")
_audit_format: str = "text"

# Module-level reference to book path for text header
_book_path_str: str | None = None


def get_log_dir() -> Path | None:
    """Get the configured log directory path."""
    return _log_dir


def get_audit_format() -> str:
    """Get the configured audit log format ('text' or 'json')."""
    return _audit_format


def setup_logging(
    book_path: str | None = None,
    debug: bool = False,
    audit: bool = True,
    audit_format: str = "text",
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
        audit_format: Format for audit log: "text" (default, human-readable) or "json" (JSONL).
        get_book: Function to get the GnuCashBook instance (for state capture).

    Raises:
        ValueError: If book_path is not provided and either audit or debug is enabled.
        ValueError: If audit_format is not "text" or "json".
    """
    global _get_book_func, _log_dir, _audit_format, _book_path_str
    _get_book_func = get_book
    _audit_format = audit_format
    _book_path_str = book_path

    if audit_format not in ("text", "json"):
        raise ValueError(f"audit_format must be 'text' or 'json', got: {audit_format}")

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

        # Choose file extension based on format
        file_ext = "jsonl" if audit_format == "json" else "txt"
        audit_file = audit_dir / f"{today}.{file_ext}"

        # Write header for text format if file is new
        write_header = audit_format == "text" and not audit_file.exists()

        audit_handler = logging.FileHandler(audit_file)
        audit_handler.setFormatter(logging.Formatter("%(message)s"))
        audit_handler.stream.reconfigure(line_buffering=True)
        audit_logger.addHandler(audit_handler)

        # Write text header if needed
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


def _capture_before_state(
    entity_type: str | None, operation: str | None, params: dict
) -> dict | None:
    """Capture entity state before mutation.

    Args:
        entity_type: "transaction", "account", or "split"
        operation: "create", "update", "delete", "void", "unvoid", "reconcile", "set_state"
        params: Tool parameters

    Returns:
        State dict or None if not applicable.
    """
    if _get_book_func is None:
        return None

    # Creates don't have before state
    if operation == "create":
        return None

    try:
        book = _get_book_func()

        if entity_type == "transaction":
            guid = params.get("guid")
            if guid:
                return book.get_transaction(guid)

        elif entity_type == "account":
            name = params.get("name")
            if name:
                return book.get_account(name)

        elif entity_type == "split":
            # For set_reconcile_state
            split_guid = params.get("split_guid")
            if split_guid:
                return _get_split_states_batch(book, [split_guid]).get(split_guid)

            # For reconcile_account (multiple splits)
            split_guids = params.get("split_guids")
            if split_guids:
                states = _get_split_states_batch(book, split_guids)
                return {"splits": [states.get(g) for g in split_guids]}

    except Exception:
        # Don't let state capture failures break the tool
        return None

    return None


def _get_split_states_batch(book, split_guids: list[str]) -> dict[str, dict | None]:
    """Get the current state of multiple splits by GUID in a single book open.

    This is much more efficient than calling _get_split_state for each GUID,
    as it opens the book only once and iterates through transactions once.

    Args:
        book: GnuCashBook wrapper instance
        split_guids: List of split GUIDs to find

    Returns:
        Dict mapping split GUID to state dict (or None if not found).
    """
    if not split_guids:
        return {}

    # Convert to set for O(1) lookup
    guids_to_find = set(split_guids)
    results: dict[str, dict | None] = {g: None for g in split_guids}

    # Open book once and iterate through all transactions
    with book.open(readonly=True) as piecash_book:
        for transaction in piecash_book.transactions:
            # Check each split in this transaction
            for split in transaction.splits:
                if split.guid in guids_to_find:
                    results[split.guid] = {
                        "guid": split.guid,
                        "account": split.account.fullname,
                        "amount": str(split.quantity),
                        "reconcile_state": split.reconcile_state,
                        "reconcile_date": (
                            split.reconcile_date.isoformat()
                            if split.reconcile_date
                            else None
                        ),
                        # Include transaction context for human-readable logs
                        "transaction_description": transaction.description,
                        "transaction_date": transaction.post_date.isoformat(),
                    }
                    guids_to_find.discard(split.guid)

                    # Early exit if we found all splits
                    if not guids_to_find:
                        return results

    return results


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
        guid = entry.get("entity_guid") or params.get("guid", "")[:8]
        guid_short = guid[:8] if guid else ""

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
            if before and after:
                # Description change
                old_desc = before.get("description", "")
                new_desc = after.get("description", "")
                if old_desc != new_desc:
                    lines.append(f'{indent}Description: "{old_desc}" → "{new_desc}"')
                else:
                    lines.append(f'{indent}Description: "{old_desc}"')

                # Date change
                old_date = before.get("date", "")
                new_date = after.get("date", "")
                if old_date != new_date:
                    lines.append(f"{indent}Date: {old_date} → {new_date}")
                else:
                    lines.append(f"{indent}Date: {old_date} (unchanged)")

                # Splits change
                if before.get("splits") != after.get("splits"):
                    lines.append(f"{indent}Splits (before):")
                    lines.append(_format_splits_text(before.get("splits", []), indent + "  "))
                    lines.append(f"{indent}Splits (after):")
                    lines.append(_format_splits_text(after.get("splits", []), indent + "  "))

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
            if after:
                desc = after.get("description", "")
                date_str = after.get("date", "")
                lines.append(f'{indent}"{desc}" ({date_str})')
                # Show before splits (from previous_splits in the response)
                prev_splits = after.get("previous_splits", [])
                if prev_splits:
                    lines.append(f"{indent}Splits (before):")
                    lines.append(_format_splits_text(prev_splits, indent + "  "))
                # Show after splits
                new_splits = after.get("splits", [])
                if new_splits:
                    lines.append(f"{indent}Splits (after):")
                    lines.append(_format_splits_text(new_splits, indent + "  "))
                # Show warnings if any
                warnings = after.get("warnings", [])
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
                guid_short = guid[:8]
                # Try to find details for this split
                split_info = next((s for s in split_details if s and s.get("guid") == guid), None)
                if split_info:
                    desc = split_info.get("transaction_description", "")
                    amount = _format_amount(split_info.get("amount"))
                    lines.append(f'{indent}  guid:{guid_short}  "{desc}"  {amount:>10}')
                else:
                    lines.append(f"{indent}  guid:{guid_short}")
            if len(split_guids) > 10:
                lines.append(f"{indent}  ... and {len(split_guids) - 10} more")

        elif operation == "SET_STATE":
            split_guid = params.get("split_guid", "")[:8]
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
                # Capture before_state for updates/deletes/voids
                before = _capture_before_state(entity_type, operation, kwargs)
                if before:
                    entry["before_state"] = before

            debug_logger.debug(
                f"MCP request: tool={func.__name__} params={json.dumps(kwargs)}"
            )
            start_time = time.time()

            try:
                result = func(*args, **kwargs)
                elapsed_ms = (time.time() - start_time) * 1000

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

                # Log in appropriate format
                if _audit_format == "json":
                    logger.info(json.dumps(entry))
                else:
                    # Text format - only log write operations
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

                debug_logger.debug(
                    f"MCP response: tool={func.__name__} status=error "
                    f"elapsed={elapsed_ms:.0f}ms error={e}"
                )

                # Log errors in both formats
                if _audit_format == "json":
                    logger.info(json.dumps(entry))
                else:
                    # For errors in text format, log a simple error line
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
