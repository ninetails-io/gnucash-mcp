# Refactoring / DRY review — v1.3 pre-release

Captured from deep-read agent run on 2026-06-02.

## Summary

Codebase exhibits healthy separation across 9 mixins + base. Two
giants (`business.py` at 7,816 lines, `core.py` at 4,266) have natural
seams but agent recommends conservative action: extract sub-renderers
from `get_book_summary`, consolidate cross-currency conversion in
invoice posting/payment, move `_normalize_account_refs_for_audit` to
the book layer, and extract a date-parsing helper. Don't split
`business.py` — the invoice lifecycle is too interdependent.

## High-value splits

### 1. `get_book_summary` rendering sub-helpers
`book/core.py:1749-2228` (480 lines). Five `_render_*` methods already
exist; the closing 100+ lines still hand-assemble assets/liabilities/
receivables/payables, business entities, commodities, scheduled. Extract:
- `_render_assets_and_liabilities(...)` → list[str]
- `_render_business_and_commodities(...)` → list[str]
- `_render_data_range_and_warnings(...)` → list[str]

Brings the orchestrator down to ~280 lines.

### 2. Cross-currency conversion in `post_invoice` / `pay_invoice`
`book/business.py:5298` (post_invoice's inline `_qty_for_split`) and
`book/business.py:5804` (pay_invoice's inline `_convert`) reimplement
the same rate-lookup + quantize pattern. `_compute_fx_gain_loss`
re-queries the rate a third time.

Add `_resolve_cross_currency_split(book, from_commodity, to_commodity,
amount, as_of_date) -> (Decimal, Decimal | None)` near line 741. Use
in both post/pay; consume the returned rate from
`_compute_fx_gain_loss` to avoid the duplicate query.

### 3. Move `_normalize_account_refs_for_audit` to `book/_base.py`
`logging_config.py:1748-1823`. Function needs a book session to call
`_find_account` but lives in the logging layer. Cross-layer coupling
is backward: book methods shouldn't know about audit staging.

Move to BaseGnuCashBook as a method; logging decorator calls it with
the open book session (already available via `_get_book_func()`).

## High-value extractions

### 4. Date parsing — 15+ duplicated sites
`date.fromisoformat(param) if param else date.today()` appears across
business.py, scheduling.py, investments.py. Add
`BaseGnuCashBook._parse_iso_date(iso_str)` and route call sites
through it.

## Joins / over-extractions

### 5. Inline trivial single-call renderers
- `book/core.py:1527` `_format_monthly_net` (10 lines, 1 call site) — inline.
- `book/core.py:1577` `_render_warnings` (13 lines, 1 call site) — inline.
- Keep `_format_reconciliation_lag` (line 1538, 2 call sites, 38 lines of date logic).

## Architectural observations

### Mixin composition — healthy
`book/__init__.py:88-99` MRO collision guard catches duplicates at
build time. Has never fired in tests. Architecture scales.

### `business.py` size is defensible
Breakdown:
- Entity CRUD (customer/vendor/employee): ~600 LOC
- Job CRUD: ~300
- Billterm CRUD: ~150
- Taxtable CRUD: ~400
- Invoice/bill/voucher/credit-note lifecycle: ~3,500
- Entry management: ~500
- Reports (outstanding/jobs/vendor): ~500
- Helpers + validators: ~1,500

Considered split shapes:
- A) `business_entities.py` (~1,200 LOC) leaving ~6,600 — leaves the
  document lifecycle where the value is.
- B) `business_documents.py` (~3,500 LOC) leaving ~4,300 — fractures
  the invoice lifecycle across files; ~15 cross-file calls.

**Recommend: keep monolithic for v1.3.0.** The invoice lifecycle
(post, pay, unpost, apply_credit_note, _add_entry,
_compute_fx_gain_loss) is highly interdependent. Revisit only if
maintenance pain shows up.

### Audit-log dispatcher
68 (entity_type, operation) pairs at `logging_config.py:1643-1708`.
Flat table, one line per entry. Not unmaintainable. Keep flat.

### Tool-layer boilerplate
Minimal duplication beyond what's already in `tools/_helpers.py`. No
new extractions warranted.

## Defer / not worth doing

- **Mixin-size balancing** — each is justified by domain.
- **Merge `_format.py` + `_currency.py`** — distinct concerns; merging would create weak-cohesion 540-LOC file.
- **Audit-staging class** — current threading-local works; risk of subtle concurrency bugs.
- **Extract `_CreateSignals` duplicate detection to its own module** — well-tested, well-designed; 5+ cross-file imports would harm clarity.

## Summary table

| Finding | Effort | Priority |
|---|---|---|
| Extract `get_book_summary` sub-renderers | Medium | Medium |
| Consolidate cross-currency conversion in post/pay | Low | Medium |
| Move `_normalize_account_refs_for_audit` to book/_base | Low | Low |
| `_parse_iso_date` helper | Low | Low |
| Inline trivial renderers (_format_monthly_net, _render_warnings) | Trivial | Low |
| Do NOT split business.py | — | — |
| Do NOT merge _format.py + _currency.py | — | — |
