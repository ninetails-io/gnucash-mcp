# Multicurrency review — v1.3 pre-release

Captured from deep-read agent run on 2026-06-02. Agent could not write
directly (read-only Explore subagent), so this file is its findings
transcribed verbatim.

## Summary

Aggregation paths and FX gain/loss are in good shape. Three rough
edges remain — one ship-blocker (`list_commodities` missing the
transaction-rate filter), and two `_collect_warnings` glitches that
produce false signals on multi-currency books. One realistic but
non-blocking third-currency edge case in `_compute_fx_gain_loss`.

## Critical findings

### 1. `list_commodities` walks `book.prices` without filtering auto-rates
`src/gnucash_mcp/book/investments.py:53-59` — the latest-price loop
includes piecash's auto-created `type='transaction'` placeholder
records. On books with recent cross-currency transactions, displayed
"latest price" can be 1.00 (the transaction-rate placeholder) instead
of the actual market quote.

**Fix:** add `if not _is_market_price(p): continue` at the head of the
loop. One line. Matches the helper used elsewhere.

## High-priority findings

### 2. `_compute_fx_gain_loss` raises on missing third-currency rate
`src/gnucash_mcp/book/business.py:856-862` — when paying an invoice
from a third-currency account and no `pay → default` rate is on file,
the method raises hard. Should mirror the `rate_at_post` branch and
return `None` (skip FX booking when material data isn't available).

### 3. `_collect_warnings` stale-price `in_use` set polluted by auto-rates
`src/gnucash_mcp/book/core.py:918-921` — the `in_use` set is built
from ALL prices including the `type='transaction'` placeholders, so a
commodity that has *only* the placeholder gets marked "in use" and
slips past the "no market price on file" warning.

**Fix:** `if not _is_market_price(p): continue` before
`in_use.add(p.commodity.guid)`.

### 4. Imbalance-account warning sums raw quantities across currencies
`src/gnucash_mcp/book/core.py:710-716` — when a non-default-currency
Imbalance account exists, splits are summed via `split.quantity`
without conversion. Unlikely to trigger in practice (Imbalance
accounts are usually in default currency) but violates the
multi-currency discipline used elsewhere.

**Fix:** route each split through `_split_in_default_currency` before
summing.

## What looks right

- Multi-currency aggregation in `spending_by_category`,
  `income_by_source`, `balance_sheet`, `net_worth`, `cash_flow` — all
  convert splits via `_split_in_default_currency`.
- Budget pacing conversion (`book/core.py:1192`) — correctly converts
  across currencies.
- FX gain/loss in `pay_invoice` via `_compute_fx_gain_loss` — sign
  conventions correct, post-vs-pay rate sourcing correct.
- Three+ market-rate paths correctly use `_is_market_price`
  (`_find_exchange_rate`, `_market_value`, `_latest_market_rates`).
- Value vs. quantity discipline holds at every transaction-write
  path.
- Balance-sheet identity holds with A/R and A/P in totals.
