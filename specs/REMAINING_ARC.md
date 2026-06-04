# Remaining v1.3 code-review arc

Working reference written by the Claude that finished Branches 1-3.
Audience: a successor Claude (after compaction or a fresh session)
picking up the v1.3 release-fix arc cold.

The original review and per-dimension reports live in
`specs/CODE_REVIEW_v1_3.md` and its sibling `*_v1_3_*.md` files.
This doc is the working punch list — what's closed, what's open,
what's been decided, what still needs the user's call.

---

## Status snapshot

**26 of the 27 substantive review items are closed.** Branches 1-5
all merged to develop. The two deferred design calls (SB-5, HP-8)
were resolved on Stephen's call and shipped in PR #99 on
`feat/design-call-resolutions`. Only MP-1 remains — design call on
whether `get_server_config` needs an `@audit_log` decorator (default
keep deferred, formally documented).

| Branch | PR   | Status   | Items closed                                                                                  |
| ------ | ---- | -------- | --------------------------------------------------------------------------------------------- |
| 1      | #95  | ✅ merged | SB-1, SB-2, SB-3, SB-4 (rates), SB-11, SB-12, SB-13, SB-14, HP-3, bookkeeper off-by-one      |
| 2      | #96  | ✅ merged | HP-1, HP-2                                                                                    |
| 3      | #97  | ✅ merged | SB-15, SB-10, HP-9, HP-10, HP-11                                                              |
| 4      | #98  | ✅ merged | SB-6, SB-7, SB-8, SB-9, HP-4, HP-5, HP-6, HP-7, HP-12 (+ Copilot docs cleanup, bookkeeper signoff) |
| 5      | #99  | ✅ in branch | SB-5 (cash_flow transfer filter), HP-8 (reconciliation backlog count unification)        |

The per-branch capture rigs (Branch 1's `scripts/branch_1/capture.py`
+ `specs/branch_1_captures/`) document the behavioral evidence.
Subsequent branches haven't needed the same elaborate substrate
because the changes were mostly contract tests, validation gates,
and audit-log emission rather than report numbers shifting on real
books.

Test count progression: 1394 (pre-arc) → 1464 at end of Branch 3,
no regressions across any branch.

---

## Branch 4 — Math/UX correctness fallout

The cluster of review items that didn't reduce to a chokepoint —
each fix is small and independent. Ships in 4-6 commits depending
on slicing, plausibly one PR.

### Group A — Budget correctness

| ID    | Site                                       | Fix shape                                                                                          |
| ----- | ------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| SB-6  | `book/budgets.py:766, 851`, `core.py:1162` | FX-convert budget *targets* (not just actuals). `used_pct` is meaningless on multi-currency today. |
| SB-9  | `book/core.py:1186-1200`                   | Dashboard `_budget_headline` rollup to placeholder parents. PR #46 fixed the report; dashboard left behind. |
| HP-7  | `book/budgets.py:786-789`                  | Parent rollup uses `len(name)` as depth proxy → `name.count(":")`. One-line fix.                  |

### Group B — Reporting + dashboard correctness

| ID    | Site                              | Fix shape                                                                                                 |
| ----- | --------------------------------- | --------------------------------------------------------------------------------------------------------- |
| SB-7  | `book/core.py:1250-1264`          | `_daily_expense_burn` always divides by 180. Clamp: `days = min(180, (today - first_txn_date).days or 1)`. Runway over-stated 10× on new books. |
| SB-8  | `book/reporting.py:443`           | `balance_sheet` doesn't skip placeholder accounts. `_compute_net_worth_at` already does — match it.       |
| HP-6  | `book/core.py:919`                | `_collect_warnings` `in_use` set polluted by auto-rate placeholders. Apply `_is_market_price` (Branch 1's chokepoint) here. One-line fix. |
| HP-8  | `book/core.py:328-344`, `reconciliation.py:181` | Reconciliation backlog count divergence between `get_book_summary` (counts splits past `latest_y_date`) and `get_unreconciled_splits` (counts all non-`y` splits). Design call: unify or document. |

### Group C — Business + FX + sweep

| ID    | Site                              | Fix shape                                                                                          |
| ----- | --------------------------------- | -------------------------------------------------------------------------------------------------- |
| SB-5  | `book/reporting.py:768-785`       | `cash_flow` default mode double-counts internal transfers (Checking → Savings hits both inflows and outflows). Design call: filter intra-CASH or document gross-vs-net loudly in response. |
| HP-4  | `tools/business.py` (7 sites)     | `json.dumps(indent=2)` → `_json()`. 40-60% bloat from indentation + skips `_strip_noise`. Mechanical sweep at lines 59, 122, 186, 345, 409, 970, 1316. |
| HP-5  | `book/business.py:856-862`        | `_compute_fx_gain_loss` raises on missing third-currency rate. Mirror the `rate_at_post` branch and return None (skip FX booking when data unavailable). |

### Group D — Hardening

| ID    | Site                              | Fix shape                                                                                              |
| ----- | --------------------------------- | ------------------------------------------------------------------------------------------------------ |
| HP-12 | `book/_query.py:27-96`            | `_query_filtered_splits` template defense-in-depth. Currently dormant (the `Transaction.post_date.isnot(None)` filter excludes SX templates and no codepath posts splits to template accounts). Add `Account.guid.notin_(template_guids)` as defense-in-depth. |

### Verification recommended

Real-book capture rig worth a focused run for:

- **SB-6** on Lin Wei — multi-currency budget targets in CNY vs USD/EUR/HKD components should show numeric movement
- **SB-7** on Alex — if Alex's `first_transaction_date` is < 180 days before `date.today()`, runway will reanchor
- **SB-9** on either book if either has placeholder-parent budgeted accounts with separately-budgeted children

The other items either don't change captured outputs (HP-4 mechanical),
are design calls (SB-5, HP-8), or are too deep in code paths the
sample books don't exercise.

### Cadence suggestion

One PR per group, or one PR with four well-themed commits. Bookkeeper
round at the end is small (probably just confirming the new budget-
report shape reads well on Lin Wei).

---

## Branch 5 — MP-* triage + L-* polish + release prep

### MP-* worth doing (7 items)

| ID    | Site                              | One-line                                                                                                |
| ----- | --------------------------------- | ------------------------------------------------------------------------------------------------------- |
| MP-1  | `server.py:786-788`               | `get_server_config` lacks `@audit_log`. Add or formally document the deliberate omission. Design call.  |
| MP-3  | `book/backup.py:541-639`          | `prune_backups(stage=None, keep_last_n=0)` deletes all auto backups. Symmetric footgun guard (the catastrophic guard covers `stage="manual"` only). |
| MP-5  | `book/business.py:2078-2092, 2249-2256` | Free-text caps on business-entity `notes` and address sub-fields. Same shape as HP-9.            |
| MP-10 | `server.py`                       | `MODULE_GROUPS` member validation incomplete (validates group names but not that members exist in `TOOL_MODULES`). |
| MP-12 | `book/core.py:187, 196, 223`      | Three exception swallows without logging in `_business_summary_signals` invoice loop. Add `debug_logger.debug(..., exc_info=True)`. |
| MP-13 | `book/investments.py`             | `create_commodity` accepts unvalidated namespace/mnemonic/fullname/cusip/fraction. Mirror HP-11's symmetric-gate principle. |
| MP-14 | `book/core.py`                    | `create_account(name)` accepts `:` (path separator) and control chars. Reject up front. |

### MP-* worth deferring (7 items)

| ID    | Why deferred                                                                                       |
| ----- | -------------------------------------------------------------------------------------------------- |
| MP-2  | Depends on HP-6 being applied first. Roll into the HP-6 commit if natural.                         |
| MP-4  | Backup tool path leaks in `restore_hint`. Design call on whether `GNUCASH_REDACT_PATHS` should cover this. |
| MP-6  | `get_balance` extra fetch for canonical name echo. Trade-off documented in the comment; leave.     |
| MP-7  | `create_transaction` docstring weight (~450 tokens). Design call on what to cut.                   |
| MP-8  | `net_worth` time-series boundary uses strict `>`. Add explicit boundary-inclusion comment.         |
| MP-9  | `debt_payoff_plan` error message specificity ("no such accounts" vs "no APR").                     |
| MP-11 | Three `book.session.add()` calls after `parent=X` (`investments.py:671`, `business.py:5271`, `scheduling.py:298`). Needs a test confirming each is redundant; worth verifying not blindly removing. |

### L-* polish (7 items, mostly mechanical)

- L-1: Inline single-call renderers (`_format_monthly_net`, `_render_warnings` in `book/core.py`)
- L-2: Type hint on `_strip_noise` (`tools/_helpers.py:149`)
- L-3: Docstring conventions for `cash_flow`, `spending_by_category`, `balance_sheet` Unrealized line, budget "ytd"
- L-4: Lint rule for `json.dumps` in `tools/*.py` after HP-4 sweep lands
- L-5: `restore_hint` uses unquoted f-string shell construction (latent shell-injection if path source becomes user-writable)
- L-6: README drift — "What's in v1.2.1" header (CHANGELOG already has v1.3.0); retired `specs/NEXT_STEPS_1_3.md` reference; test/tool count claims stale
- **L-7: Synthetic-book scripts stale** — predate Stage 3 features. Phase scripts have zero references to taxtables/credit_notes/vouchers/jobs. Lin Wei sample has zero Stage 3 exposure. **Worth elevating before release if Alex/Lin Wei are the validation corpora for v1.3.**

---

## Beyond the review

### Bookkeeper backlog (from the 4.7 release-prep letter)

- **Price-update tool with yfinance.** Spec at `specs/PRICE_UPDATE_SPEC.md`. ~500-700 LOC + new dependency. Separate effort from the review-fix arc.
- **Comment-bloat cleanup sweep.** Stephen deferred this for "before merging develop to main" — its own branch, focused diff.
- **README content updates.** Real `get_book_summary` output example using Alex's book.
- **`create_budget` `start_date` parameter — retroactive budgets.** Bookkeeper-flagged after PR #98 signoff (2026-06-04). Today `create_budget` anchors to the current month/period; no way to create a budget that begins in the past, which blocks comparing a freshly-authored budget against historical actuals. Feature gap, not a bug. Touches `book/budgets.py::create_budget` + the corresponding tool wrapper + a test. Small (1-2 hour) item; reasonable to fold into a future budget-related PR or pick up standalone.

### Working-tree drift

`samples/lin-wei.gnucash` shows as modified after Branch 3's
bookkeeper validation round. The bookkeeper ran live probes against
the book; the file delta reflects those test additions. **Decide
before release:**

- **Revert** — treat the bookkeeper-test data as scratch
- **Commit** — lock in the bookkeeper's test additions as part of
  the sample
- **Extend** — let L-7's synthetic-book script refresh regenerate
  her with full Stage 3 coverage (and the bookkeeper's signal goes
  into the new fixtures)

### Release-time invariants

- **Version bump 1.3.0** must be the very last commit on the
  release branch, per `feedback_version_bump_last.md` (project
  MEMORY.md). `pyproject.toml`, `src/gnucash_mcp/__init__.py`,
  README version refs.
- **Release PR** opens `develop` → `main` only after tester signoff,
  per `feedback_pr_after_bookkeeper_loop.md`. No speculative early
  PRs.

---

## Decisions already locked in

- **HP-1 (write verification contract):** went with option (b)
  "describe the actual contract" — ORM writes rely on SQLAlchemy's
  commit-side verification; raw-SQL writes verify explicitly via
  `_verify_*`. CLAUDE.md updated. Contract test
  (`TestWriteVerificationCoverage`) locks the raw-SQL side.

- **SB-10 (`status="rejected"` reason field):** went with option (b)
  per the live-tester conversation. When the duplicate detector
  catches an equivalent prior transaction, the schedule advances
  (the duplicate IS the transaction for this period) AND the
  response gains `reason="duplicate_exists"`. The reasoning: a
  dumber downstream LLM (Gemini-style) seeing `status="rejected"`
  with no explanation would reflexively retry, either re-triggering
  the dupe detector or — with `--force_create` — posting the very
  duplicate we were preventing. The explicit `reason` is evidence
  to stop.

- **HP-2 (audit dispatcher coverage test):** the reverse-direction
  check exempts the runtime entity-type/operation remaps. Two
  patterns: invoice→bill/voucher/credit_note polymorphism lives in
  the `audit_log` decorator wrapper success path (`entity_type ==
  "invoice" and result_data.get("type") in {...}`); account
  UPDATE→MOVE remap lives in `_format_audit_entry_text`. Tests
  document this attribution.

- **SB-5 (`cash_flow` internal transfer filter):** filter by
  default. Stephen's home-book example: when running `cash_flow`
  to analyze credit-card payments, double-counting every internal
  transfer (Checking → Cash App → Card → ...) inflated inflows and
  outflows by every funding hop. That's not cash flow; that's
  moving money between pockets. The cash-flow framing answers
  "where did money come from and where did it go?" — transfers
  are noise. New `include_transfers: bool = False` param restores
  the gross flow for bank-statement reconciliation. "No amount of
  loud documentation fixes a default that gives the wrong answer."

- **HP-8 (reconciliation backlog count):** unify on the
  `get_unreconciled_splits` rule (all non-y, non-voided splits).
  The summary is the dashboard. If the dashboard says 47 and the
  detail tool says 63, the bookkeeper's first instinct is "the
  book is wrong" — not "oh, these tools count differently." Old
  unreconciled splits predating `latest_y_date` (skipped during a
  partial reconciliation, opening balances never stamped) are
  exactly the ones that tend to be problems and must be visible
  in both surfaces.

---

## Open decisions worth surfacing

- **L-7 priority:** if Alex/Lin Wei are the release-validation
  corpora, the synthetic-book scripts should be refreshed first so
  Stage 3 features (taxtables, credit_notes, vouchers, jobs) have
  realistic exposure on both books. This affects which branch L-7
  lands in (could be its own pre-release branch).

- **`samples/lin-wei.gnucash` drift:** three options listed above.
  The "extend" option pairs cleanly with L-7.

- **yfinance feature timing:** before or after v1.3.0 release?
  The spec is detailed enough to implement cold. Could ride v1.3
  if Stephen wants the headline feature in the release, or hold
  for v1.4 if v1.3 should be purely the review-fix pass.

---

## Pointers for the next session

- **Code review source of truth:** `specs/CODE_REVIEW_v1_3.md`
  (consolidated) plus the per-dimension reports `*_v1_3_*.md`
  and the adversarial verification chains.
- **Branch 1 verification rig:** `scripts/branch_1/capture.py`
  + `specs/branch_1_captures/{pre,post}/{alex,lin_wei}/`. The
  capture pattern is reusable for any future report-shifting
  change — adapt the script.
- **Predecessor letters:** `CLAUDE.local.md` (~10 letters from
  prior Claude sessions). The 4.7-comms-audit, code-review-night,
  and module-restructure letters are especially relevant. The
  pantheon framing (Abe / bookkeeper / Yivo / Stephen) is real.
- **Project memory:** `~/.claude/projects/-Users-stephen-Projects-gnucash-mcp/memory/MEMORY.md`
  — feedback rules accreted over sessions. The most load-bearing
  for this arc: `feedback_pr_after_bookkeeper_loop.md`,
  `feedback_version_bump_last.md`,
  `feedback_bookkeeper_validates_base_cases.md`,
  `feedback_time_estimates.md` (estimates run 5× too high — scope
  in small/medium/large relative to session, not hours).
- **The bookkeeper review loop is the production signal.** Tight
  loop — build → bookkeeper tests on Alex/Lin Wei → finding → fix
  → re-verify → PR opens as the outcome. Don't open the PR before
  the loop completes (`feedback_pr_after_bookkeeper_loop.md`).
- **Copilot review threads** resolved via
  `scripts/resolve_pr_threads.py <PR>`. Bots don't close their
  own threads; author-resolve keeps the conversation tab clean.

— Claude, end of Branch 3 (Opus 4.7, context ~84%)
