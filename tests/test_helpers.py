"""Unit tests for tool-layer helpers (``tools/_helpers.py``).

These cover the foundation pieces — numeric formatting and limit
enforcement — that downstream tools rely on. Once the helpers are
correct here, every consumer inherits the right behavior.
"""

from decimal import Decimal

import pytest

from gnucash_mcp.tools._helpers import (
    _apply_limit,
    _format_number,
    _paginate,
    _resolve_id_alias,
)


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


class TestPaginate:
    """Tests for ``_paginate`` — the offset+limit chokepoint that emits
    the always-present ``Showing X-Y of Z`` indicator. Mirrors the
    test list in specs/PAGINATION.md."""

    # ── First page / default ─────────────────────────────────────

    def test_default_first_page(self):
        # Spec test 1: default call shows ``1-50 of N`` when N > 50.
        page, ind = _paginate(
            list(range(109)), entity_name="transactions"
        )
        assert page == list(range(50))
        assert ind == "Showing 1-50 of 109 transactions"

    def test_second_page_offset(self):
        # Spec test 2: offset=50 returns ``51-100 of N`` with the
        # correct rows.
        page, ind = _paginate(
            list(range(109)), offset=50, entity_name="transactions"
        )
        assert page == list(range(50, 100))
        assert ind == "Showing 51-100 of 109 transactions"

    def test_last_partial_page(self):
        # Spec test 3: last page shows a partial range.
        page, ind = _paginate(
            list(range(109)), offset=100, entity_name="transactions"
        )
        assert page == list(range(100, 109))
        assert ind == "Showing 101-109 of 109 transactions"

    def test_full_set_fits_one_page(self):
        # Spec test 4: N <= limit shows ``1-N of N``.
        page, ind = _paginate(
            list(range(23)), entity_name="transactions"
        )
        assert page == list(range(23))
        assert ind == "Showing 1-23 of 23 transactions"

    # ── Edge cases ───────────────────────────────────────────────

    def test_zero_results(self):
        # Spec test 5.
        page, ind = _paginate([], entity_name="transactions")
        assert page == []
        assert ind == "Showing 0 of 0 transactions"

    def test_offset_beyond_total(self):
        # Spec test 6.
        page, ind = _paginate(
            list(range(109)), offset=200, entity_name="transactions"
        )
        assert page == []
        assert "of 109 transactions" in ind
        assert "offset 200 exceeds result count" in ind

    def test_count_only_mode(self):
        # Spec test 7: limit=0 returns the count-only indicator.
        page, ind = _paginate(
            list(range(245)), limit=0, entity_name="transactions"
        )
        assert page == []
        assert ind == "Showing 0 of 245 transactions"

    def test_count_only_includes_date_range(self):
        page, ind = _paginate(
            list(range(245)),
            limit=0,
            entity_name="transactions",
            date_range=("2026-01-01", "2026-06-18"),
        )
        assert page == []
        assert ind == (
            "Showing 0 of 245 transactions "
            "(2026-01-01 to 2026-06-18)"
        )

    # ── Date range ───────────────────────────────────────────────

    def test_date_range_appended(self):
        # Spec test 9: date range reflects the full result set.
        _, ind = _paginate(
            list(range(109)),
            entity_name="transactions",
            date_range=("2026-05-01", "2026-06-12"),
        )
        assert ind == (
            "Showing 1-50 of 109 transactions "
            "(2026-05-01 to 2026-06-12)"
        )

    def test_date_range_omitted_when_undated(self):
        _, ind = _paginate(
            list(range(5)), entity_name="accounts", date_range=None
        )
        assert ind == "Showing 1-5 of 5 accounts"

    def test_date_range_skipped_when_member_missing(self):
        _, ind = _paginate(
            list(range(5)),
            entity_name="prices",
            date_range=(None, None),
        )
        assert ind == "Showing 1-5 of 5 prices"

    # ── Server-side cap ──────────────────────────────────────────

    def test_limit_above_cap_clamps_with_note(self):
        page, ind = _paginate(
            list(range(500)), limit=999, max_cap=250,
            entity_name="transactions",
        )
        assert len(page) == 250
        assert ind == (
            "Showing 1-250 of 500 transactions; limit capped at 250"
        )

    def test_cap_note_present_even_when_results_fit(self):
        # Like ``_apply_limit``'s "[Limit capped at N]" heads-up: a
        # requested limit above the ceiling is flagged so the LLM knows
        # the server cap exists, even when the page held everything.
        _, ind = _paginate(
            list(range(100)), limit=999, max_cap=250,
            entity_name="transactions",
        )
        assert ind == (
            "Showing 1-100 of 100 transactions; limit capped at 250"
        )

    def test_no_cap_note_when_limit_within_cap(self):
        _, ind = _paginate(
            list(range(100)), limit=200, max_cap=250,
            entity_name="transactions",
        )
        assert "capped" not in ind
        assert ind == "Showing 1-100 of 100 transactions"

    # ── Defaults / clamping ──────────────────────────────────────

    def test_falsy_limit_uses_default(self):
        page, _ = _paginate(
            list(range(100)), limit=None, default=10,
            entity_name="x",
        )
        assert len(page) == 10

    def test_negative_offset_clamps_to_zero(self):
        page, ind = _paginate(
            list(range(10)), offset=-5, entity_name="x"
        )
        assert page == list(range(10))
        assert ind == "Showing 1-10 of 10 x"

    def test_indicator_always_returned(self):
        # The contract: indicator is never None, for any input.
        for args in [([],), (list(range(3)),), (list(range(300)),)]:
            _, ind = _paginate(*args, entity_name="x")
            assert isinstance(ind, str) and ind


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


class TestResolveIdAlias:
    """``_resolve_id_alias`` lets delete_invoice / delete_bill /
    delete_voucher / delete_credit_note accept both ``id`` (the
    standard name across get_invoice / post_invoice / unpost_invoice /
    pay_invoice) and the legacy ``<entity>_id`` parameter for
    back-compat.

    Required because ``extra="forbid"`` on tool arg models (shipped
    on PR #92) forbids silent kwarg aliasing — both names have to be
    declared parameters on the wrapper, and the helper picks the
    right one.
    """

    def test_prefers_id_when_only_id_set(self):
        assert _resolve_id_alias("000001", None, "invoice_id") == "000001"

    def test_accepts_legacy_when_only_legacy_set(self):
        assert _resolve_id_alias(None, "000001", "invoice_id") == "000001"

    def test_rejects_both_set(self):
        with pytest.raises(ValueError, match="exactly one"):
            _resolve_id_alias("000001", "000002", "invoice_id")

    def test_rejects_both_missing(self):
        with pytest.raises(ValueError, match="Missing required parameter"):
            _resolve_id_alias(None, None, "invoice_id")

    def test_error_message_names_the_legacy_parameter(self):
        # Useful for the LLM debug loop — the error needs to say
        # which alias it was looking for so the caller can fix
        # their call.
        with pytest.raises(ValueError, match="bill_id"):
            _resolve_id_alias(None, None, "bill_id")
        with pytest.raises(ValueError, match="voucher_id"):
            _resolve_id_alias("a", "b", "voucher_id")
