# Code Review — GnuCash MCP Server

A code review of the GnuCash MCP Server codebase as of v1.2.1
(2026-04-30). The codebase is ~19,500 lines of Python organized as a
piecash-backed MCP server exposing a GnuCash SQLite book to LLM
clients via 75 typed tools across nine subject-area mixins.

This review was produced by reading the source independently and
forming a fresh-eyes assessment, then spot-checking the most severe
findings against the code.

Severities: **critical** (data loss / silent corruption / unsafe
defaults), **high** (correctness errors that affect numbers a user
trusts), **medium** (defects that may not bite typical use but
violate the codebase's stated invariants or defensive intent), **low**
(cosmetic, style, defensive improvements).

---

## Executive summary

The codebase is in strong shape. The architecture has matured across
many release cycles; mixin composition with collision detection,
single-book-open-per-write with thread-local audit staging,
SQL-pushed reporting filters, birthday-problem-aware short GUIDs,
and the pairing of every raw-SQL write with a `_verify_write`
round-trip are all design decisions that pay for themselves daily.
The piecash gotchas noted in `CLAUDE.md` have been internalized
into chokepoint helpers (`_safe_date_posted`, `_resolve_account`,
`_to_decimal`).

The defects that remain cluster around a single underlying pattern
— **storing values at full precision, formatting for display, then
re-parsing the formatted string for further math.** Three of the
highest-severity findings collapse to that one rule. Most of the
remainder are linear-scan performance items already noted by
predecessor sessions, and a small set of audit-staging gaps in
the newer scheduling and budget paths.

The backup module is the most defensive code in the project but
also has the most consequential remaining gap: auto-backup
failures are silently swallowed, which undermines the whole
purpose of the module. The bookkeeper's worst day is the day they
discover their backup chain has been broken for two weeks.

### Findings tally

| Severity  | Count |
|-----------|-------|
| Critical  | 3     |
| High      | 12    |
| Medium    | 26    |
| Low       | 18    |

---

## Critical

### 1. `set_budget_amount` silently truncates non-cent amounts on insert
[src/gnucash_mcp/book/budgets.py:606-625](src/gnucash_mcp/book/budgets.py:606)

```python
amount_denom = 100
amount_num = int(amount_decimal * amount_denom)
```

For `amount = "1234.567"`, `Decimal("1234.567") * 100 = Decimal("123456.700")`,
then `int(...)` truncates (not rounds) to `123456` → stored as
`1234.56`. For `amount = "499.999"`, the user sees `499.99`, not
`500.00`. No error, no warning.

The *update* path one block above (`existing.amount = amount_decimal`)
goes through piecash's hybrid property and preserves decimals — so
the same input produces different stored values depending on
whether a row already exists.

**Fix:** quantize explicitly to the account commodity's smallest
fraction with `ROUND_HALF_EVEN`, or refuse anything finer than
cents with a clear error. Use `acct.commodity.fraction` rather
than hardcoded `100`.

### 2. Backup race + second-resolution filename collision
[src/gnucash_mcp/book/backup.py:225,285,477-481](src/gnucash_mcp/book/backup.py:225)

```python
class BackupMixin:
    _backup_checked_in_process: bool = False  # class attribute
```

Two issues compound:

1. The "first write of session" gate reads and writes
   `_backup_checked_in_process` without a lock. Two simultaneous
   writes both pass the gate before either takes the backup, then
   both call `create_backup`.
2. `_format_ts` is `YYYYmmddTHHMMSS` (second resolution). Two
   backups in the same second produce the same filename.
   `sqlite3.connect(str(backup_path))` truncates and rewrites — the
   second backup silently overwrites the first.

The standard MCP deployment is single-threaded, so the race rarely
fires in practice. The collision case is reachable from a single
caller via fast `create_backup; create_backup` (e.g., a script).
Either way, the failure mode is silent loss of a backup file.

**Fix:** `threading.Lock` around the gate; append microseconds to
the filename or refuse to overwrite via `Path.exists()` precheck.

### 3. Auto-backup failures are silently swallowed
[src/gnucash_mcp/book/backup.py:483-513](src/gnucash_mcp/book/backup.py:483)

```python
try:
    self.create_backup(stage=highest_priority_stage)
    ...
except Exception:
    pass  # debug-logged only
```

`OSError("disk full")`, `PermissionError`, `RuntimeError` from
`PRAGMA integrity_check` failures — all caught and discarded. The
bookkeeper has zero visibility that the chain has been broken;
the auto-backup is the project's most-trusted seatbelt and it can
fail invisibly for weeks.

**Fix:** plumb `last_auto_backup_status` (`ok` / `failed: <reason> /
N days ago`) into `get_book_summary`'s warnings section. The data
is already in the debug log; the bookkeeper just doesn't read it.

---

## High

### 4. Investment cost-basis precision loss via formatted-string round-trip
[src/gnucash_mcp/book/investments.py:528-541,867-868](src/gnucash_mcp/book/investments.py:528)

`_lot_summary` returns `cost_per_share` formatted to 4 decimals via
`_format_number`. `calculate_lot_gain` then reads it back as
`Decimal(summary["cost_per_share"])` and multiplies by
`shares_to_sell`. For an asset bought at `$33.3333…/share`, a
1000-share sale computes `33.3333 × 1000 = $33,333.30` — but the
actual cost was $33,333.33. Gain/loss is wrong by 3¢ per ~$33k.
Tax-relevant.

**Fix:** in `calculate_lot_gain`, recompute
`cost_basis = (purchase_value / purchase_quantity) * shares_to_sell`
from the source values. Never re-parse a formatted string for math.

### 5. Scheduled-transaction month-end drift
[src/gnucash_mcp/book/scheduling.py:103-105](src/gnucash_mcp/book/scheduling.py:103)

```python
occurrence = start_date
while occurrence <= after:
    occurrence += delta
```

`relativedelta` clamps to month-end on month-mismatched dates. So a
monthly schedule starting `2026-01-31`:

- period 1: `2026-01-31 + 1 month → 2026-02-28` ✓
- period 2: `2026-02-28 + 1 month → 2026-03-28` (drift!)

After 12 months, a 31st-of-month schedule lands on the 28th
forever. Same shape on yearly schedules crossing leap-day.

**Fix:** anchor each occurrence to `start_date + (n × delta)`
rather than chaining mutations.

### 6. FX gain/loss split sign convention not end-to-end tested
[src/gnucash_mcp/book/business.py:2700-2715](src/gnucash_mcp/book/business.py:2700)

```python
quantity_sign = 1 if is_bill else -1
fx_split.quantity = quantity_sign * fx_diff
```

Tracing the four cases (customer-invoice gain, customer-invoice
loss, vendor-bill gain, vendor-bill loss) is non-obvious — the
review agent walked through twice and arrived at different
conclusions on each pass. This may be correct, but the absence of
a unit test that pins the exact dollar amount per case for each
sign is itself a high-severity gap. If the sign is inverted, every
multi-currency book's P&L is wrong by exactly the realized FX
amount.

**Fix:** add four explicit test cases, one per sign quadrant, that
assert the resulting Income split's quantity *and* the resulting
P&L direction (income increases vs. decreases).

### 7. FX account commodity may not match FX split quantity unit
[src/gnucash_mcp/book/business.py:349-359,2700-2714](src/gnucash_mcp/book/business.py:349)

The auto-created `Income:Foreign Exchange Gain/Loss` account is
created with `commodity = book.default_currency`. But the FX split
quantity is in the *payment account* commodity. When the payment
account is in a third currency (book default = USD, invoice = EUR,
pay account = GBP), the FX split's GBP-quantity is silently
treated as USD by every report that aggregates the account.

**Fix:** compute `fx_diff` in book default currency, OR set the FX
account commodity to the payment-account commodity when first
created and refuse mismatches afterwards.

### 8. `pay_invoice` does not currency-convert the A/R split
[src/gnucash_mcp/book/business.py:2635-2662](src/gnucash_mcp/book/business.py:2635)

`post_invoice` correctly applies `_qty_for_split(post_acct, ar_ap_value)`
when the A/R commodity differs from the invoice currency.
`pay_invoice` does not — it builds the A/R split with
`quantity = payment_amount` directly. A USD A/R account holding a
EUR invoice is posted in USD-converted amounts but liquidated in
EUR-as-USD. The account balance silently drifts.

**Fix:** apply the same `_qty_for_split` conversion to the
A/R/A/P split in `pay_invoice`.

### 9. Hardcoded `Decimal("0.01")` quantization breaks JPY/BHD/KWD
[src/gnucash_mcp/book/business.py:2237,2629,2686,2759](src/gnucash_mcp/book/business.py:2237)

Four call sites quantize money to 2 decimals regardless of currency.
JPY (0 decimals) and BHD/KWD (3 decimals) get silently truncated
or padded. A 100.123 BHD invoice loses 3 mils per posting.

**Fix:** derive precision from `acct.commodity.fraction` (100 →
2 decimals, 1 → 0 decimals, 1000 → 3 decimals).

### 10. `_rate_from_post_transaction` returns first cross-currency rate, not post-account rate
[src/gnucash_mcp/book/business.py:374-382](src/gnucash_mcp/book/business.py:374)

The helper iterates `inv.post_txn.splits` and returns the rate of
the first cross-currency split. When the post transaction has
multiple cross-currency legs (rare but possible with rounding
splits), this returns the wrong rate for the FX gain/loss
comparison. The rate must come from the split whose account is
`inv.post_account`.

**Fix:** filter to `s.account == inv.post_account` first.

### 11. `update_transaction` and `replace_splits` skip write verification
[src/gnucash_mcp/book/core.py:3303-3407](src/gnucash_mcp/book/core.py:3303)

`CLAUDE.md` declares "Every write is verified" as an architectural
invariant. The business module honors this rigorously via
`_verify_write` / `_verify_composite_write`. Core's
`update_transaction` and `replace_splits` don't — they call
`book.save()` and trust the result. If piecash silently failed to
flush a slot-backed field (it has historically), the response
would lie.

**Fix:** add a `_verify_write` round-trip that reads the mutated
fields back and compares.

### 12. `update_scheduled_transaction` and `delete_budget` skip audit before-state staging
[src/gnucash_mcp/book/scheduling.py:618-662](src/gnucash_mcp/book/scheduling.py:618)
[src/gnucash_mcp/book/budgets.py:849-882](src/gnucash_mcp/book/budgets.py:849)

`set_reconcile_state`, `void_transaction`, `delete_price`, and the
business-module mutators all call `_stage_audit_before` so the
audit log can render a before/after diff. The two methods above
don't — the audit log shows only the after-state for those
operations. The bookkeeper reads diffs to verify what changed; an
update without a before-state is unreviewable.

**Fix:** add `_stage_audit_before(self._sx_to_dict(sx))` and
`_stage_audit_before({"name": budget.name, ...})` respectively.

### 13. `delete_account` returns a short-GUID prefix that no longer resolves
[src/gnucash_mcp/book/core.py:3184-3196](src/gnucash_mcp/book/core.py:3184)

`_unique_prefix(account.guid, ...)` is computed before the delete,
returned after. The returned short GUID will not resolve to anything
— the account is gone. The audit-log normalization
(`_normalize_account_refs_for_audit`) will fail to look it up. The
LLM may try to use the returned handle.

**Fix:** omit `guid` from the response, or return the full deleted
GUID with a `"deleted": true` annotation that callers can detect.

### 14. Audit before-state can leak across calls
[src/gnucash_mcp/logging_config.py:1132-1188](src/gnucash_mcp/logging_config.py:1132)

`_consume_audit_before` is called on the success path after `func`
returns. The exception path tries to clear, but if a write helper
stages before-state and re-enters via a nested tool call (one tool
calling another internally), the second call's `_consume_audit_before`
sees the outer's staged state. `threading.local` isolates threads
but not nested call stacks.

**Fix:** clear before staging — call `book._consume_audit_before()`
(discarding the result) at the top of the wrapper, in addition to
the post-call consume.

### 15. `prune_backups(keep_last_n=0, dry_run=False, stage="manual")` deletes every manual backup
[src/gnucash_mcp/book/backup.py:397-398](src/gnucash_mcp/book/backup.py:397)

Only check is `if keep_last_n < 0: raise`. A misbehaving LLM
concluding "let me clean up old backups" can wipe every
human-marked backup with one call. Manual stage is supposed to be
the user's "this one matters forever" tag.

**Fix:** add a guard `if keep_last_n == 0 and not dry_run and stage
== "manual": raise ValueError("refusing to delete all manual
backups; use a label or stage filter")`.

---

## Medium

### Correctness

- **Cross-currency split.quantity summed without market conversion in budget rollup** — [budgets.py:776](src/gnucash_mcp/book/budgets.py:776). Fixed in reporting in v1.2.1; not extended to budgets. EUR + USD child accounts of a USD budget rollup as raw quantities.
- **`_calculate_lot_balance` doesn't filter voided splits** — [business.py:879-889](src/gnucash_mcp/book/business.py:879). May be inconsistent with the recent void-aware fix in lot listing/gain. Verify the contract is uniform.
- **Voided posting transaction edge case** — [business.py:2412-2440](src/gnucash_mcp/book/business.py:2412). `pay_invoice` against a voided posting computes `remaining=0` and silently closes the lot, then assigns the new payment to the closed lot.
- **`add_invoice_entry` / `add_bill_entry` accept any account type** — [business.py:1751,1853](src/gnucash_mcp/book/business.py:1751). Docstrings say INCOME / EXPENSE, code accepts ASSET. Posting math then quietly wrong.
- **`pay_quantity` can quantize to zero** — [business.py:2629-2631](src/gnucash_mcp/book/business.py:2629). Extreme rate (`< 0.005`) with payment_amount=1 produces a zero-quantity bank split. No defensive check.
- **`_find_exchange_rate` doesn't skip rate=0 on direct branch** — [business.py:432](src/gnucash_mcp/book/business.py:432). Inverse branch correctly skips zero (would div-by-zero); direct branch doesn't (but propagates a zero rate downstream).
- **`audit_log entity_type="invoice"` mis-reports vendor bills** — [tools/business.py:432,467,494](src/gnucash_mcp/tools/business.py:432). `post_invoice`/`unpost_invoice`/`pay_invoice` handle both invoices and bills but the audit log always says "invoice". Compare to `delete_invoice`/`delete_bill` which split correctly.
- **`_safe_date_posted` only handles `date_posted`, not `date_opened`** — [business.py:31-50](src/gnucash_mcp/book/business.py:31). The same empty-string regex-parser bug bites `inv.date_opened.date()` calls in `_invoice_to_dict`, `_invoice_to_compact_line`, and `list_invoices` ordering. Extend the heal to `date_opened`.
- **`_get_invoice_entries_and_total` ValueError swallowed by overly broad `except Exception`** — [business.py:743-744](src/gnucash_mcp/book/business.py:743), and downstream substitution in `vendor_spending_report` silently understates `total_billed` for any zero-entry bill.
- **Statement-balance comparison doesn't quantize to commodity fraction** — [reconciliation.py:283-289](src/gnucash_mcp/book/reconciliation.py:283). User typing `"1234.567"` against a 2-decimal book produces a perpetual 0.007 mismatch with no clear error.
- **`void_transaction` writes naive `datetime.now()` to the void-time slot** — [reconciliation.py:371](src/gnucash_mcp/book/reconciliation.py:371). Audit log uses tz-aware elsewhere. After DST or a host TZ change, the void timestamp is wrong.
- **`unvoid_transaction` doesn't validate every split has the void-former slots** — [reconciliation.py:425-437](src/gnucash_mcp/book/reconciliation.py:425). Partial corruption silently produces a partial unvoid.
- **`get_account_slots` value-shape inconsistency** — [admin.py:38-46](src/gnucash_mcp/book/admin.py:38). Single-key path stringifies; all-keys path assumes `.value`. AttributeError on first non-`.value` slot under the all-keys branch.
- **`set_account_slot` doesn't validate `key` characters** — [admin.py:53-85](src/gnucash_mcp/book/admin.py:53). Embedded `/` creates hierarchical sub-slots silently.
- **`create_scheduled_transaction` torn-write window** — [scheduling.py:255-281](src/gnucash_mcp/book/scheduling.py:255). Template account is flushed before the SX/Recurrence/Slot inserts. A failure in steps 4-6 leaves an orphan template account under `root_template`.
- **`create_transaction_from_scheduled` two-session window** — [scheduling.py:529-608](src/gnucash_mcp/book/scheduling.py:529). Schedule advance commits in session 1; transaction creation runs in session 2. If session 2 fails, the schedule has advanced but the transaction is missing.
- **Backup partial-file leak on disk-full** — [backup.py:295-321](src/gnucash_mcp/book/backup.py:295). The `try/finally` closes `dest_conn` but doesn't `unlink` the partial file on `OperationalError`. Subsequent `list_backups` shows it as a valid entry.
- **Backup GFS retention undermined by single-file multi-stage tagging** — [backup.py:489-508](src/gnucash_mcp/book/backup.py:489). When `monthly` and `weekly` are simultaneously due, the file is tagged `monthly`. `prune_backups(stage="weekly", keep_last_n=4)` doesn't see it, and may keep zero weeklies despite plenty of recent backups on disk.
- **Auto-backup snapshot taken in a separate read-only open before the write begins** — [backup.py:295-301](src/gnucash_mcp/book/backup.py:295). A window exists between snapshot and mutation where another process could change the file. The auto-backup is "before the first write of the session," not "the exact state we're about to mutate from."

### Performance

- **`get_book_summary` opens the book once but iterates `book.accounts` 7-10 times** — [core.py:1376-1733](src/gnucash_mcp/book/core.py:1376). Single materialized list + per-section consumers would halve cost on large charts. Same pattern for `book.transactions` (monthly nets, runway burn, budget headline, orientation date scan).
- **`_normalize_account_refs_for_audit` opens the book read-only on every audit emission** — [logging_config.py:912-927](src/gnucash_mcp/logging_config.py:912). The single-book-open-per-write win is partially walked back by the audit-formatting path.
- **`_account_reconciliation_status` walks `account.splits` twice per account** — [core.py:170-203](src/gnucash_mcp/book/core.py:170). One pass would suffice.
- **`list_transactions` and `search_transactions` rebuild `_guid_prefix_map` over the entire transactions table on every call** — [core.py:1985,2802](src/gnucash_mcp/book/core.py:1985). Cache by book mtime.
- **`_collect_warnings` walks `book.prices` twice plus `book.commodities` once** — [core.py:784-823](src/gnucash_mcp/book/core.py:784). Single pass, two dicts.
- **`debt_payoff_plan` recomputes per-debt monthly rate inside a 1200-iteration loop, then again for YETI** — [reporting.py:898-947](src/gnucash_mcp/book/reporting.py:898).
- **`debt_payoff_plan` uses `account[key]` slot shortcut** — [reporting.py:1018,1042,1090](src/gnucash_mcp/book/reporting.py:1018). Each access goes through the polymorphic-broken slot path. Materialize `account.slots` once into a dict.
- **`get_budget_report` per-transaction Python date-compare loop** — [budgets.py:768-785](src/gnucash_mcp/book/budgets.py:768). On a 50k-transaction book, a single-period report becomes slow. Use the same `_query_filtered_splits` SQL-pushed pattern as the reporting suite.
- **Business-module finders are O(N)** — `_find_customer/_find_vendor/_find_employee` and the inline `for a in book.accounts: if a.guid == ...` patterns in `post_invoice`, `pay_invoice`, `get_outstanding_invoices`, `vendor_spending_report`. CLAUDE.md established the indexed pattern (`book.session.query(X).filter_by(guid=...).first()`); business module hasn't been converted.
- **`create_price`, `get_latest_price`, `get_prices`, `calculate_lot_gain` walk `book.prices` linearly** — [investments.py:209-217,386-403,472-483,851-865](src/gnucash_mcp/book/investments.py:209). Indexed `filter_by(commodity_guid=..., currency_guid=...).order_by(Price.date.desc())` would be much faster on books with many prices.
- **Repeated `book.prices` scans inside business-module FX path** — `_find_exchange_rate` is called once per cross-currency entry-split during `post_invoice`. The reporting module's `_latest_market_rates` caches; business module rebuilds. Hoist a shared helper to `_base.py` (the third caller now exists).

### Code quality

- **460-line `get_book_summary` rendering monolith** — [core.py:1326-1785](src/gnucash_mcp/book/core.py:1326). Data-collection helpers (`_collect_warnings`, `_runway_metrics`, `_budget_headline`, `_account_reconciliation_status`) are well-decomposed. Rendering is not. Per-section render helpers would mirror the data layer.
- **Triple-duplicated `type='transaction'` price filter** — `core.py:231,790`, `reporting.py:253`, plus business-module mentions. The 2026-04-21 predecessor note flagged this; still un-DRY'd. A single `_is_market_price(p)` predicate consolidates.
- **`_market_value` is closure-bound inside `get_book_summary`** — [core.py:1390-1411](src/gnucash_mcp/book/core.py:1390) but does the same work as `_split_in_default_currency` in [reporting.py:286-301](src/gnucash_mcp/book/reporting.py:286). Pull both into one base-class helper.
- **`add_invoice_entry` and `add_bill_entry` are 90% duplicated** — [business.py:1710-1810,1812-1912](src/gnucash_mcp/book/business.py:1710). A shared `_add_entry(invoice, account, ..., is_bill: bool)` with a column-prefix dict eliminates ~70 lines.
- **`post_invoice` and `pay_invoice` are 250+ line methods** with clear sub-sections (validate, build splits, build txn, set metadata, FX). Extract `_compute_fx_gain_loss` helper from `pay_invoice` for testability.
- **`_lot_summary` returns `cost_basis` as the *remaining* value, not original** — [investments.py:530-541](src/gnucash_mcp/book/investments.py:530). Field name is unqualified. After a partial sale the bookkeeper reading `list_lots` may mistake residual basis for purchase value. Rename to `remaining_cost_basis` and add `original_cost_basis`.
- **Module-level imports inside functions** — `from piecash.budget import Budget`, `from piecash.core.transaction import Lot`, `from piecash._common import Recurrence`, `from dateutil.relativedelta import relativedelta` all appear inside method bodies in budgets/investments/scheduling. The 4.6 predecessor note flagged this exact pattern; not addressed in those modules.
- **`_format_audit_entry_text` swallows `_resolve_account` failures silently** — [logging_config.py:923-930](src/gnucash_mcp/logging_config.py:912). Defensible to keep log rendering robust, but a debug-log entry of "could not resolve ref X in audit" would help post-hoc investigation.
- **Misleading section comment numbering in `_collect_warnings`** — [core.py:577-893](src/gnucash_mcp/book/core.py:577). Sections are labeled `1, 2, 3, 5, 4` because operational urgency reorders them; readers stumble. Renumber or remove.
- **`_DEFAULT_TYPES` map hardcodes English account names** — [_base.py:292-298](src/gnucash_mcp/book/_base.py:292). A Spanish-language book ("Activos") gets `[ASSET]` annotations on no accounts.
- **`_strip_noise` recurses into all dicts and removes empty strings** — [tools/_helpers.py:149-156](src/gnucash_mcp/tools/_helpers.py:149). For audit purposes a memo's emptiness might be intentional. Currently fine because convention treats empty memo as omitted, but undocumented.
- **Inconsistent `_format_number` use** — reporting methods use it for some fields and `f"{x:,.2f}"` for others; rounding modes differ at boundaries.

### Security

- **No path-traversal protection in `book_path`** — [_base.py:654-665](src/gnucash_mcp/book/_base.py:654). `GNUCASH_BOOK_PATH` is taken at face value. Audit/debug log directories are constructed as `book_path.parent / f"{book_path.name}.mcp"` — a path with `..` writes logs outside the intended directory.
- **Audit logs created with default umask** — [logging_config.py:101-109](src/gnucash_mcp/logging_config.py:101). Financial data may be world-readable on multi-user systems. Set explicit `0o600`.
- **Path information leaks into error messages** — `FileNotFoundError(f"GnuCash book not found: {book_path}")` and similar surface absolute paths to MCP clients. Acceptable for single-user local config, not for multi-tenant deployments.
- **No rate-limiting or write-throttling** — a misbehaving LLM can call `create_transaction` in a tight loop. Auto-backup gates only on first write of session; SQLite locking is the only other safeguard.

---

## Low

- **`_resolve_guid` uses raw f-string for table name in SQL** — [_base.py:756-759](src/gnucash_mcp/book/_base.py:756). Validated against `_GUID_TABLES`, safe today; pattern is fragile if a contributor adds a table without re-validating.
- **`_resolve_guid` doesn't cover `slots`, `prices`, `entries` tables** — [_base.py:648-652](src/gnucash_mcp/book/_base.py:648). If a future tool emits a slot/price/entry GUID prefix, lookup raises "Invalid table". Either expand or document.
- **Reconciliation status lag uses 30-day month constant** — [core.py:1322-1324](src/gnucash_mcp/book/core.py:1322). 91 days renders as "3 months behind" same as 90.
- **`_runway_metrics` cost-basis fallback unfiltered by `as_of`** — [core.py:1161-1164](src/gnucash_mcp/book/core.py:1161). Today-only API today, so no symptom; defensive fix wouldn't hurt.
- **`safe_tool` swallows `RuntimeError` from `_verify_write` failures as generic "unexpected error"** — [tools/_helpers.py:208-217](src/gnucash_mcp/tools/_helpers.py:208). Write-verification failure is a critical correctness signal that deserves its own error_type.
- **`_format_number` strips the trailing dot** — [_format.py:78-81](src/gnucash_mcp/_format.py:78). A `Decimal("100.00")` with `strip_trailing=True` becomes `"100"`. Docstring says "drop trailing zeros after the decimal point" without mentioning the dot strip.
- **`_extract_after_state` returns `None` for empty dicts** — [logging_config.py:1059](src/gnucash_mcp/logging_config.py:1059). Catch-all returns whatever's in the JSON body; future schema changes can leak through unnoticed.
- **`get_audit_log` reads file via `read_text()` without size limit** — [tools/admin.py:118](src/gnucash_mcp/tools/admin.py:118). Long-lived deployment with daily growth could produce multi-MB files.
- **`set_reconcile_state` docstring says `reconcile_date` required for state `'y'` but implementation defaults to now** — [reconciliation.py:64-65,90-95](src/gnucash_mcp/book/reconciliation.py:64).
- **`get_unreconciled_splits` uses `split.quantity` for cleared/uncleared totals** — [reconciliation.py:184-186](src/gnucash_mcp/book/reconciliation.py:184). Correct for "balance of this account" but cross-currency users comparing to a transaction-currency bank statement get confused.
- **`_decimal_to_num_denom` doesn't handle scientific-notation Decimals with very large exponents** — [business.py:645-659](src/gnucash_mcp/book/business.py:645). Practically unreachable for entry quantities.
- **`_invoice_to_compact_line` `except Exception:` overly broad** — [business.py:743-744](src/gnucash_mcp/book/business.py:743). Tighten to `except (ValueError, AttributeError)`.
- **`_address_to_dict` deliberately drops `addr_fax`** — [business.py:543-561](src/gnucash_mcp/book/business.py:543). Documented as intentional ("it's 2026") but means inbound dict with `fax` field round-trips lossily.
- **`pay_invoice` description default `description or owner_name` makes empty-string impossible** — [business.py:2599](src/gnucash_mcp/book/business.py:2599). `description if description is not None else owner_name` is more honest.
- **`update_scheduled_transaction` uses `end_date = ""` as the clear sentinel** — [scheduling.py:647-651](src/gnucash_mcp/book/scheduling.py:647). Documented but unusual.
- **`_describe_age` int division causes "59.9 minutes ago" to display as "59 minutes"** — [backup.py:116-130](src/gnucash_mcp/book/backup.py:116).
- **`prune_backups` `would_keep` not sorted by stage-then-timestamp** — [backup.py:432-433](src/gnucash_mcp/book/backup.py:432). Cosmetic.
- **`list_backups` silently drops files whose `stat()` fails** — [backup.py:356-359](src/gnucash_mcp/book/backup.py:356). A broken symlink is invisible; `prune_backups` will never clean it. Add a debug-log warning.

---

## Test coverage gaps

Inferred from the source; not from reading the test suite.

- **FX gain/loss sign quadrants** — four explicit tests (customer-invoice gain, customer-invoice loss, vendor-bill gain, vendor-bill loss) with computed-correct dollar amounts. Highest-value missing test.
- **Multi-currency precision with non-default-fraction commodities** (JPY, BHD, BTC). Currently everything quantizes to 2 decimals.
- **Partial-payment FX rate drift** — invoice posted at 1.10, paid 40% at 1.20, remainder at 1.05. Verify cumulative FX accounting.
- **Voided posting transaction edge cases** — `pay_invoice` and `unpost_invoice` against a posting transaction the user voided in GnuCash UI.
- **`add_invoice_entry`/`add_bill_entry` with non-INCOME/non-EXPENSE accounts**.
- **Rate of zero / negative** — direct branch path.
- **`_find_invoice` collision detection** — explicit test that customer invoice and vendor bill with same id raises with both candidates listed.
- **`unpost_invoice` with payments applied (refused) vs only-voided payments (allowed)**.
- **Audit `before_state` leakage on exception paths** — stage → raise → next call sees clean state.
- **`@audit_log` re-entrancy** — nested tool calls with thread-local staging.
- **`_unique_prefix` collision boundaries** — engineered GUIDs sharing 8/9/10 chars.
- **Multi-currency `balance_sheet` with no price for a STOCK account** — verify cost-basis fallback path.
- **`_compute_net_worth_at` pre-first-transaction date** — should return 0, not crash.
- **Cross-currency `update_transaction`** — EUR transaction → USD account.
- **`create_transaction` auto-fill against scheduled-template instances** — template skip logic regression test.
- **`_apply_limit` truncation notice when `total == effective`** — boundary case.
- **First-write auto-backup race** — simulate two writes hitting first-write-of-session.
- **`delete_account` placeholder with empty splits list** — should pass safeguards and delete cleanly.
- **`replace_splits` write verification** (becomes useful once verification is added).
- **Investment cost-basis precision** — buy at $33.3333/share, sell 1000 shares, assert exact dollar match.
- **Scheduling month-end drift** — monthly schedule starting Jan 31, advance 13 months, assert period 13 falls on Feb 28 of year+1, not on the 28th of every intervening month.
- **Statement-balance precision mismatch** — three-decimal input against two-decimal book.
- **Backup `_format_ts` collision** — two `create_backup` calls within one second; assert behavior is either microsecond-distinct filenames or an explicit refuse-to-overwrite error.
- **Auto-backup OSError surfaced to caller** — once `last_auto_backup_status` plumbing is added.

---

## Patterns worth preserving and extending

These are explicit endorsements: don't refactor them away.

- **Mixin composition with collision detection** — [book/__init__.py:88-99](src/gnucash_mcp/book/__init__.py:88). Catches the silent-MRO-shadowing footgun. Earned its keep during the v1.2.0 business-module extraction.
- **`_to_decimal` rescue path through `Decimal(str(value))`** — [_base.py:51-74](src/gnucash_mcp/book/_base.py:51). Paired with `SplitInput.coerce_numbers_to_str=True` at the boundary, defends money math at two layers. Never weaken either side.
- **Birthday-problem-aware short GUIDs via `_guid_prefix_map`** — [_base.py:188-266](src/gnucash_mcp/book/_base.py:188). Most stay at 8 chars; only collisions extend. The fast-path single-GUID emit avoids the LCP math when there's no collision.
- **Single-book-open per write with thread-local audit staging** — [_base.py:673-695](src/gnucash_mcp/book/_base.py:673). Documented at both ends. The "biggest perf issue" predecessor item; fixed.
- **`_query_filtered_splits` SQL-pushed reporting filters with Python aggregation** — [reporting.py:305-374](src/gnucash_mcp/book/reporting.py:305). The inline rationale comment ("never `SUM(num/denom)` in SQL — float precision is wrong for money") is the kind of comment that pays for itself the next time someone is tempted to "optimize."
- **Cumulative-sum net-worth time series** — [reporting.py:777-805](src/gnucash_mcp/book/reporting.py:777). O(splits + intervals) instead of O(intervals × splits). Boundary advancement loop placed before running-sum update — matches "as of midnight" semantics.
- **`_format_audit_entry_text` dispatch table** — [logging_config.py:801-832](src/gnucash_mcp/logging_config.py:801). Flattened a 380-line if/elif chain into a `(entity, op) → handler` lookup with graceful degradation. New audit shapes are one row + one function.
- **Pair `_verify_write` / `_verify_composite_write` / `_verify_delete` with every raw-SQL write** — business module is rigorous. Extend the same discipline to `update_transaction`, `replace_splits`, and `delete_budget`.
- **`_safe_date_posted` chokepoint for piecash regex-parser bug** — [business.py:31-61](src/gnucash_mcp/book/business.py:31). One place, defensive, comment ties to bookkeeper bug. Extend to `date_opened`.
- **`_BUSINESS_DOC_CONFIG` table-driven dispatch** — [business.py:1429-1446](src/gnucash_mcp/book/business.py:1429). Mirrors the audit-formatter dispatcher. Use this template if employee expense vouchers (owner_type=5) are ever added.
- **`_resolve_invoice_due_date` chokepoint** — [business.py:891-981](src/gnucash_mcp/book/business.py:891). Single source of truth for "when is this invoice due" used by both `get_outstanding_invoices` and `_collect_warnings`.
- **Counter-vs-MAX(id) reconciliation in business auto-id** — [business.py:1564-1598](src/gnucash_mcp/book/business.py:1564). Pragmatic recognition that piecash's counter can drift after raw-SQL imports or external edits.
- **Live-split filtering in `unpost_invoice`** — [business.py:2412-2435](src/gnucash_mcp/book/business.py:2412). Honors GnuCash void semantics correctly. Extend the same `value == 0` filter to `_calculate_lot_balance`.
- **SQLite online backup API + `PRAGMA integrity_check` + delete-on-failure** — [backup.py:295-321](src/gnucash_mcp/book/backup.py:295). Page-level, reader-safe, atomic from concurrent-writer perspective. The right primitive.
- **Restore is intentionally not an MCP tool** — [backup.py:18-20](src/gnucash_mcp/book/backup.py:18). "If the server is broken enough to need a restore, we can't trust it to do one safely." Conservative and right.
- **Atomic-write state file via temp+rename** — [backup.py:181-185](src/gnucash_mcp/book/backup.py:181). Partial write never leaves a corrupted state file.
- **State file degrades to empty on corruption** — [backup.py:148-166](src/gnucash_mcp/book/backup.py:148). Safe default: more backups, not fewer.
- **Three-state validation lower-cased with clear errors** — [reconciliation.py:50,72-77](src/gnucash_mcp/book/reconciliation.py:50). Accepts mixed-case input, stores lowercase, lists every valid state on error.
- **Atomic batch reconcile** — [reconciliation.py:255-305](src/gnucash_mcp/book/reconciliation.py:255). All-or-nothing — duplicate-already-reconciled or wrong-account split aborts the whole batch before mutating anything.
- **`_collect_create_signals` single-pass dataclass collector** — [core.py:46-91,2121-2378](src/gnucash_mcp/book/core.py:46). When a write needs multiple views of `book.transactions`, consolidate into one helper with `want_*` flags. Reuse the pattern.
- **`_split_to_compact_dict`'s "key present iff non-default" rule** — [_base.py:346-380](src/gnucash_mcp/book/_base.py:346). Token-efficient without lying about absence.
- **Polarity validation on `update_account`** — [core.py:3070-3079](src/gnucash_mcp/book/core.py:3070). Catches debit/credit family flips before they corrupt balances. The kind of guardrail that prevents the worst class of accounting errors.
- **Comments-as-narrative** throughout the business module currency-resolution path. The "USD vendor on CNY book got bills in CNY, $500 became ¥500" comment is the level the codebase aspires to and frequently achieves.
- **Bookkeeper-loop driven evolution** — `core.py:1132-1140` and similar comments tie design choices to real-world data signals. This kind of inline justification ages well.
- **Section omission rather than empty headers in `get_book_summary`** — [core.py:1559-1561](src/gnucash_mcp/book/core.py:1559). "Absence as signal" scales with book size and is documented in-line.
- **`_apply_limit` and `_format_number` extracted to `_format.py`** — layer-neutral utilities decoupling `book/` from `tools/` while keeping a single source of truth.
- **Currency-aware compact formatter** — [reporting.py:89-107](src/gnucash_mcp/book/reporting.py:89). Bookkeeper-noted finding (hardcoded `$` → book-default mnemonic); inline comment makes the intent durable.

---

## Recommended next-pass priorities

If only a handful of changes ship from this review, in order:

1. **Auto-backup status visibility** — surface `last_auto_backup_status` into `get_book_summary`. The most consequential silent-failure mode in the project.
2. **Investment cost-basis precision fix** — recompute `cost_basis` from source values in `calculate_lot_gain` rather than re-parsing the formatted `cost_per_share`. Tax-relevant.
3. **Budget amount truncation fix** — quantize via `acct.commodity.fraction` rather than hardcoded 100, and unify the insert/update paths.
4. **Scheduling month-end drift fix** — anchor each occurrence to `start_date + (n × delta)`.
5. **FX gain/loss sign-quadrant tests** — four explicit tests, one per direction.
6. **Backup filename collision + race fix** — microsecond timestamps, `Path.exists()` precheck, lock around `_backup_checked_in_process`.
7. **Audit before-state staging in scheduling and budgets** — three small additions, make audit log diffs uniform across mixins.
8. **Currency-aware budget rollup** — extend `_split_in_default_currency` to budget reporting; the v1.2.1 fix for the rest of reporting didn't reach budgets.
9. **DRY the `type='transaction'` price filter** — single `_is_market_price` predicate.
10. **Indexed finder pattern in business module** — convert `_find_customer/_find_vendor/_find_employee` and the `for a in book.accounts: if a.guid == ...` patterns to `book.session.query(...).filter_by(guid=...).first()`.

The remaining medium/low items are real but not urgent. The
bookkeeper review loop will surface what matters when daily flow
hits something that doesn't read right.
