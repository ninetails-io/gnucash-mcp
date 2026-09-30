"""Where piecash writes a row GnuCash desktop would not have written,
the shape is corrected here — once, for every write path.

Imported unconditionally from ``book/_base.py`` so the replacements
hold whichever modules are enabled. One set of column defaults and
two validators:

Slot filler columns
    A ``slots`` row carries one value column per KVP type; the
    columns a slot does not use still hold something. GnuCash's SQL
    backend leaves ``double_val`` NULL and ``timespec_val`` at the
    epoch; piecash's defaults are the reverse (``0.0`` and NULL), so
    every slot the ORM wrote — the ``date-posted`` on each
    transaction, notes, void slots — differed from desktop's row in
    two columns (cross-currency twin, 2026-09-30; invisible to the
    earlier twins, whose dump printed only the typed column).

``Price.validate``
    piecash re-queries the row at LOCAL midnight, the only time of
    day it writes; a price at GnuCash's neutral time never matches
    and every save touching it raised.

``Split.validate``
    piecash's version does three things beyond checking the split:
    it writes a ``type='transaction'`` price for ANY cross-commodity
    split (six-decimal half-even value, local-midnight date, source
    ``user:split-register``, never updated once a row exists that
    day), and it stamps ``Buy``/``Sell`` into an empty ``action``
    on all of them. Desktop does neither that way (cross-currency
    twin, 2026-09-30: a USD→EUR transfer left action empty and a
    price of exactly ``10/9`` from ``user:price``; only a split
    entered in a stock register came back ``Buy``). The implied
    price is now written by ports of GnuCash's own two writers:

    * ``record_price`` (``Transaction.cpp``, called by the register
      as ``xaccTransRecordPrice(trans, PRICE_SOURCE_SPLIT_REG)``) —
      for a PRICED account (``xaccAccountIsPriced``: STOCK, MUTUAL,
      CURRENCY): value/amount rounded half-up to
      ``scu(currency) × COMMODITY_DENOM_MULT``.
    * ``create_price`` / ``new_price`` / ``update_price``
      (``dialog-transfer.cpp``, the exchange-rate dialog the
      register raises for any other foreign-commodity account) —
      the exact reduced ratio, stored against the non-currency
      commodity or, between currencies, against the default
      currency. The server is given both amounts, which is the
      dialog's to-amount path: ``PRICE_SOURCE_XFER_DLG_VAL``.

    Both honor GnuCash's same-day rule: an existing price from a
    preferred source stays; otherwise the row is updated in place.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from fractions import Fraction

from piecash._common import GncValidationError
from piecash.core.commodity import Price
from piecash.core.transaction import Split

from gnucash_mcp.book._currency import (
    CurrencyMixin,
    _price_row_utc,
    _price_tie_rank,  # noqa: F401  (documented ordering; used via rows below)
    _to_date,
)

# gnc-pricedb.h ``PriceSource``, in enum order: a LOWER index is the
# preferred source ("PRICE_SOURCE_EDIT_DLG will overwrite one with
# PRICE_SOURCE_FQ but not the reverse").
_GNC_PRICE_SOURCE_ORDER = (
    "user:price-editor",      # PRICE_SOURCE_EDIT_DLG
    "Finance::Quote",         # PRICE_SOURCE_FQ
    "user:price",             # PRICE_SOURCE_USER_PRICE
    "user:xfer-dialog",       # PRICE_SOURCE_XFER_DLG_VAL
    "user:split-register",    # PRICE_SOURCE_SPLIT_REG
    "user:split-import",      # PRICE_SOURCE_SPLIT_IMPORT
    "user:stock-split",       # PRICE_SOURCE_STOCK_SPLIT
    "user:stock-transaction",  # PRICE_SOURCE_STOCK_TRANSACTION
    "user:invoice-post",      # PRICE_SOURCE_INVOICE
    "temporary",              # PRICE_SOURCE_TEMP
    "invalid",                # PRICE_SOURCE_INVALID
)
_SRC_XFER_DLG = "user:xfer-dialog"
_SRC_SPLIT_REG = "user:split-register"

# gnc-pricedb.h
_CURRENCY_DENOM = 10000
_COMMODITY_DENOM_MULT = 10000

# Account.cpp ``xaccAccountIsPriced``.
_PRICED_ACCOUNT_TYPES = frozenset({"STOCK", "MUTUAL", "CURRENCY"})


def _source_rank(source: str | None) -> int:
    """Index in ``PriceSource``; an unknown string is
    ``PRICE_SOURCE_INVALID``, as ``gnc_price_get_source`` reads it."""
    try:
        return _GNC_PRICE_SOURCE_ORDER.index(source or "")
    except ValueError:
        return len(_GNC_PRICE_SOURCE_ORDER) - 1


def _round_half_up(value: Fraction, denom: int) -> Fraction:
    """``gnc_numeric_convert(value, denom, GNC_HOW_RND_ROUND_HALF_UP)``
    for a non-negative value. The result keeps ``denom`` when stored
    (see ``_stored``) — GnuCash does not reduce a converted value."""
    return Fraction((value * denom + Fraction(1, 2)).__floor__(), denom)


def _is_currency(commodity) -> bool:
    return (commodity.namespace or "") == "CURRENCY"


def _round_price(frm, to, value: Fraction) -> Fraction:
    """dialog-transfer.cpp ``round_price`` — used only to compare, so
    a sub-rounding difference does not dither an existing row."""
    if _is_currency(frm) and _is_currency(to):
        return _round_half_up(value, _CURRENCY_DENOM)
    if _is_currency(to):
        return _round_half_up(value, to.fraction * _COMMODITY_DENOM_MULT)
    if _is_currency(frm):
        return _round_half_up(value, frm.fraction * _COMMODITY_DENOM_MULT)
    return value


def _neutral(day) -> datetime:
    from gnucash_mcp.book._base import _neutral_time

    return _neutral_time(day)


def _same_day_price(session, a_guid: str, b_guid: str, when: datetime):
    """``gnc_pricedb_lookup_day_t64``: the pair's price, in either
    direction, on ``when``'s local calendar day and nearest to it in
    time; ``None`` when the day has none. Row:
    ``(guid, commodity_guid, currency_guid, stored_utc, source,
    Fraction value)``."""
    from sqlalchemy import text

    rows = session.execute(
        text(
            "SELECT guid, commodity_guid, currency_guid, date, source, "
            "value_num, value_denom FROM prices WHERE "
            "(commodity_guid = :a AND currency_guid = :b) OR "
            "(commodity_guid = :b AND currency_guid = :a)"
        ),
        {"a": a_guid, "b": b_guid},
    ).fetchall()
    day = when.astimezone().date()
    same = []
    for guid, comm, curr, raw, source, num, denom in rows:
        stored = _price_row_utc(raw)
        if stored is None or stored.astimezone().date() != day or not denom:
            continue
        same.append((guid, comm, curr, stored, source,
                     Fraction(int(num), int(denom))))
    if not same:
        return None
    # The list GnuCash walks is most-recent-first (later time, then
    # smaller GUID); nearest in time wins, and at equal distance the
    # row at or before ``when``.
    same.sort(key=lambda r: r[0])
    same.sort(key=lambda r: r[3], reverse=True)
    same.sort(key=lambda r: (abs((r[3] - when).total_seconds()), r[3] > when))
    return same[0]


def _stored(value: Fraction, denom: int | None) -> tuple[int, int]:
    """(num, denom) as GnuCash stores it: a converted value keeps the
    target denominator; an exact one is reduced."""
    if denom is None:
        return value.numerator, value.denominator
    scaled = value * denom
    return int(scaled), denom


def _insert_price(session, commodity_guid, currency_guid, when: datetime,
                  source: str, num: int, denom: int) -> None:
    from sqlalchemy import text

    from gnucash_mcp.book._base import _verify_write

    guid = uuid.uuid4().hex
    session.execute(
        text(
            "INSERT INTO prices (guid, commodity_guid, currency_guid, date, "
            "source, type, value_num, value_denom) VALUES "
            "(:g, :c, :u, :d, :s, 'transaction', :n, :dn)"
        ),
        {
            "g": guid, "c": commodity_guid, "u": currency_guid,
            "d": when.strftime("%Y-%m-%d %H:%M:%S"), "s": source,
            "n": num, "dn": denom,
        },
    )
    _verify_write(session, Price.__table__, guid, "implied price")


def _update_price(session, guid: str, when: datetime, source: str,
                  num: int, denom: int) -> None:
    from sqlalchemy import text
    from sqlalchemy.orm.util import identity_key

    from gnucash_mcp.book._base import _verify_none_remaining

    params = {
        "g": guid, "d": when.strftime("%Y-%m-%d %H:%M:%S"), "s": source,
        "n": num, "dn": denom,
    }
    session.execute(
        text(
            "UPDATE prices SET date = :d, source = :s, type = 'transaction', "
            "value_num = :n, value_denom = :dn WHERE guid = :g"
        ),
        params,
    )
    left = session.execute(
        text(
            "SELECT COUNT(*) FROM prices WHERE guid = :g AND NOT ("
            "source = :s AND type = 'transaction' AND value_num = :n "
            "AND value_denom = :dn)"
        ),
        params,
    ).scalar()
    _verify_none_remaining(left, f"implied price {guid[:8]} update")
    loaded = session.identity_map.get(identity_key(Price, guid))
    if loaded is not None:
        session.expire(loaded)


def _record_price_engine(session, split, when: datetime) -> None:
    """``record_price`` (Transaction.cpp), source SPLIT_REG."""
    comm = split.account.commodity
    curr = split.transaction.currency
    value = abs(Fraction(split.value) / Fraction(split.quantity))
    scu = curr.fraction
    source = _SRC_SPLIT_REG

    existing = _same_day_price(session, comm.guid, curr.guid, when)
    if existing is None:
        num, denom = _stored(
            _round_half_up(value, scu * _COMMODITY_DENOM_MULT),
            scu * _COMMODITY_DENOM_MULT,
        )
        _insert_price(session, comm.guid, curr.guid, when, source, num, denom)
        return

    guid, _e_comm, e_curr, _stored_at, old_source, old_value = existing
    swap = e_curr == comm.guid
    if (1 / value if swap else value) == old_value:
        return
    if _source_rank(old_source) < _source_rank(source) and not (
        old_source == _SRC_XFER_DLG and source == _SRC_SPLIT_REG
    ):
        return  # Existing price is preferred over this one.
    if swap:
        value = 1 / value
        scu = comm.fraction
    num, denom = _stored(
        _round_half_up(value, scu * _COMMODITY_DENOM_MULT),
        scu * _COMMODITY_DENOM_MULT,
    )
    _update_price(session, guid, when, source, num, denom)


def _dialog_from_to(account_comm, txn_curr, quantity: Fraction,
                    value: Fraction):
    """The exchange dialog's ``from``, ``to`` and price for one
    split: ``from`` is the side money leaves, ``to`` the side it
    arrives, and the price is to-amount / from-amount
    (``gnc_xfer_dialog_compute_price_value``)."""
    if quantity > 0:
        return txn_curr, account_comm, abs(quantity / value)
    return account_comm, txn_curr, abs(value / quantity)


def _dialog_new_price_shape(frm, to, rate: Fraction, default):
    """``new_price``: store against the non-currency commodity;
    between currencies, against the default currency when it is the
    ``from`` side. Returns ``(commodity, currency, value)``."""
    if _is_currency(frm) and not _is_currency(to):
        return to, frm, 1 / rate
    if default is not None and frm == default and to != default:
        return to, frm, 1 / rate
    return frm, to, rate


def _record_price_dialog(session, split, when: datetime, default) -> None:
    """``create_price`` (dialog-transfer.cpp), the to-amount path:
    source XFER_DLG_VAL, type TRN, exact ratio."""
    frm, to, rate = _dialog_from_to(
        split.account.commodity, split.transaction.currency,
        Fraction(split.quantity), Fraction(split.value),
    )
    source = _SRC_XFER_DLG

    existing = _same_day_price(session, frm.guid, to.guid, when)
    if existing is not None:
        guid, e_comm, e_curr, _stored_at, old_source, old_value = existing
        if _source_rank(old_source) < _source_rank(source):
            return  # Existing price is preferred, so won't supersede.
        by_guid = {frm.guid: frm, to.guid: to}
        if e_curr == frm.guid:  # reverse
            rate = 1 / rate
        e_from, e_to = by_guid[e_comm], by_guid[e_curr]
        if _round_price(e_from, e_to, rate) == _round_price(
            e_from, e_to, old_value,
        ):
            return  # Same price; no dithering.
        _update_price(
            session, guid, when, old_source, rate.numerator, rate.denominator,
        )
        return

    comm, curr, rate = _dialog_new_price_shape(frm, to, rate, default)
    _insert_price(
        session, comm.guid, curr.guid, when, source,
        rate.numerator, rate.denominator,
    )


def _cross_commodity_split_index(session) -> dict:
    """``{(account commodity guid, transaction currency guid, local
    day): [(value, quantity, account_type), ...]}`` over every
    cross-commodity split with a nonzero amount — one query, for the
    converter that restates piecash's implied prices."""
    from sqlalchemy import text

    index: dict = {}
    rows = session.execute(text(
        "SELECT a.commodity_guid, t.currency_guid, t.post_date, "
        "s.value_num, s.value_denom, s.quantity_num, s.quantity_denom, "
        "a.account_type FROM splits s "
        "JOIN accounts a ON a.guid = s.account_guid "
        "JOIN transactions t ON t.guid = s.tx_guid "
        "WHERE a.commodity_guid <> t.currency_guid AND s.quantity_num <> 0 "
        "AND s.value_num <> 0"
    )).fetchall()
    for comm, curr, raw, vn, vd, qn, qd, acct_type in rows:
        posted = _price_row_utc(raw)
        if posted is None or not vd or not qd:
            continue
        index.setdefault(
            (comm, curr, posted.astimezone().date()), [],
        ).append((Fraction(int(vn), int(vd)), Fraction(int(qn), int(qd)),
                  acct_type))
    return index


def _restated_piecash_price(candidates, stored: Fraction, account_comm,
                            txn_curr, default):
    """For one price piecash wrote (commodity = the split's account
    commodity, currency = the transaction currency, value = the
    ratio quantized to six decimals): the shape desktop's exchange
    dialog would have stored for the same split, or ``None`` when
    the originating split can't be identified unambiguously or sits
    in a priced account (whose six-decimal row is already
    ``record_price``'s shape)."""
    from decimal import Decimal

    shapes = set()
    for value, quantity, acct_type in candidates:
        ratio = abs(value / quantity)
        as_decimal = (
            Decimal(ratio.numerator) / Decimal(ratio.denominator)
        ).quantize(Decimal("0.000001"))
        if Fraction(as_decimal) != stored:
            continue
        if acct_type in _PRICED_ACCOUNT_TYPES or acct_type == "TRADING":
            return None
        frm, to, rate = _dialog_from_to(account_comm, txn_curr, quantity, value)
        comm, curr, rate = _dialog_new_price_shape(frm, to, rate, default)
        shapes.add((comm.guid, curr.guid, rate))
    if len(shapes) != 1:
        return None
    return next(iter(shapes))


def _restate_price(session, guid: str, commodity_guid: str,
                   currency_guid: str, value: Fraction) -> None:
    """Rewrite one piecash-written implied price as the exchange
    dialog's row: pair direction, exact value, ``user:xfer-dialog``."""
    from sqlalchemy import text

    from gnucash_mcp.book._base import _verify_none_remaining

    params = {
        "g": guid, "c": commodity_guid, "u": currency_guid,
        "s": _SRC_XFER_DLG, "n": value.numerator, "dn": value.denominator,
    }
    session.execute(
        text(
            "UPDATE prices SET commodity_guid = :c, currency_guid = :u, "
            "source = :s, value_num = :n, value_denom = :dn WHERE guid = :g"
        ),
        params,
    )
    left = session.execute(
        text(
            "SELECT COUNT(*) FROM prices WHERE guid = :g AND NOT ("
            "commodity_guid = :c AND currency_guid = :u AND source = :s "
            "AND value_num = :n AND value_denom = :dn)"
        ),
        params,
    ).scalar()
    _verify_none_remaining(left, f"implied price {guid[:8]} restated")


def _record_implied_price(split) -> None:
    """The price a cross-commodity split implies, written the way
    desktop writes it. No-op for a split in the transaction's own
    currency, a zero amount (a void), or a TRADING account (desktop
    generates those splits without a dialog or a register save)."""
    account = split.account
    txn = split.transaction
    if account is None or txn is None or account.commodity == txn.currency:
        return
    if not split.quantity or not split.value:
        return
    if account.type == "TRADING":
        return
    book = split.book
    session = book.session
    when = _neutral(_to_date(txn.post_date))
    if account.type in _PRICED_ACCOUNT_TYPES:
        _record_price_engine(session, split, when)
    else:
        try:
            default = book.default_currency
        except Exception:
            default = None
        _record_price_dialog(session, split, when, default)
    CurrencyMixin._invalidate_price_caches(book)


def _split_validate(self) -> None:
    """Replacement for piecash's ``Split.validate``: the same checks
    and precision normalisation; the implied price through
    ``_record_implied_price``; ``Buy``/``Sell`` stamped into an
    empty action only where desktop's register stamps it
    (``_stamp_stock_action``). Both happen only when the split is
    new or its value or quantity changed — piecash acted on ANY
    change to the split, so reconciling an old cross-currency split
    could mint a price for its day."""
    old = self.get_all_changes()

    if old["STATE_CHANGES"][-1] == "deleted":
        return

    if "_quantity_num" in old or "_value_num" in old:
        self.transaction._recalculate_balance = True

    if self.transaction_guid is None:
        raise GncValidationError("The split is not linked to a transaction")

    if self.transaction.currency == self.account.commodity:
        if self.quantity != self.value:
            raise GncValidationError(
                "The split has a quantity different from value "
                "while the transaction currency and the account commodity is the same"
            )
    else:
        if self.quantity is None:
            raise GncValidationError(
                "The split quantity is not defined while the split is on a commodity different from the transaction"
            )
        # Allow for either value to be 0.0 (or -0.0).
        if self.quantity * self.value < 0:
            raise GncValidationError(
                "The split quantity has not the same sign as the split value"
            )

    self._quantity_denom_basis = self.account.commodity_scu
    self._value_denom_basis = self.transaction.currency.fraction

    amounts_changed = (
        "new" in old["STATE_CHANGES"]
        or "_quantity_num" in old
        or "_value_num" in old
    )
    if amounts_changed:
        _record_implied_price(self)
        _stamp_stock_action(self)


def _stamp_stock_action(split) -> None:
    """``gnc_split_register_check_stock_shares``
    (split-register-control.cpp): in a register with a Shares column
    — the register of a STOCK, MUTUAL or CURRENCY-type account —
    entering a nonzero share count sets an empty Action to ``Buy``
    (positive) or ``Sell`` (negative). A bank or asset register has
    no such column, so a currency transfer's splits stay empty.

    Twin specimens, 2026-09-30: 3 AAPL entered in the AAPL register
    came back ``Buy``; USD → EUR transfers came back empty. piecash
    stamped both kinds."""
    account = split.account
    if account is None or account.type not in _PRICED_ACCOUNT_TYPES:
        return
    if split.action or not split.quantity:
        return
    split.action = "Sell" if split.quantity < 0 else "Buy"


def _price_validate(self) -> None:
    """Replacement for piecash's ``Price.validate``. The original
    re-queries the row by ``date=self.date``; that column binds a
    date at LOCAL midnight, the only shape piecash itself writes, so
    a price stored at GnuCash's neutral time (every price desktop's
    editor writes, and ours since the price twin of 2026-09-29) never
    matches and ``.one()`` raises ``NoResultFound`` on any save that
    touches it. Same uniqueness rule, compared by calendar day."""
    same_day = [
        p for p in self.book.session.query(Price).filter_by(
            commodity=self.commodity, currency=self.currency, source=self.source,
        )
        if _to_date(p.date) == _to_date(self.date)
    ]
    if len(same_day) > 1:
        raise ValueError("{} already exists in this book".format(self))


def _use_gnucash_slot_fillers() -> None:
    """Make an ORM-inserted slot's unused columns GnuCash's:
    ``double_val`` NULL, ``timespec_val`` the epoch. Typed slots set
    their own column explicitly, so only the fillers change."""
    from datetime import timezone

    from piecash.kvp import Slot
    from sqlalchemy import ColumnDefault

    table = Slot.__table__
    table.c.double_val.default = None
    epoch = ColumnDefault(datetime(1970, 1, 1, tzinfo=timezone.utc))
    table.c.timespec_val.default = epoch
    epoch._set_parent_with_dispatch(table.c.timespec_val)


_use_gnucash_slot_fillers()
Price.validate = _price_validate
Split.validate = _split_validate
