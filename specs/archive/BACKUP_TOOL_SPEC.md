# Backup Tool Specification
## GnuCash MCP Server Enhancement

**Author:** Claude (Opus 4.7) with Stephen
**Date:** 2026-04-19
**Status:** Draft — ready to implement
**Target release:** 1.3.0 (post-1.2.1)
**Module home:** `admin`

---

## Executive Summary

Users entrust this server with their real financial records. Every
other failure mode is recoverable — a buggy report can be re-run, a
wrong audit entry can be reviewed, a stale display can be refreshed.
Data loss is the one failure that can't be undone from within the
server.

This spec introduces three MCP tools (`create_backup`,
`list_backups`, `prune_backups`) plus automatic staged-retention
snapshots triggered from the audit decorator on the first write of
each day. Backups use SQLite's online backup API for atomic, safe
copies. Restore is deliberately NOT an MCP tool — it's a documented
filesystem procedure performed with the server stopped.

---

## Goals

1. **No silent data loss.** At least one daily snapshot should exist
   for any day the user wrote to the book, without the user needing
   to remember to back up.
2. **Historical depth without disk bloat.** Grandfather-father-son
   (GFS) staged retention gives coverage across timescales while
   bounding total backups at ~17.
3. **Atomic, safe copies.** SQLite online backup API — no mid-write
   corruption, even if GnuCash desktop is concurrently reading.
4. **Backup failure never fails the write.** If the disk is full,
   if the backup directory is read-only, if anything goes wrong —
   log, warn, but never abort the user's intended mutation.
5. **Restore remains a human decision.** Restoring is destructive;
   the tool surface explicitly does not expose it to avoid an LLM
   accidentally clobbering the live book.

---

## Non-Goals

- **Restore-via-MCP.** Out of scope. Documented filesystem procedure
  instead. If the server is broken enough to need a restore, we
  can't trust it to do one.
- **Remote / cloud destination.** Backups live on the same disk as
  the book. Off-site backup is the user's OS-level responsibility.
- **Encryption at rest.** Backups inherit the book's own protection
  (filesystem permissions). Adding encryption is a separate project.
- **Backup of audit logs or debug logs.** Those are ancillary; only
  the book itself is snapshotted.
- **Interactive / streaming backups.** No partial or incremental
  backups. Every backup is a full snapshot.

---

## Design Decisions

### Backup mechanism: SQLite online backup API

Python's stdlib exposes `sqlite3.Connection.backup()` which uses
SQLite's online backup interface — copies pages in chunks without
holding a long-lived write lock on the source. Safe to run while
GnuCash desktop or another process is reading or even writing to
the source book.

Alternative considered — **`shutil.copy2()`**: simpler but vulnerable
to mid-write corruption if the source SQLite file is being modified.
Would require taking an exclusive lock or forcing a WAL checkpoint
first. The online backup API does this correctly by default.

Alternative considered — **SQL dump**: human-readable, largest,
slowest, and loses binary-identical representation. Overkill for
this use case.

**Winner:** SQLite online backup API, accessed via piecash's
underlying connection.

```python
# Pseudocode
with self.open(readonly=True) as book:
    source_conn = book.session.connection().connection  # raw sqlite3 conn
    dest_conn = sqlite3.connect(str(backup_path))
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
```

### Post-backup integrity check

Run `PRAGMA integrity_check` on the new file immediately after the
copy. Takes ~50ms on a typical book; returns `"ok"` on success or a
diagnostic string on failure. If the check fails, delete the bad
backup and raise loudly — better than leaving a silent time-bomb.

```python
dest_conn = sqlite3.connect(str(backup_path))
result = dest_conn.execute("PRAGMA integrity_check").fetchone()[0]
dest_conn.close()
if result != "ok":
    backup_path.unlink()
    raise RuntimeError(f"Backup integrity check failed: {result}")
```

### Backup location

`{book_path}.mcp/backups/` — consistent with audit and debug logs
already living under `{book_path}.mcp/`. Users who back up their
GnuCash folder (e.g., via Time Machine) automatically pick up the
snapshots too.

```
/Users/stephen/Finances/
  books.gnucash                   ← the live book
  books.gnucash.mcp/
    audit/
      2026-04-19.txt
    debug/
      2026-04-19.log
    backups/                      ← new
      .state.json
      books-2026-04-19T192345-session.gnucash
      books-2026-04-19T192345-session.gnucash.integrity-ok
      ...
```

### Filename convention

`{book_stem}-{ISO-timestamp}-{stage}[-{label}].gnucash`

Examples:
- `books-2026-04-19T192345-session.gnucash`
- `books-2026-04-13T080000-weekly.gnucash`
- `books-2026-04-19T210110-manual-pre-recategorization.gnucash`

Timestamp uses a filesystem-safe ISO-8601 variant (colons replaced
with nothing — `T192345` rather than `T19:23:45`). UTC timezone
assumed; store in UTC so backups sort correctly regardless of
daylight-saving transitions.

### Staged retention (GFS)

Four stages, each with its own minimum spacing and retention count:

| Stage | Minimum spacing | Keep last N | Purpose |
|---|---|---|---|
| `session` | 12 hours | 7 | "Earlier today / yesterday — undo recent mistakes" |
| `weekly` | 7 days | 4 | "Last week's state — before the bad batch operation" |
| `monthly` | 30 days | 6 | "Pre-tax-season snapshot" |
| `manual` | n/a | ∞ (unlimited) | User-invoked via `create_backup(label=...)`; never auto-pruned |

**A backup is promoted to the highest stage whose interval has
elapsed.** On any given write trigger:
1. Check each stage (in order: monthly → weekly → session) — if the
   stage's interval has elapsed since its last backup, mark that
   stage as "due."
2. Create a single backup. If multiple stages are due, the file is
   tagged with the highest-priority stage (monthly > weekly >
   session); also update the state-file timestamps for all due
   stages so they don't each trigger an additional redundant backup.
3. Prune each stage's backups to its `keep_last_n` count.

Total steady-state disk usage: up to 7+4+6 = 17 automatic backups +
any manual ones, e.g., 17 × 50 MB = 850 MB for a mid-sized book.
Manual backups grow unbounded — user's choice.

### Auto-backup triggering

**Hook location:** inside `audit_log` decorator in `logging_config.py`,
just before the tool call when `classification == "write"`. Mirrors
the existing `_consume_audit_before()` pattern.

**Trigger gate:** a process-level flag plus the state file ensure:
- Once per Python process lifecycle at most
- Plus: state file's stage timestamps must indicate a stage is due

Process flag prevents redundant stat + JSON read on every subsequent
write after the first. Once a process has done its backup check,
it's done until restart.

```python
# Pseudocode in audit_log decorator
_BACKUP_CHECKED_IN_PROCESS = False

def wrapper(*args, **kwargs):
    ...
    if classification == "write":
        global _BACKUP_CHECKED_IN_PROCESS
        if not _BACKUP_CHECKED_IN_PROCESS:
            _BACKUP_CHECKED_IN_PROCESS = True
            if _get_book_func:
                try:
                    book = _get_book_func()
                    book._maybe_auto_backup()  # fires only if a stage is due
                except Exception as e:
                    # Never fail the write because of a backup miss
                    debug_logger.warning(f"Auto-backup skipped: {e}")
    ...
```

Why this works across the Claude Desktop double-spawn noise we
diagnosed earlier: Desktop's probe processes don't issue writes, so
they never reach the trigger. The serving process that does issue
writes runs the check exactly once.

### State file schema

`{book_path}.mcp/backups/.state.json`

```json
{
  "version": 1,
  "last_backup_by_stage": {
    "session": "2026-04-19T19:23:45+00:00",
    "weekly":  "2026-04-13T08:00:00+00:00",
    "monthly": "2026-04-01T08:00:00+00:00"
  }
}
```

Only stages that have ever had an auto-backup appear in
`last_backup_by_stage`. Missing keys mean the stage has never run —
which fires it on the next write (desired behavior: first write
after deploying the backup tool generates all three tiers if the
intervals allow).

### Error handling

All auto-backup paths swallow exceptions after logging. The write
proceeds. The audit decorator records whatever happened in the
debug log.

Manual `create_backup()` tool calls, by contrast, raise on failure
— the caller explicitly asked for a backup and deserves to know if
it failed.

### Restore procedure

Explicitly documented in:
- `create_backup()` tool response (every call includes a short
  restore hint in its output)
- A `docs/RESTORE_FROM_BACKUP.md` file created alongside this spec
  when the feature lands

Procedure:
1. Stop the MCP server (disconnect from Claude Desktop / Claude
   Code; kill any running Python process).
2. Move the current book to a recovery name:
   `mv books.gnucash books.gnucash.broken`
3. Copy the chosen backup:
   `cp books-2026-04-19T192345-session.gnucash books.gnucash`
4. Restart the server.
5. Verify with `get_book_summary` that balances look right.

**Not an MCP tool because:**
- Restore is destructive — clobbering the live book is exactly the
  kind of action that must be intentional, not an LLM autocomplete
  accident.
- If the server is broken, we can't trust it to do a correct
  restore (filesystem ops, path resolution, race conditions).
- File move is simple enough for a non-technical user with the
  five-step guide above.

---

## Tool API

### create_backup

```python
def create_backup(label: str | None = None) -> dict
```

**Description:** Create an immediate backup of the GnuCash book.
Uses SQLite's online backup API (safe even if the book is being
read or written by another process). Verifies the copy via
`PRAGMA integrity_check` before declaring success.

**Args:**
- `label`: Optional free-text label appended to the filename for
  human context (e.g., `"pre-recategorization"`). Sanitized to
  filesystem-safe characters (alphanumeric, dash, underscore).

**Returns dict:**
```json
{
  "status": "created",
  "stage": "manual",
  "path": "/Users/.../books.gnucash.mcp/backups/books-2026-04-19T192345-manual-pre-reorg.gnucash",
  "size_bytes": 47580160,
  "integrity": "ok",
  "restore_hint": "Stop the server, then: mv books.gnucash books.gnucash.broken && cp <path> books.gnucash"
}
```

**Raises:**
- `IOError` if the backup directory cannot be created / written.
- `RuntimeError` if the integrity check fails (bad backup is deleted
  before raising).

### list_backups

```python
def list_backups() -> str
```

**Description:** List all available backups across every stage.
Returns a compact text format suitable for LLM consumption.

**Returns string:**
```
stage    timestamp              age           size     label
------   -------------------    -----------   ------   --------------------
session  2026-04-19T19:23:45    just now      45.4MB
session  2026-04-18T10:30:12    1 day ago     45.4MB
weekly   2026-04-13T08:00:00    6 days ago    45.0MB
weekly   2026-04-06T08:00:00    13 days ago   44.8MB
monthly  2026-04-01T08:00:00    18 days ago   44.5MB
monthly  2026-03-01T08:00:00    49 days ago   42.1MB
manual   2026-04-14T21:01:10    5 days ago    45.0MB    pre-tax-review
```

No `verbose=True` variant; full paths aren't useful in this view.
To get a specific backup's path for restore instructions, look in
the `.backups/` directory directly.

### prune_backups

```python
def prune_backups(
    keep_last_n: int,
    stage: str | None = None,
    dry_run: bool = True,
) -> dict
```

**Description:** Remove old backups, keeping the most recent N per
stage (or within a specific stage). Default `dry_run=True` forces
the user to confirm the deletion list before committing.

**Args:**
- `keep_last_n`: Number of backups to retain per stage.
- `stage`: If set, prune only backups in this stage. If None, prune
  all auto stages (session/weekly/monthly); manual backups are
  NEVER auto-pruned — they must be pruned explicitly via
  `stage="manual"`.
- `dry_run`: If True (default), report what would be deleted without
  deleting. Pass `dry_run=False` to actually delete.

**Returns dict:**
```json
{
  "dry_run": true,
  "would_delete": [
    {"path": "...", "stage": "session", "age": "8 days ago"}
  ],
  "would_keep": [
    {"path": "...", "stage": "session", "age": "just now"},
    ...
  ]
}
```

Or for `dry_run=False`:
```json
{
  "deleted": [...],
  "kept": [...]
}
```

---

## Internal Architecture

### File layout

Option A — extend `book/admin.py`:
- Add the backup methods as new mixin methods on `AdminMixin`.
- Pro: fits the existing module-mixin pattern.
- Con: mixes slot-CRUD responsibilities with backup responsibilities
  in one module.

Option B — new `book/backup.py` + `BackupMixin`:
- Create a dedicated mixin similar to the other per-feature mixins.
- Add `"backup"` to `_MIXIN_MAP` in `book/__init__.py`.
- Pro: single responsibility per module; easier to find / test /
  disable.
- Con: one more file; but the project already has one-module-per-
  feature.

**Recommendation: Option B.** Follow the established pattern. The
backup tool surface is distinct enough from slot management that it
earns its own file.

Tools go in `tools/admin.py` though — they're still administrative
tools, and the `admin` module is always enabled when any of the
backup tools are wanted. Alternatively, add `"backup"` as its own
tool module entry in `TOOL_MODULES`; this makes backup tools opt-in
separately from slot tools. The latter is cleaner. Pick at
implementation time based on what feels right.

### BaseGnuCashBook additions

Add a single public entry point plus one auto-backup-check helper:

```python
def create_backup(
    self,
    *,
    stage: str = "manual",
    label: str | None = None,
) -> dict:
    """Implementation of the MCP tool. Used directly for manual
    backups and indirectly (via `_maybe_auto_backup`) for the auto
    stages.
    """

def _maybe_auto_backup(self) -> None:
    """Check the state file; if any stage is due, do one backup tagged
    with the highest-priority due stage and update all due stages'
    timestamps. Swallow all exceptions (log them instead).
    """
```

### Stage logic

```python
_AUTO_STAGES: list[tuple[str, timedelta, int]] = [
    ("session", timedelta(hours=12), 7),
    ("weekly",  timedelta(days=7),   4),
    ("monthly", timedelta(days=30),  6),
]
```

On auto-backup trigger:
1. Read state file (if missing, treat as "all stages due").
2. For each stage, compute `due = now - last_backup_for_stage > interval`.
3. If any are due, pick the highest-priority one (monthly > weekly >
   session) as the stage label for the new backup file.
4. Create the backup, tagged with that stage.
5. Update the state file's `last_backup_by_stage` for EVERY due
   stage (not just the highest) — this avoids duplicate backups
   on the next write.
6. For each auto stage, list existing backups matching that stage's
   filename pattern and prune down to `keep_last_n`.

### Concurrency

Two writes overlapping is already serialized by SQLite's file lock
at the session level. But two processes hitting `_maybe_auto_backup`
simultaneously could race on the state file. Acceptable in practice
because:
- Both would create a backup (worst case: two snapshots instead of
  one).
- Both would update the state file (last writer wins).
- The total count cap (`keep_last_n`) prevents unbounded growth.

If this ever becomes a real problem, add a file-lock around the
state-file-write. Not expected for the current single-user MCP
usage.

---

## Testing Strategy

### Unit tests (offline, temp books)

1. `test_create_backup_produces_valid_file`: create a backup,
   open it as a piecash book, verify it matches source content.
2. `test_create_backup_integrity_check_ok`: PRAGMA integrity_check
   succeeds on a fresh backup.
3. `test_create_backup_integrity_failure_deletes_bad_file`: force a
   bad integrity result (mock), verify file is deleted and
   RuntimeError raised.
4. `test_backup_label_sanitized`: labels with filesystem-unsafe
   characters (slashes, colons) produce valid filenames.
5. `test_list_backups_sorts_newest_first`: create three backups
   with different timestamps, verify ordering.
6. `test_prune_respects_keep_last_n`: create 10 session backups,
   `prune_backups(keep_last_n=3, stage="session", dry_run=False)`,
   verify 3 remain (the 3 newest).
7. `test_prune_defaults_to_dry_run`: call without `dry_run=False`,
   verify nothing is deleted.
8. `test_prune_never_touches_manual_unless_asked`: create 5 manual
   backups and 5 session backups, call
   `prune_backups(keep_last_n=2)` (no stage), verify all 5 manual
   remain and 2 session remain.

### Auto-backup tests

9. `test_auto_backup_fires_first_write_when_stage_due`: new book,
   no state file, issue a write, verify a session backup was
   created.
10. `test_auto_backup_skips_when_within_interval`: state file says
    session backup was 1 hour ago, issue a write, verify NO new
    backup (interval is 12 hours).
11. `test_auto_backup_once_per_process`: two writes in the same
    process, verify only one backup check fires (state file read
    once).
12. `test_auto_backup_failure_does_not_fail_write`: mock
    `_maybe_auto_backup` to raise, issue a write, verify write
    succeeds and error was logged.
13. `test_auto_backup_all_stages_from_scratch`: empty state file,
    verify the first auto-backup updates all stages'
    `last_backup_by_stage` entries to the same timestamp.
14. `test_auto_backup_promotes_to_highest_due_stage`: state file
    shows session from 13 hours ago, weekly from 8 days ago,
    verify the new backup is tagged `"weekly"` (not session).

### Integration / regression

15. `test_backup_while_write_in_progress`: not strictly
    deterministic but can be approximated — kick off a write, then
    call `create_backup` in a thread, verify both complete and the
    backup is valid. (Low priority — online backup API is
    documented to handle this.)

---

## Open Questions

1. **Should `list_backups` include the book's own size for
   comparison?** E.g., "book is 47.2 MB; you have 17 backups
   totaling 803 MB." Useful context for the "do I need to prune?"
   decision. Lean yes.

2. **Should auto-backup be opt-out-able?** Some users may have
   their own backup regime and prefer not to have this server
   creating files. Suggested: `GNUCASH_MCP_NOBACKUP=1` env var
   (parallel to `GNUCASH_MCP_NOAUDIT`), or a `--nobackup` CLI flag.
   If implemented, `get_server_config` should report the current
   backup state.

3. **Should manual backups go in a separate subdirectory from auto
   backups?** E.g.,
   `{book}.mcp/backups/auto/` vs. `{book}.mcp/backups/manual/`.
   Arguments for: cleaner mental model. Arguments against: current
   flat layout with stage in the filename is already readable. Lean
   against — flat is simpler.

4. **What if the book is open by GnuCash desktop with a lock?** The
   online backup API should work anyway (SQLite file locks
   coordinate writes, not reads). Verify empirically with a
   locked-book test during implementation.

5. **Compression?** Currently each backup is a raw SQLite copy.
   Could optionally gzip after-the-fact for space. Not urgent;
   defer to a future release if disk usage becomes a complaint.

---

## Effort Estimate

- Core `book/backup.py` + mixin wiring: ~100 LOC
- Tool wrappers in `tools/admin.py` (or `tools/backup.py`): ~50 LOC
- Hook into `audit_log` decorator: ~15 LOC
- State file read/write helpers: ~30 LOC
- Filename parsing and filtering: ~30 LOC
- Tests: ~200 LOC (15 tests as listed above)
- Docs (`RESTORE_FROM_BACKUP.md`): ~40 lines

**Total: ~450 LOC.** 1–2 session hours for implementation + test
writing + live verification on a real book.

---

## Implementation Checklist

- [ ] Add `BackupMixin` to `book/backup.py` and wire it into
      `book/__init__.py`'s `_MIXIN_MAP`.
- [ ] Implement `create_backup()`, `_maybe_auto_backup()`,
      `_list_backup_files()`, `_prune_backups()` on `BackupMixin`.
- [ ] Add `_AUTO_STAGES` constant and state-file schema helpers.
- [ ] Hook `_maybe_auto_backup()` into `audit_log` decorator
      (write path, once per process).
- [ ] Add three tool wrappers (`create_backup`, `list_backups`,
      `prune_backups`) to `tools/admin.py` OR a new
      `tools/backup.py`. Update `TOOL_MODULES` in `server.py`
      accordingly.
- [ ] Add `backup` (or same-as-admin) entry to help text.
- [ ] Write 15 tests per strategy above.
- [ ] Add `docs/RESTORE_FROM_BACKUP.md` with the five-step procedure.
- [ ] Update `README.md` with a one-paragraph "Automatic backups"
      section.
- [ ] Live-test on real book: manual create, auto-trigger, listing,
      prune dry-run, prune actual, integrity check failure
      simulation.
- [ ] Commit with a substantive message; merge via `--no-ff` per
      gitflow.

---

## Future extensions (explicitly out of scope for 1.3.0)

- **Background daemon mode** for periodic snapshots without an MCP
  trigger.
- **Remote destination** (S3, rclone, restic integration).
- **Compression** (gzip / zstd / xz).
- **Incremental / delta backups** (SQLite has `sqlite3_rsync` but
  it requires both sides to be SQLite — not useful for off-site).
- **Restore tool with safety prompts.** Revisit if the filesystem
  procedure proves too friction-heavy for users.
- **Schedule-aware backups** that align with scheduled-transaction
  posting dates for "snapshot before every rent day."
- **Integrity verification of existing backups** as a periodic
  sweep.
