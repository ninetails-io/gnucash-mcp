"""Phase 5: instantiate scheduled transactions across 2025.

Generates ~180 transactions derived from the 20 scheduled templates created
in Phase 4, with month-specific variations the static templates can't
capture:

  - 26 biweekly paychecks for Robin, with overtime bumps every 3rd or 4th
    paycheck ($200-400 added to gross, tax splits scaled proportionally)
  - 12 mortgage payments with H1 vs H2 amortization splits
  - 12 auto loan payments with H1 vs H2 amortization splits
  - 12 × 10 = 120 "simple" monthly bills (HOA, utilities, streaming,
    phone, pet food, AWS, WeWork)
  - 4 quarterly umbrella insurance premiums (Jan/Apr/Jul/Oct)
  - 2 property tax halves (Apr 30 and Oct 31)
  - 4 estimated federal tax payments at real IRS deadlines
    (Apr 15, Jun 15, Sep 15, Jan 15 of 2026)

Per the spec: paycheck overtime variants are created as direct
transactions rather than instantiated from the schedule, because the
schedule template is fixed-amount.

Sign conventions match phase_6_daily.py and the opening-balance checks:
asset in = +, asset out = -, expense accrual = +, liability increase
= - (charge), liability decrease = + (pay down).

Usage:
    uv run python scripts/synthetic_book/phase_5_schedules.py --dry-run
    uv run python scripts/synthetic_book/phase_5_schedules.py --month 1
    uv run python scripts/synthetic_book/phase_5_schedules.py
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

# ── Accounts (short aliases) ───────────────────────────────────

CHECKING = "Assets:Current Assets:Checking Account"
HSA = "Assets:Investments:HSA"
CHASE = "Liabilities:Credit Card:Chase Sapphire"
AMEX = "Liabilities:Credit Card:Business Amex"
SALARY = "Income:Salary"

# ── Paycheck constants ─────────────────────────────────────────

BASELINE_GROSS = Decimal("3269.23")
FIXED_HEALTH = Decimal("145.00")
FIXED_HSA = Decimal("44.14")

# Ratios from the baseline spec (3269.23 gross):
#   checking=2450, fed=380, SS=202.69, medicare=47.40
# Fixed (don't scale with overtime): health $145, HSA $44.14
FED_PCT = Decimal("380.00") / BASELINE_GROSS
SS_PCT = Decimal("202.69") / BASELINE_GROSS
MED_PCT = Decimal("47.40") / BASELINE_GROSS


def paycheck_splits(gross: Decimal) -> list[tuple[str, Decimal]]:
    """Build paycheck splits for the given gross amount.

    Federal, SS, Medicare scale with gross. Health insurance and HSA
    are fixed contributions. Checking absorbs the residual so the
    transaction balances exactly.
    """
    fed = (gross * FED_PCT).quantize(Decimal("0.01"))
    ss = (gross * SS_PCT).quantize(Decimal("0.01"))
    med = (gross * MED_PCT).quantize(Decimal("0.01"))
    checking = gross - fed - ss - med - FIXED_HEALTH - FIXED_HSA
    return [
        (SALARY, -gross),
        (CHECKING, checking),
        ("Expenses:Taxes:Federal", fed),
        ("Expenses:Taxes:Social Security", ss),
        ("Expenses:Taxes:Medicare", med),
        ("Expenses:Insurance:Health", FIXED_HEALTH),
        (HSA, FIXED_HSA),
    ]


# ── Amortization tables ────────────────────────────────────────

MORTGAGE_TOTAL = Decimal("2485.00")
MORTGAGE_H1_INTEREST = Decimal("2006.25")
MORTGAGE_H1_PRINCIPAL = Decimal("478.75")
MORTGAGE_H2_INTEREST = Decimal("1991.50")
MORTGAGE_H2_PRINCIPAL = Decimal("493.50")

AUTO_TOTAL = Decimal("365.00")
AUTO_H1_INTEREST = Decimal("84.63")
AUTO_H1_PRINCIPAL = Decimal("280.37")
AUTO_H2_INTEREST = Decimal("76.20")
AUTO_H2_PRINCIPAL = Decimal("288.80")


# ── Date iterators ─────────────────────────────────────────────

def iter_biweekly(start: date, count: int):
    d = start
    for _ in range(count):
        yield d
        d += timedelta(days=14)


def iter_monthly(year: int, day: int):
    """Yield the given day of each month in the year (clamped if absent)."""
    for month in range(1, 13):
        d_val = day
        # Clamp for months with fewer days (e.g., day=31 in Feb)
        while True:
            try:
                yield date(year, month, d_val)
                break
            except ValueError:
                d_val -= 1


# ── Generators ─────────────────────────────────────────────────

def gen_paychecks(rng: random.Random, year: int):
    """26 biweekly paychecks, with overtime on ~every 3rd-4th."""
    start = date(year, 1, 10)
    for i, d in enumerate(iter_biweekly(start, 26)):
        if d.year != year:
            continue
        # Overtime: every 3rd or 4th paycheck gets a bump
        overtime = Decimal("0")
        if i > 0 and i % rng.choice([3, 4]) == 0:
            overtime = Decimal(str(rng.randint(200, 400)))
        gross = BASELINE_GROSS + overtime
        desc = "Robin's Paycheck (UW Medical)"
        if overtime:
            desc += f" - ${overtime} overtime"
        yield {
            "date": d,
            "description": desc,
            "splits": paycheck_splits(gross),
        }


def gen_mortgage(year: int):
    for i, d in enumerate(iter_monthly(year, 1)):
        h2 = i >= 6  # July onward
        interest = MORTGAGE_H2_INTEREST if h2 else MORTGAGE_H1_INTEREST
        principal = MORTGAGE_H2_PRINCIPAL if h2 else MORTGAGE_H1_PRINCIPAL
        yield {
            "date": d,
            "description": "Mortgage Payment",
            "splits": [
                (CHECKING, -MORTGAGE_TOTAL),
                ("Expenses:Interest:Mortgage Interest", interest),
                ("Liabilities:Loans:Mortgage", principal),
            ],
        }


def gen_auto_loan(year: int):
    for i, d in enumerate(iter_monthly(year, 5)):
        h2 = i >= 6
        interest = AUTO_H2_INTEREST if h2 else AUTO_H1_INTEREST
        principal = AUTO_H2_PRINCIPAL if h2 else AUTO_H1_PRINCIPAL
        yield {
            "date": d,
            "description": "Auto Loan Payment",
            "splits": [
                (CHECKING, -AUTO_TOTAL),
                ("Expenses:Interest:Auto Loan Interest", interest),
                ("Liabilities:Loans:Auto Loan", principal),
            ],
        }


# Simple monthly: description, amount, day-of-month, source, target
SIMPLE_MONTHLY = [
    ("HOA Dues", "425.00", 1, CHECKING, "Expenses:Housing:HOA"),
    ("Internet - Comcast", "79.99", 3, CHECKING, "Expenses:Utilities:Internet"),
    ("Streaming Bundle", "45.97", 8, CHECKING, "Expenses:Streaming"),
    ("Phone - T-Mobile", "140.00", 12, CHECKING, "Expenses:Utilities:Phone"),
    ("Electric - Seattle City Light", "95.00", 15, CHECKING, "Expenses:Utilities:Electric"),
    ("Gas - Puget Sound Energy", "65.00", 15, CHECKING, "Expenses:Utilities:Gas"),
    ("Water/Sewer - SPU", "55.00", 15, CHECKING, "Expenses:Utilities:Water"),
    ("Pet Food - Chewy", "48.00", 20, CHECKING, "Expenses:Pet:Food"),
    ("AWS Cloud Hosting", "125.00", 1, AMEX, "Expenses:Business:Cloud Hosting"),
    ("WeWork Coworking", "250.00", 1, AMEX, "Expenses:Business:Coworking"),
]


def gen_simple_monthly(year: int):
    for name, amount_str, day, source, target in SIMPLE_MONTHLY:
        amt = Decimal(amount_str)
        for d in iter_monthly(year, day):
            yield {
                "date": d,
                "description": name,
                "splits": [
                    (source, -amt),
                    (target, amt),
                ],
            }


def gen_umbrella(year: int):
    for month in (1, 4, 7, 10):
        yield {
            "date": date(year, month, 15),
            "description": "Umbrella Insurance Premium",
            "splits": [
                (CHECKING, Decimal("-125.00")),
                ("Expenses:Insurance:Umbrella", Decimal("125.00")),
            ],
        }


def gen_property_tax(year: int):
    yield {
        "date": date(year, 4, 30),
        "description": "King County Property Tax (1st Half)",
        "splits": [
            (CHECKING, Decimal("-3200.00")),
            ("Expenses:Taxes:Property Tax", Decimal("3200.00")),
        ],
    }
    yield {
        "date": date(year, 10, 31),
        "description": "King County Property Tax (2nd Half)",
        "splits": [
            (CHECKING, Decimal("-3200.00")),
            ("Expenses:Taxes:Property Tax", Decimal("3200.00")),
        ],
    }


def gen_estimated_tax(year: int):
    """Estimated federal tax at real IRS deadlines.

    Q4 for tax year N falls on Jan 15 of year N+1. That transaction
    extends the book's data range one day into 2026.
    """
    deadlines = [
        (date(year, 4, 15), "Q1"),
        (date(year, 6, 15), "Q2"),
        (date(year, 9, 15), "Q3"),
        (date(year + 1, 1, 15), "Q4"),
    ]
    for d, quarter in deadlines:
        yield {
            "date": d,
            "description": f"Estimated Federal Tax - {quarter} {year}",
            "splits": [
                (CHECKING, Decimal("-4200.00")),
                ("Expenses:Taxes:Estimated Tax Payments", Decimal("4200.00")),
            ],
        }


# ── Driver ─────────────────────────────────────────────────────

def collect_transactions(year: int, seed: int) -> list[dict]:
    """Deterministic assembly of all Phase 5 transactions."""
    txns: list[dict] = []
    txns.extend(gen_paychecks(random.Random(seed + 1), year))
    txns.extend(gen_mortgage(year))
    txns.extend(gen_auto_loan(year))
    txns.extend(gen_simple_monthly(year))
    txns.extend(gen_umbrella(year))
    txns.extend(gen_property_tax(year))
    txns.extend(gen_estimated_tax(year))
    txns.sort(key=lambda t: (t["date"], t["description"]))
    return txns


def validate_splits(txns: list[dict]) -> None:
    """Every transaction must balance to zero."""
    for t in txns:
        total = sum(v for _, v in t["splits"])
        if total != Decimal("0"):
            raise AssertionError(
                f"Transaction does not balance: {t['description']} "
                f"on {t['date']}, total={total}, splits={t['splits']}"
            )


def print_summary(txns: list[dict]) -> None:
    per_month = Counter((t["date"].year, t["date"].month) for t in txns)
    total_gross = Decimal("0")
    for t in txns:
        total_gross += sum(abs(v) for _, v in t["splits"]) / 2
    print(f"  Total transactions: {len(txns)}")
    print(f"  Total gross flow: ${total_gross:,.2f}")
    print("  Per month:")
    for (y, m) in sorted(per_month):
        print(f"    {y}-{m:02d}: {per_month[(y, m)]:4d}")


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
        print(f"    {t['date']} {t['description'][:50]:<50} ${gross:>9}")
    if len(txns) > 10:
        print("    ...")
        print("  Last 5:")
        for t in txns[-5:]:
            gross = sum(abs(v) for _, v in t["splits"]) / 2
            print(f"    {t['date']} {t['description'][:50]:<50} ${gross:>9}")


def write_transactions(book_path: Path, txns: list[dict]) -> None:
    book = piecash.open_book(str(book_path), readonly=False)
    try:
        usd = book.commodities.get(mnemonic="USD")

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
        per_month: dict[tuple[int, int], int] = {}
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
            key = (t["date"].year, t["date"].month)
            per_month[key] = per_month.get(key, 0) + 1
            if created % 50 == 0:
                print(f"    ... {created} staged")

        print(f"  Saving book ({created} transactions)...")
        book.save()
        summary = ", ".join(
            f"{y}-{m:02d}:{per_month[(y, m)]}"
            for (y, m) in sorted(per_month)
        )
        print(f"  Done. {summary}")
    finally:
        book.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=DEFAULT_BOOK)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--month", type=int, default=None,
                        help="Only write this calendar month (1-12). "
                             "Filters all txns by date.month.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    book_path = Path(args.book)
    if not book_path.exists():
        raise SystemExit(f"Book not found: {book_path}")

    print(f"Generating Phase 5 transactions for {args.year} (seed={args.seed})")
    txns = collect_transactions(args.year, args.seed)
    validate_splits(txns)

    if args.month is not None:
        txns = [t for t in txns if t["date"].month == args.month]
        print(f"Filtered to month={args.month}: {len(txns)} transactions")

    if args.dry_run:
        dry_run(txns)
        return

    print_summary(txns)
    print()

    if not args.no_backup:
        suffix = args.month if args.month is not None else "all"
        backup = book_path.with_suffix(f".pre-phase5-{suffix}.gnucash")
        shutil.copy2(book_path, backup)
        print(f"Backup created: {backup}")

    print(f"Writing to {book_path}...")
    write_transactions(book_path, txns)
    print("OK")


if __name__ == "__main__":
    main()
