"""Tests for the BackupMixin and auto-backup hook.

Design reference: ``specs/archive/BACKUP_TOOL_SPEC.md``. The tests mirror the
"Testing Strategy" section of that spec one-for-one so a reviewer
can cross-reference what's asserted against what was designed.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import piecash
import pytest

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.book.backup import (
    _AUTO_STAGES,
    _FILENAME_RE,
    _read_attempt_status,
    _read_state,
    _sanitize_label,
    _write_attempt_status,
    _write_state,
)


# ── Manual backup creation ──────────────────────────────────────────


class TestCreateBackup:
    """Direct ``create_backup()`` behavior on a real temp book."""

    def test_create_backup_produces_valid_file(self, test_book: Path):
        """Backup must be a real SQLite file readable by piecash with
        the same transaction content as the source.
        """
        book = GnuCashBook(str(test_book))
        result = book.create_backup(stage="manual", label="smoke")

        assert result["status"] == "created"
        backup_path = Path(result["path"])
        assert backup_path.exists()
        assert backup_path.stat().st_size > 0

        # Open the backup with piecash — it should be a valid book
        # with the same transactions as the source.
        src_count = len(piecash.open_book(str(test_book), readonly=True).transactions)
        dst_count = len(piecash.open_book(str(backup_path), readonly=True).transactions)
        assert src_count == dst_count > 0

    def test_create_backup_integrity_check_ok(self, test_book: Path):
        """A fresh backup should pass ``PRAGMA integrity_check``."""
        book = GnuCashBook(str(test_book))
        result = book.create_backup(stage="manual")
        assert result["integrity"] == "ok"

    def test_create_backup_integrity_failure_deletes_bad_file(
        self, test_book: Path
    ):
        """If the integrity check fails, the bad backup file must be
        deleted and a RuntimeError raised. The caller should never
        have to clean up a half-broken snapshot.
        """
        book = GnuCashBook(str(test_book))

        # The simplest way to force a failing integrity check is to
        # patch `sqlite3.connect` for the whole create_backup call so
        # the second connection (the verify step) returns a wrapper
        # that reports corruption. The first connection (the backup
        # destination) passes through untouched so the actual page
        # copy still succeeds.
        real_connect = sqlite3.connect
        state = {"call": 0}

        class _FailingCursor:
            def fetchone(self):
                return ("corrupted: page 42 checksum mismatch",)

        class _VerifyConnection:
            def __init__(self, real):
                self._real = real

            def execute(self, *a, **kw):
                return _FailingCursor()

            def close(self):
                self._real.close()

        def fake_connect(path, *args, **kwargs):
            state["call"] += 1
            real = real_connect(path, *args, **kwargs)
            # First call = the backup destination. Leave alone so the
            # copy actually writes pages. Second call = the verify
            # connection — return a wrapper that lies about integrity.
            if state["call"] >= 2:
                return _VerifyConnection(real)
            return real

        with patch.object(sqlite3, "connect", side_effect=fake_connect):
            with pytest.raises(RuntimeError, match="integrity check failed"):
                book.create_backup(stage="manual", label="doomed")

        # The bad backup file must NOT be left behind.
        backups_dir = test_book.parent / f"{test_book.name}.mcp" / "backups"
        assert list(backups_dir.glob("*.gnucash")) == []

    def test_backup_label_sanitized(self, test_book: Path):
        """Labels with filesystem-unsafe characters become safe."""
        # Slashes, colons, spaces, non-ASCII — everything non
        # [A-Za-z0-9_-] is collapsed to dashes.
        book = GnuCashBook(str(test_book))
        result = book.create_backup(
            stage="manual", label="pre/tax: review 2026 é"
        )
        name = Path(result["path"]).name
        m = _FILENAME_RE.match(name)
        assert m is not None, f"Filename {name} did not match schema"
        label = m.group("label")
        assert label is not None
        # Characters are restricted to the safe alphabet
        assert all(c.isalnum() or c in "-_" for c in label)
        assert "pre" in label
        assert "tax" in label
        assert "review" in label
        assert "2026" in label

    def test_label_sanitize_helper_edge_cases(self):
        """Low-level helper: empty / all-unsafe inputs become None."""
        assert _sanitize_label(None) is None
        assert _sanitize_label("") is None
        assert _sanitize_label("   ") is None
        assert _sanitize_label("/::/") is None
        assert _sanitize_label("abc") == "abc"
        assert _sanitize_label("a b c") == "a-b-c"
        assert _sanitize_label("--x--") == "x"


# ── Listing ─────────────────────────────────────────────────────────


class TestListBackups:
    def test_list_backups_empty(self, test_book: Path):
        """No backups dir / empty dir → empty list."""
        book = GnuCashBook(str(test_book))
        assert book.list_backups() == []

    def test_list_backups_sorts_newest_first(self, test_book: Path):
        """Three backups at different stages should list newest
        first regardless of stage.
        """
        book = GnuCashBook(str(test_book))

        # Monkeypatch _now_utc to stage three backups with distinct
        # timestamps a second apart.
        import gnucash_mcp.book.backup as backup_mod

        base = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)
        timestamps = [base, base + timedelta(seconds=1), base + timedelta(seconds=2)]

        for i, ts in enumerate(timestamps):
            with patch.object(backup_mod, "_now_utc", return_value=ts):
                book.create_backup(stage="manual", label=f"n{i}")

        entries = book.list_backups()
        assert len(entries) == 3
        # Newest first: labels were n0/n1/n2, so expected order is
        # n2 then n1 then n0.
        labels = [e["label"] for e in entries]
        assert labels == ["n2", "n1", "n0"]


# ── Pruning ─────────────────────────────────────────────────────────


class TestPruneBackups:
    def _make_n_session_backups(self, book: GnuCashBook, n: int):
        """Create n session backups with distinct timestamps."""
        import gnucash_mcp.book.backup as backup_mod
        base = datetime(2026, 4, 19, 8, 0, 0, tzinfo=timezone.utc)
        for i in range(n):
            ts = base + timedelta(minutes=i)
            with patch.object(backup_mod, "_now_utc", return_value=ts):
                book.create_backup(stage="session")

    def test_prune_defaults_to_dry_run(self, test_book: Path):
        """Default call reports a plan and deletes nothing."""
        book = GnuCashBook(str(test_book))
        self._make_n_session_backups(book, 3)

        result = book.prune_backups(keep_last_n=1)
        assert result["dry_run"] is True
        assert len(result["would_delete"]) == 2
        assert len(result["would_keep"]) == 1

        # Nothing actually got deleted
        assert len(book.list_backups()) == 3

    def test_prune_respects_keep_last_n(self, test_book: Path):
        """With dry_run=False, only the newest N survive per stage."""
        book = GnuCashBook(str(test_book))
        self._make_n_session_backups(book, 10)

        result = book.prune_backups(
            keep_last_n=3, stage="session", dry_run=False
        )
        assert result["dry_run"] is False
        assert len(result["deleted"]) == 7
        assert len(result["kept"]) == 3

        remaining = book.list_backups()
        assert len(remaining) == 3
        # The three kept should be the newest three timestamps
        kept_ts = sorted(e["timestamp"] for e in remaining)
        assert kept_ts[0] > "2026-04-19T08:06"  # minutes 7/8/9

    def test_prune_never_touches_manual_unless_asked(
        self, test_book: Path
    ):
        """With stage=None, only auto stages are pruned — manual
        backups are preserved regardless of keep_last_n.
        """
        book = GnuCashBook(str(test_book))
        import gnucash_mcp.book.backup as backup_mod

        base = datetime(2026, 4, 19, 8, 0, 0, tzinfo=timezone.utc)

        # 5 manual backups + 5 session backups
        for i in range(5):
            ts = base + timedelta(minutes=i)
            with patch.object(backup_mod, "_now_utc", return_value=ts):
                book.create_backup(stage="manual", label=f"m{i}")
        for i in range(5):
            ts = base + timedelta(hours=1, minutes=i)
            with patch.object(backup_mod, "_now_utc", return_value=ts):
                book.create_backup(stage="session")

        # Default stage=None prunes auto stages only.
        result = book.prune_backups(keep_last_n=2, dry_run=False)
        assert result["dry_run"] is False
        # Manual backups aren't considered — 3 session deletions only.
        assert len(result["deleted"]) == 3

        remaining = book.list_backups()
        by_stage = {"session": 0, "manual": 0}
        for e in remaining:
            by_stage[e["stage"]] += 1
        assert by_stage["manual"] == 5  # untouched
        assert by_stage["session"] == 2  # pruned to keep_last_n

    def test_prune_explicit_manual_stage_works(self, test_book: Path):
        """Explicit stage='manual' DOES prune manual backups."""
        book = GnuCashBook(str(test_book))
        import gnucash_mcp.book.backup as backup_mod

        base = datetime(2026, 4, 19, 8, 0, 0, tzinfo=timezone.utc)
        for i in range(4):
            ts = base + timedelta(minutes=i)
            with patch.object(backup_mod, "_now_utc", return_value=ts):
                book.create_backup(stage="manual", label=f"m{i}")

        result = book.prune_backups(
            keep_last_n=2, stage="manual", dry_run=False
        )
        assert len(result["deleted"]) == 2
        assert len(book.list_backups()) == 2


# ── Auto-backup driver ──────────────────────────────────────────────


class TestMaybeAutoBackup:
    """``_maybe_auto_backup()`` is the heart of the auto-protection
    story — the hook every write triggers on its first invocation
    per process.
    """

    def test_fires_first_write_when_stage_due(self, test_book: Path):
        """No state file → every stage is 'due' → a backup gets taken."""
        book = GnuCashBook(str(test_book))
        book._maybe_auto_backup()

        entries = book.list_backups()
        assert len(entries) == 1
        # First run, no state: should hit monthly (highest priority).
        assert entries[0]["stage"] == "monthly"

    def test_skips_when_within_interval(self, test_book: Path):
        """Fresh session backup < 12 hours ago should NOT trigger."""
        book = GnuCashBook(str(test_book))
        backups_dir = book._backups_dir()
        backups_dir.mkdir(parents=True, exist_ok=True)

        # Pre-seed all three auto stages as "recently done."
        now = datetime.now(timezone.utc)
        state = {
            "session": now - timedelta(hours=1),
            "weekly": now - timedelta(days=1),
            "monthly": now - timedelta(days=5),
        }
        _write_state(backups_dir, state)

        book._maybe_auto_backup()
        entries = book.list_backups()
        assert entries == []  # no backup should have been taken

    def test_once_per_process(self, test_book: Path):
        """Two calls in the same process — second is a no-op."""
        book = GnuCashBook(str(test_book))
        book._maybe_auto_backup()
        n1 = len(book.list_backups())
        book._maybe_auto_backup()
        n2 = len(book.list_backups())
        assert n1 == 1
        assert n2 == 1  # no additional backup

    def test_failure_does_not_raise(self, test_book: Path):
        """If the backup machinery raises, _maybe_auto_backup
        swallows it so the user's write can proceed.
        """
        book = GnuCashBook(str(test_book))

        # Force create_backup to raise. The method should swallow.
        with patch.object(
            book, "create_backup", side_effect=RuntimeError("disk full")
        ):
            book._maybe_auto_backup()  # must not raise

    def test_all_stages_from_scratch_advance_timestamps(
        self, test_book: Path
    ):
        """First run with empty state should update all three auto
        stages' timestamps to the same 'now' — so a single backup
        satisfies session, weekly, and monthly simultaneously.
        """
        book = GnuCashBook(str(test_book))
        book._maybe_auto_backup()

        backups_dir = book._backups_dir()
        state = _read_state(backups_dir)
        assert set(state.keys()) == {"session", "weekly", "monthly"}
        # All three timestamps equal (same moment)
        timestamps = list(state.values())
        assert timestamps[0] == timestamps[1] == timestamps[2]

    def test_records_success_status(self, test_book: Path):
        """A successful auto-backup writes ``status=ok`` to
        ``.last_attempt.json`` so get_book_summary can surface it."""
        book = GnuCashBook(str(test_book))
        book._maybe_auto_backup()

        attempt = _read_attempt_status(book._backups_dir())
        assert attempt is not None
        assert attempt["status"] == "ok"
        assert attempt["reason"] is None

    def test_records_failure_status_when_swallowed(
        self, test_book: Path,
    ):
        """A failed auto-backup must not raise (the user's write
        proceeds) BUT the failure must be persisted so the
        bookkeeper finds out via get_book_summary's warnings —
        not via reading debug logs weeks later. Pre-fix, OSError
        was logged-and-forgotten, leaving the bookkeeper blind."""
        book = GnuCashBook(str(test_book))

        with patch.object(
            book, "create_backup",
            side_effect=OSError("disk full"),
        ):
            book._maybe_auto_backup()  # swallows

        attempt = _read_attempt_status(book._backups_dir())
        assert attempt is not None
        assert attempt["status"] == "failed"
        assert "disk full" in (attempt["reason"] or "")

    def test_get_backup_health_reports_failure(self, test_book: Path):
        """``get_backup_health`` exposes the persisted attempt
        status, the structure get_book_summary reads."""
        book = GnuCashBook(str(test_book))
        with patch.object(
            book, "create_backup", side_effect=OSError("readonly fs"),
        ):
            book._maybe_auto_backup()

        health = book.get_backup_health()
        assert health["last_attempt"]["status"] == "failed"
        assert "readonly fs" in health["last_attempt"]["reason"]
        # No backup file → newest is None.
        assert health["newest_backup_at"] is None
        assert health["newest_backup_age_days"] is None

    def test_get_backup_health_reports_success_and_freshness(
        self, test_book: Path,
    ):
        """Healthy state: success status + recent newest-backup age."""
        book = GnuCashBook(str(test_book))
        book._maybe_auto_backup()
        health = book.get_backup_health()
        assert health["last_attempt"]["status"] == "ok"
        assert health["newest_backup_at"] is not None
        # Created in this test run → 0 or close.
        assert health["newest_backup_age_days"] in (0, 1)

    def test_promotes_to_highest_due_stage(self, test_book: Path):
        """Session done recently, weekly and monthly overdue → the
        next backup is tagged 'monthly' (highest priority due).
        """
        book = GnuCashBook(str(test_book))
        backups_dir = book._backups_dir()
        backups_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        state = {
            "session": now - timedelta(hours=1),   # recent → NOT due
            "weekly": now - timedelta(days=10),    # overdue
            "monthly": now - timedelta(days=45),   # overdue
        }
        _write_state(backups_dir, state)

        book._maybe_auto_backup()
        entries = book.list_backups()
        assert len(entries) == 1
        assert entries[0]["stage"] == "monthly"

        # Both overdue stages' timestamps advanced; the "recent"
        # session timestamp did not change.
        new_state = _read_state(backups_dir)
        assert new_state["monthly"] > state["monthly"]
        assert new_state["weekly"] > state["weekly"]
        assert new_state["session"] == state["session"]


# ── Integration with @audit_log ─────────────────────────────────────


class TestAuditHookIntegration:
    """The audit decorator should call _maybe_auto_backup on the
    first write of a process and never thereafter.
    """

    def test_write_triggers_auto_backup(self, test_book: Path):
        """A real write through @audit_log triggers auto-backup
        before executing the write.
        """
        from gnucash_mcp.logging_config import audit_log, setup_logging

        book = GnuCashBook(str(test_book))
        setup_logging(
            book_path=str(test_book),
            debug=False,
            get_book=lambda: book,
        )

        @audit_log(
            classification="write",
            operation="update",
            entity_type="transaction",
        )
        def fake_write(**kwargs) -> str:
            return json.dumps({"guid": "abc", "status": "updated"})

        assert book._backup_checked_in_process is False
        fake_write(guid="deadbeef", description="x")
        assert book._backup_checked_in_process is True

        # Auto-backup created a file
        assert len(book.list_backups()) == 1

    def test_read_does_not_trigger_auto_backup(self, test_book: Path):
        """Reads have no business snapshotting — verify the hook
        only fires on writes.
        """
        from gnucash_mcp.logging_config import audit_log, setup_logging

        book = GnuCashBook(str(test_book))
        setup_logging(
            book_path=str(test_book),
            debug=False,
            get_book=lambda: book,
        )

        @audit_log(classification="read")
        def fake_read() -> str:
            return json.dumps({"data": "ok"})

        fake_read()
        # Flag never toggled; no backups exist
        assert book._backup_checked_in_process is False
        assert book.list_backups() == []
