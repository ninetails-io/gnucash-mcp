# Audit: Alex Chen-Morales demo book — US tax / bookkeeping plausibility

Reviewer stance: IRS-minded CPA reading `samples/alex-chen-morales.gnucash`
(1,940 transactions, 2025-01-01 → 2026-07-18) as if it were a client's
combined household + single-member-LLC book. Read-only; every number
below was pulled from the ledger (server Python API + direct SQLite).
Generator: `scripts/synthetic_book/build_alex.py`.

Severity: **HIGH** = a tax professional would refuse to sign / an
auditor would open an issue; **MED** = reads wrong or self-contradictory
to anyone who does US books; **LOW** = polish. "Fix" names the
generator stream to change.

---

## Findings (illegal or wrong)

### A1 — HIGH — Wash sale on VBTLX, booked as a deductible loss
- Evidence: `Sell 100.0000 VBTLX @ $9.7600` 2025-12-15 relieves the
  opening lot at $10.50/sh → **$74.00 loss** debited to
  `Income:Investment Income:Capital Gains`. Next day,
  `Buy 100.0000 VBTLX @ $9.7700` 2025-12-16 (lot "VBTLX 2025-12-16
  purchase"). Also `DCA VBTLX` $200 on 2025-12-01 and 2026-01-01 — both
  inside the ±30-day window.
- Why wrong: IRC §1091 disallows the loss on substantially identical
  shares repurchased within 30 days; the $74 must be added to basis of
  the replacement lot, not recognized. As entered it is a textbook
  disallowed tax-loss harvest (and the DCA schedule alone would have
  triggered it).
- Fix: `QUARTERLY_TRADES` — either (a) make the 12/15 sale a gain
  (sell from a DCA lot bought cheaper, or sell VTSAX instead), or
  (b) buy a non-identical fund on 12/16 (add a `BND`/`VBIRX`
  commodity) and pause the VBTLX DCA for Jan, or (c) keep the pair and
  add a note "wash sale — loss deferred" with the loss NOT booked to
  Capital Gains (basis-adjust the new lot). (a) is the one-line fix.

### A2 — HIGH — Employee on the books with zero payroll
- Evidence: `employees` table has **Sam Rivera** (id 000001, active,
  no address, rate 0). No transaction anywhere references Sam:
  `Expenses:Business:Contractor Payments` has 0 splits; no wage,
  withholding, employer-FICA, FUTA, WA ESD, WA L&I, WA PFML, or
  `Liabilities:Payroll` account exists; no expense vouchers
  (`invoices.owner_type=5` count = 0).
- Why wrong: an LLC with a W-2 employee must run payroll (Forms 941/940,
  W-2, WA ESD quarterly, WA L&I quarterly). 18 months of an active
  employee with $0 wages is either a phantom employee or unreported
  wages. Sam is the persona's one payroll surface and it is empty.
- Fix (pick one; cheapest first):
  1. **Reclassify Sam as a 1099 subcontractor**: delete
     `create_employee`, add a vendor "Sam Rivera (contract dev)" in
     `run_business`, post monthly bills ~$2,000–3,500 to the existing
     `Expenses:Business:Contractor Payments`, paid from Checking. Add a
     January "1099-NEC filed" memo. Zero new accounts.
  2. **Model real payroll** (if the employee/voucher tools need a live
     subject): new accounts `Expenses:Business:Wages`,
     `Expenses:Business:Payroll Taxes`,
     `Liabilities:Payroll Liabilities:{Federal WH, FICA, FUTA, WA ESD,
     WA L&I, WA PFML}`; a semi-monthly stream in `gen_recurring`
     (gross ~$2,400, employee FICA 7.65%, employer FICA 7.65%, FUTA
     0.6% on first $7,000, WA SUI ~1.2%, L&I ~$0.20/hr, PFML 0.92%
     split ~72/28); monthly EFTPS remittance and quarterly WA ESD/L&I
     payments clearing the liabilities.
  3. Either way, add 2–3 expense vouchers/yr for Sam (conference
     mileage, a laptop bag) so the voucher tool has data.

### A3 — HIGH — Estimated tax is a fraction of the liability; no SE tax, no balance-due
- Evidence (2025): `Income:LLC Revenue` $122,186.21 (USD-valued) +
  `Income:Contractor Income` $42,000 = **$164,186 gross SE income**;
  `Expenses:Business:*` $8,454.13. Net SE ≈ $152K → SE tax ≈ 152,000 ×
  0.9235 × 15.3% ≈ **$21.5K**. Joint AGI ≈ $229K (Robin W-2 $87,511 +
  SE net − ½ SE tax) → federal income tax ≈ $33K. Total ≈ **$54K**.
  Paid: Robin's withholding `Expenses:Taxes:Federal` $10,429.30 + four
  × $4,200 `Expenses:Taxes:Estimated Tax Payments` = **$27.2K**.
  No `Expenses:Taxes:Self-Employment Tax` activity (account exists,
  0 splits). No April-2026 "Form 1040 balance due" transaction, no
  refund, though `TurboTax Home & Business` was bought 2026-03-05.
- Why wrong: ~$27K underpaid with §6654 penalty; 110%-of-prior-year
  safe harbor is not credible for a persona whose 2024 looked the same.
  A CPA reading $4,200/quarter against $164K of Schedule C gross stops
  here.
- Fix: `gen_recurring` estimated-tax block — size quarters from the
  running SE income (≈ $10,500–12,000 each; vary them, Q2 lower, Q3
  catch-up), AND add one `Estimated Federal Tax - 2025 Form 1040
  balance due` ~$3–6K on 2026-04-15 plus a small refund-or-due in
  2027. Keep the account where it is (see D1 — it is correctly
  personal, not under `Expenses:Business`).

### A4 — MED — Business expenses miscategorized / run through the personal card
- Evidence: 12 × `Amazon.com - office supplies` ($713.60) and the
  recategorized `Office Supplies (Amazon)` $89 post to
  `Expenses:Business:Software` on **Chase Sapphire** (personal card);
  `Dell U2725D monitor` $450.60 → `Expenses:Business:Software`
  (equipment, not software); two client trips (`Lufthansa - SEA-BER`,
  `Hotel - Berlin`, `Air Canada - SEA-YYZ`, `Hotel - Toronto`,
  $5,295.79 across 2025–26) post to personal `Expenses:Travel` on
  Chase, with no business meals or per-diem.
- Why wrong: Schedule C line mapping is off (supplies → line 22,
  equipment → §179/de minimis, travel → line 24a). Deductible business
  travel buried in a personal category understates the LLC's expenses
  and overstates household spend; the personal card breaks the
  business/personal boundary the Business Amex exists to hold.
- Fix: `ACCOUNTS` add `Expenses:Business:Office Supplies`,
  `Expenses:Business:Equipment`, `Expenses:Business:Travel`,
  `Expenses:Business:Meals` (50%). `AMAZON_CATEGORIES` "office
  supplies" → Office Supplies on `AMEX`; `MONTHLY_EVENTS` monitor →
  Equipment; `gen_personal_life` client-trip block → `AMEX` +
  Business:Travel, add 2–3 meal lines per trip.

### A5 — MED — WA-mandated paycheck deductions missing (PFML, WA Cares, L&I)
- Evidence: every `Robin's Paycheck (UW Medical)` has exactly Federal,
  Social Security, Medicare, Health, HSA. No WA Paid Family & Medical
  Leave (0.92% × ~72% employee share ≈ $21.60/period), no WA Cares
  Fund LTC premium (0.58% ≈ $18.96), no L&I employee share (~$4–8).
- Why wrong: all three are statutory on a Washington W-2 since 2019
  / 2023; a UW Medicine stub without them is not a Washington stub.
  (No state income tax — correct.)
- Fix: `_paycheck_splits` — add three rate-based deductions to new
  `Expenses:Taxes:WA PFML`, `Expenses:Taxes:WA Cares`,
  `Expenses:Taxes:WA L&I` (and to the SX template).

### A6 — LOW — FICA/federal withholding computed on gross before §125 pre-tax deductions
- Evidence: 2025-01-10 gross 3,269.23; SS 202.69 = 6.2% × 3,269.23.
  Correct FICA wages = 3,269.23 − 145.00 (health) − 44.14 (HSA) =
  3,080.09 → SS 190.97, Medicare 44.66.
- Fix: `_paycheck_splits` — compute `taxable = gross − FIXED_HEALTH −
  FIXED_HSA` and apply SS/Medicare/federal to it.

### A7 — MED — Washington B&O tax (state + Seattle) entirely absent
- Evidence: $164K gross receipts, Seattle-based; `Expenses:Taxes:Sales
  Tax` exists with 0 splits; no DOR/City of Seattle payment anywhere.
- Why wrong: WA B&O (Service & Other Activities, 1.5% under $1M) ≈
  $2,460/yr and Seattle B&O (0.427% over the $100K threshold) ≈
  $700/yr are mandatory filings; a WA CPA looks for them first because
  there is no income tax. Absence is *acceptable for a demo* only if
  documented; it is cheap to add and reads as expertise.
- Fix: `gen_recurring` — quarterly `WA DOR - B&O tax (Q_)` from
  Checking to new `Expenses:Business:Taxes & Licenses` (B&O is a
  deductible business expense, unlike A3), plus an annual Seattle
  B&O/license (~$110 + 0.427%) and the $60 WA SOS LLC annual report in
  the LLC's anniversary month. Rename/retire `Expenses:Taxes:Sales Tax`
  (services aren't sales-taxable here).

---

## Findings (implausible)

### B1 — HIGH — Chase Sapphire is $5,880 over its credit limit and still charging
- Evidence: `credit_limit` slot = 12,000; balance 2025-12-31
  $12,465.39, 2026-07-18 **$17,880.65**, rising ~$500/mo. 2026
  payments are a flat $700 labelled "pay-in-full" while monthly
  charges run $1,000–2,400. Card would be declined; no over-limit or
  interest is booked after May 2025 (`Credit Card Interest` 0 splits in
  2026).
- Fix: `gen_credit_cards` — replace the flat $650/$700 with a
  computed payment: track the running Chase balance across the
  generated streams (or read it back with `get_balance` at statement
  close day 15) and pay the statement balance on the 28th. Same for
  Amex (B2).

### B2 — MED — Business Amex carries $3,671.50 for 10 months with $375 "pay-in-full" and no interest
- Evidence: Amex balance flat at −3,671.50 from 2025-09 through
  2026-06 (PyCon $799.90 + monitor $450.60 + late-cycle residue never
  paid), yet each month's description says "pay-in-full". At 24.49%
  APR that is ~$75/mo of interest never booked.
- Fix: as B1; and if a carried balance is wanted for the debt-payoff
  demo, book monthly interest against `Expenses:Interest:Credit Card
  Interest` and drop "pay-in-full" from the description.

### B3 — MED — LLC and household fully commingled; no owner equity accounts
- Evidence: all LLC receipts land in `Assets:Current Assets:Checking
  Account` (the household account: 29 USD invoice payments +
  17 cross-currency settlements + 13 direct 1099 deposits); Business
  Amex is paid from the same Checking; there is no `Equity:Owner's
  Draw` / `Equity:Owner's Contribution`, no LLC bank account, and the
  chart has no "Cascade Code LLC" anywhere (no account, no slot, no
  memo).
- Why it reads wrong: a disregarded SMLLC is *tax*-legal on one
  Schedule C, but every CPA tells the client to keep a separate LLC
  account and book draws — commingling pierces the liability veil.
  The name "Cascade Code LLC" appearing only in the persona spec, never
  in the ledger, means a reader can't tell there IS an LLC.
- Fix (small): `ACCOUNTS` add `Assets:Current Assets:Cascade Code LLC
  Checking [BANK]`, `Equity:Owner's Draw`, `Equity:Owner's
  Contribution`; route `run_invoice` payments and `gen_contractor_income`
  deposits to the LLC account; pay Business Amex, AWS/WeWork,
  BookkeepingCo, B&O from it; add a monthly `Owner's draw` transfer
  LLC → household Checking (~$8–10K) so the household cash flow is
  unchanged. Estimated taxes stay in the household (A3).

### B4 — MED — Two parallel contractor-revenue streams with no documents behind one of them
- Evidence: `Income:Contractor Income` 13 deposits, $65,200, all on the
  15th, descriptions "…- monthly invoice payment" (CloudNine,
  WinterTech, TechStartup, DataFlow) — but no invoice, customer, or A/R
  exists for any of them, while `Income:LLC Revenue` runs through the
  full invoice module. A reader asks: is this income outside the LLC?
  Unbilled? Under-the-table?
- Fix: `gen_contractor_income` — either make these four real customers
  in `run_business` (invoice + pay through A/R, fold into LLC Revenue),
  or rename the account to something honest like
  `Income:LLC Revenue:Platform payouts (no invoice)` with descriptions
  "Upwork payout" / "Toptal payout" (platforms that pay without the
  contractor invoicing) and drop "invoice" from the text.

### B5 — MED — Umbrella policy with no underlying auto or homeowners insurance
- Evidence: `Umbrella Insurance Premium` $125 quarterly (7 payments);
  `Expenses:Auto:Insurance` 0 splits, `Expenses:Housing:Insurance`
  0 splits. The condo carries a $385K mortgage (lender requires HO-6)
  and the Subaru carries an $18.5K loan (lender requires full
  coverage); an umbrella carrier will not write without both.
- Fix: `gen_recurring` fixed_bills — add `Auto insurance - PEMCO`
  ~$142/mo and `HO-6 condo insurance` ~$58/mo (or semiannual).

### B6 — MED — Idle money earns nothing; bond fund pays nothing; dividends don't scale
- Evidence: Savings grows $22K → **$90,000** with zero interest
  (`Income:Investment Income:Interest` 0 splits; ~$3K/yr of 1099-INT
  missing at 2025 rates). VBTLX (500–890 shares) pays **no monthly
  distribution** for 18 months (it yields ~4%). VTSAX dividends are a
  fixed $68/$73/$79/$84 per quarter while the position grows from 180
  to 786 shares (Dec-2025 payout should be ~$270, not $84). HSA $6.5K
  earns nothing.
- Fix: `DIVIDENDS_PLAN` — compute `shares_held × per_share_rate` at
  each ex-date instead of fixed dollars; add monthly VBTLX
  distributions (~$0.03/sh, reinvested); `gen_recurring` add monthly
  `Savings interest` at ~3.8% APY on the running balance to
  `Income:Investment Income:Interest`; optionally small quarterly HSA
  interest.

### B7 — MED — No retirement plan at all
- Evidence: persona says 401(k); the chart has no 401(k)/403(b)/SEP
  account and no paycheck deferral. A UW Medicine employee is
  auto-enrolled in UWRP (403(b)-style, 5–10% employee + 100% match) or
  PERS; a $164K-SE contractor with $90K idle cash and no SEP-IRA/Solo
  401(k) is leaving ~$25–35K of deduction on the table — a CPA's first
  recommendation.
- Fix: `ACCOUNTS` add `Assets:Retirement:UWRP 403(b)` and
  `Assets:Retirement:Solo 401(k)` (+ commodity or USD); `_paycheck_splits`
  add a 7% deferral + a matching `Income:Employer Retirement Match` /
  asset pair; `gen_recurring` add a December `Solo 401(k) employer
  contribution` from Checking (~$20K). Keeps Robin's HSA at
  $1,147.64/yr (fine; under $4,300) — but see B13.

### B8 — MED — Invoice numbers are not chronological
- Evidence: customer invoice IDs were assigned per-customer stream, not
  by date: #000032 (Nord) is dated 2025-01-12 but #000002 (Emerald) is
  2025-02-01; #000020 (Sound Transit, 2025-02-03) precedes #000003
  (2025-03-01); job invoices #000042–46 are all 2026. Sorted by
  `date_opened`, the sequence reads 1, 32, 2, 20, 3, 25, 33, 4, ….
- Why it reads wrong: out-of-order numbering is the classic sign of
  backdated invoices; it is the first thing a sales-tax or IRS examiner
  checks.
- Fix: `run_business` — build every invoice plan first (Emerald, ST,
  Berlin, Nord, jobs, the "recent open" extras), sort by `date_open`,
  then call `run_invoice` in that order so IDs ascend with dates. Bills
  (vendor numbers) should carry vendor-style IDs, not our counter
  (see B10).

### B9 — LOW — Berlin double-billed in June 2026 with an identical description
- Evidence: invoices #000030 (2026-06-08, EUR 4,500) and #000031
  (2026-06-28, EUR 5,400) both read `Berlin Digital engagement - June
  2026`; the first was paid 07-08, the second is open.
- Fix: `run_business` — give the horizon-anchored extra a distinct
  description (`… — sprint 2` / `— change order`) or skip it when a
  quarterly Berlin invoice already opened in the same month.

### B10 — LOW — Business entities are name-only; terms don't match the story
- Evidence: all 4 customers, 2 vendors, and the employee have
  `addr_name` only — no street, city, country, email, phone, tax ID;
  Berlin GmbH has no VAT number / reverse-charge note; the employee has
  no address or rate. Every document uses "Net 30" although the
  generator comment says Sound Transit is Net 15 and pays in 15 days;
  `2/10 Net 30` is defined and never used. Bill IDs 000001–000008 are
  our counter, but a GnuCash bill ID is the *vendor's* invoice number
  (JetBrains issues numbers like `JB-INV-2025-0112`).
- Fix: `run_business` — pass address/email on `create_customer` /
  `create_vendor` / `create_employee`; use `term="Net 15"` for Sound
  Transit and `"2/10 Net 30"` for Emerald (and let one Emerald payment
  take the discount — exercises `apply_discount`); give bills vendor
  numbers via `create_bill(id=...)` if supported.

### B11 — LOW — Every open document is overdue; reliable payers suddenly stop paying
- Evidence: 7/7 open invoices + 1/1 bill are 31–72 days past due;
  Emerald paid 17 straight retainers at exactly 27 days, then three in
  a row (#000018, #000019, #000045) go 42–72 days unpaid; Sound
  Transit (a public agency) owes $12,000 for 70 days.
- Cause: the book ends 2026-07-18 and today is 09-11 — staleness, not
  design. But a reader of the frozen sample sees a collections crisis.
- Fix: the continuation cadence (monthly rates-cache PR / demo
  updater) — or, in `run_business`, leave only the last 1–2 documents
  open and make the aging window ≤ 20 days at the build horizon.

### B12 — LOW — Property tax is an exact, unchanging $3,200 per half
- Evidence: `King County Property Tax (1st/2nd Half)` 3,200.00 ×3.
  King County bills to the cent, changes every year (levy + assessed
  value), and $6,400 on a $475K condo is ~1.35% — high for Seattle
  (~0.9–1.0%; ~$4,500).
- Fix: `gen_recurring` — per-year amount with cents (e.g. 2025
  $2,318.47 ×2, 2026 +4.1%).

### B13 — LOW — HSA implies an HDHP, but the premium and employer pattern don't
- Evidence: HSA contributions $44.14/period via payroll (fine, $1,147/yr
  ≪ $4,300) but health premium $145/period ($3,770/yr) — UW's CDHP
  (the only HSA-eligible PEBB plan) costs ~$25/mo employee share and
  UW *contributes* $700/yr to the HSA; no employer HSA contribution
  exists. No HSA distributions ever, while $1,777 of `Expenses:Medical`
  is paid from Checking — legal (and a valid "let it grow" strategy),
  but a reader expects at least an occasional HSA debit.
- Fix: `_paycheck_splits` — lower `FIXED_HEALTH` to ~$25–40 if the HSA
  stays, add employer HSA $29.17/period as `Income:Employer HSA
  Contribution` → HSA; in `gen_personal_life` route the March dental
  cleaning and quarterly copays through the HSA (`HSA → Expenses:Medical`).

### B14 — LOW — Holding periods can't be determined for the opening lots
- Evidence: lots `AAPL 2023 purchase`, `MSFT 2024 purchase`,
  `ETH 2024 purchase`, notes "opening position"; MSFT 5 sh sold
  2025-11-18 for $568.95 gain — short- vs long-term depends on a 2024
  date the book doesn't record.
- Fix: `OPENING_LOTS` — put the acquisition date in the lot title/notes
  (`MSFT bought 2024-03-14`) and set `is_closed`/dates accordingly.

### B15 — LOW — Robin's pay is frozen across a calendar year boundary
- Evidence: base gross 3,269.23 and net 2,450.12 identical on all 26
  2025 checks and all 14 2026 checks; UW/SEIU contracts carry a
  ~3–4% July or January step.
- Fix: `gen_recurring` — bump `BASELINE_GROSS` by 3.5% from
  2026-01-09 (and update the SX template).

### B16 — LOW — Overtime withheld at the 22% supplemental rate
- Evidence: `FED_SUPP_RATE = 0.22` on overtime. Overtime paid in the
  regular check is aggregated with regular wages (Pub. 15 §7); the 22%
  flat rate is for separately identified supplemental wages (bonuses).
  The *effect* (more withheld in OT periods) is right; the label is
  not.
- Fix: `_paycheck_splits` — withhold on total gross with a mildly
  progressive marginal step (e.g. 12% to a threshold, 22% above); keep
  the comment honest.

### B17 — LOW — Only a 2025 budget; the report tool errors on today's date
- Evidence: `list_budgets` → "2025 Annual Budget" only;
  `get_budget_report` raises "Today's date is outside the budget
  period range".
- Fix: `run_budget` — create a 2026 budget from the 2025 one (+3%),
  and make the amounts stop being 1.0× the plan (see C4).

### B18 — LOW — Chart clutter that a reviewer trips on
- Evidence: `Expenses:Housing:Mortgage Interest` (0 splits) duplicates
  `Expenses:Interest:Mortgage Interest` (19 splits);
  `Expenses:Taxes:Self-Employment Tax`, `Expenses:Taxes:Sales Tax`,
  `Expenses:Business:Contractor Payments`, `Income:Reimbursements`,
  `Income:Investment Income:Interest`, `Expenses:Insurance:Life`,
  `Expenses:Auto:Insurance/Maintenance`, `Expenses:Housing:Insurance`
  all have 0 splits. ETH sits under `Assets:Investments:Brokerage`
  (a brokerage doesn't custody ETH unless it's Robinhood/Fidelity
  Crypto).
- Fix: `ACCOUNTS` — delete the duplicate mortgage-interest leaf; either
  feed the empty accounts (A2, A7, B5, B6) or drop them; move ETH to
  `Assets:Investments:Coinbase:ETH`.

---

## Generation artifacts

### C1 — Fixed-day document calendar and fixed payment lag
- Emerald opens on the **1st** (19×), Sound Transit the **3rd**,
  BookkeepingCo the **5th**, Berlin the **8th**, Nord the **12th**
  of odd months; contractor deposits on the **15th** (13×). Payments
  land at exactly 27 / 15 / 30 / 30 days after posting, every time
  (36/36 settled invoices). Real clients pay on a distribution
  (15–45 days) and invoice dates slide around weekends.
- Fix: `run_business` / `_berlin_invoice_plan` / `_nord_invoice_plan` —
  jitter `date_open` ±3 business days and `date_pay` by a seeded
  normal (μ = term, σ ≈ 6 days), with one or two late payers per year.

### C2 — The seasonal calendar replays verbatim each year
- `MONTHLY_EVENTS` replay with cents-only jitter:
  `Valentine's dinner - Canlis` 2025-02-14 $165.74 / 2026-02-14
  $165.55; `Ski trip - Snoqualmie` $340.17 / $340.84; `Summer road
  trip - lodging` $890.61 / $890.76; `Byte's annual checkup` $320.10 /
  $320.87; `Anniversary dinner` $225.81 / $225.52; `4th of July party
  supplies` $145.27 / $145.38; the eight holiday-gift lines repeat to
  the recipient. The December gift list is identical both years.
- Fix: `gen_daily_weekly` seasonal block — per-year ±15% amount
  scaling, ±3-day date jitter, and randomly skip/swap ~20% of events
  per year (e.g. 2026 has no ski trip but a different concert).

### C3 — Every invoice line item is dated at build time
- `entries.date` = **2026-07-18 18:51** for all 54 documents whose
  `date_opened` spans 2025-01-01 → 2026-07-12. In GnuCash desktop
  every line reads as typed on one afternoon.
- Status: the server's `_add_entry` on develop now stamps the entry
  with the invoice's `date_opened` (business.py ~L4955), so this
  clears on regeneration. Listed so the fix plan verifies it.

### C4 — Identical amounts, month after month
- `Transfer to savings (monthly surplus sweep)` $4,000.00 ×34 (and the
  monthly count is off — 34 transfers in 18 months means a duplicate
  stream; Savings has 18 splits totalling $90,000 = 22,000 + 17 ×
  4,000, so the "34" is the mirrored Checking leg — fine — but the
  amount never moves); `Surplus sweep to VTSAX` $14,000.00 quarterly;
  `DCA VTSAX` $500.00 / `DCA VBTLX` $200.00 ×19; `Emerald Analytics`
  $3,500.00 ×19; `BookkeepingCo` $450.00 ×8; `AWS Cloud Hosting`
  $125.00 ×19 (AWS bills are never round); `WeWork Coworking` $250.00
  ×19; net pay 2,450.12 ×26; Chase "pay-in-full" 700.00 ×6; Amex
  "pay-in-full" 375.00 ×17.
- Fix: leave HOA/loan/streaming/Chewy flat (they are); jitter AWS
  (usage-based, cents), make sweeps a function of surplus (the
  continuation policy already plans this), retainer stays flat but ST
  project amounts should not cycle `8500/12000/8500`.

### C5 — Day-of-month pile-up
- Transactions per day-of-month: **1st = 190, 15th = 174**, next is
  the 5th at 89. Half the book's structured activity lands on two days.
- Fix: spread DCA to the 3rd/17th, contractor deposits off the 15th
  (B4), utilities off the 15th (`_seasonal` uses day 15 for all three).

### C6 — Machine-shaped descriptions
- `Sell 3.0000 AAPL @ $211.4500`, `Buy 0.500000 ETH @ $3759.47`,
  `Opening position — VTSAX core position`, `DCA VTSAX`,
  `Estimated Federal Tax - Q1 2025`, `Chase Sapphire — Jan 2026
  pay-in-full`. Brokerage statements say "Vanguard — Buy VTSAX" /
  "Coinbase — Sell 1 ETH"; the IRS line is "IRS USATAXPYMT".
- Fix: description templates in `run_investments` / `gen_recurring`
  / `gen_credit_cards`; put the price and share count in `notes`.

### C7 — Cyclic amount lists
- Berlin EUR: 4500/6200/4500/5800/5100/4500 (list of 5, wrapped);
  Nord CAD: 6800/5200/7400/6100/5900/6800/5200/…; Sound Transit
  8500/12000/8500/8500/12000. Round thousands, no cents, repeating —
  hourly consultants bill hours × rate (e.g. 38.5 h × €120 = €4,620).
- Fix: `BERLIN_AMOUNTS` / `NORD_AMOUNTS` / ST plan — derive from
  `hours × rate` with seeded hours; add `quantity`/`price` on the entry
  instead of `quantity=1`.

### C8 — Dividend amounts are fixed per quarter (see B6)
- `DIVIDENDS_PLAN` dollar amounts, not per-share rates — the "68/73/79/84"
  pattern repeats in 2026 against a 4× larger position.

### C9 — Stale-horizon effects visible in the dashboard
- "Book is 55 days behind", 14 overdue schedules (oldest 58 days),
  prices 56 days stale, 8/8 documents overdue. All of these are the
  frozen-sample horizon and clear with the continuation updater; listed
  because a first-time reader sees them before anything else.

### C10 — Reconciliation stops at March 2025
- Checking has 990 unreconciled splits (17 months). Intentional
  (bookkeeper ruling) and realistic for a neglected book — but paired
  with the "55 days behind" banner it compounds the "abandoned book"
  impression. Consider reconciling through the quarter before the
  horizon.

---

## What's right (don't touch)

### D1 — Estimated tax is correctly a personal item, not an LLC expense
- `Expenses:Taxes:Estimated Tax Payments` sits under `Expenses:Taxes`
  (the household tax group with Federal/SS/Medicare/Property), **not**
  under `Expenses:Business`. For a disregarded SMLLC that is the right
  home: the LLC doesn't pay income tax; Alex does. Only the *amount* is
  wrong (A3). Dates are the real IRS deadlines (Apr 15 / Jun 15 /
  Sep 15 / Jan 15) with the Q4 payment correctly in the following
  January.

### D2 — Loan amortization is genuine
- Mortgage 2025-01-01: interest 2,005.21 = 385,000 × 6.25% / 12;
  principal 479.79; balance carried forward every month
  (2026-07-01: 1,958.18 / 526.82; balance 375,443.77). Auto loan
  likewise at 5.49%. Interest correctly hits an expense; principal hits
  the liability. Mortgage interest ($23.9K) is under the $750K
  acquisition-debt cap; property tax + mortgage interest + charity
  (~$32K) means they itemize — consistent.

### D3 — W-2 withholding mechanics
- SS 6.2% / Medicare 1.45% exact; federal withholding progressive and
  larger on overtime checks (e.g. 2026-07-10: gross 3,597.23 → fed
  452.04 vs base 379.88); no state income tax (Washington); health
  premium plausible; HSA payroll contribution $1,147.64/yr under the
  $4,300 self-only limit. Salary belongs to Robin (spouse), which is
  fine for a joint household book.

### D4 — Investment lots and gains
- Every buy opens a lot with cost basis (opening, DCA, sweep, quarterly,
  whole-share buys); sells relieve a specific lot (AAPL 2023 lot 25→22,
  MSFT 2024 lot 15→10, ETH 2024 lot 2.5→1.5, VBTLX opening lot
  500→400); realized gain/loss booked with correct sign (VBTLX loss is
  a debit to Capital Gains). AAPL/MSFT pay cash dividends to Checking,
  VTSAX reinvests — the right behavior for stocks vs. a fund.
  ETH is treated as property with lot basis and a $2,127.65 capital
  gain on the 2025-10-08 sale — correct under Notice 2014-21; no
  wash-sale exposure on crypto (§1091 doesn't reach it as of 2025),
  which is worth a one-line note if A1 is fixed by changing funds.

### D5 — Cross-currency receivables
- EUR and CAD invoices post to per-currency A/R at the on-date rate,
  settle to USD Checking, and the rate drift is recognized in
  `Income:Foreign Exchange Gain/Loss` at settlement (15 entries,
  2025 net +$486.55 gain, 2026 net −$133.82) — realized, cash-basis
  correct. Customer currency, A/R commodity, and invoice currency agree
  on every document.

### D6 — Household texture that reads true
- Seasonal utilities (electric/gas winter peaks, water summer);
  cents-bearing retail spend (81% of consumer splits carry cents);
  the sticky `Vending Machine → Miscellaneous` misrule; the voided
  wrong-vendor payment, the return, the partial refund, the split
  correction, the deleted duplicate; the Amex August late fee + missed-
  cycle interest; TurboTax bought every March; King County property-tax
  dates (Apr 30 / Oct 31) are the real due dates; NAMI / Doctors Without
  Borders / Northwest Harvest are real 501(c)(3)s; gifts are all
  personal (no business-gift $25 issue); dining is all personal (no
  100%-meals issue); no home-office deduction claimed, consistent with
  paying for WeWork.

### D7 — Business spend is mostly on the business card
- AWS, WeWork, PyCon, JetBrains (via A/P) all hit `Business Amex` or
  `Accounts Payable`; the only leakage is the Amazon office-supply and
  travel lines in A4.

---

## Suggested order for a 4-day fix plan

1. **Day 1 — legal/tax correctness**: A1 (one-line trade change),
   A3 (estimate sizing + balance-due line), A2 option 1 (Sam → 1099
   vendor + 2 vouchers), A5/A6 (paycheck splits), A7 (B&O stream).
2. **Day 2 — entity boundary**: B3 (LLC checking + owner draw +
   contribution), A4 (business travel/supplies/equipment accounts),
   B4 (contractor deposits become invoices or platform payouts).
3. **Day 3 — plausibility**: B1/B2 (computed card payments), B5
   (auto/HO-6 insurance), B6 (interest, VBTLX distributions, scaled
   dividends), B7 (retirement), B8 (chronological invoice IDs),
   B10 (addresses, terms).
4. **Day 4 — artifacts**: C1/C2/C4/C5/C7 jitter passes, C6
   description templates, B12/B15/B17, regenerate, confirm C3 cleared,
   rerun `verify()` and the capture rig against the committed oracle.
