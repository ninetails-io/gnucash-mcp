# v1.5 Roadmap — deferred / needs-attention backlog

The pool of items deferred out of prior releases or flagged for
attention, as of v1.4.0. This is a list, not a plan: whether any
item lands as a **1.4.1 patch** or waits for a **1.5 feature
release** is a per-item call, tagged below where it's clear.

**Trust the code, not this list.** Several items derive from
predecessor session notes; verify each against current source
before picking it up.

---

## Needs attention (patch candidates — 1.4.1)

- ~~**Multi-book log/backup-dir collision**~~ — **CLOSED 2026-07-07**
  in two halves: backup-state starvation + cross-book pruning via
  stem scoping (PR #115, hardened in #119), audit interleave via
  per-book `.mcp` subdirs under `GNUCASH_LOG_DIR` (PR #120). See
  `specs/v1.4/review/CODE_REVIEW_v1_4.md` MB-2/MB-3.
- **`.gitignore` gap** — `samples/*.gnucash.<timestamp>.log`
  isn't matched by the current `*.gnucash.log` rule, so those
  files show as untracked. Tighten to `samples/*.gnucash.*.log`.

---

## Ruled and specced — implement next (maintainer rulings 2026-07-07)

- ~~**GB-1: unify the report rate-anchor policy**~~ — **IMPLEMENTED
  2026-07-08** on `feat/gb1-per-period-rates` (monthly-close
  valuation, TestModeAgreement lock, zero oracle drift) and
  **loop-signed-off 2026-07-09**
  (`BOOKKEEPER_TEST_REPORT_GB1.md`: Tracks A+B fully green, twice).
  Original ruling kept below for the record. Stephen's ruling: single-period `spending_by_category` /
  `income_by_source` / `cash_flow` adopt per-sub-period valuation so
  their totals agree with `group_by` mode (today: single-period
  anchors every split at range-end; group_by at each period's close;
  grand totals disagree on FX books). Recommended implementation:
  value every split at its MONTH's close in BOTH modes ("monthly
  close rates" — a defensible accounting convention); quarter/year
  columns and single-period totals become sums of month-valued
  splits, making totals granularity-invariant, and `group_by=month`
  numbers (already bookkeeper-seen) don't move. Lock with a
  mode-agreement regression test (single total == group_by grand
  total, all three granularities). **This shifts bookkeeper-validated
  numbers on multi-currency books** — per
  `feedback_bookkeeper_validates_base_cases.md` it must ship through
  a capture-rig pre/post on all three oracles AND a bookkeeper loop;
  that is why it wasn't implemented on ruling night (Fable's last).
  `vendor_spending_report` is exempt (posted-ledger both modes).
- **Resolver-twins chokepoint refactor** — `_get_or_create_fx_account`
  and `_get_or_create_discount_account` are ~150-line near-twins;
  the 2026-07-07 local review (8-angle) flagged divergence already
  visible (localized exact-matching + designate-on-exact exist only
  on the FX side; needed by discounts the day discount-leaf
  translations ship). Extract one parameterized
  `_resolve_or_create_designated(spec)` — slot key, keywords,
  own-locale leaves, canonical path/type, commodity gate flag,
  notice type. Behavior-neutral refactor; lock with the existing
  resolver test suites.
- **`_slot_bool` helper** — two incompatible slot-boolean
  conventions now exist: `_get_is_credit_note` (strict `== "1"`)
  vs `is_retirement` (lenient truthy/falsy sets). Extract one
  parser into `book/_base.py` next to `_slot_value_str`, decide
  the convention once, before a third boolean slot appears.

---

## Deferred features (1.5 candidates)

- **Price auto-retrieval** (`update_prices`) — fetch FX rates
  (Frankfurter) and stock/fund quotes (yfinance), with
  `stale_only` auto-discovery and explicit `tickers=[...]` batch.
  Closes the dashboard's stale-price warning loop. First live
  network call in the server → own sprint for failure handling.
  ~500–700 LOC. _Spec: [PRICE_UPDATE_SPEC.md](../v1.4/features/PRICE_UPDATE_SPEC.md)._
- **i18n output localization (Tier D)** — render reports, errors,
  and audit lines in the book's locale; number-format policy
  (decimal separator per locale). The largest remaining i18n
  lift. _Spec: [I18N_ACCOUNT_RESOLUTION_SPEC.md](../v1.4/i18n/I18N_ACCOUNT_RESOLUTION_SPEC.md) Tier D / §9._
- **Taxtable default cascade + `tax_override`** — customer/vendor
  default taxtable with an explicit override gate; ~80 LOC, the
  posting-math seam already accepts inherited taxtables. _Spec:
  [TAXTABLES_SPEC.md](../v1.3/features/TAXTABLES_SPEC.md)._
- **Accrual A/R revaluation** — mark-to-market open
  foreign-currency invoices at reporting date (the unrealized FX
  side; today only realized gain/loss books at settlement).
  Idea-only, no spec.
- **DB backend (Postgres/MySQL)** — open books via a SQLAlchemy
  `uri_conn` / `GNUCASH_BOOK_URI` instead of a file path. piecash
  supports it; needs driver extras and graceful degradation of
  the file-based features (backup, `os.pathsep` multi-book,
  `gnclock` recovery). Low demand — build on request. Idea-only.
- **Batch-entry follow-ons** — per-row `force` column; persisted
  duplicate-match `guid` column. _Spec:
  [BATCH_TRANSACTION_ENTRY_SPEC.md](../v1.4/features/BATCH_TRANSACTION_ENTRY_SPEC.md) (marked "deferred to v2")._

---

## Maintenance / hygiene

- **GB-1 bookkeeper-loop follow-ups** (2026-07-09 report,
  `specs/v1.5/BOOKKEEPER_TEST_REPORT_GB1.md`; F1 was fixed on the
  GB-1 branch itself):
  - **F2** — validate-then-open: a write tool that fails input
    validation still opened the book readonly=False, consuming the
    monthly auto-backup trigger on a no-op. Move account/argument
    resolution ahead of the write-mode open where feasible.
  - **F3** — same-date price tie-break is undefined/undocumented:
    two prices on one commodity/currency/date resolve by an
    accident of query order (observed `user:market_data` beating
    `user:price`). Define the rule (e.g. source priority, then
    newest insertion) and document it in `_find_prices`.
  - **F5** — timestamp conventions differ: backup filenames are
    UTC, audit/debug day-files are local-dated. Label or unify.
  - **F6** — README note: Claude Desktop may spawn 2-3 server
    processes on relaunch; the open-per-request design limits
    lock exposure, but the behavior should be documented.
  - Reads-oddly: over-forgiving path normalization (a
    `Users/...` entry without leading slash loaded), zero-total
    categories dropped from group_by tables rather than shown as
    0.00.
- **v1.4 review LOWs deliberately left open** (see
  `specs/v1.4/review/CODE_REVIEW_v1_4.md` for mechanisms): FX-1
  (`allow_after` inconsistency on cost-basis fallback legs — math
  path, wants explicit review), MB-8 (hardlinked duplicate books),
  I18N-8 (`GNUCASH_LOCALE` process-global vs per-book), plus a
  pathological backup-label re-parse edge (a manual label containing
  a literal `-<timestamp>-<stage>` tail mis-parses its stem and
  drops out of retention — exotic; noted by the 2026-07-07 review).
- **Test-isolation bug (pre-existing)** —
  `test_list_backups_logs_warning_on_unstattable_file` fails when
  `tests/test_logging.py` runs FIRST (non-alphabetical selection):
  `setup_logging` leaves the `gnucash_mcp.debug` logger with
  `propagate=False`, so `caplog` captures nothing. Fix the test to
  reset propagation (or capture via handler). Invisible in full-suite
  runs (alphabetical order masks it).
- **Test-suite pruning** — drop redundant tests (e.g.
  multi-currency cases that duplicate a USD case with one
  parameter changed) before the suite grows further. _(Flagged by
  Stephen; verify scope against current 1,714-test suite.)_
- **Per-currency report segmentation** —
  `spending_by_category` / `income_by_source` don't convert
  foreign-currency income/expense accounts. One-line fix per the
  `_split_in_default_currency` pattern if a real case appears.
  _(Pantheon note — verify against current code.)_
- **DRY `_latest_market_rates`** — near-duplicate in `book/core.py`
  and `book/reporting.py`; hoist to `_base.py` if a third caller
  appears. _(Pantheon note — verify against current code.)_

## Not actionable (tracked so it isn't re-investigated)

- **Dependabot alerts** — the ~26 open alerts are all transitive
  `mcp[cli]` web-stack CVEs (Starlette, uvicorn, python-multipart,
  PyJWT, cryptography, pydantic-settings): web-facing bugs (request
  smuggling, ReDoS, auth) in code paths a **stdio** server never
  runs — unreachable here. A `uv lock --upgrade` clears the minor
  within-range bumps but not the headline **Starlette 0.52 → 1.x**,
  which `mcp` (currently 1.26.0) pins below 1.0 — gated on the
  upstream SDK, not ours to fix. Hygiene only; no real exposure.
