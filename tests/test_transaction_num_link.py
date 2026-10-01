"""The register's Num and document-link fields on every transaction
write path that takes per-transaction columns.

Num is ``transactions.num`` — except that a book with the option "Use
Split Action Field for Number" keeps the register's Num in the
register account's split action (``gnc_set_num_action``,
engine-helpers.c). Batch entry writes ``transactions.num`` as desktop's
CSV importer does (it has no register account); statement entry is the
statement account's register and follows the option. The document link
is the ``assoc_uri`` string slot ``xaccTransSetDocLink`` writes.

Found missing by @wernerwws's fork (2026-09-29), which added num for
an Austrian GmbH's invoice references ("ER 2658", "AR 261").
"""

from datetime import date

import piecash
import pytest
from sqlalchemy import text

from gnucash_mcp._format import (
    _batch_tsv_layout,
    _parse_statement_tsv,
    _parse_update_tsv,
)
from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.book._base import _NUM_SOURCE_KEY
from gnucash_mcp.logging_config import (
    _fmt_transaction_create_batch,
    _parse_batch_submission,
)
from gnucash_mcp.tools.core import _parse_transactions_tsv

from tests.test_statement_entry import (  # noqa: F401 — fixture
    _CLOSE,
    _OPEN,
    _line,
    statement_book,
)


def _rows(book_path, sql, **params):
    b = piecash.open_book(str(book_path), readonly=True, open_if_lock=True)
    try:
        return b.session.execute(text(sql), params).fetchall()
    finally:
        b.close()


def _txn_row(book_path, description):
    return _rows(
        book_path,
        "SELECT guid, num FROM transactions WHERE description = :d",
        d=description,
    )[0]


def _link_slots(book_path, txn_guid):
    return _rows(
        book_path,
        "SELECT slot_type, string_val FROM slots "
        "WHERE obj_guid = :g AND name = 'assoc_uri'",
        g=txn_guid,
    )


def _num_on_split_action(book_path):
    """Turn the book option on the way desktop stores it: a string
    "t" under options/Accounts."""
    b = piecash.open_book(str(book_path), readonly=False, open_if_lock=True)
    b["options"] = {"Accounts": {"Use Split Action Field for Number": "t"}}
    b.save()
    b.close()


_BATCH = (
    "ref\tdate\tdescription\tnum\tlink\tamt1\tacct1\tamt2\tacct2\n"
    "1\t2026-07-01\tOffice chairs\tER 2658\tfile:///receipts/er2658.pdf"
    "\t-120.00\tAssets:Checking\t120.00\tExpenses:Groceries\n"
)


class TestGrammar:
    def test_batch_columns_any_order(self):
        layout = _batch_tsv_layout(
            "ref\tdate\tdescription\tlink\tcur\tnum\tnotes\tamt\tacct"
        )
        assert layout["fixed_idx"] == {
            "link": 3, "currency": 4, "num": 5, "notes": 6,
        }
        assert layout["fixed"] == 7

    def test_doclink_alias(self):
        layout = _batch_tsv_layout("ref\tdate\tdescription\tdoclink\tamt\tacct")
        assert layout["fixed_idx"] == {"link": 3}

    def test_duplicate_rejects(self):
        with pytest.raises(ValueError, match="duplicate"):
            _batch_tsv_layout("ref\tdate\tdescription\tlink\tdoclink\tamt\tacct")

    def test_batch_row(self):
        (row,) = _parse_transactions_tsv(_BATCH)
        assert row["num"] == "ER 2658"
        assert row["link"] == "file:///receipts/er2658.pdf"
        assert [s["account"] for s in row["splits"]] == [
            "Assets:Checking", "Expenses:Groceries",
        ]

    def test_empty_cells_absent(self):
        (row,) = _parse_transactions_tsv(
            "ref\tdate\tdescription\tnum\tlink\tamt\tacct\tamt\tacct\n"
            "1\t2026-07-01\tX\t\t\t-1\tA\t1\tB\n"
        )
        assert "num" not in row and "link" not in row

    def test_audit_display_parse_agrees(self):
        assert _parse_batch_submission(_BATCH)["1"]["num"] == "ER 2658"

    def test_statement_columns(self):
        (row,) = _parse_statement_tsv(
            "ref\tdate\tnum\traw\tamount\tlink\n"
            "1\t2026-07-03\t1042\tCHECK 1042\t-87.12\thttps://x/1\n"
        )
        assert row["num"] == "1042" and row["link"] == "https://x/1"

    def test_update_columns_and_clear(self):
        rows = _parse_update_tsv(
            "guid\tnum\tlink\tclear\n"
            "abcd1234\tAR 261\t\tlink\n"
        )
        assert rows == [{"guid": "abcd1234", "num": "AR 261", "link": ""}]


class TestBatch:
    def test_writes_num_and_link(self, test_book):
        gc = GnuCashBook(str(test_book))
        gc.create_transactions(_parse_transactions_tsv(_BATCH))
        guid, num = _txn_row(test_book, "Office chairs")
        assert num == "ER 2658"
        assert _link_slots(test_book, guid) == [
            (4, "file:///receipts/er2658.pdf"),
        ]
        txn = gc.get_transaction(guid)
        assert txn["num"] == "ER 2658"
        assert txn["doc_link"] == "file:///receipts/er2658.pdf"

    def test_no_link_writes_no_slot(self, test_book):
        gc = GnuCashBook(str(test_book))
        gc.create_transactions(_parse_transactions_tsv(
            "ref\tdate\tdescription\tamt\tacct\tamt\tacct\n"
            "1\t2026-07-01\tPlain\t-1.00\tAssets:Checking"
            "\t1.00\tExpenses:Groceries\n"
        ))
        guid, num = _txn_row(test_book, "Plain")
        assert num == ""
        assert _link_slots(test_book, guid) == []
        assert "num" not in gc.get_transaction(guid)

    def test_ignores_num_source_option(self, test_book):
        """The CSV importer writes xaccTransSetNum whatever the
        option says; so does batch entry."""
        _num_on_split_action(test_book)
        gc = GnuCashBook(str(test_book))
        gc.create_transactions(_parse_transactions_tsv(_BATCH))
        guid, num = _txn_row(test_book, "Office chairs")
        assert num == "ER 2658"
        actions = _rows(
            test_book, "SELECT action FROM splits WHERE tx_guid = :g",
            g=guid,
        )
        assert {a for (a,) in actions} == {""}

    def test_overlong_num_refused(self, test_book):
        gc = GnuCashBook(str(test_book))
        tsv = _BATCH.replace("ER 2658", "9" * 2049)
        res = gc.create_transactions(_parse_transactions_tsv(tsv))
        assert "rejected" in res["results"]
        assert "num" in res["results"]

    def test_audit_line_names_num_and_link(self, test_book):
        gc = GnuCashBook(str(test_book))
        res = gc.create_transactions(_parse_transactions_tsv(_BATCH))
        lines = _fmt_transaction_create_batch({
            "timestamp": "2026-10-01T12:00:00",
            "params": {"transactions": _BATCH},
            "after_state": res,
        })
        text_out = "\n".join(lines)
        assert '#ER 2658  "Office chairs"' in text_out
        assert "link: file:///receipts/er2658.pdf" in text_out


class TestUpdate:
    def _created(self, test_book):
        gc = GnuCashBook(str(test_book))
        gc.create_transactions(_parse_transactions_tsv(_BATCH))
        guid, _ = _txn_row(test_book, "Office chairs")
        return gc, guid

    def test_set_num_and_link(self, test_book):
        gc, guid = self._created(test_book)
        gc.update_transactions(_parse_update_tsv(
            f"guid\tnum\tlink\n{guid}\tER 2659\thttps://dms/2659\n"
        ))
        assert _txn_row(test_book, "Office chairs")[1] == "ER 2659"
        assert _link_slots(test_book, guid) == [(4, "https://dms/2659")]

    def test_clear_removes_link_slot_and_num(self, test_book):
        gc, guid = self._created(test_book)
        gc.update_transactions(_parse_update_tsv(
            f"guid\tclear\n{guid}\tnum,link\n"
        ))
        assert _txn_row(test_book, "Office chairs")[1] == ""
        assert _link_slots(test_book, guid) == []

    def test_num_only_row_is_a_change(self, test_book):
        gc, guid = self._created(test_book)
        res = gc.update_transactions(_parse_update_tsv(
            f"guid\tnum\n{guid}\t7\n"
        ))
        assert "\tupdated\t" in res["results"]


class TestStatement:
    def _enter(self, statement_book, lines):
        gc = GnuCashBook(str(statement_book))
        return gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31), "1000.00",
            str(1000 + sum(float(ln["amount"]) for ln in lines)),
            lines, dry_run=False,
        )

    def _paycheck(self, **kw):
        return _line(
            "1", date(2026, 7, 15), "2000.00",
            description="Paycheck", raw="DIRECT DEP ACME",
            splits=[{"account": "Income", "amount": "-2000.00"}], **kw,
        )

    def test_created_row_num_on_transaction(self, statement_book):
        self._enter(statement_book, [self._paycheck(
            num="1042", link="https://bank/stmt/1042",
        )])
        guid, num = _txn_row(statement_book, "Paycheck")
        assert num == "1042"
        assert _link_slots(statement_book, guid) == [
            (4, "https://bank/stmt/1042"),
        ]

    def test_created_row_num_on_bank_split_when_option_on(
        self, statement_book,
    ):
        _num_on_split_action(statement_book)
        self._enter(statement_book, [self._paycheck(num="1042")])
        guid, num = _txn_row(statement_book, "Paycheck")
        assert num == ""
        legs = dict(_rows(
            statement_book,
            "SELECT a.name, s.action FROM splits s "
            "JOIN accounts a ON a.guid = s.account_guid "
            "WHERE s.tx_guid = :g",
            g=guid,
        ))
        assert legs == {"Checking": "1042", "Income": ""}

    def test_claim_row_sets_num(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        dry = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31), "1000.00", "200.00",
            [_line("1", date(2026, 7, 1), "-800.00", raw="ACH RENT")],
        )
        cand = dry["candidates"].splitlines()[1].split("\t")[1]
        gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31), "1000.00", "200.00",
            [_line("1", date(2026, 7, 1), "-800.00", raw="ACH RENT",
                   match=cand, num="EFT 88")],
            dry_run=False,
        )
        assert _txn_row(statement_book, "July Rent")[1] == "EFT 88"


def test_option_key_is_desktops():
    """qofbookslots.h: OPTION_SECTION_ACCOUNTS "Accounts",
    OPTION_NAME_NUM_FIELD_SOURCE "Use Split Action Field for Number"."""
    assert _NUM_SOURCE_KEY == (
        "options/Accounts/Use Split Action Field for Number"
    )
