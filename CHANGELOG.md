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

**Tests:** 1,044 passing (was 540 at v1.2.0). The two synthetic
test personas built up over the patch cycle now serve as the
verification harness for any future change.

**Deferred to 1.3:** taxtables, jobs, credit notes, employee
expense vouchers, plus a few cleanup items captured in
[docs/POST_1_2_1_FOLLOWUPS.md](docs/POST_1_2_1_FOLLOWUPS.md).

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
