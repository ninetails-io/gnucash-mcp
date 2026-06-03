"""Regression tests for the Branch 1 chokepoint refactor.

Each test class locks an invariant the refactor consolidated into a
single enforcement point. They map 1:1 to the bug classes the spec
catalogues:

- ``TestResolveAccountTemplateFilter`` — SB-12
- ``TestMarketPriceFilter`` — SB-11 (added in commit 2)
- ``TestIsVoidedConsistency`` — SB-13, SB-14, HP-3 (added in commit 3)
- ``TestRatesAsOfRequiresDate`` — SB-2, SB-3, SB-4 (added in commit 4)
- ``TestNetWorthSeriesPerBoundaryRates`` — SB-1 (added in commit 5)

If any of these tests starts failing without an intentional change to
the chokepoint, the bug class is open again.
"""

from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook


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
