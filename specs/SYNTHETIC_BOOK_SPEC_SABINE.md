# Synthetic Book Specification: Sabine Brenner

**Purpose:** Test gnucash-mcp against a **natively localized account
hierarchy** — a real German chart (DATEV **SKR03**) with German,
numbered account names and EUR as the default currency. Alex (USD) and
Lin Wei (CNY) are both English-named, which is exactly why the i18n
bug class was invisible: every tool that keys off an English account
name ("Income", "Imbalance", "mortgage") is suspect until proven on a
book where no such English name exists. This is the localization
correctness stress test disguised as a person.

**Operator:** Script-generated like Alex and Lin Wei. Ships in-repo as
the German sample persona.

**Book file:** `samples/sabine-brenner.gnucash` (SQLite, default
currency EUR).

**Timeline:** January 1, 2025 onward, extended with script-generated
activity into 2026 (matching the Alex/Lin Wei convention; latest
market/FX prices on file dated 2026-06-01).

---

## The Person

**Sabine Brenner**, 41, München (Munich), Bayern. Freelance
**Kommunikations- und Grafikdesignerin** operating as an
**Einzelunternehmerin** (sole proprietor) under **Regelbesteuerung**
(VAT-registered, not Kleinunternehmerin) and filing her profit via
**Einnahmenüberschussrechnung (EÜR, §4 Abs. 3 EStG)** — the cash-basis
method most German freelancers use.

Partner **Markus Brenner**, a teacher (Gymnasiallehrer) — kept off the
books; this is Sabine's *business* book, sole-proprietor style, so
household/personal flows leave the business via **Privatentnahme**.

A cat named **Pixel**.

**Why Munich:** Germany's most expensive property market (the Seattle/
Shenzhen parallel), a strong *Mittelstand* and creative-agency scene,
and the natural home for a well-established freelance designer with a
financed *Eigentumswohnung* and a small studio.

**Why SKR03:** It is the chart a real German sole proprietor's
*Steuerberater* actually sets up — process-ordered (*Prozess­glie­derung*),
4-digit *Kontenklassen*, German throughout. It ships with GnuCash
(`acctchrt_skr03.gnucash-xea`), so the chart is authentic, not a
tidied-up English shape with German labels pasted on.

---

## Phase 1: Commodities

Book currency is **EUR**. USD is the *foreign* currency (a US client
pays in dollars), which forces cross-currency invoicing — the mirror
of Alex, and the same direction as Lin Wei but with a near-parity rate
(≈1.08), so nothing may assume rates are far from 1.0 *or* near it.

| Mnemonic | Fullname | Namespace | Fraction | Notes |
|---|---|---|---|---|
| USD | US Dollar | CURRENCY | 100 | Foreign — US client pays in this |
| IWDA.AS | iShares Core MSCI World | FUND | 10000 | Xetra-listed accumulating ETF (a designer's *Sparplan*) |

**Prices:** monthly on the 1st, in EUR (the book base). EUR/USD drifts
in the ~1.06–1.12 band across the timeline; the ETF drifts upward.
The acceptance test depends on the rate *moving* between an invoice's
post date and its pay date, so two adjacent monthly USD rates must
differ.

**Critical test:** the FX direction is "EUR per foreign unit" and the
USD rate sits near 1.0 — a book that accidentally treats `value` and
`quantity` as interchangeable can pass here by luck, so the
cross-currency invoice (Phase 5) must be hand-checked, not assumed.

---

## Phase 2: Chart of Accounts — authentic SKR03

Seed the **real** `acctchrt_skr03` hierarchy (100 accounts) verbatim:
German, numbered, *Kontenklasse*-organized. There is **no** account
named "Income"/"Expenses"/"Assets" — the top-level nodes are
`Erlöse u. Erträge 2/8` (INCOME), `Aufwendungen 2/4` (EXPENSE),
`Aktiva` (ASSET), `Passiva` (LIABILITY), and two equity roots
`Anfangsbestand 9` and `Privatkonten 1`. This is the whole point: the
type-based resolver must find the income/expense roots with zero
English names present.

Accounts Sabine actively uses (all authentic SKR03 leaves):

- **Banks:** `Aktiva:Finanzkonten 1:1200 Bankkonto` (primary, Sparkasse
  München), `…:1100 Postbank` (secondary).
- **A/R (EUR):** `Aktiva:Finanzkonten 1:1400 Ford. a. Lieferungen und
  Leistungen` [RECEIVABLE].
- **A/P:** `Passiva:Verbindlichkeiten:1600 Verblk. aus Lieferungen u.
  Leistungen` [PAYABLE].
- **Revenue:** `…8400 Erlöse USt. 19%` (design services),
  `…8300 Erlöse USt. 7%` (Einräumung von Nutzungsrechten / licensing,
  §12(2) UStG reduced rate). Exercises both VAT rates.
- **Output VAT:** `Passiva:Umsatzsteuer:1776 Umsatzsteuer 19%`,
  `…1771 Umsatzsteuer 7%`, prepayment `…1780 Umsatzsteuer-Vorauszahlung`.
- **Input VAT:** `Aktiva:Finanzkonten 1:1576 Abziehbare VSt. 19%`,
  `…1571 Abziehbare VSt. 7%`.
- **Expenses (Klasse 4):** `4210 Miete und Nebenkosten`,
  `4240 Gas, Wasser, Strom`, `4920 Telekom`, `4921 Mobilfunk D2`,
  `4922 Internet`, `4930 Bürobedarf`, `4940 Zeitschriften, Bücher`,
  `4945 Fortbildungskosten`, `4955 Buchführungskosten` (Steuerberater),
  `4610 Werbekosten`, `4670 Reisekosten Unternehmer`,
  `4360 Versicherungen`, `4970 Nebenkosten des Geldverkehrs`,
  `4855 Sofortabschreibung GWG`, Kfz-Kosten (`4520/4530/4540`),
  `2110/2121 Zinsaufwendungen`.
- **Privatkonten (owner equity flows):** `1800 Privatentnahme
  allgemein` (monthly draw), `1810 Privatsteuern` (private income tax —
  not a business expense), `1890 Privateinlagen`.
- **Opening equity:** `Anfangsbestand 9:Saldenvortragskonten:9000
  Saldenvortrag Sachkonten`.

### Additions beyond the shipped template (documented, minimal)

SKR03 ships no bank-loan liability and no fixed-asset rows Sabine
needs, so add — under the existing authentic parents, German-named:

- `Passiva:Verbindlichkeiten:0630 Verbindlichkeiten ggü. Kreditinstituten`
  [LIABILITY] — the **Hypothek** on the Eigentumswohnung (debt-payoff
  path). slot `apr=3.65`, `loan_term_months=300`.
- `Passiva:Verbindlichkeiten:0640 Kfz-Finanzierung` [LIABILITY] — car
  loan. slot `apr=4.49`, `loan_term_months=60`.
- `Aktiva:Anlage- u. Kapitalkonten 0:0090 Eigentumswohnung` [ASSET],
  `…:0320 Pkw` [ASSET].
- `Aktiva:Anlage- u. Kapitalkonten 0:0700 Wertpapierdepot` [PLACEHOLDER]
  → `…:0701 MSCI World ETF` [MUTUAL] commodity=IWDA.AS.
- A localized **Imbalance** account for Tier C: leave a small non-zero
  balance on a structurally-detected `Ausgleichskonto-EUR` [BANK,
  child of root] so the dashboard data-integrity warning fires on a
  *German* balancing-account name.

---

## Phase 3: Opening Balances (2025-01-01)

Via `Anfangsbestand 9:…:9000 Saldenvortrag Sachkonten` (the SKR03
carry-forward equity), not an English "Opening Balances":

- Bankkonto €18,400; Postbank €3,250.
- Eigentumswohnung €540,000 (Munich); Pkw €24,000.
- Wertpapierdepot: 95 units IWDA.AS at the 2025-01 price.
- Hypothek €395,000 (liability); Kfz-Finanzierung €16,500 (liability).

Net opening Eigenkapital is the residual; it must balance to the cent.

---

## Phase 4–5: Scheduled & recurring

Monthly: `4210 Miete und Nebenkosten` (Studio) €1,150;
`4920/4921/4922` Telekom/Mobilfunk/Internet; Hypothek and
Kfz-Finanzierung amortization (interest → `2110/2121`, principal →
the liability); `1800 Privatentnahme allgemein` €3,200 (Sabine pays
herself). Quarterly: `4955 Buchführungskosten` (Steuerberater),
**USt-Vorauszahlung** to the Finanzamt (`1780`). Yearly: `4360
Versicherungen` (Berufshaftpflicht), `4945 Fortbildungskosten`.

## Phase 6: Daily/weekly

EC-Karte/SEPA spend across German merchants — `4930 Bürobedarf`
(office/design supplies), `4910 Porto`, `4670 Reisekosten`
(client trips, Deutsche Bahn), `4610 Werbekosten` (portfolio, ads),
`4940 Zeitschriften/Bücher`, `4855 Sofortabschreibung GWG` (a tablet,
a lens), `4970 Nebenkosten des Geldverkehrs`. Personal spend exits via
`1800 Privatentnahme`.

## Phase 7: Business — VAT invoicing + the cross-currency client

Through the business module with **taxtables** (USt 19% and 7%):

- German clients: invoices posting to `1400`, revenue split to `8400`
  (19%) or `8300` (7%, licensing), output VAT to `1776`/`1771`.
- **The US client (cross-currency, the acceptance test):** an invoice
  **denominated in USD**, A/R in USD, **posted at one EUR/USD rate and
  paid at another** so realized FX gain/loss is recognized into a
  top-level-INCOME child resolved *by type* (German-named). This is the
  exact flow that threw pre-fix.
- Vendor bills to `1600` with input VAT to `1576`.

## Phase 8: Loans / budget / reconciliation / edge / volume

Hypothek + Kfz-Finanzierung amortization drive the debt-payoff path
(term from the `loan_term_months` slot, no English "mortgage"
keyword). A budget on the main expense classes. A reconciliation pass
on `1200 Bankkonto`. Edge cases: the non-zero localized
`Ausgleichskonto-EUR` (Tier C), plus a corrected mis-categorization.
A volume batch to stress paging.

---

## What this book tests that Alex's and Lin Wei's don't

- **Native non-English chart.** Type-based resolution
  (`_top_level_account_of_type`), Imbalance/Orphan detection, and
  debt-term resolution must all work with zero English account names.
- **Localized created-account names.** Auto-created FX gain/loss lands
  under the German income root and (Tier D) is named in German via
  locale inference from the SKR03 structural words.
- **German VAT (USt).** Two rates (19%/7%), input vs output VAT,
  USt-Vorauszahlung — exercised through taxtables and the business
  module.
- **Sole-proprietor equity flows.** Privatentnahme/Privateinlage
  instead of salary; EÜR (§4/3) cash-basis framing.
- **Near-parity FX with movement.** EUR/USD ≈1.08 with drift, so the
  cross-currency FX recognition can't pass by rate-near-1.0 luck.

---

## Acceptance & verification (bookkeeper-grade)

- **The acceptance test:** post and pay the USD client's invoice with
  rate drift — pre-fix throws inside `_get_or_create_fx_account` (no
  account named "Income"); post-fix it settles, recognizes FX into a
  German-named top-level-INCOME child, and the account GUID round-trips
  through the Layer-0 slot.
- **Cross-tool agreement to the cent:** net worth must match across
  `get_book_summary` / `balance_sheet` / `net_worth`, as Alex and Lin
  Wei do.
- **i18n surfaces fire:** the dashboard flags the German
  `Ausgleichskonto-EUR`; `debt_payoff_plan` uses the slot term; reports
  classify by type, not name.
- Route the FX number through explicit review — the bookkeeper
  validates base cases, not hand-calcs (per
  `feedback_bookkeeper_validates_base_cases`).

## Execution notes

Deterministic seed; per-phase backups (`.pre-phaseN.gnucash`) like the
existing generators. Build via `scripts/synthetic_book/build_sabine.py`
mirroring `build_lin_wei.py` (the non-USD-default analog). The chart is
seeded from the parsed `acctchrt_skr03` names verbatim so the German
strings are authentic, not transcribed.
