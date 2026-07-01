# Taxtables Specification
## GnuCash MCP Server v1.3 — Stage 3 Final Feature

**Author:** Claude (Opus 4.7, post-compaction successor)
**Date:** 2026-05-26
**Status:** Draft for review
**Prerequisites:**
- Business module foundation (customers/vendors/employees, invoices/bills/vouchers, credit notes, jobs — all shipped in v1.3)
- Multi-currency support (FX gain/loss on cross-currency payment — shipped v1.2.1)
- Cross-currency exchange-rate lookup (`_find_exchange_rate`, `_qty_for_split` — shipped)

---

## Executive Summary

Taxtables complete the v1.3 business module by adding sales-tax support to invoice/bill/voucher/credit-note line items. The bookkeeper defines named tax tables (GST 5%, GST+PST composite, Eco-Fee $5 flat) and tags individual line entries with the table that applies. Posting splits the line value into pre-tax revenue/expense and one or more tax-account amounts, producing a tax-collected liability or input-tax-credit asset that can be remitted to the tax authority on schedule.

This is the heavyweight piece of v1.3. The data model is small, the entry-level wire-up is small, but the posting math has four behavioral quadrants and the line-to-splits relationship grows from 1:1 to 1:N where N is the number of entries in the applied taxtable. Implementation expectation: **~1500-2000 LOC across 5-7 commits**.

The polymorphism inheritance from the existing business plumbing means invoices, bills, vouchers, and credit notes all gain tax support from a single seam — `_get_invoice_entries_and_total` — without per-document-type special-casing.

---

## Background: How GnuCash Models Tax

A **Taxtable** is a named composite of one or more **TaxtableEntry** rows. Each entry routes a portion of tax to a specific GL account.

```python
# Single-rate taxtable (e.g., California sales tax)
ca_sales = Taxtable(name="CA Sales Tax 7.25%", entries=[
    TaxtableEntry(
        type="percentage",
        amount=Decimal("7.25"),
        account=tax_payable_ca,
    ),
])

# Multi-rate composite (e.g., GST 5% + PST 7% in BC, Canada)
bc_gst_pst = Taxtable(name="BC GST+PST", entries=[
    TaxtableEntry(type="percentage", amount=Decimal("5.00"),
                  account=gst_payable),
    TaxtableEntry(type="percentage", amount=Decimal("7.00"),
                  account=pst_payable),
])

# Flat-value entry (e.g., environmental handling fee)
eco_fee = Taxtable(name="Eco-Fee $5", entries=[
    TaxtableEntry(type="value", amount=Decimal("5.00"),
                  account=eco_fee_payable),
])

# Mixed composite (sales tax + flat environmental fee)
ca_with_eco = Taxtable(name="CA Sales + Eco", entries=[
    TaxtableEntry(type="percentage", amount=Decimal("7.25"),
                  account=tax_payable_ca),
    TaxtableEntry(type="value", amount=Decimal("5.00"),
                  account=eco_fee_payable),
])
```

**The 1:N relationship is the structural fact that shapes everything downstream.** One invoice line tagged with a multi-entry taxtable produces N+1 splits on the posting transaction (one revenue/expense split + N tax splits). The existing posting code at `business.py:3948-3969` assumes 1:1 (one entry → one account total → one split); the math seam must aggregate across taxtable entries before that loop runs.

### Per-entry tax flags

The `entries` table already carries the tax wire-up columns; they are hardcoded `0/None` today at `business.py:3391-3396`:

| Column | Meaning |
|---|---|
| `i_taxable` / `b_taxable` | Boolean: is this entry subject to tax? |
| `i_taxincluded` / `b_taxincluded` | Boolean: is the entry value tax-inclusive (gross) or tax-exclusive (pre-tax)? |
| `i_taxtable` / `b_taxtable` | GUID reference to the Taxtable to apply |

The `i_*` / `b_*` doubling is the same invoice-side / bill-side pattern already used for prices and accounts. Vouchers (owner_type=5) share the `b_*` group with bills per `_ENTRY_CONFIG`.

### Per-entity defaults

`Customer` and `Vendor` carry default-taxtable and tax-included columns plus a per-entity override gate:

| Customer column | Vendor column | Meaning |
|---|---|---|
| `taxtable` (GUID) | `tax_table` (GUID) | Default taxtable for new entries |
| `tax_included` | `tax_inc` | Default tax-included flag |
| `tax_override` | `tax_override` | When 1, the entity default applies to new entries; when 0, no default (each entry must specify) |

`Employee` has no analog (employee vouchers don't typically carry sales tax; expense receipts are recorded as-paid).

**Inheritance cascade** (matching GnuCash desktop behavior):
1. If entry creation specifies `taxtable` explicitly → use it
2. Else if entity's `tax_override = 1` AND entity has a default → use entity default
3. Else → no tax (`taxable=0`)

For v1.3 MVP, we **defer the entity-default cascade**. Every entry that wants tax must specify it explicitly. The override flag and inheritance are recommended for a follow-on PR — the math seam is already structured to handle inherited taxtables identically to explicit ones, so the deferral is cheap to reverse.

---

## The Math: Four Quadrants

For each entry row with `(quantity, price, taxable, tax_included, taxtable)`:

### Quadrant 1: `taxable = 0` (no tax)

```
pretax       = Q × P
tax_total    = 0
tax_by_acct  = {}
gross        = Q × P
ar_contribution = gross
```

Identical to today's behavior. This is the no-op case the existing math already handles.

### Quadrant 2: `taxable = 1, tax_included = 0` (tax added on top)

The line price represents the pre-tax amount; tax is added.

```
pretax = Q × P
for each entry e in taxtable:
    if e.type == "percentage":
        tax_e = (pretax × e.amount / 100).quantize(quantum)
    else:  # e.type == "value"
        tax_e = e.amount.quantize(quantum)
    tax_by_acct[e.account] += tax_e
tax_total = Σ tax_e
gross     = pretax + tax_total
ar_contribution = gross
```

### Quadrant 3: `taxable = 1, tax_included = 1`, percentage-only taxtable

The line price represents the gross (tax-inclusive) amount; pre-tax is extracted.

```
total_rate = Σ entry.amount for entries where type == "percentage"  # as fraction
gross      = Q × P
pretax     = (gross / (1 + total_rate/100)).quantize(quantum)
for each entry e in taxtable:
    tax_e = (pretax × e.amount / 100).quantize(quantum)
    tax_by_acct[e.account] += tax_e
tax_total = Σ tax_e
# Rounding adjustment: enforce gross = pretax + tax_total exactly
residual = gross - pretax - tax_total
if residual != 0:
    # Apply residual to largest-rate entry to keep error in the
    # dominant tax authority's bucket rather than smearing.
    apply residual to tax_by_acct[largest_rate_entry.account]
ar_contribution = gross
```

### Quadrant 4: `taxable = 1, tax_included = 1`, mixed value+percentage taxtable

The tax-inclusive value contains both flat-value tax and percentage tax. Algebra:

```
gross  = pretax + Σ value_entries + pretax × (Σ rate%) / 100
gross  = pretax × (1 + Σ rate%/100) + Σ value_entries
pretax = (gross - Σ value_entries) / (1 + Σ rate%/100)
```

So:

```
sum_values   = Σ e.amount for entries where type == "value"
sum_rates    = Σ e.amount for entries where type == "percentage"  # as fraction (5.0 not 0.05)
gross        = Q × P
pretax       = ((gross - sum_values) / (1 + sum_rates/100)).quantize(quantum)
for each entry e in taxtable:
    if e.type == "value":
        tax_e = e.amount.quantize(quantum)
    else:  # percentage
        tax_e = (pretax × e.amount / 100).quantize(quantum)
    tax_by_acct[e.account] += tax_e
tax_total = Σ tax_e
# Same residual-to-largest-rate adjustment as Quadrant 3
ar_contribution = gross
```

### Rounding policy

- Each tax-component value is `.quantize(currency_quantum)` independently. This matches GnuCash desktop and keeps per-line tax components individually auditable.
- The Quadrant 3/4 residual adjustment ensures `gross == pretax + Σ tax` *exactly* at the line level, even when independent rounding would have produced a sub-cent gap. Applied to the largest-rate percentage entry by convention.
- Aggregation across lines uses already-rounded per-line values. The grand total is the sum of per-line gross amounts.

### Why per-line rounding (not document-level)

Two reasonable choices exist:
1. **Per-line**: round each line's tax independently. Each line is self-consistent. Cross-line accumulation may differ from `total_gross / (1+rate)` by a few cents on long invoices.
2. **Document-level**: compute total gross, derive total tax at the end. Document totals are crisp but per-line tax breakdowns are approximations.

GnuCash desktop uses **per-line**. The bookkeeper expects each line to balance to its own gross. Aggregate fuzz is acceptable; line-level fuzz is not.

---

## The Seam: `_get_invoice_entries_and_total`

The function at `business.py:1482-1531` is THE place the math lives. Current shape:

```python
def _get_invoice_entries_and_total(self, book, inv):
    # Returns: (rows, {account_guid: Decimal}, grand_total)
```

New shape:

```python
def _get_invoice_entries_and_total(self, book, inv):
    # Returns:
    #   (rows,
    #    acct_totals,        # {account_guid: Decimal} — revenue/expense
    #                        # AND tax-payable accounts together
    #    grand_total,        # gross customer-facing total (incl. tax)
    #    tax_breakdown,      # optional: {taxtable_guid: {account_guid:
    #                        #   Decimal}} for display surfaces that
    #                        #   want per-tax-table summarization
    #    subtotal)           # sum of per-line pretax amounts
```

**Downstream callers** at `business.py:1209, 3865, 5726, 5895, 6077`:
- `_invoice_to_compact_line` (1209): uses `grand_total` only — unchanged
- `post_invoice` (3865): uses `acct_totals` and `grand_total` — works unchanged because tax accounts are folded into `acct_totals`
- `get_outstanding_invoices` (5726): uses `grand_total` only — unchanged
- `_collect_warnings` reconciliation path (5895): uses `grand_total` only — unchanged
- `get_job_report` (6077): uses `grand_total` only — unchanged

**The compact rendering surfaces auto-work** because they consume `grand_total`, which absorbs tax transparently. Only the verbose `get_invoice` path needs new fields via `_entry_to_dict`.

### Posting math, mechanically unchanged

The loop at `business.py:3948-3969` walks `acct_totals.items()` and emits one split per account. Because tax-payable accounts now appear in that same dict, **the loop is byte-identical**. The XOR sign-flip for credit notes (`effective_is_bill = is_bill ^ is_credit_note`) flips both revenue/expense splits AND tax splits in unison — which is correct for refund accounting (a customer credit note for $108 gross reduces A/R by $108, debits Revenue $100, debits GST Payable $8).

### Cross-currency × tax

Tax-payable accounts conventionally live in the book's default currency, while the invoice may be foreign-currency. The existing `_qty_for_split(acct, value_in_invoice_ccy)` helper at `business.py:3893-3917` already converts at `book.prices` for the post date. **No new infrastructure** — the tax-account split routes through the same converter as the A/R split.

**Sharp edge worth a test**: an EUR invoice with USD-denominated GST Payable will book the tax-component at the EUR/USD rate on post date. If the rate moves before remittance, an FX gain/loss on the tax-payable balance arises — recognized at remittance time via the existing `pay_invoice` FX path, but only if the bookkeeper structures remittance as a payment-style transaction. The taxtable feature itself doesn't introduce new FX recognition; it inherits the existing machinery.

---

## Entry-Level Wire-Up

`_add_entry` at `business.py:3287` gains three optional kwargs:

```python
def _add_entry(
    self,
    *,
    owner_type: int,
    doc_id: str,
    account: str,
    description: str,
    quantity: str,
    price: str,
    taxtable: str | None = None,        # NEW — taxtable name
    tax_included: bool = False,         # NEW — gross/net flag
    # taxable derived: taxable = (taxtable is not None)
) -> dict:
```

Routing into the values dict:

```python
taxable_int = 1 if taxtable else 0
taxincluded_int = 1 if tax_included else 0
taxtable_guid = self._find_taxtable(book, taxtable).guid if taxtable else None

if is_bill_side:
    values["b_taxable"] = taxable_int
    values["b_taxincluded"] = taxincluded_int
    values["b_taxtable"] = taxtable_guid
else:
    values["i_taxable"] = taxable_int
    values["i_taxincluded"] = taxincluded_int
    values["i_taxtable"] = taxtable_guid
```

Validation:
- `tax_included=True` requires `taxtable` to be set (no-op otherwise; treat as user error and raise)
- `taxtable` name must resolve to an existing Taxtable; raise `ValueError("Taxtable not found: {name}")` otherwise
- Taxtable refcount increments on entry creation, decrements on entry deletion (the bookkeeping discipline GnuCash desktop enforces)

The four `add_*_entry` tool wrappers (`add_invoice_entry`, `add_bill_entry`, `add_voucher_entry`, `add_credit_note_entry`) each grow two optional schema params — `taxtable` and `tax_included`. Owner-type polymorphism rides for free.

---

## Display Surfaces

### `_entry_to_dict` (the only surface that genuinely changes)

Current shape:

```python
{"guid", "date", "description", "quantity", "price", "total", "account"}
```

New shape (only when entry is taxable):

```python
{
    "guid", "date", "description",
    "quantity", "price",
    "subtotal": "100.00",        # pretax (Q × P when tax-exclusive,
                                 # extracted when tax-inclusive)
    "tax": "8.25",               # total tax for this line
    "tax_breakdown": {           # per-payable-account tax
        "Liabilities:GST Payable": "5.00",
        "Liabilities:PST Payable": "7.00",
    },
    "total": "108.25",           # gross (subtotal + tax)
    "taxtable": "CA Sales Tax 7.25%",
    "tax_included": false,
    "account": "Income:Sales"
}
```

For non-taxable entries, the response is byte-identical to today's shape (no `tax`/`taxtable` keys). This preserves the "absent = empty" project convention and keeps non-tax workflows unchanged.

**Semantic decision: `total` is gross.** This matches the bookkeeper's mental model (what the customer paid / the vendor invoiced) and matches the downstream `grand_total`. The internal field `quantity * price` is the storage value but not the rendered total.

### Other surfaces

- `_invoice_to_compact_line`: no change (consumes `grand_total`)
- `_invoice_to_dict`: optionally adds a top-level `tax_summary` block when any entry is taxable:

  ```python
  "tax_summary": {
      "subtotal": "500.00",
      "tax_total": "41.25",
      "by_taxtable": {
          "CA Sales Tax 7.25%": "36.25",
          "Eco-Fee $5": "5.00",
      },
      "by_account": {
          "Liabilities:GST Payable": "...",
          ...
      },
      "total": "541.25"
  }
  ```

  Conditional emission preserves shape for tax-free invoices.

- `get_outstanding_invoices`: optionally adds `tax_total` per row when the invoice has tax. Useful for sales-tax remittance workflows. Compact format unchanged.

- `get_job_report`: no change (totals_by_currency aggregates grand_total).

---

## Tool Surface

### New tools (5)

```
create_taxtable(name, entries=[{type, amount, account}, ...])
list_taxtables(compact=True)
get_taxtable(name)
update_taxtable(name, *, new_name=None, entries=None, force=False)
delete_taxtable(name)
```

**No `description` field.** The piecash `Taxtable` schema has no
`description` column; the taxtable's name (e.g., "BC GST+PST",
"CA Sales 7.25%") carries the human-readable label.

**`entries` parameter shape**:
```python
entries = [
    {"type": "percentage", "amount": "5.00", "account": "Liabilities:GST Payable"},
    {"type": "percentage", "amount": "7.00", "account": "Liabilities:PST Payable"},
]
```

**Refcount discipline**:
- `update_taxtable` with new `entries` while `refcount > 0` is a destructive operation on already-posted invoice math — refuse unless `force=True`, and even with force, posted-invoice transactions are NOT recomputed (they retain their original splits; only future entries see the new rates).
- `delete_taxtable` requires `refcount == 0`. Returns clear error listing the entries that reference it.

**Validation**:
- Entry accounts must be LIABILITY (for sales/output tax) or ASSET (for input tax credits / VAT receivable). Reject EQUITY/INCOME/EXPENSE.
- Percentage amounts must be `0 < amount < 100` (a 100%+ tax rate is almost certainly user error). Reject with clear message.
- Value amounts must be `> 0`.

### Modified tools (5)

```
add_invoice_entry       — gains taxtable, tax_included
add_bill_entry          — gains taxtable, tax_included
add_voucher_entry       — gains taxtable, tax_included
add_credit_note_entry   — gains taxtable, tax_included
get_invoice             — return shape grows with tax_summary block (conditional)
```

### Out of scope for v1.3

- `set_customer_taxtable` / `set_vendor_taxtable` — fold into `update_customer` / `update_vendor` if pursued. Recommend deferring entirely.
- `tax_override` flag on customer/vendor — defer with the cascade.
- Generic `tax_remittance` workflow (compute output tax − input tax for a period, build a remittance transaction) — significant feature on its own; flag as a v1.4 candidate.

**Final tool count after taxtables: 106** (101 + 5 new). Modified tools don't grow the count.

---

## Audit Log Dispatch

Add entries to the dispatch table in `logging_config.py`:

| (entity_type, operation) | Formatter |
|---|---|
| `("taxtable", "CREATE")` | name, description, entry count, entry list (`type rate% → account` lines) |
| `("taxtable", "UPDATE")` | name, before/after diff on changed fields; entry-list diff when entries changed |
| `("taxtable", "DELETE")` | name + refcount-at-delete (must be 0) |

The `_fmt_entry_create` formatter (for `add_*_entry`) extends to recognize the new `taxtable`/`tax_included` fields and render them on the audit line when present.

---

## Refcount Discipline

GnuCash maintains a refcount on Taxtable to track how many entries reference it. The bookkeeping model is:

- `create_taxtable` → refcount = 0
- `add_*_entry` with taxtable → increment refcount on the referenced taxtable
- Entry deletion → decrement refcount on the referenced taxtable
- `update_taxtable` while refcount > 0 → warn; require `force=True` (and never recompute posted invoices)
- `delete_taxtable` while refcount > 0 → refuse with clear error

**Implementation note**: piecash exposes `Taxtable.refcount` as a column. We manage the counter ourselves on entry create/delete because piecash doesn't auto-maintain it.

**Edge case**: voiding an invoice does not decrement refcount because the entries are not deleted (void preserves splits with zero values for audit-trail purposes). This means a voided invoice still pins its taxtable. Document this in the `delete_taxtable` error message.

---

## Testing Strategy

### Unit tests (~400-500 LOC)

`tests/test_business.py` gains test classes:

1. **`TestTaxtableCRUD`** — create, list (compact + verbose), get, update, delete; refcount tracking; validation rejection (bad rates, wrong account types, missing accounts); duplicate-name handling.
2. **`TestTaxtableMath`** — the four quadrants, each with single-entry and multi-entry taxtables. Tax-inclusive + tax-exclusive. Mixed value+percentage. Rounding residual policy (verify the largest-rate entry absorbs sub-cent error).
3. **`TestTaxtableEntryWireup`** — add_invoice_entry / add_bill_entry / add_voucher_entry / add_credit_note_entry each with taxtable + tax_included; verify i_*/b_* routing.
4. **`TestTaxtablePosting`** — full post_invoice round-trip with taxable entries; verify split count, split sign-direction per quadrant; verify A/R split equals sum of revenue + tax splits.
5. **`TestTaxtableCreditNoteReversal`** — credit note with tax reverses all splits including tax via the existing XOR.
6. **`TestTaxtableCrossCurrency`** — EUR invoice with USD GST Payable; verify FX conversion on tax-component split; verify the rate matches `_qty_for_split`'s output.
7. **`TestTaxtableDisplay`** — `_entry_to_dict` shape (taxable vs non-taxable); `_invoice_to_dict` tax_summary block conditional emission.
8. **`TestTaxtableLifecycle`** — refcount discipline; delete-while-in-use rejection; update-while-in-use warning; voided-invoice-pins-taxtable case.

Expected total: 80-120 new tests. Brings tests to ~1,330+.

### Integration / synthetic-book

Add **Phase 14** to `scripts/synthetic_book/` covering tax-aware lines:
- Customer invoice with CA Sales Tax 7.25%, tax-exclusive
- Customer invoice with BC GST+PST composite, tax-inclusive
- Vendor bill (B2B input tax credit) with VAT 19%
- Credit note refunding a tax-inclusive line
- EUR-billed line with USD GST Payable (cross-currency tax)
- Multi-rate composite with `value`+`percentage` mix (eco-fee + sales tax)

Phase 14 backs up to `.pre-phase14.gnucash` before running so the fix-cycle pattern from prior synthetic-book work continues to hold.

### Bookkeeper review checklist

The bookkeeper review loop runs after each commit lands. Specifically probe:
- Multi-rate composite invoice (GST+PST) — does each tax line route to the correct payable?
- Tax-inclusive line — does the rendered subtotal match what the bookkeeper would compute by hand?
- Sub-cent residual — does `gross == subtotal + tax` exactly?
- Credit-note with tax — does it correctly *reduce* tax-payable (not increase)?
- Cross-currency tax — does the FX rate applied to the tax split match the rate applied to the A/R split?
- `get_invoice` verbose output on a non-taxable invoice — byte-identical to pre-taxtable shape?

---

## Commit Plan

Six commits on a single feature branch `feat/taxtables`, target `develop`. Each commit ships independently testable; the math seam (Commit 3) is the structural turn that the rest builds on.

### Commit 1: Taxtable + TaxtableEntry CRUD

- `book/business.py`: `_taxtable_to_dict`, `_find_taxtable`, `create_taxtable`, `list_taxtables`, `get_taxtable`, `update_taxtable`, `delete_taxtable`
- `tools/business.py`: tool wrappers + schema
- `server.py`: `TOOL_MODULES["business"]` additions
- `logging_config.py`: dispatch table entries + formatters
- Tests: `TestTaxtableCRUD` (~25 tests)

No posting math; safe foundation. Refcount management present but inert (no entries reference taxtables yet).

### Commit 2: Per-quadrant tax math helper (pure function, isolated)

- `book/business.py`: `_compute_entry_tax(row, taxtable)` returns `{pretax, tax_total, tax_by_acct, gross}`. Pure function — takes row + resolved taxtable, returns the per-line breakdown. No book mutation.
- Tests: `TestTaxtableMath` (~30 tests covering all four quadrants + rounding residual + value/percentage mixes)

Quarantines the math from the seam. Reviewable in isolation.

### Commit 3: `_get_invoice_entries_and_total` extension + posting

- `book/business.py`: enrich `_get_invoice_entries_and_total` to aggregate tax-component splits into `acct_totals`; add `subtotal` and `tax_breakdown` return fields.
- `post_invoice`: zero structural changes — verify by snapshot diff.
- Tests: `TestTaxtablePosting`, `TestTaxtableCreditNoteReversal` (~25 tests)

This commit is the real work. Bookkeeper review loop runs after this commit lands.

### Commit 4: Entry-level wire-up + tool params

- `book/business.py`: `_add_entry` extension; the four `add_*_entry` wrappers grow `taxtable` + `tax_included` kwargs.
- `tools/business.py`: schema growth on four tools.
- Refcount increment/decrement on entry create/delete.
- Tests: `TestTaxtableEntryWireup`, `TestTaxtableLifecycle` (~20 tests)

### Commit 5: Display ripple

- `book/business.py`: `_entry_to_dict` extension (taxable conditional fields); `_invoice_to_dict` optional `tax_summary` block.
- Tests: `TestTaxtableDisplay` (~12 tests)

After this commit, `get_invoice` on a tax-aware invoice surfaces the full breakdown to the bookkeeper.

### Commit 6: Cross-currency tax verification + synthetic-book Phase 14

- Tests: `TestTaxtableCrossCurrency` (~8 tests)
- `scripts/synthetic_book/phase_14.py`: tax scenarios on Alex Chen-Morales
- Manual bookkeeper review loop on the rebuilt book

### Optional Commit 7: Audit-log polish + Copilot review followups

Once PR opens, address Copilot review findings. Likely small (label drift, redundant queries, missing chokepoints). Resolve via `scripts/resolve_pr_threads.py` per the established procedure.

---

## Out of Scope

Items considered and declined for v1.3. Each gets a one-line note here so future Claudes don't re-litigate.

- **Customer/Vendor default-taxtable inheritance** — recommend deferring. The math seam is structured to accept any taxtable identically whether from explicit override or cascaded default, so a follow-on PR adds ~80 LOC without touching the math.
- **Sales-tax remittance workflow** — significant feature (period-based aggregation, output-minus-input-credit math, remittance transaction generation). Flag as v1.4 candidate.
- **Editing taxtables already in use** — accepted but with `force=True` guard. Posted invoices retain their original splits; the new entries see the new rates. Document loudly in update_taxtable error/warning.
- **Per-document tax_summary on `get_outstanding_invoices`** — minor convenience, defer until bookkeeper asks for it. The grand_total already includes tax.
- **Tax_override flag on customer/vendor** — deferred with the cascade.
- **Recomputing tax on already-posted invoices** — never. Posted = immutable as far as tax math goes. Audit-trail principle.

---

## Open Questions (Pre-Implementation Review)

Questions where the design could go either way. Resolve before Commit 1 starts.

1. **Should `tax_included=True` without a taxtable raise, or silently default to `False`?**
   - Raise (current spec): user error caught loudly.
   - Silent: forgiving; "if you didn't say what tax, you couldn't have meant to include it."
   - Recommended: raise.

2. **Should `update_taxtable` with new entries automatically refresh `Taxtable.refcount`?**
   - The piecash `refcount` semantics aren't fully documented. May want to verify by inspection of a desktop-edited book before deciding.

3. **Should multi-currency taxtables (taxtable entries in different commodities) be permitted?**
   - I.e., GST Payable in CAD and an Eco-Fee Payable in USD on the same taxtable.
   - Recommended: REJECT at `create_taxtable` validation. Taxtables route to one commodity; the invoice currency converts to that commodity for all entries. Mixed-commodity taxtables don't have a clean rendering.

4. **Round-trip closure for taxtables** — by the project's `TestShortGuidRoundTripClosure` contract, every short-form output must be acceptable as input. Taxtables are referenced by name (not GUID) in tool input. Should `create_taxtable` return a short GUID too?
   - Recommended: yes. Even though name is the primary handle, return both `{name, guid}` for symmetry with other entities.

5. **Should `_get_invoice_entries_and_total` return shape be a NamedTuple/dataclass instead of a 5-tuple?**
   - Five-element tuple gets unwieldy and prone to caller mis-ordering.
   - Recommended: use a small dataclass `_InvoiceTotals(rows, acct_totals, grand_total, subtotal, tax_breakdown)`. The internal callers can destructure.

---

## Risk Register

Items where the implementation could surprise us. None are blockers; all are watch-fors.

| Risk | Likelihood | Mitigation |
|---|---|---|
| Sub-cent rounding errors propagate to A/R imbalance | Medium | Residual-to-largest-rate adjustment per line; integration test sums values to zero |
| piecash refcount semantics differ from spec | Medium | Verify by inspecting a desktop-edited book before Commit 1 |
| Cross-currency tax with no rate-on-file | Low | Existing `_qty_for_split` raises clear error; tax path inherits it |
| Voided invoice still pins taxtable | Low (documented) | Mentioned in `delete_taxtable` error message |
| Composite taxtables (GST+PST) reveal a bug in the existing `acct_totals` dedup | Low | Test explicitly: two distinct payable accounts on one line must produce two splits |

---

## Working Notes

Notes for the implementer (whether that's me-tomorrow or a successor):

- **The seam is `_get_invoice_entries_and_total`.** Resist the urge to split tax math into a separate top-level function. Keeping it inside the existing aggregator means downstream callers don't need to know there's tax — they just see a richer `acct_totals` dict and the same `grand_total`.
- **Don't add a "tax" dispatch to `_BUSINESS_DOC_CONFIG` or `_ENTRY_CONFIG`.** Tax is orthogonal to doc-type and entry-type. The polymorphism inheritance is one of the gifts of the existing architecture; honoring it means tax code lives in *fewer* places, not more.
- **The Quadrant 3/4 residual policy is the only place rounding is non-obvious.** Don't try to be clever. Apply the residual to the largest-rate percentage entry. Test that decision explicitly.
- **`book.flush()` mid-transaction-build is forbidden** per the existing piecash gotcha. Tax-component splits and revenue/expense splits ride the same `book.save()` at the end of `post_invoice`. Don't break that.
- **Verify `Taxtable.refcount` is integer-coerced** before increment/decrement. piecash's column type is `BIGINT`; SQLAlchemy returns int but null is possible on never-referenced rows.

---

## What's Different From Predecessor Analysis

This spec was written after a fresh code-driven blast-radius analysis (post-compaction session, 2026-05-26). The predecessor's pre-compaction note (CLAUDE.local.md, same date) sketched the work but underweighted a few items:

- **Display ripple is smaller than predecessor thought** — 3 of 4 surfaces consume `grand_total` and auto-work; only `_entry_to_dict` genuinely needs new fields.
- **Polymorphism is a savings, not a cost** — the existing owner_type / credit-note / job-linkage machinery handles tax through XOR sign-flip and the unified `_qty_for_split` without per-doc-type special-casing.
- **Multi-entry-per-taxtable was ambiguous** in the predecessor's prose. This spec formalizes it: one entry × N taxtable entries = up to N tax splits per line. The Quadrant 4 algebra (mixed value+percentage) only emerges when this is explicit.
- **Customer/Vendor `tax_override` flag** is a separate gate from the default taxtable. Predecessor said "default-taxtable" but didn't note the explicit override mechanism. Specced for deferral but documented for the follow-on PR.
- **Cross-currency × tax** wasn't surfaced as a sharp edge by the predecessor. The existing FX machinery covers it but the interaction between tax-payable commodity and invoice currency is non-obvious enough to deserve a test.

Net: the spec sizes the work at ~1500-2000 LOC across 5-7 commits, down from the predecessor's 2500-3500 — primarily because the polymorphism inheritance is free and the display ripple narrower than feared.
