# Comment Brevity Pass — Targets

Rule 8 execution plan. Companion to [COMMENT_DOCTRINE.md](COMMENT_DOCTRINE.md)
(rule 8) and successor to [COMMENT_SWEEP_MANIFEST.md](COMMENT_SWEEP_MANIFEST.md).
Drafted 2026-06-12 on `chore/comment-sweep` at the post-sweep state.

> **AS-BUILT (2026-06-12).** Executed in five gated commits
> (`ef67c8d` business · `60b269a` core · `aa4891d`
> base/query/currency · `505c2c2`
> reporting/recon/sched/budgets/invest/backup · `dd67ded`
> logging/format/server/tools), all appended to
> `.git-blame-ignore-revs`. Net result: **−3,461 lines** across 17
> files (estimate was 2,100–2,500 — docstring rewrites tightened
> more surrounding prose than the per-block budgets predicted);
> src/ commentary went **39% → 31.7%**. Every commit passed all
> three gates (pytest 1,584 / AST-identical after docstring strip /
> wire-surface byte-identical). The DO-NOT-TOUCH list below was
> honored in full; trap stories survive in 1–2-sentence form per
> rule 3's floor. The doctest caution resolved benign: pytest does
> not collect doctests, so `_format_number`'s Examples were trimmed
> to one. A handful of `Returns:` lines that still claimed a `guid`
> key on guid-omitted responses were corrected in passing (rule 6).
> The tables below are retained as drafted, as the record of the
> budgets the edits were made against.

**Anchors are symbol names, not line numbers** — line numbers drift; locate
every block by its enclosing function/class/header text. If executing after
a compaction, re-read the doctrine and the target file's section for the
file being edited before touching anything.

## Scope and gates

- Same hard constraints as the sweep: **zero executable-line changes**;
  tool-function docstrings and pydantic Field descriptions / model
  docstrings in `tools/*.py` are **wire surface — untouchable** (verified
  by `specs/refactor-baselines-toolsurface-dump.py` full-schema diff,
  which must be byte-identical).
- Gates per commit: `uv run pytest` (1,584), AST-identical-after-docstring-
  strip vs. the pre-pass commit, wire-surface diff empty.
- **Doctest caution:** `_format_number`'s Examples section is doctest-
  formatted. Before trimming any `>>>` examples, confirm pytest does not
  collect doctests (`--doctest-modules` absent from config); if it does,
  those examples are tests and out of scope.
- Budgets below are guidance, not quotas. Rule 8's floor clause governs:
  if a budget can't be met without losing an invariant or trap, keep the
  lines and note the entry as KEPT-LONG in the as-built update.

## Candidate universe

461 blocks ≥ threshold (7+ line `#` runs, 12+ line docstrings), 8,120
lines total, of which roughly 1,100 are wire-surface tool docstrings
(out of scope). **Estimated removable: ~2,000–2,400 lines.**

## Strategy codes

- **S1** trap story → 1–2 sentences (rule 8 / rule 3 floor)
- **S2** dedup: rationale already lives on a chokepoint/callee → pointer
- **S3** Args/Returns entries restating name, type hint, or default → cut
- **S4** multiple format examples → one
- **S5** control-flow / skeleton narration → cut
- **S6** repeated per-site boilerplate (absence-as-signal, guid-omitted,
  echo-dropped rationale) → state once per file, pointer elsewhere

## DO NOT TOUCH (rule 1/3 floor, regardless of length)

- `business.py`: credit-note slot header; Job CRUD header (doctrine
  specimens); overpayment guard block; XOR posting-direction comment;
  `_compute_entry_tax` quadrants + rounding-residual paragraphs;
  gncInvoice FRAME+GUID slot structure; Address-composite gotcha
  (in `_update_business_person` body).
- `backup.py`: both prune footgun guards; symlink/uid checks rationale.
- `_query.py`: the `_DateAsDateTime` 10:59-neutral-time block.
- `reporting.py`: strict-`>` boundary comment in net_worth sweep;
  never-skip-placeholders comment.
- `_currency.py`: `_anchor_for_as_of` events-vs-revaluations convention
  (incl. the TestCrossToolPriceAgreement pointer); FX guard-vs-cap
  three-band header.
- `core.py`: `_is_template_transaction` / template-filter rationale;
  duplicate-detector primary-amount trap.
- All piecash-gotcha one-liners anywhere.
- All wire surface (tool docstrings, Field descriptions,
  `SplitInput`/`BusinessAddressInput` docstrings).

## Tier 1 — named blocks (anchor | now → budget | strategies | keep)

### book/business.py (~850 lines removable)

| anchor | now→budget | strat | keep |
|---|---|---|---|
| `pay_invoice` docstring | 78→30 | S2,S3 | amount-is-invoice-currency contract; partial payments; failure-mode list. FX routing → `_get_or_create_fx_account`; discount rules → `_compute_discount_summary` |
| `_compute_fx_gain_loss` docstring | 71→35 | S2,S3 | sign convention para; Returns contract; caller invariants. Cut per-arg restatements + rate-resolution narrative (body comments cover it) |
| `_compute_entry_tax` docstring | 61→45 | S3 | quadrants + residual policy are the contract — trim Args only |
| `create_credit_note` docstring | 60→30 | S2,S3 | linking validation + employee exclusion stated once (exclusion also on `_resolve_credit_note` — keep one, point) |
| `apply_credit_note` docstring | 57→30 | S1,S3 | booking shape + validation list; compress surrounding prose |
| `_convert_invoice_amount` docstring | 52→30 | S3 | three-band freshness guard; Returns tuple semantics |
| `_update_business_person` docstring | 46→25 | S3 | address-merge semantics; clear-with-empty-string |
| `get_job_report` docstring | 42→22 | S3 | totals_by_currency shape; posted-vs-unposted rule |
| `_invoice_to_dict` docstring | 39→20 | S6 | conditional-key rule stated once |
| `_find_invoice` docstring | 39→25 | S1 | self-heal semantics; collision contract |
| `add_credit_note_entry` docstring | 37→15 | S2 | mirrors `_add_entry`; account-type rule per side |
| `_get_invoice_entries_and_total` docstring | 37→25 | S3 | Returns dict keys are the contract |
| `_get_or_create_fx_account` docstring | 34→25 | — | resolution-order list is the contract; trim prose around it |
| `_delete_business_person` docstring | 33→15 | S5 | the numbered skeleton narrates the body — cut; keep slot-cleanup rationale |
| `update_taxtable` docstring | 32→20 | S1 | entries-replacement-is-live warning |
| `_validate_taxtable_entries` docstring | 32→20 | S3 | validation list; same-commodity rule |
| `create_taxtable` docstring | 31→18 | S2 | entries shape → `_validate_taxtable_entries` |
| `_compute_discount_summary` docstring | 30→20 | S3 | None-conditions; pre-tax-principal rule |
| `_create_business_person` docstring | 30→18 | S3 | extra_kwargs passthrough + Employee-no-notes |
| `unpost_invoice` docstring | 29→18 | S1 | payments-applied refusal |
| `_add_entry` docstring | 29→20 | S3 | taxtable wire-up + refcount side effect |
| `update_customer` docstring | 28→12 | S2 | → `_update_business_person` |
| `create_invoice`/`create_bill`/`create_voucher` docstrings | 27 ea→15 | S2,S3 | currency-resolution order; job_id constraint — state once on `_create_business_document` |
| `_resolve_invoice_due_date` docstring | 27→18 | — | 3-step resolution list is the contract |
| `_create_business_document` docstring | 26→18 | S3 | owner-currency-first rule |
| `_format_outstanding_invoices_compact` docstring | ~25→14 | S4 | one row example; (CN)/(BILL)/overpaid semantics |
| post_invoice owner-type-disambiguation `#` block | ~18→10 | S1 | collision + post-account-type inference |
| truthy-date-check `#` block (`_is_invoice_posted` call site in post_invoice) | ~13→6 | S2 | → `_safe_invoice_date`; keep cross-reference list out |
| early-payment discount validation `#` blocks in pay_invoice | ~25→14 | S1 | five-rejection-cases line; book-at-shortfall trap |

### book/core.py (~500 lines removable)

| anchor | now→budget | strat | keep |
|---|---|---|---|
| `_collect_warnings` docstring | 65→30 | S2,S5 | ordering rationale; swallow-per-category contract. Per-category coverage bullets duplicate the collectors' inline comments |
| `_collect_create_signals` docstring | 48→28 | S3 | want_* flag semantics; must-run-before-commit invariant |
| `_runway_metrics` docstring | 46→28 | S2 | liquid definition + special cases; exclusions → `_RUNWAY_LIQUID_TYPES` / `_is_in_retirement_subtree` |
| `_validate_transaction_splits` docstring | 44→25 | S3 | three input-shape rules; validate-before-mutate trap (1 sentence) |
| `create_transaction` docstring | 42→30 | S3 | TSV duplicates format + signals code are contract |
| `_budget_headline` docstring | 41→22 | S5 | formulas + rollup rule; cut the spec-fallback discussion paragraph |
| `_compute_net_worth_at` docstring | 39→22 | S2 | own-splits rule (1 line + pointer); asset/liability conversion bullets compress |
| `_account_reconciliation_status` docstring | 38→22 | S3 | filtering rules list; returned-dict shape |
| `_CreateSignals` docstring | 34→20 | S3 | attribute docs trim to one line each |
| `_monthly_net_income` docstring | 32→18 | S3 | sign convention; MTD flag; empty-list contract |
| `search_transactions`/`list_transactions`/`update_transaction`/`replace_splits` docstrings | 25–27 ea→15–18 | S3 | truncation contract once; thin-response rationale once |
| module docstring | 25→15 | S5 | holds + MRO dependency note |
| `_RUNWAY_LIQUID_TYPES` `#` block | ~23→12 | S1 | ASSET-overcounts trap (2 sentences); recategorize-as-BANK escape |
| `_render_reconciliation` sub-line `#` comment | ~17→6 | S1 | oldest-anchored lag = scope-of-work signal |
| get_book_summary today-filter `#` block | ~16→9 | — | events-vs-prices split (keep both halves, tighten) |
| section-renderers `#` header | ~14→7 | S5 | collector→renderer pattern, one paragraph |
| `_format_reconciliation_lag` docstring | ~27→12 | S5 | unit thresholds; cut the rounding essay |
| get_server_config no-audit-log `#` block (server.py) | 17→9 | S1 | the-omission-is-the-contract line survives |

### book/_base.py (~160)

| anchor | now→budget | strat |
|---|---|---|
| `_normalize_account_refs` docstring | 38→20 | S3 (mechanics-vs-config split, splits-always-walked rule) |
| `_transaction_to_compact_line` docstring | 35→22 | S4 (both shapes stay — they differ; one example each) |
| `_is_unreconciled` docstring | 31→18 | scope paragraph → 3 lines |
| `_resolve_guid` docstring | 28→18 | S3 |
| `_resolve_account` docstring | 28→20 | three shapes + template chokepoint |
| `_split_to_compact_dict` docstring | ~24→14 | S3 (emits/omits lists → terse) |
| GUID-prefix-protection `#` header | ~19→12 | birthday-problem rationale tightened |
| `_is_voided` docstring | ~20→14 | state-only semantics + both corruption directions |

### book/reporting.py (~170)

| anchor | now→budget | strat |
|---|---|---|
| `cash_flow` docstring | 49→28 | scope rule + transfer rationale tightened; settlements para → pointer to `_cashflow_txn_guids` (S2) |
| `balance_sheet` docstring | 34→22 | S2: two-effects decomposition stated ONCE — here or the Unrealized `#` block, not both |
| Unrealized gain/loss `#` block | 27→10 | S2: same duplication, keep the residual-formula side |
| `debt_payoff_plan` docstring | 33→20 | S4 one example; YETI definition |
| `_format_debt_payoff_compact` docstring | ~30→18 | S4 |
| `_cashflow_txn_guids` docstring | 29→18 | two qualifying shapes; FX-drift trap 1 sentence |
| `spending_by_category` docstring | 26→16 | S3; depth semantics keep |
| `_money_compact`/`_format_breakdown_tsv` docstrings | ~14 ea→8 | S4 |

### book/_currency.py (~90)

| anchor | now→budget | strat |
|---|---|---|
| `_find_exchange_rate_aged` docstring | 54→32 | preference order, cap, allow_after stay; compress prose |
| `_rates_as_of` docstring | 42→24 | S2: chaining detail → `_market_rate_to_default_with_path` |
| module docstring | 32→18 | single-source-of-truth list → terse |
| `_market_value` docstring | 30→18 | S3 |
| `_cross_rate_with_path` docstring | 26→18 | pivot scoring rule stays |

### logging_config.py (~75)

| anchor | now→budget | strat |
|---|---|---|
| `resolve_mcp_dir` docstring | 43→26 | both checks + symlink vector stay (DO-NOT-TOUCH adjacent); trim Args/Returns (S3) |
| `_resolve_entry_field` docstring | 29→16 | lookup order list stays |
| `redact_paths` docstring | 27→14 | opt-in posture + basename-only rule |
| `_WriteRateLimiter` + `_get_write_rate_limiter` docstrings | ~24→14 | S3 |
| `_fmt_taxtable_entry_line` docstring | ~14→7 | S2 (audit-canonicalization nuance 1 line) |

### Remaining files (~230 total)

- **book/reconciliation.py:** `reconcile_account` 57→32 (modes table
  stays; S3 on args), `get_unreconciled_splits` 36→20 (currency-unit
  note stays), `void_transaction` 29→18 (warning-not-gating rule stays).
- **book/scheduling.py:** `create_transaction_from_scheduled` 51→30
  (three-phase list is the contract; S3 elsewhere), `_upcoming_within_days`
  19→10, `update_scheduled_transaction` end_date sentinel 20→12
  (sentinel semantics stay).
- **book/budgets.py:** `get_budget_report` 35→20 (period enumeration
  stays), `create_budget` 26→14, `_collapse_period_runs` /
  `_format_budget_report_compact` S4.
- **book/investments.py:** `delete_price` 27→15, `create_price` 25→15,
  `get_prices`/`get_latest_price` S3; `_lot_decimals` precision rationale
  stays (~14).
- **book/backup.py:** `create_backup` 25→16 (S3); retention/footgun
  blocks DO NOT TOUCH; `_maybe_auto_backup` 14→10.
- **book/_query.py:** `_query_filtered_splits` 43→26 (Decimal-not-SQL +
  inclusive-bound notes stay; S3 args).
- **_format.py:** `_apply_limit` 33→20 (three notice cases stay),
  `_format_number` 30→18 (S4 — subject to the doctest caution),
  `_book_display_name` 25→12.
- **server.py:** MODULE_BACKED_BY header 15→8; freelancer /
  business_complete placement comments S6 (placement rationale once);
  `--help` string is executable — untouched.
- **tools/ (`#` comments + non-wire docstrings only):**
  `_gate_owner_type` 33→18 (three cases stay), free-text-caps `#` block
  20→12, SplitInput `#` block 20→12, tools/core.py double-fetch `#`
  comment 17→8, tools/admin.py audit-read comments ~10→6.

## Tier 2 — blanket rules for the 7–14 line tail (~300 blocks)

Apply during the same per-file pass, no per-block entries:

1. S3 everywhere: delete Args/Returns lines that restate the signature.
2. S6: "guid omitted — addressed by id", "echoed input — dropped",
   "absence-as-signal" rationales appear dozens of times; keep the first
   per file, reduce the rest to the bare statement without rationale.
3. S2: every call-site comment that re-explains `_is_voided`,
   `_is_market_price`, `_resolve_account`, `_template_account_guids`,
   `_apply_limit`, or prefix-map semantics shrinks to ≤2 lines + the
   helper name.
4. S5: delete comments that paraphrase the next statement.

## Execution order and commits

One commit per file group, largest first (business → core → base/query/
currency → reporting/recon/sched/budgets/invest/backup → logging/format/
server/tools), each gated (pytest + AST + wire diff), then a final
as-built update to this file. Add the new commit hashes to
`.git-blame-ignore-revs`.

## Savings roll-up (estimate)

business ~850 · core ~500 · reporting ~170 · _base ~160 · _currency ~90 ·
logging ~75 · remaining ~230 · Tier 2 tail ~250–400 ⇒ **~2,100–2,500
lines**, taking commentary from 39% of the codebase to ~31–32%.
