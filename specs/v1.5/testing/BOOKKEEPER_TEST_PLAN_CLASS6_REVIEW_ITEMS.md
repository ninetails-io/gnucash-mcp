# Bookkeeper live loop — class-6 review items (fix/class-6-review-items)

Branch under test: `fix/class-6-review-items`, from develop at #193.
What it claims, from class 6 of the 2026-09-04 whole-tree review:

- **Lot-held splits need force to delete** (single and batch), the
  gate `replace_splits` already had; `update_transaction` gets the
  same gate for an amount change on a lot split.
- **A lot's cached closed flag resets whenever a split leaves it or
  its amount changes** — delete, `replace_splits`, void, unvoid, and
  split edits — as GnuCash's `mark_split` / `gnc_lot_remove_split` do.
  A sold-out lot whose sell is voided stops reading closed.
- **`replace_splits` validates through `_validate_transaction_splits`**
  before anything mutates; its errors name the ref as given.
- **A NULL transaction description breaks nothing.** GnuCash's schema
  allows it; one such row used to crash search and every create.
- **`vendor_spending_report`'s docstring states the rates it uses.**

No report number may move: every change is a guard, a flag, a message,
or a docstring.

## Setup

Work on copies only. `specs/v1.5/testing/class6_loop/` holds the
harness: `mcpcall.py` runs a list of tool calls over stdio;
`loop.py` runs Part B end to end with a PASS/FAIL per check, starting
the server itself (`GNUCASH_MCP_MODULES=all`). `REPO=<worktree>` points
it at another checkout, which is how the develop fingerprint is taken.

## Part A — the oracles did not move

Capture rig, develop worktree vs this checkout, one fresh copy of each
book per side:

    uv run python scripts/branch_1/capture.py --book <copy> --out <dir>

Lin Wei takes `--today 2025-12-31 --historical 2025-06-30`, typed
literally (zsh does not word-split an unquoted variable). Expected:
the diffs hold only the `book_path` line.

## Part B — the live loop on Alex (`loop.py`)

0. **Make a sold-out lot.** Alex has none. Sell the 2 shares of
   `AAPL buy 2026-05-10` (`73ccbdb4`) at cost through
   `create_transactions` with a `qty` column; `assign_split_to_lot`
   the sell into the lot. Expected: the lot auto-closes.
1. **Delete refuses.** `delete_transaction` on the sell, then the
   list form. Expected: `validation_error` naming `AAPL buy
   2026-05-10 (Assets:Investments:Brokerage:AAPL)`; the list form
   adds `(nothing deleted)`; the sell is still there.
2. **Void and unvoid.** Void the sell: `get_lot` reads open and the
   open-only `list_lots` shows it. Unvoid: closed again.
3. **replace_splits.** Unforced: refused, naming the lot. Forced with
   the same amounts: warning names the lot, and the lot reopens.
   Missing `quantity` on the AAPL leg: the error names the ref as
   typed. Unbalanced: still refused. Re-assign the new sell split:
   the lot closes.
4. **Forced delete.** Response carries `lot_splits_affected: 1`; the
   lot is open with its 2 shares.
5. **An invoice payment** (000016, Emerald, payment `9aaa4c7e`).
   Unforced delete: refused, naming `Invoice 000016`. Void: the
   invoice reads `posted`, 3,500 due, back on
   `get_outstanding_documents`. Unvoid: `paid`.
6. **A bill payment** (000006, BookkeepingCo, payment `125cb915`).
   Unforced delete refused; forced delete goes through; the bill
   reads `posted`, 450 due.
7. **A NULL description.** Before the server starts, raw SQL nulls
   `4ddbaf82`'s description. Search, a `create_transactions` dry run,
   and `get_book_summary` all answer without an error.
8. **The audit log** for the run: each forced delete carries a
   `Forced:` line; refusals render as `ERROR` lines with the lot named.

Fingerprint: the same script on develop fails at step 0 (the NULL row
crashes the create). Without step 7's NULL row, develop's first
unforced delete removes the lot-held sell.

## Part C — GnuCash's own engine

`gnucash-cli --report run --name "Balance Sheet"` on the looped copy
and on a pristine copy. Expected: both load; the same warnings on
each; every difference between them explained by what the loop did.
Then the server's `balance_sheet` on the looped copy agrees with
GnuCash's to the cent on the accounts the loop touched.

## Routed around

Name every workaround taken, with the tool that forced it.
