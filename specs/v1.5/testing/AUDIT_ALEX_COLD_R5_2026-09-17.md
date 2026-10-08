# Cold audit, fifth pass (confirmatory): Alex Chen-Morales demo book (built 2026-09-17, 20:35)

**Verdict: SIGN.** Both R4 items are closed. Nothing on R4's "verified clean"
list regressed. The 2025 Form 1040 moved — correctly, as a downstream
consequence of the H1 fix — and still reproduces from the ledger to the cent,
including the balance due actually paid.

Reviewer stance: IRS-minded CPA, read-only against a scratch copy of
`samples/alex-chen-morales.gnucash` (2,171 transactions / 4,926 splits,
2025-01-01 → 2026-09-17, USD). Figures re-derived directly from `transactions`
/ `splits` / `slots`, not from the prior report.

---

## W1 (R4 blocker) — 2025 Seattle B&O return — **closed**

Transaction `d4a1ce2e`, 2026-04-30 (Thu), `Assets:Current Assets:Cascade Code
LLC Checking` −777.56 / `Expenses:Business:Taxes & Licenses` +777.56. Note in
full:

> 2025 Seattle B&O — worldwide gross $182,098.72 exceeds the $100,000 exemption
> threshold; 0.427% service rate on the full base $182,098.72: payroll factor
> 1.00 (sole worker in Seattle) and service-income factor 1.00 after the
> throw-out of non-nexus receipts (RCW 35.102.130, SMC 5.45.081), consistent
> with the WA DOR returns.

Each of R4's three defects is answered:

1. **Threshold and base are now the same quantity.** Factor 1.00 makes the
   apportioned base equal worldwide gross, so "gross income" carries one
   meaning through the sentence. $182,098.72 > $100,000 either way.
2. **Both statutory factors are stated.** Payroll 1.00 and service-income 1.00,
   average 1.00, with RCW 35.102.130 and SMC 5.45.081 cited — the model
   ordinance's two-factor average, not a single factor.
3. **Nexus premise now matches the state returns.** "after the throw-out of
   non-nexus receipts … consistent with the WA DOR returns." The book asserts
   one position about the $105,360 of non-WA receipts, not two.

Arithmetic and tie-out:

| Check | Result |
|---|---|
| 182,098.72 × 0.00427 | 777.5615 → **777.56**, equals the split |
| Base = the four 2025 quarterly worldwide figures | 46,057.30 + 49,615.49 + 41,516.25 + 44,909.68 = **182,098.72** |
| Due date | Seattle annual return due April 30; 2026-04-30 is a Thursday |
| Funding | LLC Checking, balance 24,315.82 after the payment |

Residual (carried from R4, sub-dollar): the base is the FX-rounded quarterly
sum, $1.07 above `Income:LLC Revenue` 2025 of $182,097.65 — $0.005 of tax.

## H1 (R4 high) — $6,500 owner's contribution — **closed**

Transaction `2cc765fa`, 2025-01-02 (Thu), exactly two legs:

- `Assets:Current Assets:Cascade Code LLC Checking` **+6,500.00**
- `Assets:Current Assets:Checking Account` **−6,500.00**

Note now reads "…transferred from personal checking". No equity leg; the
transfer matches the shape of the eighteen owner's draws in the other
direction. `Equity:Owner's Contribution` now has **zero splits** — assets no
longer enter the book from nothing, and household net worth is no longer
overstated by $6,500 from the book's second day.

---

## Regression check on R4's clean list

| Item | R5 result |
|---|---|
| 1040 settlement ties to the ledger | **Ties** — see below; payment split is exactly $2,072.00 |
| Six WA DOR returns | **All six re-derive**; unchanged from R4 |
| Card interest | **Unchanged and correct** — Chase 5 cycles Feb–Jun 2025 = 286.39; Amex 29.00 + 37.38 = 66.38 |
| No bank/cash account below zero at any day-end | **Holds**, but the Checking minimum moved (below) |
| Business-day ACH | **39 sweep/draw/contribution/ATM rows, none on a weekend or federal holiday** |
| Every transaction balances in its own currency | Holds — zero unbalanced |
| No Imbalance / Orphan account | Holds |
| Business/personal separation | Holds — no personal account funds `Expenses:Business:*`; no personal expense touches LLC Checking or the Amex |
| Card maxima | Chase 5,401.49 (2025-06-21), Amex 3,994.91 (2025-09-17) — identical to R4 |
| Retirement "balance at" notes | 403(b) 57,788.42 / Solo 401(k) 20,000.00 at 2025-12-31; 58,226.11 / 19,145.83 at 2026-03-31 — identical to R4 |
| A/R and A/P at 12/31/25 backing the accrual assertion | A/R 11,550.00 + EUR 6,150; A/P 2,562.50 — matches the note |

**Day-end minima now:** Checking **3,118.46 on 2025-01-22** (was 5,253.65 in
R4), LLC Checking 7,112.04 (2025-02-06), Savings 22,000.00, Cash 350.00, HSA
4,800.00. The Checking trough is the direct, expected consequence of the H1
fix: opening 14,500 less mortgage 2,485, HOA 425, ATM 400 and the 6,500
transfer leaves 4,690 on 01-02, drifting to 3,118.46 by 01-22 and recovering on
the 01-24 payroll. Tight but never negative, and no overdraft fee is implied
anywhere. Acceptable.

### The 1040 moved, and moved correctly

The H1 fix took $6,500 out of personal Checking in January, which shrank the
2025 surplus sweeps into Savings, which lowered Ally interest. Everything
downstream recomputed rather than being patched:

| Line | R4 | R5 | Ledger check |
|---|---|---|---|
| Schedule B interest | 1,225.19 | **1,098.60** | 11 Ally rows Feb–Dec 2025 sum to 1,098.60 |
| Taxable income | 136,791 | **136,665** | 219,742.45 − 9,798.50 − 20,000 − 31,500 − 21,779 = 136,664.95 |
| Income tax | 19,728 | **19,700** | ordinary 133,890.00 → 19,283.80 + LTCG 2,774.95 × 15% = 416.24 → 19,700.04 |
| Total tax | 39,325 | **39,297** | 19,700 + SE 19,597 |
| Balance due | 2,100 | **2,072** | 39,297 − 37,224.79 = 2,072.21; the split pays 2,072.00 |

Unchanged and re-verified: W-2 Box 1 76,467.47 (87,510.98 − health 3,770 − HSA
1,147.64 − 403(b) deferral 6,125.87, each a 26-row payroll total); withholding
10,582.79; Schedule C net 138,695.16 (revenue 182,097.65 at split *quantity* +
FX 467.98 − expenses 44,017.12 + half of meals 293.30), with all thirteen
expense lines equal to their account totals; SE tax 19,597; QBI 21,779; Schedule
D 2,774.95; dividends 706.27; HSA interest 27.58 correctly excluded. All seven
1040-ES vouchers still reconcile to (projected tax − withholding) × 90% ×
cumulative mark. 90% safe harbor 35,367.30 cleared by 37,224.79 — no Form 2210.

### Six WA DOR returns, re-derived

| Period | Worldwide (note) | WA-sourced (note) | Emerald + Sound Transit (ledger) | 1.5% | 960 − tax | Net = split |
|---|---|---|---|---|---|---|
| Q1 2025 | 46,057.30 | 19,950.00 | 10,500 + 9,450 | 690.86 | 269.14 | 421.72 ✓ |
| Q2 2025 | 49,615.49 | 22,350.00 | 10,500 + 11,850 | 744.23 | 215.77 | 528.46 ✓ |
| Q3 2025 | 41,516.25 | 14,000.00 | 14,000 + 0 | 622.74 | 337.26 | 285.48 ✓ |
| Q4 2025 | 44,909.68 | 20,437.50 | 10,500 + 9,937.50 | 673.65 | 286.35 | 387.30 ✓ |
| Q1 2026 | 36,496.93 | 16,750.00 | 7,000 + 9,750 | 547.45 | 412.55 | 134.90 ✓ |
| Q2 2026 | 43,669.10 | 27,937.50 | 10,500 + 17,437.50 | 655.04 | 304.96 | 350.08 ✓ |

---

## Still open (all previously accepted as non-blocking)

- B&O worldwide figures FX-rounded a few cents above the ledger (182,098.72 vs
  182,097.65); the Seattle note now repeats the rounded total as its base.
- `Equity:Owner's Contribution` is now an account with no splits — an empty row
  in the chart. Cosmetic; delete it or leave it.
- January 2025 Ally interest absent (11 credits in a 12-month year).
- No Q4-2024 WA excise return and no 2024 Seattle annual, though both due dates
  fall inside the book's range and the opening balances show 2024 trading.
- HSA accumulates only; 2025 medical paid from Checking/Chase. Legal.
- Sales discounts expensed rather than netted against receipts ($70 / $280).
- Itemized deductions within ~$400 of the $31,500 standard deduction; the
  working papers should show the comparison.
- Card interest is computed on a stated carried balance rather than an average
  daily balance. The notes state the method and the arithmetic ties to the cent
  at 21.49% (Chase) and 24.49% (Amex), both matching the accounts' `apr` slots.

## Verdict

**SIGN.** Every return in the book now states a method its own arithmetic
supports, and the state and city returns assert one nexus position about the
same receipts. No asset enters the book without a source. The 1040 reproduces
line by line and settles for the amount the ledger actually pays.
