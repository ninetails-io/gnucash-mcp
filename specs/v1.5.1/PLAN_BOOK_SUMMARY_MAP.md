# Plan — `get_book_summary` chart map and %guid steering

*From the bookkeeper, 2026-10-07, for the fixing session. Display
and docs only: no storage change, no new tools, no behavior change
to any listing tool. Target: today's diff, so Codex reviews it with
everything else.*

## Why (evidence, not theory)

Across six live batteries the bookkeeper learned every book's
account tree by bouncing off did-you-mean errors (three full
round-trips lost to guessed paths), and used the %short account
guids exactly zero times in hundreds of calls — because the
docstrings say "account name" and nothing ever said the audit log
records resolved names regardless of what the caller passes. An
outside review reached the same two conclusions independently.

## Change 1 — Chart of Accounts map in `get_book_summary`

Replace the current scattered count lines ("Accounts: 99 total",
"Assets: 16 accounts…", "Income: 6 active (10 total)", "Expenses:
39 active (56 total)") with ONE consolidated tree, depth two:

    Chart of accounts (99 active):
      Assets (16): Current Assets (5), Fixed Assets (2), Investments (7), Receivables (2)
      Liabilities (5): Credit Card (2), Loans (2), Payables (1)
      Equity (3)
      Income (6)
      Expenses (39): Auto (4), Business (14), Housing (4), Taxes (10), Utilities (5), …
      — drill into any branch with <the real call, see rule c>

Rules:
a. **Fold, don't append.** The map replaces the existing per-type
   count lines; the summary must not get materially longer. The
   top-asset balances currently shown under Assets stay (they are
   values, not structure).
b. **Counts obey the standing filters.** Template accounts excluded
   (the `_template_account_guids` doctrine, CLAUDE.md); hidden
   accounts excluded from the active count, shown as `(+N hidden)`
   on the branch when nonzero. Every number must tie exactly to
   what `list_accounts` returns under the same filters.
c. **The drill-down hint names a call that validates.** Check what
   `list_accounts` actually accepts (`query=`? a subtree
   parameter?) and print THAT, verbatim-tested. Do not invent
   `root=` if it does not exist — this week fixed two errors that
   prescribed uncallable cures; do not ship a third.
d. Second-level listing: show all second-level children for
   Assets/Liabilities/Equity; for Income/Expenses, show up to ~8 by
   descendant count and elide the rest with `…` (the drill-down
   hint covers them).

## Change 2 — %guid steering in the frequently-used list

Keep the top-15 list exactly as is (it is the exact-spelling and
guid source callers copy from). Change only its header:

    Frequently used accounts (last 180 days — any account parameter
    accepts the %guid or the full name):

## Change 3 — docstring updates on account-taking parameters

Every tool parameter that resolves an account (`list_transactions`,
`get_balance`, `get_account`, `update_account`, `delete_account`,
`set_account_slot`, `create_transactions`/`update_transactions`/
`enter_statement` TSV `acct` cells, `pay_document
payment_account`, `post_document post_account`,
`create_scheduled_transaction` splits, budget/report account
filters — sweep for the rest) gets the same first line:

    Account name ("Assets:Checking") or %short guid ("%d53d547").

In the TSV docs for `create_transactions` and `enter_statement`
only, add the pitch — that is where the savings are real:

    In wide batches, %guids in acct cells shrink the payload;
    nothing is lost — the audit log records the resolved account
    names regardless of which form the call used.

## Acceptance (the bookkeeper's probe, same day)

1. On all three demo books plus a book with a hidden parent
   (`abe-bench/sr-alex.gnucash` has one): every map count ties
   exactly to `list_accounts` under the same filters.
2. The drill-down hint's example call, pasted verbatim, validates
   and returns the named subtree.
3. A transaction entered with a %guid `acct` cell produces an
   audit entry naming the resolved account, byte-identical to the
   name-entered equivalent (GUIDs aside).
4. Summary line count within ±3 of current on the Alex demo.
5. Suite green; no snapshot or gate needed (display and docs only).

## Non-goals

No change to listing tools' behavior, no new parameters, no
storage shape, no `entries:` work (that is the separate
create_document item, under its own conditions).

## Fixing session notes (2026-10-07, branch `feat/book-summary-map`)

- **"Active" now means not hidden.** The old "Income: 6 active (10
  total)" counted accounts with splits. Rule b's tie to
  `list_accounts` only holds for the hidden/not-hidden split, so the
  has-activity signal is gone. Branch counts include the branch node
  itself, because that is what `list_accounts(root=…)` returns.
- **The hint rides the header line.** The spec's shape put it on a
  line of its own; that left Alex at +4 lines, so the header reads
  `Chart of accounts (96 active) — drill into any branch with
  list_accounts(root="Assets:Investments"):`. Alex is +3.
- **Second-level entries are branches only.** A top-level's leaf
  children are implied by its count (the spec's own "Equity (3)"
  lists none). Income/Expenses cap at eight branches by subtree size
  with `…`; the other top levels list every branch.
- **Change 3's pitch was false when written.** The batch-create and
  statement audit formatters re-parse the TSV themselves and printed
  `acct` cells as typed; the dispatcher's ref-normalizer never saw
  them. `_resolve_tsv_split_refs` (logging_config) now resolves them
  in one book open per entry, and the statement entry renders a
  created row's counter splits. Acceptance 3 is pinned by
  `tests/test_logging.py::TestAuditLogResolvesAccountRefs::
  test_batch_create_acct_cells_render_identically` and its
  statement twin.
- **Locks:** `tests/test_book.py::TestGetBookSummaryChartMap` (ties,
  hidden parent, template exclusion, hint validates, cap);
  `tests/test_contract_integrity.py::TestAccountRefDocstringConvention`
  (every account-shaped tool parameter is a ref with the sentence or
  a named look-alike).
- **Probe (acceptance 1, 2, 4):** alex, lin-wei, sabine-brenner, and
  `abe-bench/sr-alex` — every map count equals
  `list_accounts(root=…, limit=0)`, the hint returns the subtree,
  Alex 81 → 84 lines.
