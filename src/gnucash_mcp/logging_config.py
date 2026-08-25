"""Logging configuration for audit and debug logs.

Logs are stored alongside the GnuCash book file:
  /path/to/book.gnucash.mcp/
    audit/YYYY-MM-DD.jsonl
    debug/YYYY-MM-DD.log  (when --debug enabled)
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable

from gnucash_mcp._env import _TOGGLE_TRUE

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

    Refills at ``rate`` tokens/sec up to ``burst`` capacity — the
    bucket accommodates honest bursts (posting 5 invoices quickly)
    while throttling sustained runaway loops. ``consume()`` is
    thread-safe and returns a retry hint when denied.
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

        Returns ``(allowed, retry_after_sec)``; the retry value is
        meaningful only when denied.
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

    ``GNUCASH_WRITE_RATE_LIMIT`` (tokens/sec; absent or
    non-positive disables limiting — the cached None makes the
    per-call check a fast nullity test) and
    ``GNUCASH_WRITE_BURST`` (bucket size, default 10).
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
        # Non-numeric → unset, with a warning so the typo is seen.
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
    """Replace absolute filesystem paths with their basename when
    ``GNUCASH_REDACT_PATHS=1`` is set; pass-through otherwise.

    Opt-in (default off): paths in errors are usually the most
    useful local-debugging signal; redaction is for messages shared
    externally. Basename-only — the user still needs to know
    *which* file errored; the directory structure is the sensitive
    bit. POSIX and Windows absolute paths match; relative paths
    pass through (they don't leak layout).
    """
    # Full toggle vocabulary, not just "1" — the MCPB Advanced box
    # renders booleans as "true", and =="1" made that a silent no-op.
    if os.environ.get("GNUCASH_REDACT_PATHS", "").strip().lower() not in _TOGGLE_TRUE:
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

    ``GNUCASH_LOG_DIR`` set → a PER-BOOK subdirectory under it:
    ``{GNUCASH_LOG_DIR}/{book_filename}.mcp`` — the default layout,
    relocated. The subdir is what makes the override safe for
    multi-book: a flat shared directory interleaved two books' audit
    entries in one daily file under one book's header. Always
    per-book, never sniffed from what exists on disk — a
    conditional "reuse the flat layout if present" rule can never
    decide which book owns the flat files, so every book would
    match and the interleave would persist. v1.4.0-and-earlier flat
    files (``{GNUCASH_LOG_DIR}/audit`` …) stay on disk untouched;
    move them into the book's subdir manually to keep old history
    attached. Permission checks are bypassed under the override
    (explicit user opt-in), as before.

    Otherwise the directory is
    ``book_path.parent / f"{book_path.name}.mcp"`` and two POSIX
    sanity checks fire:

      1. The parent must not be group- or world-writable — no
         sticky-bit exemption: that bit prevents non-owner
         deletion, but anyone with write access can still *create*
         a malicious ``.mcp`` symlink.
      2. An existing ``.mcp`` entry must be a real directory owned
         by the current uid — not a symlink, not another owner.

    Together these block the symlink-hijack vector: a hostile
    co-located process pre-creating ``{book}.mcp`` as a symlink to
    attacker-controlled storage, which the subsequent
    ``mkdir(exist_ok=True)`` would follow. On Windows the checks
    are skipped (mode bits / uid don't map); set
    ``GNUCASH_LOG_DIR`` if hardening is needed there.

    Raises:
        ValueError: unsafe parent permissions, ``.mcp`` symlink,
            or foreign-uid ``.mcp``.
    """
    env_override = os.environ.get("GNUCASH_LOG_DIR")
    if env_override:
        base = Path(env_override).expanduser()
        return base / f"{Path(book_path).name}.mcp"

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

    # Lives alongside the book (or GNUCASH_LOG_DIR); the helper
    # also runs the symlink-hijack sanity checks.
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

        # Explicit UTF-8: under a C/POSIX locale (common for
        # daemonized MCP servers) the platform default encoding is
        # ASCII, and audit lines carrying localized account names
        # ("已实现获利(亏损)", "Erträge:…") would hit UnicodeEncodeError
        # inside the handler and be dropped to stderr.
        audit_handler = logging.FileHandler(audit_file, encoding="utf-8")
        audit_handler.setFormatter(logging.Formatter("%(message)s"))
        audit_handler.stream.reconfigure(line_buffering=True)
        audit_logger.addHandler(audit_handler)

        # Owner read/write only — audit logs carry financial data
        # the default umask would leave group/other readable.
        # Best-effort: Windows no-ops and its ACLs apply.
        try:
            import os as _os
            _os.chmod(audit_file, 0o600)
        except OSError:
            pass

        # Write header if needed. The trailing "\n" (plus the
        # logger's own newline) leaves a blank line after the
        # banner — get_audit_log splits entries on blank lines, so
        # without it the day's first entry glues to the header
        # block: excluded from the count, rendered on every page,
        # and leaked through limit=0.
        if write_header:
            header = _format_text_header(today, book_path, tz_name)
            audit_logger.info(header + "\n")
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

        debug_handler = logging.FileHandler(
            debug_dir / f"{today}.log", encoding="utf-8",
        )
        # PID in every line: MCP clients can spawn multiple server
        # processes against one config (observed: Claude Desktop
        # starts twins), and they all append to this same per-book
        # file. Without the PID, incident forensics cannot attribute
        # a line to a process — the exact wall the 2026-07-10
        # switch-timeout investigation hit.
        debug_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] [pid %(process)d] %(message)s",
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
        line = f"{indent}{short_name:<{max_name_len}}  {amount:>12}"
        if split.get("action"):
            line += f"  [{split['action']}]"
        if split.get("memo"):
            line += f"  {split['memo']}"
        lines.append(line)

    return "\n".join(lines)


def _resolve_entry_field(
    entry: dict, field: str, params_key: str | None = None
):
    """Look up a field in an audit entry, falling back through
    sources — write responses are trimmed, so fields the log wants
    (splits, description, date) may be absent from ``after_state``.

    Lookup order:

    1. ``after_state`` — the tool's response, richest when present
    2. ``params`` — the tool's inputs (``params_key`` names them
       when they differ, e.g. response "date" vs params
       "transaction_date")
    3. ``before_state`` — the pre-write snapshot

    Falsy values fall through to the next source; callers needing
    "really empty" vs "missing" consult the sources directly.
    Returns None when nothing resolves.
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
# Each (entity_type, operation) pair has a tiny handler returning a
# list of lines. Adding an entity type is one dict row, not another
# elif; unknown keys degrade to empty output so a new classification
# can't crash log rendering before its handler lands.

_INDENT = "          "  # 10 spaces; every handler indents its detail lines here
_INDENT_SPLITS = _INDENT + "  "  # nested indent for split blocks


def _extract_time(entry: dict) -> str:
    """Pull HH:MM:SS from an ISO-ish timestamp, defensively."""
    timestamp = entry.get("timestamp", "")
    if "T" in timestamp:
        return timestamp.split("T")[1][:8]
    return timestamp[:8] if len(timestamp) >= 8 else timestamp


def _transaction_guid(entry: dict) -> str:
    """GUID for a transaction log line — displayed as provided.
    Re-truncating would undo a birthday-problem extension (collapse
    a 9-char safe prefix back to a colliding 8).
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

    notes = after.get("notes") or params.get("notes") or ""
    if notes:
        lines.append(f"{_INDENT}notes: {notes}")

    # after_state preferred; fall back to params (thin-response case)
    splits = after.get("splits") or params.get("splits") or []
    if splits:
        lines.append(_format_splits_text(splits, _INDENT_SPLITS))
    return lines


def _fmt_transaction_create_from_scheduled(entry: dict) -> list[str]:
    """SX instantiation — the response carries ``transaction_guid``
    (not ``guid``), so the generic transaction handler would fall
    back to the params GUID, which is the SCHEDULE's. Read the
    response fields directly instead.
    """
    time_part = _extract_time(entry)
    after = entry.get("after_state") or {}
    sx_name = after.get("scheduled_transaction", "")
    date_str = after.get("transaction_date", "")

    if after.get("status") == "rejected":
        # Duplicate already posted for this period — the schedule
        # advanced but no transaction was written.
        return [
            f"{time_part}  CREATE FROM SCHEDULED  \"{sx_name}\" "
            f"({date_str})",
            f"{_INDENT}rejected: equivalent transaction already "
            f"exists for this period",
        ]

    guid = after.get("transaction_guid", "")
    desc = after.get("description", "")
    lines = [f"{time_part}  CREATE FROM SCHEDULED  guid:{guid}"]
    lines.append(f'{_INDENT}"{desc}" ({date_str})')
    detail = f'schedule: "{sx_name}"'
    instance = after.get("instance_count")
    if instance:
        detail += f"  instance #{instance}"
    lines.append(f"{_INDENT}{detail}")
    return lines


def _parse_audit_tsv_rows(tsv: str) -> list[dict]:
    """Header-bearing TSV string -> row dicts (display-only)."""
    if not tsv:
        return []
    rows = tsv.split("\n")
    header = rows[0].split("\t")
    return [dict(zip(header, r.split("\t"))) for r in rows[1:] if r]


def _parse_batch_submission(tsv: str) -> dict:
    """ref -> {description, date, notes?, splits} from the submitted
    batch TSV. Layout comes from the shared header reader
    (``_batch_tsv_layout``) so this display parse can't drift from
    the tool-layer parse; tolerant of malformed rows since this is
    display-only (a row the chunker rejects renders without its
    splits — the write path rejected such rows anyway).
    """
    from gnucash_mcp._format import (
        _BATCH_LEGACY_GROUP,
        _batch_row_splits,
        _batch_tsv_layout,
    )

    out: dict = {}
    lines = tsv.split("\n") if tsv else []
    if not lines:
        return out
    try:
        layout = _batch_tsv_layout(lines[0])
    except ValueError:
        # Malformed extension header — the write path rejected the
        # whole submission; render rows in the legacy shape.
        layout = {
            "has_notes": False, "has_cur": False,
            "notes_idx": None, "cur_idx": None, "fixed": 3,
            "group": _BATCH_LEGACY_GROUP,
        }
    fixed = layout["fixed"]
    for ln in lines[1:]:
        if not ln.strip():
            continue
        f = ln.split("\t")
        if len(f) < 3:
            continue
        try:
            splits = _batch_row_splits(f[fixed:], layout["group"])
        except ValueError:
            splits = []
        entry = {
            "description": f[2], "date": f[1].strip(), "splits": splits,
        }
        ni = layout["notes_idx"]
        if ni is not None and len(f) > ni and f[ni].strip():
            entry["notes"] = f[ni].strip()
        ci = layout["cur_idx"]
        if ci is not None and len(f) > ci and f[ci].strip():
            entry["currency"] = f[ci].strip().upper()
        out[f[0].strip()] = entry
    return out


def _fmt_transaction_create_batch(entry: dict) -> list[str]:
    """Batch create audits as N individual create blocks — one per
    committed transaction, each rendered like a single-entry create.
    Joins the submitted TSV (params) with the results TSV (after_state)
    by ref. Account refs render as the caller supplied them (the TSV
    isn't run through the audit ref-normalizer)."""
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    submitted = _parse_batch_submission(params.get("transactions") or "")
    results = _parse_audit_tsv_rows(after.get("results") or "")
    created = [r for r in results if r.get("status") == "created"]
    rejected = [r for r in results if r.get("status") == "rejected"]

    lines = [
        f"{time_part}  CREATE TRANSACTIONS (batch)  "
        f"{len(created)} created, {len(rejected)} rejected"
    ]
    for r in created:
        src = submitted.get(r.get("ref", ""), {})
        guid = r.get("txn_guid", "")
        desc = src.get("description", "")
        date_str = src.get("date", "")
        when = date_str
        if src.get("currency"):
            when = f"{date_str}, {src['currency']}"
        lines.append(
            f'{_INDENT}CREATE  guid:{guid}  "{desc}" ({when})'
        )
        if src.get("notes"):
            lines.append(f"{_INDENT_SPLITS}notes: {src['notes']}")
        reason = r.get("reason", "")
        if reason.startswith("auto_filled_from:"):
            # Splitless submission — the source guid is the trail to
            # what actually got booked.
            source = reason.split(":", 1)[1]
            lines.append(
                f"{_INDENT_SPLITS}auto-filled from guid:{source}"
            )
        splits = src.get("splits") or []
        if splits:
            lines.append(_format_splits_text(splits, _INDENT_SPLITS))
    return lines


def _fmt_transaction_update_batch(entry: dict) -> list[str]:
    """``update_transactions`` (TSV, per-row values) — old→new per
    touched field, old values from the staged before-state, new
    from the submitted TSV re-parsed through the SAME parser the
    tool used (no display drift)."""
    from gnucash_mcp._format import _parse_update_tsv

    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    before = entry.get("before_state") or {}
    befores = {
        (t.get("guid") or ""): t
        for t in before.get("transactions") or []
    }
    try:
        rows = _parse_update_tsv(params.get("updates") or "")
    except ValueError:
        rows = []
    lines = [
        f"{time_part}  UPDATE TRANSACTIONS (batch)  {len(rows)} rows"
    ]
    for r in rows[:15]:
        key = r["guid"]
        old = next(
            (
                b for g, b in befores.items()
                if g.startswith(key) or key.startswith(g[:8])
            ),
            {},
        )
        parts = [f"guid:{key}"]
        if "description" in r:
            old_d = old.get("description", "")
            parts.append(f'"{old_d}" → "{r["description"]}"')
        if "notes" in r:
            old_n = old.get("notes") or "(none)"
            parts.append(f"Notes: {old_n} → {r['notes']}")
        if "date" in r:
            old_dt = old.get("date", "")
            parts.append(f"Date: {old_dt} → {r['date']}")
        lines.append(f"{_INDENT}{'  '.join(parts)}")
    if len(rows) > 15:
        lines.append(f"{_INDENT}... and {len(rows) - 15} more")
    return lines


def _fmt_transaction_update(entry: dict) -> list[str]:
    time_part = _extract_time(entry)
    before = entry.get("before_state")
    guid = _transaction_guid(entry)

    # Broadcast form (guid list): one entry, shared values once,
    # then the touched transactions. Same dispatch key as single
    # update — the staged shape is the discriminator, mirroring
    # batch delete.
    if before and "transactions" in before:
        params = entry.get("params") or {}
        touched = before.get("transactions") or []
        lines = [
            f"{time_part}  UPDATE TRANSACTIONS (broadcast)  "
            f"{len(touched)} updated"
        ]
        if params.get("description") is not None:
            lines.append(
                f'{_INDENT}Description → "{params["description"]}"'
            )
        if params.get("transaction_date"):
            lines.append(
                f"{_INDENT}Date → {params['transaction_date']}"
            )
        if params.get("notes") is not None:
            new = params["notes"] or "(cleared)"
            lines.append(f"{_INDENT}Notes → {new}")
        for t in touched[:10]:
            lines.append(
                f'{_INDENT}  guid:{(t.get("guid") or "")[:8]}  '
                f'"{t.get("description", "")}"'
            )
        if len(touched) > 10:
            lines.append(f"{_INDENT}  ... and {len(touched) - 10} more")
        return lines

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

    # Notes are three-state at the tool boundary (text / "" clears /
    # absent leaves unchanged) — render only when the call carried
    # the field. Without this, a notes-only update logs as a no-op
    # entry: every field it DID print marked "(unchanged)".
    params = entry.get("params") or {}
    if "notes" in params and params["notes"] is not None:
        old_notes = before.get("notes") or None
        new_notes = params["notes"] or None
        if old_notes != new_notes:
            old_str = f'"{old_notes}"' if old_notes else "(none)"
            new_str = f'"{new_notes}"' if new_notes else "(cleared)"
            lines.append(f"{_INDENT}Notes: {old_str} → {new_str}")

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
    after = entry.get("after_state") or {}

    # Batch form: multi-guid delete_transaction. The response
    # carries short guids + descriptions; the composite before-state
    # carries dates and splits, in the same order.
    if "transactions" in after:
        after_txns = after.get("transactions") or []
        before_txns = (before or {}).get("transactions") or []
        count = after.get("count", len(after_txns))
        lines = [
            f"{time_part}  DELETE TRANSACTIONS (batch)  {count} deleted"
        ]
        for i, at in enumerate(after_txns):
            bt = before_txns[i] if i < len(before_txns) else {}
            lines.append(
                f'{_INDENT}DELETE  guid:{at.get("guid", "")}  '
                f'"{at.get("description", "")}" ({bt.get("date", "")})'
            )
            if bt.get("splits"):
                lines.append(
                    _format_splits_text(bt["splits"], _INDENT_SPLITS)
                )
        return lines

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
        notes = after.get("notes", params.get("notes", ""))
        if notes:
            lines.append(f'{_INDENT}Notes: "{notes}"')
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
        # ``notes`` is a diff-echo key: present in after_state only
        # when the update changed it ("" = cleared).
        if "notes" in after:
            old_notes = before.get("notes", "")
            if after["notes"]:
                lines.append(
                    f'{_INDENT}Notes: "{old_notes}" → "{after["notes"]}"'
                )
            else:
                lines.append(f'{_INDENT}Notes: "{old_notes}" → (cleared)')
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

    # The staged before-state lists the splits ACTUALLY reconciled —
    # the only source in bulk mode, where reconcile_all=true sends no
    # split_guids at all (reading only the param rendered
    # "Splits reconciled (0):" on a 51-split sweep). Params remain the
    # fallback for entries logged without a before-state.
    if split_details:
        reconciled = split_details
    else:
        reconciled = [{"guid": g} for g in (params.get("split_guids") or [])]

    lines = [
        f"{time_part}  RECONCILE  {params.get('account', '')}",
        f"{_INDENT}Statement date: {params.get('statement_date', '')}",
        f"{_INDENT}Statement balance: {_format_amount(params.get('statement_balance'))}",
    ]
    if params.get("reconcile_all"):
        mode = "bulk (reconcile_all)"
        if params.get("through_date"):
            mode += f", through {params['through_date']}"
        lines.append(f"{_INDENT}Mode: {mode}")
    lines.append(f"{_INDENT}Splits reconciled ({len(reconciled)}):")

    for split_info in reconciled[:10]:
        guid = split_info.get("guid", "")
        desc = split_info.get("transaction_description")
        if desc is None:
            lines.append(f"{_INDENT}  guid:{guid}")
        else:
            amount = _format_amount(split_info.get("amount"))
            lines.append(f'{_INDENT}  guid:{guid}  "{desc}"  {amount:>10}')
    if len(reconciled) > 10:
        lines.append(f"{_INDENT}  ... and {len(reconciled) - 10} more")

    # Exclusions are part of "what happened": a bulk sweep that
    # skipped a pending ACH should say so. Rendered as the caller
    # supplied them (prefixes that didn't resolve were ignored).
    excluded = params.get("except_guids") or []
    if excluded:
        lines.append(
            f"{_INDENT}Excluded ({len(excluded)}): "
            + ", ".join(f"guid:{g}" for g in excluded)
        )
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

    Changed fields render ``before → after`` (address expanded
    per-sub-field); unchanged fields are omitted — the book
    response is already a diff and the log mirrors it.
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
# Jobs aren't business-persons (no currency/address), so they get
# their own formatters; the owner surfaces so a reviewer sees the
# counterparty without a lookup.


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
    # after_state carries only changed keys.
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
        # force=True changed invoice owners as a side effect —
        # call it out.
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
    """Render one taxtable entry as ``5%→GST Payable`` /
    ``$5→Eco Fee`` — mirrors ``_taxtable_entry_summary`` so audit
    rows match ``list_taxtables``. Leaf names deliberately: the
    fullname-canonicalization rule covers account *parameters*;
    this is taxtable *structure*.
    """
    type_val = e.get("type", "")
    amount = e.get("amount", "")
    # "account" (path-resolved) preferred; "account_guid" defensive.
    acct = e.get("account") or e.get("account_guid", "?")
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

    # ``id`` is the preferred alias, ``invoice_id`` back-compat;
    # "" so the formatter never renders ``id:None``.
    inv_id = params.get("id") or params.get("invoice_id") or ""
    lines = [f"{time_part}  DELETE INVOICE  id:{inv_id}"]
    if after:
        entries = after.get("entries_deleted", 0)
        if entries:
            lines.append(f"{_INDENT}entries removed: {entries}")
    return lines


def _fx_stale_lines(entry: dict) -> list[str]:
    """Render the FX freshness-guard override line when present —
    ``fx_stale`` appears only when ``force=True`` overrode the
    guard, and the "forced" record gives the bookkeeper a trace.
    Shared by the post/pay formatters (every variant delegates to
    them). Empty list when no override.
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


def _fmt_price_create_batch(entry: dict) -> list[str]:
    """Batch price entry — one line per row from the results TSV
    (which is self-contained: status, commodity, date, value,
    currency), headlined by the created/updated/rejected counts."""
    time_part = _extract_time(entry)
    after = entry.get("after_state") or {}
    rows = _parse_audit_tsv_rows(after.get("results") or "")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("status", "")] = counts.get(r.get("status", ""), 0) + 1
    headline = ", ".join(
        f"{n} {status}" for status, n in sorted(counts.items())
    ) or "no rows"
    lines = [f"{time_part}  CREATE PRICES (batch)  {headline}"]
    for r in rows:
        status = r.get("status", "")
        if status in ("rejected",):
            lines.append(
                f"{_INDENT}{r.get('ref', '')}  rejected: "
                f"{r.get('reason', '')}"
            )
        else:
            cur = r.get("currency", "")
            lines.append(
                f"{_INDENT}{r.get('commodity', ''):<8}  "
                f"{r.get('value', ''):>12} {cur}  "
                f"({r.get('date', '')})  {status}"
            )
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
    memo = params.get("memo", "")
    if memo:
        lines.append(f"{_INDENT}memo: {memo}")
    lines += _fx_stale_lines(entry)
    return lines


def _fmt_bill_post(entry: dict) -> list[str]:
    """Bill POST — same shape as invoice POST but with the right
    label so the audit log doesn't mis-categorize a vendor bill
    as a customer invoice (``post_invoice`` accepts either)."""
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
# Vouchers reach these via the decorator's entity_type swap (the
# lifecycle tools register as "invoice"; the response's ``type``
# field is the truth). Same shape as the bill formatters.


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
# The audit line surfaces the side (customer/vendor) so a reviewer
# doesn't cross-reference the source ID; ``applies_to`` shows when
# linked.


def _fmt_credit_note_post(entry: dict) -> list[str]:
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
    """REFUND label — pay_invoice on a credit note is the
    cash-refund path; a reviewer must not mistake it for a normal
    payment."""
    lines = _fmt_invoice_pay(entry)
    if lines:
        lines[0] = lines[0].replace(
            "PAY INVOICE", "REFUND CREDIT NOTE",
        )
    return lines


def _fmt_credit_note_apply(entry: dict) -> list[str]:
    """APPLY — the no-cash netting of a credit note against an
    invoice/bill from the same owner."""
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
    # Decimal comparison, not string equality — the response holds
    # quantized strings ("0.00") that "== '0'" would mishandle.
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
    # The owner_id key is side-dependent (customer_id / vendor_id).
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
    # Whichever doc-ID key the tool wrapper used (mutually
    # exclusive in practice).
    inv_id = (
        params.get("invoice_id", "")
        or params.get("bill_id", "")
        or params.get("voucher_id", "")
        or params.get("credit_note_id", "")
    )
    lines = [
        f"{time_part}  CREATE ENTRY",
        f'{_INDENT}"{desc}"  total: {total}  on: {inv_id}',
    ]
    action = after.get("action", params.get("action", ""))
    notes = after.get("notes", params.get("notes", ""))
    if action or notes:
        detail = []
        if action:
            detail.append(f"action: {action}")
        if notes:
            detail.append(f"notes: {notes}")
        lines.append(f"{_INDENT}{'  '.join(detail)}")
    return lines


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
        # Per-period before/after, sorted numerically.
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
    description = params.get("description", "")
    if description and description != name:
        lines.append(f"{_INDENT}description: {description}")
    notes = params.get("notes", "")
    if notes:
        lines.append(f"{_INDENT}notes: {notes}")
    sx_currency = params.get("currency", "")
    if sx_currency:
        lines.append(f"{_INDENT}currency: {sx_currency}")
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
    if "notes" in params and params["notes"] is not None:
        old = before.get("notes")
        new = params["notes"] if params["notes"] != "" else None
        old_str = old or "(none)"
        new_str = new or "(cleared)"
        if old != new:
            lines.append(f"{_INDENT}notes: {old_str} → {new_str}")
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


def _fmt_statement_enter(entry: dict) -> list[str]:
    """``enter_statement`` audits as ONE document event — the
    statement landing (or rehearsing) as a whole, not N disconnected
    CREATE lines. Created rows render with their interpreted
    description; claimed rows render their annotation diffs from the
    staged before-state (a claim is a write to an existing
    transaction)."""
    time_part = _extract_time(entry)
    params = entry.get("params") or {}
    after = entry.get("after_state") or {}
    before = entry.get("before_state") or {}

    account = params.get("account", "")
    stmt_date = params.get("statement_date", "")
    opening = params.get("opening_balance", "")
    closing = params.get("closing_balance", "")
    dry = params.get("dry_run", True)

    verb = "ENTER STATEMENT (dry run)" if dry else "ENTER STATEMENT"
    lines = [
        f"{time_part}  {verb}  {account}  {stmt_date}  "
        f"opening {opening} → closing {closing}"
    ]
    summary = (after.get("summary") or "").replace("\n", " ")
    if summary:
        lines.append(f"{_INDENT}{summary}")
    tie = after.get("tie") or ""
    if tie and not dry:
        lines.append(f"{_INDENT}{tie}")
    if dry:
        return lines

    # Re-parse the submitted lines through the SAME parser the tool
    # used (no display drift); tolerate malformed input — the write
    # path already rejected it.
    from gnucash_mcp._format import _parse_statement_tsv

    try:
        submitted = {
            r["ref"]: r
            for r in _parse_statement_tsv(params.get("lines") or "")
        }
    except ValueError:
        submitted = {}
    results = _parse_audit_tsv_rows(after.get("results") or "")
    befores = {c.get("guid", ""): c for c in before.get("claims") or []}

    for r in results:
        ref = r.get("ref", "")
        src = submitted.get(ref, {})
        status = r.get("status", "")
        guid = r.get("guid", "")
        if status == "created":
            desc = src.get("description") or src.get("raw") or ""
            lines.append(
                f'{_INDENT}CREATE  guid:{guid}  "{desc}" '
                f'({src.get("date", "")}, {src.get("amount", "")})'
            )
            if src.get("notes"):
                lines.append(f"{_INDENT_SPLITS}notes: {src['notes']}")
            note = r.get("note", "")
            if note.startswith("auto_filled_from:"):
                lines.append(
                    f"{_INDENT_SPLITS}auto-filled from "
                    f"guid:{note.split(':', 1)[1]}"
                )
        elif status == "claimed":
            old = next(
                (
                    b for g, b in befores.items()
                    if g.startswith(guid) or guid.startswith(g[:8])
                ),
                {},
            )
            desc = old.get("description", "")
            lines.append(
                f'{_INDENT}CLAIM  split:{guid}  "{desc}" '
                f'({old.get("date", "")})  '
                f'state: {old.get("state", "")} → y'
            )
            new_memo = src.get("raw", "")
            if new_memo and new_memo != old.get("memo", ""):
                lines.append(
                    f"{_INDENT_SPLITS}memo: "
                    f"{old.get('memo', '') or '(empty)'} → {new_memo}"
                )
            new_notes = src.get("notes", "")
            if new_notes and new_notes != old.get("notes", ""):
                lines.append(
                    f"{_INDENT_SPLITS}notes: "
                    f"{old.get('notes', '') or '(empty)'} → "
                    f"{new_notes}"
                )
        elif status == "skipped_overlap":
            lines.append(
                f"{_INDENT}SKIP  split:{guid}  already reconciled"
            )
    return lines


# ── Dispatch table ────────────────────────────────────────────────
#
# Key shape: (entity_type, operation). Both in their canonical forms —
# entity_type lowercase, operation UPPERCASE. Adding a new entity type
# is one row here plus one handler function above.

_AUDIT_HANDLERS: dict[str, Callable[[dict], list[str]]] = {
    ("transaction", "CREATE"): _fmt_transaction_create,
    ("transaction", "CREATE_FROM_SCHEDULED"):
        _fmt_transaction_create_from_scheduled,
    ("transaction", "CREATE_BATCH"): _fmt_transaction_create_batch,
    ("transaction", "UPDATE_BATCH"): _fmt_transaction_update_batch,
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
    ("statement", "ENTER"): _fmt_statement_enter,
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
    # Credit-note POST/UNPOST/PAY arrive via the decorator's
    # entity_type swap; APPLY is apply_credit_note's netting op.
    ("credit_note", "CREATE"): _fmt_credit_note_create,
    ("credit_note", "DELETE"): _fmt_credit_note_delete,
    ("credit_note", "POST"): _fmt_credit_note_post,
    ("credit_note", "UNPOST"): _fmt_credit_note_unpost,
    ("credit_note", "PAY"): _fmt_credit_note_pay,
    ("credit_note", "APPLY"): _fmt_credit_note_apply,
    ("entry", "CREATE"): _fmt_entry_create,
    ("commodity", "CREATE"): _fmt_commodity_create,
    ("price", "CREATE"): _fmt_price_create,
    ("price", "CREATE_BATCH"): _fmt_price_create_batch,
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


def _normalize_account_refs_for_audit(
    params: dict, entity_type: str, operation: str
) -> dict:
    """Audit-log-specific wrapper around
    :meth:`BaseGnuCashBook._normalize_account_refs` — this side
    knows WHICH param keys carry refs (always vs per-operation);
    the book layer owns the resolve mechanics.

    Falls back to ``params`` unchanged when the book wrapper isn't
    available — the log line still renders with raw refs.
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


# The audit file's record boundary is a blank line, and its reader
# (get_audit_log) splits on "\n\n" — so a raw newline inside a
# user-controlled value (description, memo, notes, payee text from an
# imported statement) could forge an apparent entry, corrupt entry
# counts, or smuggle instructions to the LLM reading the log. Every
# string that reaches the audit file must pass through
# _escape_audit_value; entries go through the recursive walk at the
# _format_audit_entry_text chokepoint, and the decorator's error line
# escapes str(e) directly.
_AUDIT_UNSAFE_RE = re.compile(
    "[\\\\\\x00-\\x1f\\x7f"      # backslash + C0 controls + DEL
    "\\u2028\\u2029"              # line/paragraph separators
    "\\u200e\\u200f"              # LRM / RLM
    "\\u202a-\\u202e"             # bidi embedding/override controls
    "\\u2066-\\u2069]"            # bidi isolate controls
)

_AUDIT_MNEMONIC_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\r": "\\r",
                           "\t": "\\t"}


def _escape_audit_value(text: str) -> str:
    """Visibly escape control and direction-override characters.

    Backslash doubles first so the escaping is injective — a value
    that literally contained the two characters ``\\n`` stays
    distinguishable from one that contained a newline. Clean strings
    (the overwhelmingly common case) return unchanged, same object.
    """
    if not _AUDIT_UNSAFE_RE.search(text):
        return text

    def _sub(m: "re.Match[str]") -> str:
        ch = m.group(0)
        mnemonic = _AUDIT_MNEMONIC_ESCAPES.get(ch)
        if mnemonic is not None:
            return mnemonic
        cp = ord(ch)
        return f"\\x{cp:02x}" if cp < 0x100 else f"\\u{cp:04x}"

    return _AUDIT_UNSAFE_RE.sub(_sub, text)


# TSV blobs embedded in entries (batch submissions and results) are
# structural strings the handlers RE-PARSE — their tabs and newlines
# are separators, not user text, so the full escape would break the
# parse. Exempting them is sound for boundary forging: a cell cannot
# contain a raw newline or tab (it would have been a separator at
# tool-parse time). They get the TSV-preserving escape instead, which
# still neutralizes \r, stray C0 controls, and bidi overrides that
# could survive inside a cell.
_AUDIT_TSV_KEYS = frozenset({"transactions", "updates", "results",
                             "prices"})

_AUDIT_UNSAFE_TSV_RE = re.compile(
    "[\\\\\\x00-\\x08\\x0b-\\x1f\\x7f"  # C0 minus \t \n, + DEL
    "\\u2028\\u2029"
    "\\u200e\\u200f"
    "\\u202a-\\u202e"
    "\\u2066-\\u2069]"
)


def _escape_audit_tsv(text: str) -> str:
    """The cell-safe escape: preserves the structural tab/newline
    separators, escapes everything else unsafe."""
    if not _AUDIT_UNSAFE_TSV_RE.search(text):
        return text

    def _sub(m: "re.Match[str]") -> str:
        ch = m.group(0)
        mnemonic = _AUDIT_MNEMONIC_ESCAPES.get(ch)
        if mnemonic is not None:
            return mnemonic
        cp = ord(ch)
        return f"\\x{cp:02x}" if cp < 0x100 else f"\\u{cp:04x}"

    return _AUDIT_UNSAFE_TSV_RE.sub(_sub, text)


def _escape_audit_strings(value):
    """Recursively escape every string in an entry's data (values
    only — keys are schema-fixed, never user text). String values
    under _AUDIT_TSV_KEYS keep their structural separators."""
    if isinstance(value, str):
        return _escape_audit_value(value)
    if isinstance(value, dict):
        return {
            k: (
                _escape_audit_tsv(v)
                if k in _AUDIT_TSV_KEYS and isinstance(v, str)
                else _escape_audit_strings(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_escape_audit_strings(v) for v in value]
    return value


def _format_audit_entry_text(entry: dict) -> str:
    """Format an audit entry as human-readable text.

    Writes only; reads and unmapped (entity_type, operation) combos
    return "" so an unwired classification degrades silently.
    Account MOVE arrives as UPDATE-with-new_parent and is remapped
    before dispatch. Account refs in params are resolved to
    canonical fullnames first — the log is read by humans, and
    short GUIDs would force a lookup at review time.
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

    # Non-destructive — the source entry (and the debug log already
    # written from it) keeps the raw values.
    normalized_params = _normalize_account_refs_for_audit(
        entry.get("params") or {}, entity_type, operation
    )
    if normalized_params is not (entry.get("params") or {}):
        entry = {**entry, "params": normalized_params}

    # After normalization so resolved account names are covered too.
    # Escaping here, at the single dispatch chokepoint, covers every
    # handler and every field a handler might render.
    entry = _escape_audit_strings(entry)

    lines = handler(entry)
    if not lines:
        return ""
    # Belt to the escaping's suspenders: a blank line inside an
    # entry IS the record boundary, so none may survive whatever a
    # handler emits. Render any as a visible marker instead.
    return "\n".join(ln if ln.strip() else "\\n" for ln in lines)


def _normalize_for_audit(value):
    """Recursively convert pydantic models to plain dicts/primitives.

    FastMCP hands tools live model instances (e.g.
    ``list[SplitInput]``) — not JSON-serializable, and the audit
    formatters expect raw dict shapes. Each model becomes
    ``model_dump(exclude_none=True)``; ``exclude_none`` preserves
    the "key present iff value set" contract (``"quantity" in
    split`` keeps working). Plain values pass through.
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
        if "splits_reconciled" in data:
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

            # Rate limit checked BEFORE the auto-backup trigger and
            # the audit-state pre-clear: a rate-limited call never
            # started, so it must not disturb either.
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

            # Defense-in-depth pre-clear of staged audit state: if
            # the primary cleanup paths themselves error, the
            # previous tool's before-state would leak into this one
            # and render an unrelated diff. Every tool starts with
            # a clean threading-local slot.
            if _get_book_func is not None:
                try:
                    pre_book = _get_book_func()
                    if pre_book is not None:
                        pre_book._consume_audit_before()
                except Exception:
                    pass

            # Pydantic models → plain dicts before json.dumps and
            # the text formatters see them.
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

            # First write of the process triggers the auto-backup
            # check (BackupMixin's flag no-ops the rest). Silent on
            # failure — must never break a user's write.
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

                # Always consume staged before-state (even on reads)
                # to clear strays; returns None when nothing staged.
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
                            # The lifecycle tools register as
                            # entity_type="invoice" but accept all
                            # four document kinds — the response's
                            # ``type`` field is the truth; swap so
                            # the log doesn't mis-categorize.
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

                # Log a simple error line. Exception text embeds
                # user-controlled values (account names, descriptions
                # echoed by validators), so it passes the same escape
                # as formatted entries — a raw newline here could
                # forge an entry boundary just as well.
                time_part = timestamp.split("T")[1][:8] if "T" in timestamp else timestamp[:8]
                error_text = (
                    f"{time_part}  ERROR  {func.__name__}: "
                    f"{_escape_audit_value(str(e))}"
                )
                logger.info(error_text)
                logger.info("")
                _flush_logger(logger)
                raise

        # Expose the declared classification/operation on the wrapper.
        # @wraps copies __dict__ outward through later decorator layers
        # (safe_tool), so the registration layer can read this off the
        # function FastMCP stores and derive MCP ToolAnnotations
        # (readOnlyHint/destructiveHint/idempotentHint) from the same
        # declaration the audit log trusts — one source, no drift.
        wrapper.__audit_meta__ = {
            "classification": classification,
            "operation": operation,
        }
        return wrapper

    return decorator


def debug_log(message: str) -> None:
    """Log a debug message if debug logging is enabled."""
    logging.getLogger(DEBUG_LOGGER_NAME).debug(message)


def _flush_logger(logger: logging.Logger) -> None:
    """Flush all handlers for a logger."""
    for handler in logger.handlers:
        handler.flush()
