# Bookkeeper live loop — price and payment parity (test/slot-shapes)

Branch under test: `test/slot-shapes` at `4d8a494`, 25 commits on
develop at `9db02c7` (#197). Evidence and rulings:
`specs/v1.5/testing/PARITY_CREDIT_NOTE.md` (every twin, in order) and
the rulings in `BOOKKEEPER_REPORT_DASHBOARD_SENSITIVITY.md`
(2026-09-29 evening; 2026-09-30 00:30, amended 00:40). **The branch
changes storage shape in several places (prices, slot filler
columns, the payment transaction's currency), so the desktop gate
(Part C) is required.**

What it claims, since #197:

- **Which price is current is GnuCash's answer.** The latest stored
  timestamp wins, then the smaller GUID; a pair's direct and inverse
  rows are one list. The source-rank tie-break is gone.
- **A transaction's implied rate is a price.** `type='transaction'`
  rows value holdings, chain, and answer `get_latest_price`. One
  place still uses entered or fetched quotes only: the rate the
  server picks for a NEW cross-currency posting or payment.
- **Stale-price warnings key on the rate valuation used**, one
  window for all sources. An old implied rate reads "valued at the
  rate of its last transaction, N days ago"; a fresh one is silent;
  "no price on file" means no price row of any kind.
- **Price rows are the Price Editor's**: a source GnuCash recognizes
  (anything else is refused), the neutral time of day, a reduced
  value, type `last` by default.
- **A cross-currency transaction leaves desktop's price**: exact
  ratio against the default currency and `user:xfer-dialog` for a
  currency account; six-decimal half-up and `user:split-register`
  for a stock or fund account. A preferred same-day price stays;
  otherwise the day's row is updated in place. `Buy`/`Sell` is
  stamped only on stock and fund splits.
- **A cross-currency payment is a transaction in the pay account's
  currency.** Realized FX is a real balanced amount; with no rate
  change the rows are desktop's. Existing payments are untouched.
- **A document's balance is read in the document's currency**, so a
  payment desktop processed across currencies no longer reads as an
  overpayment.
- Slot rows carry desktop's unused-column values; a new account
  carries the empty `balance-limit` frame; a posted document's
  receivable split has the true epoch `reconcile_date`. Converters
  bring old rows along on the first converting write.

## Setup

- Check out `test/slot-shapes` in the server's checkout and bounce
  the server (`pkill` the server processes; Claude Desktop reloads
  them). There is no new tool and no new parameter.
  `get_server_config` should answer.
- **Writes go to a copy.** `cp ~/Projects/abe-bench/parity-server.gnucash
  ~/Projects/abe-bench/parity-loop.gnucash`, point the server at
  the copy, and confirm the `Book:` line before any write. That
  book is Alex plus the twins' desktop entries: EUR customer
  `Berlin Digital GmbH` (000003), invoice `000048` posted and
  unpaid, invoice `000049` posted and paid IN DESKTOP, receivable
  `Assets:Receivables:Accounts Receivable EUR`,
  `Assets:EUR Savings`.
- GnuCash desktop must be closed while the server writes.
- Write down today's date; several expectations are day counts.

## Part A — what the maintainer's session already established

Twins (desktop vs server, same action, rows diffed): credit note,
plain transaction, reconcile, billterm, price editor, cross-currency
transfer (rate typed, amount typed), stock purchase (both
registers), cross-currency invoice post, cross-currency payment,
and desktop's Since Last Run over 33 server-written schedules. Every
difference found is fixed on this branch except one by ruling: the
`USD/EUR … temporary` price row desktop leaks on a cross-currency
post is not written.

Capture rig, develop (`9db02c7`) vs this branch, committed sample
books, reads only. Every differing line:

- **Sabine**: `IWDA.AS` is valued by a purchase's implied rate of
  2026-07-06 (`90.722834`) where develop used the quote of
  2026-07-01 (`90.7226`): assets `EUR 628917.58` from `628917.50`;
  the warning reads `valued at the rate of its last transaction,
  86 days ago` from `last updated 91 days ago`; balance sheet as of
  2026-06-30 `627931.51` from `627931.63`; two trajectory points
  move by one euro.
- **All three**: `list_commodities` now shows a latest price on the
  book's default currency (`CURRENCY:USD US Dollar 1.415228 CAD
  (2026-07-12)`), from an implied row stored the old way round.
  Flagged for a ruling in B10.
- Alex and Lin Wei: no number moved.

## Part B — on the copy

Record for each step what came back, not only pass or fail.

**B1. Dashboard.** `get_book_summary`. Every `Stale price:` line
is one of the three forms above. A holding bought through a
cross-currency transaction does not read "no price on file".

**B2. The payment desktop made.** `get_document` for `000049`.
Expect `status: paid`, `amount_paid 900.00`, `amount_due 0.00`,
one `payments` row of `900.00` from Checking. (Develop reads this
document as overpaid by 100.)

**B3.** `get_outstanding_documents`. `000048` is listed at EUR
900.00; `000049` is not.

**B4. The posting-rate carve-out.** `pay_document` for `000048`
from `Assets:Current Assets:Checking Account`, amount `900`, today,
`dry_run`. Expect a refusal or a stale-rate block naming the last
ENTERED EUR quote: the recent implied rates must not count here.

**B5. Pay across a rate change.** `create_price` EUR at `1.18`
USD dated today. Dry-run the same payment. Expect:

| Proposed split | value | quantity |
|---|---|---|
| Accounts Receivable EUR | -1000.00 | -900.00 |
| Checking Account | 1062.00 | 1062.00 |
| Foreign Exchange Gain/Loss | -62.00 | -62.00 |

and `fx_realized` 62.00 USD, gain. Then run it for real.
`get_document 000048`: paid, `amount_paid 900.00`, due `0.00`.
`get_transaction` on the payment: currency USD, three splits,
values summing to zero.

**B6.** `get_prices` for EUR. Today has ONE row: the `user:price`
quote at 1.18. The payment added nothing (a preferred same-day
source stays).

**B7. Partial payments at two rates.** Create a EUR 500 invoice for
Berlin Digital, post it to the EUR receivable dated today. Pay
`200` today. `create_price` EUR `1.25` dated tomorrow is not
possible, so instead: `get_document` shows `amount_due 300.00`;
pay `300`; `amount_due 0.00`, two `payments` rows of 200.00 and
300.00. Try to pay `1` more: refused, in EUR.

**B8. Price writes.** `create_price` AAPL `178.70` with no type:
the response says `last`. `create_price` with
`source="user:yahoo"`: refused, naming the valid strings.

**B9. Transfers.** `create_transactions`: USD 70 from Checking to
`Assets:EUR Savings`, quantity 60, dated a day with no EUR price
(try 2026-09-15). `get_prices` EUR for that day: one row,
`user:xfer-dialog`, `transaction`, 1.1667 (7/6). A second transfer
the same day, USD 100 for EUR 80: still one row, now 1.25.

**B10. A ruling wanted.** `list_commodities`: the default currency
row carries a "latest price" in a foreign currency. Noise, or
useful? Say which; the maintainer's session left it as found.

**B11.** `balance_sheet`: every `@ rate` has at most six decimals.

**B12. Friction.** Anything you routed around, at the moment it
happened.

## Part C — the desktop gate (the maintainer, at the screen)

After Part B, on `parity-loop.gnucash`. Launch from Terminal:

```bash
/Applications/Gnucash.app/Contents/MacOS/Gnucash --debug --logto stderr 2>&1 | tee ~/gnucash-run.log
```

- **C1.** The book opens with no error dialog.
- **C2.** Business → Customer → Find Invoice…, search ID `000048`.
  It shows as paid (Paid? column ticked). Open it; nothing prompts.
- **C3.** Open `Assets:Receivables:Accounts Receivable EUR`. The
  payment line for 000048 shows a 900.00 decrease. With the cursor
  on it, View → Transaction Journal: three lines — Checking
  1,062.00, Foreign Exchange Gain/Loss 62.00, the receivable — and
  no Imbalance line.
- **C4.** In that payment, type a word in the Notes or Memo field,
  press Enter. It saves without a rebalance prompt. Undo the edit
  the same way.
- **C5.** Business → Customer → Process Payment…, customer
  `Berlin Digital GmbH`, Post To the EUR receivable. Neither
  `000048` nor `000049` is in the document list.
- **C6.** Reports → Business → Customer Report, customer Berlin
  Digital. The balance for the two invoices is zero.
- **C7.** Tools → Price Database. The EUR rows for today and for the
  B9 day read as the plan says; no source says Invalid.
- **C8.** Save if enabled, quit. No prompt on the way out.

Record each step's result in the report as it lands.

## Routed around

(The bookkeeper fills this in.)
