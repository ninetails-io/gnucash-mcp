# Bookkeeper Test Plan — GB-1 + v1.4 review fixes

**Branch under test:** `feat/gb1-per-period-rates`
**Written:** 2026-07-08, for a bookkeeper session (human or Claude)
driving the live MCP server. Every check has an exact expected
value or a mechanical pass condition, so the whole plan is
automatable.

---

## 0. Setup (Stephen does this part once)

1. Check out the branch and restart the server — **the running MCP
   process does not see code changes without a restart**:
   ```bash
   cd ~/Projects/gnucash-mcp
   git fetch && git switch feat/gb1-per-period-rates
   ```
2. Point the server at **copies** of the sample books, never real
   books and never the checked-in samples directly:
   ```bash
   mkdir -p /tmp/gb1-loop
   cp samples/alex-chen-morales.gnucash /tmp/gb1-loop/alex.gnucash
   cp samples/lin-wei.gnucash          /tmp/gb1-loop/linwei.gnucash
   cp samples/sabine-brenner.gnucash   /tmp/gb1-loop/sabine.gnucash
   export GNUCASH_BOOK_PATH="/tmp/gb1-loop/alex.gnucash:/tmp/gb1-loop/linwei.gnucash:/tmp/gb1-loop/sabine.gnucash"
   ```
   (Multi-book on purpose — Track D exercises it.)
3. Restart the MCP server / relaunch the client so the bookkeeper's
   connection is to the branch code. Confirm with `get_server_config`:
   it should report **multi-book** with 3 books and the
   `switch_book` tool present.

---

## What changed (context for the tester)

**GB-1 (this branch):** flow reports — `spending_by_category`,
`income_by_source`, `cash_flow` — now value every split at **its own
month's closing FX rate**, in single-period and `group_by` modes
alike. Consequence: the grand total for a range is the same whether
you ask for it single-period, by month, by quarter, or by year.
Previously single-period used the range-end rate and `group_by` used
each period's close, so the two modes could disagree on any book
with foreign-currency income/expense accounts. `balance_sheet` and
`net_worth` are **deliberately unchanged** (they value holdings as
of the report date — different semantics for stock vs flow).

**Also on this branch via develop (merged 2026-07-07/08, PRs
#114–#121):** transactional `switch_book`, per-book backup scoping +
per-book `.mcp` subdirs under `GNUCASH_LOG_DIR`, the localized
FX-account resolver fixes, the `is_retirement` slot, partial-period
`*` markers, and the strict Imbalance/Orphan matcher. Track D
spot-checks these.

---

## Track A — No-drift regression (the validated numbers must not move)

The three sample books have **zero** foreign-denominated flow
accounts with intra-range rate movement, so GB-1 must not change any
of their numbers. Run each call and compare **exactly**:

| # | Call | Book | Expected |
|---|------|------|----------|
| A1 | `spending_by_category(2026-01-01 → 2026-06-30)` | alex | TOTAL **63,290.64** |
| A2 | same | linwei | TOTAL **192,293.59** |
| A3 | same | sabine | TOTAL **29,967.70** |
| A4 | `cash_flow(2026-01-01 → 2026-06-30)` | alex | inflows **109784.79**, outflows **47365.88** |
| A5 | same | linwei | inflows **205734.30**, outflows **201556.36** |
| A6 | same | sabine | inflows **59689.33**, outflows **39499.49** |
| A7 | `get_book_summary` | each book | No new warnings vs. what the book showed on v1.4.0; net worth unchanged |

**Pass:** every number byte-equal. **Any drift at all is a
finding** — report the call, book, expected, actual.

## Track B — The GB-1 behavior itself (fresh-write probe)

Build the divergence case that the sample books don't contain, on
whichever test book you like (alex is fine — it's a copy):

1. `create_account`: `Expenses:EU Travel`, type EXPENSE,
   **commodity EUR** (create the EUR commodity first via
   `create_commodity` if the book lacks it — alex has EUR already).
2. `create_account`: `Assets:EUR Wallet`, type BANK, commodity EUR.
3. **Prerequisite (F4, found in the first loop):** alex ships
   with `user:market_data` EUR prices on these exact month-end
   dates; same-date prices shadow the ones you're about to plant
   (observed: 355.29 instead of 325.00). `get_prices` for EUR and
   `delete_price` any existing EUR quotes dated 2026-01-31,
   2026-02-28, or 2026-03-31 **before** planting yours.
4. `create_price` × 3 for EUR (quoted in the book default):
   - 2026-01-31 → **1.05**
   - 2026-02-28 → **1.10**
   - 2026-03-31 → **1.20**
5. Two cross-currency transactions (currency **EUR**):
   - 2026-01-15, splits: `Expenses:EU Travel` +100,
     `Assets:EUR Wallet` −100
   - 2026-02-10, splits: `Expenses:EU Travel` +200,
     `Assets:EUR Wallet` −200
   (Expect the `fx_no_reference` / rate-sanity warnings to stay
   quiet or informative — they are non-blocking.)
6. Now the assertions, range **2026-01-01 → 2026-03-31**, all four
   ways:
   - `spending_by_category(...)` single-period
   - `spending_by_category(..., group_by="month")`
   - `spending_by_category(..., group_by="quarter")`
   - `spending_by_category(..., group_by="year")`

**Pass conditions:**
- **B1:** the `EU Travel` line contributes exactly
  **100×1.05 + 200×1.10 = 325.00** in book currency — *not*
  300×1.20 = 360.00 (that's the old range-end policy; seeing 360
  means the branch isn't live — recheck Setup step 3).
- **B2:** the grand TOTAL is **identical across all four calls**
  (single == month == quarter == year).
- **B3:** in the `group_by="month"` table, EU Travel reads
  105.00 / 220.00 / 0.00 across Jan/Feb/Mar columns.
- **B4:** `balance_sheet(as_of=2026-03-31)` values the EUR Wallet
  at the **March** rate (1.20) — stock reports still use as-of
  valuation; this is correct, not a bug.

## Track C — Partial-period markers (v1.4 fix, visible in the same reports)

- **C1:** `spending_by_category(2026-01-15 → 2026-03-31,
  group_by="month")` → the first column header is **`2026-01*`**
  and a footnote `(* period only partially covered by the date
  range)` appears.
- **C2:** the same call with calendar-aligned dates
  (2026-01-01 → 2026-03-31) → **no** `*` anywhere in the header.

## Track D — Spot-checks of the merged v1.4 review fixes

- **D1 (switch_book):** `switch_book("lin")` → response contains
  the **⚠ CONTEXT RESET** banner naming the previous book, plus an
  orientation line (`… transactions | … CNY base currency`).
  `switch_book("linwei.gnucash")` again → starts with
  `Already on:` and **no** banner.
- **D2 (ambiguity):** `switch_book("a")` → error listing available
  books (matches alex only if unambiguous; if it matches, use
  `switch_book("zzz")` for the no-match error instead).
- **D3 (per-book logs):** with `GNUCASH_LOG_DIR` **unset** this is
  N/A — skip. If Stephen set it, verify on disk that each book has
  its own `{log_dir}/{book}.gnucash.mcp/audit/` daily file after a
  write to each book.
- **D4 (Imbalance matcher):** `create_account` a top-level BANK
  account named **`Açık Hesap`** → `get_book_summary` must **not**
  warn about a suspense/imbalance account for it, and runway must
  not silently drop. Delete it after.
- **D5 (retirement slot):** on alex, `set_account_slot` on a
  **new** placeholder subtree named something non-English (e.g.
  `Assets:Altersvorsorge`, with a BANK child holding a balance via
  a small transfer): set `is_retirement` = `"true"` on the parent →
  `get_book_summary` runway **excludes** the child's balance.
  Set `"false"` on the child → runway includes it again.
- **D6 (FX resolver, localized books):** on **sabine** (German
  chart, EUR default) this book's cross-currency features are
  hard to probe without USD flows; skip unless curious. The unit
  and oracle coverage is strong here.

## Session-resilience protocol (F7, field-tested 2026-07-09)

After ANY client network error or retried turn: re-verify
`get_server_config` and `get_book_summary` before trusting the
session's mental model of server state — tool calls from the lost
turn may have executed server-side (including `switch_book`). In
write-heavy phases, checkpoint with `create_backup` (manual stage,
labeled) before each write phase and end the client turn at each
checkpoint, so completed work is committed to the conversation
record. Worst case under this protocol is a one-command rollback.

## Reporting

One message (or file) with a table: check ID → PASS/FAIL → actual
value if FAIL. Anything ambiguous or merely *surprising* is worth a
line even if it technically passes — the v1.2/v1.3 loops caught
their best bugs from "this reads oddly," not from failed
assertions.

**Sign-off gate:** Tracks A and B fully green. C and D findings are
fixable follow-ups, not blockers, unless they show data loss.

After sign-off, the PR opens from `feat/gb1-per-period-rates` into
develop. The capture rig (`scripts/gb1/capture.py`) and its
committed before/after snapshots are the machine-verified half of
this plan; this loop is the half only a live server can give.
