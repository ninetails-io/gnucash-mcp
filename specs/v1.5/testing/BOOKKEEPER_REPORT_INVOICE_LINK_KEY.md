# Bookkeeper report — invoice link key (fix/invoice-link-key)

Run: 2026-09-10, book `samples/link-scratch.gnucash` (byte copy of Alex at HEAD), branch `fix/invoice-link-key` @ `3f5f0a5`, v1.4.4, 87 tools, bounced; backup `pre-link-key-loop` taken. GnuCash 5.12 as the second instrument (Steve at the GUI). Bookkeeper: Abe VII (Cowork).

## Verdict: PASS (4/4) — merge

Baseline before any business write: 108 old-key links (`slots.name='invoice'`, type 5), 0 under `gncInvoice/invoice-guid`; 1,940 transactions, 117 lots; 8 outstanding documents; invoice 000010 paid 3,500.00 / due 0.00; A/R 31,700.00, A/P 450.00, assets 861,481.84 / liabilities 410,768.91 / equity 450,712.93.

## 1. Fresh post carries the real key — PASS
- Invoice 000047 (Emerald, one 3,500 line) posted to `Assets:Accounts Receivable`: txn `abf3a872`, lot `76882773`. Raw: the `gncInvoice` frame on the transaction AND on the lot each has exactly one child, `gncInvoice/invoice-guid` (type 5) = the invoice's GUID `d024c230…`. Lot title `Invoice 000047`; split actions `Invoice`, `Invoice`.
- Bill 000009 (JetBrains, 599) posted to A/P: txn `da6c91c6`, lot `0fdcda7b`. Same frame shape pointing at `a2c5f76b…`. Lot title **`Bill 000009`**; split actions **`Bill`, `Bill`**.

## 2. Desktop finds it — PASS (Steve's eyes)
- A/R register → 2026-09-10 Emerald 3,500 posting → Jump to Invoice: **invoice 000047 opened, $3,500, unpaid.**
- Actions → Lots in This Account (it lives under Actions, not View): **open lot for Invoice 000047 with a $3,500 balance.**
- Business → Customer → Process Payment, Emerald: the dialog opened on `Assets:Receivables:Accounts Receivable EUR` (amount in €) with an empty document list and the "unattached payment" warning; switching Post To to `Assets:Accounts Receivable` listed **000047 at 3,500**. The default-account choice is GnuCash's (last-used / first A/R), not a link problem — but a stranger will read the empty list as "the invoice is missing". Worth one line in the README's desktop section.
- A/P register → JetBrains 599 posting → Jump to Invoice: **bill 000009 opened.**

## 3. Old links renamed on first write — PASS
- Ordering note: the branch renames on EVERY business write, so the sweep fired on step 1's first post, before the plan's step 3. Read accordingly.
- `post_document` 000047 response: `invoice_links_migrated: 108`. Audit: `108 invoice links renamed to GnuCash's key (desktop-navigable, nothing posted)`. Old-key count 108 → 0; real-key count 0 → 112 (108 renamed + 4 new). Transactions 1,940 → 1,942 and lots 117 → 119 — exactly the two probe posts, nothing else.
- Second business write (bill post): no migration field, no audit line.
- Desktop, on an OLD posting (Emerald, Nov 2025) → Jump to Invoice: **invoice 000011, Emerald Analytics, "Nov 2025 Consulting Retainer", $3,500 opened.** The 108 on screen.

## 4. Nothing else moved — PASS
After: outstanding list = the same 8 plus 000047 and bill 000009, amounts and days-past-due unchanged; invoice 000010 still paid 3,500.00 / due 0.00; invoice 000018 still posted 0.00 / 3,500.00; balance sheet identical except A/R +3,500.00 (35,200.00) and A/P +599.00 (1,049.00) from the probes, equity +2,901 = 3,500 revenue − 599 expense. No other line changed.

## Routed around
Nothing. Process Payment's default account needed one dropdown change (GnuCash behavior, noted above). Bug-on-old-code screenshot not taken — Steve went straight to the branch build.

## Cleanup
`link-scratch.gnucash` (+ its `.mcp` dir) can be discarded. Committed samples and production untouched. Production has business documents? — none (household book), so no sweep needed there.

Signed: Abe VII, bookkeeper. Fresh links under GnuCash's key, 108 old ones renamed with nothing posted, desktop jumps both ways. Merge.
