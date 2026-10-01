# GUI gate for `fix/v1.5-adversarial-blockers`

The manifest the bookkeeper ruled on 2026-09-30 (round 2, item 6), as
steps. Everything here was written by the server and checked against
GnuCash's engine headlessly; this gate is the part only GnuCash's own
window can answer.

**Book:** `~/Projects/abe-bench/fix-branch-gate.gnucash`, built by
`specs/v1.5/testing/build_fix_branch_gate_book.py` at `00ed9a7`.
GnuCash's engine loads it (checked on a copy). Work on the file
itself; the builder remakes it.

What the book holds:

| Document | State |
|---|---|
| Invoice 000001, Acme, 100.00 | paid with 120.00; 20.00 held as Acme's prepayment |
| Invoice 000002, Acme, 50.00 | posted, unpaid |
| Invoice 000003, Acme, 70.00 | paid 70.00, then UNPOSTED; the payment is kept |
| Invoice 000004, Acme, 200.00, Net 30 | posted by the server |
| Invoice 000005, Acme, 300.00, Net 30, tax table T5 | DRAFT, for posting in the GUI |
| Invoice 000006, Acme, EUR 100.00 | posted to Receivable EUR; Acme's currency is USD (forced) |
| Voucher 000001, Dana Reimbursee, 125.50 | posted; Hotel and Dinner are card lines |
| Price EUR/USD 1.12, 2026-06-01 | `Finance::Quote` |

## 0. The book opens

Open the file. Pass: no error dialog, no "unknown feature" refusal.
Cancel Since Last Run if it appears.

## 1. Prepayment lot held

Business > Customer > Process Payment, customer Acme Corp.

- Pass: the document list shows a pre-payment of 20.00 and one of
  70.00 beside invoices 000002 and 000004.
- Then select invoice 000002 and the 20.00 pre-payment, amount 0, OK.
  Pass: 000002 shows 30.00 still due (Find Invoice, or its window).

## 2. Unposted with its payment kept

Business > Customer > Find Invoice, 000003.

- Pass: it opens as an unposted, editable invoice.
- Post it (any date). Then Process Payment for Acme. Pass: the 70.00
  payment is offered and settles 000003 in full.

## 3. Posted billterm copy

Business > Billing Terms Editor.

- Pass: "Net 30" appears ONCE.
- Open invoice 000004. Pass: its terms read Net 30 and its due date
  is 2026-02-17.

## 4. Card voucher

Business > Employee > Find Expense Voucher, 000001.

- Pass: it opens; Hotel and Dinner show payment type Charge.
- Open the Liabilities:Company Card register. Pass: two lines, 60.00
  (Hotel) and 25.50 (Dinner). Accounts Payable shows 40.00 for Dana.

## 5. Forced off-currency invoice

- Find Invoice 000006. Pass or fail, record what the window shows as
  its currency, and whether saving it changes anything.
- Process Payment, Acme. Record whether Receivable EUR can be chosen
  as the post account and whether 000006 is listed.
- Reports > Business > Customer Report, Acme. Record whether 000006
  appears and in which currency the total is given.

This item records behavior; the refusal-by-default is already ruled.

## 6. Taxed invoice posted in the GUI (the open question)

Find Invoice 000005 (draft). Post it in the GUI, default options.
Close GnuCash, then:

```bash
sqlite3 ~/Projects/abe-bench/fix-branch-gate.gnucash "select name, invisible, parent is not null as is_copy, substr(guid,1,8) from taxtables; select i.id, e.description, substr(e.i_taxtable,1,8), (select parent is not null from taxtables t where t.guid = e.i_taxtable) as points_at_copy from entries e join invoices i on i.guid = e.invoice where e.i_taxtable is not null;"
```

- If `points_at_copy` is 1 for 000005's line: a GUI post DOES save
  the line's move to the copy. The server must then do the same at
  post, and the `unreferenced_taxtable_copy` allowlist entry goes.
- If it is 0: the GUI behaves as the headless engine did, and leaving
  lines on the live table stands.

## 7. Price Editor, same day

Tools > Price Database. EUR has one price on 2026-06-01, source
Finance::Quote, 1.12.

- Add a price for EUR on 2026-06-01, value 1.15 (the editor's source
  is `user:price-editor`). Close GnuCash, then:

```bash
sqlite3 ~/Projects/abe-bench/fix-branch-gate.gnucash "select date, source, value_num, value_denom from prices where date like '2026-06-01%';"
```

- Pass: ONE row, `user:price-editor`, 23/20. That is the
  rank-replacement the server now follows.

## After the gate

Run the server's own reads on the book GnuCash has now written to:

```bash
GNUCASH_BOOK_PATH=~/Projects/abe-bench/fix-branch-gate.gnucash uv run python -c "from gnucash_mcp.book import GnuCashBook; import os; gb = GnuCashBook(os.path.expanduser(os.environ['GNUCASH_BOOK_PATH'])); print(gb.get_outstanding_invoices()); print(gb.get_book_summary())"
```

Pass: no error, and the outstanding list agrees with what the
windows showed.
