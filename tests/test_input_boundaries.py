"""Regression tests for the Branch 3 input-boundary + atomicity work.

Five bug classes the code review surfaced about untrusted input
and write atomicity, each closed in its own commit:

- ``TestGetAuditLogPathTraversal`` — SB-15 (this commit): the
  ``log_date`` arg was interpolated into a ``Path`` without
  validation. A regex gate now rejects anything that isn't
  literally ``YYYY-MM-DD`` before path construction, blocking the
  ``../../../../etc/passwd`` style prompt-injection exfiltration
  vector.

- ``TestDeleteAccountSlotKeyValidation`` — HP-11 (commit 2):
  ``delete_account_slot`` skipped the ``_SLOT_KEY_RE`` gate
  ``set_account_slot`` enforced, so a user could target internal
  ``gnc-mcp/...`` slots the validator was meant to protect.

- ``TestInputLengthCaps`` + ``TestSplitInputExtraForbid`` — HP-9
  + HP-10 (commit 3): ``set_account_slot(value)`` and
  ``void_transaction(reason)`` accepted arbitrary-length strings;
  ``SplitInput`` used ``extra="ignore"`` which silently swallowed
  typos like ``quantitiy``.

- ``TestScheduledTransactionAtomicity`` — SB-10 (commit 4): the
  two-session write where the schedule advanced before the
  transaction landed could leave a half-state (schedule moved,
  transaction missing) on raise. Restructured to create the
  transaction first; a raise leaves the schedule unchanged. A
  duplicate-detector ``rejected`` return is treated as success
  (the duplicate IS the transaction for this period) with a
  ``reason: "duplicate_exists"`` field added to the response so
  downstream LLMs have explicit evidence to stop and move on
  rather than retry.

If any of these tests fails without an intentional change to the
boundary contract, the bug class is open again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── SB-15: get_audit_log path traversal ────────────────────────────


class TestGetAuditLogPathTraversal:
    """SB-15: ``get_audit_log(log_date)`` must validate ``log_date``
    against ``YYYY-MM-DD`` before constructing the file path.

    Pre-fix the value was interpolated directly into
    ``audit_dir / f"{log_date}.txt"``. ``log_date="../../../../etc/
    passwd"`` resolved to ``/private/etc/passwd.txt`` on macOS —
    any ``*.txt`` file readable by the server process could be
    exfiltrated through a prompt-injection vector that influences
    a user's call to this tool.
    """

    @pytest.fixture
    def audit_tool(self, tmp_path, monkeypatch):
        """Set up a server with the audit module loaded and the
        log dir pointed at a temp location with one valid log
        file ready to read."""
        from gnucash_mcp.logging_config import setup_logging
        from gnucash_mcp.server import (
            _apply_module_filter,
            _reset_lazy_load_state,
            mcp,
        )

        # Fresh book + logging dir.
        book_path = tmp_path / "test.gnucash"
        book_path.touch()
        setup_logging(book_path=str(book_path), debug=False)

        # Seed a valid audit log file for the "well-formed
        # date works" baseline test.
        log_dir = tmp_path / "test.gnucash.mcp" / "audit"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "2026-06-04.txt").write_text(
            "GNUCASH MCP AUDIT LOG\n\n"
            "[entry placeholder]\n"
        )

        # Save + restore the loaded tools state so other tests
        # aren't disturbed.
        original = dict(mcp._tool_manager._tools)
        try:
            mcp._tool_manager._tools.clear()
            _reset_lazy_load_state()
            _apply_module_filter("audit")
            yield mcp._tool_manager._tools["get_audit_log"]
        finally:
            mcp._tool_manager._tools.clear()
            mcp._tool_manager._tools.update(original)
            _reset_lazy_load_state()

    def test_well_formed_date_works(self, audit_tool):
        """``YYYY-MM-DD`` continues to work — the gate doesn't
        break the happy path."""
        result = audit_tool.fn(log_date="2026-06-04")
        # The seeded file exists and has content; result should
        # not be the "no audit log" path or an error.
        assert "AUDIT LOG" in result or "[entry placeholder]" in result

    def test_path_traversal_rejected(self, audit_tool):
        """``../../../../etc/passwd`` (the canonical traversal
        probe) must be rejected by the regex gate before any
        filesystem access."""
        result = audit_tool.fn(log_date="../../../../etc/passwd")
        # The safe_tool decorator routes ValueError to a JSON
        # error envelope. We rejected via _json(...) directly in
        # the tool, so check for the validation_error envelope.
        parsed = json.loads(result)
        assert parsed["error_type"] == "validation_error"
        assert "log_date" in parsed["error"].lower()

    def test_traversal_via_url_encoded_dots_rejected(self, audit_tool):
        """A URL-encoded traversal attempt would arrive as the
        literal characters ``%2e%2e%2f...`` if any layer
        decoded it. Regex still rejects (no hex chars in
        ``YYYY-MM-DD``)."""
        result = audit_tool.fn(log_date="%2e%2e/etc/passwd")
        parsed = json.loads(result)
        assert parsed["error_type"] == "validation_error"

    def test_partial_date_shape_rejected(self, audit_tool):
        """Almost-but-not-quite valid shapes get caught too:
        ``2026-6-4`` (missing zero-padding) doesn't match the
        ``\\d{4}-\\d{2}-\\d{2}`` pattern."""
        result = audit_tool.fn(log_date="2026-6-4")
        parsed = json.loads(result)
        assert parsed["error_type"] == "validation_error"

    def test_year_only_rejected(self, audit_tool):
        """A bare year (``2026``) doesn't match the regex —
        regression for a future relaxation that might fall back
        to year-prefix matching."""
        result = audit_tool.fn(log_date="2026")
        parsed = json.loads(result)
        assert parsed["error_type"] == "validation_error"

    def test_empty_string_falls_back_to_today(self, audit_tool):
        """Empty string is falsy in Python's ``or`` fallback, so it
        behaves identically to ``log_date=None`` and resolves to
        today's date — not a security hole (the regex still gates
        the resulting target_date, and today's strftime output
        always matches). Documented here as the intentional shape
        of the falsy-fallback contract."""
        result = audit_tool.fn(log_date="")
        # Either today's file exists (string output) or it doesn't
        # ("No audit log for ..."). Either way, NOT a
        # validation_error envelope.
        if result.startswith("{"):
            parsed = json.loads(result)
            assert parsed.get("error_type") != "validation_error", (
                f"empty string tripped validation gate: {result}"
            )

    def test_default_date_works(self, audit_tool, tmp_path):
        """``log_date=None`` falls through to today's date —
        format is generated by ``strftime("%Y-%m-%d")`` which
        always matches the regex. No false positive on the
        default path."""
        # Today's audit file may or may not exist; we only care
        # that we don't hit the validation_error envelope.
        result = audit_tool.fn()
        if result.startswith("{"):
            parsed = json.loads(result)
            assert parsed.get("error_type") != "validation_error", (
                f"default date path tripped validation gate: {result}"
            )
