"""Admin tools: account slots and audit log access.

Registered only when the 'admin' module is enabled via --modules.
"""

import re
from datetime import datetime

from gnucash_mcp.logging_config import (
    audit_log,
    get_log_dir,
)
from gnucash_mcp.tools._helpers import _json, safe_tool


# Audit log filenames are exactly ``YYYY-MM-DD.txt``. Validate
# ``log_date`` against this shape before constructing the path —
# unvalidated, ``Path(audit_dir) / f"{log_date}.txt"`` would happily
# interpolate ``../../../../etc/passwd`` and read arbitrary
# ``*.txt`` files. Prompt injection through any free-text field
# that surfaces into the audit log is the attack vector.
_LOG_DATE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
# An audit entry's first line: "HH:MM:SS  OPERATION ...".
_AUDIT_ENTRY_TS_RE = re.compile(r"\A\d{2}:\d{2}:\d{2}  ")


def register(mcp, get_book) -> None:
    """Attach admin tools to the FastMCP server.

    Args:
        mcp: FastMCP server instance.
        get_book: Callable returning the shared GnuCashBook instance.
    """

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_account_slots(
        account: str,
        key: str | None = None,
    ) -> str:
        """Read slots (custom metadata) from an account.

        Slots are key-value pairs stored on accounts for metadata like APR,
        credit limit, reward rates, or any custom data.

        Args:
            account: Account ref: full path (e.g., "Liabilities:Credit Cards:Capital One"), %short GUID, or full 32-char GUID.
            key: Specific slot key to retrieve. If omitted, returns all slots.
        """
        book = get_book()
        result = book.get_account_slots(account_name=account, key=key)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(
        classification="write",
        operation="set_slot",
        entity_type="account_slot",
    )
    def set_account_slot(
        account: str,
        key: str,
        value: str,
    ) -> str:
        """Set a custom metadata slot on an account.

        Stores a key-value pair on the account. Values are stored as strings.
        Use for APR, credit limits, reward rates, or any per-account metadata.

        Args:
            account: Account ref: full path (e.g., "Liabilities:Credit Cards:Capital One"), %short GUID, or full 32-char GUID.
            key: Slot key (e.g., "apr", "credit_limit").
            value: Slot value (always stored as string).
        """
        book = get_book()
        result = book.set_account_slot(account_name=account, key=key, value=value)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(
        classification="write",
        operation="delete_slot",
        entity_type="account_slot",
    )
    def delete_account_slot(
        account: str,
        key: str,
    ) -> str:
        """Remove one custom metadata slot from an account.

        Permanent, and surgical: only the named key is deleted —
        other slots, the account, and its transactions are untouched.
        Errors, changing nothing, if the account ref or key doesn't
        exist, or if the key contains '/' (reserved for internal
        hierarchical slots; user slots are flat). get_account_slots
        lists the removable keys; set_account_slot re-creates one.

        Args:
            account: Account ref: full path (e.g., "Liabilities:Credit Cards:Capital One"), %short GUID, or full 32-char GUID.
            key: Slot key to remove.
        """
        book = get_book()
        result = book.delete_account_slot(account_name=account, key=key)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_audit_log(
        log_date: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Read audit log entries for a date.

        Returns the human-readable text audit log, led by a
        ``Showing X-Y of Z audit entries (date)`` indicator. Each write
        operation (CREATE, UPDATE, DELETE, VOID, RECONCILE, etc.) is one
        entry separated by a blank line. Reads are not logged.

        Unlike the row-list tools, the window is anchored to the most
        recent entry: ``offset`` pages *backward* into history (offset=0
        is the newest page), since "what happened lately" is the usual
        question. ``limit=0`` returns the count only.

        Args:
            log_date: Date to read (YYYY-MM-DD). Defaults to today.
            limit: Page size (default 50). 0 = count only.
            offset: Entries to skip back from the most recent (default 0).
        """
        log_dir = get_log_dir()
        if not log_dir:
            return _json({"error": "Logging not initialized (no book path configured)"})

        audit_dir = log_dir / "audit"
        target_date = log_date or datetime.now().astimezone().strftime("%Y-%m-%d")
        # Path-traversal gate: target_date is interpolated into the
        # log path, so anything that isn't a literal YYYY-MM-DD
        # rejects before the join. Raise (rather than build the JSON
        # envelope inline) so safe_tool applies its standard shape,
        # logging, and path redaction.
        if not _LOG_DATE_RE.fullmatch(target_date):
            raise ValueError(
                f"Invalid log_date {target_date!r}: must be "
                f"YYYY-MM-DD (e.g. 2026-06-04)."
            )
        log_file = audit_dir / f"{target_date}.txt"

        if not log_file.exists():
            return f"No audit log for {target_date}"

        # Read cap: 2 MB covers ~10k audit entries — well past any
        # reasonable limit request; larger files get tail-only
        # treatment instead of a full in-memory read.
        _AUDIT_READ_CAP_BYTES = 2 * 1024 * 1024
        try:
            file_size = log_file.stat().st_size
        except OSError:
            return f"No audit log for {target_date}"
        if file_size > _AUDIT_READ_CAP_BYTES:
            with log_file.open("rb") as f:
                f.seek(-_AUDIT_READ_CAP_BYTES, 2)
                # Drop a possibly-truncated leading block before
                # decoding so we don't render a half-entry.
                raw = f.read()
            try:
                content = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                content = ""
            # Skip the first (likely partial) entry boundary.
            if "\n\n" in content:
                content = content.split("\n\n", 1)[1]
        else:
            content = log_file.read_text().strip()
        if not content:
            return f"No audit log for {target_date}"

        # Entries are separated by blank lines. The first block is the
        # day's header (box-drawing banner) — preserve it regardless of
        # paging so the reader knows date/timezone/book context.
        blocks = content.split("\n\n")
        header = None
        if blocks and "═" in blocks[0]:
            header, blocks = blocks[0], blocks[1:]
            # Files written before the banner gained its trailing
            # blank line glue the day's FIRST entry to the header
            # block — hiding it from the count, rendering it on
            # every page, and leaking it through limit=0. Split it
            # back out at the first timestamped line.
            header_lines = header.split("\n")
            for i, ln in enumerate(header_lines):
                if _AUDIT_ENTRY_TS_RE.match(ln):
                    header = "\n".join(header_lines[:i]).rstrip()
                    blocks.insert(0, "\n".join(header_lines[i:]))
                    break

        total = len(blocks)
        # Recency-anchored window: offset counts back from the newest
        # entry, so offset=0 is the most recent page. This inverts the
        # row-list tools' offset-from-start, matching how an audit log
        # is actually read. Indicator positions are 1-indexed in
        # chronological (oldest-first) order so they stay monotonic.
        if offset < 0:
            offset = 0
        if limit == 0:
            page = []
            indicator = f"Showing 0 of {total} audit entries ({target_date})"
        else:
            page_limit = limit if limit > 0 else 50
            end_idx = max(0, total - offset)
            start_idx = max(0, end_idx - page_limit)
            page = blocks[start_idx:end_idx]
            if page:
                indicator = (
                    f"Showing {start_idx + 1}-{end_idx} of {total} "
                    f"audit entries ({target_date})"
                )
            else:
                indicator = (
                    f"Showing 0 of {total} audit entries ({target_date})"
                )

        parts = [indicator] + ([header] if header else []) + page
        return "\n\n".join(parts)
