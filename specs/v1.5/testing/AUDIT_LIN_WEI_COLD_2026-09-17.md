# Cold audit — `samples/lin-wei.gnucash` (built 2026-09-17)

Auditor stance: mainland household + small-business bookkeeper (个税累计预扣、增值税小规模、社保公积金、结汇、A股交易规则). Read-only via the server's Python API plus direct sqlite3. 2,888 transactions, 2025-01-01 → 2026-09-17, CNY book, zh_CN chart. G1–G11 from the previous audit were read only after the cold pass (section 5).

Severity: **H** = a native reader stops trusting the book; **M** = raises an eyebrow, breaks a report; **L** = cosmetic.

---

## 1. Illegal or impossible

**L1 (H) — Quarterly VAT and 经营所得 prepayments land in the wrong months.**
Evidence: `增值税及附加 季度申报缴款` on 2025-03-12, 06-12, 09-12, 12-12, 2026-03-12, 06-12, 09-12; `经营所得个人所得税 季度预缴` on the 14th of the same months. Both schedules start 2025-03-12 / 03-14.
Rule: small-scale taxpayers file quarterly VAT and 经营所得 prepayments within 15 days after quarter end — January, April, July, October. March/June/September/December filings do not exist.
Fix: move both schedules to the 12th/14th of Jan/Apr/Jul/Oct (start 2025-04-12 / 04-14 for Q1-2025); add the annual 经营所得汇算清缴 in March (see L3).

**L2 (H) — VAT is paid while every quarter is under the exemption threshold.**
Evidence: business revenue per quarter (个体经营收入 + 承包收入, foreign at invoice rate): 2025 Q1 130,790; Q2 117,627; Q3 151,916; Q4 111,792; 2026 Q1 51,000; Q2 68,500; Q3 156,899. VAT paid 2,046–2,556 per quarter regardless (Q1-2026: 2,316.50 on 51,000 of sales = 4.5%).
Rule: 2023–2027 policy exempts small-scale taxpayers with quarterly sales ≤ 300,000 (月销售额 ≤ 10 万). At 1% the maximum liability if she waived the exemption would be ~1,300–1,570 + 附加 (halved). The amounts track neither the exemption nor the rate.
Fix: either (a) zero VAT with a memo `小规模纳税人 季度销售额未超 30 万，免征` and keep only the 申报 event, or (b) if she issues 专票, compute 1% × quarterly sales + 附加税费 (城建税 7%、教育费附加 3%、地方教育附加 2%, all halved) from the ledger's own revenue. Either way derive from the book.

**L3 (H) — 经营所得 prepayments are not derived from profit; annual 汇算清缴 missing.**
Evidence: 2025 profit ≈ 512,126 revenue − 29,150 经营支出 = ~483k. After 60,000 费用扣除, 5–35% brackets give ~86,400; with the 2023–2027 halving for 应纳税所得额 ≤ 200 万 (个体工商户) ≈ 43,200/yr. Paid: 5,264.50 + 5,845.23 + 5,382.50 + 5,676.99 = 22,169. No 2026-03-31 汇算清缴 entry. 2026 Q1 prepayment 5,683 on 51,000 of sales (cumulative method would give ≈ 750 after halving).
Fix: compute cumulative 经营所得 prepayments from the ledger (累计应纳税所得额 × 税率 − 速算扣除 − 已预缴), post the annual settlement in March, and let 2026's low quarters produce low prepayments.

**L4 (M) — 社保/公积金 bases move with monthly overtime.**
Evidence: base months 社保 1,650 / 公积金 1,100 on 15,000; overtime months 1,737 / 1,157 (Mar-2025, 15,787), 1,785 / 1,190 (Jun-2025, 16,229) etc. — exactly 11% and 7.33% of that month's gross.
Rule: contribution bases are fixed annually (Shenzhen adjusts each July from prior-year average wage); they do not float with the month's overtime. 7.33% is also not a legal 公积金 rate (integer 5–12%).
Fix: fix the base at 15,000 (or an annual figure), 社保 ≈ 10.3–11% of base, 公积金 an integer rate (12% is typical for 事业单位); overtime changes only gross, 个税 and net.

**L5 (M) — 承包收入 from 腾讯/字节跳动/大疆/华为云/平安科技/顺丰科技 with no 发票.**
Evidence: 15 receipts (17,000–27,500 each, 2025-01 → 2026-08) booked directly `银行储蓄卡 → 收入:承包收入`, outside the invoice module, no 发票 number, no VAT effect.
Rule: no large mainland company pays a contractor without a 发票 (个体户自开 or 税局代开); this is where VAT and 经营所得 bases come from. As booked, 199,000 (2025) + 163,500 (2026) of revenue is invisible to the tax schedules.
Fix: post these as CNY invoices to customers (顺丰科技 etc.), or add a `发票号` memo and include them in the VAT/经营所得 computation. Also name the contracting party — as booked, a reader cannot tell whether 周子航 (事业单位在编) is the one contracting, which would be 违规兼职.

**L6 (M) — Registered employee 陈宇 has no payroll, no employer 社保, no vouchers.**
Evidence: `employees` row (rate 0, workday 0, no address); zero transactions mention 陈宇; no `工资/人工` expense account exists.
Rule: an 个体工商户 with an employee must pay wages, withhold 个税, and contribute employer 社保 (~15% Shenzhen) monthly. A registered employee with no payroll is either an unpaid worker (illegal) or a phantom record.
Fix: either delete the employee, or add monthly `工资` + `社保 (单位)` + a voucher or two (the voucher module is otherwise unexercised in this book).

**L7 (M) — `订阅: VPN 服务 年付分摊`, 21 entries.**
Evidence: monthly ~25 CNY from 银行储蓄卡.
Rule: unlicensed cross-border VPN use is prohibited for individuals (工信部 2017); a demo book presented as a Shenzhen household should not carry it as a line item.
Fix: replace with 阿里云盘/腾讯视频/QQ音乐 or drop.

**L8 (L) — A-share stock sale without 印花税 or 佣金.**
Evidence: 2025-06-16 `卖出 100 300750 @ ¥246.7` → bank +24,670 exactly. Stamp duty 0.05% (12.34) and broker commission (≥ 5) missing on every buy/sell. ETF trades are correctly stamp-duty-free.
Fix: add 印花税 (sell side, stocks only) and 佣金 splits to 支出:杂项 or a `交易费用` account.

**L9 (L) — 定投 executed on non-trading days.**
Evidence: all 42 ETF 定投 fall on the 1st; 24 of them on weekends/holidays (2025-01-01, 02-01 Sat, 05-01, 10-01, 2026-01-01, 02-01 Sun, 05-01 …). Exchange ETF orders cannot fill on those days.
Fix: roll the 定投 date to the next trading day (or use the 8th/15th and skip A-share holidays).

---

## 2. Implausible

**P1 (H) — Credit cards carry growing balances with no interest.**
Evidence: 招商银行信用卡 charges 2,000–10,600/month, repayment a flat 1,850 on the 25th (the statement-close day, not a due date) for 20 months; balance 12,200 → 44,393. One 滞纳金 50 + 逾期利息 180 event (2025-09-26), nothing else. Slot says `apr 18.25`, `credit_limit 80000`. 工商银行信用卡: last payment 2025-05-22, went into credit (+1,441), then 16 months of 山姆会员店 charges with zero repayment and zero interest; balance 8,551.
Rule: a 招商 card paid below 最低还款额 accrues 0.05%/day on the full balance (~600/month at 40k) and 逾期 hits 征信 within 3 months; no bank leaves 8.5k unpaid for 16 months in silence.
Fix: pay the statement in full most months (a household with 650k/yr net does), or model revolving credit honestly: minimum 10%, monthly interest, occasional full pay-down.

**P2 (H) — 汇丰港币信用卡: 7,460 HKD spent in 2025, 1,000 repaid, 6,460 outstanding 11 months, no interest, `apr 21.0`.**
Evidence: 海港城 3,200 / 莎莎 1,800 / 苹果配件 2,460; single 还款 2025-11-05 of 1,000 HKD via 银行储蓄卡 (`Sell` action on a CNY bank split).
Fix: pay the HKD statement in full the following month (cross-border 购汇 from 银行储蓄卡 at the day's rate), and give the card 2026 activity or close it.

**P3 (M) — HK shopping booked as 食品杂货.**
Evidence: 海港城购物, 莎莎化妆品, 苹果旗舰店配件 → 支出:食品杂货.
Fix: 服装 / 个人护理 / 杂项 respectively.

**P4 (M) — 深圳跨境电商有限公司 retainer vanishes for seven months.**
Evidence: invoice 12,000 on the 1st and payment on the 28th every month Jan–Dec 2025; nothing Jan–Jul 2026; then 15,000 (08-17) and 9,000 (09-07). USD and EUR customers likewise silent Jan–Jun 2026. 个体经营收入 2026 YTD 112,899 vs 313,126 in 2025; May-2026 net −6,288.
Fix: either taper the retainer with a memo (合同到期/续签) or keep it running; keep 2026 invoicing cadence comparable to 2025 unless the narrative is "business downturn", which nothing in the book says.

**P5 (M) — No seasonality in daily life.**
Evidence: 春节 week 2025 (01-28 → 02-04): 7× 瑞幸咖啡, 8 外卖, 剧本杀, 优客工场, 健身 — a normal Shenzhen work week while the household books `春节旅行 回乡` on 02-10 and `春节回乡 高铁` on 02-17 (both after 元宵). 国庆 week 2025: 6× 瑞幸, 7× 美团外卖, 优客工场 alongside `国庆节旅行`. `春节红包` on 02-01 both years; 2026-02-01 is 16 days before 春节 (02-17).
Fix: drive holiday dates from the lunar calendar (春节 2025-01-29, 2026-02-17); during 回乡/旅行 windows suppress Shenzhen daily spend and add hometown spend; put 红包 on 除夕/初一.

**P6 (M) — Mortgage rate 3.85% and no 专项附加扣除.**
Evidence: `房屋贷款` slot apr 3.85; interest 8,983 → 8,622 (correct 等额本息 shape). 个税 uses only 5,000 起征点 + 三险一金 (verified: 217.50, 280.01, 825.40 all reproduce under 累计预扣法).
Rule: Shenzhen existing first-home mortgages repriced to ~LPR−30bp ≈ 3.3% from Oct-2024; a couple with a 2.8M mortgage claims 住房贷款利息 1,000/月 (and likely 赡养老人).
Fix: apr 3.3–3.5; add 专项附加扣除 to the 个税 model (drops monthly tax to ~117.50 in low months).

**P7 (L) — 储蓄账户 150,000, 现金 2,000, 住房公积金: no interest, ever.**
Evidence: 储蓄账户 and 现金 have exactly one split each (opening). 公积金 balance = opening + contributions; no 6-30 结息 in 2025 or 2026.
Fix: annual 公积金 结息 (1.5%, June 30); quarterly 储蓄 利息; a couple of cash withdrawals/uses.

**P8 (L) — 双职工 without 年终奖/绩效, 职业年金, or any 商业保险.**
Evidence: 周子航 21 identical 15,000 months (+ overtime); no 13th month; no 职业年金 4% (事业单位); 支出:保险:人寿保险 / 医疗保险 zero for 21 months; 汽车保养 zero; 房屋维修 zero.
Fix: one 年终绩效 in Jan/Feb; add 职业年金 to the payroll split; a 百万医疗险 and one car service per year.

**P9 (L) — Beijing convenience-store vocabulary in Shenzhen.**
Evidence: 便利蜂 219 visits (the chain has essentially no Shenzhen footprint), 全家 204 (thin in Shenzhen); no 美宜佳, 天虹微喔, 朴朴, 叮咚/美团买菜. 餐饮 is three names only (瑞幸 527, 美团外卖 342, 饿了么 61) — no restaurant, no 茶餐厅/早茶/火锅 in 21 months.
Fix: swap 便利蜂 → 美宜佳, add 2–4 sit-down restaurant names and a 早茶 weekend habit.

**P10 (L) — 自动贩卖机 paid 68× from 银行储蓄卡.**
Evidence: vending-machine 5–20 CNY debits from the bank card; every other small spend uses 微信/支付宝.
Fix: route to 微信支付.

**P11 (L) — 滴滴出行 and 共享单车 booked under 汽车:停车费.**
Evidence: 132 of 152 splits in that account are ride-hailing / bike-share.
Fix: a `支出:交通` account (打车、共享单车、地铁).

**P12 (L) — 车辆年检 300/yr booked to 汽车保险; 宠物 quarterly 体检.**
A car this new is 免检 (online 申领 at years 2/4/6, no fee); a cat gets one 体检 a year.
Fix: drop or biennialise 年检 (book to 汽车:杂项); make 体检 annual and keep 疫苗.

**P13 (L) — 办公新显示器 2,800 and 办公用品 450 booked to 经营支出:软件.**
Fix: 经营支出:办公设备 / 办公用品.

---

## 3. Generation artifacts

**A1 (H) — Rigid weekday schedules.**
Evidence (weekday counts Mon→Sun): 瑞幸咖啡 [99,102,101,99,99,11,16]; 美团外卖 [9,101,9,103,12,99,9] (Tue/Thu/Sat only); EV充电 90/90 on Wednesday; 娱乐 [27,25,1,0,1,0,18] — bars, KTV, 密室逃脱, 欢乐谷 only Mon/Tue/Sun, never Fri/Sat.
Fix: sample weekdays from a distribution (nightlife weighted Fri/Sat, charging any day), not a fixed slot.

**A2 (M) — Bill module double-counts card charges.**
Evidence: 优客工场 bills 2025-02-03 and 08-03 (1,500 each) AND a 招商 card charge `优客工场 联合办公` on the 1st of every month; 阿里云 bills 04-02 and 10-02 (350) AND monthly card charges on the 3rd. 联合办公 2025 = 23 × 1,500 (21,000 for 12 months); 云服务器 = 23 × 350.
Fix: if the vendor is paid by card, don't also bill it; if the point is to exercise bills, skip the card charge in those months.

**A3 (M) — `经营所得 季度预缴` schedule not advanced after its instance posted.**
Evidence: `schedxactions.last_occur = 2026-06-14` while transaction `8fab8a58` (2026-09-14, 6,322.90) exists; dashboard shows "Overdue scheduled: 经营所得 季度预缴 due 2026-09-14". The sibling `增值税及附加` schedule was advanced (last_occur 2026-09-12).
Fix: the generator must stamp `last_occur` (and the `from-sched-xaction` slot) for every posted instance; add a build-time check `last_occur == max(posted instance date)`.

**A4 (M) — Gift/family events repeat implausibly.**
Evidence: `婚礼随礼 (表妹)` ×3 (2025-11-09, 2026-03-23, 2026-06-04); `婚礼红包 (前同事)` ×2; `婚礼红包 (老乡)` ×2; `满月红包` ×6; `探病果篮` ×4.
Fix: draw event names without replacement (or index them: 表妹A/表哥/同学…).

**A5 (M) — 山姆会员店 exactly on the 12th, 21/21 months; retainer invoice on the 1st / payment on the 28th, 12/12.**
Fix: jitter ±3 days; skip the invoice when the 1st is a holiday (2025-01-01, 2025-10-01, 2026-01-01).

**A6 (L) — English strings in a zh_CN book.**
Evidence: `期初余额 (Opening Balances)`; lot titles `沪深300 core position`, `创业板 growth position`, `期初持仓 … (opening position)`; FX memos `FX gain on invoice 000015: post-rate 7.2634, pay-rate 7.2821` (server-generated); `错误供应商 (Wrong Vendor) 付款`; `510300 2025-11-18 purchase`; 宁德时代 lot note `200 份` (stocks are 股).
Fix: localise generator strings; the FX memo is a server-side i18n gap worth a separate ticket.

**A7 (L) — Reconciliation frozen at 2025-03-30.**
Evidence: 银行储蓄卡 146 `y` splits, all ≤ 2025-03-30; 816 `n`; every other account never reconciled. Dashboard: "18 months behind".
Fix: reconcile the bank through the prior month-end at build time; leave one open month as the demo's work item.

**A8 (L) — Flat amounts where a real bill varies.**
Evidence: 停车月卡 800, 联合办公 1,500, 云服务器 350, 物业管理费 850, 视频会员 45, 网络费 199, 话费 128 — 1 distinct amount each over 20–23 months. Most are genuinely fixed; 云服务器 (usage-billed) and 话费 (overage) should wobble.

---

## 4. What's right

- **个税 累计预扣法 is exact.** Every monthly figure reproduces (217.50 base months, 280.01 when cumulative crosses 36,000 in May, 825.40 in the June overtime month, reset in January). This is the single hardest thing to get right and it is right.
- **Spouse on the payroll, business on 林微.** G6's persona clash is gone; 周子航 (深圳市人民医院) is the salaried one, 陈宇 is not the spouse.
- **Chart names are native**: 银行储蓄卡, 微信支付, 支付宝, 住房公积金, 增值税及附加, 个人经营所得税, 社会保险, 礼金, 物业管理费; WeChat for utilities/donations, Alipay for pet/online clothing, card for business — the rails match how a Shenzhen household actually pays.
- **Cross-currency A/R is correct**: USD/EUR invoices post at the day's rate, payments realise FX gain/loss (−56.10, −351.25, +49.20 … all reproduce from the quoted rates), receivables revalue on the balance sheet at 6.7082 / 7.7762.
- **Price history is real-looking**: USD/CNY 7.30 (Jan-25) → 7.13 (Sep-25) → 6.72 (Sep-26), EUR 7.58 → 8.26 (May-25) → 7.79, HKD tracks the peg (USD/7.78), 510300 3.91 → 4.62, 159915 2.02 → 3.36, 300750 258.8 → 330.5; `source = user:market-data`.
- **A-share lot sizes** — every stock/ETF trade and lot is a multiple of 100; the CATL sale at a loss (−1,210) and the ChiNext partial sale at a gain (+2,200) both compute correctly against cost basis.
- **Loans amortise properly** (等额本息: interest 8,983 → 8,622, principal rising; car loan 490 → 330 interest at 4.9%).
- **Seasonality where the generator tried**: 电费 125 → 532 in summer; 年货采购 in January; 春节红包 6,000; 618 / 双十一 orders; 99公益日 配捐 on 9/9 and 腾讯公益 月捐 on the 8th; 中秋月饼礼盒 in September; business trips to the two foreign customers.
- **Overdue invoices staggered** 33 / 12 / 1 days; the book opens with warnings a bookkeeper would actually act on.
- **Balance sheet closes**: A 5,145,610.34 = L 2,818,399.96 + E 2,327,210.38 (期初 1,838,070 + RE 463,502.70 + Unrealized 25,637.68); retained earnings reconcile to income − expense.

---

## 5. Versus G1–G11 (read after the cold pass)

| Item | Status | Note |
|---|---|---|
| G1 odd lots | **Fixed** | All lots ×100; ETFs carry the exposure. |
| G2 营业税 | **Fixed** | Accounts are now 增值税及附加 / 个人经营所得税 — but the *amounts and months* are wrong (L1, L2, L3). Renaming exposed the next layer. |
| G3 支票账户 | **Fixed** | 银行储蓄卡. |
| G4 monthly 车险 | **Fixed** | Annual 5,400 on 05-20; 年检 300/yr remains a small oddity (P12). |
| G5 陈宇工资 | **Regressed** | The wage is gone but the employee record stayed — now a phantom employee with no payroll, no 社保 (L6). Section 3 of the old review ("Lin Wei — phantom employee") is back in a new form. |
| G6 hospital/side-business clash | **Fixed** | Option (a): spouse 周子航 on payroll, not named 陈宇. Residual: 承包收入 receipts don't name the contracting party (L5). |
| G7 everything on the 15th | **Fixed** | Schedules scattered across the 1st–28th. Replaced by day-of-*week* clustering (A1). |
| G8 flat utilities | **Fixed** | 电费 seasonal, 水/燃气 noised, broadband/phone flat. |
| G9 contracting every 4th month | **Fixed** | Irregular gaps in 2025. New: the retainer customer disappears for seven months of 2026 (P4). |
| G10 all overdue 33 days | **Fixed** | 33 / 12 / 1. |
| G11 pet clockwork | **Fixed** | Amounts noised; 体检 frequency still quarterly (P12). |

**New since G1–G11** (not in the old list): wrong filing months and sub-threshold VAT (L1, L2), 经营所得 not tied to profit (L3), floating 社保 base (L4), un-invoiced big-company receipts (L5), VPN line (L7), missing 印花税 (L8), 定投 on closed days (L9), interest-free revolving cards (P1, P2), no holiday seasonality in daily spend (P5), the bill/card double-count (A2), the stale `last_occur` (A3), repeated named events (A4), and the rigid weekday schedules (A1).

**Suggested order of work:** (1) tax engine from the ledger — months, exemption, 经营所得, 汇算清缴 (L1–L3, one commit); (2) payroll base + employee (L4, L6); (3) credit-card honesty (P1–P2) and the bill double-count (A2); (4) calendar realism — weekday sampling, lunar holidays, trading days, `last_occur` (A1, P5, L9, A3); (5) vocabulary/categorisation sweep (P3, P9–P13, L7, A6).
