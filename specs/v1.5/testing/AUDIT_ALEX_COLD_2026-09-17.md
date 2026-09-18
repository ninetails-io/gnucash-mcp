# Cold audit: Alex Chen-Morales demo book (built 2026-09-17)

Reviewer stance: IRS-minded CPA reading `samples/alex-chen-morales.gnucash`
(2,328 transactions, 2025-01-01 → 2026-09-17, USD book, 108 accounts) as a
client's combined household + single-member-LLC book. Read-only; numbers
below come from the server's Python API and direct SQLite on a scratch
copy. The 2026-09-11 audit was not consulted until the last section.

Severity: **HIGH** = would not sign; **MED** = wrong or self-contradictory
to anyone who does US books; **LOW** = polish. "Fix" names the generator
(`scripts/synthetic_book/build_alex.py`) change.

---

## Illegal or wrong

### A1 — MED — Seattle B&O computed on receipts *above* the $100k threshold
- Evidence: 2026-04-30 `City of Seattle — B&O tax (annual)` $350.54 →
  `Expenses:Business:Taxes & Licenses`; transaction note: "2025 annual
  Seattle B&O on $182,093.38 gross (over the $100,000 threshold)".
  $350.54 = (182,093.38 − 100,000) × 0.427% exactly.
- Why wrong: SMC 5.45 — the $100k is an *exemption* threshold, not a
  deduction. Over it, the 0.427% service rate applies to all taxable
  receipts → $777.54 (before apportionment). The return as booked is
  under-filed by ~$427.
- Fix: Seattle line = `taxable_gross × 0.00427` (WA-apportioned gross
  if B1 is also fixed); reword the note.

### A2 — MED — TY2025 Form 1040 "balance due" when the ledger produces a refund
- Evidence: 2026-04-15 `IRS USATAXPYMT` $4,372 → `Expenses:Taxes:Federal`,
  note "2025 Form 1040 balance due". TY2025 prepayments already on the
  books: Robin's withholding `Expenses:Taxes:Federal` $10,582.79 +
  1040-ES (2025-04-15 $9,515; 06-15 $10,515; 09-15 $7,790; 2026-01-15
  $9,478) $37,298 = **$47,880.79**.
- Liability from the book's own figures: net SE ≈ $142.9k (the
  generator's Q4 annualization note), Robin Box 1 ≈ $76.4k
  (87,510.98 − 6,158.36 403(b) − 3,770 health − 1,147.64 HSA), interest
  $997, dividends $709, LTCG $2,775, Solo 401(k) $20,000, ½ SE tax
  ≈ $10.1k, QBI ≈ $22.6k, MFJ standard deduction $31,500 → taxable
  ≈ $139.7k → income tax ≈ $20.4k + SE tax ≈ $20.2k = **≈ $40.6k**.
  Overpaid ≈ **$7.3k**. April should be an `IRS TREAS 310 TAX REF`
  deposit, not a $4,372 payment.
- Fix: derive the April settlement as `liability − prepayments` from the
  same annualized figures the 1040-ES stream uses (sign included), or
  size the quarters lower so a balance due is genuine.

### A3 — LOW — Subcontractor bills dated before the month they bill for
- Evidence: every `SR-*` bill is opened at the *prior* month-end for the
  *named* month: `SR-2026-09` opened 2026-08-31, entry "Contract
  development — September 2026", 38.5 h × $65; `SR-2026-01` opened
  2025-12-29, "January 2026", 35.25 h → $2,291.25 expensed to
  `Expenses:Business:Contractor Payments` in **2025** for 2026 work.
- Why wrong: a 1099 contractor bills in arrears; as dated, the LLC is
  paying for hours not yet worked, and the 2025 accrual ($32,288.75)
  diverges from the cash paid in 2025 ($29,997.50 — the 1099-NEC figure)
  by a bill that describes next year's work.
- Fix: open each SR bill on the last business day of the month named
  (or name the prior month in the entry).

### A4 — LOW — Business Amex interest and late fee booked to personal accounts
- Evidence: 2025-09-01 `Business Amex — late payment fee` $29.00 →
  `Expenses:Bank Charges`; 2025-09-22 `Business Amex — interest` $37.61 →
  `Expenses:Interest:Credit Card Interest`, the same account that holds
  Chase Sapphire's $278.71 of personal interest.
- Why wrong: business card interest/fees are Schedule C deductions;
  personal card interest is not. One account for both hides the split.
- Fix: route Amex interest/fees to a `Expenses:Business:Interest & Card
  Fees` leaf (paid from the Amex, as now).

### A5 — LOW — WA PFML withheld at the 2025 rate on 2026 checks
- Evidence: 2026-01-09 `UW Medicine — payroll (Robin)` gross 3,383.65,
  `Expenses:Taxes:WA PFML` 22.23 = 0.657% (2025: 0.92% × 71.43%). The
  2026 premium is 1.13% with a 71.52% employee share → 0.808% → $27.34.
  WA Cares (0.58%) is correct in both years.
- Fix: `_paycheck_splits` — year-keyed PFML rate.

### A6 — LOW — HSA earnings booked as taxable interest
- Evidence: `HSA Bank — interest` $6.33–8.20 quarterly (7 entries) →
  `Income:Investment Income:Interest`, the same account as Ally's
  1099-INT interest ($2,220.13 over the book). HSA earnings are exempt.
- Fix: `Income:Investment Income:HSA Earnings` (or memo-only inside the
  HSA asset).

---

## Implausible

### B1 — MED — WA B&O paid on 100% of gross with 60% out-of-state/foreign clients
- Evidence: `WA DOR — B&O excise tax` notes "service & other activities
  on $46,057.30 gross" (Q1 2025) at 1.5% = $690.86; six quarters total
  $3,933.89. 2025 WA-sourced revenue is Emerald ($42,000) + Sound Transit
  ($31,237.50) = 40% of $182,092; TechStartup (TX), CloudNine (OR),
  DataFlow (CO), WinterTech (MN), Berlin, Montréal are apportioned out
  under RCW 82.04.462 (customer-location for services).
- Not illegal (over-payment), but no WA CPA leaves ~$1,500/yr on the
  table, and the Seattle line (A1) is inconsistent with it.
- Fix: apportion the WA gross to WA customers in the B&O note and amount.

### B2 — LOW — VBTLX sold and rebought one day apart for a $14 gain
- Evidence: 2025-12-15 `Vanguard — Sell VBTLX` 100 sh @ 9.76 from the
  opening lot (basis 9.62; `Capital Gains` −14.00); 2025-12-16 `Vanguard
  — Buy VBTLX` 100 sh @ 9.77; 12-17 the normal $200 DCA. No wash sale
  (gain), no tax purpose, no rebalance — an unmotivated round trip.
- Fix: drop the 12/16 rebuy (sell for cash), or make it a rebalance into
  VTSAX, or a real loss harvest into a non-identical bond fund.

### B3 — LOW — `Equity:Owner's Draw` is a zero-balance pass-through
- Evidence: 34 splits, balance 0.00. Each draw is two same-day
  transactions: `Owner's draw — Cascade Code LLC (June 2026)` LLC
  Checking −28,800 / Owner's Draw +28,800, then `Owner's draw deposit —
  …` Owner's Draw −28,800 / Checking +28,800. Cumulative draws
  ($194,900 through 2026-08) never appear on the balance sheet; only
  `Owner's Contribution` 6,500 does.
- Reads as: an equity account that carries no information. In a
  consolidated household book the draw is a transfer.
- Fix: one transfer `Cascade Code LLC Checking → Checking Account` per
  draw (description "Owner's draw"), or keep the equity leg and drop the
  deposit leg only if the household side is ever split into its own book.

### B4 — LOW — Retirement balances never move with the market
- Evidence: `Assets:Retirement:UWRP 403(b)` 59,460.94 = 38,400 opening +
  contributions to the cent; `Solo 401(k)` 20,000.00 flat since
  2025-12-20 — while VTSAX in the same book went 140.80 → 183.18.
- Fix: quarterly `Fidelity/Vanguard — market change` entries against an
  unrealized-gain income leaf, or hold a priced commodity.

### B5 — LOW — HDHP story is incomplete (carried over)
- Evidence: HSA payroll contribution $44.14/check, but health premium a
  flat `Expenses:Insurance:Health` $145.00 × 44 checks (no 2026 change);
  no employer HSA seed; HSA has only inflows while `Expenses:Medical`
  $3,001.90 is paid from Checking/Chase.
- Fix: premium ~$25–40 for a PEBB CDHP, employer HSA ~$29/check, route
  the dental cleanings and copays through the HSA.

### B6 — LOW — Two BookkeepingCo bills in September 2026
- Evidence: `BKC-2026-0905` "Quarterly bookkeeping review - Q3 2026"
  $450 (paid 09-05) and `BKC-2026-0907` "Bookkeeping review - September
  2026" $450 (open). Cadence since 2025-03 is one $450 bill per quarter.
- Fix: make the open document something that isn't a duplicate (a Sam
  Rivera bill, a JetBrains renewal), or leave the Q3 review itself open.

### B7 — LOW — A "monthly donation" that changes every month
- Evidence: `Monthly donation - Doctors Without Borders` 21 × $51.76–74.95
  → `Expenses:Charity`. Recurring gifts are fixed amounts.
- Fix: flat $60 with one step-up.

### B8 — LOW — Dormant $350 cash account trips the dashboard
- Evidence: `Assets:Current Assets:Cash` one split (opening 350.00);
  `get_book_summary` warns "Critically low cash: Cash at USD 350 (under
  1 day of burn)".
- Fix: a few ATM withdrawals + cash spends, or drop the account.

### B9 — LOW — Sales discounts expensed
- Evidence: `Expenses:Business:Sales Discounts` $350 (5 × $70, 2% of the
  $3,500 Emerald retainer). Convention (and Schedule C line 2) is
  contra-revenue. Server default account; cosmetic.

---

## Generation artifacts

### C1 — Reconciliation stamped in one pass
- Every reconciled split in all 7 reconciled accounts carries
  `reconcile_date = 2026-08-31` regardless of statement month (2,244
  splits). Fix: stamp per statement close (`statement_close_day` slots
  exist on both cards).

### C2 — Void timestamp is the build time
- `Payment to Wrong Vendor (mis-routed)` 2025-03-15, slot `void-time`
  = 2026-09-17T10:34:39 — voided 18 months later. Fix: void within days.

### C3 — Weekend ACH/autopay postings
- 76 ACH-shaped transactions on Sat/Sun (mortgage, HOA, auto loan, card
  payments, Vanguard buys, savings interest, one 1040-ES on Sunday
  2025-06-15 — the due date was Monday 06-16). Fix: roll to next
  business day.

### C4 — Statement-text descriptions explain the plot
- `Business Amex — August 2025 statement (partial, late)`, `… (catch-up)`,
  `Chase Sapphire — May 2025 statement (minimum payment)`, `… (balance
  payoff)`. A bank line says "AMEX EPAYMENT"; the story belongs in
  `notes` (which those transactions leave empty).

### C5 — Micro-purchase noise floor
- ~550 debit-card lines (`Morning Coffee` 75, `Lunch Spot` 65, `Drug
  Store` 65, `Vending Machine` 62, `Transit Pass` 52, `News Stand` 51,
  `Dry Cleaner` 48, `Food Cart` 44, `Parking Meter` 43, `Corner Store`
  42), every amount in $2.00–$15.99. Good texture, visible cap. Also
  `Transit Pass` / `Parking Meter` post to `Expenses:Auto:Fuel`.

### C6 — Day-of-month pile-up (reduced, not gone)
- 1st = 130, 5th = 117, 15th = 111 transactions; next are 105/104.

### C7 — Chart asymmetry
- USD A/R is `Assets:Accounts Receivable` (top level); EUR/CAD A/R sit
  under `Assets:Receivables:`. One placement.

### C8 — No employees, vouchers, or tax tables
- `employees` 0, vouchers 0, `taxtables` 0. Moving Sam to 1099 emptied
  the voucher surface; the tax-table tools have no demo data.

### C9 — LLC pre-exists but its bank account doesn't
- `Business Amex` opens with −1,890 and Sam/JetBrains bills arrive in
  January 2025, yet `Cascade Code LLC Checking` starts from a $6,500
  `Owner's Contribution` on 2025-01-02 with no opening balance.

### C10 — Gift and donation frequencies
- `wedding gift` ×7 and `baby shower gift` ×7 in 20 months; see B7.

---

## What's right

### D1 — Paycheck mechanics
- FICA on §125-reduced wages (2025-01-10: 3,269.23 − 145 − 44.14 =
  3,080.09 → SS 190.97, Medicare 44.66); 403(b) 7% + 100% match; WA
  Cares 0.58%; PFML 0.657% (2025); L&I hours-based (6.40 → 6.96 with
  OT); overtime checks; 3.5% step on 2026-01-09; 26 + 18 checks, all
  Fridays; net-pay arithmetic exact on every check.

### D2 — Loans amortize
- Mortgage 385,000 @ 6.25% (interest 2,005.21 → 1,952.68 by 2026-09;
  principal 479.79 → 532.32; balance 374,381.89); auto 18,500 @ 5.49%;
  `apr` slots match; interest under $750k acquisition-debt cap.

### D3 — Estimated taxes
- Real deadlines with Q4 in January; income/SE portions split; notes
  carry the annualized SE income; paid from the household account (right
  for a disregarded SMLLC); B&O, SOS report, Seattle license under the LLC.

### D4 — Investments
- A lot per purchase with basis; opening lots carry acquisition dates in
  notes; sales relieve specific lots and are all long-term (AAPL
  2023-06-12 → 2025-05-15; MSFT 2024-03-14 → 2025-11-18; ETH 2024-02-20
  → 2025-10-08); realized 2025 gain $2,774.95; stocks pay cash dividends
  scaled to shares (MSFT 0.83 → 0.91/sh), funds reinvest with lots; real
  market prices (AAPL 243.85 on 2025-01-01, ETH 3,353.50, EUR 1.0389);
  ETH under `Coinbase`, treated as property.

### D5 — Entity boundary
- All LLC receipts land in `Cascade Code LLC Checking`; every business
  charge is on `Business Amex` or through A/P and paid from LLC Checking;
  no personal charge on the Amex, no business charge on Chase (TurboTax
  H&B $90 is the only arguable line); Sam Rivera is a vendor with W-9 /
  1099-NEC notes, hours × $65, $29,997.50 paid in 2025.

### D6 — Cross-currency and terms
- EUR/CAD invoices post to per-currency A/R at the post-date rate; FX
  gain/loss recognized at settlement with both rates in the memo (000034:
  1.1634 → 1.1721, $53.51 gain); 2/10 Net 30 discounts taken at 4–9 days,
  exactly 2%; Net 15 for the public agency; vendor-numbered bill IDs;
  addresses, USt-IdNr / NEQ notes.

### D7 — Book hygiene
- Chronological invoice IDs; 0 overdue at the horizon; reconciled through
  2026-08-31; 2025 and 2026 budgets with the natural-signs feature
  stamped; property tax to the cent with a 4.1% step; real 501(c)(3)s;
  every transaction balances; no future-dated entries; seasonal events
  vary year to year (Canlis 162.35 / 149.12; ski 380.65 / 341.16).

### D8 — Balance sheet closes
- A = L + E with `Unrealized Gain/Loss` 18,886.75; `Opening Balances`
  218,180 = 175,320 cash/fixed/liabilities + 42,860 opening lots.

---

## Versus the 2026-09-11 audit

**Fixed** (27 of 29 findings): A1 wash sale (now a $14 gain — leaves B2's
pointless round trip), A2 phantom employee (Sam → vendor; empties the
voucher surface, C8), A3 estimate sizing + SE split + April settlement
(but the settlement's sign is wrong — new A2), A4 business travel/meals/
equipment/supplies on the Amex, A5 WA deductions (2026 PFML rate stale —
new A5), A6 FICA on §125 wages, A7 B&O stream (threshold semantics wrong
— new A1; unapportioned — new B1), B1/B2 card balances computed with
interest and a late episode, B3 LLC checking + owner equity (draw is a
pass-through — new B3), B4 contractor deposits → real customers/invoices,
B5 auto + HO-6 insurance, B6 savings interest / VBTLX distributions /
scaled dividends, B7 retirement accounts (flat balances — new B4), B8
chronological IDs, B9 Berlin double-bill, B10 addresses/terms/bill IDs,
B11 overdue horizon, B12 property tax, B14 lot dates, B15 pay step, B16
OT withholding, B17 2026 budget, B18 chart clutter, C1 document jitter,
C2 seasonal replay, C3 entry dates, C4 flat amounts, C6 descriptions, C7
hours × rate, C8 dividends, C9 stale horizon, C10 reconciliation.

**Remaining:** B13 HDHP premium / employer HSA / HSA never used (now B5);
C5 day-of-month pile-up (190/174 → 130/117/111).

**New:** A1 Seattle B&O threshold math; A2 refund booked as balance due;
A3 SR bills dated before the work month; A4 Amex interest/fee in personal
accounts; A5 2026 PFML rate; A6 HSA interest taxable; B1 WA B&O
unapportioned; B2 VBTLX round trip; B3 draw pass-through; B4 flat
retirement; B6 duplicate BKC bill; B7 varying "monthly" donation; B8 dead
cash account; C1 single reconcile stamp; C2 void-time; C3 weekend
postings; C4 plot-in-descriptions; C7 A/R placement; C8 no
employees/vouchers/taxtables; C9 LLC opening inconsistency.

Net: the two things a CPA would stop on (A1, A2) are both one-line
arithmetic in the tax streams; nothing else rises above polish.
