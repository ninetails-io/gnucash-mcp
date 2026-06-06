"""Build the 林微 (Lín Wēi) CNY-default synthetic GnuCash book FROM ZERO.

Unlike Alex's phase scripts (which mutate an existing book), this single
script creates Lin Wei's entire book from nothing: the SQLite file, the
commodities, the full chart of accounts, opening balances and investment
opening lots, scheduled-transaction templates, recurring instantiations,
daily/weekly spending, contractor income + the business module, investment
activity, credit-card lifecycles, a budget, reconciliation, edge cases, and
a volume stress phase.

It implements ``specs/SYNTHETIC_BOOK_SPEC_CNY.md`` — a non-USD-default book
that stress-tests every currency, FX, and formatting path in the server. The
two mandatory FX regression cases (the HSBC HKD credit card and the JetBrains
US$249 vendor bill) are built so both surface in CNY on every report.

SAFETY: this script writes ONLY to ``samples/lin-wei.generated.gnucash`` (the
``--out`` path). It NEVER touches the bookkeeper-validated
``samples/lin-wei.gnucash``.

Usage:
    uv run python scripts/synthetic_book/build_lin_wei.py
    uv run python scripts/synthetic_book/build_lin_wei.py --out /tmp/lw.gnucash
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import piecash

from gnucash_mcp.book import GnuCashBook

# The shared market-data module is a sibling file; make it importable
# whether this script is launched as a module or by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from market_data import MarketData  # noqa: E402


# ── Configuration ───────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "samples" / "lin-wei.generated.gnucash"
PROTECTED = REPO_ROOT / "samples" / "lin-wei.gnucash"

SEED = 20250101
VOLUME_TXN_COUNT = 320  # Phase 13 small WeChat/Alipay transactions

YEAR = 2025
# Hardcoded fixed end of the price/report timeline. NEVER date.today() —
# the output must be byte-for-byte deterministic across runs. Matches the
# committed market-data cache range (see market_data.END).
END = date(2026, 6, 30)

D = Decimal

# Shared offline market-data accessor (real historical quotes, committed
# cache, no network). Loaded once at import; used everywhere a real price
# or FX rate is needed (monthly series, transaction-date prices, the
# actual booked amounts in cross-currency and investment transactions).
MD = MarketData.load()


def md_security(mnemonic: str, when: date) -> Decimal:
    """Native-currency (CNY) close for a Lin Wei security on ``when``."""
    return MD.security(mnemonic, when)


def md_fx_cny(foreign: str, when: date) -> Decimal:
    """CNY per one unit of ``foreign`` on ``when`` (GnuCash Price convention)."""
    return MD.fx(foreign, "CNY", when)


# ── Account path constants ──────────────────────────────────────

CHECKING = "Assets:Current Assets:Checking Account"
SAVINGS = "Assets:Current Assets:Savings Account"
CASH = "Assets:Current Assets:Cash"
WECHAT = "Assets:Current Assets:WeChat Pay"
ALIPAY = "Assets:Current Assets:Alipay"
AR_CNY = "Assets:Receivables:Accounts Receivable"
AR_USD = "Assets:Receivables:Accounts Receivable USD"
AR_EUR = "Assets:Receivables:Accounts Receivable EUR"
HOUSING_FUND = "Assets:Investments:Housing Fund"
APARTMENT = "Assets:Fixed Assets:Apartment"
VEHICLE = "Assets:Fixed Assets:Vehicle"

MOUTAI = "Assets:Investments:Brokerage:Moutai"
CATL = "Assets:Investments:Brokerage:CATL"
CSI300 = "Assets:Investments:Brokerage:CSI300"
CHINEXT = "Assets:Investments:Brokerage:ChiNext"

ICBC_CARD = "Liabilities:Credit Card:ICBC Credit Card"
CMB_CARD = "Liabilities:Credit Card:CMB Credit Card"
HSBC_CARD = "Liabilities:Credit Card:HSBC HKD Card"
MORTGAGE = "Liabilities:Loans:Mortgage"
AUTO_LOAN = "Liabilities:Loans:Auto Loan"
AP = "Liabilities:Accounts Payable"

OPENING = "Equity:Opening Balances"

SALARY = "Income:Salary"
CONTRACTOR = "Income:Contractor Income"
LLC_REVENUE = "Income:LLC Revenue"
DIVIDENDS = "Income:Investment Income:Dividends"
CAPITAL_GAINS = "Income:Investment Income:Capital Gains"
HOUSING_FUND_INCOME = "Income:Housing Fund Income"
REIMBURSEMENTS = "Income:Reimbursements"

EXP_PROP_MGMT = "Expenses:Housing:Property Management"
EXP_MORTGAGE_INT = "Expenses:Interest:Mortgage Interest"
EXP_AUTO_INT = "Expenses:Interest:Auto Loan Interest"
EXP_CC_INT = "Expenses:Interest:Credit Card Interest"
EXP_CHARGING = "Expenses:Auto:Charging"
EXP_AUTO_INS = "Expenses:Auto:Insurance"
EXP_PARKING = "Expenses:Auto:Parking"
EXP_GROCERIES = "Expenses:Groceries"
EXP_DINING = "Expenses:Dining"
EXP_ELECTRIC = "Expenses:Utilities:Electric"
EXP_WATER = "Expenses:Utilities:Water"
EXP_GAS = "Expenses:Utilities:Gas"
EXP_INTERNET = "Expenses:Utilities:Internet"
EXP_PHONE = "Expenses:Utilities:Phone"
EXP_INCOME_TAX = "Expenses:Taxes:Income Tax"
EXP_SOCIAL = "Expenses:Taxes:Social Insurance"
EXP_BUSINESS_TAX = "Expenses:Taxes:Business Tax"
EXP_STREAMING = "Expenses:Streaming"
EXP_CLOTHING = "Expenses:Clothing"
EXP_PET_FOOD = "Expenses:Pet:Food"
EXP_PET_VET = "Expenses:Pet:Vet"
EXP_TRAVEL = "Expenses:Travel"
EXP_GIFTS = "Expenses:Gifts"
EXP_CHARITY = "Expenses:Charity"
EXP_CLOUD = "Expenses:Business:Cloud Hosting"
EXP_SOFTWARE = "Expenses:Business:Software"
EXP_COWORKING = "Expenses:Business:Coworking"
EXP_MISC = "Expenses:Miscellaneous"
EXP_MEDICAL = "Expenses:Medical"


# ── Phase 1: Commodities & prices ───────────────────────────────

# Securities: (mnemonic, fullname, namespace, fraction)
SECURITIES = [
    ("600519", "贵州茅台 (Kweichow Moutai)", "SSE", 100),
    ("300750", "宁德时代 (CATL)", "SZSE", 100),
    ("510300", "华泰柏瑞沪深300ETF (CSI 300 ETF)", "SSE", 10000),
    ("159915", "易方达创业板ETF (ChiNext ETF)", "SZSE", 10000),
]

FOREIGN_CURRENCIES = ["USD", "EUR", "HKD"]

SECURITY_MNEMONICS = [s[0] for s in SECURITIES]

# Discretionary quarterly trades, shared by the price layer (so a real
# quote sits on each trade date) and the investment generator (so the
# booked CNY amount uses that same real price). Tuple:
# (month, day, action, mnemonic, shares). The per-share/unit price is
# looked up from the market cache at the trade date — no made-up numbers.
INVESTMENT_TRADES = [
    (3, 10, "buy", "600519", D("2")),
    (5, 15, "buy", "300750", D("20")),
    (7, 20, "sell", "600519", D("1")),
    (9, 12, "sell", "300750", D("15")),
    (11, 18, "buy", "510300", D("3000")),
    (12, 15, "sell", "159915", D("2000")),
]

# Cross-currency transaction dates that need a fresh FX quote on file:
# every customer-invoice and vendor-bill post & pay date whose currency
# differs from CNY, plus the HKD credit-card charge / payment dates.
# Tuple: (foreign_currency, date). Built once at import; consumed by the
# price layer (add_prices) and asserted against the generators that
# create the matching transactions. Keeping these as the single source of
# truth keeps prices and transactions on identical dates.
CROSS_CCY_FX_DATES: list[tuple[str, date]] = []

# Pacific Trade: USD invoices opened on the 5th, paid the 5th of the
# following month (Dec rolls into Jan 2026). Matches PACIFIC_PLAN below.
PACIFIC_PLAN = [(3, "3000"), (6, "3000"), (9, "4500"), (12, "3000")]
for _m, _amt in PACIFIC_PLAN:
    CROSS_CCY_FX_DATES.append(("USD", date(YEAR, _m, 5)))
    _pm = _m + 1 if _m < 12 else 1
    _py = YEAR if _m < 12 else YEAR + 1
    CROSS_CCY_FX_DATES.append(("USD", date(_py, _pm, 5)))

# Munich: EUR invoices opened the 8th, paid the 8th of the next month.
MUNICH_PLAN = [(4, "2500"), (8, "3800"), (11, "2500")]
for _m, _amt in MUNICH_PLAN:
    CROSS_CCY_FX_DATES.append(("EUR", date(YEAR, _m, 8)))
    CROSS_CCY_FX_DATES.append(("EUR", date(YEAR, _m + 1, 8)))

# JetBrains: USD bill posted 2025-01-12, left outstanding (no pay date).
JETBRAINS_POST = date(YEAR, 1, 12)
CROSS_CCY_FX_DATES.append(("USD", JETBRAINS_POST))

# HSBC HKD card charge + payment dates (cross-currency splits booked at
# the real HKD/CNY rate). Matches HSBC_CHARGES / HSBC_PAYMENT below.
HSBC_CHARGES = [
    (date(YEAR, 3, 14), "香港 海港城购物", D("3200")),
    (date(YEAR, 7, 8), "香港 莎莎化妆品", D("1800")),
    (date(YEAR, 10, 20), "香港 苹果旗舰店配件", D("2460")),
]
HSBC_PAYMENT = (date(YEAR, 11, 5), D("1000"))
for _dt, _desc, _amt in HSBC_CHARGES:
    CROSS_CCY_FX_DATES.append(("HKD", _dt))
CROSS_CCY_FX_DATES.append(("HKD", HSBC_PAYMENT[0]))

# Per-symbol display quantization for the *booked* CNY value of a price
# record. FX to four decimals (GnuCash convention), per-share securities
# to one, ETF/fund units to two.
PRICE_QUANT = {
    "USD": D("0.0001"), "EUR": D("0.0001"), "HKD": D("0.0001"),
    "600519": D("0.1"), "300750": D("0.1"),
    "510300": D("0.01"), "159915": D("0.01"),
}

# Months on which to lay down a monthly price snapshot: the 1st of each
# month across the fixed timeline 2025-01 .. END (2026-06), inclusive.
PRICE_MONTHS: list[date] = []
_y, _m = 2025, 1
while date(_y, _m, 1) <= END:
    PRICE_MONTHS.append(date(_y, _m, 1))
    _m += 1
    if _m > 12:
        _y += 1
        _m = 1


def real_price(symbol: str, when: date) -> Decimal:
    """Real CNY-base price for ``symbol`` on ``when`` from the market cache.

    ``symbol`` is either a security mnemonic (priced in CNY natively) or a
    foreign-currency code (CNY-per-foreign FX rate). Quantized per symbol.
    """
    if symbol in PRICE_QUANT and symbol not in FOREIGN_CURRENCIES:
        raw = md_security(symbol, when)
    else:
        raw = md_fx_cny(symbol, when)
    return raw.quantize(PRICE_QUANT[symbol])


def create_book_file(out_path: Path) -> None:
    """Create the SQLite book with CNY default + all commodities."""
    if out_path.resolve() == PROTECTED.resolve():
        raise SystemExit(
            "REFUSING to write to the protected book: "
            f"{PROTECTED}. Choose a different --out path."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    book = piecash.create_book(
        sqlite_file=str(out_path),
        currency="CNY",
        overwrite=True,
    )
    try:
        # Foreign currencies. ``book.currencies(mnemonic=...)`` has a
        # built-in fallback that auto-creates the ISO 4217 currency and
        # registers it with the book.
        for code in FOREIGN_CURRENCIES:
            book.currencies(mnemonic=code)
        # Securities.
        for mnemonic, fullname, namespace, fraction in SECURITIES:
            piecash.Commodity(
                namespace=namespace,
                mnemonic=mnemonic,
                fullname=fullname,
                fraction=fraction,
                book=book,
            )
        book.save()
    finally:
        book.close()


def add_prices(out_path: Path) -> int:
    """Add real CNY-base prices for all foreign currencies + securities.

    Two layers, both sourced from the committed market-data cache:

    1. A monthly snapshot (1st of each month) across 2025-01 .. END for
       every security and FX pair — the baseline series the reports walk.
    2. A price on every transaction date that needs a rate: investment
       buy/sell + DCA dates (securities) and cross-currency invoice/bill
       post & pay dates (FX). Laying a 0-day-old quote on each FX
       transaction date means post_invoice/pay_invoice find a *fresh*
       rate and never trip the StaleFXRateError freshness guard.
    """
    book = piecash.open_book(str(out_path), readonly=False)
    count = 0
    try:
        cny = book.default_currency
        comm_by_mnemonic = {c.mnemonic: c for c in book.commodities}

        # Collect (symbol -> set of dates) needing a price. Start with the
        # monthly snapshots for every symbol, then add the exact
        # transaction dates.
        wanted: dict[str, set[date]] = {
            sym: set(PRICE_MONTHS)
            for sym in FOREIGN_CURRENCIES + SECURITY_MNEMONICS
        }
        for sym, when in security_price_dates():
            wanted[sym].add(when)
        for sym, when in fx_price_dates():
            wanted[sym].add(when)

        for sym, dates in wanted.items():
            comm = comm_by_mnemonic[sym]
            for pdate in sorted(dates):
                piecash.Price(
                    commodity=comm,
                    currency=cny,
                    date=pdate,
                    value=real_price(sym, pdate),
                    type="last",
                    source="user:market-data",
                )
                count += 1
        book.save()
    finally:
        book.close()
    return count


def security_price_dates() -> list[tuple[str, date]]:
    """(mnemonic, date) pairs for every investment buy/sell + DCA date."""
    pairs: list[tuple[str, date]] = []
    # Monthly DCA on the 1st (CSI300 + ChiNext). The 1st already has a
    # monthly snapshot, but include for clarity/robustness.
    for m in range(1, 13):
        for sym in ("510300", "159915"):
            pairs.append((sym, date(YEAR, m, 1)))
    # Quarterly discretionary trades.
    for m, day, _action, sym, _shares in INVESTMENT_TRADES:
        pairs.append((sym, date(YEAR, m, day)))
    return pairs


def fx_price_dates() -> list[tuple[str, date]]:
    """(foreign, date) pairs for every cross-currency post/pay + HKD txn date."""
    pairs: list[tuple[str, date]] = []
    for cur, when in CROSS_CCY_FX_DATES:
        pairs.append((cur, when))
    return pairs


# ── Phase 2: Chart of accounts ──────────────────────────────────

# (name, type, parent, commodity, namespace, placeholder)
ACCOUNTS = [
    # Assets
    ("Assets", "ASSET", None, "CNY", "CURRENCY", True),
    ("Current Assets", "ASSET", "Assets", "CNY", "CURRENCY", True),
    ("Checking Account", "BANK", "Assets:Current Assets", "CNY", "CURRENCY", False),
    ("Savings Account", "BANK", "Assets:Current Assets", "CNY", "CURRENCY", False),
    ("Cash", "CASH", "Assets:Current Assets", "CNY", "CURRENCY", False),
    ("WeChat Pay", "BANK", "Assets:Current Assets", "CNY", "CURRENCY", False),
    ("Alipay", "BANK", "Assets:Current Assets", "CNY", "CURRENCY", False),
    ("Receivables", "ASSET", "Assets", "CNY", "CURRENCY", True),
    ("Accounts Receivable", "RECEIVABLE", "Assets:Receivables", "CNY", "CURRENCY", False),
    ("Accounts Receivable USD", "RECEIVABLE", "Assets:Receivables", "USD", "CURRENCY", False),
    ("Accounts Receivable EUR", "RECEIVABLE", "Assets:Receivables", "EUR", "CURRENCY", False),
    ("Investments", "ASSET", "Assets", "CNY", "CURRENCY", True),
    ("Brokerage", "ASSET", "Assets:Investments", "CNY", "CURRENCY", True),
    ("Moutai", "STOCK", "Assets:Investments:Brokerage", "600519", "SSE", False),
    ("CATL", "STOCK", "Assets:Investments:Brokerage", "300750", "SZSE", False),
    ("CSI300", "MUTUAL", "Assets:Investments:Brokerage", "510300", "SSE", False),
    ("ChiNext", "MUTUAL", "Assets:Investments:Brokerage", "159915", "SZSE", False),
    ("Housing Fund", "BANK", "Assets:Investments", "CNY", "CURRENCY", False),
    ("Fixed Assets", "ASSET", "Assets", "CNY", "CURRENCY", True),
    ("Apartment", "ASSET", "Assets:Fixed Assets", "CNY", "CURRENCY", False),
    ("Vehicle", "ASSET", "Assets:Fixed Assets", "CNY", "CURRENCY", False),
    # Liabilities
    ("Liabilities", "LIABILITY", None, "CNY", "CURRENCY", True),
    ("Credit Card", "LIABILITY", "Liabilities", "CNY", "CURRENCY", True),
    ("ICBC Credit Card", "CREDIT", "Liabilities:Credit Card", "CNY", "CURRENCY", False),
    ("CMB Credit Card", "CREDIT", "Liabilities:Credit Card", "CNY", "CURRENCY", False),
    ("HSBC HKD Card", "CREDIT", "Liabilities:Credit Card", "HKD", "CURRENCY", False),
    ("Loans", "LIABILITY", "Liabilities", "CNY", "CURRENCY", True),
    ("Mortgage", "LIABILITY", "Liabilities:Loans", "CNY", "CURRENCY", False),
    ("Auto Loan", "LIABILITY", "Liabilities:Loans", "CNY", "CURRENCY", False),
    ("Accounts Payable", "PAYABLE", "Liabilities", "CNY", "CURRENCY", False),
    # Income
    ("Income", "INCOME", None, "CNY", "CURRENCY", True),
    ("Salary", "INCOME", "Income", "CNY", "CURRENCY", False),
    ("Contractor Income", "INCOME", "Income", "CNY", "CURRENCY", False),
    ("LLC Revenue", "INCOME", "Income", "CNY", "CURRENCY", False),
    ("Investment Income", "INCOME", "Income", "CNY", "CURRENCY", True),
    ("Dividends", "INCOME", "Income:Investment Income", "CNY", "CURRENCY", False),
    ("Capital Gains", "INCOME", "Income:Investment Income", "CNY", "CURRENCY", False),
    ("Housing Fund Income", "INCOME", "Income", "CNY", "CURRENCY", False),
    ("Reimbursements", "INCOME", "Income", "CNY", "CURRENCY", False),
    # Income:Foreign Exchange Gain/Loss is auto-created by pay_invoice; create
    # it up front so it always exists for direct FX transactions too.
    ("Foreign Exchange Gain/Loss", "INCOME", "Income", "CNY", "CURRENCY", False),
    # Expenses
    ("Expenses", "EXPENSE", None, "CNY", "CURRENCY", True),
    ("Housing", "EXPENSE", "Expenses", "CNY", "CURRENCY", True),
    ("Mortgage Interest", "EXPENSE", "Expenses:Housing", "CNY", "CURRENCY", False),
    ("Property Management", "EXPENSE", "Expenses:Housing", "CNY", "CURRENCY", False),
    ("Maintenance", "EXPENSE", "Expenses:Housing", "CNY", "CURRENCY", False),
    ("Auto", "EXPENSE", "Expenses", "CNY", "CURRENCY", True),
    ("Charging", "EXPENSE", "Expenses:Auto", "CNY", "CURRENCY", False),
    ("Insurance", "EXPENSE", "Expenses:Auto", "CNY", "CURRENCY", False),
    ("Maintenance", "EXPENSE", "Expenses:Auto", "CNY", "CURRENCY", False),
    ("Parking", "EXPENSE", "Expenses:Auto", "CNY", "CURRENCY", False),
    ("Groceries", "EXPENSE", "Expenses", "CNY", "CURRENCY", False),
    ("Dining", "EXPENSE", "Expenses", "CNY", "CURRENCY", False),
    ("Utilities", "EXPENSE", "Expenses", "CNY", "CURRENCY", True),
    ("Electric", "EXPENSE", "Expenses:Utilities", "CNY", "CURRENCY", False),
    ("Water", "EXPENSE", "Expenses:Utilities", "CNY", "CURRENCY", False),
    ("Gas", "EXPENSE", "Expenses:Utilities", "CNY", "CURRENCY", False),
    ("Internet", "EXPENSE", "Expenses:Utilities", "CNY", "CURRENCY", False),
    ("Phone", "EXPENSE", "Expenses:Utilities", "CNY", "CURRENCY", False),
    ("Insurance", "EXPENSE", "Expenses", "CNY", "CURRENCY", True),
    ("Health", "EXPENSE", "Expenses:Insurance", "CNY", "CURRENCY", False),
    ("Life", "EXPENSE", "Expenses:Insurance", "CNY", "CURRENCY", False),
    ("Taxes", "EXPENSE", "Expenses", "CNY", "CURRENCY", True),
    ("Income Tax", "EXPENSE", "Expenses:Taxes", "CNY", "CURRENCY", False),
    ("Social Insurance", "EXPENSE", "Expenses:Taxes", "CNY", "CURRENCY", False),
    ("Business Tax", "EXPENSE", "Expenses:Taxes", "CNY", "CURRENCY", False),
    ("Subscriptions", "EXPENSE", "Expenses", "CNY", "CURRENCY", False),
    ("Streaming", "EXPENSE", "Expenses", "CNY", "CURRENCY", False),
    ("Clothing", "EXPENSE", "Expenses", "CNY", "CURRENCY", False),
    ("Pet", "EXPENSE", "Expenses", "CNY", "CURRENCY", True),
    ("Food", "EXPENSE", "Expenses:Pet", "CNY", "CURRENCY", False),
    ("Vet", "EXPENSE", "Expenses:Pet", "CNY", "CURRENCY", False),
    ("Travel", "EXPENSE", "Expenses", "CNY", "CURRENCY", False),
    ("Education", "EXPENSE", "Expenses", "CNY", "CURRENCY", False),
    ("Gifts", "EXPENSE", "Expenses", "CNY", "CURRENCY", False),
    ("Charity", "EXPENSE", "Expenses", "CNY", "CURRENCY", False),
    ("Business", "EXPENSE", "Expenses", "CNY", "CURRENCY", True),
    ("Cloud Hosting", "EXPENSE", "Expenses:Business", "CNY", "CURRENCY", False),
    ("Software", "EXPENSE", "Expenses:Business", "CNY", "CURRENCY", False),
    ("Coworking", "EXPENSE", "Expenses:Business", "CNY", "CURRENCY", False),
    ("Interest", "EXPENSE", "Expenses", "CNY", "CURRENCY", True),
    ("Credit Card Interest", "EXPENSE", "Expenses:Interest", "CNY", "CURRENCY", False),
    ("Mortgage Interest", "EXPENSE", "Expenses:Interest", "CNY", "CURRENCY", False),
    ("Auto Loan Interest", "EXPENSE", "Expenses:Interest", "CNY", "CURRENCY", False),
    ("Medical", "EXPENSE", "Expenses", "CNY", "CURRENCY", False),
    ("Miscellaneous", "EXPENSE", "Expenses", "CNY", "CURRENCY", False),
    # Equity
    ("Equity", "EQUITY", None, "CNY", "CURRENCY", True),
    ("Opening Balances", "EQUITY", "Equity", "CNY", "CURRENCY", False),
]


def create_accounts(out_path: Path) -> int:
    """Create the full chart of accounts directly via piecash."""
    book = piecash.open_book(str(out_path), readonly=False)
    count = 0
    try:
        comm_by_key = {}
        for c in book.commodities:
            comm_by_key[(c.namespace, c.mnemonic)] = c
        cny = book.default_currency
        comm_by_key[("CURRENCY", "CNY")] = cny

        acct_by_path: dict[str, piecash.Account] = {}
        for name, atype, parent_path, comm_mn, ns, placeholder in ACCOUNTS:
            parent = (
                book.root_account if parent_path is None
                else acct_by_path[parent_path]
            )
            commodity = comm_by_key[(ns, comm_mn)]
            acct = piecash.Account(
                name=name,
                type=atype,
                parent=parent,
                commodity=commodity,
                placeholder=placeholder,
            )
            full = name if parent_path is None else f"{parent_path}:{name}"
            acct_by_path[full] = acct
            count += 1
        book.save()
    finally:
        book.close()
    return count


def set_account_slots(book: GnuCashBook) -> None:
    """Set credit-card / loan metadata slots."""
    book.set_account_slot(ICBC_CARD, "apr", "18.25")
    book.set_account_slot(ICBC_CARD, "credit_limit", "50000")
    book.set_account_slot(ICBC_CARD, "statement_close_day", "20")
    book.set_account_slot(CMB_CARD, "apr", "18.25")
    book.set_account_slot(CMB_CARD, "credit_limit", "80000")
    book.set_account_slot(CMB_CARD, "statement_close_day", "25")
    book.set_account_slot(HSBC_CARD, "apr", "21.0")
    book.set_account_slot(HSBC_CARD, "credit_limit", "60000")  # HKD terms
    book.set_account_slot(MORTGAGE, "apr", "3.85")
    book.set_account_slot(AUTO_LOAN, "apr", "4.90")


# ── Phase 3: Opening balances + investment lots ─────────────────

# (account_path, balance_cny)  — opening balances via equity offset.
OPENING_BALANCES = [
    (CHECKING, D("85000")),
    (SAVINGS, D("150000")),
    (CASH, D("2000")),
    (WECHAT, D("3500")),
    (ALIPAY, D("2800")),
    (HOUSING_FUND, D("68000")),
    (MORTGAGE, D("-2800000")),
    (AUTO_LOAN, D("-120000")),
    (ICBC_CARD, D("-8500")),
    (CMB_CARD, D("-12200")),
    (APARTMENT, D("4200000")),
    (VEHICLE, D("180000")),
]

# (account, units, cost_basis_cny, lot_title)
OPENING_LOTS = [
    (MOUTAI, D("5"), D("8500"), "茅台 2024 purchase"),
    (CATL, D("30"), D("7200"), "宁德时代 2024 purchase"),
    (CSI300, D("5000"), D("20000"), "沪深300 core position"),
    (CHINEXT, D("8000"), D("17600"), "创业板 growth position"),
]


def opening_balances(out_path: Path) -> None:
    """Post opening balances (one balanced transaction) + investment lots."""
    book = piecash.open_book(str(out_path), readonly=False)
    try:
        cny = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        jan1 = date(YEAR, 1, 1)

        # Cash/liability opening balances — single balanced transaction
        # against Equity:Opening Balances.
        splits = []
        total = D("0")
        for path, bal in OPENING_BALANCES:
            splits.append(piecash.Split(account=acct[path], value=bal))
            total += bal
        # Equity absorbs the residual so the transaction balances.
        splits.append(piecash.Split(account=acct[OPENING], value=-total))
        piecash.Transaction(
            currency=cny,
            description="期初余额 (Opening Balances)",
            post_date=jan1,
            splits=splits,
        )

        # Investment opening lots: buy each holding from equity at cost.
        for path, units, cost, title in OPENING_LOTS:
            inv_acct = acct[path]
            lot = piecash.Lot(
                title=title, account=inv_acct, notes="期初持仓 (opening position)",
                is_closed=0,
            )
            inv_split = piecash.Split(
                account=inv_acct, value=cost, quantity=units,
            )
            eq_split = piecash.Split(account=acct[OPENING], value=-cost)
            piecash.Transaction(
                currency=cny,
                description=f"期初持仓 — {title}",
                post_date=jan1,
                splits=[inv_split, eq_split],
            )
            inv_split.lot = lot

        book.save()
    finally:
        book.close()


# ── Generic bulk transaction writer (piecash, fast, no audit) ───

def write_bulk(out_path: Path, txns: list[dict]) -> int:
    """Write a list of {description, date, currency, splits:[(path, value[, qty])]}.

    Each split tuple is (account_path, value) for same-currency or
    (account_path, value, quantity) when the account commodity differs
    from the transaction currency. ``currency`` defaults to CNY.
    """
    book = piecash.open_book(str(out_path), readonly=False)
    count = 0
    try:
        cny = book.default_currency
        comm_by = {c.mnemonic: c for c in book.commodities}
        acct = {a.fullname: a for a in book.accounts}
        for t in txns:
            cur = comm_by[t.get("currency", "CNY")]
            splits = []
            for sp in t["splits"]:
                if len(sp) == 3:
                    path, value, qty = sp
                    splits.append(piecash.Split(
                        account=acct[path], value=value, quantity=qty,
                    ))
                else:
                    path, value = sp
                    splits.append(piecash.Split(
                        account=acct[path], value=value,
                    ))
            piecash.Transaction(
                currency=cur,
                description=t["description"],
                post_date=t["date"],
                splits=splits,
            )
            count += 1
        book.save()
    finally:
        book.close()
    return count


def _months(year: int = YEAR):
    return [date(year, m, 1) for m in range(1, 13)]


# ── Phase 4: Scheduled-transaction templates ────────────────────

def create_scheduled_templates(book: GnuCashBook) -> int:
    """Create the SX templates so scheduled-transaction tools have data.

    These are disabled at the end of the build to avoid the GnuCash GUI
    'Since Last Run' barrage. The actual recurring activity is generated
    directly in Phase 5 for speed and amortization-split fidelity.
    """
    start = "2025-01-15"
    count = 0

    def sx(name, description, splits, frequency, start_date=start):
        nonlocal count
        book.create_scheduled_transaction(
            name=name, description=description, splits=splits,
            start_date=start_date, frequency=frequency, enabled=True,
        )
        count += 1

    # Monthly salary (Chinese pattern — monthly, paid on the 15th).
    sx("Chen Yu Salary", "深圳市人民医院 工资", [
        {"account": SALARY, "amount": "-15000"},
        {"account": CHECKING, "amount": "11800"},
        {"account": EXP_INCOME_TAX, "amount": "450"},
        {"account": EXP_SOCIAL, "amount": "1650"},
        {"account": HOUSING_FUND, "amount": "1100"},
    ], "monthly")

    sx("Mortgage Payment", "房贷还款", [
        {"account": CHECKING, "amount": "-14800"},
        {"account": EXP_MORTGAGE_INT, "amount": "8983"},
        {"account": MORTGAGE, "amount": "5817"},
    ], "monthly")

    sx("Auto Loan Payment", "车贷还款", [
        {"account": CHECKING, "amount": "-2400"},
        {"account": EXP_AUTO_INT, "amount": "490"},
        {"account": AUTO_LOAN, "amount": "1910"},
    ], "monthly")

    simple_monthly = [
        ("Property Management", "物业管理费", CHECKING, EXP_PROP_MGMT, "850"),
        ("Electric", "电费", WECHAT, EXP_ELECTRIC, "200"),
        ("Water", "水费", WECHAT, EXP_WATER, "80"),
        ("Gas", "天然气", WECHAT, EXP_GAS, "60"),
        ("Internet", "中国电信宽带", WECHAT, EXP_INTERNET, "199"),
        ("Phone", "中国移动话费", WECHAT, EXP_PHONE, "128"),
        ("Streaming", "爱奇艺 + Bilibili", WECHAT, EXP_STREAMING, "45"),
        ("Alibaba Cloud", "阿里云", CMB_CARD, EXP_CLOUD, "350"),
        ("Coworking", "优客工场", CMB_CARD, EXP_COWORKING, "1500"),
        ("Pet Food", "字节口粮", ALIPAY, EXP_PET_FOOD, "280"),
        ("Auto Insurance", "车险", CHECKING, EXP_AUTO_INS, "450"),
        ("Parking", "停车月卡", WECHAT, EXP_PARKING, "800"),
    ]
    for name, desc, src, dst, amt in simple_monthly:
        sx(name, desc, [
            {"account": src, "amount": f"-{amt}"},
            {"account": dst, "amount": amt},
        ], "monthly")

    # Quarterly
    sx("Estimated Tax", "个体工商户季度预缴税", [
        {"account": CHECKING, "amount": "-8000"},
        {"account": EXP_BUSINESS_TAX, "amount": "8000"},
    ], "quarterly")
    sx("Pet Vet", "字节季度体检", [
        {"account": ALIPAY, "amount": "-500"},
        {"account": EXP_PET_VET, "amount": "500"},
    ], "quarterly")

    # Yearly
    sx("Spring Festival Red Envelopes", "春节红包", [
        {"account": CHECKING, "amount": "-6000"},
        {"account": EXP_GIFTS, "amount": "6000"},
    ], "yearly", start_date="2025-02-01")
    sx("Vehicle Inspection", "年检", [
        {"account": CHECKING, "amount": "-300"},
        {"account": EXP_AUTO_INS, "amount": "300"},
    ], "yearly", start_date="2025-03-01")

    return count


# ── Phase 5: Recurring instantiations (direct, with amortization) ─

def gen_recurring() -> list[dict]:
    txns: list[dict] = []
    rng = random.Random(SEED + 5)

    for i, m in enumerate(range(1, 13)):
        # Salary on the 15th, with overtime every 3rd month.
        overtime = D("0")
        if m % 3 == 0:
            overtime = D(str(rng.randint(500, 1500)))
        gross = D("15000") + overtime
        net = D("11800") + overtime  # overtime flows to checking
        txns.append({
            "description": "深圳市人民医院 工资" + (
                f" (含加班 ¥{overtime})" if overtime else ""),
            "date": date(YEAR, m, 15),
            "splits": [
                (SALARY, -gross),
                (CHECKING, net),
                (EXP_INCOME_TAX, D("450")),
                (EXP_SOCIAL, D("1650")),
                (HOUSING_FUND, D("1100")),
            ],
        })
        # Housing fund employer match income (forced savings).
        txns.append({
            "description": "住房公积金 单位缴存",
            "date": date(YEAR, m, 15),
            "splits": [
                (HOUSING_FUND, D("1100")),
                (HOUSING_FUND_INCOME, D("-1100")),
            ],
        })

        # Mortgage (H1 vs H2 amortization).
        h2 = m >= 7
        m_int = D("8870") if h2 else D("8983")
        m_pri = D("5930") if h2 else D("5817")
        txns.append({
            "description": "房贷还款",
            "date": date(YEAR, m, 5),
            "splits": [
                (CHECKING, -(m_int + m_pri)),
                (EXP_MORTGAGE_INT, m_int),
                (MORTGAGE, m_pri),
            ],
        })
        a_int = D("443") if h2 else D("490")
        a_pri = D("1957") if h2 else D("1910")
        txns.append({
            "description": "车贷还款",
            "date": date(YEAR, m, 5),
            "splits": [
                (CHECKING, -(a_int + a_pri)),
                (EXP_AUTO_INT, a_int),
                (AUTO_LOAN, a_pri),
            ],
        })

    # Simple monthly bills.
    simple = [
        ("物业管理费", CHECKING, EXP_PROP_MGMT, D("850"), 1),
        ("电费", WECHAT, EXP_ELECTRIC, D("200"), 6),
        ("水费", WECHAT, EXP_WATER, D("80"), 6),
        ("天然气", WECHAT, EXP_GAS, D("60"), 6),
        ("中国电信宽带", WECHAT, EXP_INTERNET, D("199"), 8),
        ("中国移动话费", WECHAT, EXP_PHONE, D("128"), 8),
        ("爱奇艺+Bilibili会员", WECHAT, EXP_STREAMING, D("45"), 10),
        ("阿里云", CMB_CARD, EXP_CLOUD, D("350"), 1),
        ("优客工场 联合办公", CMB_CARD, EXP_COWORKING, D("1500"), 1),
        ("字节口粮", ALIPAY, EXP_PET_FOOD, D("280"), 20),
        ("平安车险", CHECKING, EXP_AUTO_INS, D("450"), 12),
        ("停车月卡", WECHAT, EXP_PARKING, D("800"), 1),
    ]
    for desc, src, dst, amt, day in simple:
        for m in range(1, 13):
            txns.append({
                "description": desc,
                "date": date(YEAR, m, day),
                "splits": [(src, -amt), (dst, amt)],
            })

    # Quarterly estimated business tax.
    for m in (3, 6, 9, 12):
        txns.append({
            "description": "个体工商户季度预缴税",
            "date": date(YEAR, m, 15),
            "splits": [(CHECKING, D("-8000")), (EXP_BUSINESS_TAX, D("8000"))],
        })
    # Quarterly pet vet.
    for m in (2, 5, 8, 11):
        txns.append({
            "description": "字节季度体检",
            "date": date(YEAR, m, 10),
            "splits": [(ALIPAY, D("-500")), (EXP_PET_VET, D("500"))],
        })

    # Monthly mobile-wallet top-ups from checking. WeChat and Alipay
    # carry most of the daily spend (coffee, delivery, groceries,
    # utilities, charging, parking); without recurring funding their
    # small opening floats would go deeply negative over the year.
    for m in range(1, 13):
        txns.append({
            "description": "充值微信钱包",
            "date": date(YEAR, m, 2),
            "splits": [(CHECKING, D("-4500")), (WECHAT, D("4500"))],
        })
        txns.append({
            "description": "充值支付宝",
            "date": date(YEAR, m, 2),
            "splits": [(CHECKING, D("-4000")), (ALIPAY, D("4000"))],
        })

    # Yearly.
    txns.append({
        "description": "春节红包",
        "date": date(YEAR, 2, 1),
        "splits": [(CHECKING, D("-6000")), (EXP_GIFTS, D("6000"))],
    })
    txns.append({
        "description": "车辆年检",
        "date": date(YEAR, 3, 1),
        "splits": [(CHECKING, D("-300")), (EXP_AUTO_INS, D("300"))],
    })
    return txns


# ── Phase 6: Daily/weekly patterns + seasonal one-offs ──────────

def gen_daily_weekly() -> list[dict]:
    txns: list[dict] = []
    rng = random.Random(SEED + 6)

    # Weekday Luckin Coffee + daily convenience store + 3x/week Meituan.
    d = date(YEAR, 1, 1)
    end = date(YEAR, 12, 31)
    day_idx = 0
    while d <= end:
        wd = d.weekday()
        if wd < 5:  # weekday coffee
            amt = D(str(rng.randint(15, 22)))
            txns.append({
                "description": "瑞幸咖啡",
                "date": d,
                "splits": [(WECHAT, -amt), (EXP_DINING, amt)],
            })
        # convenience store most days
        if day_idx % 1 == 0 and rng.random() < 0.7:
            amt = D(str(rng.randint(18, 35)))
            txns.append({
                "description": rng.choice(["便利蜂", "7-11便利店", "全家便利店"]),
                "date": d,
                "splits": [(WECHAT, -amt), (EXP_GROCERIES, amt)],
            })
        if wd in (1, 3, 5):  # Meituan delivery 3x/week
            amt = D(str(rng.randint(28, 48)))
            txns.append({
                "description": "美团外卖",
                "date": d,
                "splits": [(ALIPAY, -amt), (EXP_DINING, amt)],
            })
        if wd == 6:  # weekly Hema groceries
            amt = D(str(rng.randint(300, 420)))
            txns.append({
                "description": "盒马鲜生",
                "date": d,
                "splits": [(ALIPAY, -amt), (EXP_GROCERIES, amt)],
            })
        if wd == 2:  # weekly EV charging
            amt = D(str(rng.randint(60, 100)))
            txns.append({
                "description": "EV充电",
                "date": d,
                "splits": [(WECHAT, -amt), (EXP_CHARGING, amt)],
            })
        d = date.fromordinal(d.toordinal() + 1)
        day_idx += 1

    # Monthly Sam's Club on ICBC card.
    for m in range(1, 13):
        amt = D(str(rng.randint(420, 580)))
        txns.append({
            "description": "山姆会员店",
            "date": date(YEAR, m, 12),
            "splits": [(ICBC_CARD, -amt), (EXP_GROCERIES, amt)],
        })

    # Seasonal one-offs (Chinese calendar).
    seasonal = [
        (date(YEAR, 1, 20), "年货采购", ALIPAY, EXP_GROCERIES, D("2500")),
        (date(YEAR, 2, 10), "春节旅行 回乡", CHECKING, EXP_TRAVEL, D("3500")),
        (date(YEAR, 3, 8), "字节年度疫苗体检", ALIPAY, EXP_PET_VET, D("800")),
        (date(YEAR, 3, 20), "春装", CMB_CARD, EXP_CLOTHING, D("1200")),
        (date(YEAR, 4, 5), "清明节 出行", CHECKING, EXP_TRAVEL, D("1500")),
        (date(YEAR, 5, 1), "劳动节 短途旅行", CHECKING, EXP_TRAVEL, D("2800")),
        (date(YEAR, 5, 28), "618预售", ALIPAY, EXP_CLOTHING, D("900")),
        (date(YEAR, 7, 15), "台风季备货", ALIPAY, EXP_MISC, D("400")),
        (date(YEAR, 8, 20), "办公新显示器", CMB_CARD, EXP_SOFTWARE, D("2800")),
        (date(YEAR, 9, 10), "中秋月饼礼盒", ALIPAY, EXP_GIFTS, D("1800")),
        (date(YEAR, 10, 2), "国庆节旅行", CHECKING, EXP_TRAVEL, D("4500")),
        (date(YEAR, 10, 25), "双十一定金", ALIPAY, EXP_CLOTHING, D("500")),
        (date(YEAR, 12, 20), "节日礼物", ALIPAY, EXP_GIFTS, D("2000")),
        (date(YEAR, 12, 28), "年末慈善捐款", CHECKING, EXP_CHARITY, D("1000")),
    ]
    for dt, desc, src, dst, amt in seasonal:
        txns.append({
            "description": desc,
            "date": dt,
            "splits": [(src, -amt), (dst, amt)],
        })

    # 618 (June) spread across 5 transactions (¥3,200).
    june_amts = [D("800"), D("700"), D("650"), D("550"), D("500")]
    for i, amt in enumerate(june_amts):
        txns.append({
            "description": f"618购物节 第{i+1}单",
            "date": date(YEAR, 6, 10 + i),
            "splits": [(ALIPAY, -amt), (EXP_CLOTHING, amt)],
        })

    # Double 11 (November) spread across 8 transactions (¥5,500).
    nov_amts = [D("900"), D("800"), D("750"), D("700"), D("650"),
                D("600"), D("550"), D("550")]
    for i, amt in enumerate(nov_amts):
        txns.append({
            "description": f"双十一 第{i+1}单",
            "date": date(YEAR, 11, 11 + (i // 2)),
            "splits": [(ICBC_CARD, -amt), (EXP_CLOTHING, amt)],
        })

    return txns


# ── Phase 7a: Direct CNY contractor income ──────────────────────

def gen_contractor_income() -> list[dict]:
    contracts = [
        (range(1, 4), "华为云 外包", D("18000")),
        (range(5, 8), "腾讯 小程序项目", D("22000")),
        (range(9, 11), "字节跳动 数据看板", D("20000")),
        (range(11, 13), "美团 商家App", D("25000")),
    ]
    txns = []
    for months, client, amt in contracts:
        for m in months:
            txns.append({
                "description": f"{client} 合同款",
                "date": date(YEAR, m, 18),
                "splits": [(CHECKING, amt), (CONTRACTOR, -amt)],
            })
    return txns


# ── Phase 7b: Business module (customers, vendors, invoices, bills)

def run_business(book: GnuCashBook) -> dict:
    """Create billterms, customers, vendors, invoices, and bills.

    Returns a small dict of counts plus the JetBrains bill id (for
    verification).
    """
    counts = {"customers": 0, "vendors": 0, "invoices": 0, "bills": 0,
              "terms": 0}

    book.create_billterm(name="Net 15", due_days=15,
                         description="15天内付款")
    book.create_billterm(name="Net 30", due_days=30,
                         description="30天内付款")
    book.create_billterm(name="2/10 Net 30", due_days=30, discount_days=10,
                         discount_percent="2",
                         description="10天内付款享2%折扣")
    counts["terms"] = 3

    # Customers.
    shenzhen = book.create_customer(
        name="深圳跨境电商有限公司", currency="CNY",
        notes="本地跨境电商客户, Net 30")
    pacific = book.create_customer(
        name="Pacific Trade Solutions", currency="USD",
        notes="美国客户, USD 计价, Net 30")
    munich = book.create_customer(
        name="Handelskontor München GmbH", currency="EUR",
        notes="德国客户, EUR 计价, Net 30")
    counts["customers"] = 3

    # Vendors.
    alibaba = book.create_vendor(
        name="阿里云", currency="CNY", notes="云服务")
    jetbrains = book.create_vendor(
        name="JetBrains", currency="USD",
        notes="IDE 订阅 (USD-denominated, FX payable case)")
    urwork = book.create_vendor(
        name="优客工场", currency="CNY", notes="联合办公空间")
    counts["vendors"] = 3

    def run_invoice(customer_id, month_open, day_open, month_pay, day_pay,
                    amount, description, currency, post_account,
                    pay_year=YEAR):
        date_open = date(YEAR, month_open, day_open).isoformat()
        date_pay = date(pay_year, month_pay, day_pay).isoformat()
        inv = book.create_invoice(
            customer_id=customer_id, date_opened=date_open,
            currency=currency, term="Net 30",
        )
        book.add_invoice_entry(
            invoice_id=inv["id"], account=LLC_REVENUE,
            description=description, quantity="1", price=amount,
        )
        # No force needed: add_prices() lays a real 0-day-old FX quote on
        # every cross-currency post & pay date (see CROSS_CCY_FX_DATES),
        # so the freshness guard is satisfied with the true market rate.
        # The post→pay rate drift books a real realized FX gain/loss.
        book.post_invoice(
            invoice_id=inv["id"], post_account=post_account,
            post_date=date_open, owner_type="customer",
        )
        book.pay_invoice(
            invoice_id=inv["id"], payment_account=CHECKING,
            amount=amount, payment_date=date_pay, owner_type="customer",
        )
        counts["invoices"] += 1
        return inv["id"]

    # Shenzhen: ¥12,000/month, all 12 months (CNY → CNY checking).
    for m in range(1, 13):
        run_invoice(
            shenzhen["id"], m, 1, m, 28, "12000",
            f"{date(YEAR, m, 1).strftime('%Y年%m月')} 移动应用开发",
            "CNY", AR_CNY,
        )

    # Pacific Trade: USD invoices → AR USD, paid cross-currency to CNY.
    # PACIFIC_PLAN is shared with the price layer so quotes and posts/pays
    # land on identical dates (opened 5th, paid 5th of the next month).
    for m, amt in PACIFIC_PLAN:
        pay_m = m + 1 if m < 12 else 1
        pay_yr = YEAR if m < 12 else YEAR + 1
        run_invoice(
            pacific["id"], m, 5, pay_m, 5, amt,
            f"{date(YEAR, m, 1).strftime('%B %Y')} cross-border app engagement",
            "USD", AR_USD, pay_year=pay_yr,
        )

    # Munich: EUR invoices → AR EUR, paid cross-currency to CNY.
    # MUNICH_PLAN is shared with the price layer (opened 8th, paid 8th of
    # the next month).
    for m, amt in MUNICH_PLAN:
        pay_m = m + 1
        run_invoice(
            munich["id"], m, 8, pay_m, 8, amt,
            f"{date(YEAR, m, 1).strftime('%B %Y')} Softwareentwicklung",
            "EUR", AR_EUR,
        )

    # Vendor bills.
    def run_bill(vendor_id, month_open, day_open, month_pay, day_pay, amount,
                 description, expense_account, currency,
                 payment_account=CHECKING, pay=True, post_account=AP):
        date_open = date(YEAR, month_open, day_open).isoformat()
        date_pay = date(YEAR, month_pay, day_pay).isoformat()
        bill = book.create_bill(
            vendor_id=vendor_id, date_opened=date_open,
            currency=currency, term="Net 30",
        )
        book.add_bill_entry(
            bill_id=bill["id"], account=expense_account,
            description=description, quantity="1", price=amount,
        )
        # No force: a real FX quote sits on every cross-currency post/pay
        # date (CROSS_CCY_FX_DATES). CNY bills don't convert at all.
        book.post_invoice(
            invoice_id=bill["id"], post_account=post_account,
            post_date=date_open, owner_type="vendor",
        )
        if pay:
            book.pay_invoice(
                invoice_id=bill["id"], payment_account=payment_account,
                amount=amount, payment_date=date_pay, owner_type="vendor",
            )
        counts["bills"] += 1
        return bill["id"]

    # Alibaba Cloud: a couple of CNY bills through the business module
    # (the monthly recurring cloud charge in Phase 5 is the day-to-day;
    # these exercise the vendor-bill path explicitly).
    for m in (4, 10):
        run_bill(alibaba["id"], m, 2, m, 20, "350",
                 f"{date(YEAR, m, 1).strftime('%Y年%m月')} 云服务器",
                 EXP_CLOUD, "CNY")

    # UrWork: CNY bills.
    for m in (2, 8):
        run_bill(urwork["id"], m, 3, m, 18, "1500",
                 f"{date(YEAR, m, 1).strftime('%Y年%m月')} 工位租赁",
                 EXP_COWORKING, "CNY")

    # JetBrains US$249 — the foreign-currency PAYABLE regression case (M2).
    # Posted to the CNY-only ``Liabilities:Accounts Payable`` (the account
    # the spec and the validated book use) and left OUTSTANDING (pay=False).
    # The USD bill currency drives the A/P split: its VALUE is in USD (the
    # transaction currency) while its QUANTITY is the CNY equivalent at the
    # real USD/CNY rate on the post date. vendor_spending_report converts
    # off the bill currency regardless of the A/P account's commodity, so
    # M2 coverage holds without a USD-denominated A/P account.
    jetbrains_bill_id = run_bill(
        jetbrains["id"], JETBRAINS_POST.month, JETBRAINS_POST.day, 1, 25, "249",
        "JetBrains All Products Pack (annual subscription)",
        EXP_SOFTWARE, "USD", pay=False, post_account=AP,
    )

    counts["jetbrains_bill_id"] = jetbrains_bill_id
    return counts


# ── Phase 8: Investment activity ────────────────────────────────

ACCT_BY_SYMBOL = {
    "600519": MOUTAI, "300750": CATL, "510300": CSI300, "159915": CHINEXT,
}
OPENING_LOT_TITLE = {
    "600519": "茅台 2024 purchase",
    "300750": "宁德时代 2024 purchase",
    "510300": "沪深300 core position",
    "159915": "创业板 growth position",
}
FRACTION = {"600519": 100, "300750": 100, "510300": 10000, "159915": 10000}


def _shares_from_cny(cny: Decimal, price: Decimal, fraction: int) -> Decimal:
    places = Decimal(1) / Decimal(fraction)
    return (cny / price).quantize(places)


def run_investments(out_path: Path) -> dict:
    """Monthly DCA, quarterly trades, and dividends. Direct piecash."""
    book = piecash.open_book(str(out_path), readonly=False)
    counts = {"txns": 0, "lots": 0}
    try:
        cny = book.default_currency
        acct = {a.fullname: a for a in book.accounts}

        def find_lot(title):
            for a in book.accounts:
                for lot in a.lots:
                    if lot.title == title:
                        return lot
            return None

        # Monthly DCA: ¥2,000 CSI300 + ¥1,000 ChiNext on the 1st. Real
        # market price at the buy date sets the share count and booked
        # value, so the lot cost basis reflects actual history.
        dca = [("510300", D("2000")), ("159915", D("1000"))]
        for m in range(1, 13):
            for sym, amt in dca:
                price = real_price(sym, date(YEAR, m, 1))
                shares = _shares_from_cny(amt, price, FRACTION[sym])
                inv_acct = acct[ACCT_BY_SYMBOL[sym]]
                lot = piecash.Lot(
                    title=f"{sym} DCA {YEAR}-{m:02d}", account=inv_acct,
                    notes=f"定投 ¥{amt} @ ¥{price}", is_closed=0,
                )
                inv_split = piecash.Split(
                    account=inv_acct, value=amt, quantity=shares)
                cash_split = piecash.Split(account=acct[CHECKING], value=-amt)
                piecash.Transaction(
                    currency=cny, description=f"定投 {sym}",
                    post_date=date(YEAR, m, 1),
                    splits=[inv_split, cash_split],
                )
                inv_split.lot = lot
                counts["txns"] += 1
                counts["lots"] += 1

        # Quarterly trades: shares fixed in INVESTMENT_TRADES; the price
        # is the real market close at the trade date (same quote the
        # price layer wrote), so booked value == shares × real price.
        for m, day, action, sym, shares in INVESTMENT_TRADES:
            d = date(YEAR, m, day)
            price = real_price(sym, d)
            inv_acct = acct[ACCT_BY_SYMBOL[sym]]
            cny_amt = (shares * price).quantize(D("0.01"))
            if action == "buy":
                lot = piecash.Lot(
                    title=f"{sym} {d.isoformat()} purchase", account=inv_acct,
                    notes=f"{shares} 股 @ ¥{price}", is_closed=0,
                )
                inv_split = piecash.Split(
                    account=inv_acct, value=cny_amt, quantity=shares)
                cash_split = piecash.Split(account=acct[CHECKING], value=-cny_amt)
                piecash.Transaction(
                    currency=cny, description=f"买入 {shares} {sym} @ ¥{price}",
                    post_date=d, splits=[inv_split, cash_split],
                )
                inv_split.lot = lot
                counts["lots"] += 1
            else:
                lot = find_lot(OPENING_LOT_TITLE[sym])
                opening_split = lot.splits[0]
                cost_per = (Decimal(str(opening_split.value))
                            / Decimal(str(opening_split.quantity)))
                cost_basis = (shares * cost_per).quantize(D("0.01"))
                gain = cny_amt - cost_basis
                inv_split = piecash.Split(
                    account=inv_acct, value=-cost_basis, quantity=-shares)
                cash_split = piecash.Split(account=acct[CHECKING], value=cny_amt)
                gain_split = piecash.Split(
                    account=acct[CAPITAL_GAINS], value=-gain)
                piecash.Transaction(
                    currency=cny, description=f"卖出 {shares} {sym} @ ¥{price}",
                    post_date=d, splits=[inv_split, cash_split, gain_split],
                )
                inv_split.lot = lot
            counts["txns"] += 1

        # Dividends (cash to checking).
        dividends = [
            (6, 15, "贵州茅台 现金分红", D("125")),   # 5 sh × ~¥25
            (8, 15, "宁德时代 现金分红", D("90")),     # 30 sh × ~¥3
        ]
        for m, day, desc, amt in dividends:
            piecash.Transaction(
                currency=cny, description=desc, post_date=date(YEAR, m, day),
                splits=[
                    piecash.Split(account=acct[CHECKING], value=amt),
                    piecash.Split(account=acct[DIVIDENDS], value=-amt),
                ],
            )
            counts["txns"] += 1

        book.save()
    finally:
        book.close()
    return counts


# ── Phase 9: Credit card lifecycle ──────────────────────────────

def gen_credit_cards() -> list[dict]:
    """ICBC payoff arc, CMB monthly + Sep late fee, HSBC HKD charges."""
    txns: list[dict] = []

    # ICBC: interest Jan-Apr, payments toward payoff by May.
    icbc_interest = [(1, D("130")), (2, D("100")), (3, D("70")), (4, D("35"))]
    for m, amt in icbc_interest:
        txns.append({
            "description": "工商银行信用卡 利息",
            "date": date(YEAR, m, 18),
            "splits": [(ICBC_CARD, -amt), (EXP_CC_INT, amt)],
        })
    icbc_payments = [(1, D("2500")), (2, D("2500")), (3, D("2500")),
                     (4, D("2500")), (5, D("3000"))]
    for m, amt in icbc_payments:
        txns.append({
            "description": "工商银行信用卡 还款",
            "date": date(YEAR, m, 22),
            "splits": [(CHECKING, -amt), (ICBC_CARD, amt)],
        })

    # CMB: monthly statement payment; September late fee + interest.
    for m in range(1, 13):
        amt = D("1850")  # covers monthly Alibaba+coworking charges
        txns.append({
            "description": "招商银行信用卡 还款",
            "date": date(YEAR, m, 25),
            "splits": [(CHECKING, -amt), (CMB_CARD, amt)],
        })
    txns.append({
        "description": "招商银行信用卡 滞纳金",
        "date": date(YEAR, 9, 26),
        "splits": [(CMB_CARD, D("-50")), (EXP_CC_INT, D("50"))],
    })
    txns.append({
        "description": "招商银行信用卡 逾期利息",
        "date": date(YEAR, 9, 26),
        "splits": [(CMB_CARD, D("-180")), (EXP_CC_INT, D("180"))],
    })

    # HSBC HKD card — FOREIGN-currency liability (H1). All splits in HKD;
    # the transaction currency is HKD and the offsetting CNY account split
    # carries a HKD value (txn currency) + CNY quantity (account
    # commodity). We keep ~HK$6,460 net balance by charging more than is
    # paid. Each charge's CNY quantity uses the REAL HKD/CNY rate on the
    # charge date (a matching quote is on file via CROSS_CCY_FX_DATES); the
    # report-time valuation re-converts at the latest rate regardless.
    for dt, desc, hkd_amt in HSBC_CHARGES:
        rate = md_fx_cny("HKD", dt)
        cny_val = (hkd_amt * rate).quantize(D("0.01"))
        txns.append({
            "description": desc,
            "date": dt,
            "currency": "HKD",
            "splits": [
                # Liability split: HKD card, value in HKD (txn currency).
                (HSBC_CARD, -hkd_amt),
                # Expense split: CNY account, value in HKD (txn currency),
                # quantity in CNY (account commodity) at the real rate.
                (EXP_GROCERIES, hkd_amt, cny_val),
            ],
        })
    # One partial payment (HKD): pay HK$1,000, leaving ~HK$6,460.
    pay_dt, pay_hkd = HSBC_PAYMENT
    pay_rate = md_fx_cny("HKD", pay_dt)
    pay_cny = (pay_hkd * pay_rate).quantize(D("0.01"))
    txns.append({
        "description": "汇丰 港币卡 还款",
        "date": pay_dt,
        "currency": "HKD",
        "splits": [
            (HSBC_CARD, pay_hkd),          # liability down (HKD)
            (CHECKING, -pay_hkd, -pay_cny),  # CNY checking, HKD value / CNY qty
        ],
    })
    return txns


# ── Phase 10: Budget ────────────────────────────────────────────

def run_budget(book: GnuCashBook) -> None:
    name = "2025 年度预算"
    book.create_budget(name=name, year=YEAR, num_periods=12,
                       period_type="monthly",
                       description="林微 2025 年度家庭预算")
    monthly = [
        (EXP_GROCERIES, "2500"),
        (EXP_DINING, "2000"),
        (EXP_PROP_MGMT, "850"),
        (EXP_CHARGING, "350"),
        (EXP_PARKING, "800"),
        (EXP_STREAMING, "50"),
        (EXP_CLOTHING, "800"),
        (EXP_TRAVEL, "1500"),
        (EXP_CLOUD, "400"),
        (EXP_GIFTS, "500"),
        (EXP_MISC, "1000"),
    ]
    for acct, amt in monthly:
        book.set_budget_amount(budget_name=name, account=acct, amount=amt,
                               period="all")
    # Utilities (parent rollup): set on the placeholder parent.
    book.set_budget_amount(budget_name=name,
                           account="Expenses:Utilities", amount="700",
                           period="all")
    # Pet (parent rollup).
    book.set_budget_amount(budget_name=name, account="Expenses:Pet",
                           amount="400", period="all")

    # Seasonal overrides (period is 0-indexed: Feb=1, Jun=5, Sep=8, Oct=9, Nov=10).
    book.set_budget_amount(budget_name=name, account=EXP_TRAVEL,
                           amount="4000", period=1)
    book.set_budget_amount(budget_name=name, account=EXP_TRAVEL,
                           amount="3000", period=9)
    book.set_budget_amount(budget_name=name, account=EXP_GIFTS,
                           amount="6000", period=1)
    book.set_budget_amount(budget_name=name, account=EXP_GIFTS,
                           amount="2000", period=8)
    book.set_budget_amount(budget_name=name, account=EXP_CLOTHING,
                           amount="2000", period=5)
    book.set_budget_amount(budget_name=name, account=EXP_CLOTHING,
                           amount="3000", period=10)


# ── Phase 12: Edge cases ────────────────────────────────────────

def run_edge_cases(book: GnuCashBook, out_path: Path) -> dict:
    """Voided txn, recategorized txn, returned purchase, internal transfers."""
    info = {}

    # 4 & 5 are done via bulk write below; 1-3 need GnuCashBook for
    # void/replace_splits round-trips.

    # 1. Voided transaction: ¥3,000 to Wrong Vendor on 03/15.
    void_res = book.create_transaction(
        description="错误供应商 (Wrong Vendor) 付款",
        trans_date=date(YEAR, 3, 15),
        splits=[
            {"account": CHECKING, "amount": "-3000"},
            {"account": EXP_MISC, "amount": "3000"},
        ],
        check_duplicates=False,
    )
    book.void_transaction(guid=void_res["guid"], reason="重复付款, 作废")
    info["voided_guid"] = void_res["guid"]

    # 2. Recategorized: ¥450 Office Supplies → Misc, then replace to Software.
    recat = book.create_transaction(
        description="办公用品",
        trans_date=date(YEAR, 4, 20),
        splits=[
            {"account": CMB_CARD, "amount": "-450"},
            {"account": EXP_MISC, "amount": "450"},
        ],
        check_duplicates=False,
    )
    book.replace_splits(
        guid=recat["guid"],
        splits=[
            {"account": CMB_CARD, "amount": "-450"},
            {"account": EXP_SOFTWARE, "amount": "450"},
        ],
    )
    info["recategorized_guid"] = recat["guid"]

    # 3. Returned purchase: buy ¥1,800 JD.com on ICBC 08/10, credit 08/22.
    book.create_transaction(
        description="京东商城 采购",
        trans_date=date(YEAR, 8, 10),
        splits=[
            {"account": ICBC_CARD, "amount": "-1800"},
            {"account": EXP_MISC, "amount": "1800"},
        ],
        check_duplicates=False,
    )
    book.create_transaction(
        description="京东商城 退货",
        trans_date=date(YEAR, 8, 22),
        splits=[
            {"account": ICBC_CARD, "amount": "1800"},
            {"account": EXP_MISC, "amount": "-1800"},
        ],
        check_duplicates=False,
    )

    # 5. Internal transfers between mobile-payment accounts.
    write_bulk(out_path, [
        {
            "description": "充值微信钱包 (Checking → WeChat Pay)",
            "date": date(YEAR, 5, 10),
            "splits": [(CHECKING, D("-5000")), (WECHAT, D("5000"))],
        },
        {
            "description": "微信转支付宝 (WeChat → Alipay)",
            "date": date(YEAR, 5, 11),
            "splits": [(WECHAT, D("-3000")), (ALIPAY, D("3000"))],
        },
    ])
    return info


# ── Phase 13: Volume stress ─────────────────────────────────────

VOLUME_VENDORS = ["瑞幸咖啡", "便利蜂", "全家便利店", "美团外卖",
                  "饿了么外卖", "滴滴出行", "共享单车", "自动贩卖机"]


def gen_volume() -> list[dict]:
    rng = random.Random(SEED + 13)
    txns = []
    start = date(YEAR, 1, 1).toordinal()
    span = (date(YEAR, 12, 31).toordinal() - start)
    for _ in range(VOLUME_TXN_COUNT):
        dt = date.fromordinal(start + rng.randint(0, span))
        vendor = rng.choice(VOLUME_VENDORS)
        amt = D(str(rng.randint(5, 80)))
        category = rng.choice([EXP_DINING, EXP_MISC])
        txns.append({
            "description": vendor,
            "date": dt,
            "splits": [(CHECKING, -amt), (category, amt)],
        })
    return txns


# ── Phase 11: Reconciliation ────────────────────────────────────

def run_reconciliation(book: GnuCashBook) -> None:
    """Reconcile ICBC checking for Jan, Jun, Dec 2025 (bulk through-date)."""
    for label, through, stmt_date in [
        ("January", date(YEAR, 1, 31), date(YEAR, 1, 31)),
        ("June", date(YEAR, 6, 30), date(YEAR, 6, 30)),
        ("December", date(YEAR, 12, 31), date(YEAR, 12, 31)),
    ]:
        # Compute the reconciled balance through the date and reconcile
        # everything unreconciled up to it.
        bal = book.get_balance(CHECKING, as_of_date=through)
        try:
            book.reconcile_account(
                account_name=CHECKING,
                statement_date=stmt_date,
                statement_balance=str(bal),
                reconcile_all=True,
                through_date=through,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  Reconciliation {label} skipped: {exc}")


# ── Disable scheduled transactions ──────────────────────────────

def disable_scheduled(book: GnuCashBook) -> None:
    sxs = book.list_scheduled_transactions(enabled_only=False)
    # list_scheduled_transactions may return compact string or list.
    import re
    guids = re.findall(r"\b([0-9a-f]{8,})\b", str(sxs))
    for g in set(guids):
        try:
            book.update_scheduled_transaction(guid=g, enabled=False)
        except Exception:  # noqa: BLE001
            pass


# ── Verification ────────────────────────────────────────────────

def _parse_money(s) -> Decimal:
    """Parse a comma-formatted money string (or Decimal) to Decimal."""
    return Decimal(str(s).replace(",", "").replace("¥", "").strip())


def verify(out_path: Path, business: dict) -> None:
    print("\n" + "=" * 64)
    print("VERIFICATION")
    print("=" * 64)
    book = GnuCashBook(str(out_path))

    # Covers all activity (foreign-currency invoice payments cross into
    # early 2026) and reaches the latest real price snapshot (END).
    as_of = END
    latest_hkd = md_fx_cny("HKD", END)
    latest_usd = md_fx_cny("USD", END)

    summary = book.get_book_summary()
    bs = book.balance_sheet(as_of_date=as_of)
    nw = book.net_worth(end_date=as_of)

    # Net worth from the three tools.
    bs_assets = _parse_money(bs["assets"]["total"])
    bs_liab = _parse_money(bs["liabilities"]["total"])
    bs_nw = bs_assets - bs_liab
    nw_val = _parse_money(nw["net_worth"])

    print("\n-- Cross-tool net worth agreement --")
    print(f"  balance_sheet: assets {bs_assets:,.2f} - liabilities "
          f"{bs_liab:,.2f} = net worth {bs_nw:,.2f}")
    print(f"  net_worth tool:                 {nw_val:,.2f}")
    print(f"  agree (bs vs net_worth):        {abs(bs_nw - nw_val) < 1}")
    # get_book_summary renders net worth in its text; surface the line.
    for line in summary.splitlines():
        if "net worth" in line.lower() or "净资产" in line:
            print(f"  get_book_summary: {line.strip()}")

    # HSBC HKD card converted value (real latest HKD/CNY rate).
    print("\n-- HSBC HKD card (FX liability, H1) --")
    hkd_bal_native = book.get_balance(HSBC_CARD)
    print(f"  get_balance (HKD account commodity): HK$ {hkd_bal_native}")
    expected_cny = (Decimal(str(hkd_bal_native)) * latest_hkd)
    print(f"  expected CNY ≈ HK${hkd_bal_native} × {latest_hkd} = "
          f"¥{expected_cny.quantize(D('0.01'))}")
    hsbc_rows = [r for r in bs["liabilities"]["accounts"]
                 if "HSBC" in r["account"]]
    print(f"  balance_sheet liability row: {hsbc_rows}")

    # JetBrains bill — now in the CNY A/P, valued via real USD/CNY (M2).
    print("\n-- JetBrains bill (FX payable, outstanding, CNY A/P) --")
    ap_bal = book.get_balance(AP)
    print(f"  Accounts Payable (CNY) balance: {ap_bal}")
    print(f"  expected ≈ $249 × {latest_usd} = "
          f"¥{(D('249') * latest_usd).quantize(D('0.01'))}")
    ap_rows = [r for r in bs["liabilities"]["accounts"]
               if "Payable" in r["account"]]
    print(f"  balance_sheet A/P rows: {ap_rows}")
    try:
        vsr = book.vendor_spending_report(
            start_date=date(YEAR, 1, 1).isoformat(),
            end_date=as_of.isoformat(), compact=False)
        jb = [r for r in vsr.get("vendors", [])
              if "JetBrains" in str(r.get("vendor_name", ""))]
        print(f"  vendor_spending_report JetBrains row (CNY): {jb}")
        if vsr.get("unconverted"):
            print(f"  ⚠ unconverted bills (should be empty): "
                  f"{vsr['unconverted']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  vendor_spending_report unavailable: {exc}")

    # Investment holdings via real security prices.
    print("\n-- Investment holdings (real security prices) --")
    print(f"  Moutai latest real price: ¥{md_security('600519', END)}")
    for path in (MOUTAI, CATL, CSI300, CHINEXT):
        bal = book.get_balance(path)
        print(f"  {path}: shares {bal}")
    inv_rows = [r for r in bs["assets"]["accounts"]
                if "Brokerage" in r["account"]]
    print(f"  balance_sheet brokerage rows (CNY value): {inv_rows}")

    print("\n-- Counts --")
    with book.open() as b:
        n_acct = len(list(b.accounts))
        n_txn = len(list(b.transactions))
        n_inv = len(list(b.invoices))
        n_price = len(list(b.prices))
        n_cust = len(list(b.customers))
        n_vend = len(list(b.vendors))
        lots = 0
        for a in b.accounts:
            lots += len(a.lots)
    print(f"  accounts:        {n_acct}")
    print(f"  transactions:    {n_txn}")
    print(f"  invoices+bills:  {n_inv}")
    print(f"  customers:       {n_cust}")
    print(f"  vendors:         {n_vend}")
    print(f"  prices:          {n_price}")
    print(f"  lots:            {lots}")

    print("\n-- balance_sheet liabilities (full) --")
    for r in bs["liabilities"]["accounts"]:
        print(f"    {r}")
    print(f"  TOTAL liabilities: {bs['liabilities']['total']}")


# ── Driver ──────────────────────────────────────────────────────

def build(out_path: Path) -> None:
    print(f"Building Lin Wei book at: {out_path}")

    print("\nPhase 1: book file + commodities")
    create_book_file(out_path)
    n_prices = add_prices(out_path)
    print(f"  commodities + {n_prices} prices created")

    print("\nPhase 2: chart of accounts")
    n_acct = create_accounts(out_path)
    print(f"  {n_acct} accounts created")

    book = GnuCashBook(str(out_path))
    set_account_slots(book)
    print("  account slots set")

    print("\nPhase 3: opening balances + investment lots")
    opening_balances(out_path)
    print("  opening balances posted")

    print("\nPhase 4: scheduled-transaction templates")
    n_sx = create_scheduled_templates(book)
    print(f"  {n_sx} SX templates created")

    print("\nPhase 5: recurring instantiations")
    n = write_bulk(out_path, gen_recurring())
    print(f"  {n} recurring transactions")

    print("\nPhase 6: daily/weekly + seasonal")
    n = write_bulk(out_path, gen_daily_weekly())
    print(f"  {n} daily/weekly/seasonal transactions")

    print("\nPhase 7a: direct contractor income")
    n = write_bulk(out_path, gen_contractor_income())
    print(f"  {n} contractor deposits")

    print("\nPhase 7b: business module")
    business = run_business(book)
    print(f"  {business}")

    print("\nPhase 8: investments")
    inv_counts = run_investments(out_path)
    print(f"  {inv_counts}")

    print("\nPhase 9: credit card lifecycle")
    n = write_bulk(out_path, gen_credit_cards())
    print(f"  {n} credit-card transactions")

    print("\nPhase 10: budget")
    run_budget(book)
    print("  budget created")

    print("\nPhase 12: edge cases")
    edge = run_edge_cases(book, out_path)
    print(f"  {edge}")

    print("\nPhase 13: volume stress")
    n = write_bulk(out_path, gen_volume())
    print(f"  {n} volume transactions")

    print("\nPhase 11: reconciliation")
    run_reconciliation(book)
    print("  reconciliation done")

    print("\nDisabling scheduled transactions")
    disable_scheduled(book)
    print("  scheduled transactions disabled")

    verify(out_path, business)
    print("\nDone.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="Output path (default: samples/lin-wei.generated.gnucash)")
    args = parser.parse_args()
    out_path = Path(args.out).resolve()
    if out_path == PROTECTED.resolve():
        raise SystemExit(f"REFUSING to write to protected book: {PROTECTED}")
    build(out_path)


if __name__ == "__main__":
    main()
