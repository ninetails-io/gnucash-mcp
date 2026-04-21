"""Phase 11: reconcile Checking for January, June, and December 2025.

Per the spec, only these three months get reconciled — the other nine
months stay unreconciled to simulate a realistic mix where the user
caught up on statements sporadically.

The ``reconcile_account`` tool strictly requires:

    statement_balance == previously_reconciled + reconciling_quantities

To leave Feb-May / Jul-Nov unreconciled while still satisfying the
check, we compute a *synthetic* statement balance per call:

  - Jan: statement = sum of Jan Checking split quantities
           (including the opening-balance split)
  - Jun: statement = (Jan reconciled) + sum of Jun split quantities
  - Dec: statement = (Jan + Jun reconciled) + sum of Dec split quantities

This satisfies the tool's invariant for each call while the real
end-of-month bank balance diverges from the reconciled number. That
divergence is the whole point of the spec's "mix" exercise.

Uses gnucash_mcp.book.GnuCashBook directly.

Usage:
    uv run python scripts/synthetic_book/phase_11_reconcile.py
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from gnucash_mcp.book import GnuCashBook


DEFAULT_BOOK = os.environ.get(
    "GNUCASH_BOOK_PATH",
    "/Users/stephen/Finances/alex.chen-morales.gnucash",
)

ACCOUNT = "Assets:Current Assets:Checking Account"


def _to_date(d) -> date:
    if hasattr(d, "date") and callable(d.date):
        return d.date()
    return d


def month_splits(book, account_name: str, year: int, month: int):
    """Yield (guid, quantity_decimal) for splits on account whose
    transaction post_date falls in the given year+month and whose
    reconcile_state is not already 'y'.
    """
    import piecash
    for a in book.accounts:
        if a.fullname == account_name:
            for s in a.splits:
                t_date = _to_date(s.transaction.post_date)
                if (t_date.year == year and t_date.month == month
                        and s.reconcile_state != "y"):
                    yield s.guid, Decimal(str(s.quantity))
            return
    raise ValueError(f"Account not found: {account_name}")


def reconcile_month(
    gb: GnuCashBook, year: int, month: int, statement_day: int,
    running_reconciled_total: Decimal,
) -> Decimal:
    """Reconcile a single month. Returns the updated running total."""
    # Open briefly to collect the month's split guids and their
    # quantity sum.
    with gb.open(readonly=True) as book:
        pairs = list(month_splits(book, ACCOUNT, year, month))

    if not pairs:
        print(f"  {year}-{month:02d}: no unreconciled splits — skip")
        return running_reconciled_total

    guids = [p[0] for p in pairs]
    month_total = sum((p[1] for p in pairs), Decimal("0"))
    statement_balance = running_reconciled_total + month_total
    statement_date = date(year, month, statement_day)

    result = gb.reconcile_account(
        account_name=ACCOUNT,
        statement_date=statement_date,
        statement_balance=str(statement_balance),
        split_guids=guids,
    )
    print(f"  {year}-{month:02d}: reconciled {len(guids)} splits "
          f"(month net {month_total}); statement balance "
          f"{statement_balance}")
    return statement_balance


def run(book_path: Path) -> None:
    gb = GnuCashBook(str(book_path))

    running = Decimal("0")

    print("Reconciling Checking — January 2025")
    running = reconcile_month(gb, 2025, 1, 31, running)

    print("Reconciling Checking — June 2025")
    running = reconcile_month(gb, 2025, 6, 30, running)

    print("Reconciling Checking — December 2025")
    running = reconcile_month(gb, 2025, 12, 31, running)

    print("\nPhase 11 complete.")
    print(f"Total reconciled through Dec 31: ${running}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=DEFAULT_BOOK)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    book_path = Path(args.book)
    if not book_path.exists():
        raise SystemExit(f"Book not found: {book_path}")

    if not args.no_backup:
        backup = book_path.with_suffix(".pre-phase11.gnucash")
        shutil.copy2(book_path, backup)
        print(f"Backup created: {backup}\n")

    run(book_path)


if __name__ == "__main__":
    main()
