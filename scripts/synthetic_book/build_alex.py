"""Build the Alex Chen-Morales USD-default synthetic GnuCash book FROM ZERO.

The USD analogue of ``build_lin_wei.py``: a single script that creates
Alex's entire book from nothing — the SQLite file, the commodities,
the full chart of accounts, opening balances and investment opening
lots, scheduled-transaction templates, recurring instantiations,
daily/weekly spending, the LLC business module (every client billed
through A/R), investment activity, the credit-card lifecycle, a
budget, reconciliation, edge cases, and a volume stress phase.

It implements ``specs/SYNTHETIC_BOOK_SPEC.md`` — a USD-default book
for a Seattle software contractor (Cascade Code LLC) with a European
client paying in EUR — as revised by the IRS-minded audit in
``specs/v1.5/testing/AUDIT_ALEX_IRS_2026-09-11.md`` and the cold
re-read in ``AUDIT_ALEX_COLD_2026-09-17.md``: the LLC keeps its own
checking account (opened with a balance — it pre-exists the book)
and pays the household by owner's draw, one plain transfer per
month; every dollar of revenue is an invoice; the one contract
developer is a 1099 vendor billing in arrears on the month's last
business day (no employee, no vouchers — a W-2 employee with no
payroll was the audit's phantom); quarterly 1040-ES installments
and the April settlement come from ONE household MFJ model (Robin's
W-2 and withholding, Schedule C, SE tax with the wage base, the
Solo 401(k), QBI, the bracket table) — the settlement is signed, so
an overpaid year posts a refund; WA B&O is apportioned to the
Washington clients (RCW 82.04.462) and Seattle's B&O taxes the whole
apportioned base once worldwide gross clears the $100K exemption;
the Seattle license and the SOS annual report are paid from the
LLC; Robin's stub carries WA PFML (year-keyed rate) / WA Cares / L&I
and a 7% UWRP deferral with match, withholding computed on wages net
of §125 deductions; the retirement plans move with the market
quarterly; card statements are paid from the running balance
(interest only when a balance carries; the business card's interest
and fees on their own Schedule C line); savings earn taxable
interest, the HSA exempt interest; fund distributions are shares ×
per-share rate. ACH debits, autopays and IRS deadlines roll off
weekends; every row's ``enter_date`` is its post day. The mandatory
FX regression case is unchanged: Berlin Digital GmbH EUR invoices
post to a EUR A/R sub-account and settle cross-currency EUR->USD
with realized FX gain/loss booked.

Money flows that depend on the book's own state — statement
payments, owner's draws, surplus sweeps, savings interest — come
from the same closed-loop engine the clone-side updater runs
(``continuation.run_policy``), so the frozen base and every
continuation obey one rule set from 2025-01-01 onward.

All security prices and FX rates come from REAL historical market data
via ``market_data.MarketData`` (offline, cache-backed): VTSAX, VBTLX,
AAPL, MSFT, ETH (all USD-denominated; ETH is crypto) and EUR/CAD
FX (both -> USD). Trade prices and conversion amounts use the actual
quotes, not invented numbers; per-share distribution rates are the
only investment constants. EUR backs Berlin Digital GmbH and CAD
backs Nord Analytique; both have real A/R accounts, invoices, and
on-date prices (no orphan commodities).

SAFETY: this script writes ONLY to ``samples/alex.generated.gnucash``
(the ``--out`` path). It NEVER touches the bookkeeper-validated
``samples/alex-chen-morales.gnucash``.

Usage:
    uv run python scripts/synthetic_book/build_alex.py
    uv run python scripts/synthetic_book/build_alex.py --out /tmp/alex.gnucash
"""

from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import piecash

from gnucash_mcp.book import GnuCashBook

# market_data lives alongside this script; import works whether run as a
# module or as a path because uv adds the script dir to sys.path.
try:
    from market_data import MarketData
except ImportError:  # pragma: no cover - fallback for package-style import
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from market_data import MarketData


# ── Configuration ───────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "samples" / "alex.generated.gnucash"
PROTECTED = REPO_ROOT / "samples" / "alex-chen-morales.gnucash"

SEED = 20250101
# Phase 13 small casual-spend transactions per year of book — thinned
# from 320 (cold audit C5: the micro-purchase floor was ~550 lines in
# 20 months with a visible $16 cap).
VOLUME_TXN_COUNT = 170

YEAR = 2025  # the book opens 2025-01-01

# How far forward the "living tissue" runs. The skeleton (opening
# balances, lots, business module) is anchored in 2025; recurring
# activity, daily/weekly spend, and seasonal one-offs now run
# continuously from 2025-01-01 through THROUGH so the book reaches the
# present with a realistic burn-rate, runway, and monthly-net (no months
# of zero activity — the "data cliff" the bookkeeper flagged). Override
# with ``--through YYYY-MM-DD`` (the verify step pins 2026-06-04).
THROUGH = date.today()

# The committed market-data cache ends here; quotes past this date
# forward-fill the last real close (MarketData already does this, but we
# keep the constant so price-snapshot generation knows the horizon).
CACHE_END = date(2026, 6, 30)

D = Decimal

# Shared real-market-data accessor (offline; reads the committed cache).
MD = MarketData.load()


def _month_iter(start: date, end: date):
    """Yield (year, month) for every month touched by [start, end]."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _clamp_day(year: int, month: int, day: int) -> date:
    """A date on ``day`` of the month, clamped to the real month-end."""
    last = _days_in_month(year, month)
    return date(year, month, min(day, last))


def _next_bday(d: date) -> date:
    """``d`` rolled forward off a weekend to the next Monday.

    ACH debits, autopays, IRS deadlines and fund trades post on business
    days (a 1040-ES due on a Sunday is due the Monday); card charges and
    bank-side interest credits may land on any calendar day and are
    deliberately NOT routed through here.
    """
    if d.weekday() >= 5:
        d += timedelta(days=7 - d.weekday())
    return d


def _last_bday(year: int, month: int) -> date:
    """The last business day of the month."""
    d = _clamp_day(year, month, 31)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ── Account path constants ──────────────────────────────────────

CHECKING = "Assets:Current Assets:Checking Account"
SAVINGS = "Assets:Current Assets:Savings Account"
CASH = "Assets:Current Assets:Cash"
# The LLC's own operating account (audit B3): every invoice settles
# here, every business payable is paid from here, and the household
# is paid by owner's draw.
LLC_CHECKING = "Assets:Current Assets:Cascade Code LLC Checking"
AR_USD = "Assets:Accounts Receivable"
AR_EUR = "Assets:Receivables:Accounts Receivable EUR"
AR_CAD = "Assets:Receivables:Accounts Receivable CAD"
HSA = "Assets:Investments:HSA"
CONDO = "Assets:Fixed Assets:Condo"
VEHICLE = "Assets:Fixed Assets:Vehicle"
UWRP = "Assets:Retirement:UWRP 403(b)"
SOLO_401K = "Assets:Retirement:Solo 401(k)"

VTSAX = "Assets:Investments:Brokerage:VTSAX"
VBTLX = "Assets:Investments:Brokerage:VBTLX"
AAPL = "Assets:Investments:Brokerage:AAPL"
MSFT = "Assets:Investments:Brokerage:MSFT"
# Crypto is custodied at an exchange, not the fund brokerage (audit B18).
ETH = "Assets:Investments:Coinbase:ETH"

CHASE = "Liabilities:Credit Card:Chase Sapphire"
AMEX = "Liabilities:Credit Card:Business Amex"
MORTGAGE = "Liabilities:Loans:Mortgage"
AUTO_LOAN = "Liabilities:Loans:Auto Loan"
AP = "Liabilities:Accounts Payable"

OPENING = "Equity:Opening Balances"
OWNER_CONTRIB = "Equity:Owner's Contribution"

SALARY = "Income:Salary"
EMPLOYER_MATCH = "Income:Employer Retirement Match"
LLC_REVENUE = "Income:LLC Revenue"
DIVIDENDS = "Income:Investment Income:Dividends"
CAPITAL_GAINS = "Income:Investment Income:Capital Gains"
INTEREST_INCOME = "Income:Investment Income:Interest"
# HSA earnings are tax-exempt — never on the same 1099-INT line as
# Ally's taxable interest (cold audit A6).
HSA_INTEREST = "Income:Investment Income:HSA Interest"
# Quarterly market change on the two retirement plans, valued from a
# broad-market proxy (cold audit B4). Unrealized, never distributed.
RETIREMENT_CHANGE = "Income:Investment Income:Retirement Market Change"
FX_GAIN_LOSS = "Income:Foreign Exchange Gain/Loss"

EXP_MORTGAGE_INT = "Expenses:Interest:Mortgage Interest"
EXP_AUTO_INT = "Expenses:Interest:Auto Loan Interest"
EXP_CC_INT = "Expenses:Interest:Credit Card Interest"
EXP_HOA = "Expenses:Housing:HOA"
EXP_HOUSING_MAINT = "Expenses:Housing:Maintenance"
EXP_HOME_INS = "Expenses:Housing:Insurance"
EXP_FUEL = "Expenses:Auto:Fuel"
# Transit fares and parking meters are transportation, not fuel
# (cold audit C5).
EXP_TRANSPORTATION = "Expenses:Transportation"
EXP_AUTO_INS = "Expenses:Auto:Insurance"
EXP_AUTO_MAINT = "Expenses:Auto:Maintenance"
EXP_GROCERIES = "Expenses:Groceries"
EXP_DINING = "Expenses:Dining"
EXP_ELECTRIC = "Expenses:Utilities:Electric"
EXP_GAS = "Expenses:Utilities:Gas"
EXP_WATER = "Expenses:Utilities:Water"
EXP_INTERNET = "Expenses:Utilities:Internet"
EXP_PHONE = "Expenses:Utilities:Phone"
EXP_HEALTH = "Expenses:Insurance:Health"
EXP_UMBRELLA = "Expenses:Insurance:Umbrella"
EXP_FED = "Expenses:Taxes:Federal"
EXP_SS = "Expenses:Taxes:Social Security"
EXP_MEDICARE = "Expenses:Taxes:Medicare"
# Washington's statutory W-2 deductions (audit A5): Paid Family &
# Medical Leave, the WA Cares long-term-care premium, and the
# employee share of L&I workers' comp. No state income tax.
EXP_WA_PFML = "Expenses:Taxes:WA PFML"
EXP_WA_CARES = "Expenses:Taxes:WA Cares"
EXP_WA_LI = "Expenses:Taxes:WA L&I"
EXP_PROP_TAX = "Expenses:Taxes:Property Tax"
# 1040-ES installments split into their income-tax and SE-tax
# components (audit A3). Both are the OWNER's taxes — a disregarded
# SMLLC pays no income tax — so they live under the household
# ``Expenses:Taxes`` group, not ``Expenses:Business`` (audit D1).
EXP_EST_TAX = "Expenses:Taxes:Estimated Tax Payments"
EXP_SE_TAX = "Expenses:Taxes:Self-Employment Tax"
EXP_SUBSCRIPTIONS = "Expenses:Subscriptions"
EXP_STREAMING = "Expenses:Streaming"
EXP_CLOTHING = "Expenses:Clothing"
EXP_PET_FOOD = "Expenses:Pet:Food"
EXP_PET_VET = "Expenses:Pet:Vet"
EXP_TRAVEL = "Expenses:Travel"
EXP_EDUCATION = "Expenses:Education"
EXP_GIFTS = "Expenses:Gifts"
EXP_CHARITY = "Expenses:Charity"
EXP_CLOUD = "Expenses:Business:Cloud Hosting"
EXP_SOFTWARE = "Expenses:Business:Software"
EXP_COWORKING = "Expenses:Business:Coworking"
EXP_PROF_DEV = "Expenses:Business:Professional Development"
EXP_ACCOUNTING = "Expenses:Business:Accounting"
# Schedule C lines the audit found missing or misfiled (A4, A7, A2).
EXP_OFFICE = "Expenses:Business:Office Supplies"
EXP_EQUIPMENT = "Expenses:Business:Equipment"
EXP_BIZ_TRAVEL = "Expenses:Business:Travel"
EXP_MEALS = "Expenses:Business:Meals"
EXP_CONTRACTOR = "Expenses:Business:Contractor Payments"
EXP_BIZ_TAXES = "Expenses:Business:Taxes & Licenses"
# The business card's interest and fees are Schedule C deductions;
# the personal card's are not — one account for both hides the split
# (cold audit A4).
EXP_BIZ_CARD_FEES = "Expenses:Business:Interest & Card Fees"
EXP_SALES_DISC = "Expenses:Business:Sales Discounts"
EXP_BANK_CHARGES = "Expenses:Bank Charges"
EXP_MISC = "Expenses:Miscellaneous"
EXP_MEDICAL = "Expenses:Medical"
EXP_ENTERTAINMENT = "Expenses:Entertainment"
EXP_PERSONAL_CARE = "Expenses:Personal Care"


# ── Phase 1: Commodities & prices ───────────────────────────────

# Securities: (mnemonic, fullname, namespace, fraction)
SECURITIES = [
    ("VTSAX", "Vanguard Total Stock Market Index Fund Admiral", "FUND", 10000),
    ("VBTLX", "Vanguard Total Bond Market Index Fund Admiral", "FUND", 10000),
    ("AAPL", "Apple Inc.", "NASDAQ", 10000),
    ("MSFT", "Microsoft Corporation", "NASDAQ", 10000),
    ("ETH", "Ethereum", "CRYPTO", 1000000),
]

# EUR (Berlin Digital) + CAD (Nord Analytique) are both live FX surfaces
# with real accounts, invoices, and prices. GBP was historically listed
# here but had zero GBP accounts/transactions — an orphan commodity — so
# it is deliberately omitted.
FOREIGN_CURRENCIES = ["EUR", "CAD"]

# Monthly price points: 1st of every month from 2025-01 through the
# month of THROUGH (computed at import time). Quotes past CACHE_END
# forward-fill the last real close so valuations stay populated to the
# present. Computed via a function so a ``--through`` override that lands
# past today still gets price coverage.
def _price_months(through: date) -> list[tuple[int, int]]:
    end = max(through, CACHE_END)
    return list(_month_iter(date(YEAR, 1, 1), end))


PRICE_MONTHS = _price_months(THROUGH)


def _security_quant(symbol: str) -> Decimal:
    """Quantization step for a security price (USD per share)."""
    if symbol == "ETH":
        return D("0.01")
    return D("0.0001")


def create_book_file(out_path: Path) -> None:
    """Create the SQLite book with USD default + all commodities."""
    if out_path.resolve() == PROTECTED.resolve():
        raise SystemExit(
            "REFUSING to write to the protected book: "
            f"{PROTECTED}. Choose a different --out path."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    book = piecash.create_book(
        sqlite_file=str(out_path),
        currency="USD",
        overwrite=True,
    )
    try:
        # Foreign currencies. ``book.currencies(mnemonic=...)`` has a
        # built-in fallback that auto-creates the ISO 4217 currency.
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
    """Add USD-base prices for securities + FX pairs (real data).

    Two layers: a 1st-of-month snapshot across the timeline, then a final
    point per commodity at its LAST AVAILABLE real quote (capped at
    THROUGH). The closing point means a report dated near the present
    reflects the most recent real close instead of forward-filling the
    1st-of-month value to the end of the horizon.
    """
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    count = 0
    try:
        usd = book.default_currency
        comm_by_mnemonic = {c.mnemonic: c for c in book.commodities}
        seen: set[tuple[str, str]] = set()

        def _add(mnemonic: str, pdate: date, value: Decimal) -> None:
            nonlocal count
            key = (mnemonic, pdate.isoformat())
            if key in seen:  # piecash rejects duplicate commodity+date+currency
                return
            seen.add(key)
            piecash.Price(
                commodity=comm_by_mnemonic[mnemonic], currency=usd,
                date=pdate, value=value, type="last", source="user:market_data",
            )
            count += 1

        # Layer 1: 1st-of-month snapshots, real closes, forward-filled.
        for yr, mo in PRICE_MONTHS:
            pdate = date(yr, mo, 1)
            for sym, _full, _ns, _frac in SECURITIES:
                _add(sym, pdate, MD.security(sym, pdate).quantize(_security_quant(sym)))
            for foreign in FOREIGN_CURRENCIES:
                _add(foreign, pdate, MD.fx(foreign, "USD", pdate).quantize(D("0.0001")))

        # Layer 2: closing point at the last available real quote per
        # commodity (never past THROUGH). This is the price reports walk to
        # at the present edge of the book — the actual most-recent close,
        # not the 1st-of-month value carried over.
        for sym, _full, _ns, _frac in SECURITIES:
            asof = THROUGH  # §5: closing point AT the horizon (value forward-fills)
            _add(sym, asof, MD.security(sym, asof).quantize(_security_quant(sym)))
        for foreign in FOREIGN_CURRENCIES:
            asof = THROUGH
            _add(foreign, asof, MD.fx(foreign, "USD", asof).quantize(D("0.0001")))

        book.save()
    finally:
        book.close()
    return count


def add_event_prices(out_path: Path, events: list[tuple[str, date]]) -> int:
    """Add real prices on specific event dates (trades, invoice settle).

    Each ``(symbol_or_currency, when)`` gets a real quote on that exact
    date so cross-currency posts/pays find a fresh rate and lot-gain
    calculations have an on-date market price.
    """
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    count = 0
    try:
        usd = book.default_currency
        comm_by_mnemonic = {c.mnemonic: c for c in book.commodities}
        sec_syms = {s[0] for s in SECURITIES}
        # Skip (commodity, date) pairs that already have a price on file —
        # e.g. monthly points laid down by add_prices, or DCA buys on the
        # 1st. piecash rejects a duplicate commodity+date+currency price.
        seen: set[tuple[str, str]] = set()
        for p in book.prices:
            seen.add((p.commodity.mnemonic, p.date.date().isoformat()
                      if hasattr(p.date, "date") else p.date.isoformat()))
        for sym, when in events:
            key = (sym, when.isoformat())
            if key in seen:
                continue
            seen.add(key)
            comm = comm_by_mnemonic[sym]
            if sym in sec_syms:
                value = MD.security(sym, when).quantize(_security_quant(sym))
            else:
                value = MD.fx(sym, "USD", when).quantize(D("0.0001"))
            piecash.Price(
                commodity=comm, currency=usd, date=when,
                value=value, type="last", source="user:market_data",
            )
            count += 1
        book.save()
    finally:
        book.close()
    return count


# ── Phase 2: Chart of accounts ──────────────────────────────────

# (name, type, parent, commodity, namespace, placeholder)
#
# Every leaf here is FED by a generator stream (audit B18: an empty
# account is chart clutter a reviewer trips on). Retirement accounts
# are ASSET-typed so the reconciliation posture pass skips them (no
# statement to tie) while the balance sheet still values them.
ACCOUNTS = [
    # Assets
    ("Assets", "ASSET", None, "USD", "CURRENCY", True),
    ("Current Assets", "ASSET", "Assets", "USD", "CURRENCY", True),
    ("Checking Account", "BANK", "Assets:Current Assets", "USD", "CURRENCY", False),
    ("Savings Account", "BANK", "Assets:Current Assets", "USD", "CURRENCY", False),
    ("Cash", "CASH", "Assets:Current Assets", "USD", "CURRENCY", False),
    ("Cascade Code LLC Checking", "BANK", "Assets:Current Assets", "USD", "CURRENCY", False),
    ("Receivables", "ASSET", "Assets", "USD", "CURRENCY", True),
    ("Accounts Receivable", "RECEIVABLE", "Assets", "USD", "CURRENCY", False),
    ("Accounts Receivable EUR", "RECEIVABLE", "Assets:Receivables", "EUR", "CURRENCY", False),
    ("Accounts Receivable CAD", "RECEIVABLE", "Assets:Receivables", "CAD", "CURRENCY", False),
    ("Investments", "ASSET", "Assets", "USD", "CURRENCY", True),
    ("Brokerage", "ASSET", "Assets:Investments", "USD", "CURRENCY", True),
    ("VTSAX", "MUTUAL", "Assets:Investments:Brokerage", "VTSAX", "FUND", False),
    ("VBTLX", "MUTUAL", "Assets:Investments:Brokerage", "VBTLX", "FUND", False),
    ("AAPL", "STOCK", "Assets:Investments:Brokerage", "AAPL", "NASDAQ", False),
    ("MSFT", "STOCK", "Assets:Investments:Brokerage", "MSFT", "NASDAQ", False),
    ("Coinbase", "ASSET", "Assets:Investments", "USD", "CURRENCY", True),
    ("ETH", "STOCK", "Assets:Investments:Coinbase", "ETH", "CRYPTO", False),
    ("HSA", "BANK", "Assets:Investments", "USD", "CURRENCY", False),
    ("Retirement", "ASSET", "Assets", "USD", "CURRENCY", True),
    ("UWRP 403(b)", "ASSET", "Assets:Retirement", "USD", "CURRENCY", False),
    ("Solo 401(k)", "ASSET", "Assets:Retirement", "USD", "CURRENCY", False),
    ("Fixed Assets", "ASSET", "Assets", "USD", "CURRENCY", True),
    ("Condo", "ASSET", "Assets:Fixed Assets", "USD", "CURRENCY", False),
    ("Vehicle", "ASSET", "Assets:Fixed Assets", "USD", "CURRENCY", False),
    # Liabilities
    ("Liabilities", "LIABILITY", None, "USD", "CURRENCY", True),
    ("Credit Card", "LIABILITY", "Liabilities", "USD", "CURRENCY", True),
    ("Chase Sapphire", "CREDIT", "Liabilities:Credit Card", "USD", "CURRENCY", False),
    ("Business Amex", "CREDIT", "Liabilities:Credit Card", "USD", "CURRENCY", False),
    ("Loans", "LIABILITY", "Liabilities", "USD", "CURRENCY", True),
    ("Mortgage", "LIABILITY", "Liabilities:Loans", "USD", "CURRENCY", False),
    ("Auto Loan", "LIABILITY", "Liabilities:Loans", "USD", "CURRENCY", False),
    ("Accounts Payable", "PAYABLE", "Liabilities", "USD", "CURRENCY", False),
    # Income
    ("Income", "INCOME", None, "USD", "CURRENCY", True),
    ("Salary", "INCOME", "Income", "USD", "CURRENCY", False),
    ("Employer Retirement Match", "INCOME", "Income", "USD", "CURRENCY", False),
    ("LLC Revenue", "INCOME", "Income", "USD", "CURRENCY", False),
    ("Investment Income", "INCOME", "Income", "USD", "CURRENCY", True),
    ("Dividends", "INCOME", "Income:Investment Income", "USD", "CURRENCY", False),
    ("Capital Gains", "INCOME", "Income:Investment Income", "USD", "CURRENCY", False),
    ("Interest", "INCOME", "Income:Investment Income", "USD", "CURRENCY", False),
    ("HSA Interest", "INCOME", "Income:Investment Income", "USD", "CURRENCY", False),
    ("Retirement Market Change", "INCOME", "Income:Investment Income", "USD",
     "CURRENCY", False),
    # Income:Foreign Exchange Gain/Loss is auto-created by pay_invoice; create
    # it up front so it always exists for direct FX transactions too.
    ("Foreign Exchange Gain/Loss", "INCOME", "Income", "USD", "CURRENCY", False),
    # Expenses
    ("Expenses", "EXPENSE", None, "USD", "CURRENCY", True),
    ("Housing", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("HOA", "EXPENSE", "Expenses:Housing", "USD", "CURRENCY", False),
    ("Insurance", "EXPENSE", "Expenses:Housing", "USD", "CURRENCY", False),
    ("Maintenance", "EXPENSE", "Expenses:Housing", "USD", "CURRENCY", False),
    ("Auto", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("Fuel", "EXPENSE", "Expenses:Auto", "USD", "CURRENCY", False),
    ("Insurance", "EXPENSE", "Expenses:Auto", "USD", "CURRENCY", False),
    ("Maintenance", "EXPENSE", "Expenses:Auto", "USD", "CURRENCY", False),
    ("Transportation", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Groceries", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Dining", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Utilities", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("Electric", "EXPENSE", "Expenses:Utilities", "USD", "CURRENCY", False),
    ("Gas", "EXPENSE", "Expenses:Utilities", "USD", "CURRENCY", False),
    ("Water", "EXPENSE", "Expenses:Utilities", "USD", "CURRENCY", False),
    ("Internet", "EXPENSE", "Expenses:Utilities", "USD", "CURRENCY", False),
    ("Phone", "EXPENSE", "Expenses:Utilities", "USD", "CURRENCY", False),
    ("Insurance", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("Health", "EXPENSE", "Expenses:Insurance", "USD", "CURRENCY", False),
    ("Umbrella", "EXPENSE", "Expenses:Insurance", "USD", "CURRENCY", False),
    ("Medical", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Taxes", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("Federal", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Social Security", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Medicare", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("WA PFML", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("WA Cares", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("WA L&I", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Property Tax", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Self-Employment Tax", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Estimated Tax Payments", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Subscriptions", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Streaming", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Clothing", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Pet", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("Food", "EXPENSE", "Expenses:Pet", "USD", "CURRENCY", False),
    ("Vet", "EXPENSE", "Expenses:Pet", "USD", "CURRENCY", False),
    ("Travel", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Education", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Gifts", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Charity", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Entertainment", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Personal Care", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Business", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("Cloud Hosting", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Software", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Coworking", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Professional Development", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Accounting", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Office Supplies", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Equipment", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Travel", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Meals", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Contractor Payments", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Taxes & Licenses", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Interest & Card Fees", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Sales Discounts", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Interest", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("Credit Card Interest", "EXPENSE", "Expenses:Interest", "USD", "CURRENCY", False),
    ("Mortgage Interest", "EXPENSE", "Expenses:Interest", "USD", "CURRENCY", False),
    ("Auto Loan Interest", "EXPENSE", "Expenses:Interest", "USD", "CURRENCY", False),
    ("Bank Charges", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Miscellaneous", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    # Equity
    ("Equity", "EQUITY", None, "USD", "CURRENCY", True),
    ("Opening Balances", "EQUITY", "Equity", "USD", "CURRENCY", False),
    # Owner equity for the LLC (audit B3). In this combined household
    # book an owner's draw is a plain transfer, LLC Checking → Checking
    # (cold audit B3: an equity clearing leg that always nets to zero
    # carries no information). The draws are visible in the LLC
    # register as the month-end transfers out; the balance sheet ties
    # by construction.
    ("Owner's Contribution", "EQUITY", "Equity", "USD", "CURRENCY", False),
]


def create_accounts(out_path: Path) -> int:
    """Create the full chart of accounts directly via piecash."""
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    count = 0
    try:
        comm_by_key = {}
        for c in book.commodities:
            comm_by_key[(c.namespace, c.mnemonic)] = c
        usd = book.default_currency
        comm_by_key[("CURRENCY", "USD")] = usd

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
    book.set_account_slot(CHASE, "apr", "21.49")
    book.set_account_slot(CHASE, "credit_limit", "12000")
    book.set_account_slot(CHASE, "statement_close_day", "15")
    book.set_account_slot(AMEX, "apr", "24.49")
    book.set_account_slot(AMEX, "credit_limit", "20000")
    book.set_account_slot(AMEX, "statement_close_day", "22")
    book.set_account_slot(MORTGAGE, "apr", "6.25")
    book.set_account_slot(AUTO_LOAN, "apr", "5.49")
    # Loans opt out of the reconciliation surface — no statement
    # exists to reconcile against (bookkeeper review §1).
    book.set_account_slot(MORTGAGE, "no_reconcile", "true")
    book.set_account_slot(AUTO_LOAN, "no_reconcile", "true")


# ── Phase 3: Opening balances + investment lots ─────────────────

# (account_path, balance_usd)  — opening balances via equity offset.
OPENING_BALANCES = [
    (CHECKING, D("14500")),
    (SAVINGS, D("22000")),
    (CASH, D("350")),
    # The LLC pre-exists the book (its card opens with a balance and
    # January bills arrive), so its bank account opens with one too
    # (cold audit C9); the January owner's contribution below is a
    # working-capital top-up ahead of the Q1 receivables lag.
    (LLC_CHECKING, D("9800")),
    (HSA, D("4800")),
    (UWRP, D("38400")),           # Robin's UW plan, accumulated pre-2025
    (MORTGAGE, D("-385000")),
    (AUTO_LOAN, D("-18500")),
    (CHASE, D("-2340")),
    (AMEX, D("-1890")),
    (CONDO, D("475000")),
    (VEHICLE, D("28000")),
]

# The owner's January top-up of the LLC's working capital — booked
# against Owner's Contribution, because that is what it is: the
# owner's money put into the business (audit B3).
LLC_OPENING = D("6500")

# (account, shares, cost_basis_usd, lot_title, acquired)
#
# Acquisition dates on the opening lots (audit B14) so a sale's
# short- vs long-term character can be read off the lot. VBTLX's
# basis is its 2023–24 accumulation cost (~$9.62/sh) — the fund
# traded $9.4–$10.2 across that window — so the December 2025
# rebalance sale at the real $9.76 close realizes a GAIN and no
# §1091 wash-sale question arises (audit A1). Cost bases are
# ~the real closes of the acquisition dates.
OPENING_LOTS = [
    (VTSAX, D("180.0000"), D("21600"), "VTSAX core position",
     "2022-08 → 2024-06 (accumulated)"),
    (VBTLX, D("500.0000"), D("4810"), "VBTLX bond allocation",
     "2023-05 → 2024-09 (accumulated)"),
    (AAPL, D("25.0000"), D("4750"), "AAPL 2023 purchase", "2023-06-12"),
    (MSFT, D("15.0000"), D("5700"), "MSFT 2024 purchase", "2024-03-14"),
    (ETH, D("2.500000"), D("6000"), "ETH 2024 purchase", "2024-02-20"),
]


def opening_balances(out_path: Path) -> None:
    """Post opening balances (one balanced transaction) + investment lots."""
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    try:
        usd = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        jan1 = date(YEAR, 1, 1)

        # Cash/liability opening balances — single balanced transaction
        # against Equity:Opening Balances.
        splits = []
        total = D("0")
        for path, bal in OPENING_BALANCES:
            splits.append(piecash.Split(account=acct[path], value=bal))
            total += bal
        splits.append(piecash.Split(account=acct[OPENING], value=-total))
        piecash.Transaction(
            currency=usd,
            description="Opening Balances",
            post_date=jan1,
            enter_date=_enter_stamp(jan1),
            splits=splits,
        )

        # The owner's working-capital top-up.
        piecash.Transaction(
            currency=usd,
            description="Owner's contribution — Cascade Code LLC working capital",
            notes="January working-capital top-up ahead of the Q1 "
                  "receivables lag",
            post_date=date(YEAR, 1, 2),
            enter_date=_enter_stamp(date(YEAR, 1, 2)),
            splits=[
                piecash.Split(account=acct[LLC_CHECKING], value=LLC_OPENING),
                piecash.Split(account=acct[OWNER_CONTRIB], value=-LLC_OPENING),
            ],
        )

        # Investment opening lots: buy each holding from equity at cost.
        for path, units, cost, title, acquired in OPENING_LOTS:
            inv_acct = acct[path]
            per_share = (cost / units).quantize(D("0.0001"))
            lot = piecash.Lot(
                title=title, account=inv_acct,
                notes=f"Opening position — acquired {acquired}; "
                      f"{units} sh, basis ${cost} (${per_share}/sh)",
                is_closed=0,
            )
            inv_split = piecash.Split(
                account=inv_acct, value=cost, quantity=units,
            )
            eq_split = piecash.Split(account=acct[OPENING], value=-cost)
            piecash.Transaction(
                currency=usd,
                description=f"Opening position — {title}",
                notes=f"Acquired {acquired}; {units} sh at ${per_share}/sh",
                post_date=jan1,
                enter_date=_enter_stamp(jan1),
                splits=[inv_split, eq_split],
            )
            inv_split.lot = lot

        book.save()
    finally:
        book.close()


# ── Generic bulk transaction writer (piecash, fast, no audit) ───

# Entry timestamps (cold audit C3 of 2026-09-11 / item 5 of the 2026-09-17
# round): a row's ``enter_date`` is the moment it was keyed in, and a
# book whose every row was "entered" at the build moment is a generation
# artifact. piecash defaults ``enter_date`` to ``datetime.now()``; every
# writer here stamps it explicitly as the POST DAY at noon UTC plus a
# few seconds of write order (so same-day rows keep the order they were
# written in, which is what the server's listing sort reads). Server-API
# writes (documents, the policy engine, edge cases) can't be stamped at
# the call site; ``continuation.stamp_enter_dates`` normalizes those in
# one pass at the end of the build.
_ENTER_SEQ = 0


def _enter_stamp(post: date) -> datetime:
    """Deterministic ``enter_date`` for a row posted on ``post``."""
    global _ENTER_SEQ
    _ENTER_SEQ += 1
    base = datetime(post.year, post.month, post.day, 12, 0, 0,
                    tzinfo=timezone.utc)
    # Stay inside the post day in UTC: 12:00 + at most ~9 hours.
    return base + timedelta(seconds=_ENTER_SEQ % 32400)


def write_bulk(out_path: Path, txns: list[dict]) -> int:
    """Write a list of {description, date, currency, notes, splits:[(path, value[, qty])]}.

    Each split tuple is (account_path, value) for same-currency or
    (account_path, value, quantity) when the account commodity differs
    from the transaction currency. ``currency`` defaults to USD;
    ``notes`` (optional) is the transaction's double-line note — the
    statement-style descriptions keep the payee clean and put the
    interpretation here (audit C6). ``enter_date`` is the post day
    (``_enter_stamp``), never the build moment.
    """
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    count = 0
    try:
        usd = book.default_currency
        comm_by = {c.mnemonic: c for c in book.commodities}
        acct = {a.fullname: a for a in book.accounts}
        for t in txns:
            cur = comm_by[t.get("currency", "USD")]
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
                notes=t.get("notes") or "",
                post_date=t["date"],
                enter_date=_enter_stamp(t["date"]),
                splits=splits,
            )
            count += 1
        book.save()
    finally:
        book.close()
    return count


def _days_in_month(year: int, month: int) -> int:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (nxt - date(year, month, 1)).days


# ── Phase 4: Scheduled-transaction templates ────────────────────

def create_scheduled_templates(book: GnuCashBook) -> int:
    """Create the SX templates so scheduled-transaction tools have data.

    Disabled at the end of the build to avoid the GnuCash GUI 'Since Last
    Run' barrage. The actual recurring activity is generated directly in
    Phase 5 for speed and amortization-split fidelity.
    """
    count = 0

    def sx(name, description, splits, frequency, start_date="2025-01-15"):
        nonlocal count
        book.create_scheduled_transaction(
            name=name, description=description, splits=splits,
            start_date=start_date, frequency=frequency, enabled=True,
        )
        count += 1

    # Robin's biweekly paycheck — the template carries the CURRENT
    # base-pay recipe (post-raise, audit B15), the same lines the
    # instantiated stubs carry (audit A5/A6/B7).
    base_when = date(YEAR + 1, 1, 9)
    tmpl = _paycheck_splits(_base_gross(base_when), D("0"), base_when)
    sx("Robin's Paycheck", "UW Medical biweekly paycheck", [
        {"account": path, "amount": str(amt)} for path, amt in tmpl
    ], "biweekly", start_date="2025-01-10")

    sx("Mortgage Payment", "Capitol Hill condo mortgage", [
        {"account": CHECKING, "amount": "-2485.00"},
        {"account": EXP_MORTGAGE_INT, "amount": "2006.25"},
        {"account": MORTGAGE, "amount": "478.75"},
    ], "monthly")

    sx("Auto Loan Payment", "Subaru Outback auto loan", [
        {"account": CHECKING, "amount": "-365.00"},
        {"account": EXP_AUTO_INT, "amount": "84.63"},
        {"account": AUTO_LOAN, "amount": "280.37"},
    ], "monthly")

    simple_monthly = [
        ("HOA Dues", "Condo HOA dues", CHECKING, EXP_HOA, "425.00"),
        ("Electric", "Seattle City Light", CHECKING, EXP_ELECTRIC, "95.00"),
        ("Gas Utility", "Puget Sound Energy", CHECKING, EXP_GAS, "65.00"),
        ("Water/Sewer", "Seattle Public Utilities", CHECKING, EXP_WATER, "55.00"),
        ("Internet", "Comcast", CHECKING, EXP_INTERNET, "79.99"),
        ("Phone", "T-Mobile", CHECKING, EXP_PHONE, "140.00"),
        ("Streaming Bundle", "Streaming subscriptions", CHECKING, EXP_STREAMING, "45.97"),
        ("Auto Insurance (PEMCO)", "PEMCO auto policy", CHECKING, EXP_AUTO_INS,
         str(AUTO_INSURANCE)),
        ("Condo Insurance (HO-6)", "Safeco HO-6 condo policy", CHECKING,
         EXP_HOME_INS, str(HO6_INSURANCE)),
        ("Cloud Hosting (AWS)", "AWS", AMEX, EXP_CLOUD, "125.00"),
        ("Coworking (WeWork)", "WeWork", AMEX, EXP_COWORKING, "250.00"),
        ("Pet Food (Chewy)", "Chewy auto-ship", CHECKING, EXP_PET_FOOD, "48.00"),
    ]
    for name, desc, src, dst, amt in simple_monthly:
        sx(name, desc, [
            {"account": src, "amount": f"-{amt}"},
            {"account": dst, "amount": amt},
        ], "monthly")

    # Quarterly. The 1040-ES template carries a representative
    # installment split into its income-tax and SE-tax components;
    # the instantiated payments are sized from annualized SE income
    # (audit A3).
    sx("Estimated Tax Payment", "IRS USATAXPYMT — Form 1040-ES", [
        {"account": CHECKING, "amount": "-11000.00"},
        {"account": EXP_EST_TAX, "amount": "5600.00"},
        {"account": EXP_SE_TAX, "amount": "5400.00"},
    ], "quarterly", start_date="2025-04-15")
    sx("Umbrella Insurance", "Umbrella policy premium", [
        {"account": CHECKING, "amount": "-125.00"},
        {"account": EXP_UMBRELLA, "amount": "125.00"},
    ], "quarterly")
    sx("WA B&O Tax", "WA DOR — B&O excise return (Cascade Code LLC)", [
        {"account": LLC_CHECKING, "amount": "-600.00"},
        {"account": EXP_BIZ_TAXES, "amount": "600.00"},
    ], "quarterly", start_date="2025-04-30")

    # Yearly. King County bills to the cent and re-levies every year
    # (audit B12); the template carries the current year's half.
    half = str(_property_tax_half(base_when.year))
    sx("Property Tax (1st Half)", "King County property tax", [
        {"account": CHECKING, "amount": f"-{half}"},
        {"account": EXP_PROP_TAX, "amount": half},
    ], "yearly", start_date="2025-04-30")
    sx("Property Tax (2nd Half)", "King County property tax", [
        {"account": CHECKING, "amount": f"-{half}"},
        {"account": EXP_PROP_TAX, "amount": half},
    ], "yearly", start_date="2025-10-31")

    return count


# ── Phase 5: Recurring instantiations (direct, with amortization) ─

BASELINE_GROSS = D("3269.23")
# UW/SEIU step: +3.5% from the first 2026 check (audit B15).
RAISE_DATE = date(YEAR + 1, 1, 9)
RAISE_FACTOR = D("1.035")
FIXED_HEALTH = D("145.00")
FIXED_HSA = D("44.14")
BASE_HOURS = D("80")            # biweekly hours behind the base gross

# US payroll withholding, rate-based so every deduction TRACKS gross — an
# overtime paycheck withholds more than a base paycheck, and no two
# different-gross paychecks are identical.
#
# Order of operations (audit A6): the §125 pre-tax deductions (health
# premium, HSA) come off BEFORE FICA and income-tax withholding; the
# 403(b) deferral comes off before income tax only (it is FICA-taxable).
# FICA is exact statutory: Social Security 6.2%, Medicare 1.45%.
# Federal withholding is a two-step marginal table on the per-period
# taxable wage (12% to a threshold, 22% above it — audit B16: overtime
# paid in the regular check is aggregated with regular wages, not
# withheld at the supplemental rate), so it is genuinely non-proportional
# and clearly larger in overtime months.
#
# Washington has no income tax but three statutory employee premiums
# (audit A5): Paid Family & Medical Leave (the employee share of a
# premium ESD re-sets every year — cold audit A5: 2025 0.92% × 71.43%
# = 0.657%; 2026 1.13% × 71.52% = 0.808%; later years carry the last
# published rate until the next one is), the WA Cares Fund (0.58%),
# and the employee share of L&I workers' comp (per hour worked).
SS_RATE = D("0.062")            # Social Security employee share
MED_RATE = D("0.0145")          # Medicare employee share
FED_LOW_RATE = D("0.12")        # marginal withholding to the threshold
FED_HIGH_RATE = D("0.22")       # marginal withholding above it
FED_THRESHOLD = D("2400.00")    # per-period taxable-wage step
PFML_EMPLOYEE_RATE = {2025: D("0.00657"), 2026: D("0.00808")}
WA_CARES_RATE = D("0.0058")
LI_HOURLY_EMPLOYEE = D("0.08")      # $/hour, employee share (health care class)
UWRP_DEFERRAL_RATE = D("0.07")      # audit B7: 7% pre-tax + 100% match
UWRP_MATCH_RATE = D("0.07")


def _base_gross(when: date) -> Decimal:
    """Robin's base biweekly gross on ``when`` (pre- or post-raise)."""
    if when >= RAISE_DATE:
        return (BASELINE_GROSS * RAISE_FACTOR).quantize(D("0.01"))
    return BASELINE_GROSS


def _pfml_rate(when: date) -> Decimal:
    """The employee PFML share in force on ``when`` (year-keyed)."""
    latest = max(PFML_EMPLOYEE_RATE)
    return PFML_EMPLOYEE_RATE.get(when.year, PFML_EMPLOYEE_RATE[latest])


def _paychecks(through: date) -> list[tuple[date, Decimal, Decimal]]:
    """Robin's biweekly checks from 2025-01-10 through ``through`` as
    (pay date, gross, overtime). One RNG stream, so the tax plan and
    the payroll stream read the same checks."""
    rng = random.Random(SEED + 5)
    out: list[tuple[date, Decimal, Decimal]] = []
    d = date(YEAR, 1, 10)
    i = 0
    while d <= through:
        overtime = D("0")
        if i > 0 and i % rng.choice([3, 4]) == 0:
            overtime = D(str(rng.randint(200, 400)))
        out.append((d, _base_gross(d) + overtime, overtime))
        d += timedelta(days=14)
        i += 1
    return out


def _paycheck_splits(gross: Decimal, overtime: Decimal, when: date):
    base = _base_gross(when)
    hourly = base / BASE_HOURS
    hours = BASE_HOURS + (overtime / hourly if overtime else D("0"))

    deferral = (gross * UWRP_DEFERRAL_RATE).quantize(D("0.01"))
    match = (gross * UWRP_MATCH_RATE).quantize(D("0.01"))
    fica_wages = gross - FIXED_HEALTH - FIXED_HSA          # §125 excluded
    ss = (fica_wages * SS_RATE).quantize(D("0.01"))
    med = (fica_wages * MED_RATE).quantize(D("0.01"))
    taxable = fica_wages - deferral                        # 403(b) excluded
    fed = (min(taxable, FED_THRESHOLD) * FED_LOW_RATE
           + max(D("0"), taxable - FED_THRESHOLD) * FED_HIGH_RATE
           ).quantize(D("0.01"))
    pfml = (gross * _pfml_rate(when)).quantize(D("0.01"))
    cares = (gross * WA_CARES_RATE).quantize(D("0.01"))
    li = (hours * LI_HOURLY_EMPLOYEE).quantize(D("0.01"))
    checking = (gross - fed - ss - med - pfml - cares - li
                - FIXED_HEALTH - FIXED_HSA - deferral)
    return [
        (SALARY, -gross),
        (CHECKING, checking),
        (EXP_FED, fed),
        (EXP_SS, ss),
        (EXP_MEDICARE, med),
        (EXP_WA_PFML, pfml),
        (EXP_WA_CARES, cares),
        (EXP_WA_LI, li),
        (EXP_HEALTH, FIXED_HEALTH),
        (HSA, FIXED_HSA),
        (UWRP, deferral),
        (UWRP, match),
        (EMPLOYER_MATCH, -match),
    ]


def _amortized_split(rate_annual: Decimal, payment: Decimal,
                     balance: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """One amortization period: returns (interest, principal, new_balance).

    ``balance`` is the outstanding principal as a positive Decimal.
    """
    interest = (balance * rate_annual / D("12")).quantize(D("0.01"))
    principal = (payment - interest).quantize(D("0.01"))
    if principal > balance:  # final stub payment
        principal = balance
    return interest, principal, balance - principal


# King County bills to the cent and re-levies every year (audit B12):
# ~0.98% of a $475K assessment in 2025, +4.1%/yr.
PROPERTY_TAX_2025_HALF = D("2318.47")
PROPERTY_TAX_GROWTH = D("1.041")


def _property_tax_half(year: int) -> Decimal:
    amt = PROPERTY_TAX_2025_HALF
    for _ in range(year - YEAR):
        amt = (amt * PROPERTY_TAX_GROWTH).quantize(D("0.01"))
    return amt


# Fixed insurance premiums the umbrella policy sits on (audit B5).
AUTO_INSURANCE = D("142.00")
HO6_INSURANCE = D("58.00")

# ── Owner-level tax sizing (audit A3; cold audit A2) ──
# One household model, used three times: the 1040-ES installments
# project the year from annualized figures (Form 2210 Schedule AI
# style), the April settlement computes the finished year on the
# same model, and the stability verifier checks the book against it.
# Married filing jointly: Robin's W-2 (Box 1 = gross − 403(b) − §125)
# plus Alex's Schedule C net, less ½ SE tax, the Solo 401(k) employer
# contribution, the QBI deduction and the standard deduction, through
# the bracket table; SE tax at 92.35% × 15.3% with the Social Security
# wage base; Schedule D long-term gains at 15%. Each installment pays
# up to the cumulative 90% safe-harbor fraction of the tax NOT covered
# by Robin's withholding; the settlement is liability − prepayments,
# signed — a refund posts as an ``IRS TREAS 310`` deposit.
SE_TAX_RATE = D("0.153")
SE_TAXABLE_SHARE = D("0.9235")
SE_SS_RATE = D("0.124")
SE_MEDICARE_RATE = D("0.029")
LTCG_RATE = D("0.15")
QBI_RATE = D("0.20")
BIZ_EXPENSE_MONTHLY = D("3200")     # Alex's expense run-rate assumption
EST_CUMULATIVE = [D("0.225"), D("0.45"), D("0.675"), D("0.90")]
EST_INSTALLMENTS = [(4, 15, 3), (6, 15, 5), (9, 15, 8), (1, 15, 12)]
# (due month, due day, months of the tax year covered); Q4 pays in Jan.
# MFJ standard deduction and bracket ceilings (Rev. Proc. 2024-40 as
# amended by P.L. 119-21 for 2025; Rev. Proc. 2025-32 for 2026). Later
# years index the last published table at 2.5%/yr, rounded to $50 —
# the plan's own projection, marked as such in the notes.
FED_MFJ_TABLES = {
    2025: (D("31500"), [(D("23850"), D("0.10")), (D("96950"), D("0.12")),
                        (D("206700"), D("0.22")), (D("394600"), D("0.24")),
                        (D("501050"), D("0.32")), (D("751600"), D("0.35")),
                        (None, D("0.37"))]),
    2026: (D("32200"), [(D("24800"), D("0.10")), (D("100800"), D("0.12")),
                        (D("211400"), D("0.22")), (D("403550"), D("0.24")),
                        (D("512450"), D("0.32")), (D("768700"), D("0.35")),
                        (None, D("0.37"))]),
}
SS_WAGE_BASE = {2025: D("176100"), 2026: D("184500")}
TAX_INDEX_FACTOR = D("1.025")

# Washington B&O (audit A7; cold audit A1/B1): Service & Other
# Activities at 1.5% of the WA-APPORTIONED gross — services are sourced
# to the customer's location (RCW 82.04.462), so only the Seattle
# clients' receipts are Washington's; filed quarterly. Seattle's own
# B&O (SMC 5.45) tests its $100K exemption on worldwide gross and,
# once over it, taxes the whole Seattle-apportioned base at 0.427%
# (the threshold is an exemption, not a deduction); it settles annually
# with the license renewal. The SOS annual report is a flat $60 in the
# LLC's anniversary month.
WA_CLIENTS = {"emerald", "sound_transit"}
WA_BO_RATE = D("0.015")
SEATTLE_BO_RATE = D("0.00427")
SEATTLE_BO_THRESHOLD = D("100000")
SEATTLE_LICENSE_FEE = D("115.00")
WA_SOS_ANNUAL_REPORT = D("60.00")
LLC_ANNIVERSARY_MONTH = 3

# Solo 401(k) employer contribution (audit B7) — paid from the LLC in
# December out of the reserve the draw policy accrues all year.
SOLO_401K_CONTRIBUTION = D("20000.00")
SOLO_401K_DAY = (12, 20)

# Interest on idle money (audit B6): the HSA's cash sweep.
HSA_APY = D("0.005")


def _receipts_by_month(through: date,
                       wa_only: bool = False) -> dict[tuple[int, int], Decimal]:
    """Gross receipts (USD, at the invoice's open-date rate) per calendar
    month, from the same invoice plan the business phase executes.
    ``wa_only`` keeps the Washington-sourced clients' receipts only
    (the B&O apportioned base)."""
    out: dict[tuple[int, int], Decimal] = {}
    for inv in _all_invoice_plans(through):
        if wa_only and inv["client"] not in WA_CLIENTS:
            continue
        key = (inv["date_open"].year, inv["date_open"].month)
        out[key] = out.get(key, D("0")) + inv["usd_value"]
    return out


def _fed_table(tax_year: int) -> tuple[Decimal, list]:
    """(standard deduction, brackets) for ``tax_year``, indexed past the
    last published table."""
    if tax_year in FED_MFJ_TABLES:
        return FED_MFJ_TABLES[tax_year]
    last = max(FED_MFJ_TABLES)
    factor = TAX_INDEX_FACTOR ** (tax_year - last)
    std, brackets = FED_MFJ_TABLES[last]
    idx = lambda v: (v * factor / 50).to_integral_value(  # noqa: E731
        rounding="ROUND_HALF_UP") * 50
    return idx(std), [(None if top is None else idx(top), rate)
                      for top, rate in brackets]


def _ss_wage_base(tax_year: int) -> Decimal:
    if tax_year in SS_WAGE_BASE:
        return SS_WAGE_BASE[tax_year]
    last = max(SS_WAGE_BASE)
    return (SS_WAGE_BASE[last] * D("1.035") ** (tax_year - last) / 300
            ).to_integral_value(rounding="ROUND_HALF_UP") * 300


def _bracket_tax(taxable: Decimal, brackets: list) -> Decimal:
    tax, floor = D("0"), D("0")
    for top, rate in brackets:
        if top is None or taxable <= top:
            tax += (taxable - floor) * rate
            break
        tax += (top - floor) * rate
        floor = top
    return tax


def _household_tax(tax_year: int, se_net: Decimal, wages_box1: Decimal,
                   ltcg: Decimal = D("0")) -> dict:
    """The MFJ liability on the household's figures: ``{"se", "income",
    "total", "taxable", ...}`` (all Decimal, cents)."""
    se_base = max(D("0"), se_net) * SE_TAXABLE_SHARE
    se = (min(se_base, _ss_wage_base(tax_year)) * SE_SS_RATE
          + se_base * SE_MEDICARE_RATE).quantize(D("0.01"))
    solo_401k = SOLO_401K_CONTRIBUTION if se_net > SOLO_401K_CONTRIBUTION else D("0")
    qbi_base = max(D("0"), se_net - se / 2 - solo_401k)
    qbi = (qbi_base * QBI_RATE).quantize(D("0.01"))
    std, brackets = _fed_table(tax_year)
    agi = wages_box1 + se_net - se / 2 - solo_401k + ltcg
    taxable = max(D("0"), agi - qbi - std)
    ordinary = max(D("0"), taxable - max(D("0"), ltcg))
    income = (_bracket_tax(ordinary, brackets)
              + max(D("0"), min(ltcg, taxable)) * LTCG_RATE).quantize(D("0.01"))
    return {"se": se, "income": income, "total": se + income,
            "taxable": taxable, "qbi": qbi, "solo_401k": solo_401k,
            "std": std}


def _robin_w2(tax_year: int, upto: date | None = None) -> tuple[Decimal, Decimal, int]:
    """(Box 1 wages, federal withholding, checks) on Robin's checks in
    ``tax_year`` dated on or before ``upto`` (whole year when None)."""
    end = date(tax_year, 12, 31)
    if upto is not None:
        end = min(end, upto)
    box1 = withheld = D("0")
    n = 0
    for when, gross, overtime in _paychecks(end):
        if when.year != tax_year:
            continue
        splits: dict[str, Decimal] = {}
        for path, amt in _paycheck_splits(gross, overtime, when):
            splits[path] = splits.get(path, D("0")) + amt
        # Box 1 excludes the §125 premium, the HSA and the employee
        # 403(b) deferral (the employer match is not wages).
        deferral = (gross * UWRP_DEFERRAL_RATE).quantize(D("0.01"))
        box1 += gross - FIXED_HEALTH - FIXED_HSA - deferral
        withheld += splits[EXP_FED]
        n += 1
    return box1.quantize(D("0.01")), withheld.quantize(D("0.01")), n


def _realized_ltcg(tax_year: int, upto: date | None = None) -> Decimal:
    """Schedule D long-term gain from the fixed 2025 trade calendar
    (the only sales in the book), at the real closes, on sales dated
    on or before ``upto``."""
    if tax_year != YEAR:
        return D("0")
    basis = {acct.rsplit(":", 1)[1]: cost / units
             for acct, units, cost, _t, _a in OPENING_LOTS}
    gain = D("0")
    for m, day, action, sym, shares in QUARTERLY_TRADES:
        when = _next_bday(date(YEAR, m, day))
        if action != "sell" or (upto is not None and when > upto):
            continue
        price = MD.security(sym, when).quantize(_security_quant(sym))
        gain += ((shares * price).quantize(D("0.01"))
                 - (shares * basis[sym]).quantize(D("0.01")))
    return gain.quantize(D("0.01"))


def _estimated_tax_plan(through: date) -> list[dict]:
    """Every 1040-ES installment and April balance-due through
    ``through``: dicts with date, description, notes, income_part,
    se_part (whole dollars — people round 1040-ES vouchers)."""
    receipts = _receipts_by_month(through)
    plan: list[dict] = []
    for tax_year in range(YEAR, through.year + 1):
        paid = D("0")
        projection: Decimal | None = None
        for k, (mo, day, n_months) in enumerate(EST_INSTALLMENTS):
            # IRS deadlines roll to the next business day (cold audit C3).
            due = _next_bday(date(tax_year + 1 if mo == 1 else tax_year,
                                  mo, day))
            if due > through:
                break
            covered_through = _clamp_day(tax_year, n_months, 31)
            gross = sum((receipts.get((tax_year, m), D("0"))
                         for m in range(1, n_months + 1)), D("0"))
            net_to_date = gross - BIZ_EXPENSE_MONTHLY * n_months
            annualized = max(D("0"), net_to_date * 12 / n_months)
            # A person revises a projection, not replaces it: blend the
            # fresh annualization with the previous installment's view
            # so one lumpy quarter doesn't whipsaw the voucher.
            projection = (annualized if projection is None
                          else (projection + annualized) / 2)
            annualized = projection.quantize(D("0.01"))
            # Robin's side of the return, annualized the same way from
            # the checks dated inside the covered months.
            box1_ytd, wh_ytd, _n = _robin_w2(tax_year, upto=covered_through)
            scale = D(12) / n_months
            box1_proj = (box1_ytd * scale).quantize(D("0.01"))
            wh_proj = (wh_ytd * scale).quantize(D("0.01"))
            tax = _household_tax(tax_year, annualized, box1_proj,
                                 _realized_ltcg(tax_year, covered_through))
            uncovered = max(D("0"), tax["total"] - wh_proj)
            required = (uncovered * EST_CUMULATIVE[k]).quantize(D("1"))
            installment = max(D("0"), required - paid)
            if installment <= 0:
                continue
            income_uncovered = max(D("0"), tax["income"] - wh_proj)
            se_share = (tax["se"] / (tax["se"] + income_uncovered)
                        if tax["se"] + income_uncovered else D("1"))
            se_part = (installment * se_share).quantize(D("1"))
            plan.append({
                "date": due, "kind": "installment",
                "description": "IRS USATAXPYMT",
                "notes": f"Form 1040-ES {tax_year} Q{k + 1} — "
                         f"annualized net SE income ${annualized:,.0f}; "
                         f"projected household tax ${tax['total']:,.0f} "
                         f"less withholding ${wh_proj:,.0f}, paid to the "
                         f"{EST_CUMULATIVE[k] / D('0.90') * 100:.0f}% mark "
                         f"of the 90% safe harbor",
                "income_part": installment - se_part, "se_part": se_part,
            })
            paid += installment
        # The April settle-up on the finished year: liability on the
        # same model, less everything prepaid, SIGNED (cold audit A2).
        due = _next_bday(date(tax_year + 1, 4, 15))
        if due <= through:
            gross = sum((receipts.get((tax_year, m), D("0"))
                         for m in range(1, 13)), D("0"))
            net = max(D("0"), gross - BIZ_EXPENSE_MONTHLY * 12)
            box1, withheld, _n = _robin_w2(tax_year)
            ltcg = _realized_ltcg(tax_year)
            tax = _household_tax(tax_year, net, box1, ltcg)
            prepaid = withheld + paid
            balance = (tax["total"] - prepaid).quantize(D("1"))
            basis = (f"tax ${tax['total']:,.0f} (income ${tax['income']:,.0f}"
                     f" + SE ${tax['se']:,.0f}) on W-2 wages ${box1:,.0f}, "
                     f"Schedule C net ${net:,.0f}"
                     + (f", Schedule D ${ltcg:,.0f}" if ltcg else "")
                     + f"; prepaid ${prepaid:,.0f} (withholding "
                     f"${withheld:,.0f} + 1040-ES ${paid:,.0f})")
            if balance != 0:
                plan.append({
                    "date": due, "kind": "balance",
                    "description": ("IRS USATAXPYMT" if balance > 0
                                    else "IRS TREAS 310 TAX REF"),
                    "notes": (f"{tax_year} Form 1040 (MFJ) balance due "
                              f"${balance:,.0f}: {basis}"
                              if balance > 0 else
                              f"{tax_year} Form 1040 (MFJ) refund "
                              f"${-balance:,.0f}: {basis}"),
                    "amount": balance,
                })
    return plan


def _federal_liability(tax_year: int, through: date) -> dict | None:
    """The finished year's liability on the plan's model (what the
    stability verifier compares the book against), or None while the
    year is still open."""
    if date(tax_year, 12, 31) > through:
        return None
    receipts = _receipts_by_month(through)
    gross = sum((receipts.get((tax_year, m), D("0")) for m in range(1, 13)),
                D("0"))
    net = max(D("0"), gross - BIZ_EXPENSE_MONTHLY * 12)
    box1, withheld, _n = _robin_w2(tax_year)
    tax = _household_tax(tax_year, net, box1, _realized_ltcg(tax_year))
    tax["withholding"] = withheld
    return tax


def _bo_tax_plan(through: date) -> list[dict]:
    """Quarterly WA DOR B&O returns, the Seattle annual B&O + license
    renewal, and the SOS annual report — dicts with date, description,
    notes, amount. All paid from the LLC account."""
    receipts = _receipts_by_month(through)
    wa_receipts = _receipts_by_month(through, wa_only=True)
    apportion = ("RCW 82.04.462 sources services to the customer's "
                 "location — Emerald Analytics, Sound Transit")
    plan: list[dict] = []
    for yr in range(YEAR, through.year + 1):
        for q in range(1, 5):
            months = range(3 * q - 2, 3 * q + 1)
            gross = sum((receipts.get((yr, m), D("0")) for m in months), D("0"))
            wa_gross = sum((wa_receipts.get((yr, m), D("0")) for m in months),
                           D("0"))
            due_month = 3 * q + 1
            due = _next_bday(date(yr + 1, 1, 31) if due_month == 13
                             else _clamp_day(yr, due_month, 31))
            if due > through or wa_gross <= 0:
                continue
            plan.append({
                "date": due,
                "description": "WA DOR — B&O excise tax",
                "notes": f"Q{q} {yr} combined excise return — service & "
                         f"other activities on ${wa_gross:,.2f} "
                         f"WA-apportioned gross (${gross:,.2f} worldwide; "
                         f"{apportion})",
                "amount": (wa_gross * WA_BO_RATE).quantize(D("0.01")),
            })
        sos = _next_bday(date(yr, LLC_ANNIVERSARY_MONTH, 20))
        if sos <= through:
            plan.append({
                "date": sos,
                "description": "WA Secretary of State — LLC annual report",
                "notes": f"Cascade Code LLC annual report, {yr}",
                "amount": WA_SOS_ANNUAL_REPORT,
            })
        renewal = _next_bday(date(yr, 12, 15))
        if renewal <= through:
            plan.append({
                "date": renewal,
                "description": "City of Seattle — business license renewal",
                "notes": f"{yr + 1} business license tax certificate",
                "amount": SEATTLE_LICENSE_FEE,
            })
        annual = _next_bday(date(yr + 1, 4, 30))
        if annual <= through:
            gross = sum((receipts.get((yr, m), D("0"))
                         for m in range(1, 13)), D("0"))
            base = sum((wa_receipts.get((yr, m), D("0"))
                        for m in range(1, 13)), D("0"))
            # The $100K is an exemption threshold, not a deduction: over
            # it, the rate applies to the whole apportioned base (SMC
            # 5.45.050; cold audit A1).
            if gross > SEATTLE_BO_THRESHOLD and base > 0:
                plan.append({
                    "date": annual,
                    "description": "City of Seattle — B&O tax (annual)",
                    "notes": f"{yr} Seattle B&O — worldwide gross "
                             f"${gross:,.2f} exceeds the "
                             f"${SEATTLE_BO_THRESHOLD:,.0f} exemption "
                             f"threshold; 0.427% service rate on the "
                             f"Seattle-apportioned base ${base:,.2f} "
                             f"(service-income factor, SMC 5.45.081 — "
                             f"Emerald Analytics, Sound Transit)",
                    "amount": (base * SEATTLE_BO_RATE).quantize(D("0.01")),
                })
    return plan


def gen_recurring(through: date) -> list[dict]:
    """Recurring activity from 2025-01-01 through ``through``.

    Paychecks, loan amortization, monthly bills, quarterly/annual
    premiums, property tax, B&O and estimated federal tax all run
    continuously to the present so the book has no "data cliff" — the
    most recent months show realistic income and burn.
    """
    txns: list[dict] = []

    # Biweekly paychecks from Jan 10 2025, overtime ~every 3rd-4th
    # (one stream, shared with the tax plan — ``_paychecks``).
    hsa_contribs: list[tuple[date, Decimal]] = []
    uwrp_contribs: list[tuple[date, Decimal]] = []
    for d, gross, overtime in _paychecks(through):
        desc = "UW Medicine — payroll (Robin)"
        notes = f"Biweekly, gross ${gross:,.2f}"
        if overtime:
            notes += f" incl. ${overtime} overtime"
        if d == RAISE_DATE:
            notes += " — first check at the 3.5% step"
        splits = _paycheck_splits(gross, overtime, d)
        txns.append({"description": desc, "date": d, "notes": notes,
                     "splits": splits})
        hsa_contribs.append((d, FIXED_HSA))
        uwrp_contribs.append((d, sum((amt for path, amt in splits
                                      if path == UWRP), D("0"))))

    # Mortgage (1st) + auto loan (7th), true declining-balance
    # amortization carried forward month over month. Both are ACH
    # debits: a weekend due date posts the next business day (cold
    # audit C3), and the auto loan sits off the 5th (C6).
    mort_bal = D("385000.00")
    mort_pmt = D("2485.00")
    mort_rate = D("0.0625")
    auto_bal = D("18500.00")
    auto_pmt = D("365.00")
    auto_rate = D("0.0549")
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        first = _next_bday(date(yr, m, 1))
        if first <= through and mort_bal > 0:
            m_int, m_pri, mort_bal = _amortized_split(
                mort_rate, mort_pmt, mort_bal)
            txns.append({
                "description": "Mortgage Payment", "date": first,
                "splits": [(CHECKING, -(m_int + m_pri)),
                           (EXP_MORTGAGE_INT, m_int), (MORTGAGE, m_pri)],
            })
        seventh = _next_bday(_clamp_day(yr, m, 7))
        if seventh <= through and auto_bal > 0:
            a_int, a_pri, auto_bal = _amortized_split(
                auto_rate, auto_pmt, auto_bal)
            txns.append({
                "description": "Auto Loan Payment", "date": seventh,
                "splits": [(CHECKING, -(a_int + a_pri)),
                           (EXP_AUTO_INT, a_int), (AUTO_LOAN, a_pri)],
            })

    # Genuinely-fixed monthly bills (contractual / autopay flat rates) — these
    # stay identical every month. Auto + HO-6 premiums are the policies
    # the umbrella requires underneath it (audit B5). Autopay debits
    # roll off weekends; the WeWork membership is a card charge and
    # posts on its calendar day.
    fixed_bills = [
        ("HOA Dues", CHECKING, EXP_HOA, D("425.00"), 1, True),
        ("Streaming Bundle", CHECKING, EXP_STREAMING, D("45.97"), 8, True),
        ("PEMCO Insurance — auto policy", CHECKING, EXP_AUTO_INS,
         AUTO_INSURANCE, 6, True),
        ("Safeco — HO-6 condo policy", CHECKING, EXP_HOME_INS,
         HO6_INSURANCE, 11, True),
        ("Pet Food - Chewy", CHECKING, EXP_PET_FOOD, D("48.00"), 18, True),
        ("WeWork Coworking", AMEX, EXP_COWORKING, D("250.00"), 4, False),
    ]
    for desc, src, dst, amt, day, ach in fixed_bills:
        for yr, m in _month_iter(date(YEAR, 1, 1), through):
            when = _clamp_day(yr, m, day)
            if ach:
                when = _next_bday(when)
            if when <= through:
                txns.append({
                    "description": desc, "date": when,
                    "splits": [(src, -amt), (dst, amt)],
                })

    # Utilities + telecom DRIFT month to month (the bookkeeper's "too uniform"
    # tell): seasonal variation on electric/gas/water plus small per-bill
    # jitter on internet/phone. A dedicated RNG keeps these deterministic and
    # decoupled from the other recurring streams. Seasonal multiplier indexed
    # by month (Jan..Dec) for each utility — Seattle pattern: electric peaks
    # in winter (heat/light) and mild summer (AC); gas peaks hard in winter
    # (heating); water peaks in summer (gardens/irrigation). Each utility
    # bills on its own day (audit C5 — the 15th used to carry all three).
    rng_util = random.Random(SEED + 9)
    ELEC_SEASON = {1: 1.30, 2: 1.25, 3: 1.10, 4: 0.95, 5: 0.90, 6: 0.95,
                   7: 1.05, 8: 1.10, 9: 0.95, 10: 1.00, 11: 1.15, 12: 1.30}
    GAS_SEASON = {1: 1.80, 2: 1.70, 3: 1.40, 4: 1.05, 5: 0.70, 6: 0.55,
                  7: 0.50, 8: 0.50, 9: 0.65, 10: 1.00, 11: 1.45, 12: 1.75}
    WATER_SEASON = {1: 0.85, 2: 0.85, 3: 0.90, 4: 1.00, 5: 1.15, 6: 1.30,
                    7: 1.45, 8: 1.45, 9: 1.20, 10: 1.00, 11: 0.90, 12: 0.85}

    def _seasonal(base: float, season: dict, m: int) -> Decimal:
        # base × seasonal factor × ±6% random jitter, cents-bearing.
        val = base * season[m] * (1 + rng_util.uniform(-0.06, 0.06))
        return D(str(round(val, 2)))

    seasonal_utils = [
        ("Electric - Seattle City Light", EXP_ELECTRIC, 95.0, ELEC_SEASON, 9),
        ("Gas - Puget Sound Energy", EXP_GAS, 65.0, GAS_SEASON, 19),
        ("Water/Sewer - SPU", EXP_WATER, 55.0, WATER_SEASON, 22),
    ]
    for desc, dst, base, season, day in seasonal_utils:
        for yr, m in _month_iter(date(YEAR, 1, 1), through):
            when = _next_bday(_clamp_day(yr, m, day))
            if when <= through:
                amt = _seasonal(base, season, m)
                txns.append({
                    "description": desc, "date": when,
                    "splits": [(CHECKING, -amt), (dst, amt)],
                })

    # Internet + phone: nominally flat, but real bills drift — promo
    # roll-offs, overage, taxes/fees. Small per-bill jitter around the base,
    # with a one-time mid-2026 price bump on internet (a believable rate
    # increase) so the line isn't perfectly uniform across years. AWS is
    # usage-billed and never round (audit C4).
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        when = _next_bday(_clamp_day(yr, m, 3))
        if when <= through:
            net_base = 79.99 if (yr, m) < (2026, 7) else 84.99  # mid-2026 bump
            amt = D(str(round(net_base + rng_util.uniform(-1.5, 4.0), 2)))
            txns.append({
                "description": "Internet - Comcast", "date": when,
                "splits": [(CHECKING, -amt), (EXP_INTERNET, amt)],
            })
        when = _next_bday(_clamp_day(yr, m, 12))
        if when <= through:
            amt = D(str(round(140.0 + rng_util.uniform(-3.0, 6.0), 2)))
            txns.append({
                "description": "Phone - T-Mobile", "date": when,
                "splits": [(CHECKING, -amt), (EXP_PHONE, amt)],
            })
        when = _clamp_day(yr, m, 2)
        if when <= through:
            amt = D(str(round(125.0 + rng_util.uniform(-9.0, 14.0), 2)))
            txns.append({
                "description": "AWS Cloud Hosting", "date": when,
                "notes": f"Usage billing, {date(yr, m, 1).strftime('%B %Y')}",
                "splits": [(AMEX, -amt), (EXP_CLOUD, amt)],
            })

    # Quarterly umbrella insurance (Jan/Apr/Jul/Oct, 21st — off the
    # 15th's 1040-ES / statement pile, cold audit C6).
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        if m in (1, 4, 7, 10):
            when = _next_bday(date(yr, m, 21))
            if when <= through:
                txns.append({
                    "description": "Umbrella Insurance Premium",
                    "date": when,
                    "splits": [(CHECKING, D("-125.00")),
                               (EXP_UMBRELLA, D("125.00"))],
                })

    # Property tax halves, every year (Apr 30 / Oct 31), to the cent and
    # re-levied annually (audit B12).
    for yr in range(YEAR, through.year + 1):
        half = _property_tax_half(yr)
        for mo, day, label in [(4, 30, "1st"), (10, 31, "2nd")]:
            when = _next_bday(date(yr, mo, day))
            if when <= through:
                txns.append({
                    "description": f"King County Property Tax ({label} Half)",
                    "date": when,
                    "notes": f"{yr} levy, parcel statement — {label} half",
                    "splits": [(CHECKING, -half), (EXP_PROP_TAX, half)],
                })

    # Estimated federal tax at the real IRS deadlines, sized from
    # annualized SE income with the SE-tax component broken out, plus
    # the April balance-due on the completed year (audit A3). The
    # owner's taxes are paid from the household account (audit D1).
    for est in _estimated_tax_plan(through):
        if est["kind"] == "installment":
            total = est["income_part"] + est["se_part"]
            txns.append({
                "description": est["description"], "date": est["date"],
                "notes": est["notes"],
                "splits": [(CHECKING, -total),
                           (EXP_EST_TAX, est["income_part"]),
                           (EXP_SE_TAX, est["se_part"])],
            })
        else:
            amt = est["amount"]
            txns.append({
                "description": est["description"], "date": est["date"],
                "notes": est["notes"],
                "splits": [(CHECKING, -amt), (EXP_FED, amt)],
            })

    # Washington B&O, the Seattle license, the SOS annual report — the
    # LLC's own taxes and licenses, paid from the LLC (audit A7).
    for item in _bo_tax_plan(through):
        amt = item["amount"]
        txns.append({
            "description": item["description"], "date": item["date"],
            "notes": item["notes"],
            "splits": [(LLC_CHECKING, -amt), (EXP_BIZ_TAXES, amt)],
        })

    # Solo 401(k) employer contribution each December (audit B7).
    solo_contribs: list[date] = []
    for yr in range(YEAR, through.year + 1):
        when = _next_bday(date(yr, *SOLO_401K_DAY))
        if when <= through:
            solo_contribs.append(when)
            txns.append({
                "description": "Vanguard — Solo 401(k) employer contribution",
                "date": when,
                "notes": f"Tax year {yr} employer (profit-sharing) "
                         f"contribution from Cascade Code LLC",
                "splits": [(LLC_CHECKING, -SOLO_401K_CONTRIBUTION),
                           (SOLO_401K, SOLO_401K_CONTRIBUTION)],
            })

    # HSA cash earns a little (audit B6): quarterly on the running
    # balance (opening + payroll contributions + prior interest).
    hsa_interest_total = D("0")
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        if m not in (3, 6, 9, 12):
            continue
        qe = _clamp_day(yr, m, 31)
        if qe > through:
            continue
        contribs = sum((amt for when, amt in hsa_contribs if when <= qe),
                       D("0"))
        principal = D("4800") + contribs + hsa_interest_total
        interest = (principal * HSA_APY / 4).quantize(D("0.01"))
        if interest > 0:
            txns.append({
                "description": "HSA Bank — interest",
                "date": qe,
                "splits": [(HSA, interest), (HSA_INTEREST, -interest)],
            })
            hsa_interest_total += interest

    # The retirement plans move with the market (cold audit B4): a
    # quarterly statement line on each, the quarter's return taken from
    # a broad-market index fund (VTSAX's real closes) applied to the
    # balance at the quarter's open. Unrealized — booked against an
    # income leaf that never distributes.
    uwrp_changes = solo_changes = D("0")
    prev_qe = date(YEAR - 1, 12, 31)
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        if m not in (3, 6, 9, 12):
            continue
        qe = _clamp_day(yr, m, 31)
        if qe > through:
            continue
        p0 = MD.security("VTSAX", prev_qe)
        p1 = MD.security("VTSAX", qe)
        ret = (p1 / p0 - 1)
        quarter = f"Q{(m - 1) // 3 + 1} {yr}"
        uwrp_bal = (D("38400") + uwrp_changes
                    + sum((amt for when, amt in uwrp_contribs if when <= prev_qe),
                          D("0")))
        change = (uwrp_bal * ret).quantize(D("0.01"))
        if change != 0:
            txns.append({
                "description": "Fidelity — UWRP 403(b) market change",
                "date": qe,
                "notes": f"{quarter} statement: {ret * 100:+.2f}% on the "
                         f"${uwrp_bal:,.2f} balance at {prev_qe.isoformat()}",
                "splits": [(UWRP, change), (RETIREMENT_CHANGE, -change)],
            })
            uwrp_changes += change
        solo_bal = (solo_changes + SOLO_401K_CONTRIBUTION
                    * sum(1 for when in solo_contribs if when <= prev_qe))
        change = (solo_bal * ret).quantize(D("0.01"))
        if change != 0:
            txns.append({
                "description": "Vanguard — Solo 401(k) market change",
                "date": qe,
                "notes": f"{quarter} statement: {ret * 100:+.2f}% on the "
                         f"${solo_bal:,.2f} balance at {prev_qe.isoformat()}",
                "splits": [(SOLO_401K, change), (RETIREMENT_CHANGE, -change)],
            })
            solo_changes += change
        prev_qe = qe

    return txns


# ── Phase 6: Daily/weekly patterns + seasonal one-offs ──────────

GROCERY_VENDORS = ["QFC", "Fred Meyer", "Safeway"]
GAS_VENDORS = ["Shell", "76", "Safeway Fuel", "Chevron"]
COFFEE_VENDORS = [
    "Starbucks", "Victrola Coffee", "Caffé Ladro", "Stumptown Coffee",
    "Cherry Street Coffee", "Lighthouse Roasters", "Slate Coffee",
    "Tougo Coffee", "Analog Coffee", "Storyville Coffee",
]
RESTAURANTS = [
    "Canlis", "Westward", "Shaker & Spear", "Purple Café",
    "Tilikum Place Café", "Wild Ginger", "Kedai Makan", "Ba Bar",
    "Terra Plata", "Il Corvo", "Marination Ma Kai", "Bateau",
]
# (descriptor, expense account, card). Office supplies are a business
# purchase and go on the Business Amex (audit A4); the rest is
# household spend on Chase.
AMAZON_CATEGORIES = [
    ("household goods", EXP_MISC, CHASE),
    ("pet supplies", EXP_PET_FOOD, CHASE),
    ("books", EXP_EDUCATION, CHASE),
    ("office supplies", EXP_OFFICE, AMEX),
    ("kitchen goods", EXP_MISC, CHASE),
]
CLOTHING_VENDORS = ["Target", "Nordstrom", "REI"]

# (month, day, description, amount_str, source, target, keep).
# Negative amount = refund/reversal. ``keep`` pins the event to every
# year (the tax software, the vet, the year-end gift); everything else
# is subject to the per-year skip/scale/jitter pass (audit C2).
MONTHLY_EVENTS = [
    (1, 15, "Byte's vet visit", "180", CHECKING, EXP_PET_VET, True),
    (1, 10, "New Year gift return", "-45", CHASE, EXP_GIFTS, False),
    (2, 14, "Valentine's dinner - Canlis", "165", CHASE, EXP_DINING, False),
    (2, 22, "Ski trip - Snoqualmie", "340", CHASE, EXP_TRAVEL, False),
    (3, 5, "TurboTax Home & Business", "89", CHASE, EXP_SUBSCRIPTIONS, True),
    (3, 18, "Spring clothing", "210", CHASE, EXP_CLOTHING, False),
    (4, 9, "Subaru of Seattle - oil change & service", "89", CHASE,
     EXP_AUTO_MAINT, True),
    (4, 12, "Byte's annual checkup", "320", CHECKING, EXP_PET_VET, True),
    (5, 24, "Memorial Day BBQ supplies", "95", CHECKING, EXP_GROCERIES, False),
    (5, 10, "Garden supplies", "67", CHASE, EXP_HOUSING_MAINT, False),
    (6, 28, "Pride festival food", "120", CHASE, EXP_DINING, False),
    (6, 28, "Pride festival merch", "85", CHASE, EXP_MISC, False),
    (6, 15, "Anniversary dinner", "225", CHASE, EXP_DINING, True),
    (7, 4, "4th of July party supplies", "145", CHECKING, EXP_DINING, False),
    (7, 15, "Summer road trip - lodging", "890", CHASE, EXP_TRAVEL, False),
    (7, 18, "Road trip fuel", "340", CHASE, EXP_FUEL, False),
    (8, 8, "Dell U2725D monitor", "450", AMEX, EXP_EQUIPMENT, False),
    (9, 3, "PyCon US conference ticket", "799", AMEX, EXP_PROF_DEV, False),
    (9, 1, "Labor Day camping", "280", CHASE, EXP_TRAVEL, False),
    (10, 14, "Subaru of Seattle - scheduled service", "420", CHASE,
     EXP_AUTO_MAINT, False),
    (10, 28, "Halloween supplies", "65", CHECKING, EXP_MISC, False),
    (10, 20, "Byte vet visit", "150", CHECKING, EXP_PET_VET, False),
    (11, 25, "Thanksgiving groceries", "185", CHECKING, EXP_GROCERIES, True),
    (11, 28, "Black Friday - Target", "105", CHASE, EXP_CLOTHING, False),
    (11, 28, "Black Friday - REI", "125", CHASE, EXP_CLOTHING, False),
    (11, 29, "Black Friday - Nordstrom", "135", CHASE, EXP_CLOTHING, False),
    (11, 29, "Cyber Monday - Amazon", "55", CHASE, EXP_MISC, False),
    (12, 10, "Holiday gift - Robin", "120", CHASE, EXP_GIFTS, True),
    (12, 12, "Holiday gift - Mom", "85", CHASE, EXP_GIFTS, False),
    (12, 14, "Holiday gift - Dad", "95", CHASE, EXP_GIFTS, False),
    (12, 15, "Holiday gift - sister", "65", CHASE, EXP_GIFTS, False),
    (12, 18, "Holiday gift - coworkers", "75", CHASE, EXP_GIFTS, False),
    (12, 20, "Holiday gift - friends group", "80", CHASE, EXP_GIFTS, False),
    (12, 22, "Holiday gift - nieces", "90", CHASE, EXP_GIFTS, False),
    (12, 23, "Holiday gift - last-minute", "40", CHASE, EXP_GIFTS, False),
    (12, 26, "Holiday travel - flights", "580", CHASE, EXP_TRAVEL, False),
    (12, 30, "Year-end donation - NAMI", "500", CHECKING, EXP_CHARITY, True),
]
# Swap-ins for a skipped event's slot: the year that has no ski trip
# has a different outing instead.
SEASONAL_ALTERNATES = [
    ("Whistler weekend - lift tickets", "410", CHASE, EXP_TRAVEL),
    ("Seattle Symphony - tickets", "160", CHASE, EXP_ENTERTAINMENT),
    ("Olympic Peninsula - cabin rental", "520", CHASE, EXP_TRAVEL),
    ("Mariners game - tickets + food", "140", CHASE, EXP_ENTERTAINMENT),
    ("Cooking class - Hot Stove Society", "185", CHASE, EXP_EDUCATION),
    ("Kayak rental - Lake Union", "95", CHASE, EXP_ENTERTAINMENT),
]


# The cash float (cold audit B8): withdraw $400 whenever the jar is
# under $750 — a few days of household burn on hand, never a dashboard
# alarm, never negative.
CASH_FLOOR = D("750")
CASH_WITHDRAWAL = D("400.00")


def _vary(rng, base, spread=0.2):
    return D(str(round(base * (1 + rng.uniform(-spread, spread)), 2)))


def _spend(rng, low, high) -> Decimal:
    """A consumer dollar amount in ``[low, high]`` carrying realistic cents.

    Real-world US retail/F&B receipts rarely land on a whole dollar: prices
    like $4.85, $36.50, $12.99 are the norm. We draw a whole-dollar base in
    range then add a cents component biased toward common price-point endings
    (.99/.95/.50/.49/.00 and round-dime .x0) so the distribution looks like
    menu/shelf pricing rather than uniform noise, while still spanning
    arbitrary .xx values. Both splits of a transaction carry the same
    magnitude, so cents balance automatically.

    Use this for daily/weekly/discretionary/retail spend. Keep genuinely-round
    items (paychecks, rent/mortgage, loan payments, fixed subscriptions,
    transfers) on whole-dollar Decimals.
    """
    base = rng.randint(int(low), int(high))
    r = rng.random()
    if r < 0.14:
        cents = 0           # genuinely round (some receipts are)
    elif r < 0.34:
        cents = 99          # .99 (the dominant US price ending)
    elif r < 0.46:
        cents = 95          # .95
    elif r < 0.56:
        cents = 50          # .50
    elif r < 0.64:
        cents = 49          # .49
    elif r < 0.82:
        cents = rng.randint(1, 9) * 10   # round dime: .10 .. .90
    else:
        cents = rng.randint(1, 99)       # arbitrary cents
    amt = D(base) + (D(cents) / D(100))
    return amt.quantize(D("0.01"))


# ── Merchant → canonical expense category ───────────────────────
#
# Real people miscategorize SYSTEMATICALLY, not stochastically: a given
# merchant lands in the SAME account every time (often via an autopay/auto-
# import rule or a habit), right or wrong. This dict pins every recurring
# consumer merchant to ONE canonical category so the daily/weekly/volume
# generators look the merchant up here instead of scattering it across
# buckets.
#
# The one deliberate, CONSISTENT miscategorization (the believable standing
# mistake the household actually makes):
#   • "Vending Machine" → Miscellaneous, ALWAYS. A careful bookkeeper would
#     call a snack-machine charge Dining, but Alex set a stale auto-rule in
#     the bank's import years ago that dumps every Vending Machine charge into
#     Misc and never fixed it — so it's systematically (not randomly) wrong,
#     the same way every time. That's the realistic texture: a consistent
#     error, not stochastic scatter.
# Everything else maps to the account a careful bookkeeper would expect. In
# particular, "Transit Pass"/"Parking Meter" book to Transportation (cold
# audit C5) — NOT Dining, and not Auto:Fuel.
MERCHANT_CATEGORY: dict[str, str] = {
    # Coffee / quick food → Dining (correct)
    "Morning Coffee": EXP_DINING,
    "Lunch Spot": EXP_DINING,
    "Food Cart": EXP_DINING,
    # Convenience / sundries → Groceries (correct)
    "Corner Store": EXP_GROCERIES,
    # Health / personal → the right home (correct)
    "Drug Store": EXP_MEDICAL,
    "Dry Cleaner": EXP_PERSONAL_CARE,
    "News Stand": EXP_MISC,
    # Transport → its own leaf.
    "Parking Meter": EXP_TRANSPORTATION,
    "Transit Pass": EXP_TRANSPORTATION,
    # ── Deliberate sticky miscategorization (always wrong, always same) ──
    "Vending Machine": EXP_MISC,   # stale bank-import auto-rule → Misc, every time
}


def merchant_category(name: str, default: str) -> str:
    """Canonical category for ``name`` (consistent every time), else default.

    Matches on a leading-substring key so descriptions with a suffix still
    resolve. Encodes the sticky-error behavior in ``MERCHANT_CATEGORY``.
    """
    for key, cat in MERCHANT_CATEGORY.items():
        if name.startswith(key):
            return cat
    return default


def gen_daily_weekly(through: date) -> list[dict]:
    """Daily/weekly/seasonal spend from 2025-01-01 through ``through``.

    Runs continuously so the most recent weeks have groceries, gas,
    coffee, dining, and Amazon activity — the burn-rate the dashboard
    reads. Seasonal one-offs replay each calendar year.
    """
    txns: list[dict] = []
    start = date(YEAR, 1, 1)

    # Weekend groceries (one Sat/Sun per weekend) on Checking.
    rng = random.Random(SEED + 1)
    d = start
    while d.weekday() != 5:
        d += timedelta(days=1)
    i = 0
    while d <= through:
        vendor = GROCERY_VENDORS[i % len(GROCERY_VENDORS)]
        day = d + timedelta(days=rng.randint(0, 1))
        if day <= through:
            amt = _spend(rng, 60, 110)
            txns.append({"description": vendor, "date": day,
                         "splits": [(CHECKING, -amt),
                                    (merchant_category(vendor, EXP_GROCERIES),
                                     amt)]})
        d += timedelta(days=7)
        i += 1

    # Weekly gas fills on Checking.
    rng = random.Random(SEED + 2)
    d = start
    while d.weekday() != 0:
        d += timedelta(days=1)
    i = 0
    while d <= through:
        vendor = GAS_VENDORS[i % len(GAS_VENDORS)]
        day = d + timedelta(days=rng.randint(0, 6))
        if day <= through:
            amt = _spend(rng, 38, 68)
            txns.append({"description": f"{vendor} Gas", "date": day,
                         "splits": [(CHECKING, -amt), (EXP_FUEL, amt)]})
        d += timedelta(days=7)
        i += 1

    # Weekday coffee on Chase.
    rng = random.Random(SEED + 3)
    d = start
    while d <= through:
        if d.weekday() < 5:
            vendor = rng.choice(COFFEE_VENDORS)
            amt = _spend(rng, 4, 8)
            txns.append({"description": vendor, "date": d,
                         "splits": [(CHASE, -amt), (EXP_DINING, amt)]})
        d += timedelta(days=1)

    # Restaurants 2-3x/month.
    rng = random.Random(SEED + 4)
    for yr, m in _month_iter(start, through):
        for _ in range(rng.randint(2, 3)):
            day = _clamp_day(yr, m, rng.randint(1, 28))
            if day > through:
                continue
            vendor = rng.choice(RESTAURANTS)
            amt = _spend(rng, 45, 95)
            src = CHASE if rng.random() < 0.6 else CHECKING
            txns.append({"description": vendor, "date": day,
                         "splits": [(src, -amt), (EXP_DINING, amt)]})

    # Amazon 2-3x/month — household lines on Chase, office supplies on
    # the Business Amex (audit A4).
    rng = random.Random(SEED + 5)
    for yr, m in _month_iter(start, through):
        for _ in range(rng.randint(2, 3)):
            day = _clamp_day(yr, m, rng.randint(1, 28))
            if day > through:
                continue
            descriptor, expense, card = rng.choice(AMAZON_CATEGORIES)
            amt = _spend(rng, 15, 120)
            txns.append({"description": f"Amazon.com - {descriptor}",
                         "date": day,
                         "splits": [(card, -amt), (expense, amt)]})

    # Quarterly clothing on Chase.
    rng = random.Random(SEED + 6)
    for yr, m in _month_iter(start, through):
        if m not in (3, 6, 9, 12):
            continue
        day = date(yr, m, rng.randint(10, 20))
        if day > through:
            continue
        vendor = rng.choice(CLOTHING_VENDORS)
        amt = _spend(rng, 35, 150)
        txns.append({"description": vendor, "date": day,
                     "splits": [(CHASE, -amt), (EXP_CLOTHING, amt)]})

    # Seasonal one-offs, each calendar year in range — but never the
    # same year twice (audit C2): per (year, event) the amount scales
    # ±15%, the date slides ±3 days, and ~20% of the optional events
    # are skipped, half of those swapped for a different outing. The
    # planned dollar figure is a budget target; the actual receipt
    # carries realistic cents. Seeding is per (year, event) so a later
    # ``through`` never re-rolls an earlier year (continuation replays
    # the prefix verbatim). Refunds (negative) stay exact — they
    # reverse a known charge.
    for yr in range(YEAR, through.year + 1):
        # A swap-in outing happens at most once a year (cold audit C10:
        # named one-offs are drawn without replacement).
        used_alternates: set[str] = set()
        for month, day, desc, amt_str, src, dst, keep in MONTHLY_EVENTS:
            rng_event = random.Random(f"alex:season:{yr}:{month}:{desc}")
            amt = D(amt_str)
            if not keep and amt > 0 and rng_event.random() < 0.20:
                # Skipped this year; half the time something else
                # happened in its place.
                if rng_event.random() < 0.5:
                    offset = rng_event.randrange(len(SEASONAL_ALTERNATES))
                    order = (SEASONAL_ALTERNATES[offset:]
                             + SEASONAL_ALTERNATES[:offset])
                    pick = next((a for a in order
                                 if a[0] not in used_alternates), None)
                    if pick is None:
                        continue
                    alt_desc, alt_amt, src, dst = pick
                    used_alternates.add(alt_desc)
                    desc, amt = alt_desc, D(alt_amt)
                else:
                    continue
            when = _clamp_day(yr, month, day)
            if amt > 0:
                when += timedelta(days=rng_event.randint(-3, 3))
                when = max(when, date(YEAR, 1, 1))
            if when > through:
                continue
            if amt < 0:
                splits = [(src, abs(amt)), (dst, -abs(amt))]
            else:
                scale = 1 + rng_event.uniform(-0.15, 0.15)
                cents = D(rng_event.randint(0, 99)) / D(100)
                amt = (D(str(round(float(amt) * scale))) + cents).quantize(
                    D("0.01"))
                splits = [(src, -amt), (dst, amt)]
            txns.append({"description": desc, "date": when, "splits": splits})

    # Cash on hand (cold audit B8): the household keeps a small float —
    # an ATM withdrawal whenever it dips under the floor, spent at the
    # farmers market, on tips and on the occasional cash-only counter.
    # Run as a balance so the account never goes negative and the float
    # stays a few days of household burn (the dashboard's low-cash line
    # is one day's expense burn).
    rng = random.Random(SEED + 31)
    cash = D("350")
    cash_spends = [
        ("Ballard Farmers Market (cash)", EXP_GROCERIES, 18, 42),
        ("Cash — tips", EXP_DINING, 8, 25),
        ("Cash — street fair / food stand", EXP_DINING, 12, 30),
        ("Cash — car wash", EXP_AUTO_MAINT, 12, 20),
        ("Cash — flowers, corner stand", EXP_MISC, 10, 22),
    ]
    d = start
    while d <= through:
        if cash < CASH_FLOOR:
            atm = _next_bday(d)
            if atm <= through:
                txns.append({"description": "Chase ATM — cash withdrawal",
                             "date": atm,
                             "splits": [(CHECKING, -CASH_WITHDRAWAL),
                                        (CASH, CASH_WITHDRAWAL)]})
                cash += CASH_WITHDRAWAL
        for _ in range(rng.randint(1, 3)):
            when = d + timedelta(days=rng.randint(0, 13))
            desc, dst, lo, hi = rng.choice(cash_spends)
            amt = _spend(rng, lo, hi)
            if when > through or amt > cash:
                continue
            txns.append({"description": desc, "date": when,
                         "splits": [(CASH, -amt), (dst, amt)]})
            cash -= amt
        d += timedelta(days=14)

    return txns


# ── Phase 6b: Personal-life spending (medical, gifts, charity, ───
#               travel, entertainment, personal care) ────────────

# Vendors keep the spending legible in the register and the bookkeeper's
# eyes — same texture as the daily/weekly vendor lists above.
PHARMACY_VENDORS = ["Bartell Drugs", "Walgreens", "Rite Aid", "QFC Pharmacy"]
DOCTOR_VENDORS = ["Polyclinic copay", "Swedish Medical copay",
                  "Kaiser Permanente copay", "UW Medicine copay"]
ENTERTAINMENT_VENDORS = [
    "AMC Pacific Place", "SIFF Cinema", "Regal Thornton Place",
    "The Crocodile (cover)", "Neumos (cover)", "Sunset Tavern",
    "Stout Brewing", "Optimism Brewing", "Cinerama matinee",
    "Bowling - Garage Billiards", "Trivia night - tab",
    "Museum of Pop Culture", "Pacific Science Center",
]
CONCERT_VENDORS = [
    "Climate Pledge Arena - concert", "Paramount Theatre - show",
    "The Showbox - concert", "Moore Theatre - show",
    "WaMu Theater - concert",
]
# Birthdays recur (different people); a wedding, a baby shower or a
# housewarming is a one-off per year — drawn without replacement from
# the year's pool (cold audit C10).
GIFT_BIRTHDAYS = [
    "birthday gift - Robin's friend", "birthday gift - coworker",
    "birthday gift - niece", "birthday gift - brother-in-law",
    "birthday gift - Mom", "birthday gift - Dad", "birthday gift - sister",
    "birthday gift - Robin's brother",
]
GIFT_ONE_OFFS = ["housewarming gift", "wedding gift", "baby shower gift",
                 "graduation gift", "retirement gift - Robin's mentor"]


def gen_personal_life(through: date) -> list[dict]:
    """Personal-life spending streams, 2025-01 → ``through``.

    Each stream walks the months with realistic cadence and lumpiness,
    amounts varied within target ranges via the seeded RNG (not
    hardcoded). Paid from the same accounts the existing daily spend
    uses: routine/household-style costs on Checking, discretionary
    card-driver spend on Chase. Targets (monthly average):

      Medical        ~$100-200   pharmacy monthly + quarterly copay + annual dental
      Gifts          ~$50-100    lumpy: scattered birthdays + a December spike
      Charity        ~$50-100    monthly donation + a December year-end gift
      Travel         the one-time client trip lives in MONTHLY_EVENTS-style add below
      Entertainment  ~$100-200   weekly-ish outings + occasional concert spikes
      Personal Care  ~$50-80     monthly gym + a haircut every ~6 weeks
    """
    txns: list[dict] = []
    start = date(YEAR, 1, 1)
    rng = random.Random(SEED + 21)

    # ── Medical: pharmacy/copay monthly, quarterly doctor visit, ──
    #    annual dental cleaning. Routine health spend on Checking
    #    (HSA card / debit-style); copays/uncovered costs still flow.
    for yr, m in _month_iter(start, through):
        # Monthly pharmacy / small copay (~$30-60).
        day = _clamp_day(yr, m, rng.randint(6, 24))
        if day <= through:
            amt = _spend(rng, 30.0, 60.0)
            vendor = rng.choice(PHARMACY_VENDORS)
            txns.append({"description": vendor, "date": day,
                         "splits": [(CHECKING, -amt), (EXP_MEDICAL, amt)]})
        # Quarterly doctor-visit copay (~$120-180) in Feb/May/Aug/Nov.
        if m in (2, 5, 8, 11):
            dday = _clamp_day(yr, m, rng.randint(8, 22))
            if dday <= through:
                amt = _spend(rng, 120.0, 180.0)
                vendor = rng.choice(DOCTOR_VENDORS)
                txns.append({"description": vendor, "date": dday,
                             "splits": [(CHECKING, -amt),
                                        (EXP_MEDICAL, amt)]})
        # Annual dental cleaning (~$200) each March.
        if m == 3:
            dday = _clamp_day(yr, m, rng.randint(10, 20))
            if dday <= through:
                amt = _spend(rng, 180.0, 230.0)
                txns.append({"description": "Capitol Hill Dental - cleaning",
                             "date": dday,
                             "splits": [(CHECKING, -amt),
                                        (EXP_MEDICAL, amt)]})

    # ── Gifts: LUMPY but near-monthly baseline. Bookkeeper spec is ──
    #    "$50-100/mo average, lumpy — $20 some months, $300 in December."
    #    Most months get a small gift ($20-50); a handful of months
    #    additionally carry a bigger occasion-gift ($60-120); plus a
    #    ~$300 December holiday spike. (The seasonal MONTHLY_EVENTS
    #    already carry the named December gift list; this layers the
    #    small monthly habit + scattered occasions on top.)
    #    Small near-monthly baseline: skip only ~1 in 6 months so any
    #    recent-5-month window averages out near the target.
    def _gift_pool(yr: int) -> list[str]:
        """The year's occasions, shuffled: every one-off at most once,
        birthdays refilled as needed."""
        pool_rng = random.Random(f"alex:gifts:{yr}")
        pool = list(GIFT_ONE_OFFS) + GIFT_BIRTHDAYS * 2
        pool_rng.shuffle(pool)
        return pool

    def _next_occasion(pool: list[str]) -> str:
        if not pool:
            pool.extend(GIFT_BIRTHDAYS)
        return pool.pop()

    for yr in range(YEAR, through.year + 1):
        pool = _gift_pool(yr)
        # Small near-monthly baseline gift (Jan–Nov).
        for m in range(1, 12):
            if (yr, m) > (through.year, through.month):
                break
            if rng.random() < 0.85:  # ~5-6 of every 6 months get a small gift
                day = _clamp_day(yr, m, rng.randint(3, 26))
                if day <= through:
                    amt = _spend(rng, 30.0, 60.0)
                    txns.append({"description": _next_occasion(pool),
                                 "date": day,
                                 "splits": [(CHASE, -amt), (EXP_GIFTS, amt)]})
        # 5-6 bigger occasion gifts per year, spread across non-December
        # months, for the realistic lumpiness on top of the baseline so
        # any 5-month window catches a couple.
        n_gifts = rng.randint(5, 6)
        months = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
        chosen_months = rng.sample(months, k=min(n_gifts, len(months)))
        for m in chosen_months:
            day = _clamp_day(yr, m, rng.randint(3, 26))
            if day > through:
                continue
            amt = _spend(rng, 60.0, 120.0)
            txns.append({"description": _next_occasion(pool), "date": day,
                         "splits": [(CHASE, -amt), (EXP_GIFTS, amt)]})
        # A consolidated ~$300 early-December holiday-gift haul (on top
        # of the named per-recipient gifts in MONTHLY_EVENTS).
        dday = _clamp_day(yr, 12, rng.randint(3, 8))
        if dday <= through:
            amt = _spend(rng, 280.0, 320.0)
            txns.append({"description": "Holiday gift haul",
                         "date": dday,
                         "splits": [(CHASE, -amt), (EXP_GIFTS, amt)]})

    # ── Charity: a FIXED recurring monthly donation ($60, stepped up ──
    #    to $75 from 2026 — a standing gift is a flat amount, cold
    #    audit B7) on the 9th (off the 5th, C6) + a December year-end
    #    gift (~$300-500). The MONTHLY_EVENTS NAMI year-end donation
    #    already exists; this adds the steady monthly habit plus a
    #    second December gift so charity reads as ongoing.
    for yr, m in _month_iter(start, through):
        day = _next_bday(_clamp_day(yr, m, 9))
        if day <= through:
            amt = D("60.00") if yr == YEAR else D("75.00")
            txns.append({"description": "Monthly donation - Doctors Without "
                                        "Borders",
                         "date": day,
                         "splits": [(CHECKING, -amt), (EXP_CHARITY, amt)]})
        if m == 12:
            dday = _clamp_day(yr, m, rng.randint(20, 28))
            if dday <= through:
                amt = _spend(rng, 300.0, 500.0)
                txns.append({"description": "Year-end gift - Northwest Harvest",
                             "date": dday,
                             "splits": [(CHECKING, -amt),
                                        (EXP_CHARITY, amt)]})

    # ── Entertainment: weekly-ish outings (~$25-60) on Chase, plus an ──
    #    occasional concert/event spike (~$150-250) twice a year.
    d = start
    while d <= through:
        # ~3 outings a month (skip ~1 week in 4), jittered onto a
        # Thu-Sat evening, trimmed to land the recent-window average
        # near the $100-200 target midpoint rather than over it.
        if rng.random() < 0.82:
            day = d + timedelta(days=rng.randint(3, 5))
            if day <= through:
                amt = _spend(rng, 22.0, 50.0)
                vendor = rng.choice(ENTERTAINMENT_VENDORS)
                txns.append({"description": vendor, "date": day,
                             "splits": [(CHASE, -amt),
                                        (EXP_ENTERTAINMENT, amt)]})
        d += timedelta(days=7)
    # Concert/event spikes: spring (May) + fall (Oct), each year.
    for yr in range(YEAR, through.year + 1):
        for m in (5, 10):
            cday = _clamp_day(yr, m, rng.randint(8, 24))
            if cday > through:
                continue
            amt = _spend(rng, 150.0, 250.0)
            vendor = rng.choice(CONCERT_VENDORS)
            txns.append({"description": vendor, "date": cday,
                         "splits": [(CHASE, -amt), (EXP_ENTERTAINMENT, amt)]})

    # ── Personal Care: monthly gym membership (~$50) + haircut every ──
    #    ~6 weeks (~$40). Gym on Checking (autopay), haircuts on Chase.
    for yr, m in _month_iter(start, through):
        day = _next_bday(_clamp_day(yr, m, 6))
        if day <= through:
            amt = _vary(rng, 50.0, spread=0.06)  # ~$47-53 gym dues
            txns.append({"description": "Gym membership - Seattle Athletic",
                         "date": day,
                         "splits": [(CHECKING, -amt),
                                    (EXP_PERSONAL_CARE, amt)]})
    # Haircuts roughly every 6 weeks (42 days), jittered.
    d = start + timedelta(days=rng.randint(5, 20))
    while d <= through:
        amt = _spend(rng, 35.0, 48.0)
        txns.append({"description": "Rudy's Barbershop - haircut",
                     "date": d,
                     "splits": [(CHASE, -amt), (EXP_PERSONAL_CARE, amt)]})
        d += timedelta(days=42 + rng.randint(-4, 6))

    # ── Light retail thickening: occasional Amazon-style purchases ──
    #    (~$40-120) to Miscellaneous, ~1 per month some months.
    for yr, m in _month_iter(start, through):
        if rng.random() < 0.55:  # roughly half the months
            day = _clamp_day(yr, m, rng.randint(2, 27))
            if day > through:
                continue
            amt = _spend(rng, 40.0, 120.0)
            txns.append({"description": "Amazon.com - online order",
                         "date": day,
                         "splits": [(CHASE, -amt), (EXP_MISC, amt)]})

    # ── Periodic client-visit trips ($1,500-2,000 each): flight + hotel ──
    #    in a single month, alternating Berlin (EUR client) / Toronto
    #    (CAD client). Business travel: booked in USD on the Business
    #    Amex to Expenses:Business:Travel, with the trip's meals on
    #    Expenses:Business:Meals (Schedule C line 24a/24b — audit A4).
    #    One trip every ~5-6 months across the whole timeline, so any
    #    recent-5-month window always catches at least one trip. Anchored
    #    to month 3 then stepped +5/+6 months alternately. Keeps the
    #    light personal travel above intact.
    trip_anchor = date(YEAR, 3, 1)
    # (city, flight-vendor, hotel-label, meal vendors) alternating across trips.
    trip_specs = [
        ("Berlin", "Lufthansa - SEA-BER (Berlin client visit)",
         "Hotel - Berlin (client visit, 4 nights)",
         ["Restaurant Nobelhart & Schmutzig", "Markthalle Neun",
          "Café Einstein", "Zur letzten Instanz"]),
        ("Toronto", "Air Canada - SEA-YYZ (Nord client visit)",
         "Hotel - Toronto (client visit, 3 nights)",
         ["Canoe Restaurant", "St. Lawrence Market", "Bar Isabel",
          "Pai Northern Thai"]),
    ]
    trip_idx = 0
    cur = trip_anchor
    while cur <= through:
        flight_day = _clamp_day(cur.year, cur.month, rng.randint(18, 23))
        hotel_day = _clamp_day(cur.year, cur.month, rng.randint(24, 27))
        flight = _spend(rng, 980.0, 1180.0)
        hotel = _spend(rng, 620.0, 820.0)
        city, flight_desc, hotel_desc, meal_vendors = trip_specs[
            trip_idx % len(trip_specs)]
        if flight_day <= through:
            txns.append({"description": flight_desc, "date": flight_day,
                         "splits": [(AMEX, -flight), (EXP_BIZ_TRAVEL, flight)]})
        if hotel_day <= through:
            txns.append({"description": hotel_desc, "date": hotel_day,
                         "splits": [(AMEX, -hotel), (EXP_BIZ_TRAVEL, hotel)]})
        # 2–3 client meals across the stay (the day after the flight
        # through the hotel checkout).
        for meal_idx in range(rng.randint(2, 3)):
            meal_day = flight_day + timedelta(days=1 + meal_idx)
            meal_day = min(meal_day, hotel_day)
            meal = _spend(rng, 38.0, 96.0)
            vendor = rng.choice(meal_vendors)
            if meal_day <= through:
                txns.append({"description": f"{vendor} ({city} client meal)",
                             "date": meal_day,
                             "splits": [(AMEX, -meal), (EXP_MEALS, meal)]})
        # Step +5 or +6 months alternately so trips drift through the
        # calendar and the cadence isn't mechanically regular.
        step = 5 if trip_idx % 2 == 0 else 6
        nm = cur.month - 1 + step
        cur = date(cur.year + nm // 12, nm % 12 + 1, 1)
        trip_idx += 1

    return txns


# ── Phase 7: Business module (customers, vendors, invoices, bills) ──
#
# Every dollar of LLC revenue is an invoice through A/R (audit B4 —
# the old direct "1099 deposits" had no document behind them). Every
# invoice is planned FIRST (``_invoice_plans``), so three consumers
# read one calendar: the business phase creates the documents in
# open-date order (chronological IDs, audit B8), the price layer lays
# down a real FX rate on every post/pay date, and the tax streams size
# 1040-ES and B&O from the same gross receipts.
#
# Hourly consultants bill hours × rate (audit C7): the plan carries the
# seeded hours and the client's rate, and the entry carries them as
# quantity × price. Open dates slide ±3 business days and payments
# arrive on a seeded ~N(term, 6-day) distribution with the occasional
# late payer (audit C1). All seeding is per (purpose, anchor date) so a
# later ``through`` never re-rolls an earlier document.

# (key, name, currency, A/R account, hourly rate, billterm)
CLIENTS = {
    "emerald": ("Emerald Analytics", "USD", AR_USD, None, "2/10 Net 30"),
    "sound_transit": ("Sound Transit Data Team", "USD", AR_USD, D("150"),
                      "Net 15"),
    "berlin": ("Berlin Digital GmbH", "EUR", AR_EUR, D("120"), "Net 30"),
    "nord": ("Nord Analytique", "CAD", AR_CAD, D("135"), "Net 30"),
    "techstartup": ("TechStartup Inc", "USD", AR_USD, D("140"), "Net 30"),
    "dataflow": ("DataFlow Systems", "USD", AR_USD, D("150"), "Net 30"),
    "cloudnine": ("CloudNine Consulting", "USD", AR_USD, D("125"), "Net 30"),
    "wintertech": ("WinterTech Solutions", "USD", AR_USD, D("135"), "Net 30"),
}
EMERALD_RETAINER = D("3500.00")
TERM_DAYS = {"Net 15": 15, "Net 30": 30, "2/10 Net 30": 30}
DISCOUNT_DAYS = 10
DISCOUNT_PCT = D("0.02")

CUSTOMER_ADDRESSES = {
    "emerald": {"addr1": "1201 3rd Ave, Suite 2200",
                "addr2": "Seattle, WA 98101", "addr3": "USA",
                "phone": "+1 206 555 0142", "email": "ap@emeraldanalytics.com"},
    "sound_transit": {"addr1": "401 S Jackson St",
                      "addr2": "Seattle, WA 98104", "addr3": "USA",
                      "phone": "+1 206 555 0198",
                      "email": "accountspayable@soundtransit.example"},
    "berlin": {"addr1": "Rosenthaler Str. 40-41",
               "addr2": "10178 Berlin", "addr3": "Germany",
               "phone": "+49 30 555 0117", "email": "buchhaltung@berlindigital.de"},
    "nord": {"addr1": "1250 Boul. René-Lévesque O, bureau 1400",
             "addr2": "Montréal, QC H3B 4W8", "addr3": "Canada",
             "phone": "+1 514 555 0133", "email": "comptes@nordanalytique.ca"},
    "techstartup": {"addr1": "600 Congress Ave, Floor 14",
                    "addr2": "Austin, TX 78701", "addr3": "USA",
                    "email": "finance@techstartup.example"},
    "dataflow": {"addr1": "1800 Wazee St, Suite 300",
                 "addr2": "Denver, CO 80202", "addr3": "USA",
                 "email": "ap@dataflowsystems.example"},
    "cloudnine": {"addr1": "1120 NW Couch St, Suite 600",
                  "addr2": "Portland, OR 97209", "addr3": "USA",
                  "email": "billing@cloudnine.example"},
    "wintertech": {"addr1": "225 S 6th St, Suite 3900",
                   "addr2": "Minneapolis, MN 55402", "addr3": "USA",
                   "email": "vendors@wintertech.example"},
}
CUSTOMER_NOTES = {
    "emerald": "Seattle analytics firm; monthly data-engineering retainer. "
               "2/10 Net 30 — they take the discount when cash allows.",
    "sound_transit": "Regional transit agency; project-based engagements, "
                     "Net 15 per the master services agreement.",
    "berlin": "Digital agency in Berlin; recurring engagements. "
              "USt-IdNr. DE812345678 — B2B services to a US supplier, "
              "reverse charge; no US sales tax on professional services.",
    "nord": "Montréal data consultancy; bills in CAD. "
            "NEQ 1172345678 — GST/QST not applicable (non-resident supplier).",
    "techstartup": "Austin SaaS startup; contract development on an "
                   "hourly SOW. Issues a 1099-NEC each January.",
    "dataflow": "Denver data-platform vendor; hourly pipeline work. "
                "Issues a 1099-NEC each January.",
    "cloudnine": "Portland cloud consultancy; overflow engineering hours. "
                 "Issues a 1099-NEC each January.",
    "wintertech": "Minneapolis fintech; hourly integration work. "
                  "Issues a 1099-NEC each January.",
}
VENDORS = {
    "jetbrains": ("JetBrains", "Developer IDE and tooling.",
                  {"addr1": "Na Hřebenech II 1718/10",
                   "addr2": "140 00 Praha 4", "addr3": "Czech Republic",
                   "email": "sales@jetbrains.com"}),
    "bookkeeper": ("BookkeepingCo", "Outsourced bookkeeping firm.",
                   {"addr1": "2101 4th Ave, Suite 1250",
                    "addr2": "Seattle, WA 98121", "addr3": "USA",
                    "phone": "+1 206 555 0170",
                    "email": "billing@bookkeepingco.example"}),
    # Audit A2: the one person who works for the LLC is a 1099
    # subcontractor, billed through A/P — not a W-2 employee.
    "sam": ("Sam Rivera (contract dev)",
            "Independent contractor (sole proprietor); hourly development "
            "at $65/h under a 1099 agreement. W-9 on file; 1099-NEC filed "
            "each January.",
            {"addr1": "1517 S Fawcett Ave, Apt 4",
             "addr2": "Tacoma, WA 98402", "addr3": "USA",
             "phone": "+1 253 555 0164", "email": "sam.rivera@example.net"}),
}
SAM_RATE = D("65")


def _seeded_rng(purpose: str, anchor: date) -> random.Random:
    return random.Random(f"alex:{purpose}:{anchor.isoformat()}")


def _business_days(d: date, n: int) -> date:
    """``d`` shifted by ``n`` business days (negative = earlier)."""
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        d += timedelta(days=step)
        if d.weekday() < 5:
            remaining -= 1
    if d.weekday() >= 5:  # a zero-shift landing on a weekend → Monday
        d += timedelta(days=7 - d.weekday())
    return d


def _open_date(anchor: date) -> date:
    """Invoice open date: the anchor slid ±3 business days (audit C1),
    never before the book opens."""
    slid = _business_days(anchor, _seeded_rng("open", anchor).randint(-3, 3))
    return max(slid, date(YEAR, 1, 1))


def _pay_lag(anchor: date, term_days: int) -> int:
    """Days from open to payment: ~N(term, 6), never absurdly early,
    with ~8% late payers landing 3–5 weeks past the term."""
    rng = _seeded_rng("paylag", anchor)
    lag = int(round(rng.gauss(term_days, 6)))
    lag = max(term_days - 10, min(term_days + 18, lag))
    if rng.random() < 0.08:
        # A late payer, but never past terms + 45 days (the stability
        # invariant's aging bound) once the weekend roll is applied.
        lag += rng.randint(14, 25)
    return lag


def _quarter_hours(rng: random.Random, lo: float, hi: float) -> Decimal:
    """Billable hours in quarter-hour steps between ``lo`` and ``hi``."""
    return D(rng.randint(int(lo * 4), int(hi * 4))) / D(4)


def _plan(client: str, anchor: date, through: date, description: str,
          hours: Decimal | None = None, job: tuple[str, str] | None = None,
          date_open: date | None = None) -> dict | None:
    """One invoice plan entry, or None when it opens past ``through``."""
    name, currency, ar, rate, term = CLIENTS[client]
    date_open = date_open or _open_date(anchor)
    if date_open > through:
        return None
    if hours is None:
        amount = EMERALD_RETAINER
    else:
        amount = (hours * rate).quantize(D("0.01"))
    lag = _pay_lag(anchor, TERM_DAYS[term])
    discount = False
    if term == "2/10 Net 30":
        # Emerald takes the 2% when its cash allows — about a third of
        # the retainers, paid inside the 10-day window.
        rng = _seeded_rng("discount", anchor)
        if rng.random() < 0.35:
            discount = True
            # Inside the window even after a weekend roll (≤ day 9).
            lag = rng.randint(4, DISCOUNT_DAYS - 3)
    # Customer payments arrive by ACH — on business days (cold audit C3).
    date_pay = _next_bday(date_open + timedelta(days=lag))
    if currency == "USD":
        usd_value = amount
    else:
        usd_value = (amount * MD.fx(currency, "USD", date_open)).quantize(
            D("0.01"))
    return {
        "client": client, "customer": name, "currency": currency, "ar": ar,
        "date_open": date_open, "date_pay": date_pay, "amount": amount,
        "hours": hours, "rate": rate, "term": term, "discount": discount,
        "description": description, "job": job, "usd_value": usd_value,
        "paid": date_pay <= through,
    }


def _invoice_plans(through: date) -> list[dict]:
    """Every customer invoice from 2025-01 through ``through``, in
    open-date order."""
    plans: list[dict] = []

    # Emerald: the $3,500 monthly retainer (flat by contract).
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        anchor = date(yr, m, 1)
        p = _plan("emerald", anchor, through,
                  f"{anchor.strftime('%B %Y')} data-engineering retainer")
        if p:
            plans.append(p)

    # Sound Transit: project invoices three times a year, Net 15.
    for yr, m in _month_iter(date(YEAR, 2, 1), through):
        if m not in (2, 6, 10):
            continue
        anchor = date(yr, m, 3)
        hours = _quarter_hours(_seeded_rng("hours", anchor), 55, 80)
        p = _plan("sound_transit", anchor, through,
                  f"Data engineering services — {anchor.strftime('%B %Y')}",
                  hours=hours)
        if p:
            plans.append(p)

    # Berlin Digital: quarterly EUR engagements (the FX regression case).
    for yr, m in _month_iter(date(YEAR, 3, 1), through):
        if m not in (3, 6, 9, 12):
            continue
        anchor = date(yr, m, 8)
        hours = _quarter_hours(_seeded_rng("hours", anchor), 35, 52)
        p = _plan("berlin", anchor, through,
                  f"Berlin Digital engagement — {anchor.strftime('%B %Y')}",
                  hours=hours)
        if p:
            plans.append(p)

    # Nord Analytique: CAD invoices every other month.
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        if m % 2 == 0:
            continue
        anchor = date(yr, m, 12)
        hours = _quarter_hours(_seeded_rng("hours", anchor), 39, 55)
        p = _plan("nord", anchor, through,
                  f"Nord Analytique data services — {anchor.strftime('%B %Y')}",
                  hours=hours)
        if p:
            plans.append(p)

    # The four hourly SOW clients: one of them most months (gaps are
    # realistic for a contractor), billed on the 15th ± jitter.
    sow_clients = ["techstartup", "dataflow", "cloudnine", "wintertech"]
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        anchor = date(yr, m, 15)
        rng = _seeded_rng("sow", anchor)
        if rng.random() > 0.66:
            continue
        client = rng.choice(sow_clients)
        hours = _quarter_hours(rng, 30, 48)
        p = _plan(client, anchor, through,
                  f"Contract development — {anchor.strftime('%B %Y')}",
                  hours=hours)
        if p:
            plans.append(p)

    plans.sort(key=lambda p: (p["date_open"], p["customer"]))
    return plans


def _berlin_recent_open_date(through: date) -> date | None:
    """Open date for the deliberately-outstanding recent Berlin EUR
    invoice (~20 days before ``through``), or None if too early."""
    when = through - timedelta(days=20)
    return when if when >= date(YEAR, 1, 1) else None


def _berlin_extra_plan(through: date) -> dict | None:
    """The horizon-anchored Berlin change order left OUTSTANDING so EUR
    A/R carries a live foreign-currency balance near the horizon. Its
    description is distinct from the quarterly engagement so two June
    invoices never read as a double bill (audit B9)."""
    when = _berlin_recent_open_date(through)
    if when is None:
        return None
    hours = _quarter_hours(_seeded_rng("hours", when), 30, 45)
    return _plan("berlin", when, through,
                 f"Berlin Digital — sprint change order ({when.strftime('%B %Y')})",
                 hours=hours, date_open=when)


# Jobs: multi-invoice projects over customers. Milestones are dated
# BACKWARD from ``through`` so each job's OUTSTANDING milestone lands
# in a realistic recent aging window (``anchor_days`` = days before
# ``through`` that the job's LAST milestone opens).
JOB_SPECS = [
    ("sound_transit", "Sound Transit Q1 Migration", "ST-MIG-Q1",
     [D("30"), D("30")], 95),
    ("emerald", "Emerald Dashboard Revamp", "EM-DASH",
     [D("40"), D("36.5")], 28),
    ("sound_transit", "Sound Transit Realtime Feed", "ST-RT",
     [D("48")], 12),
]
JOB_RATE = D("150")


def _job_plans(through: date) -> list[dict]:
    plans: list[dict] = []
    for client, jname, jref, milestone_hours, anchor_days in JOB_SPECS:
        last_open = through - timedelta(days=anchor_days)
        first_open = last_open - timedelta(days=30 * (len(milestone_hours) - 1))
        if first_open < date(YEAR, 1, 1):
            continue
        for i, hours in enumerate(milestone_hours):
            mo = first_open + timedelta(days=30 * i)
            if mo > through:
                break
            name, currency, ar, _rate, term = CLIENTS[client]
            amount = (hours * JOB_RATE).quantize(D("0.01"))
            plans.append({
                "client": client, "customer": name, "currency": currency,
                "ar": ar, "date_open": mo,
                "date_pay": _next_bday(mo + timedelta(days=_pay_lag(mo, 30))),
                "amount": amount, "hours": hours, "rate": JOB_RATE,
                "term": "Net 30", "discount": False,
                "description": f"{jname} — milestone {i + 1}",
                "job": (jname, jref), "usd_value": amount,
            })
    for p in plans:
        p["paid"] = p["date_pay"] <= through
    return plans


def _all_invoice_plans(through: date) -> list[dict]:
    """The full customer-invoice calendar in open-date order: the
    recurring plans, the jobs' milestones, the Berlin change order."""
    plans = _invoice_plans(through) + _job_plans(through)
    extra = _berlin_extra_plan(through)
    if extra is not None:
        extra["paid"] = False
        plans.append(extra)
    plans.sort(key=lambda p: (p["date_open"], p["customer"],
                              p["description"]))
    return plans


def business_event_price_dates(through: date) -> list[tuple[str, date]]:
    """EUR + CAD price dates needed for invoice post + pay (real rates).

    Every open date needs a rate (post); every settled invoice also needs
    a rate on its pay date for the cross-currency realized FX gain/loss.
    """
    out: list[tuple[str, date]] = []
    for inv in _all_invoice_plans(through):
        if inv["currency"] == "USD":
            continue
        out.append((inv["currency"], inv["date_open"]))
        if inv["paid"]:
            out.append((inv["currency"], inv["date_pay"]))
    return out


def _party_by_name(book: GnuCashBook, name: str) -> dict:
    """Find an existing customer/vendor row by exact name (continuation
    mode — entities live in the frozen prefix and are never recreated)."""
    for lister in (book.list_customers, book.list_vendors):
        env = lister(compact=False, limit=250)
        rows = next((v for v in env.values() if isinstance(v, list)), [])
        for row in rows:
            if row.get("name") == name:
                return row
    raise SystemExit(f"continuation: party {name!r} not found in book")


def run_business(book: GnuCashBook, through: date,
                 since: date | None = None) -> dict:
    """Create billterms, customers, vendors, invoices, bills, and jobs.

    Documents are created in open-date order so IDs ascend with dates
    (audit B8). Invoices whose payment date is inside ``through`` are
    paid into the LLC account (Berlin's and Nord's settle cross-currency
    with realized FX gain/loss; a third of Emerald's take the 2/10
    discount); the rest stay POSTED-BUT-UNPAID, so the book shows
    outstanding receivables in USD, EUR and CAD. Vendor bills carry the
    vendor's own numbers and are paid from the LLC account (JetBrains
    from the Business Amex).

    ``since`` (continuation mode): entities and jobs already exist in
    the frozen prefix — look them up instead of creating; emit only
    documents opened AFTER ``since``. The ``through``-relative "recent
    open" extras are re-anchored each continuation but skipped while
    their predecessor is still outstanding (a quarterly client doesn't
    invoice monthly just because the updater runs monthly).
    """
    counts = {"customers": 0, "vendors": 0, "invoices": 0, "bills": 0,
              "terms": 0, "employees": 0, "jobs": 0, "open_invoices": 0,
              "discounts_taken": 0}

    open_owner_names: set[str] = set()
    customers: dict[str, dict] = {}
    vendors: dict[str, dict] = {}
    if since is None:
        book.create_billterm(name="Net 15", due_days=15,
                             description="Payment due within 15 days")
        book.create_billterm(name="Net 30", due_days=30,
                             description="Payment due within 30 days")
        book.create_billterm(name="2/10 Net 30", due_days=30,
                             discount_days=DISCOUNT_DAYS,
                             discount_percent="2",
                             description="2% discount if paid in 10 days, else net 30")
        counts["terms"] = 3

        # Customers, with the master data a document needs (audit B10).
        for key, (name, currency, _ar, _rate, _term) in CLIENTS.items():
            customers[key] = book.create_customer(
                name=name, currency=currency, notes=CUSTOMER_NOTES[key],
                address={"name": name, **CUSTOMER_ADDRESSES[key]})
        counts["customers"] = len(customers)

        for key, (name, notes, address) in VENDORS.items():
            vendors[key] = book.create_vendor(
                name=name, currency="USD", notes=notes,
                address={"name": name, **address})
        counts["vendors"] = len(vendors)
        # No employee: the LLC has no payroll (audit A2), and a W-2
        # employee record without wages is the finding, not a feature.
    else:
        for key, (name, *_rest) in CLIENTS.items():
            customers[key] = _party_by_name(book, name)
        for key, (name, *_rest) in VENDORS.items():
            vendors[key] = _party_by_name(book, name)
        env = book.get_outstanding_invoices(compact=False, limit=250)
        open_owner_names = {
            doc.get("owner_name") for doc in env.get("invoices", [])
        }

    def run_invoice(plan: dict, job_id: str | None = None):
        """Create + post an invoice from a plan entry; pay it when its
        payment date is inside ``through``. Returns the invoice id
        (None when skipped by ``since``)."""
        date_open, date_pay = plan["date_open"], plan["date_pay"]
        if since is not None and date_open <= since:
            return None
        currency = plan["currency"]
        cross = currency != "USD"
        inv = book.create_invoice(
            customer_id=customers[plan["client"]]["id"],
            date_opened=date_open.isoformat(),
            currency=currency, term=plan["term"], job_id=job_id,
        )
        if plan["hours"] is None:
            book.add_invoice_entry(
                invoice_id=inv["id"], account=LLC_REVENUE,
                description=plan["description"], quantity="1",
                price=str(plan["amount"]), action="Project",
            )
        else:
            book.add_invoice_entry(
                invoice_id=inv["id"], account=LLC_REVENUE,
                description=plan["description"],
                quantity=str(plan["hours"]), price=str(plan["rate"]),
                action="Hours",
            )
        book.post_invoice(
            invoice_id=inv["id"], post_account=plan["ar"],
            post_date=date_open.isoformat(), owner_type="customer",
            force=cross,
        )
        # The horizon clamp lives HERE, not at the callers: a stream
        # whose open date is inside ``through`` but whose pay date is
        # not must leave the document open — the continuation's aging
        # pass settles it on a later run (the closed loop's job).
        if plan["paid"] and date_pay <= through:
            amount = plan["amount"]
            if plan["discount"]:
                amount = (amount * (1 - DISCOUNT_PCT)).quantize(D("0.01"))
                book.pay_invoice(
                    invoice_id=inv["id"], payment_account=LLC_CHECKING,
                    amount=str(amount), payment_date=date_pay.isoformat(),
                    owner_type="customer", apply_discount=True,
                    discount_account=EXP_SALES_DISC,
                )
                counts["discounts_taken"] += 1
            else:
                book.pay_invoice(
                    invoice_id=inv["id"], payment_account=LLC_CHECKING,
                    amount=str(amount), payment_date=date_pay.isoformat(),
                    owner_type="customer", force=cross,
                )
        else:
            counts["open_invoices"] += 1
        counts["invoices"] += 1
        return inv["id"]

    existing_jobs: set[str] = set()
    if since is not None:
        env = book.list_jobs(compact=False, limit=250)
        rows = next((v for v in env.values() if isinstance(v, list)), [])
        existing_jobs = {row.get("name") for row in rows}
    job_ids: dict[str, str] = {}

    for plan in _all_invoice_plans(through):
        if plan["job"] is not None:
            jname, jref = plan["job"]
            if jname in existing_jobs:
                # Prefix narrative: the job and its milestones exist;
                # the settlement pass ages its open milestones.
                continue
            if jname not in job_ids:
                job = book.create_job(
                    owner_id=customers[plan["client"]]["id"],
                    owner_type="customer", name=jname, reference=jref,
                )
                job_ids[jname] = job["id"]
                counts["jobs"] += 1
            run_invoice(plan, job_id=job_ids[jname])
            continue
        if (plan["description"].startswith("Berlin Digital — sprint change order")
                and "Berlin Digital GmbH" in open_owner_names):
            continue  # predecessor still outstanding — don't stack
        run_invoice(plan)

    # Vendor bills (paid from the LLC account; JetBrains from the
    # Business Amex). IDs are the VENDOR's invoice numbers.
    def run_bill(vendor_key, bill_id, date_open, date_pay, description,
                 expense_account, quantity="1", price=None, amount=None,
                 term="Net 30", payment_account=LLC_CHECKING, paid=True):
        if since is not None and date_open <= since:
            return None
        date_pay = _next_bday(date_pay)  # ACH out of the LLC account
        bill = book.create_bill(
            vendor_id=vendors[vendor_key]["id"],
            date_opened=date_open.isoformat(), term=term, bill_id=bill_id,
        )
        total = amount if amount is not None else (
            D(quantity) * D(price)).quantize(D("0.01"))
        book.add_bill_entry(
            bill_id=bill["id"], account=expense_account,
            description=description, quantity=quantity,
            price=str(price if price is not None else amount),
            action="Hours" if quantity != "1" else "",
        )
        book.post_invoice(
            invoice_id=bill["id"], post_account=AP,
            post_date=date_open.isoformat(), owner_type="vendor",
        )
        # Same horizon clamp as run_invoice, for the same reason.
        if paid and date_pay <= through:
            book.pay_invoice(
                invoice_id=bill["id"], payment_account=payment_account,
                amount=str(total), payment_date=date_pay.isoformat(),
                owner_type="vendor",
            )
        counts["bills"] += 1
        return bill["id"]

    # JetBrains annual ($289), every January, paid from the Business Amex.
    for yr in range(YEAR, through.year + 1):
        when = date(yr, 1, 12)
        if when <= through:
            run_bill(
                "jetbrains", f"JB-INV-{yr}-0112", when,
                when + timedelta(days=13), amount=D("289.00"),
                description="IntelliJ IDEA Ultimate — annual subscription",
                expense_account=EXP_SOFTWARE, payment_account=AMEX,
            )

    # BookkeepingCo quarterly ($450): ONE document per quarter (cold
    # audit B6 — the separate horizon-anchored "recent bill" duplicated
    # the quarter's review), opened on the 8th of the quarter's last
    # month and paid on its Net 30 terms, so the most recent one is
    # naturally still open at the horizon. The continuation's aging
    # pass settles it later.
    for yr, m in _month_iter(date(YEAR, 3, 1), through):
        if m not in (3, 6, 9, 12):
            continue
        date_open = date(yr, m, 8)
        if date_open > through:
            continue
        run_bill(
            "bookkeeper", f"BKC-{yr}-{m:02d}08", date_open,
            date_open + timedelta(days=_pay_lag(date_open, 30)),
            amount=D("450.00"),
            description=f"Quarterly bookkeeping review - Q{(m - 1) // 3 + 1} {yr}",
            expense_account=EXP_ACCOUNTING,
        )

    # Sam Rivera: monthly hourly bills to Contractor Payments, Net 15,
    # paid from the LLC account (audit A2 option 1). A 1099 contractor
    # bills in arrears: the bill for a month opens on that month's last
    # business day (cold audit A3), so December's work is a December
    # accrual paid in January — the cash paid in the year is what the
    # 1099-NEC reports.
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        anchor = date(yr, m, 1)
        date_open = _last_bday(yr, m)
        if date_open > through:
            continue
        hours = _quarter_hours(_seeded_rng("sam-hours", anchor), 30, 45)
        run_bill(
            "sam", f"SR-{yr}-{m:02d}", date_open,
            date_open + timedelta(days=_pay_lag(anchor, 15)),
            quantity=str(hours), price=SAM_RATE,
            description=f"Contract development — {anchor.strftime('%B %Y')}",
            expense_account=EXP_CONTRACTOR, term="Net 15",
        )

    return counts


# ── Phase 8: Investment activity ────────────────────────────────

ACCT_BY_SYMBOL = {"VTSAX": VTSAX, "VBTLX": VBTLX, "AAPL": AAPL,
                  "MSFT": MSFT, "ETH": ETH}
OPENING_LOT_TITLE = {
    "VTSAX": "VTSAX core position", "VBTLX": "VBTLX bond allocation",
    "AAPL": "AAPL 2023 purchase", "MSFT": "MSFT 2024 purchase",
    "ETH": "ETH 2024 purchase",
}
# Where each holding is custodied — the statement line's payee (audit C6).
CUSTODIAN = {"VTSAX": "Vanguard", "VBTLX": "Vanguard", "AAPL": "Vanguard",
             "MSFT": "Vanguard", "ETH": "Coinbase"}

# Monthly DCA: $500 VTSAX on the 3rd, $200 VBTLX on the 17th (spread
# off the 1st — audit C5). Funds → fractional shares.
DCA_PLAN = [("VTSAX", 3, D("500.00")), ("VBTLX", 17, D("200.00"))]

# (month, day, action, symbol, shares) — price comes from real market
# data. The 2025 calendar: the December sale trims the bond sleeve
# toward year-end cash — 100 VBTLX from the opening lot (a GAIN at the
# real Dec-15 close against the lot's $9.62 basis, so §1091 never
# enters — audit A1). There is no same-week rebuy (cold audit B2: an
# unmotivated one-day round trip); the monthly DCA keeps accumulating.
QUARTERLY_TRADES = [
    (3, 10, "buy", "AAPL", D("5.0000")),
    (5, 15, "sell", "AAPL", D("3.0000")),
    (7, 20, "buy", "ETH", D("0.500000")),
    (8, 15, "buy", "MSFT", D("10.0000")),
    (10, 8, "sell", "ETH", D("1.000000")),
    (11, 18, "sell", "MSFT", D("5.0000")),
    (12, 15, "sell", "VBTLX", D("100.0000")),
]

# Distributions are shares held × a per-share rate (audit B6/C8), so a
# growing position pays a growing dividend. The rates approximate the
# funds'/issuers' declared 2025 distributions; they are the ONLY
# investment constants (prices are always the real cache quotes).
#   (month, day, symbol, per-share rate)
DIVIDENDS_PLAN = [
    (3, 21, "VTSAX", D("0.4165")), (6, 27, "VTSAX", D("0.4408")),
    (9, 26, "VTSAX", D("0.4527")), (12, 19, "VTSAX", D("0.5164")),
    (2, 13, "AAPL", D("0.25")), (5, 15, "AAPL", D("0.26")),
    (8, 14, "AAPL", D("0.26")), (11, 13, "AAPL", D("0.26")),
    (3, 13, "MSFT", D("0.83")), (6, 12, "MSFT", D("0.83")),
    (9, 11, "MSFT", D("0.91")), (12, 11, "MSFT", D("0.91")),
]
# VBTLX distributes monthly (~3.7% yield), reinvested.
VBTLX_MONTHLY_RATE = D("0.0305")
VBTLX_DIST_DAY = 28


def investment_event_price_dates(through: date) -> list[tuple[str, date]]:
    """All security price dates needed for trades + distributions (real
    data), spanning the full activity window through ``through``."""
    out: list[tuple[str, date]] = []
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        for sym, day, _amt in DCA_PLAN:
            d = _next_bday(_clamp_day(yr, m, day))
            if d <= through:
                out.append((sym, d))
        d = _next_bday(_clamp_day(yr, m, VBTLX_DIST_DAY))
        if d <= through:
            out.append(("VBTLX", d))
    # 2025 fixed quarterly trades.
    for m, day, _a, sym, _sh in QUARTERLY_TRADES:
        out.append((sym, _next_bday(date(YEAR, m, day))))
    # 2026+ whole-share stock buys (Feb/May/Aug/Nov, 10th).
    for yr, m in _month_iter(date(YEAR + 1, 1, 1), through):
        if m in (2, 5, 8, 11):
            d = _next_bday(date(yr, m, 10))
            if d <= through:
                out.append(("AAPL", d))
                out.append(("MSFT", d))
    # Dividends, replayed each year.
    for yr in range(YEAR, through.year + 1):
        for m, day, sym, _rate in DIVIDENDS_PLAN:
            d = _next_bday(date(yr, m, day))
            if d <= through:
                out.append((sym, d))
    return out


def _shares_from_usd(usd: Decimal, price: Decimal, fraction: int) -> Decimal:
    places = D(1) / D(fraction)
    return (usd / price).quantize(places)


def _holdings_as_of(book, cut: date) -> dict[str, Decimal]:
    """Shares held per symbol at the close of ``cut`` — read from the
    book, so a continuation's distributions scale from the real
    position rather than a replayed one."""
    acct = {a.fullname: a for a in book.accounts}
    held: dict[str, Decimal] = {}
    for sym, path in ACCT_BY_SYMBOL.items():
        total = D("0")
        for s in acct[path].splits:
            post = s.transaction.post_date
            if hasattr(post, "date"):
                post = post.date()
            if post <= cut:
                total += Decimal(str(s.quantity))
        held[sym] = total
    return held


def run_investments(out_path: Path, through: date,
                    since: date | None = None) -> dict:
    """Monthly DCA, the 2025 trade calendar, per-share distributions,
    plus recent whole-share stock buys — continuing through
    ``through``. Direct piecash. Events are walked in date order with
    a running share count so every distribution is shares × rate.
    ``since`` (continuation mode): skip every event dated on or before
    it — those trades and lots already exist in the frozen prefix,
    and re-running them would double-create."""
    cut = since or date(YEAR, 1, 1) - timedelta(days=1)
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    counts = {"txns": 0, "lots": 0}
    try:
        usd = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        frac = {s[0]: s[3] for s in SECURITIES}
        # Opening positions are dated the book's first day, one day
        # after the base build's cut — read the holdings as of that day
        # so the first distributions scale from the real positions.
        held = _holdings_as_of(book, max(cut, date(YEAR, 1, 1)))

        def find_lot(title):
            for a in book.accounts:
                for lot in a.lots:
                    if lot.title == title:
                        return lot
            return None

        # ── Build the event calendar ──
        # Trades and distributions settle on business days (cold audit
        # C3) — the same roll ``investment_event_price_dates`` applies.
        events: list[tuple[date, int, str, tuple]] = []
        for yr, m in _month_iter(date(YEAR, 1, 1), through):
            for sym, day, amt in DCA_PLAN:
                events.append((_next_bday(_clamp_day(yr, m, day)), 0, "dca",
                               (sym, amt)))
            events.append((_next_bday(_clamp_day(yr, m, VBTLX_DIST_DAY)), 3,
                           "dist", ("VBTLX", VBTLX_MONTHLY_RATE)))
        for yr, m in _month_iter(date(YEAR + 1, 1, 1), through):
            if m in (2, 5, 8, 11):
                d = _next_bday(date(yr, m, 10))
                for sym, n in (("AAPL", 2), ("MSFT", 1)):
                    events.append((d, 1, "stock", (sym, D(n))))
        for m, day, action, sym, shares in QUARTERLY_TRADES:
            events.append((_next_bday(date(YEAR, m, day)), 2, action,
                           (sym, shares)))
        for yr in range(YEAR, through.year + 1):
            for m, day, sym, rate in DIVIDENDS_PLAN:
                events.append((_next_bday(date(yr, m, day)), 3, "dist",
                               (sym, rate)))
        events.sort(key=lambda e: (e[0], e[1], e[2], str(e[3])))

        for d, _seq, kind, payload in events:
            if d > through:
                continue
            if d <= cut:
                continue
            sym = payload[0]
            inv_acct = acct[ACCT_BY_SYMBOL[sym]]
            who = CUSTODIAN[sym]
            price = MD.security(sym, d).quantize(_security_quant(sym))

            if kind == "dca":
                amt = payload[1]
                shares = _shares_from_usd(amt, price, frac[sym])
                lot = piecash.Lot(
                    title=f"{sym} DCA {d.isoformat()}", account=inv_acct,
                    notes=f"Monthly auto-invest — ${amt} @ ${price}", is_closed=0,
                )
                inv_split = piecash.Split(
                    account=inv_acct, value=amt, quantity=shares)
                cash_split = piecash.Split(account=acct[CHECKING], value=-amt)
                piecash.Transaction(
                    currency=usd, description=f"{who} — Buy {sym}",
                    notes=f"Auto-invest ${amt} @ ${price} = {shares} sh",
                    post_date=d, enter_date=_enter_stamp(d),
                    splits=[inv_split, cash_split])
                inv_split.lot = lot
                held[sym] += shares
                counts["txns"] += 1
                counts["lots"] += 1

            elif kind in ("stock", "buy"):
                shares = payload[1]
                usd_amt = (shares * price).quantize(D("0.01"))
                lot = piecash.Lot(
                    title=f"{sym} buy {d.isoformat()}", account=inv_acct,
                    notes=f"{shares} sh @ ${price}", is_closed=0,
                )
                inv_split = piecash.Split(
                    account=inv_acct, value=usd_amt, quantity=shares)
                cash_split = piecash.Split(
                    account=acct[CHECKING], value=-usd_amt)
                note = f"{shares} sh @ ${price} = ${usd_amt:,.2f}"
                piecash.Transaction(
                    currency=usd, description=f"{who} — Buy {sym}",
                    notes=note, post_date=d, enter_date=_enter_stamp(d),
                    splits=[inv_split, cash_split])
                inv_split.lot = lot
                held[sym] += shares
                counts["txns"] += 1
                counts["lots"] += 1

            elif kind == "sell":
                shares = payload[1]
                usd_amt = (shares * price).quantize(D("0.01"))
                lot = find_lot(OPENING_LOT_TITLE[sym])
                opening_split = lot.splits[0]
                cost_per = (Decimal(str(opening_split.value))
                            / Decimal(str(opening_split.quantity)))
                cost_basis = (shares * cost_per).quantize(D("0.01"))
                # Realized P/L = sale proceeds − cost basis, SIGNED:
                #   above cost → gain > 0 ; below cost → gain < 0 (a LOSS).
                # Capital Gains is a credit-normal INCOME account, so a gain is
                # a credit (value = −gain < 0) and a loss is a debit
                # (value = −gain > 0) that REDUCES capital-gains income. The
                # three splits sum to zero either way:
                #   (−cost_basis) + usd_amt + (−gain)
                #   = −cost_basis + usd_amt − (usd_amt − cost_basis) = 0.
                gain = usd_amt - cost_basis
                inv_split = piecash.Split(
                    account=inv_acct, value=-cost_basis, quantity=-shares)
                cash_split = piecash.Split(account=acct[CHECKING], value=usd_amt)
                gain_split = piecash.Split(
                    account=acct[CAPITAL_GAINS], value=-gain)
                assert (-cost_basis) + usd_amt + (-gain) == 0
                kind_word = "gain" if gain >= 0 else "loss"
                piecash.Transaction(
                    currency=usd, description=f"{who} — Sell {sym}",
                    notes=f"{shares} sh @ ${price} = ${usd_amt:,.2f}; "
                          f"lot '{lot.title}' basis ${cost_basis:,.2f}, "
                          f"realized {kind_word} ${abs(gain):,.2f}",
                    post_date=d, enter_date=_enter_stamp(d),
                    splits=[inv_split, cash_split, gain_split])
                inv_split.lot = lot
                held[sym] -= shares
                counts["txns"] += 1

            elif kind == "dist":
                rate = payload[1]
                amt = (held[sym] * rate).quantize(D("0.01"))
                if amt <= 0:
                    continue
                if sym in ("AAPL", "MSFT"):
                    # Cash dividend → Checking (no new shares).
                    cash_split = piecash.Split(
                        account=acct[CHECKING], value=amt)
                    income_split = piecash.Split(
                        account=acct[DIVIDENDS], value=-amt)
                    piecash.Transaction(
                        currency=usd,
                        description=f"{who} — {sym} dividend",
                        notes=f"{held[sym]} sh × ${rate}/sh, paid in cash",
                        post_date=d, enter_date=_enter_stamp(d),
                        splits=[cash_split, income_split])
                    counts["txns"] += 1
                else:
                    # Funds reinvest — fractional DRIP shares.
                    shares = _shares_from_usd(amt, price, frac[sym])
                    lot = piecash.Lot(
                        title=f"{sym} dividend {d.isoformat()}",
                        account=inv_acct,
                        notes=f"Reinvested distribution — ${amt} @ ${price}",
                        is_closed=0,
                    )
                    inv_split = piecash.Split(
                        account=inv_acct, value=amt, quantity=shares)
                    income_split = piecash.Split(
                        account=acct[DIVIDENDS], value=-amt)
                    piecash.Transaction(
                        currency=usd,
                        description=f"{who} — {sym} distribution reinvested",
                        notes=f"{held[sym]} sh × ${rate}/sh = ${amt} "
                              f"@ ${price} → {shares} sh",
                        post_date=d, enter_date=_enter_stamp(d),
                        splits=[inv_split, income_split])
                    inv_split.lot = lot
                    held[sym] += shares
                    counts["txns"] += 1
                    counts["lots"] += 1

        book.save()
    finally:
        book.close()
    return counts


# ── Phase 9: Credit card lifecycle ──────────────────────────────

# 2025 narrative on the cards (audit D6 keeps it): Chase carries a
# balance Jan–May with a $500 payment and real interest, pays off in
# June, then pays the statement in full; the Business Amex misses its
# August cycle (partial payment, late fee, interest), catches up in
# September. Every payment is the ACTUAL statement balance computed
# from the book (audit B1/B2) — never a fixed amount against variable
# spending — so neither card can drift past its limit. The payment
# lag and descriptions come from the same policy the continuation
# runs, so the base and every later month read identically.
CHASE_MIN_PAYMENT = D("500.00")
CHASE_PAYOFF_MONTH = (YEAR, 6)
AMEX_LATE_MONTH = (YEAR, 8)
AMEX_LATE_PARTIAL = D("200.00")
AMEX_LATE_FEE = D("29.00")


def _card_running(book, path: str) -> list[tuple[date, Decimal]]:
    """(date, value) for every split on a card account, in date order."""
    acct = {a.fullname: a for a in book.accounts}
    rows: list[tuple[date, Decimal]] = []
    for s in acct[path].splits:
        post = s.transaction.post_date
        if hasattr(post, "date"):
            post = post.date()
        rows.append((post, Decimal(str(s.value))))
    rows.sort()
    return rows


def run_credit_cards(out_path: Path, through: date) -> int:
    """Statement payments (and carried-balance interest) for both
    cards from the first 2025 close through the last close whose
    payment lands inside ``through``. Reads the charges already in the
    book and walks the cycles sequentially, so each payment is the
    true statement balance."""
    from continuation import _seeded

    book = piecash.open_book(str(out_path), readonly=True, open_if_lock=True)
    try:
        charges = {card.account: _card_running(book, card.account)
                   for card in POLICY.cards}
    finally:
        book.close()

    txns: list[dict] = []
    for card in POLICY.cards:
        rows = list(charges[card.account])
        apr = D("21.49") if card.account == CHASE else D("24.49")
        pay_from = card.pay_from or POLICY.checking
        # The business card's interest and fees are Schedule C lines;
        # the personal card's are not (cold audit A4).
        interest_acct = EXP_BIZ_CARD_FEES if card.account == AMEX else EXP_CC_INT
        fee_acct = EXP_BIZ_CARD_FEES if card.account == AMEX else EXP_BANK_CHARGES

        def balance_at(when: date) -> Decimal:
            # Liability: owed is the negative of the running value.
            return -sum((v for d, v in rows if d <= when), D("0"))

        y, m = YEAR, 1
        carried = D("0")
        while True:
            close = _clamp_day(y, m, card.close_day_default)
            pay_lag = _seeded(POLICY.key, f"paylag:{card.label}", close, 3, 7)
            # The payment is an ACH pull — business days only (C3);
            # the same roll the policy engine applies from the frozen
            # edge onward.
            pay_date = _next_bday(close + timedelta(days=pay_lag))
            if pay_date > through:
                break
            month = close.strftime("%B %Y")

            # Interest on a balance carried from the previous cycle
            # posts at this close (audit B1: booked whenever it carries).
            if carried > 0:
                interest = (carried * apr / D("100") / D("12")).quantize(
                    D("0.01"))
                if interest > 0:
                    txns.append({
                        "description": POLICY.desc_interest.format(
                            label=card.label),
                        "date": close,
                        "notes": f"Purchase APR {apr}% on the "
                                 f"${carried:,.2f} carried balance",
                        "splits": [(card.account, -interest),
                                   (interest_acct, interest)],
                    })
                    rows.append((close, -interest))
                    rows.sort()

            owed = balance_at(close)
            if owed <= 0:
                carried = D("0")
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
                continue

            # Narrative: Chase minimum-plus until the June payoff;
            # Amex's missed August. The bank line is the plain
            # statement payment; the story goes in the notes (cold
            # audit C4).
            desc = POLICY.desc_statement.format(label=card.label, month=month)
            notes = ""
            if card.account == CHASE and (y, m) < CHASE_PAYOFF_MONTH:
                payment = min(owed, CHASE_MIN_PAYMENT)
                notes = (f"Minimum-plus payment; ${owed - payment:,.2f} "
                         f"carried at {apr}% APR")
            elif card.account == AMEX and (y, m) == AMEX_LATE_MONTH:
                payment = min(owed, AMEX_LATE_PARTIAL)
                notes = (f"Partial payment after the due date — "
                         f"${owed - payment:,.2f} carried; late fee and "
                         f"interest follow")
                txns.append({
                    "description": f"{card.label} — late payment fee",
                    "date": close + timedelta(days=10),
                    "notes": f"Late fee on the {month} statement",
                    "splits": [(card.account, -AMEX_LATE_FEE),
                               (fee_acct, AMEX_LATE_FEE)],
                })
                rows.append((close + timedelta(days=10), -AMEX_LATE_FEE))
                rows.sort()
            else:
                payment = owed
                if card.account == CHASE and (y, m) == CHASE_PAYOFF_MONTH:
                    notes = "Balance paid off in full; paid in full monthly from here"
                elif (card.account == AMEX
                        and (y, m) == (AMEX_LATE_MONTH[0], AMEX_LATE_MONTH[1] + 1)):
                    notes = "Catch-up: the missed August cycle plus this statement"

            payment = payment.quantize(D("0.01"))
            txns.append({
                "description": desc, "date": pay_date, "notes": notes,
                "splits": [(pay_from, -payment), (card.account, payment)],
            })
            rows.append((pay_date, payment))
            rows.sort()
            carried = owed - payment
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    return write_bulk(out_path, txns)


# ── Phase 10: Budget ────────────────────────────────────────────

BUDGET_MONTHLY = [
    (EXP_GROCERIES, D("400")), (EXP_DINING, D("350")), (EXP_HOA, D("425")),
    (EXP_FUEL, D("220")), (EXP_STREAMING, D("46")), (EXP_CLOTHING, D("150")),
    (EXP_TRAVEL, D("300")), (EXP_CLOUD, D("150")), (EXP_GIFTS, D("100")),
    (EXP_CHARITY, D("50")), (EXP_MISC, D("200")),
    # Parent rollups (placeholders).
    ("Expenses:Utilities", D("450")), ("Expenses:Pet", D("70")),
]
# Seasonal overrides (period 0-indexed: Jun=5, Jul=6, Aug=7, Nov=10).
BUDGET_SEASONAL = [
    (EXP_TRAVEL, 5, D("600")), (EXP_TRAVEL, 6, D("600")),
    (EXP_TRAVEL, 7, D("600")), (EXP_GIFTS, 10, D("800")),
    (EXP_CHARITY, 10, D("500")),
]


def run_budget(book: GnuCashBook, through: date) -> int:
    """A budget for every calendar year in the book: 2025's plan, then
    each later year rolled forward at +3% (audit B17 — the report tool
    must have a budget covering today)."""
    n = 0
    for yr in range(YEAR, through.year + 1):
        factor = D("1.03") ** (yr - YEAR)
        name = f"{yr} Annual Budget"
        desc = (f"Alex & Robin {yr} household budget"
                + ("" if yr == YEAR else f" (rolled forward from {yr - 1} +3%)"))
        book.create_budget(name=name, year=yr, num_periods=12,
                           period_type="monthly", description=desc)
        for acct, amt in BUDGET_MONTHLY:
            book.set_budget_amount(
                budget_name=name, account=acct,
                amount=str((amt * factor).quantize(D("1"))), period="all")
        for acct, period, amt in BUDGET_SEASONAL:
            book.set_budget_amount(
                budget_name=name, account=acct,
                amount=str((amt * factor).quantize(D("1"))), period=period)
        n += 1
    return n


# ── Phase 12: Edge cases ────────────────────────────────────────

def run_edge_cases(book: GnuCashBook) -> dict:
    """Voided, recategorized, returned/refunded, split-corrected, deleted."""
    info = {}

    # 1. Voided: $500 to Wrong Vendor on 03/15.
    r = book.create_transaction(
        description="Payment to Wrong Vendor (mis-routed)",
        trans_date=date(YEAR, 3, 15),
        splits=[{"account": CHECKING, "amount": "-500.00"},
                {"account": EXP_MISC, "amount": "500.00"}],
        check_duplicates=False,
    )
    book.void_transaction(guid=r["guid"], reason="Paid wrong vendor")
    info["voided_guid"] = r["guid"]
    # The void happened when the mistake was caught — the same day it
    # posted — not at the build moment (cold audit C2). The server
    # stamps ``void-time`` with the wall clock (its audit-log
    # convention); rewrite the slot in the same tz-aware ISO shape.
    void_when = datetime(YEAR, 3, 15, 16, 45, 0,
                         tzinfo=timezone(timedelta(hours=-7)))
    gc = piecash.open_book(str(book.book_path), readonly=False,
                           do_backup=False)
    try:
        # The server hands back a short GUID prefix; match on it.
        txn = gc.session.query(piecash.Transaction).filter(
            piecash.Transaction.guid.like(f"{r['guid']}%")).one()
        txn["void-time"] = void_when.isoformat()
        gc.save()
    finally:
        gc.close()

    # 2. Recategorized: $89 Office Supplies (Misc) -> Business:Office
    #    Supplies — charged to the business card (audit A4).
    r = book.create_transaction(
        description="Office Supplies (Amazon)",
        trans_date=date(YEAR, 4, 20),
        splits=[{"account": AMEX, "amount": "-89.00"},
                {"account": EXP_MISC, "amount": "89.00"}],
        check_duplicates=False,
    )
    book.replace_splits(
        guid=r["guid"],
        splits=[{"account": AMEX, "amount": "-89.00"},
                {"account": EXP_OFFICE, "amount": "89.00"}],
    )
    info["recategorized_guid"] = r["guid"]

    # 3. Returned purchase: $249 Electronics on 08/10, credit -$249 08/22.
    book.create_transaction(
        description="Electronics Store", trans_date=date(YEAR, 8, 10),
        splits=[{"account": CHASE, "amount": "-249.00"},
                {"account": EXP_MISC, "amount": "249.00"}],
        check_duplicates=False,
    )
    book.create_transaction(
        description="Electronics Store — return", trans_date=date(YEAR, 8, 22),
        splits=[{"account": CHASE, "amount": "249.00"},
                {"account": EXP_MISC, "amount": "-249.00"}],
        check_duplicates=False,
    )

    # 4. Partial refund: $120 Department Store 09/05, $45 refund 09/15.
    book.create_transaction(
        description="Department Store", trans_date=date(YEAR, 9, 5),
        splits=[{"account": CHASE, "amount": "-120.00"},
                {"account": EXP_CLOTHING, "amount": "120.00"}],
        check_duplicates=False,
    )
    book.create_transaction(
        description="Department Store — partial refund",
        trans_date=date(YEAR, 9, 15),
        splits=[{"account": CHASE, "amount": "45.00"},
                {"account": EXP_CLOTHING, "amount": "-45.00"}],
        check_duplicates=False,
    )

    # 5. Split correction: $150 Dining -> $120 Dining + $30 Gifts.
    r = book.create_transaction(
        description="Dinner + gift card combo purchase",
        trans_date=date(YEAR, 10, 1),
        splits=[{"account": CHASE, "amount": "-150.00"},
                {"account": EXP_DINING, "amount": "150.00"}],
        check_duplicates=False,
    )
    book.replace_splits(
        guid=r["guid"],
        splits=[{"account": CHASE, "amount": "-150.00"},
                {"account": EXP_DINING, "amount": "120.00"},
                {"account": EXP_GIFTS, "amount": "30.00"}],
    )
    info["split_correction_guid"] = r["guid"]

    # 6. Deleted duplicate grocery on 11/15.
    r = book.create_transaction(
        description="QFC (duplicate of 11/15)",
        trans_date=date(YEAR, 11, 15),
        splits=[{"account": CHECKING, "amount": "-92.50"},
                {"account": EXP_GROCERIES, "amount": "92.50"}],
        check_duplicates=False,
    )
    book.delete_transaction(guid=r["guid"])
    info["deleted_guid"] = r["guid"]

    return info


# ── Phase 13: Volume stress ─────────────────────────────────────

VOLUME_VENDORS = ["Morning Coffee", "Lunch Spot", "Parking Meter",
                  "Vending Machine", "Corner Store", "Food Cart",
                  "Transit Pass", "Drug Store", "Dry Cleaner", "News Stand"]


def gen_volume(through: date) -> list[dict]:
    rng = random.Random(SEED + 13)
    txns = []
    start = date(YEAR, 1, 1).toordinal()
    span = through.toordinal() - start
    # Scale the casual-spend volume with the elapsed span so a longer
    # book gets proportionally more incidental transactions.
    count = max(VOLUME_TXN_COUNT, int(VOLUME_TXN_COUNT * span / 365))
    for _ in range(count):
        dt = date.fromordinal(start + rng.randint(0, span))
        vendor = rng.choice(VOLUME_VENDORS)
        # Cents-bearing casual spend (was whole-dollar-prone uniform noise);
        # each merchant resolves to ONE consistent category (incl. the sticky
        # Vending Machine → Misc miscategorization).
        amt = _spend(rng, 2, 24)
        target = merchant_category(vendor, EXP_MISC)
        txns.append({"description": vendor, "date": dt,
                     "splits": [(CHECKING, -amt), (target, amt)]})
    return txns


# ── Phase 11: Reconciliation ────────────────────────────────────

def run_reconciliation(out_path: Path, through: date) -> list[str]:
    """Reconcile every bank/cash/card account through the last FULL
    month before ``through`` — the same posture pass the continuation
    runs (bookkeeper review §1; audit C10): a reconciliation tool's
    demo household is current to its last statement, with the open
    month left as the natural first conversation."""
    from continuation import reconcile_through
    return reconcile_through(POLICY, out_path, through)


# ── Scheduled-transaction state (stay ENABLED, realistic timing) ─

def set_schedule_state(out_path: Path, through: date) -> dict:
    """Stamp SX cursors via the shared engine rule: everything current,
    at most ONE schedule overdue and only when it "just came due" (3–7
    days — bookkeeper review §2). The old always-overdue Estimated Tax
    hook aged into looking like neglect; the due-soon line is Alex's
    hook now."""
    from continuation import advance_sx
    return advance_sx(out_path, through)


# ── Verification ────────────────────────────────────────────────

def _parse_money(s) -> Decimal:
    return Decimal(str(s).replace(",", "").replace("$", "").strip())


def verify(out_path: Path, through: date) -> None:
    print("\n" + "=" * 64)
    print("VERIFICATION")
    print("=" * 64)
    book = GnuCashBook(str(out_path))

    # Value the book as of THROUGH (the present horizon).
    as_of = through

    summary = book.get_book_summary()
    bs = book.balance_sheet(as_of_date=as_of)
    nw = book.net_worth(end_date=as_of)

    bs_assets = _parse_money(bs["assets"]["total"])
    bs_liab = _parse_money(bs["liabilities"]["total"])
    bs_nw = bs_assets - bs_liab
    nw_val = _parse_money(nw["net_worth"])

    print("\n-- (g) Cross-tool net worth agreement --")
    print(f"  balance_sheet: assets {bs_assets:,.2f} - liabilities "
          f"{bs_liab:,.2f} = net worth {bs_nw:,.2f}")
    print(f"  net_worth tool:                 {nw_val:,.2f}")
    print(f"  agree (bs vs net_worth):        {abs(bs_nw - nw_val) < 1}")

    # ── Realism: cents on consumer spend, consistent categorization, ──
    #    variable payroll withholding, no meta-notes.
    print("\n-- (R1) Realistic cents on consumer expense splits --")
    consumer_accts = {
        EXP_GROCERIES, EXP_DINING, EXP_FUEL, EXP_CLOTHING, EXP_MISC,
        EXP_MEDICAL, EXP_GIFTS, EXP_CHARITY, EXP_TRAVEL, EXP_ENTERTAINMENT,
        EXP_PERSONAL_CARE, EXP_EDUCATION, EXP_SUBSCRIPTIONS, EXP_PET_FOOD,
        EXP_PET_VET, EXP_HOUSING_MAINT,
    }
    # Genuinely-round items only (flat dues / round tax payments). Federal
    # withholding and amortized loan interest are COMPUTED values that
    # legitimately carry cents, so they're intentionally not listed here.
    structured_accts = {EXP_HOA, EXP_PROP_TAX, EXP_EST_TAX}
    with book.open() as b:
        cons_total = cons_cents = 0
        struct_total = struct_round = 0
        for t in b.transactions:
            for s in t.splits:
                fn = s.account.fullname
                v = Decimal(str(s.value))
                if v <= 0:
                    continue
                has_cents = (v % 1) != 0
                if fn in consumer_accts:
                    cons_total += 1
                    if has_cents:
                        cons_cents += 1
                elif fn in structured_accts:
                    struct_total += 1
                    if not has_cents:
                        struct_round += 1
    if cons_total:
        print(f"  consumer expense splits with cents: "
              f"{cons_cents}/{cons_total} "
              f"({100 * cons_cents / cons_total:.1f}%) — should be most")
    if struct_total:
        print(f"  structured splits that stay round (HOA/taxes/loan-int): "
              f"{struct_round}/{struct_total} "
              f"({100 * struct_round / struct_total:.1f}%)")

    print("\n-- (R2) Consistent merchant → category mapping --")
    with book.open() as b:
        merch_cats: dict[str, set[str]] = {}
        for t in b.transactions:
            d = (t.description or "").split(" - ")[0].split(" Gas")[0]
            for s in t.splits:
                if Decimal(str(s.value)) > 0 and \
                        s.account.type.upper() == "EXPENSE":
                    merch_cats.setdefault(d, set()).add(s.account.fullname)
        for sample in ("Morning Coffee", "Lunch Spot", "Corner Store",
                       "Parking Meter", "Transit Pass", "Drug Store",
                       "Vending Machine", "Starbucks", "QFC"):
            cats = merch_cats.get(sample)
            if cats:
                tag = "consistent" if len(cats) == 1 else "SCATTERED"
                print(f"    {sample:18s} -> {sorted(cats)} ({tag})")
        print("  sticky miscategorization: Vending Machine -> "
              f"{sorted(merch_cats.get('Vending Machine', set()))} "
              "(always Miscellaneous — the standing auto-rule error)")

    print("\n-- (R3) Variable payroll withholding (~4 paychecks) --")
    with book.open() as b:
        paychecks = []
        for t in b.transactions:
            if (t.description or "").startswith("UW Medicine — payroll"):
                gross = fed = ss = med = D("0")
                for s in t.splits:
                    fn = s.account.fullname
                    v = Decimal(str(s.value))
                    if fn == SALARY:
                        gross = -v
                    elif fn == "Expenses:Taxes:Federal":
                        fed = v
                    elif fn == EXP_SS:
                        ss = v
                    elif fn == EXP_MEDICARE:
                        med = v
                paychecks.append((t.post_date.date()
                                  if hasattr(t.post_date, "date")
                                  else t.post_date, gross, fed, ss, med))
        paychecks.sort()
        # Show two base + the first two overtime (higher-gross) paychecks.
        base = [p for p in paychecks if p[1] == _base_gross(p[0])][:2]
        ot = [p for p in paychecks if p[1] != _base_gross(p[0])][:2]
        for when, gross, fed, ss, med in base + ot:
            print(f"    {when} gross ${gross:>8,.2f} | fed ${fed:>7,.2f} "
                  f"ss ${ss:>6,.2f} med ${med:>6,.2f}")
        feds = {p[2] for p in paychecks}
        print(f"  distinct federal-withholding amounts across "
              f"{len(paychecks)} paychecks: {len(feds)} "
              f"(>1 ⇒ not frozen)")

    print("\n-- (R4) No generator/test meta-notes persisted --")
    bad_tokens = ["FX payable", "EUR-denominated", "USD-denominated",
                  "CAD-denominated", "(H1 case)", "denominated invoices",
                  " case)", "(outstanding)"]
    with book.open() as b:
        hits = []
        for c in b.customers:
            note = c.notes or ""
            for tok in bad_tokens:
                if tok.lower() in note.lower():
                    hits.append(("customer", c.name, tok))
        for v in b.vendors:
            note = v.notes or ""
            for tok in bad_tokens:
                if tok.lower() in note.lower():
                    hits.append(("vendor", v.name, tok))
        for t in b.transactions:
            d = t.description or ""
            for tok in bad_tokens:
                if tok.lower() in d.lower():
                    hits.append(("txn", d[:40], tok))
    print(f"  meta-note hits in notes/descriptions: {len(hits)} "
          f"(should be 0)")
    for kind, name, tok in hits[:10]:
        print(f"    {kind}: {name!r} contains {tok!r}")

    # ── (a) No data cliff: recent months have non-zero net + runway ──
    print("\n-- (a) Recent activity (no data cliff) --")
    lines = summary.splitlines()
    for i, line in enumerate(lines):
        low = line.lower()
        if any(k in low for k in (
                "net worth trajectory", "now:", "monthly net",
                "runway", "burn")):
            print(f"  summary: {line.strip()}")
    # Per-month net income over the last 3 calendar months before THROUGH.
    print("  monthly net (income - expenses), last 3 months:")
    for back in (2, 1, 0):
        ym_first = (through.replace(day=1)
                    - relativedelta_safe(months=back))
        nxt = ym_first + relativedelta_safe(months=1)
        m_end = min(nxt - timedelta(days=1), through)
        try:
            inc = book.income_by_source(
                start_date=ym_first, end_date=m_end, compact=False)
            exp = book.spending_by_category(
                start_date=ym_first, end_date=m_end, compact=False)
            ti = _parse_money(inc.get("total", "0"))
            te = _parse_money(exp.get("total", "0"))
            print(f"    {ym_first.strftime('%Y-%m')}: income "
                  f"{ti:,.2f} - expenses {te:,.2f} = net {ti - te:,.2f}")
        except Exception as exc:  # noqa: BLE001
            print(f"    {ym_first.strftime('%Y-%m')}: (n/a: {exc})")

    # ── (a2) Personal-life spending: avg monthly over last ~5 months ──
    print("\n-- (a2) Personal-life spending (avg/mo over last ~5 months) --")
    window_start = (through.replace(day=1) - relativedelta_safe(months=4))
    n_months = 5
    # Balance just before the window vs as-of THROUGH gives the period
    # spend for each expense account (expenses only accrue debits).
    day_before = window_start - timedelta(days=1)
    # ``lumpy`` streams (Gifts) are SPECIFIED as zero-most-months with
    # spikes, so a spike-free recent window legitimately averages low —
    # the annual average is the target, not any 5-month slice. We assert
    # only that they carry flow in the window, and report the figure.
    personal = [
        ("Medical", EXP_MEDICAL, (100, 200), False),
        ("Gifts", EXP_GIFTS, (50, 100), True),
        ("Charity", EXP_CHARITY, (50, 100), False),
        ("Travel", EXP_TRAVEL, (0, None), False),
        ("Entertainment", EXP_ENTERTAINMENT, (100, 200), False),
        ("Personal Care", EXP_PERSONAL_CARE, (50, 80), False),
    ]
    print(f"  window: {window_start.isoformat()} → {through.isoformat()} "
          f"({n_months} months)")
    for label, path, (lo, hi), lumpy in personal:
        bal_start = _parse_money(book.get_balance(path, as_of_date=day_before))
        bal_end = _parse_money(book.get_balance(path, as_of_date=as_of))
        period = bal_end - bal_start
        avg = (period / n_months)
        if hi is None:
            band = f"(target >= ${lo}/mo cumulative trip-driven)"
            ok = period > 0
        elif lumpy:
            band = f"(lumpy; target ~${lo}-{hi}/mo ANNUAL avg, low in a "
            band += "spike-free window)"
            ok = period > 0
        else:
            ok = lo * D("0.6") <= avg <= hi * D("1.6")
            band = f"(target ~${lo}-{hi}/mo)"
        print(f"    {label:14s} period ${period:>9,.2f} | "
              f"avg ${avg:>7,.2f}/mo {band} "
              f"{'OK' if ok else 'CHECK'}  non-zero={period != 0}")

    # Gifts annual-average corroboration (its real target unit). Use the
    # trailing 12 months ending at THROUGH so the December spike is in.
    yr_start = (through.replace(day=1) - relativedelta_safe(months=11))
    gifts_y0 = _parse_money(book.get_balance(
        EXP_GIFTS, as_of_date=yr_start - timedelta(days=1)))
    gifts_y1 = _parse_money(book.get_balance(EXP_GIFTS, as_of_date=as_of))
    gifts_yr = gifts_y1 - gifts_y0
    print(f"    Gifts trailing-12mo: ${gifts_yr:,.2f} => "
          f"avg ${gifts_yr / 12:,.2f}/mo (annual target ~$50-100/mo)")

    # The client-visit trips must exist, on the business card and the
    # business travel/meals lines (audit A4).
    print("\n-- (a3) Client-visit trips (business travel + meals) --")
    with book.open() as b:
        trip_total = meals_total = D("0")
        trip_descs = []
        trips_on_personal_card = 0
        for t in b.transactions:
            d = t.description or ""
            if "client visit" in d or "client meal" in d:
                for s in t.splits:
                    if s.account.fullname == EXP_BIZ_TRAVEL:
                        trip_total += Decimal(str(s.value))
                        trip_descs.append(d)
                    elif s.account.fullname == EXP_MEALS:
                        meals_total += Decimal(str(s.value))
                    elif s.account.fullname == CHASE:
                        trips_on_personal_card += 1
        print(f"  business travel total: ${trip_total:,.2f} across "
              f"{len(trip_descs)} bookings; client meals ${meals_total:,.2f}")
        print(f"  trip legs on the personal card: {trips_on_personal_card} "
              f"(should be 0)")
        for d in trip_descs[-4:]:
            print(f"    - {d}")

    # New accounts must exist and carry flow.
    print("\n-- (a4) New accounts present + with flow --")
    for label, path in [("Entertainment", EXP_ENTERTAINMENT),
                        ("Personal Care", EXP_PERSONAL_CARE)]:
        bal = _parse_money(book.get_balance(path, as_of_date=as_of))
        print(f"    {label:14s} {path}  lifetime ${bal:,.2f}  "
              f"(exists+flow: {bal > 0})")

    print("\n-- (b) Investment holdings (whole vs fractional) --")
    for path, sym in [(AAPL, "AAPL"), (MSFT, "MSFT"), (VTSAX, "VTSAX"),
                      (VBTLX, "VBTLX"), (ETH, "ETH")]:
        shares = book.get_balance(path)
        latest = MD.security(sym, as_of).quantize(_security_quant(sym))
        mkt = (Decimal(str(shares)) * latest).quantize(D("0.01"))
        whole = Decimal(str(shares)) == Decimal(str(shares)).to_integral_value()
        tag = "WHOLE" if whole else "fractional"
        print(f"  {sym:6s} {shares} sh ({tag}) × ${latest} ≈ ${mkt:,.2f}")

    # ── Capital-gains sign check: a below-cost sale books a LOSS that ──
    #    reduces capital-gains income (positive/debit value on the Capital
    #    Gains split), and every sell balances to zero.
    print("\n-- (b2) Capital-gains sign (below-cost sale = loss) --")
    with book.open() as b:
        sells = []
        for t in b.transactions:
            d = t.description or ""
            if " — Sell " not in d:
                continue
            gv = D("0")
            for s in t.splits:
                if s.account.fullname == CAPITAL_GAINS:
                    gv += Decimal(str(s.value))
            bal = sum(Decimal(str(s.value)) for s in t.splits)
            # realized P/L = −(capital-gains split value)
            realized = -gv
            sells.append((d, realized, bal))
        losses = [x for x in sells if x[1] < 0]
        print(f"  sell transactions: {len(sells)}; "
              f"all balance to zero: {all(b == 0 for _, _, b in sells)}")
        for d, realized, _ in sells:
            kind = "GAIN" if realized > 0 else ("LOSS" if realized < 0 else "flat")
            print(f"    {d[:48]:48s} realized {realized:>10,.2f} ({kind})")
        print(f"  below-cost sales booked as losses: {len(losses)} "
              f"(loss ⇒ Capital Gains split value > 0, reduces income)")

    # ── Recurring-expense drift: utilities must vary month to month now ──
    print("\n-- (b3) Recurring utility drift (no longer uniform) --")
    with book.open() as b:
        util_by_desc: dict[str, list[Decimal]] = {}
        for t in b.transactions:
            d = t.description or ""
            if d.startswith(("Electric -", "Gas -", "Water/Sewer")):
                for s in t.splits:
                    if s.account.fullname in (EXP_ELECTRIC, EXP_GAS, EXP_WATER) \
                            and Decimal(str(s.value)) > 0:
                        util_by_desc.setdefault(d, []).append(
                            Decimal(str(s.value)))
        for d, amts in util_by_desc.items():
            uniq = len(set(amts))
            lo, hi = min(amts), max(amts)
            print(f"    {d[:34]:34s} n={len(amts):>3} distinct={uniq:>3} "
                  f"range ${lo:,.2f}..${hi:,.2f} "
                  f"(varies: {uniq > 1})")

    print("\n-- Cash posture (policy buffer band) --")
    chk = _parse_money(book.get_balance(CHECKING, as_of_date=as_of))
    sav = _parse_money(book.get_balance(SAVINGS, as_of_date=as_of))
    llc = _parse_money(book.get_balance(LLC_CHECKING, as_of_date=as_of))
    lo, hi = POLICY.buffer * D("0.5"), POLICY.buffer * D("3")
    print(f"  Checking: ${chk:,.2f}  (in policy band "
          f"${lo:,.0f}–${hi:,.0f}: {lo <= chk <= hi})")
    print(f"  Savings:  ${sav:,.2f}")
    print(f"  LLC Checking: ${llc:,.2f}  (working capital floor "
          f"${POLICY.business_buffer:,.0f})")

    print("\n-- (c) Outstanding receivables (USD + EUR + CAD) --")
    usd_ar = book.get_balance(AR_USD, as_of_date=as_of)
    eur_ar = book.get_balance(AR_EUR, as_of_date=as_of)
    cad_ar = book.get_balance(AR_CAD, as_of_date=as_of)
    print(f"  Assets:Accounts Receivable (USD): ${usd_ar}")
    print(f"  Assets:Receivables:A/R EUR (EUR): €{eur_ar}")
    print(f"  Assets:Receivables:A/R CAD (CAD): C${cad_ar}")
    try:
        out_inv = book.get_outstanding_invoices(compact=False)
        n_out = len(out_inv) if isinstance(out_inv, list) else "see above"
        print(f"  get_outstanding_invoices count: {n_out}")
        if isinstance(out_inv, list):
            worst = 0
            for r in out_inv:
                due = r.get("due_date")
                if due:
                    days = (through - date.fromisoformat(due)).days
                    worst = max(worst, days)
            print(f"  most-overdue outstanding invoice: {worst} days "
                  f"past due (target: <= ~90)")
    except Exception as exc:  # noqa: BLE001
        print(f"  get_outstanding_invoices: {exc}")

    print("\n-- (h) FX gain/loss (paid EUR + CAD invoices) --")
    fx_bal = book.get_balance(FX_GAIN_LOSS)
    print(f"  Income:Foreign Exchange Gain/Loss balance: {fx_bal} "
          f"(non-zero ⇒ realized FX booked on settled EUR/CAD invoices)")

    print("\n-- GBP must be absent; CAD prices must exist --")
    with book.open() as b:
        comm_mn = sorted({c.mnemonic for c in b.commodities})
        cad_prices = sum(1 for p in b.prices if p.commodity.mnemonic == "CAD")
    print(f"  commodities: {comm_mn}")
    print(f"  GBP present: {'GBP' in comm_mn}  (should be False)")
    print(f"  CAD prices on file: {cad_prices}")

    print("\n-- (d) Scheduled transactions (must stay ENABLED) --")
    enabled = book.list_scheduled_transactions(
        enabled_only=True, compact=False)
    n_en = len(enabled) if isinstance(enabled, list) else 0
    print(f"  enabled scheduled transactions: {n_en}")
    if isinstance(enabled, list):
        for sx in enabled[:6]:
            print(f"    {sx['name']}: next {sx.get('next_occurrence')} "
                  f"(last {sx.get('last_occurrence')})")
    # Overdue line from the dashboard.
    for line in lines:
        if "overdue scheduled" in line.lower():
            print(f"  summary: {line.strip()}")

    print("\n-- (e) Jobs --")
    try:
        jobs = book.list_jobs(compact=False)
        n_jobs = len(jobs) if isinstance(jobs, list) else 0
        print(f"  jobs: {n_jobs}")
        if isinstance(jobs, list):
            for j in jobs:
                print(f"    {j.get('name')} ({j.get('reference')})")
    except Exception as exc:  # noqa: BLE001
        print(f"  list_jobs: {exc}")

    print("\n-- (f) Reconciliation (most splits unreconciled) --")
    with book.open() as b:
        from sqlalchemy import text as _text
        rows = b.session.execute(_text(
            "SELECT reconcile_state, COUNT(*) FROM splits "
            "GROUP BY reconcile_state")).fetchall()
        state_counts = {r[0]: r[1] for r in rows}
    n_unrec = state_counts.get("n", 0)
    n_rec = state_counts.get("y", 0)
    n_clr = state_counts.get("c", 0)
    print(f"  splits by reconcile_state: unreconciled(n)={n_unrec}, "
          f"reconciled(y)={n_rec}, cleared(c)={n_clr}")

    _verify_audit(book, through)

    print("\n-- Counts --")
    with book.open() as b:
        n_acct = len(list(b.accounts))
        n_txn = len(list(b.transactions))
        n_inv = len(list(b.invoices))
        n_price = len(list(b.prices))
        n_cust = len(list(b.customers))
        n_vend = len(list(b.vendors))
        lots = sum(len(a.lots) for a in b.accounts)
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


def _verify_audit(book: GnuCashBook, through: date) -> None:
    """The IRS-audit invariants (AUDIT_ALEX_IRS_2026-09-11.md), read
    straight off the SQLite file so the check is independent of the
    generator's own bookkeeping."""
    import sqlite3

    print("\n-- (AUDIT) IRS-read invariants --")
    con = sqlite3.connect(str(book.book_path))
    con.row_factory = sqlite3.Row
    try:
        # A1: no loss sale with a same-symbol purchase within ±30 days.
        rows = con.execute(
            """
            SELECT date(t.post_date) AS d, a.name AS sym,
                   s.quantity_num * 1.0 / s.quantity_denom AS qty,
                   s.value_num * 1.0 / s.value_denom AS value
            FROM splits s JOIN transactions t ON t.guid = s.tx_guid
            JOIN accounts a ON a.guid = s.account_guid
            JOIN commodities c ON c.guid = a.commodity_guid
            WHERE c.namespace IN ('FUND', 'NASDAQ', 'CRYPTO')
            """).fetchall()
        gains = {}
        for r in con.execute(
                """
                SELECT t.guid AS g, s.value_num * 1.0 / s.value_denom AS v
                FROM splits s JOIN transactions t ON t.guid = s.tx_guid
                JOIN accounts a ON a.guid = s.account_guid
                WHERE a.name = 'Capital Gains'
                """):
            gains[r["g"]] = r["v"]
        sells = con.execute(
            """
            SELECT t.guid AS g, date(t.post_date) AS d, a.name AS sym
            FROM splits s JOIN transactions t ON t.guid = s.tx_guid
            JOIN accounts a ON a.guid = s.account_guid
            JOIN commodities c ON c.guid = a.commodity_guid
            WHERE c.namespace IN ('FUND', 'NASDAQ', 'CRYPTO')
              AND s.quantity_num < 0
            """).fetchall()
        wash = []
        for sell in sells:
            realized = -gains.get(sell["g"], 0.0)
            if realized >= 0:
                continue
            sd = date.fromisoformat(sell["d"])
            for r in rows:
                if r["sym"] == sell["sym"] and r["qty"] > 0:
                    dd = abs((date.fromisoformat(r["d"]) - sd).days)
                    if dd <= 30:
                        wash.append((sell["sym"], sell["d"], realized, r["d"]))
        print(f"  A1 loss sales with a same-fund buy within 30 days: "
              f"{len(wash)} (must be 0)")
        for w in wash[:5]:
            print(f"     {w}")

        # A2: no employees; Sam is a vendor with bills.
        n_emp = con.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        sam = con.execute(
            "SELECT COUNT(*) FROM invoices i JOIN vendors v "
            "ON v.guid = i.owner_guid WHERE v.name LIKE 'Sam Rivera%'"
        ).fetchone()[0]
        print(f"  A2 employees on the books: {n_emp} (0); "
              f"Sam Rivera vendor bills: {sam}")

        # A3: estimated tax totals per year.
        for yr in range(YEAR, through.year + 1):
            est = con.execute(
                """
                SELECT SUM(s.value_num * 1.0 / s.value_denom) FROM splits s
                JOIN transactions t ON t.guid = s.tx_guid
                JOIN accounts a ON a.guid = s.account_guid
                WHERE a.name IN ('Estimated Tax Payments', 'Self-Employment Tax')
                  AND date(t.post_date) BETWEEN ? AND ?
                """, (f"{yr}-01-01", f"{yr}-12-31")).fetchone()[0] or 0.0
            print(f"  A3 1040-ES paid in calendar {yr}: ${est:,.0f}")

        # B1: card balance vs its credit_limit slot at every month end.
        from continuation import _slot_decimal
        over = []
        for card_path in (CHASE, AMEX):
            limit = _slot_decimal(book, card_path, "credit_limit")
            worst = D("0")
            for yr, m in _month_iter(date(YEAR, 1, 1), through):
                me = min(_clamp_day(yr, m, 31), through)
                owed = -_parse_money(book.get_balance(card_path, as_of_date=me))
                worst = max(worst, owed)
                if owed > limit:
                    over.append((card_path.rsplit(":", 1)[1], me.isoformat(),
                                 owed))
            print(f"  B1 {card_path.rsplit(':', 1)[1]}: peak month-end "
                  f"balance ${worst:,.2f} vs limit ${limit:,.0f}")
        print(f"  B1 month-ends over limit: {len(over)} (must be 0)")

        # B8: invoice ids ascend with date_opened.
        ids = con.execute(
            "SELECT id, date(date_opened) AS d FROM invoices "
            "WHERE owner_type = 2 ORDER BY date_opened, id").fetchall()
        seq = [r["id"] for r in ids]
        print(f"  B8 customer invoice ids in open-date order: "
              f"{seq == sorted(seq)} ({len(seq)} invoices; first {seq[:3]}, "
              f"last {seq[-3:]})")
    finally:
        con.close()

    _verify_stability(book, through)


def _verify_stability(book: GnuCashBook, through: date) -> None:
    """The four horizon-independent invariants (2026-09-17 round): a
    book built to any ``--through`` — 2030 included — keeps every card
    under its limit, the household's checking inside the policy band,
    no document unpaid past terms + 45 days, and the federal tax paid
    for each finished year within 15% of the plan's liability. Read off
    the SQLite file and the server's own reports; any violation exits
    non-zero."""
    import sqlite3

    print("\n-- (STABILITY) horizon-independent invariants --")
    failures: list[str] = []
    month_ends = [min(_clamp_day(yr, m, 31), through)
                  for yr, m in _month_iter(date(YEAR, 1, 1), through)]

    # 1. Cards vs credit_limit slot, every month-end.
    from continuation import _slot_decimal
    for card_path in (CHASE, AMEX):
        limit = _slot_decimal(book, card_path, "credit_limit")
        peak, peak_when = D("0"), None
        for me in month_ends:
            owed = -_parse_money(book.get_balance(card_path, as_of_date=me))
            if owed > peak:
                peak, peak_when = owed, me
            if owed > limit:
                failures.append(f"{card_path} owes {owed} > limit {limit} "
                                f"at {me}")
        print(f"  S1 {card_path.rsplit(':', 1)[1]}: peak ${peak:,.2f} on "
              f"{peak_when} vs limit ${limit:,.0f} "
              f"({100 * peak / limit:.0f}% utilization)")

    # 2. Checking inside the policy band at every month-end (the
    #    horizon itself is mid-month and exempt, as in the engine).
    lo, hi = POLICY.buffer * D("0.5"), POLICY.buffer * D("3")
    band_min, band_max = None, None
    for me in month_ends:
        if me == through:
            continue
        chk = _parse_money(book.get_balance(CHECKING, as_of_date=me))
        band_min = chk if band_min is None else min(band_min, chk)
        band_max = chk if band_max is None else max(band_max, chk)
        if not (lo <= chk <= hi):
            failures.append(f"checking {chk} outside [{lo}, {hi}] at {me}")
    print(f"  S2 Checking at month-ends: min ${band_min:,.2f} / max "
          f"${band_max:,.2f} vs band ${lo:,.0f}–${hi:,.0f}")

    # 3. No document unpaid past terms + 45 days — paid ones by their
    #    settlement date, open ones as of the horizon.
    con = sqlite3.connect(str(book.book_path))
    con.row_factory = sqlite3.Row
    try:
        # Due date = post date + the billterm's due days (GnuCash keeps
        # no due column; the terms row is the contract).
        docs = con.execute(
            "SELECT i.id, date(i.date_posted, '+' || COALESCE(b.duedays, 0) "
            "|| ' days') AS due, i.post_lot, i.post_txn "
            "FROM invoices i LEFT JOIN billterms b ON b.guid = i.terms "
            "WHERE i.post_txn IS NOT NULL AND i.post_txn <> ''"
        ).fetchall()
        worst_paid = worst_open = 0
        n_open = 0
        for doc in docs:
            rows = con.execute(
                "SELECT date(t.post_date) AS d, "
                "s.value_num * 1.0 / s.value_denom AS v "
                "FROM splits s JOIN transactions t ON t.guid = s.tx_guid "
                "WHERE s.lot_guid = ? ORDER BY t.post_date",
                (doc["post_lot"],)).fetchall()
            due = date.fromisoformat(doc["due"])
            balance = sum(r["v"] for r in rows)
            if abs(balance) < 0.005 and len(rows) > 1:
                settled = date.fromisoformat(rows[-1]["d"])
                late = (settled - due).days
                worst_paid = max(worst_paid, late)
                if late > 45:
                    failures.append(f"{doc['id']} settled {late} days past "
                                    f"terms ({settled} vs due {due})")
            else:
                n_open += 1
                late = (through - due).days
                worst_open = max(worst_open, late)
                if late > 45:
                    failures.append(f"{doc['id']} open {late} days past "
                                    f"terms at the horizon (due {due})")
        print(f"  S3 documents: {len(docs)} posted, {n_open} open; worst "
              f"settlement {worst_paid} days past terms, worst open "
              f"{worst_open} days past terms (bound 45)")

        # 4. Federal tax paid vs the plan's liability, per finished year.
        for yr in range(YEAR, through.year + 1):
            plan = _federal_liability(yr, through)
            if plan is None or _next_bday(date(yr + 1, 4, 15)) > through:
                continue
            def _sum(sql, *params):
                return D(str(con.execute(sql, params).fetchone()[0] or 0))
            withheld = _sum(
                "SELECT SUM(s.value_num * 1.0 / s.value_denom) FROM splits s "
                "JOIN transactions t ON t.guid = s.tx_guid "
                "JOIN accounts a ON a.guid = s.account_guid "
                "WHERE a.name = 'Federal' AND t.description LIKE 'UW Medicine%' "
                "AND date(t.post_date) BETWEEN ? AND ?",
                f"{yr}-01-01", f"{yr}-12-31")
            # A transaction's notes live in the slots table.
            noted = ("EXISTS (SELECT 1 FROM slots sl WHERE sl.obj_guid = t.guid "
                     "AND sl.name = 'notes' AND sl.string_val LIKE ?)")
            estimates = _sum(
                "SELECT SUM(s.value_num * 1.0 / s.value_denom) FROM splits s "
                "JOIN transactions t ON t.guid = s.tx_guid "
                "JOIN accounts a ON a.guid = s.account_guid "
                "WHERE a.name IN ('Estimated Tax Payments', "
                f"'Self-Employment Tax') AND {noted}",
                f"Form 1040-ES {yr} Q%")
            settlement = _sum(
                "SELECT SUM(s.value_num * 1.0 / s.value_denom) FROM splits s "
                "JOIN transactions t ON t.guid = s.tx_guid "
                "JOIN accounts a ON a.guid = s.account_guid "
                f"WHERE a.name = 'Federal' AND {noted}",
                f"{yr} Form 1040 (MFJ)%")
            paid = withheld + estimates + settlement
            liability = plan["total"]
            off = abs(paid - liability) / liability if liability else D("0")
            kind = "refund" if settlement < 0 else "balance due"
            print(f"  S4 TY{yr}: paid ${paid:,.2f} = withholding "
                  f"${withheld:,.2f} + 1040-ES ${estimates:,.2f} + April "
                  f"{kind} ${settlement:,.2f}; plan liability "
                  f"${liability:,.2f} (income ${plan['income']:,.2f} + SE "
                  f"${plan['se']:,.2f}) → {100 * off:.2f}% off (bound 15%)")
            if off > D("0.15"):
                failures.append(f"TY{yr} federal paid {paid} vs liability "
                                f"{liability} ({100 * off:.1f}% off)")
    finally:
        con.close()

    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        raise SystemExit(f"stability invariants: {len(failures)} violation(s)")
    print("  all four hold")


def relativedelta_safe(months: int = 0):
    from dateutil.relativedelta import relativedelta
    return relativedelta(months=months)


# ── Continuation hooks (closed-loop policy layer) ───────────────
# Persona wiring for scripts/synthetic_book/continue_book.py; policy
# constants derived from the measured drift in
# specs/v1.5/DRIFT_ANALYSIS.md.

from continuation import CardPolicy, PersonaPolicy  # noqa: E402


def continuation_txns(through: date) -> list[dict]:
    """The deterministic streams continuation replays (spec §2.2).

    Card payments, sweeps, draws and savings interest are deliberately
    absent: fixed payments against variable spending are the measured
    drift disease — the policy layer derives them from the book itself
    (and the base build runs the same policy from 2025-01-01).
    """
    return (gen_recurring(through) + gen_daily_weekly(through)
            + gen_personal_life(through) + gen_volume(through))


def continue_business(book: GnuCashBook, through: date,
                      since: date) -> dict:
    return run_business(book, through, since=since)


def continue_investments(out_path: Path, through: date,
                         since: date) -> dict:
    return run_investments(out_path, through, since=since)


def advance_schedules(out_path: Path, through: date) -> dict:
    return set_schedule_state(out_path, through)


def extend_prices(out_path: Path, since: date, through: date) -> int:
    """Price rows for the continued range: 1st-of-month snapshots, a
    fresh closing point per commodity, and on-date quotes for the new
    trade/settlement events. ``add_event_prices`` skips anything the
    book already has, so the prefix's price table is never touched."""
    syms = [s[0] for s in SECURITIES]
    events: list[tuple[str, date]] = []
    for yr, mo in _price_months(through):
        d = date(yr, mo, 1)
        if d <= since:
            continue
        events += [(sym, d) for sym in syms]
        events += [(fc, d) for fc in FOREIGN_CURRENCIES]
    for sym in syms:
        events.append((sym, through))  # §5: dated at the horizon
    for fc in FOREIGN_CURRENCIES:
        events.append((fc, through))
    for sym, d in (investment_event_price_dates(through)
                   + business_event_price_dates(through)):
        if d > since:
            events.append((sym, d))
    return add_event_prices(out_path, events)


def ensure_rate(out_path: Path, currency: str, when: date) -> None:
    """Real FX close for a cross-currency settlement date (no-op when
    a rate for that date is already on file)."""
    add_event_prices(out_path, [(currency, when)])


def continuation_invest(out_path: Path, when: date, amount: Decimal,
                        source_path: str) -> None:
    """Policy-layer VTSAX purchase: one lot per purchase at the real
    close, mirroring the DCA/sweep lot pattern so lot and gain tooling
    see consistent data. Source is Checking (surplus sweep) or Savings
    (pile rebalance — RULED 2026-08-31)."""
    add_event_prices(out_path, [("VTSAX", when)])
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    try:
        usd = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        frac = {s[0]: s[3] for s in SECURITIES}["VTSAX"]
        price = MD.security("VTSAX", when).quantize(_security_quant("VTSAX"))
        shares = _shares_from_usd(amount, price, frac)
        kind = ("savings rebalance" if source_path == SAVINGS
                else "surplus sweep")
        lot = piecash.Lot(
            title=f"VTSAX {kind} {when.isoformat()}", account=acct[VTSAX],
            notes=f"{kind.capitalize()} — ${amount} @ ${price}", is_closed=0)
        inv_split = piecash.Split(account=acct[VTSAX], value=amount,
                                  quantity=shares)
        cash_split = piecash.Split(account=acct[source_path], value=-amount)
        piecash.Transaction(
            currency=usd, description="Vanguard — Buy VTSAX",
            notes=f"{kind.capitalize()} ${amount} @ ${price} = {shares} sh",
            post_date=when, enter_date=_enter_stamp(when),
            splits=[inv_split, cash_split])
        inv_split.lot = lot
        book.save()
    finally:
        book.close()


POLICY = PersonaPolicy(
    key="alex", currency="USD",
    checking=CHECKING, savings=SAVINGS,
    # The household floor: a 1040-ES installment plus the first half
    # of a month's bills must clear before the mid-month paycheck and
    # the month-end draw land (the estimates are ~$11K each).
    buffer=D("20000"),
    cards=(
        CardPolicy(account=CHASE, label="Chase Sapphire", kind="pif",
                   close_day_default=15),
        # The business card is paid by the business (audit B3).
        CardPolicy(account=AMEX, label="Business Amex", kind="pif",
                   close_day_default=22, repair_min=D("2000"),
                   pay_from=LLC_CHECKING),
    ),
    savings_share=D("0.40"),           # surplus: 40% savings / 60% VTSAX
    invest_months=(3, 6, 9, 12),       # VTSAX share moves quarterly
    savings_target=D("60000"),         # ~3× buffer; rebalance ends here
    rebalance_tranche=D("10000"),      # quarterly savings→VTSAX tranche
    max_monthly_sweep=D("15000"),      # staging cap — paces the repair
    min_sweep=D("200"),
    invest=continuation_invest,
    ensure_rate=ensure_rate,
    # Loans have no statement to reconcile against (review §1).
    no_reconcile=(MORTGAGE, AUTO_LOAN),
    # The LLC (audit B3): invoices settle here, payables and the
    # business card are paid from here, and the month-end draw moves
    # everything above the floor to the household as ONE transfer,
    # LLC Checking → Checking (cold audit B3 — no equity clearing
    # leg). The reserve accrues ~$1,700/mo toward the $20K December
    # Solo 401(k) contribution (audit B7).
    business_checking=LLC_CHECKING,
    business_buffer=D("8000"),
    business_reserve_monthly=D("1700"),
    # Savings earn ~3.8% APY, monthly (audit B6).
    savings_apy=D("0.038"),
    interest_income=INTEREST_INCOME,
    desc_draw="Owner's draw — Cascade Code LLC ({month})",
    desc_savings_interest="Ally Bank — savings interest ({month})",
)


def run_base_policy(out_path: Path, through: date) -> list[str]:
    """Run the closed-loop policy over the WHOLE base timeline —
    owner's draws, surplus sweeps (savings + quarterly VTSAX), the
    savings-pile rebalance, savings interest — from 2025-01-01. Card
    statements are paid by ``run_credit_cards`` (the 2025 narrative
    needs the carried-balance arcs), so the cards are masked here;
    everything else is the exact rule set the continuation applies
    from the frozen edge onward."""
    from dataclasses import replace

    from continuation import run_policy

    base_policy = replace(POLICY, cards=())
    return run_policy(base_policy, out_path,
                      date(YEAR, 1, 1) - timedelta(days=1), through)


# ── Driver ──────────────────────────────────────────────────────

def build_base(out_path: Path) -> None:
    """Commodities, chart, account slots — nothing dated."""
    print(f"Building Alex Chen-Morales base at: {out_path}")
    print("\nPhase 1: book file + commodities")
    create_book_file(out_path)
    print("\nPhase 2: chart of accounts")
    n_acct = create_accounts(out_path)
    print(f"  {n_acct} accounts created")
    set_account_slots(GnuCashBook(str(out_path)))
    print("  account slots set")


def build(out_path: Path, through: date) -> None:
    build_base(out_path)
    print(f"Activity runs 2025-01-01 → {through.isoformat()} (THROUGH)")
    print("\nPhase 1b: prices")
    n_prices = add_prices(out_path)
    # Real prices on every trade / invoice settle date.
    event_dates = (investment_event_price_dates(through)
                   + business_event_price_dates(through))
    n_event = add_event_prices(out_path, event_dates)
    print(f"  {n_prices} monthly prices + {n_event} event prices")
    book = GnuCashBook(str(out_path))

    print("\nPhase 3: opening balances + investment lots")
    opening_balances(out_path)
    print("  opening balances posted")

    print("\nPhase 4: scheduled-transaction templates")
    n_sx = create_scheduled_templates(book)
    print(f"  {n_sx} SX templates created")

    print("\nPhase 5: recurring instantiations")
    n = write_bulk(out_path, gen_recurring(through))
    print(f"  {n} recurring transactions")

    print("\nPhase 6: daily/weekly + seasonal")
    n = write_bulk(out_path, gen_daily_weekly(through))
    print(f"  {n} daily/weekly/seasonal transactions")

    print("\nPhase 6b: personal-life spending "
          "(medical/gifts/charity/travel/entertainment/personal care)")
    n = write_bulk(out_path, gen_personal_life(through))
    print(f"  {n} personal-life transactions")

    print("\nPhase 13: volume stress")
    n = write_bulk(out_path, gen_volume(through))
    print(f"  {n} volume transactions")

    print("\nPhase 7: business module (every client through A/R)")
    business = run_business(book, through)
    print(f"  {business}")

    print("\nPhase 8: investments")
    inv_counts = run_investments(out_path, through)
    print(f"  {inv_counts}")

    print("\nPhase 12: edge cases")
    edge = run_edge_cases(book)
    print(f"  {edge}")

    # Cards and the policy read the book, so every charge stream must
    # already be written: statements are paid from the real running
    # balance, draws from the LLC's real balance, sweeps from the
    # household's real surplus.
    print("\nPhase 9: credit card statements (computed from the book)")
    n = run_credit_cards(out_path, through)
    print(f"  {n} statement payments / interest / fees")

    print("\nPhase 7d: closed-loop policy — owner's draws, surplus sweeps, "
          "savings interest")
    actions = run_base_policy(out_path, through)
    print(f"  {len(actions)} policy actions; last 4:")
    for line in actions[-4:]:
        print(f"    {line}")

    print("\nPhase 10: budgets")
    n = run_budget(book, through)
    print(f"  {n} annual budgets created")

    print("\nPhase 11: reconciliation posture (through the last full month)")
    for line in run_reconciliation(out_path, through):
        print(f"  {line}")

    print("\nScheduled-transaction state (stay ENABLED, realistic timing)")
    sx_state = set_schedule_state(out_path, through)
    print(f"  {sx_state}")

    # Every row's entry timestamp is its post day (item 5 of the
    # 2026-09-17 round): the bulk writers stamp it at write time; the
    # server-API rows (documents, policy engine, edge cases) get it
    # here, in write order within each day.
    print("\nEntry timestamps (enter_date = post day, write order kept)")
    from continuation import stamp_enter_dates
    print(f"  {stamp_enter_dates(out_path)} rows stamped")

    print("\nContinuation invariants over the base timeline")
    print("  (the 2025 card narrative — Chase carrying a balance Jan–May, "
          "the Amex missed August — trips the PIF-residue alarm by design)")
    from continuation import verify_invariants
    warnings = verify_invariants(POLICY, out_path,
                                 date(YEAR, 1, 1) - timedelta(days=1), through)
    for w in warnings:
        print(f"  WARN: {w}")
    if not warnings:
        print("  clean")

    verify(out_path, through)
    print("\nDone.")


def main() -> None:
    global THROUGH, PRICE_MONTHS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT),
        help="Output path (default: samples/alex.generated.gnucash)")
    parser.add_argument(
        "--through", default=None,
        help="Run activity through this date (YYYY-MM-DD). "
             "Default: today.")
    parser.add_argument(
        "--chart-only", action="store_true",
        help="Write the chart-only base (commodities, accounts, slots; "
             "nothing dated), VACUUMed, and stop.")
    args = parser.parse_args()
    if args.through:
        THROUGH = date.fromisoformat(args.through)
        PRICE_MONTHS = _price_months(THROUGH)
    out_path = Path(args.out).resolve()
    if out_path == PROTECTED.resolve():
        raise SystemExit(f"REFUSING to write to protected book: {PROTECTED}")
    if args.chart_only:
        from base_book import vacuum
        build_base(out_path)
        print(f"  base VACUUMed: {vacuum(out_path):,} bytes")
        return
    build(out_path, THROUGH)


if __name__ == "__main__":
    main()
