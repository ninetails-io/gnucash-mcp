"""Bring a sample persona book current — the clone-side updater.

The demo-books endgame (GENERATOR_MODERNIZATION_SPEC §3): clones carry
the frozen committed books plus this updater. Running it extends a
book from its frozen edge through ``--through`` (default today):

1. **Cutoff**: the book's latest posted transaction — the frozen
   prefix's edge. Nothing at or before it is ever rewritten.
2. **Prices**: real monthly closes + event quotes for the new range,
   from the committed offline cache.
3. **Streams**: the builder's deterministic generators re-run with the
   new horizon; only transactions dated after the cutoff are written.
4. **Business / investments**: the plan generators with a ``since``
   cutoff, then a settlement pass that pays the documents a living
   book would have settled (recent ones stay open by construction).
5. **Policy**: the month-by-month closed loop — statement payments
   from real balances, sweeps from real surplus (see continuation.py).
   On first contact with a drifted book this IS the repair: the
   catch-up payoff and staged pile-drain emerge from the same rules.
6. **Scheduled-transaction cursors** advance so the dashboard opens
   clean, then invariants verify (no overdraft, revolver under limit).

Usage::

    uv run python scripts/synthetic_book/continue_book.py alex
    uv run python scripts/synthetic_book/continue_book.py alex --through 2026-08-31
    uv run python scripts/synthetic_book/continue_book.py alex --book /tmp/alex.gnucash

Without ``--book`` this updates the canonical working copy under
``samples/`` IN PLACE (that's its job — working-tree drift is by
design and never staged). A timestamped backup lands under /tmp
first, and a running gnucash-mcp server blocks the run unless
``--force``.
"""

from __future__ import annotations

import argparse
import importlib
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import continuation  # noqa: E402
from gnucash_mcp.book import GnuCashBook  # noqa: E402

# persona key -> (builder module, canonical filename under samples/)
PERSONAS = {
    "alex": ("build_alex", "alex-chen-morales.gnucash"),
    "lin-wei": ("build_lin_wei", "lin-wei.gnucash"),
    "sabine": ("build_sabine", "sabine-brenner.gnucash"),
}

SAMPLES = HERE.parent.parent / "samples"


def continue_persona(persona: str, book_path: Path, through: date) -> int:
    mod = importlib.import_module(PERSONAS[persona][0])
    policy = getattr(mod, "POLICY", None)
    if policy is None:
        raise SystemExit(
            f"{persona}: builder has no POLICY yet — continuation for "
            "this persona lands later in the arc (Alex first).")

    cutoff = continuation.find_cutoff(book_path)
    if cutoff >= through:
        # No new transactions to write, but posture maintenance
        # (reconciliation through the last full month, SX cursors) is
        # idempotent and keeps a same-day rerun honest.
        print(f"{persona}: already current (cutoff {cutoff} ≥ {through}); "
              "refreshing posture.")
        for line in continuation.reconcile_through(policy, book_path, through):
            print(f"   {line}")
        sx = mod.advance_schedules(book_path, through)
        print(f"   schedules: {sx}")
        return 0
    print(f"── Continuing {persona}: ({cutoff} → {through}] at {book_path}")

    n = mod.extend_prices(book_path, cutoff, through)
    print(f"   prices: {n} new rows")

    txns = [t for t in mod.continuation_txns(through) if t["date"] > cutoff]
    n = mod.write_bulk(book_path, txns)
    print(f"   streams: {n} transactions")

    # Settle BEFORE the plan generators: the prefix's aged documents
    # clear first, so the through-relative "recent open" documents get
    # re-created (their still-open predecessors would suppress them)
    # and this run's own new invoices aren't swept up by the aging pass.
    settled = continuation.settle_documents(policy, book_path, cutoff, through)
    for line in settled:
        print(f"   {line}")

    if policy.book_repairs is not None:
        for line in policy.book_repairs(book_path, cutoff, through):
            print(f"   repair: {line}")

    book = GnuCashBook(str(book_path))
    biz = mod.continue_business(book, through, cutoff)
    print(f"   business: {biz}")
    inv = mod.continue_investments(book_path, through, cutoff)
    print(f"   investments: {inv}")

    actions = continuation.run_policy(policy, book_path, cutoff, through)
    for line in actions:
        print(f"   policy: {line}")

    for line in continuation.reconcile_through(policy, book_path, through):
        print(f"   {line}")

    sx = mod.advance_schedules(book_path, through)
    print(f"   schedules: {sx}")

    warnings = continuation.verify_invariants(policy, book_path, cutoff,
                                              through)
    for w in warnings:
        print(f"   WARN: {w}")
    print(f"── {persona} current through {through}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("persona", choices=sorted(PERSONAS))
    parser.add_argument("--through", default=None, metavar="YYYY-MM-DD",
                        help="Continue through this date (default: today).")
    parser.add_argument("--book", default=None, metavar="PATH",
                        help="Book to update (default: the canonical "
                             "samples/ copy, in place).")
    parser.add_argument("--force", action="store_true",
                        help="Run even if a gnucash-mcp server process "
                             "appears to be running.")
    args = parser.parse_args()

    through = (date.fromisoformat(args.through) if args.through
               else date.today())
    book_path = (Path(args.book).resolve() if args.book
                 else SAMPLES / PERSONAS[args.persona][1])
    if not book_path.exists():
        raise SystemExit(f"Book not found: {book_path}")

    if not args.force:
        # Boundary-aware pattern: a real server's command line has the
        # binary name followed by a space or end-of-line; a path
        # segment ("…/gnucash-mcp/scripts/…") is always followed by
        # "/". The bare-substring pattern matched THIS script's own
        # checkout path on CI runners — the guard vetoed itself.
        probe = subprocess.run(["pgrep", "-f", "gnucash-mcp( |$)"],
                               capture_output=True, text=True)
        if probe.stdout.strip():
            raise SystemExit(
                "A gnucash-mcp server appears to be running (pids: "
                f"{' '.join(probe.stdout.split())}). It may be serving "
                "this book — stop it first, or rerun with --force.")

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = Path("/tmp") / f"pre-continue-{stamp}-{book_path.name}"
    shutil.copy2(book_path, backup)
    print(f"── Backup: {backup}")

    return continue_persona(args.persona, book_path, through)


if __name__ == "__main__":
    raise SystemExit(main())
