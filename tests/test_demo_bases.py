"""Each demo builder's chart is valid on its own.

Nothing under ``samples/`` is committed any more: the chart is code,
and every book is regenerated from nothing by its builder, in CI for
the bundle and on any clone. ``--chart-only`` writes just the
commodities, chart of accounts, and slots (seconds, not a minute),
which is enough to pin that a builder starts from a sound chart in
the right currency before any transaction is generated.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts" / "synthetic_book"

BUILDERS = [
    ("alex", "build_alex.py", "USD", 100),
    ("lin-wei", "build_lin_wei.py", "CNY", 80),
    ("sabine", "build_sabine.py", "EUR", 110),
]


def _q(path: Path, sql: str):
    con = sqlite3.connect(str(path))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


@pytest.mark.parametrize("key, builder, currency, min_accounts", BUILDERS)
def test_chart_only_build_is_a_sound_empty_book(
    tmp_path: Path, key, builder, currency, min_accounts,
):
    out = tmp_path / f"{key}.gnucash"
    subprocess.run(
        [sys.executable, str(SCRIPTS / builder), "--chart-only", "--out", str(out)],
        check=True, capture_output=True, text=True, cwd=str(ROOT),
    )
    for table in ("transactions", "splits", "prices", "invoices",
                  "entries", "lots", "schedxactions", "budgets"):
        assert _q(out, f"SELECT COUNT(*) FROM {table}")[0][0] == 0, table
    cur = _q(out, """
        SELECT c.mnemonic FROM books b
        JOIN accounts r ON r.guid = b.root_account_guid
        JOIN commodities c ON c.guid = r.commodity_guid
    """)[0][0]
    assert cur == currency
    n = _q(out, "SELECT COUNT(*) FROM accounts WHERE account_type <> 'ROOT'")[0][0]
    assert n >= min_accounts
    assert _q(out, "SELECT COUNT(*) FROM gnclock")[0][0] == 0
    assert out.stat().st_size < 400_000
