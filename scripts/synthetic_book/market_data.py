"""Real historical market data for the synthetic-book generators.

This module is the shared, offline, deterministic source of truth for
security prices and FX rates used when building the Alex and Lin Wei
synthetic books. Real quotes are fetched once into a committed cache
(``market_data_cache.json``) so that generators run with no network
access and no extra runtime dependency.

Two data sources feed the cache, both fetched only on ``--refresh``:

* **Securities** — yfinance. ``yf.Ticker(t).history(...)`` per trading
  day; we keep the raw ``Close`` in the instrument's NATIVE currency
  (China A-shares in CNY, US listings in USD). The generators know the
  currency of each mnemonic.
* **FX** — frankfurter (ECB, free, no key) via stdlib ``urllib``. The
  timeseries endpoint returns BASE units per 1 FOREIGN unit, which is
  exactly the GnuCash Price convention (commodity=foreign,
  currency=base, value=base-per-foreign).

The lookup path used by generators (``MarketData.load`` + ``.security``
+ ``.fx``) depends on ONLY the stdlib and the cache JSON — yfinance is
imported lazily inside the refresh path. Prices are stored as plain
JSON numbers and parsed via ``Decimal(str(...))`` on load to avoid
float drift.

Usage::

    # Refresh the committed cache (requires yfinance + network):
    python scripts/synthetic_book/market_data.py --refresh

    # Print sample values incl. weekend forward-fill proof:
    python scripts/synthetic_book/market_data.py --selftest

    # In a generator:
    md = MarketData.load()
    px = md.security("600519", date(2025, 6, 2))   # native-currency close
    rate = md.fx("USD", "CNY", date(2025, 6, 2))   # CNY per USD
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

# --- Instrument registry (fixed, deterministic) ---------------------------

SECURITIES = {  # book mnemonic -> yfinance ticker
    "VTSAX": "VTSAX",
    "VBTLX": "VBTLX",
    "AAPL": "AAPL",
    "MSFT": "MSFT",
    "ETH": "ETH-USD",
    "600519": "600519.SS",
    "510300": "510300.SS",
    "300750": "300750.SZ",
    "159915": "159915.SZ",
}

FX_PAIRS = [  # (foreign, base) -> base per foreign
    ("EUR", "USD"),
    ("GBP", "USD"),
    ("CAD", "USD"),  # Alex (USD book)
    ("USD", "CNY"),
    ("EUR", "CNY"),
    ("HKD", "CNY"),  # Lin Wei (CNY book)
]

START = date(2025, 1, 1)
END = date(2026, 6, 30)  # fixed range for determinism

CACHE_PATH = Path(__file__).with_name("market_data_cache.json")

_FRANKFURTER_URL = "https://api.frankfurter.dev/v1/{start}..{end}?base={base}&symbols={symbols}"


# --- Lookup API (offline; stdlib + cache only) ----------------------------


def _fx_key(foreign: str, base: str) -> str:
    return f"{foreign}/{base}"


def _forward_fill(series: dict[str, Decimal], when: date, label: str) -> Decimal:
    """Most recent value on-or-before ``when``; first value if ``when`` precedes all.

    ``series`` keys are ISO date strings. Raises if the series is empty.
    """
    if not series:
        raise KeyError(f"No cached data points for {label}")
    target = when.isoformat()
    best_key: str | None = None
    for k in series:  # ISO date strings sort lexicographically == chronologically
        if k <= target:
            if best_key is None or k > best_key:
                best_key = k
    if best_key is None:
        # Requested date precedes the first available; use the earliest.
        best_key = min(series)
    return series[best_key]


class MarketData:
    """Offline accessor over the committed market-data cache."""

    def __init__(
        self,
        securities: dict[str, dict[str, Decimal]],
        fx: dict[str, dict[str, Decimal]],
        meta: dict[str, str],
    ):
        self._securities = securities
        self._fx = fx
        self.meta = meta

    @classmethod
    def load(cls, path: Path | str | None = None) -> MarketData:
        """Read the cache JSON, parsing every price to Decimal(str(...))."""
        path = Path(path) if path is not None else CACHE_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"Market-data cache not found at {path}. "
                f"Run: python {Path(__file__).name} --refresh"
            )
        with path.open() as fh:
            raw = json.load(fh)

        def _parse(block: dict[str, dict]) -> dict[str, dict[str, Decimal]]:
            return {
                name: {d: Decimal(str(v)) for d, v in series.items()}
                for name, series in block.items()
            }

        return cls(
            securities=_parse(raw.get("securities", {})),
            fx=_parse(raw.get("fx", {})),
            meta=raw.get("meta", {}),
        )

    def security(self, mnemonic: str, when: date) -> Decimal:
        """Native-currency close for ``mnemonic`` on ``when`` (forward-filled)."""
        series = self._securities.get(mnemonic)
        if series is None:
            raise KeyError(
                f"Security {mnemonic!r} not in cache "
                f"(have: {sorted(self._securities)})"
            )
        return _forward_fill(series, when, f"security {mnemonic}")

    def fx(self, foreign: str, base: str, when: date) -> Decimal:
        """Base-per-foreign rate for ``foreign/base`` on ``when`` (forward-filled)."""
        key = _fx_key(foreign, base)
        series = self._fx.get(key)
        if series is None:
            raise KeyError(
                f"FX pair {key!r} not in cache (have: {sorted(self._fx)})"
            )
        return _forward_fill(series, when, f"fx {key}")

    def latest_security_date(self, mnemonic: str) -> date:
        """Most recent date with a real quote for ``mnemonic`` in the cache."""
        series = self._securities.get(mnemonic)
        if not series:
            raise KeyError(
                f"Security {mnemonic!r} not in cache "
                f"(have: {sorted(self._securities)})"
            )
        # ISO date strings sort lexicographically == chronologically.
        return date.fromisoformat(max(series))

    def latest_fx_date(self, foreign: str, base: str) -> date:
        """Most recent date with a real quote for ``foreign/base`` in the cache."""
        key = _fx_key(foreign, base)
        series = self._fx.get(key)
        if not series:
            raise KeyError(
                f"FX pair {key!r} not in cache (have: {sorted(self._fx)})"
            )
        return date.fromisoformat(max(series))


# --- Refresh path (yfinance + frankfurter; network required) --------------


def _fetch_security(mnemonic: str, ticker: str) -> dict[str, float]:
    """Fetch daily closes for one security via yfinance. Retries once."""
    import yfinance as yf  # lazy: only needed on --refresh

    # yfinance 'end' is exclusive; bump a day to include END.
    start_s = START.isoformat()
    end_s = (date(END.year, END.month, END.day)).isoformat()

    last_err: Exception | None = None
    for attempt in (1, 2):
        try:
            df = yf.Ticker(ticker).history(
                start=start_s,
                end=end_s,
                auto_adjust=False,
            )
            if df is None or df.empty or "Close" not in df.columns:
                raise ValueError("empty DataFrame or missing Close column")
            out: dict[str, float] = {}
            for ts, close in df["Close"].items():
                if close != close:  # NaN guard
                    continue
                out[ts.date().isoformat()] = round(float(close), 6)
            if not out:
                raise ValueError("no non-NaN closes returned")
            return out
        except Exception as exc:  # noqa: BLE001 - report, don't swallow
            last_err = exc
            print(
                f"  ! {mnemonic} ({ticker}) attempt {attempt} failed: {exc}",
                file=sys.stderr,
            )
    raise RuntimeError(f"Failed to fetch {mnemonic} ({ticker}): {last_err}")


def _fetch_fx(foreign: str, base: str) -> dict[str, float]:
    """Fetch base-per-foreign daily rates from frankfurter. Retries once."""
    url = _FRANKFURTER_URL.format(
        start=START.isoformat(),
        end=END.isoformat(),
        base=foreign,
        symbols=base,
    )
    # frankfurter rejects the default Python urllib User-Agent (403); send one.
    req = urllib.request.Request(url, headers={"User-Agent": "gnucash-mcp-synthetic-book/1.0"})
    last_err: Exception | None = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            rates = payload.get("rates", {})
            out: dict[str, float] = {}
            for d, sym_map in rates.items():
                if base in sym_map:
                    out[d] = round(float(sym_map[base]), 6)
            if not out:
                raise ValueError("no rates returned")
            return out
        except Exception as exc:  # noqa: BLE001 - report, don't swallow
            last_err = exc
            print(
                f"  ! FX {foreign}/{base} attempt {attempt} failed: {exc}",
                file=sys.stderr,
            )
    raise RuntimeError(f"Failed to fetch FX {foreign}/{base}: {last_err}")


def refresh(path: Path | str | None = None) -> dict:
    """Fetch every instrument and FX pair and write the cache JSON."""
    path = Path(path) if path is not None else CACHE_PATH
    failures: list[str] = []

    securities: dict[str, dict[str, float]] = {}
    print(f"Fetching {len(SECURITIES)} securities via yfinance...")
    for mnemonic, ticker in SECURITIES.items():
        try:
            series = _fetch_security(mnemonic, ticker)
            securities[mnemonic] = series
            print(f"  ok  {mnemonic:8s} {ticker:12s} {len(series):4d} rows")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"security {mnemonic} ({ticker}): {exc}")

    fx: dict[str, dict[str, float]] = {}
    print(f"Fetching {len(FX_PAIRS)} FX pairs via frankfurter...")
    for foreign, base in FX_PAIRS:
        key = _fx_key(foreign, base)
        try:
            series = _fetch_fx(foreign, base)
            fx[key] = series
            print(f"  ok  {key:10s} {len(series):4d} rows")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"fx {key}: {exc}")

    data = {
        "meta": {
            "start": START.isoformat(),
            "end": END.isoformat(),
            "refreshed": datetime.now().isoformat(timespec="seconds"),
        },
        "securities": securities,
        "fx": fx,
    }

    with path.open("w") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")

    print(
        f"\nWrote {path} "
        f"({len(securities)}/{len(SECURITIES)} securities, "
        f"{len(fx)}/{len(FX_PAIRS)} FX pairs)"
    )
    if failures:
        print("\nFAILURES (review needed):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
    return data


# --- Self-test ------------------------------------------------------------


def selftest() -> None:
    """Print sample lookups, including a weekend forward-fill proof."""
    md = MarketData.load()

    print("meta:", md.meta)
    print()
    print("Security closes on 2026-06-01:")
    for m in ("600519", "AAPL"):
        print(f"  {m:8s} {md.security(m, date(2026, 6, 1))}")
    print()
    print("FX rates on 2026-06-01 (base per foreign):")
    for foreign, base in (("USD", "CNY"), ("HKD", "CNY"), ("EUR", "USD")):
        print(f"  {foreign}/{base}: {md.fx(foreign, base, date(2026, 6, 1))}")
    print()

    sunday = date(2025, 6, 1)  # Sunday
    friday = date(2025, 5, 30)  # prior Friday
    ff = md.fx("USD", "CNY", sunday)
    fri = md.fx("USD", "CNY", friday)
    print("Weekend forward-fill proof (USD/CNY):")
    print(f"  Sunday  {sunday} -> {ff}")
    print(f"  Friday  {friday} -> {fri}")
    print(f"  match: {ff == fri}")


def main(argv: list[str]) -> int:
    if "--refresh" in argv:
        refresh()
        return 0
    if "--selftest" in argv:
        selftest()
        return 0
    print(__doc__)
    print("Flags: --refresh (fetch+write cache), --selftest (sample lookups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
