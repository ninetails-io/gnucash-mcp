# Bookkeeper live loop — scheduled occurrences (fix/scheduled-occurrence-chokepoint)

Seven checks, one bounce, ~15 minutes. Branch under test:
`fix/scheduled-occurrence-chokepoint`. What it claims: every surface
that answers "which occurrence of a schedule is next" now gives the
SAME date — the oldest occurrence not yet entered, GnuCash's own
Since-Last-Run rule. Before, the dashboard said "overdue, due July
15" while the default instantiation posted October 15 and then
refused July forever. Also: template splits are stored by account
GUID (a rename no longer breaks a schedule), and a schedule's
overdue date shows as overdue everywhere it appears.

Alex is the fixture: as of 2026-09-08 he carries 14 overdue
schedules, which is exactly the state the old code mishandled.
Steps 1–3 are reads. Step 4 writes ONE catch-up transaction on
Alex that a real bookkeeper would enter anyway. Steps 5–7 run on a
scratch account + scratch schedule you create and delete. Bounce
the server first (editable install: the checkout is the server;
restart to pick up the branch). Fingerprint that you're on the
branch: `list_scheduled_transactions` on Alex shows `overdue:` in
the status column. If it shows `next:` with past dates, you're on
old code — stop and say so.

1. **Three surfaces, one date.** On Alex, call
   `get_book_summary`, `get_upcoming_transactions(days=14)`, and
   `list_scheduled_transactions`. Pick three schedules (suggest:
   Estimated Tax Payment, Robin's Paycheck, Mortgage Payment) and
   confirm the date each surface gives is identical per schedule.
   Expected for Estimated Tax Payment: 2026-07-15 on all three.
   Upcoming must lead with the most-overdue item, each schedule
   appearing exactly once, the days column reading `N days
   overdue` (negative `days_until` in verbose). The dashboard's
   overdue count and the count of `overdue`-marked rows in the
   list must match. Pre-fix, upcoming would have shown
   2026-09-15/2026-10-15 for these — dates the dashboard never
   mentioned.
2. **The Scheduled summary line, as its editor.** On Alex it now
   reads something like `Scheduled: 17 recurring, 14 overdue ⚠,
   none due in next 7 days`. Overdue schedules are deliberately
   NOT counted in the 7-day bucket (they'd be double-counted; they
   are named in the overdue bucket on the same line). Question for
   you: does "14 overdue" beside "none due in next 7 days" read as
   a contradiction on one line, or does the line carry it? If it
   grates, propose the copy.
3. **Lin Wei, read-only.** `get_upcoming_transactions(days=14)`
   on lin-wei. Same shape: overdue-first, `N days overdue`, CNY
   amounts unlabeled (book default), each schedule once. Nothing
   to write.
4. **Default instantiation lands on the overdue date, not past
   it.** On Alex, `create_transaction_from_scheduled` for
   Estimated Tax Payment with NO `transaction_date`. Expect
   `transaction_date: 2026-07-15` and `instance_count: 1`.
   Pre-fix this call posted 2026-10-15 — a future-dated tax
   payment five weeks out. Then `list_scheduled_transactions`:
   that schedule now shows `next:2026-10-15` and has left the
   overdue count (13 overdue). Leave the July transaction; it is
   the correct catch-up entry for Alex's books.
5. **Walking missed periods forward, on scratch.** Create an
   account `Expenses:BK Probe` (any expense type). Create a
   schedule `BK Probe` — monthly, `start_date` = today minus 70
   days, splits `Expenses:BK Probe` +42.00 / `Assets:Current
   Assets:Checking Account` −42.00. Then:
   - `list_scheduled_transactions` → `overdue:<start_date>`.
   - `get_upcoming_transactions(days=14)` → `70 days overdue`,
     amount 42.00.
   - `create_transaction_from_scheduled` three times with no
     date. Expect the dates to be start, start+1 month,
     start+2 months, `instance_count` 1, 2, 3. A fourth call
     posts a future date (start+3 months); either skip it or
     make it and delete it — your call, just note which.
   - Backfill guard still holds: call once more with
     `transaction_date` = start (already entered). Expect the
     "not after last occurrence" refusal and NO transaction.
6. **Rename survives.** `update_account` `Expenses:BK Probe` →
   `new_name="BK Probe Renamed"`. Then
   `create_transaction_from_scheduled` on BK Probe with
   `transaction_date` = start+3 months (or the next un-entered
   date). Expect `status: created` and the transaction's split on
   `Expenses:BK Probe Renamed`. Verbose
   `list_scheduled_transactions` shows the new path in the
   template's splits. Pre-fix: "Account not found: Expenses:BK
   Probe" and a template showing the dead path.
7. **End-of-schedule copy.** `update_scheduled_transaction` on
   BK Probe with `end_date` = yesterday. Then
   `create_transaction_from_scheduled` with no date. Expect a
   refusal: "No occurrence due (past end date, or a finite
   schedule has no occurrences remaining)". Judge the copy: does
   it tell you what to do next, or only what happened? Then
   clean up: `delete_scheduled_transaction` BK Probe, delete the
   probe transactions (`delete_transaction`, or `update_transactions`
   if you prefer the batch), `delete_account` on the renamed
   account. Zero residue on Alex except the step-4 July entry.

**Not live-testable, unit-locked only:** finite schedules
(`num_occur`/`rem_occur` countdown — no tool sets the limit; only
GnuCash desktop does), and the torn-write rollback. Both have
tests that were watched failing under mutation. Also out of scope,
deliberately: MCP-created schedules are still invisible to GnuCash
desktop's Since-Last-Run (S-2 in
`specs/v1.5/review/CODE_REVIEW_SCHEDULING_2026-09-07.md`). If you
open a demo book in GnuCash desktop during this loop, do NOT click
OK on Since-Last-Run — it advances every schedule with nothing
posted. That is a separate ruling.

**Report:** pass/fail per step with actual response text where it
deviates; the standing question — what did you route around, and
specifically whether you had EVER been passing explicit
`transaction_date` values to instantiation because the default
landed wrong (that workaround is the bug's fingerprint in your
history); and the two editorial calls (step 2 line, step 7
refusal). Acceptance bar for step 1: three tools, one date, no
arithmetic required of the reader.


---

## BOOKKEEPER REPORT — loop executed 2026-09-08

On a clean Alex (restored to HEAD first — 1,940 transactions, hash
matches; skip-worktree bit cleared on his file). Fingerprint
confirmed: the list shows `overdue:` dates, not `next:` with past
dates.

- **1. Three surfaces, one date — PASS.** Estimated Tax Payment:
  dashboard names it first in the overdue warning ("oldest 56
  days"), list `overdue:2026-07-15`, upcoming 2026-07-15 /
  `days_until: -56`. Robin's Paycheck 2026-07-24 (−47) and Mortgage
  2026-08-15 (−25) identical on all three. Upcoming led with the
  most overdue, each schedule once, 14 rows; dashboard "14 overdue"
  = 14 overdue-marked rows. No arithmetic required. Bonus that
  earned its place: the dashboard now leads with "Book is 53 days
  behind — time-based warnings below may reflect unentered
  activity, not missed events," which is exactly the caveat a
  stranger needs above a wall of overdue lines.
- **2. The Scheduled line — it grates.** `17 recurring, 14 overdue
  ⚠, none due in next 7 days` reads as a contradiction to anyone
  who doesn't already know the buckets are disjoint. One word
  carries it. Proposed: `Scheduled: 17 recurring — 14 overdue ⚠
  (oldest 56 days), none further due in next 7 days`. "Further"
  tells the reader the 7-day bucket starts after the overdue set,
  and pulling "oldest N days" onto the line saves a trip to the
  warning block. When nothing is overdue it collapses to `17
  recurring, 1 due in next 7 days (USD 41)` as today.
- **3. Lin Wei — PASS, one copy note.** 15 rows, overdue-first
  (陈宇工资 and 物业管理费 at 56 days, the rest at 25), each once,
  amounts unlabeled. But the amounts render `15000` and `850` where
  Alex's render `4200.00` and `42.00`. CNY has the same two-decimal
  fraction; the padding should follow the commodity's fraction,
  not whether the book is USD.
- **4. Default instantiation — PASS.** No date given:
  `transaction_date: 2026-07-15`, `instance_count: 1`, guid
  ed4a5805. List then shows `next:2026-10-15`, 13 overdue. Left in
  place as Alex's July catch-up.
- **5. Walk forward, scratch — PASS with the one carried finding.**
  Expenses:BK Probe created; schedule BK Probe monthly from
  2026-07-01 (today −70). List `overdue:2026-07-01`; upcoming `70
  days overdue`, 42.00. Three no-date calls posted Jul 1, Aug 1,
  Sep 1 with instance_count 1, 2, 3. Skipped the fourth future call
  and let step 6 consume Oct 1. Backfill guard with
  `transaction_date=2026-07-01`: "Transaction date 2026-07-01 is
  not after last occurrence 2026-09-01. The schedule has already
  been run through that date (possibly by GnuCash desktop). Use a
  later date." — refused, nothing written. Good copy; it even
  names the desktop as a suspect.

  **The carried finding, reproduced on this branch:**
  `create_scheduled_transaction`'s response said
  `next_occurrence: 2026-10-01` for a schedule starting 2026-07-01
  with nothing entered, while `list_scheduled_transactions` one
  call later said `overdue:2026-07-01`. The create response is the
  one surface still off the rule — it computes start-plus-one-
  period. Same thing seen yesterday and, in hindsight, on
  production Saturday. Not in the plan's seven, but it's the same
  chokepoint, and it's the first number a user sees after creating
  a schedule.
- **6. Rename — PASS.** Renamed to BK Probe Renamed; instantiation
  dated Oct 1 landed its split on Expenses:BK Probe Renamed;
  verbose list shows the new path in the template splits,
  `instance_count: 4`, `last_occurrence: 2026-10-01`,
  `next_occurrence: 2026-11-01`.
- **7. End-of-schedule copy — refusal fires, copy tells only what
  happened.** After `end_date=2026-09-08`: "No occurrence due (past
  end date, or a finite schedule has no occurrences remaining)."
  The server knows which of the two it is and should say so, and
  say what to do. Proposed: `No occurrence due: 'BK Probe' ended
  2026-09-08 (last entered 2026-10-01). Clear the end date with
  update_scheduled_transaction(end_date="") to resume, or
  delete_scheduled_transaction if it's finished.` — and the sibling
  for the finite case: `…has entered all N occurrences.` The "or"
  in the current text is the server declining to look at its own
  data.
- **Cleanup:** schedule deleted, four probe transactions deleted in
  one batch, renamed account deleted. Alex is at 1,941 — HEAD plus
  the step-4 July entry, nothing else.
- **Routed around: yes.** Explicit dates had been passed to every
  instantiation — that's the fingerprint, and it's in the audit
  log: Saturday on production, after rebuilding the seven
  schedules, `transaction_date` went explicitly to every
  instantiation (Sep 1 rent, Sep 3 storage, Sep 9 Apple One)
  because the create response said the next occurrence was October
  and the default couldn't be trusted to land in September. The
  response was never questioned; it was worked around. Same
  create-response bug as step 5, and the reason it belongs in this
  branch rather than a follow-up. Nothing else routed around. One
  cost note, not a bug: verbose upcoming returns every split of
  every schedule — 14 rows cost as much as a full dashboard. The
  compact form was enough for everything the plan asked.
- **PR word:** merge once the create-response site is on
  `_sx_next_due`; the two copy proposals can ride along or follow.

### Follow-up, 2026-09-09 (same branch)

All four landed, each with a lock: the create response reads
`_sx_next_due` (a 70-day-old schedule answers its start date, and
list agrees one call later); the Scheduled line reads `N overdue ⚠
(oldest D days), none further due in next 7 days` and drops
"further" when nothing is overdue; upcoming amounts render at the
template currency's quantum (Lin Wei's 15000 → 15000.00); the
refusal names the stop and the next move — `'X' ended YYYY-MM-DD
(last entered …). Clear the end date with
update_scheduled_transaction(end_date="") to resume, or
delete_scheduled_transaction if it's finished.` and, for finite
schedules, `'X' has entered all N occurrences.` The proposed em
dash after "recurring" was kept as a comma to match the line's
existing separators. Re-probe on the committed Alex (carrying the
July catch-up): `Scheduled: 17 recurring, 13 overdue ⚠ (oldest 47
days), none further due in next 7 days`.
