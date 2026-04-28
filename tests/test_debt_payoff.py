"""Tests for debt_payoff_plan method."""

import pytest
from datetime import date
from decimal import Decimal
from pathlib import Path

from gnucash_mcp.book import GnuCashBook


class TestDebtPayoffPlan:
    """Tests for debt_payoff_plan method."""

    def test_basic_avalanche_two_debts(self, debt_book: Path):
        """Should pay highest APR debt first (Visa 23.49% before Mastercard 18.99%)."""
        gc_book = GnuCashBook(str(debt_book))

        result = gc_book.debt_payoff_plan(compact=False, monthly_budget="1000")

        # Visa (23.49%) should be first in payoff order
        assert result["payoff_order"][0] == "Liabilities:Visa"
        assert result["payoff_order"][1] == "Liabilities:Mastercard"
        # Car Loan (6.5%) should be last
        assert result["payoff_order"][2] == "Liabilities:Car Loan"

        # Verify structure
        assert len(result["debts"]) == 3
        assert "total_balance" in result
        assert "total_interest" in result
        assert "total_paid" in result
        assert "payoff_months" in result
        assert "payoff_date" in result
        assert "monthly_budget" in result
        assert result["monthly_budget"] == "1000"

        # Visa should pay off before Mastercard
        visa_month = None
        mc_month = None
        for d in result["debts"]:
            if d["account"] == "Liabilities:Visa":
                visa_month = d["payoff_month"]
            elif d["account"] == "Liabilities:Mastercard":
                mc_month = d["payoff_month"]
        assert visa_month < mc_month

    def test_yeti_multiplier(self, debt_book: Path):
        """Should calculate YETI > 1.0 (debt makes purchases cost more)."""
        gc_book = GnuCashBook(str(debt_book))

        result = gc_book.debt_payoff_plan(compact=False, monthly_budget="1000")

        yeti = result["yeti"]
        assert "multiplier" in yeti
        assert "purchase_amount" in yeti
        assert "true_cost" in yeti
        assert "explanation" in yeti

        # YETI multiplier must be > 1.0 when carrying debt
        multiplier = Decimal(yeti["multiplier"])
        assert multiplier > Decimal("1.0")

        # Default purchase amount should be $1.00
        assert yeti["purchase_amount"] == "1.00"

        # True cost should equal purchase_amount * multiplier (approximately)
        true_cost = Decimal(yeti["true_cost"])
        assert true_cost > Decimal("1.00")

        # Explanation should be human-readable
        assert "$1.00 purchase" in yeti["explanation"]
        assert "debt is paid off" in yeti["explanation"]

    def test_yeti_custom_purchase_amount(self, debt_book: Path):
        """Should calculate YETI for a custom purchase amount."""
        gc_book = GnuCashBook(str(debt_book))

        result = gc_book.debt_payoff_plan(
            compact=False,
            monthly_budget="1000",
            additional_purchase="100",
        )

        yeti = result["yeti"]
        assert yeti["purchase_amount"] == "100"
        true_cost = Decimal(yeti["true_cost"])
        assert true_cost > Decimal("100")

    def test_no_debt_accounts(self, test_book: Path):
        """Should raise ValueError when no accounts have APR slot set."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="No debt accounts found"):
            gc_book.debt_payoff_plan(compact=False, monthly_budget="500")

    def test_budget_less_than_minimums(self, debt_book: Path):
        """Should raise ValueError when budget can't cover minimum payments."""
        gc_book = GnuCashBook(str(debt_book))

        with pytest.raises(ValueError, match="less than the sum of minimum"):
            gc_book.debt_payoff_plan(compact=False, monthly_budget="100")

    def test_minimum_payment_from_slot(self, debt_book: Path):
        """Should use minimum_payment slot when present."""
        gc_book = GnuCashBook(str(debt_book))

        result = gc_book.debt_payoff_plan(compact=False, monthly_budget="1000")

        # Car Loan has minimum_payment slot set to 350
        for d in result["debts"]:
            if d["account"] == "Liabilities:Car Loan":
                assert d["minimum_payment"] == "350"
                break
        else:
            pytest.fail("Car Loan not found in results")

    def test_minimum_payment_from_balance(self, debt_book: Path):
        """Should calculate 2% of balance when no minimum_payment slot."""
        gc_book = GnuCashBook(str(debt_book))

        result = gc_book.debt_payoff_plan(compact=False, monthly_budget="1000")

        # Visa: $5000 charges - $200 payment = $4800 balance, 2% = $96
        for d in result["debts"]:
            if d["account"] == "Liabilities:Visa":
                assert d["minimum_payment"] == "96.00"
                break
        else:
            pytest.fail("Visa not found in results")

    def test_minimum_payment_fallback_2_percent(self, debt_book: Path):
        """Should fall back to 2% of balance when no slot and no payments."""
        gc_book = GnuCashBook(str(debt_book))

        result = gc_book.debt_payoff_plan(compact=False, monthly_budget="1000")

        # Mastercard has no minimum_payment slot and no payment transactions
        # Balance is $3000, 2% = $60
        for d in result["debts"]:
            if d["account"] == "Liabilities:Mastercard":
                assert d["minimum_payment"] == "60.00"
                break
        else:
            pytest.fail("Mastercard not found in results")

    def test_single_debt(self, test_book: Path):
        """Should work with a single debt account."""
        gc_book = GnuCashBook(str(test_book))

        # Create a credit card account with APR
        gc_book.create_account(
            name="Credit Card",
            account_type="CREDIT",
            parent="Liabilities",
        )
        gc_book.set_account_slot("Liabilities:Credit Card", "apr", "20.00")

        # Add a charge to create a balance
        gc_book.create_transaction(
            description="Charge",
            splits=[
                {"account": "Liabilities:Credit Card", "amount": "-1000"},
                {"account": "Expenses:Groceries", "amount": "1000"},
            ],
            trans_date=date(2026, 1, 1),
        )

        result = gc_book.debt_payoff_plan(compact=False, monthly_budget="200")

        assert len(result["debts"]) == 1
        assert result["debts"][0]["account"] == "Liabilities:Credit Card"
        assert result["debts"][0]["balance"] == "1000.00"
        assert result["payoff_months"] > 0
        assert Decimal(result["total_interest"]) > 0

    def test_zero_balance_excluded(self, test_book: Path):
        """Should skip debt accounts with zero balance."""
        gc_book = GnuCashBook(str(test_book))

        # Create a credit card with APR but no transactions (zero balance)
        gc_book.create_account(
            name="Empty Card",
            account_type="CREDIT",
            parent="Liabilities",
        )
        gc_book.set_account_slot("Liabilities:Empty Card", "apr", "22.00")

        # Should fail because no debt accounts with positive balance
        with pytest.raises(ValueError, match="No debt accounts found"):
            gc_book.debt_payoff_plan(compact=False, monthly_budget="500")

    def test_credit_limit_included(self, debt_book: Path):
        """Should include credit_limit in output when slot is present."""
        gc_book = GnuCashBook(str(debt_book))

        result = gc_book.debt_payoff_plan(compact=False, monthly_budget="1000")

        # Visa has credit_limit slot set to 10000
        visa_detail = None
        mc_detail = None
        for d in result["debts"]:
            if d["account"] == "Liabilities:Visa":
                visa_detail = d
            elif d["account"] == "Liabilities:Mastercard":
                mc_detail = d

        assert visa_detail is not None
        assert "credit_limit" in visa_detail
        assert visa_detail["credit_limit"] == "10000"

        # Mastercard does not have credit_limit slot
        assert mc_detail is not None
        assert "credit_limit" not in mc_detail

    def test_invalid_budget_zero(self, debt_book: Path):
        """Should raise ValueError for zero budget."""
        gc_book = GnuCashBook(str(debt_book))

        with pytest.raises(ValueError, match="must be a positive number"):
            gc_book.debt_payoff_plan(compact=False, monthly_budget="0")

    def test_invalid_budget_negative(self, debt_book: Path):
        """Should raise ValueError for negative budget."""
        gc_book = GnuCashBook(str(debt_book))

        with pytest.raises(ValueError, match="must be a positive number"):
            gc_book.debt_payoff_plan(compact=False, monthly_budget="-500")

    def test_total_paid_equals_balance_plus_interest(self, debt_book: Path):
        """Total paid should equal total balance + total interest."""
        gc_book = GnuCashBook(str(debt_book))

        result = gc_book.debt_payoff_plan(compact=False, monthly_budget="1000")

        total_balance = Decimal(result["total_balance"])
        total_interest = Decimal(result["total_interest"])
        total_paid = Decimal(result["total_paid"])

        assert total_paid == total_balance + total_interest

    def test_minimum_payment_small_balance(self, test_book: Path):
        """When balance is below $25, minimum should be the full balance."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_account(
            name="Almost Paid",
            account_type="CREDIT",
            parent="Liabilities",
        )
        gc_book.set_account_slot("Liabilities:Almost Paid", "apr", "20.00")

        # Small $15 charge
        gc_book.create_transaction(
            description="Small charge",
            splits=[
                {"account": "Liabilities:Almost Paid", "amount": "-15"},
                {"account": "Expenses:Groceries", "amount": "15"},
            ],
            trans_date=date(2026, 1, 1),
        )

        result = gc_book.debt_payoff_plan(compact=False, monthly_budget="50")

        for d in result["debts"]:
            if d["account"] == "Liabilities:Almost Paid":
                # Balance is $15, which is below $25, so min = full balance
                assert d["minimum_payment"] == "15"
                break
        else:
            pytest.fail("Almost Paid not found in results")


class TestDebtPayoffCompactFormat:
    """Phase 4A lock tests for the compact text-table output.
    The verbose dict (existing tests above) is now opt-in; the default
    is a compact summary the LLM can read directly without paying for
    the full ``debts`` / ``yeti`` structure on every call."""

    def test_compact_default_returns_string(self, debt_book):
        from datetime import date
        gc_book = GnuCashBook(str(debt_book))
        gc_book.create_transaction(
            description="Visa charge",
            splits=[
                {"account": "Liabilities:Visa", "amount": "-1000"},
                {"account": "Expenses:Groceries", "amount": "1000"},
            ],
            trans_date=date(2026, 1, 1),
        )
        result = gc_book.debt_payoff_plan(monthly_budget="2000")
        assert isinstance(result, str)

    def test_compact_includes_kill_order_header(self, debt_book):
        from datetime import date
        gc_book = GnuCashBook(str(debt_book))
        gc_book.create_transaction(
            description="Visa charge",
            splits=[
                {"account": "Liabilities:Visa", "amount": "-1000"},
                {"account": "Expenses:Groceries", "amount": "1000"},
            ],
            trans_date=date(2026, 1, 1),
        )
        result = gc_book.debt_payoff_plan(monthly_budget="2000")
        # Header gives budget → debt-free month → total interest at a glance.
        assert "Kill order" in result
        assert "/mo" in result
        assert "debt-free" in result
        assert "interest" in result

    def test_compact_includes_yeti_line(self, debt_book):
        from datetime import date
        gc_book = GnuCashBook(str(debt_book))
        gc_book.create_transaction(
            description="Visa charge",
            splits=[
                {"account": "Liabilities:Visa", "amount": "-1000"},
                {"account": "Expenses:Groceries", "amount": "1000"},
            ],
            trans_date=date(2026, 1, 1),
        )
        result = gc_book.debt_payoff_plan(monthly_budget="2000")
        assert "YETI" in result
        # The plain-English explanation lives on the YETI line itself.
        assert "total debt impact" in result

    def test_compact_drops_per_account_yeti_explanation(self, debt_book):
        """The pre-fix verbose response embedded a multi-line YETI
        explanation per account. Compact mode must NOT replicate that
        bloat — the YETI signal lives on a single summary line."""
        from datetime import date
        gc_book = GnuCashBook(str(debt_book))
        gc_book.create_transaction(
            description="Visa charge",
            splits=[
                {"account": "Liabilities:Visa", "amount": "-1000"},
                {"account": "Expenses:Groceries", "amount": "1000"},
            ],
            trans_date=date(2026, 1, 1),
        )
        result = gc_book.debt_payoff_plan(monthly_budget="2000")
        # Each "by the time your debt is paid off" string was repeated
        # per account in the old shape. Should appear at most once now
        # (or zero times — we use "total debt impact" wording instead).
        assert result.count("by the time your debt is paid off") <= 1
