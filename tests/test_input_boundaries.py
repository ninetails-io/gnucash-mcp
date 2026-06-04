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


# ── HP-11: delete_account_slot key validation ─────────────────────


class TestDeleteAccountSlotKeyValidation:
    """HP-11: ``delete_account_slot`` must apply the same
    ``_SLOT_KEY_RE`` gate ``set_account_slot`` enforces.

    Pre-fix delete skipped the validator. A user could pass
    ``gnc-mcp/applies-to-invoice`` and delete an internal
    namespaced slot that the credit-note linkage depends on —
    the gate exists exactly to keep user input out of internal
    sub-slot space (the ``/`` separator creates hierarchical
    slots in piecash's KVP store).
    """

    def test_delete_rejects_namespaced_key(self, test_book):
        """A ``gnc-mcp/applies-to-invoice``-style namespaced key
        must be rejected, matching ``set_account_slot``'s gate.
        Pre-fix this would have walked straight through to
        ``account[key]`` lookup and either succeeded (deleting an
        internal slot) or returned KeyError (depending on whether
        that slot existed)."""
        from gnucash_mcp.book import GnuCashBook
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="Invalid slot key"):
            gb.delete_account_slot(
                account_name="Assets:Checking",
                key="gnc-mcp/applies-to-invoice",
            )

    def test_delete_rejects_bare_slash(self, test_book):
        """A bare ``/`` in the key fails the regex — it would
        create a sub-slot reference under piecash's KVP semantics
        and could target arbitrary internal state."""
        from gnucash_mcp.book import GnuCashBook
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="Invalid slot key"):
            gb.delete_account_slot(
                account_name="Assets:Checking",
                key="parent/child",
            )

    def test_delete_rejects_other_disallowed_chars(self, test_book):
        """Anything outside ``[A-Za-z0-9_.-]`` rejects — spaces,
        colons, etc. Match the set ``set_account_slot`` permits
        exactly."""
        from gnucash_mcp.book import GnuCashBook
        gb = GnuCashBook(str(test_book))
        for bad_key in ("has space", "has:colon", "has*star"):
            with pytest.raises(ValueError, match="Invalid slot key"):
                gb.delete_account_slot(
                    account_name="Assets:Checking",
                    key=bad_key,
                )

    def test_delete_accepts_legitimate_keys(self, test_book):
        """Keys matching the regex still work end-to-end. Set a
        slot via ``set_account_slot``, delete it via
        ``delete_account_slot``, confirm both round-trip cleanly
        without tripping the new gate."""
        from gnucash_mcp.book import GnuCashBook
        gb = GnuCashBook(str(test_book))
        # The classic flat-key cases the validator was always
        # meant to allow.
        gb.set_account_slot(
            account_name="Assets:Checking",
            key="apr",
            value="3.5",
        )
        result = gb.delete_account_slot(
            account_name="Assets:Checking",
            key="apr",
        )
        assert result["status"] == "deleted"

    def test_set_and_delete_have_matching_gates(self):
        """Symmetry check: any key ``set_account_slot`` accepts,
        ``delete_account_slot`` must accept too (and vice versa).
        Catches a future divergence where one regex tightens but
        the other doesn't."""
        from gnucash_mcp.book.admin import _SLOT_KEY_RE
        # Spot-check the canonical allowed and disallowed shapes.
        # The actual contract is encoded in ``_SLOT_KEY_RE`` itself
        # — both methods import the same regex, so the contract
        # holds by construction. This test locks the import path
        # (renaming the regex on one side without the other would
        # be a regression).
        allowed = ("apr", "credit_limit", "statement-close-day", "v1.2")
        disallowed = ("has space", "gnc-mcp/applies", "has:colon")
        for k in allowed:
            assert _SLOT_KEY_RE.fullmatch(k), (
                f"regex regression: {k!r} should be allowed"
            )
        for k in disallowed:
            assert not _SLOT_KEY_RE.fullmatch(k), (
                f"regex regression: {k!r} should be rejected"
            )


# ── HP-9: input length caps ────────────────────────────────────────


class TestInputLengthCaps:
    """HP-9: ``set_account_slot(value)`` and
    ``void_transaction(reason)`` capped at sensible upper bounds.

    Pre-fix both accepted arbitrary-length strings. A malicious or
    runaway caller could exhaust the book file with a single write
    by passing megabytes of payload. The caps are byte-count (UTF-8)
    so a multi-byte unicode payload can't sneak past a char-count
    check: 64 KiB for slot values (generous for any real per-account
    metadata), 4 KiB for void reasons (generous for any realistic
    audit explanation).
    """

    def test_set_account_slot_accepts_value_at_cap(self, test_book):
        """A value exactly at the byte cap is accepted — the cap is
        the inclusive boundary."""
        from gnucash_mcp.book import GnuCashBook
        from gnucash_mcp.book.admin import _SLOT_VALUE_MAX_BYTES
        gb = GnuCashBook(str(test_book))
        # Exactly at cap (ASCII payload — bytes == chars).
        payload = "x" * _SLOT_VALUE_MAX_BYTES
        result = gb.set_account_slot(
            account_name="Assets:Checking",
            key="big_blob",
            value=payload,
        )
        assert result["status"] == "created"

    def test_set_account_slot_rejects_oversize_value(self, test_book):
        """One byte past the cap rejects with a clear error."""
        from gnucash_mcp.book import GnuCashBook
        from gnucash_mcp.book.admin import _SLOT_VALUE_MAX_BYTES
        gb = GnuCashBook(str(test_book))
        payload = "x" * (_SLOT_VALUE_MAX_BYTES + 1)
        with pytest.raises(ValueError, match="too long"):
            gb.set_account_slot(
                account_name="Assets:Checking",
                key="too_big",
                value=payload,
            )

    def test_set_account_slot_counts_utf8_bytes_not_chars(self, test_book):
        """A multi-byte UTF-8 payload that fits in chars but blows
        the byte cap must reject. Catches the bug class where a
        char-count check would let a 32 KiB string of CJK characters
        (96 KiB in UTF-8) past a 64 KiB byte cap."""
        from gnucash_mcp.book import GnuCashBook
        from gnucash_mcp.book.admin import _SLOT_VALUE_MAX_BYTES
        gb = GnuCashBook(str(test_book))
        # "贵" is 3 bytes in UTF-8. Construct a payload that's
        # 1/3 the byte cap in chars but over the byte cap in bytes.
        char_count = (_SLOT_VALUE_MAX_BYTES // 3) + 1
        payload = "贵" * char_count
        assert len(payload) < _SLOT_VALUE_MAX_BYTES, (
            "fixture math wrong: char count should be below cap"
        )
        assert len(payload.encode("utf-8")) > _SLOT_VALUE_MAX_BYTES, (
            "fixture math wrong: byte count should be above cap"
        )
        with pytest.raises(ValueError, match="too long"):
            gb.set_account_slot(
                account_name="Assets:Checking",
                key="unicode_blob",
                value=payload,
            )

    def test_void_transaction_rejects_oversize_reason(self, test_book):
        """``void_transaction(reason)`` cap is 4 KiB."""
        from gnucash_mcp.book import GnuCashBook
        gb = GnuCashBook(str(test_book))
        # Find a transaction to void.
        with gb.open(readonly=True) as pb:
            txn = next(iter(pb.transactions))
            txn_guid = txn.guid
        # 4 KiB + 1 byte.
        oversize_reason = "x" * (4 * 1024 + 1)
        with pytest.raises(ValueError, match="too long"):
            gb.void_transaction(guid=txn_guid, reason=oversize_reason)

    def test_void_transaction_accepts_reasonable_reason(self, test_book):
        """A normal-length reason still works end-to-end."""
        from gnucash_mcp.book import GnuCashBook
        gb = GnuCashBook(str(test_book))
        with gb.open(readonly=True) as pb:
            txn = next(iter(pb.transactions))
            txn_guid = txn.guid
        reason = (
            "Voided after reconciliation found duplicate posting "
            "from the imported OFX file. Original entry retained "
            "for audit trail; new entry posted under " * 5
        )
        # ~700 chars — well under 4 KiB.
        result = gb.void_transaction(guid=txn_guid, reason=reason)
        assert result["status"] == "voided"


# ── HP-10: SplitInput extra="forbid" ──────────────────────────────


class TestSplitInputExtraForbid:
    """HP-10: ``SplitInput`` must reject unknown kwargs.

    Pre-fix ``model_config = ConfigDict(extra="ignore")`` silently
    dropped typo'd keys: ``{"account": "...", "quantitiy": "10"}``
    would discard the misspelled ``quantitiy`` entirely. With
    ``extra="forbid"`` the typo raises a Pydantic validation error
    at the boundary instead of corrupting the transaction shape
    downstream. Matches the server-global ``ArgModelBase`` setting
    PR #92 shipped.
    """

    def test_typo_in_quantity_raises(self):
        """The canonical bug shape from the spec: ``quantitiy``
        instead of ``quantity``."""
        from pydantic import ValidationError
        from gnucash_mcp.tools._helpers import SplitInput
        with pytest.raises(ValidationError) as exc:
            SplitInput(
                account="Assets:Checking",
                amount="10.00",
                quantitiy="10",  # typo
            )
        # The error message should name the unknown field so the
        # caller can identify the typo.
        assert "quantitiy" in str(exc.value)

    def test_known_fields_still_accepted(self):
        """The legitimate field set still works end-to-end."""
        from gnucash_mcp.tools._helpers import SplitInput
        s = SplitInput(
            account="Assets:Checking",
            amount="10.00",
            quantity="10.0000",
            memo="test",
        )
        assert s.account == "Assets:Checking"
        assert s.amount == "10.00"
        assert s.quantity == "10.0000"
        assert s.memo == "test"

    def test_arbitrary_unknown_field_raises(self):
        """Any field outside the declared set rejects — catches the
        regression class where a future renamed-but-forgotten field
        would silently drop."""
        from pydantic import ValidationError
        from gnucash_mcp.tools._helpers import SplitInput
        with pytest.raises(ValidationError):
            SplitInput(
                account="Assets:Checking",
                amount="10.00",
                bogus_field="anything",
            )
