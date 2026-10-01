"""What a document line comes to, and what a document posts — GnuCash's
own arithmetic, ported.

A port, not a reinterpretation, in the manner of ``_recurrence_next``
(Recurrence.cpp) and ``_billterm_due_date`` (gncBillTerm.c). Three
functions from GnuCash 5.12 ``libgnucash/engine``:

* ``entry_values``  — ``gncEntryComputeValueInt`` (gncEntry.c): one
  line's value, discount, and per-account taxes, UNROUNDED.
* ``round_half_up`` — ``gnc_numeric_convert(x, denom,
  GNC_HOW_DENOM_EXACT | GNC_HOW_RND_ROUND_HALF_UP)``: the one rounding
  GnuCash applies to document amounts.
* ``document_totals`` — ``gncEntryRecomputeValues`` (per-line net
  rounded) plus ``gncInvoiceGetNetAndTaxesInternal`` (tax summed
  UNROUNDED per tax account across the whole document, then rounded
  once) — what ``gncInvoicePostToAccount`` turns into splits.

Why it exists: the server's own math rounded each line's tax
separately, half-to-even, on an already-rounded pretax, and never read
a line's discount. A third to nearly half of multi-line taxed
invoices posted a different total than desktop computes for the same
entries (pre-release adversarial review 2026-09-30,
``specs/v1.5/testing/ADVERSARIAL_REVIEW_1.5.md``, C1 and C2).

Arithmetic is exact (``fractions.Fraction``), as GnuCash's is: every
intermediate there is ``GNC_HOW_DENOM_REDUCE`` or ``GNC_HOW_DENOM_LCD``
with ``GNC_HOW_RND_ROUND`` reached only "to prevent overflow"
(gncEntry.c's own comment). Rounding happens in exactly the two places
desktop rounds, and nowhere else.

Pinned by ``tests/test_entry_math.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

# gncTaxTable.h GncAmountType, as the SQL backend stores it in
# ``entries.i_disc_type`` (gncAmountTypeToString): "VALUE" / "PERCENT".
AMT_VALUE = "VALUE"
AMT_PERCENT = "PERCENT"

# gncEntry.h GncDiscountHow, as stored in ``entries.i_disc_how``
# (gncEntryDiscountHowToString): "PRETAX" / "SAMETIME" / "POSTTAX".
DISC_PRETAX = "PRETAX"
DISC_SAMETIME = "SAMETIME"
DISC_POSTTAX = "POSTTAX"

# gncEntryCreate's defaults — what an entry holds when the stored
# string is one GnuCash does not recognize (its loader warns and keeps
# the default; rows written by server 1.2–1.4 carry '').
DEFAULT_DISC_TYPE = AMT_PERCENT
DEFAULT_DISC_HOW = DISC_PRETAX

_HUNDRED = Fraction(100)


@dataclass(frozen=True)
class TaxEntry:
    """One row of a tax table: ``kind`` is ``AMT_PERCENT`` (a rate,
    5 = 5%) or ``AMT_VALUE`` (a flat amount per line)."""

    kind: str
    amount: Fraction
    account: str


@dataclass(frozen=True)
class EntryValues:
    """``gncEntryComputeValueInt``'s results, unrounded.

    ``value`` is what the merchant gets (net of discount and of tax);
    ``value + sum(tax)`` is what the customer pays. ``taxes`` is one
    amount per distinct tax account, in tax-table order (same-account
    rows collapse, as ``gncAccountValueAdd`` does).
    """

    value: Fraction
    discount: Fraction
    taxes: tuple[tuple[str, Fraction], ...]

    @property
    def tax_total(self) -> Fraction:
        return sum((t for _, t in self.taxes), Fraction(0))


def entry_values(
    qty: Fraction,
    price: Fraction,
    tax_entries: list[TaxEntry] | tuple[TaxEntry, ...] | None,
    tax_included: bool,
    discount: Fraction = Fraction(0),
    discount_type: str = AMT_VALUE,
    discount_how: str = DISC_PRETAX,
) -> EntryValues:
    """``gncEntryComputeValueInt``.

    ``tax_entries`` is the line's tax table, or ``None``/empty when
    the line is not taxable (desktop passes ``i_taxable ? table :
    NULL``). ``qty`` is the STORED quantity — negative on a credit
    note's ordinary line; the caller negates the results for the
    document view, as ``gncEntryGetDocValue`` does.
    """
    entries = tuple(tax_entries or ())

    # Step 1: the aggregate price.
    aggregate = qty * price

    # Step 2: the pre-tax aggregate.
    tpercent = Fraction(0)
    tvalue = Fraction(0)
    for e in entries:
        if e.kind == AMT_VALUE:
            tvalue += e.amount
        elif e.kind == AMT_PERCENT:
            tpercent += e.amount
    tpercent /= _HUNDRED  # 5% -> .05

    if entries and tax_included:
        # aggregate = pretax + pretax*tpercent + tvalue
        pretax = (aggregate - tvalue) / (tpercent + 1)
    else:
        pretax = aggregate

    # Step 3: discount and taxes, in the order the line asks for.
    #
    #   Type:    discount    tax
    #   PRETAX   pretax      pretax-discount
    #   SAMETIME pretax      pretax
    #   POSTTAX  pretax+tax  pretax
    if discount_how in (DISC_PRETAX, DISC_SAMETIME):
        if discount_type == AMT_PERCENT:
            discount = pretax * (discount / _HUNDRED)
        result = pretax - discount
        if discount_how == DISC_PRETAX:
            pretax = result
    elif discount_how == DISC_POSTTAX:
        if discount_type == AMT_PERCENT:
            after_tax = pretax + pretax * tpercent + tvalue
            discount = after_tax * (discount / _HUNDRED)
        result = pretax - discount
    else:
        raise ValueError(f"unknown discount-how {discount_how!r}")

    # Step 4: the taxes, on ``pretax`` as step 3 left it.
    taxes: dict[str, Fraction] = {}
    for e in entries:
        if e.kind == AMT_VALUE:
            tax = e.amount
        elif e.kind == AMT_PERCENT:
            tax = pretax * (e.amount / _HUNDRED)
        else:
            continue
        taxes[e.account] = taxes.get(e.account, Fraction(0)) + tax

    return EntryValues(
        value=result, discount=discount, taxes=tuple(taxes.items()),
    )


def round_half_up(x: Fraction, fraction: int) -> Decimal:
    """``gnc_numeric_convert(x, fraction, GNC_HOW_DENOM_EXACT |
    GNC_HOW_RND_ROUND_HALF_UP)``: to the nearest ``1/fraction``, a tie
    going AWAY from zero (gnc-numeric.h: "rounding away from zero when
    there are two equidistant nearest integers").

    Returned as a ``Decimal`` at the commodity's quantum (``1.20``,
    not ``1.2``) for power-of-ten fractions.
    """
    scaled = x * fraction
    n = (abs(scaled.numerator) * 2 + scaled.denominator) // (
        scaled.denominator * 2
    )
    if scaled < 0:
        n = -n
    places = len(str(fraction)) - 1
    if fraction == 10 ** places:
        return Decimal(n).scaleb(-places)
    return Decimal(n) / Decimal(fraction)


@dataclass
class DocumentTotals:
    """What a document posts, in the DOCUMENT's sign (positive for an
    ordinary invoice, bill, or credit note).

    * ``net_by_account`` — each income/expense account's sum of
      per-line ROUNDED net values.
    * ``tax_by_account`` — each tax account's total, the lines'
      UNROUNDED taxes summed and then rounded once.
    * ``net`` — the subtotal; ``tax`` — the tax total; ``total`` —
      what the A/R or A/P split carries.
    """

    net_by_account: dict[str, Decimal] = field(default_factory=dict)
    tax_by_account: dict[str, Decimal] = field(default_factory=dict)
    net: Decimal = Decimal(0)
    tax: Decimal = Decimal(0)
    total: Decimal = Decimal(0)

    @property
    def by_account(self) -> dict[str, Decimal]:
        """Net and tax merged per account, as desktop merges its
        split list (``gncAccountValueAddList (splitinfo, taxes)``)."""
        merged = dict(self.net_by_account)
        for acct, amount in self.tax_by_account.items():
            merged[acct] = merged.get(acct, Decimal(0)) + amount
        return merged


def document_totals(
    lines: list[tuple[str, EntryValues]],
    fraction: int,
    is_credit_note: bool = False,
) -> DocumentTotals:
    """A document's net, taxes, and total from its lines.

    ``lines`` pairs each line's income/expense account with its
    ``entry_values`` (computed from the STORED quantity).

    * net: ``gncEntryRecomputeValues`` rounds each line's value
      (``i_value_rounded``), and ``gncInvoiceGetNetAndTaxesInternal``
      sums those — "Always use rounded net values to prevent creating
      imbalanced transactions on posting" (bug 628903).
    * tax: the same function accumulates each line's UNROUNDED tax
      values per account and rounds the per-account totals — "Round
      tax totals (accumulated per tax account) to prevent creating
      imbalanced transactions".
    * total: ``gncInvoiceGetTotalInternal``, net plus the rounded
      taxes.

    The sign: ``gncEntryGetDocValue`` / ``gncEntryGetDocTaxValues``
    negate for a credit note, whose quantities are stored negated.
    Half-up is symmetric about zero, so negating before or after the
    rounding is the same number.
    """
    sign = -1 if is_credit_note else 1
    out = DocumentTotals()
    raw_tax: dict[str, Fraction] = {}
    zero = round_half_up(Fraction(0), fraction)
    out.net = zero
    for account, ev in lines:
        net = round_half_up(sign * ev.value, fraction)
        out.net_by_account[account] = (
            out.net_by_account.get(account, zero) + net
        )
        out.net += net
        for tax_account, tax in ev.taxes:
            raw_tax[tax_account] = (
                raw_tax.get(tax_account, Fraction(0)) + sign * tax
            )
    out.tax = zero
    for tax_account, tax in raw_tax.items():
        rounded = round_half_up(tax, fraction)
        out.tax_by_account[tax_account] = rounded
        out.tax += rounded
    out.total = out.net + out.tax
    return out
