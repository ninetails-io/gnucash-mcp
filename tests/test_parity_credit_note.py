"""The credit-note parity twin, as a test.

On 2026-09-29 the same flow — post a credit note for Emerald
Analytics, apply it to invoice 000018, pay the 3,000 remainder — ran
on two byte-identical books, one in GnuCash desktop and one through
the server, and the row-by-row dumps were diffed until nothing was
left (``specs/v1.5/testing/PARITY_CREDIT_NOTE.md``). The desktop
dump is the fixture here; the server side is reproduced from the
frozen Alex sample plus ``_upgrade_book_shapes`` (the base both
twins started from) and must dump to the same text.

One line is machine-dependent by desktop's own convention: the entry
date is stored at LOCAL noon (the entry ledger's date cell), so the
fixture's 19:00:00 UTC is a PDT desktop's; the test renders the
expected value for the zone it runs in.
"""

from __future__ import annotations

import shutil
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook

_HERE = Path(__file__).resolve().parent
_SAMPLE = _HERE.parent / "samples" / "alex-chen-morales.gnucash"
_FIXTURE = _HERE / "fixtures" / "parity_credit_note_desktop.txt"
sys.path.insert(0, str(_HERE / "fixtures"))
from parity_dump import dump  # noqa: E402


def _local_noon_utc(d: date) -> str:
    return datetime.combine(d, time(12, 0)).astimezone(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def test_server_writes_what_desktop_wrote(tmp_path):
    if not _SAMPLE.exists():
        pytest.skip("Alex sample not present")
    path = tmp_path / "twin.gnucash"
    shutil.copy(_SAMPLE, path)
    gc = GnuCashBook(str(path))
    with gc.open(readonly=False) as book:
        gc._upgrade_book_shapes(book)
        book.save()

    gc.create_credit_note(
        owner_id="000001", owner_type="customer",
        date_opened="2026-09-29", credit_note_id="Parity01",
    )
    gc.add_credit_note_entry(
        credit_note_id="Parity01", account="Income:LLC Revenue",
        description="Retainer adjustment", quantity="1", price="500",
    )
    gc.post_invoice(
        invoice_id="Parity01", post_account="Assets:Accounts Receivable",
        owner_type="customer", post_date="2026-09-29",
    )
    gc.apply_credit_note(
        credit_note_id="Parity01", applies_to_invoice_id="000018",
        owner_type="customer",
    )
    gc.pay_invoice(
        invoice_id="000018",
        payment_account="Assets:Current Assets:Checking Account",
        amount="3000", payment_date="2026-09-29",
        memo="Apply credit note", owner_type="customer",
    )

    expected = _FIXTURE.read_text().replace(
        "date='2026-09-29 19:00:00'", f"date='{_local_noon_utc(date(2026, 9, 29))}'",
    )
    actual = dump(str(path))
    assert actual.splitlines() == expected.splitlines()
