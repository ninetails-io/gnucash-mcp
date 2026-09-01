# Demo Book Generators — Bookkeeper Review (2026-08-31)

> **Post-fix verification (same day, via installed bundle):** Items 1–4 all confirmed fixed in the packed samples — Alex reconciled through 2026-07-31 with no_reconcile exclusions on the loans, Lin Wei's 陈宇 registered as employee, Sabine carrying 3 schedules + budget. Fleet is current in the bundle. **One new item below (5).**

## 5. Price dating — staleness time bomb (small, real)

Sabine's summary now opens with `⚠ Stale price: IWDA.AS last updated 31 days ago` — absent at this morning's review, present by afternoon. The generator writes final prices at a month-ish boundary while anchoring data "to today," so the 30-day staleness threshold trips on or shortly after generation day. Every fresh install will grow this warning within days.

**Ask:** date each commodity's final generated price at generation date (or generation-date minus 0–2 days) so a fresh bundle opens warning-free, and the stale-price warning appears only when a book has genuinely aged — at which point it's a *feature* (it hands the first conversation "want me to fetch fresh quotes?" as a hook).

Reviewer: the household bookkeeper, via `get_book_summary` on all three regenerated books.
Verdict: **ship-worthy after item 1.** Items 2–3 are nits; 4 is optional parity.

The books are three financial cultures, not one book translated — keep that. Data range rolling to today is exactly right.

## 1. Reconciliation posture (hold the ship for this one)

**Problem:** A stranger's first `get_book_summary` opens with warnings.
- Alex: checking reconciled through 2025-03, then `1087 splits / 17 months behind ⚠`, `6 accounts never reconciled ⚠`
- Lin Wei: same shape — `821 splits / 17 months behind ⚠`, `9 accounts never reconciled ⚠`
- Sabine: nothing reconciled at all — `7 accounts never reconciled ⚠`

The demo household of a reconciliation tool should not be seventeen months behind on reconciliation.

**Ask:** Reconcile every bank/card account in all three books **through the last full month** (relative to generation date — currently 2026-07-31), leaving the current month open.
- Shows what a well-kept book looks like.
- Leaves the first conversation a natural next move: "August's statement is ready to enter" — the certified `enter_statement` demo path.
- Consistent posture across all three books (today they're three different postures).

## 2. Overdue scheduled transactions — hooks aged wrong

Overdue schedules as onboarding hooks are a good idea, but the aging reads as neglect:
- Alex: `Estimated Tax Payment due 2026-07-15` — six weeks overdue on a prosperous LLC owner's federal quarterly.
- Lin Wei: `陈宇工资` and `物业管理费` due 2026-08-15 — two weeks.

**Ask:** Date overdue items within the last ~3–7 days of generation date ("just came due"), or drop them and rely on the *due in next 7 days* line — Alex's `1 due in next 7 days (USD 3,269)` is the good hook. One hook per book is plenty.

## 3. Lin Wei — phantom employee

Scheduled `陈宇工资` (salary) exists, but the business register shows `3 customers, 3 vendors` and **zero employees**.

**Ask:** Either register 陈宇 as an employee, or rename the schedule to a contractor payment routed through a vendor.

## 4. Sabine — feature parity (optional)

Sabine carries no scheduled transactions and no budget; Alex and Lin Wei have both. A German freelancer would have Miete, insurance, and a monthly ETF Sparplan on schedule (she holds IWDA.AS — the Sparplan writes itself).

**Ask (optional):** Add a handful of schedules and a budget so all three books exercise the same surfaces. Not blocking.

## What's right (don't touch)

- Alex: LLC with EUR receivables + CAD wires, HSA, crypto, condo/mortgage, jobs — realistic breadth.
- Lin Wei: WeChat Pay / Alipay as bank accounts, 住房公积金, A-shares by ticker, USD/EUR receivables in a CNY book — native, not translated.
- Sabine: SKR03 numbered chart with live VAT (1576 Vorsteuer / 1776 Umsatzsteuer), Privatentnahmen, GWG — DATEV-literate.
- Month-to-month lumpiness (April tax dips, freelancer swings) reads lived-in.
- Prices current; no stale-price warnings.
- Previous bookkeeper test litter fully cleared by regeneration.

## Signoff (2026-08-31, same day)

All four items addressed and re-verified on the live books. The bookkeeper:
"Balance sheets, trajectories, and the cultural texture all held
steady through the fixes — surgical changes, nothing sanded off. The
demo fleet is ship-ready. Three well-kept households, each with a
fresh month waiting to be entered, each modeling the practice this
tool exists to teach. Verdict for the cousin: review closed, no
carried items."

**§5 fixed (same day):** every commodity's closing price is now dated
AT the generation horizon (values forward-fill the last real close;
Sabine's synthetic ETF prices any date natively) — in both the
continuation path and the prefix builders. A fresh bundle opens
warning-free; the stale-price warning now fires only when a book has
genuinely aged, where it works as the fetch-fresh-quotes hook.
