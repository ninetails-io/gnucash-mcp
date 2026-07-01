"""Behavior tests for batch transaction entry (create_transactions).

Spec: specs/BATCH_TRANSACTION_ENTRY_SPEC.md. These exercise the book
method directly (the tool wrapper + audit are tested separately).
"""

from datetime import date

import pytest

from gnucash_mcp.book import GnuCashBook


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
