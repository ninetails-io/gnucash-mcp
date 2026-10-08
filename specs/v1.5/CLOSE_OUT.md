# What the 1.5.0 close-out closed

The record of everything the 1.5 pre-release adversarial review and the
two scoped reviews after it found, and how 1.5.0 closed it. Item numbers
refer to `testing/ADVERSARIAL_REVIEW_1.5.md` (§5 for C-numbers, §7 for
side-findings, §8 for the lettered items, §10 for the first fix branch)
and to the scoped reviews in `review/`. Find code by name, not by the
reviews' line numbers. What 1.5.0 shipped with open is
`specs/v1.5.1/README.md`.

---

## Ruled on 2026-10-05 (bookkeeper close-out decisions)

- **FC-20:** a `gnc-mcp/converted-by` slot on the root account at
  the first converting write; a later pre-1.5 shape with a
  fingerprint in a marked book is warned about in the response and
  on the dashboard for 30 days, never rewritten. Built (`tests/
  test_review_remaining.py::TestFC20...`). A 1.4.x server that writes
  ONLY budget amounts leaves no fingerprint and is not detected; the
  upgrade guide says not to run both.
- **Old cross-currency payments (1.2 to 1.4.4):** left as they are and
  documented. The Known limitations entry says the Balance Sheet gap
  is inherent without trading accounts, names trading accounts as
  GnuCash's answer, and notes 1.5 payments add nothing to it.
- **Trading-accounts books:** a write that would produce trading
  splits is refused until the correct shape is built. Built, in
  `_piecash_shapes._transaction_validate`; the build-out that lifts
  the refusal is on the 1.5.1 list.

## The scoped review of 2026-10-05

Three readers over the merged diff; `review/SCOPED_REVIEW_2026-10-05.md`
holds the resolution table. All fixed on `fix/1.5.0-scoped-review`
(#201) except S-9 (a `?` in the book path; pre-existing), which
`fix/1.5.0-input-gaps` refuses at startup by name. Opening such a path
is on the 1.5.1 list.

## The second scoped review of 2026-10-06

Four readers over the whole close-out diff (`43353a7..d42400e`);
`review/SCOPED_REVIEW_2026-10-06.md` holds the resolution table. Fixed
on `fix/1.5.0-scoped-review` (`3748c6a`, `de67dc1`), and on
`fix/1.5.0-input-gaps`:

- **IN-7:** `_unit_count_error` checks the count of the commodity's
  smallest unit against int64 in `_money_precision_error` and the
  share-quantity branch, and `_check_price` checks a price's digits.
- **IN-5** (the warning half): `_far_date_warning` speaks on update
  (single and batch), post, pay and schedule instantiation, as the
  batch tool did.
- **IN-20:** `_check_report_range` refuses a report whose start is
  after its end; the statement balances name their field; every
  `_to_decimal` refusal carries the "plain digits" cure, and a
  statement line keeps it.
- **IN-21:** `open()` reports a missing database as
  `FileNotFoundError` with the cure, not piecash's `create_book`
  advice.

BM-9, CS-9, BS-7, BS-8 and BS-9 are on the 1.5.1 list.

## The demo books

- **FC-18 — the bundled demo books were old piecash-format files.**
  Closed by #202: no book is committed, and the builders make all
  three from nothing through the server's own write paths (prices
  through `create_prices`, splits through `_new_split`, schedule
  stamps through one shared writer). A converting write on a fresh
  build reports nothing to convert
  (`testing/BOOKKEEPER_REPORT_SAMPLE_SPIN.md`).

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
| `7b8f101` | FC-20 guard, and the trading-accounts refusal, per the close-out rulings. |

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
