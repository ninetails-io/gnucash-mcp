# Bookkeeper report — read-tool preload and price chokepoint (perf/finish-preload-and-price-chokepoint)

Run: 2026-09-20, Alex (`samples/alex-chen-morales.gnucash`, carrying the #185 ALTX probe; pre-copy at `/tmp/alex-pre-186.gnucash`), branch @ `fc702a1`, v1.4.4, 87 tools. Bookkeeper: Abe VII (Cowork).

## Verdict: PASS (Part A 1/1 rerun, Part B 4/4) — merge. Three notes, none blocking.

## Part A — oracles did not move (own hands, Alex only)
Capture rig, develop worktree at `2f35979` (#187) vs this checkout: 20 files, `diff -ru` empty. Lin Wei and Sabine accepted from the maintainer's 2026-09-20 run.

## Pre-bounce figures (step 4) — not capturable from this seat
The plan arrived with the server already on the branch (restart notice on the first call). The capture rig's develop-vs-branch diff covers `list_transactions` (compact + verbose), `get_book_summary`, and the account-filtered listing, and it is empty — that is the step-4 evidence. Post-bounce reads: `list_transactions` 1,943 rows, head `f2883336` "Road trip fuel" 2026-07-18; Checking register 1,177 rows, head `6897c2de` −4.95 2026-07-16; summary assets 862,031.84 / net worth 451,263 (ALTX included) — all identical to the #185 end state.

## Part B
1. **Same-day tie, feed then manual — PASS.** Before: `get_latest_price` AAPL = 333.74 (2026-07-17, `user:market_data`). Wrote `user:market-data` 100 then `user:price` 120, both 2026-09-20. `get_latest_price` → 120 / `user:price`; `list_commodities` verbose → AAPL 120 (2026-09-20); `balance_sheet` → `31 AAPL @ 120 (USD 3,720.00)`. Three surfaces, one number. **Deviation:** the second write came via `create_prices` and returned a bare `created` — no "outranks the first" note. The note exists on `create_price` (seen in step 2) but the bulk tool doesn't carry it.
2. **Reverse the order — PASS.** Deleted both (`delete_price` with `source` disambiguation worked). Wrote manual 120 first, feed 100 second: `create_price` for the feed answered `note: recorded, but a 'user:price' price for this date outranks it as the effective rate (manual sources win same-date ties)`. All three surfaces still 120. Deleted both; `get_latest_price` back to 333.74 / 2026-07-17.
3. **Notes search — PASS, fixture caveat.** Alex has **zero** transaction notes (raw: 0 `notes` slots on transactions), so the plan's `insurance` query returned 0 rows. Seeded notes on the three 2026 Umbrella Insurance Premium transactions via `update_transactions`; `search_transactions(field="notes", "insurance")` then returned exactly those three, same rows the description search finds for that word, and returned as quickly as the description mode. Wall-clock not instrumented from this seat — "no perceptible wait" is the honest measure.
4. **Listing and summary agree — PASS** (see pre-bounce section).

## Routed around
- Step 3's premise ("Alex's notes carry payee interpretations") is false on the committed book; I seeded three notes to exercise the mode. The plan should either seed them or point at a book that has notes.
- Nothing else.

## Notes
1. `create_prices` (bulk) lacks the same-date-tie `note` that `create_price` emits. Parity item.
2. `list_commodities` verbose omits `latest_price` entirely for an unpriced commodity (ALTX) rather than saying so; the compact `stale_days` view does say "no price on file". Minor.
3. Alex now carries the three seeded notes plus the #185 ALTX litter. Restore from `/tmp/alex-pre-186.gnucash` (server idle) or regenerate.

Signed: Abe VII, bookkeeper. One rate, every surface, whichever row was written first. Merge.

## Maintainer note — 2026-09-20, on note 1

Not a parity gap. Both writers emit the tie note on a row that is
outranked by one already on file, and neither emits it on the
row that does the outranking. In step 1 the manual quote was
written second, so no note was due from either tool; the plan's
expectation was wrong and is corrected. Verified: `create_prices`
with manual first, feed second answers the feed row's `reason`
column with `outranked by 'user:price' for this date (manual
sources win same-date ties)`, the same sentence `create_price`
returned in step 2. Note 2 (`list_commodities` verbose omits
`latest_price` for an unpriced commodity) stands as a minor item.
