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

    def test_describe_age_rounds_to_nearest_unit(self):
        """``_describe_age`` rounds rather than floors. Pre-fix
        59.9 minutes displayed as "59 minutes ago" (one unit short
        of the next boundary); now displays as "1 hour ago".
        """
        from gnucash_mcp.book.backup import _describe_age
        from datetime import datetime, timezone, timedelta

        ref = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        # 59m30s ago — rounds up to 60 min → promoted to 1 hour.
        ts = ref - timedelta(minutes=59, seconds=30)
        assert _describe_age(ts, ref) == "1 hour ago"
        # 89m30s ago — rounds to 90 min → 2 hours? actually 1.5h
        # which rounds to either. Python's banker's rounding on
        # 1.5 → 2. Let's test 100 min (clearly 2 hours).
        ts = ref - timedelta(minutes=100)
        assert _describe_age(ts, ref) == "2 hours ago"
        # 23h59m ago — rounds to 24 hours → promoted to 1 day.
        ts = ref - timedelta(hours=23, minutes=59)
        assert _describe_age(ts, ref) == "1 day ago"

    def test_list_backups_logs_warning_on_unstattable_file(
        self, test_book: Path, caplog,
    ):
        """A broken symlink (target deleted) produces an OSError on
        ``stat()``. Pre-fix this was silently dropped from
        ``list_backups`` — N-1 entries shown, broken file invisible.
        Now logs a debug warning so the issue surfaces in
        post-hoc inspection."""
        import logging
        book = GnuCashBook(str(test_book))
        # Make a real backup, then replace it with a broken symlink.
        result = book.create_backup(stage="manual", label="will-break")
        backup_path = Path(result["path"])
        backup_path.unlink()
        # Create a symlink whose target doesn't exist.
        backup_path.symlink_to("/nonexistent/target/path")

        with caplog.at_level(logging.WARNING, logger="gnucash_mcp.debug"):
            entries = book.list_backups()

        # Broken symlink is dropped from the listing.
        names = [Path(e["path"]).name for e in entries]
        assert backup_path.name not in names
        # But a warning was logged.
        assert any(
            "unstattable" in r.message.lower() for r in caplog.records
        )

    def test_backup_partial_file_unlinked_on_copy_failure(
        self, test_book: Path,
    ):
        """If the SQLite ``backup()`` call raises mid-copy, the
        partial/empty destination file must be unlinked rather
        than left on disk where ``list_backups`` would surface it
        as a "valid" backup entry.

        Forces the failure by patching the source connection's
        ``backup`` method to raise — that's where the fix's new
        ``except`` branch fires (closing dest_conn and unlinking
        the partial file).
        """
        backups_dir = test_book.parent / f"{test_book.name}.mcp" / "backups"
        book = GnuCashBook(str(test_book))

        # Wrap piecash's source connection so .backup raises.
        original_open = book.open

        from contextlib import contextmanager

        @contextmanager
        def patched_open(*args, **kwargs):
            with original_open(*args, **kwargs) as b:
                real_connection = b.session.connection
                real_conn_obj = real_connection().connection

                # Replace source_conn.backup with a raiser via a
                # proxy object.
                class _RaisingSource:
                    def __init__(self, real):
                        self._real = real

                    def backup(self, *a, **kw):
                        raise OSError("simulated disk I/O error")

                    def __getattr__(self, name):
                        return getattr(self._real, name)

                class _ConnWrapper:
                    @property
                    def connection(self):
                        return _RaisingSource(real_conn_obj)

                b.session.connection = lambda: _ConnWrapper()
                yield b

        with patch.object(book, "open", side_effect=patched_open):
            with pytest.raises(OSError, match="simulated disk I/O error"):
                book.create_backup(stage="manual", label="doomed")

        # No partial file left behind.
        if backups_dir.exists():
            assert list(backups_dir.glob("*.gnucash")) == [], (
                f"Partial backup file persisted: "
                f"{list(backups_dir.glob('*.gnucash'))}"
            )

    def test_two_backups_in_same_second_do_not_collide(
        self, test_book: Path,
    ):
        """Pre-fix, ``_format_ts`` had second resolution. Two
        ``create_backup`` calls within the same second produced the
        same filename and ``sqlite3.connect(path).backup(...)``
        truncated the existing file — second snapshot silently
        overwrote the first.

        Microsecond resolution makes collisions practically
        impossible. The ``Path.exists()`` precheck is the second line
        of defense if a clock-resolution collision somehow occurs.
        """
        book = GnuCashBook(str(test_book))
        r1 = book.create_backup(stage="manual", label="rapid-1")
        r2 = book.create_backup(stage="manual", label="rapid-2")
        # Different filenames even though wall-clock seconds match
        assert r1["path"] != r2["path"]
        # Both files exist on disk
        assert Path(r1["path"]).exists()
        assert Path(r2["path"]).exists()

    def test_create_backup_refuses_to_overwrite(self, test_book: Path):
        """If a backup file with the target name already exists (e.g.,
        clock-resolution collision or pathological monkeypatched
        time), ``create_backup`` raises rather than silently
        truncating the prior snapshot."""
        book = GnuCashBook(str(test_book))
        fixed_ts = datetime(2026, 5, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
        with patch(
            "gnucash_mcp.book.backup._now_utc", return_value=fixed_ts,
        ):
            r1 = book.create_backup(stage="manual", label="first")
            assert Path(r1["path"]).exists()
            # Same wall-clock = same filename = refusal.
            with pytest.raises(RuntimeError, match="refusing to overwrite"):
                book.create_backup(stage="manual", label="first")

    def test_legacy_second_resolution_filenames_still_parse(
        self, test_book: Path,
    ):
        """Pre-fix backup files (14-digit second-resolution timestamp)
        must still be readable by ``list_backups`` after the upgrade
        to microsecond filenames. Otherwise users would lose
        visibility on their pre-upgrade backups."""
        backups_dir = test_book.parent / f"{test_book.name}.mcp" / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        # Write a fake legacy-format file. Content doesn't matter for
        # this listing test (list_backups only stats the file).
        legacy = backups_dir / f"{test_book.stem}-20260101T120000-manual.gnucash"
        legacy.write_bytes(b"fake")

        book = GnuCashBook(str(test_book))
        listed = book.list_backups()
        legacy_entry = next(
            (e for e in listed if Path(e["path"]).name == legacy.name),
            None,
        )
        assert legacy_entry is not None, "Legacy filename failed to parse"
        assert legacy_entry["stage"] == "manual"

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

    def test_prune_under_path_redaction_deletes_real_files(
        self, test_book: Path, monkeypatch,
    ):
        """H2 regression: with GNUCASH_REDACT_PATHS=1, ``list_backups``
        redacts each entry's ``path`` to a bare basename. Pre-fix the
        pruners called ``Path(entry["path"]).unlink()`` on that
        basename, which resolved against the process CWD — a silent
        no-op, so retention never actually trimmed. The prune must
        still delete the real files in the backups dir.
        """
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        book = GnuCashBook(str(test_book))
        self._make_n_session_backups(book, 10)

        backups_dir = book._backups_dir()
        assert len(list(backups_dir.iterdir())) == 10

        # Redaction is actually active: listed paths are basenames.
        listed = book.list_backups()
        assert listed and all("/" not in e["path"] for e in listed)

        result = book.prune_backups(
            keep_last_n=3, stage="session", dry_run=False
        )
        assert len(result["deleted"]) == 7

        # The real files are gone (pre-fix: all 10 would remain because
        # unlink targeted a CWD basename that doesn't exist there).
        remaining = sorted(p.name for p in backups_dir.iterdir())
        assert len(remaining) == 3, (
            f"prune under path redaction did not delete real files; "
            f"backups dir still holds: {remaining}"
        )

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

    def test_prune_refuses_to_wipe_all_manual_backups(self, test_book: Path):
        """``prune_backups(keep_last_n=0, dry_run=False, stage="manual")``
        must raise rather than wipe every human-marked backup.

        Manual backups have unlimited retention BY DESIGN — they
        include "pre-tax-filing" / "pre-irreplaceable-thing"
        snapshots the user explicitly preserved. A misbehaving LLM
        concluding "let me clean up old backups" would otherwise
        nuke every one in a single call.
        """
        book = GnuCashBook(str(test_book))
        book.create_backup(stage="manual", label="precious")
        book.create_backup(stage="manual", label="also-precious")

        with pytest.raises(ValueError, match="Refusing to delete every manual backup"):
            book.prune_backups(keep_last_n=0, stage="manual", dry_run=False)

        # All manual backups still on disk after the refusal.
        manual_count = sum(
            1 for e in book.list_backups() if e["stage"] == "manual"
        )
        assert manual_count == 2

    def test_prune_dry_run_with_zero_manual_still_allowed(
        self, test_book: Path,
    ):
        """The guard fires only on the destructive path.
        ``dry_run=True`` — even with keep_last_n=0 manual — must
        still produce a plan (so the user can SEE what would be
        deleted before opting in)."""
        book = GnuCashBook(str(test_book))
        book.create_backup(stage="manual", label="check")

        result = book.prune_backups(
            keep_last_n=0, stage="manual", dry_run=True,
        )
        assert result["dry_run"] is True
        # Plan shows the 1 manual would be deleted.
        assert len(result["would_delete"]) == 1

    def test_prune_refuses_to_wipe_all_auto_backups(self, test_book: Path):
        """MP-3 symmetric guard: ``prune_backups(keep_last_n=0,
        dry_run=False)`` without an explicit ``stage`` must raise
        rather than wipe every auto backup across all auto stages.

        Auto backups rebuild over time (sessions on next write,
        weekly on next Monday, monthly on next 1st), but in the
        interim the user has no way to recover backups they didn't
        realize they were deleting. Mirrors the manual-stage guard
        — same shape of footgun, different stage scope.
        """
        book = GnuCashBook(str(test_book))
        book.create_backup(stage="session", label="auto-1")
        book.create_backup(stage="session", label="auto-2")

        with pytest.raises(
            ValueError,
            match="Refusing to delete every auto backup at once",
        ):
            book.prune_backups(keep_last_n=0, dry_run=False)

        # Auto backups still on disk after the refusal.
        session_count = sum(
            1 for e in book.list_backups() if e["stage"] == "session"
        )
        assert session_count == 2

    def test_prune_dry_run_with_zero_auto_still_allowed(
        self, test_book: Path,
    ):
        """``dry_run=True`` with implicit-auto + keep_last_n=0
        must still produce a plan (matches the manual-stage
        symmetric behavior — review-before-act is always
        permitted)."""
        book = GnuCashBook(str(test_book))
        book.create_backup(stage="session", label="auto")

        result = book.prune_backups(keep_last_n=0, dry_run=True)
        assert result["dry_run"] is True

    def test_prune_explicit_auto_stage_with_zero_allowed(
        self, test_book: Path,
    ):
        """An explicit ``stage='session'`` with ``keep_last_n=0``
        is intentional and permitted — the user opted in to
        zero-retention for that specific stage. The guard only
        catches the implicit-all-auto-stages case."""
        book = GnuCashBook(str(test_book))
        book.create_backup(stage="session", label="auto")

        result = book.prune_backups(
            keep_last_n=0, stage="session", dry_run=False,
        )
        # At least one auto-stage backup was deleted.
        assert len(result["deleted"]) >= 1

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


class TestBackupPathRedaction:
    """MP-4: backup tool responses must honor
    ``GNUCASH_REDACT_PATHS=1`` so absolute paths can collapse to
    basenames before the response leaves the server. Default off
    (paths are useful debugging signal locally); opt-in via the
    same env var the error-message redaction uses.
    """

    def test_create_backup_path_redacted_when_env_set(
        self, test_book: Path, monkeypatch,
    ):
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        book = GnuCashBook(str(test_book))
        result = book.create_backup(stage="manual", label="test")
        # path collapses to basename — no directory components.
        assert "/" not in result["path"], result["path"]
        # restore_hint should not embed any absolute paths
        # either.
        assert " /" not in result["restore_hint"], result["restore_hint"]
        # The book filename is preserved (so the user knows what
        # they're restoring).
        assert test_book.name in result["restore_hint"]

    def test_create_backup_path_intact_by_default(
        self, test_book: Path, monkeypatch,
    ):
        monkeypatch.delenv("GNUCASH_REDACT_PATHS", raising=False)
        book = GnuCashBook(str(test_book))
        result = book.create_backup(stage="manual", label="test")
        # Default behavior emits full absolute paths.
        assert str(test_book.parent) in result["restore_hint"], (
            "default behavior should embed full paths for actionable "
            f"restore hint; got: {result['restore_hint']!r}"
        )

    def test_list_backups_paths_redacted_when_env_set(
        self, test_book: Path, monkeypatch,
    ):
        # Create with redaction off so the file lands at a real
        # absolute path, then re-list with redaction on.
        monkeypatch.delenv("GNUCASH_REDACT_PATHS", raising=False)
        book = GnuCashBook(str(test_book))
        book.create_backup(stage="manual", label="redaction-test")
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        listed = book.list_backups()
        assert listed, "fixture failed: backup not created"
        for entry in listed:
            assert "/" not in entry["path"], entry
