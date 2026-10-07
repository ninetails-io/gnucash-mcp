# Bookkeeper loop for `fix/1.5.0-scoped-review`

What the scoped review's fixes changed that a bookkeeper would see.
Every item has a unit test and was re-run against the reviewers' own
reproduction scripts; none has had a live pass. The findings and
their resolution are in `specs/v1.5.1/review/SCOPED_REVIEW_2026-10-05.md`.

**Server:** `fix/1.5.0-scoped-review` at its tip. Bounce it first.
**Books:** a scratch copy of the multi-currency Alex; a scratch copy
with "Use Trading Accounts" ticked in GnuCash; a scratch copy with
"Use Split Action Field for Number" ticked.

## 1. A trading-accounts book takes schedules, voids and unvoids (S-2, S-7)

On the trading-accounts copy, through the server:

- Create a same-currency scheduled transaction, then change its
  notes. Pass: both succeed. (Before the fix every schedule write was
  refused: "spans more than one commodity (template)".)
- Void a cross-currency transaction GnuCash made (one with trading
  splits), then unvoid it. Pass: both succeed; the amounts are back.
- Enter a USD-to-EUR transfer. Pass: refused, naming the commodity
  with the imbalance and GnuCash desktop as the way in.
- Buy a security. Pass: refused the same way (the ruling covers it).

## 2. A payment's wording is not a number (I-2)

On the Num-on-split-actions copy: post an invoice and pay it through
the server. Then enter, by batch, a second transaction on the payment's
date with the same amount and accounts and a Num of `1234`.

- Pass: rejected as a HIGH duplicate. Before the fix it scored
  `DADx` (MEDIUM) and was created.
- Then the statement half: enter a statement for the bank account
  with a numbered line against that payment. Pass: the line is a
  MATCH, not NEW.

## 3. The dashboard's budget line matches the report (M-1)

On the multi-currency copy with a budget that includes a
foreign-currency expense account and spending in two months at two
rates:

- Pass: `get_book_summary`'s Budget line and
  `get_budget_report(period="ytd")`'s actual are the same number
  (to the unit; the headline rounds to whole currency).

## 4. Text gates on every writer (I-1, I-4)

Try a description with an escape sequence (`\x1b[31m`) through
`update_transactions`, a statement line, a customer name, an
invoice's notes, a budget name, a schedule name, and
`set_account_slot`.

- Pass: each is refused, naming the field and the character; nothing
  is written. Tabs and line breaks in notes still pass.

## 5. Flow report lines add up (M-2)

On a book with three foreign-currency expense categories of equal
amounts at an awkward rate (1.0005):

- Pass: `spending_by_category` lines sum to its TOTAL to the cent, in
  the single table, the monthly table, and the quarterly table, and
  all three tables show the same TOTAL.

## 6. The pre-conversion copy is kept (S-3)

Take a fresh copy of a 1.4.4-written book (the bench has one). Make
the first write of a fresh server process a converting one (a
budget, say), so the auto-backup fires just before it.

- Pass: the backups folder holds a `-manual-pre-1-5-upgrade.gnucash`
  file beside the stage file (the two are one hard link), and the
  marker file's second line names it. Before the fix only the stage
  file existed, and retention would have pruned it.
- Then: on a book with nothing to convert, make any converting
  write. Pass: the marker's second line reads
  `snapshot: none (nothing to convert)` and no labelled file is left.

## 7. Hidden is inherited (I-12, I-13)

Hide a parent account that has a zero-balance child.

- Pass: `list_accounts` marks the parent `[HIDDEN]`; the dashboard's
  reconciliation section lists the child as excluded (hidden, zero
  balance), as it would if the child itself were hidden.

## 8. Lookalike names by script (I-5 to I-7)

- Pass, accepted: a Persian name with and without its ZWNJ as two
  accounts; an emoji family and its three members as two accounts.
- Pass, refused as a lookalike: "Groceries" with a zero-width joiner
  inside it, with a variation selector appended, with a Hangul
  filler appended, and "Grocery Store" with a no-break space; moving
  the ZWJ "Groceries" in beside the real one.

## 9. The desktop gate on what is stored (M-5, S-3)

Open the multi-currency copy in GnuCash 5.12 after the server has
entered transactions and taken the hard-linked snapshot.

- Pass: the book opens with no error; the new transactions show on
  their dates; the snapshot file opens too.
- Not reachable here: the far-zone stamp itself (UTC−11 / UTC+14).
  In every zone from UTC−10 to UTC+13 the stored bytes are unchanged.

## Not in this loop

- **S-1 on Windows.** The process probe is written against the Win32
  API from its documentation; no Windows machine is on the bench.
  What is proven: the old call would have terminated the process,
  and the new probe answers correctly on macOS.
- **S-9**, a `?` in the book path: pre-existing, listed, not fixed.
