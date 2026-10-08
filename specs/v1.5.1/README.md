# What 1.5.1 has open

What 1.5.0 shipped with open. The record of what the 1.5.0 close-out
closed, and the reviews behind it, is `specs/v1.5/CLOSE_OUT.md`. Item
numbers refer to `specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md` and the
scoped reviews in `specs/v1.5/review/`. Find code by name, not by the
reviews' line numbers.

---

## Open

### Trading-accounts books

1.5.0 refuses a write that would produce trading splits
(`_piecash_shapes._transaction_validate`). What remains to build, for
the refusal to lift:

- trading splits at the currency's fraction, the epoch reconcile
  date, and the tree found by type (`xaccScrubUtilityGetOrMakeAccount`
  under the top-level TRADING account), not by the English name;
- `pay_document` in such a book relieves the receivable at the
  payment's value and books no realized FX split;
- an engine twin for posting a foreign-currency document, which
  needs the twin's `post` verb to supply the exchange rate
  (`gncInvoiceAddPrice`), as the Post dialog does.

The refusal covers stock and fund purchases too, the common case (the
bookkeeper's step 11 addendum put a VTSAX buy on record), and the
CHANGELOG says so in plain words.

### S-9, the second half

A book path containing `?` is refused at startup by name
(`_book_format_error`). Opening it means building the file book's
SQLite URI quoted, or through a `creator=` callable, in
`source_open_kwargs`, which changes how every file book opens.

### From the second scoped review (2026-10-06)

Each is listed under Known limitations in the CHANGELOG.

- **BM-9** — `_commodity_quantum` is a power of ten; a fraction-5
  currency (MGA) can receive a converted amount off its grid.
  `_entry_math.round_half_up` already handles it; the conversion
  sites should round the same way.
- **CS-9** — `_log_dir_identity` resolves the path without folding
  case; a case-variant or moved path gets a new folder. Identify by
  `(st_dev, st_ino)` with the path kept for display.
- **BS-7** — document-level converter passes key on any old-server
  line; key them on `billto_type = 0` alone.
- **BS-8** — engine strings ("Lot Link", "Voided transaction", the
  document titles) are written in English; desktop writes `_()`.
- **BS-9** — the once-per-book marker leaves a 1.4.x server's later
  rows converted without a fresh snapshot (FC-20 names them).
  Re-arm the snapshot when a marked book shows a fingerprint.

---

## Closed by ruling; do not pick up without a new one

- **C62 — the `Credit Notes` feature flag is not stamped.** Only
  GnuCash before 2.5.0 reads it, and a feature stamp is the riskiest
  row the server can write. Re-confirmed closed 2026-10-05.
- **C11 — the server takes no `gnclock` row.** A call-length lock
  that outlives a crash wedges desktop.
