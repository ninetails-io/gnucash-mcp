"""Count SQL statements per call on the Alex sample book (temp copy).

Needs the built book: `uv run python scripts/synthetic_book/rebuild_all.py
--skip-refresh --only alex` from the repo root (see samples/README.md)."""
import shutil, tempfile, json
from pathlib import Path
from sqlalchemy import event
from sqlalchemy.engine import Engine
from gnucash_mcp.book import GnuCashBook

tmp = Path(tempfile.mkdtemp(prefix="qc-"))
src = Path(__file__).resolve().parents[3] / "samples" / "alex-chen-morales.gnucash"
book_path = tmp / src.name
shutil.copy(src, book_path)
gb = GnuCashBook(str(book_path))

count = {"n": 0}
@event.listens_for(Engine, "before_cursor_execute")
def _c(conn, cursor, statement, parameters, context, executemany):
    count["n"] += 1

def measure(label, fn):
    count["n"] = 0
    fn()
    print(f"{count['n']:6d} queries  {label}")

with gb.open(readonly=True) as b:
    n_txn = len(b.transactions)
    n_split = sum(len(t.splits) for t in b.transactions)
    acct = next(a for a in b.accounts if a.type == "BANK" and len(a.splits) > 50)
    bank = acct.fullname
    some_split = next(s for s in acct.splits if s.reconcile_state != "y").guid
    # a lot for get_lot
    lot = next((l for a in b.accounts for l in a.lots), None)
    lot_guid = lot.guid if lot else None
print(f"book: {n_txn} transactions, {n_split} splits; account={bank}")

measure("get_unreconciled_splits(compact=True)  [split prefix map]",
        lambda: gb.get_unreconciled_splits(bank, compact=True))
measure("get_unreconciled_splits(compact=False) [verbose, no prefix map]",
        lambda: gb.get_unreconciled_splits(bank, compact=False))
measure("list_transactions(account, compact)   [register view]",
        lambda: gb.list_transactions(account=bank))
txn_guid = gb.list_transactions(account=bank, limit=1).splitlines()[1].split("\t")[1]
measure("get_transaction(one)                  [cold prefix maps]",
        lambda: gb.get_transaction(txn_guid))
if lot_guid:
    measure("get_lot                               [split prefix map]",
            lambda: gb.get_lot(lot_guid))
measure("set_reconcile_state(c)                [split prefix map]",
        lambda: gb.set_reconcile_state(some_split, "c"))
