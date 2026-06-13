"""Tests for budget tools."""

import pytest
from decimal import Decimal
from pathlib import Path

from gnucash_mcp.book import GnuCashBook, build_book_class


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
        budget = book.get_budget(compact=False, name="2026 Budget")
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

        budget = book.get_budget(compact=False, name="Q Budget")
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

        budget = book.get_budget(compact=False, name="Weekly Budget")
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

    def test_create_with_start_date(self, budget_book: Path):
        """Explicit ``start_date`` anchors the first period exactly
        — bookkeeper-flagged feature gap (PR #98 signoff). The
        argument enables historical budget creation for
        comparison against past actuals.
        """
        book = GnuCashBook(str(budget_book))
        result = book.create_budget(
            name="2024 Retroactive",
            start_date="2024-01-01",
            num_periods=12,
            period_type="monthly",
        )
        assert result["start_date"] == "2024-01-01"

        budget = book.get_budget(
            compact=False, name="2024 Retroactive",
        )
        assert budget["start_date"] == "2024-01-01"

    def test_start_date_wins_over_year(self, budget_book: Path):
        """When both ``year`` and ``start_date`` are supplied, the
        start_date is the more specific signal and takes precedence.
        """
        book = GnuCashBook(str(budget_book))
        result = book.create_budget(
            name="Mid-Year",
            year=2026,  # ignored
            start_date="2025-07-01",
            num_periods=6,
            period_type="monthly",
        )
        assert result["start_date"] == "2025-07-01"

        budget = book.get_budget(compact=False, name="Mid-Year")
        # start_date wins; year is ignored.
        assert budget["start_date"] == "2025-07-01"

    def test_create_invalid_start_date_raises(self, budget_book: Path):
        """Malformed ``start_date`` raises with a clear message —
        the ISO parse error is wrapped to surface the field name."""
        book = GnuCashBook(str(budget_book))
        with pytest.raises(
            ValueError,
            match=r"Invalid start_date '2025/01/01'",
        ):
            book.create_budget(
                name="Bad Date Budget",
                start_date="2025/01/01",
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

        # Response is thin — only the computed periods_set and status.
        # Inputs (budget, account, amount) come from tool params.
        assert result["status"] == "updated"
        assert len(result["periods_set"]) == 12

        # Verify via get_budget
        budget = book.get_budget(compact=False, name="2026 Budget")
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

        budget = book.get_budget(compact=False, name="2026 Budget")
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

    def test_set_single_period_numeric_string(self, budget_book: Path):
        """Numeric-string periods coerce to int (MCP XML param layer passes strings)."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        # "11" should behave identically to 11.
        result = book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="800.00",
            period="11",
        )

        assert result["periods_set"] == [11]

        budget = book.get_budget(compact=False, name="2026 Budget")
        grocery_amounts = None
        for acct in budget["accounts"]:
            if acct["account"] == "Expenses:Groceries":
                grocery_amounts = acct["periods"]
                break
        assert grocery_amounts is not None
        assert Decimal(grocery_amounts[11]) == Decimal("800.00")

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

        budget = book.get_budget(compact=False, name="2026 Budget")
        for acct in budget["accounts"]:
            if acct["account"] == "Expenses:Groceries":
                assert Decimal(acct["periods"][0]) == Decimal("700.00")
                break

    def test_subcent_amount_quantizes_consistently_on_insert_and_update(
        self, budget_book: Path,
    ):
        """Sub-cent input quantizes to commodity precision and produces
        identical stored values via the insert and update paths.

        Pre-fix, the insert path did ``int(amount * 100)`` which
        truncated; the update path used piecash's hybrid setter which
        did not. Same input produced different stored values depending
        on whether a row already existed.
        """
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        # Insert path (no existing row) — period 0
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="499.995",
            period=0,
        )

        # Update path — set period 0 again with same input
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="499.995",
            period=0,
        )

        # And on a fresh period — also insert path
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="499.995",
            period=1,
        )

        budget = book.get_budget(compact=False, name="2026 Budget")
        groceries = next(
            a for a in budget["accounts"]
            if a["account"] == "Expenses:Groceries"
        )
        # Banker's rounding: 499.995 → 500.00 (round half to even,
        # 9 is odd → up to 10 → carry → 500.00).
        expected = Decimal("500.00")
        assert Decimal(groceries["periods"][0]) == expected
        assert Decimal(groceries["periods"][1]) == expected
        # Insert and update paths produce the same stored value.
        assert groceries["periods"][0] == groceries["periods"][1]

    def test_subcent_amount_truncation_does_not_silently_drop_digits(
        self, budget_book: Path,
    ):
        """Larger sub-cent input rounds, doesn't truncate.

        Pre-fix, ``int(Decimal('1234.567') * 100)`` truncated to
        123456 → stored 1234.56 instead of rounding to 1234.57.
        """
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="1234.567",
            period=0,
        )

        budget = book.get_budget(compact=False, name="2026 Budget")
        groceries = next(
            a for a in budget["accounts"]
            if a["account"] == "Expenses:Groceries"
        )
        assert Decimal(groceries["periods"][0]) == Decimal("1234.57")

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

        report = book.get_budget_report(compact=False,
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

        report = book.get_budget_report(compact=False,
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
        report = book.get_budget_report(compact=False,
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

        report = book.get_budget_report(compact=False,
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

        report = book.get_budget_report(compact=False,
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

        report = book.get_budget_report(compact=False,
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

        report = book.get_budget_report(compact=False,
            budget_name="2026 Budget",
            period=0,
        )

        # Jan totals: budgeted 600+200=800, actual 500+150=650
        assert Decimal(report["totals"]["budgeted"]) == Decimal("800")
        assert Decimal(report["totals"]["actual"]) == Decimal("650")
        assert Decimal(report["totals"]["remaining"]) == Decimal("150")

    def test_parent_placeholder_rolls_up_children_actuals(
        self, budget_book: Path
    ):
        """Budget set on a parent placeholder sums children's actuals.

        Adds `Expenses:Utilities` (placeholder) with children
        `Electric` + `Gas`. Budget $450 on the parent only. Post a
        $95 charge to Electric and $65 to Gas. Report should show
        `Expenses:Utilities` actual = $160 — not $0.
        """
        import piecash
        from datetime import date as _date

        gc = GnuCashBook(str(budget_book))
        with gc.open(readonly=False) as b:
            usd = b.default_currency
            expenses = next(a for a in b.accounts if a.fullname == "Expenses")
            checking = next(a for a in b.accounts if a.fullname == "Assets:Checking")
            utilities = piecash.Account(
                name="Utilities", type="EXPENSE", parent=expenses,
                commodity=usd, placeholder=True,
            )
            electric = piecash.Account(
                name="Electric", type="EXPENSE", parent=utilities,
                commodity=usd,
            )
            gas = piecash.Account(
                name="Gas", type="EXPENSE", parent=utilities,
                commodity=usd,
            )
            piecash.Transaction(
                currency=usd, description="Electric bill",
                post_date=_date(2026, 1, 15),
                splits=[
                    piecash.Split(account=checking, value=Decimal("-95")),
                    piecash.Split(account=electric, value=Decimal("95")),
                ],
            )
            piecash.Transaction(
                currency=usd, description="Gas bill",
                post_date=_date(2026, 1, 20),
                splits=[
                    piecash.Split(account=checking, value=Decimal("-65")),
                    piecash.Split(account=gas, value=Decimal("65")),
                ],
            )
            b.save()

        gc.create_budget(name="Util Budget", year=2026, num_periods=12)
        gc.set_budget_amount(
            budget_name="Util Budget",
            account="Expenses:Utilities",
            amount="450.00",
            period=0,
        )

        report = gc.get_budget_report(compact=False,
            budget_name="Util Budget",
            period=0,
        )
        util_line = next(
            a for a in report["accounts"]
            if a["account"] == "Expenses:Utilities"
        )
        assert Decimal(util_line["budgeted"]) == Decimal("450")
        assert Decimal(util_line["actual"]) == Decimal("160")
        assert Decimal(util_line["remaining"]) == Decimal("290")

    def test_separately_budgeted_child_does_not_double_count(
        self, budget_book: Path
    ):
        """When both a parent and a child are budgeted, the child's
        actuals stay on its own line — the parent only collects
        actuals from its *non-budgeted* descendants.
        """
        import piecash
        from datetime import date as _date

        gc = GnuCashBook(str(budget_book))
        with gc.open(readonly=False) as b:
            usd = b.default_currency
            expenses = next(a for a in b.accounts if a.fullname == "Expenses")
            checking = next(a for a in b.accounts if a.fullname == "Assets:Checking")
            utilities = piecash.Account(
                name="Utilities", type="EXPENSE", parent=expenses,
                commodity=usd, placeholder=True,
            )
            electric = piecash.Account(
                name="Electric", type="EXPENSE", parent=utilities,
                commodity=usd,
            )
            gas = piecash.Account(
                name="Gas", type="EXPENSE", parent=utilities,
                commodity=usd,
            )
            piecash.Transaction(
                currency=usd, description="Electric",
                post_date=_date(2026, 1, 15),
                splits=[
                    piecash.Split(account=checking, value=Decimal("-95")),
                    piecash.Split(account=electric, value=Decimal("95")),
                ],
            )
            piecash.Transaction(
                currency=usd, description="Gas",
                post_date=_date(2026, 1, 20),
                splits=[
                    piecash.Split(account=checking, value=Decimal("-65")),
                    piecash.Split(account=gas, value=Decimal("65")),
                ],
            )
            b.save()

        gc.create_budget(name="Mixed Budget", year=2026, num_periods=12)
        # Parent budget of $450 + child Electric explicitly budgeted $100.
        # Electric's $95 actual should stay on Electric, not roll up.
        # Only Gas's $65 should roll up to the parent.
        gc.set_budget_amount(
            budget_name="Mixed Budget",
            account="Expenses:Utilities", amount="450", period=0,
        )
        gc.set_budget_amount(
            budget_name="Mixed Budget",
            account="Expenses:Utilities:Electric", amount="100", period=0,
        )
        report = gc.get_budget_report(compact=False,
            budget_name="Mixed Budget", period=0,
        )
        by_acct = {a["account"]: a for a in report["accounts"]}
        assert Decimal(by_acct["Expenses:Utilities"]["actual"]) == Decimal("65")
        assert Decimal(by_acct["Expenses:Utilities:Electric"]["actual"]) == Decimal("95")

    def test_parent_rollup_converts_foreign_currency_children(
        self, budget_book: Path,
    ):
        """When a budgeted parent has foreign-currency descendants,
        the rollup must convert their actuals to the book default
        currency via the latest market rate. Pre-fix, raw
        ``split.quantity`` was summed — 100 EUR + 100 USD became 200
        in the parent's row.

        Reuses the budget_book fixture (USD-default), adds a EUR
        commodity, a EUR-denominated ``Expenses:Travel:Europe`` leaf,
        a USD-default-currency ``Expenses:Travel:US`` sibling, plus
        a price for EUR (1 EUR = 1.10 USD). Then a 100 EUR Europe
        spend + 100 USD US spend should roll up as 110 + 100 = 210
        USD on the ``Travel`` parent — not 200.
        """
        import piecash
        from datetime import date as _date
        from piecash._common import GnucashException
        from piecash import factories

        gc = GnuCashBook(str(budget_book))
        with gc.open(readonly=False) as b:
            usd = b.default_currency
            try:
                eur = factories.create_currency_from_ISO("EUR")
                b.session.add(eur)
                b.flush()
            except (GnucashException, Exception):
                eur = next(
                    (c for c in b.commodities if c.mnemonic == "EUR"), None,
                )
                if eur is None:
                    raise

            expenses = next(
                a for a in b.accounts if a.fullname == "Expenses"
            )
            checking = next(
                a for a in b.accounts if a.fullname == "Assets:Checking"
            )

            travel = piecash.Account(
                name="Travel", type="EXPENSE", parent=expenses,
                commodity=usd, placeholder=True,
            )
            europe = piecash.Account(
                name="Europe", type="EXPENSE", parent=travel,
                commodity=eur,
            )
            us = piecash.Account(
                name="US", type="EXPENSE", parent=travel, commodity=usd,
            )
            # Price: 1 EUR = 1.10 USD (commodity=EUR, currency=USD)
            piecash.Price(
                commodity=eur, currency=usd,
                date=_date(2026, 1, 1),
                value=Decimal("1.10"),
                source="user:price", type="last",
            )
            b.save()

            # 100 EUR Europe spend (transaction is in USD currency for
            # the Checking side; cross-currency split has value=110 USD,
            # quantity=100 EUR on the Europe leg)
            piecash.Transaction(
                currency=usd, description="Hotel in Berlin",
                post_date=_date(2026, 1, 10),
                splits=[
                    piecash.Split(
                        account=checking, value=Decimal("-110"),
                        quantity=Decimal("-110"),
                    ),
                    piecash.Split(
                        account=europe, value=Decimal("110"),
                        quantity=Decimal("100"),  # 100 EUR
                    ),
                ],
            )
            piecash.Transaction(
                currency=usd, description="Hotel in Boston",
                post_date=_date(2026, 1, 15),
                splits=[
                    piecash.Split(
                        account=checking, value=Decimal("-100"),
                    ),
                    piecash.Split(
                        account=us, value=Decimal("100"),
                    ),
                ],
            )
            b.save()

        gc.create_budget(
            name="Travel Budget", year=2026, num_periods=12,
        )
        gc.set_budget_amount(
            budget_name="Travel Budget",
            account="Expenses:Travel", amount="500", period=0,
        )
        report = gc.get_budget_report(
            compact=False,
            budget_name="Travel Budget", period=0,
        )
        by_acct = {a["account"]: a for a in report["accounts"]}
        # Parent rollup: 100 USD + (100 EUR × 1.10) = 210 USD.
        # Pre-fix, raw quantity sum produced 200.
        assert Decimal(by_acct["Expenses:Travel"]["actual"]) == Decimal("210")

    def test_cross_currency_rollup_without_reporting_module(
        self, budget_book: Path,
    ):
        """Cross-currency budget rollup must work on a ``--modules
        core,budgets`` build with no reporting mixin loaded.

        Pre-fix, the conversion helpers
        (``_account_conversion_factors`` / ``_split_in_default_currency``)
        lived on ReportingMixin; budgets reached them via
        ``getattr(self, ..., None)`` and silently degraded to raw
        ``split.quantity`` sums when reporting was disabled — the
        same bug class v1.2.1 fixed for the reports themselves, now
        hiding behind a feature flag.

        After the v1.3 Stage 1 work, currency helpers live on
        :class:`CurrencyMixin` composed into :class:`BaseGnuCashBook`
        unconditionally. This test exercises the
        ``{"core","budgets"}`` build path explicitly to confirm a
        100 EUR + 100 USD spend rolls up as 210 USD on a budgeted
        USD parent (not 200).
        """
        import piecash
        from datetime import date as _date
        from piecash._common import GnucashException
        from piecash import factories

        # Use the default-build GnuCashBook to seed the data — write
        # path needs the full toolkit. The currency-mixin assertion
        # is on the *read* path (`get_budget_report`) using the
        # restricted-module build below.
        gc = GnuCashBook(str(budget_book))
        with gc.open(readonly=False) as b:
            usd = b.default_currency
            try:
                eur = factories.create_currency_from_ISO("EUR")
                b.session.add(eur)
                b.flush()
            except (GnucashException, Exception):
                eur = next(
                    (c for c in b.commodities if c.mnemonic == "EUR"), None,
                )
                if eur is None:
                    raise

            expenses = next(
                a for a in b.accounts if a.fullname == "Expenses"
            )
            checking = next(
                a for a in b.accounts if a.fullname == "Assets:Checking"
            )
            travel = piecash.Account(
                name="Travel", type="EXPENSE", parent=expenses,
                commodity=usd, placeholder=True,
            )
            europe = piecash.Account(
                name="Europe", type="EXPENSE", parent=travel,
                commodity=eur,
            )
            us = piecash.Account(
                name="US", type="EXPENSE", parent=travel, commodity=usd,
            )
            piecash.Price(
                commodity=eur, currency=usd,
                date=_date(2026, 1, 1),
                value=Decimal("1.10"),
                source="user:price", type="last",
            )
            piecash.Transaction(
                currency=usd, description="Hotel in Berlin",
                post_date=_date(2026, 1, 10),
                splits=[
                    piecash.Split(
                        account=checking, value=Decimal("-110"),
                        quantity=Decimal("-110"),
                    ),
                    piecash.Split(
                        account=europe, value=Decimal("110"),
                        quantity=Decimal("100"),
                    ),
                ],
            )
            piecash.Transaction(
                currency=usd, description="Hotel in Boston",
                post_date=_date(2026, 1, 15),
                splits=[
                    piecash.Split(
                        account=checking, value=Decimal("-100"),
                    ),
                    piecash.Split(
                        account=us, value=Decimal("100"),
                    ),
                ],
            )
            b.save()
        gc.create_budget(name="Travel Budget", year=2026, num_periods=12)
        gc.set_budget_amount(
            budget_name="Travel Budget",
            account="Expenses:Travel", amount="500", period=0,
        )

        # ── The actual assertion: query via the restricted build ──
        BookClass = build_book_class({"core", "budgets"})
        # Sanity: this build must NOT carry ReportingMixin's namespace.
        assert "ReportingMixin" not in {
            base.__name__ for base in BookClass.__mro__
        }, "Test setup: build_book_class leaked ReportingMixin"

        restricted = BookClass(str(budget_book))
        report = restricted.get_budget_report(
            compact=False,
            budget_name="Travel Budget", period=0,
        )
        by_acct = {a["account"]: a for a in report["accounts"]}
        # 100 USD + (100 EUR × 1.10) = 210 USD. Raw quantity sum = 200.
        assert Decimal(by_acct["Expenses:Travel"]["actual"]) == Decimal("210")

    def test_report_nonexistent_budget_raises(self, budget_book: Path):
        """Reporting on nonexistent budget raises ValueError."""
        book = GnuCashBook(str(budget_book))

        with pytest.raises(ValueError, match="Budget not found"):
            book.get_budget_report(compact=False,budget_name="Nonexistent", period=0)


# ============== TestBudgetContraNetting ==============


class TestBudgetContraNetting:
    """C3 regression (adversarial pass 2): budget actuals must
    accumulate SIGNED amounts so contra splits (expense refunds,
    income clawbacks) net — the same a34867c fix that
    income_by_source / spending_by_category received. Pre-fix the
    per-split gross filter (``amount > 0`` / ``amount < 0``) made
    the budget surfaces contradict those reports on the same data:
    spend 200 + refund 120 showed actual 200 instead of net 80.
    """

    def test_report_nets_expense_refunds(self, budget_book: Path):
        """get_budget_report: Jan Groceries spend is $500; a $120
        refund must net the actual to $380, not stay at gross $500."""
        from datetime import date as date_cls

        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)
        book.set_budget_amount(
            budget_name="2026 Budget",
            account="Expenses:Groceries",
            amount="600.00",
        )
        book.create_transaction(
            description="Grocery return",
            trans_date=date_cls(2026, 1, 20),
            splits=[
                {"account": "Expenses:Groceries", "amount": "-120.00"},
                {"account": "Assets:Checking", "amount": "120.00"},
            ],
        )

        report = book.get_budget_report(
            compact=False, budget_name="2026 Budget", period=0,
        )
        by_acct = {a["account"]: a for a in report["accounts"]}
        groceries = by_acct["Expenses:Groceries"]
        assert Decimal(groceries["actual"]) == Decimal("380"), (
            f"budget report kept gross actuals (refund dropped): "
            f"{groceries}"
        )
        assert Decimal(groceries["remaining"]) == Decimal("220")

    def test_dashboard_headline_nets_expense_refunds(
        self, budget_book: Path,
    ):
        """_budget_headline (dashboard): spend 200 + refund 120 against
        a 160 budget = 50% used (net 80), not 125% (gross 200).

        Uses Entertainment — the fixture account with no other
        transactions — because the headline accumulates actuals over
        the budget's full period range, and Groceries carries Jan/Feb
        fixture spend that would muddy the assertion.
        """
        from datetime import date as date_cls

        today = date_cls.today()
        month_start = date_cls(today.year, today.month, 1)

        book = GnuCashBook(str(budget_book))
        book.create_budget(
            name="Headline", year=today.year, num_periods=12,
        )
        book.set_budget_amount(
            budget_name="Headline",
            account="Expenses:Entertainment",
            amount="160.00", period=today.month - 1,
        )
        book.create_transaction(
            description="Concert tickets",
            trans_date=month_start,
            splits=[
                {"account": "Expenses:Entertainment", "amount": "200.00"},
                {"account": "Assets:Checking", "amount": "-200.00"},
            ],
        )
        book.create_transaction(
            description="Concert refund",
            trans_date=month_start,
            splits=[
                {"account": "Expenses:Entertainment", "amount": "-120.00"},
                {"account": "Assets:Checking", "amount": "120.00"},
            ],
        )

        with book.open(readonly=True) as b:
            transactions = list(b.transactions)
            headline = book._budget_headline(b, transactions)

        assert headline is not None, "budget headline returned None"
        assert headline["used_pct"] == Decimal("50"), (
            f"dashboard headline kept gross actuals (refund dropped): "
            f"{headline}"
        )


# ============== TestListAndGetBudget ==============


class TestListAndGetBudget:
    """Tests for list_budgets and get_budget."""

    def test_list_empty(self, budget_book: Path):
        """Listing budgets on a book with no budgets returns empty list."""
        book = GnuCashBook(str(budget_book))
        result = book.list_budgets(compact=False)
        assert result == []

    def test_list_single_budget(self, budget_book: Path):
        """Listing budgets returns created budgets."""
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 Budget", year=2026, num_periods=12)

        result = book.list_budgets(compact=False)
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

        budget = book.get_budget(compact=False, name="2026 Budget")
        assert budget is not None
        assert len(budget["accounts"]) == 1
        assert budget["accounts"][0]["account"] == "Expenses:Groceries"
        assert Decimal(budget["accounts"][0]["periods"][0]) == Decimal("500.00")
        assert Decimal(budget["accounts"][0]["periods"][1]) == Decimal("550.00")

    def test_get_nonexistent_returns_none(self, budget_book: Path):
        """get_budget returns None for nonexistent budget."""
        book = GnuCashBook(str(budget_book))
        assert book.get_budget(compact=False, name="Nonexistent") is None


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
        assert book.get_budget(compact=False, name="2026 Budget") is None
        assert book.list_budgets(compact=False) == []

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
        assert book.list_budgets(compact=False) == []

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
        budgets = book.list_budgets(compact=False)
        assert len(budgets) == 1
        assert budgets[0]["name"] == "2026 Budget"

        # Get full budget details
        budget = book.get_budget(compact=False, name="2026 Budget")
        assert len(budget["accounts"]) == 3

        # Get January report
        report = book.get_budget_report(compact=False,
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

        budget = book.get_budget(compact=False, name="2026 Budget")
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
