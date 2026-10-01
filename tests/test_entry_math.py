"""The parity oracle for document arithmetic.

``gnucash_mcp.book._entry_math`` is a port of three GnuCash functions
(5.12, ``libgnucash/engine``): ``gncEntryComputeValueInt``,
``gncEntryRecomputeValues``'s rounding, and
``gncInvoiceGetNetAndTaxesInternal``. Every expectation below is the
figure those functions produce, worked from the source and stated in
the comment beside it — the numbers GnuCash desktop shows in its
invoice window and posts.

The first table is the pre-release adversarial review's
(``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``, C1 / C2): the
cases where the server's old math (per-line tax, half-to-even, on a
rounded pretax; discounts never read) posted a different total.

The second half drives the same cases through a real book, since the
port is only as good as its one caller,
``_get_invoice_entries_and_total``.
"""

import sqlite3
from decimal import Decimal
from fractions import Fraction as F

import pytest

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.book import _entry_math as em

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"


def pct(rate, account="tax"):
    return em.TaxEntry(em.AMT_PERCENT, F(rate), account)


def flat(amount, account="tax"):
    return em.TaxEntry(em.AMT_VALUE, F(amount), account)


def line(qty, price, taxes=None, included=False, discount=0,
         dtype=em.AMT_VALUE, dhow=em.DISC_PRETAX, account="income"):
    return account, em.entry_values(
        F(qty), F(price), taxes, included, F(discount), dtype, dhow,
    )


def doc(*lines, fraction=100, cn=False):
    return em.document_totals(list(lines), fraction, cn)


D = Decimal


# ── round_half_up: gnc_numeric_convert(…, ROUND_HALF_UP) ──────────────

class TestRoundHalfUp:
    @pytest.mark.parametrize("x,fraction,expected", [
        ("0.125", 100, "0.13"),     # the tie goes up…
        ("-0.125", 100, "-0.13"),   # …and AWAY from zero when negative
        ("0.135", 100, "0.14"),     # half-even would give 0.14 too
        ("0.145", 100, "0.15"),     # half-even gives 0.14
        ("0.1249", 100, "0.12"),
        ("2.5", 1, "3"),            # JPY
        ("-2.5", 1, "-3"),
        ("10.1255", 1000, "10.126"),  # BHD
        ("1.2", 100, "1.20"),       # at the quantum, not "1.2"
        ("0", 100, "0.00"),
    ])
    def test_ties_go_away_from_zero(self, x, fraction, expected):
        got = em.round_half_up(F(x), fraction)
        assert got == D(expected)
        assert str(got) == expected

    def test_a_third(self):
        assert em.round_half_up(F(1, 3), 100) == D("0.33")
        assert em.round_half_up(F(2, 3), 100) == D("0.67")

    def test_non_decimal_fraction(self):
        # A commodity counted in eighths.
        assert em.round_half_up(F("0.3"), 8) == D("0.25")
        assert em.round_half_up(F("0.32"), 8) == D("0.375")


# ── The review's table (C1) ───────────────────────────────────────────

class TestReviewTable:
    def test_one_line_10_at_8_25_percent(self):
        # tax = 10.00 × 0.0825 = 0.825 → 0.83 (half-up).
        # Old server: 0.82 (half-even) → 10.82.
        t = doc(line(1, "10.00", [pct("8.25")]))
        assert (t.net, t.tax, t.total) == (D("10.00"), D("0.83"), D("10.83"))

    def test_three_lines_of_ten_cents_at_5_percent(self):
        # Each line's tax is 0.005, UNROUNDED; the account total is
        # 0.015 → 0.02. Old server rounded each 0.005 to 0.00.
        t = doc(*[line(1, "0.10", [pct(5)]) for _ in range(3)])
        assert (t.net, t.tax, t.total) == (D("0.30"), D("0.02"), D("0.32"))

    def test_two_lines_10_10_at_5_percent(self):
        # 0.505 + 0.505 = 1.01 exactly. Old server: 0.50 + 0.50.
        t = doc(*[line(1, "10.10", [pct(5)]) for _ in range(2)])
        assert (t.tax, t.total) == (D("1.01"), D("21.21"))

    def test_untaxed_half_cent_line(self):
        # 1 × 0.125 → 0.13. Old server: 0.12.
        assert doc(line(1, "0.125")).total == D("0.13")

    def test_untaxed_three_at_0_335(self):
        # 1.005 → 1.01. Old server: 1.00.
        assert doc(line(3, "0.335")).total == D("1.01")

    def test_three_tax_included_lines_at_7_percent(self):
        # Each: pretax = 10/1.07 = 9.345794… → net 9.35 (×3 = 28.05);
        # tax 0.654205… each, 1.962616… for the account → 1.96.
        # Total 30.01: a cent over three 10.00 prices, as desktop
        # shows it. Old server forced 30.00.
        t = doc(*[line(1, "10.00", [pct(7)], included=True) for _ in range(3)])
        assert (t.net, t.tax, t.total) == (D("28.05"), D("1.96"), D("30.01"))

    def test_tax_included_two_authorities(self):
        # 10.00 including GST 5% + PST 5%: pretax 9.0909…,
        # each tax 0.4545… → 0.45. Total 9.99. Old server pushed
        # the missing cent onto one authority (0.46).
        t = doc(line(1, "10.00", [pct(5, "gst"), pct(5, "pst")], included=True))
        assert t.net == D("9.09")
        assert t.tax_by_account == {"gst": D("0.45"), "pst": D("0.45")}
        assert t.total == D("9.99")

    def test_tax_is_on_the_exact_line_value_not_the_rounded_one(self):
        # 3 × 0.333 = 0.999 → net 1.00; tax = 0.999 × 10% = 0.0999
        # → 0.10. (On the rounded 1.00 it is also 0.10; the pair
        # below separates them.)
        t = doc(line(3, "0.333", [pct(10)]))
        assert (t.net, t.tax) == (D("1.00"), D("0.10"))
        # 1 × 0.245 at 100%: net 0.25 (0.245 half-up), tax 0.245
        # → 0.25. Taxing the ROUNDED 0.25 would also give 0.25, but
        # 1 × 0.244 → net 0.24, tax 0.244 → 0.24; and 1 × 0.2449 at
        # 102%: net 0.24, tax 0.249798 → 0.25, where taxing the
        # rounded net gives 0.2448 → 0.24.
        t = doc(line(1, "0.2449", [pct(102)]))
        assert (t.net, t.tax) == (D("0.24"), D("0.25"))


# ── gncEntryComputeValueInt, quadrant by quadrant ─────────────────────

class TestEntryValues:
    def test_not_taxable_ignores_the_table(self):
        # Desktop passes ``i_taxable ? table : NULL``.
        _, v = line(2, 100, None)
        assert v.value == 200 and v.taxes == ()

    def test_tax_included_without_a_table_is_plain(self):
        # ``if (tax_table && tax_included)``.
        _, v = line(2, 100, None, included=True)
        assert v.value == 200

    def test_exclusive_percent_and_flat(self):
        _, v = line(1, 100, [pct(5, "g"), flat(2, "eco")])
        assert v.value == 100
        assert dict(v.taxes) == {"g": 5, "eco": 2}

    def test_included_percent_and_flat(self):
        # pretax = (aggregate − tvalue) / (1 + tpercent)
        #        = (107 − 2) / 1.05 = 100.
        _, v = line(1, 107, [pct(5, "g"), flat(2, "eco")], included=True)
        assert v.value == 100
        assert dict(v.taxes) == {"g": 5, "eco": 2}

    def test_same_account_rows_collapse(self):
        # gncAccountValueAdd.
        _, v = line(1, 100, [pct(5, "t"), pct(7, "t")])
        assert v.taxes == (("t", F(12)),)

    def test_values_are_exact_not_rounded(self):
        _, v = line(1, 10, [pct(7)], included=True)
        assert v.value == F(1000, 107)
        assert v.tax_total == F(70, 107)

    # Type:    discount    tax
    # PRETAX   pretax      pretax-discount
    # SAMETIME pretax      pretax
    # POSTTAX  pretax+tax  pretax

    def test_discount_percent_pretax(self):
        _, v = line(1, 100, [pct(10)], discount=10,
                    dtype=em.AMT_PERCENT, dhow=em.DISC_PRETAX)
        assert (v.value, v.discount, v.tax_total) == (90, 10, 9)

    def test_discount_percent_sametime(self):
        # Discount off pretax, tax on the UNdiscounted pretax.
        _, v = line(1, 100, [pct(10)], discount=10,
                    dtype=em.AMT_PERCENT, dhow=em.DISC_SAMETIME)
        assert (v.value, v.discount, v.tax_total) == (90, 10, 10)

    def test_discount_percent_posttax(self):
        # Discount off pretax + tax = 110 → 11; tax on pretax.
        _, v = line(1, 100, [pct(10)], discount=10,
                    dtype=em.AMT_PERCENT, dhow=em.DISC_POSTTAX)
        assert (v.value, v.discount, v.tax_total) == (89, 11, 10)

    def test_discount_posttax_counts_flat_taxes(self):
        # after_tax = pretax + pretax×tpercent + tvalue = 100+10+5.
        _, v = line(1, 100, [pct(10, "g"), flat(5, "eco")], discount=10,
                    dtype=em.AMT_PERCENT, dhow=em.DISC_POSTTAX)
        assert v.discount == F("11.5")
        assert v.value == F("88.5")

    @pytest.mark.parametrize("how,tax", [
        (em.DISC_PRETAX, F("8.5")),     # on 100 − 15
        (em.DISC_SAMETIME, 10),
        (em.DISC_POSTTAX, 10),
    ])
    def test_discount_value(self, how, tax):
        # A VALUE discount is the amount as given, whatever the how.
        _, v = line(1, 100, [pct(10)], discount=15,
                    dtype=em.AMT_VALUE, dhow=how)
        assert (v.value, v.discount, v.tax_total) == (85, 15, tax)

    def test_discount_on_a_tax_included_line(self):
        # pretax = 110/1.1 = 100; 10% PRETAX → 90, tax 9.
        _, v = line(1, 110, [pct(10)], included=True, discount=10,
                    dtype=em.AMT_PERCENT, dhow=em.DISC_PRETAX)
        assert (v.value, v.tax_total) == (90, 9)

    def test_unknown_discount_how_is_an_error(self):
        with pytest.raises(ValueError, match="discount-how"):
            line(1, 100, None, discount=1, dhow="LATER")


# ── gncInvoiceGetNetAndTaxesInternal ──────────────────────────────────

class TestDocumentTotals:
    def test_net_rounds_per_line_tax_rounds_per_account(self):
        # Two lines of 0.105 on one income account, 5% tax:
        # net 0.11 + 0.11 = 0.22 (each line rounded, bug 628903);
        # tax 0.00525 × 2 = 0.0105 → 0.01 (rounded once).
        t = doc(line(1, "0.105", [pct(5)]), line(1, "0.105", [pct(5)]))
        assert t.net_by_account == {"income": D("0.22")}
        assert t.tax_by_account == {"tax": D("0.01")}
        assert t.total == D("0.23")

    def test_total_is_what_the_splits_sum_to(self):
        t = doc(
            line(3, "19.99", [pct("8.25", "state"), pct("1.5", "city")]),
            line(1, "0.335", [pct("8.25", "state")], account="other"),
            line(2, "4.445", None),
        )
        assert sum(t.by_account.values()) == t.total
        assert t.total == t.net + t.tax

    def test_tax_account_shared_with_income_merges(self):
        # gncAccountValueAddList (splitinfo, taxes).
        t = doc(line(1, 100, [pct(5, "income")]))
        assert t.by_account == {"income": D("105.00")}

    def test_credit_note_reads_positive(self):
        # Stored quantity −1 (gncEntrySetDocQuantity); the document
        # view negates (gncEntryGetDocValue / …DocTaxValues).
        t = doc(line(-1, 100, [pct(5)]), cn=True)
        assert (t.net, t.tax, t.total) == (D("100.00"), D("5.00"), D("105.00"))

    def test_credit_note_tie_rounds_like_an_invoice(self):
        # Half-up is symmetric: −0.125 → −0.13, negated → 0.13.
        assert doc(line(-1, "0.125"), cn=True).total == D("0.13")
        assert doc(line(1, "0.125")).total == D("0.13")

    def test_flat_tax_on_a_credit_note_follows_desktop(self):
        # gncEntryComputeValueInt adds a VALUE tax as given whatever
        # the quantity's sign, and the document view negates it: a
        # credit note's flat tax comes out NEGATIVE. Odd, and exactly
        # what desktop computes — parity is the contract.
        t = doc(line(-1, 100, [flat(2)]), cn=True)
        assert (t.net, t.tax, t.total) == (D("100.00"), D("-2.00"), D("98.00"))

    def test_zero_decimal_currency(self):
        t = doc(line(1, 1000, [pct(10)]), fraction=1)
        assert (t.net, t.tax, t.total) == (D("1000"), D("100"), D("1100"))
        # 2.5 → 3 (half-up), not 2.
        assert doc(line(1, "2.5"), fraction=1).total == D("3")

    def test_three_decimal_currency(self):
        t = doc(line(1, "10.1255", [pct(5)]), fraction=1000)
        assert t.net == D("10.126")
        assert t.tax == D("0.506")      # 0.506275

    def test_empty_document(self):
        t = doc()
        assert (t.net, t.tax, t.total) == (D("0.00"), D("0.00"), D("0.00"))


# ── Through a book: the port's one caller ─────────────────────────────

def _q(book_path, sql, params=()):
    con = sqlite3.connect(str(book_path))
    try:
        rows = con.execute(sql, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def _split_values(book_path, txn_prefix):
    return {
        name: Decimal(num) / Decimal(denom)
        for name, num, denom in _q(
            book_path,
            "SELECT a.name, s.value_num, s.value_denom FROM splits s "
            "JOIN accounts a ON a.guid = s.account_guid "
            "WHERE s.tx_guid LIKE ?",
            (txn_prefix + "%",),
        )
    }


@pytest.fixture
def gb(business_book):
    book = GnuCashBook(str(business_book))
    book.create_customer(name="Acme Corp")
    for name in ("GST Payable", "PST Payable"):
        book.create_account(
            name=name, account_type="LIABILITY", parent="Liabilities",
        )
    book.path_for_sql = business_book
    return book


def _table(gb, name, *entries):
    gb.create_taxtable(name=name, entries=[
        {"type": "percentage", "amount": rate,
         "account": f"Liabilities:{account} Payable"}
        for rate, account in entries
    ])


def _draft(gb, lines, taxtable=None, tax_included=False):
    inv = gb.create_invoice(customer_id="000001")
    for qty, price in lines:
        kwargs = {}
        if taxtable:
            kwargs = {"taxtable": taxtable, "tax_included": tax_included}
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Line", quantity=qty, price=price, **kwargs,
        )
    return inv["id"]


class TestPostingBooksDesktopsTotals:
    def test_single_line_8_25_percent(self, gb):
        _table(gb, "T", ("8.25", "GST"))
        doc_id = _draft(gb, [("1", "10.00")], "T")
        posted = gb.post_invoice(invoice_id=doc_id, post_account=AR)
        assert posted["total"] == "10.83"
        assert _split_values(gb.path_for_sql, posted["transaction_guid"]) == {
            "Accounts Receivable": D("10.83"),
            "Sales": D("-10.00"),
            "GST Payable": D("-0.83"),
        }

    def test_three_small_lines_tax_accumulates(self, gb):
        _table(gb, "T", ("5", "GST"))
        doc_id = _draft(gb, [("1", "0.10")] * 3, "T")
        posted = gb.post_invoice(invoice_id=doc_id, post_account=AR)
        assert posted["total"] == "0.32"
        splits = _split_values(gb.path_for_sql, posted["transaction_guid"])
        assert splits["GST Payable"] == D("-0.02")
        assert sum(splits.values()) == 0

    def test_tax_included_lines(self, gb):
        _table(gb, "T", ("7", "GST"))
        doc_id = _draft(gb, [("1", "10.00")] * 3, "T", tax_included=True)
        posted = gb.post_invoice(invoice_id=doc_id, post_account=AR)
        assert posted["total"] == "30.01"
        splits = _split_values(gb.path_for_sql, posted["transaction_guid"])
        assert splits == {
            "Accounts Receivable": D("30.01"),
            "Sales": D("-28.05"),
            "GST Payable": D("-1.96"),
        }

    def test_untaxed_half_cent(self, gb):
        doc_id = _draft(gb, [("1", "0.125")])
        assert gb.post_invoice(
            invoice_id=doc_id, post_account=AR,
        )["total"] == "0.13"

    def test_draft_and_posting_agree(self, gb):
        """get_document on the draft shows the number posting books."""
        _table(gb, "T", ("5", "GST"), ("7", "PST"))
        doc_id = _draft(gb, [("1", "10.13")], "T", tax_included=True)
        draft = gb.get_invoice(doc_id, owner_type="customer")
        posted = gb.post_invoice(invoice_id=doc_id, post_account=AR)
        assert draft["total"] == posted["total"] == "10.12"
        assert draft["tax_summary"]["by_account"] == {
            "Liabilities:GST Payable": "0.45",
            "Liabilities:PST Payable": "0.63",
        }
        after = gb.get_invoice(doc_id, owner_type="customer")
        assert after["total"] == "10.12"
        assert "total_note" not in after

    def test_bill_side(self, gb):
        gb.create_vendor(name="Supplier")
        gb.create_account(
            name="Input Tax", account_type="ASSET", parent="Assets",
        )
        gb.create_taxtable(name="IN", entries=[{
            "type": "percentage", "amount": "8.25",
            "account": "Assets:Input Tax",
        }])
        bill = gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id=bill["id"], account="Expenses:Services",
            description="x", quantity="1", price="10.00", taxtable="IN",
        )
        posted = gb.post_invoice(
            invoice_id=bill["id"], post_account=AP, owner_type="vendor",
        )
        assert posted["total"] == "10.83"
        assert _split_values(gb.path_for_sql, posted["transaction_guid"]) == {
            "Accounts Payable": D("-10.83"),
            "Services": D("10.00"),
            "Input Tax": D("0.83"),
        }

    def test_credit_note(self, gb):
        _table(gb, "T", ("8.25", "GST"))
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="1", price="10.00", taxtable="T",
        )
        posted = gb.post_invoice(
            invoice_id=cn["id"], post_account=AR, owner_type="customer",
        )
        assert posted["total"] == "10.83"
        assert _split_values(gb.path_for_sql, posted["transaction_guid"]) == {
            "Accounts Receivable": D("-10.83"),
            "Sales": D("10.00"),
            "GST Payable": D("0.83"),
        }


class TestLineDiscounts:
    """Only desktop writes a line discount; the server must read it
    (review C2). The rows are set the way gnc-entry-sql.cpp stores
    them: ``i_discount`` a rational, ``i_disc_type`` and
    ``i_disc_how`` the enum strings."""

    def _discount(self, gb, doc_id, amount, dtype="PERCENT", dhow="PRETAX"):
        guid = _q(
            gb.path_for_sql, "SELECT guid FROM invoices WHERE id = ?",
            (doc_id,),
        )[0][0]
        _q(
            gb.path_for_sql,
            "UPDATE entries SET i_discount_num = ?, i_discount_denom = 1, "
            "i_disc_type = ?, i_disc_how = ? WHERE invoice = ?",
            (amount, dtype, dhow, guid),
        )

    def test_desktop_drafted_discount_posts_discounted(self, gb):
        doc_id = _draft(gb, [("1", "100.00")])
        self._discount(gb, doc_id, 10)
        draft = gb.get_invoice(doc_id, owner_type="customer")
        assert draft["total"] == "90.00"
        assert draft["entries"][0]["discount"] == "10"
        assert draft["entries"][0]["discount_type"] == "percent"
        assert draft["entries"][0]["discount_how"] == "pretax"

        posted = gb.post_invoice(invoice_id=doc_id, post_account=AR)

        assert posted["total"] == "90.00"
        assert _split_values(gb.path_for_sql, posted["transaction_guid"]) == {
            "Accounts Receivable": D("90.00"),
            "Sales": D("-90.00"),
        }

    @pytest.mark.parametrize("dhow,total,tax", [
        ("PRETAX", "99.00", "9.00"),     # 90 + 10% of 90
        ("SAMETIME", "100.00", "10.00"),  # 90 + 10% of 100
        ("POSTTAX", "99.00", "10.00"),   # 100 − 11, + 10
    ])
    def test_discount_how_with_tax(self, gb, dhow, total, tax):
        _table(gb, "T", ("10", "GST"))
        doc_id = _draft(gb, [("1", "100.00")], "T")
        self._discount(gb, doc_id, 10, "PERCENT", dhow)
        posted = gb.post_invoice(invoice_id=doc_id, post_account=AR)
        assert posted["total"] == total
        splits = _split_values(gb.path_for_sql, posted["transaction_guid"])
        assert splits["GST Payable"] == -D(tax)

    def test_value_discount(self, gb):
        doc_id = _draft(gb, [("2", "50.00")])
        self._discount(gb, doc_id, 15, "VALUE", "PRETAX")
        assert gb.post_invoice(
            invoice_id=doc_id, post_account=AR,
        )["total"] == "85.00"

    def test_rows_from_before_1_5_carry_no_discount(self, gb):
        """Server 1.2-1.4 wrote '' in both enum columns and a zero
        discount; they must total as plain lines."""
        doc_id = _draft(gb, [("1", "100.00")])
        self._discount(gb, doc_id, 0, "", "")
        got = gb.get_invoice(doc_id, owner_type="customer")
        assert got["total"] == "100.00"
        assert "discount" not in got["entries"][0]

    def test_bill_side_never_discounts(self, gb):
        """gncEntryRecomputeValues computes the bill side with
        gnc_numeric_zero(): an entry's ``i_discount`` belongs to the
        invoice it may also sit on."""
        gb.create_vendor(name="Supplier")
        bill = gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id=bill["id"], account="Expenses:Services",
            description="x", quantity="1", price="100.00",
        )
        guid = _q(
            gb.path_for_sql, "SELECT guid FROM invoices WHERE id = ? "
            "AND owner_type = 4", (bill["id"],),
        )[0][0]
        _q(
            gb.path_for_sql,
            "UPDATE entries SET i_discount_num = 10, i_discount_denom = 1, "
            "i_disc_type = 'PERCENT', i_disc_how = 'PRETAX' WHERE bill = ?",
            (guid,),
        )
        assert gb.post_invoice(
            invoice_id=bill["id"], post_account=AP, owner_type="vendor",
        )["total"] == "100.00"
