# Changelog

Entries are terse by design: what changed, one line each, PR numbers where they exist. Rationale lives in the PRs, the specs, and the bookkeeper rulings recorded under `specs/`.

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
