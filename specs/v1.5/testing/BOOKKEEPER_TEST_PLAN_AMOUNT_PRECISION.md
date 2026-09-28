# Bookkeeper live loop — split amounts stored the way GnuCash does (fix/amount-precision)

Branch under test: `fix/amount-precision` at `1af4209`, one commit on
develop at #195. **It touches storage, so the desktop-open gate
(Part C) is required, not optional.**

What it claims:

- **Every split is stored at GnuCash's precision.** GnuCash's
  `xaccSplitSetValue` / `xaccSplitSetAmount` store a value over the
  transaction currency's fraction and a quantity over the account's
  unit, rounding half up. The server stored whatever precision the
  input had: `12` as `12/1`, and `12.345` dollars as `12345/1000` —
  a sub-cent amount desktop can never hold. Every split write now
  goes through one helper that stores it GnuCash's way.
- **Money typed finer than its currency is refused** (ruling
  2026-09-27), with one sentence everywhere: `… carries finer
  precision than USD allows (2 decimals) — re-check the
  transcription`. Applies to `create_transactions`, `replace_splits`,
  `enter_statement` lines and balances, and `pay_document`.
- **Share quantities round half up** to the account's unit (ruling
  2026-09-27): 6.81415 VTSAX shares store as 6.8142.
- Existing rows are not rewritten; their values are exact and
  GnuCash reads them.

## Setup

- Check out `fix/amount-precision` in the server's checkout and
  restart the server. `get_server_config` should answer; the tools
  are unchanged (no new tool, no new parameter).
- **Writes go to a copy, never the production book.** Copy Alex
  first: `cp samples/alex-chen-morales.gnucash /tmp/alex-precision.gnucash`
  and point the server at the copy (or `switch_book` to a demo book
  and confirm the `Book:` line before any write).

## Part A — the oracles did not move (done by the maintainer's session)

Capture rig, develop (`7b0ae90`) vs `1af4209`, clean worktrees, same
day: Alex 20 files, Lin Wei 20, Sabine 17, no differing line besides
`book_path`. Only new writes change; reads of existing books don't.

## Part B — on the copy of Alex

1. **Sub-cent money refused, batch.** `create_transactions`, one row:
   Checking `-12.345`, Groceries `12.345`. Expected: that row
   `rejected`, reason `Split for 'Assets:Current Assets:Checking
   Account': -12.345 carries finer precision than USD allows
   (2 decimals) — re-check the transcription`. Nothing created
   (`search_transactions` for its description finds nothing).
2. **The ordinary case still works.** Same row with `12.35`: created.
   And with a whole number, `12`: created, and `get_transaction`
   shows the amounts as before.
3. **Shares round.** `create_transactions` with a `qty` column: buy
   VTSAX for `1250.00`, qty `6.81415`. Expected: created;
   `get_transaction` shows the VTSAX quantity `6.8142`. Then qty
   `6.81414`: `6.8141` (half up, not always up).
4. **`replace_splits` refuses sub-cent.** On the transaction from
   step 2, replace with `10.005` / `-10.005`. Expected: refused with
   the same sentence; the transaction unchanged.
5. **Payments refuse sub-cent.** `pay_document` on an open invoice
   (e.g. 000014 on Lin Wei, or any open Alex invoice) with amount
   `100.005`, dry run first, then for real. Expected: both refused,
   `Payment amount: 100.005 carries finer precision than USD allows
   (2 decimals)…`; `get_document` shows the amount due unchanged.
6. **Statements refuse sub-cent.** `enter_statement` dry run with one
   line of `-12.345`. Expected: refused, `line <ref>: amount: -12.345
   carries finer precision…`.
7. **Void and unvoid still round-trip.** Void the step-2 transaction,
   unvoid it. Expected: amounts restored exactly; the audit log shows
   VOID and UNVOID with the split actions on both.
8. **Business writes still post.** On a copy with invoices: post one,
   pay it in full, apply a credit note if one is handy. Expected:
   everything posts and settles as before; `get_document` amounts
   read as before.

## Part C — the desktop-open gate

Open the looped copy in GnuCash desktop (not the production book).
Expected: it opens without warnings; the step-2 and step-3
transactions show `12.35`, `12.00` and 6.8142 shares; edit one amount
in the register, save, close, reopen — clean. Optional, stricter:

    sqlite3 /tmp/alex-precision.gnucash "SELECT s.value_denom, s.quantity_denom FROM splits s JOIN transactions t ON t.guid=s.tx_guid WHERE t.description IN ('<step 2 description>','<step 3 description>');"

Expected: `100` for every value; `100` for dollar quantities and
`10000` for the VTSAX quantity — never `1` or `1000`.

## Routed around

Name every workaround taken, with the tool that forced it.

## Addendum — round 2 (after the round-1 report)

Branch at `507cb9f`; four commits since round 1: `4cfb7c8` (a
currency comes from the ISO table), `7b4a3bf` (`apply_credit_note`
takes `id` / `applies_to_id`), `b1756e0` (harness: `mcpcall.py` honors
`REPO`), `507cb9f` (one rule for a document tool's owner side — a
refactor of `list_`, `get_`, `post_`, `unpost_` and `pay_document`
with no intended change; steps 5, 8 and 13 exercise all five). Same setup: a fresh copy of
committed Alex, in a user-private directory (the log sidecar refuses
`/tmp`). Restart the server on the branch; `mcpcall.py` no longer
needs patching.

The currency commit comes from the round-1 note that the German test
book's invoice read `2000.0000`. Its USD was created at fraction
10000: `create_customer` refused an unseen USD, and the caller fell
back to `create_commodity`, whose share default (10000) landed on a
currency. Existing books keep what they hold; this prevents new ones.

9. **A party adds an unseen ISO currency.** `create_party`
   customer "Zurich Probe AG", currency `CHF`. Expected: created,
   `currency: CHF`. `list_commodities` shows CHF; if you check
   storage, fraction 100.
10. **`create_commodity` for a currency.** `SEK`, namespace
    `CURRENCY`, no fraction: created, `fraction: 100`. `NOK` with
    `fraction: 10000`: refused, `The ISO 4217 fraction for NOK is
    100, and GnuCash stores the currency that way. Omit fraction, or
    pass 100.` `XYZ`: refused, `XYZ is not an ISO 4217 currency
    code…`. A security (`ACME`, namespace `NASDAQ`, no fraction)
    still gets 10000.
11. **`apply_credit_note` speaks its siblings' names.** Create a
    credit note against an open invoice (`create_document`
    `credit_note`, `applies_to_id`), add an entry, post it. Apply
    with the old names first (`credit_note_id` /
    `applies_to_invoice_id`): rejected by the schema, naming both as
    not permitted. Then `id` / `applies_to_id` (plus `party_type`
    `customer`): `applied`. The audit log's `APPLY CREDIT NOTE` line
    names the credit note and `against:` the invoice.
12. **Invoice creation adds an unseen ISO currency.**
    `create_document` invoice for an existing customer with currency
    `JPY` (not in Alex): created; JPY appears with fraction 1.

13. **The five document tools after the owner-side refactor.**
    `list_documents` with `document_type: "bill"` shows only bills,
    and with `party_type: "vendor"` the same rows. Create an invoice,
    add an entry, `post_document` it, then `unpost_document` it:
    `get_document` reads `open` again. (`get_document` and
    `pay_document` are covered by steps 5 and 8.)

Part C still stands: the desktop GUI gate (open, check, edit, save,
reopen) on a looped copy. It can take both rounds' litter at once.
`gnucash-cli` stays an optional oracle, not a requirement.
