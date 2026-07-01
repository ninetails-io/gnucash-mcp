# Price Update Tool Specification
## GnuCash MCP Server — Post-v1.3 Feature

**Status:** Draft for review
**Target:** v1.3.1 or v1.4 (separate PR after v1.3.0 release)
**Prerequisites:**
- v1.3.0 shipped (multi-currency correctness sweep, dashboard alignment)
- piecash `Price` model + `create_price` tool already in place

---

## Executive Summary

Close the loop on `get_book_summary`'s stale-price warning. Today the
LLM sees `⚠ Stale price: GBP last updated 177 days ago` and has
nowhere to go — no tool can act on it. The user must leave the
session, look up the rate manually, return, and call `create_price`.

This spec proposes `update_prices`: a single tool that fetches
current quotes from yfinance (and a second backend for FX-only
reliability) and writes them as `Price` records. Two modes:
explicit `tickers=[...]` batch, and auto-discovery via
`stale_only=true`.

Implementation expectation: **~500–700 LOC across 4 commits**, plus
yfinance as a new dependency.

---

## Background: Why This Matters

The MCP server's stated goal is "do almost anything you could do
from the GnuCash app." The app's Price Editor lets you pull quotes
from Yahoo (and Finance::Quote backends). The MCP server until now
has not — every price is a manual `create_price` call.

For three user surfaces this is acute:

1. **Investor / portfolio.** `balance_sheet` and `net_worth` value
   STOCK/MUTUAL holdings at the latest price. Stale prices →
   stale net worth. The bookkeeper-loop pattern catches this
   eventually; an LLM that could refresh prices on demand catches
   it immediately.

2. **Multi-currency bookkeeping.** Every cross-currency transaction
   needs an FX rate at post time. v1.2.1's FX gain/loss work
   recognizes drift between post-date and pay-date rates. None of
   that works without recent FX prices on file.

3. **The dashboard's stale-price warning.** Today the warning is a
   dead end. With `update_prices`, it becomes the start of an
   action cycle: warning surfaces → LLM calls tool → prices
   updated → warning clears next call.

---

## Tool Surface

Single tool, two modes that compose:

```python
def update_prices(
    tickers: list[str] | None = None,
    stale_only: bool = False,
    stale_days: int = 30,
    source: str = "yfinance",
    auto_fetch_fx_leg: bool = True,
) -> str:
    """Fetch current quotes and write them as Price records.

    Two invocation modes:

    - **Explicit:** ``tickers=["AAPL", "VTSAX", "EUR"]`` — fetch
      and update exactly these. Mnemonic-driven; the tool resolves
      each to a yfinance ticker via the mapping rules below.
    - **Stale-only:** ``stale_only=True`` — find every commodity
      in the book whose latest market price is older than
      ``stale_days`` (or missing entirely) and update each. Use
      this as the natural response to the dashboard's stale-price
      warning.

    The two modes can be combined: ``tickers=["AAPL"], stale_only=True``
    refreshes AAPL plus anything else that's stale.

    Args:
        tickers: Explicit list of commodity mnemonics to refresh.
            Resolves to yfinance tickers via the mapping rules in
            ``_resolve_yfinance_ticker``.
        stale_only: When True, also include every commodity in the
            book whose latest market price is older than
            ``stale_days``.
        stale_days: Threshold for "stale" in the auto-discovery
            path. Defaults to 30; matches the dashboard warning
            threshold so the action and the signal agree.
        source: Backend selector. ``"yfinance"`` for stocks /
            mutual funds / crypto / most FX pairs. Forward seam
            for ``"frankfurter"`` (ECB-sourced FX only, more
            reliable than yfinance for currency rates) in a
            future commit.
        auto_fetch_fx_leg: When True (default), any ticker that
            returns a price in a non-default currency triggers a
            second fetch for the FX rate from that currency to
            the book default. Without this, foreign-exchange
            stock prices would be unusable for default-currency
            valuation.

    Returns:
        JSON summary of what happened. Three buckets:
          - ``updated``: list of ``{mnemonic, ticker, price,
            currency, date}`` for successful writes
          - ``failed``: list of ``{mnemonic, reason}`` for
            tickers that couldn't be resolved or fetched
          - ``skipped``: list of ``{mnemonic, reason}`` for
            commodities that didn't need updating (already
            fresh, no rate change vs last fetch, etc.)
    """
```

---

## Design Decisions

### D1: yfinance ticker mapping

Book commodities are identified by mnemonic (`AAPL`, `EUR`,
`VTSAX`, `600519`). yfinance tickers follow Yahoo's exchange-suffix
convention. The mapping isn't 1:1 — needs a resolver.

**Resolution order** in `_resolve_yfinance_ticker(commodity)`:

1. **Per-commodity slot override.** If the commodity has a
   `gnc-mcp/yfinance-ticker` slot set, use that verbatim. Lets
   the user record `0700.HK` for Tencent when the book mnemonic
   is just `0700`.

2. **Currency commodity → FX pair.** ISO 4217 currency codes
   (`EUR`, `CAD`, `JPY`, etc., and any commodity whose namespace
   is `CURRENCY` or whose mnemonic matches `/^[A-Z]{3}$/`) map to
   `{mnemonic}{default_mnemonic}=X`. So on a USD book, `EUR` →
   `EURUSD=X`. On a CNY book, `EUR` → `EURCNY=X`.

3. **Crypto-shaped mnemonic.** Recognized crypto mnemonics
   (`BTC`, `ETH`, `SOL`, etc. — short allowlist) map to
   `{mnemonic}-USD` always. Crypto is USD-priced everywhere on
   yfinance regardless of book default.

4. **Stock / mutual fund.** Everything else: use the mnemonic
   verbatim. `AAPL` → `AAPL`, `VTSAX` → `VTSAX`. If a foreign
   stock needs an exchange suffix (`VOD.L`), the user sets it via
   the slot override (rule 1).

A failed resolution is a `failed` bucket entry, not a tool error.

### D2: FX auto-leg fetch

A non-US-listed stock returns its price in a foreign currency.
`7203.T` returns JPY. `VOD.L` returns GBP. Recording
`Price(commodity=Toyota, currency=JPY, value=2500)` is correct,
but downstream `balance_sheet` math needs JPY→USD too. Without
the FX leg, Toyota positions value at zero (no rate, cost-basis
fallback to historical USD purchase value).

When `auto_fetch_fx_leg=True` (default):
- Detect the response currency from yfinance metadata.
- If response currency ≠ book default and no fresh FX rate
  exists, fetch and store it.
- Single tool call → both prices written.
- The FX leg appears in the response's `updated` list as a
  separate entry with `mnemonic="JPY"`, `ticker="JPYUSD=X"`.

This is the "we don't have the resources to take shortcuts"
default. Off-switching is available (`auto_fetch_fx_leg=False`)
for the rare case where the caller wants just the stock leg.

### D3: Source backends

v1 ships **yfinance only.** The `source=` parameter exists in the
schema as a forward seam.

**Why not Frankfurter for FX in v1:** Frankfurter is more reliable
for daily FX closes (ECB-sourced, no scraping), but adding a
second backend doubles the integration surface in the first cut.
v1 keeps it to one. v1.x or v1.4 adds `source="frankfurter"` as
the recommended FX path; the user gets to choose.

**Why not Alpha Vantage / Twelve Data / Polygon:** API keys
required. yfinance is keyless. For a personal-finance tool used
in MCP, keyless is the right default — the user shouldn't have
to register, configure environment variables, or rotate keys to
update their portfolio.

### D4: Idempotency on same-day re-runs

GnuCash supports multiple prices per `(commodity, currency, date)`
tuple via the `source` field. The bookkeeper might run
`update_prices` Monday morning and again Monday afternoon after
an intraday move.

**Behavior:** upsert keyed on `(commodity_guid, currency_guid,
date, source)`. Same source on the same date → update the value.
Different source on the same date → insert (lets `user:price`
manual entries coexist with `user:yfinance` auto-fetched ones).

The `source` field for tool-fetched prices is `user:yfinance`
(matching the convention already used in Alex's book's
yfinance-sourced rows). Frankfurter, when added, will be
`user:frankfurter`.

### D5: Error handling

Network is fallible. yfinance is known to rate-limit, return
empty payloads on transient errors, and silently change endpoints.

**Per-ticker error isolation.** One failed ticker doesn't fail
the call. Failures land in the `failed` bucket with a reason
string (rate-limited, ticker-not-found, response-malformed,
timeout). The `updated` bucket still carries everything that
succeeded.

**Timeout.** 5-second per-ticker HTTP timeout. Batching
(yfinance's `download(["AAPL", "MSFT"])`) shares the timeout;
individual tickers within a batch don't multiply it.

**Retry posture.** No automatic retry inside the tool. The LLM
sees the failure list and can decide whether to call again
(`update_prices(tickers=["VOD.L"])` for one-off retry) or wait.
Retry-with-backoff would add complexity for marginal benefit at
this surface.

### D6: Network as new dependency posture

Every existing tool reads/writes a local SQLite file. This is the
first outbound-network tool. Worth flagging:

- **No outbound network without an explicit tool call.** Auto-
  fetch on startup, periodic refresh in the background, etc.,
  are all out of scope. The tool fires only when invoked.
- **No telemetry.** yfinance queries are routed through the
  yfinance library which talks to Yahoo's public endpoints. No
  third-party logging or analytics from this tool.
- **CHANGELOG entry calls it out** so users with offline /
  sandboxed setups know that v1.x adds network capability.
- **Add `yfinance` to `pyproject.toml` dependencies.** Mark as
  optional via an extras group if the project prefers minimal
  hard dependencies. (Decision: hard dep for v1, since the tool
  is non-functional without it.)

### D7: Module placement

Lives in **`portfolio`** module (currently houses commodities +
prices CRUD tools). The investor group (`tax_lots + portfolio`)
already aliases together, so this tool is included by default
for the investor persona.

Tool registration: `tools/portfolio.py`.
Book implementation: a new `book/quotes.py` mixin (separate from
the price-CRUD code in `book/portfolio.py` so the network logic
doesn't entangle with the existing pure-SQLite code).

---

## Testing Strategy

yfinance is a network dependency. Hermetic tests are non-
negotiable.

**Three layers:**

1. **Resolver tests** — `_resolve_yfinance_ticker` is pure (no
   network). Test exhaustively: currency on USD book, currency
   on CNY book, crypto on any book, slot override, unknown
   commodity. ~10 tests.

2. **Mocked-yfinance tests** — patch the yfinance client with a
   fake that returns canned `(price, currency, date)` tuples
   per ticker. Test the full tool path: explicit batch, stale-
   only auto-discovery, FX auto-leg trigger, partial-failure
   aggregation, upsert vs insert. ~15 tests.

3. **One live smoke test** — gated behind
   `GNUCASH_MCP_LIVE_NETWORK_TESTS=1` env var so CI doesn't hit
   the network. Verifies the actual yfinance backend responds to
   `AAPL` and writes a price record. Skipped by default; the
   developer runs it manually when touching the network code.

---

## Implementation Plan

Four commits on `feat/update-prices` (branched from develop after
v1.3.0 merges):

**Commit 1: `feat(quotes): commodity → yfinance ticker resolver`**
- `_resolve_yfinance_ticker` with the four resolution rules
- Pure function, no network
- Resolver tests pass

**Commit 2: `feat(quotes): yfinance backend + update_prices tool`**
- Add `yfinance` to dependencies
- `book/quotes.py` mixin with `update_prices` method
- `tools/portfolio.py` registers the tool
- Mocked-yfinance tests pass
- Explicit-batch mode works; stale-only deferred to commit 3

**Commit 3: `feat(quotes): stale-only auto-discovery + FX auto-leg`**
- Stale-detection scan (re-use existing `_rates_as_of` +
  threshold check)
- FX auto-leg fetch when response currency ≠ default
- Combined-mode tests pass

**Commit 4: `feat(quotes): live smoke test + CHANGELOG`**
- Gated live test for the yfinance path
- CHANGELOG entry for v1.x
- Document the new network posture

Followed by: open PR develop → develop (yes, into develop),
bookkeeper review, Copilot review, merge.

---

## Open Questions

These need a decision before commit 1. Most are
"recommended default ✓" with the alternative noted.

- [ ] **Hard dependency vs optional extra?** Default: hard. (Tool
      is non-functional without yfinance; defer the optional-
      extras complexity.)
- [ ] **Auto-fetch FX leg default ON or OFF?** Default: ON. ("No
      shortcuts" — closes the loop for foreign stocks without
      requiring two tool calls.)
- [ ] **Stale threshold default?** 30 days. Should agree with
      `_STALE_PRICE_DAYS` in `book/core.py`; refactor to a shared
      constant if it isn't already.
- [ ] **Per-commodity slot override key name?** Proposed:
      `gnc-mcp/yfinance-ticker`. Conforms to the
      "namespaced for tool-specific state" convention in
      CLAUDE.md's slot-key guidance.
- [ ] **What if yfinance is rate-limited mid-batch?** Default:
      fail open — what was fetched before the rate-limit lands;
      what wasn't returns in the failed bucket. Could batch
      smaller (5 tickers at a time) to reduce blast radius.
- [ ] **Display future-dated forecast prices?** yfinance returns
      a single "current" quote with today's date. The existing
      future-dated price convention (bookkeeper deliberately
      writes forward forecasts) is unaffected because tool
      writes are dated today.
- [ ] **Should the tool delete stale prices it replaced?** No.
      Historical prices are valuable for trajectory
      reconstruction. The dashboard already uses the most
      recent price; older ones are harmless.

---

## Out of Scope (Not v1.x)

- **Auto-refresh on schedule.** No cron, no background job, no
  startup auto-fetch. Explicit tool calls only.
- **Frankfurter / second backend.** Forward seam only; ship
  yfinance-only v1.
- **Historical backfill.** Tool writes today's price. Historical
  backfill (e.g., 90 days of EUR/USD closes) is a separate
  feature with different design tradeoffs (rate-limit batching,
  yfinance's `history()` endpoint vs `download()`, source-field
  semantics for backfilled data).
- **Notification of meaningful price moves.** "AAPL down 5%
  today" is a different surface — the LLM can compute that from
  successive `update_prices` calls.
- **Custom ticker resolvers via plugin.** Slot override handles
  the long tail; a full plugin system is overengineering for
  this surface.
