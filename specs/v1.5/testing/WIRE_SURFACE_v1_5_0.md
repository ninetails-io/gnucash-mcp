# Wire surface capture — v1.5.0 consolidation (2026-08-29)

Regenerate: this script block, from TOOL_MODULES (the truth).

## accounts (7)
- create_account
- delete_account
- get_account
- get_balance
- list_accounts
- move_account
- update_account

## audit (1)
- get_audit_log

## backup (3)
- create_backup
- list_backups
- prune_backups

## balance_sheet (1)
- balance_sheet

## budgets (6)
- create_budget
- delete_budget
- get_budget
- get_budget_report
- list_budgets
- set_budget_amount

## business_complete (1)
- vendor_spending_report

## diagnostic (1)
- get_server_config

## freelancer (26)
- add_document_entry
- apply_credit_note
- create_billterm
- create_document
- create_job
- create_party
- create_taxtable
- delete_document
- delete_job
- delete_party
- delete_taxtable
- get_document
- get_job_report
- get_outstanding_documents
- get_party
- list_billterms
- list_documents
- list_jobs
- list_parties
- list_taxtables
- pay_document
- post_document
- unpost_document
- update_job
- update_party
- update_taxtable

## portfolio (7)
- create_commodity
- create_price
- create_prices
- delete_price
- get_latest_price
- get_prices
- list_commodities

## reconciliation (4)
- get_reconciliation_status
- get_unreconciled_splits
- reconcile_account
- set_reconcile_state

## reporting (5)
- cash_flow
- debt_payoff_plan
- income_by_source
- net_worth
- spending_by_category

## scheduling (6)
- create_scheduled_transaction
- create_transaction_from_scheduled
- delete_scheduled_transaction
- get_upcoming_transactions
- list_scheduled_transactions
- update_scheduled_transaction

## slots (3)
- delete_account_slot
- get_account_slots
- set_account_slot

## summary (1)
- get_book_summary

## tax_lots (6)
- assign_split_to_lot
- calculate_lot_gain
- close_lot
- create_lot
- get_lot
- list_lots

## transactions (12)
- create_transaction
- create_transactions
- delete_transaction
- enter_statement
- get_transaction
- list_transactions
- replace_splits
- search_transactions
- unvoid_transaction
- update_transaction
- update_transactions
- void_transaction

**Total mapped: 90** (+ switch_book when 2+ books; 111 pre-consolidation)
**Business surface: 27** (was 48)
