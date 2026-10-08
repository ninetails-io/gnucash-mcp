# Bookkeeper report — MySQL / MariaDB backend (feat/mysql-backend)

Run: 2026-09-10 (PDT), book `mysql+pymysql://gnucash:***@127.0.0.1:3306/gnucash` on MariaDB 12.2 (Alex saved in from GnuCash desktop), second server entry `gnucash-mysql`, branch `feat/mysql-backend` @ `12b9da2`, v1.4.4, 86 tools. File-copy reference: primary server on `samples/alex-chen-morales.gnucash` at HEAD, same checkout. GnuCash 5.12 as second instrument (Steve at the GUI). Bookkeeper: Abe VII (Cowork).

## Verdict: PASS after round 2 (`bf9a7e3`) — merge. Round 1 was BLOCKED on the invoice-link spelling (§3); round 2 closes it and the two smaller items. One new finding (audit file handle) — non-blocking, needs a fix or a plan-step change.

Fingerprint: `Book: mysql+pymysql://gnucash:***@127.0.0.1:3306/gnucash`; `Backend: database — MCP backups unavailable (snapshot with mysqldump (mariadb-dump on MariaDB))`. Note: no "Server (re)started" notice on the first call after either bounce — the restart signal the file server gives is absent on the database backend (single-book config?). Worth making consistent; it is how the bookkeeper knows a bounce took.

## 1. Orientation reads, MariaDB vs file — PASS
`get_book_summary` identical line for line (except `Book:`): assets 861,481.84, liabilities 410,768.91, A/R 31,700 (+6,174.90 EUR, +4,207.29 CAD), A/P 450, net worth 450,713, 14 overdue schedules oldest 57 days, 17 on legacy recipe, runway 1298 days. `list_scheduled_transactions` identical order and dates (17). `get_budget_report` 2025 p0 TOTAL 2,911 / 2,691.03 / 92.4% identical. `list_lots` VTSAX: 32 open on both (the `is_closed=-1` fix live); `get_lot` core position `is_closed: false`, 180 sh / 21,600 basis. Lock refusal confirmed once while desktop held the book ("Lock on the file" — copy says "file" on a database backend; cosmetic).

## 2. Writes land, desktop reads them — PASS
- `create_transactions` 2 rows (Safeway 42.17; Uber Eats 31.80 with notes + memos both legs) → `774008b2`, `9ec44827`.
- `create_scheduled_transaction` "BK MySQL" monthly from 2026-10-01 → `3a29c1b5`, `templates_migrated: 17`.
- `set_account_slot` Expenses:Streaming `notes_probe` → created; read back.
- Invoice 000047 (Emerald, 3,500) created, entry added, posted → txn `b48cf100`, lot `8396d1c4`.
- `set_budget_amount` 2025 Annual Budget, Travel, p0 → 350 (audit `300 → 350`).
- Desktop (Steve): both 09-11 rows in Checking with notes and both memos; SX Editor lists "BK MySQL", opens clean; a migrated one (Mortgage Payment) opens clean; Find Invoice shows 000047; Jump to Invoice from its A/R posting opens it; budget cell Travel/Jan = 350. **Bonus evidence:** Since-Last-Run ran while open and posted all 17 migrated schedules' overdue occurrences (Estimated Tax 07-15, 4 paychecks, 11 Aug-15 bills, 2 Amex) — desktop posting server-migrated templates on MariaDB.

## 3. Conversion sweep on the second dialect — PARTIAL, one blocker
- Schedules: `templates_migrated: 17` on the first schedule write; raw: 0 template accounts off the `template` commodity; 18 schedxactions, 19 recurrence rows (17 + BK MySQL + the budget's). PASS.
- Budget stamp: `features/Use natural signs in budget amounts` present (1), bogus key 0. Desktop had already stamped it during Save As, so no `book_stamped` in the response is correct.
- **Invoice links: FAIL.** No write reported `invoice_links_migrated`. Raw after all writes: `name='invoice'` = 0, `gncInvoice/invoice-guid` = **2** (my invoice's txn + lot), and **`gncInvoice/invoice` = 108**. Desktop's Save As re-serialized the server's bare child `invoice` as the frame-prefixed `gncInvoice/invoice`; the sweep matches only `invoice` and declares nothing to do. Desktop confirms: **Jump to Invoice greyed out on every 2025 Emerald posting.** Not dialect-specific — any book desktop has saved since 1.2 (SQLite included) carries the prefixed spelling. The rename must match both `invoice` and `gncInvoice/invoice`; the plan's "108 rows named `invoice`" was measured on the never-resaved committed file.
- Reporting shape differs from the plan: sweeps fire per domain (schedule write → recipes; business write → links; budget write → stamp) and report per domain, not one combined line on the first write. Fine, but document it.

## 4. Reconcile round trip — PASS
`enter_statement` dry-run classified both step-2 rows MATCH (HIGH, split guids returned) and refused to tie a statement opening at today's ledger balance against the reconciled base 18,777.27 — correct opening-anchor guard. Committed anchored on the base: 2 claimed, reconciled @ 2026-09-11, tied at 18,703.30. `get_reconciliation_status`: Checking "behind, through 2026-09-11". Desktop R column: to confirm on final open (not yet viewed).

## 5. Backups refuse with the right tool; dump/restore — PASS
`create_backup` refused: names `mysqldump (mariadb-dump on MariaDB)` and `docs/RESTORE_FROM_BACKUP.md`, URI password masked. Then `mariadb-dump --single-transaction` (1.5 MB) → `DROP DATABASE` → `CREATE DATABASE … utf8mb4` → restore. Row fingerprint before/after identical: 1,978 txns (incl. 18 templates), 4,252 splits, 3,088 slots, 118 lots, 18 sx, 19 recurrences, 55 invoices, 156 budget rows, 185 reconciled splits. After bounce, `get_book_summary` = step 1 + step-2 writes + SLR postings exactly (1,960 transactions, checking 49,536.69, net worth 453,070, `18 recurring, 12 due in next 7 days`, no legacy line).

## 6. Logs under GNUCASH_LOG_DIR — PASS
`~/Finances/logs/gnucash.gnucash.mcp/audit/2026-09-10.txt` and `debug/2026-09-10.log`; nothing else. Every write above is in the audit log with account paths. Cosmetic: the directory and the audit header render the database as `gnucash.gnucash` (db name + file-style suffix).

## Routed around
- The opening-anchor guard in step 4 (plan said "closing balance from get_balance"; the guard correctly wants the reconciled base). Not a bug; the plan's wording was.
- zsh does not word-split a quoted command variable; my first dump/restore script only dumped. Reran with a function. Nothing server-side.
- Since-Last-Run posted 17 occurrences while desktop was open — scratch book, no harm, and it doubled as evidence.

## Required before merge
1. Link sweep matches `gncInvoice/invoice` as well as `invoice` (and any test fixture should include a desktop-resaved book, not only the committed file).
2. Restart notice on the database backend, so a bounce is observable.
3. Cosmetic: lock-error copy ("file") and `gnucash.gnucash` naming for database books.

Signed: Abe VII, bookkeeper. The second database holds the whole surface — reads, writes, schedules, business, budgets, reconcile, dump/restore, desktop round-trip. The one blocker is a spelling the sweep doesn't know, and it would have bitten SQLite users too the first time they pressed Save in desktop.


---

# Round 2 — 2026-09-10 (PDT), branch @ `bf9a7e3`, same MariaDB book, bounced twice. Steve at the GUI.

1. **Sweep matches desktop's spelling — PASS.** `pay_document` 000047 in full (txn `5d5c1fcc`) → `invoice_links_migrated: 108`; audit line "108 invoice links renamed to GnuCash's key (desktop-navigable, nothing posted)". Raw: `gncInvoice/invoice-guid` = 110; `invoice` = 0; `gncInvoice/invoice` = 0. Desktop: Jump to Invoice on a 2025 Emerald posting — no longer greyed out; it opened the invoice.
2. **Restart notice on a database book — PASS.** First call after bounce: `ℹ GnuCash MCP server (re)started — active book: mysql+pymysql://gnucash:***@127.0.0.1:3306/gnucash.`
3. **Audit header — PASS.** Fresh file after the second bounce reads `Book: mysql+pymysql://gnucash:***@127.0.0.1:3306/gnucash`. Directory stays `gnucash.gnucash.mcp`, as documented.
4. **Desktop R column — PASS.** Both 09-11 checking rows (Safeway, Uber Eats) show `y`.

**New finding (non-blocking): the audit writer holds the day's file open.** I set today's file aside (`mv` → `2026-09-10.round1.txt`) per the plan's step 3, then did one write. The entry landed in the RENAMED file (same inode) and `get_audit_log` — which opens by path — reported "No audit log for 2026-09-10". On a live server, deleting today's audit file (as the plan suggests) would send every later entry into an unlinked inode until the next bounce while the reader insists nothing happened. Fix: writer opens by path per append (or reader follows the handle); plan step should say "bounce" rather than "delete". Everything was recovered here by the bounce.

Signed: Abe VII, bookkeeper. Second database, whole surface, desktop as witness both ways. Merge.
