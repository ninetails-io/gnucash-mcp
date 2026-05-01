"""Phase 13: 1000-transaction volume stress test.

Generates 1000 small ($1.50–$15.00) transactions spread across 2025,
from Checking, rotating through 10 casual-spending vendor labels and
alternating between Expenses:Dining and Expenses:Miscellaneous.

This is the 'stress this book against every list / search / aggregate'
phase. The spec positions it last because it changes performance
characteristics — everything else should already be verified correct
before Phase 13 lands.

All transactions use a deterministic seed so re-runs are reproducible.
Uses piecash directly (single open/save) for speed.

Usage:
    uv run python scripts/synthetic_book/phase_13_volume.py --dry-run
    uv run python scripts/synthetic_book/phase_13_volume.py
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import piecash


DEFAULT_BOOK = os.environ.get(
    "GNUCASH_BOOK_PATH",
    "/Users/stephen/Finances/alex.chen-morales.gnucash",
)

CHECKING = "Assets:Current Assets:Checking Account"

VENDORS = [
    "Morning Coffee",
    "Lunch Spot",
    "Parking Meter",
    "Vending Machine",
    "Corner Store",
    "Food Cart",
    "Transit Pass",
    "Drug Store",
    "Dry Cleaner",
    "News Stand",
]

# Categorization: dining-ish vendors → Dining; everything else → Miscellaneous
DINING_VENDORS = {
    "Morning Coffee", "Lunch Spot", "Food Cart",
    "Vending Machine", "Corner Store",
}

# Spec: 1000 transactions spread across the year, ~3/day average.
TOTAL_TXNS = 1000
YEAR = 2025


def generate_transactions(seed: int = 42):
    """Yield dicts with date/description/amount/target, spread across 2025."""
    rng = random.Random(seed)

    year_start = date(YEAR, 1, 1)
    year_end = date(YEAR, 12, 31)
    total_days = (year_end - year_start).days + 1  # 365

    for i in range(TOTAL_TXNS):
        # Distribute transactions across the year by random day.
        day_offset = rng.randrange(total_days)
        tx_date = year_start + timedelta(days=day_offset)

        vendor = rng.choice(VENDORS)
        amount = Decimal(str(round(rng.uniform(1.50, 15.00), 2)))
        target = (
            "Expenses:Dining" if vendor in DINING_VENDORS
            else "Expenses:Miscellaneous"
        )

        yield {
            "date": tx_date,
            "description": vendor,
            "amount": amount,
            "target": target,
        }


def summarize(txns: list[dict]) -> None:
    per_month = Counter(t["date"].month for t in txns)
    per_vendor = Counter(t["description"] for t in txns)
    per_target = Counter(t["target"] for t in txns)
    total_amount = sum((t["amount"] for t in txns), Decimal("0"))

    print(f"  Total: {len(txns)} txns, gross flow ${total_amount:,.2f}")
    print(f"  Avg: ${total_amount / len(txns):.2f} per txn, "
          f"{len(txns) / 12:.1f} per month")
    print("  Per-vendor:")
    for v, n in sorted(per_vendor.items()):
        print(f"    {v:<16} {n:4d}")
    print("  Per-target:")
    for t, n in sorted(per_target.items()):
        print(f"    {t:<28} {n:4d}")
    print("  Per-month:")
    for m in range(1, 13):
        print(f"    {date(2000, m, 1).strftime('%b'):>4}: {per_month[m]:4d}")


def write_all(book_path: Path, txns: list[dict]) -> None:
    book = piecash.open_book(str(book_path), readonly=False)
    try:
        usd = book.commodities.get(mnemonic="USD")
        acct_cache: dict[str, piecash.Account] = {}
        for a in book.accounts:
            acct_cache[a.fullname] = a

        # Verify all required accounts exist before starting.
        for path in [CHECKING, "Expenses:Dining", "Expenses:Miscellaneous"]:
            if path not in acct_cache:
                raise ValueError(f"Required account missing: {path}")

        created = 0
        for t in txns:
            piecash.Transaction(
                currency=usd,
                description=t["description"],
                post_date=t["date"],
                splits=[
                    piecash.Split(
                        account=acct_cache[CHECKING],
                        value=-t["amount"],
                    ),
                    piecash.Split(
                        account=acct_cache[t["target"]],
                        value=t["amount"],
                    ),
                ],
            )
            created += 1
            if created % 200 == 0:
                print(f"    ... {created} staged")

        print(f"  Saving book ({created} transactions)...")
        book.save()
        print("  Done.")
    finally:
        book.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=DEFAULT_BOOK)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    book_path = Path(args.book)
    if not book_path.exists():
        raise SystemExit(f"Book not found: {book_path}")

    print(f"Generating volume txns for {YEAR} (seed={args.seed})")
    txns = list(generate_transactions(seed=args.seed))
    txns.sort(key=lambda t: t["date"])
    summarize(txns)

    if args.dry_run:
        return

    if not args.no_backup:
        backup = book_path.with_suffix(".pre-phase13.gnucash")
        shutil.copy2(book_path, backup)
        print(f"\nBackup created: {backup}")

    print()
    write_all(book_path, txns)


if __name__ == "__main__":
    main()
