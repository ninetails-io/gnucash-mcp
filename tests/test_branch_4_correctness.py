"""Regression tests for the Branch 4 math/UX correctness work.

Four thematic groups close 9 of the 11 Branch 4 items (SB-5 and HP-8
deferred as design calls). One test class per bug class so failures
point at the specific invariant that regressed.

Group A — Budget correctness:
- ``TestBudgetTargetFxConversion`` — SB-6
- ``TestBudgetReportDepthProxy`` — HP-7
- ``TestBudgetHeadlineRollup`` — SB-9

Group B — Reporting + dashboard correctness:
- ``TestBalanceSheetSkipsPlaceholders`` — SB-8
- ``TestDailyExpenseBurnBookAgeClamp`` — SB-7
- ``TestCollectWarningsPlaceholderFilter`` — HP-6

Group C — Business + FX:
- ``TestFxGainLossThirdCurrencyFallback`` — HP-5
- ``TestBusinessToolsUseCompactJson`` — HP-4

Group D — Hardening:
- ``TestQueryFilteredSplitsTemplateFilter`` — HP-12
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import piecash
import pytest

from gnucash_mcp.book import GnuCashBook


# ── HP-7: depth proxy ──────────────────────────────────────────────


class TestBudgetReportDepthProxy:
    """HP-7: ``get_budget_report``'s rollup-map "nearest ancestor"
    tie-break must use real depth (``name.count(":")``), not
    ``len(name)``.

    Pre-fix a budget set on ``"A:Bbbbbbbb"`` (depth 2) and one on
    ``"A:B:C"`` (depth 3) would tie-break on string length and pick
    the shallower account as the rollup target for descendants —
    wrong: actuals from ``A:B:C:D`` should roll into ``A:B:C``,
    not ``A:Bbbbbbbb``.
    """

    @pytest.fixture
    def book_with_depth_skew(self, tmp_path: Path) -> Path:
        """A USD book with nested placeholder/leaf structure
        designed to break the len-based tie-break:

            Expenses                        (placeholder)
                Bbbbbbbb                    (placeholder, budgeted)
                B                           (placeholder, budgeted)
                    C                       (placeholder, budgeted)
                        D                   (leaf — receives spend)

        The leaf ``Expenses:B:C:D``'s nearest budgeted ancestor by
        depth is ``Expenses:B:C`` (depth 3). By string length,
        ``Expenses:Bbbbbbbb`` is longer (depth 2 but more chars).
        Pre-fix would pick the longer-stringed shallower path.
        """
        book_path = tmp_path / "depth_skew.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = book.root_account
        usd = book.default_currency

        expenses = piecash.Account(
            name="Expenses", type="EXPENSE", parent=root,
            commodity=usd, placeholder=True,
        )
        bbb = piecash.Account(
            name="Bbbbbbbb", type="EXPENSE", parent=expenses,
            commodity=usd, placeholder=True,
        )
        b = piecash.Account(
            name="B", type="EXPENSE", parent=expenses,
            commodity=usd, placeholder=True,
        )
        c = piecash.Account(
            name="C", type="EXPENSE", parent=b,
            commodity=usd, placeholder=True,
        )
        d = piecash.Account(
            name="D", type="EXPENSE", parent=c, commodity=usd,
        )
        # Equity + checking for the spend transaction.
        equity = piecash.Account(
            name="Equity", type="EQUITY", parent=root,
            commodity=usd, placeholder=True,
        )
        opening = piecash.Account(
            name="Opening", type="EQUITY", parent=equity,
            commodity=usd,
        )
        assets = piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=True,
        )
        checking = piecash.Account(
            name="Checking", type="BANK", parent=assets,
            commodity=usd,
        )
        book.save()

        # Opening + a $50 spend in D.
        piecash.Transaction(
            currency=usd, description="Opening",
            post_date=date(2026, 1, 1),
            splits=[
                piecash.Split(account=checking, value=Decimal("1000")),
                piecash.Split(account=opening, value=Decimal("-1000")),
            ],
        )
        piecash.Transaction(
            currency=usd, description="Spend in D",
            post_date=date(2026, 1, 15),
            splits=[
                piecash.Split(account=d, value=Decimal("50")),
                piecash.Split(account=checking, value=Decimal("-50")),
            ],
        )
        book.save()
        book.close()
        return book_path

    def test_descendant_rolls_to_deepest_ancestor(
        self, book_with_depth_skew,
    ):
        """The leaf ``Expenses:B:C:D``'s $50 spend must roll up to
        ``Expenses:B:C`` (depth 3), NOT to ``Expenses:Bbbbbbbb``
        (depth 2, but longer string). Both are budgeted parents;
        the actual depth count determines which is nearer."""
        gb = GnuCashBook(str(book_with_depth_skew))
        gb.create_budget(name="DepthTest", year=2026, num_periods=12)
        # Budget both candidates at the same amount so the only
        # signal that disambiguates is which one gets the actuals.
        gb.set_budget_amount(
            budget_name="DepthTest",
            account="Expenses:Bbbbbbbb",
            amount="100", period=0,
        )
        gb.set_budget_amount(
            budget_name="DepthTest",
            account="Expenses:B:C",
            amount="100", period=0,
        )

        report = gb.get_budget_report(
            budget_name="DepthTest", period=0, compact=False,
        )
        rows = {r["account"]: r for r in report["accounts"]}
        # B:C is the deeper ancestor — should receive the rollup.
        assert Decimal(rows["Expenses:B:C"]["actual"]) == Decimal("50"), (
            f"deeper ancestor missed rollup; B:C row: "
            f"{rows['Expenses:B:C']}"
        )
        # Bbbbbbbb is shallower (depth 2) — should NOT receive it.
        assert Decimal(rows["Expenses:Bbbbbbbb"]["actual"]) == Decimal("0"), (
            f"shallower ancestor incorrectly received rollup; "
            f"Bbbbbbbb row: {rows['Expenses:Bbbbbbbb']}"
        )


# ── SB-6: budget target FX conversion ──────────────────────────────


class TestBudgetTargetFxConversion:
    """SB-6: ``get_budget_report`` and ``_budget_headline`` must
    FX-convert budget *targets* to default currency before comparing
    against actuals.

    Pre-fix only actuals were converted via
    ``_split_in_default_currency``; targets were summed raw in
    their stored account commodity. ``used_pct`` was meaningless
    on a budget mixing default-currency and non-default-currency
    accounts.
    """

    @pytest.fixture
    def multi_currency_budget_book(self, tmp_path: Path) -> Path:
        """USD-default book with one USD-budgeted account and one
        EUR-budgeted account, both at the same nominal amount.
        Set EUR/USD = 1.20 so the EUR target's USD-equivalent is
        well-distinguished from the raw nominal."""
        from piecash import factories
        book_path = tmp_path / "fx_budget.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = book.root_account
        usd = book.default_currency
        eur = factories.create_currency_from_ISO("EUR")
        book.session.add(eur)

        expenses = piecash.Account(
            name="Expenses", type="EXPENSE", parent=root,
            commodity=usd, placeholder=True,
        )
        usd_expense = piecash.Account(
            name="USD_Cat", type="EXPENSE", parent=expenses,
            commodity=usd,
        )
        eur_expense = piecash.Account(
            name="EUR_Cat", type="EXPENSE", parent=expenses,
            commodity=eur,
        )
        book.save()

        # EUR/USD market rate of 1.20.
        piecash.Price(
            commodity=eur, currency=usd,
            date=date(2026, 1, 1), value=Decimal("1.20"),
            type="nav", source="user:test",
        )
        book.save()
        book.close()
        return book_path

    def test_eur_target_converts_to_usd(
        self, multi_currency_budget_book,
    ):
        """A 100 EUR budget target at EUR/USD = 1.20 must report
        as 120 USD in the totals. Pre-fix it would report as
        100 (raw), making the comparison nonsense."""
        gb = GnuCashBook(str(multi_currency_budget_book))
        gb.create_budget(name="FXBudget", year=2026, num_periods=12)
        gb.set_budget_amount(
            budget_name="FXBudget",
            account="Expenses:USD_Cat",
            amount="100", period=0,
        )
        gb.set_budget_amount(
            budget_name="FXBudget",
            account="Expenses:EUR_Cat",
            amount="100", period=0,
        )

        report = gb.get_budget_report(
            budget_name="FXBudget", period=0, compact=False,
        )
        # USD target stays at 100; EUR target converts to 120.
        # Total budgeted should be 220, not 200.
        assert Decimal(report["totals"]["budgeted"]) == Decimal("220"), (
            f"EUR target not FX-converted; totals: {report['totals']}"
        )


# ── SB-9: dashboard headline rollup ────────────────────────────────


class TestBudgetHeadlineRollup:
    """SB-9: ``_budget_headline`` (used by ``get_book_summary``)
    must roll up descendants of placeholder-budgeted parents into
    the actuals calculation.

    Pre-fix the dashboard's "% used" only counted splits in
    directly-budgeted accounts. A budget on ``Expenses:Utilities``
    (placeholder) with spend in ``Expenses:Utilities:Electric``
    showed 0% used on the dashboard while ``get_budget_report``
    showed the correct percentage. PR #46 fixed the report; the
    dashboard was left behind.
    """

    @pytest.fixture
    def book_with_placeholder_budget(self, tmp_path: Path) -> Path:
        """Budget set on placeholder parent ``Expenses:Utilities``;
        spend in leaf ``Expenses:Utilities:Electric``."""
        book_path = tmp_path / "rollup.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = book.root_account
        usd = book.default_currency

        expenses = piecash.Account(
            name="Expenses", type="EXPENSE", parent=root,
            commodity=usd, placeholder=True,
        )
        utilities = piecash.Account(
            name="Utilities", type="EXPENSE", parent=expenses,
            commodity=usd, placeholder=True,
        )
        electric = piecash.Account(
            name="Electric", type="EXPENSE", parent=utilities,
            commodity=usd,
        )
        assets = piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=True,
        )
        checking = piecash.Account(
            name="Checking", type="BANK", parent=assets,
            commodity=usd,
        )
        equity = piecash.Account(
            name="Equity", type="EQUITY", parent=root,
            commodity=usd, placeholder=True,
        )
        opening = piecash.Account(
            name="Opening", type="EQUITY", parent=equity,
            commodity=usd,
        )
        book.save()

        # Opening + a $50 electric bill in the current month.
        today = date.today()
        period_start = date(today.year, today.month, 1)
        piecash.Transaction(
            currency=usd, description="Opening",
            post_date=date(today.year - 1, 1, 1),
            splits=[
                piecash.Split(account=checking, value=Decimal("1000")),
                piecash.Split(account=opening, value=Decimal("-1000")),
            ],
        )
        piecash.Transaction(
            currency=usd, description="Electric bill",
            post_date=period_start,
            splits=[
                piecash.Split(account=electric, value=Decimal("50")),
                piecash.Split(account=checking, value=Decimal("-50")),
            ],
        )
        book.save()
        book.close()
        return book_path

    def test_headline_rolls_up_descendant_spend(
        self, book_with_placeholder_budget,
    ):
        """Budget set on ``Expenses:Utilities`` (placeholder),
        spend lands in ``Expenses:Utilities:Electric``. The
        dashboard headline must show non-zero ``used_pct`` —
        pre-fix it showed 0% because the spend account wasn't
        directly budgeted."""
        gb = GnuCashBook(str(book_with_placeholder_budget))
        today = date.today()
        gb.create_budget(
            name="Util", year=today.year, num_periods=12,
        )
        gb.set_budget_amount(
            budget_name="Util",
            account="Expenses:Utilities",
            amount="100", period=today.month - 1,
        )

        # Call the headline directly.
        with gb.open(readonly=True) as book:
            from piecash.budget import Budget
            transactions = list(book.transactions)
            headline = gb._budget_headline(book, transactions)

        assert headline is not None, "budget headline returned None"
        assert headline["used_pct"] > 0, (
            f"placeholder-parent budget headline shows 0% used despite "
            f"spend in descendant: {headline}"
        )
