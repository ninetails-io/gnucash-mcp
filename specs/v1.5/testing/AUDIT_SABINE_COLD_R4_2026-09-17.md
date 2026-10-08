# Cold audit, fourth pass — samples/sabine-brenner.gnucash (built 2026-09-17 19:56)

**Verdict: WÜRDE UNTERSCHREIBEN** — one classification correction to book
(W1, the Fotodrucker on 4855) and one rate question to put to the client
(W2, 2026 Gastronomie). Neither costs the fiscus money. Nothing else a
Betriebsprüfung would correct.

Stance unchanged from R1–R3: Steuerberater reading a Freiberuflerin's SKR03
book (§18 EStG, Regelbesteuerung, Sollversteuerung, monatliche USt-VA ohne
Dauerfristverlängerung, GoBD). Read-only via the server API with
`GNUCASH_LOCALE=de_DE.UTF-8`, plus SQLite on a scratch copy
(`sabine_r4_audit.gnucash`). 1,854 transactions / 5,023 splits,
2024-12-04 … 2026-09-17. Balance sheet ties at 2025-12-31
(648,190.16 = 401,799.28 + 246,390.88). 2025 as booked: 178,498.60 income −
50,585.62 expenses. Cold pass first; R3 and the cross-model report read only
afterwards.

---

## Illegal or wrong

**W1 — Peripheriegerät on 4855 (§6 Abs. 2 EStG; BFH BStBl II 2004, 958).**
`2025-09-23 Cyberport, 4855 Sofortabschreibung GWG, EUR 756.75 netto`,
noted "GWG: Fotodrucker A3+ (Proofs) … selbständig nutzbar, GWG-Verzeichnis
Nr. 2025-04". A computer printer is the canonical non-independently-usable
peripheral under BFH 19.02.2004 VI R 135/01; only a Kombigerät with a
standalone copy/fax function qualifies. It may not be written off under
§6 Abs. 2 EStG and may not carry a GWG-Verzeichnis number.

The book already applies exactly this rule everywhere else — the Webcam
(70.81), Dokumentenscanner (205.45), USB-C Dock (92.18), Grafiktablett-Stift
(77.83) and USB-Hub (59.70) all sit on `4985 Werkzeuge und Kleingeräte`
with "Peripheriegerät, nicht selbständig nutzbar (BFH BStBl II 2004, 958)"
in the note. The Fotodrucker is the single row that contradicts the book's
own stated rule.

Tax effect is timing-neutral in practice — BMF 22.02.2022 allows a one-year
Nutzungsdauer for Computerhardware, so the deduction still lands in 2025 —
but the account and the Verzeichnis entry are wrong, and a Prüfer who pulls
the GWG-Verzeichnis finds an ineligible item on it. Fix: route to 4985 /
0027, drop Verzeichnis Nr. 2025-04, renumber 2025-04 onward.

**W2 — conditional: 2026 Bewirtungen at a flat 19%.** Thirteen 2026
restaurant Bewirtungen (Hofbräuhaus, L'Osteria, dean&david, Café Frischhut)
carry `1576` at 19/119 of the gross throughout — e.g. `2026-07-09 dean&david
gross 95.82, VSt 15.30`; `2026-08-17 dean&david gross 84.21, VSt 13.45`.
2026 VSt on this class totals **110.52**. If the reduction of
Restaurationsleistungen auf Speisen to 7% (§12 Abs. 2 Nr. 15 UStG, in force
01.01.2026) applies, a restaurant bill from 2026 is a **mixed-rate** receipt
— Speisen 7%, Getränke 19% — and the Vorsteuerabzug is capped at the
*gesetzlich geschuldete* Steuer (§15 Abs. 1 S. 1 Nr. 1 UStG; §14c Abs. 1
excess is not deductible). At a 70/30 Speisen/Getränke split the correct VSt
is ≈ 61.66, i.e. **≈ 49 EUR over-deducted across 2026**. 2025 at 19% is
correct either way.

Flagged rather than asserted: this turns on a rate change at the edge of what
I can verify from the book alone. Confirm the commencement date, then either
split the 2026 restaurant rows or leave them. The 2025 rows need no change.
Takeaway payees are already right — Vinzenzmurr (a Metzgerei) is at 7%
throughout, which is correct for Speisen zum Mitnehmen in both years.

### Verified clean this pass

- **Devices.** 7 rows on 4855 (3,267.29 net) + 6 on 4985 (739.52 net) in 21
  months, all distinct items, one unit per item per year, every 4855 row
  between 250 and 800 net. GWG-Verzeichnis **2025-01 … 2025-04** (02-17,
  04-23, 07-07, 09-23) and **2026-01 … 2026-03** (01-26, 05-04, 06-08) —
  chronological, unique, gap-free. Prices realistic throughout
  (Aktenvernichter P-4 406.13; Beamer 522.53; Laminiergerät A3 327.40).
- **December-2024 tail.** Invoices 000001–000004 posted 12-04/12-09/12-13/
  12-19 at original dates, 14,994.00 gross, each with an OPOS-Vortrag note;
  all four open at cut-over and settled individually 2025-01-08 / 01-14 /
  01-20 / 01-27 with the invoice number in the payment description. The
  12/2024 USt-VA of **2,394.00** is paid `2025-01-10` out of 1776 and equals
  456 + 551 + 646 + 741 exactly. The Anfangsbestand note states that 1400 and
  1776 are deliberately outside the Saldenvortrag; the 01.01.2025 entry
  (0320 22,408.97 / 1100 11,250.00 / 1200 18,400.00 / 9000 −35,558.97 /
  0640 −16,500.00) balances to zero, and 22,408.97 = 26,890.76 − 4,481.79
  to the cent.
- **Florists at 7%.** Blumenhaus Amalienstraße (3) and Blumen Lindner (3)
  all on `1571` — Anlage 2 Nr. 6–9 UStG. Confiserie Rottenhöfer (4) and
  Dallmayr (5) likewise 7%. No gift row carries 1576.
- **Geschenke cap.** 15 gifts, 15 distinct Empfänger-Wirtschaftsjahre, one
  gift per recipient per year, maximum **49.94 net** (Dr. A. Seidl,
  2026-04-07) — all ≤ 50 (§4 Abs. 5 S. 1 Nr. 1 EStG i.d.F. ab 2024). Every
  note names Empfänger and Anlass (§4 Abs. 7 EStG). VSt deductible
  throughout, correct below the cap (§15 Abs. 1a UStG).
- **7% Nutzungsrechte.** `8300` is Atelier Donau only, ten invoices, ten
  distinct works, none licensed twice in a year — 2025: Wanderführer
  Bayerischer Wald, Kinderbuch Bd. 1, Cover Herbstprogramm 2025,
  Jahreszeiten 2025, Donauufer/Kalender 2026; 2026: Cover Herbstprogramm
  2026, Wanderführer 2026, Jahreszeiten 2026, Donauufer/Kalender 2027,
  Kinderbuch Bd. 2. Verlag Bergblick's Editorial-Design retainer stays at
  19% (correct — Gestaltungsleistung, not Rechteeinräumung). The four
  Aukofer/Kosmos "Nutzungsrechte" lines at 19% are the conservative and
  defensible treatment for rights bundled into Werbe-/Verpackungsgestaltung
  (Abschn. 12.7 UStAE) and cost the fiscus nothing.
- **Input VAT by payee class.** 698 VAT transactions, **zero** rate
  deviations against the booked base. 7%: DB/DB Fernverkehr, MVG, FREENOW,
  both German hotels (explicitly "ohne Frühstück"), Hugendubel/PAGE/
  Rheinwerk, Amazon books (4 rows), Vinzenzmurr, florists, Confiserie,
  Dallmayr. §13b: Adobe CC/Stock, Figma, Dropbox, Backblaze, Monotype Fonts,
  Google Ads, Meta, LinkedIn, Domestika, Skillshare — `1577` and `1787` equal
  and opposite on every row. No VSt: Miete (§4 Nr. 12), Deutsche Post
  (§4 Nr. 11b), Kontoführung (§4 Nr. 8d), HUK/VGH (§4 Nr. 10a), Kfz-Steuer,
  VW-Bank-Zinsen (§4 Nr. 8a), Lufthansa MUC–LAX (§26 Abs. 3), ÖBB, Hotel
  Wien, Hotel Los Angeles, Adobe MAX (§3a Abs. 3 Nr. 5, Veranstaltungsort
  USA). **DHL Paket at 19% against Deutsche Post at 0%** is the correct
  Universaldienst distinction. TYPO Berlin (Monotype GmbH) at 19%.
- **USt-VA clearing.** 21 VAs, 2025-01-10 … 2026-09-10. Each month's
  accrual on all six accounts (1571/1576/1577 vs 1771/1776/1787) is cleared
  by the following month's VA **to the cent, 21/21 months**, zero mismatches.
  1771 appears only in months with 7% turnover, correctly. 09/2026 is the
  only open period (accrual −2,198.18, VA due 2026-10-12, past book end).
  The 10th rolls forward on 2025-05-12, 2025-08-11, 2026-01-12, 2026-05-11
  (§108 Abs. 3 AO). `1781` is empty — consistent with no Dauerfrist-
  verlängerung.
- **ESt chain and Steuerrücklage.** All ESt/SolZ/KiSt on `1810 Privatsteuern`,
  never as Betriebsausgabe (§12 Nr. 3 EStG). VZ on 10.03/10.06/10.09/10.12
  (§37 Abs. 1 EStG). 2025: 8,000 + 8,000 + 8,470 + 8,470, with a
  **nachträgliche VZ I.–II./2025 of 940.00 = 2 × (8,470 − 8,000)** after the
  2024 Bescheid — §37 Abs. 3 S. 3 EStG, exact. 2026: 8,470 + 8,470 + 10,533,
  **nachträglich I.–II./2026 = 4,126.00 = 2 × (10,533 − 8,470)**, exact.
  Abrechnung 2024 1,882.51 on 2025-09-15 and 2025 8,253.47 on 2026-09-15,
  each one month after the 12.08. Bescheid (§36 Abs. 4 EStG). The chain
  closes on itself: 2025 total ESt = 33,880 + 8,253.47 = **42,133.47**, and
  the 2026 VZ of 4 × 10,533 = **42,132** — §37 Abs. 3 S. 2 EStG to within
  rounding. Every Steuerrücklage draw from 1100 matches the payments it
  covers exactly: 2025-09-09 **11,292.51** = 8,470 + 940 + 1,882.51;
  2026-09-09 **22,912.47** = 10,533 + 4,126 + 8,253.47.
- **No bank account below zero at any day-end.** 1200 minimum
  **7,395.73** (2026-09-15), 1100 minimum **11,102.33** (2025-01-15),
  Ausgleichskonto never negative. Computed on `quantity`, which is the
  account-commodity amount.
- **Sollversteuerung and invoice sequence.** 000001–000134 gap-free and
  unique, id order = date order, output VAT recognised on the posting date.
  **Zero** payment transactions touch any VAT account. Five open at
  2026-09-17 summing to 14,087.22 (000129 1,789.76 / 000131 2,975.00 /
  000132 3,499.79 / 000133 2,713.20 / 000134 3,109.47); the one overdue is
  000129 Modehaus Lindberg, 2026-08-24, matching the dashboard warning.
- **Private zone.** 1810 holds only Finanzamt ESt debits; 1830 only the TK
  Beitrag (§10 Abs. 1 Nr. 3 EStG); 1880 only the twenty 1%-Regelung rows;
  1890 the Verpflegungspauschalen and two Überträge. Mortgage interest on the
  private Eigentumswohnung goes to `Privatkapital:Hypothekenzinsen
  (Privatentnahme)` — **equity, never a Zinsaufwand account** (§12 Nr. 1
  EStG). No business expense account contains a private payee: 4930 is
  Amazon/Office Discount/Viking/McPaper, 4940 is PAGE/Rheinwerk/Hugendubel,
  4650 is restaurants only. Groceries, pharmacy and cinema appear solely
  under 1800.
- **Bankarbeitstage.** 399 bank-touching dates; **8** fall on a non-banking
  day and all 8 are legitimate — the 01.01.2025 Eröffnungsbilanz, and seven
  month-end `Postbank Zinsgutschrift` valuta rows. Every real payment lands
  on a Bavarian banking day (Bayern holidays and 24./31.12. checked).
- **0320 Pkw notes.** The account slot now carries BLP 32,000.00, EZ 01/2024,
  AK 26,890.76 netto (= 32,000/1.19), AfA linear 6 Jahre = 4,481.79 (AfA-
  Tabelle AV), the 1%-Regelung at 320.00/Monat with the 80/20 split, the
  financing terms, **and** an explicit reasoning for omitting the 0.03%
  Zuschlag (§4 Abs. 5 Nr. 6 EStG): Wohnung and Betriebsstätte ~600 m apart
  and walked. The postings match: 20 × 368.64 (8920 64.00 / 8924 256.00 /
  1776 48.64), AfA 2025 booked 2025-12-31, 2026 AfA correctly not yet taken.
- **Loans.** VW Bank: 21 × 307.53, interest exact at 4.49% p.a. monthly on
  the opening balance every single month, Restschuld 11,140.58. Hypothek:
  20 × 2,009.38, interest exact at 3.65% p.a., Restschuld 378,366.02. Both
  are clean annuities.
- **Travel.** Verpflegungspauschalen exact against the BMF tables — Passau
  14 + 14 = 28; Berlin 14 + 28 + 14 = 56; **Los Angeles 43 + 64 + 64 + 43 =
  214**; **Wien 33 + 33 = 66** — all booked against 1890 Privateinlagen.
- **Bewirtung.** 45 rows, 70/30 between 4650 and 4654 **to the cent on every
  one**, VSt on the full net (§15 Abs. 1a S. 2 UStG), Anlass + Teilnehmer +
  "Bewirtungsbeleg liegt vor" on every note.
- **Integrity.** Zero unbalanced transactions, zero same-day duplicates, no
  voided rows, no future-dated postings.

---

## High-severity implausible

**H1 — Fuel split between 4530 and 1800 with one car on the 1%-Regelung.**
Sixteen ARAL purchases totalling **589.77** are booked to `1800
Privatentnahme allgemein` with no Vorsteuerabzug and **no note at all**,
while 40+ other ARAL/SHELL purchases go to `4530 laufende Kfz-Betriebskosten`
with 19% VSt and the note "Tankbeleg Pkw (Benzin)". Examples: `2025-01-02
ARAL 33.68`, `2025-07-07 ARAL Fil. 0378 60.18`, `2026-02-06 ARAL Fil. 0895
53.37`, `2026-07-10 ARAL Fil. 0816 50.49`.

`0320 Pkw` is the only vehicle in the book, it is 100% Betriebsvermögen, and
its private use is already compensated by the 1%-Regelung. Under that method
*all* running costs — fuel included — are Betriebsausgaben; carving out
individual fill-ups as Privatentnahme double-counts the private share and
contradicts the elected method. `4570 Fremdfahrzeuge` is empty and no second
vehicle is documented anywhere.

Direction is **against** the taxpayer: 589.77 of understated Betriebsausgaben
and ≈ **94.16** of forgone Vorsteuer, so no Prüfer raises an assessment on
it. But it is the one methodological inconsistency left in the book, and the
missing note on exactly these sixteen rows is what makes it read as a
classification leak rather than a decision. Either route them to 4530, or —
if the intent is a second, privately-held car — say so in the note and in
the 0320 slot.

Below HIGH, one line each: the Besprechungsstuhl (371.90, 2025-07-07) is
bought at Cyberport, an electronics retailer; the Beamer (522.53, 2026-05-04)
on 4855 sits in the grey zone on selbständige Nutzbarkeit (defensible —
modern projectors have standalone inputs); "NAS-Festplatte 8 TB" at 545.09
reads as a bare drive at an enclosure price; the Etikettendrucker (233.55,
4985) is labelled "selbständig nutzbar" while the book's other peripheral
notes say the opposite — cosmetic only, it is under 250 either way; business
gifts sit on `4653 Aufmerksamkeiten` where SKR03 designates 4630 (§4 Abs. 7
separate-recording is nonetheless satisfied by the dedicated account plus
per-recipient notes, no tax effect); `4957 Abschluß- u. Prüfungskosten` is
still empty although Bescheide arrive in both years and 4955 is only
75.00/month; FX difference −2.10 still lands on "Realisierter Gewinn/Verlust"
rather than an SKR03 Kursdifferenzen account; 203 transactions still carry
"Fil. NNNN" suffixes; 24 `split-register` price rows remain; `enter_date`
equals `post_date` on all 1,857 transactions.

---

## Versus R3 and the cross-model audit

| Item | Source | Status | Evidence |
|---|---|---|---|
| W1 florist VSt at 19% (€15.08) | R3 | **Fixed** | all 6 florist gifts now on `1571` at 7%; no gift row touches 1576 |
| W2 §4 Abs. 5 Nr. 6 Zuschlag undocumented | R3 | **Fixed** | 0320 slot states the 600 m walking distance and why no 0.03% applies |
| H1 serial duplicate devices, 34 rows / €15,738 | R3 + report §2 | **Fixed** | 7 rows / 3,267.29 on 4855, 6 / 739.52 on 4985; all items distinct, one per year |
| H1a GWG-Verzeichnis numbers reused / unordered | R3 | **Fixed** | 2025-01…04, 2026-01…03, chronological and unique |
| H1b nonsensical prices (Maus €596, Tastatur €651) | R3 + report §2 | **Fixed** | no mouse/keyboard on 4855 at all; remaining prices are item-appropriate |
| H1c peripherals on 4855 | report §2 | **Remains (narrowed) → W1** | one row left: Fotodrucker A3+ 756.75, 2025-09-23. The report's own fix text lists "printers" as independently usable, which is what the generator followed — that guidance is wrong under BFH BStBl II 2004, 958 |
| H2 no 2024 tail at cut-over | R3 | **Fixed** | 4 invoices 000001–000004 (14,994.00) open at 01.01.2025; 12/2024 VA 2,394.00 paid 2025-01-10; all four settled in January with invoice references |
| P6 0320 carries no BLP/EZ slot | R3 | **Fixed** | slot note carries BLP, EZ, AK, AfA, 1%-Regelung, 0.03% reasoning, financing |
| 2026 VZ sequencing | R3 (implicit) | **Improved** | R3 ran Q1/Q2 2026 already at the raised rate; R4 correctly keeps 8,470 until the 12.08.2026 Bescheid, then raises to 10,533 with a 4,126 catch-up |
| W9 FX result outside SKR03 | R3 | **Remains (LOW)** | −2.10 on "Realisierter Gewinn/Verlust", 2025-10-01 |
| Gifts on 4653 not 4630 | R3 | **Remains (LOW)** | no tax effect; §4 Abs. 7 satisfied |
| 4957 empty / no Jahresabschluss fee | R3 | **Remains (LOW)** | 4955 = 21 × 75.00 only |
| "Fil. NNNN" suffixes | R3 | **Remains** | 203 transactions |
| price residue, enter_date = post_date | R3 | **Remains (LOW)** | 24 `split-register` rows; 1,857/1,857 |
| USt-VA, Bankarbeitstage, Sollversteuerung, §13b, Bewirtung 70/30, TK, 1%-Regelung, AfA, VMA, private zone, 48.50 hook | R3 | **Preserved** | re-verified above, all clean |
| Copyright 7/19 split, 1%-Regelung, Bewirtung, per diems, monthly VA | report §2 "strengths" | **Preserved** | re-verified above |

**New this pass:** W1 (the one surviving peripheral on 4855 — a narrowing of
the cross-model report's headline finding, not a regression), W2 (2026
Gastronomie rate, conditional, ≈ €49), H1 (fuel split against the
1%-Regelung, ≈ €590 / €94 VSt, in the taxpayer's disfavour).

The cross-model report's **FAIL / SUBSTANTIAL REASSESSMENT** verdict on
§6 Abs. 2 EStG no longer stands: the €15,738.17 it assessed is down to a
single €756.75 row with a timing-neutral effect. Priority for the generator:
W1 (one row), then H1 (route the sixteen ARAL rows to 4530), then W2 once
the 2026 rate is confirmed.
