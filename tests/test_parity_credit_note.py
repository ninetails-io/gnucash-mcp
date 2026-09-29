"""The credit-note parity twin, as a test.

On 2026-09-29 the same flow — post a credit note for Emerald
Analytics, apply it to invoice 000018, pay the 3,000 remainder — ran
on two byte-identical books, one in GnuCash desktop and one through
the server, and the row-by-row dumps were diffed until nothing was
left (``specs/v1.5/testing/PARITY_CREDIT_NOTE.md``). The desktop
dump is the fixture here. The server side is reproduced on a book
built from nothing (the dump reads only the rows the flow touches:
the customer, invoice 000018 and its lot, the three accounts), and
must dump to the same text.

One line is machine-dependent by desktop's own convention: the entry
date is stored at LOCAL noon (the entry ledger's date cell), so the
fixture's 19:00:00 UTC is a PDT desktop's; the test renders the
expected value for the zone it runs in.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

import piecash

from gnucash_mcp.book import GnuCashBook

_HERE = Path(__file__).resolve().parent
_FIXTURE = _HERE / "fixtures" / "parity_credit_note_desktop.txt"
sys.path.insert(0, str(_HERE / "fixtures"))
from parity_dump import dump  # noqa: E402


def _local_noon_utc(d: date) -> str:
    return datetime.combine(d, time(12, 0)).astimezone(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _base_book(path: Path) -> GnuCashBook:
    """The slice of the twin's base the flow touches, built from
    nothing: the accounts by their names, Emerald Analytics as
    customer 000001, invoice 000018 posted 2026-06-01 for 3,500 to
    Income:LLC Revenue."""
    book = piecash.create_book(str(path), currency="USD", overwrite=True)
    root = book.root_account
    usd = book.default_currency
    assets = piecash.Account(name="Assets", type="ASSET", parent=root,
                             commodity=usd, placeholder=True)
    current = piecash.Account(name="Current Assets", type="ASSET", parent=assets,
                              commodity=usd, placeholder=True)
    piecash.Account(name="Checking Account", type="BANK", parent=current, commodity=usd)
    piecash.Account(name="Accounts Receivable", type="RECEIVABLE", parent=assets,
                    commodity=usd)
    income = piecash.Account(name="Income", type="INCOME", parent=root,
                             commodity=usd, placeholder=True)
    piecash.Account(name="LLC Revenue", type="INCOME", parent=income, commodity=usd)
    book.save()
    book.close()
    gc = GnuCashBook(str(path))
    gc.create_customer(name="Emerald Analytics", currency="USD")
    gc.create_invoice(customer_id="000001", date_opened="2026-06-01", invoice_id="000018")
    gc.add_invoice_entry(
        invoice_id="000018", account="Income:LLC Revenue",
        description="June 2026 consulting retainer", quantity="1", price="3500",
    )
    gc.post_invoice(
        invoice_id="000018", post_account="Assets:Accounts Receivable",
        post_date="2026-06-01", due_date="2026-07-01",
    )
    return gc


def test_server_writes_what_desktop_wrote(tmp_path):
    gc = _base_book(tmp_path / "twin.gnucash")
    path = tmp_path / "twin.gnucash"

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
