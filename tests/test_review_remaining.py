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


class TestHiddenIsWrittenTheWayDesktopWritesIt:
    """xaccAccountSetHidden keeps a string slot "true" (or no slot)
    and the SQL backend saves the column from it. The server reads
    the column, so update_account writes both."""

    ACCT = "Expenses:Groceries"

    def _state(self, path):
        return _q(
            path,
            "select a.hidden, s.slot_type, s.string_val from accounts a "
            "left join slots s on s.obj_guid = a.guid and s.name = 'hidden' "
            "where a.name = 'Groceries'",
        )

    def test_hide_and_show(self, test_book):
        gb = GnuCashBook(str(test_book))
        hid = gb.update_account(self.ACCT, hidden=True)
        assert hid["hidden"] is True
        assert self._state(test_book) == [(1, 4, "true")]
        assert gb.get_account(self.ACCT)["hidden"] is True
        shown = gb.update_account(self.ACCT, hidden=False)
        assert shown["hidden"] is False
        assert self._state(test_book) == [(0, None, None)]
        assert "hidden" not in gb.get_account(self.ACCT)

    def test_no_change_is_not_reported(self, test_book):
        gb = GnuCashBook(str(test_book))
        assert "hidden" not in gb.update_account(self.ACCT, hidden=False)

    def test_a_slot_out_of_step_with_the_column_is_brought_in_step(
        self, test_book,
    ):
        gb = GnuCashBook(str(test_book))
        _q(test_book, "update accounts set hidden = 1 where name = 'Groceries'")
        fixed = gb.update_account(self.ACCT, hidden=True)
        assert fixed["hidden"] is True
        assert self._state(test_book) == [(1, 4, "true")]

    def test_the_slot_tools_point_at_update_account(self, test_book):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match=r"update_account\(hidden"):
            gb.set_account_slot(self.ACCT, "hidden", "true")


class TestC60ManualBackupsOfOneStateAreOneFile:
    """The cap on manual backups: a call made while the book is
    unchanged answers with the copy that already holds it. Nothing
    is deleted, and a book that has changed is always copied."""

    def _files(self, gb):
        return sorted(p.name for p in gb._backups_dir().glob("*.gnucash"))

    def test_a_repeat_on_an_unchanged_book_writes_nothing(self, test_book):
        gb = GnuCashBook(str(test_book))
        first = gb.create_backup(label="one", skip_unchanged=True)
        assert first["status"] == "created"
        for _ in range(5):
            again = gb.create_backup(label="two", skip_unchanged=True)
            assert again["status"] == "unchanged"
            assert again["path"] == first["path"]
            assert "restore_hint" in again
        assert len(self._files(gb)) == 1

    def test_a_changed_book_is_copied_again(self, test_book):
        gb = GnuCashBook(str(test_book))
        gb.create_backup(skip_unchanged=True)
        _spend(gb)
        assert gb.create_backup(skip_unchanged=True)["status"] == "created"
        assert len(self._files(gb)) == 2

    def test_a_removed_backup_is_not_answered_with(self, test_book):
        gb = GnuCashBook(str(test_book))
        gb.create_backup(skip_unchanged=True)
        for p in gb._backups_dir().glob("*.gnucash"):
            p.unlink()
        assert gb.create_backup(skip_unchanged=True)["status"] == "created"
        assert len(self._files(gb)) == 1

    def test_the_book_method_still_copies_on_every_call(self, test_book):
        gb = GnuCashBook(str(test_book))
        gb.create_backup(label="a")
        assert gb.create_backup(label="b")["status"] == "created"
        assert len(self._files(gb)) == 2


from tests.test_review_minor import _set_read_only_days  # noqa: E402

AR = "Assets:Accounts Receivable"


class TestC69BusinessAndScheduleWritesNameTheReadOnlyPeriod:
    """The other half of C69. Desktop's Post Invoice, Process Payment
    and Since Last Run do not check the read-only option, so nothing
    is refused; the server says the date is inside the closed period
    and that desktop's register will show the result read-only."""

    INSIDE = (date.today() - timedelta(days=40)).isoformat()
    OUTSIDE = (date.today() - timedelta(days=5)).isoformat()

    def _invoice(self, gb, opened):
        gb.create_customer(name="Acme")
        inv = gb.create_invoice(customer_id="000001", date_opened=opened)
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        return inv["id"]

    def test_post_pay_and_unpost_inside_the_period(self, business_book):
        _set_read_only_days(business_book, 30)
        gb = GnuCashBook(str(business_book))
        inv = self._invoice(gb, self.INSIDE)
        posted = gb.post_invoice(inv, AR, post_date=self.INSIDE)
        assert self.INSIDE in posted["read_only_period"]
        assert "Post Invoice dialog does not check" in posted["read_only_period"]
        paid = gb.pay_invoice(
            invoice_id=inv, payment_account="Assets:Checking",
            amount="40.00", payment_date=self.INSIDE,
        )
        assert "Process Payment dialog" in paid["read_only_period"]
        unposted = gb.unpost_invoice(inv)
        assert "Unpost command" in unposted["read_only_period"]

    def test_outside_the_period_nothing_is_said(self, business_book):
        _set_read_only_days(business_book, 30)
        gb = GnuCashBook(str(business_book))
        inv = self._invoice(gb, self.OUTSIDE)
        posted = gb.post_invoice(inv, AR, post_date=self.OUTSIDE)
        paid = gb.pay_invoice(
            invoice_id=inv, payment_account="Assets:Checking",
            amount="40.00", payment_date=self.OUTSIDE,
        )
        assert "read_only_period" not in posted
        assert "read_only_period" not in paid
        assert "read_only_period" not in gb.unpost_invoice(inv)

    def test_no_option_nothing_is_said(self, business_book):
        gb = GnuCashBook(str(business_book))
        inv = self._invoice(gb, self.INSIDE)
        posted = gb.post_invoice(inv, AR, post_date=self.INSIDE)
        assert "read_only_period" not in posted

    def test_a_schedule_instance_inside_the_period(self, test_book):
        _set_read_only_days(test_book, 30)
        gb = GnuCashBook(str(test_book))
        start = (date.today() - timedelta(days=90)).isoformat()
        sx = gb.create_scheduled_transaction(
            name="Rent", description="Rent",
            splits=[
                {"account": "Expenses:Groceries", "amount": "100.00"},
                {"account": "Assets:Checking", "amount": "-100.00"},
            ],
            start_date=start, frequency="monthly",
        )
        made = gb.create_transaction_from_scheduled(
            sx["guid"], transaction_date=self.INSIDE,
        )
        assert made["status"] == "created"
        assert "Since Last Run assistant" in made["read_only_period"]
        later = gb.create_transaction_from_scheduled(
            sx["guid"], transaction_date=self.OUTSIDE,
        )
        assert "read_only_period" not in later

    def test_a_credit_note_applied_inside_the_period(self, business_book):
        _set_read_only_days(business_book, 30)
        gb = GnuCashBook(str(business_book))
        inv = self._invoice(gb, self.INSIDE)
        gb.post_invoice(inv, AR, post_date=self.INSIDE)
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="1", price="30.00",
        )
        gb.post_invoice(
            invoice_id=cn["id"], post_account=AR,
            post_date=self.INSIDE, owner_type="customer",
        )
        applied = gb.apply_credit_note(
            credit_note_id=cn["id"], applies_to_invoice_id=inv,
            owner_type="customer",
        )
        assert applied["apply_date"] == self.INSIDE
        assert "Process Payment dialog" in applied["read_only_period"]


def _num_on_split_actions(path):
    """The book option as desktop stores it (pinned in
    tests/test_transaction_num_link.py)."""
    import piecash

    with piecash.open_book(
        str(path), readonly=False, do_backup=False, open_if_lock=True,
    ) as b:
        b["options"] = {"Accounts": {"Use Split Action Field for Number": "t"}}
        b.save()


class TestC67BusinessPostingsFollowTheNumOption:
    """gnc_set_num_action: with "Use Split Action Field for Number"
    on, a posting's splits carry the document ID as their action and
    the transaction's number is the document type; a payment's
    transfer split carries the payment number (none here)."""

    def _post_and_pay(self, path):
        gb = GnuCashBook(str(path))
        gb.create_customer(name="Acme")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(inv["id"], AR)
        gb.pay_invoice(
            invoice_id=inv["id"], payment_account="Assets:Checking",
            amount="100.00",
        )
        rows = _q(
            path,
            "select t.num, a.name, s.action from transactions t "
            "join splits s on s.tx_guid = t.guid "
            "join accounts a on a.guid = s.account_guid "
            "join slots k on k.obj_guid = t.guid "
            "and k.name = 'trans-txn-type' "
            "order by k.string_val, a.name",
        )
        return inv["id"], rows

    def test_option_off(self, business_book):
        inv, rows = self._post_and_pay(business_book)
        assert rows == [
            (inv, "Accounts Receivable", "Invoice"),
            (inv, "Sales", "Invoice"),
            ("", "Accounts Receivable", "Payment"),
            ("", "Checking", "Payment"),
        ]

    def test_option_on(self, business_book):
        _num_on_split_actions(business_book)
        inv, rows = self._post_and_pay(business_book)
        assert rows == [
            ("Invoice", "Accounts Receivable", inv),
            ("Invoice", "Sales", inv),
            ("", "Accounts Receivable", "Payment"),
            ("", "Checking", ""),
        ]


class TestFC14StartupReadsTheVersionsTable:
    """A SQLite file whose tables piecash will not open is named at
    startup, with what to do, instead of failing on the first call."""

    def test_a_book_the_server_reads_passes(self, test_book):
        from gnucash_mcp.server import _book_format_error
        assert _book_format_error(test_book) is None

    def test_a_sqlite_file_that_is_not_a_book(self, tmp_path):
        from gnucash_mcp.server import _book_format_error
        p = tmp_path / "notes.gnucash"
        _q(p, "create table notes (body text)")
        assert "not a GnuCash book" in _book_format_error(p)

    def test_a_book_last_saved_by_2_6(self, test_book):
        from piecash.core.session import version_supported

        from gnucash_mcp.server import _book_format_error
        for table, version in version_supported["2.6"].items():
            _q(
                test_book,
                "update versions set table_version = ? where table_name = ?",
                (version, table),
            )
        msg = _book_format_error(test_book)
        assert "GnuCash 2.6 or older" in msg
        assert "save it once" in msg
        # piecash agrees that it cannot open this file.
        with pytest.raises(Exception):
            GnuCashBook(str(test_book)).list_accounts()

    def test_a_table_version_nobody_knows(self, test_book):
        from gnucash_mcp.server import _book_format_error
        _q(
            test_book,
            "update versions set table_version = 99 "
            "where table_name = 'splits'",
        )
        msg = _book_format_error(test_book)
        assert "versions this server does not read (splits)" in msg

    def test_the_sample_books_pass(self):
        from pathlib import Path

        from gnucash_mcp.server import _book_format_error
        samples = Path(__file__).resolve().parent.parent / "samples"
        for name in (
            "alex-chen-morales.gnucash", "lin-wei.gnucash",
        ):
            if (samples / name).exists():
                assert _book_format_error(samples / name) is None, name


class TestC32AssignSplitToLotSavesOnceOrNotAtAll:
    def _setup(self, path):
        from tests.test_lots import _buy_shares

        gb = GnuCashBook(str(path))
        lot = gb.create_lot(account="Assets:Investments:VTSAX", title="Lot")
        split = _buy_shares(gb, 8, Decimal("125"), date(2026, 1, 15))
        return gb, lot["guid"], split

    def _assigned(self, path):
        return _q(path, "select count(*) from splits where lot_guid is not null")[0][0]

    def test_a_failure_after_the_assignment_leaves_nothing(
        self, investment_book, monkeypatch,
    ):
        gb, lot, split = self._setup(investment_book)

        def boom(*a, **kw):
            raise ValueError("summary failed")

        monkeypatch.setattr(type(gb), "_lot_summary", boom)
        with pytest.raises(ValueError, match="summary failed"):
            gb.assign_split_to_lot(split_guid=split, lot_guid=lot)
        assert self._assigned(investment_book) == 0
        monkeypatch.undo()
        # The retry is clean, not "already assigned".
        done = gb.assign_split_to_lot(split_guid=split, lot_guid=lot)
        assert done["status"] == "assigned"
        assert self._assigned(investment_book) == 1

    def test_no_default_currency_is_refused_before_anything_is_written(
        self, investment_book, monkeypatch,
    ):
        gb, lot, split = self._setup(investment_book)

        def none(book):
            raise ValueError("Book has no default currency set.")

        monkeypatch.setattr(
            type(gb), "_require_default_currency", staticmethod(none),
        )
        with pytest.raises(ValueError, match="no default currency"):
            gb.assign_split_to_lot(split_guid=split, lot_guid=lot)
        assert self._assigned(investment_book) == 0


@pytest.fixture
def audited(test_book, tmp_path, monkeypatch):
    """Audit logging pointed at a scratch folder, and a write tool
    wrapped the way every registered tool is."""
    import json

    from gnucash_mcp import logging_config as lc

    monkeypatch.setenv("GNUCASH_LOG_DIR", str(tmp_path / "logs"))
    lc.setup_logging(book_path=str(test_book), audit=True)
    directory = lc._audit_directory()
    seen = {}

    @lc.audit_log(
        classification="write", operation="create",
        entity_type="transaction",
    )
    def tool(description: str = "x") -> str:
        seen["intent_during_call"] = (directory / lc._INTENT_NAME).exists()
        if description == "raise":
            raise ValueError("refused")
        return json.dumps({"guid": "a" * 32, "status": "created"})

    def trail():
        return "\n".join(
            p.read_text() for p in sorted(directory.glob("*.txt"))
        )

    try:
        yield lc, tool, directory, seen, trail
    finally:
        lc.setup_logging(book_path=None, audit=False, debug=False)


class TestDS16ARenderFailureNeverReplacesTheResult:
    def test_the_result_comes_back_and_the_trail_says_why(
        self, audited, monkeypatch,
    ):
        lc, tool, _directory, _seen, trail = audited

        def boom(entry):
            raise KeyError("after_state")

        monkeypatch.setattr(lc, "_format_audit_entry_text", boom)
        assert '"status": "created"' in tool(description="ok")
        assert (
            "WRITE  tool: succeeded; its audit entry could not be rendered"
            in trail()
        )
        assert "KeyError" in trail()


class TestDS11AWriteLeavesAnIntentUntilItsEntryIsWritten:
    def test_the_intent_is_there_during_the_call_and_gone_after(
        self, audited,
    ):
        lc, tool, directory, seen, _trail = audited
        tool(description="ok")
        assert seen["intent_during_call"] is True
        assert not (directory / lc._INTENT_NAME).exists()

    def test_a_refused_write_clears_its_intent(self, audited):
        lc, tool, directory, _seen, trail = audited
        with pytest.raises(ValueError):
            tool(description="raise")
        assert not (directory / lc._INTENT_NAME).exists()
        assert "ERROR  tool: refused" in trail()

    def _leave(self, lc, directory, pid):
        import json
        (directory / lc._INTENT_NAME).write_text(json.dumps({
            "pid": pid, "tool": "create_transactions",
            "timestamp": "2026-10-05T09:00:00-07:00",
            "params": '{"rows": "..."}',
        }))

    def _dead_pid(self):
        import subprocess
        import sys
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid

    def test_an_intent_left_by_a_dead_server_is_reported_at_startup(
        self, audited, test_book,
    ):
        lc, _tool, directory, _seen, trail = audited
        self._leave(lc, directory, self._dead_pid())
        lc.setup_logging(book_path=str(test_book), audit=True)
        text = trail()
        assert "INTERRUPTED  create_transactions started" in text
        assert "may have been committed" in text
        assert not (directory / lc._INTENT_NAME).exists()

    def test_and_before_the_next_write(self, audited):
        lc, tool, directory, _seen, trail = audited
        self._leave(lc, directory, self._dead_pid())
        tool(description="ok")
        assert trail().count("INTERRUPTED") == 1

    def test_an_intent_of_a_running_server_is_left_alone(
        self, audited, test_book,
    ):
        import os
        lc, _tool, directory, _seen, trail = audited
        self._leave(lc, directory, os.getppid())
        lc.setup_logging(book_path=str(test_book), audit=True)
        assert "INTERRUPTED" not in trail()
        assert (directory / lc._INTENT_NAME).exists()


@pytest.mark.skipif(
    __import__("os").name != "posix", reason="POSIX ownership and links",
)
class TestC53TheLogDirOverrideKeepsThePerBookChecks:
    def test_a_symlinked_book_folder_is_refused(
        self, test_book, tmp_path, monkeypatch,
    ):
        from gnucash_mcp.logging_config import resolve_mcp_dir

        logs = tmp_path / "logs"
        elsewhere = tmp_path / "elsewhere"
        logs.mkdir()
        elsewhere.mkdir()
        (logs / f"{test_book.name}.mcp").symlink_to(elsewhere)
        monkeypatch.setenv("GNUCASH_LOG_DIR", str(logs))
        with pytest.raises(ValueError, match="symlink"):
            resolve_mcp_dir(test_book)

    def test_a_real_folder_is_used(self, test_book, tmp_path, monkeypatch):
        from gnucash_mcp.logging_config import resolve_mcp_dir

        logs = tmp_path / "logs"
        (logs / f"{test_book.name}.mcp").mkdir(parents=True)
        monkeypatch.setenv("GNUCASH_LOG_DIR", str(logs))
        assert resolve_mcp_dir(test_book) == logs / f"{test_book.name}.mcp"

    def test_a_link_planted_at_the_temp_name_is_not_followed(self, tmp_path):
        from gnucash_mcp.logging_config import write_private_file

        victim = tmp_path / "victim.txt"
        victim.write_text("precious")
        state = tmp_path / ".state-book.json"
        (tmp_path / ".state-book.json.tmp").symlink_to(victim)
        write_private_file(state, '{"ok": true}')
        assert victim.read_text() == "precious"
        assert state.read_text() == '{"ok": true}'
        assert not state.is_symlink()
        assert (state.stat().st_mode & 0o777) == 0o600

    def test_backup_state_goes_through_it(self, test_book, tmp_path):
        gb = GnuCashBook(str(test_book))
        gb.create_backup(skip_unchanged=True)
        anchors = list(gb._backups_dir().glob(".manual-*.json"))
        assert len(anchors) == 1
        assert (anchors[0].stat().st_mode & 0o777) == 0o600


@pytest.mark.skipif(
    __import__("os").name != "posix", reason="POSIX file modes",
)
class TestSEC15FilesFromBefore15AreTightened:
    def test_old_audit_logs_and_backups_become_owner_only(
        self, test_book, tmp_path, monkeypatch,
    ):
        import os

        from gnucash_mcp import logging_config as lc

        monkeypatch.setenv("GNUCASH_LOG_DIR", str(tmp_path / "logs"))
        folder = lc.resolve_mcp_dir(test_book)
        old = [
            folder / "audit" / "2026-08-01.txt",
            folder / "backups" / "book-20260801T000000-manual.gnucash",
        ]
        for f in old:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("old")
            os.chmod(f, 0o644)
        outside = tmp_path / "outside.txt"
        outside.write_text("not ours to touch")
        os.chmod(outside, 0o644)
        (folder / "audit" / "link.txt").symlink_to(outside)
        try:
            lc.setup_logging(book_path=str(test_book), audit=True)
        finally:
            lc.setup_logging(book_path=None, audit=False, debug=False)
        for f in old:
            assert (f.stat().st_mode & 0o777) == 0o600, f
        assert (outside.stat().st_mode & 0o777) == 0o644


class TestIV20ControlCharactersInFreeText:
    @pytest.mark.parametrize("bad", ["\x1b[2J", "a\x08b", "x\x7f", "y\x85z"])
    def test_refused_in_description_memo_and_notes(self, test_book, bad):
        gb = GnuCashBook(str(test_book))
        for field in ("description", "notes", "memo"):
            kw = {"description": "Probe", "notes": None}
            splits = [
                {"account": "Assets:Checking", "amount": "-1"},
                {"account": "Expenses:Groceries", "amount": "1"},
            ]
            if field == "memo":
                splits[0]["memo"] = bad
            else:
                kw[field] = bad
            with pytest.raises(ValueError, match="control character"):
                gb.create_transaction(
                    splits=splits, trans_date=date(2026, 5, 1), **kw,
                )

    def test_tabs_and_line_breaks_in_notes_still_pass(self, test_book):
        gb = GnuCashBook(str(test_book))
        made = gb.create_transaction(
            description="Probe", notes="line one\nline two\ttabbed\r\n",
            splits=[
                {"account": "Assets:Checking", "amount": "-1"},
                {"account": "Expenses:Groceries", "amount": "1"},
            ],
            trans_date=date(2026, 5, 1),
        )
        assert made["guid"]


class TestIV21AccountNamesNobodyCanTellApart:
    @pytest.mark.parametrize("name", [
        "Gro​ceries", "‮seirecorG", "Dining﻿", "A⁦b⁩",
    ])
    def test_invisible_and_direction_characters_are_refused(
        self, test_book, name,
    ):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="invisible or direction"):
            gb.create_account(
                name=name, account_type="EXPENSE", parent="Expenses",
            )

    def test_a_lookalike_of_a_sibling_is_refused(self, test_book):
        gb = GnuCashBook(str(test_book))
        # A zero-width joiner, which names in some scripts need.
        with pytest.raises(ValueError, match="looks the same as"):
            gb.create_account(
                name="Gro‍ceries", account_type="EXPENSE",
                parent="Expenses",
            )
        gb.create_account(name="Café", account_type="EXPENSE", parent="Expenses")
        with pytest.raises(ValueError, match="looks the same as"):
            gb.create_account(
                name="Café", account_type="EXPENSE", parent="Expenses",
            )
        with pytest.raises(ValueError, match="looks the same as"):
            gb.update_account("Expenses:Café", new_name="Gro‍ceries")

    def test_scripts_that_need_joiners_keep_them(self, test_book):
        gb = GnuCashBook(str(test_book))
        made = gb.create_account(
            name="می‌خواهم", account_type="EXPENSE", parent="Expenses",
        )
        assert made["status"] == "created"

    def test_only_format_characters_is_not_a_name(self, test_book):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="no visible characters"):
            gb.create_account(
                name="‍‌", account_type="EXPENSE", parent="Expenses",
            )


class TestSideFinding12FourByteCharactersAreFound:
    """The scan behind the utf8mb3 refusal; the refusal itself runs
    against a real MySQL server in tests/test_db_backend.py."""

    def test_in_every_parameter_shape(self):
        from gnucash_mcp.book._base import _first_four_byte_character as f

        assert f({"a": "plain", "b": "ramen \U0001F35C"}) == "\U0001F35C"
        assert f(("x", 3, None, "\U0001F600")) == "\U0001F600"
        assert f([{"a": "ok"}, {"a": "\U0001F600"}]) == "\U0001F600"
        assert f({"a": "Café 日本語 €", "n": 3, "b": b"\xf0"}) is None
        assert f(None) is None


class TestC63DateRangesAreDecidedOnTheDecodedDate:
    """A row stored anywhere but 10:59:00 in the dashed form used to
    be compared as stored: on the wrong side of a report boundary."""

    def _march_first(self, gb, amount):
        return gb.create_transaction(
            description=f"Boundary {amount}",
            splits=[
                {"account": "Expenses:Groceries", "amount": amount},
                {"account": "Assets:Checking", "amount": f"-{amount}"},
            ],
            trans_date=date(2025, 3, 1), check_duplicates=False,
        )["guid"]

    def _restamp(self, path, guid_prefix, stored):
        _q(
            path,
            "update transactions set post_date = ? where guid like ?",
            (stored, guid_prefix + "%"),
        )

    def _march_spend(self, gb):
        report = gb.spending_by_category(
            start_date=date(2025, 3, 1), end_date=date(2025, 3, 31),
            compact=False,
        )
        return Decimal(str(report["total"]))

    def _february_spend(self, gb):
        report = gb.spending_by_category(
            start_date=date(2025, 2, 1), end_date=date(2025, 2, 28),
            compact=False,
        )
        return Decimal(str(report["total"]))

    def test_a_row_stamped_at_local_midnight(self, test_book):
        from datetime import datetime, timezone

        gb = GnuCashBook(str(test_book))
        before = self._march_spend(gb), self._february_spend(gb)
        guid = self._march_first(gb, "17.00")
        # What a local-midnight writer stores for 2025-03-01, here.
        local_midnight = datetime(2025, 3, 1).astimezone()
        stored = local_midnight.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self._restamp(test_book, guid.lstrip("%"), stored)
        assert gb.get_transaction(guid)["date"][:10] == "2025-03-01"
        assert self._march_spend(gb) - before[0] == Decimal("17.00")
        assert self._february_spend(gb) == before[1]

    def test_a_row_in_the_compact_form(self, test_book):
        gb = GnuCashBook(str(test_book))
        before = self._march_spend(gb), self._february_spend(gb)
        guid = self._march_first(gb, "23.00")
        self._restamp(test_book, guid.lstrip("%"), "20250301105900")
        assert gb.get_transaction(guid)["date"][:10] == "2025-03-01"
        assert self._march_spend(gb) - before[0] == Decimal("23.00")
        # A compact string sorts after every dashed date of its year:
        # it used to be missing from March and present nowhere, or
        # counted in a later range of the same year.
        assert self._february_spend(gb) == before[1]
        later = gb.spending_by_category(
            start_date=date(2025, 6, 1), end_date=date(2025, 6, 30),
            compact=False,
        )
        assert Decimal(str(later["total"])) == 0

    def test_a_book_without_compact_rows_is_not_scanned_twice(
        self, test_book,
    ):
        gb = GnuCashBook(str(test_book))
        with gb.open(readonly=True) as book:
            assert gb._has_compact_post_dates(book) is False
            assert gb._compact_dates_cache[1] is False
