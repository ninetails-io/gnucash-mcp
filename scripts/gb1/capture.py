"""GB-1 capture rig: report outputs before/after the rate-anchor
unification, for the bookkeeper's pre-merge review.

Usage: uv run python scripts/gb1/capture.py <tag>
Writes scripts/gb1/captures/{book}.{tag}.txt for the three sample
oracles across every surface the change touches. Diff before/after
tags to see exactly which numbers the policy shift moves.
"""
import sys
import pathlib
from datetime import date

from gnucash_mcp.book import GnuCashBook

TAG = sys.argv[1]
OUT = pathlib.Path(__file__).parent / "captures"
OUT.mkdir(exist_ok=True)

RANGE = (date(2026, 1, 1), date(2026, 6, 30))

for name in ("alex-chen-morales", "lin-wei", "sabine-brenner"):
    gb = GnuCashBook(f"samples/{name}.gnucash")
    s, e = RANGE
    parts = [
        "== spending single ==",
        str(gb.spending_by_category(start_date=s, end_date=e)),
        "== spending by month ==",
        str(gb.spending_by_category(start_date=s, end_date=e, group_by="month")),
        "== spending by quarter ==",
        str(gb.spending_by_category(start_date=s, end_date=e, group_by="quarter")),
        "== income single ==",
        str(gb.income_by_source(start_date=s, end_date=e)),
        "== income by month ==",
        str(gb.income_by_source(start_date=s, end_date=e, group_by="month")),
        "== cash_flow single ==",
        str(gb.cash_flow(start_date=s, end_date=e)),
        "== cash_flow by quarter ==",
        str(gb.cash_flow(start_date=s, end_date=e, group_by="quarter")),
    ]
    (OUT / f"{name}.{TAG}.txt").write_text("\n".join(parts))
    print(f"{name}.{TAG} captured")
