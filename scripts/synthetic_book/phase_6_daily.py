"""Phase 6: daily/weekly transaction patterns for the Alex Chen-Morales synthetic book.

Generates ~450 transactions across 2025 from a deterministic seed:
  - ~52 weekend grocery runs (QFC / Fred Meyer / Safeway rotation)
  - ~52 weekly gas fills (Shell / 76 / Safeway Fuel / Chevron rotation)
  - ~260 weekday coffee shops (10 Seattle-area cafes)
  - ~32 restaurants (2-3x/month)
  - ~32 Amazon orders (2-3x/month, rotating categories)
  - 4 quarterly clothing purchases
  - ~35 monthly one-offs per the spec (vet visits, Valentine's, ski trip, etc.)

Writes directly through piecash (bypassing the MCP server) for speed and to
avoid audit log pollution on bulk synthetic data. One book open, one save.

Sign convention (verified against the MCP server's create_transaction):
  - Asset in:  amount = +X
  - Asset out: amount = -X
  - Expense accrual:      amount = +X
  - Expense reversal:     amount = -X
  - Liability increase:   amount = -X  (charging a credit card)
  - Liability decrease:   amount = +X  (paying off a card, refunds)

The book MUST NOT be open in the GnuCash UI while this runs — piecash
acquires an exclusive file lock.

Usage:
    uv run python scripts/synthetic_book/phase_6_daily.py --dry-run
    uv run python scripts/synthetic_book/phase_6_daily.py --month 1
    uv run python scripts/synthetic_book/phase_6_daily.py
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


# ── Vendors & categories ────────────────────────────────────────

GROCERY_VENDORS = ["QFC", "Fred Meyer", "Safeway"]

GAS_VENDORS = ["Shell", "76", "Safeway Fuel", "Chevron"]

COFFEE_VENDORS = [
    "Starbucks",
    "Victrola Coffee",
    "Caffé Ladro",
    "Stumptown Coffee",
    "Cherry Street Coffee",
    "Lighthouse Roasters",
    "Slate Coffee",
    "Tougo Coffee",
    "Analog Coffee",
    "Storyville Coffee",
]

RESTAURANTS = [
    "Canlis",
    "Westward",
    "Shaker & Spear",
    "Purple Café",
    "Tilikum Place Café",
    "Wild Ginger",
    "Kedai Makan",
    "Ba Bar",
    "Terra Plata",
    "Il Corvo",
    "Marination Ma Kai",
    "Bateau",
]

# Amazon category rotation: (item descriptor, expense account)
AMAZON_CATEGORIES = [
    ("household goods", "Expenses:Miscellaneous"),
    ("pet supplies", "Expenses:Pet:Food"),
    ("books", "Expenses:Education"),
    ("office supplies", "Expenses:Business:Software"),
    ("kitchen goods", "Expenses:Miscellaneous"),
]

CLOTHING_VENDORS = ["Target", "Nordstrom", "REI"]


# ── Helpers ─────────────────────────────────────────────────────

def vary(rng: random.Random, base: float, spread: float = 0.2) -> Decimal:
    """Return base ± (base * spread), rounded to cents."""
    factor = 1 + rng.uniform(-spread, spread)
    return Decimal(str(round(base * factor, 2)))


def uniform_cents(rng: random.Random, low: float, high: float) -> Decimal:
    """Uniform float in [low, high], rounded to cents."""
    return Decimal(str(round(rng.uniform(low, high), 2)))


def iter_weekends(year: int):
    """Yield one Saturday per weekend across the year."""
    d = date(year, 1, 1)
    while d.weekday() != 5:
        d += timedelta(days=1)
    while d.year == year:
        yield d
        d += timedelta(days=7)


def iter_weekdays(year: int):
    """Yield every Mon-Fri of the year."""
    d = date(year, 1, 1)
    while d.year == year:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def iter_mondays(year: int):
    """Yield every Monday of the year (anchor for weekly gas fills)."""
    d = date(year, 1, 1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    while d.year == year:
        yield d
        d += timedelta(days=7)


def days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    return (next_first - date(year, month, 1)).days


# ── Pattern generators ──────────────────────────────────────────
#
# Each generator yields dicts with date / description / splits, where
# splits is a list of (account_fullname, Decimal amount) pairs. Amounts
# follow the sign conventions documented at the top of this module.


def gen_groceries(rng: random.Random, year: int):
    for i, sat in enumerate(iter_weekends(year)):
        vendor = GROCERY_VENDORS[i % len(GROCERY_VENDORS)]
        day = sat + timedelta(days=rng.randint(0, 1))  # Sat or Sun
        if day.year != year:
            continue
        amount = vary(rng, 85.0)
        yield {
            "date": day,
            "description": vendor,
            "splits": [
                ("Assets:Current Assets:Checking Account", -amount),
                ("Expenses:Groceries", amount),
            ],
        }


def gen_gas(rng: random.Random, year: int):
    for i, mon in enumerate(iter_mondays(year)):
        vendor = GAS_VENDORS[i % len(GAS_VENDORS)]
        day = mon + timedelta(days=rng.randint(0, 6))
        if day.year != year:
            continue
        amount = vary(rng, 52.0)
        yield {
            "date": day,
            "description": f"{vendor} Gas",
            "splits": [
                ("Assets:Current Assets:Checking Account", -amount),
                ("Expenses:Auto:Fuel", amount),
            ],
        }


def gen_coffee(rng: random.Random, year: int):
    for d in iter_weekdays(year):
        vendor = rng.choice(COFFEE_VENDORS)
        amount = vary(rng, 5.50)
        yield {
            "date": d,
            "description": vendor,
            "splits": [
                ("Liabilities:Credit Card:Chase Sapphire", -amount),
                ("Expenses:Dining", amount),
            ],
        }


def gen_restaurants(rng: random.Random, year: int):
    for month in range(1, 13):
        count = rng.randint(2, 3)
        for _ in range(count):
            day = date(year, month, rng.randint(1, days_in_month(year, month)))
            vendor = rng.choice(RESTAURANTS)
            amount = uniform_cents(rng, 45.0, 95.0)
            # ~60% on Chase, 40% on Checking
            if rng.random() < 0.6:
                source = "Liabilities:Credit Card:Chase Sapphire"
            else:
                source = "Assets:Current Assets:Checking Account"
            yield {
                "date": day,
                "description": vendor,
                "splits": [
                    (source, -amount),
                    ("Expenses:Dining", amount),
                ],
            }


def gen_amazon(rng: random.Random, year: int):
    for month in range(1, 13):
        count = rng.randint(2, 3)
        for _ in range(count):
            day = date(year, month, rng.randint(1, days_in_month(year, month)))
            descriptor, expense_acct = rng.choice(AMAZON_CATEGORIES)
            amount = uniform_cents(rng, 15.0, 120.0)
            yield {
                "date": day,
                "description": f"Amazon.com - {descriptor}",
                "splits": [
                    ("Liabilities:Credit Card:Chase Sapphire", -amount),
                    (expense_acct, amount),
                ],
            }


def gen_clothing(rng: random.Random, year: int):
    # Quarterly = last month of each quarter
    for month in (3, 6, 9, 12):
        day = date(year, month, rng.randint(10, 20))
        vendor = rng.choice(CLOTHING_VENDORS)
        amount = uniform_cents(rng, 35.0, 150.0)
        yield {
            "date": day,
            "description": vendor,
            "splits": [
                ("Liabilities:Credit Card:Chase Sapphire", -amount),
                ("Expenses:Clothing", amount),
            ],
        }


# Monthly seasonal one-offs per the spec. Each tuple:
#   (month, day, description, amount_str, source_account, target_account)
# - amount_str positive = normal purchase:
#       source gets -amt, target gets +amt
# - amount_str negative = refund/reversal:
#       source gets +|amt| (pays down liability / credits asset),
#       target gets -|amt| (reverses expense)
_CHASE = "Liabilities:Credit Card:Chase Sapphire"
_AMEX = "Liabilities:Credit Card:Business Amex"
_CHECKING = "Assets:Current Assets:Checking Account"

MONTHLY_EVENTS = [
    # Jan
    (1, 15, "Byte's vet visit", "180", _CHECKING, "Expenses:Pet:Vet"),
    (1, 10, "New Year gift return", "-45", _CHASE, "Expenses:Gifts"),
    # Feb
    (2, 14, "Valentine's dinner - Canlis", "165", _CHASE, "Expenses:Dining"),
    (2, 22, "Ski trip - Snoqualmie", "340", _CHASE, "Expenses:Travel"),
    # Mar
    (3, 5, "TurboTax Home & Business", "89", _CHASE, "Expenses:Subscriptions"),
    (3, 18, "Spring clothing", "210", _CHASE, "Expenses:Clothing"),
    # Apr
    (4, 12, "Byte's annual checkup", "320", _CHECKING, "Expenses:Pet:Vet"),
    # May
    (5, 24, "Memorial Day BBQ supplies", "95", _CHECKING, "Expenses:Groceries"),
    (5, 10, "Garden supplies", "67", _CHASE, "Expenses:Housing:Maintenance"),
    # Jun
    (6, 28, "Pride festival food", "120", _CHASE, "Expenses:Dining"),
    (6, 28, "Pride festival merch", "85", _CHASE, "Expenses:Miscellaneous"),
    (6, 15, "Anniversary dinner", "225", _CHASE, "Expenses:Dining"),
    # Jul
    (7, 4, "4th of July party supplies", "145", _CHECKING, "Expenses:Dining"),
    (7, 15, "Summer road trip - lodging", "890", _CHASE, "Expenses:Travel"),
    (7, 18, "Road trip fuel", "340", _CHASE, "Expenses:Auto:Fuel"),
    # Aug
    (8, 8, "Dell U2725D monitor", "450", _AMEX, "Expenses:Business:Software"),
    # Sep
    (9, 3, "PyCon US conference ticket", "799", _AMEX,
     "Expenses:Business:Professional Development"),
    (9, 1, "Labor Day camping", "280", _CHASE, "Expenses:Travel"),
    # Oct
    (10, 28, "Halloween supplies", "65", _CHECKING, "Expenses:Miscellaneous"),
    (10, 20, "Byte vet visit", "150", _CHECKING, "Expenses:Pet:Vet"),
    # Nov
    (11, 25, "Thanksgiving groceries", "185", _CHECKING, "Expenses:Groceries"),
    (11, 28, "Black Friday - Target", "105", _CHASE, "Expenses:Clothing"),
    (11, 28, "Black Friday - REI", "125", _CHASE, "Expenses:Clothing"),
    (11, 29, "Black Friday - Nordstrom", "135", _CHASE, "Expenses:Clothing"),
    (11, 29, "Cyber Monday - Amazon", "55", _CHASE, "Expenses:Miscellaneous"),
    # Dec - holiday gifts spread across 8 transactions
    (12, 10, "Holiday gift - Robin", "120", _CHASE, "Expenses:Gifts"),
    (12, 12, "Holiday gift - Mom", "85", _CHASE, "Expenses:Gifts"),
    (12, 14, "Holiday gift - Dad", "95", _CHASE, "Expenses:Gifts"),
    (12, 15, "Holiday gift - sister", "65", _CHASE, "Expenses:Gifts"),
    (12, 18, "Holiday gift - coworkers", "75", _CHASE, "Expenses:Gifts"),
    (12, 20, "Holiday gift - friends group", "80", _CHASE, "Expenses:Gifts"),
    (12, 22, "Holiday gift - nieces", "90", _CHASE, "Expenses:Gifts"),
    (12, 23, "Holiday gift - last-minute", "40", _CHASE, "Expenses:Gifts"),
    (12, 26, "Holiday travel - flights", "580", _CHASE, "Expenses:Travel"),
    (12, 30, "Year-end donation - NAMI", "500", _CHECKING, "Expenses:Charity"),
]


def gen_monthly_flavors(year: int):
    for month, day, desc, amt_str, source, target in MONTHLY_EVENTS:
        amt = Decimal(amt_str)
        if amt < 0:
            # Refund: source gets credited (pay down / money back),
            # target expense gets reversed.
            yield {
                "date": date(year, month, day),
                "description": desc,
                "splits": [
                    (source, abs(amt)),
                    (target, -abs(amt)),
                ],
            }
        else:
            yield {
                "date": date(year, month, day),
                "description": desc,
                "splits": [
                    (source, -amt),
                    (target, amt),
                ],
            }


# ── Main driver ─────────────────────────────────────────────────

def collect_transactions(year: int, seed: int) -> list[dict]:
    """Deterministically generate all Phase 6 transactions for the year.

    Each generator gets its own RNG derived from the master seed so that
    re-running with --month=N produces the same transactions as the
    full-year run would have produced for month N.
    """
    txns: list[dict] = []
    txns.extend(gen_groceries(random.Random(seed + 1), year))
    txns.extend(gen_gas(random.Random(seed + 2), year))
    txns.extend(gen_coffee(random.Random(seed + 3), year))
    txns.extend(gen_restaurants(random.Random(seed + 4), year))
    txns.extend(gen_amazon(random.Random(seed + 5), year))
    txns.extend(gen_clothing(random.Random(seed + 6), year))
    txns.extend(gen_monthly_flavors(year))
    txns.sort(key=lambda t: (t["date"], t["description"]))
    return txns


def print_summary(txns: list[dict]) -> None:
    per_month = Counter(t["date"].month for t in txns)
    total_amount = Decimal("0")
    for t in txns:
        # Half the sum of absolute values = transaction amount
        total_amount += sum(abs(v) for _, v in t["splits"]) / 2
    print(f"  Total transactions: {len(txns)}")
    print(f"  Total gross amount: ${total_amount:,.2f}")
    print("  Per month:")
    for m in range(1, 13):
        print(f"    {date(2000, m, 1).strftime('%b'):>4}: {per_month[m]:4d}")


def dry_run(txns: list[dict]) -> None:
    print("DRY RUN — no changes will be written")
    print()
    print_summary(txns)
    if not txns:
        return
    print()
    print("  First 5:")
    for t in txns[:5]:
        gross = sum(abs(v) for _, v in t["splits"]) / 2
        print(f"    {t['date']} {t['description']:<40} ${gross:>8}")
    if len(txns) > 10:
        print("    ...")
        print("  Last 5:")
        for t in txns[-5:]:
            gross = sum(abs(v) for _, v in t["splits"]) / 2
            print(f"    {t['date']} {t['description']:<40} ${gross:>8}")


def write_transactions(book_path: Path, txns: list[dict]) -> None:
    book = piecash.open_book(str(book_path), readonly=False)
    try:
        usd = book.commodities(mnemonic="USD")

        # Cache account lookups — walking book.accounts for every split
        # would be O(N*M). One warm-up pass, then dict hits.
        acct_cache: dict[str, piecash.Account] = {}
        for a in book.accounts:
            acct_cache[a.fullname] = a

        missing = set()
        for t in txns:
            for acct_name, _ in t["splits"]:
                if acct_name not in acct_cache:
                    missing.add(acct_name)
        if missing:
            raise ValueError(
                "Accounts missing from book:\n  "
                + "\n  ".join(sorted(missing))
            )

        created = 0
        per_month = {m: 0 for m in range(1, 13)}
        for t in txns:
            splits = [
                piecash.Split(account=acct_cache[name], value=value)
                for name, value in t["splits"]
            ]
            piecash.Transaction(
                currency=usd,
                description=t["description"],
                post_date=t["date"],
                splits=splits,
            )
            created += 1
            per_month[t["date"].month] += 1
            if created % 50 == 0:
                print(f"    ... {created} staged")

        print(f"  Saving book ({created} transactions)...")
        book.save()
        print(f"  Done. Per month: "
              f"{', '.join(f'{m:02d}:{per_month[m]}' for m in range(1, 13))}")
    finally:
        book.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=DEFAULT_BOOK,
                        help=f"Book path (default: {DEFAULT_BOOK})")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--month", type=int, default=None,
                        help="Only write this month (1-12). Useful for sanity checks.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be created without writing.")
    parser.add_argument("--no-backup", action="store_true",
                        help="Skip creating a .pre-phase6 backup copy.")
    args = parser.parse_args()

    book_path = Path(args.book)
    if not book_path.exists():
        raise SystemExit(f"Book not found: {book_path}")

    print(f"Generating Phase 6 transactions for {args.year} (seed={args.seed})")
    txns = collect_transactions(args.year, args.seed)

    if args.month is not None:
        txns = [t for t in txns if t["date"].month == args.month]
        print(f"Filtered to month={args.month}: {len(txns)} transactions")

    if args.dry_run:
        dry_run(txns)
        return

    print_summary(txns)
    print()

    if not args.no_backup:
        backup = book_path.with_suffix(f".pre-phase6-{args.month or 'all'}.gnucash")
        shutil.copy2(book_path, backup)
        print(f"Backup created: {backup}")

    print(f"Writing to {book_path}...")
    write_transactions(book_path, txns)
    print("OK")


if __name__ == "__main__":
    main()
