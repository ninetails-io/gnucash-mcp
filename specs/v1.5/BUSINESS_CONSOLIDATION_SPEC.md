# Business-surface consolidation — v1.5 implementation spec

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
3. Bookkeeper (Abe) round on the new names — he is ONE persona
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
