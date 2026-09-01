# Internationalization & Account-Resolution — Implementation Spec

**Status:** Approved for implementation, targeting v1.4 (the first
widely-promoted release).
**Author:** Claude (Opus 4.8), 2026-06-25, consolidating work by
Claude Cowork ("the cousin," same weights) and a source-probe subagent.
**Consolidates:** `specs/PRE_1_4_ROADMAP.md` (Tier 2),
`specs/gnucash-account-naming-i18n.md` (the cousin's GnuCash reference),
the 2026-06-25 GnuCash-source probe (embedded below), and the
codebase audit performed this session.

This document has two jobs, in order: (1) **preserve** how this
analysis came together — the investigation, the reversals, the
provenance — for anyone interested in how the work was done under
Claude; and (2) **specify** the implementation precisely enough to
build cold.

---

## 1. How this came together (preservation)

The work originated not from a bug report but from a release-readiness
question. The arc:

1. **"Is v1.4 enough for a real release?"** Review of the six PRs since
   v1.3.1 (batch entry, pagination, group-by, multi-book, an FX
   correctness audit, FX entry-sanity warnings) concluded: yes, minor-
   release-grade, with the FX correctness work the strongest part.

2. **"Most of my users are non-English locales."** Stephen's
   correction flipped the calculus. The headline of v1.4 is multi-
   currency correctness; the user base is majority non-US. That
   intersection is where the project was weakest.

3. **The paradox.** *"How do they use it if it's broken?"* Resolved by
   separating three things the project had silently conflated —
   **geography ≠ locale ≠ account-naming language.** A "non-US" user
   may run an English-named book (`en_IN` is a shipped GnuCash locale;
   technical users everywhere run English GnuCash). And the one hard
   failure is on a *narrow* path (business-module cross-currency
   invoice settlement), so most users never reach it. The breakage is
   real but had been hidden by selection bias in who had found the
   project so far. A promoted launch removes that bias.

4. **The audit.** A systematic sweep (this session) for every place the
   server keys logic off English account *structure* rather than off
   the locale-invariant account *type* enum. Findings in §5.

5. **The cousin's reference doc.** `gnucash-account-naming-i18n.md`,
   produced by Claude Cowork against the GnuCash source: the
   type-vs-name principle, the two independent translation sources, the
   special-account naming rules, and a gain/loss-account detection
   strategy.

6. **The source probe.** One claim the cousin's doc *implied* and the
   plan *assumed* — that GnuCash has a native mechanism for booking
   invoice-payment FX that we should match — was sent to a subagent to
   verify against current GnuCash source. It was **refuted** (§4.3),
   which reshaped the FX design. The probe also independently
   **confirmed** the cousin doc's three load-bearing source claims,
   establishing the document as trustworthy.

**Reversals worth recording** (the value the verification added):
- *"Reuse GnuCash's native gains account"* — **withdrawn.** No native
  gains account exists on an invoice-only book; it is commodity-
  trading-only.
- *"GnuCash books invoice FX somewhere we should match"* — **false.**
  Native GnuCash recognizes no FX gain/loss on invoice payment at all;
  our behavior is a deliberate improvement, not a deviation.
- *"Imbalance/Orphan detection is the hardest fix"* — **wrong.** A
  `parent == root` structural signal makes it tractable.

The lesson embedded here: a fluent, plausible plan is only as good as
its contact with the source. Three confident claims did not survive
that contact. Verify before you build, especially the claims that feel
obviously true.

---

## 2. Problem statement

GnuCash localizes account **names** per the user's locale; it never
localizes account **types**. The server, grown from an originally
USD/English-only codebase, still keys several behaviors off English
names and paths. On a localized book (`de_DE`, `es_MX`, `zh_CN`, …)
those behaviors range from cosmetic-wrong to a hard exception.

For a release that will be promoted to a predominantly non-US audience
— very likely demonstrated on video — "works only on English-named
books" is not acceptable. This spec makes account resolution
**locale-robust**: identify accounts by type and stable structural
signals, never by an English literal.

Scope boundary: this is *correctness* i18n (the server does the right
thing on a localized book), **not** *presentation* i18n (translating
the server's own output strings). The latter is large and explicitly
deferred (§9).

---

## 3. The one principle

> **Identify accounts by `GNCAccountType`, never by name.**

The type enum (`ASSET`, `LIABILITY`, `EQUITY`, `INCOME`, `EXPENSE`,
`BANK`, `CASH`, `CREDIT`, `STOCK`, `MUTUAL`, `TRADING`, `RECEIVABLE`,
`PAYABLE`, `ROOT`, …) is identical in every locale. Account names are
localized free text. Where a name *must* be used (display, or a
tiebreaker), resolve it from the book's own data or the message
catalog for the book's locale — never compare to a hard-coded English
word.

**The two-translation-sources trap.** There are two *independent*
sources of localized names that do **not** always agree:
- **Wizard chart templates** (`data/accounts/<locale>/*.gnucash-xea`),
  hand-translated per locale.
- **Runtime gettext catalog** (`po/<lang>.po`), used for auto-created
  accounts (Imbalance, Orphan, Opening Balances, …).

Example: a German book's top-level income account is **"Erträge"**
(template) while the catalog translation of "Income" is **"Ertrag"**
(§ verified in the cousin doc). Any fix that "looks up the localized
word and matches it" is therefore unsafe for template-created
accounts. This is the second reason the principle is *type, not name*.

---

## 4. Authoritative GnuCash facts (consolidated & verification status)

Sourced from the cousin's reference doc and the 2026-06-25 probe.
"CONFIRMED" = independently verified against current GnuCash `stable`
source this session.

### 4.1 Special auto-created accounts

| Account | Name construction | Type | Parent |
|---|---|---|---|
| Imbalance | `_("Imbalance") + "-" + <mnemonic>` | `BANK` | root |
| Orphan | `_("Orphan") + "-" + <mnemonic>` | `BANK` | root |
| Orphaned Gains | `_("Orphaned Gains") + "-" + <mnemonic>` | `INCOME` | root |
| Opening Balances | `_("Opening Balances")` (+ ` - <mnemonic>` multi-ccy) | `EQUITY` | equity acct |
| Retained Earnings | `_("Retained Earnings")` | `EQUITY` | equity acct |
| Trading | `_("Trading")` → `:<namespace>:<commodity>` | `TRADING` | root |

- The leading word is gettext-localized; the **currency suffix is the
  ISO mnemonic and is never translated.** CONFIRMED (`Scrub.cpp`:
  `_("Orphan")`, `_("Imbalance")`; `Account.cpp::GetOrMakeOrphanAccount`).
- Imbalance / Orphan / Orphaned-Gains are **direct children of root** —
  this makes `parent == root` a locale-stable structural signal.
  CONFIRMED.
- Separator differs: Imbalance/Orphan/Orphaned-Gains use a bare hyphen
  (`Imbalance-EUR`); the equity accounts use a spaced hyphen
  (`Opening Balances - EUR`). The unsuffixed form appears when the
  account currency equals the book default.

### 4.2 GnuCash keys off translated names too — and self-heals

`gnc_find_or_create_equity_account()` looks up the parent by
`gnc_account_lookup_by_name(root, _("Equity"))`, then **verifies
`type == EQUITY` and falls back to root**. Lesson adopted here: match
by **type first**; use a localized name only as a tiebreaker, with a
type-and-root fallback.

### 4.3 The probe's headline: no native invoice-payment FX

**Native GnuCash books NO realized FX gain/loss on cross-currency
invoice payment.** CONFIRMED by tracing the business engine:

- `gncOwner.c::gncOwnerCreatePaymentLotSecs` builds the payment so the
  A/R split's value equals its amount (both in the *invoice* currency);
  the user-supplied exchange rate only scales the *bank* split.
- Payment lots offset invoice lots **only within the same account**
  (`gncOwner.c`: `if (acct != gnc_lot_get_account(right_lot)) continue;`),
  all in the invoice currency. The A/R is posted at face value and
  closed at face value — **no rate-difference amount ever materializes
  in the ledger.**
- The `lot-mgmt/gains-acct` KVP slot and the `Orphaned Gains-CUR`
  account belong **exclusively** to the commodity capital-gains path
  (`cap-gains.cpp::xaccSplitComputeCapGains`, reachable only when a
  split's commodity ≠ its transaction currency). The business module
  **never invokes it.** `xaccAccountGainsAccount` is called only from
  `cap-gains.cpp`.

**Implication.** Our `pay_invoice` third FX split (PR #45) is a
*deliberate, more-correct* cash-basis recognition of realized FX that
native GnuCash omits. There is no native mechanism to "match," and **no
native gains account to reuse** on an invoice-only book. We create our
own account; we make its *resolution* locale-robust via a KVP slot
(§6).

### 4.4 Gain and loss net into ONE income account

CONFIRMED: GnuCash files realized gains/losses in a single
`ACCT_TYPE_INCOME` account; a loss is negative income. There is no
separate EXPENSE loss account in native behavior. **Decision (§6.4):
do not split FX into gain/loss accounts** — it diverges from universal
GnuCash convention and reads wrong against native books.

### 4.5 Known unknowns (not blockers; flagged for a live check)

- **Trading-Accounts-enabled books** (`qof_book_use_trading_accounts`)
  run a post-commit rebalancing scrub that *could* touch a payment
  transaction. The business path doesn't opt in directly; untested.
- **The GUI payment dialog** (`dialog-payment.c`) computes the exchange
  rate it passes to the engine. The engine books no FX; an extra
  GUI-side adjustment split is very unlikely but is the one corner the
  probe could not reach from the engine alone.

---

## 5. The audit — every English-structural assumption

Method: grep sweep across six assumption shapes (name lookups, hard-
coded `:`-paths, substring classification, special-account prefixes,
`.name`/`.fullname` literal comparisons, root/depth detection).
Caveat: grep finds *literals*; positional assumptions encoded without a
literal were spot-checked (depth/root detection is data-driven and
safe). High confidence the behavioral set is complete.

### Tier A — Throws (hard failure) — **1.4 BLOCKER**

- **`book/business.py` FX-account resolution.**
  `_get_or_create_fx_account` falls through to
  `self._find_account(book, "Income")` (≈`business.py:440`) for the
  create-parent. Returns `None` in a localized book → the first
  cross-currency invoice settlement with rate drift **raises**.
- **`book/business.py` discount-account resolution.** The parallel
  `_get_or_create_discount_account` uses canonical paths
  `"Expenses:Sales Discounts"` / `"Income:Purchase Discounts Taken"`
  (≈`business.py:325`) and the same English-parent fallback. Same throw.
- The fuzzy keyword tuples (`_FX_NAME_KEYWORDS`,
  `_*_DISCOUNT_NAME_KEYWORDS`) are English substrings; in a localized
  book they match nothing and fall straight to the throwing layer.

### Tier B — Silent misclassification (no throw) — **fix for flagship**

- **`book/reporting.py` mortgage term** (≈`reporting.py:1389`):
  `is_mortgage = "mortgage" in account.fullname.lower()` selects a
  360- vs 60-month amortization term for the `debt_payoff_plan`
  minimum-payment estimate. A localized mortgage (`Hypothek`) falls to
  the 5-year assumption → a **wrong computed number** (wildly
  overestimated payment & payoff schedule). Bounded only by the
  `minimum_payment` slot override.
- **`book/core.py` loan bucketing** (≈`core.py:1583`):
  `if "loan" in account.fullname.lower()` buckets liabilities into
  loans-vs-other on the dashboard. Cosmetic (totals unaffected), but a
  `Darlehen`/`Kredit` lands in the wrong bucket.

### Tier C — Special-account detection — **fix for flagship (cheap)**

- **`book/core.py` integrity warning** (≈`core.py:606`):
  `name.startswith("Imbalance-") or name.startswith("Orphan-")` gates
  the data-integrity warning. Both names are gettext-localized (§4.1),
  so the warning **silently never fires** for non-English books — a
  safety check going dark. Tractable via the `parent == root` signal
  (§6.5).

### Tier D — English leaks into output (nothing breaks) — **DEFER**

- Auto-created account *names* are English (`Foreign Exchange
  Gain/Loss`, the discount accounts). Addressed opportunistically by
  §6.3 (localize the created leaf), but acceptable in English.
- `reporting.py:756` injects the literal label `"Retained Earnings"`.
- Whole-surface output-string localization (reports, warnings, errors,
  audit log). The real i18n lift; needs a message-catalog decision.
  **Out of scope for v1.4.**

### Confirmed locale-SAFE (do not re-investigate)

All report bucketing is type-based (`_ASSET_TYPES` / `_LIABILITY_TYPES`
/ `_EQUITY_TYPES`, incl. the #107 RECEIVABLE/PAYABLE work); depth/
grouping derives the top segment from real account names and walks
parent links (`reporting.py:62`, `_get_account_depth`); rename/
collision checks compare user names to each other; the `"Word:Word"`
paths in `tools/*.py` are LLM-facing docstring **examples**, not
lookups.

---

## 6. Design

### 6.1 New chokepoint: top-level account by type

A single helper retires the entire Tier-A throw class. In
`book/_base.py` (or `book/business.py` if preferred local):

```python
def _top_level_account_of_type(self, book, acct_type):
    """Resolve the top-level account of a given GNCAccountType —
    the account of that type whose parent is the real root.

    Returns (account, notice). Locale-invariant: keys off `type`
    and `parent == book.root_account`, never a name.

    - exactly one  -> (it, None)
    - several      -> (deterministic pick, ambiguity-notice dict)
    - none         -> create a top-level account of that type under
                      root, named from the locale catalog (§6.3),
                      return (new, None)
    """
```

Rules:
- Exclude template accounts via `self._template_account_guids(book)`.
- "Top-level" = `account.parent is not None and
  account.parent.guid == book.root_account.guid`.
- Deterministic multi-pick: lowest `fullname` sort, with an
  ambiguity-notice mirroring the existing `ambiguous_fx_account`
  pattern.

### 6.2 Designated-account resolver (FX + both discount sides)

The FX path and the two discount paths are the same shape. Unify them
on one resolver. Resolution order, **most authoritative first**:

```
Layer 0  KVP slot   read a stored GUID -> resolve -> validate
                    (type + book-default currency). Locale-proof
                    and rename-proof. THE primary path after first use.
Layer 1  explicit   caller-supplied account argument (unchanged).
Layer 2  fuzzy      keyword match over accounts of the target type in
                    the default currency (unchanged; best-effort only).
Layer 3  create     find parent via _top_level_account_of_type(target
                    type); create the account; localize its leaf name
                    (§6.3); WRITE its GUID to the Layer-0 slot.
```

The only behavioral change vs. today is: **Layer 0 added**, and
**Layer 3's parent lookup is type-based instead of
`_find_account("Income")`.** Layers 1–2 are preserved so existing
English books behave identically.

Per-role parameters:

| Role | Target parent type | Slot key | Default leaf concept |
|---|---|---|---|
| FX gain/loss | `INCOME` | `gnc-mcp/fx-gain-loss-acct` | "Realized Gain/Loss" |
| Sales discount (customer invoice) | `EXPENSE` | `gnc-mcp/sales-discount-acct` | "Sales Discounts" |
| Purchase discount (vendor bill) | `INCOME` | `gnc-mcp/purchase-discount-acct` | "Purchase Discounts" |

Slot storage: these are book-global, single-account-per-role (each is
denominated in the book default currency), so a single slot on
`book.root_account` per role suffices — no need for GnuCash's per-
source-account, per-commodity slot path. Use the namespaced
`gnc-mcp/<role>` convention (path-style key → hierarchical sub-slot;
CLAUDE.md slot-naming rules). Read with `_slot_value_str`. On a stale/
missing slot, fall through and re-write on next create.

`_get_or_create_fx_account` and `_get_or_create_discount_account`
become thin wrappers binding their row of the table.

### 6.3 Localized leaf names (flagship polish; SHOULD, not blocker)

Because Layer 0 makes resolution GUID-based, the created leaf name is
purely cosmetic and can be localized freely without risking later
resolution.

**Locale source of truth (decided).** Infer the locale from the book's
own account names, with `GNUCASH_LOCALE` as an optional override.
Inference is zero-config and matches the book the user actually has;
the env override covers ambiguity. (Settled — formerly the §10 open
question.)

- `_infer_book_locale(book)`: (1) honor an explicit `GNUCASH_LOCALE`
  env/config override, reduced to its language code; else (2) **vote**
  — match the book's top-level type accounts against the bundled
  gettext structural-word catalog and take the language with the most
  matches (≥ 2, so a lone coincidental hit doesn't drive inference);
  else (3) `None`. Voting (not a single-account lookup) is required by
  the two-translation-sources trap (§3): a German book's top-level
  income is the *template* word "Erträge", which does **not** equal
  the *gettext* "Ertrag" — but Assets/Expenses/Equity
  ("Aktiva"/"Aufwand"/"Eigenkapital") match exactly and carry the
  vote. A numbered chart (SKR03) matches too few and correctly falls
  back to English.
- `_locale_account_name(concept, english_default, locale)`: look up a
  concept ("Realized Gain/Loss", "Sales Discounts", …) in a bundled
  catalog seeded from the cousin doc's translation table (12 languages
  for the core terms; extend by parsing `po/<lang>.po` later). Returns
  `english_default` when `locale` is `None`/unknown or the concept has
  no translation. (Only the FX concept ships translations today; the
  discount concepts have none in GnuCash and stay English.)
- An English fallback leaf is **harmless** — the slot still resolves
  it. So this degrades gracefully and never blocks.

### 6.4 FX gain vs loss: keep ONE income account

Per §4.4. Do not implement separate gain/loss accounts. Remove the
idea from the Tier-3 backlog (done in the roadmap). The single combined
INCOME account, loss-as-negative-income, is the GnuCash-idiomatic and
interoperable choice.

### 6.5 Tier B & C fixes

- **Mortgage term (B).** Remove the `"mortgage"` substring heuristic.
  There is no "mortgage" account type, so the locale-correct source of
  truth is explicit data: honor the existing `minimum_payment` slot and
  add an optional `loan_term_months` slot. **No default term.** When
  neither slot is set, *omit the minimum-payment estimate entirely* and
  surface `"set loan_term_months for payment estimates"` rather than
  guessing — a 30y-vs-5y guess differs by an order of magnitude, and a
  wrong estimate on camera is worse than no estimate. (Removes the
  assumption entirely; also fixes English books that named the account
  "Home Loan".)
- **Loan bucketing (B).** Replace the `"loan"` substring with a type
  split: `CREDIT` → "Credit cards", other `LIABILITY` → "Loans & other
  liabilities". Locale-correct; minor, intentional relabeling.
- **Imbalance/Orphan detection (C).** Replace the English-prefix check
  with a structural-plus-catalog predicate,
  `_is_auto_balancing_account(account, root)`: `account.type == "BANK"`
  **and** `account.parent is root` **and** `account.name.lower()`
  starts with any "Imbalance"/"Orphan" entry in the bundled locale
  catalog (`_BALANCING_ACCOUNT_NAME_PREFIXES`, §6.3). The catalog
  **prefix** match is what makes the currency suffix irrelevant: one
  rule accepts both the suffixed form (`Ungleichgewicht-EUR`, when the
  account currency ≠ book default) and the unsuffixed form
  (`Ungleichgewicht`, when it equals the default). `type == BANK` +
  `parent is root` are the structural invariants that keep the prefix
  match off ordinary user accounts; unknown locales degrade to the
  English words. `Orphaned Gains` is deliberately **not** matched — it
  is type `INCOME`, a legitimate account, not a defect. No English
  string-compare.

---

## 7. Implementation plan

One themed branch off `develop` — `fix/i18n-account-resolution` — all
fixes share the theme "stop keying on English structure," which keeps
it one coherent PR (lane discipline; not a balloon). Suggested commit
order:

1. `feat(_base): _top_level_account_of_type chokepoint` + unit tests.
2. `feat(business): KVP-slot designated-account resolver` — Layer 0 +
   type-based Layer 3; rewire `_get_or_create_fx_account` and
   `_get_or_create_discount_account` onto it. (Tier A — the blocker.)
3. `feat(i18n): book-locale inference + localized leaf names` (§6.3).
   (Flagship polish; can be deferred to a follow-up commit without
   blocking #2.)
4. `fix(reporting): loan term from slot, not English keyword` +
   `fix(core): loan bucketing by type` (Tier B).
5. `fix(core): structural Imbalance/Orphan detection` (Tier C).
6. `test(i18n): localized synthetic persona + regression locks` (§8).
7. Update `CLAUDE.md` gotchas (type-not-name; the two-translation-
   sources trap) and `specs/PRE_1_4_ROADMAP.md` (mark Tier 2 items
   done).

Invariants to preserve: piecash objects never cross the MCP boundary;
every raw-SQL write paired with `_verify_*`; slot writes via the
`entity[key]=value` accessor with `_slot_value_str` reads; no second
book-open in the resolver (operate on the already-open session).

---

## 8. Test plan

**The durable fix is a localized synthetic persona.** Alex (USD) and
Lin Wei (CNY) are both English-named, which is exactly why this bug
class was invisible. A third persona with a **native-localized account
hierarchy** converts the whole class from invisible to a failing test.

- **New persona — Sabine Brenner** (the bookkeeper owns the final build): a
  Munich freelance Grafikdesignerin / Einzelunternehmerin running the
  **SKR03 Standardkontenrahmen** — the chart a real German sole
  proprietor actually uses, not a tidied-up English-shaped hierarchy.
  EUR default; GnuCash's shipped `acctchrt_skr03` names (top-level
  INCOME "Erlöse u. Erträge 2/8", EXPENSE "Aufwendungen 2/4", numbered
  leaves like "8400 Erlöse USt. 19%", A/R "1400 Ford. a. Lieferungen
  und Leistungen"); a USD-paying client to force cross-currency
  invoicing; a `Hypothek` for the debt-payoff path; and a localized
  Imbalance account for Tier C. Built via a `scripts/synthetic_book/`
  generator like Alex/Lin Wei, deterministic seed, per-phase backups.

  **Verified (2026-06-26):** SKR03 ships with GnuCash and — despite
  being numbered and Kontenklasse-organized — maps to a clean **single
  top-level account per fundamental type**, so
  `_top_level_account_of_type` resolves it correctly: the FX account
  lands under "Erlöse u. Erträge 2/8" and self-heals on repeat via the
  English leaf. A minimal SKR03 slice with the real names is locked in
  `test_i18n_account_resolution.py::TestSKR03Chart`. (An SKR03 purist
  might want FX in a specific numbered account; auto-creation under the
  income-class placeholder is correct-by-type and acceptable.)
- **The acceptance test:** post and pay a cross-currency invoice on the
  German book with rate drift. Pre-fix this **throws**; post-fix it
  settles, recognizes FX into a (German-named) top-level-INCOME child,
  and the GUID round-trips through the slot.
- **Unit/contract tests:**
  - `_top_level_account_of_type` for 0/1/many, template exclusion,
    non-English names.
  - Resolver Layer 0 slot round-trip; stale-slot fallthrough + rewrite.
  - Debt-payoff term from slot vs. default-with-warning; no English
    keyword path remains.
  - Structural Imbalance/Orphan detection on a localized name; the
    integrity warning fires.
  - A regression lock asserting **no `_find_account(book, "<English>")`
    and no English-substring account classification** reappears in
    `book/*.py` (a grep-style contract test in the spirit of
    `TestWriteVerificationCoverage`).
- **Bookkeeper validation:** the production signal. Necessary but
  insufficient for math — route the FX number through explicit review
  (the bookkeeper validates base cases, not hand-calcs). Cross-tool
  sanity: the German book's net worth must agree across
  `get_book_summary` / `balance_sheet` / `net_worth` to the cent, as
  Alex and Lin Wei do.

---

## 9. Scope & release gating

| Item | Tier | v1.4 |
|---|---|---|
| Type-based FX/discount parent + slot resolution | A | **MUST** (blocker) |
| Mortgage term from slot, not keyword | B | **SHOULD** (wrong number on camera) |
| Loan bucketing by type | B | SHOULD (cosmetic) |
| Structural Imbalance/Orphan detection | C | SHOULD (cheap; safety warning) |
| Localized created-account leaf names | D | SHOULD (flagship polish) |
| Localized synthetic persona + regression locks | — | **MUST** (proves the fix) |
| Output-string localization (reports/errors/audit) | D | **DEFER** |
| Number-format locale policy (decimal separator) | D | **DEFER** |

The **only hard blocker** is Tier A — it throws. Everything else
improves the localized experience for the launch but does not crash.
Given the flagship/video context, the recommendation is to ship A + the
SHOULDs (they are individually small once the chokepoint exists) and
defer only the genuinely large output-localization lift.

This is a *correctness* i18n release. Full *presentation* i18n is a
future body of work and explicitly not promised by v1.4.

---

## 10. Open questions

1. **Trading-Accounts books** (§4.5) — confirm via a live test that a
   post-commit scrub doesn't interact with our FX split. Low
   probability; cheap to check on the German persona.
2. **Catalog coverage.** Seed from the cousin doc's 12-language table
   now; decide later whether to parse all 61 `po/` locales at build
   time. Not a blocker — English fallback is safe.

---

## 11. Source index

- `specs/PRE_1_4_ROADMAP.md` — Tier 2 (the audit, in roadmap form).
- `specs/gnucash-account-naming-i18n.md` — the cousin's GnuCash
  reference (types, two translation sources, special-account rules,
  gain/loss detection, 12-language translation table).
- 2026-06-25 source probe — embedded in §4.3–4.5 (GnuCash `stable`:
  `gncOwner.c`, `gncInvoice.c`, `cap-gains.cpp`, `Account.cpp`,
  `Scrub.cpp`).
- `CLAUDE.md` — slot conventions, `_template_account_guids`,
  the "type, not name" gotchas this spec extends.
