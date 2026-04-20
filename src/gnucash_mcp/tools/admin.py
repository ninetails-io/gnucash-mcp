"""Admin tools: account slots and audit log access.

Registered only when the 'admin' module is enabled via --modules.
"""

from datetime import datetime

from gnucash_mcp.logging_config import (
    audit_log,
    get_log_dir,
)
from gnucash_mcp.tools._helpers import _json, safe_tool


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
            account: Full account path (e.g., "Liabilities:Credit Cards:Capital One").
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
            account: Full account path (e.g., "Liabilities:Credit Cards:Capital One").
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
        """Remove a custom metadata slot from an account.

        Args:
            account: Full account path (e.g., "Liabilities:Credit Cards:Capital One").
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
    ) -> str:
        """Read audit log entries for a date.

        Returns the human-readable text audit log. Each write operation
        (CREATE, UPDATE, DELETE, VOID, RECONCILE, etc.) is one entry
        separated by a blank line. Reads are not logged.

        Args:
            log_date: Date to read (YYYY-MM-DD). Defaults to today.
            limit: Maximum entries to return (default 50). Most recent last.
        """
        log_dir = get_log_dir()
        if not log_dir:
            return _json({"error": "Logging not initialized (no book path configured)"})

        audit_dir = log_dir / "audit"
        target_date = log_date or datetime.now().astimezone().strftime("%Y-%m-%d")
        log_file = audit_dir / f"{target_date}.txt"

        if not log_file.exists():
            return f"No audit log for {target_date}"

        content = log_file.read_text().strip()
        if not content:
            return f"No audit log for {target_date}"

        # Entries are separated by blank lines. The first block is the
        # day's header (box-drawing banner) — preserve it regardless of
        # limit so the reader knows date/timezone/book context.
        blocks = content.split("\n\n")
        header = None
        if blocks and "═" in blocks[0]:
            header, blocks = blocks[0], blocks[1:]

        if len(blocks) > limit:
            blocks = blocks[-limit:]

        parts = ([header] if header else []) + blocks
        return "\n\n".join(parts)
