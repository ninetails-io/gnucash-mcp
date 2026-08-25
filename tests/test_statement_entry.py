"""Behavior tests for one-shot statement entry (enter_statement).

Spec: specs/v1.5/ENTER_STATEMENT_SPEC.md. Grammar tests exercise the
statement TSV dialect; the rest exercise the book method directly
(the tool wrapper + audit formatter get their own classes at the
bottom, same division as batch entry).
"""

from datetime import date
from decimal import Decimal

import piecash
import pytest

from gnucash_mcp._format import (
    _parse_statement_tsv,
    _statement_tsv_layout,
)
from gnucash_mcp.book import GnuCashBook


# ── Fixture ────────────────────────────────────────────────────────


@pytest.fixture
def statement_book(tmp_path):
    """A book shaped for statement scenarios: a reconciled opening
    anchor (+1000 checking), one unreconciled rent txn (MATCH bait),
    one auto-fill precedent (Whole Foods), identical twin coffees
    (AMBIGUOUS bait), and a credit card. Returns the path."""
    book_path = tmp_path / "stmt.gnucash"
    b = piecash.create_book(
        str(book_path), currency="USD", overwrite=True
    )
    usd = b.default_currency
    root = b.root_account
    assets = piecash.Account(
        name="Assets", type="ASSET", commodity=usd, parent=root,
        placeholder=True,
    )
    checking = piecash.Account(
        name="Checking", type="BANK", commodity=usd, parent=assets
    )
    piecash.Account(
        name="Visa", type="CREDIT", commodity=usd, parent=root
    )
    exp = piecash.Account(
        name="Expenses", type="EXPENSE", commodity=usd, parent=root,
        placeholder=True,
    )
    groceries = piecash.Account(
        name="Groceries", type="EXPENSE", commodity=usd, parent=exp
    )
    rent = piecash.Account(
        name="Rent", type="EXPENSE", commodity=usd, parent=exp
    )
    income = piecash.Account(
        name="Income", type="INCOME", commodity=usd, parent=root
    )

    def txn(dt, desc, legs, notes=None):
        return piecash.Transaction(
            currency=usd, description=desc, post_date=dt,
            notes=notes,
            splits=[
                piecash.Split(
                    account=a, value=Decimal(v), memo=m or ""
                )
                for a, v, m in legs
            ],
        )

    anchor = txn(
        date(2026, 6, 1), "Opening",
        [(checking, "1000", None), (income, "-1000", None)],
    )
    txn(
        date(2026, 7, 1), "July Rent",
        [(checking, "-800", None), (rent, "800", "july rent")],
        notes="rent note",
    )
    txn(
        date(2026, 6, 20), "Whole Foods",
        [(checking, "-60.00", None), (groceries, "60.00", "wk")],
    )
    txn(
        date(2026, 7, 10), "Blue Bottle",
        [(checking, "-5.50", None), (groceries, "5.50", None)],
    )
    txn(
        date(2026, 7, 10), "Blue Bottle",
        [(checking, "-5.50", None), (groceries, "5.50", None)],
    )
    b.save()
    for s in anchor.splits:
        if s.account == checking:
            s.reconcile_state = "y"
    b.save()
    b.close()
    return book_path


def _line(ref, d, amount, **kw):
    row = {"ref": str(ref), "date": d, "amount": amount,
           "splits": kw.pop("splits", [])}
    row.update(kw)
    return row


# The checking statement every commit-path test lands: rent claimed,
# groceries auto-filled, one coffee claimed, paycheck explicit.
_OPEN, _CLOSE = "1000.00", "2107.38"


def _checking_lines(rent_guid=None, coffee_guid=None):
    return [
        _line("1", date(2026, 7, 1), "-800.00",
              raw="ACH RENT PAYMENT", match=rent_guid),
        _line("2", date(2026, 7, 3), "-87.12",
              description="Whole Foods", raw="POS WHOLEFDS 123",
              notes="groceries"),
        _line("3", date(2026, 7, 10), "-5.50",
              description="Blue Bottle", raw="BLUE BOTTLE OAK",
              match=coffee_guid),
        _line("4", date(2026, 7, 15), "2000.00",
              description="Paycheck", raw="DIRECT DEP ACME",
              splits=[{"account": "Income", "amount": "-2000.00"}]),
    ]


def _dry(gc, lines=None, **kw):
    return gc.enter_statement(
        "Assets:Checking", date(2026, 7, 31), _OPEN, _CLOSE,
        lines if lines is not None else _checking_lines(),
        dry_run=True, **kw,
    )


def _cands(res) -> dict[str, list[dict]]:
    """candidates TSV → {ref: [row dicts]}."""
    out: dict[str, list[dict]] = {}
    lines = res["candidates"].splitlines()
    if not lines:
        return out
    header = lines[0].split("\t")
    for ln in lines[1:]:
        row = dict(zip(header, ln.split("\t")))
        out.setdefault(row["ref"], []).append(row)
    return out


def _classes(res) -> dict[str, str]:
    """lines TSV → {ref: class}."""
    out = {}
    for ln in res["lines"].splitlines()[1:]:
        f = ln.split("\t")
        out[f[0]] = f[1]
    return out


def _commit(gc, statement_book, **kw):
    """Run the standard dry-run → claim → commit flow."""
    res = _dry(gc)
    cands = _cands(res)
    rent_guid = cands["1"][0]["split_guid"]
    coffee_guid = cands["3"][0]["split_guid"]
    return gc.enter_statement(
        "Assets:Checking", date(2026, 7, 31), _OPEN, _CLOSE,
        _checking_lines(rent_guid, coffee_guid),
        dry_run=False, **kw,
    )


# ── Grammar ────────────────────────────────────────────────────────


class TestStatementGrammar:
    def test_minimal_dry_run_header(self):
        rows = _parse_statement_tsv(
            "ref\tdate\traw\tamount\n"
            "1\t2026-07-03\tPOS WHOLEFDS\t-87.12"
        )
        assert rows == [{
            "ref": "1", "date": "2026-07-03",
            "raw": "POS WHOLEFDS", "amount": "-87.12", "splits": [],
        }]

    def test_full_header_any_order(self):
        rows = _parse_statement_tsv(
            "ref\tdate\tdescription\tnotes\traw\tmatch\tamount\t"
            "amt\tacct\tmemo\n"
            "1\t2026-07-03\tWhole Foods\tgroceries\tPOS 123\t\t"
            "-87.12\t87.12\tExpenses:Groceries\tweekly"
        )
        assert rows[0]["description"] == "Whole Foods"
        assert rows[0]["splits"] == [{
            "amount": "87.12", "account": "Expenses:Groceries",
            "memo": "weekly",
        }]

    def test_fixed_amount_then_group_amt(self):
        """The fixed section claims amount once; the second
        amount-ish token starts the split groups."""
        layout = _statement_tsv_layout(
            "ref\tdate\traw\tamount\tamt\tacct"
        )
        assert layout["fixed_idx"]["amount"] == 3
        assert layout["group"] == ("amount", "account")

    def test_missing_amount_column_rejects(self):
        with pytest.raises(ValueError, match="amount column"):
            _parse_statement_tsv("ref\tdate\traw\n1\t2026-07-03\tX")

    def test_missing_identity_column_rejects(self):
        with pytest.raises(ValueError, match="description or raw"):
            _parse_statement_tsv("ref\tdate\tamount\n1\t2026-07-01\t5")

    def test_cur_stays_unknown(self):
        """Foreign-currency statements are deferred — the token must
        reject loudly, not half-parse."""
        with pytest.raises(ValueError, match="'cur'"):
            _parse_statement_tsv(
                "ref\tdate\traw\tamount\tcur\n1\t2026-07-01\tX\t5\tUSD"
            )

    def test_missing_date_cell_rejects(self):
        with pytest.raises(ValueError, match="missing date"):
            _parse_statement_tsv(
                "ref\tdate\traw\tamount\n1\t\tX\t5"
            )

    def test_missing_amount_cell_rejects(self):
        with pytest.raises(ValueError, match="missing amount"):
            _parse_statement_tsv(
                "ref\tdate\traw\tamount\n1\t2026-07-01\tX\t"
            )

    def test_empty_ref_rejects(self):
        with pytest.raises(ValueError, match="empty ref"):
            _parse_statement_tsv(
                "ref\tdate\traw\tamount\n\t2026-07-01\tX\t5"
            )


# ── Gates and preconditions ────────────────────────────────────────


class TestStatementGates:
    def test_self_check_gate(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        with pytest.raises(ValueError, match="does not self-check"):
            gc.enter_statement(
                "Assets:Checking", date(2026, 7, 31),
                "1000.00", "999.00",
                [_line("1", date(2026, 7, 3), "-87.12", raw="X")],
            )

    def test_duplicate_refs_reject(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        with pytest.raises(ValueError, match="duplicate ref"):
            gc.enter_statement(
                "Assets:Checking", date(2026, 7, 31),
                "1000.00", "990.00",
                [_line("1", date(2026, 7, 3), "-5.00", raw="X"),
                 _line("1", date(2026, 7, 4), "-5.00", raw="Y")],
            )

    def test_non_statement_account_rejects(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        with pytest.raises(ValueError, match="balance-carrying"):
            gc.enter_statement(
                "Expenses:Groceries", date(2026, 7, 31),
                "0.00", "5.00",
                [_line("1", date(2026, 7, 3), "5.00", raw="X")],
            )

    def test_foreign_commodity_account_rejects(self, tmp_path):
        book_path = tmp_path / "fx.gnucash"
        b = piecash.create_book(
            str(book_path), currency="USD", overwrite=True
        )
        eur = piecash.factories.create_currency_from_ISO("EUR")
        piecash.Account(
            name="EUR Savings", type="BANK", commodity=eur,
            parent=b.root_account,
        )
        b.save()
        b.close()
        gc = GnuCashBook(str(book_path))
        with pytest.raises(ValueError, match="denominated in EUR"):
            gc.enter_statement(
                "EUR Savings", date(2026, 7, 31), "0.00", "5.00",
                [_line("1", date(2026, 7, 3), "5.00", raw="X")],
            )

    def test_opening_gap_warns_in_dry_run(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "900.00", "2007.38", _checking_lines(), dry_run=True,
        )
        assert "does not tie to the statement's opening" in \
            res["warnings"]

    def test_opening_gap_blocks_commit(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        with pytest.raises(ValueError, match="untied base"):
            gc.enter_statement(
                "Assets:Checking", date(2026, 7, 31),
                "900.00", "2007.38",
                [_line("4", date(2026, 7, 15), "1107.38",
                       description="Paycheck",
                       splits=[{"account": "Income",
                                "amount": "-1107.38"}])],
                dry_run=False,
            )


# ── Dry-run classification ─────────────────────────────────────────


class TestStatementDryRun:
    def test_classification(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        res = _dry(gc)
        assert _classes(res) == {
            "1": "MATCH", "2": "NEW", "3": "AMBIGUOUS", "4": "NEW",
        }
        assert "2 NEW, 1 MATCH, 0 OVERLAP, 1 AMBIGUOUS" in \
            res["summary"]
        assert "2 rows need adjudication" in res["summary"]

    def test_match_carries_full_annotation(self, statement_book):
        """Ruling: the MATCH row ships the evidence — description,
        notes, memo, post date, short GUID — so the judgment pass
        can adapt annotations the server can't infer."""
        gc = GnuCashBook(str(statement_book))
        cand = _cands(_dry(gc))["1"][0]
        assert cand["description"] == "July Rent"
        assert cand["notes"] == "rent note"
        assert cand["date"] == "2026-07-01"
        assert cand["state"] == "n"
        # The probe is the raw cell ("ACH RENT PAYMENT"), which does
        # NOT desc-match "July Rent" — amount + date carry it.
        assert cand["signals"] == "-AD"
        assert len(cand["split_guid"]) >= 8

    def test_amount_only_candidate_surfaces(self, statement_book):
        """Ruling 1's rent case: a line 31 days from a same-amount
        split must surface even with no desc/date signal."""
        gc = GnuCashBook(str(statement_book))
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 8, 31),
            "1000.00", "200.00",
            [_line("1", date(2026, 8, 1), "-800.00",
                   raw="ACH RENT AUG")],
            dry_run=True,
        )
        cand = _cands(res)["1"][0]
        assert cand["description"] == "July Rent"
        assert cand["signals"] == "-A-"

    def test_prediction_note_for_new(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        res = _dry(gc)
        note = {
            f[0]: f[3] for f in
            (ln.split("\t") for ln in res["lines"].splitlines()[1:])
        }["2"]
        assert "would create -87.12 → Expenses:Groceries" in note
        assert "auto_filled_from:" in note

    def test_tie_footer_ties(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        assert "ties to the statement closing" in _dry(gc)["tie"]

    def test_dry_run_writes_nothing(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        before = gc.get_balance("Assets:Checking")
        _dry(gc)
        assert gc.get_balance("Assets:Checking") == before

    def test_dry_run_validates_explicit_splits(self, statement_book):
        """Full rehearsal: a bad counter account surfaces in the
        dry-run warnings, not at commit time."""
        gc = GnuCashBook(str(statement_book))
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "1000.00", "995.00",
            [_line("1", date(2026, 7, 3), "-5.00", raw="X",
                   description="Typo",
                   splits=[{"account": "Expenses:Nope",
                            "amount": "5.00"}])],
            dry_run=True,
        )
        assert "1\t" in res["warnings"]
        assert "Nope" in res["warnings"]


# ── Commit ─────────────────────────────────────────────────────────


class TestStatementCommit:
    def test_full_landing(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        res = _commit(gc, statement_book)
        assert "2 created, 2 claimed, 0 skipped" in res["summary"]
        assert res["new_reconciled_balance"] == "2107.38"
        assert "ties" in res["tie"]
        statuses = {
            f[0]: f[1] for f in
            (ln.split("\t") for ln in
             res["results"].splitlines()[1:])
        }
        assert statuses == {
            "1": "claimed", "2": "created", "3": "claimed",
            "4": "created",
        }

    def test_landing_reconciles_and_annotates(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        _commit(gc, statement_book)
        with gc.open() as book:
            checking = [
                a for a in book.accounts if a.name == "Checking"
            ][0]
            rent_split = next(
                s for s in checking.splits
                if s.transaction.description == "July Rent"
            )
            # Claimed: reconciled, raw landed on the memo, notes
            # updated... rent line carried raw but no notes.
            assert rent_split.reconcile_state == "y"
            assert rent_split.memo == "ACH RENT PAYMENT"
            wf = next(
                s for s in checking.splits
                if s.transaction.description == "Whole Foods"
                and s.transaction.post_date == date(2026, 7, 3)
            )
            # Created: bank leg reconciled, raw as memo, notes set,
            # auto-filled counter on Groceries at the LINE amount.
            assert wf.reconcile_state == "y"
            assert wf.memo == "POS WHOLEFDS 123"
            assert wf.transaction.notes == "groceries"
            counter = next(
                s for s in wf.transaction.splits if s is not wf
            )
            assert counter.account.fullname == "Expenses:Groceries"
            assert counter.value == Decimal("87.12")
            # Counter legs do NOT reconcile — only the statement
            # account's own legs are on the statement.
            assert counter.reconcile_state != "y"

    def test_claim_amount_mismatch_rejects(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        cands = _cands(_dry(gc))
        rent_guid = cands["1"][0]["split_guid"]
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "1000.00", "200.50",
            [_line("1", date(2026, 7, 1), "-799.50",
                   raw="ACH RENT", match=rent_guid)],
            dry_run=False,
        )
        assert "REJECTED" in res["summary"]
        assert "fix the book entry first" in res["warnings"]

    def test_match_row_with_splits_rejects(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        rent_guid = _cands(_dry(gc))["1"][0]["split_guid"]
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "1000.00", "200.00",
            [_line("1", date(2026, 7, 1), "-800.00",
                   raw="ACH RENT", match=rent_guid,
                   splits=[{"account": "Expenses:Rent",
                            "amount": "800.00"}])],
            dry_run=False,
        )
        assert "cannot also carry counter-splits" in res["warnings"]

    def test_double_claim_rejects(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        rent_guid = _cands(_dry(gc))["1"][0]["split_guid"]
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "1000.00", "-600.00",
            [_line("1", date(2026, 7, 1), "-800.00",
                   raw="ACH RENT", match=rent_guid),
             _line("2", date(2026, 7, 1), "-800.00",
                   raw="ACH RENT AGAIN", match=rent_guid)],
            dry_run=False,
        )
        assert "already claimed by another row" in res["warnings"]

    def test_duplicate_guard(self, statement_book):
        """A created line exactly matching an unclaimed unreconciled
        split rejects — the tie would hold while the book
        double-entered. force overrides (judgment ruled NEW)."""
        gc = GnuCashBook(str(statement_book))
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "1000.00", "200.00",
            [_line("1", date(2026, 7, 1), "-800.00",
                   description="July Rent", raw="ACH RENT")],
            dry_run=False,
        )
        assert "REJECTED" in res["summary"]
        assert "claim it with match=" in res["warnings"]

    def test_tie_failure_refuses_wholesale(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        # Consistent statement (gate passes) whose base makes the
        # tie impossible: force past the opening gap WITHOUT force
        # on the tie is impossible — so instead: consistent lines,
        # correct opening, but an OVERLAP-free skip that breaks the
        # tie is unreachable by construction. The reachable case:
        # forced landing onto an untied base records a discrepancy.
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "900.00", "1000.00",
            [_line("1", date(2026, 7, 20), "100.00",
                   description="Deposit",
                   splits=[{"account": "Income",
                            "amount": "-100.00"}])],
            dry_run=False, force=True,
        )
        assert "DISCREPANCY" in res["tie"]
        assert "under force" in res["tie"]

    def test_atomicity_on_row_error(self, statement_book):
        """One bad row → nothing written, good rows report
        statement_aborted."""
        gc = GnuCashBook(str(statement_book))
        before = gc.get_balance("Assets:Checking")
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "1000.00", "1095.00",
            [_line("1", date(2026, 7, 20), "100.00",
                   description="Deposit",
                   splits=[{"account": "Income",
                            "amount": "-100.00"}]),
             _line("2", date(2026, 7, 21), "-5.00",
                   description="Bad",
                   splits=[{"account": "Expenses:Nope",
                            "amount": "5.00"}])],
            dry_run=False,
        )
        assert "REJECTED" in res["summary"]
        statuses = {
            f[0]: f[1] for f in
            (ln.split("\t") for ln in
             res["results"].splitlines()[1:])
        }
        assert statuses == {
            "1": "statement_aborted", "2": "rejected",
        }
        assert gc.get_balance("Assets:Checking") == before

    def test_overlap_claim_is_noop(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        _commit(gc, statement_book)
        # Re-land one already-reconciled line as a match row on the
        # NEXT statement: skipped, tie unaffected.
        with gc.open() as book:
            checking = [
                a for a in book.accounts if a.name == "Checking"
            ][0]
            rent_split = next(
                s for s in checking.splits
                if s.transaction.description == "July Rent"
            )
            guid = rent_split.guid
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 8, 31),
            "2907.38", "2107.38",
            [_line("1", date(2026, 7, 1), "-800.00",
                   raw="ACH RENT", match=guid)],
            dry_run=False, force=True,
        )
        assert "skipped_overlap" in res["results"]
        assert "1 skipped" in res["summary"]

    def test_multi_split_precedent_rejects_autofill(
        self, statement_book
    ):
        gc = GnuCashBook(str(statement_book))
        # Give "Payday" a 3-split precedent.
        gc.create_transaction(
            description="Payday",
            splits=[
                {"account": "Assets:Checking", "amount": "900.00"},
                {"account": "Income", "amount": "-1000.00"},
                {"account": "Expenses:Groceries",
                 "amount": "100.00"},
            ],
            trans_date=date(2026, 6, 5),
            check_duplicates=False,
        )
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "1000.00", "1900.00",
            [_line("1", date(2026, 7, 20), "900.00",
                   description="Payday")],
            dry_run=False,
        )
        assert "REJECTED" in res["summary"]
        assert "supply explicit counter-splits" in res["warnings"]


class TestStatementReviewFindings:
    """Regression locks for the adversarial-review findings on the
    first implementation — each of these demonstrated a live defect
    before its fix."""

    def _reconcile_rent(self, statement_book):
        """Mark the July rent split reconciled, returning its guid."""
        import piecash as pc
        b = pc.open_book(str(statement_book), readonly=False,
                         do_backup=False)
        guid = None
        for txn in b.transactions:
            if txn.description == "July Rent":
                for s in txn.splits:
                    if s.account.name == "Checking":
                        s.reconcile_state = "y"
                        guid = s.guid
        b.save()
        b.close()
        return guid

    def test_monthly_pattern_vs_reconciled_prior_is_NEW(
        self, statement_book
    ):
        """The August rent line, 31 days after the RECONCILED July
        rent of the same amount, is a genuine NEW line: it must
        count in the projection (old code classed it OVERLAP,
        skipped it, and reported a false discrepancy)."""
        gc = GnuCashBook(str(statement_book))
        self._reconcile_rent(statement_book)
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 8, 31),
            "200.00", "-600.00",
            [_line("1", date(2026, 8, 1), "-800.00",
                   raw="ACH RENT AUG",
                   splits=[{"account": "Expenses:Rent",
                            "amount": "800.00"}])],
            dry_run=True,
        )
        assert _classes(res)["1"] == "NEW"
        # The reconciled July candidate still ships as adaptation
        # evidence.
        assert _cands(res)["1"][0]["state"] == "y"
        assert "ties" in res["tie"]

    def test_reconciled_exact_twin_is_OVERLAP_and_guarded(
        self, statement_book
    ):
        """A line whose exact twin (amount + tight date) is already
        reconciled: dry-run classes OVERLAP; a bare-create commit
        refuses (old code silently double-entered under force)."""
        gc = GnuCashBook(str(statement_book))
        guid = self._reconcile_rent(statement_book)
        assert guid is not None
        # Post-reconcile base is 200 (1000 anchor − 800 rent); a
        # re-transcription of the rent line on a tied base is the
        # double-entry trap the guard exists for.
        line = _line("1", date(2026, 7, 1), "-800.00",
                     raw="ACH RENT JULY",
                     splits=[{"account": "Expenses:Rent",
                              "amount": "800.00"}])
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "200.00", "-600.00", [line], dry_run=True,
        )
        assert _classes(res)["1"] == "OVERLAP"
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "200.00", "-600.00", [line], dry_run=False,
        )
        assert "REJECTED" in res["summary"]
        assert "landed by a prior statement" in res["warnings"]

    def test_autofill_precedent_must_touch_account(
        self, statement_book
    ):
        """A description-matched precedent paid from a DIFFERENT
        account must not auto-fill (old code picked an arbitrary
        leg and could fabricate an inter-bank transfer)."""
        gc = GnuCashBook(str(statement_book))
        res = gc.enter_statement(
            "Visa", date(2026, 7, 31), "0.00", "60.00",
            [_line("1", date(2026, 7, 20), "60.00",
                   description="Whole Foods")],
            dry_run=False,
        )
        assert "REJECTED" in res["summary"]
        assert "doesn't touch this statement account" in \
            res["warnings"]

    def test_overlap_claim_wrong_amount_rejects(
        self, statement_book
    ):
        """Claim exactness has no reconciled-split exemption: a
        wrong-GUID paste naming a reconciled split of a different
        amount diagnoses at the row (old code silently no-op'd)."""
        gc = GnuCashBook(str(statement_book))
        guid = self._reconcile_rent(statement_book)
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "200.00", "150.00",
            [_line("1", date(2026, 7, 2), "-50.00",
                   raw="SOMETHING ELSE", match=guid)],
            dry_run=False,
        )
        assert "REJECTED" in res["summary"]
        assert "wrong split" in res["warnings"]

    def test_sub_quantum_amount_rejects(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        with pytest.raises(ValueError, match="finer precision"):
            gc.enter_statement(
                "Assets:Checking", date(2026, 7, 31),
                "1000.00", "1000.004",
                [_line("1", date(2026, 7, 3), "0.004", raw="X")],
            )

    def test_empty_lines_reject(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        with pytest.raises(ValueError, match="no lines"):
            gc.enter_statement(
                "Assets:Checking", date(2026, 7, 31),
                "1000.00", "1000.00", [],
            )

    def test_rehearsal_surfaces_autofill_failure(
        self, statement_book
    ):
        """A splitless line commit would reject (no precedent) must
        warn in the dry run — the old predictor noted it but a row
        with empty description AND raw said nothing at all."""
        gc = GnuCashBook(str(statement_book))
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "1000.00", "990.00",
            [_line("1", date(2026, 7, 3), "-10.00",
                   description="Utterly Unprecedented")],
            dry_run=True,
        )
        assert "no matching transaction to auto-fill" in \
            res["warnings"]
        assert "warning(s) outstanding" in res["tie"]

    def test_dry_run_detects_double_claim(self, statement_book):
        gc = GnuCashBook(str(statement_book))
        rent_guid = _cands(_dry(gc))["1"][0]["split_guid"]
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "1000.00", "-600.00",
            [_line("1", date(2026, 7, 1), "-800.00",
                   raw="ACH RENT", match=rent_guid),
             _line("2", date(2026, 7, 1), "-800.00",
                   raw="ACH RENT AGAIN", match=rent_guid)],
            dry_run=True,
        )
        assert "already claimed by another row" in res["warnings"]

    def test_exact_match_note_flags_commit_refusal(
        self, statement_book
    ):
        """A MATCH line with an exact unreconciled twin says in its
        note what commit will do with a bare create."""
        gc = GnuCashBook(str(statement_book))
        res = _dry(gc)
        notes = {
            f[0]: f[3] for f in
            (ln.split("\t") for ln in res["lines"].splitlines()[1:])
        }
        assert "exact twin" in notes["1"]


# ── Sign transform (credit card) ───────────────────────────────────


class TestStatementSignTransform:
    def test_credit_card_statement_native(self, statement_book):
        """Charges print positive, balances print as amount owed;
        the server negates both. Zero mental sign-flips."""
        gc = GnuCashBook(str(statement_book))
        res = gc.enter_statement(
            "Visa", date(2026, 7, 31), "0.00", "42.00",
            [_line("1", date(2026, 7, 20), "42.00",
                   description="Trader Joes", raw="TRADER JOES 88",
                   splits=[{"account": "Expenses:Groceries",
                            "amount": "42.00"}])],
            dry_run=False,
        )
        assert "1 created" in res["summary"]
        assert res["new_reconciled_balance"] == "-42.00"
        assert "ties" in res["tie"]
        assert gc.get_balance("Visa") == Decimal("-42")

    def test_payment_line_on_card(self, statement_book):
        """A payment prints negative on a card statement and lands
        positive in the book."""
        gc = GnuCashBook(str(statement_book))
        gc.enter_statement(
            "Visa", date(2026, 7, 31), "0.00", "42.00",
            [_line("1", date(2026, 7, 20), "42.00",
                   description="Trader Joes",
                   splits=[{"account": "Expenses:Groceries",
                            "amount": "42.00"}])],
            dry_run=False,
        )
        res = gc.enter_statement(
            "Visa", date(2026, 8, 31), "42.00", "2.00",
            [_line("1", date(2026, 8, 10), "-40.00",
                   description="Payment Thank You",
                   splits=[{"account": "Assets:Checking",
                            "amount": "-40.00"}])],
            dry_run=False,
        )
        assert "1 created" in res["summary"]
        assert res["new_reconciled_balance"] == "-2.00"
        assert gc.get_balance("Visa") == Decimal("-2")


# ── Round-trip closure ─────────────────────────────────────────────


class TestStatementRoundTrip:
    def test_candidate_guid_claims(self, statement_book):
        """The dry-run candidates table's split_guid short form is
        valid match input — output hands look like input hands."""
        gc = GnuCashBook(str(statement_book))
        guid = _cands(_dry(gc))["1"][0]["split_guid"]
        res = gc.enter_statement(
            "Assets:Checking", date(2026, 7, 31),
            "1000.00", "200.00",
            [_line("1", date(2026, 7, 1), "-800.00",
                   raw="ACH RENT", match=guid)],
            dry_run=False,
        )
        assert "claimed" in res["results"]


# ── Audit formatter ────────────────────────────────────────────────


class TestStatementAuditFormatter:
    def _entry(self, dry):
        lines_tsv = (
            "ref\tdate\tdescription\tnotes\traw\tmatch\tamount\n"
            "1\t2026-07-01\tJuly Rent\t\tACH RENT\tddc40a70\t-800.00\n"
            "2\t2026-07-03\tWhole Foods\tgroceries\tPOS 123\t\t-87.12"
        )
        return {
            "timestamp": "2026-08-24T10:00:00-07:00",
            "params": {
                "account": "Assets:Checking",
                "statement_date": "2026-07-31",
                "opening_balance": "1000.00",
                "closing_balance": "112.88",
                "lines": lines_tsv,
                "dry_run": dry,
            },
            "before_state": {
                "account": "Assets:Checking",
                "claims": [{
                    "guid": "ddc40a70" + "0" * 24,
                    "state": "n", "memo": "", "notes": "rent note",
                    "description": "July Rent",
                    "date": "2026-07-01",
                }],
            },
            "after_state": {
                "summary": "Statement entered: 1 created, 1 "
                           "claimed, 0 skipped (already "
                           "reconciled); 2 splits reconciled at "
                           "2026-07-31.",
                "results": "ref\tstatus\tguid\tnote\n"
                           "1\tclaimed\tddc40a70\t\n"
                           "2\tcreated\tabcd1234\t"
                           "auto_filled_from:ad888455",
                "tie": "reconciled balance 112.88 ties to the "
                       "statement closing (112.88 as printed)",
            },
        }

    def test_commit_render(self):
        from gnucash_mcp.logging_config import _fmt_statement_enter
        out = "\n".join(_fmt_statement_enter(self._entry(False)))
        assert "ENTER STATEMENT  Assets:Checking  2026-07-31" in out
        assert "opening 1000.00 → closing 112.88" in out
        assert 'CLAIM  split:ddc40a70  "July Rent"' in out
        assert "state: n → y" in out
        assert "memo: (empty) → ACH RENT" in out
        assert 'CREATE  guid:abcd1234  "Whole Foods"' in out
        assert "auto-filled from guid:ad888455" in out
        assert "ties to the statement closing" in out

    def test_dry_run_render_is_one_summary(self):
        from gnucash_mcp.logging_config import _fmt_statement_enter
        entry = self._entry(True)
        entry["after_state"] = {
            "summary": "Dry run: 2 lines — 1 NEW, 1 MATCH, 0 "
                       "OVERLAP, 0 AMBIGUOUS.",
        }
        out = _fmt_statement_enter(entry)
        assert len(out) == 2
        assert "(dry run)" in out[0]

    def test_render_through_the_escape_chokepoint(self):
        """The full production path — _format_audit_entry_text
        escapes params BEFORE dispatch, and 'lines' must be in
        _AUDIT_TSV_KEYS or the TSV's tabs get escaped to literal
        text and every CREATE row renders empty (found by review;
        the direct-call tests above bypass the chokepoint)."""
        from gnucash_mcp.logging_config import (
            _format_audit_entry_text,
        )
        entry = self._entry(False)
        entry["classification"] = "write"
        entry["operation"] = "enter"
        entry["entity_type"] = "statement"
        entry["tool"] = "enter_statement"
        out = _format_audit_entry_text(entry)
        assert '"Whole Foods" (2026-07-03, -87.12)' in out
        assert "memo: (empty) → ACH RENT" in out
        assert "notes: groceries" in out


# ── Tool wrapper ───────────────────────────────────────────────────


class TestStatementToolParse:
    def test_bad_line_date_is_not_defaulted(self):
        """The grammar keeps the date cell as a string; the tool
        wrapper rejects invalid ones. Batch defaults a bad date to
        today — a statement transcription must never."""
        rows = _parse_statement_tsv(
            "ref\tdate\traw\tamount\n1\tnot-a-date\tX\t5"
        )
        assert rows[0]["date"] == "not-a-date"
