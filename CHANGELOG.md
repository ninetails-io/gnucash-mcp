# Changelog

Entries are terse by design: what changed, one line each, PR numbers where they exist. Rationale lives in the PRs, the specs, and the bookkeeper rulings recorded under `specs/`.

## Unreleased

The server's rows are GnuCash's rows. A book can live in PostgreSQL or MySQL as well as a SQLite file; every object the server stores (schedules, budgets, invoice links, credit notes, payments, prices, voids, lots, reconcile state) is now written the way GnuCash desktop writes it and checked against GnuCash's own engine; one write converts an existing book, with a snapshot taken first. Two adversarial reviews and four bookkeeper loops ran before the bump.

### Added
- **Database-backed books** — `GNUCASH_BOOK_URI` / `--book-uri` serve a book GnuCash keeps in PostgreSQL or MySQL/MariaDB; every tool works unchanged. Extras: `gnucash-mcp[postgres]`, `gnucash-mcp[mysql]`. CI runs the suite against `postgres:16` and `mariadb:11`. PostgreSQL by @vchatela (#175, from #174); MySQL/MariaDB in #181. Backup refusals name the dialect's dump tool; `docs/RESTORE_FROM_BACKUP.md` covers both.
- **Num and the document link** — read and written on every transaction tool (`num`, `link` columns; `search_transactions(field="num")`; `num:` cell in lists). Statement entry follows "Use Split Action Field for Number". A matching number is a duplicate signal (`N`), a differing one a caution (`x`) that never blocks. Gap found by @wernerwws's fork.
- **Prepayments** — `pay_document` takes `allow_prepayment` (the excess is held as the party's unapplied payment, as desktop splits it), `from_prepayment` (settle a document from money already paid, including a prepayment entered in desktop), and `payment_account_amount` (what the bank actually moved on a cross-currency payment). `unpost_document` keeps payments as `gncInvoiceUnpost` does. Unapplied payments show in `get_outstanding_documents`, `get_document`, and the dashboard.
- `update_account(hidden=…)`; `update_scheduled_transaction(start_date=…)`; `reconcile_account(closing_balance=…)`; `post_document` / `get_document` report `charged_to_card` for voucher lines paid by company card.
- Dashboard: `Overdrawn:` per negative cash account, `Low cash:` when the week's scheduled bills exceed the balance, unbalanced transactions flagged, and a check that fails says so on its own line instead of reading as a clean book.
- Audit trail: a write the server did not live to log becomes an `INTERRUPTED` line; `create_backup` leaves a line; a locked book's error names the lock's holder and whether it is still running.
- Startup reads a SQLite book's `versions` table and names a non-GnuCash file, a 2.6-era book, or an unreadable schema with what to do. The README states the minimum GnuCash version, 3.8.

### Changed
- **Every stored shape is desktop's.** Schedules are template transactions with `sched-xaction` slots, their next occurrence from a port of `Recurrence.cpp` (#176, #179). Budgets use GnuCash 3.8+'s natural-sign storage (#177, #178). Invoice links sit under `gncInvoice/invoice-guid` (#180). A credit note's quantities are stored negated and applied by a `Lot Link` transaction; posted documents point at a hidden copy of their billing term; due dates are written in the slot shape desktop reads, from a port of `gncBillTerm.c`. Reconcile dates, `reconcile-info`, slot filler columns, lot flags, and voids (`xaccTransVoid` key for key) match desktop's rows. Verified by parity twins (the same flow in desktop, diffed row by row) and engine twins (`gnucash-cli` driving the engine headlessly); the twins are tests.
- **Upgrading from 1.2–1.4.4:** the first schedule, budget, business, price, void, or reconcile write converts the whole book at once, posts nothing, and reports what it converted. Do it before your next desktop session: opening a server-made schedule in desktop's editor crashes GnuCash 5.12 until then, and the dashboard says so. A copy of the book is written to the backups folder first, labelled `pre-1-5-upgrade` and never pruned, and the write is refused if that copy cannot be made. A converter touches only rows an earlier server wrote; the book is marked, and a later write by a 1.4.x server is reported. Database books: run `pg_dump` or `mysqldump` first.
- **Prices.** One price per pair per day, as `gnc_pricedb_add_price` keeps them; which price is current follows `compare_prices_by_date`; the `type='transaction'` row a cross-currency transaction leaves is a price and is valued by, as desktop does (#94 skip overturned). Sources must be ones the price editor recognizes; dates at GnuCash's neutral time; values reduced. Every price read goes through `_find_prices` (#186, on @bhbrunt's #182), and read tools issue a fixed number of queries whatever the book's size.
- **Money.** Invoices total the way GnuCash totals them (`gncEntryComputeValueInt` ported; the server disagreed by a cent on a third of multi-line taxed invoices). A posted document's total is read from its posting, not recomputed. A cross-currency payment is booked in the transfer account's currency, with realized FX as an ordinary balanced amount. Converted balances round once per account, so every statement's lines add up to its total. Amounts print at their commodity's own places. Flow reports value each split at its month's close (the GB-1 convention) and say so in a footer when a foreign-currency line is present.
- **Refusals, as desktop refuses.** A document's posting transaction is read-only on every path, no `force`. A trading-accounts book refuses a cross-commodity write until the server writes trading splits as GnuCash does. A document in a currency its owner does not use needs `force`. The payment account is never a receivable or payable. Text refuses control characters, over-width values (GnuCash's column widths), names of more than one line, and account names that read like a sibling's or like a GUID; dates outside 1400–9998; prices that are zero, negative, or self-referential. The book's read-only period is named, as a warning, on every write that lands in it.
- `--modules=bookkeeper` is everything but business; `--modules=business` is one module (#173). `party_type` is the one name on every business tool. `get_document` carries `status`, `amount_paid`, `amount_due`; new response fields are additive throughout.
- Dashboard accuracy pass (`specs/v1.5/DASHBOARD_ACCURACY_SPEC.md`): overdue reads the settlement chokepoint, "last entry" stops at today, monthly net converts at each month's close, staleness names the rate actually used, reconciliation "behind" is measured from the last reconcile and follows desktop's statement cycle, budget pace follows the budget's own periods.
- Audit and debug files open by path on every entry; logs, backups, and the Docker image's process are owner-only; connection strings are password-masked on every road out of the server. URI mode is single-book and requires `GNUCASH_LOG_DIR`.

### Fixed
- **Pre-release adversarial review** (`specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md`): seven hostile passes returned NO-SHIP on seven blockers; every blocker and serious item is fixed, the minors are in `tests/test_review_minor.py`. **Two scoped reviews** of the fixes (`specs/v1.5.1/review/`, 81 findings) are fixed except nine listed below, each cleared by a bookkeeper loop and a desktop gate.
- An unpriced holding is worth its remaining cost basis; a sold-out position is exactly zero (#184 by @DrSkippy, #185).
- Invoice lookups and tax-bearing drafts work on PostgreSQL: a SQLite-only heal and a SQLite-only `MAX` had aborted every document call there (#189, @JamesRao98).
- Intel Macs install without Rust: `cryptography` pinned to 48.0.1 on macOS x86_64 only (#183).
- A credit note at zero reads `applied`; `close_lot` closes only an empty lot; job-attached documents are named by their kind (#173, #181).
- A schedule desktop stored as a fraction (`100/3`) no longer takes the dashboard down; ID counters GnuCash 5.0/5.1 saved as doubles auto-number again; a credit note raised on a job in desktop can be edited and applied.
- The converters change only rows the old server wrote (they used to rewrite desktop's credit notes, line dates, and bill-term counts, and raise on a GnuCash 2.6 timestamp).
- Deleting a transaction, split, account, or document no longer sweeps another entity's slots through piecash's GUID cascade.
- Report date ranges are decided on the decoded date; a book file replaced mid-write is not reported as a failed write; automatic backups keep running in a long session; two same-named books under one `GNUCASH_LOG_DIR` keep separate folders; a database password never appears in an error or log, whatever characters it holds.
- The early-payment discount is measured on what credit notes have not settled and counts from the posting date; the budget report converts month by month like the spending report; a scheduled transaction picks its rate the way a posting does.
- Audit: `get_audit_log` no longer returns the book's full path; account-update entries list only what changed; a failed render never replaces a committed write's result.
- A statement line a year off its statement, a far-future batch date, and a `create_price` suggestion that would not have worked are each caught where they are entered.

### Known limitations
Numbers refer to the review documents; `specs/v1.5.1/README.md` keeps the full list.
- The server takes no `gnclock` row of its own; do not edit in desktop and the server at once (C11).
- The read-only period is a warning, not a refusal (C69). Credit notes do not stamp the `Credit Notes` feature flag, which only GnuCash before 2.5 reads (C62).
- In a book with "Use Trading Accounts" on, stock and fund purchases as well as currency transfers must be entered in desktop; ticking the option in desktop rewrites the book's history with trading splits, which is desktop's doing.
- GnuCash's Balance Sheet does not foot on a multi-currency book without trading accounts; payments booked by 1.2–1.4.4 across currencies add to that gap, 1.5's do not. See `samples/README.md`.
- Foreign-currency flows are valued at their month's closing rate, not at the cash paid; `cash_flow` reports the cash (SR-B1).
- The bundled demo books are piecash-made, not GnuCash-made (FC-18); the suite also runs on a book `gnucash-cli` creates (FC-19).
- A book path containing `?` cannot be opened (S-9). Under `GNUCASH_LOG_DIR`, a book reached by a differently-cased or moved path gets a new log folder (CS-9); a pre-1.5 folder that names another owner is left in place with its history.
- A currency whose smallest fraction is not a power of ten (MGA) can receive a converted amount off its grid on the business module's conversion paths (BM-9). An amount whose numerator does not fit 64 bits passes the dry run and fails at commit (IN-7).
- Engine strings ("Lot Link", "Voided transaction", document titles) are written in English (BS-8). A desktop document to which a 1.4.x server added a line is treated as the old server's by the document-level converter passes, without changing a value (BS-7); rows a 1.4.x server writes after the conversion are converted without a second snapshot and named (BS-9).
- Only `create_transactions` warns about a far-future date (IN-5); a report whose start is after its end answers an empty total (IN-20).

### Credits
- @vchatela — the database backend (#175), the largest outside code contribution to date.
- @DrSkippy — the closed-position valuation bug and its regression test (#184).
- @bhbrunt (#182), @JamesRao98 (#189), @wernerwws — findings and fixes credited above.

## v1.4.4 - The statement is the call

A complete bank statement enters, claims its matches, and reconciles in one atomic call; every consequential write now rehearses before it books; a one-click Claude Desktop bundle ships from the project's first CI. (v1.4.3 was never released on GitHub — that number belongs to a registry-side rebuild.)

### Added
- **`enter_statement` (#154)** — opening balance, closing balance, every line between, one atomic call. Statement self-check (opening + Σlines = closing) before anything else. Dry-run is the default: every line classified NEW / MATCH / OVERLAP / AMBIGUOUS with self-contained comparisons (both sides, deltas, category legs). Commit is one open, one save, or nothing; the closing tie is verified post-write from saved splits. Amounts transcribe statement-native; the server applies sign conventions per account class.
- `force` on `enter_statement` split into `force_base` / `force_duplicates` — clearing an opening gap can never silently disable duplicate detection.
- `pay_document` `dry_run` — proposed splits, projected remaining balance, FX/discount treatment, and any account the real call would auto-create; one shared computation with the booking path.
- `create_transactions` dry-run gains the statement surface: summary header, `review_required` status, self-contained duplicate comparisons with deltas and split-match verdicts, candidate reconcile state.
- **MCPB bundle (#153)** — download, double-click, Claude Desktop runs the server; book file picker; demo books behind one checkbox. Built and attached by CI on every PR (tests locked on Python 3.10 and 3.13).
- MCP ToolAnnotations on every tool, derived from the audit-log classification at one chokepoint, with a contract test (#150).
- `get_outstanding_documents` (renamed from `get_outstanding_invoices`) — the one-call answer to "what is actually unpaid?"
- Dashboard: warning rollups past three items (overdue-scheduled and stale-price collapse to one aggregate line each); staleness linkage ("time-based warnings below may reflect unentered activity"); reconciliation backlog line carries net unreconciled amount beside the split count.
- Demo books are living books: a closed-loop continuation engine (`scripts/synthetic_book/continue_book.py`) extends each sample from its committed history through the build date, deriving every flow from the book itself; CI runs the same updater so the bundle ships demo books current as of build day. Every book opens with zero dashboard warnings, reconciled through the last full month, current month open.
- Business transactions carry desktop's own split actions — `Invoice`, `Credit Note`, `Payment` — on every leg (forward-only; existing transactions untouched).
- Price sources rank in three tiers (manual quote > other user sources > feeds); `create_price` / `create_prices` report when a recorded price loses its date's tie.
- `apply_credit_note` against a document other than the one referenced is allowed and says so in the response and audit log.
- `debt_payoff_plan` confesses balance-carrying debts that lack an `apr` slot.
- Transaction-entry defaults resolve loudly (date echoed when defaulted to today).

### Changed
- **Business surface consolidated, 48 tools → 27:** five `*_party` tools replace fifteen (`party_type: customer|vendor|employee`); nine `*_document` tools replace eighteen (`document_type: invoice|bill|voucher|credit_note`); jobs and taxtables fold `get_` into `list_(id=...)`. Audit entries render identically. Docstrings lead with the species.
- Installer asks one module question — "Do you invoice clients?" Budgets, scheduled transactions, and investment tracking are always on; the Advanced field remains the full `--modules` escape hatch.
- Invoice/bill status vocabulary defined once: open / posted / paid / outstanding.
- CLI arguments reject unknown values instead of silently no-oping (`--modules all` means what it says) (#151).
- Invoice line items carry the document's own date (no more timezone-dependent entry dates).
- Credit notes are `type: "credit_note"` on every surface, with no due date.
- Every error message and docstring names the consolidated surface.
- Batch entry runs one signal sweep per batch instead of up to two per row — the 7-second p95 tail is gone.
- **Restart safety (bookkeeper ruling 6):** first tool result of every process names the active book; with 2+ books configured, mutating tools are disarmed after a (re)start until `switch_book` confirms the target (a no-op "already on it" counts); every mutating response in a multi-book session names the book it wrote to. Reads are never gated; a refused write consumes no rate-limit token, triggers no backup, writes no audit line.
- **Discount-account auto-create refuses on localized books (ruling 4b):** no more English default leaf in a localized chart. Existing accounts remain adoptable through every resolution layer. Role-based resolution (4a) is the destination that lifts the refusal.
- **BEHAVIOR BREAK — currency-mismatch posting is a refusal (ruling 1 sunset):** posting a document to an A/R or A/P account in a different commodity refuses (desktop GnuCash does the same), pointing at the per-currency subledger fix. Warning-era books are unaffected: their lots still settle via FX and the downstream guards stay.
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
- Release code review (8 angles, 10 confirmed findings, 10 fixes): the FX resolver gains the same fail-safe locale gate as discounts; `delete_document` regains the credit-note `party_type` disambiguator; `create_document` refuses type-inapplicable `job_id` / `applies_to_id`; the retired freelancer toggle is honored (its surface never joined the always-on base); the multi-book write disarm no longer caches a blind fail-open; `GNUCASH_REDACT_PATHS` fails closed through the toggle chokepoint.
- Statement/batch hot path: three N+1 query patterns eliminated (candidate universe, signal sweep, split-prefix map); the query budget is contract-locked.
- Demo continuation: revolver interest can't date into the frozen prefix, and the updater independently verifies the prefix's row count, failing loud.
- `text()`-form raw SQL is now visible to the write-verification contract lock.
- Credit note nets against a job-grouped invoice from the same customer (owner check compared a Job GUID against a Customer GUID) (#165).
- Payment dry runs render as `PAY INVOICE (dry run)` in the audit log; partial payments report `partial`, not `paid` (#165).

### Validation
- Three live bookkeeper probe rounds on `enter_statement`; full business-module battery on the German sample book (#165); the deferred battery rulings' six-call live loop (one FAIL found, fixed, re-probed to signoff); demo-fleet review with all findings closed and signoff on the record.

## v1.4.2 - One call wide, every surface honest

The bulk grammar is complete — updates, prices, currency, and reconciliation are all one call wide — plus the project's first outside code contribution and an injection-hardening pass on the audit trail.

### Added
- `update_transactions` — per-row bulk edits via TSV (description, notes, date), one book open, one save, abort/skip semantics; `update_transaction` takes a guid list to broadcast one change (#145).
- `create_prices` — batch quote entry with `create_price`'s upsert semantics via a shared chokepoint, plus a stale-price work list (#143).
- Per-transaction currency: a `cur` column in batch entry and scheduled-transaction templates (#141).
- Split `action` field on every transaction create path (#142).
- `get_reconciliation_status` — per-account drill-down behind the dashboard's counts (behind / never / current / dormant / excluded), same classification as the dashboard (#146).
- `no_reconcile` account slot opts statement-less accounts out of dashboard nagging; reporting-only (#146).
- `get_book_summary` hands each session its working set: top 15 accounts by posting frequency (last 180 days) in `%short-GUID` format (#146).

### Changed
- **Behavior change:** moving the posting date of a transaction with reconciled splits requires `force=true` on the single, broadcast, and batch update paths (#148).
- Dormant accounts ($0, fully reconciled, idle) collapse into one aggregate line; carried balances with months of silence stay individually warned (#146).
- Bulk-reconcile audit entries render what actually happened; reconcile errors teach — did-you-mean account suggestions, `through_date` hints, placeholder-children warnings (#137, #144).
- Five dashboard clarity fixes from live review; notes convention pass (template notes, audit truth, leg preservation) (#138, #139).
- Release policy: sample books ship frozen and regenerate on demand via `scripts/synthetic_book/`.
- Contributor guide documents the chokepoint pattern with the established chokepoints named.

### Fixed
- `reconcile_all` honors the statement-date bound its docstring always promised — splits after the statement date stay unreconciled (#144).
- `get_book_summary` on a 33k-split, 10-commodity GBP book never completed; price lookups now memoized per commodity pair and the split graph bulk-loaded — under 10 seconds on that book, ~45% faster on small books. Contributed by @bhbrunt (#126). Follow-ups: `create_prices` invalidates the price memo, a SQL-count regression test guards the preload, a final `guid` sort key makes the price tie-break deterministic (#147).
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
- Monthly-close valuation (GB-1): flow reports value every split at its own month's closing rate in single-period and `group_by` modes, so totals agree at every granularity; stock reports keep as-of semantics; partial sub-periods marked `*`.
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
- Pathological-shapes fixture book (parents/placeholders with direct splits, overpaid lot, voided transaction, desktop SX template, foreign A/R, future-dated entry) run against every report surface.

### Changed
- `--modules` partition is role-aligned: `core` (29, group alias for nine sub-modules), `bookkeeper` (17), `investor` (12: `portfolio` + `tax_lots`), `freelancer` (19), `business` (29); `get_server_config` renders groups as `core[accounts, audit, ...]`.
- `extra="forbid"` on every tool's argument model — unknown kwargs fail loudly.
- `delete_invoice` / `delete_bill` / `delete_voucher` / `delete_credit_note` accept `id` alongside `<entity>_id`.
- Server instructions 39% smaller (~2,500 → 1,522 chars).
- Token trimming: 8-char GUID prefixes in `get_transaction` and verbose `list_transactions`; business-object `guid` fields stripped from responses (`transaction_guid` kept, as a short prefix).
- Book directory path redacted from `get_server_config` / `get_book_summary` (filename only); tool errors route through `redact_paths()`.
- **Net worth restated:** RECEIVABLE and PAYABLE accounts now sit in their natural balance-sheet buckets across `balance_sheet`, `net_worth`, and the summary trajectory — outstanding A/R minus A/P is included; historical anchors shift accordingly.
- Internal: `CurrencyMixin` extraction, `_compute_fx_gain_loss` helper, `get_book_summary` decomposed into `_render_*` helpers (~30% faster on large books), `QueryMixin` finders.

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
- Self-conducted code review ([specs/CODE_REVIEW.md](specs/CODE_REVIEW.md)): 3 critical, 12 high, 26 medium, 18 low findings; all criticals, all highs, all real-bug mediums, and 11 of 18 lows closed in this release.

### Fixed
- `balance_sheet`, `net_worth`, `cash_flow`, `get_book_summary` value foreign-currency holdings at shares × latest price (cost-basis fallback) — Alex's net worth was understated by ~$57K.
- Cross-sequence invoice/bill ID collisions fail loud naming both candidates.
- `create_price` and `get_latest_price` default to the book's currency, not USD.
- `debt_payoff_plan` uses the amortization formula for LIABILITY accounts (a ¥2.7M mortgage at 3.85% asked ¥54,590/mo instead of ~¥14,800).
- `unpost_invoice` ignores voided payment splits; `void_transaction` warns when reconciled splits are zeroed; `list_lots` skips empty lots; `owner_type` validated at all six entry points.
- Criticals: silent budget-amount truncation; backup filename collision and auto-backup gate race; swallowed auto-backup failures (summary now surfaces chain status).
- Highs: investment cost-basis precision; scheduling month-end drift; tri-currency FX gain/loss; pay-invoice A/R-side conversion; per-commodity precision (JPY, BHD/KWD); audit before-state staging; write verification on `update_transaction` / `replace_splits`; `delete_account` dangling handle; audit before-state pre-clear; `prune_backups(keep_last_n=0, manual)` refuses to wipe every manual snapshot.
- Mediums/lows: vendor bills render `POST BILL` / `PAY BILL` in the audit log; entries reject wrong account types; statement-balance comparison quantizes to commodity fraction; `unvoid_transaction` validates slot completeness; audit files written `0o600`; `_resolve_guid` per-table dispatch covering prices and entries; write-verification failures get their own `error_type`; `set_account_slot` rejects slash keys; `_describe_age` rounds.

### Tests
- 1,114 passing (was 540). Roadmap for 1.3: [specs/NEXT_STEPS_1_3.md](specs/NEXT_STEPS_1_3.md).

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
