# Bookkeeper Test Plan — enter_statement (v1.4.4 headliner)

Branch: `feat/enter-statement` (ten commits on top of develop).
Bounce the server on this branch before testing. Read the
`enter_statement` docstring fresh after the bounce — it is the
workflow spec and part of what's under validation. The server
orientation also gained a STATEMENT ENTRY section; note whether it
changed what you reached for without being told (adoption test).

## What this is

The four-step statement dance — `create_transactions` dry-run →
judgment → `create_transactions` → `get_unreconciled_splits` +
`reconcile_account` — collapses to two calls around your judgment:

1. `enter_statement(account, statement_date, opening_balance,
   closing_balance, lines, dry_run=true)` — transcribe the
   statement EXACTLY as printed (credit cards too: charges
   positive, balances as amount-owed; the server flips signs from
   the account's type). Get back per-line classification: NEW /
   MATCH / OVERLAP / AMBIGUOUS, with self-contained comparison
   rows for every candidate.
2. Rule each MATCH/AMBIGUOUS row; adapt annotations; confirm.
3. Same call, `dry_run=false` — NEW rows now carry your
   interpreted description/notes and counter-splits (or auto-fill),
   MATCH rows carry `match=<candidate_guid>` claims. One atomic
   save: enter + claim + reconcile against the closing balance, or
   nothing at all.

The statement account's own leg is SYNTHESIZED — never a column.
`raw` (the verbatim statement line) lands on that leg's memo.

## Test sequence

1. **The headline workflow, on a real statement.** Pick a month on
   your test book with a real (or realistic) bank statement.
   Dry-run with only `ref, date, raw, amount` columns — pure
   transcription. Confirm: the summary counts read correctly, the
   candidates table lets you rule each MATCH **without re-reading
   your own input** (both sides + deltas are in the row), and the
   projected tie footer matches your own arithmetic.
2. **Commit and verify.** Add `match` cells from the candidates
   table, interpreted descriptions/notes, counter-splits (or leave
   splitless rows to auto-fill). Commit. Then check with your
   normal tools: `get_reconciliation_status` (everything through
   the statement date reconciled), `get_balance`, and the audit
   log — one ENTER STATEMENT entry rendering creates, claims with
   memo/notes diffs, and the tie line.
3. **A credit-card statement, transcribed native.** Charges
   positive, closing balance as printed ("$412.88 owed"). Zero
   mental sign-flips is the promise — flag ANY place you caught
   yourself negating something.
4. **The self-check gate.** Deliberately drop a line from an
   otherwise-correct transcription. The call must reject naming
   the arithmetic (opening + lines ≠ closing) before any
   classification.
5. **The double-entry guard.** Commit a statement, then re-commit
   the same payload. It should refuse on the untied opening base.
   Then try a single line whose twin is already reconciled, as a
   bare create — it should refuse and point you at the match-row
   no-op form.
6. **The monthly pattern.** A line ~1 month after a same-amount
   recurring transaction (rent shape). It should classify NEW with
   the prior instance listed as evidence (state `y` or `n`), NOT
   OVERLAP — and the note/annotation columns should give you what
   you need to adapt the memo ("July rent" → "August rent").
7. **Batch inheritance.** Run a `create_transactions` dry-run with
   some duplicate bait. New surfaces: `summary` header,
   `review_required` status on candidate-bearing rows,
   `max_confidence` column, self-contained `duplicates`
   comparison rows (`split_match` should read exact/partial/none
   sensibly), `effects` footer. Does `review_required` change how
   you treat those rows vs the old `would_create`?

## What did you route around?

Standing section. Anything you worked around instead of reporting —
a column you wished existed, a classification you second-guessed, a
response you had to re-read, an error message that didn't name the
fix — is an unfiled bug report. Name it even if you solved it.

## Known edges (flagged, not hidden)

- Foreign-currency statements reject with a pointer to the old
  workflow (deferred, spec ruling 3).
- Zero-line statements reject; `reconcile_account` covers the
  no-activity month (needs a ruling if that's not acceptable).
- `force=true` means "land it anyway": untied opening base, tie
  discrepancy (recorded in response + audit), and exact-twin
  creates. Try it once on purpose; confirm the audit entry says
  what happened.
