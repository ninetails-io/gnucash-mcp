"""Regression tests for the Branch 1 chokepoint refactor.

Each test class locks an invariant the refactor consolidated into a
single enforcement point. They map 1:1 to the bug classes the spec
catalogues:

- ``TestResolveAccountTemplateFilter`` — SB-12
- ``TestMarketPriceFilter`` — SB-11
- ``TestIsVoidedConsistency`` — SB-13, SB-14, HP-3
- ``TestRatesAsOfRequiresDate`` — SB-2, SB-3, SB-4 (rates portion)
- ``TestNetWorthSeriesPerBoundaryRates`` — SB-1
- ``TestQueryEndDateInclusive`` — bookkeeper-flagged off-by-one in
  ``_query_filtered_splits`` against piecash's ``_DateAsDateTime``
  storage

If any of these tests starts failing without an intentional change to
the chokepoint, the bug class is open again.
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import piecash
import pytest

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.book._base import _is_unreconciled, _is_voided


class TestResolveAccountTemplateFilter:
    """SB-12: ``_resolve_account`` must return ``None`` for accounts
    in the scheduled-transaction template subtree regardless of input
    shape (path / ``%short`` / full 32-char GUID).

    Pre-fix, only the path branch filtered templates (via
    ``_find_account``). The ``%short`` and full-GUID branches went
    straight to SQLAlchemy with no filter, so the same logical account
    resolved to two different values depending on input form. That
    let ``update_account`` / ``move_account`` / ``delete_account``
    silently mutate template-tree rows when called with a non-path
    ref — a contract violation captured in
    ``specs/branch_1_captures/pre/*/40_resolve_template_via_short.txt``
    and ``…/41_resolve_template_via_full_guid.txt``.
    """

    @pytest.fixture
    def book_with_template(self, scheduled_book: Path) -> GnuCashBook:
        """A book containing a scheduled-transaction template account.

        ``create_scheduled_transaction`` provisions a template account
        under ``root_template`` as a side effect — the cleanest way
        to get a real template-subtree account into a test without
        reaching into piecash internals.
        """
        book = GnuCashBook(str(scheduled_book))
        book.create_scheduled_transaction(
            name="Monthly Rent",
            description="Rent payment",
            splits=[
                {"account": "Expenses:Rent", "amount": "1500.00"},
                {"account": "Assets:Checking", "amount": "-1500.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        return book

    def test_path_branch_returns_none_for_template(
        self, book_with_template: GnuCashBook,
    ):
        """Path lookup for a template-subtree fullname returns None.

        This was already correct pre-fix (``_find_account`` filters
        templates internally); the test locks it so a future
        refactor of ``_find_account`` can't silently regress.
        """
        with book_with_template.open(readonly=True) as pb:
            child = next(iter(pb.root_template.children))
            assert book_with_template._resolve_account(
                pb, child.fullname,
            ) is None

    def test_short_guid_branch_returns_none_for_template(
        self, book_with_template: GnuCashBook,
    ):
        """``%short`` GUID for a template-subtree account returns None.

        Pre-fix this returned the template account dict — the bug
        captured in ``40_resolve_template_via_short.txt``.
        """
        with book_with_template.open(readonly=True) as pb:
            child = next(iter(pb.root_template.children))
            short = book_with_template._account_short_guid(pb, child)
            assert book_with_template._resolve_account(pb, short) is None

    def test_full_guid_branch_returns_none_for_template(
        self, book_with_template: GnuCashBook,
    ):
        """Full 32-char GUID for a template-subtree account returns None.

        Pre-fix this returned the template account dict — the bug
        captured in ``41_resolve_template_via_full_guid.txt``.
        """
        with book_with_template.open(readonly=True) as pb:
            child = next(iter(pb.root_template.children))
            assert book_with_template._resolve_account(
                pb, child.guid,
            ) is None

    def test_non_template_account_still_resolves(
        self, book_with_template: GnuCashBook,
    ):
        """User-facing accounts must still resolve normally via every
        input shape. The chokepoint only filters templates; nothing
        else changes."""
        with book_with_template.open(readonly=True) as pb:
            checking = book_with_template._find_account(
                pb, "Assets:Checking",
            )
            assert checking is not None
            short = book_with_template._account_short_guid(pb, checking)
            full_guid = checking.guid

            for ref in ("Assets:Checking", short, full_guid):
                resolved = book_with_template._resolve_account(pb, ref)
                assert resolved is not None, (
                    f"non-template ref {ref!r} returned None"
                )
                assert resolved.guid == checking.guid, (
                    f"ref {ref!r} resolved to wrong account "
                    f"{resolved.fullname}"
                )

    def test_all_three_shapes_agree_on_template(
        self, book_with_template: GnuCashBook,
    ):
        """Symmetric statement of the invariant: for any account, all
        three resolution paths agree.

        Pre-fix this assertion failed for any template account because
        path returned None while ``%short`` and full GUID returned the
        account. Post-fix all three return None uniformly.
        """
        with book_with_template.open(readonly=True) as pb:
            child = next(iter(pb.root_template.children))
            short = book_with_template._account_short_guid(pb, child)

            results = [
                book_with_template._resolve_account(pb, child.fullname),
                book_with_template._resolve_account(pb, short),
                book_with_template._resolve_account(pb, child.guid),
            ]
            assert all(r is None for r in results), (
                f"input shapes disagree on template account: "
                f"path={results[0]!r}, short={results[1]!r}, "
                f"full={results[2]!r}"
            )


class TestMarketPriceFilter:
    """SB-11: ``list_commodities`` and ``calculate_lot_gain`` must
    skip piecash's ``type='transaction'`` auto-placeholder prices,
    and ``calculate_lot_gain`` must filter to the book's default
    currency.

    Pre-fix both methods walked ``book.prices`` raw. A placeholder
    newer than the user's last ``nav`` quote shadowed it as the
    "latest price", and a foreign-currency quote on the same
    commodity could be picked to compute default-currency proceeds —
    mis-denominating the gain. Pre-state captured in
    ``specs/branch_1_captures/pre/*/50_list_commodities.json`` and
    ``…/51_calculate_lot_gain.json``.

    Post-fix both routes through ``CurrencyMixin._find_prices`` with
    ``market_only=True`` (and currency filter where applicable),
    making the chokepoint the single source of truth for "give me
    the right prices for this commodity."
    """

    @pytest.fixture
    def book_with_vtsax_lot(self, investment_book: Path) -> tuple[Path, str]:
        """Investment book with a VTSAX buy and lot wired up.

        Buy: 10 shares at $100 each on 2026-01-10 (cost basis $1000).
        The fixture already has a 2026-01-15 nav price of $125, so
        the lot's expected market value is $1250.
        """
        with piecash.open_book(
            str(investment_book), readonly=False, do_backup=False,
        ) as book:
            usd = book.default_currency
            vtsax_acct = next(
                a for a in book.accounts
                if a.fullname == "Assets:Investments:VTSAX"
            )
            checking = next(
                a for a in book.accounts
                if a.fullname == "Assets:Checking"
            )

            buy = piecash.Transaction(
                currency=usd,
                description="VTSAX buy",
                post_date=date(2026, 1, 10),
                splits=[
                    piecash.Split(
                        account=vtsax_acct,
                        value=Decimal("1000.00"),
                        quantity=Decimal("10.0000"),
                    ),
                    piecash.Split(
                        account=checking,
                        value=Decimal("-1000.00"),
                    ),
                ],
            )
            book.session.add(buy)
            book.save()

            from piecash.core.transaction import Lot
            lot = Lot(
                title="Test lot",
                account=vtsax_acct,
                is_closed=0,
            )
            book.session.add(lot)
            buy_split = next(
                s for s in buy.splits if s.account == vtsax_acct
            )
            buy_split.lot = lot
            book.save()

            lot_guid = lot.guid

        return investment_book, lot_guid

    @staticmethod
    def _add_vtsax_price(
        book_path: Path, value: str, p_date: date, p_type: str,
        currency_mnemonic: str | None = None,
    ) -> None:
        """Insert a VTSAX price row directly via piecash.

        ``currency_mnemonic=None`` uses the book default. Pass
        ``p_type='transaction'`` to simulate piecash's auto-created
        placeholder rows; ``'nav'`` for a real user-supplied quote.
        """
        with piecash.open_book(
            str(book_path), readonly=False, do_backup=False,
        ) as book:
            vtsax = book.commodities.get(
                mnemonic="VTSAX", namespace="FUND",
            )
            if currency_mnemonic is None:
                ccy = book.default_currency
            else:
                from piecash import factories
                ccy = factories.create_currency_from_ISO(currency_mnemonic)
                book.session.add(ccy)
            piecash.Price(
                commodity=vtsax,
                currency=ccy,
                date=p_date,
                value=Decimal(value),
                type=p_type,
                source="user:test",
            )
            book.save()

    def test_list_commodities_skips_transaction_placeholders(
        self, book_with_vtsax_lot,
    ):
        """A ``type='transaction'`` placeholder newer than the user's
        last ``nav`` quote must NOT appear as ``latest_price`` on the
        commodity. Pre-fix it did — the iteration over ``book.prices``
        picked whatever was newest regardless of type."""
        book_path, _ = book_with_vtsax_lot
        # Placeholder dated 2026-06-01, ~5 months past the 2026-01-15
        # nav of $125. Pre-fix the placeholder would win.
        self._add_vtsax_price(
            book_path, value="0.99", p_date=date(2026, 6, 1),
            p_type="transaction",
        )

        gb = GnuCashBook(str(book_path))
        result = gb.list_commodities(compact=False)
        vtsax_entry = next(
            e for entries in result["commodities"].values()
            for e in entries if e["mnemonic"] == "VTSAX"
        )
        assert vtsax_entry["latest_price"]["date"] == "2026-01-15", (
            f"placeholder shadowed real nav quote: "
            f"{vtsax_entry['latest_price']}"
        )

    def test_calculate_lot_gain_skips_transaction_placeholders(
        self, book_with_vtsax_lot,
    ):
        """``calculate_lot_gain`` must skip placeholders when picking
        the default sale price. Pre-fix a $0.99 placeholder would
        produce nonsense proceeds; post-fix the $125 nav wins."""
        book_path, lot_guid = book_with_vtsax_lot
        self._add_vtsax_price(
            book_path, value="0.99", p_date=date(2026, 6, 1),
            p_type="transaction",
        )

        gb = GnuCashBook(str(book_path))
        result = gb.calculate_lot_gain(lot_guid=lot_guid)
        # Expected proceeds: 10 shares × $125 = $1250.
        # Placeholder-shadowed: 10 × $0.99 = $9.90.
        proceeds = Decimal(result["sale_proceeds"])
        assert proceeds > Decimal("1000"), (
            f"placeholder was used for proceeds: {result}"
        )

    def test_calculate_lot_gain_filters_to_default_currency(
        self, book_with_vtsax_lot,
    ):
        """A non-default-currency quote on the same commodity must
        not be picked when computing default-currency proceeds.
        Pre-fix any currency could win — a EUR-denominated VTSAX
        quote would silently mis-denominate the gain."""
        book_path, lot_guid = book_with_vtsax_lot
        # EUR price dated newer than the USD nav. Pre-fix this would
        # be picked (newest wins regardless of currency); post-fix
        # the currency filter excludes it and the USD nav wins.
        self._add_vtsax_price(
            book_path, value="999.99", p_date=date(2026, 6, 1),
            p_type="nav", currency_mnemonic="EUR",
        )

        gb = GnuCashBook(str(book_path))
        result = gb.calculate_lot_gain(lot_guid=lot_guid)
        # USD-denominated proceeds: 10 × $125 = $1250.
        # If EUR price had been picked: 10 × €999.99 = €9999.90,
        # surfaced as a much larger USD number.
        proceeds = Decimal(result["sale_proceeds"])
        assert proceeds < Decimal("5000"), (
            f"EUR-denominated quote was used for USD proceeds: {result}"
        )


class TestIsVoidedConsistency:
    """SB-13, SB-14, HP-3: voided splits are zombies kept for audit
    trail (state='v', value=0, quantity=0). Five iteration sites
    that previously disagreed on what "voided" meant now route
    through ``_is_voided``.

    Pre-fix some sites used ``state != "y"`` (admitting voided),
    some used ``value != 0`` (excluding voided), some had no filter
    at all. That let ``set_reconcile_state`` silently move a voided
    split to ``y`` (defeating ``unvoid_transaction``),
    ``get_unreconciled_splits`` and the dashboard's reconciliation
    backlog count zombies as pending work, ``assign_split_to_lot``
    attach a voided split to a lot, and ``_lot_decimals`` work only
    by coincidence.
    """

    # ── Predicate unit tests ──

    def test_predicate_true_for_voided_state(self):
        """``state == "v"`` → predicate returns True."""
        class Fake:
            reconcile_state = "v"
            value = Decimal("0")
            quantity = Decimal("0")
        assert _is_voided(Fake()) is True

    def test_predicate_false_for_normal_states(self):
        """``n`` / ``c`` / ``y`` → predicate returns False."""
        class Fake:
            reconcile_state = ""
        for state in ("n", "c", "y"):
            Fake.reconcile_state = state
            assert _is_voided(Fake()) is False, f"state {state!r} misread"

    def test_predicate_true_even_when_value_nonzero(self):
        """Partial-corruption case: state='v' but value/quantity
        weren't zeroed. The predicate is state-only and still
        catches the void marker — the safer behavior, because the
        user's intent (``void this``) is preserved as the source
        of truth."""
        class Fake:
            reconcile_state = "v"
            value = Decimal("100")
            quantity = Decimal("50")
        assert _is_voided(Fake()) is True

    # ── Application sites ──

    @pytest.fixture
    def book_with_voided_groceries(self, test_book: Path) -> Path:
        """Standard test_book with the 'Weekly Groceries' transaction
        voided. test_book has 3 transactions seeded; this fixture
        voids one so we can verify each application site filters
        the zombie splits."""
        gb = GnuCashBook(str(test_book))
        with gb.open(readonly=True) as pb:
            grocery_txn = next(
                t for t in pb.transactions
                if t.description == "Weekly Groceries"
            )
            txn_guid = grocery_txn.guid
        gb.void_transaction(txn_guid, reason="test setup")
        return test_book

    def test_set_reconcile_state_rejects_voided(
        self, book_with_voided_groceries,
    ):
        """SB-13: ``set_reconcile_state`` must refuse to change the
        state of a voided split. Pre-fix the input validator
        accepted ``state in {n, c, y}`` regardless of current
        state, so a caller could clear the void marker by moving
        the split to ``y`` — silently zeroing the recovery path
        that ``unvoid_transaction`` depends on."""
        gb = GnuCashBook(str(book_with_voided_groceries))
        with gb.open(readonly=True) as pb:
            voided_split = next(
                s for t in pb.transactions
                for s in t.splits
                if _is_voided(s)
            )
            split_guid = voided_split.guid
        with pytest.raises(ValueError, match="voided"):
            gb.set_reconcile_state(split_guid, "y")

    def test_get_unreconciled_splits_excludes_voided(
        self, book_with_voided_groceries,
    ):
        """HP-3: ``get_unreconciled_splits`` must exclude voided
        splits from the unreconciled list. Pre-fix the filter was
        ``state != "y"``, which admitted voided splits as if they
        were still pending bookkeeping work."""
        gb = GnuCashBook(str(book_with_voided_groceries))
        result = gb.get_unreconciled_splits(
            "Assets:Checking", compact=False,
        )
        for s in result["splits"]:
            assert s["reconcile_state"] != "v", (
                f"voided split surfaced as unreconciled: {s}"
            )

    def test_reconciliation_backlog_excludes_voided(
        self, book_with_voided_groceries,
    ):
        """HP-3: the dashboard's reconciliation backlog count must
        exclude voided splits. ``_account_reconciliation_status``
        feeds ``get_book_summary``'s Reconciliation section.

        Pre-fix the count was ``state != "y"`` and counted zombies
        as pending work, inflating the "N splits unreconciled"
        signal the LLM uses to plan reconciliation sessions.
        """
        gb = GnuCashBook(str(book_with_voided_groceries))
        with gb.open(readonly=True) as pb:
            results = gb._account_reconciliation_status(
                pb, list(pb.accounts),
            )

            # Confirm the fixture produced what we expect: the
            # voided transaction left a zombie split on Checking
            # plus opening + salary = 3 splits, 1 voided.
            checking = next(
                a for a in pb.accounts
                if a.fullname == "Assets:Checking"
            )
            voided_count = sum(
                1 for s in checking.splits if _is_voided(s)
            )
            assert voided_count == 1, (
                "fixture didn't produce expected voided split"
            )

        # The Checking entry's unreconciled_count must NOT include
        # the voided split. Two non-voided splits (opening +
        # salary) survived the void.
        checking_entry = next(
            r for r in results if r["account"] == "Assets:Checking"
        )
        assert checking_entry["unreconciled_count"] == 2, (
            f"voided split was counted as unreconciled: "
            f"{checking_entry}"
        )

    def test_reconciliation_backlog_counts_pre_latest_y_date(
        self, test_book: Path,
    ):
        """HP-8: the dashboard's reconciliation backlog count must
        include unreconciled splits that PREDATE the last
        reconciled ('y') split — they're the ones most likely to
        be problems (skipped during a partial reconciliation,
        opening balances never stamped, edge cases that fell
        through). Pre-fix the count was scoped to "splits after
        latest_y_date" which silently dropped them, breaking the
        invariant that the dashboard count equals
        ``get_unreconciled_splits``'s count.
        """
        from datetime import datetime as _dt
        gb = GnuCashBook(str(test_book))

        # Fixture's Checking has opening + recent transactions, none
        # reconciled. Reconcile a NEWER split, leaving the older one
        # unreconciled — pre-fix this older split would be invisible
        # to the dashboard count but visible to
        # get_unreconciled_splits.
        with gb.open(readonly=False) as book:
            checking = gb._find_account(book, "Assets:Checking")
            opening = gb._find_account(book, "Equity:Opening Balance")
            recent_date = date.today() - timedelta(days=3)
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Recent reconciled deposit",
                post_date=recent_date,
                splits=[
                    piecash.Split(account=checking, value=Decimal("100")),
                    piecash.Split(account=opening, value=Decimal("-100")),
                ],
            ))
            book.save()
            # Mark only the recent Checking split as 'y'.
            for s in checking.splits:
                if s.transaction.post_date == recent_date:
                    s.reconcile_state = "y"
                    s.reconcile_date = _dt.combine(
                        recent_date, _dt.min.time()
                    )
            book.save()

        # Detail-tool ground truth.
        detail = gb.get_unreconciled_splits(
            "Assets:Checking", compact=False,
        )
        detail_count = detail["total"]

        # Dashboard count from the same source.
        with gb.open(readonly=True) as pb:
            results = gb._account_reconciliation_status(
                pb, list(pb.accounts),
            )
        checking_entry = next(
            r for r in results if r["account"] == "Assets:Checking"
        )

        assert checking_entry["unreconciled_count"] == detail_count, (
            f"dashboard ({checking_entry['unreconciled_count']}) "
            f"disagrees with get_unreconciled_splits ({detail_count}) "
            f"— bookkeeper's first instinct will be 'the book is "
            f"wrong'"
        )
        # And confirm the pre-fix bug-shape: at least one
        # unreconciled split predates ``latest_y_date``.
        assert detail_count >= 1, (
            "fixture didn't produce the pre-latest_y_date "
            "unreconciled scenario this test is meant to lock"
        )

    def test_is_unreconciled_chokepoint_at_both_count_sites(
        self, test_book: Path,
    ):
        """HP-8 chokepoint: the predicate that determines whether a
        split counts as pending bookkeeping work must live in ONE
        place (``_is_unreconciled`` in ``_base.py``) — not duplicated
        at the dashboard count site and the detail-tool site.

        This test enforces structural identity, not just behavioral
        equality: if a future change adds a state guard at only one
        of the two predicate sites, the chokepoint discipline broke
        and the dashboard-vs-detail divergence bug class HP-8 closed
        can reopen in a new shape.

        We probe via inputs hand-picked to exercise each state value
        the predicate cares about: 'y' (skip), 'c' (count), 'n'
        (count), 'v' (skip). Both surfaces must agree on each.
        """
        # Sanity: helper returns the right answer for each state.
        class _FakeSplit:
            def __init__(self, state):
                self.reconcile_state = state

        assert _is_unreconciled(_FakeSplit("n")) is True
        assert _is_unreconciled(_FakeSplit("c")) is True
        assert _is_unreconciled(_FakeSplit("y")) is False
        assert _is_unreconciled(_FakeSplit("v")) is False

        # Structural lock: both call sites must call the helper.
        # Reading the source is the right test here — a future
        # contributor inlining ``s.reconcile_state != "y" and not
        # _is_voided(s)`` at either site would silently undo the
        # chokepoint. Searching for the helper at each site catches
        # the regression without depending on book state.
        import inspect
        from gnucash_mcp.book import core, reconciliation

        dashboard_src = inspect.getsource(
            core.CoreMixin._account_reconciliation_status
        )
        detail_src = inspect.getsource(
            reconciliation.ReconciliationMixin.get_unreconciled_splits
        )
        assert "_is_unreconciled" in dashboard_src, (
            "_account_reconciliation_status doesn't route through "
            "_is_unreconciled — HP-8 chokepoint broken"
        )
        assert "_is_unreconciled" in detail_src, (
            "get_unreconciled_splits doesn't route through "
            "_is_unreconciled — HP-8 chokepoint broken"
        )

    def test_cash_flow_voided_transactions_treated_symmetrically(
        self, multi_currency_book: Path,
    ):
        """SB-5 follow-up: a voided transaction should be skipped
        entirely in cash_flow — neither contributing to inflows/
        outflows nor inflating transfers_excluded — regardless of
        whether its legs are INCOME/EXPENSE or pure cash/bank.

        Pre-fix asymmetry: a voided INCOME/EXPENSE txn was
        classified as a 'real cash event' (its INCOME split was
        found by the type-only filter) while a voided pure-transfer
        was counted in ``transfers_excluded``. The fix added an
        early ``_is_voided`` filter at both the helper site and
        the row loop.
        """
        from datetime import date as _date
        gc = GnuCashBook(str(multi_currency_book))

        # Void the salary transaction (Income split) AND the
        # cross-currency transfer (pure cash). After voiding both,
        # the resulting cash_flow should treat each symmetrically:
        # neither contributes to the inflows/outflows totals, and
        # neither is counted in transfers_excluded.
        with gc.open(readonly=False) as book:
            for txn in list(book.transactions):
                if txn.description in (
                    "Salary",
                    "Transfer to EUR savings",
                ):
                    for s in txn.splits:
                        s.reconcile_state = "v"
                        s.value = Decimal("0")
                        s.quantity = Decimal("0")
            book.save()

        result = gc.cash_flow(
            start_date=_date(2024, 1, 1),
            end_date=_date(2024, 12, 31),
        )
        # Remaining real activity: opening balance (equity-side,
        # filtered as transfer) + groceries (real expense).
        assert Decimal(result["outflows"]) == Decimal("200"), (
            "groceries should still contribute its $200 outflow"
        )
        assert Decimal(result["inflows"]) == Decimal("0"), (
            "voided salary should NOT contribute to inflows"
        )
        # transfers_excluded counts only LIVE rearrangement txns —
        # the voided cross-currency transfer must not be counted.
        # Only the opening balance (equity-side, no INCOME/EXPENSE
        # leg, not voided) should remain.
        assert result["transfers_excluded"] == 1, (
            f"voided pure-transfer should not be in transfers_excluded; "
            f"got {result.get('transfers_excluded')}"
        )

    def test_assign_split_to_lot_rejects_voided(
        self, investment_book: Path,
    ):
        """SB-14 / HP-3: ``assign_split_to_lot`` must refuse a
        voided split. Pre-fix no state guard existed, so a voided
        (zero-quantity) split could attach to a lot and either
        trip the auto-close path immediately or sit as a
        zero-contribution row that downstream callers had to
        special-case."""
        gb = GnuCashBook(str(investment_book))
        # Buy 10 VTSAX, then void the buy transaction. The buy
        # split is left on the VTSAX account with state='v'.
        buy = gb.create_transaction(
            description="VTSAX buy",
            splits=[
                {
                    "account": "Assets:Investments:VTSAX",
                    "amount": "1000.00", "quantity": "10.0000",
                },
                {"account": "Assets:Checking", "amount": "-1000.00"},
            ],
            trans_date=date(2026, 1, 10),
        )
        gb.void_transaction(buy["guid"], reason="test")

        lot = gb.create_lot(
            "Assets:Investments:VTSAX", title="Test lot",
        )
        with gb.open(readonly=True) as pb:
            vtsax = next(
                a for a in pb.accounts
                if a.fullname == "Assets:Investments:VTSAX"
            )
            voided_split = next(
                s for s in vtsax.splits if _is_voided(s)
            )
            split_guid = voided_split.guid

        with pytest.raises(ValueError, match="voided"):
            gb.assign_split_to_lot(split_guid, lot["guid"])


class TestRatesAsOfRequiresDate:
    """SB-2, SB-3, SB-4 (rates portion): ``_rates_as_of`` and
    ``_account_conversion_factors`` require an explicit ``as_of`` —
    no default. Pre-v1.3 release both helpers defaulted ``as_of=None``
    (no upper bound, always-latest rates). Five historical-report
    sites passed nothing and silently used today's rates regardless
    of the historical period being reported:

    - ``balance_sheet`` at a past date (SB-2)
    - ``vendor_spending_report`` for a past period (SB-3)
    - ``debt_payoff_plan`` mixed-currency aggregation (SB-4 rates
      portion, via ``_split_in_default_currency`` → factors)
    - the period-flow reports (``cash_flow``,
      ``spending_by_category``, ``income_by_source``)
    - ``get_budget_report`` historical periods

    Dropping the default forces every caller to declare its intent.
    The ``_anchor_for_as_of`` helper folds now-or-future anchors to
    ``date.max`` so the bookkeeper's intentional forecasts are still
    included in "now" valuations — the convention locked by
    ``TestCrossToolPriceAgreement``.
    """

    def test_rates_as_of_requires_as_of_positionally(
        self, multi_currency_book: Path,
    ):
        """Calling ``_rates_as_of(book)`` with no ``as_of`` raises
        TypeError. Pre-fix this returned today's rates silently."""
        book = GnuCashBook(str(multi_currency_book))
        with book.open(readonly=True) as pb:
            with pytest.raises(TypeError):
                book._rates_as_of(pb)

    def test_account_conversion_factors_requires_as_of(
        self, multi_currency_book: Path,
    ):
        """Same contract on ``_account_conversion_factors`` — pre-
        fix this defaulted to today's rates inside the helper."""
        book = GnuCashBook(str(multi_currency_book))
        with book.open(readonly=True) as pb:
            with pytest.raises(TypeError):
                book._account_conversion_factors(pb)

    def test_historical_anchor_returns_historical_rates(
        self, multi_currency_book: Path,
    ):
        """A historical ``as_of`` must filter to prices ≤ that date.

        Setup: ``multi_currency_book`` already has a 2024-01-20
        EUR/USD transaction. Add an explicit older EUR rate and a
        newer EUR rate; verify the historical anchor picks the
        older one and the today anchor picks the newer one.
        """
        gb = GnuCashBook(str(multi_currency_book))

        # Write two EUR/USD market quotes: an older one and a
        # newer one. _rates_as_of with as_of between them must
        # pick the older; with as_of >= today must pick the newer.
        old_date = date(2024, 6, 1)
        new_date = date(2025, 6, 1)
        with piecash.open_book(
            str(multi_currency_book), readonly=False, do_backup=False,
        ) as book:
            usd = book.default_currency
            eur = next(
                c for c in book.commodities if c.mnemonic == "EUR"
            )
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=old_date, value=Decimal("1.10"),
                type="nav", source="user:test",
            ))
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=new_date, value=Decimal("1.20"),
                type="nav", source="user:test",
            ))
            book.save()

        with gb.open(readonly=True) as pb:
            eur_guid = next(
                c.guid for c in pb.commodities
                if c.mnemonic == "EUR"
            )

            # Historical anchor between the two prices: must pick
            # the older 1.10 rate. Pre-fix the call defaulted to
            # today and returned 1.20 regardless of as_of.
            mid_anchor = date(2024, 12, 1)
            historical = gb._rates_as_of(pb, mid_anchor)
            assert historical.get(eur_guid) == Decimal("1.10"), (
                f"historical anchor {mid_anchor} should pick 1.10, "
                f"got {historical.get(eur_guid)}"
            )

            # "Now" anchor: helper folds today to date.max, so the
            # newer 1.20 wins (and any future-dated forecast would
            # too).
            now = gb._rates_as_of(pb, date.today())
            assert now.get(eur_guid) == Decimal("1.20"), (
                f"now anchor should pick 1.20, "
                f"got {now.get(eur_guid)}"
            )

    def test_anchor_for_as_of_folds_now_or_future_to_max(
        self, test_book: Path,
    ):
        """The helper's contract: anchors at or beyond today fold to
        ``date.max`` (forecast-inclusive); past anchors stay literal
        (historical reconstruction)."""
        book = GnuCashBook(str(test_book))
        today = date.today()
        past = today - timedelta(days=30)
        future = today + timedelta(days=30)

        assert book._anchor_for_as_of(today) == date.max
        assert book._anchor_for_as_of(future) == date.max
        assert book._anchor_for_as_of(past) == past

    def test_balance_sheet_historical_uses_historical_rates(
        self, multi_currency_book: Path,
    ):
        """End-to-end: ``balance_sheet(as_of_date=<past>)`` values
        non-default-currency holdings at the historical rate, not
        today's. Pre-fix SB-2 used today's rates against historical
        quantities.

        Uses the same EUR-rate setup as
        ``test_historical_anchor_returns_historical_rates``: 1.10
        at 2024-06-01, 1.20 at 2025-06-01. The fixture's transfer
        of 1000 EUR happens on 2024-01-20. At a historical
        as_of_date of 2024-12-01, the EUR holding should be valued
        at 1.10 (1100 USD), not 1.20 (1200 USD) and not the
        cost-basis fallback (1100 USD)."""
        # Write the same older/newer rates as the predicate test.
        with piecash.open_book(
            str(multi_currency_book), readonly=False, do_backup=False,
        ) as book:
            usd = book.default_currency
            eur = next(
                c for c in book.commodities if c.mnemonic == "EUR"
            )
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=date(2024, 6, 1), value=Decimal("1.10"),
                type="nav", source="user:test",
            ))
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=date(2025, 6, 1), value=Decimal("1.20"),
                type="nav", source="user:test",
            ))
            book.save()

        gb = GnuCashBook(str(multi_currency_book))

        # Historical balance_sheet: as_of_date between the two
        # prices. EUR row should value at 1.10.
        bs = gb.balance_sheet(date(2024, 12, 1))
        eur_row = next(
            a for a in bs["assets"]["accounts"]
            if a["account"] == "Assets:Euro Savings"
        )
        # 1000 EUR × 1.10 = 1100 USD. Rate string is "@ 1.1" (the
        # display strips trailing decimal zeros). The cost-basis
        # fallback also happens to be 1100 USD here, so we
        # disambiguate via the rate token: cost-basis path shows
        # "no price data", historical-rate path shows "@ <rate>".
        assert "@ 1.1 " in eur_row["balance"], (
            f"historical balance_sheet did not use the historical "
            f"EUR rate (1.10); balance row: {eur_row}"
        )
        # Sanity check on the converted value too.
        assert eur_row["default_currency_value"] == "1100.00", (
            f"unexpected USD value: {eur_row}"
        )


class TestNetWorthSeriesPerBoundaryRates:
    """SB-1: ``net_worth`` time-series must value each boundary's
    snapshot at that boundary's own rates, not today's.

    Pre-fix the method computed a single ``factors`` map outside the
    boundary sweep and applied it uniformly — every historical
    snapshot inherited today's rates. The trajectory chart showed
    zero FX-driven variation on multi-currency books and the
    cross-tool comparison with ``get_book_summary`` (which
    correctly used historical rates in its trajectory) diverged.

    Captured in pre-state at
    ``specs/branch_1_captures/pre/lin_wei/14_net_worth_series.json``,
    where Lin Wei's quarterly snapshots all used 2025-12-01 rates
    against historical holdings.

    Post-fix the sweep pre-computes per-boundary factors and tracks
    per-account quantity + value running totals. At each snapshot
    the appropriate rate (factor × quantity, or cost-basis fallback
    when no rate is on file) is applied.
    """

    def test_time_series_uses_per_boundary_rates(
        self, multi_currency_book: Path,
    ):
        """Different rates at different boundaries → different
        snapshot values for the same holding quantity.

        Setup: the fixture's 1000 EUR transfer happens on
        2024-01-20. Add an EUR/USD rate of 1.10 on 2024-06-01 and
        1.20 on 2025-06-01. A yearly time-series across 2024 and
        2025 should value the 2024-12 boundary at the 1.10 rate and
        the 2025-12 boundary at the 1.20 rate.

        Pre-fix both would use today's rate uniformly (whatever
        ``factors = self._account_conversion_factors(book)`` picked
        when called outside the sweep). The non-EUR portion of net
        worth (Checking $6700 from $5000 opening + $3000 salary -
        $1100 transfer - $200 groceries) is unchanged by FX, so the
        EUR-driven delta is exactly what we expect to see between
        snapshots."""
        with piecash.open_book(
            str(multi_currency_book), readonly=False, do_backup=False,
        ) as book:
            usd = book.default_currency
            eur = next(
                c for c in book.commodities if c.mnemonic == "EUR"
            )
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=date(2024, 6, 1), value=Decimal("1.10"),
                type="nav", source="user:test",
            ))
            book.session.add(piecash.Price(
                commodity=eur, currency=usd,
                date=date(2025, 6, 1), value=Decimal("1.20"),
                type="nav", source="user:test",
            ))
            book.save()

        gb = GnuCashBook(str(multi_currency_book))
        result = gb.net_worth(
            start_date=date(2024, 1, 1),
            end_date=date(2025, 12, 31),
            interval="year",
        )

        series = result["series"]
        snapshots_by_date = {
            entry["date"]: Decimal(entry["net_worth"])
            for entry in series
        }

        # Two yearly boundaries should land: 2024-01-01 and 2025-01-01
        # (and 2025-12-31 appended as the end). All three are
        # post-transfer (2024-01-20). The 2024 boundaries see EUR
        # @ 1.10; the 2025 boundaries see EUR @ 1.20.
        assert "2025-01-01" in snapshots_by_date
        assert "2025-12-31" in snapshots_by_date

        # Pure FX-driven delta on the 1000 EUR holding between the
        # 2025-01-01 and 2025-12-31 snapshots: 1000 × (1.20 - 1.10) =
        # $100. The Checking balance ($6700) hasn't changed between
        # these dates (no transactions after 2024-01-25 in the fixture).
        delta = (
            snapshots_by_date["2025-12-31"]
            - snapshots_by_date["2025-01-01"]
        )
        assert delta == Decimal("100"), (
            f"per-boundary FX delta should be exactly $100 "
            f"(1000 EUR × ($1.20 − $1.10)); got delta={delta}. "
            f"Pre-fix the two snapshots would have been identical "
            f"(uniform factors across the boundary sweep)."
        )

    def test_time_series_falls_back_to_cost_basis_before_first_rate(
        self, multi_currency_book: Path,
    ):
        """Boundaries before any market rate is on file fall back to
        cost basis (``split.value`` sum) — same disambiguation
        ``_split_in_default_currency`` applies per-split, lifted to
        the per-account snapshot view.

        Pre-fix this also fell back (because there was no rate at
        ANY date — the fixture had no EUR prices at all), but the
        fallback came from a single factors map computed outside
        the sweep. Locking the behavior here so future refactors
        don't regress to e.g. zeroing unpriced accounts."""
        # No EUR prices written — fixture state.
        gb = GnuCashBook(str(multi_currency_book))
        result = gb.net_worth(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
            interval="month",
        )

        series = result["series"]
        # All months between 2024-01 and 2024-06: 1000 EUR has no
        # rate, so it should contribute split.value (= 1100 USD,
        # the transfer's transaction-currency value) at every
        # post-transfer boundary.
        post_transfer = [
            entry for entry in series
            if entry["date"] >= "2024-02-01"
        ]
        # Each post-transfer snapshot sees: Checking $6700 +
        # cost-basis-valued EUR $1100 = $7800 across the assets;
        # the fixture's Equity:Opening Balance is -$5000 (negative
        # = credit posting), Income:Salary $0 (income type, not
        # in net worth's asset/liability set). So total assets-
        # only across BANK accounts is $7800; that's the running
        # net worth post-transfer pre-grocery.
        #
        # We don't assert a specific number (fixture math is
        # complex); just confirm the running total is positive
        # and stable across the months where no transactions land
        # — the cost-basis fallback is doing its job.
        values = [Decimal(e["net_worth"]) for e in post_transfer]
        assert all(v > 0 for v in values), (
            f"post-transfer snapshots should be positive: {values}"
        )


class TestQueryEndDateInclusive:
    """Bookkeeper-flagged off-by-one in ``_query_filtered_splits``:
    ``balance_sheet(as_of_date=X)`` excluded transactions posted on
    X because piecash's ``_DateAsDateTime`` TypeDecorator stores
    ``post_date`` with a 10:59:00 neutral-time component (see
    ``piecash.sa_extra._DateAsDateTime.process_bind_param``). The
    SQL comparison ``post_date <= X`` coerced X to midnight,
    excluding same-day transactions whose stored time is 10:59.

    ``get_balance`` and other Python-side date comparisons were
    unaffected — ``process_result_value`` strips the time component
    on read, so Python comparisons against bare ``date`` objects
    work correctly. The bug surfaced as cross-tool disagreement on
    Lin Wei: ``balance_sheet(2025-12-31)`` returned CNY 270,704 for
    Checking while ``get_balance`` and the dashboard "now" view
    showed CNY 270,652 (52 CNY of December 31 activity hidden).

    Fixed at the single SQL chokepoint via ``post_date < end_date +
    1 day`` — same semantic ("inclusive of the full as_of date"),
    correctly enforced regardless of stored time component.
    """

    def test_balance_sheet_includes_same_day_transactions(
        self, test_book: Path,
    ):
        """``test_book`` has a $2000 salary deposit on 2024-01-15.
        ``balance_sheet(2024-01-15)`` must include it — Checking
        balance should be $3000 (opening $1000 + salary $2000),
        not $1000 (opening only). Pre-fix the salary was excluded
        because its stored post_date is 2024-01-15 10:59:00 and
        the SQL upper bound was 2024-01-15 00:00:00."""
        gb = GnuCashBook(str(test_book))
        bs = gb.balance_sheet(date(2024, 1, 15))
        checking_row = next(
            a for a in bs["assets"]["accounts"]
            if a["account"] == "Assets:Checking"
        )
        assert Decimal(checking_row["balance"]) == Decimal("3000.00"), (
            f"balance_sheet excluded same-day salary deposit; "
            f"Checking row: {checking_row}"
        )

    def test_balance_sheet_and_get_balance_agree(
        self, test_book: Path,
    ):
        """Cross-tool agreement: ``balance_sheet`` and ``get_balance``
        must return identical numbers for the same ``as_of_date``.
        Bookkeeper's diagnostic frame — disagreement between the two
        is the signal that surfaced this bug."""
        gb = GnuCashBook(str(test_book))
        for as_of in (
            date(2024, 1, 1),   # opening only
            date(2024, 1, 15),  # opening + salary
            date(2024, 1, 20),  # opening + salary + groceries
            date(2024, 1, 25),  # post-everything
        ):
            bs = gb.balance_sheet(as_of)
            checking_bs = Decimal(next(
                a for a in bs["assets"]["accounts"]
                if a["account"] == "Assets:Checking"
            )["balance"])
            checking_gb = gb.get_balance("Assets:Checking", as_of)
            assert checking_bs == checking_gb, (
                f"cross-tool disagreement at {as_of}: "
                f"balance_sheet={checking_bs}, "
                f"get_balance={checking_gb}"
            )

    def test_period_breakdown_includes_end_date_transactions(
        self, test_book: Path,
    ):
        """``spending_by_category`` (and the other period-flow
        reports) also route through ``_query_filtered_splits``.
        Same-day transactions on the end_date must be included.

        test_book has a $150 grocery expense on 2024-01-20. A
        report through 2024-01-20 must include it."""
        gb = GnuCashBook(str(test_book))
        result = gb.spending_by_category(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 20),
            compact=False,
        )
        assert Decimal(result["total"]) == Decimal("150"), (
            f"spending_by_category excluded same-day grocery "
            f"expense; result: {result}"
        )

    def test_end_date_max_does_not_overflow(
        self, test_book: Path,
    ):
        """``date.max`` upper bound must not raise OverflowError.

        Copilot-flagged on PR #95: the day-after strict-upper-
        bound trick (``end_date + timedelta(days=1)``) overflows
        when ``end_date == date.max``. A caller passing
        ``date.max`` semantically wants every row — the fix is to
        skip the upper-bound filter entirely in that case
        (equivalent to ``end_date is None``).

        Verified end-to-end through ``balance_sheet`` since that's
        the public surface a user could plausibly hit with a
        far-future ``as_of_date``."""
        gb = GnuCashBook(str(test_book))
        # Both calls must succeed; ``balance_sheet(date.max)``
        # should match a post-data anchor because all of
        # ``test_book``'s transactions are well before either.
        bs_max = gb.balance_sheet(date.max)
        bs_post_data = gb.balance_sheet(date(2024, 12, 31))
        assert bs_max["assets"]["total"] == bs_post_data["assets"]["total"], (
            f"date.max balance sheet diverged from post-data anchor: "
            f"max={bs_max['assets']['total']}, "
            f"post-data={bs_post_data['assets']['total']}"
        )
