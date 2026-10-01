"""Build the purpose-built book for the fix-branch GUI gate.

Every state the gate manifest asks GnuCash's own window to look at
(``BOOKKEEPER_TEST_PLAN_FIX_BRANCH_GUI_GATE.md``), made through the
server on a fresh book:

    uv run python specs/v1.5/testing/build_fix_branch_gate_book.py \
        ~/Projects/abe-bench/fix-branch-gate.gnucash

Refuses to overwrite an existing file.
"""

import sqlite3
import sys
import tempfile
import shutil
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tests.conftest as conftest  # noqa: E402
from gnucash_mcp.book import GnuCashBook  # noqa: E402
from gnucash_mcp.book.backup import BackupMixin  # noqa: E402

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"
CHECKING = "Assets:Checking"


def build(target: Path) -> None:
    if target.exists():
        raise SystemExit(f"{target} exists; not overwriting")
    # A fresh book has no pre-1.5 shapes; no snapshot folder beside it.
    BackupMixin._pre_upgrade_checked = True
    with tempfile.TemporaryDirectory() as tmp:
        path = conftest.business_book.__wrapped__(Path(tmp))
        gb = GnuCashBook(str(path))

        def invoice(price, day, **kw):
            entry = {k: kw.pop(k) for k in ("taxtable",) if k in kw}
            inv = gb.create_invoice(customer_id="000001", **kw)
            gb.add_invoice_entry(
                invoice_id=inv["id"], account="Income:Sales",
                description="Work", quantity="1", price=price, **entry,
            )
            if day:
                gb.post_invoice(
                    invoice_id=inv["id"], post_date=day,
                    post_account=kw.get("_ar", AR),
                )
            return inv["id"]

        gb.create_customer(name="Acme Corp")

        # 1. Prepayment lot held: 000001 (100) paid 120; 20 is Acme's.
        gb_id = invoice("100.00", "2026-01-15")
        gb.pay_invoice(
            invoice_id=gb_id, payment_account=CHECKING, amount="120.00",
            payment_date="2026-01-20", memo="chk 12", allow_prepayment=True,
        )
        # …and an open invoice the 20 could settle part of.
        invoice("50.00", "2026-01-16")

        # 2. Unposted with its payment kept: 000003 (70), paid, unposted.
        kept = invoice("70.00", "2026-01-17")
        gb.pay_invoice(
            invoice_id=kept, payment_account=CHECKING, amount="70.00",
            payment_date="2026-01-21", memo="chk 13",
        )
        gb.unpost_invoice(invoice_id=kept, owner_type="customer")

        # 3. Posted billterm copy: 000004 on Net 30, posted by the server.
        gb.create_account(
            name="GST Payable", account_type="LIABILITY", parent="Liabilities",
        )
        gb.create_taxtable(name="T5", entries=[{
            "type": "percentage", "amount": "5",
            "account": "Liabilities:GST Payable",
        }])
        gb.create_billterm(name="Net 30", due_days=30)
        invoice("200.00", "2026-01-18", term="Net 30")

        # 6. A DRAFT taxed invoice on Net 30, for posting IN THE GUI.
        invoice("300.00", None, term="Net 30", taxtable="T5")

        # 4. Card voucher: Dana's, one cash line and two card lines,
        #    drafted the way desktop's voucher window stores them,
        #    posted by the server.
        gb.create_account(
            name="Company Card", account_type="CREDIT", parent="Liabilities",
        )
        gb.create_employee(name="Dana Reimbursee")
        voucher = gb.create_voucher(employee_id="000001")
        for description, price in (
            ("Taxi", "40.00"), ("Hotel", "60.00"), ("Dinner", "25.50"),
        ):
            gb.add_voucher_entry(
                voucher_id=voucher["id"], account="Expenses:Services",
                description=description, quantity="1", price=price,
            )
        con = sqlite3.connect(path)
        con.execute(
            "update employees set ccard_guid = "
            "(select guid from accounts where name = 'Company Card')"
        )
        con.execute(
            "update entries set b_paytype = 2 "
            "where description in ('Hotel', 'Dinner')"
        )
        con.commit()
        con.close()
        gb.post_invoice(
            invoice_id=voucher["id"], post_account=AP,
            post_date="2026-01-19", owner_type="employee",
        )

        # 5. Forced off-currency invoice: EUR 100 for Acme (USD).
        gb.create_account(
            name="Receivable EUR", account_type="RECEIVABLE", parent="Assets",
            commodity="EUR",
        )
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value="1.10",
            price_date=date(2026, 1, 15), source="Finance::Quote",
        )
        eur = gb.create_invoice(
            customer_id="000001", currency="EUR", force=True,
        )
        gb.add_invoice_entry(
            invoice_id=eur["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(
            invoice_id=eur["id"], post_account="Assets:Receivable EUR",
            post_date="2026-01-15",
        )

        # 7. One feed quote on 2026-06-01, for the Price Editor check.
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value="1.12",
            price_date=date(2026, 6, 1), source="Finance::Quote",
        )

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)

    gb = GnuCashBook(str(target))
    print(f"built {target}")
    for row in gb.list_invoices(compact=False)["invoices"]:
        print("  ", row["id"], row.get("type"), row.get("status"),
              row.get("currency"), row.get("total"))
    print(gb.get_outstanding_invoices())


if __name__ == "__main__":
    build(Path(sys.argv[1]).expanduser())
