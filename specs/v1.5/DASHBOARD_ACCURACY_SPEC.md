# Dashboard accuracy — spec

**Status:** Part A built on `fix/dashboard-sensitivity` (2026-09-28,
one commit per item; A2's desktop gate still to close). Part B ruled
2026-09-28: all seven accepted as proposed, same branch, capture rig
once at the end of B.
**Source:** adversarial review of `get_book_summary`, 2026-09-25,
re-checked against `origin/develop` @ `736e24e` on 2026-09-27. No
merged change since `6a1f368` (#193) touched any dashboard collector
or renderer. The one related change is `_document_settlement`, which
now returns `sign`; A1 builds on it.
**Amends:** `specs/v1.2/features/GET_BOOK_SUMMARY_SPEC.md`, and
`DASHBOARD_HONEST_FAILURE_SPEC.md` (new check names in A4, A8).

## Problem

The dashboard is the first thing the LLM reads, so its warnings set
the agenda for the whole session. The review found three kinds of
problem:

1. **Wrong answers.** A warning fires about something that isn't
   true, or a number disagrees with the report tool it summarizes.
2. **Wrong on books outside the US.** The logic is correct only for
   price directions, payment terms, and statement cycles that are
   typical in the US.
3. **Poorly tuned thresholds.** Some warnings fire permanently on
   healthy books; others stay silent through a real problem.

Part A covers the first two. Part B covers the third. Every Part B
threshold is a policy choice, so each item there states a proposed
rule and waits for a ruling.

---

## Part A — correctness

### A1. Overdue documents go through `_document_settlement`

**Now.** The Warnings collector (`core.py` `_collect_warnings` §3)
and `_business_summary_counts` each call `_calculate_lot_balance`
themselves and `abs()` the result. This causes two faults:

- An **overpaid** invoice (A/R lot balance below zero) renders as
  "Past due invoice … overdue, USD 50", although the business owes
  that money back. `get_outstanding_documents` does this correctly:
  it reads `amount_due` / `overpaid` from the settlement chokepoint,
  never takes `abs()`, and stops aging overpaid documents.
- Amounts render through `int()`. A remaining balance of 0.75
  reads "USD 0 overdue".

**Rule.**
- One per-document pass feeds both the Receivables/Payables counts
  and the overdue warnings. That pass reads `_document_settlement`.
  Nothing in `core.py` calls `_calculate_lot_balance` directly.
- A document is *open* when `balance != 0`. It is *overdue* when it
  is open, `amount_due > 0`, it is not a credit note, and
  `due_date < today`. An overpaid document is open but never
  overdue. This matches `get_outstanding_documents` exactly.
- Amounts render at the lot commodity's quantum, never `int()`.

**Lock.**
- A grep-the-source test: no `_calculate_lot_balance` in
  `book/core.py`.
- An output-agreement test: the dashboard's overdue count equals the
  number of `get_outstanding_documents` rows with
  `days_past_due > 0`. Run it on a book containing one overpaid
  invoice, one credit note, and one 0.75 residual.

### A2. Due dates follow GnuCash's billterm math

**Now.** `_resolve_invoice_due_date` step 2 adds `duedays` to the
posting date for every term. This causes two faults:

- **Proximo terms** (`GNC_TERM_TYPE_PROXIMO`, such as "due the 20th
  of next month") are common in Europe and Australia and are created
  in desktop. The server reads their day-of-month as a day count, so
  documents on these terms go overdue on the wrong date.
- **`duedays = 0`** ("due on receipt") is falsy, so it falls through
  to the 30-day default and is labeled "(no term set)".

**What desktop does** (read from GnuCash `stable`, 2026-09-28):
- `gncInvoicePostToAccount` (`gncInvoice.c`) always calls
  `xaccTransSetDateDue`. Every desktop-posted document carries
  `trans-date-due`.
- The post dialog (`dialog-date-close.c`) picks the value:
  - **With terms:** `gncBillTermComputeDueDate(terms, post_date)`.
    The dialog's due-date field is overridden on OK, so terms always
    win.
  - **Without terms:** the dialog's date, which defaults to the
    posting date. `gncBillTermComputeDueDate(NULL, …)` also returns
    the posting date.
- `compute_time` / `compute_monthyear` (`gncBillTerm.c`):
  - **DAYS:** posting date + `due_days`.
  - **PROXIMO:** a `cutoff` of 0 or below adds the posting month's
    last day. If the posting day is on or before the cutoff, the due
    month is next month; otherwise it is the month after. The due day
    is `min(due_days, last day of the due month)`.

**Ruling (2026-09-28): the way desktop does.**

**Rule.**
- Port `compute_time` / `compute_monthyear` verbatim as the one
  due-date function, `_billterm_due_date(term, post_date)`. Use the
  same approach as `_recurrence_next` and Recurrence.cpp: a port, not
  a reinterpretation. `None` terms return the posting date. Pin the
  type strings (`GNC_TERM_TYPE_DAYS`, `GNC_TERM_TYPE_PROXIMO`) in a
  test.
- `duedays = 0` is a real term: due on the posting date.
- **The 30-day default is removed**, along with the "(no term set)" /
  "past 30-day default" wording. A document with no terms is due on
  its posting date, as in desktop. `no_terms` leaves the
  `_resolve_invoice_due_date` return value.
- **`post_document` writes `trans-date-due` on every post:**
  - With terms: the value comes from the port.
  - Without terms: the caller's `due_date`, defaulting to the
    posting date.
  - This is a storage change, so the desktop gate applies (see
    Gates).
- **Terms win, as in desktop.** A caller's `due_date` that disagrees
  with the terms is refused, naming both dates. Desktop discards the
  typed date silently; a tool response has no field for the user to
  watch change, so the server refuses instead of overriding. If the
  dates agree, the post goes through.
- **Reader:** read the `trans-date-due` slot. If it is absent, use
  the port. Both paths give the same answer.
- **Backfill:** a document the server posted without
  `trans-date-due` is a shape desktop never writes. Per the storage
  invariant, it goes to `_upgrade_book_shapes`, which writes the
  slot on the next business write and reports the count. Reads never
  write.

**Behavior change for the CHANGELOG.** Server-posted documents with
no terms and no explicit due date go overdue from their posting date
instead of 30 days later. On an existing book, invoices with no
terms may appear as overdue for the first time.

**Lock.**
- Table tests for the port, with expected values worked through
  GnuCash's `compute_time` by hand and cited in the test:
  - both term types;
  - cutoff on, before, and after the posting day;
  - a cutoff of 0 or below;
  - `due_days` 31 into a February due month;
  - December rolling over to the next year;
  - `duedays = 0`;
  - no terms.
- A post test: every `post_document` leaves `trans-date-due` on the
  posting transaction.
- The desktop gate: desktop's Due Bills Reminder lists a
  server-posted bill on the same date the dashboard reports.

### A3. "Last entry" ignores future-dated transactions

**Now.** "Last entry" is `max(post_date)` over every transaction.
One entry dated ahead (a posted-ahead bill, or a typo such as 2062)
switches off both the staleness ⚠ and the staleness note at the top
of Warnings.

**Rule.**
- "Last entry" is the latest `post_date` on or before today.
- When future-dated transactions exist, append
  `(N future-dated, latest YYYY-MM-DD)` to the line.
- A future-dated transaction more than 365 days ahead gets its own
  ⚠, because it is almost always a typo.
- `days_behind_for_warnings` comes from the capped date.

**Lock.** A book with a 2062 transaction and nothing entered for 30
days still shows the ⚠ and the staleness note.

### A4. Individual overdrafts warn

**Now.** Low-cash skips balances at or below zero and hands them to
runway. Runway flags only when the entire liquid pool is negative.
Checking at −300 beside savings at 10,000 is flagged nowhere.

**Rule.**
- Any BANK/CASH account whose balance as of today is below zero
  produces `Overdrawn: <leaf> at <CUR> -X`.
- The account must be non-placeholder, non-template, not
  auto-balancing, and not in a retirement subtree. This is the same
  filter low-cash uses; share it rather than copy it.
- Sort most negative first. Place the line immediately before
  low-cash.
- New check name for the honest-failure spec: `Overdraft`.
- Known false positive: a credit line typed BANK. The message stays
  as is. The fix for that is to change the account's type, which is
  the user's call.

**Lock.** The two-account book above produces exactly one Overdrawn
line.

### A5. Price staleness reads the rate valuation actually uses

**Now.** The stale-price collector keys on `p.commodity` only.
`_rates_as_of` also rates a commodity that appears only as the
*quote* side of a pair, and it chains through a pivot currency. A
EUR book that stores "1 EUR = 1.08 USD" values its USD accounts
correctly, but also shows a permanent "USD no price on file".

**Rule.**
- Staleness is the date of the rate the valuation uses.
- Add a chokepoint beside `_rates_as_of` that returns
  `{commodity_guid: (rate, rate_date, via)}`.
  - Direct and inverse rates: the date of that price.
  - Chained rates: the **oldest** leg's date.
- The dashboard's asset lines, runway, and the stale-price collector
  all read it. The collector stops walking `_find_prices` itself.
- "No price on file" means exactly this: the chokepoint has no
  entry and valuation falls back to cost basis.

**Lock.**
- The inverse-only EUR book produces no stale line.
- A chained rate whose pivot leg is 40 days old is stale at 40 days.
- Grep test: the collector does not call `_find_prices`.

### A6. Monthly net converts at monthly closes

**Now.** `_monthly_net_income` uses today's rate for all six months.
That contradicts the invariant that flow reports value at monthly
closes. On a multi-currency book, the dashboard's March figure
disagrees with `cash_flow`'s March.

**Rule.** Use `_monthly_conversion_factors`, the same factors
`income_by_source` and `spending_by_category` use.

**Lock.** Extend `TestModeAgreement`: for each full month shown, the
dashboard's net equals `income_by_source` total minus
`spending_by_category` total for that month. Run it on Lin Wei's and
Sabine's books.

### A7. Every "now" surface on the dashboard stops at today

**Now.** The balance surfaces cap at today. Two flow surfaces do
not:

- The MTD month sums through month-end, so a bill posted ahead to
  the 28th already counts on the 25th. The "vs prior month" figure
  it is compared against stops at today's day, so the comparison is
  not like-for-like.
- Budget actuals sum through the budget's end date, so pre-entered
  bills already count as "used".

**Rule.**
- The MTD bucket and budget actuals include `post_date <= today`
  only.
- The six-month window's end is `min(month_end, today)` for the
  current month.

**Lock.** A future-dated expense in the current month moves neither
the MTD line nor the budget headline.

### A8. Unbalanced transactions surface as integrity defects

**Now.** Integrity checks look only at Imbalance/Orphan balances. A
transaction whose non-voided split values don't sum to zero is never
reported unless GnuCash parked the remainder in Imbalance. Such
transactions come from raw-SQL imports, other tools, or corruption.

**Rule.**
- In one pass over the preloaded split graph, flag transactions
  where Σ `value` ≠ 0.
- In the same pass, flag same-commodity splits (account commodity =
  transaction currency) where `value` ≠ `quantity`.
- One line: `N unbalanced transactions (oldest YYYY-MM-DD) —
  get_transaction to inspect`.
- It renders with the other integrity lines.
- New check name: `Balance-integrity`.

**Lock.** Write one engineered unbalanced transaction through raw
SQL; it produces exactly one line. The sample books produce none.

### Rendering rule (applies to A1, A4, and low-cash)

A warning amount under one unit renders at the commodity quantum.
Whole-unit rounding is fine for trend lines (net worth, monthly net,
runway); it is wrong for a warning that names an amount owed.

---

## Part B — sensitivity (ruling needed on each)

### B1. A currency is stale only when it matters today

**Now.** Currencies are exempt from the "still held" filter. A
zero-balance EUR account from a 2019 trip triggers a stale warning
forever. Old transactions convert at their own month's rate, which
never goes stale.

**Proposed.** A non-default currency is checked for staleness only
when either:
- an account holds a non-zero balance in it today, or
- a transaction in the last 90 days is denominated in it or touches
  an account in it.

### B2. Separate "behind" from "outstanding items"

**Now.** The lag is measured from the oldest unreconciled split. One
cheque that never cleared makes a monthly-reconciled account read
"6 years behind ⚠".

**Proposed.**
- **Behind** is measured from the oldest unreconciled split *after*
  `latest_y_date`, as now for accounts that have never reconciled.
- Unreconciled splits dated *before* `latest_y_date` are
  **outstanding items**. They get their own line, no stronger than
  a note: `<leaf>: N outstanding items older than last reconcile
  (oldest YYYY-MM-DD, <CUR> X net)`.
- The note appears only when the oldest item is more than 90 days
  older than `latest_y_date`. That is the "cheque that never cleared
  is worth a look" signal, without calling the account behind.
- `_classify_reconciliation` stays the only place that decides the
  bucket, so `get_reconciliation_status` agrees.

### B3. Closed accounts go quiet

**Now.**
- "Never reconciled ⚠" fires forever on a zero-balance card that
  was paid off years ago.
- Nothing on the dashboard reads GnuCash's `hidden` flag, which is
  how desktop users close an account.

**Proposed.**
- *Never reconciled* + zero balance + no activity in 180 days goes
  to **dormant**.
- A `hidden` account with a zero balance is left out of
  reconciliation, low-cash, and stale-price checks.
- A `hidden` account with a non-zero balance stays visible. Money
  sitting in a closed account is itself a finding.

### B4. The reconciliation threshold follows the account's statement cycle

**Now.** The threshold is a fixed 45 days. It fires constantly on
quarterly and annual statements: brokerage cash, loans, and
European savings accounts with one statement a year.

**Proposed.** Read the statement interval desktop already stores:
the `reconcile-info/last-interval` frame (`days`, `months`) on the
account, written by desktop's reconcile dialog.
- Threshold = interval + 15 days of grace.
- When the frame is absent, the threshold stays 45.
- `reconcile_account` writes `reconcile-info/last-date` and
  `last-interval` the way desktop does. That is a storage change, so
  the desktop gate applies.
- Fetch the key names verbatim from `Account.cpp` and pin them in a
  test.
- No new tool and no new slot convention: this is desktop's own
  shape.

### B5. Budget pace follows the budget's own per-period targets

**Now.** Pace is linear in time across the whole budget span, so the
per-period targets are ignored. January insurance reads over pace
all year; a heavy December reads under pace until December.
Yearly, end-of-month, and daily recurrences drop the headline with
no message.

**Proposed.**
- Expected spend = the sum of targets for fully elapsed periods,
  plus the current period's target × the fraction of that period
  elapsed.
- Headline: `Budget (<name>): <CUR> X spent / <CUR> Y expected by
  today (±Z%)`. The ⚠ threshold stays at +10%, now measured against
  expected spend.
- Period boundaries come from `_recurrence_next`, the Recurrence.cpp
  port, so every GnuCash period type works. That function is
  module-level in `scheduling.py`; move it to `_base` if the budgets
  module must work without scheduling loaded.
- A recurrence the port can't handle renders a line saying so. It is
  never omitted silently.

### B6. Low cash accounts for bills already scheduled

**Now.** Low-cash fires only when the balance falls below one day of
the account's own average outflow. Checking at 400 with a 2,100
mortgage scheduled in 3 days stays silent. A new account's outflow
is also divided across the whole 180-day window, which makes the
warning even harder to trigger.

**Proposed.**
- Keep the one-day rule.
- Add a second trigger: the balance today is below that account's
  scheduled cash out over the next 7 days. Use the same recipes and
  cash-leg rule as `_upcoming_within_days`; factor out the
  per-account sum so the Scheduled line and this check can't drift.
- Message: `Low cash: <leaf> at <CUR> 400, <CUR> 2,100 scheduled
  out by YYYY-MM-DD`.
- The pace divisor is `min(window, days since the account's first
  split)`.

### B7. Runway burn: cash leaving BANK/CASH, and card debt shown

**Now.**
- Selling shares into ASSET-typed brokerage cash nets as money
  leaving the pool, because the STOCK leg is liquid and the ASSET
  cash leg is not.
- Card balances already owed don't shorten runway.

**Proposed.**
- A transaction's burn is `max(0, -min(pool_net, cash_net))`, where
  `pool_net` is the net of the liquid-pool legs and `cash_net` is
  the net of just its BANK/CASH legs.
  - A sale into ASSET cash burns 0.
  - Checking → brokerage burns 0.
  - Checking → rent burns the rent.
- The runway line adds `; cards owe <CUR> X` when CREDIT balances
  are non-zero. This is shown next to runway, not subtracted from
  it: whether the card is paid from savings or carried is the
  reader's call.

---

## Not in scope

- **English-only retirement fallback.** It is documented, and the
  `is_retirement` slot fixes it on any book. Revisit if the
  bookkeeper's localized persona trips it.
- **New MCP tools.** None are proposed. The tool surface is
  maintainer-gated.

## Gates

- **Desktop open.** A2 (the `trans-date-due` write) and B4 (the
  `reconcile-info` writes) touch storage. A book written by the
  branch must open and edit cleanly in GnuCash desktop before merge.
  Desktop's own due-bills reminder and reconcile dialog must read
  the values the server wrote.
- **Capture rig.** A6 and B5 change numbers the bookkeeper has
  validated. Capture before/after against the committed sample
  oracles: Alex, Lin Wei, and Sabine. Lin Wei and Sabine are where
  A5 and A6 move.
- **PostgreSQL.** A8 and A5 add passes; run the `postgres` job. No
  new raw SQL is expected. Any that appears is written in the
  three-dialect intersection.
- **Honest failure.** New collectors (Overdraft, Balance-integrity)
  route every `except` through `_check_failed`.
  `TestDashboardHonestFailure` already enforces this.

## Sequencing

- Part A is one branch off `develop` (`fix/dashboard-accuracy`).
  A1–A8 are independent, one commit each.
- A2's storage half can split off if the desktop gate stalls it.
- Part B follows the rulings, on the same branch or a follow-up —
  maintainer's call.
