# Changelog

Notable changes in each release. Pull request numbers are given where they exist.

## v1.5.0 - Every database GnuCash speaks

Books can live in PostgreSQL or MySQL/MariaDB as well as a SQLite file, and everything the server stores is now written the way GnuCash desktop writes it, so the two can share a book.

### Upgrading from 1.2–1.4.4
- The first write that touches schedules, budgets, business documents, prices, voids, or reconciliation converts the whole book to GnuCash's own storage. It posts nothing, reports what it converted, and cannot be undone.
- Before converting, the server copies the book to its backups folder (labelled `pre-1-5-upgrade`, never pruned) and refuses the write if the copy cannot be made. For a database book, run `pg_dump` or `mysqldump` first.
- Make that first write before opening the book in GnuCash desktop again. Until it is converted, a schedule created by an earlier version crashes GnuCash 5.12's Scheduled Transaction Editor.
- Don't point a 1.4.x server and a 1.5 server at the same book. A 1.4.x write to a converted book is detected and reported.
- Step by step, with how to check and how to go back: [docs/UPGRADING.md](docs/UPGRADING.md).

### Added
- **PostgreSQL and MySQL/MariaDB books** through `GNUCASH_BOOK_URI` (#175, #181). Install the `postgres` or `mysql` extra. CI tests both against real database servers. A database book is single-book, needs `GNUCASH_LOG_DIR`, and is backed up with the database's own dump tool, not by the server.
- **Num and document link** on every transaction tool. A matching Num is used as a duplicate signal.
- **Prepayments.** `pay_document` can record an overpayment as the party's unapplied payment, settle a document from earlier unapplied payments, and take the exact bank amount of a cross-currency payment. Unposting a paid document keeps its payments.
- `update_account(hidden=…)` and `update_scheduled_transaction(start_date=…)`.
- Dashboard warnings for overdrawn cash accounts, scheduled bills that exceed the week's cash, and unbalanced transactions. A dashboard check that fails says so instead of being skipped.
- An `INTERRUPTED` audit entry for a write the server was stopped before logging.
- **Demo books built from source.** The three sample books are no longer committed; `scripts/synthetic_book/` builds them deterministically, in the storage shapes GnuCash desktop reads, for the bundle, the Docker image, and any clone. Each persona was revised against a tax review for its country.

### Changed
- **GnuCash desktop compatibility.** Schedules, budgets, invoice links, credit notes, billing terms, due dates, prices, voids, lots, and reconciliation state are stored as GnuCash desktop stores them, verified against GnuCash 5.12 (#176–#181). Schedules created here run in desktop's Since Last Run, and desktop can unvoid a transaction the server voided.
- **Invoice totals match GnuCash** to the cent, including its tax rounding and line discounts entered in desktop. A posted document's total is read from its posting, so later tax-table edits don't change it.
- **Cross-currency payments** are booked in the paying account's currency, as GnuCash books them.
- **Prices.** One price per currency pair per day. Same-day ties, and the rates implied by cross-currency transactions, are resolved the way GnuCash resolves them. Read tools issue a fixed number of queries whatever the book's size (#182, #186).
- Report totals equal the sum of their lines on multi-currency books, and amounts print with each commodity's own decimal places.
- Dashboard: the account counts are one chart-of-accounts map, two levels deep, with a `list_accounts(root=…)` call to drill into any branch. Every count ties to `list_accounts`; hidden accounts are shown as `+N hidden`. Account parameters say they take a `%short` guid as well as a name.
- Dashboard: overdue invoices use GnuCash's billing-term due dates, reconciliation lag follows desktop's statement cycle, budget pace follows the budget's own periods, and stale-price warnings name the rate actually used.
- A write dated inside the book's read-only period carries a warning.
- In a book that uses trading accounts, transactions across currencies or commodities are refused (see Known limitations).
- `--modules=business` is one module; `freelancer` and `business_complete` are accepted as retired aliases (#173). New response fields are additive.
- Logs and backups are created readable by their owner only. The Docker image runs as a non-root user.

### Fixed
- An invoice's posting transaction can no longer be voided, rewritten, or re-dated; unpost the invoice instead.
- Deleting a transaction, account, or document no longer removes metadata belonging to other records.
- A database password is masked in every error message and log.
- The audit log names the account for every `acct` cell of `create_transactions` and `enter_statement`, whether the call used a name or a `%short` guid.
- Invoice lookups and deleting a taxed draft on PostgreSQL (#189).
- A sold-out holding with no price is valued at zero (#184, #185).
- Report date boundaries, automatic backups in long-running sessions, and two books with the same filename under one `GNUCASH_LOG_DIR`.
- Input validation: control characters, over-length text, multi-line names, look-alike account names, out-of-range dates, and zero or negative prices are refused.
- Intel Macs install without a Rust toolchain (#183).
- An amount or price too large for GnuCash to store is refused on its own row, in the dry run as well as the commit, instead of failing the whole batch at commit.
- A date more than a year ahead, or before 1900, draws a "check the year" warning on every dated write (updates, posting, payments, scheduled transactions), not only on batch entry.
- A report whose start date is after its end date is refused instead of answering an empty total, and an amount written with a thousands separator ("2,850.00") is refused with the field it came from and how to write it.
- A database book whose database doesn't exist is reported as a missing book, with how to create it, not as an unexpected error.

### Known limitations
- The server does not lock the book. Don't edit in GnuCash desktop and through the server at the same time.
- The book's read-only period produces a warning, not a refusal.
- In a book with "Use Trading Accounts" on, enter cross-currency and stock or fund transactions in GnuCash desktop.
- GnuCash's Balance Sheet does not balance on a multi-currency book without trading accounts; cross-currency payments made by 1.2–1.4.4 add to the gap. See `samples/README.md`.
- Foreign-currency spending and income are valued at each month's closing rate, not the cash paid; `cash_flow` reports the cash.
- A book whose path contains `?` cannot be opened.
- Fixed strings that GnuCash writes in the user's language ("Lot Link", "Voided transaction") are written in English.

### Credits
- @vchatela — PostgreSQL support (#175), the largest outside code contribution to date.
- @DrSkippy — the closed-position valuation fix (#184).
- @bhbrunt — price-lookup performance (#182).
- @JamesRao98 — the PostgreSQL invoice-lookup fix (#189).
- @wernerwws — the Num and document-link gap.

## v1.4.4 - The statement is the call

A complete bank statement enters, claims its matches, and reconciles in one atomic call; every consequential write now rehearses before it books; a one-click Claude Desktop bundle ships from the project's first CI. (v1.4.3 was never released on GitHub — that number belongs to a registry-side rebuild.)

### Added
- **`enter_statement` (#154)** — opening balance, closing balance, every line between, one atomic call. Statement self-check (opening + Σlines = closing) before anything else. Dry-run is the default: every line classified NEW / MATCH / OVERLAP / AMBIGUOUS with self-contained comparisons (both sides, deltas, category legs). Commit is one open, one save, or nothing; the closing tie is verified post-write from saved splits. Amounts transcribe statement-native; the server applies sign conventions per account class.
- `force` on `enter_statement` split into `force_base` / `force_duplicates` — clearing an opening gap can never silently disable duplicate detection.
- `pay_document` `dry_run` — proposed splits, projected remaining balance, FX/discount treatment, and any account the real call would auto-create; one shared computation with the booking path.
- `create_transactions` dry-run gains the statement surface: summary header, `review_required` status, self-contained duplicate comparisons with deltas and split-match verdicts, candidate reconcile state.
- **MCPB bundle (#153)** — download, double-click, Claude Desktop runs the server; book file picker; demo books behind one checkbox. Built and attached by CI on every PR (tests locked on Python 3.10 and 3.13).
- MCP tool annotations on every tool, marking read-only and destructive tools (#150).
- `get_outstanding_documents` (renamed from `get_outstanding_invoices`) — the one-call answer to "what is actually unpaid?"
- Dashboard: warning rollups past three items (overdue-scheduled and stale-price collapse to one aggregate line each); staleness linkage ("time-based warnings below may reflect unentered activity"); reconciliation backlog line carries net unreconciled amount beside the split count.
- Demo books are living books: a closed-loop continuation engine (`scripts/synthetic_book/continue_book.py`) extends each sample from its committed history through the build date, deriving every flow from the book itself; CI runs the same updater so the bundle ships demo books current as of build day. Every book opens with zero dashboard warnings, reconciled through the last full month, current month open.
- Business transactions carry desktop's own split actions — `Invoice`, `Credit Note`, `Payment` — on every leg (forward-only; existing transactions untouched).
- Price sources rank in three tiers (manual quote > other user sources > feeds); `create_price` / `create_prices` report when a recorded price loses its date's tie.
- `apply_credit_note` against a document other than the one referenced is allowed and says so in the response and audit log.
- `debt_payoff_plan` lists balance-carrying debts that lack an `apr` slot.
- Transaction-entry defaults resolve loudly (date echoed when defaulted to today).

### Changed
- **Business surface consolidated, 48 tools → 27:** five `*_party` tools replace fifteen (`party_type: customer|vendor|employee`); nine `*_document` tools replace eighteen (`document_type: invoice|bill|voucher|credit_note`); jobs and taxtables fold `get_` into `list_(id=...)`. Audit entries render identically.
- Installer asks one module question — "Do you invoice clients?" Budgets, scheduled transactions, and investment tracking are always on; the Advanced field remains the full `--modules` escape hatch.
- Invoice/bill status vocabulary defined once: open / posted / paid / outstanding.
- CLI arguments reject unknown values instead of silently no-oping (`--modules all` means what it says) (#151).
- Invoice line items carry the document's own date (no more timezone-dependent entry dates).
- Credit notes are `type: "credit_note"` on every surface, with no due date.
- Every error message and docstring names the consolidated surface.
- Batch entry runs one signal sweep per batch instead of up to two per row — the 7-second p95 tail is gone.
- **Restart safety:** the first tool result of every process names the active book; with 2+ books configured, mutating tools are disarmed after a (re)start until `switch_book` confirms the target (a no-op "already on it" counts); every mutating response in a multi-book session names the book it wrote to. Reads are never gated; a refused write consumes no rate-limit token, triggers no backup, writes no audit line.
- **Discount-account auto-create refuses on localized books:** no English-named account is created in a localized chart. Existing discount accounts are still found and used.
- **Behavior change — currency-mismatch posting is refused:** posting a document to an A/R or A/P account in a different currency is refused, as in GnuCash desktop; the error points to a per-currency A/R or A/P account. Documents already posted that way still settle.
- Test suite (2,100+ tests) runs parallel by default via pytest-xdist; full suite under 40 seconds.
- `cryptography` 49 → 50.0.1 (#155).

### Removed
- `create_transaction` and `update_transaction` — the batch tools reached full parity (`update_transactions` gained an opt-in per-row `clear` column). `create_transactions` and `update_transactions` are canonical for one transaction or many.
- `list_backups` and `prune_backups` — the backup store is append-only from the model's side. `create_backup` stays; retention runs internally; reviewing or deleting backups is a human filesystem operation.
- Surface: 111 tools → 86.

### Fixed
- Signed amounts reach the duplicate scorer — a refund no longer blocks as its payment's twin.
- Invisible Unicode separators in TSV input reject by name and row.
- Credit-note identity survives the piecash slot-sweep on transaction delete; owner-mismatch errors name the per-type ID collision; voucher posting reachable (#152).
- The FX account resolver refuses to auto-create on localized books, as the discount resolver does; `create_document` refuses a `job_id` or `applies_to_id` that doesn't apply to the document type; the multi-book write guard and `GNUCASH_REDACT_PATHS` fail safe.
- Statement and batch entry: three N+1 query patterns eliminated.
- Demo-book continuation no longer dates interest into the books' committed history.
- Credit note nets against a job-grouped invoice from the same customer (owner check compared a Job GUID against a Customer GUID) (#165).
- Payment dry runs render as `PAY INVOICE (dry run)` in the audit log; partial payments report `partial`, not `paid` (#165).

## v1.4.2 - One call wide, every surface honest

The bulk grammar is complete — updates, prices, currency, and reconciliation are all one call wide — plus the project's first outside code contribution and an injection-hardening pass on the audit trail.

### Added
- `update_transactions` — per-row bulk edits via TSV (description, notes, date), one book open, one save, abort/skip semantics; `update_transaction` takes a guid list to broadcast one change (#145).
- `create_prices` — batch quote entry with `create_price`'s upsert behavior, plus a stale-price work list (#143).
- Per-transaction currency: a `cur` column in batch entry and scheduled-transaction templates (#141).
- Split `action` field on every transaction create path (#142).
- `get_reconciliation_status` — per-account drill-down behind the dashboard's counts (behind / never / current / dormant / excluded), same classification as the dashboard (#146).
- `no_reconcile` account slot opts statement-less accounts out of dashboard nagging; reporting-only (#146).
- `get_book_summary` hands each session its working set: top 15 accounts by posting frequency (last 180 days) in `%short-GUID` format (#146).

### Changed
- **Behavior change:** moving the posting date of a transaction with reconciled splits requires `force=true` on the single, broadcast, and batch update paths (#148).
- Dormant accounts ($0, fully reconciled, idle) collapse into one aggregate line; carried balances with months of silence stay individually warned (#146).
- Bulk-reconcile audit entries render what actually happened; reconcile errors teach — did-you-mean account suggestions, `through_date` hints, placeholder-children warnings (#137, #144).
- Five dashboard clarity fixes; notes convention pass (template notes, audit truth, leg preservation) (#138, #139).
- Release policy: sample books ship frozen and regenerate on demand via `scripts/synthetic_book/`.

### Fixed
- `reconcile_all` honors the statement-date bound its docstring always promised — splits after the statement date stay unreconciled (#144).
- `get_book_summary` on a 33k-split, 10-commodity GBP book never completed; price lookups now memoized per commodity pair and the split graph bulk-loaded — under 10 seconds on that book, ~45% faster on small books. Contributed by @bhbrunt (#126, follow-ups in #147).
- User-controlled text (descriptions, memos, payee strings) is escaped before reaching the audit file — a crafted newline pair could previously forge an audit entry or smuggle instructions to the model reading `get_audit_log` (#148).
- `create_prices` dry-run and live execution agree on duplicate identities (#148).

### Credits
- @hpuri (Gemini CLI and `libdbd-sqlite3` guidance, issue #89), @uppaljs (the `Decimal(str(value))` rule behind `_to_decimal`), @alhosani-abdulla (issue #94, intermediate-currency chain valuation).

## v1.4.1 - Batch entry grows up; every annotation field reachable

### Added
- `create_transactions` header-declared layout: per-split `memo` columns, per-transaction `notes` column, cross-commodity `qty` columns, field order fixed by the header's first group, trailing shorthand (a row may end after its last split's amount and account), auto-fill from history for rows with no split cells (`auto_filled_from:<guid>`, still duplicate-screened), strict header validation naming unknown columns.
- `delete_transaction` accepts a list of GUIDs — one open, one save, all-or-nothing.
- Invoice/bill/voucher/credit-note line items take `notes` (4096-byte cap) and `action`.
- `pay_invoice` takes a `memo` for the bank split.
- Account `notes` on `create_account` / `update_account` (same slot GnuCash desktop reads; `""` clears).
- `list_accounts` `query` — case-insensitive substring on path and description; composes with `root`; emits %short GUIDs.
- One-command sample-book rebuild through today.

### Changed
- Monthly-close valuation: flow reports value every split at its own month's closing rate in single-period and `group_by` modes, so totals agree at every granularity; stock reports keep as-of semantics; partial sub-periods marked `*`.
- Retirement accounts classify via an `is_retirement` slot, not English name-sniffing; Imbalance/Orphan matching requires the exact word or `-CUR` shape.

### Fixed
- Transactional `switch_book` — a failed switch no longer tears server state (retry said "Already on: B" while writes went to A).
- Per-book backup scoping — two books under a shared `GNUCASH_LOG_DIR` no longer cross-prune each other's backups.
- Localized FX-account wedge — cross-currency `pay_invoice` on a German book failed forever.
- Scheduled transactions persist their `description` (previously silently dropped).
- Instantiation audit entries show the created transaction's GUID and description.

### Tests
- 1,856 passing.

## v1.4.0 - Internationalization, batch entry, and multi-book

### Added
- Locale-robust account resolution: top-level accounts resolved by `GNCAccountType`, book locale inferred by voting across type accounts, designated accounts (FX gain/loss, discounts) self-heal via a KVP slot.
- Sabine Brenner — German DATEV SKR03 sample persona (EUR); Lin Wei localized to a zh_CN chart.
- `create_transactions` — batch transaction entry, one atomic call, per-row results correlated by caller `ref`, duplicates table keyed to it.
- Multi-book: `GNUCASH_BOOK_PATH` takes an `os.pathsep`-separated list; `switch_book` flips the active book in-session with a context-reset banner.
- Pagination: `offset` plus `Showing X-Y of Z` on all list-returning tools; dated tools render the covered range.
- `group_by` sub-period columns on aggregation reports.
- FX entry-sanity warning when a cross-currency transaction's implied rate diverges sharply from the latest price.

### Fixed
- Suspense/Imbalance accounts excluded from runway and low-cash signals.
- FX gain/loss booked in the book's default currency; both-foreign posting splits valued at the posting-date rate; lot cost basis in the default currency; foreign debts with no FX rate excluded from `debt_payoff_plan` with a warning.

### Tests
- 1,714 passing.

## v1.3.1 — Business module, role-aligned modules, multi-currency correctness

### Added
- Employee expense vouchers (`create_voucher`, `add_voucher_entry`, polymorphic post/pay/unpost/delete).
- Credit notes (`create_credit_note`, `add_credit_note_entry`, `apply_credit_note`); link persisted in `gnc-mcp/applies-to-invoice`.
- Jobs (`create_job`, `get_job`, `list_jobs`, `update_job`, `get_job_report`, `delete_job`); invoices and bills accept `job_id`.
- Tax tables (`create_taxtable`, `add_taxtable_entry`, `update_taxtable`, `list_taxtables`, `get_taxtable`, `delete_taxtable`); tax splits built at post time with residual-to-largest-rate rounding; tax-inclusive pricing; refcount blocks deletion of in-use tables. Surface 87 → 106 tools.
- Bulk reconciliation: `reconcile_all=true`, `except_guids=[...]`, `through_date`; account shortcuts accepted everywhere.
- Early-payment discounts honored: `pay_invoice` `apply_discount=True` validates terms, window, and shortfall on pre-tax principal; `discount_account` resolves explicit > leaf-name match > canonical default; `get_invoice` verbose surfaces `discount_available` / `discount_expired`.
- Synthetic "Unrealized Gain/Loss" equity row on `balance_sheet` (display-only balancing residual).
- Dashboard: overdue counts on receivables/payables lines; active-jobs line.
- Write rate-limiting via token bucket, opt-in (`GNUCASH_WRITE_RATE_LIMIT`, `GNUCASH_WRITE_BURST`).
- FX staleness cap (`GNUCASH_FX_STALENESS_DAYS`, default 90); invoice post/pay raise `StaleFXRateError` instead of posting on a stale rate (override with `force`).
- Intermediate-currency valuation via a pivot currency, with provenance (`via USD`). Reported by @alhosani-abdulla (#94).
- `create_budget` accepts `start_date` for retroactive budgets.

### Changed
- `--modules` partition is role-aligned: `core` (29, group alias for nine sub-modules), `bookkeeper` (17), `investor` (12: `portfolio` + `tax_lots`), `freelancer` (19), `business` (29); `get_server_config` renders groups as `core[accounts, audit, ...]`.
- `extra="forbid"` on every tool's argument model — unknown kwargs fail loudly.
- `delete_invoice` / `delete_bill` / `delete_voucher` / `delete_credit_note` accept `id` alongside `<entity>_id`.
- Server instructions 39% smaller (~2,500 → 1,522 chars).
- Token trimming: 8-char GUID prefixes in `get_transaction` and verbose `list_transactions`; business-object `guid` fields stripped from responses (`transaction_guid` kept, as a short prefix).
- Book directory path redacted from `get_server_config` / `get_book_summary` (filename only); tool errors route through `redact_paths()`.
- **Net worth restated:** RECEIVABLE and PAYABLE accounts now sit in their natural balance-sheet buckets across `balance_sheet`, `net_worth`, and the summary trajectory — outstanding A/R minus A/P is included; historical anchors shift accordingly.
- `get_book_summary` ~30% faster on large books.

### Fixed
- A = L + E holds by construction across `balance_sheet`, `net_worth`, and the summary.
- Multi-currency aggregation: monthly net, budget headline actuals, daily expense burn (runway), and vendor spending report all convert to default currency instead of summing raw units.
- `get_book_summary` FX-converts foreign-currency liabilities; `debt_payoff_plan` values foreign debt in default currency; `vendor_spending_report` excludes unconvertible bills with a per-currency warning.
- `income_by_source` / `spending_by_category` net contra splits per account (gross → net); budget actuals net contra splits the same way.
- Net-worth surfaces agree on which splits count: every account contributes exactly its own splits.
- `pay_invoice` rejects overpayments; `remaining_balance` / `amount_due` / job `outstanding` direction-normalized (`OVERPAID`); credit notes carry no aging clock.
- Native SX template transactions filtered from `list_transactions`, `search_transactions`, dashboard, `list_commodities`; `delete_scheduled_transaction` removes desktop recipe rows.
- Voided splits protected at every boundary: `update_transaction` / `replace_splits` refuse voided targets; `reconcile_account` skips voided splits; auto-fill and duplicate detection ignore voided history; balance surfaces exclude voided splits via `_own_splits_balance`.
- Future-dated transactions excluded from runway, low-cash warning, and `debt_payoff_plan`; null `post_date` renders `(no date)`.
- Report totals depth-invariant; `depth` matches its documented contract (the old off-by-one collapsed the default report to one row).
- Historical anchors never value via future rates; rate provenance names the path used.
- Invoice settlements count as cash flow.
- Cross-commodity A/R relieved at the carried rate (pro-rata for partials); discount leg's FX drift booked with the payment leg's.
- `calculate_lot_gain` converts foreign cost basis at historical purchase rates.
- FX direction label on credit-note refunds follows the booked direction.
- Auto-fill no-match guard fires — blank descriptions carry no match signal (previously cloned an unrelated transaction).
- Dashboard never ages credit notes.
- Backup retention works under `GNUCASH_REDACT_PATHS=1` (pruners resolved a redacted basename against the working directory).
- `update_account` rename enforces `create_account`'s name validation; fuzzy FX/discount matching skips template accounts; business free-text gates short-circuit at the schema boundary; write-verification handles two splits to the same account.
- Smaller: `update_taxtable` force gate spells out blast radius; debt-plan slots convert from the account's commodity; invoice-family deletes clean slot rows; `get_lot` marks voided rows.

### Tests
- 1,584 passing (was 1,114).

## v1.2.1 — Business module shipped, multi-currency hardened

### Added
- `update_customer`, `update_vendor`, `update_employee`.
- `unpost_invoice`; `delete_transaction` refuses to remove a posting record directly and points at `unpost_invoice`.
- Cross-currency invoicing end-to-end: `post_invoice` / `pay_invoice` apply price-table rates; post-to-pay drift booked as realized FX gain/loss (`fx_account` parameter or auto-created).
- Owner-currency inheritance for new invoices and bills.
- Dashboard work-queue sections in `get_book_summary`: last entry, net-worth trajectory (five anchors), monthly net income (six months), runway, budget headline, reconciliation backlog with split counts and oldest-split lag, upcoming scheduled, consolidated warnings.
- Auto-backup on first write per session to `<book>.gnucash.mcp/backups/`; staged retention 7 session / 4 weekly / 6 monthly; `PRAGMA integrity_check` on every snapshot; `create_backup(label)`, `list_backups`, `prune_backups(dry_run=true)`. Restore is deliberately not a tool ([docs/RESTORE_FROM_BACKUP.md](docs/RESTORE_FROM_BACKUP.md)).
- Short collision-safe GUIDs (`%xxxxxxx`) accepted everywhere a path is.
- `delete_price` with source disambiguation.
- Sample books ship with the repo: `samples/alex-chen-morales.gnucash` (USD), `samples/lin-wei.gnucash` (CNY).

### Fixed
- `balance_sheet`, `net_worth`, `cash_flow`, `get_book_summary` value foreign-currency holdings at shares × latest price (cost-basis fallback) — Alex's net worth was understated by ~$57K.
- Cross-sequence invoice/bill ID collisions fail loud naming both candidates.
- `create_price` and `get_latest_price` default to the book's currency, not USD.
- `debt_payoff_plan` uses the amortization formula for LIABILITY accounts (a ¥2.7M mortgage at 3.85% asked ¥54,590/mo instead of ~¥14,800).
- `unpost_invoice` ignores voided payment splits; `void_transaction` warns when reconciled splits are zeroed; `list_lots` skips empty lots; `owner_type` validated at all six entry points.
- Silent budget-amount truncation; backup filename collision and auto-backup gate race; swallowed auto-backup failures (summary now surfaces chain status).
- Investment cost-basis precision; scheduling month-end drift; tri-currency FX gain/loss; pay-invoice A/R-side conversion; per-commodity precision (JPY, BHD/KWD); audit before-state staging; write verification on `update_transaction` / `replace_splits`; `delete_account` dangling handle; audit before-state pre-clear; `prune_backups(keep_last_n=0, manual)` refuses to wipe every manual snapshot.
- Vendor bills render `POST BILL` / `PAY BILL` in the audit log; entries reject wrong account types; statement-balance comparison quantizes to commodity fraction; `unvoid_transaction` validates slot completeness; audit files written `0o600`; `_resolve_guid` per-table dispatch covering prices and entries; write-verification failures get their own `error_type`; `set_account_slot` rejects slash keys; `_describe_age` rounds.

### Tests
- 1,114 passing (was 540).

## v1.2.0 — Business module debut

### Added
- Customers and vendors with full address support; billing terms (Net 30, early-payment discounts).
- Invoices and bills with line items, posting to A/R or A/P, partial payments.
- Outstanding-invoices report and vendor-spending breakdown.
- GnuCash UI compatibility: posted invoices carry the metadata slots (`gncInvoice`, `trans-date-due`, `date-posted`) the desktop expects.
- Write verification: raw SQL operations read-back-checked before commit, automatic rollback on failure.
- `business` tool module (22 tools), opt-in via `--modules`.
- Server-level MCP instructions sent on connect.

### Tests
- 540 passing.

## v1.1.0 — Modular tool loading

### Added
- `--modules=` flag: seven modules (core, reconciliation, reporting, budgets, scheduling, investments, admin); 52 tools down to as few as 15; `core` always loaded; `--modules=all`.
- `get_server_config` debug tool (loaded with `--debug`).
- `GNUCASH_MCP_MODULES` env var.

### Tests
- 424 passing.

## v1.0.2 — Compact output

### Changed
- Compact one-line-per-item default output for `list_transactions`, `list_commodities`, `list_scheduled_transactions`, `get_unreconciled_splits`, `list_lots`; verbose JSON via `verbose=true`.
- Minified JSON: null/empty values stripped from all responses.

### Added
- `get_book_summary` single-call snapshot.
- Partial GUID support (8+ char prefixes) for transactions, splits, lots, scheduled transactions.

### Tests
- 399 passing.

## v1.0.0 — Stable release

### Added
- `replace_splits` — wholesale split replacement on existing transactions.
- Transaction pipeline: duplicate detection, dry-run mode, auto-fill from prior transactions, date sanity checks, placeholder-account warnings.
- `list_accounts` compact mode with `root` filter.
- Account-metadata slots (APR, credit limits, reward rates).
- Audit log text format alongside JSON.

### Tests
- 394 passing.

## v0.9.0 — Feature build-out

### Added
- Investments: commodities, prices, lot-based cost basis, capital gain calculation.
- Scheduled transactions: recurring templates, upcoming bills, one-click instantiation.
- Budgets: create, set targets by period/quarter, variance reporting.
- Multi-currency: cross-currency transactions with quantity/value split handling.
- Reporting: spending by category, income by source, balance sheet, net worth, cash flow.
- Reconciliation: statement reconciliation, void/unvoid with audit trail.
- Audit logging alongside the book file.

### Tests
- 187 passing.

## v0.1.0 — Initial release

### Added
- Account listing, balances, transaction CRUD, search.
- MCP server via FastMCP; Claude Desktop integration.
- piecash SQLite interface with error handling.

---

*On the version numbers.* A missing number is a choice, not a lost release. 1.2 and 1.3 were tagged at 1.2.1 and 1.3.1 because the maintainer liked the palindromes better, and 1.4 shipped as 1.4.0 because 14 is the maintainer's favorite number. The one exception is v1.4.3, noted under v1.4.4.
