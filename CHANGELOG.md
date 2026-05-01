# Changelog

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
  *"Checking: 47 splits unreconciled since 2025-12-30 (4 months
  behind) ⚠"*. Scope, not just staleness. 12 splits is a single
  sitting; 400 is "let's narrow by month."
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
