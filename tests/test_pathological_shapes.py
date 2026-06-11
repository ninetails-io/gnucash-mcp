"""Cross-surface regression tests against the pathological-shapes book.

The final adversarial review of v1.3.1 (specs/Code Reviews/
CODE_REVIEW_v1_3_1_ADVERSARIAL_PASS_2.md) observed that every
confirmed defect lived in a shape the synthetic sample books
structurally cannot produce. The ``pathological_book`` fixture
(conftest.py) packs those shapes into one small book; this module
runs every report surface against the combination and asserts they
agree with each other and with hand-computed totals.

Two kinds of tests live here:

- **Locks** — passing tests that hold the C1 (own-splits counting)
  and C2 (overpaid-lot direction) fixes in place against the
  combined book, not just the isolated single-shape books their
  targeted regressions use.
- **``xfail(strict=True)`` markers** — review findings C9 (native
  SX templates surfacing as real transactions) and C8 (voided
  splits touched by write paths) are documented but not yet fixed.
  Each xfail describes the expected post-fix behavior; when the fix
  lands the marker trips XPASS and must be removed, flipping the
  test into a permanent lock.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import piecash
import pytest

from gnucash_mcp.book import GnuCashBook

# Hand-computed totals for the pathological book (see the fixture
# docstring in conftest.py for the per-account arithmetic).
EXPECTED_ASSETS = Decimal("11600.00")
EXPECTED_LIABILITIES = Decimal("0.00")
EXPECTED_NET_WORTH = Decimal("11600.00")


class TestCrossSurfaceAgreement:
    """All report surfaces must agree on the same numbers even with
    every pathological shape present at once."""

    def test_balance_sheet_balances_and_includes_every_shape(
        self, pathological_book,
    ):
        gb = GnuCashBook(str(pathological_book.path))
        bs = gb.balance_sheet(as_of_date=date.today())

        assert Decimal(bs["assets"]["total"]) == EXPECTED_ASSETS
        assert Decimal(bs["liabilities"]["total"]) == EXPECTED_LIABILITIES
        # A = L + E must hold exactly — and not via a fat unrealized
        # residual silently absorbing a dropped account (the C1
        # failure mode the equity residual used to hide).
        assert (
            Decimal(bs["assets"]["total"])
            - Decimal(bs["liabilities"]["total"])
            == Decimal(bs["equity"]["total"])
        )

        rows = {a["account"]: a for a in bs["assets"]["accounts"]}
        # Parent with direct splits and a child: own splits counted.
        assert Decimal(
            rows["Assets:Checking"]["balance"]
        ) == Decimal("10200.00")
        # Placeholder with direct splits: real money, not skipped.
        assert Decimal(
            rows["Assets:Savings"]["balance"]
        ) == Decimal("500.00")
        # Overpaid receivable: negative, direction preserved.
        assert Decimal(
            rows["Assets:Accounts Receivable"]["balance"]
        ) == Decimal("-200.00")
        # Foreign-denominated A/R: triplet rendering with the
        # parseable converted amount alongside (EUR 1,000 at 1.10).
        eur_row = rows["Assets:Accounts Receivable EUR"]
        assert "EUR" in eur_row["balance"]
        assert (
            Decimal(eur_row["default_currency_value"])
            == Decimal("1100.00")
        )

    def test_net_worth_agrees_with_balance_sheet(
        self, pathological_book,
    ):
        gb = GnuCashBook(str(pathological_book.path))
        nw = gb.net_worth(end_date=date.today())
        assert Decimal(nw["net_worth"]) == EXPECTED_NET_WORTH

    def test_dashboard_now_anchor_agrees(self, pathological_book):
        """The dashboard's trajectory "now" anchor and its assets
        section must match balance_sheet / net_worth — three
        surfaces, ONE scope (C1)."""
        gb = GnuCashBook(str(pathological_book.path))
        summary = gb.get_book_summary()
        assert "now: USD 11,600" in summary, (
            f"trajectory 'now' disagrees with balance_sheet; "
            f"saw:\n{summary}"
        )
        # Parent-with-children and placeholder accounts both render
        # their own balances in the assets section.
        assert "Checking: USD 10200.00" in summary
        assert "Savings: USD 500.00" in summary

    def test_cash_flow_excludes_voided_expense(self, pathological_book):
        """The only expense transaction in the book is voided; no
        flow surface may count its zombie splits."""
        gb = GnuCashBook(str(pathological_book.path))
        flow = gb.cash_flow(
            start_date=date(2026, 1, 1), end_date=date(2026, 6, 1),
        )
        assert Decimal(flow["outflows"]) == Decimal("0")
        # Transfer-inclusive mode must also run clean over the
        # placeholder transfer and lot-assigned manual payment.
        gb.cash_flow(
            start_date=date(2026, 1, 1), end_date=date(2026, 6, 1),
            include_transfers=True,
        )

    def test_outstanding_invoices_mixed_shapes(self, pathological_book):
        """Overpaid USD invoice and open EUR invoice side by side:
        direction surfaced on one, foreign currency on the other,
        aging clock only where it belongs (C2)."""
        gb = GnuCashBook(str(pathological_book.path))
        rows = {
            r["id"]: r
            for r in gb.get_outstanding_invoices(compact=False)
        }

        overpaid = rows[pathological_book.usd_invoice_id]
        assert overpaid["overpaid"] is True
        assert Decimal(overpaid["amount_due"]) == Decimal("-200.00")
        assert Decimal(overpaid["amount_paid"]) == Decimal("700.00")
        assert overpaid["days_past_due"] is None, (
            "aging clock must not tick on an overpaid document"
        )

        eur_row = rows[pathological_book.eur_invoice_id]
        assert eur_row["currency"] == "EUR"
        assert Decimal(eur_row["amount_due"]) == Decimal("1000.00")
        assert Decimal(eur_row["amount_paid"]) == Decimal("0.00")

        assert "OVERPAID" in gb.get_outstanding_invoices(compact=True)

    def test_list_and_search_render_clean(self, pathological_book):
        """Both transaction surfaces handle the full shape mix
        without raising, in compact and verbose modes."""
        gb = GnuCashBook(str(pathological_book.path))

        verbose = gb.list_transactions(compact=False, limit=250)
        descriptions = {t["description"] for t in verbose}
        assert "Opening balance" in descriptions
        assert "Transfer to savings" in descriptions
        assert "Manual overpayment" in descriptions
        assert isinstance(
            gb.list_transactions(compact=True, limit=250), str
        )

        hits = gb.search_transactions(
            "Manual overpayment", compact=False,
        )
        assert len(hits) == 1
        assert isinstance(
            gb.search_transactions("150", field="amount"), str
        )


class TestNativeSxTemplateLeak:
    """C9 (adversarial pass 2): desktop GnuCash persists
    scheduled-transaction recipes as real Transaction rows against
    ``root_template`` accounts, and a ``template`` pseudo-commodity
    behind them. Every transaction- and commodity-iteration surface
    must filter both — a stale recipe would otherwise render
    identically to a real event. Locked via the
    ``_is_template_transaction`` chokepoint (the same rule
    ``_collect_create_signals`` already applied).
    """

    def test_template_absent_from_list_transactions(
        self, pathological_book,
    ):
        gb = GnuCashBook(str(pathological_book.path))
        verbose = gb.list_transactions(compact=False, limit=250)
        descriptions = {t["description"] for t in verbose}
        assert "Mortgage Payment" not in descriptions, (
            "SX template recipe rendered as a real transaction"
        )

    def test_template_absent_from_search_transactions(
        self, pathological_book,
    ):
        gb = GnuCashBook(str(pathological_book.path))
        assert gb.search_transactions("Mortgage", compact=False) == []
        # Amount search must not surface the template's 2,485 either.
        assert gb.search_transactions(
            "2485", field="amount", compact=False,
        ) == []

    def test_template_does_not_stretch_dashboard_dates(
        self, pathological_book,
    ):
        """The template is dated 2020-01-01; every real transaction
        is from 2026. An unfiltered dashboard reports the book's
        first activity six years early."""
        gb = GnuCashBook(str(pathological_book.path))
        summary = gb.get_book_summary()
        assert "2020-01-01" not in summary, (
            "dashboard first-activity date stretched by an SX "
            "template row"
        )

    def test_template_pseudo_commodity_hidden(self, pathological_book):
        """The ``template`` pseudo-commodity must not surface in
        list_commodities, the dashboard Commodities line, or the
        stale-price warnings (it can never have a price)."""
        gb = GnuCashBook(str(pathological_book.path))

        verbose = gb.list_commodities(compact=False)
        assert "template" not in verbose
        assert "template" not in gb.list_commodities(compact=True)

        summary = gb.get_book_summary()
        commodities_line = next(
            ln for ln in summary.splitlines()
            if ln.startswith("Commodities:")
        )
        assert "template" not in commodities_line
        assert "Stale price: template" not in summary

    def test_delete_scheduled_transaction_with_recipe_rows(
        self, scheduled_book,
    ):
        """Desktop-created SXs leave real Transaction rows on the
        template account; delete must remove the recipe rows along
        with the account instead of orphaning their splits."""
        gc = GnuCashBook(str(scheduled_book))
        created = gc.create_scheduled_transaction(
            name="Rent",
            description="Monthly Rent",
            splits=[
                {"account": "Expenses:Rent", "amount": "1850.00"},
                {"account": "Assets:Checking", "amount": "-1850.00"},
            ],
            start_date="2026-01-01",
            frequency="monthly",
        )
        # Plant a desktop-style recipe transaction on the SX's
        # template account (our splits-json design leaves it empty).
        with gc.open(readonly=False) as book:
            template_acct = next(
                a for a in book.root_template.children
            )
            book.session.add(piecash.Transaction(
                currency=book.default_currency,
                description="Monthly Rent",
                post_date=date(2026, 1, 1),
                splits=[
                    piecash.Split(
                        account=template_acct,
                        value=Decimal("1850.00"),
                    ),
                    piecash.Split(
                        account=template_acct,
                        value=Decimal("-1850.00"),
                    ),
                ],
            ))
            book.save()

        gc.delete_scheduled_transaction(guid=created["guid"])

        with gc.open(readonly=True) as book:
            assert list(book.root_template.children) == [], (
                "template account survived the SX delete"
            )
            assert not any(
                t.description == "Monthly Rent"
                for t in book.transactions
            ), "recipe transaction orphaned by the SX delete"


class TestVoidedThenTouched:
    """C8 (adversarial pass 2): voided splits are protected at every
    boundary. Write paths (``update_transaction``, ``replace_splits``,
    ``reconcile_account``, auto-fill) refuse or skip them; balance
    surfaces exclude them by **state** so the partial-void corruption
    shape (``state='v'`` with non-zero values — legacy data or
    desktop edits) cannot move money that cash_flow / lots /
    reconciliation counts can't see.

    Builds the corruption in an isolated book (NOT the shared
    pathological book — a corrupted split would poison every
    cross-surface total above).
    """

    def _voided_txn(self, gc: GnuCashBook) -> str:
        created = gc.create_transaction(
            description="Voided pattern",
            splits=[
                {"account": "Expenses:Groceries", "amount": "75.00"},
                {"account": "Assets:Checking", "amount": "-75.00"},
            ],
            trans_date=date(2026, 2, 1),
            check_duplicates=False,
        )
        gc.void_transaction(guid=created["guid"], reason="test void")
        return created["guid"]

    def _corrupt_voided_split(self, gc: GnuCashBook, guid: str) -> None:
        """Simulate the partial-void corruption: both splits keep
        ``state='v'`` but carry non-zero (balanced) values again —
        exactly what the unguarded ``update_transaction`` path
        writes when handed a voided target. The values must balance
        or piecash refuses the save, which is also why the C8 shape
        survives in real books: it passes every structural check."""
        with gc.open(readonly=False) as book:
            txn = gc._find_transaction(book, guid)
            for split in txn.splits:
                if split.account.fullname == "Assets:Checking":
                    split.value = Decimal("-100.00")
                    split.quantity = Decimal("-100.00")
                else:
                    split.value = Decimal("100.00")
                    split.quantity = Decimal("100.00")
            book.save()

    def test_corrupted_void_invisible_to_balance_surfaces(
        self, test_book: Path,
    ):
        """``_is_voided`` is documented state-only precisely so that
        a corrupted ``state='v', value != 0`` split still reads as
        voided. Balance surfaces must honor that: the corruption
        must not move get_balance / balance_sheet / net_worth."""
        gc = GnuCashBook(str(test_book))
        guid = self._voided_txn(gc)

        before_balance = gc.get_balance(account_name="Assets:Checking")
        before_assets = Decimal(
            gc.balance_sheet(as_of_date=date.today())["assets"]["total"]
        )
        before_nw = Decimal(
            gc.net_worth(end_date=date.today())["net_worth"]
        )

        self._corrupt_voided_split(gc, guid)

        assert gc.get_balance(
            account_name="Assets:Checking"
        ) == before_balance
        assert Decimal(
            gc.balance_sheet(as_of_date=date.today())["assets"]["total"]
        ) == before_assets
        assert Decimal(
            gc.net_worth(end_date=date.today())["net_worth"]
        ) == before_nw

    def test_update_transaction_refuses_voided_target(
        self, test_book: Path,
    ):
        """Writing new split values into a voided transaction is the
        partial-void corruption generator — it must refuse the way
        ``set_reconcile_state`` already does."""
        gc = GnuCashBook(str(test_book))
        guid = self._voided_txn(gc)
        with pytest.raises(ValueError, match="void"):
            gc.update_transaction(
                guid=guid,
                splits=[
                    {"account": "Expenses:Groceries", "amount": "80.00"},
                    {"account": "Assets:Checking", "amount": "-80.00"},
                ],
            )

    def test_reconcile_account_must_not_flip_voided(
        self, test_book: Path,
    ):
        """Bulk reconcile must not move a voided split to 'y' — the
        exact mutation ``set_reconcile_state`` rejects with a
        written rationale. (Worst chain: void → bulk-reconcile →
        unvoid reports 'not voided' → re-void overwrites the
        void-former-* slots with zeros.)"""
        gc = GnuCashBook(str(test_book))
        guid = self._voided_txn(gc)
        with gc.open(readonly=True) as book:
            txn = gc._find_transaction(book, guid)
            split_guid = next(
                s.guid for s in txn.splits
                if s.account.fullname == "Assets:Checking"
            )

        # A voided split contributes 0, so the statement balance is
        # whatever the account's reconciled balance already is (0.00
        # in this fixture) — validation passes and the bulk path
        # reaches the flip.
        try:
            gc.reconcile_account(
                account_name="Assets:Checking",
                statement_date=date(2026, 3, 1),
                statement_balance="0.00",
                split_guids=[split_guid],
            )
        except ValueError:
            # Post-fix behavior may also be an explicit refusal —
            # either way the split must still be voided below.
            pass

        with gc.open(readonly=True) as book:
            txn = gc._find_transaction(book, guid)
            state = next(
                s.reconcile_state for s in txn.splits
                if s.guid == split_guid
            )
        assert state == "v", (
            "bulk reconcile flipped a voided split to "
            f"{state!r}, defeating unvoid_transaction"
        )

    def test_autofill_never_sources_from_voided(self, test_book: Path):
        """The void-and-re-enter workflow this server recommends
        makes the voided transaction the most recent description
        match; auto-fill must skip it (and with no other match,
        report no-match) instead of cloning zeroed splits."""
        gc = GnuCashBook(str(test_book))
        self._voided_txn(gc)
        with pytest.raises(ValueError, match="[Nn]o match"):
            gc.create_transaction(
                description="Voided pattern",
                trans_date=date(2026, 3, 1),
                check_duplicates=False,
            )


class TestFutureDatedNowSurfaces:
    """C5 (adversarial pass 2): future-dated TRANSACTIONS are
    excluded from every "now" surface (the events haven't happened
    yet); future-dated PRICES stay included (intentional forecasts).
    The fixture carries a rent payment dated today+10 — the
    cross-surface totals in TestCrossSurfaceAgreement already prove
    balance_sheet / net_worth / dashboard ignore it; this class
    locks the point-read and register behavior explicitly."""

    def test_future_txn_excluded_from_balances_but_listed(
        self, pathological_book,
    ):
        gb = GnuCashBook(str(pathological_book.path))
        # Point reads exclude the future expense...
        assert gb.get_balance(
            account_name="Assets:Checking"
        ) == Decimal("10200")
        nw = gb.net_worth(end_date=date.today())
        assert Decimal(nw["net_worth"]) == EXPECTED_NET_WORTH
        # ...but the register still shows it — it's a real entry,
        # just not a "now" event.
        verbose = gb.list_transactions(compact=False, limit=250)
        assert "Scheduled rent (future)" in {
            t["description"] for t in verbose
        }


class TestNullPostDateRows:
    """A9 (adversarial pass 2): null ``post_date`` rows are a
    documented old-book artifact. Listing, search, dashboard, and
    duplicate-detection must tolerate them instead of raising
    TypeError on date compares."""

    def _null_grocery_date(self, gc: GnuCashBook) -> None:
        from sqlalchemy import text

        with gc.open(readonly=False) as book:
            book.session.execute(text(
                "UPDATE transactions SET post_date = NULL "
                "WHERE description = 'Weekly Groceries'"
            ))
            book.save()

    def test_surfaces_tolerate_null_post_date(self, test_book: Path):
        gc = GnuCashBook(str(test_book))
        self._null_grocery_date(gc)

        # Unbounded listing renders it (sorted oldest, date=None).
        listed = gc.list_transactions(compact=False, limit=50)
        by_desc = {t["description"]: t for t in listed}
        assert "Weekly Groceries" in by_desc
        assert by_desc["Weekly Groceries"]["date"] is None
        # A start_date bound excludes what can't be dated.
        bounded = gc.list_transactions(
            start_date=date(2024, 1, 1), compact=False,
        )
        assert "Weekly Groceries" not in {
            t["description"] for t in bounded
        }
        # Compact line, search, and the dashboard all render.
        assert "(no date)" in gc.list_transactions(compact=True)
        assert len(gc.search_transactions(
            "Weekly Groceries", compact=False,
        )) == 1
        gc.get_book_summary()

    def test_duplicate_detection_skips_undated_rows(
        self, test_book: Path,
    ):
        """The create-signals collector does date-window arithmetic;
        an undated row can't anchor it and must be skipped, not
        crash the create."""
        gc = GnuCashBook(str(test_book))
        self._null_grocery_date(gc)
        result = gc.create_transaction(
            description="Weekly Groceries",
            splits=[
                {"account": "Expenses:Groceries", "amount": "99.00"},
                {"account": "Assets:Checking", "amount": "-99.00"},
            ],
            trans_date=date(2026, 6, 1),
            check_duplicates=True,
        )
        assert result["status"] == "created"
