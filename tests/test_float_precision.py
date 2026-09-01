"""Regression tests for the float/Decimal precision bug.

``create_transaction`` rejected balanced payloads like $94.87
because `0.87` has no exact binary representation and the IEEE-754
epsilon leaked from a ``float`` into ``Decimal(...)`` before the
sum-to-zero check.

Two protections now guard the boundary:

1. ``tools._helpers.SplitInput`` — pydantic model with
   ``coerce_numbers_to_str=True`` that stringifies any stray JSON
   number via Python's shortest-repr before it reaches the book
   method.
2. ``book._base._to_decimal`` — belt-and-suspenders ``Decimal(str(x))``
   used at every user-facing ``Decimal(...)`` site. Catches direct
   callers (tests, scripts) that bypass the pydantic layer.

These tests exercise both protections with the specific non-dyadic
amounts the bug report identified plus the multi-currency/penny
edge cases.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.book._base import _to_decimal
from gnucash_mcp.tools._helpers import SplitInput, _splits_to_dicts


# ── _to_decimal helper ──────────────────────────────────────────────


class TestToDecimalHelper:
    """Contract tests for the belt-and-suspenders helper."""

    def test_float_94_87_round_trips_cleanly(self):
        """The canonical bug: Decimal(0.87) carries IEEE noise;
        _to_decimal routes through str() and loses it."""
        assert _to_decimal(94.87) == Decimal("94.87")
        # Sanity: the direct-Decimal-of-float path is still broken,
        # which is why we need this helper.
        assert Decimal(94.87) != Decimal("94.87")

    def test_string_passes_through(self):
        assert _to_decimal("94.87") == Decimal("94.87")

    def test_decimal_passes_through(self):
        # Identity for Decimal inputs — no stringify round-trip needed.
        d = Decimal("94.87")
        assert _to_decimal(d) is d

    def test_integer_coerces(self):
        assert _to_decimal(42) == Decimal("42")

    def test_sum_of_non_dyadic_floats_balances(self):
        """The exact scenario from the bookkeeper's report: three non-dyadic
        floats + one float negation sum to zero."""
        amounts = [6.25, 5.75, 3.87, -15.87]
        total = sum((_to_decimal(a) for a in amounts), Decimal("0"))
        assert total == Decimal("0")


# ── SplitInput pydantic coercion ────────────────────────────────────


class TestSplitInputCoercion:
    """The boundary model converts stray JSON numbers to strings so
    `Decimal(...)` downstream never sees a float."""

    def test_bare_float_coerced_to_string(self):
        split = SplitInput(account="Assets:Checking", amount=94.87)
        assert split.amount == "94.87"
        # And the string itself round-trips exactly to the right Decimal.
        assert Decimal(split.amount) == Decimal("94.87")

    def test_string_passes_through(self):
        split = SplitInput(account="Assets:Checking", amount="94.87")
        assert split.amount == "94.87"

    def test_quantity_optional(self):
        split = SplitInput(account="Assets:Checking", amount="94.87")
        assert split.quantity is None
        # exclude_none drops it from the dict so `"quantity" in split`
        # keeps working.
        assert "quantity" not in split.model_dump(exclude_none=True)

    def test_quantity_bare_float_coerced(self):
        split = SplitInput(
            account="Assets:Euro Savings", amount="110.00", quantity=100.0,
        )
        assert split.quantity == "100.0"
        assert Decimal(split.quantity) == Decimal("100.0")

    def test_extras_forbidden(self):
        # HP-10: unknown keys now raise a ValidationError instead of
        # silently dropping. Pre-Branch-3 the config was
        # extra="ignore" — a typo like ``quantitiy`` would be
        # silently discarded, leaving the transaction with a
        # cross-currency value/quantity mismatch. extra="forbid"
        # matches the server-global ArgModelBase config and
        # surfaces typos as boundary errors.
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SplitInput(
                account="Assets:Checking", amount="1.00", unknown_key="x",
            )

    def test_splits_to_dicts_converts_list(self):
        inputs = [
            SplitInput(account="Assets:Checking", amount=94.87),
            SplitInput(account="Expenses:Groceries", amount=-94.87),
        ]
        dicts = _splits_to_dicts(inputs)
        assert dicts == [
            {"account": "Assets:Checking", "amount": "94.87"},
            {"account": "Expenses:Groceries", "amount": "-94.87"},
        ]

    def test_splits_to_dicts_none_passthrough(self):
        # create_transaction's auto-fill path passes None; must survive.
        assert _splits_to_dicts(None) is None


# ── book.create_transaction — the primary bug site ────────────────────


class TestCreateTransactionNonDyadic:
    """Direct-call tests against the book method. Feeding floats
    directly exercises the ``_to_decimal`` guard inside the book
    layer, independent of the pydantic boundary.
    """

    def test_94_87_balanced_via_float(self, test_book: Path):
        """Canonical failing case from the bug report."""
        gc = GnuCashBook(str(test_book))
        result = gc.create_transaction(
            description="Cat tree",
            splits=[
                {"account": "Expenses:Groceries", "amount": 94.87},
                {"account": "Assets:Checking", "amount": -94.87},
            ],
            trans_date=date(2026, 4, 23),
            check_duplicates=False,
        )
        assert result["status"] == "created"

    def test_94_87_balanced_via_string(self, test_book: Path):
        """Same amounts as strings — the documented happy path."""
        gc = GnuCashBook(str(test_book))
        result = gc.create_transaction(
            description="Cat tree (str)",
            splits=[
                {"account": "Expenses:Groceries", "amount": "94.87"},
                {"account": "Assets:Checking", "amount": "-94.87"},
            ],
            trans_date=date(2026, 4, 23),
            check_duplicates=False,
        )
        assert result["status"] == "created"

    def test_three_way_non_dyadic_split(self, test_book: Path):
        """6.25 + 5.75 + 3.87 against -15.87 — the three-leg case the
        spec calls out. Each leg non-dyadic; the sum is exact."""
        gc = GnuCashBook(str(test_book))
        result = gc.create_transaction(
            description="Three-way split",
            splits=[
                {"account": "Expenses:Groceries", "amount": 6.25},
                {"account": "Expenses:Groceries", "amount": 5.75},
                {"account": "Expenses:Groceries", "amount": 3.87},
                {"account": "Assets:Checking", "amount": -15.87},
            ],
            trans_date=date(2026, 4, 23),
            check_duplicates=False,
        )
        assert result["status"] == "created"

    def test_penny_split(self, test_book: Path):
        """$0.01 — smallest representable cent. Non-dyadic in binary."""
        gc = GnuCashBook(str(test_book))
        result = gc.create_transaction(
            description="Penny",
            splits=[
                {"account": "Expenses:Groceries", "amount": 0.01},
                {"account": "Assets:Checking", "amount": -0.01},
            ],
            trans_date=date(2026, 4, 23),
            check_duplicates=False,
        )
        assert result["status"] == "created"

    def test_100_divided_three_ways_rounded(self, test_book: Path):
        """$100 split three ways rounded to cents: 33.33 + 33.33 + 33.34
        balanced against -100. The third leg carries the rounding error."""
        gc = GnuCashBook(str(test_book))
        result = gc.create_transaction(
            description="Split three ways",
            splits=[
                {"account": "Expenses:Groceries", "amount": "33.33"},
                {"account": "Expenses:Groceries", "amount": "33.33"},
                {"account": "Expenses:Groceries", "amount": "33.34"},
                {"account": "Assets:Checking", "amount": "-100.00"},
            ],
            trans_date=date(2026, 4, 23),
            check_duplicates=False,
        )
        assert result["status"] == "created"

    def test_unbalanced_floats_still_rejected(self, test_book: Path):
        """Belt-and-suspenders must not mask real balance errors. A
        genuine off-by-a-cent still raises."""
        gc = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="do not balance"):
            gc.create_transaction(
                description="Bad math",
                splits=[
                    {"account": "Expenses:Groceries", "amount": 10.00},
                    {"account": "Assets:Checking", "amount": -10.01},
                ],
                trans_date=date(2026, 4, 23),
                check_duplicates=False,
            )


# ── book.replace_splits and update_transaction ────────────────────────


class TestReplaceSplitsNonDyadic:
    """replace_splits shares the same Decimal(split["amount"]) pattern
    as create; the guard applies there too."""

    def test_replace_with_non_dyadic_floats(self, test_book: Path):
        gc = GnuCashBook(str(test_book))
        # Seed a transaction we can then replace.
        created = gc.create_transaction(
            description="To replace",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=date(2026, 4, 23),
            check_duplicates=False,
        )
        result = gc.replace_splits(
            guid=created["guid"],
            splits=[
                {"account": "Expenses:Groceries", "amount": 42.29},
                {"account": "Assets:Checking", "amount": -42.29},
            ],
        )
        assert result["status"] == "splits_replaced"


class TestUpdateTransactionNonDyadic:
    """update_transaction also re-validates balance on split updates."""

    def test_update_splits_with_non_dyadic_floats(self, test_book: Path):
        gc = GnuCashBook(str(test_book))
        created = gc.create_transaction(
            description="To update",
            splits=[
                {"account": "Expenses:Groceries", "amount": "50.00"},
                {"account": "Assets:Checking", "amount": "-50.00"},
            ],
            trans_date=date(2026, 4, 23),
            check_duplicates=False,
        )
        # update_transaction matches by account fullname, so the split
        # accounts must stay the same — only amounts change.
        result = gc.update_transaction(
            guid=created["guid"],
            splits=[
                {"account": "Expenses:Groceries", "amount": 18.10},
                {"account": "Assets:Checking", "amount": -18.10},
            ],
        )
        assert result["status"] == "updated"


# ── Scheduled transactions — persisted-float protection ───────────────


class TestScheduledSplitsPersistedAsStrings:
    """The splits-json slot must never store a JSON numeric literal.
    If a float slips through the schema layer, the write-side
    normalization re-routes it through str(Decimal(str(x))) so every
    future instantiation reads a clean decimal string."""

    def test_slot_stores_string_amounts(self, scheduled_book: Path):
        import json
        from sqlalchemy import text

        gc = GnuCashBook(str(scheduled_book))
        gc.create_scheduled_transaction(
            name="Cat Tree Subscription",
            description="Rolling non-dyadic charge",
            splits=[
                {"account": "Expenses:Rent", "amount": 94.87},
                {"account": "Assets:Checking", "amount": -94.87},
            ],
            start_date="2026-05-01",
            frequency="monthly",
        )

        # Read the slot raw to confirm the stored JSON is strings.
        with gc.open(readonly=True) as book:
            rows = book.session.execute(
                text(
                    "SELECT string_val FROM slots WHERE name = :name"
                ),
                {"name": "splits-json"},
            ).fetchall()
        assert len(rows) == 1
        payload = json.loads(rows[0][0])
        # json.loads preserves types: if amount were a float, it would
        # parse as float, not str.
        assert all(isinstance(s["amount"], str) for s in payload)
        # And the values round-trip through Decimal exactly.
        total = sum(
            (Decimal(s["amount"]) for s in payload), Decimal("0"),
        )
        assert total == Decimal("0")

    def test_instantiation_balances(self, scheduled_book: Path):
        """End-to-end: create a schedule with float amounts, then
        instantiate it; the real transaction must balance."""
        gc = GnuCashBook(str(scheduled_book))
        sx = gc.create_scheduled_transaction(
            name="Cat Tree Monthly",
            description="Monthly charge",
            splits=[
                {"account": "Expenses:Rent", "amount": 94.87},
                {"account": "Assets:Checking", "amount": -94.87},
            ],
            start_date="2026-05-01",
            frequency="monthly",
        )
        result = gc.create_transaction_from_scheduled(
            guid=sx["guid"], transaction_date="2026-05-01",
        )
        assert result["status"] == "created"


# ── Multi-currency: 4-decimal FX rate precision ───────────────────────


class TestCrossCurrencyFourDecimalRate:
    """Spec asks for a multi-currency split where converted amounts
    involve 4-decimal FX rates. `create_transaction` with an explicit
    quantity exercises the quantity-side `_to_decimal` guard too."""

    def test_cross_currency_float_quantity(self, multi_currency_book: Path):
        """USD invoice, EUR savings side: value in USD (transaction
        currency), quantity in EUR. Both non-dyadic floats through
        a 4-decimal FX rate."""
        gc = GnuCashBook(str(multi_currency_book))
        # $118.75 at rate 1.0825 = 109.70 EUR.
        result = gc.create_transaction(
            description="FX transfer with rate 1.0825",
            splits=[
                {"account": "Assets:Checking", "amount": -118.75, "quantity": -118.75},
                {"account": "Assets:Euro Savings", "amount": 118.75, "quantity": 109.70},
            ],
            trans_date=date(2026, 4, 23),
            currency="USD",
            check_duplicates=False,
        )
        assert result["status"] == "created"


# ── Scalar-param tools: belt-and-suspenders via _to_decimal ───────────


class TestAuditLogNormalizesPydanticModels:
    """Live-test regression: when FastMCP decodes a tool call, it
    hands ``create_transaction`` live ``SplitInput`` instances — not
    dicts. The audit decorator used to capture those raw kwargs into
    ``entry["params"]`` and then ``json.dumps`` them for the debug
    log, which crashed with ``TypeError: Object of type SplitInput is
    not JSON serializable``. The normalizer inside ``@audit_log``
    must convert models to plain dicts before any serialization.

    These tests simulate the production path by calling the tool
    callable with ``SplitInput`` instances.
    """

    def _create_tool(self, book_path):
        """Register the core tools against a fresh FastMCP instance
        pointed at the given book, then hand back the
        create_transaction callable. Mirrors the production wiring:
        FastMCP → @audit_log → @safe_tool → @mcp.tool-registered
        function → book.replace_splits.
        """
        from mcp.server.fastmcp import FastMCP

        from gnucash_mcp.tools.core import register

        mcp = FastMCP("test-float")
        gc = GnuCashBook(str(book_path))
        register(mcp, lambda: gc)
        # FastMCP stores the registered tools with their wrapped
        # function as ``.fn``. Reach in so we can exercise the full
        # decorator stack without actually speaking MCP.
        return mcp._tool_manager._tools["replace_splits"].fn

    def test_create_with_splitinput_instances(self, test_book: Path):
        """SplitInput models from the MCP layer must round-trip
        through @audit_log + @safe_tool without serialization errors,
        and produce a real transaction."""
        import json as _json_lib

        gc = GnuCashBook(str(test_book))
        seed = gc.create_transaction(
            description="Non-dyadic via MCP path",
            splits=[
                {"account": "Expenses:Groceries", "amount": "94.87"},
                {"account": "Assets:Checking", "amount": "-94.87"},
            ],
        )
        replace = self._create_tool(test_book)
        result_json = replace(
            guid=seed["guid"],
            splits=[
                SplitInput(account="Expenses:Groceries", amount=94.87),
                SplitInput(account="Assets:Checking", amount=-94.87),
            ],
        )
        result = _json_lib.loads(result_json)
        # If the pydantic leak regressed, result would carry
        # {"error": "... SplitInput is not JSON serializable"}.
        assert "error" not in result, result
        assert result["status"] == "splits_replaced"

    def test_error_path_with_splitinput_instances(self, test_book: Path):
        """The audit decorator's error path also normalizes params —
        an unbalanced SplitInput set must serialize into the error
        entry, not crash it (the original live-test crash was
        upstream of the write; guard the non-write path too)."""
        import json as _json_lib

        gc = GnuCashBook(str(test_book))
        seed = gc.create_transaction(
            description="Error path via MCP",
            splits=[
                {"account": "Expenses:Groceries", "amount": "10.00"},
                {"account": "Assets:Checking", "amount": "-10.00"},
            ],
        )
        replace = self._create_tool(test_book)
        result_json = replace(
            guid=seed["guid"],
            splits=[
                SplitInput(account="Expenses:Groceries", amount=94.87),
                SplitInput(account="Assets:Checking", amount=-40.00),
            ],
        )
        result = _json_lib.loads(result_json)
        assert "error" in result
        assert "SplitInput" not in result["error"]


class TestScalarAmountsAcceptFloat:
    """The scalar monetary params (amount, price, quantity,
    statement_balance, etc.) are declared str at the tool layer, so
    pydantic will coerce in practice. But direct callers can still
    hand the book method a float — `_to_decimal` guards those too."""

    def test_set_budget_amount_accepts_float(self, budget_book: Path):
        gc = GnuCashBook(str(budget_book))
        gc.create_budget(name="TestBudget", year=2026, num_periods=12)
        result = gc.set_budget_amount(
            budget_name="TestBudget",
            account="Expenses:Groceries",
            amount=94.87,  # bare float
            period=0,
        )
        assert result["status"] == "updated"

    def test_create_price_accepts_float(self, investment_book: Path):
        gc = GnuCashBook(str(investment_book))
        result = gc.create_price(
            commodity="VTSAX",
            namespace="FUND",
            value=128.33,  # bare float — historical NAV
            currency="USD",
            price_date=date(2026, 3, 15),
        )
        assert result["status"] in ("created", "updated")


class TestIsMarketPriceHelper:
    """Contract tests for the ``_is_market_price`` predicate.

    Centralizing the ``type='transaction'`` check used to live as
    inline conditionals in four places (core's ``_rates_as_of`` and
    ``_collect_warnings``, reporting's ``_latest_market_rates``,
    business's ``_find_exchange_rate``). All four now route through
    this single predicate so adding any future placeholder type
    (e.g., the auto-fx-account work later in this branch) only needs
    one change.
    """

    def test_user_quote_is_market(self):
        from gnucash_mcp.book._base import _is_market_price

        class _Price:
            type = "nav"
        assert _is_market_price(_Price()) is True

    def test_transaction_placeholder_is_not_market(self):
        from gnucash_mcp.book._base import _is_market_price

        class _Price:
            type = "transaction"
        assert _is_market_price(_Price()) is False

    def test_missing_type_attr_treated_as_market(self):
        """Defensive: an ORM row without ``type`` shouldn't be
        silently skipped — better to value it than to under-count."""
        from gnucash_mcp.book._base import _is_market_price

        class _Price:
            pass
        assert _is_market_price(_Price()) is True
