# Post-1.2.1 Followups

Items that surfaced during the 1.2.1 patch cycle but were
deliberately deferred. Tracking here so they don't get lost
between releases. Not a roadmap — a catch list.

When 1.3 work begins, sweep this file: pull in what fits the
release scope, leave the rest for the file's next reader.

---

## Indexed Account-by-GUID lookups in the business module

**Where:** five sites in `src/gnucash_mcp/book/business.py` use
linear `for a in book.accounts: if a.guid == X` scans where an
indexed `book.session.query(Account).filter_by(guid=X).first()`
would be O(1):

- `get_invoice` (~line 2335) — builds an account-paths map for
  rendering entry line items. Could be replaced with a query
  for just the GUIDs the entries reference.
- `post_invoice` (~line 2552) — looks up each entry's account
  GUID before constructing splits.
- `pay_invoice` (~line 2860) — looks up `inv.post_acc_guid` to
  resolve the A/R or A/P account for the payment transaction.
- `get_outstanding_invoices` (~line 3441) — looks up each
  outstanding invoice's `post_acc_guid` to compute lot balance.
- `vendor_spending_report` (~line 3628) — same pattern.

**Why deferred:** None are correctness bugs. `post_acc_guid` is
always set by `_resolve_account` (which already excludes
templates), so the linear scans never produce wrong results.
The performance cost is invisible on Alex/Lin Wei-sized books
(~100 accounts) but would scale poorly on a power-user book
with 1k+ accounts.

**Suggested fix:** introduce a `_find_account_by_guid(book, guid)`
helper in `_base.py` (parallel to `_find_transaction` /
`_find_split`, both of which already use indexed queries —
predecessor note from `1M context` Claude flagged this).
Replace the five linear scans with the helper. Add a template
filter in the helper for belt-and-suspenders, even though
`post_acc_guid` is already validated upstream.

**Estimated scope:** ~40 lines of code, 5 call-site swaps, no
behavior change. One commit.

---

## Investment lot iteration in `_account_conversion_factors`

**Where:** `src/gnucash_mcp/book/reporting.py:_account_conversion_factors`
iterates every account, including investment commodity accounts
where the cost-basis fallback path is exercised (`split.value`
when no market rate is available).

**Why deferred:** Not a bug, but the agent audit during the
template-filter pass noted the function is run on every
reporting call (balance sheet, net worth, cash flow, summary).
Caching the rate lookup or memoizing the factor map across
short windows could measurably help on books with many
commodity accounts.

**Suggested fix:** see if the report flow already passes a
shared book session through; if so, attach the factors as a
session-scoped attribute and reuse. Otherwise leave as-is.

**Estimated scope:** investigation + likely a no-op decision;
log here in case a perf concern surfaces later.

---

## `"value"` key in `get_unreconciled_splits` actually holds quantity

**Where:** `src/gnucash_mcp/book/reconciliation.py:get_unreconciled_splits`

**Why deferred:** Cosmetic naming lie. The dict key is `"value"`
but the data is `split.quantity` (account commodity), not
`split.value` (transaction currency). On single-currency books
they're identical; on multi-currency books they diverge.
Predecessor 4.6 flagged this; still true. Renaming is a wire
breaking change so it deserves its own minor-version bump.

**Suggested fix:** add a new `"quantity"` key alongside `"value"`
in 1.3, document `"value"` as deprecated. Drop `"value"` in 1.4.

---

## DRY the `type='transaction'` price filter

**Where:** four sites filter out piecash's auto-created
`type='transaction'` price records:

- `_find_exchange_rate` (`book/business.py`)
- `_market_value` helper inside `get_book_summary` (`book/core.py`)
- `_latest_market_rates` (`book/reporting.py`)
- FX rate handling inside `pay_invoice` (`book/business.py`)

**Why deferred:** Each site filters correctly; the duplication
is just code surface that could drift if someone changes the
filter convention.

**Suggested fix:** hoist a single `_user_supplied_prices(book)`
helper in `_base.py` that yields prices excluding
`type='transaction'`. Use it everywhere. ~30 lines of
extraction.

---

## Per-currency report segmentation

**Where:** `spending_by_category`, `income_by_source`, and the
older corners of the reporting module that aggregate amounts
across accounts of different commodities.

**Why deferred:** v1.2.1 fixed this for `balance_sheet`,
`net_worth`, `cash_flow`, and `get_book_summary` via the
`_split_in_default_currency` pattern (cross-commodity values
converted at `shares × latest_price` with cost-basis fallback).
The two breakdown reports above still use raw `split.quantity`
sums — fine when income/expense accounts are all in the book
default currency (the common case), wrong when a foreign-
currency expense account exists.

**Suggested fix:** thread the same factor pattern through
`spending_by_category` and `income_by_source`. Or, more
ambitiously, add a `currency` parameter to the breakdowns that
restricts to one currency at a time and emits per-currency
sections.

**Estimated scope:** ~50 lines, mostly reuse.

---

## FX gain/loss: realized vs. mark-to-market

**Where:** `pay_invoice`'s realized FX recognition.

**Why deferred:** v1.2.1's `pay_invoice` books realized
gain/loss at settlement (cash-basis correct). Mark-to-market
revaluation of *outstanding* A/R at reporting dates is not
handled — for accrual-basis reporting with material
foreign-currency A/R, you'd revalue open A/R to the current
rate periodically with the delta to FX Gain/Loss. Out of
scope for the patch; this is a real reporting feature, not a
bug.

**Suggested fix:** new tool `revalue_open_ar(as_of_date)` that
walks open lots in foreign-currency A/R / A/P, computes the
delta vs. their posted rate, and books an adjusting
transaction. Plus the inverse on the next reporting period to
unwind the previous adjustment.

**Estimated scope:** new tool, ~150 lines, plus careful
testing against a multi-currency persona.

---

## Tax tables, jobs, credit notes, employee vouchers

**Where:** business module surface gaps.

**Why deferred:** Originally noted as 1.3 scope before the
patch cycle began. Filed here so the list is in one place.

- **Tax tables**: piecash supports `Taxtable` with rates and
  account routing. Required for VAT/sales-tax handling on
  invoice line items.
- **Jobs**: piecash `Job` groups invoices/bills by project or
  contract. Used for per-project billing summaries.
- **Credit notes**: the conventional accountant-facing way to
  reverse a posted invoice. v1.2.1's `unpost_invoice` covers
  the simpler case (delete the post and start over); credit
  notes preserve the original invoice and post a counter-
  invoice. Both have legitimate use cases.
- **Employee expense vouchers**: piecash `owner_type=5`. Today
  out of scope (`delete_employee` docstring notes this); when
  added, route through the existing
  `_create_business_document` helper so cross-currency support
  comes for free.

---

## Update notes when this file changes

Date the entry, briefly say "moved to 1.3," "moved to 1.4," or
"declined — reason." Don't delete entries on completion; keep
the trail for the next reader to learn from.
