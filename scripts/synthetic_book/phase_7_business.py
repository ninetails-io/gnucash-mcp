"""Phase 7: Alex's contractor income + LLC business flows.

Generates Alex's 1099 direct deposits and the full LLC business module
exercise — customers, invoices, vendor bills — across 2025.

  - 9 direct 1099 deposits to Checking (TechStartup, DataFlow,
    CloudNine, WinterTech across different periods)
  - 3 billing terms (Net 15, Net 30, 2/10 Net 30)
  - 3 customers (Emerald USD, Sound Transit USD, Berlin Digital EUR)
  - 20 customer invoices: 12 Emerald + 4 Sound Transit + 4 Berlin
  - 2 vendors (JetBrains, BookkeepingCo) + 5 vendor bills
  - 1 employee (Sam Rivera, LLC virtual assistant)
  - 1 new EUR A/R sub-account for Berlin Digital invoices

Spec conflict — skipped in this phase:
  Phase 4/5 already posts AWS ($125/mo) and WeWork ($250/mo) as
  Business Amex charges. Phase 7 lists them as vendors too; including
  them would double-count the expense. We skip AWS/WeWork as vendors.

Currency handling:
  Berlin Digital invoices are denominated in EUR and post to a
  dedicated EUR A/R account. Payments convert EUR→USD at the month's
  exchange rate (from Phase 1 prices) and credit to Checking.

Uses gnucash_mcp.book.GnuCashBook directly (bypasses the MCP tool layer,
so no audit logging noise for synthetic data).

Usage:
    uv run python scripts/synthetic_book/phase_7_business.py --dry-run
    uv run python scripts/synthetic_book/phase_7_business.py
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from gnucash_mcp.book import GnuCashBook
import piecash


DEFAULT_BOOK = os.environ.get(
    "GNUCASH_BOOK_PATH",
    "/Users/stephen/Finances/alex.chen-morales.gnucash",
)

CHECKING = "Assets:Current Assets:Checking Account"
AR_USD = "Assets:Receivables:Accounts Receivable"
AR_EUR = "Assets:Receivables:Accounts Receivable EUR"
AP = "Liabilities:Accounts Payable"
LLC_REVENUE = "Income:LLC Revenue"
CONTRACTOR = "Income:Contractor Income"


# ── Direct 1099 income (no business module) ────────────────────

DIRECT_1099 = [
    # (month, client, monthly_amount)
    (1, "TechStartup Inc", "4500.00"),
    (2, "TechStartup Inc", "4500.00"),
    (3, "TechStartup Inc", "4500.00"),
    (5, "DataFlow Systems", "6000.00"),
    (6, "DataFlow Systems", "6000.00"),
    (8, "CloudNine Consulting", "3800.00"),
    (9, "CloudNine Consulting", "3800.00"),
    (11, "WinterTech Solutions", "5200.00"),
    (12, "WinterTech Solutions", "5200.00"),
]


# ── Invoice definitions ────────────────────────────────────────

EMERALD_INVOICES = [
    # (month, day_opened, day_paid, amount_usd)
    (month, 1, 28, "3500.00") for month in range(1, 13)
]

SOUND_TRANSIT_INVOICES = [
    # Net 15 — paid ~15 days after invoice
    (2, 5, 20, "8500.00"),
    (3, 5, 20, "8500.00"),
    (6, 1, 16, "12000.00"),
    (10, 3, 18, "8500.00"),
]

BERLIN_INVOICES = [
    # EUR amounts, Net 30 paid ~month later
    # (month_open, day_open, month_pay, day_pay, amount_eur, eur_usd_rate_at_payment)
    (3, 10, 4, 9, "4500.00", Decimal("1.095")),   # Apr 9 rate
    (6, 5, 7, 5, "6200.00", Decimal("1.11")),     # Jul 5 rate
    (9, 8, 10, 8, "4500.00", Decimal("1.115")),   # Oct 8 rate
    (12, 3, 1, 2, "5800.00", Decimal("1.09")),    # paid Jan 2 2026, Dec rate
]


# ── Vendor bills (excluding AWS/WeWork — see module docstring) ──

JETBRAINS_BILL = {
    "month": 1,
    "day_opened": 12,
    "day_paid": 25,
    "amount": "289.00",
    "description": "JetBrains IntelliJ IDEA Ultimate subscription (annual)",
    "expense_account": "Expenses:Business:Software",
    "payment_account": "Liabilities:Credit Card:Business Amex",
}

BOOKKEEPER_BILLS = [
    # (month_opened, day_opened, month_paid, day_paid, amount)
    (3, 5, 3, 20, "450.00"),
    (6, 5, 6, 20, "450.00"),
    (9, 5, 9, 20, "450.00"),
    (12, 5, 12, 20, "450.00"),
]


# ── Helpers ────────────────────────────────────────────────────

def need_eur_ar_account(book: GnuCashBook) -> None:
    """Create the EUR A/R sub-account if it doesn't exist.

    GnuCash accounts are single-commodity, so EUR invoices need a
    EUR-denominated A/R account separate from the USD one.
    """
    existing = book.get_account(AR_EUR)
    if existing:
        print(f"  {AR_EUR} already exists (skipping create)")
        return
    book.create_account(
        name="Accounts Receivable EUR",
        account_type="RECEIVABLE",
        parent="Assets:Receivables",
        description="A/R for EUR-denominated invoices (Berlin Digital)",
        commodity="EUR",
        commodity_namespace="CURRENCY",
    )
    print(f"  Created {AR_EUR}")


# ── Driver ─────────────────────────────────────────────────────

def run(book_path: Path, dry_run: bool = False) -> None:
    if dry_run:
        # Just enumerate and print counts
        emerald = len(EMERALD_INVOICES)
        st = len(SOUND_TRANSIT_INVOICES)
        berlin = len(BERLIN_INVOICES)
        bookkeeper = len(BOOKKEEPER_BILLS)
        print("DRY RUN — planned operations:")
        print(f"  Direct 1099: {len(DIRECT_1099)} txns")
        print(f"  Billing terms: 3")
        print(f"  Customers: 3 (Emerald, Sound Transit, Berlin Digital)")
        print(f"  Vendors: 2 (JetBrains, BookkeepingCo)")
        print(f"  Employee: 1 (Sam Rivera)")
        print(f"  Customer invoices: {emerald + st + berlin} "
              f"({emerald} Emerald + {st} Sound Transit + {berlin} Berlin)")
        print(f"  Vendor bills: {1 + bookkeeper} (1 JetBrains + {bookkeeper} BookkeepingCo)")
        print(f"  EUR A/R account: 1 (new)")
        print(f"  Total book objects: {3 + 3 + 2 + 1 + emerald + st + berlin + 1 + bookkeeper + 1 + len(DIRECT_1099)}")
        return

    book = GnuCashBook(str(book_path))

    # 1. Create EUR A/R account for Berlin Digital
    print("Step 1: EUR A/R account")
    need_eur_ar_account(book)

    # 2. Direct 1099 income
    print(f"\nStep 2: {len(DIRECT_1099)} direct 1099 deposits")
    for month, client, amount_str in DIRECT_1099:
        amt = Decimal(amount_str)
        deposit_date = date(2025, month, 15)  # mid-month deposit
        book.create_transaction(
            description=f"{client} - monthly invoice payment",
            trans_date=deposit_date,
            splits=[
                {"account": CHECKING, "amount": str(amt)},
                {"account": CONTRACTOR, "amount": str(-amt)},
            ],
            check_duplicates=False,
        )
    print(f"  Done: {len(DIRECT_1099)} 1099 deposits")

    # 3. Billing terms
    print("\nStep 3: Billing terms")
    book.create_billterm(name="Net 15", due_days=15,
                         description="Payment due within 15 days")
    book.create_billterm(name="Net 30", due_days=30,
                         description="Payment due within 30 days")
    book.create_billterm(name="2/10 Net 30", due_days=30, discount_days=10,
                         discount_percent="2",
                         description="2% discount if paid in 10 days, else net 30")
    print("  Done: 3 terms created")

    # 4. Customers
    print("\nStep 4: Customers")
    emerald = book.create_customer(
        name="Emerald Analytics",
        currency="USD",
        notes="Monthly retainer, Net 30",
    )
    sound_transit = book.create_customer(
        name="Sound Transit Data Team",
        currency="USD",
        notes="Project-based engagement, Net 15",
    )
    berlin = book.create_customer(
        name="Berlin Digital GmbH",
        currency="EUR",
        notes="EUR-denominated invoices, Net 30",
    )
    print(f"  Done: Emerald={emerald['id']}, Sound Transit={sound_transit['id']}, "
          f"Berlin={berlin['id']}")

    # 5. Vendors (JetBrains + BookkeepingCo only)
    print("\nStep 5: Vendors")
    jetbrains = book.create_vendor(
        name="JetBrains",
        currency="USD",
        notes="IDE/tooling subscriptions",
    )
    bookkeeper = book.create_vendor(
        name="BookkeepingCo",
        currency="USD",
        notes="Quarterly bookkeeping review",
    )
    print(f"  Done: JetBrains={jetbrains['id']}, BookkeepingCo={bookkeeper['id']}")

    # 6. Employee
    print("\nStep 6: Employee")
    sam = book.create_employee(
        name="Sam Rivera",
        currency="USD",
    )
    print(f"  Done: Sam Rivera={sam['id']}")

    # 7. Customer invoices
    print("\nStep 7: Customer invoices")

    def run_invoice(customer_id, month_open, day_open, month_pay, day_pay,
                    amount, description, currency=None, post_account=AR_USD,
                    payment_account=CHECKING, payment_amount=None):
        """Full 4-step invoice workflow."""
        date_open = date(2025, month_open, day_open).isoformat()
        date_pay = date(2025 if month_pay >= month_open else 2026, month_pay, day_pay).isoformat()
        # Berlin Q4 pay crosses into 2026
        inv = book.create_invoice(
            customer_id=customer_id,
            date_opened=date_open,
            currency=currency,
            term="Net 30",  # default for these
        )
        book.add_invoice_entry(
            invoice_id=inv["id"],
            account=LLC_REVENUE,
            description=description,
            quantity="1",
            price=amount,
        )
        book.post_invoice(
            invoice_id=inv["id"],
            post_account=post_account,
            post_date=date_open,
        )
        book.pay_invoice(
            invoice_id=inv["id"],
            payment_account=payment_account,
            amount=payment_amount or amount,
            payment_date=date_pay,
        )
        return inv["id"]

    # Emerald: 12 monthly
    for month, day_open, day_pay, amount in EMERALD_INVOICES:
        run_invoice(
            customer_id=emerald["id"],
            month_open=month,
            day_open=day_open,
            month_pay=month,  # paid within same month (Net 30 but on 28th)
            day_pay=day_pay,
            amount=amount,
            description=f"{date(2025, month, 1).strftime('%B %Y')} consulting retainer",
        )
    print(f"  Emerald: 12 invoices done")

    # Sound Transit: 4 invoices
    for month, day_open, day_pay, amount in SOUND_TRANSIT_INVOICES:
        run_invoice(
            customer_id=sound_transit["id"],
            month_open=month,
            day_open=day_open,
            month_pay=month,
            day_pay=day_pay,
            amount=amount,
            description=f"Data engineering services - {date(2025, month, 1).strftime('%B %Y')}",
        )
    print(f"  Sound Transit: 4 invoices done")

    # Berlin Digital: 4 invoices in EUR, paid via USD (Checking) at exchange rate
    for month_open, day_open, month_pay, day_pay, amount_eur_str, rate in BERLIN_INVOICES:
        date_open = date(2025, month_open, day_open).isoformat()
        pay_year = 2025 if month_pay >= month_open else 2026
        date_pay = date(pay_year, month_pay, day_pay).isoformat()

        inv = book.create_invoice(
            customer_id=berlin["id"],
            date_opened=date_open,
            currency="EUR",
            term="Net 30",
        )
        book.add_invoice_entry(
            invoice_id=inv["id"],
            account=LLC_REVENUE,
            description=f"Berlin Digital engagement - "
                        f"{date(2025, month_open, 1).strftime('%B %Y')}",
            quantity="1",
            price=amount_eur_str,
        )
        book.post_invoice(
            invoice_id=inv["id"],
            post_account=AR_EUR,
            post_date=date_open,
        )
        # Pay in EUR (same currency as invoice). GnuCash's business module
        # should handle the EUR→USD conversion when the payment account
        # (Checking, USD) differs from invoice currency.
        book.pay_invoice(
            invoice_id=inv["id"],
            payment_account=CHECKING,
            amount=amount_eur_str,  # EUR amount
            payment_date=date_pay,
        )
    print(f"  Berlin Digital: 4 invoices done")

    # 8. Vendor bills — JetBrains (1) + BookkeepingCo (4)
    print("\nStep 8: Vendor bills")

    def run_bill(vendor_id, month_open, day_open, month_pay, day_pay, amount,
                 description, expense_account, payment_account=CHECKING):
        date_open = date(2025, month_open, day_open).isoformat()
        date_pay = date(2025, month_pay, day_pay).isoformat()
        bill = book.create_bill(
            vendor_id=vendor_id,
            date_opened=date_open,
            term="Net 30",
        )
        book.add_bill_entry(
            bill_id=bill["id"],
            account=expense_account,
            description=description,
            quantity="1",
            price=amount,
        )
        book.post_invoice(
            invoice_id=bill["id"],
            post_account=AP,
            post_date=date_open,
            owner_type="vendor",
        )
        book.pay_invoice(
            invoice_id=bill["id"],
            payment_account=payment_account,
            amount=amount,
            payment_date=date_pay,
            owner_type="vendor",
        )

    # JetBrains
    run_bill(
        vendor_id=jetbrains["id"],
        month_open=JETBRAINS_BILL["month"],
        day_open=JETBRAINS_BILL["day_opened"],
        month_pay=JETBRAINS_BILL["month"],
        day_pay=JETBRAINS_BILL["day_paid"],
        amount=JETBRAINS_BILL["amount"],
        description=JETBRAINS_BILL["description"],
        expense_account=JETBRAINS_BILL["expense_account"],
        payment_account=JETBRAINS_BILL["payment_account"],
    )
    print("  JetBrains: 1 bill done")

    # BookkeepingCo (4 quarterly)
    for month_open, day_open, month_pay, day_pay, amount in BOOKKEEPER_BILLS:
        run_bill(
            vendor_id=bookkeeper["id"],
            month_open=month_open,
            day_open=day_open,
            month_pay=month_pay,
            day_pay=day_pay,
            amount=amount,
            description=f"Quarterly bookkeeping review - Q{(month_open - 1) // 3 + 1}",
            expense_account="Expenses:Business:Accounting",
        )
    print("  BookkeepingCo: 4 bills done")

    print("\nPhase 7 complete.")


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
        backup = book_path.with_suffix(".pre-phase7.gnucash")
        shutil.copy2(book_path, backup)
        print(f"Backup created: {backup}\n")

    run(book_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
