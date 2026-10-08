# Bookkeeper live loop — native template transactions (feat/scheduled-native-templates)

Five checks, one bounce, ~25 minutes, and GnuCash desktop is the
instrument for three of them. Branch under test:
`feat/scheduled-native-templates`. What it claims: a schedule's
recipe is now stored the way GnuCash's SX editor stores it, so
desktop's Since-Last-Run posts what this server scheduled, and a
schedule made in desktop can be instantiated here. Existing
schedules migrate on the first write that touches them. Spec:
`specs/v1.5/SCHEDULED_NATIVE_TEMPLATES_SPEC.md`.

This is the one loop where the standing "click Cancel on
Since-Last-Run" rule is suspended — on a SCRATCH COPY only. Do not
click OK on the production book's dialog during this loop; nothing
here needs the real book.

Setup: copy Alex to a scratch file, configure the server on it,
bounce. Fingerprint: `list_scheduled_transactions` verbose rows carry
`recipe: legacy` (Alex's 17 were written pre-native) — if the key is
absent you're on old code.

1. **A fresh schedule is visible in desktop.** Create schedule
   "BK Native" — monthly from 2026-09-01, `Expenses:Streaming`
   +42.50 memo "probe" / `Assets:Current Assets:Checking Account`
   −42.50, description "Native probe", notes "hello". Verbose list:
   `recipe: native`, splits with paths, notes present. Open the
   scratch book in GnuCash 5.12: no notices; Actions → Scheduled
   Transactions → Scheduled Transaction Editor shows "BK Native";
   open it; the template ledger shows both legs with the right
   accounts and 42.50 on the right sides; the description reads
   "Native probe". Report exactly what the editor shows.
2. **Since-Last-Run posts it.** Still in GnuCash on the scratch
   copy, run Actions → Scheduled Transactions → Since Last Run and
   click OK for "BK Native" (its 2026-09-01 occurrence is past).
   Expected: one real transaction dated 2026-09-01, "Native probe",
   two splits on the right accounts, memo "probe" on the Streaming
   leg. Close GnuCash. Here: `list_scheduled_transactions` shows
   `last_occurrence: 2026-09-01`, `next:2026-10-01`; the
   transaction appears in `list_transactions`; and
   `create_transaction_from_scheduled` with
   `transaction_date=2026-09-01` is refused "not after last
   occurrence … possibly by GnuCash desktop" — the guard's message
   finally describes something that happened.
3. **A desktop-made schedule instantiates here.** In GnuCash on the
   scratch copy, create a schedule "BK Desktop" the normal way:
   monthly from 2026-09-15, `Expenses:Dining` 25 debit /
   `Assets:Current Assets:Checking Account` 25 credit, description
   "Desktop probe". Save, close GnuCash. Here: verbose list shows
   `recipe: native`, two splits, amounts 25.00 / −25.00 (order:
   debits first); `create_transaction_from_scheduled` with no date
   posts 2026-09-15 with those splits; `get_transaction` on it; and
   `get_audit_log` shows the CREATE FROM SCHEDULED line. Pre-branch
   this call failed "No split templates found".
4. **Legacy migrates on write, and desktop then sees it.**
   `create_transaction_from_scheduled` on Alex's "Estimated Tax
   Payment" (legacy, overdue since July) with no date. Expected:
   posts 2026-07-15, response carries `template_migrated: true`, the
   audit line says "recipe migrated to native template rows".
   Verbose list: `recipe: native`. Open the scratch copy in GnuCash:
   the editor shows Estimated Tax Payment with its two legs and
   4,200. Cancel Since-Last-Run this time (the other 16 are still
   legacy and would be consumed — that is the pre-branch hazard,
   still real for un-migrated schedules until their first write).
5. **Delete is safe for the target accounts.** Here:
   `set_account_slot` on `Expenses:Streaming` key `apr` value `0`
   (any slot). `delete_scheduled_transaction "BK Native"`. Then
   `get_account_slots Expenses:Streaming` still shows `apr`. Delete
   "BK Desktop" too. Open the scratch copy in GnuCash once more:
   opens clean, neither schedule listed, the two posted
   transactions still there. Pre-branch, deleting a desktop-made
   schedule this way could have wiped the target account's slots
   (piecash's GUID-slot cascade).

Cleanup: discard the scratch copy. Nothing touched the real
samples or production.

**Not live-testable:** the formula-with-variables refusal and the
desktop cross-commodity leg taking its rate from `book.prices`
(both unit-locked; Alex has no such schedule and creating one in
desktop needs a second commodity in the template).

**Report:** pass/fail per step; for steps 1, 3, and 4 the exact
figures GnuCash's editor displayed; the standing routed-around
question; and one editorial call — the verbose list's new `recipe`
and `problems` keys: earn their place, or noise once every schedule
is native?

---

## Round 1 report — 2026-09-09/10, Abe VII (Steve at the GnuCash GUI)

Filed at `BOOKKEEPER_REPORT_NATIVE_TEMPLATES.md`. Verdict: BLOCKED.
Native round-trip works both ways for server-shaped schedules (steps
1, 2, 5 PASS: desktop's editor shows what we wrote, Since-Last-Run
posts it, our backfill guard's message described a real event, the
target account's slots survived a delete). Three findings:

1. **Occurrence math ignored the recurrence rows.** A desktop
   schedule "start 9 Sep, monthly on the 15th" posted on the 9th and
   wrote `last_occur` 9th, so desktop's next run would post the 15th
   again. A composite schedule (monthly on the 5th AND 20th) read as
   plain monthly from the 20th.
2. **Migration crashed GnuCash's SX editor.** It wrote the template
   transaction onto the legacy template account (book currency,
   named by the schedule) instead of rebuilding the container.
3. **Delete orphaned the second recurrence row** of a composite.
   Plus, as the plan predicted: Since-Last-Run consumed all 16
   un-migrated legacy schedules with nothing posted.

## Round 2 — what changed

- `Recurrence.cpp` ported verbatim (`_recurrence_next`): the
  anchor is the recurrence row — period start, multiplier, all
  eight period types, weekend adjust with its Friday special
  case — and a schedule's next is the earliest across ALL its
  rows. `frequency` in responses is derived from the rows:
  the six familiar labels, `every 6 months`, `end of month,
  weekends forward`, `composite (2 rules)`. `start_date` only
  seeds a never-run schedule.
- Migration builds a fresh container exactly as create does
  (GUID-named account on the `template` commodity, splits at
  denominator 1), repoints the schedule, and drops the old
  account. Locked by a test that compares the rebuilt container
  to a fresh one.
- Delete removes every recurrence row (verified).
- Dashboard's Scheduled line reads `N on legacy recipe (migrates
  on first write)` while any remain, per your editorial.
  `problems` appears only when non-empty.

Re-run on a FRESH scratch copy of Alex (the round-1 copy carries
the crash repro and stays as evidence):

1. **Desktop anchor.** In GnuCash, create "BK Anchor": start
   2026-09-09, monthly on the 15th, Dining 25 / Checking 25. Close.
   Here: list shows `next_occurrence 2026-09-15`, frequency
   `monthly`; instantiate with no date → posts 2026-09-15; list
   then `next 2026-10-15`. Reopen desktop, run Since-Last-Run:
   nothing offered for BK Anchor (its 15th is done).
2. **Composite.** In GnuCash, create "BK Twice": monthly on the
   5th AND the 20th from 2026-09-01, any legs. Close. Here:
   frequency `composite (2 rules)`, `next 2026-09-05`;
   instantiate twice with no date → 09-05 then 09-20; a third →
   10-05. Delete it here; `SELECT COUNT(*) FROM recurrences WHERE
   obj_guid = <its guid>` is 0.
3. **Migration opens in the editor.** Instantiate "Estimated Tax
   Payment" (legacy) with no date → `template_migrated: true`.
   Open the copy in GnuCash; SX Editor → open Estimated Tax
   Payment: NO crash, two legs, 4,200 on the right sides. This is
   the gate.
4. **Weekend adjust, if cheap.** In GnuCash set BK Anchor's
   weekend adjustment to "forward" and move its day to a
   weekend date this month; here the label reads `monthly,
   weekends forward` and `next_occurrence` is the Monday.
5. **Dashboard line.** `get_book_summary` on the copy after step
   3: `… 16 on legacy recipe (migrates on first write)`.

Acceptance: step 3's editor opening the migrated schedule without a
crash, and step 1's Since-Last-Run offering nothing for a schedule
this server posted on the desktop's day.

---

## Round 2 report — 2026-09-10, Abe VII (Steve at the GUI)

Filed at `BOOKKEEPER_REPORT_NATIVE_TEMPLATES.md`. PASS 5/5, both
gates met: a desktop schedule anchored off its start date posted on
the desktop's day and Since-Last-Run then offered nothing for it; a
composite walked 09-05 / 09-20 / 10-05 and deleted clean; the
migrated Estimated Tax Payment opened in the SX editor without a
crash, container identical to a fresh create; weekend-forward moved
Sat Dec 12 to Mon Dec 14; the dashboard counted the 16 legacy.
Merge word given.

**Addendum ruling, adopted:** the real legacy population is the
production book (8 schedules, all written under 1.4.4). Converting
by instantiation would post early. Every schedule write now sweeps
the whole book — every legacy recipe converts, nothing posted for
the others (`templates_migrated: N`, audit line names it) — and a
no-change `update_scheduled_transaction` is the one-call
conversion. Release note drafted in the CHANGELOG entry.

Post-merge, per the bookkeeper: bounce, then on production one
no-change update converts all 8 with nothing posted; then the
paycheck through the normal path, verifying the VFIFX leg's
quantity 6.1954 on disk — the live cross-commodity replay.

## Round 3 — 2026-09-10, production, SHIP

Filed in the report. Production converted by one no-change update
(8 migrated, nothing posted, transaction count unchanged); every
schedule opens in the SX editor; the paycheck posted through the
migrated path with the VFIFX leg at 6.1954, matching the
pre-migration placeholder exactly. Pre-check established that the
1.2–1.4.4 shape crashes the editor on ANY server-made schedule.

**Follow-up ruling (maintainer, 2026-09-10):** no conversion on
open. Read-only operations keep the book read-only; conversion
happens on the first schedule write, and the dashboard warns —
count, crash, one-call fix — while any legacy recipe remains.
Option (a) declined; option (b) shipped.
