"""Tests for budget tools."""

import pytest
from decimal import Decimal
from pathlib import Path

from gnucash_mcp.book import GnuCashBook


# ============== TestCreateBudget ==============


class TestCreateBudget:
    """Tests for create_budget."""

    def test_create_monthly_budget(self, budget_book: Path):
        """Create a standard 12-period monthly budget."""
        book = GnuCashBook(str(budget_book))
        result = book.create_budget(
            name="2026 Budget",
            year=2026,
            num_periods=12,
            period_type="monthly",
            description="Annual household budget",
        )

        assert result["name"] == "2026 Budget"
        assert result["status"] == "created"
        assert result["guid"]  # non-empty GUID

        # Verify via get_budget
        budget = book.get_budget("2026 Budget")
        assert budget is not None
        assert budget["num_periods"] == 12
        assert budget["period_type"] == "monthly"
        assert budget["start_date"] == "2026-01-01"
        assert budget["description"] == "Annual household budget"

    def test_create_quarterly_budget(self, budget_book: Path):
        """Create a 4-period quarterly budget."""
        book = GnuCashBook(str(budget_book))
        result = book.create_budget(
            name="Q Budget",
            year=2026,
            num_periods=4,
            period_type="quarterly",
        )

        assert result["status"] == "created"

        budget = book.get_budget("Q Budget")
        assert budget["num_periods"] == 4
        assert budget["period_type"] == "quarterly"

    def test_create_weekly_budget(self, budget_book: Path):
        """Create a weekly budget."""
        book = GnuCashBook(str(budget_book))
        result = book.create_budget(
            name="Weekly Budget",
            year=2026,
            num_periods=52,
            period_type="weekly",
        )

        assert result["status"] == "created"

        budget = book.get_budget("Weekly Budget")
        assert budget["num_periods"] == 52
        assert budget["period_type"] == "weekly"

    def test_create_duplicate_name_raises(self, budget_book: Path):
        """Duplicate budget name raises ValueError."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026)

        with pytest.raises(ValueError, match="Budget already exists"):
            book.create_budget(name="2026 Budget", year=2026)

    def test_create_invalid_period_type_raises(self, budget_book: Path):
        """Invalid period_type raises ValueError."""
        book = GnuCashBook(str(budget_book))

        with pytest.raises(ValueError, match="Invalid period_type"):
            book.create_budget(
                name="Bad Budget",
                year=2026,
                period_type="biweekly",
            )

    def test_create_invalid_num_periods_raises(self, budget_book: Path):
        """num_periods < 1 raises ValueError."""
        book = GnuCashBook(str(budget_book))

        with pytest.raises(ValueError, match="num_periods must be at least 1"):
            book.create_budget(
                name="Bad Budget",
                year=2026,
                num_periods=0,
            )


# ============== TestSetBudgetAmount ==============


class TestSetBudgetAmount:
    """Tests for set_budget_amount."""

    def test_set_all_periods(self, budget_book: Path):
        """Set budget amount for all periods."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        result = book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="500.00",
        )

        assert result["status"] == "updated"
        assert result["account"] == "Expenses:Groceries"
        assert result["amount"] == "500.00"
        assert len(result["periods_set"]) == 12

        # Verify via get_budget
        budget = book.get_budget("2026 Budget")
        grocery_amounts = None
        for acct in budget["accounts"]:
            if acct["account"] == "Expenses:Groceries":
                grocery_amounts = acct["periods"]
                break
        assert grocery_amounts is not None
        assert len(grocery_amounts) == 12
        for p in range(12):
            assert Decimal(grocery_amounts[p]) == Decimal("500.00")

    def test_set_single_period(self, budget_book: Path):
        """Set budget amount for a single period."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        result = book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="600.00",
            period=0,
        )

        assert result["periods_set"] == [0]

        budget = book.get_budget("2026 Budget")
        grocery_amounts = None
        for acct in budget["accounts"]:
            if acct["account"] == "Expenses:Groceries":
                grocery_amounts = acct["periods"]
                break
        assert grocery_amounts is not None
        assert len(grocery_amounts) == 1
        assert Decimal(grocery_amounts[0]) == Decimal("600.00")

    def test_set_quarter_periods(self, budget_book: Path):
        """Set budget amount for a quarter (q1 = periods 0,1,2)."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        result = book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Dining",
            amount="200.00",
            period="q1",
        )

        assert result["periods_set"] == [0, 1, 2]

    def test_overwrite_existing_amount(self, budget_book: Path):
        """Overwriting an existing budget amount works."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        # Set initial amount
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="500.00",
            period=0,
        )

        # Overwrite
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="700.00",
            period=0,
        )

        budget = book.get_budget("2026 Budget")
        for acct in budget["accounts"]:
            if acct["account"] == "Expenses:Groceries":
                assert Decimal(acct["periods"][0]) == Decimal("700.00")
                break

    def test_set_nonexistent_budget_raises(self, budget_book: Path):
        """Setting amount on nonexistent budget raises ValueError."""
        book = GnuCashBook(str(budget_book))

        with pytest.raises(ValueError, match="Budget not found"):
            book.set_budget_amount(
                budget_name="Nonexistent",
                account="Expenses:Groceries",
                amount="500.00",
            )

    def test_set_nonexistent_account_raises(self, budget_book: Path):
        """Setting amount for nonexistent account raises ValueError."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        with pytest.raises(ValueError, match="Account not found"):
            book.set_budget_amount(
                budget_name="2026 Budget",
                account="Expenses:NonExistent",
                amount="500.00",
            )


# ============== TestGetBudgetReport ==============


class TestGetBudgetReport:
    """Tests for get_budget_report."""

    def _create_budget_with_amounts(self, book: GnuCashBook):
        """Helper: create a 2026 monthly budget with amounts set."""
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="600.00",
        )
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Dining",
            amount="200.00",
        )

    def test_report_specific_period_january(self, budget_book: Path):
        """Report for period 0 (January) with known actuals."""
        book = GnuCashBook(str(budget_book))
        self._create_budget_with_amounts(book)

        report = book.get_budget_report(
            budget_name="2026 Budget",
            period=0,  # January 2026
        )

        assert report["budget"] == "2026 Budget"
        assert "Period 0" in report["period"]
        assert "2026-01-01" in report["period"]

        # Find groceries — Jan actual should be $500
        for acct in report["accounts"]:
            if acct["account"] == "Expenses:Groceries":
                assert Decimal(acct["budgeted"]) == Decimal("600")
                assert Decimal(acct["actual"]) == Decimal("500")
                assert Decimal(acct["remaining"]) == Decimal("100")
                break
        else:
            pytest.fail("Expenses:Groceries not found in report")

        # Find dining — Jan actual should be $150
        for acct in report["accounts"]:
            if acct["account"] == "Expenses:Dining":
                assert Decimal(acct["budgeted"]) == Decimal("200")
                assert Decimal(acct["actual"]) == Decimal("150")
                assert Decimal(acct["remaining"]) == Decimal("50")
                break
        else:
            pytest.fail("Expenses:Dining not found in report")

    def test_report_over_budget(self, budget_book: Path):
        """Report shows negative remaining when over budget."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        # Set groceries budget to $300 — actual is $500 in January
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="300.00",
        )

        report = book.get_budget_report(
            budget_name="2026 Budget",
            period=0,
        )

        for acct in report["accounts"]:
            if acct["account"] == "Expenses:Groceries":
                assert Decimal(acct["budgeted"]) == Decimal("300")
                assert Decimal(acct["actual"]) == Decimal("500")
                assert Decimal(acct["remaining"]) == Decimal("-200")
                # percent_used should be > 100
                assert Decimal(acct["percent_used"]) > Decimal("100")
                break

    def test_report_no_transactions_period(self, budget_book: Path):
        """Report for period with no transactions shows actual=0."""
        book = GnuCashBook(str(budget_book))
        self._create_budget_with_amounts(book)

        # Period 2 = March 2026, no transactions exist
        report = book.get_budget_report(
            budget_name="2026 Budget",
            period=2,
        )

        for acct in report["accounts"]:
            assert Decimal(acct["actual"]) == Decimal("0")
            assert acct["remaining"] == acct["budgeted"]

    def test_report_february(self, budget_book: Path):
        """Report for period 1 (February) with known actuals."""
        book = GnuCashBook(str(budget_book))
        self._create_budget_with_amounts(book)

        report = book.get_budget_report(
            budget_name="2026 Budget",
            period=1,  # February 2026
        )

        # Feb groceries: $150
        for acct in report["accounts"]:
            if acct["account"] == "Expenses:Groceries":
                assert Decimal(acct["actual"]) == Decimal("150")
                break

        # Feb dining: $95
        for acct in report["accounts"]:
            if acct["account"] == "Expenses:Dining":
                assert Decimal(acct["actual"]) == Decimal("95")
                break

    def test_report_all_periods(self, budget_book: Path):
        """Report for all periods aggregates correctly."""
        book = GnuCashBook(str(budget_book))
        self._create_budget_with_amounts(book)

        report = book.get_budget_report(
            budget_name="2026 Budget",
            period="all",
        )

        assert "Periods 0-11" in report["period"]

        # Groceries: total budgeted = 600*12 = 7200, actual = 500 + 150 = 650
        for acct in report["accounts"]:
            if acct["account"] == "Expenses:Groceries":
                assert Decimal(acct["budgeted"]) == Decimal("7200")
                assert Decimal(acct["actual"]) == Decimal("650")
                break

        # Dining: total budgeted = 200*12 = 2400, actual = 150 + 95 = 245
        for acct in report["accounts"]:
            if acct["account"] == "Expenses:Dining":
                assert Decimal(acct["budgeted"]) == Decimal("2400")
                assert Decimal(acct["actual"]) == Decimal("245")
                break

    def test_report_account_filter(self, budget_book: Path):
        """Report filtered to a specific account only shows that account."""
        book = GnuCashBook(str(budget_book))
        self._create_budget_with_amounts(book)

        report = book.get_budget_report(
            budget_name="2026 Budget",
            period=0,
            account="Expenses:Groceries",
        )

        assert len(report["accounts"]) == 1
        assert report["accounts"][0]["account"] == "Expenses:Groceries"

    def test_report_totals(self, budget_book: Path):
        """Report totals aggregate across accounts."""
        book = GnuCashBook(str(budget_book))
        self._create_budget_with_amounts(book)

        report = book.get_budget_report(
            budget_name="2026 Budget",
            period=0,
        )

        # Jan totals: budgeted 600+200=800, actual 500+150=650
        assert Decimal(report["totals"]["budgeted"]) == Decimal("800")
        assert Decimal(report["totals"]["actual"]) == Decimal("650")
        assert Decimal(report["totals"]["remaining"]) == Decimal("150")

    def test_report_nonexistent_budget_raises(self, budget_book: Path):
        """Reporting on nonexistent budget raises ValueError."""
        book = GnuCashBook(str(budget_book))

        with pytest.raises(ValueError, match="Budget not found"):
            book.get_budget_report(budget_name="Nonexistent", period=0)


# ============== TestListAndGetBudget ==============


class TestListAndGetBudget:
    """Tests for list_budgets and get_budget."""

    def test_list_empty(self, budget_book: Path):
        """Listing budgets on a book with no budgets returns empty list."""
        book = GnuCashBook(str(budget_book))
        result = book.list_budgets()
        assert result == []

    def test_list_single_budget(self, budget_book: Path):
        """Listing budgets returns created budgets."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        result = book.list_budgets()
        assert len(result) == 1
        assert result[0]["name"] == "2026 Budget"
        assert result[0]["num_periods"] == 12
        assert result[0]["period_type"] == "monthly"

    def test_get_budget_with_amounts(self, budget_book: Path):
        """get_budget returns amounts grouped by account and period."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="500.00",
            period=0,
        )
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="550.00",
            period=1,
        )

        budget = book.get_budget("2026 Budget")
        assert budget is not None
        assert len(budget["accounts"]) == 1
        assert budget["accounts"][0]["account"] == "Expenses:Groceries"
        assert Decimal(budget["accounts"][0]["periods"][0]) == Decimal("500.00")
        assert Decimal(budget["accounts"][0]["periods"][1]) == Decimal("550.00")

    def test_get_nonexistent_returns_none(self, budget_book: Path):
        """get_budget returns None for nonexistent budget."""
        book = GnuCashBook(str(budget_book))
        assert book.get_budget("Nonexistent") is None


# ============== TestDeleteBudget ==============


class TestDeleteBudget:
    """Tests for delete_budget."""

    def test_delete_existing(self, budget_book: Path):
        """Delete an existing budget."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026)

        result = book.delete_budget("2026 Budget")
        assert result["status"] == "deleted"
        assert result["name"] == "2026 Budget"

        # Verify it's gone
        assert book.get_budget("2026 Budget") is None
        assert book.list_budgets() == []

    def test_delete_with_amounts(self, budget_book: Path):
        """Delete a budget with amounts (cascade delete)."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="500.00",
        )

        result = book.delete_budget("2026 Budget")
        assert result["status"] == "deleted"
        assert book.list_budgets() == []

    def test_delete_nonexistent_raises(self, budget_book: Path):
        """Deleting a nonexistent budget raises ValueError."""
        book = GnuCashBook(str(budget_book))

        with pytest.raises(ValueError, match="Budget not found"):
            book.delete_budget("Nonexistent")


# ============== TestBudgetIntegration ==============


class TestBudgetIntegration:
    """Integration tests for the full budget workflow."""

    def test_full_workflow(self, budget_book: Path):
        """Full workflow: create → set amounts → verify report."""
        book = GnuCashBook(str(budget_book))

        # Create budget
        result = book.create_budget(
            name="2026 Budget",
            year=2026,
            num_periods=12,
            period_type="monthly",
        )
        assert result["status"] == "created"

        # Set budget amounts for multiple accounts
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="600.00",
        )
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Dining",
            amount="200.00",
        )
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Entertainment",
            amount="100.00",
        )

        # Verify listing
        budgets = book.list_budgets()
        assert len(budgets) == 1
        assert budgets[0]["name"] == "2026 Budget"

        # Get full budget details
        budget = book.get_budget("2026 Budget")
        assert len(budget["accounts"]) == 3

        # Get January report
        report = book.get_budget_report(
            budget_name="2026 Budget",
            period=0,
        )

        # Verify: Groceries budgeted=600, actual=500 (under budget)
        # Dining budgeted=200, actual=150 (under budget)
        # Entertainment budgeted=100, actual=0 (no transactions)
        accounts_by_name = {a["account"]: a for a in report["accounts"]}

        assert Decimal(accounts_by_name["Expenses:Groceries"]["actual"]) == Decimal("500")
        assert Decimal(accounts_by_name["Expenses:Dining"]["actual"]) == Decimal("150")
        assert Decimal(accounts_by_name["Expenses:Entertainment"]["actual"]) == Decimal("0")

        # Totals: budgeted=900, actual=650
        assert Decimal(report["totals"]["budgeted"]) == Decimal("900")
        assert Decimal(report["totals"]["actual"]) == Decimal("650")

    def test_quarter_override(self, budget_book: Path):
        """Set all periods then override a quarter with different amount."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        # Set $500 for all periods
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="500.00",
        )

        # Override Q4 with $700
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="700.00",
            period="q4",
        )

        budget = book.get_budget("2026 Budget")
        for acct in budget["accounts"]:
            if acct["account"] == "Expenses:Groceries":
                # Q1-Q3 (periods 0-8) should be $500
                for p in range(9):
                    assert Decimal(acct["periods"][p]) == Decimal("500.00")
                # Q4 (periods 9-11) should be $700
                for p in range(9, 12):
                    assert Decimal(acct["periods"][p]) == Decimal("700.00")
                break
        else:
            pytest.fail("Expenses:Groceries not found in budget")
