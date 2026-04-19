"""Admin tools: account slots and audit log access.

Registered only when the 'admin' module is enabled via --modules.
"""

import json
from datetime import datetime

from gnucash_mcp.logging_config import (
    audit_log,
    get_audit_format,
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
        tool_filter: str | None = None,
        classification: str | None = None,
        limit: int = 50,
    ) -> str:
        """Read audit log entries.

        Args:
            log_date: Date to read (YYYY-MM-DD). Defaults to today.
            tool_filter: Filter by tool name.
            classification: Filter by "read" or "write".
            limit: Maximum entries to return (default 50).
        """
        log_dir = get_log_dir()
        if not log_dir:
            return _json({"error": "Logging not initialized (no book path configured)"})

        audit_dir = log_dir / "audit"
        target_date = log_date or datetime.now().astimezone().strftime("%Y-%m-%d")

        fmt = get_audit_format()
        primary_ext = "jsonl" if fmt == "json" else "txt"
        fallback_ext = "txt" if fmt == "json" else "jsonl"

        log_file = audit_dir / f"{target_date}.{primary_ext}"
        if not log_file.exists():
            log_file = audit_dir / f"{target_date}.{fallback_ext}"

        if not log_file.exists():
            if fmt == "json":
                return _json({"entries": [], "message": f"No audit log for {target_date}"})
            else:
                return f"No audit log for {target_date}"

        # Reading a .txt file
        if log_file.suffix == ".txt":
            content = log_file.read_text()
            lines = content.strip().split("\n")
            if len(lines) > limit:
                lines = lines[-limit:]
            text_content = "\n".join(lines)

            if fmt == "json":
                return _json({
                    "content": text_content,
                    "format": "text",
                    "note": (
                        "No .jsonl file found for this date. Returning .txt fallback. "
                        "Ensure GNUCASH_MCP_AUDIT_FORMAT=json is set when starting the server."
                    ),
                })
            else:
                return text_content

        # Reading a .jsonl file
        entries = []
        for line in log_file.read_text().strip().split("\n"):
            if not line:
                continue
            entry = json.loads(line)
            if tool_filter and entry.get("tool") != tool_filter:
                continue
            if classification and entry.get("classification") != classification:
                continue
            entries.append(entry)

        if fmt == "text":
            lines = []
            for entry in entries[-limit:]:
                ts = entry.get("timestamp", "")[:19]
                tool = entry.get("tool", "unknown")
                result = entry.get("result", "")
                lines.append(f"{ts}  {tool}  {result}")
            return "\n".join(lines) if lines else "No entries"

        return _json({"entries": entries[-limit:], "total_count": len(entries)})
