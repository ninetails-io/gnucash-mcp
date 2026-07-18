# Changelog

## v1.4.1 - Batch entry grows up; every annotation field reachable

v1.4.0 introduced batch transaction entry; v1.4.1 is the release
where it grows up — memos, notes, cross-commodity quantities, and
batch delete, driven directly by the bookkeeper's daily
reconciliation workflow. Alongside it: the adversarial-review
hardening pass, monthly-close valuation for flow reports, and memo/
notes coverage for every write surface that was silently dropping
them.

**Batch entry - the header is the schema.**

The `create_transactions` TSV header row now declares the layout.
Legacy submissions parse byte-identically; extensions are opt-in by
naming them:

- **Per-split memos** - declare `memo` columns and splits become
  `(amount, account, memo)` groups. Check numbers and statement
  refs now ride the PDF-reconcile workflow in one call.
- **Per-transaction notes** - a `notes` column after `description`.
- **Cross-commodity quantities** - declare `qty` columns for splits
  on investment or foreign-currency accounts (`qty` in the
  account's own commodity, `amt` always in the book default). The
  book layer supported this all along - the restriction was
  parser-only.
- **Field order from the header** - `amt, acct, qty, memo` is as
  valid as `amt, acct, memo, qty`; the header's first group fixes
  the order.
- **Trailing shorthand** - a row may end once its last split's
  amount and account are present; trailing memo/qty cells read as
  empty. The most common formatting mistake is now valid input.
- **Auto-fill from history** - a row with NO split cells at all
  reproduces the most recent matching-description transaction
  (splits, memos, quantities), marked `auto_filled_from:<guid>` in
  the results. Twelve recurring bills become twelve
  ref-date-description rows; dry-run the batch first to preview
  every match, then run it for real. Filled rows still pass the
  duplicate screen - auto-fill is not a bypass.
- **Strict header validation** - unknown or typo'd column names
  (`meno1`, `currency2`) reject naming the offending column instead
  of misparsing into raw decimal errors.

**Batch delete.**

- `delete_transaction` accepts a list of GUIDs - one book open, one
  save, all-or-nothing (every guid validates before anything is
  deleted). Ten 20-second cleanup calls become one. Single-guid
  behavior unchanged.

**Memo & notes coverage - every annotation field the schema had.**

- Invoice/bill/voucher/credit-note line items take `notes` (4096-
  byte cap) and `action` ("Hours", "Material") - columns that
  existed in the entries table since day one, hardcoded empty.
- `pay_invoice` takes a `memo` for the bank split (check number,
  wire reference); the A/R//A/P split keeps its Payment action.
- Account notes on `create_account` / `update_account`, stored in
  the same `notes` slot GnuCash desktop's account editor reads.
  Pass `""` to clear (slot deleted, matching desktop).
- Scheduled transactions persist their `description` (previously
  accepted and silently dropped; instantiation used the name).
  Existing templates fall back to the name - no behavior change.
- Instantiation audit entries show the created transaction's GUID
  and description (previously the schedule's GUID and an empty
  string); duplicate rejections render as such.

**Correctness & hardening - the v1.4 adversarial review closed out.**

- **Transactional `switch_book`** - a failed switch no longer tears
  server state (retry said "Already on: B" while writes went to
  book A). Everything fallible runs before the globals move; PID-
  stamped debug lines and no-op switch logging for forensics.
- **Per-book backup scoping** - two books under a shared
  `GNUCASH_LOG_DIR` no longer starve or cross-prune each other's
  backups; the log dir resolves to per-book `.mcp` subdirectories.
- **i18n follow-through** - the localized FX-account wedge fixed
  (cross-currency `pay_invoice` on a German book failed forever);
  Imbalance/Orphan matching requires the exact word or `-CUR`
  shape; retirement accounts classify via an `is_retirement` slot,
  not English name-sniffing.
- **Monthly-close valuation (GB-1)** - flow reports value every
  split at its own month's closing rate in single-period and
  `group_by` modes alike, so grand totals agree at every
  granularity. Stock reports keep as-of semantics - deliberately
  different. Partial `group_by` sub-periods are marked `*`.

**Discovery.**

- **Find accounts without paging** - `query` on `list_accounts`:
  case-insensitive substring against path AND description, so
  "4930" finds the SKR03 account and "保险" finds every insurance
  account. Composes with `root`; results emit ready-to-use %short
  GUIDs. A parameter, not a new tool.

**Tooling.**

- One-command sample-book rebuild through today.

**Tests:** 1,856 passing.

---

## v1.4.0 - Internationalization, batch entry, and multi-book

The first widely-promoted release. v1.3 finished the business
module; v1.4 makes the server locale-robust, adds bulk and
multi-book workflows, and closes a second multi-currency
correctness pass.

**Internationalization - locale-robust account resolution.**

GnuCash localizes account *names* per locale but never account
*types*. Every place the server keyed off an English literal
("Income", "Imbalance", "mortgage") was wrong the moment a book
was `de_DE` / `es_MX` / `zh_CN`. v1.4 resolves top-level accounts
by `GNCAccountType`, votes across several type accounts to infer a
book's locale, and self-heals designated accounts (FX gain/loss,
discounts) via a KVP slot that becomes rename- and locale-proof
after first use.

- Locale-robust resolution across balancing detection, Imbalance /
  Orphan handling, and cross-currency FX account naming.
- Suspense / Imbalance accounts excluded from runway and low-cash
  signals so a lopsided book doesn't distort the dashboard.
- **Sabine Brenner** - a German DATEV SKR03 synthetic persona (EUR
  default, numbered German account names) that makes the i18n bug
  class visible; ships alongside Alex (USD) and Lin Wei (CNY, now
  localized to a zh_CN chart of accounts).

**Bulk and multi-book workflows.**

- **Batch transaction entry** (`create_transactions`) - submit
  many transactions in one atomic call, with a per-transaction
  result correlated back by a caller-supplied `ref` and a
  duplicates table keyed to it. One book open, one save for the
  whole batch.
- **Multi-book** - `GNUCASH_BOOK_PATH` takes an `os.pathsep`-
  separated list; `switch_book` flips the active book in-session
  (matched by unique filename prefix) with a context-reset banner
  so cross-book references don't leak.

**Reporting.**

- **Pagination** - `offset` plus a `Showing X-Y of Z` indicator
  across all list-returning tools; dated tools also render the
  covered date range.
- **group_by** - sub-period columns on the aggregation reports.

**Multi-currency correctness (second pass).**

- FX gain/loss booked in the book's default currency; both-foreign
  posting splits valued at the posting-date rate; lot cost basis
  in the default currency; foreign debts with no FX rate excluded
  from `debt_payoff_plan` (with a warning).
- FX entry-sanity warning when a cross-currency transaction's
  implied rate diverges sharply from the latest price on file.

**Tests:** 1,714 passing.

---

## v1.3.1 — Business module, role-aligned modules, multi-currency correctness

v1.2.1 fixed everything the business module *should* have been at first
ship. v1.3 finishes the headline features the bookkeeper had been
asking for since v1.2 and reshapes the public module surface around
how real users actually deploy the server.

**Business module — the four big features.**

- **Employee expense vouchers** (`create_voucher`, `add_voucher_entry`,
  `post_invoice` / `pay_invoice` / `unpost_invoice` /
  `delete_voucher` polymorphic). The third half of the invoice
  lifecycle alongside customer invoices and vendor bills. Employees
  submit reimbursable expenses; voucher posts to A/P, payment hits
  the chosen cash account. Same `owner_type='employee'` dispatch the
  other invoice tools already understood.
- **Credit notes** (`create_credit_note`, `add_credit_note_entry`,
  `apply_credit_note`, plus the shared `post_invoice` /
  `unpost_invoice` / `delete_credit_note` path). Refund and return
  documents for customers and vendors. Posts as a negative invoice;
  applies against an outstanding invoice or bill to net the
  payable/receivable down. Lifecycle tracks
  `gnc-mcp/applies-to-invoice` so the link survives between sessions.
- **Jobs** (`create_job`, `get_job`, `list_jobs`, `update_job`,
  `get_job_report`, `delete_job`). Project-level grouping over
  invoices and bills for a single customer or vendor. A jobs-level
  P&L (`get_job_report`) shows revenue, costs, net by project — the
  view a freelancer needs at year-end without manually slicing every
  client's transactions. Invoices and bills accept an optional
  `job_id` at creation; existing tools see job-attached documents
  through the same surfaces.
- **Tax tables** (`create_taxtable`, `add_taxtable_entry`,
  `update_taxtable`, `list_taxtables`, `get_taxtable`,
  `delete_taxtable`). Composite tax rates (e.g. state + local
  sales tax stacked) attached to invoice/bill/voucher line items.
  Posting builds the tax splits at post-time from each entry's
  taxable amount × applicable rate, with residual-to-largest-rate
  rounding so totals tie exactly. Tax-inclusive pricing (`price` is
  gross, pretax extracted at post) handled symmetrically. Refcount
  discipline blocks deletion of in-use taxtables.

The Stage 3 surface adds 19 tools to the catalog (87 → 106), each
exercised against the synthetic test books before release.

**Module surface — role-aligned partition.**

`--modules` previously partitioned by code organization
(`core / admin / backup / investments / business`). The new partition
matches how people actually use the server:

- **`core`** (29 tools) — always-on ledger primitives:
  accounts, transactions, slots, audit, backup, balance sheet,
  summary, diagnostics, reconciliation. Now a group alias
  expanding to nine independently-selectable sub-modules; you
  can opt into the group or pick the sub-modules à la carte.
  *Reconciliation joined core late in the v1.3 cycle —
  bookkeeper-flagged that reconciliation touches money and
  every configuration touches money, so excluding it from any
  persona produced a server that couldn't reconcile statements.*
- **`bookkeeper`** (17 tools) — personal-finance management:
  reporting, budgets, scheduling. Group alias for the three
  underlying modules.
- **`investor`** (12 tools) — group alias for `portfolio`
  (commodities + prices, the multi-currency primitive) plus
  `tax_lots` (cost basis tracking).
- **`freelancer`** (19 tools) — customer-facing invoicing: invoice
  creation, posting, payment, outstanding-invoices, taxtables.
- **`business`** (29 tools) — vendor management, employee
  expenses, jobs, credit notes, customers, billing terms.

`get_server_config` renders the loaded modules as
`core[accounts, audit, ...], reporting, ...` so it's clear what's
inside each group without reading source. Group expansion is single-
pass; the partition is deliberately flat. See the README's "Choosing
a module set" table for which modules to load for which use case.

**Reconciliation — bulk mode for OFX-import workflows.**

- **`reconcile_all=true`** reconciles every unreconciled split on
  the account in one call. The common case for "I just imported a
  bank statement — everything matches; reconcile it all." No
  GUID round-trip; one call instead of ~100 if a month's worth of
  transactions are involved.
- **`except_guids=[...]`** carves exceptions out of the bulk set.
  When the statement matches the book *except* for one pending
  ACH or a manual split the bank doesn't know about yet, two
  prefix tokens describe the exception instead of the 100+ a full
  `split_guids` listing would cost.
- **`through_date`** filters bulk mode to splits on or before a
  date — useful when you're reconciling a mid-month statement.
- **Account shortcuts** accepted everywhere the older
  reconciliation tools wanted full paths. `%2e78c86` flows in and
  out of `reconcile_account` the same way it does in every other
  account-aware tool.

**Token bloat trimmed on read tools and business responses.**

Bookkeeper-found during PR #92 review. Two patterns wasted
tokens with no callable benefit:

- **`get_transaction` and `list_transactions(verbose=True)`** were
  emitting full 32-char GUIDs for transaction, split, and lot
  fields. The compact path of `list_transactions` already
  emitted collision-safe 8-char prefixes via the cached
  `_transaction_prefix_map`; verbose didn't. Every tool that
  accepts a GUID accepts an 8+ char prefix via `_resolve_guid`,
  so the extra 24 chars per GUID was dead weight. On a 50-
  transaction verbose list with 2-3 splits each, that's ~6-8K
  chars saved per call.
- **Business-object `guid` fields** (customer, vendor, employee,
  job, billterm, taxtable, invoice, entry) — bookkeeper-
  validated as never used by any consumer. Every business
  object is addressed by its human-readable handle (ID like
  "000001" or name like "BC GST+PST 12%"). The 32-char GUID
  on every read and write response was pure overhead.
  Stripped entirely from response shapes.

The `transaction_guid` field on business write responses
(post_invoice, pay_invoice, apply_credit_note) is preserved —
it's the one business-tool surface that emits a core-
transaction GUID consumers actually use (passed to
get_transaction to verify splits). It's now emitted as a short
prefix too.

Two new cached prefix maps in `BaseGnuCashBook`
(`_split_prefix_map`, `_lot_prefix_map`) parallel the existing
`_transaction_prefix_map` — same mtime-keyed invalidation,
same collision-safety guarantees against `_resolve_guid`.

**Early-payment discounts now actually honored.**

`create_billterm` had been accepting `discount_days` and
`discount_percent` since v1.3 launched. The fields stored
correctly, `get_billterm` returned them correctly — and
`pay_invoice` ignored them entirely. A freelancer with `2/10 Net
30` terms whose customer paid $980 of $1,000 on day 5 got a
partial-payment record with $20 still outstanding instead of
the clean settlement they expected.

The pay path now supports an explicit `apply_discount=True`
mode that validates terms exist, the payment date is within the
discount window, and the shortfall matches the expected discount
on pre-tax principal (tax is collected on behalf of the
government at the gross rate and is NOT reduced — discounting
it would short the GST/PST remittance). Each failure mode
rejects with a specific error; no silent downgrades to partial.

The discount split routes via the same auto-resolver pattern as
`fx_account`: explicit `discount_account=` parameter > leaf-name
match on `INCOME`/`EXPENSE` accounts > canonical default
(`Expenses:Sales Discounts` for customer payments,
`Income:Purchase Discounts Taken` for vendor bill payments —
auto-created on first use).

`get_invoice` verbose mode now surfaces a `discount_available`
block (or `discount_expired` once the window passes) with the
eligible-until date and dollar amount, so the LLM can
proactively offer "you can save $X by paying this by Y" without
the freelancer having to ask.

**`balance_sheet` now actually balances.**

Two unrelated bugs were producing a silent A ≠ L + E identity
failure on every multi-currency book and every book with
outstanding invoices:

- **RECEIVABLE and PAYABLE were excluded from balance_sheet's
  asset/liability buckets.** Posted-but-unpaid invoices were
  invisible — Alex's $16,200 of outstanding A/R didn't appear in
  Assets at all. Both account types now sit in their natural
  buckets across `balance_sheet`, `net_worth`, and the
  `get_book_summary` trajectory anchor (cross-tool agreement on
  net worth was the calibration the bookkeeper review pattern
  catches).
- **A synthetic "Unrealized Gain/Loss" equity row.** Assets
  render at market value (factor × quantity for commodities and
  foreign-currency cash) while equity rolls up at historical-
  cost split values. The gap — investment market drift on
  STOCK/MUTUAL plus FX translation adjustment on foreign-
  currency holdings — now appears as a single signed line in the
  equity section, computed as the balancing residual. Display-
  only; no journal entry is booked, the ledger remains at cost
  exactly the way GnuCash itself stores it. Positive = unrealized
  gain; negative = unrealized loss. On Alex's book the line
  reconciles a ~$13K gap; on Lin Wei's book the FX translation
  effect is similarly absorbed.

After both fixes, A = L + E holds by construction across the
three tools that compute net worth.

**Heads-up for users tracking trajectory month-over-month:** the
net-worth number — and every historical anchor (12mo / 6mo / 3mo
/ 1mo ago) — now includes outstanding A/R minus A/P. On books
with active business activity this is a meaningful restatement
of the trajectory. Alex's "now" anchor moves from ~$189K (pre-
v1.3) to $204K because $15K of A/R that was previously
excluded now sits in the total. Historical anchors shift by
whatever A/R / A/P existed on those dates. This is the
accounting-correct number; the pre-v1.3 view was "tangible net
worth excluding outstanding business activity," which doesn't
have a standard name and disagreed with the canonical balance-
sheet identity.

**Multi-currency aggregation sweep.**

A class of bugs that v1.2.1's "FX correctness in
spending_by_category / income_by_source" fix addressed for two
reports, but left lurking in four other places that nobody
flagged until the v1.3 pre-release audit:

- **Monthly net** in `get_book_summary` was summing INCOME and
  EXPENSE splits by `split.value` raw. The docstring called this
  out as a known limitation "rare in practice for personal /
  household books"; it isn't rare for the bookkeeper's
  freelancer-with-foreign-clients case.
- **Budget headline** actuals were summing `split.quantity` raw
  across budgeted accounts. EUR-budgeted expense accounts
  contributed raw EUR quantity to used_pct comparisons against
  USD budget targets.
- **Daily expense burn** (the runway divisor) summed EXPENSE
  splits by raw value. On Lin Wei (CNY-default with USD
  subscriptions) the burn was understated by the USD spot-rate
  factor — overstating runway days.
- **Vendor spending report** summed each bill's grand_total at
  face value. A vendor list mixing USD and EUR bills produced
  per-vendor and grand totals that were the sum of numbers in
  unrelated units.

All four now route through `_account_conversion_factors` and
`_split_in_default_currency` (or latest-rate × bill total for
vendor_spending), the same pattern `spending_by_category` and
`income_by_source` already used. Three regression tests in
TestMultiCurrencyDashboardHelpers cover the helpers; the
vendor_spending fix is structurally verified.

**Dashboard refinements.**

- **Overdue counts** on the receivables and payables lines —
  *"Receivables: USD 7,420 outstanding (3 invoices, 1 overdue ⚠)"*.
  The headline number was already there; what was missing was
  whether any of it was overdue.
- **Active jobs** line when at least one job is open —
  *"Jobs: 4 active"*. Surfaces the new entity in the same place
  the LLM already looks for "what's open."
- **Foreign-currency conversion in `spending_by_category` and
  `income_by_source`.** Pre-fix, these reports summed raw split
  quantities across commodities — a USD spend and a CNY spend
  added together to a meaningless number. Now each split is
  converted to the book's default currency via the latest market
  rate (with cost-basis fallback for unpriced commodities), the
  same pattern `balance_sheet` and `net_worth` already used.

**Security — Stage 6 hardening.**

- **Book directory path redacted from routine LLM-visible
  responses.** `get_server_config` and `get_book_summary` used
  to render the full absolute path to the loaded book — every
  orientation call and every "what's loaded?" diagnostic was
  leaking the username, home directory layout, and book filename
  into the LLM transcript. Now shown as filename only. Always-on
  for the book path specifically; backup-tool responses
  (`create_backup` restore hint, `list_backups` path field) still
  carry full paths because the restore use case functionally
  needs them.
- **Path-traversal hardening on `.mcp` sidecar directories.** Backup
  and audit-log paths derive from `GNUCASH_BOOK_PATH`; the sidecar
  resolution now checks symlink targets, ownership, and world-
  writable bits before trusting an existing directory. Sticky-bit
  dirs (the `/tmp` class) get no exemption — sticky-bit prevents
  deletion, not symlink creation. The optional `GNUCASH_LOG_DIR`
  override lets containerized deployments redirect logs without
  defeating the resolution checks.
- **Path leak redaction at the MCP error boundary.** Tool error
  responses now route through `redact_paths()` so absolute paths
  on the host filesystem don't leak into error strings sent to the
  LLM. Internal logger calls keep the full paths for debugging;
  the boundary is the wire.
- **Write rate-limiting via token bucket.** Defends against a
  runaway agent loop accidentally hammering the database with
  thousands of writes per second. Disabled by default; opt-in via
  `GNUCASH_WRITE_RATE_LIMIT` (tokens-per-second, positive float)
  with `GNUCASH_WRITE_BURST` (max bucket size, default 10). Read
  tools are unaffected.

**Strict argument validation.**

`extra="forbid"` is now the default on every tool's Pydantic
argument model. Unknown kwargs — typos like `except=[...]` instead
of `except_guids=[...]`, or stale-spec parameter names from older
docs — fail loudly at the MCP boundary with `Extra inputs are not
permitted` instead of silently no-opping. Bookkeeper-found bug:
a `reconcile_account` call with the wrong exclusion parameter name
ran with no exclusion at all and only surfaced as a downstream
balance mismatch.

**Parameter normalization.**

`delete_invoice`, `delete_bill`, `delete_voucher`, and
`delete_credit_note` now accept `id` (the standard name across
`get_invoice` / `post_invoice` / `unpost_invoice` / `pay_invoice`)
in addition to the legacy `<entity>_id`. Pass exactly one; back-
compat preserved for existing callers.

**Server instructions — 39% smaller.**

The orientation block sent to MCP clients on connect went from
~2,500 chars to 1,522 (24% under the 2K cap). Same coverage
— double-entry sign convention, account ref formats, GUID
conventions, investment workflow, safety rules — denser phrasing.
Frees ~1KB of every client's context budget.

**Internal refactors (no behavior change).**

- **`CurrencyMixin` extraction.** The conversion-factor logic
  (`_account_conversion_factors`, `_latest_market_rates`,
  `_split_in_default_currency`) hoisted into a single mixin
  composed unconditionally into `BaseGnuCashBook`. Four prior
  copies of the `type='transaction'` price filter collapse to one
  call site.
- **`_compute_fx_gain_loss`** extracted from `pay_invoice`. The
  tri-currency realized-FX delta calculation is now a standalone
  helper, reusable across any future cross-currency payment path.
- **`get_book_summary` decomposition.** The 460-line monolith
  split into `_render_*` helpers per section, plus a single-pass
  materialization of `book.accounts` and `book.transactions` that
  shaves ~30% off the call's wall time on large books.
- **`QueryMixin`** consolidates indexed `.filter_by(guid=...)`
  finders that had previously been open-coded in each consumer.

**Looking ahead.** Accrual A/R revaluation (mark-to-market open
foreign-currency invoices at reporting dates) and future-dated
transaction warnings are the next correctness items on the
backlog. The bookkeeper's daily flow remains the production
signal.

**Multi-currency correctness sweep.** An adversarial multi-agent
review of the whole v1.3 surface, plus the valuation work it leaned
on — validated against the synthetic Alex (USD) and Lin Wei (CNY)
books and the bookkeeper's real-book pass.

- **`get_book_summary` FX-converts foreign-currency liabilities.**
  The dashboard balance-sheet and net-worth trajectory summed credit
  cards and loans at raw account-commodity quantity, while
  `balance_sheet` / `net_worth` converted them — so a foreign-
  denominated card or loan disagreed across surfaces. All three now
  agree to the cent.
- **`debt_payoff_plan`** values foreign-currency debt in the book
  default currency instead of summing raw foreign units.
- **`vendor_spending_report`** excludes bills it can't convert (no
  rate on file) from the default-currency totals and emits a
  per-currency warning, rather than silently mixing currencies.
- **Intermediate-currency valuation.** A holding priced only through
  a pivot currency (e.g. a fund quoted in USD inside a CNY book) now
  values via the chain, with provenance noting the derived path
  (`via USD`).
- **FX staleness cap.** Market-rate lookups exclude quotes more than
  a configurable window (`GNUCASH_FX_STALENESS_DAYS`, default 90)
  from the report date; invoice/bill post & pay raise a clear
  `StaleFXRateError` rather than posting on a stale rate (override
  with `force`).
- **Consistent future-price convention.** The rate chain pass
  applies the same "include future-dated forecasts at now-anchors"
  rule as the direct pass.
- **`income_by_source` / `spending_by_category` net contra splits.**
  Both reports dropped within-account negative splits per split (a
  realized capital loss, an expense refund), reporting *gross*
  instead of *net* — a Capital Gains source showed gains-only, a
  refunded category overstated spend. They now net signed amounts per
  account before presenting. (Account balances were always correct;
  only these two reports misstated.)

**Final adversarial pass (pre-release).** A second adversarial
review of the release candidate probed the shapes the synthetic
books can't produce — parents and placeholders with direct splits,
overpayments, contra splits against budgets — and held the release
for four fixes:

- **Net-worth surfaces agree on which splits count.** The dashboard
  skipped parent and placeholder accounts' own splits;
  `balance_sheet` skipped placeholders'; `net_worth` skipped
  neither. None of the three rolls children up, so a parent's or
  placeholder's *direct* splits are real money no other row
  represents — and the balancing-residual equity line hid the
  deletion from the sheet's own A = L + E check. All three now share
  one rule: every account contributes exactly its own splits.
- **`pay_invoice` rejects overpayments.** Nothing compared the
  payment to the remaining balance: an overpayment drove the lot
  negative and downstream `abs()` calls inverted the sign — a
  customer who overpaid by $1,000 showed as still *owing* $1,000 in
  the collections list. Overpaying now rejects with guidance to book
  the excess as a credit note; `remaining_balance`, `amount_due`,
  and job-report `outstanding` are direction-normalized (a negative
  reads as credit held by the counterparty, rendered `OVERPAID` in
  compact rows); credit notes no longer carry an aging clock in the
  verbose outstanding list.
- **Budget actuals net contra splits.** `get_budget_report` and the
  dashboard budget headline still used the per-split gross filter
  the contra-netting fix above retired for the income/spending
  reports — the budget surfaces contradicted those reports on the
  same data. Both now accumulate signed amounts.
- **FX direction label on credit-note refunds.** A cross-currency
  credit-note refund's booked FX loss was labeled `"gain"` in the
  pay response (the ledger split was always correct); the label now
  follows the same effective direction the split was booked with.

The remaining findings from the same review closed before release,
locked by a new "pathological shapes" fixture book (parents and
placeholders with direct splits, an overpaid invoice lot, a voided
transaction, a desktop-created SX template, foreign-denominated A/R,
a future-dated entry) that runs every report surface against the
combination:

- **Native SX templates filtered everywhere.** GnuCash desktop
  persists scheduled-transaction recipes as real Transaction rows
  under `root_template` plus a `template` pseudo-commodity.
  `list_transactions`, `search_transactions`, the dashboard stats /
  warnings, `list_commodities`, and the Commodities line now filter
  both via a shared `_is_template_transaction` chokepoint, and
  `delete_scheduled_transaction` removes desktop recipe rows instead
  of orphaning their splits.
- **Voided splits protected at every boundary.** `update_transaction`
  and `replace_splits` refuse voided targets (unvoid first),
  `reconcile_account` skips voided splits in bulk sweeps and refuses
  explicitly-targeted ones, and auto-fill / duplicate detection no
  longer source signals from voided history (the silent-$0-
  transaction generator). Balance surfaces exclude voided splits by
  state through a new `_own_splits_balance` chokepoint, so the
  partial-void corruption shape can't move money invisibly.
- **Future-dated transactions excluded from "now" surfaces.**
  Runway, the low-cash warning, and `debt_payoff_plan` now cap at
  today like every other "now" surface; null `post_date` rows (an
  old-book artifact) render as `(no date)` instead of raising.
- **Report totals are depth-invariant.** `spending_by_category` /
  `income_by_source` totals are the signed sum over every group;
  net-refunded categories and net-loss sources surface explicitly
  (`net_negative_netted`) instead of silently vanishing at deeper
  `depth` values. The `depth` parameter now matches its documented
  contract (1 = top-level buckets like `Expenses:Food`); the old
  off-by-one collapsed the default report to a single `Expenses
  100%` row.
- **Historical anchors never value via future rates.** The
  intermediate-currency chain pass honors the same on-or-before
  convention as the direct pass for past anchors; rate provenance
  notes now name the path that actually produced the rate.
- **Invoice settlements count as cash flow.** Lot-linked A/R / A/P
  payments are real flow (the freelancer's revenue receipt), not
  internal transfers — and the classification no longer depends on
  whether FX drift happened to add a rescue split.
- **Cross-commodity A/R relieved at the carried rate.** When the
  receivable account's commodity differs from the invoice currency,
  settlements relieve the lot at the post-date rate it was carried
  at (pro-rata for partials), so a fully settled invoice leaves no
  phantom A/R; the early-payment-discount leg's FX drift is booked
  with the payment leg's, and the discount books at the actual
  shortfall so the validator's 1-quantum tolerance can't produce an
  unbalanceable transaction.
- **Lot gains convert foreign cost basis.** `calculate_lot_gain`
  converts foreign-denominated purchase legs at their historical
  purchase rates before comparing against default-currency proceeds.
- **Smaller corrections.** `update_taxtable`'s force gate spells out
  the live-recompute blast radius; debt-plan `minimum_payment` /
  `credit_limit` slots convert from the account's commodity;
  invoice-family deletes clean their slot rows; `get_lot` marks
  voided zombie rows and the dashboard reconciliation section
  ignores them; stale docstrings corrected and a dead helper
  removed.

**Live-test signoff fixes.** The bookkeeper's post-pass validation
run surfaced two more, both fixed before the release PR:

- **Auto-fill no-match guard actually fires.** The description match
  was bidirectional substring, so an empty-description transaction
  matched *every* proposed description — a no-match auto-fill cloned
  that unrelated transaction under the caller's description (a
  phantom write) instead of raising "no match found". Blank
  descriptions now carry no match signal on either side, which also
  cleans the same false D-signal out of the stability, recent-match,
  and duplicate buckets.
- **Dashboard never ages credit notes.** The summary's warnings
  listed an unapplied credit note as past due and counted it in the
  overdue tally while `get_outstanding_invoices` correctly exempted
  it. Credit notes stay in the open counts but carry no aging clock
  anywhere.

**Data safety.** Backup retention works again under
`GNUCASH_REDACT_PATHS=1`: the pruners deleted via a redacted
basename that resolved against the working directory — a silent
no-op (unbounded backup growth) or a wrong-file delete. They now
reconstruct the real path inside the backups directory.

**Validation & hardening.**

- `update_account`'s rename path enforces the same name validation as
  `create_account` (rejects `:`, control characters, empty names) —
  it was an unguarded parallel entry point.
- Fuzzy FX / discount-account matching skips template accounts.
- Business free-text input gates short-circuit at the MCP schema
  boundary (reject before the audit log fires); transaction
  write-verification handles two splits to the same account.

**Retroactive budgets.** `create_budget` accepts a `start_date` so a
budget can be anchored to a past period for back-comparison, not
just the current year.

Under the hood: cross-currency conversion consolidated into one path
across invoice post/pay, and the deferred review items cleared.

**Tests:** 1,584 passing (was 1,114 at v1.2.1). New regression
classes cover the four Stage 3 features end-to-end, the strict-
kwargs contract, the `id` alias mutex, the FX-correct
breakdowns (now extended to monthly net, runway, budget
headline, and vendor spending), the role-aligned module groups,
the balance-sheet equation closure across simple, multi-
currency, and A/R-bearing books, the synthetic Unrealized
Gain/Loss line's presence/absence semantics, and the correctness
sweep — cross-tool agreement on foreign-currency liabilities,
vendor-report exclusion of unconvertible bills, backup pruning
under path redaction, account-name rename validation,
same-account split write-verification, and report contra-split
netting (capital-loss in income, refund in expenses). The two
synthetic test personas (Alex, Lin Wei) and the bookkeeper's
real-book validation remain the verification harness.

---

## v1.2.1 — Business module shipped, multi-currency hardened

The long-tail completion of the v1.2 business-module promise. v1.2
shipped the customer/vendor/invoice/bill scaffolding; v1.2.1 fills
in everything that *should* have been there at first ship, plus an
extensive correctness sweep on every non-USD-default path the
test books surfaced.

**Business module — what was missing in 1.2:**

- **Update operations** for customers, vendors, and employees
  (`update_customer`, `update_vendor`, `update_employee`). Pre-fix,
  a typo on an address forced you back into GnuCash itself
  because `delete_*` refused once the entity had any documents.
- **Invoice unpost** (`unpost_invoice`). Reverses a posted
  invoice/bill cleanly — deletes the posting transaction and lot,
  clears the posted-state metadata, returns the invoice to "open"
  state. `delete_transaction` now refuses to break the lifecycle
  by removing a posting record directly; it points the caller at
  `unpost_invoice` instead.
- **Cross-currency invoicing, end-to-end.** `post_invoice` and
  `pay_invoice` apply exchange rates from the price table when
  the invoice currency differs from the A/R, A/P, or payment
  account commodity. Rate drift between post-date and pay-date is
  recognized as realized FX gain/loss in a dedicated income
  account (auto-created or specified via the new `fx_account`
  parameter). Splits are written with `value` in invoice currency
  and `quantity` in the account commodity so the transaction
  balances correctly while real foreign-currency flows reflect
  the actual conversion.
- **Owner-currency inheritance.** New invoices and bills default
  to the customer's or vendor's currency, not the book's default —
  a USD vendor's bill on a CNY book is created in USD as expected.
- **Cross-sequence ID disambiguation.** When a customer invoice
  and a vendor bill share the same numeric ID (which happens —
  GnuCash auto-numbers them from independent counter sequences),
  `get_invoice` and friends now fail loud with a message naming
  both candidates by type and currency, rather than silently
  returning whichever the SQL query surfaced first.

**The dashboard becomes a work queue.**

`get_book_summary`, the LLM's standard orientation call, gained
several new sections that turn it from "what is the state of the
books" into "what do I need to do next":

- **Last entry** — *"2026-04-29 (yesterday)"* /
  *"2026-03-15 (47 days behind) ⚠"* / *"2026-05-31 (future-dated,
  31 days ahead)"*. Tells the LLM whether the books are caught
  up or whether catch-up entry comes first.
- **Net worth trajectory** — five anchors (12mo / 6mo / 3mo / 1mo
  ago, now) so trend breaks are visible without a separate report.
- **Monthly net income** — last six months, with the current
  month tagged `(MTD)`.
- **Runway** — *"121 days (USD 84,579 liquid / USD 694/day burn)"*.
  Single most actionable personal-finance number that doesn't
  appear on standard financial statements.
- **Budget headline** — *"41% used / 33% elapsed (+8% over pace)"*
  for the current period.
- **Reconciliation backlog with split counts** —
  *"Checking: 47 splits unreconciled (4 months behind, oldest:
  2025-12-30) ⚠"*. Scope, not just staleness. 12 splits is a single
  sitting; 400 is "let's narrow by month." The lag is computed from
  the OLDEST unreconciled split so "behind" tells you the planning
  number — not how long ago the last reconciliation closed.
- **Upcoming scheduled** rolled into the Scheduled line —
  *"Scheduled: 13 recurring, 3 due in next 7 days (CNY 15,650)"*.
  No second tool call needed.
- **Consolidated warnings** — past-due invoices, stale prices,
  data integrity issues, all surfaced in one section near the
  top so the LLM sees them before reading numbers that depend on
  them.

**Cross-commodity reporting correctness.**

`balance_sheet`, `net_worth`, `cash_flow`, and `get_book_summary`
all value foreign-currency holdings at `shares × latest_price`
from the price table, with cost-basis fallback when no price is
on file. Pre-fix, multi-currency holdings were summed as raw
share counts in the default currency — Alex's net worth was
understated by ~\$57K before this landed.

**Backups + integrity.**

- **Auto-backup on first write per session.** Snapshots to
  `<your-book>.gnucash.mcp/backups/` before the very first write
  of each server process. Staged retention: 7 session / 4 weekly
  / 6 monthly with grandfather-father-son rotation; manual backups
  unbounded.
- **`PRAGMA integrity_check`** verification on every snapshot
  before it's declared valid.
- **Manual tools**: `create_backup(label)`, `list_backups`,
  `prune_backups(dry_run=true)`. Restore is deliberately *not* a
  tool — see [docs/RESTORE_FROM_BACKUP.md](docs/RESTORE_FROM_BACKUP.md).

**Correctness sweep on the non-USD path.**

The CNY-default test book (Lin Wei) surfaced a class of bugs
that a USD-only test book never would have:

- `create_price` defaulted `currency="USD"` even on non-USD
  books, silently creating nonsense prices invisible to the rate
  lookup. Now defaults to the book's default currency.
- `_find_invoice` raises on cross-sequence ID collisions instead
  of silently returning the first match.
- `debt_payoff_plan` uses the standard amortization formula for
  LIABILITY accounts (mortgages, auto loans) instead of the 2%-
  of-balance heuristic that's correct for credit cards but wildly
  wrong for amortizing loans. A ¥2.7M mortgage at 3.85% APR
  asked for ¥54,590/month pre-fix vs. the actual ~¥14,800.
- `unpost_invoice` ignores voided payment splits when checking
  whether the lot has live activity (voided payments have zero
  economic effect; they shouldn't block unpost).
- `void_transaction` warns when reconciled splits get zeroed —
  the void proceeds (audit trail trumps), but the result includes
  a warning naming the affected accounts so the bookkeeper knows
  what just got broken.
- `list_lots` skips empty lots in the default view (voided buys,
  never-assigned lots, round-tripped-to-zero lots) so a portfolio
  manager doesn't see "0 shares, 0 cost basis" rows alongside
  real positions.
- `owner_type` validation across all six entrypoints — typos and
  the not-yet-supported `"employee"` value get rejected upfront
  with a clear message instead of falling through to confusing
  downstream errors.

**Tooling.**

- **Short collision-safe GUIDs** (`%xxxxxxx`) accepted everywhere
  a path is. Tools emit them in responses; tools accept them as
  input.
- **`delete_price`** rounds out price CRUD, with source-
  disambiguation when multiple prices exist on the same
  commodity+date.
- **Sample books ship with the repo.** `samples/alex-chen-morales.gnucash`
  (USD-default) and `samples/lin-wei.gnucash` (CNY-default) — try
  the server before you point it at your real books.

**Self-conducted code review.**

Before declaring v1.2.1 ready to ship, the codebase got a full
fresh-eyes review against the architecture documented in
`CLAUDE.md`. Three parallel deep-read passes produced
[specs/CODE_REVIEW.md](specs/CODE_REVIEW.md) — 3 critical, 12
high, 26 medium, 18 low findings, each tagged with file:line and
a suggested fix. The bookkeeper then validated each round of fix
PRs against Lin Wei's CNY-default book before they landed.

Closed in this release:

- **All 3 criticals.** Silent budget-amount truncation on insert
  (sub-cent input rounded to cents inconsistently between insert
  and update paths). Backup filename collision + auto-backup gate
  race (microsecond-resolution filenames, threading lock). Auto-
  backup failures swallowed silently — `get_book_summary` now
  surfaces the chain status so the bookkeeper finds out the day
  it breaks instead of weeks later.

- **All 12 highs.** Investment cost-basis precision (a $100 / 3
  share lot now reports a tax-correct cost basis on the all-
  shares case, not $99.99). Scheduling month-end drift (a Jan-31
  monthly schedule no longer locks at the 28th forever after
  February). Tri-currency FX gain/loss correctness — when book,
  invoice, and payment-account are all different commodities, the
  realized FX delta now converts to book default before booking
  to the FX account. Pay-invoice A/R-side currency conversion.
  Hardcoded 2-decimal quantize replaced with per-commodity
  precision (JPY whole-yen, BHD/KWD 3-decimals). Audit log
  before-state staging in scheduling and budget mutators.
  `update_transaction` and `replace_splits` now satisfy the
  "every write is verified" invariant. `delete_account` no
  longer returns a dangling short-GUID handle. Audit
  before-state pre-clear at wrapper entry to defend against
  cross-call leaks. `prune_backups(keep_last_n=0, manual)` now
  refuses to wipe every human-marked snapshot.

- **All 12 real-bug mediums** plus 5 polish-mediums and 11 of
  18 lows. Vendor bills render as `POST BILL` / `PAY BILL`
  in the audit log instead of mis-categorizing as INVOICE.
  Invoice/bill entries reject wrong account types upfront.
  Statement-balance comparison quantizes to commodity fraction
  (no more perpetual 0.001 mismatches). `unvoid_transaction`
  validates void-former slot completeness before mutating.
  Audit log files are written `0o600`. `_resolve_guid` uses a
  per-table dispatch dict and covers `prices` and `entries`.
  `safe_tool` routes write-verification failures to their own
  `error_type` bucket. ``set_account_slot`` rejects keys with
  embedded slashes that would silently create hierarchical
  sub-slots. ``_describe_age`` rounds to nearest unit instead
  of flooring.

The bookkeeper-loop pattern earned its keep twice during the
review itself: a careful cross-tool sanity check ("`get_prices`
finds the price but `get_latest_price` returns null") surfaced
the same `currency="USD"` default bug v1.2.1 had fixed for
`create_price` but missed for `get_latest_price`; and a
shortcut-verifier false-positive on `replace_splits` exposed a
gap in the new write-verifier where it compared raw input refs
against canonical fullnames. Both got caught and fixed during
the review window.

The full review document ships at
[specs/CODE_REVIEW.md](specs/CODE_REVIEW.md) as historical
record — including the items deferred for the v1.2.2 cycle (the
~14 perf and code-quality refactors, plus 7 cosmetic lows).

**Tests:** 1,114 passing (was 540 at v1.2.0). ~70 new regression
tests across the review work itself, each one specifically
exercising the failing scenario its fix addresses. The two
synthetic test personas (Alex, Lin Wei) plus the bookkeeper's
real-book validation form the verification harness for any
future change.

**1.3 roadmap:** taxtables, jobs, credit notes, employee expense
vouchers, plus targeted code-hygiene work — see
[specs/NEXT_STEPS_1_3.md](specs/NEXT_STEPS_1_3.md).

---

## v1.2.0 — Business module debut

Full accounts receivable and accounts payable workflow. Create
customers and vendors, generate invoices and bills, post them to
A/R or A/P, and record payments — all through natural language.

- Customers & vendors with full address support
- Billing terms (Net 30 with early-payment discounts, etc.)
- Invoices and bills with line items, posting, and partial payments
- Outstanding-invoices report and vendor-spending breakdown
- GnuCash UI compatibility — posted invoices include the metadata
  slots (`gncInvoice`, `trans-date-due`, `date-posted`) that the
  native GnuCash interface expects
- Write verification: raw SQL operations are read-back-checked
  before commit, with automatic rollback on failure
- New `business` tool module (22 tools), opt-in via `--modules`
- Server-level MCP instructions sent to clients at connection
  time so non-Claude models get accounting guidance without
  bloating individual tool descriptions

**Tests:** 540 passing.

---

## v1.1.0 — Modular tool loading

The context-efficiency release. Previous versions advertised all
tools to every client, consuming system-prompt tokens whether you
needed investments or not.

- `--modules=` flag loads only the categories you need; seven
  modules (core, reconciliation, reporting, budgets, scheduling,
  investments, admin) let you go from 52 tools down to as few
  as 15. `core` is always loaded; `--modules=all` loads everything.
- `get_server_config` debug tool reports loaded modules, tool
  count, book path, version. Loaded only when `--debug` is set.
- `GNUCASH_MCP_MODULES` env var as an alternative to the CLI
  flag — useful for Claude Desktop configs.

**Tests:** 424 passing.

---

## v1.0.2 — Compact output

Reduced token usage on the *response* side. Every read tool that
returned verbose JSON by default now returns compact one-line-
per-item text instead.

- Compact default output for `list_transactions`,
  `list_commodities`, `list_scheduled_transactions`,
  `get_unreconciled_splits`, `list_lots` — verbose JSON via
  `verbose=true` opt-in.
- `get_book_summary`: single-call financial snapshot.
- Minified JSON: stripped null/empty values from all responses.
- Partial GUID support (8+ char prefixes) for transactions,
  splits, lots, scheduled transactions.

**Tests:** 399 passing.

---

## v1.0.0 — Stable release

Feature-complete with write safety and audit trail.

- `replace_splits`: wholesale split replacement on existing
  transactions (recategorization without void/recreate).
- Transaction pipeline: duplicate detection, dry-run mode,
  auto-fill from prior transactions, date sanity checks,
  placeholder-account warnings.
- `list_accounts` compact mode: one-line-per-account default
  with `root` filter.
- Account-metadata slots: custom key-value pairs on accounts
  (APR, credit limits, reward rates).
- Audit log text format alongside JSON option.

**Tests:** 394 passing.

---

## v0.9.0 — Feature build-out

From basic CRUD to a full accounting toolkit.

- Investments: commodities, prices, lot-based cost basis
  tracking, capital gain calculation.
- Scheduled transactions: recurring templates, upcoming bills,
  one-click instantiation.
- Budgets: create, set targets by period/quarter, variance
  reporting.
- Multi-currency: cross-currency transactions with quantity/
  value split handling.
- Reporting: spending by category, income by source, balance
  sheet, net worth, cash flow.
- Reconciliation: statement reconciliation, void/unvoid with
  audit trail.
- Audit logging: automatic write-operation logging alongside
  the book file.

**Tests:** 187 passing.

---

## v0.1.0 — Initial release

- Account listing, balances, transaction CRUD, search.
- MCP server via FastMCP, Claude Desktop integration.
- piecash SQLite interface with error handling.
