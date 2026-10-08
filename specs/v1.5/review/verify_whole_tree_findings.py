"""Verify candidate review findings against real books.

Each check prints CONFIRMED / REFUTED with the evidence. Nothing here
touches the repo's sample books in place — temp copies only.
"""
import json
import shutil
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import piecash
from piecash import factories

from gnucash_mcp.book import GnuCashBook

TMP = Path(tempfile.mkdtemp(prefix="review-"))


def build_business_book(path: Path) -> Path:
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    root, usd = book.root_account, book.default_currency
    assets = piecash.Account(name="Assets", type="ASSET", parent=root,
                             commodity=usd, placeholder=True)
    checking = piecash.Account(name="Checking", type="BANK", parent=assets,
                               commodity=usd)
    piecash.Account(name="Accounts Receivable", type="RECEIVABLE",
                    parent=assets, commodity=usd)
    liab = piecash.Account(name="Liabilities", type="LIABILITY", parent=root,
                           commodity=usd, placeholder=True)
    piecash.Account(name="Accounts Payable", type="PAYABLE", parent=liab,
                    commodity=usd)
    income = piecash.Account(name="Income", type="INCOME", parent=root,
                             commodity=usd, placeholder=True)
    piecash.Account(name="Sales", type="INCOME", parent=income, commodity=usd)
    expenses = piecash.Account(name="Expenses", type="EXPENSE", parent=root,
                               commodity=usd, placeholder=True)
    piecash.Account(name="Office Supplies", type="EXPENSE", parent=expenses,
                    commodity=usd)
    piecash.Account(name="Groceries", type="EXPENSE", parent=expenses,
                    commodity=usd)
    equity = piecash.Account(name="Equity", type="EQUITY", parent=root,
                             commodity=usd, placeholder=True)
    opening = piecash.Account(name="Opening Balance", type="EQUITY",
                              parent=equity, commodity=usd)
    book.save()
    book.session.add(piecash.Transaction(
        currency=usd, description="Opening Balance",
        post_date=date(2025, 12, 31),
        splits=[piecash.Split(account=checking, value=Decimal("10000")),
                piecash.Split(account=opening, value=Decimal("-10000"))]))
    book.save()
    book.close()
    return path


def verdict(label, ok, evidence):
    print(f"[{'CONFIRMED' if ok else 'REFUTED'}] {label}\n    {evidence}\n")


# ── A: voucher / job-attached payment description ──────────────────
gb = GnuCashBook(str(build_business_book(TMP / "a.gnucash")))
gb.create_employee(name="Dana Employee")
v = gb.create_voucher(employee_id="000001", date_opened="2026-08-01")
gb.add_voucher_entry(voucher_id=v["id"], account="Expenses:Office Supplies",
                     description="Toner", quantity="1", price="80.00")
posted = gb.post_invoice(invoice_id=v["id"],
                         post_account="Liabilities:Accounts Payable",
                         post_date="2026-08-01", owner_type="employee")
post_txn = gb.get_transaction(posted["transaction_guid"])
paid = gb.pay_invoice(invoice_id=v["id"], payment_account="Assets:Checking",
                      amount="80.00", payment_date="2026-08-05",
                      owner_type="employee")
pay_txn = gb.get_transaction(paid["transaction_guid"])
verdict(
    "A1 voucher POST txn description falls back to 'Invoice NNN', not employee name",
    post_txn["description"] == f"Invoice {v['id']}",
    f"post description={post_txn['description']!r}",
)
verdict(
    "A2 voucher PAY txn description is EMPTY (owner lookup used vendor finder)",
    pay_txn["description"] == "",
    f"pay description={pay_txn['description']!r}",
)

# job-attached invoice
gb.create_customer(name="Acme Corp")
job = gb.create_job(owner_id="000001", owner_type="customer", name="API Rewrite")
inv = gb.create_invoice(customer_id="000001", job_id=job["id"],
                        date_opened="2026-07-01")
gb.add_invoice_entry(invoice_id=inv["id"], account="Income:Sales",
                     description="Work", quantity="1", price="500.00")
posted = gb.post_invoice(invoice_id=inv["id"],
                         post_account="Assets:Accounts Receivable",
                         post_date="2026-07-01", owner_type="customer")
post_txn = gb.get_transaction(posted["transaction_guid"])
paid = gb.pay_invoice(invoice_id=inv["id"], payment_account="Assets:Checking",
                      amount="200.00", payment_date="2026-07-15",
                      owner_type="customer")
pay_txn = gb.get_transaction(paid["transaction_guid"])
verdict(
    "A3 job-attached invoice POST description is 'Invoice NNN' not customer name",
    post_txn["description"] == f"Invoice {inv['id']}",
    f"post description={post_txn['description']!r}",
)
verdict(
    "A4 job-attached invoice PAY description is EMPTY",
    pay_txn["description"] == "",
    f"pay description={pay_txn['description']!r}",
)

# ── D: job-attached invoices invisible to owner filters ────────────
outstanding_by_cust = gb.get_outstanding_invoices(customer_id="000001",
                                                  compact=False)
outstanding_all = gb.get_outstanding_invoices(compact=False)
verdict(
    "D1 get_outstanding_invoices(customer_id=...) omits the job-attached invoice",
    all(r["id"] != inv["id"] for r in outstanding_by_cust["invoices"])
    and any(r["id"] == inv["id"] for r in outstanding_all["invoices"]),
    f"by customer ids={[r['id'] for r in outstanding_by_cust['invoices']]}, "
    f"unfiltered ids={[r['id'] for r in outstanding_all['invoices']]}",
)
listed = gb.list_invoices(owner_type="customer", compact=False)
verdict(
    "D2 list_invoices(owner_type='customer') omits the job-attached invoice",
    all(r["id"] != inv["id"] for r in listed["invoices"]),
    f"ids={[r['id'] for r in listed['invoices']]}",
)
outstanding_ot = gb.get_outstanding_invoices(owner_type="customer",
                                             compact=False)
verdict(
    "D3 get_outstanding_invoices(owner_type='customer') omits it too",
    all(r["id"] != inv["id"] for r in outstanding_ot["invoices"]),
    f"ids={[r['id'] for r in outstanding_ot['invoices']]}",
)
# delete_customer with a job + job-attached posted invoice
try:
    res = gb.delete_customer("000001")
    deleted_ok = True
    ev = f"delete_customer returned {res}"
except ValueError as e:
    deleted_ok = False
    ev = f"refused: {e}"
verdict(
    "D4 delete_customer succeeds despite a job and a POSTED job-attached invoice",
    deleted_ok, ev,
)
if deleted_ok:
    after = gb.get_outstanding_invoices(compact=False)
    row = next((r for r in after["invoices"] if r["id"] == inv["id"]), None)
    verdict(
        "D4b orphaned invoice now shows owner_name=None (dangling job→customer)",
        row is not None and row["owner_name"] is None,
        f"row={row}",
    )

# ── E: warnings collector mislabels voucher / job-attached overdue ──
gb2 = GnuCashBook(str(build_business_book(TMP / "e.gnucash")))
gb2.create_employee(name="Dana Employee")
v = gb2.create_voucher(employee_id="000001", date_opened="2026-01-01")
gb2.add_voucher_entry(voucher_id=v["id"], account="Expenses:Office Supplies",
                      description="Toner", quantity="1", price="80.00")
gb2.post_invoice(invoice_id=v["id"], post_account="Liabilities:Accounts Payable",
                 post_date="2026-01-01", owner_type="employee")
summary = gb2.get_book_summary()
warn_lines = [l for l in summary.splitlines() if "Past due" in l]
verdict(
    "E1 overdue voucher renders as 'Past due invoice: #NNN' (wrong type, no name)",
    any("invoice" in l and f"#{v['id']}" in l for l in warn_lines),
    f"warnings={warn_lines}",
)
outs = gb2.get_outstanding_invoices(compact=True)
verdict(
    "E1b …while get_outstanding_documents names the employee correctly",
    "Dana Employee" in outs, outs.splitlines()[-1],
)

# ── B: unpost credit note loses applies_to link ────────────────────
gb3 = GnuCashBook(str(build_business_book(TMP / "b.gnucash")))
gb3.create_customer(name="Acme Corp")
inv = gb3.create_invoice(customer_id="000001", date_opened="2026-06-01")
gb3.add_invoice_entry(invoice_id=inv["id"], account="Income:Sales",
                      description="Work", quantity="1", price="500.00")
gb3.post_invoice(invoice_id=inv["id"], post_account="Assets:Accounts Receivable",
                 post_date="2026-06-01", owner_type="customer")
cn = gb3.create_credit_note(owner_id="000001", owner_type="customer",
                            applies_to_invoice_id=inv["id"],
                            date_opened="2026-06-10")
gb3.add_credit_note_entry(credit_note_id=cn["id"], account="Income:Sales",
                          description="Refund", quantity="1", price="100.00",
                          owner_type="customer")
before = gb3.get_invoice(cn["id"], owner_type="customer")
gb3.post_invoice(invoice_id=cn["id"], post_account="Assets:Accounts Receivable",
                 post_date="2026-06-10", owner_type="customer")
mid = gb3.get_invoice(cn["id"], owner_type="customer")
gb3.unpost_invoice(invoice_id=cn["id"], owner_type="customer")
after = gb3.get_invoice(cn["id"], owner_type="customer")
verdict(
    "B1 unpost of a linked credit note drops applies_to (credit-note flag survives)",
    before.get("applies_to") and mid.get("applies_to")
    and after.get("is_credit_note") and not after.get("applies_to"),
    f"before={before.get('applies_to')} posted={mid.get('applies_to')} "
    f"after_unpost={after.get('applies_to')} is_cn={after.get('is_credit_note')}",
)

# ── C: non-decimal amount cell kills the whole batch ───────────────
from gnucash_mcp.server import mcp, _apply_module_filter
import gnucash_mcp.server as srv
srv._book_registry.clear(); srv._book = None; srv._book_paths = []
import os
os.environ["GNUCASH_BOOK_PATH"] = str(TMP / "b.gnucash")
srv._book_paths_source = None
srv._startup_notice_pending = False
srv._writes_armed = True
_apply_module_filter("all")
create_transactions = mcp._tool_manager._tools["create_transactions"].fn
tsv = ("ref\tdate\tdescription\tamt1\tacct1\tamt2\tacct2\n"
       "1\t2026-08-01\tGood row\t-10\tAssets:Checking\t10\tExpenses:Groceries\n"
       "2\t2026-08-02\tBad row\t-$10\tAssets:Checking\t10\tExpenses:Groceries\n")
out = json.loads(create_transactions(transactions=tsv, on_error="skip",
                                     dry_run=True))
verdict(
    "C1 one '$10' cell → whole batch dies as unexpected_error (skip can't rescue)",
    out.get("error_type") == "unexpected_error",
    f"response={out}",
)
# Contrast: the price batch catches ArithmeticError per row.
create_prices = mcp._tool_manager._tools["create_prices"].fn
ptsv = ("ref\tcommodity\tdate\tvalue\n1\tUSD\t2026-08-01\t$1.0\n")
pout = json.loads(create_prices(prices=ptsv, dry_run=True))
verdict(
    "C2 …while create_prices rejects the same bad cell PER ROW",
    "results" in pout and "rejected" in pout["results"],
    f"response={pout}",
)

# ── L: batch empty date silently defaults to today with no echo ───
tsv = ("ref\tdate\tdescription\tamt1\tacct1\tamt2\tacct2\n"
       "1\t\tNo date\t-10\tAssets:Checking\t10\tExpenses:Groceries\n")
out = json.loads(create_transactions(transactions=tsv, dry_run=True))
verdict(
    "L1 empty date cell defaults to today silently (results carry no date)",
    "results" in out and "today" not in json.dumps(out)
    and date.today().isoformat() not in json.dumps(out),
    f"response={out}",
)

# ── H: scheduled template accepts a placeholder account ────────────
gb4 = GnuCashBook(str(build_business_book(TMP / "h.gnucash")))
try:
    sx = gb4.create_scheduled_transaction(
        name="Bad template", description="x",
        splits=[{"account": "Expenses", "amount": "5"},
                {"account": "Assets:Checking", "amount": "-5"}],
        start_date="2026-01-01", frequency="monthly")
    created = True
    try:
        gb4.create_transaction_from_scheduled(sx["guid"])
        inst = "instantiated?!"
    except ValueError as e:
        inst = f"instantiation fails: {e}"
except ValueError as e:
    created = False
    inst = f"create refused: {e}"
verdict("H1 template on a PLACEHOLDER account is accepted, then fails at every run",
        created and "placeholder" in inst.lower(), inst)

# ── I: duplicate billterm names ───────────────────────────────────
gb4.create_billterm("Net 30", due_days=30)
try:
    gb4.create_billterm("Net 30", due_days=45)
    dup = True
except ValueError:
    dup = False
verdict("I1 create_billterm allows a duplicate name (lookup-by-name then ambiguous)",
        dup, gb4.list_billterms(compact=True))

print("TMP:", TMP)
