# Business-surface consolidation — v1.5.0 implementation spec

**AMENDED 2026-08-29** (see §Amendments at bottom): implementation
begun on the maintainer's directive; the migration ceremony is
REMOVED from the design — old names exit outright in v1.5.0, no
`business_legacy` module, per the maintainer's ruling that
AI-facing surface needs no deprecation cycle (stateless consumers
re-read the surface every session; only the human-facing contract
gets warnings). Read the amendments before implementing from the
body below.

For the next session picking this up cold. Everything you need is
in this file, `CODEX_TOOL_GUIDE.md` beside it (the full outside
audit this spec distills — read its "Consolidation plan" section
before writing code), and `CODEX_TEST_FINDINGS.md` +
`testing/CROSS_MODEL_BATTERY_2026_08_05.md` for the bug/battery
history that motivated all of this.

## Status and authority

- Design source: ChatGPT/Codex contract audit, 2026-08-05, on
  branch `fix/business-doc-lifecycle` (all 111 tools cataloged,
  suite verified 1,972 passing at time of audit).
- Prior ruling: consolidation was litigated pre-v1.3 and HELD AT
  NO (asymmetry, LLM clarity over count, --modules as the lever).
  The re-litigation trigger ("if the count grows") has since been
  met three independent ways: Glama scored Tool Count 2/5, the
  outside-model battery flagged the document family as
  non-orthogonal, and the Codex audit produced this design.
- Stephen has NOT yet ruled GO on implementation. This spec is the
  shovel-ready plan for when he does. Do not start without his
  word; do not open a PR before the validation loop (standing
  rules).

## The decision already argued (don't re-derive)

Consolidate across **party/document TYPE**; keep materially
different **ACTIONS** as separate tools. Reject the aggressive
19-tool variant and every `manage_*(action=...)` union: they
sacrifice truthful per-action ToolAnnotations, which this repo
ships (derived from @audit_log at the _apply_module_filter
chokepoint) and scored with. One tool = one safety class, always.
The target is the audit's "strict annotation fidelity" variant:

**Business surface: 48 → 25 tools** (all-modules ≈ 87 + switch_book).

- Parties (15 → 5): `create_party`, `list_parties`, `get_party`,
  `update_party`, `delete_party` — `party_type:
  customer|vendor|employee` required (ID counters collide across
  types; this repo has the scars: see the apply_credit_note
  owner-mismatch fix). Employee has no notes field — reject,
  don't ignore.
- Documents (18 → 8): `create_document`, `add_document_entry`,
  `list_documents`, `get_document`, `post_document`,
  `unpost_document`, `pay_document`, `delete_document` —
  `document_type: invoice|bill|voucher|credit_note`. Keep
  `apply_credit_note` separate (9 doc-family tools total) rather
  than folding a settlement union into pay: the union needs too
  many nullable fields.
- Reference data (7 → 4): `list_taxtables`, `get_taxtable` merged
  into list(id=...) or kept — pick ONE pattern and match jobs;
  mutations stay separate per safety class. Billterms gain
  nothing from unions: keep `create_billterm`, `list_billterms`.
- Jobs (6 → 5): merge `get_job` into `list_jobs(id=...)` (both
  reads, same annotations); keep create/update/delete/report.
- Reports (2, renamed 1): `get_outstanding_invoices` →
  `get_outstanding_documents`; `vendor_spending_report` stays.

Arithmetic check before you trust any count: recount from
TOOL_MODULES, not from this file. The audit itself shipped one
count error and corrected it.

## Implementation shape (the audit's steps, amended)

1. NO book-layer rewrite. The book mixin already routes
   polymorphically (`_find_invoice`, `_effective_owner_type`,
   `_ENTRY_CONFIG`, shared create path with `extra_slots`).
   Consolidation is a tool-wrapper routing layer in
   `tools/business.py`.
2. `Literal` types for `party_type` / `document_type`; typed
   `TaxTableEntryInput` nested model replacing `list[dict]`
   (audit doc-fix #7 — do it here, it changes parameter schemas
   and belongs in this schema-changing release).
3. Every mutation returns canonical `type`, `id`, `owner_type`,
   `status` — no follow-up lookup needed (the correlatable-output
   doctrine).
4. New names registered in revised `freelancer` /
   `business_complete` TOOL_MODULES lists; old names move to an
   opt-in `business_legacy` module for ONE compatibility cycle.
   Never expose old and new by default together. Note
   MODULE_BACKED_BY still maps everything onto tools/business.py.
5. Annotations derive automatically (audit_log classification →
   hints) — new wrappers must carry @audit_log like all others;
   the TestToolAnnotations contract will fail loud if not.
6. Contract tests to extend: TOOL_MODULES set-equality counts,
   TestToolFileVsModulesMapping, TestToolAnnotations,
   TestVerboseDocstringConvention (new list tools must use the
   canonical verbose sentence), TestShortGuidRoundTripClosure
   untouched (no new GUID entities). Add: legacy-parity test
   (every legacy tool's behavior reachable via a new tool),
   cross-type ID-collision error tests, credit-note identity
   through post/unpost (exists: TestCodexCrossModelFindings),
   voucher lifecycle via new names.
7. Audit-log dispatch: new wrappers keep existing
   (entity_type, operation) pairs by routing to the same book
   methods — verify the dispatcher renders new-tool writes
   identically (the audit log is the human-readable surface).

## Validation gates, in order

1. Full suite green.
2. Wire-surface capture before/after (scratchpad JSON diff — the
   pattern used for the annotations branch): new surface counts
   verified, legacy module verified absent by default.
3. Bookkeeper round on the new names — he is ONE persona
   (books + testing + specs); test plan into specs/v1.5/testing/.
4. Cross-model battery COLD against consolidated names (reuse
   testing/CROSS_MODEL_BATTERY_2026_08_05.md structure; foreign
   models must select correct tools from names + docstrings with
   no legacy exposure). This gate is the audit's own step 9 and
   non-negotiable: the consolidation exists FOR foreign callers.
5. PR only after all four; Stephen's word before it opens.

## Loose ends folded in or explicitly out

- IN: audit doc-fixes #4 (list_invoices → list_documents naming
  resolves it; get Stephen's ruling on whether vouchers/credit
  notes are filterable there), #5 (Args blocks for
  update_transactions/create_prices), #7 (typed tax entries).
- Possibly already done on fix/business-doc-lifecycle — VERIFY
  before redoing: doc-fixes #1-#3 (owner_type Args lines naming
  employee; post_invoice lead sentence), #6 (create_prices fixed
  column order in first paragraph). Grep, don't trust.
- OUT (separate concerns): Codex-presentation instruction
  duplication (client adapter bug, not server); MCPB packaging;
  enter_statement; the `cur` column / two-tools question — the
  audit's consolidation makes that conversation easier but is not
  it.
- Docs: README "Choosing a module set" counts and the Glama env
  schema description mention module tool counts — update both.
  CHANGELOG entry should lead with WHY (three independent
  reviewers, one verdict) per house style.

## Known traps for this specific work

- Per-type ID collision is the house bug class (customer 000005
  vs vendor 000005): every new tool that takes an ID must require
  or unambiguously infer its type, and every error message must
  name the type. Test the collision explicitly.
- piecash slot-sweep landmine: deleting a transaction that
  carries a GUID-valued slot (gncInvoice) cascades away the
  referenced invoice's own slots. unpost_invoice restores the
  credit-note flag by raw SQL — preserve that behavior through
  any refactor, and consider the same hazard for any new delete
  path near posted documents.
- `_gate_owner_type` gates Freelancer/Business module splits on
  shared tools — the new document tools need the same gating so
  --modules=freelancer still can't touch vendor documents.
- Version numbering is Stephen's; GitHub must never mint v1.4.3
  (Glama owns that number). This work is v1.5.0 by every plan.

tekeli-li. The letters have the rest.


## Amendments (2026-08-29, maintainer rulings from the review week)

1. **No deprecation ceremony; no `business_legacy` module.** The
   maintainer's ruling: deprecation cycles are human-API practice;
   this surface's consumers are stateless readers who re-negotiate
   the contract every session. v1.5.0 ships the consolidated
   surface with the old names GONE. Supersedes the body's step 4
   and Codex's one-compatibility-cycle step 5. The removal rides a
   MINOR version (the human-facing semver signal), and the v1.4.4
   CHANGELOG carries an announcement ("the business surface
   consolidates in 1.5 — 48 tools become 25") — an announcement,
   not a deprecation: no docstring banners on tools whose
   replacements don't exist yet (a notice pointing at a
   nonexistent replacement is the docstring lying).
2. **Removal-time doc sweep replaces the grace period.** At
   implementation: grep and update everything persistent that
   names old tools — README + per-client install guides, Glama
   config/env descriptions and module tool counts, bookkeeper
   test plans under specs/*/testing/, memory entries, and
   CODEX_TOOL_GUIDE.md gets a header note that it describes the
   pre-consolidation surface. Saved AI workflows self-heal on
   next tool-list read; the docs don't.
3. **Species-first docstring rule.** Every consolidated tool's
   FIRST SENTENCE enumerates its species, never just the genus:
   "Post a customer invoice, vendor bill, employee voucher, or
   credit note…" — models select on descriptions, and substring
   consumers grepping "invoice" must still find every relevant
   tool. The genus name (document/party) appears only where the
   species list has already landed.
4. **Sub-rulings (defaults adopted 2026-08-29):**
   - `list_documents` filters across all four document types
     (vouchers and credit notes included) via `document_type`,
     omit for all.
   - Jobs AND taxtables both adopt the merged read pattern:
     `get_*` folds into `list_*(id=...)` — one pattern, both
     families.
   - Release assignment: v1.5.0, branched after the v1.4.4 tag.
5. **`pay_document` carries the full `pay_invoice` parameter
   set** including `dry_run` (the v1.4.4 rehearsal — validated
   under the old name, inherited by the new; the legacy-parity
   test must cover parameter-set equality, not just
   reachability).
6. **Validation gates unchanged** (suite, wire-surface capture,
   bookkeeper round, cold cross-model battery) — the battery's
   no-legacy-exposure premise is now trivially true, since there
   is no legacy exposure.
7. **Audit-identity mechanism (implementation finding,
   2026-08-29):** the decorator ALREADY resolves polymorphic
   entity types from the response's `type` field (the lifecycle
   tools register as "invoice" and swap to bill/voucher/
   credit_note). New document tools register entity_type
   "invoice" and inherit the swap; party tools register
   "customer", their wrappers add `type` to mutation responses,
   and the swap set extends to vendor/employee. Every existing
   (entity_type, operation) dispatch pair keeps rendering
   identically — no dispatch-table changes.
