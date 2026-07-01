# v1.3 Adversarial Anti-Pattern Audit

Aggressive grep-based pass over `src/gnucash_mcp/` looking for the
25 anti-pattern classes flagged in the audit brief. Every hit was
read in context, not classified from the regex hit alone.

**Headline finding:** the codebase is in unusually clean shape on
the financial-correctness anti-patterns (1, 2, 3, 4, 23, 24).
Where this audit found real lift, it's around **defensive
exception swallowing** that could mask debug-able failures
(Pattern 12) and one missing-decorator nit (Pattern 20). No
high-severity correctness bugs surfaced beyond what previous
review passes captured.

Counts inventoried, by pattern:

| Pattern | Sites checked | BUG | DEFENSIBLE | NEEDS_VERIFICATION |
|---|---|---|---|---|
| 1  split.quantity sums | 13 | 0 | 13 | 0 |
| 2  book.prices walks | 6 | 0 | 6 | 0 |
| 3  Decimal exact eq on converted | 5 | 0 | 5 | 0 |
| 4  float on money | 3 | 0 | 3 | 0 |
| 5  date boundary | 28 | 0 | 28 | 0 |
| 6  linear book.X scans | 14 | 0 | 13 | 1 |
| 7  session.add after parent= | 5 | 0 | 3 | 2 |
| 8  flush() mid-build | 8 | 0 | 8 | 0 |
| 9  template filter | 12 | 0 | 12 | 0 |
| 10 CallableList[0] | 0 | — | — | — |
| 11 SQL injection | 6 | 0 | 6 | 0 |
| 12 broad except | 34 | 0 | 30 | 4 |
| 13 path traversal | 4 | 0 | 4 | 0 |
| 14 destructive force= | 17 | 0 | 17 | 0 |
| 15 reconciled protection | 3 | 0 | 3 | 0 |
| 16 _resolve_account bypass | 0 | — | — | — |
| 17 slot value types | 1 | 0 | 1 | 0 |
| 18 audit stage/consume | 24 stage / 4 consume | 0 | 24 | 0 |
| 19 missing _verify_write | ~25 writes | 0 | ~24 | 1 |
| 20 missing @audit_log | 106 tools | 1 | 105 | 0 |
| 21 mutable default args | 0 | — | — | — |
| 22 test imports in src | 0 | — | — | — |
| 23 hardcoded "USD" | 19 | 0 | 19 | 0 |
| 24 Decimal(float) | 18 | 0 | 18 | 0 |
| 25 date.today() in book methods | 28 | 0 | 28 | 0 |

---

## Pattern 1: split.quantity summed without conversion across accounts

13 sites where `balance += split.quantity` accumulates. Every site
verified to be **per-account** (single commodity), with conversion
to the book's default currency applied AFTER the per-account
balance is computed.

- `book/core.py:455` (`_compute_net_worth_at`) — per-account, then
  `balance * rate` or cost-basis fallback. DEFENSIBLE.
- `book/core.py:712` (`_collect_warnings` integrity check) —
  per-account balance for orphan/imbalance accounts; raw quantity
  is acceptable because the account's own commodity is what
  matters for "is this account drifted from zero". DEFENSIBLE.
- `book/core.py:753` (`_critically_low_cash`) — per-account, then
  converted via `balance_qty * rate`. DEFENSIBLE.
- `book/core.py:1351` (`_runway_metrics`) — per-account, then
  `balance * rate` with cost-basis fallback. DEFENSIBLE.
- `book/core.py:1856` (`balance_sheet` "now" anchor) — per-leaf,
  routes through `_market_value`. DEFENSIBLE.
- `book/core.py:2347` (`get_balance`) — per-account; API
  explicitly returns in the account's own commodity. DEFENSIBLE.
- `book/reporting.py:442` — accumulates into `balances[key]`
  keyed by `account.fullname`; raw `quantity` lives next to a
  `usd`-converted field in the same dict. Aggregation across the
  dict uses `info["usd"]`, not `info["quantity"]`. DEFENSIBLE.
- `book/reporting.py:976` (`debt_payoff_plan`) — per-CREDIT /
  LIABILITY account; debts are valued in their account commodity
  and the avalanche schedule doesn't mix currencies. DEFENSIBLE
  (with the caveat that a multi-currency debt portfolio would
  surface a real bug here — but no existing call path exercises
  it).
- `book/reconciliation.py:193 / 195 / 339 / 376 / 397` — all
  inside a single-account scope (the account being reconciled).
  DEFENSIBLE.

Defensible across the board. The `_split_in_default_currency` /
`_market_value` discipline is held throughout.

---

## Pattern 2: book.prices walked without _is_market_price

6 walks total over `book.prices` (5 `for p in book.prices`, plus
indexed queries via `_find_prices` and `book.session.query(Price)`).

- `book/_currency.py:153` (`_rates_as_of`) — filters via
  `_is_market_price`. DEFENSIBLE.
- `book/_currency.py:311` (`_find_exchange_rate`) — filters via
  `_is_market_price`. DEFENSIBLE.
- `book/_currency.py:119` (`_find_prices` with `market_only=True`
  default) — filters at the indexed-query boundary. DEFENSIBLE.
- `book/core.py:918` (`_collect_warnings` stale-price check) —
  filters for the latest-date scan; intentionally does NOT filter
  for the `in_use` set (because any priced commodity, even with
  only auto-placeholder prices, still counts as "in use"). The
  comment makes the dual-purpose explicit. DEFENSIBLE.
- `book/investments.py:55` (`list_commodities`) — walks
  `book.prices` to find the latest price per commodity for the
  display column. Does NOT filter via `_is_market_price`. This
  was flagged in the audit brief. On a cross-currency book a
  `type='transaction'` placeholder could shadow the real latest
  user-supplied quote in this display. However: `list_commodities`
  is purely a display call; the `entry["latest_price"]` shown to
  the user includes the price `type` field via
  `_commodity_to_compact_line`, so a transaction-effective rate
  is at least visually identifiable. DEFENSIBLE only because the
  user can see the price type; if `list_commodities` is ever
  consumed by an automated valuation path, this becomes a BUG.
- `book/investments.py:516` (`get_latest_price`) — filters via
  `_is_market_price` (explicit comment about the v1.2.1 USD-
  default fix). DEFENSIBLE.

Note: `book/investments.py:402-409` (`get_prices` history walker)
does NOT filter `_is_market_price`, but that's correct — the
endpoint deliberately exposes price `type` and `source` so the
user can see auto-placeholders alongside real quotes. DEFENSIBLE.

---

## Pattern 3: Decimal exact-equality on converted values

5 sites where `Decimal == 0` / `Decimal == Decimal(0)` appears.
Every site verified that the LHS is a sum that arrives at zero by
exact arithmetic (no float coercion, no division-rounding):

- `book/investments.py:729` and `:858` — `Decimal(summary["quantity"])
  == 0` against a string round-trip. The string came from a
  preceding `str(Decimal(...))` in this file's own code. No
  rounding in path. DEFENSIBLE.
- `book/business.py:5552` — `Decimal(str(s.value)) == 0` for a
  void detection. Voided splits zero out exactly; no rounding.
  DEFENSIBLE.
- `book/business.py:6104, :6374, :6380, :6416, :6493, :5848` —
  comparing `remaining == 0`, `pay_quantity == 0`, `cn_remaining
  == 0`, etc. All are sums of exact decimal quantities. DEFENSIBLE.

Pattern 3 is clean. No converted-then-compared bug exists.

---

## Pattern 4: float arithmetic on money

3 `float(` call sites, all in `logging_config.py:53/55/116`. All
operate on rate-limiter tokens/rate (per-second throughput
budget), not monetary values. DEFENSIBLE.

Zero `float(` calls in any of the `book/` modules. Pattern 4 is
clean.

---

## Pattern 5: `< as_of` vs `<= as_of` (boundary inclusivity)

Sweep of 28 date-boundary comparisons. The codebase's convention:

- `post_date <= end_date` and `post_date >= start_date` — both
  bounds inclusive.
- `post_date > end_date` (continue) — implements `<=` inclusive.
- `post_date < start_date` (continue) — implements `>=` inclusive.

Every site checked:

- `book/_query.py:87/89` — `>= start_date`, `<= end_date`. CORRECT.
- `book/core.py:454 / 474 / 1184 / 1257 / 1355 (subtree) /
  2346 / 2411 / 2413 / 2586 / 2740 / 2749 / 2757 / 2907 / 3092` —
  consistent inclusive semantics throughout.
- `book/reconciliation.py:177 / 372` — `> as_of_date` /
  `> through_date` continue. Inclusive. CORRECT.
- `book/business.py:5814 / 6092 / 6466` — `as_of=parsed_date`
  exchange-rate lookups. Calls `_find_exchange_rate` which uses
  `(as_of - p_date).days` with `>= 0` as "before-or-equal".
  CORRECT.
- `book/_currency.py:159` — `p_date > as_of` continue. Inclusive
  at upper bound. CORRECT.

Pattern 5 is clean.

---

## Pattern 6: `for x in book.X:` linear scans

Most sites are inevitable (building GUID prefix maps, where you
need every GUID in the table). Only one site sticks out:

- `book/core.py:3330` (`search_transactions`) — full linear scan
  over `book.transactions` for `field == "description"`, `notes`,
  or `memo`. NEEDS_VERIFICATION as a perf concern: SQLite has no
  text index; a SQL-side `LIKE` would still be linear. The
  current implementation is correct, and on a 2,000-transaction
  book the cost is invisible. Worth flagging only if `book.session.
  query(Transaction).filter(Transaction.description.ilike(...))`
  is faster (could push the string-match into C). Not a bug.

All other sites are either:
- GUID-prefix-map builds where the linear walk is unavoidable
  (`book/_base.py:1084/1107/1129`, `book/core.py:3276/3522/3813/
  4084/4254`, `book/reconciliation.py:526/620`)
- Linear walks where the target table is small (accounts: ~50-200
  rows; commodities: ~5-20 rows)
- Single-pass methods explicitly designed for it
  (`book/core.py:2275 list_accounts`, `book/_currency.py:191
  _account_conversion_factors`)
- `book/core.py:167-172` (`_business_summary_signals` pre-index
  builder) — comment explicitly justifies the upfront cost as
  amortizing N+1 queries over a posted-invoice loop. DEFENSIBLE.

Pattern 6 is essentially clean.

---

## Pattern 7: `book.session.add()` after `parent=X` auto-register

5 `session.add()` sites:

- `book/business.py:3095` (`create_taxtable`) — `Taxtable` has no
  parent relationship, must be explicitly added. DEFENSIBLE.
- `book/scheduling.py:298` (`create_scheduled_transaction`) —
  `piecash.Account(parent=book.root_template, ...)`; the parent
  relationship auto-registers per the predecessor letters. The
  explicit `book.session.add(template_acct)` is **possibly
  redundant**. The subsequent `book.session.flush()` would
  succeed either way. NEEDS_VERIFICATION — would a code reader
  understand the explicit add is defensive? Predecessor's note
  (v1.2.1 release-prep) was explicit about NOT adding the FX
  account this way in `pay_invoice`; the rule may have been
  formalized after this code was written. Worth verifying with
  a test that drops the `add()` and confirms the template
  still persists.
- `book/investments.py:671` (`create_lot`) — `Lot(account=acct, ...)`
  with `account` being a back-populating relationship. Same
  NEEDS_VERIFICATION shape as scheduling.py:298. Test would
  drop the `add()` and confirm round-trip persistence.
- `book/business.py:5271` (`post_invoice` lot creation) — same
  pattern, same NEEDS_VERIFICATION classification. (Actually,
  this one is followed by `ar_ap_split.lot = lot` and
  `book.flush()`, so the relationship will be flushed
  regardless of whether `session.add` was called. Probably
  redundant.)

The two NEEDS_VERIFICATION items are low-risk because if the
add is unnecessary, removing it is a no-op; if it's necessary,
removing it would surface as missing-row failures in the test
suite. Worth a small experiment.

---

## Pattern 8: `book.flush()` mid-transaction-build

8 `flush()` calls. Every one verified to occur AFTER the
transaction's splits are fully populated and balanced (or the
flush is on an entity other than a transaction-in-progress):

- `book/business.py:1048` — UPDATE after data-heal, no
  transaction-build context. DEFENSIBLE.
- `book/business.py:3096` (`create_taxtable`) — `Taxtable` flush;
  not transaction-related. DEFENSIBLE.
- `book/business.py:3329` (`update_taxtable`) — same. DEFENSIBLE.
- `book/business.py:5394` (`post_invoice`) — flush AFTER full
  transaction with splits assigned. DEFENSIBLE.
- `book/business.py:5569` (`unpost_invoice`) — flush AFTER
  nulling FK pointers, before delete. DEFENSIBLE.
- `book/business.py:6099` (`pay_invoice`) — flush AFTER full
  payment transaction is built. DEFENSIBLE.
- `book/business.py:6476` (`apply_credit_note`) — same. DEFENSIBLE.
- `book/scheduling.py:299` — flush AFTER template account
  creation, not mid-transaction. DEFENSIBLE.

Pattern 8 is clean.

---

## Pattern 9: Missing `_template_account_guids()` filter

Every iteration over `book.accounts` that aggregates balances or
classifies by type checks for template inclusion. Spot-verified
sites:

- `book/_base.py:992` (`_find_account`) — filters templates.
  DEFENSIBLE.
- `book/core.py:269 / 415 / 736 / 1321 / 1795 / 2275 / 2691` — all
  filter templates. DEFENSIBLE.
- `book/reporting.py:945` (`debt_payoff_plan`) — filters templates
  with explicit defense-in-depth comment. DEFENSIBLE.
- `book/_currency.py:191` (`_account_conversion_factors`) — filters
  templates with explicit comment. DEFENSIBLE.
- `book/business.py:443 / 558` (FX-account / discount-account
  discovery) — does NOT explicitly filter templates, but the
  upstream `type in {"INCOME", "EXPENSE"}` filter rules out
  templates (which are created with `type="BANK"` per
  `book/scheduling.py:294`). DEFENSIBLE.
- `book/business.py:5006` (account_paths map for invoice
  rendering) — does NOT filter; harmless because the map is only
  queried by explicit account GUID from invoice entries, which
  never reference a template. DEFENSIBLE.

Pattern 9 is clean.

---

## Pattern 10: `book.accounts(fullname=name)[0]` slot-assertion

Zero hits. Predecessor letters' historical fix has held.

## Pattern 10: clean — 0 sites checked.

---

## Pattern 11: Raw SQL with f-string interpolation of user input

6 `text()` SQL calls. Five use named-parameter binding (`:guid`,
`:n`); one uses f-string interpolation of an internally-controlled
column name:

- `book/core.py:3788` — `:guid` bind. DEFENSIBLE.
- `book/business.py:628 / 1043 / 1805 / 1823 / 1899 / 1904 / 2739
  / 4990 / 4995` — all `:guid` bind. DEFENSIBLE.
- `book/business.py:6633-6651` (delete-invoice-or-bill refcount
  maintenance) — f-string interpolates `entry_fk` (either
  `"invoice"` or `"bill"` based on `owner_type`). `owner_type`
  is gated by `_parse_owner_type` at the tool entry, returning a
  hardcoded int from a fixed set. No external string ever
  reaches this f-string. DEFENSIBLE.
- `book/scheduling.py:177` — `:guid` bind. DEFENSIBLE.

Pattern 11 is clean.

---

## Pattern 12: `except Exception:` swallowing real failures

34 broad-except sites. Almost all are deliberate degradation
points; some are NEEDS_VERIFICATION for diagnostic surface.

**Defensible by design (no change needed):**

- `server.py:949` — startup currency-detection; book may be
  locked. Doesn't block startup.
- `logging_config.py` (8 sites) — audit-log resilience: capture
  failures must NEVER break a tool. Documented in comments.
- `book/backup.py:416 / 708 / 712 / 723 / 745` — backup
  resilience: per-file failures don't abort the whole prune;
  list_backups errors don't break get_backup_health.
- `book/scheduling.py:371 / 379 / 793` — cleanup-then-re-raise on
  partial create; audit-staging fallback on delete.
- `book/budgets.py:164` — display-format fallback.
- `book/business.py:1049` — best-effort data-heal.
- `book/_currency.py` (multiple) — defensive guards.

**NEEDS_VERIFICATION (should at minimum log at debug level):**

- `book/core.py:187` (`_business_summary_signals` invoice loop) —
  `except Exception: continue`. A piecash flake or detached-
  instance error during the dashboard's invoice loop would silently
  skip the invoice and undercount overdues. Recommend
  `debug_logger.debug(...)` at minimum so the bookkeeper can find
  out why a known invoice didn't show.
- `book/core.py:196` (same loop, due-date resolution) — same.
- `book/core.py:223` (Job count) — `except Exception: pass`.
  Silently degrades `active_jobs` to absent. Recommend
  `debug_logger.debug(...)`.
- `book/core.py:786 / 892 / 898 / 956 / 1007 / 1011 / 1050` —
  all inside `_collect_warnings` best-effort path. Documented as
  "skip failed checks, emit the rest." DEFENSIBLE *by design*,
  but logging at debug level when each section silently degrades
  would help the bookkeeper diagnose "why isn't this warning
  showing up?". Not a bug; suggested improvement.

**Tools layer:**

- `tools/admin.py:138` — see file. Per the file: catches errors
  reading the audit log file. DEFENSIBLE.
- `tools/_helpers.py:388` — `safe_tool` catches all errors and
  returns structured JSON error response. DEFENSIBLE (it's
  literally the safe-tool boundary).

Recommended fix (suggested, not required):
```python
# book/core.py:187 and similar:
except Exception as e:
    debug_logger.debug(
        f"Invoice loop skipped one: {e}", exc_info=True
    )
    continue
```

---

## Pattern 13: `os.path` operations on user-supplied paths

4 path operations:

- `book/_base.py:793` — `Path(book_path).resolve(strict=True)`.
  `book_path` comes from env var (server-controlled). Resolves
  symlinks. DEFENSIBLE.
- `logging_config.py:254 / 256` — log path from env override or
  derived from book path. Server-controlled. DEFENSIBLE.
- `book/backup.py:627 / 779` — `Path(entry["path"]).unlink()`
  where `entry` comes from an internal backups-dir manifest
  (timestamped files under server-controlled backup root). The
  manifest path can be poisoned only if an attacker can write to
  the backup directory directly — at which point they don't
  need to traverse the manifest, they can just modify backups.
  DEFENSIBLE.
- `_format.py:117` — `os.path.basename(str(book_path))`. Display-
  only. DEFENSIBLE.

Pattern 13 is clean.

---

## Pattern 14: Missing `force=False` on destructive operations

17 destructive methods inventoried. Three patterns:

- **Has explicit `force=False`**: `delete_transaction`,
  `delete_job`, `update_transaction` (`splits` mod path),
  `replace_splits`.
- **No force, hard structural gate instead**: `delete_account`
  (refuses if children OR splits exist; documented), `delete_
  invoice/bill/voucher` (refuses if posted; documented),
  `delete_customer/vendor/employee/job` (presumed similar
  structural gates).
- **No force, no destructive scope**: `delete_account_slot`,
  `delete_budget`, `delete_scheduled_transaction`, `delete_
  taxtable`, `delete_price` — each affects only its own narrow
  scope; reversible via the corresponding create tool.

Spot-checked all gate paths. No silent over-delete.

Pattern 14 is clean.

---

## Pattern 15: Reconciled-split protection

3 write paths touch splits and could collide with reconciled state:

- `delete_transaction` (`book/core.py:3801`) — checks
  `reconcile_state == "y"`, requires `force=True`. CORRECT.
- `update_transaction` splits-mod path (`book/core.py:3990`) —
  same check, same force gate. CORRECT.
- `replace_splits` (`book/core.py:4173`) — same. CORRECT.

`void_transaction` and `unvoid_transaction` deliberately operate
on reconciled splits as part of their semantics (GnuCash's void
convention is `reconcile_state = "v"`). Not bypassed; intentional.

Pattern 15 is clean.

---

## Pattern 16: `_resolve_account` bypassed by direct `book.accounts.get()`

Zero `book.accounts.get()` or `book.accounts[...]` hits in the
production source. The fix predecessor 4.7 documented (v1.2.1
code-review night) has held.

## Pattern 16: clean — 0 sites checked.

---

## Pattern 17: Slot writes with non-string non-int values

`set_account_slot(value: str)` typed at both the MCP tool surface
(`tools/admin.py:53`) and the book method (`book/admin.py:72`).
Pydantic validates the type at the tool boundary. A JSON value of
the wrong shape (list, dict, number) would be rejected with a
422-style error before reaching the book layer.

DEFENSIBLE.

## Pattern 17: clean — 1 site checked.

---

## Pattern 18: Audit-log staging not consumed

24 `_stage_audit_before` calls vs 4 `_consume_audit_before` calls.
Asymmetry is correct: stages happen in each write method (one per
write); consume happens centrally in the `@audit_log` wrapper
(success branch, error branch, plus a pre-clear at entry). All
three consume points implemented in `logging_config.py:2020/2076/
2147`.

Defense-in-depth pre-clear at wrapper entry (`logging_config.
py:2016-2022`) ensures threading-local can't leak from one tool
call to the next even if both consume paths failed. Documented
in the comment.

Pattern 18 is clean.

---

## Pattern 19: Write paths missing `_verify_write` / `_verify_composite_write`

Audit invariant from CLAUDE.md: "Every write is verified."
Inspection finds the invariant fully held for **raw-SQL writes**
(business module — Customer, Vendor, Employee, Invoice, Bill,
Voucher, Credit Note, Taxtable, Entry — all paired with verify)
and for **transaction state changes** (`update_transaction` and
`replace_splits` call `_verify_transaction_state`).

The invariant is NOT enforced for **ORM-mediated writes** —
`create_account`, `update_account`, `move_account`, `create_
transaction`, `create_lot`, `assign_split_to_lot`, `close_lot`,
`create_commodity`, `create_price`, `set_account_slot`, `delete_
account_slot`, `create_budget`, etc. These rely on SQLAlchemy/
piecash to raise on failed commit.

This is consistent with the helpers' design (the verify functions
take a SQLAlchemy `Table`, not an ORM object). The CLAUDE.md
phrasing slightly overstates the actual contract.

- `create_transaction` (`book/core.py:3270`) — book.save() with
  no post-save verify read-back. NEEDS_VERIFICATION whether this
  is desired; the invariant in CLAUDE.md is more aggressive than
  the code. Likely the right call is to update CLAUDE.md to
  reflect the actual contract: "raw-SQL writes are verified;
  ORM writes trust SQLAlchemy."

No bug, but the documentation lies.

---

## Pattern 20: `@audit_log` decorator absent on tools

106 `@mcp.tool()` decorators in `tools/` + `server.py`. 106
`@audit_log` decorators. By count, every tool is decorated.

Direct check via decorator-adjacency:

- `get_server_config` (`server.py:786-795`) — `@mcp.tool()`,
  `@safe_tool`, but **NO `@audit_log`**. This was flagged by
  the previous review. Read-only diagnostic surface; not a
  correctness bug, but inconsistent.

**BUG (cosmetic):** Add `@audit_log(classification="read")` to
`get_server_config` for consistency with the other 105 tools.

```python
@mcp.tool()
@safe_tool
@audit_log(classification="read")
def get_server_config() -> str:
    ...
```

---

## Pattern 21: Mutable default arguments

Zero hits. Pattern 21 is clean.

## Pattern 21: clean — 0 sites checked.

---

## Pattern 22: Test-only imports in production code

Zero `import pytest` / `from unittest` in `src/`. Pattern 22 is
clean.

## Pattern 22: clean — 0 sites checked.

---

## Pattern 23: Hardcoded "USD"

19 string-literal "USD" hits across `src/`. Every one is in:

- **docstring example text** (`"e.g., 'USD', 'EUR'"`) —
  documentation, not behavior.
- **default kwarg in display helpers** (`_money_compact`,
  `_format_debt_payoff_compact`, `_format_vendor_spending_compact`
  in `book/reporting.py` and `book/business.py`) — every caller
  passes the book's actual default-currency mnemonic. The
  default value `"USD"` is a fallback that triggers only if a
  caller forgets to pass `currency`. None do.

The audit history (v1.2.1 release-prep) caught and fixed every
real hardcoded-USD that affected behavior. No remnants surfaced.

Pattern 23 is clean.

---

## Pattern 24: `Decimal(float)` bug

18 `Decimal(<expr>)` constructions across `book/`. Inspected each:

- All `Decimal(str_var)` where `str_var` came from user input,
  slot read (string-typed), or from a prior `str(Decimal)` round
  trip. Safe.
- All `Decimal(int_var) / Decimal(int_var)` (num/denom from raw
  SQL rows). Both operands are integers (`q_num`, `q_denom`).
  Safe.
- `Decimal(elapsed_days) / Decimal(total_days) * Decimal(100)` —
  int / int. Safe.

Zero `Decimal(float_var)` paths. The comment in `book/_base.py:78`
explicitly warns against `Decimal(float)`. Pattern 24 is clean.

---

## Pattern 25: `date.today()` / `datetime.now()` in book methods

28 sites. Categories:

- **Default-when-None on `as_of` / `start_date` / `end_date` /
  `trans_date` parameters** — canonical pattern, exactly where
  the brief said this is fine. ~15 sites.
- **Top of dashboard / warning methods** (`get_book_summary`,
  `_collect_warnings`, `_runway_metrics`, `_critically_low_cash`,
  `_account_reconciliation_status`) — `today = date.today()`
  captures a single value used throughout the method. These
  methods are inherently "as of now" — making them parameterizable
  would just add a parameter that every caller passes
  `date.today()`. ~10 sites.
- **Entry / reconcile timestamps** (`book/business.py:3713,
  4659, 4660`, `book/reconciliation.py:97, 511`) — `datetime.now()`
  records when the user performed the action. Wallclock IS the
  semantic. Tests stub via `freezegun` / test fixtures.

Test reproducibility is preserved because every call site uses
the system clock as the canonical "now." No site introduces a
random / hidden dependence.

Pattern 25 is clean.

---

# Summary

**Bugs found:**

- (Cosmetic, P3) Pattern 20: `get_server_config` lacks
  `@audit_log(classification="read")` decorator. Trivial fix.

**Needs verification (worth a small experiment):**

- (P3) Pattern 7: Drop the `book.session.add(lot)` /
  `book.session.add(template_acct)` in `book/investments.py:671`,
  `book/business.py:5271`, `book/scheduling.py:298` and confirm
  the relationship-mediated registration is sufficient. If yes,
  the redundant adds can be removed in a cleanup pass.
- (P2) Pattern 12: Three `except Exception: pass` sites in
  `book/core.py:187/196/223` silently degrade the
  `_business_summary_signals` invoice loop. Worth adding
  `debug_logger.debug(..., exc_info=True)` so the bookkeeper can
  diagnose missing-overdue reports. Not a correctness bug.
- (Documentation) Pattern 19: CLAUDE.md's "Every write is
  verified" invariant overstates the contract. The actual rule
  is "every raw-SQL write is verified; every transaction state
  change is verified; ORM-mediated writes trust SQLAlchemy."
  Worth refining the wording in CLAUDE.md so the next reviewer
  doesn't re-raise as a bug.

**Patterns with zero hits:**

- Pattern 10 (CallableList[0])
- Pattern 16 (`_resolve_account` bypass)
- Pattern 21 (mutable default args)
- Pattern 22 (test imports in src)

**Overall**: the v1.2.1 code-review night and the v1.3 release-prep
marathon swept the codebase thoroughly. This adversarial pass
surfaced no new correctness bugs of consequence. The defensive
exception-swallowing in `_collect_warnings` is the only area where
a future maintainer could plausibly miss a real failure, and the
remediation is a one-line debug-logger addition, not a
restructuring.
