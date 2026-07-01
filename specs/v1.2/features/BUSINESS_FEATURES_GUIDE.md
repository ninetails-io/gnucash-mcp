# GnuCash Business Features: How It All Fits Together

## The Core Idea

GnuCash's business features are a **layer on top of double-entry accounting**. They don't replace the ledger — they generate ledger entries. An invoice is not a transaction. An invoice is a *promise* that eventually becomes a transaction when you post it.

## The Actors

**Customers** owe you money. **Vendors** you owe money to. That's it. They're just address books with IDs.

## The Lifecycle of an Invoice

Think of it as three phases:

### Phase 1: Draft — "Someone owes us money, but it's not in the books yet"

- You create an invoice tied to a customer
- You add line items (entries) — each one says "we sold X units of Y at $Z, charged to this income account"
- Nothing touches the ledger. No transactions exist. It's a memo.

### Phase 2: Post — "This is now real. Put it in the books."

- Posting is the moment the invoice enters the accounting system
- GnuCash creates a **transaction** with splits:
  - **Debit** Accounts Receivable (someone owes you $500)
  - **Credit** Income:Sales (you earned $500)
- It also creates a **lot** — a container that tracks "this specific $500 that Acme owes us"
- The A/R split gets assigned to the lot
- The transaction gets locked (read-only) and tagged with metadata so GnuCash knows "this was generated from invoice #000001, don't let humans edit it directly"

### Phase 3: Pay — "They paid us"

- Payment creates another transaction:
  - **Debit** Checking Account (money came in)
  - **Credit** Accounts Receivable (they no longer owe us)
- The A/R split gets assigned to the **same lot**
- Now the lot has +$500 (posting) and -$500 (payment) = $0 balance → lot closes

## Vendor Bills: The Mirror Image

Identical lifecycle, flipped:

- Bill entry: **Credit** Accounts Payable, **Debit** Expenses
- Payment: **Debit** A/P, **Credit** Checking
- Same lot mechanism for tracking "which specific bill did this payment cover"

## Why Lots Matter

Without lots, A/R is just a number — "$1,200 outstanding." With lots, you know:

- Invoice 001: $500, fully paid
- Invoice 002: $700, $200 paid, $500 remaining

Lots connect specific invoices to specific payments. Partial payments work because a lot can have multiple payment splits.

## The Metadata Web

GnuCash uses **slots** (key-value metadata) to weave everything together:

```
Invoice record
  ├── post_txn → posting transaction
  ├── post_lot → the lot
  └── post_acc → A/R account

Posting transaction (slots)
  ├── trans-txn-type: "I" (invoice-generated)
  ├── trans-read-only: "Generated from an invoice..."
  ├── trans-date-due: 2026-03-25
  └── gncInvoice → back-pointer to invoice

Lot (slots)
  └── gncInvoice → back-pointer to invoice

Payment transaction (slots)
  └── trans-txn-type: "P" (payment-generated)
```

This web of pointers is what lets GnuCash navigate: click an invoice, see its transaction; click a transaction, jump back to the invoice; look at a lot, know which invoice it belongs to.

## What the A/R Register Looks Like

```
Date       Description      Type      Debit    Credit   Balance
2026-02-25 Bob's PTA        Invoice   $75.00            $75.00
2026-02-25 Bob's PTA        Payment            $75.00   $0.00
```

The "Type" column comes from `split.action`. The description comes from the customer name. The transaction number is the invoice ID. All cosmetic but all expected by the UI.

## Database Tables Involved

| Table | Purpose |
|-------|---------|
| `customers` / `vendors` | Address books with IDs and currency |
| `billterms` | Payment terms (e.g., Net 30, 2/10 Net 30) |
| `invoices` | Invoice/bill records with linkage to posting txn, lot, and account |
| `entries` | Line items — quantity, price, linked to income/expense accounts |
| `transactions` | Posting and payment transactions (standard double-entry) |
| `splits` | Transaction splits with lot assignment for payment tracking |
| `lots` | Containers grouping an invoice's posting split with its payment splits |
| `slots` | Metadata slots linking everything together |

## The gncInvoice Slot Structure

The `gncInvoice` back-pointer is stored as a two-row FRAME+GUID structure in the slots table:

```
Row 1: obj_guid=<transaction_guid>, name='gncInvoice', slot_type=9 (FRAME), guid_val=<frame_guid>
Row 2: obj_guid=<frame_guid>, name='invoice', slot_type=5 (GUID), guid_val=<invoice_guid>
```

This same structure appears on both the posting transaction and the lot. It's how GnuCash navigates from any related object back to the originating invoice.

## GnuCash Boolean Convention

GnuCash uses `-1` for boolean true (not `1`). This applies to `lots.is_closed` and other boolean fields. Python treats both as truthy, but GnuCash's C code expects `-1`.
