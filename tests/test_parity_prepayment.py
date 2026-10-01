"""Prepayments, row for row against GnuCash's own engine.

Each scenario runs twice from one base book: through the server, and
through the engine functions the desktop dialogs call
(``tests/fixtures/engine_twin.py``). The two books must dump to the
same text. The engine's dumps are kept in
``fixtures/parity_prepayment_engine.json`` so a machine without
GnuCash still checks the server against them; where ``gnucash-cli``
exists, the live test re-derives each one from the engine and fails
if the recording has drifted.

What it pins (pre-release adversarial review 2026-09-30,
``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``, C22 / C37 / C49 /
C50):

* an overpayment is ONE payment transaction whose receivable leg is
  split in two — the document's lot and a lot of the party's own
  (``gncOwner/owner-type`` int64, ``gncOwner/owner-guid`` GUID, no
  title);
* settling a later document from that money moves the split, creates
  nothing, and destroys the emptied lot;
* unposting a paid document keeps the payment: the lot loses its
  document link and gains the document's owner (the JOB, when the
  document was a job's).

Regenerate the recording after a GnuCash upgrade with
``uv run python tests/test_parity_prepayment.py record``.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook

_HERE = Path(__file__).resolve().parent
_RECORDED = _HERE / "fixtures" / "parity_prepayment_engine.json"
sys.path.insert(0, str(_HERE / "fixtures"))
from engine_twin import (  # noqa: E402
    debris_found, dump, engine_run, find_gnucash_cli, guid_of,
)

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"
CHECKING = "Assets:Checking"
# The whole book is dumped, the base included: a lot link is dated at
# its documents' latest activity, which is a base date.
_BASE_ENDS = None


def _base(gb: GnuCashBook) -> None:
    """Acme with two posted invoices (100, 50), one of them a job's;
    a supplier with one posted bill (80)."""
    gb.create_customer(name="Acme Corp")
    job = gb.create_job(owner_id="000001", owner_type="customer", name="Fit-out")
    for price, day, job_id in (
        ("100.00", "2026-01-15", None), ("50.00", "2026-01-16", None),
        ("70.00", "2026-01-16", job["id"]),
    ):
        inv = gb.create_invoice(customer_id="000001", job_id=job_id)
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price=price,
        )
        gb.post_invoice(invoice_id=inv["id"], post_account=AR, post_date=day)
    gb.create_vendor(name="Supplier Ltd")
    bill = gb.create_bill(vendor_id="000001")
    gb.add_bill_entry(
        bill_id=bill["id"], account="Expenses:Services",
        description="Hosting", quantity="1", price="80.00",
    )
    gb.post_invoice(
        invoice_id=bill["id"], post_account=AP, post_date="2026-01-16",
        owner_type="vendor",
    )


def _ids(path: Path) -> dict[str, str]:
    doc = "select guid from invoices where id = ? and owner_type = ?"
    ids = {
        "inv1": guid_of(path, doc, "000001", 2),
        "inv2": guid_of(path, doc, "000002", 2),
        "jobinv": guid_of(path, doc, "000003", 3),
        "bill": guid_of(path, doc, "000001", 4),
        "chk": guid_of(path, "select guid from accounts where name = 'Checking'"),
    }
    try:  # only the euro scenario has a fourth customer document
        ids["eur"] = guid_of(path, doc, "000004", 2)
    except TypeError:
        pass
    return ids


def _pay(gb, doc, amount, **kw):
    return gb.pay_invoice(
        invoice_id=doc, payment_account=CHECKING, amount=amount,
        payment_date="2026-01-20", memo="chk 12", **kw,
    )


def _engine_pay(i, doc, amount):
    return f"pay|{i[doc]}|{i['chk']}|{amount}|1|20|1|2026|chk 12|"


def _credit_note(gb: GnuCashBook) -> None:
    """A posted 30.00 credit note for Acme. Only in the scenario that
    needs it: the engine's auto-apply would offset it against any
    invoice it is run on."""
    cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
    gb.add_credit_note_entry(
        credit_note_id=cn["id"], account="Income:Sales",
        description="Refund", quantity="1", price="30.00",
    )
    gb.post_invoice(
        invoice_id=cn["id"], post_account=AR, post_date="2026-01-16",
        owner_type="customer",
    )


def _euro_invoice(gb: GnuCashBook) -> None:
    """A EUR customer with a EUR receivable account and one posted
    EUR 100 invoice, at 1.10 USD on the posting day — the rate the
    payment is then made at, so no exchange difference is realized."""
    gb.create_account(
        name="Receivable EUR", account_type="RECEIVABLE", parent="Assets",
        commodity="EUR",
    )
    gb.create_price(
        commodity="EUR", namespace="CURRENCY", value="1.10",
        price_date=date(2026, 1, 15),
    )
    gb.create_customer(name="Berlin GmbH", currency="EUR")
    inv = gb.create_invoice(customer_id="000002")
    gb.add_invoice_entry(
        invoice_id=inv["id"], account="Income:Sales",
        description="Work", quantity="1", price="100.00",
    )
    gb.post_invoice(
        invoice_id=inv["id"], post_account="Assets:Receivable EUR",
        post_date="2026-01-15",
    )


def _off_owner_currency_invoice(gb: GnuCashBook) -> None:
    """A EUR 100 invoice for Acme, whose own currency is USD — a
    document desktop's invoice window cannot create and the server
    can (finding C48). Posted to a EUR receivable at 1.10."""
    gb.create_account(
        name="Receivable EUR", account_type="RECEIVABLE", parent="Assets",
        commodity="EUR",
    )
    gb.create_price(
        commodity="EUR", namespace="CURRENCY", value="1.10",
        price_date=date(2026, 1, 15),
    )
    inv = gb.create_invoice(customer_id="000001", currency="EUR", force=True)
    assert "currency is USD" in inv["warnings"][0]
    gb.add_invoice_entry(
        invoice_id=inv["id"], account="Income:Sales",
        description="Work", quantity="1", price="100.00",
    )
    gb.post_invoice(
        invoice_id=inv["id"], post_account="Assets:Receivable EUR",
        post_date="2026-01-15",
    )


# scenario → (what the server does, what the engine does[, extra base])
SCENARIOS = {
    "overpay": (
        lambda gb: _pay(gb, "000001", "120.00", owner_type="customer",
                        allow_prepayment=True),
        lambda i: [_engine_pay(i, "inv1", 120)],
    ),
    "overpay_then_settle_another": (
        lambda gb: (
            _pay(gb, "000001", "120.00", owner_type="customer",
                 allow_prepayment=True),
            gb.pay_invoice(invoice_id="000002", owner_type="customer",
                           from_prepayment=True),
        ),
        lambda i: [_engine_pay(i, "inv1", 120), f"autoapply|{i['inv2']}"],
    ),
    "overpay_covers_another_in_full": (
        lambda gb: (
            _pay(gb, "000001", "150.00", owner_type="customer",
                 allow_prepayment=True),
            gb.pay_invoice(invoice_id="000002", owner_type="customer",
                           from_prepayment=True),
        ),
        lambda i: [_engine_pay(i, "inv1", 150), f"autoapply|{i['inv2']}"],
    ),
    "bill_overpay": (
        lambda gb: _pay(gb, "000001", "95.00", owner_type="vendor",
                        allow_prepayment=True),
        # The engine's amount is signed as the dialog signs it: money
        # paid OUT to a vendor is negative.
        lambda i: [_engine_pay(i, "bill", -95)],
    ),
    "unpost_paid": (
        lambda gb: (
            _pay(gb, "000001", "100.00", owner_type="customer"),
            gb.unpost_invoice(invoice_id="000001", owner_type="customer"),
        ),
        lambda i: [_engine_pay(i, "inv1", 100), f"unpost|{i['inv1']}"],
    ),
    "unpost_part_paid": (
        lambda gb: (
            _pay(gb, "000001", "40.00", owner_type="customer"),
            gb.unpost_invoice(invoice_id="000001", owner_type="customer"),
        ),
        lambda i: [_engine_pay(i, "inv1", 40), f"unpost|{i['inv1']}"],
    ),
    "unpost_paid_job_invoice": (
        lambda gb: (
            _pay(gb, "000003", "70.00", owner_type="customer"),
            gb.unpost_invoice(invoice_id="000003", owner_type="customer"),
        ),
        lambda i: [_engine_pay(i, "jobinv", 70), f"unpost|{i['jobinv']}"],
    ),
    "unpost_with_credit_note_applied": (
        lambda gb: (
            gb.apply_credit_note(
                credit_note_id="000004", applies_to_invoice_id="000001",
                owner_type="customer",
            ),
            gb.unpost_invoice(invoice_id="000001", owner_type="customer"),
        ),
        # Auto-apply on the invoice links it to the only opposite lot
        # Acme has: the credit note's.
        lambda i: [f"autoapply|{i['inv1']}", f"unpost|{i['inv1']}"],
        _credit_note,
    ),
    "euro_overpay_from_dollars": (
        lambda gb: gb.pay_invoice(
            invoice_id="000004", payment_account=CHECKING, amount="120.00",
            payment_account_amount="132.00", payment_date="2026-01-20",
            memo="chk 12", owner_type="customer", allow_prepayment=True,
        ),
        lambda i: [f"pay|{i['eur']}|{i['chk']}|120|11/10|20|1|2026|chk 12|"],
        _euro_invoice,
    ),
    # C48, ruling 3: the engine half of "does desktop read a document
    # in a currency its owner doesn't use". Its payment code does —
    # the same rows as the server's.
    "off_owner_currency_invoice_paid": (
        lambda gb: gb.pay_invoice(
            invoice_id="000004", payment_account=CHECKING, amount="100.00",
            payment_account_amount="110.00", payment_date="2026-01-20",
            memo="chk 12", owner_type="customer",
        ),
        lambda i: [f"pay|{i['eur']}|{i['chk']}|100|11/10|20|1|2026|chk 12|"],
        _off_owner_currency_invoice,
    ),
    "off_owner_currency_invoice_unposted_after_payment": (
        lambda gb: (
            gb.pay_invoice(
                invoice_id="000004", payment_account=CHECKING,
                amount="100.00", payment_account_amount="110.00",
                payment_date="2026-01-20", memo="chk 12",
                owner_type="customer",
            ),
            gb.unpost_invoice(invoice_id="000004", owner_type="customer"),
        ),
        lambda i: [
            f"pay|{i['eur']}|{i['chk']}|100|11/10|20|1|2026|chk 12|",
            f"unpost|{i['eur']}",
        ],
        _off_owner_currency_invoice,
    ),
    "unposted_payment_settles_another": (
        lambda gb: (
            _pay(gb, "000001", "100.00", owner_type="customer"),
            gb.unpost_invoice(invoice_id="000001", owner_type="customer"),
            gb.pay_invoice(invoice_id="000002", owner_type="customer",
                           from_prepayment=True),
        ),
        lambda i: [
            _engine_pay(i, "inv1", 100), f"unpost|{i['inv1']}",
            f"autoapply|{i['inv2']}",
        ],
    ),
}


def _build(path: Path, scenario: str) -> None:
    gb = GnuCashBook(str(path))
    _base(gb)
    extra = SCENARIOS[scenario][2:]
    if extra:
        extra[0](gb)


@pytest.fixture
def base(business_book, request):
    _build(business_book, request.getfixturevalue("scenario"))
    return business_book


def _recorded() -> dict[str, str]:
    return json.loads(_RECORDED.read_text())


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_server_writes_what_the_engine_writes(base, scenario):
    server = SCENARIOS[scenario][0]
    server(GnuCashBook(str(base)))
    assert dump(base, _BASE_ENDS) == _recorded()[scenario]["dump"]
    # The server leaves none of what the allowlist excuses the engine
    # for (engine_twin.ENGINE_DEBRIS).
    assert debris_found(base) == {}


@pytest.mark.skipif(
    find_gnucash_cli() is None, reason="gnucash-cli not on this machine",
)
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_recording_is_what_the_engine_writes_today(base, scenario):
    engine = SCENARIOS[scenario][1]
    engine_run(base, engine(_ids(base)))
    recorded = _recorded()[scenario]
    assert dump(base, _BASE_ENDS) == recorded["dump"]
    # How much it leaves behind is recorded too, kind by kind, so an
    # exception is never silent.
    assert debris_found(base) == recorded["engine_debris"]


# ── C48: what desktop makes of a document in another currency ───────

# What GnuCash 5.12's engine answered, recorded 2026-09-30: Acme's
# balance with 220 of open USD invoices, and with an unpaid EUR 100
# invoice beside them.
_ENGINE_BALANCE = {"usd_only": "balance=220", "with_eur_invoice": "balance=220"}


def test_the_reason_a_foreign_currency_document_is_refused():
    """``gncOwnerGetBalanceInCurrency`` walks only the receivable
    accounts in the OWNER's currency, so a document in another
    currency is not in the party's balance at all. That is the
    misread ruling 3 on finding C48 asked for proof of, and why
    ``create_document`` refuses the mismatch unless forced."""
    assert _ENGINE_BALANCE["with_eur_invoice"] == _ENGINE_BALANCE["usd_only"]


@pytest.mark.skipif(
    find_gnucash_cli() is None, reason="gnucash-cli not on this machine",
)
@pytest.mark.parametrize("case, scenario", [
    ("usd_only", "overpay"),
    ("with_eur_invoice", "off_owner_currency_invoice_paid"),
])
def test_the_engine_still_leaves_it_out_of_the_balance(
    business_book, case, scenario,
):
    _build(business_book, scenario)  # the base; nothing paid yet
    acme = guid_of(
        business_book, "select guid from customers where name = 'Acme Corp'",
    )
    assert engine_run(business_book, [f"balance|{acme}"]) == [
        _ENGINE_BALANCE[case]
    ]
    # The server lists the EUR invoice as owed; desktop's balance
    # does not count it.
    owed = GnuCashBook(str(business_book)).get_outstanding_invoices(
        compact=False, customer_id="000001",
    )["invoices"]
    assert ("EUR" in {d["currency"] for d in owed}) == (
        case == "with_eur_invoice"
    )


def record() -> None:
    import tempfile

    import tests.conftest as conftest

    out = {}
    for scenario in sorted(SCENARIOS):
        engine = SCENARIOS[scenario][1]
        with tempfile.TemporaryDirectory() as tmp:
            path = conftest.business_book.__wrapped__(Path(tmp))
            _build(path, scenario)
            engine_run(path, engine(_ids(path)))
            out[scenario] = {
                "dump": dump(path, _BASE_ENDS),
                "engine_debris": debris_found(path),
            }
        print(f"recorded {scenario}")
    _RECORDED.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["record"]:
        sys.path.insert(0, str(_HERE.parent))
        record()
