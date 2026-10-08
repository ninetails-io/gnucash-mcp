# Cross-Model Tax Audit & Realism Evaluation of Synthetic GnuCash Books

**Date:** 2026-09-17  
**Evaluator:** Antigravity (Advanced Agentic Analysis)  
**Target Codebase:** `gnucash-mcp/scripts/synthetic_book/`  
**Sample Books Evaluated:**
1. `alex-chen-morales.gnucash` (`build_alex.py`) — US (Washington / IRS / Seattle)
2. `lin-wei.gnucash` (`build_lin_wei.py`) — China (Shenzhen / SAT / Golden Tax IV)
3. `sabine-brenner.gnucash` (`build_sabine.py`) — Germany (Munich / Finanzamt / DATEV SKR03)

---

## Executive Summary

To serve as credible sample datasets for financial software, synthetic accounting books must satisfy two distinct criteria:
1. **Mathematical & Double-Entry Integrity:** Books must balance, clear suspense accounts, correctly track realized FX gains/losses across multi-currency lots, and tie out reconciliation balances.
2. **Jurisdictional & Statutory Plausibility (The "Tax Audit" Standard):** Financial records must withstand scrutiny under local tax codes, administrative regulations, entity formation rules, and commercial banking practices.

### Overall Readiness Scorecard

| Persona | Jurisdiction / Standard | Accounting Quality | Tax Audit Result | Production Readiness |
| :--- | :--- | :--- | :--- | :--- |
| **Alex Chen-Morales** | US (IRC / WA DOR / Seattle) | 🟢 Flawless (100%) | 🟢 **PASS** (Zero adjustments) | **Ready for Release** |
| **Sabine Brenner** | Germany (EStG / UStG / SKR03) | 🟡 High (88%) | 🟡 **FAIL / ADJUSTED** (GWG disallowance) | **Near-Ready** (Fix hardware generator) |
| **Lin Wei** | China (PRC IIT / VAT / Golden Tax IV) | 🔴 Flawed (55%) | 🔴 **FAIL** (Back-taxes, severe penalties) | **Unready** (Requires structural redesign) |

---

## 1. Case Study: Alex Chen-Morales (`alex-chen-morales.gnucash`)

### Persona Profile
* **Entity:** `Cascade Code LLC` (Single-member LLC in Seattle, WA). Disregarded entity filing Schedule C on Form 1040 (MFJ).
* **Household:** Spouse employed at the University of Washington (W-2 with UWRP 403(b) retirement).
* **Core Activities:** Cloud data engineering consultancy with local enterprise clients (Sound Transit, Emerald Analytics), domestic startups, and foreign clients in Germany (EUR) and Canada (CAD). Subcontracts Sam Rivera ($2.5k–$3k/mo).

### Audit Verdict: PASS (100% Clean)
Alex would pass an IRS audit and Washington Department of Revenue (DOR) audit with **zero adjustments**.

### Key Realism Strengths
1. **Strict Corporate Veil & Banking Separation:**
   * Business revenues and expenses flow exclusively through `Assets:Current Assets:Cascade Code LLC Checking` and a dedicated `Business Amex`.
   * Household living expenses run through personal checking and personal credit cards.
   * Capital transfers from the LLC to personal checking are formally booked as **`Owner's draw`**, avoiding commingling.
2. **Mathematical Precision on Form 1040 (Transaction `0e4791d2`):**
   * **Schedule C Net ($139,930.82):** Gross receipts ($182,097.65) + realized FX gain ($467.98) less expenses ($42,781.46).
   * **IRC § 274(n) Meal Limitation:** Explicitly models the 50% statutory disallowance on business meals ($293.30 spend $\rightarrow$ $146.65 deductible).
   * **Self-Employment Tax (Schedule SE):** $139,930.82 $\times$ 92.35% = $129,226.11 SE base $\times$ 15.3% = **$19,772**, matching down to the dollar.
   * **Section 199A QBI Deduction:** 20% of ($139,931 net $-$ $9,886 half-SE $-$ $20,000 Solo 401k) = 20% of $110,045 = **$22,009**.
   * **Safe-Harbor Tracking:** Quarterly Form 1040-ES payments explicitly calculate safe-harbor thresholds (90% current year) to prevent IRC § 6654 penalties.
3. **State & Local Tax (SALT) Mastery:**
   * **WA DOR B&O Tax:** Properly cites **RCW 82.04.462** (single-factor market sourcing) and applies the Small Business B&O Credit under **RCW 82.04.4451** ($160/mo cap), resulting in $0 liability.
   * **City of Seattle B&O:** Complies with Seattle Municipal Code (**SMC 5.45.081**) for businesses exceeding the $100k worldwide gross threshold, paying 0.427% on the Seattle-apportioned base.
   * **Cryptocurrency:** Specific lot identification on Coinbase ETH sales with capital gains reported onto Schedule D.
4. **Audit Defense Posture:**
   * Avoids high-audit-risk deductions: rents a coworking desk ($250/mo) instead of claiming the contentious IRC § 280A home office deduction, and excludes vehicle expenses from Schedule C.

---

## 2. Case Study: Sabine Brenner (`sabine-brenner.gnucash`)

### Persona Profile
* **Entity:** Freiberuflerin (Liberal professional / freelance creative director under § 18 EStG in Munich-Schwabing).
* **Accounting System:** Full **DATEV SKR03** double-entry chart of accounts.
* **Core Activities:** Graphic design, branding, and copyright licensing for Bavarian corporate clients, with international software subscriptions and foreign clients.

### Audit Verdict: FAIL / SUBSTANTIAL REASSESSMENT
Sabine's operational bookkeeping is pristine, but a German tax auditor (*Betriebsprüfer*) will disallow **€15,738.17** of low-value asset write-offs, assess retroactive income tax, recalculate input VAT, and assess statutory late-payment interest (§ 233a AO).

### The Critical Bug: The "GWG" Trap (§ 6 Abs. 2 EStG)
Account `Aufwendungen 2/4:Abschreibungen:4855 Sofortabschreibung GWG` contains 34 transactions totaling €15,738.17 that violate German tax law:
1. **Fehlende selbständige Nutzbarkeit (Lack of Independent Usability):**
   * Under **§ 6 Abs. 2 Satz 2 EStG**, an asset is only eligible for instant write-off as a GWG if it is capable of independent use.
   * Under settled Federal Fiscal Court case law (**BFH BStBl II 2004, 958**) and **EStR 6.2**, computer peripherals (computer mice, keyboards, webcams, monitor arms, docking stations, external storage) are **Peripheriegeräte** and **not** independently usable. They must be capitalized as part of the computer workstation or depreciated across useful life.
2. **Nonsensical Synthetic Valuations:**
   * Due to decoupled generation logic in `build_sabine.py`, the generator pairs generic hardware labels with broad uniform price distributions:
     * Mouse: **€596.61 net**
     * Keyboard: **€615.21 net**
     * Webcam: **€597.44 net**
     * Monitor Arm: **€531.20 net**
     * Ring Light: **€596.24 net**
   * Paying €600 for a mouse or keyboard every quarter will be treated as an immediate audit red flag for fictitious expenses or disguised private consumption (*verdeckte Entnahme*).

### What Sabine Got Right (The Strengths)
* **Monthly USt-Voranmeldungen:** Meticulous monthly filings clearing accounts `1776`, `1771`, `1576`, `1571`, `1787`, `1577` with accurate *Kennziffern* (Kz. 81, 86, 46, 47, 66).
* **Copyright Tax Split:** Splits design fees (19% VAT, `8400`) from copyright usage rights (**7% VAT**, `8300`) under **§ 12 Abs. 2 Nr. 7c UStG**.
* **1%-Regelung for Company Car:** Perfectly implements **UStAE 15.23**, recognizing 80% of the €320 monthly value is subject to 19% VAT (`8924`) and 20% is non-taxable (`8920`).
* **Bewirtungskosten:** Follows **§ 4 Abs. 5 Nr. 2 EStG** with 100% input VAT deduction, 70% deductible net (`4650`), 30% non-deductible net (`4654`), and complete memo substantiation.
* **Per Diems:** Complies with **§ 9 Abs. 4a EStG** per diems booked against `1890 Privateinlagen`.

---

## 3. Case Study: Lin Wei (`lin-wei.gnucash`)

### Persona Profile
* **Entity:** Freelance Senior Software Consultant / Sole Proprietor in Shenzhen, Guangdong, China.
* **Household:** Spouse employed at Shenzhen People's Hospital (W-2 equivalent salary with statutory social security and housing provident fund). One part-time employee (Chen Yu).
* **Core Activities:** Cross-border e-commerce development, mobile architecture contracts for domestic tech giants (Tencent, DJI, SF Tech, ByteDance), and overseas retainers (US, Germany).

### Audit Verdict: FAIL (Severe Penalties & Back-Taxes)
Lin Wei would fail a Chinese tax audit (*国家税务总局 / 深圳市税务局稽查*). While the generator implemented advanced tax arithmetic, the underlying transactional structure violates core PRC regulatory frameworks.

### Major Audit Pitfalls
1. **Severe Co-Mingling of Funds (*公私不分 / 私户收款*):**
   * Lin Wei operates with **zero corporate/business bank accounts** (*无对公基本户*). All revenue from mega-enterprises (Tencent, SF Tech, DJI) and overseas clients is wired directly into her personal bank debit card (`银行储蓄卡`) or personal WeChat/Alipay accounts.
   * Under **Golden Tax Phase IV (金税四期)** and PBOC/Tax Big Data cross-matching, continuous large-sum corporate transfers into personal cards trigger automatic Anti-Money Laundering (AML) and tax evasion investigations.
2. **Income Misclassification: "承包收入" vs. "劳务报酬所得":**
   * Lin Wei books ¥386,000 in domestic tech consulting as `收入:承包收入`, rolls it into `经营所得` (Individual Business Income), and applies the small-business 50% tax reduction (≤ 2M RMB).
   * **Auditor Reclassification:** Under PRC Individual Income Tax Law, contracting as an individual without a formal B2B registered corporate structure is legally **劳务报酬所得 (Remuneration for Personal Services)**.
   * Personal service income is taxed at progressive rates of **20% to 40%** (with only a flat 20% statutory deduction) and is **ineligible** for the 50% small-business halving. This error triggers immense back-taxes, 0.05%/day late interest (*滞纳金*), and non-withholding penalties.
3. **Cross-Border VAT Exemption Non-Compliance:**
   * Export services to US and German clients were treated as 0% VAT.
   * Under **Caishui [2016] No. 36** and **SAT Announcement [2016] No. 29**, cross-border service VAT exemption is **not automatic**. It requires a formal cross-border exemption filing (*跨境应税行为免税备案*), written foreign contracts, proof of foreign consumption, and foreign currency bank settlement slips (*涉外收入申报单*). Furthermore, personal accounts cannot bypass SAFE (*国家外汇管理局*) trade settlement controls.
4. **Expense Deductibility (*税前扣除凭证缺失*):**
   * Lin Wei deducted ¥93,794.30 in business expenses (coworking, travel, equipment) paid via personal credit cards. Without official tax invoices (**发票**) bearing a registered enterprise tax ID, all deductions will be disallowed.
5. **Payroll Double-Entry Glitch:**
   * On assistant Chen Yu's monthly salary (¥3,500 wage, ¥525 employer social security), the transaction note states *"代扣个人社保 ¥360.50"*, but the bank payout is ¥4,025 (gross wage + employer share). The employee's personal share was never deducted from the net payment.

---

## 4. Actionable Generator Code Fixes

### A. Fixes for `build_sabine.py`

#### Bug 1: Decoupled Hardware Pricing & Peripheral GWG Routing
* **Location:** `build_sabine.py:853` (`_hardware()`) and `build_sabine.py:1024` (`BUSINESS` table).
* **Problem:** Any hardware with net price > €250 and ≤ €800 is automatically routed to `GWG` (`4855`), while item names are chosen randomly from a pool containing non-independent peripherals (mice, keyboards, cables) paired with random amounts between €25 and €780.
* **Fix:**
  1. Restrict `GWG` (`4855`) strictly to assets that are **selbständig nutzungsfähig** (e.g., printers, standalone graphics tablets, standalone cameras).
  2. Peripherals (mice, keyboards, monitor arms, webcams, docks, cables) must route to `Aufwendungen 2/4:verschiedene Kosten:4985 Werkzeuge und Kleingeräte` (if $\le$ €250) or be capitalized to `0027 EDV-Software / Hardware`.
  3. Tie realistic price ranges to specific item categories (e.g., mice €40–€120, keyboards €80–€220, SSDs €100–€250) rather than picking from a uniform €25–€780 distribution.

```python
# Recommended adjustment for build_sabine.py:
INDEPENDENT_GWG = {
    "Drucker Etiketten": (150, 350),
    "Farbkalibrierungsgerät": (260, 450),
    "Grafiktablett (Stand-alone)": (400, 750),
}

PERIPHERALS = {
    "Maus": (35, 120),
    "Tastatur": (70, 190),
    "Webcam": (60, 180),
    "Monitorarm": (80, 220),
    "Ringlicht": (45, 130),
    "USB-C Dock": (120, 280),
    "Externe SSD 2 TB": (110, 210),
}
```

---

### B. Fixes for `build_lin_wei.py`

#### Bug 1: Commingling of Funds & Banking Architecture
* **Location:** `build_lin_wei.py:200–350` (Account definitions).
* **Problem:** Only personal retail accounts (`银行储蓄卡`, `微信支付`, `支付宝`) exist.
* **Fix:**
  * Introduce an enterprise basic checking account: `资产:流动资产:招商银行企业对公账户` (Corporate Basic Deposit Account).
  * Route domestic enterprise client invoices (Tencent, SF Tech, DJI) and agency bills (Boyuan Bookkeeping, Ucommune) through the corporate account.
  * Pay Lin Wei's living expenses by transferring money from the corporate account to her personal account, booked through `所有者权益:业主提款` (Owner's Draw / 业主分配).

#### Bug 2: "承包收入" Classification
* **Location:** `build_lin_wei.py:359` (`CONTRACTOR = "收入:承包收入"`).
* **Problem:** Using "承包收入" for commercial IT consulting invites aggressive tax reclassification to 劳务报酬 (20%–40%).
* **Fix:**
  * Reclassify this account to `收入:技术服务收入` or consolidate under `收入:个体经营收入`.
  * Ensure Lin Wei is registered as an official **个体工商户** or **个人独资企业** so that all contract work legitimately falls under `经营所得` rather than personal labor remuneration.

#### Bug 3: Chen Yu Payroll Split
* **Location:** `build_lin_wei.py:1484–1488`.
* **Problem:** Transaction splits credit `CHECKING` for `-(ASSISTANT_WAGE + er_social)` while expensing `ASSISTANT_WAGE` and `er_social`. The employee's personal deduction (`emp_social`) is not accounted for in the split.
* **Fix:**
```python
# Correct split:
net_wage = ASSISTANT_WAGE - emp_social
txns.append({
    "description": f"{ASSISTANT} 工资发放及社保代扣代缴",
    "date": pay_day,
    "notes": f"应发工资 ¥{ASSISTANT_WAGE}，代扣个人社保 ¥{emp_social}，实发 ¥{net_wage}；单位社保 ¥{er_social}",
    "splits": [
        (EXP_BIZ_WAGES, ASSISTANT_WAGE),             # Gross Wage Expense (+3500)
        (EXP_BIZ_SOCIAL, er_social),                # Employer Social Expense (+525)
        (CHECKING, -net_wage),                      # Net pay transfer to employee (-3139.50)
        (CHECKING, -(emp_social + er_social)),      # Direct debit to Social Security Bureau (-885.50)
    ],
})
```

---

## Conclusion & Cross-Model Takeaway

* **Claude's Generator Architecture** demonstrated world-class comprehension of statutory formulas: German UStAE 15.23 car calculations, US IRC § 199A QBI mathematics, Washington RCW B&O apportionment, and Chinese small-scale VAT / individual business income brackets are coded with extraordinary fidelity.
* **Where Cross-Model Scrutiny Added Value:**
  1. **Legal Categorization vs. Raw Numbers:** Catching that German tax law forbids peripheral devices from being classified as GWG regardless of their price.
  2. **Economic Plausibility:** Spotting €600 mice and keyboards produced by decoupled RNG loops.
  3. **Operational Mechanics:** Recognizing that in China and the US, running hundreds of thousands of dollars of commercial B2B contracts through a personal debit card without corporate accounts destroys audit credibility.
* **Next Steps:**
  1. Release **Alex Chen-Morales** immediately as the premier US sample dataset.
  2. Apply the **`_hardware()` / peripheral fix** to `build_sabine.py` to make Sabine publication-ready.
  3. Refactor `build_lin_wei.py` with corporate account separation and payroll split corrections before public release.
