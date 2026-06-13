#!/usr/bin/env python3
"""Capture report outputs for Branch 1 chokepoint-refactor pre/post diff.

Branch 1 consolidates four invariants into chokepoints:

1. ``_rates_as_of(book)`` → mandatory ``as_of`` (or split) so historical
   reports pick up historical rates instead of today's.
2. ``_is_voided(split)`` helper applied at every iteration site so the
   reconciliation / lot views agree on what "voided" means.
3. ``_resolve_account`` template-filter chokepoint so %short and full-GUID
   refs match the path branch's behavior.
4. ``_market_prices_for`` wrapper so every ``book.prices`` walk skips
   ``type='transaction'`` auto-placeholders.

This script captures every report whose output could move under those
fixes, plus a few control captures that *shouldn't* move. The diff
between pre and post is the verification.

Usage::

    # Pre-state captures (do this BEFORE making any edits):
    uv run python scripts/branch_1/capture.py \\
        --book samples/alex-chen-morales.gnucash \\
        --out specs/branch_1_captures/pre/alex

    # Lin Wei's book ends 2025-12-31; "today" must be overridden to
    # avoid stale-data artifacts on time-of-now signals.
    uv run python scripts/branch_1/capture.py \\
        --book samples/lin-wei.gnucash \\
        --out specs/branch_1_captures/pre/lin_wei \\
        --today 2025-12-31 --historical 2025-06-30

    # After Branch 1 lands, repeat with --out .../post/...
    # Then:
    diff -ru specs/branch_1_captures/pre/alex \\
             specs/branch_1_captures/post/alex
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow running directly from the repo root without installing.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from gnucash_mcp.book import GnuCashBook  # noqa: E402


# ── Output helpers ──────────────────────────────────────────────────


def _dump(out_dir: Path, name: str, value) -> None:
    """Write a captured value to ``out_dir/<name>``.

    Strings → written as-is (preserves compact-formatted text tables).
    Anything else → JSON with sorted keys for stable diff order.
    """
    path = out_dir / name
    if isinstance(value, str):
        path.write_text(value if value.endswith("\n") else value + "\n")
    else:
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
        )


def _capture(out_dir: Path, name: str, fn) -> None:
    """Run ``fn()`` and dump its result. Errors get captured as text.

    The point of capturing errors is that pre-fix and post-fix both
    surface them in the diff — a tool that started failing (or
    stopped failing) is itself signal. We record the exception's
    class and message ONLY; the traceback contains source line
    numbers that drift as the codebase grows, creating false-
    positive diffs that mask real behavioral changes.
    """
    try:
        result = fn()
        _dump(out_dir, name, result)
        print(f"  ✓ {name}")
    except Exception as e:  # noqa: BLE001 — intentional broad catch
        _dump(
            out_dir,
            name,
            f"ERROR: {type(e).__name__}: {e}",
        )
        print(f"  ✗ {name} — {type(e).__name__}: {e}", file=sys.stderr)


# ── Book introspection ─────────────────────────────────────────────


def _gather_meta(book: GnuCashBook, today: date, historical: date) -> dict:
    """Per-book metadata so anchor dates are self-documenting in the diff.

    Captures first/last transaction date, default currency, transaction
    count, and per-commodity price date ranges. If "historical" is
    chosen before the first stored price, the rates fix can't show a
    diff and the report will be misleading — this surfaces that risk.
    """
    with book.open(readonly=True) as pb:
        from piecash.core.commodity import Price
        from piecash.core.transaction import Transaction

        txns = pb.session.query(Transaction).all()
        if txns:
            post_dates = sorted(t.post_date for t in txns)
            first_txn = post_dates[0].isoformat()
            last_txn = post_dates[-1].isoformat()
        else:
            first_txn = last_txn = None

        prices_by_commodity: dict[str, dict] = {}
        for p in pb.session.query(Price).all():
            key = f"{p.commodity.namespace}:{p.commodity.mnemonic}"
            entry = prices_by_commodity.setdefault(
                key, {"market_count": 0, "transaction_count": 0,
                       "first_market": None, "last_market": None}
            )
            ptype = getattr(p, "type", None)
            pdate = p.date.date() if hasattr(p.date, "date") else p.date
            if ptype == "transaction":
                entry["transaction_count"] += 1
            else:
                entry["market_count"] += 1
                iso = pdate.isoformat()
                if entry["first_market"] is None or iso < entry["first_market"]:
                    entry["first_market"] = iso
                if entry["last_market"] is None or iso > entry["last_market"]:
                    entry["last_market"] = iso

        return {
            "book_path": str(book.book_path),
            "default_currency": pb.default_currency.mnemonic,
            "transaction_count": len(txns),
            "first_transaction": first_txn,
            "last_transaction": last_txn,
            "anchor_today": today.isoformat(),
            "anchor_historical": historical.isoformat(),
            "prices_by_commodity": prices_by_commodity,
        }


def _discover_targets(book: GnuCashBook) -> dict:
    """Find per-book targets the captures need.

    Picks one reconcilable account (BANK or CREDIT with the most
    splits — the busiest is the most interesting), one investment
    account (STOCK/MUTUAL), one open lot if any exist, and a
    template-subtree account guid to exercise the
    ``_resolve_account`` template-filter asymmetry (SB-12).
    """
    targets: dict = {
        "reconcilable_account": None,
        "investment_account": None,
        "open_lot_guid": None,
        "template_account_guid": None,
        "template_account_short": None,
    }
    with book.open(readonly=True) as pb:
        template_guids = book._template_account_guids(pb)

        # Pick the busiest BANK/CREDIT account by split count.
        recon_candidates = [
            a for a in pb.accounts
            if a.guid not in template_guids
            and a.type in {"BANK", "CREDIT"}
        ]
        if recon_candidates:
            recon_candidates.sort(
                key=lambda a: len(a.splits), reverse=True
            )
            targets["reconcilable_account"] = recon_candidates[0].fullname

        # First STOCK/MUTUAL account.
        inv_candidates = [
            a for a in pb.accounts
            if a.guid not in template_guids
            and a.type in {"STOCK", "MUTUAL"}
        ]
        if inv_candidates:
            targets["investment_account"] = inv_candidates[0].fullname
            # First open lot on it, if any.
            for lot in inv_candidates[0].lots:
                if not lot.is_closed:
                    targets["open_lot_guid"] = lot.guid
                    break

        # One template-subtree account guid (SB-12 exposure).
        # Skip the root_template itself; pick a child if one exists.
        rt = pb.root_template
        if rt is not None:
            for child in rt.children:
                targets["template_account_guid"] = child.guid
                # Build the %short form _resolve_account would accept.
                targets["template_account_short"] = (
                    book._account_short_guid(pb, child)
                )
                break

    return targets


def _find_budget_name(book: GnuCashBook) -> str | None:
    """Return the first budget's name, or None if no budgets exist."""
    with book.open(readonly=True) as pb:
        from piecash.budget import Budget
        b = pb.session.query(Budget).first()
        return b.name if b else None


def _find_vendor_id(book: GnuCashBook) -> str | None:
    """Return the first vendor's id, or None if none exist.

    ``vendor_spending_report`` needs an existing vendor to filter on
    when the book has no vendor activity at all — but the no-filter
    form is more interesting (covers everyone), so this is only used
    as a backstop.
    """
    with book.open(readonly=True) as pb:
        try:
            from piecash.business.person import Vendor
            v = pb.session.query(Vendor).first()
            return v.id if v else None
        except Exception:
            return None


# ── Capture orchestration ──────────────────────────────────────────


def run_captures(
    book_path: Path,
    out_dir: Path,
    today: date,
    historical: date,
) -> None:
    """Run every Branch 1 capture against ``book_path`` into ``out_dir``."""
    book = GnuCashBook(str(book_path))

    print(f"Capturing against {book_path.name}:")
    print(f"  today    = {today}")
    print(f"  historic = {historical}")
    print(f"  out dir  = {out_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Metadata ──
    _capture(out_dir, "00_book_meta.json",
             lambda: _gather_meta(book, today, historical))

    targets = _discover_targets(book)
    _capture(out_dir, "00_targets.json", lambda: targets)

    budget_name = _find_budget_name(book)

    # ── Locks the _rates_as_of fix ──

    _capture(out_dir, "10_book_summary.txt",
             lambda: book.get_book_summary())

    _capture(out_dir, "11_balance_sheet_today.json",
             lambda: book.balance_sheet(today))
    _capture(out_dir, "12_balance_sheet_historical.json",
             lambda: book.balance_sheet(historical))

    _capture(out_dir, "13_net_worth_today.json",
             lambda: book.net_worth(end_date=today))

    # Two years of quarterly snapshots. Boundaries are where SB-1
    # bites — every historical snapshot today picks up *today*'s rates.
    series_start = date(today.year - 2, today.month, 1)
    _capture(out_dir, "14_net_worth_series.json",
             lambda: book.net_worth(
                 start_date=series_start,
                 end_date=today,
                 interval="quarter",
             ))

    # Historical Q for the flow-style reports. Pick a calendar quarter
    # that ends near `historical` so the period is well-defined.
    hist_q_end = historical
    hist_q_start = date(historical.year, historical.month, 1) \
        - timedelta(days=92)
    _capture(out_dir, "15_cash_flow_historical.json",
             lambda: book.cash_flow(
                 start_date=hist_q_start,
                 end_date=hist_q_end,
             ))
    _capture(out_dir, "16_spending_by_category_historical.txt",
             lambda: book.spending_by_category(
                 start_date=hist_q_start,
                 end_date=hist_q_end,
                 compact=True,
             ))
    _capture(out_dir, "17_income_by_source_historical.txt",
             lambda: book.income_by_source(
                 start_date=hist_q_start,
                 end_date=hist_q_end,
                 compact=True,
             ))

    if budget_name is not None:
        _capture(out_dir, "18_budget_report_all.txt",
                 lambda: book.get_budget_report(
                     budget_name=budget_name,
                     period="all",
                     compact=True,
                 ))

    # Vendor spending — historical period. Skip silently if business
    # module isn't composed into this build (Lin Wei may not have it).
    if hasattr(book, "vendor_spending_report"):
        _capture(out_dir, "19_vendor_spending_historical.txt",
                 lambda: book.vendor_spending_report(
                     start_date=hist_q_start.isoformat(),
                     end_date=hist_q_end.isoformat(),
                     compact=True,
                 ))

    # Debt payoff — only if any APR slots exist; the book method
    # raises a clear ValueError if not, which captures fine as the
    # error case.
    _capture(out_dir, "20_debt_payoff_plan.txt",
             lambda: book.debt_payoff_plan(
                 monthly_budget="10000",
                 compact=True,
             ))

    # ── Locks the _is_voided fix ──

    if targets["reconcilable_account"]:
        acct = targets["reconcilable_account"]
        safe_name = acct.replace(":", "_").replace(" ", "_")
        _capture(out_dir, f"30_unreconciled_{safe_name}.json",
                 lambda: book.get_unreconciled_splits(
                     account_name=acct,
                     compact=False,
                 ))

    if targets["investment_account"]:
        acct = targets["investment_account"]
        safe_name = acct.replace(":", "_").replace(" ", "_")
        _capture(out_dir, f"31_lots_{safe_name}.json",
                 lambda: book.list_lots(
                     account=acct,
                     include_closed=True,
                     compact=False,
                 ))

    # ── Locks the _resolve_account template-filter fix (SB-12) ──

    # Resolve a template-subtree account via three different ref shapes.
    # Path lookup already filters templates (returns None). %short and
    # full-GUID lookups currently DON'T (returns the template account).
    # Post-fix all three should return None / "not found".
    if targets["template_account_short"]:
        short = targets["template_account_short"]
        full = targets["template_account_guid"]
        _capture(
            out_dir, "40_resolve_template_via_short.txt",
            lambda: _safe_get_account(book, short),
        )
        _capture(
            out_dir, "41_resolve_template_via_full_guid.txt",
            lambda: _safe_get_account(book, full),
        )

    # ── Locks the _market_prices_for wrapper (SB-11) ──

    _capture(out_dir, "50_list_commodities.json",
             lambda: book.list_commodities(compact=False))

    if targets["open_lot_guid"]:
        lot_guid = targets["open_lot_guid"]
        _capture(out_dir, "51_calculate_lot_gain.json",
                 lambda: book.calculate_lot_gain(lot_guid=lot_guid))

    # ── Cross-tool agreement check ──
    # The bookkeeper's "same data, different tools should agree" probe.
    # Diverging here pre→post means Branch 1 introduced a regression.
    _capture(out_dir, "90_cross_tool_agreement.json",
             lambda: _cross_tool_agreement(book, today))

    print(f"Done. {sum(1 for _ in out_dir.iterdir())} files in {out_dir}.")


def _safe_get_account(book: GnuCashBook, ref: str) -> dict:
    """Wrapper that captures both success and 'not found' as data.

    Pre-fix on %short/full-GUID: returns the template account dict.
    Post-fix: returns ``{"resolved": None}``. The diff makes the
    behavior change explicit.
    """
    result = book.get_account(ref)
    if result is None:
        return {"ref": ref, "resolved": None}
    return {"ref": ref, "resolved": result}


def _cross_tool_agreement(book: GnuCashBook, today: date) -> dict:
    """Summary net worth vs net_worth(today) vs balance_sheet(today) totals.

    These three should all answer the same question. If they diverge
    pre or post, that's a bug — but the pre/post diff specifically
    isolates "did Branch 1 break this".
    """
    bs = book.balance_sheet(today)
    nw = book.net_worth(end_date=today)
    # get_book_summary's net worth is rendered into text; extract via a
    # marker rather than parsing the whole thing. Captured for human
    # diff inspection — the diff itself is what matters here, not the
    # parse logic.
    return {
        "balance_sheet_assets_total": bs["assets"]["total"],
        "balance_sheet_liabilities_total": bs["liabilities"]["total"],
        "balance_sheet_equity_total": bs["equity"]["total"],
        "net_worth_today": nw.get("net_worth"),
        "summary_excerpt": _extract_net_worth_line(book.get_book_summary()),
    }


def _extract_net_worth_line(summary_text: str) -> str | None:
    """Pull the 'Net worth' line out of the dashboard text for compare."""
    for line in summary_text.splitlines():
        if "net worth" in line.lower():
            return line.strip()
    return None


# ── Entry point ────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture Branch 1 reports for pre/post diff."
    )
    parser.add_argument(
        "--book", required=True, type=Path,
        help="Path to the GnuCash book file.",
    )
    parser.add_argument(
        "--out", required=True, type=Path,
        help="Directory to write captures into (created if absent).",
    )
    parser.add_argument(
        "--today", type=date.fromisoformat, default=None,
        help=(
            "Override the 'today' anchor (default: real today). "
            "Use for books whose data ends before today — e.g. Lin "
            "Wei needs --today=2025-12-31 to avoid stale artifacts."
        ),
    )
    parser.add_argument(
        "--historical", type=date.fromisoformat, default=None,
        help=(
            "Historical anchor for as_of_date / period-end captures. "
            "Default: 5 months before --today (rough mid-period)."
        ),
    )
    args = parser.parse_args()

    if not args.book.exists():
        sys.exit(f"Book not found: {args.book}")

    # Point logging / backups at a temp dir for the capture run so we
    # don't pollute the real .mcp directory with audit entries from
    # 30+ tool calls.
    os.environ["GNUCASH_LOG_DIR"] = str(args.out / ".tmp_logs")

    today = args.today or date.today()
    historical = args.historical or (today - timedelta(days=150))

    run_captures(args.book, args.out, today, historical)


if __name__ == "__main__":
    main()
