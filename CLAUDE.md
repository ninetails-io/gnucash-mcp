# GnuCash MCP Server — Contributor Guide

AI-facing orientation for contributors (human or AI) working on this
codebase. See `README.md` for end-user usage and `CHANGELOG.md` for
the per-release history.

Local working notes specific to the maintainer live in `CLAUDE.local.md`
(not in version control).

---

## Project at a glance

An MCP server that exposes a GnuCash SQLite book to AI assistants as a
set of typed tools. Read and write transactions, run reports, manage
scheduled transactions, budgets, investment lots, and a full business
module (customers, vendors, employees, invoices, bills).

**Tech stack:**
- Python 3.10+
- [piecash](https://github.com/sdementen/piecash) — GnuCash SQLite ORM
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (`mcp[cli]`)
- SQLAlchemy under piecash; direct Core access where the ORM blocks us
- pytest for unit + integration coverage

---

## How the codebase reached its current shape

A few architectural moves shaped the present structure. The full
release-by-release history is in `CHANGELOG.md`; what follows is the
arc, not the chronicle.

- **Single-file → modular mixins.** Early versions kept everything in
  one `book.py`. As the surface grew (reconciliation, reporting,
  budgets, scheduling, investments, business), the monolith was split
  into per-area mixins composed via `build_book_class`. Tools moved
  alongside under `tools/<area>.py`. Disabling a module via
  `--modules` skips both the mixin and the tool registration cleanly.
- **Lazy load with a registry.** `TOOL_MODULES` in `server.py` is the
  single source of truth for which tools belong to which module.
  `_lazy_load_tool_module(name)` imports `tools/<name>.py` only when
  the module is enabled, then `_apply_module_filter` removes anything
  registered that isn't in `TOOL_MODULES[<name>]`. A test in
  `tests/test_modules.py::TestToolFileVsModulesMapping` locks the
  contract — it catches the bug class where a new tool gets the
  `@mcp.tool()` decoration but is forgotten in `TOOL_MODULES`.
- **Multi-currency rebuild.** Originally USD-was-everywhere assumed.
  When the first non-USD-default book showed up, a class of bugs
  surfaced where reports summed raw `split.quantity` across
  commodities, prices defaulted to USD even for currency arguments,
  and FX gain/loss from rate drift wasn't recognized. The fix
  threaded `_split_in_default_currency` (or equivalent factor map)
  through every aggregation path, made `create_price` default to the
  book's default currency, and added realized FX recognition on
  cross-currency invoice payment.
- **Dashboard as a work queue.** `get_book_summary` started as a
  status snapshot and evolved into the LLM's first-call orientation
  surface. It now surfaces net-worth trajectory, runway, monthly net
  income, budget pacing, reconciliation backlog with split counts,
  warnings, and upcoming scheduled transactions — answering "what do
  I need to do next" in one call.
- **Audit log dispatcher.** Originally a 380-line if/elif chain;
  flattened into a `(entity_type, operation) → formatter` dispatch
  table. Adding a new entity-operation pair is one row in the table
  plus a small formatter function.

---

## Architecture

### Layered design

```
server.py              FastMCP bootstrap. TOOL_MODULES registry.
                       Lazy-loads tool modules from an enabled set;
                       disabled modules never build their Pydantic
                       schemas.

tools/<area>.py        MCP tool registration. Thin wrappers that
                       validate schemas, unpack arguments (date
                       strings → date objects, etc.), call book
                       methods, format results.

book/<area>.py         Business-logic mixins composed into
                       GnuCashBook via build_book_class. One mixin
                       per subject area (core, business, budgets,
                       investments, reconciliation, reporting,
                       scheduling, admin, backup).

book/_base.py          BaseGnuCashBook. Shared helpers: open(),
                       _find_account, _resolve_account, _resolve_guid,
                       _unique_prefix, audit staging, write
                       verification, template-account filtering.

logging_config.py      Audit log + debug log. @audit_log decorator
                       wraps tool registrations; a dispatch table
                       keyed on (entity_type, operation) handles
                       formatting.

_format.py             Layer-neutral helpers (_format_number,
                       _apply_limit) used by both book and tools.
```

The mixin composition is deliberate. Each mixin owns its subject area
and can be enabled or disabled at server start via `--modules`.
`build_book_class` composes only the enabled mixins into a concrete
`GnuCashBook`, and `tools/` registration mirrors that — a disabled
module contributes zero tools to the MCP surface.

### Invariants worth preserving

- **piecash objects never cross the MCP boundary.** Book methods
  return dicts or primitives. Tool wrappers stringify for transport.
- **One book open per write.** `@audit_log` stages before-state on
  the already-open session via `threading.local`, then reads it back
  at write time. Don't add a second open for audit capture.
- **`split.value` is in transaction currency; `split.quantity` is in
  account commodity.** Reports that aggregate across commodities
  convert `quantity × latest_price` to the book's default currency.
  Both single-currency and multi-currency books work correctly.
- **GUIDs returned to MCP callers are short prefixes** (8 chars,
  extended per birthday-problem collision needs). Tools that accept
  GUIDs accept 8+ char prefixes via `_resolve_guid`. Account refs
  also accept `%xxxxxxx` short-GUID shorthand alongside path and
  full-GUID forms.
- **Cross-currency exchange rates** come from `book.prices`.
  `type='transaction'` auto-defaults created by piecash on
  cross-currency transactions are skipped — they'd shadow user-
  supplied market prices.
- **Every write is verified.** `_verify_write` /
  `_verify_composite_write` read back what was written and raise if
  the round-trip doesn't match.
- **Template accounts filtered everywhere they shouldn't appear.**
  `book.root_template` and its descendants are real Account rows in
  `book.accounts`. Any iteration that aggregates balances, surfaces
  accounts to the user, or classifies by type must filter them via
  `self._template_account_guids(book)`. `_find_account` and
  `_resolve_account` already do this; raw iterations need the filter
  added explicitly.
- **`owner_type` is validated at the entry point**, not pattern-
  matched inline. All six business tools that take it call
  `_parse_owner_type(value)`, which returns the piecash int code or
  raises with a message naming the valid options. A new owner-typed
  tool should follow the same path.

### Data model conventions

- Dates as `datetime.date` internally; ISO strings (`YYYY-MM-DD`) at
  the MCP boundary.
- Amounts as `Decimal` internally; strings at the MCP boundary.
- Account paths colon-delimited, case-sensitive
  (`Expenses:Groceries`).
- GUIDs are 32-char lowercase hex internally; tools emit short
  prefixes and accept any prefix length ≥ 8 via `_resolve_guid`.

---

## piecash gotchas

Hard-won rules. Many were invisible failures before the test coverage
existed.

- **Books must be closed after use.** Use the `open()` context
  manager; it handles the SQLite file lock with retry backoff.
- **`book.flush()` persists pending changes; `book.cancel()` reverts.**
  Don't call `flush()` mid-transaction-build — orphan `Split` objects
  lack `tx_guid` and will raise NOT NULL `IntegrityError`. Let the
  final `book.save()` flush everything together.
- **Account lookup**: use `_find_account(book, fullname)` or
  `_resolve_account(book, ref)` — don't do
  `book.accounts(fullname=name)[0]` (CallableList integer indexing
  raises a slot-assertion error).
- **Newly-created accounts**: `piecash.Account(parent=X, ...)`
  auto-registers via the parent relationship. Don't call
  `book.session.add(acct)` — redundant. And `book.accounts` fullname
  lookups won't find the new account until after flush; in tests,
  keep Python references to the objects you construct.
- **Splits**: `value` is in transaction currency, `quantity` in
  account commodity. Same-currency transactions have
  `value == quantity`. Cross-currency: value on all splits must sum
  to zero (the transaction balances in its own currency); quantities
  don't need to balance across commodities.
- **Cross-currency prices**: piecash auto-creates `type='transaction'`
  price records as effective-rate placeholders on any cross-currency
  transaction. Helpers that walk `book.prices` for market valuation
  must skip these or they'll shadow real user-supplied rates.
- **piecash `Address` is a composite, not a relationship.** It views
  the parent row's `addr_addr1`, `addr_addr2`, etc. columns directly.
  Mutating through the composite (`entity.address.addr1 = "..."`)
  doesn't persist; assigning a fresh `Address(...)` to
  `entity.address` doesn't either. Set the raw columns
  (`entity.addr_addr1 = "..."`) on update.
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
- **Attribute names vs column names**: ORM attributes sometimes
  differ from table columns. `Split.transaction_guid` (column is
  `tx_guid`); `Account.type` (column is `account_type`). `dir(Split)`
  tells you the truth.
- **Indexed queries are ~1000× faster than loops**:
  `book.session.query(X).filter_by(guid=full_guid).first()` over
  `for x in book.x:` for finders. `_find_transaction`, `_find_split`,
  etc. use the indexed form.
- **Never sum num/denom in SQL** — float precision is wrong for
  money. Fetch rows and aggregate in Python with `Decimal`.
- **Voided splits are zombies, not gone.** GnuCash's void operation
  preserves the split with zeroed values and `reconcile_state='v'`
  for audit-trail purposes. Code that asks "does this lot/account
  have any payment activity" must filter on `s.value != 0` or
  `s.reconcile_state != 'v'`, not on split presence alone.

---

## Extending the server

### Adding a new tool

The contributor checklist that the test suite enforces:

1. **Method on the mixin.** `book/<area>.py` — add a method to the
   appropriate mixin (`BusinessMixin`, `BudgetsMixin`, etc.). Return
   a dict or primitive; use `Decimal` internally, strings at the
   interface. Dates as `datetime.date` internally.
2. **Tool registration.** `tools/<area>.py` — add a function inside
   `register(mcp, get_book)` decorated with `@mcp.tool`, `@safe_tool`,
   and `@audit_log(classification="read"|"write", operation=..., entity_type=...)`.
   The wrapper unpacks the MCP schema (parses date strings, etc.)
   and calls the book method.
3. **`TOOL_MODULES` entry.** Add the new tool name to
   `TOOL_MODULES[<module>]` in `server.py`. Without this, the tool
   gets registered briefly during lazy-load, then *removed* by
   `_apply_module_filter`'s "drop anything not in keep set" pass —
   silent invisibility at runtime. The contract test
   (`TestToolFileVsModulesMapping` in `tests/test_modules.py`) fails
   loud if you skip this step.
4. **Audit log dispatch entry** (writes only). Add
   `("<entity_type>", "<OPERATION>")` to the dispatch table at the
   bottom of `logging_config.py` plus a small formatter that renders
   the before/after diff.
5. **Tests.** `tests/test_<area>.py` for the book-level method. The
   tool-level integration in `tests/test_tools.py` is mostly there
   for write-shape contracts; add only if the tool has interesting
   schema or wrapper behavior.
6. **Live test.** For write tools, exercise against a test GnuCash
   book before committing. Pause for confirmation if the change
   affects real book data.

### Cross-commodity work

When touching anything that aggregates balances or flows across
accounts of different commodities:

- Use `_split_in_default_currency(split, account, factor)` from
  `book/reporting.py` (or the equivalent `_market_value` helper in
  `book/core.py`).
- Skip `type='transaction'` prices (auto-defaults).
- Fall back to `split.value` when no market rate is on file —
  that's the transaction-currency amount, which equals cost basis
  for default-currency-denominated investment purchases and degrades
  gracefully for foreign-currency holdings without prices.

### Where the business module differs

The business-ledger objects (Customer, Vendor, Employee, Invoice,
Bill, Billterm) have piecash constructors that are blocked — you
can't just `piecash.Invoice(...)`. The create paths in
`book/business.py` use raw SQL inserts paired with `_verify_write`
round-trip checks. When extending the business module, follow that
pattern rather than trying to use the ORM constructors directly.

### Performance considerations

- **Per-write overhead**: one book open, one save. Don't add a second
  open inside `@audit_log` or any decorator — it doubles write
  latency.
- **Reports spanning all transactions**: use `_query_filtered_splits`
  in `book/reporting.py` to push date and account-type filters into
  indexed SQL. Aggregate in Python with `Decimal` (never
  `SUM(num/denom)` in SQL — float precision is wrong for money).
- **Finders**: `book.session.query(X).filter_by(guid=full_guid).first()`
  is indexed. Avoid `for x in book.x:` linear scans for finder
  patterns; the `_find_*` helpers use the indexed form. (A handful
  of business.py call sites still use linear scans — see
  `docs/POST_1_2_1_FOLLOWUPS.md`.)

---

## Testing

Three layers:

- **Unit tests** under `tests/test_*.py`, one file per mixin area.
  Fast, hermetic, use temporary books per test.
- **Tool-level integration** in `tests/test_tools.py` — exercises the
  MCP registration path (tool → book method → result serialization).
- **Persona-based integration** via `scripts/synthetic_book/*.py`.
  Phase scripts build a realistic multi-year book exercising most
  tool paths. Reporting regressions surface here before unit tests
  catch them. Two synthetic personas ship under `samples/`: Alex
  (USD-default, full feature exercise) and Lin Wei (CNY-default,
  multi-currency stress).

Run with `uv run pytest`. Per-phase synthetic-book rebuild:
`uv run python scripts/synthetic_book/phase_<N>.py` in order. Each
phase backs up the book before running.

For live verification against a personal GnuCash book, ensure
`GNUCASH_BOOK_PATH` points at a test copy, not production data.

---

## Development conventions

### Commits

- Short summary, then bullets for detail.
- Conventional-commits style prefixes (`feat(budgets):`,
  `fix(core):`).
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
- **Tool defined but not visible to clients**: it's missing from
  `TOOL_MODULES` in `server.py`. The contract test
  (`TestToolFileVsModulesMapping`) catches this.
- **MCP server not seeing code changes**: the server process
  needs a restart for tool-layer changes. Scripts that import
  `gnucash_mcp.book.GnuCashBook` directly bypass the server and
  pick up changes on next invocation.
- **Audit log entries missing fields**: the formatter for
  `(entity_type, operation)` may not be in the dispatch table.
  Falls through to a generic renderer that drops detail.
