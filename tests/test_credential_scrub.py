"""Connection-string credentials never leave the server.

``_scrub_credentials`` (``_format.py``) is the one scrubber; every
road out passes through it — tool results (``redact_paths``), the
audit and debug files, the error logger that propagates to stderr,
startup errors, the dashboard's failed-check lines.

The pre-release adversarial review
(``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``) found the roads
that skipped it:

* C16a — an exception raised while opening a database book quotes
  the whole connection string (piecash: ``Database
  'postgresql://user:pw@host/db' does not exist``) and ``str(e)``
  went to the model, the audit file, and stderr as written;
* C16b — a password in the query string (``?password=``,
  ``sslpassword=``) passed through the display name unmasked;
* C52a — a connection string that failed to parse was echoed in
  full in the error saying so;
* C59 — the tool-error logger carried all of the above to stderr.

The end-to-end half is in ``tests/test_db_backend.py``
(``TestCredentialsNeverLeave``), where the URI-mode server fixture
lives.
"""

import logging

import pytest

from gnucash_mcp._format import (
    _book_display_name,
    _mask_uri_secrets,
    _parse_book_url,
    _redact_uri,
    _scrub_credentials,
)
from gnucash_mcp.logging_config import CredentialScrubFilter, redact_paths

S = "S3CRET"


class TestRedactUri:
    @pytest.mark.parametrize("uri,expected", [
        (f"postgresql://u:{S}@h:5432/db", "postgresql://u:***@h:5432/db"),
        (f"mysql+pymysql://u:{S}@h/db", "mysql+pymysql://u:***@h/db"),
        # A password holding '@': masked whole, not split at the
        # first one (SQLAlchemy's own hide_password leaves the tail
        # in the host).
        (f"postgresql://u:p@ss{S}@h/db", "postgresql://u:***@h/db"),
        # Query-string credentials — libpq's and pymysql's spellings.
        (f"postgresql://u@h/db?password={S}",
         "postgresql://u@h/db?password=***"),
        (f"postgresql://u:{S}@h/db?sslmode=verify-full&sslpassword={S}",
         "postgresql://u:***@h/db?sslmode=verify-full&sslpassword=***"),
        (f"mysql+pymysql://u@h/db?passwd={S}",
         "mysql+pymysql://u@h/db?passwd=***"),
        # No credential: unchanged.
        ("postgresql://u@h/db", "postgresql://u@h/db"),
        ("sqlite:////tmp/x.gnucash", "sqlite:////tmp/x.gnucash"),
    ])
    def test_masks(self, uri, expected):
        assert _redact_uri(uri) == expected
        assert _book_display_name(uri) == expected
        assert S not in _redact_uri(uri)

    def test_socket_form_with_query_password(self):
        out = _redact_uri(
            f"postgresql:///gnucash?host=/var/run/postgresql&user=u"
            f"&password={S}"
        )
        assert S not in out and "password=***" in out

    @pytest.mark.parametrize("bad", [
        f"postgresql:/u:{S}@h/db", f"u:{S}@h/db", f"postgres ql//u:{S}@h",
    ])
    def test_unparseable_is_never_echoed(self, bad):
        assert S not in _redact_uri(bad)

    def test_mask_is_idempotent(self):
        once = _mask_uri_secrets(f"postgresql://u:{S}@h/db?password={S}")
        assert _mask_uri_secrets(once) == once


class TestParseErrorDoesNotEchoTheString:
    """C52a. A string that fails to parse is usually a good one with
    a typo — password and all — and this error goes to stderr."""

    @pytest.mark.parametrize("bad", [
        f"postgresql:/u:{S}@h/db",
        f"postgres ql://u:{S}@h/db",
        f"u:{S}@h/db",
    ])
    def test_no_echo(self, bad):
        with pytest.raises(ValueError) as refusal:
            _parse_book_url(bad)
        message = str(refusal.value)
        assert S not in message
        assert "Not a valid database URL" in message
        # Still actionable.
        assert "postgresql://user:password@host:5432/gnucash" in message


class TestScrubCredentialsInText:
    @pytest.mark.parametrize("text", [
        # piecash/core/session.py
        f"Database 'postgresql://gnucash:{S}@db/gnucsh' does not exist "
        f"(please use create_book to create it)",
        # SQLAlchemy's pysqlite dialect
        f"Invalid SQLite URL: sqlite://dbuser:{S}@\nValid SQLite URL "
        f"forms are:\n sqlite:///:memory:",
        # SQLAlchemy's make_url
        f"Could not parse SQLAlchemy URL from string "
        f"'postgresql:/u:{S}@h/db'",
        f"connect postgresql://u:{S}@host:5432/db failed.",
        f"(see postgresql:///db?host=/tmp&user=u&password={S}, retry)",
        f'engine "mysql+pymysql://u@h/db?passwd={S}&charset=utf8"',
        f"a\npostgresql://u:{S}@h/db\nb",
    ])
    def test_secret_is_gone(self, text):
        out = _scrub_credentials(text)
        assert S not in out
        assert "***" in out

    def test_what_surrounds_it_survives(self):
        out = _scrub_credentials(
            f"Database 'postgresql://gnucash:{S}@db/gnucsh' does not exist"
        )
        assert out == (
            "Database 'postgresql://gnucash:***@db/gnucsh' does not exist"
        )

    @pytest.mark.parametrize("text", [
        "Account not found: Expenses:Medical",
        "docs at https://example.com/a/b?x=1 are fine",
        "file:///Users/j/books/b.gnucash",
        "meeting 12:30@noon, ratio 3:/4",
        "postgresql://u@h/db has no password in it",
        "",
    ])
    def test_text_without_credentials_is_untouched(self, text):
        assert _scrub_credentials(text) == text


class TestRedactPathsAlwaysScrubs:
    """Every error a tool returns passes through ``redact_paths``."""

    TEXT = f"Database 'postgresql://u:{S}@localhost:55432/ledger' missing"

    def test_with_path_redaction_off(self, monkeypatch):
        monkeypatch.delenv("GNUCASH_REDACT_PATHS", raising=False)
        out = redact_paths(self.TEXT)
        assert S not in out
        assert "postgresql://u:***@localhost:55432/ledger" in out

    def test_with_path_redaction_on_the_uri_is_not_mangled(
        self, monkeypatch,
    ):
        """The path patterns used to read the URI as a drive letter
        and a path, leaving ``postgresqledger`` — masked only by
        accident, and not always (``postgresql://u:pw@localhost``
        came out ``postgresqu:pw@localhost``)."""
        monkeypatch.setenv("GNUCASH_REDACT_PATHS", "1")
        out = redact_paths(
            self.TEXT + " at /Users/jane/Finance/book.gnucash"
        )
        assert S not in out
        assert "postgresql://u:***@localhost:55432/ledger" in out
        assert "/Users/jane" not in out and "book.gnucash" in out
        assert S not in redact_paths(f"postgresql://u:{S}@localhost")


class TestLogFilter:
    def test_record_is_scrubbed_before_any_handler(self, caplog):
        log = logging.getLogger("test.credential.scrub")
        log.addFilter(CredentialScrubFilter())
        with caplog.at_level(logging.INFO, logger=log.name):
            log.error("boom: %s", f"postgresql://u:{S}@h/db")
            log.warning(f"Validation error: sqlite://u:{S}@ nope")
            log.info("nothing secret here")
        assert S not in caplog.text
        assert "postgresql://u:***@h/db" in caplog.text
        assert "nothing secret here" in caplog.text

    def test_the_tool_error_logger_carries_it(self):
        from gnucash_mcp.tools import _helpers
        assert any(
            isinstance(f, CredentialScrubFilter)
            for f in _helpers.logger.filters
        )
