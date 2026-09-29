# Bookkeeper report — dashboard accuracy and sensitivity (fix/dashboard-sensitivity)

Run: 2026-09-28, remote bookkeeper (Abe VI) on Ixion. Branch checkout at
`11a32ab` (code `823cbc9`); every call over stdio via the class6 harness
(`REPO` env — unpatched, b1756e0 holding). Books: copies of the
WORKING-TREE Alex and Lin Wei samples (see route-around 1) in
`~/Projects/abe-bench/` (`alex-dash`, `lin-dash`). Today: 2026-09-28.
Production book and registered books untouched.

## Verdict: Part B 16/16 PASS — and **Part C step 2 FAIL: desktop cannot read A2's `trans-date-due` slots**. The gate caught a shipping-stopper the server-side battery could not see. Six route-around notes. *(Superseded: fixed in `bbd86a0`; Part C re-run CLOSED, PASS — see the end of this report.)*

## Part B — step by step
1. **Baseline — PASS.** Checking `992 splits … 18 months behind, oldest
   2025-04-01` + `1 outstanding item older than last reconcile (oldest
   2024-03-01, USD 1000.00 net)`; Runway `… USD 194/day … ; cards owe
   USD 21,927)`; `Last entry: 2026-07-18 (72 days behind) ⚠`, no future
   note. All as Part A captured.
2. **Future entries (A3) — PASS.** 2062 row: Last entry unchanged +
   `(1 future-dated, latest 2062-03-15)`; Data range ends 2062-03-15;
   typo warning verbatim with the search hint. +7d row: `2 future-dated`,
   typo line still `1`. Both deleted clean.
3. **Overdraft (A4) — PASS.** `⚠ Overdrawn: Petty Cash at USD -50.25`
   (quantum, never 0); a future 5,000 deposit did NOT clear it.
4. **Low cash from schedule (B6) — PASS.** Verbatim: `⚠ Low cash: Petty
   Cash at USD 399.75, USD 2,100.00 scheduled out by 2026-10-01`;
   Scheduled line counts `USD 2,100 out`. Boundary tested by fresh
   schedule at +10 days → no warning, `none further due in next 7 days`
   (the plan's update-path was not expressible — route-around 2).
   Bonus: the first schedule write reported the A2 backfill in-band
   (`due_dates_backfilled: 54`) and converted the 17 legacy-recipe
   schedules, clearing that warning.
5. **MTD stops at today (A7) — PASS.** Sep MTD −50 before and after a
   4,000 charge dated tomorrow.
6. **0.75 residual (A1) — PASS.** `⚠ Past due invoice: Emerald Analytics
   30 days overdue, USD 0.75`; overdue count 7→8; outstanding shows
   `days_past_due 30`, `amount_due 0.75`; dashboard count equals rows
   with days_past_due > 0.
7. **Due on receipt (A2) — PASS.** 0-day term posts due on the posting
   date; `40 days overdue, USD 500.00`; verbose has no `no_terms` key.
8. **Terms win (A2) — PASS.** Refusal verbatim: `due_date 2026-09-08
   disagrees with the document's terms 'Net 30', which put it due
   2026-09-18 for a 2026-08-19 posting. Omit due_date to use the terms
   (as GnuCash does)…`; document stayed unposted; reposted termwise →
   `10 days overdue, USD 250.00`.
9. **No terms = due on posting (A2) — PASS.** `20 days overdue,
   USD 75.00`; due_date equals posting date; no `30-day default`,
   no `no term set` anywhere.
10. **Stale prices (A5, B1) — PASS.** All 7 rollup members hold
    non-zero balances (AAPL/MSFT/VBTLX/VTSAX/ETH positions, CAD/EUR in
    the FX receivables). A today-dated ETH price dropped the rollup to
    6 on the next summary.
11. **Monthly net = flow reports (A6), Lin Wei — PASS.** June:
    71,422.26 − 34,483.18 = 36,939.08 → `+36,939`. May:
    38,100.00 − 35,875.84 = 2,224.16 → `+2,224`.
12. **Behind vs outstanding (B2) — PASS.** Status buckets (997 behind
    after probe litter + 1 outstanding, oldest 2024-03-01) sum exactly
    to `get_unreconciled_splits`' total of 998.
13. **Dormancy (B3) — PASS.** 400-day-idle paid-off card lists
    `dormant` (`1 account dormant ($0, idle)` appeared); the 30-day one
    lists `never`; `never reconciled` grew only by the recent card.
14. **Reconcile records the cycle (B4) — PASS, strict.** Both
    reconciles booked (16 then 2 splits, balances tied); two RECONCILE
    audit lines; slots hold ONE `reconcile-info` frame:
    `last-date` on 2026-07-31, `last-interval/months = 3`, `days = 0`.
15. **Budget pace (B5) — PASS on behavior; plan formula nit.**
    Jan-only target: `USD 5,922 spent / USD 1,200 expected by today
    (+394%)` — January in full, nothing of December's. After setting
    Groceries 500 for ALL periods (which overwrites period 0 — the
    plan's `1,200 + …` formula assumed it wouldn't): expected
    `4,467` = 500×8 + 500×(28÷30), matching the per-period rule
    exactly. Note: with two budgets in the book, the dashboard paced
    the newly-created one.
16. **Runway ignores a share sale (B7) — PASS.** Adjacent summaries:
    195/day immediately before the AAPL sale, 195/day after (my own
    earlier probe litter had drifted baseline 194→195 — the sale
    itself moved nothing); a 400 rent payment moved it to 197 (+2);
    `cards owe USD 21,927` constant throughout.

## Routed around
1. **The plan's baselines bind to an unversioned artifact — by
   design, so fingerprint it.** The samples are deliberately kept out
   of commits (blob doctrine: skip-worktree, pre-commit guard, CI
   regenerates from frozen bases), and committing them would bloat the
   repo forever — the bookkeeper's first draft of this note said
   "commit them" and is hereby retracted. But the plan's 992+1
   baseline reproduces only from the maintainer's working-tree copy of
   the moment: a future re-run can't tell drift from regression. Small
   fix: the plan (or the capture rig) records the sample's sha256 at
   capture time, so any re-run knows whether it holds the same book.
   (For this run: worked from the working-tree copies as the plan's
   own `cp samples/…` instructs; baseline matched exactly.)
2. **`update_scheduled_transaction` cannot move a start date** (accepts
   enabled/end_date/notes…), so step 4's "move the schedule's start"
   is not expressible; tested the boundary by delete-and-recreate.
   Either give the tool a date-mover or reword the step.
   *(Maintainer, 2026-09-29: `update_scheduled_transaction` now takes
   `start_date`, moving the recurrence rows and the schedule's start
   together as desktop's editor does.)*
3. **`reconcile_account` wants `statement_balance`** where
   `enter_statement` says `closing_balance` — a third argot for the
   same number; my first guess (`ending_balance`) matched neither.
   *(Maintainer, 2026-09-29: `reconcile_account` now accepts
   `closing_balance`; the original name still works.)*
4. **Step 15's formula vs the tool:** an all-periods
   `set_budget_amount` overwrites period 0; the plan's expected-value
   line assumed January survived. Behavior is right; the plan sentence
   isn't.
5. **`get_unreconciled_splits` has no count-only mode**; pulled a
   50-row page for its total line. *(Maintainer: it has one —
   `limit=0` returns the indicator and totals only, as the tool's
   docstring says. A documentation miss on the plan's side.)*
6. **`mcpcall.py` continues past isError** — my bad `start_date` call
   errored and the following delete still ran, silently destroying the
   schedule under test. Fine for a harness, but battery authors should
   check every reply (I now do).

Litter manifest (alex-dash only; lin-dash untouched by writes): Petty
Cash (+399.75 with overdraft history), Expenses:Housing:Rent,
Brokerage:Cash (+400), AAPL 31→29 shares, Old/Recent Store Cards ($0),
invoices A9101 (partial, 0.75 due), A9102, A9103, A9104 (posted,
overdue), billterm "Due on receipt", budget "2026 Test", ETH price
2026-09-28, Savings reconciled through 2026-07-31, ~570 in probe cash
movements. Suitable for Part C as-is (steps 7/8/9/14 are all present),
per the plan's note that the gate can take the litter.

Signed: Abe VI. Every warning named its number at the quantum, every
due date is GnuCash's own arithmetic, and the dashboard now flinches at
exactly the things a bookkeeper flinches at.


## Part C — desktop gate, 2026-09-28 evening (maintainer at the screen, bookkeeper on storage)

**Step C2 FAIL.** On the looped copy, desktop shows: no due-documents
reminder at open (the bills reminder fired on this same desktop
yesterday against the precision book), Business → Invoices Due
Reminder a silent no-op, Receivable Aging entirely "Current", and
Find Invoice's Due column EMPTY for every server-written slot except
one absurdity — invoice 000013 (gdate_val `20260131`, slot_type 10)
rendered as **04/04/7161**.

Diagnosis: the server (A2 and its backfill, `due_dates_backfilled:
54`) writes `trans-date-due` as a piecash GDate (slot_type 10,
`gdate_val` YYYYMMDD). GnuCash desktop stores that key as a time64
(gncInvoice's `xaccTransSetDateDue`) and cannot decode ours: it treats
the documents as having no due date at all (worse than the
precision-era books, which have NO slot and fall back to
due-on-posting). Every server-side check in Part B passed because the
server reads its own format — the precise blind spot Part C exists
to cover.

Fix direction: write the slot exactly as GnuCash's SQL backend does
for this key (time64/timespec row; confirm against a desktop-posted
specimen), and re-run the backfill on affected books. A
desktop-posted Net-30 specimen is being captured on the looped copy
for byte-level comparison. Steps C3–C6 deferred until the format fix
lands; re-run the whole gate then.

### The specimen table (2026-09-28 evening, C3 exchange)

| writer        | slot_type | column       | value                  |
|---------------|-----------|--------------|------------------------|
| server (A2)   | 10        | gdate_val    | 20260918               |
| GnuCash desktop | 6       | timespec_val | 2026-10-28 10:59:00    |

- Desktop, posting a Net-30 invoice unaided, computed due 10/28/2026
  and wrote the row above (C3, desktop half: PASS).
- The server read desktop's slot straight back: `due_date 2026-10-28`,
  `days_past_due: -30` (C3, server half: PASS — the READ path is
  bilingual; only the WRITE path speaks the wrong dialect).
- Verdict sharpened: fix is write-only — A2's writer and its backfill
  emit `slot_type 6` timespec rows at GnuCash's neutral clock time
  (the same 10:59:00 convention as `transactions.post_date`); the
  reader needs nothing. Re-run the backfill on touched books.
- Etiquette pass observed in passing: with desktop holding the book,
  the server refused cleanly (`lock_error`, "Close GnuCash and try
  again") rather than fighting for the lock. After the desktop
  session, the summary answers normally.
- C4/C5/C6 (reconcile proposal, desktop reconcile read-back,
  edit-save-reopen) deferred to the post-fix re-run of the full gate.

## Part C — re-run after the timespec fix (2026-09-28 evening, `bbd86a0`)

The writer and backfill now emit `slot_type 6 | timespec_val <due>
10:59:00` (column for column the desktop specimen, pinned by
`tests/test_billterm_due_date.py::TestDesktopShape`), the reader
accepts both shapes, and the backfill rewrote the looped copy's 58
GDate rows in place (59 timespec rows after; desktop's own untouched).

- **C1 open — PASS.** No format or feature dialog.
- **C2 reminders — PASS.** On open, desktop raised the Due Invoices
  Reminder (11 documents: the A9101 0.75 residual due 08/29, the
  due-on-receipt A9102 due 08/19, the Net-30 A9103 due 09/18, and the
  sample's eight) and the Due Bills Reminder (BookkeepingCo, due
  08/07) — the dates the dashboard reports. Both were silent before
  the fix. Since Last Run listed the migrated schedules as To-Create
  (desktop reading the native recipes); cancelled, nothing posted.
- **C3 — PASS** (unchanged from the first run: desktop's Net-30
  specimen reads back as 2026-10-28).
- **C4 proposal — FAIL as written; the plan was wrong, not the
  server.** Desktop proposed today's date. `startRecnWindow`
  (window-reconcile.cpp) computes last date + interval and then
  clamps: `if (*statement_date > today) *statement_date = today;`.
  Savings' last date 2026-07-31 + 3 months = 2026-10-31 is after
  2026-09-28, so today is what desktop proposes — it read the frame
  and applied its own rule. The plan's expected value ignored the
  clamp, and its C5 instruction to reconcile ON 2026-10-31 asked for
  a future statement date, which no statement can carry (the
  maintainer's objection; desktop accepts it silently, as does
  `reconcile_account` — see the follow-up below). Valid re-check:
  a fresh account reconciled twice a month apart in the past, so the
  proposal lands before today (`Gate Test` below).
- **C5 desktop reconcile read back — PASS, strict.** After Finish on
  2026-10-31 the slots hold ONE `reconcile-info` frame:
  `last-date` 1793516399 (2026-10-31 23:59:59 local, desktop's
  day-end — the same convention `_write_reconcile_info` stores),
  `last-interval/months` 3, `days` 0, plus desktop's own
  `include-children` 0. Desktop wrote into the server's frame, not
  beside it.
- **C5 on Gate Test (past-dated re-check) — PASS, strict.** The
  server created `Assets:Current Assets:Gate Test`, deposited 100 on
  2026-05-15 and 2026-06-15, and reconciled 05/31 then 06/30 (frame:
  `last-date` 2026-06-30, `months` 1). After desktop's Finish on
  2026-07-31 the slots hold ONE `reconcile-info` frame for the
  account: `last-date` 1785567599 (2026-07-31 23:59:59 local),
  `last-interval/months` 1, `days` 0, plus desktop's
  `include-children` 0.
- **C4 on Gate Test — PASS.** Unaided, the Reconcile dialog proposed
  07/31/2026: 2026-06-30 plus the server-recorded 1-month interval,
  last day of month kept, before today so no clamp.
- **C6 edit-save-reopen — PASS.** Petty Cash 450.00 → 451.00 in the
  register, save, quit, reopen: clean, the same three dialogs and
  nothing else.

**Part C verdict: CLOSED, PASS** on `bbd86a0` (C1–C6). The gate did its
job twice over: it caught the due-date slot shape the server-side
battery could not see, and it caught the plan's own error about
desktop's future-date clamp.

Follow-up for the maintainer: neither desktop nor `reconcile_account`
refuses a statement date in the future. A statement can't be dated
after today; a future date is a typo. Whether the server should
refuse (or warn) is a tool-behavior call and is not in this branch.

Route-around: the maintainer drove C4–C6 at the screen. Claude's
desktop control could open the copy and read every dialog, but a
CleanShot X overlay refused every click, so the dialogs were
dismissed by hand.
