"""Phase 14: Alex's Stage 3 business features — taxtables, jobs,
credit notes, employee vouchers.

Stage 3 features shipped in v1.3 but the phase scripts predated
them. This phase exercises each on Alex's existing entities so
the validation corpus (samples/alex-chen-morales.gnucash) carries
realistic Stage 3 activity:

  - 1 taxtable: WA Sales Tax 10.1% (against an in-state customer)
  - 1 job: "Sound Transit Q1 Migration" grouping 2 invoices
  - 1 customer credit note: $500 partial credit on a prior
    Emerald invoice (out-of-scope work returned)
  - 1 voucher: Sam Rivera's Q3 conference reimbursement (3 line
    items, posted + paid)

Each feature is exercised once with realistic accompanying
financial activity. The script is idempotent within a single
phase 14 run (creates fresh entities each time) but expects
phase 7 to have populated customers/vendors/employees by ID.

Uses GnuCashBook directly to bypass MCP audit logging on
synthetic data.

Usage:
    uv run python scripts/synthetic_book/phase_14_stage3.py --dry-run
    uv run python scripts/synthetic_book/phase_14_stage3.py
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


# Account paths used in this phase. Each must exist in Alex's
# chart of accounts; phase 14 doesn't bootstrap structure.
CHECKING = "Assets:Current Assets:Checking Account"
AR_USD = "Assets:Receivables:Accounts Receivable"
AP = "Liabilities:Accounts Payable"
LLC_REVENUE = "Income:LLC Revenue"
EXPENSE_TRAVEL = "Expenses:Travel"
EXPENSE_CONFERENCES = "Expenses:Business:Professional Development"
# Sales-tax-collected goes to a liability account. Created during
# this phase if it doesn't exist (taxtables need a target account).
SALES_TAX_LIAB = "Liabilities:Sales Tax Collected"


def _find_customer_id_by_name(book: GnuCashBook, name: str) -> str:
    """Return the auto-assigned customer ID for ``name``. Phase 7
    created customers in a known order; this lookup decouples
    phase 14 from that ordering."""
    customers = book.list_customers(compact=False)
    for c in customers:
        if c["name"] == name:
            return c["id"]
    raise SystemExit(
        f"Customer {name!r} not found — was phase 7 run? "
        f"Phase 14 depends on its entities."
    )


def _find_employee_id_by_name(book: GnuCashBook, name: str) -> str:
    employees = book.list_employees(compact=False)
    for e in employees:
        if e["name"] == name:
            return e["id"]
    raise SystemExit(
        f"Employee {name!r} not found — was phase 7 run?"
    )


def _find_invoice_for_customer_by_name(
    book: GnuCashBook, customer_name: str,
) -> str:
    """Return the ID of any posted invoice belonging to the
    customer with the given name. The list_invoices verbose
    shape carries ``owner_name`` and ``date_posted`` (None when
    unposted) but not ``owner_id``, so we resolve via name."""
    result = book.list_invoices(
        owner_type="customer", compact=False,
    )
    # list_invoices wraps the rows in {invoices, count, total, notice}
    # — unlike list_customers/list_employees which return a bare
    # list. The shape divergence is by-design (list_invoices
    # carries truncation context).
    for inv in result["invoices"]:
        if (
            inv.get("owner_name") == customer_name
            and inv.get("date_posted")
        ):
            return inv["id"]
    raise SystemExit(
        f"No posted invoice found for customer {customer_name!r} — "
        f"phase 7 should have posted 12 Emerald invoices."
    )


def _ensure_account(
    book: GnuCashBook,
    path: str,
    account_type: str,
    parent: str,
) -> None:
    """Create the account if it doesn't already exist.
    Stage 3 phase 14 needs ``Liabilities:Sales Tax Collected``
    which phase 7's chart doesn't include."""
    try:
        existing = book.get_account(path)
        if existing:
            return
    except Exception:
        pass
    leaf = path.split(":")[-1]
    try:
        book.create_account(
            name=leaf, account_type=account_type, parent=parent,
        )
    except ValueError as e:
        if "already exists" in str(e).lower():
            return
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 14: Alex Stage 3 features.",
    )
    parser.add_argument(
        "--book", default=DEFAULT_BOOK,
        help=f"Path to GnuCash book (default: {DEFAULT_BOOK})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print plan without making changes.",
    )
    args = parser.parse_args()

    book_path = Path(args.book)
    if not book_path.exists():
        print(f"ERROR: book not found: {book_path}")
        return 1

    if args.dry_run:
        print(f"DRY RUN — would phase-14 against {book_path}")
        print("  Step 1: Create Liabilities:Sales Tax Collected")
        print("  Step 2: Create taxtable 'WA Sales Tax 10.1%'")
        print("  Step 3: Issue taxed invoice to a customer")
        print("  Step 4: Create job 'Sound Transit Q1 Migration'")
        print("         + 2 job-attached invoices")
        print("  Step 5: Issue $500 credit note against Emerald")
        print("  Step 6: Sam Rivera Q3 conference voucher "
              "(3 entries, post + pay)")
        return 0

    # Pre-phase backup. Mirrors the pattern in phases 5-13.
    backup_path = book_path.with_suffix(".pre-phase14.gnucash")
    shutil.copy2(book_path, backup_path)
    print(f"Backup: {backup_path}")

    book = GnuCashBook(str(book_path))

    # Step 1: ensure the sales-tax liability account exists.
    print("\nStep 1: Sales tax liability account")
    _ensure_account(book, SALES_TAX_LIAB, "LIABILITY", "Liabilities")
    print(f"  {SALES_TAX_LIAB} ready")

    # Step 2: taxtable.
    print("\nStep 2: Taxtable")
    try:
        tt = book.create_taxtable(
            name="WA Sales Tax 10.1%",
            entries=[
                {
                    "type": "percentage",
                    "amount": "10.1",
                    "account": SALES_TAX_LIAB,
                },
            ],
        )
        print(f"  Created taxtable: {tt['name']}")
    except ValueError as e:
        if "already exists" in str(e).lower():
            print("  Taxtable already present; reusing")
        else:
            raise

    # Step 3: a taxed customer invoice. The customer pre-exists
    # from phase 7 (Sound Transit is in-state for the sake of the
    # exercise — they're a Seattle entity).
    print("\nStep 3: Taxed invoice")
    sound_transit_id = _find_customer_id_by_name(
        book, "Sound Transit Data Team",
    )
    inv = book.create_invoice(
        customer_id=sound_transit_id,
        date_opened=date(2025, 10, 5).isoformat(),
        term="Net 30",
    )
    book.add_invoice_entry(
        invoice_id=inv["id"],
        account=LLC_REVENUE,
        description="October on-site engineering — WA taxable",
        quantity="40",
        price="200.00",  # 40hr × $200 = $8000 pretax
        taxtable="WA Sales Tax 10.1%",
    )
    book.post_invoice(
        invoice_id=inv["id"],
        post_account=AR_USD,
        post_date=date(2025, 10, 5).isoformat(),
    )
    book.pay_invoice(
        invoice_id=inv["id"],
        payment_account=CHECKING,
        amount="8808.00",  # 8000 + 808 tax
        payment_date=date(2025, 10, 30).isoformat(),
    )
    print(f"  Taxed invoice {inv['id']}: $8000 pretax + $808 tax")

    # Step 4: Job + two job-attached invoices.
    print("\nStep 4: Job (Sound Transit Q1 Migration)")
    job = book.create_job(
        owner_id=sound_transit_id,
        owner_type="customer",
        name="Sound Transit Q1 Migration",
        reference="ST-2025-Q1-MIG",
    )
    print(f"  Job created: {job['id']} ({job['name']})")
    for month_open, day_open, month_pay, day_pay, amount in [
        (4, 5, 4, 25, "4500.00"),  # April invoice
        (5, 5, 5, 25, "4500.00"),  # May invoice
    ]:
        date_open = date(2025, month_open, day_open).isoformat()
        date_pay = date(2025, month_pay, day_pay).isoformat()
        ji = book.create_invoice(
            customer_id=sound_transit_id,
            job_id=job["id"],
            date_opened=date_open,
            term="Net 30",
        )
        book.add_invoice_entry(
            invoice_id=ji["id"],
            account=LLC_REVENUE,
            description=f"Q1 migration milestone — "
                        f"{date(2025, month_open, 1).strftime('%B')}",
            quantity="1",
            price=amount,
        )
        book.post_invoice(
            invoice_id=ji["id"],
            post_account=AR_USD,
            post_date=date_open,
        )
        book.pay_invoice(
            invoice_id=ji["id"],
            payment_account=CHECKING,
            amount=amount,
            payment_date=date_pay,
        )
        print(f"  Job invoice {ji['id']}: ${amount}")

    # Step 5: Credit note against Emerald.
    print("\nStep 5: Credit note (Emerald)")
    emerald_id = _find_customer_id_by_name(book, "Emerald Analytics")
    target_invoice = _find_invoice_for_customer_by_name(
        book, "Emerald Analytics",
    )
    cn = book.create_credit_note(
        owner_id=emerald_id,
        owner_type="customer",
        applies_to_invoice_id=target_invoice,
        date_opened=date(2025, 9, 15).isoformat(),
        notes=(
            f"Partial credit for out-of-scope work billed on "
            f"invoice {target_invoice}."
        ),
    )
    book.add_credit_note_entry(
        credit_note_id=cn["id"],
        account=LLC_REVENUE,
        description="Reduce out-of-scope billing",
        quantity="1",
        price="500.00",
    )
    book.post_invoice(
        invoice_id=cn["id"],
        post_account=AR_USD,
        post_date=date(2025, 9, 15).isoformat(),
    )
    print(f"  Credit note {cn['id']}: $500 against invoice "
          f"{target_invoice}")

    # Step 6: Employee voucher (Sam Rivera Q3 conference).
    print("\nStep 6: Employee voucher (Sam Rivera)")
    sam_id = _find_employee_id_by_name(book, "Sam Rivera")
    voucher = book.create_voucher(
        employee_id=sam_id,
        date_opened=date(2025, 8, 28).isoformat(),
        notes="Q3 industry conference + travel reimbursement.",
    )
    for desc, account, quantity, price in [
        ("PyCon registration", EXPENSE_CONFERENCES, "1", "650.00"),
        ("Round-trip flight ATL ↔ Portland", EXPENSE_TRAVEL, "1",
         "412.50"),
        ("Ground transport + parking", EXPENSE_TRAVEL, "1", "180.00"),
    ]:
        book.add_voucher_entry(
            voucher_id=voucher["id"],
            account=account,
            description=desc,
            quantity=quantity,
            price=price,
        )
    book.post_invoice(
        invoice_id=voucher["id"],
        post_account=AP,
        post_date=date(2025, 8, 28).isoformat(),
        owner_type="employee",
    )
    # Voucher total: 650 + 412.50 + 180 = 1242.50
    book.pay_invoice(
        invoice_id=voucher["id"],
        payment_account=CHECKING,
        amount="1242.50",
        payment_date=date(2025, 9, 5).isoformat(),
        owner_type="employee",
    )
    print(f"  Voucher {voucher['id']}: $1242.50 reimbursed to Sam")

    print("\n✅ Phase 14 complete — Stage 3 features exercised.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
