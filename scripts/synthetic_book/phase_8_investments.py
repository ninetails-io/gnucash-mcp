"""Phase 8: investment activity for 2025 — DCA, quarterly trades, dividends.

Generates ~44 transactions exercising the lot + investment workflow:

  - 24 monthly DCA buys (VTSAX $500 + VBTLX $200 on the 1st)
  - 4 quarterly individual-security buys (AAPL, ETH, MSFT)
  - 3 quarterly partial-lot sells (AAPL, ETH, MSFT) with capital gain
    booked to Income:Investment Income:Capital Gains
  - 1 VBTLX "tax-loss harvest" sell + immediate rebuy pair
  - 12 reinvested dividends (VTSAX/AAPL/MSFT quarterly)

Every buy and every dividend reinvestment creates a new lot, and the
investment-side split is assigned to the new lot. Each sell assigns the
(negative-quantity) investment-side split back to the source lot (the
opening lot in all cases here, since we sell FIFO-ish and the opening
lots have enough shares for all sell sizes).

Prices come from Phase 1's monthly price table. Dividend USD amounts
are approximated from average share counts × per-share rates rather
than computed dynamically — this keeps the script simple without
re-walking the running share balance, and the spec says "~$0.35/share".

Bypasses the MCP server; uses piecash directly for speed and no audit
noise. The book MUST NOT be open in the GnuCash UI.

Usage:
    uv run python scripts/synthetic_book/phase_8_investments.py --dry-run
    uv run python scripts/synthetic_book/phase_8_investments.py
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import piecash


DEFAULT_BOOK = os.environ.get(
    "GNUCASH_BOOK_PATH",
    "/Users/stephen/Finances/alex.chen-morales.gnucash",
)

# ── Accounts ────────────────────────────────────────────────────

CHECKING = "Assets:Current Assets:Checking Account"
VTSAX_ACCT = "Assets:Investments:Brokerage:VTSAX"
VBTLX_ACCT = "Assets:Investments:Brokerage:VBTLX"
AAPL_ACCT = "Assets:Investments:Brokerage:AAPL"
MSFT_ACCT = "Assets:Investments:Brokerage:MSFT"
ETH_ACCT = "Assets:Investments:Brokerage:ETH"

DIVIDENDS = "Income:Investment Income:Dividends"
CAPITAL_GAINS = "Income:Investment Income:Capital Gains"


# Phase 1 monthly prices (USD per share), keyed by (symbol, month)
PRICES = {
    ("VTSAX", 1): Decimal("120.00"), ("VTSAX", 2): Decimal("121.50"),
    ("VTSAX", 3): Decimal("122.80"), ("VTSAX", 4): Decimal("124.00"),
    ("VTSAX", 5): Decimal("125.20"), ("VTSAX", 6): Decimal("126.00"),
    ("VTSAX", 7): Decimal("124.50"), ("VTSAX", 8): Decimal("122.00"),
    ("VTSAX", 9): Decimal("123.80"), ("VTSAX", 10): Decimal("125.50"),
    ("VTSAX", 11): Decimal("126.80"), ("VTSAX", 12): Decimal("128.00"),
    ("VBTLX", 1): Decimal("10.50"), ("VBTLX", 2): Decimal("10.52"),
    ("VBTLX", 3): Decimal("10.55"), ("VBTLX", 4): Decimal("10.58"),
    ("VBTLX", 5): Decimal("10.60"), ("VBTLX", 6): Decimal("10.62"),
    ("VBTLX", 7): Decimal("10.65"), ("VBTLX", 8): Decimal("10.68"),
    ("VBTLX", 9): Decimal("10.70"), ("VBTLX", 10): Decimal("10.73"),
    ("VBTLX", 11): Decimal("10.76"), ("VBTLX", 12): Decimal("10.80"),
    ("AAPL", 2): Decimal("195.50"), ("AAPL", 3): Decimal("201.20"),
    ("AAPL", 5): Decimal("210.40"), ("AAPL", 8): Decimal("198.30"),
    ("AAPL", 11): Decimal("218.00"),
    ("MSFT", 3): Decimal("388.00"), ("MSFT", 6): Decimal("400.00"),
    ("MSFT", 8): Decimal("407.50"), ("MSFT", 9): Decimal("410.00"),
    ("MSFT", 11): Decimal("417.00"), ("MSFT", 12): Decimal("420.00"),
    ("ETH", 7): Decimal("2820.00"),
}


# ── DCA (monthly) ───────────────────────────────────────────────

def dca_plan(year: int):
    """Yield DCA buy specs for every month.

    Spec: $500 VTSAX + $200 VBTLX on the 1st of each month.
    Shares are computed to 4 decimal places from the monthly price.
    """
    for month in range(1, 13):
        yield {
            "date": date(year, month, 1),
            "symbol": "VTSAX",
            "account": VTSAX_ACCT,
            "usd_amount": Decimal("500.00"),
            "price": PRICES[("VTSAX", month)],
            "lot_title": f"VTSAX DCA {year}-{month:02d}",
        }
        yield {
            "date": date(year, month, 1),
            "symbol": "VBTLX",
            "account": VBTLX_ACCT,
            "usd_amount": Decimal("200.00"),
            "price": PRICES[("VBTLX", month)],
            "lot_title": f"VBTLX DCA {year}-{month:02d}",
        }


# ── Quarterly trades ────────────────────────────────────────────

# (date, action, symbol, shares, price, notes)
# - action: "buy" or "sell"
# - Sells come from the opening lot (named per Phase 3)
QUARTERLY_TRADES = [
    # (month, day, action, symbol, shares, price_override?)
    (3, 10, "buy", "AAPL", Decimal("5.0000"), Decimal("195.00")),
    (5, 15, "sell", "AAPL", Decimal("3.0000"), Decimal("210.00")),
    (7, 20, "buy", "ETH", Decimal("0.500000"), Decimal("2800.00")),
    (8, 15, "buy", "MSFT", Decimal("10.0000"), Decimal("395.00")),
    (10, 8, "sell", "ETH", Decimal("1.000000"), Decimal("3100.00")),
    (11, 18, "sell", "MSFT", Decimal("5.0000"), Decimal("415.00")),
    # VBTLX tax-loss scenario: sell 100 @ $10.60 (Dec), rebuy 100 @ $10.80
    (12, 15, "sell", "VBTLX", Decimal("100.0000"), Decimal("10.60")),
    (12, 16, "buy", "VBTLX", Decimal("100.0000"), Decimal("10.80")),
]

ACCOUNTS_BY_SYMBOL = {
    "VTSAX": VTSAX_ACCT, "VBTLX": VBTLX_ACCT,
    "AAPL": AAPL_ACCT, "MSFT": MSFT_ACCT, "ETH": ETH_ACCT,
}

# Opening lot titles (matching what Phase 3 created)
OPENING_LOT_TITLES = {
    "VTSAX": "VTSAX core position",
    "VBTLX": "VBTLX bond allocation",
    "AAPL": "AAPL 2023 purchase",
    "MSFT": "MSFT 2024 purchase",
    "ETH": "ETH 2024 purchase",
}


# ── Dividends ───────────────────────────────────────────────────
#
# Amounts approximated from average-share-count × $/share rather than
# recomputed dynamically. Reinvested: income credited, brokerage
# account debited with quantity = new shares.
#
# (month, day, symbol, usd_amount, price)
DIVIDENDS_PLAN = [
    # VTSAX quarterly ($0.35/share). Shares grow with DCA; approximate.
    (3, 15, "VTSAX", Decimal("68.00"), Decimal("122.80")),   # ~194 shares
    (6, 15, "VTSAX", Decimal("73.00"), Decimal("126.00")),   # ~208 shares
    (9, 15, "VTSAX", Decimal("79.00"), Decimal("123.80")),   # ~225 shares
    (12, 15, "VTSAX", Decimal("84.00"), Decimal("128.00")),  # ~240 shares
    # AAPL quarterly ($0.25/share)
    (2, 15, "AAPL", Decimal("6.25"), Decimal("195.50")),     # 25 shares
    (5, 15, "AAPL", Decimal("7.50"), Decimal("210.40")),     # 30 (after Mar buy)
    (8, 15, "AAPL", Decimal("6.75"), Decimal("198.30")),     # 27 (after May sell)
    (11, 15, "AAPL", Decimal("6.75"), Decimal("218.00")),    # 27
    # MSFT quarterly ($0.75/share)
    (3, 15, "MSFT", Decimal("11.25"), Decimal("388.00")),    # 15 shares
    (6, 15, "MSFT", Decimal("11.25"), Decimal("400.00")),    # 15
    (9, 15, "MSFT", Decimal("18.75"), Decimal("410.00")),    # 25 (after Aug buy)
    (12, 15, "MSFT", Decimal("15.00"), Decimal("420.00")),   # 20 (after Nov sell)
]


# ── Helpers ─────────────────────────────────────────────────────

def shares_from_usd(usd: Decimal, price: Decimal, fraction: int = 10000) -> Decimal:
    """Convert USD purchase amount to share count at the commodity's fraction."""
    places = Decimal(1) / Decimal(fraction)
    return (usd / price).quantize(places)


def find_lot_by_title(book, title: str):
    """Find the first lot matching the given title."""
    # Iterate accounts to find lots (they're attached to accounts).
    for acct in book.accounts:
        for lot in acct.lots:
            if lot.title == title:
                return lot
    return None


# ── Driver ─────────────────────────────────────────────────────

def write_transactions(book_path: Path, dry_run: bool = False) -> None:
    book = piecash.open_book(str(book_path), readonly=False)
    try:
        usd = book.commodities.get(mnemonic="USD")
        acct_cache: dict[str, piecash.Account] = {}
        for a in book.accounts:
            acct_cache[a.fullname] = a

        created_txns = 0
        created_lots = 0

        # ── Phase 8a: Monthly DCA ─────────────────────────────
        print("Step 1: Monthly DCA (24 buys)")
        for spec in dca_plan(2025):
            acct = acct_cache[spec["account"]]
            usd_amt = spec["usd_amount"]
            price = spec["price"]
            shares = shares_from_usd(usd_amt, price, acct.commodity.fraction)

            lot = piecash.Lot(
                title=spec["lot_title"],
                account=acct,
                notes=f"Monthly DCA — ${usd_amt} @ ${price}",
                is_closed=0,
            )

            inv_split = piecash.Split(
                account=acct, value=usd_amt, quantity=shares,
            )
            cash_split = piecash.Split(
                account=acct_cache[CHECKING], value=-usd_amt,
            )
            piecash.Transaction(
                currency=usd,
                description=f"DCA {spec['symbol']}",
                post_date=spec["date"],
                splits=[inv_split, cash_split],
            )
            inv_split.lot = lot
            created_txns += 1
            created_lots += 1

        # ── Phase 8b: Quarterly trades ────────────────────────
        print("Step 2: Quarterly trades (buys and sells)")
        for month, day, action, sym, shares, price in QUARTERLY_TRADES:
            d = date(2025, month, day)
            acct = acct_cache[ACCOUNTS_BY_SYMBOL[sym]]
            usd_amt = (shares * price).quantize(Decimal("0.01"))

            if action == "buy":
                lot = piecash.Lot(
                    title=f"{sym} {d.isoformat()} purchase",
                    account=acct,
                    notes=f"Quarterly trade — {shares} shares @ ${price}",
                    is_closed=0,
                )
                inv_split = piecash.Split(
                    account=acct, value=usd_amt, quantity=shares,
                )
                cash_split = piecash.Split(
                    account=acct_cache[CHECKING], value=-usd_amt,
                )
                piecash.Transaction(
                    currency=usd,
                    description=f"Buy {shares} {sym} @ ${price}",
                    post_date=d,
                    splits=[inv_split, cash_split],
                )
                inv_split.lot = lot
                created_lots += 1

            else:  # sell
                # FIFO-ish: sell from opening lot (all sells fit in opening)
                lot = find_lot_by_title(book, OPENING_LOT_TITLES[sym])
                if lot is None:
                    raise ValueError(
                        f"Opening lot not found for {sym}: "
                        f"'{OPENING_LOT_TITLES[sym]}'"
                    )
                # Cost basis per share = lot's original cost basis / opening quantity
                # For our opening lots the split is a single buy, so derive from it:
                opening_split = lot.splits[0]
                cost_per_share = (
                    Decimal(str(opening_split.value))
                    / Decimal(str(opening_split.quantity))
                )
                cost_basis = (shares * cost_per_share).quantize(Decimal("0.01"))
                sale_proceeds = usd_amt
                gain = sale_proceeds - cost_basis

                inv_split = piecash.Split(
                    account=acct,
                    value=-cost_basis,
                    quantity=-shares,
                )
                cash_split = piecash.Split(
                    account=acct_cache[CHECKING],
                    value=sale_proceeds,
                )
                gain_split = piecash.Split(
                    account=acct_cache[CAPITAL_GAINS],
                    value=-gain,  # credit to income (natural-credit account)
                )
                piecash.Transaction(
                    currency=usd,
                    description=f"Sell {shares} {sym} @ ${price}",
                    post_date=d,
                    splits=[inv_split, cash_split, gain_split],
                )
                inv_split.lot = lot  # assign sell split back to source lot

            created_txns += 1

        # ── Phase 8c: Reinvested dividends ────────────────────
        print("Step 3: Reinvested dividends (12)")
        for month, day, sym, usd_amt, price in DIVIDENDS_PLAN:
            d = date(2025, month, day)
            acct = acct_cache[ACCOUNTS_BY_SYMBOL[sym]]
            shares = shares_from_usd(usd_amt, price, acct.commodity.fraction)

            lot = piecash.Lot(
                title=f"{sym} dividend {d.isoformat()}",
                account=acct,
                notes=f"Reinvested dividend — ${usd_amt} @ ${price}",
                is_closed=0,
            )

            inv_split = piecash.Split(
                account=acct, value=usd_amt, quantity=shares,
            )
            income_split = piecash.Split(
                account=acct_cache[DIVIDENDS], value=-usd_amt,
            )
            piecash.Transaction(
                currency=usd,
                description=f"{sym} dividend (reinvested)",
                post_date=d,
                splits=[inv_split, income_split],
            )
            inv_split.lot = lot
            created_txns += 1
            created_lots += 1

        if dry_run:
            print(f"\nDRY RUN — would stage {created_txns} txns, "
                  f"{created_lots} lots")
            book.cancel()
            return

        print(f"\nSaving book ({created_txns} txns, {created_lots} lots)...")
        book.save()
        print("Done.")
    finally:
        book.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=DEFAULT_BOOK)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    book_path = Path(args.book)
    if not book_path.exists():
        raise SystemExit(f"Book not found: {book_path}")

    if not args.dry_run and not args.no_backup:
        backup = book_path.with_suffix(".pre-phase8.gnucash")
        shutil.copy2(book_path, backup)
        print(f"Backup created: {backup}\n")

    write_transactions(book_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
