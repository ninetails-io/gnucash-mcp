# GnuCash MCP Server — Contributor Guide

AI-facing orientation for contributors (human or AI) working on this
codebase. See `README.md` for end-user usage and `CHANGELOG.md` for
the per-release history.

Local working notes specific to the maintainer live in `CLAUDE.local.md`
(not in version control).

---

## Project at a glance

An MCP server that exposes a GnuCash book — a SQLite file, or a
database GnuCash keeps the same schema in — to AI assistants as a
set of typed tools. Read and write transactions, run reports, manage
scheduled transactions, budgets, investment lots, and a full business
module (customers, vendors, employees, invoices, bills).

**Tech stack:**
- Python 3.10+
- [piecash](https://github.com/sdementen/piecash) — GnuCash ORM
  (SQLite; PostgreSQL/MySQL via `uri_conn`)
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

- **The server never invents a storage shape for a GnuCash object.
  If it can't write what desktop writes, it doesn't write.** The book
  is a shared file; GnuCash desktop is the other party to it and reads
  every row we persist. A private shape that only this server can read
  is a defect even when every server-side test passes — scheduled
  transactions shipped that way from 1.2 through 1.4.4 (desktop listed
  them, advanced them on Since-Last-Run, posted nothing) and the first
  migration attempt produced a template that crashed GnuCash 5.12 on
  edit. Feature stamps under a key GnuCash doesn't know made a book
  unopenable the same week. Consequences: (1) before persisting any
  object type, read how GnuCash's own backend writes it (tables, slot
  frames, commodity of template accounts, recurrence rows) and write
  exactly that; (2) "opens and edits cleanly in GnuCash desktop" is a
  merge gate for any branch that touches storage, not an optional
  bookkeeper step; (3) any migration for a shape we already shipped
  rebuilds the object the way `create` does — never patches the old
  container in place. (Added 2026-09-10 by ruling, after the
  native-templates loop.)

  Why the gate exists at all: it is a promise to users, not a
  workflow. For most of the audience this server is a *complement*
  to GnuCash desktop — they keep entering, reviewing, and reporting
  there, and the server is the assistant that fills the book in
  between. The maintainer's own use is closer to a replacement (the
  LLM's review table stands in for the register), and that must
  never be grounds for relaxing the gate. The invariant is written
  for the audience, not for whoever is currently maintaining it.
  (Stated 2026-09-21.)
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
- **Cross-currency exchange rates** come from `book.prices`, every
  row included. The `type='transaction'` row piecash (and desktop)
  writes on a cross-currency transaction is a price GnuCash values
  by — its lookups never filter on type — so the server counts it
  too (maintainer ruling 2026-09-29, overturning the issue #94
  skip). Staleness keys on the rate valuation used, one window for
  all sources; `_market_prices_only` exists only so the dashboard
  warning can name a stale implied rate's provenance.
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
- **A book may not be a file.** `GNUCASH_BOOK_URI` / `--book-uri`
  serves a book from PostgreSQL (or any SQLAlchemy URL) —
  contributed by [@vchatela](https://github.com/vchatela) in PR
  #175, from the request in #174. Ask
  `self.source.is_file` — never `book_path is not None` — before
  doing anything file-shaped. `BookSource` (`book/_base.py`) owns
  that question, and exactly four things ask it: backups, the
  GUID-prefix cache token, the audit/backup directory, and the
  startup format sniff. URI mode is single-book by construction —
  `_book_paths` stays empty, which is what keeps `switch_book`
  unregistered, `multi_book_active()` False and the ruling-6 write
  disarm inactive without any of them knowing DB books exist.
  Connection URIs are password-masked wherever a book is named,
  and in every error and log line (`_scrub_credentials`),
  unconditionally (not behind `GNUCASH_REDACT_PATHS` — a path is a
  privacy preference, a credential is a leak).
- **Raw SQL is written in the three-dialect intersection.** Every
  `text(...)` statement runs unchanged on SQLite, PostgreSQL, and
  MySQL/MariaDB. SQLite is the permissive one — scalar two-argument
  `MAX`/`MIN`, `strftime`, `julianday`, `IFNULL`, `INSERT OR`,
  `GLOB`, and comparing a typed column to `''` all work there and
  nowhere else, and SQLite-only tests can't tell. The refcount
  clamp shipped as `MAX(0, refcount - :n)` and no tax-bearing draft
  could be deleted on a database book; the `date_posted = ''` heal
  aborted every PostgreSQL invoice lookup (#189). Write the portable
  form (`CASE`, `COALESCE`); where a statement genuinely must be
  one backend's, gate it on `_dialect_name`. Locked by
  `TestBackendPortabilityChokepoints::test_raw_sql_avoids_sqlite_only_constructs`.
  When a fix is dialect-shaped, the sibling search is "read every
  raw statement", not "grep for the construct that broke" — the
  second instance here was found only on the full read.
- **GnuCash's flag columns are INTEGER, not BOOLEAN.**
  `placeholder`, `hidden`, `enabled`, `is_closed`, `active`,
  `invisible`, the entry `*_taxable` / `*_taxincluded` pair. SQLite has no
  boolean type and coerces silently, so a Python `bool` worked by
  accident for as long as SQLite was the only backend; PostgreSQL
  raises `DatatypeMismatch`. Write them through `_gnc_bool`, locked
  by `TestBackendPortabilityChokepoints`.
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
  the per-pair memo since #126, and the raw stored time on every
  row it returns). `_price_tie_rank` is which price is current:
  GnuCash's `compare_prices_by_date` — later stored time, then
  smaller GUID — so every same-day tie lands where desktop's
  Accounts tab lands (price twin, 2026-09-29; the source-rank
  tie-break it replaced was bookkeeper F3).
- `_rates_as_of` / `_monthly_conversion_factors` /
  `_account_conversion_factors` — which FX rate a report may use
  (as-of is mandatory; flow vs. stock semantics pick the factory).
- `_resolve_account` / `_resolve_guid` — every inbound account/GUID
  ref, template-filtered, before any comparison or lookup.
- `_is_voided` / `_is_unreconciled` — split-state predicates shared
  by dashboards and detail tools so counts agree by construction.
- `_slot_bool` — tri-state boolean slot parsing (the third private
  parsing convention was the trigger to consolidate).
- `_upsert_price` / `_price_plan` — single/batch price writes can't
  diverge, and both keep GnuCash's rule: ONE price per pair per day
  (`gnc_pricedb_add_price`). A source that ranks equal or better
  than the day's price (in either direction of the pair) replaces
  it; a worse-ranked one is not written (`kept`). The engine twin
  showed desktop's SQL backend still saves the row its price
  database turned away; the server does not copy that (ruling
  2026-09-30: "desktop-readable, not litter-compatible"). Books
  still hold multi-row days — tests build one with
  `tests/conftest.py::leak_same_day_price` — and `_price_tie_rank`
  remains how such a day is read. Pinned by
  `tests/test_parity_prices.py`.
- `_classify_reconciliation` — dashboard aggregates and the
  drill-down table bucket rows identically.
- `_find_invoice_owner_by_guid` / `_document_owner_clause` — who a
  document belongs to, by name and by SQL filter. `owner_guid` is
  polymorphic (a Job on job-attached documents, an Employee on
  vouchers); a hand-rolled `owner_type == 4` or `owner_guid ==
  guid` silently drops both. Locked by `test_owner_resolution.py`.
- `_cache_token` — the only read of the book file's mtime; returns
  None for a DB book, which every prefix cache reads as "always
  rebuild".
- `_gnc_bool` — the one coercion for GnuCash's INTEGER flag
  columns.
- `_book_display_name` — how a book is named to anyone, path
  basenamed and URI password-masked.
- `_scrub_credentials` (`_format.py`) — the one scrubber for text
  that leaves the server by ANY road, masking connection-string
  credentials: the userinfo password (split at the LAST `@`, so a
  password containing one is masked whole) and secret-bearing query
  parameters (`?password=`, `sslpassword=`, `passwd=`), neither of
  which SQLAlchemy's `hide_password` fully covers. Naming the book
  safely was never enough: an exception raised while OPENING a
  database book quotes the whole connection string (piecash's
  "Database '…' does not exist", SQLAlchemy's "Invalid SQLite
  URL: …"), and `str(e)` went to the model, the audit file, and
  stderr beside a header that masked the same password (review
  C16a). Every road passes through it now: `redact_paths` (every
  tool error; the scrub is unconditional, the path half stays
  opt-in), the audit ERROR and debug lines, `_DailyFileHandler.emit`
  (the files we own), `CredentialScrubFilter` on the tool-error
  logger (which propagates to the host's stderr handler), `main()`'s
  startup prints, and `_describe_check_failure`. `_parse_book_url`
  never echoes a string it could not parse. A new place that puts
  exception text or a URI in front of anyone calls the scrubber;
  don't reach for `render_as_string(hide_password=True)` directly.
  Locked by `tests/test_credential_scrub.py` and the end-to-end
  `TestCredentialsNeverLeave` (plants a password, searches every
  output).
- `_sx_recipe` — the one reader of a schedule's recipe: GnuCash's
  template rows first, the pre-1.5 `splits-json` slot as fallback.
  Readers never write; every schedule write converts the book's
  legacy recipes at once, posting nothing.
- `_sx_next_due` / `_recurrence_next` — which occurrence is next.
  `Recurrence.cpp` ported verbatim: the anchor is the recurrence
  row (period start, mult, type, weekend adjust), a composite takes
  the earliest across its rows, and every surface — dashboard
  overdue, list, upcoming, the instantiation default — reads the
  same answer.
- `_budget_targets` / `_budget_stored_sign` — budget amounts cross
  the storage boundary in exactly two places: GnuCash's natural
  sign on disk (credit-normal types negative, book stamped), the
  surface's magnitudes everywhere else.
- `_strip_guid_slots` — called before every ORM delete of a row
  that can carry a GUID slot or a frame: transactions, splits, lots,
  accounts, template rows, parties, documents. piecash's `SlotGUID`
  inherits a delete-orphan cascade joined on the referenced GUID, so
  deleting a row that carries one sweeps every slot of the entity it
  points at (the target account's for a template split, the
  schedule's for a stamped instance, the invoice's for a posting
  transaction — the credit-note incident). Strip GUID and frame rows
  by raw SQL first. The rule was a convention until the 1.5
  adversarial review found four sites that skipped it (unpost wiped
  the invoice's document link; `replace_splits`, `delete_account`,
  and `delete_account_slot` each reached another entity's slots).
  **Locked by `tests/test_guid_slot_cascade.py::
  TestOrmDeleteSitesStripFirst`** — grep-the-source: every
  `book.session.delete(` / `book.delete(` / slot-accessor `del` in
  `book/*.py` strips within 40 lines or is listed with the reason
  the row cannot carry one.
- `_check_user_slot_key` / `_check_user_slot_row` (`book/admin.py`)
  — the one gate on the user-slot tools. By NAME: GnuCash's own
  account frames (`reconcile-info`, `lot-mgmt`, `ofx`, `import-map`,
  `balance-limit`, `tax-US`, …, pinned from Account.cpp), the two
  flags desktop reads from the slot while the server reads the
  column (`placeholder`, `hidden`), and the server's own `gnc-mcp`
  frame. By SHAPE: a slot that is not a plain string row is someone
  else's structured data, so a GnuCash key nobody listed is still
  refused. Strings desktop stores the way a user slot would
  (`notes`, `color`, `tax-related`) stay writable.
- `_upgrade_book_shapes` — the one caller of every conversion from
  a pre-1.5 private shape to GnuCash's own (schedule recipes,
  invoice link key, budget signs). Every schedule, budget, and
  business write runs it, merges its counts into the response, and
  one audit renderer names what converted. Reads never write. A new
  converter for a shape we shipped wrong goes here, not on its own
  module's writes. Two rules the 1.5 adversarial review added:
  **(1) it snapshots first** — `_ensure_pre_upgrade_snapshot`
  (`book/backup.py`) runs ahead of every converter, once per book
  (a marker file in the backups folder), copying the file's last
  COMMITTED state through a separate read-only connection and
  REFUSING the write if a file book can't be copied; it runs before
  the converters, not after they report work, because a large
  conversion can spill SQLite's page cache and take the exclusive
  lock mid-transaction. The suite switches it off in
  `tests/conftest.py`. **(2) a converter identifies old rows by
  what the old writer left, never by what the values look like.**
  The credit-note pass guessed from signs ("all positive means
  pre-1.5") and negated a legitimate credit note desktop and 1.5
  both store that way; it now keys on the old server's own
  fingerprint (`entries.i_disc_type = ''`). A converter that cannot
  tell its own old rows from desktop's rows must not run on them.
  The GUI gate found the same class a second time (G-1: desktop's
  Duplicate Invoice dates a line at 10:59 UTC, as the old server
  did, and the entry pass moved it), so the rule now covers the
  whole of `_migrate_business_shapes`: the marks are read once at
  the top (blank discount defaults on an entry; `billto_type = 0`
  on a document; piecash's filler columns on a slot row, which make
  a payment's `date-posted` slot or a lot's empty `notes` slot the
  old server's) and every pass skips an unmarked row. Bill-term
  counts are raised, never lowered. **(3) a snapshot that guarded
  nothing is withdrawn**: when the converters report no work, the
  pre-upgrade snapshot just taken is removed (the marker stays).
  Locked by `tests/test_converter_false_positives.py`, which
  compares every row of every table across a converter run on a
  1.5-written book, on desktop's shapes set down byte for byte, and
  on rows the engine has just written. A new pass gets a case there.
- `_dialect_name` / `_rollback_if_aborted` — the only backend
  branch a raw-SQL site may take, and the only way a swallowed
  database error is cleared. A statement one backend rejects
  (`WHERE date_posted = ''` on a PostgreSQL timestamp) aborts the
  whole transaction there, and the bare `except: pass` that follows
  hands the NEXT statement an `InFailedSqlTransaction` naming the
  wrong culprit — every invoice lookup on PostgreSQL failed that way
  until @JamesRao98's fork caught it. Gate such statements on the
  dialect; call the rollback helper in every `except` that swallows
  a database error. The real-driver proof is
  `_RealDatabaseTests.test_rollback_if_aborted`.
- `_check_failed` — the one way a dashboard collector records a
  failed check: clear an aborted PostgreSQL transaction, debug-log
  the traceback, return the visible line with the reason inline
  (`Low-cash check failed: ValueError: …`). A collector that
  swallows on its own reports a failed check as a clean book — the
  Python 3.10 GDATE incident — and on PostgreSQL leaves every later
  collector querying an aborted transaction. Locked by
  `TestDashboardHonestFailure`; spec
  `specs/v1.5/DASHBOARD_HONEST_FAILURE_SPEC.md`. Its first catch was
  `get_backup_health` raising on every database book's dashboard
  call, unseen for three weeks.
- `_rates_as_of_dated` — which rate valuation uses AND from when:
  `{guid: (rate, rate_date, via)}`, a chain dated by its oldest
  leg; `_rates_as_of` is its projection, and the dashboard's
  stale-price warning reads the date, so the warning describes the
  rate actually used (spec A5).
- `_open_documents` — the dashboard's one pass over posted
  documents, through `_document_settlement`; the business counts
  and the overdue warnings both consume it, and nothing in core
  reads a lot balance directly (grep-locked, spec A1).
- `_billterm_due_date` / `_write_due_date_slot` — GnuCash's
  `compute_time` ported verbatim, and the one writer of
  `trans-date-due` (a slot_type 6 timespec at 10:59:00 UTC, the
  desktop-posted specimen pinned by test; the GDate row the server
  wrote from 1.2 through the first cut of 1.5 was one desktop could
  not read — the desktop gate's catch, 2026-09-28).
- `_budget_period_bounds` — every budget period boundary, from the
  Recurrence.cpp port; the dashboard headline and
  `get_budget_report` share it (spec B5). `_recurrence_next` and
  its helpers live in `_base.py` for that reason.
- `_write_reconcile_info` / `_read_reconcile_info_all` — desktop's
  `reconcile-info` frame (key names pinned from Account.cpp), with
  `gnc_save_reconcile_interval` ported verbatim; `reconcile_account`
  and `enter_statement` write it, the dashboard threshold reads it
  (spec B4; desktop-gated).
- `_upcoming_cash_legs` — one pass over the schedules due this week;
  the Scheduled line and the low-cash trigger both read it (spec B6).
- `_future_statement_warning` — the one sentence both reconcile
  writers attach to a statement dated after today.
- **The parity twin** (`specs/v1.5/testing/PARITY_CREDIT_NOTE.md`) —
  the instrument for everything the slot registry can't see: the
  same flow on two identical books, one in desktop, diffed row by
  row with `tests/fixtures/parity_dump.py`. "Parity" means the diff
  is empty (maintainer ruling, 2026-09-29), not that desktop copes.
  `tests/test_parity_credit_note.py` keeps the credit-note twin's
  desktop dump as the expected text; a new business write path gets
  its own twin and fixture the same way. Conventions it fixed, all
  in `_migrate_business_shapes` for old rows: credit-note entry
  quantities stored negated (`gncEntrySetDocQuantity`), the
  application as `gncOwnerCreateLotLink`'s `L` transaction, document
  dates at the neutral 10:59 UTC and entry dates at local noon,
  `gncEntryCreate`'s column defaults, document lot flags left at -1,
  payment memos on both legs, no `date-posted` slot on `P`/`L`
  transactions, no empty notes slot on a lot.
- `_write_void_slots` / `_migrate_void_shapes` — `xaccTransVoid` and
  `xaccSplitVoid` key for key (numeric originals, GnuCash's
  void-time form, read-only); the converter rewrites the string-
  typed voids the server made before 2026-09-29. **The lock for the
  whole class is `tests/test_slot_shapes.py`**: a registry of every
  slot key the server writes, typed from the GnuCash source line
  that defines it, checked against every write site, the sample
  books on disk, and a fresh void. A new slot key goes in the
  registry first, with its citation — that is what stops the next
  "desktop can't read it" from shipping.
- **Entry guards from the 1.5 adversarial review** — each in the
  one place every caller passes, each with the reason beside it:
  `_check_price` (a price is positive, of one commodity in another,
  a plausible size; gates `create_price` and `create_prices`, dry run
  included); `_validate_transaction_splits` (a foreign-CURRENCY split
  has money on both sides or neither, and a share quantity that
  rounds to nothing is refused while one that merely rounds is
  reported through `_fx_sanity_warnings`); `pay_invoice` (the payment
  account is never a RECEIVABLE or PAYABLE, as desktop's dialog
  excludes them); `apply_credit_note` (two lots on the same side of
  the ledger have nothing to offset — never `abs()` a lot balance to
  decide that); `_credited_share` (the early-payment discount is
  measured on what credit notes have not settled);
  `_rational_amount` (a schedule amount desktop stored as a
  non-decimal fraction rounds half-up to the template currency
  instead of raising); `_repair_double_counters` (ID counters
  GnuCash 5.0/5.1 saved as doubles, bug 798930); the row builders'
  `_tsv_cell` / `_one_line` (book text cannot start a row of its
  own; account paths stay resolvable). Status of every review item:
  `specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md` §10.
- `_refuse_posting_record` / `_require_editable` (`book/_base.py`)
  — a document's posting transaction is read-only on every path:
  delete, void, `replace_splits`, and all three update forms route
  through the one refusal, keyed on `invoices.post_txn` (the link
  itself, not the `trans-read-only` slot, which a void used to
  overwrite and an unvoid delete). No `force` override, as desktop
  has none: `xaccTransVoid` refuses a read-only transaction and the
  register won't edit one. Before 1.5 only delete refused; a void or
  a forced `replace_splits` left the invoice reading "paid" with no
  payment, and a date edit left the document and ledger with two
  posting dates. `_require_editable` also carries the
  voided-is-immutable rule the four edit paths each had a copy of.
  A new transaction-changing path goes in the `ATTEMPTS` table in
  `tests/test_posting_record_guard.py`. Tests that need the old
  voided-posting state build it with
  `tests/conftest.py::void_posting_record`. `void_transaction` also
  takes `force` for a reconciled split, as delete and
  `replace_splits` do (side-finding 11).
- `_read_only_before` / `_read_only_period_note` (`book/_base.py`)
  — the book's read-only date (today minus the book option "Day
  Threshold for Read-Only Transactions", a double under
  `options/Accounts/…`; the engine reads no other type) and the one
  sentence a write attaches when it touches a transaction dated
  before it. A WARNING by ruling (2026-10-01, review C69): desktop
  refuses in the register only, the engine commits. Every
  transaction-changing path in core and reconciliation attaches it
  (create, batch, statement, the three updates, `replace_splits`,
  void, unvoid, both deletes); a new one does too. Tests:
  `TestC69TheReadOnlyPeriodIsNamed`.
- `_entry_math` (`book/_entry_math.py`) — what a document line
  comes to and what a document posts: GnuCash's own arithmetic,
  ported from `gncEntryComputeValueInt`, `gncEntryRecomputeValues`,
  and `gncInvoiceGetNetAndTaxesInternal` (5.12). Exact rationals
  throughout; rounding happens in exactly the two places desktop
  rounds — each line's NET half-up to the currency, and each tax
  ACCOUNT's document total (the lines' unrounded taxes summed) half-up
  once. A line's discount (which only desktop writes) is applied by
  its type and its how (PRETAX / SAMETIME / POSTTAX); the bill side
  never discounts; a credit note is computed from the stored
  (negated) quantity and negated for the document view, as
  `gncEntryGetDocValue` does. `_get_invoice_entries_and_total` is
  its one caller. The server's own math (tax per line, half-to-even,
  on a rounded pretax, a residual cent forced onto the largest rate,
  discounts never read) disagreed with desktop on a third to nearly
  half of multi-line taxed invoices — the 1.5 adversarial review's
  C1/C2. Don't "tidy" a total that differs from the summed prices by
  a cent on tax-included lines: desktop shows and posts that total.
  Pinned by `tests/test_entry_math.py`, every figure worked from the
  GnuCash source, and against the engine itself by the totals oracle
  (next entry).
- **The totals oracle** (`tests/fixtures/desktop_totals.py` +
  `parity_totals.scm`) — the parity twin's sibling for NUMBERS, and
  it needs no GUI: draft documents through the server, then have
  `gnucash-cli` load the book and print `gncInvoiceGetTotal` /
  `…Subtotal` / `…Tax` for each, through a tiny report loaded from a
  temp `GNC_CONFIG_HOME` (nothing is installed; Guile's compile cache
  is off). `tests/test_entry_math_desktop.py` has two halves: 300
  recorded documents with GnuCash 5.12's answers
  (`desktop_totals_5_12.json`), replayed through the server's row
  reader everywhere including CI; and a live run on a fresh random
  book whenever `gnucash-cli` is installed. Change the entry math and
  both must stay at zero differences; re-record
  (`uv run python tests/fixtures/desktop_totals.py record`) only when
  the generator changes or a new GnuCash release is the reference.
  Any question of the form "what would desktop compute for this
  draft" can be asked the same way — write the report, not a
  screenshot plan.
- `_posted_total` — what a POSTED document came to: its posting
  transaction's split in the document's lot, direction-normalized.
  The posting is the record; the entries describe the document.
  `_document_settlement` takes `grand_total` from it, so
  `amount_paid` is always the sum of what the lot's other splits
  settled, on every surface (`get_document`, the lists, outstanding,
  `pay_document`'s `total_paid`, the job report). The total used to
  be re-derived from the entries on every read: a tax table edited
  after posting turned a paid 105 invoice into "total 110, paid
  110", and a document desktop posted with a line discount read as
  part-paid with no payment on file. `get_document` now carries
  `total_note` when entries and posting disagree. Entries are summed
  only for a draft, or when the posting can't be read (voided or
  absent). This is also what lets the entry math change (the 1.5
  tax-rounding port) without moving any document already posted.
  Locked by `tests/test_posted_total.py`.
- `_lot_split_amount` — what one split in a document's lot settles,
  in the DOCUMENT's currency: its quantity when the account is in
  that currency and the transaction is not (a payment booked in the
  transfer account's currency — desktop's rule,
  `gncOwnerCreatePaymentLotSecs`, and the server's since
  2026-09-30), else its value. `_calculate_lot_balance` and the
  `payments` list both read through it; summing values alone read a
  desktop-settled EUR invoice as overpaid. `pay_invoice` writes the
  payment in the pay account's currency, relieves the receivable at
  its carrying amount when realized FX is booked beside it, and
  records the day's price itself (`_record_payment_price`) — its
  splits carry `SKIP_IMPLIED_PRICE_ATTR` so none implies the
  posting rate as today's.
- `_payment_lots` / `_unapplied_payments` — a party's money with no
  document on it: lots carrying desktop's `gncOwner/owner-type` and
  `gncOwner/owner-guid` and no LIVE invoice link
  (`_document_lot_guids`: a GUID inside the `gncInvoice` frame — the
  engine leaves the frame standing, empty, on a lot it has unposted).
  The outstanding list, `get_document`, and the dashboard's past-due
  lines all read `_unapplied_payments`. The writes are ports, named
  for what they port: `_attach_owner_to_lot`, `_offset_lots`,
  `_reduce_split_to`, `_find_offsetting_split`, `_create_lot_link`,
  `_auto_apply_lots` (gncOwner.c), and `unpost_invoice` is
  `gncInvoiceUnpost` — payments are kept, never refused.
- **The engine twin** (`tests/fixtures/engine_twin.py`,
  `engine_act.scm`) — the parity twin with no desktop on screen:
  `gnucash-cli` loads a report whose renderer calls the engine
  functions the dialogs call, and the SQL backend saves each commit
  (a report's `SESSION_READ_ONLY` only skips the lock). Same action
  through the server on a copy, `dump` both, the text must match;
  the engine's dumps are recorded for CI. A new business write path
  whose desktop side is an engine call gets a scenario in
  `tests/test_parity_prepayment.py` (or a sibling) before it merges.
  What the engine leaves in a SQL book that the server does not
  copy is a NAMED allowlist, `engine_twin.ENGINE_DEBRIS`, each entry
  saying what it is and why (bookkeeper ruling 2026-09-30, round 2):
  the empty payment lot abandoned after every payment moved into a
  document's lot; stale `post_txn` / `post_lot` / `post_acc` on an
  unposted document; the hidden child tax table no saved row
  references; stored refcounts. `dump` filters only what the list
  names, `debris_found` counts each kind, and every recording keeps
  the engine's counts beside its dump — never a silent filter. A new
  exception is added to the list with its reason, or it is a diff.
  SETTLED by the GUI gate (2026-10-01): a posted line may point at
  its tax table or at the hidden copy. Desktop writes both shapes
  and reads both; the server leaves lines on the live table.
- `_billterm_return_child` — `gncBillTermReturnChild`: a posted
  document points at a hidden copy of its billing term (same name,
  `invisible` 1, `parent` the term, refcount 0), reused while it
  still matches the term field for field. `_card_charges` — the
  `GNC_PAYMENT_CARD` branch of `gncInvoicePostToAccount`: what an
  employee voucher sends to the company card instead of the payable;
  `post_invoice` and `get_invoice` both read it. Both pinned by
  `tests/test_parity_posting.py`.
- `_lot_is_closed` / `_lot_cache_flag` — the one reader of
  `lots.is_closed`, ported from `gnc_lot_is_closed`. GnuCash keeps
  the flag as a tri-state: `1` and `0` are cached answers, and
  `LOT_CLOSED_UNKNOWN (-1)` means "compute from the balance", which
  is what desktop leaves on every lot it touches. The reader resolves
  -1 the way GnuCash does (no splits → open; zero balance → closed);
  `_lot_cache_flag` stores the computed answer before any
  `split.lot = lot`, because piecash's guard tests the raw column.
  Writers use the named constants. Locked by `test_lot_closed.py`,
  grep-the-source in both directions.
- `_ordered_splits` / `_txn_sort_key` — how a transaction's legs and
  a listing's rows are ordered. Splits follow GnuCash's own
  `xaccTransSortSplits` (non-negative value first), then account
  path, then GUID; rows sort by post date, entry time, GUID. Every
  list with ties (schedules, upcoming, dashboard previews, lots)
  carries a data-derived tie-break. The point is backend
  independence: SQLite and InnoDB return rows in different orders,
  and a book renders identically on both only when no renderer
  leans on the storage order.
- `_DailyFileHandler` — the audit and debug writers open the day's
  file by path on every record, with `_local_day` as the one clock.
  That makes the log robust to a file being moved or removed
  underneath a running server (it comes back at the path, with its
  header) and rolls to the next day's file without a restart.
- `_parse_owner_type`, `_commodity_quantum`, `_effective_owner_type`
  — same story, smaller surface. `_is_market_price` /
  `_market_prices_only` — the quotes-only re-derivation the
  dashboard warning compares against valuation to name a stale
  implied rate; nothing values inside it.
- **From the 1.5 close-out (`fix/1.5.0-numbers`, 2026-10-05)**,
  each the one place its rule lives; status of every item is in
  `specs/v1.5.1/README.md`:
  - `_query_filtered_splits` decides a date range on the DECODED
    date. SQL takes the range with two days of slack (plus, on a
    SQLite book that holds them, every row in GnuCash 2.6's compact
    date form); Python keeps the rows whose `post_date`, as piecash
    decodes it, is inside. It returns a list, not a Query. A row
    stamped at local midnight or in the compact form used to fall on
    the wrong side of a boundary (C63). Don't add a second SQL-side
    date comparison anywhere.
  - A converted balance is rounded to the currency's unit once per
    ACCOUNT (`_market_value`, and per account in `balance_sheet` and
    `net_worth`), and totals are sums of those. Lines add up to
    their total, and assets minus liabilities, `net_worth`, and the
    dashboard are one figure. Locked by
    `TestConvertedBalancesAreRoundedPerAccount`.
  - `source_open_kwargs` gives a file book ONE connection for the
    life of an `open()` (StaticPool, nothing reset on return).
    piecash's NullPool reconnected by path after every commit, so a
    book renamed between commit and response got an empty twin at
    its old path and its write was reported as failed (C28). Don't
    open a second connection on the book's engine inside a session.
  - `write_private_file` (`logging_config.py`) is how any small
    state file under the log folder is written: exclusive, no link
    followed, 0600. `_check_mcp_dir_entry` is the per-book folder
    check, applied under `GNUCASH_LOG_DIR` too (C53).
  - The write intent (`_write_intent` / `_report_interrupted_write`):
    a file beside the audit log from before a write tool runs until
    its entry is written; a leftover whose process is gone becomes
    an `INTERRUPTED` line (DS-11). After the tool returns, nothing in
    `audit_log` may replace its result (DS-16).
  - `_guard_three_byte_text`: a four-byte character headed for a
    `utf8mb3` MySQL table is refused at the cursor, by name
    (side-finding 12). GnuCash desktop creates those tables; piecash
    and CI's fixture create `utf8mb4`.
  - `_check_text` refuses control characters other than tab and
    line breaks; `_validate_account_name` refuses invisible and
    bidi characters; `_refuse_lookalike_name` (via `_name_skeleton`)
    refuses a name that reads like a sibling's. ZWJ and ZWNJ stay
    legal: Persian, Indic and emoji spellings need them.
  - `_book_tables_error` (`server.py`): the startup check makes
    piecash's own `versions` comparison, so a book piecash would
    refuse is named at startup with what to do (FC-14).
  - `tests/fixtures/gnucash_made.py`: a book GnuCash CREATED, made
    headless through `gnucash-cli` and Guile's FFI (the Guile
    bindings do not wrap `qof_session_begin`). `tests/
    test_gnucash_created_book.py` sweeps the server's write paths on
    it and has GnuCash load and pay against the result: the headless
    half of the desktop-open gate. A new write path gets a line in
    its `_sweep`.
  - The engine twin's `dump` ends with the book's own slots
    (options, counters, feature flags). `iso_date_feature` is the
    one named difference there.
- **Trading-accounts books REFUSE a write that would need trading
  splits** (`_piecash_shapes._transaction_validate`, bookkeeper
  ruling 2026-10-05 item 3): the trigger is piecash's own, a non-zero
  quantity imbalance in some commodity, so a schedule template, a
  void, and an unvoid of a desktop-made transaction pass (the first
  cut compared commodity sets and refused all three: scoped review
  S-2). piecash's trading splits are at a denominator GnuCash does
  not use, the tree is found by the English name, and `pay_invoice`
  books a realized FX split where GnuCash books none. What has to be
  built before the refusal lifts is listed in
  `specs/v1.5.1/README.md`; an engine twin decides when it has.
- **From the scoped review (`specs/v1.5.1/review/`, 2026-10-05):**
  `_pid_alive` (`_format.py`) is the ONE process probe — `os.kill(pid,
  0)` terminates the process on Windows, and the audit intent and the
  `gnclock` holder note both asked it (grep-locked). `_check_control_
  chars` is the one text rule, behind `_check_text` and every writer
  that caps bytes on its own. Flow reports round per (category,
  month) cell and sum; the budget headline converts as the report
  does (`_monthly_conversion_factors`). `_num_bearing_actions`: with
  Num on split actions, an action counts as a number only off a
  business or stock transaction. `_is_hidden` inherits the flag as
  `xaccAccountIsHidden` does. `_name_skeleton` keeps a joiner where
  it draws (Arabic, Indic, emoji, tag sequences) and drops it
  between Latin letters. Transaction dates bind through
  `_neutral_time` (`_date_bind`), not piecash's flat 10:59.
- **From the second scoped review (`specs/v1.5.1/review/
  SCOPED_REVIEW_2026-10-06.md`, four readers over the whole close-out
  diff):** `_check_one_line` is the rule for every name-like field
  (party, schedule, budget, bill term, job, lot title, document ID,
  commodity): no tab, line break, NEL, or Unicode line/paragraph
  separator, so a name can never start a row of its own on the
  dashboard or in a report (the C57 class, found a second time; the
  emitters also pass names through `_one_line`). `_check_ledger_date`
  is the one date range, GnuCash's 1400-01-01 to 9998-12-31
  (MINTIME/MAXTIME, a year's headroom for far-date arithmetic),
  applied where a transaction date binds (`_date_bind`) and on every
  schedule, budget, and report date; a refusal raised at bind time
  comes back through `safe_tool` as a validation error, never
  `unexpected_error`. `_stored_timestamp_utc` is the one decoder for
  a timestamp read by raw SQL (ISO text, GnuCash 2.6's compact form,
  a driver's datetime); the reconcile-info converter raised on the
  compact form and refused every later write to the book (BS-2).
  `_merge_into_link` extends a lot link as `gncOwnerCreateLotLink`
  does, one split per (lot, account), added to rather than
  duplicated, and `apply_credit_note` signs each link split as the
  negation of its lot's balance, never by side (BM-1).
  `_account_references` is the one census of what else points at an
  account (schedule templates, document lines, tax-table entries,
  `invoices.post_acc`, `employees.ccard_guid`, budget amounts);
  `delete_account` refuses while any remain (CS-4).
  `_legacy_folder_may_belong_to` claims a pre-1.5 `.mcp` folder for
  a book only when the folder's own contents do not name another
  owner: its backups' `books.guid`, then the `Book:` path in its
  newest audit file (CS-3; the bookkeeper's Q2 added the header). `register_secrets_from_url` / `_mask_known_secrets`
  (`_format.py`): a connection string's password is registered at
  startup and masked as TEXT wherever it appears, because a password
  holding a quote or a space defeats the URL-shaped scrub (CS-1).
  `_ensure_pre_upgrade_snapshot` always writes a fresh copy of the
  committed state; the hard link to a stage backup it took before
  could be an older state of the book (CS-2). And the converter rule
  held three more times (BS-1, BS-5, BS-6): a pass that cannot name
  the old server's own mark on a row does not touch the row.
- `_CONVERTED_BY_KEY` / `_OLD_SERVER_WRITE_KEY` (`_base.py`): the
  server's own marks on the root account. The first converting write
  marks the book; a pre-1.5 fingerprint found later in a marked book
  (`_OLD_SERVER_FINGERPRINTS`) is an old server's write, warned about
  in the response and on the dashboard for 30 days, never rewritten
  (FC-20; C9/G-1 doctrine: no fingerprint, no flip).

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
4. **The payoff is legibility, not just correctness.** PR #126 —
   [@bhbrunt](https://github.com/bhbrunt), an outside contributor,
   fixing a never-completes pathology on a 33k-split book — was
   possible as a small, safe diff because every
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
  manager; it handles the lock with retry backoff. Only the OPEN
  retries — the `yield` sits outside the loop, because a
  lock-shaped error raised by the *body* used to re-enter it and
  yield twice, which `@contextmanager` turns into "generator didn't
  stop after throw()" with the real error lost.
- **A locked book announces itself three ways** —
  `sqlite3.OperationalError`, `sqlalchemy.exc.OperationalError`, and
  piecash's bare `GnucashException("Lock on the file")` off the
  `gnclock` table (which GnuCash populates on both backends).
  `_is_lock_error` decides; non-lock members of those classes
  re-raise untouched.
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
- **Cross-currency prices**: a cross-commodity split leaves a
  `type='transaction'` price row, written by
  `_piecash_shapes._record_implied_price` the way desktop writes it
  (piecash's own version is replaced). Valuation counts it like any
  other row; a test whose subject is "no price at all" must delete
  those rows (`tests/conftest.py::drop_transaction_prices`). The
  rate the business module picks for a NEW posting is the one
  exception: quotes only, inside `_market_prices_only`.
- **piecash's `Price.date` binds at local midnight and validates
  against its own shape.** The column is `_DateAsDateTime(
  neutral_time=False)`: it accepts only a bare `date`, stores it at
  local midnight in UTC, and the save-time `Price.validate` re-queries
  the row with that same bind — so a price stored at GnuCash's
  neutral time (every desktop-written price, and ours since the
  price twin of 2026-09-29) raises `NoResultFound` on any save that
  touches it. `book/_piecash_shapes.py` replaces `Price.validate`
  with a by-day comparison; `_stamp_price_row` stamps the date and
  the reduced value by raw SQL. Don't write a price date through
  the ORM.
- **`book/_piecash_shapes.py` is where piecash's shapes are
  corrected**, imported unconditionally from `_base.py`: slot
  filler-column defaults (GnuCash leaves `double_val` NULL and
  `timespec_val` at the epoch; piecash wrote 0.0 and NULL on every
  slot), and `Split.validate` replaced so a cross-commodity split
  writes GnuCash's implied price (`record_price` for STOCK/MUTUAL
  accounts, the exchange dialog's `create_price` otherwise) and
  stamps no `Buy`/`Sell` action. A new piecash behavior that
  diverges from desktop's rows gets fixed there, once, not per
  write path.
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
- Count `type='transaction'` prices; desktop does (ruling 2026-09-29).
- A commodity priced only through a pivot currency values via a
  one-hop chain (provenance notes the path, e.g. `via USD`). The
  chain came from Abdulla Alhosani's
  ([@alhosani-abdulla](https://github.com/alhosani-abdulla))
  report in issue #94; its second half — skipping
  `type='transaction'` legs as fee-laden — was overturned on
  2026-09-29 for parity with desktop, which chains through them.
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
  One builder per persona generates a realistic multi-year book
  exercising most tool paths. Reporting regressions surface here
  before unit tests catch them. Three personas: Alex (USD-default,
  full feature exercise, audited as an IRS-minded read), Lin Wei
  (CNY-default, zh_CN chart, multi-currency stress), and Sabine
  Brenner (EUR-default, German SKR03 chart — the i18n bug-class
  oracle). **No book is committed**: `samples/*.gnucash` is
  ignored, the chart is code, and `rebuild_all.py --skip-refresh`
  builds all three from nothing through today, deterministically;
  CI does the same for the bundle and the Glama image. Each
  builder's `--chart-only` writes just the chart in seconds, and
  `tests/test_demo_bases.py` checks it on every run.

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

**PostgreSQL coverage.** `tests/test_db_backend.py` runs almost
entirely without a database: a `sqlite:///` URI is a real
SQLAlchemy URL, so it exercises every DB code path that isn't
dialect-specific. The genuinely PostgreSQL-shaped assertions sit in
`TestPostgresBackend` and skip unless `GNUCASH_TEST_PG_URI` names a
reachable server. Locally:

```bash
docker run -d --name gnucash-mcp-pg -e POSTGRES_PASSWORD=gnucash \
  -e POSTGRES_USER=gnucash -e POSTGRES_DB=gnucash -p 55432:5432 postgres:16
GNUCASH_TEST_PG_URI=postgresql://gnucash:gnucash@localhost:55432/gnucash \
  uv run --extra dev --extra postgres pytest -q tests/test_db_backend.py
```

CI's `postgres` job does the same against a `postgres:16` service
container. A dialect bug that SQLite hides — the bool-in-an-INTEGER-
column class above — only ever shows up there.

**MySQL / MariaDB coverage.** The same class runs a second time as
`TestMySQLBackend` under `GNUCASH_TEST_MYSQL_URI` (one body,
`_RealDatabaseTests`; the subclasses differ only in URI, dump tool,
and the live-connection query). Locally, against a Homebrew or
Docker MariaDB, on a database the fixture may DROP — never the one
holding a real book:

```bash
GNUCASH_TEST_MYSQL_URI=mysql+pymysql://gnucash:gnucash@127.0.0.1:3306/gnucash_test \
  uv run --extra dev --extra mysql pytest -q tests/test_db_backend.py
```

CI's `mysql` job runs it against a `mariadb:11` service container.

**A desktop-saved copy is an oracle.** GnuCash desktop's File → Save
As into a database re-serializes every row through GnuCash's own
backend: KVP children under their frame's full path, lot flags reset
to UNKNOWN, rows in primary-key order. Diffing every read tool
between the committed file and that copy — 24 calls, identical except
the `Book:` line is the pass — checks the server against desktop's
serializer directly, which no server-only book can. Run it for any
branch that touches storage or a backend; the harness shape is in
`feedback_desktop_saved_copy_is_an_oracle` (memory) and takes minutes.

---

## Development conventions

### Commits

- Short summary, then bullets for detail.
- Conventional-commits style prefixes (`feat(budgets):`,
  `fix(core):`).
- No attribution lines.

**Who the message is for** (added 2026-09-12, after reading all 900
subjects back). The first 900 commits were written by Claude with no
guidance on audience, and the log shows it: 137 subjects over 72
characters, most of them two-clause sentences addressed to someone
who was in the session. The rules that were missing:

- **The subject is for a stranger.** Fifty characters, seventy-two
  hard cap, one clause. It names the change, not the moment it was
  found. No house vocabulary (loop, battery, ruling, cousin, the
  bookkeeper's rounds), no review IDs (`SB-6`, `HP-7`), no session or
  persona names, no metaphors. "fix(budgets): store GnuCash's natural
  sign" is right; "the un-blooming in 1.4.4" is a letter title.
- **The body carries the story.** What changed and why, then the
  pointers: the review ID, the spec path, the bookkeeper round that
  found it. The narrative of discovery lives here or in
  `CLAUDE.local.md`, never in the subject.
- **A merge subject is the PR title with its number, never the
  branch name.** PRs merge with `--merge` and no squash, so
  `git log --first-parent` IS the release history — and 43 of the
  first 48 merge commits on develop read `Merge pull request #N from
  ninetails-io/…`, which tells a reviewer nothing. Set it on every
  merge: `gh pr merge N --merge --delete-branch --subject
  "feat(mcpb): one-click Claude Desktop bundle (#N)"`. First-parent
  should read as a changelog without opening a single PR.
- **Changelog edits ride the commit that earns them.** A separate
  `docs(changelog):` commit is for a release pass, not for every fix.

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
3. **No sample book is committed** (ruling 2026-09-17, replacing
   the frozen-demo policy of v1.4.2–v1.4.4 and the last of the
   binary blobs). The builders are the samples: CI builds the three
   books from nothing at bundle time, and so does any clone. Report
   numbers re-anchor on (generator version, cache version,
   `--through`), not on committed bytes: for before/after
   verification, build both sides same-machine at the same
   `--through`.
   **The market-data cache IS refreshed per release** (standing as
   of v1.4.4): `uv run python scripts/synthetic_book/market_data.py
   --refresh --through <release date>` and commit the updated
   `market_data_cache.json` — every build reads it, and a stale
   cache caps how current the demo books can be.
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
