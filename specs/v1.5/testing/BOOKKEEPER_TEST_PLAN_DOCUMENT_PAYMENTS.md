# Bookkeeper live loop — document payment surfaces (fix/document-payment-surfaces)

Branch under test: `fix/document-payment-surfaces`, from develop at
#193. It answers two "routed around" items from the class-6 loop
(`BOOKKEEPER_REPORT_CLASS6_REVIEW_ITEMS.md`):

- **`list_documents` says `paid`.** Its status column only knew open
  and posted, against the vocabulary its own docstring defines.
  Both it and `get_document` now read `_document_status`; verbose
  rows gain `status`. The `status` filter stays document state
  (`posted` still returns paid documents).
- **`get_document` lists `payments`** once posted: `{guid, date,
  amount, from}` per non-voided settlement in the lot, oldest first.
  `guid` is the settling transaction; `from` is the account the money
  moved through, or the other document's title for an applied credit
  note.

Reading each row's lot made the listing N+1; a preload of the page's
posting accounts, lots, and lot splits keeps it flat (locked by a
count-the-queries test). No report number may move.

The third item, the trade price a deleted transaction leaves behind,
is not changed: GnuCash desktop leaves it too (see the report).

## Part A — the oracles did not move

Capture rig on Alex, Lin Wei (`--today 2025-12-31 --historical
2025-06-30`), and Sabine, develop worktree vs this checkout, **both
sides the same day** — the dashboard's day counts move at midnight.

## Part B — on a copy of Alex, over stdio

1. `list_documents` (limit 250) and `get_outstanding_documents`
   (limit 250). Expected: the documents listed `posted` are exactly
   the outstanding set, by id; the rest read `paid`.
2. `get_document` 000016 (paid): one payment, `9aaa4c7e`, 3,500.00,
   from Checking. 000041 (unpaid): `payments: []`.
3. Foreign currency: 000025 (EUR) and 000033 (CAD) list their payment
   in the document's currency, from Checking — not the FX account.
4. Verbose `list_documents` for bills: each row carries `status`.
5. The bounced-payment workflow: `void_transaction` the guid from
   step 2. 000016 reads `posted`, 3,500.00 due, `payments: []`, and
   the list row reads `posted`. Unvoid: `paid` again, payment listed.

## Routed around

Name every workaround taken, with the tool that forced it.
