"""The SlotGUID cascade, locked at every ORM delete site.

piecash's ``SlotGUID`` inherits ``SlotFrame.slots``: a delete-orphan
relation joined on ``obj_guid == guid_val``. For a frame that is its
children; for a GUID slot it is every slot of the entity the GUID
points at. ORM-deleting a row that carries a GUID slot — or a frame
holding one — therefore deletes the REFERENCED entity's slots.

``_strip_guid_slots`` is the chokepoint: GUID and frame rows go by raw
SQL before the owner is ORM-deleted. The pre-release adversarial
review (2026-09-30, ``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``,
C4b / C5 / C7 / C8) found four delete sites that skipped it. Each
gets its reproduction here, and ``TestOrmDeleteSitesStripFirst`` greps
the source so a fifth can't land unnoticed.
"""

import re
import sqlite3
import uuid
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook

_STRING, _GUID, _FRAME = 4, 5, 9


# ── raw-row helpers: write slots the way GnuCash desktop stores them ──

def _q(book_path, sql, params=()):
    con = sqlite3.connect(str(book_path))
    try:
        rows = con.execute(sql, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def _raw_slot(book_path, obj_guid, name, slot_type, *, string_val=None,
              guid_val=None, int64_val=0):
    _q(
        book_path,
        "INSERT INTO slots (obj_guid, name, slot_type, int64_val, "
        "string_val, double_val, timespec_val, guid_val, "
        "numeric_val_num, numeric_val_denom, gdate_val) "
        "VALUES (?, ?, ?, ?, ?, NULL, '1970-01-01 00:00:00', ?, 0, 1, NULL)",
        (obj_guid, name, slot_type, int64_val, string_val, guid_val),
    )


def _raw_frame_with_guid(book_path, owner_guid, frame, child, target_guid):
    """``frame`` on ``owner_guid`` holding ``frame/child`` → target,
    GnuCash's shape for an account link (Account.cpp
    ``set_kvp_account_path``)."""
    frame_guid = uuid.uuid4().hex
    _raw_slot(book_path, owner_guid, frame, _FRAME, guid_val=frame_guid)
    _raw_slot(
        book_path, frame_guid, f"{frame}/{child}", _GUID,
        guid_val=target_guid,
    )


def _slots_of(book_path, obj_guid):
    return sorted(
        _q(
            book_path,
            "SELECT name, slot_type, string_val, int64_val FROM slots "
            "WHERE obj_guid = ?",
            (obj_guid,),
        )
    )


def _slot_names(book_path, obj_guid):
    return sorted(n for n, *_ in _slots_of(book_path, obj_guid))


def _account_guid(book_path, name):
    return _q(book_path, "SELECT guid FROM accounts WHERE name = ?", (name,))[0][0]


def _invoice_guid(book_path, doc_id):
    return _q(book_path, "SELECT guid FROM invoices WHERE id = ?", (doc_id,))[0][0]


# ── C5: unpost_invoice ────────────────────────────────────────────────

class TestUnpostLeavesInvoiceSlots:
    """gncInvoiceUnpost destroys the posting transaction and the lot
    and never touches the invoice's own slots. The server's delete
    used to sweep them all through ``gncInvoice/invoice-guid``."""

    DOCLINK = "file:///scans/inv1.pdf"

    def _posted_invoice(self, gb):
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="Consulting", quantity="1", price="500.00",
        )
        return gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )

    def test_unpost_preserves_document_link_and_flag(self, business_book):
        gb = GnuCashBook(str(business_book))
        posted = self._posted_invoice(gb)
        inv_guid = _invoice_guid(business_book, posted["id"])
        # GNC_INVOICE_DOCLINK "assoc_uri" (gncInvoice.c) — desktop's
        # "Manage Document Link".
        _raw_slot(
            business_book, inv_guid, "assoc_uri", _STRING,
            string_val=self.DOCLINK,
        )
        before = _slots_of(business_book, inv_guid)
        assert ("assoc_uri", _STRING, self.DOCLINK, 0) in before

        gb.unpost_invoice(invoice_id=posted["id"])

        assert _slots_of(business_book, inv_guid) == before

    def test_unpost_credit_note_keeps_each_slot_exactly_once(
        self, business_book,
    ):
        """The credit-note flag and the applies-to link used to be
        swept and re-inserted by hand; now they are never touched, so
        the restore must not leave a second copy either."""
        gb = GnuCashBook(str(business_book))
        posted = self._posted_invoice(gb)
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
            applies_to_invoice_id=posted["id"],
        )
        gb.add_credit_note_entry(
            credit_note_id=cn["id"], account="Income:Sales",
            description="Refund", quantity="1", price="100.00",
        )
        gb.post_invoice(
            invoice_id=cn["id"],
            post_account="Assets:Accounts Receivable",
            owner_type="customer",
        )
        cn_guid = _invoice_guid(business_book, cn["id"])
        _raw_slot(
            business_book, cn_guid, "assoc_uri", _STRING,
            string_val=self.DOCLINK,
        )
        before = _slots_of(business_book, cn_guid)
        names = [n for n, *_ in before]
        assert "credit-note" in names and "assoc_uri" in names

        result = gb.unpost_invoice(
            invoice_id=cn["id"], owner_type="customer",
        )

        assert result["type"] == "credit_note"
        assert _slots_of(business_book, cn_guid) == before
        # The target invoice is a bystander.
        inv_guid = _invoice_guid(business_book, posted["id"])
        assert "credit-note" in _slot_names(business_book, inv_guid)
        # And the link still reads back.
        assert posted["id"] in str(gb.get_invoice(
            cn["id"], owner_type="customer",
        )["applies_to"])


# ── C4b: replace_splits ───────────────────────────────────────────────

class TestReplaceSplitsStripsSplitGuidSlots:
    """A split's ``gains-split`` / ``gains-source`` slot (cap-gains.cpp
    ``xaccSplitComputeCapGains``) points at a split in ANOTHER
    transaction."""

    def _two_transactions(self, gb, book_path):
        from datetime import date
        a = gb.create_transaction(
            description="Sale", trans_date=date(2026, 1, 10),
            splits=[
                {"account": "Assets:Checking", "amount": "100.00"},
                {"account": "Income:Salary", "amount": "-100.00"},
            ],
        )
        b = gb.create_transaction(
            description="Gain", trans_date=date(2026, 1, 10),
            splits=[
                {"account": "Assets:Checking", "amount": "7.00"},
                {"account": "Income:Salary", "amount": "-7.00"},
            ],
        )

        def first_split(prefix):
            return _q(
                book_path,
                "SELECT s.guid FROM splits s JOIN transactions t "
                "ON t.guid = s.tx_guid WHERE t.guid LIKE ? "
                "ORDER BY s.guid LIMIT 1",
                (prefix + "%",),
            )[0][0]

        return a["guid"], first_split(a["guid"]), first_split(b["guid"])

    NEW = [
        {"account": "Assets:Checking", "amount": "80.00"},
        {"account": "Income:Salary", "amount": "-80.00"},
    ]

    def test_one_way_link_leaves_other_transactions_slots(self, test_book):
        gb = GnuCashBook(str(test_book))
        ta, sa, sb = self._two_transactions(gb, test_book)
        _raw_slot(test_book, sa, "gains-split", _GUID, guid_val=sb)
        _raw_slot(test_book, sb, "notes", _STRING, string_val="keep")

        gb.replace_splits(ta, self.NEW)

        assert ("notes", _STRING, "keep", 0) in _slots_of(test_book, sb)

    def test_desktops_two_way_pair_does_not_raise(self, test_book):
        """Used to raise sqlalchemy.exc.CircularDependencyError."""
        gb = GnuCashBook(str(test_book))
        ta, sa, sb = self._two_transactions(gb, test_book)
        _raw_slot(test_book, sa, "gains-split", _GUID, guid_val=sb)
        _raw_slot(test_book, sb, "gains-source", _GUID, guid_val=sa)
        _raw_slot(test_book, sb, "notes", _STRING, string_val="keep")

        result = gb.replace_splits(ta, self.NEW)

        assert result["status"] == "splits_replaced"
        assert ("notes", _STRING, "keep", 0) in _slots_of(test_book, sb)


# ── C7: delete_account ────────────────────────────────────────────────

class TestDeleteAccountLeavesLinkedAccounts:
    """Desktop stores account links as GUID slots under a frame
    (Account.cpp ``set_kvp_account_path``)."""

    @pytest.mark.parametrize("frame,child", [
        ("ofx", "associated-income-account"),
        ("associated-account", "dividend"),
        ("lot-mgmt", "gains-acct"),
        ("import-map", "desc"),
    ])
    def test_linked_account_keeps_its_slots(self, test_book, frame, child):
        gb = GnuCashBook(str(test_book))
        gb.create_account("OldBank", "BANK", parent="Assets")
        old = _account_guid(test_book, "OldBank")
        target = _account_guid(test_book, "Salary")
        _raw_slot(test_book, target, "notes", _STRING, string_val="n")
        _raw_slot(test_book, target, "color", _STRING, string_val="#fff")
        _raw_frame_with_guid(test_book, old, frame, child, target)

        gb.delete_account("Assets:OldBank")

        assert _slot_names(test_book, target) == ["color", "notes"]
        assert _slots_of(test_book, old) == []
        # No orphaned frame children either.
        assert _q(
            test_book,
            "SELECT COUNT(*) FROM slots WHERE name LIKE ?",
            (frame + "/%",),
        )[0][0] == 0


# ── C8: the user-slot tools ───────────────────────────────────────────

class TestUserSlotToolsNeverTouchStructuredSlots:
    def _linked(self, gb, book_path):
        target = _account_guid(book_path, "Salary")
        checking = _account_guid(book_path, "Checking")
        _raw_slot(book_path, target, "notes", _STRING, string_val="n")
        _raw_slot(book_path, target, "color", _STRING, string_val="#fff")
        return checking, target

    def test_delete_refuses_reserved_frame_and_cascades_nothing(
        self, test_book,
    ):
        gb = GnuCashBook(str(test_book))
        checking, target = self._linked(gb, test_book)
        _raw_frame_with_guid(
            test_book, checking, "ofx", "associated-income-account", target,
        )

        with pytest.raises(ValueError, match="reserved"):
            gb.delete_account_slot("Assets:Checking", "ofx")

        assert _slot_names(test_book, target) == ["color", "notes"]
        assert "ofx" in _slot_names(test_book, checking)

    def test_delete_refuses_an_unknown_frame_by_shape(self, test_book):
        """A GnuCash frame this module has never heard of is still
        safe: the refusal keys on the row's type, not a name list."""
        gb = GnuCashBook(str(test_book))
        checking, target = self._linked(gb, test_book)
        _raw_frame_with_guid(
            test_book, checking, "future-feature", "acct", target,
        )

        with pytest.raises(ValueError, match="structured frame data"):
            gb.delete_account_slot("Assets:Checking", "future-feature")

        assert _slot_names(test_book, target) == ["color", "notes"]

    def test_delete_refuses_a_bare_guid_slot(self, test_book):
        gb = GnuCashBook(str(test_book))
        checking, target = self._linked(gb, test_book)
        _raw_slot(test_book, checking, "linked", _GUID, guid_val=target)

        with pytest.raises(ValueError, match="structured guid data"):
            gb.delete_account_slot("Assets:Checking", "linked")

        assert _slot_names(test_book, target) == ["color", "notes"]

    def test_set_refuses_to_retype_a_structured_slot(self, test_book):
        gb = GnuCashBook(str(test_book))
        checking, target = self._linked(gb, test_book)
        _raw_frame_with_guid(
            test_book, checking, "future-feature", "acct", target,
        )

        with pytest.raises(ValueError, match="structured frame data"):
            gb.set_account_slot("Assets:Checking", "future-feature", "x")

    @pytest.mark.parametrize("key", [
        # Frames desktop owns — Account.cpp KEY_RECONCILE_INFO,
        # KEY_LOT_MGMT, KEY_ASSOC_INCOME_ACCOUNT's "ofx",
        # KEY_BALANCE_LIMIT, IMAP_FRAME, IMAP_FRAME_BAYES, "tax-US",
        # "associated-account"; gnc-ab-kvp.c "hbci".
        "reconcile-info", "lot-mgmt", "ofx", "balance-limit",
        "import-map", "import-map-bayes", "tax-US",
        "associated-account", "hbci",
        # Flags desktop reads from the slot and this server from the
        # column (xaccAccountGetPlaceholder / xaccAccountGetHidden).
        "placeholder", "hidden",
        # The server's own frame.
        "gnc-mcp",
    ])
    def test_reserved_keys_refused_even_when_absent(self, test_book, key):
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="reserved"):
            gb.set_account_slot("Assets:Checking", key, "true")
        with pytest.raises(ValueError, match="reserved"):
            gb.delete_account_slot("Assets:Checking", key)
        assert _slots_of(
            test_book, _account_guid(test_book, "Checking"),
        ) == []

    def test_reconcile_survives_a_refused_slot_write(self, test_book):
        """The string a user slot write used to leave at
        ``reconcile-info`` made every later reconcile fail write
        verification."""
        from datetime import date
        gb = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="reserved"):
            gb.set_account_slot("Assets:Checking", "reconcile-info", "x")
        balance = gb.get_balance("Assets:Checking")
        result = gb.reconcile_account(
            "Assets:Checking", date.today(), str(balance),
            reconcile_all=True,
        )
        assert "error" not in result

    def test_plain_user_slots_still_round_trip(self, test_book):
        gb = GnuCashBook(str(test_book))
        assert gb.set_account_slot(
            "Assets:Checking", "apr", "5.25",
        ) == {"status": "created"}
        assert gb.set_account_slot(
            "Assets:Checking", "apr", "6",
        ) == {"status": "updated"}
        # Strings desktop stores the same way stay writable.
        gb.set_account_slot("Assets:Checking", "color", "#abcdef")
        assert gb.delete_account_slot(
            "Assets:Checking", "apr",
        ) == {"status": "deleted"}
        assert gb.get_account_slots("Assets:Checking")["slots"] == {
            "color": "#abcdef",
        }


# ── Siblings: deletes that orphaned a frame's children ────────────────

class TestDeletesLeaveNoOrphanFrameChildren:
    """``gnc_sql_slots_delete`` walks into frames. Deleting only the
    owner's own slot rows left each frame's children behind, keyed on
    a frame GUID nothing references."""

    def _orphans(self, book_path):
        return _q(
            book_path,
            "SELECT name FROM slots s WHERE NOT EXISTS ("
            "  SELECT 1 FROM slots f WHERE f.guid_val = s.obj_guid "
            "  AND f.slot_type = 9) "
            "AND s.obj_guid NOT IN (SELECT guid FROM accounts) "
            "AND s.obj_guid NOT IN (SELECT guid FROM transactions) "
            "AND s.obj_guid NOT IN (SELECT guid FROM splits) "
            "AND s.obj_guid NOT IN (SELECT guid FROM invoices) "
            "AND s.obj_guid NOT IN (SELECT guid FROM customers) "
            "AND s.obj_guid NOT IN (SELECT guid FROM lots) "
            "AND s.obj_guid NOT IN (SELECT guid FROM books)",
        )

    def test_customer_with_payment_frame(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        cust = _q(business_book, "SELECT guid FROM customers")[0][0]
        checking = _account_guid(business_book, "Checking")
        _raw_slot(business_book, checking, "notes", _STRING, string_val="n")
        # gncOwner.c: "payment" frame, "last_acct" GUID.
        _raw_frame_with_guid(
            business_book, cust, "payment", "last_acct", checking,
        )

        gb.delete_customer("000001")

        assert self._orphans(business_book) == []
        assert _slot_names(business_book, checking) == ["notes"]

    def test_credit_note_with_applies_to_frame(self, business_book):
        gb = GnuCashBook(str(business_book))
        gb.create_customer(name="Acme Corp")
        gb.create_invoice(customer_id="000001")
        gb.add_invoice_entry(
            invoice_id="000001", account="Income:Sales",
            description="x", quantity="1", price="500.00",
        )
        gb.post_invoice(
            invoice_id="000001",
            post_account="Assets:Accounts Receivable",
        )
        cn = gb.create_credit_note(
            owner_id="000001", owner_type="customer",
            applies_to_invoice_id="000001",
        )

        gb.delete_credit_note(cn["id"], owner_type="customer")

        assert self._orphans(business_book) == []


# ── The lock: every ORM delete site strips first ──────────────────────

_BOOK_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "gnucash_mcp" / "book"
)

# ORM deletes of a mapped row. ``del obj[key]`` on a Python dict is
# not one; the slot-accessor form (``del account[key]``) is, and is
# listed below by file.
_ORM_DELETE = re.compile(r"\b(?:book\.session\.delete|book\.delete)\(")
_SLOT_DEL = re.compile(r"^\s*del (account|transaction|split|lot|inv|invoice)\[")

# How far above a delete its strip may sit (same function, same block).
_WINDOW = 40

# Sites that need no strip, each with the reason it is safe. A new
# entry needs the same: WHY the deleted row can never carry a GUID
# slot or a frame holding one.
_NO_STRIP_NEEDED = {
    ("admin.py", "del account[key]"):
        "guarded by _check_user_slot_row: only a plain string slot "
        "reaches the del",
    ("core.py", 'del account["notes"]'):
        "notes is a string slot (Account.cpp set_kvp_string_path)",
    ("core.py", 'del account["hidden"]'):
        "hidden is a string slot (Account.cpp set_kvp_boolean_path)",
    ("reconciliation.py", "del split[key]"):
        "void-former-amount / void-former-value: numeric slots",
    ("reconciliation.py", 'del transaction["void-former-notes"]'):
        "string slot",
    ("reconciliation.py", "del transaction[key]"):
        "void-reason / void-time / trans-read-only: string slots",
    ("investments.py", "book.session.delete(target)"):
        "a Price row; GnuCash writes no slots on prices",
    ("business.py", "book.session.delete(tt)"):
        "a Taxtable row; no slots",
    ("business.py", "book.session.delete(job)"):
        "a Job row; GnuCash writes no GUID slots on jobs",
    ("budgets.py", "book.session.delete(budget)"):
        "a Budget row; its slots are notes/period strings",
    ("scheduling.py", "book.session.delete(sx)"):
        "a schedule row: its template rows are stripped by "
        "_strip_template_recipe; the sx itself carries no GUID slot",
}


class TestOrmDeleteSitesStripFirst:
    """Grep-the-source, like TestWriteVerificationCoverage: every ORM
    delete in ``book/*.py`` is preceded by ``_strip_guid_slots`` (or
    its scheduling wrapper) within the window, or is listed above with
    the reason it cannot cascade."""

    def _sites(self):
        for path in sorted(_BOOK_DIR.glob("*.py")):
            lines = path.read_text().splitlines()
            for i, line in enumerate(lines):
                if line.lstrip().startswith("#"):
                    continue
                if _ORM_DELETE.search(line) or _SLOT_DEL.match(line):
                    yield path.name, i, line.strip(), lines

    def test_every_orm_delete_strips_or_is_exempt(self):
        unguarded = []
        for fname, i, stmt, lines in self._sites():
            if (fname, stmt) in _NO_STRIP_NEEDED:
                continue
            window = "\n".join(lines[max(0, i - _WINDOW):i])
            if (
                "_strip_guid_slots(" in window
                or "_strip_template_recipe(" in window
            ):
                continue
            unguarded.append(f"{fname}:{i + 1}: {stmt}")
        assert not unguarded, (
            "ORM delete without _strip_guid_slots (piecash's SlotGUID "
            "cascade deletes the slots of whatever a GUID slot points "
            "at). Strip first, or add the site to _NO_STRIP_NEEDED "
            "with the reason it cannot cascade:\n  "
            + "\n  ".join(unguarded)
        )

    def test_exemptions_still_exist(self):
        """A stale exemption hides nothing but documents a lie."""
        seen = {(f, s) for f, _, s, _ in self._sites()}
        stale = sorted(set(_NO_STRIP_NEEDED) - seen)
        assert not stale, f"exempted sites no longer in the source: {stale}"
