# Scoped review 3: core, storage safety, logging, backups, server

Range reviewed: `fix/v1.5-adversarial-blockers` 43353a7..d42400e. Every finding below was re-checked against the current tree (HEAD 204f4d1, which is newer than the diff). No files under src/ or tests/ were changed and git state was not touched. Repro scripts are in this folder. The scratch databases `gnucash_review3_core` (PostgreSQL and MariaDB) were dropped afterwards.

Gates run:
- `tests/test_db_backend.py` against real PostgreSQL 16 and MariaDB: 108 passed, 1 skipped.
- Focused suites (backup, contract_integrity, credential_scrub, guid_slot_cascade, logging, multicurrency_audit, posting_record_guard): 3288 passed, 36 skipped.
- `dbsweep.py`: a broad write sweep on PostgreSQL and MariaDB. It covers create, batch, update, batch update, void, unvoid, replace_splits, the statement dry run and commit, reconcile, invoice post and pay, the posting-record refusals, schedule create, instantiate and update, upcoming, budgets, the dashboard, account create, move and delete, delete transaction, and the backup refusal. The book carried a red-line threshold of 30 days. No dialect errors, and no "check failed" line on either dashboard.

## Findings

### CS-1. BLOCKER (narrow trigger), CONFIRMED: a database password containing `'`, `"` or whitespace goes out unmasked
- Where: `src/gnucash_mcp/_format.py:1071` (`_URI_IN_TEXT_RE`), `:1089` (`_LOOSE_USERINFO_RE`), `:1095` (`_mask_uri_secrets`).
- What happens:
  - SQLAlchemy's `make_url` accepts a raw `'`, `"` or space in the password, and `BookSource.from_uri` hands piecash that raw string.
  - On any failed open, piecash raises `GnucashException("Database '<uri_conn>' does not exist …")` with the URI verbatim. This includes a wrong password or a typo in the database name, because `sqlalchemy_utils.database_exists` swallows the connection error and answers False.
  - The scrubber's URI token stops at the first quote or whitespace, so the authority it inspects has no `@` and nothing is masked. `_LOOSE_USERINFO_RE` also refuses to cross a quote or a space.
  - The full password then reaches the tool error (through `redact_paths`), the audit ERROR line, the debug log, and the host's stderr through `CredentialScrubFilter`.
- Repro (end to end, `leak.py`): URI `postgresql://review3_q:pa'ss@localhost:55432/gnucash_review3_typo`. The tool error text reads `Unexpected error: GnucashException: Database 'postgresql://review3_q:pa'ss@localhost:…`, and the audit ERROR line carries the same text. `scrub.py` shows the same leak for `pa"ss` and `p ss`. `_redact_uri` (the header and display name) is correct: it masks all three.
- Suggested fix:
  - Primary: the server knows the secret, so scrub it literally. At `BookSource.from_uri`, register `url.password` (raw and percent-encoded forms) and every secret query value. `_scrub_credentials` then replaces those strings wherever they appear. Shape-only matching cannot bound a password that contains the delimiters it relies on.
  - Secondary: in `_mask_uri_secrets`, mask from the first `:` after `scheme://` through the last `@` that comes before the first `/`, even across quotes and spaces.
  - Lock it by adding `'`, `"` and space passwords to `TestCredentialsNeverLeave`.

### CS-2. SERIOUS, CONFIRMED: the pre-upgrade snapshot can be a hard link to a different, older state of the book
- Where: `src/gnucash_mcp/book/backup.py:623` (hash-matched branch of `_ensure_pre_upgrade_snapshot`) and `:510` (`_book_unchanged_since_last_backup`).
- What happens:
  - "Unchanged" means the current hash equals the last auto-backup's recorded `book_sha256` and *any* backup of this stem exists.
  - The branch then links `list_backups()[0]`, the newest file of any stage, as `…-manual-pre-1-5-upgrade.gnucash` and names it in the marker.
  - If the snapshot that actually holds the recorded state is gone, the link points at an older state, and the irreversible conversion proceeds with no copy of the state it converted. The file can be gone through `prune_backups(stage="monthly", keep_last_n=0, dry_run=False)`, which is allowed, or through a manual delete.
  - The same predicate makes `_maybe_auto_backup` skip forever after that, saying the protection "already exists".
- Repro (`preupg.py`):
  1. Manual backup "mine" of the empty book.
  2. One transaction, then an auto-backup (monthly stage).
  3. Prune the monthly stage to 0.
  4. Call `_ensure_pre_upgrade_snapshot()`.
  - Result: `pre_upgrade_backup` is a hard link to "mine". It holds 0 transactions while the book holds 1, and the marker names it.
- Suggested fix:
  - Record the filename beside `book_sha256` in the state file, as the manual anchor already does. Treat the state as unchanged only when that file still exists.
  - Simpler for the one-time event: always copy the committed state in the pre-upgrade path, and keep the link only as a disk optimisation when the anchored file is verified.

### CS-3. SERIOUS, CONFIRMED: under a shared `GNUCASH_LOG_DIR`, a pre-1.5 folder is claimed by whichever same-named book writes first, and its retention deletes the other book's backups
- Where: `src/gnucash_mcp/logging_config.py:494` (`if owner is None or owner == mine: return plain`), with `claim_log_dir`.
- What happens:
  - A folder written before C31 has no `.owner`. The first book that resolves to it after the upgrade claims it, even when it is the other `ledger.gnucash`.
  - That book then lists the legacy snapshots as its own (stem match), and `_prune_auto_stages` deletes them by its own retention.
  - The real owner now gets `ledger.gnucash-<hash>.mcp` and sees none of its history.
  - This is the exact C31 scenario; the fix holds only for folders created after it.
- Repro (`c31.py`):
  1. Seven session backups of book A sit in an unclaimed `ledger.gnucash.mcp`.
  2. Book B (different directory, same filename) writes once: `.owner` now names B.
  3. B's next session backup plus retention deletes A's `ledger-20260901T120000-session.gnucash`.
  4. A then resolves to `ledger.gnucash-12a1f40f.mcp` and lists 0 backups.
- Suggested fix:
  - Never auto-claim a non-empty unclaimed folder. Claim it only on positive evidence that it is this book's, such as the recorded `book_sha256` matching the current file or the audit header's identity.
  - Otherwise give the new book a hashed folder and leave the legacy one untouched, with a dashboard or startup note.

### CS-4. SERIOUS, CONFIRMED (pre-existing; this path was touched by C7): `delete_account` deletes accounts other objects still refer to
- Where: `src/gnucash_mcp/book/core.py:7157` (`delete_account` checks only children and `account.splits`).
- What happens:
  - An account with no splits but referenced by any of these is deleted:
    - a scheduled transaction's template split (`sched-xaction/account` GUID slot);
    - a draft document entry (`entries.i_acct` / `b_acct`);
    - a tax-table entry (`taxtable_entries.account`).
  - Each reference is left dangling.
  - Desktop refuses this. The delete-account command lists `qof_instance_get_referring_object_list` ("The list below shows objects which make use of the account …") and will not delete until they are changed.
- Repro:
  - `delacct.py`: a schedule uses `Dining`; `delete_account("Dining")` succeeds. The template slot still points at the deleted GUID, the dashboard still lists the schedule as overdue, and `create_transaction_from_scheduled` fails with `Account not found: <guid>`.
  - `delacct2.py`: `entries.i_acct` dangling 1, `taxtable_entries` dangling 1.
- Suggested fix:
  - Refuse when any referrer exists, naming them: template-split GUID slots with `guid_val = account.guid`, entries, tax-table entries, documents' `post_acc`, owners' default accounts, and bill terms if relevant.
  - Lock it with a test per referrer kind. This is the desktop-parity "server never writes what desktop wouldn't" invariant.

### CS-5. MINOR, CONFIRMED: path redaction passes `sqlite:///` URIs through verbatim, so the whole book path leaks with `GNUCASH_REDACT_PATHS=1`
- Where: `src/gnucash_mcp/logging_config.py:285-297` (the hold step).
- What happens:
  - Connection strings are "held" so the path patterns don't mangle them, but a `sqlite:////Users/…/ledger.gnucash` URI is a path, and it is restored untouched.
  - piecash quotes exactly this URI when a file book has gone missing.
  - With spaces, the held token ends at the first space and the private-prefix pass cannot match the split path either.
  - This is a regression introduced by the hold step (before it, `posix_re` reached into the URI).
- Repro:
  - Book at `…/Client Books/Acme Ltd.gnucash`, redaction on, file renamed away, `list_accounts()`. The error reads `Database 'sqlite:////private/…/Client Books/Acme Ltd.gnucash' does not exist`.
  - The same happens with `_book_path_str` set, and for `sqlite:///file:/…%20…?mode=ro`.
- Suggested fix: hold only non-file schemes. For a `sqlite` URI, redact the path part to its basename (`sqlite:///ledger.gnucash`).

### CS-6. MINOR, CONFIRMED (simulated trigger): the C27 rollback deletes the instance even when the schedule advance was committed
- Where: `src/gnucash_mcp/book/scheduling.py:1669-1680`.
- What happens:
  - Any exception out of the phase-3 `with self.open(...)` triggers `delete_transaction(created_guid)` plus "nothing changed. Retry."
  - Some exceptions arrive after `book.save()` has committed `last_occur`, `instance_count` and `rem_occur`: the attribute refresh on `instance_count` after commit, or `close()`/`dispose()` in `open()`'s `finally`.
  - The occurrence is then consumed with no transaction. A retry posts the next period, and this one is silently skipped.
- Repro (`c27.py`, patching `Book.close` to raise after a successful phase-3 commit): the response says "removed again, so nothing changed". The schedule now reads `overdue:2026-09-01`, the August occurrence is consumed, and there are 0 transactions.
- Suggested fix:
  - Capture `instance_count` and `remaining` before `save()`.
  - In the handler, re-read the schedule in a fresh session. Delete the instance only if `last_occur` did not move. Otherwise stamp it and report success with a warning.

### CS-7. MINOR, CONFIRMED: `_lock_holder_note` builds an unescaped SQLite URI and creates stray files
- Where: `src/gnucash_mcp/book/_base.py:2287` (`f"file:{self.book_path}?mode=ro"`; every other read-only open uses `quote()`).
- What happens:
  - For a book path containing `#` or `?`, SQLite truncates the name at the fragment or query and opens the truncated path read-write.
  - That creates an empty file next to the book (`a#2.gnucash` creates `a`; `b?x.gnucash` creates `b`). A `%xx` sequence in the name opens a different file.
  - The note silently comes back empty.
  - This happens only on the lock-error path.
- Repro: `lk/` in this folder, which shows the stray `a` and `b` files.
- Suggested fix: `quote(str(self.book_path))`, as `_committed_state` and `_book_tables_error` do.

### CS-8. MINOR, CONFIRMED: a void reason bypasses `_check_text`
- Where: `src/gnucash_mcp/book/reconciliation.py:588` (byte cap only).
- What happens:
  - On PostgreSQL, a NUL in the reason reaches the driver ("A string literal cannot contain NUL (0x00) characters") rather than being refused by name.
  - On SQLite the reason is stored and shows cut off at the NUL.
  - Other C0 controls, such as ESC/ANSI, are stored and echoed back (`'void_reason': 'bad\x1breason'`).
- Repro: `pgedge.py`.
- Suggested fix: `_check_text(reason, _SLOT_TEXT_WIDTH, "void reason")` beside the byte cap, plus the control-character refusal the other text gates use.

### CS-9. MINOR, CONFIRMED: under `GNUCASH_LOG_DIR`, the same book reached by a case-variant or moved path loses its folder
- Where: `src/gnucash_mcp/logging_config.py:328` (`_log_dir_identity` uses `Path.resolve()`, which does not canonicalise case on APFS).
- What happens:
  - `Ledger.gnucash` and `ledger.gnucash` are one file, but the second resolves to `ledger.gnucash-0cf090e5.mcp`.
  - That means a fresh backup chain, no pre-upgrade marker, and a split audit trail.
  - `list_backups` deliberately matches stems case-insensitively for exactly this macOS case, so the two rules disagree.
  - A book moved to another folder also loses its history.
- Repro: an inline `resolve_mcp_dir` call (see transcript); it returns two different folders for one file.
- Suggested fix: identify by `(st_dev, st_ino)`, or by case-folded `resolve()` on case-insensitive volumes, with the path kept as the display.

### CS-10. NIT: state files under the log folder are written inconsistently
- The pre-upgrade marker (`backup.py`, in both `_ensure_pre_upgrade_snapshot` and `_withdraw_pre_upgrade_snapshot`) and `.owner` (`claim_log_dir`) are written with `Path.write_text`. That follows a planted link and is not 0600, unlike `write_private_file`, which C53 made the rule for small state files.
- `marker.exists()` also follows a link, so a dangling or planted marker suppresses the snapshot.
- The folder itself is owner-checked, so this is consistency, not an exploit.

## Examined and sound

- **Posting-record guard (`_refuse_posting_record` / `_require_editable`):**
  - Delete, void, `replace_splits`, `update_transaction`, the broadcast form and `update_transactions` all refuse, on PostgreSQL and MariaDB too.
  - Statement claims that would annotate (raw, notes, num, link) refuse; reconcile-only claims are allowed.
  - `assign_split_to_lot` cannot move the posting split, which already sits in the document lot.
  - Unvoid restores `trans-read-only` on a posting record.
- **Read-only period (`_read_only_before` / `_read_only_period_note`):**
  - The double-only read matches `qof_book_get_num_days_autoreadonly`.
  - The note fires on every listed path on all three dialects, including the schedule instance.
  - Batch paths read the threshold once.
  - It never raises.
- **`_strip_guid_slots`:**
  - Frame walk by `slot_type = 9` / `guid_val`.
  - Deletes by `obj_guid` only, so it does not reach the target account's own frames.
  - `objects` are expired before the ORM delete.
- **Raw SQL in the three-dialect intersection:**
  - New `text(...)` statements were reviewed.
  - The only SQLite-specific one (`NOT LIKE '____-%'`) is gated on `_dialect_name`.
  - `_read_only_before` rolls back an aborted transaction.
  - The sweep on PostgreSQL and MariaDB was clean.
- **`_is_lock_error` phrase list:** "no such table: gnclock" and paths containing "lock" no longer match. `_book_tables_error` makes exactly piecash's `version_supported` comparison.
- **`_to_decimal`:**
  - The plain-number gate holds.
  - The 10^17 and 18-place caps hold.
  - Values near int64 at a 1/100 denominator are refused by piecash's ValueError and never reach save.
- **Shape checks in `_validate_transaction_splits`:** C12's zero-leg refusal on currency accounts, C21's refusal of a quantity that rounds to nothing (and the warning when it merely rounds), and the IV-24 quantity check.
- **`_upgrade_book_shapes` withdraw logic:**
  - The current tree excludes `pre_upgrade_backup` and a first-budget stamp from "converted".
  - The withdraw unlinks only the link.
  - The marker is rewritten to say no snapshot was needed.
- **Pre-upgrade snapshot plumbing:**
  - The read-only `_committed_state` copy uses a percent-quoted URI.
  - A copy failure refuses the write.
  - A database book returns `{}`.
- **C29 `_maybe_auto_backup`:** the recheck and retry windows behave as documented, and the lock is shared across instances.
- **`_scrub_credentials` on ordinary shapes:** last-`@` split, `?password=`, `sslpassword=`, `passwd=`, `@` in the database name, `mysql+pymysql://user@host`, IPv6 hosts, percent-encoded passwords, and `/`, `<`, `)` in passwords. `_redact_uri` is correct for every shape tried, including the CS-1 ones.
- **Tool-error and log plumbing:** `safe_tool` embeds tracebacks in the message, so `CredentialScrubFilter` sees them. `_DailyFileHandler.emit` scrubs. `audit_to_stderr` has the filter.
- **Audit escaping:**
  - `_escape_audit_strings` runs at the single dispatch chokepoint, and blank handler lines are rendered as a visible `\n`.
  - The ERROR and debug lines are escaped (C57/SEC-18).
  - TSV keys keep only their structural separators, and every handler re-parses them rather than echoing them.
- **`redact_paths`:** spaced paths ending in a known extension, quoted spaced paths, UNC, Windows drive letters, and `file://` all redact correctly; the CS-5 exception aside.
- **`get_server_config`:** basenames and the masked URI only.
- **`switch_book`:** the DS-10 stderr fallback; URI mode stays single-book.
- **Scheduling:**
  - `_recurrence_next` matches Recurrence.cpp, including the adjusted-start early return, verified against the GnuCash source. Spot sequences are right for the 31st monthly, the Feb-29 yearly, bimonthly from the 30th, end of month, and both weekend adjusts.
  - `_rational_amount` handles decimal and non-decimal denominators.
  - C18 takes the instance rate from quotes only, with the staleness cap and guard note.
- **Dashboard:** collectors route through `_check_failed`, and no swallowing collector was found. The upcoming-schedule read is now guarded.
