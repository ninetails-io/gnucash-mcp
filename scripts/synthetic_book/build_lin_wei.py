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

The household (Shenzhen, a native zh_CN chart): 林微 runs a cross-border
e-commerce development business — every invoice, contract deposit, and business
expense is hers, and her quarterly filings are 增值税及附加 plus the 经营所得
income-tax prepayment. Her spouse 周子航 is on staff at 深圳市人民医院, a public
institution whose employees may not run a side business, so the salaried income
with social-insurance and 住房公积金 withholding is his. Everyday money moves on
a bank debit card (银行储蓄卡) that funds WeChat Pay and Alipay. The portfolio is
宁德时代 in 100-share lots plus two ETFs, every position a whole number of 一手
round lots, priced only from the offline market cache. The cat is 字节. Bills
land on their own days, 电费 follows Shenzhen's summer air-conditioning curve,
contracting income has irregular gaps, and the open receivables carry staggered
ages. The shape traces to the Gemini/bookkeeper domain audit (G1–G11) in
``specs/v1.5/testing/BOOKKEEPER_REVIEW_DEMO_GENERATORS.md`` §6 and the cold
audit ``specs/v1.5/testing/AUDIT_LIN_WEI_COLD_2026-09-17.md`` (round 2):
every tax figure is computed from the ledger (quarterly VAT with the
small-scale exemption and 专票 rule, cumulative 经营所得 prepayments, the
March 汇算清缴), payroll bases are fixed, the assistant 陈宇 is paid, every
big-company contract is an invoice, card statements are paid from the
running balance (interest only while a balance carries), the HKD card is
repaid by 购汇, and the calendar follows the lunar table, the exchange's
trading days and the banks' business days.

SAFETY: this script writes ONLY to ``samples/lin-wei.generated.gnucash`` (the
``--out`` path). It NEVER touches the bookkeeper-validated
``samples/lin-wei.gnucash``.

Usage:
    uv run python scripts/synthetic_book/build_lin_wei.py
    uv run python scripts/synthetic_book/build_lin_wei.py --out /tmp/lw.gnucash
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
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
D = Decimal

# Recurring-calendar anchors shared by the SX templates (Phase 4) and the
# instantiated history (Phase 5), so the forward schedule and the ledger
# agree on the day each bill lands.
#
# A small-scale VAT taxpayer files quarterly, within 15 days AFTER the
# quarter ends — January, April, July, October (audit L1: the old
# Mar/Jun/Sep/Dec filings do not exist). 经营所得 prepays on the same
# cadence; the annual 汇算清缴 settles the prior year by March 31.
TAX_VAT_DAY = 12                     # 增值税及附加 quarterly filing
TAX_PIT_DAY = 14                     # 经营所得 quarterly prepayment
TAX_SETTLE_DAY = 20                  # 经营所得 汇算清缴 (March)
TAX_VAT_START = f"{YEAR}-04-{TAX_VAT_DAY:02d}"
TAX_PIT_START = f"{YEAR}-04-{TAX_PIT_DAY:02d}"
TAX_SETTLE_START = f"{YEAR + 1}-03-{TAX_SETTLE_DAY:02d}"
AUTO_INS_RENEWAL = date(YEAR, 5, 20)   # policy anniversary (交强险+商业险)
AUTO_INS_ANNUAL = Decimal("5400")      # ≈ ¥950 交强险 + ¥4,450 商业险

# ── Tax engine parameters (all AMOUNTS are derived from the ledger) ──
#
# VAT, 小规模纳税人 (2023–2027 policy): 1% levy; a quarter whose 普票
# sales ≤ ¥300,000 is exempt — but 专票 sales never are, and the big
# mainland companies she contracts to require 专票 for their own input
# credit, so every 承包 invoice carries VAT regardless of the threshold.
# Cross-border services (the USD/EUR customers) are export-exempt.
# 附加税费 ride on the VAT actually due: 城建税 7% + 教育费附加 3% +
# 地方教育附加 2%, all halved for small-scale taxpayers.
VAT_RATE = D("0.01")
VAT_EXEMPT_QUARTERLY = D("300000")
VAT_SURCHARGE_RATE = (D("0.07") + D("0.03") + D("0.02")) * D("0.5")
# 经营所得 (sole-proprietor business income), five brackets on annual
# taxable income: (upper, rate, quick deduction). 费用扣除 ¥60,000/yr.
# 2023–2027: the portion of taxable income ≤ ¥2,000,000 is taxed at half.
BIZ_TAX_BRACKETS = [
    (D("30000"), D("0.05"), D("0")),
    (D("90000"), D("0.10"), D("1500")),
    (D("300000"), D("0.20"), D("10500")),
    (D("500000"), D("0.30"), D("40500")),
    (D("99999999999"), D("0.35"), D("65500")),
]
BIZ_TAX_ANNUAL_DEDUCTION = D("60000")
BIZ_TAX_HALVING_CAP = D("2000000")
BIZ_TAX_HALVING_YEARS = range(2023, 2028)

# ── Payroll bases (audit L4) ────────────────────────────────────
# 社保 and 公积金 contribute on a FIXED base the employer sets each July
# from the prior year's average wage — never on the month's overtime.
# Employee-side rates: 养老 8% + 医疗 2% + 失业 0.5% = 10.5%; 公积金 8%
# (an integer rate — 7.33% was never a legal figure).
SOCIAL_BASE = D("15000")
SOCIAL_INS_RATE = D("0.105")
HOUSING_FUND_RATE = D("0.08")
SOCIAL_INS_EMPLOYEE = (SOCIAL_BASE * SOCIAL_INS_RATE).quantize(D("0.01"))  # 1,575
HOUSING_FUND_EMPLOYEE = (SOCIAL_BASE * HOUSING_FUND_RATE).quantize(D("0.01"))  # 1,200

# 陈宇 — the business's registered part-time assistant (audit L6): a real
# wage on the 10th, employee 社保 withheld, employer 社保 as a business
# expense. Below the ¥5,000 起征点, so no 个税 is withheld.
ASSISTANT = "陈宇"
ASSISTANT_WAGE = D("3500")
ASSISTANT_EMPLOYER_SOCIAL_RATE = D("0.15")   # Shenzhen 单位 ≈ 15%

# ── Chinese calendar (deterministic tables, no lunar library) ───
# 春节 (lunar New Year's Day), 中秋, 端午 per year the builder can reach.
# A build past 2030 needs the table extended — better a loud KeyError
# than a 春节 in the wrong month.
LUNAR_NEW_YEAR = {
    2025: date(2025, 1, 29), 2026: date(2026, 2, 17), 2027: date(2027, 2, 6),
    2028: date(2028, 1, 26), 2029: date(2029, 2, 13), 2030: date(2030, 2, 3),
    2031: date(2031, 1, 23), 2032: date(2032, 2, 11),
}
MID_AUTUMN = {
    2025: date(2025, 10, 6), 2026: date(2026, 9, 25), 2027: date(2027, 9, 15),
    2028: date(2028, 10, 3), 2029: date(2029, 9, 22), 2030: date(2030, 9, 12),
    2031: date(2031, 10, 1), 2032: date(2032, 9, 19),
}
DRAGON_BOAT = {
    2025: date(2025, 5, 31), 2026: date(2026, 6, 19), 2027: date(2027, 6, 9),
    2028: date(2028, 5, 28), 2029: date(2029, 6, 16), 2030: date(2030, 6, 5),
    2031: date(2031, 6, 24), 2032: date(2032, 6, 12),
}
QINGMING = {
    2025: date(2025, 4, 4), 2026: date(2026, 4, 5), 2027: date(2027, 4, 5),
    2028: date(2028, 4, 4), 2029: date(2029, 4, 4), 2030: date(2030, 4, 5),
    2031: date(2031, 4, 5), 2032: date(2032, 4, 4),
}


def spring_festival(year: int) -> date:
    """The generators iterate only years in range, so a year past the
    table is a build horizon we never planned for — fail loud."""
    return LUNAR_NEW_YEAR[year]


def _span(start: date, days: int) -> set[date]:
    return {start + timedelta(days=i) for i in range(days)}


def cn_public_holidays(year: int) -> set[date]:
    """Statutory closures (banks and the exchanges): 元旦, 春节 除夕→初七,
    清明, 劳动节, 端午, 中秋, 国庆. The 2025/2026 windows match the State
    Council notices; later years follow the same shape. A year past the
    lunar table (a payment run that spills into the January after the
    horizon) keeps the fixed-date holidays only."""
    days: set[date] = {date(year, 1, 1)}
    days |= _span(date(year, 5, 1), 5)
    days |= _span(date(year, 10, 1), 7)
    if year in LUNAR_NEW_YEAR:
        days |= _span(LUNAR_NEW_YEAR[year] - timedelta(days=1), 8)  # 除夕..初七
        days |= _span(QINGMING[year], 3)
        days |= _span(DRAGON_BOAT[year], 3)
        days |= _span(MID_AUTUMN[year], 3)
    return days


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in cn_public_holidays(d.year)


def next_business_day(d: date) -> date:
    """Roll a bank posting off a weekend/holiday to the next business
    day (audit §6: card charges keep their calendar date; bank postings
    do not clear on a Sunday)."""
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def next_trading_day(d: date) -> date:
    """The exchange calendar is the public-holiday calendar plus
    weekends — an ETF 定投 cannot fill on 2025-05-01 (audit L9)."""
    return next_business_day(d)


def away_windows(year: int) -> list[tuple[date, date, str]]:
    """Days the household is OUT of Shenzhen (inclusive ranges): 回乡
    for 春节 (from 腊月二十七 through 初五), the 劳动节 short trip, the
    国庆 trip. Shenzhen daily spend is suppressed inside these windows
    and hometown/holiday spend takes its place (audit P5)."""
    ny = spring_festival(year)
    return [
        (ny - timedelta(days=3), ny + timedelta(days=5), "春节回乡"),
        (date(year, 5, 1), date(year, 5, 3), "劳动节"),
        (date(year, 10, 1), date(year, 10, 5), "国庆"),
    ]


def away_label(d: date) -> str | None:
    for start, end, label in away_windows(d.year):
        if start <= d <= end:
            return label
    return None

# The household's two earners. 林微 runs the cross-border e-commerce
# development business (every invoice, contract deposit, and business
# expense is hers). Her spouse 周子航 is on staff at 深圳市人民医院 — a
# public institution (事业单位) whose employees may not run a side
# business, which is why the salaried income with social-insurance and
# 住房公积金 withholding is his, never hers. (NOT 陈宇: that is the
# part-time assistant registered as an employee in the business module.)
SPOUSE = "周子航"
SALARY_DESC = f"{SPOUSE} 工资 (深圳市人民医院)"

# Day-of-month for the structured monthly flows (G7: nothing else in a real
# household lands on the 15th just because a salary does).
SALARY_DAY = 10
MORTGAGE_DAY = 18
AUTO_LOAN_DAY = 8
PET_VET_DAY = 10                   # 字节 quarterly 体检 (Feb/May/Aug/Nov)

# Shenzhen electricity is subtropical: the air conditioner runs from May
# into October and the summer bill is 2–4× the winter base. Multiplier per
# calendar month on ELECTRIC_BASE, before noise.
ELECTRIC_BASE = Decimal("150")
ELECTRIC_SEASON = {
    1: "1.15", 2: "1.00", 3: "0.90", 4: "1.05", 5: "1.50", 6: "2.40",
    7: "3.20", 8: "3.40", 9: "2.70", 10: "1.80", 11: "1.15", 12: "1.00",
}

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

# Her everyday bank account is a debit card (借记卡) — the mainland has no
# personal "checking"; the WeChat/Alipay rails below sit on top of it.
CHECKING = "资产:流动资产:银行储蓄卡"
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

# A-shares trade in 一手 = 100-share lots (ETF units likewise), and a
# ¥1,400+ Moutai lot is ¥140k+ — not this household's portfolio. The
# single-stock position is CATL in round lots; the core exposure is ETFs.
CATL = "资产:投资:证券账户:宁德时代"
CSI300 = "资产:投资:证券账户:沪深300ETF"
CHINEXT = "资产:投资:证券账户:创业板ETF"

ICBC_CARD = "负债:信用卡:工商银行信用卡"
CMB_CARD = "负债:信用卡:招商银行信用卡"
HSBC_CARD = "负债:信用卡:汇丰港币信用卡"
MORTGAGE = "负债:贷款:房屋贷款"
AUTO_LOAN = "负债:贷款:汽车贷款"
AP = "负债:应付账款"
# USD payables subledger — same per-currency pattern as AR_USD/AR_EUR.
# Created lazily by run_business (the frozen prefix predates it).
AP_USD = "负债:应付账款（美元）"

OPENING = "所有者权益:期初余额"

SALARY = "收入:工资"  # the spouse's payslip (see SPOUSE above)
CONTRACTOR = "收入:承包收入"
LLC_REVENUE = "收入:个体经营收入"
DIVIDENDS = "收入:投资收益:股息"
CAPITAL_GAINS = "收入:投资收益:资本利得"
HOUSING_FUND_INCOME = "收入:住房公积金收入"
REIMBURSEMENTS = "收入:报销收入"
INTEREST_INCOME = "收入:利息收入"

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
# 营业税 was abolished 2016-05-01 (营改增). A sole proprietor today files
# VAT + surcharges (小规模纳税人, quarterly) and prepays 经营所得 income
# tax quarterly — two accounts, two filings.
EXP_VAT = "支出:税费:增值税及附加"
EXP_BIZ_INCOME_TAX = "支出:税费:个人经营所得税"
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
EXP_BIZ_WAGES = "支出:经营支出:工资"
EXP_BIZ_SOCIAL = "支出:经营支出:社保（单位）"
EXP_OFFICE_EQUIP = "支出:经营支出:办公设备"
EXP_OFFICE_SUPPLIES = "支出:经营支出:办公用品"
EXP_BIZ_SERVICES = "支出:经营支出:代理记账"
EXP_BIZ_TRAVEL = "支出:经营支出:差旅"
EXP_BIZ = "支出:经营支出"
EXP_TRADING_FEES = "支出:交易费用"
EXP_TRANSPORT = "支出:交通"
EXP_MISC = "支出:杂项"
EXP_MEDICAL = "支出:医疗"
EXP_ENTERTAINMENT = "支出:娱乐"
EXP_PERSONAL_CARE = "支出:个人护理"


# ── Phase 1: Commodities & prices ───────────────────────────────

# Securities: (mnemonic, fullname, namespace, fraction)
SECURITIES = [
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
# Every quantity is a whole number of 一手 (100-share / 100-unit lots).
INVESTMENT_TRADES = [
    (6, 16, "sell", "300750", D("100")),   # one lot out of the opening two,
                                           # below basis → a realized LOSS
    (9, 12, "buy", "300750", D("100")),    # bought a lot back on strength
    (11, 18, "buy", "510300", D("3000")),
    (12, 15, "sell", "159915", D("2000")),
]

# Cross-currency documents. Every USD/EUR retainer invoice follows the
# same calendar EVERY year the timeline reaches (audit P4: the foreign
# customers went silent for the first half of 2026): Pacific Trade opens
# on the 5th and pays the 5th of the following month; Handelskontor
# München opens the 8th and pays the 8th of the next month. Payments
# roll to a business day. The price layer lays a real quote on each
# open/pay date, so post_invoice/pay_invoice never trip the freshness
# guard and every paid invoice books a real realized FX gain/loss.
PACIFIC_PLAN = [(3, "3000"), (6, "3000"), (9, "4500"), (12, "3000")]
MUNICH_PLAN = [(4, "2500"), (8, "3800"), (11, "2500")]


def years_in_range() -> list[int]:
    return list(range(YEAR, THROUGH.year + 1))


def foreign_invoice_plans() -> list[dict]:
    """Every cross-currency invoice OPENED on or before THROUGH, with
    its scheduled payment date (which may fall past THROUGH — then the
    invoice stays open, the way a living A/R ledger reads)."""
    plans: list[dict] = []
    for yy in years_in_range():
        for m, amt in PACIFIC_PLAN:
            open_d = date(yy, m, 5)
            pay_y, pay_m = (yy + 1, 1) if m == 12 else (yy, m + 1)
            plans.append({
                "customer": "pacific", "currency": "USD", "amount": amt,
                "open": open_d,
                "pay": next_business_day(date(pay_y, pay_m, 5)),
                "desc": f"{open_d.strftime('%B %Y')} cross-border app "
                        f"engagement",
                "job": m == 9,
            })
        for m, amt in MUNICH_PLAN:
            open_d = date(yy, m, 8)
            plans.append({
                "customer": "munich", "currency": "EUR", "amount": amt,
                "open": open_d,
                "pay": next_business_day(date(yy, m + 1, 8)),
                "desc": f"{open_d.strftime('%B %Y')} Softwareentwicklung",
                "job": m == 11,
            })
    return [p for p in plans if p["open"] <= THROUGH]

# JetBrains: the USD bill is RE-DATED at build time to the 1st of THROUGH's
# month (a date that carries a monthly USD/CNY snapshot), so it no longer
# needs a fixed FX date here. The open cross-currency invoices (Pacific
# USD, Munich EUR) post on THROUGH-relative dates instead — see
# OPEN_DOC_AGE / open_document_fx_dates(), which lays a real quote on each
# of those post dates for the 7-day FX freshness guard.

# Open receivables, as days before THROUGH the document posts. With Net 30
# terms that leaves three invoices overdue by 1, 12 and 33 days and two
# posted-but-current — staggered ages, the way a living A/R ledger reads
# (G10: a single anchor made every overdue invoice the same age).
OPEN_DOC_AGE = {
    "sz_milestone": 31,     # 深圳跨境电商 平台改版里程碑 — 1 day overdue
    "sz_maint": 10,         # 深圳跨境电商 运维支持 — due in 20 days
    "pacific": 63,          # Pacific Trade retainer (USD) — 33 days overdue
    "munich_p2": 42,        # München ERP Phase 2 (EUR) — 12 days overdue
    "munich_wartung": 3,    # München Wartung (EUR) — due in 27 days
}


def open_document_date(key: str) -> date:
    """Post date of a THROUGH-relative open document."""
    return THROUGH - timedelta(days=OPEN_DOC_AGE[key])


def open_document_fx_dates() -> list[tuple[str, date]]:
    """(foreign, post_date) for every cross-currency open document, so a
    real quote sits ON its post date (post_invoice's freshness guard is
    7 days; a 1st-of-month snapshot is not enough for these)."""
    return [
        ("USD", open_document_date("pacific")),
        ("EUR", open_document_date("munich_p2")),
        ("EUR", open_document_date("munich_wartung")),
    ]

# HSBC HKD card — the FOREIGN-currency liability regression case. A few
# Hong Kong trips a year (the same three anchors every year, amounts
# noised), each charge booked at the real HKD/CNY rate; every statement
# is then repaid IN FULL the following month by 购汇 from 银行储蓄卡
# (run_hsbc_statements — audit P2: HK$6,460 carried for eleven months
# with no interest was the old shape). Categories per audit P3.
HSBC_CLOSE_DAY = 8
HSBC_TRIPS = [  # (month, day, description, HKD, expense account)
    (3, 14, "香港 海港城购物", D("3200"), EXP_CLOTHING),
    (7, 8, "香港 莎莎化妆品", D("1800"), EXP_PERSONAL_CARE),
    (10, 20, "香港 苹果旗舰店配件", D("2460"), EXP_MISC),
]


def hsbc_charges() -> list[tuple[date, str, Decimal, str]]:
    """(date, description, HKD amount, expense account) for every HK
    charge on or before THROUGH. 2025 keeps the original three amounts;
    later years noise them ±15% on a per-year seeded stream."""
    out: list[tuple[date, str, Decimal, str]] = []
    for yy in years_in_range():
        rng = random.Random(f"{SEED}:hsbc:{yy}")
        for m, day, desc, hkd, acct in HSBC_TRIPS:
            dt = date(yy, m, day)
            if dt > THROUGH:
                continue
            amt = hkd if yy == YEAR else (
                hkd * D(str(round(rng.uniform(0.85, 1.15), 3)))
            ).quantize(D("1"))
            out.append((dt, desc, amt, acct))
    return out

# Per-symbol display quantization for the *booked* CNY value of a price
# record. FX to four decimals (GnuCash convention), per-share securities
# to one, ETF/fund units to two.
PRICE_QUANT = {
    "USD": D("0.0001"), "EUR": D("0.0001"), "HKD": D("0.0001"),
    "300750": D("0.1"),
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
            wanted[sym].add(THROUGH)  # §5: closing point AT the horizon
        for sym in SECURITY_MNEMONICS:
            wanted[sym].add(THROUGH)

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


def dca_dates() -> list[date]:
    """The monthly 定投 fill dates: the first TRADING day of each month
    through THROUGH (audit L9 — 24 of 42 fills sat on closed days)."""
    out: list[date] = []
    for yy, m in iter_months():
        d = next_trading_day(date(yy, m, 1))
        if d <= THROUGH:
            out.append(d)
    return out


def trade_date(m: int, day: int) -> date:
    """A discretionary trade's fill date, rolled to a trading day."""
    return next_trading_day(date(YEAR, m, day))


def security_price_dates() -> list[tuple[str, date]]:
    """(mnemonic, date) pairs for every investment buy/sell + DCA date."""
    pairs: list[tuple[str, date]] = []
    for d in dca_dates():
        for sym in ("510300", "159915"):
            pairs.append((sym, d))
    for m, day, _action, sym, _shares in INVESTMENT_TRADES:
        pairs.append((sym, trade_date(m, day)))
    return pairs


def fx_price_dates() -> list[tuple[str, date]]:
    """(foreign, date) pairs for every cross-currency post/pay + HKD
    charge date. HSBC statement payments add their own rows at build
    time (run_hsbc_statements) because their dates come from the
    book's statement cycle."""
    pairs: list[tuple[str, date]] = []
    for plan in foreign_invoice_plans():
        pairs.append((plan["currency"], plan["open"]))
        if plan["pay"] <= THROUGH:
            pairs.append((plan["currency"], plan["pay"]))
    for dt, _desc, _amt, _acct in hsbc_charges():
        pairs.append(("HKD", dt))
    pairs.extend(open_document_fx_dates())
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
    ("银行储蓄卡", "BANK", "资产:流动资产", "CNY", "CURRENCY", False),
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
    ("利息收入", "INCOME", "收入", "CNY", "CURRENCY", False),
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
    ("增值税及附加", "EXPENSE", "支出:税费", "CNY", "CURRENCY", False),
    ("个人经营所得税", "EXPENSE", "支出:税费", "CNY", "CURRENCY", False),
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
    ("工资", "EXPENSE", "支出:经营支出", "CNY", "CURRENCY", False),
    ("社保（单位）", "EXPENSE", "支出:经营支出", "CNY", "CURRENCY", False),
    ("办公设备", "EXPENSE", "支出:经营支出", "CNY", "CURRENCY", False),
    ("办公用品", "EXPENSE", "支出:经营支出", "CNY", "CURRENCY", False),
    ("代理记账", "EXPENSE", "支出:经营支出", "CNY", "CURRENCY", False),
    ("差旅", "EXPENSE", "支出:经营支出", "CNY", "CURRENCY", False),
    ("交易费用", "EXPENSE", "支出", "CNY", "CURRENCY", False),
    ("交通", "EXPENSE", "支出", "CNY", "CURRENCY", False),
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
    book.set_account_slot(HSBC_CARD, "statement_close_day",
                          str(HSBC_CLOSE_DAY))
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

# (account, mnemonic, units, lot_title). Units are whole 一手 lots; the
# cost basis is units × the cache close on the opening date, so the
# 2025-01-01 price snapshot and the lot's basis are the same number.
OPENING_LOTS = [
    (CATL, "300750", D("200"), "宁德时代 期初持仓"),
    (CSI300, "510300", D("5000"), "沪深300 core position"),
    (CHINEXT, "159915", D("8000"), "创业板 growth position"),
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

        # Investment opening lots: buy each holding from equity at cost —
        # the real close on the opening date, never an invented basis.
        for path, sym, units, title in OPENING_LOTS:
            inv_acct = acct[path]
            price = real_price(sym, jan1)
            cost = (units * price).quantize(D("0.01"))
            lot = piecash.Lot(
                title=title, account=inv_acct,
                notes=f"期初持仓 {units} 份 @ ¥{price} (opening position)",
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
                notes=t.get("notes"),
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
    "美宜佳": EXP_GROCERIES,
    "7-11便利店": EXP_GROCERIES,
    "天虹微喔": EXP_GROCERIES,
    "山姆会员店": EXP_GROCERIES,
    # Transport: ride-hail, bike-share and the metro all book to 交通
    # (audit P11 — they used to sit under 汽车:停车费).
    "滴滴出行": EXP_TRANSPORT,
    "共享单车": EXP_TRANSPORT,
    "深圳地铁": EXP_TRANSPORT,
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


# ── Monthly bill calendar (shared by Phase 4 templates and Phase 5) ──
#
# (sx_name, description, source, expense, template_amount, day). The
# template amount is the flat figure the forward schedule shows; the
# instantiated history varies the utility and pet-food lines through
# ``_variable_bill_amount`` below. Days are scattered the way autopay
# dates really fall — nothing shares the salary's 10th or the mortgage's
# 18th, and the utilities cluster in the last week like a Shenzhen
# 供电/水务/燃气 billing cycle.
MONTHLY_BILLS = [
    ("物业管理费", "物业管理费", CHECKING, EXP_PROP_MGMT, D("850"), 5),
    ("电费", "电费", WECHAT, EXP_ELECTRIC, D("180"), 25),
    ("水费", "水费", WECHAT, EXP_WATER, D("80"), 23),
    ("天然气", "天然气", WECHAT, EXP_GAS, D("60"), 27),
    ("宽带费", "中国电信宽带", WECHAT, EXP_INTERNET, D("199"), 8),
    ("话费", "中国移动话费", WECHAT, EXP_PHONE, D("128"), 12),
    ("视频会员", "爱奇艺+Bilibili会员", WECHAT, EXP_STREAMING, D("45"), 10),
    ("阿里云", "阿里云", CMB_CARD, EXP_CLOUD, D("350"), 3),
    ("联合办公", "优客工场 联合办公", CMB_CARD, EXP_COWORKING, D("1500"), 1),
    ("宠物口粮", "字节口粮", ALIPAY, EXP_PET_FOOD, D("280"), 20),
    ("停车月卡", "停车月卡", WECHAT, EXP_PARKING, D("800"), 28),
]


def _variable_bill_amount(name: str, base: Decimal, month: int,
                          rng: random.Random) -> Decimal:
    """The month's actual charge for a metered or variable bill.

    电费 rides ELECTRIC_SEASON (summer AC 2–4× winter) with ±12% noise;
    水费 runs ~20% higher through the hot months; 燃气费 ~30% higher in
    winter (hot water, hotpot season); 字节's food varies with what was on
    offer. Fixed tariffs (broadband, phone, memberships, the parking pass,
    property management) return ``base`` unchanged and draw nothing, so
    each bill's RNG stream is a pure function of (bill, month).
    """
    if name == "电费":
        factor = (D(ELECTRIC_SEASON[month])
                  * D(str(round(rng.uniform(0.88, 1.12), 4))))
        base = ELECTRIC_BASE
    elif name == "水费":
        factor = ((D("1.20") if month in (6, 7, 8, 9) else D("1"))
                  * D(str(round(rng.uniform(0.85, 1.15), 4))))
    elif name == "天然气":
        factor = ((D("1.30") if month in (12, 1, 2) else D("1"))
                  * D(str(round(rng.uniform(0.85, 1.15), 4))))
    elif name == "宠物口粮":
        factor = D(str(round(rng.uniform(0.86, 1.18), 4)))
    else:
        return base
    return (base * factor).quantize(D("0.01"))


# ── Phase 4: Scheduled-transaction templates ────────────────────

def create_scheduled_templates(book: GnuCashBook) -> int:
    """Create the SX templates so scheduled-transaction tools have data.

    These are left ENABLED (see set_schedule_state) with a realistic
    ``last_occur`` so the scheduled-transaction demo surface stays populated
    (the prior hand-built book had 13 active). The actual recurring activity
    is generated directly in Phase 5 for speed and amortization-split
    fidelity; these templates are the forward-looking schedule.
    """
    count = 0

    def sx(name, description, splits, frequency, start_date):
        nonlocal count
        book.create_scheduled_transaction(
            name=name, description=description, splits=splits,
            start_date=start_date, frequency=frequency, enabled=True,
        )
        count += 1

    # Monthly salary — the spouse's 事业单位 payslip (gross, withholdings,
    # net to the debit card), paid once a month. The template shows a
    # base month: fixed-base 社保/公积金, first-bracket 个税.
    base_tax = (SOCIAL_BASE - SOCIAL_INS_EMPLOYEE - HOUSING_FUND_EMPLOYEE
                - IIT_MONTHLY_DEDUCTION) * D("0.03")
    base_net = (SOCIAL_BASE - SOCIAL_INS_EMPLOYEE - HOUSING_FUND_EMPLOYEE
                - base_tax)
    sx(f"{SPOUSE}工资", SALARY_DESC, [
        {"account": SALARY, "amount": f"-{SOCIAL_BASE}"},
        {"account": CHECKING, "amount": str(base_net)},
        {"account": EXP_INCOME_TAX, "amount": str(base_tax)},
        {"account": EXP_SOCIAL, "amount": str(SOCIAL_INS_EMPLOYEE)},
        {"account": HOUSING_FUND, "amount": str(HOUSING_FUND_EMPLOYEE)},
    ], "monthly", start_date=f"{YEAR}-01-{SALARY_DAY:02d}")

    # The business's part-time assistant: wage + 社保 remitted on the
    # 10th (audit L6 — a registered employee with no payroll was a
    # phantom).
    emp_social = (ASSISTANT_WAGE * SOCIAL_INS_RATE).quantize(D("0.01"))
    er_social = (ASSISTANT_WAGE * ASSISTANT_EMPLOYER_SOCIAL_RATE
                 ).quantize(D("0.01"))
    sx(f"{ASSISTANT}工资", f"{ASSISTANT} 工资 + 社保缴纳", [
        {"account": CHECKING,
         "amount": f"-{ASSISTANT_WAGE + er_social}"},
        {"account": EXP_BIZ_WAGES, "amount": str(ASSISTANT_WAGE)},
        {"account": EXP_BIZ_SOCIAL, "amount": str(er_social)},
    ], "monthly", start_date=f"{YEAR}-01-{SALARY_DAY:02d}")

    sx("房贷还款", "房贷还款", [
        {"account": CHECKING, "amount": "-14800"},
        {"account": EXP_MORTGAGE_INT, "amount": "8983"},
        {"account": MORTGAGE, "amount": "5817"},
    ], "monthly", start_date=f"{YEAR}-01-{MORTGAGE_DAY:02d}")

    # The car loan retires on its own; the schedule ends with it.
    book.create_scheduled_transaction(
        name="车贷还款", description="车贷还款", splits=[
            {"account": CHECKING, "amount": "-2400"},
            {"account": EXP_AUTO_INT, "amount": "490"},
            {"account": AUTO_LOAN, "amount": "1910"},
        ], start_date=f"{YEAR}-01-{AUTO_LOAN_DAY:02d}", frequency="monthly",
        end_date=auto_loan_payoff_date().isoformat(), enabled=True,
    )
    count += 1

    # One calendar for the templates and the ledger: each bill's schedule
    # starts on the same day-of-month its history lands on.
    for name, desc, src, dst, amt, day in MONTHLY_BILLS:
        sx(name, desc, [
            {"account": src, "amount": f"-{amt}"},
            {"account": dst, "amount": str(amt)},
        ], "monthly", start_date=f"{YEAR}-01-{day:02d}")

    # Quarterly — the two filings a sole proprietor actually makes, in
    # the 15-day window AFTER each quarter closes (Jan/Apr/Jul/Oct).
    # The template amounts are placeholders; the ledger's instances are
    # computed from the book by run_taxes.
    sx("增值税及附加 季度申报", "增值税及附加 季度申报缴款", [
        {"account": CHECKING, "amount": "-700"},
        {"account": EXP_VAT, "amount": "700"},
    ], "quarterly", start_date=TAX_VAT_START)
    sx("经营所得 季度预缴", "经营所得个人所得税 季度预缴", [
        {"account": CHECKING, "amount": "-10000"},
        {"account": EXP_BIZ_INCOME_TAX, "amount": "10000"},
    ], "quarterly", start_date=TAX_PIT_START)
    # Annual 汇算清缴 of the prior year's 经营所得, by March 31.
    sx("经营所得 汇算清缴", "经营所得个人所得税 年度汇算清缴", [
        {"account": CHECKING, "amount": "-3000"},
        {"account": EXP_BIZ_INCOME_TAX, "amount": "3000"},
    ], "yearly", start_date=TAX_SETTLE_START)
    # 字节's vaccination + check-up is annual (audit P12).
    sx("宠物体检", "字节年度疫苗体检", [
        {"account": ALIPAY, "amount": "-800"},
        {"account": EXP_PET_VET, "amount": "800"},
    ], "yearly", start_date=f"{YEAR}-03-08")

    # Yearly. 红包 go out on 除夕 — the schedule anchors on 2025's and
    # the ledger follows the lunar table each year.
    sx("春节红包", "春节红包", [
        {"account": CHECKING, "amount": "-6000"},
        {"account": EXP_GIFTS, "amount": "6000"},
    ], "yearly",
       start_date=(spring_festival(YEAR) - timedelta(days=1)).isoformat())
    # 交强险 + 商业险 renew together once a year (never monthly).
    sx("车险", "平安车险 交强险+商业险 (年缴)", [
        {"account": CHECKING, "amount": f"-{AUTO_INS_ANNUAL}"},
        {"account": EXP_AUTO_INS, "amount": str(AUTO_INS_ANNUAL)},
    ], "yearly", start_date=AUTO_INS_RENEWAL.isoformat())

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


AUTO_LOAN_OPENING = D("120000")


def auto_loan_schedule() -> list[tuple[Decimal, Decimal]]:
    """(interest, principal) for every 车贷 instalment from 2025-01 until
    the ¥120,000 balance is retired — the last instalment is whatever
    principal remains. Independent of THROUGH, so a 2030 build stops
    paying the moment the loan is gone instead of driving the liability
    negative."""
    out: list[tuple[Decimal, Decimal]] = []
    remaining = AUTO_LOAN_OPENING
    elapsed = 0
    while remaining > 0:
        m_int, m_pri = _amort(D("490"), D("1910"), D("2400"), D("8"), elapsed)
        m_pri = min(m_pri, remaining)
        out.append((m_int, m_pri))
        remaining -= m_pri
        elapsed += 1
    return out


def auto_loan_payoff_date() -> date:
    n = len(auto_loan_schedule())
    yy, mm = YEAR + (n - 1) // 12, (n - 1) % 12 + 1
    return next_business_day(_clamp_day(yy, mm, AUTO_LOAN_DAY))


# ── Payroll withholding (rate-based, cumulative IIT) ─────────────

# 社保/公积金 rates and the fixed base live in the configuration block
# at the top of the file (SOCIAL_BASE, SOCIAL_INS_RATE,
# HOUSING_FUND_RATE). Withholding follows gross only through 个税.
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

    # Cumulative-withholding state, reset each calendar year. Tracks YTD
    # taxable income and YTD tax already withheld so each month's IIT is the
    # incremental amount (累计预扣法) — it rises as the year progresses.
    cum_taxable_by_year: dict[int, Decimal] = {}
    cum_tax_by_year: dict[int, Decimal] = {}

    # 陈宇's payroll: individual 社保 withheld from the wage, employer 社保
    # on top, both remitted with the wage on payday.
    emp_social = (ASSISTANT_WAGE * SOCIAL_INS_RATE).quantize(D("0.01"))
    er_social = (ASSISTANT_WAGE * ASSISTANT_EMPLOYER_SOCIAL_RATE
                 ).quantize(D("0.01"))

    # 住房公积金 running balance, for the annual 结息 on June 30 (audit P7).
    hf_balance = dict(OPENING_BALANCES)[HOUSING_FUND]
    auto_schedule = auto_loan_schedule()

    for elapsed, (yy, m) in enumerate(months):
        # Salary on the 10th (a bank posting — rolls off a weekend or
        # holiday to the next business day), with overtime every 3rd
        # month. 社保/公积金 sit on the FIXED base; only gross, 个税 and
        # net move with overtime (audit L4).
        pay_day = next_business_day(_clamp_day(yy, m, SALARY_DAY))
        if _on_or_before_through(pay_day):
            overtime = D("0")
            if m % 3 == 0:
                overtime = D(str(rng.randint(500, 1500)))
            gross = SOCIAL_BASE + overtime
            social = SOCIAL_INS_EMPLOYEE
            housing = HOUSING_FUND_EMPLOYEE

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
                "description": SALARY_DESC + (
                    f" (含加班 ¥{overtime})" if overtime else ""),
                "date": pay_day,
                "splits": [
                    (SALARY, -gross),
                    (CHECKING, net),
                    (EXP_INCOME_TAX, income_tax),
                    (EXP_SOCIAL, social),
                    (HOUSING_FUND, housing),
                ],
            })
            # Housing fund employer match (forced savings) — same fixed base.
            txns.append({
                "description": f"住房公积金 单位缴存 ({SPOUSE})",
                "date": pay_day,
                "splits": [
                    (HOUSING_FUND, housing),
                    (HOUSING_FUND_INCOME, -housing),
                ],
            })
            hf_balance += housing * 2

            # The assistant's wage + 社保, same payday.
            txns.append({
                "description": f"{ASSISTANT} 工资 + 社保缴纳",
                "date": pay_day,
                "notes": (f"工资 ¥{ASSISTANT_WAGE}，代扣个人社保 ¥{emp_social}，"
                          f"单位社保 ¥{er_social}"),
                "splits": [
                    (CHECKING, -(ASSISTANT_WAGE + er_social)),
                    (EXP_BIZ_WAGES, ASSISTANT_WAGE),
                    (EXP_BIZ_SOCIAL, er_social),
                ],
            })

        # 公积金 annual 结息 (June 30, 1.5% on the balance).
        if m == 6:
            d_int = date(yy, 6, 30)
            if _on_or_before_through(d_int) and d_int >= date(YEAR, 1, 1):
                interest = (hf_balance * D("0.015")).quantize(D("0.01"))
                txns.append({
                    "description": "住房公积金 年度结息",
                    "date": d_int,
                    "splits": [(HOUSING_FUND, interest),
                               (INTEREST_INCOME, -interest)],
                })
                hf_balance += interest

        # Mortgage on the 18th, auto loan on the 8th (bank autopay — rolls
        # to a business day). Level payment, interest/principal mix
        # shifts each month as the loan amortizes (across multiple years).
        d5 = next_business_day(_clamp_day(yy, m, MORTGAGE_DAY))
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
        d5 = next_business_day(_clamp_day(yy, m, AUTO_LOAN_DAY))
        if _on_or_before_through(d5) and elapsed < len(auto_schedule):
            a_int, a_pri = auto_schedule[elapsed]
            txns.append({
                "description": "车贷还款",
                "date": d5,
                "splits": [
                    (CHECKING, -(a_int + a_pri)),
                    (EXP_AUTO_INT, a_int),
                    (AUTO_LOAN, a_pri),
                ],
            })

    # Monthly bills, on the calendar the templates share. Each variable
    # bill draws from its OWN seeded stream, one draw per month whether or
    # not the month is written, so a (bill, month) amount is the same on a
    # fresh build and on a continuation replay. Bank-debited bills roll to
    # a business day; wallet and card autopays keep their calendar date.
    for name, desc, src, dst, amt, day in MONTHLY_BILLS:
        rng_bill = random.Random(f"{SEED}:bill:{name}")
        for yy, m in months:
            amount = _variable_bill_amount(name, amt, m, rng_bill)
            d = _clamp_day(yy, m, day)
            if src == CHECKING:
                d = next_business_day(d)
            if not _on_or_before_through(d):
                continue
            txns.append({
                "description": desc,
                "date": d,
                "splits": [(src, -amount), (dst, amount)],
            })

    # 字节's annual vaccination + check-up (March 8; what the vet finds
    # varies). The quarterly filings live in run_taxes — they are
    # computed from the ledger, not drawn.
    rng_pet = random.Random(f"{SEED}:pet")
    for yy in sorted({y for y, _ in months}):
        vet = _spend(rng_pet, 650, 950)
        d = date(yy, 3, 8)
        if date(YEAR, 1, 1) <= d <= THROUGH:
            txns.append({
                "description": "字节年度疫苗体检",
                "date": d,
                "splits": [(ALIPAY, -vet), (EXP_PET_VET, vet)],
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

    # Yearly — Spring Festival red envelopes on 除夕 (lunar table), and
    # the auto-insurance renewal (交强险+商业险, one annual premium on the
    # policy anniversary), each year in range. The car is 免检 for its
    # first six years, so there is no 年检 fee (audit P12).
    years = sorted({yy for yy, _ in months})
    for yy in years:
        d = spring_festival(yy) - timedelta(days=1)
        if date(YEAR, 1, 1) <= d <= THROUGH:
            txns.append({
                "description": "春节红包",
                "date": d,
                "notes": f"{yy} 除夕 长辈+晚辈红包",
                "splits": [(CHECKING, D("-6000")), (EXP_GIFTS, D("6000"))],
            })
        d = next_business_day(
            date(yy, AUTO_INS_RENEWAL.month, AUTO_INS_RENEWAL.day))
        if date(YEAR, 1, 1) <= d <= THROUGH:
            txns.append({
                "description": "平安车险 交强险+商业险 (年缴)",
                "date": d,
                "splits": [(CHECKING, -AUTO_INS_ANNUAL),
                           (EXP_AUTO_INS, AUTO_INS_ANNUAL)],
            })
    return txns


# ── Phase 6: Daily/weekly patterns + seasonal one-offs ──────────

# Per-weekday habit probabilities, Monday → Sunday. A week has a SHAPE,
# not a timetable (audit A1: coffee 99/102/101/99/99/11/16, delivery on
# Tue/Thu/Sat only, charging always on Wednesday): coffee is a workday
# thing that spills into weekends, delivery peaks on tired weeknights and
# lazy weekends, the car charges whenever the battery is low, the big
# grocery run is a weekend errand.
COFFEE_P = [0.82, 0.85, 0.85, 0.85, 0.80, 0.35, 0.25]
DELIVERY_P = [0.40, 0.45, 0.40, 0.45, 0.50, 0.55, 0.50]
CONVENIENCE_P = [0.70, 0.70, 0.70, 0.70, 0.70, 0.55, 0.50]
CHARGING_P = [0.14, 0.14, 0.14, 0.14, 0.16, 0.14, 0.14]
HEMA_P = [0.05, 0.05, 0.05, 0.05, 0.10, 0.45, 0.50]

# Shenzhen convenience chains (audit P9 — 便利蜂 has no Shenzhen footprint).
CONVENIENCE_VENDORS = ["美宜佳", "7-11便利店", "天虹微喔"]

# What the household spends on while AWAY (inside an away window the
# Shenzhen habits above are suppressed): the hometown week, the 劳动节
# and 国庆 trips. (description, expense, low, high).
HOLIDAY_SPEND = {
    "春节回乡": [
        ("老家 餐馆 家宴", EXP_DINING, 120, 380),
        ("走亲访友 礼品", EXP_GIFTS, 150, 400),
        ("老家 超市 年货", EXP_GROCERIES, 60, 220),
        ("县城 打车", EXP_TRANSPORT, 15, 45),
    ],
    "劳动节": [
        ("景区门票", EXP_TRAVEL, 80, 200),
        ("民宿 住宿", EXP_TRAVEL, 300, 600),
        ("当地餐馆", EXP_DINING, 80, 260),
    ],
    "国庆": [
        ("景区门票", EXP_TRAVEL, 80, 220),
        ("酒店 住宿", EXP_TRAVEL, 350, 700),
        ("当地餐馆", EXP_DINING, 90, 300),
        ("特产 伴手礼", EXP_GIFTS, 100, 300),
    ],
}


def seasonal_events(yy: int) -> list[tuple[date, str, str, str, Decimal]]:
    """The household's holiday calendar for one year, driven by the lunar
    table: 年货 the week before 春节, the 回乡 train BEFORE 春节 and the
    return after 初五, 清明/劳动节/国庆 outings, 月饼 ahead of 中秋, the
    year-end donation, and the shopping-festival habits."""
    ny = spring_festival(yy)
    return [
        (ny - timedelta(days=9), "年货采购", ALIPAY, EXP_GROCERIES, D("2500")),
        (ny - timedelta(days=3), "春节回乡 高铁 (深圳→老家)", CHECKING,
         EXP_TRAVEL, D("1260")),
        (ny + timedelta(days=5), "返深 高铁 (老家→深圳)", CHECKING,
         EXP_TRAVEL, D("1260")),
        (date(yy, 3, 20), "春装", CMB_CARD, EXP_CLOTHING, D("900")),
        (QINGMING[yy], "清明节 出行", CHECKING, EXP_TRAVEL, D("1500")),
        (date(yy, 5, 1), "劳动节 短途旅行", CHECKING, EXP_TRAVEL, D("2800")),
        (date(yy, 5, 28), "618预售", ALIPAY, EXP_CLOTHING, D("900")),
        (date(yy, 7, 15), "台风季备货", ALIPAY, EXP_MISC, D("400")),
        (MID_AUTUMN[yy] - timedelta(days=6), "中秋月饼礼盒", ALIPAY,
         EXP_GIFTS, D("1800")),
        (date(yy, 10, 1), "国庆节旅行", CHECKING, EXP_TRAVEL, D("4500")),
        (date(yy, 10, 25), "双十一定金", ALIPAY, EXP_CLOTHING, D("500")),
        (date(yy, 12, 20), "节日礼物", ALIPAY, EXP_GIFTS, D("2000")),
        (date(yy, 12, 28), "年末慈善捐款", CHECKING, EXP_CHARITY, D("1000")),
    ]


def gen_daily_weekly() -> list[dict]:
    txns: list[dict] = []
    rng = random.Random(SEED + 6)

    # Day by day from 2025-01-01 through THROUGH. Every habit draws once
    # per day whether or not it fires, so a (habit, day) outcome is the
    # same on a fresh build and on a continuation replay.
    d = date(YEAR, 1, 1)
    while d <= THROUGH:
        wd = d.weekday()
        away = away_label(d)
        r_coffee, r_conv, r_deliv, r_hema, r_charge, r_holiday = (
            rng.random() for _ in range(6))
        if away is None:
            if r_coffee < COFFEE_P[wd]:
                amt = _spend(rng, 15, 22)
                vend = "瑞幸咖啡"
                txns.append({
                    "description": vend, "date": d,
                    "splits": [(WECHAT, -amt),
                               (merchant_category(vend, EXP_DINING), amt)],
                })
            if r_conv < CONVENIENCE_P[wd]:
                amt = _spend(rng, 18, 35)
                vend = rng.choice(CONVENIENCE_VENDORS)
                txns.append({
                    "description": vend, "date": d,
                    "splits": [(WECHAT, -amt),
                               (merchant_category(vend, EXP_GROCERIES), amt)],
                })
            if r_deliv < DELIVERY_P[wd]:
                amt = _spend(rng, 28, 48)
                vend = "美团外卖"
                txns.append({
                    "description": vend, "date": d,
                    "splits": [(ALIPAY, -amt),
                               (merchant_category(vend, EXP_DINING), amt)],
                })
            if r_hema < HEMA_P[wd]:
                amt = _spend(rng, 300, 420)
                vend = "盒马鲜生"
                txns.append({
                    "description": vend, "date": d,
                    "splits": [(ALIPAY, -amt),
                               (merchant_category(vend, EXP_GROCERIES), amt)],
                })
            if r_charge < CHARGING_P[wd]:
                amt = _spend(rng, 60, 100)
                vend = "EV充电"
                txns.append({
                    "description": vend, "date": d,
                    "splits": [(WECHAT, -amt),
                               (merchant_category(vend, EXP_CHARGING), amt)],
                })
        elif r_holiday < 0.65:
            desc, acct, lo, hi = rng.choice(HOLIDAY_SPEND[away])
            amt = _spend(rng, lo, hi)
            txns.append({
                "description": desc, "date": d,
                "splits": [(WECHAT, -amt), (acct, amt)],
            })
        d += timedelta(days=1)

    # Monthly 山姆 run on the ICBC card — around the 12th, never ON the
    # 12th every month (audit A5).
    for yy, m in iter_months():
        sc = _clamp_day(yy, m, 12 + rng.randint(-3, 3))
        amt = _spend(rng, 420, 580)
        while away_label(sc) is not None:   # not while out of town
            sc += timedelta(days=7)
        if not _on_or_before_through(sc):
            continue
        vend = "山姆会员店"
        txns.append({
            "description": vend, "date": sc,
            "splits": [(ICBC_CARD, -amt),
                       (merchant_category(vend, EXP_GROCERIES), amt)],
        })

    # The holiday calendar, every year the timeline reaches, plus the
    # 618 / 双十一 order bursts.
    for yy in years_in_range():
        for dt, desc, src, dst, amt in seasonal_events(yy):
            if date(YEAR, 1, 1) <= dt <= THROUGH:
                txns.append({
                    "description": desc, "date": dt,
                    "splits": [(src, -amt), (dst, amt)],
                })
        for i, amt in enumerate([D("450"), D("400"), D("300")]):
            dt = date(yy, 6, 10 + i)
            if _on_or_before_through(dt):
                txns.append({
                    "description": f"618购物节 第{i+1}单", "date": dt,
                    "splits": [(ALIPAY, -amt), (EXP_CLOTHING, amt)],
                })
        for i, amt in enumerate([D("500"), D("450"), D("400"), D("350")]):
            dt = date(yy, 11, 11 + (i // 2))
            if _on_or_before_through(dt):
                txns.append({
                    "description": f"双十一 第{i+1}单", "date": dt,
                    "splits": [(ICBC_CARD, -amt), (EXP_CLOTHING, amt)],
                })

    # One-off equipment: the office monitor (2025 only — a purchase, not
    # a habit), booked as 办公设备 (audit P13).
    if _on_or_before_through(date(YEAR, 8, 20)):
        txns.append({
            "description": "办公新显示器", "date": date(YEAR, 8, 20),
            "splits": [(CMB_CARD, D("-2800")), (EXP_OFFICE_EQUIP, D("2800"))],
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
# Named events are drawn WITHOUT replacement (audit A4: 表妹 married
# three times). Birthdays and 伴手礼 recur every year (a per-year pool);
# a wedding, a 满月, a 乔迁 or a hospital visit happens to a given person
# once, so those pools are drawn down across the WHOLE timeline.
GIFT_OCCASIONS_YEARLY = [
    "同事 小张 生日礼物", "同事 老刘 生日礼物", "朋友 阿May 生日红包",
    "侄女 生日礼物", "外甥 生日礼物", "客户 伴手礼", "老家 伴手礼",
    "师姐 生日礼物", "健身搭子 生日礼物", "父亲 生日礼物", "婆婆 生日礼物",
]
GIFT_OCCASIONS_ONCE = [
    "表哥 乔迁之礼", "大学室友 乔迁之礼", "高中同学 满月红包",
    "前同事 满月红包", "邻居 探病果篮", "舅舅 探病果篮", "同事 升职贺礼",
    "师妹 乔迁之礼", "老乡 满月红包", "姑姑 探病果篮", "同事 小赵 满月红包",
    "表姐 乔迁之礼", "发小 升职贺礼", "堂哥 满月红包", "阿姨 探病果篮",
    "前领导 荣休贺礼", "球友 乔迁之礼", "同学 开业花篮", "邻居 满月红包",
    "客户 Kevin 乔迁之礼", "师兄 探病果篮", "表妹 满月红包",
]
WEDDING_OCCASIONS = [
    "婚礼红包 (大学同学 王磊)", "婚礼红包 (前同事 陈静)", "婚礼随礼 (表妹)",
    "婚礼红包 (老乡 李涛)", "婚礼红包 (高中同学 周婷)", "婚礼随礼 (表弟)",
    "婚礼红包 (同事 小赵)", "婚礼红包 (研究生同学 刘洋)",
    "婚礼红包 (客户 Kevin)", "婚礼随礼 (堂妹)", "婚礼红包 (球友 阿强)",
    "婚礼红包 (邻居家儿子)", "婚礼红包 (同事 小张)", "婚礼随礼 (表姐)",
    "婚礼红包 (发小 大鹏)", "婚礼红包 (大学同学 张楠)", "婚礼随礼 (堂弟)",
    "婚礼红包 (前同事 老周)", "婚礼红包 (师妹 林小雨)", "婚礼红包 (健身教练)",
    "婚礼红包 (高中同学 吴昊)", "婚礼随礼 (侄子)", "婚礼红包 (客户 Anna)",
    "婚礼红包 (老乡 赵倩)", "婚礼红包 (研究生同学 孙悦)", "婚礼随礼 (堂姐)",
    "婚礼红包 (球友 小胖)", "婚礼红包 (邻居家女儿)",
]
# Nightlife has a weekend shape (Mon → Sun weights; audit A1 had bars
# and KTV on Mon/Tue/Sun only).
ENTERTAINMENT_DOW_W = [0.5, 0.5, 0.6, 0.8, 2.0, 2.5, 1.5]
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
    ("阿里云盘 会员", 18, 25),            # cloud drive (audit L7)
    ("iCloud 储存 200GB", 21, 21),       # Apple iCloud monthly
    ("知乎盐选 会员", 19, 25),            # Zhihu premium
    ("得到 知识会员", 25, 38),            # DeDao premium
    ("百度网盘 超级会员", 15, 30),        # cloud-drive membership
]


def gen_personal_life() -> list[dict]:
    """Personal-life spending streams, 2025-01 → THROUGH, localized to a
    Shenzhen (深圳) contractor household (林微 + 周子航 + the cat 字节).

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
    gift_pool: dict[int, list[str]] = {}
    once_pool = rng.sample(GIFT_OCCASIONS_ONCE, k=len(GIFT_OCCASIONS_ONCE))
    for yy, m in iter_months(start):
        if yy not in gift_pool:
            gift_pool[yy] = rng.sample(GIFT_OCCASIONS_YEARLY,
                                       k=len(GIFT_OCCASIONS_YEARLY))
        # Skip Sep (中秋 carried) and Dec (节日礼物 carried) for the
        # baseline so we don't pile on top of the big named events.
        if m in (9, 12):
            continue
        if rng.random() < 0.85:  # ~5 of every 6 months get a small gift
            day = _clamp_day(yy, m, rng.randint(3, 26))
            # Every third gift is a once-in-a-lifetime occasion while the
            # pool lasts; the rest are the yearly birthdays.
            use_once = once_pool and rng.random() < 0.34
            occasion = once_pool.pop() if use_once else gift_pool[yy].pop()
            if _on_or_before_through(day):
                amt = _spend(rng,120, 350)
                txns.append({"description": occasion,
                             "date": day,
                             "splits": [(WECHAT, -amt), (EXP_GIFTS, amt)]})
    # 婚礼红包: 3-4 weddings a year, spread across non-春节 months, each
    # ¥800-1,600 — the lumpy occasions that make any 5-month window catch
    # at least one. Each year's couples are distinct people.
    couples_pool = rng.sample(WEDDING_OCCASIONS, k=len(WEDDING_OCCASIONS))
    for yy in sorted({y for y, _ in iter_months(start)}):
        n_weddings = rng.randint(3, 4)
        pool = [3, 4, 5, 6, 7, 8, 10, 11]
        chosen = rng.sample(pool, k=min(n_weddings, len(pool)))
        couples = [couples_pool.pop() for _ in chosen if couples_pool]
        for m, who in zip(chosen, couples):
            day = _clamp_day(yy, m, rng.randint(3, 26))
            if not _on_or_before_through(day):
                continue
            amt = _spend(rng,800, 1600)
            txns.append({"description": who,
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
    d = start - timedelta(days=start.weekday())  # the Monday of week 1
    while d <= THROUGH:
        # ~3 outings a month (skip ~1 week in 5), on a weekday drawn
        # from the nightlife shape (Fri/Sat heavy, never a fixed slot).
        fires = rng.random() < 0.80
        offset = rng.choices(range(7), weights=ENTERTAINMENT_DOW_W)[0]
        day = d + timedelta(days=offset)
        if fires and start <= day <= THROUGH and away_label(day) is None:
            amt = _spend(rng,80, 200)
            src = rng.choice([WECHAT, ALIPAY])
            txns.append({"description": rng.choice(ENTERTAINMENT_VENDORS),
                         "date": day,
                         "splits": [(src, -amt),
                                    (EXP_ENTERTAINMENT, amt)]})
        d += timedelta(days=7)
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
        ("出差 美国 Pacific Trade (机票+酒店)", CMB_CARD, 6000, 8000),
        ("出差 德国 Handelskontor München (机票+酒店)", CMB_CARD, 6000, 8000),
    ]
    trip_idx = 0
    cur = date(YEAR, 4, 1)
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


# ── Phase 7a: Contract engagements (invoiced through A/R) ───────

# The big mainland companies Lin Wei contracts to. Every progress payment
# is an INVOICE to a named customer, posted to 收入:承包收入 and carrying a
# 增值税专用发票 note — that is where the VAT and 经营所得 bases come from
# (audit L5: 199,000 of 2025 revenue used to land in the bank with no
# 发票, and no reader could tell who the contracting party was).
CONTRACT_CLIENTS = [
    "华为云", "腾讯", "字节跳动", "美团",
    "平安科技", "大疆", "顺丰科技",
]
CONTRACT_PROJECTS = {
    "华为云": "外包开发", "腾讯": "小程序项目", "字节跳动": "数据看板",
    "美团": "商家App", "平安科技": "风控看板", "大疆": "内部工具",
    "顺丰科技": "小程序",
}


def contract_invoice_plans() -> list[dict]:
    """Monthly progress invoices per engagement (Net 15, opened ~12 days
    before the client's payment run), a 尾款 bump when an engagement
    runs its full course, and irregular idle months falling wherever
    the pipeline ran dry (G9). Each year draws from its own seeded
    stream, so a year's calendar is the same on a fresh build and on a
    continuation replay. Only invoices opened on or before THROUGH."""
    plans: list[dict] = []
    for yy in years_in_range():
        rng = random.Random(f"{SEED}:contract:{yy}")
        n_gaps = rng.choice([1, 2, 2, 3])
        gaps = set(rng.sample(range(1, 13), n_gaps))
        clients = CONTRACT_CLIENTS[:]
        rng.shuffle(clients)
        client_idx = 0
        engagement = None   # [client, monthly_amount, pay_day, months_left]
        for m in range(1, 13):
            if m in gaps:
                engagement = None
                continue
            if engagement is None:
                client = clients[client_idx % len(clients)]
                client_idx += 1
                amt = D(str(rng.choice(range(16000, 26001, 500))))
                engagement = [client, amt, rng.randint(15, 25),
                              rng.randint(2, 4)]
            client, amt, pay_day, left = engagement
            left -= 1
            final = left == 0
            stage = "尾款" if final else "进度款"
            amount = amt + (D(str(rng.choice([0, 2000, 4000])))
                            if final else D("0"))
            engagement = None if final else [client, amt, pay_day, left]
            pay = next_business_day(_clamp_day(yy, m, pay_day))
            open_d = next_business_day(pay - timedelta(days=12))
            if open_d > THROUGH:
                continue
            plans.append({
                "client": client, "open": open_d, "pay": pay,
                "amount": amount,
                "desc": (f"{date(yy, m, 1).strftime('%Y年%m月')} "
                         f"{CONTRACT_PROJECTS[client]} {stage}"),
            })
    return plans


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


def _employee_by_name(book: GnuCashBook, name: str) -> dict:
    env = book.list_employees(compact=False, limit=250)
    rows = next((v for v in env.values() if isinstance(v, list)), [])
    for row in rows:
        if row.get("name") == name:
            return row
    raise SystemExit(f"continuation: employee {name!r} not found in book")


# The Shenzhen retainer: ¥12,000/month in 2025, renewed each January at
# +5% (rounded to the hundred) — a contract that keeps running (audit P4).
def shenzhen_retainer(yy: int) -> Decimal:
    raised = D("12000") * (D("1.05") ** (yy - YEAR))
    return (raised / D("100")).to_integral_value(rounding="ROUND_HALF_UP") * D("100")


# Bills that are NOT card charges (audit A2: 优客工场 and 阿里云 were both
# billed AND charged to the card for the same service). The vendors on
# the bill path are paid by bank transfer only.
BOOKKEEPING_FEE = D("900")          # quarterly 代理记账
HARDWARE_BILLS = [                  # (month, day, description, amount, account)
    (3, 15, "机械键盘 + 显示器支架", D("1280"), EXP_OFFICE_EQUIP),
    (9, 15, "打印纸 / 墨盒 / 网线", D("460"), EXP_OFFICE_SUPPLIES),
]
# 陈宇's expense claims — two vouchers a year through the voucher module.
ASSISTANT_VOUCHERS = [              # (month, day, description, amount, account)
    (4, 18, "客户拜访 交通+餐费", D("386.50"), EXP_BIZ_TRAVEL),
    (10, 22, "办公耗材 自购报销", D("263.80"), EXP_OFFICE_SUPPLIES),
]


def run_business(book: GnuCashBook, since: date | None = None) -> dict:
    """Create billterms, customers, vendors, the employee, jobs, and
    every invoice / bill / voucher through THROUGH.

    ``since`` (continuation mode): entities and jobs already exist in
    the frozen prefix — look them up instead of creating; emit only
    documents opened AFTER ``since``, and skip a THROUGH-relative
    "recent open" document while its predecessor is still outstanding.
    """
    counts = {"customers": 0, "vendors": 0, "invoices": 0, "bills": 0,
              "vouchers": 0, "terms": 0}

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

        shenzhen = book.create_customer(
            name="深圳跨境电商有限公司", currency="CNY",
            notes="本地跨境电商客户, 月度运维+开发 retainer, Net 30")
        pacific = book.create_customer(
            name="Pacific Trade Solutions", currency="USD",
            notes="美国客户, 移动应用外包, Net 30")
        munich = book.create_customer(
            name="Handelskontor München GmbH", currency="EUR",
            notes="德国客户, ERP 集成项目, Net 30")
        contract_customers = {
            name: book.create_customer(
                name=name, currency="CNY",
                notes=f"{CONTRACT_PROJECTS[name]} 外包, 开具增值税专用发票, Net 15")
            for name in CONTRACT_CLIENTS
        }
        counts["customers"] = 3 + len(contract_customers)

        bookkeeper = book.create_vendor(
            name="深圳博源代理记账", currency="CNY", notes="代理记账 季度服务费")
        hardware = book.create_vendor(
            name="华强北 赛格电子", currency="CNY", notes="办公设备/耗材")
        jetbrains = book.create_vendor(
            name="JetBrains", currency="USD", notes="IDE 年度订阅")
        counts["vendors"] = 3

        assistant = book.create_employee(name=ASSISTANT, currency="CNY")
        counts["employees"] = 1

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
        contract_customers = {name: _party_by_name(book, name)
                              for name in CONTRACT_CLIENTS}
        bookkeeper = _party_by_name(book, "深圳博源代理记账")
        hardware = _party_by_name(book, "华强北 赛格电子")
        jetbrains = _party_by_name(book, "JetBrains")
        assistant = _employee_by_name(book, ASSISTANT)
        job_sz = _job_by_name(book, "跨境电商平台改版")
        job_pacific = _job_by_name(book, "Pacific Mobile App v2")
        job_munich = _job_by_name(book, "München ERP-Integration")
        env = book.get_outstanding_invoices(compact=False, limit=250)
        open_owner_names = {
            doc.get("owner_name") for doc in env.get("invoices", [])
        }

    def run_invoice(customer_id, date_open, date_pay, amount, description,
                    currency, post_account, revenue_account=LLC_REVENUE,
                    term="Net 30", pay=True, job_id=None, notes=""):
        """Create → post → (optionally) pay a customer invoice. ``pay``
        is honoured only when ``date_pay`` is on or before THROUGH —
        an invoice whose payment run hasn't happened yet stays open."""
        if since is not None and date_open <= since:
            return None
        inv = book.create_invoice(
            customer_id=customer_id, date_opened=date_open.isoformat(),
            currency=currency, term=term, job_id=job_id, notes=notes,
        )
        book.add_invoice_entry(
            invoice_id=inv["id"], account=revenue_account,
            description=description, quantity="1", price=str(amount),
        )
        # No force needed: add_prices() lays real FX quotes on cross-currency
        # post & pay dates plus a monthly snapshot on the 1st of every month
        # through THROUGH, so the freshness guard is always satisfied with a
        # true market rate. The post→pay rate drift books a real realized
        # FX gain/loss on paid invoices.
        book.post_invoice(
            invoice_id=inv["id"], post_account=post_account,
            post_date=date_open.isoformat(), owner_type="customer",
        )
        if pay and date_pay <= THROUGH:
            book.pay_invoice(
                invoice_id=inv["id"], payment_account=CHECKING,
                amount=str(amount), payment_date=date_pay.isoformat(),
                owner_type="customer",
            )
        counts["invoices"] += 1
        return inv["id"]

    recent_open = date(THROUGH.year, THROUGH.month, 1)

    # Shenzhen (CNY): the monthly retainer, every year the timeline
    # reaches, invoiced on the first business day and paid on the 28th's
    # business day. The first two 2025 months attach to the platform job.
    for yy in years_in_range():
        fee = shenzhen_retainer(yy)
        for m in range(1, 13):
            open_d = next_business_day(date(yy, m, 1))
            if open_d > THROUGH:
                break
            run_invoice(
                shenzhen["id"], open_d,
                next_business_day(date(yy, m, 28)), fee,
                f"{date(yy, m, 1).strftime('%Y年%m月')} 移动应用开发与运维",
                "CNY", AR_CNY,
                job_id=(job_sz["id"] if (yy, m) in ((YEAR, 1), (YEAR, 2))
                        else None),
                notes=(f"{yy} 年度合同续签，月费 ¥{fee}" if m == 1 and yy > YEAR
                       else ""),
            )
    # Shenzhen OUTSTANDING (CNY A/R demo surface): a milestone just past
    # due and a maintenance invoice not yet due, both unpaid.
    if "深圳跨境电商有限公司" not in open_owner_names:
        sz_ms = open_document_date("sz_milestone")
        run_invoice(
            shenzhen["id"], sz_ms, sz_ms, "15000",
            f"{sz_ms.strftime('%Y年%m月')} 平台改版里程碑",
            "CNY", AR_CNY, pay=False, job_id=job_sz["id"])
        sz_mt = open_document_date("sz_maint")
        run_invoice(
            shenzhen["id"], sz_mt, sz_mt, "9000",
            f"{sz_mt.strftime('%Y年%m月')} 运维支持",
            "CNY", AR_CNY, pay=False)

    # Pacific Trade (USD → AR USD) and München (EUR → AR EUR): the same
    # calendar every year, paid cross-currency into CNY.
    for plan in foreign_invoice_plans():
        if plan["customer"] == "pacific":
            cust, post_acct, job = pacific, AR_USD, job_pacific
        else:
            cust, post_acct, job = munich, AR_EUR, job_munich
        run_invoice(
            cust["id"], plan["open"], plan["pay"], plan["amount"],
            plan["desc"], plan["currency"], post_acct,
            job_id=(job["id"] if plan["job"] else None),
        )
    if "Pacific Trade Solutions" not in open_owner_names:
        pac = open_document_date("pacific")
        run_invoice(
            pacific["id"], pac, pac, "5200",
            f"{pac.strftime('%B %Y')} retainer + change requests",
            "USD", AR_USD, pay=False, job_id=job_pacific["id"])
    if "Handelskontor München GmbH" not in open_owner_names:
        mp2 = open_document_date("munich_p2")
        run_invoice(
            munich["id"], mp2, mp2, "4100",
            f"{mp2.strftime('%B %Y')} ERP-Integration Phase 2",
            "EUR", AR_EUR, pay=False, job_id=job_munich["id"])
        mw = open_document_date("munich_wartung")
        run_invoice(
            munich["id"], mw, mw, "2800",
            f"{mw.strftime('%B %Y')} Wartung",
            "EUR", AR_EUR, pay=False)

    # Contract engagements (CNY, Net 15, 专票) to 承包收入.
    for plan in contract_invoice_plans():
        run_invoice(
            contract_customers[plan["client"]]["id"], plan["open"],
            plan["pay"], plan["amount"], plan["desc"], "CNY", AR_CNY,
            revenue_account=CONTRACTOR, term="Net 15",
            notes="增值税专用发票（征收率 1%）",
        )

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
            description=description, quantity="1", price=str(amount),
        )
        book.post_invoice(
            invoice_id=bill["id"], post_account=post_account,
            post_date=date_open.isoformat(), owner_type="vendor",
        )
        if pay and date_pay <= THROUGH:
            book.pay_invoice(
                invoice_id=bill["id"], payment_account=payment_account,
                amount=str(amount), payment_date=date_pay.isoformat(),
                owner_type="vendor",
            )
        counts["bills"] += 1
        return bill["id"]

    for yy in years_in_range():
        # 代理记账 quarterly service fee, billed early in the quarter.
        for m in (1, 4, 7, 10):
            open_d = next_business_day(date(yy, m, 6))
            if open_d > THROUGH:
                continue
            run_bill(bookkeeper["id"], open_d,
                     next_business_day(open_d + timedelta(days=12)),
                     BOOKKEEPING_FEE,
                     f"{yy}年 第{(m - 1) // 3 + 1}季度 代理记账服务费",
                     EXP_BIZ_SERVICES, "CNY")
        for m, day, desc, amount, acct in HARDWARE_BILLS:
            open_d = next_business_day(date(yy, m, day))
            if open_d > THROUGH:
                continue
            run_bill(hardware["id"], open_d,
                     next_business_day(open_d + timedelta(days=9)),
                     amount, desc, acct, "CNY")

    # JetBrains US$249 — the foreign-currency PAYABLE case (M2).
    # RE-DATED to a recent month (the 1st of THROUGH's month, which
    # carries a USD/CNY monthly snapshot) and left OUTSTANDING
    # (pay=False) so it reads as a current payable, not apparent
    # corruption. Posted to the USD A/P subledger: post_invoice
    # refuses a document/post-account commodity mismatch since
    # v1.5.0 (battery ruling 1 — per-currency payables are correct
    # practice, and the demo models it).
    jetbrains_post = recent_open
    jetbrains_bill_id = None
    if "JetBrains" not in open_owner_names:
        try:
            existing = book.get_account(AP_USD)
        except Exception:
            existing = None
        if not existing:
            try:
                book.create_account(
                    name=AP_USD.split(":")[-1],
                    account_type="PAYABLE",
                    parent="负债",
                    commodity="USD",
                )
            except ValueError as e:
                if "already exists" not in str(e).lower():
                    raise
        jetbrains_bill_id = run_bill(
            jetbrains["id"], jetbrains_post, jetbrains_post, "249",
            "JetBrains All Products Pack (annual subscription)",
            EXP_SOFTWARE, "USD", pay=False, post_account=AP_USD,
        )

    # 陈宇's expense vouchers: two a year, posted to A/P and reimbursed
    # from the bank account (audit L6 — the voucher module was
    # otherwise unexercised in this book).
    for yy in years_in_range():
        for m, day, desc, amount, acct in ASSISTANT_VOUCHERS:
            open_d = next_business_day(date(yy, m, day))
            if open_d > THROUGH:
                continue
            if since is not None and open_d <= since:
                continue
            voucher = book.create_voucher(
                employee_id=assistant["id"], date_opened=open_d.isoformat(),
                currency="CNY", term="Net 15",
            )
            book.add_voucher_entry(
                voucher_id=voucher["id"], account=acct,
                description=desc, quantity="1", price=str(amount),
            )
            book.post_invoice(
                invoice_id=voucher["id"], post_account=AP,
                post_date=open_d.isoformat(), owner_type="employee",
            )
            pay_d = next_business_day(open_d + timedelta(days=7))
            if pay_d <= THROUGH:
                book.pay_invoice(
                    invoice_id=voucher["id"], payment_account=CHECKING,
                    amount=str(amount), payment_date=pay_d.isoformat(),
                    owner_type="employee",
                )
            counts["vouchers"] += 1

    counts["jetbrains_bill_id"] = jetbrains_bill_id
    return counts


# ── Phase 8: Investment activity ────────────────────────────────

ACCT_BY_SYMBOL = {
    "300750": CATL, "510300": CSI300, "159915": CHINEXT,
}
OPENING_LOT_TITLE = {sym: title for _acct, sym, _units, title in OPENING_LOTS}
FRACTION = {"300750": 100, "510300": 10000, "159915": 10000}


# Mainland A-shares AND exchange-traded funds both trade in 一手 — round
# lots of 100 shares/units — so every buy is a whole multiple of 100 sized
# to a target budget, never a CNY amount divided by price (fractional or
# odd-lot holdings are impossible on the SSE/SZSE).
ROUND_LOT = {"300750": 100, "510300": 100, "159915": 100}

# A-share trading costs (audit L8): 印花税 0.05% on the SELL side of
# stocks (ETFs are exempt), broker commission 0.025% with a ¥5 minimum
# on stock trades both ways. ETF 定投 rides the broker's zero-minimum
# fund plan, so the DCA fills carry no fee line.
STAMP_DUTY_RATE = D("0.0005")
COMMISSION_RATE = D("0.00025")
COMMISSION_MIN = D("5")


def _commission(gross: Decimal) -> Decimal:
    return max(COMMISSION_MIN, (gross * COMMISSION_RATE).quantize(D("0.01")))


def _whole_units(budget: Decimal, price: Decimal, lot: int) -> Decimal:
    """Largest whole multiple of ``lot`` units whose cost ≤ ``budget``.

    Rounds down to the nearest lot (100). Returns at least one lot so a
    DCA buy is never zero-sized.
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
        for d in dca_dates():
            yy, m = d.year, d.month
            if d <= cut:
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
            d = trade_date(m, day)
            if d <= cut:
                continue
            price = real_price(sym, d)
            inv_acct = acct[ACCT_BY_SYMBOL[sym]]
            cny_amt = (shares * price).quantize(D("0.01"))
            is_stock = sym == "300750"
            fee = _commission(cny_amt) if is_stock else D("0")
            if action == "buy":
                lot = piecash.Lot(
                    title=f"{sym} {d.isoformat()} 买入", account=inv_acct,
                    notes=f"{shares} 股 @ ¥{price}" + (
                        f"，佣金 ¥{fee}" if fee else ""),
                    is_closed=0,
                )
                inv_split = piecash.Split(
                    account=inv_acct, value=cny_amt, quantity=shares)
                cash_split = piecash.Split(account=acct[CHECKING],
                                           value=-(cny_amt + fee))
                splits = [inv_split, cash_split]
                if fee:
                    splits.append(piecash.Split(account=acct[EXP_TRADING_FEES],
                                                value=fee))
                piecash.Transaction(
                    currency=cny, description=f"买入 {shares} {sym} @ ¥{price}",
                    post_date=d, splits=splits,
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
                #   below cost → realized_pl < 0  (a LOSS — the June CATL
                #               lot sold under its January basis)
                # Capital Gains is a credit-normal INCOME account, so a gain
                # is a credit (value = −realized_pl < 0) and a loss is a debit
                # (value = −realized_pl > 0) that REDUCES capital-gains income.
                # The three splits sum to zero either way:
                #   (−cost_basis) + cny_amt + (−realized_pl)
                #   = −cost_basis + cny_amt − (cny_amt − cost_basis) = 0.
                realized_pl = cny_amt - cost_basis
                # Sell-side costs: 印花税 + 佣金 come out of the proceeds
                # and book to 交易费用; the gain is measured on the gross.
                stamp = ((cny_amt * STAMP_DUTY_RATE).quantize(D("0.01"))
                         if is_stock else D("0"))
                fees = stamp + fee
                inv_split = piecash.Split(
                    account=inv_acct, value=-cost_basis, quantity=-shares)
                cash_split = piecash.Split(account=acct[CHECKING],
                                           value=cny_amt - fees)
                gain_split = piecash.Split(
                    account=acct[CAPITAL_GAINS], value=-realized_pl)
                splits = [inv_split, cash_split, gain_split]
                if fees:
                    splits.append(piecash.Split(account=acct[EXP_TRADING_FEES],
                                                value=fees))
                # Defensive: the synthetic data must balance to the fen.
                assert (-cost_basis) + (cny_amt - fees) + (-realized_pl) + fees == 0
                piecash.Transaction(
                    currency=cny, description=f"卖出 {shares} {sym} @ ¥{price}",
                    notes=(f"印花税 ¥{stamp}，佣金 ¥{fee}" if fees else None),
                    post_date=d, splits=splits,
                )
                inv_split.lot = lot
            counts["txns"] += 1

        # Dividends (cash to checking).
        dividends = [
            (8, 15, "宁德时代 现金分红", D("300")),    # 100 sh × ~¥3
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


# ── Phase 9: Credit cards ───────────────────────────────────────

def gen_hsbc_charges() -> list[dict]:
    """The HSBC HKD card's charges — FOREIGN-currency liability (H1). All
    splits in HKD; the transaction currency is HKD and the offsetting
    CNY expense split carries a HKD value (txn currency) + CNY quantity
    (account commodity) at the REAL HKD/CNY rate on the charge date (a
    matching quote is on file via fx_price_dates)."""
    txns: list[dict] = []
    for dt, desc, hkd_amt, acct in hsbc_charges():
        rate = md_fx_cny("HKD", dt)
        cny_val = (hkd_amt * rate).quantize(D("0.01"))
        txns.append({
            "description": desc,
            "date": dt,
            "currency": "HKD",
            "notes": f"HK${hkd_amt} @ {rate} CNY/HKD",
            "splits": [
                (HSBC_CARD, -hkd_amt),
                (acct, hkd_amt, cny_val),
            ],
        })
    return txns


def run_hsbc_statements(out_path: Path) -> int:
    """Pay every HSBC statement IN FULL by 购汇 from 银行储蓄卡 on a
    business day 5–9 days after the close (audit P2). The statement
    balance is the card's running HKD balance at the close; the CNY
    quantity is the real HKD/CNY rate on the payment date, which the
    price layer records on that date."""
    from continuation import _seeded

    rows = [(dt, -amt) for dt, _desc, amt, _acct in hsbc_charges()]
    txns: list[dict] = []
    price_dates: list[tuple[str, date]] = []
    y, m = YEAR, 1
    while True:
        close = _clamp_day(y, m, HSBC_CLOSE_DAY)
        lag = _seeded("lin-wei", "paylag:汇丰港币信用卡", close, 5, 9)
        pay = next_business_day(close + timedelta(days=lag))
        if pay > THROUGH:
            break
        owed = -sum((v for d, v in rows if d <= close), D("0"))
        if owed > 0:
            rate = md_fx_cny("HKD", pay)
            cny = (owed * rate).quantize(D("0.01"))
            txns.append({
                "description": "汇丰 港币卡 还款（购汇）",
                "date": pay,
                "currency": "HKD",
                "notes": (f"{close.strftime('%Y-%m')} 账单 HK${owed} 全额还清，"
                          f"购汇 @ {rate}"),
                "splits": [
                    (HSBC_CARD, owed),             # liability down (HKD)
                    (CHECKING, -owed, -cny),       # HKD value / CNY quantity
                ],
            })
            rows.append((pay, owed))
            price_dates.append(("HKD", pay))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    _add_price_rows(out_path, price_dates)
    return write_bulk(out_path, txns)


def _card_running(book, path: str) -> list[tuple[date, Decimal]]:
    """(date, value) for every split on a card account, in date order."""
    acct = {a.fullname: a for a in book.accounts}
    rows: list[tuple[date, Decimal]] = []
    for sp in acct[path].splits:
        post = sp.transaction.post_date
        if hasattr(post, "date"):
            post = post.date()
        rows.append((post, Decimal(str(sp.value))))
    rows.sort()
    return rows


# The ICBC opening arrears (¥8,500) are paid down ¥1,500 a cycle with
# interest on the carried balance until clear, then the card is paid in
# full like 招商. The CMB card is paid in full from the first statement.
ICBC_CATCHUP = D("1500")
CARD_TERMS = [  # (account, label, statement close day)
    (CMB_CARD, "招商银行信用卡", 25),
    (ICBC_CARD, "工商银行信用卡", 20),
]


def run_credit_cards(out_path: Path) -> int:
    """Statement payments (and carried-balance interest) for the two
    CNY cards from the first 2025 close through the last close whose
    payment lands inside THROUGH. Reads the charges already in the
    book and walks the cycles sequentially, so each payment is the true
    statement balance and interest posts only in a month a balance
    actually carried (audit P1)."""
    from continuation import _seeded

    gc = piecash.open_book(str(out_path), readonly=True, open_if_lock=True)
    try:
        charges = {acct: _card_running(gc, acct) for acct, _l, _c in CARD_TERMS}
        aprs = {}
        for acct, _l, _c in CARD_TERMS:
            slots = {sl.name: sl.value for sl in
                     next(a for a in gc.accounts if a.fullname == acct).slots}
            aprs[acct] = D(str(slots.get("apr", "18.25")))
    finally:
        gc.close()

    txns: list[dict] = []
    for acct, label, close_day in CARD_TERMS:
        rows = list(charges[acct])
        apr = aprs[acct]

        def balance_at(when: date) -> Decimal:
            return -sum((v for d, v in rows if d <= when), D("0"))

        y, m = YEAR, 1
        carried = D("0")
        # The ICBC opening arrears are paid down in instalments; once
        # cleared the card is paid in full for good (a later big cycle
        # is not new arrears).
        catching_up = acct == ICBC_CARD
        while True:
            close = _clamp_day(y, m, close_day)
            pay_lag = _seeded("lin-wei", f"paylag:{label}", close, 3, 7)
            pay_date = close + timedelta(days=pay_lag)
            if pay_date > THROUGH:
                break
            if carried > 0:
                interest = (carried * apr / D("100") / D("12")).quantize(
                    D("0.01"))
                if interest > 0:
                    txns.append({
                        "description": f"{label} 利息",
                        "date": close,
                        "notes": f"上期未还 ¥{carried} 按年化 {apr}% 计息",
                        "splits": [(acct, -interest), (EXP_CC_INT, interest)],
                    })
                    rows.append((close, -interest))
                    rows.sort()
            owed = balance_at(close)
            if owed <= 0:
                carried = D("0")
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
                continue
            if catching_up and owed > ICBC_CATCHUP:
                payment = ICBC_CATCHUP
                desc = f"{label} 还款（分期清偿欠款）"
            elif catching_up:
                payment = owed
                desc = f"{label} 还款（结清欠款）"
                catching_up = False
            else:
                payment = owed
                desc = f"{label} 还款"
            payment = payment.quantize(D("0.01"))
            txns.append({
                "description": desc, "date": pay_date,
                "notes": f"{close.strftime('%Y-%m')} 账单 ¥{owed}",
                "splits": [(CHECKING, -payment), (acct, payment)],
            })
            rows.append((pay_date, payment))
            rows.sort()
            carried = owed - payment
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return write_bulk(out_path, txns)


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

    # 2. Recategorized: ¥450 办公用品 → 杂项 by mistake, then replace_splits
    #    to 经营支出:办公用品 (audit P13).
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
            {"account": EXP_OFFICE_SUPPLIES, "amount": "450"},
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

# Small wallet spend. Commuting (滴滴/单车/地铁) is a workday thing; the
# weekend list drops it. Every line rides WeChat or Alipay — nobody pays
# a vending machine from a bank card (audit P10).
VOLUME_WEEKDAY = ["瑞幸咖啡", "美宜佳", "天虹微喔", "美团外卖", "饿了么外卖",
                  "滴滴出行", "共享单车", "深圳地铁", "自动贩卖机"]
VOLUME_WEEKEND = ["瑞幸咖啡", "美宜佳", "天虹微喔", "美团外卖", "饿了么外卖",
                  "滴滴出行", "自动贩卖机"]


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
        pool = VOLUME_WEEKEND if dt.weekday() >= 5 else VOLUME_WEEKDAY
        vendor = rng.choice(pool)
        amt = _spend(rng, 5, 80)
        src = rng.choice([WECHAT, WECHAT, ALIPAY])
        if away_label(dt) is not None:
            continue
        # Category is the merchant's canonical bucket — consistent every time,
        # including the sticky vending-machine→Misc miscategorization. No more
        # per-transaction random scatter across Dining/Misc.
        category = merchant_category(vendor, EXP_DINING)
        txns.append({
            "description": vendor,
            "date": dt,
            "splits": [(src, -amt), (category, amt)],
        })
    return txns


# ── Phase 14: Taxes computed from the ledger ────────────────────
#
# Nothing here is a constant amount. The quarterly VAT return reads the
# quarter's sales by 发票 class (专票 / 普票 / export) from the posted
# invoices; the 经营所得 prepayment reads cumulative revenue and 经营支出
# from the same ledger and applies the five-bracket table with the
# 2023–2027 halving; the March 汇算清缴 settles the prior year with the
# deductions that are only claimed annually. Every figure is written
# into the transaction's notes so a reader can re-derive it.

ANNUAL_ONLY_DEDUCTIONS = D("21600")   # 赡养老人 ¥18,000 + 继续教育 ¥3,600


def _ledger_account_paths(con: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """guid → (fullname, commodity mnemonic) for every account under the
    book's root; template accounts (under GnuCash's template root) are
    left out, so template rows never count as activity."""
    root = con.execute("SELECT root_account_guid FROM books").fetchone()[0]
    rows = con.execute(
        "SELECT a.guid, a.name, a.parent_guid, c.mnemonic FROM accounts a "
        "LEFT JOIN commodities c ON c.guid = a.commodity_guid").fetchall()
    by_guid = {g: (n, pg, mn) for g, n, pg, mn in rows}
    paths: dict[str, tuple[str, str] | None] = {}

    def path(g):
        if g in paths:
            return paths[g]
        name, parent, mn = by_guid[g]
        if parent is None:
            paths[g] = None
        elif parent == root:
            paths[g] = (name, mn)
        else:
            up = path(parent)
            paths[g] = None if up is None else (f"{up[0]}:{name}", mn)
        return paths[g]

    return {g: path(g) for g in by_guid if path(g) is not None}


def _ledger_rows(con: sqlite3.Connection):
    """[(txn_guid, post_date, description, [(fullname, mnemonic,
    quantity, reconcile_state), ...])] for every ledger transaction."""
    paths = _ledger_account_paths(con)
    rows = con.execute(
        "SELECT t.guid, t.post_date, t.description, s.account_guid, "
        "s.quantity_num, s.quantity_denom, s.reconcile_state "
        "FROM splits s JOIN transactions t ON t.guid = s.tx_guid "
        "ORDER BY t.post_date, t.guid").fetchall()
    txns: dict[str, list] = {}
    order: list[str] = []
    for guid, post, desc, acct, qn, qd, state in rows:
        if acct not in paths:
            continue
        if guid not in txns:
            txns[guid] = [guid, date.fromisoformat(post[:10]), desc, []]
            order.append(guid)
        fullname, mn = paths[acct]
        txns[guid][3].append((fullname, mn, D(qn) / D(qd), state))
    return [txns[g] for g in order]


def _quarter(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def _ledger_by_quarter(out_path: Path) -> tuple[dict, dict]:
    """(revenue, expenses) per (year, quarter). ``revenue[q]`` splits
    into 专票 (承包收入 — the big-company contracts), 普票 (domestic
    个体经营收入) and export (个体经营收入 settled through a foreign-
    currency A/R). Expenses are everything under 支出:经营支出."""
    con = sqlite3.connect(str(out_path))
    try:
        rows = _ledger_rows(con)
    finally:
        con.close()
    revenue: dict[tuple[int, int], dict[str, Decimal]] = {}
    expenses: dict[tuple[int, int], Decimal] = {}
    for _guid, post, _desc, splits in rows:
        q = _quarter(post)
        ar_foreign = any(
            fn.startswith("资产:应收款项:") and mn != "CNY"
            for fn, mn, _qty, _st in splits)
        for fn, _mn, qty, state in splits:
            if state == "v":
                continue
            if fn == CONTRACTOR:
                klass = "special"
            elif fn == LLC_REVENUE:
                klass = "export" if ar_foreign else "normal"
            elif fn.startswith(EXP_BIZ + ":"):
                expenses[q] = expenses.get(q, D("0")) + qty
                continue
            else:
                continue
            bucket = revenue.setdefault(
                q, {"special": D("0"), "normal": D("0"), "export": D("0")})
            bucket[klass] += -qty
    return revenue, expenses


def _biz_income_tax(taxable: Decimal, year: int) -> Decimal:
    """经营所得 tax on annual taxable income, with the 2023–2027 halving
    on the portion up to ¥2,000,000."""
    if taxable <= 0:
        return D("0")

    def bracket(x: Decimal) -> Decimal:
        for upper, rate, quick in BIZ_TAX_BRACKETS:
            if x <= upper:
                return x * rate - quick
        raise AssertionError("open-ended top bracket")

    tax = bracket(taxable)
    if year in BIZ_TAX_HALVING_YEARS:
        tax -= bracket(min(taxable, BIZ_TAX_HALVING_CAP)) * D("0.5")
    return tax.quantize(D("0.01"))


def _filing_dates(yy: int, q: int) -> tuple[date, date]:
    """(VAT filing date, 经营所得 prepayment date) for quarter q of yy —
    the 12th/14th of the month after the quarter, on a business day."""
    fy, fm = (yy + 1, 1) if q == 4 else (yy, 3 * q + 1)
    return (next_business_day(date(fy, fm, TAX_VAT_DAY)),
            next_business_day(date(fy, fm, TAX_PIT_DAY)))


def tax_transactions(out_path: Path) -> tuple[list[dict], dict]:
    """Every VAT return, 经营所得 prepayment and 汇算清缴 due on or
    before THROUGH, computed from the book as built so far. Returns the
    transactions and a per-year summary used by the report."""
    revenue, expenses = _ledger_by_quarter(out_path)
    txns: list[dict] = []
    summary: dict[int, dict] = {}
    zero = {"special": D("0"), "normal": D("0"), "export": D("0")}
    for yy in years_in_range():
        paid = D("0")
        vat_year = D("0")
        quarters_filed = 0
        for q in (1, 2, 3, 4):
            vat_date, pit_date = _filing_dates(yy, q)
            r = revenue.get((yy, q), zero)
            if vat_date <= THROUGH:
                special_base = (r["special"] / D("1.01")).quantize(D("0.01"))
                normal_base = (r["normal"] / D("1.01")).quantize(D("0.01"))
                domestic = special_base + normal_base
                exempt = domestic <= VAT_EXEMPT_QUARTERLY
                vat = special_base * VAT_RATE
                if not exempt:
                    vat += normal_base * VAT_RATE
                vat = vat.quantize(D("0.01"))
                surcharge = (vat * VAT_SURCHARGE_RATE).quantize(D("0.01"))
                total = vat + surcharge
                notes = (f"{yy}年Q{q} 专票销售额（不含税）¥{special_base}，"
                         f"普票 ¥{normal_base}"
                         + ("（季度销售额未超 30 万，免征）" if exempt else "")
                         + f"，跨境服务出口 ¥{r['export']}（免税）；"
                         f"增值税 ¥{vat} + 附加税费 ¥{surcharge}")
                txns.append({
                    "description": "增值税及附加 季度申报缴款",
                    "date": vat_date, "notes": notes,
                    "splits": [(CHECKING, -total), (EXP_VAT, total)],
                })
                vat_year += total
            if pit_date <= THROUGH:
                rev_ytd = sum((sum(revenue.get((yy, qq), zero).values())
                               for qq in range(1, q + 1)), D("0"))
                exp_ytd = sum((expenses.get((yy, qq), D("0"))
                               for qq in range(1, q + 1)), D("0"))
                deduction = (BIZ_TAX_ANNUAL_DEDUCTION * 3 * q / 12
                             ).quantize(D("0.01"))
                taxable = rev_ytd - exp_ytd - deduction
                cum_tax = _biz_income_tax(taxable, yy)
                prepay = max(D("0"), cum_tax - paid).quantize(D("0.01"))
                notes = (f"{yy}年 1–{3 * q}月 累计收入 ¥{rev_ytd} − 成本费用 "
                         f"¥{exp_ytd} − 费用扣除 ¥{deduction} = 累计应纳税所得额 "
                         f"¥{taxable}；累计应纳税额 ¥{cum_tax}"
                         + ("（≤200万部分减半）" if yy in BIZ_TAX_HALVING_YEARS
                            else "")
                         + f"，已预缴 ¥{paid}，本期预缴 ¥{prepay}")
                txns.append({
                    "description": "经营所得个人所得税 季度预缴",
                    "date": pit_date, "notes": notes,
                    "splits": [(CHECKING, -prepay),
                               (EXP_BIZ_INCOME_TAX, prepay)],
                })
                paid += prepay
                quarters_filed = q
        settle_date = next_business_day(date(yy + 1, 3, TAX_SETTLE_DAY))
        settlement = D("0")
        annual = None
        if settle_date <= THROUGH:
            rev_y = sum((sum(revenue.get((yy, qq), zero).values())
                         for qq in range(1, 5)), D("0"))
            exp_y = sum((expenses.get((yy, qq), D("0"))
                         for qq in range(1, 5)), D("0"))
            taxable = (rev_y - exp_y - BIZ_TAX_ANNUAL_DEDUCTION
                       - ANNUAL_ONLY_DEDUCTIONS)
            annual = _biz_income_tax(taxable, yy)
            settlement = (annual - paid).quantize(D("0.01"))
            if settlement != 0:
                kind = "补税" if settlement > 0 else "退税"
                txns.append({
                    "description": "经营所得个人所得税 年度汇算清缴",
                    "date": settle_date,
                    "notes": (f"{yy}年度汇算清缴：收入 ¥{rev_y} − 成本费用 ¥{exp_y}"
                              f" − 费用扣除 ¥{BIZ_TAX_ANNUAL_DEDUCTION} − 专项附加扣除 "
                              f"¥{ANNUAL_ONLY_DEDUCTIONS}（赡养老人、继续教育）= "
                              f"应纳税所得额 ¥{taxable}；应纳税额 ¥{annual}，"
                              f"已预缴 ¥{paid}，{kind} ¥{abs(settlement)}"),
                    "splits": [(CHECKING, -settlement),
                               (EXP_BIZ_INCOME_TAX, settlement)],
                })
        summary[yy] = {
            "vat": vat_year, "pit_prepaid": paid, "pit_settlement": settlement,
            "pit_annual": annual, "quarters_filed": quarters_filed,
        }
    return txns, summary


def run_taxes(out_path: Path) -> dict:
    txns, summary = tax_transactions(out_path)
    n = write_bulk(out_path, txns)
    summary["written"] = n
    return summary


# ── Phase 11: Reconciliation ────────────────────────────────────

def run_reconciliation(out_path: Path) -> list[str]:
    """The bookkeeper's posture (review §1): every bank / wallet / card
    account reconciled through the last FULL month, the current month
    left open as the demo's work item (audit A7 — the bank used to be
    frozen at 2025-03-30)."""
    from continuation import reconcile_through
    return reconcile_through(POLICY, out_path, THROUGH)


# ── Entry timestamps ────────────────────────────────────────────

def stamp_entry_dates(out_path: Path) -> int:
    """``enter_date`` = the post date plus a few hours, not the build
    moment (audit round 2, item 7). piecash and the server both stamp
    ``datetime.now()`` on every write; a book whose 2,900 rows were all
    entered in the same minute is a generator's fingerprint. The offset
    is a hash of (post_date, description), so the stamp is stable
    across rebuilds."""
    con = sqlite3.connect(str(out_path))
    try:
        rows = con.execute(
            "SELECT guid, post_date, description FROM transactions").fetchall()
        for guid, post, desc in rows:
            if not post:
                continue
            base = datetime.strptime(post[:19], "%Y-%m-%d %H:%M:%S")
            h = int(hashlib.sha1(f"{post}|{desc}".encode()).hexdigest(), 16)
            entered = base + timedelta(hours=1 + h % 9, minutes=(h >> 8) % 60,
                                       seconds=(h >> 16) % 60)
            con.execute("UPDATE transactions SET enter_date = ? WHERE guid = ?",
                        (entered.strftime("%Y-%m-%d %H:%M:%S"), guid))
        con.commit()
    finally:
        con.close()
    return len(rows)


# ── Scheduled-transaction state (kept ENABLED) ──────────────────

def _sx_instances(ledger, template_desc: str) -> list:
    """The ledger transactions that are instances of a schedule: same
    description, or the description plus a parenthesised suffix (the
    overtime months of the payslip)."""
    return [t for t in ledger
            if (t.description == template_desc
                or t.description.startswith(template_desc + " ("))
            and t.post_date <= THROUGH]


def set_schedule_state(out_path: Path) -> dict:
    """``last_occur`` = the latest POSTED instance of each schedule, and
    every instance stamped with GnuCash's ``from-sched-xaction`` slot
    (audit A3: 经营所得 季度预缴 sat at 2026-06-14 with its September
    instance in the ledger, so the dashboard called it overdue). A
    schedule with no instance yet keeps ``last_occur`` empty."""
    info = {"schedules": 0, "instances": 0, "unposted": []}
    stamps: list[tuple[str, str]] = []   # (txn guid, sx guid)
    gc = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    try:
        ledger = _ledger_txns(gc)
        for sx in gc.session.query(piecash.ScheduledTransaction).all():
            tmpl = sx.template_account
            tmpl_txns = {sp.transaction for sp in tmpl.splits}
            desc = (next(iter(tmpl_txns)).description
                    if tmpl_txns else sx.name)
            instances = _sx_instances(ledger, desc)
            sx.enabled = 1
            sx.last_occur = (max(t.post_date for t in instances)
                             if instances else None)
            if not instances:
                info["unposted"].append(sx.name)
            stamps.extend((t.guid, sx.guid) for t in instances)
            info["schedules"] += 1
        gc.save()
    finally:
        gc.close()
    # The instance stamp, exactly as desktop (and the server's
    # create_transaction_from_scheduled) writes it: a bare GUID slot.
    con = sqlite3.connect(str(out_path))
    try:
        have = {row[0] for row in con.execute(
            "SELECT obj_guid FROM slots WHERE name = 'from-sched-xaction'")}
        for txn_guid, sx_guid in stamps:
            if txn_guid in have:
                continue
            con.execute(
                "INSERT INTO slots (obj_guid, name, slot_type, guid_val) "
                "VALUES (?, 'from-sched-xaction', 9, ?)", (txn_guid, sx_guid))
            info["instances"] += 1
        con.commit()
    finally:
        con.close()
    return info


# ── Verification ────────────────────────────────────────────────

def _parse_money(s) -> Decimal:
    """Parse a comma-formatted money string (or Decimal) to Decimal."""
    return Decimal(str(s).replace(",", "").replace("¥", "").strip())


def _ledger_txns(book: piecash.Book) -> list:
    """Ledger rows only. Since 1.5 every schedule's template is a real
    Transaction row (on an account under GnuCash's template root, all-zero
    splits) — it is not activity, and a realism check that sorts it in
    front of the salary run reads zeros."""
    template_guids: set[str] = set()
    stack = [book.root_template]
    while stack:
        acct = stack.pop()
        template_guids.add(acct.guid)
        stack.extend(acct.children)
    return [t for t in book.transactions
            if not any(s.account.guid in template_guids for s in t.splits)]


def _verify_realism(out_path: Path) -> None:
    """Deep-realism evidence: cents, merchant→category, payroll, cap-gains."""
    book = piecash.open_book(str(out_path), readonly=True)
    try:
        txns = _ledger_txns(book)

        # 1. Cents on consumer spend. Sample ~20 daily-spend transactions and
        #    report the fraction carrying non-zero jiao/fen.
        consumer_merchants = (
            "瑞幸", "美团外卖", "饿了么", "盒马", "美宜佳", "7-11",
            "天虹微喔", "山姆", "滴滴出行", "共享单车", "自动贩卖机",
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
        for key in (SALARY_DESC, "房贷还款", "车贷还款",
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
            (t for t in txns if t.description.startswith(SALARY_DESC)),
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
                for key in ("瑞幸", "美团外卖", "饿了么", "盒马", "美宜佳",
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
                cg = [s for s in t.splits
                      if s.account.fullname == CAPITAL_GAINS]
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


def _month_ends_through(through: date) -> list[date]:
    out: list[date] = []
    y, m = YEAR, 1
    while True:
        nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        end = nxt - timedelta(days=1)
        if end > through:
            return out
        out.append(end)
        y, m = nxt.year, nxt.month


def _running_balance(rows, fullname: str):
    """A ``bal_at(date)`` closure over the ledger rows for one account
    (non-voided quantities, cumulative by post date)."""
    events = sorted(
        (post, qty) for _g, post, _d, splits in rows
        for fn, _mn, qty, state in splits
        if fn == fullname and state != "v")

    def bal_at(when: date) -> Decimal:
        return sum((q for d, q in events if d <= when), D("0"))
    return bal_at


def _verify_invariants(out_path: Path, tax_summary: dict) -> None:
    """Round-2 invariants over every month-end of the timeline. Each
    raises SystemExit on violation; the values are printed either way
    so the build log carries the evidence."""
    print("\n-- Invariants over every month-end (audit round 2) --")
    con = sqlite3.connect(str(out_path))
    try:
        rows = _ledger_rows(con)
        slots = {}
        for obj, name, sval in con.execute(
                "SELECT s.obj_guid, s.name, "
                "COALESCE(s.string_val, CAST(s.int64_val AS TEXT)) "
                "FROM slots s WHERE s.name IN ('credit_limit')"):
            slots.setdefault(obj, {})[name] = sval
        paths = _ledger_account_paths(con)
        limits = {paths[g][0]: D(v["credit_limit"]) for g, v in slots.items()
                  if g in paths and "credit_limit" in v}
        invoices = con.execute(
            "SELECT i.id, i.date_posted, bt.duedays, i.post_lot, i.currency "
            "FROM invoices i LEFT JOIN billterms bt ON bt.guid = i.terms "
            "WHERE i.date_posted IS NOT NULL AND i.date_posted <> ''"
        ).fetchall()
        lot_splits = {}
        for lot, post, qn, qd, state in con.execute(
                "SELECT s.lot_guid, t.post_date, s.quantity_num, "
                "s.quantity_denom, s.reconcile_state FROM splits s "
                "JOIN transactions t ON t.guid = s.tx_guid "
                "WHERE s.lot_guid IS NOT NULL"):
            lot_splits.setdefault(lot, []).append(
                (date.fromisoformat(post[:10]), D(qn) / D(qd), state))
    finally:
        con.close()
    month_ends = _month_ends_through(THROUGH)
    checkpoints = month_ends + ([THROUGH] if THROUGH not in month_ends else [])

    # 1. Every credit card ≤ its credit_limit slot.
    worst: dict[str, tuple[Decimal, date]] = {}
    for card in (CMB_CARD, ICBC_CARD, HSBC_CARD):
        bal_at = _running_balance(rows, card)
        limit = limits.get(card)
        for me in checkpoints:
            owed = -bal_at(me)
            if card not in worst or owed > worst[card][0]:
                worst[card] = (owed, me)
            if limit is not None and owed > limit:
                raise SystemExit(f"INVARIANT: {card} owes {owed} over limit "
                                 f"{limit} at {me}")
    for card, (owed, me) in worst.items():
        print(f"  card ≤ limit: {card.split(':')[-1]} peak owed {owed:,.2f} "
              f"on {me} (limit {limits.get(card)}) OK")

    # 2. 银行储蓄卡 inside the policy band at every month-end.
    lo, hi = POLICY.buffer * D("0.5"), POLICY.buffer * D("3")
    bal_at = _running_balance(rows, CHECKING)
    band = [(me, bal_at(me)) for me in month_ends]
    out_of_band = [(me, b) for me, b in band if not (lo <= b <= hi)]
    mn = min(band, key=lambda t: t[1]) if band else None
    mx = max(band, key=lambda t: t[1]) if band else None
    print(f"  checking band [{lo:,.0f}, {hi:,.0f}]: min {mn[1]:,.2f} on "
          f"{mn[0]}, max {mx[1]:,.2f} on {mx[0]}; "
          f"{len(out_of_band)} month-ends outside")
    if out_of_band:
        raise SystemExit(f"INVARIANT: checking outside band at "
                         f"{out_of_band[:3]}")

    # 3. No invoice unpaid beyond terms + 45 days at any month-end.
    late: list[tuple] = []
    max_age = (0, None)
    for inv_id, posted, duedays, lot, cur in invoices:
        posted_d = date.fromisoformat(posted[:10])
        due = posted_d + timedelta(days=int(duedays or 30))
        legs = lot_splits.get(lot, [])
        balance = sum((q for _d, q, st in legs if st != "v"), D("0"))
        paid_on = max((d for d, _q, _st in legs), default=None)
        settled = paid_on if balance == 0 and len(legs) > 1 else None
        for me in checkpoints:
            if posted_d > me:
                continue
            if settled is not None and settled <= me:
                continue
            age = (me - due).days
            if age > max_age[0]:
                max_age = (age, inv_id)
            if age > 45:
                late.append((inv_id, posted_d, due, me))
    print(f"  invoices: {len(invoices)} posted; oldest past-due age at any "
          f"checkpoint {max_age[0]} days (#{max_age[1]}); "
          f"{len(late)} beyond terms+45 OK" if not late else
          f"  invoices: {len(late)} beyond terms+45: {late[:3]}")
    if late:
        raise SystemExit("INVARIANT: invoice unpaid beyond terms + 45 days")

    # 4. VAT + 经营所得 booked within 15% of what the ledger implies.
    revenue, expenses = _ledger_by_quarter(out_path)
    zero = {"special": D("0"), "normal": D("0"), "export": D("0")}
    booked_vat: dict[int, Decimal] = {}
    booked_pit: dict[int, Decimal] = {}
    for _g, post, desc, splits in rows:
        for fn, _mn, qty, state in splits:
            if state == "v":
                continue
            if fn == EXP_VAT:
                # A return filed in month M covers the quarter ending in
                # M-1: January's covers the prior year's Q4.
                yr = post.year - 1 if post.month == 1 else post.year
                booked_vat[yr] = booked_vat.get(yr, D("0")) + qty
            elif fn == EXP_BIZ_INCOME_TAX:
                yr = post.year - 1 if post.month in (1, 3) else post.year
                booked_pit[yr] = booked_pit.get(yr, D("0")) + qty
    for yy in years_in_range():
        s_ = tax_summary.get(yy, {})
        qf = s_.get("quarters_filed", 0)
        if qf == 0:
            continue
        # Implied VAT: recomputed from the ledger, quarter by quarter.
        implied_vat = D("0")
        for q in range(1, qf + 1):
            r = revenue.get((yy, q), zero)
            sb = (r["special"] / D("1.01")).quantize(D("0.01"))
            nb = (r["normal"] / D("1.01")).quantize(D("0.01"))
            vat = sb * VAT_RATE + (nb * VAT_RATE
                                   if sb + nb > VAT_EXEMPT_QUARTERLY else 0)
            vat = vat.quantize(D("0.01"))
            implied_vat += vat + (vat * VAT_SURCHARGE_RATE).quantize(D("0.01"))
        rev = sum((sum(revenue.get((yy, q), zero).values())
                   for q in range(1, qf + 1)), D("0"))
        exp = sum((expenses.get((yy, q), D("0")) for q in range(1, qf + 1)),
                  D("0"))
        settled = s_.get("pit_annual") is not None
        deductions = (BIZ_TAX_ANNUAL_DEDUCTION + ANNUAL_ONLY_DEDUCTIONS
                      if settled else BIZ_TAX_ANNUAL_DEDUCTION * 3 * qf / 12)
        implied_pit = _biz_income_tax(rev - exp - deductions, yy)
        got_vat = booked_vat.get(yy, D("0"))
        got_pit = booked_pit.get(yy, D("0"))
        implied = implied_vat + implied_pit
        got = got_vat + got_pit
        dev = (abs(got - implied) / implied * 100) if implied else D("0")
        print(f"  tax {yy} (Q1–Q{qf}{', settled' if settled else ''}): "
              f"VAT+附加 booked {got_vat:,.2f} vs implied {implied_vat:,.2f}; "
              f"经营所得 booked {got_pit:,.2f} vs implied {implied_pit:,.2f}; "
              f"deviation {dev:.2f}%")
        if implied and dev > 15:
            raise SystemExit(f"INVARIANT: {yy} tax booked {got} vs implied "
                             f"{implied} ({dev:.1f}%)")

    # A3: every schedule's cursor is its latest posted instance.
    gc = piecash.open_book(str(out_path), readonly=True, open_if_lock=True)
    try:
        ledger = _ledger_txns(gc)
        bad = []
        for sx in gc.session.query(piecash.ScheduledTransaction).all():
            tmpl_txns = {sp.transaction for sp in sx.template_account.splits}
            desc = next(iter(tmpl_txns)).description if tmpl_txns else sx.name
            inst = _sx_instances(ledger, desc)
            want = max((t.post_date for t in inst), default=None)
            have = sx.last_occur
            if hasattr(have, "date") and have is not None:
                have = have.date()
            if want != have:
                bad.append((sx.name, have, want))
        n_sx = gc.session.query(piecash.ScheduledTransaction).count()
    finally:
        gc.close()
    print(f"  schedules: {n_sx} cursors == latest posted instance"
          f"{' OK' if not bad else ' MISMATCH ' + str(bad)}")
    if bad:
        raise SystemExit("INVARIANT: last_occur != latest posted instance")


def verify(out_path: Path, business: dict, tax_summary: dict | None = None) -> None:
    print("\n" + "=" * 64)
    print("VERIFICATION")
    print("=" * 64)
    book = GnuCashBook(str(out_path))
    _verify_invariants(out_path, tax_summary or {})

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

    # Investment holdings via real security prices. Round-lot check: every
    # A-share / ETF position must be a whole multiple of 一手 (100).
    print("\n-- Investment holdings (round-lot check) --")
    print(f"  CATL latest real price: ¥{md_security('300750', as_of)}")
    for path in (CATL, CSI300, CHINEXT):
        bal = Decimal(str(book.get_balance(path)))
        round_lot = (bal == bal.to_integral_value()) and bal % 100 == 0
        print(f"  {path}: shares {bal}  round_lot={round_lot}")
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
        n_txn = len(_ledger_txns(b))
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
    Card statements, taxes and sweeps are deliberately absent — the
    policy layer derives payments from the book itself, and
    hsbc_payoff_repair settles the HKD card in narrative."""
    global THROUGH
    THROUGH = through
    return (gen_recurring() + gen_daily_weekly() + gen_personal_life()
            + gen_hsbc_charges() + gen_volume())


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
        pairs.append((sym, through))  # §5: dated at the horizon
    for sym in SECURITY_MNEMONICS:
        pairs.append((sym, through))
    # The re-created open cross-currency documents post on
    # THROUGH-relative dates; each needs a same-day quote.
    pairs.extend(open_document_fx_dates())
    return _add_price_rows(out_path, pairs)


def ensure_rate(out_path: Path, currency: str, when: date) -> None:
    """Real FX close for a cross-currency settlement date (no-op when
    a rate for that date is already on file)."""
    _add_price_rows(out_path, [(currency, when)])


def continuation_invest(out_path: Path, when: date, amount: Decimal,
                        source_path: str) -> None:
    """Policy-layer 沪深300 purchase: whole round lots at the real
    close, one lot per purchase, mirroring the DCA lot pattern. Source
    is 银行储蓄卡 (surplus sweep) or 储蓄账户 (pile rebalance)."""
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
    """Register the employee if missing (idempotent — the part-time
    assistant from bookkeeper review §3)."""
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
        # Both CNY cards are paid in full at every statement (audit P1).
        # The base build's 2025 arc — the ICBC opening arrears paid down
        # ¥1,500 a cycle with interest — is run_credit_cards' narrative;
        # from the frozen edge onward the continuation pays the true
        # statement balance.
        CardPolicy(account=CMB_CARD, label="招商银行信用卡", kind="pif",
                   close_day_default=25),
        CardPolicy(account=ICBC_CARD, label="工商银行信用卡", kind="pif",
                   close_day_default=20),
        # 汇丰 HKD card: statements are 购汇 repayments (a cross-currency
        # write the engine doesn't do) — run_hsbc_statements in the base
        # build, hsbc_payoff_repair in continuation.
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
    # 储蓄账户 earns a demand-deposit rate, monthly (audit P7).
    savings_apy=D("0.015"),
    interest_income=INTEREST_INCOME,
    desc_statement="{label} 还款",
    desc_repair_card="{label} 还款（清理累积欠款）",
    desc_sweep="转入储蓄账户（月度结余）",
    desc_repair_sweep="转入储蓄账户（结余归集）",
    desc_topup="储蓄账户转入（补足日常余额）",
    desc_savings_interest="储蓄账户 利息",
    desc_interest="{label} 利息",
)


def run_base_policy(out_path: Path) -> list[str]:
    """Run the closed-loop policy over the WHOLE base timeline — surplus
    sweeps (savings + quarterly 沪深300), the savings-pile rebalance,
    savings interest — from 2025-01-01. Card statements are paid by
    run_credit_cards (the 2025 narrative needs the ICBC catch-up arc),
    so the cards are masked here; everything else is the exact rule
    set the continuation applies from the frozen edge onward."""
    from dataclasses import replace

    from continuation import run_policy

    base_policy = replace(POLICY, cards=())
    return run_policy(base_policy, out_path,
                      date(YEAR, 1, 1) - timedelta(days=1), THROUGH)


# ── Driver ──────────────────────────────────────────────────────

def build_base(out_path: Path) -> None:
    """Commodities, chart, account slots — nothing dated."""
    print(f"Building Lin Wei base at: {out_path}")
    print("\nPhase 1: book file + commodities")
    create_book_file(out_path)
    print("\nPhase 2: chart of accounts")
    n_acct = create_accounts(out_path)
    print(f"  {n_acct} accounts created")
    set_account_slots(GnuCashBook(str(out_path)))
    print("  account slots set")


def build(out_path: Path) -> None:
    build_base(out_path)
    print("\nPhase 1b: prices")
    n_prices = add_prices(out_path)
    print(f"  {n_prices} prices created")
    book = GnuCashBook(str(out_path))

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

    print("\nPhase 7: business module (every client through A/R)")
    business = run_business(book)
    print(f"  {business}")

    print("\nPhase 8: investments")
    inv_counts = run_investments(out_path)
    print(f"  {inv_counts}")

    print("\nPhase 9a: HSBC HKD card charges")
    n = write_bulk(out_path, gen_hsbc_charges())
    print(f"  {n} HKD charges")

    print("\nPhase 10: budget")
    run_budget(book)
    print("  budget created")

    print("\nPhase 12: edge cases")
    edge = run_edge_cases(book, out_path)
    print(f"  {edge}")

    print("\nPhase 13: volume stress")
    n = write_bulk(out_path, gen_volume())
    print(f"  {n} volume transactions")

    # Everything below READS the book: taxes from the posted revenue and
    # 经营支出, statements from the real running card balances, sweeps
    # from the household's real surplus.
    print("\nPhase 14: taxes computed from the ledger")
    tax_summary = run_taxes(out_path)
    for yy in years_in_range():
        s_ = tax_summary[yy]
        print(f"  {yy}: VAT+附加 {s_['vat']:,.2f}; 经营所得 prepaid "
              f"{s_['pit_prepaid']:,.2f}, 汇算清缴 {s_['pit_settlement']:,.2f}"
              f" (Q1–Q{s_['quarters_filed']})")
    print(f"  {tax_summary['written']} tax transactions")

    print("\nPhase 9b: HSBC statements paid in full by 购汇")
    n = run_hsbc_statements(out_path)
    print(f"  {n} HKD statement payments")

    print("\nPhase 9c: CNY card statements (computed from the book)")
    n = run_credit_cards(out_path)
    print(f"  {n} statement payments / interest")

    print("\nPhase 7d: closed-loop policy — surplus sweeps, savings interest")
    actions = run_base_policy(out_path)
    print(f"  {len(actions)} policy actions; last 4:")
    for line in actions[-4:]:
        print(f"    {line}")

    print("\nPhase 11: reconciliation posture (through the last full month)")
    for line in run_reconciliation(out_path):
        print(f"  {line}")

    print("\nEntry timestamps")
    n = stamp_entry_dates(out_path)
    print(f"  {n} transactions entered on their post date")

    print("\nScheduled-transaction state (cursor = latest posted instance)")
    sx_state = set_schedule_state(out_path)
    print(f"  {sx_state}")

    print("\nContinuation invariants over the base timeline")
    from continuation import verify_invariants
    warnings = verify_invariants(POLICY, out_path,
                                 date(YEAR, 1, 1) - timedelta(days=1), THROUGH)
    for w in warnings:
        print(f"  WARN: {w}")
    if not warnings:
        print("  clean")

    verify(out_path, business, tax_summary)
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
    parser.add_argument(
        "--chart-only", action="store_true",
        help="Write the chart-only base (commodities, accounts, slots; "
             "nothing dated), VACUUMed, and stop.")
    args = parser.parse_args()
    if args.through:
        THROUGH = date.fromisoformat(args.through)
        if THROUGH < date(YEAR, 1, 1):
            raise SystemExit(
                f"--through {THROUGH} precedes the book start {YEAR}-01-01")
    out_path = Path(args.out).resolve()
    if out_path == PROTECTED.resolve():
        raise SystemExit(f"REFUSING to write to protected book: {PROTECTED}")
    if args.chart_only:
        from base_book import vacuum
        build_base(out_path)
        print(f"  base VACUUMed: {vacuum(out_path):,} bytes")
        return
    print(f"Activity timeline runs 2025-01-01 → THROUGH={THROUGH}")
    build(out_path)


if __name__ == "__main__":
    main()
