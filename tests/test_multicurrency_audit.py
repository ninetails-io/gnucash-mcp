"""Regression tests for the multicurrency-correctness audit.

Each test locks one finding from the FX-misreporting sweep:

- B1: the realized FX gain/loss account must be in the book default
  currency, so the gain (a default-currency quantity) is never recorded
  in the wrong commodity.
- B2: a posting/payment split whose account commodity AND transaction
  currency are both non-default is valued at the POSTING-DATE rate, not
  a report-time rate.
- S5: lot cost basis surfaces in the book default currency for a
  foreign-denominated purchase, not the raw transaction currency.
- S6: a foreign-currency debt with no FX rate on file is excluded from
  debt_payoff_plan (with a warning) instead of producing a mixed-unit
  schedule.

Every one is invisible in a single-currency book — they manifest only
when the book default differs from a transaction/account currency.
"""

from datetime import date
from decimal import Decimal

import piecash
import pytest
from piecash import factories

from gnucash_mcp.book import GnuCashBook


# --------------------------------------------------------------------------
# B1 — FX gain/loss account must be in the book default currency
# --------------------------------------------------------------------------

class TestFxAccountCurrencyGuard:
    def test_explicit_non_default_currency_fx_account_rejected(
        self, business_book,
    ):
        """An explicit fx_account in a non-default commodity is rejected
        — booking a default-currency gain there would record it in the
        wrong commodity (a $42 gain becoming €42)."""
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as book:
            eur = factories.create_currency_from_ISO("EUR")
            income = gb._find_account(book, "Income")
            piecash.Account(
                name="FX Gain EUR", type="INCOME",
                parent=income, commodity=eur,
            )
            book.save()
        with gb.open(readonly=True) as book:
            with pytest.raises(
                ValueError, match="book default currency",
            ):
                gb._get_or_create_fx_account(
                    book, fx_account="Income:FX Gain EUR",
                )

    def test_fuzzy_match_skips_non_default_currency_account(
        self, business_book,
    ):
        """A keyword-matching FX account in a non-default commodity is
        NOT auto-selected by the fuzzy layer; resolution falls through
        to the canonical default-currency account."""
        gb = GnuCashBook(str(business_book))
        with gb.open(readonly=False) as book:
            eur = factories.create_currency_from_ISO("EUR")
            income = gb._find_account(book, "Income")
            # "fx" keyword would match, but the EUR commodity disqualifies it.
            piecash.Account(
                name="FX Gains", type="INCOME",
                parent=income, commodity=eur,
            )
            book.save()
        with gb.open(readonly=True) as book:
            fx_acct, _notice = gb._get_or_create_fx_account(book)
            assert fx_acct.commodity == book.default_currency
            assert fx_acct.fullname != "Income:FX Gains"


# --------------------------------------------------------------------------
# B2 — both-foreign posting split valued at the posting-date rate
# --------------------------------------------------------------------------

def test_posting_split_valued_at_posting_date_rate(tmp_path):
    """When neither the account commodity nor the transaction currency
    is the book default, _posting_split_in_default converts at the
    posting-date rate — not a later/period-end rate."""
    path = tmp_path / "b2.gnucash"
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    usd = book.default_currency
    eur = factories.create_currency_from_ISO("EUR")

    assets = piecash.Account(
        name="Assets", type="ASSET", commodity=usd,
        parent=book.root_account, placeholder=True,
    )
    eur_a = piecash.Account(
        name="EUR A", type="BANK", commodity=eur, parent=assets,
    )
    eur_b = piecash.Account(
        name="EUR B", type="EXPENSE", commodity=eur,
        parent=book.root_account,
    )
    # Posting-date rate 1.10; a later rate 1.50 that must NOT be used.
    book.session.add(piecash.Price(
        commodity=eur, currency=usd,
        date=date(2025, 1, 15), value="1.10", type="last",
    ))
    book.session.add(piecash.Price(
        commodity=eur, currency=usd,
        date=date(2025, 6, 1), value="1.50", type="last",
    ))
    txn = piecash.Transaction(
        currency=eur, post_date=date(2025, 1, 15),
        description="EUR entry",
        splits=[
            piecash.Split(
                account=eur_a, value=Decimal("100"),
                quantity=Decimal("100"),
            ),
            piecash.Split(
                account=eur_b, value=Decimal("-100"),
                quantity=Decimal("-100"),
            ),
        ],
    )
    book.save()
    book.close()

    gb = GnuCashBook(str(path))
    with gb.open(readonly=True) as book:
        usd = book.default_currency
        split = next(
            s for s in book.transactions[0].splits
            if s.account.name == "EUR A"
        )
        amount, converted_ok = gb._posting_split_in_default(
            book, split, usd,
        )
        assert converted_ok
        # 100 EUR x 1.10 (posting date) = 110.00, NOT 150.00 (later rate).
        assert amount == Decimal("110.00")


# --------------------------------------------------------------------------
# S5 — lot cost basis in the book default currency
# --------------------------------------------------------------------------

def test_lot_cost_basis_converted_for_foreign_purchase(tmp_path):
    """A fund bought in EUR in a USD-default book surfaces its cost
    basis converted to USD at the purchase-date rate — not a bare EUR
    number that reads as USD."""
    path = tmp_path / "s5.gnucash"
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    usd = book.default_currency
    eur = factories.create_currency_from_ISO("EUR")

    fund = piecash.Commodity(
        namespace="FUND", mnemonic="EUFND",
        fullname="Euro Fund", fraction=10000,
    )
    book.session.add(fund)
    assets = piecash.Account(
        name="Assets", type="ASSET", commodity=usd,
        parent=book.root_account, placeholder=True,
    )
    inv = piecash.Account(
        name="EU Fund", type="MUTUAL", commodity=fund, parent=assets,
    )
    cash_eur = piecash.Account(
        name="EUR Cash", type="BANK", commodity=eur, parent=assets,
    )
    book.session.add(piecash.Price(
        commodity=eur, currency=usd,
        date=date(2025, 3, 1), value="1.20", type="last",
    ))
    buy = piecash.Transaction(
        currency=eur, post_date=date(2025, 3, 1),
        description="Buy fund",
        splits=[
            piecash.Split(
                account=inv, value=Decimal("1000"),
                quantity=Decimal("10"),
            ),
            piecash.Split(
                account=cash_eur, value=Decimal("-1000"),
                quantity=Decimal("-1000"),
            ),
        ],
    )
    lot = piecash.Lot(title="Lot 1", account=inv, is_closed=0)
    book.save()
    buy_split = next(s for s in buy.splits if s.account == inv)
    buy_split.lot = lot
    book.save()
    book.close()

    gb = GnuCashBook(str(path))
    res = gb.list_lots(account="Assets:EU Fund", compact=False)
    row = res["lots"][0]
    # 1000 EUR x 1.20 = 1200.00 USD, not a bare "1000".
    assert Decimal(row["original_cost_basis"]) == Decimal("1200.00")
    assert Decimal(row["cost_basis"]) == Decimal("1200.00")
    assert Decimal(row["cost_per_share"]) == Decimal("120.0000")


# --------------------------------------------------------------------------
# S6 — foreign debt with no FX rate excluded from debt_payoff_plan
# --------------------------------------------------------------------------

def _build_debt_book(tmp_path, *, with_usd_debt):
    """USD-default book with a EUR loan that has no EUR/USD price, and
    optionally a normal USD credit card."""
    path = tmp_path / "s6.gnucash"
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    usd = book.default_currency
    eur = factories.create_currency_from_ISO("EUR")

    liab = piecash.Account(
        name="Liabilities", type="LIABILITY", commodity=usd,
        parent=book.root_account, placeholder=True,
    )
    equity = piecash.Account(
        name="Equity", type="EQUITY", commodity=usd,
        parent=book.root_account, placeholder=True,
    )
    opening_usd = piecash.Account(
        name="Opening USD", type="EQUITY", commodity=usd, parent=equity,
    )
    opening_eur = piecash.Account(
        name="Opening EUR", type="EQUITY", commodity=eur, parent=equity,
    )
    eur_loan = piecash.Account(
        name="EUR Loan", type="LIABILITY", commodity=eur, parent=liab,
    )
    # EUR loan balance: -2000 EUR (no EUR->USD price anywhere).
    piecash.Transaction(
        currency=eur, post_date=date(2025, 1, 1), description="EUR loan",
        splits=[
            piecash.Split(account=eur_loan, value=Decimal("-2000"),
                          quantity=Decimal("-2000")),
            piecash.Split(account=opening_eur, value=Decimal("2000"),
                          quantity=Decimal("2000")),
        ],
    )
    accounts = [("Liabilities:EUR Loan", "6.5")]
    if with_usd_debt:
        usd_card = piecash.Account(
            name="Visa", type="CREDIT", commodity=usd, parent=liab,
        )
        piecash.Transaction(
            currency=usd, post_date=date(2025, 1, 1), description="Visa",
            splits=[
                piecash.Split(account=usd_card, value=Decimal("-1000"),
                              quantity=Decimal("-1000")),
                piecash.Split(account=opening_usd, value=Decimal("1000"),
                              quantity=Decimal("1000")),
            ],
        )
        accounts.append(("Liabilities:Visa", "20"))
    book.save()
    book.close()

    gb = GnuCashBook(str(path))
    for name, apr in accounts:
        gb.set_account_slot(account_name=name, key="apr", value=apr)
    return gb


def test_debt_payoff_excludes_foreign_debt_without_rate(tmp_path):
    """A foreign debt with no FX rate is excluded and surfaced as a
    warning; valuable debts still produce a schedule."""
    gb = _build_debt_book(tmp_path, with_usd_debt=True)
    result = gb.debt_payoff_plan(compact=False, monthly_budget="1000")

    assert "Liabilities:Visa" in result["payoff_order"]
    assert "Liabilities:EUR Loan" not in result["payoff_order"]
    assert result.get("excluded") == ["Liabilities:EUR Loan"]
    assert result.get("warnings")
    assert "EUR Loan" in result["warnings"][0]
    assert "no FX rate" in result["warnings"][0]


def test_debt_payoff_all_foreign_excluded_raises_clear_error(tmp_path):
    """When every debt is foreign-with-no-rate, the FX-specific error
    fires instead of the misleading 'no apr slot' message."""
    gb = _build_debt_book(tmp_path, with_usd_debt=False)
    with pytest.raises(ValueError, match="no FX rate on file"):
        gb.debt_payoff_plan(compact=False, monthly_budget="1000")


# --------------------------------------------------------------------------
# Budget no-rate fold — warn instead of folding foreign units silently
# --------------------------------------------------------------------------

def test_budget_report_warns_on_unconvertible_foreign_target(tmp_path):
    """A budget target on a foreign account with no FX rate is still
    counted (a caveated line beats a dropped one) but surfaces a warning
    naming the currency, instead of folding raw foreign units silently."""
    path = tmp_path / "budget.gnucash"
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    usd = book.default_currency
    eur = factories.create_currency_from_ISO("EUR")
    expenses = piecash.Account(
        name="Expenses", type="EXPENSE", commodity=usd,
        parent=book.root_account, placeholder=True,
    )
    piecash.Account(
        name="EU Travel", type="EXPENSE", commodity=eur, parent=expenses,
    )
    book.save()
    book.close()

    gb = GnuCashBook(str(path))
    gb.create_budget(name="2026", num_periods=12, period_type="monthly")
    gb.set_budget_amount(
        budget_name="2026", account="Expenses:EU Travel",
        amount="500", period="all",
    )
    res = gb.get_budget_report(
        budget_name="2026", period="all", compact=False,
    )
    assert res.get("warnings")
    assert "EUR" in res["warnings"][0]
    # The amount is still included (not dropped), just flagged.
    assert Decimal(res["totals"]["budgeted"]) > 0


# --------------------------------------------------------------------------
# _market_value cost-basis fallback — converts foreign purchases, no mix
# --------------------------------------------------------------------------

def test_market_value_cost_basis_converts_foreign_purchase(tmp_path):
    """When a holding has no market price, the cost-basis fallback
    values each purchase at its posting-date rate (book default), not a
    raw sum of foreign transaction-currency values."""
    path = tmp_path / "mv.gnucash"
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    usd = book.default_currency
    eur = factories.create_currency_from_ISO("EUR")
    # A security with NO price on file -> forces the cost-basis fallback.
    fund = piecash.Commodity(
        namespace="FUND", mnemonic="NOPRICE",
        fullname="Unpriced Fund", fraction=10000,
    )
    book.session.add(fund)
    assets = piecash.Account(
        name="Assets", type="ASSET", commodity=usd,
        parent=book.root_account, placeholder=True,
    )
    inv = piecash.Account(
        name="FundX", type="MUTUAL", commodity=fund, parent=assets,
    )
    cash_eur = piecash.Account(
        name="EUR Cash", type="BANK", commodity=eur, parent=assets,
    )
    # Only a EUR->USD price exists (1.20); the fund itself is unpriced.
    book.session.add(piecash.Price(
        commodity=eur, currency=usd,
        date=date(2025, 3, 1), value="1.20", type="last",
    ))
    piecash.Transaction(
        currency=eur, post_date=date(2025, 3, 1), description="Buy",
        splits=[
            piecash.Split(account=inv, value=Decimal("1000"),
                          quantity=Decimal("10")),
            piecash.Split(account=cash_eur, value=Decimal("-1000"),
                          quantity=Decimal("-1000")),
        ],
    )
    book.save()
    book.close()

    gb = GnuCashBook(str(path))
    with gb.open(readonly=True) as book:
        inv_acct = gb._find_account(book, "Assets:FundX")
        usd = book.default_currency
        rates = gb._rates_as_of(book, date(2025, 3, 1))
        value, note = gb._market_value(
            inv_acct, Decimal("10"),
            book=book, rates=rates, default_currency=usd,
            today=date(2025, 3, 1),
        )
        assert "no price data" in note
        # 1000 EUR x 1.20 = 1200 USD, not a raw 1000.
        assert value == Decimal("1200.00")
