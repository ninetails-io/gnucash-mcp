# Restoring a GnuCash Book From a Backup

This is a **filesystem procedure**, not an MCP tool. Restore is
deliberately kept off the tool surface — clobbering the live book is
exactly the kind of destructive action that must be a deliberate
human decision, never an LLM autocomplete accident. If the server is
broken enough that you need a restore, we also can't trust it to do
one safely.

Follow these steps with a text editor or terminal, not via Claude.

---

## Prerequisites

You'll need:
- Your backup file — typically at
  `{book_path}.mcp/backups/{book}-{timestamp}-{stage}.gnucash`.
  Look at that directory directly via Finder / `ls` — filenames
  carry a UTC timestamp and the retention stage. (There is
  deliberately no MCP tool that lists or deletes backups: the
  store is append-only from the model's side, so the safety net
  can't be enumerated or shrunk by a confused or manipulated
  session.)
- Your live book path — typically from the `GNUCASH_BOOK_PATH`
  environment variable in your MCP client config. Example:
  `/Users/stephen/Finances/books.gnucash`.

If your book lives in a database rather than a file
(`GNUCASH_BOOK_URI`), this procedure does not apply — skip to
[If your book lives in PostgreSQL or MySQL](#if-your-book-lives-in-postgresql-or-mysql).

---

## Step 1: Stop the MCP server

Disconnect from Claude Desktop, Claude Code, or whichever MCP client
is running the server. Specifically:

- **Claude Desktop** — quit the app entirely (⌘Q on macOS, not just
  close the window).
- **Claude Code** — close the CLI session; make sure no background
  `uv run python -m gnucash_mcp` process is still alive.

Verify nothing is holding the book open:

```bash
# macOS / Linux
ps aux | grep gnucash_mcp | grep -v grep
```

If anything comes back, kill it:

```bash
kill <PID>
```

Also close **GnuCash desktop** if it's open on the same book — it
holds a lock that would interfere with the copy.

---

## Step 2: Set aside the current book

**Do not delete** the current book even if you think it's corrupted.
Move it to a recovery name so you can refer back to it if the
restore goes wrong:

```bash
mv /path/to/books.gnucash /path/to/books.gnucash.broken
```

Also move the matching `.LCK` or `.log` file if GnuCash left one
behind:

```bash
rm -f /path/to/books.gnucash.LCK /path/to/books.gnucash.log
```

---

## Step 3: Copy the backup into place

Pick the backup you want to restore (by timestamp) and copy it to
the live book path:

```bash
cp /path/to/books.gnucash.mcp/backups/books-2026-04-19T192345-session.gnucash \
   /path/to/books.gnucash
```

**Do not move (`mv`)** — copying leaves the backup in place so you
can try a different one if this restore doesn't give you the state
you expected.

---

## Step 4: Restart the MCP server

Bring the MCP client back up (reopen Claude Desktop / Claude Code).
The server loads the newly-restored book on next connection.

---

## Step 5: Verify

From Claude, call:

- `get_book_summary` — balances, account count, transaction count
  should match what you expected from this backup's timestamp.
- If a specific transaction or account is what you came here to
  recover, use `search_transactions` or `get_account` to confirm
  it's there.

If the restored state looks right:
- Delete the `.broken` file you set aside in Step 2 when you're
  confident you won't need it.
- Keep the backup in place; the next auto-snapshot (first write of
  the day) will add a new session backup alongside it.

If something's still off:
- Go back to Step 2, mv the current (now-restored) file aside
  again, and try a different backup. Multiple restores are cheap
  because backups are files, not stateful operations.

---

## If your book lives in PostgreSQL or MySQL

Everything above assumes a book in a SQLite file, which is what the
MCP backup store snapshots. A database-backed book
(`GNUCASH_BOOK_URI`) has no such store — `create_backup` refuses,
and the automatic session/weekly/monthly snapshots never run.

**That means backups are yours to arrange before you put a real
book in a database.** The database's own tooling does the job:

```bash
# Back up (schedule this — cron, systemd timer, your host's snapshots)
pg_dump --format=custom gnucash > gnucash-$(date -u +%Y%m%dT%H%M%SZ).dump

# Restore, with the MCP server and GnuCash both stopped (Step 1 above)
dropdb gnucash && createdb gnucash
pg_restore --dbname=gnucash gnucash-20260907T101500Z.dump
```

MySQL / MariaDB, the same shape (`mariadb-dump` is MariaDB's name for
the same tool; `mysqldump` still works there):

```bash
# Back up
mysqldump --single-transaction gnucash > gnucash-$(date -u +%Y%m%dT%H%M%SZ).sql

# Restore, both programs stopped
mysql -e "DROP DATABASE gnucash; CREATE DATABASE gnucash CHARACTER SET utf8mb4"
mysql gnucash < gnucash-20260907T101500Z.sql
```

Steps 1, 4 and 5 of the file procedure still apply as written: stop
the server first, restart it after, and verify with
`get_book_summary` that the transaction count and balances match
what you expected to restore.

If the book is stuck because GnuCash desktop still holds it, that is
the `gnclock` table, not a corrupt restore — close GnuCash and
retry. The server reports it as a lock error with that advice.

---

## Safety notes

- **Auto-backups won't restore themselves.** The backup tool
  creates snapshots; it never reads them back into the live book.
  That's by design.
- **Your audit log is NOT restored alongside the book.** Audit logs
  live in `{book_path}.mcp/audit/` and aren't included in backups.
  After a restore, the audit trail shows all mutations from before
  the restore point; transactions that existed at the restored
  moment but not in the current book are simply gone from the
  live view.
- **Restoring across schema versions** — if you've upgraded GnuCash
  since the backup was taken, the restored file may want to run a
  migration on next open in GnuCash desktop. That's expected
  behavior, not a failure.
- **Your tests do not run against backups.** `test_book` and other
  fixtures use `tmp_path` — the restore procedure is tested in
  production, by you, when you need it.

---

## If you get stuck

Open a new Claude session and describe what happened — with the
server stopped, an MCP-less Claude can still advise on filesystem
steps, interpret piecash errors, or help you verify the backup
file's integrity (e.g., by asking you to run `sqlite3
path/to/backup.gnucash "PRAGMA integrity_check"`). Keep the
`.broken` file around until you're satisfied with the restore —
it's your insurance against a bad recovery.
