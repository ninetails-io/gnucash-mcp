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
                       schemas. Multi-book: GNUCASH_BOOK_PATH takes
                       an os.pathsep-separated list; a module-level
                       singleton holds the CURRENT book and the
                       inline switch_book tool repoints it (visible
                       only when 2+ books are configured).

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
- **Every raw-SQL write is verified.** Two-tier contract:
  - **ORM writes** (`book.session.add(obj)`, attribute mutation,
    `book.session.delete(obj)`) rely on SQLAlchemy's commit-side
    verification — constraint violations, missing FKs, and stale-
    object failures raise during `book.save()`. Explicit
    `_verify_*` would be redundant.
  - **Raw-SQL writes** (`book.session.execute(Table.__table__.
    insert/update/delete(...))`) need explicit verification —
    SQLAlchemy executes the SQL but can't tell whether the WHERE
    clause matched any rows or the INSERT actually landed.
    `_verify_write` / `_verify_composite_write` / `_verify_delete`
    read back the affected row and raise if the round-trip doesn't
    match.
  Locked by `tests/test_contract_integrity.py::TestWriteVerificationCoverage`
  — every raw-SQL DML site in `book/*.py` must have a paired
  `_verify_*` call within 40 lines.
- **Template accounts filtered everywhere they shouldn't appear.**
  `book.root_template` and its descendants are real Account rows in
  `book.accounts`. Any iteration that aggregates balances, surfaces
  accounts to the user, or classifies by type must filter them via
  `self._template_account_guids(book)`. `_find_account` and
  `_resolve_account` already do this; raw iterations need the filter
  added explicitly.
- **Flow reports value at monthly closes; stock reports value
  as-of.** `spending_by_category` / `income_by_source` / `cash_flow`
  convert every split at its own MONTH's closing rate in single-
  period and `group_by` modes alike, so grand totals are identical
  at every granularity (locked by `TestModeAgreement`).
  `balance_sheet` / `net_worth` value holdings as of the report
  date — deliberately different semantics. Don't "fix" one to match
  the other.
- **A book switch is transactional and audited.** `switch_book`
  runs everything fallible (book construction, log activation)
  BEFORE the current-book globals move together; a failure leaves
  the server fully on the previous book, and the switch itself is
  written to BOTH books' audit trails. The unlocked current-book
  global is safe only while every tool is sync and the MCP SDK runs
  sync tools inline — `test_all_tools_are_sync` pins that
  assumption; don't add an async tool without redesigning it.
- **Backup/log state is per-book, even under a shared
  `GNUCASH_LOG_DIR`.** The override resolves to a per-book
  subdirectory (`{log_dir}/{book}.mcp`); backup state files and
  retention scoping key on the book's filename stem
  (case-insensitively — stems are validated unique at startup).
  Anything new that persists per-book state under the log dir must
  follow the same scoping or two books will share it.
- **`owner_type` is validated at the entry point**, not pattern-
  matched inline. All six business tools that take it call
  `_parse_owner_type(value)`, which returns the piecash int code or
  raises with a message naming the valid options. A new owner-typed
  tool should follow the same path.
- **Identify accounts by `GNCAccountType`, never by an English name.**
  GnuCash localizes account *names* per locale but never *types*. Code
  that keys off an English literal (`_find_account(book, "Income")`,
  `"mortgage" in fullname`, `name.startswith("Imbalance-")`) is wrong
  the moment the book is `de_DE`/`es_MX`/`zh_CN`/… Resolve top-level
  accounts by type via `_top_level_account_of_type`; where a name
  *must* be used, resolve it from the book's own data, not a hard-coded
  word. Two traps make this stricter than it looks:
  - **Two independent translation sources that disagree.** Wizard chart
    templates (`data/accounts/<locale>/*.gnucash-xea`) and the runtime
    gettext catalog (`po/<lang>.po`) don't always match — a German
    book's top-level income is the template's **"Erträge"** while the
    catalog translation of "Income" is **"Ertrag"**. So a "look up the
    localized word and match it" fix is unsafe for template-created
    accounts. `_infer_book_locale` therefore *votes* across several
    top-level type accounts rather than trusting any one. And its
    **None means undetermined, not English** — a numbered chart
    (SKR03/DATEV "Aufwendungen 2/4") matches no locale's words at
    all. Cosmetic callers (leaf naming) may fall back to English on
    None; anything that CREATES accounts must require
    `_book_reads_english`'s affirmative match instead (ruling 4(b);
    the bookkeeper's Sabine live-loop repro, 2026-09-01).
  - **Designated accounts self-heal via a KVP slot.** The FX and
    discount resolvers store the resolved account's GUID on the root
    account (`gnc-mcp/fx-gain-loss-acct`, etc.) on first use, then
    resolve by GUID forever after (`_resolve_designated_account`). This
    is locale- AND rename-proof; the leaf name becomes purely cosmetic,
    which is what makes localized created-account names (§6.3) safe. A
    stale slot falls through to the lower layers and is rewritten.

### The chokepoint pattern

The invariants above stay true because each one lives in exactly ONE
helper, with every caller routed through it. This is the codebase's
core bug-class strategy, adopted during the v1.3 review arc after the
dominant finding shape turned out to be "an invariant exists but is
enforced at only some sites." The fix for that class is never to patch
the divergent sites — it's to consolidate the rule into a single
helper, convert every site into a caller, and lock the convergence
with a test that fails when a new site skips the helper.

Established chokepoints and the rule each one owns:

- `_find_prices` — price-history access (market-price filter,
  same-date tie-break, and since #126 the per-pair memo).
- `_rates_as_of` / `_monthly_conversion_factors` /
  `_account_conversion_factors` — which FX rate a report may use
  (as-of is mandatory; flow vs. stock semantics pick the factory).
- `_resolve_account` / `_resolve_guid` — every inbound account/GUID
  ref, template-filtered, before any comparison or lookup.
- `_is_voided` / `_is_unreconciled` — split-state predicates shared
  by dashboards and detail tools so counts agree by construction.
- `_slot_bool` — tri-state boolean slot parsing (the third private
  parsing convention was the trigger to consolidate).
- `_upsert_price` — single/batch price writes can't diverge.
- `_classify_reconciliation` — dashboard aggregates and the
  drill-down table bucket rows identically.
- `_parse_owner_type`, `_commodity_quantum`, `_is_market_price`,
  `_effective_owner_type` — same story, smaller surface.

Working rules:

1. **Second duplicate is a smell; third is the trigger.** When you
   find yourself writing a rule that exists elsewhere — even in a
   slightly different private form — consolidate before extending.
2. **Fix a bug at its chokepoint, then grep for siblings.** A bug of
   the form "the check and the act disagree" almost always has
   relatives enforcing the same invariant elsewhere by hand.
3. **Lock it.** A chokepoint without a contract test is a
   convention; with one it's an invariant. See
   `TestToolFileVsModulesMapping`, `TestWriteVerificationCoverage`,
   `TestModeAgreement`, `TestShortGuidRoundTripClosure`, and the
   price-invalidation and preload SQL-count tests for the house
   styles: set-equality, grep-the-source, output-agreement, and
   count-the-queries all work.
4. **The payoff is legibility, not just correctness.** PR #126 — an
   outside contributor fixing a never-completes pathology on a
   33k-split book — was possible as a small, safe diff because every
   rate lookup already flowed through one function. Keep it that
   way: new code that bypasses a chokepoint makes the next
   contributor's change bigger than it should be.

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
- **Lot constructor is OPEN** — `Lot(title=..., account=...,
  notes=..., is_closed=0)` works directly, unlike the blocked
  business-object constructors (see "Where the business module
  differs"). `title`/`notes` are `pure_slot_property` — slot-stored,
  transparently accessed.
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
  `sqlalchemy.text()` for slot reads/deletes. For slot **writes
  and per-entity reads**, the `entity[key] = value` /
  `entity[key]` accessors work — piecash handles the polymorphism
  internally. Use `_slot_value_str(...)` from `book/_base.py` to
  extract a stable string from typed slot wrappers
  (`SlotString`, `SlotInt64`, etc.).
- **Slot key naming convention**: bare keys for universal
  financial concepts (`apr`, `credit_limit`,
  `statement_close_day`, `reward_rate`, `is_retirement`); namespaced
  `gnc-mcp/<key>` prefix for tool-specific state where collisions
  with another tool's convention are plausible (e.g.
  `gnc-mcp/applies-to-invoice` for our credit-note linkage).
  Test for which side: *could a reasonable developer arrive at
  this exact key independently?* Yes → bare. No → namespaced.
  Path-style keys (containing `/`) create hierarchical sub-slots
  in GnuCash's KVP store; that's what the namespace prefix
  exploits. The `_SLOT_KEY_RE` validator in `book/admin.py`
  gates USER input to flat keys only — internal slot keys set
  by book methods bypass that gate by design.
- **KVP_Type enum**: `SlotType` TypeDecorator expects `KVP_Type`
  enum values (e.g., `KVP_Type.KVP_TYPE_STRING`), not raw ints.
- **Detached instances**: ORM object attributes are only accessible
  while the session is open. Capture what you need inside the
  `with self.open()` block; accessing after close raises
  `DetachedInstanceError`.
- **`create_transaction()` returns a dict** with `guid` key, not a
  GUID string. `trans_date` expects a `date` object, not a string.
- **`_split_to_dict()` uses the `"value"` key** for the
  transaction-currency amount — not `"amount"`.
- **Attribute names vs column names**: ORM attributes sometimes
  differ from table columns. `Split.transaction_guid` (column is
  `tx_guid`); `Account.type` (column is `account_type`). `dir(Split)`
  tells you the truth.
- **Indexed queries are ~1000× faster than loops**:
  `book.session.query(X).filter_by(guid=full_guid).first()` over
  `for x in book.x:` for finders. `_find_transaction`, `_find_split`,
  etc. use the indexed form.
- **Never sum num/denom in SQL** — float precision is wrong for
  money. Fetch rows and aggregate in Python with `Decimal`. The
  input-side twin of this rule — floats decimalize via
  `Decimal(str(value))`, never `Decimal(float)` — was first
  demonstrated in a 2026 fork by Junaid Saeed Uppal
  ([@uppaljs](https://github.com/uppaljs)), whose
  `Decimal(22167.58) == 22167.579999...` example became
  `_to_decimal`.
- **Voided splits are zombies, not gone.** GnuCash's void operation
  preserves the split with zeroed values and `reconcile_state='v'`
  for audit-trail purposes. Code that asks "does this lot/account
  have any payment activity" must filter on `s.value != 0` or
  `s.reconcile_state != 'v'`, not on split presence alone.
- **GDATE columns are compact `YYYYMMDD` strings — don't feed them to
  `date.fromisoformat` raw.** Date slots/columns stored as GnuCash
  GDATE (e.g. `slots.gdate_val`, an invoice's `trans-date-due`) come
  back as `"20260528"`, no dashes. Python 3.11+ `date.fromisoformat`
  accepts that; **3.10 (a supported target) rejects it and raises.**
  Normalize to digits and build the date explicitly. The bite is
  worse when the caller wraps the parse in a broad `except` — a 3.10
  `ValueError` then silently drops the feature (this is exactly how
  overdue-invoice/bill warnings went dark until found).

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

- Use `_split_in_default_currency(split, account, factor)` (or the
  `_market_value` helper) from `book/_currency.py` — the
  CurrencyMixin is composed into every book class unconditionally.
- Flow reports get their factors from `_monthly_conversion_factors`
  (each split at its month's close); as-of valuations use
  `_account_conversion_factors(book, as_of)`. Pick by report kind,
  not convenience — see the flow-vs-stock invariant above.
- Skip `type='transaction'` prices (auto-defaults).
- A commodity priced only through a pivot currency values via a
  one-hop chain (provenance notes the path, e.g. `via USD`) — and
  the chain legs obey the same market-price filter as direct
  lookups; a `type='transaction'` rate inside the chain leaks
  fee-laden implied rates into valuations. Both halves of this
  rule come from Abdulla Alhosani's
  ([@alhosani-abdulla](https://github.com/alhosani-abdulla))
  report in issue #94.
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
  patterns; the `_find_*` helpers use the indexed form.

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
  catch them. Three synthetic personas ship under `samples/`: Alex
  (USD-default, full feature exercise), Lin Wei (CNY-default,
  zh_CN chart, multi-currency stress), and Sabine Brenner
  (EUR-default, German SKR03 chart — the i18n bug-class oracle).

**Migrating tests across a behavior break.** When a change closes a
creation path (e.g. the v1.5.0 currency-mismatch post refusal),
migrate the affected tests by SUBJECT, not mechanically: tests
whose subject survives the break re-route through the
correct-practice path (the FX-staleness and tax-conversion tests
moved to per-currency A/R); tests whose subject IS the
now-uncreatable historical state engineer that state byte-faithfully
via raw SQL (post through the still-open door, then flip the rows
to what warning-era books actually hold). The state outlives the
door that made it — real books carry it forever, so the guards that
protect it need tests that can still construct it.

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
- After addressing Copilot review threads (reply + fix), resolve
  them with `uv run python scripts/resolve_pr_threads.py <PR>` —
  bots don't resolve their own threads, so author-resolve keeps
  the PR conversation tab clean. `--dry-run` previews; `--all`
  resolves regardless of author for the rare case a human
  reviewer leaves threads open after agreeing in chat.

### Git safety (added 2026-08-29, paid for twice that same day)

This working tree is dirty BY DESIGN — sample-book drift is never
staged, and CLAUDE.md may carry uncommitted additions between doc
commits. The M flags become wallpaper, which is exactly when a
history operation destroys real work. Rules:

- **Never run `reset --hard`, `restore`, or `checkout` over
  modified paths without `git stash push` first** (or a verified
  clean `git status`). No exceptions for "routine" surgery — the
  two incidents were both routine.
- **Prefer constructions that never need a reset.** To move a
  commit between branches: create the new branch at the commit
  FIRST (`git branch new <sha>`), or `cherry-pick` onto a branch
  made from the right base — then remove it from the source with
  the tree stashed. To undo a commit, prefer `revert`.
- **Commit your own work the moment it exists.** Uncommitted work
  is the only kind a reset can kill. Docs drafts, spec edits,
  scratch analyses — commit them to the branch they belong to
  immediately; reword later with the tree clean.
- **Recovery, when prevention fails:** `git fsck --unreachable`
  lists staged-then-lost blobs (`git cat-file blob <sha>` recovers
  them); session transcripts may hold diff output naming blob
  hashes; macOS local APFS snapshots
  (`tmutil listlocalsnapshots /`) live on the internal disk and
  survive a dead backup target.
- **Verify pushes with `git ls-remote`**, never a piped push (the
  pipeline's exit code is the pipe's, not push's).

### Staging

- Never `git add -A` or `git add .`. Stage specific files by name.

### Committing

- Commits flow freely as work progresses; live validation happens at
  the BRANCH level, not per commit. The bookkeeper loop (live
  testing against a real server on the feature branch, with a
  written test plan and report) runs before the PR opens — the PR
  is the outcome of that loop, not the substrate for it. Changes
  that shift bookkeeper-validated report numbers additionally need
  a capture-rig before/after against the sample oracles.

### Branch workflow (gitflow)

- `main` — release branch. Only receives merges from `develop`.
- `develop` — integration branch. All feature PRs target `develop`.
- Feature branches: `feat/<name>` or `fix/<name>`, branched from
  `develop`.
- Docs-only changes can go directly to `develop`.
- Release: open PR `develop` → `main` only after tester signoff.

### Release checklist (in order)

1. **CHANGELOG entry** — the release's story for external readers,
   written before the bump so the diff review can check it against
   what actually shipped.
2. **README refresh** — version references, and feature coverage:
   a first-time visitor's click lands here, so the headline
   workflow must reflect the current release, not the one before
   it.
3. **Sample books are NOT regenerated per release** (policy since
   v1.4.2 — each committed regeneration permanently grows every
   future clone, and stable books are better byte-identity
   oracles). They ship as frozen demos; stale-price warnings and
   pending scheduled transactions accumulating between
   regenerations is expected. Regenerate
   (`scripts/synthetic_book/phase_<N>.py`, in order) only when
   phase scripts gain coverage for new features, or when
   date-decay warrants it — and treat it as a deliberate,
   capture-rig-invalidating event. For before/after report
   verification, capture against the committed books at HEAD (or
   generate locally and capture both sides same-machine).
4. Tester/bookkeeper signoff on develop.
5. **Satisfy Dependabot** — Dependabot scans only the default
   branch, so open alerts persist until a release lands; clearing
   them mid-cycle is invisible, clearing them here makes the
   release ship with a clean scan. Check
   `gh api repos/ninetails-io/gnucash-mcp/dependabot/alerts?state=open`,
   upgrade flagged packages in the lockfile
   (`uv lock --upgrade-package <name>`), and run the test suite
   against the refreshed lock. Most alerts here are transitive
   and unexploitable (stdio server, no network surface) — fix
   them anyway; the badge on a financial tool's repo costs more
   than the bump.
6. **Version bump LAST** — one commit: `pyproject.toml`,
   `__init__.py`, and a fresh `uv lock` staging `uv.lock`. The
   lockfile records the project's own version; a bump without the
   re-lock ships a lockfile that contradicts the release (v1.4.1
   did; it breaks `uv sync --locked`/`--frozen` consumers such as
   CI and bundle builds). Version numbering and timing are the
   maintainer's call.
7. Release PR `develop` → `main`; merge on the maintainer's go.
8. Annotated tag, push verified with `git ls-remote` (never trust
   a piped push).

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
- **Not sure which book a session was on**: switch_book writes
  `SWITCH BOOK` lines to BOTH books' audit trails (departure on the
  old book, arrival on the new). If those lines are absent, the
  session never switched.
- **Audit log entries missing fields**: the formatter for
  `(entity_type, operation)` may not be in the dispatch table.
  Falls through to a generic renderer that drops detail.
