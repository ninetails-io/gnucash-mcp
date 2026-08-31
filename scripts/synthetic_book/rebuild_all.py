"""Rebuild all three sample oracle books from 2025-01 through today.

One command that runs the whole pipeline the individual pieces
already support:

1. **Refresh market data** — yfinance (securities) + frankfurter
   (FX) into the committed cache ``market_data_cache.json``, fetched
   from 2025-01-01 through ``--through`` (default: today). Requires
   network + the dev dependency group (``uv sync``). Skippable.
2. **Build** Alex, Lin Wei, and Sabine to their ``*.generated.gnucash``
   paths with the same ``--through``. The canonical sample books are
   never written directly (each builder refuses).
3. **Verify** every generated book: opens with piecash, correct
   default currency, transaction count above a per-book floor, and
   recent activity (latest posting within 45 days of ``--through``).
4. **Promote** (unless ``--no-promote``): the previous canonical
   books are backed up to a timestamped directory under /tmp, then
   the verified generated books replace them.

Promotion CHANGES THE ORACLES: any committed capture snapshots and
test-plan expected values (e.g. specs/v1.5/BOOKKEEPER_TEST_PLAN_GB1.md
Track A) describe the build they were taken against, not the new one.
Re-capture after promoting if a live loop is planned.

Usage::

    uv run python scripts/synthetic_book/rebuild_all.py
    uv run python scripts/synthetic_book/rebuild_all.py --through 2026-07-10
    uv run python scripts/synthetic_book/rebuild_all.py --skip-refresh --only sabine
    uv run python scripts/synthetic_book/rebuild_all.py --no-promote
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent
SAMPLES = REPO / "samples"


@dataclass(frozen=True)
class _Book:
    key: str            # --only selector
    builder: str        # script filename in this directory
    generated: str      # builder output under samples/
    canonical: str      # the shipped oracle it replaces
    currency: str       # expected default currency
    txn_floor: int      # sanity floor; well under the real count


BOOKS = (
    _Book("alex", "build_alex.py", "alex.generated.gnucash",
          "alex-chen-morales.gnucash", "USD", 1500),
    _Book("lin-wei", "build_lin_wei.py", "lin-wei.generated.gnucash",
          "lin-wei.gnucash", "CNY", 2000),
    _Book("sabine", "build_sabine.py", "sabine-brenner.generated.gnucash",
          "sabine-brenner.gnucash", "EUR", 400),
)


def _refresh_cache(through: date) -> None:
    print(f"── Refreshing market-data cache through {through} …")
    subprocess.run(
        [sys.executable, str(HERE / "market_data.py"),
         "--refresh", "--through", through.isoformat()],
        check=True,
    )


def _build(book: _Book, through: date) -> Path:
    out = SAMPLES / book.generated
    print(f"── Building {book.key} → {out.name} (through {through}) …")
    subprocess.run(
        [sys.executable, str(HERE / book.builder),
         "--out", str(out), "--through", through.isoformat()],
        check=True,
    )
    return out


def _verify(book: _Book, path: Path, through: date) -> None:
    """Open the generated book and check the invariants a broken
    build would violate. Raises SystemExit with a specific message —
    a failed verification must block promotion."""
    import piecash

    print(f"── Verifying {path.name} …")
    gc_book = piecash.open_book(str(path), readonly=True,
                                open_if_lock=True)
    try:
        cur = gc_book.default_currency.mnemonic
        if cur != book.currency:
            raise SystemExit(
                f"{path.name}: default currency {cur}, "
                f"expected {book.currency}"
            )
        post_dates = [
            t.post_date for t in gc_book.transactions
            if t.post_date is not None
        ]
        count = len(post_dates)
        if count < book.txn_floor:
            raise SystemExit(
                f"{path.name}: only {count} transactions "
                f"(floor {book.txn_floor}) — truncated build?"
            )
        latest = max(post_dates)
        if latest < through - timedelta(days=45):
            raise SystemExit(
                f"{path.name}: latest activity {latest} is more than "
                f"45 days before {through} — timeline didn't extend?"
            )
        print(f"   ok: {count} txns, {cur}, latest activity {latest}")
    finally:
        gc_book.close()


def _promote(book: _Book, generated: Path, backup_dir: Path) -> None:
    canonical = SAMPLES / book.canonical
    backup_dir.mkdir(parents=True, exist_ok=True)
    if canonical.exists():
        shutil.copy2(canonical, backup_dir / canonical.name)
    shutil.copy2(generated, canonical)
    print(f"── Promoted {generated.name} → {canonical.name}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--through", default=None, metavar="YYYY-MM-DD",
        help="Timeline + market-data horizon (default: today).")
    parser.add_argument(
        "--skip-refresh", action="store_true",
        help="Use the committed market-data cache as-is (offline; "
             "quotes past the cache horizon forward-fill).")
    parser.add_argument(
        "--only", choices=[b.key for b in BOOKS], default=None,
        help="Rebuild a single book (default: all three).")
    parser.add_argument(
        "--force", action="store_true",
        help="Promote even if a gnucash-mcp server process is "
             "running (it may be serving the canonical books).")
    parser.add_argument(
        "--no-promote", action="store_true",
        help="Stop after verification; leave the canonical books "
             "untouched (outputs stay at samples/*.generated.gnucash).")
    parser.add_argument(
        "--continue-only", action="store_true",
        help="Skip the prefix rebuild: run the closed-loop updater "
             "(continue_book.py) on each canonical book IN PLACE — "
             "repair + extend through --through — then verify. The "
             "frozen committed prefix is never rewritten.")
    args = parser.parse_args()

    through = (
        date.fromisoformat(args.through) if args.through else date.today()
    )
    books = [b for b in BOOKS if args.only in (None, b.key)]

    if args.continue_only:
        for book in books:
            cmd = [sys.executable, str(HERE / "continue_book.py"),
                   book.key, "--through", through.isoformat()]
            if args.force:
                cmd.append("--force")
            subprocess.run(cmd, check=True)
        for book in books:
            _verify(book, SAMPLES / book.canonical, through)
        print("\nDone (continue-only). Canonical books current through "
              f"{through} — working-tree drift only, never staged.")
        return 0

    if not args.skip_refresh:
        _refresh_cache(through)
    else:
        print("── Market-data refresh skipped (using committed cache).")

    built: list[tuple[_Book, Path]] = []
    for book in books:
        built.append((book, _build(book, through)))

    # Verify EVERYTHING before promoting ANYTHING — a half-promoted
    # oracle set is worse than a stale one.
    for book, path in built:
        _verify(book, path, through)

    if args.no_promote:
        print("\nDone (no-promote). Generated books verified at:")
        for _, path in built:
            print(f"  {path}")
        return 0

    # Live-server guard (learned 2026-07-10): a running gnucash-mcp
    # may have these exact canonical files configured — the samples
    # double as live oracle books. Promotion rewrites SQLite files
    # in place; doing that under an active server risks a read
    # landing mid-truncate. Open-per-request keeps the window small,
    # but the safe move is not to race it at all.
    probe = subprocess.run(
        ["pgrep", "-f", "gnucash-mcp"], capture_output=True, text=True,
    )
    if probe.stdout.strip() and not args.force:
        raise SystemExit(
            "A gnucash-mcp server appears to be running (pids: "
            f"{' '.join(probe.stdout.split())}). Promotion rewrites "
            "the canonical sample books in place — stop the client/"
            "server first, or rerun with --force if you are certain "
            "no configured server is serving these files."
        )

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_dir = Path("/tmp") / f"sample-books-pre-rebuild-{stamp}"
    for book, path in built:
        _promote(book, path, backup_dir)

    print(f"\nDone. Previous canonical books preserved at: {backup_dir}")
    print(
        "Reminder: the oracles just changed — committed capture\n"
        "snapshots and test-plan expected values describe the OLD\n"
        "build. Re-capture before the next live loop, and commit the\n"
        "new sample books + market_data_cache.json together."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
