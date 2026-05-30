# Early-Payment Discount Specification
## GnuCash MCP Server v1.3 — Billterms Correctness

**Status:** Draft for review
**Target:** v1.3 (on `feat/v1.3-blockers` — this is a release-blocker correctness fix)
**Prerequisites:** v1.3 billterms (already shipped — fields stored, never honored)

---

## Executive Summary

`create_billterm` accepts `discount_days` and `discount_percent`
fields. They're stored. They're returned by `get_billterm`. And
`pay_invoice` ignores them entirely.

A freelancer setting "2/10 Net 30" terms on a $1,000 invoice
expects the math to honor the early-payment discount. When the
customer pays $980 on day 8, today's behavior records a partial
payment — $20 stays outstanding, the invoice never closes, and
the freelancer has to either chase the customer for the $20
(awkward) or manually write off the $20 (audit nightmare with no
link to the term that authorized it).

Configured-but-silently-ignored fields are exactly the
"financial software that lies to its user" pattern the project
has repeatedly rejected (Strict-kwargs validation;
RECEIVABLE/PAYABLE inclusion in balance_sheet; fail-fast on
typo'd modules). This spec fixes pay_invoice to honor the
discount terms.

Implementation expectation: **~250 LOC + ~12 tests, 2 commits.**

---

## Background: Discount Term Semantics

Standard convention: `"2/10 Net 30"` means "2% discount if paid
within 10 days; otherwise full amount due in 30 days." The
discount applies only to the principal — taxes are computed on
gross then NOT reduced by the discount (or are, depending on
jurisdiction — but the simpler "discount on principal only"
convention is what GnuCash's billterm model encodes).

Two GAAP-accepted accounting treatments:

- **Gross method (proposed default):** invoice books at $1,000.
  Customer pays $980. Books record cash +$980, A/R −$1,000,
  Discount Expense (or Sales Discount contra-revenue) +$20.
  A/R clears fully. This is the standard small-business
  approach.
- **Net method:** invoice books at $980 (presumed-discounted)
  with $20 in an "Interest Income on Late Payment" contra
  account. If customer pays late at $1,000, the $20 is
  recognized as interest income. Less common; not proposed.

Gross method is what create_billterm's fields imply (discount
configured as a reduction taken at payment time, not a
pre-recognized reduction in revenue).

---

## Tool Surface Changes

`pay_invoice` gains two new parameters:

```python
def pay_invoice(
    self,
    invoice_id: str,
    payment_account: str,
    amount: str,
    payment_date: str | None = None,
    description: str | None = None,
    owner_type: str | None = None,
    fx_account: str | None = None,
    apply_discount: bool = False,              # NEW
    discount_account: str | None = None,       # NEW
) -> dict:
    """...
    Args:
        ...existing args...
        apply_discount: When True, treat the payment as
            early-payment-discount eligible. The tool validates
            that the invoice has discount terms AND the payment
            date is within the discount window AND the shortfall
            (grand_total - amount) matches the expected discount
            within commodity precision. If validation passes, the
            shortfall books as a separate split to
            ``discount_account`` (auto-resolved per the FX-account
            pattern). If validation fails, the call rejects with a
            clear error rather than silently treating as a partial
            payment. Default False — explicit opt-in surfaces what
            the freelancer intends.
        discount_account: Optional account to receive the discount
            shortfall. Accepts a full path, ``%short`` GUID, or
            full 32-char GUID. Must be an INCOME or EXPENSE
            account. Same auto-resolution pattern as
            ``fx_account``: explicit > name-match in book >
            canonical auto-create.
    """
```

---

## Design Decisions

### D1: Detection — explicit opt-in, not auto-detect

**Decision: `apply_discount: bool = False`, explicit parameter, default opt-out.**

Two reasons:

1. **Auto-detection is risky.** A customer paying $980 of $1000 *could* be taking the discount (within window) or *could* be making a partial payment that coincidentally matches. Math-based detection ("if shortfall ≈ expected discount, it's a discount") risks false positives — exactly the kind of silent inference financial software should not make.

2. **Opt-in surfaces intent.** The freelancer calling `pay_invoice(amount="980", apply_discount=True)` is stating "this payment closes the invoice via discount." The tool validates that statement; if any condition fails (no terms set, outside window, amount doesn't match expected discount), it rejects with a specific reason. The user knows exactly what happened.

Reject reasons cover:

- Invoice has no billterm
- Billterm has no `discount_days`/`discount_percent` set
- `payment_date - invoice.date_opened > discount_days`
- `(grand_total - amount) ≠ expected_discount` within `_commodity_quantum`

Each surface a distinct error message.

### D2: Discount account resolution

Follow the `fx_account` pattern (D2 from the FX gain/loss spec):

1. **Explicit `discount_account`** if supplied. Validate exists, validate is INCOME or EXPENSE.
2. **Search book by leaf-name match** for common discount-account names:
   - Customer-side (`owner_type='customer'`): `"sales discounts"`, `"discounts given"`, `"customer discounts"`, `"sales discount"`
   - Vendor-side (`owner_type='vendor'`): `"purchase discounts"`, `"discounts taken"`, `"purchase discount"`, `"vendor discounts"`
3. **Canonical auto-create** if no match:
   - Customer-side: `Expenses:Sales Discounts` (gross-method convention; the discount is a cost of getting paid fast)
   - Vendor-side: `Income:Purchase Discounts Taken`

If multiple candidates match the leaf-name search, include a `discount_notice` field in the response listing them, so the caller can pass `discount_account=` explicitly next time. Same UX as the FX `fx_notice`.

### D3: Cross-currency interaction

Order of operations: **discount first, then FX gain/loss.**

A EUR invoice for €1,000 with 2/10 Net 30. Customer pays €980 within window in USD on a USD-default book.

1. Compute discount in invoice currency: €20
2. Effective invoice amount: €980 (gross-method: A/R clears full €1,000, discount books €20 to Expenses:Sales Discounts in EUR-converted-to-USD-at-payment-date-rate)
3. Customer's USD payment converts to €980 at the payment-date EUR/USD rate
4. If the rate moved between post-date and pay-date, the FX gain/loss split absorbs the delta (existing v1.2.1 logic)

The discount and FX gain/loss splits are independent — discount is "what the invoice settlement reduced by"; FX gain/loss is "what the rate movement contributed." Both can exist on the same payment transaction; the math composes cleanly.

### D4: Vendor side parity

Same logic, mirrored. A vendor offering us "2/10 Net 30" on a $1000 bill: we pay $980 within window, take the $20 as `Income:Purchase Discounts Taken`. The polymorphic `owner_type` dispatch handles routing.

### D5: Partial payments within window

What if customer pays $500 of $1000 within the discount window?

**Decision: partial payment, no discount applied, no error.** The `apply_discount=True` flag *means* "this payment closes the invoice via discount." A partial payment doesn't close anything, so the flag is incorrect for that call. The tool should reject the call with a specific error: `"apply_discount=True requires a full settlement payment; got 500.00 of 1000.00 outstanding"`.

The freelancer wanting partial payment within window omits `apply_discount`. The remaining $500 stays open and can still be settled with the discount on a subsequent call if all conditions hold then.

Edge case: what if the user makes multiple payments and the *last* one closes the invoice via discount? Then that last call uses `apply_discount=True` and the math works on the remaining balance. This naturally falls out — `expected_discount` is computed from the original `grand_total × discount_percent / 100`, the shortfall on the closing payment matches, the tool accepts.

### D6: Past the window but matches discount amount

Customer pays $980 of $1000 on day 15 (window is 10). User calls `pay_invoice(amount="980", apply_discount=True)`.

**Decision: reject with clear error.** The discount terms are *time-conditional*. Paying late and claiming the discount is a renegotiation, not an automatic right. Tool rejects: `"Payment date 2026-05-20 is beyond billterm discount window (10 days from 2026-05-05; deadline was 2026-05-15)"`.

The freelancer wanting to grant the discount anyway has two paths:
- Issue a credit note for $20 to clear the remainder (formal write-off, audit trail)
- Reduce the invoice amount via `update_invoice` if it's not posted; otherwise unpost → adjust → repost (existing workflow)

Either path leaves a real record of the negotiated outcome.

### D7: Same-payment-different-account

What if the user wants to record the discount but to a different account than the auto-resolved default? They pass `discount_account=` explicitly. Same shape as `fx_account=`. No additional design.

---

## Testing Strategy

Three layers, mirroring the existing test patterns:

### Same-currency happy path (4 tests)

- Customer invoice with 2/10 Net 30, pay within window, full discount applies, A/R clears, Sales Discounts expense booked.
- Same but vendor side: pay vendor bill within their discount window, Purchase Discounts Taken income booked.
- Pay at the discount boundary (day 10 exactly) — accepted.
- Pay at day 11 — rejected with deadline-passed error.

### Validation rejections (5 tests)

- `apply_discount=True` on invoice without billterm → "no billterm" error
- `apply_discount=True` on billterm without discount → "term has no discount" error
- `apply_discount=True` past window → "outside window" error
- `apply_discount=True` with wrong amount → "amount doesn't match expected discount" error
- `apply_discount=True` on partial payment → "requires full settlement" error

### Account resolution + cross-currency (3 tests)

- Explicit `discount_account=` — used as-is
- Auto-resolution via leaf-name match — finds existing "Sales Discounts" account
- Cross-currency invoice (EUR billed on USD book) with discount + FX gain/loss — both splits land correctly, transaction balances in invoice currency

---

## Implementation Plan

Two commits on the existing `feat/v1.3-blockers` branch:

**Commit 1: `feat(business): early-payment discount in pay_invoice`**
- `_resolve_discount_account` helper paralleling the FX helper
- `apply_discount` + `discount_account` parameters on book-layer `pay_invoice`
- Validation chain (terms exist? has discount? in window? amount matches?)
- Discount split written alongside payment + A/R clear
- Cross-currency interaction with existing `_compute_fx_gain_loss`

**Commit 2: `feat(business): early-payment discount tool params + tests`**
- Expose new parameters on the `pay_invoice` tool wrapper in `tools/business.py`
- 12 regression tests across same-currency, validation, and cross-currency cases
- CHANGELOG entry under "Multi-currency aggregation sweep" (same v1.3 release-prep section)

No split into a separate branch — this is correctness on a feature that already shipped (billterms in v1.3 Stage 3). Belongs on the release-blockers branch alongside the other corrections.

---

## Open Questions

These need a decision before commit 1:

- [ ] **Account convention naming.** Proposed: `Expenses:Sales Discounts` (customer-side) and `Income:Purchase Discounts Taken` (vendor-side). Alternatives: `Income:Sales Discounts Given` (treating as contra-revenue), `Expenses:Purchase Discounts` (treating taken discounts as cost reduction). Both alternatives are valid GAAP; the proposed convention matches more small-business accounting templates.
- [ ] **Discount on tax included in line items?** GnuCash's billterm convention: discount applies to pre-tax principal only. Sales tax is computed on gross then NOT reduced. Confirm this matches your bookkeeper-validated expectation.
- [ ] **`apply_discount=True` on a credit-note payment?** Currently credit notes flow through `pay_invoice` with the inverse-amount convention. Probably want to reject `apply_discount=True` on credit notes entirely (no semantic meaning for "discount on a refund").
- [ ] **`get_invoice` verbose mode.** Should the response include a computed `discount_eligible_until` date and `discount_amount` when the invoice has discount terms? Forward-looking signal for the LLM: "you could close this with discount until X." Cheap to add; helpful for the freelancer workflow.

---

## Out of Scope (not v1.3)

- **Net-method accounting treatment.** Gross method only.
- **Discount on credit notes.** Rejected.
- **Multi-tier discounts.** GnuCash billterm only supports one discount window; "5/10, 2/20, Net 30" would need a different data model.
- **Discount on partial payments.** The closing payment can take the discount on the remainder; intermediate payments cannot.
- **Discount expiration warning in `get_book_summary`.** "3 invoices with discounts expiring this week" would be useful but is a dashboard feature, separable.

---

## Why This Lands on v1.3 (Not Deferred)

Per the bookkeeper-validation memory just filed: financial-math
bugs surface from code review, not bookkeeper signoff. This one
came from a predecessor's "I haven't verified..." note that
never got verified. Shipping v1.3 with the feature documented
but unimplemented would be the same silent-lie pattern strict-
kwargs rejected.

The freelancer persona (just promoted to a coherent module in
the previous commit) IS the target user for this feature. Solo
invoicers with discount terms ARE who v1.3's freelancer module
markets to. Shipping that persona with a known broken term-
handling path while highlighting freelancer mode in the README
would be the kind of mismatch that erodes trust.

Per "we don't have the resources to take shortcuts" — this is
the correct work.
