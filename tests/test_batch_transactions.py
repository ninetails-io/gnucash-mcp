"""Behavior tests for batch transaction entry (create_transactions).

Spec: specs/BATCH_TRANSACTION_ENTRY_SPEC.md. These exercise the book
method directly (the tool wrapper + audit are tested separately).
"""

from datetime import date
from decimal import Decimal

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

    def test_unknown_header_columns_reject_by_name(self):
        """6c (bookkeeper finding): now that the header is load-
        bearing, unknown columns must fail on the FORMAT with the
        offending name — falling back to positional pairs misparsed
        rows into raw decimal errors."""
        tsv = (
            "ref\tdate\tdescription\tamt1\tacct1\tcurrency1"
            "\tamt2\tacct2\tcurrency2\n"
            "1\t2026-05-21\tGas\t-5.00\tAssets:Checking\tUSD"
            "\t5.00\tExpenses:Groceries\tUSD"
        )
        with pytest.raises(ValueError, match="unrecognized column 'currency1'"):
            _parse_transactions_tsv(tsv)

    def test_typoed_memo_token_rejects_instead_of_misparsing(self):
        tsv = (
            "ref\tdate\tdescription\tamt1\tacct1\tmeno1\n"
            "1\t2026-05-21\tGas\t-5.00\tAssets:Checking\toops"
            "\t5.00\tExpenses:Groceries\t"
        )
        with pytest.raises(ValueError, match="unrecognized column 'meno1'"):
            _parse_transactions_tsv(tsv)

    def test_wrong_fixed_prefix_rejects(self):
        tsv = (
            "col_a\tcol_b\tcol_c\tcol_d\tcol_e\n"
            "1\t2026-05-21\tGas\t-5.00\tAssets:Checking"
            "\t5.00\tExpenses:Groceries"
        )
        with pytest.raises(ValueError, match="must start with ref, date"):
            _parse_transactions_tsv(tsv)

    def test_trailing_header_tab_tolerated(self):
        tsv = (
            "ref\tdate\tdescription\tamt1\tacct1\tamt2\tacct2\t\n"
            "1\t2026-05-21\tGas\t-5.00\tAssets:Checking"
            "\t5.00\tExpenses:Groceries"
        )
        rows = _parse_transactions_tsv(tsv)
        assert len(rows[0]["splits"]) == 2

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

    def test_qty_header_switches_to_quantity_groups(self):
        tsv = (
            "ref\tdate\tdescription\tamt1\tacct1\tqty1"
            "\tamt2\tacct2\tqty2\n"
            "1\t2026-07-01\tVFIFX Purchase\t-505.17\tAssets:Checking\t"
            "\t505.17\tAssets:401k:VFIFX\t7.7936"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"] == [
            # empty qty cell → same-currency split, no quantity key
            {"account": "Assets:Checking", "amount": "-505.17"},
            {
                "account": "Assets:401k:VFIFX", "amount": "505.17",
                "quantity": "7.7936",
            },
        ]

    def test_quad_group_memo_and_qty(self):
        tsv = (
            "ref\tdate\tdescription\tamt\tacct\tmemo\tqty\n"
            "1\t2026-07-01\tShares\t-10.00\tAssets:Checking\tsettle\t"
            "\t10.00\tAssets:401k:VFIFX\t\t0.153"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"][0] == {
            "account": "Assets:Checking", "amount": "-10.00",
            "memo": "settle",
        }
        assert rows[0]["splits"][1] == {
            "account": "Assets:401k:VFIFX", "amount": "10.00",
            "quantity": "0.153",
        }

    def test_header_first_group_fixes_field_order(self):
        """qty-before-memo in the header → qty-before-memo in rows.
        The header is the schema for ORDER, not just presence."""
        tsv = (
            "ref\tdate\tdescription\tamt\tacct\tqty\tmemo\n"
            "1\t2026-07-01\tShares\t10.00\tAssets:401k:VFIFX"
            "\t0.153\tbuy\t-10.00\tAssets:Checking\t\tsettle"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"][0] == {
            "account": "Assets:401k:VFIFX", "amount": "10.00",
            "quantity": "0.153", "memo": "buy",
        }
        assert rows[0]["splits"][1] == {
            "account": "Assets:Checking", "amount": "-10.00",
            "memo": "settle",
        }

    def test_unknown_split_token_in_extension_header_rejects(self):
        tsv = (
            "ref\tdate\tdescription\tamt\tacct\tmemo\tcurrency\n"
            "1\t2026-07-01\tX\t-1\tA\tm\tEUR\t1\tB\t\t"
        )
        with pytest.raises(ValueError, match="unrecognized column"):
            _parse_transactions_tsv(tsv)

    def test_row_may_end_after_last_required_field(self):
        """Trailing optional cells may be omitted — a triple-header
        row ending right after its last account parses with an empty
        memo (was THE most common formatting mistake; now shorthand)."""
        tsv = (
            "ref\tdate\tdescription\tamt\tacct\tmemo\n"
            "1\t2026-05-21\tGas\t-5.00\tAssets:Checking\tcard"
            "\t5.00\tExpenses:Groceries"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"] == [
            {
                "account": "Assets:Checking", "amount": "-5.00",
                "memo": "card",
            },
            {"account": "Expenses:Groceries", "amount": "5.00"},
        ]

    def test_quad_row_may_omit_both_trailing_optionals(self):
        tsv = (
            "ref\tdate\tdescription\tamt\tacct\tmemo\tqty\n"
            "1\t2026-05-21\tCoffee\t-4.50\tAssets:Checking\t\t"
            "\t4.50\tExpenses:Dining"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"][1] == {
            "account": "Expenses:Dining", "amount": "4.50",
        }

    def test_quad_row_may_omit_just_the_final_optional(self):
        # Ends after memo — only qty omitted.
        tsv = (
            "ref\tdate\tdescription\tamt\tacct\tmemo\tqty\n"
            "1\t2026-05-21\tCoffee\t-4.50\tAssets:Checking\t\t"
            "\t4.50\tExpenses:Dining\ttip included"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"][1] == {
            "account": "Expenses:Dining", "amount": "4.50",
            "memo": "tip included",
        }

    def test_omission_respects_header_field_order(self):
        # qty before memo in the header → ending after qty omits
        # only the memo.
        tsv = (
            "ref\tdate\tdescription\tamt\tacct\tqty\tmemo\n"
            "1\t2026-07-01\tShares\t-10.00\tAssets:Checking\t\tsettle"
            "\t10.00\tAssets:401k:VFIFX\t0.153"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"][1] == {
            "account": "Assets:401k:VFIFX", "amount": "10.00",
            "quantity": "0.153",
        }

    def test_splitless_row_parses_as_auto_fill_request(self):
        tsv = (
            "ref\tdate\tdescription\tamt1\tacct1\tamt2\tacct2\n"
            "1\t2026-07-01\tRent\n"
            "2\t2026-07-01\tNetflix\t\t"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"] == []
        # Stray trailing tabs stripped — still an auto-fill request.
        assert rows[1]["splits"] == []

    def test_minimal_header_for_all_autofill_batch(self):
        """ref/date/description with no split columns at all — the
        natural header for a pure auto-fill batch (found live: the
        layout validator used to reject the empty split region)."""
        tsv = (
            "ref\tdate\tdescription\n"
            "1\t2026-08-01\tMortgage Payment\n"
            "2\t2026-08-01\tNetflix"
        )
        rows = _parse_transactions_tsv(tsv)
        assert [r["splits"] for r in rows] == [[], []]

    def test_splitless_row_with_notes_column(self):
        tsv = (
            "ref\tdate\tdescription\tnotes\tamt\tacct\n"
            "1\t2026-07-01\tRent\tJuly\n"
            "2\t2026-07-01\tNetflix"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"] == [] and rows[0]["notes"] == "July"
        assert rows[1]["splits"] == [] and "notes" not in rows[1]

    def test_omitting_required_fields_still_rejects(self):
        # Row ends after an amount — the account is missing; that's
        # a misalignment, not an optional-cell shorthand.
        tsv = (
            "ref\tdate\tdescription\tamt\tacct\tmemo\n"
            "1\t2026-05-21\tGas\t-5.00\tAssets:Checking\tcard\t5.00"
        )
        with pytest.raises(ValueError, match="missing account"):
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

    def test_cross_commodity_quantity_round_trip(self, multi_currency_book):
        """A batch row with an explicit quantity on a non-default-
        commodity account books value in the transaction currency
        and quantity in the account's commodity — same contract as
        create_transaction, same validation chokepoint."""
        gc = GnuCashBook(str(multi_currency_book))
        env = gc.create_transactions([{
            "ref": "1",
            "date": date(2026, 7, 1),
            "description": "EUR top-up",
            "splits": [
                {"account": "Assets:Checking", "amount": "-1100.00"},
                {
                    "account": "Assets:Euro Savings",
                    "amount": "1100.00",
                    "quantity": "1000.00",
                    "memo": "wire ref 4471",
                },
            ],
        }])
        rows = _parse(env["results"])
        assert rows[0]["status"] == "created"
        txn = gc.get_transaction(rows[0]["txn_guid"])
        eur = [s for s in txn["splits"]
               if s["account"] == "Assets:Euro Savings"][0]
        assert Decimal(eur["value"]) == Decimal("1100.00")
        assert Decimal(eur["quantity"]) == Decimal("1000.00")
        assert eur["memo"] == "wire ref 4471"

    def test_missing_quantity_on_foreign_account_rejects_row(
        self, multi_currency_book,
    ):
        gc = GnuCashBook(str(multi_currency_book))
        env = gc.create_transactions([{
            "ref": "1",
            "date": date(2026, 7, 1),
            "description": "EUR top-up sans qty",
            "splits": [
                {"account": "Assets:Checking", "amount": "-1100.00"},
                {"account": "Assets:Euro Savings", "amount": "1100.00"},
            ],
        }])
        rows = _parse(env["results"])
        assert rows[0]["status"] == "rejected"
        assert "quantity" in rows[0]["reason"].lower()

    def test_auto_fill_reproduces_last_shape(self, test_book):
        gc = GnuCashBook(str(test_book))
        gc.create_transaction(
            description="Rent",
            splits=[
                {"account": "Assets:Checking", "amount": "-1800",
                 "memo": "unit 4B"},
                {"account": "Expenses:Groceries", "amount": "1800"},
            ],
            trans_date=date(2026, 5, 20),
        )
        env = gc.create_transactions([{
            "ref": "1", "date": date(2026, 6, 20),
            "description": "Rent", "splits": [],
        }])
        rows = _parse(env["results"])
        assert rows[0]["status"] == "created"
        assert rows[0]["reason"].startswith("auto_filled_from:")

        txn = gc.get_transaction(rows[0]["txn_guid"])
        by_acct = {s["account"]: s for s in txn["splits"]}
        assert by_acct["Assets:Checking"]["value"].startswith("-1800")
        assert by_acct["Assets:Checking"]["memo"] == "unit 4B"
        assert txn["date"] == "2026-06-20"

    def test_auto_fill_no_match_rejects_row(self, test_book):
        gc = GnuCashBook(str(test_book))
        env = gc.create_transactions([
            {"ref": "1", "date": date(2026, 6, 20),
             "description": "Nothing like this exists", "splits": []},
            _txn(2, "Real one", "25.00"),
        ], on_error="skip")
        rows = {r["ref"]: r for r in _parse(env["results"])}
        assert rows["1"]["status"] == "rejected"
        assert "auto-fill" in rows["1"]["reason"]
        assert rows["2"]["status"] == "created"

    def test_auto_fill_no_match_aborts_batch_by_default(self, test_book):
        gc = GnuCashBook(str(test_book))
        env = gc.create_transactions([
            {"ref": "1", "date": date(2026, 6, 20),
             "description": "Nothing like this exists", "splits": []},
            _txn(2, "Casualty", "25.00"),
        ])
        rows = {r["ref"]: r for r in _parse(env["results"])}
        assert rows["2"]["reason"] == "batch_aborted"
        assert "Casualty" not in _descriptions(gc)

    def test_auto_fill_dry_run_carries_marker(self, test_book):
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        before = len(
            gc.list_transactions(compact=False)["transactions"]
        )
        env = gc.create_transactions([{
            "ref": "1", "date": date(2026, 6, 20),
            "description": "Rent", "splits": [],
        }], dry_run=True)
        rows = _parse(env["results"])
        assert rows[0]["status"] == "would_create"
        assert rows[0]["reason"].startswith("auto_filled_from:")
        after = len(
            gc.list_transactions(compact=False)["transactions"]
        )
        assert after == before  # nothing written

    def test_auto_fill_still_screened_for_duplicates(self, test_book):
        """Auto-filling 'Rent' dated within the duplicate window of
        the source rejects like any other duplicate — auto-fill is
        not a bypass."""
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)  # 2026-05-20
        env = gc.create_transactions([{
            "ref": "1", "date": date(2026, 5, 21),
            "description": "Rent", "splits": [],
        }])
        rows = _parse(env["results"])
        assert rows[0]["status"] == "rejected"
        assert rows[0]["reason"] == "duplicate_detected"

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


class TestBatchDryRunUpgrades:
    """Ruling 7 (statement spec §9): the batch dry-run inherits the
    rehearsal surface — summary header (counts + homework, never a
    clearance), max_confidence results column, per-account effects
    footer."""

    def test_summary_counts_and_homework(self, test_book):
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        env = gc.create_transactions(
            [_txn(1, "Rent", "1800", d=date(2026, 5, 20)),
             _txn(2, "Fresh Thing", "10.00")],
            dry_run=True,
        )
        assert "Dry run: 2 rows — " in env["summary"]
        assert "1 would_create, 0 review_required, 1 rejected" in \
            env["summary"]
        assert "duplicate candidates" in env["summary"]
        # The clearance principle: no verdict vocabulary.
        assert "safe" not in env["summary"].lower()
        assert "blocking" not in env["summary"].lower()

    def test_summary_verified_empty(self, test_book):
        gc = GnuCashBook(str(test_book))
        env = gc.create_transactions(
            [_txn(1, "Fresh Thing", "10.00")], dry_run=True,
        )
        assert "No duplicate candidates." in env["summary"]

    def test_max_confidence_column(self, test_book):
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        env = gc.create_transactions(
            [_txn(1, "Rent", "1800", d=date(2026, 5, 20)),
             _txn(2, "Fresh Thing", "10.00")],
            dry_run=True,
        )
        rows = {r["ref"]: r for r in _parse(env["results"])}
        assert rows["1"]["max_confidence"] == "HIGH"
        assert rows["2"]["max_confidence"] == ""

    def test_max_confidence_on_commit_rows_too(self, test_book):
        """One results shape across modes — the column isn't
        dry-run-only."""
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        env = gc.create_transactions(
            [_txn(1, "Rent", "1800", d=date(2026, 5, 20))],
            force=True,
        )
        rows = _parse(env["results"])
        assert rows[0]["status"] == "created"
        assert rows[0]["max_confidence"] == "HIGH"

    def test_effects_footer(self, test_book):
        gc = GnuCashBook(str(test_book))
        env = gc.create_transactions(
            [_txn(1, "A", "10.00"), _txn(2, "B", "5.50")],
            dry_run=True,
        )
        effects = {
            f[0]: f[1] for f in
            (ln.split("\t") for ln in
             env["effects"].splitlines()[1:])
        }
        assert effects["Assets:Checking"] == "-15.50"
        assert effects["Expenses:Groceries"] == "15.50"

    def test_commit_has_no_summary_or_effects(self, test_book):
        gc = GnuCashBook(str(test_book))
        env = gc.create_transactions([_txn(1, "A", "10.00")])
        assert "summary" not in env
        assert "effects" not in env

    def test_review_required_status(self, test_book):
        """Ruling 8: a non-blocking (MEDIUM) candidate turns
        would_create into review_required — a projected-action
        label must not masquerade as clearance."""
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        # Same description + date, amount off by > $1: desc+date
        # MEDIUM, non-blocking.
        env = gc.create_transactions(
            [_txn(1, "Rent", "1700", d=date(2026, 5, 21))],
            dry_run=True,
        )
        rows = _parse(env["results"])
        assert rows[0]["status"] == "review_required"
        assert rows[0]["max_confidence"] == "MEDIUM"
        assert "1 rows are review_required" in env["summary"]

    def test_duplicates_table_is_self_contained(self, test_book):
        """Ruling 9: the candidate row carries proposed + existing
        values, deltas, category legs, and a split_match verdict —
        no join back to the caller's input."""
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        env = gc.create_transactions(
            [_txn(1, "Rent", "1700", d=date(2026, 5, 21))],
            dry_run=True,
        )
        lines = env["duplicates"].splitlines()
        row = dict(zip(lines[0].split("\t"), lines[1].split("\t")))
        assert row["desc_new"] == "Rent"
        assert row["desc_old"] == "Rent"
        assert row["date_delta_days"] == "-1"
        # Amounts anchor to the SIGNED CATEGORY primary (R3 P5):
        # the expense leg, +1700 vs +1800.
        assert row["amt_new"] == "1700"
        assert row["amt_old"] == "1800"
        assert row["amt_delta"] == "100"
        assert row["cat_new"] == "Expenses:Groceries=1700"
        assert row["cat_old"] == "Expenses:Groceries=1800"
        assert row["split_match"] == "partial"
        # T7 (statement round): the shared table's state column is
        # populated on the batch surface too — most-anchored split
        # state of the candidate transaction.
        assert row["state"] == "n"

    def test_cross_frame_blanks_delta_and_names_currency(
        self, multi_currency_book
    ):
        """Round-two renderer finding: comparability is
        candidate-vs-PROPOSAL currency. A default-currency proposal
        against an EUR candidate must blank amt_delta and name EUR
        in cur — 100 EUR vs 100 USD is not a twin."""
        gc = GnuCashBook(str(multi_currency_book))
        gc.create_transaction(
            description="Consulting Invoice",
            currency="EUR",
            splits=[
                {"account": "Assets:Euro Savings",
                 "amount": "-100.00"},
                {"account": "Expenses:Groceries",
                 "amount": "100.00", "quantity": "108.00"},
            ],
            trans_date=date(2026, 5, 21),
            check_duplicates=False,
        )
        env = gc.create_transactions(
            [_txn(1, "Consulting Invoice", "100.00")],
            dry_run=True,
        )
        lines = env["duplicates"].splitlines()
        row = dict(zip(lines[0].split("\t"), lines[1].split("\t")))
        assert row["cur"] == "EUR"
        assert row["amt_delta"] == ""

    def test_refund_is_not_the_payments_twin(self, test_book):
        """R3 P5 (blocker-class): signed amounts must live in the
        SCORER, not just the display. A +104 refund was HIGH-
        blocked as a duplicate of the -104 payment, with
        split_match 'exact' on inverted legs. The direction anchor
        is the signed CATEGORY primary — a balanced transaction
        always carries both signs, so the funding leg can't carry
        the signal."""
        gc = GnuCashBook(str(test_book))
        gc.create_transaction(
            description="Amazon",
            splits=[
                {"account": "Assets:Checking", "amount": "-104.00"},
                {"account": "Expenses:Groceries",
                 "amount": "104.00"},
            ],
            trans_date=date(2026, 5, 20),
        )
        env = gc.create_transactions(
            [{
                "ref": "1", "date": date(2026, 5, 21),
                "description": "Amazon Refund",
                "splits": [
                    {"account": "Assets:Checking",
                     "amount": "104.00"},
                    {"account": "Expenses:Groceries",
                     "amount": "-104.00"},
                ],
            }],
            dry_run=True,
        )
        rows = _parse(env["results"])
        # Surfaced for review (desc+date), never HIGH-blocked.
        assert rows[0]["status"] == "review_required"
        assert rows[0]["max_confidence"] == "MEDIUM"
        lines = env["duplicates"].splitlines()
        row = dict(zip(lines[0].split("\t"), lines[1].split("\t")))
        assert "A" not in row["signals"]
        assert Decimal(row["amt_new"]) == Decimal("-104")
        assert Decimal(row["amt_old"]) == Decimal("104")
        assert Decimal(row["amt_delta"]) == Decimal("208")
        assert row["split_match"] == "partial"

    def test_transfer_duplicates_still_match_on_magnitude(
        self, test_book
    ):
        """The fallback: transfer-shaped rows (no category leg)
        keep magnitude comparison, so a true transfer duplicate
        still blocks."""
        gc = GnuCashBook(str(test_book))
        gc.create_transaction(
            description="Card Payment",
            splits=[
                {"account": "Assets:Checking", "amount": "-500.00"},
                {"account": "Equity:Opening Balance",
                 "amount": "500.00"},
            ],
            trans_date=date(2026, 5, 20),
        )
        env = gc.create_transactions(
            [{
                "ref": "1", "date": date(2026, 5, 20),
                "description": "Card Payment",
                "splits": [
                    {"account": "Assets:Checking",
                     "amount": "-500.00"},
                    {"account": "Equity:Opening Balance",
                     "amount": "500.00"},
                ],
            }],
            dry_run=True,
        )
        rows = _parse(env["results"])
        assert rows[0]["status"] == "rejected"
        assert rows[0]["max_confidence"] == "HIGH"

    def test_reconciled_candidate_shows_state_y(self, test_book):
        """The bookkeeper's fourth verification probe: a reconciled
        duplicate candidate reads y — entered AND tied, decisive."""
        import piecash as pc
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        b = pc.open_book(str(test_book), readonly=False,
                         do_backup=False)
        for txn in b.transactions:
            if txn.description == "Rent":
                for s in txn.splits:
                    if s.account.name == "Checking":
                        s.reconcile_state = "y"
        b.save()
        b.close()
        env = gc.create_transactions(
            [_txn(1, "Rent", "1800", d=date(2026, 5, 20))],
            dry_run=True,
        )
        lines = env["duplicates"].splitlines()
        row = dict(zip(lines[0].split("\t"), lines[1].split("\t")))
        assert row["state"] == "y"

    def test_split_match_none_on_distinct_categories(
        self, test_book
    ):
        """The Chewy/fuel case: MEDIUM on date+amount, decisively
        distinct on category."""
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        env = gc.create_transactions(
            [_txn(1, "Rent", "1799.50", d=date(2026, 5, 21),
                  acct_to="Income:Salary")],
            dry_run=True,
        )
        lines = env["duplicates"].splitlines()
        row = dict(zip(lines[0].split("\t"), lines[1].split("\t")))
        assert row["split_match"] == "none"


class TestBatchCurColumn:
    """The ``cur`` column sets a ROW's transaction currency.

    Motivating case: a USD-to-USD transfer inside a CNY/USD-default
    book — with no book-currency leg, the pinned default frame
    forced fabricated conversion values on both sides of an event
    containing no conversion at all."""

    def test_cur_parses_after_description(self):
        tsv = (
            "ref\tdate\tdescription\tcur\tamt1\tacct1\tamt2\tacct2\n"
            "1\t2026-07-15\tCard Payment\tUSD\t-500\tA:U\t500\tL:U\n"
            "2\t2026-07-16\tLocal Rent\t\t-900\tA:C\t900\tE:R"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["currency"] == "USD"
        assert "currency" not in rows[1]  # empty cell = book default

    def test_cur_and_notes_in_either_order(self):
        for header in (
            "ref\tdate\tdescription\tnotes\tcur\tamt1\tacct1\tamt2\tacct2",
            "ref\tdate\tdescription\tcur\tnotes\tamt1\tacct1\tamt2\tacct2",
        ):
            cells = {"notes": "why", "cur": "eur"}
            order = [t for t in header.split("\t")[3:5]]
            row = "1\t2026-07-15\tX\t" + "\t".join(
                cells[c] for c in order
            ) + "\t-10\tA\t10\tB"
            rows = _parse_transactions_tsv(header + "\n" + row)
            assert rows[0]["currency"] == "EUR"
            assert rows[0]["notes"] == "why"

    def test_duplicate_cur_rejects(self):
        with pytest.raises(ValueError, match="duplicate 'cur'"):
            _parse_transactions_tsv(
                "ref\tdate\tdescription\tcur\tcur\tamt1\tacct1\n"
                "1\t2026-07-15\tX\tUSD\tUSD\t-10\tA"
            )

    def test_currency_token_still_rejects(self):
        """'currency' (and typo'd 'currency2') stay unknown tokens —
        the 1.4.1 reject-by-name contract is unchanged."""
        with pytest.raises(ValueError, match="currency"):
            _parse_transactions_tsv(
                "ref\tdate\tdescription\tcurrency\tamt1\tacct1\n"
                "1\t2026-07-15\tX\tUSD\t-10\tA"
            )

    def test_foreign_pair_books_in_foreign_frame(
        self, multi_currency_book,
    ):
        """EUR->EUR transfer in a USD book: cur=EUR, statement
        numbers as amounts, no qty, transaction denominated EUR."""
        gc = GnuCashBook(str(multi_currency_book))
        gc.create_account(
            name="EUR Checking", account_type="BANK",
            parent="Assets", commodity="EUR",
        )
        env = gc.create_transactions([{
            "ref": "1", "date": date(2026, 7, 15),
            "description": "Move to savings", "currency": "EUR",
            "splits": [
                {"account": "Assets:EUR Checking", "amount": "-40.00"},
                {"account": "Assets:Euro Savings", "amount": "40.00"},
            ],
        }])
        rows = _parse(env["results"])
        assert rows[0]["status"] == "created"
        txn = gc.get_transaction(rows[0]["txn_guid"])
        assert txn["currency"] == "EUR"
        splits = {s["account"]: s for s in txn["splits"]}
        sav = splits["Assets:Euro Savings"]
        assert sav["value"] == "40" and sav["quantity"] == "40"

    def test_cur_row_with_default_commodity_leg_needs_qty(
        self, multi_currency_book,
    ):
        """A USD leg inside a EUR-frame row is the cross-commodity
        one now — omitting its qty rejects the row."""
        gc = GnuCashBook(str(multi_currency_book))
        env = gc.create_transactions([{
            "ref": "1", "date": date(2026, 7, 15),
            "description": "EUR purchase from USD account",
            "currency": "EUR",
            "splits": [
                {"account": "Assets:Checking", "amount": "-40.00"},
                {"account": "Assets:Euro Savings", "amount": "40.00"},
            ],
        }])
        rows = _parse(env["results"])
        assert rows[0]["status"] == "rejected"
        assert "quantity" in rows[0]["reason"]

    def test_mixed_batch_default_and_cur_rows(
        self, multi_currency_book,
    ):
        gc = GnuCashBook(str(multi_currency_book))
        gc.create_account(
            name="EUR Checking", account_type="BANK",
            parent="Assets", commodity="EUR",
        )
        env = gc.create_transactions([
            {
                "ref": "1", "date": date(2026, 7, 15),
                "description": "USD groceries",
                "splits": [
                    {"account": "Assets:Checking", "amount": "-30.00"},
                    {"account": "Expenses:Groceries", "amount": "30.00"},
                ],
            },
            {
                "ref": "2", "date": date(2026, 7, 15),
                "description": "EUR shuffle", "currency": "EUR",
                "splits": [
                    {"account": "Assets:EUR Checking", "amount": "-15.00"},
                    {"account": "Assets:Euro Savings", "amount": "15.00"},
                ],
            },
        ])
        rows = {r["ref"]: r for r in _parse(env["results"])}
        assert rows["1"]["status"] == "created"
        assert rows["2"]["status"] == "created"
        gc2 = GnuCashBook(str(multi_currency_book))
        t1 = gc2.get_transaction(rows["1"]["txn_guid"])
        t2 = gc2.get_transaction(rows["2"]["txn_guid"])
        assert t1["currency"] == "USD"
        assert t2["currency"] == "EUR"

    def test_unknown_currency_rejects_row(self, multi_currency_book):
        gc = GnuCashBook(str(multi_currency_book))
        env = gc.create_transactions([{
            "ref": "1", "date": date(2026, 7, 15),
            "description": "X", "currency": "CHF",
            "splits": [
                {"account": "Assets:Checking", "amount": "-1.00"},
                {"account": "Expenses:Groceries", "amount": "1.00"},
            ],
        }])
        rows = _parse(env["results"])
        assert rows[0]["status"] == "rejected"
        assert "CHF" in rows[0]["reason"]

    def test_cur_with_autofill_row_rejects(self, multi_currency_book):
        gc = GnuCashBook(str(multi_currency_book))
        env = gc.create_transactions([{
            "ref": "1", "date": date(2026, 7, 15),
            "description": "Groceries", "currency": "EUR",
            "splits": [],
        }])
        rows = _parse(env["results"])
        assert rows[0]["status"] == "rejected"
        assert "auto-fill" in rows[0]["reason"]

    def test_audit_submission_parse_carries_currency(self):
        from gnucash_mcp.logging_config import _parse_batch_submission
        parsed = _parse_batch_submission(
            "ref\tdate\tdescription\tcur\tamt1\tacct1\tamt2\tacct2\n"
            "1\t2026-07-15\tCard Payment\tusd\t-500\tA:U\t500\tL:U"
        )
        assert parsed["1"]["currency"] == "USD"

    def test_cross_currency_coincidence_is_nonblocking_and_labeled(
        self, multi_currency_book,
    ):
        """188 EUR is not 188 USD: the amount signal only fires
        within the same currency frame, so a cross-currency numeric
        coincidence lands as a non-blocking MEDIUM (desc+date,
        'D-D') labeled with the candidate's currency — visible,
        interpretable, and never forcing a weaker model to refuse
        a legitimate entry (bookkeeper finding, cur-column round)."""
        gc = GnuCashBook(str(multi_currency_book))
        gc.create_account(
            name="EUR Checking", account_type="BANK",
            parent="Assets", commodity="EUR",
        )
        # Existing EUR-frame transaction: 188 EUR.
        gc.create_transaction(
            description="Tea House", currency="EUR",
            splits=[
                {"account": "Assets:EUR Checking", "amount": "-188.00"},
                {"account": "Assets:Euro Savings", "amount": "188.00"},
            ],
            trans_date=date(2026, 7, 15),
        )
        # New default-frame (USD) row, same description/amount/date
        # — creates WITHOUT force: the coincidence is not a HIGH.
        env = gc.create_transactions([{
            "ref": "1", "date": date(2026, 7, 15),
            "description": "Tea House",
            "splits": [
                {"account": "Assets:Checking", "amount": "-188.00"},
                {"account": "Expenses:Groceries", "amount": "188.00"},
            ],
        }])
        rows = _parse(env["results"])
        assert rows[0]["status"] == "created"
        dups = _parse(env["duplicates"])
        assert dups, "expected a labeled cross-currency MEDIUM"
        assert dups[0]["confidence"] == "MEDIUM"
        assert dups[0]["signals"] == "D-D"
        assert dups[0]["cur"] == "EUR"


class TestSplitActionColumn:
    """The ``act`` split column carries GnuCash's typed movement
    tag (investment-register convention: Buy/Sell/Dividend)."""

    def test_act_column_parses_into_split_dicts(self):
        tsv = (
            "ref\tdate\tdescription\tamt1\tacct1\tact1\tamt2\tacct2\tact2\n"
            "1\t2026-07-01\tVFIFX Purchase\t-500\tA:Chk\t\t500\tA:VFIFX\tBuy"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["splits"][1]["action"] == "Buy"
        assert "action" not in rows[0]["splits"][0]  # empty cell

    def test_batch_action_lands_on_split(self, multi_currency_book):
        gc = GnuCashBook(str(multi_currency_book))
        env = gc.create_transactions([{
            "ref": "1", "date": date(2026, 7, 15),
            "description": "EUR feed",
            "splits": [
                {"account": "Assets:Checking", "amount": "-110.00",
                 "action": "Wire"},
                {"account": "Assets:Euro Savings", "amount": "110.00",
                 "quantity": "100.00"},
            ],
        }])
        rows = _parse(env["results"])
        assert rows[0]["status"] == "created"
        txn = gc.get_transaction(rows[0]["txn_guid"])
        chk = next(
            s for s in txn["splits"]
            if s["account"] == "Assets:Checking"
        )
        assert chk["action"] == "Wire"


class TestSignalSweepAmortization:
    """The table sweep (sort + filter of every transaction) is the
    collector's dominant cost on large books; each surface must pay
    it at most ONCE per call, however many rows it screens — the
    debug log's create_transactions p95 tail was O(rows) sweeps.
    Counts real invocations via a wrapping monkeypatch: a new call
    site that forgets to share the sweep fails here, not in a
    profiler six months later.
    """

    @pytest.fixture
    def sweep_calls(self, monkeypatch):
        from gnucash_mcp.book.core import CoreMixin
        calls = {"n": 0}
        orig = CoreMixin._signal_sweep

        def counting(self, book):
            calls["n"] += 1
            return orig(self, book)

        monkeypatch.setattr(CoreMixin, "_signal_sweep", counting)
        return calls

    def test_batch_sweeps_once(self, test_book, sweep_calls):
        """Mixed batch — a splitless auto-fill row (phase-1
        preflight) plus explicit rows (phase-2 screens): one sweep
        covers all of them."""
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        sweep_calls["n"] = 0
        env = gc.create_transactions([
            {"ref": "1", "date": date(2026, 6, 21),
             "description": "Rent", "splits": []},
            _txn(2, "Groceries B", "25.00"),
            _txn(3, "Groceries C", "30.00"),
        ])
        rows = {r["ref"]: r for r in _parse(env["results"])}
        assert rows["2"]["status"] == "created"
        assert rows["3"]["status"] == "created"
        assert sweep_calls["n"] == 1

    def test_single_splitless_sweeps_once(self, test_book, sweep_calls):
        """Splitless create makes TWO collector calls (auto-fill
        preflight + duplicate scan); they share one sweep."""
        gc = GnuCashBook(str(test_book))
        _seed_rent(gc)
        sweep_calls["n"] = 0
        result = gc.create_transaction(
            description="Rent", trans_date=date(2026, 6, 21),
        )
        assert result["status"] in ("created", "rejected")
        assert sweep_calls["n"] == 1
