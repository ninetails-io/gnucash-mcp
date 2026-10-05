# What 1.5 still has open

This file began as the list of everything the 1.5 pre-release
adversarial review found and 1.5's first cut did not fix (bookkeeper
pre-tag triage, 2026-10-01, ruling 6 as amended). Most of it has since
been fixed on `fix/1.5.0-numbers`. What follows is what is left, then
the record of what was closed.

Item numbers refer to
`specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md` (§5 for C-numbers, §7
for side-findings, §8 for the lettered items, §10 for the first fix
branch). Find code by name, not by the review's line numbers.

---

## Open

### Waiting on a decision

- **FC-20 — a 1.4.x server writing to a book 1.5 has converted.**
  1.4.4 reads schedule recipes only from the `splits-json` slot 1.5
  deletes, and its `set_budget_amount` writes magnitudes into a book
  stamped for natural signs, so an income budget lands with the wrong
  sign. Ruled 2026-10-05: the guard is a slot in the server's own
  `gnc-mcp` frame, not a feature flag or schema marker. Not built
  yet: a budget row 1.4.4 wrote carries no mark of its own, so the
  slot can say "an old server has written here since conversion"
  only when that server also left a shape with a fingerprint (a
  `splits-json` recipe, an old invoice link, an old void). What the
  slot should do on that evidence is the open question.
- **Payments booked by 1.2 through 1.4.4 across currencies.** Their
  FX leg has value 0 in the transaction's currency and the gain in
  the quantity alone, which GnuCash's Balance Sheet (no trading
  accounts) reads as no gain. 1.5 books new payments in the payment
  account's currency. Whether a converter restates the old ones is
  undecided; see `specs/v1.5/testing/
  BOOKKEEPER_REPORT_SIDE_FINDING_13.md`, "Not in scope".

### Found on 2026-10-05, not fixed

- **Books that use trading accounts.** The review refuted the claim
  that no trading splits are written (C68) and left three residuals
  unchecked. An engine twin of a cross-currency payment in a
  trading-accounts book now shows:
  - `pay_document` books a realized FX gain or loss split. GnuCash
    books none in such a book: the receivable is relieved at the
    payment's value and the trading accounts carry the difference.
  - The trading splits themselves are piecash's: value and quantity
    at denominator 1 where GnuCash writes the currency's fraction,
    and a NULL reconcile date until the next converting write.
  - piecash finds the trading tree by the English name "Trading".
  - Posting a foreign-currency document in a trading-accounts book
    has no engine twin: the twin's `post` verb gives the engine no
    exchange rate, and the engine refuses.
  Until this is built, a trading-accounts book gets rows desktop
  would not write on a cross-currency payment. Medium to large.

### Not the server's code

- **FC-18 — the bundled demo books are old piecash-format files.**
  Belongs to the sample-book regeneration (`feat/demo-books-legal-
  pass` and the round branches).

### Closed by ruling; do not pick up without a new one

- **C62 — the `Credit Notes` feature flag is not stamped.** Only
  GnuCash before 2.5.0 reads it, and a feature stamp is the riskiest
  row the server can write. Re-confirmed closed 2026-10-05.
- **C11 — the server takes no `gnclock` row.** A call-length lock
  that outlives a crash wedges desktop.

---

## Closed on `fix/1.5.0-numbers`

Each has its test (`tests/test_review_remaining.py` unless noted) and
its CHANGELOG line.

| Commit | Item |
|---|---|
| `4d81469` | MM-12. The budget report converts at monthly closes. |
| `7937d7a` | C46. The discount window counts from the posting date. |
| `9ba2283` | Side-finding 9. A rounded share quantity implies no price. |
| `a5c6f4b` | C20 and the rest of MM-10. Amounts print as GnuCash prints them. |
| `2636f81` | Side-finding 11: void needs `force` on a reconciled split. SS-17: void and unvoid handle notes as `xaccTransVoid` / `xaccTransUnvoid` do. |
| `6b51087` | C26. Template amounts are reduced fractions. |
| `4af3839` | `update_account(hidden=...)`; no tool could hide an account. |
| `33a43a2` | C60. A manual backup of an unchanged book writes nothing. |
| `210210a` | C69, the other half. Postings, payments, credit-note application and schedule instances name the read-only period. |
| `2bb1d4b` | C67. Postings follow "Use Split Action Field for Number"; engine-twinned in `test_parity_posting.py`. |
| `c795b33` | FC-14. Startup reads the `versions` table. |
| `e59f0c1` | C32. `assign_split_to_lot` saves once. |
| `0c18453` | DS-11 (write intent, `INTERRUPTED` line), DS-16 (render cannot replace the result), DS-10 (stderr fallback; test in `test_modules.py`). |
| `cd4ac3c` | C53. The log-dir override keeps the per-book checks; state files are written link-safe. |
| `4e4e85c` | IV-20, IV-21, SEC-15, SEC-17. The Docker image was built and run as uid 10001. |
| `926012f` | The read-only warning on every backend (`test_db_backend.py`). |
| `7e95a93` | Side-finding 12. A four-byte character on a `utf8mb3` MySQL table is refused by name; tested on MariaDB. |
| `fd58b97` | C63. Date ranges are decided on the decoded date. |
| `04e37fe` | Side-finding 13, documented in `samples/README.md`. |
| `adbc7fd` | Review §9 item 6. The engine twin's dump covers the book's own slots. One named difference, `iso_date_feature`. |
| `1e82edb` | C28. One connection per book open. |
| `d56bd6b` | Converted balances round once per account, so a statement's lines add up to its total (found during C20). |
| `8e3f021` | FC-19. A book `gnucash-cli` creates, swept by the server and loaded again by GnuCash (`test_gnucash_created_book.py`). |

Narrower than the finding, on purpose:

- **Side-finding 12** refuses the write; it does not convert a
  desktop-created book's tables to `utf8mb4`.
- **C60** caps copies of one state. A caller that changes the book
  between calls still gets a file per call.
- **IV-21** allows the zero-width joiners Persian, Indic and emoji
  spellings need, and refuses a name that reads like a sibling's.
- **C26** leaves templates already in a book as they are.
- **FC-19** adds one GnuCash-created fixture; most unit fixtures are
  still piecash-made.
