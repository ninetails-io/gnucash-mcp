# Cold audit, round 4 — `samples/lin-wei.gnucash` (built 2026-09-17 19:55)

**Verdict: FAIL — narrowly, on the documentary layer only.** The money is right. Round 4 put a
real 对公账户 under the business and every structural charge the cross-model audit made against
this book is now answered: all 47 receivable settlements land in the 对公账户, payroll and bills
and filings leave from it, the owner draws to the household monthly, 陈宇's row is four legs with
the withheld share inside the remittance, and 63/63 non-payroll 经营支出 rows carry a 发票 whose
抬头 names a registered 个体工商户. VAT and 经营所得 reproduce to the cent from the ledger at every
quarter. What fails is the statutory *identifier* layer: four fields that a Shenzhen filer's bank
or the 电子税务局 would reject, each stamped on 100% of its kind — the SAFE transaction code on all
11 结汇 rows, the 发票 number format on all 28 invoice notes, the 统一社会信用代码's check digit,
and a per-invoice 免税备案 number. None moves a balance; all are generator string formatting.

Stance: mainland household + 个体工商户 bookkeeper, reading with 金税四期 cross-matching and SAFE
settlement rules in mind. Read-only: server Python API (`get_book_summary`, `list_accounts`,
`balance_sheet`, `income_by_source`, `spending_by_category`, `list_invoices`, `list_customers`,
`list_scheduled_transactions`) plus a full split dump off a private scratch copy
(`linwei-r4-audit.gnucash`); balances re-derived from split quantities. 2,982 ledger transactions
+ 21 SX templates, 2025-01-01 → 2026-09-17. R3 and the cross-model audit were read only after the
cold pass.

---

## 1. Illegal or impossible

**R4-1 (H) — every 结汇 declared under 交易编码 121010, which is 货物贸易/一般贸易.**
Evidence: 11 settlement rows, `b77f5870` 2025-04-07 through `099d9771` 2026-09-08, each noted
`涉外收入申报 编号 …；交易编码 121010（电信、计算机和信息服务）`. In the SAFE
《涉外收支交易分类与代码（2014版）》, `121010` is *货物贸易 — 纳入海关统计的货物贸易 — 一般贸易*.
电信、计算机和信息服务 is `227010 / 227020 / 227030`. The note's own parenthetical names the right
category and carries the wrong code. Consequences the persona would actually hit: a goods-trade code
requires the declarant to sit in the 贸易外汇收支企业名录 — a 个体工商户 selling services is not, so
the bank rejects the declaration at the counter; and if it passed, the 货物贸易外汇监测系统 would
show ~CNY 259,700 of 收汇 against zero 出口报关, which is the textbook 总量核查 flag. The one field
the round was built to demonstrate is the one field that is wrong.
Fix: `227020`（计算机服务）on all 11 rows; keep the parenthetical.

**R4-2 (H) — invoice references are in the pre-数电票 format, which Guangdong stopped issuing in 2021.**
Evidence: 28 invoice/bill notes of the form `增值税普通发票 代码 044031957524 号码 14905986`
(12-digit 代码, 8-digit 号码) — e.g. the 2025-01-06 代理记账 bill, the 2026-09-09 顺丰科技 专票
(`代码 044031987851 号码 86408456`). 全面数字化电子发票 piloted in 广东 from 2021-12-01 and went
nationwide 2024-12-01: a 数电票 carries a **20-digit 发票号码** (年份2 + 省局代码2 + 开票渠道1 +
15位流水) and **no 发票代码 at all**. Every invoice in this book is dated 2025–2026 and in a format
the system no longer issues. Separately the 12-digit codes are internally malformed — positions 9–10
are the 票种代码 and should read `04` for a 增值税普通发票; here they are random digits (`…9575`**`24`**).
Fix: drop 代码 entirely; emit a 20-digit number beginning `25`/`26` + `44`.

**R4-3 (H) — 统一社会信用代码 92440300MA5FQ2X7J8 fails its own check digit.**
Evidence: the code appears on all 43 发票 notes. Under GB 32100-2015 the 18th character is a mod-31
check over the first 17 (alphabet `0-9A-HJ-NP-QRTUVWXY`, weights 1,3,9,27,19,26,16,17,20,29,25,13,8,24,10,30,28);
computed check character is **`M`**, the book carries **`8`**. The 电子税务局, 开票系统 and every
piece of 财务软件 validate this on entry, so the code cannot exist. Prefix and structure are
otherwise right (`92` 个体工商户, `440300` 深圳).
Fix: `92440300MA5FQ2X7JM`.

**R4-4 (M) — a fresh 跨境应税行为免税备案 number on every export invoice, twice under one contract.**
Evidence: 15 export invoices, 15 distinct numbers — `深税跨境备〔2025〕第6678号` (000024, 2025-03-05)
through `〔2026〕第8214号` (000038, 2026-09-14); and `〔2026〕第3209号` (000037, 2026-08-06) and
`〔2026〕第9848号` (000035, 2026-08-08) both cite **合同编号 HKM-2026-083**. 国家税务总局公告2016年第29号
第七条 (amended by 2024年第15号) files once, at first exemption; 相同跨境应税行为再次发生，无需再次办理
免税备案手续, materials 留存备查. Two filings eight weeks apart for one contract is not a thing that
happens. The `深税跨境备〔20xx〕第N号` doc-number form is also invented — 备案 is a 表单 receipt, not
a numbered 批文.
Fix: one number per customer (or one for the book), repeated; add `已备案，相同业务留存备查` on
subsequent invoices.

Nothing else at this level. Checked and clean, with evidence in §3: the 对公账户's whole traffic;
陈宇's four-leg payroll; the 技术服务收入 / 个体工商户 / 专票 chain; every BANK and CASH day-end;
VAT and 附加; 经营所得 quarterly and annual; cards and HKD 购汇; `last_occur`.

---

## 2. High-severity implausible

**R4-5 — the 普票 half of every VAT filing rests on 23 invoices that carry no 发票 reference at all.**
Evidence: invoices `000001`–`000023` to 深圳跨境电商有限公司 (12,000/月 2025, 12,600/月 2026, plus
15,000 on 2026-08-17 and 9,000 on 2026-09-07) have empty `notes`, and their posting transactions
carry no note either — the only income rows in the book with no documentary trail. Yet every VAT
note counts them precisely: `普票 ¥35,643.56` = 36,000 ÷ 1.01, `¥37,425.74` = 37,800 ÷ 1.01, exempt
under 30万/季. The customer is a 有限公司; it cannot expense CNY 151,200/year without a 发票, and
under 金税四期 the seller's 开票数据 is the primary cross-match against the declared 免税/普票
销售额. Every other revenue line in the book — 专票 to 顺丰/大疆/腾讯/华为云/平安/字节, 免税备案 on
all 15 export invoices — carries its paperwork. This one, the most regular stream in the ledger, has
none.
Fix: a 数电普通发票 number per retainer invoice, same shape as the 专票 notes.

---

## 3. Attention items — verified

- **对公账户 (`%efa0109` 资产:流动资产:招商银行对公账户), the whole ledger.** 136 splits. In: all
  47 receivable settlements (0 elsewhere — the counterpart account on every A/R-clearing transaction
  in the book is this one), including 11 结汇 rows. Out: 21 payroll rows, 7 代理记账 payments, 3 华强北
  bill payments, 3 陈宇 vouchers, 6 增值税 filings, 6 经营所得 预缴, 1 汇算清缴 (a **refund**, +3,240 on
  2026-03-20), 20 业主提款. Nothing household ever touches it — a scan for a non-经营/non-税费 expense
  account on any transaction that includes this account returns zero rows. Card repayments come only
  from `银行储蓄卡` (20 招商, 19 工商), never from here. Balance never negative: minimum 32,484.47 on
  2026-02-10, close 72,630.58.
- **业主提款, monthly, both legs.** 20 pairs, same date each month: `对公账户 −X / 所有者权益:业主提款 +X`
  then `业主提款 −X / 银行储蓄卡 +X` (2025-01-27 `391b46a7` + `819c70c2`, … 2026-08-31). 16,100 → 62,100,
  sized to the month. The equity account nets to exactly 0 across the book — it is used as a clearing
  leg, not a cumulative draw balance; legal, and worth knowing before someone reads 业主提款 as zero.
- **陈宇's four legs.** 21/21 months, e.g. `b0e36006` 2026-09-10: `经营支出:工资 +3,500`,
  `经营支出:社保（单位） +525`, `对公账户 −3,139.50`, `对公账户 −885.50`. 3,500 − 360.50 = 3,139.50 net;
  360.50 + 525 = 885.50 remitted. 个人社保 10.3% on 3,500 is Shenzhen 一档 (养老 8 + 医疗 2 + 失业 0.3)
  exactly. No 个税 (3,500 < 5,000). The cross-model audit's "employee share never deducted" is fixed.
- **技术服务收入 under a registered 个体工商户.** 17 invoices to 顺丰科技 / 字节跳动 / 大疆 / 腾讯 /
  华为云 / 平安科技, each noted `增值税专用发票（征收率 1%）… 销方 深圳市林微电子商务工作室（统一社会信用
  代码 …）；技术服务费，款项汇入对公账户`. A 小规模纳税人 self-issuing 专票 at 1% is correct for
  2025–2026 (财政部 税务总局公告2023年第19号, extended through 2027). With the registration, the
  employee, the 缴费单位 remittances and the 对公 receipts in place, 经营所得 + the ≤200万 halving is
  the right treatment — the cross-model audit's 劳务报酬 reclassification no longer applies.
- **Export invoice paperwork.** All 15 carry 免税备案 + 合同编号 + `服务完全在境外消费，适用增值税免税
  （财税〔2016〕36号 附件4、国家税务总局公告2016年第29号）` + the 开票方 USCC. No Chinese 发票 on them,
  which is right — a 免税 service sale to an overseas buyer is invoiced commercially. Receipts settle
  into the 对公账户 at the day's rate with FX gain/loss recognised (`4d3b61b2` 2026-07-06: A/R USD 3,000
  out at post-rate 6.7656, bank in CNY 20,387.10 at 6.7957, 已实现获利 90.30). Only R4-1 and R4-4 spoil it.
- **发票 on 经营支出: 63/63.** Every non-payroll 经营支出 split carries a 发票 note with
  `抬头 深圳市林微电子商务工作室（统一社会信用代码 …）`; the 42 payroll legs correctly carry none
  (工资表 and 社保缴款凭证 are the vouchers, not a 发票). The one foreign purchase is handled properly:
  `c9782de1` 2026-09-01 JetBrains USD 249 — `境外单位不开具中国发票，凭合同及付汇凭证税前扣除
  （国家税务总局公告2018年第28号 第十一条）`, which is the correct citation.
- **Every BANK / CASH day-end ≥ 0, 20½ months.** Running minimum by quantity: 对公账户 32,484.47
  (2026-02-10), 银行储蓄卡 11,037.96 (2025-09-14), 储蓄账户 150,000.00 (opening), 微信支付 537.24
  (2025-05-08), 支付宝 513.44 (2025-07-27), 现金 2,000.00 (never moves), 住房公积金 68,000.00 (opening).
  Zero negative day-ends anywhere. R3's fix holds.
- **VAT / 附加 re-derives from the ledger, six filings.** 2025-04-14, 07-14, 10-13, 2026-01-12, 04-13,
  07-13 — all within 15 days of quarter end, all weekdays. Each note's three figures are the ledger's
  own: Q1-25 专票 73,000 ÷ 1.01 = 72,277.23 → VAT 722.77, 普票 36,000 ÷ 1.01 = 35,643.56 exempt,
  跨境 21,790.20; …; Q2-26 专票 68,500 ÷ 1.01 = 67,821.78 → 678.22, 普票 37,800 ÷ 1.01 = 37,425.74,
  跨境 40,283.80. 附加 at 3.5% (城建 7% halved under 六税两费; 教育费附加/地方教育附加 exempt) —
  722.77 × 3.5% = 25.30, 678.22 × 3.5% = 23.74. Paid amounts tie: 748.07, 409.90, 532.87, 348.41,
  522.62, 701.96. Taxable quarterly sales peak at 128,019 不含税 (Q3-2026 to date) against the 300,000
  threshold — 43%, not the 92% R3 reported, because 免税出口 sits outside the threshold test.
- **经营所得, seven filings, exact.** 累计应纳税所得额 = 收入 (ledger 个体经营 + 技术服务) − 成本费用
  (ledger 经营支出) − 5,000/月. Q1-25 130,790.20 − 19,805 − 15,000 = 95,985.20 → ×20% − 10,500,
  halved = **4,348.52** ✓; H1 172,555.05 → 12,005.50 (−4,348.52 = 7,656.98) ✓; 9M 280,328.51 →
  22,782.85 ✓; FY 358,331.31 → ×30% − 40,500 halved = 33,499.70 ✓; 汇算 2026-03-20 with
  −21,600 专项附加 → 336,731.31 → 30,259.70, **refund 3,240.00** ✓; 2026 Q1 67,997.10 → 2,649.86 ✓;
  H1 180,669.40 → 12,816.94 − 2,649.86 = **10,167.08** ✓. The 21,600 decomposes cleanly as 赡养老人
  1,500 × 12 (非独生子女 share) + 继续教育 职业资格 3,600 — both valid, and available to 林微 only
  because her 综合所得 is nil (the 15,000/月 wage is 周子航's, `收入:工资`, 21/21 months).
- **周子航's payroll, 累计预扣法 to the cent.** `7a8523ff` 2026-09-10: gross 16,030 − 个税 828.50 −
  社保 1,545 − 公积金 1,200 = net 12,456.50; employer 公积金 1,200 booked separately (`4ed07361`) as
  `住房公积金收入`, so the fund grows 2,400/月. The withholding follows 累计预扣: Jan-25 7,255 × 3%
  = 217.65, Mar (overtime 787) 22,552 × 3% − 435.30 = 241.26, Jun 45,546 crossing 36,000 →
  36,000×3% + 9,546×10% − 1,186.20 = 848.40. All 21 months reproduce.
- **Cards and HKD 购汇.** 招商 (limit 80,000) carries the 经营支出 subscriptions plus household
  clothing/education, repaid in full from 银行储蓄卡 20×, close 1,850; 工商 (limit 50,000) carries
  groceries and the only interest in the book, 结清 2025-10-26, close 560.66; 汇丰港币 (limit HKD 60,000)
  five HK$ purchases each repaid in full by 购汇 in HKD with the CNY leg at that day's rate
  (HK$3,200 → 3,007.87 @ 0.93996 … HK$1,957 → 1,691.20 @ 0.86418, tracking the 7.8 peg against USD
  7.26 → 6.71). No card ever over limit; maximum day-end balance on all three is 0.00 (never overpaid).
- **`last_occur` on 21/21 schedules**, each equal to the latest posted instance (房贷 20260818,
  物业 20260907, 宽带/车贷 20260908, 周子航/视频会员/陈宇 20260910, 话费 20260912, 增值税 20260713,
  预缴 20260714, 汇算 20260320, 春节红包 20260216, 宠物体检 20260308, 车险 20260520); every
  next-due lands after it, and the 3-due-in-7-days dashboard figure agrees.
- **Calendar.** No transaction posted after 2026-09-17; no 对公账户, 税费 or A-share row on a weekend;
  A-share activity respects market holidays (2025-02-05 after 春节, 2025-10-09 after 国庆,
  2026-05-06 after 劳动节); 红包 on 除夕 both years (2025-01-28, 2026-02-16).
- **Securities.** 印花税 0.05% + 佣金 0.025% on the CATL sale (18.51 on 24,670), 佣金 only on the buy
  (8.12 on 32,500), ETF sale stamp-free — all correct. `卖出 2000 159915` proceeds 6,240 − cost 4,040
  = gain 2,200 credited; `卖出 100 300750` 24,670 − 25,880 = loss 1,210 debited. A-share capital gains
  left untaxed, correctly.
- **Balance sheet ties.** Assets 5,343,305.48 − Liabilities 2,762,801.00 = Equity 2,580,504.48.
  A/R CNY 60,100 / EUR 6,900 / USD 9,700 equal the 8 open invoices; A/P 460 + USD 249 the 2 open bills.

---

## 4. Versus R3 and the cross-model audit

| Item | Source | Status | Evidence |
|---|---|---|---|
| Direct 微信→支付宝 3,000 (2025-05-11) | R3-1 (L) | **Fixed** | Now `5de9dbb1` 微信零钱提现 −3,000 → 储蓄卡 and `29e25cbf` 充值支付宝 +3,000, same day; English glosses dropped. |
| No 对公账户; revenue into personal cards/wallets | Cross-model §3.1 (公私不分) | **Fixed** | `%efa0109` 招商银行对公账户 opened 2025-01-01 at 60,000; all 47 A/R settlements land there; card repayments never from it; no household expense on it. |
| 承包收入 → 劳务报酬所得 reclassification risk | Cross-model §3.2 | **Fixed** | Account renamed `收入:技术服务收入`; registered 个体工商户 with USCC on every 专票; employs 陈宇 and remits as a 缴费单位 → 经营所得 + ≤200万 halving stands. |
| Cross-border exemption undocumented; personal-account settlement | Cross-model §3.3 | **Partly fixed** | 免税备案 + 合同编号 + 36号文/29号公告 citations on all 15; 涉外收入申报 编号 on all 11 receipts; settlement into 对公账户. **But** R4-1 (wrong 交易编码) and R4-4 (per-invoice 备案号) are new. |
| Expenses without 发票 bearing a registered tax ID | Cross-model §3.4 | **Fixed** | 63/63 non-payroll 经营支出 rows carry 发票 + 抬头 + USCC; foreign purchase cites 2018年第28号 第十一条. **But** R4-2 (format) and R4-3 (check digit) are new, and R4-5 leaves the 普票 income side bare. |
| Payroll: employee 社保 share never deducted | Cross-model §3.5 | **Fixed** | Four legs; 3,139.50 net + 885.50 remittance = 4,025 = 3,500 + 525. |
| Q3-2026 revenue step, unexplained | R3 §4 R5 (M) | **Remains** | Q3 277,975 vs Q2 146,584; now three foreign invoices inside five weeks (USD 5,200 07-16, EUR 4,100 08-06, EUR 3,800 08-08) and two extra retainer invoices. No narrative note. |
| Every row entered on its posting day | R3 §4 R4 (M) | **Remains** | 2,982/2,982 same-day. |
| 经营所得 revenue booked VAT-inclusive; 附加 not deducted as 税金 | R3 §5 | **Remains** | 收入 base = 130,790.20 (含税), not 129,711; ≈ +35 CNY/quarter in the taxpayer's disfavour. |
| 陈宇 employer 社保 at 15% (Shenzhen ≈ 19–21%) | R3 §5 | **Remains** | 525 / 3,500 unchanged. |
| ETF DCA and the ETF sale without 佣金 | R3 §5 | **Remains** | 51 of 53 trades carry no 佣金; only the two 宁德时代 rows do. Defensible under a waived-minimum broker. |
| Foreign invoices on Saturdays | R3 §5 | **Remains** | 2025-11-08, 2026-08-08, 2026-09-05. |
| English glosses; 现金 never moves | R3 §5 / R1 | **Remain** | `期初余额 (Opening Balances)`, `错误供应商 (Wrong Vendor) 付款` (the voided demo row); 现金 has exactly one split, the 2,000 opening. |

**New this round:** R4-1, R4-2, R4-3, R4-4 (§1) and R4-5 (§2) — all in statutory identifiers and
documentary references, none in the money.

---

## 5. Below threshold, noted for the generator (not counted)

- 林微 pays no 社保 for herself. A 有雇工的个体工商户业主 normally participates, as 灵活就业 or through
  her own 单位户 (~1,000–1,500/月 in Shenzhen). Grey enough not to count; conspicuous next to 陈宇's.
- 涉外收入申报 编号 is 10 digits (`2026222280`); the bank's report number is longer.
- Neither spouse claims 住房贷款利息 (1,000/月) though the mortgage is in the book; 慈善捐款 11,853.57
  in 2026 is not claimed either. Both conservative, both a little odd for a household this careful.
- 汇兑损益 (+392.51 in 2025, −329.29 in 2026 YTD) sits outside the 经营所得 收入 base in the notes.
  Immaterial, but it is taxable 经营所得.
- `支出:住房:房贷利息` and `支出:利息:房贷利息` both exist; only the latter is used.
- The 对公账户 has no `description` naming it as the 基本存款账户 of 深圳市林微电子商务工作室 — the
  USCC lives only in invoice notes.
