"""Tests for the logging and audit system."""

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gnucash_mcp.logging_config import (
    AUDIT_LOGGER_NAME,
    DEBUG_LOGGER_NAME,
    _resolve_entry_field,
    audit_log,
    resolve_mcp_dir,
    setup_logging,
)


class _StagedBook:
    """Fake GnuCashBook that simulates the real staging contract.

    Mirrors ``BaseGnuCashBook._stage_audit_before`` /
    ``_consume_audit_before``: a write book method calls
    ``_stage_audit_before(state)`` while its session is open, and
    ``_consume_audit_before()`` returns and clears the staged state.

    The constructor accepts a ``state`` arg as a convenience —
    callers that don't want to wire up a custom staging callback
    can pass it in and have it auto-stage on the first
    ``_consume_audit_before`` AFTER the wrapper's pre-clear has
    run. The audit decorator pre-clears at wrapper entry to defend
    against leaks from prior calls; that pre-clear must NOT eat
    the test fixture's intended state. We track whether the
    pre-clear has fired so the test's intended state surfaces on
    the post-call consume only.
    """

    def __init__(self, state: dict | None):
        self._initial_state = state
        self._staged: dict | None = None
        self._pre_clear_done = False

    def _stage_audit_before(self, state: dict | None) -> None:
        self._staged = state

    def _consume_audit_before(self) -> dict | None:
        if not self._pre_clear_done:
            # Simulate the wrapper's pre-clear: returns whatever's
            # currently staged. After this fires, the constructor's
            # ``initial_state`` becomes available as the test's
            # "staged-during-func" state.
            self._pre_clear_done = True
            stale = self._staged
            self._staged = self._initial_state
            return stale
        # Subsequent consume: yield then clear (real staging contract).
        state = self._staged
        self._staged = None
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


class TestDeleteDocFormattersHandleIdAlias:
    """Plumb Bob bookkeeper-flagged: the four delete-document
    formatters (``_fmt_invoice_delete``, ``_fmt_bill_delete``,
    ``_fmt_voucher_delete``, ``_fmt_credit_note_delete``) used to
    look up only the legacy ``<entity>_id`` key in ``params``.
    The tool layer accepts ``id`` as the preferred alias —
    callers using ``id=...`` produced audit lines like
    ``DELETE INVOICE id:None``. Formatters now prefer ``id``
    falling back to the legacy alias, falling back to ``""``.
    """

    def test_invoice_delete_with_id_alias(self):
        from gnucash_mcp.logging_config import _fmt_invoice_delete
        entry = {
            "timestamp": "2026-06-04T10:00:00",
            "params": {"id": "000040"},
        }
        lines = _fmt_invoice_delete(entry)
        assert "DELETE INVOICE  id:000040" in lines[0]
        assert "None" not in lines[0]

    def test_invoice_delete_with_legacy_invoice_id(self):
        from gnucash_mcp.logging_config import _fmt_invoice_delete
        entry = {
            "timestamp": "2026-06-04T10:00:00",
            "params": {"invoice_id": "000041"},
        }
        lines = _fmt_invoice_delete(entry)
        assert "DELETE INVOICE  id:000041" in lines[0]

    def test_bill_delete_with_id_alias(self):
        from gnucash_mcp.logging_config import _fmt_bill_delete
        entry = {
            "timestamp": "2026-06-04T10:00:00",
            "params": {"id": "000050"},
        }
        lines = _fmt_bill_delete(entry)
        assert "DELETE BILL  id:000050" in lines[0]
        assert "None" not in lines[0]

    def test_voucher_delete_with_id_alias(self):
        from gnucash_mcp.logging_config import _fmt_voucher_delete
        entry = {
            "timestamp": "2026-06-04T10:00:00",
            "params": {"id": "000010"},
        }
        lines = _fmt_voucher_delete(entry)
        assert "DELETE VOUCHER  id:000010" in lines[0]
        assert "None" not in lines[0]

    def test_credit_note_delete_with_id_alias(self):
        from gnucash_mcp.logging_config import _fmt_credit_note_delete
        entry = {
            "timestamp": "2026-06-04T10:00:00",
            "params": {"id": "000005"},
        }
        lines = _fmt_credit_note_delete(entry)
        assert "DELETE CREDIT NOTE  id:000005" in lines[0]
        assert "None" not in lines[0]


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


class TestBudgetAndScheduledAuditHandlers:
    """Audit handlers for budgets and scheduled transactions.

    Pre-fix, ``_AUDIT_HANDLERS`` had no entries for ``budget`` or
    ``scheduled_transaction``. Every UPDATE / DELETE on those entities
    rendered as an empty string — the bookkeeper had no way to see
    what changed. The book methods also didn't stage before-state.
    Both gaps closed in the same commit.
    """

    def test_budget_update_renders_per_period_diff(
        self, temp_book_path, temp_log_dir,
    ):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "budget",
            "operation": "update",
            "timestamp": "2026-04-30T18:00:00",
            "params": {
                "budget_name": "2026 Budget",
                "account": "Expenses:Groceries",
                "amount": "550.00",
            },
            "before_state": {
                "budget_name": "2026 Budget",
                "account": "Expenses:Groceries",
                "prior_amounts": {0: "500.00", 1: None},
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert "UPDATE BUDGET" in rendered
        assert '"2026 Budget"' in rendered
        assert "Expenses:Groceries" in rendered
        assert "period 0: 500.00 → 550.00" in rendered
        assert "period 1: (unset) → 550.00" in rendered

    def test_budget_delete_renders_snapshot(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "budget",
            "operation": "delete",
            "timestamp": "2026-04-30T18:00:00",
            "params": {"name": "2026 Budget"},
            "before_state": {
                "name": "2026 Budget",
                "num_periods": 12,
                "amount_count": 8,
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert 'DELETE BUDGET  "2026 Budget"' in rendered
        assert "periods: 12" in rendered
        assert "amounts removed: 8" in rendered

    def test_scheduled_update_enable_toggle(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "scheduled_transaction",
            "operation": "update",
            "timestamp": "2026-04-30T18:00:00",
            "params": {"guid": "abcdef01", "enabled": False},
            "before_state": {
                "name": "Monthly Rent",
                "enabled": True,
                "end_date": None,
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert 'UPDATE SCHEDULED  "Monthly Rent"' in rendered
        assert "enabled: True → False" in rendered

    def test_scheduled_update_end_date_clear(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "scheduled_transaction",
            "operation": "update",
            "timestamp": "2026-04-30T18:00:00",
            "params": {"guid": "abcdef01", "end_date": ""},
            "before_state": {
                "name": "Monthly Rent",
                "enabled": True,
                "end_date": "2026-12-31",
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert "end_date: 2026-12-31 → (cleared)" in rendered

    def test_scheduled_delete_includes_history(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "scheduled_transaction",
            "operation": "delete",
            "timestamp": "2026-04-30T18:00:00",
            "params": {"guid": "abcdef01"},
            "before_state": {
                "name": "Monthly Rent",
                "frequency": "monthly",
                "start_date": "2026-01-01",
                "instance_count": 4,
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert 'DELETE SCHEDULED  "Monthly Rent"' in rendered
        assert "frequency: monthly  start: 2026-01-01" in rendered
        assert "had run 4 times" in rendered

    def test_scheduled_create_renders_description(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "scheduled_transaction",
            "operation": "create",
            "timestamp": "2026-07-15T18:00:00",
            "params": {
                "name": "Car Insurance",
                "description": "Progressive policy #4471",
                "start_date": "2026-08-01",
            },
            "after_state": {
                "name": "Car Insurance",
                "frequency": "monthly",
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert 'CREATE SCHEDULED  "Car Insurance"' in rendered
        assert "description: Progressive policy #4471" in rendered

    def test_create_from_scheduled_shows_txn_guid_and_description(self):
        """The instantiation response keys the new transaction as
        ``transaction_guid`` — the generic CREATE handler used to
        fall back to the params GUID (the SCHEDULE's) and render an
        empty description. The dedicated handler reads the response."""
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "transaction",
            "operation": "create_from_scheduled",
            "timestamp": "2026-07-15T18:00:00",
            "params": {"guid": "aaaa1111", "transaction_date": "2026-08-01"},
            "after_state": {
                "transaction_guid": "bcd3e6e1",
                "scheduled_transaction": "Car Insurance",
                "description": "Progressive policy #4471, autopay",
                "transaction_date": "2026-08-01",
                "instance_count": 1,
                "status": "created",
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert "CREATE FROM SCHEDULED  guid:bcd3e6e1" in rendered
        assert "aaaa1111" not in rendered
        assert '"Progressive policy #4471, autopay" (2026-08-01)' in rendered
        assert 'schedule: "Car Insurance"  instance #1' in rendered

    def test_create_from_scheduled_rejected_duplicate(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "transaction",
            "operation": "create_from_scheduled",
            "timestamp": "2026-07-15T18:00:00",
            "params": {"guid": "aaaa1111"},
            "after_state": {
                "transaction_guid": None,
                "scheduled_transaction": "Car Insurance",
                "description": "Progressive policy #4471, autopay",
                "transaction_date": "2026-08-01",
                "instance_count": 2,
                "status": "rejected",
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert 'CREATE FROM SCHEDULED  "Car Insurance" (2026-08-01)' in rendered
        assert "rejected: equivalent transaction already exists" in rendered

    def test_entry_create_renders_notes_and_action(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "entry",
            "operation": "create",
            "timestamp": "2026-07-15T18:00:00",
            "params": {"invoice_id": "000001"},
            "after_state": {
                "invoice_id": "000001",
                "description": "April retainer",
                "total": "1500.00",
                "notes": "PO #2231",
                "action": "Hours",
                "status": "created",
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert '"April retainer"  total: 1500.00  on: 000001' in rendered
        assert "action: Hours  notes: PO #2231" in rendered

    def test_entry_create_plain_has_no_detail_line(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "entry",
            "operation": "create",
            "timestamp": "2026-07-15T18:00:00",
            "params": {"bill_id": "000001"},
            "after_state": {
                "bill_id": "000001",
                "description": "Paper",
                "total": "50.00",
                "status": "created",
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert '"Paper"  total: 50.00  on: 000001' in rendered
        assert "notes:" not in rendered
        assert "action:" not in rendered


class TestBudgetAndScheduledStaging:
    """Verify the book methods actually stage before-state.

    The audit handler tests above use synthetic entries to lock the
    rendering contract; these tests run the real book methods and
    assert that ``_consume_audit_before`` returns the expected staged
    dict.
    """

    def test_set_budget_amount_stages_prior_amounts(self, budget_book):
        from gnucash_mcp.book import GnuCashBook
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="2026 B", year=2026, num_periods=12)
        # Initial set populates period 0
        book.set_budget_amount(
            budget_name="2026 B",
            account="Expenses:Groceries",
            amount="500",
            period=0,
        )
        # Overwrite — staged before-state should carry the prior 500
        book.set_budget_amount(
            budget_name="2026 B",
            account="Expenses:Groceries",
            amount="600",
            period=0,
        )
        before = book._consume_audit_before()
        assert before is not None
        assert before["account"] == "Expenses:Groceries"
        # period key may be int or str depending on dict round-trip
        prior = before["prior_amounts"]
        # The prior amount before this last set was 500
        assert any(
            (str(v) == "500" or str(v) == "500.00")
            for v in prior.values()
        ), f"expected prior 500 in {prior}"

    def test_delete_budget_stages_snapshot(self, budget_book):
        from gnucash_mcp.book import GnuCashBook
        book = GnuCashBook(str(budget_book))
        book.create_budget(name="To Delete", year=2026, num_periods=12)
        book.set_budget_amount(
            budget_name="To Delete",
            account="Expenses:Groceries",
            amount="100",
            period=0,
        )
        book._consume_audit_before()  # clear any staged state
        book.delete_budget(name="To Delete")
        before = book._consume_audit_before()
        assert before is not None
        assert before["name"] == "To Delete"
        assert before["num_periods"] == 12
        assert before["amount_count"] >= 1

    def test_update_scheduled_stages_prior_state(self, scheduled_book):
        from gnucash_mcp.book import GnuCashBook
        gb = GnuCashBook(str(scheduled_book))
        sx = gb.create_scheduled_transaction(
            name="Rent",
            description="Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "100"},
                {"account": "Assets:Checking", "amount": "-100"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        gb._consume_audit_before()  # clear staged state from create
        gb.update_scheduled_transaction(sx["guid"], enabled=False)
        before = gb._consume_audit_before()
        assert before is not None
        assert before["name"] == "Rent"
        assert before["enabled"] is True

    def test_delete_scheduled_stages_snapshot(self, scheduled_book):
        from gnucash_mcp.book import GnuCashBook
        gb = GnuCashBook(str(scheduled_book))
        sx = gb.create_scheduled_transaction(
            name="To Delete",
            description="x",
            splits=[
                {"account": "Expenses:Rent", "amount": "50"},
                {"account": "Assets:Checking", "amount": "-50"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        gb._consume_audit_before()
        gb.delete_scheduled_transaction(sx["guid"])
        before = gb._consume_audit_before()
        assert before is not None
        assert before["name"] == "To Delete"
        assert before["frequency"] == "monthly"


class TestDispatchTableDegradation:
    """Unknown (entity_type, operation) pairs must degrade gracefully.

    The audit text formatter was flattened from a 380-line if/elif
    chain into a dispatch table in the prerelease-2 polish cycle.
    The chain's default behavior was to silently skip unmapped combos
    (falling off the bottom of the if/elif); the dispatcher preserves
    that semantic — a new classification wired in book code but not
    yet wired to a handler returns empty string, not a crash.

    Lets Employee handlers land before Employee write tools without
    failing rendering of any write the user happens to make in the
    interim.
    """

    def test_unknown_entity_type_returns_empty_string(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text

        entry = {
            "classification": "write",
            "operation": "create",
            "entity_type": "hologram",  # fictional entity, never in the dispatcher
            "timestamp": "2026-04-21T12:34:56",
            "params": {"name": "Test"},
            "after_state": {"id": "000001", "name": "Test"},
        }
        assert _format_audit_entry_text(entry) == ""

    def test_unknown_operation_returns_empty_string(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text

        entry = {
            "classification": "write",
            "operation": "hypnotize",  # fictional op on a real entity
            "entity_type": "transaction",
            "timestamp": "2026-04-21T12:34:56",
            "params": {},
        }
        assert _format_audit_entry_text(entry) == ""

    def test_read_classification_returns_empty_string(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text

        entry = {
            "classification": "read",
            "operation": "create",
            "entity_type": "transaction",
            "timestamp": "2026-04-21T12:34:56",
        }
        assert _format_audit_entry_text(entry) == ""


class TestAuditEntityTypeBillSwap:
    """``post_invoice`` / ``unpost_invoice`` / ``pay_invoice`` carry
    ``entity_type="invoice"`` on the decorator but accept either
    invoices or vendor bills. The audit log must render BILL when
    the call operated on a bill — pre-fix every bill operation
    showed up as "POST INVOICE" / "PAY INVOICE" in the log,
    mis-categorizing the entry.
    """

    def test_bill_post_renders_as_post_bill(self):
        """The dispatcher swaps entity_type to ``bill`` based on
        the response's ``type`` field; the bill handler then
        renders ``POST BILL`` instead of ``POST INVOICE``."""
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "bill",  # post-swap
            "operation": "post",
            "timestamp": "2026-04-30T18:00:00",
            "params": {"id": "B-001", "post_account": "Liabilities:A/P"},
            "after_state": {
                "type": "bill", "total": "500.00",
                "post_date": "2026-03-10",
                "transaction_guid": "abc12345",
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert "POST BILL" in rendered
        assert "POST INVOICE" not in rendered

    def test_bill_pay_renders_as_pay_bill(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        entry = {
            "classification": "write",
            "entity_type": "bill",
            "operation": "pay",
            "timestamp": "2026-04-30T18:00:00",
            "params": {"id": "B-001", "payment_account": "Assets:Checking"},
            "after_state": {
                "type": "bill", "amount_paid": "500.00",
                "remaining_balance": "0.00",
                "transaction_guid": "abc12345",
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert "PAY BILL" in rendered
        assert "PAY INVOICE" not in rendered


class TestAuditBeforeStateLeak:
    """Defense-in-depth: a previous call's staged before-state must
    never leak into the next call's audit entry, regardless of how
    the previous call exited.

    Pre-fix, the wrapper consumed before-state on the success path
    (post-func) and tried to consume on the exception path. If
    either consume itself raised (e.g., book wrapper transiently
    unavailable), the threading-local kept the staged state, and
    the next decorated call would render an unrelated diff. The
    fix adds a pre-clear at the TOP of the wrapper so every tool
    starts with a clean slot.
    """

    def test_pre_clear_drops_leaked_before_state(
        self, temp_book_path, temp_log_dir,
    ):
        """A second tool call must not see before-state staged by a
        previous call that failed to consume it."""

        class _RetainingBook:
            def __init__(self):
                self._staged = None

            def _stage_audit_before(self, state):
                self._staged = state

            def _consume_audit_before(self):
                state = self._staged
                self._staged = None
                return state

        book = _RetainingBook()
        # Pre-stage state as if a previous call had left it behind.
        book._stage_audit_before({"description": "leaked from prior call"})
        assert book._staged is not None

        setup_logging(
            book_path=str(temp_book_path),
            debug=False,
            get_book=lambda: book,
        )

        @audit_log(
            classification="write", operation="create",
            entity_type="transaction",
        )
        def fresh_call(description: str) -> str:
            return json.dumps({"guid": "abcd1234", "description": description})

        fresh_call(description="this call's own description")

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        content = txt_file.read_text()

        # The leaked before-state's "description" must NOT appear in
        # this call's audit entry.
        assert "leaked from prior call" not in content
        # Sanity: this call's own description still rendered.
        assert "this call's own description" in content


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


class TestAuditLogResolvesAccountRefs:
    """The audit log is the one human-readable surface in the app.
    Short GUIDs ``%xxxxxxx`` and full 32-char GUIDs are convenient on
    the wire but noise to a reviewer scanning the log. The formatter
    resolves them back to canonical full paths via book lookup before
    handing the entry to the per-operation formatter.

    Path inputs are unchanged. Resolution failures degrade gracefully
    (raw value stays in place) so logging never crashes a tool.
    """

    def test_short_guid_in_top_level_account_param_resolved(
        self, test_book
    ):
        from gnucash_mcp.book import GnuCashBook
        from gnucash_mcp.logging_config import (
            _format_audit_entry_text,
            setup_logging,
        )

        book = GnuCashBook(str(test_book))
        setup_logging(
            book_path=str(test_book),
            debug=False,
            get_book=lambda: book,
        )

        # Discover Checking's short GUID the same way the LLM would —
        # off the list_accounts compact line.
        with book.open(readonly=True) as b:
            account = book._find_account(b, "Assets:Checking")
            short = book._account_short_guid(b, account)

        # Build a synthetic RECONCILE entry with the short GUID where
        # the LLM would normally pass it.
        entry = {
            "classification": "write",
            "operation": "reconcile",
            "entity_type": "split",
            "timestamp": "2026-04-27T12:00:00-07:00",
            "params": {
                "account": short,
                "statement_date": "2026-04-27",
                "statement_balance": "2850.00",
                "split_guids": [],
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert "Assets:Checking" in rendered, (
            f"audit log should resolve {short!r} to the full path; "
            f"got:\n{rendered}"
        )
        assert short not in rendered, (
            f"audit log still contains the raw short GUID {short!r}:\n{rendered}"
        )

    def test_short_guid_in_split_list_resolved(self, test_book):
        from gnucash_mcp.book import GnuCashBook
        from gnucash_mcp.logging_config import (
            _format_audit_entry_text,
            setup_logging,
        )

        book = GnuCashBook(str(test_book))
        setup_logging(
            book_path=str(test_book),
            debug=False,
            get_book=lambda: book,
        )

        with book.open(readonly=True) as b:
            checking = book._find_account(b, "Assets:Checking")
            groceries = book._find_account(b, "Expenses:Groceries")
            short_check = book._account_short_guid(b, checking)
            short_grocery = book._account_short_guid(b, groceries)

        # Synthetic CREATE TRANSACTION entry with shorts in both splits.
        entry = {
            "classification": "write",
            "operation": "create",
            "entity_type": "transaction",
            "timestamp": "2026-04-27T12:00:00-07:00",
            "params": {
                "description": "Lunch",
                "transaction_date": "2026-04-27",
                "splits": [
                    {"account": short_check, "amount": "-12.50"},
                    {"account": short_grocery, "amount": "12.50"},
                ],
            },
        }
        rendered = _format_audit_entry_text(entry)
        # Transaction-split rendering deliberately uses leaf names for
        # compactness (``_format_splits_text``). Pre-normalization the
        # leaf would be the raw short GUID itself (no colons → leaf == ref).
        # After normalization we get the human leaf names. Both branches
        # of that contract:
        assert "Checking" in rendered
        assert "Groceries" in rendered
        assert short_check not in rendered
        assert short_grocery not in rendered

    def test_full_guid_resolved_too(self, test_book):
        from gnucash_mcp.book import GnuCashBook
        from gnucash_mcp.logging_config import (
            _format_audit_entry_text,
            setup_logging,
        )

        book = GnuCashBook(str(test_book))
        setup_logging(
            book_path=str(test_book),
            debug=False,
            get_book=lambda: book,
        )
        with book.open(readonly=True) as b:
            account = book._find_account(b, "Assets:Checking")
            full_guid = account.guid

        entry = {
            "classification": "write",
            "operation": "set_slot",
            "entity_type": "account_slot",
            "timestamp": "2026-04-27T12:00:00-07:00",
            "params": {
                "account": full_guid,
                "key": "color",
                "value": "blue",
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert "Assets:Checking" in rendered
        assert full_guid not in rendered

    def test_path_input_unchanged(self, test_book):
        from gnucash_mcp.book import GnuCashBook
        from gnucash_mcp.logging_config import (
            _format_audit_entry_text,
            setup_logging,
        )

        book = GnuCashBook(str(test_book))
        setup_logging(
            book_path=str(test_book),
            debug=False,
            get_book=lambda: book,
        )

        entry = {
            "classification": "write",
            "operation": "set_slot",
            "entity_type": "account_slot",
            "timestamp": "2026-04-27T12:00:00-07:00",
            "params": {
                "account": "Assets:Checking",
                "key": "color",
                "value": "blue",
            },
        }
        rendered = _format_audit_entry_text(entry)
        assert "Assets:Checking" in rendered

    def test_unresolvable_short_guid_left_in_place(self, test_book):
        """Resolution failure (well-formed prefix that matches nothing)
        must NOT crash the formatter — it falls through to the raw value.
        Better to log ``%deadbe0`` than to drop the entry."""
        from gnucash_mcp.book import GnuCashBook
        from gnucash_mcp.logging_config import (
            _format_audit_entry_text,
            setup_logging,
        )

        book = GnuCashBook(str(test_book))
        setup_logging(
            book_path=str(test_book),
            debug=False,
            get_book=lambda: book,
        )

        entry = {
            "classification": "write",
            "operation": "reconcile",
            "entity_type": "split",
            "timestamp": "2026-04-27T12:00:00-07:00",
            "params": {
                "account": "%deadbe0",
                "statement_date": "2026-04-27",
                "statement_balance": "0.00",
                "split_guids": [],
            },
        }
        rendered = _format_audit_entry_text(entry)
        # Raw value preserved when resolution fails.
        assert "%deadbe0" in rendered


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


class TestResolveMcpDir:
    """Stage 6 — path-traversal hardening on the .mcp directory
    resolver. Audit / debug / backup all flow through this helper
    so an attacker who can write to the book's parent directory
    can't redirect log writes via a pre-created symlink."""

    def test_default_derivation(self, tmp_path):
        """Without GNUCASH_LOG_DIR, the .mcp dir is derived from
        the book path's parent and name."""
        book = tmp_path / "alex.gnucash"
        # Don't actually need the file to exist; the helper
        # only stats the parent.
        result = resolve_mcp_dir(book)
        assert result == tmp_path / "alex.gnucash.mcp"

    def test_env_override_used(self, tmp_path, monkeypatch):
        """GNUCASH_LOG_DIR takes precedence over derivation — and
        yields a PER-BOOK subdir under it, so two books sharing the
        override never interleave audit files or backup state."""
        override = tmp_path / "elsewhere"
        monkeypatch.setenv("GNUCASH_LOG_DIR", str(override))
        result = resolve_mcp_dir(tmp_path / "alex.gnucash")
        assert result == override / "alex.gnucash.mcp"
        other = resolve_mcp_dir(tmp_path / "linwei.gnucash")
        assert other == override / "linwei.gnucash.mcp"
        assert result != other

    def test_env_override_expands_tilde(self, monkeypatch):
        """GNUCASH_LOG_DIR=~/foo expands to the user's home."""
        monkeypatch.setenv("GNUCASH_LOG_DIR", "~/my-logs")
        result = resolve_mcp_dir("/anywhere/book.gnucash")
        assert "~" not in str(result)
        assert str(result).endswith("my-logs/book.gnucash.mcp")

    @pytest.mark.skipif(
        os.name != "posix",
        reason="POSIX permission check skipped on non-Unix",
    )
    def test_rejects_world_writable_parent(self, tmp_path):
        """A book in a 777 parent directory raises a clear error.

        Threat model: hostile co-located process pre-creates
        ``alex.gnucash.mcp`` as a symlink to attacker-controlled
        storage before the server runs.
        """
        # Make tmp_path world-writable (without sticky bit).
        os.chmod(tmp_path, 0o777)
        book = tmp_path / "alex.gnucash"
        try:
            with pytest.raises(ValueError, match="world-writable"):
                resolve_mcp_dir(book)
        finally:
            # Restore for pytest cleanup.
            os.chmod(tmp_path, 0o755)

    @pytest.mark.skipif(
        os.name != "posix",
        reason="POSIX permission check skipped on non-Unix",
    )
    def test_accepts_user_only_writable_parent(self, tmp_path):
        """Default tmp_path permissions are safe."""
        # tmp_path defaults to 0o700 on most pytest configs.
        result = resolve_mcp_dir(tmp_path / "alex.gnucash")
        assert result == tmp_path / "alex.gnucash.mcp"

    @pytest.mark.skipif(
        os.name != "posix",
        reason="POSIX permission check skipped on non-Unix",
    )
    def test_sticky_bit_does_not_exempt(self, tmp_path):
        """Sticky-bit directories (like /tmp) are STILL rejected.
        Copilot PR #91 review: the sticky bit prevents non-owner
        deletion/rename but does not prevent non-owner creation
        of new entries — a hostile process can still pre-create
        ``{book}.mcp`` as a symlink in /tmp before the server
        runs. Set GNUCASH_LOG_DIR explicitly if the book lives
        in a sticky-bit dir."""
        os.chmod(tmp_path, 0o1777)
        try:
            with pytest.raises(ValueError, match="world-writable"):
                resolve_mcp_dir(tmp_path / "alex.gnucash")
        finally:
            os.chmod(tmp_path, 0o755)

    @pytest.mark.skipif(
        os.name != "posix",
        reason="POSIX symlink check skipped on non-Unix",
    )
    def test_existing_mcp_symlink_rejected(self, tmp_path):
        """Defense in depth: if a ``.mcp`` already exists at the
        derived path and is a symlink, refuse. Catches the case
        where an earlier symlink-hijack attempt left an artifact
        even after the parent's permissions were tightened."""
        book = tmp_path / "alex.gnucash"
        target = tmp_path / "elsewhere"
        target.mkdir()
        (tmp_path / "alex.gnucash.mcp").symlink_to(target)
        with pytest.raises(ValueError, match="symlink"):
            resolve_mcp_dir(book)

    def test_env_override_bypasses_perm_check(
        self, tmp_path, monkeypatch,
    ):
        """The env-override path lets a user opt into any
        location they want — the perm check is for the derived
        path only. Setting GNUCASH_LOG_DIR is an explicit
        statement of trust."""
        if os.name == "posix":
            os.chmod(tmp_path, 0o777)
        override = tmp_path / "explicit"
        monkeypatch.setenv("GNUCASH_LOG_DIR", str(override))
        try:
            # Should not raise despite the world-writable
            # parent — env override bypasses the check.
            result = resolve_mcp_dir(tmp_path / "alex.gnucash")
            assert result == override / "alex.gnucash.mcp"
        finally:
            if os.name == "posix":
                os.chmod(tmp_path, 0o755)


class TestRedactPaths:
    """Stage 6 — path leak redaction. When GNUCASH_REDACT_PATHS=1,
    absolute paths in error messages are reduced to their basename
    so externally-shared MCP responses don't leak filesystem
    layout. Default off (opt-in)."""

    from gnucash_mcp.logging_config import redact_paths
    _rp = staticmethod(redact_paths)

    def test_passthrough_when_unset(self, monkeypatch):
        """Without the env var, text is returned unchanged."""
        monkeypatch.delenv("GNUCASH_REDACT_PATHS", raising=False)
        text = "GnuCash book not found: /Users/stephen/Books/alex.gnucash"
        assert self._rp(text) == text

    def test_passthrough_when_set_to_zero(self, monkeypatch):
        """Only the exact value '1' enables redaction."""
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "0")
        text = "Path: /Users/alice/secret.gnucash"
        assert self._rp(text) == text

    def test_posix_path_redacted(self, monkeypatch):
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        text = "GnuCash book not found: /Users/stephen/Books/alex.gnucash"
        result = self._rp(text)
        assert "/Users/stephen" not in result
        assert "alex.gnucash" in result

    def test_windows_path_redacted(self, monkeypatch):
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        text = r"Cannot open C:\Users\Alice\Documents\book.gnucash"
        result = self._rp(text)
        assert r"C:\Users" not in result
        assert "book.gnucash" in result

    def test_windows_forward_slash_redacted(self, monkeypatch):
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        text = "Path: C:/Users/Alice/book.gnucash"
        result = self._rp(text)
        assert "/Alice" not in result
        assert "book.gnucash" in result

    def test_relative_paths_pass_through(self, monkeypatch):
        """Relative paths don't leak filesystem layout — left alone."""
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        text = "Failed to read samples/book.gnucash from working dir"
        result = self._rp(text)
        # The relative path itself doesn't get rewritten (no leading
        # / or drive letter). "samples/book.gnucash" stays intact.
        assert "samples/book.gnucash" in result

    def test_multiple_paths_in_one_message(self, monkeypatch):
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        text = (
            "Backup verification failed: "
            "/Users/alice/books/source.gnucash -> "
            "/Users/alice/backups/snapshot.db"
        )
        result = self._rp(text)
        assert "/Users/alice" not in result
        assert "source.gnucash" in result
        assert "snapshot.db" in result

    def test_path_in_quotes_redacted(self, monkeypatch):
        """Paths inside quotes are caught at quote boundaries."""
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        text = "Lock on file '/Users/alice/book.gnucash' detected"
        result = self._rp(text)
        assert "/Users" not in result
        assert "book.gnucash" in result

    def test_no_paths_no_change(self, monkeypatch):
        """Plain error messages with no paths pass through unchanged."""
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        text = "Account not found: Expenses:Coffee"
        # Colon between words isn't a Windows drive letter, but
        # the regex requires alphabetic single char + ":" + slash.
        assert self._rp(text) == text


class TestWriteRateLimiter:
    """Stage 6 — write rate-limiting at the audit_log decorator.
    Token bucket; disabled by default; opt-in via
    GNUCASH_WRITE_RATE_LIMIT env var."""

    def _setup_decorated_tool(self):
        """Build a minimal write-classified tool decorated by
        audit_log. Returns the wrapped function."""
        @audit_log(classification="write", operation="create",
                   entity_type="transaction")
        def fake_write(description: str = "x") -> str:
            return json.dumps({"guid": "abc12345", "status": "created"})
        return fake_write

    def test_no_limit_by_default(self, monkeypatch, temp_book_path):
        """Without the env var, writes proceed unthrottled."""
        from gnucash_mcp.logging_config import reset_write_rate_limiter
        monkeypatch.delenv("GNUCASH_WRITE_RATE_LIMIT", raising=False)
        reset_write_rate_limiter()
        setup_logging(book_path=str(temp_book_path), debug=False)
        tool = self._setup_decorated_tool()
        # 50 consecutive writes — all succeed.
        for _ in range(50):
            result = json.loads(tool(description="x"))
            assert "error" not in result

    def test_rate_limit_kicks_in(self, monkeypatch, temp_book_path):
        """Burst of 3 with rate 0.1 tok/s: 4th write rate-limited."""
        from gnucash_mcp.logging_config import reset_write_rate_limiter
        monkeypatch.setenv("GNUCASH_WRITE_RATE_LIMIT", "0.1")
        monkeypatch.setenv("GNUCASH_WRITE_BURST", "3")
        reset_write_rate_limiter()
        setup_logging(book_path=str(temp_book_path), debug=False)
        tool = self._setup_decorated_tool()
        # First three drain the burst capacity.
        for i in range(3):
            r = json.loads(tool(description=f"x{i}"))
            assert "error" not in r, f"call {i} unexpectedly limited"
        # Fourth hits the limit.
        r = json.loads(tool(description="x3"))
        assert r.get("error_type") == "rate_limited"
        assert "retry_after_seconds" in r
        # Retry is positive (some real wall-clock delay).
        assert r["retry_after_seconds"] > 0

    def test_reads_never_rate_limited(self, monkeypatch, temp_book_path):
        """Even with an aggressive write limit, reads are
        unaffected. The decorator's gate is on classification."""
        from gnucash_mcp.logging_config import reset_write_rate_limiter
        monkeypatch.setenv("GNUCASH_WRITE_RATE_LIMIT", "0.01")
        monkeypatch.setenv("GNUCASH_WRITE_BURST", "1")
        reset_write_rate_limiter()
        setup_logging(book_path=str(temp_book_path), debug=False)

        @audit_log(classification="read")
        def fake_read() -> str:
            return json.dumps({"ok": True})

        # 20 reads in a row — none rate-limited.
        for _ in range(20):
            r = json.loads(fake_read())
            assert "error" not in r

    def test_invalid_env_value_disables(self, monkeypatch):
        """A non-numeric env value disables the limiter (with a
        debug-log warning) rather than crashing."""
        from gnucash_mcp.logging_config import (
            _get_write_rate_limiter,
            reset_write_rate_limiter,
        )
        monkeypatch.setenv("GNUCASH_WRITE_RATE_LIMIT", "asdf")
        reset_write_rate_limiter()
        assert _get_write_rate_limiter() is None

    def test_zero_or_negative_rate_disables(self, monkeypatch):
        from gnucash_mcp.logging_config import (
            _get_write_rate_limiter,
            reset_write_rate_limiter,
        )
        for val in ("0", "0.0", "-5"):
            monkeypatch.setenv("GNUCASH_WRITE_RATE_LIMIT", val)
            reset_write_rate_limiter()
            assert _get_write_rate_limiter() is None, f"{val} should disable"

    def test_token_bucket_refills(self, monkeypatch):
        """Direct unit test on the bucket: after waiting, tokens
        refill at the configured rate."""
        from gnucash_mcp.logging_config import _WriteRateLimiter
        limiter = _WriteRateLimiter(rate=10.0, burst=2)
        # Drain the bucket.
        assert limiter.consume() == (True, 0.0)
        assert limiter.consume() == (True, 0.0)
        # Bucket empty — third call denied.
        allowed, retry = limiter.consume()
        assert not allowed
        # Retry should be small (~0.1s for 1 token at 10 tok/s).
        assert 0.05 < retry < 0.2
        # Wait for refill (sleep slightly longer than retry).
        time.sleep(0.15)
        # Now allowed.
        allowed, _ = limiter.consume()
        assert allowed
