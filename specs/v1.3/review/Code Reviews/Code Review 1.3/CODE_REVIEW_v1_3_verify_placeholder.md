# Verification: piecash `type='transaction'` placeholder leak

Adversarial verifier pass over four claims about whether helpers
walking `book.prices` / `session.query(Price)` skip piecash's
auto-created `type='transaction'` placeholder rows via the
`_is_market_price` helper.

Background: piecash auto-creates Price rows with `type='transaction'`
on every cross-currency transaction to record that one transaction's
effective rate. Helpers that pick a "latest price" for valuation must
skip them, or those bookkeeping artifacts shadow user-supplied market
quotes. The canonical filter lives at
`book/_currency.py:51` (`_is_market_price`).

---

## Claim P-1 — `list_commodities` leaks placeholders

### Verdict
**CONFIRMED.** Real bug.

### Reasoning
`book/investments.py:55-59`:

```python
for p in book.prices:
    key = f"{p.commodity.namespace}:{p.commodity.mnemonic}"
    p_date = _to_date(p.date)
    if key not in latest_prices or p_date > latest_prices[key][0]:
        latest_prices[key] = (p_date, p)
```

The loop selects the latest-dated Price per `(namespace, mnemonic)`
key. No `_is_market_price(p)` filter. There is no `type` field on the
emitted entry either (line 75-79 emits `value`, `currency`, `date`
only), so the caller cannot tell that the "latest price" is an
effective-rate placeholder rather than a market quote.

This is the *exact* anti-pattern `_is_market_price` was centralized
to prevent — and its module docstring at `_currency.py:62-67`
explicitly lists `list_commodities` as a sibling of the call sites
that need the filter. The cousin function `get_latest_price` at
`investments.py:516-517` *does* apply the filter; the comment there
(lines 506-515) reads as if written about exactly this divergence.

### Concrete reproduction
1. Book default currency USD. Commodity `FUND:VTSAX` exists.
2. User writes a manual VTSAX price: `2026-04-30, 273.43 USD,
   source="yfinance", type="last"`.
3. User posts an EUR-denominated invoice into a VTSAX-quoted account
   (or any cross-currency transaction involving VTSAX). piecash
   auto-creates a Price row dated `2026-05-15` with
   `type="transaction"`, source `"user:split-register"`, value =
   effective rate from that one txn (e.g., `1.07` if the txn
   denominated VTSAX as a currency leg with a 1.07 effective rate).
4. `list_commodities` is called.

Expected: latest price = `273.43 USD @ 2026-04-30`.
Actual: latest price = `1.07 USD @ 2026-05-15` (the placeholder
shadows the manual quote because `2026-05-15 > 2026-04-30`).

The user sees a wildly wrong "latest price" in the orientation
listing. The fix is one line: `if not _is_market_price(p): continue`
before the date comparison.

---

## Claim P-2 — `calculate_lot_gain` picks placeholders and wrong-currency prices

### Verdict
**CONFIRMED.** Real bug, two filter gaps.

### Reasoning
`book/investments.py:941-956`:

```python
commodity = lot.account.commodity
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

Two missing filters:
1. **No `_is_market_price` filter.** Same `type='transaction'`
   placeholder shadowing as P-1.
2. **No currency filter.** The lot account's commodity might be
   priced in multiple currencies in `book.prices` (e.g., VTSAX
   quoted in both USD and EUR for portfolios that hold the same
   security across two reporting books, or — more commonly — a
   foreign stock with both home-market and USD ADR quotes). The
   loop picks whichever is newest regardless of currency, then on
   line 967 multiplies `price * shares_to_sell` and treats the
   result as cost-basis-comparable. If the price was in EUR and
   the cost basis is in USD, the gain calculation is a unit
   mismatch.

The cousin function `get_latest_price` (lines 487-532) applies
BOTH filters: line 504 filters by currency, line 516 filters via
`_is_market_price`. The two functions answer the same question
("what's the latest price for this commodity?") but disagree on
which prices are eligible.

### Concrete reproduction
Scenario A (placeholder shadow):
1. Lot in account `Investments:VTSAX`, currency USD, 100 shares @
   $250 cost basis.
2. Manual price on file: `2026-04-30, 273.43 USD, type="last"`.
3. A cross-currency transaction touching VTSAX creates an
   auto-placeholder dated `2026-05-15, 1.07 USD,
   type="transaction"`.
4. `calculate_lot_gain(lot_guid)` with no `sale_price` override.

Expected: proceeds = `100 × 273.43 = 27,343 USD`, gain = `2,343 USD`.
Actual: proceeds = `100 × 1.07 = 107 USD`, gain = `-24,893 USD` —
a 25k loss on a real 2.3k gain.

Scenario B (currency confusion):
1. Lot in account `Investments:Foreign:NESN`, account currency
   CHF. Book default USD.
2. User has both a CHF quote (`2026-04-30, 95.50 CHF`) and a USD
   ADR quote (`2026-05-01, 110.00 USD`) on the same commodity.
3. `calculate_lot_gain` picks the USD price because it's newer.
4. Cost basis was recorded in CHF.

Expected: gain computed in CHF, comparable to CHF cost basis.
Actual: gain computed by mixing CHF cost basis with a USD
proceeds figure — output is numerically meaningless.

The fix is to apply both filters, exactly mirroring
`get_latest_price`. Default to the lot's account commodity's
"home" currency (the book default if unspecified by the user),
and skip placeholders.

---

## Claim P-3 — `_collect_warnings` stale-price `in_use` leaks placeholders

### Verdict
**REFUTED as stated, but a related bug exists.**

### Reasoning
The claim says "slips past the 'no market price on file' warning."
That specific failure mode doesn't occur. Walking through
`book/core.py:918-953` for a commodity that has ONLY a
`type='transaction'` placeholder (and no real quote):

- Line 919: `in_use.add(p.commodity.guid)` — added.
- Line 920-921: `if not _is_market_price(p): continue` — skipped
  before reaching `by_commodity_latest`.
- Line 941: `latest = by_commodity_latest.get(commodity.guid)` →
  `None` (because line 920 filtered the placeholder).
- Line 942: `if latest is None:` → emits
  `"Stale price: <mnemonic> no price on file"`.

So the warning DOES fire. The claim's stated failure ("slips
past the warning") is not what the code does.

**However**, the placeholder DOES pollute the `in_use` set in a
different, real way:

A commodity that has *only* a `type='transaction'` placeholder
and *no holding account* (e.g., the FX cross-currency placeholder
sat between two currency commodities, where the "from"
commodity isn't held by any non-currency account) will now
appear in `in_use` purely because of the placeholder. That
commodity will then surface a `"no price on file"` warning even
though there's no real asset position to report on.

This is a less severe bug than P-2 / P-1 — it produces a spurious
warning rather than wrong math — but it's the same shape of leak.
The minimal fix is to move line 919 below the `_is_market_price`
guard. The `in_use` signal was originally extracted from price
presence on the theory that "priced = in use even without an
account holding," which is true for *market* prices but not for
auto-placeholders.

### Concrete reproduction
1. Book default USD. Two currency commodities exist: USD and JPY.
2. No account holds JPY.
3. A single cross-currency txn between two USD accounts and a
   JPY-denominated invoice creates a `JPY/USD type='transaction'`
   placeholder.
4. `get_book_summary` runs `_collect_warnings`.

Expected: no stale-price warning about JPY (no account holds it;
no real quote was ever needed).
Actual: warning "Stale price: JPY no price on file" surfaces in
the orientation summary.

---

## Claim P-4 — anti-pattern hunter disagreement

### Verdict
**The anti-pattern hunter undercounted. P-1, P-2, and the
modified P-3 are all real.** P-1 was their single hit; P-2 and
P-3 use different code shapes (indexed `session.query(Price)` for
P-2; `_is_market_price` filter present but applied AFTER `in_use`
is updated for P-3) and slipped a naive grep pattern.

### Reasoning
I enumerated every `book.prices` iteration and every
`session.query(Price)` call in the codebase:

| Location | Shape | `_is_market_price`? | Currency filter? |
|---|---|---|---|
| `investments.py:55` `list_commodities` | `for p in book.prices` | **NO** | N/A (display) |
| `investments.py:214` `create_price` | `session.query(Price).filter_by(commodity, currency, source)` | N/A (collision check) | yes |
| `investments.py:304` `delete_price` | `session.query(Price).filter_by(commodity, [source])` | N/A (deletion target) | yes (via source) |
| `investments.py:398` `get_prices` | `session.query(Price).filter_by(commodity)` | N/A (listing) | optional |
| `investments.py:498` `get_latest_price` | `session.query(Price).filter_by(commodity)` then loop | **YES** (line 516) | yes (line 504) |
| `investments.py:941` `calculate_lot_gain` | `session.query(Price).filter_by(commodity)` then loop | **NO** | **NO** |
| `core.py:918` `_collect_warnings` | `for p in book.prices` | partial (after `in_use.add`) | N/A |
| `_currency.py:111` `_indexed_prices` | `session.query(Price).filter()` | yes (when `market_only=True`, default) | optional |
| `_currency.py:153` `_rates_as_of` | `for p in book.prices` | YES (line 156) | YES (line 154) |
| `_currency.py:311` `_find_exchange_rate` | `for p in book.prices` | YES (line 312) | yes (by commodity match) |

A grep for `book.prices` without `_is_market_price` nearby would
have caught only P-1 (`investments.py:55`). The anti-pattern
hunter reported exactly that and stopped, because:

- P-2 uses a `session.query(Price)` indexed lookup, not a
  `book.prices` iteration. A `book.prices` grep misses it. A
  broader grep on `session.query(Price)` returns six hits, four
  of which are legitimately by-design and only one (P-2) is the
  same hazard as P-1.
- P-3's loop *does* contain `_is_market_price(p)`. A grep for
  "`book.prices` walks without the filter" returns clean. The
  bug is the placement: `in_use.add` runs one line *above* the
  filter, so the set picks up placeholder commodities. This is
  invisible to single-line pattern matching; you have to read
  the small-block control flow.

The original "agreement" and "multicurrency" agents who flagged
P-2 and P-3 read the code. The anti-pattern hunter ran a grep.
The agreement agents are right; the anti-pattern hunter is
right about its single hit but wrong about its scope.

### Resolution
P-1, P-2 are confirmed and should land as a fix bundle. P-3 is
real but with a different fix than the claim text suggested
(move `in_use.add` below `_is_market_price` guard, not "add the
filter where there was none"). All three sit in the same
1-line-per-site pattern of repair.
