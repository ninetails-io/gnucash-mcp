# Bookkeeper report — price and payment parity (test/slot-shapes)

Run 2026-09-30, midday (all day counts read from this date). Book:
`parity-loop.gnucash`, fresh copy of `parity-server.gnucash`
(10:57 snapshot, which holds desktop's payment of `000049` made this
morning). Server confirmed `Book: parity-loop.gnucash` before any
write. GnuCash desktop closed for all of Part B (verified: no
process). Bench rig: `class6_loop/mcpcall.py`, private stdio server
per invocation, `REPO` at the checkout.

**Head drift.** The plan binds `4d8a494`; the tested head is
`b5c98b6`, two commits later — `c391cca` (the plan itself) and
`b5c98b6` `fix(currency): a temporary price row is not a quote`.
27 commits on develop `9db02c7`, not the plan's 25. B4's pass
belongs to the head as tested: the drift commit is exactly the
temporary-rows-must-not-satisfy-the-guard rule B4 exercises.

## Part A

The maintainer's twin evidence (`PARITY_CREDIT_NOTE.md`) and the
capture-rig diffs are accepted as read. The Sabine shifts are the
designed consequence of implied rates becoming prices (an 86-day
implied rate now beats a 91-day quote); the `list_commodities`
default-currency line was flagged for B10 and is ruled there.

## Part B — verdict: 11/11

**B1 PASS.** Dashboard stale line, verbatim:

    ⚠ Stale prices: 5 commodities, oldest 75 days (CAD, MSFT, VBTLX, +2 more) — get_prices / create_prices to refresh

The five are CAD/MSFT/VBTLX/VTSAX/ETH (74–75 days). ALTX — 394 days
stale, and its latest row is `transaction`/`user:split-register` —
is correctly excluded: not held, per the held/used rollup ruling.
EUR does not appear: it is fresh through the implied
`user:xfer-dialog` row desktop's payment left today, and
`Assets:EUR Savings` values at `315 EUR @ 1.111111 (USD 350.00)` —
no holding anywhere reads "no price on file".

**B2 PASS.** `get_document 000049`: `status: paid`,
`amount_paid 900.00`, `amount_due 0.00`, one payments row
`{"guid":"2faebf52","date":"2026-09-30","amount":"900.00","from":"Assets:Current Assets:Checking Account"}`.
Develop read this document overpaid by 100; this branch reads the
balance in the document's currency and gets it right.

**B3 PASS.** `000048` listed at `EUR 900` ("1 days past due" — see
friction 5); `000049` absent.

**B4 PASS — the posting-rate carve-out holds.** Dry-run pay of
`000048` before any quote entry, verbatim refusal:

    EUR/USD rate is 75 days from the payment date (2026-09-30); last
    quoted 2026-07-17 at 1.1435. This rate is locked at payment time
    and cannot be updated retroactively. Either run
    create_price(commodity='EUR', value='...') to add a rate near
    2026-09-30, or pass force=true to proceed with the stale rate.

`error_type: stale_fx_rate` with structured `fx_detail`. The book
held a same-day implied rate (desktop's xfer-dialog row, today), the
twins' older implied rows, and two leaked `temporary` rows — none
satisfied the guard; it named the last ENTERED quote only. Exactly
the carve-out, and exactly what the drift commit `b5c98b6` claims.
One flaw: the error's own cure does not validate — see friction 1.

**B5 PASS — the amendment's payment shape, to the penny.**
`create_price` EUR 1.18 today, then dry-run:

| Proposed split | value | quantity |
|---|---|---|
| Accounts Receivable EUR | -1000.00 | -900 |
| Checking Account | 1062.00 | 1062.00 |
| Foreign Exchange Gain/Loss | -62.00 | -62.00 |

`fx_realized` 62.00 USD, gain — the plan's table exactly, plus a
memo naming both rates: `FX gain on invoice 000048: post-rate
1.1111, pay-rate 1.18`. Run for real (txn `1fdf75ba`): identical to
the proposal. `get_document`: paid, 900.00/0.00. `get_transaction`:
**currency USD** (the payment account's), three splits, values
1062 − 1000 − 62 = 0, receivable split lot-linked (`e21d7563`), all
actions `Payment`. Realized FX is a real balanced amount, not a
zero-value marker. This is the 00:40 amendment shipping.

Also noted: `create_price`'s response volunteered that the same-day
xfer-dialog row outranks the new quote as the EFFECTIVE (valuation)
rate by desktop's order, while the payment still took the entered
quote — the two rate-pickers split exactly as designed, and the
response says so unprompted.

**B6 PASS.** `get_prices` EUR, today: exactly two rows —

    2026-09-30  1.18  USD  transaction  user:xfer-dialog
    2026-09-30  1.18  USD  last         user:price

The payment added no third row; the dialog row was updated in place
from 1.1111 to 1.18 (the in-place arm of the same-day rule — the
plan allowed either outcome of the GUID draw).

**B7 PASS.** Invoice `000050` (Berlin Digital, EUR 500, posted today
to the EUR receivable). Pay 200: `status: partial`,
`amount_due 300.00`. Pay 300: paid, `amount_due 0.00`, two payments
rows (200.00, 300.00), converted at 1.18 (236.00 / 354.00 USD). Pay
1 more, verbatim refusal:

    Payment of 1 EUR exceeds the outstanding balance of 0 EUR on
    Invoice 000050. Pay at most the outstanding balance. To record a
    genuine overpayment, pay the outstanding balance and book the
    excess as a credit note (create_document with
    document_type='credit_note') so it shows as credit owed to the
    counterparty rather than a phantom receivable.

Balance stated in EUR, and the refusal teaches the honest route.

**B8 PASS.** `create_price` AAPL 178.70, no type → response says
`"type":"last"`. `source="user:yahoo"`, verbatim refusal:

    Price source 'user:yahoo' is not one GnuCash recognizes (it would
    show as Invalid in the price editor). Use one of: Finance::Quote,
    invalid, temporary, user:invoice-post, user:price,
    user:price-editor, user:split-import, user:split-register,
    user:stock-split, user:stock-transaction, user:xfer-dialog. A
    quote feed is 'Finance::Quote'; a price you typed is 'user:price'.

**B9 PASS.** T1 (USD 70 → 60 EUR, 2026-09-15, a day with no EUR
row): `get_prices` that day shows one row,
`1.1667 USD transaction user:xfer-dialog` — the exact 7/6. T2 (USD
100 → 80 EUR, same day): still one row, now `1.25`. Updated in
place, never accumulated. Bonus: the duplicate rig flagged T2
against T1 at MEDIUM confidence with a full candidate table and
created it anyway — duplicate detection reads cross-currency rows
correctly.

**B10 — RULING: noise. Suppress it.** The specimen:

    CURRENCY:USD	US Dollar	0.9 EUR (2026-09-23)

while `get_latest_price` for USD answers `null`. Two tools disagree
about whether the book's default currency has a latest price;
`get_latest_price` is right. Every other line in the listing is
priced IN the default currency; a "price" ON the default currency in
a foreign unit restates the EUR row backwards, and a reader who
takes the listing at face value will average the two directions into
a rate that exists nowhere. The ROW stays — it is desktop's row,
written the old way round, and the reader is bilingual by design —
but `list_commodities` should print the default-currency cell as
`— (default currency)` and never search reversed rows for it,
matching `get_latest_price`'s null. Display-level, patch-sized, no
storage change, no desktop gate needed for it.

**B11 PASS.** `balance_sheet`: every `@ rate` at six decimals or
fewer (1.18, 0.7131, 178.7, 393.82, 9.58, 1853.7, 1.111111 at B1).
Cash math ties: Checking 50,422.73 + 1,652.00 payments in − 170.00
transfers out = 51,904.73 as shown; EUR Savings 315 + 60 + 80 =
455 EUR @ 1.18.

## Routed around (B12)

1. **The stale_fx_rate cure doesn't validate.** The refusal says
   `create_price(commodity='EUR', value='...')`; that call is
   refused — `namespace` is required. The error should name a call
   that works: `create_price(commodity='EUR',
   namespace='CURRENCY', value='...', date='...')`.
2. **The create_transactions contract surfaces one refusal at a
   time.** "needs a header row and at least one data row" → "batch
   header must start with ref, date, description" → only an
   unrecognized column finally printed the whole contract ("columns
   are ref, date, description, notes, cur, then amt, acct, memo,
   qty split groups"). Four rounds to first success. The full
   contract line already exists — print it in the first two errors
   too. (Bears on the small-model ladder test: a model that gives
   up at refusal two never learns the format.)
3. **Parameter argot, coached in one round each** (recorded as
   argot, not defect): `pay_document` wants
   `payment_account`/`payment_date`; `create_document` wants
   `document_type`/`owner_id`; `post_document` wants `post_account`;
   `add_document_entry` requires `document_type` even though
   document ids are unique across types — that one is worth a look.
4. `get_server_config` reports `Version: 1.4.4` on the 1.5 release
   branch. Bump before tag.
5. Grammar nit, two tools: "1 days past due" / "1 days overdue".

Nothing else was re-phrased or called twice.

## Litter manifest — what Part C inherits on `parity-loop.gnucash`

- Payment txn `1fdf75ba` (000048: Checking 1,062.00 / FX G&L 62.00 /
  AR EUR −1,000.00, USD, today)
- Invoice `000050` EUR 500, posted and paid in two partials (txns
  `1c0f35aa` 236.00, `83b8f21d` 354.00); posting txn `52fbad12`,
  lot `2eb6a39f`
- Price rows: EUR `user:price` 1.18 today; EUR xfer-dialog today
  updated to 1.18; EUR xfer-dialog 1.25 on 2026-09-15; AAPL
  `user:price` 178.70 today
- Transfers `24b46da6` (T1) and `fb6ad64f` (T2) on 2026-09-15,
  Checking → EUR Savings
- Outstanding after the run: only `000031` (EUR 5,400) remains on
  the EUR receivable

## Part C — the desktop gate (open; the maintainer, at the screen)

C1–C8 as planned, with the plan's expectations concretized to the
book as it now stands:

- **C5**: the Process Payment document list should omit `000048`,
  `000049`, AND `000050` (paid during Part B); `000031` (EUR 5,400)
  SHOULD appear.
- **C6**: the Customer Report balance for 000048/000049/000050 is
  zero; 000031's 5,400 is the only open balance.
- **C7**: Price Database, EUR — today: exactly two rows, both 1.18
  (`user:xfer-dialog` and `user:price`); 2026-09-15: one row, 1.25
  (`user:xfer-dialog`); July's 1.1435 quote untouched; no source
  reads Invalid. AAPL: a 178.70 `user:price` row for today above
  the 200 row of 2026-09-29.
- **C3**: the journal's three lines are Checking 1,062.00, Foreign
  Exchange Gain/Loss 62.00, the receivable — no Imbalance.

Storage shape changed in Part B writes (payment currency, price
rows, slot columns via converters), so per standing rule the gate is
REQUIRED before merge. The bookkeeper signs Part B; the gate
signature line below stays blank until the screen session.

Part C result: CLOSED — passed 2026-09-30, 12:50. Detail below.

## Part C results (maintainer at the screen, 12:32–12:50)

- **C1 PASS** — book opened clean; no dialog at open, and none for the
  whole session (maintainer: "No dialogs or notifications occurred").
- **C2 PASS** — (initially recorded as covered-by-evidence; upgraded
  on the maintainer's attestation, 13:05.) Find Invoice, exact ID
  typed, found exactly `000048`; double-click opened the invoice in
  the main window's tabbed interface with no prompt. (Small UX note,
  desktop's own: the Find window must be closed before the opened
  tab can be interacted with.) Paid status attested via C5/C6.
- **C3 PASS** — the payment journal in the EUR receivable register:
  three lines, no Imbalance. Displayed figures were 955.80 / 55.80,
  not the plan's 1,062.00 / 62.00 — the register converts the USD
  splits to EUR for display (× 0.9, the USD-in-EUR row desktop left
  on 9/23), and the proof of parity is that desktop's OWN payment of
  000049 directly above renders by the identical convention (its
  1,000.00 USD Checking split displays as 900.00). Display balances
  internally: 955.80 − 55.80 = 900.00. A USD register shows the plan's
  literal numbers. **Bonus attestation from the same screenshot:
  000050's Due Date column reads 09/30/2026 — the type-6 due-date
  writer, verified again on a fresh invoice.**
- **C4 PASS** — memo edit saved and undone, no rebalance prompt. The
  server-written USD payment shape survives desktop's edit path.
- **C5 PASS** — Process Payment lists only 000031 (EUR 5,400); 000048,
  000049 and 000050 all absent.
- **C6 PASS** — Customer Report: Total Due €5,400.00, all of it
  000031, aged correctly (61–90 bucket at 64 days). September's
  trio nets to zero on the report's face (900 + 900 + 500 invoiced,
  200 + 300 + 900 + 900 paid); column math ties (5,800 + 17,300 −
  17,700 = 5,400); every figure in the document currency; FX memos
  carry both rates into the description column. The new payment sits
  indistinguishably in the book's existing Jan/Apr/Jul FX lineage.
  (Cosmetic, desktop's own: same-day ordering places 000050's
  partials above its invoice, so the running balance transiently
  reads 7,000/6,700.)
- **C7 PASS** — Price Database as predicted: EUR today two rows both
  1.18, 2026-09-15 one row 1.25, July's 1.1435 intact, no Invalid
  source anywhere.
- **C8 PASS** — subwindows closed, app exited, no prompt.

## Verdict

Part B 11/11. Part C closed, one step covered by equivalent evidence
rather than performed. The desktop gate is satisfied for every
storage shape this branch changes: the USD payment transaction,
the price rows (typed, implied, updated-in-place), and the due-date
slots. One ruling filed (B10: suppress the default-currency latest
price in list_commodities) and five friction items, none blocking.

**`test/slot-shapes` at `b5c98b6` is cleared by the bookkeeper for
merge into 1.5.** Riders already standing: the B10 patch and
friction 1–2 (error-text fixes) can land as patches; friction 4
(version string) should land before the tag.
