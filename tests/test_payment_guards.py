"""Settling a document: the money has to move, and a credit has to
meet a debit (review C14, C15)."""

from decimal import Decimal

import pytest

from gnucash_mcp.book import GnuCashBook

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"


def _invoice(gb, lines=(("1", "500.00"),)):
    try:
        gb.create_customer(name="Acme Corp")
    except ValueError:
        pass
    inv = gb.create_invoice(customer_id="000001")
    for qty, price in lines:
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Line", quantity=qty, price=price,
        )
    gb.post_invoice(invoice_id=inv["id"], post_account=AR)
    return inv["id"]


class TestPaymentAccountIsWhereTheMoneyMoved:
    @pytest.mark.parametrize("account", [AR, AP])
    def test_receivable_or_payable_is_refused(self, business_book, account):
        """Paying an invoice "from" its own A/R read ``paid``, left
        the outstanding list, and moved nothing."""
        gb = GnuCashBook(str(business_book))
        doc = _invoice(gb)
        ar_before = gb.get_balance(AR)

        with pytest.raises(ValueError, match="money moved through"):
            gb.pay_invoice(
                invoice_id=doc, payment_account=account, amount="500.00",
            )

        got = gb.get_invoice(doc, owner_type="customer")
        assert (got["status"], got["amount_due"]) == ("posted", "500.00")
        assert gb.get_balance(AR) == ar_before
        assert doc in str(gb.get_outstanding_invoices())

    def test_dry_run_refuses_too(self, business_book):
        gb = GnuCashBook(str(business_book))
        doc = _invoice(gb)
        with pytest.raises(ValueError, match="money moved through"):
            gb.pay_invoice(
                invoice_id=doc, payment_account=AR, amount="500.00",
                dry_run=True,
            )

    def test_a_bank_account_still_pays(self, business_book):
        gb = GnuCashBook(str(business_book))
        doc = _invoice(gb)
        paid = gb.pay_invoice(
            invoice_id=doc, payment_account="Assets:Checking",
            amount="500.00",
        )
        assert paid["status"] == "paid"

    def test_other_account_types_stay_open_as_in_desktop(
        self, business_book,
    ):
        """Desktop's dialog hides only A/R and A/P; an income or
        equity account is selectable there (a write-off, an owner
        contribution)."""
        gb = GnuCashBook(str(business_book))
        doc = _invoice(gb)
        paid = gb.pay_invoice(
            invoice_id=doc, payment_account="Equity:Opening Balance",
            amount="500.00",
        )
        assert paid["status"] == "paid"


class TestCreditNoteNeedsADebitToMeet:
    def _charge_shaped_credit_note(self, gb):
        """Lines that net to a CHARGE: -1 x 170 and 1 x 100 on a
        credit note total -70, which posts as a 70 debit — the same
        side as an invoice."""
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        for qty, price in (("1", "100.00"), ("-1", "170.00")):
            gb.add_credit_note_entry(
                credit_note_id=cn["id"], account="Income:Sales",
                description="Line", quantity=qty, price=price,
                owner_type="customer",
            )
        gb.post_invoice(
            invoice_id=cn["id"], post_account=AR, owner_type="customer",
        )
        return cn["id"]

    def test_same_side_lots_are_not_offset(self, business_book):
        gb = GnuCashBook(str(business_book))
        doc = _invoice(gb)
        cn = self._charge_shaped_credit_note(gb)
        assert gb.get_balance(AR) == Decimal("570.00")
        before = gb.get_outstanding_invoices(compact=False)

        with pytest.raises(ValueError, match="nothing to offset"):
            gb.apply_credit_note(cn, doc)

        # The customer owes 570; nothing may claim 140 is owed back.
        assert gb.get_outstanding_invoices(compact=False) == before
        assert gb.get_invoice(doc, owner_type="customer")["amount_due"] == "500.00"

    def test_an_ordinary_credit_note_still_applies(self, business_book):
        gb = GnuCashBook(str(business_book))
        doc = _invoice(gb)
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="1", price="120.00",
            owner_type="customer",
        )
        gb.post_invoice(
            invoice_id=cn["id"], post_account=AR, owner_type="customer",
        )
        result = gb.apply_credit_note(cn["id"], doc)
        assert result["status"] == "applied"
        assert gb.get_invoice(doc, owner_type="customer")["amount_due"] == "380.00"


class TestEarlyPaymentDiscountAfterACreditNote:
    """Review C47. The discount was measured on the full subtotal
    whatever credit notes had since been applied: a 1,000 invoice
    with 900 credited back, on "2/10" terms, refused the correct 98
    and booked a 20.00 discount on a 100 balance."""

    def _setup(self, gb, credit="900.00", percent="2"):
        from datetime import date
        today = date.today().isoformat()
        gb.create_customer(name="Acme Corp")
        gb.create_billterm(
            name="2/10 net 30", due_days=30, discount_days=10,
            discount_percent=percent,
        )
        inv = gb.create_invoice(
            customer_id="000001", term="2/10 net 30", date_opened=today,
        )
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Goods", quantity="1", price="1000.00",
        )
        gb.post_invoice(invoice_id=inv["id"], post_account=AR, post_date=today)
        if credit:
            cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
            gb.add_credit_note_entry(
                credit_note_id=cn["id"], account="Income:Sales",
                description="Returned", quantity="1", price=credit,
                owner_type="customer",
            )
            gb.post_invoice(
                invoice_id=cn["id"], post_account=AR, owner_type="customer",
                post_date=today,
            )
            gb.apply_credit_note(cn["id"], inv["id"])
        return inv["id"], today

    def test_discount_is_on_what_was_not_credited(self, business_book):
        gb = GnuCashBook(str(business_book))
        doc, today = self._setup(gb)
        got = gb.get_invoice(doc, owner_type="customer")
        assert got["amount_due"] == "100.00"
        assert got["discount_available"]["amount"] == "2.00"

        with pytest.raises(ValueError, match="adjust amount to 98.00"):
            gb.pay_invoice(
                invoice_id=doc, payment_account="Assets:Checking",
                amount="80.00", payment_date=today, apply_discount=True,
            )
        paid = gb.pay_invoice(
            invoice_id=doc, payment_account="Assets:Checking",
            amount="98.00", payment_date=today, apply_discount=True,
        )
        assert paid["status"] == "paid"
        assert paid["discount"]["amount"] == "2.00"

    def test_without_a_credit_note_nothing_changes(self, business_book):
        gb = GnuCashBook(str(business_book))
        doc, today = self._setup(gb, credit=None)
        paid = gb.pay_invoice(
            invoice_id=doc, payment_account="Assets:Checking",
            amount="980.00", payment_date=today, apply_discount=True,
        )
        assert paid["discount"]["amount"] == "20.00"

    def test_an_earlier_cash_payment_does_not_shrink_it(self, business_book):
        """The settling payment still takes the whole discount."""
        gb = GnuCashBook(str(business_book))
        doc, today = self._setup(gb, credit=None)
        gb.pay_invoice(
            invoice_id=doc, payment_account="Assets:Checking",
            amount="500.00", payment_date=today,
        )
        paid = gb.pay_invoice(
            invoice_id=doc, payment_account="Assets:Checking",
            amount="480.00", payment_date=today, apply_discount=True,
        )
        assert paid["status"] == "paid"
        assert paid["discount"]["amount"] == "20.00"

    def test_never_suggests_a_negative_payment(self, business_book):
        """A 30% term on a 1,000 invoice with 750 credited: the
        discount (75) is under the 250 owed. But cash already paid
        can leave less than the discount; the hint then said to pay
        a negative amount."""
        gb = GnuCashBook(str(business_book))
        doc, today = self._setup(gb, credit=None, percent="30")
        gb.pay_invoice(
            invoice_id=doc, payment_account="Assets:Checking",
            amount="800.00", payment_date=today,
        )
        with pytest.raises(ValueError) as refusal:
            gb.pay_invoice(
                invoice_id=doc, payment_account="Assets:Checking",
                amount="150.00", payment_date=today, apply_discount=True,
            )
        assert "adjust amount to -" not in str(refusal.value)
        assert "exceeds what is still owed" in str(refusal.value)
