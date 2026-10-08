"""Part B of the class-6 bookkeeper loop, over stdio against a copy of Alex.

Every call goes through the MCP tool layer. Each step prints the reply
the client sees and a PASS/FAIL line for what the plan expects.

Usage (see BOOKKEEPER_TEST_PLAN_CLASS6_REVIEW_ITEMS.md):

    uv run python loop.py /path/to/alex-copy.gnucash
    REPO=/path/to/develop-worktree uv run python loop.py /path/to/other-copy

Step 0 (the sell) runs here, so the book must be a fresh copy of the
committed Alex. The lot and payment GUIDs below are that book's; a
regenerated Alex needs them looked up again.
"""
import asyncio
import json
import os
import sqlite3
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

BOOK = sys.argv[1]
LOT = "73ccbdb4"
SELL_TSV = (
    "ref\tdate\tdescription\tamt1\tacct1\tqty1\tamt2\tacct2\tqty2\n"
    "1\t2026-07-20\tSell AAPL 2026-05-10 lot\t-586.64\t"
    "Assets:Investments:Brokerage:AAPL\t-2\t586.64\t"
    "Assets:Current Assets:Checking Account\t"
)
INV_PAYMENT, BILL_PAYMENT = "9aaa4c7e", "125cb915"
NULLED = "4ddbaf82"  # an Emerald Analytics payment; B7 nulls its description
results = []


def js(text):
    """A JSON reply; the first call of a session carries the server's
    restart notice ahead of it."""
    return json.loads(text[text.index("{"):])


def check(label, ok, detail=""):
    results.append((label, ok))
    print(f"--- {'PASS' if ok else 'FAIL'}: {label}{' — ' + detail if detail else ''}")


async def run(session):
    async def call(name, args):
        r = await session.call_tool(name, args)
        text = "\n".join(c.text for c in r.content if hasattr(c, "text"))
        print(f"\n>>> {name} {json.dumps(args)}{' [isError]' if r.isError else ''}\n{text}")
        return text

    def lot_closed(text):
        return js(text)["is_closed"]

    async def doc(doc_id, kind):
        return js(await call("get_document", {"id": doc_id, "document_type": kind}))

    # Step 0: sell the lot's 2 shares at cost; the sell goes into the
    # lot, which closes.
    created = js(await call("create_transactions", {"transactions": SELL_TSV}))
    SELL = created["results"].splitlines()[1].split("\t")[2]
    txn = js(await call("get_transaction", {"guid": SELL}))
    sell_split = next(sp["guid"] for sp in txn["splits"] if sp["account"].endswith(":AAPL"))
    await call("assign_split_to_lot", {"split_guid": sell_split, "lot_guid": LOT})
    check("setup: lot closes on the sell", lot_closed(await call("get_lot", {"guid": LOT})) is True)

    # B1: delete refuses a lot-held split, single and batch.
    t = await call("delete_transaction", {"guid": SELL})
    check("B1 delete refused without force", "splits in lots" in t and "AAPL buy 2026-05-10" in t)
    t = await call("delete_transaction", {"guid": [SELL]})
    check("B1 batch delete refused, nothing deleted", "nothing deleted" in t)
    check("B1 sell still there", "Sell AAPL" in await call("get_transaction", {"guid": SELL}))

    # B2: void reopens the lot, unvoid closes it again.
    await call("void_transaction", {"guid": SELL, "reason": "class-6 loop: entered in error"})
    check("B2 void reopens the lot", lot_closed(await call("get_lot", {"guid": LOT})) is False)
    open_lots = await call("list_lots", {"account": "Assets:Investments:Brokerage:AAPL"})
    check("B2 open-lot listing shows it", "AAPL buy 2026-05-10" in open_lots)
    await call("unvoid_transaction", {"guid": SELL})
    check("B2 unvoid closes it again", lot_closed(await call("get_lot", {"guid": LOT})) is True)

    # B3: replace_splits on the lot-held sell.
    same = [
        {"account": "Assets:Investments:Brokerage:AAPL", "amount": "-586.64", "quantity": "-2"},
        {"account": "Assets:Current Assets:Checking Account", "amount": "586.64"},
    ]
    t = await call("replace_splits", {"guid": SELL, "splits": same})
    check("B3 replace refused without force", "splits in lots" in t)
    t = await call("replace_splits", {"guid": SELL, "splits": same, "force": True})
    check("B3 forced replace warns about the lot", "Removed splits from lots" in t)
    check("B3 lot reopens after the replace", lot_closed(await call("get_lot", {"guid": LOT})) is False)
    t = await call("replace_splits", {"guid": SELL, "splits": [
        {"account": "Assets:Investments:Brokerage:AAPL", "amount": "-586.64"},
        {"account": "Assets:Current Assets:Checking Account", "amount": "586.64"},
    ]})
    check("B3 missing quantity names the ref as given",
          "Split for 'Assets:Investments:Brokerage:AAPL' requires 'quantity'" in t)
    t = await call("replace_splits", {"guid": SELL, "splits": [
        {"account": "Assets:Investments:Brokerage:AAPL", "amount": "-586.64", "quantity": "-2"},
        {"account": "Assets:Current Assets:Checking Account", "amount": "500.00"},
    ]})
    check("B3 imbalance still refused", "do not balance" in t)
    # Put the new sell split back in the lot for B4.
    txn = js(await call("get_transaction", {"guid": SELL}))
    new_split = next(s["guid"] for s in txn["splits"] if s["account"].endswith(":AAPL"))
    await call("assign_split_to_lot", {"split_guid": new_split, "lot_guid": LOT})
    check("B3 re-assigned sell closes the lot", lot_closed(await call("get_lot", {"guid": LOT})) is True)

    # B4: forced delete reports the lot and reopens it.
    t = await call("delete_transaction", {"guid": SELL, "force": True})
    check("B4 forced delete reports lot_splits_affected", '"lot_splits_affected":1' in t.replace(" ", ""))
    lot = js(await call("get_lot", {"guid": LOT}))
    check("B4 lot open with its 2 shares", lot["is_closed"] is False
          and lot["summary"]["quantity"].startswith("2"), str(lot["summary"]))

    # B5: an invoice payment.
    t = await call("delete_transaction", {"guid": INV_PAYMENT})
    check("B5 invoice payment delete refused", "splits in lots" in t)
    await call("void_transaction", {"guid": INV_PAYMENT, "reason": "class-6 loop: bounced"})
    d = await doc("000016", "invoice")
    check("B5 voided payment: invoice owed again", d["status"] == "posted" and d["amount_due"] == "3500.00", str(d.get("amount_due")))
    out = await call("get_outstanding_documents", {"party_type": "customer"})
    check("B5 invoice back on the outstanding list", "000016" in out)
    await call("unvoid_transaction", {"guid": INV_PAYMENT})
    d = await doc("000016", "invoice")
    check("B5 unvoided: invoice paid again", d["status"] == "paid")

    # B6: a bill payment, forced delete.
    t = await call("delete_transaction", {"guid": BILL_PAYMENT})
    check("B6 bill payment delete refused", "splits in lots" in t)
    t = await call("delete_transaction", {"guid": BILL_PAYMENT, "force": True})
    check("B6 forced delete goes through", "deleted" in t)
    d = await doc("000006", "bill")
    check("B6 bill owed again", d["status"] == "posted" and d["amount_due"] == "450.00", str(d.get("amount_due")))

    # B7: a NULL description (nulled by raw SQL before the server
    # started) breaks neither search, create, nor the dashboard.
    t = await call("search_transactions", {"query": "Emerald", "limit": 3})
    check("B7 search with a NULL description", "error" not in t)
    t = await call("create_transactions", {"dry_run": True, "transactions": (
        "ref\tdate\tdescription\tamt1\tacct1\tamt2\tacct2\n"
        "1\t2026-07-21\tCorner Store\t-12.00\t"
        "Assets:Current Assets:Checking Account\t12.00\tExpenses:Groceries"
    )})
    check("B7 create dry run with a NULL description", '"results"' in t and "error" not in t)
    t = await call("get_book_summary", {})
    check("B7 dashboard with a NULL description", "error_type" not in t)


async def main():
    with sqlite3.connect(BOOK) as conn:
        nulled = conn.execute(
            "UPDATE transactions SET description = NULL WHERE guid LIKE ?",
            (NULLED + "%",),
        ).rowcount
    assert nulled == 1, f"expected one row to null, got {nulled}"
    params = StdioServerParameters(
        command="uv", args=["run", "--directory", os.environ.get("REPO", "/Users/stephen/Projects/gnucash-mcp"), "gnucash-mcp"],
        env={**os.environ, "GNUCASH_BOOK_PATH": BOOK, "GNUCASH_MCP_MODULES": "all"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            await run(s)
    print(f"\n=== {sum(ok for _, ok in results)}/{len(results)} checks passed")
    for label, ok in results:
        if not ok:
            print("FAILED:", label)


asyncio.run(main())
