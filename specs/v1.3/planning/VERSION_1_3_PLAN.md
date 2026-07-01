# v1.3 Plan (current)

What's left in v1.3 and the order to land it. Reflects the state of
`develop` after PRs #79–#82 and the verification pass conducted on
2026-05-23. The original plan drafted 2026-05-21 lives in this
file's git history — about half its items had already shipped or
were premise-wrong, so this rewrite is what to trust going forward.

**Discipline:** verify against current code before taking any item
here as actionable. Grep + read first. The original plan had ~half
its items inaccurate at the time of writing; don't trust the plan,
trust the code.

---

## Shipped to develop

- **PR #79** `feat/currency-mixin` — `book/_currency.py` extracted;
  `CurrencyMixin` composed unconditionally into `BaseGnuCashBook`;
  budgets getattr-fallback closed; `_market_value` /
  `_split_in_default_currency` unified.
- **PR #80** `feat/v1.3-stage-2` — `reconcile_account` bulk mode
  (`reconcile_all=True`) + shortcut acceptance + `through_date`;
  `_validate_transaction_splits` for update validation parity;
  `_safe_invoice_date` generalization; module-level imports hoist.
- **PR #81** `feat/fx-gain-loss-extraction` — `_compute_fx_gain_loss`
  extracted from `pay_invoice` with four sign-quadrant unit tests.
- **PR #82** `feat/get-book-summary-decomposition` — six `_render_*`
  helpers; `book.transactions` materialized once threaded through
  five sub-helpers; `book.accounts` materialized once threaded
  through four sub-helpers including the trajectory path.
- **This PR (`perf/stage-4-sweep`)** — `debt_payoff_plan`
  inner-loop hoist + slot materialization; transaction prefix map
  cache by book mtime; `QueryMixin` extracted into
  `BaseGnuCashBook`; `get_budget_report` SQL push.

Current test count: 1,133 passing. Zero regressions throughout.

---

## Next: module restructure

Replace the feature-based module split with a role-aligned flat
partition. Each tool belongs to exactly one module; composition by
listing (`--modules=Core,Personal`); no inheritance.

| Module | Tools | Subject |
|---|---|---|
| **Core** | 26 | The ledger, always on: account CRUD, transaction CRUD + `replace_splits` + `void_transaction` / `unvoid_transaction`, `get_balance`, `balance_sheet`, `get_book_summary`, account slots, `get_audit_log`, backups, `get_server_config` |
| **Personal** | 20 | Budgets, scheduling, reconciliation (less void/unvoid), lifestyle reports |
| **Portfolio** | 6 | Commodities + prices — the multi-currency primitive |
| **Investor** | 6 | Tax-lot management |
| **Freelancer** | 14 | Customer-facing invoicing |
| **Business** | 16 | Vendors + bills + employees + billterms (additive to Freelancer) |

**Implementation notes:**

- `void_transaction` / `unvoid_transaction` migrate from
  `reconciliation` to `Core` (coupled with `delete_transaction`).
- `get_audit_log`, the three backup tools, the three account-slot
  tools migrate to `Core`. The `admin` module dissolves; its tools
  belong with the ledger primitives.
- `investments` splits into `Portfolio` (prices/commodities) +
  `Investor` (lots).
- `business` splits into `Freelancer` + `Business`.
- Mixin layer doesn't need to match the tool partition 1:1. Add a
  `TOOL_TO_MIXIN` derivation in `_apply_module_filter` so multiple
  module names can point to the same mixin.
- `backup` no longer needs the force-load special case in
  `_apply_module_filter` — its tools are in `Core`.

**Scope:** ~80 lines in `server.py` + small mixin-loading change +
`--modules` help text + docs updates.

**Order:** before headline features and the investments-split-paired
perf item. Vouchers and the rest land in `Business` directly; the
indexed `book.prices` perf item lands in `Portfolio` directly. Doing
the restructure first avoids two rounds of code moves.

**Open question — shared-operation tool placement.** Several
business tools dispatch on `owner_type` to handle both customer
invoices AND vendor bills from a single entry point:
`post_invoice`, `unpost_invoice`, `pay_invoice`, `list_invoices`,
`get_invoice`. Steve's partition lists them in `Freelancer`, but
they also operate on bills (owner_type='vendor') — so a user with
only `--modules=Core,Freelancer` could call `pay_invoice` against
a bill if one exists in the book. The original plan flagged this
as "the wrinkle" and noted "a naive entity-based split duplicates
these or requires a shared third module." Four resolutions
worth considering:

1. **Place in Freelancer, accept the cross-module reach.** Tools
   work for both invoices and bills regardless of which module a
   given entity lives in. Side effect: Freelancer-only users can
   touch bills if they exist (created via import, prior session,
   etc.). Pragmatic; matches the partition as drafted.
2. **Place in Freelancer with runtime owner_type gating.** Reject
   `pay_invoice(owner_type='vendor')` when Business isn't loaded.
   Awkward — couples runtime behavior to module loading; the same
   tool call works or doesn't depending on `--modules`.
3. **Duplicate as `pay_invoice` (Freelancer, customer-only) +
   `pay_bill` (Business).** Owner_type dispatch becomes per-module.
   Clean separation; reverses the "shared lifecycle" design that
   the current single-entry-point shape codifies.
4. **Promote shared-lifecycle tools to a third module
   (e.g. `Documents`).** Both Freelancer and Business depend on
   it. Restores the "shared third module" path the original plan
   rejected as naive, but might be the right answer if option 1
   feels too leaky.

Don't decide here. Worth raising before the restructure starts
because the choice ripples through the partition and the mixin
layout.

---

## Headline features

Four new business surfaces, ordered per the original reasoning:

### Vouchers (first)

Employee expense reimbursement (piecash `owner_type=5`). Routes
through existing `_create_business_document` so cross-currency
comes along for free.

**Critical coupling:** ship the `_parse_owner_type` validator
update in the SAME commit. Today the validator rejects `"employee"`
explicitly (good); after vouchers it must accept it and return 5.
Different commits would mean a temporary state where vouchers exist
but the validator rejects them.

### Credit notes (second)

The accountant-grade way to reverse a posted invoice without
rewriting history. v1.2.1's `unpost_invoice` covers the simpler
case (delete the post, start fresh); credit notes post a
counter-invoice that nets against the original. Both have
legitimate use cases.

### Jobs (third)

piecash `Job` groups invoices/bills under a parent project.
`create_job(customer_or_vendor_id, name, reference)`;
`create_invoice` / `create_bill` accept optional `job_id`; new
per-job report tool surfacing total billed / paid / outstanding.

### Tax tables (last — touches the most code)

piecash `Taxtable` with rates and account routing. VAT/sales-tax
handling on invoice line items. `create_taxtable(name, rate,
account)`; `add_invoice_entry` / `add_bill_entry` accept an optional
`taxtable` parameter that splits the line into pre-tax and tax
amounts at posting time. Most-requested feature from non-US users.

---

## Accrual / per-currency

### Per-currency report segmentation

`spending_by_category` (`reporting.py:262`) and `income_by_source`
(`reporting.py:328`) still sum raw `split.quantity` — wrong when a
foreign-currency expense account exists. Thread the
`_account_conversion_factors` / `_split_in_default_currency`
pattern through both methods. Currency mixin is already composed.
~30 lines per method.

### `revalue_open_ar` (mark-to-market)

PR #81 books realized FX gain/loss at settlement (cash-basis
correct). Mark-to-market revaluation of *outstanding* A/R at
reporting dates is not handled. New tool `revalue_open_ar(as_of_date)`
walks open lots in foreign-currency A/R / A/P, computes the delta
vs their posted rate, books an adjusting transaction, records the
unwind for the next period. ~150 lines plus careful testing against
Lin Wei's CNY book.

---

## Deferred — needs design session

### `_normalize_account_refs_for_audit` shared session

The audit log's account-ref normalization opens the book a SECOND
time per write tool (the first being the tool's own session). The
original plan's "share the audit decorator's own book session"
framing turned out misleading — the audit decorator doesn't open a
book; it just reads TLS state. The actual fix paths:

1. **TLS staging from tool methods.** Add a helper that tool methods
   call from inside their open block to pre-resolve account refs
   from kwargs. Audit wrapper consumes the staged dict. ~100-150
   lines across multiple write tools. Most invasive but
   architecturally clean.
2. **Wrapper-managed book sessions.** Audit_log decorator opens
   once, passes the book to the tool method. Tool methods accept a
   `book` parameter or detect a wrapper-managed session. Big
   restructure touching every write tool.
3. **Caching + accept the double-open.** Cache ref → fullname
   resolutions in `_normalize_account_refs_for_audit` (TLS-scoped,
   invalidated on book mtime). Bounds the cost to first-use of each
   unique ref. Least invasive but doesn't truly eliminate the
   second open.

Option 3 is the pragmatic minimum; option 1 is the architecturally
clean version. Pick when fresh; don't decide at end-of-day.

---

## Security

### Path-traversal hardening

`resolve(strict=True)` collapses `..` but doesn't constrain the
resolved path to a parent directory. Audit / debug / backup dirs
derive from `book_path.parent / f"{name}.mcp"` — a symlink in the
book path could redirect log writes elsewhere. Add a
`GNUCASH_LOG_DIR` env override; when unset, validate
`book_path.parent` is writable only by current user. ~40 lines.

### Path leak redaction

Error messages surface absolute paths to MCP clients. Route all
user-visible messages through `_redact_path` helper gated on
`GNUCASH_REDACT_PATHS=1` env var. ~30 lines, ~10 call-site updates.

### Write rate-limiting

Token bucket on writes (env-configurable, default permissive).
Returns a clear error when exceeded so the LLM can back off.
Doesn't fire on reads. ~50 lines.

---

## Cosmetic / lows — verified still real

Bundle into one cleanup commit at the end of v1.3. Only the
verification-survivors are here; the original plan had 14, most
already done.

- `_format_number` `strip_trailing=True` drops the trailing dot
  (`Decimal("100.00")` → `"100"`). Either keep the dot or document.
  `_format.py:78-81`.
- `_runway_metrics` cost-basis fallback unfiltered by `as_of`.
  Defensive fix; no current symptom (API is today-only).
  `core.py:1233-1241`.
- `pay_invoice` description default `description or owner_name`
  makes empty-string indistinguishable from None. Use `description
  if description is not None else owner_name`. `business.py:2732`.
- `update_scheduled_transaction` `end_date=""` as clear-sentinel.
  Document more loudly or switch to a `clear_end_date: bool` flag.
  `scheduling.py:759`.
- `_address_to_dict` deliberately drops `addr_fax` (documented).
  Dict with `fax` round-trips lossily. Either preserve or document
  inbound rejection. `business.py:731`.

**Doc-only TODOs** (no code change, just docstrings):

- `_DEFAULT_TYPES` English-only. Hardcoded to English chart-of-
  accounts names ("Assets", "Liabilities", …). A book with a
  Spanish / German / 资产 / 負債 chart gets the redundant
  `[ASSET]` annotation on every account because the type-suppression
  dict never matches. Known limitation; no fix planned for v1.3.
  Needs a one-line docstring note on `_DEFAULT_TYPES` documenting
  the assumption and pointing toward a future localization pass.
  `_base.py` around the constant definition.
- `_strip_noise` convention. Recurses into all dicts and removes
  empty strings (correct under the project's "absent = empty"
  convention, but undocumented in the function itself). A future
  tool returning `{"field": ""}` to mean "cleared" would lose that
  signal silently. Add a docstring clarifying the convention and
  the edge case.

**Already done — no action needed** (verified 2026-05-23):
`_collect_warnings` section numbering, `_format_audit_entry_text`
debug-log on resolve failures, `set_reconcile_state` docstring,
`prune_backups would_keep` sort, `list_backups` stat-failure
logging, `get_unreconciled_splits` value-key (the field is named
`"amount"`), 30-day month constant in `_format_reconciliation_lag`.

---

## Spot-check before working

Items I couldn't definitively verify in the 2026-05-23 pass.
Grep + read before treating as actionable:

- `_extract_after_state` empty-dict behavior. `logging_config.py:1264`.
- `_decimal_to_num_denom` scientific-notation Decimal handling.
  `business.py:645-659`.
- `_invoice_to_compact_line` broad `except Exception` — confirm
  location and whether worth narrowing to
  `except (ValueError, AttributeError)`.
- `get_audit_log` size limit / `max_bytes` parameter.
  `tools/admin.py:118`.

---

## Out of scope

Kept from the original plan so future Claudes don't re-litigate
these. Each was considered and declined for the reason given.

- **Polymorphic `BusinessDocument` object model** — piecash's
  underlying schema is flat (one `invoices` table, `owner_type`
  discriminator). The `_BUSINESS_DOC_CONFIG` / `_ENTRY_CONFIG`
  dispatch tables collapse the duplication already. Object model
  would fight the ORM.
- **`Report` engine / `QueryReport` base class** — current 5-6
  reports have genuinely different aggregation shapes. A base
  class would be violated by every concrete implementation.
  Revisit after three more reports land.
- **Split `get_book_summary` into composable sub-tools** —
  pre-bundle is right while it's the bookkeeper's first-call
  orientation surface. If a second caller appears, expose data
  collectors then.
- **Declarative `@tool_descriptor` collapsing the three-decorator
  stack** — auto-derivation breaks on `post_invoice`,
  `set_account_slot`, invoice/bill polymorphism. Three decorators
  is idiomatic.
- **Invert `TOOL_MODULES` to decorator auto-registration** — the
  dict is the source of truth for `--modules` help, lazy-load
  triggers, and the contract test. Right kind of explicit.

---

## Suggested ordering

1. **Module restructure** — foundational; everything downstream
   lands in its final home.
2. **Indexed `book.prices` queries** — pairs naturally with the
   `Portfolio` module created in step 1.
3. **Headline business features** (vouchers → credit notes → jobs
   → tax tables).
4. **Per-currency report segmentation** + **`revalue_open_ar`** —
   both want module restructure done first.
5. **Deferred audit normalization** — pick design option when
   fresh.
6. **Security** — three items, one PR.
7. **Cosmetic / lows bundle** — single cleanup commit at the end.

---

## Working with this file

- New work: add an entry above with scope, plan, and an estimate.
- Item ships: move to the Shipped section with the PR ref, delete
  the body.
- Item declined: leave here with a one-line dated note.
- Bookkeeper review loop is the production signal. Findings get
  filed here.
- **Verification discipline:** before working any item, grep + read
  the relevant code. Don't trust this file as authoritative; trust
  the code.
- **Historical:** the original v1.3 plan as drafted 2026-05-21
  lives in git history. Find with:
  `git log --diff-filter=M -- specs/VERSION_1_3_PLAN.md` then
  `git show <commit>:specs/VERSION_1_3_PLAN.md`.
