"""Backup tools: manual snapshot creation, listing, and retention.

Always registered — `_apply_module_filter` forces 'backup' into the
enabled set the same way it forces 'core'. Automatic backups happen
regardless (via `@audit_log`'s `_maybe_auto_backup` hook); these
tools give users the option to snapshot on demand, review what's on
disk, and prune manually.

Restore is deliberately NOT a tool — it's a documented filesystem
procedure performed with the server stopped. See
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

    # Classified as read-w.r.t.-the-book: these tools touch disk but
    # never mutate the GnuCash file. Read classification also prevents
    # the backup tools from triggering the auto-backup hook on
    # themselves (which the process-level flag would short-circuit
    # anyway, but the cleaner contract is "the book didn't change").
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
        (session / weekly / monthly stages) does not touch them. Use
        ``prune_backups(stage="manual")`` if you want to clean them
        up explicitly.

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

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_backups() -> str:
        """List all available backups, newest first.

        Returns a compact tab-separated table with one row per backup:
        ``stage``, ``timestamp`` (ISO UTC), ``age``, ``size`` (MB),
        and ``label`` (if any). Stages are ``session`` / ``weekly`` /
        ``monthly`` (automatic retention tiers) and ``manual`` (user-
        invoked, unlimited retention).
        """
        book = get_book()
        entries = book.list_backups()
        if not entries:
            return "No backups found."

        # Header + rows. TSV is compact and the LLM can parse it
        # without a schema reminder.
        lines = ["stage\ttimestamp\tage\tsize_mb\tlabel"]
        for e in entries:
            size_mb = f"{e['size_bytes'] / (1024 * 1024):.1f}"
            label = e.get("label") or ""
            lines.append(
                f"{e['stage']}\t{e['timestamp']}\t{e['age']}\t"
                f"{size_mb}\t{label}"
            )
        return "\n".join(lines)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def prune_backups(
        keep_last_n: int,
        stage: str | None = None,
        dry_run: bool = True,
    ) -> str:
        """Remove older backups, keeping the most recent N per stage.

        Default is ``dry_run=True`` — the response shows what WOULD
        be deleted without touching disk, so the caller can confirm
        before committing. Pass ``dry_run=False`` to actually delete.

        When ``stage`` is None (default), only auto-retention stages
        (session / weekly / monthly) are pruned. Manual backups are
        never auto-pruned; target them explicitly with
        ``stage="manual"``.

        Args:
            keep_last_n: Number of backups to retain per affected
                stage. Must be >= 0.
            stage: If set, only prune within this stage. One of
                ``session``, ``weekly``, ``monthly``, ``manual``.
            dry_run: When True (default), report only. When False,
                delete.
        """
        book = get_book()
        result = book.prune_backups(
            keep_last_n=keep_last_n,
            stage=stage,
            dry_run=dry_run,
        )
        return _json(result)
