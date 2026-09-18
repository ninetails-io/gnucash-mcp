# Cold audit, third pass — samples/sabine-brenner.gnucash (built 2026-09-17 16:36)

**Verdict: WÜRDE UNTERSCHREIBEN** — one €15 VSt correction to book (W1) and
one written explanation to obtain before the signature (H1, the device
purchases); nothing else a Betriebsprüfung would correct.

Stance unchanged from R1/R2: Steuerberater reading a Freiberuflerin's SKR03
book (Regelbesteuerung, Sollversteuerung, monatliche USt-VA ohne
Dauerfristverlängerung, GoBD), read-only via the server API with
`GNUCASH_LOCALE=de_DE.UTF-8` plus SQLite on a scratch copy (sha
`10512a7304c1…`). 1,886 transactions, 5,115 splits, 2025-01-01 … 2026-09-17.
P&L as booked: **2025** 178,711.75 − 57,215.96 = **121,495.79** (steuerlich
+497.89 4654 +55.11 4665 = 122,048.79); **2026 YTD** 113,818.18 − 39,965.97
= 73,852.21. Balance sheet ties at both dates (2025-12-31: 628,469.65 =
401,753.01 + 226,716.64; 2026-09-17: 630,066.88 = 391,924.84 + 238,142.04).
Read the R2 report only after the cold pass.

---

## Illegal or wrong

**W1 — 19% VSt on cut flowers (Anlage 2 Nr. 7 UStG: 7%).** Five gifts from
"Blumen Lindner" on 4653 carry 1576 at 19/119 of the gross; a florist's
Strauß is 7%. Over-deducted VSt 15.08 total:
2025-06-13 gross 23.31 (3.72 booked / 1.52 due); 2026-01-02 38.91 (6.21 /
2.55); 2026-02-04 28.72 (4.59 / 1.88); 2026-03-12 50.02 (7.99 / 3.27);
2026-05-20 18.95 (3.03 / 1.24). At 7% the net values rise (T. Wimmer
2026-03-12 → 46.75) but every recipient-year stays ≤ 50, so the cap is
unaffected. Fix: 1571 leg on florist rows.

**W2 — conditional, small.** No §4 Abs. 5 Nr. 6 EStG add-back for
Wohnung–Betrieb trips although the Wohnung (Eigentumswohnung Schwabing) and
the studio (Miete Studio Schwabing) are in the same quarter and the Pkw is on
the 1%-Regelung. If the Pkw is used for those trips, a Prüfer adds 0.03% ×
32,000 × km per month less the Entfernungspauschale (≈ €12/month at 3 km).
If she walks, nothing. Worth a note on the 0320 account either way.

Specifically verified clean this pass:
- **Geschenke** (4653/4665): 15 gifts, 15 distinct Empfänger-Wirtschaftsjahre,
  max 49.84 net (Dr. A. Seidl 2026-01-20); the one breach — Dr. C. Vogel
  2025-03-03, 51.50 net — sits on 4665 at gross 55.11 with no VSt leg;
  Anlass is Geburtstag/Projektabschluss, "Weihnachten" only on 2025-12-15.
- **7% Nutzungsrechte** (8300 10,562.00 / 10,560.00): Atelier Donau only,
  one invoice per work per year — 2025: 000004 Wanderführer, 000005
  Kinderbuch Bd. 1, 000019 Cover Herbstprogramm, 000023 Jahreszeiten, 000029
  Donauufer Kalender 2026; 2026: 000082, 000083, 000091, 000092, 000094 —
  each with Laufzeit and §12 Abs. 2 Nr. 7c in the notes. Brauerei Aukofer
  and Café Kosmos rights lines are 19% inside the design invoice (000003
  1,392.00; 000010 1,372.00; 000088 674.00; 000101 648.00).
- **Input VAT by payee class**: §13b net-zero (1577 = 1787 at every date) on
  Adobe CC/Stock, Figma, Monotype Fonts, Dropbox, Backblaze, Google Ads,
  Meta, LinkedIn, Domestika, Skillshare with Rechnungsnr.; 7% on Deutsche
  Bahn, MVG, FREENOW, both German hotels (ohne Frühstück), Bücher/PAGE,
  Dallmayr/Confiserie, Vinzenzmurr; no VSt on Deutsche Post, Kontoführung,
  HUK/VGH, Kfz-Steuer, Miete ("Vermieter ohne Option"), Lufthansa MUC–LAX
  (§26 Abs. 3), ÖBB, Hotel Wien (AT-USt), Hotel Los Angeles, Adobe MAX
  (Veranstaltungsort USA); TYPO Berlin (Monotype GmbH) at 1576.
- **Bewirtung**: 54 rows, all 4650/4654 = 70/30 to the cent, VSt on the full
  net, Anlass + Teilnehmer + "Bewirtungsbeleg liegt vor", none on a weekend.
- **USt-VA**: 21 VAs 2025-02-10 … 2026-09-10, each clearing all six accounts
  (1776/1771/1787 vs 1576/1571/1577) by exactly the prior month's activity —
  21/21 months to the cent; 10th rolls to Monday on 2025-05-12, 2025-08-11,
  2026-01-12, 2026-05-11; September 2026 in flight (1776 2,352.39, 1787 =
  1577 = 65.85, 1576 227.92, 1571 12.59 → Zahllast 2,111.88).
- **ESt / Steuerrücklage**: 1810 2025 = 8,000 + 8,000 + 8,470 + 940 +
  1,882.51 + 8,470 = 35,762.51; 2024 tax 33,882.51 → VZ 8,470 = ¼; 2025 tax
  33,880 + 4,917.21 = 38,797.21 → VZ 9,699 = ¼ rounded down, nachträglich
  I–II/2026 2 × 1,229 = 2,458; 38.8k is within a few hundred euros of
  Grundtarif + SolZ + 8% KiSt on zvE ≈ 107k (121.5k − 14.1k KV/PV). Every
  Finanzamt debit on the 10th/15th is preceded one Bankarbeitstag earlier by
  a Postbank draw of the identical sum (2025-09-09 11,292.51 = 8,470 + 940 +
  1,882.51; 2026-09-09 17,074.21 = 9,699 + 2,458 + 4,917.21).
- **Cash story**: Postbank 11,250.00 + 47,922.33 sweeps + 215.25 Zinsen −
  35,762.51 ESt − 2,000 ETF − 1,199.89 private = 20,425.18 at 2025-12-31;
  → 10,149.95 today. No EXPENSE row touches 1100. Day-end minima 1100
  7,867.17 (2025-03-19), 1200 6,637.11 (2026-09-15); never below zero.
- **Bankarbeitstage**: only Postbank Zinsgutschrift on weekend month-ends
  (valuta, correct); Hypothek rolls 2025-08-30 → 09-01, 11-30 → 12-01,
  2026-02-28 → 03-02, 05-30 → 06-01; ETF 2025-01-06 → 01-07.
- **Sollversteuerung / sequence**: 000001–000130 gap-free, id order = date
  order, output VAT on posting date, payments only 1400↔1200 (1407↔1200 for
  USD), lags 3–32 days; 5 open (1400 14,087.22), 1 overdue (000125 Modehaus
  Lindberg 2026-08-24, 1,789.76 gross). USD invoice 000052 3,500 @ 0.8536
  → 8338 2,987.60, paid 2025-10-01 @ 0.853, FX −2.10.
- **Private zone**: Hypothek 2,009.38 = Zinsen (395,000 × 3.65%/12 =
  1,201.46 in month 1) + Tilgung, Zinsen in EQUITY only; ETF and mortgage
  via 1800/Privatkapital four-split; TK Höchstbeitrag 1,171.41 = 5,512.50 ×
  (17.05% + 4.2%) exactly, 1,249.11 in 2026; 1%-Regelung 20 × 368.64 to
  2026-08-31 (256.00 + 48.64 + 64.00); AfA 4,481.79 = 26,890.76/6, BLP
  32,000 = AK × 1.19; VMA exact (Passau 14+14, Berlin 14+28+14, LA
  43+64+64+43, Wien 33+33) via 1890.
- **Entry dates**: 1,886/1,886 enter_date = post_date.

---

## High-severity implausible

**H1 — Serial duplicate device purchases on 4855/4985 (Prüfer would ask; tax
effect if unanswered).** 34 GWG rows + 18 Kleingeräte rows in 21 months,
€18,290 net (2025: 8,870.79 + 1,432.03; 2026 YTD: 6,867.38 + 1,120.57), all
under €800 net, but the items repeat: Tastatur ×6 (4855: 537.03 01-16,
651.19 05-14, 615.21 09-26, 259.00 2026-07-06; 4985: 57.76, 43.86), Webcam
×7 (298.73, 560.76, 597.44, 265.61 + 65.48, 210.24, 231.55), Studio-Kopfhörer
×5 (607.48 2025-04-02, 376.92 04-04, 349.03 04-22 — three pairs in twenty
days — 584.66 2026-06-17, 179.41), Grafiktablett-Stift ×6, Ringlicht ×5,
NAS-Festplatte 8 TB ×4, Farbkalibrierungsgerät ×4, Externe SSD 2 TB ×4,
Monitorarm ×4, Maus ×2 (596.61, 271.12), Etikettendrucker ×3. A Prüfer
treats the surplus units as Entnahme (§4 Abs. 1 S. 2, §6 Abs. 1 Nr. 4 EStG;
§3 Abs. 1b UStG) or denies them outright: at one unit per device per two
years the exposure is roughly €12k net + €2.3k VSt. Two aggravators: the
GWG-Verzeichnis numbers are not chronological and are reused ("Nr. 2025-21"
on 2025-01-16 Tastatur and 2025-12-03 Ringlicht; "2025-27" on 03-19 and
12-05), which says the "laufend zu führende Verzeichnis" (§6 Abs. 2 S. 4)
is not; and unit prices are an order of magnitude off (Maus 596.61, Webcam
597.44, Stift 517.65, Tastatur 651.19 net; likewise Parkhaus Stachus 89.55
net per visit). Fix in the generator: a device inventory with a
replacement cycle (one Tastatur, one Maus, one Kopfhörer…), realistic price
bands per item, a monotonic Verzeichnis counter.

**H2 — 2025 opens with no prior-year tail although the business ran in 2024.**
9000 Saldenvortrag carries only 0320 22,408.97 / 1100 11,250.00 / 1200
18,400.00 / 0640 −16,500.00; 1790 Umsatzsteuer Vorjahr and 9008 Debitoren are
empty; no Finanzamt debit on 2025-01-10 for the 12/2024 USt-VA (the first is
2025-02-10 for 01/2025, and every later month shows a 1.2–2.7k Zahllast);
the first Zahlungseingang (2025-01-13, Re. 000003) settles a 2025 invoice.
Yet the Pkw (EZ 01/2024, one year of AfA taken), the VW loan, and "ESt-VZ
lt. Bescheid 2024" (8,000/quarter) say ~€100k profit in 2024. Bank
statements for January 2025 would show the December VA and December
collections. Fix: seed 1790 (≈ 2.4k) and 9008 (two or three December
invoices) in the Anfangsbestand and clear them in January.

Below HIGH, one line each: no Jahresabschluss/Erklärungs-Honorar on 4957 in
either year although Bescheide 2024 and 2025 arrive (4955 is 75.00/month
only); Vinzenzmurr as venue for 12 "Jahresgespräch"/"Kick-off" Bewirtungen at
the 7% takeaway rate (no fisc loss); business gifts on 4653
"Aufmerksamkeiten" where SKR03 designates 4630 (§4 Abs. 7 separate
recording is satisfied, no tax effect); Privateinlage 1,349.53 on
2026-05-07 with no source and no need (1200 ≥ 6,637.11 throughout); the
reports are Soll-basis — the EÜR 2025 moves open A/R 7,990.85 gross and the
12/2025 VA paid 2026-01-12 (outside the 10-day window, BFH X R 44/16) into
2026; repeats one year apart (Werkstatt Neubau "Corporate Design Relaunch"
2,900.00 on 2025-05-14 and 2026-05-14; Lufthansa 1,148.00 on 2025-08-18 and
2026-08-18; Bescheid "vom 12.08." both years).

---

## Versus R2 (AUDIT_SABINE_COLD_R2_2026-09-17.md)

| Item | Status | Evidence |
|---|---|---|
| N1 gift cap per Empfänger-Jahr | **Fixed** | 15 gifts / 15 recipient-years, max 49.84; the one > 50 (51.50, 2025-03-03) on 4665 gross without VSt; Weihnachten only 12-15; 4653 2025 203.83, 2026 234.12 |
| N2 7% licence pattern | **Fixed** | 8300 40,505 → 10,562.00 + 10,560.00, Atelier Donau only, one invoice per work; Aukofer/Kosmos rights at 19% inside 000003/000010/000088/000101 |
| GWG price tags (below HIGH in R2) | **Escalated → H1** | same prices, now with item names that repeat 4–7× and a Verzeichnis with reused numbers |
| P6 Pkw residual | **Partly fixed** | 0640 opens via Saldenvortrag (no longer a "fresh 01/2025 loan"), slots apr 4.49 / 60 months; 0320 still carries no BLP/EZ slot — only the AfA note does |
| W9 FX result outside SKR03 | **Remains (LOW)** | "Realisierter Gewinn/Verlust" −2.10, 2025-10-01 |
| A2 "Fil. NNNN" suffixes | **Widened** | 198 transactions; R2 saw private rows only, now also 4530 (26), 4650/4654 (11), 4670 (12), 4930 (10) |
| A7 price residue | **Remains** | 24 `transaction`-type price rows, 47 `last` |
| Profit / ESt chain | **Re-fitted** | Gewinn 111,013 → 121,496; 1810 2025 33,558.78 → 35,762.51 with the 2025 Bescheid now a 4,917.21 Nachzahlung and VZ 9,699; still tariff-consistent |
| Postbank story | **Re-seeded** | opening 3,250 → 11,250; sweeps + ESt draws + private card + ETF reconcile to 10,149.95; 1200 floor 8,072.87 → 6,637.11 (2026-09-15) |
| USt-VA, Bankarbeitstage, Sollversteuerung, §13b in/out, Bewirtung, TK, 1%-Regelung, AfA, VMA, private zone, 48.50 hook, entry dates | **Preserved** | see the clean list above |

**New this pass:** W1 (florist VSt rate, €15), H1 (device duplicates —
the persona's only remaining Prüfer conversation), H2 (missing 2024 tail
at cut-over). Priority for the generator: H1, then H2, then W1; the
below-HIGH list is polish.
