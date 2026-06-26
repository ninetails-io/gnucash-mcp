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

### Audit (2026-06-25): every place the server keys on English structure

Systematic grep sweep across six assumption shapes — name lookups,
hardcoded `:`-paths, substring classification, special-account
prefixes, `.name`/`.fullname` literal comparisons, root/depth
detection. **Caveat:** grep finds English *literals*; it can't prove
the absence of a positional assumption encoded without one (those
were spot-checked — depth/root detection is data-driven and safe).
High confidence the *behavioral* set below is complete.

**A — Throws (hard failure):**
- **LEAD ITEM (correctness-grade): resolve helper-account parents by
  TYPE, not English name.** `_get_or_create_fx_account`
  (`business.py:440`, `_find_account(book, "Income")`) and the
  parallel discount-account path (canonical paths
  `"Expenses:Sales Discounts"` / `"Income:Purchase Discounts Taken"`,
  `business.py:325`) return None in a localized book → the FIRST
  cross-currency payment with rate drift **throws**. Resolve by the
  top-level INCOME/EXPENSE-type account instead; fixes localization
  AND plain user-renames in one stroke. (A/R / A/P resolution is
  already type-based, so it's safe.) Build the resolver to take a
  target type as a parameter, not a constant — FX wants INCOME,
  sales discount is contra-income, purchase discount is
  contra-expense, and a future gain-vs-loss split (Tier 3) puts the
  loss half under EXPENSE.
- The fuzzy keyword tuples (`_FX_NAME_KEYWORDS`,
  `_*_DISCOUNT_NAME_KEYWORDS`) are English substrings; in a localized
  book they match nothing and fall straight to the throwing layer.

**FX-account design — RESOLVED (2026-06-25 source probe + cousin doc
`specs/gnucash-account-naming-i18n.md`).** Headline finding that
reframes the fix: **native GnuCash books NO realized FX gain/loss on
cross-currency invoice payment.** It closes A/R at *face value in the
invoice currency* and lets the bank split absorb the rate
(`gncOwner.c::gncOwnerCreatePaymentLotSecs`; lots offset only within
the same account, all in the invoice currency, so no rate-difference
amount ever materializes). The `lot-mgmt/gains-acct` slot +
`Orphaned Gains-CUR` account belong **solely** to the commodity
capital-gains path (`cap-gains.cpp::xaccSplitComputeCapGains`,
reachable only when split commodity ≠ txn currency) — **never invoked
by the business module.** So our `pay_invoice` third FX split (PR #45)
is a *deliberate, more-correct* cash-basis recognition, **not** a
deviation-bug — there is no native mechanism to "match," and (a
correction to my earlier framing) **no native `Orphaned Gains`
account to "reuse"** on an invoice-only book. The fix:
1. **Creation** — resolve the top-level INCOME account by TYPE
   (locale-robust), create the FX account under it, mirroring
   GnuCash's own `GetOrMakeOrphanAccount` shape: a SINGLE income
   account where a loss is negative income.
2. **Resolution thereafter** — store the account GUID in a KVP slot
   and resolve from the slot, never by name (GnuCash idiom:
   `lot-mgmt/gains-acct/<commodity-unique-name>`, or our own
   namespaced slot). Locale-proof AND rename-proof.
3. Optionally localize the *created* name via the `.po` catalog
   (cousin doc ships the table) — cosmetic, not a blocker.
   Secondary claims in the cousin doc (`xaccAccountGainsAccount`,
   `GetOrMakeOrphanAccount`, single-income-account netting) were all
   independently CONFIRMED against current source during the probe.
   Open known-unknown: Trading-Accounts-enabled books run a
   post-commit rebalancing scrub that *could* touch the payment txn —
   untested; flag for a live check if a user turns that on.

**B — Silent misclassification (no throw — wrong bucket/number):**
- `reporting.py:1389` — `is_mortgage = "mortgage" in account.fullname
  .lower()` picks a 360- vs 60-month amortization term for the
  `debt_payoff_plan` minimum-payment estimate. A localized mortgage
  (`Hypothek`) falls to the 5-year assumption → wildly overestimated
  payment + payoff schedule. **Changes a computed number** (bounded
  only by the `minimum_payment` slot override).
- `core.py:1583` — `if "loan" in account.fullname.lower()` buckets
  liabilities into loans vs other on the dashboard. Cosmetic (totals
  unaffected), but `Darlehen`/`Kredit` lands in the wrong bucket.

**C — Special-account detection (CONFIRMED bug via GnuCash source):**
- `core.py:606` — `name.startswith("Imbalance-") or
  name.startswith("Orphan-")` gates the data-integrity warning.
  GnuCash generates BOTH names through gettext — `Scrub.cpp`
  `_("Orphan")` (L125) and `_("Imbalance")` (L464) — so they ARE
  localized (`<translated>-<CUR>`). The warning silently never fires
  for non-English books. **Tractable** (was mis-flagged "hardest"):
  GnuCash hangs Imbalance / Orphan / Orphaned-Gains directly off the
  **root** account, so `parent == root` is a locale-stable structural
  signal (cousin doc + 2026-06-25 probe, `Account.cpp` / `Scrub.cpp`).
  Detect via `parent == root` + type (`BANK` -> Imbalance/Orphan,
  `INCOME` -> Orphaned Gains) + a `...-<ISO4217>` suffix, with the
  locale `.po` word as an optional tiebreaker -- no English
  string-compare. A *safety* warning going dark -- prioritize above
  the cosmetic B/D items.

**D — English leaks into output (nothing breaks; the cosmetic lift):**
- Auto-created account *names* are English (`piecash.Account(name=
  "Foreign Exchange Gain/Loss", ...)`, the discount accounts) — even
  after the parent-by-type fix, you create an English-named account
  inside a `de` book. Make default names localizable/configurable.
- `reporting.py:756` injects the literal label `"Retained Earnings"`
  into balance_sheet regardless of locale.
- User-facing string localization (reports, warnings, errors, audit
  log) — the real lift; needs a message-catalog decision.

**Confirmed locale-SAFE (don't re-litigate):** all report bucketing is
type-based (`_ASSET_TYPES` etc., incl. the #107 RECEIVABLE/PAYABLE
work); depth/grouping derives the top segment from real account names
and walks parent links (`reporting.py:62`, `_get_account_depth`);
rename/collision checks compare user names to each other; the
`"Word:Word"` paths in `tools/*.py` are LLM-facing docstring examples,
not lookups.

### Remaining design items
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
- ~~Separate FX gain vs. loss accounts~~ — **recommend AGAINST**
  (2026-06-25 probe): native GnuCash nets gain+loss into ONE INCOME
  account (a loss is negative income); a separate EXPENSE loss account
  diverges from universal GnuCash convention and reads wrong against
  native books. Keep the single combined account.
- Taxtable default-taxtable UX (unrelated to FX) — still open.

## Housekeeping

- **`lin-wei.gnucash` fossil.** Bill #000001's payment carries an
  un-converted A/P relief (`qty=289` instead of the full CNY) — a relic
  of a pre-fix `pay_invoice` (cross-currency lot-relief bug, since
  fixed; current `pay_invoice` settles the equivalent bill to a clean
  zero, verified). Decision pending: regenerate (loses bookkeeper state)
  / leave (free adversarial fixture) / repair-in-place (5 min). Leaning
  **leave + document**.
- README content (real `get_book_summary` example), comment cleanup.
