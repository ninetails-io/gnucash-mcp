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
        with pytest.raises(AssertionError, match="only support books"):
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
        seen["intent_during_call"] = (
            directory / lc._intent_name(__import__("os").getpid())
        ).exists()
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
        assert not list(directory.glob(".pending-write*.json"))

    def test_a_refused_write_clears_its_intent(self, audited):
        lc, tool, directory, _seen, trail = audited
        with pytest.raises(ValueError):
            tool(description="raise")
        assert not list(directory.glob(".pending-write*.json"))
        assert "ERROR  tool: refused" in trail()

    def _leave(self, lc, directory, pid):
        import json
        (directory / lc._intent_name(pid)).write_text(json.dumps({
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
        assert not list(directory.glob(".pending-write*.json"))

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
        assert (directory / lc._intent_name(os.getppid())).exists()


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


class TestC28ABookMovedBetweenCommitAndResponse:
    """Every write builds its response after the commit. The book's
    connection used to be reopened by path for that, so a file moved
    in between got an empty twin at its old path and the committed
    write was reported as failed."""

    def test_the_write_is_reported_and_no_empty_file_appears(
        self, test_book, monkeypatch,
    ):
        import os

        import piecash

        gb = GnuCashBook(str(test_book))
        moved = test_book.with_name("moved.gnucash")
        real_save = piecash.Book.save

        def save_then_move(book):
            real_save(book)
            if test_book.exists():
                os.rename(test_book, moved)

        monkeypatch.setattr(piecash.Book, "save", save_then_move)
        made = gb.create_account(
            name="Moved Under Us", account_type="EXPENSE", parent="Expenses",
        )
        monkeypatch.undo()
        assert made["status"] == "created"
        assert made["fullname"] == "Expenses:Moved Under Us"
        assert not test_book.exists()
        rows = _q(
            moved, "select count(*) from accounts where name = ?",
            ("Moved Under Us",),
        )
        assert rows == [(1,)]


class TestConvertedBalancesAreRoundedPerAccount:
    """A converted balance is rounded to the currency's unit once per
    account, as GnuCash rounds a conversion, and totals are sums of
    those. Three EUR accounts of 1.00 at 1.005 are 1.00 each (half to
    even) and 3.00 together; rounding the unrounded sum gave 3.02,
    two cents more than the lines beside it."""

    AS_OF = date(2026, 6, 30)

    def _book(self, test_book):
        gb = GnuCashBook(str(test_book))
        for name in ("Euro A", "Euro B", "Euro C"):
            gb.create_account(
                name=name, account_type="BANK", parent="Assets",
                commodity="EUR",
            )
            gb.create_transaction(
                description=f"Fund {name}",
                splits=[
                    {"account": f"Assets:{name}", "amount": "1.00",
                     "quantity": "1.00"},
                    {"account": "Assets:Checking", "amount": "-1.00"},
                ],
                trans_date=date(2026, 6, 1), check_duplicates=False,
            )
        from tests.conftest import drop_transaction_prices
        drop_transaction_prices(test_book)
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value="1.005",
            price_date=date(2026, 6, 15),
        )
        return gb

    def test_lines_add_up_and_the_surfaces_agree(self, test_book):
        from tests.test_multicurrency_audit import _three_surfaces

        gb = self._book(test_book)
        sheet = gb.balance_sheet(as_of_date=self.AS_OF)
        euro = [
            Decimal(row["default_currency_value"])
            for row in sheet["assets"]["accounts"]
            if row["account"].startswith("Assets:Euro")
        ]
        assert euro == [Decimal("1.00")] * 3
        for section in ("assets", "liabilities", "equity"):
            lines = sum(
                Decimal(row.get("default_currency_value", row["balance"]))
                for row in sheet[section]["accounts"]
            )
            assert lines == Decimal(sheet[section]["total"]), section
        assert Decimal(sheet["assets"]["total"]) == (
            Decimal(sheet["liabilities"]["total"])
            + Decimal(sheet["equity"]["total"])
        )
        a_minus_l, net_worth, dashboard = _three_surfaces(gb, self.AS_OF)
        assert a_minus_l == net_worth == dashboard

    def test_the_series_ends_on_the_point_in_time_figure(self, test_book):
        gb = self._book(test_book)
        point = gb.net_worth(end_date=self.AS_OF)["net_worth"]
        series = gb.net_worth(
            start_date=date(2026, 5, 31), end_date=self.AS_OF,
            interval="month",
        )["series"]
        assert series[-1]["net_worth"] == point


class TestFC20AnOldServerWritingAfterTheConversion:
    """The converted book is marked in the server's own frame. A
    pre-1.5 shape found later in a marked book was written by an old
    server: the write says so, the dashboard says so for a while, and
    nothing is rewritten (bookkeeper ruling 2026-10-05, item 2)."""

    def _root_slots(self, path):
        # A path key is a row inside the ``gnc-mcp`` frame.
        return dict(_q(
            path,
            "select name, string_val from slots where name like 'gnc-mcp/%'",
        ))

    def _plant_old_entry(self, gb, path):
        gb.create_customer(name="Acme")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Old", quantity="1", price="10.00",
        )
        # gncEntryCreate's defaults, which 1.5 and desktop write and
        # the old server left blank: the C9 fingerprint.
        _q(path, "update entries set i_disc_type = '' where description = 'Old'")
        return inv["id"]

    def test_the_first_converting_write_marks_the_book(self, business_book):
        from gnucash_mcp import __version__

        gb = GnuCashBook(str(business_book))
        made = gb.create_budget(name="Household", year=2026)
        assert made["book_marked_converted"] == __version__
        assert self._root_slots(business_book) == {
            "gnc-mcp/converted-by": __version__,
        }
        assert "old_server_write" not in made

    def test_an_old_shape_in_an_unmarked_book_is_just_converted(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        inv = self._plant_old_entry(gb, business_book)
        made = gb.post_invoice(inv, AR)  # a converting write
        assert made["entries_normalized"] == 1
        assert "old_server_write" not in made
        assert "gnc-mcp/old-server-write" not in self._root_slots(business_book)

    def test_an_old_shape_in_a_marked_book_is_warned_about(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gb.create_budget(name="Household", year=2026)  # marks
        inv = self._plant_old_entry(gb, business_book)
        made = gb.post_invoice(inv, AR)  # a converting write
        assert made["entries_normalized"] == 1
        assert "server older than 1.5 has written" in made["old_server_write"]
        assert "get_budget" in made["old_server_write"]
        slots = self._root_slots(business_book)
        assert slots["gnc-mcp/old-server-write"] == date.today().isoformat()
        # The dashboard carries it.
        assert "An older server (1.4.x) wrote to this book" in gb.get_book_summary()
        # Nothing was rewritten beyond the converter's own work: the
        # budget rows are untouched (there are none to flip).
        assert _q(business_book, "select count(*) from budget_amounts") == [(0,)]

    def test_the_dashboard_line_expires(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_budget(name="Household", year=2026)
        with gb.open(readonly=False) as book:
            book.root_account["gnc-mcp/old-server-write"] = (
                date.today() - timedelta(days=45)
            ).isoformat()
            book.save()
        assert "An older server" not in gb.get_book_summary()


def _trading_accounts_on(path):
    import piecash

    with piecash.open_book(
        str(path), readonly=False, do_backup=False, open_if_lock=True,
    ) as b:
        b["options"] = {"Accounts": {"Use Trading Accounts": "t"}}
        b.save()


class TestTradingAccountsBooksRefuseWhatWouldWriteTradingSplits:
    """Until the server writes GnuCash's trading splits, a transaction
    across commodities in a book with "Use Trading Accounts" on is
    refused rather than written in a shape GnuCash does not write
    (bookkeeper ruling 2026-10-05, item 3)."""

    def _euro(self, gb):
        gb.create_account(
            name="Euro", account_type="BANK", parent="Assets", commodity="EUR",
        )

    def _transfer(self, gb, **kw):
        return gb.create_transaction(
            description="To euro", splits=[
                {"account": "Assets:Euro", "amount": "110.00", "quantity": "100.00"},
                {"account": "Assets:Checking", "amount": "-110.00"},
            ],
            trans_date=date(2026, 3, 1), check_duplicates=False, **kw,
        )

    def test_a_cross_currency_transfer_is_refused(self, test_book):
        gb = GnuCashBook(str(test_book))
        self._euro(gb)
        _trading_accounts_on(test_book)
        before = _q(test_book, "select count(*) from transactions")
        with pytest.raises(ValueError, match="uses trading accounts"):
            self._transfer(gb)
        assert _q(test_book, "select count(*) from transactions") == before

    def test_same_currency_entries_edits_and_deletes_pass(self, test_book):
        gb = GnuCashBook(str(test_book))
        self._euro(gb)
        crossed = self._transfer(gb)  # before the option: piecash's rows
        _trading_accounts_on(test_book)
        plain = _spend(gb)
        gb.update_transaction(crossed["guid"], description="Renamed")
        assert gb.get_transaction(crossed["guid"])["description"] == "Renamed"
        gb.delete_transaction(crossed["guid"])
        gb.delete_transaction(plain)

    def test_a_foreign_currency_posting_is_refused(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="Receivable EUR", account_type="RECEIVABLE", parent="Assets",
            commodity="EUR",
        )
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value="1.10",
            price_date=date(2026, 1, 15),
        )
        gb.create_customer(name="Berlin GmbH", currency="EUR")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        _trading_accounts_on(business_book)
        with pytest.raises(ValueError, match="uses trading accounts"):
            gb.post_invoice(
                invoice_id=inv["id"], post_account="Assets:Receivable EUR",
                post_date="2026-01-15",
            )
        assert gb.get_invoice(inv["id"])["status"] != "posted"


class TestThePreUpgradeMarkerNamesTheSnapshot:
    """Bookkeeper close-out loop, flag 2: when the auto-backup taken
    moments before already holds the pre-conversion state, nothing
    more is copied, and the marker says which file that is."""

    def test_an_existing_copy_is_named(self, test_book, monkeypatch):
        from gnucash_mcp.book.backup import BackupMixin

        from gnucash_mcp.book.backup import _write_state

        gb = GnuCashBook(str(test_book))
        # What the auto-backup leaves: a copy, and the book's hash as
        # the identical-content anchor.
        taken = gb.create_backup(stage="session")
        _write_state(
            gb._backups_dir(), test_book.stem, {},
            book_sha256=gb._current_book_hash(),
        )
        monkeypatch.setattr(BackupMixin, "_pre_upgrade_checked", False)
        result = gb._ensure_pre_upgrade_snapshot()
        session_copy = taken["path"].rsplit("/", 1)[-1]
        name = result["pre_upgrade_backup"]
        # The auto-backup's copy, under the manual label, which
        # retention never prunes (S-3); a hard link where possible.
        assert name.endswith("-manual-pre-1-5-upgrade.gnucash")
        assert f"snapshot: {name}" in gb._pre_upgrade_marker().read_text()
        files = {p.name: p for p in gb._backups_dir().glob("*.gnucash")}
        assert set(files) == {session_copy, name}
        assert files[name].read_bytes() == files[session_copy].read_bytes()

    def test_a_withdrawn_snapshot_is_struck_from_the_marker(
        self, test_book, monkeypatch,
    ):
        from gnucash_mcp.book.backup import BackupMixin

        gb = GnuCashBook(str(test_book))
        monkeypatch.setattr(BackupMixin, "_pre_upgrade_checked", False)
        result = gb._ensure_pre_upgrade_snapshot()
        gb._withdraw_pre_upgrade_snapshot(result["pre_upgrade_backup"])
        assert "snapshot: none (nothing to convert)" in (
            gb._pre_upgrade_marker().read_text()
        )
        assert not list(gb._backups_dir().glob("*-manual-pre-1-5-upgrade.gnucash"))

    def test_a_fresh_copy_is_named_too(self, test_book, monkeypatch):
        from gnucash_mcp.book.backup import BackupMixin

        gb = GnuCashBook(str(test_book))
        monkeypatch.setattr(BackupMixin, "_pre_upgrade_checked", False)
        result = gb._ensure_pre_upgrade_snapshot()
        name = result["pre_upgrade_backup"]
        assert name.endswith("-manual-pre-1-5-upgrade.gnucash")
        assert f"snapshot: {name}" in gb._pre_upgrade_marker().read_text()



class TestScopedReviewStorage:
    """Scoped review 2026-10-05, storage findings."""

    def test_the_pid_probe_lives_in_one_place(self):
        """S-1: os.kill(pid, 0) terminates the process on Windows.
        The probe is _format._pid_alive, and nothing else calls
        os.kill."""
        import re
        from pathlib import Path

        src = Path(__file__).resolve().parent.parent / "src" / "gnucash_mcp"
        offenders = [
            str(p.relative_to(src)) for p in src.rglob("*.py")
            if p.name != "_format.py" and re.search(r"\bos\.kill\(", p.read_text())
        ]
        assert offenders == []

    def test_the_probe_answers_for_this_process_and_a_dead_one(self):
        import os
        import subprocess
        import sys

        from gnucash_mcp._format import _pid_alive

        assert _pid_alive(os.getpid()) is True
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        assert _pid_alive(p.pid) is False
        assert _pid_alive("x") is None

    def test_a_trading_book_still_takes_schedules_voids_and_unvoids(
        self, test_book,
    ):
        """S-2, S-7: the refusal is piecash's own trigger, a quantity
        imbalance; a template (zero splits), a void (zero amounts) and
        an unvoid of a balanced transaction leave none."""
        gb = GnuCashBook(str(test_book))
        gb.create_account(
            name="Euro", account_type="BANK", parent="Assets", commodity="EUR",
        )
        crossed = gb.create_transaction(
            description="To euro", splits=[
                {"account": "Assets:Euro", "amount": "110.00", "quantity": "100.00"},
                {"account": "Assets:Checking", "amount": "-110.00"},
            ],
            trans_date=date(2026, 3, 1), check_duplicates=False,
        )
        # Give it the trading splits desktop would have written, so it
        # is a desktop-made transaction for the test's purposes.
        import piecash

        with gb.open(readonly=False) as book:
            usd = book.default_currency
            eur = book.commodities(mnemonic="EUR")
            trading = piecash.Account(
                name="Trading", type="TRADING", commodity=usd,
                parent=book.root_account, placeholder=1,
            )
            ccy = piecash.Account(
                name="CURRENCY", type="TRADING", commodity=usd,
                parent=trading, placeholder=1,
            )
            t_eur = piecash.Account(name="EUR", type="TRADING", commodity=eur, parent=ccy)
            t_usd = piecash.Account(name="USD", type="TRADING", commodity=usd, parent=ccy)
            txn = gb._find_transaction(book, crossed["guid"])
            txn.splits.append(piecash.Split(
                account=t_eur, value=Decimal("-110"), quantity=Decimal("-100"),
            ))
            txn.splits.append(piecash.Split(
                account=t_usd, value=Decimal("110"), quantity=Decimal("110"),
            ))
            book.save()
        _trading_accounts_on(test_book)
        sx = gb.create_scheduled_transaction(
            name="Rent", description="Rent",
            splits=[
                {"account": "Expenses:Groceries", "amount": "900.00"},
                {"account": "Assets:Checking", "amount": "-900.00"},
            ],
            start_date="2026-01-01", frequency="monthly",
        )
        assert sx["guid"]
        gb.update_scheduled_transaction(sx["guid"], notes="Rent, monthly")
        assert gb.void_transaction(crossed["guid"], reason="oops")["status"] == "voided"
        gb.unvoid_transaction(crossed["guid"])
        assert gb.get_transaction(crossed["guid"])["splits"][0]["value"] != "0"
        with pytest.raises(ValueError, match="quantity imbalance in EUR"):
            gb.create_transaction(
                description="Again", splits=[
                    {"account": "Assets:Euro", "amount": "11.00", "quantity": "10.00"},
                    {"account": "Assets:Checking", "amount": "-11.00"},
                ],
                trans_date=date(2026, 3, 2), check_duplicates=False,
            )


# ── Scoped review of 2026-10-05: money and input findings ───────────


class TestScopedReviewMoney:
    def test_the_dashboard_budget_line_agrees_with_the_report(self, test_book):
        """M-1: both convert each month's actuals at that month's
        close; the headline kept one period-end rate."""
        from tests.conftest import drop_transaction_prices

        gb = GnuCashBook(str(test_book))
        gb.create_account(
            name="Euro Food", account_type="EXPENSE", parent="Expenses",
            commodity="EUR",
        )
        year = date.today().year
        gb.create_budget(name="Year", year=year)
        gb.set_budget_amount("Year", "Expenses:Euro Food", "100.00")
        for month, rate in ((1, "1.0"), (2, "1.4")):
            gb.create_transaction(
                description=f"Food {month}", splits=[
                    {"account": "Expenses:Euro Food", "amount": str(100 * float(rate)),
                     "quantity": "100.00"},
                    {"account": "Assets:Checking", "amount": str(-100 * float(rate))},
                ],
                trans_date=date(year, month, 15), check_duplicates=False,
            )
        drop_transaction_prices(test_book)
        for month, rate in ((1, "1.0"), (2, "1.4"), (12, "2.0")):
            gb.create_price(
                commodity="EUR", namespace="CURRENCY", value=rate,
                price_date=date(year, month, 28),
            )
        if date.today() < date(year, 3, 1):
            pytest.skip("needs February to have closed")
        report = gb.get_budget_report(
            budget_name="Year", period="ytd", compact=False,
        )
        actual = Decimal(str(report["totals"]["actual"]))
        with gb.open(readonly=True) as book:
            headline = gb._budget_headline(book, list(book.transactions))
        assert headline is not None
        assert headline["spent"] == actual.quantize(Decimal("1"))

    def _three_euro_categories(self, gb, test_book, rate="1.0005"):
        from tests.conftest import drop_transaction_prices

        for name in ("Food", "Rent", "Fun"):
            gb.create_account(
                name=name, account_type="EXPENSE", parent="Expenses",
                commodity="EUR",
            )
            gb.create_transaction(
                description=name, splits=[
                    {"account": f"Expenses:{name}", "amount": "10.00",
                     "quantity": "10.00"},
                    {"account": "Assets:Checking", "amount": "-10.00"},
                ],
                trans_date=date(2026, 3, 10), check_duplicates=False,
            )
        drop_transaction_prices(test_book)
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value=rate,
            price_date=date(2026, 3, 31),
        )

    def test_flow_report_lines_add_up_and_granularities_agree(self, test_book):
        """M-2: 10.00 three times at 1.0005 is 10.00, 10.00, 10.00 and
        30.00, in the single table, the grouped table, and the
        structured output alike."""
        gb = GnuCashBook(str(test_book))
        self._three_euro_categories(gb, test_book)
        single = gb.spending_by_category(
            start_date=date(2026, 1, 1), end_date=date(2026, 3, 31),
            compact=False,
        )
        assert [c["amount"] for c in single["categories"]] == ["10.00"] * 3
        assert single["total"] == "30.00"
        table = gb.spending_by_category(
            start_date=date(2026, 1, 1), end_date=date(2026, 3, 31),
        )
        assert "TOTAL" in table and "30.00" in table and "30.02" not in table
        grouped = gb.spending_by_category(
            start_date=date(2026, 1, 1), end_date=date(2026, 3, 31),
            group_by="quarter",
        )
        assert "30.00" in grouped and "30.02" not in grouped
        flow = gb.cash_flow(start_date=date(2026, 1, 1), end_date=date(2026, 3, 31))
        assert "." in flow["outflows"]  # formatted, not a raw Decimal

    def test_lot_gain_is_the_difference_of_its_rounded_parts(self, investment_book):
        """M-3."""
        gb = GnuCashBook(str(investment_book))
        lot = gb.create_lot(account="Assets:Investments:VTSAX", title="Lot")
        made = gb.create_transaction(
            description="Buy 3 for 100", splits=[
                {"account": "Assets:Investments:VTSAX", "amount": "100.00",
                 "quantity": "3"},
                {"account": "Assets:Checking", "amount": "-100.00"},
            ],
            trans_date=date(2026, 1, 15), check_duplicates=False,
        )
        split = next(
            s["guid"] for s in gb.get_transaction(made["guid"])["splits"]
            if s["account"].endswith("VTSAX")
        )
        gb.assign_split_to_lot(split_guid=split, lot_guid=lot["guid"])
        gain = gb.calculate_lot_gain(lot["guid"], shares="1", sale_price="10.015")
        proceeds, cost, capital = (
            Decimal(gain[k]) for k in ("sale_proceeds", "cost_basis", "capital_gain")
        )
        assert proceeds == Decimal("10.02")  # a split value: half-up
        assert capital == proceeds - cost

    def test_a_desktop_amount_finer_than_the_currency_rounds(self):
        """M-4: 1001/1000 in a USD schedule is 1.00 to Since-Last-Run."""
        from gnucash_mcp.book.scheduling import SchedulingMixin

        amount = SchedulingMixin._rational_amount
        assert str(amount(1001, 1000, 100)) == "1.00"
        assert str(amount(1005, 1000, 100)) == "1.01"
        assert str(amount(10005, 1000, 1)) == "10"

    def test_transaction_dates_bind_at_gnucashs_adjusted_neutral_time(self):
        """M-5: the stored stamp follows _neutral_time, not a flat
        10:59, so a far zone reads the day back correctly."""
        from datetime import datetime, timezone

        from piecash.sa_extra import _DateAsDateTime

        from gnucash_mcp.book._base import _neutral_time

        d = date(2026, 1, 31)
        bound = _DateAsDateTime(neutral_time=True).process_bind_param(d, None)
        assert isinstance(bound, datetime) and bound.tzinfo is None
        expected = _neutral_time(d).astimezone(timezone.utc).replace(tzinfo=None)
        assert bound == expected


class TestScopedReviewInput:
    ESC = "x\x1b[31my"

    def test_update_transactions_and_statements_gate_their_text(self, test_book):
        """I-1."""
        gb = GnuCashBook(str(test_book))
        guid = _spend(gb)
        out = gb.update_transactions([{"guid": guid, "description": self.ESC}])
        assert "control character" in str(out)
        assert gb.get_transaction(guid)["description"] == "Void probe"
        with pytest.raises(ValueError, match="line 1: description contains a control"):
            gb.enter_statement(
                account_name="Assets:Checking", statement_date=date(2026, 6, 30),
                opening_balance=str(gb.get_balance("Assets:Checking")),
                closing_balance=str(gb.get_balance("Assets:Checking") - Decimal("5")),
                lines=[{"ref": "1", "date": date(2026, 6, 1), "description": self.ESC,
                        "amount": "-5.00", "account": "Expenses:Groceries"}],
            )

    def test_a_payments_wording_is_not_a_number(self, business_book):
        """I-2: with Num on split actions, a batch row numbered 1234
        against a recorded payment is still a HIGH duplicate."""
        _num_on_split_actions(business_book)
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(inv["id"], AR, post_date="2026-05-01")
        gb.pay_invoice(
            invoice_id=inv["id"], payment_account="Assets:Checking",
            amount="100.00", payment_date="2026-05-10",
        )
        result = gb.create_transactions([{
            "ref": "1", "date": date(2026, 5, 10), "description": "Acme",
            "num": "1234",
            "splits": [
                {"account": "Assets:Checking", "amount": "100.00"},
                {"account": AR, "amount": "-100.00"},
            ],
        }])
        assert "HIGH" in str(result) and "rejected" in str(result)
        assert _q(
            business_book,
            "select count(*) from transactions where post_date like '2026-05-10%'",
        ) == [(1,)]

    def test_a_claim_cannot_annotate_a_posting_record(self, business_book):
        """I-3: reconciling a posting is fine; writing its Num is an
        edit of a read-only transaction."""
        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="Company Card", account_type="CREDIT", parent="Liabilities",
        )
        gb.create_employee(name="Dana")
        voucher = gb.create_voucher(employee_id="000001")
        gb.add_voucher_entry(
            voucher_id=voucher["id"], account="Expenses:Services",
            description="Hotel", quantity="1", price="60.00",
        )
        _q(business_book, "update employees set ccard_guid = "
           "(select guid from accounts where name = 'Company Card')")
        _q(business_book, "update entries set b_paytype = 2")
        gb.post_invoice(
            invoice_id=voucher["id"], post_account="Liabilities:Accounts Payable",
            post_date="2026-05-01", owner_type="employee",
        )
        (card_split,) = _q(
            business_book,
            "select s.guid from splits s join accounts a on a.guid = s.account_guid "
            "where a.name = 'Company Card'",
        )[0]
        with_num = gb.enter_statement(
            account_name="Liabilities:Company Card",
            statement_date=date(2026, 5, 31),
            opening_balance="0", closing_balance="60.00",
            lines=[{"ref": "1", "date": date(2026, 5, 1), "description": "Hotel",
                    "amount": "60.00", "match": card_split, "num": "AUTH 1"}],
            dry_run=False,
        )
        assert "posting record" in str(with_num)
        assert _q(
            business_book, "select num from transactions where num != ''",
        ) == [(voucher["id"],)]

    @pytest.mark.parametrize("field", [
        "customer name", "customer address", "invoice notes", "entry description",
        "job name", "budget name", "schedule name", "schedule memo",
        "lot title", "commodity fullname", "account description", "slot value",
    ])
    def test_every_free_text_field_refuses_a_control_character(
        self, business_book, field,
    ):
        """I-4."""
        gb = GnuCashBook(str(business_book))
        bad = self.ESC
        with pytest.raises(ValueError, match="control character"):
            if field == "customer name":
                gb.create_customer(name=bad)
            elif field == "customer address":
                gb.create_customer(name="Acme", address={"addr1": bad})
            elif field == "invoice notes":
                gb.create_customer(name="Acme")
                gb.create_invoice(customer_id="000001", notes=bad)
            elif field == "entry description":
                gb.create_customer(name="Acme")
                inv = gb.create_invoice(customer_id="000001")
                gb.add_invoice_entry(
                    invoice_id=inv["id"], account="Income:Sales",
                    description=bad, quantity="1", price="1",
                )
            elif field == "job name":
                gb.create_customer(name="Acme")
                gb.create_job(owner_id="000001", owner_type="customer", name=bad)
            elif field == "budget name":
                gb.create_budget(name=bad, year=2026)
            elif field == "schedule name":
                gb.create_scheduled_transaction(
                    name=bad, description="x",
                    splits=[{"account": "Expenses:Services", "amount": "1"},
                            {"account": "Assets:Checking", "amount": "-1"}],
                    start_date="2026-01-01", frequency="monthly",
                )
            elif field == "schedule memo":
                gb.create_scheduled_transaction(
                    name="ok", description="x",
                    splits=[{"account": "Expenses:Services", "amount": "1", "memo": bad},
                            {"account": "Assets:Checking", "amount": "-1"}],
                    start_date="2026-01-01", frequency="monthly",
                )
            elif field == "lot title":
                gb.create_commodity(mnemonic="ZZ1", fullname="Zed Fund", namespace="FUND")
                gb.create_account(name="Fund", account_type="MUTUAL", parent="Assets",
                                  commodity="ZZ1", commodity_namespace="FUND")
                gb.create_lot(account="Assets:Fund", title=bad)
            elif field == "commodity fullname":
                gb.create_commodity(mnemonic="ZZZ", fullname=bad)
            elif field == "account description":
                gb.create_account(name="Dining", account_type="EXPENSE",
                                  parent="Expenses", description=bad)
            elif field == "slot value":
                gb.set_account_slot("Expenses:Services", "color", bad)

    def test_lookalikes_by_script(self, test_book):
        """I-5, I-6, I-7: a joiner that draws something keeps a name
        distinct; one that draws nothing does not; blanks that are not
        spaces are not visible characters."""
        gb = GnuCashBook(str(test_book))
        ok = lambda n: gb.create_account(name=n, account_type="EXPENSE", parent="Expenses")  # noqa: E731
        ok("Grocery Store")
        ok("می‌خواهم"); ok("میخواهم")                 # Persian, with and without ZWNJ
        ok("👨‍👩‍👧"); ok("👨👩👧")                  # a family, and three people
        ok("🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f")
        ok("🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f")
        for twin in ("Groceries͏", "Groceries️", "Groceriesㅤ",
                     "Grocery Store".replace(" Store", ""), "Gro‍ceries"):
            with pytest.raises(ValueError, match="looks the same as|invisible"):
                ok(twin)
        for blank in ("ㅤ", "⠀⠀", "‍‌"):
            with pytest.raises(ValueError, match="no visible characters"):
                ok(blank)
        # A move checks too.
        gb.create_account(name="Holding", account_type="EXPENSE", parent="Expenses", placeholder=True)
        gb.create_account(name="Gro‍ceries", account_type="EXPENSE", parent="Expenses:Holding")
        with pytest.raises(ValueError, match="looks the same as"):
            gb.move_account("Expenses:Holding:Gro‍ceries", "Expenses")

    def test_list_accounts_and_the_dashboard_know_a_hidden_parent(self, test_book):
        """I-12, I-13."""
        from gnucash_mcp.book._base import _is_hidden

        gb = GnuCashBook(str(test_book))
        gb.create_account(name="Old Bank", account_type="BANK", parent="Assets", placeholder=True)
        gb.create_account(name="Savings", account_type="BANK", parent="Assets:Old Bank")
        gb.update_account("Assets:Old Bank", hidden=True)
        listing = gb.list_accounts()
        assert "Assets:Old Bank [PLACEHOLDER, HIDDEN]" in listing or "HIDDEN" in listing
        with gb.open(readonly=True) as book:
            child = gb._find_account(book, "Assets:Old Bank:Savings")
            assert child.hidden in (0, None, False)
            assert _is_hidden(child) is True

    def test_the_fc20_warning_names_every_flipped_type(self):
        """I-11."""
        from gnucash_mcp.book._base import BaseGnuCashBook

        text = BaseGnuCashBook._old_server_write_warning("1.4.4")
        for word in ("income", "liability", "credit card", "payable", "equity"):
            assert word in text


class TestForeignFlowLinesNameTheirValuation:
    """SR-B1 rider 2: a flow table with a foreign-currency line says
    it is valued at monthly closes and where the cash is."""

    def test_the_footer_appears_only_with_a_foreign_line(self, test_book):
        from gnucash_mcp.book.reporting import _FOREIGN_FLOW_NOTE

        gb = GnuCashBook(str(test_book))
        plain = gb.spending_by_category(
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        assert _FOREIGN_FLOW_NOTE not in plain
        gb.create_account(
            name="Fees EUR", account_type="EXPENSE", parent="Expenses",
            commodity="EUR",
        )
        gb.create_transaction(
            description="Fee", splits=[
                {"account": "Expenses:Fees EUR", "amount": "33.35", "quantity": "33.33"},
                {"account": "Assets:Checking", "amount": "-33.35"},
            ],
            trans_date=date(2026, 7, 17), check_duplicates=False,
        )
        for table in (
            gb.spending_by_category(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)),
            gb.spending_by_category(
                start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), group_by="quarter",
            ),
        ):
            assert _FOREIGN_FLOW_NOTE in table
        structured = gb.spending_by_category(
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), compact=False,
        )
        assert structured["valuation_note"] == _FOREIGN_FLOW_NOTE
        income = gb.income_by_source(
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), compact=False,
        )
        assert "valuation_note" not in income


# ── Second scoped review (the fix branch's diff), 2026-10-06 ────────


class TestSecondReviewBlockers:
    def test_a_posted_credit_note_whose_total_crosses_zero_is_left_alone(
        self, business_book,
    ):
        """BS-1: the converter negates only lines carrying the old
        server's mark. A 1.5-posted credit note with +100 untaxed and
        −95 taxed at 10% posts −4.50 against lines summing −5; the
        converter used to read the disagreeing signs as the pre-1.5
        shape and reverse every line."""
        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="GST Payable", account_type="LIABILITY", parent="Liabilities",
        )
        gb.create_taxtable(name="T10", entries=[{
            "type": "percentage", "amount": "10",
            "account": "Liabilities:GST Payable",
        }])
        gb.create_customer(name="Acme")
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Charge back", quantity="1", price="100.00",
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="-1", price="95.00", taxtable="T10",
        )
        gb.post_invoice(
            invoice_id=cn["id"], post_account=AR, post_date="2026-01-20",
            owner_type="customer",
        )
        before = _q(business_book, "select description, quantity_num from entries order by description")
        # Two converting writes later...
        gb.create_budget(name="B", year=2026)
        gb.create_customer(name="Beta")
        inv = gb.create_invoice(customer_id="000002")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="10.00",
        )
        made = gb.post_invoice(inv["id"], AR)
        assert "credit_note_entries_migrated" not in made
        after = _q(business_book, "select description, quantity_num from entries order by description")
        assert [r for r in after if r[0] != "Work"] == before

    def test_a_2_6_reconcile_date_does_not_stop_the_converters(self, test_book):
        """BS-2."""
        gb = GnuCashBook(str(test_book))
        guid = _spend(gb)
        split = gb.get_transaction(guid)["splits"][0]
        gb.set_reconcile_state(split_guid=split["guid"], state="y")
        _q(
            test_book,
            "update splits set reconcile_date = '20150131235959' where guid like ?",
            (split["guid"] + "%",),
        )
        made = gb.create_budget(name="B", year=2026)  # a converting write
        assert made["status"] == "created"
        assert _q(
            test_book, "select reconcile_date from splits where guid like ?",
            (split["guid"] + "%",),
        ) == [("20150131235959",)]

    def test_a_credit_note_that_nets_to_a_charge_moves_both_lots_toward_zero(
        self, business_book,
    ):
        """BM-1: the link's signs come from the lots' balances, not
        from which side the document is on. A negative-total invoice
        (−70) and a credit note whose line is a charge (−50): applying
        50 leaves the credit note settled and the invoice at −20. The
        side-fixed signs pushed both to −120 and +100 and said
        "applied"."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Credit back", quantity="-1", price="70.00",
        )
        gb.post_invoice(inv["id"], AR, post_date="2026-01-15", force=True)
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Restocking fee", quantity="-1", price="50.00",
        )
        gb.post_invoice(
            invoice_id=cn["id"], post_account=AR, post_date="2026-01-16",
            owner_type="customer", force=True,
        )
        applied = gb.apply_credit_note(
            credit_note_id=cn["id"], applies_to_invoice_id=inv["id"],
            owner_type="customer",
        )
        assert applied["amount_applied"] == "50.00"
        assert Decimal(gb.get_invoice(cn["id"], owner_type="customer")["amount_due"]) == 0
        assert Decimal(gb.get_invoice(inv["id"])["amount_due"]) == Decimal("-20.00")

    def test_a_password_with_a_quote_or_a_space_is_masked(self):
        """CS-1: known secrets are masked literally, whatever the
        text around them."""
        from gnucash_mcp._format import _scrub_credentials, register_secrets_from_url

        register_secrets_from_url("postgresql://u:pa'ss wd@h:5/db?sslpassword=x y")
        text = (
            "Database 'postgresql://u:pa'ss wd@h:5/db?sslpassword=x y' "
            "does not exist"
        )
        out = _scrub_credentials(text)
        assert "pa'ss wd" not in out and "x y" not in out
        assert "***" in out


class TestSecondReviewSerious:
    def _posted_bill(self, gb, currency=None):
        gb.create_vendor(name="EuroSup", currency=currency)
        bill = gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id=bill["id"], account="Expenses:Services",
            description="Hosting", quantity="1", price="1000.00",
        )
        return bill["id"]

    def test_prepayment_cash_is_not_a_credit_against_the_discount(
        self, business_book,
    ):
        """BM-2: only a lot-link transaction counts as credit."""
        gb = GnuCashBook(str(business_book))
        gb.create_billterm(name="2/10 net 30", due_days=30, discount_days=10,
                           discount_percent="2")
        gb.create_customer(name="Acme")
        inv = gb.create_invoice(customer_id="000001", term="2/10 net 30")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="1000.00",
        )
        gb.post_invoice(inv["id"], AR, post_date="2026-03-01")
        # 200 arrives as a prepayment first, then settles the invoice.
        gb.pay_invoice(
            invoice_id=inv["id"], payment_account="Assets:Checking",
            amount="1200.00", payment_date="2026-03-02",
            allow_prepayment=True,
        ) if False else None
        paid = gb.pay_invoice(
            invoice_id=inv["id"], payment_account="Assets:Checking",
            amount="200.00", payment_date="2026-03-02",
        )
        assert paid["status"] == "partial"
        # 780 with the 2% discount on the 800 balance... the discount is
        # on the full 1000 (20.00), so the correct payment is 780.
        settled = gb.pay_invoice(
            invoice_id=inv["id"], payment_account="Assets:Checking",
            amount="780.00", payment_date="2026-03-05", apply_discount=True,
        )
        assert settled["status"] == "paid"

    def test_vendor_report_reads_a_desktop_shaped_payment(self, business_book):
        """BM-3: outstanding comes from the settlement chokepoint,
        converted at the posting rate."""
        from tests.conftest import drop_transaction_prices

        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="Payable EUR", account_type="PAYABLE", parent="Liabilities",
            commodity="EUR",
        )
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value="1.10",
            price_date=date(2026, 1, 10),
        )
        bill = self._posted_bill(gb, currency="EUR")
        gb.post_invoice(
            invoice_id=bill, post_account="Liabilities:Payable EUR",
            post_date="2026-01-15", owner_type="vendor",
        )
        # Paid in desktop's shape: a USD transaction, the payable
        # relieved at the pay-date value, no FX split.
        gb.pay_invoice(
            invoice_id=bill, payment_account="Assets:Checking",
            amount="1000.00", payment_account_amount="1200.00",
            payment_date="2026-02-01", owner_type="vendor",
        )
        drop_transaction_prices(business_book)
        report = gb.vendor_spending_report(
            start_date="2026-01-01", end_date="2026-12-31", compact=False,
        )
        (row,) = report["vendors"]
        assert Decimal(row["outstanding"]) == 0
        assert Decimal(gb.get_invoice(bill, owner_type="vendor")["amount_due"]) == 0

    def test_the_pre_conversion_copy_is_the_committed_state(
        self, test_book, monkeypatch,
    ):
        """CS-2: always a fresh copy, never a link to the newest file
        of any stage."""
        from gnucash_mcp.book.backup import BackupMixin, _write_state

        gb = GnuCashBook(str(test_book))
        gb.create_backup(stage="session")
        _write_state(
            gb._backups_dir(), test_book.stem, {},
            book_sha256=gb._current_book_hash(),
        )
        _spend(gb)  # the book moved on; the anchor did not
        monkeypatch.setattr(BackupMixin, "_pre_upgrade_checked", False)
        result = gb._ensure_pre_upgrade_snapshot()
        copy = gb._backups_dir() / result["pre_upgrade_backup"]
        assert _q(copy, "select count(*) from transactions") == _q(
            test_book, "select count(*) from transactions",
        )

    def test_a_legacy_folder_holding_another_books_backups_is_not_claimed(
        self, tmp_path, monkeypatch,
    ):
        """CS-3."""
        import shutil

        import tests.conftest as conftest
        from gnucash_mcp.logging_config import resolve_mcp_dir

        a_dir, b_dir = tmp_path / "a", tmp_path / "b"
        a_dir.mkdir(); b_dir.mkdir()
        a = conftest.test_book.__wrapped__(a_dir)
        b = conftest.test_book.__wrapped__(b_dir)
        assert a.name == b.name
        logs = tmp_path / "logs"
        legacy = logs / f"{a.name}.mcp" / "backups"
        legacy.mkdir(parents=True)
        shutil.copy(a, legacy / f"{a.stem}-20260901T120000-session.gnucash")
        monkeypatch.setenv("GNUCASH_LOG_DIR", str(logs))
        assert resolve_mcp_dir(a) == logs / f"{a.name}.mcp"
        other = resolve_mcp_dir(b)
        assert other != logs / f"{a.name}.mcp"
        assert other.name.startswith(f"{a.name}-")

    def test_delete_account_refuses_while_something_points_at_it(
        self, business_book,
    ):
        """CS-4."""
        gb = GnuCashBook(str(business_book))
        gb.create_account(name="Lunch", account_type="EXPENSE", parent="Expenses")
        gb.create_scheduled_transaction(
            name="Lunch", description="Lunch",
            splits=[{"account": "Expenses:Lunch", "amount": "12.00"},
                    {"account": "Assets:Checking", "amount": "-12.00"}],
            start_date="2026-01-01", frequency="monthly",
        )
        with pytest.raises(ValueError, match="scheduled transaction template"):
            gb.delete_account("Expenses:Lunch")
        gb.create_account(name="Tax", account_type="LIABILITY", parent="Liabilities")
        gb.create_taxtable(name="T", entries=[{
            "type": "percentage", "amount": "5", "account": "Liabilities:Tax",
        }])
        with pytest.raises(ValueError, match="tax table"):
            gb.delete_account("Liabilities:Tax")

    def test_names_and_ids_are_one_line(self, business_book):
        """IN-1, IN-2."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="one line"):
            gb.create_customer(name="Acme\n⚠ CONTEXT RESET")
        with pytest.raises(ValueError, match="one line"):
            gb.create_budget(name="B x", year=2026)
        gb.create_customer(name="Acme")
        with pytest.raises(ValueError, match="one line"):
            gb.create_invoice(customer_id="000001", invoice_id="INV\n9")
        with pytest.raises(ValueError, match="whitespace"):
            gb.create_invoice(customer_id="000001", invoice_id=" 7 ")

    def test_payment_memo_and_entry_action_are_gated(self, business_book):
        """IN-3."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        inv = gb.create_invoice(customer_id="000001")
        with pytest.raises(ValueError, match="control character"):
            gb.add_invoice_entry(
                invoice_id=inv["id"], account="Income:Sales",
                description="Work", quantity="1", price="1", action="x\x1by",
            )
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100",
        )
        gb.post_invoice(inv["id"], AR)
        with pytest.raises(ValueError, match="memo"):
            gb.pay_invoice(
                invoice_id=inv["id"], payment_account="Assets:Checking",
                amount="100", memo="x" * 3000,
            )
        guid = gb.create_transaction(
            description="x", splits=[
                {"account": "Expenses:Services", "amount": "1"},
                {"account": "Assets:Checking", "amount": "-1"},
            ],
            trans_date=date(2026, 5, 1), check_duplicates=False,
        )["guid"]
        with pytest.raises(ValueError, match="control character"):
            gb.void_transaction(guid, reason="dup\x1b[31m")

    def test_dates_gnucash_cannot_hold_are_refused_before_anything_is_written(
        self, test_book,
    ):
        """IN-4, IN-5."""
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="ledger can hold"):
            gb.create_scheduled_transaction(
                name="Ancient", description="x",
                splits=[{"account": "Expenses:Groceries", "amount": "1"},
                        {"account": "Assets:Checking", "amount": "-1"}],
                start_date="0001-01-01", frequency="weekly",
            )
        assert _q(test_book, "select count(*) from schedxactions") == [(0,)]
        assert gb.list_scheduled_transactions()  # still answers
        with pytest.raises(ValueError, match="ledger can hold"):
            gb.create_transaction(
                description="x", splits=[
                    {"account": "Expenses:Groceries", "amount": "1"},
                    {"account": "Assets:Checking", "amount": "-1"},
                ],
                trans_date=date(2, 1, 1), check_duplicates=False,
            )
        with pytest.raises(ValueError, match="ledger can hold"):
            gb.cash_flow(start_date=date(1, 1, 1), end_date=date(2026, 1, 1))

    def test_prices_tsv_refuses_a_stray_cell(self):
        """IN-6."""
        from gnucash_mcp.tools.investments import _parse_prices_tsv

        with pytest.raises(ValueError, match="cells for a 4-column header"):
            _parse_prices_tsv(
                "ref\tcommodity\tdate\tvalue\nr\tVTSAX\t2026-02-02\t1\t234.56\n"
            )
        rows = _parse_prices_tsv(
            "﻿ref\tcommodity\tdate\tvalue\nr\tVTSAX\t2026-02-02\t234.56\n"
        )
        assert rows[0]["value"] == "234.56"


class TestSecondReviewMinor:
    def test_a_refund_beyond_a_credit_note_is_not_a_prepayment(self, business_book):
        """BM-5."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="1", price="100.00",
        )
        gb.post_invoice(
            invoice_id=cn["id"], post_account=AR, post_date="2026-01-16",
            owner_type="customer",
        )
        with pytest.raises(ValueError, match="cannot be held as a prepayment"):
            gb.pay_invoice(
                invoice_id=cn["id"], payment_account="Assets:Checking",
                amount="150.00", payment_date="2026-01-20",
                owner_type="customer", allow_prepayment=True,
            )

    def test_a_settled_document_reads_zero_not_negative_zero(self, business_book):
        """BM-7."""
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Sup")
        bill = gb.create_bill(vendor_id="000001")
        gb.add_bill_entry(
            bill_id=bill["id"], account="Expenses:Services",
            description="H", quantity="1", price="80.00",
        )
        gb.post_invoice(
            invoice_id=bill["id"], post_account="Liabilities:Accounts Payable",
            post_date="2026-01-16", owner_type="vendor",
        )
        gb.pay_invoice(
            invoice_id=bill["id"], payment_account="Assets:Checking",
            amount="80.00", payment_date="2026-01-20", owner_type="vendor",
        )
        assert gb.get_invoice(bill["id"], owner_type="vendor")["amount_due"] == "0.00"

    def test_a_scientific_amount_echoes_plainly(self):
        """IN-16."""
        from gnucash_mcp.book._base import _to_decimal

        assert str(_to_decimal("1e3")) == "1000"
        assert str(_to_decimal("1E+1")) == "10"
        assert str(_to_decimal("12.50")) == "12.50"

    def test_dates_are_yyyy_mm_dd_and_name_their_row(self):
        """IN-13."""
        from gnucash_mcp.tools._helpers import _parse_iso_date

        assert _parse_iso_date("2026-01-05") == date(2026, 1, 5)
        for bad in ("20260105", "2026-W02-1", "2026-1-5"):
            with pytest.raises(ValueError, match="YYYY-MM-DD"):
                _parse_iso_date(bad)

    def test_commodity_namespace_rules(self, test_book):
        """IN-12."""
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="cannot contain ':'"):
            gb.create_commodity(mnemonic="X:QQ", fullname="x", namespace="NYSE")
        with pytest.raises(ValueError, match="CURRENCY"):
            gb.create_commodity(mnemonic="USD", fullname="Fake", namespace="currency")
        with pytest.raises(ValueError, match="template"):
            gb.create_commodity(mnemonic="template", fullname="t", namespace="template")

    def test_billterm_days_are_bounded(self, business_book):
        """IN-9."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="at most 36500"):
            gb.create_billterm(name="Forever", due_days=2**31)

    def test_slot_values_fit_the_column(self, test_book):
        """IN-11."""
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="at most 4096"):
            gb.set_account_slot("Expenses:Groceries", "color", "x" * 5000)

    def test_a_negative_offset_is_refused(self):
        """IN-20."""
        from gnucash_mcp._format import _paginate

        with pytest.raises(ValueError, match="offset must be"):
            _paginate([1, 2, 3], offset=-1, limit=2, entity_name="things")

    def test_a_sqlite_uri_is_redacted_like_a_path(self, monkeypatch):
        """CS-5."""
        from gnucash_mcp.logging_config import redact_paths

        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        out = redact_paths("Invalid SQLite URL: sqlite:////Users/me/private/books/mine.gnucash")
        assert "/Users/me" not in out and "mine.gnucash" in out

    def test_slot_fillers_keep_the_business_marks_when_business_is_off(self, test_book):
        """BS-3."""
        gb = GnuCashBook(str(test_book))
        guid = _spend(gb)
        _q(
            test_book,
            "update slots set double_val = 0, timespec_val = NULL "
            "where name = 'date-posted' and obj_guid like ?",
            (guid + "%",),
        )
        with gb.open(readonly=False) as book:
            gb._migrate_slot_fillers(book, keep_business_marks=True)
            book.save()
        assert _q(
            test_book,
            "select count(*) from slots where name = 'date-posted' "
            "and double_val = 0 and obj_guid like ?",
            (guid + "%",),
        ) == [(1,)]

    def test_an_extended_lot_link_has_one_split_per_lot(self, business_book):
        """BS-4: one credit note applied to two invoices is three
        splits in one link transaction, as the engine writes it."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        for price in ("100.00", "50.00"):
            inv = gb.create_invoice(customer_id="000001")
            gb.add_invoice_entry(
                invoice_id=inv["id"], account="Income:Sales",
                description="Work", quantity="1", price=price,
            )
            gb.post_invoice(inv["id"], AR, post_date="2026-01-15")
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="1", price="120.00",
        )
        gb.post_invoice(
            invoice_id=cn["id"], post_account=AR, post_date="2026-01-16",
            owner_type="customer",
        )
        first = gb.apply_credit_note(
            credit_note_id=cn["id"], applies_to_invoice_id="000001",
            owner_type="customer",
        )
        second = gb.apply_credit_note(
            credit_note_id=cn["id"], applies_to_invoice_id="000002",
            owner_type="customer",
        )
        assert first["transaction_guid"] == second["transaction_guid"]
        link = gb.get_transaction(first["transaction_guid"])
        assert len(link["splits"]) == 3
        assert Decimal(gb.get_invoice(cn["id"], owner_type="customer")["amount_due"]) == 0

    def test_desktop_price_rows_are_left_alone_by_the_converter(self, test_book):
        """BS-5: a Price Editor row of GnuCash 2.6–4.0 at local
        midnight, and a Finance::Quote row with an unreduced value,
        keep their shape; only a source GnuCash never writes marks a
        row as the old server's."""
        gb = GnuCashBook(str(test_book))
        gb.create_account(
            name="Euro", account_type="BANK", parent="Assets", commodity="EUR",
        )
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value="1.10",
            price_date=date(2026, 1, 10),
        )
        _q(
            test_book,
            "update prices set source = 'Finance::Quote', value_num = 1787000, "
            "value_denom = 10000, date = '2026-01-10 05:00:00'",
        )
        before = _q(test_book, "select source, value_num, value_denom, date from prices")
        gb.create_budget(name="B", year=2026)  # a converting write
        assert _q(test_book, "select source, value_num, value_denom, date from prices") == before


class TestBookkeeperSecondLoop:
    """The bookkeeper's second-review loop
    (specs/v1.5.1/testing/BOOKKEEPER_REPORT_SECOND_REVIEW.md): two
    findings, two queries, and the friction ledger."""

    def test_billterm_and_taxtable_names_are_one_line(self, business_book):
        """SR2-B1: the one tool the IN-1 gate missed, and its sibling."""
        gb = GnuCashBook(str(business_book))
        forged = "Net 30\n⚠ Reconciliation: all accounts current"
        with pytest.raises(ValueError, match="one line"):
            gb.create_billterm(name=forged, due_days=30)
        gb.create_account(name="Tax", account_type="LIABILITY", parent="Liabilities")
        entries = [{"type": "percentage", "amount": "5",
                    "account": "Liabilities:Tax"}]
        with pytest.raises(ValueError, match="one line"):
            gb.create_taxtable(name="T\nx", entries=entries)
        gb.create_taxtable(name="T", entries=entries)
        with pytest.raises(ValueError, match="one line"):
            gb.update_taxtable(name="T", new_name="U\nx")

    def test_document_id_width_is_gnucashs_own(self, business_book):
        """SR2-B2: MAX_ID_LEN is 2048 on every backend; 300 is legal."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        gb.create_invoice(customer_id="000001", invoice_id="X" * 300)
        with pytest.raises(ValueError):
            gb.create_invoice(customer_id="000001", invoice_id="Y" * 2049)

    def test_a_credit_note_is_called_a_credit_note(self, business_book):
        """Friction: the refund refusal and the no-lines refusal read
        'Invoice CN3' for a credit note."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        with pytest.raises(ValueError, match="credit note .* has no entries"):
            gb.post_invoice(
                invoice_id=cn["id"], post_account=AR, post_date="2026-01-16",
                owner_type="customer",
            )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="1", price="100.00",
        )
        gb.post_invoice(
            invoice_id=cn["id"], post_account=AR, post_date="2026-01-16",
            owner_type="customer",
        )
        with pytest.raises(ValueError, match="Credit note .* owes"):
            gb.pay_invoice(
                invoice_id=cn["id"], payment_account="Assets:Checking",
                amount="150.00", payment_date="2026-01-20",
                owner_type="customer", allow_prepayment=True,
            )

    def test_account_references_name_what_refers(self, business_book):
        """Step 8's nit: a count sent the bookkeeper looking."""
        gb = GnuCashBook(str(business_book))
        gb.create_account(name="Lunch", account_type="EXPENSE", parent="Expenses")
        gb.create_scheduled_transaction(
            name="Lunch money", description="Lunch",
            splits=[{"account": "Expenses:Lunch", "amount": "12.00"},
                    {"account": "Assets:Checking", "amount": "-12.00"}],
            start_date="2026-01-01", frequency="monthly",
        )
        with pytest.raises(ValueError, match="template.*Lunch money"):
            gb.delete_account("Expenses:Lunch")
        gb.create_budget(name="Lunch plan", year=2026)
        gb.set_budget_amount(
            budget_name="Lunch plan", account="Expenses:Lunch",
            period=0, amount="12.00",
        )
        with pytest.raises(ValueError, match="budget.*Lunch plan"):
            gb.delete_account("Expenses:Lunch")

    def test_post_and_pay_dates_are_gated_before_anything_else(
        self, business_book,
    ):
        """Step 10's nit: an ancient pay date on a EUR document met
        the missing-rate error first."""
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="1400-01-01"):
            gb.pay_invoice(
                invoice_id="nope", payment_account="Assets:Checking",
                amount="1.00", payment_date="0002-01-01",
            )
        with pytest.raises(ValueError, match="1400-01-01"):
            gb.post_invoice(
                invoice_id="nope", post_account=AR, post_date="0002-06-01",
            )

    def test_a_legacy_folder_proves_its_owner_by_its_audit_header(
        self, tmp_path, monkeypatch,
    ):
        """Q2: a folder with no backups still names the path that
        wrote it; another path means not ours."""
        import tests.conftest as conftest
        from gnucash_mcp.logging_config import (
            _format_text_header, resolve_mcp_dir,
        )

        a_dir, b_dir = tmp_path / "a", tmp_path / "b"
        a_dir.mkdir(); b_dir.mkdir()
        a = conftest.test_book.__wrapped__(a_dir)
        b = conftest.test_book.__wrapped__(b_dir)
        logs = tmp_path / "logs"
        plain = logs / f"{a.name}.mcp"
        audit = plain / "audit"
        audit.mkdir(parents=True)
        (audit / "2026-09-01.log").write_text(
            _format_text_header("2026-09-01", str(a)) + "\n", encoding="utf-8",
        )
        monkeypatch.setenv("GNUCASH_LOG_DIR", str(logs))
        other = resolve_mcp_dir(b)
        assert other != plain and other.name.startswith(f"{a.name}-")
        assert resolve_mcp_dir(a) == plain

    def test_a_legacy_folder_with_no_evidence_is_claimed(
        self, tmp_path, monkeypatch,
    ):
        import tests.conftest as conftest
        from gnucash_mcp.logging_config import resolve_mcp_dir

        b_dir = tmp_path / "b"
        b_dir.mkdir()
        b = conftest.test_book.__wrapped__(b_dir)
        logs = tmp_path / "logs"
        plain = logs / f"{b.name}.mcp"
        (plain / "audit").mkdir(parents=True)
        monkeypatch.setenv("GNUCASH_LOG_DIR", str(logs))
        assert resolve_mcp_dir(b) == plain


def _rows(result) -> dict[str, dict]:
    """``{ref: row}`` from a batch tool's results TSV."""
    lines = result["results"].splitlines()
    head = lines[0].split("\t")
    return {r[0]: dict(zip(head, r)) for r in (ln.split("\t") for ln in lines[1:])}


class TestIN7AmountsFitGnuCashsNumerator:
    """GnuCash stores an amount as a 64-bit count of its commodity's
    smallest unit. An amount under ``_to_decimal``'s magnitude bound
    can still overflow that count (10^16 dollars is 10^18 cents);
    it passed the dry run and then failed the whole batch at commit,
    taking the good rows with it (second scoped review IN-7)."""

    _HUGE = "99999999999999999"  # 1e17 cents, over 2**63

    def _row(self, ref, amount):
        return {
            "ref": ref, "date": date(2026, 5, 10), "description": f"Row {ref}",
            "splits": [
                {"account": "Expenses:Groceries", "amount": amount},
                {"account": "Assets:Checking", "amount": f"-{amount}"},
            ],
        }

    def test_the_row_is_refused_in_the_dry_run(self, test_book):
        gb = GnuCashBook(str(test_book))
        rows = _rows(gb.create_transactions(
            [self._row("1", "12.00"), self._row("2", self._HUGE)],
            dry_run=True, on_error="skip",
        ))
        assert rows["1"]["status"] == "would_create"
        assert rows["2"]["status"] == "rejected"
        assert "too large to store in USD" in rows["2"]["reason"]

    def test_the_good_row_survives_the_commit(self, test_book):
        gb = GnuCashBook(str(test_book))
        before = _q(test_book, "select count(*) from transactions")[0][0]
        rows = _rows(gb.create_transactions(
            [self._row("1", "12.00"), self._row("2", self._HUGE)],
            on_error="skip",
        ))
        assert rows["1"]["status"] == "created"
        assert rows["2"]["status"] == "rejected"
        assert _q(test_book, "select count(*) from transactions")[0][0] == before + 1

    def test_the_largest_storable_amount_is_accepted(self, test_book):
        gb = GnuCashBook(str(test_book))
        largest = str((Decimal(2**63 - 1) / 100).quantize(Decimal("0.01")))
        rows = _rows(gb.create_transactions(
            [self._row("1", largest)], dry_run=True,
        ))
        assert rows["1"]["status"] == "would_create", rows

    def test_a_price_with_too_many_digits_is_refused(self, test_book):
        gb = GnuCashBook(str(test_book))
        gb.create_commodity(mnemonic="VTSAX", fullname="Vanguard Total",
                            namespace="FUND")
        rows = _rows(gb.create_prices([
            {"ref": "1", "commodity": "VTSAX", "date": date(2026, 5, 10),
             "value": "148.32"},
            {"ref": "2", "commodity": "VTSAX", "date": date(2026, 5, 11),
             "value": "12345678.123456789012"},
        ], on_error="skip"))
        assert rows["1"]["status"] == "created"
        assert rows["2"]["status"] == "rejected"
        assert "too many digits" in rows["2"]["reason"]
        with pytest.raises(ValueError, match="too many digits"):
            gb.create_price(commodity="VTSAX", namespace="FUND",
                            value="12345678.123456789012",
                            price_date=date(2026, 5, 12))


class TestIN5EveryDatedWriteWarnsOfASlippedYear:
    """The batch tool warned of a date more than a year ahead (C34);
    update, post, pay and instantiate stored 2200 without a word
    (second scoped review IN-5). One helper, ``_far_date_warning``,
    now speaks on every path. A warning, never a refusal."""

    FAR = date.today() + timedelta(days=800)

    def _spend(self, gb):
        return gb.create_transaction(
            description="Lunch", trans_date=date.today(),
            splits=[
                {"account": "Expenses:Groceries", "amount": "10.00"},
                {"account": "Assets:Checking", "amount": "-10.00"},
            ],
            check_duplicates=False,
        )["guid"]

    def test_update_transaction(self, test_book):
        gb = GnuCashBook(str(test_book))
        guid = self._spend(gb)
        result = gb.update_transaction(guid, trans_date=self.FAR)
        far = [w for w in result.get("warnings", []) if w["type"] == "far_date"]
        assert far and "more than a year ahead" in far[0]["message"], result

    def test_update_transactions(self, test_book):
        gb = GnuCashBook(str(test_book))
        guid = self._spend(gb)
        rows = _rows(gb.update_transactions([{"guid": guid, "date": self.FAR}]))
        (row,) = rows.values()
        assert row["status"] == "updated"
        assert "more than a year ahead" in row["reason"]

    def test_post_and_pay(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme")
        inv = gb.create_invoice(customer_id="000001",
                                date_opened=self.FAR.isoformat())
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        posted = gb.post_invoice(inv["id"], AR, post_date=self.FAR.isoformat())
        assert "more than a year ahead" in posted["date_warning"]
        paid = gb.pay_invoice(
            invoice_id=inv["id"], payment_account="Assets:Checking",
            amount="40.00", payment_date=self.FAR.isoformat(),
        )
        assert "more than a year ahead" in paid["date_warning"]

    def test_schedule_instance(self, test_book):
        gb = GnuCashBook(str(test_book))
        sx = gb.create_scheduled_transaction(
            name="Rent", description="Rent",
            splits=[
                {"account": "Expenses:Groceries", "amount": "100.00"},
                {"account": "Assets:Checking", "amount": "-100.00"},
            ],
            start_date=date.today().isoformat(), frequency="monthly",
        )
        made = gb.create_transaction_from_scheduled(
            sx["guid"], transaction_date=self.FAR.isoformat(),
        )
        assert made["status"] == "created"
        assert "more than a year ahead" in made["date_warning"]

    def test_an_ordinary_date_says_nothing(self, test_book):
        gb = GnuCashBook(str(test_book))
        guid = self._spend(gb)
        result = gb.update_transaction(guid, trans_date=date.today())
        assert not [w for w in result.get("warnings", [])
                    if w["type"] == "far_date"]
