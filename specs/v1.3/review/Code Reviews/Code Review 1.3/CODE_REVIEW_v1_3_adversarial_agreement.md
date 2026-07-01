# Cross-Tool Agreement Audit (v1.3 Adversarial Review)

Adversarial scan for places where two MCP tools, asked the same
question about the same data, would return different answers. This
class of bug has historically come from the bookkeeper review loop
because every tool's unit test isolates that tool and never asks
"does Tool A agree with Tool B?". Predecessor letters mention
canonical examples (USD-default `get_latest_price`, `get_book_summary`
vs `balance_sheet` March-31/April-30 price drift, jobs not chased
in `get_outstanding_invoices`).

Scope: read-only audit of `src/gnucash_mcp/book/*.py` at commit
`fda6ef8`. No code modified. Findings ranked roughly by severity
(impact on user-visible numbers + likelihood of triggering).

---

## A. CONFIRMED disagreements

### A-1. `list_commodities` shows transaction-placeholder prices as "latest"; every other price tool skips them

**Tools:** `list_commodities` vs `get_latest_price` / `get_book_summary` /
`balance_sheet` / `_rates_as_of` / `get_prices` (`get_prices` shows
all, but flags `type` column).

**Code:**

- `book/investments.py:54-59` (`list_commodities`):
  ```
  latest_prices: dict[str, tuple] = {}
  for p in book.prices:
      key = f"{p.commodity.namespace}:{p.commodity.mnemonic}"
      p_date = _to_date(p.date)
      if key not in latest_prices or p_date > latest_prices[key][0]:
          latest_prices[key] = (p_date, p)
  ```
  No `_is_market_price(p)` filter.

- `book/investments.py:516-521` (`get_latest_price`):
  ```
  if not _is_market_price(p):
      continue
  ```
- `book/_currency.py:156-157` (`_rates_as_of`, used by
  `get_book_summary` and `balance_sheet`):
  ```
  if not _is_market_price(p):
      continue
  ```

**Scenario:** Alex has a EUR-denominated invoice posted yesterday.
piecash auto-created a `type='transaction'` Price row to capture
the effective EUR→USD rate of that one posting. The next call to
`list_commodities` shows EUR latest price = that auto-placeholder
(e.g. 1.083 from yesterday's invoice). But `get_latest_price("EUR",
"CURRENCY")`, `_rates_as_of`, `get_book_summary`'s asset valuation,
and `balance_sheet`'s asset valuation all skip the placeholder and
return the most recent real market quote (e.g. 1.085 from a week
ago via yfinance).

**Walked numbers (Alex's book, hypothetical):**
- `list_commodities` row: `CURRENCY:EUR  latest_price: 1.083 (2026-06-02)`
- `get_latest_price("EUR", "CURRENCY")`: `{"value": "1.085", "date": "2026-05-26"}`
- `balance_sheet` line for `Assets:EUR Cash` (€1,000): displays
  `1000.0000 EUR @ 1.085 (USD 1,085.00)`
- `list_commodities`-derived expectation: $1,083.

**Classification:** CONFIRMED disagreement. Same canonical fix as
`get_latest_price` got — route through `_is_market_price`.

---

### A-2. `_resolve_account` returns template accounts for `%short` and full-GUID input but not for path input

**Tools:** `get_account` (and any tool using `_resolve_account` —
`get_balance`, `cash_flow(account=...)`, `update_account`,
`reconcile_account`, etc.).

**Code:**

- `book/_base.py:991-997` (`_find_account`):
  ```
  template_guids = self._template_account_guids(book)
  for account in book.accounts:
      if account.guid in template_guids:
          continue
      if account.fullname == fullname:
          return account
  return None
  ```
- `book/_base.py:1153-1179` (`_resolve_account`):
  ```
  if ref.startswith(self._SHORT_ACCOUNT_GUID_PREFIX):
      ...
      return book.session.query(Account).filter_by(guid=full_guid).first()
  if len(ref) == 32 and _HEX_GUID_RE.fullmatch(ref):
      ...
      return book.session.query(Account).filter_by(guid=ref.lower()).first()
  return self._find_account(book, ref)
  ```

The `%short` and 32-char-GUID branches go straight to SQLAlchemy with no
template filter; only the path branch routes through `_find_account`
(which DOES filter).

**Scenario:** User reads `list_accounts` (which filters templates) and
sees no template entries. But `_account_short_guid_map` builds prefixes
across ALL accounts including templates. If a `%xxxxxxx` prefix maps
to a template account guid (because the template's guid lands first
in the prefix table), then `get_account("%xxxxxxx")` returns the
template account, while `get_account("Some Template Path")` correctly
returns None.

A worse path: tools like `update_account`, `move_account`,
`delete_account`, `cash_flow(account=...)`, `reconcile_account`,
`set_account_slot` accept short or full GUIDs and call
`_resolve_account`. They could be invoked against a template account
and partially succeed before failing on downstream invariants — or
worse, silently mutate template scaffolding.

**Classification:** CONFIRMED disagreement. Fix: add the
`template_guids` filter to the `%short` and 32-char-GUID branches of
`_resolve_account`, OR exclude template guids from
`_account_short_guid_map` (probably both).

---

### A-3. `_budget_headline` (dashboard) does NOT roll up children; `get_budget_report` DOES

**Tools:** `get_book_summary` (budget line) vs `get_budget_report`.

**Code:**

- `book/core.py:1186-1200` (`_budget_headline`):
  ```
  for s in txn.splits:
      if s.account.guid not in budgeted_account_guids:
          continue
      ...
  ```
  Only counts splits ON budgeted account guids.

- `book/budgets.py:776-789` then `812-827` (`get_budget_report`):
  ```
  for desc in descendants:
      if desc.fullname in budgeted:
          continue
      ...
      rollup_map[desc.fullname] = acct_name
  ...
  for split, _txn, account in rows:
      rollup_target = rollup_map.get(account.fullname)
      if rollup_target is None:
          continue
      ...
  ```
  Descendants roll up to the nearest budgeted ancestor.

**Scenario (real, Alex-like):** User budgets `Expenses:Utilities`
(placeholder, no direct splits) at $400/month. Spend lives in leaf
children: `Expenses:Utilities:Electric` ($180),
`Expenses:Utilities:Gas` ($110).

- `get_budget_report` actuals: $290 (rolled up), `used_pct ≈ 73%`.
- `_budget_headline` actuals: $0 (no splits on the parent guid
  itself), `used_pct = 0%`.

The dashboard renders "Budget: <name> 0% used (40% elapsed)" while
the detailed report shows "73% used". `_budget_headline`'s
docstring acknowledges "no parent rollup" but doesn't surface that
this diverges from `get_budget_report` by design. PR #46
(v1.2.1) fixed this in `get_budget_report`; the dashboard
headline was not updated to match.

**Classification:** CONFIRMED disagreement.

---

### A-4. Reconciliation backlog count: `get_book_summary` excludes splits before `latest_y_date`; `get_unreconciled_splits` includes them

**Tools:** `get_book_summary` (reconciliation section) vs
`get_unreconciled_splits`.

**Code:**

- `book/core.py:338-344` (`_collect_reconciliation`, the
  summary's per-account block):
  ```
  days_behind = (today - latest_y_date).days
  unreconciled_count = sum(
      1 for s in account.splits
      if s.reconcile_state != "y"
      and s.transaction.post_date > latest_y_date
  )
  ```
  Counts only splits past the last-reconciled date.

- `book/reconciliation.py:181-195` (`get_unreconciled_splits`):
  ```
  if split.reconcile_state != "y":
      split_dict = {...}
      all_unreconciled.append(split_dict)
      if split.reconcile_state == "c":
          cleared_total += split.quantity
      else:
          uncleared_total += split.quantity
  ```
  Counts ALL non-`y` splits regardless of post date.

**Scenario:** Checking is reconciled through 2026-05-01. Between
2026-01-01 and 2026-04-30 there are 8 splits left in 'c' (cleared but
not finalized — i.e., the user never closed those statements out).
Since 2026-05-01 there are 5 new 'n' splits.

- `get_book_summary` reconciliation line: `Checking: 5 unreconciled
  through 2026-05-01 (33 days behind)`.
- `get_unreconciled_splits("Checking")`: count = 13 (8 + 5).

The bookkeeper sees a 5/13 mismatch and assumes one of the two
tools is broken.

**Classification:** CONFIRMED disagreement. Both numbers are
arguably "right" for different questions ("what do I still need to
clear past my last reconciliation?" vs "how many splits in the
register are not yet finalized?"). But the units don't match and
nothing in the response tells the LLM that.

---

### A-5. `calculate_lot_gain` includes `type='transaction'` placeholders when picking "latest price"; `get_latest_price` skips them

**Tools:** `calculate_lot_gain` (default sale_price path) vs
`get_latest_price` and all other price-consuming tools.

**Code:**

- `book/investments.py:944-956` (`calculate_lot_gain`):
  ```
  candidates = book.session.query(Price).filter_by(
      commodity_guid=commodity.guid,
  ).all()
  latest_price = None
  latest_date = None
  for p in candidates:
      p_date = _to_date(p.date)
      if latest_date is None or p_date > latest_date:
          latest_date = p_date
          latest_price = p
  ```
  No `_is_market_price` filter, no currency filter.

- `book/investments.py:498-521` (`get_latest_price`): filters by
  `currency` AND `_is_market_price`.

**Scenario:** A STOCK lot in VTSAX (USD-denominated). User
recently posted a cross-currency transaction touching VTSAX (rare
but possible for FX-margin scenarios, or a corporate-action mid-
transition). piecash auto-created a `type='transaction'` Price row
dated yesterday at an effective rate of $1.00. User has a yfinance
`type='last'` price from this morning at $273.43.

- `calculate_lot_gain(lot)` picks the $1.00 placeholder as "latest"
  → wildly understated proceeds.
- `get_latest_price("VTSAX", "FUND")` correctly returns $273.43.

**Classification:** CONFIRMED disagreement. Even without the
cross-currency edge case, the missing currency filter means a
VTSAX with prices quoted in both USD and EUR could return the EUR
price as "latest" and multiply EUR-shares-priced into a USD lot.

---

### A-6. Voided splits inflate `get_unreconciled_splits` count but not `_collect_reconciliation` count via different path

**Tools:** `get_unreconciled_splits` count vs `_collect_reconciliation`
count.

**Code:** `get_unreconciled_splits` checks `reconcile_state != "y"`
which is true for `'v'` (voided). `_collect_reconciliation`'s
`unreconciled_count` does the same check. So actually both INCLUDE
voided splits in their count.

The disagreement is not between these two but between either of
these and the user's mental model — "voided" means "neutralized,
not part of the open work." Both should arguably filter
`reconcile_state == 'v'`.

**Classification:** NEEDS_VERIFICATION as a cross-tool issue;
CONFIRMED as a UX inconsistency. Currently both tools agree but
both are arguably wrong against user intent. Mentioned because
predecessor 4.7-evening noted this pattern.

---

### A-7. `_runway_metrics` uses date-filtered rates; rest of `get_book_summary` uses unrestricted "latest"

**Tools:** `get_book_summary`'s runway figure vs its asset-display
and trajectory-now figures.

**Code:**

- `book/core.py:1322` (`_runway_metrics`):
  ```
  rates = self._rates_as_of(book, today, default_currency)
  ```
  Only prices on-or-before today.

- `book/core.py:1818` (inline in `get_book_summary`):
  ```
  latest_prices: dict[str, Decimal] = self._rates_as_of(book)
  ```
  No date filter — picks up future-dated forecast prices the
  bookkeeper has deliberately written.

- `book/core.py:423-426` (`_compute_net_worth_at`):
  ```
  if as_of >= date.today():
      rates = self._rates_as_of(book)
  else:
      rates = self._rates_as_of(book, as_of, default_currency)
  ```
  Special-cases "now anchor" to also use the unrestricted set.

**Scenario:** Bookkeeper writes a yfinance forecast price dated
tomorrow ($280 for VTSAX, current real is $273).

- Dashboard "now" net worth and per-account display: uses $280.
- Dashboard runway liquid-assets pool: uses $273 (today-filter
  excludes tomorrow).

The runway calculation under-reports liquid asset value by the
forecast delta. Probably small in practice, but the inconsistency
violates the principle "every byte of get_book_summary tells the
same story." Predecessor 4.7-evening flagged this exact philosophy
split ("today filter for events vs for revaluations"); the runway
helper appears to have been overlooked when the rest of the
dashboard converged on unrestricted-latest.

**Classification:** CONFIRMED disagreement, small-magnitude in
typical cases.

---

### A-8. `_query_filtered_splits` does NOT filter template accounts; per-account loops in `get_book_summary` DO

**Tools:** `balance_sheet`, `net_worth`, `spending_by_category`,
`income_by_source`, `cash_flow` (all use `_query_filtered_splits`)
vs `get_book_summary`, `_compute_net_worth_at` (filter `template_guids`).

**Code:**

- `book/_query.py:80-93`: no template filter, no placeholder filter.
- `book/core.py:1795`, `_compute_net_worth_at:415`,
  `_collect_reconciliation:269`, `_daily_expense_burn` (via
  `account.type` test inline), `_runway_metrics:1321`: explicit
  `template_guids = self._template_account_guids(book)` filter.

**Scenario:** This is dormant in practice today because
`create_scheduled_transaction` creates template accounts with
`type=BANK` but assigns NO splits to them — they're just
scaffolding. So `_query_filtered_splits(account_types={"BANK"})`
returns 0 rows from template accounts and the arithmetic is
correct.

But the invariant in CLAUDE.md says:
> Any iteration that aggregates balances, surfaces accounts to the
> user, or classifies by type must filter them via
> `self._template_account_guids(book)`.

`_query_filtered_splits` is the canonical aggregation primitive
and silently violates this invariant. The moment some future
change starts assigning splits to template accounts (e.g., a
"materialize the next instance" preview that posts splits to the
template before promoting), every report flips from "right by
coincidence" to "silently double-counts."

**Classification:** NEEDS_VERIFICATION as an active bug, CONFIRMED
as an architectural invariant violation. The fix is one line in
`_query_filtered_splits` to add the template filter; the cost is
zero on today's data.

---

## B. NEEDS_VERIFICATION

### B-1. `get_outstanding_invoices` reports per-invoice amounts in invoice currency; `balance_sheet`/`get_book_summary` show A/R in book default currency

**Tools:** `get_outstanding_invoices` (amount_due) vs `balance_sheet`
(receivables row) vs `get_book_summary` (receivables_total).

**Code:**

- `book/business.py:7449-7452`:
  ```
  "original_amount": str(grand_total),
  "amount_paid": str(amount_paid),
  "amount_due": str(abs(balance)),
  ```
  Values are in `inv.currency` (the invoice's currency). The
  `currency` field is part of the response, so the data is
  technically present.

- `book/reporting.py:425`, `book/core.py:1882-1898`: A/R accounts
  flow through `_split_in_default_currency` / `_market_value`,
  yielding book-default amounts.

**Scenario:** USD-default book. One outstanding €5,000 EUR invoice.
EUR→USD rate = 1.10.

- `get_outstanding_invoices`: `amount_due: "5000"` with
  `currency: "EUR"`.
- `balance_sheet`: A/R subtotal $5,500.
- `get_book_summary` receivables_total: $5,500.

A bookkeeper running both and summing "amount_due" across the
invoice list to validate balance_sheet would see $5,000 ≠ $5,500.
The discrepancy is "correct" (different currencies) but invisible
without parsing the per-row `currency` field.

**Classification:** NEEDS_VERIFICATION — is this a UX bug or a
deliberate design? `_get_book_summary` `receivables_total` line
arguably should annotate "(N invoices, M cross-currency)" so the
mismatch is explainable.

---

### B-2. `cash_flow` includes transfers; `spending_by_category` / `income_by_source` exclude transfers — naturally divergent

**Tools:** `cash_flow.outflows` vs `spending_by_category.total`;
`cash_flow.inflows` vs `income_by_source.total`.

**Code:**

- `book/reporting.py:752-786` (`cash_flow`): aggregates splits
  whose account is in `_CASH_TYPES = {"BANK", "CASH"}`.
- `book/reporting.py:240-311` (`spending_by_category`): aggregates
  splits whose account is in `{"EXPENSE"}`.

**Scenario:** $3,200 paycheck → checking; $500 transfer
checking → savings; $2,700 spent across expense categories.

- `cash_flow.inflows`: $3,200 (paycheck) + $500 (savings inflow)
  = $3,700.
- `cash_flow.outflows`: $500 (checking → savings) + $2,700 (spend)
  = $3,200.
- `income_by_source.total`: $3,200.
- `spending_by_category.total`: $2,700.

The user's reasonable expectation is "cash_flow.inflows
== income_by_source.total" and "cash_flow.outflows ==
spending_by_category.total". Neither holds when transfers exist.

**Classification:** NEEDS_VERIFICATION — design-correct but
undocumented. The `cash_flow` docstring doesn't surface this. A
bookkeeper using both for a quick sanity check would be confused.
Mitigation: a one-line annotation in the response noting that
cash_flow includes inter-account transfers.

---

### B-3. `_market_value` cost-basis fallback walks ALL account splits, ignoring `today`-filter where the caller passes `today=None`

**Tools:** `get_book_summary` (uses `_market_value(today=today)`)
vs callers that pass `today=None`.

**Code:**

- `book/_currency.py:265-269`:
  ```
  cost_basis = Decimal("0")
  for s in account.splits:
      if today is None or s.transaction.post_date <= today:
          cost_basis += Decimal(str(s.value))
  return cost_basis, ...
  ```

Only one caller passes `today` — `get_book_summary` at line 1866.
`_compute_net_worth_at` does NOT use `_market_value`; it inlines its
own cost-basis loop with `s.transaction.post_date <= as_of`. The
two cost-basis paths agree by construction today, but a future
caller adding `_market_value(today=None)` would silently include
future-dated splits in cost basis while the surrounding report's
quantity loop has already excluded them.

**Classification:** NEEDS_VERIFICATION — latent inconsistency that
no current caller triggers. Worth tightening to require `today`
when `with_cost_fallback=True` — fail loud rather than silently
diverge.

---

### B-4. `_account_short_guid_map` includes template guids in disambiguation; `list_accounts` does not

**Tools:** `list_accounts` output vs `_resolve_account` ambiguity
calculations.

**Code:**

- `book/_base.py:1048` (`_account_short_guid_map`):
  ```
  guids = [a.guid for a in book.accounts]
  ```
  Includes everything in `book.accounts` (templates included).

- `book/core.py:2275-2278` (`list_accounts`): skips template guids
  when building the response.

**Scenario:** Two accounts share an 8-char prefix; one is real, one is
a template. `_account_short_guid_map` returns a longer
(disambiguated) prefix for both because it sees both. `list_accounts`
emits a longer prefix than necessary because the disambiguation
includes a template that the user can't see. Not a correctness bug,
but a wire-cost inefficiency that also feeds A-2 (template guids
become resolvable via `%short`).

**Classification:** NEEDS_VERIFICATION; mostly a wire-cost issue
unless A-2 is fixed by removing templates from the prefix map.

---

## C. VERIFIED to agree

### C-1. `balance_sheet` net worth identity vs `get_book_summary` vs `net_worth(today)`

`balance_sheet.assets.total - balance_sheet.liabilities.total ==
get_book_summary.net_worth == net_worth(end_date=today).net_worth`
holds by construction because:

- All three use `_split_in_default_currency` (or the equivalent
  `_market_value` for `get_book_summary`'s per-account display,
  which converges on the same `_rates_as_of` map).
- All three use the same type sets: `_ASSET_TYPES` (reporting.py:45)
  is identical to `_NW_ASSET_TYPES` (core.py:368), and similarly
  for liabilities. Both include RECEIVABLE on the asset side and
  PAYABLE on the liability side.
- `get_book_summary` and `_compute_net_worth_at` (for `as_of >= today`)
  use `_rates_as_of(book)` (no date filter); `net_worth(today)` via
  `_query_filtered_splits` also gets the same rates via
  `_account_conversion_factors(book)` → `_rates_as_of(book)` (no
  filter, line 188 in `_currency.py`).

Confirmed agreement is fragile against (A-8) — if
`_query_filtered_splits` ever needs the template filter, `net_worth`
would need it too. Today: agreement holds because templates have no
splits.

### C-2. `get_book_summary` per-account asset display uses same `_rates_as_of(book)` as `balance_sheet`'s per-account display

Both go through `_rates_as_of(book)` with no date filter. The
canonical disagreement from the predecessor letters (March-31 vs
April-30 cross-tool gap) was fixed by this convergence; the fix
holds in current code.

### C-3. `get_invoice` and `get_outstanding_invoices` resolve invoice owner the same way (post-PR #88)

Both route through `_find_invoice_owner_by_guid` which chases
`owner_type=3` (Job) through to the underlying customer/vendor.
The original 4.7-pre-compaction note about jobs-not-chased no
longer applies.

### C-4. `_split_in_default_currency` and `_market_value` share `_rates_as_of` source

Same map (`_rates_as_of(book)` with no upper bound). The
account-level vs split-level wrappers differ in API but draw from
the same rate dictionary, eliminating one prior class of cross-
report drift.

---

## Summary

| # | Severity | Cross-tool pair | Status |
|---|----------|----------------|--------|
| A-1 | High | `list_commodities` vs `get_latest_price`/`get_book_summary`/`balance_sheet` | CONFIRMED |
| A-2 | High | `get_account("%short")` vs `get_account("path")` for templates | CONFIRMED |
| A-3 | High | `get_book_summary` budget headline vs `get_budget_report` | CONFIRMED |
| A-4 | Medium | `get_book_summary` recon backlog vs `get_unreconciled_splits` | CONFIRMED |
| A-5 | High | `calculate_lot_gain` default price vs `get_latest_price` | CONFIRMED |
| A-6 | Low | Voided-split UX (both tools agree but probably wrong) | UX issue |
| A-7 | Low | `_runway_metrics` rates-as-of-today vs dashboard latest | CONFIRMED |
| A-8 | Latent | `_query_filtered_splits` skips template filter | architectural |
| B-1 | Medium | `get_outstanding_invoices` amount_due in invoice ccy | NEEDS_VERIFICATION |
| B-2 | Medium | `cash_flow` vs `spending_by_category`/`income_by_source` | design-by-doc |
| B-3 | Latent | `_market_value` cost-basis fallback semantics | NEEDS_VERIFICATION |
| B-4 | Low | Short-guid map includes templates | inefficiency |

Five **CONFIRMED disagreements** (A-1, A-2, A-3, A-4, A-5) directly
affect user-visible numbers and would surface in a bookkeeper
review pass that compared two tools' outputs for the same entity.

A-1, A-3, A-5 share the same root cause (missing `_is_market_price`
filter or missing rollup). They're each one-line fixes routed
through existing shared helpers.

A-2 is the most architecturally interesting: `_resolve_account`'s
three-path resolution silently disagrees on template visibility.
Either `_resolve_account` adds a template filter on all three
paths or `_account_short_guid_map` excludes templates from
disambiguation.

A-4 is the trickiest to fix because both numbers are arguably
correct for different questions. A response field naming or a
docstring clarification would help; making them numerically agree
would require choosing one semantic.
