"""The MINOR tier of the 1.5 pre-release adversarial review.

One class per finding, named for it, each with the test that fails on
the code as reviewed. Findings and their cross-examination are in
``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md`` (sections 5, 7, 8).
"""

import sqlite3
from datetime import date, timedelta
from decimal import Decimal

import pytest

from gnucash_mcp.book import GnuCashBook

AR = "Assets:Accounts Receivable"
AP = "Liabilities:Accounts Payable"


def _q(path, sql, params=()):
    con = sqlite3.connect(str(path))
    try:
        rows = con.execute(sql, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


class TestC38CreateAccountReturnsAUsableReference:
    def test_the_returned_guid_is_the_form_tools_accept(self, test_book):
        gb = GnuCashBook(str(test_book))
        made = gb.create_account(
            name="Dining", account_type="EXPENSE", parent="Expenses",
        )
        assert made["guid"].startswith("%")
        # It goes straight back in.
        child = gb.create_account(
            name="Lunch", account_type="EXPENSE", parent=made["guid"],
        )
        assert child["fullname"] == "Expenses:Dining:Lunch"
        assert gb.get_balance(made["guid"]) == Decimal(0)


class TestBL22AJobAttachedBillIsABill:
    def test_post_pay_and_unpost_name_it_a_bill(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_vendor(name="Supplier Ltd")
        job = gb.create_job(owner_id="000001", owner_type="vendor", name="Refit")
        bill = gb.create_bill(vendor_id="000001", job_id=job["id"])
        gb.add_bill_entry(
            bill_id=bill["id"], account="Expenses:Services",
            description="Labour", quantity="1", price="80.00",
        )
        posted = gb.post_invoice(
            invoice_id=bill["id"], post_account=AP, owner_type="vendor",
        )
        paid = gb.pay_invoice(
            invoice_id=bill["id"], payment_account="Assets:Checking",
            amount="30.00", owner_type="vendor",
        )
        unposted = gb.unpost_invoice(invoice_id=bill["id"], owner_type="vendor")
        assert (posted["type"], paid["type"], unposted["type"]) == (
            "bill", "bill", "bill",
        )


class TestSideFinding7ApplyDateIsTheLinksDate:
    def test_the_response_names_the_transactions_date(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(invoice_id=inv["id"], post_account=AR,
                        post_date="2026-01-15")
        cn = gb.create_credit_note(owner_id="000001", owner_type="customer")
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="1", price="30.00",
        )
        gb.post_invoice(invoice_id=cn["id"], post_account=AR,
                        post_date="2026-01-20", owner_type="customer")

        applied = gb.apply_credit_note(
            credit_note_id=cn["id"], applies_to_invoice_id=inv["id"],
            owner_type="customer",
        )

        assert applied["apply_date"] == "2026-01-20"
        txn = gb.get_transaction(applied["transaction_guid"])
        assert str(txn["date"])[:10] == "2026-01-20"


class TestC64DueDateReadsTheColumnItsTypeNames:
    def test_a_gdate_row_is_not_read_as_1970(self, business_book):
        """Every slot row carries an epoch in ``timespec_val`` as
        filler; a GDate row read timespec-first was due 1970-01-01."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(invoice_id=inv["id"], post_account=AR,
                        post_date="2026-01-15")
        _q(
            business_book,
            "update slots set slot_type = 10, gdate_val = '20260214', "
            "timespec_val = '1970-01-01 00:00:00' "
            "where name = 'trans-date-due'",
        )
        row = gb.get_outstanding_invoices(compact=False)["invoices"][0]
        assert row["due_date"] == "2026-02-14"
        assert row["days_past_due"] < 20000


def _schedule(gb, **kw):
    return gb.create_scheduled_transaction(
        name="Rent", description="Rent",
        splits=[
            {"account": "Expenses:Groceries", "amount": "100.00"},
            {"account": "Assets:Checking", "amount": "-100.00"},
        ],
        start_date="2026-01-01", frequency="monthly", **kw,
    )


class TestSideFinding3AnExplicitDateStillStopsAtTheEnd:
    def test_a_date_past_the_schedules_end_is_refused(self, test_book):
        gb = GnuCashBook(str(test_book))
        sx = _schedule(gb, end_date="2026-03-31")
        with pytest.raises(ValueError, match="after 'Rent' ended"):
            gb.create_transaction_from_scheduled(
                sx["guid"], transaction_date="2026-06-01",
            )
        # A date inside the schedule's life still works.
        made = gb.create_transaction_from_scheduled(
            sx["guid"], transaction_date="2026-02-01",
        )
        assert made["status"] == "created"


class TestC27AFailedAdvanceLeavesNothingBehind:
    def test_the_instance_is_taken_back_out_and_the_retry_is_clean(
        self, test_book, monkeypatch,
    ):
        gb = GnuCashBook(str(test_book))
        sx = _schedule(gb)
        before = _q(test_book, "select count(*) from transactions")
        real = type(gb)._upgrade_book_shapes
        calls = {"n": 0}

        def failing(self, book):
            calls["n"] += 1
            raise RuntimeError("disk full")

        monkeypatch.setattr(type(gb), "_upgrade_book_shapes", failing)
        with pytest.raises(RuntimeError) as failure:
            gb.create_transaction_from_scheduled(sx["guid"])
        assert "nothing changed" in str(failure.value)
        assert calls["n"] == 1
        # No orphan instance, and the schedule has not moved.
        assert _q(test_book, "select count(*) from transactions") == before
        assert _q(
            test_book, "select instance_count from schedxactions",
        ) == [(0,)]

        monkeypatch.setattr(type(gb), "_upgrade_book_shapes", real)
        again = gb.create_transaction_from_scheduled(sx["guid"])
        assert again["status"] == "created"
        assert again["transaction_guid"]
        assert again["instance_count"] == 1


# ── input validation ────────────────────────────────────────────────

from gnucash_mcp.book._base import _to_decimal  # noqa: E402
from gnucash_mcp._format import _parse_update_tsv  # noqa: E402
from gnucash_mcp.tools.core import _parse_transactions_tsv  # noqa: E402


def _txn(ref, amount, d=date(2026, 5, 21), **second):
    return {
        "ref": str(ref), "date": d, "description": f"Row {ref}",
        "splits": [
            {"account": "Assets:Checking", "amount": f"-{amount}"},
            {"account": "Expenses:Groceries", "amount": str(amount), **second},
        ],
    }


def _results(env):
    lines = env["results"].split("\n")
    header = lines[0].split("\t")
    return {
        r["ref"]: r
        for r in (dict(zip(header, ln.split("\t"))) for ln in lines[1:])
    }


class TestIV27AnAmountIsPlainDigits:
    @pytest.mark.parametrize("text", [
        "1234.56", " 12 ", "-0.5", "+3", ".5", "5.", "1e3", "2.5E-2",
    ])
    def test_ordinary_numbers_parse(self, text):
        assert _to_decimal(text) == Decimal(text.strip())

    @pytest.mark.parametrize("text", [
        "5_000", "٥", "５", "１２.５", "1,000", "$10", "", "1 000",
    ])
    def test_lookalikes_are_refused(self, text):
        with pytest.raises(ValueError, match="not a valid decimal amount"):
            _to_decimal(text)

    def test_non_strings_still_convert(self):
        assert _to_decimal(94.87) == Decimal("94.87")
        assert _to_decimal(7) == Decimal(7)
        assert _to_decimal(Decimal("1.2345678901234567890123")) == Decimal(
            "1.2345678901234567890123"
        )


class TestC39AnAmountThatCannotBeStoredIsARowError:
    @pytest.mark.parametrize("text", ["1e17", "100000000000000000", "9e999998"])
    def test_too_large(self, text):
        with pytest.raises(ValueError, match="too large to store"):
            _to_decimal(text)

    @pytest.mark.parametrize("text", ["0.0000000000000000001", "1e-1000000"])
    def test_too_fine(self, text):
        with pytest.raises(ValueError, match="more decimal places"):
            _to_decimal(text)

    def test_the_largest_storable_amounts_still_pass(self):
        assert _to_decimal("99999999999999999") == Decimal("99999999999999999")
        assert _to_decimal("0.000000000000000001") == Decimal("1e-18")

    def test_a_batch_skips_the_row_and_keeps_the_rest(self, test_book):
        """It used to raise out of the save, past ``on_error="skip"``,
        after the dry run had said ``would_create``."""
        gb = GnuCashBook(str(test_book))
        rows = [_txn(1, "50.00"), _txn(2, "1" + "0" * 26)]
        dry = _results(gb.create_transactions(rows, dry_run=True, on_error="skip"))
        assert dry["2"]["status"] == "rejected"
        env = gb.create_transactions(rows, on_error="skip")
        got = _results(env)
        assert got["1"]["status"] == "created"
        assert got["2"]["status"] == "rejected"
        assert "too large" in env["results"]


class TestIV18DatesAtTheEdgeOfTheCalendar:
    @pytest.mark.parametrize("day", [date(1, 1, 1), date(9999, 12, 31)])
    def test_a_batch_row_is_rejected_not_the_batch_crashed(self, test_book, day):
        gb = GnuCashBook(str(test_book))
        env = gb.create_transactions(
            [_txn(1, "50.00"), _txn(2, "5.00", d=day)], on_error="skip",
        )
        got = _results(env)
        assert got["1"]["status"] == "created"
        assert got["2"]["status"] == "rejected"

    def test_the_look_ahead_window_is_bounded(self, test_book):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="between 0 and 3660"):
            gb.get_upcoming_transactions(days=3285000)
        assert gb.get_upcoming_transactions(days=30) is not None


class TestIV22ABomOnTheHeader:
    def test_the_header_parses(self):
        tsv = (
            "﻿ref\tdate\tdescription\tamount\taccount\tamount\taccount\n"
            "1\t2026-05-21\tLunch\t-12.00\tAssets:Checking\t12.00\t"
            "Expenses:Groceries"
        )
        rows = _parse_transactions_tsv(tsv)
        assert rows[0]["ref"] == "1"
        assert len(rows[0]["splits"]) == 2


class TestIV23ACellPastTheHeaderIsNotDropped:
    def test_the_row_is_refused(self):
        guid = "a" * 32
        with pytest.raises(ValueError, match="more cell.* than the header"):
            _parse_update_tsv(
                f"guid\tdescription\n{guid}\tCoffee\twith a stray tab"
            )
        # Trailing empty cells (a trailing tab) are still fine.
        assert _parse_update_tsv(
            f"guid\tdescription\n{guid}\tCoffee\t"
        ) == [{"guid": guid, "description": "Coffee"}]


class TestIV24AQuantityThatDisagreesOnASameCurrencySplit:
    def test_refused_with_the_reason(self, test_book):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="transaction's own currency"):
            gb.create_transaction(
                description="Lunch", trans_date=date(2026, 5, 21),
                splits=[
                    {"account": "Assets:Checking", "amount": "-12.00"},
                    {"account": "Expenses:Groceries", "amount": "12.00",
                     "quantity": "13.00"},
                ],
            )

    def test_one_that_agrees_is_harmless(self, test_book):
        gb = GnuCashBook(str(test_book))
        made = gb.create_transaction(
            description="Lunch", trans_date=date(2026, 5, 21),
            splits=[
                {"account": "Assets:Checking", "amount": "-12.00"},
                {"account": "Expenses:Groceries", "amount": "12.00",
                 "quantity": "12.00"},
            ],
        )
        assert made["guid"]


class TestIV21AccountNamesThatReadAsReferences:
    @pytest.mark.parametrize("name", [
        "%abcdef0", "0123456789abcdef0123456789abcdef", " Groceries",
        "Groceries ",
    ])
    def test_refused(self, test_book, name):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="Account name"):
            gb.create_account(
                name=name, account_type="EXPENSE", parent="Expenses",
            )

    def test_a_percent_sign_inside_a_name_is_fine(self, test_book):
        gb = GnuCashBook(str(test_book))
        made = gb.create_account(
            name="401k 5% match", account_type="EXPENSE", parent="Expenses",
        )
        assert made["fullname"] == "Expenses:401k 5% match"


class TestMM11AStatementBalanceFinerThanACent:
    def test_refused_rather_than_rounded(self, test_book):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="statement_balance"):
            gb.reconcile_account(
                account_name="Assets:Checking",
                statement_date=date(2024, 1, 31),
                statement_balance="100.005", reconcile_all=True,
            )


class TestBL23BillTermsThatCannotBeMet:
    @pytest.mark.parametrize("kw, text", [
        ({"due_days": -30}, "cannot be negative"),
        ({"due_days": 30, "discount_days": -1}, "cannot be negative"),
        ({"due_days": 10, "discount_days": 20, "discount_percent": "2"},
         "longer than"),
        ({"discount_percent": "150"}, "between 0 and 100"),
        ({"discount_percent": "-2"}, "between 0 and 100"),
    ])
    def test_refused(self, business_book, kw, text):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match=text):
            gb.create_billterm(name="Odd", **kw)

    def test_ordinary_terms_are_fine(self, business_book):
        gb = GnuCashBook(str(business_book))
        assert gb.create_billterm(
            name="2/10 Net 30", due_days=30, discount_days=10,
            discount_percent="2",
        )["status"] == "created"
        assert gb.create_billterm(name="Due on receipt", due_days=0)


class TestIV25BoundsOnBudgetsAndCommodities:
    def test_a_budget_has_a_name_and_a_sane_length(self, test_book):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="at most 1200"):
            gb.create_budget(name="Forever", year=2026, num_periods=100000)
        with pytest.raises(ValueError, match="name cannot be empty"):
            gb.create_budget(name="  ", year=2026)

    @pytest.mark.parametrize("fraction", [3, 8, 10 ** 12, 250])
    def test_a_commodity_fraction_is_a_power_of_ten(self, test_book, fraction):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="power of ten"):
            gb.create_commodity(
                mnemonic="ODD", fullname="Odd Fund", namespace="FUND",
                fraction=fraction,
            )

    def test_ordinary_fractions_are_fine(self, test_book):
        gb = GnuCashBook(str(test_book))
        for mnemonic, fraction in (("WHOLE", 1), ("FUNDX", 10000), ("COIN", 10 ** 8)):
            gb.create_commodity(
                mnemonic=mnemonic, fullname=mnemonic, namespace="FUND",
                fraction=fraction,
            )


class TestC42AScheduleThatEndsBeforeItStarts:
    def test_refused_at_creation(self, test_book):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="is before start_date"):
            _schedule(gb, end_date="2025-06-30")

    def test_refused_at_update(self, test_book):
        gb = GnuCashBook(str(test_book))
        sx = _schedule(gb)
        with pytest.raises(ValueError, match="before the schedule's start"):
            gb.update_scheduled_transaction(sx["guid"], end_date="2025-06-30")
        assert gb.update_scheduled_transaction(
            sx["guid"], end_date="2026-12-31",
        )


# ── storage shape and safety ────────────────────────────────────────

import os  # noqa: E402
import stat  # noqa: E402
import uuid  # noqa: E402

from gnucash_mcp.book._base import _is_lock_error  # noqa: E402


class TestC43TheRootIsNotAnAccountToPostTo:
    def test_a_root_guid_resolves_to_nothing(self, test_book):
        gb = GnuCashBook(str(test_book))
        root = _q(
            test_book, "select guid from accounts where account_type = 'ROOT' "
            "and name = 'Root Account'",
        )[0][0]
        with gb.open() as book:
            assert gb._resolve_account(book, root) is None
            assert gb._resolve_account(book, "%" + root[:8]) is None
        row = _txn(1, "123.00")
        row["splits"][1]["account"] = root
        got = _results(gb.create_transactions([row]))
        assert got["1"]["status"] == "rejected"
        assert _q(
            test_book, "select count(*) from splits where account_guid = ?",
            (root,),
        ) == [(0,)]
        with pytest.raises(ValueError):
            gb.delete_account_slot(root, "notes")


class TestFC13OnlyALockIsALock:
    @pytest.mark.parametrize("message", [
        "database is locked", "(sqlite3.OperationalError) database is locked",
        "Lock on the file /books/a.gnucash", "database table is locked",
        "Lock wait timeout exceeded; try restarting transaction",
        "could not obtain lock on row in relation \"gnclock\"",
    ])
    def test_real_locks(self, message):
        assert _is_lock_error(Exception(message))

    @pytest.mark.parametrize("message", [
        "no such table: gnclock",
        "unable to open database file: /home/sherlock/books/a.gnucash",
        "disk I/O error on /mnt/busybox/a.gnucash",
        "connection refused",
    ])
    def test_other_failures_are_not(self, message):
        assert not _is_lock_error(Exception(message))


class TestC23FinishingAReconcileClearsAPostponedOne:
    def test_the_postpone_frame_goes(self, test_book):
        gb = GnuCashBook(str(test_book))
        splits = gb.get_unreconciled_splits("Assets:Checking", compact=False)
        total = sum((Decimal(s["amount"]) for s in splits["splits"]), Decimal(0))
        gb.reconcile_account(
            account_name="Assets:Checking", statement_date=date(2024, 1, 31),
            statement_balance=str(total), reconcile_all=True,
        )
        frame = _q(
            test_book,
            "select s.guid_val from slots s join accounts a on a.guid = "
            "s.obj_guid where a.name = 'Checking' and s.name = 'reconcile-info'",
        )[0][0]
        # What desktop's "Postpone" leaves: a date and a balance.
        parked = uuid.uuid4().hex
        _q(
            test_book,
            "insert into slots (obj_guid, name, slot_type, guid_val) "
            "values (?, 'reconcile-info/postpone', 9, ?)", (frame, parked),
        )
        _q(
            test_book,
            "insert into slots (obj_guid, name, slot_type, int64_val) "
            "values (?, 'reconcile-info/postpone/date', 1, 1706745599)",
            (parked,),
        )
        _q(
            test_book,
            "insert into slots (obj_guid, name, slot_type, numeric_val_num, "
            "numeric_val_denom) values (?, 'reconcile-info/postpone/balance', "
            "3, 12345, 100)", (parked,),
        )

        gb.reconcile_account(
            account_name="Assets:Checking", statement_date=date(2024, 2, 29),
            statement_balance=str(total), reconcile_all=True,
        )

        assert _q(
            test_book, "select count(*) from slots where name like "
            "'reconcile-info/postpone%'",
        ) == [(0,)]
        # The rest of the frame is intact.
        assert _q(
            test_book, "select count(*) from slots where name = "
            "'reconcile-info/last-date'",
        ) == [(1,)]


class TestSS15ANewLotHasDesktopsShape:
    def test_flag_unknown_and_no_empty_notes(self, test_book):
        gb = GnuCashBook(str(test_book))
        gb.create_lot(account="Assets:Checking", title="Lot A")
        assert _q(test_book, "select is_closed from lots") == [(-1,)]
        assert _q(
            test_book,
            "select name from slots where obj_guid in (select guid from lots)",
        ) == [("title",)]

    def test_a_note_is_kept(self, test_book):
        gb = GnuCashBook(str(test_book))
        gb.create_lot(account="Assets:Checking", title="Lot B", notes="2024 buy")
        assert sorted(_q(
            test_book,
            "select name, string_val from slots where obj_guid in "
            "(select guid from lots)",
        )) == [("notes", "2024 buy"), ("title", "Lot B")]


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
class TestDS15ABackupIsReadableByItsOwnerOnly:
    def test_mode(self, test_book, tmp_path, monkeypatch):
        monkeypatch.setenv("GNUCASH_LOG_DIR", str(tmp_path / "logs"))
        gb = GnuCashBook(str(test_book))
        made = gb.create_backup(label="manual")
        path = made.get("path") or made.get("backup_path")
        files = [path] if path and os.path.exists(path) else [
            str(p) for p in (tmp_path / "logs").rglob("*.gnucash")
        ]
        assert files
        for f in files:
            assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


class TestMM15ATemplateQuantityIsRoundedNotTruncated:
    def test_the_fifth_decimal_rounds(self, test_book):
        gb = GnuCashBook(str(test_book))
        gb.create_commodity(
            mnemonic="VTSAX", fullname="Total Market", namespace="FUND",
            fraction=10000,
        )
        gb.create_account(
            name="Brokerage", account_type="MUTUAL", parent="Assets",
            commodity="VTSAX", commodity_namespace="FUND",
        )
        gb.create_scheduled_transaction(
            name="Monthly buy", description="Monthly buy",
            splits=[
                {"account": "Assets:Brokerage", "amount": "150.00",
                 "quantity": "1.23456"},
                {"account": "Assets:Checking", "amount": "-150.00"},
            ],
            start_date="2026-01-01", frequency="monthly",
        )
        assert _q(
            test_book,
            "select numeric_val_num, numeric_val_denom from slots "
            "where slot_type = 3 and name like '%quantity%'",
        ) == [(12346, 10000)]


# ── logging ─────────────────────────────────────────────────────────

import json  # noqa: E402
import logging  # noqa: E402
import shutil  # noqa: E402


class TestC33AnAuditFolderRemovedMidSessionComesBack:
    def test_the_next_entry_is_written(self, tmp_path):
        from gnucash_mcp.logging_config import _DailyFileHandler

        directory = tmp_path / "ledger.mcp" / "audit"
        directory.mkdir(parents=True)
        handler = _DailyFileHandler(directory, ".log", lambda day: f"# {day}")
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord("a", logging.INFO, "", 0, "first", None, None)
        handler.emit(record)
        shutil.rmtree(tmp_path / "ledger.mcp")

        record = logging.LogRecord("a", logging.INFO, "", 0, "second", None, None)
        handler.emit(record)

        files = list(directory.glob("*.log"))
        assert len(files) == 1
        assert "second" in files[0].read_text()


class TestC60AManualBackupLeavesAnAuditLine:
    def test_the_line_names_the_file(self, test_book, tmp_path, monkeypatch):
        from gnucash_mcp import server as server_module
        from gnucash_mcp.logging_config import setup_logging

        monkeypatch.setenv("GNUCASH_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.setenv("GNUCASH_BOOK_PATH", str(test_book))
        server_module._book = None
        pre = dict(server_module.mcp._tool_manager._tools)
        server_module._reset_lazy_load_state()
        server_module._apply_module_filter("all")
        try:
            setup_logging(
                book_path=str(test_book), audit=True,
                get_book=server_module.get_book,
            )
            tool = server_module.mcp._tool_manager._tools["create_backup"].fn
            made = json.loads(tool(label="before-cleanup"))
            name = os.path.basename(made["path"])
            files = [
                p for p in (tmp_path / "logs").rglob("*")
                if p.is_file() and "audit" in p.parts
            ]
            log = "\n".join(p.read_text() for p in files)
            assert f"CREATE BACKUP  {name}" in log, [str(f) for f in files]
        finally:
            setup_logging(book_path=None, audit=False, debug=False)
            for added in set(server_module.mcp._tool_manager._tools) - set(pre):
                del server_module.mcp._tool_manager._tools[added]
            server_module._reset_lazy_load_state()
            server_module._book = None


class TestC65TextFitsGnuCashsColumns:
    def test_a_transaction_description_memo_and_notes(self, test_book):
        gb = GnuCashBook(str(test_book))
        splits = [
            {"account": "Assets:Checking", "amount": "-1.00"},
            {"account": "Expenses:Groceries", "amount": "1.00"},
        ]
        for kw, text in (
            ({"description": "d" * 2049}, "description is 2049 characters"),
            ({"description": "ok", "notes": "n" * 4097}, "notes is 4097"),
            ({"description": "a\x00b"}, "NUL"),
        ):
            with pytest.raises(ValueError, match=text):
                gb.create_transaction(
                    splits=splits, trans_date=date(2026, 5, 21), **kw,
                )
        long_memo = [dict(splits[0], memo="m" * 2049), splits[1]]
        with pytest.raises(ValueError, match="memo for 'Assets:Checking'"):
            gb.create_transaction(
                description="ok", splits=long_memo,
                trans_date=date(2026, 5, 21),
            )
        made = gb.create_transaction(
            description="d" * 2048, splits=splits, trans_date=date(2026, 5, 21),
        )
        with pytest.raises(ValueError, match="description is 3000"):
            gb.update_transaction(made["guid"], description="x" * 3000)

    def test_a_batch_row_is_rejected_not_the_batch(self, test_book):
        gb = GnuCashBook(str(test_book))
        bad = _txn(2, "5.00")
        bad["description"] = "x" * 5000
        got = _results(gb.create_transactions(
            [_txn(1, "50.00"), bad], on_error="skip",
        ))
        assert (got["1"]["status"], got["2"]["status"]) == ("created", "rejected")

    def test_names(self, business_book):
        gb = GnuCashBook(str(business_book))
        with pytest.raises(ValueError, match="Account name is 5000"):
            gb.create_account(
                name="x" * 5000, account_type="EXPENSE", parent="Expenses",
            )
        gb.create_account(
            name="GST Payable", account_type="LIABILITY", parent="Liabilities",
        )
        with pytest.raises(ValueError, match="Taxtable name is 51 characters"):
            gb.create_taxtable(name="T" * 51, entries=[{
                "type": "percentage", "amount": "5",
                "account": "Liabilities:GST Payable",
            }])
        assert gb.create_taxtable(name="T" * 50, entries=[{
            "type": "percentage", "amount": "5",
            "account": "Liabilities:GST Payable",
        }])["status"] == "created"
        with pytest.raises(ValueError, match="Billterm name"):
            gb.create_billterm(name="N" * 2049)


class TestC66ALookupMatchesTheNameItWasGiven:
    """A book GnuCash desktop made in MariaDB compares text without
    regard to case. SQLite's NOCASE collation on the same column is
    that behavior in miniature."""

    @staticmethod
    def _case_insensitive_names(path):
        _q(path, "alter table taxtables rename to taxtables_old")
        _q(
            path,
            "create table taxtables (guid text(32) primary key not null, "
            "name text(50) not null collate nocase, refcount bigint not "
            "null, invisible integer not null, parent text(32))",
        )
        _q(path, "insert into taxtables select * from taxtables_old")
        _q(path, "drop table taxtables_old")

    def test_two_names_differing_only_in_case_stay_two_tables(
        self, business_book,
    ):
        gb = GnuCashBook(str(business_book))
        gb.create_account(
            name="GST Payable", account_type="LIABILITY", parent="Liabilities",
        )
        entry = lambda pct: [{  # noqa: E731
            "type": "percentage", "amount": pct,
            "account": "Liabilities:GST Payable",
        }]
        gb.create_taxtable(name="SALES TAX", entries=entry("8"))
        self._case_insensitive_names(business_book)
        # The database now says 'Sales Tax' = 'SALES TAX'.
        assert _q(
            business_book, "select count(*) from taxtables where name = 'Sales Tax'",
        ) == [(1,)]

        with pytest.raises(ValueError, match="not found"):
            gb.get_taxtable("Sales Tax")
        gb.create_taxtable(name="Sales Tax", entries=entry("5"))
        assert gb.get_taxtable("Sales Tax")["entries"][0]["amount"] in ("5", "5.00")
        assert gb.get_taxtable("SALES TAX")["entries"][0]["amount"] in ("8", "8.00")

        gb.delete_taxtable("Sales Tax")

        assert _q(business_book, "select name from taxtables") == [("SALES TAX",)]


import time as _time  # noqa: E402

from gnucash_mcp.book._base import _neutral_time  # noqa: E402


@pytest.mark.skipif(not hasattr(_time, "tzset"), reason="needs tzset")
class TestC25TheNeutralTimeInFarZones:
    """``gnc_time64_get_day_neutral`` is 10:59 UTC, shifted outside
    UTC-10..UTC+13 so the stamp is still the intended day locally."""

    @pytest.fixture
    def zone(self, monkeypatch):
        def set_zone(name):
            monkeypatch.setenv("TZ", name)
            _time.tzset()
        yield set_zone
        monkeypatch.undo()
        _time.tzset()

    @pytest.mark.parametrize("name, utc_hour", [
        ("UTC", 10), ("America/Los_Angeles", 10), ("Europe/Berlin", 10),
        ("Pacific/Honolulu", 10),      # UTC-10: the edge, unshifted
        ("Pacific/Auckland", 10),      # UTC+12/+13
        ("Pacific/Pago_Pago", 11),     # UTC-11
        ("Pacific/Kiritimati", 9),     # UTC+14
    ])
    def test_the_stamp(self, zone, name, utc_hour):
        zone(name)
        stamp = _neutral_time(date(2026, 9, 29))
        assert (stamp.hour, stamp.minute) == (utc_hour, 59)
        # …and it is the same calendar day where the user is.
        assert stamp.astimezone().date() == date(2026, 9, 29)


class TestFC17AutoIdsFollowTheBooksCounterFormat:
    @staticmethod
    def _set_format(path, counter, fmt):
        book = _q(path, "select guid from books")[0][0]
        frame = _q(
            path, "select guid_val from slots where obj_guid = ? "
            "and name = 'counter_formats'", (book,),
        )
        if frame:
            frame = frame[0][0]
        else:
            frame = uuid.uuid4().hex
            _q(
                path, "insert into slots (obj_guid, name, slot_type, guid_val) "
                "values (?, 'counter_formats', 9, ?)", (book, frame),
            )
        _q(
            path, "insert into slots (obj_guid, name, slot_type, string_val) "
            "values (?, ?, 4, ?)", (frame, f"counter_formats/{counter}", fmt),
        )

    def test_documents_parties_and_jobs(self, business_book):
        self._set_format(business_book, "gncInvoice", "INV-%04li")
        self._set_format(business_book, "gncCustomer", "C%.3li")
        self._set_format(business_book, "gncJob", "%li/JOB")
        gb = GnuCashBook(str(business_book))

        customer = gb.create_customer(name="Acme Corp")
        assert customer["id"] == "C001"
        first = gb.create_invoice(customer_id="C001")
        second = gb.create_invoice(customer_id="C001")
        assert (first["id"], second["id"]) == ("INV-0001", "INV-0002")
        job = gb.create_job(owner_id="C001", owner_type="customer", name="Refit")
        assert job["id"] == "1/JOB"
        # The IDs are usable handles.
        gb.add_invoice_entry(
            invoice_id="INV-0002", account="Income:Sales",
            description="Work", quantity="1", price="10.00",
        )
        assert gb.get_invoice("INV-0002", owner_type="customer")["total"] == "10.00"
        # A counter with no format of its own keeps six digits.
        assert gb.create_vendor(name="Supplier Ltd")["id"] == "000001"

    @pytest.mark.parametrize("fmt, expected", [
        ("%.6li", "000007"), ("%li", "7"), ("%05lli", "00007"),
        ("INV-%04I64i-A", "INV-0007-A"), ("100%%-%li", "100%-7"),
        ("no conversion", "000007"), ("%s", "000007"), ("%li %li", "000007"),
    ])
    def test_formats(self, business_book, fmt, expected):
        self._set_format(business_book, "gncInvoice", fmt)
        gb = GnuCashBook(str(business_book))
        with gb.open() as book:
            assert gb._counter_id(book, "counter_invoice", 7) == expected


class TestC44ADocumentWhoseTotalIsNegative:
    @pytest.fixture
    def refund_invoice(self, business_book):
        """An invoice whose lines net to -70: a return larger than
        the sale."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Sale", quantity="1", price="30.00",
        )
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Return", quantity="-1", price="100.00",
        )
        gb.post_invoice(invoice_id=inv["id"], post_account=AR,
                        post_date="2026-01-15")
        return gb, business_book, inv["id"]

    def test_it_reads_open_and_owed_to_the_customer(self, refund_invoice):
        gb, _, doc = refund_invoice
        got = gb.get_invoice(doc, owner_type="customer")
        # It read: status paid, amount_paid 0.00, overpaid true.
        assert got["status"] == "posted"
        assert (got["total"], got["amount_paid"], got["amount_due"]) == (
            "-70.00", "0.00", "-70.00",
        )
        assert "overpaid" not in got
        row = gb.get_outstanding_invoices(compact=False)["invoices"][0]
        assert row["amount_due"] == "-70.00"
        assert row["days_past_due"] is None
        assert "overpaid" not in row
        assert "Past due" not in gb.get_book_summary()

    def test_it_is_settled_by_refunding_the_customer(self, refund_invoice):
        gb, path, doc = refund_invoice
        bank = gb.get_balance("Assets:Checking", date(2026, 12, 31))
        paid = gb.pay_invoice(
            invoice_id=doc, payment_account="Assets:Checking",
            amount="70.00", payment_date="2026-01-20",
        )
        assert paid["status"] == "paid"
        assert paid["remaining_balance"] == "0.00"
        # Cash went OUT.
        assert gb.get_balance(
            "Assets:Checking", date(2026, 12, 31),
        ) - bank == Decimal("-70")
        got = gb.get_invoice(doc, owner_type="customer")
        assert (got["status"], got["amount_due"]) == ("paid", "0.00")
        assert gb.get_outstanding_invoices(compact=False)["total"] == 0

    def test_an_ordinary_overpaid_invoice_still_reads_overpaid(
        self, business_book,
    ):
        """The flag keeps its meaning for books that already hold an
        overpayment driven through the lot."""
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        inv = gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id=inv["id"], account="Income:Sales",
            description="Work", quantity="1", price="100.00",
        )
        gb.post_invoice(invoice_id=inv["id"], post_account=AR,
                        post_date="2026-01-15")
        gb.pay_invoice(
            invoice_id=inv["id"], payment_account="Assets:Checking",
            amount="100.00", payment_date="2026-01-20",
        )
        # The state pre-1.4 servers could leave: the payment split
        # larger than the invoice, in the invoice's own lot.
        _q(
            business_book,
            "update splits set value_num = value_num * 13 / 10, "
            "quantity_num = quantity_num * 13 / 10 where action = 'Payment'",
        )
        got = gb.get_invoice(inv["id"], owner_type="customer")
        assert got["overpaid"] is True
        assert (got["status"], got["amount_due"]) == ("paid", "-30.00")


class TestMM9TheLatestPriceInEitherDirection:
    def test_a_newer_quote_the_other_way_round_is_the_latest(self, test_book):
        gb = GnuCashBook(str(test_book))
        gb.create_account(
            name="Euro Cash", account_type="BANK", parent="Assets",
            commodity="EUR",
        )
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value="1.10",
            currency="USD", price_date=date(2024, 6, 1),
        )
        assert gb.get_latest_price("EUR", "CURRENCY", "USD")["value"] in (
            "1.10", "1.1",
        )
        gb.create_price(
            commodity="USD", namespace="CURRENCY", value="0.80",
            currency="EUR", price_date=date(2026, 6, 1),
        )
        got = gb.get_latest_price("EUR", "CURRENCY", "USD")
        assert got["date"] == "2026-06-01"
        assert Decimal(got["value"]) == Decimal("1.25")
        assert got["inverted_from"].startswith("USD/EUR")
        # A still newer direct quote wins again, undecorated.
        gb.create_price(
            commodity="EUR", namespace="CURRENCY", value="1.20",
            currency="USD", price_date=date(2026, 7, 1),
        )
        got = gb.get_latest_price("EUR", "CURRENCY", "USD")
        assert (got["date"], "inverted_from" in got) == ("2026-07-01", False)
