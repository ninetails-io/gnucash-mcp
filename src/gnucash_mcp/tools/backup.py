"""Backup tools: on-demand snapshot creation.

Always registered — `_apply_module_filter` forces 'backup' into the
enabled set the same way it forces 'core'. Automatic backups happen
regardless (via `@audit_log`'s `_maybe_auto_backup` hook), with
staged retention pruning them internally; this tool gives users the
option to snapshot on demand.

The backup store is deliberately APPEND-ONLY from the model's side:
the safety net exists to survive model mistakes, so no tool may
enumerate or delete it. Listing and manual pruning are filesystem
operations the user performs directly (the ``.mcp/backups``
directory beside the book), and restore is a documented procedure
performed with the server stopped — see
``docs/RESTORE_FROM_BACKUP.md``.
"""

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import _json, safe_tool


def register(mcp, get_book) -> None:
    """Attach backup tools to the FastMCP server.

    Args:
        mcp: FastMCP server instance.
        get_book: Callable returning the shared GnuCashBook instance.
    """

    # Classified as read-w.r.t.-the-book: this tool touches disk but
    # never mutates the GnuCash file. Read classification also prevents
    # it from triggering the auto-backup hook on itself (which the
    # process-level flag would short-circuit anyway, but the cleaner
    # contract is "the book didn't change").
    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def create_backup(label: str | None = None) -> str:
        """Create an on-demand backup of the GnuCash book.

        Uses SQLite's online backup API so the snapshot is safe even
        if the book is being read or written concurrently by GnuCash
        desktop. Verifies the copy with ``PRAGMA integrity_check``
        before declaring success; a failed check deletes the bad
        backup and raises.

        Manual backups are kept indefinitely — automatic retention
        (session / weekly / monthly stages) does not touch them. To
        review or remove backups, the user works with the files
        directly in the backup directory; no tool can list or delete
        them.

        The response includes a ``restore_hint`` describing the
        filesystem command to restore from this backup. Restore is a
        human-run filesystem operation, not an MCP tool — see
        ``docs/RESTORE_FROM_BACKUP.md`` for details.

        Args:
            label: Optional free-text label (sanitized to
                ``[A-Za-z0-9_-]``) appended to the filename for human
                context, e.g. ``"pre-recategorization"`` or
                ``"pre-tax-filing"``.
        """
        book = get_book()
        result = book.create_backup(stage="manual", label=label)
        return _json(result)
