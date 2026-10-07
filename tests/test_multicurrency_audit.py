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
from tests.conftest import drop_transaction_prices, leak_same_day_price


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
    raw sum of foreign transaction-currency values.

    The EUR buy leaves a NOPRICE/EUR implied-rate row that values the
    fund since the 2026-09-29 ruling (desktop counts it); the subject
    is the no-price fallback, so the row is deleted below."""
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
    assert drop_transaction_prices(path) == 1

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


def test_balance_sheet_excludes_closed_unpriced_position(tmp_path):
    """A fully-closed holding (net quantity zero) in a commodity that
    was never independently priced must value at zero, not the
    realized gain/loss baked into its unpriced sell leg.

    ``_split_in_default_currency``'s cost-basis fallback sums each
    split's raw ``.value`` — correct while a position is open, since
    that value approximates cost basis. But once every share is sold,
    the true remaining value is zero regardless of what the unpriced
    buy/sell legs recorded; summing them instead leaks the realized
    gain (or loss) as a phantom balance. Reproduces a real book: a
    small-cap altcoin bought for 1000 and fully sold for 1500 two
    years later, with no market price ever recorded for it.
    """
    path = tmp_path / "closed.gnucash"
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    usd = book.default_currency
    coin = piecash.Commodity(
        namespace="CRYPTO", mnemonic="ALT",
        fullname="Some Altcoin", fraction=100000000,
    )
    book.session.add(coin)
    assets = piecash.Account(
        name="Assets", type="ASSET", commodity=usd,
        parent=book.root_account, placeholder=True,
    )
    cash = piecash.Account(
        name="Checking", type="BANK", commodity=usd, parent=assets,
    )
    holding = piecash.Account(
        name="Altcoin", type="STOCK", commodity=coin, parent=assets,
    )
    piecash.Transaction(
        currency=usd, post_date=date(2021, 1, 1), description="Buy",
        splits=[
            piecash.Split(account=holding, value=Decimal("1000"),
                          quantity=Decimal("10")),
            piecash.Split(account=cash, value=Decimal("-1000"),
                          quantity=Decimal("-1000")),
        ],
    )
    piecash.Transaction(
        currency=usd, post_date=date(2022, 6, 1), description="Sell",
        splits=[
            piecash.Split(account=holding, value=Decimal("-1500"),
                          quantity=Decimal("-10")),
            piecash.Split(account=cash, value=Decimal("1500"),
                          quantity=Decimal("1500")),
        ],
    )
    book.save()
    book.close()

    gb = GnuCashBook(str(path))
    bs = gb.balance_sheet(as_of_date=date(2026, 1, 1))

    rows = {a["account"]: a for a in bs["assets"]["accounts"]}
    # The closed, unpriced position holds nothing and must not appear
    # in the report at all — not a "$0.00" line, simply absent, same
    # as any other zero-balance account.
    assert "Assets:Altcoin" not in rows
    # Total reflects only the cash account: -1000 (buy) + 1500 (sell) = 500.
    assert Decimal(bs["assets"]["total"]) == Decimal("500.00")
    # A = L + E still holds, and the unbooked 500 gain is what the
    # balancing residual carries — not a dropped account.
    assert (
        Decimal(bs["assets"]["total"]) - Decimal(bs["liabilities"]["total"])
        == Decimal(bs["equity"]["total"])
    )
    # net_worth reads the same book the same way (#185: it used to
    # keep the phantom while balance_sheet dropped it).
    nw = gb.net_worth(end_date=date(2026, 1, 1))
    assert Decimal(nw["net_worth"]) == Decimal("500")


def _unpriced_holding_book(path, legs, *, commodity_ns="CRYPTO"):
    """USD book with one never-priced holding and one checking account.

    ``legs`` is a list of ``(post_date, description, quantity, value)``
    for the holding; the cash leg mirrors ``value``. Returns the
    GnuCashBook.

    Every leg leaves an implied-rate ``type='transaction'`` row, which
    values the holding since the 2026-09-29 ruling (desktop counts
    it). "Never priced" means no price row of any type — a real state
    after desktop's Price Editor, or in older books — so they go.
    """
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    usd = book.default_currency
    coin = piecash.Commodity(
        namespace=commodity_ns, mnemonic="ALT",
        fullname="Some Altcoin", fraction=100000000,
    )
    book.session.add(coin)
    assets = piecash.Account(
        name="Assets", type="ASSET", commodity=usd,
        parent=book.root_account, placeholder=True,
    )
    cash = piecash.Account(
        name="Checking", type="BANK", commodity=usd, parent=assets,
    )
    holding = piecash.Account(
        name="Altcoin", type="STOCK", commodity=coin, parent=assets,
    )
    for post_date, desc, qty, value in legs:
        piecash.Transaction(
            currency=usd, post_date=post_date, description=desc,
            splits=[
                piecash.Split(account=holding, value=Decimal(value),
                              quantity=Decimal(qty)),
                piecash.Split(account=cash, value=-Decimal(value),
                              quantity=-Decimal(value)),
            ],
        )
    book.save()
    book.close()
    assert drop_transaction_prices(path) == len(legs)
    return GnuCashBook(str(path))


def _three_surfaces(gb, as_of):
    """(balance_sheet A - L, net_worth, dashboard net worth) as of a
    date — the numbers that must agree on any book."""
    bs = gb.balance_sheet(as_of_date=as_of)
    sheet = (
        Decimal(bs["assets"]["total"]) - Decimal(bs["liabilities"]["total"])
    )
    nw = Decimal(gb.net_worth(end_date=as_of)["net_worth"])
    with gb.open(readonly=True) as book:
        dash = gb._compute_net_worth_at(
            book, as_of, book.default_currency, list(book.accounts),
        )
    # The sheet renders cents; the other two return the raw Decimal.
    cent = Decimal("0.01")
    return sheet, nw.quantize(cent), dash.quantize(cent)


class TestUnpricedCostBasis:
    """#185: an unpriced holding is worth its remaining cost basis.

    The old fallback summed every leg's raw ``value``, which is cost
    minus proceeds: a partly sold position carried its realized gain
    (or loss) as part of the "basis", and a fully sold one showed the
    gain as a phantom holding. The rule now lives in one place
    (``_unpriced_cost_basis`` / ``_CostPool``) and every surface reads
    it, so balance_sheet, net_worth and the dashboard agree by
    construction.
    """

    AS_OF = date(2026, 1, 1)

    def test_partial_sale_values_remaining_units_at_average_cost(
        self, tmp_path,
    ):
        gb = _unpriced_holding_book(tmp_path / "partial.gnucash", [
            (date(2021, 1, 1), "Buy", "10", "1000"),
            (date(2022, 6, 1), "Sell", "-9", "-1350"),
        ])
        bs = gb.balance_sheet(as_of_date=self.AS_OF)
        rows = {a["account"]: a for a in bs["assets"]["accounts"]}
        # One unit left, bought at 100 — not 1000 - 1350 = -350.
        assert Decimal(
            rows["Assets:Altcoin"]["default_currency_value"]
        ) == Decimal("100.00")
        assert "no price data" in rows["Assets:Altcoin"]["balance"]
        # Cash: -1000 + 1350 = 350; plus the unit at cost = 450.
        assert Decimal(bs["assets"]["total"]) == Decimal("450.00")
        sheet, nw, dash = _three_surfaces(gb, self.AS_OF)
        assert sheet == nw == dash == Decimal("450")

    def test_repurchase_after_full_sale_starts_a_new_basis(
        self, tmp_path,
    ):
        """Chronological: the pool relieves to zero on the sale and
        the later buy opens a fresh basis — not the average of every
        buy ever made."""
        gb = _unpriced_holding_book(tmp_path / "rebuy.gnucash", [
            (date(2021, 1, 1), "Buy", "10", "1000"),
            (date(2021, 6, 1), "Sell", "-10", "-1500"),
            (date(2022, 1, 1), "Buy again", "10", "2000"),
        ])
        bs = gb.balance_sheet(as_of_date=self.AS_OF)
        rows = {a["account"]: a for a in bs["assets"]["accounts"]}
        assert Decimal(
            rows["Assets:Altcoin"]["default_currency_value"]
        ) == Decimal("2000.00")
        sheet, nw, dash = _three_surfaces(gb, self.AS_OF)
        # Cash: -1000 + 1500 - 2000 = -1500; holding 2000 → 500.
        assert sheet == nw == dash == Decimal("500")

    def test_dust_remainder_is_not_a_knife_edge(self, tmp_path):
        """One satoshi left behind used to keep the whole realized
        gain on the sheet as a negative asset; now it is one satoshi
        at cost."""
        gb = _unpriced_holding_book(tmp_path / "dust.gnucash", [
            (date(2021, 1, 1), "Buy", "10", "1000"),
            (date(2022, 6, 1), "Sell", "-9.99999999", "-1499.99"),
        ])
        bs = gb.balance_sheet(as_of_date=self.AS_OF)
        rows = {a["account"]: a for a in bs["assets"]["accounts"]}
        remaining = Decimal(rows["Assets:Altcoin"]["default_currency_value"])
        assert Decimal("0") <= remaining < Decimal("0.01")
        sheet, nw, dash = _three_surfaces(gb, self.AS_OF)
        assert sheet == nw == dash

    def test_voided_sale_does_not_relieve_the_basis(self, tmp_path):
        gb = _unpriced_holding_book(tmp_path / "void.gnucash", [
            (date(2021, 1, 1), "Buy", "10", "1000"),
            (date(2022, 6, 1), "Sell", "-10", "-1500"),
        ])
        with gb.open(readonly=False) as book:
            sale = [t for t in book.transactions
                    if t.description == "Sell"][0]
            for s in sale.splits:
                s.reconcile_state = "v"
                s.value = Decimal("0")
                s.quantity = Decimal("0")
            book.save()
        bs = gb.balance_sheet(as_of_date=self.AS_OF)
        rows = {a["account"]: a for a in bs["assets"]["accounts"]}
        assert Decimal(
            rows["Assets:Altcoin"]["default_currency_value"]
        ) == Decimal("1000.00")

    def test_series_boundaries_match_point_in_time(self, tmp_path):
        """The trajectory's incremental pool reads the same basis at
        each boundary as the one-shot valuation."""
        gb = _unpriced_holding_book(tmp_path / "series.gnucash", [
            (date(2021, 3, 1), "Buy", "10", "1000"),
            (date(2022, 6, 1), "Sell", "-4", "-600"),
            (date(2023, 9, 1), "Buy", "2", "300"),
            (date(2024, 2, 1), "Sell", "-8", "-1200"),
        ])
        series = gb.net_worth(
            end_date=self.AS_OF, start_date=date(2021, 1, 1),
            interval="year",
        )["series"]
        assert len(series) == 6
        for point in series:
            boundary = date.fromisoformat(point["date"])
            one_shot = gb.net_worth(end_date=boundary)["net_worth"]
            assert Decimal(point["net_worth"]) == Decimal(one_shot), (
                point["date"]
            )
        # After the last sale nothing remains: cash only.
        assert Decimal(series[-1]["net_worth"]) == Decimal("500")

    def test_foreign_liability_paid_off_leaves_the_sheet(self, tmp_path):
        """Sign-agnostic: a EUR card with no EUR rate on file, charged
        then paid in full, is a zero liability — and the FX difference
        the user never booked is the residual, on every surface.
        (Both legs leave EUR/USD implied-rate rows, which count as
        rates since the 2026-09-29 ruling; they are deleted so the
        card stays rate-less, as the subject needs.)"""
        path = tmp_path / "card.gnucash"
        book = piecash.create_book(str(path), currency="USD", overwrite=True)
        usd = book.default_currency
        eur = factories.create_currency_from_ISO("EUR")
        assets = piecash.Account(
            name="Assets", type="ASSET", commodity=usd,
            parent=book.root_account, placeholder=True,
        )
        liab = piecash.Account(
            name="Liabilities", type="LIABILITY", commodity=usd,
            parent=book.root_account, placeholder=True,
        )
        cash = piecash.Account(
            name="Checking", type="BANK", commodity=usd, parent=assets,
        )
        card = piecash.Account(
            name="EuroCard", type="CREDIT", commodity=eur, parent=liab,
        )
        exp = piecash.Account(
            name="Expenses", type="EXPENSE", commodity=usd,
            parent=book.root_account,
        )
        piecash.Transaction(
            currency=usd, post_date=date(2021, 1, 1), description="Charge",
            splits=[
                piecash.Split(account=card, value=Decimal("-1100"),
                              quantity=Decimal("-1000")),
                piecash.Split(account=exp, value=Decimal("1100"),
                              quantity=Decimal("1100")),
            ],
        )
        piecash.Transaction(
            currency=usd, post_date=date(2021, 2, 1), description="Payoff",
            splits=[
                piecash.Split(account=card, value=Decimal("1050"),
                              quantity=Decimal("1000")),
                piecash.Split(account=cash, value=Decimal("-1050"),
                              quantity=Decimal("-1050")),
            ],
        )
        book.save()
        book.close()
        assert drop_transaction_prices(path) == 2
        gb = GnuCashBook(str(path))
        bs = gb.balance_sheet(as_of_date=self.AS_OF)
        assert bs["liabilities"]["accounts"] == []
        sheet, nw, dash = _three_surfaces(gb, self.AS_OF)
        assert sheet == nw == dash == Decimal("-1050")


class TestSameDatePriceTieBreak:
    """Two prices on the same commodity/currency/day resolve the way
    GnuCash's ``compare_prices_by_date`` resolves them: the later
    stored time, then the smaller GUID. Bookkeeper finding F3 first
    made the tie deliberate (a source rank); the price twin of
    2026-09-29 showed desktop valuing a holding by a
    wall-clock-stamped zero row the server ranked below the day's
    neutral-time quote, and the maintainer ruled that parity means
    agreeing on the price. Every surface — posting FX, as-of
    valuation, the latest quote — reads one rule."""

    @staticmethod
    def _guids(gc, *, source_by_value):
        from sqlalchemy import text
        with gc.open(readonly=True) as book:
            rows = book.session.execute(text(
                "SELECT guid, value_num, value_denom FROM prices"
            )).fetchall()
        return {
            Decimal(n) / Decimal(d): g for g, n, d in rows
        }

    def test_equal_times_go_to_the_smaller_guid(
        self, multi_currency_book,
    ):
        from datetime import date
        gc = GnuCashBook(str(multi_currency_book))
        d = date(2026, 3, 31)
        gc.create_price("EUR", "CURRENCY", "1.30", price_date=d,
                        source="Finance::Quote")
        # A second row for the day, as desktop's SQL backend leaves
        # one and older servers wrote one (see leak_same_day_price).
        leak_same_day_price(multi_currency_book, "1.10", "user:price")
        by_value = {
            v: g for v, g in self._guids(gc, source_by_value=None).items()
            if v in (Decimal("1.30"), Decimal("1.10"))
        }
        winner = min(by_value, key=lambda v: by_value[v])
        with gc.open(readonly=True) as book:
            eur = book.commodities(mnemonic="EUR")
            usd = book.default_currency
            assert gc._find_exchange_rate(book, eur, usd, d) == winner
            assert gc._rates_as_of(book, d)[eur.guid] == winner
            assert Decimal(str(gc._find_prices(
                book, commodity_guid=eur.guid, currency_guid=usd.guid,
            )[0].value)) == winner

    def test_later_stored_time_beats_the_smaller_guid(
        self, multi_currency_book,
    ):
        """The twin's shape: desktop's editor left a row stamped at
        wall-clock time, later than the day's neutral-time quote.
        Desktop uses it; so does the server, whatever its GUID."""
        from datetime import date
        from sqlalchemy import text
        gc = GnuCashBook(str(multi_currency_book))
        d = date(2026, 3, 31)
        gc.create_price("EUR", "CURRENCY", "1.30", price_date=d,
                        source="Finance::Quote")
        leak_same_day_price(multi_currency_book, "1.10", "user:price-editor")
        with gc.open(readonly=False) as book:
            book.session.execute(text(
                "UPDATE prices SET date = '2026-03-31 20:44:14' "
                "WHERE source = 'user:price-editor'"
            ))
            book.save()
        with gc.open(readonly=True) as book:
            eur = book.commodities(mnemonic="EUR")
            usd = book.default_currency
            assert gc._find_exchange_rate(book, eur, usd, d) == Decimal("1.10")
            assert gc._rates_as_of(book, d)[eur.guid] == Decimal("1.10")

    @staticmethod
    def _day_rows(path, day="2026-03-31"):
        import sqlite3
        con = sqlite3.connect(str(path))
        try:
            return sorted(
                (source, Decimal(n) / Decimal(d))
                for source, n, d in con.execute(
                    "select source, value_num, value_denom from prices "
                    "where date like ?", (day + "%",),
                )
            )
        finally:
            con.close()

    def test_one_price_per_pair_per_day_by_source_rank(
        self, multi_currency_book,
    ):
        """``gnc_pricedb_add_price``: a price whose source ranks equal
        or better takes the day; one that ranks worse is turned away.
        The server wrote one row per source, and which won was the
        GUID draw (adversarial review 2026-09-30, C24; bookkeeper
        ruling the same night)."""
        from datetime import date
        gc = GnuCashBook(str(multi_currency_book))
        d = date(2026, 3, 31)
        first = gc.create_price(
            "EUR", "CURRENCY", "1.10", price_date=d, source="user:price",
        )
        assert (first["status"], "note" in first) == ("created", False)

        # A feed quote outranks a typed ``user:price``: it takes the day.
        second = gc.create_price(
            "EUR", "CURRENCY", "1.30", price_date=d, source="Finance::Quote",
        )
        assert second["status"] == "replaced"
        assert second["replaced"]["source"] == "user:price"
        assert self._day_rows(multi_currency_book) == [
            ("Finance::Quote", Decimal("1.30")),
        ]

        # The typed price again: turned away, nothing written, and
        # the caller is told why and how to override.
        third = gc.create_price(
            "EUR", "CURRENCY", "1.15", price_date=d, source="user:price",
        )
        assert third["status"] == "kept"
        assert third["existing"]["source"] == "Finance::Quote"
        assert "user:price-editor" in third["note"]
        assert self._day_rows(multi_currency_book) == [
            ("Finance::Quote", Decimal("1.30")),
        ]

        # The Price Editor's rank beats the feed.
        fourth = gc.create_price(
            "EUR", "CURRENCY", "1.15", price_date=d,
            source="user:price-editor",
        )
        assert fourth["status"] == "replaced"
        assert self._day_rows(multi_currency_book) == [
            ("user:price-editor", Decimal("1.15")),
        ]
        # The same source again updates in place.
        fifth = gc.create_price(
            "EUR", "CURRENCY", "1.16", price_date=d,
            source="user:price-editor",
        )
        assert fifth["status"] == "updated"
        assert self._day_rows(multi_currency_book) == [
            ("user:price-editor", Decimal("1.16")),
        ]

    def test_a_quote_the_other_way_round_replaces_the_days_price(
        self, multi_currency_book,
    ):
        from datetime import date
        gc = GnuCashBook(str(multi_currency_book))
        d = date(2026, 3, 31)
        gc.create_price("EUR", "CURRENCY", "1.25", price_date=d,
                        source="user:price-editor")
        result = gc.create_price(
            "USD", "CURRENCY", "0.80", currency="EUR", price_date=d,
            source="user:price-editor",
        )
        assert result["status"] == "replaced"
        assert result["replaced"]["quoted"] == "opposite direction"
        assert self._day_rows(multi_currency_book) == [
            ("user:price-editor", Decimal("0.80")),
        ]

    def test_create_price_notes_when_outranked(
        self, multi_currency_book,
    ):
        """A book can still hold several rows for a day (desktop's
        SQL backend leaves the ones its price database turned away).
        When a written row is not the one desktop will use — the
        later stored time, then the smaller GUID — the write says
        so."""
        from datetime import date
        from sqlalchemy import text
        gc = GnuCashBook(str(multi_currency_book))
        d = date(2026, 3, 31)
        gc.create_price(
            "EUR", "CURRENCY", "1.10", price_date=d, source="user:price",
        )
        leaked = leak_same_day_price(
            multi_currency_book, "1.30", "user:split-register",
        )
        # Stamp the leaked row later in the day, so it is the one
        # desktop reads whatever the GUIDs are.
        with gc.open(readonly=False) as book:
            book.session.execute(
                text("UPDATE prices SET date = '2026-03-31 20:44:14' "
                     "WHERE guid = :g"), {"g": leaked},
            )
            book.save()
        again = gc.create_price(
            "EUR", "CURRENCY", "1.12", price_date=d, source="user:price",
        )
        if again["status"] != "kept":
            assert "outranks it as the effective rate" in again["note"]
            assert "user:split-register" in again["note"]

    def test_create_prices_batch_notes_outranked_in_reason(
        self, multi_currency_book,
    ):
        """Batch entry reports the same tie loss in the reason
        column — single and batch can't diverge (chokepoint)."""
        from datetime import date
        gc = GnuCashBook(str(multi_currency_book))
        d = date(2026, 3, 31)
        gc.create_price("EUR", "CURRENCY", "1.30", price_date=d,
                        source="Finance::Quote")
        rows = [{
            "ref": "1", "commodity": "EUR", "date": d,
            "value": "1.10", "source": "user:price",
        }]
        dry = gc.create_prices(rows, dry_run=True)["results"].splitlines()[1]
        assert "would_keep" in dry and "outranks 'user:price'" in dry
        live = gc.create_prices(rows)["results"].splitlines()[1]
        assert "\tkept\t" in live and "outranks 'user:price'" in live
        assert self._day_rows(multi_currency_book) == [
            ("Finance::Quote", Decimal("1.30")),
        ]
        better = [{
            "ref": "2", "commodity": "EUR", "date": d,
            "value": "1.20", "source": "user:price-editor",
        }]
        assert "would_replace" in gc.create_prices(
            better, dry_run=True,
        )["results"]
        assert "\treplaced\t" in gc.create_prices(better)["results"]


class TestPriceLookupMemo:
    """_find_prices memoizes each (commodity_guid, currency_guid)
    lookup on the open book: the first request queries, repeats are
    dict hits. The memoized result must match a fresh query exactly,
    including the same-date tie-break (bookkeeper finding F3, see
    TestSameDatePriceTieBreak above) — a memo sorted on date alone
    would put same-date ties back to depending on row order, which
    is the bug F3 fixed."""

    def test_repeat_lookups_agree_and_are_memoized(
        self, multi_currency_book,
    ):
        gc = GnuCashBook(str(multi_currency_book))
        d = date(2026, 3, 31)
        gc.create_price("EUR", "CURRENCY", "1.30", price_date=d,
                        source="Finance::Quote")
        leak_same_day_price(multi_currency_book, "1.10", "user:price")

        with gc.open(readonly=True) as book:
            eur = book.commodities(mnemonic="EUR")
            usd = book.default_currency

            first = gc._find_prices(book, commodity_guid=eur.guid,
                                    currency_guid=usd.guid)
            # The pair is memoized now; the repeat is a dict hit.
            memo = getattr(book, gc._PRICE_LOOKUPS_ATTR)
            assert (eur.guid, usd.guid) in memo
            repeat = gc._find_prices(book, commodity_guid=eur.guid,
                                     currency_guid=usd.guid)

            assert [p.guid for p in first] == [p.guid for p in repeat], (
                "memoized lookups must return the same prices "
                "in the same order as the first query"
            )
            # The smaller GUID wins the same-time tie on both. (The
            # fixture's 2024 transfer row rides along, older — a
            # price since the 2026-09-29 ruling — so the tie is the
            # two quotes on ``d``.)
            from gnucash_mcp.book._currency import _to_date

            tied = [p for p in first if _to_date(p.date) == d]
            assert len(tied) == 2 and len(first) == 3
            assert first[0].guid == min(p.guid for p in tied)
            assert repeat[0].guid == first[0].guid

    def test_price_written_mid_call_is_visible_after_invalidation(
        self, multi_currency_book,
    ):
        gc = GnuCashBook(str(multi_currency_book))
        d = date(2026, 4, 30)

        with gc.open(readonly=False) as book:
            eur = book.commodities(mnemonic="EUR")
            usd = book.default_currency

            # Memoize the pair, so a later read would be served
            # from the memo.
            before = gc._find_prices(book, commodity_guid=eur.guid,
                                     currency_guid=usd.guid)

            book.session.add(piecash.Price(
                commodity=eur, currency=usd, date=d,
                value=Decimal("1.42"), source="user:price",
            ))
            book.save()
            gc._invalidate_price_caches(book)

            after = gc._find_prices(book, commodity_guid=eur.guid,
                                    currency_guid=usd.guid)
            assert len(after) == len(before) + 1
            assert after[0].value == Decimal("1.42"), (
                "a price added mid-call must be visible once the "
                "caches are invalidated"
            )

    def test_every_price_write_path_invalidates_the_memo(
        self, multi_currency_book, monkeypatch,
    ):
        """The cache-safety argument rests on one invariant: every
        path that adds or removes a Price row invalidates the memo
        after its save. create_price and delete_price were the only
        write paths when the memo landed; bulk create_prices shipped
        separately and silently fell outside the claim. This test
        turns the invariant into a contract over all three."""
        gc = GnuCashBook(str(multi_currency_book))
        cls = type(gc)
        calls: list[bool] = []
        real = cls._invalidate_price_caches

        def spy(book):
            calls.append(True)
            real(book)

        monkeypatch.setattr(
            cls, "_invalidate_price_caches", staticmethod(spy),
        )

        gc.create_price("EUR", "CURRENCY", "1.31",
                        price_date=date(2026, 5, 1),
                        source="user:price")
        assert calls, "create_price must invalidate after its save"

        calls.clear()
        gc.create_prices([{
            "ref": "r1", "commodity": "EUR",
            "namespace": "CURRENCY", "date": date(2026, 5, 2),
            "value": "1.32",
        }])
        assert calls, (
            "bulk create_prices must invalidate after its save"
        )

        calls.clear()
        gc.delete_price("EUR", "CURRENCY",
                        price_date=date(2026, 5, 2),
                        source="user:price")
        assert calls, "delete_price must invalidate after its save"


# --------------------------------------------------------------------------
# MM-12 — a budget report is a flow report: monthly-close valuation
# --------------------------------------------------------------------------

def test_budget_report_actuals_agree_with_spending_by_category(tmp_path):
    """Actuals convert at each split's month close, as the spending
    report does; one period-end rate made the two disagree on the same
    data (the review's 260.0 against 240.0)."""
    path = tmp_path / "budget_fx.gnucash"
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    usd = book.default_currency
    eur = factories.create_currency_from_ISO("EUR")
    assets = piecash.Account(
        name="Assets", type="ASSET", commodity=usd,
        parent=book.root_account, placeholder=True,
    )
    cash = piecash.Account(
        name="EUR Cash", type="CASH", commodity=eur, parent=assets,
    )
    expenses = piecash.Account(
        name="Expenses", type="EXPENSE", commodity=usd,
        parent=book.root_account, placeholder=True,
    )
    travel = piecash.Account(
        name="EU Travel", type="EXPENSE", commodity=eur, parent=expenses,
    )
    for d in (date(2026, 1, 10), date(2026, 2, 10)):
        piecash.Transaction(
            currency=eur, description="Trip", post_date=d,
            splits=[
                piecash.Split(account=travel, value=Decimal("100")),
                piecash.Split(account=cash, value=Decimal("-100")),
            ],
        )
    book.save()
    book.close()

    gb = GnuCashBook(str(path))
    for d, rate in (
        (date(2026, 1, 15), "1.0"),
        (date(2026, 2, 15), "1.4"),
        (date(2026, 3, 15), "2.0"),
    ):
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value=rate,
            currency="USD", price_date=d,
        )
    gb.create_budget(
        name="2026", num_periods=12, period_type="monthly",
        start_date="2026-01-01",
    )
    gb.set_budget_amount(
        budget_name="2026", account="Expenses:EU Travel",
        amount="100", period="all",
    )

    res = gb.get_budget_report(budget_name="2026", period="all", compact=False)
    spend = gb.spending_by_category(
        start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        depth=2, compact=False,
    )
    row = next(a for a in res["accounts"] if a["account"] == "Expenses:EU Travel")
    assert Decimal(row["actual"]) == Decimal("240")
    assert Decimal(row["actual"]) == Decimal(spend["total"])
    # Targets: each month's 100 EUR at that month's close (Mar's 2.0
    # carries through December).
    assert Decimal(row["budgeted"]) == Decimal("2240")
