# Bookkeeper live loop — read-tool preload and price chokepoint (perf/finish-preload-and-price-chokepoint)

Four checks, one bounce, no desktop instrument. Branch under test:
`perf/finish-preload-and-price-chokepoint`, two commits on develop
at #187. What it claims: the last three hand-rolled price walks
(`_commodities_with_market_prices`, `list_commodities`,
`get_latest_price`) now read `_find_prices`, so the rate the operator
is shown is the rate the reports use, and it is the same rate on
every backend. And the whole-book preload now covers slot values and
skips the unused account pass, so every read tool issues a fixed
number of queries whatever the book's size — a notes search included.
Closes #186.

Numbers on file do not change for any book with at most one market
quote per commodity per day. The unit suite proves the arithmetic
and locks the query counts; this loop checks the tool surface on a
real book and that the oracles did not move.

## Setup

- Branch checked out; bounce Claude Desktop once. The `gnucash`
  server's first book is production; `switch_book` to Alex and
  confirm the `Book:` line before any write. Alex still carries the
  ALTX probe from the #185 loop, which is fine and useful here.
- Copy first: `cp samples/alex-chen-morales.gnucash /tmp/alex-pre-186.gnucash`.

Fingerprint: after step 2, `get_latest_price` for the tie commodity
answers the manual quote. Develop answers whichever row the backend
returns first, which on SQLite is the one written first.

## Part A — the oracles did not move

Already run by the maintainer's Claude Code session on 2026-09-20:
capture rig on Alex (20 files), Lin Wei (20, with `--today
2025-12-31 --historical 2025-06-30`), Sabine (17), develop worktree
versus this checkout — three empty diffs. Rerun if you want your own
hands on it; the commands are in the #185 plan. Pass the Lin Wei
flags literally, not through a shell variable.

## Part B — on Alex

1. **A same-day tie, both ways round.** `create_prices` for AAPL in
   USD, two rows dated today: source `user:market-data` at 100, then
   source `user:price` at 120 (feed first, manual second, so storage
   order and rank order disagree). Expected: no tie note on either
   write in this order — the note fires on a row that is OUTRANKED
   by one already on file, and nothing outranks a manual quote
   (step 2 exercises the note). Then `get_latest_price` AAPL: value 120, source
   `user:price`. `list_commodities` (verbose): AAPL's latest price
   120. `balance_sheet` as of today: the AAPL row is priced `@ 120`.
   Three surfaces, one number. Develop: `get_latest_price` and
   `list_commodities` say 100, the sheet says 120.

2. **Reverse the write order.** `delete_price` both rows. Write them
   again, manual first, feed second. Expected: all three surfaces
   still say 120. The answer depends on rank, not on which row was
   written first. `delete_price` both when done, and confirm
   `get_latest_price` AAPL returns to the cached-quote value it had
   before step 1 (note it before starting).

3. **The notes search is no longer a per-transaction walk.**
   Alex carries no transaction notes (the builders write none), so
   seed three first: `update_transactions` on the three 2026
   Umbrella Insurance Premium rows, notes `insurance`. Then
   `search_transactions` with `field="notes"` and query
   `insurance`. Expected: exactly those three rows, the same rows
   the description search finds for that word, and the call
   returns in the same order of time as a description search. On
   develop the notes mode was the slowest read tool on the book
   (one query per transaction, about two thousand on Alex); it
   should now feel like the others.

4. **The listing and the summary still agree with themselves.**
   `list_transactions` unfiltered, then `list_transactions` for
   `Assets:Checking`, then `get_book_summary`. Expected: identical
   rows and figures to the same three calls before the bounce
   (take them first). The preload change moves nothing; if any
   number or row differs, that is a finding.

Restore: `cp /tmp/alex-pre-186.gnucash samples/alex-chen-morales.gnucash`
with the server idle, or leave the drift.

## Not in scope

- `search_transactions` on a NULL description, its verbose-mode
  GUIDs, and amount-mode validation are pre-existing and not part
  of this branch.
- `_rates_as_of` is still recomputed per call within one
  `get_book_summary` (about 14 times); a memo is a separate item.

## Report

`BOOKKEEPER_REPORT_PRICE_CHOKEPOINT.md` beside this file: the
pre-bounce figures from step 4, each step's observed against
expected, any workaround. A workaround is a finding.
