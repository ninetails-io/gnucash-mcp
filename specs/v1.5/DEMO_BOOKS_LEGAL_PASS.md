# Demo books — the legal pass (Sep 11–15, 2026)

Three new bases for Alex, Lin Wei, and Sabine that pass a domain
audit by an AI reading them as a tax professional in each country.
The findings for Lin Wei (G1–G11) and Sabine (S1–S11) are on record
in `testing/BOOKKEEPER_REVIEW_DEMO_GENERATORS.md` §6–7 (Gemini,
2026-09-01, bookkeeper-verified). Alex has never been audited; an
IRS-minded read runs today and files at
`testing/AUDIT_ALEX_IRS_2026-09-11.md`.

## 0. Two rulings needed before the first build

1. **Re-committing the bases.** The 2026-08-30 ruling says the
   committed blobs are the permanent prefix and "zero new blob
   bytes ever enter history." This pass replaces the prefix: three
   new books, ~6.7 MB of new blobs in every future clone. It is the
   case the release checklist reserves for "when phase scripts gain
   coverage" — a deliberate, capture-rig-invalidating event — and it
   needs the maintainer's word as such. (Alternative that honors the
   old ruling: keep the blobs and fix everything through the
   continuation's repair-in-narrative layer. Not viable here: the
   fixes are structural — accounts renamed, a persona's employer
   changed, VAT mechanics — not narrative.)
2. **Re-anchoring.** Every bookkeeper-validated report number on the
   samples changes. The capture rigs re-anchor on generated output
   pinned by (generator version, cache version, --through), per the
   generator spec §3. No test pins sample contents (two tests name
   the files; neither reads them).

## 1. What "passes" means

Each new base is handed cold to a domain-auditing model (the same
kind of read that produced G1–G11 / S1–S11) with the instruction
"audit this as a <country> tax professional." Pass = no finding an
auditor would call illegal or impossible; implausibilities are
allowed if the persona doc names them as deliberate hooks (Sabine's
€48.50 stays). The bookkeeper reads the audit and rules per finding,
as on 2026-09-01.

## 2. Work, per persona

### Sabine (SKR03, EUR) — the tiers as ruled on 2026-09-01

- **A — regenerate with current code + data fixes:** entry dates =
  document dates (S4, a 1.4.4 fix the frozen base predates);
  chronological sequential invoice numbers (S5 — §14 UStG / GoBD;
  this is the "illegal" one); customer master data — street, city,
  well-formed-but-invalid USt-IdNr. (S7).
- **B — the scope commit:** one book, two zones. A Privat branch
  holds the residence, the mortgage and its interest, and the ETF;
  connected through Privatentnahmen/-einlagen. Resolves S1 (mortgage
  interest out of 2110 — deducting private mortgage interest against
  the business is the other "illegal"), S10 (ETF to Privat), S3 (Pkw:
  business asset with monthly 1%-Regelung private-use imputation).
- **C — mechanics:** monthly USt-Voranmeldung with Zahllast = 1776 −
  1576 from the actual monthly figures, cleared through the bank
  (S2 — Vorsteuer accumulating forever reads as VAT never remitted);
  year-end AfA for the Pkw (S8); Drittland service revenue to 8338,
  EU B2B to the Reverse-Charge accounts (S6).
- **D — keep with a note in the persona doc:** no hard year-end close
  (S9); the €48.50 Unklare Lastschrift (S11).

### Lin Wei (CNY, zh_CN)

- **Rename-and-reschedule (G2, G3, G4):** 营业税 → 增值税及附加 +
  个人经营所得税 (abolished 2016 — the anachronism a native reader
  trips on first); 支票账户 → 银行储蓄卡; 车险 annual (~¥5,400).
- **Persona (G6 — the "illegal" one):** a public-hospital employee
  cannot run the side business. Ruled option (a): the ¥15,000 salary
  with social-insurance and 公积金 withholding becomes a spouse's
  (dual-earner household), spouse NOT named 陈宇 (that is the
  registered employee, G5 stands). Lin Wei's own income is the
  e-commerce business.
- **Portfolio (G1):** A-shares in 100-share lots (一手) of tickers her
  income supports, or exposure via the two ETFs already in the book
  (no lot minimum). Recommendation: ETFs.
- **Jitter pass (G7–G11):** scatter schedule days (salary 10th,
  mortgage 18th, utilities ~25th); seasonal 电费 curve with noise;
  vary the contracting-income gap pattern; stagger overdue-invoice
  ages; noise the pet amounts (字节 the cat stays).
- **Cross-border check (new, from the legal angle):** USD/EUR
  receivables into a mainland account — settlement should read as
  结汇 through the bank with the FX booked, not as foreign currency
  spent domestically. Verify in the audit; fix only if flagged.

### Alex (USD) — audited 2026-09-11 (`testing/AUDIT_ALEX_IRS_2026-09-11.md`)

Three findings a CPA would refuse to sign, none of which were on the
suspect list: a **wash sale** booked as a deductible loss (VBTLX sold
at a loss 12/15, bought back 12/16 — A1); a **W-2 employee with no
payroll** — no wages, withholding, employer FICA/FUTA, or WA
remittances (A2); **estimated tax at half the liability**, no SE tax,
no April balance due (A3). The suspects were wrong in the useful
direction: estimated tax is already correctly a household item (D1),
and the LLC's spend is mostly on the business card (D7). What an IRS
reader flags next: business spend on the personal card (A4), WA
PFML/Cares/L&I missing from paychecks (A5), no B&O tax (A7), a card
$5,880 over its limit (B1), full LLC/household commingling with no
owner equity (B3), non-chronological invoice numbers (B8), no
retirement plan (B7), idle cash earning nothing (B6). Plus the usual
artifacts: fixed-day calendar, verbatim yearly replay, 1st/15th
pile-up. Work order is the audit's own four days: legal → entity
boundary → plausibility → artifacts.

## 3. Sequence (four days, beside the release)

| Day | Books | Release |
|---|---|---|
| Sep 11 | Alex audit files; rulings 0.1 and 0.2; Sabine tier A + Lin Wei renames (mechanical, no design) | cache refresh; README version pass |
| Sep 12 | Sabine tiers B–C; Lin Wei persona + portfolio + jitter; Alex fixes per audit | CHANGELOG title + lede once named |
| Sep 13 | `rebuild_all.py` (refresh → build → verify → promote); cold domain audits of all three new books; bookkeeper rules per finding | — |
| Sep 14 | fixes from the audits; final rebuild; bookkeeper fleet review (the 2026-08-31 checklist: warnings, reconciliation posture, hooks); commit bases + README counts | bump with lock (last) |
| Sep 15 | — | release PR, merge, tag, bundle, Glama |

The bundle ships whatever `samples/` holds at the tag, continued
through build day by CI. New bases must be committed before the
bump.

## 4. Order of work inside each generator

Sabine first this time (smallest generator, most findings ruled
already, both "illegal" items live there). Then Lin Wei. Alex last,
because its list arrives last and its generator is the largest.

## 5. Out of scope

- Simulating a year-end close (S9 — GnuCash convention).
- A tax layer in the server (standing never).
- New features in the generators beyond what the audits name.

---

## Ruled 2026-09-17 — no committed books at all

Ruling 0.1 resolved by elimination. A chart-only base was built and
verified (~230 KB per persona) and then dropped before it was ever
pushed: the chart is code, so a committed copy of it is a cached
copy of code, and a cached copy is the thing that drifts. Nothing
under `samples/` is committed but the README. The builders create
each book from nothing (`piecash.create_book`) and generate every
transaction deterministically; CI builds the three books at bundle
time, the Glama image at image build, and a clone with
`rebuild_all.py --skip-refresh`. This retires the frozen prefix, the
continuation-as-updater path for committed books, the pre-commit
blob guard's only exception, and the last binary blobs in the tree.
Each builder keeps `--chart-only` as a seconds-long validity mode,
exercised by `tests/test_demo_bases.py`. Ruling 0.2 stands: report
numbers re-anchor on (generator version, cache version, --through).

## Round 2 — 2026-09-17, against the cold audits

Three cold audits of the round-1 builds (`testing/AUDIT_*_COLD_2026-09-17.md`)
found the chart-and-persona layer fixed and the layer under it exposed:
tax mechanics derived from constants instead of the ledger, and cards
that spiral wherever a builder does not run the policy engine from
day one. Round 2, one commit per persona on the branch:

- **Sabine** — a payee → tax-treatment table (domestic 19%/7%,
  exempt, §13b EU/Drittland, Bewirtung 70/30 with notes, private)
  that every variable row goes through; the USt-VA clears all six VAT
  accounts; ESt-Vorauszahlungen and settlements from the EÜR; every
  revenue receipt invoice-posted (Sollversteuerung); Hypothekenzinsen
  as a drawing under Privatkapital; bank postings on Bankarbeitstage.
- **Lin Wei** — VAT/附加 and 经营所得 computed from posted revenue
  and expenses by quarter (small-scale exemption, cumulative method,
  汇算清缴) and filed the month after quarter end; fixed 社保/公积金
  bases; 陈宇 on a real part-time wage with employer 社保 and
  vouchers; the policy engine from 2025-01-01 with statement-balance
  card payments, interest where a balance carries, HKD repaid via
  购汇; contracting receipts as 专票 invoices; weekday sampling,
  lunar 春节, 印花税, trading-day 定投.
- **Alex** — the two remaining arithmetic lines (Seattle B&O on the
  whole apportioned base once over the threshold; the April
  settlement derived with sign from one household model); WA B&O
  apportioned to WA clients; the shared realism items; the
  reconciliation and business-day helpers moved into
  `continuation.py` so every builder inherits them.
- **All three** — `enter_date` = posting day; four stability
  invariants in each builder's `verify()` over every month-end
  (cards ≤ limit, cash within band, tax paid within 15% of the
  ledger's implied liability, no document past terms + 45 days),
  proven on builds through 2030-12-31: 72 month-ends, all four hold,
  in each book.

A second set of cold audits, scoped to "would not sign" and
high-severity implausibility only, runs on the round-2 books and is
the pass criterion.

## Round 3 — 2026-09-17, against the second-pass audits

Scope "would not sign" only. Seven items across three books, all
persona rules or tax-model inputs, none structural. Landed as three
commits (3abb1a9, 3a14470, a3bcb02): Sabine's gift cap per recipient
and year with breaches to 4665, one 7% licence per work and only to
the publisher, a tax-reserve draw from Postbank before each ESt due
date; Lin Wei's wallets sized to spend and topped up on demand,
discretionary buys gated on the checking floor, client trips as 差旅,
教育费附加 exempt under the threshold; Alex's 1040 prepared from the
ledger's own rows (Schedule B and C from the book, the settlement
note listing every line), the WA B&O small-business credit ($0
returns still filed), federal holidays in the business-day calendar.
Every builder's verify() now also asserts no bank or cash account
below zero at any day-end. Each round-3 build reproduced by the
maintainer's session before landing; the merged engine's five-year
builds and a third cold pass are the remaining gates.

## Rounds 4–5 — 2026-09-17, the identifier layer

A fourth reader (Gemini, `docs/SYNTHETIC_BOOKS_TAX_AUDIT_REPORT.md`)
and the third-pass audits converged on the last layer: statutory
detail a native filer's bank or tax portal would reject, none of it
moving a balance. Alex: the settlement note states the accrual
method; WA B&O applies the throw-out rule; the Seattle return shares
that premise (both factors 1.00); the LLC top-up comes from personal
checking; engine transfers fall on business days. Sabine: the device
catalogue draws without repeats at real prices with printers among the
Peripheriegeräte; a December-2024 tail; florists at 7%; every fill-up
a business expense. Lin Wei: a 对公账户 for invoices, 结汇 and tax
filings with owner's draws; the payroll row withholds and remits;
技术服务收入 under a registered 个体工商户; 免税备案 and 涉外收入申报
references; 发票 notes on every expense; then (round 5) the SAFE
services code, 数电票 numbering, a checksum-valid credit code, one
备案 per contract, and 普票 references on the retainer.

Fourth-pass verdicts: Sabine würde unterschreiben; Alex signs once the
Seattle return matched (fixed); Lin Wei passes once the identifiers do
(round 5). All rounds landed by the maintainer's session after
reproducing each build; five-year builds hold every invariant on the
final engine. What remains is the desktop gate and the release
checklist.
