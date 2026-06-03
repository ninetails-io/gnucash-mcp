"""Regression tests for the Branch 1 chokepoint refactor.

Each test class locks an invariant the refactor consolidated into a
single enforcement point. They map 1:1 to the bug classes the spec
catalogues:

- ``TestResolveAccountTemplateFilter`` — SB-12
- ``TestMarketPriceFilter`` — SB-11
- ``TestIsVoidedConsistency`` — SB-13, SB-14, HP-3
- ``TestRatesAsOfRequiresDate`` — SB-2, SB-3, SB-4 (added in commit 4)
- ``TestNetWorthSeriesPerBoundaryRates`` — SB-1 (added in commit 5)

If any of these tests starts failing without an intentional change to
the chokepoint, the bug class is open again.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import piecash
import pytest

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.book._base import _is_voided


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
