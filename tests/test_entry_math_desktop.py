"""The server's document arithmetic against GnuCash's own engine.

Two halves, one instrument (``tests/fixtures/desktop_totals.py``):

* **Recorded.** ``desktop_totals_5_12.json`` holds 300 random draft
  documents — their entry rows as stored, and the total, subtotal,
  and tax GnuCash 5.12's engine reported for each
  (``gncInvoiceGetTotal`` and friends, asked headlessly through
  ``gnucash-cli``). This half replays the rows through the server's
  math and expects GnuCash's answers. It needs no GnuCash, so it runs
  everywhere, CI included.
* **Live.** Whenever ``gnucash-cli`` is on the machine: a fresh book
  drafted through the server, and the engine asked there and then.

This is "parity means the diff is empty" for numbers. The pre-1.5
math disagreed with the engine on 383 of 600 such documents
(``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``, C1 / C2).
"""

from __future__ import annotations

import os
import sys
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.book import _entry_math as em

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import desktop_totals  # noqa: E402


def _replay(doc, tables):
    """A recorded document through the server's real row reader
    (``_entry_line_values``) and ``document_totals``."""
    lines = []
    for row in doc["rows"]:
        taxtable = row["b_taxtable"] if doc["is_bill"] else row["i_taxtable"]
        lines.append(GnuCashBook._entry_line_values(
            SimpleNamespace(**row), doc["is_bill"], tables.get(taxtable, []),
        ))
    return em.document_totals(lines, doc["fraction"], doc["is_credit_note"])


@pytest.fixture(scope="module")
def recorded():
    data = desktop_totals.load_recorded()
    tables = {
        guid: [
            em.TaxEntry(e["kind"], Fraction(e["amount"]), e["account"])
            for e in entries
        ]
        for guid, entries in data["taxtables"].items()
    }
    return data, tables


class TestRecordedDesktopTotals:
    def test_every_document_totals_as_gnucash_totalled_it(self, recorded):
        data, tables = recorded
        wrong = []
        for doc in data["documents"]:
            got = _replay(doc, tables)
            want = doc["desktop"]
            if (
                Fraction(got.total), Fraction(got.net), Fraction(got.tax),
            ) != (
                Fraction(want["total"]), Fraction(want["subtotal"]),
                Fraction(want["tax"]),
            ):
                side = "bill" if doc["is_bill"] else "invoice"
                wrong.append(
                    f"{side} {doc['id']}"
                    f"{' (credit note)' if doc['is_credit_note'] else ''}: "
                    f"server total/net/tax {got.total}/{got.net}/{got.tax}, "
                    f"GnuCash {data['gnucash']} "
                    f"{float(Fraction(want['total']))}/"
                    f"{float(Fraction(want['subtotal']))}/"
                    f"{float(Fraction(want['tax']))}"
                )
        assert not wrong, (
            f"{len(wrong)} of {len(data['documents'])} documents differ "
            f"from GnuCash's engine:\n  " + "\n  ".join(wrong[:15])
        )

    def test_the_recorded_set_covers_the_hard_cases(self, recorded):
        """An oracle that never meets a discount or a tie proves
        nothing; pin what the set contains so a re-record can't
        quietly thin it."""
        data, _ = recorded
        docs = data["documents"]
        rows = [(d, r) for d in docs for r in d["rows"]]
        assert len(docs) >= 250
        kinds = {(d["is_bill"], d["is_credit_note"]) for d in docs}
        assert kinds == {
            (False, False), (False, True), (True, False), (True, True),
        }
        # Every discount type × how, on the customer side.
        seen = {
            (r["i_disc_type"], r["i_disc_how"])
            for d, r in rows if not d["is_bill"] and r["i_discount_num"]
        }
        assert seen == {
            (t, h) for t in ("PERCENT", "VALUE")
            for h in ("PRETAX", "SAMETIME", "POSTTAX")
        }
        assert sum(1 for d, r in rows if r["i_taxincluded"] or r["b_taxincluded"]) > 50
        assert sum(1 for d in docs if len(d["rows"]) >= 3) > 80
        # Flat-value and multi-account tax tables are in play.
        tables = data["taxtables"].values()
        assert any(e["kind"] == "VALUE" for t in tables for e in t)
        assert any(len({e["account"] for e in t}) > 1 for t in tables)
        # Sub-cent prices (denominator 1000): where ties come from.
        assert sum(
            1 for d, r in rows
            if (r["b_price_denom"] if d["is_bill"] else r["i_price_denom"]) == 1000
        ) > 200
        # And the oddity the port must keep: a credit note under a
        # flat-value tax totals with that tax NEGATIVE in GnuCash.
        assert any(Fraction(d["desktop"]["tax"]) < 0 for d in docs)


@pytest.mark.skipif(
    desktop_totals.find_gnucash_cli() is None,
    reason="gnucash-cli not installed (set GNUCASH_CLI to its path)",
)
class TestLiveDesktopOracle:
    def test_server_totals_match_the_engine_on_a_fresh_book(self):
        """Through the real read path (``_get_invoice_entries_and_
        total``) on a book the server just drafted. Reproduce a
        failure with ``uv run python tests/fixtures/desktop_totals.py
        live <count> <seed>``."""
        seed = int(os.environ.get("GNUCASH_ORACLE_SEED", "20260930"))
        problems = desktop_totals.live(120, seed)
        assert not problems, (
            f"seed {seed}: {len(problems)} documents differ from "
            f"GnuCash's engine:\n  " + "\n  ".join(problems[:15])
        )
