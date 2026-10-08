# Whole-tree review — 2026-09-04

Method: the entire `src/` tree (35.7k lines) plus the contract tests
(`test_contract_integrity`, `test_chokepoint_invariants`,
`test_write_verification`, `test_modules`, `test_manifest`, `conftest`)
were loaded into one context and read end to end, then every candidate
finding was verified by running it against temp copies of test-fixture
books and the Alex sample book. Nothing in the repo was modified;
nothing was committed. The verification scripts sit beside this file:
`verify_whole_tree_findings.py` (behavioral checks, temp books) and
`verify_whole_tree_query_counts.py` (SQL statement counts on a temp
copy of the Alex sample). Run each with `GNUCASH_MCP_NOAUDIT=1 uv run
python <file>`.

Status legend: CONFIRMED = reproduced by running code; INSPECTION =
established by reading, not run.

## Class 1 — the owner-resolution chokepoint is bypassed at seven sites

`_find_invoice_owner_by_guid` (business.py:278) and the job-aware
subquery in `_find_invoice` (business.py:1413-1435) exist precisely so
that `owner_type=3` (job-attached) documents and `owner_type=5`
(voucher) documents resolve to their real counterparty. Seven sibling
sites still key on `owner_type == 4` / `owner_guid == entity.guid`
directly. Each looks locally correct; together they make job-attached
and employee documents second-class across the surface.

| # | Site | Effect | Status |
|---|------|--------|--------|
| 1a | `post_invoice` business.py:5417-5423 | posting txn description defaults to `Invoice NNN` for vouchers and job-attached invoices instead of the counterparty name | CONFIRMED |
| 1b | `pay_invoice` business.py:5897-5908 | payment txn description is the EMPTY STRING for vouchers and job-attached invoices (vendor finder given an employee/job guid → None → `""`) | CONFIRMED |
| 1c | `_collect_warnings` core.py:927-946 | overdue voucher / job-attached bill renders `Past due invoice: #000001` — wrong document type, no name — while `get_outstanding_documents` names it correctly | CONFIRMED |
| 1d | `list_invoices` business.py:4909-4910 | `owner_type` filter drops job-attached invoices | CONFIRMED |
| 1e | `get_outstanding_invoices` business.py:7503-7524 | `owner_type`, `customer_id`, and `vendor_id` filters all drop job-attached documents from the collections list | CONFIRMED |
| 1f | `vendor_spending_report` business.py:8051-8064 | `owner_type == 4` + `owner_guid == vendor.guid` exclude job-attached vendor bills from spend totals | INSPECTION (identical shape to 1d/1e) |
| 1g | `_invoice_dependency_check` business.py:6996-7017 | ignores jobs and job-attached documents: `delete_customer` SUCCEEDS with a job and a POSTED job-attached invoice on the books; the invoice then resolves to `owner_name=None` forever | CONFIRMED |

Severity: 1b and 1g are the real ones (ledger data quality; dangling
references that no tool can repair). 1a/1c/1d/1e are visible
wrongness on every business persona that uses jobs or vouchers.

Fix shape (one branch): a `_document_owner(book, inv)` helper that
returns `(effective_owner_type, owner_entity)` via the existing
job-chasing finder, used by 1a/1b/1c; an "owner matches" SQL clause
(the `or_(owner_type == ot, and_(owner_type == 3, owner_guid.in_(job
subquery)))` shape `_find_invoice` already has) hoisted into a helper
used by 1d/1e/1f; and a jobs check added to 1g. Lock with one test per
site using a job-attached invoice and a voucher.

Reproduction: build a business book, create an employee + voucher and
a customer + job + invoice, post and pay both, then read the
transactions back; call `get_outstanding_invoices(customer_id=...)`;
call `delete_customer`.

## Class 2 — `_to_decimal` raises the wrong exception type

`_to_decimal` (book/_base.py:135) lets `decimal.InvalidOperation`
escape. That class is an `ArithmeticError`, not a `ValueError`, so:

- `create_transactions` phase 1 catches `(ValueError, KeyError)`
  (core.py:3829); a single `$10` or `10x` cell in ONE row kills the
  whole batch with `unexpected_error: InvalidOperation: [<class
  'decimal.ConversionSyntax'>]`, and `on_error="skip"` cannot rescue
  it. CONFIRMED.
- `create_prices` catches `ArithmeticError` explicitly
  (investments.py:455) and rejects the same cell PER ROW. CONFIRMED —
  the two batch surfaces disagree.
- `enter_statement` guards its own amount cells but counter-split
  `amt` cells flow through `_validate_transaction_splits` unguarded
  (core.py:4547 via `_statement_dispositions`'s `except ValueError`).
- `safe_tool` maps only `ValueError` to `validation_error`, so every
  bare `_to_decimal` site reports a typo'd amount as an internal error.

Fix: catch `InvalidOperation` inside `_to_decimal` and re-raise as
`ValueError` naming the offending text. One line retires the class at
every caller; the `create_prices` special-case can then go.

## Class 3 — unposting a linked credit note drops its `applies_to`

`unpost_invoice` (business.py:5712-5742) knows the posting-txn delete
sweeps the invoice's own slots and restores the `credit-note` flag.
It does not restore `gnc-mcp/applies-to-invoice`. Post + unpost a
credit note created with `applies_to_invoice_id` and the link is gone
(`is_credit_note` survives). CONFIRMED. Fix: snapshot both slots before
the delete, restore both; lock with a post/unpost round-trip test.

## Class 4 — three callers never migrated to `_split_prefix_map`

Release-review finding 8 made `_split_prefix_map` (_base.py:1454) a
single indexed query, mtime-cached. Three callers still build the map
by walking `book.transactions → t.splits`, one lazy load per
transaction. Measured on the Alex book (1,940 transactions, 4,147
splits), SQL statements per call:

| Call | Queries | Of which the prefix walk |
|------|---------|--------------------------|
| `get_unreconciled_splits(compact=True)` reconciliation.py:290-293 | 3,140 | ~1,940 |
| `get_unreconciled_splits(compact=False)` (baseline) | 1,199 | 0 |
| `set_reconcile_state` reconciliation.py:119-122 | 1,964 | ~1,940 |
| `get_lot` investments.py:1170-1173 | 1,955 | ~1,940 |
| `get_transaction` (uses the cached map) | 129 | — |

CONFIRMED. The remaining ~1,200 baseline on `get_unreconciled_splits`
is a second head: the sort key `s.transaction.post_date` lazy-loads
every split's transaction; the account-scoped keepalive query in
`enter_statement` (core.py:4361-4367) is the template for that one.

## Class 5 — smaller confirmed items

- **Scheduled template accepts a placeholder account** then fails at
  every instantiation (scheduling.py:318 validates via the shared
  validator but never checks `placeholder`; `create_transaction`
  rejects at write time). Same shape as the all-foreign-leg bug the
  docstring at scheduling.py:313-317 says it fixed. CONFIRMED.
- **Batch rows with an empty `date` cell default to today silently**
  (tools/core.py:80) with no echo in the results row, while the
  retired single create echoed `date` when defaulted and
  `enter_statement` refuses an empty date outright. CONFIRMED.
- **`create_billterm` allows duplicate names** (business.py:2865);
  every term lookup is by name with `.first()`, so the second
  "Net 30" is unreachable and which one a document gets is row
  order. CONFIRMED.

## Class 6 — by inspection only

- `delete_transaction` has no lot-membership guard
  (core.py:5761-5794) while `replace_splits` requires `force` for
  the same (core.py:6583-6590). Deleting a lot-assigned buy or an
  invoice payment silently changes cost basis / reopens the invoice.
- `replace_splits` re-implements `_validate_transaction_splits`
  inline (core.py:6459-6466 and 6604-6627) — the sum-to-zero,
  quantity, and sign rules exist in two private forms.
- `_runway_metrics` hand-rolls the cost-basis fallback
  (core.py:1458-1469) instead of calling `_market_value`; it skips
  the per-leg FX conversion the shared helper does and, like
  `_market_value:804`, compares a possibly-NULL `post_date` to today
  (old-book artifact → TypeError inside `get_book_summary`).
- `vendor_spending_report`'s docstring promises "converted ... at
  period-end rates" (business.py:8016-8021); the code reads the
  posting ledger at posting-date rates (7809-7843), which is the
  better behavior. The docstring lies.
- Five copies of the `owner_type = party_type if party_type else
  {...}.get(...)` block in tools/business.py (779, 826, 876, 917,
  1028); five copies of the SX unpack (recurrence → frequency,
  start/end/last → `_next_occurrence(after=today-1)`) across
  scheduling.py and core.py:653. Both are past the third-duplicate
  trigger.
- GUID-omission convention violated on `update_taxtable` /
  `delete_taxtable` (business.py:3578, 3638), `_delete_invoice_or_bill`
  (6915), verbose `list_lots` / `get_lot`, and `_budget_to_dict`.
  Cosmetic.
- Flow reports and the dashboard's budget/burn/monthly-net passes do
  not apply `_is_voided`; the stock reports do. Only bites on the
  partial-void corruption shape `_own_splits_balance` documents.
- `search_transactions` (core.py:5266) and `_signal_sweep`
  (core.py:2935) call `.lower()` on `transaction.description`, which
  can be NULL on desktop-created books; `enter_statement` guards the
  same read with `or ""`.

## Did loading everything help?

Yes, and only for the class the letters predicted. Every CONFIRMED
item above is a chokepoint that exists and a sibling site that
bypasses it, spread across two to four files. A file-scoped reader
sees `_find_vendor_by_guid(book, inv.owner_guid)` and has no way to
know `owner_guid` can be a Job; sees `except (ValueError, KeyError)`
and has no way to know `_to_decimal` raises something else; sees the
fixed `_split_prefix_map` and never visits the three callers. The
local layer is clean — the eight-angle agent reviews did that work.
The whole-tree pass found nothing local and everything relational.

## Branch status — `fix/document-owner-resolution` (2026-09-04)

Five commits, each with behavioral locks verified by mutation and
the full suite green before commit. Merged to develop as PR #171
(`f84932e`, 2026-09-04); the bookkeeper items in its test plan are
still open.

| Class | Commit | Locks |
|-------|--------|-------|
| 1 owner resolution (all seven sites, plus `delete_employee`'s missing voucher guard) | `ed82d3f` | `tests/test_owner_resolution.py` |
| 2 `_to_decimal` exception type | `95aa182` | `tests/test_float_precision.py` (new classes at the end) |
| 3 credit-note `applies_to` across unpost | `2cbd1c6` | `tests/test_business.py::TestCreditNoteUnpostKeepsAppliesTo` |
| 4 split-prefix map + account preload (three sites, bulk reconcile, and the register view found while measuring) | `023c230`, `385b521` | `tests/test_query_budgets.py` |

Alex sample, SQL statements per call, before → after:

| Call | Before | After |
|------|--------|-------|
| `get_unreconciled_splits` (compact) | 3,140 | 33 |
| `set_reconcile_state` | 1,964 | 24 |
| `get_lot` | 1,955 | 14 |
| `list_transactions(account=…)` | 1,351 | 84 |

Still open from this review: class 5 (placeholder templates, batch
empty-date echo, billterm duplicate names) and the class-6
inspection items. `get_transaction` at ~127 statements per call
(three cold prefix maps after any write) was noted, not addressed.

## Branch status — `fix/class-5-review-items` (2026-09-04, post-compaction)

Three commits from develop `f84932e`, one per class-5 item, each
lock watched failing under mutation, full suite 2205 green before
commit. Pushed; open as PR #172 against develop.

| Item | Commit | Lock |
|------|--------|------|
| H1 placeholder legs in scheduled templates | `9fb0649` | `test_scheduled.py::TestCreateScheduled::test_placeholder_leg_refused_at_create` |
| L1 blank date cell in `create_transactions` (rejects, matching `enter_statement`; the docstring never promised a default) | `154d5c8` | `test_batch_transactions.py::test_empty_date_cell_rejects` |
| I1 duplicate billterm names | `eef2ec7` | `test_business.py::TestCreateBillterm::test_duplicate_name_refused` |

`verify_whole_tree_findings.py` lines H1, L1, I1 all read REFUTED on
this branch.

Bookkeeper battery (`specs/v1.5/testing/BOOKKEEPER_REPORT_PR172.md`):
all three items pass, the #171 owed items pass, two follow-ups
landed on the same branch — one refusal naming every delete blocker
with the employee copy no longer offering credit notes (`d8d6331`), and
every account miss routed through `_account_not_found_error`
(seventeen sites across seven modules, source-grep locked, `d4c35a0`).

## For the next instance

PR #171 is merged; the bookkeeper loop on its test plan is still
owed. For the remaining items, branch fresh from develop. Before
anything: `git fetch --prune`, confirm develop has not
moved, run the suite with the exit code captured to a file (never
piped through tail). Two behavior changes to name in the test plan:
`delete_customer` / `delete_vendor` now refuse when the party has a
bare job (message points at `delete_job`), and `delete_employee`
refuses with vouchers.

Class-5 items — DONE on `fix/class-5-review-items` (see the branch
status above); kept here for the record of what each was:

1. **Placeholder templates** (scheduling.py, `create_scheduled_transaction`
   after the `_validate_transaction_splits` call ~line 318): raise
   `self._placeholder_error(v["account"])` for any validated split on a
   placeholder, mirroring `create_transactions` phase 1. Test: create
   with `Expenses` as a leg → ValueError mentioning placeholder.
2. **Batch empty-date echo** (tools/core.py:80 and
   `create_transactions` results): either reject an empty `date` cell
   like `enter_statement` does, or carry the resolved date in the
   results row. Stephen's call which; the asymmetry is the finding.
3. **Duplicate billterm names** (business.py `create_billterm`): check
   `Billterm.name == name AND invisible == 0` before the insert; raise
   naming the existing term.

Verify each with the class-5 lines of
`verify_whole_tree_findings.py` (H1, L1, I1 flip to REFUTED).

Class-6 inspection items are listed above; the two worth a branch
of their own are the `delete_transaction` lot guard and the
`replace_splits` re-implementation of the validator.
