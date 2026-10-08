# Cold audit, fourth pass: Alex Chen-Morales demo book (built 2026-09-17, 19:54)

**Verdict: WOULD NOT SIGN** — one return only. The 2025 Form 1040 and all
six WA DOR excise returns now reproduce from the ledger to the cent and are
signable as documented. The 2025 **Seattle** annual B&O return is not: its
method statement measures the exemption threshold against a different
quantity than it taxes, uses one factor where the city statute uses two, and
contradicts the nexus premise the state returns in the same book assert about
the same receipts.

Reviewer stance: IRS-minded CPA reading `samples/alex-chen-morales.gnucash`
(2,152 transactions / 4,928 splits, 2025-01-01 → 2026-09-17, USD, 111
accounts) cold, as a client's combined household + single-member-LLC book.
Read-only, via a scratch copy; figures from the server's Python API
(`get_book_summary`, `list_accounts`, `balance_sheet`, `income_by_source`,
`spending_by_category`, `list_invoices`, `list_transactions`,
`get_transaction`) plus direct ORM reads for split-level detail. R3 read only
after the cold pass.

All four R3 items are closed. Two things survive the pass.

---

## Would not sign

### W1 — The 2025 Seattle B&O return's method statement does not support its number

- Evidence — `49af9428`, 2026-04-30, $327.67 to `Expenses:Business:Taxes &
  Licenses`, note in full: *"2025 Seattle B&O — worldwide gross $182,098.72
  exceeds the $100,000 exemption threshold; 0.427% service rate on the
  Seattle-apportioned base $76,737.50 (service-income factor, SMC 5.45.081 —
  Emerald Analytics, Sound Transit)."* Arithmetic ties: 76,737.50 × 0.00427 =
  327.67; $76,737.50 = the four 2025 quarters' WA-sourced receipts
  (19,950.00 + 22,350.00 + 14,000.00 + 20,437.50), which tie to the ledger
  exactly; 0.427% is the correct Seattle service rate.
- Three defects in the reasoning, not the arithmetic:
  1. **The threshold is tested against the wrong quantity.** The sentence
     clears the $100,000 exemption on *worldwide* gross ($182,098.72) and
     then taxes a base of $76,737.50. Seattle measures the exemption on
     taxable gross income — the apportioned figure, the same $76,737.50 — and
     on that measure the return is *below* threshold. One sentence cannot use
     two different meanings of "gross income" and reach a number.
  2. **Only one apportionment factor is applied.** City service income is
     apportioned on the average of the payroll factor and the service-income
     factor (RCW 35.102.130, the model ordinance SMC 5.45.081 implements).
     Alex is the LLC's only worker and works in Seattle, so the payroll
     factor is 1.00. Even leaving the service factor at the note's implied
     0.4214, the base is (1.00 + 0.4214)/2 × 182,097.65 = $129,418, tax
     $552.61 — not $327.67.
  3. **It contradicts the state returns on the same receipts.** All six WA
     DOR notes now assert *"the LLC has nexus only in Washington, so receipts
     attributable to states and countries where it is not taxable leave the
     denominator"* — that is what drives the state factor to 100%. The city
     formula carries the same not-taxable-elsewhere exclusion. If the premise
     holds for Olympia it holds for Seattle, and the service factor is also
     1.00: base $182,097.65, tax $777.56. The book therefore asserts two
     mutually exclusive nexus positions about the same $105,360 of non-WA
     receipts, in the same year, five months apart in the ledger.
- Why it blocks: the three readings give $0, $553 and $778 for one return and
  the book picks a fourth. The dollars are small; the preparer's exposure is
  signing a jurisdictional formula the working papers contradict.
- Fix: state the Seattle analysis the way the state notes now state theirs —
  payroll factor, service-income factor, whether the city throw-out applies,
  and the threshold measured on the apportioned base. If the demo wants the
  cheapest consistent story, it is the state returns' own premise: factor
  1.00, base $182,097.65, tax $777.56, and the threshold clears on the
  apportioned base for the same reason.

---

## High-severity implausible

### H1 — $6,500 of assets enter the book with no source

- Evidence: `2025-01-02 Owner's contribution — Cascade Code LLC working
  capital`, note *"January working-capital top-up ahead of the Q1 receivables
  lag"* — two splits only: `Assets:Current Assets:Cascade Code LLC Checking`
  +6,500.00 / `Equity:Owner's Contribution` −6,500.00. It is the sole entry
  in that equity account for the whole book (balance −6,500.00 at
  2026-09-17).
- Why implausible: this is a combined household book. Every account the
  household holds cash in is in it — Checking, Savings, Cash, HSA, LLC
  Checking — and `Equity:Opening Balances` (−185,120 plus the five opening
  investment positions) already carries the 2025-01-01 net position. A
  working-capital top-up on 2025-01-02 has to come from one of those
  accounts; none is debited, so household net worth rises $6,500 on the
  book's second day out of nothing, and stays overstated by it through the
  `get_book_summary` trajectory and every `balance_sheet` since.
- The asymmetry is the tell: the eighteen **draws** in the other direction
  ($108,800 in 2025, $71,500 in 2026 YTD) are modelled correctly as LLC
  Checking → personal Checking transfers with nothing through equity or the
  P&L. Only the inbound leg is booked as a capital contribution.
- Ledger check that it is affordable as a transfer: personal Checking closes
  2025-01-02 at $11,190.00 after the mortgage payment and HOA dues, so a
  −6,500 leg leaves $4,690 and never turns the account negative.
- Fix: make the contribution a transfer from `Assets:Current Assets:Checking
  Account`, matching the draws. (Keeping the equity account is fine if the
  source is meant to be outside the book's scope — but then say so, because
  nothing in the book is.)

---

## Re-derivation from the ledger — everything that now ties

### 2025 Form 1040 (MFJ), settlement note `d1a616b6`, 2026-04-15, $2,100.00

| Line | Note | Ledger | |
|---|---|---|---|
| W-2 Box 1 (Robin) | 76,467.47 | gross 87,510.98 − §125 health 3,770.00 − HSA 1,147.64 − 403(b) deferral 6,125.87 = **76,467.47** | ties |
| Withholding | 10,582.79 | 26 payroll `Expenses:Taxes:Federal` rows = **10,582.79** | ties |
| LLC revenue (accrual) | 182,097.65 | `Income:LLC Revenue` 2025 at split quantity (USD) = **182,097.65** | ties |
| FX gain | 467.98 | 9 `Income:Foreign Exchange Gain/Loss` rows 2025 net **−467.98** credit | ties |
| Schedule C expenses | 44,017.12 | 13 `Expenses:Business:*` accounts, each line in the note = its account total to the cent | ties |
| Schedule C net | 138,695.16 | 182,097.65 + 467.98 − 44,017.12 + 50% × meals 293.30 = **138,695.16** | ties |
| Method | accrual, line F | A/R 12/31/25 11,550.00 + EUR 6,150; A/P 2,562.50 — real subledgers across the year-end | ties |
| Schedule B | 1,225.19 / 706.27 | Ally interest 11 rows = 1,225.19; dividends = 706.27; HSA interest 27.58 correctly excluded | ties |
| Schedule D | 2,774.95 | AAPL 64.35 (acq 2023-06-12) + ETH 2,127.65 (2024-02-20) + MSFT 568.95 (2024-03-14) + VBTLX 14.00 (2023-05→2024-09) — all long-term | ties |
| SE tax | 19,597 | 138,695.16 × 0.9235 × 15.3% = **19,597.00**. No OASDI offset is correct: the W-2 is Robin's, the Schedule C is Alex's, and the $176,100 base is per-individual. Additional Medicare correctly absent (82,593 + 128,085 = 210,678 < 250,000 MFJ) | ties |
| Solo 401(k) | 20,000 | employer contribution 2025-12-22 from LLC Checking; limit 20% × (138,695.16 − 9,798.50) = 25,779 | ties |
| QBI | 21,779 | 20% × (138,695.16 − 9,798.50 − 20,000) = **21,779.33** | ties |
| Taxable | 136,791 | 219,869.04 − 9,798.50 − 20,000 − 31,500 − 21,779 = **136,791.54**; $31,500 is the 2025 MFJ standard deduction | ties |
| Income tax | 19,728 | ordinary 134,016.05 → 19,311.53 + LTCG 2,774.95 × 15% = 416.24 = **19,727.77** (dividends taxed as ordinary — conservative, VBTLX's are non-qualified) | ties |
| 1040-ES | 26,642 | 6,659 + 7,974 + 4,831 + 7,178, split each quarter between `Estimated Tax Payments` and `Self-Employment Tax` | ties |
| Balance due | 2,100 | 39,325 − 37,224.79 = **2,100.21**; 90% safe harbor 35,392.50 cleared by 37,224.79, no Form 2210 | ties |

All seven 1040-ES vouchers reconcile to one formula, (projected tax −
withholding) × 90% × cumulative mark: 2025 Q1 6,659 / Q2 cum 14,633 / Q3 cum
19,464 / Q4 cum 26,642; 2026 Q1 3,910 / Q2 cum 8,107 / Q3 cum 14,301.

### The six WA DOR returns, under throw-out with the small-business credit

| Period | Paid | Worldwide gross (note / ledger) | WA-sourced | 1.5% | Credit 2×480−tax | Net |
|---|---|---|---|---|---|---|
| Q1 2025 | 2025-04-30 | 46,057.30 / 46,057.07 | 19,950.00 | 690.86 | 269.14 | **421.72** |
| Q2 2025 | 2025-07-31 | 49,615.49 / 49,615.36 | 22,350.00 | 744.23 | 215.77 | **528.46** |
| Q3 2025 | 2025-10-31 | 41,516.25 / 41,515.80 | 14,000.00 | 622.74 | 337.26 | **285.48** |
| Q4 2025 | 2026-02-02 | 44,909.68 / 44,909.42 | 20,437.50 | 673.65 | 286.35 | **387.30** |
| Q1 2026 | 2026-04-30 | 36,496.93 / 36,496.40 | 16,750.00 | 547.45 | 412.55 | **134.90** |
| Q2 2026 | 2026-07-31 | 43,669.10 / 43,669.04 | 27,937.50 | 655.04 | 304.96 | **350.08** |

Every WA-sourced figure equals Emerald Analytics + Sound Transit revenue for
the quarter, to the cent. Every credit is the RCW 82.04.4451 phase-out at
$160/month × 3 = $480 ($960 − tax), correctly applied because every quarter's
tax falls between $480 and $960. Due dates correct, including Q4 2025 rolled
from Saturday 2026-01-31 to Monday 2026-02-02.

### Also verified clean

- **Amex late-fee episode.** August statement closes 2025-08-22 at $2,031.80,
  due 2025-09-16 (Tue). Late fee $29.00 on 2025-09-17 (Wed, the day after);
  $200 partial payment on 2025-09-18 (Thu), note's carried $1,831.80 =
  2,031.80 − 200 exactly; interest $37.38 at the next close 2025-09-22 (Mon);
  statement cleared in full 2025-09-25. No holiday, no date out of order, no
  note contradicting the ledger.
- **Card interest only where a balance carried.** Chase Sapphire closes on
  the 15th; interest in exactly the five cycles Feb–Jun 2025 with a carry,
  each = 21.49%/12 × (prior close − $500) to the cent (38.10 / 40.41 / 59.10
  / 67.39 / 81.39 = 286.39 = `Expenses:Interest:Credit Card Interest` 2025).
  From 2025-06-23 every payment equals the balance at the prior 15th exactly,
  and no interest follows. Amex: one interest charge on the one carried
  statement; every other cycle's payment equals the 22nd-close balance. Card
  interest lands in `Interest:Credit Card Interest` (personal) and `Business:
  Interest & Card Fees` (Amex $29.00 + $37.38 = 66.38) with no crossover.
- **No bank or cash account below zero at any day-end**, over all 625 days:
  Checking min 5,253.65 (2026-06-25); LLC Checking 7,112.04 (2025-02-06);
  Savings 22,000.00; Cash 350.00; HSA 4,800.00.
- **Sweeps, draws and top-ups all on business days.** All 14 surplus sweeps,
  the accumulated-surplus transfer, all 18 owner's draws and the contribution
  land Mon–Fri and off the 2025–2026 federal holiday list, with the month-end
  rolls visible (2025-05-30, 2025-08-29, 2025-11-28 past Thanksgiving,
  2026-01-30, 2026-02-27).
- **Business/personal separation.** No `Expenses:Business:*` split is funded
  from personal Checking, Savings, Cash or Chase; nothing personal touches
  LLC Checking or the Amex. Travel $3,681.19 and meals $293.30 (2025) are the
  Berlin and Toronto trip charges exactly; meals correctly halved on
  Schedule C.
- Every transaction balances in its own currency; no Imbalance or Orphan
  account exists; card maxima $5,401.49 (Chase) and $3,994.91 (Amex) stay
  inside plausible limits; the retirement market-change rows are pegged to
  the book's own VTSAX price series and their "balance at" figures equal the
  ledger balances to the cent (403(b) 57,788.42 and Solo 401(k) 20,000.00 at
  2025-12-31; 58,226.11 and 19,145.83 at 2026-03-31).

---

## Versus R3 (AUDIT_ALEX_COLD_R3_2026-09-17.md)

| # | R3 item | R4 status |
|---|---------|-----------|
| W1 | Schedule C accrual-basis, method not stated | **Fixed** — settlement note now reads "accrual method, line F, consistent with the A/R and A/P subledgers: LLC revenue $182,097.65 is accrual revenue as invoiced, not customer receipts" |
| W2 | Six WA B&O returns apportion without the throw-out rule | **Fixed** — every note now cites WAC 458-20-19402, states the nexus premise, drives the receipts factor to 100% and pays the credit-net tax. Totals $2,107.94, matching R3's far-end projection (R3's Q4 2025 figure of 387.28 was a cent-level slip; 960 − 673.65 = 286.35 → 387.30 is right) |
| — | R3's parenthetical: "the Seattle return rests on the same sourcing … check it in the same pass" | **Not done → new W1.** The Seattle return was left on the pre-throw-out single-factor base and now contradicts the six state returns |
| H1 | Amex late fee posts 09-01 (Labor Day), before the due date; payment note contradicts its date | **Fixed** — fee 2025-09-17, payment 2025-09-18, both after the 2025-09-16 due date, both business days; carried $1,831.80 now ties to the 08-22 close |
| H2 | Month-end sweeps/draws on Sat/Sun (8 rows) | **Fixed** — all 34 sweep/draw/contribution rows on business days |
| MED | Quarterly B&O cadence with $0 net (DOR would assign annual) | **Moot** — the returns now owe $134.90–$528.46 a quarter; quarterly is the right assignment at $180k gross |
| MED | HSA accumulates only; medical paid from Checking/Chase | **Remains** — 2025 medical $1,705.62, none from the HSA. Legal (pay out of pocket, reimburse later) and not a return issue |
| B9 | Sales discounts expensed rather than netted against receipts | **Remains** — $70.00 (2025) / $280.00 (2026); no tax effect, $1.05 of B&O |
| min | B&O worldwide figures FX-rounded a few cents off the ledger | **Remains** — 46,057.30 vs 46,057.07 etc., summing to 182,098.72 against `Income:LLC Revenue` 182,097.65. Sub-dollar; only worth fixing because the Seattle note repeats the rounded total |
| min | January 2025 Ally interest absent | **Remains** — first credit 2025-02-28; 11 monthly credits in a 12-month year, ≈ $65 short of a real 1099-INT |
| min | Itemizing within ≈ $400 of the standard deduction | **Remains** — mortgage interest 23,894.67 + property tax 4,636.94 + charity 1,140.70 = 29,672.31; with the WA sales-tax table the comparison is inside a few hundred dollars of $31,500 and the working papers should show it |
| — | Owner's contribution booked to equity in a combined book | **New H1** |
| — | No Q4-2024 WA return or 2024 Seattle annual return | **New, minor** — the book opens with LLC Checking $9,800, an Amex balance and A/R, so the LLC traded in 2024; the Q4-2024 excise return (due 2025-01-31) and the 2024 Seattle annual (due 2025-04-30) both fall inside the book's range and neither appears |

Net: R3's two blockers and both high-severity items are closed, and the 1040
now reproduces line by line including the Schedule SE treatment R3 called
correctly (per-individual OASDI base; the W-2 is the spouse's). What is left
is the one return R3 named but did not open — Seattle — plus a $6,500 equity
entry that the book's own draws show the right shape for.
