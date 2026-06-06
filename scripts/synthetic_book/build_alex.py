"""Build the Alex Chen-Morales USD-default synthetic GnuCash book FROM ZERO.

The USD analogue of ``build_lin_wei.py``: a single script that creates
Alex's entire book from nothing — the SQLite file, the commodities,
the full chart of accounts, opening balances and investment opening
lots, scheduled-transaction templates, recurring instantiations,
daily/weekly spending, contractor income + the LLC business module,
investment activity, the credit-card lifecycle, a budget,
reconciliation, edge cases, and a volume stress phase.

It implements ``specs/SYNTHETIC_BOOK_SPEC.md`` — a USD-default book
for a Seattle software contractor (Cascade Code LLC) with a European
client paying in EUR. The mandatory FX regression case is the Berlin
Digital GmbH EUR invoices, which post to a EUR A/R sub-account and
settle cross-currency EUR->USD with realized FX gain/loss booked.

All security prices and FX rates come from REAL historical market data
via ``market_data.MarketData`` (offline, cache-backed): VTSAX, VBTLX,
AAPL, MSFT, ETH (all USD-denominated; ETH is crypto) and EUR/CAD
FX (both -> USD). Trade prices and conversion amounts use the actual
quotes, not invented numbers. EUR backs Berlin Digital GmbH and CAD
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
from datetime import date, timedelta
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
VOLUME_TXN_COUNT = 320  # Phase 13 small casual-spend transactions

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


# ── Account path constants ──────────────────────────────────────

CHECKING = "Assets:Current Assets:Checking Account"
SAVINGS = "Assets:Current Assets:Savings Account"
CASH = "Assets:Current Assets:Cash"
AR_USD = "Assets:Accounts Receivable"
AR_EUR = "Assets:Receivables:Accounts Receivable EUR"
AR_CAD = "Assets:Receivables:Accounts Receivable CAD"
HSA = "Assets:Investments:HSA"
CONDO = "Assets:Fixed Assets:Condo"
VEHICLE = "Assets:Fixed Assets:Vehicle"

VTSAX = "Assets:Investments:Brokerage:VTSAX"
VBTLX = "Assets:Investments:Brokerage:VBTLX"
AAPL = "Assets:Investments:Brokerage:AAPL"
MSFT = "Assets:Investments:Brokerage:MSFT"
ETH = "Assets:Investments:Brokerage:ETH"

CHASE = "Liabilities:Credit Card:Chase Sapphire"
AMEX = "Liabilities:Credit Card:Business Amex"
MORTGAGE = "Liabilities:Loans:Mortgage"
AUTO_LOAN = "Liabilities:Loans:Auto Loan"
AP = "Liabilities:Accounts Payable"

OPENING = "Equity:Opening Balances"

SALARY = "Income:Salary"
CONTRACTOR = "Income:Contractor Income"
LLC_REVENUE = "Income:LLC Revenue"
DIVIDENDS = "Income:Investment Income:Dividends"
CAPITAL_GAINS = "Income:Investment Income:Capital Gains"
INTEREST_INCOME = "Income:Investment Income:Interest"
REIMBURSEMENTS = "Income:Reimbursements"
FX_GAIN_LOSS = "Income:Foreign Exchange Gain/Loss"

EXP_MORTGAGE_INT = "Expenses:Interest:Mortgage Interest"
EXP_AUTO_INT = "Expenses:Interest:Auto Loan Interest"
EXP_CC_INT = "Expenses:Interest:Credit Card Interest"
EXP_HOA = "Expenses:Housing:HOA"
EXP_HOUSING_MAINT = "Expenses:Housing:Maintenance"
EXP_FUEL = "Expenses:Auto:Fuel"
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
EXP_PROP_TAX = "Expenses:Taxes:Property Tax"
EXP_EST_TAX = "Expenses:Taxes:Estimated Tax Payments"
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
    """Add monthly USD-base prices for securities + FX pairs (real data)."""
    book = piecash.open_book(str(out_path), readonly=False)
    count = 0
    try:
        usd = book.default_currency
        comm_by_mnemonic = {c.mnemonic: c for c in book.commodities}
        for yr, mo in PRICE_MONTHS:
            pdate = date(yr, mo, 1)
            # Securities: USD close, real, forward-filled.
            for sym, _full, _ns, _frac in SECURITIES:
                comm = comm_by_mnemonic[sym]
                value = MD.security(sym, pdate).quantize(_security_quant(sym))
                piecash.Price(
                    commodity=comm, currency=usd, date=pdate,
                    value=value, type="last", source="user:market_data",
                )
                count += 1
            # FX: USD per foreign unit, real, forward-filled.
            for foreign in FOREIGN_CURRENCIES:
                comm = comm_by_mnemonic[foreign]
                value = MD.fx(foreign, "USD", pdate).quantize(D("0.0001"))
                piecash.Price(
                    commodity=comm, currency=usd, date=pdate,
                    value=value, type="last", source="user:market_data",
                )
                count += 1
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
    book = piecash.open_book(str(out_path), readonly=False)
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
ACCOUNTS = [
    # Assets
    ("Assets", "ASSET", None, "USD", "CURRENCY", True),
    ("Current Assets", "ASSET", "Assets", "USD", "CURRENCY", True),
    ("Checking Account", "BANK", "Assets:Current Assets", "USD", "CURRENCY", False),
    ("Savings Account", "BANK", "Assets:Current Assets", "USD", "CURRENCY", False),
    ("Cash", "CASH", "Assets:Current Assets", "USD", "CURRENCY", False),
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
    ("ETH", "STOCK", "Assets:Investments:Brokerage", "ETH", "CRYPTO", False),
    ("HSA", "BANK", "Assets:Investments", "USD", "CURRENCY", False),
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
    ("Contractor Income", "INCOME", "Income", "USD", "CURRENCY", False),
    ("LLC Revenue", "INCOME", "Income", "USD", "CURRENCY", False),
    ("Investment Income", "INCOME", "Income", "USD", "CURRENCY", True),
    ("Dividends", "INCOME", "Income:Investment Income", "USD", "CURRENCY", False),
    ("Capital Gains", "INCOME", "Income:Investment Income", "USD", "CURRENCY", False),
    ("Interest", "INCOME", "Income:Investment Income", "USD", "CURRENCY", False),
    ("Reimbursements", "INCOME", "Income", "USD", "CURRENCY", False),
    # Income:Foreign Exchange Gain/Loss is auto-created by pay_invoice; create
    # it up front so it always exists for direct FX transactions too.
    ("Foreign Exchange Gain/Loss", "INCOME", "Income", "USD", "CURRENCY", False),
    # Expenses
    ("Expenses", "EXPENSE", None, "USD", "CURRENCY", True),
    ("Housing", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("Mortgage Interest", "EXPENSE", "Expenses:Housing", "USD", "CURRENCY", False),
    ("HOA", "EXPENSE", "Expenses:Housing", "USD", "CURRENCY", False),
    ("Insurance", "EXPENSE", "Expenses:Housing", "USD", "CURRENCY", False),
    ("Maintenance", "EXPENSE", "Expenses:Housing", "USD", "CURRENCY", False),
    ("Auto", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("Fuel", "EXPENSE", "Expenses:Auto", "USD", "CURRENCY", False),
    ("Insurance", "EXPENSE", "Expenses:Auto", "USD", "CURRENCY", False),
    ("Maintenance", "EXPENSE", "Expenses:Auto", "USD", "CURRENCY", False),
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
    ("Life", "EXPENSE", "Expenses:Insurance", "USD", "CURRENCY", False),
    ("Umbrella", "EXPENSE", "Expenses:Insurance", "USD", "CURRENCY", False),
    ("Medical", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Taxes", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("Federal", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Social Security", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Medicare", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Property Tax", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Self-Employment Tax", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Estimated Tax Payments", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
    ("Sales Tax", "EXPENSE", "Expenses:Taxes", "USD", "CURRENCY", False),
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
    ("Contractor Payments", "EXPENSE", "Expenses:Business", "USD", "CURRENCY", False),
    ("Interest", "EXPENSE", "Expenses", "USD", "CURRENCY", True),
    ("Credit Card Interest", "EXPENSE", "Expenses:Interest", "USD", "CURRENCY", False),
    ("Mortgage Interest", "EXPENSE", "Expenses:Interest", "USD", "CURRENCY", False),
    ("Auto Loan Interest", "EXPENSE", "Expenses:Interest", "USD", "CURRENCY", False),
    ("Bank Charges", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    ("Miscellaneous", "EXPENSE", "Expenses", "USD", "CURRENCY", False),
    # Equity
    ("Equity", "EQUITY", None, "USD", "CURRENCY", True),
    ("Opening Balances", "EQUITY", "Equity", "USD", "CURRENCY", False),
]


def create_accounts(out_path: Path) -> int:
    """Create the full chart of accounts directly via piecash."""
    book = piecash.open_book(str(out_path), readonly=False)
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


# ── Phase 3: Opening balances + investment lots ─────────────────

# (account_path, balance_usd)  — opening balances via equity offset.
OPENING_BALANCES = [
    (CHECKING, D("14500")),
    (SAVINGS, D("22000")),
    (CASH, D("350")),
    (HSA, D("4800")),
    (MORTGAGE, D("-385000")),
    (AUTO_LOAN, D("-18500")),
    (CHASE, D("-2340")),
    (AMEX, D("-1890")),
    (CONDO, D("475000")),
    (VEHICLE, D("28000")),
]

# (account, shares, cost_basis_usd, lot_title)
OPENING_LOTS = [
    (VTSAX, D("180.0000"), D("21600"), "VTSAX core position"),
    (VBTLX, D("500.0000"), D("5250"), "VBTLX bond allocation"),
    (AAPL, D("25.0000"), D("4750"), "AAPL 2023 purchase"),
    (MSFT, D("15.0000"), D("5700"), "MSFT 2024 purchase"),
    (ETH, D("2.500000"), D("6000"), "ETH 2024 purchase"),
]


def opening_balances(out_path: Path) -> None:
    """Post opening balances (one balanced transaction) + investment lots."""
    book = piecash.open_book(str(out_path), readonly=False)
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
            splits=splits,
        )

        # Investment opening lots: buy each holding from equity at cost.
        for path, units, cost, title in OPENING_LOTS:
            inv_acct = acct[path]
            lot = piecash.Lot(
                title=title, account=inv_acct,
                notes="opening position", is_closed=0,
            )
            inv_split = piecash.Split(
                account=inv_acct, value=cost, quantity=units,
            )
            eq_split = piecash.Split(account=acct[OPENING], value=-cost)
            piecash.Transaction(
                currency=usd,
                description=f"Opening position — {title}",
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
    from the transaction currency. ``currency`` defaults to USD.
    """
    book = piecash.open_book(str(out_path), readonly=False)
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
                post_date=t["date"],
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

    # Robin's biweekly paycheck (baseline split per spec).
    sx("Robin's Paycheck", "UW Medical biweekly paycheck", [
        {"account": SALARY, "amount": "-3269.23"},
        {"account": CHECKING, "amount": "2450.00"},
        {"account": EXP_FED, "amount": "380.00"},
        {"account": EXP_SS, "amount": "202.69"},
        {"account": EXP_MEDICARE, "amount": "47.40"},
        {"account": EXP_HEALTH, "amount": "145.00"},
        {"account": HSA, "amount": "44.14"},
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
        ("Cloud Hosting (AWS)", "AWS", AMEX, EXP_CLOUD, "125.00"),
        ("Coworking (WeWork)", "WeWork", AMEX, EXP_COWORKING, "250.00"),
        ("Pet Food (Chewy)", "Chewy auto-ship", CHECKING, EXP_PET_FOOD, "48.00"),
    ]
    for name, desc, src, dst, amt in simple_monthly:
        sx(name, desc, [
            {"account": src, "amount": f"-{amt}"},
            {"account": dst, "amount": amt},
        ], "monthly")

    # Quarterly.
    sx("Estimated Tax Payment", "IRS estimated federal tax", [
        {"account": CHECKING, "amount": "-4200.00"},
        {"account": EXP_EST_TAX, "amount": "4200.00"},
    ], "quarterly", start_date="2025-04-15")
    sx("Umbrella Insurance", "Umbrella policy premium", [
        {"account": CHECKING, "amount": "-125.00"},
        {"account": EXP_UMBRELLA, "amount": "125.00"},
    ], "quarterly")

    # Yearly.
    sx("Property Tax (1st Half)", "King County property tax", [
        {"account": CHECKING, "amount": "-3200.00"},
        {"account": EXP_PROP_TAX, "amount": "3200.00"},
    ], "yearly", start_date="2025-04-30")
    sx("Property Tax (2nd Half)", "King County property tax", [
        {"account": CHECKING, "amount": "-3200.00"},
        {"account": EXP_PROP_TAX, "amount": "3200.00"},
    ], "yearly", start_date="2025-10-31")

    return count


# ── Phase 5: Recurring instantiations (direct, with amortization) ─

BASELINE_GROSS = D("3269.23")
FIXED_HEALTH = D("145.00")
FIXED_HSA = D("44.14")

# US payroll withholding, rate-based so every deduction TRACKS gross — an
# overtime paycheck withholds more than a base paycheck, and no two
# different-gross paychecks are identical (the bookkeeper's "frozen
# deductions" tell on Lin Wei, adapted to a US persona).
#
# FICA is exact statutory: Social Security 6.2%, Medicare 1.45% of gross.
# Federal income-tax withholding is PROGRESSIVE: a base marginal rate on
# the regular gross, plus a higher supplemental rate on any overtime portion
# (the IRS 22% flat supplemental-wage rate), so federal is genuinely
# non-proportional and clearly larger in overtime/bonus months — not a flat
# percentage frozen across the year.
SS_RATE = D("0.062")            # Social Security employee share
MED_RATE = D("0.0145")          # Medicare employee share
FED_BASE_RATE = D("0.1162")     # regular-wage federal withholding (≈ prior 380/3269)
FED_SUPP_RATE = D("0.22")       # IRS supplemental rate on overtime/bonus


def _paycheck_splits(gross: Decimal, overtime: Decimal = D("0")):
    ss = (gross * SS_RATE).quantize(D("0.01"))
    med = (gross * MED_RATE).quantize(D("0.01"))
    regular = gross - overtime
    fed = (regular * FED_BASE_RATE
           + overtime * FED_SUPP_RATE).quantize(D("0.01"))
    checking = gross - fed - ss - med - FIXED_HEALTH - FIXED_HSA
    return [
        (SALARY, -gross),
        (CHECKING, checking),
        (EXP_FED, fed),
        (EXP_SS, ss),
        (EXP_MEDICARE, med),
        (EXP_HEALTH, FIXED_HEALTH),
        (HSA, FIXED_HSA),
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


def gen_recurring(through: date) -> list[dict]:
    """Recurring activity from 2025-01-01 through ``through``.

    Paychecks, loan amortization, monthly bills, quarterly/annual
    premiums, property tax, and estimated federal tax all run
    continuously to the present so the book has no "data cliff" — the
    most recent months show realistic income and burn.
    """
    txns: list[dict] = []
    rng = random.Random(SEED + 5)

    # Biweekly paychecks from Jan 10 2025, overtime ~every 3rd-4th.
    d = date(YEAR, 1, 10)
    i = 0
    while d <= through:
        overtime = D("0")
        if i > 0 and i % rng.choice([3, 4]) == 0:
            overtime = D(str(rng.randint(200, 400)))
        gross = BASELINE_GROSS + overtime
        desc = "Robin's Paycheck (UW Medical)"
        if overtime:
            desc += f" - ${overtime} overtime"
        txns.append({"description": desc, "date": d,
                     "splits": _paycheck_splits(gross, overtime)})
        d += timedelta(days=14)
        i += 1

    # Mortgage (1st) + auto loan (5th), true declining-balance
    # amortization carried forward month over month.
    mort_bal = D("385000.00")
    mort_pmt = D("2485.00")
    mort_rate = D("0.0625")
    auto_bal = D("18500.00")
    auto_pmt = D("365.00")
    auto_rate = D("0.0549")
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        first = date(yr, m, 1)
        if first <= through and mort_bal > 0:
            m_int, m_pri, mort_bal = _amortized_split(
                mort_rate, mort_pmt, mort_bal)
            txns.append({
                "description": "Mortgage Payment", "date": first,
                "splits": [(CHECKING, -(m_int + m_pri)),
                           (EXP_MORTGAGE_INT, m_int), (MORTGAGE, m_pri)],
            })
        fifth = _clamp_day(yr, m, 5)
        if fifth <= through and auto_bal > 0:
            a_int, a_pri, auto_bal = _amortized_split(
                auto_rate, auto_pmt, auto_bal)
            txns.append({
                "description": "Auto Loan Payment", "date": fifth,
                "splits": [(CHECKING, -(a_int + a_pri)),
                           (EXP_AUTO_INT, a_int), (AUTO_LOAN, a_pri)],
            })

    # Genuinely-fixed monthly bills (contractual / autopay flat rates) — these
    # stay identical every month.
    fixed_bills = [
        ("HOA Dues", CHECKING, EXP_HOA, D("425.00"), 1),
        ("Streaming Bundle", CHECKING, EXP_STREAMING, D("45.97"), 8),
        ("Pet Food - Chewy", CHECKING, EXP_PET_FOOD, D("48.00"), 20),
        ("AWS Cloud Hosting", AMEX, EXP_CLOUD, D("125.00"), 1),
        ("WeWork Coworking", AMEX, EXP_COWORKING, D("250.00"), 1),
    ]
    for desc, src, dst, amt, day in fixed_bills:
        for yr, m in _month_iter(date(YEAR, 1, 1), through):
            when = _clamp_day(yr, m, day)
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
    # (heating); water peaks in summer (gardens/irrigation).
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
        ("Electric - Seattle City Light", EXP_ELECTRIC, 95.0, ELEC_SEASON),
        ("Gas - Puget Sound Energy", EXP_GAS, 65.0, GAS_SEASON),
        ("Water/Sewer - SPU", EXP_WATER, 55.0, WATER_SEASON),
    ]
    for desc, dst, base, season in seasonal_utils:
        for yr, m in _month_iter(date(YEAR, 1, 1), through):
            when = _clamp_day(yr, m, 15)
            if when <= through:
                amt = _seasonal(base, season, m)
                txns.append({
                    "description": desc, "date": when,
                    "splits": [(CHECKING, -amt), (dst, amt)],
                })

    # Internet + phone: nominally flat, but real bills drift — promo
    # roll-offs, overage, taxes/fees. Small per-bill jitter around the base,
    # with a one-time mid-2026 price bump on internet (a believable rate
    # increase) so the line isn't perfectly uniform across years.
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        when = _clamp_day(yr, m, 3)
        if when <= through:
            net_base = 79.99 if (yr, m) < (2026, 7) else 84.99  # mid-2026 bump
            amt = D(str(round(net_base + rng_util.uniform(-1.5, 4.0), 2)))
            txns.append({
                "description": "Internet - Comcast", "date": when,
                "splits": [(CHECKING, -amt), (EXP_INTERNET, amt)],
            })
        when = _clamp_day(yr, m, 12)
        if when <= through:
            amt = D(str(round(140.0 + rng_util.uniform(-3.0, 6.0), 2)))
            txns.append({
                "description": "Phone - T-Mobile", "date": when,
                "splits": [(CHECKING, -amt), (EXP_PHONE, amt)],
            })

    # Quarterly umbrella insurance (Jan/Apr/Jul/Oct, 15th).
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        if m in (1, 4, 7, 10):
            when = date(yr, m, 15)
            if when <= through:
                txns.append({
                    "description": "Umbrella Insurance Premium",
                    "date": when,
                    "splits": [(CHECKING, D("-125.00")),
                               (EXP_UMBRELLA, D("125.00"))],
                })

    # Property tax halves, every year (Apr 30 / Oct 31).
    for yr in range(YEAR, through.year + 1):
        for mo, day, half in [(4, 30, "1st"), (10, 31, "2nd")]:
            when = date(yr, mo, day)
            if when <= through:
                txns.append({
                    "description": f"King County Property Tax ({half} Half)",
                    "date": when,
                    "splits": [(CHECKING, D("-3200.00")),
                               (EXP_PROP_TAX, D("3200.00"))],
                })

    # Estimated federal tax at real IRS quarterly deadlines.
    for yr in range(YEAR, through.year + 2):
        for mo, day, q in [(4, 15, "Q1"), (6, 15, "Q2"),
                           (9, 15, "Q3"), (1, 15, "Q4")]:
            # Q4 of tax-year Y is paid the following January.
            tax_year = yr - 1 if q == "Q4" else yr
            if tax_year < YEAR:
                continue
            when = date(yr, mo, day)
            if when <= through:
                txns.append({
                    "description": f"Estimated Federal Tax - {q} {tax_year}",
                    "date": when,
                    "splits": [(CHECKING, D("-4200.00")),
                               (EXP_EST_TAX, D("4200.00"))],
                })

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
AMAZON_CATEGORIES = [
    ("household goods", EXP_MISC),
    ("pet supplies", EXP_PET_FOOD),
    ("books", EXP_EDUCATION),
    ("office supplies", EXP_SOFTWARE),
    ("kitchen goods", EXP_MISC),
]
CLOTHING_VENDORS = ["Target", "Nordstrom", "REI"]

# (month, day, description, amount_str, source, target).
# Negative amount = refund/reversal.
MONTHLY_EVENTS = [
    (1, 15, "Byte's vet visit", "180", CHECKING, EXP_PET_VET),
    (1, 10, "New Year gift return", "-45", CHASE, EXP_GIFTS),
    (2, 14, "Valentine's dinner - Canlis", "165", CHASE, EXP_DINING),
    (2, 22, "Ski trip - Snoqualmie", "340", CHASE, EXP_TRAVEL),
    (3, 5, "TurboTax Home & Business", "89", CHASE, EXP_SUBSCRIPTIONS),
    (3, 18, "Spring clothing", "210", CHASE, EXP_CLOTHING),
    (4, 12, "Byte's annual checkup", "320", CHECKING, EXP_PET_VET),
    (5, 24, "Memorial Day BBQ supplies", "95", CHECKING, EXP_GROCERIES),
    (5, 10, "Garden supplies", "67", CHASE, EXP_HOUSING_MAINT),
    (6, 28, "Pride festival food", "120", CHASE, EXP_DINING),
    (6, 28, "Pride festival merch", "85", CHASE, EXP_MISC),
    (6, 15, "Anniversary dinner", "225", CHASE, EXP_DINING),
    (7, 4, "4th of July party supplies", "145", CHECKING, EXP_DINING),
    (7, 15, "Summer road trip - lodging", "890", CHASE, EXP_TRAVEL),
    (7, 18, "Road trip fuel", "340", CHASE, EXP_FUEL),
    (8, 8, "Dell U2725D monitor", "450", AMEX, EXP_SOFTWARE),
    (9, 3, "PyCon US conference ticket", "799", AMEX, EXP_PROF_DEV),
    (9, 1, "Labor Day camping", "280", CHASE, EXP_TRAVEL),
    (10, 28, "Halloween supplies", "65", CHECKING, EXP_MISC),
    (10, 20, "Byte vet visit", "150", CHECKING, EXP_PET_VET),
    (11, 25, "Thanksgiving groceries", "185", CHECKING, EXP_GROCERIES),
    (11, 28, "Black Friday - Target", "105", CHASE, EXP_CLOTHING),
    (11, 28, "Black Friday - REI", "125", CHASE, EXP_CLOTHING),
    (11, 29, "Black Friday - Nordstrom", "135", CHASE, EXP_CLOTHING),
    (11, 29, "Cyber Monday - Amazon", "55", CHASE, EXP_MISC),
    (12, 10, "Holiday gift - Robin", "120", CHASE, EXP_GIFTS),
    (12, 12, "Holiday gift - Mom", "85", CHASE, EXP_GIFTS),
    (12, 14, "Holiday gift - Dad", "95", CHASE, EXP_GIFTS),
    (12, 15, "Holiday gift - sister", "65", CHASE, EXP_GIFTS),
    (12, 18, "Holiday gift - coworkers", "75", CHASE, EXP_GIFTS),
    (12, 20, "Holiday gift - friends group", "80", CHASE, EXP_GIFTS),
    (12, 22, "Holiday gift - nieces", "90", CHASE, EXP_GIFTS),
    (12, 23, "Holiday gift - last-minute", "40", CHASE, EXP_GIFTS),
    (12, 26, "Holiday travel - flights", "580", CHASE, EXP_TRAVEL),
    (12, 30, "Year-end donation - NAMI", "500", CHECKING, EXP_CHARITY),
]


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
# particular, "Transit Pass"/"Parking Meter" book to Auto:Fuel (transport's
# nearest home in this chart) — NOT Dining.
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
    # Transport — no Transport account in this chart, so the consistent home
    # is Auto:Fuel (NOT Dining — that was the kind of wrong mapping the
    # bookkeeper flagged).
    "Parking Meter": EXP_FUEL,
    "Transit Pass": EXP_FUEL,
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

    # Amazon 2-3x/month on Chase.
    rng = random.Random(SEED + 5)
    for yr, m in _month_iter(start, through):
        for _ in range(rng.randint(2, 3)):
            day = _clamp_day(yr, m, rng.randint(1, 28))
            if day > through:
                continue
            descriptor, expense = rng.choice(AMAZON_CATEGORIES)
            amt = _spend(rng, 15, 120)
            txns.append({"description": f"Amazon.com - {descriptor}",
                         "date": day,
                         "splits": [(CHASE, -amt), (expense, amt)]})

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

    # Seasonal one-offs, replayed each calendar year in range. The planned
    # dollar figure is a budget target; the actual receipt carries realistic
    # cents (real dinners/gifts/trips don't ring up on a whole dollar). A
    # dedicated RNG keeps these deterministic and decoupled from the daily
    # streams. Refunds (negative) stay exact — they reverse a known charge.
    rng_event = random.Random(SEED + 11)
    for yr in range(YEAR, through.year + 1):
        for month, day, desc, amt_str, src, dst in MONTHLY_EVENTS:
            when = _clamp_day(yr, month, day)
            if when > through:
                continue
            amt = D(amt_str)
            if amt < 0:
                splits = [(src, abs(amt)), (dst, -abs(amt))]
            else:
                cents = D(rng_event.randint(0, 99)) / D(100)
                amt = (amt + cents).quantize(D("0.01"))
                splits = [(src, -amt), (dst, amt)]
            txns.append({"description": desc, "date": when, "splits": splits})

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
GIFT_OCCASIONS = [
    "birthday gift - Robin's friend", "birthday gift - coworker",
    "birthday gift - niece", "birthday gift - brother-in-law",
    "housewarming gift", "wedding gift", "baby shower gift",
]


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
    for yr, m in _month_iter(start, through):
        if m == 12:
            continue  # December carried by the named list + spike below
        if rng.random() < 0.85:  # ~5-6 of every 6 months get a small gift
            day = _clamp_day(yr, m, rng.randint(3, 26))
            if day <= through:
                amt = _spend(rng, 30.0, 60.0)
                occ = rng.choice(GIFT_OCCASIONS)
                txns.append({"description": occ, "date": day,
                             "splits": [(CHASE, -amt), (EXP_GIFTS, amt)]})
    for yr in range(YEAR, through.year + 1):
        # 5-6 bigger occasion gifts per year, spread across non-December
        # months, for the realistic lumpiness on top of the baseline so
        # any 5-month window catches a couple.
        n_gifts = rng.randint(5, 6)
        pool = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
        chosen_months = rng.sample(pool, k=min(n_gifts, len(pool)))
        for m in chosen_months:
            day = _clamp_day(yr, m, rng.randint(3, 26))
            if day > through:
                continue
            amt = _spend(rng, 60.0, 120.0)
            occ = rng.choice(GIFT_OCCASIONS)
            txns.append({"description": occ, "date": day,
                         "splits": [(CHASE, -amt), (EXP_GIFTS, amt)]})
        # A consolidated ~$300 early-December holiday-gift haul (on top
        # of the named per-recipient gifts in MONTHLY_EVENTS).
        dday = _clamp_day(yr, 12, rng.randint(3, 8))
        if dday <= through:
            amt = _spend(rng, 280.0, 320.0)
            txns.append({"description": "Holiday gift haul",
                         "date": dday,
                         "splits": [(CHASE, -amt), (EXP_GIFTS, amt)]})

    # ── Charity: recurring monthly donation (~$50-75) + a December ──
    #    year-end gift (~$300-500). The MONTHLY_EVENTS NAMI year-end
    #    donation already exists; this adds the steady monthly habit
    #    plus a second December gift so charity reads as ongoing.
    for yr, m in _month_iter(start, through):
        day = _clamp_day(yr, m, 5)
        if day <= through:
            amt = _spend(rng, 50.0, 75.0)
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
        day = _clamp_day(yr, m, 2)
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
    #    (CAD client). Booked in USD on Chase (travel charged stateside).
    #    One trip every ~5-6 months across the whole timeline, so any
    #    recent-5-month window always catches at least one trip. Anchored
    #    to month 3 then stepped +5/+6 months alternately. Keeps the
    #    light travel above intact.
    trip_anchor = date(YEAR, 3, 1)
    # (city, flight-vendor, hotel-label) alternating across trips.
    trip_specs = [
        ("Berlin", "Lufthansa - SEA-BER (Berlin client visit)",
         "Hotel - Berlin (client visit, 4 nights)"),
        ("Toronto", "Air Canada - SEA-YYZ (Nord client visit)",
         "Hotel - Toronto (client visit, 3 nights)"),
    ]
    trip_idx = 0
    cur = trip_anchor
    while cur <= through:
        flight_day = _clamp_day(cur.year, cur.month, rng.randint(18, 23))
        hotel_day = _clamp_day(cur.year, cur.month, rng.randint(24, 27))
        flight = _spend(rng, 980.0, 1180.0)
        hotel = _spend(rng, 620.0, 820.0)
        _, flight_desc, hotel_desc = trip_specs[trip_idx % len(trip_specs)]
        if flight_day <= through:
            txns.append({"description": flight_desc, "date": flight_day,
                         "splits": [(CHASE, -flight), (EXP_TRAVEL, flight)]})
        if hotel_day <= through:
            txns.append({"description": hotel_desc, "date": hotel_day,
                         "splits": [(CHASE, -hotel), (EXP_TRAVEL, hotel)]})
        # Step +5 or +6 months alternately so trips drift through the
        # calendar and the cadence isn't mechanically regular.
        step = 5 if trip_idx % 2 == 0 else 6
        nm = cur.month - 1 + step
        cur = date(cur.year + nm // 12, nm % 12 + 1, 1)
        trip_idx += 1

    return txns


# ── Phase 7a: Direct 1099 contractor income ─────────────────────

CONTRACTOR_CLIENTS = [
    ("TechStartup Inc", D("4500")),
    ("DataFlow Systems", D("6000")),
    ("CloudNine Consulting", D("3800")),
    ("WinterTech Solutions", D("5200")),
]


def gen_contractor_income(through: date) -> list[dict]:
    """1099 deposits on the 15th, ~8 months a year (gaps are realistic
    for a contractor), continuing through ``through``."""
    rng = random.Random(SEED + 7)
    txns: list[dict] = []
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        # ~2/3 of months have a 1099 deposit.
        if rng.random() > 0.66:
            continue
        when = date(yr, m, 15)
        if when > through:
            continue
        client, amt = rng.choice(CONTRACTOR_CLIENTS)
        txns.append({
            "description": f"{client} - monthly invoice payment",
            "date": when,
            "splits": [(CHECKING, amt), (CONTRACTOR, -amt)],
        })
    return txns


# ── Phase 7c: Cash management — surplus sweeps out of checking ──

# A savvy tech freelancer wouldn't park six figures in a non-interest
# checking account. Without active cash management, Alex's checking
# balloons to ~$155K (income outpaces spend). These sweeps move the
# surplus into savings and the brokerage on an ongoing cadence so
# checking settles into a realistic ~$40K-$60K operating buffer. Net
# worth is unchanged — cash is reclassified into other assets, and
# A = L + E still balances. VTSAX sweeps buy fractional fund shares at
# real prices (realistic for a Vanguard mutual fund) and open their own
# lots, exactly like the DCA path.

MONTHLY_SAVINGS_SWEEP = D("4000.00")     # checking -> savings, monthly
QUARTERLY_VTSAX_SWEEP = D("14000.00")    # checking -> VTSAX, quarterly


def gen_savings_sweep(through: date) -> list[dict]:
    """Monthly surplus transfer from Checking into Savings (on the 27th).

    Skips the opening month so the sweep reads as a habit that starts
    once the first months of income have landed.
    """
    txns: list[dict] = []
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        when = _clamp_day(yr, m, 27)
        # Start sweeping from February 2025 onward.
        if when < date(YEAR, 2, 1) or when > through:
            continue
        txns.append({
            "description": "Transfer to savings (monthly surplus sweep)",
            "date": when,
            "splits": [(CHECKING, -MONTHLY_SAVINGS_SWEEP),
                       (SAVINGS, MONTHLY_SAVINGS_SWEEP)],
        })
    return txns


def run_vtsax_sweep(out_path: Path, through: date) -> dict:
    """Quarterly surplus sweep from Checking into VTSAX (fractional shares).

    A separate lot per sweep, cost = USD swept, shares at the real VTSAX
    close on the sweep date. Mirrors the DCA path so lot/gain tooling
    sees consistent data.
    """
    book = piecash.open_book(str(out_path), readonly=False)
    counts = {"txns": 0, "lots": 0}
    try:
        usd = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        frac = {s[0]: s[3] for s in SECURITIES}["VTSAX"]
        inv_acct = acct[VTSAX]
        # Quarterly: Mar/Jun/Sep/Dec on the 20th.
        for yr, m in _month_iter(date(YEAR, 1, 1), through):
            if m not in (3, 6, 9, 12):
                continue
            when = _clamp_day(yr, m, 20)
            if when < date(YEAR, 2, 1) or when > through:
                continue
            price = MD.security("VTSAX", when).quantize(_security_quant("VTSAX"))
            shares = _shares_from_usd(QUARTERLY_VTSAX_SWEEP, price, frac)
            lot = piecash.Lot(
                title=f"VTSAX sweep {when.isoformat()}", account=inv_acct,
                notes=f"Surplus sweep — ${QUARTERLY_VTSAX_SWEEP} @ ${price}",
                is_closed=0,
            )
            inv_split = piecash.Split(
                account=inv_acct, value=QUARTERLY_VTSAX_SWEEP, quantity=shares)
            cash_split = piecash.Split(
                account=acct[CHECKING], value=-QUARTERLY_VTSAX_SWEEP)
            piecash.Transaction(
                currency=usd,
                description="Surplus sweep to VTSAX",
                post_date=when, splits=[inv_split, cash_split])
            inv_split.lot = lot
            counts["txns"] += 1
            counts["lots"] += 1
        book.save()
    finally:
        book.close()
    return counts


# ── Phase 7b: Business module (customers, vendors, invoices, bills)

# Berlin Digital invoices a quarter apart; the EUR amount cycles through
# this list. Generated dynamically through THROUGH so EUR A/R has both
# settled invoices (realized FX gain/loss) AND recent open ones.
BERLIN_AMOUNTS = ["4500.00", "6200.00", "4500.00", "5800.00", "5100.00"]


def _berlin_invoice_plan(through: date) -> list[dict]:
    """Quarterly Berlin EUR invoices, opened on the 8th, paid ~30 days
    later. Invoices whose payment date is past ``through`` are left
    POSTED-BUT-UNPAID. Returns dicts with open/pay dates + paid flag.
    """
    plan: list[dict] = []
    i = 0
    for yr, m in _month_iter(date(YEAR, 3, 1), through):
        if m not in (3, 6, 9, 12):
            continue
        date_open = date(yr, m, 8)
        if date_open > through:
            continue
        date_pay = date_open + timedelta(days=30)
        amount = BERLIN_AMOUNTS[i % len(BERLIN_AMOUNTS)]
        i += 1
        plan.append({
            "date_open": date_open,
            "date_pay": date_pay,
            "amount": amount,
            "paid": date_pay <= through,
        })
    return plan


def _berlin_recent_open_date(through: date) -> date | None:
    """Open date for the deliberately-outstanding recent Berlin EUR
    invoice (~20 days before ``through``), or None if too early."""
    when = through - timedelta(days=20)
    return when if when >= date(YEAR, 1, 1) else None


# Nord Analytique (Montréal) invoices in CAD ~every 2 months. The CAD
# amount cycles through this list. Settled ones cross CAD->USD with
# realized FX gain/loss; the most recent one is left OUTSTANDING so CAD
# A/R carries a live foreign-currency balance alongside EUR.
NORD_AMOUNTS = ["6800.00", "5200.00", "7400.00", "6100.00", "5900.00"]


def _nord_invoice_plan(through: date) -> list[dict]:
    """Bi-monthly Nord CAD invoices, opened on the 12th of odd months,
    paid ~30 days later. The single most-recent invoice is left
    POSTED-BUT-UNPAID (outstanding CAD A/R); all earlier ones are paid
    (cross-currency realized FX gain/loss). Returns dicts with
    open/pay dates + paid flag.
    """
    raw: list[dict] = []
    i = 0
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        if m % 2 == 0:  # invoice in odd months (Jan, Mar, May, ...)
            continue
        date_open = date(yr, m, 12)
        if date_open > through:
            continue
        amount = NORD_AMOUNTS[i % len(NORD_AMOUNTS)]
        i += 1
        raw.append({
            "date_open": date_open,
            "date_pay": date_open + timedelta(days=30),
            "amount": amount,
        })
    # Leave the single most-recent invoice outstanding so CAD A/R always
    # carries a live balance near the horizon; pay everything older.
    for idx, inv in enumerate(raw):
        is_last = idx == len(raw) - 1
        inv["paid"] = (not is_last) and inv["date_pay"] <= through
    return raw


def business_event_price_dates(through: date) -> list[tuple[str, date]]:
    """EUR + CAD price dates needed for invoice post + pay (real rates).

    Every open date needs a rate (post); every settled invoice also needs
    a rate on its pay date for the cross-currency realized FX gain/loss.
    """
    out: list[tuple[str, date]] = []
    for inv in _berlin_invoice_plan(through):
        out.append(("EUR", inv["date_open"]))
        if inv["paid"]:
            out.append(("EUR", inv["date_pay"]))
    recent = _berlin_recent_open_date(through)
    if recent is not None:
        out.append(("EUR", recent))
    for inv in _nord_invoice_plan(through):
        out.append(("CAD", inv["date_open"]))
        if inv["paid"]:
            out.append(("CAD", inv["date_pay"]))
    return out


def run_business(book: GnuCashBook, through: date) -> dict:
    """Create billterms, customers, vendors, invoices, bills, and jobs.

    A realistic slice of invoices is left POSTED-BUT-UNPAID so the book
    shows outstanding receivables in both USD and EUR A/R; the rest are
    paid (Berlin's settled EUR invoices still book realized FX gain/loss).
    Three jobs group project invoices across customers.
    """
    counts = {"customers": 0, "vendors": 0, "invoices": 0, "bills": 0,
              "terms": 0, "employees": 0, "jobs": 0, "open_invoices": 0}

    book.create_billterm(name="Net 15", due_days=15,
                         description="Payment due within 15 days")
    book.create_billterm(name="Net 30", due_days=30,
                         description="Payment due within 30 days")
    book.create_billterm(name="2/10 Net 30", due_days=30, discount_days=10,
                         discount_percent="2",
                         description="2% discount if paid in 10 days, else net 30")
    counts["terms"] = 3

    # Customers. Notes are natural client descriptors — nothing that names a
    # test scenario (no "EUR-denominated", "FX case", currency tags, etc.).
    emerald = book.create_customer(
        name="Emerald Analytics", currency="USD",
        notes="Seattle analytics firm; monthly data-engineering retainer.")
    sound_transit = book.create_customer(
        name="Sound Transit Data Team", currency="USD",
        notes="Regional transit agency; project-based engagements.")
    berlin = book.create_customer(
        name="Berlin Digital GmbH", currency="EUR",
        notes="Digital agency in Berlin; recurring engagements.")
    nord = book.create_customer(
        name="Nord Analytique", currency="CAD",
        notes="Montréal data consultancy.")
    counts["customers"] = 4

    # Vendors.
    jetbrains = book.create_vendor(
        name="JetBrains", currency="USD", notes="Developer IDE and tooling.")
    bookkeeper = book.create_vendor(
        name="BookkeepingCo", currency="USD",
        notes="Outsourced bookkeeping firm.")
    counts["vendors"] = 2

    # Employee.
    book.create_employee(name="Sam Rivera", currency="USD")
    counts["employees"] = 1

    def run_invoice(customer_id, date_open, date_pay, amount, description,
                    currency, post_account, paid=True, job_id=None):
        """Create + post an invoice; pay it only when ``paid`` is True.

        Returns the invoice id. ``date_open`` / ``date_pay`` are dates.
        """
        cross = currency != "USD"
        inv = book.create_invoice(
            customer_id=customer_id, date_opened=date_open.isoformat(),
            currency=currency, term="Net 30", job_id=job_id,
        )
        book.add_invoice_entry(
            invoice_id=inv["id"], account=LLC_REVENUE,
            description=description, quantity="1", price=amount,
        )
        book.post_invoice(
            invoice_id=inv["id"], post_account=post_account,
            post_date=date_open.isoformat(), owner_type="customer",
            force=cross,
        )
        if paid:
            book.pay_invoice(
                invoice_id=inv["id"], payment_account=CHECKING,
                amount=amount, payment_date=date_pay.isoformat(),
                owner_type="customer", force=cross,
            )
        else:
            counts["open_invoices"] += 1
        counts["invoices"] += 1
        return inv["id"]

    # Emerald: $3,500/month retainer, every month through ``through``.
    # The two most-recent retainers are left open (outstanding A/R);
    # everything older is paid ~27 days after opening.
    emerald_months = [
        (yr, m) for yr, m in _month_iter(date(YEAR, 1, 1), through)
        if date(yr, m, 1) <= through
    ]
    for idx, (yr, m) in enumerate(emerald_months):
        date_open = date(yr, m, 1)
        date_pay = date_open + timedelta(days=27)
        # Open if it's one of the last two months, or its pay date is
        # still in the future.
        is_recent = idx >= len(emerald_months) - 2
        paid = (not is_recent) and date_pay <= through
        run_invoice(
            emerald["id"], date_open, date_pay, "3500.00",
            f"{date_open.strftime('%B %Y')} consulting retainer",
            "USD", AR_USD, paid=paid,
        )

    # Sound Transit: larger project invoices, Net 15. Most paid; the
    # last one left open as a recent outstanding receivable.
    st_plan = []
    qn = 0
    for yr, m in _month_iter(date(YEAR, 2, 1), through):
        if m not in (2, 6, 10):
            continue
        date_open = date(yr, m, 3)
        if date_open > through:
            continue
        amount = ["8500.00", "12000.00", "8500.00"][qn % 3]
        qn += 1
        st_plan.append((date_open, amount))
    for idx, (date_open, amount) in enumerate(st_plan):
        date_pay = date_open + timedelta(days=15)
        paid = (idx < len(st_plan) - 1) and date_pay <= through
        run_invoice(
            sound_transit["id"], date_open, date_pay, amount,
            f"Data engineering services - {date_open.strftime('%B %Y')}",
            "USD", AR_USD, paid=paid,
        )

    # Berlin Digital: EUR invoices -> EUR A/R. Settled ones cross to USD
    # with realized FX gain/loss; recent ones left open (EUR A/R balance).
    for inv in _berlin_invoice_plan(through):
        run_invoice(
            berlin["id"], inv["date_open"], inv["date_pay"], inv["amount"],
            f"Berlin Digital engagement - "
            f"{inv['date_open'].strftime('%B %Y')}",
            "EUR", AR_EUR, paid=inv["paid"],
        )

    # One recent Berlin EUR invoice opened ~20 days before ``through`` and
    # left OUTSTANDING, so EUR A/R carries a live foreign-currency balance
    # (the quarterly cadence alone can leave EUR A/R empty near the
    # horizon). Its post date is added to the event-price dates so the
    # cross-currency post finds a real EUR/USD rate.
    berlin_open_date = _berlin_recent_open_date(through)
    if berlin_open_date is not None:
        run_invoice(
            berlin["id"], berlin_open_date,
            berlin_open_date + timedelta(days=30), "5400.00",
            f"Berlin Digital engagement - "
            f"{berlin_open_date.strftime('%B %Y')}",
            "EUR", AR_EUR, paid=False,
        )

    # Nord Analytique: CAD invoices -> CAD A/R. Settled ones cross CAD to
    # USD with realized FX gain/loss; the most recent one is left open so
    # CAD A/R carries a live foreign-currency balance (a genuine CAD
    # multi-currency surface alongside Berlin's EUR).
    for inv in _nord_invoice_plan(through):
        run_invoice(
            nord["id"], inv["date_open"], inv["date_pay"], inv["amount"],
            f"Nord Analytique data services - "
            f"{inv['date_open'].strftime('%B %Y')}",
            "CAD", AR_CAD, paid=inv["paid"],
        )

    # ── Jobs: multi-invoice projects over customers ──
    # Milestones are dated BACKWARD from ``through`` so each job's
    # OUTSTANDING milestone lands in a realistic recent aging window
    # (anchor offset below). Year-old open invoices would look like
    # corruption (no write-off), so the unpaid milestones are kept
    # current-to-recently-past-due; paid milestones step back in time
    # from the anchor. ``anchor_days`` = days before ``through`` that the
    # job's LAST milestone opens.
    jobs_specs = [
        (sound_transit["id"], "Sound Transit Q1 Migration", "ST-MIG-Q1",
         [("4500.00", True), ("4500.00", True)], 95),
        (emerald["id"], "Emerald Dashboard Revamp", "EM-DASH",
         [("6000.00", True), ("5500.00", False)], 28),
        (sound_transit["id"], "Sound Transit Realtime Feed", "ST-RT",
         [("7200.00", False)], 12),
    ]
    for owner_id, jname, jref, milestones, anchor_days in jobs_specs:
        last_open = through - timedelta(days=anchor_days)
        first_open = last_open - timedelta(days=30 * (len(milestones) - 1))
        if first_open < date(YEAR, 1, 1):
            continue
        job = book.create_job(
            owner_id=owner_id, owner_type="customer",
            name=jname, reference=jref,
        )
        counts["jobs"] += 1
        for i, (amount, paid) in enumerate(milestones):
            mo = first_open + timedelta(days=30 * i)
            if mo > through:
                break
            run_invoice(
                owner_id, mo, mo + timedelta(days=20), amount,
                f"{jname} — milestone {i + 1}", "USD", AR_USD,
                paid=(paid and mo + timedelta(days=20) <= through),
                job_id=job["id"],
            )

    # Vendor bills.
    def run_bill(vendor_id, date_open, date_pay, amount, description,
                 expense_account, payment_account=CHECKING, paid=True):
        bill = book.create_bill(
            vendor_id=vendor_id, date_opened=date_open.isoformat(),
            term="Net 30",
        )
        book.add_bill_entry(
            bill_id=bill["id"], account=expense_account,
            description=description, quantity="1", price=amount,
        )
        book.post_invoice(
            invoice_id=bill["id"], post_account=AP,
            post_date=date_open.isoformat(), owner_type="vendor",
        )
        if paid:
            book.pay_invoice(
                invoice_id=bill["id"], payment_account=payment_account,
                amount=amount, payment_date=date_pay.isoformat(),
                owner_type="vendor",
            )
        counts["bills"] += 1
        return bill["id"]

    # JetBrains annual ($289), paid from Business Amex.
    run_bill(
        jetbrains["id"], date(YEAR, 1, 12), date(YEAR, 1, 25), "289.00",
        "JetBrains IntelliJ IDEA Ultimate subscription (annual)",
        EXP_SOFTWARE, payment_account=AMEX,
    )

    # BookkeepingCo quarterly ($450), every quarter through ``through``.
    for yr, m in _month_iter(date(YEAR, 3, 1), through):
        if m not in (3, 6, 9, 12):
            continue
        date_open = date(yr, m, 5)
        if date_open > through:
            continue
        run_bill(
            bookkeeper["id"], date_open, date_open + timedelta(days=15),
            "450.00",
            f"Quarterly bookkeeping review - Q{(m - 1) // 3 + 1} {yr}",
            EXP_ACCOUNTING,
        )

    # Re-dated outstanding bill: a recent BookkeepingCo bill left UNPAID
    # so Accounts Payable shows a current outstanding balance (rather than
    # a stale/corrupt-looking one). Opened ~10 days before ``through``.
    recent_open = through - timedelta(days=10)
    run_bill(
        bookkeeper["id"], recent_open, recent_open + timedelta(days=30),
        "450.00",
        f"Quarterly bookkeeping review - {recent_open.strftime('%B %Y')}",
        EXP_ACCOUNTING, paid=False,
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

# (month, day, action, symbol, shares) — price comes from real market data.
QUARTERLY_TRADES = [
    (3, 10, "buy", "AAPL", D("5.0000")),
    (5, 15, "sell", "AAPL", D("3.0000")),
    (7, 20, "buy", "ETH", D("0.500000")),
    (8, 15, "buy", "MSFT", D("10.0000")),
    (10, 8, "sell", "ETH", D("1.000000")),
    (11, 18, "sell", "MSFT", D("5.0000")),
    (12, 15, "sell", "VBTLX", D("100.0000")),
    (12, 16, "buy", "VBTLX", D("100.0000")),
]

# Reinvested dividends (per-share rates from the spec).
DIVIDENDS_PLAN = [
    (3, 15, "VTSAX", D("68.00")), (6, 15, "VTSAX", D("73.00")),
    (9, 15, "VTSAX", D("79.00")), (12, 15, "VTSAX", D("84.00")),
    (2, 15, "AAPL", D("6.25")), (5, 15, "AAPL", D("7.50")),
    (8, 15, "AAPL", D("6.75")), (11, 15, "AAPL", D("6.75")),
    (3, 15, "MSFT", D("11.25")), (6, 15, "MSFT", D("11.25")),
    (9, 15, "MSFT", D("18.75")), (12, 15, "MSFT", D("15.00")),
]


def investment_event_price_dates(through: date) -> list[tuple[str, date]]:
    """All security price dates needed for trades + dividends (real data),
    spanning the full activity window through ``through``."""
    out: list[tuple[str, date]] = []
    # DCA on the 1st (already covered by monthly price points, but be safe).
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        d = date(yr, m, 1)
        if d <= through:
            out.append(("VTSAX", d))
            out.append(("VBTLX", d))
    # 2025 fixed quarterly trades.
    for m, day, _a, sym, _sh in QUARTERLY_TRADES:
        out.append((sym, date(YEAR, m, day)))
    # 2026+ whole-share stock buys (Feb/May/Aug/Nov, 10th).
    for yr, m in _month_iter(date(YEAR + 1, 1, 1), through):
        if m in (2, 5, 8, 11):
            d = date(yr, m, 10)
            if d <= through:
                out.append(("AAPL", d))
                out.append(("MSFT", d))
    # Dividends, replayed each year.
    for yr in range(YEAR, through.year + 1):
        for m, day, sym, _amt in DIVIDENDS_PLAN:
            d = date(yr, m, day)
            if d <= through:
                out.append((sym, d))
    # Quarterly VTSAX surplus sweeps (Mar/Jun/Sep/Dec, 20th) need a real
    # VTSAX price on the sweep date for the fractional-share math.
    for yr, m in _month_iter(date(YEAR, 1, 1), through):
        if m in (3, 6, 9, 12):
            d = _clamp_day(yr, m, 20)
            if date(YEAR, 2, 1) <= d <= through:
                out.append(("VTSAX", d))
    return out


def _shares_from_usd(usd: Decimal, price: Decimal, fraction: int) -> Decimal:
    places = D(1) / D(fraction)
    return (usd / price).quantize(places)


def run_investments(out_path: Path, through: date) -> dict:
    """Monthly DCA, quarterly trades, reinvested dividends, plus recent
    whole-share stock buys — continuing through ``through``. Direct
    piecash."""
    book = piecash.open_book(str(out_path), readonly=False)
    counts = {"txns": 0, "lots": 0}
    try:
        usd = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        frac = {s[0]: s[3] for s in SECURITIES}

        def find_lot(title):
            for a in book.accounts:
                for lot in a.lots:
                    if lot.title == title:
                        return lot
            return None

        # Monthly DCA: $500 VTSAX + $200 VBTLX on the 1st (real prices).
        # Funds → fractional shares (realistic for Vanguard mutual funds).
        # Runs continuously through ``through``.
        dca = [("VTSAX", D("500.00")), ("VBTLX", D("200.00"))]
        for yr, m in _month_iter(date(YEAR, 1, 1), through):
            d = date(yr, m, 1)
            if d > through:
                continue
            for sym, amt in dca:
                price = MD.security(sym, d).quantize(_security_quant(sym))
                shares = _shares_from_usd(amt, price, frac[sym])
                inv_acct = acct[ACCT_BY_SYMBOL[sym]]
                lot = piecash.Lot(
                    title=f"{sym} DCA {yr}-{m:02d}", account=inv_acct,
                    notes=f"Monthly DCA — ${amt} @ ${price}", is_closed=0,
                )
                inv_split = piecash.Split(
                    account=inv_acct, value=amt, quantity=shares)
                cash_split = piecash.Split(account=acct[CHECKING], value=-amt)
                piecash.Transaction(
                    currency=usd, description=f"DCA {sym}",
                    post_date=d, splits=[inv_split, cash_split])
                inv_split.lot = lot
                counts["txns"] += 1
                counts["lots"] += 1

        # Quarterly WHOLE-SHARE stock buys (AAPL/MSFT are individual
        # stocks — you buy whole shares). Cost = shares × real price.
        # Starts after the 2025 fixed trade calendar so it doesn't
        # collide; runs through ``through``.
        stock_dca = [("AAPL", 2), ("MSFT", 1)]  # whole shares per quarter
        for yr, m in _month_iter(date(YEAR + 1, 1, 1), through):
            if m not in (2, 5, 8, 11):
                continue
            d = date(yr, m, 10)
            if d > through:
                continue
            for sym, n_shares in stock_dca:
                price = MD.security(sym, d).quantize(_security_quant(sym))
                shares = D(n_shares)  # WHOLE shares
                usd_amt = (shares * price).quantize(D("0.01"))
                inv_acct = acct[ACCT_BY_SYMBOL[sym]]
                lot = piecash.Lot(
                    title=f"{sym} buy {d.isoformat()}", account=inv_acct,
                    notes=f"{shares} whole shares @ ${price}", is_closed=0,
                )
                inv_split = piecash.Split(
                    account=inv_acct, value=usd_amt, quantity=shares)
                cash_split = piecash.Split(
                    account=acct[CHECKING], value=-usd_amt)
                piecash.Transaction(
                    currency=usd,
                    description=f"Buy {shares} {sym} @ ${price}",
                    post_date=d, splits=[inv_split, cash_split])
                inv_split.lot = lot
                counts["txns"] += 1
                counts["lots"] += 1

        # Quarterly trades at real prices.
        for m, day, action, sym, shares in QUARTERLY_TRADES:
            d = date(YEAR, m, day)
            price = MD.security(sym, d).quantize(_security_quant(sym))
            inv_acct = acct[ACCT_BY_SYMBOL[sym]]
            usd_amt = (shares * price).quantize(D("0.01"))
            if action == "buy":
                lot = piecash.Lot(
                    title=f"{sym} {d.isoformat()} purchase", account=inv_acct,
                    notes=f"{shares} shares @ ${price}", is_closed=0,
                )
                inv_split = piecash.Split(
                    account=inv_acct, value=usd_amt, quantity=shares)
                cash_split = piecash.Split(account=acct[CHECKING], value=-usd_amt)
                piecash.Transaction(
                    currency=usd, description=f"Buy {shares} {sym} @ ${price}",
                    post_date=d, splits=[inv_split, cash_split])
                inv_split.lot = lot
                counts["lots"] += 1
            else:
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
                piecash.Transaction(
                    currency=usd, description=f"Sell {shares} {sym} @ ${price}",
                    post_date=d, splits=[inv_split, cash_split, gain_split])
                inv_split.lot = lot
            counts["txns"] += 1

        # Reinvested dividends at real prices, replayed each year
        # through ``through``.
        # VTSAX (a fund) reinvests its dividends — fractional DRIP shares
        # are realistic. AAPL/MSFT (individual stocks) pay dividends in
        # CASH to Checking, so their share counts stay WHOLE.
        for yr in range(YEAR, through.year + 1):
            for m, day, sym, amt in DIVIDENDS_PLAN:
                d = date(yr, m, day)
                if d > through:
                    continue
                inv_acct = acct[ACCT_BY_SYMBOL[sym]]
                if sym in ("AAPL", "MSFT"):
                    # Cash dividend → Checking (no new shares).
                    cash_split = piecash.Split(
                        account=acct[CHECKING], value=amt)
                    income_split = piecash.Split(
                        account=acct[DIVIDENDS], value=-amt)
                    piecash.Transaction(
                        currency=usd,
                        description=f"{sym} dividend (cash)",
                        post_date=d, splits=[cash_split, income_split])
                    counts["txns"] += 1
                else:
                    price = MD.security(sym, d).quantize(
                        _security_quant(sym))
                    shares = _shares_from_usd(amt, price, frac[sym])
                    lot = piecash.Lot(
                        title=f"{sym} dividend {d.isoformat()}",
                        account=inv_acct,
                        notes=f"Reinvested dividend — ${amt} @ ${price}",
                        is_closed=0,
                    )
                    inv_split = piecash.Split(
                        account=inv_acct, value=amt, quantity=shares)
                    income_split = piecash.Split(
                        account=acct[DIVIDENDS], value=-amt)
                    piecash.Transaction(
                        currency=usd,
                        description=f"{sym} dividend (reinvested)",
                        post_date=d, splits=[inv_split, income_split])
                    inv_split.lot = lot
                    counts["txns"] += 1
                    counts["lots"] += 1

        book.save()
    finally:
        book.close()
    return counts


# ── Phase 9: Credit card lifecycle ──────────────────────────────

def gen_credit_cards(through: date) -> list[dict]:
    """Chase payoff arc (Jan-Jun) + Amex monthly with Aug late fee.

    The pay-in-full balance queries in the original phase script depended
    on the live running balance; here we build the book in deterministic
    passes, so we approximate the lifecycle with fixed, sensible amounts
    that keep both cards roughly in the spec's narrative shape. From 2026
    onward both cards run a steady monthly pay-in-full so the balances
    stay realistic as daily-driver charges keep landing on them through
    ``through`` (rather than ballooning past the 2025 cliff).
    """
    txns: list[dict] = []

    # Chase interest Jan-May (declining), then paid off in June.
    chase_interest = [(1, D("42.00")), (2, D("35.00")), (3, D("28.00")),
                      (4, D("20.00")), (5, D("12.00"))]
    for m, amt in chase_interest:
        txns.append({
            "description": f"Chase Sapphire interest — "
                           f"{date(YEAR, m, 1).strftime('%B %Y')}",
            "date": date(YEAR, m, 15),
            "splits": [(CHASE, -amt), (EXP_CC_INT, amt)],
        })
    # Chase minimum payments Jan-May, larger payoff June.
    chase_payments = [(1, 20, D("500.00")), (2, 20, D("500.00")),
                      (3, 20, D("500.00")), (4, 20, D("500.00")),
                      (5, 20, D("500.00")), (6, 25, D("1200.00"))]
    for m, day, amt in chase_payments:
        label = "June payoff" if m == 6 else "payment"
        txns.append({
            "description": f"Chase Sapphire {label} — "
                           f"{date(YEAR, m, 1).strftime('%B %Y')}",
            "date": date(YEAR, m, day),
            "splits": [(CHECKING, -amt), (CHASE, amt)],
        })
    # Jul-Dec pay-in-full (daily-driver charges land on Chase).
    for m in range(7, 13):
        txns.append({
            "description": f"Chase Sapphire — "
                           f"{date(YEAR, m, 1).strftime('%b')} pay-in-full",
            "date": date(YEAR, m, 28),
            "splits": [(CHECKING, -D("650.00")), (CHASE, D("650.00"))],
        })

    # Business Amex monthly pay-in-full (covers AWS+WeWork ~$375/mo).
    for m in range(1, 13):
        if m == 8:
            continue
        txns.append({
            "description": f"Business Amex — "
                           f"{date(YEAR, m, 1).strftime('%b')} pay-in-full",
            "date": date(YEAR, m, 25),
            "splits": [(CHECKING, -D("375.00")), (AMEX, D("375.00"))],
        })
    # August late: partial payment + $29 late fee + ~$38 interest.
    txns.append({
        "description": "Business Amex — August partial payment (late)",
        "date": date(YEAR, 8, 25),
        "splits": [(CHECKING, -D("200.00")), (AMEX, D("200.00"))],
    })
    txns.append({
        "description": "Business Amex — late payment fee",
        "date": date(YEAR, 8, 28),
        "splits": [(AMEX, -D("29.00")), (EXP_BANK_CHARGES, D("29.00"))],
    })
    txns.append({
        "description": "Business Amex — August interest (missed cycle)",
        "date": date(YEAR, 8, 28),
        "splits": [(AMEX, -D("38.00")), (EXP_CC_INT, D("38.00"))],
    })

    # 2026+ steady-state pay-in-full for both cards so they don't balloon
    # as the daily-driver charges keep landing through ``through``. We
    # stop one month short of ``through`` so the most recent cycle's
    # charges remain as an outstanding (unpaid) balance — realistic.
    cutoff = (through.replace(day=1) - timedelta(days=1))
    for yr, m in _month_iter(date(YEAR + 1, 1, 1), cutoff):
        chase_when = _clamp_day(yr, m, 28)
        if date(YEAR, 1, 1) <= chase_when <= cutoff:
            txns.append({
                "description": f"Chase Sapphire — "
                               f"{chase_when.strftime('%b %Y')} pay-in-full",
                "date": chase_when,
                "splits": [(CHECKING, -D("700.00")), (CHASE, D("700.00"))],
            })
        amex_when = _clamp_day(yr, m, 25)
        if date(YEAR, 1, 1) <= amex_when <= cutoff:
            txns.append({
                "description": f"Business Amex — "
                               f"{amex_when.strftime('%b %Y')} pay-in-full",
                "date": amex_when,
                "splits": [(CHECKING, -D("375.00")), (AMEX, D("375.00"))],
            })

    return txns


# ── Phase 10: Budget ────────────────────────────────────────────

def run_budget(book: GnuCashBook) -> None:
    name = "2025 Annual Budget"
    book.create_budget(name=name, year=YEAR, num_periods=12,
                       period_type="monthly",
                       description="Alex & Robin 2025 household budget")
    monthly = [
        (EXP_GROCERIES, "400"), (EXP_DINING, "350"), (EXP_HOA, "425"),
        (EXP_FUEL, "220"), (EXP_STREAMING, "46"), (EXP_CLOTHING, "150"),
        (EXP_TRAVEL, "300"), (EXP_CLOUD, "150"), (EXP_GIFTS, "100"),
        (EXP_CHARITY, "50"), (EXP_MISC, "200"),
    ]
    for acct, amt in monthly:
        book.set_budget_amount(budget_name=name, account=acct, amount=amt,
                               period="all")
    # Parent rollups (placeholders).
    book.set_budget_amount(budget_name=name, account="Expenses:Utilities",
                           amount="450", period="all")
    book.set_budget_amount(budget_name=name, account="Expenses:Pet",
                           amount="70", period="all")

    # Seasonal overrides (period 0-indexed: Jun=5, Jul=6, Aug=7, Nov=10).
    for p in (5, 6, 7):
        book.set_budget_amount(budget_name=name, account=EXP_TRAVEL,
                               amount="600", period=p)
    book.set_budget_amount(budget_name=name, account=EXP_GIFTS,
                           amount="800", period=10)
    book.set_budget_amount(budget_name=name, account=EXP_CHARITY,
                           amount="500", period=10)


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

    # 2. Recategorized: $89 Office Supplies (Misc) -> Business:Software.
    r = book.create_transaction(
        description="Office Supplies (Amazon)",
        trans_date=date(YEAR, 4, 20),
        splits=[{"account": CHASE, "amount": "-89.00"},
                {"account": EXP_MISC, "amount": "89.00"}],
        check_duplicates=False,
    )
    book.replace_splits(
        guid=r["guid"],
        splits=[{"account": CHASE, "amount": "-89.00"},
                {"account": EXP_SOFTWARE, "amount": "89.00"}],
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
        amt = _spend(rng, 2, 15)
        target = merchant_category(vendor, EXP_MISC)
        txns.append({"description": vendor, "date": dt,
                     "splits": [(CHECKING, -amt), (target, amt)]})
    return txns


# ── Phase 11: Reconciliation ────────────────────────────────────

def run_reconciliation(book: GnuCashBook) -> None:
    """Reconcile only the FIRST few statement cycles of checking.

    A realistic book is reconciled through the last bank statement and
    has many hundreds of more-recent unreconciled splits. We reconcile
    checking through the first three months of 2025 and leave everything
    after that unreconciled, matching how an active book actually looks
    (the bookkeeper flagged a fully-reconciled book as unrealistic).
    """
    for label, through_date, stmt_date in [
        ("January", date(YEAR, 1, 31), date(YEAR, 1, 31)),
        ("February", date(YEAR, 2, 28), date(YEAR, 2, 28)),
        ("March", date(YEAR, 3, 31), date(YEAR, 3, 31)),
    ]:
        bal = book.get_balance(CHECKING, as_of_date=through_date)
        try:
            book.reconcile_account(
                account_name=CHECKING, statement_date=stmt_date,
                statement_balance=str(bal), reconcile_all=True,
                through_date=through_date,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  Reconciliation {label} skipped: {exc}")


# ── Scheduled-transaction state (stay ENABLED, realistic timing) ─

# Schedules we deliberately leave OVERDUE (last_occur pushed two periods
# back so the dashboard surfaces an overdue-scheduled warning) — by name.
#
# Only the Estimated Tax Payment stays overdue: it's a believable
# real-world lapse (a freelancer can genuinely miss a quarterly IRS
# deadline) and the bookkeeper keeps it as a demo of the overdue-warning
# surface. The Auto Loan Payment is NOT overdue — missing a car payment
# hits credit after 30 days, which would be unrealistic; its recurring
# instantiation is posted current (the monthly amortization in
# ``gen_recurring`` runs through ``through``) and its SX cursor advances
# to the latest occurrence below.
OVERDUE_SX = {"Estimated Tax Payment"}


def set_schedule_state(out_path: Path, through: date) -> dict:
    """Leave SX templates ENABLED with realistic ``last_occur`` timing.

    Most schedules get ``last_occur`` set to their most recent occurrence
    on/before ``through`` so their next occurrence is upcoming (the
    dashboard's "due soon" line, and a working
    ``create_transaction_from_scheduled`` target). A couple in
    ``OVERDUE_SX`` are pushed two periods back so they read as overdue —
    driving the overdue-schedule warning.

    We set ``last_occur`` directly via piecash because the public
    ``update_scheduled_transaction`` tool intentionally doesn't expose it
    (it's GnuCash desktop's "Since Last Run" cursor, not user-editable).
    """
    from dateutil.relativedelta import relativedelta

    period = {
        "weekly": timedelta(days=7),
        "biweekly": timedelta(days=14),
        "monthly": relativedelta(months=1),
        "quarterly": relativedelta(months=3),
        "yearly": relativedelta(years=1),
    }
    info = {"enabled": 0, "upcoming": 0, "overdue": 0}

    book = piecash.open_book(str(out_path), readonly=False)
    try:
        for sx in book.session.query(piecash.ScheduledTransaction).all():
            rec = sx.recurrence
            if rec is None:
                continue
            mult = rec.recurrence_mult
            ptype = rec.recurrence_period_type
            # Map piecash recurrence to a frequency label.
            if ptype == "week" and mult == 2:
                freq = "biweekly"
            elif ptype == "week":
                freq = "weekly"
            elif ptype == "month" and mult == 3:
                freq = "quarterly"
            elif ptype == "year":
                freq = "yearly"
            else:
                freq = "monthly"

            start = sx.start_date
            if hasattr(start, "date"):
                start = start.date()

            # Walk occurrences forward from start, collecting those on or
            # before ``through``.
            step = period[freq]
            occs = []
            cur = start
            while cur <= through and len(occs) < 5000:
                occs.append(cur)
                cur = cur + step

            if not occs:
                last_occ = None
            elif sx.name in OVERDUE_SX and len(occs) >= 2:
                # Push last_occur back so the next occurrence falls before
                # today → the dashboard reads it as overdue.
                last_occ = occs[-2]
                info["overdue"] += 1
            else:
                last_occ = occs[-1]
                info["upcoming"] += 1

            sx.enabled = 1
            sx.last_occur = last_occ
            info["enabled"] += 1
        book.save()
    finally:
        book.close()
    return info


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
            if (t.description or "").startswith("Robin's Paycheck"):
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
        base = [p for p in paychecks if p[1] == BASELINE_GROSS][:2]
        ot = [p for p in paychecks if p[1] != BASELINE_GROSS][:2]
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

    # The one-time client-visit trip must exist.
    print("\n-- (a3) One-time client-visit trip ($1,500-$2,000) --")
    with book.open() as b:
        trip_total = D("0")
        trip_descs = []
        for t in b.transactions:
            d = t.description or ""
            if "Berlin client visit" in d or "client visit" in d:
                for s in t.splits:
                    if s.account.fullname == EXP_TRAVEL:
                        trip_total += Decimal(str(s.value))
                        trip_descs.append(d)
        print(f"  trip travel total: ${trip_total:,.2f} across "
              f"{len(trip_descs)} bookings "
              f"(in $1,500-$2,000 band: "
              f"{D('1500') <= trip_total <= D('2000')})")
        for d in trip_descs:
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
            if not d.startswith("Sell "):
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

    print("\n-- Checking operating buffer (target ~$40K-$60K) --")
    chk = _parse_money(book.get_balance(CHECKING, as_of_date=as_of))
    sav = _parse_money(book.get_balance(SAVINGS, as_of_date=as_of))
    in_band = D("40000") <= chk <= D("60000")
    print(f"  Checking: ${chk:,.2f}  (in $40K-$60K band: {in_band})")
    print(f"  Savings:  ${sav:,.2f}")

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


def relativedelta_safe(months: int = 0):
    from dateutil.relativedelta import relativedelta
    return relativedelta(months=months)


# ── Driver ──────────────────────────────────────────────────────

def build(out_path: Path, through: date) -> None:
    print(f"Building Alex Chen-Morales book at: {out_path}")
    print(f"Activity runs 2025-01-01 → {through.isoformat()} (THROUGH)")

    print("\nPhase 1: book file + commodities + monthly prices")
    create_book_file(out_path)
    n_prices = add_prices(out_path)
    # Real prices on every trade / invoice settle date.
    event_dates = (investment_event_price_dates(through)
                   + business_event_price_dates(through))
    n_event = add_event_prices(out_path, event_dates)
    print(f"  commodities + {n_prices} monthly prices + "
          f"{n_event} event prices")

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
    n = write_bulk(out_path, gen_recurring(through))
    print(f"  {n} recurring transactions")

    print("\nPhase 6: daily/weekly + seasonal")
    n = write_bulk(out_path, gen_daily_weekly(through))
    print(f"  {n} daily/weekly/seasonal transactions")

    print("\nPhase 6b: personal-life spending "
          "(medical/gifts/charity/travel/entertainment/personal care)")
    n = write_bulk(out_path, gen_personal_life(through))
    print(f"  {n} personal-life transactions")

    print("\nPhase 7a: direct 1099 contractor income")
    n = write_bulk(out_path, gen_contractor_income(through))
    print(f"  {n} contractor deposits")

    print("\nPhase 7c: cash management (surplus sweeps out of checking)")
    n = write_bulk(out_path, gen_savings_sweep(through))
    sweep = run_vtsax_sweep(out_path, through)
    print(f"  {n} savings sweeps + {sweep['txns']} VTSAX sweeps "
          f"({sweep['lots']} lots)")

    print("\nPhase 7b: business module")
    business = run_business(book, through)
    print(f"  {business}")

    print("\nPhase 8: investments")
    inv_counts = run_investments(out_path, through)
    print(f"  {inv_counts}")

    print("\nPhase 9: credit card lifecycle")
    n = write_bulk(out_path, gen_credit_cards(through))
    print(f"  {n} credit-card transactions")

    print("\nPhase 10: budget")
    run_budget(book)
    print("  budget created")

    print("\nPhase 12: edge cases")
    edge = run_edge_cases(book)
    print(f"  {edge}")

    print("\nPhase 13: volume stress")
    n = write_bulk(out_path, gen_volume(through))
    print(f"  {n} volume transactions")

    print("\nPhase 11: reconciliation")
    run_reconciliation(book)
    print("  reconciliation done (only first 3 months of 2025)")

    print("\nScheduled-transaction state (stay ENABLED, realistic timing)")
    sx_state = set_schedule_state(out_path, through)
    print(f"  {sx_state}")

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
    args = parser.parse_args()
    if args.through:
        THROUGH = date.fromisoformat(args.through)
        PRICE_MONTHS = _price_months(THROUGH)
    out_path = Path(args.out).resolve()
    if out_path == PROTECTED.resolve():
        raise SystemExit(f"REFUSING to write to protected book: {PROTECTED}")
    build(out_path, THROUGH)


if __name__ == "__main__":
    main()
