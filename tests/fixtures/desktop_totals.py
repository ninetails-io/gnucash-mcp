"""The arithmetic parity oracle: what GnuCash's own engine totals a
document at, asked headlessly.

``tests/fixtures/parity_dump.py`` diffs the ROWS two books hold. This
is its sibling for NUMBERS: draft documents through the server, then
have ``gnucash-cli`` load the book and print ``gncInvoiceGetTotal`` /
``…Subtotal`` / ``…Tax`` for each (``parity_totals.scm``). No GUI, no
screenshots, nothing installed: GnuCash's data and config directories
are pointed at a temp folder for the one run, and Guile's compile
cache is switched off.

Three uses:

* ``uv run python tests/fixtures/desktop_totals.py record`` — rebuild the
  recorded set (``desktop_totals_5_12.json``): each document's entry
  rows as stored, and the totals GnuCash 5.12 gave. CI has no GnuCash;
  ``tests/test_entry_math_desktop.py`` replays the rows through the
  server's math and expects GnuCash's recorded answers.
* the live test in the same file, which runs whenever ``gnucash-cli``
  is on the machine: a fresh random book, the server's totals against
  the engine's.
* by hand, when the entry math changes: ``… live <n> <seed>``.

History: written for the 1.5 adversarial review (C1 / C2). The
server's own tax math disagreed with the engine on 383 of 600 random
documents; the port in ``book/_entry_math.py`` agrees on all of them.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).parent
REPORT = HERE / "parity_totals.scm"
RECORDED = HERE / "desktop_totals_5_12.json"
RECORDED_SEED = 1512
RECORDED_COUNT = 300

_CLI_CANDIDATES = (
    "/Applications/Gnucash.app/Contents/MacOS/gnucash-cli",
)

# The entry columns the arithmetic reads (gnc-entry-sql.cpp).
ENTRY_COLUMNS = (
    "quantity_num", "quantity_denom",
    "i_acct", "i_price_num", "i_price_denom",
    "i_discount_num", "i_discount_denom", "i_disc_type", "i_disc_how",
    "i_taxable", "i_taxincluded", "i_taxtable",
    "b_acct", "b_price_num", "b_price_denom",
    "b_taxable", "b_taxincluded", "b_taxtable",
)


def find_gnucash_cli() -> str | None:
    """``$GNUCASH_CLI``, a ``gnucash-cli`` on PATH, or the macOS app's."""
    override = os.environ.get("GNUCASH_CLI")
    if override:
        return override if Path(override).exists() else None
    on_path = shutil.which("gnucash-cli")
    if on_path:
        return on_path
    return next((c for c in _CLI_CANDIDATES if Path(c).exists()), None)


# ── drafting: random documents through the server ────────────────────

_TABLES = {
    "single": [("percentage", "8.25", "Liabilities:GST Payable")],
    "two": [
        ("percentage", "5", "Liabilities:GST Payable"),
        ("percentage", "7", "Liabilities:PST Payable"),
    ],
    "same": [
        ("percentage", "5", "Liabilities:GST Payable"),
        ("percentage", "7", "Liabilities:GST Payable"),
    ],
    "flat": [
        ("value", "2.00", "Liabilities:ECO Payable"),
        ("percentage", "5", "Liabilities:GST Payable"),
    ],
    "odd": [("percentage", "7.375", "Liabilities:PST Payable")],
    "in_a": [("percentage", "8.25", "Assets:Input Tax A")],
    "in_two": [
        ("percentage", "5", "Assets:Input Tax A"),
        ("percentage", "9.975", "Assets:Input Tax B"),
    ],
}
_SALES = ["single", "two", "same", "flat", "odd"]
_PURCHASE = ["in_a", "in_two"]
_QUANTITIES = ["1", "2", "3", "7", "12", "0.5", "2.5", "1.25", "0.333"]


def _price(rng: random.Random) -> str:
    kind = rng.random()
    if kind < 0.35:
        return f"{rng.randint(1, 50000) / 100:.2f}"
    if kind < 0.70:
        return f"{rng.randint(1, 500000) / 1000:.3f}"       # sub-cent
    if kind < 0.85:
        return f"{rng.randint(1, 2000) * 5 / 1000:.3f}"     # ends in 5: ties
    return str(rng.choice(
        [10, 100, 19.99, 0.10, 0.125, 0.335, 10.10, 100.07, 10.13]
    ))


def build_book(path: Path, count: int, seed: int) -> None:
    """A business book holding ``count`` DRAFT documents — invoices,
    bills, and credit notes on both sides — with random lines, tax
    tables (one rate, two accounts, two rates on one account, a flat
    amount, an awkward rate), tax-included lines, and, on customer
    documents, the line discounts only desktop writes (set by SQL the
    way gnc-entry-sql.cpp stores them). Deterministic for a seed."""
    import piecash

    from gnucash_mcp.book import GnuCashBook

    rng = random.Random(seed)
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    root, usd = book.root_account, book.default_currency

    def acct(name, type_, parent, placeholder=False):
        return piecash.Account(
            name=name, type=type_, parent=parent, commodity=usd,
            placeholder=placeholder,
        )

    assets = acct("Assets", "ASSET", root, True)
    acct("Checking", "BANK", assets)
    acct("Accounts Receivable", "RECEIVABLE", assets)
    acct("Input Tax A", "ASSET", assets)
    acct("Input Tax B", "ASSET", assets)
    liabilities = acct("Liabilities", "LIABILITY", root, True)
    acct("Accounts Payable", "PAYABLE", liabilities)
    for name in ("GST", "PST", "ECO"):
        acct(f"{name} Payable", "LIABILITY", liabilities)
    income = acct("Income", "INCOME", root, True)
    acct("Sales", "INCOME", income)
    acct("Consulting", "INCOME", income)
    expenses = acct("Expenses", "EXPENSE", root, True)
    acct("Services", "EXPENSE", expenses)
    acct("Supplies", "EXPENSE", expenses)
    book.save()
    book.close()

    gb = GnuCashBook(str(path))
    gb.create_customer(name="Acme Corp")
    gb.create_vendor(name="Supplier")
    for name, entries in _TABLES.items():
        gb.create_taxtable(name=name, entries=[
            {"type": t, "amount": a, "account": ac} for t, a, ac in entries
        ])

    for _ in range(count):
        kind = rng.choice(
            ["invoice"] * 5 + ["cn"] * 2 + ["bill"] * 2 + ["vendor_cn"]
        )
        if kind == "invoice":
            doc = gb.create_invoice(customer_id="000001")
            add = lambda **k: gb.add_invoice_entry(invoice_id=doc["id"], **k)
        elif kind == "cn":
            doc = gb.create_credit_note(owner_id="000001", owner_type="customer")
            add = lambda **k: gb.add_credit_note_entry(
                credit_note_id=doc["id"], owner_type="customer", **k)
        elif kind == "bill":
            doc = gb.create_bill(vendor_id="000001")
            add = lambda **k: gb.add_bill_entry(bill_id=doc["id"], **k)
        else:
            doc = gb.create_credit_note(owner_id="000001", owner_type="vendor")
            add = lambda **k: gb.add_credit_note_entry(
                credit_note_id=doc["id"], owner_type="vendor", **k)
        sales = kind in ("invoice", "cn")
        tables = _SALES if sales else _PURCHASE
        accounts = (
            ["Income:Sales", "Income:Consulting"] if sales
            else ["Expenses:Services", "Expenses:Supplies"]
        )
        doc_table = rng.choice(tables + [None])
        included = rng.random() < 0.35
        for _ in range(rng.choice([1, 1, 2, 3, 3, 5, 8])):
            kwargs = dict(
                account=rng.choice(accounts), description="line",
                quantity=rng.choice(_QUANTITIES), price=_price(rng),
            )
            table = (
                doc_table if rng.random() < 0.85
                else rng.choice(tables + [None])
            )
            if table:
                kwargs.update(taxtable=table, tax_included=included)
            add(**kwargs)

    con = sqlite3.connect(path)
    for (guid,) in con.execute(
        "SELECT guid FROM entries WHERE invoice IS NOT NULL ORDER BY "
        "date_entered, guid"
    ).fetchall():
        # Draw for every row so the stream doesn't depend on GUID order.
        roll = rng.random()
        dtype = rng.choice(["PERCENT", "PERCENT", "VALUE"])
        percent = rng.choice([(10, 1), (25, 2), (5, 1), (333, 100)])
        value = rng.choice([(1, 1), (333, 100), (5, 2)])
        how = rng.choice(["PRETAX", "SAMETIME", "POSTTAX"])
        if roll < 0.4:
            num, den = percent if dtype == "PERCENT" else value
            con.execute(
                "UPDATE entries SET i_discount_num = ?, "
                "i_discount_denom = ?, i_disc_type = ?, i_disc_how = ? "
                "WHERE guid = ?",
                (num, den, dtype, how, guid),
            )
    con.commit()
    con.close()


# ── reading a book: the rows the arithmetic sees ─────────────────────

def export_documents(path: Path) -> dict:
    """Every document's entry rows and the tax tables they name, as
    stored — enough to replay the arithmetic with no book."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    taxtables = {}
    for tt in con.execute("SELECT guid, name FROM taxtables"):
        taxtables[tt["guid"]] = [
            {
                "kind": "PERCENT" if e["type"] == 2 else "VALUE",
                "amount": f"{e['amount_num']}/{e['amount_denom']}",
                "account": e["account"],
            }
            for e in con.execute(
                "SELECT type, amount_num, amount_denom, account FROM "
                "taxtable_entries WHERE taxtable = ? ORDER BY id",
                (tt["guid"],),
            )
        ]
    documents = []
    for inv in con.execute(
        "SELECT i.guid, i.id, i.owner_type, c.fraction, "
        "COALESCE((SELECT int64_val FROM slots s WHERE s.obj_guid = i.guid "
        "          AND s.name = 'credit-note'), 0) AS is_cn "
        "FROM invoices i JOIN commodities c ON c.guid = i.currency "
        "ORDER BY i.owner_type, i.id"
    ).fetchall():
        is_bill = inv["owner_type"] != 2
        column = "bill" if is_bill else "invoice"
        rows = [
            {k: r[k] for k in ENTRY_COLUMNS}
            for r in con.execute(
                f"SELECT * FROM entries WHERE {column} = ? "
                "ORDER BY date_entered, guid",
                (inv["guid"],),
            )
        ]
        documents.append({
            "guid": inv["guid"],
            "id": inv["id"],
            "is_bill": is_bill,
            "is_credit_note": bool(inv["is_cn"]),
            "fraction": inv["fraction"],
            "rows": rows,
        })
    con.close()
    return {"taxtables": taxtables, "documents": documents}


# ── the oracle: GnuCash's engine, headless ───────────────────────────

def desktop_totals(path: Path, guids: list[str]) -> dict[str, tuple]:
    """``{guid: (total, subtotal, tax)}`` as exact ``Fraction``s, from
    GnuCash's own engine. Works on a COPY of ``path``."""
    cli = find_gnucash_cli()
    if cli is None:
        raise RuntimeError("gnucash-cli not found (set GNUCASH_CLI)")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "data").mkdir()
        (tmp / "config").mkdir()
        (tmp / "config" / "config-user.scm").write_text(
            f'(load "{REPORT}")\n'
        )
        (tmp / "guids.txt").write_text("\n".join(guids) + "\n")
        copy = tmp / "book.gnucash"
        shutil.copy(path, copy)
        out = tmp / "report.html"
        env = dict(
            os.environ,
            GNC_DATA_HOME=str(tmp / "data"),
            GNC_CONFIG_HOME=str(tmp / "config"),
            GUILE_AUTO_COMPILE="0",
            PARITY_GUIDS=str(tmp / "guids.txt"),
        )
        run = subprocess.run(
            [cli, "--report", "run", "--name", "Parity Totals",
             "--output-file", str(out), str(copy)],
            env=env, capture_output=True, text=True, timeout=900,
        )
        if run.returncode != 0 or not out.exists():
            raise RuntimeError(
                f"gnucash-cli failed ({run.returncode}):\n"
                f"{run.stdout[-2000:]}\n{run.stderr[-2000:]}"
            )
        html = out.read_text()
    found = {}
    for guid, rest in re.findall(r"PARITY\|([0-9a-f]{32})\|([^<\s]+)", html):
        if rest != "MISSING":
            found[guid] = tuple(Fraction(x) for x in rest.split("|"))
    return found


def desktop_version() -> str:
    cli = find_gnucash_cli()
    out = subprocess.run(
        [cli, "--version"], capture_output=True, text=True, timeout=120,
    ).stdout
    match = re.search(r"GnuCash\s+([0-9][^\s]*)", out)
    return match.group(1) if match else out.strip()


# ── the server's answer ──────────────────────────────────────────────

def server_totals(path: Path) -> dict[str, tuple]:
    """``{guid: (total, subtotal, tax)}`` through the real read path,
    ``_get_invoice_entries_and_total``."""
    from piecash.business.invoice import Invoice

    from gnucash_mcp.book import GnuCashBook

    gb = GnuCashBook(str(path))
    out = {}
    with gb.open(readonly=True) as book:
        for inv in book.session.query(Invoice).all():
            t = gb._get_invoice_entries_and_total(book, inv)
            out[inv.guid] = (
                t["grand_total"], t["subtotal"],
                sum(t["tax_breakdown"].values(), Decimal(0)),
            )
    return out


def record() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "oracle.gnucash"
        build_book(path, RECORDED_COUNT, RECORDED_SEED)
        data = export_documents(path)
        answers = desktop_totals(path, [d["guid"] for d in data["documents"]])
    for doc in data["documents"]:
        total, subtotal, tax = answers[doc.pop("guid")]
        doc["desktop"] = {
            "total": str(total), "subtotal": str(subtotal), "tax": str(tax),
        }
    header = {
        "gnucash": desktop_version(),
        "seed": RECORDED_SEED,
        "note": (
            "Entry rows as stored, and the totals GnuCash's engine gave "
            "(gncInvoiceGetTotal / TotalSubtotal / TotalTax). Each row "
            "is the values of 'columns', in order. Regenerate with: "
            "uv run python tests/fixtures/desktop_totals.py record"
        ),
        "columns": list(ENTRY_COLUMNS),
        "taxtables": data["taxtables"],
    }
    # One document per line: the file diffs by document.
    lines = [json.dumps(header, indent=1, sort_keys=True)[:-2] + ","]
    lines.append(' "documents": [')
    docs = []
    for doc in data["documents"]:
        doc["rows"] = [[r[c] for c in ENTRY_COLUMNS] for r in doc["rows"]]
        docs.append("  " + json.dumps(doc, sort_keys=True, separators=(",", ":")))
    lines.append(",\n".join(docs))
    lines.append(" ]\n}")
    RECORDED.write_text("\n".join(lines) + "\n")
    print(f"recorded {len(docs)} documents "
          f"from GnuCash {header['gnucash']} -> {RECORDED.name}")


def load_recorded() -> dict:
    """The recorded set, each row back as a ``{column: value}`` dict."""
    data = json.loads(RECORDED.read_text())
    columns = data["columns"]
    for doc in data["documents"]:
        doc["rows"] = [dict(zip(columns, row)) for row in doc["rows"]]
    return data


def live(count: int, seed: int) -> list[str]:
    """Mismatches between the server and the engine on a fresh book."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "oracle.gnucash"
        build_book(path, count, seed)
        ours = server_totals(path)
        theirs = desktop_totals(path, list(ours))
    problems = []
    for guid, mine in ours.items():
        if guid not in theirs:
            problems.append(f"{guid}: GnuCash did not answer")
        elif tuple(Fraction(x) for x in mine) != theirs[guid]:
            problems.append(
                f"{guid}: server {[str(x) for x in mine]} != GnuCash "
                f"{[str(float(x)) for x in theirs[guid]]}"
            )
    return problems


if __name__ == "__main__":
    if sys.argv[1:2] == ["record"]:
        record()
    elif sys.argv[1:2] == ["live"]:
        bad = live(int(sys.argv[2]), int(sys.argv[3]))
        print(f"{len(bad)} mismatches")
        for line in bad[:20]:
            print(" ", line)
        sys.exit(1 if bad else 0)
    else:
        sys.exit(__doc__)
