# Cold audit, second pass — samples/sabine-brenner.gnucash (built 2026-09-17 14:53)

**Verdict: WÜRDE UNTERSCHREIBEN** — with two corrections booked first
(N1 gifts, N2 licence invoices); nothing else a Betriebsprüfung would touch.

Auditor stance as in the first pass: Steuerberater reading a Freiberuflerin's
SKR03/EÜR book (Regelbesteuerung, Sollversteuerung, monatliche USt-VA, GoBD),
read-only via the server API (`GNUCASH_LOCALE=de_DE.UTF-8`) plus SQLite on a
scratch copy (sha `b6a07134…`). 1,874 transactions, 5,084 splits,
2025-01-01 … 2026-09-17. EÜR as booked: **2025** Betriebseinnahmen 167,575.12
− Betriebsausgaben 56,561.67 = **Gewinn 111,013.45** (steuerlich +386.50
4654 add-back = 111,399.95); **2026 YTD** 118,435.76 − 37,309.18 = 81,126.58.
Balance sheet ties at both dates (627,664.72 = 391,592.35 + 236,072.37).

Scope: only "Illegal or wrong" (tax-effective correction) and HIGH-severity
implausible. Polish omitted unless it rises.

---

## Illegal or wrong

**N1 — Geschenke exceed the €50 cap per Empfänger und Wirtschaftsjahr (HIGH, §4 Abs. 5 Nr. 1 EStG; §15 Abs. 1a UStG).**
The cap is per recipient per year, not per gift. Every 4653 note asserts
"≤ 50 € netto", but 10 of 13 recipient-years break the cap:

| Jahr | Empfänger | Summe netto | Belege |
|---|---|---|---|
| 2025 | Dr. C. Vogel (Praxis Dr. Vogel) | 51.50 | 2025-03-03 Confiserie Rottenhöfer (single gift already > 50; note says "≤ 50 €") |
| 2025 | J. Lindner (Architekturbüro Lindner) | 71.80 | 01-10 Dallmayr 22.16 + 01-27 Dallmayr 49.64 |
| 2025 | P. Ahmadi (Café Kosmos) | 83.48 | 06-10 Blumen Lindner 38.06 + 12-15 Confiserie 45.42 |
| 2025 | T. Wimmer (BioBackhaus) | 65.08 | 08-11 Confiserie 42.22 + 09-02 Dallmayr 22.86 |
| 2026 | Dr. A. Seidl (Stadtmarketing) | 85.42 | 01-02 Dallmayr 44.49 + 03-23 Confiserie 40.93 |
| 2026 | Dr. C. Vogel | 51.44 | 03-16 Dallmayr 22.29 + 09-07 Dallmayr 29.15 |
| 2026 | F. Aukofer (Brauerei Aukofer) | 59.77 | 08-13 Dallmayr 16.17 + 09-07 Blumen Lindner 43.60 |
| 2026 | J. Lindner | 58.73 | 05-20 Blumen 15.92 + 07-23 Blumen 42.81 |
| 2026 | K. Reisinger (Atelier Donau) | 137.01 | 01-13 43.44 + 04-02 47.26 + 07-20 46.31 (all Dallmayr) |
| 2026 | N. Berger (Festival Tollwood) | 62.65 | 06-08 Blumen 26.03 + 06-10 Confiserie 36.62 |

Correction: 2025 271.86 net + 23.60 VSt (of 4653 359.20); 2026 **455.02 net
+ 47.23 VSt — the entire 2026 4653** is non-deductible, and the VSt on it is
not abziehbar. Fully non-deductible gifts go to 4665, VSt leg dropped; the VA
for those months is understated by the VSt. The Beleg text also undermines
itself: "Anlass: Weihnachten" on 2025-03-03, 2026-04-02, 2026-07-20 and
2026-09-07 (×2); two "Projektabschluss" gifts to N. Berger two days apart.
Fix: one gift per recipient per Wirtschaftsjahr, cap enforced on the
recipient-year total, Anlass drawn from the calendar (Weihnachten only in
December), and the "≤ 50 €" assertion computed rather than pasted.

**N2 — 7% on repeated "Einräumung von Nutzungsrechten" for one motif to the same commercial customer (HIGH, §12 Abs. 2 Nr. 7c UStG; UStAE 12.7).**
8300 carries 40,505.00 net on 19 licence-only invoices (2025: 24,357.00;
2026: 16,148.00). The first pass rated the 7% a defensible risk position at
€14.9k; it has nearly tripled and its shape changed:
- **Brauerei Aukofer KG** — "Illustration Sudhaus-Motiv" licensed **8 times**
  (000030 05-19, 000038 06-23, 000043 07-10, 000055 09-11, 000056 09-15,
  000077 2026-01-05, 000092 03-06, 000103 05-11), 16,980.00 net — while the
  label design itself ("Etikettenserie Festbier", 19%) is invoiced separately
  for less. 000055 and 000056 licence the same motif four days apart.
- **Café Kosmos** — "Illustration Wandmotiv Gastraum" licensed **5 times**
  (000031, 000040, 000063, 000110, 000111), 10,541.00 net, next to 18,742.00
  of 19% work for the same café.
- **Atelier Donau** — 6 invoices, 12,984.00 net (Kinderbuch, Donauufer,
  Jahreszeiten): a publisher licensing illustration; this part is the
  textbook 7% case and stands.
For a brewery's beer label and a restaurant's wall, the Rechteeinräumung is
Nebenleistung to the Gebrauchsgrafik (BFH: Grafik-Designer taxes at 19%
unless the copyright transfer is the wesentlicher Zweck); a licence is
granted once (or per period), not re-invoiced every six weeks at a new
price. A Betriebsprüfung reclassifies Aukofer + Kosmos: **27,521.00 net →
USt-Nachforderung 3,302.52** (Sabine's, until corrected invoices reach the
vorsteuerabzugsberechtigte customers); Atelier Donau's 12,984.00 (1,558.08)
stays at risk but arguable. Fix in the generator: 7% only for Atelier
Donau, one licence invoice per work; for Aukofer/Kosmos fold the rights into
the 19% design invoice (or make the 7% share a single, clearly separate
licence per work with a Laufzeit in the notes).

Nothing else in the EÜR would be corrected. Specifically verified clean:
input VAT by payee class (insurance gross to 4520/4360; 11 EU/US SaaS and
ad payees through 1787/1577 net-zero with Rechnungsnr. and "§13b" in the
notes; Adobe MAX as Veranstaltungsort USA, nicht steuerbar; 1571 on Bahn,
MVG, FREENOW, Bücher, Metzgerei, Hotels — 152 rows); Bewirtung 47 rows all
70/30 to 4650/4654 with Anlass, Teilnehmer and "Bewirtungsbeleg liegt vor",
VSt on the full net (§15 Abs. 1a S. 2); private items (ADAC, Netflix,
Amazon Prime, Friseur, IKEA, groceries) to 1800; ESt/SolZ/KiSt only in
1810; Hypothekenzinsen only in Privatkapital (EQUITY); no revenue outside
A/R; no EXPENSE row touches the Postbank.

---

## High-severity implausible

None beyond N1/N2. Everything that reached HIGH in the first pass now
coheres:

- **Profit level.** 2025 Gewinn 111,013.45 is carried by the persona:
  1810 shows ESt-VZ 8,000 → 8,470 → 8,389 per quarter with the 2024
  Bescheid settled 2025-09-15 (1,882.51) and the 2025 Bescheid (12.08.2026)
  settled 2026-09-15 (−321.22) and I–II/2026 adjusted (−162.00); 2025 total
  33,558.78 ≈ tariff on zvE ≈ 96k (+ 8% KiSt, SolZ Milderungszone). TK is
  at the Höchstbeitrag, 1,171.41 (2025) → 1,249.11 (2026), childless PV
  rate — exactly what €111k implies. Drawings ≈ €101k/yr (1800 ≈ 1,250/mo
  + Hypothek 2,009.38 + ETF 1,200 + ESt + KV) leave the Bankkonto at its
  15,000 corridor and the Postbank accumulating (3,250 → 18,658.57). No
  overdraft at any date (1200 min 8,072.87, 2025-06-30).
- **Pkw.** AK 26,890.76 net = BLP 32,000 gross ÷ 1.19; AfA 4,481.79 = AK/6;
  0320 opens 22,408.97 (one prior year taken), closes 2025 at 17,927.18;
  1880 = 368.64 × 20 months to 2026-08-31 (8924 256.00 + 1776 48.64 + 8920
  64.00); EnBW gone. Residual, not HIGH: 0640 is a fresh 60-month 4.49%
  loan from 01/2025 (16,500 → 11,140.58) for a car in service since 01/2024,
  and the 0320 account still carries no BLP/Erstzulassung note — only the
  loan has slots.
- **Bankarbeitstage.** Zero bank splits on Sat/Sun or bundes-/bayerische
  Feiertage except Postbank Zinsgutschrift valuta month-end (correct).
  Hypothek 30th rolls (2025-08-30 → 09-01, 11-30 → 12-01, 2026-05-30 →
  06-01); Miete 1st rolls (2025-11-01 → 11-03); ETF 6th rolls (2025-01-06
  Heilige Drei Könige → 01-07); VA 10th rolls (2025-05-10 → 05-12, 08-10 →
  08-11, 2026-01-10 → 01-12, 05-10 → 05-11).
- **Entry dates.** All 1,874 transactions: enter_date = post_date, lag 0.
- **USt-VA.** 20 VAs; each clears all six accounts (1776/1771/1787 vs
  1576/1571/1577) by exactly the prior month's activity — 96/96
  account-months to the cent; only September 2026 is in flight (1776
  1,883.47, 1771 118.58, 1787 = 1577 = 83.70, 1576 198.21, 1571 30.36).
  No Erstattungsmonat; Zahllast 1,336.66 … 2,553.87.
- **Sollversteuerung.** 130 invoices 000001–000130, gap-free, id order =
  date order; every 8400/8300/8336/8338 row is an invoice posting; output
  VAT at posting, cleared by the posting month's VA; payments −11 … +18
  days around Net 14; 4 open (11,796.47 = 1400), 0 overdue.

Worth a sentence, below HIGH: GWG price tags (Tastatur 651.19, Maus 596.61,
Ringlicht 596.24 net) and Skillshare/Domestika "courses" at 75–385 each are
off by an order of magnitude for what they are; GWG-Verzeichnis numbers are
not chronological (2025-89 on 04-02, 2025-31 on 05-14); 199 descriptions
still carry "Fil. NNNN" (all private 1800 rows). No tax effect.

---

## Versus the first cold audit (AUDIT_SABINE_COLD_2026-09-17.md)

| Item | Status | Evidence |
|---|---|---|
| W1 insurance VSt | **Fixed** | HUK 612.00 → 4520 gross, VGH 428.00 → 4360 gross, no 1576 leg (2025-01-22/24, 2026-01-22/24) |
| W2 §13b inbound | **Fixed** | Meta, Google Ads, LinkedIn, Adobe CC/Stock, Figma, Monotype, Dropbox, Backblaze, Domestika, Skillshare: net to 4610/4964/4806/4945 + 1787 credit + 1577 debit (e.g. Google Ads 2025-01-16 352.59 / 66.99); 1577 = 1787 at every date; TYPO Berlin (Monotype GmbH) correctly 1576; Adobe MAX correctly no VAT |
| W3 Bewirtung | **Fixed** | 47 rows 4650/4654 = 70/30 exact, VSt on 100 % net, Anlass + Teilnehmer on every note; private meals at the same venues sit in 1800 without notes |
| W4 no 7% VSt | **Fixed** | 1571: 152 rows (DB Fernverkehr 176.64 → 12.36; Motel One, Hotel Weisser Hase; Rheinwerk, Hugendubel, PAGE; Vinzenzmurr, Dallmayr) |
| W5 no invoices / Ist–Soll mix | **Fixed** | 0 direct revenue rows; 130 sequential invoices with Leistungszeitraum; one regime |
| W6 private interest in EXPENSE | **Fixed** | `Privatkapital:Hypothekenzinsen (Privatentnahme)` is EQUITY (−23,553.62); P&L total = SKR03 only |
| W7 ADAC | **Fixed** | 2 rows, 188.00 → 1800 |
| W8 4985 misuse | **Fixed** | SaaS → 4964/4806; 4985 max 231.55 net; 4855 max 651.19 net with Verzeichnis-Nr. |
| W9 FX result outside SKR03 | **Remains (LOW)** | "Realisierter Gewinn/Verlust" 2.10 (2025-10-01), not 2150 |
| P1 no ESt-VZ | **Fixed** | 1810: 11 rows, quarterly + Bescheid cycle, 60,608.29 total |
| P2 Bergblick two channels | **Fixed** | 0 non-invoice Bergblick rows; retainer 2,400 → 2,500 net monthly through 000128 |
| P3 Adobe twice | **Fixed** | 21 × 54.98 only; the two bills are Flyeralarm 785.40 |
| P4 travel without lodging | **Fixed** | 4676 hotels per trip; 4674 Pauschalen via 1890 (LA 214 = 43+64+64+43, Wien 66, Berlin 56, Passau 28 — all correct); one Adobe MAX / TYPO per year |
| P5 weekend banking | **Fixed** | see Bankarbeitstage above |
| P6 Pkw parameters | **Mostly fixed** | BLP/AK/AfA cohere; residual loan-start vs Erstzulassung and no 0320 note |
| P7 TK too low | **Fixed** | Höchstbeitrag 1,171.41 / 1,249.11 |
| P8 vendor realism | **Fixed** | no Staples, no sevDesk |
| P9 customers pay to Postbank | **Fixed** | all Zahlungseingänge on 1200; Postbank = sweeps in, private out, interest |
| P10 7% Nutzungsrechte | **Escalated → N2** | 14.9k → 40.5k net, 19 invoices, repeated per motif |
| A1 entry dates | **Fixed** | 1,874/1,874 same day |
| A2 "Fil." suffixes | **Partly remains** | 199 transactions, private rows only |
| A3 no notes/memos/nums | **Fixed** | 1,706 notes, 129 memos, 132 nums; Rechnungsnr. on SaaS rows |
| A4 nothing reconciled | **Fixed** | 1200 and 1100 reconciled through 2026-08-31 |
| A5 calendar rigidity | **Fixed** | rolling dates, payment lags vary |
| A6 September Aukofer cluster | **Fixed** | 000123 08-17, 000127 09-07 |
| A7 price residue | **Remains (harmless)** | 24 `transaction`-type EUR/USD price rows |
| R1–R13 | **Preserved** | VA mechanics, §13b outbound (8336: 000029/000067/000106), Drittland (8338: 000053 USD 3,500 @ 0.8536, paid 10-01 @ 0.853, −2.10), 1%-Regelung, AfA, GWG, TK as Sonderausgabe, private zone wiring, invoice register, Kfz-Finanzierung, 7% output on Atelier Donau, Kontenrahmen discipline, the 48.50 hook |

**New this pass:** N1 (gift cap per recipient-year, plus false "≤ 50 €" and
mid-year "Weihnachten" notes), N2 (licence invoicing pattern makes the 7%
indefensible for Aukofer/Kosmos). Priority for the generator: N2 (rewrite
the licence policy — it is a €3.3k USt exposure and the persona's largest
remaining risk), then N1 (a recipient-year accumulator and a calendar-aware
Anlass), then the P6/W9/A2 residue as polish.
