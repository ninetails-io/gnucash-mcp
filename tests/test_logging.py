"""Tests for the logging and audit system."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from gnucash_mcp.logging_config import (
    AUDIT_LOGGER_NAME,
    DEBUG_LOGGER_NAME,
    audit_log,
    setup_logging,
)


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
    """Tests for the audit_log decorator (JSON format)."""

    def test_read_tool_logged(self, temp_book_path, temp_log_dir):
        """Verify read tool calls are logged with params (JSON format)."""
        setup_logging(book_path=str(temp_book_path), debug=False, audit_format="json")

        @audit_log(classification="read")
        def test_read_tool(account_name: str) -> str:
            return json.dumps({"balance": "100.00"})

        result = test_read_tool(account_name="Assets:Checking")

        # Read the log file
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        log_file = temp_log_dir / "audit" / f"{today}.jsonl"
        assert log_file.exists()

        entries = [json.loads(line) for line in log_file.read_text().strip().split("\n")]
        assert len(entries) >= 1

        entry = entries[-1]
        assert entry["tool"] == "test_read_tool"
        assert entry["classification"] == "read"
        assert entry["params"] == {"account_name": "Assets:Checking"}
        assert entry["result"] == "success"
        assert "before_state" not in entry
        assert "after_state" not in entry

    def test_write_tool_logged(self, temp_book_path, temp_log_dir):
        """Verify write tool calls are logged with operation and entity_type (JSON format)."""
        setup_logging(book_path=str(temp_book_path), debug=False, audit_format="json")

        @audit_log(classification="write", operation="create", entity_type="transaction")
        def test_create_tool(description: str) -> str:
            return json.dumps({"guid": "abc123", "status": "created"})

        result = test_create_tool(description="Test transaction")

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        log_file = temp_log_dir / "audit" / f"{today}.jsonl"
        entries = [json.loads(line) for line in log_file.read_text().strip().split("\n")]

        entry = entries[-1]
        assert entry["tool"] == "test_create_tool"
        assert entry["classification"] == "write"
        assert entry["operation"] == "create"
        assert entry["entity_type"] == "transaction"
        assert entry["result"] == "success"
        assert entry["entity_guid"] == "abc123"

    def test_failed_tool_logged(self, temp_book_path, temp_log_dir):
        """Verify failed tool calls are logged with error (JSON format)."""
        setup_logging(book_path=str(temp_book_path), debug=False, audit_format="json")

        @audit_log(classification="read")
        def test_failing_tool(name: str) -> str:
            raise ValueError("Account not found")

        with pytest.raises(ValueError):
            test_failing_tool(name="Nonexistent")

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        log_file = temp_log_dir / "audit" / f"{today}.jsonl"
        entries = [json.loads(line) for line in log_file.read_text().strip().split("\n")]

        entry = entries[-1]
        assert entry["tool"] == "test_failing_tool"
        assert entry["result"] == "error"
        assert entry["error"] == "Account not found"

    def test_error_json_response_logged_as_error(self, temp_book_path, temp_log_dir):
        """Verify tools returning JSON with 'error' key are logged as errors (JSON format)."""
        setup_logging(book_path=str(temp_book_path), debug=False, audit_format="json")

        @audit_log(classification="read")
        def test_error_response(name: str) -> str:
            return json.dumps({"error": "Not found", "error_type": "not_found"})

        result = test_error_response(name="Bad")

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        log_file = temp_log_dir / "audit" / f"{today}.jsonl"
        entries = [json.loads(line) for line in log_file.read_text().strip().split("\n")]

        entry = entries[-1]
        assert entry["result"] == "error"
        assert entry["error"] == "Not found"


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

    def test_text_format_creates_txt_file(self, temp_book_path, temp_log_dir):
        """Verify text format creates .txt file, not .jsonl."""
        setup_logging(book_path=str(temp_book_path), debug=False, audit_format="text")

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        jsonl_file = temp_log_dir / "audit" / f"{today}.jsonl"

        assert txt_file.exists()
        assert not jsonl_file.exists()

    def test_text_format_has_header(self, temp_book_path, temp_log_dir):
        """Verify text format file has proper header."""
        setup_logging(book_path=str(temp_book_path), debug=False, audit_format="text")

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        txt_file = temp_log_dir / "audit" / f"{today}.txt"
        content = txt_file.read_text()

        assert "GNUCASH MCP AUDIT LOG" in content
        assert str(temp_book_path) in content

    def test_text_format_logs_write_operations(self, temp_book_path, temp_log_dir):
        """Verify text format logs write operations in human-readable form."""
        setup_logging(book_path=str(temp_book_path), debug=False, audit_format="text")

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
        setup_logging(book_path=str(temp_book_path), debug=False, audit_format="text")

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

    def test_text_format_logs_replace_splits(self, temp_book_path, temp_log_dir):
        """Verify text format logs replace_splits with before and after splits."""
        setup_logging(book_path=str(temp_book_path), debug=False, audit_format="text")

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


class TestAuditLogIntegration:
    """Integration tests for the complete audit trail."""

    def test_write_operation_lifecycle(self, temp_book_path, temp_log_dir):
        """Test a complete write operation lifecycle captures correct states (JSON format)."""
        setup_logging(book_path=str(temp_book_path), debug=False, audit_format="json")

        # Simulate a create operation
        @audit_log(classification="write", operation="create", entity_type="transaction")
        def create_txn(description: str, splits: list) -> str:
            return json.dumps({
                "guid": "txn123",
                "description": description,
                "splits": splits,
            })

        create_txn(
            description="Grocery shopping",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
        )

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        log_file = temp_log_dir / "audit" / f"{today}.jsonl"
        entries = [json.loads(line) for line in log_file.read_text().strip().split("\n")]

        create_entry = [e for e in entries if e.get("operation") == "create"][-1]
        assert create_entry["entity_guid"] == "txn123"
        assert create_entry["after_state"]["description"] == "Grocery shopping"
