# Code review v1.3 — verification of miscellaneous claims

Adversarial verification pass over six claims spanning state-loss,
agreement, math, and completeness shapes. Each claim was read against
the live code at the cited lines; verdicts below.

---

## Claim S-1 (state F1) — `create_transaction_from_scheduled` data loss shape

### Verdict
**CONFIRMED.**

### Reasoning
At `book/scheduling.py:620-707` the method opens two sequential
sessions:

- **Session 1** (line 620, `with self.open(readonly=False) as book`):
  resolves the scheduled txn, computes the next occurrence date,
  validates the splits exist, then mutates
  `sx.last_occur = max(last, txn_date)` and
  `sx.instance_count += 1`, calls `book.save()` (line 692), and
  exits the context — **committing** the schedule advance.

- **Session 2** (line 695, `self.create_transaction(...)`): a separate
  call. `create_transaction` opens its own session at
  `book/core.py:3097` (`with self.open(readonly=readonly) as book`).

Nothing wraps the call at line 695 in a try/except, and there is no
compensating reversal of `last_occur` / `instance_count` if
`create_transaction` raises. `create_transaction` has multiple raise
paths reachable after schedule-side commit:

- `len(splits) < 2` → ValueError (`core.py:3128`)
- Placeholder account → ValueError (`core.py:3204`)
- `_validate_transaction_splits` failures (sum-to-zero, account
  resolution, cross-currency sign/quantity, currency-not-found)
- Any `book.save()` failure on the second open (write lock,
  IntegrityError on malformed split, FK violation if a referenced
  account was deleted between sessions)

`create_transaction` can also return `{"status": "rejected"}` with no
exception in the HIGH-duplicate path (`core.py:3164-3169`). In that
case session 1 has committed and session 2 has *intentionally not*
written — the schedule advanced, no transaction was created, and
re-running advances again. This is the most plausible production
trigger because the duplicate detector is on by default.

### Concrete reproduction
1. Build a scheduled monthly txn (Rent: Checking -1500, Expenses
   +1500) with `last_occur = None`. Call
   `create_transaction_from_scheduled(guid=...)`. Succeeds — Rent
   txn for today exists, `last_occur` set to today,
   `instance_count = 1`.
2. Manually create another Rent transaction on the same date (or
   leave the just-created one in place).
3. Call `create_transaction_from_scheduled(guid=..., transaction_date=tomorrow)`.
   Internally:
   - Session 1: advances `last_occur` to tomorrow,
     `instance_count = 2`, commits.
   - Session 2: `create_transaction(...)` runs duplicate detection,
     finds the recent Rent, returns `{"status": "rejected",
     "reason": "duplicate_detected"}`.
4. **Permanent state:** `last_occur=tomorrow`, `instance_count=2`,
   but only ONE Rent transaction in the book. Re-running with
   `transaction_date=tomorrow+1day` would advance again. The missed
   month is permanently lost — the schedule will never re-emit it.

Alternative trigger via raise: between sessions, a concurrent
session (or even just GnuCash desktop with the book in
multi-user mode) deletes the Checking account. Session 1 commits
fine. Session 2 raises in `_validate_transaction_splits`. Same
outcome.

### Remediation sketch
Either (a) collapse to one session — `create_transaction` accepts an
already-open `book` argument or a `defer_save` flag — or (b) wrap
session 2 in try/except and re-open session 1 to roll back
`last_occur` / `instance_count` on failure. (a) is simpler and
matches the rest of the codebase's "one book open per write"
invariant.

---

## Claim S-2 (agreement A-3) — dashboard headline doesn't roll up; budget report does

### Verdict
**CONFIRMED.**

### Reasoning
`_budget_headline` at `book/core.py:1160-1200`:

```python
budgeted_account_guids: set[str] = set()
for ba in budget.amounts:
    total_budgeted += Decimal(str(ba.amount))
    budgeted_account_guids.add(ba.account.guid)
...
for s in txn.splits:
    if s.account.guid not in budgeted_account_guids:
        continue
    ...
```

The actuals loop matches splits **only on the GUIDs of accounts
that have a direct BudgetAmount row**. No descendant collection.

`get_budget_report` at `book/budgets.py:776-789` builds a
`rollup_map` that traverses each budgeted account's descendants
(via `_collect_descendants`) and maps each descendant fullname to
its nearest budgeted ancestor (preferring the longer ancestor path
when nested). Then at line 813 it consults `rollup_map` to attribute
each child split to its parent's bucket.

The dashboard headline therefore reports zero actuals against any
placeholder-parent budget whose actuals live in non-budgeted
children — exactly the pattern PR #46 (v1.2.1) fixed in the
report. CLAUDE.md's release-note narrative confirms PR #46's scope
was the report; the headline was extracted into `_budget_headline`
independently and never carried the rollup.

### Concrete reproduction
Set up Alex's "Utilities" budget (placeholder parent
`Expenses:Utilities`, $200/mo, with children `Electric`, `Gas`,
`Water` carrying the actual spend):

1. `set_budget_amount(budget_name="2026", account="Expenses:Utilities", amount=200)`
2. `create_transaction("Electric Co", splits=[
     {"account": "Assets:Checking", "amount": -120},
     {"account": "Expenses:Utilities:Electric", "amount": 120}])`
3. `get_budget_report(budget_name="2026", period="ytd")` →
   reports $120 used against the $200 Utilities budget (correct,
   thanks to rollup).
4. `get_book_summary()` → budget headline section reports `0%
   used` for Utilities, because Electric's GUID is not in
   `budgeted_account_guids`.

Cross-tool disagreement: `0%` (dashboard) vs `60%` (report) for
the same budget on the same day.

### Remediation sketch
Extract a shared `_build_rollup_map(budget)` helper on the
budgets mixin and call it from both `_budget_headline` and
`get_budget_report`. The cross-currency factor handling is
already shared; the rollup map is the last divergence.

---

## Claim S-3 (math C-4) — runway burn always divides by 180 days

### Verdict
**CONFIRMED.**

### Reasoning
`_daily_expense_burn` at `book/core.py:1222-1264`:

```python
def _daily_expense_burn(self, book, transactions, days=None):
    if days is None:
        days = self._RUNWAY_BURN_DAYS  # = 180 (line 568)
    today = date.today()
    window_start = today - timedelta(days=days)
    ...
    for txn in transactions:
        if txn.post_date < window_start or txn.post_date > today:
            continue
        for s in txn.splits:
            if s.account.type == "EXPENSE":
                expenses += self._split_in_default_currency(...)
    return expenses / Decimal(days)
```

The division is unconditional: total expense over the window /
180. There is no clamping to "days since first transaction" or
"days since book inception." A book that has existed for 19 days
with $3000 of expenses sums $3000 (every txn falls in the
180-day window because the window extends 161 days before book
inception), then divides by 180 → $16.67/day burn. The true
~19-day burn rate of $158/day is understated by ~9.5×.

Runway = liquid_assets / daily_burn. Under-stated burn →
over-stated runway by the same factor. A new user with $5000 of
liquid assets and $3000 spent in 19 days has a true runway of
~13 days. The dashboard would report $5000 / $16.67 = 300 days.
The "<60 days ⚠" warning never fires.

This is the inverse of the bug shape a new user needs:
exactly when their book is youngest and the signal is least
trustworthy, the system tells them they're fine when they're not.

### Concrete reproduction
1. Create a fresh book today (2026-06-03). Default-currency USD.
2. Open Checking: $5000 balance (one offsetting equity split).
3. Over 19 days, create $3000 of expense transactions.
4. Call `get_book_summary()`. Runway section reports
   `300 days` (no warning).
5. True burn ($3000 / 19 = $158/day) gives 32 days runway,
   which would trip the warning threshold.

### Remediation sketch
Track `book_age_days = min(_RUNWAY_BURN_DAYS, (today -
first_post_date).days + 1)` and divide by that. Or skip
emitting the runway section when fewer than N days of
activity exist (signal not yet reliable).

---

## Claim S-4 (math C-2) — `cash_flow` double-counts internal transfers (gross only)

### Verdict
**CONFIRMED** for gross figures (`inflows`, `outflows`). The
`account` argument case (single named account) is unaffected
because it filters to one account's splits.

### Reasoning
`cash_flow` at `book/reporting.py:737-800` default mode (no
`account` argument) calls `_query_filtered_splits(
account_types=_CASH_TYPES)` where `_CASH_TYPES = frozenset(
{"BANK", "CASH"})` (line 49).

`_query_filtered_splits` (`book/_query.py:90-91`):

```python
if account_types is not None:
    q = q.filter(Account.type.in_(list(account_types)))
```

Returns every Split row whose Account is BANK or CASH. There is
no transfer-detection logic — the loop at lines 778-785
unconditionally accumulates positive amounts into `inflows` and
negative amounts into `outflows`.

A bank-to-bank transfer (Checking $1000 → Savings $1000) creates
two splits, both on BANK accounts:

- Checking: value = -1000 (BANK)
- Savings:  value = +1000 (BANK)

Both pass the `account_types` filter. Result: `inflows += 1000`
and `outflows += 1000`. Net is correct (cancels), but gross
figures are inflated by every internal sweep — paycheck
auto-deposit to high-yield savings, monthly cash-to-checking
sweeps, credit-card-payment-funded transfers all show as both
income and outflow.

The single-account case (`account=...`) sets `account_guids` to
the one GUID; only that account's splits are returned, so
internal transfers from that account show as outflows but the
receiving side is correctly absent (filtered out).

### Concrete reproduction
1. On Alex's book, create a $1000 transfer:
   `create_transaction("Sweep to savings", splits=[
     {"account": "Assets:Checking", "amount": -1000},
     {"account": "Assets:Savings", "amount": 1000}])`
2. `cash_flow(start_date="2026-06-01", end_date="2026-06-30")`
   → inflows includes the $1000 (Savings side), outflows
   includes the $1000 (Checking side).
3. Net is correct ($0 from this transfer) but gross figures
   inflate by $1000 each.

For Alex's full June 2026 with paycheck + sweeps: paycheck
$5000 (income → checking +5000) and a $2000 sweep to savings
should report `inflows=5000, outflows=<expenses>`. Actual:
`inflows=5000 + 2000 = 7000`, `outflows=<expenses> + 2000`. Net
still matches but the gross-flow narrative is misleading.

### Remediation sketch
Detect intra-cash transfers by grouping splits by transaction
and checking whether all splits on a transaction are within
`_CASH_TYPES` (or more strictly: the cancellation amount on
the other side is also a cash-type account). For those
transactions, exclude them from both totals. Alternative:
sum-of-net-per-cash-account approach (each cash-account
contribution to the period is `sum(splits.value)` for that
account; inflows = sum of positive contributions across
accounts, outflows = sum of negative contributions).

---

## Claim S-5 (completeness) — "every write is verified" invariant is false

### Verdict
**CONFIRMED.**

### Reasoning
Per-file counts of `book.save()` vs `_verify_*`:

| File | save() | _verify_* |
|------|--------|-----------|
| `admin.py` | 2 | **0** |
| `investments.py` | 7 | **0** |
| `reconciliation.py` | 4 | **0** |
| `core.py` | 10 | 3 |
| `business.py` | 20 | 23 |
| `budgets.py` | 3 | 7 |
| `scheduling.py` | 5 | 8 |
| `backup.py` | 0 | 0 |
| `_base.py` | (helpers) | 4 (definitions) |

Three mixin files — admin, investments, reconciliation — have
ZERO verify calls. Specific high-impact unverified write sites:

- **`delete_account`** (`core.py:3704-3756`). Calls
  `book.session.delete(account); book.save()` at lines 3753-3754.
  No verify. (A round-trip "account.guid is no longer found"
  check would be the natural shape — `_verify_delete` exists in
  `_base.py` and is used in `business.py` for customer/vendor/
  employee deletion.)

- **`pay_invoice`** (`business.py:5604`...). Final `book.save()`
  at line 6107. No verify on the resulting transaction or
  lot-state diff. The `apply_credit_note` path (line 6498)
  same shape.

- **`unpost_invoice`** (`business.py:5450`...). Final
  `book.save()` at line 5590. No verify confirming the txn /
  lot deletion took.

- **`reconcile_account` / `set_reconcile_state` / void/unvoid**
  (`reconciliation.py`). Four save sites, zero verify.

- **`create_lot` / `assign_split_to_lot` / `close_lot` /
  `create_commodity` / `create_prices`** (`investments.py`).
  Seven save sites, zero verify.

- **`set_account_slot` / `delete_account_slot`** (`admin.py`).
  Two save sites, zero verify.

I checked whether the `@audit_log` decorator's `_consume_audit_before`
path performs verification — it does not. `grep _verify_
src/gnucash_mcp/logging_config.py` returns no matches. The decorator
captures before-state and renders after-state, but no round-trip
read-back is performed at the decorator layer.

CLAUDE.md's invariant statement ("Every write is verified.
`_verify_write` / `_verify_composite_write` read back what was
written and raise if the round-trip doesn't match") therefore
overstates the actual coverage. The invariant **holds for
business.py composite writes** (raw-SQL inserts must round-trip
because piecash's ORM constructors are blocked there) but is
**not enforced** as a project-wide property.

### Concrete reproduction
Add a write tool to admin.py or investments.py that silently
corrupts state (e.g. forget to call `book.flush()` before save,
or assign an attribute to a detached instance). No verify
fires. The write would silently no-op (or partially apply) and
return success. The audit log would record the intended
parameters. The bug would surface only on the next read.

Empirically: this happened in v1.2.1 (the `replace_splits`
shortcut-verifier false-negative the bookkeeper caught). The
verifier in `_base.py` exists; the rule "call it after every
save" is enforced by convention and code review, not by
structure.

### Remediation sketch
Two paths:
1. Lift verification into the `@audit_log` decorator. Generic
   round-trip: re-fetch the entity by `after_state.guid` and
   raise if it doesn't match the response shape.
2. Add a per-mixin contract test that asserts every
   `book.save()` site in a mixin file is preceded or followed
   by a `_verify_*` call. Lint via AST walk.

Either should be a v1.3.x item before declaring the invariant
in CLAUDE.md as written.

---

## Claim S-6 (completeness) — six (entity_type, operation) audit pairs missing handlers

### Verdict
**CONFIRMED**, with one refinement: the missing-handler entries
don't render with an "empty diff" — they emit **nothing at all**
to the audit log. The debug log still captures the entry.

### Reasoning
`_AUDIT_HANDLERS` dispatch table at `logging_config.py:1643-1708`.
Checking the six pairs:

| (entity_type, operation) | In _AUDIT_HANDLERS? | Emitted by a tool? |
|---|---|---|
| `commodity:CREATE` | No | Yes — `tools/investments.py:37` |
| `price:CREATE` | No | Yes — `tools/investments.py:69` |
| `lot:CREATE` | No | Yes — `tools/investments.py:220` |
| `lot:UPDATE` | No | Yes — `tools/investments.py:287, 338` |
| `scheduled_transaction:CREATE` | No | Yes — `tools/scheduling.py:18` |
| `budget:CREATE` | No | Yes — `tools/budgets.py:53` |

All six tool sites decorate with `@audit_log(classification="write", ...)`
so they reach the dispatcher. The dispatcher falls through to
`return ""` at `logging_config.py:1881`. The decorator's main
loop at line 2128-2131 gates on truthy text:

```python
text_entry = _format_audit_entry_text(entry)
if text_entry:
    logger.info(text_entry)
    logger.info("")
```

Empty string → nothing written to audit logger. The debug logger
still records the response metadata via line 2122-2125.

The claim "entry shows but the diff is empty" is **slightly more
generous than reality**: the audit log shows nothing — not even
a header line with the empty diff. From a human reviewing the
audit text file, these six write operations are invisible.

### Concrete reproduction
1. Tail the audit log file.
2. Call `create_lot(account="Assets:Investments:Vanguard:VTSAX",
   title="2026-06-03 buy")`. Verify the lot is created (via
   `list_lots`).
3. The audit log shows **no entry** for the lot creation. The
   debug log (`debug.log` or equivalent) shows a single
   `MCP response: tool=create_lot status=success ...` line but
   no human-readable lot-create entry.
4. Same shape for the other five operations.

This matters for the project's audit-log-as-human-readable-surface
goal: write operations that create commodities, prices, lots,
scheduled transactions, or budgets are entirely missing from the
historical record. The bookkeeper review loop reads this log.

### Remediation sketch
Add six small formatters to `logging_config.py` following the
existing pattern (`_fmt_lot_create`, `_fmt_budget_create`, etc.)
and add their `(entity_type, operation)` rows to `_AUDIT_HANDLERS`.
Each formatter is ~6-10 lines; total addition is small. There's
also room for a contract test
(`tests/test_audit_log_completeness.py` or similar) that
enumerates every distinct `(entity_type, operation)` pair
appearing in any `@audit_log(classification="write", ...)` call
across `tools/` and asserts each has a handler entry — the
same pattern used by `TestToolFileVsModulesMapping` for the
module registry.

---

## Summary

All six claims **confirmed**.

| Claim | Verdict | Severity |
|---|---|---|
| S-1 data loss in `create_transaction_from_scheduled` | CONFIRMED | High — silent data loss on duplicate-detected runs |
| S-2 dashboard/report budget rollup disagreement | CONFIRMED | Medium — cross-tool disagreement on placeholder-parent budgets |
| S-3 daily burn always /180 | CONFIRMED | High — runway warning suppressed on new books, exactly when it's needed |
| S-4 `cash_flow` double-counts transfers | CONFIRMED (gross only) | Medium — net correct, gross figures misleading |
| S-5 "every write verified" invariant false | CONFIRMED | Medium — invariant overstated in CLAUDE.md; admin/investments/reconciliation have zero verify calls |
| S-6 six audit-log handler gaps | CONFIRMED | Low-Medium — entry suppressed entirely (worse than claimed empty diff), affects audit trail completeness |
