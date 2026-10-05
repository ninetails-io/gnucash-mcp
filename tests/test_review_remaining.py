"""What the 1.5 pre-release review found and 1.5's first cut left.

One class per finding, named for it, each with the test that fails on
the code as it stood. The findings are in
``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md`` (sections 5, 7, 8)
and were listed in ``specs/v1.5.1/README.md`` until they were fixed.
"""

import sqlite3
from datetime import date, timedelta
from decimal import Decimal

import pytest

from gnucash_mcp.book import GnuCashBook


def _q(path, sql, params=()):
    con = sqlite3.connect(str(path))
    try:
        rows = con.execute(sql, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def _spend(gb, notes=None, amount="10"):
    return gb.create_transaction(
        description="Void probe", notes=notes,
        splits=[
            {"account": "Assets:Checking", "amount": f"-{amount}"},
            {"account": "Expenses:Groceries", "amount": amount},
        ],
        trans_date=date.today() - timedelta(days=1),
    )["guid"]


def _notes(gb, guid):
    with gb.open(readonly=True) as book:
        txn = gb._find_transaction(book, guid)
        return txn.notes, txn.get("void-former-notes")


class TestSS17VoidNotesFollowXaccTransVoid:
    """xaccTransVoid copies the notes whenever the slot holds a
    string; xaccTransUnvoid puts the former notes back and otherwise
    leaves the notes alone. Neither reads the text of the void note,
    which GnuCash writes in the user's language."""

    def test_unvoid_restores_notes_under_a_translated_void_note(
        self, test_book,
    ):
        gb = GnuCashBook(str(test_book))
        guid = _spend(gb, notes="original")
        gb.void_transaction(guid, reason="oops")
        # What a German GnuCash leaves.
        with gb.open(readonly=False) as book:
            gb._find_transaction(book, guid).notes = "Stornierte Buchung"
            book.save()
        gb.unvoid_transaction(guid)
        assert _notes(gb, guid) == ("original", None)

    def test_an_empty_notes_slot_is_kept_through_void_and_unvoid(
        self, test_book,
    ):
        gb = GnuCashBook(str(test_book))
        guid = _spend(gb)
        with gb.open(readonly=False) as book:
            gb._find_transaction(book, guid)["notes"] = ""
            book.save()
        gb.void_transaction(guid, reason="oops")
        assert _notes(gb, guid) == ("Voided transaction", "")
        gb.unvoid_transaction(guid)
        assert _notes(gb, guid) == ("", None)

    def test_with_no_former_notes_the_void_note_stays(self, test_book):
        gb = GnuCashBook(str(test_book))
        guid = _spend(gb)
        gb.void_transaction(guid, reason="oops")
        assert _notes(gb, guid) == ("Voided transaction", None)
        gb.unvoid_transaction(guid)
        # xaccTransUnvoid touches the notes only when it has former
        # notes to restore.
        assert _notes(gb, guid) == ("Voided transaction", None)


class TestSideFinding11VoidingAReconciledSplitNeedsForce:
    def test_refused_then_forced(self, test_book):
        gb = GnuCashBook(str(test_book))
        guid = _spend(gb)
        split = gb.get_transaction(guid)["splits"][0]
        gb.set_reconcile_state(split_guid=split["guid"], state="y")
        with pytest.raises(ValueError, match="Voiding will break"):
            gb.void_transaction(guid, reason="oops")
        assert gb.get_transaction(guid)["splits"][0]["value"] != "0"
        forced = gb.void_transaction(guid, reason="oops", force=True)
        assert forced["status"] == "voided"
        assert split["account"] in forced["warning"]

    def test_a_cleared_split_needs_no_force(self, test_book):
        gb = GnuCashBook(str(test_book))
        guid = _spend(gb)
        split = gb.get_transaction(guid)["splits"][0]
        gb.set_reconcile_state(split_guid=split["guid"], state="c")
        assert gb.void_transaction(guid, reason="oops")["status"] == "voided"
