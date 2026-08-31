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
from datetime import date, timedelta
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

# End of the recurring/spending activity timeline. DEFAULTS to today so the
# book always has recent activity (no data cliff → realistic burn-rate,
# runway, and monthly-net). Pin with ``--through YYYY-MM-DD`` for a
# deterministic run. Activity runs continuously from 2025-01-01 through
# THROUGH. Set by main() / build(); the module-level default is today.
THROUGH = date.today()

# End of the committed market-data cache. Prices forward-fill past this
# (see market_data._forward_fill), so report/transaction dates beyond it
# still resolve to the last real quote. The monthly price-snapshot series
# runs through max(END, THROUGH) so every reporting date has a price row.
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
#
# Lin Wei runs a native zh_CN book: the entire chart of accounts is in
# Chinese, the way a real Shenzhen household's GnuCash book looks. The
# ASCII identifiers below are kept for code readability; only their VALUES
# (the account paths) are localized. The five top-level roots are the exact
# zh structural words the server's _STRUCTURAL_TYPE_NAMES catalog carries
# (资产/负债/收入/支出/所有者权益), so _infer_book_locale votes this book to
# "zh" and the localized-created-account paths (e.g. the FX gain/loss
# account) get Chinese leaf names automatically.

CHECKING = "资产:流动资产:支票账户"
SAVINGS = "资产:流动资产:储蓄账户"
CASH = "资产:流动资产:现金"
WECHAT = "资产:流动资产:微信支付"
ALIPAY = "资产:流动资产:支付宝"
AR_CNY = "资产:应收款项:应收账款"
AR_USD = "资产:应收款项:应收账款（美元）"
AR_EUR = "资产:应收款项:应收账款（欧元）"
HOUSING_FUND = "资产:投资:住房公积金"
APARTMENT = "资产:固定资产:公寓"
VEHICLE = "资产:固定资产:车辆"

MOUTAI = "资产:投资:证券账户:贵州茅台"
CATL = "资产:投资:证券账户:宁德时代"
CSI300 = "资产:投资:证券账户:沪深300ETF"
CHINEXT = "资产:投资:证券账户:创业板ETF"

ICBC_CARD = "负债:信用卡:工商银行信用卡"
CMB_CARD = "负债:信用卡:招商银行信用卡"
HSBC_CARD = "负债:信用卡:汇丰港币信用卡"
MORTGAGE = "负债:贷款:房屋贷款"
AUTO_LOAN = "负债:贷款:汽车贷款"
AP = "负债:应付账款"

OPENING = "所有者权益:期初余额"

SALARY = "收入:工资"
CONTRACTOR = "收入:承包收入"
LLC_REVENUE = "收入:个体经营收入"
DIVIDENDS = "收入:投资收益:股息"
CAPITAL_GAINS = "收入:投资收益:资本利得"
HOUSING_FUND_INCOME = "收入:住房公积金收入"
REIMBURSEMENTS = "收入:报销收入"

EXP_PROP_MGMT = "支出:住房:物业管理费"
EXP_MORTGAGE_INT = "支出:利息:房贷利息"
EXP_AUTO_INT = "支出:利息:车贷利息"
EXP_CC_INT = "支出:利息:信用卡利息"
EXP_CHARGING = "支出:汽车:充电费"
EXP_AUTO_INS = "支出:汽车:汽车保险"
EXP_PARKING = "支出:汽车:停车费"
EXP_GROCERIES = "支出:食品杂货"
EXP_DINING = "支出:餐饮"
EXP_UTILITIES = "支出:公用事业"
EXP_ELECTRIC = "支出:公用事业:电费"
EXP_WATER = "支出:公用事业:水费"
EXP_GAS = "支出:公用事业:燃气费"
EXP_INTERNET = "支出:公用事业:网络费"
EXP_PHONE = "支出:公用事业:电话费"
EXP_INCOME_TAX = "支出:税费:个人所得税"
EXP_SOCIAL = "支出:税费:社会保险"
EXP_BUSINESS_TAX = "支出:税费:营业税"
EXP_STREAMING = "支出:视频会员"
EXP_SUBSCRIPTIONS = "支出:订阅"
EXP_EDUCATION = "支出:教育"
EXP_CLOTHING = "支出:服装"
EXP_PET = "支出:宠物"
EXP_PET_FOOD = "支出:宠物:宠物食品"
EXP_PET_VET = "支出:宠物:宠物医疗"
EXP_TRAVEL = "支出:旅行"
EXP_GIFTS = "支出:礼金"
EXP_CHARITY = "支出:慈善捐款"
EXP_CLOUD = "支出:经营支出:云服务器"
EXP_SOFTWARE = "支出:经营支出:软件"
EXP_COWORKING = "支出:经营支出:联合办公"
EXP_MISC = "支出:杂项"
EXP_MEDICAL = "支出:医疗"
EXP_ENTERTAINMENT = "支出:娱乐"
EXP_PERSONAL_CARE = "支出:个人护理"


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

# JetBrains: the USD bill is RE-DATED at build time to the 1st of THROUGH's
# month (a date that carries a monthly USD/CNY snapshot), so it no longer
# needs a fixed FX date here. The recent outstanding cross-currency invoices
# (Pacific USD, Munich EUR) and the re-dated JetBrains bill all post on a
# 1st-of-month, which price_months() already covers — so the 90-day FX
# freshness guard is satisfied without enumerating those dynamic dates.

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

def price_months() -> list[date]:
    """1st-of-month price-snapshot dates from 2025-01 through max(END, THROUGH).

    Extends past the market-data cache end so every reporting date — including
    a present-day THROUGH beyond the cache — has a monthly snapshot. Prices on
    dates past the cache forward-fill to the last real quote.
    """
    horizon = max(END, THROUGH)
    months: list[date] = []
    y, m = 2025, 1
    while date(y, m, 1) <= horizon:
        months.append(date(y, m, 1))
        m += 1
        if m > 12:
            y += 1
            m = 1
    return months


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
    3. A closing point per commodity at its LAST AVAILABLE real quote
       (capped at THROUGH), so a report at the present edge of the book
       reflects the most recent real close rather than forward-filling
       the 1st-of-month value to the end of the horizon.
    """
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    count = 0
    try:
        cny = book.default_currency
        comm_by_mnemonic = {c.mnemonic: c for c in book.commodities}

        # Collect (symbol -> set of dates) needing a price. Start with the
        # monthly snapshots for every symbol, then add the exact
        # transaction dates.
        wanted: dict[str, set[date]] = {
            sym: set(price_months())
            for sym in FOREIGN_CURRENCIES + SECURITY_MNEMONICS
        }
        for sym, when in security_price_dates():
            wanted[sym].add(when)
        for sym, when in fx_price_dates():
            wanted[sym].add(when)
        # Closing point: last available real quote per commodity, never past
        # THROUGH. real_price() returns the actual most-recent close there.
        for sym in FOREIGN_CURRENCIES:
            wanted[sym].add(min(THROUGH, MD.latest_fx_date(sym, "CNY")))
        for sym in SECURITY_MNEMONICS:
            wanted[sym].add(min(THROUGH, MD.latest_security_date(sym)))

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
#
# A native zh_CN chart. The five top-level roots are the exact zh
# structural words in the server's _STRUCTURAL_TYPE_NAMES catalog
# (资产/负债/收入/支出/所有者权益) so _infer_book_locale resolves this book
# to "zh". Parent paths are the localized paths, matching the constants
# above. Types are locale-invariant (GnuCash never localizes the type enum),
# so every type-based server code path keeps working unchanged.
ACCOUNTS = [
    # 资产 (Assets)
    ("资产", "ASSET", None, "CNY", "CURRENCY", True),
    ("流动资产", "ASSET", "资产", "CNY", "CURRENCY", True),
    ("支票账户", "BANK", "资产:流动资产", "CNY", "CURRENCY", False),
    ("储蓄账户", "BANK", "资产:流动资产", "CNY", "CURRENCY", False),
    ("现金", "CASH", "资产:流动资产", "CNY", "CURRENCY", False),
    ("微信支付", "BANK", "资产:流动资产", "CNY", "CURRENCY", False),
    ("支付宝", "BANK", "资产:流动资产", "CNY", "CURRENCY", False),
    ("应收款项", "ASSET", "资产", "CNY", "CURRENCY", True),
    ("应收账款", "RECEIVABLE", "资产:应收款项", "CNY", "CURRENCY", False),
    ("应收账款（美元）", "RECEIVABLE", "资产:应收款项", "USD", "CURRENCY", False),
    ("应收账款（欧元）", "RECEIVABLE", "资产:应收款项", "EUR", "CURRENCY", False),
    ("投资", "ASSET", "资产", "CNY", "CURRENCY", True),
    ("证券账户", "ASSET", "资产:投资", "CNY", "CURRENCY", True),
    ("贵州茅台", "STOCK", "资产:投资:证券账户", "600519", "SSE", False),
    ("宁德时代", "STOCK", "资产:投资:证券账户", "300750", "SZSE", False),
    ("沪深300ETF", "MUTUAL", "资产:投资:证券账户", "510300", "SSE", False),
    ("创业板ETF", "MUTUAL", "资产:投资:证券账户", "159915", "SZSE", False),
    ("住房公积金", "BANK", "资产:投资", "CNY", "CURRENCY", False),
    ("固定资产", "ASSET", "资产", "CNY", "CURRENCY", True),
    ("公寓", "ASSET", "资产:固定资产", "CNY", "CURRENCY", False),
    ("车辆", "ASSET", "资产:固定资产", "CNY", "CURRENCY", False),
    # 负债 (Liabilities)
    ("负债", "LIABILITY", None, "CNY", "CURRENCY", True),
    ("信用卡", "LIABILITY", "负债", "CNY", "CURRENCY", True),
    ("工商银行信用卡", "CREDIT", "负债:信用卡", "CNY", "CURRENCY", False),
    ("招商银行信用卡", "CREDIT", "负债:信用卡", "CNY", "CURRENCY", False),
    ("汇丰港币信用卡", "CREDIT", "负债:信用卡", "HKD", "CURRENCY", False),
    ("贷款", "LIABILITY", "负债", "CNY", "CURRENCY", True),
    ("房屋贷款", "LIABILITY", "负债:贷款", "CNY", "CURRENCY", False),
    ("汽车贷款", "LIABILITY", "负债:贷款", "CNY", "CURRENCY", False),
    ("应付账款", "PAYABLE", "负债", "CNY", "CURRENCY", False),
    # 收入 (Income)
    ("收入", "INCOME", None, "CNY", "CURRENCY", True),
    ("工资", "INCOME", "收入", "CNY", "CURRENCY", False),
    ("承包收入", "INCOME", "收入", "CNY", "CURRENCY", False),
    ("个体经营收入", "INCOME", "收入", "CNY", "CURRENCY", False),
    ("投资收益", "INCOME", "收入", "CNY", "CURRENCY", True),
    ("股息", "INCOME", "收入:投资收益", "CNY", "CURRENCY", False),
    ("资本利得", "INCOME", "收入:投资收益", "CNY", "CURRENCY", False),
    ("住房公积金收入", "INCOME", "收入", "CNY", "CURRENCY", False),
    ("报销收入", "INCOME", "收入", "CNY", "CURRENCY", False),
    # The realized FX gain/loss account is intentionally NOT pre-created:
    # pay_invoice auto-creates it on the first cross-currency settlement,
    # under the top-level INCOME account resolved by TYPE, with a localized
    # leaf name (已实现获利(亏损) on a zh book). That exercises the server's
    # i18n auto-creation + KVP self-healing path — the whole point of a
    # natively-localized persona.
    # 支出 (Expenses)
    ("支出", "EXPENSE", None, "CNY", "CURRENCY", True),
    ("住房", "EXPENSE", "支出", "CNY", "CURRENCY", True),
    ("房贷利息", "EXPENSE", "支出:住房", "CNY", "CURRENCY", False),
    ("物业管理费", "EXPENSE", "支出:住房", "CNY", "CURRENCY", False),
    ("房屋维修", "EXPENSE", "支出:住房", "CNY", "CURRENCY", False),
    ("汽车", "EXPENSE", "支出", "CNY", "CURRENCY", True),
    ("充电费", "EXPENSE", "支出:汽车", "CNY", "CURRENCY", False),
    ("汽车保险", "EXPENSE", "支出:汽车", "CNY", "CURRENCY", False),
    ("汽车保养", "EXPENSE", "支出:汽车", "CNY", "CURRENCY", False),
    ("停车费", "EXPENSE", "支出:汽车", "CNY", "CURRENCY", False),
    ("食品杂货", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("餐饮", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("公用事业", "EXPENSE", "支出", "CNY", "CURRENCY", True),
    ("电费", "EXPENSE", "支出:公用事业", "CNY", "CURRENCY", False),
    ("水费", "EXPENSE", "支出:公用事业", "CNY", "CURRENCY", False),
    ("燃气费", "EXPENSE", "支出:公用事业", "CNY", "CURRENCY", False),
    ("网络费", "EXPENSE", "支出:公用事业", "CNY", "CURRENCY", False),
    ("电话费", "EXPENSE", "支出:公用事业", "CNY", "CURRENCY", False),
    ("保险", "EXPENSE", "支出", "CNY", "CURRENCY", True),
    ("医疗保险", "EXPENSE", "支出:保险", "CNY", "CURRENCY", False),
    ("人寿保险", "EXPENSE", "支出:保险", "CNY", "CURRENCY", False),
    ("税费", "EXPENSE", "支出", "CNY", "CURRENCY", True),
    ("个人所得税", "EXPENSE", "支出:税费", "CNY", "CURRENCY", False),
    ("社会保险", "EXPENSE", "支出:税费", "CNY", "CURRENCY", False),
    ("营业税", "EXPENSE", "支出:税费", "CNY", "CURRENCY", False),
    ("订阅", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("视频会员", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("服装", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("宠物", "EXPENSE", "支出", "CNY", "CURRENCY", True),
    ("宠物食品", "EXPENSE", "支出:宠物", "CNY", "CURRENCY", False),
    ("宠物医疗", "EXPENSE", "支出:宠物", "CNY", "CURRENCY", False),
    ("旅行", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("教育", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("礼金", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("慈善捐款", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("娱乐", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("个人护理", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("经营支出", "EXPENSE", "支出", "CNY", "CURRENCY", True),
    ("云服务器", "EXPENSE", "支出:经营支出", "CNY", "CURRENCY", False),
    ("软件", "EXPENSE", "支出:经营支出", "CNY", "CURRENCY", False),
    ("联合办公", "EXPENSE", "支出:经营支出", "CNY", "CURRENCY", False),
    ("利息", "EXPENSE", "支出", "CNY", "CURRENCY", True),
    ("信用卡利息", "EXPENSE", "支出:利息", "CNY", "CURRENCY", False),
    ("房贷利息", "EXPENSE", "支出:利息", "CNY", "CURRENCY", False),
    ("车贷利息", "EXPENSE", "支出:利息", "CNY", "CURRENCY", False),
    ("医疗", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("杂项", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    # 所有者权益 (Equity)
    ("所有者权益", "EQUITY", None, "CNY", "CURRENCY", True),
    ("期初余额", "EQUITY", "所有者权益", "CNY", "CURRENCY", False),
]


def create_accounts(out_path: Path) -> int:
    """Create the full chart of accounts directly via piecash."""
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
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
    # Loans opt out of the reconciliation surface — no statement
    # exists to reconcile against (bookkeeper review §1).
    book.set_account_slot(MORTGAGE, "no_reconcile", "true")
    book.set_account_slot(AUTO_LOAN, "no_reconcile", "true")


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
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
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
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
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


def iter_months(start: date = date(YEAR, 1, 1), through: date | None = None):
    """Yield (year, month) for each month from ``start`` through ``through``.

    ``through`` defaults to the module ``THROUGH`` (today unless pinned). The
    final partial month is included so spending continues right up to the
    present — that's what kills the data cliff.
    """
    if through is None:
        through = THROUGH
    y, m = start.year, start.month
    while (y, m) <= (through.year, through.month):
        yield y, m
        m += 1
        if m > 12:
            y += 1
            m = 1


def _clamp_day(year: int, month: int, day: int) -> date:
    """``date(year, month, day)`` clamped to the last valid day of the month."""
    import calendar
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _on_or_before_through(d: date) -> bool:
    """True if ``d`` is on or before THROUGH (so we don't post the future)."""
    return d <= THROUGH


def _yuan(rng: random.Random, low: int, high: int) -> Decimal:
    """A whole-yuan amount uniformly in ``[low, high]``.

    Reserved for genuinely-round amounts (fixed bills, dues priced to the
    yuan). Consumer/discretionary spend uses ``_spend`` below, which carries
    realistic jiao/fen — uniform integers are the single strongest
    statistical fingerprint of generated data, so real receipts must show
    cents.
    """
    return D(str(rng.randint(low, high)))


def _spend(rng: random.Random, low: int, high: int) -> Decimal:
    """A consumer amount in ``[low, high]`` yuan carrying realistic jiao/fen.

    Real-world retail/F&B receipts rarely land on a whole yuan: prices like
    ¥18.80, ¥36.50, ¥27.30 are the norm. We draw a whole-yuan base in range
    then add a fen component. The fen value is biased toward common
    price-point endings (.00/.50/.80/.90/.99 and round-jiao .x0) so the
    distribution looks like menu/shelf pricing rather than uniform noise,
    while still spanning arbitrary .xx values. Both splits of a transaction
    carry the same magnitude, so cents balance automatically.
    """
    base = rng.randint(low, high)
    r = rng.random()
    if r < 0.16:
        fen = 0          # genuinely round (some receipts are)
    elif r < 0.30:
        fen = 50         # .50
    elif r < 0.42:
        fen = 80         # .80 (very common CN price ending)
    elif r < 0.52:
        fen = 90         # .90
    elif r < 0.60:
        fen = 99         # .99
    elif r < 0.80:
        fen = rng.randint(1, 9) * 10   # round jiao: .10 .. .90
    else:
        fen = rng.randint(1, 99)       # arbitrary fen
    cents = D(base) + (D(fen) / D(100))
    return cents.quantize(D("0.01"))


# ── Merchant → canonical expense category ───────────────────────
#
# Real people miscategorize SYSTEMATICALLY, not stochastically: a given
# merchant lands in the SAME account every time (often via an autopay rule
# or a habit), right or wrong. This dict pins every recurring consumer
# merchant to ONE canonical category. The daily/weekly/volume generators
# look the merchant up here instead of scattering it across buckets.
#
# The one deliberate, CONSISTENT miscategorization (the believable standing
# mistake the household actually makes):
#   • 自动贩卖机 (vending machine) → Miscellaneous, ALWAYS. A careful
#     bookkeeper would call a snack-machine charge Dining, but the user set a
#     stale auto-rule years ago that dumps every 自动贩卖机 charge into Misc
#     and never fixed it — so it's systematically (not randomly) wrong, the
#     same way every time. That's the realistic texture: a consistent error,
#     not stochastic scatter.
# Everything else maps to the account a careful bookkeeper would expect.
MERCHANT_CATEGORY: dict[str, str] = {
    # Coffee / tea / delivery / dine-in → Dining (correct)
    "瑞幸咖啡": EXP_DINING,
    "瑞幸": EXP_DINING,
    "美团外卖": EXP_DINING,
    "饿了么外卖": EXP_DINING,
    "饿了么": EXP_DINING,
    # Groceries / convenience / warehouse → Groceries (correct)
    "盒马鲜生": EXP_GROCERIES,
    "盒马": EXP_GROCERIES,
    "便利蜂": EXP_GROCERIES,
    "7-11便利店": EXP_GROCERIES,
    "全家便利店": EXP_GROCERIES,
    "山姆会员店": EXP_GROCERIES,
    # Transport — there is no Transport account, so the correct home is Auto.
    # Ride-hail and bike-share both book to Auto:Parking consistently.
    "滴滴出行": EXP_PARKING,
    "共享单车": EXP_PARKING,
    "EV充电": EXP_CHARGING,
    # ── Deliberate sticky miscategorization (always wrong, always same) ──
    "自动贩卖机": EXP_MISC,         # vending: stale auto-rule → Misc, every time
}


def merchant_category(name: str, default: str) -> str:
    """Canonical category for ``name`` (consistent every time), else default.

    Matches on a leading-substring key so descriptions with a suffix
    (e.g. ``"瑞幸咖啡 (外送)"``) still resolve. Encodes the sticky-error
    behavior in ``MERCHANT_CATEGORY``.
    """
    for key, cat in MERCHANT_CATEGORY.items():
        if name.startswith(key):
            return cat
    return default


# ── Phase 4: Scheduled-transaction templates ────────────────────

def create_scheduled_templates(book: GnuCashBook) -> int:
    """Create the SX templates so scheduled-transaction tools have data.

    These are left ENABLED (see set_schedule_state) with a realistic
    ``last_occur`` so the scheduled-transaction demo surface stays populated
    (the prior hand-built book had 13 active). The actual recurring activity
    is generated directly in Phase 5 for speed and amortization-split
    fidelity; these templates are the forward-looking schedule.
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
    sx("陈宇工资", "深圳市人民医院 工资", [
        {"account": SALARY, "amount": "-15000"},
        {"account": CHECKING, "amount": "11800"},
        {"account": EXP_INCOME_TAX, "amount": "450"},
        {"account": EXP_SOCIAL, "amount": "1650"},
        {"account": HOUSING_FUND, "amount": "1100"},
    ], "monthly")

    sx("房贷还款", "房贷还款", [
        {"account": CHECKING, "amount": "-14800"},
        {"account": EXP_MORTGAGE_INT, "amount": "8983"},
        {"account": MORTGAGE, "amount": "5817"},
    ], "monthly")

    sx("车贷还款", "车贷还款", [
        {"account": CHECKING, "amount": "-2400"},
        {"account": EXP_AUTO_INT, "amount": "490"},
        {"account": AUTO_LOAN, "amount": "1910"},
    ], "monthly")

    simple_monthly = [
        ("物业管理费", "物业管理费", CHECKING, EXP_PROP_MGMT, "850"),
        ("电费", "电费", WECHAT, EXP_ELECTRIC, "200"),
        ("水费", "水费", WECHAT, EXP_WATER, "80"),
        ("天然气", "天然气", WECHAT, EXP_GAS, "60"),
        ("宽带费", "中国电信宽带", WECHAT, EXP_INTERNET, "199"),
        ("话费", "中国移动话费", WECHAT, EXP_PHONE, "128"),
        ("视频会员", "爱奇艺 + Bilibili", WECHAT, EXP_STREAMING, "45"),
        ("阿里云", "阿里云", CMB_CARD, EXP_CLOUD, "350"),
        ("联合办公", "优客工场", CMB_CARD, EXP_COWORKING, "1500"),
        ("宠物口粮", "字节口粮", ALIPAY, EXP_PET_FOOD, "280"),
        ("车险", "车险", CHECKING, EXP_AUTO_INS, "450"),
        ("停车月卡", "停车月卡", WECHAT, EXP_PARKING, "800"),
    ]
    for name, desc, src, dst, amt in simple_monthly:
        sx(name, desc, [
            {"account": src, "amount": f"-{amt}"},
            {"account": dst, "amount": amt},
        ], "monthly")

    # Quarterly
    sx("季度预缴税", "个体工商户季度预缴税", [
        {"account": CHECKING, "amount": "-8000"},
        {"account": EXP_BUSINESS_TAX, "amount": "8000"},
    ], "quarterly")
    sx("宠物体检", "字节季度体检", [
        {"account": ALIPAY, "amount": "-500"},
        {"account": EXP_PET_VET, "amount": "500"},
    ], "quarterly")

    # Yearly
    sx("春节红包", "春节红包", [
        {"account": CHECKING, "amount": "-6000"},
        {"account": EXP_GIFTS, "amount": "6000"},
    ], "yearly", start_date="2025-02-01")
    sx("车辆年检", "年检", [
        {"account": CHECKING, "amount": "-300"},
        {"account": EXP_AUTO_INS, "amount": "300"},
    ], "yearly", start_date="2025-03-01")

    return count


# ── Phase 5: Recurring instantiations (direct, with amortization) ─

def _amort(base_int: Decimal, base_pri: Decimal, payment: Decimal,
           drift: Decimal, elapsed: int) -> tuple[Decimal, Decimal]:
    """Interest/principal split for month ``elapsed``, level total ``payment``.

    Interest declines by ``drift`` per month (principal grows to compensate),
    clamped so interest never goes below ~10% of the payment. Keeps the
    payment constant while the interest/principal mix shifts realistically as
    the loan amortizes across multiple years.
    """
    m_int = base_int - drift * elapsed
    floor = (payment * D("0.10")).quantize(D("1"))
    if m_int < floor:
        m_int = floor
    m_pri = payment - m_int
    return m_int, m_pri


# ── Payroll withholding (rate-based, cumulative IIT) ─────────────

# Employee-side statutory rates as a fraction of gross. These track gross
# month-to-month, so overtime/bonus months withhold more than base months —
# unlike the old frozen 1650/1100 constants. Calibrated so a base ¥15,000
# gross lands near the prior figures (social 1650, housing 1100).
SOCIAL_INS_RATE = D("0.110")     # 五险 employee portion ≈ 11% of gross
HOUSING_FUND_RATE = D("0.0733")  # 公积金 employee portion ≈ 7.33% of gross
IIT_MONTHLY_DEDUCTION = D("5000")  # 起征点 ¥60,000/yr = ¥5,000/mo standard

# China's cumulative-withholding (累计预扣法) annual brackets on cumulative
# taxable income: (upper_bound, rate, quick_deduction). Approximate — the
# point is that the income-tax line RISES through the year as cumulative
# taxable income crosses brackets, and responds to gross. Not exact PRC law.
IIT_BRACKETS = [
    (D("36000"), D("0.03"), D("0")),
    (D("144000"), D("0.10"), D("2520")),
    (D("300000"), D("0.20"), D("16920")),
    (D("420000"), D("0.25"), D("31920")),
    (D("660000"), D("0.30"), D("52920")),
    (D("960000"), D("0.35"), D("85920")),
    (D("99999999"), D("0.45"), D("181920")),
]


def _iit_cumulative(cum_taxable: Decimal) -> Decimal:
    """Total IIT owed YTD on ``cum_taxable`` (cumulative taxable income)."""
    if cum_taxable <= 0:
        return D("0")
    for upper, rate, quick in IIT_BRACKETS:
        if cum_taxable <= upper:
            return (cum_taxable * rate - quick).quantize(D("0.01"))
    return D("0")


def gen_recurring() -> list[dict]:
    txns: list[dict] = []
    rng = random.Random(SEED + 5)

    months = list(iter_months())
    start_ym = (YEAR, 1)

    # Cumulative-withholding state, reset each calendar year. Tracks YTD
    # taxable income and YTD tax already withheld so each month's IIT is the
    # incremental amount (累计预扣法) — it rises as the year progresses.
    cum_taxable_by_year: dict[int, Decimal] = {}
    cum_tax_by_year: dict[int, Decimal] = {}

    for elapsed, (yy, m) in enumerate(months):
        # Salary on the 15th, with overtime every 3rd month.
        d15 = _clamp_day(yy, m, 15)
        if _on_or_before_through(d15):
            overtime = D("0")
            if m % 3 == 0:
                overtime = D(str(rng.randint(500, 1500)))
            gross = D("15000") + overtime

            # Statutory deductions track gross (so overtime months differ).
            social = (gross * SOCIAL_INS_RATE).quantize(D("1"))
            housing = (gross * HOUSING_FUND_RATE).quantize(D("1"))

            # Cumulative-withholding IIT: this month's tax is the YTD tax owed
            # on cumulative taxable income minus tax already withheld YTD.
            month_taxable = gross - social - housing - IIT_MONTHLY_DEDUCTION
            cum_taxable = cum_taxable_by_year.get(yy, D("0")) + month_taxable
            cum_taxable_by_year[yy] = cum_taxable
            tax_owed_ytd = _iit_cumulative(cum_taxable)
            income_tax = (tax_owed_ytd - cum_tax_by_year.get(yy, D("0")))
            if income_tax < 0:
                income_tax = D("0")
            income_tax = income_tax.quantize(D("0.01"))
            cum_tax_by_year[yy] = cum_tax_by_year.get(yy, D("0")) + income_tax

            # Net to checking = gross − all employee withholdings.
            net = gross - income_tax - social - housing
            txns.append({
                "description": "深圳市人民医院 工资" + (
                    f" (含加班 ¥{overtime})" if overtime else ""),
                "date": d15,
                "splits": [
                    (SALARY, -gross),
                    (CHECKING, net),
                    (EXP_INCOME_TAX, income_tax),
                    (EXP_SOCIAL, social),
                    (HOUSING_FUND, housing),
                ],
            })
            # Housing fund employer match income (forced savings) — matches
            # the employee contribution, so it tracks gross too.
            txns.append({
                "description": "住房公积金 单位缴存",
                "date": d15,
                "splits": [
                    (HOUSING_FUND, housing),
                    (HOUSING_FUND_INCOME, -housing),
                ],
            })

        # Mortgage + auto loan on the 5th. Level payment, interest/principal
        # mix shifts each month as the loan amortizes (across multiple years).
        d5 = _clamp_day(yy, m, 5)
        if _on_or_before_through(d5):
            m_int, m_pri = _amort(
                D("8983"), D("5817"), D("14800"), D("19"), elapsed)
            txns.append({
                "description": "房贷还款",
                "date": d5,
                "splits": [
                    (CHECKING, -(m_int + m_pri)),
                    (EXP_MORTGAGE_INT, m_int),
                    (MORTGAGE, m_pri),
                ],
            })
            a_int, a_pri = _amort(
                D("490"), D("1910"), D("2400"), D("8"), elapsed)
            txns.append({
                "description": "车贷还款",
                "date": d5,
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
        for yy, m in months:
            d = _clamp_day(yy, m, day)
            if not _on_or_before_through(d):
                continue
            txns.append({
                "description": desc,
                "date": d,
                "splits": [(src, -amt), (dst, amt)],
            })

    # Quarterly estimated business tax (Mar/Jun/Sep/Dec, 15th).
    # Quarterly pet vet (Feb/May/Aug/Nov, 10th).
    for yy, m in months:
        if m in (3, 6, 9, 12):
            d = _clamp_day(yy, m, 15)
            if _on_or_before_through(d):
                txns.append({
                    "description": "个体工商户季度预缴税",
                    "date": d,
                    "splits": [(CHECKING, D("-8000")),
                               (EXP_BUSINESS_TAX, D("8000"))],
                })
        if m in (2, 5, 8, 11):
            d = _clamp_day(yy, m, 10)
            if _on_or_before_through(d):
                txns.append({
                    "description": "字节季度体检",
                    "date": d,
                    "splits": [(ALIPAY, D("-500")), (EXP_PET_VET, D("500"))],
                })

    # Monthly mobile-wallet top-ups from checking. WeChat and Alipay
    # carry most of the daily spend (coffee, delivery, groceries,
    # utilities, charging, parking); without recurring funding their
    # small opening floats would go deeply negative over the year.
    for yy, m in months:
        d = _clamp_day(yy, m, 2)
        if not _on_or_before_through(d):
            continue
        txns.append({
            "description": "充值微信钱包",
            "date": d,
            "splits": [(CHECKING, D("-4500")), (WECHAT, D("4500"))],
        })
        txns.append({
            "description": "充值支付宝",
            "date": d,
            "splits": [(CHECKING, D("-4000")), (ALIPAY, D("4000"))],
        })

    # Yearly — Spring Festival red envelopes (Feb 1) + vehicle inspection
    # (Mar 1), each year in range.
    years = sorted({yy for yy, _ in months})
    for yy in years:
        d = date(yy, 2, 1)
        if date(YEAR, 1, 1) <= d <= THROUGH:
            txns.append({
                "description": "春节红包",
                "date": d,
                "splits": [(CHECKING, D("-6000")), (EXP_GIFTS, D("6000"))],
            })
        d = date(yy, 3, 1)
        if date(YEAR, 1, 1) <= d <= THROUGH:
            txns.append({
                "description": "车辆年检",
                "date": d,
                "splits": [(CHECKING, D("-300")), (EXP_AUTO_INS, D("300"))],
            })
    return txns


# ── Phase 6: Daily/weekly patterns + seasonal one-offs ──────────

def gen_daily_weekly() -> list[dict]:
    txns: list[dict] = []
    rng = random.Random(SEED + 6)

    # Weekday Luckin Coffee + daily convenience store + 3x/week Meituan.
    # Runs continuously from 2025-01-01 through THROUGH (today unless pinned)
    # so the most recent weeks always carry fresh spend — no data cliff.
    d = date(YEAR, 1, 1)
    end = THROUGH
    day_idx = 0
    while d <= end:
        wd = d.weekday()
        if wd < 5:  # weekday coffee
            amt = _spend(rng, 15, 22)
            vend = "瑞幸咖啡"
            txns.append({
                "description": vend,
                "date": d,
                "splits": [(WECHAT, -amt),
                           (merchant_category(vend, EXP_DINING), amt)],
            })
        # convenience store most days
        if day_idx % 1 == 0 and rng.random() < 0.7:
            amt = _spend(rng, 18, 35)
            vend = rng.choice(["便利蜂", "7-11便利店", "全家便利店"])
            txns.append({
                "description": vend,
                "date": d,
                "splits": [(WECHAT, -amt),
                           (merchant_category(vend, EXP_GROCERIES), amt)],
            })
        if wd in (1, 3, 5):  # Meituan delivery 3x/week
            amt = _spend(rng, 28, 48)
            vend = "美团外卖"
            txns.append({
                "description": vend,
                "date": d,
                "splits": [(ALIPAY, -amt),
                           (merchant_category(vend, EXP_DINING), amt)],
            })
        if wd == 6:  # weekly Hema groceries
            amt = _spend(rng, 300, 420)
            vend = "盒马鲜生"
            txns.append({
                "description": vend,
                "date": d,
                "splits": [(ALIPAY, -amt),
                           (merchant_category(vend, EXP_GROCERIES), amt)],
            })
        if wd == 2:  # weekly EV charging
            amt = _spend(rng, 60, 100)
            vend = "EV充电"
            txns.append({
                "description": vend,
                "date": d,
                "splits": [(WECHAT, -amt),
                           (merchant_category(vend, EXP_CHARGING), amt)],
            })
        d = date.fromordinal(d.toordinal() + 1)
        day_idx += 1

    # Monthly Sam's Club on ICBC card — every month in range.
    for yy, m in iter_months():
        sc = _clamp_day(yy, m, 12)
        if not _on_or_before_through(sc):
            continue
        amt = _spend(rng, 420, 580)
        vend = "山姆会员店"
        txns.append({
            "description": vend,
            "date": sc,
            "splits": [(ICBC_CARD, -amt),
                       (merchant_category(vend, EXP_GROCERIES), amt)],
        })

    # Repeating seasonal anchors for every FULL year after 2025 that the
    # timeline reaches (the 2025 calendar below is hand-curated). Keeps later
    # years from looking sparse vs. 2025 while staying date-bounded.
    extra_years = sorted(
        {yy for yy, _ in iter_months()} - {YEAR}
    )
    for yy in extra_years:
        recurring_seasonal = [
            (date(yy, 1, 20), "年货采购", ALIPAY, EXP_GROCERIES, D("2500")),
            (date(yy, 2, 10), "春节旅行 回乡", CHECKING, EXP_TRAVEL, D("3500")),
            (date(yy, 4, 5), "清明节 出行", CHECKING, EXP_TRAVEL, D("1500")),
            (date(yy, 5, 1), "劳动节 短途旅行", CHECKING, EXP_TRAVEL, D("2800")),
            (date(yy, 9, 10), "中秋月饼礼盒", ALIPAY, EXP_GIFTS, D("1800")),
            (date(yy, 10, 2), "国庆节旅行", CHECKING, EXP_TRAVEL, D("4500")),
            (date(yy, 12, 28), "年末慈善捐款", CHECKING, EXP_CHARITY, D("1000")),
        ]
        for dt, desc, src, dst, amt in recurring_seasonal:
            if _on_or_before_through(dt):
                txns.append({
                    "description": desc,
                    "date": dt,
                    "splits": [(src, -amt), (dst, amt)],
                })
        # 618 + Double 11 for the extra year — trimmed to match the 2025
        # hand-curated calendar below (¥1,150 / ¥1,700) now that the Phase 6b
        # near-monthly Clothing baseline carries more of the annual total.
        for i, amt in enumerate([D("450"), D("400"), D("300")]):
            dt = date(yy, 6, 10 + i)
            if _on_or_before_through(dt):
                txns.append({
                    "description": f"618购物节 第{i+1}单",
                    "date": dt,
                    "splits": [(ALIPAY, -amt), (EXP_CLOTHING, amt)],
                })
        for i, amt in enumerate([D("500"), D("450"), D("400"), D("350")]):
            dt = date(yy, 11, 11 + (i // 2))
            if _on_or_before_through(dt):
                txns.append({
                    "description": f"双十一 第{i+1}单",
                    "date": dt,
                    "splits": [(ICBC_CARD, -amt), (EXP_CLOTHING, amt)],
                })

    # Seasonal one-offs (Chinese calendar) — 2025 hand-curated calendar.
    seasonal = [
        (date(YEAR, 1, 20), "年货采购", ALIPAY, EXP_GROCERIES, D("2500")),
        (date(YEAR, 2, 10), "春节旅行 回乡", CHECKING, EXP_TRAVEL, D("3500")),
        (date(YEAR, 3, 8), "字节年度疫苗体检", ALIPAY, EXP_PET_VET, D("800")),
        (date(YEAR, 3, 20), "春装", CMB_CARD, EXP_CLOTHING, D("900")),
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

    # 618 (June) spread across 3 transactions (¥1,150). Trimmed from the
    # original 5-order ¥3,200 haul so the higher near-monthly Clothing baseline
    # (Phase 6b) doesn't push the annual Clothing average past the ~¥1,000/mo
    # band — the seasonal texture stays, the spike just carries less of it.
    june_amts = [D("450"), D("400"), D("300")]
    for i, amt in enumerate(june_amts):
        txns.append({
            "description": f"618购物节 第{i+1}单",
            "date": date(YEAR, 6, 10 + i),
            "splits": [(ALIPAY, -amt), (EXP_CLOTHING, amt)],
        })

    # Double 11 (November) spread across 4 transactions (¥1,700). Trimmed from
    # the original 8-order ¥5,500 haul for the same reason as 618 above.
    nov_amts = [D("500"), D("450"), D("400"), D("350")]
    for i, amt in enumerate(nov_amts):
        txns.append({
            "description": f"双十一 第{i+1}单",
            "date": date(YEAR, 11, 11 + (i // 2)),
            "splits": [(ICBC_CARD, -amt), (EXP_CLOTHING, amt)],
        })

    return txns


# ── Phase 6b: Personal-life spending (medical, gifts, charity, ───
#               travel, entertainment, personal care) ────────────

# Vendors keep the spending legible in the register and the bookkeeper's
# eyes — same texture as the daily/weekly vendor lists above. All Chinese
# (the book is a UTF-8 / character-encoding test corpus).
PHARMACY_VENDORS = ["国大药房", "海王星辰药店", "老百姓大药房", "叮当快药"]
CLINIC_VENDORS = ["社区健康服务中心 自费", "深圳市人民医院 门诊自费",
                  "北大深圳医院 挂号", "丁香诊所 自费"]
DENTAL_VENDORS = ["拜博口腔 洗牙", "美奥口腔 补牙", "深圳口腔医院"]
ENTERTAINMENT_VENDORS = [
    "万达影城 电影票", "保利院线 观影", "KTV 唱吧", "纯K量贩式KTV",
    "剧本杀 谜案馆", "密室逃脱", "海岸城酒吧", "1979酒吧",
    "深圳欢乐谷", "世界之窗", "Livehouse B10", "桌游吧",
]
CONCERT_VENDORS = [
    "演唱会门票 (深圳湾体育中心)", "音乐节 (大运中心)",
    "话剧 (保利剧院)", "脱口秀专场 (笑友剧场)",
]
GIFT_OCCASIONS = [
    "同事生日礼物", "朋友生日红包", "侄女生日礼物", "乔迁之礼",
    "满月红包", "探病果篮", "伴手礼",
]
WEDDING_OCCASIONS = [
    "婚礼红包 (大学同学)", "婚礼红包 (前同事)", "婚礼随礼 (表妹)",
    "婚礼红包 (老乡)",
]
ONLINE_RETAIL_VENDORS = ["淘宝", "京东商城", "拼多多", "天猫超市"]

# Tech-consultant learning: course platforms + technical books + meetups.
EDUCATION_COURSE_VENDORS = [
    "极客时间 专栏", "极客时间 训练营", "Udemy 课程", "拉勾教育 课程",
    "慕课网 实战课", "极客时间 大厂案例课",
]
EDUCATION_BOOK_VENDORS = [
    "京东 技术书籍", "当当 计算机图书", "O'Reilly 技术书", "异步图书 技术书",
]
EDUCATION_MEETUP_VENDORS = [
    "技术沙龙 报名", "QCon 大会 门票", "ArchSummit 架构师峰会",
    "GDG 深圳 Meetup", "PyCon China 门票",
]
# Recurring digital subscriptions a Shenzhen tech worker actually pays for.
# (mostly steady monthly autopay; VPN essential for cross-border work.)
SUBSCRIPTION_VENDORS = [
    ("VPN 服务 年付分摊", 28, 45),       # cross-border VPN (essential)
    ("iCloud 储存 200GB", 21, 21),       # Apple iCloud monthly
    ("知乎盐选 会员", 19, 25),            # Zhihu premium
    ("得到 知识会员", 25, 38),            # DeDao premium
    ("百度网盘 超级会员", 15, 30),        # cloud-drive membership
]


def gen_personal_life() -> list[dict]:
    """Personal-life spending streams, 2025-01 → THROUGH, localized to a
    Shenzhen (深圳) contractor household (林微 + 陈雨 + the cat 字节).

    Each stream walks the months with realistic cadence and lumpiness,
    amounts varied within CNY target ranges via the seeded RNG. Small
    daily spend rides WeChat Pay / Alipay (matching the existing daily
    conventions); larger items ride Checking or the credit cards. The
    cadence is deliberately near-monthly + periodic so the bookkeeper's
    recent-~5-month evaluation window is never empty for any category.

    Targets (monthly average, CNY):
      Medical        ~¥200-500   药店 monthly + periodic clinic/specialist + 牙科
      Gifts          ~¥400-700   small near-monthly baseline + 婚礼红包 + occasions
      Charity        ~¥100-300   腾讯公益 monthly + 99公益日 (Sep) spike
      Travel         periodic trips (春节/国庆/international client) every ~5 mo
      Entertainment  ~¥400-800   KTV/电影/剧本杀/bars + occasional 演唱会 spike
      Personal Care  ~¥300-600   健身房 monthly + 理发 periodic + 美容/按摩
      Misc (online)  occasional 淘宝/京东/拼多多 orders most months
      Education      ~¥200-400   极客时间/Udemy 课程 + 技术书籍 + 技术沙龙
      Subscriptions  ~¥100-200   VPN/iCloud/知乎盐选/得到 monthly autopay
    """
    txns: list[dict] = []
    start = date(YEAR, 1, 1)
    rng = random.Random(SEED + 21)

    # ── Medical: 药店 monthly small spend, periodic clinic/specialist ──
    #    visit, periodic 牙科 (dental). 医保 covers a lot, but out-of-pocket
    #    (自费) happens. Small spend on WeChat Pay, bigger on Checking.
    for yy, m in iter_months(start):
        # Monthly pharmacy / small out-of-pocket (~¥80-200).
        day = _clamp_day(yy, m, rng.randint(6, 24))
        if _on_or_before_through(day):
            amt = _spend(rng,80, 200)
            txns.append({"description": rng.choice(PHARMACY_VENDORS),
                         "date": day,
                         "splits": [(WECHAT, -amt), (EXP_MEDICAL, amt)]})
        # Periodic 自费 clinic / specialist visit (~¥250-500) in
        # Feb/May/Aug/Nov.
        if m in (2, 5, 8, 11):
            dday = _clamp_day(yy, m, rng.randint(8, 22))
            if _on_or_before_through(dday):
                amt = _spend(rng,250, 500)
                txns.append({"description": rng.choice(CLINIC_VENDORS),
                             "date": dday,
                             "splits": [(CHECKING, -amt),
                                        (EXP_MEDICAL, amt)]})
        # 牙科 (dental) twice a year — cleaning in March, a filling/checkup
        # in September (~¥300-700).
        if m in (3, 9):
            dday = _clamp_day(yy, m, rng.randint(10, 20))
            if _on_or_before_through(dday):
                amt = _spend(rng,300, 700)
                txns.append({"description": rng.choice(DENTAL_VENDORS),
                             "date": dday,
                             "splits": [(CHECKING, -amt),
                                        (EXP_MEDICAL, amt)]})

    # ── Gifts: LUMPY but with a small near-monthly baseline so no recent ──
    #    window is empty. The existing seasonal calendar already carries
    #    春节红包 (¥6,000 yearly, Phase 5), 中秋月饼礼盒 (¥1,800, Sep) and
    #    节日礼物 (¥2,000, Dec) — do NOT duplicate those. This layers a
    #    small monthly habit + scattered 婚礼红包 / occasion gifts on top.
    for yy, m in iter_months(start):
        # Skip Sep (中秋 carried) and Dec (节日礼物 carried) for the
        # baseline so we don't pile on top of the big named events.
        if m in (9, 12):
            continue
        if rng.random() < 0.85:  # ~5 of every 6 months get a small gift
            day = _clamp_day(yy, m, rng.randint(3, 26))
            if _on_or_before_through(day):
                amt = _spend(rng,120, 350)
                txns.append({"description": rng.choice(GIFT_OCCASIONS),
                             "date": day,
                             "splits": [(WECHAT, -amt), (EXP_GIFTS, amt)]})
    # 婚礼红包: 3-4 weddings a year, spread across non-春节 months, each
    # ¥800-1,600 — the lumpy occasions that make any 5-month window catch
    # at least one.
    for yy in sorted({y for y, _ in iter_months(start)}):
        n_weddings = rng.randint(3, 4)
        pool = [3, 4, 5, 6, 7, 8, 10, 11]
        chosen = rng.sample(pool, k=min(n_weddings, len(pool)))
        for m in chosen:
            day = _clamp_day(yy, m, rng.randint(3, 26))
            if not _on_or_before_through(day):
                continue
            amt = _spend(rng,800, 1600)
            txns.append({"description": rng.choice(WEDDING_OCCASIONS),
                         "date": day,
                         "splits": [(CHECKING, -amt), (EXP_GIFTS, amt)]})

    # ── Charity: recurring monthly 腾讯公益 donation (~¥100-200) + the ──
    #    99公益日 spike each September (~¥500-1,000). The year-end
    #    年末慈善捐款 (¥1,000, Dec 28) already exists in Phase 6 — don't
    #    duplicate it; this adds the steady monthly habit + the Sep spike.
    for yy, m in iter_months(start):
        day = _clamp_day(yy, m, 8)
        if _on_or_before_through(day):
            amt = _spend(rng,100, 200)
            txns.append({"description": "腾讯公益 月捐",
                         "date": day,
                         "splits": [(WECHAT, -amt), (EXP_CHARITY, amt)]})
        if m == 9:
            dday = _clamp_day(yy, m, 9)  # 99公益日
            if _on_or_before_through(dday):
                amt = _spend(rng,500, 1000)
                txns.append({"description": "99公益日 配捐",
                             "date": dday,
                             "splits": [(WECHAT, -amt), (EXP_CHARITY, amt)]})

    # ── Entertainment (NEW): weekly-ish outings (~¥80-200) on WeChat/Alipay ──
    #    + occasional 演唱会 / festival spike (~¥600-1,200) twice a year.
    d = start
    while d <= THROUGH:
        # ~3 outings a month (skip ~1 week in 5), jittered onto a
        # Fri-Sun evening.
        if rng.random() < 0.80:
            day = date.fromordinal(d.toordinal() + rng.randint(4, 6))
            if _on_or_before_through(day):
                amt = _spend(rng,80, 200)
                src = rng.choice([WECHAT, ALIPAY])
                txns.append({"description": rng.choice(ENTERTAINMENT_VENDORS),
                             "date": day,
                             "splits": [(src, -amt),
                                        (EXP_ENTERTAINMENT, amt)]})
        d = date.fromordinal(d.toordinal() + 7)
    # 演唱会 / 音乐节 spikes: spring (May) + fall (Oct), each year, on the
    # CMB card (bigger discretionary charge).
    for yy in sorted({y for y, _ in iter_months(start)}):
        for m in (5, 10):
            cday = _clamp_day(yy, m, rng.randint(8, 24))
            if not _on_or_before_through(cday):
                continue
            amt = _spend(rng,600, 1200)
            txns.append({"description": rng.choice(CONCERT_VENDORS),
                         "date": cday,
                         "splits": [(CMB_CARD, -amt),
                                    (EXP_ENTERTAINMENT, amt)]})

    # ── Personal Care (NEW): monthly 健身房 dues (~¥250) autopay + 理发 ──
    #    every ~5 weeks (~¥60-120) + periodic 美容/护肤 and 按摩.
    for yy, m in iter_months(start):
        day = _clamp_day(yy, m, 3)
        if _on_or_before_through(day):
            amt = _spend(rng,240, 280)  # 健身房 monthly dues
            txns.append({"description": "威尔士健身 月卡",
                         "date": day,
                         "splits": [(CHECKING, -amt),
                                    (EXP_PERSONAL_CARE, amt)]})
        # 美容 / 护肤 most months (~¥120-300) — skincare / facial.
        if rng.random() < 0.6:
            dday = _clamp_day(yy, m, rng.randint(12, 26))
            if _on_or_before_through(dday):
                amt = _spend(rng,120, 300)
                txns.append({"description": rng.choice(
                                 ["丝芙兰 护肤品", "屈臣氏 护肤", "美容院 面部护理"]),
                             "date": dday,
                             "splits": [(ALIPAY, -amt),
                                        (EXP_PERSONAL_CARE, amt)]})
        # 按摩 roughly every other month (~¥150-280).
        if m % 2 == 0:
            mday = _clamp_day(yy, m, rng.randint(14, 28))
            if _on_or_before_through(mday):
                amt = _spend(rng,150, 280)
                txns.append({"description": "中医推拿按摩",
                             "date": mday,
                             "splits": [(WECHAT, -amt),
                                        (EXP_PERSONAL_CARE, amt)]})
    # 理发 roughly every 5 weeks (35 days), jittered, on WeChat Pay.
    d = date.fromordinal(start.toordinal() + rng.randint(5, 18))
    while d <= THROUGH:
        amt = _spend(rng,60, 120)
        txns.append({"description": "理发店 剪发",
                     "date": d,
                     "splits": [(WECHAT, -amt), (EXP_PERSONAL_CARE, amt)]})
        d = date.fromordinal(d.toordinal() + 35 + rng.randint(-4, 6))

    # ── Light online retail thickening: occasional 淘宝/京东/拼多多 orders ──
    #    (~¥150-500) to Miscellaneous, most months (Chinese online shopping
    #    is heavy; Misc is otherwise thin). Paid on Alipay or CMB card.
    for yy, m in iter_months(start):
        if rng.random() < 0.7:  # most months
            day = _clamp_day(yy, m, rng.randint(2, 27))
            if not _on_or_before_through(day):
                continue
            amt = _spend(rng,150, 500)
            src = rng.choice([ALIPAY, CMB_CARD])
            txns.append({"description": rng.choice(ONLINE_RETAIL_VENDORS) + " 网购",
                         "date": day,
                         "splits": [(src, -amt), (EXP_MISC, amt)]})

    # ── Periodic trips (¥3,000-8,000 each): 高铁/flights + hotel folded ──
    #    into the month. A trip every ~5 months across the timeline so any
    #    recent-5-month window always catches at least one. Anchored to
    #    month 2 (春节 home) then stepped +5 months, cycling through:
    #    春节回乡 → 国庆出游 → international client visit (US Pacific Trade /
    #    Europe Handelskontor München). The light seasonal travel in
    #    Phase 6 stays intact; these are the bigger periodic anchors.
    trip_specs = [
        ("春节回乡 高铁+住宿 (深圳→老家)", CHECKING, 3000, 5000),
        ("国庆出游 机票+酒店 (云南/三亚)", CHECKING, 4000, 7000),
        ("出差 美国 Pacific Trade (机票+酒店)", CMB_CARD, 6000, 8000),
        ("出差 德国 Handelskontor München (机票+酒店)", CMB_CARD, 6000, 8000),
    ]
    trip_idx = 0
    cur = date(YEAR, 2, 1)
    while cur <= THROUGH:
        tday = _clamp_day(cur.year, cur.month, rng.randint(8, 22))
        desc, src, lo, hi = trip_specs[trip_idx % len(trip_specs)]
        if _on_or_before_through(tday):
            amt = _spend(rng,lo, hi)
            txns.append({"description": desc, "date": tday,
                         "splits": [(src, -amt), (EXP_TRAVEL, amt)]})
        nm = cur.month - 1 + 5  # +5 months
        cur = date(cur.year + nm // 12, nm % 12 + 1, 1)
        trip_idx += 1

    # ── Clothing (baseline, part 1 — shared RNG): the original modest ──
    #    near-monthly wardrobe habit. Kept on the shared ``rng`` in its original
    #    position and with its original draw structure (so every category drawn
    #    AFTER it — Education, Subscriptions — stays byte-stable). The recent-
    #    window boost is layered separately at the end of this function on a
    #    DEDICATED rng (part 2), so tuning the boost never perturbs this stream.
    CLOTHING_ONLINE_VENDORS = ["优衣库 网店", "淘宝 服饰", "天猫 服装旗舰店",
                               "网易严选 服饰"]
    CLOTHING_MALL_VENDORS = ["万象城 优衣库", "海岸城 ZARA", "万象城 商场购物",
                             "海岸城 H&M", "壹方城 服装"]
    for yy, m in iter_months(start):
        if rng.random() < 0.8:  # most months get a clothing purchase
            day = _clamp_day(yy, m, rng.randint(4, 26))
            if _on_or_before_through(day):
                amt = _spend(rng,300, 650)
                src = rng.choice([ALIPAY, ALIPAY, CMB_CARD])
                vend = (rng.choice(CLOTHING_MALL_VENDORS) if src == CMB_CARD
                        else rng.choice(CLOTHING_ONLINE_VENDORS))
                txns.append({"description": vend, "date": day,
                             "splits": [(src, -amt), (EXP_CLOTHING, amt)]})

    # ── Education (NEW): a tech consultant who keeps learning. A recurring ──
    #    small course/learning habit most months (~¥120-260 on 极客时间/慕课网,
    #    Alipay/WeChat), an occasional bigger course or technical book on the
    #    京东/当当/O'Reilly side (~¥200-400 on CMB card / Checking) a few times
    #    a year, and a periodic 技术沙龙/meetup/conference fee. Aggregate lands
    #    in the ~¥200-400/mo target. Near-monthly so the recent window is full.
    for yy, m in iter_months(start):
        # Small recurring online-course / column spend most months.
        if rng.random() < 0.75:
            day = _clamp_day(yy, m, rng.randint(5, 24))
            if _on_or_before_through(day):
                amt = _spend(rng,120, 260)
                src = rng.choice([ALIPAY, WECHAT])
                txns.append({
                    "description": rng.choice(EDUCATION_COURSE_VENDORS),
                    "date": day,
                    "splits": [(src, -amt), (EXP_EDUCATION, amt)]})
        # Quarterly-ish technical book order (Feb/May/Aug/Nov), larger ticket.
        if m in (2, 5, 8, 11):
            bday = _clamp_day(yy, m, rng.randint(8, 22))
            if _on_or_before_through(bday):
                amt = _spend(rng,200, 400)
                txns.append({
                    "description": rng.choice(EDUCATION_BOOK_VENDORS),
                    "date": bday,
                    "splits": [(CMB_CARD, -amt), (EXP_EDUCATION, amt)]})
        # Twice-a-year 技术沙龙 / meetup / conference entry (Apr + Oct).
        if m in (4, 10):
            mday = _clamp_day(yy, m, rng.randint(10, 24))
            if _on_or_before_through(mday):
                amt = _spend(rng,200, 400)
                txns.append({
                    "description": rng.choice(EDUCATION_MEETUP_VENDORS),
                    "date": mday,
                    "splits": [(CHECKING, -amt), (EXP_EDUCATION, amt)]})

    # ── Subscriptions (NEW): steady monthly digital autopay. Each vendor ──
    #    debits on its own day-of-month from Checking (autopay), giving a
    #    stable ~¥100-200/mo aggregate. VPN essential for cross-border tech
    #    work; iCloud/知乎盐选/得到/百度网盘 round out the stack. Each runs
    #    every month in range so any recent window is fully populated.
    for i, (label, lo, hi) in enumerate(SUBSCRIPTION_VENDORS):
        debit_day = 4 + i * 5  # spread across the month (4, 9, 14, 19, 24)
        for yy, m in iter_months(start):
            day = _clamp_day(yy, m, debit_day)
            if not _on_or_before_through(day):
                continue
            amt = _spend(rng,lo, hi)
            txns.append({
                "description": label,
                "date": day,
                "splits": [(CHECKING, -amt), (EXP_SUBSCRIPTIONS, amt)]})

    # ── Clothing (baseline, part 2 — dedicated RNG top-up): the Phase 6 ──
    #    618/双十一/春装 seasonal spikes cluster in Mar/Jun/Nov, so a recent
    #    Feb→Jun evaluation window sees none of them, and part 1's modest
    #    ~¥300-650/0.8 habit alone reads only ~¥300/mo there — well under the
    #    bookkeeper's ~¥500-1,000/mo band. This near-certain monthly top-up
    #    (~¥250-380: more 优衣库/淘宝/天猫 online + mall trips to 万象城/海岸城)
    #    lifts part 1 + part 2 combined into band for the recent window while
    #    keeping the annual average inside ~¥600-1,000/mo (helped by the trimmed
    #    Phase 6 spikes). Uses its OWN dedicated RNG (``SEED + 210``) so its
    #    draw count is decoupled from the shared stream — Education,
    #    Subscriptions, and every category drawn earlier stay byte-stable no
    #    matter how this block is tuned. Kept last in the function for that
    #    reason. Small online buys ride Alipay; mall hauls ride the CMB card.
    rng_cloth = random.Random(SEED + 210)
    for yy, m in iter_months(start):
        if rng_cloth.random() < 0.95:  # nearly every month gets a top-up
            day = _clamp_day(yy, m, rng_cloth.randint(4, 26))
            if _on_or_before_through(day):
                amt = _spend(rng_cloth, 250, 380)
                src = rng_cloth.choice([ALIPAY, ALIPAY, CMB_CARD])
                vend = (rng_cloth.choice(CLOTHING_MALL_VENDORS)
                        if src == CMB_CARD
                        else rng_cloth.choice(CLOTHING_ONLINE_VENDORS))
                txns.append({"description": vend, "date": day,
                             "splits": [(src, -amt), (EXP_CLOTHING, amt)]})

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
    years = sorted({yy for yy, _ in iter_months()})
    for yy in years:
        for month_range, client, amt in contracts:
            for m in month_range:
                d = date(yy, m, 18)
                if not (date(YEAR, 1, 1) <= d <= THROUGH):
                    continue
                txns.append({
                    "description": f"{client} 合同款",
                    "date": d,
                    "splits": [(CHECKING, amt), (CONTRACTOR, -amt)],
                })
    return txns


# ── Phase 7b: Business module (customers, vendors, invoices, bills)

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


def _job_by_name(book: GnuCashBook, name: str) -> dict:
    env = book.list_jobs(compact=False, limit=250)
    rows = next((v for v in env.values() if isinstance(v, list)), [])
    for row in rows:
        if row.get("name") == name:
            return row
    raise SystemExit(f"continuation: job {name!r} not found in book")


def run_business(book: GnuCashBook, since: date | None = None) -> dict:
    """Create billterms, customers, vendors, invoices, and bills.

    Returns a small dict of counts plus the JetBrains bill id (for
    verification).

    ``since`` (continuation mode): entities and jobs already exist in
    the frozen prefix — look them up instead of creating; emit only
    documents opened AFTER ``since``, and skip a THROUGH-relative
    "recent open" document while its predecessor is still outstanding.
    """
    counts = {"customers": 0, "vendors": 0, "invoices": 0, "bills": 0,
              "terms": 0}

    open_owner_names: set[str] = set()
    if since is None:
        book.create_billterm(name="Net 15", due_days=15,
                             description="15天内付款")
        book.create_billterm(name="Net 30", due_days=30,
                             description="30天内付款")
        book.create_billterm(name="2/10 Net 30", due_days=30,
                             discount_days=10, discount_percent="2",
                             description="10天内付款享2%折扣")
        counts["terms"] = 3

        # Customers.
        shenzhen = book.create_customer(
            name="深圳跨境电商有限公司", currency="CNY",
            notes="本地跨境电商客户, Net 30")
        pacific = book.create_customer(
            name="Pacific Trade Solutions", currency="USD",
            notes="美国客户, 移动应用外包, Net 30")
        munich = book.create_customer(
            name="Handelskontor München GmbH", currency="EUR",
            notes="德国客户, ERP 集成项目, Net 30")
        counts["customers"] = 3

        # Vendors.
        alibaba = book.create_vendor(
            name="阿里云", currency="CNY", notes="云服务")
        jetbrains = book.create_vendor(
            name="JetBrains", currency="USD",
            notes="IDE 年度订阅")
        urwork = book.create_vendor(
            name="优客工场", currency="CNY", notes="联合办公空间")
        counts["vendors"] = 3

        # 陈宇 — the part-time assistant behind the 陈宇工资 salary
        # schedule (bookkeeper review §3: a salary schedule with zero
        # employees registered read as a phantom).
        book.create_employee(name="陈宇", currency="CNY")
        counts["employees"] = 1

        # Jobs — multi-invoice projects (owner_type=job over customers),
        # matching the prior hand-built book's 3 active jobs. A couple of
        # invoices below attach to these via job_id so get_job_report has
        # data.
        job_sz = book.create_job(
            owner_id=shenzhen["id"], owner_type="customer",
            name="跨境电商平台改版", reference="SZ-2025-01")
        job_pacific = book.create_job(
            owner_id=pacific["id"], owner_type="customer",
            name="Pacific Mobile App v2", reference="PAC-2025-Q3")
        job_munich = book.create_job(
            owner_id=munich["id"], owner_type="customer",
            name="München ERP-Integration", reference="MUC-2025-11")
        counts["jobs"] = 3
    else:
        shenzhen = _party_by_name(book, "深圳跨境电商有限公司")
        pacific = _party_by_name(book, "Pacific Trade Solutions")
        munich = _party_by_name(book, "Handelskontor München GmbH")
        alibaba = _party_by_name(book, "阿里云")
        jetbrains = _party_by_name(book, "JetBrains")
        urwork = _party_by_name(book, "优客工场")
        job_sz = _job_by_name(book, "跨境电商平台改版")
        job_pacific = _job_by_name(book, "Pacific Mobile App v2")
        job_munich = _job_by_name(book, "München ERP-Integration")
        env = book.get_outstanding_invoices(compact=False, limit=250)
        open_owner_names = {
            doc.get("owner_name") for doc in env.get("invoices", [])
        }

    def run_invoice(customer_id, date_open, date_pay,
                    amount, description, currency, post_account,
                    pay=True, job_id=None):
        """Create → post → (optionally) pay a customer invoice.

        ``date_open`` / ``date_pay`` are ``date`` objects. When ``pay`` is
        False the invoice is posted but left OUTSTANDING (a receivables
        demo surface). ``job_id`` groups the invoice under a Job.
        """
        if since is not None and date_open <= since:
            return None
        inv = book.create_invoice(
            customer_id=customer_id, date_opened=date_open.isoformat(),
            currency=currency, term="Net 30", job_id=job_id,
        )
        book.add_invoice_entry(
            invoice_id=inv["id"], account=LLC_REVENUE,
            description=description, quantity="1", price=amount,
        )
        # No force needed: add_prices() lays real FX quotes on cross-currency
        # post & pay dates (CROSS_CCY_FX_DATES) plus a monthly snapshot on the
        # 1st of every month through THROUGH, so the 90-day freshness guard is
        # always satisfied with a true market rate. The post→pay rate drift
        # books a real realized FX gain/loss on paid invoices.
        book.post_invoice(
            invoice_id=inv["id"], post_account=post_account,
            post_date=date_open.isoformat(), owner_type="customer",
        )
        if pay:
            book.pay_invoice(
                invoice_id=inv["id"], payment_account=CHECKING,
                amount=amount, payment_date=date_pay.isoformat(),
                owner_type="customer",
            )
        counts["invoices"] += 1
        return inv["id"]

    # A "recent" anchor relative to THROUGH for open invoices. Snap to the
    # 1st of THROUGH's month and the prior month so the post date always has a
    # monthly FX snapshot (cross-currency 90-day freshness guard) and reads as
    # current, not year-overdue.
    recent_open = date(THROUGH.year, THROUGH.month, 1)
    prev_m = recent_open.month - 1 or 12
    prev_y = recent_open.year if recent_open.month > 1 else recent_open.year - 1
    prev_open = date(prev_y, prev_m, 1)

    # Shenzhen (CNY): ¥12,000/month for all of 2025, PAID. The first two
    # months attach to the cross-border-platform job (multi-invoice project).
    for m in range(1, 13):
        run_invoice(
            shenzhen["id"], date(YEAR, m, 1), date(YEAR, m, 28), "12000",
            f"{date(YEAR, m, 1).strftime('%Y年%m月')} 移动应用开发",
            "CNY", AR_CNY,
            job_id=(job_sz["id"] if m in (1, 2) else None),
        )
    # Shenzhen OUTSTANDING (CNY A/R demo surface): one recent + one older
    # open invoice, both unpaid. Continuation: skipped while a Shenzhen
    # document is still outstanding (don't stack open invoices).
    if "深圳跨境电商有限公司" not in open_owner_names:
        run_invoice(
            shenzhen["id"], recent_open, recent_open, "15000",
            f"{recent_open.strftime('%Y年%m月')} 平台改版里程碑",
            "CNY", AR_CNY, pay=False, job_id=job_sz["id"])
        run_invoice(
            shenzhen["id"], date(YEAR + 1, 1, 1), date(YEAR + 1, 1, 1), "9000",
            f"{date(YEAR + 1, 1, 1).strftime('%Y年%m月')} 运维支持",
            "CNY", AR_CNY, pay=False)

    # Pacific Trade (USD → AR USD): the 2025 plan is PAID cross-currency to
    # CNY (PACIFIC_PLAN shares dates with the price layer). The Q3 batch
    # attaches to the Pacific job. One recent USD invoice is left OUTSTANDING.
    for m, amt in PACIFIC_PLAN:
        pay_m = m + 1 if m < 12 else 1
        pay_yr = YEAR if m < 12 else YEAR + 1
        run_invoice(
            pacific["id"], date(YEAR, m, 5), date(pay_yr, pay_m, 5), amt,
            f"{date(YEAR, m, 1).strftime('%B %Y')} cross-border app engagement",
            "USD", AR_USD,
            job_id=(job_pacific["id"] if m == 9 else None),
        )
    if "Pacific Trade Solutions" not in open_owner_names:
        run_invoice(
            pacific["id"], recent_open, recent_open, "5200",
            f"{recent_open.strftime('%B %Y')} retainer + change requests",
            "USD", AR_USD, pay=False, job_id=job_pacific["id"])

    # Munich (EUR → AR EUR): the 2025 plan is PAID cross-currency to CNY.
    # One older + one recent EUR invoice are left OUTSTANDING.
    for m, amt in MUNICH_PLAN:
        pay_m = m + 1
        run_invoice(
            munich["id"], date(YEAR, m, 8), date(YEAR, pay_m, 8), amt,
            f"{date(YEAR, m, 1).strftime('%B %Y')} Softwareentwicklung",
            "EUR", AR_EUR,
            job_id=(job_munich["id"] if m == 11 else None),
        )
    if "Handelskontor München GmbH" not in open_owner_names:
        run_invoice(
            munich["id"], prev_open, prev_open, "4100",
            f"{prev_open.strftime('%B %Y')} ERP-Integration Phase 2",
            "EUR", AR_EUR, pay=False, job_id=job_munich["id"])
        run_invoice(
            munich["id"], recent_open, recent_open, "2800",
            f"{recent_open.strftime('%B %Y')} Wartung",
            "EUR", AR_EUR, pay=False)

    # Vendor bills.
    def run_bill(vendor_id, date_open, date_pay, amount,
                 description, expense_account, currency,
                 payment_account=CHECKING, pay=True, post_account=AP):
        """Create → post → (optionally) pay a vendor bill. Dates are ``date``."""
        if since is not None and date_open <= since:
            return None
        bill = book.create_bill(
            vendor_id=vendor_id, date_opened=date_open.isoformat(),
            currency=currency, term="Net 30",
        )
        book.add_bill_entry(
            bill_id=bill["id"], account=expense_account,
            description=description, quantity="1", price=amount,
        )
        # No force: a real FX quote sits on every cross-currency post/pay
        # date (CROSS_CCY_FX_DATES) plus the monthly snapshots through THROUGH.
        # CNY bills don't convert at all.
        book.post_invoice(
            invoice_id=bill["id"], post_account=post_account,
            post_date=date_open.isoformat(), owner_type="vendor",
        )
        if pay:
            book.pay_invoice(
                invoice_id=bill["id"], payment_account=payment_account,
                amount=amount, payment_date=date_pay.isoformat(),
                owner_type="vendor",
            )
        counts["bills"] += 1
        return bill["id"]

    # Alibaba Cloud: a couple of CNY bills through the business module
    # (the monthly recurring cloud charge in Phase 5 is the day-to-day;
    # these exercise the vendor-bill path explicitly).
    for m in (4, 10):
        run_bill(alibaba["id"], date(YEAR, m, 2), date(YEAR, m, 20), "350",
                 f"{date(YEAR, m, 1).strftime('%Y年%m月')} 云服务器",
                 EXP_CLOUD, "CNY")

    # UrWork: CNY bills.
    for m in (2, 8):
        run_bill(urwork["id"], date(YEAR, m, 3), date(YEAR, m, 18), "1500",
                 f"{date(YEAR, m, 1).strftime('%Y年%m月')} 工位租赁",
                 EXP_COWORKING, "CNY")

    # JetBrains US$249 — the foreign-currency PAYABLE regression case (M2).
    # RE-DATED to a recent month (the 1st of THROUGH's month, which carries a
    # USD/CNY monthly snapshot) and left OUTSTANDING (pay=False) so it reads
    # as a current payable, not apparent corruption. Posted to the CNY-only
    # ``Liabilities:Accounts Payable``: the USD bill currency drives the A/P
    # split — its VALUE is USD (txn currency), its QUANTITY the CNY equivalent
    # at the real USD/CNY rate on the post date. vendor_spending_report
    # converts off the bill currency regardless of the A/P account commodity,
    # so M2 coverage holds without a USD-denominated A/P account.
    jetbrains_post = recent_open
    jetbrains_bill_id = None
    if "JetBrains" not in open_owner_names:
        jetbrains_bill_id = run_bill(
            jetbrains["id"], jetbrains_post, jetbrains_post, "249",
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


# Chinese A-shares / ETFs trade in whole units (ETFs in round lots of 100,
# stocks share-by-share). You cannot hold a fractional ETF share, so DCA
# buys a WHOLE number of units sized to a target budget — never a fixed CNY
# amount divided by price (which produces fractional holdings).
ROUND_LOT = {"600519": 1, "300750": 1, "510300": 100, "159915": 100}


def _whole_units(budget: Decimal, price: Decimal, lot: int) -> Decimal:
    """Largest whole multiple of ``lot`` units whose cost ≤ ``budget``.

    ETFs (lot=100) round down to the nearest 100; stocks (lot=1) to whole
    shares. Returns at least one lot so a DCA buy is never zero-sized.
    """
    raw = budget / price
    units = (int(raw) // lot) * lot
    if units < lot:
        units = lot
    return Decimal(units)


def run_investments(out_path: Path, since: date | None = None) -> dict:
    """Monthly DCA, quarterly trades, and dividends. Direct piecash.

    ``since`` (continuation mode): skip every event dated on or before
    it — those trades and lots already exist in the frozen prefix."""
    cut = since or date(YEAR, 1, 1) - timedelta(days=1)
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
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

        # Monthly DCA: target ~¥2,000 CSI300 + ~¥1,000 ChiNext on the 1st,
        # but buy a WHOLE number of units (round lots of 100). Real market
        # price at the buy date sets units and booked value, so the lot cost
        # basis reflects actual history and holdings stay whole. Runs every
        # month through THROUGH so DCA continues into the present.
        dca = [("510300", D("2000")), ("159915", D("1000"))]
        for yy, m in iter_months():
            d = _clamp_day(yy, m, 1)
            if not _on_or_before_through(d) or d <= cut:
                continue
            for sym, budget in dca:
                price = real_price(sym, d)
                units = _whole_units(budget, price, ROUND_LOT[sym])
                cost = (units * price).quantize(D("0.01"))
                inv_acct = acct[ACCT_BY_SYMBOL[sym]]
                lot = piecash.Lot(
                    title=f"{sym} DCA {yy}-{m:02d}", account=inv_acct,
                    notes=f"定投 {units} 份 @ ¥{price}", is_closed=0,
                )
                inv_split = piecash.Split(
                    account=inv_acct, value=cost, quantity=units)
                cash_split = piecash.Split(account=acct[CHECKING], value=-cost)
                piecash.Transaction(
                    currency=cny, description=f"定投 {sym}",
                    post_date=d,
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
            if d <= cut:
                continue
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
                # Realized P/L = sale proceeds − cost basis, SIGNED.
                #   above cost → realized_pl > 0  (a gain)
                #   below cost → realized_pl < 0  (a LOSS — e.g. Moutai sold
                #               at ¥1,437 vs ¥1,700 basis = −¥263)
                # Capital Gains is a credit-normal INCOME account, so a gain
                # is a credit (value = −realized_pl < 0) and a loss is a debit
                # (value = −realized_pl > 0) that REDUCES capital-gains income.
                # The three splits sum to zero either way:
                #   (−cost_basis) + cny_amt + (−realized_pl)
                #   = −cost_basis + cny_amt − (cny_amt − cost_basis) = 0.
                realized_pl = cny_amt - cost_basis
                inv_split = piecash.Split(
                    account=inv_acct, value=-cost_basis, quantity=-shares)
                cash_split = piecash.Split(account=acct[CHECKING], value=cny_amt)
                gain_split = piecash.Split(
                    account=acct[CAPITAL_GAINS], value=-realized_pl)
                # Defensive: the synthetic data must balance to the fen.
                assert (-cost_basis) + cny_amt + (-realized_pl) == 0
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
            if date(YEAR, m, day) <= cut:
                continue
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

    # CMB: monthly statement payment (continues every month through THROUGH so
    # the recurring Alibaba+coworking charges keep getting paid off);
    # September 2025 late fee + interest.
    for yy, m in iter_months():
        d = _clamp_day(yy, m, 25)
        if not _on_or_before_through(d):
            continue
        amt = D("1850")  # covers monthly Alibaba+coworking charges
        txns.append({
            "description": "招商银行信用卡 还款",
            "date": d,
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
                           account=EXP_UTILITIES, amount="700",
                           period="all")
    # Pet (parent rollup).
    book.set_budget_amount(budget_name=name, account=EXP_PET,
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
    span = (THROUGH.toordinal() - start)
    # Scale the volume count with the timeline length so a multi-year book
    # gets proportional small-transaction noise (≈ the original 320/year).
    days = max(span, 1)
    count = max(VOLUME_TXN_COUNT, round(VOLUME_TXN_COUNT * days / 365))
    for _ in range(count):
        dt = date.fromordinal(start + rng.randint(0, span))
        vendor = rng.choice(VOLUME_VENDORS)
        amt = _spend(rng, 5, 80)
        # Category is the merchant's canonical bucket — consistent every time,
        # including the sticky vending-machine→Misc miscategorization. No more
        # per-transaction random scatter across Dining/Misc.
        category = merchant_category(vendor, EXP_DINING)
        txns.append({
            "description": vendor,
            "date": dt,
            "splits": [(CHECKING, -amt), (category, amt)],
        })
    return txns


# ── Phase 11: Reconciliation ────────────────────────────────────

def run_reconciliation(book: GnuCashBook) -> None:
    """Reconcile only the first months of 2025 for checking.

    A realistic personal book is never fully reconciled — the bookkeeper
    catches up the bank account for a few early months and then falls behind,
    leaving hundreds-to-thousands of unreconciled splits. We reconcile
    checking through the end of March 2025 only; everything after stays
    unreconciled.
    """
    for label, through, stmt_date in [
        ("January", date(YEAR, 1, 31), date(YEAR, 1, 31)),
        ("February", date(YEAR, 2, 28), date(YEAR, 2, 28)),
        ("March", date(YEAR, 3, 31), date(YEAR, 3, 31)),
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


# ── Scheduled-transaction state (kept ENABLED) ──────────────────

def set_schedule_state(out_path: Path) -> None:
    """Stamp SX cursors via the shared engine rule: everything current,
    at most ONE schedule overdue and only when it "just came due" (3-7
    days -- bookkeeper review §2). Replaces the fixed two-overdue-index
    scheme, whose hooks aged into reading as neglect."""
    from continuation import advance_sx
    return advance_sx(out_path, THROUGH)


# ── Verification ────────────────────────────────────────────────

def _parse_money(s) -> Decimal:
    """Parse a comma-formatted money string (or Decimal) to Decimal."""
    return Decimal(str(s).replace(",", "").replace("¥", "").strip())


def _verify_realism(out_path: Path) -> None:
    """Deep-realism evidence: cents, merchant→category, payroll, cap-gains."""
    book = piecash.open_book(str(out_path), readonly=True)
    try:
        txns = list(book.transactions)

        # 1. Cents on consumer spend. Sample ~20 daily-spend transactions and
        #    report the fraction carrying non-zero jiao/fen.
        consumer_merchants = (
            "瑞幸", "美团外卖", "饿了么", "盒马", "便利蜂", "7-11",
            "全家便利店", "山姆", "滴滴出行", "共享单车", "自动贩卖机",
            "EV充电",
        )
        consumer = [t for t in txns
                    if any(t.description.startswith(k)
                           for k in consumer_merchants)]
        consumer.sort(key=lambda t: str(t.post_date))
        sample = consumer[:20]
        with_cents = 0
        print("\n-- Realism #1: cents on consumer spend (sample of 20) --")
        for t in sample:
            amt = abs(t.splits[0].value)
            has_cents = (amt % 1) != 0
            with_cents += 1 if has_cents else 0
            print(f"    {t.description:18s} ¥{amt}")
        frac = with_cents / len(sample) if sample else 0
        print(f"  fraction with non-zero jiao/fen: {with_cents}/{len(sample)} "
              f"= {frac:.0%}")

        # Structured items stay round.
        print("  structured items (should be round):")
        for key in ("深圳市人民医院 工资", "房贷还款", "车贷还款",
                    "春节红包"):
            hit = next((t for t in txns if t.description.startswith(key)),
                       None)
            if hit:
                vals = [abs(s.value) for s in hit.splits]
                allround = all((v % 1) == 0 for v in vals)
                print(f"    {key:22s} splits {vals}  all_round={allround}")

        # 3. Variable payroll withholding across months (incl. overtime).
        print("\n-- Realism #3: payroll withholding varies by month --")
        sal = sorted(
            (t for t in txns if t.description.startswith("深圳市人民医院 工资")),
            key=lambda t: str(t.post_date))
        # First four 2025 salary runs (March is an overtime month).
        for t in sal[:4]:
            def _split(path_end):
                for s in t.splits:
                    if s.account.fullname.endswith(path_end):
                        return s.value
                return None
            gross = -_split("收入:工资")
            it = _split("税费:个人所得税")
            soc = _split("税费:社会保险")
            hf = _split("住房公积金")
            ot = "OT" if "加班" in t.description else "  "
            print(f"    {str(t.post_date)[:7]} {ot} gross ¥{gross:>8}  "
                  f"income_tax ¥{it:>8}  social ¥{soc:>6}  housing ¥{hf:>6}")

        # 2. Consistent merchant → category mapping.
        print("\n-- Realism #2: merchant → category consistency --")
        from collections import defaultdict
        m2c: dict[str, set] = defaultdict(set)
        for t in txns:
            # The expense split is the one whose account is under Expenses.
            exp = [s for s in t.splits
                   if s.account.fullname.startswith("支出:")]
            for s in exp:
                for key in ("瑞幸", "美团外卖", "饿了么", "盒马", "便利蜂",
                            "滴滴出行", "共享单车", "自动贩卖机", "山姆",
                            "EV充电"):
                    if t.description.startswith(key):
                        m2c[key].add(s.account.fullname)
        for key in sorted(m2c):
            cats = sorted(m2c[key])
            flag = "OK (single)" if len(cats) == 1 else "⚠ MULTIPLE"
            short = [c.replace("支出:", "") for c in cats]
            print(f"    {key:10s} → {short}  {flag}")
        print("  deliberate sticky miscategorization: "
              "自动贩卖机 → 杂项 (always)")

        # 4. Meta-notes stripped from persisted customer/vendor data.
        print("\n-- Realism #4: meta-notes in persisted data --")
        bad = ("FX payable", "USD-denominated", "计价", "case", "H1 case",
               "regression")
        offenders = []
        for ent in list(book.customers) + list(book.vendors):
            note = ent.notes or ""
            for b in bad:
                if b in note:
                    offenders.append((ent.name, b, note))
        print(f"  customer/vendor notes scanned: "
              f"{len(list(book.customers)) + len(list(book.vendors))}")
        print(f"  offending notes: {offenders if offenders else 'NONE'}")

        # 5. Capital-gains sign: below-cost sale must be a LOSS (negative).
        print("\n-- Realism #5: realized capital gain/loss sign --")
        for t in txns:
            if t.description.startswith("卖出"):
                cg = [s for s in t.splits if s.account.fullname.endswith(
                    "Capital Gains")]
                if not cg:
                    continue
                # Realized P/L = -(value on the income split).
                realized = -cg[0].value
                kind = "LOSS" if realized < 0 else "gain"
                bal = sum(s.value for s in t.splits)
                print(f"    {t.description:26s} realized P/L ¥{realized:>9} "
                      f"({kind})  splits_balance={bal == 0}")
    finally:
        book.close()


def verify(out_path: Path, business: dict) -> None:
    print("\n" + "=" * 64)
    print("VERIFICATION")
    print("=" * 64)
    book = GnuCashBook(str(out_path))

    # Covers all activity through the present (THROUGH) as well as the cached
    # price horizon (END). Prices forward-fill past END.
    as_of = max(END, THROUGH)
    latest_hkd = md_fx_cny("HKD", as_of)
    latest_usd = md_fx_cny("USD", as_of)

    summary = book.get_book_summary()
    bs = book.balance_sheet(as_of_date=as_of)
    nw = book.net_worth(end_date=as_of)

    # ── Realism checks (cents, categorization, withholding, cap-gains) ──
    _verify_realism(out_path)

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

    # Investment holdings via real security prices. Whole-unit check:
    # CSI300 / ChiNext must be integers (no fractional ETF shares).
    print("\n-- Investment holdings (whole-unit check) --")
    print(f"  Moutai latest real price: ¥{md_security('600519', as_of)}")
    for path in (MOUTAI, CATL, CSI300, CHINEXT):
        bal = Decimal(str(book.get_balance(path)))
        whole = (bal == bal.to_integral_value())
        print(f"  {path}: shares {bal}  whole={whole}")
    inv_rows = [r for r in bs["assets"]["accounts"]
                if "Brokerage" in r["account"]]
    print(f"  balance_sheet brokerage rows (CNY value): {inv_rows}")

    # Data-cliff / runway check: recent monthly net + runway from the
    # dashboard. With activity through THROUGH these must be non-zero /
    # realistic (not a 6,000-day runway).
    print("\n-- Data-cliff / runway (get_book_summary) --")
    for line in summary.splitlines():
        low = line.lower()
        if any(k in low for k in (
                "monthly net", "runway", "burn", "month net",
                "净", "跑道", "月")):
            print(f"  {line.strip()}")

    # Receivables across all three A/R commodities (outstanding invoices).
    print("\n-- Outstanding receivables (CNY / USD / EUR A/R) --")
    for path, label in ((AR_CNY, "CNY"), (AR_USD, "USD"), (AR_EUR, "EUR")):
        bal = book.get_balance(path, as_of_date=as_of)
        print(f"  {path} ({label}): {bal}")
    try:
        oi = book.get_outstanding_invoices(compact=False)
        n_out = oi.get("count", oi.get("total")) if isinstance(oi, dict) else oi
        print(f"  get_outstanding_invoices count: {n_out}")
    except Exception as exc:  # noqa: BLE001
        print(f"  get_outstanding_invoices unavailable: {exc}")

    # Personal-life spending: avg monthly over the last ~5 months. The
    # bookkeeper evaluates the most recent window, so every category must
    # carry flow there (not a single event elsewhere in the 17-month span).
    print("\n-- Personal-life spending (avg/mo over last ~5 months) --")
    nm = THROUGH.month - 4
    ws_y = THROUGH.year + (nm - 1) // 12
    ws_m = (nm - 1) % 12 + 1
    window_start = date(ws_y, ws_m, 1)
    n_months = 5
    day_before = date.fromordinal(window_start.toordinal() - 1)
    personal = [
        ("Medical", EXP_MEDICAL, (200, 500), False),
        ("Gifts", EXP_GIFTS, (200, 700), True),
        ("Charity", EXP_CHARITY, (100, 300), False),
        ("Travel", EXP_TRAVEL, (0, None), False),
        ("Entertainment", EXP_ENTERTAINMENT, (400, 800), False),
        ("Personal Care", EXP_PERSONAL_CARE, (300, 600), False),
        ("Clothing", EXP_CLOTHING, (500, 1000), True),
        ("Education", EXP_EDUCATION, (200, 400), False),
        ("Subscriptions", EXP_SUBSCRIPTIONS, (100, 200), False),
    ]
    print(f"  window: {window_start.isoformat()} → {THROUGH.isoformat()} "
          f"({n_months} months)")
    for label, path, (lo, hi), lumpy in personal:
        bal_start = _parse_money(book.get_balance(path, as_of_date=day_before))
        bal_end = _parse_money(book.get_balance(path, as_of_date=as_of))
        period = bal_end - bal_start
        avg = period / n_months
        if hi is None:
            band = f"(target trip-driven; ≥1 trip in window)"
            ok = period > 0
        elif lumpy:
            band = (f"(lumpy; target ~¥{lo}-{hi}/mo, recent window may "
                    "run low/high)")
            ok = period > 0
        else:
            ok = lo * D("0.5") <= avg <= hi * D("1.8")
            band = f"(target ~¥{lo}-{hi}/mo)"
        print(f"    {label:14s} period ¥{period:>10,.2f} | "
              f"avg ¥{avg:>8,.2f}/mo {band} "
              f"{'OK' if ok else 'CHECK'}  non-zero={period != 0}")

    # New accounts exist with flow.
    print("\n-- New accounts (Entertainment, Personal Care) --")
    for path in (EXP_ENTERTAINMENT, EXP_PERSONAL_CARE):
        bal = _parse_money(book.get_balance(path, as_of_date=as_of))
        print(f"    {path}: lifetime flow ¥{bal:,.2f}  exists={bal != 0}")

    # Jobs present.
    print("\n-- Jobs --")
    try:
        jobs = book.list_jobs(compact=False)
        jlist = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
        print(f"  list_jobs: {len(jlist) if hasattr(jlist, '__len__') else jlist}")
        if isinstance(jlist, list):
            for j in jlist:
                print(f"    {j.get('name')} ({j.get('reference')})")
    except Exception as exc:  # noqa: BLE001
        print(f"  list_jobs unavailable: {exc}")

    # Scheduled transactions still enabled.
    print("\n-- Scheduled transactions (enabled) --")
    try:
        sx_enabled = book.list_scheduled_transactions(enabled_only=True)
        sx_list = (sx_enabled.get("scheduled_transactions", sx_enabled)
                   if isinstance(sx_enabled, dict) else sx_enabled)
        if isinstance(sx_list, list):
            print(f"  enabled SX count: {len(sx_list)}")
        else:
            import re as _re
            print(f"  enabled SX count (parsed): "
                  f"{len(_re.findall(r'[0-9a-f]{8,}', str(sx_enabled)))}")
    except Exception as exc:  # noqa: BLE001
        print(f"  list_scheduled_transactions unavailable: {exc}")
    try:
        upc = book.get_upcoming_transactions(days=45)
        print(f"  upcoming (45d): {upc if isinstance(upc, str) else len(upc)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  get_upcoming_transactions unavailable: {exc}")

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
        # Unreconciled splits across the whole book: not reconciled ('y'),
        # not voided ('v'), and carrying a non-zero value. A realistic
        # never-fully-reconciled book has many hundreds-to-thousands.
        from sqlalchemy import text as _text
        unrec = b.session.execute(_text(
            "SELECT COUNT(*) FROM splits "
            "WHERE reconcile_state NOT IN ('y','v') "
            "AND value_num != 0"
        )).scalar()
    print(f"  accounts:           {n_acct}")
    print(f"  transactions:       {n_txn}")
    print(f"  invoices+bills:     {n_inv}")
    print(f"  customers:          {n_cust}")
    print(f"  vendors:            {n_vend}")
    print(f"  prices:             {n_price}")
    print(f"  lots:               {lots}")
    print(f"  unreconciled splits: {unrec}")

    print("\n-- balance_sheet liabilities (full) --")
    for r in bs["liabilities"]["accounts"]:
        print(f"    {r}")
    print(f"  TOTAL liabilities: {bs['liabilities']['total']}")


# ── Continuation hooks (closed-loop policy layer) ───────────────
# Persona wiring for scripts/synthetic_book/continue_book.py; policy
# constants derived from the measured drift in
# specs/v1.5/DRIFT_ANALYSIS.md. Lin Wei is the DELIBERATE revolver:
# her 招商 card rides its 50%-utilization bound and her interest
# burden is load-bearing for the debt-payoff demos.

from continuation import CardPolicy, PersonaPolicy  # noqa: E402


def continuation_txns(through: date) -> list[dict]:
    """The deterministic streams continuation replays (spec §2.2).
    ``gen_credit_cards`` is deliberately absent — the policy layer
    derives payments and interest from the book itself; the HSBC HKD
    card's scripted arc stays prefix history ("leave 汇丰",
    DRIFT_ANALYSIS)."""
    global THROUGH
    THROUGH = through
    return (gen_recurring() + gen_daily_weekly() + gen_personal_life()
            + gen_contractor_income() + gen_volume())


def _add_price_rows(out_path: Path, pairs: list[tuple[str, date]]) -> int:
    """Real CNY-base quotes for (symbol, date) pairs, skipping any the
    book already has (the prefix's price table is never touched)."""
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    count = 0
    try:
        cny = book.default_currency
        comm_by = {c.mnemonic: c for c in book.commodities}
        seen: set[tuple[str, str]] = set()
        for p in book.prices:
            when = p.date.date() if hasattr(p.date, "date") else p.date
            seen.add((p.commodity.mnemonic, when.isoformat()))
        for sym, when in pairs:
            key = (sym, when.isoformat())
            if key in seen:
                continue
            seen.add(key)
            piecash.Price(
                commodity=comm_by[sym], currency=cny, date=when,
                value=real_price(sym, when), type="last",
                source="user:market-data",
            )
            count += 1
        book.save()
    finally:
        book.close()
    return count


def extend_prices(out_path: Path, since: date, through: date) -> int:
    """Continuation prices: 1st-of-month snapshots in (since, through]
    plus a fresh closing point per commodity."""
    global THROUGH
    THROUGH = through
    pairs: list[tuple[str, date]] = []
    for d in price_months():
        if d <= since:
            continue
        pairs += [(sym, d)
                  for sym in FOREIGN_CURRENCIES + SECURITY_MNEMONICS]
    for sym in FOREIGN_CURRENCIES:
        pairs.append((sym, min(through, MD.latest_fx_date(sym, "CNY"))))
    for sym in SECURITY_MNEMONICS:
        pairs.append((sym, min(through, MD.latest_security_date(sym))))
    return _add_price_rows(out_path, pairs)


def ensure_rate(out_path: Path, currency: str, when: date) -> None:
    """Real FX close for a cross-currency settlement date (no-op when
    a rate for that date is already on file)."""
    _add_price_rows(out_path, [(currency, when)])


def continuation_invest(out_path: Path, when: date, amount: Decimal,
                        source_path: str) -> None:
    """Policy-layer 沪深300 purchase: whole round lots at the real
    close, one lot per purchase, mirroring the DCA lot pattern. Source
    is 支票账户 (surplus sweep) or 储蓄账户 (pile rebalance)."""
    _add_price_rows(out_path, [("510300", when)])
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    try:
        cny = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        price = real_price("510300", when)
        units = _whole_units(amount, price, ROUND_LOT["510300"])
        cost = (units * price).quantize(D("0.01"))
        kind = "储蓄调仓" if source_path == SAVINGS else "结余投资"
        lot = piecash.Lot(
            title=f"510300 {kind} {when.isoformat()}",
            account=acct[CSI300],
            notes=f"{kind} {units} 份 @ ¥{price}", is_closed=0)
        inv_split = piecash.Split(account=acct[CSI300], value=cost,
                                  quantity=units)
        cash_split = piecash.Split(account=acct[source_path], value=-cost)
        piecash.Transaction(
            currency=cny, description=f"买入 510300（{kind}）",
            post_date=when, splits=[inv_split, cash_split])
        inv_split.lot = lot
        book.save()
    finally:
        book.close()


def _ensure_employee(book: GnuCashBook, name: str) -> bool:
    """Register the employee if missing (idempotent — the 陈宇工资
    schedule's owner, bookkeeper review §3)."""
    env = book.list_employees(compact=False, limit=250)
    rows = next((v for v in env.values() if isinstance(v, list)), [])
    if any(row.get("name") == name for row in rows):
        return False
    book.create_employee(name=name, currency="CNY")
    return True


def hsbc_payoff_repair(out_path: Path, cutoff: date,
                       through: date) -> list[str]:
    """Settle the dormant 汇丰 HKD card in narrative and stop using it.

    DEVIATES from DRIFT_ANALYSIS's "leave 汇丰" — deliberately, after
    the bookkeeper review: a carried balance with months of silence is
    exactly what the dashboard's carried-balance rule flags as missing
    entries (no interest ever posts). Paying it off retires the card
    into the classifier's ``dormant`` bucket honestly; the scripted
    2025 HKD activity stays as prefix history. Idempotent — a zero
    balance means a prior continuation already settled it."""
    book = GnuCashBook(str(out_path))
    owed_hkd = -Decimal(str(book.get_balance(HSBC_CARD)))
    if owed_hkd <= 0:
        return []
    when = cutoff + timedelta(days=8)
    if when > through:
        return []
    _add_price_rows(out_path, [("HKD", when)])
    rate = md_fx_cny("HKD", when)
    owed_cny = (owed_hkd * rate).quantize(D("0.01"))
    write_bulk(out_path, [{
        "description": "汇丰 港币卡 结清（销卡）",
        "date": when,
        "currency": "HKD",
        "splits": [
            (HSBC_CARD, owed_hkd),               # liability to zero (HKD)
            (CHECKING, -owed_hkd, -owed_cny),    # HKD value / CNY quantity
        ],
    }])
    return [f"汇丰 settled HK${owed_hkd} (¥{owed_cny}) on {when}"]


def continue_business(book: GnuCashBook, through: date,
                      since: date) -> dict:
    global THROUGH
    THROUGH = through
    counts = run_business(book, since=since)
    counts["employees"] = int(_ensure_employee(book, "陈宇"))
    return counts


def continue_investments(out_path: Path, through: date,
                         since: date) -> dict:
    global THROUGH
    THROUGH = through
    return run_investments(out_path, since=since)


def advance_schedules(out_path: Path, through: date):
    global THROUGH
    THROUGH = through
    return set_schedule_state(out_path)


POLICY = PersonaPolicy(
    key="lin-wei", currency="CNY",
    checking=CHECKING, savings=SAVINGS,
    buffer=D("40000"),                 # DRIFT_ANALYSIS: measured floor
    cards=(
        # 招商: the deliberate revolver — pays down to 50% of its
        # ¥80,000 limit on first contact (¥7,817, DRIFT ANALYSIS), then
        # minimum-plus payments hold it at the bound; interest accrues.
        CardPolicy(account=CMB_CARD, label="招商银行信用卡", kind="revolver",
                   close_day_default=25, bound_utilization=D("0.50"),
                   payment_plus=D("600"), accrue_interest=True,
                   interest_account=EXP_CC_INT),
        # 工商: the nobody-pays-this card. max_payment turns the ¥7.2k
        # arrears into a ¥1,500/month catch-up (DRIFT prescription);
        # once cleared the floor payment covers each cycle's charges.
        CardPolicy(account=ICBC_CARD, label="工商银行信用卡", kind="revolver",
                   close_day_default=20, payment_plus=D("1400"),
                   max_payment=D("1500"), accrue_interest=True,
                   interest_account=EXP_CC_INT),
        # 汇丰 HKD card: left alone by policy (DRIFT: static, scripted
        # prefix history).
    ),
    savings_share=D("0.60"),           # thin sweeps — she stays cash-tight
    invest_months=(3, 6, 9, 12),
    savings_target=D("120000"),
    rebalance_tranche=D("15000"),      # smaller tranches (ruled 2026-08-31)
    max_monthly_sweep=D("25000"),      # staging cap for the ¥193k pile
    min_sweep=D("500"),
    invest=continuation_invest,
    ensure_rate=ensure_rate,
    book_repairs=hsbc_payoff_repair,
    # Loans have no statement to reconcile against (review §1).
    no_reconcile=(MORTGAGE, AUTO_LOAN),
    desc_statement="{label} 还款",
    desc_repair_card="{label} 还款（清理累积欠款）",
    desc_sweep="转入储蓄账户（月度结余）",
    desc_repair_sweep="转入储蓄账户（结余归集）",
    desc_interest="{label} 利息",
)


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

    print("\nPhase 6b: personal-life spending "
          "(medical/gifts/charity/travel/entertainment/personal care)")
    n = write_bulk(out_path, gen_personal_life())
    print(f"  {n} personal-life transactions")

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

    print("\nScheduled-transaction state (kept enabled)")
    set_schedule_state(out_path)
    print("  scheduled transactions left enabled with realistic last_occur")

    verify(out_path, business)
    print("\nDone.")


def main() -> None:
    global THROUGH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="Output path (default: samples/lin-wei.generated.gnucash)")
    parser.add_argument(
        "--through", default=None, metavar="YYYY-MM-DD",
        help="Pin the end of the activity timeline for a deterministic run. "
             "Defaults to today (so the book always has recent activity).")
    args = parser.parse_args()
    if args.through:
        THROUGH = date.fromisoformat(args.through)
        if THROUGH < date(YEAR, 1, 1):
            raise SystemExit(
                f"--through {THROUGH} precedes the book start {YEAR}-01-01")
    out_path = Path(args.out).resolve()
    if out_path == PROTECTED.resolve():
        raise SystemExit(f"REFUSING to write to protected book: {PROTECTED}")
    print(f"Activity timeline runs 2025-01-01 → THROUGH={THROUGH}")
    build(out_path)


if __name__ == "__main__":
    main()
