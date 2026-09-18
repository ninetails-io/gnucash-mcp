# Cold audit, round 5 — `samples/lin-wei.gnucash` (built 2026-09-17 20:36)

**Verdict: PASS.** All five R4 documentary failures are fixed, correctly and at the right level of
detail — not patched over. Nothing in R4's verified-exact list regressed: VAT and 附加, 经营所得,
累计预扣, the 对公账户 routing, the four-leg payroll, HKD 购汇, `last_occur` and the day-end
non-negativity all re-derive from the ledger exactly as before. As a mainland reader I would now
hand this book to a 税务师 without flagging an identifier.

Stance: 深圳 household + 有雇工的个体工商户 bookkeeper, reading with 金税四期 cross-matching and
SAFE settlement rules in mind. Read-only off a private scratch copy (`linwei-r5-audit.gnucash`);
full split dump, all figures re-derived from split quantities; `balance_sheet` via the server API.
2,982 transactions + 21 SX templates, 2025-01-01 → 2026-09-17. R4 read first, as instructed.

---

## 1. The five R4 failures

**R4-1 — SAFE 交易编码.** Fixed. All 11 结汇 rows now read
`交易编码 227020（电信、计算机和信息服务—计算机服务）`; zero occurrences of `121010` remain
anywhere in the book. 227020 is the correct 计算机服务 code under
《涉外收支交易分类与代码（2014版）》, and it drops the 货物贸易 problem entirely — a service
exporter no longer claims a code that would require 贸易外汇收支企业名录 registration.

**R4-2 — 发票 number format.** Fixed. Zero `发票代码` strings and zero 12-digit 代码 remain.
All 113 references are 20-digit 数电票 numbers: `25`/`26` (year) + `44` (广东) + channel digit +
15-digit serial, 102 distinct numbers over 102 distinct documents. The 11 apparent duplicates are
each a vendor bill and its own posting transaction carrying the same number — the same document,
correctly. Year prefix matches the document date on every one; province digits are `44`
throughout, including on the two third-party sellers' bills (深圳博源代理记账, 华强北 赛格电子),
who are themselves Shenzhen issuers.

**R4-3 — 统一社会信用代码 check digit.** Fixed, and R4's predicted character was wrong. The book
now carries `92440300MA5FQ2X7J0` on all 128 occurrences, with no variant anywhere. Recomputed
independently under GB 32100-2015 — 31-character alphabet `0123456789ABCDEFGHJKLMNPQRTUWXY`
(I, O, S, V and Z all excluded), weights 1,3,9,27,19,26,16,17,20,29,25,13,8,24,10,30,28, check =
`A[(31 − Σ/31 mod 31) mod 31]` — the check character over `92440300MA5FQ2X7J` is **`0`**. R4's
`M` came from a 32-character alphabet that wrongly retained `V`. The book's value is right.

**R4-4 — per-invoice 免税备案.** Fixed, and fixed the way the rule actually works. Two filings
exist, one per overseas customer: 受理编号 `4403544951706547` (Pacific Trade Solutions, 首次备案
2025-03-05, 8 invoices) and `4403399596272445` (Handelskontor München, 首次备案 2025-04-08,
7 invoices). Each 首次备案 date equals the date of that customer's first export invoice; the
earliest invoice under each number carries no repeat clause, and all 13 later ones carry
`相同跨境应税行为再次发生，无需再次办理备案，合同及收汇凭证留存备查（国家税务总局公告2016年第29号
第七条）`. The invented `深税跨境备〔20xx〕第N号` doc-number form is gone. The R4 complaint case —
two invoices eight weeks apart under one contract — now cites one 受理编号 and one 合同编号
(HKM-2026-123). Renewed contracts (PTS-2026-122, HKM-2026-123) correctly ride the original
filing rather than opening a new one.

**R4-5 — retainer invoices with no 发票.** Fixed. All 23 深圳跨境电商有限公司 invoices
(000001–000023) now carry `数电普通发票 发票号码 …；购方 …；销方 深圳市林微电子商务工作室（统一
社会信用代码 92440300MA5FQ2X7J0）；小规模纳税人，征收率 1%`. Quarter by quarter, the 普票
sales declared in every VAT note now map to invoices with a number — **NO-DOC revenue is ¥0.00 in
all seven quarters**. Document coverage is now complete: 17 专票, 23 普票, 15 跨境免税, 11 purchase
数电普票, 1 境外形式发票 (JetBrains), and 3 员工报销单 which correctly carry no 发票 number.

---

## 2. Regression check — R4's verified-exact list

All re-derived from split quantities on this build.

- **VAT / 附加, six filings.** Every 专票 / 普票 / 跨境 figure in every note equals the ledger:
  Q1-25 73,000 ÷ 1.01 = 72,277.23, 普票 35,643.56, 跨境 21,790.20 → 722.77 + 25.30 = 748.07;
  … Q2-26 67,821.78 → 678.22 + 23.74 = 701.96. Paid amounts tie: 748.07, 409.90, 532.87, 348.41,
  522.62, 701.96. 附加 at 3.5% throughout.
- **经营所得, seven filings.** 收入 (技术服务 + 个体经营, foreign invoices at their CNY quantity)
  − 经营支出 − 5,000/月 reproduces every 累计应纳税所得额 to the cent: 95,985.20 → 4,348.52;
  172,555.05 → 12,005.50; 280,328.51 → 22,782.85; 358,331.31 → 33,499.70; 汇算 336,731.31 →
  30,259.70, 退税 3,240.00; 2026 Q1 67,997.10 → 2,649.86; H1 180,669.40 → 12,816.94.
- **累计预扣, 21/21 months.** Recomputed the full 累计预扣法 for 周子航 from gross, 社会保险 1,545
  and 住房公积金 1,200: **21 of 21 months reproduce the booked 个税 exactly**, including the
  bracket crossing in June of both years (45,546 → 848.40; 45,696 → 820.60) and the September 2026
  overtime month (16,030 − 828.50 − 1,545 − 1,200 = 12,456.50).
- **Day-end balances.** Zero negative day-ends on any BANK or CASH account. Minima: 对公账户
  32,484.47 (2026-02-10), 银行储蓄卡 11,037.96 (2025-09-14), 微信支付 537.24, 支付宝 513.44,
  储蓄账户 150,000.00, 现金 2,000.00, 住房公积金 68,000.00.
- **对公账户 routing.** 136 splits. All 47 A/R settlements land here and nowhere else (the
  counterpart on every A/R-clearing transaction in the book is this account: 20 深圳跨境电商,
  11 结汇, 5 大疆, 3 顺丰, 3 平安, 2 腾讯, 2 华为云, 1 字节). Out: 42 payroll legs, 7 代理记账,
  3 赛格电子, 3 陈宇 vouchers, 6 增值税, 7 经营所得, 20 业主提款. No household expense account
  appears on any transaction touching it.
- **Four-leg payroll.** 21/21 months: 工资 3,500 + 社保（单位）525, 对公 −3,139.50 and −885.50.
  Employee share 360.50 = 10.3% of 3,500 (深圳一档: 养老 8 + 医疗 2 + 失业 0.3); remittance
  885.50 = 360.50 + 525. The three 陈宇 报销 pairs are separate two-leg 应付账款 rows, correctly.
- **HKD 购汇.** 5 purchases, each repaid in full in HKD with a CNY leg at the day's rate; card
  balance returns to exactly 0.00 each time, never overpaid, close 0.00.
- **`last_occur`, 21/21 schedules**, all matching R4's dates (周子航/陈宇 20260910, 房贷 20260818,
  话费 20260912, 增值税 20260713, 预缴 20260714, 汇算 20260320, 春节红包 20260216, 车险 20260520 …).
- **Balance sheet.** Assets 5,343,305.48 − Liabilities 2,762,801.00 = Equity 2,580,504.48 —
  identical to R4.
- **Also unchanged and clean:** 63/63 non-payroll 经营支出 splits carry a 发票 (42 payroll legs
  correctly carry none); FX gain/loss signs correct on all 11 settlements; stamp duty 0.05% and
  佣金 0.025% on the CATL sale; no 对公账户 or 税费 row on a weekend; no card ever over limit.

---

## 3. Below threshold, noted for the generator (not counted)

- **The channel digit is `0` on all 113 数电票 numbers, including the 12 purchase invoices.**
  国家税务总局公告2024年第11号 says only that the 5th position carries `开具渠道等信息` and
  publishes no enumeration, so `0` cannot be called invalid — but three different issuers
  (林微, 深圳博源代理记账, 华强北 赛格电子) sharing one channel digit is the one thing a
  filer's eye would snag on. Varying it per issuer would cost nothing.
- 涉外收入申报 编号 remains 10 digits (`2026222280`); a bank's report number is longer. Carried
  over from R4.
- H1-2025 累计应纳税额 is booked 12,005.50 where the halving gives 12,005.505 — one cent, rounded
  down where 2026 Q1 (2,649.855 → 2,649.86) rounds up. Immaterial; worth one rounding convention.
- Everything R4 listed as "Remains" still remains and none of it is disqualifying: the unexplained
  Q3-2026 revenue step (跨境 148,675 vs 40,284 in Q2), all 6,144 splits entered on their posting
  day, 经营所得 收入 booked VAT-inclusive, 陈宇's employer 社保 at 15%, three foreign invoices on
  Saturdays, 现金 with a single opening split, 林微 paying no 社保 for herself, and 业主提款
  netting to zero as a clearing leg.

Sources consulted for the 数电票 numbering rule:
[东奥会计继续教育](https://www.dongao.com/jxjy/csgh/202411284505228.shtml),
[济南市人民政府 12345知识库](http://www.jinan.gov.cn/art/2024/11/25/art_118356_16160.html).
