"""Phase 12: edge-case transactions exercising correction tools.

Spec items exercised:
  1. Void a transaction (Mar 15 $500 to "Wrong Vendor")
  2. Recategorize via replace_splits (Apr 20 $89 Office Supplies → Business:Software)
  3. Returned purchase (Aug 10 $249 Electronics, Aug 22 credit -$249)
  4. Partial refund (Sep 5 $120 Department Store, Sep 15 $45 refund)
  5. Split correction via replace_splits (Oct 1 $150 Dining → $120 + $30 Gifts)
  6. Delete a duplicate grocery (Nov 15 dup created + deleted)

Item 7 from the spec (multi-currency payment) was already exercised in
Phase 7 with the now-fixed pay_invoice path.

Uses gnucash_mcp.book.GnuCashBook directly so we get the real
void_transaction / replace_splits / delete_transaction tool logic.

Usage:
    uv run python scripts/synthetic_book/phase_12_edge_cases.py
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

from gnucash_mcp.book import GnuCashBook


DEFAULT_BOOK = os.environ.get(
    "GNUCASH_BOOK_PATH",
    "/Users/stephen/Finances/alex.chen-morales.gnucash",
)


def run(book_path: Path) -> None:
    gb = GnuCashBook(str(book_path))

    print("Step 1: Void a $500 payment to Wrong Vendor")
    r = gb.create_transaction(
        description="Payment to Wrong Vendor (mis-routed)",
        trans_date=date(2025, 3, 15),
        splits=[
            {"account": "Assets:Current Assets:Checking Account",
             "amount": "-500.00"},
            {"account": "Expenses:Miscellaneous", "amount": "500.00"},
        ],
        check_duplicates=False,
    )
    # r["guid"] is a short prefix; void_transaction accepts 8+ char prefixes.
    gb.void_transaction(guid=r["guid"], reason="Paid wrong vendor")
    print(f"  Voided {r['guid']}")

    print("Step 2: Recategorize $89 Office Supplies → Business:Software")
    r = gb.create_transaction(
        description="Office Supplies (Amazon)",
        trans_date=date(2025, 4, 20),
        splits=[
            {"account": "Liabilities:Credit Card:Chase Sapphire",
             "amount": "-89.00"},
            {"account": "Expenses:Miscellaneous", "amount": "89.00"},
        ],
        check_duplicates=False,
    )
    gb.replace_splits(
        guid=r["guid"],
        splits=[
            {"account": "Liabilities:Credit Card:Chase Sapphire",
             "amount": "-89.00"},
            {"account": "Expenses:Business:Software", "amount": "89.00"},
        ],
    )
    print(f"  Recategorized {r['guid']}")

    print("Step 3: Returned purchase — Electronics Store $249 → refund -$249")
    gb.create_transaction(
        description="Electronics Store",
        trans_date=date(2025, 8, 10),
        splits=[
            {"account": "Liabilities:Credit Card:Chase Sapphire",
             "amount": "-249.00"},
            {"account": "Expenses:Miscellaneous", "amount": "249.00"},
        ],
        check_duplicates=False,
    )
    gb.create_transaction(
        description="Electronics Store — return",
        trans_date=date(2025, 8, 22),
        splits=[
            {"account": "Liabilities:Credit Card:Chase Sapphire",
             "amount": "249.00"},      # positive on CC: pays down debt
            {"account": "Expenses:Miscellaneous", "amount": "-249.00"},  # reverses expense
        ],
        check_duplicates=False,
    )
    print("  Purchase + full refund booked")

    print("Step 4: Partial refund — Department Store $120 → $45 refunded")
    gb.create_transaction(
        description="Department Store",
        trans_date=date(2025, 9, 5),
        splits=[
            {"account": "Liabilities:Credit Card:Chase Sapphire",
             "amount": "-120.00"},
            {"account": "Expenses:Clothing", "amount": "120.00"},
        ],
        check_duplicates=False,
    )
    gb.create_transaction(
        description="Department Store — partial refund",
        trans_date=date(2025, 9, 15),
        splits=[
            {"account": "Liabilities:Credit Card:Chase Sapphire",
             "amount": "45.00"},
            {"account": "Expenses:Clothing", "amount": "-45.00"},
        ],
        check_duplicates=False,
    )
    print("  Purchase + partial refund booked")

    print("Step 5: Split correction — $150 Dining → $120 Dining + $30 Gifts")
    r = gb.create_transaction(
        description="Dinner + gift card combo purchase",
        trans_date=date(2025, 10, 1),
        splits=[
            {"account": "Liabilities:Credit Card:Chase Sapphire",
             "amount": "-150.00"},
            {"account": "Expenses:Dining", "amount": "150.00"},
        ],
        check_duplicates=False,
    )
    gb.replace_splits(
        guid=r["guid"],
        splits=[
            {"account": "Liabilities:Credit Card:Chase Sapphire",
             "amount": "-150.00"},
            {"account": "Expenses:Dining", "amount": "120.00"},
            {"account": "Expenses:Gifts", "amount": "30.00"},
        ],
    )
    print(f"  Split correction on {r['guid']}")

    print("Step 6: Delete a duplicate grocery")
    r = gb.create_transaction(
        description="QFC (duplicate of 11/15)",
        trans_date=date(2025, 11, 15),
        splits=[
            {"account": "Assets:Current Assets:Checking Account",
             "amount": "-92.50"},
            {"account": "Expenses:Groceries", "amount": "92.50"},
        ],
        check_duplicates=False,
    )
    gb.delete_transaction(guid=r["guid"])
    print(f"  Created + deleted {r['guid']}")

    print("\nPhase 12 complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=DEFAULT_BOOK)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    book_path = Path(args.book)
    if not book_path.exists():
        raise SystemExit(f"Book not found: {book_path}")

    if not args.no_backup:
        backup = book_path.with_suffix(".pre-phase12.gnucash")
        shutil.copy2(book_path, backup)
        print(f"Backup created: {backup}\n")

    run(book_path)


if __name__ == "__main__":
    main()
