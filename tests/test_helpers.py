"""Unit tests for tool-layer helpers (``tools/_helpers.py``).

These cover the foundation pieces — numeric formatting and limit
enforcement — that downstream tools rely on. Once the helpers are
correct here, every consumer inherits the right behavior.
"""

from decimal import Decimal

import pytest

from gnucash_mcp.tools._helpers import _apply_limit, _format_number


class TestFormatNumber:
    """Tests for ``_format_number`` — the single chokepoint that
    keeps numeric values out of the response in 26-decimal form."""

    # ── Currency-style (default decimals=2, no strip) ────────────

    def test_rounds_to_two_decimals(self):
        assert _format_number(Decimal("1234.567")) == "1234.57"

    def test_pads_to_two_decimals(self):
        assert _format_number(Decimal("1234.5")) == "1234.50"

    def test_zero_renders_padded(self):
        assert _format_number(Decimal("0")) == "0.00"

    def test_negative_currency(self):
        assert _format_number(Decimal("-12.34")) == "-12.34"

    def test_high_precision_input_truncates(self):
        # The "calculate_lot_gain at 26 decimals" case the spec calls out.
        long = Decimal("43.91052631578947368421052632")
        assert _format_number(long) == "43.91"

    # ── Share-quantity style (decimals=4) ────────────────────────

    def test_share_quantity_four_decimals(self):
        assert _format_number(Decimal("230.762"), decimals=4) == "230.7620"

    def test_share_quantity_rounds_at_fourth(self):
        assert (
            _format_number(Decimal("230.76225"), decimals=4) == "230.7623"
        )

    # ── Crypto style (decimals=6) ────────────────────────────────

    def test_crypto_six_decimals(self):
        assert _format_number(Decimal("2.5"), decimals=6) == "2.500000"

    def test_crypto_basis_long_input_truncates(self):
        # The "list_lots at 7.500000000000000000000000001" case.
        long = Decimal("7.500000000000000000000000001")
        assert _format_number(long, decimals=6) == "7.500000"

    # ── Strip-trailing for variable-precision fields ─────────────

    def test_strip_trailing_drops_zeros(self):
        assert (
            _format_number(Decimal("1.50"), decimals=2, strip_trailing=True)
            == "1.5"
        )

    def test_strip_trailing_drops_dot_when_all_zero(self):
        assert (
            _format_number(Decimal("1.00"), decimals=2, strip_trailing=True)
            == "1"
        )

    def test_strip_trailing_keeps_nonzero_decimals(self):
        assert (
            _format_number(Decimal("1.05"), decimals=2, strip_trailing=True)
            == "1.05"
        )

    def test_strip_trailing_zero_is_zero(self):
        assert (
            _format_number(Decimal("0"), decimals=2, strip_trailing=True)
            == "0"
        )

    # ── Edge cases / robustness ──────────────────────────────────

    def test_none_renders_as_zero_padded(self):
        assert _format_number(None) == "0.00"

    def test_none_strip_trailing_is_zero(self):
        assert _format_number(None, strip_trailing=True) == "0"

    def test_empty_string_renders_as_zero(self):
        assert _format_number("") == "0.00"

    def test_string_input_parses(self):
        assert _format_number("1234.5") == "1234.50"

    def test_int_input_parses(self):
        assert _format_number(42) == "42.00"

    def test_unparseable_input_passes_through(self):
        # Callers occasionally hand us "N/A" or similar — must not crash.
        assert _format_number("N/A") == "N/A"

    def test_no_scientific_notation_for_large_numbers(self):
        # Decimal would normally emit 1E+9; format("f") prevents that.
        assert _format_number(Decimal("1000000000")) == "1000000000.00"

    def test_no_scientific_notation_for_small_numbers(self):
        # Decimal("0.0001") doesn't normally emit scientific, but
        # confirm the format keeps things explicit at decimals=4.
        assert _format_number(Decimal("0.0001"), decimals=4) == "0.0001"


class TestApplyLimit:
    """Tests for ``_apply_limit`` — the cap-and-notice helper that
    every list-returning tool now uses for consistent truncation."""

    # ── Common cases ─────────────────────────────────────────────

    def test_returns_all_when_under_limit(self):
        items, notice = _apply_limit([1, 2, 3], limit=10)
        assert items == [1, 2, 3]
        assert notice is None

    def test_truncates_when_over_limit(self):
        items, notice = _apply_limit(list(range(100)), limit=5)
        assert items == [0, 1, 2, 3, 4]
        assert notice is not None
        assert "Showing 5 of 100" in notice
        assert "items" in notice

    def test_entity_name_in_notice(self):
        items, notice = _apply_limit(
            list(range(100)), limit=5, entity_name="splits"
        )
        assert items == [0, 1, 2, 3, 4]
        assert "5 of 100 splits" in notice

    # ── Defaults ─────────────────────────────────────────────────

    def test_falsy_limit_uses_default(self):
        items, notice = _apply_limit(list(range(100)), limit=None, default=10)
        assert len(items) == 10
        assert "Showing 10 of 100" in notice

    def test_zero_limit_uses_default(self):
        items, notice = _apply_limit(list(range(100)), limit=0, default=10)
        assert len(items) == 10

    def test_negative_limit_uses_default(self):
        items, notice = _apply_limit(list(range(100)), limit=-5, default=10)
        assert len(items) == 10

    # ── Server-side cap ──────────────────────────────────────────

    def test_caller_limit_above_cap_clamps(self):
        items, notice = _apply_limit(
            list(range(500)), limit=999, max_cap=250
        )
        assert len(items) == 250
        assert notice is not None
        assert "limit capped at 250" in notice
        assert "Showing 250 of 500" in notice

    def test_caller_limit_above_cap_when_results_fit(self):
        # Cap fires but results would have fit anyway → "results fit
        # under the cap" is implicit; we emit the cap notice as a
        # heads-up that the over-limit had no effect.
        items, notice = _apply_limit(
            list(range(100)), limit=999, max_cap=250
        )
        assert items == list(range(100))
        assert notice == "[Limit capped at 250]"

    # ── Notice hints ─────────────────────────────────────────────

    def test_default_hint_says_set_limit_higher(self):
        _, notice = _apply_limit(list(range(100)), limit=5)
        assert "set limit= higher" in notice

    def test_suggest_narrow_changes_hint(self):
        _, notice = _apply_limit(
            list(range(100)),
            limit=5,
            entity_name="transactions",
            suggest_narrow=True,
        )
        assert "narrow filters" in notice

    # ── Edge cases ───────────────────────────────────────────────

    def test_empty_list_no_truncation(self):
        items, notice = _apply_limit([], limit=10)
        assert items == []
        assert notice is None

    def test_list_at_exact_limit_no_notice(self):
        items, notice = _apply_limit([1, 2, 3, 4, 5], limit=5)
        assert items == [1, 2, 3, 4, 5]
        assert notice is None

    def test_returns_correct_slice_order(self):
        # Truncation preserves input order (just ``items[:n]``).
        items, _ = _apply_limit(list(range(20)), limit=3)
        assert items == [0, 1, 2]


class TestSafeToolWriteVerificationRouting:
    """``safe_tool`` distinguishes write-verification failures from
    generic unexpected errors.

    Pre-fix, every ``RuntimeError`` (including ``_verify_write`` /
    ``_verify_transaction_state`` ones — the architectural "every
    write is verified" invariant the codebase upholds) collapsed
    into ``error_type=unexpected_error``. Callers couldn't tell
    "the write didn't land" from "we tried to read a missing key."
    """

    def test_verification_failure_routed_to_dedicated_error_type(self):
        import json
        from gnucash_mcp.tools._helpers import safe_tool

        @safe_tool
        def fake_tool() -> str:
            raise RuntimeError("Transaction write verification failed: ...")

        result = json.loads(fake_tool())
        assert result["error_type"] == "write_verification_failed"
        assert "verification failed" in result["error"].lower()

    def test_other_runtime_errors_still_unexpected(self):
        import json
        from gnucash_mcp.tools._helpers import safe_tool

        @safe_tool
        def fake_tool() -> str:
            raise RuntimeError("some unrelated runtime issue")

        result = json.loads(fake_tool())
        assert result["error_type"] == "unexpected_error"

    def test_value_errors_unchanged(self):
        import json
        from gnucash_mcp.tools._helpers import safe_tool

        @safe_tool
        def fake_tool() -> str:
            raise ValueError("Account not found: Bogus")

        result = json.loads(fake_tool())
        assert result["error_type"] == "validation_error"
