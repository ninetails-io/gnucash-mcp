# Changelog

Entries are terse by design: what changed, one line each, PR numbers where they exist. Rationale lives in the PRs, the specs, and the bookkeeper rulings recorded under `specs/`.

## Unreleased

### Added
- **Database-backed books** — `GNUCASH_BOOK_URI` / `--book-uri` serve a book GnuCash keeps in PostgreSQL or MySQL/MariaDB instead of a SQLite file; every tool works unchanged. Drivers ship as extras: `pip install "gnucash-mcp[postgres]"` or `"gnucash-mcp[mysql]"`. PostgreSQL contributed by @vchatela (#175, requested in #174); MySQL/MariaDB in #181.
- Both dialects are proven against real servers: the database test class runs once per dialect, and CI carries a `postgres:16` job and a `mariadb:11` job beside the three-Python suite (#175, #181).
- `_gnc_bool` — one coercion for GnuCash's INTEGER flag columns, contract-locked; a Python bool never reaches storage on any backend (#175).
- Backup refusals and `get_server_config` name the dialect's own dump tool (`pg_dump`, `mysqldump` / `mariadb-dump`); `docs/RESTORE_FROM_BACKUP.md` covers both (#181).

### Changed
- **Every price read goes through `_find_prices`** (#186): `list_commodities` and `get_latest_price` now show the same rate the reports use on a same-day tie (manual quote over feed), on every backend, and `_rates_as_of` no longer loads the price table twice. The whole-book preload behind `list_transactions`, `search_transactions`, and `get_book_summary` covers slot values and skips the unused account pass, so every read tool issues a fixed number of queries whatever the book's size; a notes search on a 120-transaction book went from 129 statements to 9. Builds on @bhbrunt's #182.
- URI mode is single-book by construction (`switch_book`, demo books, and the write disarm follow the path list); setting both `GNUCASH_BOOK_PATH` and `GNUCASH_BOOK_URI` is a startup error. `GNUCASH_LOG_DIR` is required in URI mode. Connection URIs are password-masked everywhere a book is named (#175).
- A database book announces a server (re)start like a file book, and its audit header names the masked URI (#181).
- `open()` disposes piecash's engine after close, so a database book holds no server connection between tool calls (#175).
- GUID-prefix lookups run through one SQLAlchemy engine, one query text for every dialect (#175).
- `--modules=bookkeeper` is everything except business; `--modules=business` is one module, the retired `freelancer` / `business_complete` names still accepted (#173).
- `get_document` carries `status`, `amount_paid`, and `amount_due`; `pay_document` reports the per-call `payment` beside cumulative `total_paid`, both from one settlement chokepoint (#173).
- `party_type` is the one name on every business tool; job tools answer it as they accept it. Amounts leave at the commodity's quantum; audit CREATE lines render the counterparty as `Name (id)` (#173).
- A credit note at zero balance reports `status: applied` (#173).
- `close_lot` closes a zero-balance lot, GnuCash's definition; a lot that still holds shares is refused with the balance named (#181).

### Upgrading from 1.2–1.4.4
Schedule recipes, invoice links, and budget signs are now stored the way GnuCash desktop stores them, so desktop and this server read each other's work. **One write converts an existing book:** the first schedule, budget, or business write after upgrading converts everything at once, posts nothing, and reports what it converted in the response and the audit log; a no-change `update_scheduled_transaction` on any schedule is the deliberate one-call version. Do it before your next GnuCash desktop session — until then, opening a server-made schedule in desktop's Scheduled Transaction Editor crashes GnuCash 5.12, and the dashboard says so while any remain. Reads never write.

### Pre-release review
- An adversarial review of the 1.5 candidate at `43353a7` — seven hostile passes (money math, storage shape, data safety, input validation, business logic, security, file format), every serious finding cross-examined blind by a second reviewer — returned **NO-SHIP**. Seven blockers: invoice tax rounding and entry discounts diverge from desktop; an invoice's posting transaction can be voided or rewritten; three ORM deletes bypass `_strip_guid_slots`; a database password is echoed on a mistyped URI. Findings, verdicts, and the falsifying test for each are in `specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md`. The fixes land before the version bump.

### Dashboard accuracy
`get_book_summary` is the first thing an assistant reads, so its warnings set the session's agenda. An adversarial review found warnings that were wrong, right only for US conventions, or tuned to fire on healthy books; each finding is one commit on `fix/dashboard-sensitivity`, ruled and live-tested (`specs/v1.5/DASHBOARD_ACCURACY_SPEC.md`, bookkeeper report under `specs/v1.5/testing/`).
- Overdue documents read the settlement chokepoint: an overpaid invoice is open, never overdue, and amounts render at the currency's quantum (`USD 0.75`, not `USD 0`). The dashboard's overdue count equals `get_outstanding_documents`' rows past due.
- **Due dates follow GnuCash's billterm math**, ported from `gncBillTerm.c`: proximo terms and due-on-receipt work; the 30-day default is gone. A document with no terms is due on its posting date, as in desktop — on an existing book, no-terms invoices may show as overdue for the first time. `post_document` writes `trans-date-due` on every post in the row shape desktop reads (the server had written a shape desktop could not decode since 1.2; the first business write after upgrading rewrites those rows), and a `due_date` that disagrees with the terms is refused naming both dates. `no_terms` left the outstanding-documents response.
- "Last entry" stops at today; future-dated entries are counted beside it, and one more than a year ahead warns. The month-to-date net and budget actuals include only entries through today.
- Monthly net converts each month at its own close, so it equals `income_by_source` minus `spending_by_category` for that month.
- A `Stale price` line names the rate valuation actually uses (inverse and chained rates included, a chain dated by its oldest leg), and a currency is checked only while something holds it or used it in the last 90 days.
- New lines: `Overdrawn:` per negative cash account; `Low cash:` when a week's scheduled bills exceed the balance; unbalanced transactions as an integrity defect.
- Reconciliation: "behind" is measured from the last reconcile, with older unreconciled splits noted as outstanding items; a never-reconciled, empty, idle account is dormant; hidden empty accounts are excluded; the threshold follows the statement cycle desktop records (`reconcile-info`), which `reconcile_account` and `enter_statement` now write the way desktop does.
- Budget pace follows the budget's own per-period targets (`spent / expected by today`); runway burn is cash leaving a cash account, with card balances shown beside it.
- `reconcile_account` accepts `closing_balance`, the name `enter_statement` uses; a statement dated after today goes through with a warning in both tools.
- `update_scheduled_transaction` takes `start_date`, doing what desktop's Scheduled Transaction Editor does: the recurrence rows and the schedule's start move together, so the new date is the phase for every future occurrence.
- **Business rows are GnuCash's own, row for row.** A parity twin — the same credit-note flow on two identical books, one in GnuCash desktop and one through the server, diffed row by row — found seven differences and they are all gone: a credit note's entry quantities are stored negated as `gncEntrySetDocQuantity` does (the server's positive quantities showed a server credit note in desktop as a charge; reads negate back, so the tool surface is unchanged); a credit note is applied with the `Lot Link` transaction `gncOwnerCreateLotLink` writes; document dates sit at GnuCash's neutral time and entry dates at the entry ledger's; entries carry `gncEntryCreate`'s defaults; document lots leave their closed flag for GnuCash to compute; a payment's memo lands on both legs; no stray slots. The first converting write after upgrading brings existing rows to the same shape and reports what moved. The twin is now a test: `tests/test_parity_credit_note.py` reproduces the server side and expects the desktop dump verbatim.
- An unreconciled split's `reconcile_date` is stored as the epoch, as desktop stores it, instead of NULL (or, on a posted document's receivable split, the epoch shifted to local midnight); a reconciled split's is the statement date's local day end rather than midnight; the account's `reconcile-info` frame carries `include-children` as desktop's Finish writes it. The first converting write brings existing rows along. Found by two one-transaction twins — the only columns a plain transaction and a reconcile differed on.
- A billterm's `refcount` is the number of documents, customers, and vendors referencing it, maintained on assignment and delete as desktop does; every document that is not a credit note carries `credit-note` 0, as desktop writes it. The first converting write recounts and completes existing rows. Found by a one-invoice twin; the ID desktop chose (`000047`, continuing the six-digit counter past a hand-named `Parity01`) matched the server's rule exactly, and no private child copy of the term is made.
- A price's `source` must be one GnuCash's price editor recognizes (`user:price`, `user:price-editor`, `Finance::Quote`, …); the generators' `user:market_data` showed every sample price as **Invalid** in desktop, and is now written and converted as `Finance::Quote`. Price dates are stored at GnuCash's neutral time rather than local midnight, and values reduced (178.70 is `1787/10`, as the editor stores it). The default price type is `last`, the editor's default, rather than `nav`. The first price write after upgrading converts existing rows. Found by a one-price twin, reported by the maintainer from the Price Editor.
- **Which price is current follows GnuCash's own order**: the later stored timestamp, then the smaller GUID (`compare_prices_by_date`), on every surface — valuation, posting FX, the latest quote. The source-rank tie-break from 1.4.4 (manual quote over other user sources over feeds) is retired: a twin showed desktop valuing a holding by a row its own editor had stamped at wall-clock time while the server ranked the day's neutral-time quote above it. Parity means agreeing on the price. The note a price write carries when another same-day row wins now names the row desktop will use.
- **A cross-currency transaction's implied rate is a price.** GnuCash's lookups never filter on price type, so the `type='transaction'` row every cross-currency transaction leaves behind values holdings in desktop; the server skipped those rows since issue #94 and now counts them everywhere it values, posts, or chains a rate (maintainer ruling, 2026-09-29). A holding priced only by its own transactions is therefore valued rather than carried at cost. Staleness keys on the date of the rate valuation actually used, whatever its source, against one window: a fresh implied rate warns of nothing, an old one reads `valued at the rate of its last transaction, 60 days ago` so the cure is in the sentence (bookkeeper ruling).
- **A cross-currency transaction leaves the price desktop leaves.** A twin (USD 100 to EUR 90, typed in desktop) stored one price, EUR/USD at exactly `10/9`, and an empty split action; the server, through piecash, stored a six-decimal rate in the split's own direction at local midnight and stamped `Buy` into the action. The implied price is now written by ports of GnuCash's two writers: `record_price` for stock and fund accounts (rounded half-up to the currency's fraction × 10000, `user:split-register`) and the exchange dialog's `create_price` for every other foreign-currency account (exact ratio, stored against the default currency, `user:xfer-dialog`), each with GnuCash's same-day rule: a preferred source stays, otherwise the day's row is updated in place. A price is recorded when a split is entered or its amounts change, no longer on any edit. `Buy`/`Sell` is stamped only on a split in a stock or fund account, as desktop's stock register does, not on a currency transfer. The first converting write restates existing rows where the originating split can be identified.
- A pair's prices are one list whichever way each row is stored, as in GnuCash: the most current of the direct and inverse rows values the holding. The server preferred a direct row over a newer inverse one.
- The rate the server applies to a new cross-currency posting or payment still comes from quotes somebody entered or fetched, not from a transaction's implied rate: a posting that priced itself off the last posting's echo would keep that rate fresh forever and the staleness guard would never fire.
- Slot rows carry GnuCash's values in the columns their type does not use (`double_val` NULL, `timespec_val` at the epoch); piecash wrote 0.0 and NULL on every slot, including the `date-posted` on each transaction. A new account carries the empty `balance-limit` frame desktop's account dialog leaves. The first converting write brings existing slot rows along.
- **A cross-currency payment is booked in the transfer account's currency, as GnuCash books it.** A EUR invoice paid from a USD account is now a USD transaction: the bank split in dollars, the receivable split carrying the dollar value and the euro quantity (`gncOwnerCreatePaymentLotSecs`). The server had booked it in the invoice's currency. Realized FX is no longer a zero-value split: the receivable is relieved at the amount it was carried at and the gain or loss is an ordinary balanced amount beside it. With no rate change the transaction is row for row what desktop writes. The day's price is the rate actually paid. Payments already in a book are left as they are; both forms are valid to desktop and to the server.
- **A payment desktop processed across currencies now reads correctly.** A document's balance summed split values, which mixes currencies once a payment is in the transfer account's currency: a EUR 900 invoice desktop had settled with USD 1,000 showed as overpaid by 100. The balance, `amount_paid`, `amount_due`, and the `payments` list are now read in the document's currency from each split.
- Valuation rates display at no more than six decimals (`9900 EUR @ 1.1401`, not a 28-digit inverse).
- `list_commodities` no longer shows a "latest price" on the book's default currency; every other line is priced in it, and `get_latest_price` answers null there (bookkeeper ruling). Batch-entry refusals print the whole column contract from the first error; the stale-rate refusal's suggested `create_price` call now validates as written; day counts of one read `1 day`.
- **Voids are GnuCash's own.** `void_transaction` now writes what `xaccTransVoid` writes: numeric `void-former-amount` / `void-former-value` on each split, `void-time` in GnuCash's date form, the notes stashed, the transaction read-only. Desktop had never recognized a server void (the originals were strings under one key it does not know), so its Unvoid was unavailable or would have restored zeros. `unvoid_transaction` reads desktop's voids; the first void, unvoid, schedule, budget, or business write after upgrading rewrites every old void in place, keeping the amounts it held. A contract test now checks every slot the server writes against the GnuCash source that defines it (`tests/test_slot_shapes.py`).

### Fixed
- **Deleting one thing no longer reaches another's metadata.** piecash deletes every slot of whatever a GUID-valued slot points at when the row carrying it is deleted through the ORM, and four paths did that without stripping first. `unpost_document` wiped every slot on the invoice itself, including the document link GnuCash desktop attaches; `replace_splits` deleted the slots of a split in another transaction (and raised `CircularDependencyError` on the capital-gains pair desktop writes); `delete_account` and `delete_account_slot` deleted the notes, colour, and reconcile state of any account the deleted one linked to (OFX income account, lot gains account, import map). All four strip first now, deleting a customer, vendor, employee, or document no longer leaves a frame's children behind, and a source-scanning test fails on any ORM delete that skips the step. Pre-release review, `specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md` (C4b, C5, C7, C8).
- `create_price` and `create_prices` refuse a price that is zero or negative (a negative EUR rate valued 1,000 EUR at −1,100 on the balance sheet), a commodity priced in itself, and absurd magnitudes. The stale-rate refusal's suggested `create_price` call now names both currencies; followed as written it used to record USD-in-USD on a USD book and the retry failed the same way. Review items C13, C35.
- **A database password no longer appears in errors or logs.** The server masked the password wherever it named the book, but an error raised while opening a database book quotes the whole connection string, and that text went out as written: a mistyped database name returned `Database 'postgresql://user:PASSWORD@host/db' does not exist` to the assistant, wrote it to the audit log, and printed it on stderr (which Claude Desktop keeps on disk). One scrubber now covers every way text leaves the server (tool errors, audit and debug files, the error log, startup errors, dashboard failure lines). It also masks a password given as a query parameter (`?password=`, `sslpassword=`), which the book's displayed name used to show, and a password containing `@`. A connection string that fails to parse is no longer echoed back in the error. `--help` and the README now say to keep a password out of `--book-uri`, where the process list shows it. Review items C16a, C16b, C52a, C52b, C59.
- **A document's posting transaction is read-only, everywhere.** `void_transaction` voided an invoice's posting transaction, and `replace_splits` rewrote it under `force=true` (its own error suggested the flag); either way the invoice then read `paid` in full with an empty payments list and left the outstanding list. `update_transactions` could re-date it, leaving the document and the ledger with two posting dates. All of them refuse now, as `delete_transaction` already did and as GnuCash does, with no `force` override: unpost the document, change it, post it again. Voiding a payment is unaffected. `unvoid_transaction` on a posting that an earlier version voided puts its read-only marker back. Review items C4a, C4b, C4c.
- **Invoices, bills, and credit notes total the way GnuCash totals them.** The server computed tax line by line, rounded half-to-even, on an already-rounded line value, and on tax-included lines forced a spare cent onto the largest rate so the total matched the summed prices. GnuCash rounds each line's net half-up, adds up each tax account's unrounded tax across the whole document, and rounds that once. The two disagreed by a cent on a third to nearly half of multi-line taxed invoices: one line of 10.00 at 8.25% posted 10.82 where desktop shows 10.83; three 10.00 tax-included lines at 7% posted 30.00 where desktop shows 30.01. The arithmetic is now a port of GnuCash's own (`gncEntryComputeValueInt`, `gncInvoiceGetNetAndTaxesInternal`), so a draft reads, and posts, the total desktop's invoice window shows. **A line discount entered in GnuCash desktop is honored** (percent or value; before tax, at the same time, or after tax): a discounted invoice drafted in desktop used to post at full price here. `get_document` shows `discount` on such a line. Documents already posted are untouched, since a posted total is read from the posting. Checked against GnuCash itself, not a reading of its source: a headless oracle drafts random documents through the server and has `gnucash-cli` report the engine's total, subtotal, and tax for each. The old math differed on 383 of 600; the port differs on none of 1,540. Three hundred of GnuCash 5.12's answers are recorded as a test that runs in CI (`tests/test_entry_math_desktop.py`). Review items C1, C2.
- **A posted document's total is what it was posted at.** The total was recomputed from the entries on every read, so anything that changed the computation after posting moved the "total" off the booking and showed the gap as money paid: a tax table edited from 5% to 10% turned an invoice posted and paid at 105 into `total 110, amount_paid 110`, and an invoice GnuCash desktop had posted with a line discount read as part-paid with an empty payments list. `total`, `amount_paid`, and `amount_due` now come from the posting on every surface (`get_document`, `list_documents`, `get_outstanding_documents`, `pay_document`'s `total_paid`, `get_job_report`), so `amount_paid` always equals the sum of `payments`. `get_document` adds `total_note` when the entries no longer add up to the posted amount. A draft still totals its entries. Review items C10, BL-14.
- **The account slot tools leave GnuCash's own keys alone.** `set_account_slot` and `delete_account_slot` refuse GnuCash's account frames (`reconcile-info`, `lot-mgmt`, `ofx`, `import-map`, `balance-limit`, `tax-US`, `associated-account`, `hbci`), the server's `gnc-mcp` frame, and any slot that holds something other than a plain string. `placeholder` and `hidden` are refused too: desktop reads them from the slot and this server from the account record, so a slot written alone made the two disagree about the account (use `update_account(placeholder=…)`). A string written over `reconcile-info` used to make every later reconcile on that account fail.
- **A dashboard check that fails says so.** `get_book_summary`'s collectors skipped a failed check silently, so a check that fell over read as a clean book (the way overdue-invoice warnings once went dark on Python 3.10). Each failure now renders one warning line naming the check and the reason inline — `Low-cash check failed: ValueError: …` — first line only, URIs masked, one line per check with a skipped count for per-item loops. On PostgreSQL the same chokepoint clears the aborted transaction a failed statement leaves behind, so one bad check no longer blanks every warning after it. Its first catch: `get_backup_health` had been raising on every database book's dashboard call (a file-path assumption), silently; it now reports the empty shape a book with no backup chain deserves. Spec: `specs/v1.5/DASHBOARD_HONEST_FAILURE_SPEC.md`.
- **Tax-bearing drafts can be deleted on PostgreSQL and MySQL.** `delete_document`'s taxtable refcount decrement used SQLite's scalar `MAX(0, …)`, which both database backends reject (MAX is an aggregate there), so any unposted invoice or bill with a taxed entry was undeletable on a database book. The clamp is a CASE expression now, and a contract test keeps SQLite-only functions out of raw SQL. Sibling of #189, found by reading every raw statement for the bug class.
- **Invoice lookups work on PostgreSQL.** `_find_invoice`'s `date_posted=''` self-heal now runs only on SQLite, the one backend where that state can arise; on PostgreSQL the comparison itself aborted the transaction, so `get_document`, `post_document`, `pay_document`, `add_document_entry`, and explicit-id creation failed on every PostgreSQL book with `InFailedSqlTransaction`. Swallowed database errors in the business module clear an aborted transaction (`_rollback_if_aborted`) before the next statement, and the per-dialect database tests now run the document lifecycle. Found and fixed independently by @JamesRao98 on the 10xtechnology fork (#189).
- **Scheduled transactions are GnuCash's own** (#179, #176): the recipe is a template transaction with `sched-xaction` slots, so desktop's Since-Last-Run posts what this server scheduled and a desktop-made schedule instantiates here; the next occurrence is read from the recurrence rows by a port of `Recurrence.cpp` (every period type, weekend adjustment, composite rules), and one rule answers "which occurrence is next" on every surface — dashboard, lists, and the instantiation default all agree, overdue occurrences lead and walk forward. Templates store account GUIDs (rename-proof), finite schedules honor their remaining count, a late failure rolls back cleanly, and the refusal on a finished schedule names which stop applied.
- **Budgets follow GnuCash 3.8+'s natural-sign storage** and carry its feature stamp, so desktop displays what this server writes and vice versa; the tool surface stays in magnitudes. `get_budget_report` keeps income and expense on their own sides with a NET line (#177, #178).
- **Invoice links are written under GnuCash's key** (`gncInvoice/invoice-guid`), so Jump to Invoice, the lot viewer, and Process Payment find server-posted documents; lot titles and split actions carry the document's type string (#180). The conversion also recognizes the spelling desktop writes back on re-save (#181).
- **Lot open/closed state reads the way GnuCash defines it**: the flag's `-1` means "compute from the balance", as desktop leaves it, so lots desktop has touched list and settle correctly (#181).
- **Output order comes from the data**: splits follow GnuCash's `xaccTransSortSplits` (debits first) then account path, same-day transactions sort by entry time, and every list carries a data-derived tie-break, so a book renders identically on SQLite, PostgreSQL, and MySQL (#181).
- **Audit and debug logs open the day's file by path on every entry**: a file moved or removed under a running server comes back at its path, and the log rolls to the next day without a restart (#181).
- Deleting a transaction or schedule strips GUID-valued slots before the ORM delete, so piecash's cascade can never reach the entity the slot points at (#179).
- A database book's flag columns and a book path with a percent escape both round-trip (#175).
- Demo-book continuation counts ledger rows only when guarding its frozen prefix; schedule templates are not activity (#181).
- **An unpriced holding is worth its remaining cost basis** (#185): remaining units at the running average cost of the legs that acquired them, so a fully sold position is exactly zero and a partly sold one carries none of its realized gain. The old fallback summed every leg's raw value, which left a sold-out altcoin on the balance sheet as a phantom holding — found and fixed for the closed case by @DrSkippy in #184. One rule now serves `balance_sheet`, `net_worth` (point-in-time and series), the dashboard, and runway, so they agree by construction; voided and undated legs are skipped the way every own-splits sum skips them.
- **Intel Macs install without a Rust toolchain again**: `cryptography` (transitive, via `mcp`) dropped macOS x86_64 wheels at 49.0; a marker-scoped constraint keeps 48.0.1 on that platform only, every other platform stays current.

### Credits
- @DrSkippy — the closed-position valuation bug and its regression test (#184).
- @vchatela — the database backend (#175), the largest outside code contribution to date: nine commits, the `[postgres]` extra, and CI against PostgreSQL.

## v1.4.4 - The statement is the call

A complete bank statement enters, claims its matches, and reconciles in one atomic call; every consequential write now rehearses before it books; a one-click Claude Desktop bundle ships from the project's first CI. (v1.4.3 was never released on GitHub — that number belongs to a registry-side rebuild.)

### Added
- **`enter_statement` (#154)** — opening balance, closing balance, every line between, one atomic call. Statement self-check (opening + Σlines = closing) before anything else. Dry-run is the default: every line classified NEW / MATCH / OVERLAP / AMBIGUOUS with self-contained comparisons (both sides, deltas, category legs). Commit is one open, one save, or nothing; the closing tie is verified post-write from saved splits. Amounts transcribe statement-native; the server applies sign conventions per account class.
- `force` on `enter_statement` split into `force_base` / `force_duplicates` — clearing an opening gap can never silently disable duplicate detection.
- `pay_document` `dry_run` — proposed splits, projected remaining balance, FX/discount treatment, and any account the real call would auto-create; one shared computation with the booking path.
- `create_transactions` dry-run gains the statement surface: summary header, `review_required` status, self-contained duplicate comparisons with deltas and split-match verdicts, candidate reconcile state.
- **MCPB bundle (#153)** — download, double-click, Claude Desktop runs the server; book file picker; demo books behind one checkbox. Built and attached by CI on every PR (tests locked on Python 3.10 and 3.13).
- MCP ToolAnnotations on every tool, derived from the audit-log classification at one chokepoint, with a contract test (#150).
- `get_outstanding_documents` (renamed from `get_outstanding_invoices`) — the one-call answer to "what is actually unpaid?"
- Dashboard: warning rollups past three items (overdue-scheduled and stale-price collapse to one aggregate line each); staleness linkage ("time-based warnings below may reflect unentered activity"); reconciliation backlog line carries net unreconciled amount beside the split count.
- Demo books are living books: a closed-loop continuation engine (`scripts/synthetic_book/continue_book.py`) extends each sample from its committed history through the build date, deriving every flow from the book itself; CI runs the same updater so the bundle ships demo books current as of build day. Every book opens with zero dashboard warnings, reconciled through the last full month, current month open.
- Business transactions carry desktop's own split actions — `Invoice`, `Credit Note`, `Payment` — on every leg (forward-only; existing transactions untouched).
- Price sources rank in three tiers (manual quote > other user sources > feeds); `create_price` / `create_prices` report when a recorded price loses its date's tie.
- `apply_credit_note` against a document other than the one referenced is allowed and says so in the response and audit log.
- `debt_payoff_plan` confesses balance-carrying debts that lack an `apr` slot.
- Transaction-entry defaults resolve loudly (date echoed when defaulted to today).

### Changed
- **Business surface consolidated, 48 tools → 27:** five `*_party` tools replace fifteen (`party_type: customer|vendor|employee`); nine `*_document` tools replace eighteen (`document_type: invoice|bill|voucher|credit_note`); jobs and taxtables fold `get_` into `list_(id=...)`. Audit entries render identically. Docstrings lead with the species.
- Installer asks one module question — "Do you invoice clients?" Budgets, scheduled transactions, and investment tracking are always on; the Advanced field remains the full `--modules` escape hatch.
- Invoice/bill status vocabulary defined once: open / posted / paid / outstanding.
- CLI arguments reject unknown values instead of silently no-oping (`--modules all` means what it says) (#151).
- Invoice line items carry the document's own date (no more timezone-dependent entry dates).
- Credit notes are `type: "credit_note"` on every surface, with no due date.
- Every error message and docstring names the consolidated surface.
- Batch entry runs one signal sweep per batch instead of up to two per row — the 7-second p95 tail is gone.
- **Restart safety (bookkeeper ruling 6):** first tool result of every process names the active book; with 2+ books configured, mutating tools are disarmed after a (re)start until `switch_book` confirms the target (a no-op "already on it" counts); every mutating response in a multi-book session names the book it wrote to. Reads are never gated; a refused write consumes no rate-limit token, triggers no backup, writes no audit line.
- **Discount-account auto-create refuses on localized books (ruling 4b):** no more English default leaf in a localized chart. Existing accounts remain adoptable through every resolution layer. Role-based resolution (4a) is the destination that lifts the refusal.
- **BEHAVIOR BREAK — currency-mismatch posting is a refusal (ruling 1 sunset):** posting a document to an A/R or A/P account in a different commodity refuses (desktop GnuCash does the same), pointing at the per-currency subledger fix. Warning-era books are unaffected: their lots still settle via FX and the downstream guards stay.
- Test suite (2,100+ tests) runs parallel by default via pytest-xdist; full suite under 40 seconds.
- `cryptography` 49 → 50.0.1 (#155).

### Removed
- `create_transaction` and `update_transaction` — the batch tools reached full parity (`update_transactions` gained an opt-in per-row `clear` column). `create_transactions` and `update_transactions` are canonical for one transaction or many.
- `list_backups` and `prune_backups` — the backup store is append-only from the model's side. `create_backup` stays; retention runs internally; reviewing or deleting backups is a human filesystem operation.
- Surface: 111 tools → 86.

### Fixed
- Signed amounts reach the duplicate scorer — a refund no longer blocks as its payment's twin.
- Invisible Unicode separators in TSV input reject by name and row.
- Credit-note identity survives the piecash slot-sweep on transaction delete; owner-mismatch errors name the per-type ID collision; voucher posting reachable (#152).
- Release code review (8 angles, 10 confirmed findings, 10 fixes): the FX resolver gains the same fail-safe locale gate as discounts; `delete_document` regains the credit-note `party_type` disambiguator; `create_document` refuses type-inapplicable `job_id` / `applies_to_id`; the retired freelancer toggle is honored (its surface never joined the always-on base); the multi-book write disarm no longer caches a blind fail-open; `GNUCASH_REDACT_PATHS` fails closed through the toggle chokepoint.
- Statement/batch hot path: three N+1 query patterns eliminated (candidate universe, signal sweep, split-prefix map); the query budget is contract-locked.
- Demo continuation: revolver interest can't date into the frozen prefix, and the updater independently verifies the prefix's row count, failing loud.
- `text()`-form raw SQL is now visible to the write-verification contract lock.
- Credit note nets against a job-grouped invoice from the same customer (owner check compared a Job GUID against a Customer GUID) (#165).
- Payment dry runs render as `PAY INVOICE (dry run)` in the audit log; partial payments report `partial`, not `paid` (#165).

### Validation
- Three live bookkeeper probe rounds on `enter_statement`; full business-module battery on the German sample book (#165); the deferred battery rulings' six-call live loop (one FAIL found, fixed, re-probed to signoff); demo-fleet review with all findings closed and signoff on the record.

## v1.4.2 - One call wide, every surface honest

The bulk grammar is complete — updates, prices, currency, and reconciliation are all one call wide — plus the project's first outside code contribution and an injection-hardening pass on the audit trail.

### Added
- `update_transactions` — per-row bulk edits via TSV (description, notes, date), one book open, one save, abort/skip semantics; `update_transaction` takes a guid list to broadcast one change (#145).
- `create_prices` — batch quote entry with `create_price`'s upsert semantics via a shared chokepoint, plus a stale-price work list (#143).
- Per-transaction currency: a `cur` column in batch entry and scheduled-transaction templates (#141).
- Split `action` field on every transaction create path (#142).
- `get_reconciliation_status` — per-account drill-down behind the dashboard's counts (behind / never / current / dormant / excluded), same classification as the dashboard (#146).
- `no_reconcile` account slot opts statement-less accounts out of dashboard nagging; reporting-only (#146).
- `get_book_summary` hands each session its working set: top 15 accounts by posting frequency (last 180 days) in `%short-GUID` format (#146).

### Changed
- **Behavior change:** moving the posting date of a transaction with reconciled splits requires `force=true` on the single, broadcast, and batch update paths (#148).
- Dormant accounts ($0, fully reconciled, idle) collapse into one aggregate line; carried balances with months of silence stay individually warned (#146).
- Bulk-reconcile audit entries render what actually happened; reconcile errors teach — did-you-mean account suggestions, `through_date` hints, placeholder-children warnings (#137, #144).
- Five dashboard clarity fixes from live review; notes convention pass (template notes, audit truth, leg preservation) (#138, #139).
- Release policy: sample books ship frozen and regenerate on demand via `scripts/synthetic_book/`.
- Contributor guide documents the chokepoint pattern with the established chokepoints named.

### Fixed
- `reconcile_all` honors the statement-date bound its docstring always promised — splits after the statement date stay unreconciled (#144).
- `get_book_summary` on a 33k-split, 10-commodity GBP book never completed; price lookups now memoized per commodity pair and the split graph bulk-loaded — under 10 seconds on that book, ~45% faster on small books. Contributed by @bhbrunt (#126). Follow-ups: `create_prices` invalidates the price memo, a SQL-count regression test guards the preload, a final `guid` sort key makes the price tie-break deterministic (#147).
- User-controlled text (descriptions, memos, payee strings) is escaped before reaching the audit file — a crafted newline pair could previously forge an audit entry or smuggle instructions to the model reading `get_audit_log` (#148).
- `create_prices` dry-run and live execution agree on duplicate identities (#148).

### Credits
- @hpuri (Gemini CLI and `libdbd-sqlite3` guidance, issue #89), @uppaljs (the `Decimal(str(value))` rule behind `_to_decimal`), @alhosani-abdulla (issue #94, intermediate-currency chain valuation).

## v1.4.1 - Batch entry grows up; every annotation field reachable

### Added
- `create_transactions` header-declared layout: per-split `memo` columns, per-transaction `notes` column, cross-commodity `qty` columns, field order fixed by the header's first group, trailing shorthand (a row may end after its last split's amount and account), auto-fill from history for rows with no split cells (`auto_filled_from:<guid>`, still duplicate-screened), strict header validation naming unknown columns.
- `delete_transaction` accepts a list of GUIDs — one open, one save, all-or-nothing.
- Invoice/bill/voucher/credit-note line items take `notes` (4096-byte cap) and `action`.
- `pay_invoice` takes a `memo` for the bank split.
- Account `notes` on `create_account` / `update_account` (same slot GnuCash desktop reads; `""` clears).
- `list_accounts` `query` — case-insensitive substring on path and description; composes with `root`; emits %short GUIDs.
- One-command sample-book rebuild through today.

### Changed
- Monthly-close valuation (GB-1): flow reports value every split at its own month's closing rate in single-period and `group_by` modes, so totals agree at every granularity; stock reports keep as-of semantics; partial sub-periods marked `*`.
- Retirement accounts classify via an `is_retirement` slot, not English name-sniffing; Imbalance/Orphan matching requires the exact word or `-CUR` shape.

### Fixed
- Transactional `switch_book` — a failed switch no longer tears server state (retry said "Already on: B" while writes went to A).
- Per-book backup scoping — two books under a shared `GNUCASH_LOG_DIR` no longer cross-prune each other's backups.
- Localized FX-account wedge — cross-currency `pay_invoice` on a German book failed forever.
- Scheduled transactions persist their `description` (previously silently dropped).
- Instantiation audit entries show the created transaction's GUID and description.

### Tests
- 1,856 passing.

## v1.4.0 - Internationalization, batch entry, and multi-book

### Added
- Locale-robust account resolution: top-level accounts resolved by `GNCAccountType`, book locale inferred by voting across type accounts, designated accounts (FX gain/loss, discounts) self-heal via a KVP slot.
- Sabine Brenner — German DATEV SKR03 sample persona (EUR); Lin Wei localized to a zh_CN chart.
- `create_transactions` — batch transaction entry, one atomic call, per-row results correlated by caller `ref`, duplicates table keyed to it.
- Multi-book: `GNUCASH_BOOK_PATH` takes an `os.pathsep`-separated list; `switch_book` flips the active book in-session with a context-reset banner.
- Pagination: `offset` plus `Showing X-Y of Z` on all list-returning tools; dated tools render the covered range.
- `group_by` sub-period columns on aggregation reports.
- FX entry-sanity warning when a cross-currency transaction's implied rate diverges sharply from the latest price.

### Fixed
- Suspense/Imbalance accounts excluded from runway and low-cash signals.
- FX gain/loss booked in the book's default currency; both-foreign posting splits valued at the posting-date rate; lot cost basis in the default currency; foreign debts with no FX rate excluded from `debt_payoff_plan` with a warning.

### Tests
- 1,714 passing.

## v1.3.1 — Business module, role-aligned modules, multi-currency correctness

### Added
- Employee expense vouchers (`create_voucher`, `add_voucher_entry`, polymorphic post/pay/unpost/delete).
- Credit notes (`create_credit_note`, `add_credit_note_entry`, `apply_credit_note`); link persisted in `gnc-mcp/applies-to-invoice`.
- Jobs (`create_job`, `get_job`, `list_jobs`, `update_job`, `get_job_report`, `delete_job`); invoices and bills accept `job_id`.
- Tax tables (`create_taxtable`, `add_taxtable_entry`, `update_taxtable`, `list_taxtables`, `get_taxtable`, `delete_taxtable`); tax splits built at post time with residual-to-largest-rate rounding; tax-inclusive pricing; refcount blocks deletion of in-use tables. Surface 87 → 106 tools.
- Bulk reconciliation: `reconcile_all=true`, `except_guids=[...]`, `through_date`; account shortcuts accepted everywhere.
- Early-payment discounts honored: `pay_invoice` `apply_discount=True` validates terms, window, and shortfall on pre-tax principal; `discount_account` resolves explicit > leaf-name match > canonical default; `get_invoice` verbose surfaces `discount_available` / `discount_expired`.
- Synthetic "Unrealized Gain/Loss" equity row on `balance_sheet` (display-only balancing residual).
- Dashboard: overdue counts on receivables/payables lines; active-jobs line.
- Write rate-limiting via token bucket, opt-in (`GNUCASH_WRITE_RATE_LIMIT`, `GNUCASH_WRITE_BURST`).
- FX staleness cap (`GNUCASH_FX_STALENESS_DAYS`, default 90); invoice post/pay raise `StaleFXRateError` instead of posting on a stale rate (override with `force`).
- Intermediate-currency valuation via a pivot currency, with provenance (`via USD`). Reported by @alhosani-abdulla (#94).
- `create_budget` accepts `start_date` for retroactive budgets.
- Pathological-shapes fixture book (parents/placeholders with direct splits, overpaid lot, voided transaction, desktop SX template, foreign A/R, future-dated entry) run against every report surface.

### Changed
- `--modules` partition is role-aligned: `core` (29, group alias for nine sub-modules), `bookkeeper` (17), `investor` (12: `portfolio` + `tax_lots`), `freelancer` (19), `business` (29); `get_server_config` renders groups as `core[accounts, audit, ...]`.
- `extra="forbid"` on every tool's argument model — unknown kwargs fail loudly.
- `delete_invoice` / `delete_bill` / `delete_voucher` / `delete_credit_note` accept `id` alongside `<entity>_id`.
- Server instructions 39% smaller (~2,500 → 1,522 chars).
- Token trimming: 8-char GUID prefixes in `get_transaction` and verbose `list_transactions`; business-object `guid` fields stripped from responses (`transaction_guid` kept, as a short prefix).
- Book directory path redacted from `get_server_config` / `get_book_summary` (filename only); tool errors route through `redact_paths()`.
- **Net worth restated:** RECEIVABLE and PAYABLE accounts now sit in their natural balance-sheet buckets across `balance_sheet`, `net_worth`, and the summary trajectory — outstanding A/R minus A/P is included; historical anchors shift accordingly.
- Internal: `CurrencyMixin` extraction, `_compute_fx_gain_loss` helper, `get_book_summary` decomposed into `_render_*` helpers (~30% faster on large books), `QueryMixin` finders.

### Fixed
- A = L + E holds by construction across `balance_sheet`, `net_worth`, and the summary.
- Multi-currency aggregation: monthly net, budget headline actuals, daily expense burn (runway), and vendor spending report all convert to default currency instead of summing raw units.
- `get_book_summary` FX-converts foreign-currency liabilities; `debt_payoff_plan` values foreign debt in default currency; `vendor_spending_report` excludes unconvertible bills with a per-currency warning.
- `income_by_source` / `spending_by_category` net contra splits per account (gross → net); budget actuals net contra splits the same way.
- Net-worth surfaces agree on which splits count: every account contributes exactly its own splits.
- `pay_invoice` rejects overpayments; `remaining_balance` / `amount_due` / job `outstanding` direction-normalized (`OVERPAID`); credit notes carry no aging clock.
- Native SX template transactions filtered from `list_transactions`, `search_transactions`, dashboard, `list_commodities`; `delete_scheduled_transaction` removes desktop recipe rows.
- Voided splits protected at every boundary: `update_transaction` / `replace_splits` refuse voided targets; `reconcile_account` skips voided splits; auto-fill and duplicate detection ignore voided history; balance surfaces exclude voided splits via `_own_splits_balance`.
- Future-dated transactions excluded from runway, low-cash warning, and `debt_payoff_plan`; null `post_date` renders `(no date)`.
- Report totals depth-invariant; `depth` matches its documented contract (the old off-by-one collapsed the default report to one row).
- Historical anchors never value via future rates; rate provenance names the path used.
- Invoice settlements count as cash flow.
- Cross-commodity A/R relieved at the carried rate (pro-rata for partials); discount leg's FX drift booked with the payment leg's.
- `calculate_lot_gain` converts foreign cost basis at historical purchase rates.
- FX direction label on credit-note refunds follows the booked direction.
- Auto-fill no-match guard fires — blank descriptions carry no match signal (previously cloned an unrelated transaction).
- Dashboard never ages credit notes.
- Backup retention works under `GNUCASH_REDACT_PATHS=1` (pruners resolved a redacted basename against the working directory).
- `update_account` rename enforces `create_account`'s name validation; fuzzy FX/discount matching skips template accounts; business free-text gates short-circuit at the schema boundary; write-verification handles two splits to the same account.
- Smaller: `update_taxtable` force gate spells out blast radius; debt-plan slots convert from the account's commodity; invoice-family deletes clean slot rows; `get_lot` marks voided rows.

### Tests
- 1,584 passing (was 1,114).

## v1.2.1 — Business module shipped, multi-currency hardened

### Added
- `update_customer`, `update_vendor`, `update_employee`.
- `unpost_invoice`; `delete_transaction` refuses to remove a posting record directly and points at `unpost_invoice`.
- Cross-currency invoicing end-to-end: `post_invoice` / `pay_invoice` apply price-table rates; post-to-pay drift booked as realized FX gain/loss (`fx_account` parameter or auto-created).
- Owner-currency inheritance for new invoices and bills.
- Dashboard work-queue sections in `get_book_summary`: last entry, net-worth trajectory (five anchors), monthly net income (six months), runway, budget headline, reconciliation backlog with split counts and oldest-split lag, upcoming scheduled, consolidated warnings.
- Auto-backup on first write per session to `<book>.gnucash.mcp/backups/`; staged retention 7 session / 4 weekly / 6 monthly; `PRAGMA integrity_check` on every snapshot; `create_backup(label)`, `list_backups`, `prune_backups(dry_run=true)`. Restore is deliberately not a tool ([docs/RESTORE_FROM_BACKUP.md](docs/RESTORE_FROM_BACKUP.md)).
- Short collision-safe GUIDs (`%xxxxxxx`) accepted everywhere a path is.
- `delete_price` with source disambiguation.
- Sample books ship with the repo: `samples/alex-chen-morales.gnucash` (USD), `samples/lin-wei.gnucash` (CNY).
- Self-conducted code review ([specs/CODE_REVIEW.md](specs/CODE_REVIEW.md)): 3 critical, 12 high, 26 medium, 18 low findings; all criticals, all highs, all real-bug mediums, and 11 of 18 lows closed in this release.

### Fixed
- `balance_sheet`, `net_worth`, `cash_flow`, `get_book_summary` value foreign-currency holdings at shares × latest price (cost-basis fallback) — Alex's net worth was understated by ~$57K.
- Cross-sequence invoice/bill ID collisions fail loud naming both candidates.
- `create_price` and `get_latest_price` default to the book's currency, not USD.
- `debt_payoff_plan` uses the amortization formula for LIABILITY accounts (a ¥2.7M mortgage at 3.85% asked ¥54,590/mo instead of ~¥14,800).
- `unpost_invoice` ignores voided payment splits; `void_transaction` warns when reconciled splits are zeroed; `list_lots` skips empty lots; `owner_type` validated at all six entry points.
- Criticals: silent budget-amount truncation; backup filename collision and auto-backup gate race; swallowed auto-backup failures (summary now surfaces chain status).
- Highs: investment cost-basis precision; scheduling month-end drift; tri-currency FX gain/loss; pay-invoice A/R-side conversion; per-commodity precision (JPY, BHD/KWD); audit before-state staging; write verification on `update_transaction` / `replace_splits`; `delete_account` dangling handle; audit before-state pre-clear; `prune_backups(keep_last_n=0, manual)` refuses to wipe every manual snapshot.
- Mediums/lows: vendor bills render `POST BILL` / `PAY BILL` in the audit log; entries reject wrong account types; statement-balance comparison quantizes to commodity fraction; `unvoid_transaction` validates slot completeness; audit files written `0o600`; `_resolve_guid` per-table dispatch covering prices and entries; write-verification failures get their own `error_type`; `set_account_slot` rejects slash keys; `_describe_age` rounds.

### Tests
- 1,114 passing (was 540). Roadmap for 1.3: [specs/NEXT_STEPS_1_3.md](specs/NEXT_STEPS_1_3.md).

## v1.2.0 — Business module debut

### Added
- Customers and vendors with full address support; billing terms (Net 30, early-payment discounts).
- Invoices and bills with line items, posting to A/R or A/P, partial payments.
- Outstanding-invoices report and vendor-spending breakdown.
- GnuCash UI compatibility: posted invoices carry the metadata slots (`gncInvoice`, `trans-date-due`, `date-posted`) the desktop expects.
- Write verification: raw SQL operations read-back-checked before commit, automatic rollback on failure.
- `business` tool module (22 tools), opt-in via `--modules`.
- Server-level MCP instructions sent on connect.

### Tests
- 540 passing.

## v1.1.0 — Modular tool loading

### Added
- `--modules=` flag: seven modules (core, reconciliation, reporting, budgets, scheduling, investments, admin); 52 tools down to as few as 15; `core` always loaded; `--modules=all`.
- `get_server_config` debug tool (loaded with `--debug`).
- `GNUCASH_MCP_MODULES` env var.

### Tests
- 424 passing.

## v1.0.2 — Compact output

### Changed
- Compact one-line-per-item default output for `list_transactions`, `list_commodities`, `list_scheduled_transactions`, `get_unreconciled_splits`, `list_lots`; verbose JSON via `verbose=true`.
- Minified JSON: null/empty values stripped from all responses.

### Added
- `get_book_summary` single-call snapshot.
- Partial GUID support (8+ char prefixes) for transactions, splits, lots, scheduled transactions.

### Tests
- 399 passing.

## v1.0.0 — Stable release

### Added
- `replace_splits` — wholesale split replacement on existing transactions.
- Transaction pipeline: duplicate detection, dry-run mode, auto-fill from prior transactions, date sanity checks, placeholder-account warnings.
- `list_accounts` compact mode with `root` filter.
- Account-metadata slots (APR, credit limits, reward rates).
- Audit log text format alongside JSON.

### Tests
- 394 passing.

## v0.9.0 — Feature build-out

### Added
- Investments: commodities, prices, lot-based cost basis, capital gain calculation.
- Scheduled transactions: recurring templates, upcoming bills, one-click instantiation.
- Budgets: create, set targets by period/quarter, variance reporting.
- Multi-currency: cross-currency transactions with quantity/value split handling.
- Reporting: spending by category, income by source, balance sheet, net worth, cash flow.
- Reconciliation: statement reconciliation, void/unvoid with audit trail.
- Audit logging alongside the book file.

### Tests
- 187 passing.

## v0.1.0 — Initial release

### Added
- Account listing, balances, transaction CRUD, search.
- MCP server via FastMCP; Claude Desktop integration.
- piecash SQLite interface with error handling.
