# Bookkeeper Test Plan — Batch Entry: Memos, Notes, Quantities

Branch: `feat/batch-memo-notes` (three commits on top of develop).
Bounce the server on this branch before testing. Read the
`create_transactions` docstring fresh after the bounce — it is the
format spec and part of what's under validation.

## What changed

The `create_transactions` TSV **header row now declares the
layout**. Every split column is numbered per split group — `amt1,
acct1, qty1` belong to split 1; `amt2, acct2, qty2` belong to
split 2 — and the groups repeat as wide as a row needs. Four
layouts, all valid:

| Layout | Header split columns | Split group |
|---|---|---|
| Legacy | `amt1, acct1, amt2, acct2…` | `(amount, account)` |
| Memos | `…, memo1, amt2, acct2, memo2…` | `(amount, account, memo)` |
| Quantities | `…, qty1, amt2, acct2, qty2…` | `(amount, account, qty)` |
| Both | `…, memo1, qty1, …` | `(amount, account, memo, qty)` |

- A `notes` column directly after `description` adds
  per-transaction notes to any of the four.
- Field order inside a group follows the header's **first** group —
  `amt, acct, qty, memo` is as valid as `amt, acct, memo, qty`.
- `qty` is the split's amount in **its account's own commodity**
  (shares, EUR); `amt` is always in the book's default currency.
  An empty qty cell means the account is in the default currency.
- Empty memo/qty cells **mid-row** keep their tabs; a row may
  simply END once its last split's amount and account are present
  — trailing optional cells read as empty, no placeholder tabs.

## Test sequence

1. **Regression first**: one batch in the legacy format (2–3 rows,
   no memo/qty/notes in the header). Results TSV, duplicate
   screening, and audit rendering should look exactly as they did
   before this branch.

2. **The split-association check** (the heart of this plan): one
   transaction with THREE splits where every split carries a
   *distinct* memo and the commodity splits carry *distinct*
   quantities — e.g. a paycheck-style row:

       ref	date	description	amt1	acct1	memo1	qty1	amt2	acct2	memo2	qty2	amt3	acct3	memo3	qty3
       1	2026-07-15	Payday	-1000.00	Assets:Checking	net pay		600.00	Assets:401k:VFIFX	pre-tax	9.2551	400.00	Assets:401k:VBTLX	match	5.1008

   Then `get_transaction` and verify **each value landed on its own
   split**: "net pay" on Checking with no quantity, 9.2551 shares
   on VFIFX with "pre-tax", 5.1008 on VBTLX with "match". Nothing
   swapped, nothing shifted by one column. This is the test that
   proves qty1 ≠ qty2 ≠ qty3.

3. **Order variation**: repeat a small version of step 2 with the
   header's first group written `amt, acct, qty, memo` (qty before
   memo). Same verification — the header's order, not a hardcoded
   one, must win.

4. **Notes column**: a batch mixing a filled notes cell and an
   empty one. Notes on the first transaction only.

5. **The real workflow**: re-run a slice of the PDF-statement
   reconcile that motivated this feature — per-line memos (check
   numbers, statement refs) in one call, including at least one
   investment or foreign-currency line with a qty. This is the
   scenario the feature exists for.

6. **Error quality and shorthand** (each behaves as described):
   - A memo/qty batch row ending right after its last account →
     ACCEPTED, trailing memo/qty read as empty (this was an error
     pre-relaxation; verify the split really has no memo/qty).
   - A row ending after an AMOUNT (account missing) → rejected
     naming the missing required field.
   - A split on a share/foreign-commodity account with an EMPTY
     qty cell → that row rejects citing the missing quantity
     (other rows unaffected under `on_error="skip"`).
   - A header mixing in an unrecognized split column (e.g.
     `currency1`) → rejected naming the bad column, not misparsed.

7. **Duplicate screening sanity**: a batch row duplicating an
   existing transaction (same description/amount/date) *with* a
   memo — still caught; memos and quantities don't affect matching.

8. **Audit log**: read back the entries from steps 2–5. Batch
   blocks should show `notes:` lines and per-split memos next to
   the right accounts; step 1's legacy block should be unchanged.

## Expected non-changes

- Transaction currency is always the book default. A transaction
  *denominated* in another currency (EUR invoice paid in EUR terms)
  still goes through `create_transaction` with its `currency`
  parameter — qty covers foreign-commodity *splits*, not
  foreign-currency *transactions*.
- Results/duplicates envelope shape, `ref` correlation, `force`,
  `dry_run`, and `on_error` semantics all unchanged.
- `create_transaction` (singular) untouched — still the tool for
  single complex entries and non-default transaction currencies.
