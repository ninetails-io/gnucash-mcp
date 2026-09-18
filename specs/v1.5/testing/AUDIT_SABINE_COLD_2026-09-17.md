# Cold audit — samples/sabine-brenner.gnucash (built 2026-09-17)

Auditor stance: Steuerberater reading a Freiberuflerin's SKR03/EÜR book
(Regelbesteuerung, monatliche USt-VA, GoBD) as the Finanzamt would.
Read-only via the server API plus direct SQLite. 1,669 transactions,
4,446 splits, 2025-01-01 … 2026-09-17. EÜR 2025 as booked:
Betriebseinnahmen 140,425.50 − Betriebsausgaben 54,420.92 = Gewinn
86,004.58. 2026 YTD: 79,058.00 − 35,437.17 = 43,620.83.

Severity: HIGH = would be corrected by a Betriebsprüfung with tax effect;
MED = formal defect or material implausibility; LOW = cosmetic / Kontenrahmen.

---

## Illegal or wrong

**W1 — Vorsteuer on insurance premiums (HIGH, §4 Nr. 10 UStG).**
1576 credited on tax-exempt insurance; Versicherungsteuer is not Vorsteuer.
- HUK-Coburg Kfz-Versicherung 2025-01-22 and 2026-01-22: gross 612.00 → 4520 514.29 + 1576 97.71 (each)
- VGH Berufshaftpflicht 2025-01-24 and 2026-01-24: gross 428.00 → 4360 359.66 + 1576 68.34 (each)
Over-deducted 332.10; VAs 01/2025 and 01/2026 understated.
Fix: book gross to 4520/4360, no 1576 leg (Kfz-Steuer is already booked correctly without VSt — copy that).

**W2 — 19% Vorsteuer claimed on foreign B2B services that carry no German USt (HIGH, §13b / §15 Abs. 1 UStG).**
Reverse-charge purchases booked as domestic gross invoices. 1576 legs on:
Meta Platforms (IE) 338.16, Adobe MAX 283.84, Domestika (ES) 256.69,
Adobe Creative Cloud (IE) 219.45, Skillshare (US) 141.72, LinkedIn Ads (IE) 100.05,
Adobe Stock (IE) 99.75, Google Ads (IE) 61.15, Figma (US) 59.85, Dropbox (IE) 40.22,
Monotype 39.86, Backblaze (US) 35.91 — ≈ €1,675 over 21 months.
No invoice with German USt exists for any of these, so the 1576 credits are
fictitious and the VA Zahllast is understated by the same amount; Kz. 46/47/67
never reported. The accounts for this exist and are empty:
3120 Leistungen §13b, 1577 VSt §13b, 1787 USt §13b (0 rows each).
Fix: net to expense, 1787 credit + 1577 debit (net zero), VA Kz. 46 + 67.
Only All-Inkl (DE), Telekom, 1&1, Vodafone, Stadtwerke, Flyeralarm, sevDesk,
MediaMarkt/Apple/Cyberport/Gravis/Amazon.de are legitimately 1576.

**W3 — Restaurant bills booked 100% as 4653 Aufmerksamkeiten (HIGH, §4 Abs. 5 Nr. 1/2 EStG).**
73 rows, €3,324.20 net: Café Frischhut ×16, L'Osteria ×14, dean&david ×9,
Vinzenzmurr ×6, Hofbräuhaus ×3, all with full 1576.
These are Bewirtungen: 70% deductible (4650), 30% non-deductible (4654),
only with Bewirtungsbeleg naming Anlass and Teilnehmer — the book has zero
notes/memos anywhere. Read as gifts instead, 36 of 73 exceed €50 net
(e.g. 2025-06-11 Hofbräuhaus 77.50; 2025-11-27 Café Frischhut 80.27) and are
fully non-deductible (4665). Same venues also appear as 1800 Privatentnahme
(L'Osteria ×12, Hofbräuhaus ×11) — mixed use with no distinguishing memo.
Fix: 4650/4654 split with Anlass/Teilnehmer in notes; anything without a
Beleg → 1800.

**W4 — All input tax at 19%; 1571 Abziehbare VSt. 7% has zero rows (MED, §12 Abs. 2 UStG).**
- Bahn Fernverkehr (7% since 2020): Deutsche Bahn 2025-02-05 gross 145.43 → booked 122.21 + 23.22 VSt; correct 135.92 + 9.51. DB Navigator + Deutsche Bahn total ≈ €2,530 net → ≈ €480 VSt claimed vs ≈ €197 due.
- Books/magazines (7%): Rheinwerk Verlag 2025-01-21 gross 47.69 → 40.08 + 7.61; Hugendubel 2025-02-17; PAGE Magazin ×N (4940 total 383.66 net, 72.9 VSt claimed vs ≈ 30).
- Vinzenzmurr (Metzgerei, 7% on food) under 4653.
Over-deducted ≈ €330. Fix: 7% legs to 1571; VA correction.

**W5 — 85% of revenue has no invoice document and no invoice number (MED, §14 Abs. 4 Nr. 4 UStG; GoBD Belegprinzip).**
8400 holds 99 rows / €190,558 net; only 9 are invoice-posted (Bergblick 000001–000010, 000013).
90 "Honorar <Kunde>" bank receipts (€167,558 net) and 11 "Nutzungsrechte …"
7% receipts (€14,890 net) are posted bank → 8400/8300 + 1776/1771 with an empty
`num`, no invoice, no memo. The Finanzamt cannot verify the sequence, the
Leistungsdatum, or the USt-Ausweis for those. Secondary: the book mixes
Sollversteuerung (000013: 722.00 USt sits in 1776 since 2026-09-01, unpaid)
with Istversteuerung (direct Honorare taxed on receipt). A Freiberuflerin may
elect §20 UStG, but one book must show one regime.
Fix: generator emits an invoice (sequential across all customers) for every
Honorar/Nutzungsrechte receipt, or at minimum writes the invoice number into
`num` and the Leistung into notes; decide Ist vs. Soll and state it in the
persona doc.

**W6 — Private mortgage interest lives in an EXPENSE-type account (MED, §12 Nr. 1 EStG boundary).**
Privatausgaben:Hypothekenzinsen: 14,253.65 (2025), 9,299.97 (2026 YTD).
Outside the SKR03 ranges, so the EÜR total is unaffected — but GnuCash's P&L
and `spending_by_category` show it as the third-largest expense (20.8%), and
the balance sheet's Retained Earnings 2025 (71,750.93) is profit after private
interest; the business profit is 86,004.58. "Nothing private in the P&L" is
violated at the account-type level.
Fix: make Privatausgaben an EQUITY branch (drawings) or book the interest leg
straight to Privatkapital; keep the Hypothek principal split.

**W7 — ADAC as Kfz-Betriebskosten with VSt (LOW).**
15 rows, €848.02 net + 1576 125.53 to 4530. ADAC e.V. Mitgliedsbeitrag carries
no USt and is a personal membership; 15 charges in 21 months for an annual
fee is also implausible. Fix: 1800, no VSt, once a year.

**W8 — 4985 Werkzeuge und Kleingeräte used for SaaS and for €250–800 hardware (LOW, Kontenrahmen / §6 Abs. 2 EStG).**
Software subscriptions (Adobe CC 21 × 54.98, Adobe Jahresabo 660.00, Adobe
Stock, Figma, Monotype, Backblaze) belong in 4964 (Lizenzen) or 4806.
Hardware rows > €250 net expensed as Kleingeräte instead of GWG (4855 +
GWG-Verzeichnis): MediaMarkt 2025-01-16 279.04; Cyberport 2025-03-24 266.75;
Apple 2025-08-03 287.41; MediaMarkt 2025-09-09 253.77; Cyberport 2025-11-06
263.73; Amazon.de 2026-01-12 269.59; Apple 2026-07-13 281.39; Apple 2026-08-16
255.21; MediaMarkt 2026-09-14 267.58. Fix: reclassify.

**W9 — FX result outside SKR03 (LOW).**
"Erlöse u. Erträge 2/8:Realisierter Gewinn/Verlust" carries the 2.10 Kursverlust
on the Lumen Labs payment (2025-10-01). SKR03: 2150 (Aufwand) / 2660 (Ertrag).

---

## Implausible

**P1 — No Einkommensteuer-Vorauszahlungen at all (HIGH).**
Profit 2025 ≈ €86k, yet 1810 Privatsteuern has zero rows and the only
Finanzamt payments are the 20 USt-VAs. Seven quarterly ESt/SolZ (+KiSt)
prepayment dates (10.03/06/09/12) pass with nothing, and no 2024 Nachzahlung
appears. A Freiberuflerin at this income pays ≈ €6k/quarter. Fix: generator
adds quarterly 1200 → 1810 transfers (and a Jahresausgleich in 2026).

**P2 — Verlag Bergblick paid through two channels (MED).**
Monthly retainer invoices 000001–000010 (2,400 net, always paid on the 20th)
AND direct "Honorar Verlag Bergblick" receipts without invoice: 2025-01-05
(2,168.18 gross — same day invoice 000001 posts), 2025-04-02, 2025-07-13 (Sun),
2025-07-25, 2025-09-02, 2026-06-11, plus two on Postbank (4,331.60). The
retainer stops after 08/2025 unexplained and resumes as a one-off 3,800
(000013) in 09/2026. Fix: one channel per customer; if the retainer ends, end it.

**P3 — Adobe subscribed twice (MED).**
Bill 000001 "Creative Cloud Jahresabo" 660.00 net (2025-02-01, paid 02-10)
AND 21 monthly "Adobe Creative Cloud" 54.98 net charges (1,154.58 total). The
annual is never renewed in 02/2026. Fix: keep one.

**P4 — Travel without lodging; conferences every other month (MED).**
4670: Lufthansa 6 flights + DB 18 tickets + Sixt 6 + FREENOW 9, but zero hotel
rows and no 4674 Verpflegungsmehraufwand. Adobe MAX charged 9× (€2,069) and
TYPO Berlin 5× (€1,723) — both annual events. Fix: one ticket per event per
year; add hotel + Pauschalen per trip.

**P5 — Bank activity on weekends (MED, SEPA books on TARGET days only).**
27 of 100 Honorar credits fall on Sat/Sun (2025-01-04 Sat BioBackhaus
1,875.44; 2025-01-05 Sun Bergblick 2,168.18; 2025-04-19 Sat ×2; 2025-05-11 Sun
BioBackhaus 2,285.99 …) and 4 of 20 Finanzamt debits (2025-05-10 Sat,
2025-08-10 Sun, 2026-01-10 Sat, 2026-05-10 Sun). Standing orders (Miete,
Krankenkasse 7/21 each) and the Hypothek (30th) likewise never shift.
Fix: roll to the next Bankarbeitstag.

**P6 — Pkw parameters don't cohere (LOW).**
Residual 24,000 on 2025-01-01 with 4,800/yr linear (6 yrs) implies cost 28,800
net ≈ 34,272 gross, but the 1%-Regelung uses BLP 32,000 (320/month) — a list
price below the purchase price. The car both fuels (ARAL/Shell 32 rows) and
charges (EnBW Ladestation 13 rows); a PHEV would take the 0.5% rule. No
0.03%-Regelung for Wohnung–Betriebsstätte trips (defensible: both in Schwabing).
Fix: state BLP/cost/Erstzulassung in the 0320 account notes; pick one drivetrain.

**P7 — TK contribution low for the income (LOW).**
781.53/month (2025) → 812.40 (2026) implies an assessment base ≈ €3,680/month;
at €86k profit a voluntarily insured Freiberuflerin sits at the ceiling
(≈ €1,170). Defensible if the Beitragsbescheid lags; note it.

**P8 — Vendor realism (LOW).**
Staples closed its German stores in 2023/24 yet appears 31× through 2026-09;
Steuerkanzlei Hoffmann (75/month, 4955) *and* sevDesk (14/month) *and* GnuCash
is three bookkeeping systems.

**P9 — Customers alternate payee IBANs (LOW).**
BioBackhaus pays 3× to 1100 Postbank (6,574.75) and 8× to 1200 Bankkonto;
Festival Tollwood and Bergblick likewise. Postbank also carries private
spending (Rossmann, Starbucks). Fix: one business account per customer.

**P10 — 7% on "Nutzungsrechte" is a risk position, not an error (LOW).**
€14,890 to 8300 on 11 receipts + 000004. §12 Abs. 2 Nr. 7c UStG applies only
where the Urheberrechtsübertragung is the Hauptleistung; for Gebrauchsgrafik
the BFH taxes the design service at 19%. Keep, but the persona doc should say
these are licence-only invoices.

---

## Generation artifacts

**A1 — Every entry date is the build date.** All 4,446 splits carry
enter_date 2026-09-17, including the 2025-01-01 opening balances. GoBD
(§146 Abs. 1 AO, "zeitgerecht") reads this as 20 months of late recording.
The legal-pass plan asked for entry date = document date; the builder writes
"now".

**A2 — "Fil. NNNN" branch suffixes.** 443 of 578 distinct descriptions
(561 transactions) end in a random 4-digit Filiale: Lufthansa Fil. 0992,
FREENOW Fil. 0991, Sixt Fil. 0163, DB Navigator Fil. 0124, Meta Platforms
Fil. 0741, Skillshare Fil. 0040, Amazon.de Fil. 0446, Apple Store München
Fil. 0553 — entities without branches. The same payee appears with and
without a suffix, which also splits payee statistics.

**A3 — No notes, memos, or document numbers.** 0 of 1,669 transactions have
notes; 0 split memos; `num` is set only on the 14 invoice/bill posts. No
Belegnummer, no Bewirtungsanlass, no Reiseziel anywhere.

**A4 — Nothing reconciled.** 1,368 Bankkonto and 262 Postbank splits, all
state `n`; dashboard flags three accounts never reconciled.

**A5 — Calendar rigidity.** VA always on the 10th, invoice payment always on
the 20th (Net 14 → one day late, eight times running), Sparplan on the 6th,
Hypothek on the 30th, no weekend/holiday shifting (see P5).

**A6 — September 2026 cluster.** Brauerei Aukofer credits on 09-02, 09-03,
09-17 (2,331 / 1,304 / 2,750 net) — three payments from one client in 16
days after 8 months of nothing from them.

**A7 — Harmless residue.** Three EUR/USD price rows of type `transaction`
(source user:split-register) from the Lumen Labs post/pay; the ETF opening
lot titled "Sparplan-Bestand" while purchase lots are titled by the 1st of the
month although bought on the 6th.

---

## What's right

**R1 — USt-Voranmeldung mechanics are exact.** 20 monthly VAs (01/2025 …
08/2026) on the 10th; each one debits 1776/1771 and credits 1576 by precisely
the prior month's turnover — 48 of 48 account-months match to the cent —
and pays the variable Zahllast from 1200 (407.06 … 2,383.63). 1776 at
2026-09-17 = 2,449.48 = September to date; 1576 = 503.99 = September VSt to
date. 1781 unused, consistent with no Dauerfristverlängerung.

**R2 — §13b outbound (Austria).** 000007 (2,900) and 000012 (1,750) to
Werkstatt Neubau Kommunikation GmbH (ATU12345678): taxable = 0, no tax table,
"Steuerschuldnerschaft des Leistungsempfängers" on the entry, posted to 8336.
Correct (ZM obligation is outside the ledger).

**R3 — Drittland service (USA).** 000011 Lumen Labs Inc., USD 3,500 → 1407
Ford. USD / 8338 at 0.8536 (2,987.60), paid 2025-10-01 at 0.853 with the
2.10 Kursverlust recognised. §3a Abs. 2 UStG, nicht steuerbar, no USt-IdNr.
on the US customer — all correct.

**R4 — 1%-Regelung.** 20 months, 368.64 each to 1880 Unentgeltliche
Wertabgaben = 8924 256.00 (80% of 320) + 1776 48.64 + 8920 64.00 (20% without
USt). Textbook Abschn. 15.23 UStAE split, no gaps.

**R5 — AfA.** 2025-12-31 "AfA Pkw 2025 (linear, 6 Jahre)" 4,800 → 4832,
0320 Pkw 24,000 → 19,200. 2026 AfA correctly not yet booked.

**R6 — GWG.** 20 rows on 4855, all ≤ €800 net (max 654.83 Apple 2025-06-09),
individually identifiable.

**R7 — Krankenkasse as Sonderausgabe.** TK monthly to 1830, never to a 4xxx
account; 781.53 → 812.40 from 2026-01. The scheduled transaction matches.

**R8 — Private zone wiring.** Hypothek (annuity 2,009.38; interest 1,201.46 →
1,153.47, principal 807.92 → 855.91; 395,000 → 378,366.02), ETF Sparplan
(21 × 1,200; 380.3633 IWDA.AS; one lot per purchase; cost 33,275; market
35,014.23), Eigentumswohnung 540,000 — all outside SKR03, bridged through
1800 (90,126.82), 1890 (1,220.62) and Privatkapital. Balance sheet ties:
634,410.74 = 391,956.08 + 242,454.66. No bank overdraft at any date
(1200 min 10,658.66 on 2026-08-18).

**R9 — Invoice register.** 000001–000013 sequential and chronological
(000004 on 03-12 after 000003 on 03-05; 000007 on 05-14 after 000006 on
05-05); bill counter separate. Customer master complete: street, city,
country, well-formed USt-IdNr. (DE811234567, DE812345671, ATU12345678),
Lumen Labs correctly without one. Net 14 terms; 000013 (4,522.00) is 2 days
overdue on the audit date — the dashboard says so.

**R10 — Kfz-Finanzierung.** VW Bank 307.53/month; 2121 interest declining
61.74 → (≈ 4.5% p.a.), 0640 principal 16,500 → 11,140.58.

**R11 — 7% output on Atelier Donau 000004** via the "USt 7%" table → 1771,
cleared in the 03/2025 VA.

**R12 — Kontenrahmen discipline elsewhere.** Kfz-Steuer (Hauptzollamt)
without VSt; bank fees (4970) without VSt; studio rent 4210 without VSt
(Vermieter ohne Option), 1,150 → 1,190 from 01/2026; Stadtwerke 19% to 4240;
Saldenvortrag 9000 = 29,150 = 18,400 + 3,250 + 24,000 − 16,500.

**R13 — The onboarding hook is intact.** 2026-09-17 "Unklare Lastschrift
(noch zu klären)" 48.50 to Ausgleichskonto-EUR; the dashboard warns on it.

---

## Versus S1–S11 (old build, BOOKKEEPER_REVIEW_DEMO_GENERATORS.md §7)

| Item | Old finding | Now |
|---|---|---|
| S1 | Mortgage interest in 2110 | **Fixed** — out of SKR03 into Privatausgaben; residual W6 (EXPENSE type pollutes GnuCash P&L) |
| S2 | 1576 never cleared, flat €1,850 VA | **Fixed** — R1, 48/48 months exact |
| S3 | Pkw without private-use imputation | **Fixed** — R4 1%-Regelung + R5 AfA; P6 parameter nit |
| S4 | Entry dates = generation date | **Not fixed** — A1, every split enter_date = 2026-09-17 |
| S5 | Invoice numbers out of order | **Fixed** — R9 |
| S6 | Drittland to 8120; EU B2B unhandled | **Fixed** — R2 (8336), R3 (8338) |
| S7 | Customer master data empty | **Fixed** — R9 |
| S8 | No AfA | **Fixed** for the Pkw — R5 |
| S9 | No year-end close | Unchanged by ruling (GnuCash convention) — fine |
| S10 | ETF in business | **Fixed** — R8 |
| S11 | €48.50 Unklare Lastschrift | Present by design — R13 |

**New in this build (not in S1–S11):** W1 insurance VSt; W2 §13b inbound
never used while 1577/1787/3120 sit empty; W3 Bewirtung as Aufmerksamkeiten;
W4 no 7% Vorsteuer ever; W5 no invoices for 85% of revenue and a Soll/Ist
mix; P1 no ESt-Vorauszahlungen; P2 Bergblick double channel; P3 Adobe double
subscription; P4 travel without lodging; P5 weekend banking; A2 "Fil." suffixes;
A3 no notes/memos/Belegnummern.

**Priority for the generator:** W2, W1, W3, W4 (all mechanical: a payee →
tax-treatment table), then P1 (four dates a year), then W5 (invoice per
receipt or `num` + notes), then A1/A2/P5 (dates and descriptions). Everything
in "What's right" should be frozen as-is — R1–R4 are the parts a Steuerberater
would show a client as the model.
