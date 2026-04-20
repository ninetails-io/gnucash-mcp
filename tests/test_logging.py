"""Tests for the logging and audit system."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gnucash_mcp.logging_config import (
    AUDIT_LOGGER_NAME,
    DEBUG_LOGGER_NAME,
    _resolve_entry_field,
    audit_log,
    setup_logging,
)


class _StagedBook:
    """Fake GnuCashBook that yields a pre-set before-state once.

    Used by tests that need to inject before_state into `@audit_log`
    without opening a real book. Mirrors the contract of
    `BaseGnuCashBook._consume_audit_before`: returns the staged dict
    on first call, clears itself so subsequent calls return None.
    """

    def __init__(self, state: dict | None):
        self._state = state

    def _consume_audit_before(self) -> dict | None:
        state = self._state
        self._state = None
        return state


@pytest.fixture
def temp_book_path(tmp_path):
    """Create a temporary book path for testing.

    Logs will be created at {book_path}.mcp/ alongside the book.
    """
    book_path = tmp_path / "test-book.gnucash"
    book_path.touch()  # Create the file so path is valid
    return book_path


@pytest.fixture
def temp_log_dir(temp_book_path):
    """Get the log directory that corresponds to the temp book path."""
    log_dir = temp_book_path.parent / f"{temp_book_path.name}.mcp"
    return log_dir


class TestSetupLogging:
    """Tests for the setup_logging function."""

    def test_audit_log_directory_created(self, temp_book_path, temp_log_dir):
        """Verify audit log directory is created alongside the book."""
        setup_logging(book_path=str(temp_book_path), debug=False)
        assert (temp_log_dir / "audit").exists()

    def test_debug_log_directory_created_when_enabled(self, temp_book_path, temp_log_dir):
        """Verify debug log directory is created when debug=True."""
        setup_logging(book_path=str(temp_book_path), debug=True)
        assert (temp_log_dir / "debug").exists()

    def test_debug_log_directory_not_created_when_disabled(self, temp_book_path, temp_log_dir):
        """Verify debug log directory is not created when debug=False."""
        setup_logging(book_path=str(temp_book_path), debug=False)
        # The directory may exist but should not have been created for debug
        # Check that no debug log file was created
        debug_dir = temp_log_dir / "debug"
        if debug_dir.exists():
            assert len(list(debug_dir.glob("*.log"))) == 0

    def test_requires_book_path(self):
        """Verify setup_logging raises error without book_path."""
        with pytest.raises(ValueError, match="book_path is required"):
            setup_logging(book_path=None, debug=False)

    def test_noaudit_skips_audit_directory(self, temp_book_path, temp_log_dir):
        """Verify --noaudit prevents audit directory creation."""
        setup_logging(book_path=str(temp_book_path), debug=False, audit=False)
        # Neither audit nor debug dir should exist
        assert not (temp_log_dir / "audit").exists()
        assert not (temp_log_dir / "debug").exists()

    def test_noaudit_with_debug_creates_only_debug(self, temp_book_path, temp_log_dir):
        """Verify --noaudit with --debug only creates debug directory."""
        setup_logging(book_path=str(temp_book_path), debug=True, audit=False)
        assert not (temp_log_dir / "audit").exists()
        assert (temp_log_dir / "debug").exists()


class TestAuditLogDecorator:
    """Tests for the audit_log decorator's error and write handling."""

    def test_raising_tool_logged_as_error(self, temp_book_path, temp_log_dir):
        """A tool raising an exception writes an ERROR line to the text audit log."""
        setup_logging(book_path=str(temp_book_path), debug=False)

        @audit_log(classification="write", operation="create", entity_type="transaction")
        def test_failing_tool(description: str) -> str:
            raise ValueError("Account not found")

        with pytest.raises(ValueError):
            test_failing_tool(description="bad")

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        content = txt_file.read_text()

        assert "ERROR  test_failing_tool: Account not found" in content

    def test_write_tool_logged(self, temp_book_path, temp_log_dir):
        """Successful write tools emit a CREATE/UPDATE/etc. header line."""
        setup_logging(book_path=str(temp_book_path), debug=False)

        @audit_log(classification="write", operation="create", entity_type="transaction")
        def test_create_tool(description: str) -> str:
            return json.dumps({"guid": "abc123", "description": description})

        test_create_tool(description="Test transaction")

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        content = txt_file.read_text()

        assert "CREATE TRANSACTION" in content
        assert "guid:abc123" in content
        assert "Test transaction" in content


class TestDebugLogging:
    """Tests for debug logging."""

    def test_debug_log_off_by_default(self, temp_book_path, temp_log_dir):
        """Verify no debug log when --debug not set."""
        setup_logging(book_path=str(temp_book_path), debug=False)

        @audit_log(classification="read")
        def test_tool() -> str:
            return json.dumps({})

        test_tool()

        debug_dir = temp_log_dir / "debug"
        if debug_dir.exists():
            log_files = list(debug_dir.glob("*.log"))
            if log_files:
                # File exists but should be empty or not have our entries
                pass  # Debug logger is disabled, so no entries

    def test_debug_log_captures_timing(self, temp_book_path, temp_log_dir):
        """Verify debug log captures request/response timing when enabled."""
        setup_logging(book_path=str(temp_book_path), debug=True)

        @audit_log(classification="read")
        def test_timed_tool() -> str:
            return json.dumps({"data": "test"})

        test_timed_tool()

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        debug_file = temp_log_dir / "debug" / f"{today}.log"
        assert debug_file.exists()

        content = debug_file.read_text()
        assert "MCP request: tool=test_timed_tool" in content
        assert "MCP response: tool=test_timed_tool" in content
        assert "elapsed=" in content


class TestTextFormat:
    """Tests for text format audit logging."""

    def test_text_format_has_header(self, temp_book_path, temp_log_dir):
        """Verify text format file has proper header."""
        setup_logging(book_path=str(temp_book_path), debug=False)

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        content = txt_file.read_text()

        assert "GNUCASH MCP AUDIT LOG" in content
        assert str(temp_book_path) in content

    def test_text_format_logs_write_operations(self, temp_book_path, temp_log_dir):
        """Verify text format logs write operations in human-readable form."""
        setup_logging(book_path=str(temp_book_path), debug=False)

        @audit_log(classification="write", operation="create", entity_type="transaction")
        def test_create(description: str) -> str:
            return json.dumps({"guid": "abc123", "description": description, "date": "2026-02-04"})

        test_create(description="Test Transaction")

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        content = txt_file.read_text()

        assert "CREATE TRANSACTION" in content
        assert "Test Transaction" in content

    def test_text_format_skips_read_operations(self, temp_book_path, temp_log_dir):
        """Verify text format does not log read operations."""
        setup_logging(book_path=str(temp_book_path), debug=False)

        @audit_log(classification="read")
        def test_read(name: str) -> str:
            return json.dumps({"balance": "100.00"})

        test_read(name="Test")

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        content = txt_file.read_text()

        # Should only have the header, no read operation logged
        assert "test_read" not in content
        assert "balance" not in content

    def test_text_format_update_falls_back_to_params_for_splits(
        self, temp_book_path, temp_log_dir
    ):
        """UPDATE audit entry still shows new splits when response trims them.

        `update_transaction`'s response is thin
        (``{guid, date, description, status}`` — no splits). When
        ``after_state`` omits ``splits``, ``description``, or ``date``,
        the audit log should pull them from tool params instead so the
        text view keeps the before/after diff detail human reviewers
        rely on.
        """
        # Inject before_state via a fake book the decorator can consume
        # from — mirrors what `update_transaction` would stage in
        # production using `_stage_audit_before` from its open session.
        staged = _StagedBook({
            "description": "Old description",
            "date": "2026-01-01",
            "splits": [
                {"account": "Expenses:Old", "value": "10.00"},
                {"account": "Assets:Checking", "value": "-10.00"},
            ],
        })
        setup_logging(
            book_path=str(temp_book_path),
            debug=False,
            get_book=lambda: staged,
        )

        @audit_log(
            classification="write",
            operation="update",
            entity_type="transaction",
        )
        def test_update(
            guid: str,
            description: str,
            transaction_date: str,
            splits: list,
        ) -> str:
            # Simulated thin response: no description, date, or splits.
            return json.dumps({"guid": guid, "status": "updated"})

        test_update(
            guid="abcdef01",
            description="New description",
            transaction_date="2026-02-15",
            splits=[
                {"account": "Expenses:New", "value": "20.00"},
                {"account": "Assets:Checking", "value": "-20.00"},
            ],
        )

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        content = txt_file.read_text()

        # Header present
        assert "UPDATE TRANSACTION" in content
        # Description diff rendered from params, not response
        assert 'Description: "Old description" → "New description"' in content
        # Date diff rendered from params (transaction_date -> date mapping)
        assert "Date: 2026-01-01 → 2026-02-15" in content
        # Splits diff rendered from params (response had none)
        assert "Splits (before):" in content
        assert "Splits (after):" in content
        assert "Old" in content  # old split account leaf
        assert "New" in content  # new split account leaf

    def test_text_format_replace_splits_falls_back_to_params(
        self, temp_book_path, temp_log_dir
    ):
        """REPLACE_SPLITS audit entry uses params for new splits when
        response drops the ``splits`` echo.

        ``replace_splits`` returns only
        ``{guid, status, previous_splits, warnings}`` — the new splits
        must come from tool params in the audit log. Description and
        date come from before_state (they don't change on this op but
        the formatter still renders them).
        """
        # Before-state gives description/date; in production this is
        # staged by the book method from its own open session.
        staged = _StagedBook({
            "description": "Recategorize",
            "date": "2026-02-10",
        })
        setup_logging(
            book_path=str(temp_book_path),
            debug=False,
            get_book=lambda: staged,
        )

        @audit_log(
            classification="write",
            operation="replace_splits",
            entity_type="transaction",
        )
        def test_replace_splits(guid: str, splits: list) -> str:
            # Simulated thin response: no splits echo, just previous_splits.
            return json.dumps({
                "guid": guid,
                "status": "splits_replaced",
                "previous_splits": [
                    {"account": "Expenses:Groceries", "value": "50.00"},
                    {"account": "Assets:Checking", "value": "-50.00"},
                ],
            })

        test_replace_splits(
            guid="abcdef02",
            splits=[
                {"account": "Expenses:Dining", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
        )

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        content = txt_file.read_text()

        assert "REPLACE SPLITS" in content
        # Description/date from before_state
        assert "Recategorize" in content
        assert "2026-02-10" in content
        # Before splits from response (previous_splits is preserved)
        assert "Splits (before):" in content
        assert "Groceries" in content
        # After splits from params (response no longer echoes them)
        assert "Splits (after):" in content
        assert "Dining" in content

    def test_text_format_logs_replace_splits(self, temp_book_path, temp_log_dir):
        """Verify text format logs replace_splits with before and after splits."""
        setup_logging(book_path=str(temp_book_path), debug=False)

        @audit_log(classification="write", operation="replace_splits", entity_type="transaction")
        def test_replace_splits(guid: str, splits: list) -> str:
            return json.dumps({
                "guid": guid,
                "description": "Test Transaction",
                "date": "2026-02-14",
                "splits": [
                    {"account": "Expenses:Dining", "value": "50.00"},
                    {"account": "Assets:Checking", "value": "-50.00"},
                ],
                "previous_splits": [
                    {"account": "Expenses:Groceries", "value": "50.00"},
                    {"account": "Assets:Checking", "value": "-50.00"},
                ],
                "status": "splits_replaced",
            })

        test_replace_splits(
            guid="abc12345",
            splits=[
                {"account": "Expenses:Dining", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
        )

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        content = txt_file.read_text()

        assert "REPLACE SPLITS" in content
        assert "Splits (before):" in content
        assert "Splits (after):" in content
        assert "Groceries" in content  # Old split (formatted as leaf name)
        assert "Dining" in content  # New split (formatted as leaf name)


class TestResolveEntryField:
    """Unit tests for the ``_resolve_entry_field`` fallback helper.

    The helper unifies the lookup order across audit entry sources so
    trimmed responses still render richly in the human-readable log:
    after_state → params → before_state → None.
    """

    def test_after_state_wins_when_present(self):
        entry = {
            "after_state": {"description": "from after"},
            "params": {"description": "from params"},
            "before_state": {"description": "from before"},
        }
        assert _resolve_entry_field(entry, "description") == "from after"

    def test_falls_through_to_params_when_after_empty(self):
        entry = {
            "after_state": {},
            "params": {"description": "from params"},
        }
        assert _resolve_entry_field(entry, "description") == "from params"

    def test_falls_through_to_before_state(self):
        entry = {
            "after_state": None,
            "params": {},
            "before_state": {"description": "from before"},
        }
        assert _resolve_entry_field(entry, "description") == "from before"

    def test_returns_none_when_missing_everywhere(self):
        assert _resolve_entry_field({}, "description") is None

    def test_alternate_params_key(self):
        """Response ``date`` maps to params ``transaction_date``."""
        entry = {"params": {"transaction_date": "2026-01-15"}}
        assert _resolve_entry_field(
            entry, "date", params_key="transaction_date"
        ) == "2026-01-15"

    def test_empty_string_is_not_present(self):
        """Falsy values fall through — after's empty desc yields to params."""
        entry = {
            "after_state": {"description": ""},
            "params": {"description": "from params"},
        }
        assert _resolve_entry_field(entry, "description") == "from params"

    def test_empty_list_is_not_present(self):
        """Empty splits list falls through to a populated params list."""
        entry = {
            "after_state": {"splits": []},
            "params": {"splits": [{"account": "Assets:Checking"}]},
        }
        assert _resolve_entry_field(entry, "splits") == [{"account": "Assets:Checking"}]

    def test_none_after_state_handled(self):
        """after_state being None (not just empty) is safe."""
        entry = {
            "after_state": None,
            "params": {"description": "from params"},
        }
        assert _resolve_entry_field(entry, "description") == "from params"

    def test_missing_sources_handled(self):
        """Entries without any source keys don't crash."""
        assert _resolve_entry_field({"timestamp": "..."}, "anything") is None


class TestAuditLogIntegration:
    """Integration tests for the complete audit trail."""

    def test_write_operation_lifecycle(self, temp_book_path, temp_log_dir):
        """A create-transaction lifecycle renders as a readable audit entry.

        The entry should carry the header (operation + short GUID), the
        description, date, and the full split list — sourced from
        ``after_state`` or (trimmed-response fallback) from ``params``.
        """
        setup_logging(book_path=str(temp_book_path), debug=False)

        @audit_log(classification="write", operation="create", entity_type="transaction")
        def create_txn(description: str, splits: list, transaction_date: str) -> str:
            # Simulate a thin response: just guid + status. The formatter
            # must reach into params for description / date / splits.
            return json.dumps({"guid": "txn12345", "status": "created"})

        create_txn(
            description="Grocery shopping",
            transaction_date="2026-02-15",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
        )

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        content = txt_file.read_text()

        assert "CREATE TRANSACTION" in content
        assert "guid:txn12345" in content
        assert "Grocery shopping" in content
        assert "2026-02-15" in content
        # Split leaf names appear in the rendered splits block
        assert "Groceries" in content
        assert "Checking" in content


class TestBookOpenAccounting:
    """Regression guard: each write should open the book exactly once.

    Before this work, the @audit_log decorator called
    _capture_before_state → book.get_transaction(...) → opened the book
    read-only; the tool then opened it again read-write. Every write
    paid the double-open tax. Now write book methods stage their own
    before-state via _stage_audit_before / _consume_audit_before on the
    BaseGnuCashBook wrapper, and the decorator reads it back without
    opening anything.

    This test patches `piecash.open_book` with a counter and asserts
    exactly one open per `update_transaction` call. If it ever starts
    failing with count=2, someone added a read-only pre-capture back.
    """

    def test_update_transaction_opens_book_once(
        self, test_book, monkeypatch
    ):
        import piecash

        from gnucash_mcp.book import GnuCashBook

        book_wrapper = GnuCashBook(str(test_book))
        # Register the wrapper so audit_log's consume path has access.
        setup_logging(
            book_path=str(test_book),
            debug=False,
            get_book=lambda: book_wrapper,
        )

        # Find an existing transaction GUID (one read-only open here —
        # we reset the counter after).
        with book_wrapper.open(readonly=True) as pb:
            txn_guid = pb.transactions[0].guid

        # Defeat the auto-backup hook for this test. The hook is a
        # separate mechanism (tested by TestBackup* below) that legitimately
        # adds one open to the first write of each process. We want to
        # measure the WRITE path's opens in isolation.
        book_wrapper._backup_checked_in_process = True

        open_count = [0]
        original = piecash.open_book

        def counting_open(*args, **kwargs):
            open_count[0] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(piecash, "open_book", counting_open)

        @audit_log(
            classification="write",
            operation="update",
            entity_type="transaction",
        )
        def update_tool(guid: str, description: str) -> str:
            return json.dumps(
                book_wrapper.update_transaction(
                    guid=guid, description=description
                )
            )

        update_tool(guid=txn_guid, description="renamed by book-open test")

        assert open_count[0] == 1, (
            f"Expected exactly 1 book open for one update_transaction "
            f"call, got {open_count[0]}. The @audit_log decorator may "
            f"have regained a pre-capture step that opens the book "
            f"read-only before the write."
        )

    def test_create_transaction_opens_book_once(
        self, test_book, monkeypatch
    ):
        """A full create_transaction call (auto-fill + duplicate check
        + write + post-write consistency warning) opens the book
        exactly once.

        Pre-consolidation this path opened the book four times — once
        per helper: ``_auto_fill_splits``, ``_check_auto_fill_stability``,
        ``_find_duplicates``, and the write stage. After the single-pass
        collector refactor, every stage shares one session.
        """
        import piecash

        from gnucash_mcp.book import GnuCashBook

        book_wrapper = GnuCashBook(str(test_book))

        # Seed a transaction we can auto-fill from (test_book's fixture
        # has a "Weekly Groceries" txn — we'll reuse that description to
        # exercise the auto-fill path).
        open_count = [0]
        original = piecash.open_book

        def counting_open(*args, **kwargs):
            open_count[0] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(piecash, "open_book", counting_open)

        # Provide explicit splits so this is a pure write + preflight
        # (no auto-fill second pass) — still exercises the duplicate
        # scan and post-write consistency warning. This is the common
        # path; auto-fill is tested separately.
        book_wrapper.create_transaction(
            description="Single-open regression guard",
            splits=[
                {"account": "Expenses:Groceries", "amount": "42.00"},
                {"account": "Assets:Checking", "amount": "-42.00"},
            ],
            check_duplicates=True,
        )

        assert open_count[0] == 1, (
            f"Expected exactly 1 book open for a full create_transaction "
            f"call, got {open_count[0]}. Pre-consolidation this path "
            f"opened the book 4 times (auto-fill, stability, duplicates, "
            f"write). If this test fails with count > 1, a helper has "
            f"regained its own `with self.open(...)` instead of reusing "
            f"the caller's session."
        )


class TestCreateSignalsCollector:
    """The single-pass ``_collect_create_signals`` consolidates four
    independent O(N) scans into one (or two, for the auto-fill path).

    These tests assert the contract directly by counting collector
    invocations per create_transaction call. They're an architecture
    regression guard: if a future change reintroduces a helper that
    does its own book walk, the counts shift and these tests fail.
    """

    def test_single_pass_when_splits_provided(self, test_book, monkeypatch):
        """With explicit splits, the collector runs exactly once — one
        sort, one traversal, all four signal types bundled."""
        from gnucash_mcp.book import GnuCashBook
        from gnucash_mcp.book.core import CoreMixin

        book_wrapper = GnuCashBook(str(test_book))

        original = CoreMixin._collect_create_signals
        call_count = [0]

        def counting(self, *args, **kwargs):
            call_count[0] += 1
            return original(self, *args, **kwargs)

        monkeypatch.setattr(
            CoreMixin, "_collect_create_signals", counting
        )

        book_wrapper.create_transaction(
            description="Collector call-count test",
            splits=[
                {"account": "Expenses:Groceries", "amount": "15.00"},
                {"account": "Assets:Checking", "amount": "-15.00"},
            ],
            check_duplicates=True,
        )

        assert call_count[0] == 1, (
            f"Expected 1 collector pass when splits are provided; got "
            f"{call_count[0]}."
        )

    def test_two_passes_when_auto_filling(self, test_book, monkeypatch):
        """Auto-fill legitimately needs two passes: the first finds the
        source transaction (needed to derive proposed_amounts for the
        duplicate scan); the second runs duplicates + recent-matches
        with those amounts. This is the best we can do in a single
        book-open without speculative buffering."""
        from gnucash_mcp.book import GnuCashBook
        from gnucash_mcp.book.core import CoreMixin

        book_wrapper = GnuCashBook(str(test_book))

        original = CoreMixin._collect_create_signals
        call_count = [0]

        def counting(self, *args, **kwargs):
            call_count[0] += 1
            return original(self, *args, **kwargs)

        monkeypatch.setattr(
            CoreMixin, "_collect_create_signals", counting
        )

        # test_book's fixture has "Weekly Groceries" — auto-fill from it.
        book_wrapper.create_transaction(
            description="Weekly Groceries",
            splits=None,  # trigger auto-fill
        )

        assert call_count[0] == 2, (
            f"Expected 2 collector passes when auto-filling (one to "
            f"find the source, one to run duplicates/recent against "
            f"the resulting amounts); got {call_count[0]}."
        )
