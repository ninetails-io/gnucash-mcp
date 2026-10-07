# Bookkeeper report — document payment surfaces (fix/document-payment-surfaces)

Run: 2026-09-27, copies of the committed demo books; branch at
`c6614cf`, develop at `6a1f368` (#193). Run by the maintainer's Claude
Code session standing in for the remote bookkeeper, every Part B call
through the MCP tool layer over stdio (the class-6 loop's
`mcpcall.py`).

## Verdict: PASS (Part A 3/3, Part B 5/5) — no changes needed during the loop.

## Part A — the oracles did not move
Alex 20 files, Lin Wei 20, Sabine 17: no differing line besides
`book_path`. A first attempt compared develop captures from
2026-09-26 against branch captures from 2026-09-27 and showed 100 /
34 / 64 differing lines, all day counts ("70 days behind" → "71");
recaptured both sides the same day, clean.

## Part B — Alex
1. **List agrees with the unpaid list — PASS.** 8 documents `posted`,
   46 `paid`; the 8 are exactly `get_outstanding_documents`' set.
2. **Payments named — PASS.** 000016: `[{"guid":"9aaa4c7e","date":
   "2026-04-28","amount":"3500.00","from":"Assets:Current
   Assets:Checking Account"}]` — the transaction the class-6 loop had
   to find by searching the customer and matching date and amount.
   000041: `payments: []`.
3. **Foreign currency — PASS.** 000025 EUR 4,500.00 and 000033 CAD
   5,200.00, each one payment in the document's currency, from
   Checking.
4. **Verbose rows — PASS.** Bills 000008 `posted`, 000007 `paid`.
5. **Bounced payment — PASS.** Voided `9aaa4c7e` from the document:
   000016 `posted`, 3,500.00 due, `payments: []`, list row `posted`.
   Unvoided: `paid`, payment listed again.

## The third item — not changed, on GnuCash's evidence
Deleting a transaction leaves the `type='transaction'` price recorded
with it, and GnuCash's reports then value the commodity at it. Desktop
does the same: the register records the price
(`xaccTransRecordPrice (trans, PRICE_SOURCE_SPLIT_REG)` in
`split-register.c`), and `gnc_pricedb_remove_price` is called only
from the price editor, the commodities dialog, the CSV price
importer, and the price database itself — never from the transaction,
split, or register code. Removing the price on delete would be a
behavior desktop doesn't have.

## Routed around
- Nothing.

Signed: Claude (Opus 5.5), Claude Code, for the bookkeeper. The list
says paid when it's paid, and a bounced payment is one call from the
document. Ready for a PR.
