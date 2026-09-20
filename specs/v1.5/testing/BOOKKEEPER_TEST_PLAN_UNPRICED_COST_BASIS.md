# Bookkeeper live loop — unpriced holdings at cost basis (fix/unpriced-holding-valuation)

Seven checks, one bounce, no desktop instrument. Branch under test:
`fix/unpriced-holding-valuation`, one commit on top of develop at
#182. What it claims: a holding with no market price on file is
worth its remaining units at the running average cost of the legs
that bought them. A fully sold position is exactly zero, a partly
sold one carries none of its realized gain, and `balance_sheet`,
`net_worth` (point-in-time and series), the dashboard, and runway
all read that one number. Closes #185; subsumes #184.

The unit suite proves the arithmetic on hand-built books. What it
cannot prove is that the numbers land the same way through the
tool surface on a real book with everything else in it, or that
the three oracle books did not move. That is this loop.

## Setup

- Branch checked out in the working tree (`git branch --show-current`
  reads `fix/unpriced-holding-valuation`). The Desktop `gnucash`
  server runs `uv run --directory ~/Projects/gnucash-mcp`, so the
  checkout IS the server. Bounce Claude Desktop once after checkout.
- The `gnucash` server lists four books. **The first is
  `~/Finances/books.gnucash`, production.** Every step below runs
  on Alex. `switch_book` to Alex first and confirm the `Book:` line
  before any write.
- Alex is git-tracked on this branch and rebuilt by its builder at
  will, so writing to it is fine; sample drift is never staged.
  Still, take a copy first so restore is one command:
  `cp samples/alex-chen-morales.gnucash /tmp/alex-pre-185.gnucash`.
- Dates below are chosen inside Alex's span. `ALTX` is the scratch
  commodity; nothing prices it until step 6.

Fingerprint (the one line that tells old code from new): after
step 3, `balance_sheet` shows `Assets:Investments:Altcoin` as
`1 ALTX (USD 100.00, no price data)`. Develop shows
`1 ALTX (USD -350.00, no price data)`, a negative asset.

## Part A — the oracles did not move (CLI, before the bounce)

Every commodity in the three demo books is priced from the
committed cache, so the fallback never fires on them and no report
number may change. Prove it with the capture rig, before and after,
same machine, same `--through`:

    # "before": a worktree at develop
    git worktree add /tmp/wt-develop origin/develop
    (cd /tmp/wt-develop && uv run python scripts/branch_1/capture.py \
        --book /Users/stephen/Projects/gnucash-mcp/samples/alex-chen-morales.gnucash \
        --out /tmp/cap185/pre/alex)
    # "after": this checkout
    uv run python scripts/branch_1/capture.py \
        --book samples/alex-chen-morales.gnucash --out /tmp/cap185/post/alex
    diff -ru /tmp/cap185/pre/alex /tmp/cap185/post/alex

Repeat for `lin-wei.gnucash` and `sabine-brenner.gnucash` (Lin Wei
needs `--today 2025-12-31 --historical 2025-06-30`, see the rig's
docstring). Expected: three empty diffs. Any non-empty diff is a
finding, full stop, before the live part starts.

## Part B — the live loop on Alex

Record the baseline first: `balance_sheet` as of today (assets
total, liabilities total, equity total), `net_worth` as of today,
and the dashboard's net-worth line from `get_book_summary`. Call
them A0, N0, D0. They should already agree with each other; if they
do not, stop and report that before continuing, because every
later check is a delta against them.

1. **Create the unpriced holding.** `create_commodity` mnemonic
   `ALTX`, fullname `Scratch Altcoin`, namespace `CRYPTO`, fraction
   100000000. `create_account` `Assets:Investments:Altcoin`, type
   STOCK, commodity ALTX. Then one `create_transactions` row dated
   2024-03-01, description `Buy ALTX`: checking leg −1000, Altcoin
   leg +1000 with qty 10 (use the `qty` split column). Expected:
   `balance_sheet` shows the row as `10 ALTX (USD 1,000.00, no
   price data)`; assets total A0 unchanged (cash down, holding up);
   `net_worth` N0 unchanged; dashboard D0 unchanged. Also expected:
   `get_book_summary` now carries a `Stale price: ALTX no price on
   file` warning. That warning is pre-existing behavior and correct.

2. **The buy alone matches develop.** Nothing in step 1 exercises
   the fix; it establishes that a buy-only holding values the same
   way it always did. If any of A0/N0/D0 moved, that is a finding.

3. **Partial sale.** One `create_transactions` row dated
   2025-06-01, `Sell 9 ALTX`: Altcoin leg −1350 with qty −9,
   checking leg +1350. Expected on all three surfaces: +450 against
   baseline (cash is net +350, the remaining unit is 100 at cost).
   The row reads `1 ALTX (USD 100.00, no price data)`. This is the
   fingerprint. Old code: +0 on the sheet and net_worth, and the
   row reads −350.00.

4. **Close it out.** One row dated 2025-09-01, `Sell 1 ALTX`:
   Altcoin −200 qty −1, checking +200. Expected: the Altcoin row is
   absent from `balance_sheet` (not a 0.00 line); all three surfaces
   read +550 against baseline; equity's `Unrealized Gain/Loss` line
   has grown by 550, which is the gain nobody booked to income.
   That residual is the documented place it lands; note the number,
   do not treat it as a finding.

5. **Trajectory agrees with point-in-time.** `net_worth` with
   `start_date` 2024-01-01, `end_date` today, interval `year`. For
   each boundary in the series, call `net_worth` with that boundary
   as `end_date`. Expected: equal to the cent at every boundary. The
   2025-01-01 point carries the holding at 1000, the 2026-01-01
   point carries it at 0.

6. **A price switches the rule off.** `create_price` ALTX in USD at
   50, dated today. Expected: nothing changes on any surface (the
   position is closed, 0 × 50). Then `void_transaction` on the
   step-4 sale. Expected: the row returns as `1 ALTX @ 50 (USD
   50.00)`, market-valued, not cost; all three surfaces move by the
   same amount (−200 cash, +50 holding: −150 against the step-4
   state). `delete_price` it. Expected: the row returns to
   `1 ALTX (USD 100.00, no price data)` and the three surfaces move
   together again. The fallback fires only when no price exists,
   and a void does not relieve the basis.

7. **Runway is on the same rule.** With the holding open at 100
   (end of step 6), read the runway line in `get_book_summary`.
   `unvoid_transaction` the sale and read it again. Expected: the
   liquid figure moves by exactly the holding's basis (100), the
   same number the asset line shows; no error, no `no price data`
   oddity in the runway text. Old code summed raw values here with
   its own loop.

Restore: `cp /tmp/alex-pre-185.gnucash samples/alex-chen-morales.gnucash`
with the server idle, or leave the drift; the builder regenerates
Alex from nothing.

## What is not in scope

- A transfer of an unpriced holding between two accounts at market
  value opens the destination's basis at the transfer value. That is
  the approximation the dashboard already made and the commit says
  so; it is not a finding here.
- GnuCash desktop's own balance sheet values an unpriced stock from
  its transaction-implied prices, so it will not match cost basis on
  Alex after step 3. Desktop is not an oracle for this loop.
- The `Unrealized Gain/Loss` label carrying a realized, unbooked
  gain is accepted behavior (review of #184, finding 5).

## Report

Write `BOOKKEEPER_REPORT_UNPRICED_COST_BASIS.md` beside this file:
the baseline triple, each step's observed numbers against expected,
and any place a workaround was needed. A workaround is a finding.
