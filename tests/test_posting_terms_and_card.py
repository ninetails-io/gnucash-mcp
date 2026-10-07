"""What posting freezes, and where a card line goes.

Two findings of the pre-release adversarial review
(``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``), with the rows
pinned against GnuCash's engine in ``test_parity_posting.py``:

* C10 — a posted document must point at a hidden copy of its billing
  term (``gncBillTermReturnChild``), so that editing the term changes
  what new documents get and not what old ones were posted under.
* C3 — an employee voucher's company-card lines post to the card
  account and come off what is owed to the employee.

And the side finding under C10: a book GnuCash has posted in holds
hidden tax-table copies with the same NAME as the live table, and a
name lookup must never return one.
"""

import sqlite3
import uuid
from decimal import Decimal

import pytest

from gnucash_mcp.book import GnuCashBook

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"


def _q(path, sql, params=()):
    con = sqlite3.connect(str(path))
    try:
        rows = con.execute(sql, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def _invoice(gb, term="2/10 Net 30", price="1000.00"):
    inv = gb.create_invoice(customer_id="000001", term=term)
    gb.add_invoice_entry(
        invoice_id=inv["id"], account="Income:Sales",
        description="Work", quantity="1", price=price,
    )
    return inv["id"]


@pytest.fixture
def terms(business_book):
    gb = GnuCashBook(str(business_book))
    gb.create_billterm(
        name="2/10 Net 30", due_days=30, discount_days=10,
        discount_percent="2",
    )
    gb.create_customer(name="Acme Corp")
    return gb, business_book


def _term_of(path, doc):
    return _q(
        path,
        "select b.invisible, b.parent is not null, b.refcount, b.duedays, "
        "b.discount_num, b.guid from invoices i join billterms b "
        "on b.guid = i.terms where i.id = ?", (doc,),
    )[0]


class TestPostingFreezesTheTerms:
    def test_a_posted_document_points_at_a_hidden_copy(self, terms):
        gb, path = terms
        doc = _invoice(gb)
        assert _term_of(path, doc)[:2] == (0, 0)  # the live term, as a draft

        gb.post_invoice(invoice_id=doc, post_account=AR, post_date="2026-01-15")

        invisible, is_child, refcount, duedays, discount, _ = _term_of(path, doc)
        assert (invisible, is_child, refcount) == (1, 1, 0)
        assert (duedays, discount) == (30, 2)
        # One name in the list, and it is the live one.
        assert gb.list_billterms().count("2/10 Net 30") == 1
        assert _q(
            path, "select refcount from billterms where parent is null",
        ) == [(0,)]

    def test_editing_the_term_leaves_posted_documents_alone(self, terms):
        gb, path = terms
        doc = _invoice(gb)
        gb.post_invoice(invoice_id=doc, post_account=AR, post_date="2026-01-15")
        before = gb.get_invoice(doc, owner_type="customer")

        # The term is renegotiated: 5% within 20 days.
        _q(
            path,
            "update billterms set discount_num = 5, discountdays = 20 "
            "where parent is null",
        )

        after = gb.get_invoice(doc, owner_type="customer")
        assert after == before
        # A new document gets the new terms, and its own copy at post.
        new = _invoice(gb)
        gb.post_invoice(invoice_id=new, post_account=AR, post_date="2026-01-20")
        assert _term_of(path, new)[4] == 5
        assert _term_of(path, doc)[4] == 2
        assert _term_of(path, new)[5] != _term_of(path, doc)[5]

    def test_documents_posted_under_the_same_terms_share_one_copy(self, terms):
        gb, path = terms
        first, second = _invoice(gb), _invoice(gb)
        gb.post_invoice(invoice_id=first, post_account=AR, post_date="2026-01-15")
        gb.post_invoice(invoice_id=second, post_account=AR, post_date="2026-01-16")
        assert _term_of(path, first)[5] == _term_of(path, second)[5]
        assert _q(path, "select count(*) from billterms") == [(2,)]

    def test_unpost_and_repost_keep_the_copy(self, terms):
        """gncInvoiceUnpost leaves the terms where posting put them,
        and a copy returns itself when asked for its child."""
        gb, path = terms
        doc = _invoice(gb)
        gb.post_invoice(invoice_id=doc, post_account=AR, post_date="2026-01-15")
        copy = _term_of(path, doc)[5]
        gb.unpost_invoice(invoice_id=doc, owner_type="customer")
        assert _term_of(path, doc)[5] == copy
        gb.post_invoice(invoice_id=doc, post_account=AR, post_date="2026-01-18")
        assert _term_of(path, doc)[5] == copy
        assert _q(path, "select count(*) from billterms") == [(2,)]
        # The due date still comes from the terms.
        assert gb.get_outstanding_invoices(
            compact=False,
        )["invoices"][0]["due_date"] == "2026-02-17"

    def test_the_early_payment_discount_reads_the_copy(self, terms):
        gb, _ = terms
        doc = _invoice(gb)
        gb.post_invoice(invoice_id=doc, post_account=AR)
        paid = gb.pay_invoice(
            invoice_id=doc, payment_account="Assets:Checking",
            amount="980.00", apply_discount=True,
        )
        assert paid["status"] == "paid"
        assert paid["discount"]["amount"] == "20.00"


class TestHiddenTaxTableCopies:
    @pytest.fixture
    def with_copy(self, business_book):
        """A live table at 10% and the hidden 5% copy desktop left
        when it posted under the old rate."""
        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="GST Payable", account_type="LIABILITY", parent="Liabilities",
        )
        gb.create_taxtable(name="GST", entries=[{
            "type": "percentage", "amount": "10",
            "account": "Liabilities:GST Payable",
        }])
        parent, account = _q(
            business_book,
            "select t.guid, e.account from taxtables t "
            "join taxtable_entries e on e.taxtable = t.guid",
        )[0]
        child = uuid.uuid4().hex
        # GUIDs sort before the parent's, so an unfiltered
        # first-by-name lookup would find the copy.
        _q(
            business_book,
            "insert into taxtables (guid, name, refcount, invisible, parent) "
            "values (?, 'GST', 0, 1, ?)", (child, parent),
        )
        _q(
            business_book,
            "insert into taxtable_entries (taxtable, account, amount_num, "
            "amount_denom, type) values (?, ?, 5, 1, 2)", (child, account),
        )
        return gb, business_book, parent, child

    def test_the_list_shows_one(self, with_copy):
        gb, *_ = with_copy
        listing = gb.list_taxtables(compact=False)
        assert listing["total"] == 1
        assert [t["name"] for t in listing["taxtables"]] == ["GST"]

    def test_a_name_resolves_to_the_live_table(self, with_copy):
        gb, path, parent, child = with_copy
        assert gb.get_taxtable("GST")["entries"][0]["amount"] in ("10", "10.00")
        gb.create_customer(name="Acme Corp")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00", taxtable="GST",
        )
        assert _q(path, "select i_taxtable from entries") == [(parent,)]
        assert gb.get_invoice(inv["id"], owner_type="customer")["total"] == "110.00"

    def test_a_duplicate_name_is_still_refused(self, with_copy):
        gb, *_ = with_copy
        with pytest.raises(ValueError):
            gb.create_taxtable(name="GST", entries=[{
                "type": "percentage", "amount": "7",
                "account": "Liabilities:GST Payable",
            }])


class TestVoucherCardLines:
    @pytest.fixture
    def voucher(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="Company Card", account_type="CREDIT", parent="Liabilities",
        )
        gb.create_employee(name="Dana Reimbursee")
        v = gb.create_voucher(employee_id="000001")
        for description, price in (("Taxi", "20.00"), ("Hotel", "300.00")):
            gb.add_voucher_entry(
                voucher_id=v["id"], account="Expenses:Services",
                description=description, quantity="1", price=price,
            )
        return gb, business_book, v["id"]

    @staticmethod
    def _as_desktop_drafts_it(path, card_account=True):
        """The Hotel line paid with the company card — fields only
        desktop's voucher window writes."""
        if card_account:
            _q(
                path,
                "update employees set ccard_guid = "
                "(select guid from accounts where name = 'Company Card')",
            )
        _q(path, "update entries set b_paytype = 2 where description = 'Hotel'")

    @staticmethod
    def _balance(path, account):
        return Decimal(_q(
            path,
            "select coalesce(sum(s.value_num * 1.0 / s.value_denom), 0) "
            "from splits s join accounts a on a.guid = s.account_guid "
            "where a.name = ?", (account,),
        )[0][0]).quantize(Decimal("0.01"))

    def test_card_lines_post_to_the_employees_card(self, voucher):
        gb, path, doc = voucher
        self._as_desktop_drafts_it(path)

        posted = gb.post_invoice(
            invoice_id=doc, post_account=AP, owner_type="employee",
        )

        assert posted["total"] == "20.00"
        assert posted["charged_to_card"] == {
            "account": "Liabilities:Company Card", "amount": "300.00",
            "document_total": "320.00",
        }
        assert self._balance(path, "Company Card") == Decimal("-300.00")
        assert self._balance(path, "Accounts Payable") == Decimal("-20.00")
        assert self._balance(path, "Services") == Decimal("320.00")
        assert _q(
            path,
            "select s.memo, s.action from splits s join accounts a "
            "on a.guid = s.account_guid where a.name = 'Company Card'",
        ) == [("Hotel", "Expense")]

    def test_the_employee_is_owed_and_paid_only_the_cash_part(self, voucher):
        gb, path, doc = voucher
        self._as_desktop_drafts_it(path)
        gb.post_invoice(invoice_id=doc, post_account=AP, owner_type="employee")

        got = gb.get_invoice(doc, owner_type="employee")
        assert (got["total"], got["amount_due"]) == ("20.00", "20.00")
        assert got["charged_to_card"]["amount"] == "300.00"
        # 20 posted against 320 of entries is the card, not drift.
        assert "total_note" not in got
        with pytest.raises(ValueError, match="exceeds the outstanding"):
            gb.pay_invoice(
                invoice_id=doc, payment_account="Assets:Checking",
                amount="320.00", owner_type="employee",
            )
        paid = gb.pay_invoice(
            invoice_id=doc, payment_account="Assets:Checking",
            amount="20.00", owner_type="employee",
        )
        assert paid["status"] == "paid"

    def test_the_extra_to_charge_goes_to_the_card_too(self, voucher):
        gb, path, doc = voucher
        self._as_desktop_drafts_it(path)
        _q(path, "update invoices set charge_amt_num = 500, charge_amt_denom = 100")
        posted = gb.post_invoice(
            invoice_id=doc, post_account=AP, owner_type="employee",
        )
        assert posted["total"] == "15.00"
        assert posted["charged_to_card"]["amount"] == "305.00"
        assert sorted(_q(
            path,
            "select s.memo, s.value_num from splits s join accounts a "
            "on a.guid = s.account_guid where a.name = 'Company Card'",
        )) == [("Extra to Charge Card", -500), ("Hotel", -30000)]

    def test_without_a_card_account_everything_is_owed(self, voucher):
        """Desktop separates card lines only when the employee has a
        card account to send them to."""
        gb, path, doc = voucher
        self._as_desktop_drafts_it(path, card_account=False)
        posted = gb.post_invoice(
            invoice_id=doc, post_account=AP, owner_type="employee",
        )
        assert posted["total"] == "320.00"
        assert "charged_to_card" not in posted

    def test_a_voucher_with_no_card_lines_is_unchanged(self, voucher):
        gb, path, doc = voucher
        _q(
            path,
            "update employees set ccard_guid = "
            "(select guid from accounts where name = 'Company Card')",
        )
        posted = gb.post_invoice(
            invoice_id=doc, post_account=AP, owner_type="employee",
        )
        assert posted["total"] == "320.00"
        assert "charged_to_card" not in posted
        assert "charged_to_card" not in gb.get_invoice(doc, owner_type="employee")
