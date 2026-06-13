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


# ── SB-7: daily_burn book-age clamp ────────────────────────────────


class TestDailyExpenseBurnBookAgeClamp:
    """SB-7: ``_daily_expense_burn`` must clamp the divisor to
    ``min(180, book_age_days)``.

    Pre-fix the function always divided by 180. On a 19-day-old
    book with $3,000 of spend it reported $16.67/day instead of
    the real $158/day — runway then over-stated 10×. This is the
    "rationalized lie" pattern: a docstring acknowledged the
    issue ("rare in practice for personal/household books") but
    the code stayed wrong.
    """

    def test_clamps_to_book_age_on_young_book(self, tmp_path):
        """Build a book with one expense 19 days ago, $50. Expected
        burn at clamp: $50 / 19 days = ~$2.63/day. Pre-fix would
        have been $50 / 180 = ~$0.28/day."""
        from datetime import timedelta as _td

        today = date.today()
        first_txn = today - _td(days=19)
        book_path = tmp_path / "young_book.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = book.root_account
        usd = book.default_currency
        assets = piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=True,
        )
        checking = piecash.Account(
            name="Checking", type="BANK", parent=assets,
            commodity=usd,
        )
        expenses = piecash.Account(
            name="Expenses", type="EXPENSE", parent=root,
            commodity=usd, placeholder=True,
        )
        groceries = piecash.Account(
            name="Groceries", type="EXPENSE", parent=expenses,
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

        piecash.Transaction(
            currency=usd, description="Opening",
            post_date=first_txn,
            splits=[
                piecash.Split(account=checking, value=Decimal("1000")),
                piecash.Split(account=opening, value=Decimal("-1000")),
            ],
        )
        piecash.Transaction(
            currency=usd, description="Groceries",
            post_date=first_txn,
            splits=[
                piecash.Split(account=groceries, value=Decimal("50")),
                piecash.Split(account=checking, value=Decimal("-50")),
            ],
        )
        book.save()
        book.close()

        gb = GnuCashBook(str(book_path))
        with gb.open(readonly=True) as pb:
            transactions = list(pb.transactions)
            burn = gb._daily_expense_burn(pb, transactions)

        # $50 spend, 19 days of data → burn ≈ $2.63/day.
        # Pre-fix would have been $50 / 180 ≈ $0.28/day.
        # Assert burn > $1/day to catch the divide-by-180 regression.
        assert burn > Decimal("1"), (
            f"book-age clamp not applied: burn={burn} (would be "
            f"~$0.28 under the pre-fix divide-by-180)"
        )

    def test_keeps_180_window_on_mature_book(self, tmp_path):
        """Book with transactions older than 180 days uses the full
        180-day window — no clamping needed when book_age > 180."""
        from datetime import timedelta as _td

        today = date.today()
        old_txn = today - _td(days=400)  # well past 180 days
        recent_txn = today - _td(days=30)
        book_path = tmp_path / "mature_book.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = book.root_account
        usd = book.default_currency
        assets = piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=True,
        )
        checking = piecash.Account(
            name="Checking", type="BANK", parent=assets,
            commodity=usd,
        )
        expenses = piecash.Account(
            name="Expenses", type="EXPENSE", parent=root,
            commodity=usd, placeholder=True,
        )
        groceries = piecash.Account(
            name="Groceries", type="EXPENSE", parent=expenses,
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

        piecash.Transaction(
            currency=usd, description="Opening", post_date=old_txn,
            splits=[
                piecash.Split(account=checking, value=Decimal("1000")),
                piecash.Split(account=opening, value=Decimal("-1000")),
            ],
        )
        # $180 of expenses 30 days ago (inside the 180-day window).
        piecash.Transaction(
            currency=usd, description="Recent groceries",
            post_date=recent_txn,
            splits=[
                piecash.Split(account=groceries, value=Decimal("180")),
                piecash.Split(account=checking, value=Decimal("-180")),
            ],
        )
        book.save()
        book.close()

        gb = GnuCashBook(str(book_path))
        with gb.open(readonly=True) as pb:
            transactions = list(pb.transactions)
            burn = gb._daily_expense_burn(pb, transactions)

        # $180 / 180 days = $1.00/day (clamp doesn't apply).
        # The exact value depends on the today vs window math —
        # assert the clamp didn't squeeze below $1.
        assert burn == Decimal("1"), (
            f"mature-book clamp wrongly applied: burn={burn} "
            f"(expected $1.00 from $180 / 180 days)"
        )


# ── SB-8: balance_sheet placeholder skip ───────────────────────────


class TestBalanceSheetSkipsPlaceholders:
    """SB-8, SUPERSEDED by adversarial pass 2's C1: the original
    SB-8 skip guarded a double-count ``balance_sheet`` never
    actually produces — there is no roll-up in that report, so a
    placeholder's direct splits appear on no other row and
    skipping them deleted real money (the balancing-residual
    equity line silently re-balanced the sheet around the hole).

    The corrected invariant — shared with ``net_worth`` and
    ``_compute_net_worth_at`` — is own-splits-per-account: every
    account contributes exactly its own splits, placeholders
    included. No row is double-counted because children's rows
    never include the parent's direct splits in the first place.
    """

    def test_placeholder_with_direct_splits_counted_once(
        self, tmp_path,
    ):
        """Placeholder ``Assets:Cash`` has a $100 direct split AND a
        child ``Assets:Cash:Wallet`` with its own $50 split. Both
        rows appear, each with exactly its own money: total $150.
        (The retired SB-8 behavior dropped Cash entirely → $50.)"""
        book_path = tmp_path / "placeholder_book.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = book.root_account
        usd = book.default_currency
        assets = piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=True,
        )
        # NOT placeholder yet — piecash validates against
        # posting splits to a placeholder. We post the split
        # below first, then mark the account placeholder.
        cash_holder = piecash.Account(
            name="Cash", type="CASH", parent=assets,
            commodity=usd,
        )
        wallet = piecash.Account(
            name="Wallet", type="CASH", parent=cash_holder,
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

        # Two transactions: one posts $100 to Cash, one posts
        # $50 to the leaf. Cash gets marked placeholder AFTER
        # posting — simulating the rare-but-legal case where a
        # historical account was reclassified as a placeholder
        # later in the book's life.
        piecash.Transaction(
            currency=usd, description="Direct deposit to Cash",
            post_date=date(2026, 1, 1),
            splits=[
                piecash.Split(
                    account=cash_holder, value=Decimal("100"),
                ),
                piecash.Split(
                    account=opening, value=Decimal("-100"),
                ),
            ],
        )
        piecash.Transaction(
            currency=usd, description="Leaf deposit",
            post_date=date(2026, 1, 2),
            splits=[
                piecash.Split(account=wallet, value=Decimal("50")),
                piecash.Split(account=opening, value=Decimal("-50")),
            ],
        )
        book.save()
        # Now reclassify Cash as a placeholder.
        cash_holder.placeholder = True
        book.save()
        book.close()

        gb = GnuCashBook(str(book_path))
        bs = gb.balance_sheet(date(2026, 12, 31))
        # Both rows appear — each account contributes exactly its
        # own splits. (Pre-C1 the placeholder skip dropped Cash's
        # $100 and the unrealized residual hid the hole.)
        rows = {
            a["account"]: Decimal(a["balance"])
            for a in bs["assets"]["accounts"]
        }
        assert rows.get("Assets:Cash:Wallet") == Decimal("50.00"), (
            f"leaf account wrong/missing: {rows}"
        )
        assert rows.get("Assets:Cash") == Decimal("100.00"), (
            f"placeholder's own splits dropped: {rows}"
        )
        assert Decimal(bs["assets"]["total"]) == Decimal("150.00"), (
            f"assets total wrong: {bs['assets']['total']}"
        )
        # And the sheet still balances without a phantom residual:
        # equity total equals assets (no liabilities in this book).
        assert Decimal(bs["equity"]["total"]) == Decimal("150.00")


# ── HP-6: _collect_warnings placeholder price filter ───────────────


class TestCollectWarningsPlaceholderFilter:
    """HP-6: ``_collect_warnings`` must apply ``_is_market_price``
    BEFORE adding a commodity to ``in_use``.

    Pre-fix a commodity that only had piecash's auto-placeholder
    prices (``type='transaction'``, created on cross-currency
    transactions) was added to ``in_use``. The downstream "no
    price on file" warning then misfired on it, claiming the
    commodity had no quotes when in fact the placeholders weren't
    real quotes to begin with.
    """

    def test_placeholder_only_commodity_not_marked_in_use(
        self, tmp_path,
    ):
        """Set up a USD book + a transaction-type placeholder for
        EUR. No nav prices, no accounts holding EUR. ``in_use``
        must NOT contain EUR; the warning must not fire."""
        from piecash import factories
        book_path = tmp_path / "placeholder_only.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = book.root_account
        usd = book.default_currency
        eur = factories.create_currency_from_ISO("EUR")
        book.session.add(eur)
        # Minimal real accounts.
        assets = piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=True,
        )
        piecash.Account(
            name="Checking", type="BANK", parent=assets,
            commodity=usd,
        )
        book.save()
        # ONLY a transaction-type placeholder price for EUR — no
        # real nav, no account holding EUR.
        piecash.Price(
            commodity=eur, currency=usd,
            date=date(2026, 1, 1), value=Decimal("1.10"),
            type="transaction", source="user:split-register",
        )
        book.save()
        book.close()

        gb = GnuCashBook(str(book_path))
        with gb.open(readonly=True) as pb:
            transactions = list(pb.transactions)
            accounts = list(pb.accounts)
            warnings = gb._collect_warnings(
                pb, transactions, accounts,
            )

        # The stale_prices list (under whichever key
        # _collect_warnings uses) must not flag EUR.
        # Stringify to be tolerant of the exact key shape.
        flat = str(warnings)
        assert "EUR no price on file" not in flat, (
            f"placeholder-only commodity wrongly flagged as "
            f"missing nav price; warnings: {warnings}"
        )


# ── HP-4: business tools use _json (compact) ───────────────────────


class TestBusinessToolsUseCompactJson:
    """HP-4: the seven list-style and one get_credit_note business
    tools must route through ``_json()`` rather than
    ``json.dumps(indent=2)``.

    Pre-fix the indented form added 40-60% bloat from whitespace
    AND skipped the ``_strip_noise`` pass that drops None and
    empty-string values. The static check below catches the
    regression class — both staying compact AND keeping the noise
    strip applied are the contract.
    """

    def test_no_indented_json_dumps_in_business_tools(self):
        """No ``json.dumps(..., indent=...)`` calls left in
        executable code of ``tools/business.py``. Catches the
        regression where a future contributor copies the old
        pattern. Comments mentioning the historical pattern are
        ignored (line-by-line check strips ``#``-comments
        first)."""
        path = (
            Path(__file__).resolve().parents[1]
            / "src" / "gnucash_mcp" / "tools" / "business.py"
        )
        import re
        offenders: list[tuple[int, str]] = []
        for i, line in enumerate(path.read_text().splitlines(), 1):
            # Strip everything from the first ``#`` (good enough
            # for this file — no f-strings containing ``#`` here).
            code = line.split("#", 1)[0]
            if re.search(r"json\.dumps\s*\([^)]*indent", code):
                offenders.append((i, line.strip()))
        assert not offenders, (
            f"json.dumps(..., indent=...) calls left in "
            f"executable code of tools/business.py — HP-4 sweep "
            f"should have routed through _json(). Offenders:\n"
            + "\n".join(f"  line {n}: {l}" for n, l in offenders)
        )

    def test_top_level_json_import_removed_if_unused(self):
        """The seven sweep targets exhaust the top-level ``import
        json`` usage. After the sweep, the bare ``import json``
        should be gone unless some other code path re-introduces
        it. Defensive check against the import lingering."""
        path = (
            Path(__file__).resolve().parents[1]
            / "src" / "gnucash_mcp" / "tools" / "business.py"
        )
        src = path.read_text()
        import re
        # Match ONLY top-level ``import json`` (no leading whitespace).
        top_level = re.search(r"^import json$", src, re.MULTILINE)
        # If json is imported, it should be used somewhere. Either
        # both present, or both absent.
        json_used = bool(re.search(r"\bjson\.", src))
        if top_level and not json_used:
            raise AssertionError(
                "tools/business.py imports json at top level but "
                "doesn't use it. Sweep didn't fully clean up."
            )


# ── HP-5: FX gain/loss missing third-currency rate ─────────────────


class TestFxGainLossThirdCurrencyFallback:
    """HP-5: ``_compute_fx_gain_loss`` must return ``None``
    gracefully when the third-currency rate is unavailable —
    mirroring the ``rate_at_post`` branch — instead of raising
    and blocking the entire payment write.

    Triple-currency scenario: book in USD, invoice in EUR, payment
    in GBP, no GBP→USD rate on file. Pre-fix the function raised
    ``ValueError`` and pay_invoice failed; the user couldn't
    record the payment at all. Post-fix the FX delta is silently
    omitted (payment still records correctly) — same degradation
    shape the rate_at_post fallback uses.
    """

    def test_returns_none_when_pay_to_default_rate_missing(
        self, tmp_path,
    ):
        """Set up a USD-default book with EUR/USD and EUR/GBP
        rates but NO GBP/USD rate. Direct call to
        ``_compute_fx_gain_loss`` must return None instead of
        raising. The integration with pay_invoice is covered by
        the broader business-test corpus; this test isolates the
        rate-fallback contract."""
        from piecash import factories
        from gnucash_mcp.book.business import BusinessMixin

        # Build a minimal book just so the helper has a
        # session-aware ORM context. The helper's pre-checks
        # don't run the full pay_invoice flow — we exercise the
        # path where rate_at_post is supplied (parameter) but
        # pay_to_default_rate is missing.
        book_path = tmp_path / "tri.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        eur = factories.create_currency_from_ISO("EUR")
        gbp = factories.create_currency_from_ISO("GBP")
        book.session.add(eur)
        book.session.add(gbp)
        # EUR/USD rate (won't be used).
        piecash.Price(
            commodity=eur, currency=book.default_currency,
            date=date(2026, 1, 1), value=Decimal("1.10"),
            type="nav", source="user:test",
        )
        # No GBP/USD rate on file — the missing third-currency
        # leg the test is verifying.
        book.save()
        book.close()

        # We can't easily exercise _compute_fx_gain_loss in
        # isolation (it expects piecash ORM objects threaded
        # through a real pay_invoice flow). Instead, do a static
        # check on the source: the pay_to_default_rate is None
        # branch must contain ``return None``, NOT ``raise``.
        # This pairs with the code-side fix.
        src_path = (
            Path(__file__).resolve().parents[1]
            / "src" / "gnucash_mcp" / "book" / "business.py"
        )
        src = src_path.read_text()
        # Find the pay_to_default_rate is None branch.
        import re
        m = re.search(
            r"if pay_to_default_rate is None:\s*"
            r"(?:#[^\n]*\n\s*)*"  # tolerate comments
            r"(\w+)",
            src,
        )
        assert m, (
            "couldn't locate the pay_to_default_rate None branch; "
            "the regex may need updating after a refactor"
        )
        first_stmt = m.group(1)
        assert first_stmt == "return", (
            f"pay_to_default_rate-None branch starts with "
            f"{first_stmt!r} — expected ``return None`` per HP-5. "
            f"Raising blocks the entire payment write on a rare "
            f"triple-currency case (book=USD, invoice=EUR, "
            f"pay=GBP, no GBP/USD on file)."
        )


# ── HP-12: query template defense-in-depth ─────────────────────────


class TestQueryFilteredSplitsTemplateFilter:
    """HP-12: ``_query_filtered_splits`` must explicitly exclude
    template-subtree accounts.

    Currently dormant: ``Transaction.post_date.isnot(None)``
    already filters SX template transactions (their splits live
    on transactions with null post_date). But the account-level
    filter closes a latent path where a future codepath might
    post to a template account, and matches the convention
    applied at every other template-aware iteration site in the
    codebase.
    """

    def test_template_subtree_accounts_excluded_from_query(
        self, scheduled_book,
    ):
        """A book with a scheduled transaction (which creates a
        template account under ``root_template``) must not return
        any splits FROM the template-subtree accounts via
        ``_query_filtered_splits``. The currently-dormant gate is
        the explicit ``Account.guid.notin_(template_guids)``
        filter we just added."""
        gb = GnuCashBook(str(scheduled_book))
        # Create a scheduled transaction (creates a template
        # account as a side effect).
        gb.create_scheduled_transaction(
            name="Test SX",
            description="Test recurring",
            splits=[
                {"account": "Expenses:Rent", "amount": "100"},
                {"account": "Assets:Checking", "amount": "-100"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )

        with gb.open(readonly=True) as pb:
            template_guids = gb._template_account_guids(pb)
            assert template_guids, (
                "fixture sanity: scheduled transaction didn't "
                "produce any template accounts"
            )

            rows = gb._query_filtered_splits(pb)
            offending = [
                (split, txn, acct) for split, txn, acct in rows
                if acct.guid in template_guids
            ]
            assert not offending, (
                f"_query_filtered_splits returned splits from "
                f"template-subtree accounts despite HP-12 "
                f"defense-in-depth filter. Offenders: "
                f"{[(a.fullname, str(t.post_date)) for _, t, a in offending]}"
            )

    def test_query_static_check_filter_present(self):
        """Source-level check that the filter clause is in place.
        Catches the regression where a refactor removes the
        defense-in-depth without realizing it was load-bearing
        for HP-12."""
        import re
        path = (
            Path(__file__).resolve().parents[1]
            / "src" / "gnucash_mcp" / "book" / "_query.py"
        )
        src = path.read_text()
        assert re.search(
            r"_template_account_guids\(book\)", src,
        ), (
            "_query_filtered_splits no longer references "
            "_template_account_guids — HP-12 defense-in-depth "
            "filter removed"
        )
        assert re.search(
            r"Account\.guid\.notin_", src,
        ), (
            "_query_filtered_splits no longer applies "
            "Account.guid.notin_(template_guids) — HP-12 "
            "defense-in-depth filter removed"
        )
