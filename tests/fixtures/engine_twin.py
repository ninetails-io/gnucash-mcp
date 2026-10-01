"""The engine twin: the same business action on two copies of one
book, one through the server and one through GnuCash's own engine,
then the rows compared.

``desktop_totals.py`` asks the engine for NUMBERS. This drives it to
WRITE: ``engine_act.scm`` is loaded into ``gnucash-cli`` as a report
whose renderer calls the functions the dialogs call
(``gncInvoiceApplyPayment``, ``gncInvoiceUnpost``,
``gncInvoiceAutoApplyPayments``), and the SQL backend saves each
commit as it does under the GUI. No screen, no clicks; GnuCash's
data and config directories are a temp folder for the one run.

``dump`` renders the business rows of a book with every GUID replaced
by a role read off the row's content, so two books that hold the same
thing dump to the same text. "Parity" is that text being equal.

Written for the prepayment work in the 1.5 adversarial review
(``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``, C22 / C37 / C49 /
C50). ``tests/test_parity_prepayment.py`` keeps the engine's dumps as
the expected text for machines without GnuCash, and re-derives them
live where ``gnucash-cli`` exists.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ACT = HERE / "engine_act.scm"

_CLI_CANDIDATES = (
    "/Applications/Gnucash.app/Contents/MacOS/gnucash-cli",
)


def find_gnucash_cli() -> str | None:
    override = os.environ.get("GNUCASH_CLI")
    if override:
        return override if Path(override).exists() else None
    on_path = shutil.which("gnucash-cli")
    if on_path:
        return on_path
    return next((c for c in _CLI_CANDIDATES if Path(c).exists()), None)


def engine_run(book: Path, actions: list[str]) -> list[str]:
    """Run ``actions`` (see ``engine_act.scm``) on ``book`` IN PLACE
    through GnuCash's engine. Returns each action's result word —
    ``ok``; ``rejected`` where the engine declined (a price it would
    not take); ``balance=<n>`` for a question — and raises on
    anything else."""
    cli = find_gnucash_cli()
    if cli is None:
        raise RuntimeError("gnucash-cli not found (set GNUCASH_CLI)")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "data").mkdir()
        (tmp / "config").mkdir()
        (tmp / "config" / "config-user.scm").write_text(f'(load "{ACT}")\n')
        (tmp / "actions.txt").write_text("\n".join(actions) + "\n")
        out = tmp / "report.html"
        env = dict(
            os.environ,
            GNC_DATA_HOME=str(tmp / "data"),
            GNC_CONFIG_HOME=str(tmp / "config"),
            GUILE_AUTO_COMPILE="0",
            PARITY_ACTIONS=str(tmp / "actions.txt"),
        )
        run = subprocess.run(
            [cli, "--report", "run", "--name", "Engine Act",
             "--output-file", str(out), str(book)],
            env=env, capture_output=True, text=True, timeout=900,
        )
        if run.returncode != 0 or not out.exists():
            raise RuntimeError(
                f"gnucash-cli failed ({run.returncode}):\n"
                f"{run.stdout[-2000:]}\n{run.stderr[-2000:]}"
            )
        results = re.findall(r"ACT\|([a-z]+(?:=[-0-9/]+)?)\|", out.read_text())
    bad = [
        r for r in results
        if r not in ("ok", "rejected") and not r.startswith("balance=")
    ]
    if len(results) != len(actions) or bad:
        raise RuntimeError(f"engine actions did not all run: {results}")
    return results


def guid_of(book: Path, sql: str, *params) -> str:
    con = sqlite3.connect(f"file:{book}?mode=ro", uri=True)
    try:
        return con.execute(sql, params).fetchone()[0]
    finally:
        con.close()


# ── what the engine leaves that the server does not copy ────────────

# A NAMED allowlist, by bookkeeper ruling (2026-09-30, fix-branch
# round 2, item 3): each exception to "parity means an empty diff" is
# listed here with what it is and why, ``dump`` filters only what is
# named here, and ``debris_found`` counts each kind so a test can say
# how much of it a book holds. Nothing is filtered silently. The GUI
# gate's prepayment book is the backstop if desktop turns out to
# depend on any of it.
ENGINE_DEBRIS = {
    "empty_lot": (
        "A lot with no split. gncOwnerApplyPaymentSecs makes a payment "
        "lot, moves its split into the document's lot, and leaves the "
        "empty lot behind; the engine destroys such lots when it next "
        "meets one (gncOwnerAutoApplyPaymentsWithLots, "
        "gncScrubBusinessLot). The server never creates one."
    ),
    "stale_posting_refs": (
        "post_txn / post_lot / post_acc still set on a document whose "
        "date_posted is NULL. gncInvoiceUnpost clears all four, but the "
        "SQL backend leaves a NULL object reference out of its UPDATE "
        "(add_objectref_guid_to_query returns early), so the old GUIDs "
        "stay. The server writes the NULLs the engine means."
    ),
    "unreferenced_taxtable_copy": (
        "A hidden child tax table no entry row names. "
        "gncInvoicePostToAccount makes the copy and repoints each line "
        "at it in memory; in the headless engine run the line was never "
        "re-saved, so the copy is referenced by nothing. ACCEPTED as "
        "debris; whether a GUI post saves the line's move is a separate "
        "question, OPEN until the gate (ruling item 4: post a taxed "
        "invoice in the GUI on a SQL book, then SELECT invisible, "
        "parent FROM taxtables and the entries' i_taxtable)."
    ),
    "refcount": (
        "The stored refcount of a billing term (not compared, not "
        "counted). The engine adds one for every reference it loads "
        "and saves the sum, so the number grows with each session."
    ),
}


def debris_found(book: Path) -> dict[str, int]:
    """How much of each countable kind of ``ENGINE_DEBRIS`` a book
    holds; kinds with none are left out. A server-written book
    returns ``{}``."""
    con = sqlite3.connect(f"file:{book}?mode=ro", uri=True)
    try:
        counts = {
            "empty_lot": con.execute(
                "select count(*) from lots where guid not in "
                "(select lot_guid from splits where lot_guid is not null)"
            ).fetchone()[0],
            "stale_posting_refs": con.execute(
                "select count(*) from invoices where date_posted is null "
                "and (post_txn is not null or post_lot is not null "
                "or post_acc is not null)"
            ).fetchone()[0],
            "unreferenced_taxtable_copy": con.execute(
                "select count(*) from taxtables t where parent is not null "
                "and not exists (select 1 from entries e where "
                "e.i_taxtable = t.guid or e.b_taxtable = t.guid)"
            ).fetchone()[0],
        }
    finally:
        con.close()
    return {kind: n for kind, n in counts.items() if n}


# ── the dump ─────────────────────────────────────────────────────────

_SLOT_COLUMNS = (
    "slot_type", "int64_val", "string_val", "double_val", "timespec_val",
    "numeric_val_num", "numeric_val_denom", "gdate_val",
)


def dump(
    book: Path, skip_transactions_before: str | None = None,
    debris=ENGINE_DEBRIS,
) -> str:
    """Every transaction, split, lot, and document row of ``book`` as
    text, GUIDs replaced by roles:

    * accounts, parties, commodities, documents by their names;
    * a transaction by its date, type, description, number, and the
      splits it holds;
    * a lot by its title (marked ``(owner)`` once it is a party's and
      no longer a document's), or — a payment lot has none — by the
      splits in it.

    ``enter_date`` is the wall clock and is left out. Slots are read
    through their frames, so a frame's own GUID never appears.

    What the engine leaves in a SQL book that the server does not
    copy is left out of the text ONLY where ``debris`` names it — by
    default the whole ``ENGINE_DEBRIS`` allowlist, each entry of which
    says what it is and why. Pass ``debris=()`` to see everything.
    """
    con = sqlite3.connect(f"file:{book}?mode=ro", uri=True)

    def q(sql, *a):
        return con.execute(sql, a).fetchall()

    names: dict[str, str] = {}
    for g, n in q("select guid, name from accounts"):
        names[g] = f"acct:{n}"
    for table, tag in (("customers", "cust"), ("vendors", "vend"),
                       ("employees", "emp"), ("jobs", "job")):
        column = "username" if table == "employees" else "name"
        for g, n in q(f"select guid, {column} from {table}"):
            names[g] = f"{tag}:{n}"
    for g, m in q("select guid, mnemonic from commodities"):
        names[g] = f"ccy:{m}"
    for g, i, owner_type in q("select guid, id, owner_type from invoices"):
        names[g] = f"doc:{owner_type}:{i}"
    # A posted document points at a hidden copy of its billing term
    # (and desktop makes one of each tax table): same name, a parent.
    for table, tag in (("billterms", "term"), ("taxtables", "tax")):
        for g, name, parent in q(f"select guid, name, parent from {table}"):
            names[g] = f"{tag}:{name}" + (" (posted copy)" if parent else "")

    def n(v):
        return names.get(v, v) if isinstance(v, str) else v

    def slots(obj: str, indent: str) -> list[str]:
        lines = []
        rows = q(
            "select name, guid_val, " + ", ".join(_SLOT_COLUMNS)
            + " from slots where obj_guid = ? order by name", obj,
        )
        for name, guid_val, *rest in rows:
            fields = dict(zip(_SLOT_COLUMNS, rest))
            if fields["slot_type"] == 9:  # a frame: its children
                lines.append(f"{indent}SLOT {name} frame")
                lines += slots(guid_val, indent + "  ")
                continue
            shown = " ".join(f"{k}={v!r}" for k, v in fields.items())
            lines.append(
                f"{indent}SLOT {name} {shown} guid_val={n(guid_val)!r}"
            )
        return lines

    def split_key(row):
        return (n(row["account_guid"]), row["value_num"], row["quantity_num"])

    con.row_factory = sqlite3.Row
    txns = []
    for t in q("select * from transactions"):
        if skip_transactions_before and t["post_date"] < skip_transactions_before:
            # Not printed, but still named, so a document's post_txn
            # never shows as a raw GUID.
            names[t["guid"]] = f"txn@{t['post_date'][:10]}:{t['num']}"
            continue
        ttype = q(
            "select string_val from slots where obj_guid = ? "
            "and name = 'trans-txn-type'", t["guid"],
        )
        splits = sorted(
            q("select * from splits where tx_guid = ?", t["guid"]),
            key=split_key,
        )
        key = (
            t["post_date"], ttype[0][0] if ttype else "-",
            t["description"], t["num"],
            tuple(split_key(s) for s in splits),
        )
        txns.append((key, t, splits))
    txns.sort(key=lambda x: x[0])
    for i, (_, t, _splits) in enumerate(txns):
        names[t["guid"]] = f"txn#{i}"

    # Lots: titled ones by title; untitled by what sits in them.
    lot_rows = q("select * from lots")
    untitled = []
    for lot in lot_rows:
        title = q(
            "select string_val from slots where obj_guid = ? "
            "and name = 'title'", lot["guid"],
        )
        if title:
            # A live document link is a GUID INSIDE the gncInvoice
            # frame; the engine leaves the frame, empty, on a lot it
            # has unposted.
            linked = q(
                "select 1 from slots f join slots c "
                "on c.obj_guid = f.guid_val and c.slot_type = 5 "
                "where f.obj_guid = ? and f.name = 'gncInvoice'",
                lot["guid"],
            )
            names[lot["guid"]] = (
                f"lot:{title[0][0]}" + ("" if linked else " (owner)")
            )
        else:
            held = sorted(
                (n(s["tx_guid"]), s["value_num"], s["quantity_num"])
                for s in q("select * from splits where lot_guid = ?",
                           lot["guid"])
            )
            untitled.append((held, lot["guid"]))
    for i, (_, guid) in enumerate(sorted(untitled)):
        names[guid] = f"lot:untitled#{i}"

    out: list[str] = []
    for _, t, splits in txns:
        out.append(
            f"TXN {names[t['guid']]} post_date={t['post_date']!r} "
            f"num={t['num']!r} desc={t['description']!r} "
            f"ccy={n(t['currency_guid'])}"
        )
        out += slots(t["guid"], "  ")
        for s in splits:
            out.append(
                f"  SPLIT acct={n(s['account_guid'])} memo={s['memo']!r} "
                f"action={s['action']!r} rec={s['reconcile_state']} "
                f"rec_date={s['reconcile_date']} "
                f"value={s['value_num']}/{s['value_denom']} "
                f"qty={s['quantity_num']}/{s['quantity_denom']} "
                f"lot={n(s['lot_guid'])}"
            )
            out += slots(s["guid"], "    ")
    for lot in sorted(lot_rows, key=lambda r: names[r["guid"]]):
        if "empty_lot" in debris and not q(
            "select 1 from splits where lot_guid = ?", lot["guid"],
        ):
            continue
        out.append(
            f"LOT {names[lot['guid']]} account={n(lot['account_guid'])} "
            f"is_closed={lot['is_closed']}"
        )
        out += slots(lot["guid"], "  ")
    for inv in q("select * from invoices order by owner_type, id"):
        unposted = (
            "stale_posting_refs" in debris and inv["date_posted"] is None
        )
        shown = " ".join(
            f"{k}={n(inv[k])!r}" for k in inv.keys()
            if k not in ("guid", "date_opened")
            and not (unposted and k in ("post_txn", "post_lot", "post_acc"))
        )
        out.append(f"DOC {names[inv['guid']]} {shown}")
        out += slots(inv["guid"], "  ")
        for entry in sorted(
            q("select * from entries where invoice = ? or bill = ?",
              inv["guid"], inv["guid"]),
            key=lambda e: (e["description"], e["quantity_num"]),
        ):
            # ``date`` is local noon (the entry ledger's date cell)
            # and ``date_entered`` the wall clock.
            shown = " ".join(
                f"{k}={n(entry[k])!r}" for k in entry.keys()
                if k not in ("guid", "date", "date_entered")
            )
            out.append(f"  ENTRY {shown}")
    for term in sorted(
        q("select * from billterms"), key=lambda t: names[t["guid"]],
    ):
        shown = " ".join(
            f"{k}={n(term[k])!r}" for k in term.keys()
            if k != "guid" and not (k == "refcount" and "refcount" in debris)
        )
        out.append(f"TERM {names[term['guid']]} {shown}")
    # Tax tables are listed only when asked for: the one kind of row
    # that differs is the unreferenced copy.
    if "unreferenced_taxtable_copy" not in debris:
        for table in sorted(
            q("select * from taxtables"), key=lambda t: names[t["guid"]],
        ):
            shown = " ".join(
                f"{k}={n(table[k])!r}" for k in table.keys()
                if k not in ("guid", "refcount")
            )
            out.append(f"TAXTABLE {names[table['guid']]} {shown}")
    con.close()
    return "\n".join(out) + "\n"

