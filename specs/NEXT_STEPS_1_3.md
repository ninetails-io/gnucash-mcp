# Next Steps — v1.3

The 1.3 roadmap. The headline is the business module getting a
proper accountant-grade complement: tax tables, jobs, credit notes,
and employee expense vouchers — the half of the business module
that was deliberately scoped out of 1.2 so 1.2 could ship as a
focused half. Plus a handful of code-hygiene items that surfaced
during 1.2.1 and are queued to land alongside.

When 1.3 work begins, sweep this file into the implementation plan.
When 1.3 ships, items that landed move to ``CHANGELOG.md`` and
remaining items either roll forward or get retired with a brief
note.

---

## Headline features

### Tax tables

piecash supports `Taxtable` with rates and account routing. Required
for VAT/sales-tax handling on invoice line items. The expected shape:

- `create_taxtable(name, rate, account)` — define a rate that posts
  the tax portion to a specified liability account
- `add_invoice_entry` and `add_bill_entry` accept an optional
  `taxtable` parameter that splits the line into pre-tax and tax
  amounts at posting time
- Existing invoices/bills without a taxtable continue to behave
  exactly as today (it's an additive parameter)

This is the single feature most-requested by users in non-US
jurisdictions where invoicing without VAT/GST is non-compliant.

### Jobs

piecash `Job` groups invoices and bills under a parent project or
contract. Used for per-project billing summaries. The expected
shape:

- `create_job(customer_or_vendor_id, name, reference)` — define a
  job tied to one entity
- `create_invoice` / `create_bill` accept an optional `job_id`
- New per-job report tool surfaces total billed, paid, and outstanding
  for each job

### Credit notes

The conventional accountant-facing way to reverse a posted invoice.
v1.2.1's `unpost_invoice` covers the simpler case (delete the post
and start fresh); credit notes preserve the original invoice and
post a counter-invoice that nets against it. Both have legitimate
use cases — accountants generally prefer credit notes for any
month-closed invoice since unposting rewrites history.

### Employee expense vouchers

piecash `owner_type=5`. The third document type in the business
module after invoices (customer-facing) and bills (vendor-facing).
Vouchers track employee expense reimbursement. Today rejected at
the entry point with a clear "not yet supported" message; the 1.3
implementation routes through the existing
`_create_business_document` helper so cross-currency support comes
along for free.

---

## Code hygiene

### Indexed account-by-GUID lookups in the business module

Five sites in `src/gnucash_mcp/book/business.py` use linear
`for a in book.accounts: if a.guid == X` scans where an indexed
`book.session.query(Account).filter_by(guid=X).first()` would be
O(1):

- `get_invoice` (~line 2335) — builds an account-paths map for
  rendering entry line items.
- `post_invoice` (~line 2552) — looks up each entry's account GUID
  before constructing splits.
- `pay_invoice` (~line 2860) — looks up `inv.post_acc_guid` to
  resolve the A/R or A/P account for the payment transaction.
- `get_outstanding_invoices` (~line 3441) — looks up each
  outstanding invoice's `post_acc_guid` to compute lot balance.
- `vendor_spending_report` (~line 3628) — same pattern.

None are correctness bugs today. `post_acc_guid` is always set by
`_resolve_account` (which already excludes templates), so the linear
scans never produce wrong results. The performance cost is invisible
on books up to a few hundred accounts but scales poorly past 1,000.

**Plan:** introduce a `_find_account_by_guid(book, guid)` helper in
`_base.py` (parallel to `_find_transaction` / `_find_split`, both
already indexed). Replace the five linear scans with the helper. Add
a template filter in the helper for belt-and-suspenders, even though
`post_acc_guid` is already validated upstream.

**Estimated scope:** ~40 lines of code, 5 call-site swaps, no
behavior change. One commit.

### Per-currency report segmentation

`spending_by_category`, `income_by_source`, and the older corners of
the reporting module that aggregate amounts across accounts of
different commodities. v1.2.1 fixed this for `balance_sheet`,
`net_worth`, `cash_flow`, and `get_book_summary` via the
`_split_in_default_currency` pattern (cross-commodity values
converted at `shares × latest_price` with cost-basis fallback). The
two breakdown reports above still use raw `split.quantity` sums —
fine when income/expense accounts are all in the book default
currency (the common case), wrong when a foreign-currency expense
account exists.

**Plan:** thread the same factor pattern through
`spending_by_category` and `income_by_source`. Or, more ambitiously,
add a `currency` parameter to the breakdowns that restricts to one
currency at a time and emits per-currency sections.

**Estimated scope:** ~50 lines, mostly reuse.

### `"value"` key in `get_unreconciled_splits` actually holds quantity

Cosmetic naming lie. The dict key is `"value"` but the data is
`split.quantity` (account commodity), not `split.value` (transaction
currency). On single-currency books they're identical; on multi-
currency books they diverge. Predecessor 4.6 flagged this; still true.

**Plan:** add a new `"quantity"` key alongside `"value"` in 1.3,
document `"value"` as deprecated. Drop `"value"` in 1.4. (Wire-
breaking changes deserve a deprecation cycle.)

### DRY the `type='transaction'` price filter

Four sites filter out piecash's auto-created `type='transaction'`
price records:

- `_find_exchange_rate` (`book/business.py`)
- `_market_value` helper inside `get_book_summary` (`book/core.py`)
- `_latest_market_rates` (`book/reporting.py`)
- FX rate handling inside `pay_invoice` (`book/business.py`)

Each site filters correctly; the duplication is just code surface
that could drift if someone changes the filter convention.

**Plan:** hoist a single `_user_supplied_prices(book)` helper in
`_base.py` that yields prices excluding `type='transaction'`. Use it
everywhere. ~30 lines of extraction.

---

## Reporting / accrual-basis

### FX gain/loss: realized vs. mark-to-market

v1.2.1's `pay_invoice` books realized gain/loss at settlement
(cash-basis correct). Mark-to-market revaluation of *outstanding*
A/R at reporting dates is not handled — for accrual-basis reporting
with material foreign-currency A/R, you'd revalue open A/R to the
current rate periodically with the delta to FX Gain/Loss. Not a bug
in 1.2.1; a real reporting feature for 1.3+.

**Plan:** new tool `revalue_open_ar(as_of_date)` that walks open
lots in foreign-currency A/R / A/P, computes the delta vs. their
posted rate, and books an adjusting transaction. Plus the inverse
on the next reporting period to unwind the previous adjustment.

**Estimated scope:** new tool, ~150 lines, plus careful testing
against a multi-currency persona.

---

## Working with this file

- New 1.3 work: add an entry above with scope, plan, and an
  estimate.
- Item lands in 1.3: move the entry to `CHANGELOG.md` under the
  v1.3 release.
- Item slips to 1.4: leave it here, add a one-line dated note
  ("deferred to 1.4 because X").
- Item declined: leave it here, add a one-line dated note
  ("declined — reason"). Don't delete; the trail helps the next
  reader.
