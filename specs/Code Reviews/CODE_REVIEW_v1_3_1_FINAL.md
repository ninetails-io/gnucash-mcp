# Code Review — v1.3.1 Pre-Release (FINAL / Synthesis)

## Executive summary

This adversarial pass surfaced **15 confirmed defects** (1 unverified LOW excluded
from the confirmed count below but listed). The dominant theme is a single
root-cause bug class: **`get_book_summary` and its sub-surfaces value
foreign-currency *liabilities* at raw account-commodity quantity, while the
standalone `balance_sheet` / `net_worth` tools FX-convert them** — producing
cross-tool money disagreement on the LLM's primary orientation surface for any
multi-currency book holding a foreign-denominated credit card or loan. Three
verified findings collapse to this one root cause; `debt_payoff_plan` and the
scheduled-transaction upcoming total carry the same "sum raw quantity across
commodities" defect in other code paths. Separately, the backup retention
subsystem (`prune_backups` and the unattended `_prune_auto_stages`) **deletes via
a redacted basename-only path under `GNUCASH_REDACT_PATHS=1`**, silently failing
to reclaim disk (or deleting a CWD same-named file). This is the same FX-aggregation
bug class PR #92 hunted on Jun 3; the liability arm was missed because the Jun 3
review examined `_compute_net_worth_at` only for asset conversion and historical
rates and treated it as correct — see REGRESSED tags.

## Release recommendation: **ship-with-fixes**

Justification: No CRITICAL defects; nothing corrupts the live book or loses user
data on a default configuration. But two HIGH clusters violate explicitly-defended
invariants on first-class supported configurations: (1) the cross-tool
price-agreement invariant — the bookkeeper's primary correctness check — is broken
for multi-currency liabilities on the dashboard the LLM calls first; (2) backup
retention silently stops working under a documented opt-in privacy flag. Both are
of the exact bug class the project treats as release-significant. The four HIGH
items (which dedup to two root causes) should be fixed and locked with regression
tests before v1.3.1 ships. MEDIUM/LOW items can ride the same branch or defer.

---

## CRITICAL

None.

---

## HIGH

### H1. `get_book_summary` does not FX-convert foreign-currency liabilities (one root cause, two surfaces) — REGRESSED (same class as PR #92 / Jun 3 FX sweep)

Four verified findings collapse to a single root cause: in `book/core.py`, the
asset / RECEIVABLE / PAYABLE branches FX-convert via `_market_value(...)` (resp.
`rates.get()` in the trajectory helper), but the **CREDIT and LIABILITY branches
append raw `-balance`** (account-commodity `split.quantity`) with no conversion.
The standalone `balance_sheet` / `net_worth` tools convert *every* type — including
`{LIABILITY, CREDIT, PAYABLE}` — via `_split_in_default_currency`. So a
foreign-denominated credit card or loan yields different Liabilities totals and a
different net worth between the dashboard and the reports.

- **`book/core.py:571-574`** — `_compute_net_worth_at` (net-worth trajectory):
  liability branch is unconditional `liabilities_total += -balance`; asset branch
  at 551-570 does `balance * rate` with cost-basis fallback. Drives the 5-point
  trajectory in `get_book_summary`; diverges from `reporting.py` `net_worth`
  (`net_worth` at reporting.py:721 routes all nw_types through
  `_split_in_default_currency`).
- **`book/core.py:1978-1989`** — `_collect_summary_balance_sheet` (dashboard
  balance-sheet section): `data.credit_cards.append((leaf, -balance))` and
  `loan_accts/other_liab_accts.append((leaf, neg_balance))` raw, while assets
  (1970), RECEIVABLE (1993), PAYABLE (2003) all call `_market_value(...)`.
  `liabilities_total` (2045-2048) sums the unconverted values. Diverges from
  `reporting.py` `balance_sheet` (line 478 converts all rows via
  `_split_in_default_currency`).

**Why it matters:** Violates the cross-tool price-agreement invariant — the
bookkeeper's primary correctness check — on the orientation surface the LLM calls
first. Wrong net worth shown silently. Exactly the FX-aggregation class fixed in
PR #92; the Jun 3 review inspected `_compute_net_worth_at` for asset/historical-rate
correctness and treated it as correct, leaving the liability arm uncaught.

**Fix:** Route CREDIT and LIABILITY balances through the same conversion as assets
in both methods — `if account.commodity == default_currency` add `-balance` raw,
else `rates.get(account.commodity.guid)` multiply with the `split.value`
cost-basis fallback (mirror `_market_value` / lines 554-570). Add a
foreign-currency-liability case to `TestCrossToolPriceAgreement` so the two tools
are locked into agreement.

### H2. Backup retention deletes via redacted basename-only path under `GNUCASH_REDACT_PATHS=1` (two sites)

`list_backups` stores each entry's `path` as `redact_paths(str(path))`
(`book/backup.py:529`), which under `GNUCASH_REDACT_PATHS=1` collapses the absolute
path to **basename only** (`logging_config.py:182-205`). Both pruners then call
`Path(entry["path"]).unlink()` on that bare basename, which resolves against the
process CWD, not the backups dir. The `OSError` (typically `FileNotFoundError`) is
swallowed, so the prune reports success while deleting nothing — or deletes an
unrelated same-named file in CWD. There is no un-redacted path field to fall back on.

- **`book/backup.py:679-686`** — `prune_backups` (operator-driven; `dry_run`
  defaults True, so observable but still broken on `dry_run=False`).
- **`book/backup.py:831-835`** — `_prune_auto_stages`, invoked unattended from
  `_maybe_auto_backup` on the **first write of every process**
  (`logging_config.py:2171-2177` → `backup.py:753`). Silent: retention never trims,
  `keep_last_n` is violated, auto-stage backups grow without bound until disk fills.

**Why it matters:** Silently defeats the retention half of the backup subsystem —
the project's primary data-loss seatbelt — under a documented, supported opt-in
flag (MP-4). The auto path is worst: unattended, invisible until disk pressure.
Not CRITICAL because it doesn't touch the live book and the wrong-file delete needs
a CWD name collision; but a safety subsystem silently broken under a first-class
config is HIGH.

**Fix:** Never derive the deletion target from redaction-mutated output. Either add
an internal `_list_backups_raw()` returning un-redacted absolute `Path`s for
internal callers (redact only at the MCP tool boundary), or reconstruct
`self._backups_dir() / Path(entry["path"]).name` before `unlink()`. Apply at both
sites.

---

## MEDIUM

### M1. `debt_payoff_plan` sums raw `split.quantity` for liability balances — no FX conversion
**`book/reporting.py:1205-1208`.** `balance += split.quantity` then negate, with no
`_split_in_default_currency` — the one method in the reporting layer that bypasses
the FX helper used everywhere else (balance_sheet 478, net_worth 721, cash_flow 969).
A foreign-currency debt is treated as if its foreign units were default-currency:
amortization (`balance * monthly_rate ...` at 1260), `total_balance` (1315),
`total_paid` (1318), and the budget-feasibility gate (`budget < total_minimums`,
1309) all run on mixed/wrong units. *Note: the avalanche/snowball kill-order sorts
by APR (1330/1338), which is currency-independent, so payoff sequence is unaffected
— the harm is mis-valued balances and totals, not mis-ordering.*
**Fix:** Multiply each balance by `_rates_as_of(book, today).get(account.commodity.guid)`
(cost-basis fallback) before use; or bucket per currency and refuse to cross-sum.

### M2. `vendor_spending_report` silently sums mixed-currency figures when no FX rate is on file
**`book/business.py:7958-7973`.** Conversion is gated `if rate is not None:` with no
`else` — when `_rates_as_of` omits a bill's currency (no resolvable price), the raw
foreign `total/paid/outstanding` flow unconverted into the per-vendor and grand
totals (7982-7984), and the returned totals block (8001-8009) carries no warning.
Same silent-cross-currency-summation class PR #92 fixed elsewhere.
**Fix:** On a `None` rate, exclude the bill from cross-currency grand totals and
surface a per-currency residual / structured note naming the unconverted currency.

### M3. `_rates_as_of` chain pass uses raw `as_of` instead of the future-folded anchor
**`book/_currency.py:302-309` (vs direct pass 276/286).** The direct pass folds a
now/future `as_of` to `date.max` (`_anchor_for_as_of`, line 225) so future-dated
forecast prices are included (documented convention). The issue-#94 chain pass
passes the unfolded `as_of` into `_market_rate_to_default`, whose legs prefer
before-`as_of` prices (`_find_exchange_rate_aged` 749-794). A chained commodity
with a future-dated leg picks an older before-today rate while a directly-priced
commodity picks the forecast — inconsistent application of the same convention,
yielding subtly different valuations on the same report date.
**Fix:** Pass the folded `anchor` into the chain pass (or fold inside
`_market_rate_to_default`) so direct and chained commodities apply the identical
future-prices-included rule.

### M4. Scheduled-transaction upcoming total sums raw amounts across currencies, labels them in book default currency
**`book/scheduling.py:509-512`** (sum) **+ `book/core.py:2273-2277`** (render).
`_upcoming_within_days` sums positive split `amount`s across all in-window SXs with
no commodity tracking (splits-json carries no commodity field), and
`get_book_summary` renders it as a single `{default_currency} N`. A CNY-default
book with a USD-denominated scheduled bill adds USD+CNY and shows one "CNY N"
figure. Same wrong-money class fixed in other dashboard helpers in PR #92.
**Fix:** Bucket the upcoming total per currency (or only sum SXs whose currency
equals default), or store SX currency in splits-json for `_rates_as_of` conversion.
At minimum, suppress the single-figure render when the window spans >1 currency.

### M5. `update_account` rename bypasses MP-14 name validation (`:` / control-char / empty)
**`book/core.py:3950-3959`.** The rename branch checks only sibling-name conflict,
then `account.name = new_name` with no validation. `create_account` (3814-3827)
rejects empty/whitespace, `:` (path separator per MP-14), and control chars — the
rename path is an unguarded parallel entry point. `update_account(name='Assets:Bank',
new_name='Foo:Bar')` corrupts the account's fullname so path-based
`_find_account`/`_resolve_account` misparse it.
**Fix:** Extract the three `create_account` name checks into a shared
`_validate_account_name` helper and call it before assigning `account.name` in
`update_account`.

---

## LOW

### L1. Fuzzy-match helpers don't filter template accounts
**`book/business.py:442-448` (`_get_or_create_fx_account`) + `557-563`
(`_get_or_create_discount_account`).** Both iterate `book.accounts` filtering only on
type (INCOME/EXPENSE) + name-keyword substring, with no `_template_account_guids`
skip — unlike `debt_payoff_plan` (reporting.py:1175-1182). Deviation from the
"filter template accounts everywhere" invariant on a write path. Blast radius is
narrow: the server only ever creates BANK-typed template accounts, so the type
guard already excludes every server-created template; manifesting requires an
INCOME/EXPENSE template account created by native GnuCash, keyword-named, as the
sole candidate. **Fix:** Add `if account.guid in template_guids: continue` to both
loops (defense-in-depth, one line each).

### L2. `_verify_transaction_state` keys splits by account fullname — same-account collision
**`book/core.py:4240-4246`.** Two splits to the same account (legal; reachable via
`replace_splits`) collapse to one dict entry; the count guard passes when counts
match, and the overwritten split's value is never verified. Defense-in-depth
round-trip net only, not a primary write path — splits are built correctly
in-memory; this can only fail to *detect* a hypothetical silent partial-write in a
narrow case. **Fix:** Key by `(fullname, memo)` or multiset-match expected against
actual.

### L3. Raw `text()` UPDATE for taxtable refcount is unverified and invisible to the contract test
**`book/business.py:6852` (+ self-heal at 1044).** No `_verify_*` readback, and the
contract scanner (`tests/test_contract_integrity.py:483-485`) only matches
`Table.__table__.(insert|delete|update)()`, so `text()`-string DML escapes the
"every raw-SQL write is verified" guarantee entirely. *The claimed corruption harm
is refuted:* the `taxtable_guid` is read from the same entry rows being deleted, the
delete guard blocks deletion while refcount>0, `MAX(0, ...)` makes any no-op
harmless, and the stored `refcount` column has no in-process consumer (all logic
uses `_compute_taxtable_refcount` COUNT(*)). So this is a latent coverage-gap, not
an execution-manifesting defect. **Fix:** Broaden the contract-test scanner to also
match raw `text()` UPDATE/INSERT/DELETE so future `text()` DML can't escape; a
verify on the refcount UPDATE is optional given no consumer.

### L4. `vendor_spending_report` verbose totals emit unquantized FX products *(UNVERIFIED)*
**`book/business.py:7961-7963` (multiply) + `7988-7990`/`8002-8004` (str()).** In the
verbose path, FX-converted figures are `str()`-emitted without quantizing to
currency precision, exposing many fractional digits; the compact path masks it via
`_money` 2dp formatting. Not adversarially verified. **Fix:** Quantize to
`_commodity_quantum(default_currency)` immediately after the FX multiply so both
paths return currency-precise values.

---

## Scope & method

Cartography-then-Opus-lens-then-verify pass (33 agents, ~3.17M tokens, ~12.7 min):
each finding was produced by a
lensed sweep (fx / reports / state / security / math) and then independently
adversarially verified against the real source before inclusion (`is_real=true`),
except L4 which is an unverified LOW. **Code comments were treated as
non-evidence** — claims were settled from code structure and data flow alone
(this refuted the L3 corruption harm and the M2/M4 "intentional fallback"
rationalizations). Deduped against the Jun 3 review (`specs/Code Reviews/`,
believed fully fixed): H1 is tagged REGRESSED — it is the same FX-aggregation bug
class PR #92 fixed, and the Jun 3 `verify_fx` pass examined `_compute_net_worth_at`
for asset conversion and historical rates but treated the function as correct,
leaving the liability arm uncaught. Confirmed count: 15 (4 HIGH deduping to 2 root
causes, 5 MEDIUM, 4 LOW of which 3 verified + 1 unverified). No CRITICAL.
