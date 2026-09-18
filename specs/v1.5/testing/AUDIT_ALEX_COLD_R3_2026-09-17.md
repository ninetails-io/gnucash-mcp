# Cold audit, third pass: Alex Chen-Morales demo book (built 2026-09-17, 16:35)

**Verdict: WOULD NOT SIGN.**

Reviewer stance: IRS-minded CPA reading `samples/alex-chen-morales.gnucash`
(2,152 transactions, 2025-01-01 → 2026-09-17, USD, 111 accounts) cold, as
a client's combined household + single-member-LLC book. Read-only; figures
from the server's Python API (`get_book_summary`, `list_accounts`,
`balance_sheet`, `income_by_source`, `spending_by_category`,
`list_invoices`, `list_transactions`, `get_transaction`, `get_lot`,
`search_transactions`) and direct SQLite on a scratch copy. Scope: what a
CPA would refuse to sign, plus HIGH-severity implausibility. R2 was read
only after the cold pass. WA statute checked today (WAC 458-20-19402
throw-out; RCW 82.04.4451 / DOR special notice, $160/month service credit).

Both R2 blockers are closed: the 1040 note now reproduces from the ledger
line by line (re-derivation below), and all six B&O returns take the
credit and post $0. What remains is the layer under the arithmetic.

---

## Would not sign

### W1 — Schedule C is accrual-basis and the return does not say so
- Evidence: the settlement note (`0e4791d2`, 2026-04-15) reports revenue
  $182,097.65 = `Income:LLC Revenue` 2025, which GnuCash books at invoice
  posting. The book carries real receivables and payables across the
  year-end: A/R 2025-01-01 $3,500.00 → 2025-12-31 $11,550.00 + EUR 6,150
  (USD 7,154.91); A/P $0 → $2,562.50 (SR-2025-12 $2,112.50 paid
  2026-01-20; BKC-2025-1208 $450). Cash actually received from customers
  into LLC Checking in 2025: **$163,790.72** (Emerald 38,430.00; Sound
  Transit 31,237.50; Nord 26,913.36; TechStartup 23,590.00; Berlin
  17,798.61; WinterTech 10,496.25; CloudNine 8,312.50; DataFlow 7,012.50).
  Cash paid to Sam Rivera 2025: $27,885.00 vs $29,997.50 accrued;
  BookkeepingCo $1,350 vs $1,800.
- Why it blocks: Schedule C line F is not a formality. On the cash
  method (the default for a one-person service business), net profit is
  ≈ $123,700, not $139,930.82 — roughly $5k less tax (SE + income + QBI
  effect). On the accrual method the note is right to the cent, but the
  note, the four 1040-ES notes ("annualized net SE income") and the
  B&O notes never state the method. Either answer is signable; the
  ledger cannot be signed without one.
- Fix: one clause in the settlement note — "Schedule C, accrual method
  (line F), consistent with the A/R and A/P subledgers" — and the same
  word on the first 1040-ES of 2025. (Accrual also keeps the B&O returns
  consistent, which report on invoice date.)

### W2 — All six WA B&O returns apportion by customer location without the throw-out rule
- Evidence: every `WA DOR — B&O excise return` note (Q1 2025 `05ceedba`
  … Q2 2026 `e86d822d`) computes 1.5% on Emerald + Sound Transit
  invoices only, cites RCW 82.04.462, and takes the $160/month credit to
  $0. The WA bases tie to the ledger to the cent (19,950 / 22,350 /
  14,000 / 20,437.50 / 16,750 / 27,937.50) and the credit math is right
  (every quarter ≤ $480 → credit = tax). The customers left out: Berlin
  Digital (DE), Nord Analytique (CA), TechStartup, CloudNine, DataFlow,
  WinterTech — none over $100k/yr, and the only out-of-state presence in
  the ledger is one Toronto client visit (Amex 2025-08-21 → 08-26).
- Why it blocks: WAC 458-20-19402 excludes from the receipts-factor
  *denominator* any income "not taxable in any other state" where part
  of the work was performed in Washington. Alex works in Seattle for
  remote clients; unless the LLC is taxable where each customer sits
  (WA's own nexus test: $100k receipts or physical presence), those
  receipts are thrown out and the factor rises toward 1.0. At the far
  end (everything non-WA thrown out) the six returns owe **$2,107.92**
  (per quarter, tax on worldwide gross less the credit's 2×480−tax
  phase-out: 421.72 / 528.46 / 285.48 / 387.28 / 134.90 / 350.08). At
  the near end (Berlin and Nord "taxable elsewhere" via the visit, the
  four US remote clients thrown out) the factor is ≈ 0.60 and every
  quarter still clears the credit → $0. The returns as documented pick
  the $0 answer without the analysis that decides it. The Seattle return
  (`fc00839e`) rests on the same sourcing and the city model ordinance
  has the same not-taxable-elsewhere cascade; check it in the same pass.
- Fix: add a throw-out sentence to each B&O note stating which
  customers' receipts stay in the denominator and why (nexus fact per
  customer), and let the stream compute the factor from that. If the
  demo wants to keep $0 due, the cheapest consistent story is the
  near-end one above; say it.

---

## High-severity implausible

### H1 — Business Amex late fee posts before the due date, on Labor Day
- Evidence: 08-22 statement $2,031.80; `Business Amex — August 2025
  statement payment` $200.00 on **2025-08-28** (note: "Partial payment
  after the due date"); `Business Amex — late payment fee` $29.00 on
  **2025-09-01** (Labor Day); interest $37.38 on 09-22 = 1,831.80 ×
  24.49%/12 (ties).
- Why implausible: a 08-22 close has a due date around 09-16; a payment
  six days after close is early, not late, and its own note contradicts
  the ledger date. The fee also lands on a federal holiday. The
  interest row is fine (assessed at the next close on the carried
  balance); only the fee's date and the payment note are wrong.
- Fix: date the $200 payment after the due date (e.g. 09-18) and the
  fee on the 09-22 statement with the interest, or drop the fee.

### H2 — External month-end ACH transfers on weekends
- Evidence (all bank-to-bank; Checking is Chase per the ATM rows,
  Savings is Ally per the interest rows): `Transfer to savings (monthly
  surplus sweep)` 2025-05-31 Sat, 2025-11-30 Sun, 2026-01-31 Sat,
  2026-02-28 Sat; `Owner's draw — Cascade Code LLC` 2025-05-31 Sat,
  2025-08-31 Sun, 2026-01-31 Sat, 2026-02-28 Sat (LLC bank unnamed; if
  it is not Chase these are external too). Every other ACH-shaped row
  checked — payroll, IRS 1040-ES (06-16-2025 correctly rolled from the
  Sunday), mortgage, auto loan, HOA, customer receipts, Rivera
  payments, card payments — lands on a business day, and none lands on
  a federal holiday (2025–2026 list checked). Ally month-end interest
  on weekends is normal and not counted.
- Fix: roll month-end sweeps and draws with the same `_next_bday` the
  other streams use.

---

## Re-derivation of the 2025 Form 1040 (MFJ) from the ledger

| Line | Note | Ledger | |
|---|---|---|---|
| W-2 Box 1 (Robin) | 76,467.47 | gross 87,510.98 − §125 health 3,770.00 − HSA 1,147.64 − 403(b) 6,125.87 = **76,467.47** | ties |
| Withholding | 10,582.79 | 26 payroll rows `Expenses:Taxes:Federal` = **10,582.79** | ties |
| Schedule C expenses | 42,781.46 | 13 `Expenses:Business:*` accounts sum **42,781.46**; Taxes & Licenses 175 = SOS 60 + license 115 | ties |
| Schedule C net | 139,930.82 | 182,097.65 + FX 467.98 − 42,781.46 + 50% × meals 293.30 = **139,930.82** (accrual; see W1) | ties |
| Schedule B | 1,237.19 / 706.27 | Ally interest 11 rows = 1,237.19; AAPL/MSFT/VTSAX/VBTLX dividends 24 rows = 706.27; HSA interest 27.58 correctly excluded | ties |
| Schedule D | 2,774.95 | AAPL 64.35 (acq 2023-06-12), ETH 2,127.65 (2024-02-20), MSFT 568.95 (2024-03-14), VBTLX 14.00 (2023-05→2024-09): all long-term | ties |
| SE tax | 19,772 | 139,930.82 × 0.9235 × 15.3% = 19,771.59; W-2 is the spouse's, so no SS-wage offset | ties |
| Solo 401(k) | 20,000 | employer contribution 2025-12-22 from LLC Checking; limit 20% × (139,930.82 − 9,886) = 26,009 | ties |
| QBI | 22,009 | 20% × (139,930.82 − 9,886 − 20,000) = 22,008.96 | ties |
| Taxable | 137,722 | 221,116.70 − 9,886 − 20,000 − 31,500 − 22,009 = 137,721.70 | ties |
| Income tax | 19,933 | ordinary 134,947 → 19,516.35; LTCG 2,774.95 × 15% = 416.24 → 19,932.59 | ties |
| 1040-ES | 26,642 | 6,659 + 7,974 + 4,831 + 7,178 | ties |
| Balance due | 2,479 | 39,704 − 37,224.79 = 2,479.21; 90% safe harbor 35,734 cleared, each cumulative installment on pace | ties |

Also verified clean: no bank/cash account below zero at any day-end
(min: Checking 5,074.65 on 2026-06-25; LLC Checking 7,112.04 on
2025-02-06; Savings 22,000; Cash 350; HSA 4,800); every Sam Rivera bill
dated the last business day of its month, paid 6–48 days later; Chase
Sapphire max $5,401.49 vs $12,000 limit with interest in exactly the
five cycles (Feb–Jun 2025) a balance was carried, each = 21.49%/12 ×
(prior close − $500) to the cent; Business Amex max $3,832.29 vs
$20,000, one interest charge on the one carried statement; owner's
draws never take LLC Checking below $7,112 and total $110,000 in 2025
with nothing routed through the P&L.

---

## Versus R2 (AUDIT_ALEX_COLD_R2_2026-09-17.md)

| # | R2 item | R3 status |
|---|---------|-----------|
| W1 | 1040 not prepared from the books (1099-INT/DIV omitted; run-rate Schedule C) | **Fixed** — every line reproduces from the ledger (table above); balance due $2,479 |
| W2 | WA B&O paid without the small-business credit | **Fixed** — six $0 returns, no payment rows, credit stated per note; superseded by **new W2** (throw-out not applied) |
| MED | Federal holidays not rolled (22 rows) | **Fixed** — no ACH-shaped row on a 2025–2026 federal holiday; H1's late fee on Labor Day is the one holiday posting left, and it is wrong for a different reason |
| — | Weekend ACH postings (R2: "internal sweeps/draws legitimate") | **Reopened as H2** — Chase → Ally sweeps are external; four month-ends land on Sat/Sun |
| — | Amex $200 partial / $29 fee / $37.38 interest (R2: clear) | **New H1** — fee precedes the due date and contradicts its own payment note |
| MED | Quarterly cadence with $0 net (DOR would assign annual) | Remains |
| MED | HSA accumulates only, medical paid from Checking/Chase | Remains |
| B9 | Sales discounts expensed | Remains (cosmetic; net-of-receipts presentation, no tax effect) |
| — | Schedule C accounting method unstated | **New W1** |

Minor, not blocking: the B&O notes' worldwide figures are FX-rounded a
few cents off the ledger by quarter (46,057.30 vs 46,057.07 … summing
to 182,098.72 vs `Income:LLC Revenue` 182,097.65; the Seattle note
repeats 182,098.72); January 2025 Ally interest is absent (first credit
2025-02-28 — ≈ $70 short on Schedule B, a 1099-INT mismatch); itemizing
is within ≈ $400 of the $31,500 standard deduction once WA sales tax is
tabled (mortgage interest 23,894.67 + property tax 4,636.94 + charity
1,140.70 + sales-tax table), worth the preparer's two minutes.

Net: R2's two blockers are closed exactly as scoped and the arithmetic
now ties end to end. The two that replace them are not arithmetic —
one is a missing method election that changes the number by ≈ $16k of
net profit, the other a missing apportionment step that changes six $0
returns by up to ≈ $2.1k — and both are, again, one sentence in a note
away from signable.
