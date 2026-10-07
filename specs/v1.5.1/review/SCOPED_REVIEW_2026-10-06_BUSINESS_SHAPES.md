# Scoped review: business and transaction storage shapes (bizshapes)

Range reviewed: `fix/v1.5-adversarial-blockers` 43353a7..d42400e (src.diff / tests.diff). Every finding below was checked against the CURRENT working tree (HEAD 204f4d1) and is still present there. Repro scripts are in this folder. Run each one from the repo root with `uv run pytest -q -s -p no:cacheprovider -W ignore <file>`. They import `tests/conftest.py` fixtures and `tests/fixtures/engine_twin.py`. Nothing under src/ or tests/ was modified.

## Findings

### BS-1 — BLOCKER — CONFIRMED (server-posted and engine-posted)
**The posted-credit-note pass in `_migrate_business_shapes` decides from the values, not from a fingerprint. It negates the lines of desktop's credit notes, and of 1.5's own.**
`src/gnucash_mcp/book/business.py:3475-3489`

- **What the pass does.** For a POSTED credit note, it compares the sign of Σ(stored qty × price) with the sign of the A/R or A/P posting split. When they disagree, it negates every entry (`to_negate = list(rows)`). It never consults `old_entries` (`i_disc_type = ''`). This is the same content-guessing that C9 removed from the unposted branch, kept alive in the posted branch.
- **When the two signs disagree on a legitimate document.** Σ qty×price ignores tax, discounts and card charges, so it can disagree in sign with the posted total whenever those carry the total across zero. Example: a credit note with a +100 untaxed line and a −95 line taxed 10% totals −4.50 and posts A/R +4.50. Its stored sum is −5. That is a mismatch, so the pass flips it.
- **Repro: `test_bs_cn_flip.py`.**
  - `test_server_posted`: the 1.5 server creates and posts the note. The next converting write reports `credit_note_entries_migrated: 1`.
  - `test_engine_posted`: GnuCash's engine posts it (`post` verb). Same flip.
  - In both, entries go from (Refund −1, Restocking +1) to (Refund +1, Restocking −1). `get_document` then shows `total_note: "Posted at -4.50; the entries now compute to 4.50"`.
  - Desktop shows the lines reversed. Unpost and re-post would book +4.50 instead of −4.50.
- **Other triggers (plausible, same code path):**
  - A desktop credit note with a line discount.
  - An employee-voucher credit note fully charged to the company card. Its A/P split is 0, which reads as sign −1.
- **Missed by** `test_converter_false_positives.py`, which only exercises unposted credit notes.
- **Fix.** Gate the posted branch on the fingerprint too: negate only rows in `old_entries`, which is what the old server's rows carry whether posted or not. Keep the sign comparison, if at all, as a sanity check on fingerprinted rows only. Add an engine-posted, mixed-sign, taxed credit note to `TestRowsGnuCashsEngineJustWrote`.

### BS-2 — BLOCKER — CONFIRMED (predates the range; still present; in scope as a listed converter)
**`_migrate_reconcile_conventions` raises on a GnuCash 2.6 compact timestamp, and every converting write on the book is then refused.**
`src/gnucash_mcp/book/_base.py:3556`

- **The cause.** On SQLite, `old` comes back as the raw stored text and goes straight into `datetime.fromisoformat(old)`. GnuCash 2.6's SQLite backend wrote every timespec as `%04d%02d%02d%02d%02d%02d`, confirmed in 2.6.21 `gnc-backend-sql.c` `TIMESPEC_STR_FORMAT`. The file is kept as `gc26_backend.c` here.
- **Why such rows survive.** Reconciled splits are exactly the rows nobody edits again. CLAUDE.md already treats compact rows as real (see `_query_filtered_splits`).
- **Repro: `test_bs_compact_recon.py`.** One split is set to `reconcile_state='y', reconcile_date='20150131235959'`.
  - Results: `convert` → `ValueError: Invalid isoformat string`; `post_invoice` raised; `create_price` raised; `create_budget` raised.
  - Every schedule, budget, business, price, void and reconcile write fails this way.
- **This is a chokepoint bypass.** `_price_row_utc` (`_currency.py:173`) already decodes compact text. The reconcile converter rolled its own parser. The UPDATE's `reconcile_date == as_utc` would also never match a compact row.
- **Fix.** Decode through one shared raw-timestamp helper, e.g. promote `_price_row_utc`. Run the UPDATE and its verification on the raw stored value, not a re-bound datetime. Add a compact row to the converter tests.

### BS-3 — MINOR — CONFIRMED
**Business-module fingerprints are erased when the first converting write runs on a server without the business module.**
`_base.py:3099` (`_upgrade_book_shapes`), `_base.py:3329` (`_migrate_slot_fillers`), `business.py:3430-3446`

- **The dependency.** The `old_payments` and `old_lots` marks are piecash's filler columns on slot rows. `_migrate_slot_fillers` runs in the base class on every converting write and normalizes those columns book-wide. `_migrate_business_shapes` runs only when BusinessMixin is composed.
- **What goes wrong.** Take a server started with `--modules` that excludes business. Its void, price, schedule or budget write wipes the marks first. When business is enabled later, those old-server rows are never converted:
  - the `date-posted` slot on P transactions stays;
  - the stored `is_closed` on document lots stays (desktop's `gncInvoiceIsPaid` reads a stored 0/1 as the answer);
  - the empty lot `notes` slot stays.
- **Repro: `test_bs_module_order.py`.**
  - Control run with the full class: `{'lot_flags_reset': 1, 'payment_slots_pruned': 1, 'lot_notes_pruned': 1}`.
  - Lite class (core, reconciliation, investments), then the full class: `{'slot_fillers_normalized': 2}`, then `{}`. Old rows left as (1, 1, 0).
- **Fix.** Read the slot-filler fingerprint sets in `_upgrade_book_shapes` before any converter, whatever modules are loaded. Either persist them (e.g. defer `_migrate_slot_fillers` until `_migrate_business_shapes` has run on the book), or have the filler pass skip `date-posted` / lot `notes` rows when the business converter is absent.

### BS-4 — MINOR — CONFIRMED (engine twin)
**Extending an existing lot link does not merge sub-splits the way `gncOwnerCreateLotLink` does.**
`business.py:2626-2670` (`_create_lot_link`) and `business.py:9785-9806` (`apply_credit_note`)

- **What desktop does.** After extending an existing TXN_TYPE_LINK transaction, the engine calls `xaccScrubMergeLotSubSplits(to_lot/from_lot, FALSE)` (gncOwner.c:1211-1212). Two splits of one link transaction in the same lot merge into one.
- **What the server does.** It never merges.
- **Repro: `test_bs_linkmerge.py`.** One credit note of 100 is applied to invoices of 60 and 70.
  - Server: the L transaction has 4 splits; the credit-note lot has two (+60, +40).
  - Engine (`autoapply` twice): 3 splits; the credit-note lot has one +100.
  - Balances agree. The row shape and what desktop's register shows do not.
- **Also a chokepoint smell.** `apply_credit_note` carries its own copy of the `gncOwnerCreateLotLink` port instead of calling `_create_lot_link`, so the fix would have to go in twice.
- **Fix.** Port `xaccScrubMergeLotSubSplits` (merge splits of the same non-invoice transaction in the same lot; `merge_splits` sums amount and value and resets reconcile to 'n'). Call it at the end of a single `_create_lot_link`, and route `apply_credit_note` through that function.

### BS-5 — MINOR — CONFIRMED by source (date half) / PLAUSIBLE (value half)
**`_migrate_price_shapes` keys on content and rewrites desktop-written prices.**
`investments.py:454-459`

- **Date half (confirmed by source).**
  - Every price a user typed in the Price Editor in GnuCash 2.6 through 4.0 is stored at LOCAL MIDNIGHT. In `gnc-date-edit.c`, `get_date_internal` calls `gnc_tm_set_day_start` in 2.6.21, 3.0, 3.11 and 4.0, and `gnc_tm_set_day_neutral` from 4.14 on. Copies are kept here as `de_*.c` and `gc26_*.c`.
  - That is exactly the shape the converter treats as "the server's", so it moves every such desktop price to 10:59 UTC.
  - The local day is unchanged, so nothing visible moves. But it is a desktop-row rewrite, at scale, on long-lived books.
- **Value half (plausible).**
  - The 5.12 Finance::Quote path stores the quote string's denominator unreduced: `GncNumeric{std::string}` → `reduce_number_pair` only shrinks big numbers. "178.7000" is stored as 1787000/10000.
  - The converter reduces every such row, and it does so again after each later desktop fetch.
  - Not reproduced headless: Guile returns reduced rationals, so the engine verb cannot write an unreduced value.
- **Fix.** Gate both passes on an old-server mark. Candidates: the sources only the server wrote (`user:market_data`) or a prices-row shape only piecash leaves. Failing that, leave rows that are already in a GnuCash-recognized source alone.

### BS-6 — NIT — CONFIRMED by reading
**The credit-application → lot-link pass is not fingerprinted.**
`business.py:3519`

It selects P transactions by the English description prefix "Credit applied: " plus two A/R or A/P splits in lots. It ignores `old_payments`, which every old-server P transaction carries, unlike its sibling passes. A desktop P transaction with that description would be rewritten. That is unlikely but unguarded. Gate it on `guid in old_payments`.

### BS-7 — NIT — CONFIRMED by reading
**Mixed-authorship documents get document-level rewrites.**
`business.py` (document-dates, `credit_note_flags_completed`)

- `old_documents` includes any document with at least one old-server entry. A desktop-created document to which a 1.4 server added a line gets its document-level fields rewritten by the document-dates and credit-note-flag passes:
  - a pre-4.x local-noon `date_opened` / `date_posted` moves to neutral;
  - a missing `credit-note` flag gets 0.
- Both are benign in value, but they are rows desktop wrote. Consider keying document-level passes on `billto_type = 0` alone, which is the document's own mark.

### BS-8 — NIT — CONFIRMED by reading
**Localized desktop text is written in English.**

Desktop writes `_()` strings in the user's language. The server always writes English:

- "Voided transaction" / "Transaction Voided" (`reconciliation.py:690,704`)
- "Generated from an invoice. Try unposting the invoice."
- "Lot Link"
- "Offset between documents: "
- "Extra to Charge Card"
- the "Invoice", "Bill", "Credit Note" titles and actions

Nothing reads these strings back (grep clean), so this is display-only. In a German book, though, desktop shows English text next to its own.

### BS-9 — NIT — CONFIRMED by reading
**The once-per-book marker leaves later old-server rows converted without a snapshot.**
`backup.py:556-665`

- The marker is written on a book's first converting write even when nothing converts; the snapshot is withdrawn but the marker stays.
- If a 1.4.x server later writes to the book (the FC-20 scenario), the next 1.5 write converts those rows with no snapshot. That includes BS-1-class entry negation, which changes amounts.
- The FC-20 warning fires, but no copy exists.
- Consider re-arming the snapshot whenever `_OLD_SERVER_FINGERPRINTS` counts are non-zero in a marked book.

## Examined and sound

- **Converter idempotence.** Ran `_upgrade_book_shapes` twice on copies of six pre-1.5 sample books (alex-chen-morales, lin-wei, sabine-brenner, and the three `.generated`; script `run_samples.py`). Every second run returned `{}` and changed zero rows. (`book_marked_converted` reads "1.4.4" because the version is not yet bumped, as expected.)
- **Fingerprint soundness across desktop versions.**
  - `billto_type = 0`: 5.12 `gnc-owner-sql.cpp` writes NULL type and guid when there is no bill-to.
  - Slot fillers: 2.6.21 writes a formatted epoch string for non-timespec slots and NULL double; 3.0 and 5.12 write the epoch via the MINTIME check. So no GnuCash version writes `timespec_val NULL` or `double_val 0` on a non-typed slot.
  - NULL `reconcile_date`: never written by 2.6, 3.0 or 5.12, so `_migrate_split_reconcile_dates` touches only piecash rows. Its near-epoch window does not match 2.6 compact epoch text.
- **`_migrate_reconcile_conventions` midnight test** (format crash aside, BS-2). Desktop reconcile dates are day-end in 2.6 through 5.12. CSV import uses neutral; the import matcher and register use `now`. The `include-children` back-fill matches 5.12's start dialog, which always writes it.
- **`_migrate_void_shapes`.** Keys on the invented `void-former-quantity` only.
- **`_write_void_slots` / unvoid.** Matches `xaccTransVoid`, `xaccSplitVoid` and `xaccTransUnvoid` key for key: notes copied when present, numeric former amount/value, read-only reason, and `void-time` in `format_iso8601` form with a space and no zone.
- **`_refuse_posting_record` / `_require_editable`.** Keyed on `invoices.post_txn`. A stale `post_txn` left by an engine unpost names a destroyed transaction, so it cannot misfire.
- **`_billterm_return_child` and refcounts.**
  - Matches `gncBillTermCopy` fields; `invisible = 1`, `parent` set, refcount 0.
  - The parent is decremented only when > 0, as `gncBillTermDecRef`'s guard does.
  - Unpost leaves the document on the child, as the engine does.
  - The converter only raises counts.
- **`_write_due_date_slot`.** slot_type 6 at the neutral time, NULL double, updated in place. `_backfill_due_dates` rewrites only type ≠ 6 rows.
- **`_neutral_time` (C25).** Matches `LDT_from_date_daypart`, including boost's truncating `hours()`.
- **`_card_charges` and the posting splits.** Sign and value agree with the `gncInvoicePostToAccount` card branch (`-bal value` per card line; extra = `to_charge_amount` negated for credit notes); an extra-only voucher still posts its card split.
- **`unpost_invoice`.**
  - Strips GUID slots and frames before the ORM delete.
  - Leaves the empty `gncInvoice` frame.
  - Attaches `invoice->owner` (the Job for a job document), as `gncInvoiceUnpost` does.
  - Rebalances the link lots and destroys empty lots.
- **`_strip_guid_slots` coverage.** All 17 ORM delete sites match the lock regex. There are no bulk `query().delete()` calls. The only `del X[` outside the regex is `del book["counters/…"]`, a non-GUID slot. Party delete strips (owner `invoice-last-posted-account` / `payment-last-account` are GUID slots).
- **`_repair_double_counters` (C61).** Repro `test_bs_counters.py`: the double row is replaced in place by an int64 row in the same `counters` frame and the next ID is correct.
- **Price writers.**
  - `_record_price_engine` matches `record_price` (exact-value early return, the XFER_DLG_VAL/SPLIT_REG exception, the swap uses the commodity SCU).
  - `_record_dialog_price` matches `create_price` / `update_price` (keeps the old source on update, type `transaction`, `round_price` no-dither).
  - `_price_plan` matches `add_price`'s `p->source > old->source` rule.
  - The `Price.validate` replacement is same-source, so leaked multi-source days do not raise.
  - The source list equals 5.12 `source_names`.
  - The Price Editor and Finance::Quote (with a quote date) in 5.12 are neutral, so 5.x rows are untouched by the date pass.
- **Slot registry (`tests/test_slot_shapes.py`).** Citations checked against source:
  - `credit-note` int64
  - `trans-date-due` TIME64 → 6
  - `date-posted` GDate
  - `hidden` / `placeholder` (`set_kvp_boolean_path`: "true" or no slot; piecash's `slot_transform` agrees)
  - `reconcile-info/include-children` (`set_kvp_int64_path`)
  - `gncOwner/owner-type` int64

  All correct.
