"""Prepayments: money a party has paid that no document has absorbed.

GnuCash keeps it in the receivable or payable account in a lot of
its own, attached to the party. Before 1.5 the server had no such
thing, and four findings of the pre-release adversarial review
(``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``) hung on that:

* C37 — an overpayment was refused with advice to book the excess as
  a credit note, which understates the bank and the income;
* C49 — unposting a paid document was refused with advice to void the
  payment, a bank line that may already be reconciled;
* C50 — a prepayment made in desktop was invisible: the dashboard
  called the invoice it covered past due, and no tool could apply it;
* C22 — a cross-currency payment booked the bank amount a quote
  implied, with no way to say what the bank actually moved.

The ROWS are pinned against GnuCash's engine in
``test_parity_prepayment.py``. This file is the behavior around them.
"""

import sqlite3
from datetime import date, timedelta
from decimal import Decimal

import pytest

from gnucash_mcp.book import GnuCashBook

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"
CHECKING = "Assets:Checking"


def _q(path, sql, params=()):
    con = sqlite3.connect(str(path))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _invoice(gb, price, post_date="2026-01-15", customer="000001", **post):
    inv = gb.create_invoice(customer_id=customer)
    gb.add_invoice_entry(
        invoice_id=inv["id"], account="Income:Sales",
        description="Work", quantity="1", price=price,
    )
    gb.post_invoice(
        invoice_id=inv["id"], post_account=AR, post_date=post_date, **post,
    )
    return inv["id"]


@pytest.fixture
def acme(business_book):
    """Acme with two posted invoices: 000001 for 100, 000002 for 50."""
    gb = GnuCashBook(str(business_book))
    gb.create_customer(name="Acme Corp")
    _invoice(gb, "100.00")
    _invoice(gb, "50.00", post_date="2026-01-16")
    return gb, business_book


def _pay(gb, doc, amount, **kw):
    kw.setdefault("payment_date", "2026-01-20")
    return gb.pay_invoice(
        invoice_id=doc, payment_account=CHECKING, amount=amount, **kw,
    )


def _doc(gb, doc):
    return gb.get_invoice(doc, owner_type="customer")


def _unapplied(gb, **kw):
    return gb.get_outstanding_invoices(compact=False, **kw).get(
        "unapplied_payments", [],
    )


class TestOverpayment:
    def test_refused_by_default_and_the_advice_is_not_a_credit_note(self, acme):
        gb, path = acme
        before = _q(path, "select count(*) from transactions")
        with pytest.raises(ValueError) as refusal:
            _pay(gb, "000001", "120.00")
        message = str(refusal.value)
        assert "allow_prepayment" in message
        assert "exceeds the outstanding balance of 100.00" in message
        # C37: following the old text booked 100 at a bank that got
        # 120, and took 20 off income that was earned.
        assert "credit note" not in message.lower()
        assert "credit_note" not in message
        assert _q(path, "select count(*) from transactions") == before

    def test_the_bank_gets_what_moved_and_the_excess_is_the_partys(self, acme):
        gb, _ = acme
        bank_before = gb.get_balance(CHECKING, date(2026, 12, 31))
        income_before = gb.get_balance("Income:Sales", date(2026, 12, 31))

        paid = _pay(gb, "000001", "120.00", allow_prepayment=True)

        assert paid["status"] == "paid"
        assert paid["payment"] == "120.00"
        assert paid["remaining_balance"] == "0.00"
        assert paid["prepayment"]["amount"] == "20.00"
        assert paid["prepayment"]["party"] == "Acme Corp"
        assert gb.get_balance(CHECKING, date(2026, 12, 31)) - bank_before == Decimal("120")
        assert gb.get_balance("Income:Sales", date(2026, 12, 31)) == income_before

        doc = _doc(gb, "000001")
        assert (doc["status"], doc["amount_paid"], doc["amount_due"]) == (
            "paid", "100.00", "0.00",
        )
        assert "overpaid" not in doc
        # The payment names the bank, not its own sibling split.
        assert [(p["amount"], p["from"]) for p in doc["payments"]] == [
            ("100.00", CHECKING),
        ]
        assert [
            (u["party_name"], u["amount"], u["currency"], u["since"])
            for u in _unapplied(gb)
        ] == [("Acme Corp", "20.00", "USD", "2026-01-20")]

    def test_dry_run_shows_the_split_and_books_nothing(self, acme):
        gb, path = acme
        before = _q(path, "select count(*) from splits")
        plan = _pay(gb, "000001", "120.00", allow_prepayment=True, dry_run=True)
        assert plan["status"] == "would_pay"
        assert plan["prepayment"]["amount"] == "20.00"
        held = [r for r in plan["proposed_splits"] if r.get("prepayment")]
        assert [(r["account"], r["value"]) for r in held] == [(AR, "-20.00")]
        assert sum(Decimal(r["value"]) for r in plan["proposed_splits"]) == 0
        assert _q(path, "select count(*) from splits") == before
        assert _unapplied(gb) == []

    def test_nothing_to_settle_is_not_a_prepayment(self, acme):
        gb, _ = acme
        _pay(gb, "000001", "100.00")
        with pytest.raises(ValueError, match="nothing left to pay"):
            _pay(gb, "000001", "25.00", allow_prepayment=True)

    def test_a_bill_overpaid_is_the_business_money_with_the_vendor(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
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
        paid = gb.pay_invoice(
            invoice_id=bill["id"], payment_account=CHECKING, amount="95.00",
            payment_date="2026-01-20", owner_type="vendor",
            allow_prepayment=True,
        )
        assert paid["prepayment"]["amount"] == "15.00"
        assert [
            (u["party_type"], u["party_name"], u["amount"])
            for u in _unapplied(gb)
        ] == [("vendor", "Supplier Ltd", "15.00")]
        assert _unapplied(gb, owner_type="customer") == []
        assert len(_unapplied(gb, owner_type="vendor")) == 1


class TestSettleFromPrepayment:
    def test_moves_the_earlier_payment_and_creates_nothing(self, acme):
        gb, path = acme
        first = _pay(gb, "000001", "120.00", allow_prepayment=True)
        txns = _q(path, "select count(*) from transactions")
        bank = gb.get_balance(CHECKING, date(2026, 12, 31))

        result = gb.pay_invoice(invoice_id="000002", from_prepayment=True)

        assert result["status"] == "partial"
        assert result["applied_from_prepayment"] == "20.00"
        assert result["remaining_balance"] == "30.00"
        assert result["unapplied_remaining"] == "0.00"
        assert [u["guid"] for u in result["from_payments"]] == [
            first["transaction_guid"]
        ]
        assert _q(path, "select count(*) from transactions") == txns
        assert gb.get_balance(CHECKING, date(2026, 12, 31)) == bank
        doc = _doc(gb, "000002")
        assert (doc["amount_paid"], doc["amount_due"]) == ("20.00", "30.00")
        assert [(p["amount"], p["from"], p["guid"]) for p in doc["payments"]] == [
            ("20.00", CHECKING, first["transaction_guid"]),
        ]
        assert _unapplied(gb) == []

    def test_a_stated_amount_takes_part_and_leaves_the_rest(self, acme):
        gb, _ = acme
        _pay(gb, "000001", "170.00", allow_prepayment=True)  # 70 held

        part = gb.pay_invoice(
            invoice_id="000002", amount="30.00", from_prepayment=True,
        )
        assert part["remaining_balance"] == "20.00"
        assert part["unapplied_remaining"] == "40.00"
        assert _unapplied(gb)[0]["amount"] == "40.00"

        rest = gb.pay_invoice(invoice_id="000002", from_prepayment=True)
        assert rest["status"] == "paid"
        assert rest["applied_from_prepayment"] == "20.00"
        assert _unapplied(gb)[0]["amount"] == "20.00"
        assert _doc(gb, "000002")["amount_paid"] == "50.00"

    def test_dry_run_plans_and_changes_nothing(self, acme):
        gb, path = acme
        _pay(gb, "000001", "120.00", allow_prepayment=True)
        before = _q(path, "select guid, lot_guid, value_num from splits order by guid")
        plan = gb.pay_invoice(
            invoice_id="000002", from_prepayment=True, dry_run=True,
        )
        assert plan["status"] == "would_apply"
        assert plan["applied_from_prepayment"] == "20.00"
        assert plan["remaining_balance_after"] == "30.00"
        assert _q(
            path, "select guid, lot_guid, value_num from splits order by guid",
        ) == before

    def test_refused_when_the_party_has_none(self, acme):
        gb, _ = acme
        with pytest.raises(ValueError) as refusal:
            gb.pay_invoice(invoice_id="000002", from_prepayment=True)
        assert "no unapplied payment" in str(refusal.value)
        assert "payment_account" in str(refusal.value)

    def test_another_partys_money_is_not_used(self, acme):
        gb, _ = acme
        gb.create_customer(name="Other Co")
        other = _invoice(gb, "40.00", customer="000002")
        _pay(gb, "000001", "120.00", allow_prepayment=True)
        with pytest.raises(ValueError, match="Other Co has no unapplied"):
            gb.pay_invoice(invoice_id=other, from_prepayment=True)

    def test_more_than_can_be_applied_is_refused_with_the_numbers(self, acme):
        gb, _ = acme
        _pay(gb, "000001", "120.00", allow_prepayment=True)
        with pytest.raises(ValueError) as refusal:
            gb.pay_invoice(
                invoice_id="000002", amount="45.00", from_prepayment=True,
            )
        message = str(refusal.value)
        assert "owes 50.00" in message and "20.00 USD unapplied" in message

    @pytest.mark.parametrize("extra", [
        {"payment_account": CHECKING},
        {"payment_date": "2026-02-01"},
        {"memo": "chk 9"},
        {"allow_prepayment": True},
        {"apply_discount": True},
        {"payment_account_amount": "20.00"},
    ])
    def test_arguments_that_describe_a_payment_are_refused(self, acme, extra):
        gb, _ = acme
        _pay(gb, "000001", "120.00", allow_prepayment=True)
        with pytest.raises(ValueError, match="not apply with from_prepayment"):
            gb.pay_invoice(invoice_id="000002", from_prepayment=True, **extra)

    def test_a_plain_payment_still_needs_its_account_and_amount(self, acme):
        gb, _ = acme
        with pytest.raises(ValueError, match="payment_account and amount"):
            gb.pay_invoice(invoice_id="000001", amount="10.00")


class TestUnpostKeepsPayments:
    def test_unpost_edit_repost_and_settle_from_the_kept_payment(self, acme):
        gb, path = acme
        paid = _pay(gb, "000001", "100.00")
        bank = gb.get_balance(CHECKING, date(2026, 12, 31))

        gb.unpost_invoice(invoice_id="000001", owner_type="customer")
        assert _unapplied(gb)[0]["amount"] == "100.00"
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="More", quantity="1", price="25.00",
        )
        gb.post_invoice(invoice_id="000001", post_account=AR,
                        post_date="2026-01-25")
        assert _doc(gb, "000001")["unapplied_payments_available"] == "100.00"

        result = gb.pay_invoice(invoice_id="000001", from_prepayment=True)

        assert result["status"] == "partial"
        assert result["remaining_balance"] == "25.00"
        assert result["from_payments"][0]["guid"] == paid["transaction_guid"]
        assert gb.get_balance(CHECKING, date(2026, 12, 31)) == bank
        assert _unapplied(gb) == []
        # One payment transaction throughout; the old lot is gone.
        assert _q(
            path,
            "select count(*) from slots where name = 'trans-txn-type' "
            "and string_val = 'P'",
        ) == [(1,)]
        assert _q(
            path,
            "select count(*) from lots where guid not in "
            "(select lot_guid from splits where lot_guid is not null)",
        ) == [(0,)]

    def test_a_reconciled_bank_line_stays_reconciled(self, acme):
        """C49: the old remedy was to void the payment first."""
        gb, path = acme
        paid = _pay(gb, "000001", "100.00")
        bank_split = _q(
            path,
            "select s.guid from splits s join accounts a on a.guid = "
            "s.account_guid join transactions t on t.guid = s.tx_guid "
            "where a.name = 'Checking' and t.guid like ?",
            (paid["transaction_guid"] + "%",),
        )[0][0]
        gb.set_reconcile_state(bank_split, "y", date(2026, 1, 31))

        gb.unpost_invoice(invoice_id="000001", owner_type="customer")

        assert _q(
            path, "select reconcile_state, value_num from splits where guid = ?",
            (bank_split,),
        ) == [("y", 10000)]

    def test_a_credit_note_application_is_removed_and_the_note_reopens(
        self, acme,
    ):
        gb, path = acme
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="1", price="30.00",
        )
        gb.post_invoice(invoice_id=cn["id"], post_account=AR,
                        post_date="2026-01-16", owner_type="customer")
        gb.apply_credit_note(
            credit_note_id=cn["id"], applies_to_invoice_id="000001",
            owner_type="customer",
        )

        result = gb.unpost_invoice(invoice_id="000001", owner_type="customer")

        assert result["links_removed"] == 1
        assert "payments_kept" not in result
        assert gb.get_invoice(cn["id"], owner_type="customer")["amount_due"] == "30.00"
        assert _q(
            path,
            "select count(*) from slots where name = 'trans-txn-type' "
            "and string_val = 'L'",
        ) == [(0,)]


class TestTheDashboardAndTheLists:
    def test_a_past_due_invoice_names_the_money_already_received(
        self, business_book,
    ):
        """C50: 500 received ahead of a 500 invoice read as "Past due
        … USD 500.00" in the first-call work queue."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        old = (date.today() - timedelta(days=60)).isoformat()
        first = _invoice(gb, "100.00", post_date=old)
        gb.pay_invoice(
            invoice_id=first, payment_account=CHECKING, amount="600.00",
            payment_date=old, allow_prepayment=True,
        )
        second = _invoice(gb, "500.00", post_date=old)

        summary = gb.get_book_summary()
        line = next(
            l for l in summary.splitlines() if "Past due invoice: Acme" in l
        )
        assert "USD 500.00 in unapplied payments" in line
        assert "from_prepayment" in line
        assert _doc(gb, second)["unapplied_payments_available"] == "500.00"

        gb.pay_invoice(invoice_id=second, from_prepayment=True)

        assert "Past due invoice" not in gb.get_book_summary()
        assert "unapplied_payments_available" not in _doc(gb, second)

    def test_the_compact_outstanding_list_shows_unapplied_payments(self, acme):
        gb, _ = acme
        assert "Unapplied payments" not in gb.get_outstanding_invoices()
        _pay(gb, "000001", "120.00", allow_prepayment=True)
        text = gb.get_outstanding_invoices()
        tail = text[text.index("Unapplied payments"):].splitlines()
        assert "from_prepayment" in tail[0]
        assert tail[1].split("\t") == [
            "Acme Corp", "USD 20.00", "since 2026-01-20", AR,
        ]
        assert "Unapplied payments" not in gb.get_outstanding_invoices(
            owner_type="vendor",
        )

    def test_voiding_the_payment_takes_the_prepayment_with_it(self, acme):
        gb, _ = acme
        paid = _pay(gb, "000001", "120.00", allow_prepayment=True)
        gb.void_transaction(paid["transaction_guid"], "bounced")
        assert _unapplied(gb) == []
        assert _doc(gb, "000001")["amount_due"] == "100.00"


class TestWhatTheBankActuallyMoved:
    """C22: ``amount`` is in the document's currency; the bank line is
    in the bank's."""

    @pytest.fixture
    def euro(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="Receivable EUR", account_type="RECEIVABLE",
            parent="Assets", commodity="EUR",
        )
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value="1.12",
            price_date=date(2026, 1, 15),
        )
        gb.create_customer(name="Berlin GmbH", currency="EUR")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="900.00",
        )
        gb.post_invoice(
            invoice_id=inv["id"], post_account="Assets:Receivable EUR",
            post_date="2026-01-15",
        )
        return gb, business_book, inv["id"]

    def test_the_stated_amount_is_what_books(self, euro):
        gb, path, doc = euro
        bank = gb.get_balance(CHECKING, date(2026, 12, 31))
        paid = gb.pay_invoice(
            invoice_id=doc, payment_account=CHECKING, amount="900.00",
            payment_date="2026-01-17", payment_account_amount="1003.47",
        )
        assert gb.get_balance(CHECKING, date(2026, 12, 31)) - bank == Decimal("1003.47")
        assert paid["status"] == "paid"
        assert paid["payment_account_amount"] == "1003.47"
        # The day's price is the rate paid, not the quote re-dated.
        num, denom = _q(
            path,
            "select value_num, value_denom from prices "
            "where date like '2026-01-17%'",
        )[0]
        assert Decimal(num) / Decimal(denom) == Decimal("1003.47") / Decimal("900")

    def test_no_quote_near_the_payment_date_is_needed(self, euro):
        gb, _, doc = euro
        late = "2026-09-01"  # 229 days after the only quote
        with pytest.raises(ValueError):
            gb.pay_invoice(
                invoice_id=doc, payment_account=CHECKING, amount="900.00",
                payment_date=late,
            )
        paid = gb.pay_invoice(
            invoice_id=doc, payment_account=CHECKING, amount="900.00",
            payment_date=late, payment_account_amount="1050.00",
        )
        assert paid["status"] == "paid"
        assert "fx_stale" not in paid

    def test_refused_when_both_are_the_same_currency(self, acme):
        gb, _ = acme
        with pytest.raises(ValueError, match="another currency"):
            _pay(gb, "000001", "100.00", payment_account_amount="100.00")


class TestThePartyCannotBeDeletedFromUnderItsMoney:
    def test_customer(self, acme):
        gb, _ = acme
        _pay(gb, "000001", "120.00", allow_prepayment=True)
        with pytest.raises(ValueError, match="1 unapplied payment"):
            gb.delete_customer("000001")

    def test_job(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        job = gb.create_job(owner_id="000001", owner_type="customer", name="Fit-out")
        inv = gb.create_invoice(customer_id="000001", job_id=job["id"])
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="70.00",
        )
        gb.post_invoice(invoice_id=inv["id"], post_account=AR,
                        post_date="2026-01-16")
        _pay(gb, inv["id"], "70.00", owner_type="customer")
        gb.unpost_invoice(invoice_id=inv["id"], owner_type="customer")
        # The lot is the JOB's (gncInvoiceUnpost attaches the
        # document's own owner); the money is Acme's to use.
        assert _unapplied(gb)[0]["party_name"] == "Acme Corp"
        with pytest.raises(ValueError, match="unapplied payment"):
            gb.delete_job(job["id"], force=True)
