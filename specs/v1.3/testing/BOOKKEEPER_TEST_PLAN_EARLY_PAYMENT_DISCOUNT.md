# Bookkeeper Test Plan — Early-Payment Discount

**What's new since last signoff:** `pay_invoice` now honors the
`discount_days` / `discount_percent` fields on billterms via an
explicit `apply_discount=True` opt-in. `get_invoice` verbose
mode surfaces forward-signal blocks for invoices with discount
terms. Spec at `specs/EARLY_PAYMENT_DISCOUNT_SPEC.md`.

## Pre-flight

Confirm the new code is live by reading the `pay_invoice` tool
description. It should mention `apply_discount` and
`discount_account` parameters. If those aren't there, the
server is on stale code — ask Stephen to bounce.

## Test setup

Create a test billterm + customer + invoice with discount
terms. Suggested values keep the math obvious.

```
create_billterm(
    name="2/10 Net 30",
    due_days=30,
    discount_days=10,
    discount_percent="2",
)

create_customer(name="Discount Test Co")

create_invoice(
    customer_id=<customer id>,
    term="2/10 Net 30",
    date_opened=<today's ISO date>,
)

add_invoice_entry(
    invoice_id=<invoice id>,
    account="Income:Sales",        # or whatever income account exists
    description="Consulting work",
    quantity="1",
    price="1000.00",
)

post_invoice(
    id=<invoice id>,
    post_account="Assets:Accounts Receivable",
    post_date=<today's ISO date>,
)
```

Note the invoice id for the tests below.

## Happy path: discount applied successfully

Pay $980 of the $1,000 invoice within the 10-day window:

```
pay_invoice(
    id=<invoice id>,
    payment_account="Assets:Checking",
    amount="980",
    payment_date=<today's ISO date>,
    apply_discount=true,
)
```

**Expected response includes:**

- `"status": "paid"`
- `"remaining_balance": "0"` (the A/R cleared in full)
- `"discount"` block with `amount: "20.00"`, `currency: "USD"`,
  `account` containing "Sales Discounts" (auto-created if it
  didn't exist)

**Verify the books:** call `get_transaction` on the returned
`transaction_guid`. The splits should be:

- `Assets:Checking` +$980
- `Assets:Accounts Receivable` −$1,000
- `Expenses:Sales Discounts` +$20

Sums to zero. A=L+E preserved.

## Validation rejection tests

Repeat with a fresh invoice per case (or unpost+repost between
tries).

### Past the window

Set `date_opened` to 15 days ago, then call
`pay_invoice(apply_discount=true)`. Expected: error containing
`"beyond the billterm discount window"` and naming the
deadline.

### Wrong amount (intended partial)

Within the window, call `pay_invoice(amount="500",
apply_discount=true)`. Expected: error containing
`"shortfall doesn't match"` and suggesting the correct amount.

### Invoice with no discount terms

Create an invoice with `term="Net 30"` (no discount fields) or
with no term at all. Call `pay_invoice(apply_discount=true)`.
Expected: error about "no billterm linked" or "no early-payment
discount configured."

### Apply to a credit note

Create + post a customer credit note, then call
`pay_invoice(apply_discount=true)` on it. Expected: error
`"Discounts cannot be applied to credit note settlements"`.

### Unknown parameter typo

Call `pay_invoice(apply_dscount=true)` (typo). Expected:
validation error from the strict-kwargs guard
(`extra="forbid"`), naming the unknown field.

## Get_invoice forward signal

Call `get_invoice(id=<invoice id>)` (verbose mode is default
for `get_invoice`) on the discount-eligible test invoice.

**Expected response includes a `discount_available` block:**

- `amount`: "20.00"
- `currency`: "USD"
- `percent`: "2"
- `eligible_until`: ISO date 10 days from `date_opened`

**Then set date_opened to 15+ days ago** (recreate the
invoice), call `get_invoice` again. The block should be named
`discount_expired` instead of `discount_available`, with the
same fields.

## Vendor side (optional but ideal)

Same shape mirrored. Create a vendor + bill with discount
terms, then `pay_invoice(apply_discount=true,
owner_type="vendor")`. The discount split should land in
`Income:Purchase Discounts Taken` (auto-created income
account).

## Reporting back

**Green:** confirm happy path + at least 3 of the 5 rejection
cases + the `discount_available` forward signal all produce
expected behavior.

**Findings:** per finding, give the exact tool call + expected
behavior + actual response + severity hunch (blocking /
follow-up / cosmetic).

The unit tests cover all 11 scenarios; this is the live
confirmation that the wire-level behavior matches.
