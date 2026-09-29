# Bookkeeper live loop — dashboard accuracy and sensitivity (fix/dashboard-sensitivity)

Branch under test: `fix/dashboard-sensitivity` at `823cbc9`, sixteen
commits on develop at `736e24e` (#196). Spec:
`specs/v1.5/DASHBOARD_ACCURACY_SPEC.md`. **Two commits touch storage
(A2 writes `trans-date-due` on every post; B4 writes desktop's
`reconcile-info` frame on every reconcile), so the desktop-open gate
(Part C) is required, not optional.**

What it claims, in the order the dashboard shows them:

- **Last entry stops at today** (A3). A posted-ahead bill or a 2062
  typo no longer hides staleness; future entries are counted beside
  the line, and one more than a year ahead gets its own ⚠.
- **Unbalanced transactions are integrity defects** (A8). A
  transaction whose split values don't sum to zero is named, even
  when nothing sits in Imbalance.
- **Overdrawn accounts warn** (A4), one line each, own currency,
  most negative first, before low-cash. Warning amounts render at the
  currency's quantum (`USD 0.75`, never `USD 0`).
- **Low cash has a second trigger** (B6): balance below the account's
  scheduled cash out over the next 7 days. A new account's pace
  divides by its own age.
- **Overdue documents agree with `get_outstanding_documents`** (A1):
  one settlement pass feeds the counts and the warnings; an overpaid
  document is open but never overdue.
- **Due dates follow GnuCash's billterm math** (A2). Proximo terms
  and due-on-receipt work; the 30-day default and "(no term set)"
  are gone — a document with no terms is due on its posting date, as
  in desktop. Every post writes `trans-date-due`; a caller's
  `due_date` that disagrees with the terms is refused. The
  `no_terms` key left the outstanding-documents response.
- **Stale prices read the rate valuation uses** (A5), and a currency
  is checked only while an account holds it or a transaction in the
  last 90 days used it (B1).
- **Reconciliation:** "behind" is measured from the oldest
  unreconciled split after the last reconcile; older ones are
  *outstanding items* with their own note (B2). A never-reconciled
  zero-balance account idle 180 days is dormant; a hidden zero-balance
  account is excluded (B3). The threshold follows the statement cycle
  desktop recorded, interval + 15 days (B4).
- **Monthly net converts at monthly closes** (A6), and the MTD row
  and budget actuals stop at today (A7).
- **Runway** burn is cash leaving a BANK/CASH account (a share sale
  into brokerage cash burns nothing), and card balances read beside
  the number as `cards owe` (B7).
- **Budget pace follows the budget's own period targets** (B5). The
  line now reads `<CUR> X spent / <CUR> Y expected by today (±Z%)`.

## Setup

- Check out `fix/dashboard-sensitivity` in the server's checkout and
  restart the server. `get_server_config` should answer; there is no
  new tool and no new parameter.
- **Writes go to copies, never the production book.** Copy Alex and
  Lin Wei: `cp samples/alex-chen-morales.gnucash /tmp/alex-dash.gnucash`
  and `cp samples/lin-wei.gnucash /tmp/lin-dash.gnucash`; point the
  server at the copies and confirm the `Book:` line before any write.
- Write down today's date; several expectations are day counts.

## Part A — the oracles moved only where ruled (done by the maintainer's session)

Capture rig, develop (`736e24e`) vs `99b8d2d`, same day, all three
samples. Every differing line traces to a ruled item: past-due
amounts at the quantum (`USD 3,500.00`); Alex's Checking Account
reads `992 splits … 18 months behind, oldest: 2025-04-01` plus
`1 outstanding item older than last reconcile (oldest 2024-03-01,
USD 1000.00 net)` where it read `993 splits … 3 years behind`;
`cards owe USD 21,927` / `CNY 60,633` beside runway; Sabine's
`Stale price: USD` gone (nothing holds USD, nothing used it in 90
days). No other number moved.

## Part B — on the copy of Alex (Lin Wei where named)

Call `get_book_summary` first and keep the output; most steps compare
against it.

1. **Baseline reads as Part A says.** The Checking Account line and
   its outstanding-items note; the Runway line ending
   `; cards owe USD 21,927)`; `Last entry: 2026-07-18 (N days behind) ⚠`
   with no future-dated note.
2. **Future entry does not hide staleness (A3).** `create_transactions`,
   one row dated `2062-03-15`, Checking `-10` / Groceries `10`,
   description `Typo year`. Expected on the next summary: the
   `Last entry:` line unchanged except a trailing
   `(1 future-dated, latest 2062-03-15)`; `Data range:` now ends
   `2062-03-15`; a Warnings line `1 transaction dated more than a year
   ahead (latest 2062-03-15) — likely a typo; search_transactions to
   inspect`. Then the same row dated seven days ahead: the count
   becomes `2 future-dated` and the typo line still says `1`.
   `delete_transaction` both before step 3.
3. **Overdraft (A4).** `create_account` `Assets:Current Assets:Petty
   Cash` type CASH. `create_transactions`: Petty Cash `-50.25` /
   Groceries `50.25`, dated yesterday. Expected: `⚠ Overdrawn: Petty
   Cash at USD -50.25`, placed after any integrity/backup lines and
   before any `Critically low cash` line. Then a deposit of `5000`
   dated seven days ahead: the line stays (a future deposit can't
   clear today's overdraft). Delete the deposit; leave the overdraft
   for step 4.
4. **Low cash from scheduled bills (B6).** Deposit `450` into Petty
   Cash dated yesterday (balance `399.75`). `create_scheduled_transaction`
   `Petty rent`: Petty Cash `-2100` / Expenses:Housing:Rent `2100`,
   monthly, start date three days from today. Expected: `⚠ Low cash:
   Petty Cash at USD 399.75, USD 2,100.00 scheduled out by
   <today+3>`; the Scheduled line counts it as due in the next 7
   days with `USD 2,100 out`. Move the schedule's start to ten days
   ahead (`update_scheduled_transaction`): the low-cash line
   disappears. Delete the schedule.
5. **MTD and budget stop at today (A7).** Skip on the last day of a
   month. Note the `Sep 2026 (MTD)` row, then `create_transactions`
   Groceries `4000` / Checking `-4000` dated tomorrow. Expected: the
   MTD row is unchanged. Delete it.
6. **A 0.75 residual is not "USD 0" (A1).** `create_document` invoice
   for an existing customer, one entry `100.75`; `post_document`
   dated 60 days ago with `due_date` 30 days ago; `pay_document`
   `100`. Expected: `⚠ Past due invoice: <customer> 30 days overdue,
   USD 0.75`; the Receivables line's overdue count is one higher
   than baseline; `get_outstanding_documents` shows the same document
   with `days_past_due` 30 and `amount_due` `0.75`, and the number of
   rows with `days_past_due > 0` equals the dashboard's overdue count.
7. **Due-on-receipt is a real term (A2).** `create_billterm` `Due on
   receipt`, `due_days` 0. `create_document` invoice with
   `term="Due on receipt"`, entry `500`; `post_document` dated 40 days
   ago, no `due_date`. Expected: `40 days overdue, USD 500.00`;
   `get_outstanding_documents` (verbose) `due_date` equals the posting
   date and has no `no_terms` key.
8. **Terms win (A2).** Another invoice with `term="Net 30"`, entry
   `250`. `post_document` dated 40 days ago with `due_date` 20 days
   ago. Expected: refused, naming both dates (`… disagrees with the
   document's terms 'Net 30', which put it due <post+30> …`); the
   document is still unposted. Post again with no `due_date`:
   posted, `10 days overdue, USD 250.00`.
9. **No terms means due on posting (A2, behavior change).** Invoice
   with no term, entry `75`, posted 20 days ago, no `due_date`.
   Expected: `20 days overdue, USD 75.00`, and nowhere the words
   `30-day default` or `no term set`. `get_outstanding_documents`
   compact reads `20 days past due`.
10. **Stale prices name only what matters (A5, B1).** Every commodity
    in the `Stale prices` rollup must have an account holding a
    non-zero balance in it (`list_accounts`, `get_balance`) or, for a
    currency, a transaction in the last 90 days. `create_price` for
    one of the named commodities dated today: it leaves the rollup
    on the next summary.
11. **Monthly net equals the flow reports (A6), on the Lin Wei copy.**
    For `Jun 2026`: `income_by_source` June 1–30 total minus
    `spending_by_category` June 1–30 total, rounded to whole CNY,
    equals the dashboard's June row. Repeat for one more full month.
12. **Behind vs outstanding (B2), on Alex.** `get_reconciliation_status`:
    Checking Account bucket `behind`, `992 unreconciled (oldest:
    2025-04-01)`, and `1 outstanding older than last reconcile
    (oldest: 2024-03-01)`. `get_unreconciled_splits` on Checking
    reports 993 in total: the two buckets sum to the detail tool.
13. **A paid-off card goes dormant (B3).** `create_account`
    `Liabilities:Credit Card:Old Store Card` type CREDIT; a `120`
    charge and a `120` payoff both dated 400 days ago. Expected: the
    Reconciliation section's `N accounts dormant ($0, idle)` count is
    one higher than baseline and `never reconciled` did not grow;
    `get_reconciliation_status` lists the card as `dormant`. Repeat
    with the charge and payoff dated 30 days ago on a second card: it
    lists as `never`.
14. **Reconcile records the statement cycle (B4).** `reconcile_account`
    on `Assets:Current Assets:Savings Account`, `reconcile_all`,
    `statement_date` `2026-04-30` with the balance `get_balance`
    reports as of that date; then again with `2026-07-31`. Expected:
    both succeed; the audit log shows two RECONCILE lines. Optional,
    stricter:

        sqlite3 /tmp/alex-dash.gnucash "SELECT name, int64_val FROM slots WHERE name LIKE 'reconcile-info%' ORDER BY name;"

    Expected: one `reconcile-info` row per reconciled account, a
    `reconcile-info/last-date` whose value is a Unix time on
    2026-07-31, `reconcile-info/last-interval/months` = 3 and
    `…/days` = 0.
15. **Budget pace follows period targets (B5).** `create_budget`
    `2026 Test` for year 2026, 12 periods; `set_budget_amount`
    Groceries `1200` for period 0 only and Utilities `900` for period
    11 only. Expected line: `Budget (2026 Test): USD <groceries spent
    in January> spent / USD 1,200 expected by today (…)` — expected is
    the January target in full and nothing of December's. Then set
    Groceries `500` for all periods: expected becomes
    `1,200 + 500 × (months fully elapsed) + 500 × (day of month ÷
    days in month)`, rounded; `get_budget_report` per-period targets
    reconcile to it.
16. **Runway burn ignores a share sale into brokerage cash (B7).**
    `create_account` `Assets:Investments:Brokerage:Cash` type ASSET.
    `create_transactions`, one row: sell 2 AAPL (`qty -2`, amount
    `-400`) into Brokerage:Cash `400`, dated yesterday. Expected: the
    Runway line's `/day cash out` is unchanged from baseline (`USD
    194/day`) and `cards owe` is unchanged. Then a `400` rent payment
    from Checking dated yesterday: `/day` rises by about 2.

## Part C — the desktop-open gate (the maintainer, at the screen)

Use the looped copy from Part B, or a fresh copy with steps 7, 8, 9
and 14 applied. Never the production book.

1. **Opens clean.** GnuCash desktop, File → Open, the SQLite copy.
   Expected: no format, feature or "unknown key" dialog.
2. **Server-posted due dates are desktop's.** Business → Customer →
   Find Customer, open the step-7 invoice. The Due Date field shows
   the posting date; the step-8 invoice shows posting + 30. Post a
   vendor bill on the copy from the server with `Net 15` and no
   `due_date`, then Business → Vendor → Bills Due Reminder (or the
   since-startup reminder): it lists the bill on posting + 15, the
   date `get_outstanding_documents` reports.
3. **Desktop-posted due dates are the server's.** In desktop, create
   and post a new customer invoice with `Net 30`. Server:
   `get_outstanding_documents` shows `due_date` = posting + 30 and
   `get_book_summary` ages it from that date.
4. **The reconcile dialog reads the server's frame.** Actions →
   Reconcile on Savings Account. Expected: the Statement Date
   proposed is `2026-10-31` (last-date 2026-07-31 plus the recorded
   3-month interval; the last day of the month is kept). Cancel.
5. **Desktop's reconcile is read back.** Reconcile Savings Account in
   desktop with statement date `2026-10-31`, Finish. Server:
   `get_reconciliation_status` shows Savings `through 2026-10-31`.
   Optional, stricter: the sqlite query from step 14 still shows ONE
   `reconcile-info` row for Savings, `last-date` on 2026-10-31,
   `months` 3.
6. **Edit, save, close, reopen.** Change one amount in the Checking
   register, save, quit, reopen. Expected: clean; the server's
   `get_book_summary` still answers.
7. Optional, stricter — every posted document carries the slot:

        sqlite3 /tmp/alex-dash.gnucash "SELECT COUNT(*) FROM invoices WHERE date_posted IS NOT NULL AND post_txn NOT IN (SELECT obj_guid FROM slots WHERE name = 'trans-date-due');"

   Expected: `0` after any business write on the branch (the
   backfill runs on the first one and the response reports
   `due_dates_backfilled`).

## Routed around

Name every workaround taken, with the tool that forced it. Known in
advance: `update_account` cannot set `hidden` (B3's hidden half is
unit-tested and desktop-settable only); proximo terms cannot be
created by `create_billterm` (unit-tested against gncBillTerm.c; to
exercise one live, set `type = 'GNC_TERM_TYPE_PROXIMO'` and `cutoff`
on a term row with sqlite, then post against it).
