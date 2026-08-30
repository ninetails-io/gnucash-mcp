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

        # Explanation should be human-readable. Currency mnemonic
        # flows from the book's default currency (USD on the test
        # fixture; would be CNY/EUR/etc. on non-USD books).
        assert "USD 1.00 purchase" in yeti["explanation"]
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

    def test_no_debt_accounts_with_apr(self, test_book: Path):
        """Should raise the MP-9 "Found N but no apr" branch.

        The test_book fixture has a Liabilities placeholder
        account (debt-typed) but no APR slot set anywhere, so the
        error explains that fixing the slot is the right next
        action — NOT "create a debt account first."
        """
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(
            ValueError,
            match=r"Found \d+ CREDIT/LIABILITY account",
        ):
            gc_book.debt_payoff_plan(compact=False, monthly_budget="500")

    def test_foreign_currency_debt_balance_is_converted(
        self, tmp_path: Path,
    ):
        """M1 regression: a foreign-currency debt balance must be
        valued in the book default currency (rate × quantity), not
        summed as raw foreign units. Pre-fix debt_payoff_plan summed
        raw split.quantity — the one reporting-layer method that
        bypassed the FX helper.
        """
        import piecash
        from piecash import factories

        book_path = tmp_path / "fx_debt.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        usd = book.default_currency
        eur = factories.create_currency_from_ISO("EUR")
        book.session.add(eur)
        root = book.root_account

        liabilities = piecash.Account(
            name="Liabilities", type="LIABILITY", parent=root,
            commodity=usd, placeholder=True,
        )
        euro_loan = piecash.Account(
            name="Euro Loan", type="LIABILITY", parent=liabilities,
            commodity=eur,
        )
        expenses = piecash.Account(
            name="Expenses", type="EXPENSE", parent=root,
            commodity=eur, placeholder=True,
        )
        spent = piecash.Account(
            name="Spent", type="EXPENSE", parent=expenses, commodity=eur,
        )
        book.save()

        euro_loan["apr"] = "10.00"
        euro_loan["minimum_payment"] = "100"
        book.save()

        # Draw 1000 EUR on the loan (EUR transaction; value==quantity).
        book.session.add(piecash.Transaction(
            currency=eur, description="Loan draw",
            post_date=date(2026, 1, 1),
            splits=[
                piecash.Split(
                    account=euro_loan,
                    value=Decimal("-1000"), quantity=Decimal("-1000"),
                ),
                piecash.Split(
                    account=spent,
                    value=Decimal("1000"), quantity=Decimal("1000"),
                ),
            ],
        ))
        # EUR/USD = 1.25 → 1000 EUR owed == 1250 USD.
        book.session.add(piecash.Price(
            commodity=eur, currency=usd, date=date(2026, 2, 1),
            value="1.25", source="user:test", type="nav",
        ))
        book.save()

        gc_book = GnuCashBook(str(book_path))
        result = gc_book.debt_payoff_plan(
            compact=False, monthly_budget="1000",
        )

        loan = next(
            d for d in result["debts"]
            if d["account"] == "Liabilities:Euro Loan"
        )
        # Converted (1000 × 1.25), not the raw foreign 1000.
        assert Decimal(loan["balance"]) == Decimal("1250.00")
        assert Decimal(result["total_balance"]) == Decimal("1250.00")
        # A3 (adversarial pass 2): the minimum_payment slot is an
        # account-currency scalar — 100 EUR must enter the plan as
        # 125.00 USD, not be treated as $100. Pre-fix the M1 balance
        # conversion de-synced balance and minimum, skewing the
        # feasibility gate.
        assert Decimal(loan["minimum_payment"]) == Decimal("125.00")

    def test_no_debt_accounts_at_all(self, tmp_path: Path):
        """Should raise the MP-9 "no debt accounts" branch on a
        chart with zero CREDIT/LIABILITY accounts.

        Build a minimal book with only Assets/Income/Expenses/
        Equity so the "no debt-typed accounts at all" path fires.
        """
        import piecash
        from decimal import Decimal

        book_path = tmp_path / "no_liabilities.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = book.root_account
        usd = book.default_currency
        # Only non-debt types.
        piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=True,
        )
        piecash.Account(
            name="Income", type="INCOME", parent=root,
            commodity=usd, placeholder=True,
        )
        book.save()
        book.close()

        gc_book = GnuCashBook(str(book_path))
        with pytest.raises(
            ValueError,
            match="No CREDIT or LIABILITY accounts",
        ):
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

        # Should fail because the CREDIT account exists but has
        # zero balance — MP-9's "Found N accounts but ..." branch.
        with pytest.raises(
            ValueError,
            match=r"Found \d+ CREDIT/LIABILITY account",
        ):
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


class TestDebtPayoffAmortizingLoans:
    """Regression: LIABILITY accounts (mortgage, auto loan) must use
    the amortization formula, not the credit-card 2%-of-balance rule.

    Pre-fix, a ¥2.7M mortgage at 3.85% APR was assigned a minimum of
    ¥54,590/month (2% of balance), tripping the budget gate on any
    realistic household budget. Post-fix, amortization gives
    ~¥12,800/month, leaving plenty of room for a ¥30K budget.
    """

    def _liability_book(self, tmp_path: Path) -> Path:
        """A book with a mortgage + auto loan + credit card, no min slot."""
        import piecash

        book_path = tmp_path / "amort_test.gnucash"
        book = piecash.create_book(
            str(book_path), currency="CNY", overwrite=True,
        )
        cny = book.default_currency
        root = book.root_account

        liabilities = piecash.Account(
            name="Liabilities", type="LIABILITY", parent=root,
            commodity=cny, placeholder=True,
        )
        loans = piecash.Account(
            name="Loans", type="LIABILITY", parent=liabilities,
            commodity=cny, placeholder=True,
        )
        mortgage = piecash.Account(
            name="Mortgage", type="LIABILITY", parent=loans,
            commodity=cny,
        )
        auto = piecash.Account(
            name="Auto Loan", type="LIABILITY", parent=loans,
            commodity=cny,
        )
        cc = piecash.Account(
            name="Credit Card", type="CREDIT", parent=liabilities,
            commodity=cny,
        )
        equity = piecash.Account(
            name="Equity", type="EQUITY", parent=root,
            commodity=cny, placeholder=True,
        )
        opening = piecash.Account(
            name="Opening Balance", type="EQUITY", parent=equity,
            commodity=cny,
        )
        book.save()

        mortgage["apr"] = "3.85"
        auto["apr"] = "4.90"
        # The amortization term is data, not inferred from the account
        # name (the old English "mortgage" substring was locale-
        # fragile — it missed "Hypothek"/"Darlehen"). Each loan
        # declares its term via the loan_term_months slot: the Mortgage
        # 30y, the Auto Loan 5y. There is no default term — a loan
        # without this slot (and without minimum_payment) is omitted
        # from the plan (see test_liability_without_term_is_omitted).
        mortgage["loan_term_months"] = "360"
        auto["loan_term_months"] = "60"
        cc["apr"] = "18.25"
        book.save()

        # Set opening balances. Liabilities are credit-normal (negative
        # quantity = positive amount owed).
        for acct, amount in (
            (mortgage, "-2729518"),
            (auto, "-96798"),
            (cc, "-19385"),
        ):
            tx = piecash.Transaction(
                currency=cny,
                description=f"Opening: {acct.name}",
                post_date=date(2026, 1, 1),
                splits=[
                    piecash.Split(account=acct, value=Decimal(amount)),
                    piecash.Split(account=opening, value=-Decimal(amount)),
                ],
            )
            book.session.add(tx)
        book.save()
        return book_path

    def test_mortgage_uses_amortization_not_two_percent(self, tmp_path: Path):
        """The mortgage minimum should be ~¥12-13K (amortization on its
        slot-declared 30-year term), not ¥54K (2% of balance)."""
        book_path = self._liability_book(tmp_path)
        gc_book = GnuCashBook(str(book_path))

        result = gc_book.debt_payoff_plan(
            compact=False, monthly_budget="30000",
        )

        for d in result["debts"]:
            if d["account"] == "Liabilities:Loans:Mortgage":
                mp = Decimal(d["minimum_payment"])
                # 30-year amortization on ¥2,729,518 at 3.85% =
                # ~¥12,789, from the loan_term_months=360 slot. Allow a
                # generous ±¥500 band — the formula is exact; the band
                # absorbs rounding only.
                assert Decimal("12200") < mp < Decimal("13400"), (
                    f"Mortgage minimum {mp} outside expected range"
                )
                # And nowhere near the broken 2% answer (¥54,590).
                assert mp < Decimal("20000")
                break
        else:
            pytest.fail("Mortgage not found in results")

    def test_auto_loan_uses_amortization_not_two_percent(self, tmp_path: Path):
        """Auto loan uses its loan_term_months slot (5-year term)."""
        book_path = self._liability_book(tmp_path)
        gc_book = GnuCashBook(str(book_path))

        result = gc_book.debt_payoff_plan(
            compact=False, monthly_budget="30000",
        )

        for d in result["debts"]:
            if d["account"] == "Liabilities:Loans:Auto Loan":
                mp = Decimal(d["minimum_payment"])
                # 5-year amortization on ¥96,798 at 4.9% = ~¥1,824.
                # Generous ±¥150 band.
                assert Decimal("1700") < mp < Decimal("2000"), (
                    f"Auto loan minimum {mp} outside expected range"
                )
                # Discriminate by term: the 5y slot → ~¥1,824, whereas
                # the mortgage's 30y slot on the same balance would give
                # far less. Proves the loan_term_months slot drives the
                # amortization, not the account name (and not a default).
                assert mp > Decimal("1500"), (
                    "Auto loan minimum too low — the loan_term_months "
                    "slot (60) was not honored"
                )
                break
        else:
            pytest.fail("Auto Loan not found in results")

    def test_credit_card_keeps_two_percent(self, tmp_path: Path):
        """Credit cards still use the 2% formula post-fix — only
        LIABILITY accounts switched to amortization."""
        book_path = self._liability_book(tmp_path)
        gc_book = GnuCashBook(str(book_path))

        result = gc_book.debt_payoff_plan(
            compact=False, monthly_budget="30000",
        )

        for d in result["debts"]:
            if d["account"] == "Liabilities:Credit Card":
                # 2% of ¥19,385 = ¥387.70 (above the ¥25 floor).
                assert d["minimum_payment"] == "387.70"
                break
        else:
            pytest.fail("Credit Card not found in results")

    def test_realistic_budget_no_longer_trips_gate(self, tmp_path: Path):
        """The original bug: ¥30K budget on a ¥2.85M debt stack
        rejected as 'less than sum of minimum payments' because the
        2% rule was demanding ¥57K/month. Post-fix it should clear."""
        book_path = self._liability_book(tmp_path)
        gc_book = GnuCashBook(str(book_path))

        # No exception → fix works.
        result = gc_book.debt_payoff_plan(
            compact=False, monthly_budget="30000",
        )
        assert "debts" in result
        # Sum of minimums should land in the ~¥15-17K range
        # (mortgage + auto + 2% of CC), well under ¥30K.
        total_min = sum(
            Decimal(d["minimum_payment"]) for d in result["debts"]
        )
        assert total_min < Decimal("18000"), (
            f"Sum of minimums {total_min} too high; "
            "amortization fix may have regressed"
        )

    def test_minimum_payment_slot_still_wins_for_liability(
        self, tmp_path: Path,
    ):
        """The user-set minimum_payment slot must still override the
        amortization formula — explicit user input always wins."""
        book_path = self._liability_book(tmp_path)
        gc_book = GnuCashBook(str(book_path))
        # Pin mortgage minimum to a specific value via slot.
        gc_book.set_account_slot(
            "Liabilities:Loans:Mortgage", "minimum_payment", "14800",
        )

        result = gc_book.debt_payoff_plan(
            compact=False, monthly_budget="30000",
        )

        for d in result["debts"]:
            if d["account"] == "Liabilities:Loans:Mortgage":
                # Slot value (14800) wins over amortization estimate (~12789).
                assert d["minimum_payment"] == "14800"
                break
        else:
            pytest.fail("Mortgage not found in results")

    def test_liability_without_term_is_omitted(self, tmp_path: Path):
        """A LIABILITY with an APR and balance but no loan_term_months
        and no minimum_payment is omitted from the plan rather than
        amortized from a guessed term — a wrong estimate (30y vs 5y
        differ by an order of magnitude) is worse than none."""
        book_path = self._liability_book(tmp_path)
        gc_book = GnuCashBook(str(book_path))
        # Strip the Mortgage's term slot so it has no payment source.
        gc_book.delete_account_slot(
            "Liabilities:Loans:Mortgage", "loan_term_months",
        )

        result = gc_book.debt_payoff_plan(
            compact=False, monthly_budget="30000",
        )

        # Omitted from the actionable plan...
        names = [d["account"] for d in result["debts"]]
        assert "Liabilities:Loans:Mortgage" not in names
        # ...and surfaced as unestimable with an actionable message,
        # rather than silently dropped or guessed.
        assert "Liabilities:Loans:Mortgage" in result["unestimable"]
        assert any(
            "loan_term_months" in w for w in result["warnings"]
        ), "expected a warning naming loan_term_months"
        # The estimable debts (Auto Loan term=60, Credit Card 2%) still plan.
        assert "Liabilities:Loans:Auto Loan" in names

    def test_all_liabilities_unestimable_raises_actionably(
        self, tmp_path: Path,
    ):
        """If every qualifying debt is unestimable, the error names the
        fix (set loan_term_months / minimum_payment), not the wrong
        'no apr slot' cause."""
        import piecash

        book_path = tmp_path / "unestimable_only.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        usd = book.default_currency
        root = book.root_account
        liabilities = piecash.Account(
            name="Liabilities", type="LIABILITY", parent=root,
            commodity=usd, placeholder=True,
        )
        loan = piecash.Account(
            name="Personal Loan", type="LIABILITY", parent=liabilities,
            commodity=usd,
        )
        equity = piecash.Account(
            name="Equity", type="EQUITY", parent=root,
            commodity=usd, placeholder=True,
        )
        opening = piecash.Account(
            name="Opening Balance", type="EQUITY", parent=equity,
            commodity=usd,
        )
        book.save()
        loan["apr"] = "6.5"  # APR set, but no term and no minimum_payment.
        book.save()
        tx = piecash.Transaction(
            currency=usd, description="Opening: Personal Loan",
            post_date=date(2026, 1, 1),
            splits=[
                piecash.Split(account=loan, value=Decimal("-12000")),
                piecash.Split(account=opening, value=Decimal("12000")),
            ],
        )
        book.session.add(tx)
        book.save()

        gc_book = GnuCashBook(str(book_path))
        with pytest.raises(ValueError, match="loan_term_months"):
            gc_book.debt_payoff_plan(
                compact=False, monthly_budget="5000",
            )


class TestDebtPayoffTemplateAccountFiltering:
    """Defense-in-depth: ensure template accounts (scaffolding
    under ``book.root_template`` for scheduled transactions)
    cannot leak into the avalanche schedule, even when they
    inherit type=CREDIT/LIABILITY from a parent in the user's
    chart and have an ``apr`` slot somehow set on them.

    Not currently exploitable in practice (no MCP tool sets
    slots on template accounts), but this locks the contract so
    a future feature can't accidentally regress it.
    """

    def test_template_credit_account_excluded_from_payoff(
        self, debt_book: Path,
    ):
        import piecash
        gc_book = GnuCashBook(str(debt_book))

        # Add a CREDIT-typed template account with an APR slot.
        # If the filter weren't applied, this would inflate
        # the avalanche schedule.
        with gc_book.open(readonly=False) as book:
            tmpl_root = book.root_template
            template_credit = piecash.Account(
                name="Template Credit Card",
                type="CREDIT",
                parent=tmpl_root,
                commodity=book.default_currency,
            )
            book.session.add(template_credit)
            book.save()
            template_credit["apr"] = "30.00"
            # Synthesize a balance via a transaction in the
            # template subtree (templates can have splits,
            # they're just scaffolding).
            book.save()
            template_guid = template_credit.guid

        result = gc_book.debt_payoff_plan(
            compact=False, monthly_budget="1000",
        )

        # The template account must NOT appear in the avalanche
        # schedule. Match by name (the fixture's real accounts
        # are Visa, Mastercard, Car Loan).
        template_account_names = [
            d["account"] for d in result["debts"]
            if "Template" in d["account"]
        ]
        assert template_account_names == [], (
            f"Template account leaked into payoff schedule: "
            f"{template_account_names}"
        )


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


class TestNoAprConfession:
    """Balance-carrying debts without an APR are confessed, not
    silently dropped — the third exclusion class, same contract as
    the FX-unvalued and unestimable confessions."""

    def test_balance_carrying_debt_without_apr_is_confessed(
        self, debt_book: Path,
    ):
        gc_book = GnuCashBook(str(debt_book))
        gc_book.delete_account_slot("Liabilities:Visa", "apr")

        result = gc_book.debt_payoff_plan(
            compact=False, monthly_budget="1000",
        )

        names = [d["account"] for d in result["debts"]]
        assert "Liabilities:Visa" not in names
        assert result["no_apr"] == ["Liabilities:Visa"]
        assert any("'apr'" in w for w in result["warnings"])
        # The other debts still plan.
        assert "Liabilities:Mastercard" in names

    def test_compact_output_carries_the_confession(
        self, debt_book: Path,
    ):
        gc_book = GnuCashBook(str(debt_book))
        gc_book.delete_account_slot("Liabilities:Visa", "apr")

        out = gc_book.debt_payoff_plan(
            compact=True, monthly_budget="1000",
        )
        assert "⚠" in out
        assert "Liabilities:Visa" in out
        assert "balance-carrying" in out
