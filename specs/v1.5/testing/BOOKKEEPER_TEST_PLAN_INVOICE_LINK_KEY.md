# Bookkeeper live loop — invoice link key (fix/invoice-link-key)

Four checks, one bounce, ~15 minutes, GnuCash desktop for two of
them. Branch under test: `fix/invoice-link-key`. What it claims: the
link GnuCash walks from a posting transaction (and its lot) back to
the invoice is now written under GnuCash's key,
`gncInvoice/invoice-guid` (`gnc-engine.h`, pinned by a test). Since
1.2 the child row was named `invoice`, which desktop never reads.
Every business write renames the book's old links on the way,
nothing posted. Lot titles and posting-split actions now carry
GnuCash's type string per document type. Source of the finding:
`DESKTOP_PARITY_BUSINESS.md` §1.

Scratch copy of Alex, server on it, bounced, `create_backup` first.
Fingerprint: after step 1, the posting transaction's `gncInvoice`
frame has a child named `gncInvoice/invoice-guid`; on old code it is
named `invoice`.

1. **Fresh post carries the real key.** Create a customer invoice
   here with one entry, post it. Raw: children of the `gncInvoice`
   frames on the posting transaction and on its lot are each one
   row named `gncInvoice/invoice-guid` with `guid_val` = the
   invoice's GUID. Then a BILL: post it; its lot title reads `Bill
   <id>` and every split's action is `Bill` (old code: `Invoice`
   for both).
2. **Desktop finds it.** Open the copy in GnuCash 5.12. A/R
   register → select the posting transaction → Business menu /
   right-click → "Jump to Invoice": the invoice opens. View Lots on
   A/R: the lot lists with its invoice. Business → Customer →
   Process Payment for that customer: the invoice appears in the
   open-documents list, amount right. Repeat the jump for the bill
   from the A/P register. Report exactly what each dialog showed.
3. **Old links are renamed on the first write.** Alex's inherited
   posted documents were written by old code. Before any business
   write: raw `SELECT COUNT(*) FROM slots WHERE name = 'invoice'
   AND slot_type = 5` is N > 0. Do one business write — pay any
   open invoice here, or `unpost` and re-`post` one — and the
   response carries `invoice_links_migrated: N`, the audit line says
   "N invoice links renamed to GnuCash's key … nothing posted", the
   count is now 0, and no other row changed (transaction count, lot
   count, balances identical). A second write carries no such field.
   Then in GnuCash, "Jump to Invoice" from one of THOSE older
   posting transactions works.
4. **Nothing else moved.** `get_outstanding_documents` and
   `get_document` on a paid and an open document read the same
   before and after step 3. `balance_sheet` identical.

Cleanup: discard the copy.

**Not live-testable:** none — this is entirely a desktop-visible
change, and desktop is the instrument.

**Report:** pass/fail per step; verbatim what "Jump to Invoice" and
Process Payment showed for a server-posted document before the
rename (if you check one on old code first, even better — that is
the bug on screen) and after; the standing routed-around question.

---

## Report — 2026-09-10, Abe VII (Steve at the GUI)

Filed at `BOOKKEEPER_REPORT_INVOICE_LINK_KEY.md`. PASS 4/4 on
3f5f0a5. Fresh posts carry `gncInvoice/invoice-guid` on transaction
and lot; a bill's lot titles `Bill 000009` with `Bill` actions.
Desktop: Jump to Invoice opened the server-posted invoice and bill,
the lot viewer listed the lot, Process Payment listed 000047 once
its Post To account was the USD A/R. Alex's 108 old links renamed
on the first business write (`invoice_links_migrated: 108`, audit
line present, nothing else moved); Jump to Invoice from a Nov 2025
posting then opened invoice 000011 — the 108 on screen. One
editorial note for the README's desktop section: Process Payment
opens on GnuCash's last-used A/R (here the EUR one) with an empty
list, which a stranger will read as "the invoice is missing" until
they switch Post To. Merge word given.
