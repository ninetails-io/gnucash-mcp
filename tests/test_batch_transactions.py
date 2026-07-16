"""Behavior tests for batch transaction entry (create_transactions).

Spec: specs/BATCH_TRANSACTION_ENTRY_SPEC.md. These exercise the book
method directly (the tool wrapper + audit are tested separately).
"""

from datetime import date

import pytest

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.tools.core import _parse_transactions_tsv


def _parse(tsv: str) -> list[dict]:
    """Parse a header-bearing TSV string into a list of dicts."""
    if not tsv:
        return []
    lines = tsv.split("\n")
    header = lines[0].split("\t")
    return [dict(zip(header, ln.split("\t"))) for ln in lines[1:]]


def _txn(ref, desc, amt, d=date(2026, 5, 21),
         acct_from="Assets:Checking", acct_to="Expenses:Groceries"):
    return {
        "ref": str(ref),
        "date": d,
        "description": desc,
        "splits": [
            {"account": acct_from, "amount": f"-{amt}"},
            {"account": acct_to, "amount": str(amt)},
        ],
    }


def _seed_rent(gc):
    gc.create_transaction(
        description="Rent",
        splits=[
            {"account": "Assets:Checking", "amount": "-1800"},
            {"account": "Expenses:Groceries", "amount": "1800"},
        ],
        trans_date=date(2026, 5, 20),
    )


def _descriptions(gc):
    return {t["description"] for t in gc.list_transactions(compact=False)["transactions"]}


class TestBatchTsvParser:
    """Header-driven layout of the batch TSV (tool-layer parser).

    The header declares the shape: legacy headers parse as
    positional (amount, account) pairs exactly as before; ``memo``
    split columns switch to triples; a ``notes`` token in column 4
    inserts a per-transaction notes column.
    """

    def test_legacy_pairs_unchanged(self):
        tsv = (
            "ref\tdate\tdescription\tamt1\tacct1\tamt2\tacct2\n"
            "1\t2026-05-21\tGas\t-54.19\tAssets:Checking"
            "\t54.19\tExpenses:Auto:Fuel"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"] == [
            {"account": "Assets:Checking", "amount": "-54.19"},
            {"account": "Expenses:Auto:Fuel", "amount": "54.19"},
        ]
        assert "notes" not in rows[0]

    def test_arbitrary_legacy_header_still_pairs(self):
        """Pre-extension callers could put anything in the header
        (it was purely decorative) — those parse as pairs still."""
        tsv = (
            "col_a\tcol_b\tcol_c\tcol_d\tcol_e\n"
            "1\t2026-05-21\tGas\t-5.00\tAssets:Checking"
            "\t5.00\tExpenses:Groceries"
        )
        rows = _parse_transactions_tsv(tsv)
        assert len(rows[0]["splits"]) == 2
        assert "memo" not in rows[0]["splits"][0]

    def test_memo_header_switches_to_triples(self):
        tsv = (
            "ref\tdate\tdescription\tamt1\tacct1\tmemo1"
            "\tamt2\tacct2\tmemo2\n"
            "1\t2026-05-21\tGas\t-54.19\tAssets:Checking\tcard #4471"
            "\t54.19\tExpenses:Auto:Fuel\t"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"] == [
            {
                "account": "Assets:Checking", "amount": "-54.19",
                "memo": "card #4471",
            },
            # empty memo cell → no memo key
            {"account": "Expenses:Auto:Fuel", "amount": "54.19"},
        ]

    def test_notes_column(self):
        tsv = (
            "ref\tdate\tdescription\tnotes\tamt1\tacct1\tamt2\tacct2\n"
            "1\t2026-05-21\tGas\tfrom July statement\t-5.00"
            "\tAssets:Checking\t5.00\tExpenses:Groceries\n"
            "2\t2026-05-22\tCoffee\t\t-4.00\tAssets:Checking"
            "\t4.00\tExpenses:Groceries"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["notes"] == "from July statement"
        assert "notes" not in rows[1]  # empty cell → absent

    def test_notes_and_memos_combined(self):
        tsv = (
            "ref\tdate\tdescription\tnotes\tamt\tacct\tmemo"
            "\tamt\tacct\tmemo\n"
            "1\t2026-05-21\tGas\tstatement p.2\t-5.00"
            "\tAssets:Checking\tcard #4471\t5.00"
            "\tExpenses:Groceries\t"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["notes"] == "statement p.2"
        assert rows[0]["splits"][0]["memo"] == "card #4471"

    def test_ragged_rows_with_triples(self):
        tsv = (
            "ref\tdate\tdescription\tamt\tacct\tmemo\n"
            "1\t2026-05-21\tTwo splits\t-5.00\tAssets:Checking\ta"
            "\t5.00\tExpenses:Groceries\tb\n"
            "2\t2026-05-21\tThree splits\t-9.00\tAssets:Checking\t"
            "\t4.00\tExpenses:Groceries\t\t5.00\tExpenses:Dining\tc"
        )
        rows = _parse_transactions_tsv(tsv)
        assert len(rows[0]["splits"]) == 2
        assert len(rows[1]["splits"]) == 3
        assert rows[1]["splits"][2]["memo"] == "c"

    def test_triple_count_mismatch_names_the_tab(self):
        # Last memo cell's tab dropped — THE likely mistake.
        tsv = (
            "ref\tdate\tdescription\tamt\tacct\tmemo\n"
            "1\t2026-05-21\tGas\t-5.00\tAssets:Checking\tcard"
            "\t5.00\tExpenses:Groceries"
        )
        with pytest.raises(ValueError, match="memo cell's tab"):
            _parse_transactions_tsv(tsv)


class TestBatchCreate:
    def test_all_valid_atomic_create(self, test_book):
        gc = GnuCashBook(str(test_book))
        env = gc.create_transactions([
            _txn(1, "Groceries A", "50.00"),
            _txn(2, "Groceries B", "25.00"),
        ])
        rows = _parse(env["results"])
        assert len(rows) == 2
        assert all(r["status"] == "created" for r in rows)
        assert all(r["txn_guid"] for r in rows)
        assert env["duplicates"] == ""
        assert {"Groceries A", "Groceries B"} <= _descriptions(gc)

    def test_structural_failure_aborts(self, test_book):
        gc = GnuCashBook(str(test_book))
        bad = _txn(2, "Unbalanced", "10.00")
        bad["splits"][1]["amount"] = "999"  # breaks sum-to-zero
        env = gc.create_transactions([_txn(1, "Good one", "50.00"), bad])
        rows = {r["ref"]: r for r in _parse(env["results"])}
        assert rows["2"]["status"] == "rejected"
        assert rows["1"]["status"] == "rejected"
        assert rows["1"]["reason"] == "batch_aborted"
        assert "Good one" not in _descriptions(gc)  # nothing written

    def test_on_error_skip_keeps_good(self, test_book):
        gc = GnuCashBook(str(test_book))
        bad = _txn(2, "Unbalanced", "10.00")
        bad["splits"][1]["amount"] = "999"
        env = gc.create_transactions(
            [_txn(1, "Good one", "50.00"), bad], on_error="skip",
        )
        rows = {r["ref"]: r for r in _parse(env["results"])}
        assert rows["1"]["status"] == "created"
        assert rows["2"]["status"] == "rejected"
        assert "Good one" in _descriptions(gc)

    def test_duplicate_rejects_only_its_row(self, test_book):
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        env = gc.create_transactions([
            _txn(1, "Rent", "1800", d=date(2026, 5, 20)),  # HIGH dup
            _txn(2, "Fresh", "30.00"),
        ])
        rows = {r["ref"]: r for r in _parse(env["results"])}
        assert rows["1"]["status"] == "rejected"
        assert rows["1"]["reason"] == "duplicate_detected"
        assert rows["2"]["status"] == "created"
        dup_rows = _parse(env["duplicates"])
        assert dup_rows and all(d["ref"] == "1" for d in dup_rows)

    def test_force_overrides_duplicate(self, test_book):
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        env = gc.create_transactions(
            [_txn(1, "Rent", "1800", d=date(2026, 5, 20))], force=True,
        )
        rows = _parse(env["results"])
        assert rows[0]["status"] == "created"

    def test_dry_run_writes_nothing(self, test_book):
        gc = GnuCashBook(str(test_book))
        env = gc.create_transactions(
            [_txn(1, "Preview", "50.00")], dry_run=True,
        )
        rows = _parse(env["results"])
        assert rows[0]["status"] == "would_create"
        assert "Preview" not in _descriptions(gc)

    def test_notes_and_memos_round_trip(self, test_book):
        gc = GnuCashBook(str(test_book))
        t = _txn(1, "Gas fill-up", "54.19")
        t["notes"] = "from July statement, p.2"
        t["splits"][0]["memo"] = "card #4471"
        env = gc.create_transactions([t])
        rows = _parse(env["results"])
        assert rows[0]["status"] == "created"

        txn = gc.get_transaction(rows[0]["txn_guid"])
        assert txn["notes"] == "from July statement, p.2"
        memos = {s["account"]: s.get("memo", "") for s in txn["splits"]}
        assert memos["Assets:Checking"] == "card #4471"
        assert memos["Expenses:Groceries"] == ""

    def test_plain_batch_shape_unchanged(self, test_book):
        gc = GnuCashBook(str(test_book))
        env = gc.create_transactions([_txn(1, "Plain", "10.00")])
        rows = _parse(env["results"])
        txn = gc.get_transaction(rows[0]["txn_guid"])
        assert "notes" not in txn
        assert all(not s.get("memo") for s in txn["splits"])

    def test_checksum_dupcount_equals_table_rows(self, test_book):
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        env = gc.create_transactions([
            _txn(1, "Rent", "1800", d=date(2026, 5, 20)),
            _txn(2, "Fresh", "30.00"),
        ], force=True)
        results = _parse(env["results"])
        dup_total = sum(int(r["dup_count"]) for r in results if r["dup_count"])
        assert dup_total == len(_parse(env["duplicates"]))

    def test_bad_on_error_raises(self, test_book):
        gc = GnuCashBook(str(test_book))
        with pytest.raises(ValueError):
            gc.create_transactions([_txn(1, "x", "1")], on_error="bad")
