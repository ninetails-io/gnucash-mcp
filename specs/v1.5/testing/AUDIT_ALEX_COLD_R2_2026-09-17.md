# Cold audit, second pass: Alex Chen-Morales demo book (built 2026-09-17, 14:51)

**Verdict: WOULD NOT SIGN.**

Reviewer stance: IRS-minded CPA reading `samples/alex-chen-morales.gnucash`
(2,172 transactions, 2025-01-01 → 2026-09-17, USD, 111 accounts) cold, as a
client's combined household + single-member-LLC book. Read-only; figures
from the server's Python API (`get_book_summary`, `income_by_source`,
`spending_by_category`, `balance_sheet`) and direct SQLite on a scratch
copy. Scope is limited to what a CPA would refuse to sign and what is
implausible at HIGH severity; the first cold audit was read only after
the cold pass. Statute citations were checked against the source text
today (RCW 82.04.4451, WAC 458-20-104).

---

## Would not sign

### W1 — TY2025 Form 1040 is not prepared from the books
- Evidence (2026-04-15 `IRS USATAXPYMT` $3,200 → `Expenses:Taxes:Federal`,
  note): "tax $40,424 (income $20,120 + SE $20,303) on W-2 wages $76,467,
  Schedule C net $143,693, Schedule D $2,775; prepaid $37,224 (withholding
  $10,583 + 1040-ES $26,641)". Arithmetic inside the note ties, prepaid
  ties to the ledger (Robin's `Expenses:Taxes:Federal` 2025 = $10,582.79;
  1040-ES 6,659 + 7,974 + 4,831 + 7,177 = $26,641), sign is right, and
  the safe harbor holds (37,224 ≥ 90% × 40,424 = 36,382; each cumulative
  installment clears its 22.5/45/67.5/90% mark). The return's *inputs* do
  not come from the ledger:
  1. **1099-INT / 1099-DIV income omitted.** 2025 `Income:Investment
     Income:Interest` $1,229.29 (Ally, 12 monthly credits) and
     `…:Dividends` $706.24 (AAPL/MSFT cash dividends) are absent from
     the return. Proof: the note's $20,120 income tax reproduces to the
     dollar on wages + Schedule C + Schedule D − ½ SE − Solo 401(k) −
     QBI − $31,500 standard deduction with interest and dividends
     excluded; including them gives ≈ $20,500. (`build_alex.py::
     _household_tax` takes `se_net, wages_box1, ltcg` and nothing else.)
     Income the IRS matches by document, left off the return.
  2. **Schedule C net is a run-rate, not the ledger.** $143,693 =
     worldwide gross $182,093 − $3,200 × 12 (`BIZ_EXPENSE_MONTHLY`).
     The ledger's 2025 Schedule C: revenue $182,092.63 (`income_by_
     source`), business expenses $43,625.96 (`spending_by_category:
     Business`, incl. contractor $29,997.50, travel $3,681.19, coworking
     $3,000, accounting $1,800, B&O $1,019.50 …), FX gain $503.39 → net
     ≈ $138,970 (≈ $138,820 after the 50% meals haircut on $293.30).
     The return overstates Schedule C by ≈ $4,700; SE tax on the ledger
     net is ≈ $19,636, not $20,303.
  3. Net of the two errors the ledger supports a balance due of ≈ $2,100,
     not $3,200 — but the direction is beside the point: a Schedule C
     that does not agree to the books and a 1040 missing two 1099s is
     not a return a preparer signs.
- Fix: feed `_household_tax` the ledger's 2025 P&L (revenue − business
  expense accounts − 50% meals + FX gain, interest and ordinary/qualified
  dividends), and have the note list every line that made the tax.

### W2 — Every WA B&O return is filed and paid without the small-business credit
- Evidence: six `WA DOR — B&O excise tax` payments from LLC Checking:
  Q1 2025 $299.25, Q2 $335.25, Q3 $210.00, Q4 $306.56 (paid 2026-02-02),
  Q1 2026 $251.25, Q2 2026 $419.06 — $1,821.37 total; every note reads
  "service & other activities on $X WA-apportioned gross" and nothing
  else. The 1.5% math ties (19,950 / 22,350 / 14,000 / 20,437.50 /
  16,750 / 27,937.50 × 1.5%) and the WA bases tie to the Emerald +
  Sound Transit invoices by quarter.
- Why wrong: RCW 82.04.4451(2)(a)/(3) (WAC 458-20-104): for a taxpayer
  reporting ≥ 50% under service & other activities, the maximum credit
  is $160 per month of the reporting period since 2023-01-01 — $480 per
  quarter — and "when the amount of tax otherwise due … is equal to or
  less than the maximum credit, a credit is allowed … in the amount of
  the tax otherwise due". Alex's largest quarter is $419.06 < $480, so
  every one of the six returns owes **$0** B&O. My DOR applies the
  credit automatically; a service filer at this size does not write a
  $299.25 check. As booked, $1,821.37 was paid on returns that owe
  nothing, and the annual Seattle line (W-correct, below) sits next to
  quarterly state returns that a DOR reviewer would send back.
- Fix: in the B&O stream, compute `credit = min(tax, 480)` (quarterly;
  `2 × 480 − tax`, floor 0, above it), post nothing when net is $0 and
  say so in a memo/notes-only "return filed, $0 due after small-business
  credit" (or drop the payment rows entirely); keep the $115 license and
  the Seattle line.

---

## High-severity implausible

None. The following were checked and clear at this severity (kept for
the traceability the round-2 brief asked for):

- **Seattle B&O** — 2026-04-30 $327.67; note: "worldwide gross
  $182,093.38 exceeds the $100,000 exemption threshold; 0.427% service
  rate on the Seattle-apportioned base $76,737.50 (service-income
  factor, SMC 5.45.081)". $76,737.50 × 0.00427 = $327.67 exactly; base =
  13 Emerald invoices ($45,500, Seattle 98101) + 3 Sound Transit
  ($31,237.50, Seattle 98104); single-factor is right with no payroll.
  Correct.
- **Subcontractor bills** — every `SR-YYYY-MM` is opened on the last
  business day of the month it names (SR-2025-01 2025-01-31 … SR-2026-08
  2026-08-31); twelve 2025 bills = $29,997.50 = 2025 `Contractor
  Payments`. Cash paid to Sam in calendar 2025 is $27,885.00 (SR-2025-12
  paid 2026-01-19) — that is the 1099-NEC figure; the book makes no
  contrary claim.
- **WA PFML** — 0.657% on every 2025 check, 0.808% on every 2026 check
  (2026-01-09: gross 3,383.65 → $27.34); WA Cares 0.58% both years.
- **Owner's draws** — one transfer per month, LLC Checking →
  Checking (e.g. 2025-10-31 −28,000 / +28,000); `Equity:Owner's Draw`
  gone; `Owner's Contribution` 6,500 and LLC Checking opening $9,800
  make the January 2025 Amex/JetBrains/Sam activity fundable.
- **Card balances and interest** — Chase Sapphire: interest on the
  carried balance at 21.49%/12 to the cent (2,127.38 → 38.10; 2,256.62 →
  40.41; 3,300.11 → 59.10; 3,762.87 → 67.39; 4,545.03 → 81.39), payoff
  2025-06-23 $5,206.13 = the 06-15 statement balance, paid in full
  monthly since; Business Amex: $200 partial on the 08-22 statement,
  $29 late fee 09-01, interest 09-22 $37.38 = 1,831.80 × 24.49%/12,
  catch-up $3,832.29 on 09-25 → 0; both under `credit_limit`.
- **Weekend postings** — no mortgage/HOA/auto-loan/1040-ES/Vanguard/
  payroll/card payment lands on a Saturday or Sunday. The 20 weekend
  ACH-shaped rows left are legitimate: Ally month-end interest, internal
  sweeps/draws, and document *dates* (vendor bills, job invoices).
- **Entry dates** — `enter_date` equals `post_date` on all 2,172
  transactions (4,928 of 4,929 splits stamped 12:00–12:02). Not a tax
  matter; a human book would enter statements in batches days later.
  MED, noted, not a signing issue.

Noted below threshold (MED), one line each:
- `_next_bday` rolls weekends only. ACH items on federal holidays: Sam
  Rivera paid 2025-12-25 and 2026-01-19; mortgage + HOA on 2025-01-01,
  2025-09-01, 2026-01-01; auto loan 2026-09-07; Vanguard buys 2025-02-17,
  2026-01-19, 2026-07-03; card payments 2025-01-20, 2025-05-26,
  2025-11-27, 2026-01-19, 2026-05-25, 2026-06-19 (22 rows).
- With $0 net B&O each quarter (W2) DOR would put the LLC on annual
  filing; the quarterly cadence becomes a second inconsistency once W2
  is fixed.
- HSA still only accumulates ($6,785.74; no distributions) while
  `Expenses:Medical` $1,705.62 (2025) is paid from Checking/Chase.

---

## Versus the first cold audit (AUDIT_ALEX_COLD_2026-09-17.md)

| # | Item | Status |
|---|------|--------|
| A1 | Seattle B&O threshold as deduction | **Fixed** — 0.427% on the full Seattle-apportioned base; note rewritten; $327.67 ties |
| A2 | 1040 settlement sign / arithmetic | **Fixed as filed, remains as prepared** — quarters resized ($26,641), $3,200 balance due is genuine on the model and the safe harbor holds; but the model omits 1099-INT/DIV and uses a run-rate Schedule C → **W1** |
| A3 | SR bills dated before the work month | **Fixed** — opened last business day of the month named |
| A4 | Amex interest/fee in personal accounts | **Fixed** — `Expenses:Business:Interest & Card Fees` $29.00 + $37.38 |
| A5 | 2026 PFML at the 2025 rate | **Fixed** — 0.808% on 2026 checks |
| A6 | HSA earnings as taxable interest | **Fixed** — `Income:Investment Income:HSA Interest` |
| B1 | WA B&O unapportioned | **Fixed** — quarterly bases = Emerald + Sound Transit invoices; superseded by **W2** (credit never taken, all six quarters owe $0) |
| B2 | VBTLX sell/rebuy round trip | **Fixed** — 2025-12-15 sale for cash only; 12-17 is the ordinary $200 DCA |
| B3 | Owner's Draw pass-through | **Fixed** — single transfer per draw |
| B4 | Retirement balances never move | **Fixed** — quarterly `market change` entries (8) against `Retirement Market Change` |
| B5 | HDHP/HSA story incomplete | **Remains** (LOW) |
| B6 | Two BKC bills in Sep 2026 | **Fixed** — one `BKC-2026-0908` |
| B7 | Varying "monthly" donation | **Fixed** — $60, one step to $75 |
| B8 | Dormant $350 cash account | **Fixed** — ATM withdrawals and cash spends, balance $1,109.82 |
| B9 | Sales discounts expensed | **Remains** (cosmetic) |
| C1 | Single reconcile stamp | **Fixed** — per statement month |
| C2 | Void-time = build time | **Fixed** — 2025-03-15T16:45 |
| C3 | Weekend ACH postings | **Fixed** for weekends; **new (MED)**: federal holidays not rolled (22 rows, incl. a contractor paid on Christmas Day) |
| C4 | Plot in statement descriptions | **Fixed** — story moved to `notes` |
| C7 | A/R placement asymmetry | **Fixed** — EUR/CAD A/R are top-level siblings of USD A/R |
| C8 | No employees/vouchers/taxtables | **Remains** (by design) |
| C9 | LLC pre-exists, no opening bank balance | **Fixed** — LLC Checking opens at $9,800 |
| — | 1040 omits 1099-INT/DIV; Schedule C not from the ledger | **New — W1** |
| — | WA small-business B&O credit never applied; six $0 returns paid | **New — W2** |

Net: the first audit's two signing blockers are closed exactly as
scoped, and two new ones took their place — both again one function in
the tax streams (`_household_tax` and the B&O emitter), both invisible
to the ledger's own balancing and to every cross-tool tie, both the kind
of thing only the statute or the 1099 stack would catch. Everything
else a CPA would stop on in this book is fixed.
