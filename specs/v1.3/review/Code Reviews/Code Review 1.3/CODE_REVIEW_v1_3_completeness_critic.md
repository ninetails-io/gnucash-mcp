# Code Review — v1.3 pre-release: COMPLETENESS CRITIC

The job of this pass is not to find bugs but to identify what was
**not searched**, what was **claimed but not verified**, and what
**categories of failure** the existing methodology would miss. It
complements the five sibling reports — does not duplicate them.

Findings here are organized as *probes* rather than *bugs*: each
section ends with a concrete, actionable check the maintainer (or a
follow-up Claude pass) could run.

---

## Dimensions not searched

### D-1. Locale-sensitive formatting

`strftime("%b %Y")` (book/reporting.py:172, 205) and `"%B %Y"`
(reporting.py:205) render month abbreviations / names through the
process's locale. On a server running with `LANG=fr_FR.UTF-8`, the
debt-payoff plan emits `"Avr 2030"` instead of `"Apr 2030"`. The
LLM may then echo "Avr" back to a user expecting English. Not a
correctness bug per se, but the rest of the codebase uses ISO
dates (`%Y-%m-%d`) consistently — these two `%b/%B` strings are
the outliers.

**Probe:** Run any debt-payoff or trajectory rendering with
`LC_TIME=fr_FR.UTF-8` set before launching the server; confirm
whether the LLM-facing output is still English. If yes, either
force `locale.setlocale(locale.LC_TIME, "C")` at server start, or
hand-format the month names from a fixed English map.

### D-2. Timezone of `date.today()`

44 call sites. `date.today()` returns the *local* timezone's
date. A server running with `TZ=UTC` while the user's wall clock
is in UTC-8 will compute "today" eight hours ahead of the user's
expectation. Mid-day-Pacific writes "happen tomorrow" from the
server's perspective. Affects:
- `_compute_net_worth_at` branching at `book/core.py:423`
  (whether to filter price dates).
- All "now" / "today" defaults in `get_balance`,
  `list_transactions`, etc.
- `_should_run_pre_write_backup` daily check.
- `create_transaction` when `trans_date` is omitted.
- `_collect_create_signals` duplicate-detection window.

Backup timestamps correctly use UTC (`_now_utc()`), but the
`date.today()` paths assume server-local-tz matches user-tz.

**Probe:** Set `TZ=UTC` before invoking the server with a book
where Stephen's wall clock is Pacific. Run
`create_transaction(description="test")` without a `trans_date`.
Verify the resulting `post_date` matches the user's local-time
expectation, not the server's. If they diverge, decide whether
`date.today()` should be replaced with an explicit
`datetime.now(tz=user_tz).date()`, or document that the server's
TZ must match the user's.

### D-3. Float-precision Decimal construction

`tools/_helpers.py:_to_decimal` exists to guard user-supplied
strings from `Decimal(float)` precision loss. But `Decimal(...)`
appears 25+ times across the codebase. Most are `Decimal(str)`
or `Decimal(0)` (safe), but a few merit auditing:
- `book/reporting.py:84` `Decimal(r['amount'])` — `amount` is
  computed in Python, should already be `Decimal`. Verify it
  never round-trips through float.
- `book/core.py:721` parses a string from an error-message split.
  String input → safe.
- `_apply_limit` and other display-side conversions are likely
  safe but worth a manual sweep.

**Probe:** Write a one-shot lint that grep-asserts every
`Decimal(` call site receives either a string-literal,
integer-literal, or a `Decimal`-typed expression — never a `float`
or an `amount`-named bare attribute access. About a 30-minute
audit. The previous review's "Decimal precision preserved
end-to-end" claim is plausible but not enumerated.

### D-4. Documentation drift

- **README.md:263**: claims `--modules=all` is "every tool, 106
  of them." Stage 3 actually shipped at 101, but PR #92/#93
  cleanup may have changed this. Verify against current
  `TOOL_MODULES` totals.
- **README.md:271**: "core gives 29 tools." If module rebalancing
  has shifted any tool, this number is stale.
- **README.md:426**: section header "## What's in v1.2.1" but the
  CHANGELOG already documents v1.3.0. README is stale by one
  release.
- **README.md:463**: points to `specs/NEXT_STEPS_1_3.md` which
  the predecessor letters explicitly say is retired in favor of
  `specs/VERSION_1_3_PLAN.md`. Dead pointer.
- **README.md:527**: "1,355 tests as of v1.3.0" — actual current
  count is **1,394** (run `uv run pytest --collect-only -q`).
- **README.md:543**: "Roughly 18,000 lines of Python source" —
  actual is ~21,000 (`wc -l src/gnucash_mcp/**/*.py` →
  19,768 LOC). Drift is real but minor.

**Probe:** Section "What's in v1.2.1" is the load-bearing
content; rewrite to "What's in v1.3" before release.
NEXT_STEPS_1_3.md pointer must update or delete.

### D-5. Synthetic-book pipeline coverage

`scripts/synthetic_book/` last touched May 1, 2026. Last touched
**before** module restructure (May 24), Stage 3 features
(vouchers / credit notes / jobs / taxtables — May 26 to early
June), and the v1.3 release prep marathon.

`grep` confirms: zero references to taxtable / credit_note /
voucher / create_job / job_id in the phase scripts.

The two sample books on disk:
- `samples/alex-chen-morales.gnucash` last rebuilt May 31 (after
  Stage 3 lands but *via the same phase scripts that don't
  exercise the new features* — so even if the file is recent, it
  doesn't cover the v1.3 surface).
- `samples/lin-wei.gnucash` last touched May 26 — **before** Stage
  3 work landed. The Chinese-yuan multicurrency persona has zero
  exposure to vouchers, credit notes, jobs, or taxtables.

This is the single biggest gap in the verification methodology.
The bookkeeper validates against Alex's book through live MCP
calls, which provides some coverage, but Lin Wei does not get
exercised at all for the Stage 3 surface.

**Probe:** Add phase scripts (or extend existing ones) that
introduce voucher/credit_note/job/taxtable rows. At minimum:
- Phase 7 (business): add a job to Berlin Digital, add a credit
  note against an outstanding invoice, attach a taxtable to one
  invoice entry.
- Phase 7 for Lin Wei equivalent: ensure the CNY-default book
  exercises the same paths.

Rebuild both sample books after extending the scripts. CI should
verify that both samples open cleanly + the v1.3 surface returns
non-empty results for at least one row of each new entity type.

### D-6. Concurrency / threading model

The codebase uses `threading.local` (`_audit_tls` and
`_backup_check_lock`) on the assumption that the MCP server is
single-threaded. **Verified for stdio transport**: FastMCP runs
sync tool functions inline in an `anyio` event loop on a single
thread, so concurrency-of-tool-calls within a process is
serial.

But: **HTTP/SSE transport changes this.** `mcp.run(transport=
"streamable-http")` mounts a Starlette app that uvicorn can run
with multiple workers. The `--transport` flag isn't currently
exposed by gnucash-mcp's CLI, so this is a theoretical risk for
this release. If a future feature adds HTTP support without
auditing the threading-local sites, the audit-log staging would
silently leak across requests.

**Probe:** Add a docstring on `_stage_audit_before` /
`_consume_audit_before` documenting "single-thread stdio
assumption; HTTP transport requires per-request isolation." Add a
runtime assertion in `setup_logging` that the transport is stdio,
or that an alternative isolation is wired up if not.

### D-7. Input length / abuse vectors

No `max_length` constraints on any user-supplied string field:
description, memo, notes, customer/vendor/employee name,
address. A pathological caller could write a 1 MB description.
The DB grows, but no functional failure — until `_strip_noise`
or `_json` tries to serialize the whole thing back through the
audit log. Audit log entries could balloon to tens of MB per
write.

**Probe:** Add a 4-8 KB upper bound on free-text fields via the
Pydantic models (`max_length=8192` on `description`, `memo`,
`notes`, `name`, `address`). Document the limit in tool
docstrings. Confirm the audit-log JSON serializer handles the
limit gracefully on legacy already-written-long fields.

### D-8. Empty-string overwrite semantics

`_strip_noise` (`tools/_helpers.py:149`) strips empty-string
values from response dicts. The convention is "empty string
means no value." But this is asymmetric: a user passing
`memo=""` to `update_transaction` to *clear* an existing memo
gets the empty string honored on the write side (via the
nullable parameter convention) but the response dropped on the
read side. Not a bug, but creates inconsistency between
write-tool input and read-tool output of the same field.

**Probe:** Audit `update_*` tools — does each correctly
distinguish "not passed" from "passed but empty"? For instance,
`update_transaction(notes="")` should clear notes; does it? The
code in `update_transaction` (`book/core.py:4008-4009`) reads
`notes if notes else None` — this means empty string is mapped
to None, *not* preserved. If a user wants to set notes to an
empty string explicitly (rather than NULL), they can't. Decide
whether this is intentional and document it on each `update_*`
tool.

### D-9. Backup retention / disk-pressure edge cases

`prune_backups` and the staged retention (7 session / 4 weekly /
6 monthly) work normally, but the methodology hasn't probed:
- Disk-full conditions during a backup write
- Permission denied on the .mcp/ directory after creation
- Symlink attacks on the .mcp/backups path
- Behavior when the parent volume goes read-only mid-write

**Probe:** Mount a 1 MB ramdisk, set `GNUCASH_LOG_DIR` to it, run
20 write tools in a row. Verify graceful degradation (either the
write succeeds and backup-warns, or the write fails with a clear
"out of space" message).

### D-10. Error-recovery paths

Tests are happy-path biased. Several error-recovery patterns are
not exercised:
- `_verify_write` raising mid-create_transaction — does the
  session rollback cleanly?
- `book.save()` raising `IntegrityError` because a pre-condition
  silently changed between read and write — does the audit-log
  `before_state` get cleared, or does it leak into the next
  call?
- Stale `gnclock` left by a crashed process — the recovery path
  (DELETE FROM gnclock) is documented in CLAUDE.md but not
  automated. Probe whether the retry-with-exponential-backoff in
  `open()` ever recovers cleanly vs. eventually raising.

**Probe:** Add a `tests/test_error_recovery.py` with:
- A mock `book.save()` that raises after the audit stage
- A mock `_verify_write` that raises after a multi-step write
- A pre-existing `gnclock` row from a non-existent PID
Verify that subsequent calls see clean state, not stale before-
state or partial commits.

---

## Claims requiring independent verification

### C-1. "Every write is verified" (FALSE — enumerated)

`CLAUDE.md` invariants section: *"Every write is verified.
`_verify_write` / `_verify_composite_write` read back what was
written and raise if the round-trip doesn't match."*

Counts:
- `book.save()` call sites: **55** across all modules.
- `_verify_write` / `_verify_composite_write` /
  `_verify_delete` / `_verify_transaction_state` call sites:
  **47**.

Modules with `book.save()` but ZERO write-verify calls:
- **`book/admin.py`** (2 saves, 0 verifies) — `set_account_slot`
  and `delete_account_slot` write to the slots table with no
  round-trip check.
- **`book/investments.py`** (7 saves, 0 verifies) —
  `create_commodity`, `create_price`, `delete_price`,
  `create_lot`, `update_lot`, `close_lot`,
  `assign_split_to_lot` all save unverified.
- **`book/reconciliation.py`** (4 saves, 0 verifies) —
  `set_reconcile_state`, `reconcile_account`,
  `void_transaction`, `unvoid_transaction` write
  reconcile-state changes unverified.

Modules with partial coverage:
- **`book/core.py`** (10 saves, 3 verifies) — `delete_account`
  has NO `_verify_delete` call (line 3754 saves; no check that
  the row is gone).
- **`book/business.py`** (20 saves, 23 verifies) — better
  ratio, but several lifecycle operations (`pay_invoice` at
  5413, `unpost_invoice` at 5590, `post_invoice` at one of the
  earlier sites, `apply_credit_note` at 6107) save without an
  end-to-end round-trip check. The verify calls are for
  *creation* writes; the lifecycle mutations don't get the
  same treatment.

The invariant as written in CLAUDE.md is **not true** for
roughly 13 write tools. The CLAUDE.md text should be either
weakened to "most writes are verified" with explicit per-tool
table, or the unverified paths should be brought up to the
documented standard.

**Probe:** Enumerate per-write-tool whether `_verify_*` is
called. Add the table to CLAUDE.md as a contract. Add a
contract test in `tests/test_write_verification.py` that fails
if any new write-tool registration is added without a verify
call (mirror the existing `TestToolFileVsModulesMapping`
pattern).

### C-2. "Audit-log formatter dispatch covers every write op" (FALSE — enumerated)

`logging_config.py:1643-1708` is the dispatcher. By comparing
the 51 `(entity_type, operation)` pairs registered in tool
files against the dispatcher entries, the **missing formatters**
are:

| entity_type | operation | Tool registration site |
|---|---|---|
| commodity | create | tools/investments.py:37 |
| price | create | tools/investments.py:69 |
| lot | create | tools/investments.py:220 |
| lot | update | tools/investments.py:287, 338 |
| scheduled_transaction | create | tools/scheduling.py:18 |
| budget | create | tools/budgets.py:53 |
| billterm | update | (if exists in tools/business.py) |
| billterm | delete | (if exists in tools/business.py) |

`_format_audit_entry_text` silently returns `""` for missing
keys (per the docstring: *"degrades silently rather than
crashing"*). The audit text-format files for these tools
render as empty lines. M-7 in the existing review caught this
class but did not enumerate the specific gaps.

**Probe:** Verify the table above against the source. Add the
missing formatter functions (each is ~15 lines, copy-paste
shape from the existing `_fmt_*_create` family). Add a startup
check that every `(entity_type, operation)` registered via
`@audit_log(classification="write", ...)` has a dispatcher
entry — fail the import if missing.

### C-3. "1,130 tests passing" (STALE)

Predecessor letter dated 2026-05-24 quoted 1,130 tests. Letter
dated 2026-05-26 quoted 1,243. Letter dated 2026-05-31
(release-prep marathon) doesn't quote a number. README.md
claims "1,355 tests as of v1.3.0." Actual count today (per
`uv run pytest --collect-only -q`): **1,394**.

The number drifts continuously and predecessor letters age
fast. Don't trust the number.

**Probe:** Strip the test count from README entirely, or add a
CI step that updates it. (Easier: strip it.)

### C-4. "Multicurrency aggregation correct everywhere" (PARTIAL)

The existing report claims aggregation is correct in
`spending_by_category`, `income_by_source`, `balance_sheet`,
`net_worth`, `cash_flow`. Spot-checked sites where the claim
**is NOT verified**:

- **`debt_payoff_plan`** (`book/reporting.py:974-977`) sums
  `split.quantity` raw without conversion. For a CNY-default
  book with a USD-denominated debt account (or vice versa),
  the balance comes out in raw foreign units and is treated
  as the default currency. Mixed-currency debt portfolios →
  wrong YETI calculation.
- **`vendor_spending_report`** (`book/business.py:7667`) uses
  `_rates_as_of(book)` with NO `as_of` parameter — same B-1
  shape as `balance_sheet`. A historical-period vendor report
  applies today's exchange rates to historical bills.
- **`get_outstanding_invoices`** returns invoice totals in the
  invoice's own currency (intentional — documented). But the
  compact-format renderer does NOT label the currency for
  every row; verify rendering.
- **`get_job_report`** explicitly buckets by currency
  (`totals_by_currency`) — correct.
- **`get_book_summary` trajectory anchors** correctly route
  through `_compute_net_worth_at` which branches on date.
- **`get_book_summary` instructions block** at startup:
  whether the dashboard's per-bucket subtotals (assets,
  liabilities, business signals) all route through
  `_split_in_default_currency` — partially verified, not
  fully enumerated.

**Probe:** On Lin Wei's CNY-default book, create a debt
account in USD with an APR slot, give it a balance. Run
`debt_payoff_plan(monthly_budget="1000")`. Verify the kill
order doesn't list the balance as raw USD treated as CNY.

**Probe:** Set up Lin Wei's book with 6 months of vendor bills
(some CNY, some USD), then run
`vendor_spending_report(start_date="2024-01-01",
end_date="2024-06-30")`. Set the USD/CNY rate to something
distinctive **today** (e.g., 100:1) that's nothing like the
actual mid-2024 rate (~7:1). Verify the report uses
mid-2024 rates, not today's.

### C-5. "Template accounts filtered everywhere they shouldn't appear" (PARTIAL)

`_template_account_guids()` is called explicitly at 11 sites
across `book/core.py`, `book/_base.py`, `book/_currency.py`,
and `book/reporting.py`. But several `for account in
book.accounts` iterations don't apply the filter:

- `book/business.py:443` (`_find_fx_account`) — fuzzy match on
  INCOME/EXPENSE by name keyword. Template accounts are
  excluded by virtue of not matching the keyword set, but a
  pathological user-named template account *could* match. Low
  risk.
- `book/business.py:558` — same pattern.
- `book/business.py:5006` — builds `account_paths: dict[guid,
  fullname]` for entry display. Template account paths would
  be included if any entry referenced one (unlikely in
  practice, but the filter would be defense-in-depth).
- `book/core.py:170` — builds `accounts_by_guid` for
  per-invoice valuation. Template accounts won't have lots
  referenced by invoices, but the filter would be more
  precise.

The claim "template accounts filtered EVERYWHERE they
shouldn't appear" is over-broad; "filtered everywhere they
COULD appear in user-facing output" is more accurate.

**Probe:** For each `for ... in book.accounts` iteration in
the codebase, identify whether its output reaches user-facing
data. If yes, add `if account.guid in template_guids:
continue`. About 8 sites to audit.

### C-6. "`_format_number` precision rules correct" (TRUE for declared cases)

`book/_format.py` (read as part of this review) has 2-decimal
for currency, 4 for shares, 6 for crypto. Logic is correct
**for the declared cases**, but the dispatch is by `unit`
parameter — caller has to pass the right unit. If a caller
passes `unit="shares"` for a currency value, it'll display 4
decimals — silently wrong-precision.

**Probe:** Audit `_format_number` callers — does each pass the
correct `unit`? Grep for `_format_number(` and verify the
second arg.

### C-7. "Pydantic `extra='forbid'` patched at startup" (TRUE)

Confirmed at `server.py:35-38`. The patch executes at import
time before any tool module loads. **But**: `SplitInput`
(`tools/_helpers.py:83-86`) explicitly sets
`extra="ignore"` — overrides the global. Single override,
already flagged as L-3 in the existing review.

**Probe:** Promote `SplitInput` to `extra="forbid"`. Verify no
existing call site passes extra keys to splits.

### C-8. "`book.session.query(X).filter_by(guid=g).first()` is indexed" (UNVERIFIED)

The claim from predecessor letters is that this is ~1000×
faster than the loop alternative. Plausible (GUID is a primary
key in piecash's schema), but not measured against a real-shape
book in any committed benchmark.

**Probe:** Use `EXPLAIN QUERY PLAN` on Alex's book against a
representative `filter_by(guid=...)` query. Verify SQLite is
using the index. If yes, the claim holds; if no, the perf
gains are wrong.

---

## Bug classes our methodology misses

### M-1. Scale-only bugs

Alex has 357 transactions, Lin Wei ~2,000. A real bookkeeper
might have 30,000+ over a decade. Bugs that manifest only at
scale:
- O(N²) loops that complete in 50 ms on small books but in 5
  minutes on a large one. (`_collect_create_signals` was once
  one of these.)
- Memory bloat from keeping all transactions in Python
  objects when SQL aggregation would scale.
- SQLite query planner regressions at high cardinality.

**Mitigation:** Build a 30k-transaction synthetic book (extend
phase_13_volume.py output). Time critical reads. Profile.

### M-2. Long-running process bugs

The server is typically restarted with each Claude Desktop
session. If a power user runs the same server for a week:
- File descriptor leaks (each backup write opens / closes
  files — verify no leaks via `lsof`)
- Memory bloat from accumulating audit-log buffers (debug
  logs grow; rotation policy unverified)
- mtime cache invalidation edge cases on same-second writes
  on HFS+ filesystems

**Mitigation:** Set up a soak test: 8 hours of one-write-per-
minute, watch RSS and FD counts.

### M-3. piecash version upgrade bugs

`pyproject.toml` pins `piecash>=1.2.0` and `mcp[cli]>=1.0.0`
— both wide constraints. `uv.lock` has `piecash 1.2.1` and
`sqlalchemy 1.4.54`. The piecash dep emits a
`RemovedIn20Warning` for SQLAlchemy 2.0 compatibility — if
anyone bumps SQLAlchemy, things break.

A piecash 1.2.2 release with different behavior on a single
ORM attribute would silently change semantics. Nothing tests
that piecash's `Invoice` constructor is still blocked, that
`type='transaction'` prices are still auto-created on cross-
currency txns, that lots' `is_closed` field still uses int -1
sentinel.

**Mitigation:** Pin `piecash==1.2.1` and `sqlalchemy<2`
exactly in `pyproject.toml`. Add a sanity test that
explicitly asserts the piecash behaviors the codebase
depends on (invoice constructor raises, auto-rate placeholder
created, etc.).

### M-4. Error-path bugs the test suite happy-paths past

Tests almost universally exercise success paths. The
`pytest.raises` cases test that errors are raised; they don't
test that error paths *clean up correctly*. For instance:
- After a `_verify_write` failure, is the audit-log
  before-state cleared?
- After `book.save()` raises `IntegrityError`, does the next
  call see clean state?
- After a tool raises in the middle of building a multi-step
  transaction, is the SQLite session rolled back?

The threading-local pattern documents that
`_consume_audit_before` "always clears on read" — but a
raised exception before `_consume_audit_before` runs would
leave the before-state set. The wrapper's pre-clear at entry
(per the predecessor letter) is the safety net; verify it
fires unconditionally.

**Mitigation:** Add `tests/test_error_recovery.py` covering
clean-state-after-failure for each write tool's failure modes.

### M-5. Edge cases the bookkeeper hasn't hit

The bookkeeper-loop validates real-world workflows on
plausible-shape books. It doesn't probe:
- Zero-amount transactions (single test exists at
  `tests/test_business.py:1848`; coverage is thin)
- Single-split transactions (one test exists at
  `tests/test_book.py:4147`)
- Accounts with very long paths (5+ levels deep) — does
  short-prefix collision detection hold?
- Commodities with the same mnemonic in different namespaces
  (one test exists; not exhaustive — does
  `_find_exchange_rate` choose correctly when both
  `EXCHANGE:TEST` and `FUND:TEST` exist?)
- Transactions with 10+ splits — does `replace_splits`
  validate the sum-to-zero correctly when there are many
  contributing splits?
- Reconciled splits crossing currency boundaries
- Voucher / credit note posted in foreign currency

**Mitigation:** A `tests/test_edge_cases.py` covering each.

### M-6. Bugs in CLI argument parsing

`server.py:880` parses `--modules=X` by string match on
`sys.argv`. This silently ignores:
- `--modules X` (space-separated form)
- `--modules=X --modules=Y` (last-wins behavior is
  unspecified)
- Unknown flags like `--transport sse` (silently passed
  through to FastMCP which may accept them)

**Mitigation:** Use `argparse` instead of ad-hoc string
matching. Documented `--help` from argparse is also tighter
than the current manual help-text emission at line 856.

### M-7. Audit-log integrity in long lookbacks

`get_audit_log` returns audit entries. If an entry references
an entity that has since been deleted (account, transaction,
customer), the rendering may break. Tests cover the happy
path; the "renaming an account changes the audit log's
historical record" case is not exercised.

**Mitigation:** A test that creates a customer, posts an
invoice, deletes the customer (or renames an account), then
calls `get_audit_log` and verifies it renders cleanly.

---

## Assumptions to validate

### A-1. "piecash auto-creates `type='transaction'` prices for every cross-currency txn"

Used by `_is_market_price`, `_find_exchange_rate`,
`_market_value`, `_latest_market_rates`, `_rates_as_of`, and
in `pay_invoice` FX gain/loss. The auto-creation is piecash
internals; if a piecash bugfix release changes the rule (e.g.,
only creates on a subset of cross-currency txns), our skip
logic could miss real market prices the user explicitly set.

**Probe:** Empirically verify with the current piecash 1.2.1:
create a cross-currency transaction, inspect the resulting
Price records — confirm `type='transaction'` is always
present, the rate is the effective transaction rate, and the
date is the transaction's post-date. Document the finding.

### A-2. "ORM `filter_by(guid=...)` is indexed"

`tests/` don't include any EXPLAIN-level assertions on query
plans.

**Probe:** As C-8 above.

### A-3. "Threading-local isolation is correct for our event loop"

With stdio + sync tools running inline in an `anyio` event
loop on a single thread, `threading.local` works as designed
because the thread doesn't change. **But**: `anyio` could in
theory schedule work to a worker thread (via
`anyio.to_thread.run_sync`). Does any of our code path
trigger this?

**Probe:** Add a debug-log line in `_stage_audit_before`
recording `threading.get_ident()`. Run a write tool. Confirm
the thread ID matches the main thread. If it ever differs,
the threading-local assumption is broken.

### A-4. "SQLite mtime resolution is nanoseconds"

`_split_prefix_map` and friends cache by `mtime_ns`. APFS and
ext4 use nanosecond precision; HFS+ has 1-second precision.
On macOS 10.13+ APFS is default but a user on an old machine
with an HFS+ volume could see stale cache entries on rapid
back-to-back writes.

**Probe:** Document the assumption explicitly in the docstring,
or fall back to a counter-based invalidation (each write
bumps a session counter that the cache key includes).

### A-5. "Decimal arithmetic is exact and precision-safe"

True for `Decimal(str)` and arithmetic among Decimals. NOT
true for `Decimal(float)` — which is why `_to_decimal` exists.
The discipline is enforced manually; no contract test verifies
that every monetary `Decimal(...)` construction took a string
or Decimal, never a float. See D-3.

---

## Recommended additional passes before release

In rough priority order:

### R-1. Run the full test suite end-to-end TODAY

Predecessor letters quote test counts and "all passing"
claims from various dates. Before release, run
`uv run pytest -x` to confirm clean. If any test fails, the
release blocker is real and not yet flagged. Estimated time:
3-5 minutes.

### R-2. Extend synthetic-book scripts for Stage 3 features

Add taxtable, credit-note, voucher, and job rows to Phase 7.
Rebuild both Alex and Lin Wei. Verify the new entity types
appear in `get_book_summary` / `list_invoices` /
`get_job_report` on both books. Estimated time: 2-4 hours.

### R-3. Bookkeeper-validate on Lin Wei

Specifically run `vendor_spending_report` and
`debt_payoff_plan` on Lin Wei's CNY-default book. The two
sites flagged in C-4 are likely real bugs that the existing
review didn't catch because Lin Wei doesn't get the same
attention as Alex. Estimated time: 30 minutes — 2 hours
depending on bugs found.

### R-4. Enumerate write-verification gaps

C-1 found that 13 write tools lack `_verify_*` calls. Decide
which need verification added and which the CLAUDE.md
invariant should be relaxed for. Estimated time: 1-3 hours
depending on scope.

### R-5. Add the missing audit-log formatters

C-2 enumerated 7+ missing dispatcher entries. Each formatter
is ~15 lines. Estimated time: 1-2 hours.

### R-6. Fix README documentation drift

D-4 lists 6 README inaccuracies. Update "What's in v1.2.1" →
"What's in v1.3"; fix or remove dead pointers; remove or
auto-update test counts. Estimated time: 30 minutes.

### R-7. Locale and timezone audit

D-1 and D-2 are subtle but real. At minimum, document the
assumption in README ("run the server in the same locale and
timezone as your wall-clock expectations"). Fix the `%b/%B`
locale leak. Estimated time: 30 minutes — 1 hour for docs +
fix.

### R-8. Pin piecash and sqlalchemy exactly

M-3 — `pyproject.toml` should pin to the exact versions
tested. A `piecash` minor release could silently change
behavior. Estimated time: 5 minutes.

---

## Items NOT in scope for this critic

The sibling reports handle:
- **Specific multicurrency bugs**: see
  `CODE_REVIEW_v1_3_multicurrency.md`.
- **I/O bloat fixes**: see `CODE_REVIEW_v1_3_io_efficiency.md`.
- **Refactoring opportunities**: see
  `CODE_REVIEW_v1_3_refactoring.md`.
- **Report-accuracy edge cases**: see
  `CODE_REVIEW_v1_3_report_accuracy.md` and
  `CODE_REVIEW_v1_3.md`.
- **Architecture / contract violations**: see
  `CODE_REVIEW_v1_3_architecture.md`.

Where this critic overlaps with those reports, it's
deliberate — the overlap is in **claims those reports made
that I'm asking us to verify**, not in additional findings of
the same shape.

---

## Closing note

The existing review is good but optimistic. The praise-laden
predecessor letters and the "what looks right" sections of
each sibling report establish a baseline of high confidence
that may be slightly miscalibrated. The two highest-confidence
items I'd flag for the maintainer:

1. **The "every write is verified" invariant is FALSE** as
   stated. Either the invariant or the codebase needs to
   change.
2. **`vendor_spending_report` and `debt_payoff_plan` have
   B-1-shaped FX bugs** that the existing review did not
   identify — almost certainly because they were tested on
   USD-default Alex.

Everything else here is process-level: dimensions not
searched, claims not verified, edges not probed. Worth a
half-day pass before the release PR opens; not worth blocking
the release on.
