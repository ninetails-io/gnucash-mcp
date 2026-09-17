"""Build the Sabine Brenner synthetic book — a German SKR03 persona.

Sabine Brenner, Munich freelance Grafikdesignerin (Einzelunternehmerin,
Regelbesteuerung, EÜR). EUR default; authentic DATEV **SKR03** chart
(German, numbered) so the i18n bug class — every tool that keys off an
English account name — becomes a failing test. A USD-paying client
forces cross-currency invoicing (the FX acceptance test); German VAT
(USt 19%/7%) runs through taxtables; a Hypothek drives the debt-payoff
path; a localized Ausgleichskonto exercises the Tier-C warning.

One book, two zones (the 2026-09-01 scope ruling, BOOKKEEPER_REVIEW_
DEMO_GENERATORS.md §7): the SKR03 ranges are the business and feed the
EÜR; a Privat zone — three German-named top-level roots, ``Privat-
vermögen`` / ``Privatschulden`` / ``Privatkapital`` — holds the
residence, its Hypothek and the MSCI World ETF. Money crosses the zone
boundary only through 1800 Privatentnahme / 1890 Privateinlagen, with
``Privatkapital`` as the private side's counterpart, so the business's
Kapitalkonto stays complete. Nothing private sits in an EXPENSE-type
account: the Hypothek interest is a drawing leg under ``Privatkapital``
(cold audit W6), so GnuCash's P&L and the EÜR agree on what profit is.

What the book does the way a German Steuerberater expects (cold audit
2026-09-17, specs/v1.5/testing/AUDIT_SABINE_COLD_2026-09-17.md):

- **Sollversteuerung, one regime.** Every euro of revenue is an
  invoice through A/R — one gap-free chronological sequence across all
  customers (§14 UStG / GoBD), Leistungszeitraum in the notes, output
  VAT at posting (1776/1771) and cleared by the USt-VA of the posting
  month. Licence-only invoices (Einräumung von Nutzungsrechten) carry
  7% (§12 Abs. 2 Nr. 7c UStG); everything else 19%. EU-B2B services
  are Reverse Charge on 8336, Drittland services on 8338.
- **One payee → tax-treatment table** (``PAYEES``) that every variable
  expense passes through: domestic 19% (1576) and 7% (1571 — Bahn
  Fernverkehr, Nahverkehr, Bücher, Übernachtung, Lebensmittel);
  exempt gross (insurance, Kfz-Steuer, bank fees, Briefporto, rent,
  foreign hotels and flights); §13b Reverse Charge for EU and
  Drittland SaaS/ads (net to expense, 1787 credit + 1577 debit, VA
  Kz. 46/47/67); Bewirtung 70/30 (4650/4654) with Anlass and
  Teilnehmer on every row; private items to 1800.
- **A monthly USt-Voranmeldung** on the 10th (rolled to the next
  Bankarbeitstag): Zahllast = the prior month's 1776/1771/1787 output
  minus its 1576/1571/1577 input, derived from the book, cleared
  through the Bankkonto. No VAT account accumulates past the month in
  flight.
- **Einkommensteuer**: quarterly Vorauszahlungen (10 Mar/Jun/Sep/Dec)
  from 1200 to 1810 Privatsteuern, sized by the last Bescheid; the
  simulated Bescheid for year Y−1 lands on 12 August of Y, adjusts the
  running quarters and settles Y−1 on 15 September. Everything from 2025
  on is derived from the book's own EÜR; only the 2024 Bescheid figures
  are constants.
- The Pkw stays a business asset (BLP €32,000 gross ↔ AK €26,890.76
  net ↔ AfA €4,481.79/yr) with the 1%-Regelung private-use imputation
  every month (1880 against 8924/8920 + 1776) and linear AfA to 4832
  every 31 December.
- Bank postings roll off weekends and Bavarian holidays to the next
  Bankarbeitstag; every transaction's entry date is its document date;
  bank accounts are reconciled through the last full month; the
  closed-loop cash policy (corridor top-up, surplus sweep, quarterly
  ETF skim) runs from day one so the Bankkonto never drifts.
- No hard year-end close (GnuCash convention), and the €48.50 "Unklare
  Lastschrift" stays: it is the onboarding hook, not a defect.

Deterministic (fixed per-phase seeds). Date-pinnable for a reproducible
run; defaults to today so the sample stays current.

    uv run python scripts/synthetic_book/build_sabine.py --out /tmp/sb.gnucash
    uv run python scripts/synthetic_book/build_sabine.py --out /tmp/sb.gnucash --through 2026-06-30

NEVER writes to the protected sample (samples/sabine-brenner.gnucash).
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import os
import random
import sqlite3
from datetime import date, timedelta
from decimal import Decimal as D, ROUND_HALF_UP, ROUND_FLOOR
from pathlib import Path

import piecash
from dateutil.easter import easter

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
VST7 = f"{FIN}:1571 Abziehbare VSt. 7%"
VST_RC = f"{FIN}:1577 Abziehbare VStr. nach §13b UStG 19%"
AP = "Passiva:Verbindlichkeiten:1600 Verblk. aus Lieferungen u. Leistungen"
ERL = "Erlöse u. Erträge 2/8:Erlöskonten 8"
ERT = "Erlöse u. Erträge 2/8:Ertragskonten 2"
REV19 = f"{ERL}:8400 Erlöse USt. 19%"
REV7 = f"{ERL}:8300 Erlöse USt. 7%"
ZINSERTRAG = f"{ERT}:2650 sonstige Zinsen und ähnliche Erträge"
# S6: services to a Drittland customer are not taxable in Germany
# (§3a Abs. 2 UStG, place of supply = recipient) → SKR03 8338. Services
# to an EU-B2B customer are Reverse Charge (§13b) → SKR03 8336 (the
# "im anderen EU-Land steuerpflichtigen sonstigen Leistungen" account;
# 8337 is the DOMESTIC §13b sibling and is not used here). 8120 is the
# goods-export account (§4 Nr. 1a) — a designer exports no goods, so it
# is deliberately absent from the chart.
REV_DRITTLAND = f"{ERL}:8338 Erlöse aus im Drittland steuerbaren Leistungen"  # ADD
REV_EU_B2B = f"{ERL}:8336 Erlöse aus im anderen EU-Land steuerpflichtigen sonstigen Leistungen"  # ADD
# S3: the 1%-Regelung's income side (SKR03): 80% of the imputed value
# bears 19% USt (8924), the flat 20% for VAT-free car costs does not
# (8920). The private side is 1880 Unentgeltliche Wertabgaben.
REV_KFZ_19 = f"{ERL}:8924 Verwendung von Gegenständen für Zwecke außerhalb des Unternehmens 19% USt"  # ADD
REV_KFZ_0 = f"{ERL}:8920 Verwendung von Gegenständen für Zwecke außerhalb des Unternehmens ohne USt"  # ADD
UST19 = "Passiva:Umsatzsteuer:1776 Umsatzsteuer 19%"
UST7 = "Passiva:Umsatzsteuer:1771 Umsatzsteuer 7%"
UST_RC = "Passiva:Umsatzsteuer:1787 Umsatzsteuer § 13b UStG 19%"
UST_VZ = "Passiva:Umsatzsteuer:1780 Umsatzsteuer-Vorauszahlung"
OPENING = "Anfangsbestand 9:Saldenvortragskonten:9000 Saldenvortrag Sachkonten"
PRIVK = "Privatkonten 1:Privatentnahmen/-einlagen"
PRIV_DRAW = f"{PRIVK}:1800 Privatentnahme allgemein"
PRIV_STEUERN = f"{PRIVK}:1810 Privatsteuern"
MIETE = "Aufwendungen 2/4:Raumkosten:4210 Miete und Nebenkosten"
TELEKOM = "Aufwendungen 2/4:verschiedene Kosten:4920 Telekom"
MOBILFUNK = "Aufwendungen 2/4:verschiedene Kosten:4921 Mobilfunk D2"
INTERNET = "Aufwendungen 2/4:verschiedene Kosten:4922 Internet"
BUEROBEDARF = "Aufwendungen 2/4:verschiedene Kosten:4930 Bürobedarf"
PORTO = "Aufwendungen 2/4:verschiedene Kosten:4910 Porto"
STEUERBERATER = "Aufwendungen 2/4:verschiedene Kosten:4955 Buchführungskosten"
FORTBILDUNG = "Aufwendungen 2/4:verschiedene Kosten:4945 Fortbildungskosten"
LIZENZEN = "Aufwendungen 2/4:verschiedene Kosten:4964 Aufwendungen für die zeitlich befristete Überlassung von Rechten (Lizenzen, Konzessionen)"
WARTUNG = "Aufwendungen 2/4:Reparatur/Instandhaltung:4806 Wartungskosten für Hard- und Software"
WERBUNG = "Aufwendungen 2/4:Werbe-/Reisekosten:4610 Werbekosten"
REISE = "Aufwendungen 2/4:Werbe-/Reisekosten:4670 Reisekosten Unternehmer"
VERPFLEGUNG = "Aufwendungen 2/4:Werbe-/Reisekosten:4674 Reisekosten Unternehmer Verpflegungsmehraufwand"
UEBERNACHTUNG = "Aufwendungen 2/4:Werbe-/Reisekosten:4676 Reisekosten Unternehmer Übernachtungsaufwand"
BEWIRTUNG = "Aufwendungen 2/4:Werbe-/Reisekosten:4650 Bewirtungskosten"
BEWIRTUNG_NA = "Aufwendungen 2/4:Werbe-/Reisekosten:4654 Nicht abzugsfähige Bewirtungskosten"
GWG = "Aufwendungen 2/4:Abschreibungen:4855 Sofortabschreibung GWG"
VERSICHERUNG = "Aufwendungen 2/4:Versicherungsbeiträge:4360 Versicherungen"
BANKGEBUEHR = "Aufwendungen 2/4:verschiedene Kosten:4970 Nebenkosten des Geldverkehrs"
# S1: 2110 stays in the chart and stays EMPTY — the Hypothek is
# private (see the Privat zone below), so its interest never touches a
# business expense account. The Kfz financing interest is business:
# the Pkw is a business asset.
ZINS_KFZ = "Aufwendungen 2/4:Zinsaufwendungen:2121 Zinsaufwendungen für KFZ Finanzierung"
AFA_KFZ = "Aufwendungen 2/4:Abschreibungen:4832 Abschreibungen auf Kfz"   # ADD (S8)
STROM = "Aufwendungen 2/4:Raumkosten:4240 Gas, Wasser, Strom (Verwaltung, Vertrieb)"
WERKZEUG = "Aufwendungen 2/4:verschiedene Kosten:4985 Werkzeuge und Kleingeräte"
AUFMERK = "Aufwendungen 2/4:Werbe-/Reisekosten:4653 Aufmerksamkeiten"
BUECHER = "Aufwendungen 2/4:verschiedene Kosten:4940 Zeitschriften, Bücher"
KFZ_BETRIEB = "Aufwendungen 2/4:Kfz-Kosten:4530 laufende Kfz-Betriebskosten"
KFZ_STEUER = "Aufwendungen 2/4:Kfz-Kosten:4510 Kfz-Steuer"
KFZ_VERS = "Aufwendungen 2/4:Kfz-Kosten:4520 Kfz-Versicherungen"
PRIV_EINLAGE = f"{PRIVK}:1890 Privateinlagen"
PRIV_WERTABGABE = f"{PRIVK}:1880 Unentgeltliche Wertabgaben"  # ADD (S3)
# 1830 Sonderausgaben — private health insurance is a Privatentnahme, not
# a business expense (Vorsorgeaufwendungen).
KRANKENKASSE = f"{PRIVK}:1830 Sonderausgaben unbeschränkt abzugsfähig"
# Business additions (under authentic German parents)
KFZ_FIN = "Passiva:Verbindlichkeiten:0640 Kfz-Finanzierung"
PKW = f"{ANL}:0320 Pkw"
# ── The Privat zone (Tier B) ──────────────────────────────────────
# SKR03 has no private-asset range (its 18xx accounts are the business's
# view of draws), so these carry plain German names, no numbers. One
# type-homogeneous root per zone side — GnuCash's parent/child type
# compatibility rules out a single mixed "Privat" root. There is no
# private EXPENSE root (audit W6): private consumption is a drawing
# against Privatkapital, never a P&L line.
PRIV_ASSETS = "Privatvermögen"                  # top-level ASSET
PRIV_LIAB = "Privatschulden"                    # top-level LIABILITY
PRIV_KAPITAL = "Privatkapital"                  # top-level EQUITY — the private side's counterpart to 1800/1890
WOHNUNG = f"{PRIV_ASSETS}:Eigentumswohnung Schwabing"
DEPOT = f"{PRIV_ASSETS}:Wertpapierdepot comdirect"
ETF = f"{DEPOT}:MSCI World ETF"
HYPOTHEK = f"{PRIV_LIAB}:Hypothek Sparkasse"
# The Hypothek's interest leg: an EQUITY child of Privatkapital, so the
# amount stays visible in a register of its own without ever being an
# expense (W6). A drawing, in GnuCash's terms.
ZINS_HYP_PRIV = f"{PRIV_KAPITAL}:Hypothekenzinsen (Privatentnahme)"
AUSGLEICH = "Ausgleichskonto-EUR"   # root-level BANK — localized Imbalance (Tier C)

# S3/S8/P6: the Pkw, parameters coherent. Bruttolistenpreis €32,000 →
# 1%-Regelung €320/month; bought new for exactly that list price in
# January 2024, so Anschaffungskosten = 32,000 / 1.19 = €26,890.76 net
# (the 19% VSt was reclaimed on purchase); linear over 6 years → AfA
# €4,481.79/year. 2024's AfA is already taken, hence the €22,408.97
# book value on 01.01.2025. The last year leaves the €1 Erinnerungswert.
# Verbrenner (Benzin) — it fuels at ARAL/Shell and never charges (P6).
PKW_LISTENPREIS = D("32000")
PKW_AK = (PKW_LISTENPREIS / D("1.19")).quantize(D("0.01"), ROUND_HALF_UP)   # 26,890.76
PKW_AFA_YEARS = 6
PKW_FIRST_AFA_YEAR = 2024
PKW_NOTES = ("VW Golf VIII 1.5 eTSI (Benzin), Erstzulassung 01/2024. "
             "Bruttolistenpreis 32.000,00 € (1%-Regelung: 320,00 €/Monat), "
             "Anschaffungskosten 26.890,76 € netto, AfA linear 6 Jahre "
             "(4.481,79 €/Jahr). Kein 0,03%-Zuschlag: Wohnung und Studio "
             "beide in Schwabing.")

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
    ('4806 Wartungskosten für Hard- und Software', 'EXPENSE', 'Aufwendungen 2/4:Reparatur/Instandhaltung', "EUR", "CURRENCY", False),
    ('4360 Versicherungen', 'EXPENSE', 'Aufwendungen 2/4:Versicherungsbeiträge', "EUR", "CURRENCY", False),
    ('4380 Beiträge', 'EXPENSE', 'Aufwendungen 2/4:Versicherungsbeiträge', "EUR", "CURRENCY", False),
    ('4390 sonstige Ausgaben', 'EXPENSE', 'Aufwendungen 2/4:Versicherungsbeiträge', "EUR", "CURRENCY", False),
    ('4396 steuerlich abzugsfähige Verspätungszuschläge und Zwangsgelder', 'EXPENSE', 'Aufwendungen 2/4:Versicherungsbeiträge', "EUR", "CURRENCY", False),
    ('4610 Werbekosten', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
    ('4650 Bewirtungskosten', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
    ('4653 Aufmerksamkeiten', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
    ('4654 Nicht abzugsfähige Bewirtungskosten', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
    ('4665 nicht abzugsfähige Betriebsausg. aus Werbe-, Repräs.- u. Reisekosten', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
    ('4670 Reisekosten Unternehmer', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
    ('4674 Reisekosten Unternehmer Verpflegungsmehraufwand', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
    ('4676 Reisekosten Unternehmer Übernachtungsaufwand', 'EXPENSE', 'Aufwendungen 2/4:Werbe-/Reisekosten', "EUR", "CURRENCY", False),
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
    ('4964 Aufwendungen für die zeitlich befristete Überlassung von Rechten (Lizenzen, Konzessionen)', 'EXPENSE', 'Aufwendungen 2/4:verschiedene Kosten', "EUR", "CURRENCY", False),
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
    # — business zone, authentic SKR03 numbers under authentic parents —
    ('1407 Ford. a. Lief. u. Leist. USD', 'RECEIVABLE', 'Aktiva:Finanzkonten 1', "USD", "CURRENCY", False),
    ('8338 Erlöse aus im Drittland steuerbaren Leistungen', 'INCOME', ERL, "EUR", "CURRENCY", False),
    ('8336 Erlöse aus im anderen EU-Land steuerpflichtigen sonstigen Leistungen', 'INCOME', ERL, "EUR", "CURRENCY", False),
    ('8924 Verwendung von Gegenständen für Zwecke außerhalb des Unternehmens 19% USt', 'INCOME', ERL, "EUR", "CURRENCY", False),
    ('8920 Verwendung von Gegenständen für Zwecke außerhalb des Unternehmens ohne USt', 'INCOME', ERL, "EUR", "CURRENCY", False),
    ('4832 Abschreibungen auf Kfz', 'EXPENSE', 'Aufwendungen 2/4:Abschreibungen', "EUR", "CURRENCY", False),
    ('1880 Unentgeltliche Wertabgaben', 'EQUITY', 'Privatkonten 1:Privatentnahmen/-einlagen', "EUR", "CURRENCY", False),
    ('0320 Pkw', 'ASSET', 'Aktiva:Anlage- u. Kapitalkonten 0', "EUR", "CURRENCY", False),
    ('0640 Kfz-Finanzierung', 'LIABILITY', 'Passiva:Verbindlichkeiten', "EUR", "CURRENCY", False),
    # — Privat zone (Tier B): residence, Hypothek, ETF, capital —
    (PRIV_ASSETS, 'ASSET', None, "EUR", "CURRENCY", True),
    ('Eigentumswohnung Schwabing', 'ASSET', PRIV_ASSETS, "EUR", "CURRENCY", False),
    ('Wertpapierdepot comdirect', 'ASSET', PRIV_ASSETS, "EUR", "CURRENCY", True),
    ('MSCI World ETF', 'MUTUAL', DEPOT, ETF_MNEMONIC, "FUND", False),
    (PRIV_LIAB, 'LIABILITY', None, "EUR", "CURRENCY", True),
    ('Hypothek Sparkasse', 'LIABILITY', PRIV_LIAB, "EUR", "CURRENCY", False),
    (PRIV_KAPITAL, 'EQUITY', None, "EUR", "CURRENCY", False),
    ('Hypothekenzinsen (Privatentnahme)', 'EQUITY', PRIV_KAPITAL, "EUR", "CURRENCY", False),
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


# ── Calendar: Bankarbeitstage (P5 / A5) ────────────────────────────
def bavarian_holidays(year: int) -> set[date]:
    """Gesetzliche Feiertage in Bayern (München: incl. Mariä Himmelfahrt)
    plus the two Bankfeiertage (24./31.12.) on which German banks book
    nothing."""
    e = easter(year)
    return {
        date(year, 1, 1), date(year, 1, 6),
        e - timedelta(days=2), e + timedelta(days=1),     # Karfreitag, Ostermontag
        date(year, 5, 1),
        e + timedelta(days=39), e + timedelta(days=50),   # Himmelfahrt, Pfingstmontag
        e + timedelta(days=60),                           # Fronleichnam
        date(year, 8, 15), date(year, 10, 3), date(year, 11, 1),
        date(year, 12, 24), date(year, 12, 25), date(year, 12, 26),
        date(year, 12, 31),
    }


_HOLIDAY_CACHE: dict[int, set[date]] = {}


def is_bankday(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    hol = _HOLIDAY_CACHE.get(d.year)
    if hol is None:
        hol = _HOLIDAY_CACHE[d.year] = bavarian_holidays(d.year)
    return d not in hol


def bankday(d: date) -> date:
    """The next Bankarbeitstag on or after ``d`` — where a SEPA booking,
    a Dauerauftrag or a Finanzamt debit actually lands."""
    while not is_bankday(d):
        d += timedelta(days=1)
    return d


def bankday_back(d: date) -> date:
    """The last Bankarbeitstag on or before ``d``."""
    while not is_bankday(d):
        d -= timedelta(days=1)
    return d


def weekday_in(first: date, day: int) -> date:
    """A weekday (Mon–Fri) on or after ``day`` of the month — invoice
    dates are working days even though they need no bank."""
    d = day_in(first, day)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


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
        # P6: the Pkw's parameters live on the account itself.
        by_path[PKW].description = PKW_NOTES
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
    for acct in (HYPOTHEK, KFZ_FIN, UST19, UST7, UST_RC):
        book.set_account_slot(acct, "no_reconcile", "true")


# ── Phase 3: opening balances + ETF lot ────────────────────────────
# Business opening balances vortrag to 9000 (SKR03); the Privat zone's
# open against Privatkapital — two Saldenvortrag transactions, one per
# zone, so the business's Kapitalkonto never carries the residence.
def pkw_afa(year: int) -> D:
    """Linear AfA for ``year``; zero once written off. The final year
    leaves the €1 Erinnerungswert on 0320."""
    annual = (PKW_AK / PKW_AFA_YEARS).quantize(D("0.01"))           # 4,481.79
    last = PKW_FIRST_AFA_YEAR + PKW_AFA_YEARS - 1
    if year < PKW_FIRST_AFA_YEAR or year > last:
        return D("0")
    if year == last:
        return PKW_AK - annual * (PKW_AFA_YEARS - 1) - D("1")
    return annual


PKW_OPENING = PKW_AK - pkw_afa(PKW_FIRST_AFA_YEAR)                   # 22,408.97

OPENING_BALANCES = [
    (BANKKONTO, D("18400")),
    (POSTBANK, D("3250")),
    (PKW, PKW_OPENING),
    (KFZ_FIN, D("-16500")),
]
OPENING_PRIVATE = [
    (WOHNUNG, D("540000")),
    (HYPOTHEK, D("-395000")),
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
        acct = {a.fullname: a for a in book.accounts}
        cost = D("1200.00")
        for first in iter_months():
            buy_day = bankday(day_in(first, 6))
            if buy_day <= cut or buy_day > THROUGH:
                continue
            _etf_buy(book, acct, buy_day, cost, BANKKONTO,
                     lot_title=f"Sparplan {buy_day.isoformat()}",
                     lot_notes="MSCI World Sparplan",
                     description="MSCI World ETF Sparplan (comdirect)")
            n += 1
        book.save()
    finally:
        book.close()
    return n


def _etf_buy(book, acct: dict, when: date, amount: D, source_path: str,
             *, lot_title: str, lot_notes: str, description: str) -> None:
    """One ETF purchase crossing the zone boundary: the bank leg is the
    business's Privatentnahme (1800), the ETF lands in the Privat zone
    against Privatkapital. Four splits, one transaction, one lot."""
    units = (amount / etf_price(when)).quantize(D("0.0001"), ROUND_HALF_UP)
    lot = piecash.Lot(title=lot_title, account=acct[ETF], notes=lot_notes,
                      is_closed=0)
    isp = piecash.Split(account=acct[ETF], value=amount, quantity=units)
    piecash.Transaction(
        currency=book.default_currency, description=description,
        post_date=when,
        splits=[piecash.Split(account=acct[source_path], value=-amount),
                piecash.Split(account=acct[PRIV_DRAW], value=amount),
                isp,
                piecash.Split(account=acct[PRIV_KAPITAL], value=-amount)])
    isp.lot = lot


def opening_balances(out_path: Path) -> None:
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    try:
        eur = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        jan1 = date(YEAR, 1, 1)
        for rows, equity, desc in (
                (OPENING_BALANCES, OPENING, "Anfangsbestand 01.01.2025"),
                (OPENING_PRIVATE, PRIV_KAPITAL, "Anfangsbestand 01.01.2025 (privat)")):
            splits, total = [], D("0")
            for path, bal in rows:
                splits.append(piecash.Split(account=acct[path], value=bal))
                total += bal
            splits.append(piecash.Split(account=acct[equity], value=-total))
            piecash.Transaction(currency=eur, description=desc,
                                post_date=jan1, splits=splits)
        # ETF opening lot — private, so against Privatkapital.
        etf_cost = (etf_price(jan1) * ETF_UNITS).quantize(D("0.01"), ROUND_HALF_UP)
        lot = piecash.Lot(title="MSCI World ETF — Sparplan-Bestand",
                          account=acct[ETF], notes="Eröffnungsbestand", is_closed=0)
        inv = piecash.Split(account=acct[ETF], value=etf_cost, quantity=ETF_UNITS)
        eqs = piecash.Split(account=acct[PRIV_KAPITAL], value=-etf_cost)
        piecash.Transaction(currency=eur, description="Anfangsbestand — MSCI World ETF (privat)",
                            post_date=jan1, splits=[inv, eqs])
        inv.lot = lot
        book.save()
    finally:
        book.close()


# ── Small helpers ──────────────────────────────────────────────────
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


def day_in(first: date, day: int) -> date:
    last = calendar.monthrange(first.year, first.month)[1]
    return first.replace(day=min(day, last))


def _cents(rng, lo: int, hi: int) -> D:
    return (D(rng.randint(lo, hi)) + D(rng.randint(0, 99)) / D(100)).quantize(D("0.01"))


def _lumpy(rng, avg: int) -> int:
    """Lumpy monthly count: 0 in quiet months, spikes in busy ones."""
    return rng.randint(0, 2 * avg)


def _tx(day: date, desc: str, splits: list, notes: str | None = None,
        *, bank: bool = True) -> dict:
    """One transaction dict for ``write_bulk``. Anything with a bank leg
    is dated on a Bankarbeitstag (P5); book-only entries (AfA, the
    1%-Regelung, Pauschalen) keep their calendar date."""
    when = bankday(day) if bank else day
    t = {"date": when, "description": desc, "splits": splits}
    if notes:
        t["notes"] = notes
    return t


def rent_for(year: int) -> D:
    """Studio rent: Mieterhöhung in 2026, then every second year."""
    if year <= 2025:
        return D("1150")
    return D("1190") + D("50") * ((year - 2026) // 2)


def kk_for(year: int) -> D:
    """TK monthly contribution for a voluntarily insured Freiberuflerin
    at the Beitragsbemessungsgrenze (P7): 2025 = 5,512.50 × (14.6% +
    2.45% Zusatzbeitrag) + 5,512.50 × 4.2% PV (kinderlos) = 1,171.41;
    2026 = 5,812.50 × 17.29% + 4.2% = 1,249.11; then +3%/yr with the
    BBG."""
    if year <= 2025:
        return D("1171.41")
    if year == 2026:
        return D("1249.11")
    return (D("1249.11") * (D("1.03") ** (year - 2026))).quantize(D("0.01"), ROUND_HALF_UP)


# ── The payee → tax-treatment table (W1/W2/W3/W4/W7/W8) ───────────
# Every variable business expense is booked through ``_book_expense``
# from this table; ``verify()`` locks the consequences (1571/1577/1787
# carry rows, exempt payees never touch 1576, no 4653 row without a
# note). Treatments:
#   vst19        domestic invoice with 19% USt → 1576
#   vst7         domestic invoice with 7% USt → 1571 (Bahn Fernverkehr,
#                Nahverkehr/Taxi, Bücher/Zeitschriften, Übernachtung,
#                Lebensmittel)
#   exempt       no German USt on the invoice and no §13b either:
#                insurance (§4 Nr. 10), Kfz-Steuer, bank fees (§4 Nr. 8),
#                Briefporto (§4 Nr. 11b), foreign hotels, cross-border
#                transport, tickets to events held abroad (§3a Abs. 3
#                Nr. 5) — gross to the expense account
#   rc_eu        EU supplier, sonstige Leistung B2B → §13b: net to the
#                expense account, 1787 credit + 1577 debit (VA Kz. 46/47/67)
#   rc_drittland Drittland supplier, same §13b mechanics (Kz. 84/85/67)
#   bewirtung    restaurant Beleg: 70% net to 4650, 30% to 4654, the
#                whole VSt to 1576/1571 (§15 Abs. 1a UStG); Anlass and
#                Teilnehmer in the notes, or the row is private (1800)
#   privat       a personal item paid from the business account → 1800
# (category, payee) → (account, treatment, has_branches). ``has_branches``
# gates the "Fil. NNNN" statement suffix (A2): only chains carry one.
PAYEES: dict[tuple[str, str], tuple[str, str, bool]] = {
    # Bürobedarf
    ("buero", "Amazon.de"): (BUEROBEDARF, "vst19", False),
    ("buero", "Viking Direkt"): (BUEROBEDARF, "vst19", False),
    ("buero", "Office Discount"): (BUEROBEDARF, "vst19", False),
    ("buero", "McPaper"): (BUEROBEDARF, "vst19", True),
    # Hardware — routed by net amount (≤ 250 Kleingerät, else GWG)
    ("hardware", "MediaMarkt"): (WERKZEUG, "vst19", True),
    ("hardware", "Cyberport"): (WERKZEUG, "vst19", False),
    ("hardware", "Apple Store München"): (WERKZEUG, "vst19", False),
    ("hardware", "Gravis"): (WERKZEUG, "vst19", True),
    ("hardware", "Amazon.de"): (WERKZEUG, "vst19", False),
    # Local travel (4670): Nahverkehr and Taxi at 7%, Mietwagen at 19%
    ("reise", "MVG München"): (REISE, "vst7", False),
    ("reise", "FREENOW"): (REISE, "vst7", False),
    ("reise", "Deutsche Bahn"): (REISE, "vst7", False),
    ("reise", "Sixt"): (REISE, "vst19", True),
    # Bewirtung (restaurants; Vinzenzmurr is a Metzgerei-Imbiss at 7%)
    ("bewirtung", "L'Osteria"): (BEWIRTUNG, "bewirtung19", True),
    ("bewirtung", "Café Frischhut"): (BEWIRTUNG, "bewirtung19", False),
    ("bewirtung", "dean&david"): (BEWIRTUNG, "bewirtung19", True),
    ("bewirtung", "Hofbräuhaus"): (BEWIRTUNG, "bewirtung19", False),
    ("bewirtung", "Vinzenzmurr"): (BEWIRTUNG, "bewirtung7", True),
    # Aufmerksamkeiten (≤ 50 € netto, §4 Abs. 5 Nr. 1 EStG)
    ("aufmerk", "Blumen Lindner"): (AUFMERK, "vst19", False),
    ("aufmerk", "Confiserie Rottenhöfer"): (AUFMERK, "vst7", False),
    ("aufmerk", "Dallmayr"): (AUFMERK, "vst7", False),
    # Werbung: the platforms are Irish → §13b; Flyeralarm is a vendor bill
    ("werbung", "Google Ads"): (WERBUNG, "rc_eu", False),
    ("werbung", "Meta Platforms"): (WERBUNG, "rc_eu", False),
    ("werbung", "LinkedIn Ads"): (WERBUNG, "rc_eu", False),
    # Porto: Briefporto is USt-frei (Universaldienst), Paketdienste 19%
    ("porto", "Deutsche Post"): (PORTO, "exempt", False),
    ("porto", "DHL Paket"): (PORTO, "vst19", False),
    ("porto", "Hermes Versand"): (PORTO, "vst19", False),
    ("porto", "DPD"): (PORTO, "vst19", False),
    # Bücher / Zeitschriften at 7%
    ("buecher", "Hugendubel"): (BUECHER, "vst7", True),
    ("buecher", "Amazon.de"): (BUECHER, "vst7", False),
    ("buecher", "PAGE Magazin"): (BUECHER, "vst7", False),
    ("buecher", "Rheinwerk Verlag"): (BUECHER, "vst7", False),
    # Kfz-Betriebskosten (Benzin, Parken); ADAC is a private membership
    ("tanken", "SHELL"): (KFZ_BETRIEB, "vst19", True),
    ("tanken", "ARAL"): (KFZ_BETRIEB, "vst19", True),
    ("tanken", "Parkhaus Stachus"): (KFZ_BETRIEB, "vst19", False),
    ("privat", "ADAC e.V. Mitgliedsbeitrag"): (PRIV_DRAW, "privat", False),
    # Fortbildung: online courses from ES/US → §13b; TYPO Berlin is a
    # German-organized event (19%); Adobe MAX is held in Los Angeles
    ("fortbildung", "Domestika"): (FORTBILDUNG, "rc_eu", False),
    ("fortbildung", "Skillshare"): (FORTBILDUNG, "rc_drittland", False),
    ("fortbildung", "TYPO Berlin (Monotype GmbH)"): (FORTBILDUNG, "vst19", False),
    ("fortbildung", "Adobe MAX (Adobe Inc.)"): (FORTBILDUNG, "exempt", False),
    # SaaS (W8): licences to 4964, storage/backup to 4806; all §13b
    ("saas", "Adobe Creative Cloud"): (LIZENZEN, "rc_eu", False),
    ("saas", "Adobe Stock"): (LIZENZEN, "rc_eu", False),
    ("saas", "Figma"): (LIZENZEN, "rc_drittland", False),
    ("saas", "Monotype Fonts"): (LIZENZEN, "rc_drittland", False),
    ("saas", "Dropbox"): (WARTUNG, "rc_eu", False),
    ("saas", "Backblaze"): (WARTUNG, "rc_drittland", False),
    ("saas", "All-Inkl Webhosting"): (INTERNET, "vst19", False),
    # Fixed domestic services (19%) and exempt fixed costs
    ("fix", "Stadtwerke München"): (STROM, "vst19", False),
    ("fix", "Telekom Deutschland"): (INTERNET, "vst19", False),
    ("fix", "Vodafone Mobilfunk"): (MOBILFUNK, "vst19", False),
    ("fix", "1&1 Festnetz"): (TELEKOM, "vst19", False),
    ("fix", "Steuerkanzlei Hoffmann"): (STEUERBERATER, "vst19", False),
    ("fix", "Kontoführungsgebühr"): (BANKGEBUEHR, "exempt", False),
    ("fix", "Hauptzollamt Kfz-Steuer"): (KFZ_STEUER, "exempt", False),
    ("fix", "HUK-Coburg Kfz-Versicherung"): (KFZ_VERS, "exempt", False),
    ("fix", "VGH Berufshaftpflicht"): (VERSICHERUNG, "exempt", False),
    # Trips (annual): domestic rail/hotel at 7%, cross-border exempt
    ("trip", "Deutsche Bahn Fernverkehr"): (REISE, "vst7", False),
    ("trip", "Hotel Weisser Hase Passau"): (UEBERNACHTUNG, "vst7", False),
    ("trip", "Motel One Berlin-Hackescher Markt"): (UEBERNACHTUNG, "vst7", False),
    ("trip", "ÖBB Personenverkehr"): (REISE, "exempt", False),
    ("trip", "Hotel Beethoven Wien"): (UEBERNACHTUNG, "exempt", False),
    ("trip", "Lufthansa"): (REISE, "exempt", False),
    ("trip", "Hotel Figueroa Los Angeles"): (UEBERNACHTUNG, "exempt", False),
}

# Treatment → VAT rate for the domestic/§13b legs.
_RATE = {"vst19": D("19"), "vst7": D("7"), "rc_eu": D("19"), "rc_drittland": D("19"),
         "bewirtung19": D("19"), "bewirtung7": D("7")}

# §13b, EU vs Drittland: the same three-account mechanic; the VA
# reports them on different lines (Kz. 46/47 vs 84/85, VSt on 67).
RC_TREATMENTS = ("rc_eu", "rc_drittland")


def _book_expense(day: date, category: str, payee: str, gross: D,
                  notes: str | None = None, *, acct_from: str = BANKKONTO,
                  account: str | None = None, description: str | None = None) -> dict:
    """THE chokepoint for variable spend: turn (payee, gross) into the
    splits its tax treatment requires. ``gross`` is what the bank line
    shows — for §13b rows that is the net (the foreign invoice carries
    no German USt; the 19% is self-assessed and reclaimed in one go)."""
    account_default, treatment, _ = PAYEES[(category, payee)]
    account = account or account_default
    desc = description or payee
    if treatment == "exempt":
        splits = [(acct_from, -gross), (account, gross)]
    elif treatment == "privat":
        splits = [(acct_from, -gross), (PRIV_DRAW, gross)]
    elif treatment in RC_TREATMENTS:
        vat = (gross * _RATE[treatment] / D("100")).quantize(D("0.01"), ROUND_HALF_UP)
        splits = [(acct_from, -gross), (account, gross), (UST_RC, -vat), (VST_RC, vat)]
    elif treatment.startswith("bewirtung"):
        rate = _RATE[treatment]
        net, vat = _vat_split(gross, rate)
        deductible = (net * D("0.70")).quantize(D("0.01"), ROUND_HALF_UP)
        splits = [(acct_from, -gross), (BEWIRTUNG, deductible),
                  (BEWIRTUNG_NA, net - deductible),
                  (VST19 if rate == 19 else VST7, vat)]
    else:
        rate = _RATE[treatment]
        net, vat = _vat_split(gross, rate)
        splits = [(acct_from, -gross), (account, net),
                  (VST19 if rate == 19 else VST7, vat)]
    return _tx(day, desc, splits, notes)


def _hardware(day: date, payee: str, gross: D, item: str, rng) -> dict:
    """Hardware routes by net price (W8): ≤ €250 net is a Kleingerät
    (4985); above that and ≤ €800 it is a GWG on 4855 with a
    GWG-Verzeichnis note (§6 Abs. 2 EStG)."""
    net, _ = _vat_split(gross, D("19"))
    if net <= D("250"):
        return _book_expense(day, "hardware", payee, gross,
                             f"Kleingerät: {item} ({net:.2f} € netto)")
    nr = f"{day.year}-{rng.randint(1, 99):02d}"
    return _book_expense(day, "hardware", payee, gross,
                         f"GWG: {item}, AK {net:.2f} € netto — GWG-Verzeichnis Nr. {nr}",
                         account=GWG)


def _pick(rng, category: str, names: list[str]) -> str:
    """A merchant string, with a German-style branch reference (Fil. =
    Filiale) only for chains that have branches (A2)."""
    name = rng.choice(names)
    key = (category, name)
    has_branches = PAYEES[key][2] if key in PAYEES else name in P_BRANCHES
    if has_branches and rng.random() < 0.45:
        return f"{name} Fil. {rng.randint(1, 999):04d}"
    return name


def _payee_of(description: str) -> str:
    return description.split(" Fil. ")[0]


# Monthly SaaS/tooling — real product names, routed through PAYEES
# (SaaS to 4964/4806, W8) at the price the foreign invoice shows (net).
# Adobe Creative Cloud is the single Adobe subscription (P3).
SUBSCRIPTIONS = [
    ("Adobe Creative Cloud", "54.98", "ADOBE-IE"),
    ("Figma", "15.00", "FIGMA-US"),
    ("Adobe Stock", "24.99", "ADOBE-IE"),
    ("Monotype Fonts", "9.99", "MONOTYPE-US"),
    ("Dropbox", "9.99", "DROPBOX-IE"),
    ("All-Inkl Webhosting", "14.28", "ALLINKL"),
    ("Backblaze", "9.00", "BACKBLAZE-US"),
]


def _rechnungsnr(prefix: str, when: date, rng) -> str:
    return f"{prefix}-{when:%Y%m}-{rng.randint(100000, 999999)}"


# ── Phase 4: recurring (Miete, Telekom, Privatentnahme, loans) ─────
def gen_recurring() -> list[dict]:
    """Fixed monthly business obligations + loans. Real Dauerauftrag-
    style descriptions, rent rising, utilities seasonal, every bank
    posting on a Bankarbeitstag."""
    rng = random.Random(SEED + 4)
    txns = []
    hyp = _amort(D("395000"), D("3.65"), 300)
    kfz = _amort(D("16500"), D("4.49"), 60)
    for first in iter_months():
        rent = rent_for(first.year)
        txns.append(_tx(first.replace(day=1), "Dauerauftrag Miete Studio Schwabing",
                        [(BANKKONTO, -rent), (MIETE, rent)],
                        "Studio Schwabing, Vermieter ohne Option zur USt (§9 UStG)"))
        # Utilities: higher in winter (heating), lower in summer.
        winter = first.month in (11, 12, 1, 2, 3)
        strom = D("168.00") if winter else D("94.00")
        txns.append(_book_expense(first.replace(day=4), "fix", "Stadtwerke München", strom,
                                  f"Abschlag Strom/Gas Studio {first:%m/%Y}"))
        for name, gross in (("Telekom Deutschland", D("49.99")),
                            ("Vodafone Mobilfunk", D("39.99")),
                            ("1&1 Festnetz", D("24.99"))):
            txns.append(_book_expense(first.replace(day=3), "fix", name, gross,
                                      f"Rechnungsnr. {_rechnungsnr(name[:3].upper(), first, rng)}"))
        for name, amount, prefix in SUBSCRIPTIONS:
            txns.append(_book_expense(first.replace(day=2), "saas", name, D(amount),
                                      f"Rechnungsnr. {_rechnungsnr(prefix, first, rng)} "
                                      f"— Abo {first:%m/%Y}"))
        txns.append(_book_expense(first.replace(day=8), "fix", "Steuerkanzlei Hoffmann", D("89.25"),
                                  f"Buchführung/USt-VA {first:%m/%Y}, Rechnungsnr. "
                                  f"{_rechnungsnr('SKH', first, rng)}"))
        txns.append(_book_expense(first.replace(day=2), "fix", "Kontoführungsgebühr", D("8.90")))
        # Kfz financing: business loan, business interest (2121).
        try:
            pmt, interest, principal = next(kfz)
            txns.append(_tx(day_in(first, 15), "VW Bank Kfz-Finanzierung",
                            [(BANKKONTO, -pmt), (KFZ_FIN, principal), (ZINS_KFZ, interest)]))
        except StopIteration:
            pass
        # Hypothek: PRIVATE. The bank leg leaves the business as a
        # Privatentnahme (1800); on the private side the Tilgung reduces
        # the Hypothek and the Zinsen are a drawing against Privatkapital
        # (W6: no EXPENSE account anywhere). One transaction, both zones
        # balanced, and the statement line is still the single
        # Dauerauftrag (S1).
        try:
            pmt, interest, principal = next(hyp)
            txns.append(_tx(day_in(first, 30), "Dauerauftrag Hypothek Sparkasse (privat)",
                            [(BANKKONTO, -pmt), (PRIV_DRAW, pmt),
                             (HYPOTHEK, principal), (ZINS_HYP_PRIV, interest),
                             (PRIV_KAPITAL, -pmt)]))
        except StopIteration:
            pass
        # S3: 1%-Regelung — private use of the business Pkw, booked on
        # the last day of the month: 1% of the Bruttolistenpreis, 80% of
        # it with 19% USt (8924 + 1776), 20% without (8920), the gross
        # as unentgeltliche Wertabgabe (1880).
        txns.append(_kfz_privatnutzung(day_in(first, 31)))
        if first.month == 1:
            # W1: insurance premiums carry Versicherungsteuer, not USt —
            # gross to 4520/4360, no 1576 leg. Kfz-Steuer likewise.
            txns.append(_book_expense(first.replace(day=20), "fix", "Hauptzollamt Kfz-Steuer", D("184.00"),
                                      f"Kfz-Steuer {first.year}, Steuernummer 42/512/03127"))
            txns.append(_book_expense(first.replace(day=22), "fix", "HUK-Coburg Kfz-Versicherung", D("612.00"),
                                      f"Jahresbeitrag {first.year}, Vertrag KH/VK — Versicherungsteuer, kein VSt-Abzug"))
            txns.append(_book_expense(first.replace(day=24), "fix", "VGH Berufshaftpflicht", D("428.00"),
                                      f"Berufshaftpflicht Grafikdesign {first.year} — Versicherungsteuer, kein VSt-Abzug"))
            # W7: ADAC is a personal membership, once a year, no VSt.
            txns.append(_book_expense(first.replace(day=10), "privat", "ADAC e.V. Mitgliedsbeitrag", D("94.00"),
                                      "privat"))
    return txns


def _kfz_privatnutzung(when: date) -> dict:
    monthly = (PKW_LISTENPREIS * D("0.01")).quantize(D("0.01"))     # 320.00
    taxable = (monthly * D("0.8")).quantize(D("0.01"))              # 256.00
    ust = (taxable * D("0.19")).quantize(D("0.01"), ROUND_HALF_UP)   # 48.64
    untaxed = monthly - taxable                                      # 64.00
    return _tx(when, "Private Kfz-Nutzung (1%-Regelung)",
               [(PRIV_WERTABGABE, monthly + ust), (REV_KFZ_19, -taxable),
                (UST19, -ust), (REV_KFZ_0, -untaxed)],
               "1% von 32.000 € BLP; 80% mit 19% USt (Abschn. 15.23 UStAE)", bank=False)


def gen_yearend() -> list[dict]:
    """S8: AfA bookings on 31 December of every completed year within
    the horizon — the Pkw is the only capitalized asset (GWG are
    expensed on purchase; the residence is private and not
    depreciable)."""
    txns = []
    for year in range(YEAR, THROUGH.year + 1):
        when = date(year, 12, 31)
        afa = pkw_afa(year)
        if when > THROUGH or afa == 0:
            continue
        txns.append(_tx(when, f"AfA Pkw {year} (linear, 6 Jahre)",
                        [(AFA_KFZ, afa), (PKW, -afa)],
                        f"AK {PKW_AK} € netto / 6 Jahre; Erstzulassung 01/2024", bank=False))
    return txns


# ── Phase 5: variable business spend ───────────────────────────────
# Client contacts for Bewirtung/Aufmerksamkeiten notes.
CONTACTS = {
    "Verlag Bergblick": "M. Huber", "Atelier Donau": "K. Reisinger",
    "Stadtmarketing München": "Dr. A. Seidl", "BioBackhaus GmbH": "T. Wimmer",
    "Praxis Dr. Vogel": "Dr. C. Vogel", "Architekturbüro Lindner": "J. Lindner",
    "Festival Tollwood": "N. Berger", "Café Kosmos": "P. Ahmadi",
    "Brauerei Aukofer": "F. Aukofer", "Modehaus Lindberg": "S. Lindberg",
}
ANLAESSE = ["Projektbesprechung", "Briefing neue Kampagne", "Abnahme Layout",
            "Kick-off Verpackungsdesign", "Jahresgespräch", "Präsentation Entwürfe",
            "Korrekturschleife Katalog"]
HARDWARE = ["USB-C Dock", "Externe SSD 2 TB", "Monitorarm", "Studio-Kopfhörer",
            "Farbkalibrierungsgerät", "Webcam", "Ringlicht", "Tastatur", "Maus",
            "Grafiktablett-Stift", "NAS-Festplatte 8 TB", "Drucker Etiketten"]
BUERO_ITEMS = ["Druckerpapier, Toner", "Skizzenbücher, Marker", "Ordner, Register",
               "Präsentationsmappen", "Briefumschläge", "Klebeband, Cutter"]
BUECHER_ITEMS = ["Typografie-Fachbuch", "PAGE Magazin Abo", "Design-Jahrbuch",
                 "Farbtheorie-Handbuch", "InDesign-Praxisbuch"]

# Business spend: (category, lo, hi, avg-count/mo, payees)
BUSINESS = [
    ("buero", 8, 90, 4, ["Amazon.de", "Viking Direkt", "Office Discount", "McPaper"]),
    ("hardware", 25, 780, 2, ["MediaMarkt", "Cyberport", "Apple Store München", "Gravis", "Amazon.de"]),
    ("reise", 3, 120, 4, ["MVG München", "FREENOW", "Deutsche Bahn", "Sixt"]),
    ("bewirtung", 18, 95, 3, ["L'Osteria", "Café Frischhut", "Vinzenzmurr", "Hofbräuhaus", "dean&david"]),
    ("aufmerk", 15, 55, 1, ["Blumen Lindner", "Confiserie Rottenhöfer", "Dallmayr"]),
    ("werbung", 30, 420, 1, ["Google Ads", "Meta Platforms", "LinkedIn Ads"]),
    ("porto", 3, 30, 3, ["Deutsche Post", "DHL Paket", "Hermes Versand", "DPD"]),
    ("buecher", 10, 65, 1, ["Hugendubel", "Amazon.de", "PAGE Magazin", "Rheinwerk Verlag"]),
    ("tanken", 30, 110, 4, ["SHELL", "ARAL", "Parkhaus Stachus"]),
    ("fortbildung", 60, 480, 1, ["Domestika", "Skillshare"]),
]


def gen_variable() -> list[dict]:
    """Lumpy seeded business spend with real merchant names, every row
    through the payee table (its VAT treatment) and carrying the note a
    Beleg would (A3)."""
    rng = random.Random(SEED + 6)
    txns = []
    clients = list(CONTACTS)
    for first in iter_months():
        for category, lo, hi, avg, names in BUSINESS:
            for _ in range(_lumpy(rng, avg)):
                day = day_in(first, rng.randint(2, 27))
                desc = _pick(rng, category, names)
                payee = _payee_of(desc)
                gross = _cents(rng, lo, hi)
                if category == "hardware":
                    txns.append(_hardware(day, payee, gross, rng.choice(HARDWARE), rng))
                    continue
                if category == "bewirtung":
                    # W3: a Bewirtung needs Anlass + Teilnehmer; a fifth
                    # of the restaurant lines have no Beleg — private.
                    if rng.random() < 0.2:
                        txns.append(_tx(day, desc, [(BANKKONTO, -gross), (PRIV_DRAW, gross)], "privat"))
                        continue
                    client = rng.choice(clients)
                    note = (f"Bewirtung — Anlass: {rng.choice(ANLAESSE)} {client}; "
                            f"Teilnehmer: S. Brenner, {CONTACTS[client]} ({client}); "
                            "Bewirtungsbeleg liegt vor")
                    txns.append(_book_expense(day, category, payee, gross, note, description=desc))
                    continue
                if category == "aufmerk":
                    client = rng.choice(clients)
                    note = (f"Geschenk an {CONTACTS[client]} ({client}), Anlass: "
                            f"{rng.choice(['Projektabschluss', 'Geburtstag', 'Weihnachten', 'Jubiläum'])}; "
                            "≤ 50 € netto (§4 Abs. 5 Nr. 1 EStG)")
                    txns.append(_book_expense(day, category, payee, gross, note))
                    continue
                if category == "reise":
                    client = rng.choice(clients)
                    note = f"Zweck: Kundentermin {client}" if payee != "Sixt" \
                        else f"Zweck: Mietwagen Fototermin {client}"
                    txns.append(_book_expense(day, category, payee, gross, note, description=desc))
                    continue
                if category in ("werbung", "fortbildung"):
                    prefix = payee.split()[0].upper()[:6]
                    kind = "Kampagne" if category == "werbung" else "Online-Kurs"
                    note = f"Rechnungsnr. {_rechnungsnr(prefix, day, rng)} — {kind}; §13b UStG (Reverse Charge)"
                    txns.append(_book_expense(day, category, payee, gross, note))
                    continue
                if category == "buero":
                    note = f"{rng.choice(BUERO_ITEMS)}; Bestellnr. {_rechnungsnr('B', day, rng)}"
                elif category == "buecher":
                    note = f"{rng.choice(BUECHER_ITEMS)} (7% USt)"
                elif category == "porto":
                    note = "Versand Druckmuster an Kunden" if payee != "Deutsche Post" \
                        else "Briefporto (USt-frei, §4 Nr. 11b UStG)"
                else:  # tanken
                    note = "Tankbeleg Pkw (Benzin)" if payee != "Parkhaus Stachus" else "Parken Kundentermin Innenstadt"
                txns.append(_book_expense(day, category, payee, gross, note, description=desc))
    return txns


# ── Phase 5b: trips (P4) — one per event per year, with lodging ────
def _pauschale(day: date, trip: str, amount: D) -> dict:
    """Verpflegungsmehraufwand: a deductible Pauschale without a cash
    movement — 4674 against 1890 Privateinlage."""
    return _tx(day, f"Reisekostenabrechnung Verpflegungsmehraufwand — {trip}",
               [(VERPFLEGUNG, amount), (PRIV_EINLAGE, -amount)],
               f"Pauschalen §9 Abs. 4a EStG: {trip}", bank=False)


def gen_trips() -> list[dict]:
    """Four business trips a year, each a whole Beleg set: transport,
    lodging, Pauschalen, and the conference ticket where there is one.
    Passau and Wien match the client invoices; TYPO Berlin and Adobe MAX
    are annual conferences (P4), not bimonthly charges."""
    txns = []
    for year in range(YEAR, THROUGH.year + 1):
        # Kundentermin Passau (Atelier Donau) — 2 days in March
        d = date(year, 3, 10)
        z = "Kundentermin Atelier Donau, Passau (Briefing Illustrationsserie)"
        txns.append(_book_expense(d, "trip", "Deutsche Bahn Fernverkehr", D("58.00"),
                                  f"Zweck: {z}; München–Passau und zurück (Fernverkehr 7%)"))
        txns.append(_book_expense(d, "trip", "Hotel Weisser Hase Passau", D("119.00"),
                                  f"Zweck: {z}; 1 Übernachtung (7% USt, ohne Frühstück)"))
        txns.append(_pauschale(date(year, 3, 11), "Passau 10.–11.03.", D("28.00")))
        # TYPO Berlin — 3 days in May
        d = date(year, 5, 21)
        z = f"Konferenz TYPO Berlin {year}"
        txns.append(_book_expense(date(year, 4, 14), "fortbildung", "TYPO Berlin (Monotype GmbH)", D("702.10"),
                                  f"Zweck: {z}; Konferenzticket, Rechnungsnr. TYPO-{year}-{1440 + year % 100}"))
        txns.append(_book_expense(date(year, 4, 14), "trip", "Deutsche Bahn Fernverkehr", D("189.00"),
                                  f"Zweck: {z}; ICE München–Berlin und zurück (Fernverkehr 7%)"))
        txns.append(_book_expense(d, "trip", "Motel One Berlin-Hackescher Markt", D("258.00"),
                                  f"Zweck: {z}; 2 Übernachtungen (7% USt)"))
        txns.append(_pauschale(date(year, 5, 23), "Berlin 21.–23.05.", D("56.00")))
        # Werkstatt Neubau, Wien — Corporate Design / Messestand (Nov)
        d = date(year, 11, 4)
        z = "Kundentermin Werkstatt Neubau Kommunikation, Wien (Messestand-Grafik)"
        txns.append(_book_expense(d, "trip", "ÖBB Personenverkehr", D("112.90"),
                                  f"Zweck: {z}; Railjet München–Wien und zurück — grenzüberschreitend, keine deutsche USt"))
        txns.append(_book_expense(d, "trip", "Hotel Beethoven Wien", D("164.00"),
                                  f"Zweck: {z}; 1 Übernachtung — österreichische USt, kein VSt-Abzug in DE"))
        txns.append(_pauschale(date(year, 11, 5), "Wien 04.–05.11.", D("66.00")))
        # Adobe MAX, Los Angeles — late October
        d = date(year, 10, 27)
        z = f"Konferenz Adobe MAX {year}, Los Angeles"
        txns.append(_book_expense(date(year, 8, 18), "fortbildung", "Adobe MAX (Adobe Inc.)", D("1750.00"),
                                  f"Zweck: {z}; Konferenzpass — Veranstaltungsort USA, §3a Abs. 3 Nr. 5 UStG, in DE nicht steuerbar"))
        txns.append(_book_expense(date(year, 8, 18), "trip", "Lufthansa", D("1148.00"),
                                  f"Zweck: {z}; Flug MUC–LAX–MUC — grenzüberschreitende Beförderung, keine USt (§26 Abs. 3 UStG)"))
        txns.append(_book_expense(d, "trip", "Hotel Figueroa Los Angeles", D("987.00"),
                                  f"Zweck: {z}; 3 Übernachtungen, USD-Belastung umgerechnet — kein VSt-Abzug"))
        txns.append(_pauschale(date(year, 10, 30), "Los Angeles 27.–30.10.", D("214.00")))
    return txns


# ── Phase 6: personal living (Privatentnahme) ──────────────────────
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
# Chains with branches (A2) among the personal merchants.
P_BRANCHES = {"REWE", "EDEKA", "LIDL", "ALDI Süd", "dm-drogerie", "Rossmann", "L'Osteria",
              "Vapiano", "dean&david", "Starbucks", "Bäckerei Rischart", "MediaMarkt",
              "OBI Baumarkt", "ARAL", "Müller Drogerie", "Cinemaxx", "Hugendubel", "Gravis",
              "McPaper", "Vinzenzmurr", "SHELL", "Sixt"}


def gen_personal() -> list[dict]:
    """Personal living + Krankenkasse, itemized as Privatentnahme draws."""
    rng = random.Random(SEED + 12)
    txns = []
    for first in iter_months():
        # Krankenkasse premium at the Beitragsbemessungsgrenze (P7),
        # rising each January with the BBG.
        kk = kk_for(first.year)
        txns.append(_tx(day_in(first, 1), "Techniker Krankenkasse Beitrag",
                        [(BANKKONTO, -kk), (KRANKENKASSE, kk)],
                        "freiwillig versichert, Höchstbeitrag (KV + PV)"))
        for name, amt in P_SUBS:
            txns.append(_tx(day_in(first, 5), name,
                            [(BANKKONTO, -D(amt)), (PRIV_DRAW, D(amt))]))
        for names, lo, hi, avg in [
                (P_GROCERIES, 12, 95, 8), (P_DINING, 14, 65, 4),
                (P_COFFEE, 3, 14, 7), (P_HOUSE, 25, 600, 1),
                (P_TRANSPORT, 3, 60, 3), (P_MISC, 12, 140, 2)]:
            for _ in range(_lumpy(rng, avg)):
                amt = _cents(rng, lo, hi)
                acct = BANKKONTO if rng.random() < 0.9 else POSTBANK
                txns.append(_tx(day_in(first, rng.randint(2, 27)),
                                _pick(rng, "personal", names),
                                [(acct, -amt), (PRIV_DRAW, amt)]))
        if rng.random() < 0.12:
            amt = _cents(rng, 600, 2500)
            txns.append(_tx(day_in(first, rng.randint(2, 27)),
                            "Privateinlage (Übertrag privat)",
                            [(BANKKONTO, amt), (PRIV_EINLAGE, -amt)]))
    return txns


# ── Phase 7: business — every euro of revenue is an invoice (W5) ───
# S7: customer master data. Every USt-IdNr. below is WELL-FORMED BUT
# DELIBERATELY INVALID — the DE numbers fail the ISO 7064 MOD 11,10
# check digit (``_ustid`` builds them that way) and the ATU number fails
# Austria's weighted check — so the demo can never be mistaken for a
# real registration. GnuCash has no VAT-ID column; German practice
# keeps it in the customer notes.
def _ustid(digits8: str) -> str:
    """A DE USt-IdNr. whose check digit is deliberately one off the
    ISO 7064 MOD 11,10 value — well-formed, never valid."""
    p = 10
    for ch in digits8:
        s = (int(ch) + p) % 10 or 10
        p = (2 * s) % 11
    check = (11 - p) % 10
    return f"DE{digits8}{(check + 1) % 10}"


CUSTOMERS = {
    "verlag": dict(
        name="Verlag Bergblick GmbH", currency="EUR",
        notes="Editorial-Design, München (Retainer). USt-IdNr. DE812345671",
        address={"addr1": "Leopoldstraße 48", "addr2": "80802 München",
                 "addr3": "Deutschland", "email": "buchhaltung@verlag-bergblick.example"}),
    "atelier": dict(
        name="Atelier Donau", currency="EUR",
        notes="Illustration / Nutzungsrechte (Lizenzen, 7%). USt-IdNr. DE811234567",
        address={"addr1": "Am Gries 7", "addr2": "94032 Passau",
                 "addr3": "Deutschland"}),
    "stadtmarketing": dict(
        name="Stadtmarketing München GmbH", currency="EUR",
        notes=f"Kampagnen und Broschüren. USt-IdNr. {_ustid('81326540')}",
        address={"addr1": "Herzog-Wilhelm-Straße 15", "addr2": "80331 München",
                 "addr3": "Deutschland"}),
    "biobackhaus": dict(
        name="BioBackhaus GmbH", currency="EUR",
        notes=f"Verpackung und Filialgrafik. USt-IdNr. {_ustid('81409877')}",
        address={"addr1": "Gewerbering 4", "addr2": "85748 Garching",
                 "addr3": "Deutschland"}),
    "vogel": dict(
        name="Praxis Dr. Vogel", currency="EUR",
        notes="Zahnarztpraxis, keine USt-IdNr. (Heilberuf, §4 Nr. 14 UStG — "
              "Rechnungen mit 19% USt, kein Vorsteuerabzug beim Kunden).",
        address={"addr1": "Hohenzollernstraße 92", "addr2": "80796 München",
                 "addr3": "Deutschland"}),
    "lindner": dict(
        name="Architekturbüro Lindner", currency="EUR",
        notes=f"Wettbewerbs- und Portfoliografik. USt-IdNr. {_ustid('81577321')}",
        address={"addr1": "Nymphenburger Straße 120", "addr2": "80636 München",
                 "addr3": "Deutschland"}),
    "tollwood": dict(
        name="Tollwood GmbH", currency="EUR",
        notes=f"Festivalplakate und Programmhefte. USt-IdNr. {_ustid('81398844')}",
        address={"addr1": "Ganghoferstraße 33", "addr2": "80339 München",
                 "addr3": "Deutschland"}),
    "kosmos": dict(
        name="Café Kosmos", currency="EUR",
        notes=f"Karten, Plakate, Wandillustration. USt-IdNr. {_ustid('81460219')}",
        address={"addr1": "Dachauer Straße 7", "addr2": "80335 München",
                 "addr3": "Deutschland"}),
    "aukofer": dict(
        name="Brauerei Aukofer KG", currency="EUR",
        notes=f"Etiketten und Anzeigen. USt-IdNr. {_ustid('81288756')}",
        address={"addr1": "Brauhausgasse 2", "addr2": "93309 Kelheim",
                 "addr3": "Deutschland"}),
    "lindberg": dict(
        name="Modehaus Lindberg", currency="EUR",
        notes=f"Kataloge und Schaufenstergrafik. USt-IdNr. {_ustid('81344590')}",
        address={"addr1": "Sendlinger Straße 21", "addr2": "80331 München",
                 "addr3": "Deutschland"}),
    # Drittland (S6): USD, place of supply = recipient → 8338, no USt.
    "lumen": dict(
        name="Lumen Labs Inc.", currency="USD",
        notes="US-Startup, Brand-Design. Drittland — sonstige Leistung nach "
              "§3a Abs. 2 UStG, in Deutschland nicht steuerbar (8338).",
        address={"addr1": "2261 Market Street #4021", "addr2": "San Francisco, CA 94114",
                 "addr3": "USA"}),
    # EU B2B (S6): Reverse Charge §13b → 8336; the invoice carries the
    # mandatory "Steuerschuldnerschaft des Leistungsempfängers" note.
    "wien": dict(
        name="Werkstatt Neubau Kommunikation GmbH", currency="EUR",
        notes="Wien — innergemeinschaftliche sonstige Leistung, Reverse Charge "
              "(§13b UStG). USt-IdNr. ATU12345678",
        address={"addr1": "Burggasse 21", "addr2": "1070 Wien",
                 "addr3": "Österreich"}),
}
RC_NOTE = "Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge, §13b UStG)"
LIZENZ_NOTE = ("Einräumung zeitlich und räumlich beschränkter Nutzungsrechte — "
               "Hauptleistung ist die Urheberrechtsübertragung (§12 Abs. 2 Nr. 7c UStG, 7%)")

# Projects per customer for the invoice pool; ``lizenz`` items are the
# 7% licence-only invoices (P10: licence is the Hauptleistung).
PROJECTS = {
    "stadtmarketing": (["Plakatkampagne Stadtgründungsfest", "Broschüre Radlhauptstadt München",
                        "Social-Media-Templates Tourismus", "Messewand ITB"], []),
    "biobackhaus": (["Verpackungsdesign Brotlinie", "Filialplakate Saison",
                     "Speisekarten-Relaunch", "Etiketten Bio-Sortiment"], []),
    "vogel": (["Praxis-Logo und Geschäftsausstattung", "Website-Layout Praxis",
               "Patientenflyer Prophylaxe"], []),
    "lindner": (["Wettbewerbsplakate Wohnquartier", "Portfolio-Broschüre",
                 "Bauschild-Grafik"], []),
    "tollwood": (["Festivalplakat Sommer", "Programmheft Layout", "Lageplan-Grafik"], []),
    "kosmos": (["Speisekarte und Getränkekarte", "Event-Plakate"],
               ["Illustration Wandmotiv Gastraum"]),
    "aukofer": (["Etikettenserie Festbier", "Anzeigenkampagne Sommer"],
                ["Illustration Sudhaus-Motiv"]),
    "lindberg": (["Katalog Frühjahr/Sommer", "Schaufenster-Grafik", "Newsletter-Templates"], []),
    "atelier": ([], ["Illustrationsserie Donauufer", "Illustration Kinderbuch",
                     "Illustrationsserie Jahreszeiten"]),
}
_SHORT = {"verlag": "Verlag Bergblick", "atelier": "Atelier Donau",
          "stadtmarketing": "Stadtmarketing München", "biobackhaus": "BioBackhaus",
          "vogel": "Praxis Dr. Vogel", "lindner": "Architekturbüro Lindner",
          "tollwood": "Tollwood", "kosmos": "Café Kosmos", "aukofer": "Brauerei Aukofer",
          "lindberg": "Modehaus Lindberg", "lumen": "Lumen Labs", "wien": "Werkstatt Neubau"}


def _invoice_plan() -> list[dict]:
    """Every customer invoice of the book, as data. ``run_business``
    creates them in date order so the server's counter yields
    chronological, gap-free numbers (S5 — §14 UStG / GoBD): 000001 on
    the earliest date, never a later date with a smaller number.

    One channel per customer (P2): Verlag Bergblick is a monthly
    retainer, every other domestic client is invoiced per project from a
    seeded pool — the same monthly volume the old direct Honorar
    deposits carried, now with a Leistungszeitraum, a number and a
    document. Payment lands ``3–32`` seeded days after posting on a
    Bankarbeitstag; a few run past Net 14, none past Net 14 + 45."""
    rng = random.Random(SEED + 9)
    plan: list[dict] = []
    pool = [k for k in PROJECTS if k != "atelier"]
    lizenz_pool = [k for k, (_, liz) in PROJECTS.items() if liz]

    def _pay(opened: date) -> date:
        return bankday(opened + timedelta(days=int(rng.triangular(3, 32, 10))))

    for first in iter_months():
        # The retainer — 2,400 net in 2025, +100 each year.
        opened = weekday_in(first, 5)
        net = D("2400") + D("100") * (first.year - YEAR)
        plan.append(dict(customer="verlag", opened=opened, currency="EUR",
                         account=REV19, description=f"Editorial-Design {first:%m/%Y} (Retainer)",
                         price=net, taxtable="USt 19%", ar=AR,
                         notes=f"Leistungszeitraum: {first:%m/%Y}",
                         pay=(_pay(opened), (net * D("1.19")).quantize(D("0.01")))))
        # Project invoices from the pool — a Munich senior-designer
        # volume: the book must carry the ESt and a ceiling-level
        # Krankenkasse and still sweep a surplus.
        for _ in range(rng.randint(4, 6)):
            net = D(rng.randint(1200, 3000))
            lizenz = rng.random() < 0.2
            key = rng.choice(lizenz_pool if lizenz else pool)
            projects, lizenzen = PROJECTS[key]
            opened = weekday_in(first, rng.randint(2, 26))
            if lizenz:
                title = rng.choice(lizenzen)
                plan.append(dict(customer=key, opened=opened, currency="EUR",
                                 account=REV7, description=f"Einräumung von Nutzungsrechten — {title}",
                                 price=net, taxtable="USt 7%", ar=AR,
                                 notes=f"Leistungszeitraum: {first:%m/%Y}. {LIZENZ_NOTE}",
                                 entry_notes=LIZENZ_NOTE,
                                 pay=(_pay(opened), (net * D("1.07")).quantize(D("0.01")))))
            else:
                plan.append(dict(customer=key, opened=opened, currency="EUR",
                                 account=REV19, description=rng.choice(projects),
                                 price=net, taxtable="USt 19%", ar=AR,
                                 notes=f"Leistungszeitraum: {first:%m/%Y}",
                                 pay=(_pay(opened), (net * D("1.19")).quantize(D("0.01")))))
    # EU B2B, Reverse Charge (S6 / R2): no tax line, 8336 — the May
    # Corporate Design and the November Messestand, every year.
    for year in range(YEAR, THROUGH.year + 1):
        for opened, paid, desc, net in (
                (date(year, 5, 14), date(year, 5, 30), "Corporate Design Relaunch", D("2900")),
                (date(year, 11, 6), date(year, 11, 21), "Messestand-Grafik Wien", D("1750"))):
            plan.append(dict(customer="wien", opened=opened, currency="EUR",
                             account=REV_EU_B2B, description=f"{desc} — {RC_NOTE}",
                             price=net, taxtable=None, ar=AR, notes=RC_NOTE,
                             pay=(bankday(paid), net)))
    # THE ACCEPTANCE TEST (R3): USD client (Drittland, 8338), post & pay
    # at different EUR/USD rates -> realized FX into a type-resolved
    # INCOME child (German leaf under GNUCASH_LOCALE=de).
    plan.append(dict(customer="lumen", opened=US_POST, currency="USD",
                     account=REV_DRITTLAND, description="Brand identity system (Drittland, §3a Abs. 2 UStG)",
                     price=D("3500"), taxtable=None, ar=AR_USD,
                     notes="Leistungszeitraum: 08/2025. Nicht steuerbar in Deutschland (§3a Abs. 2 UStG)",
                     pay=(US_PAY, D("3500"))))
    plan.sort(key=lambda p: (p["opened"], p["customer"], p["description"]))
    return plan


def _bill_plan() -> list[dict]:
    """Vendor bills through A/P: the annual print run at Flyeralarm
    (Visitenkarten, Portfolio-Mappen) with domestic 19% VSt on 1576."""
    plan = []
    for year in range(YEAR, THROUGH.year + 1):
        opened = date(year, 2, 3)
        plan.append(dict(opened=opened, description=f"Druck Portfolio-Mappen und Visitenkarten {year}",
                         price=D("660"), pay=bankday(opened + timedelta(days=9))))
    return plan


def _ensure_master_data(book: GnuCashBook) -> dict:
    """Billterm, taxtables, customers and the vendor — idempotent by
    name, so continuation runs reuse the prefix's records."""
    existing_terms = {row.get("name") for row in _rows(book.list_billterms(compact=False))}
    if "Net 14" not in existing_terms:
        book.create_billterm(name="Net 14", due_days=14, description="14 Tage netto")
    existing_tables = {row.get("name") for row in _rows(book.list_taxtables(compact=False))}
    for name, rate, account in (("USt 19%", "19", UST19), ("USt 7%", "7", UST7),
                                ("VSt 19%", "19", VST19)):
        if name not in existing_tables:
            book.create_taxtable(name=name, entries=[
                {"type": "percentage", "amount": rate, "account": account}])
    customers = {row.get("name"): row.get("id")
                 for row in _rows(book.list_customers(compact=False, limit=250))}
    ids = {}
    for key, spec in CUSTOMERS.items():
        ids[key] = customers.get(spec["name"]) or book.create_customer(**spec)["id"]
    vendors = {row.get("name"): row.get("id")
               for row in _rows(book.list_vendors(compact=False, limit=250))}
    vendor_name = "Flyeralarm GmbH"
    vendor = vendors.get(vendor_name) or book.create_vendor(
        name=vendor_name, currency="EUR",
        notes="Online-Druckerei. USt-IdNr. DE813574620",
        address={"addr1": "Alfred-Nobel-Straße 18", "addr2": "97080 Würzburg",
                 "addr3": "Deutschland"})["id"]
    return {"customers": ids, "vendor": vendor}


def _rows(envelope) -> list[dict]:
    if isinstance(envelope, dict):
        return next((v for v in envelope.values() if isinstance(v, list)), [])
    return list(envelope or [])


def run_business(book: GnuCashBook, since: date | None = None) -> dict:
    """Post every invoice and bill opened after ``since`` (all of them
    on a fresh build) and pay those whose payment date is within the
    horizon. Prefix documents are settled by the continuation engine."""
    counts = {"customers": 0, "vendors": 1, "invoices": 0, "paid": 0, "bills": 0}
    master = _ensure_master_data(book)
    ids = master["customers"]
    counts["customers"] = len(ids)

    for spec in _invoice_plan():
        if spec["opened"] > THROUGH or (since is not None and spec["opened"] <= since):
            continue
        inv = book.create_invoice(customer_id=ids[spec["customer"]],
                                  date_opened=spec["opened"].isoformat(),
                                  currency=spec["currency"], term="Net 14",
                                  notes=spec.get("notes", ""))
        book.add_invoice_entry(invoice_id=inv["id"], account=spec["account"],
                               description=spec["description"], quantity="1",
                               price=str(spec["price"]), taxtable=spec["taxtable"],
                               notes=spec.get("entry_notes", ""))
        book.post_invoice(invoice_id=inv["id"], post_account=spec["ar"],
                          post_date=spec["opened"].isoformat(), owner_type="customer")
        counts["invoices"] += 1
        if spec["pay"] and spec["pay"][0] <= THROUGH:
            paid, amount = spec["pay"]
            short = _SHORT[spec["customer"]]
            book.pay_invoice(invoice_id=inv["id"], payment_account=BANKKONTO,
                             amount=str(amount), payment_date=paid.isoformat(),
                             owner_type="customer",
                             description=f"Zahlungseingang {short} Re. {inv['id']}",
                             memo=f"SEPA-Gutschrift {CUSTOMERS[spec['customer']]['name']} "
                                  f"RE-{inv['id']}")
            counts["paid"] += 1

    for spec in _bill_plan():
        if spec["opened"] > THROUGH or (since is not None and spec["opened"] <= since):
            continue
        bill = book.create_bill(vendor_id=master["vendor"],
                                date_opened=spec["opened"].isoformat(),
                                currency="EUR", term="Net 14",
                                notes=f"Rechnungsnr. FA-{spec['opened'].year}-{71824}")
        book.add_bill_entry(bill_id=bill["id"], account=WERBUNG,
                            description=spec["description"], quantity="1",
                            price=str(spec["price"]), taxtable="VSt 19%")
        book.post_invoice(invoice_id=bill["id"], post_account=AP,
                          post_date=spec["opened"].isoformat(), owner_type="vendor")
        counts["bills"] += 1
        if spec["pay"] <= THROUGH:
            book.pay_invoice(invoice_id=bill["id"], payment_account=BANKKONTO,
                             amount=str((spec["price"] * D("1.19")).quantize(D("0.01"))),
                             payment_date=spec["pay"].isoformat(), owner_type="vendor",
                             description=f"Flyeralarm GmbH Re. {bill['id']}",
                             memo=f"SEPA-Überweisung Flyeralarm RE FA-{spec['opened'].year}-71824")
    return counts


# ── Phase 8: edge — localized Ausgleichskonto (Tier C) ─────────────
def run_edge(out_path: Path) -> None:
    # A recent, not-yet-cleared item parked in the localized Ausgleichskonto
    # (Tier-C fixture: trips the dashboard's German-Imbalance warning). Dated
    # near THROUGH so it reads as a fresh "to clarify", not a year-old defect.
    # Clamped to THROUGH: write_bulk drops anything dated past it, and a
    # horizon before the 17th would otherwise silently lose the hook. A
    # Lastschrift lands on a Bankarbeitstag — the last one on or before.
    edge_date = bankday_back(min(day_in(date(THROUGH.year, THROUGH.month, 1), 17), THROUGH))
    write_bulk(out_path, [{
        "date": edge_date,
        "description": "Unklare Lastschrift (noch zu klären)",
        "splits": [(BANKKONTO, D("-48.50")), (AUSGLEICH, D("48.50"))],
    }])


# ── Phase 7d: USt-Voranmeldung (S2, R1) ────────────────────────────
UST_VA_DESC = "Finanzamt München USt-Voranmeldung"
# Output side (Kz. 81/86 and the §13b 46/47), input side (Kz. 66 and
# the §13b 67). The VA clears each by its month's movement.
VAT_ACCOUNTS = (UST19, UST7, UST_RC, VST19, VST7, VST_RC)


def run_ust_va(out_path: Path, since: date | None = None) -> int:
    """Monthly USt-Voranmeldung from the book's ACTUAL figures: on the
    10th of month M+1 (next Bankarbeitstag), Zahllast(M) = the
    1776/1771/1787 output VAT booked in M minus the 1576/1571/1577 input
    VAT booked in M, cleared through the Bankkonto (a Finanzamt refund
    when negative). Each clearing debits the VAT accounts by exactly
    their month's movement, so no VAT account accumulates past the month
    in flight. The §13b pair nets to zero by construction (Kz. 46/47
    against 67) but is declared and cleared like the rest.

    Reads the book rather than the generator's streams so invoice and
    bill taxes posted by the server are included. Idempotent by period
    label; ``since`` (continuation) restricts writes to dates after the
    frozen prefix."""
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    n = 0
    try:
        eur = book.default_currency
        acct = {a.fullname: a for a in book.accounts}
        movement: dict[tuple[int, int], dict[str, D]] = {}
        done: set[str] = set()
        for path in VAT_ACCOUNTS:
            for sp in acct[path].splits:
                tx = sp.transaction
                if tx.description.startswith(UST_VA_DESC):
                    done.add(tx.description[len(UST_VA_DESC):].strip())
                    continue
                key = (tx.post_date.year, tx.post_date.month)
                bucket = movement.setdefault(key, {p: D("0") for p in VAT_ACCOUNTS})
                bucket[path] += D(str(sp.value))
        for (y, m) in sorted(movement):
            due = bankday(date(y + 1, 1, 10) if m == 12 else date(y, m + 1, 10))
            label = f"{m:02d}/{y}"
            if due > THROUGH or (since is not None and due <= since) or label in done:
                continue
            clearing = [(p, -v) for p, v in movement[(y, m)].items() if v != 0]
            if not clearing:
                continue
            # Debits on the output accounts minus the credits on the
            # input accounts: >0 pays the Finanzamt, <0 is its refund.
            zahllast = sum(v for _, v in clearing)
            splits = [piecash.Split(account=acct[p], value=v) for p, v in clearing]
            splits.append(piecash.Split(account=acct[BANKKONTO], value=-zahllast))
            piecash.Transaction(currency=eur, description=f"{UST_VA_DESC} {label}",
                                post_date=due, splits=splits,
                                notes=f"Voranmeldungszeitraum {label}; Kz. 81/86 Umsätze, "
                                      "Kz. 46/47 §13b, Kz. 66/67 Vorsteuer; Lastschrift Finanzamt")
            n += 1
        book.save()
    finally:
        book.close()
    return n


# ── Phase 7e: Einkommensteuer (P1) ─────────────────────────────────
EST_DESC = "Finanzamt München ESt-Vorauszahlung"
EST_NACHTRAG_DESC = "Finanzamt München nachträgliche ESt-Vorauszahlung"
EST_ABRECHNUNG_DESC = "Finanzamt München ESt-Abrechnung"
# The one pre-book year: Gewinn and Sonderausgaben (the Krankenkasse at
# the 2024 ceiling) lt. Bescheid 2024, and the quarterly Vorauszahlung
# that stood at the start of 2025 (set by the 2023 Bescheid).
# Everything from 2025 on is read from the book.
PRIOR_YEAR = {"profit": D("111000"), "sonderausgaben": D("13020"), "vz_quarterly": D("8000")}
BESCHEID_DAY = (8, 12)      # the simulated Bescheid for year Y−1 is dated 12 August of Y
SETTLEMENT_DAY = (9, 15)    # …and its Abschlusszahlung/Erstattung falls due a month after Bekanntgabe
QUARTERS = ((3, "I"), (6, "II"), (9, "III"), (12, "IV"))


def est_tarif(zve: D) -> D:
    """§32a EStG, Tarif 2025 (Grundtabelle), in full euros."""
    zve = zve.to_integral_value(rounding=ROUND_FLOOR)
    if zve <= 12096:
        est = D("0")
    elif zve <= 17443:
        y = (zve - 12096) / D("10000")
        est = (D("932.30") * y + D("1400")) * y
    elif zve <= 68480:
        z = (zve - 17443) / D("10000")
        est = (D("176.64") * z + D("2397")) * z + D("1015.13")
    elif zve <= 277825:
        est = D("0.42") * zve - D("10911.92")
    else:
        est = D("0.45") * zve - D("19246.67")
    return est.to_integral_value(rounding=ROUND_FLOOR)


def est_total(profit: D, sonderausgaben: D) -> D:
    """ESt + SolZ (Freigrenze 19,950 €, Milderungszone 11.9%) + KiSt
    (8% in Bayern) on the EÜR profit less Sonderausgaben."""
    est = est_tarif(profit - sonderausgaben)
    solz = D("0") if est <= 19950 else min(est * D("0.055"), (est - 19950) * D("0.119"))
    kist = est * D("0.08")
    return (est + solz + kist).quantize(D("0.01"), ROUND_HALF_UP)


def _quarterly(total: D) -> D:
    return (total / 4).to_integral_value(rounding=ROUND_FLOOR)


def euer(out_path: Path, year: int) -> tuple[D, D]:
    """(Gewinn, Sonderausgaben) for ``year`` from the book: every split
    on an account under the SKR03 income and expense roots (the P&L
    the Finanzamt sees — nothing private is in there), and the year's
    1830 Krankenkasse as the deductible Vorsorgeaufwand. Decimal
    aggregation in Python, never in SQL."""
    con = sqlite3.connect(str(out_path))
    try:
        rows = con.execute(
            """
            WITH RECURSIVE tree(guid, root) AS (
                SELECT guid, name FROM accounts
                WHERE parent_guid = (SELECT root_account_guid FROM books)
                UNION ALL
                SELECT a.guid, tree.root FROM accounts a JOIN tree ON a.parent_guid = tree.guid
            )
            SELECT tree.root, a.name, s.value_num, s.value_denom
            FROM splits s
            JOIN accounts a ON a.guid = s.account_guid
            JOIN tree ON tree.guid = a.guid
            JOIN transactions t ON t.guid = s.tx_guid
            WHERE substr(t.post_date, 1, 4) = ?
            """, (str(year),)).fetchall()
    finally:
        con.close()
    income = expense = kk = D("0")
    for root, name, num, denom in rows:
        v = D(num) / D(denom)
        if root == "Erlöse u. Erträge 2/8":
            income -= v
        elif root == "Aufwendungen 2/4":
            expense += v
        elif name.startswith("1830 "):
            kk += v
    return (income - expense).quantize(D("0.01")), kk.quantize(D("0.01"))


def est_schedule(out_path: Path, year: int) -> tuple[list[tuple[date, str, D]], D, D]:
    """The Finanzamt's ESt cash flow for ``year`` as (date, description,
    amount) rows plus the year's assessment figures. Vorauszahlungen
    are set by the last Bescheid: Q1/Q2 at the rate the Bescheid for
    ``year−2`` fixed, then the (simulated) Bescheid for ``year−1``
    arrives on 12 August, Q3 and Q4 run at its rate with a nachträgliche
    Vorauszahlung for Q1/Q2, and the settlement of ``year−1`` — its
    assessed ESt less the Vorauszahlungen actually paid for it, read
    from the book — falls due on 15 September."""
    def assessed(y: int) -> D:
        if y < YEAR:
            return est_total(PRIOR_YEAR["profit"], PRIOR_YEAR["sonderausgaben"])
        profit, kk = euer(out_path, y)
        return est_total(profit, kk)

    old = PRIOR_YEAR["vz_quarterly"] if year == YEAR else _quarterly(assessed(year - 2))
    new = _quarterly(assessed(year - 1))
    rows: list[tuple[date, str, D]] = []
    for month, roman in QUARTERS:
        rate = old if month < 8 else new
        rows.append((bankday(date(year, month, 10)), f"{EST_DESC} {roman}/{year}", rate))
    if new != old:
        rows.append((bankday(date(year, 9, 10)),
                     f"{EST_NACHTRAG_DESC} I.–II./{year} (Anpassung lt. Bescheid {year - 1})",
                     2 * (new - old)))
    paid_prior = (4 * PRIOR_YEAR["vz_quarterly"] if year == YEAR
                  else _est_paid_for(out_path, year - 1, vz_only=True))
    settlement = assessed(year - 1) - paid_prior
    bm, bd = BESCHEID_DAY
    rows.append((bankday(date(year, *SETTLEMENT_DAY)),
                 f"{EST_ABRECHNUNG_DESC} {year - 1} lt. Bescheid vom {bd:02d}.{bm:02d}.{year}",
                 settlement))
    return rows, old, new


def _est_paid_for(out_path: Path, year: int, *, vz_only: bool = False) -> D:
    """Sum of 1810 rows attributable to tax year ``year``: the
    Vorauszahlungen labelled ``…/year`` (and, unless ``vz_only``, the
    Abrechnung for ``year``)."""
    con = sqlite3.connect(str(out_path))
    try:
        rows = con.execute(
            """
            SELECT t.description, s.value_num, s.value_denom
            FROM splits s JOIN accounts a ON a.guid = s.account_guid
            JOIN transactions t ON t.guid = s.tx_guid
            WHERE a.name = '1810 Privatsteuern'
            """).fetchall()
    finally:
        con.close()
    total = D("0")
    for desc, num, denom in rows:
        v = D(num) / D(denom)
        if desc.startswith((EST_DESC, EST_NACHTRAG_DESC)) and desc.split(" (")[0].endswith(f"/{year}"):
            total += v
        elif not vz_only and desc.startswith(f"{EST_ABRECHNUNG_DESC} {year} "):
            total += v
    return total.quantize(D("0.01"))


def run_est(out_path: Path, since: date | None = None) -> int:
    """Write the ESt rows due within the horizon: 1200 → 1810
    Privatsteuern (an Erstattung reverses). Year by year, so each
    settlement reads the Vorauszahlungen the book already holds.
    Idempotent by description; ``since`` restricts to the continuation
    window."""
    n = 0
    for year in range(YEAR, THROUGH.year + 1):
        rows, _, _ = est_schedule(out_path, year)
        book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
        try:
            eur = book.default_currency
            acct = {a.fullname: a for a in book.accounts}
            done = {tx.description for tx in book.transactions
                    if tx.description.startswith("Finanzamt München ESt")}
            for when, desc, amount in rows:
                if when > THROUGH or (since is not None and when <= since) or desc in done:
                    continue
                if amount == 0:
                    continue
                piecash.Transaction(
                    currency=eur, description=desc, post_date=when,
                    notes="Einkommensteuer, Solidaritätszuschlag und Kirchensteuer — "
                          "privat (§12 Nr. 3 EStG), Lastschrift Finanzamt",
                    splits=[piecash.Split(account=acct[BANKKONTO], value=-amount),
                            piecash.Split(account=acct[PRIV_STEUERN], value=amount)])
                n += 1
            book.save()
        finally:
            book.close()
    return n


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
                                notes=t.get("notes") or "",
                                post_date=t["date"], splits=splits)
            n += 1
        book.save()
    finally:
        book.close()
    return n


# ── Entry dates (A1 / S4) ──────────────────────────────────────────
def _entry_time(post: str, description: str) -> str:
    """A deterministic evening entry time on the document's date
    (16:00–19:59 UTC — Sabine books in the evening)."""
    h = hashlib.md5(f"{post}|{description}".encode()).hexdigest()
    hour = 16 + int(h[:2], 16) % 4
    minute = int(h[2:4], 16) % 60
    second = int(h[4:6], 16) % 60
    return f"{post[:10]} {hour:02d}:{minute:02d}:{second:02d}"


def stamp_enter_dates(out_path: Path) -> int:
    """GoBD: entered when it happened. piecash and the server both
    stamp ``enter_date`` with the wall clock at write time, which made
    every row in the book look twenty months late (A1). Rewrite every
    transaction's entry timestamp onto its posting date, and every
    document entry's ``date_entered`` onto its entry date. Idempotent
    (the time of day is a hash of the row), raw SQL because piecash's
    ORM would re-stamp on save."""
    con = sqlite3.connect(str(out_path))
    try:
        rows = con.execute("SELECT guid, post_date, description, enter_date FROM transactions").fetchall()
        updates = [(_entry_time(post, desc), guid) for guid, post, desc, entered in rows
                   if entered != _entry_time(post, desc)]
        con.executemany("UPDATE transactions SET enter_date = ? WHERE guid = ?", updates)
        con.execute("UPDATE entries SET date_entered = date WHERE date_entered <> date")
        con.commit()
        back = con.execute("SELECT COUNT(*) FROM transactions WHERE substr(enter_date,1,10) <> substr(post_date,1,10)").fetchone()[0]
    finally:
        con.close()
    if back:
        raise SystemExit(f"stamp_enter_dates: {back} rows still carry a foreign entry date")
    return len(updates)


# ── Verify ─────────────────────────────────────────────────────────
def _month_ends(through: date) -> list[date]:
    out = []
    for first in iter_months():
        end = day_in(first, 31)
        if end <= through:
            out.append(end)
    return out


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
    assert "Hypothek Sparkasse" in dp and "Kfz-Finanzierung" in dp, \
        "debt-payoff did not include the German loans"
    print("✓ Debt-payoff includes Hypothek + Kfz (slot-driven term)")

    # 6. The audit tiers (BOOKKEEPER_REVIEW_DEMO_GENERATORS.md §7).
    con = sqlite3.connect(str(out_path))
    try:
        # S5: invoice numbers chronological + gap-free (customer docs).
        rows = con.execute(
            "SELECT id, date(date_opened) FROM invoices WHERE owner_type = 2 "
            "ORDER BY id").fetchall()
        assert [int(r[0]) for r in rows] == list(range(1, len(rows) + 1)), \
            f"invoice numbers not gap-free: {rows}"
        assert [r[1] for r in rows] == sorted(r[1] for r in rows), \
            f"invoice numbers not chronological: {rows}"
        # S4: every entry dated on its document's date, entered that day.
        bad = con.execute(
            "SELECT i.id FROM entries e JOIN invoices i ON e.invoice = i.guid "
            "WHERE date(e.date) <> date(i.date_opened) OR date(e.date_entered) <> date(e.date)").fetchall()
        assert not bad, f"entry date != invoice date on {bad}"
        # S7: every customer has street + city and a USt-IdNr. or a
        # Drittland note.
        for name, a1, a2, notes in con.execute(
                "SELECT name, addr_addr1, addr_addr2, notes FROM customers"):
            assert a1 and a2, f"{name}: missing street/city"
            assert "USt-IdNr." in notes or "Drittland" in notes, f"{name}: no VAT status"
        # A1: every transaction entered on its posting date.
        late = con.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE substr(enter_date,1,10) <> substr(post_date,1,10)").fetchone()[0]
        assert late == 0, f"{late} transactions entered on a day other than their posting date"
        n_tx = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        # W5: revenue = invoices. Every 8400/8300 row is an invoice posting.
        direct = con.execute(
            "SELECT COUNT(*) FROM splits s JOIN accounts a ON a.guid = s.account_guid "
            "JOIN transactions t ON t.guid = s.tx_guid "
            "WHERE (a.name LIKE '8400 %' OR a.name LIKE '8300 %') AND t.num = ''").fetchone()[0]
        assert direct == 0, f"{direct} revenue rows without an invoice number"
        rev_rows = con.execute(
            "SELECT COUNT(*) FROM splits s JOIN accounts a ON a.guid = s.account_guid "
            "WHERE a.name LIKE '8400 %' OR a.name LIKE '8300 %'").fetchone()[0]
        # W1–W4/W7: the payee table's consequences.
        def _count(sql):
            return con.execute(sql).fetchone()[0]
        for path in (VST7, VST_RC, UST_RC):
            leaf = path.rsplit(":", 1)[1]
            n_rows = _count(f"SELECT COUNT(*) FROM splits s JOIN accounts a ON a.guid = s.account_guid "
                            f"WHERE a.name = '{leaf}' AND s.value_num <> 0")
            assert n_rows > 0, f"{leaf} carries no rows"
        exempt = [p for (c, p), (_, t, _) in PAYEES.items() if t in ("exempt", "privat")]
        bad = con.execute(
            "SELECT DISTINCT t.description FROM splits s JOIN accounts a ON a.guid = s.account_guid "
            "JOIN transactions t ON t.guid = s.tx_guid "
            "WHERE (a.name LIKE '1576 %' OR a.name LIKE '1571 %' OR a.name LIKE '1577 %') "
            f"AND t.description IN ({','.join('?' * len(exempt))})", exempt).fetchall()
        assert not bad, f"input VAT on exempt/private payees: {bad}"
        rc = [p for (c, p), (_, t, _) in PAYEES.items() if t in RC_TREATMENTS]
        bad = con.execute(
            "SELECT DISTINCT t.description FROM splits s JOIN accounts a ON a.guid = s.account_guid "
            "JOIN transactions t ON t.guid = s.tx_guid "
            f"WHERE a.name LIKE '1576 %' AND t.description IN ({','.join('?' * len(rc))})", rc).fetchall()
        assert not bad, f"domestic VSt on §13b payees: {bad}"
        # A transaction's notes live in its KVP frame (slots.name = 'notes').
        has_note = ("EXISTS (SELECT 1 FROM slots n WHERE n.obj_guid = t.guid "
                    "AND n.name = 'notes' AND n.string_val <> '')")
        for leaf in ("4653 Aufmerksamkeiten", "4650 Bewirtungskosten", "4654 Nicht abzugsfähige Bewirtungskosten"):
            noteless = _count(
                "SELECT COUNT(*) FROM splits s JOIN accounts a ON a.guid = s.account_guid "
                "JOIN transactions t ON t.guid = s.tx_guid "
                f"WHERE a.name = '{leaf}' AND NOT {has_note}")
            assert noteless == 0, f"{noteless} rows on {leaf} without a note"
        # A3: every business expense row (SKR03 4xxx) carries a note,
        # except document postings (the bill IS the Beleg; its
        # Rechnungsnr. sits on the document, ``num`` links to it) and
        # the Dauerauftrag-style fixed costs that need none.
        noteless_biz = _count(
            "SELECT COUNT(DISTINCT t.guid) FROM splits s JOIN accounts a ON a.guid = s.account_guid "
            "JOIN transactions t ON t.guid = s.tx_guid "
            "WHERE a.account_type = 'EXPENSE' AND a.name GLOB '4[0-9][0-9][0-9] *' "
            f"AND NOT {has_note} AND t.num = '' "
            "AND t.description NOT IN ('Kontoführungsgebühr', 'VW Bank Kfz-Finanzierung')")
        assert noteless_biz == 0, f"{noteless_biz} business expense rows without a note"
        bewirt = _count("SELECT COUNT(DISTINCT s.tx_guid) FROM splits s JOIN accounts a ON a.guid = s.account_guid "
                        "WHERE a.name = '4650 Bewirtungskosten'")
        gwg_over = _count(
            "SELECT COUNT(*) FROM splits s JOIN accounts a ON a.guid = s.account_guid "
            "WHERE a.name LIKE '4855 %' AND s.value_num * 1.0 / s.value_denom > 800")
        assert gwg_over == 0, "GWG over €800 net"
        klein_over = _count(
            "SELECT COUNT(*) FROM splits s JOIN accounts a ON a.guid = s.account_guid "
            "WHERE a.name LIKE '4985 %' AND s.value_num * 1.0 / s.value_denom > 250")
        assert klein_over == 0, "Kleingerät over €250 net"
        # No transaction with a bank leg on a weekend or Bavarian holiday.
        weekend = con.execute(
            "SELECT DISTINCT date(t.post_date) FROM splits s JOIN accounts a ON a.guid = s.account_guid "
            "JOIN transactions t ON t.guid = s.tx_guid "
            "WHERE a.name IN ('1200 Bankkonto', '1100 Postbank')").fetchall()
        # The Anfangsbestand is a book entry dated 1 January (Neujahr).
        off = [d for (d,) in weekend
               if not is_bankday(date.fromisoformat(d)) and d != date(YEAR, 1, 1).isoformat()]
        # The interest credit is the one tolerated exception: its
        # Wertstellung is the month-end, as on a real statement.
        interest_only = {r[0] for r in con.execute(
            "SELECT date(post_date) FROM transactions t WHERE description = 'Postbank Zinsgutschrift' "
            "AND NOT EXISTS (SELECT 1 FROM transactions o JOIN splits s ON s.tx_guid = o.guid "
            "JOIN accounts a ON a.guid = s.account_guid WHERE date(o.post_date) = date(t.post_date) "
            "AND o.guid <> t.guid AND a.name IN ('1200 Bankkonto', '1100 Postbank'))")}
        off = [d for d in off if d not in interest_only]
        assert not off, f"bank postings on non-bank days: {off[:5]}"
    finally:
        con.close()
    print(f"✓ S4/S5/S7/W5: {len(rows)} invoices numbered in date order, entries dated and "
          f"entered on their documents, {rev_rows} revenue rows all invoice-posted, "
          f"customers with master data; {n_tx} transactions entered on their posting date")
    print(f"✓ W1–W4/W7/W8: 1571/1577/1787 carry rows; exempt and §13b payees never touch 1576; "
          f"{bewirt} Bewirtungen with Anlass/Teilnehmer; GWG ≤ 800, Kleingeräte ≤ 250; "
          "bank postings on Bankarbeitstage only")

    # S1: nothing private in 2110 and no private EXPENSE root (W6);
    # S8: AfA has hit the Pkw.
    zins = "Aufwendungen 2/4:Zinsaufwendungen:2110 Zinsaufwendungen für kurzfristige Verbindlichkeiten"
    assert book.get_balance(zins, as_of_date=THROUGH) == 0, "2110 carries interest — the Hypothek is private"
    with book.open() as b:
        roots = {a.name: a.type for a in b.accounts if a.parent is b.root_account}
    assert "Privatausgaben" not in roots and roots.get(PRIV_KAPITAL) == "EQUITY", roots
    hyp_zins = book.get_balance(ZINS_HYP_PRIV, as_of_date=THROUGH)
    assert hyp_zins > 0, "Hypothek interest not recorded under Privatkapital"
    print(f"✓ S1/W6: 2110 = 0; no private EXPENSE root; Hypothekenzinsen €{hyp_zins} as a "
          "drawing under Privatkapital")
    if THROUGH >= date(YEAR, 12, 31):
        assert book.get_balance(PKW, as_of_date=THROUGH) == PKW_OPENING - sum(
            pkw_afa(y) for y in range(YEAR, THROUGH.year + 1)
            if date(y, 12, 31) <= THROUGH), "Pkw AfA missing"
        print(f"✓ S8/P6: Pkw AfA booked; book value €{book.get_balance(PKW, as_of_date=THROUGH)} "
              f"(BLP {PKW_LISTENPREIS} ↔ AK {PKW_AK} ↔ AfA {pkw_afa(YEAR)}/yr)")

    # ── The four month-end invariants ───────────────────────────
    ends = _month_ends(THROUGH)
    # I1: 1200 Bankkonto inside the policy band at every month-end,
    # never negative anywhere it is checked.
    lo, hi = POLICY.buffer * D("0.5"), POLICY.buffer * D("3")
    balances = {}
    for end in ends + [THROUGH]:
        bal = D(str(book.get_balance(BANKKONTO, as_of_date=end)))
        balances[end] = bal
        assert bal >= 0, f"1200 negative at {end}: {bal}"
        if end != THROUGH:
            assert lo <= bal <= hi, f"1200 outside [{lo}, {hi}] at {end}: {bal}"
    print(f"✓ I1: 1200 Bankkonto in [{lo}, {hi}] at all {len(ends)} month-ends; "
          f"min €{min(balances.values())} ({min(balances, key=balances.get)}), "
          f"at {THROUGH}: €{balances[THROUGH]}")

    # I2: every VAT account holds only the current month's movement at
    # each month-end whose prior month's VA is due by then.
    with book.open() as b:
        acct = {a.fullname: a for a in b.accounts}
        rows_by = {}
        for path in VAT_ACCOUNTS:
            rows_by[path] = [(s.transaction.post_date, D(str(s.value)),
                              s.transaction.description.startswith(UST_VA_DESC))
                             for s in acct[path].splits]
    checked = 0
    for end in ends + [THROUGH]:
        first = end.replace(day=1)
        prior_due = bankday(date(first.year, first.month, 10))
        if prior_due > THROUGH or first == date(YEAR, 1, 1):
            continue
        for path, rows_ in rows_by.items():
            balance = sum(v for d, v, _ in rows_ if d <= end)
            this_month = sum(v for d, v, is_va in rows_ if first <= d <= end and not is_va)
            assert balance == this_month, \
                f"{path.rsplit(':', 1)[1]} at {end}: balance {balance} ≠ this month's {this_month}"
        checked += 1
    print(f"✓ I2: 1576/1571/1577 and 1776/1771/1787 fully cleared by the following VA "
          f"at {checked} month-ends (only the month in flight outstanding)")

    # I3: ESt paid within 15% of what the EÜR implies, per complete year.
    for year in range(YEAR, THROUGH.year):
        profit, kk = euer(out_path, year)
        implied = est_total(profit, kk)
        paid = _est_paid_for(out_path, year)
        settled = bankday(date(year + 1, *SETTLEMENT_DAY)) <= THROUGH
        gap = abs(paid - implied) / implied if implied else D("0")
        print(f"  ESt {year}: EÜR-Gewinn €{profit}, KK €{kk} → implied €{implied}; "
              f"paid €{paid} ({'settled' if settled else 'Vorauszahlungen only'}, "
              f"gap {gap * 100:.1f}%)")
        assert gap <= D("0.15"), f"ESt {year}: paid {paid} vs implied {implied}"
    print("✓ I3: ESt paid within 15% of the EÜR-implied liability for every complete year")

    # I4: no invoice unpaid beyond Net 14 + 45 days.
    outstanding = _rows(book.get_outstanding_invoices(compact=False, limit=1000))
    stale = [(d.get("id"), d.get("date_posted")) for d in outstanding
             if d.get("date_posted")
             and (THROUGH - date.fromisoformat(str(d["date_posted"])[:10])).days > 14 + 45]
    assert not stale, f"documents unpaid beyond Net 14 + 45 days: {stale}"
    print(f"✓ I4: {len(outstanding)} documents open, none past Net 14 + 45 days")


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
    prefix history, and the repair below resolves it. Revenue is not a
    stream: it is invoices, written by ``continue_business``."""
    global THROUGH
    THROUGH = through
    return (gen_recurring() + gen_yearend() + gen_variable() + gen_trips()
            + gen_personal())


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
        acct = {a.fullname: a for a in book.accounts}
        _etf_buy(book, acct, when, amount, source_path,
                 lot_title=f"Sparplan Sonderkauf {when.isoformat()}",
                 lot_notes="MSCI World — Überschussanlage",
                 description="MSCI World ETF Sonderkauf (comdirect)")
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
    rows = _rows(book.list_scheduled_transactions(compact=False, limit=250))
    existing = {row.get("name") for row in rows}
    rent, kk = rent_for(THROUGH.year), kk_for(THROUGH.year)
    plans = [
        ("Miete Studio", "Dauerauftrag Miete Studio Schwabing", [
            {"account": BANKKONTO, "amount": str(-rent)},
            {"account": MIETE, "amount": str(rent)},
        ], "monthly", "2025-01-01"),
        ("Krankenkasse", "Techniker Krankenkasse Beitrag", [
            {"account": BANKKONTO, "amount": str(-kk)},
            {"account": KRANKENKASSE, "amount": str(kk)},
        ], "monthly", "2025-01-01"),
        # The ETF split needs a quantity (IWDA.AS ≠ EUR). A template's
        # unit count is nominal — the realized buys reprice monthly —
        # so ~€1,200 at a recent close is the honest placeholder. Same
        # zone-crossing shape as the realized buys (_etf_buy).
        ("ETF Sparplan", "MSCI World ETF Sparplan (comdirect)", [
            {"account": BANKKONTO, "amount": "-1200.00"},
            {"account": PRIV_DRAW, "amount": "1200.00"},
            {"account": ETF, "amount": "1200.00", "quantity": "13.1378"},
            {"account": PRIV_KAPITAL, "amount": "-1200.00"},
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
    rows = _rows(book.list_budgets(compact=False))
    if any(row.get("name") == "Budget 2025" for row in rows):
        return False
    book.create_budget(name="Budget 2025", year=YEAR, num_periods=12,
                       period_type="monthly",
                       description="Studio-Budget Sabine Brenner")
    monthly = [
        (MIETE, str(rent_for(YEAR))),
        (KRANKENKASSE, str(kk_for(YEAR).to_integral_value())),
        (BUEROBEDARF, "120"),
        (INTERNET, "60"),
        (REISE, "250"),
    ]
    for acct, amt in monthly:
        book.set_budget_amount(budget_name="Budget 2025", account=acct,
                               amount=amt, period="all")
    return True


def continue_business(book: GnuCashBook, through: date,
                      since: date) -> dict:
    """Continuation: the invoices and bills opened after the frozen
    edge (paid within the horizon on their own lag; the prefix's open
    documents are the settlement pass's), then the USt-VA and ESt rows
    newly due — each from the book's real figures."""
    global THROUGH
    THROUGH = through
    ensure_schedules(book)
    ensure_budget(book)
    counts = run_business(book, since=since)
    counts["ust_va"] = run_ust_va(Path(book.book_path), since=since)
    counts["est"] = run_est(Path(book.book_path), since=since)
    return counts


def continue_investments(out_path: Path, through: date,
                         since: date) -> int:
    global THROUGH
    THROUGH = through
    return run_investments(out_path, since=since)


def advance_schedules(out_path: Path, through: date) -> dict:
    """The last persona hook a continuation calls — so the entry-date
    stamp (idempotent) covers everything the run wrote before it."""
    from continuation import advance_sx
    info = advance_sx(out_path, through)
    info["entry_dates_stamped"] = stamp_enter_dates(out_path)
    return info


POLICY = PersonaPolicy(
    key="sabine", currency="EUR",
    checking=BANKKONTO,                # the thin flow account
    savings=POSTBANK,                  # the accumulating parking account
    # Corridor midpoint: a quarterly ESt-Vorauszahlung, the USt-VA, the
    # Hypothek, the Krankenkasse and the rent all land in the first ten
    # days of a quarter month — the month-end floor must carry them.
    buffer=D("15000"),
    cards=(),
    savings_share=D("1"),              # surplus parks in Postbank whole
    invest_months=(3, 6, 9, 12),
    # Postbank is also the Steuerrücklage: the September Nachzahlung and
    # the quarterly Vorauszahlungen are topped up from it, so it skims
    # to the ETF only above a reserve that covers them.
    savings_target=D("20000"),
    rebalance_tranche=D("2000"),       # modest — steady state suffices
    max_monthly_sweep=D("6000"),
    min_sweep=D("100"),
    invest=continuation_invest,
    ensure_rate=ensure_rate,
    book_repairs=ausgleich_repair,
    # Loans + VAT clearing: no statement to reconcile against (§1).
    no_reconcile=(HYPOTHEK, KFZ_FIN, UST19, UST7, UST_RC),
    # The Postbank pile earns Tagesgeld interest — a Betriebseinnahme
    # on the business account (2650), booked monthly by the engine.
    savings_apy=D("0.0125"),
    interest_income=ZINSERTRAG,
    desc_sweep="Übertrag auf Postbank (Monatsüberschuss)",
    desc_repair_sweep="Übertrag auf Postbank — angesammelter Überschuss",
    desc_topup="Umbuchung von Postbank (Kontodeckung)",
    desc_savings_interest="Postbank Zinsgutschrift",
)


def run_base_policy(out_path: Path) -> list[str]:
    """Run the closed-loop policy over the WHOLE base timeline — the
    corridor top-up, the surplus sweep to Postbank, the quarterly ETF
    skim, Postbank interest — from 2025-01-01, so the Bankkonto never
    drifts and the base book is exactly what a continuation would have
    produced month by month."""
    from continuation import run_policy
    return run_policy(POLICY, out_path, date(YEAR, 1, 1) - timedelta(days=1), THROUGH)


def redate_engine_rows(out_path: Path) -> int:
    """The engine dates its month-end moves on the calendar month-end;
    a Sparkasse→Postbank transfer or an ETF order lands on a
    Bankarbeitstag (P5). Move each one back to the last bank day of its
    month — balance-neutral by construction, because nothing else posts
    on a non-bank day, so every month-end balance the engine read is
    unchanged. The interest credit keeps its Wertstellung on the
    month-end, as a bank's does. Base build only: in a continuation the
    prior bank day could be the frozen cutoff itself."""
    transfers = {POLICY.desc_sweep, POLICY.desc_repair_sweep, POLICY.desc_topup,
                 "MSCI World ETF Sonderkauf (comdirect)"}
    book = piecash.open_book(str(out_path), readonly=False, do_backup=False)
    n, etf_dates = 0, []
    try:
        for tx in book.transactions:
            if tx.description not in transfers or is_bankday(tx.post_date):
                continue
            old, new = tx.post_date, bankday_back(tx.post_date)
            tx.post_date = new
            for sp in tx.splits:
                if sp.lot is not None and old.isoformat() in sp.lot.title:
                    sp.lot.title = sp.lot.title.replace(old.isoformat(), new.isoformat())
                    etf_dates.append(new)
            n += 1
        book.save()
    finally:
        book.close()
    if etf_dates:
        # Same month, same synthetic close — the order is priced as booked.
        _add_price_rows(out_path, [(ETF_MNEMONIC, d) for d in etf_dates])
    return n


# ── Driver ──────────────────────────────────────────────────────

def build_base(out_path: Path) -> None:
    """Commodities, SKR03 chart, account slots — nothing dated."""
    os.environ["GNUCASH_LOCALE"] = "de_DE.UTF-8"
    print(f"Building Sabine base at: {out_path}")
    print("\nPhase 1: book + commodities")
    create_book_file(out_path)
    print("\nPhase 2: SKR03 chart of accounts")
    print(f"  {create_accounts(out_path)} accounts")
    set_account_slots(GnuCashBook(str(out_path)))
    print("  loan slots set")


def build(out_path: Path) -> None:
    # Sabine runs a German-locale system, so the server names auto-created
    # accounts in German (Tier-D): the FX gain/loss leaf becomes
    # "Realisierter Gewinn/Verlust", not the English fallback that a
    # locale-less run would pick on a numbered SKR03 chart.
    os.environ["GNUCASH_LOCALE"] = "de_DE.UTF-8"
    build_base(out_path)
    print(f"  (THROUGH={THROUGH})")
    print("\nPhase 1b: prices")
    print(f"  {add_prices(out_path)} prices")
    book = GnuCashBook(str(out_path))
    print("\nPhase 3: opening balances + ETF lot")
    opening_balances(out_path)
    print("\nPhase 4: recurring (rent, utilities, subs, loans, 1%-Regelung)")
    print(f"  {write_bulk(out_path, gen_recurring())} recurring txns")
    print("\nPhase 4b: year-end AfA (Pkw, linear)")
    print(f"  {write_bulk(out_path, gen_yearend())} AfA bookings")
    print("\nPhase 5: variable business spend (through the payee table)")
    print(f"  {write_bulk(out_path, gen_variable())} variable txns")
    print("\nPhase 5b: trips (transport, lodging, Pauschalen, conferences)")
    print(f"  {write_bulk(out_path, gen_trips())} trip rows")
    print("\nPhase 6: personal living + Krankenkasse (Privatentnahme)")
    print(f"  {write_bulk(out_path, gen_personal())} personal txns")
    print("\nPhase 7b: monthly ETF Sparplan")
    print(f"  {run_investments(out_path)} Sparplan buys")
    print("\nPhase 7: business (every euro of revenue through A/R; the Flyeralarm bill)")
    print(f"  {run_business(book)}")
    print("\nPhase 7d: USt-Voranmeldung (Zahllast from the book's own figures)")
    print(f"  {run_ust_va(out_path)} monthly filings")
    print("\nPhase 7e: Einkommensteuer (Vorauszahlungen + Bescheid-Abrechnung from the EÜR)")
    print(f"  {run_est(out_path)} Finanzamt rows")
    print("\nPhase 7f: closed-loop cash policy from day one (top-up, sweep, ETF skim, interest)")
    actions = run_base_policy(out_path)
    print(f"  {len(actions)} policy actions; last 4:")
    for line in actions[-4:]:
        print(f"    {line}")
    print(f"  {redate_engine_rows(out_path)} month-end transfers moved to the prior Bankarbeitstag")
    print("\nPhase 7c: schedules + budget (parity — bookkeeper review §4)")
    from continuation import advance_sx, reconcile_through
    print(f"  {ensure_schedules(book)} schedules, "
          f"budget={ensure_budget(book)}, cursors={advance_sx(out_path, THROUGH)}")
    print("\nPhase 8: edge — localized Ausgleichskonto")
    run_edge(out_path)
    print("\nPhase 9: reconciliation posture (through the last full month)")
    for line in reconcile_through(POLICY, out_path, THROUGH):
        print(f"  {line}")
    print("\nPhase 10: entry dates = document dates (GoBD)")
    print(f"  {stamp_enter_dates(out_path)} entry timestamps stamped")
    verify(out_path)
    print("\nDone.")


def main() -> None:
    global THROUGH
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--through", default=None, metavar="YYYY-MM-DD",
                   help="Pin the timeline end for a deterministic run (default: today).")
    p.add_argument(
        "--chart-only", action="store_true",
        help="Write the chart-only base (commodities, accounts, slots; "
             "nothing dated), VACUUMed, and stop.")
    a = p.parse_args()
    if a.through:
        THROUGH = date.fromisoformat(a.through)
        if THROUGH < date(YEAR, 1, 1):
            raise SystemExit(f"--through {THROUGH} precedes {YEAR}-01-01")
    out = Path(a.out).resolve()
    if out == PROTECTED.resolve():
        raise SystemExit(f"REFUSING to write protected book: {PROTECTED}")
    if a.chart_only:
        from base_book import vacuum
        build_base(out)
        print(f"  base VACUUMed: {vacuum(out):,} bytes")
        return
    build(out)


if __name__ == "__main__":
    main()
