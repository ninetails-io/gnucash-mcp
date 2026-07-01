# Briefing: gnucash-mcp Server, Next Spec Round

**To:** Claude Chat (spec author)
**From:** Claude Cowork (field tester)
**Re:** Findings from building the Lin Wei (林微) CNY-default synthetic book; recommended next features and specs to write

This document is not itself a spec. It's a briefing of everything I learned from running ~1,960 real transactions through the gnucash-mcp server during the Lin Wei build, so that you can write specs for the next round of work without missing context. Each major section either describes a finding, identifies a feature gap, or flags a tradeoff that you'll need to design around. Where I think a section should become its own spec, I say so.

---

## 1. Server scope & target audience

The product owner has clarified that this server's job is **personal finance + small business finance**, with the small-business owner as the ideal target. That positioning has consequences for everything below:

- The business module needs to be **complete enough to actually use**, not just demoable. Small businesses won't tolerate "yes you can post invoices but you can't apply tax to them."
- Personal-finance features (Lin Wei's daily life, scheduled transactions, investment lots, mortgage tracking) are already in good shape. The growth area is the business module.
- Workflow tools that matter: invoicing, tax handling, returns/credits, project-based billing (jobs), aging reports.
- Workflow tools that are probably out of scope: payroll integration, employee expense vouchers (only matters for businesses with W-2 employees, which most GnuCash SMB users don't have).

Keep the personal-finance experience clean while building out the business module. The two shouldn't get tangled — a Lin-Wei-shaped personal user shouldn't be shown business primitives they don't need.

---

## 2. Field findings from the Lin Wei build

These aren't bugs exactly — they're observations about how the server behaves at scale that you'll want to keep in mind when designing new tools.

### 2.1 LLM-loop economics

`create_transaction` is fine for interactive use. It is **catastrophically expensive for bulk loads**. ~1,100 transactions via the LLM round-trip would take roughly 3-4 hours of LLM session time and burn the user's usage cap multiple times. The same operation as direct SQL (skipping the LLM and the MCP layer entirely) takes ~3 seconds and uses zero LLM budget.

This isn't a bug, it's a structural reality of LLM-mediated CRUD. But it points at a real missing primitive: **`bulk_create_transactions(transactions=[...])`** — same per-transaction validation, but the LLM only has to be in the loop once per batch rather than once per row. Without it, any user trying to do a one-time migration from another accounting system, or a recurring nightly OFX sync, will hit the same wall I did.

This deserves its own spec. See §6 below.

### 2.2 Read-cache behavior

The MCP server has **no read cache** — external SQL writes are picked up on the very next tool call, no bounce or invalidation needed. This is good. It means SQL surgery and MCP tool calls compose cleanly in the same session. Don't break this property when designing new features; specifically, don't add an in-memory caching layer that invalidates only on certain write paths.

### 2.3 No foreign keys enforced

GnuCash sqlite has FK constraints declared but not enforced (`PRAGMA foreign_keys` is OFF by default). This makes raw SQL deletions tractable but means any new tool that does multi-table writes needs to handle referential integrity manually. Specifically: **lots, slots, and entries can be orphaned** if the parent (transaction, invoice, account) is deleted without sweeping them.

I orphaned 21 slot rows during the cleanup of test invoices — those were tied to deleted lots. The MCP server's existing `delete_transaction` may have similar gaps; worth auditing.

### 2.4 The "delete a posted invoice" hole

This is a real bug. Workflow:

1. `delete_transaction(post_txn_guid)` succeeds — the transaction goes away
2. `delete_invoice(invoice_id)` is then called
3. Server rejects with: `"Cannot delete posted invoice 'XXX'. Void it or issue a credit note instead."`
4. But the post transaction is gone, so the invoice is "posted" in metadata only — it has no balance contribution, no AR split, just a row in `invoices` with `post_txn` pointing at a transaction that doesn't exist.

The bug: **deleting the post transaction should reset the invoice's posted state** (clear `post_txn`, `post_lot`, `post_acc`, `date_posted`). Or: there should be an explicit `unpost_invoice` tool that does it cleanly, and `delete_transaction` on a post-txn should refuse with a "use unpost_invoice first" message.

I had to do SQL surgery to clean up six orphan-posted invoices in our session. A real bookkeeper hitting this can't.

This deserves its own spec. See §5.1 below.

### 2.5 Verification subtlety on Bug A (FX account routing)

The `pay_invoice` tool **does** support `fx_account` (explicit parameter) **and** does substring-match against existing FX-named accounts. Both layers were implemented. I incorrectly reported them as unfixed in an earlier verification round because the Lin Wei book had two matching FX accounts coexisting (`Income:FX Gain Loss` and `Income:Foreign Exchange Gain/Loss`), so the heuristic correctly fell through to the canonical default per documented behavior. Two-account fallthrough is the right design — but it's also the case that misses verification if your test setup has the same naming collision.

**Implication for spec design**: any "auto-detect existing X" heuristic in a new feature needs a documented disambiguation path AND clear test setup that exercises both the single-match and multiple-match cases.

### 2.6 The `data range` auto-extends to leftover prices

The `get_book_summary` "Data range" header reflects the date range across both transactions AND prices. If a user has 2026-dated price records but no 2026 transactions, the range still shows "2025-01-01 to 2026-XX-XX." Mildly confusing. Either the header should restrict to transaction dates only, or `get_book_summary` should call out the source of the range extension.

---

## 3. Open server bugs (in addition to the one above)

### 3.1 No `delete_price` tool

`create_price` exists, `get_prices` / `get_latest_price` exist, but no way to delete a price. You can update by re-creating with the same date+source, but you can't remove a stale or test-injected price. For users with thousands of price records from automated feeds (Quote Checker, Yahoo Finance imports, etc.) this is a real gap.

### 3.2 `get_outstanding_invoices` doesn't bucket by aging

`get_outstanding_invoices` returns a flat list with `days_past_due`. Real bookkeepers want **30/60/90/120+ buckets** with totals per bucket. Either extend `get_outstanding_invoices(format="aged")` or add a new `get_aging_report(account, as_of_date)` tool. Probably the latter is cleaner.

### 3.3 Early-payment discount may not be applied

`create_billterm` accepts `discount_days` and `discount_percent`. I haven't verified whether `pay_invoice` actually applies the discount when payment lands within the discount window. Worth testing before designing tax-table specs (because tax-on-discount math can get messy).

---

## 4. Missing major features

These each deserve their own spec.

### 4.1 Tax tables (HIGHEST PRIORITY)

**Why:** Without it, the server can't ship to anywhere with VAT/GST/sales tax, which is most of the world. Lin Wei specifically didn't need it (Chinese sole proprietors don't charge consumption tax to enterprise clients), but the next persona — a UK Ltd consultant, a Canadian retailer, a Bavarian freelancer — will.

**Schema already in place:** `taxtables` and `taxtable_entries` tables exist. Each entry on an invoice/bill has an `i_taxtable` / `b_taxtable` column ready to receive a tax-table GUID. libgnucash's posting code already handles tax materialization if those fields are set correctly.

**Tools needed:**
- `create_taxtable(name, parent=None)` — with optional parent for hierarchical tables (e.g., "EU VAT" parent, "EU VAT - Standard 20%" child)
- `add_taxtable_entry(taxtable, account, amount_or_percent, type)` — type is 'PERCENT' or 'VALUE'
- `list_taxtables()`, `get_taxtable(name)`, `delete_taxtable(name)`
- Extend `add_invoice_entry` and `add_bill_entry` with optional `taxtable=None` parameter (and `taxable=True/False`, `tax_included=True/False` flags that already exist as `i_taxable`, `i_taxincluded` columns)

**Posting behavior:** When the invoice is posted, the post transaction should include extra splits — one per tax-table entry — that route the computed tax to the entry's destination account. libgnucash does this automatically if the entry's `i_taxtable` is set. Confirm whether the existing `post_invoice` correctly triggers this (I suspect yes, but it's worth testing because the tax-included flag affects whether tax is added to the line total or extracted from it).

**Edge cases for Chat to design around:**
- Multiple-component tax tables (e.g., Quebec: GST 5% + QST 9.975%, both on the same line)
- Tax inclusive vs exclusive pricing (whether the line price already has tax baked in)
- Reverse charge mechanism (B2B EU invoices where buyer self-accounts for VAT — usually means no tax line on the invoice but a notation that reverse charge applies)
- Tax on discount (if you apply a 2% early-payment discount, does the tax recompute or stay locked at the invoice-date amount?)
- Mixed-tax invoices (one line standard rate, one line zero-rated, one line exempt)
- Customer-specific tax exemptions (some customers pay no tax — exemption certificate on file)

**Cross-references:** Once tax tables exist, `get_outstanding_invoices` and the future `get_aging_report` should optionally surface tax-payable / tax-receivable balances by jurisdiction. The `pay_invoice` early-payment-discount logic (§3.3) needs to interact with tax correctly.

### 4.2 Jobs (HIGH PRIORITY for project-based businesses)

**Why:** GnuCash's `jobs` are how you group multiple invoices for the same customer under a project umbrella ("Acme Corp - Q1 2026 Engagement," "Smith Family - Kitchen Renovation"). Consultants, contractors, agencies, anyone billing per project, depend on this. It's also how a PM-shaped user can answer "how profitable was the Acme engagement" without doing the rollup by hand.

**Schema already in place:** `jobs` table exists. Invoices can reference a job via `owner_guid` when `owner_type` indicates a job rather than a customer/vendor directly. (Check the schema — I think the model is that the invoice points at the job, and the job points at the customer/vendor, so a chain.)

**Tools needed:**
- `create_job(name, customer_or_vendor_id, owner_type)` — bind to one customer or one vendor
- `list_jobs(customer_id=None, vendor_id=None, active=True)`
- `get_job(id)` — show all invoices/bills under it, totals
- `update_job(id, active=...)` — close/reopen a job
- Extend `create_invoice` and `create_bill` to optionally accept a `job_id` instead of a direct customer/vendor

**Edge cases:**
- Can a job span multiple customers? GnuCash says no, but real-life joint engagements happen. Design decision.
- Closed-job behavior: can you still issue invoices against a closed job? Standard answer is "no, but you can reopen it."
- Job-level reports: total billed, total paid, total outstanding per job. These would replace the customer-level rollup for project-based users.

### 4.3 Credit notes (MEDIUM PRIORITY)

**Why:** Returns and refunds happen everywhere. Right now if a customer returns goods after the invoice is posted, the bookkeeper has no clean tool — they'd have to either edit the original invoice (impossible after posting), write a manual offsetting transaction (loses the structured AR linkage), or void the original and reissue (wrong for partial returns).

**How GnuCash models it:** A credit note is essentially a negative invoice. Same `invoices` table row, but quantities and prices are negative. When posted, it generates a transaction that reverses the AR entry. Linked back to the original invoice via slot keys (so the "amount due on invoice 000007" calculation correctly nets the credit note).

**Tools needed:**
- `create_credit_note(customer_id, original_invoice_id=None, ...)` — same shape as `create_invoice` but flagged as credit-note. Optionally references the original invoice it's correcting.
- `add_credit_note_entry(...)` — same as `add_invoice_entry` but with sign-conventions handled correctly (positive quantities and prices internally, server flips the math)
- `post_credit_note(...)`, `pay_credit_note(...)` (or the existing `post_invoice`/`pay_invoice` could detect and dispatch — your call)

**Edge cases:**
- Credit note exceeds remaining invoice balance: handle as a customer credit that applies to the next invoice automatically? Or refund?
- Credit notes against fully-paid invoices: refund the customer (need a `payment_account` parameter to specify where the cash goes)
- Tax on credit notes: usually mirrors the original invoice's tax structure; the server should default to inheriting it.

### 4.4 Aging reports (MEDIUM PRIORITY)

Already mentioned in §3.2. Design as either an extension to `get_outstanding_invoices` or as a separate `get_aging_report(account, buckets=[30,60,90,120], as_of_date)` tool. The latter is cleaner because aging is a report shape, not just a filter on outstanding invoices.

---

## 5. Smaller bug fixes / API improvements

### 5.1 Unpost-invoice mechanism

Already described in §2.4. Three options:

1. **`unpost_invoice(id)` tool** that reverses the post (deletes the post txn, clears the invoice's posted-state fields). Most explicit.
2. **Cascade behavior on `delete_transaction`**: if the deleted txn is an invoice's post txn, automatically unpost the invoice. Most surprising, possibly the most user-friendly.
3. **Both**: `unpost_invoice` as the primary tool, plus `delete_transaction` refusing to delete a post-txn with a "use unpost_invoice first" error.

I'd recommend option 3.

### 5.2 `delete_price` tool

Trivial — single SQL `DELETE FROM prices WHERE guid=?`. Add it.

### 5.3 The data-range header in `get_book_summary`

See §2.6. Either restrict the range to transactions only, or call out price-driven extensions in the header.

---

## 6. Bulk-write tool (CROSS-CUTTING)

**The case for it:** As discussed in §2.1, LLM-loop CRUD is fundamentally the wrong shape for bulk inserts. Without a bulk path, the server can't reasonably support:

- One-time migrations from QuickBooks, Xero, FreshBooks, or other systems (often tens of thousands of historical transactions)
- Recurring nightly OFX/CAMT/CSV imports from banks
- Synthetic-book builders like our Lin Wei test setup
- Any scripted reconciliation workflow

**Tool shape:**

```
bulk_create_transactions(
    transactions: [
        {description, transaction_date, currency, splits: [...]},
        ...
    ],
    check_duplicates: bool = False,
    atomic: bool = True,  # if True, all-or-nothing; if False, best-effort
    return_failures: bool = True
)
```

Returns: per-transaction `{guid, status: created|skipped|failed, error: ...}`.

**Validation:** same per-transaction validation as `create_transaction`, but accumulated and returned in one batch. If `atomic=True`, any single failure rolls back the whole batch.

**Edge cases:**
- Lot assignment for investment buys — currently requires a separate `assign_split_to_lot` call. Bulk tool should accept optional `lot_id` per split.
- FX gain/loss on cross-currency splits — same logic as `create_transaction`; just runs N times.
- Duplicate detection cost — disabling it (`check_duplicates=False`) should be the default for bulk loads to avoid O(N²) behavior.

This deserves its own spec but it's a small one.

---

## 7. Recommended sequencing

Concretely, in priority order:

1. **Spec: Tax tables** — biggest single feature gap, unblocks non-US users, schema-ready
2. **Spec: Unpost-invoice + delete-price + data-range fix** — three small bugs in one spec, clears the technical-debt deck
3. **Spec: Credit notes** — depends partially on (1) for tax-mirror behavior; do after taxtables design is settled but can be implemented in parallel
4. **Spec: Jobs** — independent of the others, can slot in any time, smaller surface
5. **Spec: Bulk-write tool** — independent, do whenever there's a free engineering slot
6. **Spec: Aging reports** — lowest priority of these, more of a polish feature

Items 1-4 together complete the business module to a "ready to ship for SMB" bar. Item 5 changes what use cases the server can support. Item 6 is icing.

Out of scope for this round (flagged for later):

- Employees / expense vouchers — only matters for SMBs with W-2 employees, deprioritize
- Customer statements (printable PDFs) — output formatting, separate concern
- Recurring invoices — could be modeled via existing scheduled-transactions, separate design
- Multi-currency tax tables — defer until single-currency tax tables are working

---

## 8. What I'm not deciding (Chat: please decide these at design time)

- **Tax-included vs tax-exclusive default**: should `add_invoice_entry` default to `tax_included=False` (US convention) or `tax_included=True` (EU convention)? Both are valid. Pick a default and document why.
- **Hierarchical tax tables**: should the API expose the parent/child relationship, or always require flat tables? Hierarchical is more powerful but harder to reason about.
- **Credit-note linkage**: required, optional, or never? Your call — required is simplest, optional is most flexible, never (always standalone) is most permissive.
- **Job ownership rules**: one customer per job? Or allow multi-customer jobs as a power-user feature?
- **Bulk-tool atomicity default**: `atomic=True` or `atomic=False`? Atomic-by-default is safer; non-atomic-by-default is what migration tools usually want.

These are real tradeoffs, not just stylistic. Designing them upfront will save back-and-forth with Code later.

---

## 9. What this briefing covers vs. doesn't

**Covers:**
- Tax tables, jobs, credit notes (the major gaps)
- The unpost / delete-price / data-range bugs (smaller gaps)
- The bulk-write tool (infrastructure)
- Aging reports (polish)
- Out-of-scope items so they don't get re-litigated
- Tradeoffs Chat needs to make at design time

**Does NOT cover:**
- Detailed tool argument schemas — that's the spec's job
- Implementation details — that's Code's job
- UI/UX decisions for any client consuming this server — separate concern
- Versioning / migration strategy if these features change wire format — Chat should think about this when writing each spec

---

## 10. Reproduction harness

The Lin Wei book (`/Users/stephen/Finances/lin.wei.gnucash`) is available as a populated test bed. It already exercises:
- 3 foreign currencies (USD, EUR, HKD) against a CNY default
- 4 investment lots with partial sales
- ~1,960 transactions including ~1,100 daily-life items
- 3 customers (CNY, USD, EUR) and 3 vendors (CNY, USD, CNY)
- Reconciliation through 2025-12-31 on the checking account
- One past-due foreign-currency invoice (perfect for credit-note testing — issue a partial credit against invoice 000010 and watch the AR math)
- One Chinese-named budget with seasonal overrides

Anything you spec that needs validation can be exercised against this book directly. If a spec needs a different test shape (e.g., a US LLC with W-2 employees and tax tables), that's a separate synthetic build worth planning before Code starts implementation.

---

*Prepared by Claude Cowork after building the Lin Wei book end-to-end and surfacing the gaps. Hand off to Claude Chat for spec authoring; Code can then take individual specs in priority order.*
