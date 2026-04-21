"""Phase 9: credit card interest + payment lifecycle.

Implements the spec's narrative for both credit cards:

Chase Sapphire
  - Opens Jan at $2,340 (carried balance)
  - Charged monthly interest Jan-May (21.49% APR on prior-month balance)
  - Minimum payments Jan-May, then full payoff during June
  - Jul-Dec: used as daily driver, paid in full each month-end

Business Amex
  - Monthly pay-in-full every month...
  - ...except August, which simulates a LATE payment: $29 late fee +
    one month's interest (~$38). Sep catches up with residual + Sep's
    own charges.

Strategy: re-open the book once per month, query the card's balance at
that point (reflecting all prior transactions including Phase 5/6/7/8
and this phase's already-posted interest and payments), then size the
current month's payment accordingly. This keeps the narrative honest
against whatever charges actually landed in the book.

Sign conventions match prior phases:
  - Liability increase (interest, late fee, charge): amount = -X
  - Liability decrease (payment):                    amount = +X
  - Expense accrual:                                 amount = +X
  - Asset out:                                       amount = -X

Uses piecash directly; bypasses MCP server.

Usage:
    uv run python scripts/synthetic_book/phase_9_credit_cards.py --dry-run
    uv run python scripts/synthetic_book/phase_9_credit_cards.py
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import piecash


DEFAULT_BOOK = os.environ.get(
    "GNUCASH_BOOK_PATH",
    "/Users/stephen/Finances/alex.chen-morales.gnucash",
)

CHECKING = "Assets:Current Assets:Checking Account"
CHASE = "Liabilities:Credit Card:Chase Sapphire"
AMEX = "Liabilities:Credit Card:Business Amex"
CC_INTEREST = "Expenses:Interest:Credit Card Interest"
BANK_CHARGES = "Expenses:Bank Charges"


# Chase monthly interest charges (Jan-May) per the spec
CHASE_INTEREST = [
    (1, 15, Decimal("42.00")),
    (2, 15, Decimal("35.00")),
    (3, 15, Decimal("28.00")),
    (4, 15, Decimal("20.00")),
    (5, 15, Decimal("12.00")),
]

# Chase minimum/target payments Jan-May (day 20 each month)
CHASE_MIN_PAYMENTS = [
    (1, 20, Decimal("500.00")),
    (2, 20, Decimal("500.00")),
    (3, 20, Decimal("500.00")),
    (4, 20, Decimal("500.00")),
    (5, 20, Decimal("500.00")),
]

# Chase June payoff: dynamic — query balance on June 25 and pay it in full.
CHASE_JUNE_PAYOFF_DAY = 25

# Chase pay-in-full for Jul-Dec: query balance at end of each month,
# pay that amount to return to ~$0. This models "used as daily driver,
# statements paid in full monthly."
CHASE_FULL_PAY_MONTHS = [(m, 28) for m in range(7, 13)]

# Amex monthly pay-in-full (same pattern except August)
AMEX_FULL_PAY_MONTHS = [
    (m, 25) for m in range(1, 13) if m != 8
]

# Amex August late payment
AMEX_LATE_FEE_DATE = (8, 28)   # $29 late fee
AMEX_LATE_FEE = Decimal("29.00")
AMEX_LATE_INTEREST_DATE = (8, 28)  # ~$38 interest for the missed cycle
AMEX_LATE_INTEREST = Decimal("38.00")
# Aug partial payment: pay only $500 even though balance is higher
AMEX_AUG_PARTIAL_DATE = (8, 25)
AMEX_AUG_PARTIAL = Decimal("500.00")


# ── Helpers ─────────────────────────────────────────────────────

def _to_date(d) -> date:
    """Normalize a piecash txn post_date (datetime or date) to date."""
    if hasattr(d, "date") and callable(d.date):
        return d.date()
    return d


def balance_of(
    book: piecash.Book, fullname: str, as_of: date | None = None
) -> Decimal:
    """Return account balance as of a given date (inclusive), or now.

    For liability accounts, returns "what you owe" as a positive
    number, matching the MCP server's get_balance normalization.

    When ``as_of`` is provided, only sum splits whose parent
    transaction's post_date is on or before that date.
    """
    for a in book.accounts:
        if a.fullname == fullname:
            total = Decimal("0")
            for s in a.splits:
                if as_of is not None:
                    tdate = _to_date(s.transaction.post_date)
                    if tdate > as_of:
                        continue
                total += Decimal(str(s.value))
            if a.type in ("LIABILITY", "CREDIT", "PAYABLE"):
                return -total
            return total
    raise ValueError(f"Account not found: {fullname}")


def _value_for(acct, direction: str, amount: Decimal) -> Decimal:
    """Compute the signed split value for an account given the
    direction the account's balance should move.

    direction:
      'up'   — balance increases (asset in, expense accrues, liability rises,
               income earned)
      'down' — balance decreases (asset out, expense refund, liability paid,
               income reversal)

    Sign rules:
      Debit-natural  (ASSET, BANK, CASH, EXPENSE)  : up=+, down=-
      Credit-natural (LIABILITY, CREDIT, PAYABLE,
                      INCOME, EQUITY)              : up=-, down=+
    """
    debit_natural = {"ASSET", "BANK", "CASH", "EXPENSE", "STOCK", "MUTUAL"}
    credit_natural = {"LIABILITY", "CREDIT", "PAYABLE", "INCOME", "EQUITY"}
    if acct.type in debit_natural:
        return amount if direction == "up" else -amount
    elif acct.type in credit_natural:
        return -amount if direction == "up" else amount
    raise ValueError(f"Unsupported account type: {acct.type}")


def post_txn(
    book,
    usd,
    acct_cache,
    description: str,
    post_date: date,
    transfers,
):
    """Post a transaction whose splits are described by (path, direction, amount).

    Each transfer tuple: (account_path, 'up' or 'down', Decimal amount).
    The helper translates direction → signed value per account type.
    The caller is responsible for a balanced set (values sum to zero
    in the transaction currency).
    """
    splits = []
    for path, direction, amount in transfers:
        acct = acct_cache[path]
        splits.append(piecash.Split(
            account=acct,
            value=_value_for(acct, direction, amount),
        ))
    piecash.Transaction(
        currency=usd,
        description=description,
        post_date=post_date,
        splits=splits,
    )


# ── Driver ─────────────────────────────────────────────────────

def run(book_path: Path, dry_run: bool = False) -> None:
    book = piecash.open_book(str(book_path), readonly=False)
    try:
        usd = book.commodities.get(mnemonic="USD")
        acct_cache: dict[str, piecash.Account] = {}
        for a in book.accounts:
            acct_cache[a.fullname] = a

        counts = {"chase_interest": 0, "chase_payment": 0,
                  "amex_payment": 0, "amex_late": 0}

        # ── Chase interest charges (Jan-May) ─────────────────
        # For each: Chase balance rises (up), Interest expense rises (up).
        print("Step 1: Chase interest charges (Jan-May)")
        for month, day, amount in CHASE_INTEREST:
            post_txn(
                book, usd, acct_cache,
                description=f"Chase Sapphire interest — "
                            f"{date(2025, month, 1).strftime('%B %Y')}",
                post_date=date(2025, month, day),
                transfers=[
                    (CHASE, "up", amount),
                    (CC_INTEREST, "up", amount),
                ],
            )
            counts["chase_interest"] += 1

        # ── Chase minimum payments Jan-May ───────────────────
        # Payment: Checking down, Chase down (debt paid).
        print("Step 2: Chase minimum payments (Jan-May)")
        for month, day, amount in CHASE_MIN_PAYMENTS:
            post_txn(
                book, usd, acct_cache,
                description=f"Chase Sapphire payment — "
                            f"{date(2025, month, 1).strftime('%B %Y')}",
                post_date=date(2025, month, day),
                transfers=[
                    (CHECKING, "down", amount),
                    (CHASE, "down", amount),
                ],
            )
            counts["chase_payment"] += 1

        # Flush pending writes so balance queries see them.
        book.flush()

        # ── Chase June payoff: query balance, pay it off ─────
        print("Step 3: Chase June payoff (dynamic)")
        payoff_date = date(2025, 6, CHASE_JUNE_PAYOFF_DAY)
        chase_balance = balance_of(book, CHASE, as_of=payoff_date)
        june_payoff = chase_balance.quantize(Decimal("0.01"))
        if june_payoff > 0:
            post_txn(
                book, usd, acct_cache,
                description="Chase Sapphire — June payoff",
                post_date=payoff_date,
                transfers=[
                    (CHECKING, "down", june_payoff),
                    (CHASE, "down", june_payoff),
                ],
            )
            counts["chase_payment"] += 1
            print(f"  Paid off ${june_payoff} in Chase")
        else:
            print(f"  Chase already at ${chase_balance}; no payoff needed")

        # ── Chase pay-in-full Jul-Dec ────────────────────────
        # For each month, query balance at the payment date and pay
        # whatever's owed. This keeps Chase oscillating back to ~0
        # each month as Phase 6 daily charges land throughout.
        print("Step 4: Chase monthly pay-in-full (Jul-Dec)")
        for month, day in CHASE_FULL_PAY_MONTHS:
            book.flush()
            pay_date = date(2025, month, day)
            chase_balance = balance_of(book, CHASE, as_of=pay_date)
            pay = chase_balance.quantize(Decimal("0.01"))
            if pay > 0:
                post_txn(
                    book, usd, acct_cache,
                    description=f"Chase Sapphire — "
                                f"{date(2025, month, 1).strftime('%b')} "
                                f"pay-in-full",
                    post_date=pay_date,
                    transfers=[
                        (CHECKING, "down", pay),
                        (CHASE, "down", pay),
                    ],
                )
                counts["chase_payment"] += 1

        # ── Amex August partial + late fee + interest (done FIRST) ────────
        # Book Aug first so the Sep pay-in-full's balance query sees
        # the Aug residual + late fee + interest correctly.
        print("Step 5: Amex August late payment (booked first)")
        book.flush()
        # Partial payment
        post_txn(
            book, usd, acct_cache,
            description="Business Amex — August partial payment (late)",
            post_date=date(2025, AMEX_AUG_PARTIAL_DATE[0],
                           AMEX_AUG_PARTIAL_DATE[1]),
            transfers=[
                (CHECKING, "down", AMEX_AUG_PARTIAL),
                (AMEX, "down", AMEX_AUG_PARTIAL),
            ],
        )
        counts["amex_payment"] += 1
        # Late fee: Amex up, Bank Charges up
        post_txn(
            book, usd, acct_cache,
            description="Business Amex — late payment fee",
            post_date=date(2025, AMEX_LATE_FEE_DATE[0],
                           AMEX_LATE_FEE_DATE[1]),
            transfers=[
                (AMEX, "up", AMEX_LATE_FEE),
                (BANK_CHARGES, "up", AMEX_LATE_FEE),
            ],
        )
        counts["amex_late"] += 1
        # Interest: Amex up, Interest expense up
        post_txn(
            book, usd, acct_cache,
            description="Business Amex — August interest (missed cycle)",
            post_date=date(2025, AMEX_LATE_INTEREST_DATE[0],
                           AMEX_LATE_INTEREST_DATE[1]),
            transfers=[
                (AMEX, "up", AMEX_LATE_INTEREST),
                (CC_INTEREST, "up", AMEX_LATE_INTEREST),
            ],
        )
        counts["amex_late"] += 1

        # ── Amex monthly pay-in-full (non-August) ────────────
        # Done after Aug stuff is booked, so Sep's as-of query picks
        # up the residual from August's partial + late fee + interest.
        print("Step 6: Amex monthly pay-in-full (non-August)")
        for month, day in AMEX_FULL_PAY_MONTHS:
            book.flush()
            pay_date = date(2025, month, day)
            amex_balance = balance_of(book, AMEX, as_of=pay_date)
            pay = amex_balance.quantize(Decimal("0.01"))
            if pay > 0:
                post_txn(
                    book, usd, acct_cache,
                    description=f"Business Amex — "
                                f"{date(2025, month, 1).strftime('%b')} "
                                f"pay-in-full",
                    post_date=pay_date,
                    transfers=[
                        (CHECKING, "down", pay),
                        (AMEX, "down", pay),
                    ],
                )
                counts["amex_payment"] += 1

        if dry_run:
            book.cancel()
            print(f"\nDRY RUN — would have staged:")
            for k, v in counts.items():
                print(f"  {k}: {v}")
            return

        total = sum(counts.values())
        print(f"\nSaving book ({total} transactions)...")
        book.save()
        print("Per-category counts:")
        for k, v in counts.items():
            print(f"  {k}: {v}")

        # Final balance report
        print("\nFinal balances:")
        print(f"  Chase Sapphire: ${balance_of(book, CHASE)}")
        print(f"  Business Amex:  ${balance_of(book, AMEX)}")
    finally:
        book.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=DEFAULT_BOOK)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    book_path = Path(args.book)
    if not book_path.exists():
        raise SystemExit(f"Book not found: {book_path}")

    if not args.dry_run and not args.no_backup:
        backup = book_path.with_suffix(".pre-phase9.gnucash")
        shutil.copy2(book_path, backup)
        print(f"Backup created: {backup}\n")

    run(book_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
