# Code Review — v1.3 pre-release (consolidated, adversarial pass)

Two-pass review. First pass was deferential — too willing to trust
code comments, prior-Claude letters, and the CHANGELOG. Maintainer
caught the bias; second pass was adversarial — six skeptics each
prompted to refute, then a separate verifier round to refute the
refutations. Findings below are what *survived* adversarial
verification.

Per-dimension reports:

- [Multicurrency](CODE_REVIEW_v1_3_multicurrency.md)
- [I/O efficiency](CODE_REVIEW_v1_3_io_efficiency.md)
- [Refactoring / DRY](CODE_REVIEW_v1_3_refactoring.md)
- [Report accuracy](CODE_REVIEW_v1_3_report_accuracy.md)
- [Architecture / contracts](CODE_REVIEW_v1_3_architecture.md)

Adversarial reports:

- [Math walk](CODE_REVIEW_v1_3_adversarial_math.md)
- [Anti-pattern hunt](CODE_REVIEW_v1_3_adversarial_antipatterns.md)
- [State / concurrency / integrity](CODE_REVIEW_v1_3_adversarial_state.md)
- [Security / input boundary](CODE_REVIEW_v1_3_adversarial_security.md)
- [Cross-tool agreement](CODE_REVIEW_v1_3_adversarial_agreement.md)
- [Completeness critic](CODE_REVIEW_v1_3_completeness_critic.md)

Refuter verifications:

- [FX rate-as-of](CODE_REVIEW_v1_3_verify_fx.md) — 5/5 confirmed
- [Voided splits](CODE_REVIEW_v1_3_verify_voided.md) — 6/6 confirmed
- [_resolve_account](CODE_REVIEW_v1_3_verify_resolve_account.md) — 3/3 confirmed
- [Placeholder filter](CODE_REVIEW_v1_3_verify_placeholder.md) — 2/3 confirmed; P-3 partially refuted (different real bug surfaced)
- [Security](CODE_REVIEW_v1_3_verify_security.md) — 3/3 confirmed (one reframed)
- [Misc](CODE_REVIEW_v1_3_verify_misc.md) — 6/6 confirmed

---

## Headline

**v1.3 is not ready to ship.** The adversarial pass surfaced ~25
substantive findings beyond the deferential pass. The bookkeeper
loop validates base cases on Alex's book; that's necessary but not
sufficient for math correctness. Six themes account for most of the
correctness damage:

1. **`_rates_as_of(book)` without an `as_of` parameter** in 5+
   sites. Historical reports use today's rates against historical
   quantities. Includes `balance_sheet` (already known B-1) plus
   `net_worth` time-series, `vendor_spending_report`, `debt_payoff_plan`,
   `_account_conversion_factors` (the helper itself).
2. **Voided-split filter inconsistency**. Five iteration sites
   handle the `state='v', value=0` pairing differently — some
   filter on state, some on value, some on neither. `set_reconcile_state`
   can move voided → `y` and break `unvoid_transaction`.
3. **`_resolve_account` template-filter asymmetry**. The path
   branch filters templates; the `%short` and full-GUID branches
   don't. `update_account`, `move_account`, `delete_account` can
   silently mutate template-tree rows when called with a non-path ref.
4. **Placeholder prices in latest-price queries**. `list_commodities`
   and `calculate_lot_gain` walk `book.prices` without
   `_is_market_price`. Auto-rate placeholders can shadow real
   market quotes.
5. **Two-session writes without atomicity**. `create_transaction_from_scheduled`
   commits the schedule advance in session 1, then opens session 2
   for the transaction. Data-loss shape: schedule advances, transaction
   never lands.
6. **"Every write is verified" is false**. `admin.py`,
   `investments.py`, `reconciliation.py` have zero `_verify_*` calls.
   `pay_invoice`, `unpost_invoice`, `apply_credit_note`, `delete_account`
   save without verification. The invariant in CLAUDE.md is overstated.

Plus a real path-traversal security finding (`get_audit_log`
unvalidated `log_date`), six audit-log dispatcher gaps that suppress
write entries entirely (not "empty diff" — full suppression because
the empty string fails the truthy gate), and a runway-over-stated-10×
bug on new books.

---

## Ship blockers — correctness

### SB-1. `net_worth` time-series uses LATEST FX rates for every snapshot
`book/reporting.py:640`. `_account_conversion_factors(book)` is
called once outside the boundary sweep with no `as_of`, so every
historical snapshot uses today's rates. Disagrees with
`_compute_net_worth_at` (`book/core.py:423-426`) which correctly
branches on `as_of >= date.today()`. Trajectory chart shows zero
FX-driven variation on multi-currency books; cross-tool divergence
with `get_book_summary`.

**Fix:** Move `_account_conversion_factors` inside the per-snapshot
loop and pass `as_of` per boundary date. Pre-compute a rates-by-
boundary table from `_rates_as_of(book, as_of, default_currency)`.

### SB-2. `balance_sheet` applies today's prices to historical dates
`book/reporting.py:450`. Already in the deferential pass — splits
filtered by `end_date=as_of_date`, but `_rates_as_of(book)` has no
date filter. Branch on `as_of_date >= date.today()` mirroring
`_compute_net_worth_at`.

### SB-3. `vendor_spending_report` has the same shape as SB-2
`book/business.py:7667`. Historical-period vendor totals apply
today's rates. The v1.2.1 fix to balance_sheet/net_worth/cash_flow
didn't reach this report.

### SB-4. `debt_payoff_plan` sums raw quantities across currencies
`book/reporting.py:974-977`, `1064`, `1071`. Per-debt balances sum
`split.quantity` without `_split_in_default_currency` conversion,
then are aggregated against a `monthly_budget` in default currency.
Avalanche timeline is nonsense on books with mixed-currency debt.

### SB-5. `cash_flow` default mode double-counts internal transfers
`book/reporting.py:768-785`. Filter is `account_types=_CASH_TYPES`
(BANK, CASH). A Checking → Savings transfer hits both inflows
(Savings positive) and outflows (Checking negative). Net is correct;
gross is inflated by every internal sweep. Either filter
intra-CASH-type transfers or document the gross-vs-net convention
loudly in the response.

### SB-6. `get_budget_report` and `_budget_headline` don't FX-convert budget targets
`book/budgets.py:766, 851` and `book/core.py:1162-1164`. Targets
are summed in their stored account commodity; actuals are converted
to default currency. `used_pct` is meaningless on mixed-currency
budgets.

### SB-7. `_daily_expense_burn` always divides by 180 days
`book/core.py:1250-1264`. No book-age clamp. A book 19 days old
with $3,000 spending shows daily_burn = $16.67 instead of $158.
Runway over-stated ~10× — exactly the wrong direction for the
warning. The "rationalized lie" pattern from the predecessor letters.

**Fix:** `days = min(180, (date.today() - first_txn_date).days or 1)`.

### SB-8. `balance_sheet` doesn't skip placeholder accounts
`_compute_net_worth_at` (`book/core.py:443`) explicitly skips
placeholders; `balance_sheet` does not. Cross-tool disagreement on
the same data when a placeholder has direct splits (rare but
legal).

### SB-9. `_budget_headline` doesn't roll up to placeholder parents
`book/core.py:1186-1200` matches splits only on directly-budgeted
account GUIDs. `get_budget_report` (`book/budgets.py:776-789`)
builds a rollup_map so descendants count. PR #46 fixed the report;
the dashboard headline was left behind. Books that budget
placeholder parents (common pattern) see "0% used" on the dashboard
while the report shows the correct percentage.

### SB-10. `create_transaction_from_scheduled` data-loss path
`book/scheduling.py:599-707`. Two sequential sessions: first
commits `last_occur` / `instance_count + 1`, second opens for the
actual `create_transaction`. If session 2 fails — including the
`status="rejected"` duplicate-detector path which returns without
raising — the schedule has advanced but no transaction exists.
Re-running advances again.

**Fix:** Stage the transaction first, then advance the schedule in
the same session, then verify both before save.

### SB-11. `list_commodities` and `calculate_lot_gain` pick placeholder prices
`book/investments.py:53-59` and `:944-956`. Both walk `book.prices`
without `_is_market_price`. Auto-rate placeholders shadow real
market quotes. `calculate_lot_gain` also lacks a currency filter —
the picked price may be in the wrong currency.

### SB-12. `_resolve_account` template-filter asymmetry
`book/_base.py:1134-1179`. The path branch filters via
`_find_account`; the `%short` and full-GUID branches go straight to
SQLAlchemy with no template filter. `update_account`, `move_account`,
`delete_account` can silently mutate template-tree rows.
`get_account("Template Root:Rent SX")` returns None;
`get_account("%abc1234")` for the same logical account returns the
template. Same logical operation, three input shapes, two violate
the documented contract.

**Fix:** Single chokepoint — apply
`if acct and acct.guid in self._template_account_guids(book): return None`
after each branch returns in `_resolve_account`.

### SB-13. `set_reconcile_state` can move voided splits to `'y'`
`book/reconciliation.py:52-101`. Gates input string against
`{n,c,y}` but doesn't check current state. Setting a voided split
to `y` creates a reconciled $0 split that defeats `unvoid_transaction`'s
state='v' signal. Recovery path lost.

**Fix:** Reject if current `reconcile_state == 'v'`.

### SB-14. `_lot_decimals` doesn't filter voided splits → lot stays open
`book/investments.py:566-573`. Voided BUY zeros purchase quantity;
the partial SELL still contributes its negative; `remaining = -30`.
`assign_split_to_lot`'s auto-close check (`quantity == 0`) is
False, lot stays open in a clearly-wrong state.

### SB-15. `get_audit_log(log_date)` path traversal
`tools/admin.py:112-144`. `log_date` is interpolated into a `Path`
with no validation. Verified: `Path('/tmp/audit') / '../../../../etc/passwd.txt'`
resolves to `/private/etc/passwd.txt`. Prompt injection in any
free-text field that gets surfaced into the audit log can exfiltrate
arbitrary `*.txt` files.

**Fix:** Validate `log_date` matches `\d{4}-\d{2}-\d{2}` before
constructing the path.

---

## High-priority

### HP-1. "Every write is verified" — invariant is overstated
55 `book.save()` sites vs 47 `_verify_*` calls. `admin.py`,
`investments.py`, `reconciliation.py` have zero verify calls.
`pay_invoice` (5604), `unpost_invoice` (5450), `apply_credit_note`
(6498), `delete_account` (core.py:3754) all save without verify.

**Action:** Decide whether to (a) bring all write paths up to the
invariant, or (b) relax CLAUDE.md to describe the actual contract
("ORM writes rely on SQLAlchemy commit; raw-SQL writes verify
explicitly"). Option (a) is the safer release-time choice.

### HP-2. Six audit-log dispatcher entries missing — full suppression
`logging_config.py:1643-1708` `_AUDIT_HANDLERS`. Missing:
`commodity:CREATE`, `price:CREATE`, `lot:CREATE`, `lot:UPDATE`,
`scheduled_transaction:CREATE`, `budget:CREATE`. The fallback
returns `""`, and the decorator at line 2129 gates on truthy — so
these six write operations emit NOTHING to the audit log, not
"empty diff". Debug log still captures them; the user-readable
audit trail does not.

### HP-3. Voided-split filter cluster — five additional sites
Beyond SB-13 and SB-14:
- `get_unreconciled_splits` filter is `!= "y"` — admits voided
  (`book/reconciliation.py:181-195`).
- No site asserts `state=='v' ⇒ value==0`. Five sites filter on
  one or the other but not both; partial corruption isn't caught.
- `assign_split_to_lot` (`book/investments.py:828-852`) has no
  state guard — a voided split assigns cleanly, then auto-closes
  the lot DOA.
- `get_book_summary` reconciliation backlog
  (`book/core.py:328-344`) counts voided as unreconciled.

**Fix:** Single `_is_voided(split)` helper in `_base.py`; apply at
all five iteration sites.

### HP-4. `tools/business.py` uses `json.dumps(indent=2)` instead of `_json()` — 7 sites
Lines **59, 122, 186, 345, 409, 970, 1316** — `list_customers`,
`list_vendors`, `list_employees`, `list_invoices`, `list_bills`,
`list_credit_notes`, `get_credit_note`. 40-60% bloat from
indentation + skips `_strip_noise`. Mechanical fix.

### HP-5. `_compute_fx_gain_loss` raises on missing third-currency rate
`book/business.py:856-862`. Third-currency payment with no
`pay → default` rate raises hard. Mirror the `rate_at_post` branch:
return `None` (skip FX booking when data not available).

### HP-6. `_collect_warnings` in_use set polluted by auto-rates
`book/core.py:919`. The `in_use.add()` happens BEFORE the
`_is_market_price` filter, so commodities held only via cross-
currency placeholders get marked "in use" and a different warning
(no-price-on-file for unused commodities) misfires.

### HP-7. `get_budget_report` parent rollup uses string length as depth proxy
`book/budgets.py:786-789`. Replace `len(acct_name)` with
`acct_name.count(':')`.

### HP-8. Reconciliation backlog count divergence
`get_book_summary` counts only splits past `latest_y_date`;
`get_unreconciled_splits` counts ALL non-`y` splits. Both arguably
right for different questions; the units don't match and the LLM
isn't told which is which.

### HP-9. Unbounded slot values — resource exhaustion
`book/admin.py:71-115` (`set_account_slot(value)`) and
`book/reconciliation.py:478` (`void_transaction(reason)`) accept
arbitrary-length values. No upper bound. Verified exhaustion path.

**Fix:** Single `len()` cap line at the top of each method (e.g.,
4 KiB for `reason`, 64 KiB for slot values).

### HP-10. `SplitInput` uses `extra="ignore"` — typos silently corrupt
`tools/_helpers.py:83-86`. Server-global `extra="forbid"` (PR #92)
catches typos; the local override creates a one-off hole.
`quantitiy` instead of `quantity` silently drops, corrupting
multi-currency splits.

### HP-11. `delete_account_slot` skips `_SLOT_KEY_RE` validation
`book/admin.py:117-144`. `set_account_slot` enforces the key
regex; `delete_account_slot` does not. Users can target internal
namespaced slots (`gnc-mcp/applies-to-invoice`) the validator was
meant to protect.

### HP-12. `_query_filtered_splits` doesn't filter template accounts
`book/_query.py:27-96`. Currently dormant because
`Transaction.post_date.isnot(None)` excludes SX templates and no
codepath posts splits to template accounts. Latent: defense-in-
depth `Account.guid.notin_(template_guids)` would close it.

---

## Medium-priority

### MP-1. `get_server_config` lacks `@audit_log`
`server.py:786-788`. Read tools carry `@audit_log(classification="read")`;
this one doesn't. May be deliberate (diagnostic), but the comment
explains a different deliberate choice (registering outside the
module gate). Decide and document.

### MP-2. `_collect_warnings` Imbalance account sums raw quantities
`book/core.py:710-716`. Routes via `_split_in_default_currency`
needed.

### MP-3. `prune_backups(stage=None, keep_last_n=0)` deletes all auto backups
`book/backup.py:541-639`. The catastrophic-footgun guard only
covers `stage="manual"`. The source comment marks the auto-stages
behavior as intentional (auto chains have policy-driven retention).
Should add a symmetric guard for the no-stage case so the LLM-
calling-with-wrong-args path is protected.

### MP-4. Backup tool responses leak absolute paths
`book/backup.py:459-470` (and friends). `restore_hint` emits the
book path twice in shell-command form regardless of
`GNUCASH_REDACT_PATHS`.

### MP-5. No length caps on business-entity free-text
`book/business.py:2078-2092` (create), `:2249-2256` (update).
`notes` and address sub-fields can balloon book file size.

### MP-6. `get_balance` does an extra fetch to echo canonical name
`tools/core.py:89-107`. Trade-off — the echo helps confirm `%short`
resolution. Either drop the second query or keep the design call.

### MP-7. `create_transaction` docstring is token-heavy
`tools/core.py:177-197`. ~450 tokens of duplicate-detection signal
spec on every tool discovery.

### MP-8. `net_worth` time-series boundary uses strict `>` (correct, fragile)
`book/reporting.py:704-716`. Add explicit boundary-inclusion comment.

### MP-9. `debt_payoff_plan` error message misleads
`book/reporting.py:1058-1062`. Distinguish "no such accounts" from
"accounts exist, no APR".

### MP-10. `MODULE_GROUPS` member validation incomplete
`server.py`. Validates group names but not that members exist in
`TOOL_MODULES`.

### MP-11. Three `book.session.add()` calls after `parent=X`
`book/investments.py:671`, `book/business.py:5271`,
`book/scheduling.py:298`. Predecessor flagged the pattern;
redundant or harmless depending on entity. Worth a test.

### MP-12. Three exception swallows without logging
`book/core.py:187/196/223` in `_business_summary_signals` invoice
loop. piecash flakes silently undercount overdue invoices. Add
`debug_logger.debug(..., exc_info=True)`.

### MP-13. `create_commodity` accepts unvalidated namespace/mnemonic/fullname/cusip/fraction
### MP-14. `create_account(name)` accepts `:` (path separator) and control chars

---

## Low-priority polish

- L-1. Inline single-call renderers `_format_monthly_net` (`book/core.py:1527`), `_render_warnings` (`:1577`).
- L-2. Type hint on `_strip_noise` (`tools/_helpers.py:149`).
- L-3. Docstring conventions: `cash_flow`, `spending_by_category`, `balance_sheet` Unrealized line, budget "ytd".
- L-4. Lint rule for `json.dumps` in `tools/*.py` after HP-4.
- L-5. `restore_hint` uses unquoted f-string shell construction (latent shell-injection if path source becomes user-writable).
- L-6. README drift: "What's in v1.2.1" section header (CHANGELOG already has v1.3.0); points to retired `specs/NEXT_STEPS_1_3.md`; test count and tool count claims need refresh.
- L-7. Synthetic-book scripts last touched May 1 — predate Stage 3 features and module restructure. Phase scripts contain zero references to taxtables/credit_notes/vouchers/jobs. Lin Wei sample book has zero Stage 3 exposure.

---

## Refactor backlog (post-release)

- R-1. Extract `get_book_summary` sub-renderers (`book/core.py:1749-2228`).
- R-2. Consolidate cross-currency conversion in invoice post/pay (`book/business.py:5298, 5804`).
- R-3. Move `_normalize_account_refs_for_audit` to `book/_base.py`.
- R-4. Date-parsing helper `_parse_iso_date`.
- R-5. Audit-log coverage test (pair with HP-2).
- R-6. **DO NOT** split `business.py` (7,816 LOC) — invoice lifecycle too interdependent.
- R-7. **DO NOT** merge `_format.py` and `_currency.py` — distinct concerns.

---

## Reframing of the deferential pass's findings

The original "What looks right" section claimed several things that
the adversarial pass disproved:

- ❌ "Multi-currency aggregation in all reports correctly converts
  via `_split_in_default_currency`" — `debt_payoff_plan` doesn't
  (SB-4); `vendor_spending_report` historical periods don't (SB-3).
- ❌ "Every write is verified" — overstated; see HP-1.
- ❌ "Template-account filtering consistent" — `_resolve_account`
  is asymmetric (SB-12); `_query_filtered_splits` is latent
  (HP-12).
- ❌ "Voided splits filtered properly everywhere" — five sites
  inconsistent (HP-3).
- ❌ "Reconciliation backlog accurate" — counts voided as
  unreconciled (HP-3 / SB-15 portion).
- ❌ "Decimal precision end-to-end" — true mostly, but
  `_lot_decimals` doesn't filter voided (SB-14).
- ❌ "Audit-log dispatch covered for every write" — six gaps cause
  full suppression (HP-2).

Still true:

- ✅ MRO collision guard.
- ✅ Contract tests `TestToolFileVsModulesMapping`,
  `TestWriteResponseShape`, `TestShortGuidRoundTripClosure`.
- ✅ Decorator stacking `@mcp.tool() → @safe_tool → @audit_log()`
  consistent on all but `get_server_config`.
- ✅ Lazy-load idempotency.
- ✅ `safe_tool` error envelope uniform.
- ✅ Short-GUID contracts locked.
- ✅ `_strip_noise()` applied via `_json()` (except where HP-4
  bypasses).
- ✅ FX gain/loss in `pay_invoice` (modulo HP-5 third-currency
  edge).
- ✅ Date boundaries inclusive at both ends.
- ✅ Future transactions excluded from "now" balances via SQL filter.
- ✅ No SQL injection — 7 `text()` sites all parameterized.

---

## Suggested order of operations

This is no longer "release-prep with polish" — it's a correctness
sweep before release.

1. **Branch 1: SB-1 through SB-15 (correctness blockers).** Single
   branch. Each fix is small (most are one-line or a single
   helper). Bookkeeper-validate against both Alex (USD) and Lin
   Wei (CNY) before opening the PR. Add regression tests for each.
2. **Branch 2: HP-1 / HP-2 / HP-3 (invariant integrity).** Either
   bring writes up to the verify standard OR relax CLAUDE.md; add
   the six missing audit handlers; consolidate voided-split
   filtering into `_is_voided`. Bookkeeper-validate.
3. **Branch 3: HP-4 through HP-12 (correctness + I/O sweep).**
   Bookkeeper-validate.
4. **Branch 4: MP-* and L-* polish.** Optional pre-release.
5. **Release PR develop → main.** Version bump in this PR per
   `feedback_version_bump_last.md`.
6. **R-* refactor backlog** as v1.4 branches.

Each step follows `feedback_pr_after_bookkeeper_loop.md`: work on a
local branch, bookkeeper validates, PR opens as the outcome.

---

## What the methodology change taught us

The first pass found two ship-blockers and ~20 medium-priority
items. The adversarial pass found 15 ship-blockers and 12 high-
priority items. The delta isn't because the codebase got worse
between passes — it's because the first pass trusted comments and
the second walked the math.

Specifically:

- **The `_rates_as_of` pattern**. The first pass found one site
  (`balance_sheet`). The adversarial pass found five. The
  difference: math-walking each report from inputs to outputs
  versus reading and trusting "the helper handles this" comments.
- **Voided-split semantics**. The first pass had zero findings.
  The adversarial pass found six. The difference: explicitly
  enumerating "what if state and value disagree" rather than
  assuming the convention holds.
- **`_resolve_account` asymmetry**. The first pass said
  "template-account filtering is consistent." Adversarial pass:
  three input branches, only one filters.
- **Audit suppression (HP-2)**. The first pass cited "audit-log
  dispatcher architecture is clean." Adversarial pass: six write
  operations emit NOTHING.

The lesson for the next reviewer (or the next compaction-survivor
self): if your review's "what looks right" section is longer than
your "findings" section, you weren't being skeptical enough.
Distrust the comments. Walk the math. Construct specific failure
scenarios before reading the code.
