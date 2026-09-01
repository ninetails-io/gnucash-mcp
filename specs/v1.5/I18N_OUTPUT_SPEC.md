# i18n Output Localization — v1.5 spec (Tier D elaboration)

Status: **draft — inventory complete, awaiting scoping ruling**
Origin: 2026-07-19 codebase sweep (two-agent inventory of every
hardcoded English string that reaches a user surface).
Predecessor: `specs/v1.4/i18n/I18N_ACCOUNT_RESOLUTION_SPEC.md`,
which shipped *correctness* i18n (Tiers A–C) and deferred this —
*presentation* i18n — as Tier D.

---

## 1. Problem

The v1.4 work made the server locale-**safe**: nothing throws or
mis-resolves on a German or Chinese book. But nearly every string the
server *renders* is still English — report labels, dashboard
sections, warnings, audit-log vocabulary, and (worst) a few account
names and descriptions that persist into the user's book data.

The mitigating fact that shapes this whole spec: **an LLM sits
between the server and the human.** Tool responses are read by a
model that translates on the fly for the user. English in a tool
response is therefore *mostly* free. The exceptions — where English
actually reaches a human raw, or gets frozen into data — are what
this spec targets. Cost-effectiveness ranking, not string count,
drives the phasing.

---

## 2. Target languages — the GnuCash list

We aim at exactly the languages GnuCash itself ships message
catalogs for: **the 61 `po/<lang>.po` locales** (verified against
`Gnucash/gnucash` `stable`, 2026-07-19):

```
ar  as  az  bg  brx  ca  cs  da  de  doi  el
en_AU  en_GB  en_NZ  es  es_NI  et  eu  fa  fi  fr
gu  he  hi  hr  hu  id  it  ja  kn  ko  kok
kok@latin  ks  lt  lv  mai  mk  mni  mni@bengali  mr  nb
ne  nl  pl  pt  pt_BR  ro  ru  rw  sk  sr
sv  ta  te  tr  uk  ur  vi  zh_CN  zh_TW
```

(61 including the variant catalogs; plus `en` as the source
language.)

### 2.1 Normalization policy (existing, keep)

Locale keys in our tables are normalized 2–3-letter language codes
(`pt_BR → pt`, `zh_CN → zh`), matching `_STRUCTURAL_TYPE_NAMES` /
`_LOCALIZED_ACCOUNT_NAMES` in `book/_base.py`. Under that policy the
61 catalogs collapse to **53 languages**:

| Group | Coverage today |
|---|---|
| **46 languages already keyed** in `_STRUCTURAL_TYPE_NAMES` (ar, as, bg, brx, ca, cs, da, de, doi, el, es, fi, fr, gu, he, hi, hr, hu, id, it, ja, kn, ko, kok, ks, lt, lv, mai, mni, mr, nb, ne, nl, pl, pt, ro, ru, sk, sr, sv, ta, te, tr, uk, ur, vi, zh) | Full: locale inference + FX leaf name |
| **6 languages in GnuCash's catalog but not ours**: az (Azerbaijani), et (Estonian), eu (Basque), fa (Persian), mk (Macedonian), rw (Kinyarwanda) | None — excluded in v1.4 because their po files lack the five structural words and/or Realized Gain/Loss; books in these locales degrade to English (safe by design) |
| **en** (+ en_AU / en_GB / en_NZ variants) | Source language |

### 2.2 Script variants — open question for the ruling

Three catalogs are *script* variants, not regional variants:
`zh_TW` (Traditional vs `zh_CN` Simplified), `kok@latin` (Konkani
in Latin vs Devanagari), `mni@bengali` (Meetei in Bengali vs Meetei
Mayek). The current `zh_CN → zh` normalization would hand a
Traditional-Chinese book Simplified strings — arguably worse than
English. Options:

- **(a)** Extend keys to script-qualified where it matters
  (`zh-Hans` / `zh-Hant`); `_infer_book_locale` already votes on
  account-name words, which differ between scripts, so detection is
  feasible.
- **(b)** Keep normalization, accept the mismatch, document it.

Recommendation: (a) for Chinese only (real user population, and Lin
Wei gives us a zh oracle); (b) for kok/mni until demand appears.

### 2.3 Where translations come from

Critical distinction from the v1.4 work: GnuCash's po catalogs
contain GnuCash's *own* strings. Almost none of our output
vocabulary ("Runway", "Kill order", "CONTEXT RESET", "Splits
(before):") exists in any GnuCash catalog. **The GnuCash list tells
us WHICH languages; it cannot give us the strings.** Sources, in
priority order:

1. **GnuCash po catalogs** — for the ~15 concepts they do cover
   (account-type words, Imbalance/Orphan, Retained Earnings,
   Opening Balances, statuses like posted/paid where GnuCash's UI
   has them). Always prefer these: they match what the user's
   GnuCash UI already shows.
2. **LLM-generated, natively reviewed where possible** — for our own
   vocabulary. Generation is cheap for a frontier model; the risk is
   unreviewed accounting terminology in 50 languages. Mitigation:
   full human-quality review for the oracle languages (de, zh, en);
   spot review for the majors (es, fr, pt, ja, ru); accept
   LLM-quality for the long tail, with English fallback one key away.

---

## 3. Inventory (2026-07-19 sweep)

Tiered by where a lookup table earns its keep. File:line references
are as of `develop` at v1.4.1.

### Tier 1 — persists into book data (cannot be re-translated later)

The only category where English is *frozen*: once written, the LLM
can't fix it on read.

| Item | Site |
|---|---|
| Discount account leaves `Sales Discounts` / `Purchase Discounts Taken` — **no translations shipped at all** | `book/business.py:330-331`, creation at `:862` |
| English `description=` persisted onto auto-created FX and discount accounts | `book/business.py:674-677`, `:866-871` |
| Fallback parent names `Income` / `Expenses` when no top-level account of the type exists | `book/business.py:612`, `:814` |

The `_LOCALIZED_ACCOUNT_NAMES` catalog + `_locale_account_name`
chokepoint already exist; this tier is "add rows" (a
`sales_discounts` / `purchase_discounts` / `account descriptions`
concept per language), not new machinery. Note the resolver-twins
refactor in the v1.5 README wants to land first — it gives the
discount resolver the same localized-exact-match path the FX
resolver has.

### Tier 2 — audit log (a human reads it raw)

The audit log is the one surface with **no LLM in between** — the
bookkeeper and Stephen read the text files directly. ~120 literals
across the ~60 formatters in `logging_config.py`: operation headers
(`CREATE TRANSACTION`, `POST BILL`, `SWITCH BOOK`), field labels
(`Statement balance:`, `Splits (before):`, `Reason:`), advisory
lines (`lot auto-closed (quantity reached zero)`), the daily file
header banner. Already dispatch-table-shaped — a lookup table slots
into the existing structure with no redesign.

Open policy question: is the audit log's language the *book's*
locale or the *operator's*? A German book maintained by an
English-speaking bookkeeper argues for an explicit setting
(`GNUCASH_AUDIT_LOCALE`, default = book locale).

### Tier 3 — rendered report/dashboard text (highest volume)

Read through the LLM (which can translate), but: mixed-language
tables read badly, the LLM shouldn't have to re-render TSVs, and
these labels sit *next to* the user's own localized account names.

- `get_book_summary` (`book/core.py`) — ~40 labels/sentences: every
  section header (`Assets:`, `Runway:`, `Net worth trajectory:`,
  `Reconciliation:`), all 11 warning templates, budget pacing
  phrases, lag phrases (`{N} months behind`, `never reconciled`).
- TSV headers and pagination: `Total` / `Avg` / `TOTAL`
  (`_format.py:169,180`), `[Showing N of M …]` family
  (`_format.py:459-594`), report column labels (`Account`,
  `Budget`, `Actual`, `Remaining`, `%Used` in `book/budgets.py:163`;
  `Inflows`/`Outflows`/`Net` in `book/reporting.py:118-135`;
  `Category`/`Source` at `reporting.py:446,570`).
- Synthetic balance-sheet rows `Retained Earnings` /
  `Unrealized Gain/Loss` (`reporting.py:847,855`) — GnuCash's own
  catalog covers `Retained Earnings`; use it.
- Debt-payoff narrative (`Kill order…`, `Debt-free: {Month YYYY}`,
  `reporting.py:170-220`).
- The `switch_book` CONTEXT RESET banner + orientation snapshot
  (`server.py:771-784`, `:906-910`).
- Advisory sentences: FX sanity-check messages
  (`book/_currency.py:637-661`), budget FX-fold warning
  (`budgets.py:927`), backup refusals (`backup.py:549-704`),
  ambiguity notices (`_base.py:985`, `business.py:567`).

### Tier 4 — fragments, plurals, dates (hardest, lowest value)

- English pluralization baked into f-strings: `{N} account(s)`,
  `{N} day(s) ago`, `"s" if n != 1`. Full CLDR plural rules
  (Slavic 3-form, Arabic 6-form) are out of scope. Policy: **design
  them out** — prefer `Accounts: 5` / label-colon-count shapes over
  grammatical sentences wherever a template is touched anyway.
- `strftime("%b %Y")` / `"%B %Y"` in the debt-payoff report emits
  month names from the *process* locale — a latent bug independent
  of this spec (a de_DE.UTF-8 host already leaks German month names
  into an English book today). Fix with an explicit month-name row
  in the message table, not `locale.setlocale` (process-global,
  thread-hostile — same class as the I18N-8 LOW).
- Unit words: `shares`, `basis`, `/mo`, `/day`, `days`, `(MTD)`.

### Explicitly OUT of scope (declared, not deferred)

- **Error messages** (~300 raise sites) and **exception envelopes**
  — read only by the LLM, which translates or acts on them.
  Localizing them has the worst effort-to-value ratio in the
  codebase and would bloat every mixin. English is the contract.
- **Tool docstrings, param descriptions, `instructions=` block,
  `--help`** — prompts to the model / CLI operator. English.
- **Wire enums**: dict `status` values (`created` / `posted` /
  `paid` / `deleted`…), frequency tokens (`monthly`, `weekly`),
  backup stages, TSV *machine* headers (`ref`, `guid`,
  `dup_count`). Callers match on these; localizing them is a
  breaking change with zero benefit. Declare them protocol.
- **Log-file *names* and debug log** — operator-facing plumbing.

### Related but separate: number formatting

The v1.5 README's Tier D bullet also names decimal-separator
policy. Recommendation: **do not localize number rendering.**
Amounts in TSVs are machine-readable by contract
(`Decimal`-parseable, `.` separator); a `1.234,56` cell breaks
every downstream consumer including the LLM's own arithmetic. The
LLM presents numbers to the user in their convention. One spec
line, closes the question.

---

## 4. Design sketch

### 4.1 Message table module

New `src/gnucash_mcp/_i18n.py` (layer-neutral, like `_format.py`,
importable from both `book/` and `tools/`):

```python
MESSAGES: dict[str, dict[str, str]] = {
    "summary.assets": {"en": "Assets", "de": "Aktiva", ...},
    "runway.days":    {"en": "Runway: {n} days ({detail})", ...},
    "audit.create_transaction": {"en": "CREATE TRANSACTION", ...},
    "month.abbr.1":   {"en": "Jan", "de": "Jan.", "zh": "1月", ...},
    ...
}

def msg(key: str, locale: str | None, **fmt) -> str:
    """Look up key in locale, fall back to en, then format().
    A missing key is a KeyError in tests, en-fallback in prod."""
```

- Keys are concept slugs, dot-namespaced by surface
  (`summary.*`, `report.*`, `audit.*`, `notice.*`, `month.*`).
- Values are `str.format` templates; placeholders are named, never
  positional (translators reorder).
- Locale resolution reuses `_infer_book_locale` (+ the existing
  `GNUCASH_LOCALE` override). Resolve **once per operation** and
  thread it, or cache per book-path — do not re-vote per string.
- English fallback at every lookup; a locale is allowed to be
  partial. This is what makes the 6 uncovered languages and the
  long tail safe.

### 4.2 Contract tests

- Every non-`en` locale's keys ⊆ `en` keys (no orphan
  translations); `en` covers every key used in code (grep-derived,
  same pattern as `TestToolFileVsModulesMapping`).
- Placeholder agreement: each translation's `{name}` set equals the
  `en` template's set — catches the classic broken-format-string
  translation bug at test time, not at render time.
- Oracle locks: Sabine (de) and Lin Wei (zh) report captures —
  this work shifts bookkeeper-validated *text* (not numbers, but
  the capture rig diffs text); run before/after per
  `feedback_before_after_testing.md`.

### 4.3 Phasing

| Phase | Scope | Size |
|---|---|---|
| **P0** | `_i18n.py` module + contract tests + number-format ruling (§3, one doc line) | small |
| **P1** | Tier 1: discount leaves, account descriptions, fallback parents — after the resolver-twins refactor | small |
| **P2** | Tier 2: audit-log vocabulary + audit-locale policy ruling | medium |
| **P3** | Tier 3: `get_book_summary`, then report tables/warnings | large (mechanical but wide) |
| **P4** | Tier 4: month names; plural-avoidance rephrasing opportunistically | small, ongoing |

P1 and P2 are release-worthy alone. P3 is the long tail and can ship
per-surface across releases. Translation coverage can also phase by
language: oracle languages first (de, zh), majors next, long tail
LLM-generated behind the English fallback.

---

## 5. Open questions for the ruling

1. **Script variants** (§2.2) — zh-Hans/zh-Hant split, or accept
   the normalization?
2. **Audit-log locale** (Tier 2) — book locale, or separate
   operator setting?
3. **Translation review bar** (§2.3) — is LLM-generated + English
   fallback acceptable for the long tail, or do we ship only
   reviewed languages and let the rest fall back entirely?
4. **Does Tier 3 clear the bar at all?** The LLM-in-the-middle
   argument cuts hardest here: if the answer is "the model
   translates fine," v1.5 ships P0–P2 only and Tier 3 stays
   English by policy rather than by omission. Legitimate outcome;
   should be chosen, not defaulted into.
