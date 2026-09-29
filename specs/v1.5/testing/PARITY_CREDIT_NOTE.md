# Parity twin — customer credit note (desktop vs server)

Two byte-identical books from one converted base (`parity-base`,
sha256 `cbe89477…`), each performing the same credit-note flow; then
a row-by-row diff of what landed. Desktop is the oracle; the server
must match it. Nobody needs to understand credit notes to run this —
only which menu.

Inputs, identical on both sides:

| | |
|---|---|
| customer | Emerald Analytics |
| open invoice to apply against | 000018, USD 3,500, posted 2026-06-01, one line to `Income:LLC Revenue` |
| credit note | one line, description `Retainer adjustment`, quantity 1, price 500.00, account `Income:LLC Revenue` |
| dates | opened, posted, and applied 2026-09-29; no billing term |
| post to | `Assets:Accounts Receivable` |
| ID | whatever the book's counter gives (both books start on the same counter) |

## Desktop side — `~/Projects/abe-bench/parity-desktop.gnucash`

1. GnuCash: File → Open, pick `parity-desktop.gnucash`. **Cancel**
   Since Last Run; **Close** the two reminders.
2. **New credit note.** Business → Customer → New Invoice…. In the
   dialog, set *Type* to **Credit Note** (the radio at the top).
   Date Opened `09/29/2026`. Customer: click Select…, find
   `Emerald Analytics`, OK. Leave Job and Billing Terms empty. OK.
3. **One line.** In the credit-note window's entry grid, on the
   first row: Date `09/29/2026`, Description `Retainer adjustment`,
   Action left blank, Income Account `Income:LLC Revenue`, Quantity
   `1`, Unit Price `500.00`. Press Enter so the line commits.
4. **Post it.** Toolbar Post (or Business → Customer → Post
   Invoice). Post Date `09/29/2026`, Due Date `09/29/2026`, Post To
   Account `Assets:Accounts Receivable`, Description left as
   offered, Accumulate Splits as offered. OK. Note the credit note's
   ID from the window title (`Credit Note 0000NN`); write it down.
5. **Apply it to the invoice.** Business → Customer → Process
   Payment…. Customer: Select… `Emerald Analytics`. Date
   `09/29/2026`. In the Documents list, tick **both** invoice
   `000018` and the credit note you just posted. Leave *Amount* at
   whatever the dialog computes after ticking (it should read
   0.00: the 500 credit offsets 500 of the invoice, and no money
   moves). Post To `Assets:Accounts Receivable`; Transfer Account
   any bank account (it is unused at 0.00). Memo `apply credit
   note`. OK.
6. File → Save. Quit GnuCash. Tell me the credit note's ID.

If any dialog offers something the path doesn't mention, accept the
default and note it.

## Server side — `~/Projects/abe-bench/parity-server.gnucash`

Run by Claude with the same inputs, in this order:
`create_document` (credit note, Emerald Analytics, opened
2026-09-29, one entry as above), `post_document` (2026-09-29, A/R,
no due date given → due on posting), `apply_credit_note` against
000018 dated 2026-09-29.

## The diff

Row by row on both books, for the credit note's invoice row, its
entry, its posting transaction and splits, its lot, the application
payment transaction and splits, and every slot on all of them:
column for column, with GUIDs, ids, and timestamps normalized.
Anything left after normalization is a finding.

## Results — 2026-09-29

Desktop side driven by the maintainer (one adjustment to the path:
the Process Payment dialog's Post To defaulted to the EUR
receivable, and the Amount read 3,000 after ticking both documents,
so desktop wrote the lot link AND a 3,000 payment from Checking; the
server side mirrored both). Diff by `parity_dump.py` (role-mapped
GUIDs, clock timestamps dropped): 29 desktop lines vs 32 server.

| # | difference | desktop | server | weight |
|---|---|---|---|---|
| 1 | credit-note entry `quantity_num` | −1 (`gncEntrySetDocQuantity` negates for a credit note; reads negate back) | +1 | **shipping**: desktop shows the server's credit note as −1 × 500 = −500, a charge; a desktop credit note reads negative on the server |
| 2 | how the credit note is applied | one `L` transaction, action "Lot Link", memo "Offset between documents: Credit Note Parity01 - Invoice 000018", description = customer | one `P` transaction, action "Payment", memos "Credit from Parity01" / "Net against 000018", description "Credit applied: …", plus a `date-posted` slot | open: reverse gate pending |
| 3 | `invoices.date_opened` / `date_posted` time of day | 10:59:00 UTC (neutral) | local midnight (07:00 UTC here) | low: same date here, fragile across zones |
| 4 | entry defaults | `i_disc_type` PERCENT, `i_disc_how` PRETAX, `b_taxable` 1, `b_paytype` 1, `billto_type` NULL | "", "", 0, 0, 0 | low |
| 5 | lot `is_closed` after settlement | −1 (recompute) | 1 | none: both legal |
| 6 | 3,000 payment A/R split memo | "Apply credit note" on both splits | on the bank split only | cosmetic |
| 7 | empty `notes` slot on the new lot | absent | present ('') | cosmetic |

Matched exactly: the credit note's invoice row otherwise; its posting
transaction (num, description, currency, both splits' accounts,
actions "Credit Note", signs and denominators); the lot title; every
slot on the posting transaction (`gncInvoice` frame, `date-posted`,
`trans-date-due` timespec, `trans-read-only`, `trans-txn-type` I);
the `credit-note` int64 flag; the 3,000 payment transaction's
type, splits, and lot.

### Reverse gate — desktop reading the server's twin (Claude at the screen)

`parity-server.gnucash` opened in GnuCash desktop: clean, reminders
fire, totals identical to the desktop twin ($450,762.93 net).

- **Finding 1 demonstrated.** Find Invoice → Parity01 lists as a
  Credit Note, Paid. Opened, the entry reads Quantity **−1.00**,
  Unit Price 500.00, and the status bar **Total: −$500.00**. Desktop
  negates a credit note's stored quantity on display
  (`gncEntryGetDocQuantity`); the server stored +1, so the credit
  note shows as a charge. Every server-made credit note in every
  book does this.
- **Finding 2 tolerated.** Process Payment for Emerald Analytics
  with Post To `Assets:Accounts Receivable` lists only 000045 and
  000019 — the two genuinely open invoices. 000018 is absent
  (desktop reads the server's 3,000 payment plus the 500 application
  as settling it) and Parity01 is absent (fully applied). The `P`
  shape is not what desktop writes, but desktop reads it correctly.
  Parity for the write shape (a `TXN_TYPE_LINK` "Lot Link"
  transaction) is a want, not a shipping stopper; no migration of
  existing applications is needed.
- The Post To default landed on the EUR receivable again, for the
  same reason it did for the maintainer: desktop's pick on a
  multi-currency book, not the server's doing.

**Verdict:** one shipping stopper (entry quantity sign, both
directions), one write-shape difference to bring to parity when
convenient, three low-weight column conventions.

### Outcome — 2026-09-29, `test/slot-shapes`

Ruling: desktop parity means shape parity in the data file, not
tolerance. Every line in the table is fixed on the server's write
side, with a converter under `_upgrade_book_shapes`
(`_migrate_business_shapes`) for rows already on disk, and the twin
re-run until the dump matched:

    diff desktop.txt server2.txt → empty (25 lines each)

The dump orders transactions by type rather than entry time (the
two sides did the same two operations in opposite order) and maps
the invoice's own posting GUIDs to roles; neither is a storage
difference. The desktop dump is now a fixture
(`tests/fixtures/parity_credit_note_desktop.txt`) and
`tests/test_parity_credit_note.py` reproduces the server side from
the frozen sample and expects it verbatim, with the one
machine-dependent line (the entry date at the desktop's local noon)
rendered for the zone the test runs in.
