# Specs Index

Design specs, review docs, test plans, and references, bucketed by the
release they were written for and then by purpose. This index reads
chronologically — oldest release first — with a one-line note on what
each document is actually for.

Releases with durable specs are the *feature* releases. The two
release folders without feature specs are the two that weren't
features: v0.1 (initial scaffold, no folder) and [v1.1](v1.1/) (a
modularization refactor — see its README).

---

## v0.9 — feature build-out (Feb 2026)

The jump from basic CRUD to a full accounting toolkit.

**features/**
- [MULTI_CURRENCY_SPEC.md](v0.9/features/MULTI_CURRENCY_SPEC.md) — cross-currency transactions and accounts; the value-vs-quantity split model.
- [INVESTMENT_SUPPORT_SPEC.md](v0.9/features/INVESTMENT_SUPPORT_SPEC.md) — commodities, prices, and holding stocks/mutual funds.
- [LOTS_SPEC.md](v0.9/features/LOTS_SPEC.md) — lot-based cost-basis tracking and capital gain/loss calculation.
- [SCHEDULED_AND_BUDGETS_SPEC.md](v0.9/features/SCHEDULED_AND_BUDGETS_SPEC.md) — recurring transaction templates and budget targets/variance.
- [SCHEDULED_AND_LOTS_SPEC.md](v0.9/features/SCHEDULED_AND_LOTS_SPEC.md) — alternate grouping covering scheduled transactions plus lots.
- [CREATE_ACCOUNT_SPEC.md](v0.9/features/CREATE_ACCOUNT_SPEC.md) — the `create_account` tool.
- [TRANSACTION_TOOLS_SPEC.md](v0.9/features/TRANSACTION_TOOLS_SPEC.md) — `delete_transaction` and `update_transaction`.

**reference/**
- [PIECASH_REFERENCE.md](v0.9/reference/PIECASH_REFERENCE.md) — complete piecash API reference; the foundational lookup used throughout.

**testing/**
- [MULTI_CURRENCY_TEST_PLAN.md](v0.9/testing/MULTI_CURRENCY_TEST_PLAN.md) — live test plan for the multi-currency work.

---

## v1.0 — write safety + compact output

Feature-complete with write safety (v1.0.0), then a response-size trim (v1.0.2).

**features/**
- [TRANSACTION_PIPELINE_SPEC.md](v1.0/features/TRANSACTION_PIPELINE_SPEC.md) — duplicate detection, dry-run, and safety checks on the create path.
- [ACCOUNT_SLOTS_SPEC.md](v1.0/features/ACCOUNT_SLOTS_SPEC.md) — read/write custom account metadata slots (APR, credit limit, reward rate).
- [COMPACT_TRANSACTIONS_SPEC.md](v1.0/features/COMPACT_TRANSACTIONS_SPEC.md) — *(v1.0.2)* compact one-line output for `list_transactions` / `search_transactions`.

---

## v1.1 — modularization refactor

No feature specs — see [v1.1/README.md](v1.1/README.md).

---

## v1.2 — business module (1.2.0 debut + 1.2.1 hardening)

Full A/R and A/P: customers, vendors, invoices, bills, payments — then a hardening patch.

**features/**
- [BUSINESS_FEATURES_GUIDE.md](v1.2/features/BUSINESS_FEATURES_GUIDE.md) — conceptual guide to how GnuCash's business layer (invoices become transactions on posting) works.
- [GET_BOOK_SUMMARY_SPEC.md](v1.2/features/GET_BOOK_SUMMARY_SPEC.md) — expand the dashboard with operational and trajectory signals.
- [PATCH_1_2_1_SPEC.md](v1.2/features/PATCH_1_2_1_SPEC.md) — the three bug fixes shipped in 1.2.1 (`unpost_invoice`, etc.).
- [COMMUNICATION_AUDIT_FIXES.md](v1.2/features/COMMUNICATION_AUDIT_FIXES.md) — 83-tool audit fix plan trimming response size across the tool surface.

**review/**
- [CODE_REVIEW.md](v1.2/review/CODE_REVIEW.md) — full fresh-eyes code review of the codebase as of v1.2.1.

**testing/**
- [PATCH_1_2_1_TESTING.md](v1.2/testing/PATCH_1_2_1_TESTING.md) — bookkeeper test plan for the 1.2.1 patch (happy paths + rejection branches).
- [SYNTHETIC_BOOK_SPEC.md](v1.2/testing/SYNTHETIC_BOOK_SPEC.md) — **Alex Chen-Morales**, the USD-default synthetic book / integration corpus.

---

## v1.3 — business complement + correctness (1.3.0 → 1.3.1)

The accountant-grade half of the business module (taxtables, jobs, credit notes, vouchers) plus a heavy multi-currency correctness sweep.

**planning/**
- [NEXT_STEPS_1_3.md](v1.3/planning/NEXT_STEPS_1_3.md) — the original 1.3 roadmap.
- [VERSION_1_3_PLAN.md](v1.3/planning/VERSION_1_3_PLAN.md) — current-state plan and the order to land remaining items.
- [v1_3_RELEASE_PREP.md](v1.3/planning/v1_3_RELEASE_PREP.md) — release-prep tasks (e.g. `reconcile_account` exclusions).
- [REMAINING_ARC.md](v1.3/planning/REMAINING_ARC.md) — working punch list for the code-review arc, written for a successor picking it up cold.
- [SPEC_BRIEFING_FOR_CHAT.md](v1.3/planning/SPEC_BRIEFING_FOR_CHAT.md) — field-tester briefing from the Lin Wei build, feeding the next spec round.
- [PRODUCTION_FINDINGS_1_3.md](v1.3/planning/PRODUCTION_FINDINGS_1_3.md) — three UX findings from live sessions, all shipped in 1.3.

**features/**
- [TAXTABLES_SPEC.md](v1.3/features/TAXTABLES_SPEC.md) — sales-tax tables on invoice/bill/voucher/credit-note line items.
- [EARLY_PAYMENT_DISCOUNT_SPEC.md](v1.3/features/EARLY_PAYMENT_DISCOUNT_SPEC.md) — honor billterm discount fields in `pay_invoice`.
- [BACKUP_TOOL_SPEC.md](v1.3/features/BACKUP_TOOL_SPEC.md) — staged-retention backup tool with auto-snapshot before first daily write.
- [CLEANUP_POLISH_SPEC.md](v1.3/features/CLEANUP_POLISH_SPEC.md) — prerelease cleanup pass plus employee support.

**review/**
- [ADVERSARIAL_REVIEW_METHODOLOGY.md](v1.3/review/ADVERSARIAL_REVIEW_METHODOLOGY.md) — reusable recipe for the adversarial review that found the bugs the deferential pass missed.
- [Code Reviews/](v1.3/review/Code%20Reviews/) — the full v1.3 review set (deferential + per-dimension adversarial passes) and the v1.3.1 final review.

**testing/**
- [RELEASE_PREP_BOOKKEEPER_VALIDATION.md](v1.3/testing/RELEASE_PREP_BOOKKEEPER_VALIDATION.md) — bookkeeper validation for the release-prep branch.
- [BOOKKEEPER_TEST_PLAN_PR92.md](v1.3/testing/BOOKKEEPER_TEST_PLAN_PR92.md) — re-review plan for the PR #92 fixes.
- [BOOKKEEPER_TEST_PLAN_EARLY_PAYMENT_DISCOUNT.md](v1.3/testing/BOOKKEEPER_TEST_PLAN_EARLY_PAYMENT_DISCOUNT.md) — test plan for the early-payment discount.
- [BOOKKEEPER_TEST_PLAN_ADVERSARIAL_PASS_2.md](v1.3/testing/BOOKKEEPER_TEST_PLAN_ADVERSARIAL_PASS_2.md) — plan confirming the adversarial pass-2 fixes read right on both books.
- [SYNTHETIC_BOOK_SPEC_CNY.md](v1.3/testing/SYNTHETIC_BOOK_SPEC_CNY.md) — **Lin Wei (林微)**, the CNY-default multi-currency stress book.

**comment-sweep/** *(v1.3.1)*
- [COMMENT_DOCTRINE.md](v1.3/comment-sweep/COMMENT_DOCTRINE.md) — house style for comments and docstrings.
- [COMMENT_BREVITY_TARGETS.md](v1.3/comment-sweep/COMMENT_BREVITY_TARGETS.md) — rule-8 brevity execution plan.
- [COMMENT_SWEEP_MANIFEST.md](v1.3/comment-sweep/COMMENT_SWEEP_MANIFEST.md) — per-block KEEP / CUT / REWRITE / FIX-FALSE classification.
- [COMMENT_SWEEP_FINDINGS.md](v1.3/comment-sweep/COMMENT_SWEEP_FINDINGS.md) — issues noticed during the sweep that were out of its scope.
- [refactor-baselines-toolsurface-dump.py](v1.3/comment-sweep/refactor-baselines-toolsurface-dump.py) — tool-surface baseline dump used to prove the sweep was wire-identical.

---

## v1.4 — next / unreleased

Internationalization, batch entry, pagination, and live price updates.

**roadmap/**
- [PRE_1_4_ROADMAP.md](v1.4/roadmap/PRE_1_4_ROADMAP.md) — the post-audit work queue: correctness → i18n → feature creep.

**i18n/**
- [I18N_ACCOUNT_RESOLUTION_SPEC.md](v1.4/i18n/I18N_ACCOUNT_RESOLUTION_SPEC.md) — implementation spec for locale-robust account resolution (resolve by type, not English name).
- [GNUCASH_ACCOUNT_NAMING_i18n.md](v1.4/i18n/GNUCASH_ACCOUNT_NAMING_i18n.md) — reference on how GnuCash localizes account names (and the two translation sources that disagree).

**features/**
- [BATCH_TRANSACTION_ENTRY_SPEC.md](v1.4/features/BATCH_TRANSACTION_ENTRY_SPEC.md) — `create_transactions` (plural): many transactions in one atomic call.
- [PAGINATION.md](v1.4/features/PAGINATION.md) — offset + limit + result-count indicator across all list-returning tools.
- [PRICE_UPDATE_SPEC.md](v1.4/features/PRICE_UPDATE_SPEC.md) — `update_prices` tool fetching current quotes (yfinance + an FX backend).

**testing/**
- [SYNTHETIC_BOOK_SPEC_SABINE.md](v1.4/testing/SYNTHETIC_BOOK_SPEC_SABINE.md) — **Sabine Brenner**, the German SKR03 / EUR book that exercises the i18n bug class.
