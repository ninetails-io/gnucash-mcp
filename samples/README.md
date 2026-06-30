# Sample Books

Two synthetic GnuCash books that ship with the MCP server. Each
represents a fully-populated persona with realistic multi-year
activity — enough to exercise every tool the server exposes, plus
enough variety (currencies, languages, business activity, lots,
budgets) to act as living documentation of what the server can do.

These books are **fictional**. The names, addresses, account
numbers, and amounts are invented. Use them freely.

---

## alex-chen-morales.gnucash

Seattle-based independent software contractor with a US LLC. Tests
the **USD-default** path through every feature the server supports.

- **Default currency:** USD (with EUR, GBP, CAD also in the book)
- **Accounts:** 141
- **Transactions:** ~2,475
- **Period covered:** 2025–2026

What's in here:

- W-2 income from a primary employer plus contractor income through
  the LLC
- A brokerage account holding **VTSAX**, **VBTLX**, **AAPL**, and
  **MSFT**, plus a small **ETH** position — cost-basis tracking
  via lots
- A 401(k) with employer match
- A mortgage, a car loan, two credit cards
- **Four customers** spanning USD, EUR, GBP, and CAD: Emerald
  Analytics, Sound Transit Data Team, Berlin Digital GmbH, and
  Nord Analytique. The cross-currency invoices exercise the
  realized FX gain/loss path on rate drift between post-date and
  pay-date.
- **Two vendors** (JetBrains, BookkeepingCo) and **one employee**
  (Sam Rivera) — exercises the create_employee path
- Recurring scheduled transactions for rent, utilities, payroll,
  loan payments
- A monthly budget with mid-year revisions
- Reconciliation history on the checking account through 2025-Q4

This is the canonical "all-features" book — if you want to see what
``get_book_summary`` looks like with every section populated, open
this one.

## lin-wei.gnucash (林微)

Shenzhen-based small-business owner running a cross-border e-commerce
operation. Tests the **CNY-default** path with multi-currency
activity, validating that the server works end-to-end on a
non-USD-default book.

- **Default currency:** CNY (人民币 — with USD, EUR, HKD also in the book)
- **Accounts:** 105
- **Transactions:** ~1,960
- **Period covered:** 2025

What's in here:

- **Three customers** mixing scripts and currencies:
  深圳跨境电商有限公司 (CNY, exercises the audit log's UTF-8
  handling), Pacific Trade Solutions (USD), Handelskontor München
  GmbH (EUR). The foreign-currency invoices exercise the FX
  gain/loss recognition path on rate drift between post-date
  and pay-date.
- **Three vendors** also bilingual: 阿里云 Alibaba Cloud, JetBrains,
  and 优客工场 UrWork
- Domestic Chinese investments: 茅台 (Moutai, ticker 600519),
  宁德时代 (CATL, ticker 300750), and ETFs (沪深300 / 510300 and
  创业板 / 159915)
- A mortgage at LPR-based 3.85% APR, an auto loan at 4.9%, two
  domestic credit cards (CMB, ICBC) — exercises the
  ``debt_payoff_plan`` amortization path for non-USD books
- Mixed payment platforms: traditional checking + 支付宝 (Alipay)
  + 微信支付 (WeChat Pay)

Built specifically to surface non-USD-default bugs — the multi-
currency correctness sweep in v1.2.1 used this book as the
verification harness.

---

## sabine-brenner.gnucash

Munich freelance Grafikdesignerin (Einzelunternehmerin) running the
authentic DATEV **SKR03** chart. Tests the **natively localized
account hierarchy** path: German, numbered account names with **no
English account named "Income"/"Imbalance"/"mortgage"** anywhere, so
every tool that keys off an English name is exercised against a book
where that assumption fails.

- **Default currency:** EUR (with USD in the book for a US client)
- **Accounts:** 110 (the shipped `acctchrt_skr03` verbatim + a few
  German-named additions: Hypothek, Kfz-Finanzierung, fixed assets,
  an ETF depot, a localized Ausgleichskonto)
- **Transactions:** ~220
- **Period covered:** 2025 → 2026

What's in here:

- **German VAT (USt) invoicing** through taxtables — 19% on design
  services (`8400 Erlöse USt. 19%`) and the reduced 7% rate on
  licensing/Nutzungsrechte (`8300`), with output VAT to `1776`/`1771`
  and reclaimable input VAT (Vorsteuer) to `1576`.
- **A USD-paying export client** (Lumen Labs Inc.) — invoiced in USD,
  posted and paid at different EUR/USD rates, so realized FX gain/loss
  is recognized into a top-level-INCOME child **resolved by type**
  (`Erlöse u. Erträge 2/8:Foreign Exchange Gain/Loss` — English leaf,
  because locale inference correctly declines on a numbered chart).
- **Sole-proprietor equity flows:** monthly Privatentnahme instead of
  salary; opening balances via the SKR03 `Anfangsbestand`.
- **A Hypothek and a Kfz-Finanzierung** with `apr` + `loan_term_months`
  slots — exercises `debt_payoff_plan` with no English "mortgage"
  keyword in sight.
- **A localized Ausgleichskonto-EUR** carrying a small balance, so the
  dashboard's data-integrity warning fires on a *German* Imbalance
  name (the structural Tier-C detector).

Built to convert the i18n bug class from invisible to a failing test:
net worth agrees to the cent across `get_book_summary` /
`balance_sheet` / `net_worth`, all classification is by account type,
and the cross-currency FX path settles instead of throwing.

---

## How to point the server at a sample book

```bash
export GNUCASH_BOOK_PATH=$(pwd)/samples/alex-chen-morales.gnucash
uv run python -m gnucash_mcp
```

Or for Claude Desktop / Claude Code, point the configured book path
at the file. **You probably want to copy the file out of the repo
first** — the server writes audit logs and creates auto-backups
alongside the book file, and you don't want either committed back.
A workflow that works:

```bash
cp samples/alex-chen-morales.gnucash ~/scratch/alex.gnucash
export GNUCASH_BOOK_PATH=~/scratch/alex.gnucash
```

---

## How these were built

Both books were generated by the phase scripts in
``scripts/synthetic_book/`` (one persona per script set) and
extensively iterated by hand against the live MCP server during
v1.2.1 development. Re-running the scripts from scratch produces
deterministic output — so if you want to see the exact pipeline
that built these, that's the recipe.

The books shipped here are the post-iteration state, vacuumed via
``sqlite3 ... 'VACUUM'`` so SQLite's free-page list doesn't
inflate the file size. They've passed ``PRAGMA integrity_check``.
