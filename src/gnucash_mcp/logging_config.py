"""Logging configuration for audit and debug logs.

Logs are stored alongside the GnuCash book file:
  /path/to/book.gnucash.mcp/
    audit/YYYY-MM-DD.jsonl
    debug/YYYY-MM-DD.log  (when --debug enabled)
"""

import json
import logging
import os
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


class _WriteRateLimiter:
    """Token-bucket rate limiter for MCP write operations.

    Refills at ``rate`` tokens per second up to ``burst`` capacity.
    ``consume()`` is the hot path — thread-safe, returns whether
    the call is allowed and (if not) how long until a token is
    available so the response can include a useful retry hint.

    Token bucket beats simple per-window counting because it
    accommodates honest bursts (e.g., posting 5 invoices in quick
    succession) while still throttling sustained runaway loops.
    The burst capacity is the ceiling; the rate is the steady-
    state allowance.
    """

    def __init__(self, rate: float, burst: int):
        import threading
        self.rate = float(rate)
        self.burst = int(burst)
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self) -> tuple[bool, float]:
        """Try to consume one token.

        Returns:
            ``(allowed, retry_after_sec)`` where ``allowed`` is
            True when a token was available (and consumed) and
            False when the bucket is empty. ``retry_after_sec``
            is meaningful only when not allowed — the wall-clock
            seconds until the next full token would be available.
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(
                self.burst,
                self.tokens + elapsed * self.rate,
            )
            self.last_refill = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True, 0.0
            retry = (1.0 - self.tokens) / self.rate
            return False, retry


# Lazy module-level cache. Reset via reset_write_rate_limiter()
# (callers: tests, ``setup_logging`` restart paths).
_write_limiter: _WriteRateLimiter | None = None
_write_limiter_initialized: bool = False


def _get_write_rate_limiter() -> _WriteRateLimiter | None:
    """Resolve the write rate limiter from env, caching the result.

    Honors:
      - ``GNUCASH_WRITE_RATE_LIMIT`` (tokens per second, positive
        float). Absent or non-positive disables limiting entirely
        — the cached return is ``None`` and the per-call check
        becomes a fast nullity test.
      - ``GNUCASH_WRITE_BURST`` (integer max bucket size; default
        10). Allows the user to tolerate honest bursts above the
        steady-state rate.

    Default (no env vars): no limiting — writes proceed at full
    speed, matching pre-Stage-6 behavior.
    """
    global _write_limiter, _write_limiter_initialized
    if _write_limiter_initialized:
        return _write_limiter

    rate_str = os.environ.get("GNUCASH_WRITE_RATE_LIMIT")
    if not rate_str:
        _write_limiter = None
        _write_limiter_initialized = True
        return None

    try:
        rate = float(rate_str)
    except ValueError:
        # Non-numeric env value: treat as unset, log a warning
        # via the debug logger so the user sees the typo.
        logging.getLogger(DEBUG_LOGGER_NAME).warning(
            f"GNUCASH_WRITE_RATE_LIMIT={rate_str!r} is not a "
            f"valid number; rate limiting disabled."
        )
        _write_limiter = None
        _write_limiter_initialized = True
        return None

    if rate <= 0:
        _write_limiter = None
        _write_limiter_initialized = True
        return None

    burst_str = os.environ.get("GNUCASH_WRITE_BURST", "10")
    try:
        burst = max(1, int(burst_str))
    except ValueError:
        burst = 10

    _write_limiter = _WriteRateLimiter(rate=rate, burst=burst)
    _write_limiter_initialized = True
    return _write_limiter


def reset_write_rate_limiter() -> None:
    """Drop the cached rate limiter so the next call re-reads env.

    Test seam — production code reads env once per process start.
    """
    global _write_limiter, _write_limiter_initialized
    _write_limiter = None
    _write_limiter_initialized = False


def redact_paths(text: str) -> str:
    """Replace absolute filesystem paths with their basename when the
    ``GNUCASH_REDACT_PATHS=1`` env var is set. Pass-through otherwise.

    Targets the case where MCP error messages might be shared
    externally (issue trackers, screenshots, public bug reports)
    and the user doesn't want their filesystem layout leaking.
    Default off — paths in errors are usually the most useful
    debugging signal for local development. Opt-in posture
    matches GNUCASH_LOG_DIR (no behavior change unless asked).

    Redaction is basename-only: ``/Users/alice/Books/alex.gnucash``
    becomes ``alex.gnucash``. The filename is preserved because
    the user still needs to know *which* file errored — the
    sensitive bit is the directory structure leading to it.

    Both POSIX (``/``) and Windows (``C:\\path`` / ``C:/path``)
    absolute paths are matched. Relative paths pass through
    unchanged because they don't leak filesystem layout.

    Args:
        text: Error message or other string that may contain
            absolute paths.

    Returns:
        ``text`` with absolute paths replaced by basename, or
        unchanged when redaction is disabled.
    """
    if os.environ.get("GNUCASH_REDACT_PATHS") != "1":
        return text

    import re

    # POSIX absolute paths: /foo/bar/baz.ext
    # Stop at whitespace, quotes, or common delimiter chars.
    posix_re = re.compile(r"/(?:[^\s/'\"<>]+/)+[^\s/'\"<>]+")
    # Windows: C:\foo\bar.ext or C:/foo/bar.ext
    win_re = re.compile(
        r"[A-Za-z]:[/\\](?:[^\s'\"<>]+[/\\])*[^\s'\"<>]+"
    )

    def to_basename(m):
        # Split on either separator so Windows paths matched on a
        # POSIX-running host (where ``Path("C:\\...").name`` would
        # return the whole string) still extract the leaf.
        full = m.group(0).replace("\\", "/")
        return full.rsplit("/", 1)[-1]

    # Windows first (more specific prefix); then POSIX.
    text = win_re.sub(to_basename, text)
    text = posix_re.sub(to_basename, text)
    return text


def resolve_mcp_dir(book_path: Path | str) -> Path:
    """Resolve the ``.mcp`` directory for audit / debug / backup storage.

    Honors the ``GNUCASH_LOG_DIR`` environment override — when set,
    that path is used directly (the user has opted into an explicit
    location, typically a user-private directory outside the book's
    folder). When unset, the directory is derived from
    ``book_path.parent / f"{book_path.name}.mcp"`` and two POSIX
    sanity checks fire:

      1. The parent directory must not be group- or world-writable
         (no sticky-bit exemption: that bit prevents non-owner
         deletion, but anyone with write access can still create
         a malicious ``.mcp`` symlink). Pre-existing entries that
         pass this check are still subject to (2).
      2. If a ``.mcp`` entry already exists, it must be a real
         directory owned by the current user — not a symlink, and
         not owned by another uid.

    Together these guard against the symlink-hijack vector where
    a hostile co-located process pre-creates ``{book}.mcp`` as a
    symlink to attacker-controlled storage before the server
    runs; the subsequent ``mkdir(parents=True, exist_ok=True)``
    would otherwise follow that symlink.

    On Windows the checks are skipped (POSIX mode bits and uid
    don't map cleanly); set ``GNUCASH_LOG_DIR`` explicitly if
    hardening is needed there.

    Args:
        book_path: Path to the ``.gnucash`` file. Accepts ``str``
            or ``Path``.

    Returns:
        Path to the ``.mcp`` directory. The caller creates
        ``audit/``, ``debug/``, ``backups/`` subdirectories as
        needed.

    Raises:
        ValueError: parent directory has unsafe permissions,
            OR an existing ``.mcp`` is a symlink, OR is owned by
            a different uid. ``GNUCASH_LOG_DIR`` set bypasses
            all checks (explicit user opt-in).
    """
    env_override = os.environ.get("GNUCASH_LOG_DIR")
    if env_override:
        return Path(env_override).expanduser()

    book_path = Path(book_path)
    parent = book_path.parent
    mcp_dir = parent / f"{book_path.name}.mcp"

    if os.name == "posix":
        try:
            mode = parent.stat().st_mode
            # 0o020 = group-write, 0o002 = world-write.
            # Reject regardless of sticky bit: the sticky bit
            # prevents non-owner *deletion/rename*, but any
            # principal with write access to the parent can
            # still *create* a new entry (e.g. a symlink named
            # ``{book}.mcp`` pointing to attacker-controlled
            # storage) before this process runs. The subsequent
            # mkdir(parents=True, exist_ok=True) would follow
            # that symlink. Sticky-bit-protected dirs like /tmp
            # are explicitly unsafe for this use; set
            # GNUCASH_LOG_DIR if the book really lives there.
            if mode & 0o022:
                raise ValueError(
                    f"Refusing to use {parent} for logs/backups: "
                    f"directory is group- or world-writable "
                    f"(mode={oct(mode & 0o777)}). A hostile co-"
                    f"located process could pre-create a "
                    f"malicious .mcp symlink redirecting log "
                    f"writes. Either tighten permissions "
                    f"(chmod go-w {parent}) or set "
                    f"GNUCASH_LOG_DIR to a user-private location."
                )
        except FileNotFoundError:
            # Parent missing — let downstream mkdir surface
            # the issue naturally with its own error.
            pass

        # Defense in depth: if a ``.mcp`` already exists at the
        # derived path, require it to be a real directory owned
        # by the current user. Catches the case where an earlier
        # attack already pre-created the symlink and the parent
        # has since been re-tightened.
        try:
            if mcp_dir.exists() or mcp_dir.is_symlink():
                if mcp_dir.is_symlink():
                    raise ValueError(
                        f"Refusing to use {mcp_dir}: path is a "
                        f"symlink. Logs/backups must live in a "
                        f"real directory you own; a symlink "
                        f"could redirect writes elsewhere. "
                        f"Remove or replace it, or set "
                        f"GNUCASH_LOG_DIR to a different path."
                    )
                st = mcp_dir.stat()
                if st.st_uid != os.geteuid():
                    raise ValueError(
                        f"Refusing to use {mcp_dir}: directory "
                        f"is owned by uid={st.st_uid}, not the "
                        f"current user (uid={os.geteuid()}). "
                        f"Logs/backups go to a directory you "
                        f"own; mismatched ownership suggests an "
                        f"earlier symlink/hijack attempt."
                    )
        except FileNotFoundError:
            pass

    return mcp_dir


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

    # Log directory lives alongside the book file by default
    # (e.g., /path/to/finances.gnucash → /path/to/finances.gnucash.mcp/),
    # or wherever GNUCASH_LOG_DIR points if set. The helper also
    # performs the parent-dir permission sanity check that
    # defends against symlink-hijack on shared-user POSIX hosts.
    log_dir = resolve_mcp_dir(book_path)
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

        # Restrict the audit file to owner read/write. Audit logs
        # contain transaction descriptions, account paths, dollar
        # amounts — not appropriate for the host's default umask
        # (which would commonly leave the file group/other
        # readable on multi-user systems). os.chmod is best-effort:
        # platforms without POSIX permission bits (Windows) silently
        # no-op, which is fine — the host's own ACLs apply there.
        try:
            import os as _os
            _os.chmod(audit_file, 0o600)
        except OSError:
            pass

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


def _fmt_person_update(entry: dict, type_label: str) -> list[str]:
    """Shared UPDATE renderer for customer / vendor / employee.

    Header line carries the entity id. Each field that actually
    changed renders as ``before → after``, with ``address``
    expanded to per-sub-field lines so a phone-number tweak is
    visible at a glance. Fields that didn't change are omitted —
    the response from the book layer is already a diff, so the
    audit log mirrors that shape.
    """
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    before = entry.get("before_state") or {}

    person_id = params.get("id", "") or after.get("id", "")
    lines = [f"{time_part}  UPDATE {type_label.upper()}  id:{person_id}"]

    # Top-level field diffs. Skip ``guid``/``id``/``status`` —
    # those are echo / metadata.
    SKIP = {"guid", "id", "status"}
    for key, new_val in after.items():
        if key in SKIP or key == "address":
            continue
        old_val = before.get(key)
        if old_val == new_val:
            continue
        lines.append(
            f"{_INDENT}{key}: {old_val!r} → {new_val!r}"
        )

    # Address sub-field diffs.
    addr_after = after.get("address") or {}
    addr_before = before.get("address") or {}
    if addr_after:
        lines.append(f"{_INDENT}address:")
        for key, new_val in addr_after.items():
            old_val = addr_before.get(key, "")
            lines.append(
                f"{_INDENT}  {key}: {old_val!r} → {new_val!r}"
            )

    return lines


def _fmt_customer_update(entry: dict) -> list[str]:
    return _fmt_person_update(entry, "customer")


def _fmt_vendor_update(entry: dict) -> list[str]:
    return _fmt_person_update(entry, "vendor")


def _fmt_employee_update(entry: dict) -> list[str]:
    return _fmt_person_update(entry, "employee")


def _fmt_customer_create(entry: dict) -> list[str]:
    return _fmt_person_create(entry, "customer")


def _fmt_customer_delete(entry: dict) -> list[str]:
    return _fmt_person_delete(entry, "customer", "customer_id")


def _fmt_vendor_create(entry: dict) -> list[str]:
    return _fmt_person_create(entry, "vendor")


def _fmt_vendor_delete(entry: dict) -> list[str]:
    return _fmt_person_delete(entry, "vendor", "vendor_id")


def _fmt_employee_create(entry: dict) -> list[str]:
    return _fmt_person_create(entry, "employee")


def _fmt_employee_delete(entry: dict) -> list[str]:
    return _fmt_person_delete(entry, "employee", "employee_id")


# ── Job formatters ───────────────────────────────────────────
# Jobs aren't business-persons (no currency/address fields), so
# they get their own formatters rather than going through
# _fmt_person_*. Owner_type and owner_id surface so a reviewer
# scanning the audit log can immediately see which counterparty
# the job belongs to without a separate lookup.


def _fmt_job_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    job_id = after.get("id", "")
    name = after.get("name", params.get("name", ""))
    owner_type = (
        after.get("owner_type") or params.get("owner_type", "")
    )
    owner_id = params.get("owner_id", "")
    ref = after.get("reference") or params.get("reference", "")
    lines = [
        f"{time_part}  CREATE JOB  id:{job_id}",
        f'{_INDENT}name: "{name}"  {owner_type}: {owner_id}',
    ]
    if ref:
        lines.append(f"{_INDENT}reference: {ref}")
    return lines


def _fmt_job_update(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state") or {}
    after = entry.get("after_state") or {}
    job_id = params.get("job_id", "")
    lines = [f"{time_part}  UPDATE JOB  id:{job_id}"]
    # Show only the fields that changed (the book method returns
    # only changed keys in after_state — anything missing means
    # unchanged, anything present means new value).
    for key in ("name", "reference", "active"):
        if key in after:
            old_val = before.get(key, "?")
            new_val = after[key]
            lines.append(
                f"{_INDENT}{key}: {old_val!r} → {new_val!r}"
            )
    return lines


def _fmt_job_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    job_id = params.get("job_id", "")
    name = after.get("name", "")
    reparented = after.get("reparented_count", 0)
    lines = [f"{time_part}  DELETE JOB  id:{job_id}"]
    if name:
        lines.append(f'{_INDENT}name: "{name}"')
    if reparented:
        # force=True re-parented invoices to the underlying
        # customer/vendor — call that out so a reviewer sees the
        # invoice owners changed as a side effect of the delete.
        lines.append(
            f"{_INDENT}reparented invoices: {reparented}"
        )
    return lines


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


def _fmt_taxtable_entry_line(e: dict) -> str:
    """Render one taxtable entry as ``5%→GST Payable`` or
    ``$5→Eco Fee Payable``. Mirrors the runtime
    ``_taxtable_entry_summary`` so audit-log rows look identical
    to what ``list_taxtables`` prints — leaf account name, no
    parent path prefix.

    Per CLAUDE.md the audit log canonicalizes account *parameter*
    references to fullnames (so a ``%shortguid`` input renders as
    ``Income:Sales``). This renderer is for taxtable-entry
    *structure* (metadata about the taxtable itself, not a user
    parameter), so the leaf-name convention matches the user's
    mental model from ``list_taxtables`` and keeps audit lines
    scannable.
    """
    type_val = e.get("type", "")
    amount = e.get("amount", "")
    # account_paths-resolved (preferred) → "account"; falls back
    # to "account_guid" for entries serialized without the path
    # map (shouldn't happen via our book methods, but defensive).
    acct = e.get("account") or e.get("account_guid", "?")
    # Trim to leaf name to match _taxtable_entry_summary's
    # ``e.account.name`` output. A path "Liabilities:GST Payable"
    # becomes "GST Payable"; a bare leaf or raw GUID passes
    # through unchanged.
    if ":" in acct:
        acct = acct.rsplit(":", 1)[-1]
    if type_val == "percentage":
        return f"{amount}%→{acct}"
    return f"${amount}→{acct}"


def _fmt_taxtable_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    name = after.get("name", params.get("name", ""))
    entries = after.get("entries") or params.get("entries") or []
    lines = [
        f"{time_part}  CREATE TAXTABLE",
        f'{_INDENT}name: "{name}"  entries: {len(entries)}',
    ]
    for e in entries:
        lines.append(f"{_INDENT}  {_fmt_taxtable_entry_line(e)}")
    return lines


def _fmt_taxtable_update(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    name = params.get("name", "")
    lines = [f'{time_part}  UPDATE TAXTABLE  name: "{name}"']
    changed = after.get("changed") or {}
    if "name" in changed:
        old = changed["name"].get("before", "?")
        new = changed["name"].get("after", "?")
        lines.append(f"{_INDENT}name: {old!r} → {new!r}")
    if "entries" in changed:
        before_entries = changed["entries"].get("before") or []
        after_entries = changed["entries"].get("after") or []
        lines.append(
            f"{_INDENT}entries: {len(before_entries)} → "
            f"{len(after_entries)}"
        )
        # Show the before/after detail; this is the audit
        # rationale for keeping the diff in the response.
        if before_entries:
            lines.append(f"{_INDENT}  before:")
            for e in before_entries:
                lines.append(
                    f"{_INDENT}    {_fmt_taxtable_entry_line(e)}"
                )
        if after_entries:
            lines.append(f"{_INDENT}  after:")
            for e in after_entries:
                lines.append(
                    f"{_INDENT}    {_fmt_taxtable_entry_line(e)}"
                )
    return lines


def _fmt_taxtable_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state") or {}
    name = params.get("name", "")
    entries = before.get("entries") or []
    lines = [
        f'{time_part}  DELETE TAXTABLE  name: "{name}"',
        f"{_INDENT}removed entries: {len(entries)}",
    ]
    for e in entries:
        lines.append(f"{_INDENT}  {_fmt_taxtable_entry_line(e)}")
    return lines


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

    # Plumb Bob bookkeeper-flagged: delete_invoice accepts ``id`` as
    # the preferred alias OR ``invoice_id`` for back-compat. Prefer
    # whichever the caller supplied; fall back to empty string so
    # the formatter never renders ``id:None``.
    inv_id = params.get("id") or params.get("invoice_id") or ""
    lines = [f"{time_part}  DELETE INVOICE  id:{inv_id}"]
    if after:
        entries = after.get("entries_deleted", 0)
        if entries:
            lines.append(f"{_INDENT}entries removed: {entries}")
    return lines


def _fx_stale_lines(entry: dict) -> list[str]:
    """Render the FX freshness-guard override line when present.

    ``post_invoice`` / ``pay_invoice`` attach an ``fx_stale`` block
    to the response only when ``force=True`` overrode the stale-rate
    guard. Surfacing "forced" here gives the bookkeeper a traceable
    record — a guard that was consciously overridden, unlike a
    warning that leaves no trace. Empty list when no override.

    Shared by the post and pay formatters; every bill/voucher/
    credit-note variant delegates to those two, so this one helper
    covers the whole business lifecycle.
    """
    after = entry.get("after_state") or {}
    fx = after.get("fx_stale")
    if not fx:
        return []
    return [
        f"{_INDENT}FX: {fx.get('currency', '')} rate "
        f"{fx.get('rate_used', '')} — {fx.get('age_days', '')} days "
        f"stale, forced (quoted {fx.get('rate_date', '')})"
    ]


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
    lines += _fx_stale_lines(entry)
    return lines


# ── Investment handlers ───────────────────────────────────────────


def _fmt_commodity_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    ns = after.get("namespace", params.get("namespace", ""))
    mnem = after.get("mnemonic", params.get("mnemonic", ""))
    lines = [f"{time_part}  CREATE COMMODITY  {ns}:{mnem}"]
    fullname = after.get("fullname", params.get("fullname", ""))
    if fullname:
        lines.append(f'{_INDENT}fullname: "{fullname}"')
    fraction = after.get("fraction", params.get("fraction"))
    if fraction is not None:
        lines.append(f"{_INDENT}fraction: {fraction}")
    return lines


def _fmt_price_create(entry: dict) -> list[str]:
    """``create_price`` returns ``status="updated"`` when a price at
    the same (commodity, currency, date, source) already existed and
    was overwritten; ``"created"`` otherwise. Surface that in the
    header verb so a human reading the log can tell a fresh write
    from an overwrite."""
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    ns = after.get("namespace", params.get("namespace", ""))
    comm = after.get("commodity", params.get("commodity", ""))
    status = after.get("status", "")
    verb = "UPDATE" if status == "updated" else "CREATE"
    lines = [f"{time_part}  {verb} PRICE  {ns}:{comm}"]
    date_str = after.get("date", params.get("price_date", "") or "")
    value = after.get("value", params.get("value", ""))
    currency = after.get("currency", params.get("currency", "") or "")
    if date_str or value:
        parts = []
        if date_str:
            parts.append(f"date: {date_str}")
        if value:
            parts.append(f"value: {value}{' ' + currency if currency else ''}")
        lines.append(f"{_INDENT}{'  '.join(parts)}")
    return lines


def _fmt_price_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state") or {}

    namespace = params.get("namespace", "")
    commodity = params.get("commodity", "")
    head = f"{time_part}  DELETE PRICE  {namespace}:{commodity}"
    if before:
        date_str = before.get("date", "")
        value = before.get("value", "")
        source = before.get("source", "")
        return [
            head,
            f"{_INDENT}date: {date_str}  value: {value}  source: {source}",
        ]
    return [head]


def _fmt_lot_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    title = after.get("title", params.get("title", ""))
    lines = [f'{time_part}  CREATE LOT  "{title}"']
    account = after.get("account", params.get("account", ""))
    if account:
        lines.append(f"{_INDENT}account: {account}")
    notes = after.get("notes", params.get("notes", ""))
    if notes:
        lines.append(f'{_INDENT}notes: "{notes}"')
    return lines


def _fmt_lot_update(entry: dict) -> list[str]:
    """Two operations register as ``lot:UPDATE`` —
    ``assign_split_to_lot`` (status=``"assigned"``) and
    ``close_lot`` (status=``"closed"``). The header verb branches on
    response status so the human reader can tell them apart at a
    glance."""
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    status = after.get("status", "")

    if status == "closed":
        title = after.get("title", "")
        if title:
            return [f'{time_part}  CLOSE LOT  "{title}"']
        return [f"{time_part}  CLOSE LOT  {params.get('guid', '')}"]

    # Default: assign_split_to_lot
    lines = [f"{time_part}  ASSIGN SPLIT TO LOT"]
    split_guid = params.get("split_guid", "")
    lot_guid = params.get("lot_guid", "")
    if split_guid:
        lines.append(f"{_INDENT}split: {split_guid}")
    if lot_guid:
        lines.append(f"{_INDENT}lot: {lot_guid}")
    if after.get("is_closed"):
        lines.append(
            f"{_INDENT}lot auto-closed (quantity reached zero)"
        )
    return lines


def _fmt_invoice_unpost(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state") or {}

    lines = [f"{time_part}  UNPOST INVOICE  id:{params.get('id', '')}"]
    if before:
        was_posted = before.get("date_posted", "") or ""
        was_account = before.get("post_account", "") or ""
        lines.append(
            f"{_INDENT}was posted:{was_posted}  "
            f"post_account:{was_account}"
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
    lines += _fx_stale_lines(entry)
    return lines


def _fmt_bill_post(entry: dict) -> list[str]:
    """Bill POST — same shape as invoice POST but with the right
    label so the audit log doesn't mis-categorize a vendor bill
    as a customer invoice. Pre-fix the (invoice, POST) handler
    fired for both because ``post_invoice`` accepts either."""
    lines = _fmt_invoice_post(entry)
    if lines:
        lines[0] = lines[0].replace("POST INVOICE", "POST BILL")
    return lines


def _fmt_bill_unpost(entry: dict) -> list[str]:
    """Bill UNPOST — same shape as invoice UNPOST with the right
    label."""
    lines = _fmt_invoice_unpost(entry)
    if lines:
        lines[0] = lines[0].replace("UNPOST INVOICE", "UNPOST BILL")
    return lines


def _fmt_bill_pay(entry: dict) -> list[str]:
    """Bill PAY — same shape as invoice PAY with the right label."""
    lines = _fmt_invoice_pay(entry)
    if lines:
        lines[0] = lines[0].replace("PAY INVOICE", "PAY BILL")
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

    # See _fmt_invoice_delete — same id/bill_id alias issue.
    bill_id = params.get("id") or params.get("bill_id") or ""
    lines = [f"{time_part}  DELETE BILL  id:{bill_id}"]
    if after:
        entries = after.get("entries_deleted", 0)
        if entries:
            lines.append(f"{_INDENT}entries removed: {entries}")
    return lines


# ── Voucher formatters ───────────────────────────────────────
# Vouchers (employee expense reimbursements) share the
# post/unpost/pay lifecycle code path with bills — the audit log
# decorator swaps entity_type from "invoice" to "voucher" when the
# response's ``type`` field is "voucher" (see the decorator's
# polymorphism block). These formatters mirror the bill ones with
# the right label.


def _fmt_voucher_post(entry: dict) -> list[str]:
    lines = _fmt_invoice_post(entry)
    if lines:
        lines[0] = lines[0].replace("POST INVOICE", "POST VOUCHER")
    return lines


def _fmt_voucher_unpost(entry: dict) -> list[str]:
    lines = _fmt_invoice_unpost(entry)
    if lines:
        lines[0] = lines[0].replace("UNPOST INVOICE", "UNPOST VOUCHER")
    return lines


def _fmt_voucher_pay(entry: dict) -> list[str]:
    lines = _fmt_invoice_pay(entry)
    if lines:
        lines[0] = lines[0].replace("PAY INVOICE", "PAY VOUCHER")
    return lines


def _fmt_voucher_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    voucher_id = after.get("id", "")
    employee_id = after.get("employee_id", params.get("employee_id", ""))
    return [
        f"{time_part}  CREATE VOUCHER  id:{voucher_id}",
        f"{_INDENT}employee: {employee_id}",
    ]


def _fmt_voucher_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state")

    # See _fmt_invoice_delete — same id/voucher_id alias issue.
    voucher_id = params.get("id") or params.get("voucher_id") or ""
    lines = [
        f"{time_part}  DELETE VOUCHER  "
        f"id:{voucher_id}"
    ]
    if after:
        entries = after.get("entries_deleted", 0)
        if entries:
            lines.append(f"{_INDENT}entries removed: {entries}")
    return lines


# ── Credit-note formatters ───────────────────────────────────
# Credit notes can be customer- or vendor-sided (owner_type 2 or
# 4); the credit-note flag is the differentiator. The formatters
# surface the side in the audit line because a reviewer scanning
# the log shouldn't have to cross-reference the source ID to
# know which kind of credit note was issued. ``applies_to`` is
# shown when the link is set.


def _fmt_credit_note_post(entry: dict) -> list[str]:
    """Credit note POST — same shape as invoice POST but
    explicitly labeled. The posting direction was reversed at
    the book layer; the audit log just reports what happened."""
    lines = _fmt_invoice_post(entry)
    if lines:
        lines[0] = lines[0].replace(
            "POST INVOICE", "POST CREDIT NOTE",
        )
    return lines


def _fmt_credit_note_unpost(entry: dict) -> list[str]:
    lines = _fmt_invoice_unpost(entry)
    if lines:
        lines[0] = lines[0].replace(
            "UNPOST INVOICE", "UNPOST CREDIT NOTE",
        )
    return lines


def _fmt_credit_note_pay(entry: dict) -> list[str]:
    """Credit note PAY — for credit notes, ``pay_invoice`` is
    the cash-refund path (the bookkeeper sent cash to a customer
    or received it from a vendor, depending on side). The
    audit-log label calls it out so a reviewer doesn't mistake
    a refund for a normal payment."""
    lines = _fmt_invoice_pay(entry)
    if lines:
        lines[0] = lines[0].replace(
            "PAY INVOICE", "REFUND CREDIT NOTE",
        )
    return lines


def _fmt_credit_note_apply(entry: dict) -> list[str]:
    """Credit note APPLY — the netting transaction that
    settles a credit note against an outstanding invoice/bill
    from the same owner. No cash moves; the credit balance just
    transfers between lots on the same A/R or A/P account.
    """
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    cn_id = params.get("credit_note_id", "")
    target_id = params.get("applies_to_invoice_id", "")
    amount = after.get("amount_applied", "")
    cn_remaining = after.get("credit_note_remaining", "")
    target_remaining = after.get("target_remaining", "")
    lines = [
        f"{time_part}  APPLY CREDIT NOTE  id:{cn_id}",
        (
            f"{_INDENT}applied: {amount}  "
            f"against: {target_id}"
        ),
    ]
    # ``apply_credit_note`` returns quantized strings like
    # "0.00", not "0", so a string-equality check against "0"
    # would print "remaining: 0.00" lines on fully-settled
    # documents. Decimal comparison handles every quantize
    # shape ("0", "0.00", "0.0000") uniformly.
    # (Copilot PR #87 review.)
    from decimal import Decimal as _D, InvalidOperation as _IO
    def _is_zero(s: str) -> bool:
        if not s:
            return True
        try:
            return _D(s) == 0
        except _IO:
            return False
    if not _is_zero(cn_remaining):
        lines.append(
            f"{_INDENT}credit note remaining: {cn_remaining}"
        )
    if not _is_zero(target_remaining):
        lines.append(
            f"{_INDENT}target remaining: {target_remaining}"
        )
    return lines


def _fmt_credit_note_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    cn_id = after.get("id", "")
    owner_type = params.get("owner_type", "")
    # owner_id key is owner-type-dependent: customer credit notes
    # surface customer_id; vendor credit notes surface vendor_id.
    # Pull whichever the after-state carries.
    owner_id = (
        after.get("customer_id")
        or after.get("vendor_id")
        or params.get("owner_id", "")
    )
    lines = [f"{time_part}  CREATE CREDIT NOTE  id:{cn_id}"]
    detail = f"{_INDENT}{owner_type}: {owner_id}"
    applies_to = after.get("applies_to")
    if applies_to:
        detail += f"  applies to: {applies_to.get('id', '')}"
    lines.append(detail)
    return lines


def _fmt_credit_note_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state")

    # See _fmt_invoice_delete — same id/credit_note_id alias issue.
    cn_id = params.get("id") or params.get("credit_note_id") or ""
    lines = [
        f"{time_part}  DELETE CREDIT NOTE  "
        f"id:{cn_id}"
    ]
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
    # Entry can belong to an invoice / bill / voucher / credit
    # note — the params carry whichever ID key the tool wrapper
    # used. First-match wins; all four are mutually exclusive in
    # practice.
    inv_id = (
        params.get("invoice_id", "")
        or params.get("bill_id", "")
        or params.get("voucher_id", "")
        or params.get("credit_note_id", "")
    )
    return [
        f"{time_part}  CREATE ENTRY",
        f'{_INDENT}"{desc}"  total: {total}  on: {inv_id}',
    ]


# ── Budget handlers ────────────────────────────────────────────────


def _fmt_budget_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    name = after.get("name", params.get("name", ""))
    lines = [f'{time_part}  CREATE BUDGET  "{name}"']
    info_parts = []
    num_periods = params.get("num_periods")
    if num_periods is not None:
        info_parts.append(f"periods: {num_periods}")
    period_type = params.get("period_type")
    if period_type:
        info_parts.append(f"type: {period_type}")
    year = params.get("year")
    if year is not None:
        info_parts.append(f"year: {year}")
    if info_parts:
        lines.append(f"{_INDENT}{'  '.join(info_parts)}")
    desc = params.get("description", "")
    if desc:
        lines.append(f'{_INDENT}description: "{desc}"')
    return lines


def _fmt_budget_update(entry: dict) -> list[str]:
    """``set_budget_amount`` is logged as ``budget UPDATE``. Renders
    the per-period before/after diff captured by the staged
    ``prior_amounts`` map."""
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state") or {}
    budget_name = params.get("budget_name", before.get("budget_name", ""))
    account = params.get("account", before.get("account", ""))
    new_amount = params.get("amount", "")

    lines = [
        f"{time_part}  UPDATE BUDGET",
        f'{_INDENT}"{budget_name}"  account: {account}',
    ]
    prior = before.get("prior_amounts") or {}
    if prior:
        # Show before/after per period. ``prior_amounts`` is keyed by
        # period number; sort numerically so the human reader sees
        # period 0, 1, 2, … in order.
        for p in sorted(prior, key=lambda k: int(k) if str(k).isdigit() else 0):
            old = prior[p]
            old_str = old if old is not None else "(unset)"
            lines.append(
                f"{_INDENT}period {p}: {old_str} → {new_amount}"
            )
    else:
        lines.append(f"{_INDENT}amount: {new_amount}")
    return lines


def _fmt_budget_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state") or {}
    name = before.get("name", params.get("name", ""))
    lines = [f'{time_part}  DELETE BUDGET  "{name}"']
    if before:
        np = before.get("num_periods")
        ac = before.get("amount_count")
        if np is not None:
            lines.append(
                f"{_INDENT}periods: {np}  amounts removed: {ac or 0}"
            )
    return lines


# ── Scheduled-transaction handlers ─────────────────────────────────


def _fmt_scheduled_transaction_create(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    name = after.get("name", params.get("name", ""))
    lines = [f'{time_part}  CREATE SCHEDULED  "{name}"']
    freq = after.get("frequency", params.get("frequency", ""))
    start = params.get("start_date", "")
    end = params.get("end_date", "")
    parts = []
    if freq:
        parts.append(f"frequency: {freq}")
    if start:
        parts.append(f"start: {start}")
    if end:
        parts.append(f"end: {end}")
    if parts:
        lines.append(f"{_INDENT}{'  '.join(parts)}")
    next_occ = after.get("next_occurrence")
    if next_occ:
        lines.append(f"{_INDENT}next: {next_occ}")
    return lines


def _fmt_scheduled_transaction_update(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state") or {}
    name = before.get("name", "")
    lines = [f'{time_part}  UPDATE SCHEDULED  "{name}"']
    if "enabled" in params and params["enabled"] is not None:
        old = before.get("enabled")
        new = bool(params["enabled"])
        if old is not None and old != new:
            lines.append(f"{_INDENT}enabled: {old} → {new}")
        else:
            lines.append(f"{_INDENT}enabled: {new}")
    if "end_date" in params and params["end_date"] is not None:
        old = before.get("end_date")
        new = params["end_date"] if params["end_date"] != "" else None
        old_str = old or "(none)"
        new_str = new or "(cleared)"
        if old != new:
            lines.append(f"{_INDENT}end_date: {old_str} → {new_str}")
    return lines


def _fmt_scheduled_transaction_delete(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    before = entry.get("before_state") or {}
    name = before.get("name", "")
    lines = [f'{time_part}  DELETE SCHEDULED  "{name}"']
    freq = before.get("frequency")
    start = before.get("start_date")
    instance_count = before.get("instance_count")
    if freq:
        lines.append(f"{_INDENT}frequency: {freq}  start: {start}")
    if instance_count:
        lines.append(
            f"{_INDENT}had run {instance_count} time"
            f"{'s' if instance_count != 1 else ''}"
        )
    return lines


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
    ("customer", "UPDATE"): _fmt_customer_update,
    ("customer", "DELETE"): _fmt_customer_delete,
    ("vendor", "CREATE"): _fmt_vendor_create,
    ("vendor", "UPDATE"): _fmt_vendor_update,
    ("vendor", "DELETE"): _fmt_vendor_delete,
    ("employee", "CREATE"): _fmt_employee_create,
    ("employee", "UPDATE"): _fmt_employee_update,
    ("employee", "DELETE"): _fmt_employee_delete,
    ("job", "CREATE"): _fmt_job_create,
    ("job", "UPDATE"): _fmt_job_update,
    ("job", "DELETE"): _fmt_job_delete,
    ("billterm", "CREATE"): _fmt_billterm_create,
    ("taxtable", "CREATE"): _fmt_taxtable_create,
    ("taxtable", "UPDATE"): _fmt_taxtable_update,
    ("taxtable", "DELETE"): _fmt_taxtable_delete,
    ("invoice", "CREATE"): _fmt_invoice_create,
    ("invoice", "DELETE"): _fmt_invoice_delete,
    ("invoice", "POST"): _fmt_invoice_post,
    ("invoice", "UNPOST"): _fmt_invoice_unpost,
    ("invoice", "PAY"): _fmt_invoice_pay,
    ("bill", "CREATE"): _fmt_bill_create,
    ("bill", "DELETE"): _fmt_bill_delete,
    ("bill", "POST"): _fmt_bill_post,
    ("bill", "UNPOST"): _fmt_bill_unpost,
    ("bill", "PAY"): _fmt_bill_pay,
    ("voucher", "CREATE"): _fmt_voucher_create,
    ("voucher", "DELETE"): _fmt_voucher_delete,
    ("voucher", "POST"): _fmt_voucher_post,
    ("voucher", "UNPOST"): _fmt_voucher_unpost,
    ("voucher", "PAY"): _fmt_voucher_pay,
    # Credit-note CREATE / DELETE for the create-side surface;
    # POST / UNPOST / PAY are reached via the polymorphic
    # entity_type swap (the lifecycle tools register as
    # ``entity_type="invoice"`` and the audit-decorator's
    # polymorphism handler rewrites it when the response type
    # is "credit_note"). APPLY is the netting operation
    # (``apply_credit_note``).
    ("credit_note", "CREATE"): _fmt_credit_note_create,
    ("credit_note", "DELETE"): _fmt_credit_note_delete,
    ("credit_note", "POST"): _fmt_credit_note_post,
    ("credit_note", "UNPOST"): _fmt_credit_note_unpost,
    ("credit_note", "PAY"): _fmt_credit_note_pay,
    ("credit_note", "APPLY"): _fmt_credit_note_apply,
    ("entry", "CREATE"): _fmt_entry_create,
    ("commodity", "CREATE"): _fmt_commodity_create,
    ("price", "CREATE"): _fmt_price_create,
    ("price", "DELETE"): _fmt_price_delete,
    ("lot", "CREATE"): _fmt_lot_create,
    ("lot", "UPDATE"): _fmt_lot_update,
    ("budget", "CREATE"): _fmt_budget_create,
    ("budget", "UPDATE"): _fmt_budget_update,
    ("budget", "DELETE"): _fmt_budget_delete,
    ("scheduled_transaction", "CREATE"): _fmt_scheduled_transaction_create,
    ("scheduled_transaction", "UPDATE"): _fmt_scheduled_transaction_update,
    ("scheduled_transaction", "DELETE"): _fmt_scheduled_transaction_delete,
}


_ACCOUNT_REF_KEYS_ALWAYS = frozenset({
    "account",
    "account_name",
    "post_account",
    "payment_account",
    "new_parent",
    "parent",
})

# Keys whose values are account refs ONLY for specific (entity_type,
# operation) pairs. ``name`` is the canonical example: it's the leaf
# name on CREATE ACCOUNT (e.g. ``"Groceries"``) but the full ref on
# UPDATE / MOVE / DELETE.
_ACCOUNT_REF_KEYS_CONDITIONAL: dict[tuple[str, str], frozenset[str]] = {
    ("account", "UPDATE"): frozenset({"name"}),
    ("account", "MOVE"): frozenset({"name"}),
    ("account", "DELETE"): frozenset({"name"}),
}


# R-3: ``_looks_like_guid_ref`` moved to ``book/_base.py`` so it
# sits next to ``_resolve_account`` (the chokepoint it gates) and
# can be shared by any future display surface that wants to skip
# already-canonical path strings before opening a session.


def _normalize_account_refs_for_audit(
    params: dict, entity_type: str, operation: str
) -> dict:
    """Audit-log-specific wrapper around
    :meth:`BaseGnuCashBook._normalize_account_refs`.

    R-3 split the responsibilities:

    - This function knows the audit-log-specific config — which
      param keys carry account refs always, which carry them only
      for specific ``(entity_type, operation)`` pairs.
    - The book layer owns the actual session-open + resolve + walk
      mechanics, alongside ``_resolve_account`` and the other
      chokepoints it depends on.

    Falls back to ``params`` unchanged when the book wrapper isn't
    available (server not fully wired up yet, or audit log fired
    during init) — log rendering still produces a useful line with
    raw refs.
    """
    if not params:
        return params
    if _get_book_func is None:
        return params
    try:
        book_wrapper = _get_book_func()
    except Exception:
        return params
    if book_wrapper is None:
        return params
    # Test-fixture / lightweight wrappers may not subclass
    # ``BaseGnuCashBook``; degrade gracefully rather than crash
    # audit-log rendering.
    if not hasattr(book_wrapper, "_normalize_account_refs"):
        return params

    keys_to_normalize = _ACCOUNT_REF_KEYS_ALWAYS | (
        _ACCOUNT_REF_KEYS_CONDITIONAL.get(
            (entity_type, operation),
        ) or frozenset()
    )
    return book_wrapper._normalize_account_refs(
        params, keys_to_normalize,
    )


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

    Account refs in ``params`` (``%shortguid`` or full 32-char GUIDs)
    are resolved to canonical full paths before handlers see them.
    Audit logs are read by humans; short GUIDs are convenient on the
    wire but would force a manual lookup at review time.
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

    # Substitute canonical fullnames in for any %short / full-GUID
    # account refs the LLM passed. Non-destructive: the source entry
    # (and the debug log already written from it) keeps the raw values.
    normalized_params = _normalize_account_refs_for_audit(
        entry.get("params") or {}, entity_type, operation
    )
    if normalized_params is not (entry.get("params") or {}):
        entry = {**entry, "params": normalized_params}

    lines = handler(entry)
    return "\n".join(lines) if lines else ""


def _normalize_for_audit(value):
    """Recursively convert pydantic models to plain dicts/primitives.

    The audit decorator captures the tool's ``kwargs`` into
    ``entry["params"]`` and also stringifies them into the debug log
    via ``json.dumps``. When a tool declares a pydantic model in its
    signature (e.g. ``splits: list[SplitInput]`` on
    ``create_transaction``), FastMCP hands us live model instances —
    not JSON-serializable, and the audit text formatters expect the
    raw dict shape (``split.get("account")``, etc.).

    This normalizer walks the kwargs once and produces an equivalent
    value with every pydantic model replaced by ``model_dump
    (exclude_none=True)``. Plain dicts, lists, and scalars pass
    through untouched (``exclude_none`` preserves the
    "key present iff value set" contract the book methods depend
    on — ``"quantity" in split`` keeps working).
    """
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, list):
        return [_normalize_for_audit(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize_for_audit(v) for k, v in value.items()}
    return value


def _extract_after_state(result: str, entity_type: str | None) -> dict | None:
    """Extract entity state from tool result JSON.

    Args:
        result: JSON string returned by tool
        entity_type: Audit entity type (e.g. "transaction", "invoice")

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
        operation: For writes: the operation verb ("create", "update",
                   "delete", "void", "post", "pay", ...) — uppercased to
                   match the (entity_type, operation) dispatch table.
        entity_type: Entity-type key in the audit dispatch table
                     ("transaction", "account", "invoice", "budget", ...).
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> str:
            logger = logging.getLogger(AUDIT_LOGGER_NAME)
            debug_logger = logging.getLogger(DEBUG_LOGGER_NAME)
            timestamp = datetime.now().astimezone().isoformat()

            # Write rate limit (Stage 6 #3). Default disabled —
            # enabled when the user sets GNUCASH_WRITE_RATE_LIMIT.
            # Checked BEFORE the auto-backup trigger and BEFORE
            # the pre-clear of staged audit state: a rate-limited
            # call hasn't started the tool, so it shouldn't
            # disturb the next call's audit-staging slot or
            # provoke a backup snapshot.
            if classification == "write":
                limiter = _get_write_rate_limiter()
                if limiter is not None:
                    allowed, retry = limiter.consume()
                    if not allowed:
                        debug_logger.warning(
                            f"Write rate limit hit on "
                            f"{func.__name__}; retry in "
                            f"{retry:.1f}s."
                        )
                        return json.dumps({
                            "error": (
                                f"Write rate limit exceeded "
                                f"(retry in {retry:.1f}s). "
                                f"Configured via "
                                f"GNUCASH_WRITE_RATE_LIMIT="
                                f"{os.environ.get('GNUCASH_WRITE_RATE_LIMIT')} "
                                f"tokens/sec."
                            ),
                            "error_type": "rate_limited",
                            "retry_after_seconds": round(retry, 2),
                        })

            # Defense-in-depth: clear any previously-staged audit
            # before-state at the TOP of the wrapper. The post-call
            # consume (success branch) and the exception-path clear
            # below are the primary cleanup paths, but if either one
            # itself errors out (e.g., the book wrapper transiently
            # unavailable), threading-local state can carry the
            # previous tool's before-state into this one — and that
            # tool would render an unrelated diff in its audit entry.
            # Pre-clearing here means every tool starts with a clean
            # threading-local slot regardless of what the previous
            # call did.
            if _get_book_func is not None:
                try:
                    pre_book = _get_book_func()
                    if pre_book is not None:
                        pre_book._consume_audit_before()
                except Exception:
                    pass

            # Normalize up front so pydantic models (e.g. list[SplitInput]
            # from the transaction-creating tools) become plain dicts before
            # they hit json.dumps in the debug line or the text-audit
            # formatters that read from entry["params"].
            normalized_kwargs = _normalize_for_audit(kwargs)

            entry = {
                "timestamp": timestamp,
                "tool": func.__name__,
                "classification": classification,
                "params": normalized_kwargs,
            }

            if classification == "write":
                entry["operation"] = operation
                entry["entity_type"] = entity_type

            debug_logger.debug(
                f"MCP request: tool={func.__name__} "
                f"params={json.dumps(normalized_kwargs)}"
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
                            # Invoice/bill/voucher/credit_note
                            # polymorphism: post_invoice /
                            # unpost_invoice / pay_invoice all carry
                            # entity_type="invoice" on the decorator
                            # but accept any of the four kinds.
                            # The response's ``type`` field is the
                            # truth — swap entity_type to match so
                            # the audit log doesn't mis-categorize.
                            # Credit notes can be customer- or
                            # vendor-sided; the type field is just
                            # "credit_note" regardless of owner_type
                            # (the formatter pulls the owner side
                            # from the response payload).
                            if (
                                entity_type == "invoice"
                                and result_data.get("type")
                                in {"bill", "voucher", "credit_note"}
                            ):
                                entry["entity_type"] = (
                                    result_data["type"]
                                )
                            after = _extract_after_state(
                                result, entry["entity_type"]
                            )
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
