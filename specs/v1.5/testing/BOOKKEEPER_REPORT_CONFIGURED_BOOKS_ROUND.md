# Bookkeeper round — the maintainer's configured books (#194 + fix/document-payment-surfaces)

Run: 2026-09-27, standing in for the remote bookkeeper, on **copies**
of the three books the maintainer's live server is configured with:
the maintainer's own book (the laptop's copy, last written June),
`lin-wei.gnucash`, and a small German-chart test book. Code: develop
at `f14e2fa` (#194 merged) plus this branch. Every Part B call went
through the MCP tool layer over stdio. Personal figures and payees
from the maintainer's book are deliberately left out of this file.

## Verdict: PASS (Part A 3/3, Part B 24/24, Part C clean). One fix landed during the round.

## Part A — the oracles did not move
Capture rig, develop before #194 (`6a1f368`) vs this branch, same
day, fresh copy per side: 17 / 20 / 15 files, no differing line
besides `book_path`. Everything #194 and this branch change is a
guard, a flag, a message, or a new field — no report number moved on
real data.

## Part B
**The maintainer's book — 10/10.** Its two lot-held splits are 401(k)
purchases inside payroll transactions.
- Delete refused, single and batch — **after the fix below**, naming
  both blockers.
- Voiding a lot's only buy leaves a zero balance, which GnuCash reads
  as closed; unvoid restores the shares and the open lot.
- Forced delete of the other paycheck: `reconciled_splits_affected:
  1, lot_splits_affected: 1`; the emptied lot reads open; the audit
  log's `Forced:` line names both overrides.
- One description NULLed on the copy: search, a create dry run (which
  reached the duplicate sweep), and the dashboard all answered.

**lin-wei.gnucash — 7/7.** 29 posted documents, CNY book with USD
receivables.
- The documents listed `posted` are exactly the outstanding set (6).
- Every row's status equals `get_document`'s.
- On all 29, the listed payments sum to `amount_paid`.
- A USD invoice's payment is listed in USD, the lot's currency. The
  bounced-payment workflow — void from the document, owed again, back
  on the outstanding list, unvoid — holds.

**German-chart test book — 7/7.** The same checks on its one invoice,
with SKR03 account paths in every message.

## Fix that landed during the round
**`fix(core): a delete or replace refusal names every blocker`.** On
the maintainer's book each paycheck-with-401(k)-buy is both reconciled
and lot-held. The refusal named only the reconciliation, so force
given for it reopened the lot unannounced. `delete_transaction`
(single and batch) and `replace_splits` now name both. No sample book
has the shape; only real data showed it.

## Part C — GnuCash's own engine
GnuCash 5.15's Balance Sheet on the looped copy of the maintainer's
book and on a pristine copy: both load, 0 warnings, 0 errors. Every
difference is the one forced delete, and they sum exactly (net pay,
the FSA deposit, the 401(k) shares at market, retained earnings for
gross less deductions).

The server's `balance_sheet` matches GnuCash to the cent everywhere
but the 401(k), which differs by under a dollar on the pristine book
too. Not a regression: GnuCash values at the newest price of any kind
(a `transaction` / `user:split-register` trade price), the server at
the newest market price — the documented skip of `type='transaction'`
prices, the same difference the class-6 loop saw on Alex.

## Notes
1. The German test book's invoice reports `total` as `2000.0000`
   beside `amount_paid` `2000.00`. It predates this work (same on
   `6a1f368`).
2. `update_transaction`'s reconciled gate still raises before its lot
   gate. No MCP tool reaches that path.
3. The live server in the maintainer's session was started before
   this work; the maintainer restarted it for the live round below.

Signed: Claude (Opus 5.5), Claude Code, for the bookkeeper.

## Live round — the restarted server, 2026-09-27

The maintainer restarted the live server (11:32, after this branch's
last commit), and the same surfaces were exercised through it on the
real files. Reads only on the maintainer's book and on the committed
Lin Wei sample; the one write was a void/unvoid round trip on the
German-chart test book.

- **Maintainer's book:** lots, `get_lot`, search, and the dashboard
  read correctly.
- **Lin Wei:** the list shows 23 `paid` and 6 `posted`, and the 6 are
  exactly the outstanding list. Job-attached EUR and USD invoices
  list their payment in the document's currency; an unpaid one lists
  `payments: []`.
- **Test book:** the unforced delete of the invoice payment was
  refused, naming the invoice. Void → `posted`, full amount due,
  `payments: []`, on the outstanding list; unvoid → `paid`, payment
  listed. The payment has an FX gain/loss leg, and `from` still names
  the bank account. The audit log records the refusal, the void, and
  the unvoid. Split amounts, actions, and slots after the round trip
  match the pre-round copy.

Two things seen that predate this work: unvoid restores a quantity
with denominator 1 (`1900/1` where it was `190000/100`, same value),
and the UNVOID audit block omitted split actions that the VOID block
shows. The second traced to `_split_to_compact_dict`, which also fed
`replace_splits`' `previous_splits` — the audit log's only record of
the legs it deletes — so their actions were lost from the log. Fixed
on this branch: actions ride along when set, like memos.
