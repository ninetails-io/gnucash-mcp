# Storage / connections / concurrency / data safety: review of d42400e..e99d46e

Repros live in this folder. Run them from the repo root with
`uv run python -m pytest -p tests.conftest <file> -q -W ignore -s`
(the `-p tests.conftest` flag loads the suite's `test_book` / `business_book` fixtures).

## Findings

### S-1: SERIOUS (Windows) | PLAUSIBLE (documented CPython behavior; no Windows machine to run it on)
**Where:** `src/gnucash_mcp/logging_config.py:3249` (`_pid_alive`), called from `_report_interrupted_write` (`:3259`).

**What happens:** On Windows, `os.kill(pid, 0)` does not probe the process. CPython calls `TerminateProcess(handle, 0)` for any signal other than CTRL_C/CTRL_BREAK, so the target is killed. Two things follow:
- An intent file left by another pid causes that process to be terminated. This happens at startup (`setup_logging`) and before every write. The other pid can be a live twin server mid-write (the project notes that Claude Desktop starts twins), or an unrelated process that has reused a crashed server's pid.
- A dead pid raises `OSError` (WinError 87), not `ProcessLookupError`. `_pid_alive` reads that as "alive", so on Windows an interrupted write is never reported and DS-11 does not work at all.

**Sibling, outside this range but the same bug class:** `book/_base.py:2234`, `_lock_holder_note` runs `os.kill(int(pid), 0)` on the pid that GnuCash desktop wrote into `gnclock`. On Windows, any tool call made while desktop has the book open would therefore terminate GnuCash desktop. That one is BLOCKER-class for Windows users. The same function also builds its URI as `f"file:{self.book_path}?mode=ro"` without `quote()`, so a path containing `?`, `#` or `%` silently returns an empty note.

**Repro:** none runnable here. The behavior is in the CPython docs for `os.kill`: "Any other value for sig will cause the process to be unconditionally killed by the TerminateProcess API."

**Fix:** Route both liveness checks through one helper. On POSIX it keeps `os.kill(pid, 0)`. On Windows it uses `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` plus `GetExitCodeProcess == STILL_ACTIVE` via ctypes, or returns "unknown". Add a grep-lock test that no bare `os.kill(..., 0)` exists.

### S-2: SERIOUS | CONFIRMED
**Where:** `book/_piecash_shapes.py:581-585` (`_transaction_validate`).

**What happens:** Template transactions sit on template accounts, whose commodity is GnuCash's `template` pseudo-commodity. The refusal therefore sees commodities `{template}` against a USD currency and refuses. The consequences in a book with "Use Trading Accounts" on:
- Every `create_scheduled_transaction` is refused, even a same-currency rent schedule. The message reads "spans more than one commodity (template)".
- `update_scheduled_transaction` is refused the same way.
- If the book holds any pre-1.5 schedule, `_upgrade_book_shapes` rebuilds its templates on every schedule, budget and business write. Every such write is then refused: `create_budget` fails. The converter can never run, so the book stays blocked.

Template splits carry no amounts (the amounts live in slots), so piecash would add no trading splits anyway. The refusal protects nothing here.

**Repro:** `test_s_trading.py::test_same_currency_schedule_in_trading_book` and `::test_legacy_schedule_blocks_every_converting_write`.

**Fix:** Leave splits on template accounts out of the commodity set, or skip the check when the transaction's splits are on `template`-namespace accounts. Pin it with a trading-book test that creates a schedule and runs a converting write.

### S-3: SERIOUS (data safety; the logic predates this range, the new marker line states the promise) | CONFIRMED
**Where:** `book/backup.py:613-625`, the `pre_upgrade_backup_existing` branch.

**What happens:** When the auto-backup taken moments earlier already holds the pre-conversion state, nothing more is copied, and the marker now records `snapshot: <that file>`. That file is an auto-stage snapshot (usually `session`, keep 7). `_prune_auto_stages` deletes it after seven later session backups, which is a few days of normal use.

After that, the only exact copy of the book from before the irreversible 1.5 conversion is gone, and the marker still names it. The other branch writes a never-pruned `manual` `pre-1-5-upgrade` file. That is the guarantee C30 describes.

**Repro:** `test_s_marker.py::test_existing_copy_is_prunable` fails with `pre-conversion copy test-…-session.gnucash pruned`.

**Fix:** In the existing-copy branch, make the copy manual-stage: `os.link` or copy the auto snapshot to `{stem}-{ts}-manual-pre-1-5-upgrade.gnucash`, which is cheap. Alternatively, have `_prune_auto_stages` skip the file the marker names.

### S-4: MINOR | CONFIRMED
**Where:** `book/backup.py:623` (marker write) together with `book/_base.py:3112-3120` (withdraw).

**What happens:** The marker is written with `snapshot: <name>` inside `_ensure_pre_upgrade_snapshot`. When the converters then find nothing to do (any desktop-made or 1.5-made book), `_upgrade_book_shapes` withdraws that snapshot. The marker keeps naming a file that no longer exists. That is the case the new marker line was meant to make legible to a reader of the backups folder.

**Repro:** `test_s_marker.py::test_marker_names_a_withdrawn_snapshot` fails with `marker names missing file test-…-manual-pre-1-5-upgrade.gnucash`.

**Fix:** Have `_withdraw_pre_upgrade_snapshot` rewrite the marker's second line, for example `snapshot: none (nothing to convert)`.

### S-5: MINOR | CONFIRMED
**Where:** `book/backup.py:714-730` (`skip_unchanged`, which `tools/backup.py` always passes).

**What happens:** `create_backup(label="pre-tax-filing")` on an unchanged book returns `status: unchanged` with the earlier file, which may have no label or a different one. No file carrying the requested label is ever written. The label is the user's handle for finding a restore point ("pre-big-reorg style", per the docstring), and it is silently dropped. The audit note is skipped as well.

**Repro:** `test_s_anchor.py::test_label_dropped_when_unchanged`. The files list holds only `test-…-manual.gnucash`.

**Fix:** Skip only when no label is given or the held file already carries the same label. Otherwise write the labeled copy, or hard-link the held file under the labeled name.

### S-6: MINOR | CONFIRMED (two-process simulation)
**Where:** `logging_config.py:3238` / `:3295` (one fixed `.pending-write.json` per audit directory) and `write_private_file` (`:339`, fixed `.tmp` name with a stale-temp unlink).

**What happens:**
- Two server processes on one book share one intent path. Twins are documented, and the same happens with two containers sharing a volume where both run as PID 1. If process B is mid-write and process A starts a write, A sees B alive, leaves the file, then overwrites it with its own intent. A clears it on completion. If B then dies, B's committed write is never reported. The repro shows `INTERRUPTED lines: 0`.
- With equal pids in containers, A treats B's live intent as its own stale one and logs a false INTERRUPTED line.
- `write_private_file` first unlinks `<name>.tmp` as stale. Two concurrent writers can unlink each other's temp mid-write. One `os.replace` then fails with FileNotFoundError, or installs the other's half-written file. Both callers swallow the error. This also affects the backup state, attempt and manual-anchor files.

**Repro:** `S=<this folder> uv run python repro_intent_twins.py`

**Fix:** Name the intent per process (`.pending-write-{pid}.json`) and have the reporter scan for `.pending-write-*.json`. Have `write_private_file` create its temp with `tempfile.mkstemp(dir=path.parent)`, which is exclusive and has a random name, so no stale-temp unlink is needed.

### S-7: MINOR | CONFIRMED
**Where:** `book/_piecash_shapes.py:575-578`.

**What happens:** `void_transaction` and `unvoid_transaction` on an existing cross-commodity transaction in a trading-accounts book are refused, with "Enter it in GnuCash desktop". Void zeroes every leg and unvoid restores every leg, trading legs included, so each commodity stays balanced and piecash would add no trading splits. The refusal guards nothing. It also contradicts the docstring's "edits that leave the splits alone pass".

Reconcile-state changes, date edits and notes edits pass, as do deletes.

**Repro:** `test_s_trading.py::test_void_cross_commodity_in_trading_book` and `::test_unvoid_cross_commodity_in_trading_book`.

**Fix:** Refuse only when the per-commodity quantity imbalance (outside TRADING accounts) is non-zero, that is, when `normalize_trading_accounts` would actually add or adjust rows.

### S-8: NIT | CONFIRMED (predates this range; not a regression)
**Where:** `book/_base.py` `open()`.

**What happens:**
- When `piecash.open_book` raises after building its engine ("Unsupported table versions", "Lock on the file"), that engine's connection to the book stays open until cyclic GC. NullPool behaves the same way; the baseline is in `test_s_lockleak_nullpool.py`. It matters only on Windows, where an open handle blocks the restore procedure's `mv`.
- If `book.close()` raises, `dispose()` is skipped.

**Repro:** `test_s_lockleak.py::test_unsupported_versions_leaves_no_connection`

**Fix:** Wrap `dispose()` in its own `finally`.

### S-9: NIT | CONFIRMED (predates this range)
**What happens:** A book path containing `?` cannot be opened. piecash's `sqlite:///` URL treats it as a query string and reports "Database … does not exist". The new `_book_tables_error` passes such a file at startup, because it quotes the path correctly, so the failure still lands on every tool call rather than at startup.

**Repro:** a book named `q?x.gnucash` (`test_s_pool`-style one-liner in the session; `sp ace`, `%41` and `#` all open fine).

**Fix:** Open a file book through a `creator=` callable or a quoted URI, or refuse `?` at startup by name.

### S-10: NIT | CONFIRMED
**What happens:** `_book_tables_error` waits sqlite3's default 5 s when another connection holds EXCLUSIVE (measured 5.2 s), then returns None. Under RESERVED it returns at once. This is harmless, but a `timeout=1` like `_lock_holder_note` uses would keep startup snappy.

## Examined and sound

- **StaticPool / `pool_reset_on_return=None`** (`test_s_pool.py`, all pass):
  - No file descriptor survives a write or a read tool call; `dispose()` closes the single connection.
  - An exception after a flush rolls back, and an external `BEGIN EXCLUSIVE` succeeds right away.
  - A read while an external writer holds RESERVED works.
  - A read-only open does not block an external EXCLUSIVE between statements, and a write open does not block one after its commit.
  - An atomic replace of the file after a commit makes a second commit in the same `open()` fail loudly with `READONLY_DBMOVED`, not silently. Reads keep the old inode, as C28 intends.
  - Nothing in `src/` checks out a second connection on the book's engine. The only `session.connection()` users are `_rollback_if_aborted` and `create_backup`'s read-only copy, and both use the session's own connection.
  - The pre-upgrade snapshot uses a separate `sqlite3` connection with `mode=ro` and a `quote()`d path.
  - The GUID-prefix engine is a separate NullPool engine.
  - Sync tools run inline (mcp 1.28.1 `call_fn_with_arg_validation`) and `src/` starts no threads, so `check_same_thread` is safe.
- **`_transaction_validate`:**
  - Deletes, description/notes/date edits, and reconcile-state changes (`set_reconcile_state`, `reconcile_account`) pass.
  - `replace_splits` into cross-commodity is refused.
  - `self.book` resolves through `object_session` at before_commit.
  - The ValueError is raised in `before_commit` after the flush. `open()`'s `close()` rolls back, and later writes on the same instance succeed.
- **`_guard_three_byte_text`** (live on local MariaDB, scratch database `gnucash_review_s2`, dropped afterwards; `repro_mysql_guard.py`):
  - Refuses by table name on transactions, splits (memo), slots (notes) and accounts.
  - Nothing is stored, and a later plain write succeeds.
  - Exactly one listener per engine across repeated opens: the engine is single-use and disposed, so nothing accumulates.
  - The regex covers every raw INSERT/UPDATE form in `book/`: there is no REPLACE, schema-qualified or backticked target, or CTE.
  - executemany parameter lists are walked.
- **Audit decorator:**
  - A rate-limited call returns before the intent.
  - The error path clears the intent after the ERROR line. The success path cannot replace `result`, and `_audit_render_failed` never raises.
  - Params are scrubbed before being truncated to 2000 characters, so the cut cannot split a URI past the scrubber.
  - No tool calls another decorated tool, so there is no nesting.
  - With no audit directory, no intent is written.
  - The stderr handler from `audit_to_stderr` is cleared by the next `setup_logging` (`handlers.clear()`).
- **`_tighten_existing_files`:** 20k files in 0.48 s on the first pass and 0.08 s after. Symlinks are lstat-skipped and their targets are not touched (verified), other owners' files are skipped, and the function is POSIX-gated. A theoretical lstat→chmod TOCTOU remains, which `chmod(follow_symlinks=False)` cannot close on Linux. Acceptable.
- **`_check_mcp_dir_entry` under `GNUCASH_LOG_DIR`:** consolidates the existing check. It is a deliberate behavior change for override users with a symlinked or foreign-owned per-book folder, and it refuses with a clear message.
- **`_book_tables_error`:**
  - Returns None for all 13 sample books.
  - It is correct on paths with spaces, `#`, `%`, `?` and a `%41` directory.
  - A locked or unreadable file falls through to None, so the real open reports it.
  - It mirrors piecash's `version_supported` comparison, including the 2.6 special case.
- **Manual anchor:** the hash is read before the copy. A change in between records the older hash, and the next call copies again, which is the safe direction (`test_s_anchor.py::test_anchor_hash_vs_copy_race` → `created`). The anchor `file` is basename-only, so it cannot be used for path traversal.
