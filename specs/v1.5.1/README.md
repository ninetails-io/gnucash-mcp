# v1.5.1 backlog — minor issues deferred out of 1.5

Everything the 1.5 pre-release adversarial review found and 1.5 did
not fix. Each item was closed for 1.5 by a recorded decision
(bookkeeper pre-tag triage, 2026-10-01, ruling 6 as amended) and is
named in the Known limitations section of the 1.5 CHANGELOG entry.
This is a list, not a plan: whether an item lands in 1.5.1, later,
or never is a per-item call.

**Trust the code, not this list.** File and line references come
from the review, written against `43353a7`; the fix branch moved
most of them. Find the code by the name, not the number. Item
numbers refer to `specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md`
(§5 for C-numbers, §7 for side-findings, §8 for the lettered
items, §10 for what was fixed and how).

Sizes are relative to one working session: small, medium, large.

---

## Fixed since this list was written

On `fix/1.5.0-numbers`, each with its test and its CHANGELOG line.

| Commit | Item |
|---|---|
| `4d81469` | MM-12. The budget report converts at monthly closes, as the flow reports do. |
| `7937d7a` | C46. The discount window counts from the posting date, through the billing-term math. |
| `9ba2283` | Side-finding 9. A rounded share quantity implies no price. |
| `a5c6f4b` | C20 and the rest of MM-10. Amounts print the way GnuCash prints them. |

Side-finding 13 (the 644.57) is explained to the cent in
`specs/v1.5/testing/BOOKKEEPER_REPORT_SIDE_FINDING_13.md`. It is not
a server defect.

---

## Worth doing first

These touch a number someone reads, or a book someone could damage.

- **FC-20 — nothing stops a 1.4.x server writing to a book 1.5 has
  converted.** 1.4.4 reads schedule recipes only from the
  `splits-json` slot 1.5 deletes, and its `set_budget_amount` writes
  magnitudes into a book now stamped for natural signs, so an income
  budget lands with the wrong sign. 1.5 cannot stop an old server;
  what it can do is leave a `gnc-mcp/schema` marker a future server
  checks, and say plainly in the upgrade notes not to run both. The
  only item here where a stored amount can end up wrong. Small.
- **C63 — date-range filters compare stored text.** `_query.py`.
  Correct for every row GnuCash 3 or later wrote (verified on 5.12).
  A row in the 2.6-era compact form, or stamped at local midnight,
  can fall on the wrong side of a boundary. Options: detect such
  rows once at open and warn on the dashboard, or compare on a
  normalized form. Medium, and needs a 2.6-written book in hand.

## Desktop parity

- **C67 — business postings ignore "Use Split Action Field for
  Number".** Transaction entry, search and the duplicate screen read
  the option as of 1.5 (`feat/transaction-fields`). What remains is
  `business.py`, the posting and payment writers; GnuCash's
  `gnc_set_num_action` swaps number and action when the option is
  on. Metadata only. A book with the option set is no longer the
  blocker: the transaction-fields gate stored it as desktop does and
  desktop showed it ticked (`specs/v1.5.1/testing/
  BOOKKEEPER_REPORT_TRANSACTION_FIELDS.md`, B1).
- **No tool hides an account.** The slot tools refuse `hidden`
  because desktop reads the flag from the account's slot while the
  server reads the column. The fix is a `hidden` parameter on
  `update_account` that writes both, as `placeholder` does. A
  parameter, not a new tool, but still the maintainer's call. Small.
- **C26 — schedule template amounts are not reduced fractions.**
  `scheduling.py`. The server stores 420000/100 and 0/100 where
  desktop stores 4200/1 and 0/1. Equal values; a parity diff only.
  Small.
- **SS-17 — unvoid recognizes only the English void note.**
  `reconciliation.py`. A transaction voided by a German GnuCash
  keeps "Stornierte Buchung" after unvoid. The void side also skips
  an empty notes slot desktop would copy to `void-former-notes`.
  Restore from `void-former-notes` whatever the current text is.
  Small.
- **C69, the other half.** 1.5 warns when a write lands in the
  book's read-only period. Business postings, payments, and
  scheduled instantiation do not warn, because desktop's own dialogs
  do not check there. Whether the server should refuse instead of
  warn stays a product call.
- **Side-finding 11 — `void_transaction` on a reconciled split
  warns and proceeds.** Desktop asks before it does the same. A
  `force` gate like the one on delete and `replace_splits` would be
  consistent. Small.
- **Side-finding 12 — emoji on a desktop-created MySQL book.**
  Desktop creates `utf8mb3` tables; a four-byte character is refused
  under strict mode or stored as `?` without it. CI's fixture uses
  piecash's `utf8mb4` tables and cannot see it. Refuse the character
  with a clear message on such a book. Small, needs a
  desktop-created MariaDB book.
- **C62 — the `Credit Notes` feature flag is not stamped.** Closed
  as designed; here so nobody rediscovers it. Only GnuCash before
  2.5.0 reads the flag, and a feature stamp is the riskiest row the
  server can write. Do not pick up without a new ruling.
- **C11 — the server takes no `gnclock` row.** Closed as designed,
  for the same reason: a call-length lock that outlives a crash
  wedges desktop. Do not pick up without a new ruling.

## Robustness

- **FC-14 — the startup check reads only the SQLite magic.**
  `server.py`. A 2.6-era `versions` table, or a missing one, passes
  startup and fails every call with a raw piecash error. Check the
  `versions` table at startup and say what is wrong. Small.
- **C28 — a book renamed between commit and response.** Every write
  tool reloads ORM objects after the commit; if the file has moved,
  SQLite creates an empty file at the old path and the committed
  write is reported as failed. Capture what the response needs
  before the commit. Medium, every write path.
- **C32 — `assign_split_to_lot` can commit and then raise.**
  `investments.py`. Needs a root account with no commodity. If the
  assignment zeroes the lot and the next step fails, the lot is
  saved with its closed flag reading open. Small.
- **DS-11 — the audit line is written after the commit.** A server
  killed in between leaves a committed write with no line.
  `logging_config.py`. A write-ahead "intent" line would close it.
  Medium.
- **DS-16 — a failure rendering the audit entry reports a committed
  write as an error.** Speculative: 30 of 68 formatters raised under
  off-type fuzzing, no natural input found. Wrap the render so it
  can never replace the result. Small.
- **DS-10 — a `switch_book` that fails twice over** can leave the
  session with no audit handler. `server.py`, the bare `except`
  around the fallback. Small.
- **C60, the cap — manual backups are unlimited.** A rate limit or
  a count cap on `create_backup` is a product call.

## Security and privacy

- **C53 — a `GNUCASH_LOG_DIR` override skips the symlink and owner
  checks.** `logging_config.py`, `backup.py`. Apply the same checks
  to `{LOG_DIR}/{book}.mcp`, create files exclusively without
  following links. Small.
- **SEC-17 — the Docker image runs as root.** Add a `USER`. Small,
  but test the mounted-book permissions.
- **SEC-15, the remainder — audit files written before 1.5 keep
  mode 0644.** New files are 0600. Tighten existing ones on first
  open. Small.
- **IV-20, the remainder — control characters other than NUL** are
  accepted in descriptions, memos, and notes. Small.
- **IV-21, the remainder — zero-width and right-to-left characters**
  in account names: three visually identical "Groceries". Small.

## Test infrastructure

- **FC-19 — fixtures are piecash-created books.** Schemas differ
  from GnuCash's own in small ways (`splits.tx_guid` nullability,
  the `gnclock` column spelling, MySQL charset and collation). The
  engine twins, the converter's false-positive test, and the GUI
  gate now run against rows GnuCash wrote; a GnuCash-CREATED fixture
  book (made headless by `gnucash-cli`, never committed as a binary)
  would close the rest. Medium.
- **FC-18 — the bundled demo books are old piecash-format files.**
  Being addressed by the sample-book generation work ahead of the
  release.
- **The read-only-period warning has no database-backend test.** Its
  one query is a plain SELECT; a case in `test_db_backend.py` would
  make that a fact. Small.
