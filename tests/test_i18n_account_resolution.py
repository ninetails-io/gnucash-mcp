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

        # Idempotent: the first create stored the account's GUID in the
        # root slot, so the second call resolves the same account via
        # Layer 0 rather than creating a duplicate.
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


class TestDesignatedAccountSlotLayer0:
    """§6.2 Layer 0: once resolved, the FX/discount account's GUID is
    stored in a root-account slot and every later resolution reads that
    GUID directly — making resolution self-healing, rename-proof, and
    locale-proof. A stale slot falls through and is rewritten.
    """

    def test_fx_designation_survives_account_rename(self, localized_book):
        # First resolve creates the account and writes its GUID to the
        # gnc-mcp/fx-gain-loss-acct slot.
        gb = GnuCashBook(str(localized_book))
        with gb.open(readonly=False) as b:
            acct, _ = gb._get_or_create_fx_account(b)
            b.save()
            original_guid = acct.guid

        # Rename the account to a word matching NO fuzzy keyword and not
        # the canonical path — only the GUID slot (Layer 0) can find it.
        gb.update_account(
            "Erträge:Foreign Exchange Gain/Loss",
            new_name="Wechselkursergebnis",
        )

        with gb.open(readonly=False) as b:
            acct2, notice = gb._get_or_create_fx_account(b)
            # Same account by GUID, despite the rename — no duplicate.
            assert acct2.guid == original_guid
            assert acct2.name == "Wechselkursergebnis"
            assert notice is None

    def test_explicit_arg_overrides_stored_designation(self, localized_book):
        # An explicit fx_account wins over the slot and is NOT persisted
        # as the new designation (it is a per-call override).
        gb = GnuCashBook(str(localized_book))
        with gb.open(readonly=False) as b:
            designated, _ = gb._get_or_create_fx_account(b)
            b.save()
            designated_guid = designated.guid
        # Create a second, distinct INCOME account to pass explicitly.
        gb.create_account(
            "Sonstige Kursdifferenzen", "INCOME",
            parent="Erträge", commodity="EUR",
        )
        with gb.open(readonly=False) as b:
            override, _ = gb._get_or_create_fx_account(
                b, fx_account="Erträge:Sonstige Kursdifferenzen"
            )
            assert override.fullname == "Erträge:Sonstige Kursdifferenzen"
            # The slot still points at the original designation.
            slotted = gb._resolve_designated_account(
                b, gb._FX_ACCOUNT_SLOT_KEY
            )
            assert slotted.guid == designated_guid

    def test_stale_slot_falls_through_and_rewrites(self, localized_book):
        gb = GnuCashBook(str(localized_book))
        # Point the slot at a well-formed GUID that resolves to nothing.
        with gb.open(readonly=False) as b:
            b.root_account[gb._FX_ACCOUNT_SLOT_KEY] = "deadbeef" * 4
            b.save()

        with gb.open(readonly=False) as b:
            # Layer 0 returns None on the dangling GUID; resolution
            # falls through and lands a real account.
            assert gb._resolve_designated_account(
                b, gb._FX_ACCOUNT_SLOT_KEY
            ) is None
            acct, _ = gb._get_or_create_fx_account(b)
            assert acct is not None
            assert acct.type == "INCOME"
            b.save()

        # The slot self-healed: Layer 0 now resolves the real account.
        with gb.open() as b:
            resolved = gb._resolve_designated_account(
                b, gb._FX_ACCOUNT_SLOT_KEY
            )
            assert resolved is not None
            assert resolved.fullname == "Erträge:Foreign Exchange Gain/Loss"

    def test_discount_sides_use_independent_slots(self, localized_book):
        # Sales (EXPENSE) and purchase (INCOME) designations are stored
        # under separate slot keys and resolve independently.
        gb = GnuCashBook(str(localized_book))
        with gb.open(readonly=False) as b:
            sales, _ = gb._get_or_create_discount_account(
                b, owner_type_is_bill=False
            )
            purchase, _ = gb._get_or_create_discount_account(
                b, owner_type_is_bill=True
            )
            b.save()
            sales_guid, purchase_guid = sales.guid, purchase.guid

        with gb.open() as b:
            via_sales = gb._resolve_designated_account(
                b, gb._SALES_DISCOUNT_SLOT_KEY
            )
            via_purchase = gb._resolve_designated_account(
                b, gb._PURCHASE_DISCOUNT_SLOT_KEY
            )
            assert via_sales.guid == sales_guid
            assert via_sales.type == "EXPENSE"
            assert via_purchase.guid == purchase_guid
            assert via_purchase.type == "INCOME"
            assert via_sales.guid != via_purchase.guid


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
    amortization term from an English "mortgage" substring, and must
    not fall back to a fabricated default term. A German "Hypothek"
    with no ``loan_term_months`` (and no ``minimum_payment``) is
    omitted from the plan with an actionable message rather than
    amortized from a guessed term; setting ``loan_term_months`` makes
    it estimable — both proven via the omit error / the budget gate.
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

    def test_german_mortgage_without_term_is_omitted_not_guessed(self, tmp_path):
        # €200k @ 4% Hypothek with no loan_term_months and no
        # minimum_payment. There is no "mortgage" account type and no
        # default term to fall back on, so its payment can't be
        # estimated — and it's the only debt, so the plan raises an
        # actionable error naming loan_term_months rather than guessing
        # a 30y (or 5y) term. Proves both: the English-keyword guess is
        # gone (a "Hypothek" never matched it) AND there is no silent
        # default standing in for the missing data.
        path = tmp_path / "hyp.gnucash"
        self._hypothek_book(path)
        gb = GnuCashBook(str(path))
        with pytest.raises(ValueError, match="loan_term_months"):
            gb.debt_payoff_plan(monthly_budget="2000", compact=True)

    def test_loan_term_months_slot_is_honored(self, tmp_path):
        # Force a 5y term via the slot → ~€3,683/mo minimum, which
        # exceeds the €2,000 budget and trips the gate. Proves the
        # slot drives the term.
        path = tmp_path / "hyp5y.gnucash"
        self._hypothek_book(path, term_months_slot="60")
        gb = GnuCashBook(str(path))
        with pytest.raises(ValueError, match="less than the sum of minimum"):
            gb.debt_payoff_plan(monthly_budget="2000", compact=True)


class TestCrossCurrencyPaymentOnLocalizedBook:
    """End-to-end acceptance test. Paying a cross-currency invoice on a
    localized (German) book used to raise ValueError inside
    _get_or_create_fx_account — there is no account named "Income", so
    the realized-FX recognition threw on the FIRST such payment. It now
    settles and books the FX under the German income root "Erträge".
    """

    @staticmethod
    def _add_usd_ar_and_price(gb, rate_date, rate_value):
        # Mirror of the EUR helper in test_business, inverted: this book
        # is EUR-default, so the foreign side is USD. Adds a USD A/R and
        # a USD→EUR price.
        with gb.open(readonly=False) as book:
            eur = book.default_currency
            usd = next(
                (c for c in book.commodities if c.mnemonic == "USD"), None
            )
            if usd is None:
                usd = piecash.factories.create_currency_from_ISO("USD")
                book.session.add(usd)
            if not any(
                a.fullname == "Aktiva:Forderungen USD"
                for a in book.accounts
            ):
                aktiva = next(
                    a for a in book.accounts if a.fullname == "Aktiva"
                )
                book.session.add(piecash.Account(
                    name="Forderungen USD", type="RECEIVABLE",
                    parent=aktiva, commodity=usd,
                ))
            book.session.add(piecash.Price(
                commodity=usd, currency=eur,
                date=date.fromisoformat(rate_date),
                value=str(rate_value), source="user:test", type="nav",
            ))
            book.save()

    def test_pay_books_fx_under_localized_income_root(self, localized_book):
        gb = GnuCashBook(str(localized_book))
        # USD 1 = 0.90 EUR at post, 0.95 EUR at pay → rate drift → FX.
        self._add_usd_ar_and_price(gb, "2026-03-10", "0.90")
        self._add_usd_ar_and_price(gb, "2026-03-20", "0.95")

        gb.create_customer(name="Acme USA", currency="USD")
        gb.create_invoice(
            customer_id="000001", currency="USD",
            date_opened="2026-03-10",
        )
        gb.add_invoice_entry(
            invoice_id="000001", account="Erträge:Umsatzerlöse",
            description="Beratung", quantity="1", price="1000.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Aktiva:Forderungen USD",
            post_date="2026-03-10",
        )

        # The line that raised ValueError before the fix.
        result = gb.pay_invoice(
            invoice_id="000001",
            payment_account="Aktiva:Girokonto",
            amount="1000.00",
            payment_date="2026-03-20",
        )

        # Realized FX booked under the German income root, by TYPE.
        assert "fx_realized" in result
        assert (
            result["fx_realized"]["account"]
            == "Erträge:Foreign Exchange Gain/Loss"
        )
        with gb.open() as b:
            fx = next(
                (a for a in b.accounts
                 if a.fullname == "Erträge:Foreign Exchange Gain/Loss"),
                None,
            )
            assert fx is not None
            assert fx.type == "INCOME"
            assert fx.commodity == b.default_currency  # EUR


class TestSKR03Chart:
    """Sabine Brenner's chart: the SKR03 Standardkontenrahmen that a
    real German Einzelunternehmerin uses. It's numbered and organized
    by Kontenklasse, not by the five fundamental types — but GnuCash's
    shipped `acctchrt_skr03` template still wraps each class in a single
    top-level account of the right TYPE. These tests use the actual
    SKR03 top-level names (verified against GnuCash `stable`) to prove
    the resolver handles the real chart, not just a tidy German one.
    """

    @staticmethod
    def _skr03_book(path):
        # Minimal slice of acctchrt_skr03: real top-level names, real
        # nesting (income leaves live two levels deep under the
        # top-level INCOME placeholder).
        book = piecash.create_book(str(path), currency="EUR", overwrite=True)
        root = book.root_account
        eur = book.default_currency
        aktiva = piecash.Account(
            name="Aktiva", type="ASSET", parent=root,
            commodity=eur, placeholder=True,
        )
        piecash.Account(
            name="1200 Bank", type="BANK", parent=aktiva, commodity=eur,
        )
        ertraege = piecash.Account(
            name="Erlöse u. Erträge 2/8", type="INCOME", parent=root,
            commodity=eur, placeholder=True,
        )
        erloeskonten = piecash.Account(
            name="Erlöskonten 8", type="INCOME", parent=ertraege,
            commodity=eur, placeholder=True,
        )
        piecash.Account(
            name="8400 Erlöse USt. 19%", type="INCOME",
            parent=erloeskonten, commodity=eur,
        )
        piecash.Account(
            name="Aufwendungen 2/4", type="EXPENSE", parent=root,
            commodity=eur, placeholder=True,
        )
        book.save()
        book.close()

    def test_top_level_income_is_the_class_placeholder(self, tmp_path):
        path = tmp_path / "skr03.gnucash"
        self._skr03_book(path)
        gb = GnuCashBook(str(path))
        with gb.open() as b:
            acct, notice = gb._top_level_account_of_type(b, "INCOME")
            # The root-child placeholder, NOT the nested "Erlöskonten 8"
            # or the leaf "8400 …".
            assert acct.fullname == "Erlöse u. Erträge 2/8"
            assert notice is None
            exp, _ = gb._top_level_account_of_type(b, "EXPENSE")
            assert exp.fullname == "Aufwendungen 2/4"

    def test_fx_account_lands_under_skr03_income_root(self, tmp_path):
        path = tmp_path / "skr03fx.gnucash"
        self._skr03_book(path)
        gb = GnuCashBook(str(path))
        with gb.open(readonly=False) as b:
            acct, _ = gb._get_or_create_fx_account(b)
            assert acct.type == "INCOME"
            assert acct.parent.fullname == "Erlöse u. Erträge 2/8"
            assert (
                acct.fullname
                == "Erlöse u. Erträge 2/8:Foreign Exchange Gain/Loss"
            )
            b.save()
