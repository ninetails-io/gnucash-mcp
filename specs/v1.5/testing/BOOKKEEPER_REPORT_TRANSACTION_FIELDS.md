# Bookkeeper report: Num and document link (`feat/transaction-fields`)

*2026-10-01. Against the bookkeeper test plan for this branch. Server
at `7490440` (the four feature commits plus the N-1 fix below) for
scenarios 1–6, and `150fb9c` for the follow-up re-run,
driven over stdio as a client sees it, a fresh server process per
session. Desktop: GnuCash 5.12, worked by the maintainer.*

## Books

- **A**: `~/Projects/abe-bench/txn-fields-off.gnucash`, scenarios 1–4.
- **B**: `~/Projects/abe-bench/txn-fields-on.gnucash`, scenario 5.

Both are copies of the committed `samples/alex-chen-morales.gnucash`,
and each took the 1.5 conversion on its first write. Pre-gate
snapshots sit beside them (`*.pre-gate.gnucash`). Receipts for the
links are in `abe-bench/receipts/`.

**One deviation from the plan.** Scenario 5 says to turn the option
on in desktop. The server session set it in desktop's stored form
instead (a string `t` at `options/Accounts/Use Split Action Field
for Number`). Gate step B1 confirms that desktop shows the option
ticked.

## Scenarios

### 1. Batch entry with Num and link: PASS

Two bills on Checking: BookkeepingCo `ER 2658` with a link to
`receipts/er2658.pdf`, and JetBrains `ER 2659` with none.

- `get_transaction` returns `num` on both and `doc_link` on ER 2658
  only.
- `list_transactions` on Checking ends those two lines with
  `num:ER 2658` and `num:ER 2659`.
- `search_transactions("ER 26", field="num")` finds both;
  `"2659"` finds only JetBrains.

### 2. Duplicate screen: PASS

Each batch was sent as a dry run, then committed.

| Case | Signals | Confidence | Dry run | Commit |
|---|---|---|---|---|
| (a) row 1 unchanged | `DADN` (`num_new` = `num_old` = ER 2658) | HIGH | rejected | rejected, `duplicate_detected` |
| (b) `ER 2660`, else the same | `DADx` | MEDIUM | review_required | created |
| (c) `ÜBERWEISUNG`, `ER 2658`, 10 days later | `-A-N` | MEDIUM | review_required | created |
| (d) no num: an existing Safeway row, plus a new one | `DAD` / none | HIGH / — | rejected / would_create | rejected / created |

For (d), the same batch went through the pre-feature server
(`d42400e`) on a fresh copy. Summary, results and effects are
identical. The duplicates table differs only by the two new empty
columns, `num_new` and `num_old`.

### 3. Statement entry with check numbers: PASS

The statement was generated from Book A's own Checking for April
2025, the month after its last reconciliation (2025-03-31,
18,777.27). It holds all 68 unreconciled April lines as a bank would
print them, plus:

- `CHECK 1041`, against a check entered by batch for Seattle
  Plumbing Co with Num 1041 and a receipt link;
- `CHECK 1039` on the HOA dues, which had no Num;
- a new `CHECK 1042` for the same 250.00 on the same day.

Opening 18,777.27, closing 21,584.81.

- **Dry run.** CHECK 1041: MATCH, HIGH, `-ADN`, `num_old` 1041.
  CHECK 1042: NEW; its candidate is the 1041 check at MEDIUM,
  `-ADx`.
- **Commit, no force.** 69 claimed and 1 created, tied at 21,584.81.
- **Read back.** 1042 has Num `1042`. 1041 keeps its Num and link.
  The HOA claim gained `1039` (step 5). Every claimed split's memo
  holds the statement's raw text.

### 4. Updates: PASS

- Num `R-5521` set on Corner Bakery, which had none.
- ER 2658's link changed to `receipts/er2658-corrected.pdf`.
- ER 2660's Num and link cleared with `clear` = `num,link`.

`get_transaction` before and after each step agrees. `get_audit_log`
shows `Num: (none) → R-5521`, `Link: …er2658.pdf →
…er2658-corrected.pdf`, and `Num: ER 2660 → (none)  Link: … →
(none)`. A row that sets and clears the same field is refused with
`row 1: sets AND clears 'num' — pick one`.

### 5. Option on: PASS

- **Statement.** One created line, CHECK 2001, Emerald City Window
  Washing, 75.00. `get_transaction` shows `"action":"2001"` on the
  Checking split and no transaction-level `num`.
- **Search.** `search_transactions("2001", field="num")` finds it.
- **Batch.** Green Tree Service `T-77` lands on the transaction:
  `get_transaction` shows `"num":"T-77"`.

### 6. Desktop gate

- [x] **Book A opens.** Beyond Since Last Run (cancelled), the only
  popup was desktop's due bills and invoices reminder, which comes
  from the demo's business data.
- [x] **Num column.** It shows 1039, 1041, 1042, ER 2658, ER 2659,
  R-5521, the ÜBERWEISUNG ER 2658, and the cleared row blank.
- [x] **Links.** Opening the linked document from the register works
  on both linked rows (1041, and ER 2658, which opens the corrected
  PDF). The plan's "document-link indicator" does not match desktop
  as the maintainer uses it: he has never seen a marker for linked
  rows, so there is nothing to compare. Both links are stored as
  desktop's `xaccTransSetDocLink` stores them, a string `assoc_uri`
  slot on the transaction, which the engine twin
  (`test_parity_num_link`) compares.
- [x] **Edit in desktop.** Corner Bakery's Num changed to R-5522 in
  the register. The server reads `R-5522` (`get_transaction`, and a
  Num search).
- [x] **Find.** Number contains `ER 26` and `104` return the same
  transactions as `search_transactions`.
- [x] **Option ticked.** Book B's File ▸ Properties ▸ Accounts shows
  "Use Split Action Field for Number" ticked.
- [x] **Option-on Num column.** Checking's register shows 2001 in
  Num. Green Tree Service shows nothing in Num and `T-77` as T-Num in
  double-line view. Expenses:Housing:Maintenance shows no 2001.
- [x] **Find, Number/Action** contains `2001` finds Window Washing.
- [x] **Find, Transaction Number** contains `T-77` finds Green Tree
  Service. The first try returned nothing because it was opened from
  the previous search's results tab, where Find refines the current
  results; run again from the Accounts tab, it found it.

What GnuCash wrote on its own, all desktop's shapes:

- **Book A.** An empty transaction (no description, one zero split
  on Checking, dated 2026-05-31): the register's blank row, saved
  like the blank invoice line in the 1.5 gate. Desktop also stamped
  its feature flags and rewrote the business counters in its own
  shape.
- **Book B.** The options frame was rewritten with the same value.

## Findings

**N-1 (MINOR): fixed in `7490440`.** In a book with the option on,
the statement scan and its `num_old` cell read only the statement
split's action. Batch entry writes `transactions.num` whatever the
option says (desktop's CSV importer does the same), so a check
entered by batch carried no number on a statement. A different-
numbered check for the same amount on the same day was then offered
as its MATCH, with coaching to claim it. The scan now reads the
transaction's Num plus, with the option on, the split's action, as
the batch screen does (`_statement_cand_nums`). Two tests in
`TestStatementScan` fail on `4a98067` and pass now. Re-run on a
fresh option-on copy: CHECK 1041 MATCH (`-ADN`), CHECK 1042 NEW
(`-ADx`), tied. Full suite 3162 passed, 32 skipped.

**N-2 (MINOR, display only): fixed in `150fb9c`.** In an option-on book, compact
list and search lines show no `num:` cell for a number kept on a
split action. The cell reads only `transaction.num`
(`_transaction_to_compact_line`, `book/_base.py`). So
`list_transactions` on Checking shows Window Washing without 2001,
though desktop's Checking register shows 2001 in Num, and the Num
search that found it shows no number either. Nothing is stored
wrong. Now, with the option on, the register form's `num:` is the
filtered account's split action and a `tnum:` cell carries the
transaction's own number. The unfiltered form tags each leg
`#<number>` and labels the transaction's number `tnum:`. With the
option off, lines are unchanged.

## Follow-up: `act` as the register's Num in an option-on book

The maintainer asked whether a per-split Num column is worth adding.
The bookkeeper's view is no. T-Num is transaction-level, and the
split-level number is the split's Action, which batch entry already
writes per split with `act`. In an option-on book the bank leg's
Action *is* that register's Num, so a split `num` column would be a
second name for the same slot.

Checked on Book B (snapshot `txn-fields-on.pre-act.gnucash`) with
two batch rows:

- **Pacific Gutter Co**: `act` 1043 on the Checking leg, no `num`.
- **Puget Plumbing Supply**: `act` 1044 on the Checking leg and
  `num` INV-88.

| Probe | Result |
|---|---|
| Storage | PASS. `get_transaction` shows `"action":"1043"` / `"1044"` on the Checking splits, and `"num":"INV-88"` on the second transaction. |
| Desktop | PASS (maintainer). The Checking register shows 1043 and 1044 in Num; Puget shows INV-88 as T-Num in double-line view. |
| Num search | PASS. `"1043"` finds Pacific Gutter Co (no number on the line: N-2). |
| Statement | PASS. CHECK 1043 and CHECK 1044 are both MATCH, HIGH, `-ADN`; `num_old` reads `1044 / INV-88`. |
| Register-form list | N-2: Pacific shows no number, and Puget shows `num:INV-88` where desktop's register shows 1044. |
| Duplicate screen | **N-3**, below. |

**N-3 (MINOR): fixed in `150fb9c`.** In an option-on book, the batch duplicate
screen takes a row's proposed number only from its `num` cell, never
from `act`. Candidates are read with their split actions, so the
screen is lopsided. A second check, 1045, to the same payee for the
same amount on the same day:

- numbered by `act` 1045: `num_new` empty, `DAD`, **HIGH,
  rejected** as a twin of 1043;
- numbered by `num` 1045: `DADx`, MEDIUM, review_required.

The route that puts the number where desktop's register shows it is
the one the screen cannot see. Now, with the option on, a row's numbers
are its `num` plus its `act` cells (`_batch_row_nums`), as a
candidate's already were, and `_num_signal` compares the two sets.
`num_new` lists them the way `num_old` does.

**Doc line: done in `150fb9c`.** `create_transactions`' `num`
paragraph now says that with the option on, `num` writes the T-Num
and a check number belongs in `act` on the bank leg.

**Re-run at `150fb9c`** on a fresh copy of the pre-follow-up
snapshot:

- The register list shows Puget as `num:1044	tnum:INV-88` and
  Pacific as `num:1043`.
- The Num search shows `Checking Account -120 #1043`.
- Check 1045 numbered by `act` is `DADx`, MEDIUM, review_required.
- The same check 1043 sent again is still rejected.
- The statement for CHECK 1043 and 1044 is unchanged: MATCH, `-ADN`.

Seven new tests (`TestDuplicateScreenAct`,
`TestCompactLinesShowSplitNums`); the five that describe the fixes
fail on `7490440`. Full suite 3169 passed, 32 skipped; no new lint.

## Friction

1. **The docs told me about Num before I needed it** in
   `create_transactions`, `enter_statement`, `update_transactions`,
   `search_transactions` and `list_transactions`. `get_transaction`'s
   description said nothing about `num` or `doc_link`; fixed in
   `150fb9c`.
2. **The duplicates tables added `num_new` and `num_old` mid-row**,
   between `desc_old` and `notes_old`. A caller that reads by header
   is fine; one that reads by position shifts.
3. **The audit log uses two vocabularies for one event.** A
   statement claim logs `num: (empty) → 1039`; an update logs
   `Num: (none) → R-5521`.
4. **No-change updates report `updated`** and log `X → X`. This
   predates the branch and holds for every field.
5. **A dry run in which every MATCH row already carries `match=`**
   still says "69 rows need adjudication".
6. **Num search in an option-on book is noisy.** A substring search
   also hits desktop's own split-action labels: `INV` matched 55
   transactions through `Invoice`. Desktop's Find behaves the same.
7. **My own first statement was wrong.** I generated it from split
   values, which are EUR and CAD on the foreign deposits, so the tie
   failed. The server's figure was right; a bank prints the USD
   quantity.
