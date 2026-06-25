# Pre-1.4 Roadmap

Captured 2026-06-24 so the plan survives session boundaries and
compaction. Priority order (Stephen's): **correctness → i18n →
feature creep.** Lane discipline (the PR #92 lesson): each tier is its
own themed branch/PR, NOT one ballooning branch.

---

## Tier 0 — DONE (branch `fix/multicurrency-correctness`)

The FX-misreporting audit and its fixes. All committed, 1727 tests
green, bookkeeper-validated on Alex + Lin Wei.

- vendor_spending_report reads the posted ledger, not re-converted face value
- **B1** FX gain/loss account required in book default currency
- **B2** both-foreign posting splits valued at the posting-date rate
- **S5** lot cost basis in book default currency
- **S6** foreign debts with no FX rate excluded from debt_payoff_plan (warned)
- budget targets: warn instead of silently folding un-converted foreign units
- **`_market_value`** cost-basis fallback converts each purchase at its
  posting-date rate (book threaded through the 6 core.py call sites) —
  no more mixed-currency cost sums. (Initially mis-scoped as "too big";
  it was 6 callers, all with book in scope.)
- regression tests in `tests/test_multicurrency_audit.py`

## Tier 0b — DONE (branch `feat/fx-entry-sanity`)

Preventive correctness — catch the bad rate at *entry* instead of
valuing it correctly after the fact.

- **Cross-commodity implied-rate sanity warning.** A user-supplied
  cross-commodity split encodes its rate via `|value|/|quantity|`;
  `_fx_sanity_warnings` flags (non-blocking) when that implied rate is
  off the latest price on file by ≥ a configurable ratio (default 2×,
  `GNUCASH_FX_SANITY_RATIO`). Catches decimal slips / inverted pairs —
  the JetBrains-fossil class (implied 1.0 vs market 7.0). Wired into
  `create_transaction`, `create_transactions` (per-row warnings side-
  table), `update_transaction`, `replace_splits`. Warns, never blocks.
- regression tests in `tests/test_fx_entry_sanity.py`

## Tier 1 — Correctness (remaining)

- **S3 — `_monthly_net_income` re-prices history at today's rate**
  (`core.py`). Tagged **1.4.1** (display drift on a dashboard, not data
  corruption). Fix = per-month-end rate maps, like `_budget_headline`.

## Tier 2 — Internationalization (new branch)

GnuCash ships localized account-hierarchy templates per locale
(`accounts/<locale>/`, ISO 639-1 + 3166-1). A `ja`/`de`/`ko` book has
no account literally named "Income"; account *types* (RECEIVABLE,
PAYABLE, INCOME…) are never localized. Verified against the GnuCash
wiki (Account_Hierarchy_Template, Locale_Settings).

- **LEAD ITEM (correctness-grade): resolve helper-account parents by
  TYPE, not English name.** `_get_or_create_fx_account` and the
  discount-account path call `self._find_account(book, "Income")`,
  which returns None in a localized book → the FIRST cross-currency
  payment **throws**. Resolving by the top-level INCOME/EXPENSE-type
  account fixes localization AND plain user-renames in one stroke.
  (A/R / A/P resolution is already type-based, so it's safe.)
- Localizable / configurable default account names (FX gain/loss,
  discount) + locale-aware or configurable fuzzy keywords
  (`_FX_NAME_KEYWORDS`, `_*_DISCOUNT_NAME_KEYWORDS`).
- User-facing string localization (reports, warnings, errors, audit
  log) — the real lift; needs a message-catalog decision.
- Number-format locale policy (decimal separator): machine-canonical
  vs. display-localized.

## Tier 3 — Feature creep (branch per feature)

- **Frankfurter FX auto-updater** (`specs/PRICE_UPDATE_SPEC.md`) — first
  live network call into a deterministic server; earns its own sprint
  (offline/failure handling, don't-clobber-user-rates, `type='transaction'`
  skip, commodity selection). CDN gotcha: Frankfurter 403s the default
  `Python-urllib` User-Agent; set an explicit UA.
- **Accrual A/R mark-to-market revaluation.** Realized FX is handled at
  settlement; open foreign invoices aren't revalued at reporting dates.
  Feature-sized (a revaluation pass, delta → FX gain/loss). Timing-of-
  recognition only; converges to the same total at settlement.
- Separate FX gain vs. loss accounts; taxtable default-taxtable UX.

## Housekeeping

- **`lin-wei.gnucash` fossil.** Bill #000001's payment carries an
  un-converted A/P relief (`qty=289` instead of the full CNY) — a relic
  of a pre-fix `pay_invoice` (cross-currency lot-relief bug, since
  fixed; current `pay_invoice` settles the equivalent bill to a clean
  zero, verified). Decision pending: regenerate (loses bookkeeper state)
  / leave (free adversarial fixture) / repair-in-place (5 min). Leaning
  **leave + document**.
- README content (real `get_book_summary` example), comment cleanup.
