# Desktop parity walk — business module

For the maintainer's manual testing before 1.5.0 (Sep 15), and for
the bookkeeper's loop if a fix branch results. The question for
every structure the server writes is the one the schedules failed:
does GnuCash desktop read it, can desktop edit it, and does the
server read what desktop writes back. Nothing here is a report
number; it is all "open it in desktop and look."

Source-verified on 2026-09-10 (`stable` branch), quoted where it
matters. One defect is already confirmed from source and is listed
first; the rest are questions the walk answers.

## 0. Setup

A scratch copy of Alex (he has customers, vendors, an employee,
jobs, billterms, a taxtable, invoices and bills in every state).
Server on the copy, bounced. GnuCash 5.12 opened on the same file
between steps, closed before the next server call (SQLite writes
land immediately; the lock is real). `create_backup` first.

## 1. CONFIRMED FROM SOURCE — the invoice link slot has the wrong key

What GnuCash writes on a posting transaction and on its lot
(`gncInvoice.c` `gncInvoicePostToAccount`:
`qof_instance_set(QOF_INSTANCE(txn), "invoice", guid)`, same on the
lot) resolves through the property maps in `Transaction.cpp` /
`gnc-lot.cpp` to the two-element KVP path
`{GNC_INVOICE_ID, GNC_INVOICE_GUID}`, and `gnc-engine.h` defines:

```c
#define GNC_INVOICE_ID    "gncInvoice"
#define GNC_INVOICE_GUID  "invoice-guid"
```

On disk that is a `gncInvoice` frame row whose child row is named
`gncInvoice/invoice-guid` (full-path naming, the same as
`sched-xaction/account`, which desktop-made schedules confirmed in
the native-templates loop). This server's `_write_gncinvoice_slot`
(`book/business.py`) writes the frame correctly and then a child
named **`invoice`**. GnuCash will never find it.

What that breaks, to confirm in desktop:

- **1a.** Post an invoice here. In desktop, open the A/R register,
  select the posting transaction, and try the register's "Jump to
  Invoice" (right-click / Business menu). Expected on the bug: no
  invoice found, or the item greyed out. Do the same from Tools →
  General Journal and from the account's Lot Viewer (View Lots): the
  lot for a server-posted invoice should show no invoice title link.
- **1b.** Business → Customer → Process Payment for that customer.
  The dialog lists open documents by walking lots and asking each
  lot for its invoice. A server-posted invoice may be missing from
  the list, or appear as an unnamed pre-payment lot. Report exactly
  what the list shows beside a desktop-posted invoice for the same
  customer.
- **1c.** Post an invoice IN desktop, then `get_document` /
  `get_outstanding_documents` here. The server reads the forward
  pointers (`post_txn`, `post_lot`, `post_acc` on the invoice row),
  so this direction should already work; confirm it does.
- **1d.** Read back the rows: `SELECT name FROM slots WHERE obj_guid
  IN (<posting txn guid>, <lot guid>)` and the children of each
  `gncInvoice` frame. Server-posted: `invoice`. Desktop-posted:
  `gncInvoice/invoice-guid`. That diff is the bug on disk.

Fix shape (its own branch, desktop-gated): write
`gncInvoice/invoice-guid`; read both names during the migration
era; on any business write that touches a document, rewrite that
document's posting transaction and lot links to the real key
(nothing else changes, nothing posted); a lock that pins the key
against a copy of the `gnc-engine.h` line, as the budget stamp has.

## 2. Posting transaction and lot — everything else

GnuCash sets, per `gncInvoicePostToAccount`:
`xaccTransSetTxnType(txn, TXN_TYPE_INVOICE)` (KVP `trans-txn-type`
= `"I"`), `xaccTransSetReadOnly(txn, "Generated from an invoice.
Try unposting the invoice.")` (KVP `trans-read-only`),
`xaccTransSetDateDue` (KVP `trans-date-due`), num = invoice id,
description = owner name, currency = invoice currency; lot title
`"%s %s"` of the type string and the id (`Invoice 000047`,
`Bill 000012`, and the voucher / credit-note type strings); each
split's action = the type string, memo as entered.

The server writes `trans-txn-type` `I`, the read-only reason, the
due date, num = id, and lot title `Invoice {id}` — for every
document type.

- **2a.** Open a server-posted invoice's transaction in the A/R
  register in desktop. Expected: the register refuses edits with
  the "Generated from an invoice" message. If it lets you edit, the
  read-only slot isn't being read.
- **2b.** Same for a server-posted BILL (A/P register) and a
  VOUCHER. Then View Lots on A/P: the lot title. Ours says
  `Invoice 000012` on a bill; desktop's would say `Bill 000012`.
  Cosmetic, but note it — the lot viewer is how a desktop user
  finds things.
- **2c.** Split actions: in the register, the Action column on the
  A/R leg and on each income leg. Desktop writes the type string
  ("Invoice"); ours writes `doc_action`. Report what shows.
- **2d.** Due date: Reports → Business → Receivable Aging on a
  server-posted invoice buckets by `trans-date-due`. Confirm the
  aging matches the invoice's due date.

## 3. Payments — the shape question

GnuCash's `gncOwnerApplyPaymentSecs` creates a **payment lot** of
its own (KVP `gncOwner/owner-type` int64 and `gncOwner/owner-guid`,
defines in `gnc-engine.h`), puts the payment split in it, marks the
payment transaction `trans-txn-type` `"P"` with split action
"Payment", and then reconciles it against the invoice's lot with a
**lot-link transaction** (`trans-txn-type` `"L"`, description "Lot
Link", memos "Offset between business items: …"). The server puts
the payment split directly into the invoice's lot and marks the
transaction `P`; it never creates a payment lot or a lot link, and
writes no `gncOwner` slots anywhere.

- **3a.** Pay a server-posted invoice here, in full. In desktop:
  the invoice shows Paid; Process Payment for that customer shows
  nothing outstanding; View Lots on A/R shows the invoice lot at
  zero balance with two splits. Then in desktop, Unpost the
  invoice: GnuCash should refuse or warn because payments exist.
  Report what it does — a silent unpost that leaves the payment
  orphaned in A/R is a finding.
- **3b.** Pay the SAME invoice in desktop instead (Process Payment
  dialog), then `get_document` here: `status: paid`, `amount_paid`
  right. The server's settlement reads splits in the invoice lot;
  desktop's lot link puts a split there too, so this should hold.
  Then `pay_document` here on that already-paid invoice: expect the
  overpayment guard. And `get_outstanding_documents`: gone.
- **3c.** Partial payment in desktop, remainder here. `get_document`
  must show both; `pay_document`'s `amount_due` must equal what
  desktop's invoice window shows.
- **3d.** Pre-payment in desktop (Process Payment with no invoice
  selected). Desktop creates an owner lot with `gncOwner` slots.
  Here: does `get_outstanding_documents` or `get_party` see the
  credit? (Likely not — there is no server concept of an owner
  lot. If not, that is a documented gap, not a corruption.) Then
  post an invoice here and pay it in desktop, letting desktop
  apply the pre-payment; confirm `get_document` reads it paid.

## 4. Credit notes

GnuCash: `#define GNC_INVOICE_IS_CN "credit-note"`, int64 1, on the
invoice's KVP. The server writes exactly that (`invoice["credit-note"]
= 1`). Its `gnc-mcp/applies-to-invoice` link is server-only by
design.

- **4a.** Create and post a credit note here; in desktop, Business →
  Customer → Find Invoice: it must list as a Credit Note, open as
  one, show negative amounts the way desktop shows its own.
- **4b.** Create a credit note IN desktop; here `list_documents`
  shows `(CN)` and `get_document` reads `type: credit_note`.
- **4c.** Apply a server credit note to an invoice here
  (`apply_credit_note`); in desktop the invoice shows the reduced
  balance and Process Payment shows the credit consumed. Then the
  reverse: apply a desktop credit note in desktop's Process
  Payment; `get_document` here shows it applied.

## 5. Entries, tax tables, terms

Entries are table columns (`gnc-entry-sql.cpp` col_table: only
`guid` and `date` non-null; the invoice side is `i_acct`, `i_price`,
`invoice`, `i_taxable`, `i_taxincluded`, `i_taxtable`; the bill side
`b_*` and `bill`). The server fills one side and zeroes the other.
Bool columns are INTEGER and go through `_gnc_bool`.

- **5a.** Open a server-created invoice with 3+ entries in desktop's
  invoice window, edit a quantity, save, post from desktop. Then
  `get_document` here: the edited quantity and desktop's posting.
- **5b.** An entry with a server-created tax table: desktop's
  invoice window shows the tax line and the taxtable name; posting
  in desktop produces the tax split to the taxtable's account. And
  the reverse: a desktop tax table on a server entry
  (`add_document_entry` with `taxtable=`).
- **5c.** A server-created billterm on a desktop invoice: the due
  date desktop computes matches what `get_document` reports. Terms
  with an early-payment discount: desktop's Process Payment offers
  the discount on the same day boundary the server's `apply_discount`
  uses.
- **5d.** Jobs: a server-created job on a desktop invoice's Job
  field; a desktop job read by `list_jobs` / `get_job_report`.

## 6. Parties and IDs

Customers, vendors, employees are ORM-created (piecash), IDs minted
from GnuCash's own counters (`counters/gncCustomer` etc.; documents
from `gncInvoice` / `gncBill` / `gncExpVoucher` with a max-of-table
guard).

- **6a.** Create a customer here, then a customer in desktop:
  desktop's auto ID must be the next number, not a repeat. Same for
  an invoice (`gncInvoice`) and a bill (`gncBill`).
- **6b.** Edit a server customer's address in desktop; `get_party`
  here shows it. Edit here (`update_party`); desktop shows it.
  (piecash's `Address` composite is the trap — the server writes
  the raw columns.)

## 7. Delete paths and the slot cascade

`_strip_guid_slots` now runs before every ORM transaction delete.
The business paths that delete transactions (`unpost_document`,
`delete_document` on a posted document, voiding payments) should be
checked for the same cascade the schedules had:

- **7a.** Set a slot on the A/R account (`set_account_slot`). Post
  and unpost an invoice here. The slot survives. Post, pay, void
  the payment, unpost. Survives.
- **7b.** Delete a posted-then-unposted document here; the customer
  still has its slots and address.

## What to report

Per item, what desktop showed, verbatim where it is text. Item 1 is
already a fix; the rest decide whether it rides the same branch.
The standing question applies: anything routed around while
running the business module through both programs — the bookkeeper
has never had to, and that is exactly where a workaround would have
been living unnoticed.
