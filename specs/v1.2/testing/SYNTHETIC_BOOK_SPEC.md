# Synthetic Book Specification: Alex Chen-Morales

**Purpose:** Create a richly populated GnuCash book that exercises every module and tool in gnucash-mcp. This book serves as:
- A comprehensive integration test dataset
- A stress test for list/search operations at volume
- A demo book for screenshots, videos, and documentation
- A regression test target for future releases

**Operator:** Claude Code, running against the test MCP server instance.

**Book file:** Create a fresh book. Do NOT use the production book.

**Timeline:** January 1, 2025 through December 31, 2025 (one full calendar year).

---

## The Person

**Alex Chen-Morales**, 38, Seattle WA. Software contractor (1099) with an LLC called Cascade Code LLC. Also takes occasional short-term W-2 contracts. Partner **Robin Chen-Morales** works W-2 as a nurse at UW Medical Center.

Two incomes. One complicated (Alex: 1099 + LLC), one simple (Robin: W-2 biweekly).

They own a condo in Capitol Hill with a mortgage. One car with a loan. A cat named Byte. An HSA. A brokerage account Alex actively manages. A European client who pays in EUR.

---

## Phase 1: Commodities

Create before anything else — accounts reference these.

| Mnemonic | Fullname | Namespace | Fraction | Notes |
|----------|----------|-----------|----------|-------|
| VTSAX | Vanguard Total Stock Market Index Fund Admiral | FUND | 10000 | Core holding |
| VBTLX | Vanguard Total Bond Market Index Fund Admiral | FUND | 10000 | Bond allocation |
| AAPL | Apple Inc. | NASDAQ | 10000 | Individual stock |
| MSFT | Microsoft Corporation | NASDAQ | 10000 | Individual stock |
| ETH | Ethereum | CRYPTO | 1000000 | Crypto, 6 decimal places |
| EUR | Euro | CURRENCY | 100 | May already exist in book |
| GBP | Pound Sterling | CURRENCY | 100 | May already exist in book |

**Prices:** Create a monthly price for each commodity on the 1st of each month (Jan–Dec 2025). Use realistic-ish values:

- VTSAX: Start ~$120, end ~$128, gentle uptrend with a dip in Q3
- VBTLX: Start ~$10.50, end ~$10.80, mostly flat
- AAPL: Start ~$190, end ~$220, volatile
- MSFT: Start ~$380, end ~$420, steady climb
- ETH: Start ~$2,400, end ~$3,200, volatile with a spike in Q4
- EUR: 1.08–1.12 range against USD
- GBP: 1.26–1.30 range against USD

---

## Phase 2: Chart of Accounts

Create accounts in this order (parents before children). Mark accounts as placeholder where indicated. Skip any that already exist from the book template.

### Assets
```
Assets [PLACEHOLDER]
  Assets:Current Assets [PLACEHOLDER]
    Assets:Current Assets:Checking Account [BANK]
    Assets:Current Assets:Savings Account [BANK]
    Assets:Current Assets:Cash [CASH]
  Assets:Receivables [PLACEHOLDER]
    Assets:Accounts Receivable [RECEIVABLE]  — for LLC invoicing
  Assets:Investments [PLACEHOLDER]
    Assets:Investments:Brokerage [PLACEHOLDER]
      Assets:Investments:Brokerage:VTSAX [MUTUAL] commodity=VTSAX
      Assets:Investments:Brokerage:VBTLX [MUTUAL] commodity=VBTLX
      Assets:Investments:Brokerage:AAPL [STOCK] commodity=AAPL, namespace=NASDAQ
      Assets:Investments:Brokerage:MSFT [STOCK] commodity=MSFT, namespace=NASDAQ
      Assets:Investments:Brokerage:ETH [STOCK] commodity=ETH, namespace=CRYPTO
    Assets:Investments:HSA [BANK]
  Assets:Fixed Assets [PLACEHOLDER]
    Assets:Fixed Assets:Condo [ASSET] — for tracking home value
    Assets:Fixed Assets:Vehicle [ASSET]
```

### Liabilities
```
Liabilities [PLACEHOLDER]
  Liabilities:Credit Card [PLACEHOLDER]
    Liabilities:Credit Card:Chase Sapphire [CREDIT]
    Liabilities:Credit Card:Business Amex [CREDIT]
  Liabilities:Loans [PLACEHOLDER]
    Liabilities:Loans:Mortgage [LIABILITY]
    Liabilities:Loans:Auto Loan [LIABILITY]
  Liabilities:Accounts Payable [PAYABLE] — for LLC vendor bills
```

Set account slots:
- Chase Sapphire: apr=21.49, credit_limit=12000, statement_close_day=15
- Business Amex: apr=24.49, credit_limit=20000, statement_close_day=22
- Mortgage: apr=6.25
- Auto Loan: apr=5.49

### Income
```
Income [PLACEHOLDER]
  Income:Salary [INCOME] — Robin's W-2
  Income:Contractor Income [INCOME] — Alex 1099, non-LLC direct
  Income:LLC Revenue [INCOME] — Cascade Code LLC invoiced income
  Income:Investment Income [PLACEHOLDER]
    Income:Investment Income:Dividends [INCOME]
    Income:Investment Income:Capital Gains [INCOME]
    Income:Investment Income:Interest [INCOME]
  Income:Reimbursements [INCOME]
```

### Expenses
```
Expenses [PLACEHOLDER]
  Expenses:Housing [PLACEHOLDER]
    Expenses:Housing:Mortgage Interest [EXPENSE]
    Expenses:Housing:HOA [EXPENSE]
    Expenses:Housing:Insurance [EXPENSE]
    Expenses:Housing:Maintenance [EXPENSE]
  Expenses:Auto [PLACEHOLDER]
    Expenses:Auto:Fuel [EXPENSE]
    Expenses:Auto:Insurance [EXPENSE]
    Expenses:Auto:Maintenance [EXPENSE]
  Expenses:Groceries [EXPENSE]
  Expenses:Dining [EXPENSE]
  Expenses:Utilities [PLACEHOLDER]
    Expenses:Utilities:Electric [EXPENSE]
    Expenses:Utilities:Gas [EXPENSE]
    Expenses:Utilities:Water [EXPENSE]
    Expenses:Utilities:Internet [EXPENSE]
    Expenses:Utilities:Phone [EXPENSE]
  Expenses:Insurance [PLACEHOLDER]
    Expenses:Insurance:Health [EXPENSE]
    Expenses:Insurance:Life [EXPENSE]
    Expenses:Insurance:Umbrella [EXPENSE]
  Expenses:Medical [EXPENSE]
  Expenses:Taxes [PLACEHOLDER]
    Expenses:Taxes:Federal [EXPENSE]
    Expenses:Taxes:Social Security [EXPENSE]
    Expenses:Taxes:Medicare [EXPENSE]
```
```
    Expenses:Taxes:Property Tax [EXPENSE]
    Expenses:Taxes:Self-Employment Tax [EXPENSE]
    Expenses:Taxes:Estimated Tax Payments [EXPENSE]
    Expenses:Taxes:Sales Tax [EXPENSE]
  Expenses:Subscriptions [EXPENSE]
  Expenses:Streaming [EXPENSE]
  Expenses:Clothing [EXPENSE]
  Expenses:Pet [PLACEHOLDER]
    Expenses:Pet:Food [EXPENSE]
    Expenses:Pet:Vet [EXPENSE]
  Expenses:Travel [EXPENSE]
  Expenses:Education [EXPENSE]
  Expenses:Gifts [EXPENSE]
  Expenses:Charity [EXPENSE]
  Expenses:Business [PLACEHOLDER]
    Expenses:Business:Cloud Hosting [EXPENSE]
    Expenses:Business:Software [EXPENSE]
    Expenses:Business:Coworking [EXPENSE]
    Expenses:Business:Professional Development [EXPENSE]
    Expenses:Business:Accounting [EXPENSE]
    Expenses:Business:Contractor Payments [EXPENSE]
  Expenses:Interest [PLACEHOLDER]
    Expenses:Interest:Credit Card Interest [EXPENSE]
    Expenses:Interest:Mortgage Interest [EXPENSE]
    Expenses:Interest:Auto Loan Interest [EXPENSE]
  Expenses:Bank Charges [EXPENSE]
  Expenses:Miscellaneous [EXPENSE]
```

### Equity
```
Equity:Opening Balances [EQUITY]
```

---

## Phase 3: Opening Balances (January 1, 2025)

Create transactions dated 2025-01-01 against Equity:Opening Balances.

| Account | Balance | Notes |
|---------|---------|-------|
| Checking | $14,500.00 | Primary operating account |
| Savings | $22,000.00 | Emergency fund |
| Cash | $350.00 | |
| HSA | $4,800.00 | |
| Mortgage | -$385,000.00 | Original $420K, ~3 years in |
| Auto Loan | -$18,500.00 | 2023 Subaru Outback |
| Chase Sapphire | -$2,340.00 | Carried balance |
| Business Amex | -$1,890.00 | Business expenses from Dec |
| Condo | $475,000.00 | Estimated market value |
| Vehicle | $28,000.00 | Estimated value |

Investment opening positions — create as buy transactions on 2025-01-01, create a lot for each, and assign the split:

| Account | Shares | Cost Basis | Lot Title |
|---------|--------|------------|-----------|
| VTSAX | 180.0000 | $21,600.00 | VTSAX core position |
| VBTLX | 500.0000 | $5,250.00 | VBTLX bond allocation |
| AAPL | 25.0000 | $4,750.00 | AAPL 2023 purchase |
| MSFT | 15.0000 | $5,700.00 | MSFT 2024 purchase |
| ETH | 2.500000 | $6,000.00 | ETH 2024 purchase |

---

## Phase 4: Scheduled Transactions

Create these scheduled transactions with appropriate start dates.

### Biweekly

**Robin's Paycheck** (every other Friday starting 01/10/2025, $85K/yr gross):
- Income:Salary → -$3,269.23
- Assets:Current Assets:Checking Account → $2,450.00
- Expenses:Taxes:Federal → $380.00
- Expenses:Taxes:Social Security → $202.69
- Expenses:Taxes:Medicare → $47.40
- Expenses:Insurance:Health → $145.00
- Assets:Investments:HSA → $44.14

### Monthly (all on the 1st unless noted)

| Name | Amount | From | To | Day |
|------|--------|------|----|-----|
| Mortgage Payment | $2,485.00 | Checking | Split (see below) | 1st |
| Auto Loan Payment | $365.00 | Checking | Split (see below) | 5th |
| HOA Dues | $425.00 | Checking | Expenses:Housing:HOA | 1st |
| Electric | $95.00 | Checking | Expenses:Utilities:Electric | 15th |
| Gas Utility | $65.00 | Checking | Expenses:Utilities:Gas | 15th |
| Water/Sewer | $55.00 | Checking | Expenses:Utilities:Water | 15th |
| Internet | $79.99 | Checking | Expenses:Utilities:Internet | 3rd |
| Phone | $140.00 | Checking | Expenses:Utilities:Phone | 12th |
| Streaming Bundle | $45.97 | Checking | Expenses:Streaming | 8th |
| Cloud Hosting (AWS) | $125.00 | Business Amex | Expenses:Business:Cloud Hosting | 1st |
| Coworking (WeWork) | $250.00 | Business Amex | Expenses:Business:Coworking | 1st |
| Pet Food (Chewy) | $48.00 | Checking | Expenses:Pet:Food | 20th |

**Mortgage split** ($2,485/month, 6.25% on ~$385K):
- Jan–Jun: Expenses:Interest:Mortgage Interest $2,006.25, Liabilities:Loans:Mortgage $478.75
- Jul–Dec: Expenses:Interest:Mortgage Interest $1,991.50, Liabilities:Loans:Mortgage $493.50

**Auto loan split** ($365/month, 5.49% on ~$18.5K):
- Jan–Jun: Expenses:Interest:Auto Loan Interest $84.63, Liabilities:Loans:Auto Loan $280.37
- Jul–Dec: Expenses:Interest:Auto Loan Interest $76.20, Liabilities:Loans:Auto Loan $288.80

### Quarterly
| Name | Amount | From | To | Months |
|------|--------|------|----|--------|
| Estimated Tax Payment | $4,200.00 | Checking | Expenses:Taxes:Estimated Tax Payments | Apr, Jun, Sep, Jan(next yr) |
| Umbrella Insurance | $125.00 | Checking | Expenses:Insurance:Umbrella | Jan, Apr, Jul, Oct |

### Yearly
| Name | Amount | From | To | Month |
|------|--------|------|----|-------|
| Property Tax (1st Half) | $3,200.00 | Checking | Expenses:Taxes:Property Tax | April 30 |
| Property Tax (2nd Half) | $3,200.00 | Checking | Expenses:Taxes:Property Tax | October 31 |

---

## Phase 5: Instantiate Scheduled Transactions

Use `create_transaction_from_scheduled` to instantiate every occurrence throughout 2025.

For Robin's paycheck, vary amounts slightly: every 3rd or 4th paycheck, add $200-400 to gross (overtime/shift differential) and adjust tax splits proportionally. Create these variants as direct transactions rather than from the schedule template.

---

## Phase 6: Daily/Weekly Transaction Patterns

Create as individual transactions to build volume and variety. Vary amounts ±20%.

### Weekly patterns (~52 each)
| Vendor | Category | Base Amount | Account | Pattern |
|--------|----------|-------------|---------|---------|
| QFC / Fred Meyer / Safeway (rotate) | Groceries | $85 | Checking | Sat/Sun |
| Gas station (Shell, 76, Safeway Fuel) | Auto:Fuel | $52 | Checking | Varies |
| Coffee shops (various names) | Dining | $5.50 | Chase Sapphire | Weekdays, 5x/week |

### 2-3x per month
| Vendor | Category | Base Amount | Account |
|--------|----------|-------------|---------|
| Various restaurants | Dining | $45-95 | Chase Sapphire or Checking |
| Amazon | Various (rotate categories) | $15-120 | Chase Sapphire |
| Target / Nordstrom | Clothing | $35-150 | Chase Sapphire (quarterly) |

### Monthly one-offs (seasonal flavor)
- **Jan:** Vet visit $180, New Year gift return -$45 (Chase refund)
- **Feb:** Valentine's dinner $165, Ski trip Snoqualmie $340 (Travel)
- **Mar:** Tax prep software $89 (Subscriptions), Spring clothing $210
- **Apr:** Vet annual checkup $320
- **May:** Memorial Day BBQ supplies $95, Garden supplies $67
- **Jun:** Pride festival $120 (Dining) + $85 (Misc), Anniversary dinner $225
- **Jul:** 4th of July party $145, Summer road trip $890 (Travel) + $340 (Fuel, multiple fills)
- **Aug:** New monitor $450 (Business Amex → Business:Software)
- **Sep:** Conference ticket $799 (Business Amex → Business:Professional Development), Labor Day camping $280
- **Oct:** Halloween supplies $65, Vet visit $150
- **Nov:** Thanksgiving groceries $185, Black Friday $420 (spread across 4 transactions)
- **Dec:** Holiday gifts $650 (8 transactions), Holiday travel $580, Year-end charity $500

---

## Phase 7: Alex's Contractor Income

### Direct 1099 income (deposits to checking)
| Period | Client | Monthly Amount |
|--------|--------|---------------|
| Jan–Mar | TechStartup Inc | $4,500 |
| May–Jun | DataFlow Systems | $6,000 |
| Aug–Sep | CloudNine Consulting | $3,800 |
| Nov–Dec | WinterTech Solutions | $5,200 |

Book as: Checking ← Income:Contractor Income. One transaction per month.

### LLC invoiced income (full business module)

**Create billing terms:**
- "Net 15" — due_days=15
- "Net 30" — due_days=30
- "2/10 Net 30" — due_days=30, discount_days=10, discount_percent=2

**Create customers:**
| Name | Currency | Term | Invoice Pattern |
|------|----------|------|-----------------|
| Emerald Analytics | USD | Net 30 | $3,500/month, all 12 months |
| Sound Transit Data Team | USD | Net 15 | $8,500 Feb, $8,500 Mar, $12,000 Jun, $8,500 Oct |
| Berlin Digital GmbH | EUR | Net 30 | €4,500 Mar, €6,200 Jun, €4,500 Sep, €5,800 Dec |

For each invoice: `create_invoice` → `add_invoice_entry` → `post_invoice` → `pay_invoice`.
Berlin Digital pays in EUR — use exchange rate from Phase 1 prices for the conversion.

**Create vendors:**
| Name | Currency | Bill Pattern |
|------|----------|-------------|
| Amazon Web Services | USD | Monthly $125 (Jun: $180, Nov: $210 for traffic spikes) |
| JetBrains | USD | Annual $289, January |
| WeWork | USD | Monthly $250 |
| BookkeepingCo | USD | Quarterly $450 (Mar, Jun, Sep, Dec) |

For each bill: `create_bill` → `add_bill_entry` → `post_invoice` (owner_type=vendor) → `pay_invoice` from Business Amex or Checking.

**Create one employee:**
| Name | Notes |
|------|-------|
| Sam Rivera | Part-time virtual assistant for Cascade Code LLC |

---

## Phase 8: Investment Activity

### Monthly contributions (1st of each month)
- Buy $500 VTSAX: create lot → create transaction → assign split to lot
- Buy $200 VBTLX: same pattern

### Quarterly trades
| Month | Action | Details |
|-------|--------|---------|
| March | Buy 5 AAPL | @ ~$195. New lot. |
| May | Sell 3 AAPL | @ $210. Use `calculate_lot_gain` first. Partial sale from 2023 lot. |
| July | Buy 0.5 ETH | @ $2,800. New lot. |
| August | Buy 10 MSFT | @ $395. New lot. |
| October | Sell 1.0 ETH | @ $3,100. Close original lot + partial new lot. |
| November | Sell 5 MSFT | @ $415. Partial lot sale. |
| December | Sell 100 VBTLX | @ $10.60 (tax-loss harvest). Immediately rebuy (wash sale scenario). |

### Dividends (reinvested — create lot + assign for each)
- VTSAX: Quarterly ~$0.35/share. Mar, Jun, Sep, Dec.
- AAPL: Quarterly $0.25/share. Feb, May, Aug, Nov.
- MSFT: Quarterly $0.75/share. Mar, Jun, Sep, Dec.

For each: Income:Investment Income:Dividends → brokerage account (with quantity for reinvested shares). Create lot, assign split.

---

## Phase 9: Credit Card Lifecycle

### Chase Sapphire
Opens Jan with $2,340 balance. Monthly interest at 21.49% APR on declining balance. Minimum payment $90 + extra payments. **Paid off by June.** Then used as daily driver, paid in full monthly Jul–Dec (no interest).

Monthly interest estimates: Jan ~$42, Feb ~$35, Mar ~$28, Apr ~$20, May ~$12. June: final payoff.

### Business Amex
Running balance for business expenses. Monthly statement payment. Simulate one **late payment in August**: $29 late fee + one month's interest (~$38).

---

## Phase 10: Budget

Create "2025 Annual Budget" with 12 monthly periods.

| Category | Monthly Budget |
|----------|---------------|
| Groceries | $400 |
| Dining | $350 |
| Housing:HOA | $425 |
| Utilities (all) | $450 |
| Auto:Fuel | $220 |
| Streaming | $46 |
| Clothing | $150 |
| Pet (all) | $70 |
| Travel | $300 |
| Business:Cloud Hosting | $150 |
| Gifts | $100 |
| Charity | $50 |
| Miscellaneous | $200 |

Use `set_budget_amount` with period="all" for most, then override:
- Travel: periods 6,7,8 → $600 (summer travel season)
- Gifts: period 11 → $800 (December holidays)
- Charity: period 11 → $500 (year-end giving)

---

## Phase 11: Reconciliation

After all transactions are created, reconcile the checking account for January, June, and December using `get_unreconciled_splits` → `reconcile_account`. Leave other months unreconciled to maintain a realistic mix.

---

## Phase 12: Edge Cases & Corrections

These exercise tools that only matter when things go wrong.

1. **Voided transaction:** Create $500 payment to "Wrong Vendor" on 03/15, void with reason "Paid wrong vendor."
2. **Recategorized transaction:** Create $89 "Office Supplies" to Miscellaneous on 04/20, then `replace_splits` to Business:Software.
3. **Returned purchase:** Buy $249 "Electronics Store" on Chase 08/10, credit -$249 on 08/22.
4. **Partial refund:** Buy $120 "Department Store" on 09/05, refund $45 on 09/15.
5. **Split correction:** Create $150 to Dining on 10/01, then `replace_splits` to $120 Dining + $30 Gifts.
6. **Deleted transaction:** Create duplicate grocery on 11/15, then delete it.
7. **Multi-currency payment:** Berlin Digital pays in EUR with exchange rate conversion.

---

## Phase 13: Volume Stress Test

Programmatically create **1,000 small transactions** in checking:
- Spread across the year (~3/day)
- 10 rotating vendors: "Morning Coffee", "Lunch Spot", "Parking Meter", "Vending Machine", "Corner Store", "Food Cart", "Transit Pass", "Drug Store", "Dry Cleaner", "News Stand"
- Amounts between $1.50 and $15.00
- Categorize to Expenses:Dining or Expenses:Miscellaneous
- Use `check_duplicates=false` for performance

This tests `list_transactions` truncation, `search_transactions` at scale, `get_unreconciled_splits` with hundreds of splits, and `spending_by_category` aggregation.

---

## Execution Notes for Claude Code

1. **Create a fresh book.** The test MCP server must point at a new SQLite file, not the production book.

2. **Order matters.** Follow phases in sequence: commodities → accounts → opening balances → scheduled transactions → instantiate schedules → manual transactions → investments → business → budget → reconciliation → edge cases → volume.

3. **Verify as you go.** After each phase, run `get_book_summary` and spot-check balances. Catch errors early.

4. **Use `check_duplicates=false`** on bulk creates. You are the source of truth for this synthetic data.

5. **Investment splits need quantity.** The split for an investment account needs both `amount` (USD value) and `quantity` (shares). These differ because share price ≠ $1.

6. **Lots are mandatory for investments.** Every buy: `create_lot` → `create_transaction` → `assign_split_to_lot`. Every sell: `calculate_lot_gain` first, then book the sale referencing the lot.

7. **EUR invoices.** Berlin Digital payments need EUR quantity and USD value at the exchange rate on payment date. Use EUR prices from Phase 1.

8. **Don't skip Phase 12.** The edge cases exercise void, unvoid, replace_splits, delete, and refund workflows that are under-tested.

9. **Volume test is last.** It changes performance characteristics. Verify everything else first.

10. **When done, run the full report suite:**
    - `balance_sheet` as of 12/31/2025
    - `net_worth` time series monthly for 2025
    - `cash_flow` for full year
    - `spending_by_category` depth=2 for full year
    - `income_by_source` depth=2 for full year
    - `debt_payoff_plan` at $1,000/month
    - `get_budget_report` period="all"
    - `get_outstanding_invoices` (should be empty)
    - `list_lots` on each investment account
