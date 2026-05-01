"""BackupMixin — SQLite online-backup snapshots with staged retention.

Data loss is the one failure mode that can't be undone from within
this server: a mangled book with no clean copy is game over. This
mixin adds snapshot-based recovery: a ``create_backup()`` primitive
built on SQLite's online backup API, plus a ``_maybe_auto_backup()``
hook the audit decorator calls on the first write of a process to
protect users who forget to back up manually.

Auto-backups follow a grandfather-father-son retention policy — at
most 7 session (12h spacing), 4 weekly (7d spacing), 6 monthly (30d
spacing) automatic backups. Manual backups made via the
``create_backup()`` MCP tool are kept indefinitely and never touched
by auto-pruning.

Restore is explicitly not exposed as an MCP tool. The documented
procedure is a filesystem copy with the server stopped — see
``docs/RESTORE_FROM_BACKUP.md``. If the server is broken enough to
need a restore, we can't trust it to do one safely.

Design reference: ``specs/archive/BACKUP_TOOL_SPEC.md``.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import piecash

debug_logger = logging.getLogger("gnucash_mcp.debug")


# ── Retention policy ─────────────────────────────────────────────────


@dataclass(frozen=True)
class _BackupStage:
    """A tier in the grandfather-father-son retention scheme.

    ``interval`` is the minimum spacing between backups of this stage;
    a new backup is only taken when ``now - last_backup_of_stage``
    exceeds it. ``keep_last_n`` is the retention count — older backups
    in this stage are pruned.
    """

    name: str
    interval: timedelta
    keep_last_n: int


# Order matters: higher-priority first. When multiple stages are due
# at the same time, the resulting backup file is tagged with the
# highest-priority name (but every due stage's state-file timestamp
# is advanced, so a single file satisfies them all).
_AUTO_STAGES: tuple[_BackupStage, ...] = (
    _BackupStage("monthly", timedelta(days=30), 6),
    _BackupStage("weekly", timedelta(days=7), 4),
    _BackupStage("session", timedelta(hours=12), 7),
)

_MANUAL_STAGE_NAME = "manual"

# Pre-computed set for fast validation / filtering. Manual is separate
# because it has unlimited retention and no interval gating.
_AUTO_STAGE_NAMES: frozenset[str] = frozenset(s.name for s in _AUTO_STAGES)
_ALL_STAGE_NAMES: frozenset[str] = _AUTO_STAGE_NAMES | {_MANUAL_STAGE_NAME}

# Filename schema:
#   {book_stem}-{YYYYmmddTHHMMSSffffff}-{stage}[-{label}].gnucash
# Timestamp is UTC, colons stripped for filesystem safety. The
# microsecond suffix (``ffffff``) is optional in the regex so legacy
# second-resolution backups continue to parse — files written by
# v1.2.1 and earlier had no microseconds. New writes always emit the
# 20-digit form. Label is sanitized to [A-Za-z0-9_-].
_FILENAME_RE = re.compile(
    r"^(?P<stem>.+)"
    r"-(?P<ts>\d{8}T\d{6}(?:\d{6})?)"
    r"-(?P<stage>[a-z]+)"
    r"(?:-(?P<label>[A-Za-z0-9_-]+))?"
    r"\.gnucash$"
)

# Anything stripped to [] becomes the default label "untitled" so the
# file never ends with a trailing dash.
_LABEL_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _sanitize_label(label: str | None) -> str | None:
    """Return a filesystem-safe label or None if input was empty/useless."""
    if not label:
        return None
    safe = _LABEL_SAFE_RE.sub("-", label).strip("-")
    return safe or None


def _now_utc() -> datetime:
    """Timezone-aware UTC timestamp, factored out so tests can monkeypatch."""
    return datetime.now(timezone.utc)


def _format_ts(ts: datetime) -> str:
    """Format a UTC timestamp for filenames (colons stripped).

    Includes microseconds. Pre-fix, second-resolution timestamps meant
    two ``create_backup`` calls within the same second produced the
    same filename — and SQLite's ``connection.backup(dest_conn)``
    truncates an existing dest, so the second snapshot silently
    overwrote the first. Microsecond resolution makes collisions
    practically impossible; the explicit ``Path.exists()`` check in
    ``create_backup`` is the second line of defense.
    """
    return ts.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _parse_ts(ts_str: str) -> datetime:
    """Inverse of ``_format_ts``. Accepts both new (microsecond) and
    legacy (second) resolution so v1.2.1-and-earlier backup files
    continue to parse. Returns tz-aware UTC datetime.
    """
    fmt = "%Y%m%dT%H%M%S%f" if len(ts_str) > 15 else "%Y%m%dT%H%M%S"
    return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)


def _describe_age(ts: datetime, reference: datetime | None = None) -> str:
    """Human-readable age string for listings — 'just now', '3 days ago', etc.

    Rounds to nearest unit rather than floor — pre-fix a 59.9-minute
    age displayed as "59 minutes ago" (one unit short of the next
    boundary). Round-half-up makes the boundary cases honest:
    59m30s reads as "60 minutes ago" → which then promotes to "1
    hour ago" via the next-bucket check.
    """
    now = reference or _now_utc()
    delta = now - ts
    seconds = delta.total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = round(seconds / 60)
        if minutes >= 60:
            return "1 hour ago"
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = round(seconds / 3600)
        if hours >= 24:
            return "1 day ago"
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = round(seconds / 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


# ── State file (last-backup-per-stage) ───────────────────────────────


def _state_path(backups_dir: Path) -> Path:
    return backups_dir / ".state.json"


def _read_state(backups_dir: Path) -> dict[str, datetime]:
    """Read ``last_backup_by_stage`` as a dict of stage → UTC datetime.

    Missing file, malformed JSON, or unparseable timestamps all degrade
    to an empty state — auto-backup reacts by treating every stage as
    due, which is the safe default.
    """
    path = _state_path(backups_dir)
    try:
        with path.open() as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    result: dict[str, datetime] = {}
    for stage, iso in data.get("last_backup_by_stage", {}).items():
        if stage not in _AUTO_STAGE_NAMES:
            continue
        try:
            parsed = datetime.fromisoformat(iso)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            result[stage] = parsed
        except (TypeError, ValueError):
            # Malformed entry → treat that stage as "never run"
            continue
    return result


def _write_state(backups_dir: Path, state: dict[str, datetime]) -> None:
    """Persist ``last_backup_by_stage`` to disk."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "last_backup_by_stage": {
            stage: ts.astimezone(timezone.utc).isoformat()
            for stage, ts in state.items()
        },
    }
    path = _state_path(backups_dir)
    # Write via a temp + rename so a partial write never leaves a
    # corrupted state file.
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(path)


# ── Auto-backup attempt status (separate file) ───────────────────────
#
# Decoupled from .state.json on purpose: the per-stage timestamp file
# advances only on successful backup, so it can't tell us "we tried
# 6 hours ago and it failed." A separate ``.last_attempt.json``
# captures every attempt — success or failure — so get_book_summary
# can surface backup-chain breaks the bookkeeper would otherwise
# discover only by reading debug logs.


def _attempt_path(backups_dir: Path) -> Path:
    return backups_dir / ".last_attempt.json"


def _read_attempt_status(backups_dir: Path) -> dict | None:
    """Return ``{status, reason, at}`` for the most recent auto-backup
    attempt, or None if no attempt has ever been recorded.

    ``status`` is ``"ok"`` or ``"failed"``. ``reason`` is the
    exception string for failures (None on success). ``at`` is a
    tz-aware UTC datetime.
    """
    path = _attempt_path(backups_dir)
    try:
        with path.open() as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    try:
        at = datetime.fromisoformat(data["at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    status = data.get("status")
    if status not in ("ok", "failed"):
        return None
    return {
        "status": status,
        "reason": data.get("reason"),
        "at": at,
    }


def _write_attempt_status(
    backups_dir: Path,
    status: str,
    reason: str | None,
    at: datetime,
) -> None:
    """Persist the most recent auto-backup attempt result. Atomic
    temp+rename, same pattern as ``_write_state``.
    """
    backups_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "reason": reason,
        "at": at.astimezone(timezone.utc).isoformat(),
    }
    path = _attempt_path(backups_dir)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(path)


# ── Filename inspection ──────────────────────────────────────────────


def _parse_backup_filename(
    path: Path,
) -> tuple[datetime, str, str | None] | None:
    """Return ``(timestamp, stage, label)`` if the filename looks like
    a backup, else None. Used by ``list_backups`` and ``prune_backups``
    to read metadata from filenames without consulting any other state.
    """
    m = _FILENAME_RE.match(path.name)
    if not m:
        return None
    try:
        ts = _parse_ts(m.group("ts"))
    except ValueError:
        return None
    stage = m.group("stage")
    if stage not in _ALL_STAGE_NAMES:
        return None
    return ts, stage, m.group("label")


# ── Mixin ────────────────────────────────────────────────────────────


class BackupMixin:
    """Snapshot the GnuCash book via SQLite's online backup API.

    Integrates with ``BaseGnuCashBook.book_path`` to compute the
    backup directory (``{book_path}.mcp/backups/``) and piecash's
    SQLite connection for atomic, lock-safe page-level copying.
    """

    # Flag toggled on first auto-backup attempt per process lifecycle.
    # Prevents stat + JSON read on every subsequent write once we've
    # decided this process is up-to-date.
    _backup_checked_in_process: bool = False

    # Serialize the read+write of ``_backup_checked_in_process`` across
    # threads. Without this, two simultaneous "first writes" of the
    # session both pass the gate and both call ``create_backup`` —
    # second-resolution filenames could collide, and the second backup
    # would silently overwrite the first. The standard MCP deployment
    # is single-threaded, so this is defense-in-depth for any future
    # multi-worker deployment.
    _backup_check_lock: threading.Lock = threading.Lock()

    # ── Paths ────────────────────────────────────────────────────

    def _backups_dir(self) -> Path:
        """Where backups live: ``{book_path}.mcp/backups/``.

        Consistent with audit / debug log locations so users backing up
        their GnuCash folder (e.g., via Time Machine) pick up the
        snapshots automatically.
        """
        book_path = self.book_path
        return book_path.parent / f"{book_path.name}.mcp" / "backups"

    # ── Core primitive: create a backup ──────────────────────────

    def create_backup(
        self,
        *,
        stage: str = _MANUAL_STAGE_NAME,
        label: str | None = None,
    ) -> dict:
        """Write a fresh snapshot of the book via SQLite's online
        backup API and verify it with PRAGMA integrity_check.

        Args:
            stage: One of ``session``, ``weekly``, ``monthly``,
                ``manual``. Auto stages are driven by the retention
                policy; manual is user-invoked and has unlimited
                retention.
            label: Optional free-text label (sanitized to
                ``[A-Za-z0-9_-]``) appended to the filename. Useful for
                "pre-big-reorg" style human markers.

        Returns:
            Dict with ``status``, ``stage``, ``path``, ``size_bytes``,
            ``integrity``, and ``restore_hint``. All paths are absolute
            strings.

        Raises:
            ValueError: If the stage is unknown.
            RuntimeError: If the integrity check fails after the copy
                (the bad backup file is deleted before raising so the
                caller never has to clean up).
            OSError: If the backup directory can't be created or
                written.
        """
        if stage not in _ALL_STAGE_NAMES:
            raise ValueError(
                f"Unknown backup stage: {stage!r}. "
                f"Must be one of: {', '.join(sorted(_ALL_STAGE_NAMES))}"
            )

        safe_label = _sanitize_label(label)
        ts = _now_utc()
        backups_dir = self._backups_dir()
        backups_dir.mkdir(parents=True, exist_ok=True)

        stem = self.book_path.stem
        ts_part = _format_ts(ts)
        filename = f"{stem}-{ts_part}-{stage}"
        if safe_label:
            filename += f"-{safe_label}"
        filename += ".gnucash"
        backup_path = backups_dir / filename

        # Defense in depth against filename collisions. The microsecond
        # resolution in ``_format_ts`` makes this practically
        # impossible, but ``sqlite3.connect(path)`` would happily
        # truncate an existing file — so refuse to overwrite explicitly
        # rather than silently destroy a prior snapshot.
        if backup_path.exists():
            raise RuntimeError(
                f"Backup path already exists, refusing to overwrite: "
                f"{backup_path}. This indicates a clock-resolution "
                f"collision; retry."
            )

        # Perform the SQLite online backup. We open the source via
        # piecash's readonly context (no write lock on the live book)
        # and reach into the underlying sqlite3 connection. SQLite's
        # backup() copies pages in chunks without blocking readers.
        with self.open(readonly=True) as book:
            source_conn = book.session.connection().connection
            dest_conn = sqlite3.connect(str(backup_path))
            try:
                source_conn.backup(dest_conn)
            except Exception:
                # Disk-full (or any other) failure mid-copy leaves
                # a partial/empty file at backup_path. Pre-fix the
                # try/finally only closed the connection — the
                # truncated file stayed on disk and would surface
                # in ``list_backups`` as a "valid backup" until the
                # next ``PRAGMA integrity_check`` (which only runs
                # in the success path). Best to fail loud: close
                # the connection, unlink the partial file, and
                # propagate the original exception.
                dest_conn.close()
                try:
                    backup_path.unlink()
                except OSError:
                    pass
                raise
            finally:
                # Idempotent: safe to call after the explicit close
                # in the except branch — sqlite3.connection.close()
                # is no-op on a closed connection.
                dest_conn.close()

        # Verify the backup with PRAGMA integrity_check before
        # declaring success. If the check fails, delete the bad file
        # so we never leave a broken snapshot masquerading as a valid
        # recovery option.
        verify_conn = sqlite3.connect(str(backup_path))
        try:
            row = verify_conn.execute("PRAGMA integrity_check").fetchone()
            integrity = row[0] if row else "unknown"
        finally:
            verify_conn.close()
        if integrity != "ok":
            try:
                backup_path.unlink()
            except OSError:
                pass
            raise RuntimeError(
                f"Backup integrity check failed: {integrity}. "
                f"Bad backup file has been deleted."
            )

        size_bytes = backup_path.stat().st_size
        return {
            "status": "created",
            "stage": stage,
            "path": str(backup_path),
            "size_bytes": size_bytes,
            "integrity": integrity,
            "restore_hint": (
                "Restore by stopping the server, then: "
                f"mv {self.book_path} {self.book_path}.broken && "
                f"cp {backup_path} {self.book_path}"
            ),
        }

    # ── Listing ──────────────────────────────────────────────────

    def list_backups(self) -> list[dict]:
        """Return every recognized backup file under the backups dir,
        newest first. Each entry carries ``stage``, ``timestamp`` (ISO
        UTC), ``age`` (human-readable), ``size_bytes``, ``label``, and
        ``path``.
        """
        backups_dir = self._backups_dir()
        if not backups_dir.exists():
            return []

        entries: list[dict] = []
        now = _now_utc()
        for path in backups_dir.iterdir():
            parsed = _parse_backup_filename(path)
            if parsed is None:
                continue
            ts, stage, label = parsed
            try:
                size = path.stat().st_size
            except OSError as e:
                # Most common case: a broken symlink (target moved
                # or deleted). Pre-fix this was silently dropped —
                # ``list_backups`` showed N-1 entries and
                # ``prune_backups`` would never clean the broken
                # link. Logging surfaces the issue at the next
                # debug-log inspection without breaking the listing.
                debug_logger.warning(
                    f"Backup file unstattable, skipping: {path} ({e})"
                )
                continue
            entries.append({
                "stage": stage,
                "timestamp": ts.isoformat(),
                "age": _describe_age(ts, now),
                "size_bytes": size,
                "label": label,
                "path": str(path),
            })

        entries.sort(key=lambda e: e["timestamp"], reverse=True)
        return entries

    # ── Pruning ──────────────────────────────────────────────────

    def prune_backups(
        self,
        keep_last_n: int,
        *,
        stage: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        """Remove old backups, keeping the most recent N per stage.

        Args:
            keep_last_n: Number of backups to retain per stage.
            stage: If set, only prune within this stage. If None,
                prune each auto stage independently — manual backups
                are never touched unless explicitly targeted via
                ``stage='manual'``.
            dry_run: Default True. When True, returns the
                would-delete / would-keep lists without touching
                disk.

        Returns:
            Dict with the deletion plan or results. Lists contain the
            same entry shape as ``list_backups``.
        """
        if keep_last_n < 0:
            raise ValueError("keep_last_n must be non-negative")
        if stage is not None and stage not in _ALL_STAGE_NAMES:
            raise ValueError(
                f"Unknown stage: {stage!r}. "
                f"Must be one of: {', '.join(sorted(_ALL_STAGE_NAMES))}"
            )

        # Catastrophic-footgun guard. ``keep_last_n=0`` on the
        # manual stage with ``dry_run=False`` deletes every
        # human-marked backup the user has ever made, including
        # "pre-tax-filing" / "pre-irreplaceable-thing" labels they
        # explicitly preserved. Manual backups have unlimited
        # retention BY DESIGN — this is the project's seatbelt
        # against data loss. Refuse to wipe them all in one call;
        # require the caller to step through deliberately (small
        # keep_last_n + a label filter, or use ``dry_run=True`` to
        # see the plan first). Auto-stage zero-retention is still
        # allowed because those have policy-driven retention and
        # the user can always rebuild them.
        if (
            stage == _MANUAL_STAGE_NAME
            and keep_last_n == 0
            and not dry_run
        ):
            raise ValueError(
                "Refusing to delete every manual backup. Manual "
                "backups have unlimited retention by design — they "
                "include human-marked snapshots like "
                "'pre-tax-filing' the user explicitly preserved. "
                "Use dry_run=True to review the plan, or pass a "
                "non-zero keep_last_n (e.g. keep_last_n=5 to keep "
                "the 5 most recent manual backups)."
            )

        all_backups = self.list_backups()
        by_stage: dict[str, list[dict]] = {}
        for entry in all_backups:
            by_stage.setdefault(entry["stage"], []).append(entry)

        # Which stages are in scope for this call?
        if stage is not None:
            target_stages = {stage}
        else:
            # Default: only auto stages. Manual is never auto-pruned
            # — the user must opt in via explicit stage="manual".
            target_stages = set(_AUTO_STAGE_NAMES)

        would_delete: list[dict] = []
        would_keep: list[dict] = []
        for stage_name, entries in by_stage.items():
            if stage_name not in target_stages:
                # Stage not targeted: all of its backups are retained
                # (but not reported in "would_keep" either — the
                # response is about what was actively considered).
                continue
            # entries are already newest-first thanks to list_backups
            keep = entries[:keep_last_n]
            drop = entries[keep_last_n:]
            would_keep.extend(keep)
            would_delete.extend(drop)

        # Sort would_keep by stage-then-timestamp-desc so a multi-
        # stage prune groups sessions/weeklies/monthlies together
        # (newest-first within each stage). Pre-fix it was just
        # timestamp-desc, which interleaved stages — readable for
        # single-stage prunes, awkward for the ``stage=None`` (all
        # auto stages) case. Two-pass stable sort: timestamp-desc
        # first so the within-stage order is newest-first, then
        # by stage to group.
        would_keep.sort(key=lambda e: e["timestamp"], reverse=True)
        would_keep.sort(key=lambda e: e["stage"])
        would_delete.sort(key=lambda e: e["timestamp"], reverse=True)

        if dry_run:
            return {
                "dry_run": True,
                "would_delete": would_delete,
                "would_keep": would_keep,
            }

        # Actually delete. Swallow per-file failures with a log so one
        # missing file doesn't abort the whole prune.
        deleted: list[dict] = []
        kept: list[dict] = list(would_keep)
        for entry in would_delete:
            p = Path(entry["path"])
            try:
                p.unlink()
                deleted.append(entry)
            except OSError as e:
                debug_logger.warning(
                    f"Could not delete backup {p}: {e}"
                )
        return {
            "dry_run": False,
            "deleted": deleted,
            "kept": kept,
        }

    # ── Auto-backup driver (called from @audit_log) ───────────────

    def _maybe_auto_backup(self) -> None:
        """Called once per process, before the first write, from the
        audit decorator. Creates a backup tagged with the highest-
        priority due stage (if any stages are due) and prunes each
        auto stage down to its ``keep_last_n``.

        Silently returns on any failure: an auto-backup that errors
        must never fail the user's write. The audit debug log records
        the reason.

        This method is safe to call multiple times within a process —
        ``_backup_checked_in_process`` gates it — but the audit
        decorator already enforces that contract at its layer.
        """
        # Serialize the gate so concurrent first-writes can't both
        # pass the check before either flips the flag. Without the
        # lock, two threads racing on the very first write would each
        # call ``create_backup`` — and with file-level naming they'd
        # collide on the same path.
        with self._backup_check_lock:
            if self._backup_checked_in_process:
                return
            # Flag BEFORE running so a raise here won't cause the
            # audit hook to retry on every subsequent write of the
            # process.
            self._backup_checked_in_process = True

        backups_dir = self._backups_dir()
        now = _now_utc()
        try:
            state = _read_state(backups_dir)

            # Identify due stages. Process in priority order (monthly
            # > weekly > session) so the first "due" stage becomes the
            # filename tag.
            due_stages: list[_BackupStage] = []
            for s in _AUTO_STAGES:
                last = state.get(s.name)
                if last is None or (now - last) >= s.interval:
                    due_stages.append(s)

            if not due_stages:
                # Nothing was due, so this isn't an "attempt" we need
                # to record — skipping is the expected outcome when
                # the chain is healthy and recent.
                return

            # Take ONE backup tagged with the highest-priority due
            # stage. Advance every due stage's timestamp so multiple
            # stages don't each trigger their own separate backup.
            highest = due_stages[0]
            self.create_backup(stage=highest.name, label=None)
            new_state = dict(state)
            for s in due_stages:
                new_state[s.name] = now
            _write_state(backups_dir, new_state)

            # Prune each auto stage to its keep_last_n.
            self._prune_auto_stages()

            # Record success so get_book_summary can surface a green
            # "auto-backup ran N minutes ago" signal — and so a later
            # failure becomes visible against the prior baseline.
            try:
                _write_attempt_status(backups_dir, "ok", None, now)
            except Exception as e:
                debug_logger.warning(
                    f"Could not record auto-backup success status: {e}"
                )
        except Exception as e:
            # Failure path: the user's write is still allowed to
            # proceed, but the bookkeeper needs to find out — pre-fix,
            # OSError-on-disk-full was silently swallowed for weeks.
            # We persist the failure so get_book_summary's Warnings
            # section can surface it on the next read.
            debug_logger.warning(f"Auto-backup skipped: {e}")
            try:
                _write_attempt_status(
                    backups_dir, "failed", str(e), now,
                )
            except Exception as write_err:
                debug_logger.warning(
                    f"Could not record auto-backup failure status: "
                    f"{write_err}"
                )

    def get_backup_health(self) -> dict:
        """Return a small dict describing the auto-backup chain's
        recent state. Read from disk on demand — does not require an
        open book session.

        Returns:
            ``{
                "last_attempt": {"status", "reason", "at"} | None,
                "newest_backup_at": datetime | None,
                "newest_backup_age_days": int | None,
            }``
        """
        backups_dir = self._backups_dir()
        attempt = _read_attempt_status(backups_dir)
        try:
            entries = self.list_backups()
        except Exception:
            entries = []
        newest_at: datetime | None = None
        newest_age_days: int | None = None
        if entries:
            # ``list_backups`` returns newest-first.
            try:
                ts_iso = entries[0]["timestamp"]
                newest_at = datetime.fromisoformat(ts_iso)
                if newest_at.tzinfo is None:
                    newest_at = newest_at.replace(tzinfo=timezone.utc)
                newest_age_days = (_now_utc() - newest_at).days
            except (KeyError, TypeError, ValueError):
                pass
        return {
            "last_attempt": attempt,
            "newest_backup_at": newest_at,
            "newest_backup_age_days": newest_age_days,
        }

    def _prune_auto_stages(self) -> None:
        """Internal: prune every auto stage to its configured
        ``keep_last_n``. Swallows errors per-file. Manual backups are
        untouched.
        """
        all_backups = self.list_backups()
        by_stage: dict[str, list[dict]] = {}
        for entry in all_backups:
            by_stage.setdefault(entry["stage"], []).append(entry)

        for stage in _AUTO_STAGES:
            entries = by_stage.get(stage.name, [])
            for old_entry in entries[stage.keep_last_n:]:
                try:
                    Path(old_entry["path"]).unlink()
                except OSError as e:
                    debug_logger.warning(
                        f"Prune skipped {old_entry['path']}: {e}"
                    )
