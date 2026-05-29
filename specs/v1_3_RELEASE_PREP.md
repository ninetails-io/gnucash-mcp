# v1.3 Release Prep — Tasks for Claude Code

## 1. `reconcile_account` — add `except` parameter

**Value:** When reconciling a statement, the common case is "all
splits except one or two." Currently the LLM must either pass 106
GUIDs to include, or use `reconcile_all=true` which grabs
everything. The `except` parameter lets the LLM say
`reconcile_all=true, except=["guid1", "guid2"]` — 2 tokens
instead of 106. Complement to `through_date` for cases where the
exclusions aren't date-based (pending ACH, reversed holds, manual
splits the bank doesn't know about yet).

**Spec:**
- New optional parameter `except: list[str]` on `reconcile_account`
- Only valid when `reconcile_all=true` (reject otherwise)
- Fetch all unreconciled splits, remove any whose GUID prefix
  matches an entry in `except`, reconcile the rest
- Validate: remaining splits + existing reconciled balance ==
  statement_balance

## 2. Server instructions block rewrite

Replace the current `instructions` parameter in server config with
this (1,490 chars, 27% under the 2K cap):

```python
instructions="""GnuCash MCP Server — Double-Entry Accounting Tools
ORIENTATION:
- Call get_book_summary first. It returns structure, currency, balances, warnings, and reconciliation status.
- Use list_accounts to get exact paths and short GUIDs before writing.
- Use search_transactions before creating to avoid duplicates.
- Every transaction has splits that MUST sum to zero.
ACCOUNT REFERENCES:
- Tools accept full paths ("Expenses:Groceries"), short GUIDs ("%2e78c86"), or full 32-char GUIDs.
- list_accounts emits short GUIDs at line start: "%2e78c86\\tAssets:Savings [BANK]". Reuse them — ~80% smaller than paths.
- Paths are colon-delimited, case-sensitive. Use paths when naming new accounts or reasoning about hierarchy; short GUIDs for everything else.
- Account short GUIDs: 7+ hex chars with leading "%". Transaction/split GUIDs: 8+ bare hex prefix, no marker.
DOUBLE-ENTRY SIGN CONVENTION:
- Positive = debit (increases Asset/Expense, decreases Liability/Income/Equity).
- Negative = credit (reverse).
- Credit card payment: checking -200, card +200. Income: checking +3000, income -3000.
INVESTMENT FLOW: create_lot → create_transaction (with quantity/cost) → assign_split_to_lot → create_price → calculate_lot_gain.
SLOTS: get_account_slots / set_account_slot store per-account metadata (APR, credit limit, statement day) as strings.
SAFETY: Reconciled splits are protected (use force=true to override). Prefer void_transaction over delete for audit trail. delete_account is blocked if account has children or transactions.
"""
```

## 3. Parameter inconsistency: `invoice_id` vs `id`

`delete_invoice` takes `invoice_id`. Every other invoice tool
(`get_invoice`, `post_invoice`, `unpost_invoice`, `pay_invoice`)
takes `id`. Same for `delete_bill`, `delete_voucher`,
`delete_credit_note`.

Standardize: accept both `id` and `invoice_id` (alias), prefer
`id` in documentation. Don't break existing callers — just add
the alias.

## 4. CHANGELOG for v1.3

Write `CHANGELOG.md` entry for v1.3. Key additions since v1.2.1:

- Expense vouchers (create, post, pay employee reimbursements)
- Credit notes (create, post, apply against invoices/bills)
- Jobs (project-level grouping over invoices/bills per customer/vendor)
- Tax tables (composite rates, tax-inclusive pricing, refcount lifecycle)
- Billing terms
- `reconcile_all=true` bulk reconciliation mode
- `except` parameter on reconcile_account
- `through_date` filter on reconcile_account
- Account shortcut support in reconcile_account
- `get_book_summary` enhancements: overdue invoice warnings, receivable/payable counts, business entity summary
- FX fix: `spending_by_category` and `income_by_source` now convert foreign-currency splits to book default currency
- Currency-mixin refactor (code organization, no behavior change)
- FX gain/loss extraction to `_compute_fx_gain_loss` helper (code organization, no behavior change)
- Server instructions block rewrite (59% smaller)
- Parameter alias: `id` accepted on delete_invoice/bill/voucher/credit_note

Tool count: 76 → 106 (30 new tools).
Test count: 1,101 → 1,320+.

## 5. README update

Update tool count, add real `get_book_summary` output as example
(use Alex's book — it exercises business features). Update any
version references from 1.2.1 to 1.3.

## 6. Lin Wei cleanup

Restore from backup:
```
mv lin-wei.gnucash lin-wei.gnucash.broken
cp .../backups/lin-wei-20260522T014728650225-manual-pre-fx-extraction-test.gnucash lin-wei.gnucash
```
Book currently has test invoice 000022 + payment + USD price from
FX extraction testing.

## 7. Version bump

Update version string from 1.2.1 to 1.3.0 wherever it appears
(pyproject.toml, server config, any __version__ constants).
