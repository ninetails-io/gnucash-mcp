# Second scoped review of the 1.5 close-out (2026-10-06)

Four Opus readers over the whole close-out diff, `43353a7..d42400e`
(`fix/v1.5-adversarial-blockers`: everything 1.5 changed after the
adversarial review's own fixes), one dimension each, every finding
reproduced before it was reported or marked PLAUSIBLE with the reason.
The first scoped review (2026-10-05) covered only #200's diff; this is
the broader pass the maintainer asked for before the sample books are
regenerated.

- `SCOPED_REVIEW_2026-10-06_BUSINESS_MONEY.md` — business-module
  money math (BM-1 to BM-9).
- `SCOPED_REVIEW_2026-10-06_BUSINESS_SHAPES.md` — business-module
  stored shapes and converters (BS-1 to BS-9).
- `SCOPED_REVIEW_2026-10-06_CORE.md` — core, storage, logging,
  scheduling (CS-1 to CS-10).
- `SCOPED_REVIEW_2026-10-06_INPUT.md` — input validation, business
  logic, desktop parity, tool surface (IN-1 to IN-21).

Totals: 4 BLOCKER, 10 SERIOUS, 20 MINOR, 15 NIT. Forty fixed whole and
three in part on `fix/1.5.0-scoped-review`, in two commits: `3748c6a`
(blockers and serious) and `de67dc1` (minor and nit). Six stay open,
each listed under Known limitations in the CHANGELOG. Tests in
`tests/test_review_remaining.py` (`TestSecondReviewBlockers`,
`TestSecondReviewSerious`, `TestSecondReviewMinor`) unless noted.

## Resolution

Verification at `de67dc1`: full suite 3313 passed, 36 skipped;
PostgreSQL gate 90 passed; MariaDB gate 91 passed; every reader's
reproduction script re-run against the fixed code.

| Item | Grade | Disposition |
|---|---|---|
| BM-1 lot-link splits signed by side, not from the lots' balances | BLOCKER (by the letter) | Fixed, `3748c6a`: each link split is the negation of its lot's balance, as `gncOwnerCreateLotLink`. |
| BM-2 any lot-linked split counted as a credit application | SERIOUS | Fixed, `3748c6a`: `_credited_share` counts `trans-txn-type` `L` only. |
| BM-3 vendor report's outstanding read from the payment's value | SERIOUS | Fixed, `3748c6a`: `_bill_amounts_in_default` takes `amount_due` from `_document_settlement`, converted at the posting rate. |
| BM-4 discount leg converts through a quote under `payment_account_amount` | MINOR | Fixed, `de67dc1`: converted at the rate the payment named. |
| BM-5 prepayment excess allowed on a refund | MINOR | Fixed, `de67dc1`: refused on a credit note or a negative document. |
| BM-6 conversions round half-even | MINOR | Fixed, `de67dc1`: half-up, as `dialog-transfer.cpp`. |
| BM-7 `-0.00` on a settled document | NIT | Fixed, `de67dc1`. |
| BM-8 unquantized outstanding; 28-digit rate in the FX memo | NIT | Fixed, `de67dc1` (`_format_rate`). |
| BM-9 fraction-5 currencies quantize to a power of ten | NIT | Open. Known limitations. |
| BS-1 credit-note converter decides from the values | BLOCKER | Fixed, `3748c6a`: keyed on `i_disc_type = ''`, posted or not; desktop's and 1.5's credit notes untouched. |
| BS-2 converter raises on a GnuCash 2.6 compact timestamp | BLOCKER | Fixed, `3748c6a`: `_stored_timestamp_utc`; the reconcile pass skips rows already in the compact form. |
| BS-3 business fingerprints erased without the business module | MINOR | Fixed, `de67dc1`: `_migrate_slot_fillers(keep_business_marks=...)`. |
| BS-4 lot link not merged into the existing split | MINOR | Fixed, `de67dc1`: `_merge_into_link`. |
| BS-5 price converter keys on content | MINOR | Fixed, `de67dc1`: the old server's rows (a source GnuCash never writes) and piecash's `user:split-register` midnight rows only. |
| BS-6 lot-link pass not fingerprinted | NIT | Fixed, `de67dc1`: the payment must be among the old server's. |
| BS-7 mixed-authorship documents get document-level rewrites | NIT | Open; benign in value. Known limitations. |
| BS-8 engine strings written in English | NIT | Open; display only. Known limitations. |
| BS-9 later old-server rows converted without a snapshot | NIT | Open; the response and the dashboard name the rows (FC-20). Known limitations. |
| CS-1 a password holding a quote or a space goes out unmasked | BLOCKER | Fixed, `3748c6a`: `register_secrets_from_url` at startup, `_mask_known_secrets` on every road out. |
| CS-2 pre-upgrade snapshot a hard link to an OLDER state | SERIOUS | Fixed, `3748c6a`: always a fresh copy of the committed state. |
| CS-3 legacy folder claimed by the first same-named book | SERIOUS | Fixed, `3748c6a`: `_legacy_folder_may_belong_to`, by the book GUID in the folder's backups; the audit header's `Book:` path added after the bookkeeper's Q2. |
| CS-4 `delete_account` with references elsewhere | SERIOUS | Fixed, `3748c6a`: `_account_references`; refused by name. |
| CS-5 `sqlite:///` URIs not redacted | MINOR | Fixed, `de67dc1`. |
| CS-6 C27 rollback deletes an instance whose advance was committed | MINOR | Fixed, `de67dc1`: the schedule is re-read; the instance stays and the error says so. |
| CS-7 unescaped URI in `_lock_holder_note` | MINOR | Fixed, `de67dc1`: `quote()`. |
| CS-8 void reason bypasses `_check_text` | MINOR | Fixed, `de67dc1`. |
| CS-9 case-variant or moved path gets a new log folder | MINOR | Open. Known limitations. |
| CS-10 state files written three ways | NIT | Fixed, `de67dc1`: all through `write_private_file`. |
| IN-1 names forge lines on the dashboard and in reports | SERIOUS | Fixed, `3748c6a`: `_check_one_line` on every name-like field; emitters pass names through `_one_line`. |
| IN-2 document ID skips the text gate | SERIOUS | Fixed, `3748c6a`. |
| IN-3 business free text skips the gate | SERIOUS | Fixed, `3748c6a`: `_check_text` on every business writer, `pay_document` included. |
| IN-4 a schedule starting 0001-01-01 breaks every listing | SERIOUS | Fixed, `3748c6a`: `_check_ledger_date`. |
| IN-5 calendar guard and far-date warning only in the batch tool | MINOR | Range half fixed, `3748c6a`: every stored date passes `_check_ledger_date` where it binds. The far-date WARNING stays batch-only; listed. |
| IN-6 `create_prices` drops cells past the header | SERIOUS | Fixed, `3748c6a`: refused, naming the row. |
| IN-7 representability bound checks magnitude and scale separately | MINOR | Fixed on `fix/1.5.0-input-gaps`: the count of the commodity's smallest unit is checked per row (`_unit_count_error`), and a price's digits in `_check_price`. |
| IN-8 BOM stripped by one of four parsers | MINOR | Fixed, `3748c6a`: `_tsv_lines`. |
| IN-9 bill-term day counts unbounded | MINOR | Fixed, `de67dc1`: 36,500 at most. |
| IN-10 account names accept C1 controls and line separators | MINOR | Fixed, `3748c6a`: `_validate_account_name` runs the control-character and one-line rules. |
| IN-11 slot caps exceed GnuCash's columns | MINOR | Fixed, `de67dc1`. |
| IN-12 commodity namespace never gated | MINOR | Fixed, `de67dc1`: no `:`, no case variant of `CURRENCY`/`ISO4217`, no `template`. |
| IN-13 a malformed date sinks the batch without naming the row | MINOR | Fixed, `de67dc1`. |
| IN-14 `pay_document` to a placeholder is piecash's error | MINOR | Fixed, `de67dc1`: `_placeholder_error`. |
| IN-15 uppercase ISO hint missing | NIT | Fixed, `de67dc1`. |
| IN-16 `1E+3` echoed | NIT | Fixed, `de67dc1`: `_plain_decimal`. |
| IN-17 refusals name parameters the tool lacks | NIT | Fixed, `de67dc1`: `party_type`. |
| IN-18 doubled backslash in three path cells | NIT | Fixed, `de67dc1`. |
| IN-19 empty draft lists with `?` | NIT | Fixed, `de67dc1`: `0.00`. |
| IN-20 small boundary inconsistencies | NIT | In part, `de67dc1`: a negative `offset` is refused. A report whose start is after its end still answers an empty total; listed. |
| IN-21 URI-mode errors give the wrong cure | NIT | In part, `3748c6a`: the not-found cure names `GNUCASH_BOOK_URI`. piecash's `create_book` advice on a missing database still surfaces; listed. |
| SR2-B1 `create_billterm` takes a multi-line name (bookkeeper's live pass) | SERIOUS | Fixed, `TestBookkeeperSecondLoop`: bill-term and tax-table names pass `_check_one_line`. |
| SR2-B2 a 300-character document ID is accepted (bookkeeper's live pass) | — | Not a defect: GnuCash's `MAX_ID_LEN` is 2048 on every backend, and the gate is that width; the test plan misstated PostgreSQL's column. |
| Q1 / Q2 (bookkeeper's queries) | — | Plan errors, corrected in the plan; Q2 also added the audit header as adoption evidence. Answers in `testing/BOOKKEEPER_REPORT_SECOND_REVIEW.md`. |
