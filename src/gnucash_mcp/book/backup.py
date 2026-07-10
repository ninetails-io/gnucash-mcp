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
import os
import re
import shlex
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import piecash

from gnucash_mcp.logging_config import redact_paths

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

    Microsecond resolution: at second resolution two same-second
    backups share a filename, and SQLite's backup() truncates an
    existing dest — silently overwriting the first snapshot. The
    Path.exists() check in create_backup is the second defense.
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
    """Human-readable age string — 'just now', '3 days ago', etc.

    Rounds to nearest unit, not floor: 59m30s reads as "1 hour ago"
    via the next-bucket promotion rather than the dishonest
    "59 minutes ago".
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
#
# State and attempt files are PER-BOOK (keyed by the book's filename
# stem). Under a shared GNUCASH_LOG_DIR, every book resolves the same
# backups/ directory — a single shared .state.json meant book A's
# auto-backup advanced every stage timestamp and book B's
# _maybe_auto_backup never found anything due: B ran with no
# auto-backup protection at all. The legacy unscoped .state.json is
# read as a fallback only when GNUCASH_LOG_DIR is unset (the default
# {book}.mcp/ layout is per-book by construction, so the legacy file
# can only belong to this book); under an override it is ignored —
# treating the state as empty just means one extra backup, which is
# the safe direction.


def _state_path(backups_dir: Path, stem: str) -> Path:
    return backups_dir / f".state-{stem}.json"


def _read_state(backups_dir: Path, stem: str) -> dict[str, datetime]:
    """Read ``last_backup_by_stage`` as a dict of stage → UTC datetime.

    Missing file, malformed JSON, or unparseable timestamps all degrade
    to an empty state — auto-backup reacts by treating every stage as
    due, which is the safe default.
    """
    path = _state_path(backups_dir, stem)
    if not path.exists() and not os.environ.get("GNUCASH_LOG_DIR"):
        # Pre-scoping layout: per-book dir, so the unscoped file is
        # unambiguously this book's.
        path = backups_dir / ".state.json"
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


def _read_book_hash(backups_dir: Path, stem: str) -> str | None:
    """The ``book_sha256`` recorded by the last auto-backup, or None
    (pre-upgrade state file, or none recorded). Same file-resolution
    rules as ``_read_state``."""
    path = _state_path(backups_dir, stem)
    if not path.exists() and not os.environ.get("GNUCASH_LOG_DIR"):
        path = backups_dir / ".state.json"
    try:
        with path.open() as f:
            return json.load(f).get("book_sha256")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_state(
    backups_dir: Path, stem: str, state: dict[str, datetime],
    book_sha256: str | None = None,
) -> None:
    """Persist ``last_backup_by_stage`` (and, when provided, the
    sha256 of the BOOK file at backup time — the identical-content
    skip's comparison anchor) to disk."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "last_backup_by_stage": {
            stage: ts.astimezone(timezone.utc).isoformat()
            for stage, ts in state.items()
        },
    }
    if book_sha256 is not None:
        payload["book_sha256"] = book_sha256
    path = _state_path(backups_dir, stem)
    # Write via a temp + rename so a partial write never leaves a
    # corrupted state file.
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(path)


# ── Auto-backup attempt status (separate file) ───────────────────────
#
# Decoupled from .state.json: the per-stage file advances only on
# success, so it can't say "we tried 6 hours ago and failed."
# .last_attempt.json captures every attempt so get_book_summary can
# surface chain breaks otherwise buried in debug logs.


def _attempt_path(backups_dir: Path) -> Path:
    return backups_dir / ".last_attempt.json"


def _attempt_path_scoped(backups_dir: Path, stem: str) -> Path:
    return backups_dir / f".last_attempt-{stem}.json"


def _read_attempt_status(backups_dir: Path, stem: str) -> dict | None:
    """Return ``{status, reason, at}`` for the most recent auto-backup
    attempt, or None if no attempt has ever been recorded.

    ``status`` is ``"ok"`` or ``"failed"``. ``reason`` is the
    exception string for failures (None on success). ``at`` is a
    tz-aware UTC datetime. Per-book scoped like the state file (see
    the state-file comment above); the unscoped legacy file is read
    only when GNUCASH_LOG_DIR is unset.
    """
    path = _attempt_path_scoped(backups_dir, stem)
    if not path.exists() and not os.environ.get("GNUCASH_LOG_DIR"):
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
    stem: str,
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
    path = _attempt_path_scoped(backups_dir, stem)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(path)


# ── Filename inspection ──────────────────────────────────────────────


def _parse_backup_filename(
    path: Path,
) -> tuple[datetime, str, str | None, str] | None:
    """Return ``(timestamp, stage, label, stem)`` if the filename looks
    like a backup, else None. Used by ``list_backups`` and
    ``prune_backups`` to read metadata from filenames without
    consulting any other state; the stem lets ``list_backups`` scope
    to its own book when several books share a backups directory.
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
    return ts, stage, m.group("label"), m.group("stem")


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

        Resolved via the shared ``resolve_mcp_dir`` helper so the
        ``GNUCASH_LOG_DIR`` env override + parent-dir permission
        check apply to backup storage the same way they apply to
        audit / debug logs. Consistent location means users
        backing up their GnuCash folder (Time Machine, etc.)
        pick up the snapshots automatically.
        """
        from gnucash_mcp.logging_config import resolve_mcp_dir
        return resolve_mcp_dir(self.book_path) / "backups"

    def _resolve_backup_path(self, entry: dict) -> Path:
        """Absolute on-disk path for a backup listing entry.

        Pruning must never trust ``entry["path"]`` directly: under
        ``GNUCASH_REDACT_PATHS=1`` ``list_backups`` redacts that field
        to the bare basename, so ``Path(entry["path"]).unlink()``
        would resolve against the process CWD — a silent no-op, or a
        delete of an unrelated same-named file there. Reconstruct from
        the backups dir + the filename so a prune only ever deletes a
        file that actually lives in the backups directory, redaction on
        or off. (``list_backups`` lists only direct children of the
        backups dir, so the basename round-trips cleanly.)
        """
        return self._backups_dir() / Path(entry["path"]).name

    def _current_book_hash(self) -> str | None:
        """sha256 of the book file's bytes right now, or None on any
        read error (callers fail toward taking a backup)."""
        import hashlib

        try:
            return hashlib.sha256(
                Path(self.book_path).read_bytes()
            ).hexdigest()
        except OSError as e:
            debug_logger.warning(f"Book hash failed: {e}")
            return None

    def _book_unchanged_since_last_backup(
        self, current_hash: str | None,
    ) -> bool:
        """True iff the book's bytes match the hash the last
        auto-backup recorded AND at least one snapshot still exists.

        The comparison anchor is the BOOK file's own hash at backup
        time (recorded in the state file), not the snapshot's bytes —
        SQLite's online-backup API legitimately produces a different
        page layout, so book-vs-snapshot comparison always differs.
        Manual backups don't update the anchor; the cost is one
        redundant auto snapshot after a manual backup, the safe
        direction. Missing anchor (pre-upgrade state) reads as
        "changed".
        """
        if current_hash is None:
            return False
        try:
            recorded = _read_book_hash(self._backups_dir(),
                                       self.book_path.stem)
            if recorded is None or recorded != current_hash:
                return False
            return bool(self.list_backups())
        except Exception as e:  # noqa: BLE001 — fail toward backing up
            debug_logger.warning(
                f"Backup identity check failed (backing up anyway): {e}"
            )
            return False

    # ── Core primitive: create a backup ──────────────────────────

    def create_backup(
        self,
        *,
        stage: str = _MANUAL_STAGE_NAME,
        label: str | None = None,
    ) -> dict:
        """Write a fresh snapshot via SQLite's online backup API and
        verify it with PRAGMA integrity_check.

        Args:
            stage: ``session`` / ``weekly`` / ``monthly`` (policy-
                driven) or ``manual`` (user-invoked, unlimited
                retention).
            label: Optional marker (sanitized to ``[A-Za-z0-9_-]``)
                appended to the filename — "pre-big-reorg" style.

        Returns:
            ``{status, stage, path, size_bytes, integrity,
            restore_hint}``.

        Raises:
            ValueError: Unknown stage.
            RuntimeError: Integrity check failed (the bad file is
                deleted before raising).
            OSError: Backup directory not creatable/writable.
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

        # Refuse to overwrite — sqlite3.connect(path) would happily
        # truncate an existing snapshot (see _format_ts).
        if backup_path.exists():
            raise RuntimeError(
                f"Backup path already exists, refusing to overwrite: "
                f"{backup_path}. This indicates a clock-resolution "
                f"collision; retry."
            )

        # Source opened readonly (no write lock on the live book);
        # SQLite's backup() copies pages without blocking readers.
        with self.open(readonly=True) as book:
            source_conn = book.session.connection().connection
            dest_conn = sqlite3.connect(str(backup_path))
            try:
                source_conn.backup(dest_conn)
            except Exception:
                # A mid-copy failure leaves a partial file that
                # list_backups would show as a "valid backup" (the
                # integrity check only runs on success). Unlink it
                # and propagate.
                dest_conn.close()
                try:
                    backup_path.unlink()
                except OSError:
                    pass
                raise
            finally:
                # Idempotent — close() is a no-op on a closed conn.
                dest_conn.close()

        # Verify before declaring success; a failed check deletes
        # the file so no broken snapshot masquerades as recovery.
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

        # Path-bearing fields route through redact_paths (opt-in
        # via GNUCASH_REDACT_PATHS). Paths are shell-quoted —
        # spaces/metachars break the command, and an unquoted
        # f-string is a latent injection if a future path component
        # is user-influenced.
        restore_hint = (
            "Restore by stopping the server, then: "
            f"mv {shlex.quote(str(self.book_path))} "
            f"{shlex.quote(str(self.book_path) + '.broken')} && "
            f"cp {shlex.quote(str(backup_path))} "
            f"{shlex.quote(str(self.book_path))}"
        )
        return {
            "status": "created",
            "stage": stage,
            "path": redact_paths(str(backup_path)),
            "size_bytes": size_bytes,
            "integrity": integrity,
            "restore_hint": redact_paths(restore_hint),
        }

    # ── Listing ──────────────────────────────────────────────────

    def list_backups(self) -> list[dict]:
        """Return every recognized backup file OF THIS BOOK under the
        backups dir, newest first. Each entry carries ``stage``,
        ``timestamp`` (ISO UTC), ``age`` (human-readable),
        ``size_bytes``, ``label``, and ``path``.

        Scoped by filename stem: several books can share a backups
        directory (GNUCASH_LOG_DIR), and every consumer of this
        listing — retention slots in ``prune_backups`` /
        ``_prune_auto_stages``, the newest-backup health signal —
        must see only this book's snapshots. An unscoped listing let
        one book's prune delete another book's backups.
        """
        backups_dir = self._backups_dir()
        if not backups_dir.exists():
            return []

        # Case-INSENSITIVE stem match, consistent with switch_book
        # matching and the parse-time uniqueness check: on macOS a
        # user can relaunch with ledger.gnucash after backups were
        # written as Ledger-<ts>-... (same file — resolve() doesn't
        # canonicalize case); an exact compare would make every
        # existing backup invisible to listing, pruning, and the
        # dashboard's backup-health signal.
        own_stem = self.book_path.stem.lower()
        entries: list[dict] = []
        now = _now_utc()
        for path in backups_dir.iterdir():
            parsed = _parse_backup_filename(path)
            if parsed is None:
                continue
            ts, stage, label, stem = parsed
            if stem.lower() != own_stem:
                continue
            try:
                size = path.stat().st_size
            except OSError as e:
                # Usually a broken symlink — log rather than drop
                # silently, so the issue is findable.
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
                # Opt-in path redaction.
                "path": redact_paths(str(path)),
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
        # see the plan first). Per-stage auto zero-retention is
        # still allowed because those have policy-driven retention
        # and the user can always rebuild them; the symmetric
        # guard below catches the "all auto stages at once" case
        # which is a different shape of footgun (the user typed
        # ``keep_last_n=0`` to free disk space, not realizing the
        # default scope is every auto stage).
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

        # Symmetric footgun guard for the auto stages.
        # ``prune_backups(keep_last_n=0)`` without an explicit
        # ``stage`` deletes every session / weekly / monthly
        # backup in one call. The user typed it intending "free up
        # disk space"; the intent was almost certainly per-stage,
        # not "wipe all auto backups." Auto backups rebuild over
        # time (sessions on next write, weekly on next Monday,
        # monthly on next 1st), but until then the recoverability
        # window is gone — and the user has no way to recover
        # backups they didn't realize they were deleting. Mirror
        # the manual-stage guard: require the caller to opt in
        # explicitly by naming the stage.
        if (
            stage is None
            and keep_last_n == 0
            and not dry_run
        ):
            raise ValueError(
                "Refusing to delete every auto backup at once. "
                "``prune_backups(keep_last_n=0)`` without an "
                "explicit ``stage`` wipes session, weekly, AND "
                "monthly auto backups in a single call — the "
                "recoverability window is gone until each stage's "
                "next scheduled rebuild. Use dry_run=True to "
                "review the plan, pass an explicit ``stage`` "
                "(e.g. ``stage='session'``) to scope the delete, "
                "or pass a non-zero ``keep_last_n``."
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

        # Group by stage, newest-first within each (two-pass stable
        # sort) — plain timestamp-desc interleaves stages in the
        # all-auto-stages case.
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
            p = self._resolve_backup_path(entry)
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
        audit decorator: backs up under the highest-priority due
        stage (if any) and prunes each auto stage to its
        ``keep_last_n``.

        Silently returns on any failure — an auto-backup error must
        never fail the user's write; the debug log records why.
        Safe to call repeatedly (``_backup_checked_in_process``
        gates it).
        """
        # Lock so concurrent first-writes can't both pass the gate
        # and collide on the same backup filename.
        with self._backup_check_lock:
            if self._backup_checked_in_process:
                return
            # Flag BEFORE running so a raise here won't cause the
            # audit hook to retry on every subsequent write of the
            # process.
            self._backup_checked_in_process = True

        backups_dir = self._backups_dir()
        stem = self.book_path.stem
        now = _now_utc()
        try:
            state = _read_state(backups_dir, stem)

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

            # Content-aware skip (bookkeeper finding F2): the audit
            # decorator fires this hook before the tool body runs, so
            # a write that later fails validation still lands here —
            # and duplicated a multi-MB snapshot for a no-op. True
            # validate-then-open would need a second book open per
            # write (the perf sin the project eliminated); instead,
            # when the book's bytes match the newest existing
            # snapshot, the protection a new backup would add already
            # exists — advance the schedule without writing a
            # duplicate file. Comparison failure of any kind falls
            # through to a normal backup (today's behavior — the
            # fail-safe direction).
            current_hash = self._current_book_hash()
            if self._book_unchanged_since_last_backup(current_hash):
                debug_logger.info(
                    "Auto-backup skipped: book content unchanged "
                    "since the last snapshot; stages advanced."
                )
            else:
                # Take ONE backup tagged with the highest-priority
                # due stage. Advance every due stage's timestamp so
                # multiple stages don't each trigger their own
                # separate backup.
                highest = due_stages[0]
                self.create_backup(stage=highest.name, label=None)
            new_state = dict(state)
            for s in due_stages:
                new_state[s.name] = now
            _write_state(backups_dir, stem, new_state,
                         book_sha256=current_hash)

            # Prune each auto stage to its keep_last_n.
            self._prune_auto_stages()

            # Record success so get_book_summary can surface a green
            # "auto-backup ran N minutes ago" signal — and so a later
            # failure becomes visible against the prior baseline.
            try:
                _write_attempt_status(backups_dir, stem, "ok", None, now)
            except Exception as e:
                debug_logger.warning(
                    f"Could not record auto-backup success status: {e}"
                )
        except Exception as e:
            # Failure path: the user's write is still allowed to
            # proceed, but the user needs to find out — a silently
            # swallowed OSError-on-disk-full leaves no recovery
            # option the day it matters.
            # We persist the failure so get_book_summary's Warnings
            # section can surface it on the next read.
            debug_logger.warning(f"Auto-backup skipped: {e}")
            try:
                _write_attempt_status(
                    backups_dir, stem, "failed", str(e), now,
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
        attempt = _read_attempt_status(backups_dir, self.book_path.stem)
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
                    self._resolve_backup_path(old_entry).unlink()
                except OSError as e:
                    debug_logger.warning(
                        f"Prune skipped {old_entry['path']}: {e}"
                    )
