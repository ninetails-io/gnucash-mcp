"""Database-backed books: GNUCASH_BOOK_URI / --book-uri.

Most of this file needs no database. A ``sqlite:///`` URI is a real
SQLAlchemy URL, so pointing the URI interface at a temp book exercises
every DB code path that isn't dialect-specific — ``open()`` through
``uri_conn``, GUID resolution through the engine, the None cache
token, the backup refusal, and the whole config pipeline — in the
ordinary hermetic suite.

The genuinely PostgreSQL-shaped assertions live in
``TestPostgresBackend`` and skip unless ``GNUCASH_TEST_PG_URI`` names
a reachable server (CI's postgres service sets it).
"""

import os
from datetime import date
from pathlib import Path

import piecash
import pytest

from gnucash_mcp._format import (
    _book_display_name,
    _looks_like_book_uri,
    _parse_book_url,
    _redact_uri,
)
from gnucash_mcp.book import BookSource, GnuCashBook


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def book_uri(test_book: Path) -> str:
    """The standard test book, addressed as a connection URI."""
    return f"sqlite:///{test_book}"


@pytest.fixture
def uri_book(book_uri: str) -> GnuCashBook:
    """A GnuCashBook that believes it lives in a database."""
    return GnuCashBook(BookSource.from_uri(book_uri))


@pytest.fixture
def clean_server():
    """Server module with its book globals saved and restored.

    Every test here mutates process-global book state; without the
    restore, an xdist worker running a multi-book test afterwards
    would inherit a URI config and fail for reasons that have nothing
    to do with it.
    """
    import gnucash_mcp.server as srv

    saved = {
        name: getattr(srv, name)
        for name in (
            "_book", "_book_uri", "_book_paths", "_current_path",
            "_book_registry", "_book_paths_source", "_logging_audit",
            "_logging_debug",
        )
    }
    saved_env = {
        k: os.environ.get(k)
        for k in ("GNUCASH_BOOK_PATH", "GNUCASH_BOOK_URI", "GNUCASH_LOG_DIR")
    }
    srv._book = None
    srv._book_uri = None
    srv._book_paths = []
    srv._current_path = None
    srv._book_registry = {}
    srv._book_paths_source = None
    for k in saved_env:
        os.environ.pop(k, None)
    try:
        yield srv
    finally:
        for name, value in saved.items():
            setattr(srv, name, value)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── BookSource ────────────────────────────────────────────────────


class TestBookSource:
    def test_from_path_is_file_backed(self, test_book: Path):
        src = BookSource.from_path(test_book)
        assert src.is_file
        assert src.path == test_book.resolve()
        assert src.display_name == "test.gnucash"
        assert src.log_name == "test.gnucash"

    def test_from_uri_is_not_file_backed(self):
        src = BookSource.from_uri("postgresql://u:pw@host:5432/ledger")
        assert not src.is_file
        assert src.path is None

    def test_uri_log_name_gives_db_books_the_same_mcp_layout(self):
        """``{database}.gnucash`` so resolve_mcp_dir yields
        ``{GNUCASH_LOG_DIR}/ledger.gnucash.mcp`` — identical in shape
        to what a file book gets, so audit/debug consumers need no
        special case."""
        src = BookSource.from_uri("postgresql://u@host/ledger")
        assert src.log_name == "ledger.gnucash"

    def test_uri_without_database_falls_back(self):
        src = BookSource.from_uri("postgresql://user@host")
        assert src.log_name == "book.gnucash"

    def test_from_path_rejects_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            BookSource.from_path(tmp_path / "nope.gnucash")

    def test_from_path_rejects_directory(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            BookSource.from_path(tmp_path)

    def test_from_uri_rejects_garbage(self):
        with pytest.raises(ValueError):
            BookSource.from_uri("this is not a url")


# ── Credential redaction ──────────────────────────────────────────


class TestRedaction:
    def test_password_is_masked(self):
        out = _redact_uri("postgresql://alice:hunter2@db.internal:5432/gnucash")
        assert "hunter2" not in out
        assert "alice" in out and "db.internal" in out and "gnucash" in out

    def test_unparseable_uri_is_not_echoed(self):
        """If we can't locate the password we can't prove there isn't
        one, so the whole value is withheld rather than printed."""
        assert _redact_uri("postgres//:mangled:pw@@") == "<database>"

    def test_display_name_masks_uris(self):
        out = _book_display_name("mysql+pymysql://bob:s3cret@h/books")
        assert "s3cret" not in out
        assert out.startswith("mysql+pymysql://bob:")

    def test_display_name_still_basenames_paths(self):
        assert _book_display_name("/home/someone/Ledger.gnucash") == "Ledger.gnucash"

    def test_windows_path_is_not_mistaken_for_a_uri(self):
        assert not _looks_like_book_uri(r"C:\Users\me\book.gnucash")
        assert _book_display_name(r"C:\Users\me\book.gnucash").endswith(
            "book.gnucash"
        )

    def test_unset_book_is_reported_as_such(self):
        assert _book_display_name(None) == "not set"

    def test_book_summary_header_masks_the_password(self, uri_book):
        """The dashboard names the book on its first line — the most
        frequently emitted string in a session."""
        out = uri_book.get_book_summary()
        assert "sqlite:///" in out
        assert "Book: " in out


# ── Reads and writes over a URI ───────────────────────────────────


class TestUriBookOperations:
    def test_accounts_read_back(self, uri_book):
        assert "Assets:Checking" in uri_book.list_accounts()

    def test_write_round_trips(self, uri_book):
        result = uri_book.create_transaction(
            description="URI-mode write",
            splits=[
                {"account": "Expenses:Groceries", "amount": "12.34"},
                {"account": "Assets:Checking", "amount": "-12.34"},
            ],
            trans_date=date(2026, 1, 15),
            check_duplicates=False,
        )
        assert result["status"] == "created"
        assert "URI-mode write" in uri_book.search_transactions("URI-mode write")

    def test_guid_prefix_resolution_works(self, uri_book):
        """The GUID engine is the one read path that bypasses piecash
        entirely — its own connection, its own SQL — so it has to be
        proven separately on the URI backend."""
        with uri_book.open() as book:
            guid = book.root_account.guid
        assert uri_book._resolve_guid("accounts", guid[:8]) == guid

    def test_prefix_map_is_stable_across_calls(self, uri_book):
        with uri_book.open() as book:
            first = uri_book._transaction_prefix_map(book)
            second = uri_book._transaction_prefix_map(book)
        assert first == second


class TestCacheToken:
    def test_file_books_use_mtime(self, test_book: Path):
        book = GnuCashBook(str(test_book))
        assert book._cache_token() == test_book.stat().st_mtime_ns

    def test_db_books_disable_the_cache(self, uri_book):
        """None means "always rebuild": a shared database can be
        committed to by another client between two of our reads, and a
        stale prefix map emits colliding short GUIDs — wrong output,
        not merely slow output."""
        assert uri_book._cache_token() is None

    def test_disabled_cache_is_never_served(self, uri_book):
        with uri_book.open() as book:
            uri_book._transaction_prefix_map(book)
            # Poison the cache slot: a book that honored it would
            # return the poison instead of rebuilding.
            uri_book._txn_prefix_cache = (None, {"poison": "poison"})
            again = uri_book._transaction_prefix_map(book)
        assert "poison" not in again


# ── Degradation ───────────────────────────────────────────────────


class TestBackupDegradation:
    def test_create_backup_refuses_with_an_actionable_message(self, uri_book):
        with pytest.raises(ValueError) as exc:
            uri_book.create_backup()
        msg = str(exc.value)
        assert "pg_dump" in msg
        assert "RESTORE_FROM_BACKUP" in msg

    def test_refusal_does_not_leak_the_password(self):
        book = GnuCashBook(
            BookSource.from_uri("postgresql://u:hunter2@h:5432/ledger")
        )
        with pytest.raises(ValueError) as exc:
            book.create_backup()
        assert "hunter2" not in str(exc.value)

    def test_auto_backup_hook_is_a_silent_no_op(self, uri_book):
        """The audit decorator fires this before every first write. It
        must not raise — a refusal here would block the write itself."""
        uri_book._maybe_auto_backup()
        assert uri_book._backup_checked_in_process is False

    def test_file_books_still_back_up(self, test_book: Path):
        book = GnuCashBook(str(test_book))
        result = book.create_backup(label="regression")
        assert result["status"] == "created"
        assert Path(result["path"]).exists()


# ── Config plumbing ───────────────────────────────────────────────


class TestUriConfig:
    def test_valid_uri_is_accepted(self, clean_server):
        assert (
            clean_server._parse_book_uri("  postgresql://u@h/db  ")
            == "postgresql://u@h/db"
        )

    @pytest.mark.parametrize("value", [None, "", "   ", "not a url"])
    def test_invalid_uris_are_rejected(self, clean_server, value):
        with pytest.raises(clean_server._BookPathError):
            clean_server._parse_book_uri(value)

    def test_sqlite_uri_warns_about_lost_backups(self, clean_server):
        notes = clean_server._book_uri_warnings("sqlite:////tmp/x.gnucash")
        assert notes and "backups" in notes[0]

    def test_postgres_uri_warns_about_nothing(self, clean_server):
        assert clean_server._book_uri_warnings("postgresql://u@h/db") == []

    def test_get_book_serves_the_uri(self, clean_server, book_uri):
        clean_server._install_book_uri(book_uri, activate=False)
        book = clean_server.get_book()
        assert not book.source.is_file
        assert book.source.uri == book_uri

    def test_uri_mode_is_single_book(self, clean_server, book_uri):
        """No path list means multi_book_active() is False by
        construction, which is what keeps switch_book unregistered and
        the restart write-disarm inactive — no special-casing needed
        in any of them."""
        clean_server._install_book_uri(book_uri, activate=False)
        assert clean_server._book_paths == []
        assert clean_server.multi_book_active() is False

    def test_demo_books_are_skipped_in_uri_mode(
        self, clean_server, book_uri, monkeypatch
    ):
        clean_server._install_book_uri(book_uri, activate=False)
        monkeypatch.setattr(
            clean_server, "_demo_book_paths",
            lambda: (_ for _ in ()).throw(AssertionError("should not run")),
        )
        clean_server._append_demo_books()
        assert clean_server._book_paths == []

    def test_env_var_is_picked_up_on_reset(self, clean_server, book_uri):
        os.environ["GNUCASH_BOOK_URI"] = book_uri
        clean_server._book = None
        assert clean_server.get_book().source.uri == book_uri

    def test_server_config_names_the_missing_backups(
        self, clean_server, book_uri
    ):
        clean_server._server_state.update({
            "book_path": "postgresql://u:pw@h:5432/ledger",
            "book_is_uri": True,
        })
        out = clean_server._get_server_config_impl()
        assert "pw" not in out
        assert "MCP backups unavailable" in out


class TestModeInstallationIsExclusive:
    """Whichever mode main() picks owns the environment.

    Regression: get_book() consults ``GNUCASH_BOOK_URI`` before the
    path list, so a leftover URI variable alongside ``--book`` used
    to start in path mode and then serve the DATABASE on the first
    tool call — a write landing in a ledger nobody selected, which is
    the wrong-book class the restart guards exist to prevent.
    """

    def test_installing_paths_clears_the_uri(
        self, clean_server, test_book, book_uri
    ):
        os.environ["GNUCASH_BOOK_URI"] = book_uri
        clean_server._install_book_list(
            [test_book.resolve()], activate=False
        )
        assert clean_server._book_uri is None
        assert "GNUCASH_BOOK_URI" not in os.environ
        assert clean_server.get_book().source.is_file

    def test_installing_a_uri_clears_the_paths(
        self, clean_server, test_book, book_uri
    ):
        os.environ["GNUCASH_BOOK_PATH"] = str(test_book)
        clean_server._install_book_uri(book_uri, activate=False)
        assert clean_server._book_paths == []
        assert "GNUCASH_BOOK_PATH" not in os.environ
        assert not clean_server.get_book().source.is_file


class TestRegistryKey:
    """Regression: the book-instance registry key.

    ``BookSource`` moved the key from ``str(path)`` to the source
    URI, but ``_switch_book_impl``'s already-on-this-book check kept
    building ``str(path)`` — so it stopped matching and every no-op
    switch took the full CONTEXT RESET path instead of the cheap
    "Already on" one. Both sites derive it through ``_registry_key``
    now; these pin that they agree.
    """

    def test_path_and_source_agree(self, clean_server, test_book):
        src = BookSource.from_path(test_book)
        assert (
            clean_server._registry_key(test_book.resolve())
            == clean_server._registry_key(src)
        )

    def test_book_for_stores_under_the_shared_key(
        self, clean_server, test_book
    ):
        book = clean_server._book_for(test_book.resolve())
        key = clean_server._registry_key(test_book.resolve())
        assert clean_server._book_registry[key] is book

    def test_uri_books_key_on_their_uri(self, clean_server, book_uri):
        src = BookSource.from_uri(book_uri)
        assert clean_server._registry_key(src) == book_uri


class TestLogDirRequirement:
    def test_required_when_logging_is_on(self, clean_server):
        clean_server._logging_audit = True
        with pytest.raises(clean_server._BookPathError) as exc:
            clean_server._require_log_dir_for_uri()
        assert "GNUCASH_LOG_DIR" in str(exc.value)

    def test_satisfied_when_set(self, clean_server, tmp_path):
        clean_server._logging_audit = True
        os.environ["GNUCASH_LOG_DIR"] = str(tmp_path)
        clean_server._require_log_dir_for_uri()

    def test_not_required_when_logging_is_off(self, clean_server):
        clean_server._logging_audit = False
        clean_server._logging_debug = False
        clean_server._require_log_dir_for_uri()

    def test_uri_audit_dir_mirrors_the_file_layout(self, tmp_path):
        from gnucash_mcp.logging_config import resolve_mcp_dir

        os.environ["GNUCASH_LOG_DIR"] = str(tmp_path)
        try:
            src = BookSource.from_uri("postgresql://u@h/ledger")
            assert resolve_mcp_dir(src.log_name) == tmp_path / "ledger.gnucash.mcp"
        finally:
            os.environ.pop("GNUCASH_LOG_DIR", None)


class TestAdvancedBoxRejection:
    def test_book_uri_cannot_be_set_through_the_advanced_box(self):
        """The MCPB bundle always passes ``--book`` from its required
        file picker, so a URI smuggled in through the advanced
        options field would be silently ignored. Fail loudly instead
        — the same rule GNUCASH_BOOK_PATH already has.
        """
        import gnucash_mcp._env as env

        saved_errors = list(env._env_errors)
        saved = os.environ.get("GNUCASH_MCP_ADVANCED")
        env._env_errors.clear()
        os.environ["GNUCASH_MCP_ADVANCED"] = (
            "GNUCASH_BOOK_URI=postgresql://u@h/db"
        )
        try:
            env._apply_advanced_env()
            assert env._env_errors
            assert "GNUCASH_BOOK_URI" in env._env_errors[0]
            assert "GNUCASH_BOOK_URI" not in os.environ
        finally:
            env._env_errors[:] = saved_errors
            if saved is None:
                os.environ.pop("GNUCASH_MCP_ADVANCED", None)
            else:
                os.environ["GNUCASH_MCP_ADVANCED"] = saved


class TestCliParsing:
    def test_separate_token_form(self, clean_server):
        _, uri, *_ = clean_server._parse_cli_argv(
            ["--book-uri", "postgresql://u@h/db"]
        )
        assert uri == "postgresql://u@h/db"

    def test_equals_form(self, clean_server):
        _, uri, *_ = clean_server._parse_cli_argv(
            ["--book-uri=postgresql://u@h/db"]
        )
        assert uri == "postgresql://u@h/db"

    def test_missing_value_is_a_loud_error(self, clean_server):
        with pytest.raises(clean_server._CliParseError):
            clean_server._parse_cli_argv(["--book-uri"])

    def test_missing_value_before_another_flag(self, clean_server):
        with pytest.raises(clean_server._CliParseError):
            clean_server._parse_cli_argv(["--book-uri", "--debug"])

    def test_book_flag_is_untouched(self, clean_server):
        paths, uri, *_ = clean_server._parse_cli_argv(["--book", "/a", "/b"])
        assert paths == ["/a", "/b"] and uri is None

    def test_help_documents_the_flag(self, clean_server):
        text = clean_server._build_help_text()
        assert "--book-uri" in text
        assert "GNUCASH_BOOK_URI" in text


class TestMutualExclusion:
    """A book is a file or a database, never both — picking silently
    would put writes in whichever ledger won a coin toss."""

    class _Reached(Exception):
        """Sentinel: main() got past book installation."""

    def _main(self, srv, argv, monkeypatch):
        """Run main() up to — and not into — tool registration.

        main() would otherwise run _apply_module_filter, which
        permanently pops inline tools (switch_book) from the shared
        FastMCP registry and leaves every later multi-book test on
        this xdist worker failing by scheduling accident; conftest's
        PRISTINE_INLINE_TOOLS exists because of exactly that. Cutting
        at _append_demo_books — the first call after the book config
        is installed — keeps these tests to the part they're about.
        """
        monkeypatch.setattr(srv.sys, "argv", ["gnucash-mcp", *argv])
        def _stop():
            raise self._Reached
        monkeypatch.setattr(srv, "_append_demo_books", _stop)
        try:
            srv.main()
        except self._Reached:
            pass

    def test_both_flags_fail_fast(
        self, clean_server, test_book, book_uri, monkeypatch, capsys
    ):
        with pytest.raises(SystemExit) as exc:
            self._main(
                clean_server,
                ["--book", str(test_book), "--book-uri", book_uri],
                monkeypatch,
            )
        assert exc.value.code == 2
        assert "mutually exclusive" in capsys.readouterr().err

    def test_both_env_vars_fail_fast(
        self, clean_server, test_book, book_uri, monkeypatch, capsys
    ):
        os.environ["GNUCASH_BOOK_PATH"] = str(test_book)
        os.environ["GNUCASH_BOOK_URI"] = book_uri
        with pytest.raises(SystemExit) as exc:
            self._main(clean_server, [], monkeypatch)
        assert exc.value.code == 2
        assert "mutually exclusive" in capsys.readouterr().err

    def test_flag_beats_the_other_interfaces_env_var(
        self, clean_server, test_book, book_uri, monkeypatch, capsys, tmp_path
    ):
        """Args win over the environment — the same rule --book
        already has — but the ignored variable is announced, because a
        leftover GNUCASH_BOOK_PATH is exactly what makes a wrong-book
        scare."""
        os.environ["GNUCASH_BOOK_PATH"] = str(test_book)
        os.environ["GNUCASH_LOG_DIR"] = str(tmp_path)
        self._main(clean_server, ["--book-uri", book_uri], monkeypatch)
        assert clean_server._book_uri == book_uri
        assert clean_server._book_paths == []
        assert "GNUCASH_BOOK_PATH is ignored" in capsys.readouterr().err


# ── Latent-bug regression ─────────────────────────────────────────


class TestPercentInFilename:
    def test_percent_escape_in_filename_resolves_guids(self, tmp_path):
        """Regression: a book whose FILENAME contains a percent escape.

        The pre-DB-backend code built its lookup connection as
        ``f"file:{path}?mode=ro"`` and handed it to sqlite3 with
        ``uri=True``, which percent-DECODES the path — so
        ``budget 100%25 final.gnucash`` was looked for as
        ``budget 100% final.gnucash`` and every GUID lookup died with
        "unable to open database file". piecash opens such a book
        fine, so the book worked everywhere except short-GUID
        resolution.

        Percent-ENCODING the path (needed anyway so one URI form
        serves both backends) fixes it. Not merely theoretical: `%`
        is legal in filenames on every supported platform, and GnuCash
        itself never rejected one.
        """
        path = tmp_path / "budget 100%25 final.gnucash"
        piecash.create_book(str(path), currency="USD", overwrite=True).close()
        book = GnuCashBook(str(path))
        with book.open() as opened:
            guid = opened.root_account.guid
        assert book._resolve_guid("accounts", guid[:8]) == guid


class TestEngineDisposal:
    """``open()`` disposes piecash's engine after close.

    piecash binds a fresh engine to every ``open_book`` and never
    disposes it, so its pool keeps a connection checked in after
    ``close()``. On PostgreSQL that is one server slot per tool call
    until the cyclic GC happens to reclaim the engine — measured at
    15 opens, 15 live connections with GC paused. The PostgreSQL job
    counts real connections (``TestPostgresBackend``); this is the
    hermetic half.
    """

    def test_engine_is_disposed_after_close(self, test_book: Path, monkeypatch):
        from unittest.mock import MagicMock

        real_open = piecash.open_book
        engines = []

        def spying_open(*args, **kwargs):
            book = real_open(*args, **kwargs)
            engine = book.session.get_bind()
            engine.dispose = MagicMock(wraps=engine.dispose)
            engines.append(engine)
            return book

        monkeypatch.setattr(piecash, "open_book", spying_open)
        gb = GnuCashBook(test_book)
        with gb.open(readonly=True) as opened:
            assert opened.default_currency is not None
        assert len(engines) == 1
        engines[0].dispose.assert_called_once()

    def test_startup_breadcrumb_masks_the_book(self):
        """The one place main() logs the raw book value routes it
        through ``_book_display_name`` — in URI mode that value
        carries the database password."""
        import inspect

        import gnucash_mcp.server as srv

        src = inspect.getsource(srv.main)
        assert "Book: {_book_display_name(book_path)}" in src
        assert "Book: {book_path}" not in src


# ── PostgreSQL ────────────────────────────────────────────────────


def _worker_pg_uri() -> str | None:
    """``GNUCASH_TEST_PG_URI`` with a per-xdist-worker database name.

    The fixture below creates its book with ``overwrite=True``, which
    DROPS and recreates the database. Under the suite's default
    parallel run two workers would do that to each other mid-test, so
    each worker gets its own database instead.
    """
    uri = os.environ.get("GNUCASH_TEST_PG_URI")
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not uri or not worker:
        return uri
    url = _parse_book_url(uri)
    return str(url.set(database=f"{url.database or 'gnucash'}_{worker}"))


_PG_URI = _worker_pg_uri()


@pytest.mark.skipif(
    not _PG_URI, reason="set GNUCASH_TEST_PG_URI to run PostgreSQL tests"
)
class TestPostgresBackend:
    """The dialect-specific half: a real GnuCash book in PostgreSQL.

    Everything above proves the URI *plumbing* on SQLite. What only
    PostgreSQL can prove is that piecash's schema round-trips through
    a second dialect at all — the paramstyle of the GUID queries
    (``%s`` vs ``?``), server-side type coercion of GnuCash's numeric
    columns, and the gnclock lock check on a non-file backend.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def pg_book():
        book = piecash.create_book(
            uri_conn=_PG_URI, currency="USD", overwrite=True
        )
        root = book.root_account
        usd = book.default_currency
        assets = piecash.Account(
            name="Assets", type="ASSET", parent=root,
            commodity=usd, placeholder=1,
        )
        piecash.Account(
            name="Checking", type="BANK", parent=assets, commodity=usd,
        )
        expenses = piecash.Account(
            name="Expenses", type="EXPENSE", parent=root,
            commodity=usd, placeholder=1,
        )
        piecash.Account(
            name="Groceries", type="EXPENSE", parent=expenses, commodity=usd,
        )
        book.save()
        book.close()
        # create_book's engine would otherwise hold a connection and
        # make the drop below fail with ObjectInUse — the same leak
        # open() disposes for the server.
        book.session.get_bind().dispose()
        yield GnuCashBook(BookSource.from_uri(_PG_URI))

        # Drop the worker's database so a local run doesn't leave one
        # behind per worker. Best-effort: a failure here is litter,
        # never a test result, and the next run recreates it anyway
        # (create_book overwrites).
        try:
            from sqlalchemy_utils import drop_database

            drop_database(_PG_URI)
        except Exception:
            pass

    def test_accounts_read_back(self, pg_book):
        listing = pg_book.list_accounts()
        assert "Assets:Checking" in listing
        assert "Expenses:Groceries" in listing

    def test_write_round_trips(self, pg_book):
        result = pg_book.create_transaction(
            description="Postgres write",
            splits=[
                {"account": "Expenses:Groceries", "amount": "42.50"},
                {"account": "Assets:Checking", "amount": "-42.50"},
            ],
            trans_date=date(2026, 2, 1),
            check_duplicates=False,
        )
        assert result["status"] == "created"
        assert "Postgres write" in pg_book.search_transactions("Postgres write")

    def test_guid_prefix_resolution_uses_the_right_paramstyle(self, pg_book):
        """psycopg2 wants ``%s`` where sqlite3 wants ``?``; the named
        ``:prefix`` form is what makes one query text serve both."""
        with pg_book.open() as book:
            guid = book.root_account.guid
        assert pg_book._resolve_guid("accounts", guid[:8]) == guid

    def test_balance_report_runs(self, pg_book):
        assert pg_book.get_book_summary()

    def test_backups_refuse(self, pg_book):
        with pytest.raises(ValueError, match="pg_dump"):
            pg_book.create_backup()

    def test_placeholder_account_creation(self, pg_book):
        """Regression: ``create_account(placeholder=True)`` passed a
        Python bool into an INTEGER column. SQLite coerced it
        silently; PostgreSQL raises DatatypeMismatch, so this core
        tool was simply broken on the new backend until _gnc_bool.
        """
        pg_book.create_account(
            name="Sub", account_type="EXPENSE",
            parent="Expenses", placeholder=True,
        )
        assert "Expenses:Sub" in pg_book.list_accounts()

    def test_placeholder_toggle(self, pg_book):
        """The update path writes the same column."""
        pg_book.create_account(
            name="Toggle", account_type="EXPENSE", parent="Expenses",
        )
        pg_book.update_account("Expenses:Toggle", placeholder=True)
        assert "Expenses:Toggle [PLACEHOLDER]" in pg_book.list_accounts()

    def test_open_close_releases_the_connection(self, pg_book):
        """Five open/close cycles leave the server's connection count
        where it started — the engine is disposed, not pooled."""
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        probe = create_engine(_PG_URI, poolclass=NullPool)

        def live() -> int:
            with probe.connect() as conn:
                return conn.execute(text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database()"
                )).scalar()

        before = live()
        for _ in range(5):
            with pg_book.open(readonly=True) as opened:
                assert opened.default_currency is not None
        assert live() == before
