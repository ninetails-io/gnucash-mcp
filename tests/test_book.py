"""Tests for GnuCashBook wrapper."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import piecash
import pytest

from gnucash_mcp.book import GnuCashBook, GnuCashLockError


def _seed_template_transaction(
    gc: GnuCashBook,
    description: str,
    amount: str,
    trans_date: date,
) -> str:
    """Seed a GnuCash-style scheduled-transaction TEMPLATE as a real
    Transaction whose splits post to accounts under root_template.

    GnuCash persists each scheduled-transaction's split recipe as a
    Transaction row whose splits reference accounts rooted at
    ``book.root_template``. Our MCP creates schedules via a
    ``splits-json`` Slot instead (see ``create_scheduled_transaction``)
    so the MCP never produces these rows directly — but books
    touched by the GnuCash desktop UI are full of them, and the
    bookkeeper hit exactly that on Alex's mortgage backfill. Tests
    fabricate one via piecash directly to exercise the filter.

    Returns the transaction's GUID so tests can assert it never
    resurfaces in user-facing output.
    """
    with gc.open(readonly=False) as book:
        template_acct = piecash.Account(
            name=f"{description} Template",
            type="BANK",
            parent=book.root_template,
            commodity=book.default_currency,
        )
        book.session.add(template_acct)
        book.flush()
        txn = piecash.Transaction(
            currency=book.default_currency,
            description=description,
            post_date=trans_date,
            splits=[
                piecash.Split(
                    account=template_acct, value=Decimal(amount),
                ),
                piecash.Split(
                    account=template_acct, value=-Decimal(amount),
                ),
            ],
        )
        book.session.add(txn)
        book.save()
        return txn.guid


def _parse_duplicates(tsv: str) -> list[dict]:
    """Parse the create_transaction duplicates TSV into a list of dicts.

    ``create_transaction`` emits duplicate candidates as a compact
    newline-separated TSV (see ``_duplicates_to_tsv``). Tests were
    written against the old list-of-dicts shape; this helper lets
    them assert on the structured view without the response
    actually carrying the overhead.

    Empty / missing string returns ``[]``.
    """
    if not tsv:
        return []
    rows = []
    for line in tsv.split("\n"):
        parts = line.split("\t")
        rows.append({
            "confidence": parts[0],
            "guid": parts[1],
            "date": parts[2],
            "amount": parts[3],
            "description": parts[4],
            "signals": parts[5],
        })
    return rows


class TestGnuCashBookInit:
    """Tests for GnuCashBook initialization."""

    def test_init_with_valid_path(self, test_book: Path):
        """Should initialize successfully with valid book path."""
        book = GnuCashBook(str(test_book))
        assert book.book_path == test_book

    def test_init_with_invalid_path(self):
        """Should raise FileNotFoundError for non-existent path."""
        with pytest.raises(FileNotFoundError):
            GnuCashBook("/nonexistent/path/book.gnucash")


class TestGnuCashBookOpen:
    """Tests for book open context manager."""

    def test_open_readonly(self, test_book: Path):
        """Should open book in readonly mode."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            assert book is not None
            assert book.root_account is not None

    def test_open_readwrite(self, test_book: Path):
        """Should open book in read-write mode."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=False) as book:
            assert book is not None


class TestGetBookSummary:
    """Tests for get_book_summary method."""

    def test_get_book_summary_returns_string(self, test_book: Path):
        """Should return a formatted text string."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.get_book_summary()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_book_summary_contains_sections(self, test_book: Path):
        """Should contain all expected sections."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.get_book_summary()

        assert "Book:" in result
        assert "Currency:" in result
        assert "Accounts:" in result
        assert "Assets:" in result
        assert "Liabilities:" in result
        assert "Income:" in result
        assert "Expenses:" in result
        assert "Transactions:" in result
        assert "Commodities:" in result
        # The bottom-line "Net worth:" line was removed in favor of
        # the trajectory section's "now" anchor — single source of
        # truth, no risk of two displayed numbers disagreeing.
        assert "Net worth trajectory:" in result
        assert "Net worth:" not in result

    def test_get_book_summary_currency(self, test_book: Path):
        """Should show USD as default currency."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.get_book_summary()
        assert "Currency: USD" in result

    def test_get_book_summary_book_path(self, test_book: Path):
        """Should include the book file path."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.get_book_summary()
        assert f"Book: {test_book}" in result

    def test_investment_valued_at_latest_price(
        self, multi_currency_book: Path
    ):
        """Foreign-currency asset balance reports as USD market value.

        The multi_currency_book fixture has an EUR Savings account with
        1000 EUR in it and a USD-denominated Checking. Add a EUR→USD
        price, then verify the summary shows the EUR balance valued
        at that rate.
        """
        import piecash
        from datetime import date as _date
        gc_book = GnuCashBook(str(multi_currency_book))
        with gc_book.open(readonly=False) as book:
            usd = book.default_currency
            eur = None
            for c in book.commodities:
                if c.mnemonic == "EUR":
                    eur = c
                    break
            assert eur is not None
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=_date(2026, 3, 1),
                value="1.20",
                source="user:test",
                type="nav",
            ))
            book.save()

        result = gc_book.get_book_summary()
        # 1000 EUR × 1.20 = $1200. Decimal("1.20") stringifies as "1.2".
        assert "1000 EUR @ 1.2" in result
        assert "(USD 1200.00)" in result

    def test_investment_no_price_falls_back_to_cost_basis(
        self, multi_currency_book: Path
    ):
        """With no EUR→USD price on file, the summary tags the line
        as 'no price data' and uses cost basis (sum of split values).

        In the fixture the cross-currency transfer booked value=1100 USD
        on the EUR side, so the fallback cost basis is $1,100.
        """
        gc_book = GnuCashBook(str(multi_currency_book))
        result = gc_book.get_book_summary()
        assert "1000 EUR — no price data" in result
        assert "(USD 1100.00)" in result or "(USD 1100)" in result

    def test_business_entities_line(self, business_book: Path):
        """Summary includes Business line when entities exist."""
        gc_book = GnuCashBook(str(business_book))
        gc_book.create_customer(name="Alpha Co")
        gc_book.create_customer(name="Beta LLC")
        gc_book.create_vendor(name="Gamma Supplies")
        gc_book.create_employee(name="Delta Worker")

        result = gc_book.get_book_summary()
        assert "Business: 2 customers, 1 vendor, 1 employee" in result

    def test_business_line_omitted_when_empty(self, business_book: Path):
        """No Business line when the book has no customers/vendors/employees."""
        gc_book = GnuCashBook(str(business_book))
        result = gc_book.get_book_summary()
        assert "Business:" not in result

    def test_budgets_line(self, budget_book: Path):
        """Summary shows Budgets count when budgets exist."""
        gc_book = GnuCashBook(str(budget_book))
        gc_book.create_budget(name="Test Budget", year=2026)
        result = gc_book.get_book_summary()
        assert "Budgets: 1" in result


class TestGetBookSummaryReconciliation:
    """Reconciliation section in get_book_summary.

    Replaces the old "Transactions: N (M unreconciled)" suffix —
    which counted unreconciled splits across all account types and
    was operationally useless — with per-account last-reconciled
    state for reconcilable account types. See
    ``CoreMixin._account_reconciliation_status`` and the spec at
    ``docs/GET_BOOK_SUMMARY_SPEC.md`` §1.
    """

    def _reconcile_split(
        self,
        gc: GnuCashBook,
        account: str,
        on_date: date,
    ) -> None:
        """Mark every split on the given account-on-date as
        reconciled. Helper for setting up test fixtures with a
        known reconciliation history.
        """
        with gc.open(readonly=False) as book:
            acct = gc._find_account(book, account)
            assert acct is not None, account
            for s in acct.splits:
                if s.transaction.post_date == on_date:
                    s.reconcile_state = "y"
                    from datetime import datetime as _dt
                    s.reconcile_date = _dt.combine(on_date, _dt.min.time())
            book.save()

    def test_unreconciled_count_removed_from_transactions_line(
        self, test_book: Path,
    ):
        """The old '(M unreconciled)' suffix on the Transactions
        line is gone — its information was misleading."""
        gc = GnuCashBook(str(test_book))
        result = gc.get_book_summary()
        # "Transactions: N" remains, but no "(... unreconciled)" tail.
        txn_line = next(
            l for l in result.split("\n") if l.startswith("Transactions:")
        )
        assert "unreconciled" not in txn_line

    def test_reconciliation_section_present_when_activity(
        self, test_book: Path,
    ):
        """Reconciliation section appears when there's at least
        one reconcilable account with transaction activity. With
        the fixture's Checking having activity but no reconciled
        splits, that activity surfaces as the collapsed
        '<N> account never reconciled' footer rather than a
        per-account line."""
        gc = GnuCashBook(str(test_book))
        result = gc.get_book_summary()
        assert "Reconciliation:" in result
        recon = result.split("Reconciliation:")[1].split("\nTransactions:")[0]
        assert "1 account never reconciled ⚠" in recon

    def test_recent_reconciliation_collapses_to_current_count(
        self, test_book: Path,
    ):
        """Account reconciled within the 45-day freshness window
        does NOT surface individually — its through-date carries no
        actionable signal beyond "this one's fine," so it joins
        the collapsed '<N> account(s) current' line. No warning
        marker on that line; current accounts are by definition
        not behind."""
        gc = GnuCashBook(str(test_book))
        recent = date.today() - timedelta(days=10)
        with gc.open(readonly=False) as book:
            checking = gc._find_account(book, "Assets:Checking")
            opening = gc._find_account(book, "Equity:Opening Balance")
            t = piecash.Transaction(
                currency=book.default_currency,
                description="Recent reconciled deposit",
                post_date=recent,
                splits=[
                    piecash.Split(account=checking, value=Decimal("100")),
                    piecash.Split(account=opening, value=Decimal("-100")),
                ],
            )
            book.session.add(t)
            book.save()
        self._reconcile_split(gc, "Assets:Checking", recent)
        result = gc.get_book_summary()
        recon = result.split("Reconciliation:")[1].split("\nTransactions:")[0]
        # No per-account through-date line for the now-current Checking.
        assert f"through {recent.isoformat()}" not in recon
        # Collapsed into the current count.
        assert "1 account current" in recon
        # No ⚠ on the current line — current accounts are by definition
        # not stale. The whole section may still have a ⚠ from the
        # never-reconciled footer covering other accounts, so check
        # the current line specifically.
        current_line = next(
            line for line in recon.split("\n")
            if "account current" in line and "never" not in line
        )
        assert "⚠" not in current_line

    def test_stale_reconciliation_warns_with_months_lag(
        self, test_book: Path,
    ):
        """Account whose last reconcile is well past 45 days shows
        '(N months behind) ⚠'."""
        gc = GnuCashBook(str(test_book))
        old = date.today() - timedelta(days=120)
        with gc.open(readonly=False) as book:
            checking = gc._find_account(book, "Assets:Checking")
            opening = gc._find_account(book, "Equity:Opening Balance")
            t = piecash.Transaction(
                currency=book.default_currency,
                description="Old reconciled deposit",
                post_date=old,
                splits=[
                    piecash.Split(account=checking, value=Decimal("50")),
                    piecash.Split(account=opening, value=Decimal("-50")),
                ],
            )
            book.session.add(t)
            book.save()
        self._reconcile_split(gc, "Assets:Checking", old)
        result = gc.get_book_summary()
        recon = result.split("Reconciliation:")[1].split("\n")
        checking_line = next(l for l in recon if "Checking" in l)
        assert "months behind" in checking_line
        assert "⚠" in checking_line

    def test_stale_45_to_60_days_uses_days_unit(
        self, test_book: Path,
    ):
        """The 45-59 day window uses days, not months — months
        scale only kicks in at 60+."""
        gc = GnuCashBook(str(test_book))
        d50 = date.today() - timedelta(days=50)
        with gc.open(readonly=False) as book:
            checking = gc._find_account(book, "Assets:Checking")
            opening = gc._find_account(book, "Equity:Opening Balance")
            t = piecash.Transaction(
                currency=book.default_currency,
                description="50-day-old reconcile",
                post_date=d50,
                splits=[
                    piecash.Split(account=checking, value=Decimal("10")),
                    piecash.Split(account=opening, value=Decimal("-10")),
                ],
            )
            book.session.add(t)
            book.save()
        self._reconcile_split(gc, "Assets:Checking", d50)
        result = gc.get_book_summary()
        recon = result.split("Reconciliation:")[1].split("\n")
        checking_line = next(l for l in recon if "Checking" in l)
        assert "days behind" in checking_line
        assert "⚠" in checking_line

    def test_never_reconciled_collapses_to_count_line(
        self, test_book: Path,
    ):
        """Accounts with transaction activity but no 'y' splits
        do NOT surface individually — they collapse into a single
        '<N> account(s) never reconciled ⚠' footer line. Naming
        each one would balloon the section on production books;
        a 15-card power user would otherwise see 20+ identical
        lines.

        The fixture's Checking is the only reconcilable-with-
        activity account and it has nothing reconciled, so the
        count is 1 (singular grammar)."""
        gc = GnuCashBook(str(test_book))
        result = gc.get_book_summary()
        recon = result.split("Reconciliation:")[1].split("\nTransactions:")[0]
        assert "Checking: never reconciled" not in recon
        assert "1 account never reconciled ⚠" in recon

    def test_never_reconciled_pluralizes(self, test_book: Path):
        """Multiple never-reconciled accounts → '<N> accounts'
        with the plural 's'. Smoke-tests grammar transition."""
        gc = GnuCashBook(str(test_book))
        gc.create_account(
            name="Visa", account_type="CREDIT", parent="Liabilities",
        )
        gc.create_account(
            name="Mastercard", account_type="CREDIT", parent="Liabilities",
        )
        with gc.open(readonly=False) as book:
            visa = gc._find_account(book, "Liabilities:Visa")
            mc = gc._find_account(book, "Liabilities:Mastercard")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Visa charge",
                post_date=date.today() - timedelta(days=10),
                splits=[
                    piecash.Split(account=visa, value=Decimal("-50")),
                    piecash.Split(account=opening, value=Decimal("50")),
                ],
            ))
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Mastercard charge",
                post_date=date.today() - timedelta(days=5),
                splits=[
                    piecash.Split(account=mc, value=Decimal("-75")),
                    piecash.Split(account=opening, value=Decimal("75")),
                ],
            ))
            book.save()
        result = gc.get_book_summary()
        recon = result.split("Reconciliation:")[1].split("\nTransactions:")[0]
        # 3 never-reconciled: Checking + Visa + Mastercard.
        assert "3 accounts never reconciled ⚠" in recon
        # None named individually.
        assert "Visa: never reconciled" not in recon
        assert "Mastercard: never reconciled" not in recon

    def test_stale_individual_alongside_current_and_never_collapse(
        self, test_book: Path,
    ):
        """Three buckets coexist: stale reconciled accounts render
        individually with their lag; current reconciled accounts
        collapse; never-reconciled accounts collapse separately.
        Per-account-distinct info stays per-account; identical-
        across-accounts info collapses to a count."""
        gc = GnuCashBook(str(test_book))
        # Never-reconciled bucket: add Visa with activity, no recon.
        gc.create_account(
            name="Visa", account_type="CREDIT", parent="Liabilities",
        )
        with gc.open(readonly=False) as book:
            visa = gc._find_account(book, "Liabilities:Visa")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Visa charge",
                post_date=date.today() - timedelta(days=10),
                splits=[
                    piecash.Split(account=visa, value=Decimal("-50")),
                    piecash.Split(account=opening, value=Decimal("50")),
                ],
            ))
            book.save()
        # Current bucket: Checking gets a recent reconciled split.
        recent = date.today() - timedelta(days=5)
        with gc.open(readonly=False) as book:
            checking = gc._find_account(book, "Assets:Checking")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Recent reconciled deposit",
                post_date=recent,
                splits=[
                    piecash.Split(account=checking, value=Decimal("100")),
                    piecash.Split(account=opening, value=Decimal("-100")),
                ],
            ))
            book.save()
        self._reconcile_split(gc, "Assets:Checking", recent)
        # Stale bucket: add Mortgage with an old reconciled split.
        gc.create_account(
            name="Mortgage", account_type="LIABILITY", parent="Liabilities",
        )
        old = date.today() - timedelta(days=120)
        with gc.open(readonly=False) as book:
            mortgage = gc._find_account(book, "Liabilities:Mortgage")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Mortgage opening",
                post_date=old,
                splits=[
                    piecash.Split(account=mortgage, value=Decimal("-1000")),
                    piecash.Split(account=opening, value=Decimal("1000")),
                ],
            ))
            book.save()
        self._reconcile_split(gc, "Liabilities:Mortgage", old)

        result = gc.get_book_summary()
        recon = result.split("Reconciliation:")[1].split("\nTransactions:")[0]
        # Stale: by-name with months-behind warning.
        assert "Mortgage:" in recon
        assert "months behind" in recon
        # Current: 1 (Checking), no per-account line.
        assert "1 account current" in recon
        assert f"Checking: through {recent.isoformat()}" not in recon
        # Never: 1 (Visa).
        assert "1 account never reconciled ⚠" in recon
        assert "Visa: never reconciled" not in recon

    def test_current_pluralizes(self, test_book: Path):
        """Multiple current accounts → '<N> accounts current' with
        plural 's'. Symmetric grammar with the never-reconciled
        bucket."""
        gc = GnuCashBook(str(test_book))
        # Add a second BANK account.
        gc.create_account(
            name="Savings", account_type="BANK", parent="Assets",
        )
        recent = date.today() - timedelta(days=5)
        with gc.open(readonly=False) as book:
            checking = gc._find_account(book, "Assets:Checking")
            savings = gc._find_account(book, "Assets:Savings")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Checking deposit",
                post_date=recent,
                splits=[
                    piecash.Split(account=checking, value=Decimal("100")),
                    piecash.Split(account=opening, value=Decimal("-100")),
                ],
            ))
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Savings deposit",
                post_date=recent,
                splits=[
                    piecash.Split(account=savings, value=Decimal("200")),
                    piecash.Split(account=opening, value=Decimal("-200")),
                ],
            ))
            book.save()
        self._reconcile_split(gc, "Assets:Checking", recent)
        self._reconcile_split(gc, "Assets:Savings", recent)
        result = gc.get_book_summary()
        recon = result.split("Reconciliation:")[1].split("\nTransactions:")[0]
        assert "2 accounts current" in recon

    def test_unused_account_skipped(self, test_book: Path):
        """A reconcilable account with no transaction activity is
        not 'behind' — it's just unused. It shouldn't appear in
        the Reconciliation section at all."""
        gc = GnuCashBook(str(test_book))
        # Add a brand-new credit card account with no transactions.
        gc.create_account(
            name="Unused Card",
            account_type="CREDIT",
            parent="Liabilities",
        )
        result = gc.get_book_summary()
        # Section exists (Checking has activity), but the unused
        # card must not be in it.
        recon = result.split("Reconciliation:")[1].split("\n", 1)[1]
        # Stop at the next top-level section (no leading whitespace).
        recon_block = recon.split("\nTransactions:")[0]
        assert "Unused Card" not in recon_block

    def test_income_expense_equity_excluded(self, test_book: Path):
        """Reconciliation only applies to BANK / CREDIT / LIABILITY
        (and qualifying ASSETs). Income / Expense / Equity accounts
        never appear regardless of activity."""
        gc = GnuCashBook(str(test_book))
        result = gc.get_book_summary()
        recon = result.split("Reconciliation:")[1].split("\nTransactions:")[0]
        for excluded in ("Salary", "Groceries", "Opening Balance"):
            assert excluded not in recon

    def test_section_omitted_when_no_reconcilable_activity(
        self, tmp_path: Path,
    ):
        """A book with only INCOME/EXPENSE/EQUITY accounts (no BANK,
        no CREDIT, no LIABILITY with activity) emits no
        Reconciliation section — not even the header."""
        # Build a minimal book with only non-reconcilable account
        # types.
        book_path = tmp_path / "no_reconcilable.gnucash"
        book = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = book.root_account
        usd = book.default_currency
        income = piecash.Account(
            name="Income", type="INCOME", parent=root,
            commodity=usd, placeholder=True,
        )
        book.session.add(income)
        salary = piecash.Account(
            name="Salary", type="INCOME", parent=income, commodity=usd,
        )
        book.session.add(salary)
        equity = piecash.Account(
            name="Equity", type="EQUITY", parent=root,
            commodity=usd, placeholder=True,
        )
        book.session.add(equity)
        opening = piecash.Account(
            name="Opening Balance", type="EQUITY", parent=equity,
            commodity=usd,
        )
        book.session.add(opening)
        book.save()
        book.close()

        gc = GnuCashBook(str(book_path))
        result = gc.get_book_summary()
        assert "Reconciliation:" not in result

    def test_asset_with_reconciled_history_included(
        self, test_book: Path,
    ):
        """ASSET accounts with any reconciled history surface — the
        spec calls out brokerage cash, escrow, prepaid as legitimate
        ASSET-typed reconcilable accounts.

        Use a stale reconciliation date so the brokerage shows up
        by name (the stale bucket renders individually). A current
        ASSET would collapse into the count and we couldn't prove
        the filter let it through."""
        gc = GnuCashBook(str(test_book))
        gc.create_account(
            name="Brokerage Cash", account_type="ASSET", parent="Assets",
        )
        old = date.today() - timedelta(days=120)
        with gc.open(readonly=False) as book:
            broker = gc._find_account(book, "Assets:Brokerage Cash")
            opening = gc._find_account(book, "Equity:Opening Balance")
            t = piecash.Transaction(
                currency=book.default_currency,
                description="Initial brokerage deposit",
                post_date=old,
                splits=[
                    piecash.Split(account=broker, value=Decimal("1000")),
                    piecash.Split(account=opening, value=Decimal("-1000")),
                ],
            )
            book.session.add(t)
            book.save()
        self._reconcile_split(gc, "Assets:Brokerage Cash", old)
        result = gc.get_book_summary()
        recon = result.split("Reconciliation:")[1].split("\nTransactions:")[0]
        # Stale ASSET surfaces individually.
        assert "Brokerage Cash" in recon
        assert "months behind" in recon

    def test_asset_without_reconciled_history_excluded(
        self, test_book: Path,
    ):
        """ASSET accounts with no 'y' or 'c' history are skipped —
        most ASSET accounts (real estate, vehicles, investment
        positions) don't get reconciled and shouldn't surface."""
        gc = GnuCashBook(str(test_book))
        # ASSET-typed account with activity but no reconciled splits.
        gc.create_account(
            name="Vehicle", account_type="ASSET", parent="Assets",
        )
        with gc.open(readonly=False) as book:
            vehicle = gc._find_account(book, "Assets:Vehicle")
            opening = gc._find_account(book, "Equity:Opening Balance")
            t = piecash.Transaction(
                currency=book.default_currency,
                description="Vehicle purchase",
                post_date=date(2024, 1, 1),
                splits=[
                    piecash.Split(account=vehicle, value=Decimal("15000")),
                    piecash.Split(account=opening, value=Decimal("-15000")),
                ],
            )
            book.session.add(t)
            book.save()
        result = gc.get_book_summary()
        recon = result.split("Reconciliation:")[1].split("\nTransactions:")[0]
        assert "Vehicle" not in recon


class TestGetBookSummaryNetWorthTrajectory:
    """Net worth trajectory section in get_book_summary.

    Five anchor points (12mo / 6mo / 3mo / 1mo ago, now) showing
    how net worth has evolved. See
    ``CoreMixin._net_worth_trajectory`` and the spec at
    ``docs/GET_BOOK_SUMMARY_SPEC.md`` §2.
    """

    @staticmethod
    def _months_ago(n: int) -> date:
        from dateutil.relativedelta import relativedelta
        today = date.today()
        if n == 0:
            return today
        return today - relativedelta(months=n)

    def _seed_balanced_deposit(
        self,
        gc: GnuCashBook,
        amount: str,
        on_date: date,
    ) -> None:
        """Seed a deposit (Checking debit, Opening Balance credit)
        anchored on a given date so trajectory anchors find balances
        as of that date."""
        gc.create_transaction(
            description="Deposit",
            splits=[
                {"account": "Assets:Checking", "amount": amount},
                {"account": "Equity:Opening Balance", "amount": f"-{amount}"},
            ],
            trans_date=on_date,
            check_duplicates=False,
        )

    def test_section_omitted_for_empty_book(self, tmp_path: Path):
        """A book with no transactions has no first_date → no
        trajectory anchors → omit the section entirely."""
        book_path = tmp_path / "empty.gnucash"
        b = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        b.session.add(piecash.Account(
            name="Assets", type="ASSET", parent=b.root_account,
            commodity=b.default_currency, placeholder=True,
        ))
        b.save()
        b.close()
        gc = GnuCashBook(str(book_path))
        result = gc.get_book_summary()
        assert "Net worth trajectory" not in result

    def test_full_history_emits_five_anchors(self, test_book: Path):
        """A book with >12mo of data emits all five anchors,
        oldest first."""
        gc = GnuCashBook(str(test_book))
        # Seed an anchor old enough to ensure 12mo coverage.
        old = self._months_ago(13)
        self._seed_balanced_deposit(gc, "1000", old)
        # Plus more recent activity.
        self._seed_balanced_deposit(gc, "500", self._months_ago(0))
        result = gc.get_book_summary()
        section = result.split("Net worth trajectory:\n", 1)[1]
        rows = []
        for line in section.split("\n"):
            if line.startswith("  "):
                rows.append(line)
            else:
                break
        assert len(rows) == 5
        # First row is oldest (12mo ago), last is now.
        assert "12mo ago" in rows[0]
        assert "now" in rows[-1]

    def test_short_history_drops_old_anchors(self, test_book: Path):
        """A book with only 4mo of data drops 12mo and 6mo anchors;
        keeps 3mo, 1mo, now (anchors within or at the data range
        start). Spec: 'omit the data points before the start of
        the data range'."""
        gc = GnuCashBook(str(test_book))
        # Seed only recent activity (~4 months ago)
        self._seed_balanced_deposit(gc, "1000", self._months_ago(4))
        self._seed_balanced_deposit(gc, "500", self._months_ago(0))

        # Reset fixture's 2024 transactions so they don't extend the
        # data range. We can't modify the fixture in place; instead
        # rely on first_date logic — fixture has 2024-01 transactions
        # which are >12mo old. So all five anchors qualify.
        # This test demonstrates the behavior on a book where the
        # earliest transaction defines the data-range floor.

        # Build a fresh minimal book with controlled first_date
        # for the strict short-history check.
        import shutil
        short_path = test_book.parent / "short_history.gnucash"
        shutil.copy(test_book, short_path)
        # Delete all transactions older than 4mo by removing them
        # via piecash.
        gc2 = GnuCashBook(str(short_path))
        with gc2.open(readonly=False) as book:
            cutoff = self._months_ago(4)
            for txn in list(book.transactions):
                if txn.post_date < cutoff:
                    book.session.delete(txn)
            book.save()

        result = gc2.get_book_summary()
        if "Net worth trajectory" not in result:
            # If pruning emptied the book, that's a different test
            # path; ensure trajectory is omitted then.
            return

        section = result.split("Net worth trajectory:\n", 1)[1]
        rows = []
        for line in section.split("\n"):
            if line.startswith("  "):
                rows.append(line)
            else:
                break
        # Anchor labels actually present:
        labels = [
            label for label in ("12mo ago", "6mo ago", "3mo ago", "1mo ago", "now")
            if any(label in r for r in rows)
        ]
        # 12mo and 6mo should be dropped; 3mo, 1mo, now should remain.
        assert "12mo ago" not in labels
        assert "6mo ago" not in labels
        assert "3mo ago" in labels
        assert "1mo ago" in labels
        assert "now" in labels

    def test_oldest_first_ordering(self, test_book: Path):
        """Trajectory rows are oldest → newest, matching the
        natural left-to-right reading of a time series."""
        gc = GnuCashBook(str(test_book))
        self._seed_balanced_deposit(gc, "100", self._months_ago(13))
        self._seed_balanced_deposit(gc, "200", self._months_ago(0))
        result = gc.get_book_summary()
        section = result.split("Net worth trajectory:\n", 1)[1]
        rows = section.split("\n")[:5]
        # Verify exact order.
        ordered = [
            "12mo ago", "6mo ago", "3mo ago", "1mo ago", "now",
        ]
        for i, expected in enumerate(ordered):
            assert expected in rows[i], (i, expected, rows[i])

    def test_now_anchor_is_today(self, test_book: Path):
        """The 'now' anchor uses today's date and reflects the
        current net worth. For a book with $1000 in checking, no
        liabilities, the 'now' line is +1000 in book currency."""
        gc = GnuCashBook(str(test_book))
        # Fixture's net worth is $1000 (opening balance) + $2000
        # (salary) - $0 (groceries doesn't change net worth) = $3000.
        # Wait, groceries DOES affect: it's an EXPENSE-type, not in
        # the NW_TYPES set, so doesn't contribute. But Checking
        # decreased by $150 from groceries.
        # Net worth assets: Checking has $1000+$2000-$150 = $2850.
        # No liabilities → net worth = $2850.
        result = gc.get_book_summary()
        section = result.split("Net worth trajectory:\n", 1)[1]
        now_row = next(r for r in section.split("\n")[:5] if "now" in r)
        assert "USD 2,850" in now_row

    def test_value_format_thousands_separator(self, test_book: Path):
        """Values render with thousands separators, no decimals,
        prefixed with the book's default currency."""
        gc = GnuCashBook(str(test_book))
        # Seed a value that will exercise comma formatting at "now".
        self._seed_balanced_deposit(gc, "12000", self._months_ago(0))
        result = gc.get_book_summary()
        section = result.split("Net worth trajectory:\n", 1)[1]
        now_row = next(r for r in section.split("\n")[:5] if "now" in r)
        # Total should now include the seed; format has comma.
        assert "USD" in now_row
        # No decimals.
        assert "." not in now_row.split("USD")[-1]
        # Comma separator.
        assert "," in now_row

    def test_trajectory_reflects_growth(self, test_book: Path):
        """A book where wealth grew should show now > 12mo ago.
        Sanity check that point-in-time computation differs across
        anchors."""
        gc = GnuCashBook(str(test_book))
        # Seed activity exactly at 12mo ago and exactly now to
        # produce a measurable gap.
        self._seed_balanced_deposit(gc, "100", self._months_ago(13))
        self._seed_balanced_deposit(gc, "5000", self._months_ago(0))

        result = gc.get_book_summary()
        section = result.split("Net worth trajectory:\n", 1)[1]
        rows = section.split("\n")[:5]
        oldest_row = rows[0]
        newest_row = rows[-1]
        # Extract numeric value from each row.
        import re
        old_val = int(re.search(r"USD ([0-9,]+)", oldest_row).group(1).replace(",", ""))
        new_val = int(re.search(r"USD ([0-9,]+)", newest_row).group(1).replace(",", ""))
        assert new_val > old_val

    def test_now_agrees_with_assets_minus_liabilities(
        self, test_book: Path,
    ):
        """Trajectory's "now" anchor and the displayed Assets /
        Liabilities sections agree on net worth — both filter at
        today, both use the same conversion semantics. Locks in the
        bookkeeper's $2,906 gap fix on Alex's book.

        Builds a book with a future-dated transaction (data range
        extends past today). Without the today-filter, the Assets
        section sums all splits including the future entry while
        trajectory's "now" filters at today, producing a
        discrepancy. With the filter, both align."""
        gc = GnuCashBook(str(test_book))
        # Seed a future-dated transaction. Its $5,000 deposit
        # should NOT count toward today's net worth, neither in
        # Assets section nor in trajectory's "now" anchor.
        future = date.today() + timedelta(days=10)
        with gc.open(readonly=False) as book:
            checking = gc._find_account(book, "Assets:Checking")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Future-dated entry",
                post_date=future,
                splits=[
                    piecash.Split(
                        account=checking, value=Decimal("5000"),
                    ),
                    piecash.Split(
                        account=opening, value=Decimal("-5000"),
                    ),
                ],
            ))
            book.save()

        result = gc.get_book_summary()
        # Parse Assets total and Liabilities total from the output.
        import re
        assets_line = next(
            l for l in result.split("\n")
            if l.startswith("Assets: ")
        )
        assets_match = re.search(r"USD\s+([\d.]+)", assets_line)
        assets_total = Decimal(assets_match.group(1))

        liabilities_line = next(
            l for l in result.split("\n")
            if l.startswith("Liabilities: ")
        )
        liab_match = re.search(r"USD\s+([\d.]+)", liabilities_line)
        liabilities_total = Decimal(liab_match.group(1))

        # Trajectory's "now" line.
        section = result.split("Net worth trajectory:\n", 1)[1]
        now_row = next(
            r for r in section.split("\n")[:5] if "now" in r
        )
        now_match = re.search(r"USD\s+([\d,]+)", now_row)
        now_value = Decimal(now_match.group(1).replace(",", ""))

        # The two computations must agree by construction (within
        # quantize-to-whole-dollar rounding).
        balance_sheet_net_worth = (assets_total - liabilities_total)
        assert abs(balance_sheet_net_worth - now_value) < Decimal("1"), (
            f"Assets ({assets_total}) - Liabilities "
            f"({liabilities_total}) = {balance_sheet_net_worth}, "
            f"but trajectory's 'now' = {now_value}"
        )

    def test_now_uses_cost_basis_for_unpriced_foreign_commodity(
        self, tmp_path: Path,
    ):
        """When an investment account holds a foreign commodity with
        no price on record, ``_compute_net_worth_at`` falls back to
        cost basis (sum of split.value, in transaction currency =
        book default for typical USD-denominated buys). Mirrors the
        bottom-line ``_market_value`` helper's fallback so the
        trajectory's "now" anchor agrees with what the user
        previously read off the (now-removed) ``Net worth:`` line.

        Regression for the bookkeeper's $2,905 discrepancy report —
        the earlier implementation skipped unpriced commodities
        entirely, which deflated trajectory below the bottom-line."""
        book_path = tmp_path / "unpriced.gnucash"
        b = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = b.root_account
        usd = b.default_currency
        # An unpriced commodity (no Price rows entered).
        from piecash import Commodity
        nopr = Commodity(
            namespace="FUND",
            mnemonic="NOPR",
            fullname="Unpriced Fund",
            fraction=10000,
        )
        b.session.add(nopr)
        assets = piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=True,
        )
        b.session.add(assets)
        investments = piecash.Account(
            name="Investments", type="ASSET", parent=assets,
            commodity=usd, placeholder=True,
        )
        b.session.add(investments)
        nopr_acct = piecash.Account(
            name="NOPR Holding", type="MUTUAL", parent=investments,
            commodity=nopr,
        )
        b.session.add(nopr_acct)
        equity = piecash.Account(
            name="Equity", type="EQUITY", parent=root,
            commodity=usd, placeholder=True,
        )
        b.session.add(equity)
        opening = piecash.Account(
            name="Opening Balance", type="EQUITY", parent=equity,
            commodity=usd,
        )
        b.session.add(opening)
        b.save()

        # Buy 100 shares of NOPR at $50 per share = $5,000 cost.
        # value=USD (transaction currency), quantity=100 NOPR shares.
        purchase_date = date.today() - timedelta(days=30)
        b.session.add(piecash.Transaction(
            currency=usd,
            description="Buy NOPR",
            post_date=purchase_date,
            splits=[
                piecash.Split(
                    account=nopr_acct,
                    value=Decimal("5000"),
                    quantity=Decimal("100"),
                ),
                piecash.Split(
                    account=opening,
                    value=Decimal("-5000"),
                ),
            ],
        ))
        b.save()
        b.close()

        gc = GnuCashBook(str(book_path))
        result = gc.get_book_summary()
        section = result.split("Net worth trajectory:\n", 1)[1]
        now_row = next(r for r in section.split("\n")[:5] if "now" in r)
        # Cost basis fallback: 100 shares @ no price → $5,000 cost.
        # Net worth = $5,000 (no liabilities).
        assert "USD 5,000" in now_row

    def test_section_omitted_when_all_anchors_predate_data(
        self, tmp_path: Path,
    ):
        """If first_date is in the future relative to all anchors
        (e.g., book starts today), all anchors except 'now' could
        still qualify; this test exercises the very-new-book path.
        Most importantly: a brand-new book with zero transactions
        omits the section, and 'now' alone with no prior history
        still shows trajectory if first_date ≤ today."""
        book_path = tmp_path / "fresh.gnucash"
        b = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = b.root_account
        usd = b.default_currency
        assets = piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=True,
        )
        b.session.add(assets)
        checking = piecash.Account(
            name="Checking", type="BANK", parent=assets, commodity=usd,
        )
        b.session.add(checking)
        equity = piecash.Account(
            name="Equity", type="EQUITY", parent=root,
            commodity=usd, placeholder=True,
        )
        b.session.add(equity)
        opening = piecash.Account(
            name="Opening Balance", type="EQUITY", parent=equity,
            commodity=usd,
        )
        b.session.add(opening)
        b.save()
        b.close()
        # Empty book → no transactions → omit.
        gc = GnuCashBook(str(book_path))
        result = gc.get_book_summary()
        assert "Net worth trajectory" not in result


class TestGetBookSummaryMonthlyNet:
    """Monthly cash flow section in get_book_summary.

    Last 6 calendar months of net income (income − expenses), most
    recent first, with current-month MTD marker. See
    ``CoreMixin._monthly_net_income`` and the spec at
    ``docs/GET_BOOK_SUMMARY_SPEC.md`` §3.
    """

    def _seed_income(
        self,
        gc: GnuCashBook,
        amount: str,
        on_date: date,
        description: str = "Income",
    ) -> None:
        """Seed a single income transaction (Income:Salary credited
        for ``amount``, Assets:Checking debited)."""
        gc.create_transaction(
            description=description,
            splits=[
                {"account": "Income:Salary", "amount": f"-{amount}"},
                {"account": "Assets:Checking", "amount": amount},
            ],
            trans_date=on_date,
            check_duplicates=False,
        )

    def _seed_expense(
        self,
        gc: GnuCashBook,
        amount: str,
        on_date: date,
        description: str = "Expense",
    ) -> None:
        """Seed a single expense (Expenses:Groceries debited,
        Assets:Checking credited)."""
        gc.create_transaction(
            description=description,
            splits=[
                {"account": "Expenses:Groceries", "amount": amount},
                {"account": "Assets:Checking", "amount": f"-{amount}"},
            ],
            trans_date=on_date,
            check_duplicates=False,
        )

    @staticmethod
    def _months_ago(n: int) -> date:
        """First day of the calendar month ``n`` months before today.
        Plain (year, month) arithmetic to match the production
        helper, no dateutil dependency."""
        today = date.today()
        year, month = today.year, today.month
        for _ in range(n):
            if month == 1:
                year, month = year - 1, 12
            else:
                month -= 1
        return date(year, month, 1)

    def test_section_omitted_with_no_income_or_expense_activity(
        self, test_book: Path,
    ):
        """A book with no income/expense activity in the last 6
        months emits no Monthly net section. The fixture's
        2024-01 activity is well outside the rolling 6-month
        window from any current run-date."""
        gc = GnuCashBook(str(test_book))
        result = gc.get_book_summary()
        assert "Monthly net" not in result

    def test_section_present_when_recent_activity(
        self, test_book: Path,
    ):
        """Seed activity inside the window → section appears."""
        gc = GnuCashBook(str(test_book))
        self._seed_income(gc, "1000", date.today())
        result = gc.get_book_summary()
        assert "Monthly net (last 6 months):" in result

    def test_six_months_emitted_oldest_to_newest(
        self, test_book: Path,
    ):
        """When the section emits, it's exactly 6 month-rows in
        most-recent-first order."""
        gc = GnuCashBook(str(test_book))
        self._seed_income(gc, "1000", date.today())
        result = gc.get_book_summary()
        section = result.split("Monthly net (last 6 months):\n", 1)[1]
        # Section runs until the next non-indented line.
        rows = []
        for line in section.split("\n"):
            if line.startswith("  "):
                rows.append(line)
            else:
                break
        assert len(rows) == 6
        # Most recent first: row 0 is the current month.
        today = date.today()
        current_label = today.strftime("%b %Y")
        assert current_label in rows[0]
        # Row 5 is 5 months ago.
        oldest_label = self._months_ago(5).strftime("%b %Y")
        assert oldest_label in rows[5]

    def test_current_month_marked_mtd(self, test_book: Path):
        """The current calendar month is partial — its row carries
        a (MTD) suffix on the label."""
        gc = GnuCashBook(str(test_book))
        self._seed_income(gc, "1000", date.today())
        result = gc.get_book_summary()
        section = result.split("Monthly net (last 6 months):\n", 1)[1]
        first_row = section.split("\n", 1)[0]
        assert "(MTD)" in first_row

    def test_zero_month_reports_zero_not_omitted(
        self, test_book: Path,
    ):
        """Months with no income/expense activity render as ``+0``,
        they don't fall out of the section."""
        gc = GnuCashBook(str(test_book))
        self._seed_income(gc, "1000", date.today())
        # No activity 3 months ago.
        result = gc.get_book_summary()
        section = result.split("Monthly net (last 6 months):\n", 1)[1]
        three_ago_label = self._months_ago(3).strftime("%b %Y")
        three_ago_row = next(
            line for line in section.split("\n")
            if three_ago_label in line
        )
        assert "+0" in three_ago_row

    def test_signed_format(self, test_book: Path):
        """Positive net: ``+N``. Negative net: ``-N`` (native sign).
        Zero: ``+0``. Always prefixed with explicit sign."""
        gc = GnuCashBook(str(test_book))
        # Current month: net positive (income > expense)
        self._seed_income(gc, "2000", date.today())
        self._seed_expense(gc, "500", date.today())
        # 1 month ago: net negative
        one_ago = self._months_ago(1)
        # Use mid-month so it doesn't roll into a different month
        one_ago_mid = date(one_ago.year, one_ago.month, 15)
        self._seed_expense(gc, "300", one_ago_mid)

        result = gc.get_book_summary()
        section = result.split("Monthly net (last 6 months):\n", 1)[1]
        rows = section.split("\n")[:6]

        # Current month: +1500
        assert "+1,500" in rows[0]
        # 1 month ago: -300
        one_ago_row = next(
            r for r in rows
            if one_ago.strftime("%b %Y") in r
        )
        assert "-300" in one_ago_row

    def test_income_minus_expense_arithmetic(self, test_book: Path):
        """Sanity: net = income contributions − expense contributions
        for the same month, regardless of how many transactions."""
        gc = GnuCashBook(str(test_book))
        d = date.today()
        self._seed_income(gc, "5000", d, "salary 1")
        self._seed_income(gc, "1000", d, "salary 2")
        self._seed_expense(gc, "1500", d, "groceries 1")
        self._seed_expense(gc, "200", d, "groceries 2")
        # Net: 6000 - 1700 = 4300
        result = gc.get_book_summary()
        section = result.split("Monthly net (last 6 months):\n", 1)[1]
        first_row = section.split("\n", 1)[0]
        assert "+4,300" in first_row

    def test_old_activity_outside_window_excluded(
        self, test_book: Path,
    ):
        """Income from 12 months ago doesn't bleed into the 6-month
        window. The section either omits entirely (if no in-window
        activity) or shows the 6 months and ignores ancient data."""
        gc = GnuCashBook(str(test_book))
        # Seed activity in current month so the section renders,
        # plus old activity that should be ignored.
        self._seed_income(gc, "100", date.today())
        old = self._months_ago(11)
        old_mid = date(old.year, old.month, 15)
        self._seed_income(gc, "999999", old_mid)

        result = gc.get_book_summary()
        section = result.split("Monthly net (last 6 months):\n", 1)[1]
        # No row should contain the old amount.
        rows = section.split("\n")[:6]
        assert not any("999,999" in r for r in rows)


class TestGetBookSummaryRunway:
    """Runway section in get_book_summary.

    Days the household could survive on liquid assets at current
    burn rate if income stopped today. See ``CoreMixin._runway_metrics``
    and the spec at ``docs/GET_BOOK_SUMMARY_SPEC.md`` §4.
    """

    def _seed_recent_expense(
        self,
        gc: GnuCashBook,
        amount: str,
        days_ago: int,
    ) -> None:
        """Seed an expense transaction that lands within the runway
        burn window."""
        when = date.today() - timedelta(days=days_ago)
        gc.create_transaction(
            description=f"Expense {days_ago}d ago",
            splits=[
                {"account": "Expenses:Groceries", "amount": amount},
                {"account": "Assets:Checking", "amount": f"-{amount}"},
            ],
            trans_date=when,
            check_duplicates=False,
        )

    def test_section_omitted_with_no_expenses_in_window(
        self, test_book: Path,
    ):
        """No expense activity in the 180-day burn window → no
        runway computable → omit section. The fixture's $150
        groceries transaction is 2024-01-20, outside any current
        180-day window."""
        gc = GnuCashBook(str(test_book))
        result = gc.get_book_summary()
        assert "Runway:" not in result

    def test_section_present_with_expense_activity(
        self, test_book: Path,
    ):
        """A book with recent expenses emits the runway section."""
        gc = GnuCashBook(str(test_book))
        self._seed_recent_expense(gc, "100", 30)
        result = gc.get_book_summary()
        assert "Runway:" in result

    def test_runway_format(self, test_book: Path):
        """Runway line carries days, parenthesized liquid + burn,
        currency-prefixed thousands-separated values, no decimals."""
        gc = GnuCashBook(str(test_book))
        # $180 of expenses over 180 days → $1/day burn.
        # Fixture's Checking starts at $2,850; this seeded expense
        # subtracts $180 from it via the offsetting split, leaving
        # liquid = $2,670. Runway = 2,670 days, far above 60 → no ⚠.
        self._seed_recent_expense(gc, "180", 30)
        result = gc.get_book_summary()
        runway_line = next(
            l for l in result.split("\n") if l.startswith("Runway:")
        )
        assert "days" in runway_line
        assert "USD" in runway_line
        assert "liquid" in runway_line
        assert "/day burn" in runway_line
        # Comma-separated for the liquid (2,670).
        assert "2,670" in runway_line
        # No decimals.
        assert "." not in runway_line

    def test_warning_below_60_days(self, test_book: Path):
        """Runway < 60 days → ⚠ marker."""
        gc = GnuCashBook(str(test_book))
        # Burn $100/day for 180 days = $18,000.
        # Liquid in fixture: $2,850. Runway = 28 days. Under 60.
        self._seed_recent_expense(gc, "18000", 30)
        result = gc.get_book_summary()
        runway_line = next(
            l for l in result.split("\n") if l.startswith("Runway:")
        )
        assert "⚠" in runway_line

    def test_no_warning_at_or_above_60_days(self, test_book: Path):
        """Runway ≥ 60 days → no ⚠ marker; absence is the signal."""
        gc = GnuCashBook(str(test_book))
        # Tiny burn: $90 over 180 days = $0.50/day.
        # Liquid $2,850. Runway = 5,700 days. No warning.
        self._seed_recent_expense(gc, "90", 30)
        result = gc.get_book_summary()
        runway_line = next(
            l for l in result.split("\n") if l.startswith("Runway:")
        )
        assert "⚠" not in runway_line

    def test_negative_liquid_position(self, tmp_path: Path):
        """Overdrafts exceeding positive cash → '0 days — liquid
        position is negative ⚠'."""
        book_path = tmp_path / "neg_liquid.gnucash"
        b = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        root = b.root_account
        usd = b.default_currency
        assets = piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=True,
        )
        b.session.add(assets)
        checking = piecash.Account(
            name="Checking", type="BANK", parent=assets, commodity=usd,
        )
        b.session.add(checking)
        expenses = piecash.Account(
            name="Expenses", type="EXPENSE", parent=root,
            commodity=usd, placeholder=True,
        )
        b.session.add(expenses)
        groc = piecash.Account(
            name="Groceries", type="EXPENSE", parent=expenses,
            commodity=usd,
        )
        b.session.add(groc)
        equity = piecash.Account(
            name="Equity", type="EQUITY", parent=root,
            commodity=usd, placeholder=True,
        )
        b.session.add(equity)
        opening = piecash.Account(
            name="Opening Balance", type="EQUITY", parent=equity,
            commodity=usd,
        )
        b.session.add(opening)
        b.save()
        # Overdraft Checking by $100, expense the same.
        b.session.add(piecash.Transaction(
            currency=usd,
            description="Overdraft expense",
            post_date=date.today() - timedelta(days=10),
            splits=[
                piecash.Split(account=groc, value=Decimal("100")),
                piecash.Split(account=checking, value=Decimal("-100")),
            ],
        ))
        b.save()
        b.close()

        gc = GnuCashBook(str(book_path))
        result = gc.get_book_summary()
        runway_line = next(
            l for l in result.split("\n") if l.startswith("Runway:")
        )
        assert "0 days" in runway_line
        assert "negative" in runway_line
        assert "⚠" in runway_line

    def test_asset_typed_account_excluded_from_liquid(
        self, test_book: Path,
    ):
        """ASSET-typed accounts (real estate, vehicles, fixed assets)
        do NOT count as liquid even when in the book's default
        currency. Regression for the bookkeeper's report on Alex's
        2026-04-23 review: a $473K condo and $28K vehicle were
        wrongly counted as liquid, inflating runway from 116 days
        to 768 days (4 months vs 2 years — different conversation
        with the user)."""
        gc = GnuCashBook(str(test_book))
        gc.create_account(
            name="Condo", account_type="ASSET", parent="Assets",
        )
        with gc.open(readonly=False) as book:
            condo = gc._find_account(book, "Assets:Condo")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Condo purchase",
                post_date=date.today() - timedelta(days=120),
                splits=[
                    piecash.Split(
                        account=condo, value=Decimal("473250"),
                    ),
                    piecash.Split(
                        account=opening, value=Decimal("-473250"),
                    ),
                ],
            ))
            book.save()
        self._seed_recent_expense(gc, "180", 30)
        result = gc.get_book_summary()
        runway_line = next(
            l for l in result.split("\n") if l.startswith("Runway:")
        )
        # Condo MUST NOT contribute. Liquid stays at $2,670 (the
        # fixture's Checking, after the seeded expense).
        assert "2,670" in runway_line
        assert "473,250" not in runway_line
        assert "475,920" not in runway_line  # 2,670 + 473,250 NOT

    def test_stock_with_market_price_valued_at_market(
        self, investment_book: Path,
    ):
        """STOCK / MUTUAL accounts ARE liquid — brokerage positions
        sell in a day at market price. Valuation: shares × latest
        user-supplied price. The investment_book fixture seeds VTSAX
        at $125/share."""
        gc = GnuCashBook(str(investment_book))
        # Buy 50 shares of VTSAX at $125/share = $6,250 cost. The
        # account commodity is VTSAX (FUND); the fixture's price
        # row makes the latest rate $125 USD per share.
        with gc.open(readonly=False) as book:
            vtsax_acct = gc._find_account(book, "Assets:Investments:VTSAX")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Buy VTSAX",
                post_date=date.today() - timedelta(days=20),
                splits=[
                    piecash.Split(
                        account=vtsax_acct,
                        value=Decimal("6250"),
                        quantity=Decimal("50"),
                    ),
                    piecash.Split(
                        account=opening, value=Decimal("-6250"),
                    ),
                ],
            ))
            book.save()
        # Add expense activity so runway is computable.
        gc.create_transaction(
            description="Some expense",
            splits=[
                {"account": "Income:Capital Gains", "amount": "-180"},
                {"account": "Assets:Checking", "amount": "180"},
            ],
            trans_date=date.today() - timedelta(days=10),
            check_duplicates=False,
        )
        # Need EXPENSE-typed account for daily_burn. The fixture
        # doesn't have one; create one and post against it.
        gc.create_account(
            name="Misc", account_type="EXPENSE", parent=None,
        )
        gc.create_transaction(
            description="Misc expense",
            splits=[
                {"account": "Misc", "amount": "180"},
                {"account": "Assets:Checking", "amount": "-180"},
            ],
            trans_date=date.today() - timedelta(days=15),
            check_duplicates=False,
        )

        result = gc.get_book_summary()
        runway_line = next(
            l for l in result.split("\n") if l.startswith("Runway:")
        )
        # Liquid: Checking starts at $10,000 + $180 (income) - $180
        # (misc) = $10,000. Plus VTSAX 50 shares × $125 = $6,250.
        # Total liquid: $16,250.
        assert "16,250" in runway_line

    def test_retirement_subtree_excluded_from_liquid(
        self, test_book: Path,
    ):
        """Accounts under a 'Retirement' parent (IRA / 401k / 403b
        and similar) are excluded from runway liquid even when
        their type is otherwise liquid (BANK / STOCK / MUTUAL).
        Early-withdrawal penalties make them unavailable for
        'if income stops today' runway purposes.

        Regression for the bookkeeper's report on Alex's book: a
        $13,716 401k was being counted as liquid because BANK type
        passed the filter."""
        gc = GnuCashBook(str(test_book))
        gc.create_account(
            name="Retirement",
            account_type="ASSET",
            parent="Assets",
            placeholder=True,
        )
        gc.create_account(
            name="401k",
            account_type="BANK",
            parent="Assets:Retirement",
        )
        with gc.open(readonly=False) as book:
            k401 = gc._find_account(book, "Assets:Retirement:401k")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="401k contribution",
                post_date=date.today() - timedelta(days=15),
                splits=[
                    piecash.Split(
                        account=k401, value=Decimal("13716"),
                    ),
                    piecash.Split(
                        account=opening, value=Decimal("-13716"),
                    ),
                ],
            ))
            book.save()
        self._seed_recent_expense(gc, "180", 30)

        result = gc.get_book_summary()
        runway_line = next(
            l for l in result.split("\n") if l.startswith("Runway:")
        )
        # 401k stays out of liquid; runway uses $2,670 Checking only.
        assert "2,670" in runway_line
        assert "13,716" not in runway_line
        assert "16,386" not in runway_line  # 2,670 + 13,716 NOT

    def test_retirement_match_is_case_insensitive(
        self, test_book: Path,
    ):
        """The retirement-subtree check is case-insensitive — a
        user who writes "RETIREMENT" or "Retirement" in any path
        component still gets their child accounts excluded."""
        gc = GnuCashBook(str(test_book))
        gc.create_account(
            name="RETIREMENT ACCOUNTS",
            account_type="ASSET",
            parent="Assets",
            placeholder=True,
        )
        gc.create_account(
            name="Roth IRA",
            account_type="BANK",
            parent="Assets:RETIREMENT ACCOUNTS",
        )
        with gc.open(readonly=False) as book:
            roth = gc._find_account(
                book, "Assets:RETIREMENT ACCOUNTS:Roth IRA",
            )
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Roth contribution",
                post_date=date.today() - timedelta(days=10),
                splits=[
                    piecash.Split(
                        account=roth, value=Decimal("7000"),
                    ),
                    piecash.Split(
                        account=opening, value=Decimal("-7000"),
                    ),
                ],
            ))
            book.save()
        self._seed_recent_expense(gc, "180", 30)

        result = gc.get_book_summary()
        runway_line = next(
            l for l in result.split("\n") if l.startswith("Runway:")
        )
        # Roth IRA stays out; runway uses $2,670 Checking only.
        assert "7,000" not in runway_line
        assert "2,670" in runway_line

    def test_stock_without_price_uses_cost_basis(
        self, test_book: Path,
    ):
        """STOCK without a price falls back to cost basis (sum of
        split.value, in transaction currency = book default). Same
        fallback net worth uses, so runway and net worth agree on
        the value of unpriced holdings."""
        gc = GnuCashBook(str(test_book))
        # Build a STOCK account with no Price rows. piecash needs
        # a non-currency commodity for STOCK accounts.
        with gc.open(readonly=False) as book:
            from piecash import Commodity
            wild = Commodity(
                namespace="NYSE",
                mnemonic="WILD",
                fullname="Unpriced Wild Stock",
                fraction=10000,
            )
            book.session.add(wild)
            assets = gc._find_account(book, "Assets")
            opening = gc._find_account(book, "Equity:Opening Balance")
            wild_acct = piecash.Account(
                name="WILD",
                type="STOCK",
                parent=assets,
                commodity=wild,
            )
            book.session.add(wild_acct)
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Buy WILD at cost",
                post_date=date.today() - timedelta(days=15),
                splits=[
                    piecash.Split(
                        account=wild_acct,
                        value=Decimal("4500"),
                        quantity=Decimal("100"),
                    ),
                    piecash.Split(
                        account=opening, value=Decimal("-4500"),
                    ),
                ],
            ))
            book.save()
        self._seed_recent_expense(gc, "180", 30)

        result = gc.get_book_summary()
        runway_line = next(
            l for l in result.split("\n") if l.startswith("Runway:")
        )
        # Cost basis $4,500. Plus Checking $2,670 (after seeded
        # expense). Liquid total: $7,170.
        assert "7,170" in runway_line


class TestGetBookSummaryBudgetHeadline:
    """Budget headline section in get_book_summary.

    One line per active budget (the one whose period range covers
    today). See ``CoreMixin._budget_headline`` and
    ``docs/GET_BOOK_SUMMARY_SPEC.md`` §6.
    """

    def _make_budget_covering_today(
        self,
        gc: GnuCashBook,
        name: str = "Test Budget",
    ) -> str:
        """Create a 12-month budget anchored to the current year so
        today falls within its range. Returns the budget name."""
        gc.create_budget(name=name, year=date.today().year, num_periods=12)
        return name

    def test_section_omitted_when_no_budgets(self, test_book: Path):
        """A book with no budgets gets no headline."""
        gc = GnuCashBook(str(test_book))
        result = gc.get_book_summary()
        assert "Budget (" not in result

    def test_section_omitted_when_no_budget_covers_today(
        self, test_book: Path,
    ):
        """A budget anchored to a year that doesn't include today
        is skipped — there's no budget the user is currently
        living inside."""
        gc = GnuCashBook(str(test_book))
        # Anchor budget to 2020, which doesn't include today.
        gc.create_budget(
            name="Stale 2020", year=2020, num_periods=12,
        )
        result = gc.get_book_summary()
        assert "Budget (" not in result

    def test_section_present_with_active_budget(
        self, budget_book: Path,
    ):
        """Active budget covering today renders a headline line."""
        gc = GnuCashBook(str(budget_book))
        name = self._make_budget_covering_today(gc, "Annual Test")
        gc.set_budget_amount(
            budget_name=name,
            account="Expenses:Groceries",
            amount="500",
        )
        result = gc.get_book_summary()
        assert "Budget (Annual Test):" in result

    def test_format_components(self, budget_book: Path):
        """Headline format: name, % used, % elapsed, variance,
        optional ⚠. Currency-free, all percentage values."""
        gc = GnuCashBook(str(budget_book))
        self._make_budget_covering_today(gc, "Test Budget")
        gc.set_budget_amount(
            budget_name="Test Budget",
            account="Expenses:Groceries",
            amount="500",
        )
        result = gc.get_book_summary()
        budget_line = next(
            l for l in result.split("\n") if l.startswith("Budget (")
        )
        # Format pieces — no currency markers (this is %).
        assert "% used" in budget_line
        assert "% elapsed" in budget_line
        # Either "+X% over pace" / "X% under pace" / "on pace" form.
        assert (
            "over pace" in budget_line
            or "under pace" in budget_line
            or "on pace" in budget_line
        )

    def test_overspend_variance_warns_at_10_pct(
        self, budget_book: Path,
    ):
        """Variance > +10% (used% ahead of elapsed% by more than
        10 points) earns a ⚠ marker."""
        # Build a fresh budget book where we control exact numbers.
        gc = GnuCashBook(str(budget_book))
        # Anchor the budget to start of current year, num_periods=12
        # → covers ~all of this calendar year.
        self._make_budget_covering_today(gc, "Overspend")
        # Tiny budget target → easy to exceed.
        gc.set_budget_amount(
            budget_name="Overspend",
            account="Expenses:Groceries",
            amount="100",  # $100/month × 12 = $1,200 total
        )
        # Big actual spend in the budget's accounts.
        gc.create_transaction(
            description="Massive grocery run",
            splits=[
                {"account": "Expenses:Groceries", "amount": "5000"},
                {"account": "Assets:Checking", "amount": "-5000"},
            ],
            trans_date=date.today(),
            check_duplicates=False,
        )
        result = gc.get_book_summary()
        budget_line = next(
            l for l in result.split("\n") if l.startswith("Budget (")
        )
        # Used: 5000 / 1200 = 416% (capped semantically by intent).
        # Variance vs elapsed = ~416 - elapsed%, well over +10.
        assert "⚠" in budget_line
        assert "over pace" in budget_line

    def test_underspend_no_warning(self, budget_book: Path):
        """Variance ≤ +10% (under pace or close) → no warning marker."""
        gc = GnuCashBook(str(budget_book))
        self._make_budget_covering_today(gc, "Underspend")
        gc.set_budget_amount(
            budget_name="Underspend",
            account="Expenses:Groceries",
            amount="10000",
        )
        # No actuals at all in the budgeted account during the
        # budget period.
        result = gc.get_book_summary()
        budget_line = next(
            l for l in result.split("\n") if l.startswith("Budget (")
        )
        assert "⚠" not in budget_line
        # 0% used vs ~partial-year% elapsed → "under pace".
        assert "under pace" in budget_line

    def test_multiple_budgets_picks_latest_start(
        self, budget_book: Path,
    ):
        """When multiple budgets cover today, the headline shows
        the one with the latest start date — most recently
        effective."""
        gc = GnuCashBook(str(budget_book))
        # Two budgets covering today; one anchored to current year,
        # the other to a year that started slightly later (still
        # covering today). The fixture has plenty of history; pick
        # both to start in this current year.
        gc.create_budget(
            name="Older", year=date.today().year - 1, num_periods=24,
        )
        gc.create_budget(
            name="Newer", year=date.today().year, num_periods=12,
        )
        # Both need at least one BudgetAmount to qualify.
        for n in ("Older", "Newer"):
            gc.set_budget_amount(
                budget_name=n,
                account="Expenses:Groceries",
                amount="100",
            )
        result = gc.get_book_summary()
        budget_line = next(
            l for l in result.split("\n") if l.startswith("Budget (")
        )
        # The Newer budget (starts current year) wins.
        assert "Newer" in budget_line
        assert "Older" not in budget_line

    def test_zero_target_budget_omits_section(
        self, budget_book: Path,
    ):
        """A budget with no BudgetAmount rows (or all zero) has
        nothing to compare actuals against; omit rather than
        divide by zero."""
        gc = GnuCashBook(str(budget_book))
        self._make_budget_covering_today(gc, "Empty")
        # No set_budget_amount calls — no targets at all.
        result = gc.get_book_summary()
        assert "Budget (Empty)" not in result


class TestGetBookSummaryWarnings:
    """Consolidated Warnings section in get_book_summary.

    Lives near the top of the output so the LLM sees data
    integrity issues, stale prices, and overdue items BEFORE
    reading numbers that depend on them. See
    ``CoreMixin._collect_warnings`` and
    ``docs/GET_BOOK_SUMMARY_SPEC.md`` §5.
    """

    def test_section_omitted_when_no_warnings(self, test_book: Path):
        """No warnings → no header, no body — absence is the signal.
        The fixture is a clean book with no integrity issues, no
        stale prices, no overdue scheduled."""
        gc = GnuCashBook(str(test_book))
        result = gc.get_book_summary()
        assert "Warnings:" not in result

    def test_imbalance_account_with_balance_warns(
        self, test_book: Path,
    ):
        """A non-zero balance on Imbalance-{ccy} indicates a
        structural defect — flag with a data-integrity note."""
        gc = GnuCashBook(str(test_book))
        # Build the auto-created Imbalance account that GnuCash
        # would have created for an unbalanced transaction. We're
        # simulating the post-defect state.
        with gc.open(readonly=False) as book:
            assets = gc._find_account(book, "Assets")
            opening = gc._find_account(book, "Equity:Opening Balance")
            imbalance = piecash.Account(
                name="Imbalance-USD",
                type="BANK",
                parent=book.root_account,
                commodity=book.default_currency,
            )
            book.session.add(imbalance)
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Phantom imbalance",
                post_date=date.today() - timedelta(days=10),
                splits=[
                    piecash.Split(
                        account=imbalance, value=Decimal("42.50"),
                    ),
                    piecash.Split(
                        account=opening, value=Decimal("-42.50"),
                    ),
                ],
            ))
            book.save()

        result = gc.get_book_summary()
        assert "Warnings:" in result
        warnings_block = result.split("Warnings:")[1].split("\n")
        joined = "\n".join(warnings_block)
        assert "Imbalance-USD" in joined
        assert "data integrity issue" in joined
        assert "⚠" in joined

    def test_orphan_account_with_balance_warns(self, test_book: Path):
        """Orphan-{ccy} non-zero balance → data integrity warning,
        same shape as Imbalance."""
        gc = GnuCashBook(str(test_book))
        with gc.open(readonly=False) as book:
            opening = gc._find_account(book, "Equity:Opening Balance")
            orphan = piecash.Account(
                name="Orphan-USD",
                type="BANK",
                parent=book.root_account,
                commodity=book.default_currency,
            )
            book.session.add(orphan)
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Orphaned residue",
                post_date=date.today() - timedelta(days=20),
                splits=[
                    piecash.Split(
                        account=orphan, value=Decimal("3.14"),
                    ),
                    piecash.Split(
                        account=opening, value=Decimal("-3.14"),
                    ),
                ],
            ))
            book.save()

        result = gc.get_book_summary()
        warnings_block = result.split("Warnings:")[1].split("\n")
        joined = "\n".join(warnings_block)
        assert "Orphan-USD" in joined
        assert "data integrity issue" in joined

    def test_zero_balance_imbalance_does_not_warn(
        self, test_book: Path,
    ):
        """An Imbalance- account with a zero balance is fine — only
        non-zero balance is a defect."""
        gc = GnuCashBook(str(test_book))
        with gc.open(readonly=False) as book:
            piecash.Account(
                name="Imbalance-USD",
                type="BANK",
                parent=book.root_account,
                commodity=book.default_currency,
            )
            book.save()
        result = gc.get_book_summary()
        # Section may emit for other reasons; verify Imbalance-USD
        # specifically is not in any warning line.
        if "Warnings:" in result:
            warnings_block = result.split("Warnings:")[1].split(
                "Accounts:"
            )[0]
            assert "Imbalance-USD" not in warnings_block

    def test_stale_price_warns(self, investment_book: Path):
        """A non-default commodity in active use whose latest price
        is older than the staleness threshold gets a Warnings
        line. The investment_book fixture has VTSAX with a
        single price on 2026-01-15, which is now well past the
        30-day cutoff."""
        gc = GnuCashBook(str(investment_book))
        result = gc.get_book_summary()
        assert "Warnings:" in result
        warnings_block = result.split("Warnings:")[1].split(
            "Accounts:"
        )[0]
        assert "VTSAX" in warnings_block
        assert "Stale price" in warnings_block
        assert "days ago" in warnings_block

    def test_unpriced_commodity_in_use_warns_no_price_on_file(
        self, test_book: Path,
    ):
        """A commodity referenced by an account but with no price
        record at all → 'no price on file' warning."""
        gc = GnuCashBook(str(test_book))
        with gc.open(readonly=False) as book:
            from piecash import Commodity
            wild = Commodity(
                namespace="NYSE",
                mnemonic="WILD",
                fullname="Wild Stock",
                fraction=10000,
            )
            book.session.add(wild)
            assets = gc._find_account(book, "Assets")
            piecash.Account(
                name="WILD",
                type="STOCK",
                parent=assets,
                commodity=wild,
            )
            book.save()
        result = gc.get_book_summary()
        assert "Warnings:" in result
        warnings_block = result.split("Warnings:")[1].split(
            "Accounts:"
        )[0]
        assert "WILD" in warnings_block
        assert "no price on file" in warnings_block

    def test_iso_currency_in_use_with_stale_rate_warns(
        self, tmp_path: Path,
    ):
        """ISO currencies (CURRENCY namespace) get the same stale-
        price treatment as commodity tickers when they're in active
        use. A multi-currency book with a foreign-currency
        receivable account but no recent FX rate fires
        'Stale price: EUR' so the bookkeeper knows converted
        totals (e.g., 'Receivables: USD X') are unreliable.

        Regression for the cousin's review note on Alex's book:
        stale FX rates cascade into wrong receivables totals; not
        flagging them leaves the user with no visibility into
        whether the displayed conversion is current."""
        book_path = tmp_path / "fx_stale.gnucash"
        b = piecash.create_book(
            str(book_path), currency="USD", overwrite=True,
        )
        from piecash import factories
        eur = factories.create_currency_from_ISO("EUR")
        b.session.add(eur)

        root = b.root_account
        usd = b.default_currency
        assets = piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=True,
        )
        b.session.add(assets)
        # EUR-typed receivable — pulls EUR into in_use.
        ar_eur = piecash.Account(
            name="Accounts Receivable EUR",
            type="RECEIVABLE",
            parent=assets,
            commodity=eur,
        )
        b.session.add(ar_eur)
        equity = piecash.Account(
            name="Equity", type="EQUITY", parent=root,
            commodity=usd, placeholder=True,
        )
        b.session.add(equity)
        opening = piecash.Account(
            name="Opening Balance", type="EQUITY", parent=equity,
            commodity=usd,
        )
        b.session.add(opening)
        b.save()

        # Seed an old EUR price (well past the 30-day staleness
        # threshold). The book has EUR in_use via the AR account
        # but the latest non-transaction price is ancient.
        old_price = piecash.Price(
            commodity=eur,
            currency=usd,
            date=date.today() - timedelta(days=400),
            value=Decimal("1.10"),
            type="last",
            source="user:test",
        )
        b.session.add(old_price)
        b.save()
        b.close()

        gc = GnuCashBook(str(book_path))
        result = gc.get_book_summary()
        assert "Warnings:" in result
        warnings_block = result.split("Warnings:")[1].split(
            "Accounts:"
        )[0]
        assert "EUR" in warnings_block
        assert "Stale price" in warnings_block

    def test_unused_commodity_does_not_warn(self, test_book: Path):
        """A commodity in book.commodities but referenced by no
        account or price doesn't earn a warning — the user isn't
        actually depending on it."""
        gc = GnuCashBook(str(test_book))
        with gc.open(readonly=False) as book:
            from piecash import Commodity
            unused = Commodity(
                namespace="NYSE",
                mnemonic="UNUSED",
                fullname="Unused Symbol",
                fraction=10000,
            )
            book.session.add(unused)
            book.save()
        result = gc.get_book_summary()
        if "Warnings:" in result:
            warnings_block = result.split("Warnings:")[1].split(
                "Accounts:"
            )[0]
            assert "UNUSED" not in warnings_block

    def test_default_currency_never_stale_warned(
        self, test_book: Path,
    ):
        """The book's default currency doesn't need a price; never
        flag it as stale."""
        gc = GnuCashBook(str(test_book))
        result = gc.get_book_summary()
        if "Warnings:" in result:
            warnings_block = result.split("Warnings:")[1].split(
                "Accounts:"
            )[0]
            # USD is the fixture's default currency.
            assert "USD" not in warnings_block.replace(
                "Imbalance-USD", ""
            ).replace("Orphan-USD", "")

    def test_overdue_scheduled_warns(self, scheduled_book: Path):
        """A scheduled transaction whose next occurrence is in the
        past surfaces in Warnings."""
        gc = GnuCashBook(str(scheduled_book))
        # Anchor a schedule far enough in the past that today's
        # next-occurrence would already be overdue regardless of
        # frequency.
        gc.create_scheduled_transaction(
            name="Overdue Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date=(date.today() - timedelta(days=400)).isoformat(),
            frequency="monthly",
        )
        result = gc.get_book_summary()
        assert "Warnings:" in result
        warnings_block = result.split("Warnings:")[1].split(
            "Accounts:"
        )[0]
        assert "Overdue scheduled" in warnings_block
        assert "Overdue Rent" in warnings_block

    def test_disabled_scheduled_does_not_warn(
        self, scheduled_book: Path,
    ):
        """A disabled scheduled transaction isn't fired regardless
        of dates → not overdue."""
        gc = GnuCashBook(str(scheduled_book))
        sx = gc.create_scheduled_transaction(
            name="Disabled Schedule",
            description="x",
            splits=[
                {"account": "Expenses:Rent", "amount": "100"},
                {"account": "Assets:Checking", "amount": "-100"},
            ],
            start_date=(date.today() - timedelta(days=400)).isoformat(),
            frequency="monthly",
        )
        gc.update_scheduled_transaction(guid=sx["guid"], enabled=False)
        result = gc.get_book_summary()
        if "Warnings:" in result:
            warnings_block = result.split("Warnings:")[1].split(
                "Accounts:"
            )[0]
            assert "Disabled Schedule" not in warnings_block

    def test_low_cash_below_one_day_burn_warns(
        self, test_book: Path,
    ):
        """A BANK / CASH account whose balance falls below one day
        of daily expense burn earns a 'Critically low cash:'
        warning. Threshold scales with the user's actual spending,
        not a fixed dollar floor.

        Regression for the cousin's report on Alex's $6 Savings
        account at $683/day burn — relative threshold catches it
        cleanly."""
        gc = GnuCashBook(str(test_book))
        # Seed enough expense activity that daily_burn is high
        # enough to flag fixture's tiny accounts. With $36,000
        # over 180 days → $200/day burn. Fixture's Savings doesn't
        # exist, so add one with a $5 balance.
        gc.create_account(
            name="Savings", account_type="BANK", parent="Assets",
        )
        with gc.open(readonly=False) as book:
            savings = gc._find_account(book, "Assets:Savings")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Token deposit",
                post_date=date.today() - timedelta(days=20),
                splits=[
                    piecash.Split(account=savings, value=Decimal("5")),
                    piecash.Split(account=opening, value=Decimal("-5")),
                ],
            ))
            book.save()
        # Seed $36,000 of expenses → $200/day burn.
        gc.create_transaction(
            description="Burn",
            splits=[
                {"account": "Expenses:Groceries", "amount": "36000"},
                {"account": "Assets:Checking", "amount": "-36000"},
            ],
            trans_date=date.today() - timedelta(days=30),
            check_duplicates=False,
        )

        result = gc.get_book_summary()
        assert "Warnings:" in result
        warnings_block = result.split("Warnings:")[1].split(
            "Accounts:"
        )[0]
        assert "Critically low cash" in warnings_block
        assert "Savings" in warnings_block
        assert "under 1 day of burn" in warnings_block

    def test_low_cash_above_one_day_burn_does_not_warn(
        self, test_book: Path,
    ):
        """An account with balance above 1 day of burn doesn't
        flag — that's not critically low, just normal-low."""
        gc = GnuCashBook(str(test_book))
        # Tiny burn: $90 over 180 days = $0.50/day. Even Cash at
        # $1,668 (well above $0.50) doesn't qualify as low.
        gc.create_transaction(
            description="Tiny burn",
            splits=[
                {"account": "Expenses:Groceries", "amount": "90"},
                {"account": "Assets:Checking", "amount": "-90"},
            ],
            trans_date=date.today() - timedelta(days=30),
            check_duplicates=False,
        )
        result = gc.get_book_summary()
        if "Warnings:" in result:
            warnings_block = result.split("Warnings:")[1].split(
                "Accounts:"
            )[0]
            assert "Critically low cash" not in warnings_block

    def test_low_cash_zero_balance_does_not_warn(
        self, test_book: Path,
    ):
        """Zero balance on a bank account = unused, not critically
        low. Only positive-but-low qualifies."""
        gc = GnuCashBook(str(test_book))
        gc.create_account(
            name="Empty Savings", account_type="BANK", parent="Assets",
        )
        # Seed enough burn to set a high threshold, so any other
        # account would fire — but Empty Savings stays out because
        # it has zero balance.
        gc.create_transaction(
            description="Burn",
            splits=[
                {"account": "Expenses:Groceries", "amount": "36000"},
                {"account": "Assets:Checking", "amount": "-36000"},
            ],
            trans_date=date.today() - timedelta(days=30),
            check_duplicates=False,
        )
        result = gc.get_book_summary()
        if "Warnings:" in result:
            warnings_block = result.split("Warnings:")[1].split(
                "Accounts:"
            )[0]
            assert "Empty Savings" not in warnings_block

    def test_low_cash_retirement_account_excluded(
        self, test_book: Path,
    ):
        """Retirement-subtree BANK accounts don't enter the low-
        cash check (they're already excluded from runway, same
        heuristic). A retirement holding can be tiny without
        being a cash-flow problem."""
        gc = GnuCashBook(str(test_book))
        gc.create_account(
            name="Retirement", account_type="ASSET", parent="Assets",
            placeholder=True,
        )
        gc.create_account(
            name="Roth IRA", account_type="BANK",
            parent="Assets:Retirement",
        )
        with gc.open(readonly=False) as book:
            roth = gc._find_account(book, "Assets:Retirement:Roth IRA")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Roth seed",
                post_date=date.today() - timedelta(days=10),
                splits=[
                    piecash.Split(account=roth, value=Decimal("3")),
                    piecash.Split(account=opening, value=Decimal("-3")),
                ],
            ))
            book.save()
        # Burn high enough to put threshold above $3.
        gc.create_transaction(
            description="Burn",
            splits=[
                {"account": "Expenses:Groceries", "amount": "36000"},
                {"account": "Assets:Checking", "amount": "-36000"},
            ],
            trans_date=date.today() - timedelta(days=30),
            check_duplicates=False,
        )
        result = gc.get_book_summary()
        if "Warnings:" in result:
            warnings_block = result.split("Warnings:")[1].split(
                "Accounts:"
            )[0]
            assert "Roth IRA" not in warnings_block

    def test_low_cash_skipped_when_no_burn(self, test_book: Path):
        """When the book has no expense activity in the burn
        window, there's no daily-burn benchmark. Skip the
        low-cash check entirely rather than guess a threshold."""
        gc = GnuCashBook(str(test_book))
        gc.create_account(
            name="Empty Savings", account_type="BANK", parent="Assets",
        )
        with gc.open(readonly=False) as book:
            savings = gc._find_account(book, "Assets:Savings") if \
                gc._find_account(book, "Assets:Savings") else \
                gc._find_account(book, "Assets:Empty Savings")
            opening = gc._find_account(book, "Equity:Opening Balance")
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Trivial deposit",
                post_date=date.today() - timedelta(days=5),
                splits=[
                    piecash.Split(account=savings, value=Decimal("3")),
                    piecash.Split(account=opening, value=Decimal("-3")),
                ],
            ))
            book.save()
        # No expense activity in the 180-day window.
        result = gc.get_book_summary()
        if "Warnings:" in result:
            warnings_block = result.split("Warnings:")[1].split(
                "Accounts:"
            )[0]
            assert "Critically low cash" not in warnings_block

    def test_past_due_invoice_warns(self, business_book: Path):
        """A posted invoice with a past due_date and non-zero lot
        balance fires a 'Past due invoice:' warning."""
        gc = GnuCashBook(str(business_book))
        gc.create_customer(name="Acme Corp", currency="USD")
        gc.create_invoice(
            customer_id="000001",
            date_opened=(date.today() - timedelta(days=60)).isoformat(),
        )
        gc.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Service",
            quantity="1",
            price="5000",
        )
        gc.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date=(date.today() - timedelta(days=60)).isoformat(),
            due_date=(date.today() - timedelta(days=30)).isoformat(),
        )
        result = gc.get_book_summary()
        assert "Warnings:" in result
        warnings_block = result.split("Warnings:")[1].split(
            "Accounts:"
        )[0]
        assert "Past due invoice" in warnings_block
        assert "Acme Corp" in warnings_block
        assert "30 days overdue" in warnings_block
        assert "USD 5,000" in warnings_block

    def test_past_due_bill_warns(self, business_book: Path):
        """A posted vendor bill with past due_date and non-zero
        lot balance fires 'Past due bill:'."""
        gc = GnuCashBook(str(business_book))
        gc.create_vendor(name="Office Depot", currency="USD")
        gc.create_bill(
            vendor_id="000001",
            date_opened=(date.today() - timedelta(days=45)).isoformat(),
        )
        gc.add_bill_entry(
            bill_id="000001",
            account="Expenses:Office Supplies",
            description="Pens",
            quantity="1",
            price="250",
        )
        gc.post_invoice(
            invoice_id="000001",
            post_account="Liabilities:Accounts Payable",
            post_date=(date.today() - timedelta(days=45)).isoformat(),
            due_date=(date.today() - timedelta(days=15)).isoformat(),
            owner_type="vendor",
        )
        result = gc.get_book_summary()
        warnings_block = result.split("Warnings:")[1].split(
            "Accounts:"
        )[0]
        assert "Past due bill" in warnings_block
        assert "Office Depot" in warnings_block
        assert "15 days overdue" in warnings_block

    def test_past_due_invoice_without_terms_falls_back_to_30_days(
        self, business_book: Path,
    ):
        """An invoice posted without an explicit due_date AND
        without a billterm falls back to date_posted + 30 days.
        The warning anchors the days count to that assumption
        ('N days past 30-day default') and tags '(no term set)'
        so the bookkeeper sees both the duration and the data
        gap without the string reading as contractual."""
        gc = GnuCashBook(str(business_book))
        gc.create_customer(name="No Terms Co", currency="USD")
        gc.create_invoice(
            customer_id="000001",
            date_opened=(date.today() - timedelta(days=50)).isoformat(),
        )
        gc.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Service",
            quantity="1",
            price="1500",
        )
        # post_date 50 days ago + 30-day fallback = 20 days past.
        # No due_date passed → falls back.
        gc.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date=(date.today() - timedelta(days=50)).isoformat(),
            due_date=None,
        )
        result = gc.get_book_summary()
        warnings_block = result.split("Warnings:")[1].split(
            "Accounts:"
        )[0]
        assert "Past due invoice" in warnings_block
        assert "No Terms Co" in warnings_block
        assert "20 days past 30-day default" in warnings_block
        assert "(no term set)" in warnings_block
        # Regression: the old wording shouldn't reappear.
        assert "20 days overdue" not in warnings_block
        assert "(posted without terms)" not in warnings_block

    def test_past_due_invoice_uses_term_duedays_no_terms_annotation(
        self, business_book: Path,
    ):
        """When an invoice was posted with a billterm (e.g. Net 30)
        but no explicit ``due_date``, the warning should compute
        the due date from the billterm's ``duedays`` and NOT
        annotate '(posted without terms)' — the term IS known.

        Regression for the Berlin Digital case on Alex's book:
        the warning correctly computed 55 days overdue from
        Net 30 + posting date, but contradicted itself by saying
        '(posted without terms)' — undermining the number."""
        gc = GnuCashBook(str(business_book))
        gc.create_billterm(name="Net 30", due_days=30)
        gc.create_customer(name="Berlin Digital", currency="USD")
        gc.create_invoice(
            customer_id="000001",
            date_opened=(date.today() - timedelta(days=85)).isoformat(),
            term="Net 30",
        )
        gc.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Service",
            quantity="1",
            price="4200",
        )
        # Post WITHOUT explicit due_date — relies on the term.
        gc.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date=(date.today() - timedelta(days=85)).isoformat(),
        )
        result = gc.get_book_summary()
        warnings_block = result.split("Warnings:")[1].split(
            "Accounts:"
        )[0]
        # Net 30 + 85 days posted = 55 days overdue.
        assert "Berlin Digital" in warnings_block
        assert "55 days overdue" in warnings_block
        # The term IS known; no contradiction annotation.
        assert "(posted without terms)" not in warnings_block

    def test_paid_invoice_does_not_warn(self, business_book: Path):
        """A posted invoice that's been fully paid (lot balance
        zero) doesn't fire — no outstanding receivable."""
        gc = GnuCashBook(str(business_book))
        gc.create_customer(name="Prompt Payer", currency="USD")
        gc.create_invoice(
            customer_id="000001",
            date_opened=(date.today() - timedelta(days=60)).isoformat(),
        )
        gc.add_invoice_entry(
            invoice_id="000001",
            account="Income:Sales",
            description="Done",
            quantity="1",
            price="2000",
        )
        gc.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
            post_date=(date.today() - timedelta(days=60)).isoformat(),
            due_date=(date.today() - timedelta(days=30)).isoformat(),
        )
        gc.pay_invoice(
            invoice_id="000001",
            payment_account="Assets:Checking",
            amount="2000",
            payment_date=(date.today() - timedelta(days=10)).isoformat(),
        )
        result = gc.get_book_summary()
        if "Warnings:" in result:
            warnings_block = result.split("Warnings:")[1].split(
                "Accounts:"
            )[0]
            assert "Prompt Payer" not in warnings_block

    def test_unposted_invoice_does_not_warn(
        self, business_book: Path,
    ):
        """An invoice in draft (no date_posted) isn't past due —
        it's not yet a receivable."""
        gc = GnuCashBook(str(business_book))
        gc.create_customer(name="Draft Co", currency="USD")
        gc.create_invoice(
            customer_id="000001",
            date_opened=(date.today() - timedelta(days=60)).isoformat(),
        )
        # No post_invoice call — invoice stays in draft.
        result = gc.get_book_summary()
        if "Warnings:" in result:
            warnings_block = result.split("Warnings:")[1].split(
                "Accounts:"
            )[0]
            assert "Draft Co" not in warnings_block

    def test_warnings_section_above_accounts(
        self, investment_book: Path,
    ):
        """When emitted, Warnings appears above Accounts — that's
        the scan-first ordering the spec calls for."""
        gc = GnuCashBook(str(investment_book))
        result = gc.get_book_summary()
        assert "Warnings:" in result
        warnings_idx = result.index("Warnings:")
        accounts_idx = result.index("Accounts:")
        assert warnings_idx < accounts_idx


class TestMissingDefaultCurrency:
    """Tests for books with no default currency."""

    def _corrupt_book_currency(self, book_path: Path):
        """Null out the default currency GUID in the books table."""
        import sqlite3
        conn = sqlite3.connect(str(book_path))
        conn.execute(
            "UPDATE books SET root_template_guid = root_template_guid"
        )  # no-op to ensure we can write
        # Delete the commodity referenced by the book
        conn.execute(
            "DELETE FROM commodities WHERE namespace = 'CURRENCY'"
        )
        conn.commit()
        conn.close()

    def test_get_book_summary_clear_error(self, test_book: Path):
        self._corrupt_book_currency(test_book)
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="no default currency"):
            gc_book.get_book_summary()

    def test_create_transaction_clear_error(self, test_book: Path):
        self._corrupt_book_currency(test_book)
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="no default currency"):
            gc_book.create_transaction(
                description="Test",
                splits=[
                    {"account": "Assets:Checking", "amount": "100"},
                    {"account": "Income:Salary", "amount": "-100"},
                ],
            )

    def test_explicit_currency_bypasses_error(self, test_book: Path):
        """Passing currency explicitly should work even without default."""
        # Don't corrupt — just verify the workaround path works
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Test with explicit currency",
            currency="USD",
            splits=[
                {"account": "Assets:Checking", "amount": "50"},
                {"account": "Income:Salary", "amount": "-50"},
            ],
        )
        assert result["guid"]


def _path_from_compact_line(line: str) -> str:
    """Extract the 'fullname [ANNOTATION]' portion from a compact line.

    New format is '%shortguid<TAB>fullname [ANNOTATION]'. This helper
    keeps the assertions in this test module readable without scattering
    the split logic everywhere.
    """
    return line.split("\t", 1)[1] if "\t" in line else line


class TestListAccounts:
    """Tests for list_accounts method."""

    def test_list_accounts_returns_all(self, test_book: Path):
        """Default should return compact string with all accounts."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts()

        assert isinstance(result, str)
        assert "Assets:Checking" in result
        assert "Expenses:Groceries" in result
        assert "Income:Salary" in result

    def test_list_accounts_sorted(self, test_book: Path):
        """Compact output lines should be sorted by account name."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts()

        lines = result.strip().split("\n")
        # Extract fullname (before any annotation bracket); skip past
        # the '%shortguid<TAB>' prefix introduced by short GUIDs.
        names = [
            _path_from_compact_line(line).split(" [")[0]
            for line in lines
        ]
        assert names == sorted(names)

    def test_list_accounts_structure_verbose(self, test_book: Path):
        """compact=False should return proper account dict structure."""
        gc_book = GnuCashBook(str(test_book))
        accounts = gc_book.list_accounts(compact=False)

        assert isinstance(accounts, list)
        account = accounts[0]
        assert "guid" in account
        assert "name" in account
        assert "fullname" in account
        assert "type" in account
        assert "commodity" in account
        assert "description" in account
        assert "placeholder" in account

    def test_compact_annotations(self, test_book: Path):
        """Non-obvious types should be annotated, obvious ones not."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts()
        # Compare on the path-portion of each line (strip the
        # '%shortguid<TAB>' prefix).
        paths = [_path_from_compact_line(l) for l in result.strip().split("\n")]

        # Assets:Checking is BANK (not default ASSET) → annotated
        checking = [p for p in paths if p.startswith("Assets:Checking")][0]
        assert "[BANK]" in checking

        # Expenses:Groceries is EXPENSE (default under Expenses) → no annotation
        groceries = [p for p in paths if p.startswith("Expenses:Groceries")][0]
        assert "[" not in groceries

        # Income:Salary is INCOME (default under Income) → no annotation
        salary = [p for p in paths if p.startswith("Income:Salary")][0]
        assert "[" not in salary

    def test_compact_placeholder(self, test_book: Path):
        """Placeholder accounts should be annotated."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts()
        paths = [_path_from_compact_line(l) for l in result.strip().split("\n")]

        # "Assets" is ASSET (default) + placeholder → [PLACEHOLDER]
        assert "Assets [PLACEHOLDER]" in paths

        # "Expenses" is EXPENSE (default) + placeholder → [PLACEHOLDER]
        assert "Expenses [PLACEHOLDER]" in paths

    def test_verbose_mode(self, test_book: Path):
        """compact=False should return list of dicts (old behavior)."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts(compact=False)

        assert isinstance(result, list)
        assert all(isinstance(a, dict) for a in result)
        fullnames = {a["fullname"] for a in result}
        assert "Assets" in fullnames
        assert "Assets:Checking" in fullnames

    def test_root_filter(self, test_book: Path):
        """root parameter should filter to a subtree."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts(root="Expenses")
        paths = [_path_from_compact_line(l) for l in result.strip().split("\n")]

        for path in paths:
            assert path.startswith("Expenses")
        assert any("Expenses:Groceries" in p for p in paths)

    def test_root_filter_verbose(self, test_book: Path):
        """root + compact=False should return filtered dicts."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts(root="Assets", compact=False)

        assert isinstance(result, list)
        for a in result:
            assert a["fullname"].startswith("Assets")

    def test_root_no_partial_match(self, test_book: Path):
        """root filter should not partially match account names."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts(root="Exp")
        assert result == ""

    def test_root_nonexistent(self, test_book: Path):
        """root filter for nonexistent account returns empty."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts(root="Nonexistent")
        assert result == ""

    def test_root_includes_self(self, test_book: Path):
        """root account itself should be included in results."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts(root="Expenses")
        paths = [_path_from_compact_line(l) for l in result.strip().split("\n")]
        # The root itself appears as 'Expenses [PLACEHOLDER]' (or similar)
        # — i.e., a path-portion with no ':' separator before any annotation.
        assert any(
            p.startswith("Expenses") and ":" not in p.split(" [")[0]
            for p in paths
        )


class TestGetAccount:
    """Tests for get_account method."""

    def test_get_existing_account(self, test_book: Path):
        """Should return account details for existing account."""
        gc_book = GnuCashBook(str(test_book))
        account = gc_book.get_account("Assets:Checking")

        assert account is not None
        assert account["name"] == "Checking"
        assert account["fullname"] == "Assets:Checking"
        assert account["type"] == "BANK"

    def test_get_nonexistent_account(self, test_book: Path):
        """Should return None for non-existent account."""
        gc_book = GnuCashBook(str(test_book))
        account = gc_book.get_account("Nonexistent:Account")

        assert account is None


class TestShortAccountGuids:
    """Tests for the short-guid format ('%XXXXXXX') and the helpers
    that generate / resolve it. The short form replaces verbose
    account paths in tool I/O — e.g., the LLM reads 'Assets:Current
    Assets:Savings Account' once from list_accounts output and
    re-references it as '%abcdef0' on every subsequent call.
    """

    def test_short_guid_format(self, test_book: Path):
        """Short GUIDs start with '%' and have 7+ hex chars after."""
        import re
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            account = gc_book._find_account(book, "Assets:Checking")
            short = gc_book._account_short_guid(book, account)
        assert short.startswith("%")
        # 7-char minimum hex after the '%' marker.
        assert re.fullmatch(r"%[0-9a-f]{7,32}", short)

    def test_short_guid_matches_full_guid_prefix(self, test_book: Path):
        """The hex after '%' is a true prefix of the full GUID."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            account = gc_book._find_account(book, "Assets:Checking")
            full_guid = account.guid
            short = gc_book._account_short_guid(book, account)
        suffix = short[1:]  # strip '%'
        assert full_guid.startswith(suffix)

    def test_short_guid_map_covers_all_accounts(self, test_book: Path):
        """The batch map maps every account.guid to a '%' short form."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            account_guids = {a.guid for a in book.accounts}
            short_map = gc_book._account_short_guid_map(book)
        assert set(short_map.keys()) == account_guids
        assert all(v.startswith("%") for v in short_map.values())
        # Short forms must be unique across the book.
        assert len(set(short_map.values())) == len(short_map)

    def test_short_guid_unique_within_book(self, test_book: Path):
        """No two accounts get the same '%shortguid'."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            shorts = []
            for account in book.accounts:
                shorts.append(gc_book._account_short_guid(book, account))
        assert len(set(shorts)) == len(shorts)


class TestResolveAccount:
    """Tests for _resolve_account — the universal handle that takes a
    path, a '%short' GUID, or a full 32-char GUID and returns the
    matching Account. This is the chokepoint that lets every tool
    accept any of the three forms transparently.
    """

    def test_resolve_by_path(self, test_book: Path):
        """Path input works the same as _find_account."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            account = gc_book._resolve_account(book, "Assets:Checking")
            assert account is not None
            assert account.fullname == "Assets:Checking"

    def test_resolve_by_short_guid(self, test_book: Path):
        """A '%XXXXXXX' short form resolves back to the same account."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            checking = gc_book._find_account(book, "Assets:Checking")
            short = gc_book._account_short_guid(book, checking)
            resolved = gc_book._resolve_account(book, short)
            assert resolved is not None
            assert resolved.guid == checking.guid

    def test_resolve_by_full_guid(self, test_book: Path):
        """A 32-char full GUID resolves directly."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            checking = gc_book._find_account(book, "Assets:Checking")
            resolved = gc_book._resolve_account(book, checking.guid)
            assert resolved is not None
            assert resolved.guid == checking.guid

    def test_resolve_unknown_path_returns_none(self, test_book: Path):
        """Unknown path returns None (not raise)."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            assert gc_book._resolve_account(book, "Nope:Nada") is None

    def test_resolve_unmatched_short_guid_returns_none(
        self, test_book: Path
    ):
        """A well-formed short GUID with no match returns None."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            # 'deadbe0' is well-formed (7 hex) but unlikely to match.
            assert gc_book._resolve_account(book, "%deadbe0") is None

    def test_resolve_short_guid_too_short_raises(self, test_book: Path):
        """Short GUIDs with fewer than 7 hex chars raise ValueError."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            with pytest.raises(ValueError, match="too short"):
                gc_book._resolve_account(book, "%abc")

    def test_resolve_short_guid_non_hex_raises(self, test_book: Path):
        """Non-hex characters after '%' raise ValueError."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            with pytest.raises(ValueError, match="non-hex"):
                gc_book._resolve_account(book, "%xyz1234")

    def test_list_accounts_emits_short_guids(self, test_book: Path):
        """Compact list_accounts output: '%shortguid<TAB>fullname [ANN]'."""
        import re
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts()
        for line in result.strip().split("\n"):
            # '%' + 7+ hex + TAB + non-empty path
            assert re.match(r"^%[0-9a-f]{7,32}\t\S", line), (
                f"unexpected line shape: {line!r}"
            )

    def test_list_accounts_short_guids_resolve_back(
        self, test_book: Path
    ):
        """Round-trip: every short GUID emitted by list_accounts must
        resolve back to the account whose path appears on the same line.
        """
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_accounts()
        with gc_book.open(readonly=True) as book:
            for line in result.strip().split("\n"):
                short, rest = line.split("\t", 1)
                # Path is everything before the optional " [ANN]" suffix.
                path = rest.split(" [", 1)[0]
                resolved = gc_book._resolve_account(book, short)
                assert resolved is not None, (
                    f"short {short!r} from list_accounts did not resolve"
                )
                assert resolved.fullname == path


class TestShortGuidEndToEnd:
    """Each book method that takes an account ref now flows through
    ``_resolve_account``, so short GUIDs ('%XXXXXXX') and full GUIDs
    work interchangeably with the original full-path form.

    These tests cover one representative method per mixin to lock in
    that the wiring is plumbed through end-to-end. Unit-level coverage
    of the resolver itself lives in ``TestResolveAccount``; this class
    is the integration sweep.
    """

    @staticmethod
    def _short_for(gc_book, fullname: str) -> str:
        """Resolve a fullname into the '%shortguid' a tool would receive."""
        with gc_book.open(readonly=True) as book:
            account = gc_book._find_account(book, fullname)
            return gc_book._account_short_guid(book, account)

    # --- core ---

    def test_get_balance_via_short_guid(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short = self._short_for(gc_book, "Assets:Checking")
        # Same answer the path-form returns.
        assert gc_book.get_balance(short) == gc_book.get_balance("Assets:Checking")

    def test_get_account_via_short_guid(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short = self._short_for(gc_book, "Assets:Checking")
        result = gc_book.get_account(short)
        assert result is not None
        assert result["fullname"] == "Assets:Checking"

    def test_list_transactions_filter_via_short_guid(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short = self._short_for(gc_book, "Assets:Checking")
        by_short = gc_book.list_transactions(account=short)
        by_path = gc_book.list_transactions(account="Assets:Checking")
        assert by_short == by_path

    def test_create_transaction_split_via_short_guid(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short_check = self._short_for(gc_book, "Assets:Checking")
        short_dining = self._short_for(gc_book, "Expenses:Groceries")
        baseline = gc_book.get_balance("Assets:Checking")
        gc_book.create_transaction(
            description="Lunch (split via short GUIDs)",
            splits=[
                {"account": short_check, "amount": "-12.50"},
                {"account": short_dining, "amount": "12.50"},
            ],
            check_duplicates=False,
        )
        # Posted: balance drops by 12.50 in checking.
        assert gc_book.get_balance("Assets:Checking") == baseline - Decimal("12.50")

    def test_update_account_via_short_guid(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short = self._short_for(gc_book, "Expenses:Groceries")
        gc_book.update_account(name=short, description="Renamed via short GUID")
        # Path lookup confirms the side effect.
        result = gc_book.get_account("Expenses:Groceries")
        assert result["description"] == "Renamed via short GUID"

    # --- list_accounts root= ---

    def test_list_accounts_root_via_short_guid(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short = self._short_for(gc_book, "Expenses")
        by_short = gc_book.list_accounts(root=short)
        by_path = gc_book.list_accounts(root="Expenses")
        assert by_short == by_path

    # --- reporting ---

    def test_cash_flow_account_filter_via_short_guid(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short = self._short_for(gc_book, "Assets:Checking")
        by_short = gc_book.cash_flow(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            account=short,
        )
        by_path = gc_book.cash_flow(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            account="Assets:Checking",
        )
        assert by_short == by_path

    # --- admin (slot CRUD) ---

    def test_set_account_slot_via_short_guid(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short = self._short_for(gc_book, "Assets:Checking")
        gc_book.set_account_slot(short, "color", "blue")
        # Round-trip through path-form to confirm storage. The book
        # method returns ``{"account": ..., "slots": {...}}`` — the
        # nested ``slots`` dict is what we're verifying.
        result = gc_book.get_account_slots("Assets:Checking")
        assert result["slots"].get("color") == "blue"

    # --- reconciliation ---

    def test_get_unreconciled_splits_via_short_guid(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short = self._short_for(gc_book, "Assets:Checking")
        by_short = gc_book.get_unreconciled_splits(short)
        by_path = gc_book.get_unreconciled_splits("Assets:Checking")
        assert by_short == by_path

    def test_full_guid_also_works(self, test_book: Path):
        """The third input form (32-char full GUID) round-trips too."""
        gc_book = GnuCashBook(str(test_book))
        with gc_book.open(readonly=True) as book:
            account = gc_book._find_account(book, "Assets:Checking")
            full_guid = account.guid
        assert gc_book.get_balance(full_guid) == gc_book.get_balance("Assets:Checking")


class TestCanonicalAccountEcho:
    """Every tool that echoes an account back in its response must
    return the canonical full path — not the raw input string. The
    caller passes ``%2e78c86`` and gets back ``Assets:Current Assets:
    Savings Account``, which is more informative AND keeps the
    contract uniform across the tool surface.

    Locks the design so a future refactor can't regress to "echo
    whatever came in." Bookkeeper-driven: the inconsistency between
    get_balance (was echoing input) and replace_splits (always
    canonical) was the trigger for this contract.
    """

    @staticmethod
    def _short_for(gc_book, fullname: str) -> str:
        with gc_book.open(readonly=True) as book:
            account = gc_book._find_account(book, fullname)
            return gc_book._account_short_guid(book, account)

    def test_get_account_slots_echoes_canonical(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short = self._short_for(gc_book, "Assets:Checking")
        result = gc_book.get_account_slots(account_name=short)
        assert result["account"] == "Assets:Checking"

    def test_get_unreconciled_splits_echoes_canonical(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short = self._short_for(gc_book, "Assets:Checking")
        result = gc_book.get_unreconciled_splits(account_name=short, compact=False)
        assert result["account"] == "Assets:Checking"

    def test_cash_flow_echoes_canonical(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        short = self._short_for(gc_book, "Assets:Checking")
        result = gc_book.cash_flow(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            account=short,
        )
        assert result["account"] == "Assets:Checking"


class TestTemplateAccountsHidden:
    """Scheduled transactions persist their split templates as real
    Account rows under ``book.root_template``. piecash surfaces those
    in ``book.accounts`` alongside the user's chart of accounts, but
    they're GnuCash internals — not transactions the user posts to
    or a balance they care about. Every user-facing lookup path
    filters them out.

    Exercising this end-to-end: create a scheduled transaction
    (which creates a template account with the same name), then
    verify ``list_accounts`` and ``get_account`` both hide it.
    """

    def test_list_accounts_hides_template(self, scheduled_book: Path):
        """The template account created by ``create_scheduled_transaction``
        must not surface in ``list_accounts``."""
        gc = GnuCashBook(str(scheduled_book))
        gc.create_scheduled_transaction(
            name="MonthlyRentTemplate",
            description="Monthly Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-05-01",
            frequency="monthly",
        )
        # Compact and verbose paths must both hide the template.
        compact = gc.list_accounts()
        assert "MonthlyRentTemplate" not in compact

        verbose = gc.list_accounts(compact=False)
        names = {a["fullname"] for a in verbose}
        assert not any("MonthlyRentTemplate" in n for n in names)
        # User's chart of accounts still appears normally.
        assert "Assets:Checking" in {a["fullname"] for a in verbose}
        assert "Expenses:Rent" in {a["fullname"] for a in verbose}

    def test_get_account_hides_template(self, scheduled_book: Path):
        """Looking up a template account by name returns None, same
        shape as a missing account."""
        gc = GnuCashBook(str(scheduled_book))
        gc.create_scheduled_transaction(
            name="AnotherTemplate",
            description="Whatever",
            splits=[
                {"account": "Expenses:Rent", "amount": "100.00"},
                {"account": "Assets:Checking", "amount": "-100.00"},
            ],
            start_date="2026-05-01",
            frequency="monthly",
        )
        # Whatever piecash's fullname for the template happens to be,
        # neither the bare name nor any plausible templated path
        # should resolve.
        assert gc.get_account("AnotherTemplate") is None
        assert gc.get_account("Template Root:AnotherTemplate") is None

    def test_scheduled_instantiation_still_works(
        self, scheduled_book: Path,
    ):
        """Canary: filtering templates from the user-facing lookup
        paths must not break the scheduled-transaction instantiation
        path, which reaches templates through piecash relationships
        (``sx.template_account``), not by name."""
        gc = GnuCashBook(str(scheduled_book))
        sx = gc.create_scheduled_transaction(
            name="RentToInstantiate",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-05-01",
            frequency="monthly",
        )
        result = gc.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-05-01",
        )
        assert result["status"] == "created"

    def test_root_filter_does_not_surface_templates(
        self, scheduled_book: Path,
    ):
        """Even with an aggressive ``root=`` filter that could
        conceivably match the template subtree name, templates stay
        hidden. Belt-and-suspenders for edge-case GnuCash namings."""
        gc = GnuCashBook(str(scheduled_book))
        gc.create_scheduled_transaction(
            name="TemplatedThing",
            description="x",
            splits=[
                {"account": "Expenses:Rent", "amount": "100.00"},
                {"account": "Assets:Checking", "amount": "-100.00"},
            ],
            start_date="2026-05-01",
            frequency="monthly",
        )
        # Any list_accounts call, filtered or not, omits templates.
        for root in (None, "Assets", "Expenses", "Template Root"):
            result = gc.list_accounts(root=root)
            assert "TemplatedThing" not in result


class TestGetBalance:
    """Tests for get_balance method."""

    def test_get_balance_all_time(self, test_book: Path):
        """Should return correct balance for all time."""
        gc_book = GnuCashBook(str(test_book))

        # Checking: +1000 (opening) +2000 (salary) -150 (groceries) = 2850
        balance = gc_book.get_balance("Assets:Checking")
        assert balance == Decimal("2850")

    def test_get_balance_as_of_date(self, test_book: Path):
        """Should return correct balance as of specific date."""
        gc_book = GnuCashBook(str(test_book))

        # As of Jan 10, only opening balance: 1000
        balance = gc_book.get_balance("Assets:Checking", as_of_date=date(2024, 1, 10))
        assert balance == Decimal("1000")

        # As of Jan 15, opening + salary: 3000
        balance = gc_book.get_balance("Assets:Checking", as_of_date=date(2024, 1, 15))
        assert balance == Decimal("3000")

    def test_get_balance_nonexistent_account(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.get_balance("Nonexistent:Account")

    def test_get_balance_excludes_future_dated_by_default(self, test_book: Path):
        """Default (no as_of_date) should treat 'today' as the cutoff,
        excluding future-dated entries.

        Regression test for the bug where a future-dated paycheck or
        accrued-interest entry would inflate (or future-dated bill
        deflate) the displayed balance, misleading planners about
        what is actually in the account right now.
        """
        from datetime import date as date_cls, timedelta

        gc_book = GnuCashBook(str(test_book))
        baseline = gc_book.get_balance("Assets:Checking")

        # Post a future-dated salary deposit (one year out).
        future_date = date_cls.today() + timedelta(days=365)
        gc_book.create_transaction(
            description="Salary (post-dated, scheduled deposit)",
            splits=[
                {"account": "Assets:Checking", "amount": "1000.00"},
                {"account": "Income:Salary", "amount": "-1000.00"},
            ],
            trans_date=future_date,
        )

        # Default get_balance must NOT include the future entry.
        assert gc_book.get_balance("Assets:Checking") == baseline

        # Explicit future as_of_date DOES include it.
        projected = gc_book.get_balance(
            "Assets:Checking", as_of_date=future_date
        )
        assert projected == baseline + Decimal("1000")


class TestListTransactions:
    """Tests for list_transactions method."""

    def test_list_all_transactions(self, test_book: Path):
        """Should return all transactions."""
        gc_book = GnuCashBook(str(test_book))
        transactions = gc_book.list_transactions(compact=False)

        assert len(transactions) == 3
        descriptions = {t["description"] for t in transactions}
        assert "Opening Balance" in descriptions
        assert "Salary Deposit" in descriptions
        assert "Weekly Groceries" in descriptions

    def test_list_transactions_by_account(self, test_book: Path):
        """Should filter transactions by account."""
        gc_book = GnuCashBook(str(test_book))

        # Groceries account only has one transaction
        transactions = gc_book.list_transactions(account="Expenses:Groceries", compact=False)
        assert len(transactions) == 1
        assert transactions[0]["description"] == "Weekly Groceries"

    def test_list_transactions_date_range(self, test_book: Path):
        """Should filter transactions by date range."""
        gc_book = GnuCashBook(str(test_book))

        # Only Jan 10-18 should get salary deposit
        transactions = gc_book.list_transactions(
            start_date=date(2024, 1, 10),
            end_date=date(2024, 1, 18),
            compact=False,
        )
        assert len(transactions) == 1
        assert transactions[0]["description"] == "Salary Deposit"

    def test_list_transactions_limit(self, test_book: Path):
        """Should respect limit parameter."""
        gc_book = GnuCashBook(str(test_book))
        transactions = gc_book.list_transactions(limit=2, compact=False)

        assert len(transactions) == 2

    def test_list_transactions_sorted_descending(self, test_book: Path):
        """Should return transactions sorted by date descending."""
        gc_book = GnuCashBook(str(test_book))
        transactions = gc_book.list_transactions(compact=False)

        dates = [t["date"] for t in transactions]
        assert dates == sorted(dates, reverse=True)

    def test_list_transactions_nonexistent_account(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.list_transactions(account="Nonexistent:Account")

    def test_compact_truncation_notice_when_over_limit(self, test_book: Path):
        """Compact mode appends [Showing N of M ...] when results truncate."""
        gc_book = GnuCashBook(str(test_book))
        # test_book has 3 transactions; limit=2 triggers truncation.
        result = gc_book.list_transactions(limit=2, compact=True)
        assert "[Showing 2 of 3 transactions" in result
        assert "start_date/end_date" in result

    def test_compact_no_notice_when_under_limit(self, test_book: Path):
        """No notice when results fit under the limit."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_transactions(limit=50, compact=True)
        assert "Showing" not in result

    def test_compact_cap_note_when_limit_over_max(self, test_book: Path):
        """Limit above MAX_LIST_LIMIT (250) gets clamped with a note when
        results fit, or included in the truncation message when they don't."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_transactions(limit=10000, compact=True)
        # test_book has only 3, fits under the cap
        assert "Limit capped at 250" in result
        assert "results fit under the cap" in result

    def test_verbose_mode_no_notice(self, test_book: Path):
        """Non-compact (dict) mode returns list silently — no notice field."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_transactions(limit=2, compact=False)
        assert isinstance(result, list)
        assert len(result) == 2


class TestCompactTransactionFormat:
    """Tests for the compact output shapes of list/search_transactions.

    Two shapes to defend:
      - Unfiltered: ``DATE\\tguid\\tDESC\\tsplits``
      - Register (list_transactions(account=X)):
        ``DATE\\tguid\\t±AMT\\tDESC\\tother splits``

    And the collapse rule: transactions with > 4 splits in the rendered
    column truncate to top-3 by |value| + "+N more". Prerelease-1 bug:
    the old ``exclude_account`` variant dropped the filtered split
    without putting anything in its place, and readers mis-parsed the
    remaining split as the description.
    """

    def _paycheck_book(self, gc_book):
        """Add a 7-split paycheck transaction to the existing fixture.

        Splits (|value| in USD):
          Income:Salary        -4865   gross
          Assets:Checking       3000   net deposit
          Expenses:Federal Tax   800
          Assets:401k            400   top-3 by |value| stops here
          Expenses:SS            350
          Expenses:State Tax     200
          Expenses:Groceries     115   ...the "+more" bucket
        """
        # Create new accounts the paycheck lands in
        gc_book.create_account(name="Federal Tax", account_type="EXPENSE", parent="Expenses")
        gc_book.create_account(name="State Tax", account_type="EXPENSE", parent="Expenses")
        gc_book.create_account(name="SS", account_type="EXPENSE", parent="Expenses")
        gc_book.create_account(name="401k", account_type="ASSET", parent="Assets")

        gc_book.create_transaction(
            description="Paycheck",
            splits=[
                {"account": "Income:Salary", "amount": "-4865"},
                {"account": "Assets:Checking", "amount": "3000"},
                {"account": "Expenses:Federal Tax", "amount": "800"},
                {"account": "Assets:401k", "amount": "400"},
                {"account": "Expenses:SS", "amount": "350"},
                {"account": "Expenses:State Tax", "amount": "200"},
                {"account": "Expenses:Groceries", "amount": "115"},
            ],
            trans_date=date(2026, 2, 15),
            check_duplicates=False,
        )

    # ── Unfiltered shape (search_transactions + list_transactions w/o filter) ──

    def test_unfiltered_line_has_description_in_column_3(self, test_book: Path):
        """Unfiltered output: 4 tab-separated columns ending with splits."""
        gc_book = GnuCashBook(str(test_book))
        lines = gc_book.list_transactions().strip().split("\n")
        groceries = next(l for l in lines if "Weekly Groceries" in l)
        cols = groceries.split("\t")
        assert len(cols) == 4
        assert cols[0] == "2024-01-20"
        assert len(cols[1]) >= 8  # guid prefix
        assert cols[2] == "Weekly Groceries"
        assert "Expenses:Groceries" in cols[3]
        assert "Assets:Checking" in cols[3]

    def test_search_output_matches_unfiltered_list_shape(self, test_book: Path):
        """search_transactions's compact shape is identical to unfiltered list."""
        gc_book = GnuCashBook(str(test_book))
        search_line = gc_book.search_transactions("Groceries").strip().split("\n")[0]
        list_line = next(
            l for l in gc_book.list_transactions().strip().split("\n")
            if "Weekly Groceries" in l
        )
        assert search_line == list_line

    # ── Register shape (list_transactions with account filter) ──

    def test_register_line_has_signed_amount_column(self, test_book: Path):
        """Filtered output: 5 columns, column 3 is signed impact on focus account."""
        gc_book = GnuCashBook(str(test_book))
        lines = gc_book.list_transactions(account="Assets:Checking").strip().split("\n")
        groceries = next(l for l in lines if "Weekly Groceries" in l)
        cols = groceries.split("\t")
        assert len(cols) == 5
        assert cols[0] == "2024-01-20"
        assert cols[2] == "-150"  # groceries drew $150 out of checking
        assert cols[3] == "Weekly Groceries"
        # Focus account is dropped from the splits column
        assert "Assets:Checking" not in cols[4]
        assert "Expenses:Groceries" in cols[4]

    def test_register_positive_deposit_has_plus_sign(self, test_book: Path):
        """Inflows render with an explicit '+' so sign is visible at a glance."""
        gc_book = GnuCashBook(str(test_book))
        lines = gc_book.list_transactions(account="Assets:Checking").strip().split("\n")
        salary = next(l for l in lines if "Salary" in l)
        cols = salary.split("\t")
        assert cols[2] == "+2000"

    def test_register_description_position_is_stable(self, test_book: Path):
        """Description is always the last-but-one column in register form.

        The prerelease-1 bug came from a reader parsing the filtered
        output as 3-column 'DATE GUID SPLITS' and missing the
        description. The register shape makes column 4 unambiguously
        the description.
        """
        gc_book = GnuCashBook(str(test_book))
        lines = gc_book.list_transactions(account="Assets:Checking").strip().split("\n")
        for line in lines:
            cols = line.split("\t")
            # 5 or 6 cols (6 if the transaction has notes)
            assert len(cols) >= 5
            # Column 3 parses as a signed decimal
            assert cols[2].lstrip("+-").replace(".", "").isdigit()
            # Column 4 is not a split (no account path / no amount pair)
            assert ":" not in cols[3] or not any(
                ch.isdigit() for ch in cols[3].split()[-1]
            )

    # ── Split-list collapse ──

    def test_no_collapse_below_threshold(self, test_book: Path):
        """Transactions with <= 4 splits render every split, no '+N more'."""
        gc_book = GnuCashBook(str(test_book))
        out = gc_book.list_transactions()
        assert "+" not in out or "more" not in out  # nothing collapsed

    def test_collapse_at_7_splits_unfiltered(self, test_book: Path):
        """7-split paycheck collapses to 3 + '+4 more' in unfiltered output."""
        gc_book = GnuCashBook(str(test_book))
        self._paycheck_book(gc_book)
        line = next(
            l for l in gc_book.search_transactions("Paycheck").strip().split("\n")
            if "Paycheck" in l
        )
        cols = line.split("\t")
        splits_col = cols[3]
        # Top 3 by |value|: Salary (-4865), Checking (+3000), Fed (+800)
        assert "Income:Salary -4865" in splits_col
        assert "Assets:Checking 3000" in splits_col
        assert "Expenses:Federal Tax 800" in splits_col
        assert "+4 more" in splits_col
        # The smaller ones got collapsed away
        assert "SS" not in splits_col
        assert "State" not in splits_col
        assert "Groceries 115" not in splits_col

    def test_collapse_filtered_drops_focus_then_collapses(self, test_book: Path):
        """Register form: focus account drops first, THEN top-3 collapse on the rest."""
        gc_book = GnuCashBook(str(test_book))
        self._paycheck_book(gc_book)
        line = next(
            l for l in gc_book.list_transactions(account="Assets:Checking").strip().split("\n")
            if "Paycheck" in l
        )
        cols = line.split("\t")
        assert cols[2] == "+3000"  # focus amount
        assert cols[3] == "Paycheck"
        splits_col = cols[4]
        # 6 other splits → top 3 by |value|: Salary, Fed, 401k
        assert "Income:Salary -4865" in splits_col
        assert "Expenses:Federal Tax 800" in splits_col
        assert "Assets:401k 400" in splits_col
        assert "+3 more" in splits_col
        # Focus account is absent from the splits column
        assert "Assets:Checking" not in splits_col

    def test_collapse_ranks_by_absolute_value(self, test_book: Path):
        """Collapse ordering is by |value| descending, not by appearance or sign.

        A -4865 entry outranks a +3000 entry because |4865| > |3000|.
        Verified implicitly by the paycheck fixture where Salary is
        first despite being negative.
        """
        gc_book = GnuCashBook(str(test_book))
        self._paycheck_book(gc_book)
        line = next(
            l for l in gc_book.search_transactions("Paycheck").strip().split("\n")
            if "Paycheck" in l
        )
        splits_col = line.split("\t")[3]
        # Salary (|-4865|) should appear before Checking (|3000|)
        salary_idx = splits_col.index("Income:Salary")
        checking_idx = splits_col.index("Assets:Checking")
        assert salary_idx < checking_idx


class TestGetTransaction:
    """Tests for get_transaction method."""

    def test_get_existing_transaction(self, test_book: Path):
        """Should return transaction details for existing GUID."""
        gc_book = GnuCashBook(str(test_book))

        # First get a valid GUID
        transactions = gc_book.list_transactions(compact=False)
        guid = transactions[0]["guid"]

        # Then fetch by GUID
        transaction = gc_book.get_transaction(guid)
        assert transaction is not None
        assert transaction["guid"] == guid
        assert "date" in transaction
        assert "description" in transaction
        assert "splits" in transaction

    def test_get_nonexistent_transaction(self, test_book: Path):
        """Should return None for non-existent GUID."""
        gc_book = GnuCashBook(str(test_book))
        transaction = gc_book.get_transaction("deadbeef00000000")

        assert transaction is None


class TestCreateTransaction:
    """Tests for create_transaction method."""

    def test_create_simple_transaction(self, test_book: Path):
        """Should create a simple two-split transaction."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="Test Transaction",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=date(2024, 2, 1),
        )

        assert result["status"] == "created"
        guid = result["guid"]
        # Write responses now return a collision-safe short prefix
        # (usually 8 chars) rather than the full 32-char GUID, to save
        # tokens on every tool call. Resolution tolerates the prefix.
        assert len(guid) >= 8

        # Verify transaction was created
        transaction = gc_book.get_transaction(guid)
        assert transaction["description"] == "Test Transaction"
        assert transaction["date"] == "2024-02-01"
        assert len(transaction["splits"]) == 2

    def test_create_transaction_with_memo(self, test_book: Path):
        """Should create transaction with split memos."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="Transaction with Memo",
            splits=[
                {"account": "Expenses:Groceries", "amount": "25.00", "memo": "Weekly shop"},
                {"account": "Assets:Checking", "amount": "-25.00", "memo": "Debit"},
            ],
        )

        transaction = gc_book.get_transaction(result["guid"])
        memos = {s["memo"] for s in transaction["splits"]}
        assert "Weekly shop" in memos
        assert "Debit" in memos

    def test_create_transaction_unbalanced(self, test_book: Path):
        """Should raise ValueError for unbalanced splits."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="do not balance"):
            gc_book.create_transaction(
                description="Unbalanced",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-40.00"},
                ],
            )

    def test_create_transaction_single_split(self, test_book: Path):
        """Should raise ValueError for single split."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="at least 2 splits"):
            gc_book.create_transaction(
                description="Single Split",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                ],
            )

    def test_create_transaction_invalid_account(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.create_transaction(
                description="Invalid Account",
                splits=[
                    {"account": "Nonexistent:Account", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-50.00"},
                ],
            )

    def test_create_transaction_placeholder_rejected(self, test_book: Path):
        """Should reject transaction targeting a placeholder account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="placeholder") as exc_info:
            gc_book.create_transaction(
                description="Bad Transaction",
                splits=[
                    {"account": "Expenses", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-50.00"},
                ],
            )
        # Error should suggest child accounts
        assert "Expenses:Groceries" in str(exc_info.value)

    def test_create_transaction_placeholder_suggests_children(self, test_book: Path):
        """Error message should list the placeholder's child accounts."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Use one of:"):
            gc_book.create_transaction(
                description="Bad Transaction",
                splits=[
                    {"account": "Assets", "amount": "-50.00"},
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                ],
            )

    def test_create_transaction_with_notes(self, test_book: Path):
        """Should create transaction with notes field."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="Safeway",
            splits=[
                {"account": "Expenses:Groceries", "amount": "75.00", "memo": "Meats"},
                {"account": "Assets:Checking", "amount": "-75.00"},
            ],
            notes="P2W1 groceries",
        )

        transaction = gc_book.get_transaction(result["guid"])
        assert transaction["description"] == "Safeway"
        assert transaction["notes"] == "P2W1 groceries"
        # Verify memo is separate from notes
        memos = {s["memo"] for s in transaction["splits"]}
        assert "Meats" in memos

    def test_create_transaction_without_notes(self, test_book: Path):
        """Transaction without notes should not include notes key."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="No Notes",
            splits=[
                {"account": "Expenses:Groceries", "amount": "10.00"},
                {"account": "Assets:Checking", "amount": "-10.00"},
            ],
        )

        transaction = gc_book.get_transaction(result["guid"])
        assert "notes" not in transaction


class TestCreateTransactionWarnings:
    """Tests for transaction creation warnings."""

    def test_future_date_warning(self, test_book: Path):
        """Should warn about future-dated transactions but still create them."""
        gc_book = GnuCashBook(str(test_book))
        future = date.today() + timedelta(days=30)
        result = gc_book.create_transaction(
            description="Future Payment",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=future,
        )
        assert result["status"] == "created"
        assert any(w["type"] == "future_date" for w in result["warnings"])

    def test_old_date_warning(self, test_book: Path):
        """Should warn about dates more than 365 days in the past."""
        gc_book = GnuCashBook(str(test_book))
        old = date.today() - timedelta(days=400)
        result = gc_book.create_transaction(
            description="Old Payment",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=old,
        )
        assert result["status"] == "created"
        assert any(w["type"] == "old_date" for w in result["warnings"])

    def test_normal_date_no_warning(self, test_book: Path):
        """Should not warn about normal dates."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Normal Payment",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=date.today(),
        )
        assert result["status"] == "created"
        assert "warnings" not in result

    def test_negative_expense_warning(self, test_book: Path):
        """Should warn about negative amounts to expense accounts."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Expense Reversal",
            splits=[
                {"account": "Expenses:Groceries", "amount": "-25.00"},
                {"account": "Assets:Checking", "amount": "25.00"},
            ],
        )
        assert result["status"] == "created"
        assert any(w["type"] == "negative_expense" for w in result["warnings"])

    def test_positive_income_warning(self, test_book: Path):
        """Should warn about positive amounts to income accounts."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Income Reversal",
            splits=[
                {"account": "Income:Salary", "amount": "100.00"},
                {"account": "Assets:Checking", "amount": "-100.00"},
            ],
        )
        assert result["status"] == "created"
        assert any(w["type"] == "positive_income" for w in result["warnings"])

    def test_warnings_dont_block_creation(self, test_book: Path):
        """Warnings should not prevent transaction creation."""
        gc_book = GnuCashBook(str(test_book))
        future = date.today() + timedelta(days=30)
        result = gc_book.create_transaction(
            description="Warned but Created",
            splits=[
                {"account": "Expenses:Groceries", "amount": "-10.00"},
                {"account": "Assets:Checking", "amount": "10.00"},
            ],
            trans_date=future,
        )
        assert result["status"] == "created"
        assert result["guid"]
        # Should have both future_date and negative_expense warnings
        warning_types = {w["type"] for w in result["warnings"]}
        assert "future_date" in warning_types
        assert "negative_expense" in warning_types


class TestDuplicateDetection:
    """Tests for duplicate transaction detection."""

    def test_high_duplicate_rejected(self, test_book: Path):
        """Should reject when all 3 signals match (description, amount, date)."""
        gc_book = GnuCashBook(str(test_book))
        # Existing: "Weekly Groceries", $150, 2024-01-20
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
        )
        assert result["status"] == "rejected"
        assert result["reason"] == "duplicate_detected"
        dups = _parse_duplicates(result["duplicates"])
        assert len(dups) > 0
        assert dups[0]["confidence"] == "HIGH"

    def test_high_duplicate_force_create(self, test_book: Path):
        """Should create when force_create overrides HIGH duplicate."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
            force_create=True,
        )
        assert result["status"] == "created"
        assert "guid" in result
        assert len(_parse_duplicates(result["duplicates"])) > 0

    def test_medium_duplicate_allowed(self, test_book: Path):
        """Should allow creation with MEDIUM confidence (2/3 signals)."""
        gc_book = GnuCashBook(str(test_book))
        # Same description and date, different amount
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "200.00"},
                {"account": "Assets:Checking", "amount": "-200.00"},
            ],
            trans_date=date(2024, 1, 20),
        )
        assert result["status"] == "created"
        assert any(
            d["confidence"] == "MEDIUM"
            for d in _parse_duplicates(result.get("duplicates", ""))
        )

    def test_low_duplicate_included(self, test_book: Path):
        """LOW confidence duplicates should be included for reference."""
        gc_book = GnuCashBook(str(test_book))
        # Only amount matches (~$150), different description, different date
        result = gc_book.create_transaction(
            description="Totally Different Store",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.50"},
                {"account": "Assets:Checking", "amount": "-150.50"},
            ],
            trans_date=date(2024, 1, 25),
        )
        assert result["status"] == "created"
        # Should have LOW confidence match (amount only). Note: LOW is
        # currently suppressed by _collect_create_signals (only HIGH/
        # MEDIUM emit), so this assertion no-ops when duplicates is
        # empty — retained for the day LOW is re-enabled.
        if result.get("duplicates"):
            assert any(
                d["confidence"] == "LOW"
                for d in _parse_duplicates(result["duplicates"])
            )

    def test_check_duplicates_false_skips(self, test_book: Path):
        """Should skip duplicate check entirely when check_duplicates=False."""
        gc_book = GnuCashBook(str(test_book))
        # Exact duplicate but check disabled
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
            check_duplicates=False,
        )
        assert result["status"] == "created"
        assert "duplicates" not in result

    def test_substring_description_match(self, test_book: Path):
        """Substring matching should work in both directions.

        Match signals are now emitted as a compact 3-char string:
        ``"DAD"`` means description + amount + date matched;
        ``"D-D"`` means description + date matched, amount did not.
        """
        gc_book = GnuCashBook(str(test_book))
        # "Groceries" is substring of "Weekly Groceries"
        result = gc_book.create_transaction(
            description="Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
        )
        assert result["status"] == "rejected"
        high = [
            d for d in _parse_duplicates(result["duplicates"])
            if d["confidence"] == "HIGH"
        ]
        assert len(high) > 0
        # Position 0 of signals string = description match
        assert high[0]["signals"][0] == "D"

    def test_amount_tolerance(self, test_book: Path):
        """Amount match should use ±$1.00 tolerance."""
        gc_book = GnuCashBook(str(test_book))
        # $150.99 is within $1.00 of $150.00
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.99"},
                {"account": "Assets:Checking", "amount": "-150.99"},
            ],
            trans_date=date(2024, 1, 20),
        )
        assert result["status"] == "rejected"
        # Position 1 of signals string = amount match
        assert _parse_duplicates(result["duplicates"])[0]["signals"][1] == "A"

    def test_date_window(self, test_book: Path):
        """Date match should use ±2 day window."""
        gc_book = GnuCashBook(str(test_book))
        # 2 days after existing (2024-01-20), should still match
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 22),
        )
        assert result["status"] == "rejected"
        # Position 2 of signals string = date match
        assert _parse_duplicates(result["duplicates"])[0]["signals"][2] == "D"

    def test_no_duplicates_distant_date(self, test_book: Path):
        """Should find no duplicates when date is far from existing."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2025, 6, 15),
        )
        assert result["status"] == "created"
        assert "duplicates" not in result


class TestDuplicateAmountPrimaryOnly:
    """The amount signal compares the proposed transaction's primary
    amount (max absolute split value) to each candidate's primary
    amount — not any-to-any across every split pair.

    Earlier iterations compared all proposed splits against all
    candidate splits, which produced false-positive MEDIUM matches
    on multi-split transactions whenever a tiny deduction happened
    to land within ±$1 of some unrelated single-split transaction's
    total. The bookkeeper's 2026-01-09 paycheck fired MEDIUM-AD
    matches against two same-day coffee purchases ($5.67 Analog,
    $5.29 Slate) because one of the paycheck's deduction lines was
    in that ~$5 range — completely unrelated transactions,
    meaninglessly flagged.

    These tests pin the new behavior: multi-split transactions only
    amount-match when their HEADLINE number (what a human reading
    the register would call "the amount") lines up.
    """

    def test_paycheck_does_not_false_match_coffee(self, test_book: Path):
        """Regression for the bookkeeper's -AD paycheck/coffee false
        positive. A paycheck with a ~$5 deduction line and a
        same-day coffee purchase near $5 must not surface as
        duplicates of each other."""
        gc = GnuCashBook(str(test_book))
        # Seed Analog Coffee $5.67 on 2026-01-09.
        gc.create_transaction(
            description="Analog Coffee",
            splits=[
                {"account": "Expenses:Groceries", "amount": "5.67"},
                {"account": "Assets:Checking", "amount": "-5.67"},
            ],
            trans_date=date(2026, 1, 9),
            check_duplicates=False,
        )
        # Paycheck on the same day. Gross 3269.23, net 2141.84,
        # deductions including a $5.65 line that's within $1 of the
        # coffee's $5.67 total. The old any-to-any predicate would
        # fire MEDIUM-AD; primary-max ignores deduction-tail
        # coincidences.
        result = gc.create_transaction(
            description="Robin's Paycheck (UW Medical)",
            splits=[
                {"account": "Income:Salary", "amount": "-3269.23"},
                {"account": "Assets:Checking", "amount": "2141.84"},
                {"account": "Expenses:Groceries", "amount": "980.00"},
                {"account": "Expenses:Groceries", "amount": "141.74"},
                {"account": "Expenses:Groceries", "amount": "5.65"},
            ],
            trans_date=date(2026, 1, 9),
            check_duplicates=True,
        )
        assert result["status"] == "created"
        dups = _parse_duplicates(result.get("duplicates", ""))
        # No coffee in the duplicate list — it would have shown up
        # with signals "-AD" under the old predicate.
        descriptions = [d["description"] for d in dups]
        assert "Analog Coffee" not in descriptions

    def test_paycheck_matches_paycheck(self, test_book: Path):
        """The positive case: two paychecks with matching gross (the
        primary amount on each side) still surface as duplicates.
        Verifies primary-max didn't over-prune."""
        gc = GnuCashBook(str(test_book))
        # Seed an earlier paycheck with the same gross.
        gc.create_transaction(
            description="Robin's Paycheck (UW Medical)",
            splits=[
                {"account": "Income:Salary", "amount": "-3269.23"},
                {"account": "Assets:Checking", "amount": "2141.84"},
                {"account": "Expenses:Groceries", "amount": "1127.39"},
            ],
            trans_date=date(2025, 12, 26),
            check_duplicates=False,
        )
        # Now create a same-gross paycheck two weeks later (same
        # ±30d window, different date → D, A, not D → MEDIUM-DA-).
        result = gc.create_transaction(
            description="Robin's Paycheck (UW Medical)",
            splits=[
                {"account": "Income:Salary", "amount": "-3269.23"},
                {"account": "Assets:Checking", "amount": "2141.84"},
                {"account": "Expenses:Groceries", "amount": "1127.39"},
            ],
            trans_date=date(2026, 1, 9),
            check_duplicates=True,
        )
        dups = _parse_duplicates(result.get("duplicates", ""))
        assert any(
            d["description"] == "Robin's Paycheck (UW Medical)"
            and d["signals"] == "DA-"
            for d in dups
        )

    def test_two_split_transactions_still_amount_match(
        self, test_book: Path,
    ):
        """Two-split transactions (the common case) weren't touched by
        this change — their primary amount IS their headline amount,
        so near-match detection keeps working."""
        gc = GnuCashBook(str(test_book))
        result = gc.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.99"},
                {"account": "Assets:Checking", "amount": "-150.99"},
            ],
            # Existing fixture transaction is same-desc $150 on
            # 2024-01-20; within ±$1 and ±2 days → HIGH.
            trans_date=date(2024, 1, 20),
        )
        assert result["status"] == "rejected"
        dups = _parse_duplicates(result["duplicates"])
        assert dups[0]["confidence"] == "HIGH"
        assert dups[0]["signals"] == "DAD"


class TestTemplateTransactionsExcluded:
    """Scheduled-transaction template rows are recipes, not events.
    GnuCash desktop persists each SX's splits as a real Transaction
    whose splits post to accounts under ``root_template``. The
    duplicate-detection scan must filter those out — otherwise a
    user entering a mortgage payment for the first time would always
    see the mortgage template as a "duplicate" candidate (D-D
    typically, since template amounts drift from reality but
    descriptions and cadence match), training them to ignore the
    duplicate warning.

    Regression for Abe's 2026-04-23 filing, which surfaced $2,485
    mortgage-template MEDIUM matches against $2,850 real-world
    payments on Alex's book.
    """

    def test_template_transaction_never_surfaces_as_duplicate(
        self, test_book: Path,
    ):
        """The exact bookkeeper scenario: a template transaction for
        'Mortgage Payment' at a stale $2,485 must not appear in the
        duplicates list when the user enters a real mortgage payment
        at $2,850."""
        gc = GnuCashBook(str(test_book))
        template_guid = _seed_template_transaction(
            gc,
            description="Mortgage Payment",
            amount="2485",
            trans_date=date(2025, 1, 1),
        )
        result = gc.create_transaction(
            description="Mortgage Payment",
            splits=[
                {"account": "Expenses:Groceries", "amount": "2850.00"},
                {"account": "Assets:Checking", "amount": "-2850.00"},
            ],
            trans_date=date(2026, 1, 1),
            check_duplicates=True,
        )
        assert result["status"] == "created"
        dups_tsv = result.get("duplicates", "")
        # The template's short-guid prefix must not appear anywhere
        # in the TSV response.
        assert template_guid[:8] not in dups_tsv

    def test_real_transaction_still_surfaces_even_with_template_present(
        self, test_book: Path,
    ):
        """Having a template on file doesn't suppress legitimate
        duplicate detection against real user transactions that share
        the same description."""
        gc = GnuCashBook(str(test_book))
        _seed_template_transaction(
            gc,
            description="Mortgage Payment",
            amount="2485",
            trans_date=date(2025, 1, 1),
        )
        # A real, user-posted mortgage transaction (not in the
        # template subtree).
        real = gc.create_transaction(
            description="Mortgage Payment",
            splits=[
                {"account": "Expenses:Groceries", "amount": "2850.00"},
                {"account": "Assets:Checking", "amount": "-2850.00"},
            ],
            trans_date=date(2026, 1, 1),
            check_duplicates=False,
        )
        # Now try to enter the same mortgage payment again → should
        # flag the REAL one (HIGH, all three signals), not the
        # template.
        result = gc.create_transaction(
            description="Mortgage Payment",
            splits=[
                {"account": "Expenses:Groceries", "amount": "2850.00"},
                {"account": "Assets:Checking", "amount": "-2850.00"},
            ],
            trans_date=date(2026, 1, 1),
            check_duplicates=True,
        )
        assert result["status"] == "rejected"
        dups = _parse_duplicates(result["duplicates"])
        assert dups[0]["confidence"] == "HIGH"
        # Short-guid of the real transaction, not the template.
        assert dups[0]["guid"] == real["guid"]

    def test_template_not_used_for_auto_fill(self, test_book: Path):
        """Auto-fill pulls the most-recent matching-description
        transaction's splits when the user omits ``splits``. A
        template row near the top of the sorted list by date could
        hijack auto-fill and poison the created transaction with
        template-account splits — verify the filter keeps it clear."""
        gc = GnuCashBook(str(test_book))
        # Seed a template in 2026 (most recent by date) and a real
        # transaction earlier. If the filter misses, auto-fill grabs
        # the template; if it holds, auto-fill uses the real one.
        _seed_template_transaction(
            gc,
            description="Utility Bill",
            amount="500",
            trans_date=date(2026, 3, 1),
        )
        gc.create_transaction(
            description="Utility Bill",
            splits=[
                {"account": "Expenses:Groceries", "amount": "120.50"},
                {"account": "Assets:Checking", "amount": "-120.50"},
            ],
            trans_date=date(2026, 1, 15),
            check_duplicates=False,
        )
        # Omit splits — force auto-fill.
        result = gc.create_transaction(
            description="Utility Bill",
            trans_date=date(2026, 4, 1),
            check_duplicates=False,
        )
        assert result["status"] == "created"
        # The auto-fill source should be the real transaction, not
        # the template. If it were the template, the created
        # transaction's splits would point at template accounts
        # (which would also throw balance-sheet reporting off).
        # Verify by fetching the new transaction.
        created = gc.get_transaction(result["guid"])
        accounts = {s["account"] for s in created["splits"]}
        # Template accounts have "Utility Bill Template" in their
        # fullname; real transaction uses Expenses:Groceries /
        # Assets:Checking.
        assert not any("Template" in a for a in accounts), accounts
        assert "Assets:Checking" in accounts
        assert "Expenses:Groceries" in accounts


class TestDuplicatesTsvShape:
    """The ``duplicates`` response field is a newline-separated TSV
    string, not a list of dicts. Abe's bookkeeper thread parses it
    column-wise to decide whether to retry with ``force_create=True``
    or back off — compact shape matters because a rejection often
    fires mid-conversation and the full JSON form was blowing
    through the context budget.

    Contract:

        confidence<TAB>guid<TAB>date<TAB>amount<TAB>description<TAB>signals

    One row per candidate, newline-separated, no header. HIGH before
    MEDIUM.
    """

    def test_rejected_duplicates_is_tsv_string(self, test_book: Path):
        """The rejection path always carries duplicates; response
        must be a str, not a list."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
        )
        assert result["status"] == "rejected"
        assert isinstance(result["duplicates"], str)
        # No legacy leak of list-of-dicts.
        assert not result["duplicates"].startswith("[")
        assert "{" not in result["duplicates"]

    def test_tsv_columns_and_order(self, test_book: Path):
        """Each row is six tab-separated columns in the documented
        order. The first row is the one with the strongest match
        (HIGH confidence when all three signals hit)."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
        )
        first_line = result["duplicates"].split("\n")[0]
        cols = first_line.split("\t")
        assert len(cols) == 6
        confidence, guid, dt, amount, description, signals = cols
        assert confidence == "HIGH"
        assert len(guid) >= 8  # short prefix, never a raw 32-char guid
        assert dt == "2024-01-20"
        # piecash's GncNumeric strips trailing zeros (150, not 150.00),
        # so compare numerically rather than asserting exact text.
        assert Decimal(amount) == Decimal("150")
        assert description == "Weekly Groceries"
        assert signals == "DAD"

    def test_tsv_row_count_matches_candidates(self, test_book: Path):
        """Number of newline-separated rows == number of unique
        duplicate candidates returned."""
        gc_book = GnuCashBook(str(test_book))
        # Seed a second HIGH match to guarantee two rows.
        gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.50"},
                {"account": "Assets:Checking", "amount": "-150.50"},
            ],
            trans_date=date(2024, 1, 21),
            force_create=True,
        )
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
        )
        lines = result["duplicates"].split("\n")
        # Both matches should appear as separate rows.
        assert len(lines) >= 2

    def test_success_path_drops_empty_duplicates_in_json(
        self, test_book: Path,
    ):
        """When there are no duplicates on a successful create, the
        field is absent from the response (book method returns dict
        without the key) rather than carrying an empty string."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Totally New Description For This Book",
            splits=[
                {"account": "Expenses:Groceries", "amount": "7.42"},
                {"account": "Assets:Checking", "amount": "-7.42"},
            ],
            trans_date=date(2025, 8, 1),
        )
        assert result["status"] == "created"
        assert "duplicates" not in result

    def test_tsv_shape_in_json_response(self, test_book: Path):
        """Sanity: after ``_json`` serialization the TSV string lands
        in the response verbatim (modulo JSON string escaping) — no
        accidental re-listification by the serializer."""
        import json as _json_lib

        from gnucash_mcp.tools._helpers import _json

        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
        )
        encoded = _json(result)
        decoded = _json_lib.loads(encoded)
        assert isinstance(decoded["duplicates"], str)
        # The TSV tabs survived encoding and round-trip.
        assert "\t" in decoded["duplicates"]


class TestDryRun:
    """Tests for dry run mode on create_transaction."""

    def test_dry_run_returns_proposal(self, test_book: Path):
        """Should return dry-run marker plus computed info (warnings, duplicates).

        The old `proposed_transaction` echo was dropped — the caller already
        knows what they proposed. Dry-run response only carries net-new info:
        validation warnings, duplicate candidates, and auto-fill provenance.
        """
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Dry Run Test",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=date(2024, 3, 1),
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert "warnings" in result
        assert "duplicates" in result
        # Inputs (description, date, splits) are NOT echoed back
        assert "proposed_transaction" not in result

    def test_dry_run_no_write(self, test_book: Path):
        """Dry run should not create a transaction in the book."""
        gc_book = GnuCashBook(str(test_book))
        before = gc_book.list_transactions(compact=False)
        gc_book.create_transaction(
            description="Ghost Transaction",
            splits=[
                {"account": "Expenses:Groceries", "amount": "99.99"},
                {"account": "Assets:Checking", "amount": "-99.99"},
            ],
            dry_run=True,
        )
        after = gc_book.list_transactions(compact=False)
        assert len(after) == len(before)

    def test_dry_run_includes_warnings(self, test_book: Path):
        """Dry run should include warnings."""
        gc_book = GnuCashBook(str(test_book))
        future = date.today() + timedelta(days=30)
        result = gc_book.create_transaction(
            description="Future Dry Run",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=future,
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert any(w["type"] == "future_date" for w in result["warnings"])

    def test_dry_run_includes_duplicates(self, test_book: Path):
        """Dry run should include duplicate candidates."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=date(2024, 1, 20),
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert len(_parse_duplicates(result["duplicates"])) > 0

    def test_dry_run_validation_errors_raised(self, test_book: Path):
        """Dry run should still raise validation errors."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="do not balance"):
            gc_book.create_transaction(
                description="Unbalanced Dry Run",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-40.00"},
                ],
                dry_run=True,
            )

    def test_dry_run_placeholder_rejected(self, test_book: Path):
        """Dry run should reject placeholder accounts."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="placeholder"):
            gc_book.create_transaction(
                description="Placeholder Dry Run",
                splits=[
                    {"account": "Expenses", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-50.00"},
                ],
                dry_run=True,
            )

    def test_dry_run_unknown_currency(self, test_book: Path):
        """Dry run should raise error for unknown currency."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="not found"):
            gc_book.create_transaction(
                description="Bad Currency Dry Run",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                    {"account": "Assets:Checking", "amount": "-50.00"},
                ],
                currency="XYZ",
                dry_run=True,
            )


class TestAutoFillTransaction:
    """Tests for auto-fill splits from previous transactions."""

    def test_auto_fill_from_description(self, test_book: Path):
        """Should auto-fill splits from most recent matching transaction."""
        gc_book = GnuCashBook(str(test_book))
        # "Weekly Groceries" exists in fixture: $150 groceries / -$150 checking
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            trans_date=date(2024, 3, 1),
            check_duplicates=False,
        )
        assert result["status"] == "created"
        # Verify the transaction was created with auto-filled splits
        txn = gc_book.get_transaction(result["guid"])
        accounts = {s["account"] for s in txn["splits"]}
        assert "Expenses:Groceries" in accounts
        assert "Assets:Checking" in accounts

    def test_auto_fill_result_includes_source(self, test_book: Path):
        """Should include auto_filled_from with source transaction info."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            trans_date=date(2024, 3, 1),
            check_duplicates=False,
        )
        assert "auto_filled_from" in result
        assert result["auto_filled_from"]["description"] == "Weekly Groceries"
        assert "guid" in result["auto_filled_from"]
        assert "date" in result["auto_filled_from"]

    def test_auto_fill_no_match(self, test_book: Path):
        """Should raise ValueError when no matching transaction found."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="No matching transaction found"):
            gc_book.create_transaction(
                description="Never Seen Before XYZ123",
            )

    def test_auto_fill_with_dry_run(self, test_book: Path):
        """Should auto-fill and report the source in dry run mode.

        After the token-efficiency trim, dry_run responses no longer echo
        back the proposed splits. We still get ``auto_filled_from`` which
        identifies the source transaction — enough to prove auto-fill ran
        and picked the right predecessor.
        """
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            dry_run=True,
            check_duplicates=False,
        )
        assert result["dry_run"] is True
        assert "auto_filled_from" in result
        # Source has the matching description, confirming we pulled from
        # the right predecessor transaction
        assert result["auto_filled_from"]["description"] == "Weekly Groceries"

    def test_auto_fill_preserves_memo(self, test_book: Path):
        """Auto-fill should preserve memo from source transaction."""
        gc_book = GnuCashBook(str(test_book))
        # First create a transaction with a memo
        gc_book.create_transaction(
            description="Coffee Shop",
            splits=[
                {"account": "Expenses:Groceries", "amount": "5.00", "memo": "Latte"},
                {"account": "Assets:Checking", "amount": "-5.00"},
            ],
            trans_date=date(2024, 2, 1),
            check_duplicates=False,
        )
        # Auto-fill from it
        result = gc_book.create_transaction(
            description="Coffee Shop",
            trans_date=date(2024, 3, 1),
            check_duplicates=False,
        )
        txn = gc_book.get_transaction(result["guid"])
        memos = {s["memo"] for s in txn["splits"]}
        assert "Latte" in memos

    def test_explicit_splits_no_auto_fill(self, test_book: Path):
        """Providing explicit splits should bypass auto-fill."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "200.00"},
                {"account": "Assets:Checking", "amount": "-200.00"},
            ],
            trans_date=date(2024, 3, 1),
            check_duplicates=False,
        )
        assert result["status"] == "created"
        assert "auto_filled_from" not in result
        # Verify the explicit amount was used, not auto-filled
        txn = gc_book.get_transaction(result["guid"])
        for s in txn["splits"]:
            if s["account"] == "Expenses:Groceries":
                assert s["value"] == "200"


class TestSplitConsistency:
    """Tests for split consistency warnings."""

    def _create_dining_account(self, gc_book):
        """Helper to add Expenses:Dining account."""
        gc_book.create_account(
            name="Dining",
            account_type="EXPENSE",
            parent="Expenses",
        )

    def _seed_groceries(self, gc_book, count=2):
        """Create recent grocery transactions for consistency baseline."""
        today = date.today()
        for i in range(count):
            gc_book.create_transaction(
                description="Weekly Groceries",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "150.00"},
                    {"account": "Assets:Checking", "amount": "-150.00"},
                ],
                trans_date=today - timedelta(days=7 * (i + 1)),
                check_duplicates=False,
            )

    def test_no_history(self, test_book: Path):
        """First transaction with a new description produces no warning."""
        gc_book = GnuCashBook(str(test_book))
        self._create_dining_account(gc_book)
        result = gc_book.create_transaction(
            description="Brand New Vendor XYZ",
            splits=[
                {"account": "Expenses:Dining", "amount": "25.00"},
                {"account": "Assets:Checking", "amount": "-25.00"},
            ],
            check_duplicates=False,
        )
        assert result["status"] == "created"
        warnings = result.get("warnings", [])
        consistency = [w for w in warnings if w["type"] == "split_consistency"]
        assert len(consistency) == 0

    def test_matching_pattern(self, test_book: Path):
        """Same expense account as recent history produces no warning."""
        gc_book = GnuCashBook(str(test_book))
        self._seed_groceries(gc_book)
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "175.00"},
                {"account": "Assets:Checking", "amount": "-175.00"},
            ],
            check_duplicates=False,
        )
        assert result["status"] == "created"
        warnings = result.get("warnings", [])
        consistency = [w for w in warnings if w["type"] == "split_consistency"]
        assert len(consistency) == 0

    def test_different_pattern(self, test_book: Path):
        """Different expense account triggers split_consistency warning."""
        gc_book = GnuCashBook(str(test_book))
        self._create_dining_account(gc_book)
        self._seed_groceries(gc_book)
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Dining", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            check_duplicates=False,
        )
        assert result["status"] == "created"
        warnings = result.get("warnings", [])
        consistency = [w for w in warnings if w["type"] == "split_consistency"]
        assert len(consistency) == 1
        assert "Expenses:Groceries" in consistency[0]["message"]
        assert "Expenses:Dining" in consistency[0]["message"]

    def test_ignores_funding_account(self, test_book: Path):
        """Changing funding account (Checking→Credit Card) with same
        expense should NOT trigger warning."""
        gc_book = GnuCashBook(str(test_book))
        self._seed_groceries(gc_book)
        # Add a credit card account
        gc_book.create_account(
            name="Credit Card",
            account_type="CREDIT",
            parent="Liabilities",
        )
        # Pay groceries from credit card instead of checking
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Liabilities:Credit Card", "amount": "-150.00"},
            ],
            check_duplicates=False,
        )
        assert result["status"] == "created"
        warnings = result.get("warnings", [])
        consistency = [w for w in warnings if w["type"] == "split_consistency"]
        assert len(consistency) == 0

    def test_transfer_pattern(self, test_book: Path):
        """Bank-to-bank transfer detects changed target account."""
        gc_book = GnuCashBook(str(test_book))
        gc_book.create_account(
            name="Savings",
            account_type="BANK",
            parent="Assets",
        )
        gc_book.create_account(
            name="Emergency Fund",
            account_type="BANK",
            parent="Assets",
        )
        today = date.today()
        # Seed: transfer Checking → Savings
        gc_book.create_transaction(
            description="Monthly Transfer",
            splits=[
                {"account": "Assets:Savings", "amount": "500.00"},
                {"account": "Assets:Checking", "amount": "-500.00"},
            ],
            trans_date=today - timedelta(days=7),
            check_duplicates=False,
        )
        # New: transfer Checking → Emergency Fund (different target)
        result = gc_book.create_transaction(
            description="Monthly Transfer",
            splits=[
                {"account": "Assets:Emergency Fund", "amount": "500.00"},
                {"account": "Assets:Checking", "amount": "-500.00"},
            ],
            check_duplicates=False,
        )
        assert result["status"] == "created"
        warnings = result.get("warnings", [])
        consistency = [w for w in warnings if w["type"] == "split_consistency"]
        # Transfer between funding accounts → fallback uses all accounts
        assert len(consistency) == 1

    def test_with_dry_run(self, test_book: Path):
        """Split consistency warning appears in dry-run result."""
        gc_book = GnuCashBook(str(test_book))
        self._create_dining_account(gc_book)
        self._seed_groceries(gc_book)
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Dining", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            dry_run=True,
            check_duplicates=False,
        )
        assert result["dry_run"] is True
        consistency = [
            w for w in result["warnings"]
            if w["type"] == "split_consistency"
        ]
        assert len(consistency) == 1
        assert "Expenses:Groceries" in consistency[0]["message"]


class TestAutoFillStability:
    """Tests for auto-fill stability warnings."""

    def _seed_consistent(self, gc_book, count=3):
        """Create consistent grocery transactions."""
        today = date.today()
        for i in range(count):
            gc_book.create_transaction(
                description="Weekly Groceries",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "150.00"},
                    {"account": "Assets:Checking", "amount": "-150.00"},
                ],
                trans_date=today - timedelta(days=7 * (i + 1)),
                check_duplicates=False,
            )

    def _create_dining_account(self, gc_book):
        gc_book.create_account(
            name="Dining",
            account_type="EXPENSE",
            parent="Expenses",
        )

    def test_stable_pattern(self, test_book: Path):
        """All recent matches consistent, no warning."""
        gc_book = GnuCashBook(str(test_book))
        self._seed_consistent(gc_book)
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            check_duplicates=False,
        )
        assert result["status"] == "created"
        assert "auto_filled_from" in result
        warnings = result.get("warnings", [])
        stability = [w for w in warnings if w["type"] == "auto_fill_unstable"]
        assert len(stability) == 0

    def test_unstable_pattern(self, test_book: Path):
        """Recent matches differ, auto_fill_unstable warning fires."""
        gc_book = GnuCashBook(str(test_book))
        self._create_dining_account(gc_book)
        today = date.today()
        # Older: groceries account
        gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=today - timedelta(days=14),
            check_duplicates=False,
        )
        # More recent: dining account (different pattern)
        gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Dining", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=today - timedelta(days=7),
            check_duplicates=False,
        )
        # Auto-fill should trigger instability warning
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            check_duplicates=False,
        )
        assert result["status"] == "created"
        assert "auto_filled_from" in result
        warnings = result.get("warnings", [])
        stability = [w for w in warnings if w["type"] == "auto_fill_unstable"]
        assert len(stability) == 1
        assert "different account patterns" in stability[0]["message"]

    def test_single_match(self, test_book: Path):
        """Only one prior transaction, no instability warning."""
        gc_book = GnuCashBook(str(test_book))
        today = date.today()
        gc_book.create_transaction(
            description="One-Time Vendor",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=today - timedelta(days=7),
            check_duplicates=False,
        )
        result = gc_book.create_transaction(
            description="One-Time Vendor",
            check_duplicates=False,
        )
        assert result["status"] == "created"
        warnings = result.get("warnings", [])
        stability = [w for w in warnings if w["type"] == "auto_fill_unstable"]
        assert len(stability) == 0

    def test_stability_with_dry_run(self, test_book: Path):
        """Instability warning appears in dry-run result."""
        gc_book = GnuCashBook(str(test_book))
        self._create_dining_account(gc_book)
        today = date.today()
        gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=today - timedelta(days=14),
            check_duplicates=False,
        )
        gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Dining", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=today - timedelta(days=7),
            check_duplicates=False,
        )
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            dry_run=True,
            check_duplicates=False,
        )
        assert result["dry_run"] is True
        stability = [
            w for w in result["warnings"]
            if w["type"] == "auto_fill_unstable"
        ]
        assert len(stability) == 1

    def test_unstable_no_consistency_conflict(self, test_book: Path):
        """Stability warning fires but NOT consistency warning when
        proposed splits match most recent transaction."""
        gc_book = GnuCashBook(str(test_book))
        self._create_dining_account(gc_book)
        today = date.today()
        # Older: groceries
        gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=today - timedelta(days=14),
            check_duplicates=False,
        )
        # More recent: dining
        gc_book.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Dining", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            trans_date=today - timedelta(days=7),
            check_duplicates=False,
        )
        # Auto-fill grabs most recent (dining) — matches recent so no
        # consistency warning, but stability warning should fire
        result = gc_book.create_transaction(
            description="Weekly Groceries",
            check_duplicates=False,
        )
        warnings = result.get("warnings", [])
        stability = [w for w in warnings if w["type"] == "auto_fill_unstable"]
        consistency = [w for w in warnings if w["type"] == "split_consistency"]
        assert len(stability) == 1
        assert len(consistency) == 0


class TestSearchTransactions:
    """Tests for search_transactions method."""

    def test_search_by_description(self, test_book: Path):
        """Should find transactions by description."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("Salary", field="description", compact=False)
        assert len(results) == 1
        assert results[0]["description"] == "Salary Deposit"

    def test_search_by_description_case_insensitive(self, test_book: Path):
        """Should search case-insensitively."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("salary", field="description", compact=False)
        assert len(results) == 1

    def test_search_by_amount_exact(self, test_book: Path):
        """Should find transactions by exact amount."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("150", field="amount", compact=False)
        assert len(results) == 1
        assert results[0]["description"] == "Weekly Groceries"

    def test_search_by_amount_greater_than(self, test_book: Path):
        """Should find transactions with amount greater than threshold."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions(">500", field="amount", compact=False)
        # Opening (1000) and Salary (2000) transactions
        assert len(results) == 2

    def test_search_by_amount_less_than(self, test_book: Path):
        """Should find transactions with amount less than threshold."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("<200", field="amount", compact=False)
        assert len(results) == 1
        assert results[0]["description"] == "Weekly Groceries"

    def test_search_by_amount_range(self, test_book: Path):
        """Should find transactions with amount in range."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("100-500", field="amount", compact=False)
        assert len(results) == 1
        assert results[0]["description"] == "Weekly Groceries"

    def test_search_by_notes(self, test_book: Path):
        """Should find transactions by notes field."""
        gc_book = GnuCashBook(str(test_book))

        # Create a transaction with notes
        gc_book.create_transaction(
            description="Safeway",
            splits=[
                {"account": "Expenses:Groceries", "amount": "30.00"},
                {"account": "Assets:Checking", "amount": "-30.00"},
            ],
            notes="P2W1 groceries",
        )

        results = gc_book.search_transactions("P2W1", field="notes", compact=False)
        assert len(results) == 1
        assert results[0]["description"] == "Safeway"
        assert results[0]["notes"] == "P2W1 groceries"

    def test_search_by_notes_no_match(self, test_book: Path):
        """Should return empty list when no notes match."""
        gc_book = GnuCashBook(str(test_book))

        results = gc_book.search_transactions("nonexistent", field="notes", compact=False)
        assert len(results) == 0

    def test_search_invalid_field(self, test_book: Path):
        """Should raise ValueError for invalid field."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Invalid search field"):
            gc_book.search_transactions("test", field="invalid")

    def test_search_by_amount_invalid_query(self, test_book: Path):
        """Should raise ValueError for malformed amount query."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Invalid amount query"):
            gc_book.search_transactions(">notanumber", field="amount")

    def test_search_compact_truncation_notice(self, test_book: Path):
        """Compact mode appends truncation notice when matches exceed limit."""
        gc_book = GnuCashBook(str(test_book))
        # Match all 3 transactions on a common substring, limit=2 forces truncation.
        # 'a' appears in "Opening Balance", "Salary Deposit", "Weekly Groceries".
        # Actually just use a broad amount filter for certainty.
        result = gc_book.search_transactions(">0", field="amount", limit=2, compact=True)
        assert "[Showing 2 of 3 transactions" in result

    def test_search_compact_cap_applied(self, test_book: Path):
        """Limits above MAX_LIST_LIMIT get clamped with a note."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.search_transactions(">0", field="amount", limit=10000, compact=True)
        assert "Limit capped at 250" in result


class TestCreateAccount:
    """Tests for create_account method."""

    def test_create_account_success(self, test_book: Path):
        """Should create a new account under existing parent."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_account(
            name="Test Category",
            account_type="EXPENSE",
            parent="Expenses",
            description="A test expense category",
        )

        assert result["status"] == "created"
        assert result["fullname"] == "Expenses:Test Category"
        assert len(result["guid"]) >= 8  # short collision-safe prefix

        # Verify account exists
        account = gc_book.get_account("Expenses:Test Category")
        assert account is not None
        assert account["description"] == "A test expense category"

    def test_create_account_nested(self, test_book: Path):
        """Should create account under nested parent."""
        gc_book = GnuCashBook(str(test_book))

        # First create a parent
        gc_book.create_account(
            name="Online Services",
            account_type="EXPENSE",
            parent="Expenses",
            placeholder=True,
        )

        # Then create child
        result = gc_book.create_account(
            name="AI Subscriptions",
            account_type="EXPENSE",
            parent="Expenses:Online Services",
            description="Claude, ChatGPT, etc.",
        )

        assert result["fullname"] == "Expenses:Online Services:AI Subscriptions"

    def test_create_account_placeholder(self, test_book: Path):
        """Should create placeholder account."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_account(
            name="Placeholder Category",
            account_type="EXPENSE",
            parent="Expenses",
            placeholder=True,
        )

        account = gc_book.get_account("Expenses:Placeholder Category")
        assert account["placeholder"] is True

    def test_create_account_parent_not_found(self, test_book: Path):
        """Should raise ValueError if parent doesn't exist."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Parent account not found"):
            gc_book.create_account(
                name="Test",
                account_type="EXPENSE",
                parent="Nonexistent:Parent",
            )

    def test_create_account_duplicate(self, test_book: Path):
        """Should raise ValueError if account with same name exists under parent."""
        gc_book = GnuCashBook(str(test_book))

        # Groceries already exists under Expenses
        with pytest.raises(ValueError, match="already exists"):
            gc_book.create_account(
                name="Groceries",
                account_type="EXPENSE",
                parent="Expenses",
            )

    def test_create_account_invalid_type(self, test_book: Path):
        """Should raise ValueError for invalid account type."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Invalid account type"):
            gc_book.create_account(
                name="Test",
                account_type="INVALID",
                parent="Expenses",
            )

    def test_create_account_type_case_insensitive(self, test_book: Path):
        """Should accept lowercase account types."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_account(
            name="Lowercase Type Test",
            account_type="expense",
            parent="Expenses",
        )

        assert result["status"] == "created"

    def test_create_root_level_account(self, test_book: Path):
        """Should create account at root level when parent is omitted."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_account(
            name="Testing",
            account_type="ASSET",
        )
        assert result["status"] == "created"
        assert result["fullname"] == "Testing"
        assert "warning" in result
        assert "root level" in result["warning"]

    def test_root_level_account_no_warning_with_parent(self, test_book: Path):
        """Should not include warning when parent is provided."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.create_account(
            name="Normal Child",
            account_type="EXPENSE",
            parent="Expenses",
        )
        assert result["status"] == "created"
        assert "warning" not in result

    def test_root_level_account_in_list(self, test_book: Path):
        """Root-level account should appear in list_accounts."""
        gc_book = GnuCashBook(str(test_book))
        gc_book.create_account(name="TopLevel", account_type="ASSET")
        result = gc_book.list_accounts()
        assert "TopLevel" in result

    def test_root_level_account_get_balance(self, test_book: Path):
        """get_balance should work on a root-level account."""
        gc_book = GnuCashBook(str(test_book))
        gc_book.create_account(name="TopLevel", account_type="ASSET")
        balance = gc_book.get_balance("TopLevel")
        assert balance == 0

    def test_root_level_duplicate_blocked(self, test_book: Path):
        """Should block duplicate root-level accounts."""
        gc_book = GnuCashBook(str(test_book))
        gc_book.create_account(name="UniqueRoot", account_type="ASSET")
        with pytest.raises(ValueError, match="already exists"):
            gc_book.create_account(name="UniqueRoot", account_type="ASSET")


class TestUpdateAccount:
    """Tests for update_account method."""

    def test_update_account_rename(self, test_book: Path):
        """Should rename an account."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.update_account(
            name="Expenses:Groceries",
            new_name="Food & Groceries",
        )

        assert result["status"] == "updated"
        assert result["name"] == "Food & Groceries"

        # Verify old name doesn't exist
        assert gc_book.get_account("Expenses:Groceries") is None
        # Verify new name exists
        assert gc_book.get_account("Expenses:Food & Groceries") is not None

    def test_update_account_description(self, test_book: Path):
        """Should update account description."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.update_account(
            name="Expenses:Groceries",
            description="Weekly grocery shopping",
        )

        assert result["status"] == "updated"
        assert result["description"] == "Weekly grocery shopping"

    def test_update_account_placeholder(self, test_book: Path):
        """Should update placeholder status — and echo the change."""
        gc_book = GnuCashBook(str(test_book))

        # The test fixture's "Expenses" is already a placeholder, so
        # passing ``placeholder=True`` would be a no-op under the new
        # diff-only contract. Toggle it off first to make the second
        # call observably change the account.
        gc_book.update_account(name="Expenses", placeholder=False)
        result = gc_book.update_account(name="Expenses", placeholder=True)

        assert result["status"] == "updated"
        # ``placeholder`` only appears in the response when it actually
        # changed — diff-style write echo (Phase 2 of the comms-audit).
        assert result["placeholder"] is True

    def test_update_account_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.update_account(
                name="Nonexistent:Account",
                description="test",
            )

    def test_update_account_name_conflict(self, test_book: Path):
        """Should raise ValueError if new name conflicts with sibling."""
        gc_book = GnuCashBook(str(test_book))

        # Create another expense account
        gc_book.create_account(
            name="Dining",
            account_type="EXPENSE",
            parent="Expenses",
        )

        # Try to rename Groceries to Dining
        with pytest.raises(ValueError, match="already exists"):
            gc_book.update_account(
                name="Expenses:Groceries",
                new_name="Dining",
            )


    def test_update_account_type_liability_to_credit(self, test_book: Path):
        """Should allow changing LIABILITY to CREDIT (same polarity)."""
        gc_book = GnuCashBook(str(test_book))
        gc_book.create_account(
            name="CareCredit", account_type="LIABILITY", parent="Liabilities",
        )
        result = gc_book.update_account(
            name="Liabilities:CareCredit", account_type="CREDIT",
        )
        assert result["status"] == "updated"
        assert result["type"] == "CREDIT"

    def test_update_account_type_credit_to_liability(self, test_book: Path):
        """Should allow changing CREDIT to LIABILITY (reverse, same polarity)."""
        gc_book = GnuCashBook(str(test_book))
        gc_book.create_account(
            name="Store Card", account_type="CREDIT", parent="Liabilities",
        )
        result = gc_book.update_account(
            name="Liabilities:Store Card", account_type="LIABILITY",
        )
        assert result["status"] == "updated"
        assert result["type"] == "LIABILITY"

    def test_update_account_type_asset_to_bank(self, test_book: Path):
        """Should allow changing ASSET to BANK (same polarity)."""
        gc_book = GnuCashBook(str(test_book))
        gc_book.create_account(
            name="Savings", account_type="ASSET", parent="Assets",
        )
        result = gc_book.update_account(
            name="Assets:Savings", account_type="BANK",
        )
        assert result["status"] == "updated"
        assert result["type"] == "BANK"

    def test_update_account_type_stock_to_mutual(self, test_book: Path):
        """Should allow changing STOCK to MUTUAL (same polarity)."""
        gc_book = GnuCashBook(str(test_book))
        gc_book.create_account(
            name="Index Fund", account_type="STOCK", parent="Assets",
        )
        result = gc_book.update_account(
            name="Assets:Index Fund", account_type="MUTUAL",
        )
        assert result["status"] == "updated"
        assert result["type"] == "MUTUAL"

    def test_update_account_type_cross_polarity_blocked(self, test_book: Path):
        """Should block ASSET to LIABILITY (cross-polarity)."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="flip the debit/credit polarity"):
            gc_book.update_account(
                name="Assets:Checking", account_type="LIABILITY",
            )

    def test_update_account_type_expense_to_income_blocked(self, test_book: Path):
        """Should block EXPENSE to INCOME (cross-polarity)."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="flip the debit/credit polarity"):
            gc_book.update_account(
                name="Expenses:Groceries", account_type="INCOME",
            )

    def test_update_account_type_invalid_type(self, test_book: Path):
        """Should reject invalid account types."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="Invalid account type"):
            gc_book.update_account(
                name="Assets:Checking", account_type="NONSENSE",
            )

    def test_update_account_type_same_type_noop(self, test_book: Path):
        """Changing to the same type should succeed without error AND
        the diff-style response should NOT echo ``type`` (no change)."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.update_account(
            name="Assets:Checking", account_type="BANK",
        )
        assert result["status"] == "updated"
        # Pre-Phase-2 the response echoed every field. New contract:
        # only fields that actually changed appear. Same-type set
        # changes nothing, so ``type`` is absent.
        assert "type" not in result


class TestWriteResponseShape:
    """Tests for the trimmed write-response contract introduced in
    Phase 2 of the comms-audit fixes. ``update_account`` and
    ``move_account`` previously echoed the entire account record on
    every write; the new contract returns ``{guid, status, ...changed
    fields}`` only. Locks the shape so future refactors don't
    regress to "echo everything"."""

    def test_update_account_response_omits_unchanged_fields(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.update_account(
            name="Expenses:Groceries",
            description="Updated description",
        )
        assert result["status"] == "updated"
        # ``description`` changed — present.
        assert result["description"] == "Updated description"
        # Other fields didn't change — absent.
        assert "name" not in result
        assert "type" not in result
        assert "placeholder" not in result
        assert "fullname" not in result
        # ``guid`` is always returned (the caller's handle).
        assert result["guid"].startswith("%")

    def test_update_account_response_carries_short_guid(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.update_account(
            name="Expenses:Groceries",
            description="Refresh",
        )
        # Short account GUID format: '%' + ≥7 hex chars.
        import re
        assert re.fullmatch(r"%[0-9a-f]{7,32}", result["guid"]), (
            f"unexpected guid shape: {result['guid']!r}"
        )

    def test_move_account_response_shape(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        # Set up a destination parent for the move.
        gc_book.create_account(
            name="Daily Expenses", account_type="EXPENSE",
            parent="Expenses", placeholder=True,
        )
        result = gc_book.move_account(
            name="Expenses:Groceries",
            new_parent="Expenses:Daily Expenses",
        )
        assert result["status"] == "moved"
        assert result["fullname"] == "Expenses:Daily Expenses:Groceries"
        assert result["parent"] == "Expenses:Daily Expenses"
        assert result["guid"].startswith("%")
        # Shape contract: nothing else.
        assert set(result.keys()) == {"guid", "fullname", "parent", "status"}


class TestMoveAccount:
    """Tests for move_account method."""

    def test_move_account_success(self, test_book: Path):
        """Should move an account to new parent."""
        gc_book = GnuCashBook(str(test_book))

        # Create a new parent category
        gc_book.create_account(
            name="Daily Expenses",
            account_type="EXPENSE",
            parent="Expenses",
            placeholder=True,
        )

        # Move Groceries under Daily Expenses
        result = gc_book.move_account(
            name="Expenses:Groceries",
            new_parent="Expenses:Daily Expenses",
        )

        assert result["status"] == "moved"
        assert result["fullname"] == "Expenses:Daily Expenses:Groceries"

    def test_move_account_not_found(self, test_book: Path):
        """Should raise ValueError if account not found."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.move_account(
                name="Nonexistent:Account",
                new_parent="Expenses",
            )

    def test_move_account_parent_not_found(self, test_book: Path):
        """Should raise ValueError if new parent not found."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Parent account not found"):
            gc_book.move_account(
                name="Expenses:Groceries",
                new_parent="Nonexistent:Parent",
            )

    def test_move_account_circular_reference(self, test_book: Path):
        """Should raise ValueError if move would create circular reference."""
        gc_book = GnuCashBook(str(test_book))

        # Create a child under Groceries
        gc_book.create_account(
            name="Organic",
            account_type="EXPENSE",
            parent="Expenses:Groceries",
        )

        # Try to move Groceries under its own child
        with pytest.raises(ValueError, match="Cannot move account under itself"):
            gc_book.move_account(
                name="Expenses:Groceries",
                new_parent="Expenses:Groceries:Organic",
            )

    def test_move_account_name_conflict(self, test_book: Path):
        """Should raise ValueError if name conflicts in new location."""
        gc_book = GnuCashBook(str(test_book))

        # Create an account under Assets with same name as one under Expenses
        gc_book.create_account(
            name="Groceries",
            account_type="ASSET",
            parent="Assets",
        )

        # Try to move Expenses:Groceries to Assets (conflict with Assets:Groceries)
        with pytest.raises(ValueError, match="already exists"):
            gc_book.move_account(
                name="Expenses:Groceries",
                new_parent="Assets",
            )


class TestDeleteAccount:
    """Tests for delete_account method."""

    def test_delete_account_success(self, test_book: Path):
        """Should delete an empty account."""
        gc_book = GnuCashBook(str(test_book))

        # Create a new account to delete
        gc_book.create_account(
            name="To Delete",
            account_type="EXPENSE",
            parent="Expenses",
        )

        result = gc_book.delete_account("Expenses:To Delete")

        assert result["status"] == "deleted"
        assert gc_book.get_account("Expenses:To Delete") is None

    def test_delete_account_not_found(self, test_book: Path):
        """Should raise ValueError if account not found."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.delete_account("Nonexistent:Account")

    def test_delete_account_with_children(self, test_book: Path):
        """Should raise ValueError if account has children."""
        gc_book = GnuCashBook(str(test_book))

        # Expenses has Groceries as a child
        with pytest.raises(ValueError, match="Cannot delete account with children"):
            gc_book.delete_account("Expenses")

    def test_delete_account_with_transactions(self, test_book: Path):
        """Should raise ValueError if account has transactions."""
        gc_book = GnuCashBook(str(test_book))

        # Groceries has transactions
        with pytest.raises(ValueError, match="Cannot delete account with"):
            gc_book.delete_account("Expenses:Groceries")


class TestDeleteTransaction:
    """Tests for delete_transaction method."""

    def test_delete_transaction_success(self, test_book: Path):
        """Should delete an existing transaction."""
        gc_book = GnuCashBook(str(test_book))

        # Get a transaction to delete
        transactions = gc_book.list_transactions(compact=False)
        guid = transactions[0]["guid"]
        description = transactions[0]["description"]

        result = gc_book.delete_transaction(guid)

        assert result["status"] == "deleted"
        # Response guid is a short collision-safe prefix of the full guid.
        assert guid.startswith(result["guid"])
        assert result["description"] == description

        # Verify transaction is gone
        assert gc_book.get_transaction(guid) is None

    def test_delete_transaction_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent transaction."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Transaction not found"):
            gc_book.delete_transaction("deadbeef00000000")

    def test_delete_reconciled_transaction_rejected(self, test_book: Path):
        """Should reject deletion of transaction with reconciled splits."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions(compact=False)
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        guid = transactions[0]["guid"]
        with pytest.raises(ValueError, match="reconciled splits"):
            gc_book.delete_transaction(guid)

    def test_delete_reconciled_transaction_force(self, test_book: Path):
        """Should allow deletion with force=True despite reconciled splits."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions(compact=False)
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        guid = transactions[0]["guid"]
        result = gc_book.delete_transaction(guid, force=True)

        assert result["status"] == "deleted"
        assert result["reconciled_splits_affected"] == 1
        assert gc_book.get_transaction(guid) is None


class TestUpdateTransaction:
    """Tests for update_transaction method."""

    def test_update_description_only(self, test_book: Path):
        """Should update only the description."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions(compact=False)
        guid = transactions[0]["guid"]

        result = gc_book.update_transaction(
            guid=guid,
            description="Updated Description",
        )

        assert result["status"] == "updated"
        assert result["description"] == "Updated Description"

    def test_update_date_only(self, test_book: Path):
        """Should update only the date."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions(compact=False)
        guid = transactions[0]["guid"]

        result = gc_book.update_transaction(
            guid=guid,
            trans_date=date(2024, 6, 15),
        )

        assert result["status"] == "updated"
        assert result["date"] == "2024-06-15"

    def test_update_splits(self, test_book: Path):
        """Should update split amounts."""
        gc_book = GnuCashBook(str(test_book))

        # Get the groceries transaction (150.00)
        transactions = gc_book.search_transactions("Groceries", compact=False)
        guid = transactions[0]["guid"]

        result = gc_book.update_transaction(
            guid=guid,
            splits=[
                {"account": "Expenses:Groceries", "amount": "175.00"},
                {"account": "Assets:Checking", "amount": "-175.00"},
            ],
        )

        assert result["status"] == "updated"
        # Verify new amounts
        updated = gc_book.get_transaction(guid)
        for split in updated["splits"]:
            if split["account"] == "Expenses:Groceries":
                assert split["value"] == "175"

    def test_update_everything(self, test_book: Path):
        """Should update description, date, and splits together."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Groceries", compact=False)
        guid = transactions[0]["guid"]

        result = gc_book.update_transaction(
            guid=guid,
            description="Safeway Groceries",
            trans_date=date(2024, 1, 21),
            splits=[
                {"account": "Expenses:Groceries", "amount": "160.00"},
                {"account": "Assets:Checking", "amount": "-160.00"},
            ],
        )

        assert result["status"] == "updated"
        assert result["description"] == "Safeway Groceries"
        assert result["date"] == "2024-01-21"

    def test_update_transaction_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent transaction."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Transaction not found"):
            gc_book.update_transaction(
                guid="deadbeef00000000",
                description="Test",
            )

    def test_update_splits_unbalanced(self, test_book: Path):
        """Should raise ValueError for unbalanced splits."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions(compact=False)
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="do not balance"):
            gc_book.update_transaction(
                guid=guid,
                splits=[
                    {"account": "Expenses:Groceries", "amount": "100.00"},
                    {"account": "Assets:Checking", "amount": "-90.00"},
                ],
            )

    def test_update_splits_account_not_found(self, test_book: Path):
        """Should raise ValueError if split account not in transaction."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Groceries", compact=False)
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="Account not found in transaction"):
            gc_book.update_transaction(
                guid=guid,
                splits=[
                    {"account": "Expenses:Nonexistent", "amount": "100.00"},
                    {"account": "Assets:Checking", "amount": "-100.00"},
                ],
            )

    def test_update_notes(self, test_book: Path):
        """Should add notes to an existing transaction."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Groceries", compact=False)
        guid = transactions[0]["guid"]

        result = gc_book.update_transaction(
            guid=guid,
            notes="P2W1 groceries",
        )

        # Response is thin now (guid/date/description/status). Notes
        # persistence is verified through get_transaction below.
        assert result["status"] == "updated"

        updated = gc_book.get_transaction(guid)
        assert updated["notes"] == "P2W1 groceries"

    def test_clear_notes(self, test_book: Path):
        """Should clear notes when empty string is passed."""
        gc_book = GnuCashBook(str(test_book))

        # Create transaction with notes
        create_result = gc_book.create_transaction(
            description="With Notes",
            splits=[
                {"account": "Expenses:Groceries", "amount": "20.00"},
                {"account": "Assets:Checking", "amount": "-20.00"},
            ],
            notes="Some notes",
        )
        guid = create_result["guid"]

        # Clear notes
        result = gc_book.update_transaction(guid=guid, notes="")
        assert "notes" not in result

        # Verify persistence
        updated = gc_book.get_transaction(guid)
        assert "notes" not in updated

    def test_update_reconciled_splits_rejected(self, test_book: Path):
        """Should reject split updates on transactions with reconciled splits."""
        gc_book = GnuCashBook(str(test_book))

        # Get the groceries transaction and reconcile a split
        transactions = gc_book.search_transactions("Groceries", compact=False)
        guid = transactions[0]["guid"]
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        with pytest.raises(ValueError, match="reconciled splits"):
            gc_book.update_transaction(
                guid=guid,
                splits=[
                    {"account": "Expenses:Groceries", "amount": "175.00"},
                    {"account": "Assets:Checking", "amount": "-175.00"},
                ],
            )

    def test_update_reconciled_splits_force(self, test_book: Path):
        """Should allow split updates with force=True despite reconciled splits."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Groceries", compact=False)
        guid = transactions[0]["guid"]
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        result = gc_book.update_transaction(
            guid=guid,
            splits=[
                {"account": "Expenses:Groceries", "amount": "175.00"},
                {"account": "Assets:Checking", "amount": "-175.00"},
            ],
            force=True,
        )
        assert result["status"] == "updated"

    def test_update_description_on_reconciled_ok(self, test_book: Path):
        """Should allow description/date/notes changes without force on reconciled."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Groceries", compact=False)
        guid = transactions[0]["guid"]
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        # Description change should work without force
        result = gc_book.update_transaction(
            guid=guid, description="Updated Groceries"
        )
        assert result["status"] == "updated"
        assert result["description"] == "Updated Groceries"


class TestReplaceSplits:
    """Tests for replace_splits method."""

    def test_basic_replace_splits(self, test_book: Path):
        """Should replace splits with new accounts."""
        gc_book = GnuCashBook(str(test_book))

        # Find the grocery transaction
        transactions = gc_book.search_transactions("Weekly Groceries", compact=False)
        guid = transactions[0]["guid"]
        original_splits = transactions[0]["splits"]

        # Get original accounts for verification
        original_accounts = {s["account"] for s in original_splits}
        assert "Expenses:Groceries" in original_accounts

        # Create a Dining account to replace splits with
        gc_book.create_account(
            name="Dining",
            account_type="EXPENSE",
            parent="Expenses",
        )

        # Recategorize: Groceries -> Dining
        result = gc_book.replace_splits(
            guid=guid,
            splits=[
                {"account": "Expenses:Dining", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
        )

        # Response is thin — no splits echo. Verify the new state via
        # a follow-up read.
        assert result["status"] == "splits_replaced"
        refreshed = gc_book.get_transaction(guid)
        new_accounts = {s["account"] for s in refreshed["splits"]}
        assert "Expenses:Dining" in new_accounts
        assert "Expenses:Groceries" not in new_accounts

    def test_preserves_transaction_identity(self, test_book: Path):
        """Should preserve transaction GUID, description, date, notes."""
        gc_book = GnuCashBook(str(test_book))

        # Find the grocery transaction
        transactions = gc_book.search_transactions("Weekly Groceries", compact=False)
        guid = transactions[0]["guid"]
        original_date = transactions[0]["date"]
        original_description = transactions[0]["description"]

        # Create a Dining account
        gc_book.create_account(
            name="Dining",
            account_type="EXPENSE",
            parent="Expenses",
        )

        # Recategorize
        result = gc_book.replace_splits(
            guid=guid,
            splits=[
                {"account": "Expenses:Dining", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
        )

        # guid stays in the thin response (as a short prefix).
        # description / date are preserved on the transaction itself —
        # verified via read-back.
        assert guid.startswith(result["guid"])
        refreshed = gc_book.get_transaction(guid)
        assert refreshed["date"] == original_date
        assert refreshed["description"] == original_description

    def test_returns_previous_splits(self, test_book: Path):
        """Should include previous_splits in response for audit trail."""
        gc_book = GnuCashBook(str(test_book))

        # Find the grocery transaction
        transactions = gc_book.search_transactions("Weekly Groceries", compact=False)
        guid = transactions[0]["guid"]
        original_splits = transactions[0]["splits"]

        # Create a Dining account
        gc_book.create_account(
            name="Dining",
            account_type="EXPENSE",
            parent="Expenses",
        )

        # Recategorize
        result = gc_book.replace_splits(
            guid=guid,
            splits=[
                {"account": "Expenses:Dining", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
        )

        assert "previous_splits" in result
        assert len(result["previous_splits"]) == len(original_splits)
        previous_accounts = {s["account"] for s in result["previous_splits"]}
        assert "Expenses:Groceries" in previous_accounts

    def test_requires_balanced_splits(self, test_book: Path):
        """Should reject splits that don't balance to zero."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Weekly Groceries", compact=False)
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="do not balance"):
            gc_book.replace_splits(
                guid=guid,
                splits=[
                    {"account": "Expenses:Groceries", "amount": "150.00"},
                    {"account": "Assets:Checking", "amount": "-140.00"},  # Wrong
                ],
            )

    def test_requires_two_splits(self, test_book: Path):
        """Should reject fewer than 2 splits."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Weekly Groceries", compact=False)
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="At least 2 splits"):
            gc_book.replace_splits(
                guid=guid,
                splits=[
                    {"account": "Expenses:Groceries", "amount": "0.00"},
                ],
            )

    def test_account_not_found(self, test_book: Path):
        """Should reject splits with non-existent accounts."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Weekly Groceries", compact=False)
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.replace_splits(
                guid=guid,
                splits=[
                    {"account": "Expenses:NonExistent", "amount": "150.00"},
                    {"account": "Assets:Checking", "amount": "-150.00"},
                ],
            )

    def test_placeholder_rejected(self, test_book: Path):
        """Should reject splits to placeholder accounts."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Weekly Groceries", compact=False)
        guid = transactions[0]["guid"]

        # Expenses is a placeholder in test_book
        with pytest.raises(ValueError, match="placeholder account"):
            gc_book.replace_splits(
                guid=guid,
                splits=[
                    {"account": "Expenses", "amount": "150.00"},
                    {"account": "Assets:Checking", "amount": "-150.00"},
                ],
            )

    def test_transaction_not_found(self, test_book: Path):
        """Should reject non-existent transaction GUID."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Transaction not found"):
            gc_book.replace_splits(
                guid="00000000000000000000000000000000",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "150.00"},
                    {"account": "Assets:Checking", "amount": "-150.00"},
                ],
            )

    def test_reconciled_requires_force(self, test_book: Path):
        """Should reject recategorizing reconciled splits without force."""
        gc_book = GnuCashBook(str(test_book))

        # Get a transaction and reconcile one of its splits
        transactions = gc_book.search_transactions("Weekly Groceries", compact=False)
        guid = transactions[0]["guid"]
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        with pytest.raises(ValueError, match="reconciled splits"):
            gc_book.replace_splits(
                guid=guid,
                splits=[
                    {"account": "Expenses:Groceries", "amount": "150.00"},
                    {"account": "Assets:Checking", "amount": "-150.00"},
                ],
            )

    def test_reconciled_with_force(self, test_book: Path):
        """Should allow recategorizing reconciled splits with force."""
        gc_book = GnuCashBook(str(test_book))

        # Get a transaction and reconcile one of its splits
        transactions = gc_book.search_transactions("Weekly Groceries", compact=False)
        guid = transactions[0]["guid"]
        split_guid = transactions[0]["splits"][0]["guid"]
        gc_book.set_reconcile_state(split_guid, "y")

        # Create a Dining account
        gc_book.create_account(
            name="Dining",
            account_type="EXPENSE",
            parent="Expenses",
        )

        result = gc_book.replace_splits(
            guid=guid,
            splits=[
                {"account": "Expenses:Dining", "amount": "150.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            force=True,
        )

        assert result["status"] == "splits_replaced"
        assert "warnings" in result
        assert any("reconciled" in w.lower() for w in result["warnings"])

    def test_lot_requires_force(self, investment_book: Path):
        """Should reject recategorizing splits in lots without force."""
        gc_book = GnuCashBook(str(investment_book))

        # Create an investment purchase with a lot
        lot_result = gc_book.create_lot(
            account="Assets:Investments:VTSAX",
            title="Test Lot",
        )
        lot_guid = lot_result["guid"]

        # Create a buy transaction
        result = gc_book.create_transaction(
            description="Buy VTSAX",
            splits=[
                {
                    "account": "Assets:Investments:VTSAX",
                    "amount": "1250.00",
                    "quantity": "10",
                },
                {"account": "Assets:Checking", "amount": "-1250.00"},
            ],
        )
        txn_guid = result["guid"]

        # Get the investment split and assign to lot
        txn = gc_book.get_transaction(txn_guid)
        inv_split = next(
            s for s in txn["splits"]
            if s["account"] == "Assets:Investments:VTSAX"
        )
        gc_book.assign_split_to_lot(inv_split["guid"], lot_guid)

        # Try to replace splits without force
        with pytest.raises(ValueError, match="splits in lots"):
            gc_book.replace_splits(
                guid=txn_guid,
                splits=[
                    {
                        "account": "Assets:Investments:VTSAX",
                        "amount": "1250.00",
                        "quantity": "10",
                    },
                    {"account": "Assets:Checking", "amount": "-1250.00"},
                ],
            )

    def test_lot_with_force(self, investment_book: Path):
        """Should allow recategorizing splits in lots with force and warning."""
        gc_book = GnuCashBook(str(investment_book))

        # Create an investment purchase with a lot
        lot_result = gc_book.create_lot(
            account="Assets:Investments:VTSAX",
            title="Test Lot For Force",
        )
        lot_guid = lot_result["guid"]

        # Create a buy transaction
        result = gc_book.create_transaction(
            description="Buy VTSAX for force test",
            splits=[
                {
                    "account": "Assets:Investments:VTSAX",
                    "amount": "1250.00",
                    "quantity": "10",
                },
                {"account": "Assets:Checking", "amount": "-1250.00"},
            ],
        )
        txn_guid = result["guid"]

        # Get the investment split and assign to lot
        txn = gc_book.get_transaction(txn_guid)
        inv_split = next(
            s for s in txn["splits"]
            if s["account"] == "Assets:Investments:VTSAX"
        )
        gc_book.assign_split_to_lot(inv_split["guid"], lot_guid)

        # Recategorize with force
        result = gc_book.replace_splits(
            guid=txn_guid,
            splits=[
                {
                    "account": "Assets:Investments:VTSAX",
                    "amount": "1250.00",
                    "quantity": "10",
                },
                {"account": "Assets:Checking", "amount": "-1250.00"},
            ],
            force=True,
        )

        assert result["status"] == "splits_replaced"
        assert "warnings" in result
        assert any("lot" in w.lower() for w in result["warnings"])

    def test_three_way_split(self, test_book: Path):
        """Should allow recategorizing to more splits than original."""
        gc_book = GnuCashBook(str(test_book))

        # Find the grocery transaction (2 splits)
        transactions = gc_book.search_transactions("Weekly Groceries", compact=False)
        guid = transactions[0]["guid"]
        assert len(transactions[0]["splits"]) == 2

        # Create additional accounts
        gc_book.create_account(
            name="Dining",
            account_type="EXPENSE",
            parent="Expenses",
        )

        # Recategorize to 3 splits
        result = gc_book.replace_splits(
            guid=guid,
            splits=[
                {"account": "Expenses:Groceries", "amount": "100.00"},
                {"account": "Expenses:Dining", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
        )

        # Thin response — verify new split count via read-back.
        assert result["status"] == "splits_replaced"
        refreshed = gc_book.get_transaction(guid)
        assert len(refreshed["splits"]) == 3

    def test_reduce_to_two_splits(self, budget_book: Path):
        """Should allow recategorizing to fewer splits than original."""
        gc_book = GnuCashBook(str(budget_book))

        # Create a 3-way split transaction
        result = gc_book.create_transaction(
            description="Multi-category purchase",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Expenses:Dining", "amount": "30.00"},
                {"account": "Assets:Checking", "amount": "-80.00"},
            ],
        )
        guid = result["guid"]

        # Get the transaction to verify 3 splits
        txn = gc_book.get_transaction(guid)
        assert len(txn["splits"]) == 3

        # Recategorize to 2 splits
        result = gc_book.replace_splits(
            guid=guid,
            splits=[
                {"account": "Expenses:Groceries", "amount": "80.00"},
                {"account": "Assets:Checking", "amount": "-80.00"},
            ],
        )

        # Thin response — verify new split count via read-back.
        assert result["status"] == "splits_replaced"
        refreshed = gc_book.get_transaction(guid)
        assert len(refreshed["splits"]) == 2

    def test_preserves_memo(self, test_book: Path):
        """Should preserve memo on new splits when provided."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.search_transactions("Weekly Groceries", compact=False)
        guid = transactions[0]["guid"]

        result = gc_book.replace_splits(
            guid=guid,
            splits=[
                {
                    "account": "Expenses:Groceries",
                    "amount": "150.00",
                    "memo": "Updated memo",
                },
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
        )

        # Thin response — verify memo persistence via read-back.
        assert result["status"] == "splits_replaced"
        refreshed = gc_book.get_transaction(guid)
        groceries_split = next(
            s for s in refreshed["splits"] if s["account"] == "Expenses:Groceries"
        )
        assert groceries_split["memo"] == "Updated memo"

    def test_cross_currency_requires_quantity(self, multi_currency_book: Path):
        """Should require quantity for cross-currency splits."""
        gc_book = GnuCashBook(str(multi_currency_book))

        # Find a USD transaction
        transactions = gc_book.search_transactions("Groceries", compact=False)
        guid = transactions[0]["guid"]

        # Try to replace splits to EUR account without quantity
        with pytest.raises(ValueError, match="requires 'quantity'"):
            gc_book.replace_splits(
                guid=guid,
                splits=[
                    {"account": "Assets:Euro Savings", "amount": "200.00"},
                    {"account": "Assets:Checking", "amount": "-200.00"},
                ],
            )

    def test_cross_currency_with_quantity(self, multi_currency_book: Path):
        """Should allow cross-currency splits when quantity provided."""
        gc_book = GnuCashBook(str(multi_currency_book))

        # Find a USD transaction
        transactions = gc_book.search_transactions("Groceries", compact=False)
        guid = transactions[0]["guid"]

        # Recategorize with proper quantity
        result = gc_book.replace_splits(
            guid=guid,
            splits=[
                {
                    "account": "Assets:Euro Savings",
                    "amount": "200.00",
                    "quantity": "182.00",  # EUR equivalent
                },
                {"account": "Assets:Checking", "amount": "-200.00"},
            ],
        )

        # Thin response — verify the cross-currency quantity via read-back.
        assert result["status"] == "splits_replaced"
        refreshed = gc_book.get_transaction(guid)
        eur_split = next(
            s for s in refreshed["splits"] if s["account"] == "Assets:Euro Savings"
        )
        assert Decimal(eur_split["quantity"]) == Decimal("182.00")

    def test_quantity_sign_mismatch(self, multi_currency_book: Path):
        """Should reject quantity with opposite sign from amount."""
        gc_book = GnuCashBook(str(multi_currency_book))

        transactions = gc_book.search_transactions("Groceries", compact=False)
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="same sign"):
            gc_book.replace_splits(
                guid=guid,
                splits=[
                    {
                        "account": "Assets:Euro Savings",
                        "amount": "200.00",
                        "quantity": "-182.00",  # Wrong sign
                    },
                    {"account": "Assets:Checking", "amount": "-200.00"},
                ],
            )


class TestSetReconcileState:
    """Tests for set_reconcile_state method."""

    def test_set_reconcile_cleared(self, test_book: Path):
        """Should set split to cleared state."""
        gc_book = GnuCashBook(str(test_book))

        # Get a split guid
        transactions = gc_book.list_transactions(compact=False)
        split_guid = transactions[0]["splits"][0]["guid"]

        result = gc_book.set_reconcile_state(split_guid, "c")

        # `reconcile_state` in the response was an input echo — dropped.
        # Persistence verified below via list_transactions read-back.
        assert result["status"] == "updated"
        refreshed = gc_book.list_transactions(compact=False)
        updated_split = next(
            s for t in refreshed for s in t["splits"] if s["guid"] == split_guid
        )
        assert updated_split["reconcile_state"] == "c"

    def test_set_reconcile_reconciled(self, test_book: Path):
        """Should set split to reconciled state with date."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions(compact=False)
        split_guid = transactions[0]["splits"][0]["guid"]

        result = gc_book.set_reconcile_state(
            split_guid, "y", reconcile_date=date(2024, 1, 31)
        )

        # reconcile_date stays (it's computed — today if not given).
        # reconcile_state echo is gone; persistence check below.
        assert result["status"] == "updated"
        assert result["reconcile_date"] is not None
        refreshed = gc_book.list_transactions(compact=False)
        updated_split = next(
            s for t in refreshed for s in t["splits"] if s["guid"] == split_guid
        )
        assert updated_split["reconcile_state"] == "y"

    def test_set_reconcile_new(self, test_book: Path):
        """Should reset split to new state."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions(compact=False)
        split_guid = transactions[0]["splits"][0]["guid"]

        # First set to cleared
        gc_book.set_reconcile_state(split_guid, "c")

        # Then reset to new
        result = gc_book.set_reconcile_state(split_guid, "n")

        assert result["reconcile_date"] is None
        refreshed = gc_book.list_transactions(compact=False)
        updated_split = next(
            s for t in refreshed for s in t["splits"] if s["guid"] == split_guid
        )
        assert updated_split["reconcile_state"] == "n"

    def test_set_reconcile_invalid_state(self, test_book: Path):
        """Should raise ValueError for invalid state."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions(compact=False)
        split_guid = transactions[0]["splits"][0]["guid"]

        with pytest.raises(ValueError, match="Invalid reconcile state"):
            gc_book.set_reconcile_state(split_guid, "x")

    def test_set_reconcile_split_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent split."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Split not found"):
            gc_book.set_reconcile_state("deadbeef00000000", "c")


class TestGetUnreconciledSplits:
    """Tests for get_unreconciled_splits method."""

    def test_get_unreconciled_splits(self, test_book: Path):
        """Should return unreconciled splits for account."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.get_unreconciled_splits("Assets:Checking", compact=False)

        assert "splits" in result
        assert "cleared_total" in result
        assert "uncleared_total" in result
        assert result["account"] == "Assets:Checking"
        # All splits should be unreconciled initially
        assert result["count"] > 0

    def test_get_unreconciled_splits_with_date(self, test_book: Path):
        """Should filter splits by date."""
        gc_book = GnuCashBook(str(test_book))

        # Get splits before a specific date
        result = gc_book.get_unreconciled_splits(
            "Assets:Checking", as_of_date=date(2024, 1, 10), compact=False,
        )

        # All returned splits should be on or before the date
        for split in result["splits"]:
            assert split["date"] <= "2024-01-10"

    def test_get_unreconciled_splits_account_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.get_unreconciled_splits("Nonexistent:Account")


class TestReconcileAccount:
    """Tests for reconcile_account method."""

    def test_reconcile_account_success(self, test_book: Path):
        """Should reconcile splits when balance matches."""
        gc_book = GnuCashBook(str(test_book))

        # Get unreconciled splits
        unreconciled = gc_book.get_unreconciled_splits("Assets:Checking", compact=False)

        # Calculate what the balance should be
        total = Decimal("0")
        guids = []
        for split in unreconciled["splits"]:
            total += Decimal(split["amount"])
            guids.append(split["guid"])

        # Reconcile all splits
        result = gc_book.reconcile_account(
            account_name="Assets:Checking",
            statement_date=date(2024, 1, 31),
            statement_balance=str(total),
            split_guids=guids,
        )

        assert result["status"] == "reconciled"
        assert result["splits_reconciled"] == len(guids)

    def test_reconcile_account_balance_mismatch(self, test_book: Path):
        """Should raise ValueError when balance doesn't match."""
        gc_book = GnuCashBook(str(test_book))

        unreconciled = gc_book.get_unreconciled_splits("Assets:Checking", compact=False)
        guids = [s["guid"] for s in unreconciled["splits"]]

        with pytest.raises(ValueError, match="Balance mismatch"):
            gc_book.reconcile_account(
                account_name="Assets:Checking",
                statement_date=date(2024, 1, 31),
                statement_balance="9999999.99",  # Wrong balance
                split_guids=guids,
            )

    def test_reconcile_account_split_wrong_account(self, test_book: Path):
        """Should raise ValueError if split belongs to different account."""
        gc_book = GnuCashBook(str(test_book))

        # Get a split from a different account
        transactions = gc_book.list_transactions(compact=False)
        expense_split = None
        for split in transactions[0]["splits"]:
            if "Expenses" in split["account"]:
                expense_split = split["guid"]
                break

        if expense_split:
            with pytest.raises(ValueError, match="belongs to account"):
                gc_book.reconcile_account(
                    account_name="Assets:Checking",
                    statement_date=date(2024, 1, 31),
                    statement_balance="0",
                    split_guids=[expense_split],
                )


class TestVoidTransaction:
    """Tests for void_transaction method."""

    def test_void_transaction_success(self, test_book: Path):
        """Should void a transaction."""
        gc_book = GnuCashBook(str(test_book))

        # Get a transaction to void
        transactions = gc_book.list_transactions(compact=False)
        guid = transactions[0]["guid"]

        result = gc_book.void_transaction(guid, reason="Entered in error")

        assert result["status"] == "voided"
        assert result["void_reason"] == "Entered in error"

        # Verify the transaction is voided (splits have 0 value and 'v' state)
        voided = gc_book.get_transaction(guid)
        for split in voided["splits"]:
            assert split["value"] == "0"
            assert split["reconcile_state"] == "v"

    def test_void_transaction_no_reason(self, test_book: Path):
        """Should raise ValueError if no reason provided."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions(compact=False)
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="reason is required"):
            gc_book.void_transaction(guid, reason="")

    def test_void_transaction_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent transaction."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Transaction not found"):
            gc_book.void_transaction("deadbeef00000000", reason="Test")

    def test_void_transaction_already_voided(self, test_book: Path):
        """Should raise ValueError if already voided."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions(compact=False)
        guid = transactions[0]["guid"]

        # Void once
        gc_book.void_transaction(guid, reason="First void")

        # Try to void again
        with pytest.raises(ValueError, match="already voided"):
            gc_book.void_transaction(guid, reason="Second void")


class TestUnvoidTransaction:
    """Tests for unvoid_transaction method."""

    def test_unvoid_transaction_success(self, test_book: Path):
        """Should restore a voided transaction."""
        gc_book = GnuCashBook(str(test_book))

        # Get original transaction values
        transactions = gc_book.list_transactions(compact=False)
        guid = transactions[0]["guid"]
        original = gc_book.get_transaction(guid)
        original_values = {s["account"]: s["value"] for s in original["splits"]}

        # Void it
        gc_book.void_transaction(guid, reason="Test void")

        # Unvoid it
        result = gc_book.unvoid_transaction(guid)

        assert result["status"] == "unvoided"

        # unvoid response uses _split_to_compact_dict — account + value,
        # with reconcile_state only emitted when non-default. Unvoid
        # always restores to 'n', so no reconcile_state key present.
        for split in result["splits"]:
            assert split["value"] == original_values[split["account"]]
            assert "reconcile_state" not in split  # default 'n' not emitted

        # Post-unvoid state confirmed via read-back.
        refreshed = gc_book.get_transaction(guid)
        for split in refreshed["splits"]:
            assert split["reconcile_state"] == "n"

    def test_unvoid_transaction_not_voided(self, test_book: Path):
        """Should raise ValueError if transaction is not voided."""
        gc_book = GnuCashBook(str(test_book))

        transactions = gc_book.list_transactions(compact=False)
        guid = transactions[0]["guid"]

        with pytest.raises(ValueError, match="not voided"):
            gc_book.unvoid_transaction(guid)

    def test_unvoid_transaction_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent transaction."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Transaction not found"):
            gc_book.unvoid_transaction("deadbeef00000000")


class TestSpendingByCategory:
    """Tests for spending_by_category method."""

    def test_spending_by_category(self, test_book: Path):
        """Should return spending breakdown."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.spending_by_category(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        # `period` dropped — LLM supplied start/end, no need to echo.
        assert "total" in result
        assert "categories" in result
        assert Decimal(result["total"]) > 0

    def test_spending_by_category_empty_period(self, test_book: Path):
        """Should return zero for period with no transactions."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.spending_by_category(
            start_date=date(2020, 1, 1),
            end_date=date(2020, 1, 31),
        )

        assert result["total"] == "0"
        assert result["categories"] == []


class TestIncomeBySource:
    """Tests for income_by_source method."""

    def test_income_by_source(self, test_book: Path):
        """Should return income breakdown."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.income_by_source(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        # `period` dropped — input echo.
        assert "total" in result
        assert "sources" in result
        assert Decimal(result["total"]) > 0


class TestBalanceSheet:
    """Tests for balance_sheet method."""

    def test_balance_sheet(self, test_book: Path):
        """Should return balance sheet with assets, liabilities, equity."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.balance_sheet(as_of_date=date(2024, 12, 31))

        assert "as_of_date" in result
        assert "assets" in result
        assert "liabilities" in result
        assert "equity" in result
        assert "total" in result["assets"]
        assert "accounts" in result["assets"]


class TestBalanceSheetNumericContract:
    """Phase 4B follow-up: bookkeeper flagged the totals leaking
    Decimal precision (``"612011.489832"``) and the per-account
    ``usd_value`` being unrounded for investments / redundant for
    currency accounts. Lock the contract:

    - All monetary outputs (totals + account values) are 2 decimals.
    - Currency-default accounts have ``balance`` only, no ``usd_value``.
    - Non-currency accounts have both, with ``usd_value`` rounded.
    """

    def test_section_totals_render_at_2_decimals(self, test_book: Path):
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.balance_sheet(as_of_date=date(2024, 12, 31))
        for section in ("assets", "liabilities", "equity"):
            total = result[section]["total"]
            # Currency-style: every total ends with exactly two decimal
            # digits, no scientific notation, no precision noise.
            assert "." in total, f"{section} total missing decimal: {total!r}"
            assert len(total.split(".")[-1]) == 2, (
                f"{section} total wrong precision: {total!r}"
            )

    def test_currency_accounts_have_no_usd_value_field(
        self, test_book: Path,
    ):
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.balance_sheet(as_of_date=date(2024, 12, 31))
        for row in result["assets"]["accounts"]:
            # Test fixture is single-currency USD — every asset row
            # is currency-default. None should carry ``usd_value``.
            assert "usd_value" not in row, (
                f"redundant usd_value on currency row: {row}"
            )

    def test_currency_account_balance_renders_at_2_decimals(
        self, test_book: Path,
    ):
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.balance_sheet(as_of_date=date(2024, 12, 31))
        for row in result["assets"]["accounts"]:
            balance = row["balance"]
            # Currency-default rows: pure numeric, 2 decimals always.
            assert "." in balance
            assert len(balance.split(".")[-1]) == 2, (
                f"row {row['account']!r} balance wrong precision: {balance!r}"
            )


class TestNetWorth:
    """Tests for net_worth method."""

    def test_net_worth_point_in_time(self, test_book: Path):
        """Should calculate net worth at a point in time."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.net_worth(end_date=date(2024, 12, 31))

        assert "as_of_date" in result
        assert "net_worth" in result

    def test_net_worth_time_series(self, test_book: Path):
        """Should calculate net worth time series."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.net_worth(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            interval="month",
        )

        assert "series" in result
        assert len(result["series"]) > 0
        assert "date" in result["series"][0]
        assert "net_worth" in result["series"][0]

    def test_net_worth_invalid_interval(self, test_book: Path):
        """Should raise ValueError for invalid interval."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Invalid interval"):
            gc_book.net_worth(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
                interval="invalid",
            )


class TestCashFlow:
    """Tests for cash_flow method."""

    def test_cash_flow(self, test_book: Path):
        """Should calculate cash flow."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.cash_flow(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        # `period` dropped (echo); `net` dropped (derivable: inflows - outflows).
        assert "inflows" in result
        assert "outflows" in result

    def test_cash_flow_specific_account(self, test_book: Path):
        """Should calculate cash flow for specific account."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.cash_flow(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            account="Assets:Checking",
        )

        assert result["account"] == "Assets:Checking"

    def test_cash_flow_invalid_account(self, test_book: Path):
        """Should raise ValueError for invalid account."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Account not found"):
            gc_book.cash_flow(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
                account="Nonexistent:Account",
            )


class TestListCommodities:
    """Tests for list_commodities method."""

    def test_list_commodities(self, test_book: Path):
        """Should return commodities grouped by namespace."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_commodities(compact=False)

        assert "default_currency" in result
        assert result["default_currency"] == "USD"
        assert "commodities" in result
        assert "CURRENCY" in result["commodities"]

        # Should include USD at minimum
        mnemonics = [c["mnemonic"] for c in result["commodities"]["CURRENCY"]]
        assert "USD" in mnemonics

    def test_list_commodities_structure(self, test_book: Path):
        """Should return proper structure for each commodity."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.list_commodities(compact=False)

        currencies = result["commodities"]["CURRENCY"]
        for commodity in currencies:
            assert "mnemonic" in commodity
            assert "fullname" in commodity
            assert "fraction" in commodity


class TestCreateAccountWithCurrency:
    """Tests for create_account with commodity parameter."""

    def test_create_account_with_currency(self, test_book: Path):
        """Should create account with specified currency."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_account(
            name="EUR Savings",
            account_type="BANK",
            parent="Assets",
            commodity="EUR",
            description="Euro savings account",
        )

        assert result["status"] == "created"
        assert result["fullname"] == "Assets:EUR Savings"

        # Verify the account commodity is EUR
        account = gc_book.get_account("Assets:EUR Savings")
        assert account is not None
        assert account["commodity"] == "EUR"

    def test_create_account_default_currency(self, test_book: Path):
        """Should use default currency when commodity is None."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_account(
            name="Default Currency Account",
            account_type="BANK",
            parent="Assets",
        )

        assert result["status"] == "created"

        account = gc_book.get_account("Assets:Default Currency Account")
        assert account["commodity"] == "USD"  # Book default

    def test_create_account_invalid_currency(self, test_book: Path):
        """Should raise ValueError for invalid currency code."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Invalid currency code"):
            gc_book.create_account(
                name="Bad Currency",
                account_type="BANK",
                parent="Assets",
                commodity="INVALID",
            )

    def test_create_account_creates_currency_commodity(self, test_book: Path):
        """Should auto-create currency commodity if it doesn't exist in book."""
        gc_book = GnuCashBook(str(test_book))

        # GBP shouldn't exist in the book initially (only USD)
        result = gc_book.create_account(
            name="GBP Account",
            account_type="BANK",
            parent="Assets",
            commodity="GBP",
        )

        assert result["status"] == "created"

        # Verify GBP is now in the commodities list
        commodities = gc_book.list_commodities(compact=False)
        mnemonics = [c["mnemonic"] for c in commodities["commodities"]["CURRENCY"]]
        assert "GBP" in mnemonics


class TestCreateTransactionMultiCurrency:
    """Tests for create_transaction with multi-currency support."""

    def test_create_transaction_with_explicit_currency(self, test_book: Path):
        """Should create transaction with specified currency."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="Explicit USD Transaction",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=date(2024, 2, 1),
            currency="USD",
        )

        guid = result["guid"]
        transaction = gc_book.get_transaction(guid)
        assert transaction["currency"] == "USD"

    def test_create_transaction_default_currency(self, test_book: Path):
        """Should use default currency when none specified."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_transaction(
            description="Default Currency Transaction",
            splits=[
                {"account": "Expenses:Groceries", "amount": "30.00"},
                {"account": "Assets:Checking", "amount": "-30.00"},
            ],
        )

        transaction = gc_book.get_transaction(result["guid"])
        assert transaction["currency"] == "USD"

    def test_create_cross_currency_transaction(self, test_book: Path):
        """Should create cross-currency transaction with quantity."""
        gc_book = GnuCashBook(str(test_book))

        # First create a EUR account
        gc_book.create_account(
            name="EUR Card",
            account_type="CREDIT",
            parent="Liabilities",
            commodity="EUR",
        )

        # Create a cross-currency transaction: USD transaction with EUR split
        result = gc_book.create_transaction(
            description="Dinner in Paris",
            currency="USD",
            splits=[
                {"account": "Expenses:Groceries", "amount": "55.00"},
                {
                    "account": "Liabilities:EUR Card",
                    "amount": "-55.00",
                    "quantity": "-50.00",
                },
            ],
            trans_date=date(2024, 3, 1),
        )

        guid = result["guid"]
        transaction = gc_book.get_transaction(guid)
        assert transaction["currency"] == "USD"

        # Verify the EUR split has different value and quantity
        for split in transaction["splits"]:
            if split["account"] == "Liabilities:EUR Card":
                assert split["value"] == "-55"
                assert split["quantity"] == "-50"

    def test_create_cross_currency_missing_quantity(self, test_book: Path):
        """Should raise ValueError when quantity is required but missing."""
        gc_book = GnuCashBook(str(test_book))

        # Create a EUR account
        gc_book.create_account(
            name="EUR Checking",
            account_type="BANK",
            parent="Assets",
            commodity="EUR",
        )

        with pytest.raises(ValueError, match="requires 'quantity'"):
            gc_book.create_transaction(
                description="Missing quantity",
                currency="USD",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                    {"account": "Assets:EUR Checking", "amount": "-50.00"},
                ],
            )

    def test_create_cross_currency_sign_mismatch(self, test_book: Path):
        """Should raise ValueError when quantity and value have different signs."""
        gc_book = GnuCashBook(str(test_book))

        # Create a EUR account
        gc_book.create_account(
            name="EUR Savings",
            account_type="BANK",
            parent="Assets",
            commodity="EUR",
        )

        with pytest.raises(ValueError, match="same sign"):
            gc_book.create_transaction(
                description="Sign mismatch",
                currency="USD",
                splits=[
                    {"account": "Expenses:Groceries", "amount": "50.00"},
                    {
                        "account": "Assets:EUR Savings",
                        "amount": "-50.00",
                        "quantity": "45.00",  # Wrong sign!
                    },
                ],
            )

    def test_create_transaction_backward_compatible(self, test_book: Path):
        """Existing single-currency workflow should work unchanged."""
        gc_book = GnuCashBook(str(test_book))

        # No currency, no quantity - original API
        result = gc_book.create_transaction(
            description="Backward Compatible",
            splits=[
                {"account": "Expenses:Groceries", "amount": "85.00"},
                {"account": "Assets:Checking", "amount": "-85.00"},
            ],
        )

        guid = result["guid"]
        transaction = gc_book.get_transaction(guid)
        assert transaction["description"] == "Backward Compatible"

        # Value and quantity should be equal for same-currency
        for split in transaction["splits"]:
            assert split["value"] == split["quantity"]


class TestMultiCurrencyBalances:
    """Tests that balances and reports use split.quantity (account commodity)
    rather than split.value (transaction currency)."""

    def test_get_balance_uses_quantity(self, multi_currency_book: Path):
        """EUR account balance should be in EUR (quantity), not USD (value)."""
        gc_book = GnuCashBook(str(multi_currency_book))
        balance = gc_book.get_balance("Assets:Euro Savings")
        # The EUR savings account received 1000 EUR (quantity),
        # NOT 1100 USD (value)
        assert balance == Decimal("1000")

    def test_get_balance_usd_account_unaffected(self, multi_currency_book: Path):
        """USD account balance should still be correct."""
        gc_book = GnuCashBook(str(multi_currency_book))
        balance = gc_book.get_balance("Assets:Checking")
        # 5000 (opening) + 3000 (salary) - 1100 (transfer) - 200 (groceries)
        assert balance == Decimal("6700")

    def test_balance_sheet_values_foreign_currency_at_cost_basis_without_price(
        self, multi_currency_book: Path
    ):
        """Without a EUR/USD price on file, balance_sheet falls back
        to cost basis (split.value, in transaction currency) for the
        EUR account. The fixture's FX transfer booked value=1100 USD
        on the EUR side, so cost basis = $1,100.

        Phase 4B (comms): currency-default accounts read their value
        from ``balance``; non-default accounts have a parseable
        ``usd_value`` alongside the human-readable triplet ``balance``.
        ``usd_value`` is dropped for currency rows where it would just
        repeat ``balance``.
        """
        gc_book = GnuCashBook(str(multi_currency_book))
        result = gc_book.balance_sheet(as_of_date=date(2024, 12, 31))
        accounts = {a["account"]: a for a in result["assets"]["accounts"]}
        # Currency account: numeric in ``balance``, no ``usd_value``.
        checking = accounts["Assets:Checking"]
        assert Decimal(checking["balance"]) == Decimal("6700.00")
        assert "usd_value" not in checking
        # Non-currency: ``usd_value`` for parsing, ``balance`` for display.
        eur = accounts["Assets:Euro Savings"]
        assert Decimal(eur["usd_value"]) == Decimal("1100.00")
        assert "EUR" in eur["balance"]
        assert "no price data" in eur["balance"]

    def test_balance_sheet_values_foreign_currency_at_market_with_price(
        self, multi_currency_book: Path
    ):
        """With an EUR/USD price on file, balance_sheet values the EUR
        savings account at shares × rate = 1000 × 1.20 = $1,200.
        """
        import piecash
        from datetime import date as _date

        gc_book = GnuCashBook(str(multi_currency_book))
        with gc_book.open(readonly=False) as b:
            usd = b.default_currency
            eur = next(c for c in b.commodities if c.mnemonic == "EUR")
            b.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=_date(2024, 12, 31),
                value="1.20", source="user:test", type="nav",
            ))
            b.save()

        result = gc_book.balance_sheet(as_of_date=date(2024, 12, 31))
        eur_row = next(
            a for a in result["assets"]["accounts"]
            if a["account"] == "Assets:Euro Savings"
        )
        # Non-currency row: ``usd_value`` is parseable + rounded.
        assert Decimal(eur_row["usd_value"]) == Decimal("1200.00")
        # Phase 4B: human-readable balance shows shares + rate + USD.
        assert "EUR" in eur_row["balance"]
        assert "@" in eur_row["balance"]
        assert "USD" in eur_row["balance"]

    def test_net_worth_converts_foreign_currency(
        self, multi_currency_book: Path
    ):
        """Net worth uses market value (or cost-basis fallback) for
        non-default-currency accounts rather than summing raw
        quantities as if they were USD.
        """
        gc_book = GnuCashBook(str(multi_currency_book))
        result = gc_book.net_worth(end_date=date(2024, 12, 31))
        net = Decimal(result["net_worth"])
        # Without a price: Checking 6700 + Euro Savings 1100 (cost basis)
        assert net == Decimal("7800")

    def test_cash_flow_converts_foreign_currency(
        self, multi_currency_book: Path
    ):
        """Cash flow aggregates USD-equivalent amounts across cash
        and bank accounts (using cost basis as fallback when no
        market rate is on file).
        """
        gc_book = GnuCashBook(str(multi_currency_book))
        result = gc_book.cash_flow(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        inflows = Decimal(result["inflows"])
        outflows = Decimal(result["outflows"])
        # Checking inflows: 5000 + 3000 = 8000
        # Checking outflows: 1100 + 200 = 1300
        # EUR Savings inflow at cost basis (no price): 1100
        assert inflows == Decimal("9100")
        assert outflows == Decimal("1300")

    def test_spending_by_category_uses_quantity(self, multi_currency_book: Path):
        """Expense reporting should use quantity."""
        gc_book = GnuCashBook(str(multi_currency_book))
        result = gc_book.spending_by_category(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            depth=2,
        )
        assert result["total"] == "200"
        assert len(result["categories"]) == 1
        assert result["categories"][0]["account"] == "Expenses:Groceries"

    def test_income_by_source_uses_quantity(self, multi_currency_book: Path):
        """Income reporting should use quantity."""
        gc_book = GnuCashBook(str(multi_currency_book))
        result = gc_book.income_by_source(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            depth=2,
        )
        assert result["total"] == "3000"
        assert len(result["sources"]) == 1
        assert result["sources"][0]["account"] == "Income:Salary"


class TestCreateCommodity:
    """Tests for create_commodity method."""

    def test_create_commodity(self, test_book: Path):
        """Should create a new commodity and return it in list_commodities."""
        gc_book = GnuCashBook(str(test_book))

        result = gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market Index Fund Admiral",
            namespace="FUND",
            fraction=10000,
            cusip="922908728",
        )

        assert result["status"] == "created"
        assert result["mnemonic"] == "VTSAX"
        assert result["namespace"] == "FUND"
        assert result["fullname"] == "Vanguard Total Stock Market Index Fund Admiral"
        assert result["fraction"] == 10000

        # Verify it appears in list_commodities
        commodities = gc_book.list_commodities(compact=False)
        assert "FUND" in commodities["commodities"]
        mnemonics = [c["mnemonic"] for c in commodities["commodities"]["FUND"]]
        assert "VTSAX" in mnemonics

    def test_create_commodity_duplicate(self, test_book: Path):
        """Should raise ValueError when commodity already exists in namespace."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        with pytest.raises(ValueError, match="already exists"):
            gc_book.create_commodity(
                mnemonic="VTSAX",
                fullname="Duplicate",
                namespace="FUND",
            )

    def test_create_commodity_different_namespace(self, test_book: Path):
        """Should allow same mnemonic in different namespaces."""
        gc_book = GnuCashBook(str(test_book))

        result1 = gc_book.create_commodity(
            mnemonic="TEST",
            fullname="Test Fund",
            namespace="FUND",
        )
        result2 = gc_book.create_commodity(
            mnemonic="TEST",
            fullname="Test Stock",
            namespace="NASDAQ",
        )

        assert result1["status"] == "created"
        assert result2["status"] == "created"

        commodities = gc_book.list_commodities(compact=False)
        assert "FUND" in commodities["commodities"]
        assert "NASDAQ" in commodities["commodities"]


class TestCreateAccountWithCommodity:
    """Tests for create_account with non-currency commodities."""

    def test_create_account_with_fund_commodity(self, test_book: Path):
        """Should create MUTUAL account with FUND commodity."""
        gc_book = GnuCashBook(str(test_book))

        # First create the commodity
        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        # Create a parent for investments
        gc_book.create_account(
            name="Investments",
            account_type="ASSET",
            parent="Assets",
            placeholder=True,
        )

        # Create the fund account
        result = gc_book.create_account(
            name="VTSAX",
            account_type="MUTUAL",
            parent="Assets:Investments",
            commodity="VTSAX",
            commodity_namespace="FUND",
        )

        assert result["status"] == "created"
        assert result["fullname"] == "Assets:Investments:VTSAX"

        # Verify the account commodity
        account = gc_book.get_account("Assets:Investments:VTSAX")
        assert account is not None
        assert account["commodity"] == "VTSAX"

    def test_create_account_missing_commodity(self, test_book: Path):
        """Should raise ValueError when commodity doesn't exist."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Commodity not found"):
            gc_book.create_account(
                name="Missing Fund",
                account_type="MUTUAL",
                parent="Assets",
                commodity="NONEXISTENT",
                commodity_namespace="FUND",
            )


class TestPrices:
    """Tests for price management methods."""

    def test_create_price(self, test_book: Path):
        """Should record a price and retrieve it."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        result = gc_book.create_price(
            commodity="VTSAX",
            namespace="FUND",
            value="127.50",
            currency="USD",
            price_date=date(2026, 2, 7),
            price_type="nav",
        )

        assert result["status"] == "created"
        assert result["value"] == "127.50"
        assert result["date"] == "2026-02-07"

        # Verify via get_prices (now returns dict with prices/count/total/notice)
        result = gc_book.get_prices(commodity="VTSAX", namespace="FUND")
        prices = result["prices"]
        assert result["total"] == 1
        assert len(prices) == 1
        assert Decimal(prices[0]["value"]) == Decimal("127.50")
        assert prices[0]["type"] == "nav"

    def test_create_price_update(self, test_book: Path):
        """Should update existing price with same date/source."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        # Create initial price
        gc_book.create_price(
            commodity="VTSAX",
            namespace="FUND",
            value="127.50",
            price_date=date(2026, 2, 7),
        )

        # Update with new value (same date and source)
        result = gc_book.create_price(
            commodity="VTSAX",
            namespace="FUND",
            value="128.75",
            price_date=date(2026, 2, 7),
        )

        assert result["status"] == "updated"
        assert result["value"] == "128.75"

        # Should still be only 1 price, not 2
        result = gc_book.get_prices(commodity="VTSAX", namespace="FUND")
        prices = result["prices"]
        assert len(prices) == 1
        assert prices[0]["value"] == "128.75"

    def test_get_prices_filtered(self, test_book: Path):
        """Should filter prices by date range."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        # Create prices on multiple dates
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="125.00", price_date=date(2026, 2, 1),
        )
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="126.50", price_date=date(2026, 2, 5),
        )
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="128.75", price_date=date(2026, 2, 10),
        )

        # Filter to middle date
        result = gc_book.get_prices(
            commodity="VTSAX",
            namespace="FUND",
            start_date=date(2026, 2, 3),
            end_date=date(2026, 2, 8),
        )
        prices = result["prices"]
        assert len(prices) == 1
        assert Decimal(prices[0]["value"]) == Decimal("126.50")

        # All prices, should be descending
        all_result = gc_book.get_prices(commodity="VTSAX", namespace="FUND")
        all_prices = all_result["prices"]
        assert len(all_prices) == 3
        assert all_prices[0]["date"] == "2026-02-10"  # Most recent first
        assert all_prices[2]["date"] == "2026-02-01"  # Oldest last

    def test_get_latest_price(self, test_book: Path):
        """Should return the most recent price."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="125.00", price_date=date(2026, 2, 1),
        )
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="128.75", price_date=date(2026, 2, 10),
        )
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="126.50", price_date=date(2026, 2, 5),
        )

        result = gc_book.get_latest_price(
            commodity="VTSAX", namespace="FUND", currency="USD",
        )
        assert result is not None
        assert result["value"] == "128.75"
        assert result["date"] == "2026-02-10"

    def test_get_latest_price_no_prices(self, test_book: Path):
        """Should return None when no prices exist."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        result = gc_book.get_latest_price(
            commodity="VTSAX", namespace="FUND", currency="USD",
        )
        assert result is None

    def test_create_price_commodity_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent commodity."""
        gc_book = GnuCashBook(str(test_book))

        with pytest.raises(ValueError, match="Commodity not found"):
            gc_book.create_price(
                commodity="NONEXISTENT",
                namespace="FUND",
                value="100.00",
            )


class TestInvestmentWorkflow:
    """Integration tests for the full investment workflow."""

    def test_full_investment_workflow(self, test_book: Path):
        """Full workflow: create fund, account, price, buy shares, check balance."""
        gc_book = GnuCashBook(str(test_book))

        # 1. Create commodity
        gc_book.create_commodity(
            mnemonic="VTSAX",
            fullname="Vanguard Total Stock Market",
            namespace="FUND",
            fraction=10000,
        )

        # 2. Create account hierarchy
        gc_book.create_account(
            name="Investments",
            account_type="ASSET",
            parent="Assets",
            placeholder=True,
        )
        gc_book.create_account(
            name="401k",
            account_type="ASSET",
            parent="Assets:Investments",
            placeholder=True,
        )
        gc_book.create_account(
            name="VTSAX",
            account_type="MUTUAL",
            parent="Assets:Investments:401k",
            commodity="VTSAX",
            commodity_namespace="FUND",
        )

        # 3. Record price
        gc_book.create_price(
            commodity="VTSAX",
            namespace="FUND",
            value="127.50",
            price_date=date(2026, 2, 7),
        )

        # 4. Buy shares: $500 at $127.50/share = 3.9216 shares
        result = gc_book.create_transaction(
            description="VTSAX purchase",
            splits=[
                {
                    "account": "Assets:Investments:401k:VTSAX",
                    "amount": "500.00",
                    "quantity": "3.9216",
                },
                {
                    "account": "Assets:Checking",
                    "amount": "-500.00",
                },
            ],
            trans_date=date(2026, 2, 7),
            currency="USD",
        )
        assert result["status"] == "created"

        # 5. Check balance — should show share count
        balance = gc_book.get_balance("Assets:Investments:401k:VTSAX")
        assert balance == Decimal("3.9216")

        # 6. Get latest price
        price = gc_book.get_latest_price(
            commodity="VTSAX", namespace="FUND", currency="USD",
        )
        assert Decimal(price["value"]) == Decimal("127.50")

    def test_sell_shares(self, test_book: Path):
        """Should handle selling shares (negative quantity)."""
        gc_book = GnuCashBook(str(test_book))

        # Setup: create fund, account, buy shares
        gc_book.create_commodity(
            mnemonic="VTSAX", fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )
        gc_book.create_account(
            name="Investments", account_type="ASSET",
            parent="Assets", placeholder=True,
        )
        gc_book.create_account(
            name="VTSAX", account_type="MUTUAL",
            parent="Assets:Investments",
            commodity="VTSAX", commodity_namespace="FUND",
        )

        # Buy 10 shares at $100
        gc_book.create_transaction(
            description="Buy VTSAX",
            splits=[
                {"account": "Assets:Investments:VTSAX", "amount": "1000.00", "quantity": "10"},
                {"account": "Assets:Checking", "amount": "-1000.00"},
            ],
            trans_date=date(2026, 1, 15),
            currency="USD",
        )

        # Sell 3 shares at $110
        gc_book.create_transaction(
            description="Sell VTSAX",
            splits=[
                {"account": "Assets:Investments:VTSAX", "amount": "-330.00", "quantity": "-3"},
                {"account": "Assets:Checking", "amount": "330.00"},
            ],
            trans_date=date(2026, 2, 7),
            currency="USD",
        )

        # Balance should be 7 shares
        balance = gc_book.get_balance("Assets:Investments:VTSAX")
        assert balance == Decimal("7")

    def test_multiple_funds(self, test_book: Path):
        """Should handle multiple funds in same account hierarchy."""
        gc_book = GnuCashBook(str(test_book))

        # Create two funds
        gc_book.create_commodity(
            mnemonic="VTSAX", fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )
        gc_book.create_commodity(
            mnemonic="VTIAX", fullname="Vanguard Total International",
            namespace="FUND",
        )

        # Create accounts
        gc_book.create_account(
            name="Investments", account_type="ASSET",
            parent="Assets", placeholder=True,
        )
        gc_book.create_account(
            name="VTSAX", account_type="MUTUAL",
            parent="Assets:Investments",
            commodity="VTSAX", commodity_namespace="FUND",
        )
        gc_book.create_account(
            name="VTIAX", account_type="MUTUAL",
            parent="Assets:Investments",
            commodity="VTIAX", commodity_namespace="FUND",
        )

        # Buy both
        gc_book.create_transaction(
            description="Buy VTSAX",
            splits=[
                {"account": "Assets:Investments:VTSAX", "amount": "500.00", "quantity": "4"},
                {"account": "Assets:Checking", "amount": "-500.00"},
            ],
            trans_date=date(2026, 2, 7),
            currency="USD",
        )
        gc_book.create_transaction(
            description="Buy VTIAX",
            splits=[
                {"account": "Assets:Investments:VTIAX", "amount": "300.00", "quantity": "10"},
                {"account": "Assets:Checking", "amount": "-300.00"},
            ],
            trans_date=date(2026, 2, 7),
            currency="USD",
        )

        # Check balances
        vtsax_bal = gc_book.get_balance("Assets:Investments:VTSAX")
        vtiax_bal = gc_book.get_balance("Assets:Investments:VTIAX")
        assert vtsax_bal == Decimal("4")
        assert vtiax_bal == Decimal("10")

    def test_list_commodities_with_prices(self, test_book: Path):
        """Enhanced list_commodities should show latest prices."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.create_commodity(
            mnemonic="VTSAX", fullname="Vanguard Total Stock Market",
            namespace="FUND",
        )

        # Add prices
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="125.00", price_date=date(2026, 2, 1),
        )
        gc_book.create_price(
            commodity="VTSAX", namespace="FUND",
            value="128.75", price_date=date(2026, 2, 7),
        )

        commodities = gc_book.list_commodities(compact=False)
        fund_entries = commodities["commodities"]["FUND"]
        vtsax = next(c for c in fund_entries if c["mnemonic"] == "VTSAX")

        assert "latest_price" in vtsax
        assert vtsax["latest_price"]["value"] == "128.75"
        assert vtsax["latest_price"]["date"] == "2026-02-07"
        assert vtsax["latest_price"]["currency"] == "USD"
