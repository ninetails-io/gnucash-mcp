"""Num and document link, against GnuCash's own engine.

The same paycheck entered two ways on two copies of one book: through
the server, and through the engine function desktop calls for the same
entry (``fixtures/engine_act.scm`` verb ``txn``):

* batch entry ↔ the CSV importer, ``xaccTransSetNum``;
* statement entry ↔ the register's Num cell on the bank account's
  split, ``gnc_set_num_action``;

each with the book option "Use Split Action Field for Number" off and
on. The rows that carry the two fields — ``transactions.num``, each
split's ``action``, and the ``assoc_uri`` slot row in full — must
match. The option is set on both copies the same way, so the
option-on register scenario also shows the engine reading the
option's stored shape.

Regenerate after a GnuCash upgrade with
``uv run python -m tests.test_parity_num_link record``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.tools.core import _parse_transactions_tsv

from tests.test_statement_entry import (  # noqa: F401 — fixture
    _line,
    statement_book,
)
from tests.test_transaction_num_link import _num_on_split_action

_HERE = Path(__file__).resolve().parent
_RECORDED = _HERE / "fixtures" / "parity_num_link_engine.json"
sys.path.insert(0, str(_HERE / "fixtures"))
from engine_twin import engine_run, find_gnucash_cli  # noqa: E402

NUM = "1042"
LINK = "https://bank.example/stmt/1042"

# scenario → (desktop path, option on)
SCENARIOS = {
    "import_option_off": ("import", False),
    "import_option_on": ("import", True),
    "register_option_off": ("register", False),
    "register_option_on": ("register", True),
}


def _prepare(path: Path, scenario: str) -> None:
    if SCENARIOS[scenario][1]:
        _num_on_split_action(path)


def _server(path: Path, scenario: str) -> None:
    gb = GnuCashBook(str(path))
    if SCENARIOS[scenario][0] == "import":
        gb.create_transactions(_parse_transactions_tsv(
            "ref\tdate\tdescription\tnum\tlink\tamt\tacct\tamt\tacct\n"
            f"1\t2026-07-15\tPaycheck\t{NUM}\t{LINK}"
            "\t2000.00\tAssets:Checking\t-2000.00\tIncome\n"
        ))
    else:
        gb.enter_statement(
            "Assets:Checking", date(2026, 7, 31), "1000.00", "3000.00",
            [_line(
                "1", date(2026, 7, 15), "2000.00",
                description="Paycheck", num=NUM, link=LINK,
                splits=[{"account": "Income", "amount": "-2000.00"}],
            )],
            dry_run=False,
        )


def _engine(path: Path, scenario: str) -> list[str]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        guids = dict(con.execute("select name, guid from accounts"))
    finally:
        con.close()
    return engine_run(path, [
        f"txn|{SCENARIOS[scenario][0]}|{guids['Checking']}|"
        f"{guids['Income']}|-2000|15|7|2026|Paycheck|{NUM}|{LINK}"
    ])


def _rows(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        (guid, num), = con.execute(
            "select guid, num from transactions "
            "where description = 'Paycheck'"
        ).fetchall()
        actions = sorted(con.execute(
            "select a.name, s.action from splits s "
            "join accounts a on a.guid = s.account_guid "
            "where s.tx_guid = ?", (guid,),
        ).fetchall())
        link = con.execute(
            "select slot_type, int64_val, string_val, double_val, "
            "timespec_val, guid_val, numeric_val_num, "
            "numeric_val_denom, gdate_val from slots "
            "where obj_guid = ? and name = 'assoc_uri'", (guid,),
        ).fetchall()
    finally:
        con.close()
    return {
        "num": num,
        "actions": [list(r) for r in actions],
        "assoc_uri": [list(r) for r in link],
    }


def _recorded() -> dict:
    return json.loads(_RECORDED.read_text())


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_server_writes_what_the_engine_writes(statement_book, scenario):
    _prepare(statement_book, scenario)
    _server(statement_book, scenario)
    assert _rows(statement_book) == _recorded()[scenario]["rows"]


@pytest.mark.skipif(
    find_gnucash_cli() is None, reason="gnucash-cli not on this machine",
)
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_recording_is_what_the_engine_writes_today(statement_book, scenario):
    _prepare(statement_book, scenario)
    results = _engine(statement_book, scenario)
    assert {"results": results, "rows": _rows(statement_book)} == (
        _recorded()[scenario]
    )


def test_the_option_moves_num_only_in_the_register():
    """The finding, read off the recording: the importer ignores the
    option; the register puts Num on the bank split's action."""
    got = {k: v["rows"] for k, v in _recorded().items()}
    for scenario in ("import_option_off", "import_option_on",
                     "register_option_off"):
        assert got[scenario]["num"] == NUM
        assert {a for _acct, a in got[scenario]["actions"]} == {""}
    assert got["register_option_on"]["num"] == ""
    assert got["register_option_on"]["actions"] == [
        ["Checking", NUM], ["Income", ""],
    ]


def record() -> None:
    import tempfile

    import tests.test_statement_entry as fixtures

    out = {}
    for scenario in sorted(SCENARIOS):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixtures.statement_book.__wrapped__(Path(tmp))
            _prepare(path, scenario)
            results = _engine(path, scenario)
            out[scenario] = {"results": results, "rows": _rows(path)}
        print(f"recorded {scenario}: {out[scenario]}")
    _RECORDED.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["record"]:
        record()
    else:
        sys.exit("usage: test_parity_num_link.py record")
