# Sample Books

Three synthetic GnuCash personas ship with the server as **code**:
nothing under `samples/` is committed but this file. The builders
under `scripts/synthetic_book/` create each book from nothing and
generate years of activity deterministically, priced from the
committed market-data cache, so the demo a stranger opens was built
by current code on GnuCash's own storage shapes, through the day it
was built. The MCPB bundle and the Glama image build them at build
time; a clone builds them with one command.

These books are **fictional**. Names, addresses, tax IDs, account
numbers, and amounts are invented; tax IDs are well-formed and
deliberately invalid. Use them freely.

## Building the books

```bash
uv run python scripts/synthetic_book/rebuild_all.py --skip-refresh
```

builds all three through today, verifies each, and places them at
the canonical paths in `samples/` (ignored by git). CI does the same
for the MCPB bundle and the Glama image on every build.
`--through YYYY-MM-DD` pins the timeline; `--only alex` builds one;
`--no-promote` leaves the results at `samples/*.generated.gnucash`.
To bring a built book forward later without rebuilding,
`continue_book.py` extends it from its last day. Each builder's
`--chart-only` writes just the chart (seconds); `tests/test_demo_bases.py`
checks that chart on every run.

## alex-chen-morales.gnucash

Seattle software contractor with a single-member LLC (Cascade Code
LLC) and a spouse on a hospital payroll. **USD default**, with EUR
and CAD receivables. Built: about 2,150 transactions over 2025 to the
build date, 111 accounts.

- W-2 paycheck with federal, Social Security, Medicare, Washington
  PFML, WA Cares and L&I deductions, a 403(b) deferral and match;
  the LLC pays quarterly estimated tax sized from its income, plus an
  April balance due
- The LLC has its own checking account; monthly owner's draws to
  the household; the April 1040 settlement prepared from the ledger
- Customers in USD, EUR (Berlin Digital GmbH, the cross-currency FX
  regression case) and CAD; a 1099 subcontractor billed through A/P;
  Washington B&O tax and the Seattle license
- Brokerage lots in VTSAX, VBTLX, AAPL and MSFT, an ETH position, an
  HSA, a Solo 401(k); dividends and interest scale with holdings
- A mortgage, an auto loan, two credit cards paid at the statement
  balance, with interest in the months a balance carried
- Scheduled transactions, two annual budgets, reconciliation through
  the last full month

Audited as an IRS-minded read four times, 2026-09-11 to 09-17
(`specs/v1.5/testing/AUDIT_ALEX_COLD_R3_2026-09-17.md`).

## lin-wei.gnucash (林微)

Shenzhen cross-border e-commerce seller whose spouse (周子航) is on a
hospital payroll. A **native zh_CN chart, CNY default**, with USD,
EUR and HKD in the book. Built: about 2,950 transactions, 101
accounts.

- A registered 个体工商户 with a 对公账户 for invoices, 结汇 and tax
  filings, owner's draws to the household; WeChat Pay and Alipay
  beside a debit card (银行储蓄卡); 住房公积金 on the spouse's salary
- Quarterly VAT and surcharges (增值税及附加) and personal business
  income tax prepayments
- USD and EUR receivables in a CNY book; an HKD credit card; a USD
  vendor bill (the two mandatory FX regression cases)
- A-share and ETF holdings in round lots; a part-time employee (陈宇)
  with withheld 社保 and expense vouchers; every business expense
  carries a 发票 note
- Seasonal utilities, scattered schedule days, and a cat named 字节

Audited as a Chinese household four times, 2026-09-01 to 09-17
(`specs/v1.5/testing/AUDIT_LIN_WEI_COLD_R3_2026-09-17.md` passed;
cross-model read in `docs/SYNTHETIC_BOOKS_TAX_AUDIT_REPORT.md`).

## sabine-brenner.gnucash

Munich freelance designer, one book in two zones: **SKR03 business
ranges** feeding the EÜR, and a private branch for the residence, its
mortgage and an ETF, connected through Privatentnahmen and
Privateinlagen. **EUR default**, USD for a Drittland client. Built:
about 1,850 transactions, 125 accounts.

- Live VAT: six input and output VAT accounts (19%, 7%, §13b) cleared
  by a monthly USt-Voranmeldung computed from the book's own figures;
  a payee → tax-treatment table behind every expense row
- Sequential invoice numbers in date order; customers with master
  data; Drittland and EU reverse-charge revenue on their own accounts
- The Pkw as a business asset with the monthly 1%-Regelung and
  year-end AfA; a Kfz loan
- Three schedules, a budget, and the €48.50 "Unklare Lastschrift
  (noch zu klären)" the dashboard flags: the onboarding hook, kept on
  purpose

Audited as German tax books four times, 2026-09-01 to 09-17
(`specs/v1.5/testing/AUDIT_SABINE_COLD_R3_2026-09-17.md`: would sign).
The i18n oracle: numbered accounts defeat English-name matching, so
every server path that resolves accounts by type is exercised here.
