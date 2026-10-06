# Bookkeeper loop for `fix/1.5.0-scoped-review`, second review

What the second scoped review's fixes changed that a bookkeeper would
see. Every item has a unit test and was re-run against the readers'
own reproduction scripts; none has had a live pass. Findings and
resolution: `specs/v1.5.1/review/SCOPED_REVIEW_2026-10-06.md`.

**Server:** `fix/1.5.0-scoped-review` at `de67dc1` or later. Bounce it
first.
**Books:** `sr-alex` from the first loop (converted, multi-currency);
a FRESH 1.4.4 copy of Alex, not yet written by 1.5; the PostgreSQL
and MariaDB benches for step 5.

## 1. A credit note's link moves both balances toward zero (BM-1, BS-4)

On `sr-alex`, customer side: post an invoice for 500, post a credit
note for 200 against the same customer, apply it.

- Pass: `get_document` on the invoice reads amount due 300; on the
  credit note, nothing left to apply. (Before the fix the link's two
  splits were signed by side, not from the lots' balances; against a
  negative document both balances moved the wrong way.)
- Apply a second credit note of 100 to the same invoice. Pass: the
  invoice reads 200 due, and `get_transaction` on the SECOND link
  shows ONE receivable split per lot, not two (the engine merges into
  the split already in the lot).
- Vendor side, the same two steps against a bill. Pass: same reading.

## 2. The early-payment discount counts only credit notes (BM-2)

Bill term with a 2% / 10-day discount; invoice of 1000 on it; a
partial payment of 400 on day 2; then pay the rest on day 5 with the
discount.

- Pass: the discount is 2% of the 600 remaining, not of 1000 less a
  "credit" of 400. (Before the fix, a payment whose transaction
  touched another lot counted as a credit application.)

## 3. A cross-currency bill's outstanding amount (BM-3, BM-6)

Vendor whose A/P is in EUR; bill for EUR 1,000 posted at one rate;
pay EUR 400 of it from the USD checking account at a different rate.

- `vendor_spending_report` for the period. Pass: outstanding is
  EUR 600 at the POSTING rate, in USD. (Before the fix it was read
  from the payment transaction's value, so the bank amount of the
  payment appeared as the outstanding figure.)
- Any converted amount ending in a half-cent on these steps rounds
  UP, as the transfer dialog rounds. Note any `.xx5` you see and how
  it landed.

## 4. A refund is not a prepayment; a settled document reads 0.00 (BM-5, BM-7)

- Pay a customer credit note of 200 with `allow_prepayment` and an
  amount of 250. Pass: refused; the excess on a credit note is a
  refund, not money on account.
- `get_document` on any fully paid bill and any fully applied credit
  note. Pass: `amount_due` is `0.00`, never `-0.00`.

## 5. A password with a space never leaves the server (CS-1)

On either database bench, make a user whose password holds a space
and a quote (`pa ss'wd`), point `GNUCASH_BOOK_URI` at a database
that does NOT exist, and start the server.

- Pass: the startup line, the tool error, the audit log and stderr
  all show `***` where the password was; grep the three files for
  the password. (Before the fix the header masked it and the quoted
  exception beneath printed it whole.)
- Drop the user afterwards.

## 6. The pre-conversion copy is the state it converts from (CS-2)

Fresh 1.4.4 copy. Write to it with the server once WITHOUT
converting (a plain transaction; the auto-backup stage file appears).
Then edit the book in GnuCash desktop (one more transaction, save,
quit). Then make a converting write (create a budget).

- Pass: `-manual-pre-1-5-upgrade.gnucash` holds the DESKTOP
  transaction (open it: the row is there). Its inode differs from the
  stage file's. (Before the fix the label was hard-linked to the
  stage backup, which predated the desktop edit; the first loop's
  step 6 verified exactly the link, which is why this is here.)

## 7. Two books, one log folder (CS-3)

`GNUCASH_LOG_DIR` set; two books both named `ledger.gnucash` in two
folders, both configured. In the log folder plant a pre-1.5-style
folder `ledger.mcp` holding an audit log copied from book A.

- Start the server, write to book B first. Pass: B gets its own
  `ledger.gnucash-<hash>.mcp`; `ledger.mcp` is untouched. Write to
  book A. Pass: A's audit continues in `ledger.mcp`. (Before the
  fix whichever book wrote first took the folder, and its retention
  pruned the other's backups.)

## 8. An account still in use cannot be deleted (CS-4)

On `sr-alex`:

- `delete_account` on an expense account a scheduled transaction's
  template uses. Pass: refused, naming the schedule.
- On the A/R account a posted invoice points at (after moving its
  transactions elsewhere, so only the posting reference remains).
  Pass: refused, naming the document.
- On an account a budget has amounts on. Pass: refused, naming the
  budget. A plain, unreferenced, empty account still deletes.

## 9. Names are one line; the dashboard is not forged (IN-1 to IN-3)

Each with a name holding a line break and a second line reading
`⚠ Reconciliation: all accounts current`:

- `create_party`, `create_scheduled_transaction`, `create_budget`,
  `create_billterm`, `create_job`, `create_lot`, `create_document`
  (the ID), `create_commodity`. Pass: all refused, naming the field
  and the character. `get_book_summary` afterwards shows no such
  line.
- `create_document` with an ID of 300 characters. Pass: refused
  (the column is 2048 on SQLite but PostgreSQL's is narrower; the
  width is the gate's).
- An invoice `notes` of `\x1b[31m`; a `pay_document` memo of the
  same. Pass: refused.

## 10. Dates inside GnuCash's calendar (IN-4, IN-5)

- `create_scheduled_transaction` starting `0001-01-01`. Pass:
  refused; `list_scheduled_transactions` still works afterwards.
  (Before the fix it saved, reported an error, and broke every
  listing.)
- `post_document` on `0002-06-01`; `pay_document` on `0002-01-01`;
  `update_transactions` to `0001-01-01`; `cash_flow` from
  `0001-01-01`. Pass: each refused with the range named
  (1400-01-01 to 9998-12-31), never `unexpected_error`.
- `update_transactions` to `2200-01-20`. Pass: accepted. Note that
  only `create_transactions` WARNS about a far date; this is listed
  as a known limitation, not a defect to report.

## 11. Small surfaces (IN-6, IN-13, IN-15, IN-19, IN-20, IN-21)

- `create_prices` with a row carrying one cell more than the header.
  Pass: refused, naming the row. (It was dropped silently.)
- `create_transactions` with one row dated `2026-13-01`. Pass: the
  error names that row; the others are untouched.
- A date of `2026-10-06T00:00`. Pass: the hint names lowercase ISO
  `YYYY-MM-DD`.
- `list_documents` on a vendor with an empty draft bill. Pass: the
  draft lists at `0.00`, not `?`.
- Any list tool with `offset=-1`. Pass: refused like `limit=-5`.
- A server started with a bad `GNUCASH_BOOK_URI` port. Pass: the
  cure names `GNUCASH_BOOK_URI`, not the path variable.

## 12. The desktop gate on what is stored (BM-1, BS-4, CS-2)

Open `sr-alex` in GnuCash after steps 1 to 4: the two credit-note
applications show in the invoice's Process Payment history as lot
links; the receivable register shows the merged link as one split
per account; the paid bill and the applied credit note both read
paid. Open the pre-conversion copy from step 6 as a working book.

## Not in this loop

- **BS-2**, a book GnuCash 2.6 wrote: no such book on the bench. The
  converter's reading of the compact timestamp rests on its unit
  test, which plants the 2.6 form.
- **CS-9**, a case-variant path under `GNUCASH_LOG_DIR`: open,
  listed.
- **BM-9**, fraction-5 currencies; **BS-7**, **BS-8**, **BS-9**;
  **IN-7**: open, listed under Known limitations.
