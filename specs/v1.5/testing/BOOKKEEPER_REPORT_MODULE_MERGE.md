# Bookkeeper loop: refactor/merge-business-modules

Three rounds on Alex (`samples/alex-chen-morales.gnucash`), server
bounced onto the branch before each. All three closed clean; the
bookkeeper's final word was "ship it". Probe data was left in the
sample book and is removed by `git restore` on the sample.

## Round 1: module realignment

- `--modules=bookkeeper`: 60 tools reported (59 plus `switch_book`
  on a multi-book config). core[9 sub-modules] plus
  bookkeeper[budgets, portfolio, reporting, scheduling, tax_lots].
  The 27 business tools absent from the client, not gated.
- Lot path end to end (create_lot, create_transactions with qty,
  assign_split_to_lot, calculate_lot_gain, get_lot), the sequence
  the old gate refused. Clean.
- `--modules=business`: 57 tools (30 core, 27 business); every
  bookkeeper tool gone from the client. Vendor bill and employee
  voucher end to end, partial and final payments, unpost refusal
  with payments applied. Audit labels correct throughout.

Findings, all fixed on the branch: the ID-collision error coached
`owner_type`, a parameter no document tool exposes; `get_document`
carried no payment state; `pay_document`'s `amount_paid` was per
call, not cumulative. One out-of-scope bug hashbanged:
`delete_transaction` silently orphans a lot-assigned split.

## Round 2: the fixes

- Payment state on `get_document` for a bill and a voucher: open
  with no amounts; posted with paid 0 and full due; partial; paid
  and still legible after leaving the unpaid list; agreement with
  `get_outstanding_documents` to the character.
- Audit PAY lines carry `total paid`.
- Three-way ID collision (invoice, bill, voucher on 000010): error
  names `document_type` and `party_type`; following it literally
  resolved without a schema rejection.
- `create_job` / `list_jobs` / `apply_credit_note` with
  `party_type`; `owner_type` rejected at the schema.

Findings, all fixed on the branch: amount_due and remaining_balance
unpadded beside padded paid totals; job responses echoed
`owner_type`. Rulings taken: CREATE audit lines render the
counterparty as `Name (id)`; a settled credit note reads `applied`.

## Round 3: the round-two fixes and rulings

- Every settlement amount at two places on pay and get.
- CREATE BILL, CREATE CREDIT NOTE, CREATE JOB lines render
  `Name (id)`.
- Job response carries `party_type` and `owner_name`, no
  `owner_type`.
- Credit note applied in full reads `applied`; its target invoice
  still reads `posted` with the applied amount as `amount_paid`.

Routed around: nothing. Residual, not a blocker: entry rows inside
`get_document` still render `price: 450 / total: 450` under a
document total of `450.00`. A unit price may carry more precision
than the currency quantum, so this needs a small design call
rather than a blind quantize; deferred.
