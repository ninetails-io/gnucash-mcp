# GnuCash MCP Server — Contributor Guide

AI-facing orientation for contributors (human or AI) working on this
codebase. See `README.md` for end-user usage.

Local working notes specific to the maintainer live in `CLAUDE.local.md`
(not in version control).

---

## Project overview

An MCP server that exposes a GnuCash SQLite book to AI assistants as
a set of typed tools. Read and write transactions, run reports,
manage scheduled transactions, budgets, investment lots, and a full
business module (customers, vendors, employees, invoices, bills).

**Tech stack:**
- Python 3.10+
- [piecash](https://github.com/sdementen/piecash) — GnuCash SQLite ORM
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (`mcp[cli]`)
- SQLAlchemy under piecash; direct Core access where the ORM blocks us
- pytest for unit + integration coverage

---

## Architecture

### Layered design

```
server.py              FastMCP bootstrap. Lazy-loads tool modules
                       from an enabled set; disabled modules never
                       build their Pydantic schemas.

tools/<area>.py        MCP tool registration. Thin wrappers that
                       validate schemas, unpack arguments (date
                       strings → date objects, etc.), call book
                       methods, format results.

book/<area>.py         Business-logic mixins composed into
                       GnuCashBook via `build_book_class`. One mixin
                       per subject area (core, business, budgets,
                       investments, reconciliation, reporting,
                       scheduling, admin).

book/_base.py          BaseGnuCashBook. Shared helpers: open(),
                       _find_account, _resolve_guid, _unique_prefix,
                       audit staging, write verification.

logging_config.py      Audit log + debug log. @audit_log decorator
                       wraps tool registrations; a dispatch table
                       keyed on (entity_type, operation) handles
                       formatting.
```

The mixin composition is deliberate. Each mixin owns its subject area
and can be enabled or disabled at server start via `--modules`. The
`build_book_class` helper composes only the enabled mixins into a
concrete `GnuCashBook`, and `tools/` registration mirrors that — a
disabled module contributes zero tools to the MCP surface.

### Invariants worth preserving

- **piecash objects never cross the MCP boundary.** Book methods
  return dicts or primitives. Tool wrappers stringify for transport.
- **One book open per write.** `@audit_log` stages before-state on
  the already-open session via `threading.local`, then reads it back
  at write time. Don't add a second open for audit capture.
- **split.value is in transaction currency; split.quantity is in
  account commodity.** Reports that aggregate across commodities
  convert `quantity × latest_price` to the book's default currency.
  Both single-currency and multi-currency books work correctly.
- **GUIDs returned to MCP callers are short prefixes** (8 chars,
  extended per birthday-problem collision needs). Tools that accept
  GUIDs accept 8+ char prefixes via `_resolve_guid`.
- **Cross-currency exchange rates** come from `book.prices`.
  `type='transaction'` auto-defaults created by piecash on
  cross-currency transactions are skipped — they'd shadow
  user-supplied market prices.
- **Every write is verified.** `_verify_write` / `_verify_composite_write`
  read back what was written and raise if the round-trip doesn't match.

### Testing

Three layers:

- **Unit tests** under `tests/test_*.py`, one file per mixin area.
  Fast, hermetic, use temporary books per test.
- **Tool-level integration** in `tests/test_tools.py` — exercises the
  MCP registration path (tool → book method → result serialization).
- **Persona-based integration** via `scripts/synthetic_book/*.py`.
  Eight phase scripts build a realistic multi-year book (~1,800
  transactions) exercising most tool paths. Reporting regressions
  surface here before unit tests catch them.

As of v1.2.1: 724 tests passing.

---

## Development conventions

### Commits
- Short summary, then bullets for detail.
- Conventional-commits style prefixes (`feat(budgets):`, `fix(core):`).
- No attribution lines.

### Pull requests
- `## Summary` with bullet points, then `## Test plan` with checklist.
- Merge with `--merge --delete-branch` (no squash — preserves feature
  commit history under the merge commit).

### Staging
- Never `git add -A` or `git add .`. Stage specific files by name.

### Committing
- Pause for live testing before committing changes that affect real
  book data.

### Branch workflow (gitflow)
- `main` — release branch. Only receives merges from `develop`.
- `develop` — integration branch. All feature PRs target `develop`.
- Feature branches: `feat/<name>` or `fix/<name>`, branched from
  `develop`.
- Docs-only changes can go directly to `develop`.
- Release: open PR `develop` → `main` only after tester signoff.

### Testing workflow
- `uv run pytest` for the full suite.
- Synthetic book rebuild: `uv run python scripts/synthetic_book/phase_<N>.py`
  in order. Each phase backs up the book first.
- Live verification against a personal GnuCash book: ensure
  `GNUCASH_BOOK_PATH` points at a test copy, not production data.

---

## Implementation history

Release-level summary of what landed when. The README contains
user-facing changelog notes; this section names the architectural
arc.

### Phase 1–6: foundation
Project scaffolding, `GnuCashBook` wrapper, MCP server bootstrap,
initial tool set (list/get/create/search transactions,
list/get accounts, balances), MCP resources, pytest fixtures.

### Phase 7: account management
`update_account`, `move_account`, `delete_account` with
safeguards (no delete if account has transactions or children
unless force=true and a replacement account is provided).

### Phase 8: reconciliation
`set_reconcile_state`, `get_unreconciled_splits`, `reconcile_account`
with statement-balance validation.

### Phase 9: void transaction
Proper GnuCash void (not delete): keeps the transaction, marks
splits with `'v'` state, stores original values in slots for
`unvoid_transaction`.

### Phase 10: reporting suite
`spending_by_category`, `income_by_source`, `balance_sheet`,
`net_worth`, `cash_flow`. All use `_query_filtered_splits` to push
date and account-type filters into indexed SQL, then aggregate in
Python with exact `Decimal` arithmetic.

### Phases 11–13: convenience, export, import
`duplicate_transaction`, `find_duplicates`, CSV/OFX import and
export.

### Phase 14: scheduled transactions
`create_scheduled_transaction` with frequency (weekly, biweekly,
monthly, bimonthly, quarterly, yearly), instantiation via
`create_transaction_from_scheduled`. Splits stored as JSON in a
slot (`splits-json`) because piecash's Slot ORM has polymorphic
relationship conflicts — slot reads/deletes use raw SQL.

### Phase 15: budgets
`create_budget`, `set_budget_amount`, `get_budget_report` with
parent-account rollup (budgets set on a placeholder parent sum
their children's actuals; separately-budgeted children stay on
their own line to avoid double-counting).

### Phase 16: lots
Investment cost-basis tracking. `create_lot`, `assign_split_to_lot`,
`calculate_lot_gain`. Lot constructor is OPEN in piecash (most
business-module constructors are blocked, this one isn't).
`title`/`notes` are `pure_slot_property` — piecash handles slot
storage transparently.

### v1.2.0: business module
Full piecash business-ledger integration. `create_customer`,
`create_vendor`, `create_invoice` / `create_bill`,
`add_invoice_entry`, `post_invoice`, `pay_invoice`, billing terms
with discount_days/discount_percent.

### v1.2.1: backups + efficiency + cross-currency + employees
Most v1.2.x work consolidated into one release:
- **Backups**: auto-backup on first write per server session,
  grandfather-father-son retention (7 session / 4 weekly / 6
  monthly), `PRAGMA integrity_check` verification.
- **Employees**: third business-person entity alongside Customer
  and Vendor.
- **Cross-currency invoicing**: `post_invoice` and `pay_invoice`
  apply exchange rates from `book.prices` when invoice currency
  differs from A/R or payment account commodity. Realized FX
  gain/loss recognized on rate drift between post-date and pay-date,
  booked to auto-created `Income:Foreign Exchange Gain/Loss`.
- **Efficiency**: single book-open per write (audit staging via
  `threading.local`), short collision-safe GUID prefixes in write
  responses, thin write responses, SQL-pushed reporting, cumulative-
  sum `net_worth` time-series (O(splits + intervals) from
  O(intervals × splits)).
- **Display**: register-form `list_transactions(account=X)` output,
  multi-split collapse (>4 splits → top 3 + "N more"), truncation
  notices with server-side 250-item cap.
- **Market-value reporting**: `get_book_summary`, `balance_sheet`,
  `net_worth`, `cash_flow` value non-default-currency accounts at
  `shares × latest_price` with cost-basis fallback. Investment
  holdings no longer sum raw share counts as USD.
- **Refactor**: business-module DRY (shared `_create_business_person`
  and `_create_business_document` helpers), audit-log dispatcher
  flattened from a 380-line if/elif chain into a lookup table,
  structured SQL deletes (SQLAlchemy Core replacing `text("DELETE...")`).

---

## piecash gotchas

Hard-won rules accumulated across versions. Many of these were
invisible failures before the test coverage existed.

- **Books must be closed after use.** Use the `open()` context
  manager; it handles the SQLite file lock with retry backoff.
- **`book.flush()` persists pending changes**; `book.cancel()`
  reverts. Don't call `flush()` mid-transaction-build — orphan
  `Split` objects lack `tx_guid` and will raise NOT NULL
  `IntegrityError`. Let the final `book.save()` flush everything
  together.
- **Account lookup**: use `_find_account(book, fullname)` — don't
  do `book.accounts(fullname=name)[0]` (CallableList integer
  indexing raises a slot-assertion error).
- **Newly-created accounts**: `piecash.Account(parent=X, ...)`
  auto-registers via the parent relationship. Don't call
  `book.session.add(acct)` — redundant. And `book.accounts`
  fullname lookups won't find the new account until after flush;
  in tests, keep Python references to the objects you construct.
- **Splits**: `value` is in transaction currency, `quantity` in
  account commodity. Same-currency transactions have `value == quantity`.
  Cross-currency: value on all splits must sum to zero (the
  transaction balances in its own currency); quantities don't need
  to balance across commodities.
- **Cross-currency prices**: piecash auto-creates `type='transaction'`
  price records as effective-rate placeholders on any cross-currency
  transaction. Helpers that walk `book.prices` for market valuation
  must skip these or they'll shadow real user-supplied rates.
- **Slot ORM conflicts**: polymorphic relationships on the Slot
  table make direct ORM queries fail. Use raw SQL via
  `sqlalchemy.text()` for slot reads/deletes.
- **KVP_Type enum**: `SlotType` TypeDecorator expects `KVP_Type`
  enum values (e.g., `KVP_Type.KVP_TYPE_STRING`), not raw ints.
- **Detached instances**: ORM object attributes are only accessible
  while the session is open. Capture what you need inside the
  `with self.open()` block; accessing after close raises
  `DetachedInstanceError`.
- **`create_transaction()` returns a dict** with `guid` key, not a
  GUID string. `trans_date` expects a `date` object, not a string.
- **`_split_to_dict()` uses `"value"` key** for the transaction-
  currency amount, not `"amount"`.
- **Attribute names vs column names**: ORM attributes sometimes
  differ from table columns. `Split.transaction_guid` (column is
  `tx_guid`); `Account.type` (column is `account_type`). `dir(Split)`
  tells you the truth.
- **Indexed queries are ~1000× faster than loops**:
  `book.session.query(X).filter_by(guid=full_guid).first()` over
  `for x in book.x:` for finders. `_find_transaction`,
  `_find_split`, etc. use the indexed form.
- **Never sum num/denom in SQL** — float precision is wrong for
  money. Fetch rows and aggregate in Python with `Decimal`.

---

## Extending the server

### Adding a new tool

1. **Method on the mixin.** `book/<area>.py` — add a method to the
   appropriate mixin (`BusinessMixin`, `BudgetsMixin`, etc.). Return
   a dict or primitive; use `Decimal` internally, strings at the
   interface. Dates as `datetime.date` internally.
2. **Tool registration.** `tools/<area>.py` — add a function inside
   `register(mcp, get_book)` decorated with `@mcp.tool`, `@safe_tool`,
   and `@audit_log(classification="read"|"write", operation=..., entity_type=...)`.
   The wrapper unpacks the MCP schema (parses date strings, etc.)
   and calls the book method. Write tools return the book-method
   result via `_json()`; read tools may return pre-formatted text
   for compact output.
3. **Tests.** `tests/test_<area>.py` for the book-level method.
   Optionally `tests/test_tools.py` for MCP-layer integration
   (exercises the registered tool callable).
4. **Live test.** For write tools, exercise against a test
   GnuCash book before committing.

### Finding bugs with integration testing

The synthetic book in `scripts/synthetic_book/` produces a
realistic year of activity for a fictional persona. Run a relevant
subset (or the full pipeline) when shipping reporting or aggregation
features. Bugs that unit tests miss — especially cross-commodity
aggregation, hierarchical rollups, and multi-month summations —
often surface here.

Per-phase `--no-backup`, `--month N`, and `--dry-run` flags support
quick iteration cycles: `restore pre-phase-N.gnucash` → re-run
`phase_N.py` with a fix → verify via MCP tool call.

### Cross-commodity work

When touching anything that aggregates balances or flows across
accounts of different commodities:

- Use `_split_in_default_currency(split, account, factor)` from
  `book/reporting.py` (or the equivalent `_market_value` helper in
  `book/core.py`).
- Skip `type='transaction'` prices (auto-defaults).
- Fall back to `split.value` when no market rate is on file —
  that's the transaction-currency amount, which equals cost basis
  for USD-denominated investment purchases and degrades gracefully
  for foreign-currency holdings without prices.

### Audit log contract

Every write tool should emit a structured audit entry via
`@audit_log`. The text-format formatter
(`_format_audit_entry_text`) has a dispatch table keyed on
`(entity_type, operation)`. Adding a new entity/operation is a
dict entry plus a small formatter function; the formatter should
produce a readable before/after diff since human testers read the
log directly.

### Performance considerations

- **Per-write overhead**: one book open, one save. Don't add a
  second open inside `@audit_log` or any decorator — it doubles
  write latency.
- **Reports spanning all transactions**: use `_query_filtered_splits`
  in `book/reporting.py` to push date and account-type filters into
  indexed SQL. Aggregate in Python with `Decimal` (never `SUM(num/denom)`
  in SQL — float precision is wrong for money).
- **Finders**: `book.session.query(X).filter_by(guid=full_guid).first()`
  is indexed. Avoid `for x in book.x:` linear scans for finder
  patterns; the `_find_*` helpers use the indexed form.

### Where the business module differs

The business-ledger objects (Customer, Vendor, Employee, Invoice,
Bill, Billterm) have piecash constructors that are blocked — you
can't just `piecash.Invoice(...)`. Instead, the create paths in
`book/business.py` use raw SQL inserts paired with `_verify_write`
round-trip checks. When extending the business module, follow
that pattern rather than trying to use the ORM constructors
directly.

---

## When things go wrong

- **Stale SQLite lock** (piecash complains "Lock on the file"):
  check the `gnclock` table. If the holding PID isn't running
  (stale lock from a crashed process), `DELETE FROM gnclock` is
  safe.
- **DetachedInstanceError**: you're accessing an ORM attribute
  after the session closed. Capture the attribute inside the
  `with` block.
- **Balance mismatches** in cross-currency transactions: check
  whether the split `value` (transaction currency) sums to zero.
  Quantities don't need to balance across commodities; values do.
- **MCP server not seeing code changes**: the server process
  needs a restart for tool-layer changes. Scripts that import
  `gnucash_mcp.book.GnuCashBook` directly bypass the server and
  pick up changes on next invocation.
