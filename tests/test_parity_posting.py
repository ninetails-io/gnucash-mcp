"""Posting a document, row for row against GnuCash's own engine.

``gncInvoicePostToAccount`` through ``fixtures/engine_twin.py``, and
``post_invoice`` on a copy of the same book; the two must dump to the
same text. Written for two findings of the pre-release adversarial
review (``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``):

* C10 — a posted document points at a hidden COPY of its billing
  term, so editing the term later does not rewrite the terms of what
  is already posted. The server left it on the live term.
* C3 — an employee's voucher lines paid with the company card post to
  the card account, one split per line, and come off what is owed to
  the employee. The server posted them to the payable.

A third claim is OPEN. The review expected each posted line to point
at a hidden copy of its TAX TABLE (``gncTaxTableReturnChild`` in the
post path). In this headless run the engine makes the copy and
repoints the line in memory, but the line's row is never re-saved:
the entries still name the parent table, and the copy is referenced
by nothing (``engine_twin.ENGINE_DEBRIS``, counted per scenario in
the recording). The server leaves entries on the live table for now.
The bookkeeper ruled (2026-09-30, round 2, item 4) that two oracles
conflict here and the GUI gate decides: post a taxed invoice in the
GUI on a SQL book, then read ``taxtables`` and the entries'
``i_taxtable``.

Regenerate after a GnuCash upgrade with
``uv run python tests/test_parity_posting.py record``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook

_HERE = Path(__file__).resolve().parent
_RECORDED = _HERE / "fixtures" / "parity_posting_engine.json"
sys.path.insert(0, str(_HERE / "fixtures"))
from engine_twin import (  # noqa: E402
    debris_found, dump, engine_run, find_gnucash_cli, guid_of,
)

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"
# The fixture's opening-balance transaction (2025-12-31) is written by
# piecash directly, and the first business write normalizes its rows;
# the engine side never makes one. Nothing posting touches.
_SINCE = "2026-01-01"


def _tax_and_terms(gb: GnuCashBook) -> None:
    gb.create_account(
        name="GST Payable", account_type="LIABILITY", parent="Liabilities",
    )
    gb.create_taxtable(name="T5", entries=[{
        "type": "percentage", "amount": "5",
        "account": "Liabilities:GST Payable",
    }])
    gb.create_billterm(name="Net 30", due_days=30)


def _invoices(gb: GnuCashBook) -> None:
    """Two draft invoices for Acme on Net 30, each with taxed lines."""
    _tax_and_terms(gb)
    gb.create_customer(name="Acme Corp")
    for prices in (("100.00", "40.00"), ("19.99",)):
        inv = gb.create_invoice(customer_id="000001", term="Net 30")
        for i, price in enumerate(prices):
            gb.add_invoice_entry(
                invoice_id=inv["id"], account="Income:Sales",
                description=f"Work {i}", quantity="3", price=price,
                taxtable="T5",
            )


def _plain(gb: GnuCashBook) -> None:
    gb.create_customer(name="Acme Corp")
    inv = gb.create_invoice(customer_id="000001")
    gb.add_invoice_entry(
        invoice_id=inv["id"], account="Income:Sales",
        description="Work", quantity="1", price="100.00",
    )
    cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
    gb.add_credit_note_entry(
        credit_note_id=cn["id"], account="Income:Sales",
        description="Refund", quantity="1", price="30.00",
    )


def _bill(gb: GnuCashBook) -> None:
    _tax_and_terms(gb)
    gb.create_vendor(name="Supplier Ltd")
    bill = gb.create_bill(vendor_id="000001", term="Net 30")
    gb.add_bill_entry(
        bill_id=bill["id"], account="Expenses:Services",
        description="Hosting", quantity="1", price="80.00", taxtable="T5",
    )


def _card_voucher(gb: GnuCashBook) -> None:
    """A voucher as desktop's voucher window drafts it: one line paid
    in cash, two with the company card, and 5.00 extra to charge the
    card. The server writes none of those three things, so the rows
    are set the way desktop stores them."""
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
    con = sqlite3.connect(gb.book_path)
    try:
        con.execute(
            "update employees set ccard_guid = "
            "(select guid from accounts where name = 'Company Card')"
        )
        con.execute(
            "update entries set b_paytype = 2 "
            "where description in ('Hotel', 'Dinner')"
        )
        con.execute(
            "update invoices set charge_amt_num = 500, charge_amt_denom = 100"
        )
        con.commit()
    finally:
        con.close()


def _post(gb, doc, account, **kw):
    return gb.post_invoice(
        invoice_id=doc, post_account=account, post_date="2026-01-15", **kw,
    )


def _engine_post(path: Path, doc: str, owner_type: int, account: str,
                 due: str = "15|1|2026") -> str:
    inv = guid_of(
        path, "select guid from invoices where id = ? and owner_type = ?",
        doc, owner_type,
    )
    acct = guid_of(path, "select guid from accounts where name = ?", account)
    return f"post|{inv}|{acct}|15|1|2026|{due}|"


# scenario → (extra base, what the server does, what the engine does)
SCENARIOS = {
    "plain_invoice": (
        _plain,
        lambda gb: _post(gb, "000001", AR, owner_type="customer"),
        lambda p: [_engine_post(p, "000001", 2, "Accounts Receivable")],
    ),
    "credit_note": (
        _plain,
        lambda gb: _post(gb, "000002", AR, owner_type="customer"),
        lambda p: [_engine_post(p, "000002", 2, "Accounts Receivable")],
    ),
    "invoice_with_terms_and_tax": (
        _invoices,
        lambda gb: _post(gb, "000001", AR, owner_type="customer"),
        lambda p: [
            _engine_post(p, "000001", 2, "Accounts Receivable", "14|2|2026"),
        ],
    ),
    "second_invoice_shares_the_posted_copy": (
        _invoices,
        lambda gb: (
            _post(gb, "000001", AR, owner_type="customer"),
            _post(gb, "000002", AR, owner_type="customer"),
        ),
        lambda p: [
            _engine_post(p, "000001", 2, "Accounts Receivable", "14|2|2026"),
            _engine_post(p, "000002", 2, "Accounts Receivable", "14|2|2026"),
        ],
    ),
    "bill_with_terms_and_tax": (
        _bill,
        lambda gb: _post(gb, "000001", AP, owner_type="vendor"),
        lambda p: [
            _engine_post(p, "000001", 4, "Accounts Payable", "14|2|2026"),
        ],
    ),
    "voucher_with_card_lines": (
        _card_voucher,
        lambda gb: _post(gb, "000001", AP, owner_type="employee"),
        lambda p: [_engine_post(p, "000001", 5, "Accounts Payable")],
    ),
}


@pytest.fixture
def base(business_book, request):
    scenario = request.getfixturevalue("scenario")
    SCENARIOS[scenario][0](GnuCashBook(str(business_book)))
    return business_book


def _recorded() -> dict[str, str]:
    return json.loads(_RECORDED.read_text())


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_server_posts_what_the_engine_posts(base, scenario):
    SCENARIOS[scenario][1](GnuCashBook(str(base)))
    assert dump(base, _SINCE) == _recorded()[scenario]["dump"]
    assert debris_found(base) == {}


@pytest.mark.skipif(
    find_gnucash_cli() is None, reason="gnucash-cli not on this machine",
)
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_recording_is_what_the_engine_writes_today(base, scenario):
    engine_run(base, SCENARIOS[scenario][2](base))
    recorded = _recorded()[scenario]
    assert dump(base, _SINCE) == recorded["dump"]
    assert debris_found(base) == recorded["engine_debris"]


def record() -> None:
    import tempfile

    import tests.conftest as conftest

    out = {}
    for scenario in sorted(SCENARIOS):
        setup, _server, engine = SCENARIOS[scenario]
        with tempfile.TemporaryDirectory() as tmp:
            path = conftest.business_book.__wrapped__(Path(tmp))
            setup(GnuCashBook(str(path)))
            engine_run(path, engine(path))
            out[scenario] = {
                "dump": dump(path, _SINCE),
                "engine_debris": debris_found(path),
            }
        print(f"recorded {scenario}")
    _RECORDED.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["record"]:
        sys.path.insert(0, str(_HERE.parent))
        record()
