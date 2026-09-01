> **HISTORICAL SURFACE.** This audit catalogs the PRE-consolidation
> 111-tool surface (2026-08-05). Its consolidation plan SHIPPED on
> 2026-08-29 (feat/business-consolidation, 48 business tools → 27,
> total 90); the current registry is TOOL_MODULES in server.py and
> the capture in testing/WIRE_SURFACE_v1_5_0.md. Tool-by-tool
> descriptions below refer to retired names.

# GnuCash MCP Tool Guide and Interface Assessment

Assessment target: `/Users/stephen/Projects/gnucash-mcp`, branch
`fix/business-doc-lifecycle`, with the server reloaded on 2026-08-05.
The repository declares version 1.4.2 while this branch also contains
the v1.5 contract and lifecycle work.

This guide covers all 110 tools in `TOOL_MODULES` plus the conditional
`switch_book` tool, for 111 tools in a multi-book, all-modules session.

## Executive assessment

Overall LLM interface quality: **8.7/10**.

| Dimension | Score | Assessment |
|---|---:|---|
| Accounting correctness and safety | 9.3 | Strong double-entry validation, reconciled-split protection, audit logging, backups, strict unknown-argument rejection, and decimal-string handling. |
| Input design | 9.0 | Exact account references, typed nested splits, dry runs, correlation keys, and excellent TSV batch entry. Business CRUD is the main source of repetition. |
| Output design | 8.5 | Compact text and TSV defaults are excellent for an LLM; verbose JSON is explicit and minified. Some mutation envelopes contain TSV encoded inside JSON. |
| Tool selection and naming | 7.9 | Core names are clear. The business document family is polymorphic but still named around invoices, so the type coverage is not obvious from names alone. |
| Documentation | 9.1 | Detailed, operational docstrings with examples, failure behavior, and workflow guidance. A few descriptions remain longer than they need to be or lag the full polymorphic contract. |
| Payload efficiency | 8.0 | Compact responses and short GUIDs are strong. Tool-definition cost is high in an all-modules session, especially where common server instructions are repeated by the client presentation layer. |
| Modularity | 9.0 | Role and leaf modules let deployments avoid irrelevant tools. `core` remains complete enough to do real bookkeeping and reconciliation. |

The best design decisions are `get_book_summary` as an orientation
chokepoint, `%short` account GUIDs, decimal strings, compact-by-default
read tools, batch TSV with caller-owned `ref` values, and
`reconcile_all=true`. The biggest weakness is not behavior. It is the
48-tool business surface, where entity type and action are multiplied
into many near-duplicate names.

## How to use the server

### First calls

1. Call `get_book_summary` first.
2. In a multi-book session, confirm the current book in the summary or
   call `get_server_config`; use `switch_book` before resolving any
   account or entity references.
3. Use `list_accounts(query=...)` to resolve exact paths or `%short`
   GUIDs before writing.
4. Use `search_transactions` before creating entries where duplicates
   are plausible.
5. Prefer compact defaults. Set `verbose=true` only when structured
   JSON fields are needed; it does not add accounting information.

### Reference and sign conventions

- Account inputs accept a full case-sensitive path, a `%` plus 7 or
  more hexadecimal GUID characters, or a full 32-character GUID.
- Transaction, split, lot, and scheduled-transaction GUIDs accept a
  bare prefix of 8 or more hexadecimal characters.
- Positive values are debits; negative values are credits.
- Asset and expense debits increase balances. Liability, income, and
  equity credits increase balances.
- Amounts and quantities should be decimal strings, not JSON numbers.
- A transaction's split amounts must sum to zero in its transaction
  currency. A split into a different commodity also needs `quantity`.

### Output conventions

- Compact list/report output is complete text or TSV and usually begins
  with a `Showing X-Y of Z` indicator.
- `verbose=true` changes list/report output to structured JSON.
- Point reads and mutations generally return minified JSON.
- `limit=0` is the cheap count-only path on paginated tools.
- Empty strings and `null` values are stripped from serialized result
  objects; false booleans and empty collections remain.

### Mutation discipline

- Use `dry_run=true` before large transaction or price batches.
- Leave duplicate checking enabled. Use `force` only after inspecting
  the duplicate or safety condition it overrides.
- Prefer `void_transaction` to deletion when an audit trail matters.
- Reconciled data is protected. Preserve an unchanged reconciled bank
  leg when using `replace_splits` to recategorize its other leg.
- Posted business documents must normally be unwound in dependency
  order: void/remove payment, unpost document, then delete document.

## Module selection

`core` is always added. Counts below are current source-of-truth counts.

| Selection | Added tools | Intended caller |
|---|---:|---|
| `core` | 32 | Ledger, transactions, reconciliation, backups, audit, and balance sheet. |
| `bookkeeper` | 17 | Reporting, budgets, and recurring transactions. |
| `investor` | 13 | Commodities, prices, and tax lots. |
| `freelancer` | 31 | Customer invoicing, jobs, taxes, terms, and credit notes. |
| `business` | 48 | `freelancer` plus vendors, bills, employees, vouchers, and vendor reporting. |
| `all` | 110 | Every mapped tool; multi-book configuration can add `switch_book` as tool 111. |

For most personal books, `--modules=bookkeeper` produces 49 tools
including forced core. A freelancer gets 63 with
`--modules=freelancer`. A complete small-business deployment gets 80
with `--modules=business`. These cuts matter because the model must
select among every surfaced tool.

## Rating rubric

- **10**: immediately selectable, complete contract, efficient payload,
  strong safety, and no unnecessary lookup.
- **9**: production-quality with a minor naming, batching, or output
  limitation.
- **8**: correct and usable but requires inference, an extra call, or a
  repetitive schema.
- **7**: meaningful discoverability, consistency, or lifecycle friction.
- **6 or lower**: misleading, unsafe, materially incomplete, or costly.

The scores below assess LLM-caller interface quality, not the financial
importance of the operation.

## Complete tool catalog

### Summary (1 tool, module score 10/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `get_book_summary` | First-call dashboard: book identity, currency, data range, account structure, major balances, warnings, reconciliation state, net-worth trajectory, budget, schedules, and business counts. Compact and unusually high-value. | 10 |

### Accounts (7 tools, module score 9.2/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `list_accounts` | Browse or search the chart. Use `query` instead of paging when resolving a name; `root` scopes a subtree; compact rows expose reusable `%short` GUIDs. | 10 |
| `get_account` | Fetch type, commodity, description, placeholder state, GUID, and hierarchy for one account. A miss returns an error object rather than raising. | 9 |
| `get_balance` | Return one account balance on an explicit date; defaults to today and therefore excludes future-dated entries. Echoes the canonical account path. | 10 |
| `create_account` | Create a typed account under an optional parent, including non-currency commodities, description, notes, and placeholder state. | 9 |
| `update_account` | Rename or change metadata and compatible account types. Empty notes clear; cross-polarity type changes are blocked. | 9 |
| `move_account` | Reparent an account with children and balances intact. Cycle and sibling-name conflicts fail without changing the book. | 9 |
| `delete_account` | Delete only an empty leaf account. Children and transactions are hard blockers. Concise and safe. | 9 |

### Transactions (11 tools, module score 9.5/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `list_transactions` | Browse by account and date. Account-filtered compact output becomes register form with signed account impact; large split sets are summarized honestly. | 10 |
| `get_transaction` | Fetch the complete transaction and all split GUIDs/details from an 8+ character transaction GUID prefix. | 9 |
| `create_transaction` | Create one balanced transaction with optional auto-fill, duplicate detection, notes, raw-statement memos, multi-currency quantities, and dry run. | 10 |
| `create_transactions` | Atomic TSV bulk entry with caller refs, flexible split groups, notes, memos, quantities, actions, per-row currency, auto-fill, duplicate tables, dry run, and abort/skip policy. Best-in-class batch interface. | 10 |
| `update_transaction` | Update one transaction or broadcast identical fields to many. Supports clearing notes and guarded changes to reconciled entries. | 9 |
| `update_transactions` | TSV per-row bulk changes to date, description, and notes with abort/skip behavior. Efficient, but its docstring lacks a conventional `Args` block and batch cells cannot clear values. | 9 |
| `delete_transaction` | Delete one or an atomic list. Reconciled and invoice-posting transactions are protected unless the appropriate workflow or explicit force is used. | 9 |
| `replace_splits` | Replace the full split set while preserving unchanged reconciled legs and their memos/state. This makes safe recategorization possible without a bespoke tool. | 10 |
| `search_transactions` | Search description, memo, notes, or amount with pagination and compact output. Amount operators cover exact, range, greater-than, and less-than queries. | 9 |
| `void_transaction` | Audit-preserving cancellation that zeros split values and requires a reason. Prefer over deletion for booked activity. | 10 |
| `unvoid_transaction` | Restore pre-void amounts and reset restored splits to unreconciled. Clear contract, though the name does not advertise the reconciliation reset. | 9 |

### Account slots (3 tools, module score 8.5/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `get_account_slots` | Read all custom account metadata or one key, such as APR, credit limit, statement day, or `no_reconcile`. | 9 |
| `set_account_slot` | Store one string value under an account key. Flexible and compact, but intentionally schema-light because values are untyped. | 8 |
| `delete_account_slot` | Remove one metadata key; rejects unsafe slash-containing keys and leaves other slots intact. | 9 |

### Audit (1 tool, module score 9/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `get_audit_log` | Read human-oriented mutation history by date with pagination. Strong operational complement to the book, though it is not a general query language over audit fields. | 9 |

### Backup (3 tools, module score 9/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `create_backup` | Create a labeled on-demand snapshot in addition to automatic pre-write backups. Returns file metadata without exposing unnecessary content. | 9 |
| `list_backups` | List timestamped backups newest-first with stage and pagination. | 9 |
| `prune_backups` | Retention cleanup by stage with `dry_run=true` by default. Excellent destructive default; keeping a count rather than a date policy is simple but limited. | 9 |

### Balance sheet (1 tool, module score 9/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `balance_sheet` | Canonical assets, liabilities, and equity report on an optional as-of date, including commodity valuation. Clear single-purpose reporting primitive. | 9 |

### Diagnostics and book selection (2 possible tools, module score 9.5/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `get_server_config` | Verify loaded modules, tool count, active book filename, version, and debug state. Useful when client/server state may be stale. | 9 |
| `switch_book` | Multi-book-only session switch by unique filename prefix. Returns a loud context reset and invalidates all prior account/GUID/entity references. Transactional logging handoff is well designed. | 10 |

### Reconciliation (4 tools, module score 9.5/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `get_reconciliation_status` | Account-level work queue grouped into behind, never, current, dormant, and explicitly excluded. Better than inferring state from balances. | 10 |
| `get_unreconciled_splits` | Default compact TSV/text is the right choice. It returns split GUIDs and full-set totals with honest pagination; use `verbose=true` only for JSON fields. | 10 |
| `set_reconcile_state` | Set one split to new, cleared, or reconciled with a date. Correct low-level escape hatch, but easier to misuse than statement-level reconciliation. | 8 |
| `reconcile_account` | Reconcile targeted split GUIDs or sweep all through a date, optionally excluding a few GUIDs, while proving the statement balance. `reconcile_all` removes a costly lookup round trip. | 10 |

### Reporting (5 tools, module score 9/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `spending_by_category` | Expense totals for inclusive caller-supplied dates, optional hierarchy depth, JSON mode, or month/quarter/year TSV trends. No hidden calendar snapping. | 9 |
| `income_by_source` | Income counterpart to category spending, including depth and grouped trend output. | 9 |
| `net_worth` | One-date value or interval trajectory. When a series is requested, the end date is included even if it is off the regular step. | 9 |
| `cash_flow` | Inflow/outflow analysis, optional account scope, transfer treatment, and grouped periods. Detailed transfer semantics make a subtle report usable. | 9 |
| `debt_payoff_plan` | Avalanche schedule with optional hypothetical purchase and structured output. Valuable but domain-specific `YETI multiplier` terminology raises selection/doc cost. | 8 |

### Budgets (6 tools, module score 8.8/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `list_budgets` | Compact paginated inventory or verbose structured list. | 9 |
| `get_budget` | Read one budget and all period/account amounts; compact default avoids a large nested payload. | 9 |
| `create_budget` | Create monthly, weekly, or custom-period budgets from a year or start date. Correct but has several mutually dependent date/period inputs. | 8 |
| `set_budget_amount` | Set an account target for one period or all periods using decimal strings and exact account refs. | 9 |
| `get_budget_report` | Compare actuals to targets by period/account with optional child rollup. Compact default is efficient. | 9 |
| `delete_budget` | Permanently remove a budget and all budget amounts without touching transactions; a missing name fails without change. | 9 |

### Scheduling (6 tools, module score 8.7/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `create_scheduled_transaction` | Create a recurring balanced template with frequency, dates, notes, currency, and enabled state. Strong schema, necessarily detailed. | 9 |
| `list_scheduled_transactions` | Inventory templates with enabled filter, compact/JSON modes, and pagination. | 9 |
| `get_upcoming_transactions` | Due-instance work list for a caller-selected day window. | 9 |
| `create_transaction_from_scheduled` | Materialize one actual transaction from a template on an optional date. Simple, but no dry-run or duplicate-control arguments are surfaced here. | 8 |
| `update_scheduled_transaction` | Update enabled state, end date, or notes. Narrow update surface means frequency/splits require replacement rather than edit. | 8 |
| `delete_scheduled_transaction` | Delete a template by GUID. Clear but destructive and without a soft-disable shortcut in the same call; `update... enabled=false` is the safer route. | 8 |

### Portfolio and prices (7 tools, module score 8.9/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `list_commodities` | Inventory currencies/securities or produce the exact stale-held-quote work list with `stale_days` and `held_only`. | 10 |
| `create_commodity` | Create a security/currency-like commodity with namespace, precision, and optional CUSIP/ISIN. | 9 |
| `create_price` | Upsert one quote by commodity, currency, date, and source. Decimal-string value and quote-direction example are strong. | 9 |
| `create_prices` | Atomic TSV bulk quote upsert with refs, dry run, and abort policy. Optional columns must appear in one fixed order, which is stricter than `create_transactions`. | 8 |
| `get_prices` | Paginated compact history or verbose JSON with date/currency filters. | 9 |
| `get_latest_price` | Return the newest quote, including date, value, type, and source, or null. | 9 |
| `delete_price` | Delete by commodity/namespace/date with source disambiguation. Correct but naturally identity-heavy for one deletion. | 8 |

### Tax lots (6 tools, module score 8.5/10)

| Tool | Use and important behavior | Score |
|---|---|---:|
| `create_lot` | Create an empty cost-basis lot for an investment account; docstring gives the complete follow-on workflow. | 9 |
| `list_lots` | Paginated open-lot inventory with optional closed lots and JSON detail. | 9 |
| `get_lot` | Return assigned splits, quantity, cost basis, and per-share summary. | 9 |
| `assign_split_to_lot` | Attach an investment split to a lot. Correct, but requires a preceding transaction-detail lookup for the split GUID. | 8 |
| `calculate_lot_gain` | Actual/latest-price or hypothetical shares-and-price gain calculation. Useful but caller must understand whether it is realized or projected. | 8 |
| `close_lot` | Manual cleanup for a zero-share lot that did not auto-close. A low-level operation appropriately kept out of core. | 8 |

### Freelancer business tools (31 tools, module score 8.0/10)

#### Customers

| Tool | Use and important behavior | Score |
|---|---|---:|
| `create_customer` | Create a customer with currency, notes, and typed address fields. Duplicate-name warnings reduce orphan test records. | 8 |
| `list_customers` | Compact paginated active-customer list or verbose JSON. | 9 |
| `get_customer` | Fetch one customer by business ID. Simple, but duplicates the vendor/employee shape. | 8 |
| `update_customer` | Partial update, clearable notes, activation state, and partial address changes. | 8 |
| `delete_customer` | Delete only when no invoices depend on the customer; deactivation is the safer historical-record path. | 8 |

#### Documents and settlement

| Tool | Use and important behavior | Score |
|---|---|---:|
| `create_invoice` | Create a draft customer invoice, optionally tied to a term or job. No ledger entry exists until posting. | 8 |
| `add_invoice_entry` | Add a positive quantity/price line to an unposted invoice, with income account, tax table, notes, and action. | 8 |
| `list_invoices` | Actually queries the shared document table, but the name and description emphasize invoices/bills and under-advertise vouchers and credit notes. | 7 |
| `get_invoice` | Returns every document type, including vouchers and credit notes, with full entries. The polymorphism is good; the invoice-only name and customer/vendor-only `owner_type` argument text remain misleading. | 7 |
| `post_invoice` | Posts invoices, bills, vouchers, and credit notes through one implementation with FX staleness protection. The first sentence still names only invoices/bills. | 8 |
| `unpost_invoice` | Correctly reverses all four document types and preserves type identity. Its `owner_type` argument text still omits `employee`. | 8 |
| `pay_invoice` | Handles partial payments, bills, vouchers/credit notes where applicable, FX gain/loss, early-payment discounts, memo, and stale-rate forcing. Powerful but a 12-argument, 389-word local docstring is near the practical schema limit. | 8 |
| `delete_invoice` | Delete an unposted customer invoice, with preferred `id` plus a legacy alias. Safe but contributes to four duplicated document delete tools. | 7 |
| `get_outstanding_invoices` | Compact receivable/payable work list with owner filters, balances, and pagination. Strong report despite invoice-centric name. | 9 |

#### Taxes, terms, jobs, and credit notes

| Tool | Use and important behavior | Score |
|---|---|---:|
| `create_taxtable` | Create a multi-entry tax table with account routing and percentage/value semantics. Detailed and safe, but input entries are loose `list[dict]` rather than a typed nested model. | 8 |
| `list_taxtables` | Compact paginated inventory or verbose JSON. | 9 |
| `get_taxtable` | Fetch one table and all entries. | 8 |
| `update_taxtable` | Rename or replace entries with protection when posted documents depend on the table; `force` makes the exceptional path explicit. | 8 |
| `delete_taxtable` | Delete only when dependency rules permit. | 8 |
| `create_billterm` | Create due/discount terms. There is no corresponding get, update, or delete tool, so the lifecycle is visibly incomplete. | 7 |
| `list_billterms` | Read available terms in compact or JSON form. | 8 |
| `create_job` | Create a customer/vendor job with name and reference. | 8 |
| `list_jobs` | Filter by owner and active state with compact/JSON output. | 9 |
| `get_job` | Fetch one job by ID. | 8 |
| `update_job` | Partial name/reference/active update. | 8 |
| `delete_job` | Delete, with optional force behavior around linked documents. Deactivation is normally safer. | 8 |
| `get_job_report` | Per-job billed, paid, outstanding, and document breakdown. High-value endpoint that avoids client-side joins. | 9 |
| `create_credit_note` | Create a customer or vendor credit note, optionally tied to a source document, with correct reversed-posting semantics documented. | 9 |
| `add_credit_note_entry` | Add a positive-price line and let the credit-note flag invert posting. Explicitly prevents a common sign mistake. | 9 |
| `delete_credit_note` | Delete an unposted credit note with type validation, owner disambiguation, preferred `id`, and legacy alias. | 8 |
| `apply_credit_note` | Net a posted credit against a same-owner document without cash. Cross-owner errors now identify both owner types, IDs, and names. | 9 |

### Complete-business additions (17 tools, module score 8.0/10)

#### Vendors and employees

| Tool | Use and important behavior | Score |
|---|---|---:|
| `create_vendor` | Vendor counterpart to customer creation with currency, notes, address, and duplicate warning. | 8 |
| `list_vendors` | Compact paginated active-vendor list or verbose JSON. | 9 |
| `get_vendor` | Fetch one vendor by ID. | 8 |
| `update_vendor` | Partial update with clearable notes, activation, and partial address semantics. | 8 |
| `delete_vendor` | Delete only without dependent bills; deactivation preserves history. | 8 |
| `create_employee` | Create an employee for voucher workflows with currency and address. | 8 |
| `list_employees` | Compact paginated active-employee list or verbose JSON. | 9 |
| `get_employee` | Fetch one employee by ID. | 8 |
| `update_employee` | Partial employee update and activation. Unlike customer/vendor, there is no notes field, reflecting the underlying model but reducing family symmetry. | 8 |
| `delete_employee` | Delete only when voucher dependencies allow it; deactivation is safer for history. | 8 |

#### Bills, vouchers, and vendor reporting

| Tool | Use and important behavior | Score |
|---|---|---:|
| `create_bill` | Vendor counterpart to invoice creation, with term, job, currency, and caller-supplied ID. | 8 |
| `add_bill_entry` | Add an expense/asset line, optional input-tax table, notes, and action to an unposted bill. | 8 |
| `delete_bill` | Delete an unposted bill via preferred `id` or legacy `bill_id`. Repeats the document delete schema. | 7 |
| `create_voucher` | Create an employee expense voucher that later posts and pays through the shared invoice lifecycle tools. | 8 |
| `add_voucher_entry` | Add an expense/asset line to an unposted voucher. Repeats almost all bill-entry fields. | 8 |
| `delete_voucher` | Delete an unposted voucher via preferred `id` or legacy alias. | 7 |
| `vendor_spending_report` | Posted-bill totals by vendor, including billed, paid, outstanding, and optional grouped-period TSV. | 9 |

## Payload assessment

### Input payloads: 9/10

The strongest input path is headered TSV. It lets a caller send many
transactions or prices without repeating JSON keys, preserves decimal
text exactly, and returns caller correlation keys. `create_transactions`
also allows rows with different split counts and composes optional
fields through the header.

Short account GUIDs are another material win. A token-heavy path such
as `Assets:Current Assets:Checking Account` can be replaced after one
lookup by a stable `%xxxxxxx` reference. Batch mutation, list-valued
transaction update/delete, `reconcile_all`, and `except_guids` all
remove avoidable round trips.

The main input costs are:

- All-modules selection exposes 111 schemas.
- The business surface repeats nearly identical party and document
  fields.
- `pay_invoice` is necessarily broad and approaches mega-tool size.
- `create_prices` requires optional columns in fixed order, unlike the
  more flexible transaction header grammar.
- Tax-table entries use `list[dict]`, so their inner shape is described
  in prose rather than enforced and advertised by a nested model.

### Output payloads: 8.5/10

The compact default is the right design. It prevents models from asking
for verbose JSON reflexively, and the descriptions now say explicitly
that verbose changes structure rather than information. Honest page
indicators and full-set totals prevent truncated pages from being read
as complete reports.

Minified UTF-8 JSON, noise stripping, and TSV trend tables are sensible.
The main inefficiency is that some batch mutation results are JSON
envelopes containing escaped TSV strings. That is defensible when two
tables must be keyed together, but direct text sections would be
slightly cheaper for an LLM-only consumer.

### Tool-definition payload: 7.5/10 in the current Codex presentation

Measured from the reloaded `mcp__gnucash_mcp_2` tools visible to this
Codex session:

- 111 descriptions total 336,844 characters and 45,258 whitespace
  words, including displayed function declarations.
- A 2,029-character, 275-word server instruction prefix is repeated on
  every visible tool description by this presentation layer.
- That repeated prefix alone accounts for 225,219 characters before
  individual tool descriptions and schemas.
- `create_transactions` is the largest visible definition at 8,387
  characters; `pay_invoice` is second at 5,444.

The source correctly defines the common instructions once on `FastMCP`.
The repetition is therefore at least partly a client/adapter
presentation effect, not 111 copies in the Python source. It still
matters to this model's effective tool context. Module cuts and tool
consolidation reduce the repetition regardless of which layer causes
it. The server should not delete useful global guidance merely to work
around one host; it should first check the raw `tools/list` payload and
the adapter's flattening behavior.

## Consolidation plan

### Recommendation

Consolidate across **document or party type**, while keeping materially
different **actions** as separate tools. This preserves strong schemas
and truthful MCP safety annotations.

The current business package is 48 tools, not more than 50 on this
branch: 31 in `freelancer` and 17 in `business_complete`. A practical
target is **19 business tools**, reducing an all-modules server from 110
mapped tools to about 81 without changing the book layer.

### Why not one `action` mega-tool

A single tool such as:

```text
business_document(action, document_type, id?, owner_id?, account?,
                  entries?, post_account?, payment_account?, ...)
```

would cut the count aggressively, but it has serious costs:

- MCP `ToolAnnotations` apply to the whole tool. One callable cannot
  honestly be read-only for `get`, non-destructive for `create`, and
  destructive for `delete` at the same time.
- Most fields become nullable and legal only for certain action/type
  combinations. JSON Schema exposes a large bag of options while the
  real contract moves back into prose and runtime errors.
- The output shape changes radically by action.
- The description becomes longer than `pay_invoice`, offsetting some
  schema-count savings.
- Permission systems and clients cannot distinguish a read from a
  deletion before invocation.
- Audit operation derivation becomes more indirect.

A Pydantic discriminated union could express action-specific variants,
but MCP clients vary in how well models and UIs handle `oneOf` schemas.
It also does not solve per-action tool annotations.

An action field is appropriate where every action has similar risk and
shape. It is not a good top-level boundary for create/get/delete in an
accounting system.

### Proposed 19-tool business surface

#### Parties: 15 tools to 5

| New tool | Replaces | Key schema |
|---|---|---|
| `create_party` | `create_customer`, `create_vendor`, `create_employee` | `party_type: customer|vendor|employee`, name, currency, notes, address. Reject notes for employee if unsupported. |
| `list_parties` | Three list tools | Optional `party_type`; active filter, pagination, verbose. |
| `get_party` | Three get tools | `party_type`, `id`. Type should be required because ID counters collide. |
| `update_party` | Three update tools | `party_type`, `id`, shared partial fields. |
| `delete_party` | Three delete tools | `party_type`, `id`; retain dependency checks and recommend deactivation. |

#### Documents: 18 tools to 8

| New tool | Replaces | Key schema |
|---|---|---|
| `create_document` | Four create tools | `document_type: invoice|bill|voucher|credit_note`, `owner_id`, dates, currency, term, job/source link, optional `id`. Derive valid owner type from document type except credit notes. |
| `add_document_entry` | Four entry tools | `document_type`, `id`, account, description, quantity, price, tax, notes, action. Account-type validation stays in the book layer. |
| `list_documents` | `list_invoices` | Optional document/owner type, status, job, pagination, verbose. Name finally matches actual shared-table behavior. |
| `get_document` | `get_invoice` | `id`, optional/required disambiguating document or owner type; always returns explicit `type`. |
| `post_document` | `post_invoice` | Same posting schema, with a name that truthfully covers all four types. |
| `unpost_document` | `unpost_invoice` | Same unpost behavior and payment dependency guard. |
| `settle_document` | `pay_invoice`, `apply_credit_note` | `method: cash|apply_credit`, then a small method-specific payload. If the union is awkward, keep `pay_document` and `apply_credit_note` as two tools, making the target 20 instead of 19. |
| `delete_document` | Four delete tools | `document_type`, `id`. One preferred ID field; legacy aliases remain only in legacy wrappers. |

This is the most important consolidation. It removes ten tools and the
misleading invoice-centric generic names while retaining action-specific
safety annotations.

#### Reference data: 7 tools to 2

| New tool | Replaces | Design note |
|---|---|---|
| `manage_billterms` | `create_billterm`, `list_billterms` | `action=create|list` is acceptable because the surface is tiny; add get/update/delete later only if the book layer supports them. |
| `manage_taxtable` | Five tax-table tools | An action union is defensible but loses read/delete annotations. Prefer `list_taxtables` plus `mutate_taxtable(action=create|update|delete)` if annotations are a priority. |

For strict annotation fidelity, this section becomes four tools rather
than two.

#### Jobs: 6 tools to 2

| New tool | Replaces | Design note |
|---|---|---|
| `manage_job` | Create/list/get/update/delete job | This is the least certain merge. A five-tool polymorphic CRUD family is safer if per-action annotations matter more than count. |
| `get_job_report` | Existing report | Keep separate because it is a high-value analytical read with a stable shape. |

An annotation-preserving variant can merge `get_job` into
`list_jobs(id=...)` because both are reads, while keeping create,
update, delete, and report separate. That produces five job tools.

#### Reports: keep 2

- Rename `get_outstanding_invoices` to `get_outstanding_documents` or
  `get_receivables_payables`.
- Keep `vendor_spending_report` unchanged.

### Preferred balance: 24 tools

The technically strongest endpoint is **24 business tools**, not 19:

- 5 party tools
- 8 document tools
- 4 reference-data tools split by read versus mutation
- 4 job CRUD/list tools (`list_jobs` also handles exact-ID lookup) plus
  `get_job_report` (5 total)
- 2 reports

This preserves truthful read-only/destructive/idempotent annotations
while still removing 24 of 48 business tools. The all-modules surface
falls from 110 to about 86 mapped tools, plus conditional `switch_book`.
Keeping `get_job` separate would make the package 25 tools and the
all-modules surface 87.

### Implementation shape

No rewrite of the 7,896-line book business mixin is required. The book
layer already has shared document primitives (`get_invoice`,
`post_invoice`, `unpost_invoice`, `pay_invoice`) and type-resolution
helpers. Consolidation can be a tool-wrapper routing layer.

1. Add strict `Literal` aliases for `PartyType`, `DocumentType`, and
   settlement method.
2. Add typed nested models for tax-table entries and any settlement
   variants. Keep `extra="forbid"` and decimal-string coercion.
3. Implement private routing helpers in `tools/business.py` that map
   document/party type to the existing book methods.
4. Register the new surface in revised `freelancer` and
   `business_complete` module lists.
5. Move old names into an opt-in `business_legacy` module for one
   compatibility cycle. Do not expose old and new names together by
   default, or the tool-count goal is defeated.
6. Return canonical `type`, `id`, `owner_type`, and status fields from
   every document mutation so follow-up calls need no lookup.
7. Preserve one action per safety class. Derive annotations from the
   actual wrapper operation as the server does today.
8. Extend contract tests to assert registry count, no unmapped tools,
   annotation truth, legacy parity, owner-ID collision errors, credit
   identity through post/unpost, and voucher lifecycle behavior.
9. Run the existing cross-model battery cold against the consolidated
   names before removing legacy exposure.

### Naming and schema rules for the new surface

- Use `document_type`, not `owner_type`, when the caller is choosing an
  invoice, bill, voucher, or credit note.
- Use `party_type` for customer/vendor/employee records.
- Use `id` everywhere after creation. Do not carry four legacy ID field
  names into the new tools.
- Require explicit type where independent ID counters can collide.
- Always return the canonical document type, even when the caller gave
  enough context to infer it.
- Keep compact/verbose semantics identical across every list tool.
- Keep `pay_document` and `apply_credit_note` separate if a clean typed
  settlement union cannot be expressed without many nullable fields.

## Specific documentation fixes still worth making

1. `get_invoice` says it supports all four document types, but its
   `owner_type` argument documents only customer/vendor. Add employee
   and explain credit-note disambiguation.
2. `unpost_invoice` names all four types in its lead but documents only
   customer/vendor in `owner_type`. Add employee.
3. `post_invoice` supports all four types but opens with only customer
   invoice/vendor bill. Rename it or make the first sentence complete.
4. `list_invoices` understates the shared table's document coverage.
   Confirm and document whether vouchers and credit notes should be
   included and filterable.
5. `update_transactions` and `create_prices` explain their parameters
   in prose but omit conventional `Args` sections. The schemas are
   still usable, but consistency would improve generated descriptions.
6. `create_prices` should either allow optional header columns in any
   declared order or state the fixed-order difference from
   `create_transactions` in its first input paragraph.
7. Replace `list[dict]` in `create_taxtable` and `update_taxtable` with a
   typed `TaxTableEntryInput` model so account, amount, and type appear
   in JSON Schema and reject misspelled keys before backup/audit work.

## Verification basis

This assessment used:

- the live, reloaded 111-tool Codex surface;
- `TOOL_MODULES`, module filtering, common server instructions, and
  annotation derivation in `src/gnucash_mcp/server.py`;
- every wrapper and docstring under `src/gnucash_mcp/tools/`;
- shared input/serialization helpers in `tools/_helpers.py`;
- the business book-layer routing and lifecycle methods;
- contract, business, batch, reconciliation, annotation, and prior
  cross-model test specifications;
- the repository's full pytest suite: **1,972 passed** in 127.58 seconds
  (903 dependency/SQLAlchemy warnings, no failures).
