# Bookkeeper live loop — MySQL / MariaDB backend (feat/mysql-backend)

Six checks, one bounce, GnuCash desktop as the second instrument for
two of them. Branch under test: `feat/mysql-backend`. What it claims:
a book GnuCash keeps in MySQL or MariaDB is served the same way a
PostgreSQL one is — every tool, unchanged — with the `mysql` extra
(PyMySQL) as the driver. The test suite now runs its real-database
class once per dialect, and CI gains a MariaDB job. The backup
refusal and `get_server_config` name the dialect's own dump tool.
This is the 1.5.0 headline, so the question is breadth: does the
whole surface hold on the second database, not just the eight
things the unit gate checks.

Setup (done on the maintainer's machine): MariaDB 12.2 via Homebrew,
database `gnucash`, user `gnucash`/`gnucash`, Alex saved into it
from GnuCash desktop (File → Save As → mysql). Server entry
`gnucash-mysql` in the Desktop config with
`GNUCASH_BOOK_URI=mysql+pymysql://gnucash:gnucash@127.0.0.1:3306/gnucash`
and `GNUCASH_LOG_DIR` set. Bounced. Nothing here touches production
or the committed sample files.

Fingerprint: `get_server_config` reads `Book: mysql+pymysql://
gnucash:***@127.0.0.1:3306/gnucash` and `Backend: database — MCP
backups unavailable (snapshot with mysqldump (mariadb-dump on
MariaDB))`. Old code says `pg_dump` on that line.

1. **Orientation reads on the desktop-written book.** (Already run
   once by the maintainer's Claude Code session — see the findings
   below — rerun it on the branch head to confirm.) GnuCash
   desktop closed (it holds `gnclock`; the server refuses while it
   does — confirm that refusal once, then close desktop).
   `get_book_summary`, `list_accounts`, `balance_sheet`,
   `net_worth`, `spending_by_category` for 2026, `list_documents`,
   `get_outstanding_documents`, `list_scheduled_transactions`,
   `get_budget_report`. Compare every figure against the same calls
   on the file copy (`samples/alex-chen-morales.gnucash` at HEAD,
   which is what was saved into MariaDB). Expected: identical to
   the penny, including A/R, A/P, the 17 schedules, the budget.
   This is the report-number check; the gate tests only prove a
   two-account book.
2. **Writes land and desktop reads them.** `create_transactions`
   with two rows (one plain expense, one with a memo and notes);
   `create_scheduled_transaction` "BK MySQL" monthly from
   2026-10-01; `set_account_slot` on `Expenses:Streaming`;
   `create_document` + `post_document` one customer invoice;
   `set_budget_amount` on one line. Each response normal, each
   `get_*` reads back. Then open GnuCash desktop on the MariaDB
   book: the two transactions in their registers with memo and
   notes; "BK MySQL" in the SX editor, opens without crash; the
   invoice under Business → Customer → Find Invoice, Jump to
   Invoice works from the A/R register; the budget cell. Close
   desktop.
3. **The conversion sweep runs on the second dialect.** The
   committed Alex still carries the 1.4 shapes (17 legacy schedule
   recipes, 108 old-key invoice links, no budget stamp), and Save As
   copied them into MariaDB as-is. The FIRST schedule/business/budget
   write in step 2 therefore carries `templates_migrated: 17`,
   `invoice_links_migrated: 108`, `book_stamped: true`, with one
   audit line naming all three; the next write carries none. This
   is the raw-SQL slot INSERT/UPDATE/DELETE paths running on MySQL
   for the first time — verify with `SELECT COUNT(*) FROM slots
   WHERE name='invoice'` = 0 and `= 'gncInvoice/invoice-guid'` =
   108 afterward, and that desktop's SX editor opens a migrated
   schedule (step 2 covers the visit).
4. **Reconcile round trip.** `enter_statement` dry-run then commit
   on `Assets:Current Assets:Checking Account` with a two-line
   statement (the step-2 rows, closing balance from `get_balance`).
   `get_reconciliation_status` shows them reconciled. Desktop:
   the register's R column shows `y`.
5. **Backups refuse with the right tool.** `create_backup`: refused,
   message names `mysqldump` and `docs/RESTORE_FROM_BACKUP.md`, no
   password in it. Then the real thing, at a shell:
   `mysqldump --single-transaction gnucash > /tmp/alex.sql`, drop
   and recreate the database, restore, bounce, `get_book_summary`
   identical to step 1 plus the step-2 writes. This is the
   procedure the README now tells a MySQL user to schedule.
6. **Audit and debug logs live under `GNUCASH_LOG_DIR`.**
   `get_audit_log` for today lists every write above, rendered with
   account paths. The directory holds `gnucash.gnucash.mcp/audit/`
   and `debug/`, nothing beside the database.

Cleanup: none required; the MariaDB `gnucash` database is the
maintainer's scratch. Drop it or keep it.

**Not live-testable here:** the CI job itself (Docker is not
running on this machine; it proves on the PR). The
`mariadb+pymysql://` scheme naming `mariadb-dump` is parse-only,
unit-locked.

**Report:** pass/fail per step; for step 1 any figure that differs
between the file copy and the database copy, exactly; for step 2
what desktop showed; the standing routed-around question — this is
the first time the business, budget, and schedule surfaces run on a
non-SQLite backend, and a workaround here is a dialect bug.

---

## Step 1 as run by Claude Code, 2026-09-10 (pre-loop)

Every read surface was rendered on both copies and diffed (script:
compare file vs `mysql+pymysql://…/gnucash`, 24 calls: summary,
accounts, balance sheet, net worth, spending, income, cash flow,
documents, outstanding, parties, jobs, terms, tax tables, budgets,
schedules, upcoming, transactions compact and verbose, search, one
transaction, lots, reconciliation status). Every figure agreed from
the first run. Two things did not, both fixed on the branch before
the loop:

1. **Order leaked from the storage engine.** SQLite returns rows in
   insertion order, InnoDB by primary key, and several renderers never
   sorted: a transaction's splits (credit-first on MySQL, debit-first
   on the file), same-day transactions, the schedule list, the
   upcoming list, the dashboard's "+N more" previews for overdue and
   stale prices, lots. Now: splits follow GnuCash's own
   `xaccTransSortSplits` (debits first) then account path; same-day
   rows by entry time; schedules by next due; the rest by name or
   acquisition date. After the fix all 24 calls render identically
   except the `Book:` line.
2. **The lot flag.** Desktop's Save As left 63 of 117 lots at
   `is_closed = -1`, which is GnuCash's `LOT_CLOSED_UNKNOWN`
   ("compute from the balance"), and the server read it as closed —
   every open VTSAX lot rendered `CLOSED` and dropped out of
   `list_lots`. Worse for the business walk: piecash's own guard reads
   the raw column, so `pay_document` on an invoice desktop had
   partially paid (§3c of the parity walk) would have raised "Lot is
   closed". Fixed at one reader (`_lot_is_closed`, ported from
   `gnc_lot_is_closed`); writers cache 0/1 as GnuCash does; the flag
   is cached before every split-to-lot assignment. `close_lot` now
   refuses a lot with a balance.

Step 2's expectations stand. Add to step 2: `list_lots` on
`Assets:Investments:Brokerage:VTSAX` shows the open positions (not an
empty list), and `get_lot` on one of them reads `is_closed: false`.

---

## Round 1 report — 2026-09-10, Abe VII (Steve at the GUI)

Filed at `BOOKKEEPER_REPORT_MYSQL_BACKEND.md`. PASS on all six
dialect checks — reads identical to the file, writes read back in
desktop (Since-Last-Run posted 17 server-migrated schedules on
MariaDB), reconcile tied, `mariadb-dump` → drop → restore round-
tripped 1,978 transactions row for row, logs under `GNUCASH_LOG_DIR`.
BLOCKED on one finding MySQL exposed but that is not dialect-
specific: desktop's Save As re-serialized every pre-1.5 link child
`invoice` as `gncInvoice/invoice`, the sweep matched only the bare
spelling, and Jump to Invoice stayed greyed out on all 108 old
postings. Two smaller items: no "(re)started" notice on a database
book, and the audit header naming the book `gnucash.gnucash`.

## Round 2 — what changed

- **Sweep matches both spellings.** `_LEGACY_INVOICE_LINKS =
  ("invoice", "gncInvoice/invoice")`; the frame query and the rename
  take either. Test parametrized over both, the second engineered
  the way desktop writes it. Any SQLite book that has been through
  desktop since 1.2 carries the prefixed form, so this would have
  bitten file users on their first desktop save after upgrading.
- **Restart notice on a database book.** `_consume_startup_notice`
  had nothing to name when the path list was empty; it now names
  the masked URI. Locked.
- **Audit header names the masked URI**, not the storage key.
  The `.mcp` directory keeps its `{database}.gnucash.mcp` name
  (that is the per-book layout `GNUCASH_LOG_DIR` documents); only
  the header line a human reads changed.
- **Lock copy** — "Details: Lock on the file" is piecash's own
  exception text; the wrapper's sentence is backend-neutral. Left
  as is.
- Sweeps reporting per domain (schedule write → recipes, business
  write → links, budget write → stamp): that is the shape — each
  write runs all converters, but a converter with nothing to do
  says nothing. The plan's "one combined line" was wrong.

Re-run, on the same MariaDB book (no reset needed):

1. One business write (`pay_document` on 000047 in full, or unpost
   and re-post it): response carries `invoice_links_migrated: 108`,
   the audit line names it. Raw: `gncInvoice/invoice` = 0,
   `gncInvoice/invoice-guid` = 110. Desktop: Jump to Invoice from a
   2025 Emerald posting opens the invoice.
2. Bounce; first call carries `ℹ GnuCash MCP server (re)started —
   active book: mysql+pymysql://gnucash:***@127.0.0.1:3306/gnucash.`
3. Delete today's audit file under the log dir (or wait for
   tomorrow's): the new header reads the masked URI.

---

## Round 2 report — 2026-09-10, Abe VII (Steve at the GUI)

PASS 4/4 on bf9a7e3: `pay_document` renamed the 108 desktop-spelled
links and Jump to Invoice opened a 2025 posting; the restart notice
names the masked URI; the fresh audit header names it too; the
reconciled rows read `y` in desktop. Merge word given. One new
non-blocking finding: the audit writer held the day's file open, so
a file set aside by `mv` kept receiving entries (same inode) while
`get_audit_log`, which opens by path, reported "No audit log".

## Round 3 — what changed (rides the branch, no re-run needed)

The audit and debug writers open the day's file by path on every
entry (`_DailyFileHandler`). A renamed or deleted file comes back at
the path with its header and 0600 permissions on the next write,
and a server running past midnight moves to the next day's file
instead of appending to yesterday's — the second failure was live
on every Claude Desktop session left open overnight, and the reader
would have said "No audit log" for the new day. Locked by three
tests (rename, midnight, delete). Step 3's "delete today's audit
file" is now a safe instruction rather than a trap; "bounce" works
too.
