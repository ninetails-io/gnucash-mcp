# Bookkeeper loop for `fix/1.5.0-numbers`

What the branch changed that a bookkeeper would see, as steps. Every
item was unit-tested, and the business ones were checked against
GnuCash's engine headlessly; this loop is the live server on a real
book, and the windows only GnuCash can show. Status of every review
item is in `specs/v1.5.1/README.md`.

**Server:** `fix/1.5.0-numbers` at its tip. Bounce it first.
**Book:** a scratch copy of a multi-currency book (Alex), plus any
book that has been open in GnuCash 5.12.

## The capture rig, already run (2026-10-05)

Before = `cf01b81` (the tip of `feat/transaction-fields`); after =
this branch. The three committed sample books, range 2026-01-01 to
2026-06-30: balance sheet, net worth and its monthly series,
spending and income (single and grouped), cash flow, the dashboard,
lots, outstanding documents, commodities, upcoming.

- **No flow report changed** on any book: spending, income and cash
  flow are identical before and after.
- **Sabine:** nothing changed but how lot quantities print.
- **Display only (C20):** `2 ETH` (was `2.0 ETH`), `9900.00 EUR`
  (was `9900 EUR`), `4100.00 EUR`, `6460.00 HKD`; lot quantities
  drop trailing zeros (`22 shares`, was `22.0000`).
- **Three figures moved by one cent**, all on Alex's net worth
  series: 348394.30 to .29, 383319.87 to .86, 410007.97 to .98. That
  is the per-account rounding: each account's converted balance is
  rounded once and the total is the sum of the lines.
- The budget report could not be captured (the sample budgets'
  periods do not include today, on either side). MM-12 is covered by
  its unit tests only.

## 1. Lines add up

`balance_sheet` on the multi-currency book.

- Pass: the lines of each section add up to its total, to the cent,
  and assets equal liabilities plus equity.
- Pass: assets minus liabilities equals `net_worth` for the same
  date and the dashboard's net worth.

## 2. Void and unvoid

- Void a transaction with a reconciled split. Pass: refused, naming
  the account and `force=true`. With `force`, it voids and warns.
- Void a transaction that has notes, then unvoid it. Pass: the notes
  are back.
- Void one with no notes, then unvoid it. Pass: the notes read
  "Voided transaction" afterwards. This is what GnuCash does; rule
  on whether it should stand.
- In GnuCash: void a transaction from the register, then
  `unvoid_transaction` through the server. Pass: desktop shows it
  restored and editable.

## 3. Hide an account

`update_account(name, hidden=true)`, then open the book in GnuCash.

- Pass: the account is hidden in the account tree (View > Filter By,
  "Show hidden accounts" off), and the Edit Account dialog shows
  Hidden ticked.
- Untick it in GnuCash, save. Pass: `get_account` no longer reports
  `hidden`.

## 4. Backups

Call `create_backup` twice with nothing in between.

- Pass: the second answers `status: unchanged` with the first file's
  path, and the folder holds one file.
- Enter a transaction, call it again. Pass: a second file.

## 5. Read-only period

Set File > Properties > Accounts > "Day Threshold for Read-Only
Transactions" to 30 in GnuCash. Through the server, post an invoice
dated 40 days ago and pay it the same day.

- Pass: both responses carry `read_only_period`, naming the date,
  and say GnuCash's own dialog does not check the option.
- In GnuCash, the posting shows in the register as read-only.

## 6. Num on split actions

In a scratch book, tick File > Properties > Accounts > "Use Split
Action Field for Number". Post and pay an invoice through the server.

- Pass: in the receivable register, the Num column shows the invoice
  ID on the posting line. (The engine twin already matches row for
  row; this is the window.)

## 7. An interrupted write

Start a large `create_transactions` batch and `pkill` the server
while it runs. Restart and make any write.

- Pass: the audit log has an `INTERRUPTED` line naming the tool and
  its start time, or the batch's own entry if it finished first.

## 8. A book the server cannot read

Point the server at a SQLite file that is not a GnuCash book.

- Pass: startup refuses, saying it is not a GnuCash book. No
  "Unsupported table versions".

## 9. MariaDB, the desktop-created book

On the `gnucash` database (saved from desktop), enter a transaction
whose description has an emoji.

- Pass: refused, naming the character and the table. Nothing is
  written, and no `?` appears in the register.

## 10. Docker

`docker build` the image and start it.

- Pass: `id` inside it is not root; a write to a demo book works and
  leaves an audit log.

## 11. A trading-accounts book

In a scratch copy, tick File > Properties > Accounts > "Use Trading
Accounts" in GnuCash. Through the server, enter a USD-to-EUR
transfer, and a same-currency expense.

- Pass: the transfer is refused, naming the option and GnuCash
  desktop as the way to enter it; the expense goes through.

## 12. An old server's write

On a book 1.5 has converted, run a 1.4.4 server and create a
scheduled transaction with it. Back on 1.5, make any business or
schedule write.

- Pass: the response carries `old_server_write`, and the dashboard's
  Warnings section says an older server wrote to the book and that
  its budget signs need review.
