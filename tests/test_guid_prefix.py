"""Tests for GUID protection: output-side prefix collisions and input-side
fail-fast validation.

Output side
-----------
The blanket ``guid[:8]`` truncation in compact formatters is unsafe at
scale — the birthday problem puts the collision rate at ~1-2% by ~10,000
entries. ``_guid_prefix_map`` extends colliding prefixes just enough to
stay unique within the set, preserving the short-prefix affordance for
the LLM while guaranteeing that every emitted prefix is a valid lookup
key against ``_resolve_guid``.

Input side
----------
``_resolve_guid`` rejects malformed inputs before opening SQLite —
length must be in [8, 32], characters must be hex, and uppercase is
normalized to lowercase. Previously garbage-shaped inputs survived
to the DB and surfaced as "not found" (misleading: the input was
never a valid GUID to begin with).

These tests use contrived GUID-shaped strings (no piecash required) so
we can force collisions deterministically. ``_resolve_guid``'s input
validation is tested directly — it errors before any DB I/O, so we
don't need a real book path.
"""

from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.book._base import (
    _guid_prefix_map,
    _lcp_length,
    _lot_to_compact_line,
    _short_guid,
    _sx_to_compact_line,
    _unreconciled_split_to_compact_line,
    _upcoming_to_compact_line,
)


class TestLcpLength:
    """Longest-common-prefix helper."""

    def test_identical(self):
        assert _lcp_length("abcdef", "abcdef") == 6

    def test_no_overlap(self):
        assert _lcp_length("abcdef", "ghijkl") == 0

    def test_partial(self):
        assert _lcp_length("abcxyz", "abcdef") == 3

    def test_one_is_prefix_of_other(self):
        assert _lcp_length("abc", "abcdef") == 3
        assert _lcp_length("abcdef", "abc") == 3

    def test_empty(self):
        assert _lcp_length("", "abc") == 0
        assert _lcp_length("abc", "") == 0
        assert _lcp_length("", "") == 0


class TestGuidPrefixMap:
    """The per-set minimum-unique-prefix mapper."""

    def test_empty(self):
        assert _guid_prefix_map([]) == {}

    def test_single(self):
        """Single GUID gets min_len (default 8)."""
        result = _guid_prefix_map(["aaaaaaaa1111"])
        assert result == {"aaaaaaaa1111": "aaaaaaaa"}

    def test_all_unique_at_min_len(self):
        """When no two GUIDs share the first 8 chars, all get 8."""
        guids = ["aaaaaaaa1111", "bbbbbbbb2222", "cccccccc3333"]
        result = _guid_prefix_map(guids)
        assert result == {
            "aaaaaaaa1111": "aaaaaaaa",
            "bbbbbbbb2222": "bbbbbbbb",
            "cccccccc3333": "cccccccc",
        }

    def test_collision_at_min_len_extends_both(self):
        """Two GUIDs sharing an 8-char prefix both extend to 9."""
        guids = ["aaaaaaaa1xxx", "aaaaaaaa2xxx", "cccccccc3333"]
        result = _guid_prefix_map(guids)
        # The colliding pair extends just one past divergence.
        assert result["aaaaaaaa1xxx"] == "aaaaaaaa1"
        assert result["aaaaaaaa2xxx"] == "aaaaaaaa2"
        # Non-colliding GUID stays at 8.
        assert result["cccccccc3333"] == "cccccccc"

    def test_deeper_collision_extends_further(self):
        """Sharing 10 chars forces length 11."""
        guids = ["aaaaaaaaaa1x", "aaaaaaaaaa2x"]
        result = _guid_prefix_map(guids)
        assert result["aaaaaaaaaa1x"] == "aaaaaaaaaa1"
        assert result["aaaaaaaaaa2x"] == "aaaaaaaaaa2"

    def test_three_way_shared_prefix(self):
        """Three GUIDs sharing the min_len prefix all extend."""
        guids = ["aaaaaaaa1111", "aaaaaaaa2222", "aaaaaaaa3333"]
        result = _guid_prefix_map(guids)
        assert result["aaaaaaaa1111"] == "aaaaaaaa1"
        assert result["aaaaaaaa2222"] == "aaaaaaaa2"
        assert result["aaaaaaaa3333"] == "aaaaaaaa3"

    def test_asymmetric_collision(self):
        """Collision on one side only still forces extension."""
        # c shares 8 with d, but e is alone
        guids = ["dddddddd1111", "dddddddd2222", "ffffffff9999"]
        result = _guid_prefix_map(guids)
        assert result["dddddddd1111"] == "dddddddd1"
        assert result["dddddddd2222"] == "dddddddd2"
        assert result["ffffffff9999"] == "ffffffff"

    def test_min_len_parameter(self):
        """Callers can request a longer minimum."""
        guids = ["aaaaaaaa1111", "bbbbbbbb2222"]
        # min_len=10 forces both to 10, even though 1 char would disambiguate
        result = _guid_prefix_map(guids, min_len=10)
        assert result["aaaaaaaa1111"] == "aaaaaaaa11"
        assert result["bbbbbbbb2222"] == "bbbbbbbb22"

    def test_duplicate_input_dedupes(self):
        """Duplicate GUIDs in the input map to the same (full) prefix."""
        guids = ["aaaaaaaa1111", "aaaaaaaa1111", "bbbbbbbb2222"]
        result = _guid_prefix_map(guids)
        assert len(result) == 2
        assert "aaaaaaaa1111" in result
        assert "bbbbbbbb2222" in result

    def test_result_prefixes_are_actually_unique(self):
        """Invariant: the emitted prefixes never collide."""
        guids = [
            "aaaaaaaa1111",
            "aaaaaaaa2222",
            "aaaaaaaaaaaa",
            "aaaaaaaaaabb",
            "bbbbbbbb3333",
            "bbbbbbbb4444",
        ]
        result = _guid_prefix_map(guids)
        prefixes = list(result.values())
        # No prefix is a prefix of another (a looser check that catches
        # the same failure mode)
        for a in prefixes:
            for b in prefixes:
                if a is b:
                    continue
                assert not b.startswith(a), (
                    f"Prefix collision: {a!r} is a prefix of {b!r}"
                )

    def test_full_guid_collision_maps_to_full_string(self):
        """Two identical full GUIDs (impossible for uuid4 but defend anyway)
        both map to the full string."""
        # _guid_prefix_map dedupes via set(), so this actually tests that
        # a single GUID of exactly min_len chars maps to itself.
        result = _guid_prefix_map(["abcdefgh"])  # exactly 8 chars
        assert result["abcdefgh"] == "abcdefgh"

    def test_shorter_than_min_len(self):
        """A GUID shorter than min_len maps to its full content."""
        result = _guid_prefix_map(["abc"])  # only 3 chars
        assert result["abc"] == "abc"


class TestShortGuid:
    """The formatter-facing resolver."""

    def test_uses_prefix_map_when_present(self):
        prefixes = {"aaaaaaaa1234": "aaaaaaaa1"}
        assert _short_guid("aaaaaaaa1234", prefixes) == "aaaaaaaa1"

    def test_falls_back_to_eight_char_truncation(self):
        """Without a map, behaves exactly like the old guid[:8]."""
        assert _short_guid("aaaaaaaa1234", None) == "aaaaaaaa"

    def test_missing_guid_in_map_falls_back(self):
        """If the map was built for a different set, default to [:8]."""
        prefixes = {"other_guid_xx": "other_gu"}
        assert _short_guid("aaaaaaaa1234", prefixes) == "aaaaaaaa"

    def test_empty_map_falls_back(self):
        assert _short_guid("aaaaaaaa1234", {}) == "aaaaaaaa"


class TestCompactFormattersUsePrefixMap:
    """Integration: the compact formatters actually honor the prefix arg."""

    def test_unreconciled_split_uses_map(self):
        split = {
            "guid": "aaaaaaaa1234",
            "date": "2026-01-15",
            "description": "Safeway",
            "amount": "-47.50",
            "reconcile_state": "n",
        }
        # Map with forced 9-char prefix
        prefixes = {"aaaaaaaa1234": "aaaaaaaa1"}
        line = _unreconciled_split_to_compact_line(split, prefixes=prefixes)
        assert line.startswith("aaaaaaaa1\t")
        # Without the map, falls back to 8
        line_default = _unreconciled_split_to_compact_line(split)
        assert line_default.startswith("aaaaaaaa\t")

    def test_lot_uses_map(self):
        lot = {
            "guid": "aaaaaaaa1234",
            "title": "VTSAX Jan 2026",
            "quantity": "10.0000",
            "cost_basis": "2504.50",
        }
        prefixes = {"aaaaaaaa1234": "aaaaaaaa1"}
        line = _lot_to_compact_line(lot, prefixes=prefixes)
        assert line.startswith("aaaaaaaa1\t")
        line_default = _lot_to_compact_line(lot)
        assert line_default.startswith("aaaaaaaa\t")

    def test_sx_uses_map(self):
        sx = {
            "guid": "aaaaaaaa1234",
            "name": "Monthly Rent",
            "frequency": "monthly",
            "enabled": True,
            "next_occurrence": "2026-03-01",
        }
        prefixes = {"aaaaaaaa1234": "aaaaaaaa1"}
        line = _sx_to_compact_line(sx, prefixes=prefixes)
        assert line.startswith("aaaaaaaa1\t")
        line_default = _sx_to_compact_line(sx)
        assert line_default.startswith("aaaaaaaa\t")

    def test_upcoming_uses_map(self):
        entry = {
            "guid": "aaaaaaaa1234",
            "name": "Comcast Xfinity",
            "occurrence_date": "2026-02-27",
            "days_until": 12,
            "amount": "149.26",
        }
        prefixes = {"aaaaaaaa1234": "aaaaaaaa1"}
        line = _upcoming_to_compact_line(entry, prefixes=prefixes)
        assert line.startswith("aaaaaaaa1\t")
        line_default = _upcoming_to_compact_line(entry)
        assert line_default.startswith("aaaaaaaa\t")

    # _transaction_to_compact_line takes a piecash Transaction object,
    # not a dict — tested in the main test suite by the real book tests.
    # A unit test here would require mocking piecash heavily for little gain.


class TestResolveGuidValidation:
    """Fail-fast input validation in ``_resolve_guid``.

    These tests error before any SQLite call, so a valid book path isn't
    required — just a GnuCashBook instance built from a minimal book.
    """

    @pytest.fixture
    def book(self, test_book: Path) -> GnuCashBook:
        return GnuCashBook(str(test_book))

    # ── Length ──────────────────────────────────────────────────

    def test_table_dispatch_uses_parameterized_query(
        self, book: GnuCashBook,
    ):
        """``_resolve_guid`` looks up its SQL via the
        ``_GUID_TABLE_QUERIES`` dispatch dict rather than f-string-
        interpolating the table name. Pre-fix the query was built as
        ``f"SELECT guid FROM {table} ..."`` — safe via the
        ``_GUID_TABLES`` allowlist but a fragile pattern if a future
        contributor added a table without re-validating.
        """
        # Every entry in _GUID_TABLES has a matching query.
        assert set(book._GUID_TABLES) == set(book._GUID_TABLE_QUERIES.keys())
        # Each query string targets the right table and parameterizes
        # the prefix (no f-string-of-user-input hidden in there).
        for table, sql in book._GUID_TABLE_QUERIES.items():
            assert "?" in sql, f"{table} query lost its parameter binding"
            assert f"FROM {table}" in sql, (
                f"{table} query doesn't target the right table: {sql}"
            )

    def test_extended_table_coverage(self, book: GnuCashBook):
        """Coverage extended to ``prices`` and ``entries`` tables —
        both have ``guid`` columns and may surface as short prefixes
        from any tool that emits them. Pre-fix only the original 10
        tables resolved; a price-GUID prefix would raise
        "Invalid table"."""
        assert "prices" in book._GUID_TABLES
        assert "entries" in book._GUID_TABLES
        # Slots is intentionally absent — slots have no primary
        # GUID; they're keyed by (obj_guid, name).
        assert "slots" not in book._GUID_TABLES

    def test_rejects_under_8_chars(self, book: GnuCashBook):
        with pytest.raises(ValueError, match="too short"):
            book._resolve_guid("transactions", "abc")

    def test_rejects_empty_string(self, book: GnuCashBook):
        with pytest.raises(ValueError, match="too short"):
            book._resolve_guid("transactions", "")

    def test_rejects_exactly_7_chars(self, book: GnuCashBook):
        with pytest.raises(ValueError, match="too short"):
            book._resolve_guid("transactions", "abcdef1")

    def test_rejects_over_32_chars(self, book: GnuCashBook):
        with pytest.raises(ValueError, match="too long"):
            book._resolve_guid("transactions", "a" * 33)

    def test_rejects_absurdly_long(self, book: GnuCashBook):
        """Oversized strings never hit SQLite."""
        with pytest.raises(ValueError, match="too long"):
            book._resolve_guid("transactions", "a" * 500)

    # ── Character set ───────────────────────────────────────────

    def test_rejects_non_hex_letters(self, book: GnuCashBook):
        with pytest.raises(ValueError, match="non-hex"):
            book._resolve_guid("transactions", "xyzxyzxyz")

    def test_rejects_underscores(self, book: GnuCashBook):
        """The old 'nonexistent_guid' sentinel would now be caught here."""
        with pytest.raises(ValueError, match="non-hex"):
            book._resolve_guid("transactions", "nonexistent_guid")

    def test_rejects_dashes(self, book: GnuCashBook):
        """Standard UUID display form with dashes is not valid."""
        with pytest.raises(ValueError, match="non-hex"):
            book._resolve_guid("transactions", "abcd-ef01-2345-6789")

    def test_rejects_whitespace(self, book: GnuCashBook):
        with pytest.raises(ValueError, match="non-hex"):
            book._resolve_guid("transactions", "abcd efgh")

    def test_rejects_punctuation(self, book: GnuCashBook):
        with pytest.raises(ValueError, match="non-hex"):
            book._resolve_guid("transactions", "abcd!@#$")

    # ── Case handling ───────────────────────────────────────────

    def test_uppercase_hex_is_accepted_and_normalized(
        self, book: GnuCashBook
    ):
        """Uppercase hex survives validation (normalized to lowercase).

        We can't assert a successful resolve without a known GUID in the
        book, but we can assert that validation doesn't reject it — the
        failure mode shifts from 'non-hex characters' to 'No transaction
        found matching GUID prefix'.
        """
        with pytest.raises(ValueError, match="No transaction found"):
            book._resolve_guid("transactions", "DEADBEEF00")
        # And the error message should contain the lowercased form
        try:
            book._resolve_guid("transactions", "DEADBEEF00")
        except ValueError as e:
            assert "deadbeef00" in str(e), (
                f"Expected lowercased form in error, got: {e}"
            )

    def test_mixed_case_hex_is_accepted(self, book: GnuCashBook):
        """Mixed case survives the character check."""
        with pytest.raises(ValueError, match="No transaction found"):
            book._resolve_guid("transactions", "DeAdBeEf00")

    # ── Table whitelist ─────────────────────────────────────────

    def test_rejects_unknown_table(self, book: GnuCashBook):
        with pytest.raises(ValueError, match="Invalid table"):
            book._resolve_guid("users", "deadbeef00")

    def test_rejects_sql_injection_in_table(self, book: GnuCashBook):
        """The table whitelist stops ``"; DROP TABLE ..."`` style inputs."""
        with pytest.raises(ValueError, match="Invalid table"):
            book._resolve_guid(
                "transactions; DROP TABLE splits;--", "deadbeef00"
            )

    # ── Validation happens before SQLite ────────────────────────

    def test_validation_runs_before_db_io(self, tmp_path: Path):
        """Malformed inputs raise even when the book file doesn't exist.

        This proves the rejection is pure input validation, not a by-
        product of a failed database lookup.
        """
        # Create an empty file to satisfy FileNotFoundError in __init__,
        # then delete it so any SQLite attempt would fail. If validation
        # runs first, the malformed-input error fires cleanly.
        fake_book = tmp_path / "fake.gnucash"
        fake_book.touch()
        book = GnuCashBook(str(fake_book))
        fake_book.unlink()  # now the file is gone

        # These must raise ValueError (not sqlite3.OperationalError)
        with pytest.raises(ValueError, match="too short"):
            book._resolve_guid("transactions", "abc")
        with pytest.raises(ValueError, match="too long"):
            book._resolve_guid("transactions", "a" * 50)
        with pytest.raises(ValueError, match="non-hex"):
            book._resolve_guid("transactions", "xyz_not_hex")
        with pytest.raises(ValueError, match="Invalid table"):
            book._resolve_guid("bogus_table", "deadbeef00")
