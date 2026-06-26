"""Locale-robust account resolution (i18n).

See ``specs/I18N_ACCOUNT_RESOLUTION_SPEC.md``. The governing principle:
identify accounts by ``GNCAccountType`` (a locale-invariant enum),
never by an English name. These tests run against the ``localized_book``
fixture — a German-named chart with no account literally named "Income"
or "Expenses" — so any English-name assumption fails loudly.
"""

from datetime import date
from decimal import Decimal

import piecash
import pytest

from gnucash_mcp.book import GnuCashBook


class TestTopLevelAccountOfType:
    """The ``_top_level_account_of_type`` chokepoint — the
    locale-invariant replacement for ``_find_account(book, "Income")``.
    """

    def test_finds_localized_income_parent(self, localized_book):
        # The German top-level income account is "Erträge". Resolution
        # is by TYPE, so it is found despite never being named "Income".
        gb = GnuCashBook(str(localized_book))
        with gb.open() as b:
            acct, notice = gb._top_level_account_of_type(b, "INCOME")
            assert acct is not None
            assert acct.fullname == "Erträge"
            assert notice is None

    def test_finds_localized_expense_parent(self, localized_book):
        gb = GnuCashBook(str(localized_book))
        with gb.open() as b:
            acct, notice = gb._top_level_account_of_type(b, "EXPENSE")
            assert acct is not None
            assert acct.fullname == "Aufwand"
            assert notice is None

    def test_ignores_nested_account_of_same_type(self, localized_book):
        # "Umsatzerlöse" is INCOME but nested under "Erträge"; only the
        # true top-level (root child) account must be returned.
        gb = GnuCashBook(str(localized_book))
        with gb.open() as b:
            acct, _ = gb._top_level_account_of_type(b, "INCOME")
            assert acct.fullname == "Erträge"

    def test_returns_none_when_type_absent(self, localized_book):
        # The German chart has no top-level TRADING account.
        gb = GnuCashBook(str(localized_book))
        with gb.open() as b:
            acct, notice = gb._top_level_account_of_type(b, "TRADING")
            assert acct is None
            assert notice is None

    def test_ambiguous_returns_lowest_fullname_with_notice(self, tmp_path):
        # Two top-level INCOME accounts: deterministic lowest-fullname
        # pick, plus a surfaced ambiguity notice.
        path = tmp_path / "ambig.gnucash"
        book = piecash.create_book(str(path), currency="EUR", overwrite=True)
        root = book.root_account
        eur = book.default_currency
        piecash.Account(
            name="Erträge", type="INCOME", parent=root,
            commodity=eur, placeholder=True,
        )
        piecash.Account(
            name="Andere Erträge", type="INCOME", parent=root,
            commodity=eur, placeholder=True,
        )
        book.save()
        book.close()

        gb = GnuCashBook(str(path))
        with gb.open() as b:
            acct, notice = gb._top_level_account_of_type(b, "INCOME")
            assert acct is not None
            assert acct.fullname == "Andere Erträge"  # 'A' < 'E'
            assert notice is not None
            assert notice["type"] == "ambiguous_top_level_account"
            assert notice["account_type"] == "INCOME"
            assert set(notice["candidates"]) == {"Andere Erträge", "Erträge"}
            assert notice["chosen"] == "Andere Erträge"


class TestLocalizedHelperAccounts:
    """The Tier-A blocker: the FX and discount helper-account
    resolvers used to call ``_find_account(book, "Income")`` and
    **throw** on a localized book (no account named "Income"). They now
    resolve the parent by type and create under the German roots.
    """

    def test_fx_account_autocreates_under_localized_income(
        self, localized_book
    ):
        gb = GnuCashBook(str(localized_book))
        with gb.open(readonly=False) as b:
            # This call raised ValueError before the fix.
            acct, _ = gb._get_or_create_fx_account(b)
            assert acct is not None
            assert acct.type == "INCOME"
            assert acct.commodity == b.default_currency
            # Parent resolved by TYPE → the German income root.
            assert acct.parent.fullname == "Erträge"
            assert acct.fullname == "Erträge:Foreign Exchange Gain/Loss"
            b.save()

        # Idempotent: a second call resolves the same account (the
        # English leaf self-matches the fuzzy layer) rather than
        # creating a duplicate.
        with gb.open(readonly=False) as b:
            acct2, _ = gb._get_or_create_fx_account(b)
            assert acct2.fullname == "Erträge:Foreign Exchange Gain/Loss"

    def test_discount_accounts_autocreate_under_localized_parents(
        self, localized_book
    ):
        gb = GnuCashBook(str(localized_book))
        with gb.open(readonly=False) as b:
            # Sales discount → EXPENSE side → German expense root.
            sales, _ = gb._get_or_create_discount_account(
                b, owner_type_is_bill=False
            )
            assert sales.type == "EXPENSE"
            assert sales.parent.fullname == "Aufwand"

            # Purchase discount → INCOME side → German income root.
            purchase, _ = gb._get_or_create_discount_account(
                b, owner_type_is_bill=True
            )
            assert purchase.type == "INCOME"
            assert purchase.parent.fullname == "Erträge"
            b.save()


class TestAutoBalancingAccountDetection:
    """Tier C: GnuCash localizes the "Imbalance"/"Orphan" leading word,
    so the old English-prefix check went dark on localized books. The
    structural + catalog detector must catch them in any locale while
    leaving ordinary accounts alone.
    """

    @staticmethod
    def _make_book(path, specs):
        # specs: (name, type, parent_name | None) — parent None = root.
        book = piecash.create_book(str(path), currency="EUR", overwrite=True)
        root = book.root_account
        eur = book.default_currency
        made = {}
        for name, atype, parent in specs:
            made[name] = piecash.Account(
                name=name, type=atype,
                parent=root if parent is None else made[parent],
                commodity=eur,
            )
        book.save()
        book.close()

    def test_detects_english_and_localized(self, tmp_path):
        path = tmp_path / "bal.gnucash"
        self._make_book(path, [
            ("Aktiva", "ASSET", None),
            ("Imbalance-USD", "BANK", None),        # English, suffixed
            ("Ausgleichskonto-EUR", "BANK", None),  # de imbalance, suffixed
            ("Ausbuchungskonto", "BANK", None),     # de orphan, unsuffixed
            ("Girokonto", "BANK", "Aktiva"),        # ordinary nested bank
            ("Tagesgeld", "BANK", None),            # ordinary root bank
        ])
        gb = GnuCashBook(str(path))
        with gb.open() as b:
            root = b.root_account
            by_name = {a.name: a for a in b.accounts}
            assert gb._is_auto_balancing_account(by_name["Imbalance-USD"], root)
            assert gb._is_auto_balancing_account(
                by_name["Ausgleichskonto-EUR"], root
            )
            assert gb._is_auto_balancing_account(
                by_name["Ausbuchungskonto"], root
            )
            assert not gb._is_auto_balancing_account(
                by_name["Girokonto"], root
            )
            assert not gb._is_auto_balancing_account(
                by_name["Tagesgeld"], root
            )

    def test_structural_gate_excludes_nested_and_nonbank(self, tmp_path):
        path = tmp_path / "bal2.gnucash"
        self._make_book(path, [
            ("Aktiva", "ASSET", None),
            # Named like an imbalance account but NOT a root child.
            ("Imbalance-USD", "BANK", "Aktiva"),
            # "Orphaned Gains" is a legitimate INCOME account, not a
            # defect — the BANK gate must exclude it even though its
            # name starts with "orphan".
            ("Orphaned Gains-USD", "INCOME", None),
        ])
        gb = GnuCashBook(str(path))
        with gb.open() as b:
            root = b.root_account
            by_name = {a.name: a for a in b.accounts}
            assert not gb._is_auto_balancing_account(
                by_name["Imbalance-USD"], root
            )
            assert not gb._is_auto_balancing_account(
                by_name["Orphaned Gains-USD"], root
            )


class TestLoanTermLocaleRobust:
    """Tier B: ``debt_payoff_plan`` must not guess a loan's
    amortization term from an English "mortgage" substring. A German
    "Hypothek" now gets the 30y default term (not the old 5y), and the
    ``loan_term_months`` slot overrides it — both proven via the
    budget gate (budget < sum-of-minimums raises).
    """

    @staticmethod
    def _hypothek_book(path, term_months_slot=None):
        book = piecash.create_book(str(path), currency="EUR", overwrite=True)
        root = book.root_account
        eur = book.default_currency
        aktiva = piecash.Account(
            name="Aktiva", type="ASSET", parent=root,
            commodity=eur, placeholder=True,
        )
        piecash.Account(
            name="Girokonto", type="BANK", parent=aktiva, commodity=eur,
        )
        fremdkapital = piecash.Account(
            name="Fremdkapital", type="LIABILITY", parent=root,
            commodity=eur, placeholder=True,
        )
        hypothek = piecash.Account(
            name="Hypothek", type="LIABILITY", parent=fremdkapital,
            commodity=eur,
        )
        eigenkapital = piecash.Account(
            name="Eigenkapital", type="EQUITY", parent=root,
            commodity=eur, placeholder=True,
        )
        anfang = piecash.Account(
            name="Anfangsbestand", type="EQUITY", parent=eigenkapital,
            commodity=eur,
        )
        book.save()
        hypothek["apr"] = "4.00"   # no minimum_payment slot on purpose
        if term_months_slot is not None:
            hypothek["loan_term_months"] = term_months_slot
        book.save()
        book.session.add(piecash.Transaction(
            currency=eur, description="Hypothek aufgenommen",
            post_date=date(2025, 1, 1),
            splits=[
                piecash.Split(account=hypothek, value=Decimal("-200000")),
                piecash.Split(account=anfang, value=Decimal("200000")),
            ],
        ))
        book.save()
        book.close()

    def test_german_mortgage_gets_30y_default_not_5y(self, tmp_path):
        # €200k @ 4%: ~€955/mo over 30y, ~€3,683/mo over 5y. A €2,000
        # budget covers the 30y default but NOT the old 5y keyword
        # term — so a clean run proves the English "mortgage" guess is
        # gone (a "Hypothek" never matched it, so it used to amortize
        # over 5y and blow the budget gate).
        path = tmp_path / "hyp.gnucash"
        self._hypothek_book(path)
        gb = GnuCashBook(str(path))
        result = gb.debt_payoff_plan(monthly_budget="2000", compact=True)
        assert result  # no ValueError

    def test_loan_term_months_slot_is_honored(self, tmp_path):
        # Force a 5y term via the slot → ~€3,683/mo minimum, which
        # exceeds the €2,000 budget and trips the gate. Proves the
        # slot drives the term.
        path = tmp_path / "hyp5y.gnucash"
        self._hypothek_book(path, term_months_slot="60")
        gb = GnuCashBook(str(path))
        with pytest.raises(ValueError, match="less than the sum of minimum"):
            gb.debt_payoff_plan(monthly_budget="2000", compact=True)
