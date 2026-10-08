# Cold audit, round 2 — `samples/lin-wei.gnucash` (built 2026-09-17 14:52)

**Verdict: FAIL as a native reader — narrowly.** The tax engine, payroll, cards, loans, trades and schedules now hold up line by line; what breaks trust is two balances that cannot exist and sit on the face of the dashboard: `资产:流动资产:支付宝` at **−4,131.93** today (negative for 165 days) and `资产:流动资产:银行储蓄卡` overdrawn to **−18,014.03** for a week in November 2025 with the mortgage autodebit landing inside the overdraft. Fix those two and this book passes at this scope.

Stance: mainland household + 个体工商户 bookkeeper. Read-only: server Python API (`get_book_summary`, `list_accounts`) plus sqlite3 on a scratch copy; balances re-derived from split quantities. 2,899 ledger transactions + 21 SX templates, 2025-01-01 → 2026-09-17. The first cold audit (L1–L9, P1–P13, A1–A8) was read only after the cold pass (section 4).

---

## 1. Illegal or impossible

**R1 (H) — `支付宝` runs negative for months; a prepaid wallet cannot.**
Evidence (running balance by quantity, opening first): first dip 2025-03-01; negative day-ends in 12 of 21 months; continuously negative since late May 2026; minimum **−5,942.28 on 2026-08-02**; today −4,131.93 (dashboard: `支付宝: CNY -4131.93`). Top-ups are a fixed `充值支付宝 4,000` on the 2nd of each month while Alipay spend (盒马, 美团外卖, 自动贩卖机, 宠物, 服装) averages more. Account type BANK under 流动资产, so nothing here can be read as 花呗 credit.
Fix: size the monthly 充值 to the prior month's Alipay spend (or top up whenever the balance would go below ~500, as a real user does), and assert `min running balance ≥ 0` for 支付宝 and 微信支付 at build time. (微信支付 is fine: minimum 1,145.14.)

**R2 (H) — `银行储蓄卡` (a debit card) is overdrawn 2025-11-18 → 11-24, minimum −18,014.03.**
Evidence: day-end balance 2025-11-14 10,884.60 → 11-18 `买入 3000 510300 @ ¥4.68` −14,040.00 and `房贷还款` −14,800.00 the same day → −17,978.23; 11-19 −18,014.03; 11-21 华为云 +17,000 → −1,014.03; 11-24 工商 card payment → −3,165.53; positive again only on 11-28 (retainer +12,000), then `储蓄账户转入（补足日常余额） +34,208.82` on 11-30. A 储蓄卡 has no overdraft; the mortgage debit would have bounced (逾期, 征信), and the 14,040 ETF purchase would never have filled. No other negative day on this account in 21 months — the discretionary 11-18 buy is the cause.
Fix: gate every discretionary buy (`买入 … @`, `结余投资`) on the checking balance after the month's fixed debits, or move the 补足 transfer to the day it is needed rather than month-end. Assert `min running balance ≥ 0` on 银行储蓄卡.

Nothing else at this level. Specifically checked and clean: VAT months/exemption/专票 rule, 经营所得 cumulative prepayments and 汇算清缴, 社保/公积金 bases, 陈宇 payroll, card limits, HKD 购汇, 印花税, `last_occur` (details in section 3).

---

## 2. High-severity implausible

**R3 (H→M) — Client trips are booked as personal `支出:旅行`, so the tax engine never sees them.**
Evidence: `出差 美国 Pacific Trade (机票+酒店)` 6,696.00 (2025-04-11) and 6,701.20 (2026-02-09); `出差 德国 Handelskontor München (机票+酒店)` 7,358.00 (2025-09-17) and 6,345.20 (2026-07-19) — all `招商银行信用卡 → 支出:旅行`. `支出:经营支出:差旅` holds only 陈宇's two 386.50 vouchers. The 经营所得 notes deduct 成本费用 = 经营支出 only, so 2025's 14,054 of client travel is taxed as profit (≈ 1,405 overpaid after the halving) and 2026's 13,046 likewise. A bookkeeper who runs a ¥386.50 差旅 voucher through the employee module would not leave ¥7,358 of client travel in the household bucket.
Fix: book `出差 …` to `支出:经营支出:差旅`; the tax engine then picks them up unchanged.

**R4 (M) — Every transaction was entered on its posting day.**
Evidence: `enter_date` date == `post_date` date for 2,920 / 2,920 rows; entry hour uniform 12:00–20:00; this includes 春节 days in the hometown, weekends, and the 2025-01-01 opening. A book that demonstrates statement entry and reconciliation should show entries clustering on bookkeeping days a few days to weeks after posting (card statements after the 20th/25th close, bank after month-end), with `post_date < enter_date` the norm. Listed here because the brief asked; a native reader would not see it in most views.
Fix: give each account an entry cadence (cards: 2–5 days after statement close; bank: weekly; wallets: same day) and derive `enter_date` from it.

**R5 (M) — Q3 2026 revenue spikes ~80% with no narrative.**
Evidence (CNY, by quantity): quarterly business revenue 2025 Q1–Q4 = 130,790 / 117,627 / 151,916 / 111,792; 2026 Q1 109,503, Q2 146,584, **Q3 (to 09-17) 277,975**. Five foreign invoices in nine weeks (Pacific USD 5,200 on 07-16 and 4,500 on 09-05; Handelskontor EUR 4,100 on 08-06, 3,800 on 08-08, 2,800 on 09-14) versus ~2 per quarter before; plus 顺丰 19,500 + 23,500 and the retainer's second September invoice (9,000 `运维支持`). Dashboard monthly net: May +8,931 → Aug +96,645. Growth is plausible; the step is not explained by anything in the book. Three of these are the intentional overdue demo invoices, so this may be by design — flagging, not failing.
Fix (optional): spread the 2026 foreign cadence like 2025's, or add a note on the job invoices (`Mobile App v2 里程碑`, `ERP-Integration 二期`) so the jump reads as a project phase.

---

## 3. Attention items — verified (evidence)

- **VAT / 附加, months and base.** Filed 2025-04-14, 07-14, 10-13 (Mon; 12th was Sun), 2026-01-12, 04-13 (Mon), 07-13 — the 15-days-after-quarter rule. Each note reproduces from the ledger: 专票 base = 承包收入 ÷ 1.01 (Q1-2025 73,000 → 72,277.23 → VAT 722.77), 普票 exempt under 300,000/季 (36,000), 跨境服务 export exempt (21,790.20 = the USD invoice at post rate). Cross-checked all six quarters to the cent. 1% levy ✓, 专票 never exempt ✓. One below-threshold note in section 5 (附加 rate).
- **经营所得.** Cumulative method reproduces every quarter: 2025 Q1 累计 130,790.20 − 19,805 − 15,000 = 95,985.20 → ×20% − 10,500 = 8,697.04, halved 4,348.52 ✓; Q2 12,675.10 − 4,348.52 = 8,326.58 ✓; Q3 24,188.25 ✓; Q4 35,607.80 ✓ (372,385.31 in the 30% bracket). 汇算清缴 2026-03-20: 350,785.31 after 60,000 + 赡养老人 18,000 + 继续教育 3,600 → 32,367.80, 退税 3,240.00 ✓ (专项附加扣除 claimed only at 汇算 is the rule for 经营所得). 2026 Q1 2,984.92 and Q2 10,502.14 ✓. 60,000 减除 is legitimate: 林微 has no 综合所得. Halving (≤200万, 2023–2027) ✓.
- **社保/公积金 bases.** 周子航: 社保 1,575.00 and 公积金 1,200.00 on a fixed 15,000 base in all 21 months, overtime months (15,787 / 16,229 / 15,906 / 15,727 / 16,215 / 15,951 / 16,030) change only gross, 个税 and net ✓. 个税 累计预扣 reproduces (216.75 → 240.36 → 280.59 at the 36,000 crossing → 845.40 → 722.50; January reset) ✓. 单位缴存 1,200 = employee 8% ✓. 结息 2025-06-30 1,236.00 and 2026-06-30 1,686.54 = 1.5% ✓.
- **陈宇.** 3,500 gross + 525 employer 社保 (15%) monthly, 4,025 out of 银行储蓄卡 on the 10th (business day), 21/21 months; two vouchers (差旅 386.50 ×2, 办公用品 263.80) posted to 应付账款 and paid a week later ✓. No 个税 (below 5,000) ✓. Above Shenzhen minimum wage ✓.
- **Cards vs limits, interest.** 招商 (limit 80,000, close 25): max month-end 14,302.80; each statement paid in full 3–7 days after close (Sep-2025 9,431.50 → paid 10-01; Feb-2026 9,206.67 → 03-03), no interest ✓. 工商 (limit 50,000, close 20): opening 8,500 paid down 1,500/月 with interest = balance × 18.25%/12 (114.04, 101.27 … 1.47), 结清 2025-10-26, thereafter paid in full each cycle ✓. 汇丰港币 (limit HKD 60,000, close 8): five HK purchases, each repaid in full 6–34 days later, never over 3,200 ✓.
- **HKD repayment via 购汇.** Transaction currency HKD; card leg +HKD, bank leg −HKD value / −CNY quantity at the day's rate (3,200 → 3,007.87 = 0.9400; 1,957 → 1,691.20 = 0.8642, consistent with USD 6.767 on 07-16 and the 7.8 peg) ✓.
- **Revenue continuity.** Retainer 12,000 → 12,600 monthly through 2026-09 (note `2026 年度合同续签`), 专票 customers every 1–2 months, foreign customers on cadence; A/R 60,100 / USD 9,700 / EUR 6,900 open = the three flagged overdue + not-yet-due ✓ (see R5 for the Q3 step).
- **Weekdays.** Payroll, 陈宇, 车贷, tax filings, 公积金 all on business days (10th → Mon when weekend; 2025-10-13, 2026-04-13). No A/R receipt on a weekend. Card autopays and wallet spend on weekends ✓. Coffee/takeout/nightlife weekday distributions now natural (瑞幸 Mon–Sun 74,87,84,90,75,39,36; 娱乐 Fri/Sat-heavy).
- **春节.** 红包 6,000 on 除夕 both years (2025-01-28, 2026-02-16) ✓; hometown windows 2025-01-26 → 02-03 and 2026-02-14 → 02-22 with 高铁, 家宴, 年货, 走亲访友 and no Shenzhen coffee ✓; first trading-day DCA 2025-02-05 ✓.
- **印花税.** `卖出 100 300750 @ ¥246.7`: 交易费用 18.51 = 印花税 0.05% (12.34) + 佣金 0.025% (6.17) ✓; buy 100 @ 325 fee 8.12 = 0.025% ✓; ETF sale 2,000 159915 @ 3.12 stamp-free ✓. Round lots ✓; CATL −1,210 loss and ChiNext +2,200 gain against cost ✓.
- **Entry vs posting dates.** See R4.
- **`last_occur` vs latest posted instance.** All 21 schedules match (周子航工资 / 陈宇工资 / 视频会员 2026-09-10; 话费 09-12; 车贷 09-08; 宽带 09-08; 物业 09-07; 阿里云 09-03; 联合办公 09-01; 停车 08-28; 天然气 08-27; 电费 08-25; 水费 08-23; 宠物口粮 08-20; 房贷 08-18; 增值税 07-13; 经营所得预缴 07-14; 汇算清缴 03-20; 宠物体检 03-08; 春节红包 02-16; 车险 05-20) ✓. 328 `from-sched-xaction` stamps.
- **Loans.** 房贷 等额本息 14,800/月 at 3.85%: interest 8,983.33 → 8,622, principal 5,817 → 6,178, balance 2,680,050 ✓ (implied term ≈ 24 years, legal). 车贷 2,400/月 at 4.9%: 490 → 330, balance 78,210 ✓.

---

## 4. Versus the first cold audit

| Item | Status | Evidence |
|---|---|---|
| L1 filing months | **Fixed** | Jan/Apr/Jul/Oct 12th–14th; no Mar/Jun/Sep/Dec filings. |
| L2 VAT under threshold | **Fixed** | 专票 1% only; 普票 exempt; export exempt; notes re-derive. |
| L3 经营所得 not from profit; no 汇算 | **Fixed** | Cumulative prepayments and 2026-03-20 汇算清缴 all reproduce. |
| L4 floating 社保 base | **Fixed** | 1,575 / 1,200 flat on 15,000 across overtime months. |
| L5 承包收入 without 发票 | **Fixed** | 17 CNY invoices to 腾讯/字节/大疆/华为云/平安/顺丰 noted `增值税专用发票（征收率 1%）`. |
| L6 phantom 陈宇 | **Fixed** | Payroll + 社保（单位） + three vouchers. |
| L7 VPN | **Fixed** | Zero rows. |
| L8 印花税/佣金 | **Fixed** | 交易费用 on the stock sell and buy. |
| L9 定投 on closed days | **Fixed** | All 42 DCA and quarter-end buys on trading days. |
| P1 cards no interest | **Fixed** | See section 3. |
| P2 汇丰 unpaid | **Fixed** | Five purchases, five full 购汇 repayments, 2026 activity. |
| P3 HK shopping as groceries | **Fixed** | 服装 / 个人护理 / 杂项. |
| P4 retainer vanishes | **Fixed** (over-corrected) | Monthly 12,600 through 2026; new Q3 spike is R5. |
| P5 no holiday seasonality | **Fixed** | Lunar dates; hometown windows. |
| P6 mortgage 3.85%, no 专项附加扣除 | **Remains** (below threshold) | apr still 3.85, no LPR reprice in Jan 2026; 周子航 claims no 专项附加扣除; 林微 does claim two at 汇算. |
| P7 no interest on 储蓄/公积金; 现金 unused | **Mostly fixed** | Monthly 储蓄 利息 and 6-30 公积金 结息 present; 现金 still one split (2,000, never touched). |
| P8 no 年终奖/职业年金/保险/保养/维修 | **Remains** (L) | 人寿/医疗保险, 汽车保养, 房屋维修 all zero; no bonus month. 车险 5,400/yr ✓. |
| P9 Beijing convenience stores, no restaurants | **Fixed** (stores) / **partial** (restaurants) | 美宜佳 169, 天虹微喔 208, 7-11 115, 便利蜂/全家 0; sit-down dining still only 老家 家宴. |
| P10 vending from bank card | **Fixed** | 微信 28 / 支付宝 18. |
| P11 滴滴 under 停车费 | **Fixed** | 支出:交通 (122). |
| P12 年检; quarterly pet 体检 | **Fixed** | No 年检; 体检 annual 03-08. |
| P13 显示器 to 软件 | **Fixed** | 办公设备 1,280 / 2,800; 办公用品 separate. |
| A1 rigid weekdays | **Fixed** | Distributions above. |
| A2 bill/card double-count | **Fixed** | No 优客工场/阿里云 bills; vendors are 博源, 赛格, JetBrains. |
| A3 stale `last_occur` | **Fixed** | 21/21 match. |
| A4 repeated named events | **Fixed** | 表妹 / 堂妹 / 舅舅 / 师兄 / 邻居 distinct. |
| A5 山姆 on the 12th, invoice on the 1st | **Fixed** | 山姆 9th–15th; invoices roll to business days. |
| A6 English strings | **Remains** (L) | `期初余额 (Opening Balances)`, lot titles `沪深300 core position`, `创业板 growth position`. |
| A7 reconciliation frozen | **Fixed** | 银行储蓄卡 reconciled to 09-14, cards to Sep, wallets to today. |
| A8 flat bills | **Remains** (L) | 云服务器, 话费, 物业, 网络, 停车 each 1 distinct amount over 20–21 rows. |

**New this round:** R1 (支付宝 negative), R2 (银行储蓄卡 overdraft), R3 (client travel outside 经营支出), R4 (entry dates), R5 (Q3-2026 spike).

---

## 5. Below threshold, noted for the generator (not counted)

- **附加税费 at 6% of VAT** (城建 7% + 教育 3% + 地方教育 2%, halved). Under 财税〔2016〕12号 a taxpayer whose quarterly sales ≤ 300,000 — the same threshold the note itself invokes — is exempt from 教育费附加 and 地方教育附加 outright, leaving 城建税 7% halved = 3.5%. Q1-2025 附加 should be 25.30, not 43.37; ~18 CNY/quarter, but it is a figure the 电子税务局 would not produce.
- 周子航 社保 at 10.5% (养老 8 + 医疗 2 + 失业 0.5); Shenzhen employee 失业 is 0.3% → 10.3%, 1,545. Base never re-set in July 2025/2026 despite prior-year average > 15,000.
- `房贷还款` autodebit rolled to the next business day (2025-01-20, 2025-05-19, 2026-02-24 after 春节); bank autodebits run on the contract day regardless. `春节红包` SX is a Gregorian yearly recurrence anchored 01-28 — GnuCash will propose 2027-01-28, not 除夕 2027-02-05.
- Three foreign invoices issued on Saturdays (2025-11-08, 2026-08-08, 2026-09-05).
- ETF DCA carries no 佣金 across 42 buys (stock trades do).
- Export receipts settle straight into a personal 储蓄卡 in CNY with no 涉外收入申报 trace — common practice, within the USD 50k 便利化 quota (2025 ≈ 23.4k, 2026 YTD ≈ 13.4k), but a 个体户 对公 account would be the by-the-book shape.
