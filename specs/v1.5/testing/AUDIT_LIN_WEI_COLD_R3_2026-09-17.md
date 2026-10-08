# Cold audit, round 3 — `samples/lin-wei.gnucash` (built 2026-09-17 16:36)

**Verdict: PASS as a native reader.** Both R2 failures are gone: no bank, cash or wallet account has a negative day-end anywhere in 20½ months, and client travel now sits in `支出:经营支出:差旅` where the 经营所得 notes deduct it. The tax engine, payroll, cards, loans, trades and schedules reproduce to the cent. One row remains that a mainland reader would call impossible (a direct 微信→支付宝 transfer, 3,000 on 2025-05-11) — it moves no balance anywhere it could not be, so it is listed, not failed.

Stance: mainland household + 个体工商户 bookkeeper. Read-only: server Python API (`get_book_summary`, `list_accounts`, `list_scheduled_transactions(compact=False)`, `balance_sheet`, `income_by_source`, `spending_by_category`, `list_invoices`, `get_transaction`, `get_account_slots`) plus a full split dump through `gb.open()`; balances re-derived from split quantities on a private scratch copy. 2,917 ledger transactions + 21 SX templates, 2025-01-01 → 2026-09-17. R2 was read only after the cold pass.

---

## 1. Illegal or impossible

**R3-1 (L) — `微信转支付宝 (WeChat → Alipay)` 3,000 on 2025-05-11: the channel does not exist.**
Evidence: `5ddd3b6f` — `微信支付 −3,000 / 支付宝 +3,000`, both legs reconciled 2025-05-31. WeChat balance cannot be sent to Alipay; the real path is 微信提现 → 银行卡 → 充值支付宝 (two rows, T+0/T+1). It is the only wallet↔wallet row in the book and, with its sibling `充值微信钱包 (Checking → WeChat Pay)` 5,000 on 05-10 (`c1d74e5d`), the only two wallet rows carrying an English gloss — they read as a hand patch. Balances are unaffected (微信 after the transfer 2,000+; 支付宝 never below 513.44).
Fix: replace with `微信零钱提现` 3,000 (微信 → 银行储蓄卡, 05-10) + `充值支付宝` 3,000 (银行储蓄卡 → 支付宝, 05-11); drop the English glosses.

Nothing else at this level. Checked and clean (evidence in §3): every BANK/CASH day-end ≥ 0; VAT/附加 incl. 教育费附加 exemption; 经营所得 with 差旅 in the base; 社保 10.3% / 公积金 8% on 15,000; 陈宇 payroll; card limits, interest only on the carried 工商 balance, HKD 购汇; discretionary buys; `last_occur` ×21; entry dates.

---

## 2. High-severity implausible

None new. Two R2 items remain below the high bar and are carried in §4 (R4 entry dates, R5 Q3-2026 revenue step).

---

## 3. Attention items — verified

- **Bank / cash day-ends ≥ 0 (all 20½ months).** Minimum running balance by quantity: `银行储蓄卡` 14,345.42 (2025-05-06); `储蓄账户` 150,000.00 (opening); `微信支付` 554.03 (2025-10-29); `支付宝` 513.44 (2025-07-27); `现金` 2,000.00 (never moves). Zero negative day-ends. The R2 overdraft week (2025-11-18 → 24) is gone: no discretionary `买入 … @` lands in November now; quarter-end `结余投资`/`储蓄调仓` buys (03-31, 06-30, 09-30, 12-31) all clear with ≥ 16k left on the card. `储蓄账户转入（补足日常余额）` 33,003.62 on 2025-10-02 lands the day after the 10-01 low (16,296.38).
- **Wallet 充值 pattern reads as a household's.** 微信: 29 top-ups — on the 2nd of every month, sized 1,000–7,800 to the prior month's spend, plus seven `充值微信钱包（余额不足）` 1,500–2,000 mid-month when the balance nears 500. 支付宝: 32 — same shape (2nd of month 200–5,600; twelve `余额不足` 1,500). All from `银行储蓄卡` except R3-1. Two `余额不足` two days apart (2025-09-28/30, 2026-05-23/28) around 国庆/五一 spend — plausible.
- **VAT / 附加, months and base.** Six filings 2025-04-14, 07-14, 10-13, 2026-01-12, 04-13, 07-13 (all Mondays/within 15 days). Every note re-derives from the ledger: 专票 = 承包收入 ÷ 1.01 (Q1-25 73,000 → 72,277.23 → VAT 722.77; Q2-25 大疆 40,000; Q3-25 腾讯 52,000; Q4-25 华为云 34,000; Q1-26 平安 51,000; Q2-26 平安+大疆 68,500), 普票 = the retainer (36,000 / 37,800) exempt under 300,000/季, 跨境 = the USD/EUR receipts at post rate (Q1-25 21,790.20 = USD 3,000 @ 7.2634; Q2-25 20,089.75 + 21,537.60 = 41,627.35 …). **附加 now 3.5% (城建 7% halved; 教育费附加 / 地方教育附加 exempt under 财税〔2016〕12号)**: 722.77 × 3.5% = 25.30 ✓ — the R2 §5 note is fixed. Quarterly totals all < 300,000 (max Q3-2026 to date 277,975 — 92% of the threshold; a reader would watch it).
- **经营所得, with 差旅 in the base.** 成本费用 = 全部 经营支出 including 差旅: 2025 Q2 45,862.50 = 19,805 + 26,057.50 (6,696 US trip + 386.50 voucher inside) ✓; FY-2025 93,794.30 = `spending_by_category` 经营支出 ✓; 2026 H1 45,417.70 ✓. Cumulative prepayments: Q1-25 95,985.20 → 4,348.52; H1 172,555.05 → 12,005.50 (−4,348.52 = 7,656.98); 9M 280,328.51 → 22,782.85; FY 358,331.31 → ×30% − 40,500 halved = 33,499.70 ✓. 汇算清缴 2026-03-20: −60,000 − 21,600 (赡养老人 18,000 + 继续教育 3,600) → 336,731.31 → 30,259.70, 退税 3,240.00 posted `银行储蓄卡 +3,240 / 个人经营所得税 −3,240` ✓. 2026: Q1 67,997.10 → 2,649.86; H1 180,669.40 → 12,816.94 ✓. 专项附加 only at 汇算 ✓; 60,000 legitimate (林微 has no 综合所得) ✓; ≤200万 halving ✓. Revenue base 130,790.20 / 512,125.61 = 个体经营收入 + 承包收入 by quantity ✓.
- **社保 10.3% / 公积金 8%, fixed base.** 周子航 21/21 months: 社保 1,545.00, 公积金 1,200.00 on 15,000 regardless of overtime (15,787 / 16,229 / 15,906 / 15,727 / 16,215 / 15,951 / 16,030 change only gross, 个税, net). 累计预扣 reproduces incl. the 36,000 crossing: 2025 217.65 ×2 → 241.26 → 217.65 → 291.99 (May) → 848.40 → 725.50 …; 2026 217.65 → 254.10 → 321.95 (May) → 820.60 → 725.50 → 828.50 ✓. Employer 公积金 1,200/月 → `住房公积金收入` 14,400 (2025) / 10,800 (2026 YTD) ✓; 结息 06-30 into 利息收入 ✓.
- **陈宇 payroll.** 21/21 months on the 10th (business day): 工资 3,500 + 社保（单位） 525 = 4,025 out of `银行储蓄卡`; note carries 代扣个人社保 360.50 (10.3%) ✓; no 个税 (< 5,000) ✓; three vouchers (差旅 386.50 ×2, 办公用品 263.80) → 应付账款, paid 7 days later ✓; SX `陈宇工资` splits match ✓.
- **Cards vs limits; interest only where a balance carried; HKD 购汇.** `招商` (limit 80,000, close 25): peak owed 14,302.80 (Jan-25); every statement paid in full 3–7 days after close; no interest; today 1,850 = Sep 联合办公 + 阿里云 unbilled ✓. `工商` (limit 50,000, close 20): opening 8,500 paid 1,500/月, interest = unpaid × 18.25%/12 (114.04 → 101.27 → … → 1.47), `结清` 2025-10-26, thereafter paid in full, no interest ✓ (ICBC's unpaid-portion basis, not 全额罚息 — correct bank). `汇丰港币` (limit HKD 60,000, close 8): five HK$ purchases (3,200 / 1,800 / 2,460 / 3,155 / 1,957), each repaid in full by 购汇 in HKD with the CNY leg at that day's rate (3,200 → 3,007.87 @ 0.93996; 1,957 → 1,691.20 @ 0.86418; consistent with USD 7.26 → 6.71 and the 7.8 peg) ✓. No card ever over limit or overpaid (max day-end balance 0.00 on all three).
- **Discretionary buys never overdraw.** All 42 DCA rows + 8 quarter-end buys + `买入 100 300750 @ 325` (−32,508.12 on 2025-09-12, card left ≥ 20k) clear; see minimums above. 印花税 0.05% on the CATL sale, 佣金 0.025%, ETF sale stamp-free, round lots, share balances never negative ✓.
- **Loans.** 房贷 等额本息 14,800 at 3.85%: interest 8,983 → 8,622 (−19/月), balance 2,680,050 ✓. 车贷 2,400 at 4.90%: 490 → 330, balance 78,210 ✓. Slots `apr` match.
- **`last_occur` on every schedule.** 21/21 equal the latest posted instance: 房贷 08-18, 宠物口粮 08-20, 水费 08-23, 电费 08-25, 天然气 08-27, 停车 08-28, 联合办公 09-01, 阿里云 09-03, 物业 09-07, 宽带/车贷 09-08, 周子航/视频会员/陈宇 09-10, 话费 09-12, 增值税 07-13, 预缴 07-14, 汇算 03-20, 春节红包 02-16, 宠物体检 03-08, 车险 05-20 ✓. Next-due dates all after the last instance; 3 due in 7 days matches the dashboard.
- **Entry dates.** `enter_date` day == `post_date` day for 2,917/2,917; entry hours 11–20 UTC (19:00–04:00 北京). No entry before its posting; no future-dated posts. Unchanged from R2 (R4).
- **A/R, A/P, overdue.** Open CNY 60,100 = 000055 23,500 + 000023 9,000 + 000021 12,600 + 000022 15,000; EUR 6,900 = 000038 2,800 + 000037 4,100; USD 9,700 = 000033 4,500 + 000036 5,200 — the 8 open invoices; the three overdue (33 / 12 / 1 days) are the intended demo. A/P 460 (赛格 000011) + USD 249 (JetBrains → 软件 1,673.85) ✓. FX gain/loss on each foreign receipt (−135.90 … +254.40) ✓.
- **Calendar.** Payroll/loan/tax rows roll to business days (2025-05-12, 2025-10-09, 2026-01-12, 2026-02-24 after 春节, 2026-04-20); wallet top-ups on any day ✓. 红包 6,000 on 除夕 both years ✓.

---

## 4. Versus R2

| R2 item | Status | Evidence |
|---|---|---|
| R1 (H) `支付宝` negative for months | **Fixed** | min 513.44 (2025-07-27); today 1,968.07; 32 sized top-ups + `余额不足` rows. |
| R2 (H) `银行储蓄卡` overdrawn 2025-11-18 → 24 | **Fixed** | min 14,345.42 (2025-05-06); no November discretionary buy; 补足 transfer on 10-02. |
| R3 (H→M) client trips as `支出:旅行` | **Fixed** | Four `出差 …` rows → `支出:经营支出:差旅` (6,696 / 7,358 / 6,701.20 / 6,345.20); 成本费用 in every 经营所得 note includes them; 2025 tax fell 35,607.80 → 33,499.70, 汇算 30,259.70. |
| R4 (M) every row entered on its posting day | **Remains** | 2,917/2,917 same-day; hours 11–20 UTC. |
| R5 (M) Q3-2026 revenue step | **Remains** | Q3 277,975 vs Q2 146,584; same five foreign invoices; no narrative note. |
| §5 附加 at 6% | **Fixed** | 3.5% (城建 only; 教育费附加/地方教育附加 exempt); 25.30 on 722.77. |
| §5 周子航 社保 10.5% | **Fixed** | 1,545.00 = 10.3% (失业 0.3%). |
| §5 房贷 autodebit rolled to business day; 春节红包 SX Gregorian | Remain | 2025-01-20, 05-19, 2026-02-24; SX next 2027-01-28. |
| §5 foreign invoices on Saturdays | Remain | 2025-11-08, 2026-08-08, 2026-09-05. |
| §5 ETF DCA without 佣金; export receipts into personal 储蓄卡 | Remain | Unchanged. |
| R1-round P6/P7/P8/A6/A8 (LPR reprice, 现金 idle, no insurance/bonus, English strings, flat bills) | Remain (L) | 现金 one split; `期初余额 (Opening Balances)`, lot titles; **plus two new English glosses** (`充值微信钱包 (Checking → WeChat Pay)`, `微信转支付宝 (WeChat → Alipay)`) — see R3-1. |

**New this round:** R3-1 (direct 微信→支付宝 transfer). Nothing at H.

---

## 5. Below threshold, noted for the generator (not counted)

- 经营所得 revenue is booked VAT-inclusive (专票 73,000, not 72,277.23) and the 增值税及附加 paid (748.07 …) is not deducted as 税金 — net effect ≈ +35 CNY tax per quarter, in the taxpayer's disfavour; the 电子税务局 form would show the exclusive figure.
- 支付宝 regular top-up of 200 on 2026-05-02 followed by two `余额不足` 1,500 (05-23, 05-28) — the sizing rule under-shot one month.
- 陈宇 employer 社保 at 15% is on the low side for Shenzhen (养老 15 + 医疗 ~5 + 失业 0.7 + 工伤 ≈ 21–22%); accepted in R2, unchanged.
- `错误供应商 (Wrong Vendor) 付款` 2025-03-15 is a voided demo row (state `v`, zero legs) — fine, but English.
