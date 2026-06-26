"""Locale-robust account resolution (i18n).

See ``specs/I18N_ACCOUNT_RESOLUTION_SPEC.md``. The governing principle:
identify accounts by ``GNCAccountType`` (a locale-invariant enum),
never by an English name. These tests run against the ``localized_book``
fixture — a German-named chart with no account literally named "Income"
or "Expenses" — so any English-name assumption fails loudly.
"""

import piecash

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
