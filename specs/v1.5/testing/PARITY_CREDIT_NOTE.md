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

## Plain-transaction twin — 2026-09-29 afternoon

One transaction each side ("Twin probe", 100 from Checking Account
to Bank Charges; the maintainer's landed in `parity-server`, the
book GnuCash had reopened). Every column and slot matched — `num`,
the neutral post time, empty memo and action, state `n`,
denominators, the `date-posted` slot, no split slots — except
`splits.reconcile_date`: desktop `1970-01-01 00:00:00` (time64 0),
server NULL. Fixed at `_new_split` with a converter
(`split_reconcile_dates_filled`). Open candidate from the same
look: the time of day the server stores on a *reconciled* split
(local midnight) against desktop's, not yet sampled.

## Reconcile twin — 2026-09-29 afternoon

The maintainer reconciled Bank Charges in desktop. Desktop's
`reconcile_date` on the reconciled splits: `2026-09-30 06:59:59`
UTC = the statement date's local day end (`gnc_time64_get_day_end`,
the same convention as `reconcile-info/last-date`); the server
wrote local midnight. Desktop's Finish also wrote
`reconcile-info/include-children` 0, which the server's frame
lacked. Both fixed on the write side with a converter
(`reconcile_dates_normalized`, `reconcile_frames_completed`).

## Billterm twin — 2026-09-29 afternoon

The maintainer created (not posted) an invoice for Emerald Analytics
with `Net 30`, letting desktop pick the ID. Desktop chose `000047`,
continuing the six-digit counter past the hand-named `Parity01`;
the server's `max(counter, highest numeric id) + 1` gives the same.
No child copy of the billterm: the invoice's `terms` points at the
parent row, as the server's does. Two differences, both fixed with
converters (`billterm_refcounts_recomputed`,
`credit_note_flags_completed`): desktop maintains `refcount` (55 =
every referencing document; the base held 0), and it writes
`credit-note` 0 on a plain document where the server wrote nothing.
Every other column matched.

## Price twin — 2026-09-29 evening

The maintainer added one price in desktop's Price Editor (AAPL, 200,
today) and reported the finding himself: every other price in the
book listed its source as **Invalid**. The generators wrote
`user:market_data`, a string GnuCash has never had; the editor
shows anything outside `gnc-pricedb.h`'s list that way. The desktop
row differed from the server's in two columns: `source`
(`user:price-editor`) and `date` (`10:59:00` UTC, the neutral time;
the server stored local midnight, `07:00:00` UTC here). Value
`200/1` matched; type `last` matched.

Fixed with a validating writer and a converter
(`price_sources_normalized`, `price_dates_normalized`): the writer
refuses a source string desktop would show as Invalid, maps the two
market-data strings to `Finance::Quote`, and stamps the date at the
neutral time by raw SQL, since piecash's `Price.date` column can
only bind a date at local midnight. The same column is why piecash's
save-time `Price.validate` re-queries the row at midnight and raises
`NoResultFound` on any desktop-written price the server touches;
`book/investments.py` replaces it with a by-day comparison. Every
price write runs the converter.

Second round, same evening: the maintainer added AAPL at 178.70 dated
the day before. Desktop stored `1787/10` — `gnc_numeric_reduce` —
where piecash keeps the typed denominator (`17870/100`; the
generators' `1787000/10000`). The writer now reduces by raw SQL in
the same UPDATE as the date stamp, and the converter reduces
existing rows (`price_values_reduced`). The converter's date rule
was tightened at the same time: only a row at the server's
local-midnight shape moves; a third row desktop left in the book at
`20:44:14` UTC (value 0/1, `user:price-editor`, a wall-clock stamp
the editor's own Add produced) stays as desktop wrote it. The
editor's Type default is `last`, offered alongside Bid, Ask,
Unknown, and Net Asset Value; the server's default is `nav`, an open
question for the maintainer. The real-driver gate
(`_RealDatabaseTests::test_price_row_lands_in_editor_shape`) writes
one price on PostgreSQL and MariaDB, where `prices.date` is a true
timestamp rather than SQLite's text.

### Reading parity — the current price

With the zero row in the book, desktop's Accounts tab valued the
31 AAPL shares at 0.00; the server's `get_latest_price` said 200.
GnuCash's `compare_prices_by_date` (gnc-pricedb.cpp, stable) orders
a pair's prices by the full stored time, then by `guid_compare`
ascending, and `gnc_pricedb_lookup_latest` takes the head of that
list; the zero row's 20:44:14 stamp beats the 200's 10:59:00. The
server ordered by calendar day and broke same-day ties by source
rank (bookkeeper F3). Maintainer ruling: "parity means agreeing on
the price." `_price_tie_rank` is now GnuCash's order; `_find_prices`
carries the raw stored time on each row it returns (piecash's
column type strips it), and the outranker note names the row
desktop will use. The zero row itself came from the editor's Add
path: `dialog-price-editor.c` clones the selected price with source
`user:price-editor`, time `gnc_time(NULL)` and value zero before
the dialog opens, which is the only path in the editor that stamps
a wall-clock time; a saved price goes through the date widget and
lands at the neutral time.

Second ruling, same evening: "The server should NOT skip
`type='transaction'` rows because desktop doesn't. The ruling made
before is overturned." `_find_prices` now returns every row;
valuation, posting FX, chains, `get_latest_price`,
`calculate_lot_gain` all count a transaction's implied rate. A
first cut exempted transaction rows from the stale-price warning;
the test migration showed that hiding a sixty-day-old direct rate
behind a fresh chain of quotes, and the bookkeeper ruled it out
(`BOOKKEEPER_REPORT_DASHBOARD_SENSITIVITY.md`, 2026-09-29 evening):
staleness keys on the date of the rate valuation actually used,
one window for all sources, provenance named — "valued at the rate
of its last transaction, 45 days ago". 33 tests migrated by
subject; the ones about
the unpriced state delete the transaction rows
(`drop_transaction_prices`), since a book with a holding and no
price row at all is a state desktop's Price Editor can produce.

The zero-value AAPL row was deleted from the server twin after its
origin was traced; with it gone both sides value AAPL at 200.

Open from the migration: piecash rounds the implied rate to six
decimals when it writes the transaction row; desktop stores the
exact ratio of the split's value to its amount. A cross-currency
transaction entered in desktop is the next twin.

## Cross-currency twin — 2026-09-30

The maintainer created `Assets:EUR Savings` (EUR) in the server twin
and transferred USD 100 into it from Checking, typing 0.90 as the
rate. The same two actions were run through the server on a copy.

| Row | Desktop | Server (before) |
|---|---|---|
| Price | EUR/USD `10/9`, 10:59:00 UTC, `user:price`, `transaction` | none that day once desktop's existed; on a fresh day: the split's own direction, six decimals half-even, local midnight, `user:split-register` |
| EUR split `action` | `''` | `'Buy'` (piecash stamps Buy/Sell on any cross-commodity split) |
| Account slots | `balance-limit`, an empty frame | none |
| Every slot row | `double_val` NULL, `timespec_val` 1970-01-01 00:00:00 | `double_val` 0.0, `timespec_val` NULL |

The last row is a class, not an instance: 1,915 of the twin's
`date-posted` slots carried piecash's filler columns against two
desktop-written ones. The earlier twins missed it because
`parity_dump.py` printed only a slot's typed column; it now prints
the unused ones too, and the credit-note fixture was regenerated
from the desktop twin (it reproduces byte for byte from
`parity-desktop.gnucash`).

Fixes, in `book/_piecash_shapes.py` (imported unconditionally):

- `Split.validate` replaced. The implied price is written by ports
  of GnuCash's own writers — `record_price` (Transaction.cpp; the
  register calls it as `xaccTransRecordPrice(trans,
  PRICE_SOURCE_SPLIT_REG)`) for accounts `xaccAccountIsPriced`
  accepts, and the exchange dialog's `create_price` / `new_price` /
  `update_price` (dialog-transfer.cpp) for the rest, on its
  to-amount path (`user:xfer-dialog`; the maintainer typed the rate
  instead, which is the dialog's `user:price` path and the only
  column that differs from the specimen). No action stamped.
- Slot column defaults set to GnuCash's; `_migrate_slot_fillers`
  converts existing rows; `create_account` writes the
  `balance-limit` frame (slot registry entry added).
- `_migrate_price_shapes` no longer reduces `type='transaction'`
  rows (`record_price` keeps a fixed denominator) and restates a
  piecash-written currency row as the dialog's when its split can
  be identified, in the same pass that moves it off local midnight.

Reading side: `_rates_as_of_dated` and `_find_exchange_rate_aged`
now treat a pair's direct and inverse rows as one list
(`pricedb_get_prices_internal` merges them). Desktop's direction
rule made this visible: an implied USD/EUR row was shadowing a newer
EUR/USD quote.

One carve-out: the rate the server CHOOSES for a new posting or
payment is looked up over quotes only. Counting implied rates there
lets each posting refresh the echo of the last one, and the FX
staleness guard never fires again. Ruled by the maintainer,
2026-09-30: "Carve out it is. Stale prices are our own invention in
this case."

Not yet probed: a stock purchase in desktop's register (confirms the
`record_price` port's denominator and source), a cross-currency
invoice post (`user:invoice-post`), and the dialog's to-amount path.

### Desktop gate and second round — 2026-09-30

The converters ran on the server twin (`slot_fillers_normalized`
2,570) and the server added `Assets:EUR Savings Server` and a
USD 50 → EUR 45 transfer dated 2026-09-28. Desktop then opened that
file, the maintainer entered two transactions in it (a purchase of
2 AAPL for 800.00 and a USD 50 → EUR 45 transfer typed as a
to-amount), saved, and quit: the converted book opens, takes
writes, and saves, and desktop left every converted slot row as the
converter wrote it (0 rows back in piecash's shape). The maintainer
repeated both entries in `parity-server.pre-xccy.gnucash`, the
unconverted backup, which is where "EUR Savings Server" was missing.

Both entries were dated 2026-09-29, a day that already held a
preferred price for each pair (`user:price-editor` for AAPL,
`user:price` for EUR). Desktop added no price row and changed none,
and its split rows are byte-identical to the server's for the same
two entries on the same day: empty action on the stock split too,
AAPL quantity `20000/10000`, value `80000/100`. That confirms the
same-day rule in both ports and the absence of a Buy stamp. It does
not confirm the shape of a NEW row, which the server writes as
`AAPL/USD 400000000/1000000 user:split-register` and
`EUR/USD 10/9 user:xfer-dialog`; that needs the same two entries on
a day with no price for the pair.

### Third round — 2026-09-30, the stock register

The maintainer entered `AAPL 3` (3 shares for 1,000.00) in the AAPL
register and `Euro test 3` (USD 70 → EUR 60 as a to-amount) in the
Checking register. Both were saved dated 2026-09-29 again — the
register's default date — so the day's preferred prices stood and
the new-row shape is still unprobed.

The AAPL register entry did show something the bank-side entry had
not: its AAPL split came back with action `Buy`. The earlier
purchase, entered from the Checking register, had an empty action.
`gnc_split_register_check_stock_shares`
(split-register-control.cpp) sets an empty Action to Buy or Sell
when a share count is entered in a register that has a Shares
column (stock, portfolio, currency registers); a bank register has
no such column. piecash stamped every cross-commodity split; the
first fix stamped none. The server now stamps a split in a priced
account (STOCK, MUTUAL, CURRENCY type) and nothing else, on entry
or amount change only.

The maintainer then re-dated both entries to 2026-09-20 in place.

- **Stock side confirmed.** Committing the re-dated `AAPL 3` ran
  `xaccTransRecordPrice` for the new day and desktop wrote
  `AAPL/USD | 2026-09-20 10:59:00 | user:split-register |
  transaction | 333333333/1000000` — the row
  `test_stock_purchase_is_record_price` asserts for the same
  purchase, denominator unreduced.
- **Currency side: no row.** Re-dating `Euro test 3` wrote no price
  for 2026-09-20. The register's `record_price` skips an account
  that is not priced, and a date edit does not reopen the exchange
  dialog, which is the only writer for a currency account. That is
  the port's behavior too (a split whose amounts did not change
  records nothing), but the dialog's to-amount row for a NEW
  transfer on a free day is still without a desktop specimen.
- Desktop also saved a blank transaction on 2026-09-20: empty
  description, one zero-value Checking split. A stray register row,
  not part of the probe.

**Currency side confirmed.** A new transfer entered with the date set
first (`Euro test 4`, 2026-09-21, USD 70 → EUR 60 typed as a
to-amount) made desktop write `EUR/USD | 2026-09-21 10:59:00 |
user:xfer-dialog | transaction | 7/6` — the dialog port's row,
source string included
(`test_to_amount_transfer_matches_the_second_desktop_specimen`).
Both ports now have a desktop specimen for a new row and for the
same-day rule. The blank transaction was deleted in desktop.

## Cross-currency invoice twin — click path

Book: `~/Projects/abe-bench/parity-server.gnucash`. Customer
`Berlin Digital GmbH` (000003) is a EUR customer in a USD book; its
receivable is `Assets:Receivables:Accounts Receivable EUR`.

1. Business → Customer → New Invoice…
   - Customer: `Berlin Digital GmbH`. Leave Invoice ID blank (desktop
     picks it; expect `000048`). Date Opened: 09/22/2026. OK.
2. In the invoice window, one entry line:
   - Date 09/22/2026, Description `Twin invoice`, Income Account
     `Income:LLC Revenue`, Quantity `1`, Unit Price `900`.
   - Tab off the line so it is saved.
3. Click Post (toolbar).
   - Post Date 09/22/2026, Due Date as offered, Post To
     `Assets:Receivables:Accounts Receivable EUR`, leave
     "Accumulate Splits" as it is. OK.
   - When the exchange-rate dialog appears (the income account is
     USD, the invoice EUR), choose **To Amount** and type `1000.00`
     (so EUR 900 = USD 1000, rate 10/9). OK.
4. File → Save if enabled, then quit.

Report back: the invoice ID desktop chose, and anything the dialogs
asked that is not listed here.

### Result — 2026-09-30

Desktop chose `000048`, as predicted, and raised the exchange dialog;
the maintainer typed USD 1,000.00 as the to-amount. The same invoice
was run through the server on a copy (a day later, behind an entered
EUR/USD quote, since the server picks its posting rate from quotes).

Every column of the invoice row, the entry row, the posting
transaction, both splits, the transaction's six slots, the lot and
its slots, and the invoice's `credit-note` slot matched — filler
columns included — except one: the receivable split's
`reconcile_date`, `1970-01-01 00:00:00` in desktop and
`1970-01-01 08:00:00` from the server. The posting path passed a
naive `datetime(1970, 1, 1)`, which piecash localized; every other
split went through `_new_split`'s tz-aware epoch. 55 such rows in
the twin, one per posted document. Fixed at the writer; the
reconcile-date converter now also moves a near-epoch date on an
unreconciled split to the epoch. `parity_dump.py` had not printed
`reconcile_date` at all; it does now, and the credit-note fixture
was regenerated from the desktop twin.

Prices: desktop wrote `EUR/USD 10/9 user:xfer-dialog transaction`,
which is the server's row. It also left a second row,
`USD/EUR 9/10 temporary last`, at the same instant:
`gnc_price_invert` (gnc-pricedb.h: "The source is set to
PRICE_SOURCE_TEMP") builds a reversed copy for the posting code's
conversion, and the SQL backend commits any instance it is handed —
the same leak that left the zero-value AAPL row. The server does
not write it. Open question for the maintainer.

The Post dialog's Due Date stayed at the day the dialog opened
(09/29) when the Post Date was changed to 09/22 on a customer with
no terms; the server's default for that case is the posting date.
Both are the user's choice at post time.
