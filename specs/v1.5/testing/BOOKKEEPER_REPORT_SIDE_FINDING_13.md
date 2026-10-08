# Bookkeeper report: side-finding 13, the Alex demo's 644.57

*2026-10-01. Claimed by the bookkeeper in the pre-tag triage, to be
chased before the regenerated samples freeze. Book: the committed
`samples/alex-chen-morales.gnucash` (last rebuilt `787ec62`), read
from a copy. GnuCash 5.12, `gnucash-cli --report run`.*

## Verdict

Explained to the cent. Not a server defect in 1.5, and not an
unbalanced book: every transaction balances, and no split in a
same-currency leg has quantity ≠ value. The gap is GnuCash's
Balance Sheet (no trading accounts) revaluing a particular kind of
split at the report-date rate. Half of it goes away when the samples
are regenerated through the current server; half of it does not.

## Reproduction

`gnucash-cli --report run --name "Balance Sheet"` on the committed
book: Total Assets $861,481.84, Total Liabilities & Equity
$862,126.41. The totals differ from the review's (report date), the
gap is the same 644.57.

## Where it comes from

Every income leg booked inside a EUR- or CAD-currency transaction
into a USD income account carries two numbers: its stored USD amount
(the split's quantity, fixed at the rate of the day) and its value
in the transaction's currency. The report counts those legs at
value × the report-date rate. Summing quantity − value × rate over
those legs (latest EUR 1.1435, CAD 0.7131) gives exactly −644.57:

| Legs | Count | Contribution |
|---|---|---|
| LLC Revenue on EUR/CAD invoice posts | 17 | −291.84 |
| Foreign Exchange Gain/Loss on EUR/CAD payments, value 0 in the transaction's currency, nonzero USD quantity | 15 | −352.73 |
| **Total** | 32 | **−644.57** |

The gap therefore moves with the latest EUR and CAD rates. On a
scratch copy, one new CAD price moved it from 644.57 to 1,467.25; a
payment that recorded a later CAD rate moved it to −1,709.65.

## The two kinds of leg

**Value-0 FX legs (−352.73): the server's old payment form.** The
`_compute_fx_gain_loss` docstring calls value 0 with the drift in the
quantity alone "the pre-2026-09-30 form". The current server pays a
foreign invoice from a USD account in a USD transaction, and the FX
split carries a real value. Probe on a scratch copy (CAD invoice
posted at the book's rate, paid with `payment_account_amount`): the
FX split was written −36.90/−36.90 in a USD transaction, so it
contributes nothing to the gap at any rate. Regenerating the samples
through the current server removes this half.

**Revenue legs (−291.84): the posting shape itself.** A CAD invoice
posted to a USD income account is a CAD transaction whose income
split holds USD at the post rate. That is how GnuCash's own invoice
post books it, so a desktop-made book shows the same behavior. This
half survives regeneration, at whatever the report-date rates make
it.

## For the generator top-up

- Regenerate through the current server and the value-0 legs are
  gone. The Balance Sheet will still not foot on Alex or any other
  multi-currency demo; the residue is the revenue legs, and it
  varies with the last EUR/CAD/GBP prices in the book.
- If the demo must foot, that is a choice about the books, not a
  fix: GnuCash's answer is trading accounts. Whether the server
  writes correctly into a trading-accounts book has not been checked.
- Otherwise, one line in `samples/README.md` saying why the
  Balance Sheet on the multi-currency demos doesn't foot, with this
  report as the reference.

## Not in scope, for the maintainer

The 1.5 converter leaves existing value-0 FX legs alone. On the
scratch copy, the converting write left the gap at exactly 644.57.
Any book that took cross-currency payments from 1.2 through 1.4.4
keeps them. Restating them would rewrite money rows in user books;
that is a 1.5.1-or-never call.
