# Version 1.3 Plan

**This document supersedes `specs/NEXT_STEPS_1_3.md` and the open
items in `specs/CODE_REVIEW.md`.** It is the single source of truth
for v1.3 scope.

The shape of v1.3 is: a half-and-half release. The new-feature half
fills out the business module to accountant-grade (tax tables, jobs,
credit notes, employee expense vouchers). The hygiene half clears
the deferred-medium and deferred-low backlog from v1.2.1's
self-conducted review plus the additional architectural smells
surfaced during the v1.2.1-retrospective pass.

The release naming question — whether the headline business
features alone are enough for v1.3, with hygiene held for v1.3.x —
is deferred until the headline features have shipped to a feature
branch and we can see their actual size.

---

## Shipped to develop

These items have landed on `develop` but `main` is unchanged at
v1.2.1. Versioning + release-to-main is deferred.

- **PR #79** `feat/currency-mixin` — `book/_currency.py` extracted;
  `CurrencyMixin` composed unconditionally into `BaseGnuCashBook`;
  budgets `getattr`-fallback hole closed; `_market_value` and
  `_split_in_default_currency` co-located. Folded in: the
  "Currency conversion as a cross-cutting concern" architecture
  item and the "Unify `_market_value` and
  `_split_in_default_currency`" hygiene item.
- **PR #80** `feat/v1.3-stage-2` — `reconcile_account` bulk mode
  (`reconcile_all=True`), shortcut acceptance, `through_date`
  filter; `_validate_transaction_splits` extracted for
  `update_transaction` validation parity; `_safe_invoice_date`
  generalization covering both `date_posted` and `date_opened`;
  module-level imports hoisted (Price, relativedelta,
  sqlalchemy.text).
- **PR #81** `feat/fx-gain-loss-extraction` — `_compute_fx_gain_loss`
  pulled from `pay_invoice` into a sibling helper on
  `BusinessMixin`; four sign-quadrant unit tests added;
  bookkeeper-verified byte-identical.
- **PR #82** `feat/get-book-summary-decomposition` — six
  `_render_*` helpers extracted; `book.transactions` materialized
  once and threaded through five sub-helpers;
  `book.accounts` materialized once and threaded through four
  sub-helpers including the trajectory path (up to 10 ORM sweeps
  per call → zero). The plan's "single-pass `_collect_warnings`"
  item was already done in v1.2.1's commit `5073b60`; stale plan
  content, no PR needed.

Test count: 1,116 (pre-shipped) → 1,130 (post-PR #82). Zero
regressions throughout.

---

## Headline features

### Tax tables

piecash supports `Taxtable` with rates and account routing. Required
for VAT/sales-tax handling on invoice line items. The expected shape:

- `create_taxtable(name, rate, account)` — define a rate that posts
  the tax portion to a specified liability account.
- `add_invoice_entry` and `add_bill_entry` accept an optional
  `taxtable` parameter that splits the line into pre-tax and tax
  amounts at posting time.
- Existing invoices/bills without a taxtable continue to behave
  exactly as today (it's an additive parameter).

Most-requested feature from non-US users where invoicing without
VAT/GST is non-compliant.

### Jobs

piecash `Job` groups invoices and bills under a parent project or
contract. Used for per-project billing summaries.

- `create_job(customer_or_vendor_id, name, reference)` — define a
  job tied to one entity.
- `create_invoice` / `create_bill` accept an optional `job_id`.
- New per-job report tool surfaces total billed, paid, and
  outstanding for each job.

### Credit notes

The conventional accountant-facing way to reverse a posted invoice.
v1.2.1's `unpost_invoice` covers the simpler case (delete the post
and start fresh); credit notes preserve the original invoice and
post a counter-invoice that nets against it. Both have legitimate
use cases — accountants generally prefer credit notes for any
month-closed invoice since unposting rewrites history.

### Employee expense vouchers

piecash `owner_type=5`. The third document type in the business
module after invoices (customer-facing) and bills (vendor-facing).
Vouchers track employee expense reimbursement. Today rejected at
the entry point with a clear "not yet supported" message; the 1.3
implementation routes through the existing
`_create_business_document` helper so cross-currency support comes
along for free.

- **Couples with the `employee` owner_type validation item under
  Tool UX below.** Landing vouchers without updating the validator
  recreates the same confusing-error bug in reverse: today
  `owner_type="employee"` is rejected silently; post-vouchers it
  needs to be *accepted*. The validator's allowed-list must expand
  to `["customer", "vendor", "employee"]` in the same commit that
  enables vouchers, otherwise the next bookkeeper session
  encounters "employee" being silently dropped on the way *in*
  instead of *out*.

---

## Architecture

### Module restructure: role-aligned flat partition

The original modularization (v1.1) carved the tool surface into
nine modules at ~54 tools; v1.2.1 grew to 88. The current split
groups by subject area (core, reconciliation, reporting, budgets,
scheduling, investments, business, admin, backup), with `core`
specially-cased as always-loaded plus `backup` force-added in
`_apply_module_filter`. That worked at 54 tools; at 88 it's
showing strain. The business module alone is ~30 tools — a third
of the surface behind one flag — and headline-feature work
(vouchers, credit notes, jobs, tax tables) pushes past 35. Other
modules have grown asymmetric coupling: a multi-currency user
needs the price tools (in `investments`) but not the lot tools;
a household bookkeeper needs `void_transaction` (in
`reconciliation`) because `delete_transaction` (in `core`) refuses
posted-document transactions and points the caller at `void`.

**Proposed shape: flat partition, role-named, composable.**

Each tool belongs to exactly one module. No inheritance, no
overlaps. The user composes by listing: `--modules=Core,Personal`
for a typical personal-finance user; `--modules=Core,Freelancer`
for a solo invoicer; `--modules=Core,Personal,Portfolio,Investor`
for an active investor. `all` stays as the catch-all. Module names
are role-anchored rather than feature-anchored — the user picks
what fits their identity rather than mapping features to needs.

| Module | Tools | Subject |
|---|---|---|
| **Core** | 26 | The ledger, always on: account CRUD, transaction CRUD + `replace_splits` + `void_transaction`/`unvoid_transaction`, `get_balance`, `balance_sheet`, `get_book_summary`, account slots, `get_audit_log`, backups, `get_server_config` |
| **Personal** | 20 | Budgets, scheduling, reconciliation, lifestyle reports (`net_worth`, `spending_by_category`, `income_by_source`, `cash_flow`, `debt_payoff_plan`) |
| **Portfolio** | 6 | Commodities + prices: `create_commodity`, `list_commodities`, `create_price`, `delete_price`, `get_prices`, `get_latest_price`. The multi-currency primitive — needed independently of investment-tracking |
| **Investor** | 6 | Tax-lot management: `create_lot`, `close_lot`, `get_lot`, `list_lots`, `assign_split_to_lot`, `calculate_lot_gain` |
| **Freelancer** | 14 | Customer-facing invoicing: customer CRUD, invoice lifecycle, `get_outstanding_invoices` |
| **Business** | 16 | Full business module, additive to Freelancer: vendor CRUD, bill lifecycle, employee CRUD, billterms, `vendor_spending_report` |

Total: 26 + 20 + 6 + 6 + 14 + 16 = 88 tools today. The Business
column will grow when vouchers / credit notes / jobs / tax tables
ship (Headline features).

**Design properties:**

- **Flat partition.** Each tool in exactly one module. No
  inheritance hierarchy; no module silently includes another. The
  user always sees the full set they enabled.
- **Role-anchored naming.** "Freelancer" and "Business" map to
  who the user IS, not what features they happen to want. A solo
  consultant identifies as Freelancer naturally; adding vendors
  and employees graduates them to Business.
- **Composition by listing.** `--modules=Core,Personal,Portfolio`
  is unambiguous and explicit. No magic expansion of one name
  into a chain of others. The `all` shortcut remains for
  power-users.
- **Always-on `Core`.** Per the original design, the smallest
  meaningful set is loaded by default. `Core` is composed onto
  every invocation unless explicitly listed in `--modules`.
- **`backup` and the audit log are inside `Core`.** Today
  `backup` is force-loaded in `_apply_module_filter` (special-
  case); after this restructure that special case disappears
  because the backup tools are part of `Core`. Same for the audit
  log reader.

**Implementation notes:**

- The mixin layer doesn't need to match the tool partition 1:1.
  Today nine mixins map 1:1 to nine modules. After the
  restructure, a single mixin can host tools that span multiple
  modules (e.g., `BalanceSheetMixin`-like tools currently in
  `ReportingMixin` are split across `Core` and `Personal`). The
  pragmatic move is: keep mixins where they are; let
  `_apply_module_filter` derive which mixins to load by union
  over the tools' host mixins.
- A `TOOL_TO_MIXIN` dict (or a reverse index built from
  `TOOL_MODULES`) drives the mixin composition. Modules become
  pure tool-bucketing; mixins stay an internal data-layer
  concern.
- `void_transaction` / `unvoid_transaction` migrate from
  `reconciliation` to `Core` — they're already tightly coupled
  with `delete_transaction` (which redirects to them). This is
  one of the items that motivated the restructure.
- `get_audit_log`, `create_backup`, `list_backups`,
  `prune_backups`, and the three account-slot tools migrate from
  `admin` / `backup` to `Core`. The `admin` module dissolves —
  its tools belong with the ledger primitives.
- The `business` module dissolves into `Freelancer` + `Business`.
  Vendors/employees/billterms move to `Business`; customers and
  the customer-invoice path stay in `Freelancer`. Shared lifecycle
  tools (`post_invoice`, `pay_invoice`, etc. with `owner_type`
  dispatch) live in whichever module owns the dominant entity —
  Freelancer for the customer path, Business adds the vendor
  path's bills as a sibling lifecycle.

**Wins:**

- The typical personal-finance user picks `Core + Personal` (46
  tools) and skips 42 they'll never touch.
- A multi-currency household bookkeeper picks `Core + Personal +
  Portfolio` (52 tools) and gets price management without lots
  or commodities-as-investments noise.
- A solo invoicer picks `Core + Freelancer` (40 tools) — no
  budgets, no vendor management.
- The `--modules` flag becomes a meaningful UX surface again,
  not a list of internal module names the user has to translate
  from their actual workflow.

**Order:** do this BEFORE the headline features and the
investments split. Vouchers and the rest land in `Business`
directly; the indexed `book.prices` perf-sweep item lands in
`Portfolio` directly. Doing the restructure first avoids two
rounds of code moves.

**Estimated scope:** ~80 lines in `server.py` (TOOL_MODULES
rebucket, group recognition, derived mixin-loading), ~20 lines
moving `void_transaction` / `unvoid_transaction` to `CoreMixin`,
plus `--modules` help-text and docs updates. The mixin layer
stays put.

### Currency conversion as a cross-cutting concern

**Shipped in PR #79.** See "Shipped to develop" above.

---

## Code hygiene

The following items were on this list and have shipped to
`develop` — see "Shipped to develop" above for PR refs:

- Unify `_market_value` and `_split_in_default_currency` (PR #79)
- Extract `_compute_fx_gain_loss` from `pay_invoice` (PR #81)
- Harden `update_transaction` validation parity with
  `create_transaction` (PR #80)
- Extend `_safe_date_posted` to `date_opened` (PR #80)
- Section-render extraction in `get_book_summary` (PR #82)

Remaining items below.

### Indexed `book.prices` queries in investments + business

`create_price`, `get_latest_price`, `get_prices`, `calculate_lot_gain`
all walk `book.prices` linearly (5+ call sites in `book/investments.py`).
`_find_exchange_rate` in `book/business.py` rebuilds per cross-currency
entry inside `post_invoice`.

**Plan:** introduce `_find_prices(book, commodity_guid=None,
currency_guid=None, market_only=True)` in the new
`book/_currency.py` that issues an indexed
`book.session.query(Price).filter_by(...).order_by(Price.date.desc())`
query, paired with caching for the
once-per-report rate-map case. Replace all linear walks.

**Estimated scope:** ~60 lines, 6-8 call-site swaps.

### Push `get_budget_report` filtering to SQL

`book/budgets.py:811` walks `book.transactions` with a Python
date-compare loop. Reporting's `_query_filtered_splits` already
encapsulates the SQL-pushed equivalent. Convert.

**Estimated scope:** ~30 lines, one method body. Becomes free
once the currency-mixin work makes `_query_filtered_splits`
accessible from budgets without the implicit reporting
dependency.

### Cache `_guid_prefix_map` by book mtime

`list_transactions` and `search_transactions` rebuild the full-table
prefix map on every call. The map is correct as long as the book
hasn't been mutated since last build; gate on `book_path.stat().st_mtime_ns`.

**Estimated scope:** ~20 lines, plus a small cache-invalidation
test.

### `debt_payoff_plan` inner-loop hoisting + slot materialization

Recomputes per-debt monthly rate inside the 1200-iter amortization
loop. Each `account[key]` slot access goes through the
polymorphic-broken slot path. Hoist rate computation to one
per-debt; materialize `account.slots` into a dict once per
account.

**Estimated scope:** ~30 lines, no behavior change. `book/reporting.py`
`debt_payoff_plan`.

### `_normalize_account_refs_for_audit` book-open

`logging_config.py:912` opens a read-only book per audit emission,
walking back the single-open-per-write win from v1.2.1. Plan:
share the audit decorator's own book session with the formatter
when one's available; fall back to opening only when the formatter
fires outside a tool context (the rare path).

**Estimated scope:** ~40 lines, careful threading.

### `_format_audit_entry_text` silent `_resolve_account` failures

Currently swallows silently for log-render robustness. Add a
debug-log entry ("could not resolve ref X in audit") so post-hoc
investigation is possible. `logging_config.py:923-930`.

**Estimated scope:** 5 lines.

### Budgets' duplicated `_fmt` local function

`_format_budget_report_compact` in budgets defines a local `_fmt`
that duplicates `_format_number` logic with different rounding,
and renders percents as a different format string than
reporting's breakdowns. Route through `_format_number` for
consistency.

**Estimated scope:** ~20 lines.

### Misleading section comment numbering in `_collect_warnings`

Sections labeled `1, 2, 3, 5, 4` because operational urgency
reorders them. Either renumber or add a leading comment explaining
the deliberate disorder. `book/core.py:577-893`.

**Estimated scope:** 2 minutes.

### `_DEFAULT_TYPES` English-only

Hardcoded to English chart-of-accounts names ("Assets",
"Liabilities", …). A book with a Spanish or German chart gets the
redundant `[ASSET]` annotation on every account. Acceptable today;
flag for a future localization pass. **No fix in v1.3.** Listed
here as a known limitation that gets a one-line docstring note.

### `_strip_noise` documentation

Recurses into all dicts and removes empty strings (correct under
the project's "absent = empty" convention, but undocumented).
Add a docstring clarifying the convention and the edge case (a
future tool returning `{"field": ""}` to mean "cleared" would
lose that signal).

**Estimated scope:** doc-only.

---

## Tool UX — production findings

Surfaced during the April–May 2026 production accounting sessions
through the bookkeeper review loop. Each item has real friction
behind it, not architectural speculation.

The following items have shipped to `develop`:

- `reconcile_account` bulk mode (`reconcile_all=True`) — PR #80
- `reconcile_account` accepts account shortcuts — PR #80

Plus a bookkeeper-flagged refinement during the same PR:
`reconcile_all=True` should reconcile **every** unreconciled split
by default, not silently filter by `statement_date`. Optional
`through_date` parameter for explicit date-bounding. (Test:
`test_reconcile_all_no_default_date_filter`.)

Remaining items below.

### `owner_type` validation rejects invalid values explicitly

**Problem:** `unpost_invoice(id="000001", owner_type="employee")`
silently ignores the invalid `owner_type`, falls through to the
unfiltered lookup, and returns a confusing disambiguation error
between customer invoice and vendor bill. The error redirects the
LLM to valid options but never mentions that `"employee"` itself
was the invalid input — wasted round-trips while the LLM tries to
figure out what went wrong.

**Plan:** validate `owner_type` against the allowed set at the
entry point of `unpost_invoice`, `post_invoice`, `pay_invoice`,
and every other tool that accepts it. Reject with:
`"Invalid owner_type 'employee'. Must be 'customer' or 'vendor'."`

The allowed set is `["customer", "vendor"]` today,
`["customer", "vendor", "employee"]` once vouchers ship — see the
sub-bullet on the Employee expense vouchers headline feature.
The validator change must land in the same commit as vouchers,
not after.

**Estimated scope:** one validation check per tool entry point.
~10 lines total. ~15 if shared via a `_VALID_OWNER_TYPES` constant
+ a small `_validate_owner_type` helper (the cleaner shape if
vouchers extend the list).

---

## Reporting / accrual-basis

### Per-currency report segmentation

`spending_by_category` and `income_by_source` still use raw
`split.quantity` sums — fine when income/expense accounts are all
in the book default currency (the common case), wrong when a
foreign-currency expense account exists. v1.2.1 fixed this for
`balance_sheet`, `net_worth`, `cash_flow`, `get_book_summary`.

**Plan:** thread the same factor pattern through both methods.
Once `book/_currency.py` lands, this is a 30-line addition per
method. Optionally add a `currency` parameter for explicit
per-currency breakdowns.

**Estimated scope:** ~80 lines including tests.

### FX gain/loss: realized vs mark-to-market

v1.2.1's `pay_invoice` books realized gain/loss at settlement
(cash-basis correct). Mark-to-market revaluation of *outstanding*
A/R at reporting dates is not handled. For accrual-basis reporting
with material foreign-currency A/R, periodic revaluation books the
delta to FX Gain/Loss; the next period unwinds the previous
adjustment.

**Plan:** new tool `revalue_open_ar(as_of_date)` that walks open
lots in foreign-currency A/R / A/P, computes the delta vs their
posted rate, books an adjusting transaction, and records the
unwind for the next period.

**Estimated scope:** ~150 lines, plus careful testing against
Lin Wei's CNY book.

---

## Security

### Path-traversal hardening

`resolve(strict=True)` was added but only collapses `..`; doesn't
constrain the resolved path to a parent directory. Audit / debug
/ backup dirs are still derived as `book_path.parent /
f"{name}.mcp"`. A symlink in the book path could redirect log
writes elsewhere.

**Plan:** add a `GNUCASH_LOG_DIR` env var that, when set,
overrides the parent-of-book-path derivation. When unset, validate
that `book_path.parent` is writable by the current user only
(stat the parent dir, check no group/world write bits). Useful
for multi-tenant deployments; harmless for single-user.

**Estimated scope:** ~40 lines plus tests.

### Sanitize path leaks in error messages

`FileNotFoundError(f"GnuCash book not found: {book_path}")` and
similar surface absolute paths to MCP clients. Acceptable for
single-user local config, not multi-tenant.

**Plan:** route all user-visible error messages through a
`_redact_path` helper that strips to the basename when a new
`GNUCASH_REDACT_PATHS=1` env var is set.

**Estimated scope:** ~30 lines, ~10 call-site updates.

### Write rate-limiting

A misbehaving LLM can call `create_transaction` in a tight loop.
Auto-backup gates on first write of session; SQLite locking is
the only other safeguard.

**Plan:** simple token-bucket on writes (env-configurable, default
permissive). Returns a clear error when exceeded so the LLM can
back off. Doesn't fire on reads.

**Estimated scope:** ~50 lines.

---

## Cosmetic / lows

These are real but small. Bundle into one cleanup commit after the
substantive work lands.

- **30-day month constant** in reconciliation lag — render 91 days
  as "3 months" same as 90. Fix the heuristic.
  `book/core.py:1322-1324`.
- **`_runway_metrics` cost-basis fallback unfiltered by `as_of`** —
  today-only API today, so no symptom; defensive fix. `book/core.py:1161-1164`.
- **`_format_number` strips trailing dot** — `Decimal("100.00")` with
  `strip_trailing=True` becomes `"100"`. Either keep the dot or
  document the strip in the docstring. `_format.py:78-81`.
- **`_extract_after_state` returns `None` for empty dicts** — catch-all
  swallows whatever's in the JSON body; document or tighten.
  `logging_config.py:1059`.
- **`get_audit_log` reads file without size limit** — daily growth on
  a long-lived deployment could yield multi-MB reads. Add a
  default `max_bytes` and tail-the-file option. `tools/admin.py:118`.
- **`set_reconcile_state` docstring lie** — says `reconcile_date`
  required for state `'y'` but implementation defaults to now.
  Fix the docstring. `book/reconciliation.py:64-65,90-95`.
- **`get_unreconciled_splits` "value"-key naming lie** — the key
  contains `split.quantity` (account commodity), not `split.value`
  (transaction currency). Diverges on multi-currency books.
  Predecessor 4.6 flagged this; 4.7 acknowledged. v1.3 deprecation
  cycle: add `"quantity"` key alongside `"value"`, document
  `"value"` as deprecated. Drop `"value"` in 1.4. Wire-breaking
  changes deserve a deprecation cycle.
- **`_decimal_to_num_denom` doesn't handle scientific-notation
  Decimals with very large exponents** — practically unreachable
  for entry quantities. Document the assumption.
  `book/business.py:645-659`.
- **`_invoice_to_compact_line` overly broad `except Exception`** —
  tighten to `except (ValueError, AttributeError)`. `book/business.py:743-744`.
- **`_address_to_dict` deliberately drops `addr_fax`** — documented
  ("it's 2026"), but a dict with `fax` round-trips lossily. Either
  preserve or document inbound rejection. `book/business.py:543-561`.
- **`pay_invoice` description default `description or owner_name`
  makes empty-string impossible** — `description if description is
  not None else owner_name` is more honest. `book/business.py:2599`.
- **`update_scheduled_transaction` uses `end_date=""` as clear
  sentinel** — documented but unusual. Document more loudly or
  switch to a `clear_end_date: bool` flag. `book/scheduling.py:647-651`.
- **`prune_backups` `would_keep` not sorted by stage-then-timestamp** —
  cosmetic. `book/backup.py:432-433`.
- **`list_backups` silently drops files whose `stat()` fails** —
  broken symlinks invisible; `prune_backups` never cleans them.
  Add a debug-log warning. `book/backup.py:356-359`.

---

## Out of scope (deliberately not in v1.3)

Listed so this file is the answer to "did we consider X?" — yes,
and the answer was no. Notes on the reasoning, in case v1.4
revisits.

- **Polymorphic `BusinessDocument` object model** — Invoice / Bill /
  Voucher as subclasses of a base. piecash's underlying schema is
  flat (one `invoices` table, `owner_type` discriminator). The
  current `_BUSINESS_DOC_CONFIG` / `_ENTRY_CONFIG` dispatch tables
  collapse the duplication already. Adding vouchers is two config
  rows, not three new classes. Object model would fight the ORM.
- **`Report` engine / `QueryReport` base class** — current 5–6
  reports have genuinely different aggregation shapes (net_worth
  cumsum trick, cash_flow inflow/outflow split, balance_sheet
  commodity conversion). A base class would be violated by every
  concrete implementation. Revisit after three more reports land.
- **Split `get_book_summary` into composable sub-tools** — pre-bundle
  is the right call while it's the bookkeeper's first-call
  orientation surface. If a second caller (dashboard UI, different
  agent) appears, expose data collectors as sub-tools then; keep
  `get_book_summary` as the convenience wrapper that composes them.
  Don't fragment first.
- **Declarative `@tool_descriptor` collapsing the three-decorator
  stack** — auto-derivation breaks on `post_invoice` (entity=invoice,
  op=post), `set_account_slot` (entity=account_slot, op=set),
  invoice/bill polymorphism. Three decorators is idiomatic Python;
  meta-decorating loses clarity and test isolation.
- **Invert `TOOL_MODULES` to decorator auto-registration** — the
  dict is the source of truth for `--modules` help text, lazy-load
  triggers, and the contract test. Right kind of explicit.

---

## Suggested ordering

What's left, in the order I'd land it. Done items are in "Shipped
to develop" above.

1. **Module restructure** (Architecture) — flat partition into
   `Core` / `Personal` / `Portfolio` / `Investor` / `Freelancer` /
   `Business`. Foundational; everything downstream lands in its
   final home. ~80 lines in `server.py` plus a small mixin-
   loading derivation. Documented as a single PR.
2. **Indexed `book.prices` queries** (Code hygiene) — naturally
   pairs with the `Portfolio` module created in step 1. Same
   code, one PR.
3. **Headline business features** — vouchers first (ships the
   `owner_type` validator update in the same commit per Tool UX);
   credit notes second; jobs third; tax tables last (touches the
   most code). Each as its own PR; all land in the `Business`
   module post-restructure.
4. **Reporting / accrual** — `revalue_open_ar` for mark-to-market
   on outstanding A/R, plus per-currency segmentation for
   `spending_by_category` and `income_by_source` (both will
   benefit from the currency mixin that landed in #79).
5. **Remaining performance sweep** — `_guid_prefix_map` mtime
   cache, `get_budget_report` SQL push, `debt_payoff_plan`
   inner-loop hoist, `_normalize_account_refs_for_audit` session
   sharing. Small focused commits, bundle 2-3 per PR.
6. **Security** — path-traversal hardening, error-message
   redaction, write rate-limiting. Independent; can land anytime
   but bundle as one PR.
7. **Cosmetic / lows** — single bundled cleanup commit at the
   end. The list in the next section is the canonical bundle.

The original plan called for a separate "split the business
module into sub-modules" step at the end; that's now folded into
step 1 as the `Freelancer` / `Business` partition.

---

## Working with this file

- New 1.3 work: add an entry above with scope, plan, and an
  estimate.
- Item lands: move the entry to `CHANGELOG.md` under v1.3, delete
  from here.
- Item slips to 1.4: leave here, add a one-line dated note
  ("deferred to 1.4 because X").
- Item declined: leave here, add a one-line dated note
  ("declined — reason"). Don't delete; the trail helps the next
  reader.
- Bookkeeper review loop is the production signal. If a bookkeeper
  finding maps to a planned item here, link it. If it's new,
  add it.
- Reference original sources only when a future reader needs the
  longer history: `specs/CODE_REVIEW.md` (the v1.2.1 self-review
  in full, kept as historical record), `specs/NEXT_STEPS_1_3.md`
  (the pre-retrospective v1.3 backlog, superseded by this file).
