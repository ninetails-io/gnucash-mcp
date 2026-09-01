"""Build the Sabine Brenner synthetic book — a German SKR03 persona.

Sabine Brenner, Munich freelance Grafikdesignerin (Einzelunternehmerin,
Regelbesteuerung, EÜR). EUR default; authentic DATEV **SKR03** chart
(German, numbered) so the i18n bug class — every tool that keys off an
English account name — becomes a failing test. A USD-paying client
forces cross-currency invoicing (the FX acceptance test); German VAT
(USt 19%/7%) runs through taxtables; a Hypothek drives the debt-payoff
path; a localized Ausgleichskonto exercises the Tier-C warning.

Deterministic (fixed per-phase seeds). Date-pinnable for a reproducible
run; defaults to today so the sample stays current.

    uv run python scripts/synthetic_book/build_sabine.py --out /tmp/sb.gnucash
    uv run python scripts/synthetic_book/build_sabine.py --out /tmp/sb.gnucash --through 2026-06-30

NEVER writes to the protected sample (samples/sabine-brenner.gnucash).
"""

from __future__ import annotations

import argparse
import calendar
import os
import random
from datetime import date, timedelta
from decimal import Decimal as D, ROUND_HALF_UP
from pathlib import Path

import piecash

from gnucash_mcp.book import GnuCashBook
from market_data import MarketData

SEED = 20250101
YEAR = 2025
THROUGH = date.today()

ROOT = Path(__file__).resolve().parents[2]
PROTECTED = ROOT / "samples" / "sabine-brenner.gnucash"
DEFAULT_OUT = ROOT / "samples" / "sabine-brenner.generated.gnucash"

MD = MarketData.load()
ETF_MNEMONIC = "IWDA.AS"

# ── Account path constants (authentic SKR03 unless marked ADD) ──────
FIN = "Aktiva:Finanzkonten 1"
ANL = "Aktiva:Anlage- u. Kapitalkonten 0"
BANKKONTO = f"{FIN}:1200 Bankkonto"
POSTBANK = f"{FIN}:1100 Postbank"
AR = f"{FIN}:1400 Ford. a. Lieferungen und Leistungen"
AR_USD = f"{FIN}:1407 Ford. a. Lief. u. Leist. USD"          # ADD
VST19 = f"{FIN}:1576 Abziehbare VSt. 19%"
AP = "Passiva:Verbindlichkeiten:1600 Verblk. aus Lieferungen u. Leistungen"
REV19 = "Erlöse u. Erträge 2/8:Erlöskonten 8:8400 Erlöse USt. 19%"
REV7 = "Erlöse u. Erträge 2/8:Erlöskonten 8:8300 Erlöse USt. 7%"
REV_EXPORT = "Erlöse u. Erträge 2/8:Erlöskonten 8:8120 Steuerfreie Umsätze §4 Nr. 1a UStG"  # ADD
UST19 = "Passiva:Umsatzsteuer:1776 Umsatzsteuer 19%"
UST7 = "Passiva:Umsatzsteuer:1771 Umsatzsteuer 7%"
UST_VZ = "Passiva:Umsatzsteuer:1780 Umsatzsteuer-Vorauszahlung"
OPENING = "Anfangsbestand 9:Saldenvortragskonten:9000 Saldenvortrag Sachkonten"
PRIV_DRAW = "Privatkonten 1:Privatentnahmen/-einlagen:1800 Privatentnahme allgemein"
MIETE = "Aufwendungen 2/4:Raumkosten:4210 Miete und Nebenkosten"
TELEKOM = "Aufwendungen 2/4:verschiedene Kosten:4920 Telekom"
MOBILFUNK = "Aufwendungen 2/4:verschiedene Kosten:4921 Mobilfunk D2"
INTERNET = "Aufwendungen 2/4:verschiedene Kosten:4922 Internet"
BUEROBEDARF = "Aufwendungen 2/4:verschiedene Kosten:4930 Bürobedarf"
PORTO = "Aufwendungen 2/4:verschiedene Kosten:4910 Porto"
STEUERBERATER = "Aufwendungen 2/4:verschiedene Kosten:4955 Buchführungskosten"
FORTBILDUNG = "Aufwendungen 2/4:verschiedene Kosten:4945 Fortbildungskosten"
WERBUNG = "Aufwendungen 2/4:Werbe-/Reisekosten:4610 Werbekosten"
REISE = "Aufwendungen 2/4:Werbe-/Reisekosten:4670 Reisekosten Unternehmer"
GWG = "Aufwendungen 2/4:Abschreibungen:4855 Sofortabschreibung GWG"
VERSICHERUNG = "Aufwendungen 2/4:Versicherungsbeiträge:4360 Versicherungen"
BANKGEBUEHR = "Aufwendungen 2/4:verschiedene Kosten:4970 Nebenkosten des Geldverkehrs"
ZINS_HYP = "Aufwendungen 2/4:Zinsaufwendungen:2110 Zinsaufwendungen für kurzfristige Verbindlichkeiten"
ZINS_KFZ = "Aufwendungen 2/4:Zinsaufwendungen:2121 Zinsaufwendungen für KFZ Finanzierung"
STROM = "Aufwendungen 2/4:Raumkosten:4240 Gas, Wasser, Strom (Verwaltung, Vertrieb)"
WERKZEUG = "Aufwendungen 2/4:verschiedene Kosten:4985 Werkzeuge und Kleingeräte"
AUFMERK = "Aufwendungen 2/4:Werbe-/Reisekosten:4653 Aufmerksamkeiten"
BUECHER = "Aufwendungen 2/4:verschiedene Kosten:4940 Zeitschriften, Bücher"
KFZ_BETRIEB = "Aufwendungen 2/4:Kfz-Kosten:4530 laufende Kfz-Betriebskosten"
KFZ_STEUER = "Aufwendungen 2/4:Kfz-Kosten:4510 Kfz-Steuer"
KFZ_VERS = "Aufwendungen 2/4:Kfz-Kosten:4520 Kfz-Versicherungen"
PRIV_EINLAGE = "Privatkonten 1:Privatentnahmen/-einlagen:1890 Privateinlagen"
# Additions (under authentic German parents)
HYPOTHEK = "Passiva:Verbindlichkeiten:0630 Verbindlichkeiten ggü. Kreditinstituten"
KFZ_FIN = "Passiva:Verbindlichkeiten:0640 Kfz-Finanzierung"
WOHNUNG = f"{ANL}:0090 Eigentumswohnung"
PKW = f"{ANL}:0320 Pkw"
DEPOT = f"{ANL}:0700 Wertpapierdepot"
ETF = f"{ANL}:0700 Wertpapierdepot:0701 MSCI World ETF"
AUSGLEICH = "Ausgleichskonto-EUR"   # root-level BANK — localized Imbalance (Tier C)

# ── Phase 2: chart = authentic SKR03 (100) + additions ─────────────
SKR03 = [
    ('Aktiva', 'ASSET', None, "EUR", "CURRENCY", True),
    ('Anfangsbestand 9', 'EQUITY', None, "EUR", "CURRENCY", True),
    ('Aufwendungen 2/4', 'EXPENSE', None, "EUR", "CURRENCY", True),
    ('Erlöse u. Erträge 2/8', 'INCOME', None, "EUR", "CURRENCY", True),
    ('Passiva', 'LIABILITY', None, "EUR", "CURRENCY", True),
    ('Privatkonten 1', 'EQUITY', None, "EUR", "CURRENCY", True),
    ('Anlage- u. Kapitalkonten 0', 'ASSET', 'Aktiva', "EUR", "CURRENCY", True),
    ('Finanzkonten 1', 'ASSET', 'Aktiva', "EUR", "CURRENCY", True),
    ('Wareneingangs- u. Bestandskonten 3', 'ASSET', 'Aktiva', "EUR", "CURRENCY", True),
    ('Saldenvortragskonten', 'EQUITY', 'Anfangsbestand 9', "EUR", "CURRENCY", True),
    ('Abschreibungen', 'EXPENSE', 'Aufwendungen 2/4', "EUR", "CURRENCY", True),
    ('Kfz-Kosten', 'EXPENSE', 'Aufwendungen 2/4', "EUR", "CURRENCY", True),
    ('Personalkosten', 'EXPENSE', 'Aufwendungen 2/4', "EUR", "CURRENCY", True),
    ('Raumkosten', 'EXPENSE', 'Aufwendungen 2/4', "EUR", "CURRENCY", True),
    ('Reparatur/Instandhaltung', 'EXPENSE', 'Aufwendungen 2/4', "EUR", "CURRENCY", True),
    ('Versicherungsbeiträge', 'EXPENSE', 'Aufwendungen 2/4', "EUR", "CURRENCY", True),
    ('Werbe-/Reisekosten', 'EXPENSE', 'Aufwendungen 2/4', "EUR", "CURRENCY", True),
    ('Zinsaufwendungen', 'EXPENSE', 'Aufwendungen 2/4', "EUR", "CURRENCY", True),
    ('verschiedene Kosten', 'EXPENSE', 'Aufwendungen 2/4', "EUR", "CURRENCY", True),
    ('Erlöskonten 8', 'INCOME', 'Erlöse u. Erträge 2/8', "EUR", "CURRENCY", True),
    ('Ertragskonten 2', 'INCOME', 'Erlöse u. Erträge 2/8', "EUR", "CURRENCY", True),
    ('Umsatzsteuer', 'LIABILITY', 'Passiva', "EUR", "CURRENCY", True),
    ('Verbindlichkeiten', 'LIABILITY', 'Passiva', "EUR", "CURRENCY", True),
    ('Privatentnahmen/-einlagen', 'EQUITY', 'Privatkonten 1', "EUR", "CURRENCY", True),
    ('0027 EDV-Software', 'ASSET', 'Aktiva:Anlage- u. Kapitalkonten 0', "EUR", "CURRENCY", False),
    ('0210 Maschinen', 'ASSET', 'Aktiva:Anlage- u. Kapitalkonten 0', "EUR", "CURRENCY", False),
    ('0400 Betriebsausstattung', 'ASSET', 'Aktiva:Anlage- u. Kapitalkonten 0', "EUR", "CURRENCY", False),
    ('0410 Geschäftsausstattung', 'ASSET', 'Aktiva:Anlage- u. Kapitalkonten 0', "EUR", "CURRENCY", False),
    ('0420 Büroeinrichtung', 'ASSET', 'Aktiva:Anlage- u. Kapitalkonten 0', "EUR", "CURRENCY", False),
    ('0430 Ladeneinrichtung', 'ASSET', 'Aktiva:Anlage- u. Kapitalkonten 0', "EUR", "CURRENCY", False),
    ('0565 Darlehen', 'ASSET', 'Aktiva:Anlage- u. Kapitalkonten 0', "EUR", "CURRENCY", False),
    ('1100 Postbank', 'BANK', 'Aktiva:Finanzkonten 1', "EUR", "CURRENCY", False),
    ('1200 Bankkonto', 'BANK', 'Aktiva:Finanzkonten 1', "EUR", "CURRENCY", False),
    ('1371 Gewinnermittlung §4/3 nicht Ergebniswirksam', 'ASSET', 'Aktiva:Finanzkonten 1', "EUR", "CURRENCY", False),
    ('1400 Ford. a. Lieferungen und Leistungen', 'RECEIVABLE', 'Aktiva:Finanzkonten 1', "EUR", "CURRENCY", False),
    ('1571 Abziehbare VSt. 7%', 'ASSET', 'Aktiva:Finanzkonten 1', "EUR", "CURRENCY", False),
    ('1576 Abziehbare VSt. 19%', 'ASSET', 'Aktiva:Finanzkonten 1', "EUR", "CURRENCY", False),
    ('1577 Abziehbare VStr. nach §13b UStG 19%', 'ASSET', 'Aktiva:Finanzkonten 1', "EUR", "CURRENCY", False),
    ('1590 Durchlaufende Posten', 'ASSET', 'Aktiva:Finanzkonten 1', "EUR", "CURRENCY", False),
    ('3120 Leistungen §13b UStG 19% Vorsteuer, 19% Umsatzsteuer', 'ASSET', 'Aktiva:Wareneingangs- u. Bestandskonten 3', "EUR", "CURRENCY", False),
    ('3400 Wareneingang VSt. 19%', 'ASSET', 'Aktiva:Wareneingangs- u. Bestandskonten 3', "EUR", "CURRENCY", False),
    ('9000 Saldenvortrag Sachkonten', 'EQUITY', 'Anfangsbestand 9:Saldenvortragskonten', "EUR", "CURRENCY", False),
    ('9008 Saldenvorträge Debitoren', 'EQUITY', 'Anfangsbestand 9:Saldenvortragskonten', "EUR", "CURRENCY", False),
    ('9009 Saldenvorträge Kreditoren', 'EQUITY', 'Anfangsbestand 9:Saldenvortragskonten', "EUR", "CURRENCY", False),
    ('4855 Sofortabschreibung GWG', 'EXPENSE', 'Aufwendungen 2/4:Abschreibungen', "EUR", "CURRENCY", False),
    ('4510 Kfz-Steuer', 'EXPENSE', 'Aufwendungen 2/4:Kfz-Kosten', "EUR", "CURRENCY", False),
    ('4520 Kfz-Versicherungen', 'EXPENSE', 'Aufwendungen 2/4:Kfz-Kosten', "EUR", "CURRENCY", False),
    ('4530 laufende Kfz-Betriebskosten', 'EXPENSE', 'Aufwendungen 2/4:Kfz-Kosten', "EUR", "CURRENCY", False),
    ('4540 Kfz-Reparaturen', 'EXPENSE', 'Aufwendungen 2/4:Kfz-Kosten', "EUR", "CURRENCY", False),
    ('4570 Fremdfahrzeuge', 'EXPENSE', 'Aufwendungen 2/4:Kfz-Kosten', "EUR", "CURRENCY", False),
    ('4580 sonstige Kfz-Kosten', 'EXPENSE', 'Aufwendungen 2/4:Kfz-Kosten', "EUR", "CURRENCY", False),
    ('4120 Gehälter', 'EXPENSE', 'Aufwendungen 2/4:Personalkosten', "EUR", "CURRENCY", False),
    ('4130 gesetzliche soziale Aufwendungen', 'EXPENSE', 'Aufwendungen 2/4:Personalkosten', "EUR", "CURRENCY", False),
    ('4165 Aufwendungen für Altersvorsorge', 'EXPENSE', 'Aufwendungen 2/4:Personalkosten', "EUR", "CURRENCY", False),
    ('4170 Vermögenswirksame Leistungen', 'EXPENSE', 'Aufwendungen 2/4:Personalkosten', "EUR", "CURRENCY", False),
    ('4190 Aushilfslöhne', 'EXPENSE', 'Aufwendungen 2/4:Personalkosten', "EUR", "CURRENCY", False),
    ('4210 Miete und Nebenkosten', 'EXPENSE', 'Aufwendungen 2/4:Raumkosten', "EUR", "CURRENCY", False),
    ('4240 Gas, Wasser, Strom (Verwaltung, Vertrieb)', 'EXPENSE', 'Aufwendungen 2/4:Raumkosten', "EUR", "CURRENCY", False),
    ('4250 Reinigung', 'EXPENSE', 'Aufwendungen 2/4:Raumkosten', "EUR", "CURRENCY", False),
    ('4805 Reparatur u. Instandh. von Anlagen/Maschinen u. Betriebs- u. Geschäftsausst.', 'EXPENSE', 'Aufwendungen 2/4:Reparatur/Instandhaltung', "EUR", "CURRENCY", False),
    ('4360 Versicherungen', 'EXPENSE', 'Aufwendungen 2/4:Versicherungsbeiträge', "EUR", "CURRENCY", False),
    ('4380 Beiträge', 'EXPENSE', 'Aufwendungen 2/4:Versicherungsbeiträge', "EUR", "CURRENCY", False),
    ('4390 sonstige Ausgaben', 'EXPENSE', 'Aufwendungen 2/4:Versicherungsbeiträge', "EUR", "CURRENCY", False),
    ('4396 steuerlich abzugsfähige Verspätungszuschläge und Zwangsgelder', 'EXPENSE', 'Aufwendungen 2/4:Versicherungsbeiträge', "EUR", "CURRENCY", False),
    ('4610 Werbekosten', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
    ('4653 Aufmerksamkeiten', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
    ('4665 nicht abzugsfähige Betriebsausg. aus Werbe-, Repräs.- u. Reisekosten', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
    ('4670 Reisekosten Unternehmer', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
    ('2110 Zinsaufwendungen für kurzfristige Verbindlichkeiten', 'EXPENSE', 'Aufwendungen 2/4:Zinsaufwendungen', "EUR", "CURRENCY", False),
    ('2121 Zinsaufwendungen für KFZ Finanzierung', 'EXPENSE', 'Aufwendungen 2/4:Zinsaufwendungen', "EUR", "CURRENCY", False),
    ('4910 Porto', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
    ('4920 Telekom', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
    ('4921 Mobilfunk D2', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
    ('4922 Internet', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
    ('4930 Bürobedarf', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
    ('4940 Zeitschriften, Bücher', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
    ('4945 Fortbildungskosten', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
    ('4955 Buchführungskosten', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
    ('4957 Abschluß- u. Prüfungskosten', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
    ('4970 Nebenkosten des Geldverkehrs', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
    ('4985 Werkzeuge und Kleingeräte', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
    ('8300 Erlöse USt. 7%', 'INCOME', 'Erlöse u. Erträge 2/8:Erlöskonten 8', "EUR", "CURRENCY", False),
    ('8400 Erlöse USt. 19%', 'INCOME', 'Erlöse u. Erträge 2/8:Erlöskonten 8', "EUR", "CURRENCY", False),
    ('2500 Außerordentliche Erträge', 'INCOME', 'Erlöse u. Erträge 2/8:Ertragskonten 2', "EUR", "CURRENCY", False),
    ('2650 sonstige Zinsen und ähnliche Erträge', 'INCOME', 'Erlöse u. Erträge 2/8:Ertragskonten 2', "EUR", "CURRENCY", False),
    ('2700 Sonstige Erträge', 'INCOME', 'Erlöse u. Erträge 2/8:Ertragskonten 2', "EUR", "CURRENCY", False),
    ('1771 Umsatzsteuer 7%', 'LIABILITY', 'Passiva:Umsatzsteuer', "EUR", "CURRENCY", False),
    ('1776 Umsatzsteuer 19%', 'LIABILITY', 'Passiva:Umsatzsteuer', "EUR", "CURRENCY", False),
    ('1780 Umsatzsteuer-Vorauszahlung', 'LIABILITY', 'Passiva:Umsatzsteuer', "EUR", "CURRENCY", False),
    ('1781 Umsatzsteuer-Vorauszahlung 1/11', 'LIABILITY', 'Passiva:Umsatzsteuer', "EUR", "CURRENCY", False),
    ('1787 Umsatzsteuer § 13b UStG 19%', 'LIABILITY', 'Passiva:Umsatzsteuer', "EUR", "CURRENCY", False),
    ('1790 Umsatzsteuer Vorjahr', 'LIABILITY', 'Passiva:Umsatzsteuer', "EUR", "CURRENCY", False),
    ('1791 Umsatzsteuer frühere Jahre', 'LIABILITY', 'Passiva:Umsatzsteuer', "EUR", "CURRENCY", False),
    ('1600 Verblk. aus Lieferungen u. Leistungen', 'PAYABLE', 'Passiva:Verbindlichkeiten', "EUR", "CURRENCY", False),
    ('1800 Privatentnahme allgemein', 'EQUITY', 'Privatkonten 1:Privatentnahmen/-einlagen', "EUR", "CURRENCY", False),
    ('1810 Privatsteuern', 'EQUITY', 'Privatkonten 1:Privatentnahmen/-einlagen', "EUR", "CURRENCY", False),
    ('1820 Sonderausgaben beschränkt abzugsfähig', 'EQUITY', 'Privatkonten 1:Privatentnahmen/-einlagen', "EUR", "CURRENCY", False),
    ('1830 Sonderausgaben unbeschränkt abzugsfähig', 'EQUITY', 'Privatkonten 1:Privatentnahmen/-einlagen', "EUR", "CURRENCY", False),
    ('1850 Außergewöhnliche Belastungen', 'EQUITY', 'Privatkonten 1:Privatentnahmen/-einlagen', "EUR", "CURRENCY", False),
    ('1890 Privateinlagen', 'EQUITY', 'Privatkonten 1:Privatentnahmen/-einlagen', "EUR", "CURRENCY", False),
]

ADDITIONS = [
    # leaf, type, parent_path, comm, ns, placeholder
    ('1407 Ford. a. Lief. u. Leist. USD', 'RECEIVABLE', 'Aktiva:Finanzkonten 1', "USD", "CURRENCY", False),
    ('8120 Steuerfreie Umsätze §4 Nr. 1a UStG', 'INCOME', 'Erlöse u. Erträge 2/8:Erlöskonten 8', "EUR", "CURRENCY", False),
    ('0090 Eigentumswohnung', 'ASSET', 'Aktiva:Anlage- u. Kapitalkonten 0', "EUR", "CURRENCY", False),
    ('0320 Pkw', 'ASSET', 'Aktiva:Anlage- u. Kapitalkonten 0', "EUR", "CURRENCY", False),
    ('0700 Wertpapierdepot', 'ASSET', 'Aktiva:Anlage- u. Kapitalkonten 0', "EUR", "CURRENCY", True),
    ('0701 MSCI World ETF', 'MUTUAL', 'Aktiva:Anlage- u. Kapitalkonten 0:0700 Wertpapierdepot', ETF_MNEMONIC, "FUND", False),
    ('0630 Verbindlichkeiten ggü. Kreditinstituten', 'LIABILITY', 'Passiva:Verbindlichkeiten', "EUR", "CURRENCY", False),
    ('0640 Kfz-Finanzierung', 'LIABILITY', 'Passiva:Verbindlichkeiten', "EUR", "CURRENCY", False),
    # Localized auto-balancing account (Tier C): root-level BANK, German word.
    ('Ausgleichskonto-EUR', 'BANK', None, "EUR", "CURRENCY", False),
]

ACCOUNTS = SKR03 + ADDITIONS


# ── price helpers ──────────────────────────────────────────────────
def eur_per_usd(when: date) -> D:
    """EUR per 1 USD = 1 / (USD per EUR from the cached EUR/USD series)."""
    usd_per_eur = MD.fx("EUR", "USD", when)   # base USD per foreign EUR
    return (D("1") / usd_per_eur).quantize(D("0.0001"), ROUND_HALF_UP)


def etf_price(when: date) -> D:
    """Deterministic synthetic IWDA.AS series in EUR (no cache entry)."""
    months = (when.year - 2025) * 12 + (when.month - 1)
    base = D("85.00") * (D("1.004") ** months)
    rng = random.Random(SEED + 777 + months)
    jitter = D(str(round(rng.uniform(-0.8, 0.8), 2)))
    return (base + jitter).quantize(D("0.0001"), ROUND_HALF_UP)


def price_months() -> list[date]:
    out, y, m = [], 2025, 1
    while (y, m) <= (max(THROUGH, date(YEAR, 1, 1)).year,
                     max(THROUGH, date(YEAR, 1, 1)).month):
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# Cross-currency invoice post/pay dates that need a fresh USD rate.
US_POST = date(YEAR, 9, 1)
US_PAY = date(YEAR, 10, 1)


# ── Phase 1: book + commodities + prices ───────────────────────────
def create_book_file(out_path: Path) -> None:
    if out_path.resolve() == PROTECTED.resolve():
        raise SystemExit(f"REFUSING to write protected book: {PROTECTED}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    book = piecash.create_book(
        sqlite_file=str(out_path), currency="EUR", overwrite=True)
    try:
        book.currencies(mnemonic="USD")
        piecash.Commodity(namespace="FUND", mnemonic=ETF_MNEMONIC,
                          fullname="iShares Core MSCI World UCITS ETF",
                          fraction=10000, book=book)
        book.save()
    finally:
        book.close()


def add_prices(out_path: Path) -> int:
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    n = 0
    try:
        eur = book.default_currency
        usd = next(c for c in book.commodities if c.mnemonic == "USD")
        etf = next(c for c in book.commodities if c.mnemonic == ETF_MNEMONIC)
        # Closing point AT the horizon (bookkeeper review §5): a fresh
        # build opens without a stale-price warning.
        usd_dates = set(price_months()) | {US_POST, US_PAY, THROUGH}
        etf_dates = set(price_months()) | {date(YEAR, 1, 1), THROUGH}
        for when in sorted(usd_dates):
            piecash.Price(commodity=usd, currency=eur, date=when,
                          value=eur_per_usd(when), type="last",
                          source="user:market-data")
            n += 1
        for when in sorted(etf_dates):
            piecash.Price(commodity=etf, currency=eur, date=when,
                          value=etf_price(when), type="last",
                          source="user:synthetic")
            n += 1
        book.save()
    finally:
        book.close()
    return n


# ── Phase 2: chart ─────────────────────────────────────────────────
def create_accounts(out_path: Path) -> int:
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    n = 0
    try:
        comm_by = {(c.namespace, c.mnemonic): c for c in book.commodities}
        comm_by[("CURRENCY", "EUR")] = book.default_currency
        by_path: dict[str, piecash.Account] = {}
        for name, atype, parent_path, mn, ns, ph in ACCOUNTS:
            parent = (book.root_account if parent_path is None
                      else by_path[parent_path])
            acct = piecash.Account(name=name, type=atype, parent=parent,
                                   commodity=comm_by[(ns, mn)], placeholder=ph)
            full = name if parent_path is None else f"{parent_path}:{name}"
            by_path[full] = acct
            n += 1
        book.save()
    finally:
        book.close()
    return n


def set_account_slots(book: GnuCashBook) -> None:
    book.set_account_slot(HYPOTHEK, "apr", "3.65")
    book.set_account_slot(HYPOTHEK, "loan_term_months", "300")
    book.set_account_slot(KFZ_FIN, "apr", "4.49")
    book.set_account_slot(KFZ_FIN, "loan_term_months", "60")
    # Loans and VAT clearing accounts opt out of the reconciliation
    # surface — no statement exists to reconcile against (bookkeeper
    # review §1; USt settles via the monthly USt-VA, not a statement).
    for acct in (HYPOTHEK, KFZ_FIN, UST19, UST7):
        book.set_account_slot(acct, "no_reconcile", "true")


# ── Phase 3: opening balances + ETF lot ────────────────────────────
OPENING_BALANCES = [
    (BANKKONTO, D("18400")),
    (POSTBANK, D("3250")),
    (WOHNUNG, D("540000")),
    (PKW, D("24000")),
    (HYPOTHEK, D("-395000")),
    (KFZ_FIN, D("-16500")),
]
ETF_UNITS = D("95")


def run_investments(out_path: Path, since: date | None = None) -> int:
    """Monthly MSCI World ETF Sparplan — invests the freelancer's surplus
    (soaks idle cash) and exercises the investment/lot path. Each buy is
    its own lot for cost-basis tracking. ``since`` (continuation mode):
    skip buys dated on or before it — those lots exist in the prefix."""
    cut = since or date(YEAR, 1, 1) - timedelta(days=1)
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    n = 0
    try:
        eur = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        etf, bank = acct[ETF], acct[BANKKONTO]
        cost = D("1200.00")
        for first in iter_months():
            buy_day = day_in(first, 6)
            if buy_day <= cut or buy_day > THROUGH:
                continue
            price = etf_price(first)
            units = (cost / price).quantize(D("0.0001"), ROUND_HALF_UP)
            lot = piecash.Lot(title=f"Sparplan {first.isoformat()}", account=etf,
                              notes="MSCI World Sparplan", is_closed=0)
            isp = piecash.Split(account=etf, value=cost, quantity=units)
            bsp = piecash.Split(account=bank, value=-cost)
            piecash.Transaction(currency=eur, description="MSCI World ETF Sparplan (comdirect)",
                                post_date=day_in(first, 6), splits=[isp, bsp])
            isp.lot = lot
            n += 1
        book.save()
    finally:
        book.close()
    return n


def opening_balances(out_path: Path) -> None:
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    try:
        eur = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        jan1 = date(YEAR, 1, 1)
        splits, total = [], D("0")
        for path, bal in OPENING_BALANCES:
            splits.append(piecash.Split(account=acct[path], value=bal))
            total += bal
        splits.append(piecash.Split(account=acct[OPENING], value=-total))
        piecash.Transaction(currency=eur, description="Anfangsbestand 01.01.2025",
                            post_date=jan1, splits=splits)
        # ETF opening lot.
        etf_cost = (etf_price(jan1) * ETF_UNITS).quantize(D("0.01"), ROUND_HALF_UP)
        lot = piecash.Lot(title="MSCI World ETF — Sparplan-Bestand",
                          account=acct[ETF], notes="Eröffnungsbestand", is_closed=0)
        inv = piecash.Split(account=acct[ETF], value=etf_cost, quantity=ETF_UNITS)
        eqs = piecash.Split(account=acct[OPENING], value=-etf_cost)
        piecash.Transaction(currency=eur, description="Anfangsbestand — MSCI World ETF",
                            post_date=jan1, splits=[inv, eqs])
        inv.lot = lot
        book.save()
    finally:
        book.close()


# ── Phase 4: recurring (Miete, Telekom, Privatentnahme, loans) ─────
def _amort(P: D, apr: D, n_months: int):
    r = apr / D("100") / D("12")
    pmt = (P * r * (1 + r) ** n_months / ((1 + r) ** n_months - 1)
           ).quantize(D("0.01"), ROUND_HALF_UP)
    bal = P
    for _ in range(n_months):
        interest = (bal * r).quantize(D("0.01"), ROUND_HALF_UP)
        principal = pmt - interest
        bal -= principal
        yield pmt, interest, principal


def _vat_split(gross: D, rate: D):
    """(net, vat) for a gross amount at `rate` percent."""
    net = (gross / (1 + rate / D("100"))).quantize(D("0.01"), ROUND_HALF_UP)
    return net, gross - net


def iter_months():
    y, m = 2025, 1
    while (y, m) <= (THROUGH.year, THROUGH.month):
        yield date(y, m, 1)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _vat_expense(day, desc, account, gross, acct_from=BANKKONTO):
    """A business expense with 19% reclaimable input VAT (3-split)."""
    net, vat = _vat_split(gross, D("19"))
    return {"date": day, "description": desc,
            "splits": [(acct_from, -gross), (account, net), (VST19, vat)]}


def day_in(first: date, day: int) -> date:
    last = calendar.monthrange(first.year, first.month)[1]
    return first.replace(day=min(day, last))


def _pick(rng, names: list[str]) -> str:
    """A merchant string, sometimes with a German-style branch reference
    (Fil. = Filiale), as a real card-payment line reads."""
    name = rng.choice(names)
    return f"{name} Fil. {rng.randint(1, 999):04d}" if rng.random() < 0.45 else name


def _cents(rng, lo: int, hi: int) -> D:
    return (D(rng.randint(lo, hi)) + D(rng.randint(0, 99)) / D(100)).quantize(D("0.01"))


def _lumpy(rng, avg: int) -> int:
    """Lumpy monthly count: 0 in quiet months, spikes in busy ones."""
    return rng.randint(0, 2 * avg)


# 1830 Sonderausgaben — private health insurance is a Privatentnahme, not
# a business expense (Vorsorgeaufwendungen).
KRANKENKASSE = "Privatkonten 1:Privatentnahmen/-einlagen:1830 Sonderausgaben unbeschränkt abzugsfähig"

# Monthly SaaS/tooling — real product names, routed to authentic SKR03.
SUBSCRIPTIONS = [
    (WERKZEUG, "65.43", "Adobe Creative Cloud"),
    (WERKZEUG, "17.85", "Figma Professional"),
    (WERKZEUG, "29.74", "Adobe Stock"),
    (WERKZEUG, "11.89", "Monotype Fonts"),
    (BUEROBEDARF, "11.99", "Dropbox Business"),
    (INTERNET, "14.28", "All-Inkl Webhosting"),
    (BUEROBEDARF, "16.66", "sevDesk"),
    (WERKZEUG, "10.71", "Backblaze"),
]


def gen_recurring() -> list[dict]:
    """Fixed monthly business obligations + loans + USt-VA. Real
    Dauerauftrag-style descriptions, rent rising annually, utilities
    seasonal."""
    txns = []
    hyp = _amort(D("395000"), D("3.65"), 300)
    kfz = _amort(D("16500"), D("4.49"), 60)
    for first in iter_months():
        rent = D("1150") if first.year == 2025 else D("1190")  # Mieterhöhung
        txns.append({"date": first.replace(day=1), "description": "Dauerauftrag Miete Studio Schwabing",
                     "splits": [(BANKKONTO, -rent), (MIETE, rent)]})
        # Utilities: higher in winter (heating), lower in summer.
        winter = first.month in (11, 12, 1, 2, 3)
        strom = D("168.00") if winter else D("94.00")
        txns.append(_vat_expense(first.replace(day=4), "Stadtwerke München", STROM, strom))
        for path, gross, name in [(INTERNET, D("49.99"), "Telekom Deutschland"),
                                  (MOBILFUNK, D("39.99"), "Vodafone Mobilfunk"),
                                  (TELEKOM, D("24.99"), "1&1 Festnetz")]:
            txns.append(_vat_expense(first.replace(day=3), name, path, gross))
        for path, gross, name in SUBSCRIPTIONS:
            txns.append(_vat_expense(first.replace(day=2), name, path, D(gross)))
        txns.append(_vat_expense(first.replace(day=8), "Steuerkanzlei Hoffmann", STEUERBERATER, D("89.25")))
        txns.append({"date": first.replace(day=2), "description": "Kontoführungsgebühr",
                     "splits": [(BANKKONTO, D("-8.90")), (BANKGEBUEHR, D("8.90"))]})
        for gen, liab, zins, name, day in [
                (hyp, HYPOTHEK, ZINS_HYP, "Dauerauftrag Hypothek Sparkasse", 30),
                (kfz, KFZ_FIN, ZINS_KFZ, "VW Bank Kfz-Finanzierung", 15)]:
            try:
                pmt, interest, principal = next(gen)
                txns.append({"date": day_in(first, day), "description": name,
                             "splits": [(BANKKONTO, -pmt), (liab, principal), (zins, interest)]})
            except StopIteration:
                pass
        txns.append({"date": first.replace(day=10), "description": "Finanzamt München USt-Voranmeldung",
                     "splits": [(UST19, D("1850.00")), (BANKKONTO, D("-1850.00"))]})
    for first in iter_months():
        if first.month == 1:
            txns.append({"date": first.replace(day=20), "description": "Hauptzollamt Kfz-Steuer",
                         "splits": [(BANKKONTO, D("-184.00")), (KFZ_STEUER, D("184.00"))]})
            txns.append(_vat_expense(first.replace(day=22), "HUK-Coburg Kfz-Versicherung", KFZ_VERS, D("612.00")))
            txns.append(_vat_expense(first.replace(day=24), "VGH Berufshaftpflicht", VERSICHERUNG, D("428.00")))
    return txns


# Business spend: (account, lo, hi, avg-count/mo, merchant-key)
B_MERCHANTS = {
    "buero": ["Amazon.de", "Staples", "Office Discount", "McPaper München"],
    "werkzeug": ["MediaMarkt München", "Cyberport", "Apple Store München", "Gravis", "Amazon.de"],
    "reise": ["Deutsche Bahn", "DB Navigator", "MVG München", "Lufthansa", "Sixt", "FREENOW"],
    "aufmerk": ["L'Osteria", "Café Frischhut", "Vinzenzmurr", "Hofbräuhaus", "dean&david"],
    "werbung": ["Google Ads", "Meta Platforms", "LinkedIn Ads", "Flyeralarm"],
    "porto": ["Deutsche Post", "DHL Paket", "Hermes Versand", "DPD"],
    "buecher": ["Hugendubel", "Amazon.de", "PAGE Magazin", "Rheinwerk Verlag"],
    "tanken": ["SHELL München", "ARAL", "EnBW Ladestation", "Parkhaus Stachus", "ADAC"],
    "fortbildung": ["Domestika", "Skillshare", "TYPO Berlin", "Adobe MAX"],
    "gwg": ["MediaMarkt München", "Cyberport", "Apple Store München", "Gravis"],
}
BUSINESS = [
    (BUEROBEDARF, 8, 90, 4, "buero"),
    (WERKZEUG, 25, 350, 2, "werkzeug"),
    (REISE, 12, 220, 4, "reise"),
    (AUFMERK, 14, 95, 3, "aufmerk"),
    (WERBUNG, 30, 420, 1, "werbung"),
    (PORTO, 3, 30, 3, "porto"),
    (BUECHER, 10, 65, 1, "buecher"),
    (KFZ_BETRIEB, 30, 110, 4, "tanken"),
    (FORTBILDUNG, 60, 480, 1, "fortbildung"),
    (GWG, 120, 780, 1, "gwg"),
]


def gen_variable() -> list[dict]:
    """Lumpy seeded business spend with real merchant names (19% VSt)."""
    rng = random.Random(SEED + 6)
    txns = []
    for first in iter_months():
        for path, lo, hi, avg, key in BUSINESS:
            for _ in range(_lumpy(rng, avg)):
                acct = BANKKONTO if rng.random() < 0.7 else POSTBANK
                txns.append(_vat_expense(day_in(first, rng.randint(2, 27)),
                                         _pick(rng, B_MERCHANTS[key]), path,
                                         _cents(rng, lo, hi), acct))
    return txns


# Personal living — itemized Privatentnahme (1800) with real merchants, so
# the Girokonto reads like a real statement while the book stays strictly
# business-only (personal -> equity draw).
P_GROCERIES = ["REWE", "EDEKA", "LIDL", "ALDI Süd", "Vollcorner Bio", "dm-drogerie", "Rossmann"]
P_DINING = ["Hofbräuhaus", "L'Osteria", "Vapiano", "Wirtshaus zur Brez'n", "dean&david"]
P_COFFEE = ["Starbucks", "Café Glockenspiel", "Man Versus Machine", "Bäckerei Rischart", "Döner Imbiss Schwabing"]
P_HOUSE = ["IKEA Brunnthal", "Höffner", "MediaMarkt", "Amazon.de", "OBI Baumarkt"]
P_TRANSPORT = ["MVG München", "Deutsche Bahn", "FREENOW", "ARAL"]
P_MISC = ["Apotheke am Markt", "Friseur Schnittstelle", "Body & Soul Fitness", "Cinemaxx", "Müller Drogerie"]
P_SUBS = [("Netflix", "12.99"), ("Spotify", "10.99"), ("Amazon Prime", "8.99")]


def gen_personal() -> list[dict]:
    """Personal living + Krankenkasse, itemized as Privatentnahme draws."""
    rng = random.Random(SEED + 12)
    txns = []
    for first in iter_months():
        # Krankenkasse premium rises each January (Zusatzbeitrag/Beitragssatz).
        kk = D("781.53") if first.year == 2025 else D("812.40")
        txns.append({"date": day_in(first, 1), "description": "Techniker Krankenkasse Beitrag",
                     "splits": [(BANKKONTO, -kk), (KRANKENKASSE, kk)]})
        for name, amt in P_SUBS:
            txns.append({"date": day_in(first, 5), "description": name,
                         "splits": [(BANKKONTO, -D(amt)), (PRIV_DRAW, D(amt))]})
        for names, lo, hi, avg in [
                (P_GROCERIES, 12, 95, 8), (P_DINING, 14, 65, 4),
                (P_COFFEE, 3, 14, 7), (P_HOUSE, 25, 600, 1),
                (P_TRANSPORT, 3, 60, 3), (P_MISC, 12, 140, 2)]:
            for _ in range(_lumpy(rng, avg)):
                amt = _cents(rng, lo, hi)
                acct = BANKKONTO if rng.random() < 0.85 else POSTBANK
                txns.append({"date": day_in(first, rng.randint(2, 27)),
                             "description": _pick(rng, names),
                             "splits": [(acct, -amt), (PRIV_DRAW, amt)]})
        if rng.random() < 0.12:
            amt = _cents(rng, 600, 2500)
            txns.append({"date": day_in(first, rng.randint(2, 27)),
                         "description": "Privateinlage (Übertrag privat)",
                         "splits": [(BANKKONTO, amt), (PRIV_EINLAGE, -amt)]})
    return txns


CLIENTS = ["Verlag Bergblick", "Atelier Donau", "Stadtmarketing München", "BioBackhaus GmbH",
           "Praxis Dr. Vogel", "Architekturbüro Lindner", "Festival Tollwood", "Café Kosmos",
           "Brauerei Aukofer", "Modehaus Lindberg"]


def gen_honorar() -> list[dict]:
    """Direct (non-invoiced) Honorar deposits — the bulk of revenue, paid
    straight to the bank with output VAT (19%, occasionally 7% licensing).
    Lifted to a Munich senior-designer level so the Bankkonto stays solvent."""
    rng = random.Random(SEED + 9)
    txns = []
    for first in iter_months():
        for _ in range(rng.randint(4, 6)):
            net = D(rng.randint(800, 2800))
            seven = rng.random() < 0.2
            rate, rev, ust = (D("7"), REV7, UST7) if seven else (D("19"), REV19, UST19)
            vat = (net * rate / D("100")).quantize(D("0.01"))
            acct = BANKKONTO if rng.random() < 0.85 else POSTBANK
            kind = "Nutzungsrechte" if seven else "Honorar"
            txns.append({"date": day_in(first, rng.randint(2, 27)),
                         "description": f"{kind} {rng.choice(CLIENTS)}",
                         "splits": [(acct, net + vat), (rev, -net), (ust, -vat)]})
    return txns


# ── Phase 7: business — VAT invoicing + USD cross-currency + bill ──
def run_business(book: GnuCashBook) -> dict:
    counts = {"customers": 0, "vendors": 0, "invoices": 0, "bills": 0}
    book.create_billterm(name="Net 14", due_days=14, description="14 Tage netto")
    book.create_taxtable(name="USt 19%", entries=[
        {"type": "percentage", "amount": "19", "account": UST19}])
    book.create_taxtable(name="USt 7%", entries=[
        {"type": "percentage", "amount": "7", "account": UST7}])

    studio = book.create_customer(name="Verlag Bergblick GmbH", currency="EUR",
                                  notes="Editorial-Design, München")
    agentur = book.create_customer(name="Atelier Donau", currency="EUR",
                                   notes="Illustration / Nutzungsrechte")
    us = book.create_customer(name="Lumen Labs Inc.", currency="USD",
                              notes="US-Startup, Brand-Design (Export, steuerfrei)")
    counts["customers"] = 3
    vendor = book.create_vendor(name="Adobe Systems Software GmbH", currency="EUR",
                                notes="Creative Cloud Abo")
    counts["vendors"] = 1

    # German 19% services invoices, paid in EUR.
    for m in range(1, 9):
        net = D("2400")
        inv = book.create_invoice(customer_id=studio["id"],
                                  date_opened=date(YEAR, m, 5).isoformat(),
                                  currency="EUR", term="Net 14")
        book.add_invoice_entry(invoice_id=inv["id"], account=REV19,
                               description=f"Editorial-Design {m:02d}/2025",
                               quantity="1", price=str(net), taxtable="USt 19%")
        book.post_invoice(invoice_id=inv["id"], post_account=AR,
                          post_date=date(YEAR, m, 5).isoformat(), owner_type="customer")
        gross = (net * D("1.19")).quantize(D("0.01"))
        book.pay_invoice(invoice_id=inv["id"], payment_account=BANKKONTO,
                         amount=str(gross), payment_date=date(YEAR, m, 20).isoformat(),
                         owner_type="customer")
        counts["invoices"] += 1

    # Licensing invoice at the reduced 7% rate (Nutzungsrechte).
    inv = book.create_invoice(customer_id=agentur["id"],
                              date_opened=date(YEAR, 3, 12).isoformat(),
                              currency="EUR", term="Net 14")
    book.add_invoice_entry(invoice_id=inv["id"], account=REV7,
                           description="Einräumung von Nutzungsrechten (Illustration)",
                           quantity="1", price="1200", taxtable="USt 7%")
    book.post_invoice(invoice_id=inv["id"], post_account=AR,
                      post_date=date(YEAR, 3, 12).isoformat(), owner_type="customer")
    book.pay_invoice(invoice_id=inv["id"], payment_account=BANKKONTO,
                     amount=str((D("1200") * D("1.07")).quantize(D("0.01"))),
                     payment_date=date(YEAR, 3, 26).isoformat(), owner_type="customer")
    counts["invoices"] += 1

    # THE ACCEPTANCE TEST: USD client, export (no VAT), post & pay at
    # different EUR/USD rates -> realized FX into a type-resolved INCOME
    # child (English leaf on this numbered SKR03 chart, by design).
    inv = book.create_invoice(customer_id=us["id"], date_opened=US_POST.isoformat(),
                              currency="USD", term="Net 14")
    book.add_invoice_entry(invoice_id=inv["id"], account=REV_EXPORT,
                           description="Brand identity system (export)",
                           quantity="1", price="3500")
    book.post_invoice(invoice_id=inv["id"], post_account=AR_USD,
                      post_date=US_POST.isoformat(), owner_type="customer")
    book.pay_invoice(invoice_id=inv["id"], payment_account=BANKKONTO,
                     amount="3500", payment_date=US_PAY.isoformat(),
                     owner_type="customer")
    counts["invoices"] += 1

    # One open (outstanding) EUR invoice for an A/R demo surface.
    recent = date(THROUGH.year, THROUGH.month, 1)
    inv = book.create_invoice(customer_id=studio["id"],
                              date_opened=recent.isoformat(), currency="EUR", term="Net 14")
    book.add_invoice_entry(invoice_id=inv["id"], account=REV19,
                           description="Geschäftsbericht 2025 — Layout",
                           quantity="1", price="3800", taxtable="USt 19%")
    book.post_invoice(invoice_id=inv["id"], post_account=AR,
                      post_date=recent.isoformat(), owner_type="customer")
    counts["invoices"] += 1

    # A vendor bill (Adobe CC), input VAT, paid.
    bill = book.create_bill(vendor_id=vendor["id"],
                            date_opened=date(YEAR, 2, 1).isoformat(),
                            currency="EUR", term="Net 14")
    book.add_bill_entry(bill_id=bill["id"], account="Aufwendungen 2/4:verschiedene Kosten:4985 Werkzeuge und Kleingeräte",
                        description="Creative Cloud Jahresabo", quantity="1",
                        price="660", taxtable="USt 19%")
    book.post_invoice(invoice_id=bill["id"], post_account=AP,
                      post_date=date(YEAR, 2, 1).isoformat(), owner_type="vendor")
    book.pay_invoice(invoice_id=bill["id"], payment_account=BANKKONTO,
                     amount=str((D("660") * D("1.19")).quantize(D("0.01"))),
                     payment_date=date(YEAR, 2, 10).isoformat(), owner_type="vendor")
    counts["bills"] = 1
    return counts


# ── Phase 8: edge — localized Ausgleichskonto (Tier C) ─────────────
def run_edge(out_path: Path) -> None:
    # A recent, not-yet-cleared item parked in the localized Ausgleichskonto
    # (Tier-C fixture: trips the dashboard's German-Imbalance warning). Dated
    # near THROUGH so it reads as a fresh "to clarify", not a year-old defect.
    edge_date = day_in(date(THROUGH.year, THROUGH.month, 1), 17)
    write_bulk(out_path, [{
        "date": edge_date,
        "description": "Unklare Lastschrift (noch zu klären)",
        "splits": [(BANKKONTO, D("-48.50")), (AUSGLEICH, D("48.50"))],
    }])


def write_bulk(out_path: Path, txns: list[dict]) -> int:
    # Sabine's generators iterate MONTHS and emit each month whole, so
    # this is the module's clamp: nothing dated past THROUGH may land
    # (a through early in a month would otherwise write the rest of
    # that month as future-dated activity).
    txns = [t for t in txns if t["date"] <= THROUGH]
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    n = 0
    try:
        eur = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        for t in txns:
            splits = [piecash.Split(account=acct[p], value=v) for p, v in t["splits"]]
            piecash.Transaction(currency=eur, description=t["description"],
                                post_date=t["date"], splits=splits)
            n += 1
        book.save()
    finally:
        book.close()
    return n


# ── Verify ─────────────────────────────────────────────────────────
def verify(out_path: Path) -> None:
    book = GnuCashBook(str(out_path))
    print("\n── Verify ──")
    bs = book.balance_sheet(as_of_date=THROUGH)
    nw = book.net_worth(end_date=THROUGH)
    assets = D(str(bs["assets"]["total"]))
    liab = D(str(bs["liabilities"]["total"]))
    equity = D(str(bs["equity"]["total"]))
    networth = D(str(nw["net_worth"]))
    cent = D("0.01")

    # 1. Accounting equation: A = L + E (to the cent).
    assert abs(assets - (liab + equity)) < cent, \
        f"A != L+E: {assets} vs {liab}+{equity}"
    # 2. Cross-tool agreement: net_worth == assets - liabilities (to the cent).
    assert abs(networth - (assets - liab)) < cent, \
        f"net_worth {networth} != assets-liab {assets - liab}"
    print(f"✓ A=L+E and net worth agree to the cent: "
          f"€{assets} = €{liab} + €{equity}")

    # 3. FX recognized under the type-resolved German income root, with a
    #    German leaf (Tier-D naming under GNUCASH_LOCALE=de).
    with book.open() as b:
        fx = [a.fullname for a in b.accounts
              if "Realisierter Gewinn/Verlust" in a.name
              or "Foreign Exchange Gain/Loss" in a.name]
        assert any("Erlöse u. Erträge 2/8" in f for f in fx), \
            f"FX account not under the German income root: {fx}"
    print(f"✓ FX recognized under German INCOME (by type): {fx[0]}")

    # 4. Tier-C: the localized Ausgleichskonto is flagged as a defect.
    summary = str(book.get_book_summary())
    assert "Ausgleichskonto-EUR" in summary, \
        "localized Imbalance account not flagged in get_book_summary"
    print("✓ Tier-C: German Ausgleichskonto flagged in the dashboard")

    # 5. Debt-payoff resolves both German loans via their slots (no English
    #    'mortgage' keyword present anywhere).
    dp = str(book.debt_payoff_plan(monthly_budget="3000"))
    assert "Kreditinstituten" in dp and "Kfz-Finanzierung" in dp, \
        "debt-payoff did not include the German loans"
    print("✓ Debt-payoff includes Hypothek + Kfz (slot-driven term)")


# ── Orchestration ──────────────────────────────────────────────────
# ── Continuation hooks (closed-loop policy layer) ───────────────
# Persona wiring for scripts/synthetic_book/continue_book.py. Sabine
# is the healthy control (DRIFT_ANALYSIS): no cards, no schedules, a
# thin Bankkonto that lives off the corridor top-up, and one honest
# repair — the €48.50 Ausgleichskonto blemish clears in narrative, in
# German, and the i18n book earns its clean bill of health.

from continuation import PersonaPolicy  # noqa: E402

AUFW_SONSTIGE = "Aufwendungen 2/4:Versicherungsbeiträge:4390 sonstige Ausgaben"


def continuation_txns(through: date) -> list[dict]:
    """The deterministic streams continuation replays (spec §2.2).
    ``run_edge`` is deliberately absent — the Ausgleichskonto item is
    prefix history, and the repair below resolves it."""
    global THROUGH
    THROUGH = through
    return (gen_recurring() + gen_variable() + gen_personal()
            + gen_honorar())


def _add_price_rows(out_path: Path, pairs: list[tuple[str, date]]) -> int:
    """EUR-base quotes for (symbol, date) pairs, skipping any the book
    already has (the prefix's price table is never touched)."""
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    n = 0
    try:
        eur = book.default_currency
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
            value = (eur_per_usd(when) if sym == "USD"
                     else etf_price(when))
            piecash.Price(
                commodity=comm_by[sym], currency=eur, date=when,
                value=value, type="last", source="user:market-data",
            )
            n += 1
        book.save()
    finally:
        book.close()
    return n


def extend_prices(out_path: Path, since: date, through: date) -> int:
    global THROUGH
    THROUGH = through
    pairs = [(sym, d) for d in iter_months() if d > since
             for sym in ("USD", ETF_MNEMONIC)]
    # §5: closing points dated AT the horizon so a fresh bundle opens
    # warning-free (values forward-fill the last real close; the ETF
    # series is synthetic and prices any date natively).
    pairs.append(("USD", through))
    pairs.append((ETF_MNEMONIC, through))
    return _add_price_rows(out_path, pairs)


def ensure_rate(out_path: Path, currency: str, when: date) -> None:
    _add_price_rows(out_path, [(currency, when)])


def continuation_invest(out_path: Path, when: date, amount: D,
                        source_path: str) -> None:
    """Policy-layer ETF purchase mirroring the Sparplan lot pattern —
    the quarterly Postbank skim lands in the same MSCI World depot."""
    _add_price_rows(out_path, [(ETF_MNEMONIC, when)])
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    try:
        eur = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        price = etf_price(when)
        units = (amount / price).quantize(D("0.0001"), ROUND_HALF_UP)
        lot = piecash.Lot(
            title=f"Sparplan Sonderkauf {when.isoformat()}",
            account=acct[ETF], notes="MSCI World — Überschussanlage",
            is_closed=0)
        isp = piecash.Split(account=acct[ETF], value=amount, quantity=units)
        bsp = piecash.Split(account=acct[source_path], value=-amount)
        piecash.Transaction(
            currency=eur,
            description="MSCI World ETF Sonderkauf (comdirect)",
            post_date=when, splits=[isp, bsp])
        isp.lot = lot
        book.save()
    finally:
        book.close()


def ausgleich_repair(out_path: Path, cutoff: date,
                     through: date) -> list[str]:
    """Clear the €48.50 Ausgleichskonto blemish with a dated
    reclassification (spec §2.3) — idempotent: a zero balance means a
    prior continuation already resolved it."""
    book = GnuCashBook(str(out_path))
    balance = D(str(book.get_balance(AUSGLEICH)))
    if balance == 0:
        return []
    when = cutoff + timedelta(days=6)
    if when > through:
        return []
    book.create_transaction(
        description="Korrektur — ungeklärte Differenz aufgelöst",
        trans_date=when,
        splits=[
            {"account": AUSGLEICH, "amount": str(-balance)},
            {"account": AUFW_SONSTIGE, "amount": str(balance)},
        ],
        check_duplicates=False,
    )
    return [f"Ausgleichskonto {balance} EUR aufgelöst on {when}"]


def ensure_schedules(book: GnuCashBook) -> int:
    """Feature parity (bookkeeper review §4): the schedules a German
    freelancer would actually run — Miete, Krankenkasse, and the ETF
    Sparplan she already executes monthly. Idempotent by name."""
    env = book.list_scheduled_transactions(compact=False, limit=250)
    rows = next((v for v in env.values() if isinstance(v, list)), [])
    existing = {row.get("name") for row in rows}
    plans = [
        ("Miete Studio", "Dauerauftrag Miete Studio Schwabing", [
            {"account": BANKKONTO, "amount": "-1190.00"},
            {"account": MIETE, "amount": "1190.00"},
        ], "monthly", "2025-01-01"),
        ("Krankenkasse", "Techniker Krankenkasse Beitrag", [
            {"account": BANKKONTO, "amount": "-812.40"},
            {"account": KRANKENKASSE, "amount": "812.40"},
        ], "monthly", "2025-01-01"),
        # The ETF split needs a quantity (IWDA.AS ≠ EUR). A template's
        # unit count is nominal — the realized buys reprice monthly —
        # so ~€1,200 at a recent close is the honest placeholder.
        ("ETF Sparplan", "MSCI World ETF Sparplan (comdirect)", [
            {"account": BANKKONTO, "amount": "-1200.00"},
            {"account": ETF, "amount": "1200.00", "quantity": "13.1378"},
        ], "monthly", "2025-01-06"),
    ]
    created = 0
    for name, desc, splits, freq, start in plans:
        if name in existing:
            continue
        book.create_scheduled_transaction(
            name=name, description=desc, splits=splits,
            start_date=start, frequency=freq, enabled=True,
        )
        created += 1
    return created


def ensure_budget(book: GnuCashBook) -> bool:
    """Feature parity (bookkeeper review §4): a modest business budget
    so all three books exercise the budget surface. Idempotent."""
    env = book.list_budgets(compact=False)
    rows = next((v for v in env.values() if isinstance(v, list)), [])
    if any(row.get("name") == "Budget 2025" for row in rows):
        return False
    book.create_budget(name="Budget 2025", year=YEAR, num_periods=12,
                       period_type="monthly",
                       description="Studio-Budget Sabine Brenner")
    monthly = [
        (MIETE, "1190"),
        (KRANKENKASSE, "812"),
        ("Aufwendungen 2/4:verschiedene Kosten:4930 Bürobedarf", "120"),
        ("Aufwendungen 2/4:verschiedene Kosten:4922 Internet", "60"),
        ("Aufwendungen 2/4:Werbe-/Reisekosten:4670 Reisekosten Unternehmer",
         "250"),
    ]
    for acct, amt in monthly:
        book.set_budget_amount(budget_name="Budget 2025", account=acct,
                               amount=amt, period="all")
    return True


def continue_business(book: GnuCashBook, through: date,
                      since: date) -> dict:
    """Sabine's business module is fixed-2025 VAT fixtures except one
    THROUGH-relative open invoice (the A/R demo surface). Re-anchor it
    after the settlement pass ages its predecessor — skipped while the
    predecessor is still outstanding."""
    ensure_schedules(book)
    ensure_budget(book)
    env = book.get_outstanding_invoices(compact=False, limit=250)
    open_names = {doc.get("owner_name") for doc in env.get("invoices", [])}
    if "Verlag Bergblick GmbH" in open_names:
        return {"invoices": 0}
    # ~10 days before the horizon: with Net 14 terms the invoice reads
    # as a CURRENT receivable, not already-overdue on first open.
    recent = through - timedelta(days=10)
    if recent <= since:
        return {"invoices": 0}
    cust_env = book.list_customers(compact=False, limit=250)
    rows = next((v for v in cust_env.values() if isinstance(v, list)), [])
    verlag = next(row for row in rows
                  if row.get("name") == "Verlag Bergblick GmbH")
    inv = book.create_invoice(customer_id=verlag["id"],
                              date_opened=recent.isoformat(),
                              currency="EUR", term="Net 14")
    book.add_invoice_entry(invoice_id=inv["id"], account=REV19,
                           description=f"Editorial-Design — Ausgabe "
                                       f"{recent.strftime('%m/%Y')}",
                           quantity="1", price="3800", taxtable="USt 19%")
    book.post_invoice(invoice_id=inv["id"], post_account=AR,
                      post_date=recent.isoformat(), owner_type="customer")
    return {"invoices": 1}


def continue_investments(out_path: Path, through: date,
                         since: date) -> int:
    global THROUGH
    THROUGH = through
    return run_investments(out_path, since=since)


def advance_schedules(out_path: Path, through: date) -> dict:
    from continuation import advance_sx
    return advance_sx(out_path, through)


POLICY = PersonaPolicy(
    key="sabine", currency="EUR",
    checking=BANKKONTO,                # the thin flow account
    savings=POSTBANK,                  # the accumulating parking account
    buffer=D("8000"),                  # corridor midpoint (DRIFT)
    cards=(),
    savings_share=D("1"),              # surplus parks in Postbank whole
    invest_months=(3, 6, 9, 12),
    savings_target=D("10000"),         # Postbank skims to the ETF above this
    rebalance_tranche=D("2000"),       # modest — steady state suffices
    max_monthly_sweep=D("6000"),
    min_sweep=D("100"),
    invest=continuation_invest,
    ensure_rate=ensure_rate,
    book_repairs=ausgleich_repair,
    # Loans + VAT clearing: no statement to reconcile against (§1).
    no_reconcile=(HYPOTHEK, KFZ_FIN, UST19, UST7),
    desc_sweep="Übertrag auf Postbank (Monatsüberschuss)",
    desc_repair_sweep="Übertrag auf Postbank — angesammelter Überschuss",
    desc_topup="Umbuchung von Postbank (Kontodeckung)",
)


# ── Driver ──────────────────────────────────────────────────────

def build(out_path: Path) -> None:
    # Sabine runs a German-locale system, so the server names auto-created
    # accounts in German (Tier-D): the FX gain/loss leaf becomes
    # "Realisierter Gewinn/Verlust", not the English fallback that a
    # locale-less run would pick on a numbered SKR03 chart.
    os.environ["GNUCASH_LOCALE"] = "de_DE.UTF-8"
    print(f"Building Sabine book at: {out_path}  (THROUGH={THROUGH})")
    print("\nPhase 1: book + commodities + prices")
    create_book_file(out_path)
    print(f"  {add_prices(out_path)} prices")
    print("\nPhase 2: SKR03 chart of accounts")
    print(f"  {create_accounts(out_path)} accounts")
    book = GnuCashBook(str(out_path))
    set_account_slots(book)
    print("  loan slots set")
    print("\nPhase 3: opening balances + ETF lot")
    opening_balances(out_path)
    print("\nPhase 4: recurring (rent, utilities, subs, loans, USt-VA)")
    print(f"  {write_bulk(out_path, gen_recurring())} recurring txns")
    print("\nPhase 5: variable business spend")
    print(f"  {write_bulk(out_path, gen_variable())} variable txns")
    print("\nPhase 6: personal living + Krankenkasse (Privatentnahme)")
    print(f"  {write_bulk(out_path, gen_personal())} personal txns")
    print("\nPhase 7: direct Honorar deposits")
    print(f"  {write_bulk(out_path, gen_honorar())} Honorar deposits")
    print("\nPhase 7b: monthly ETF Sparplan")
    print(f"  {run_investments(out_path)} Sparplan buys")
    print("\nPhase 7: business (VAT invoicing + USD cross-currency)")
    print(f"  {run_business(book)}")
    print("\nPhase 7c: schedules + budget (parity — bookkeeper review §4)")
    from continuation import advance_sx
    print(f"  {ensure_schedules(book)} schedules, "
          f"budget={ensure_budget(book)}, cursors={advance_sx(out_path, THROUGH)}")
    print("\nPhase 8: edge — localized Ausgleichskonto")
    run_edge(out_path)
    verify(out_path)
    print("\nDone.")


def main() -> None:
    global THROUGH
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--through", default=None, metavar="YYYY-MM-DD",
                   help="Pin the timeline end for a deterministic run (default: today).")
    a = p.parse_args()
    if a.through:
        THROUGH = date.fromisoformat(a.through)
        if THROUGH < date(YEAR, 1, 1):
            raise SystemExit(f"--through {THROUGH} precedes {YEAR}-01-01")
    out = Path(a.out).resolve()
    if out == PROTECTED.resolve():
        raise SystemExit(f"REFUSING to write protected book: {PROTECTED}")
    build(out)


if __name__ == "__main__":
    main()
