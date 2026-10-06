"""InvestmentsMixin — commodities, prices, and lots (cost-basis tracking).

Commodities are non-currency assets (stocks, mutual funds). Prices
record per-date quotes. Lots group purchase splits so that capital
gains can be computed per-lot when shares are sold.

Depends on shared helpers from BaseGnuCashBook:
  - self.open, self._resolve_guid, self._find_account,
    self._find_split, self._find_commodity,
    self._require_default_currency, self._get_or_create_currency
  - _to_date, _commodity_to_compact_line, _lot_to_compact_line
    (module-level in _base)
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

import piecash
from piecash.core.commodity import Price
from piecash.core.transaction import Lot

from gnucash_mcp.book._currency import _price_row_utc, _price_tie_rank
from gnucash_mcp.book._base import _commodity_quantum
from gnucash_mcp.book._base import _check_one_line
from gnucash_mcp.book._base import (
    _format_account_amount,
    _lot_cache_flag,
    _LOT_CLOSED,
    _LOT_CLOSED_UNKNOWN,
    _LOT_OPEN,
    _lot_is_closed,
    _commodity_to_compact_line,
    _guid_prefix_map,
    _is_voided,
    _lot_to_compact_line,
    _neutral_time,
    _verify_delete,
    _verify_none_remaining,
    _to_date,
    _to_decimal,
    _unique_prefix,
    _check_text,
    _TEXT_WIDTH,
    _SLOT_TEXT_WIDTH,
)
from gnucash_mcp._format import (
    _format_converted,
    _format_number,
    _format_price,
    _paginate,
    _round_converted,
)


class InvestmentsMixin:
    """Commodity/price/lot CRUD and capital-gain calculation."""

    # ── Commodities and prices ────────────────────────────────────

    def list_commodities(
        self, compact: bool = True, limit: int = 50, offset: int = 0,
        stale_days: int | None = None, held_only: bool = False,
    ) -> dict | str:
        """List all commodities in the book with latest prices.

        Leads with a ``Showing X-Y of Z commodities`` indicator; page
        with ``offset``. With no filter arguments the output is the
        complete list, unchanged — filters are opt-in and AND-combine.

        Args:
            compact: If True (default), return compact one-line-per-commodity
                     string. If False, return the verbose envelope with
                     commodities grouped by namespace.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.
            stale_days: Only commodities whose latest market price is
                at least this many days old — INCLUDING commodities
                with no price at all (undefined staleness is maximal
                staleness), EXCLUDING the book default currency
                (its price is trivially 1). Rows gain a staleness
                marker. The price-update work list is
                ``stale_days=30, held_only=True``.
            held_only: Only commodities some real (non-template)
                account is denominated in.

        Returns:
            If compact: indicator + newline-separated commodity lines.
            If not compact: envelope ``{showing, total, offset, count,
            default_currency, commodities}`` (commodities grouped by
            namespace, limited to the page).
        """
        with self.open(readonly=True) as book:
            by_namespace: dict[str, list[dict]] = {}

            # Latest market quote per commodity. ``_find_prices`` is
            # newest-first with the same-date tie-break every
            # valuation path uses, so the first row per commodity is
            # the one the reports price by — a transaction's implied
            # rate included, as desktop counts it.
            latest_market: dict[str, tuple[date, "Price"]] = {}
            for p in self._find_prices(book):
                latest_market.setdefault(
                    p.commodity.guid, (_to_date(p.date), p),
                )

            today = date.today()
            default_commodity = self._require_default_currency(book)
            in_use_accounts: set | None = None
            if held_only:
                # Same in-use definition as the dashboard's stale-
                # price warning: any non-ROOT, non-template account
                # denominated in the commodity.
                template_guids = self._template_account_guids(book)
                in_use_accounts = {
                    a.commodity.guid for a in book.accounts
                    if a.type != "ROOT" and a.guid not in template_guids
                }

            for commodity in book.commodities:
                ns = commodity.namespace
                # 'template' = GnuCash's SX-scaffolding pseudo-commodity.
                if ns.lower() == "template":
                    continue
                if (
                    in_use_accounts is not None
                    and commodity.guid not in in_use_accounts
                ):
                    continue
                days_stale: int | None = None
                no_price = False
                if stale_days is not None:
                    if commodity == default_commodity:
                        continue
                    latest = latest_market.get(commodity.guid)
                    if latest is None:
                        no_price = True
                    else:
                        days_stale = (today - latest[0]).days
                        if days_stale < stale_days:
                            continue
                if ns not in by_namespace:
                    by_namespace[ns] = []

                entry: dict = {
                    "mnemonic": commodity.mnemonic,
                    "fullname": commodity.fullname,
                    "fraction": commodity.fraction,
                }

                if commodity == default_commodity:
                    # The book's default currency has no price OF its
                    # own: every other line is priced IN it, and a row
                    # stored the other way round (USD/EUR, from an
                    # older writer) restates a EUR rate backwards.
                    # ``get_latest_price`` answers null here too
                    # (bookkeeper ruling B10, 2026-09-30).
                    entry["latest_price"] = None
                    entry["default_currency"] = True
                elif commodity.guid in latest_market:
                    _, price = latest_market[commodity.guid]
                    entry["latest_price"] = {
                        "value": str(price.value),
                        "currency": price.currency.mnemonic,
                        "date": _to_date(price.date).isoformat(),
                    }
                else:
                    # Explicit null: "no price on file" is an answer
                    # (the compact view says it in words); a missing
                    # key reads as a field the caller forgot to ask
                    # for.
                    entry["latest_price"] = None
                # Staleness markers only under the filter — the
                # unfiltered listing's shape is unchanged.
                if days_stale is not None:
                    entry["days_stale"] = days_stale
                if no_price:
                    entry["no_price"] = True

                by_namespace[ns].append(entry)

            for ns in by_namespace:
                by_namespace[ns].sort(key=lambda c: c["mnemonic"])

            default_currency = self._require_default_currency(book).mnemonic

            # Flatten to one ordered list (namespace, then mnemonic —
            # the compact render order) so pagination has a flat
            # sequence to slice; the verbose path re-groups the page.
            flat = [
                (ns, entry)
                for ns, entries in sorted(by_namespace.items())
                for entry in entries
            ]
            page, indicator = _paginate(
                flat, offset=offset, limit=limit,
                entity_name="commodities",
            )

            if compact:
                lines = [indicator]
                lines += [
                    _commodity_to_compact_line(ns, entry)
                    for ns, entry in page
                ]
                return "\n".join(lines)
            else:
                paged_by_ns: dict[str, list[dict]] = {}
                for ns, entry in page:
                    paged_by_ns.setdefault(ns, []).append(entry)
                return {
                    "showing": indicator,
                    "total": len(flat),
                    "offset": offset,
                    "count": len(page),
                    "default_currency": default_currency,
                    "commodities": paged_by_ns,
                }

    def create_commodity(
        self,
        mnemonic: str,
        fullname: str,
        namespace: str = "FUND",
        fraction: int | None = None,
        cusip: str | None = None,
    ) -> dict:
        """Create a new commodity (stock, mutual fund, etc.) in the book.

        Args:
            mnemonic: Symbol (e.g., "VTSAX", "AAPL"). Must be unique within namespace.
            fullname: Full name (e.g., "Vanguard Total Stock Market Index Fund").
            namespace: Grouping category. Common values: "FUND", "NASDAQ",
                       "NYSE", "AMEX", or any custom string. Default "FUND".
            fraction: Smallest fractional unit. Use 10000 for 4 decimal places
                      (standard for shares), 100 for 2, 1000000 for 6 (crypto).
                      Default 10000. A CURRENCY is GnuCash's, from the ISO
                      4217 table: its fraction, name, and quote flag come
                      from there, and a fraction that disagrees is refused.
            cusip: Optional CUSIP/ISIN identifier for the security.

        Returns:
            Dict with mnemonic, namespace, fullname, fraction, and status.

        Raises:
            ValueError: If commodity already exists in that namespace.
        """
        # Every free-text argument through the one text gate (scoped
        # review 2026-10-05, I-4): no control characters, GnuCash's
        # column width.
        for _field in ("name", "description", "notes", "title", "reference", "fullname", "mnemonic", "memo", "action"):
            _check_text(
                locals().get(_field),
                _SLOT_TEXT_WIDTH if _field == "notes" else _TEXT_WIDTH, _field,
            )
            if _field in ("name", "title", "reference", "fullname", "mnemonic", "action"):
                _check_one_line(locals().get(_field), _field)
        # Validate up front — useful errors instead of an
        # IntegrityError or silent corruption downstream.
        if not mnemonic or not mnemonic.strip():
            raise ValueError("Commodity mnemonic cannot be empty")
        if not fullname or not fullname.strip():
            raise ValueError("Commodity fullname cannot be empty")
        if not namespace or not namespace.strip():
            raise ValueError("Commodity namespace cannot be empty")
        for label, value in (
            ("mnemonic", mnemonic),
            ("fullname", fullname),
            ("namespace", namespace),
        ):
            if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
                raise ValueError(
                    f"Commodity {label} contains control characters. "
                    f"Got: {value!r}."
                )
        if cusip is not None and any(
            ord(ch) < 0x20 or ord(ch) == 0x7f for ch in cusip
        ):
            raise ValueError(
                f"Commodity cusip contains control characters. "
                f"Got: {cusip!r}."
            )
        # A currency is never invented: GnuCash's come from the ISO
        # table. The share default (10000) once landed on a USD a
        # caller created here, and every amount in it read at 4 places.
        if namespace == "CURRENCY":
            from piecash.core.currency_ISO import ISO_currencies

            iso = ISO_currencies.get(mnemonic)
            if iso is None:
                raise ValueError(
                    f"{mnemonic} is not an ISO 4217 currency code — "
                    f"GnuCash's currencies come from the ISO table. Use "
                    f"another namespace for a non-currency commodity."
                )
            iso_fraction = 10 ** int(iso.fraction)
            if fraction is not None and fraction != iso_fraction:
                raise ValueError(
                    f"The ISO 4217 fraction for {mnemonic} is "
                    f"{iso_fraction}, and GnuCash stores the currency "
                    f"that way. Omit fraction, or pass {iso_fraction}."
                )
            fraction = iso_fraction
        elif fraction is None:
            fraction = 10000
        # fraction must be a positive integer — zero or negative
        # breaks every quantity computation that divides by it.
        if not isinstance(fraction, int) or fraction <= 0:
            raise ValueError(
                f"Commodity fraction must be a positive integer. "
                f"Got: {fraction!r}."
            )
        # GnuCash's fraction is a power of ten (its editor offers 1
        # to 1/1,000,000,000). Fraction 3 raised on every later
        # write and 8 stored 0.3 shares as 300/1000; 10^12 overflows
        # the 64-bit numerator above a few million units
        # (adversarial review 2026-09-30, MM-14 / IV-25).
        if fraction not in {10 ** n for n in range(10)}:
            raise ValueError(
                f"Commodity fraction must be a power of ten from 1 to "
                f"1000000000 (10000 stores four decimal places). "
                f"Got: {fraction!r}."
            )

        with self.open(readonly=False) as book:
            existing = self._find_commodity(book, mnemonic, namespace)
            if existing:
                raise ValueError(
                    f"Commodity {namespace}:{mnemonic} already exists"
                )

            if namespace == "CURRENCY":
                commodity = self._get_or_create_currency(book, mnemonic)
            else:
                commodity = piecash.Commodity(
                    namespace=namespace,
                    mnemonic=mnemonic,
                    fullname=fullname,
                    fraction=fraction,
                    cusip=cusip or "",
                    book=book,
                )

            book.save()

            return {
                "mnemonic": commodity.mnemonic,
                "namespace": commodity.namespace,
                "fullname": commodity.fullname,
                "fraction": commodity.fraction,
                "status": "created",
            }

    # gnc-pricedb.h, verbatim: the only source strings GnuCash's
    # price editor recognizes. Anything else renders as "Invalid"
    # (gnc_price_source_string_to_enum), which is how every price the
    # sample generators wrote as ``user:market_data`` looked in
    # desktop (2026-09-29). Spellings the server's own history used
    # map to the string GnuCash uses for a quote feed.
    _GNC_PRICE_SOURCES = frozenset({
        "user:price-editor", "Finance::Quote", "user:price",
        "user:xfer-dialog", "user:split-register", "user:split-import",
        "user:stock-split", "user:stock-transaction", "user:invoice-post",
        "temporary", "invalid",
    })
    _PRICE_SOURCE_ALIASES = {
        "user:market_data": "Finance::Quote",
        "user:market-data": "Finance::Quote",
    }
    # gnc-pricedb.h PRICE_TYPE_*: "bid", "ask", "last", "nav",
    # "transaction", "unknown".
    _GNC_PRICE_TYPES = frozenset({"bid", "ask", "last", "nav", "transaction", "unknown"})

    @staticmethod
    def _gnc_price_source(source: str) -> str:
        source = InvestmentsMixin._PRICE_SOURCE_ALIASES.get(source, source)
        if source not in InvestmentsMixin._GNC_PRICE_SOURCES:
            raise ValueError(
                f"Price source {source!r} is not one GnuCash recognizes "
                f"(it would show as Invalid in the price editor). Use one "
                f"of: {', '.join(sorted(InvestmentsMixin._GNC_PRICE_SOURCES))}. "
                f"A quote feed is 'Finance::Quote'; a price you typed is "
                f"'user:price'."
            )
        return source

    @staticmethod
    def _gnc_price_type(price_type: str) -> str:
        if price_type not in InvestmentsMixin._GNC_PRICE_TYPES:
            raise ValueError(
                f"Price type {price_type!r} is not one GnuCash recognizes. "
                f"Use one of: {', '.join(sorted(InvestmentsMixin._GNC_PRICE_TYPES))}."
            )
        return price_type

    def _migrate_price_shapes(self, book) -> dict:
        """Write path only: price rows the server (or its generators)
        wrote before 2026-09-29 to desktop's shape.

        * A source string GnuCash recognizes (``user:market_data``
          and its hyphenated twin become ``Finance::Quote``, any
          other unknown string ``user:price``).
        * The date at the neutral time. Only a row at the server's
          local-midnight shape moves; a row desktop stamped at some
          other time is desktop's business.
        * A quote's value reduced (``gnc_numeric_reduce``: desktop's
          editor stores 178.70 as 1787/10; piecash keeps the typed
          denominator, 17870/100). A ``type='transaction'`` row is
          NOT reduced — ``record_price`` stores those at a fixed
          ``scu × 10000`` denominator.
        * An implied price piecash wrote for a currency account
          (``user:split-register``, six decimals, the split's own
          direction) restated as the exchange dialog's row — exact
          ratio, stored against the default currency,
          ``user:xfer-dialog`` — when the split it came from can be
          identified. Done only in the pass that moves the row off
          local midnight, so it runs once per row.

        Runs through ``_upgrade_book_shapes``."""
        from fractions import Fraction
        from sqlalchemy import text

        from gnucash_mcp.book._piecash_shapes import (
            _cross_commodity_split_index,
            _restate_price,
            _restated_piecash_price,
        )

        out: dict = {}
        sources = 0
        dates = 0
        values = 0
        restated = 0
        split_index = None
        commodities = None
        rows = book.session.execute(
            text(
                "SELECT guid, source, date, value_num, value_denom, type, "
                "commodity_guid, currency_guid FROM prices"
            )
        ).fetchall()
        for guid, src, raw, num, denom, ptype, comm_guid, curr_guid in rows:
            src = src or ""
            if src not in self._GNC_PRICE_SOURCES:
                new_src = self._PRICE_SOURCE_ALIASES.get(src, "user:price")
                book.session.execute(
                    text("UPDATE prices SET source = :s WHERE guid = :g"),
                    {"s": new_src, "g": guid},
                )
                left = book.session.execute(
                    text("SELECT COUNT(*) FROM prices WHERE guid = :g AND source <> :s"),
                    {"s": new_src, "g": guid},
                ).scalar()
                _verify_none_remaining(left, f"price {guid[:8]} source → {new_src}")
                sources += 1
            as_utc = _price_row_utc(raw)
            local = as_utc.astimezone() if as_utc is not None else None
            move_date = local is not None and local.time() == datetime.min.time()
            implied = ptype == "transaction"
            stored = Fraction(int(num), int(denom)) if denom else None
            reduce_value = (
                not implied and stored is not None
                and stored.denominator != int(denom)
            )
            if move_date or reduce_value:
                self._stamp_price_row(
                    book, guid,
                    price_date=local.date() if move_date else None,
                    value=stored if reduce_value else None,
                )
                dates += int(move_date)
                values += int(bool(reduce_value))
            if (
                implied and move_date and stored is not None
                and src == "user:split-register"
            ):
                if split_index is None:
                    split_index = _cross_commodity_split_index(book.session)
                    commodities = {c.guid: c for c in book.commodities}
                    try:
                        default = self._require_default_currency(book)
                    except Exception:
                        default = None
                comm = commodities.get(comm_guid)
                curr = commodities.get(curr_guid)
                shape = None
                if comm is not None and curr is not None:
                    shape = _restated_piecash_price(
                        split_index.get((comm_guid, curr_guid, local.date()), []),
                        stored, comm, curr, default,
                    )
                if shape is not None:
                    _restate_price(book.session, guid, *shape)
                    restated += 1
        if sources:
            out["price_sources_normalized"] = sources
        if dates:
            out["price_dates_normalized"] = dates
        if values:
            out["price_values_reduced"] = values
        if restated:
            out["implied_prices_restated"] = restated
        if sources or dates or values or restated:
            book.session.expire_all()
            self._invalidate_price_caches(book)
        return out

    @staticmethod
    def _stamp_price_row(book, guid: str, price_date=None, value=None) -> None:
        """Bring one price row to the price editor's shape: ``date``
        at the neutral time (10:59:00 UTC) and ``value_num`` /
        ``value_denom`` reduced, as ``gnc_numeric_reduce`` leaves
        them. piecash's ``Price.date`` column accepts only a bare
        date and binds it at LOCAL midnight, and its value hybrid
        keeps the typed denominator, so the ORM can't write either;
        one portable UPDATE, verified by read-back. The date is
        bound as the ``YYYY-MM-DD HH:MM:SS`` string GnuCash stores in
        SQLite and PostgreSQL/MySQL cast to a timestamp. ``value`` is
        anything ``Fraction`` accepts (a Decimal, a string, a
        Fraction)."""
        from fractions import Fraction
        from sqlalchemy import text

        sets = []
        params: dict = {"g": guid}
        if price_date is not None:
            params["d"] = _neutral_time(price_date).strftime("%Y-%m-%d %H:%M:%S")
            sets.append("date = :d")
        if value is not None:
            frac = Fraction(value)
            params["n"] = frac.numerator
            params["dn"] = frac.denominator
            sets.append("value_num = :n")
            sets.append("value_denom = :dn")
        if not sets:
            return
        book.session.execute(
            text(f"UPDATE prices SET {', '.join(sets)} WHERE guid = :g"),
            params,
        )
        left = book.session.execute(
            text(
                "SELECT COUNT(*) FROM prices WHERE guid = :g AND NOT ("
                + " AND ".join(sets) + ")"
            ),
            params,
        ).scalar()
        _verify_none_remaining(left, f"price {guid[:8]} → editor shape")
        stale = book.session.get(Price, guid)
        if stale is not None:
            book.session.expire(stale)

    @staticmethod
    def _check_price(comm, resolved_currency, value) -> None:
        """What makes a price row a price, checked before either
        entry point writes one (single and batch share it, and the
        batch's dry run reports the same refusal).

        - Positive. Valuation multiplies a holding by the latest
          row, so ``-1.10`` valued 1,000 EUR at −1,100 and ``0``
          dropped the holding from the balance sheet — while the
          rate a posting uses skipped both rows, so two readers
          disagreed about whether the row existed (review C13).
        - Of one commodity IN ANOTHER. USD priced in USD is always 1
          and tells no reader anything; the row was reachable by
          following the stale-rate refusal's own suggestion on a
          book whose default currency was the invoice's (review C35).
        - A number a price can be. ``9e999998`` parses, then spent
          24 seconds becoming a rational before being refused.
        """
        amount = _to_decimal(value)
        if not amount.is_finite() or amount <= 0:
            raise ValueError(
                f"A price must be greater than zero; got {value}."
            )
        if abs(amount.adjusted()) > 15:
            raise ValueError(
                f"Price {value} is out of range for a price."
            )
        if comm.guid == resolved_currency.guid:
            raise ValueError(
                f"A price of {comm.mnemonic} in {comm.mnemonic} is "
                f"always 1. Name the other currency: "
                f"currency='<the currency {comm.mnemonic} is priced in>'."
            )

    @staticmethod
    def _price_plan(book, comm, resolved_currency, price_date, source):
        """What ``gnc_pricedb_add_price`` would do with a price for
        this pair on this day — ``(action, existing_row)``.

        GnuCash keeps one price per pair per day. Its ``add_price``
        (gnc-pricedb.cpp) looks up the day's price for the pair, in
        EITHER direction (``gnc_pricedb_lookup_day_t64``), and then:

            if (p->source > old_price->source) return FALSE;
            gnc_pricedb_remove_price (db, old_price);

        a new price whose source ranks worse is turned away ("Better
        price already in DB"); one that ranks equal or better takes
        the day. ``action`` is ``created`` (no price that day),
        ``updated`` (same source, same direction), ``replaced`` (a
        lower-ranked or opposite-direction row gives way), or
        ``kept`` (the existing row outranks the new one, which is not
        written). No mutation; the dry run and the write share it.

        The engine twin (``tests/test_parity_prices.py``) found one
        more thing desktop does: the price it turns away was saved to
        the SQL file before ``add_price`` was asked, so the row stays
        on disk, unreferenced in memory until the next load. The
        server does not write that row (bookkeeper ruling,
        2026-09-30: "fidelity means desktop-readable, not
        litter-compatible").
        """
        from gnucash_mcp.book._piecash_shapes import (
            _same_day_price,
            _source_rank,
        )

        old = _same_day_price(
            book.session, comm.guid, resolved_currency.guid,
            _neutral_time(price_date),
        )
        if old is None:
            return "created", None
        _guid, old_comm, _curr, _stored, old_source, _value = old
        if _source_rank(source) > _source_rank(old_source):
            return "kept", old
        if old_comm == comm.guid and old_source == source:
            return "updated", old
        return "replaced", old

    @staticmethod
    def _upsert_price(
        book, comm, resolved_currency, price_date,
        value: str, price_type: str, source: str,
    ) -> str:
        """Write one price the way ``gnc_pricedb_add_price`` admits
        it (``_price_plan``); NO save.

        Single chokepoint shared by ``create_price`` and
        ``create_prices`` so single and batch entry can't diverge.
        The caller owns ``book.save()`` — batch saves once for the
        whole set.

        Returns ``"created"``, ``"updated"``, ``"replaced"``, or
        ``"kept"`` (nothing written: the day's existing price
        outranks this one).
        """
        from sqlalchemy.orm.util import identity_key

        source = InvestmentsMixin._gnc_price_source(source)
        price_type = InvestmentsMixin._gnc_price_type(price_type)
        book.flush()
        action, old = InvestmentsMixin._price_plan(
            book, comm, resolved_currency, price_date, source,
        )
        if action == "kept":
            return action

        # Desktop stores a price's date at the neutral time (10:59:00
        # UTC) and its value reduced; piecash binds a bare date at
        # local midnight and keeps the typed denominator (price twin,
        # 2026-09-29), so the row is flushed and re-stamped.
        if old is not None and old[1] == comm.guid:
            # Same direction: the row is rewritten where it stands.
            existing = book.session.query(Price).filter_by(guid=old[0]).first()
            existing.source = source
            existing.type = price_type
            book.flush()
            InvestmentsMixin._stamp_price_row(
                book, existing.guid, price_date=price_date,
                value=_to_decimal(value),
            )
            return action
        if old is not None:
            # Quoted the other way round: the old row goes
            # (gnc_pricedb_remove_price) and the new one is inserted.
            # Raw SQL — a price row carries no slots to cascade.
            book.session.execute(
                Price.__table__.delete().where(
                    Price.__table__.c.guid == old[0]
                )
            )
            _verify_delete(
                book.session, Price.__table__, {"guid": old[0]},
                f"superseded price {old[0][:8]}",
            )
            loaded = book.session.identity_map.get(
                identity_key(Price, old[0])
            )
            if loaded is not None:
                book.session.expunge(loaded)
        created = piecash.Price(
            commodity=comm,
            currency=resolved_currency,
            date=price_date,
            value=_to_decimal(value),
            type=price_type,
            source=source,
        )
        book.flush()
        InvestmentsMixin._stamp_price_row(
            book, created.guid, price_date=price_date, value=_to_decimal(value),
        )
        return action

    @staticmethod
    def _kept_price_reason(old, source: str) -> str:
        """Why a price was not written, for the caller."""
        return (
            f"not written: the {old[4]!r} price already recorded for "
            f"this date outranks {source!r}, and GnuCash keeps one "
            f"price per pair per day (its own add_price turns this "
            f"one away). To replace it, record the price with "
            f"source='user:price-editor', or delete_price the "
            f"existing one first."
        )

    @staticmethod
    def _same_date_outranker(
        book, comm, resolved_currency, price_date, source,
    ) -> str | None:
        """Source of a same-day row desktop will use instead of the
        one just written under ``source``, or None when the written
        row is the day's current price.

        The rule is ``_price_tie_rank`` — GnuCash's own: the later
        stored time, then the smaller GUID. An operator who just
        wrote a price and can't see it winning has been misled by
        silence — both ``create_price`` and ``create_prices``
        surface this so single and batch entry can't diverge on it.
        Reads the rows fresh (not the memo) so a batch sees its own
        earlier writes.
        """
        from gnucash_mcp.book._currency import CurrencyMixin

        own = None
        others = []
        for p in CurrencyMixin._query_prices_with_time(
            book, comm.guid, resolved_currency.guid,
        ):
            if _to_date(p.date) != price_date:
                continue
            if p.source == source:
                own = p
            else:
                others.append(p)
        if own is None or not others:
            return None
        best = max(others, key=_price_tie_rank)
        if _price_tie_rank(best) > _price_tie_rank(own):
            return best.source
        return None

    def _resolve_price_commodity(self, book, mnemonic: str,
                                 namespace: str | None):
        """Resolve a batch price row's commodity.

        With ``namespace``: exact lookup. Without: search across
        namespaces (excluding GnuCash's ``template`` pseudo-
        commodity); exactly one match resolves, zero or several
        raise naming the fix — most books have unambiguous
        mnemonics, so the ns column stays optional.
        """
        if namespace:
            comm = self._find_commodity(book, mnemonic, namespace)
            if not comm:
                raise ValueError(
                    f"Commodity not found: {namespace}:{mnemonic}"
                )
            return comm
        matches = [
            c for c in book.commodities
            if c.mnemonic == mnemonic
            and c.namespace.lower() != "template"
        ]
        if not matches:
            raise ValueError(f"Commodity not found: {mnemonic}")
        if len(matches) > 1:
            namespaces = ", ".join(sorted(c.namespace for c in matches))
            raise ValueError(
                f"Commodity '{mnemonic}' is ambiguous across "
                f"namespaces ({namespaces}) — supply the ns column"
            )
        return matches[0]

    def create_prices(
        self,
        prices: list[dict],
        on_error: str = "abort",
        dry_run: bool = False,
    ) -> dict:
        """Record MANY prices in one book-open / one save.

        Each entry: ``{ref, commodity, date (date), value,
        namespace (optional), currency (optional — quote currency,
        defaults to the book default), source (optional, default
        "user:price"), price_type (optional, default "last")}``.

        Per-row semantics are ``create_price``'s exactly (shared
        upsert chokepoint): same (commodity, currency, date,
        source) updates in place → ``status: updated``; otherwise
        ``created``. ``on_error="abort"`` (default) sinks the whole
        batch on any invalid row; ``"skip"`` keeps the good rows.
        ``dry_run`` reports ``would_create`` / ``would_update``
        without writing.

        Returns ``{"results": TSV}`` — columns
        ``ref, status, commodity, date, value, currency, reason``.
        ``reason`` also notes when a same-date row from a
        higher-ranked source outranks the written row as the
        effective rate.
        """
        if on_error not in ("abort", "skip"):
            raise ValueError("on_error must be 'abort' or 'skip'")
        refs = [p["ref"] for p in prices]
        if len(set(refs)) != len(refs):
            raise ValueError(
                "duplicate ref in batch — each ref must be unique"
            )

        readonly = dry_run
        by_ref: dict = {}
        with self.open(readonly=readonly) as book:
            prepared = []
            for p in prices:
                ref = p["ref"]
                try:
                    comm = self._resolve_price_commodity(
                        book, p["commodity"], p.get("namespace"),
                    )
                    cur_code = p.get("currency")
                    if cur_code is None:
                        resolved_currency = (
                            self._require_default_currency(book)
                        )
                    elif readonly:
                        resolved_currency = self._find_commodity(
                            book, cur_code, "CURRENCY",
                        )
                        if not resolved_currency:
                            raise ValueError(
                                f"Currency '{cur_code}' not found "
                                f"in book. Dry run cannot create "
                                f"new currencies."
                            )
                    else:
                        resolved_currency = (
                            self._get_or_create_currency(book, cur_code)
                        )
                    value = p["value"]
                    # Non-decimal, non-positive, or a self-price:
                    # rejected per row, dry run included.
                    self._check_price(comm, resolved_currency, value)
                    prepared.append({
                        "ref": ref,
                        "comm": comm,
                        "currency": resolved_currency,
                        "date": p["date"],
                        "value": str(value),
                        "type": p.get("price_type") or "last",
                        "source": p.get("source") or "user:price",
                    })
                except (ValueError, KeyError) as e:
                    by_ref[ref] = {
                        "ref": ref, "status": "rejected",
                        "reason": str(e),
                    }

            # GnuCash keeps one price per pair per day
            # (``_price_plan``), so two rows of one batch for the
            # same pair and date — in either direction, from any
            # source — cannot both stand: the second would replace
            # the first or be turned away by it, while dry_run (each
            # row checked against the original book state) would
            # report both as would_create. Rejecting the second up
            # front keeps dry-run and live agreeing and makes the
            # caller say which price they mean.
            seen_identity: dict = {}
            deduped = []
            for row in prepared:
                identity = (
                    frozenset((row["comm"].guid, row["currency"].guid)),
                    row["date"],
                )
                first_ref = seen_identity.get(identity)
                if first_ref is not None:
                    by_ref[row["ref"]] = {
                        "ref": row["ref"], "status": "rejected",
                        "reason": (
                            f"duplicate price — same pair and date as "
                            f"ref '{first_ref}'; GnuCash keeps one "
                            f"price per pair per day"
                        ),
                    }
                    continue
                seen_identity[identity] = row["ref"]
                deduped.append(row)
            prepared = deduped

            if by_ref and on_error == "abort":
                for row in prepared:
                    by_ref[row["ref"]] = {
                        "ref": row["ref"], "status": "rejected",
                        "reason": "batch_aborted",
                    }
                return self._prices_envelope(prices, by_ref)

            wrote = False
            for row in prepared:
                # The same decision either way (``_price_plan``); the
                # dry run only skips the write.
                action, old = self._price_plan(
                    book, row["comm"], row["currency"], row["date"],
                    row["source"],
                )
                if dry_run:
                    status = {
                        "created": "would_create",
                        "updated": "would_update",
                        "replaced": "would_replace",
                        "kept": "would_keep",
                    }[action]
                else:
                    status = self._upsert_price(
                        book, row["comm"], row["currency"],
                        row["date"], row["value"], row["type"],
                        row["source"],
                    )
                    wrote = wrote or status != "kept"
                by_ref[row["ref"]] = {
                    "ref": row["ref"], "status": status,
                    "commodity": row["comm"].mnemonic,
                    "date": row["date"].isoformat(),
                    "value": row["value"],
                    "currency": row["currency"].mnemonic,
                }
                if action == "kept":
                    by_ref[row["ref"]]["reason"] = self._kept_price_reason(
                        old, row["source"],
                    )
                    continue
                # Same-date tie loss surfaces in the reason column
                # — dry run projects it identically (the outranker
                # ignores the not-yet-written row either way).
                outranked_by = self._same_date_outranker(
                    book, row["comm"], row["currency"],
                    row["date"], row["source"],
                )
                if outranked_by:
                    by_ref[row["ref"]]["reason"] = (
                        f"outranked by {outranked_by!r} for this "
                        f"date (desktop's order: later stamp, then smaller GUID)"
                    )

            shapes: dict = {}
            if wrote:
                shapes = self._upgrade_book_shapes(book)
                book.save()
                self._invalidate_price_caches(book)
            return {**self._prices_envelope(prices, by_ref), **shapes}

    @staticmethod
    def _prices_envelope(prices: list[dict], by_ref: dict) -> dict:
        """Results TSV in submission order, one row per input ref."""
        lines = ["ref\tstatus\tcommodity\tdate\tvalue\tcurrency\treason"]
        for p in prices:
            r = by_ref.get(p["ref"], {})
            lines.append(
                f"{r.get('ref', p['ref'])}\t{r.get('status', '')}\t"
                f"{r.get('commodity', '')}\t{r.get('date', '')}\t"
                f"{r.get('value', '')}\t{r.get('currency', '')}\t"
                f"{r.get('reason', '')}"
            )
        return {"results": "\n".join(lines)}

    def create_price(
        self,
        commodity: str,
        namespace: str,
        value: str,
        currency: str | None = None,
        price_date: date | None = None,
        price_type: str = "last",
        source: str = "user:price",
    ) -> dict:
        """Record a price for a commodity (stock price, NAV, exchange rate).

        Args:
            commodity: Symbol (e.g., "VTSAX", "USD").
            namespace: Namespace (e.g., "FUND", "CURRENCY").
            value: Price per unit as decimal string.
            currency: Quote currency; defaults to the book default
                (on a CNY book, ``commodity="USD", value="7.30"``
                means 1 USD = 7.30 CNY). Pass explicitly for pairs
                that don't involve the book default.
            price_date: Defaults to today.
            price_type: "last" (default, as desktop's price editor), "nav", "bid", "ask",
                "unknown".
            source: Source identifier. Default "user:price".

        Returns:
            Dict echoing the RESOLVED currency mnemonic, plus
            status "updated" (same commodity/currency/date/source
            existed) or "created". A ``note`` key appears when a
            same-date row from a higher-ranked source outranks the
            written row as the effective rate.

        Raises:
            ValueError: If commodity not found or invalid currency.
        """
        if price_date is None:
            price_date = date.today()

        with self.open(readonly=False) as book:
            comm = self._find_commodity(book, commodity, namespace)
            if not comm:
                raise ValueError(
                    f"Commodity not found: {namespace}:{commodity}"
                )

            # Default to the BOOK's currency, never a hardcoded
            # "USD" — that stores nonsense like commodity=USD
            # currency=USD on non-USD books, invisible to
            # _find_exchange_rate.
            if currency is None:
                resolved_currency = self._require_default_currency(book)
            else:
                resolved_currency = self._get_or_create_currency(
                    book, currency,
                )

            self._check_price(comm, resolved_currency, value)

            # A price write converts the book's pre-1.5 shapes first
            # (generator sources desktop shows as Invalid, midnight
            # dates) so this row and the existing ones share one
            # convention.
            shapes = self._upgrade_book_shapes(book)
            plan, old = self._price_plan(
                book, comm, resolved_currency, price_date,
                self._gnc_price_source(source),
            )
            status = self._upsert_price(
                book, comm, resolved_currency, price_date,
                value, price_type, source,
            )

            book.save()
            self._invalidate_price_caches(book)

            result = {
                "commodity": commodity,
                "namespace": namespace,
                # Resolved mnemonic, not the (possibly None) input.
                "currency": resolved_currency.mnemonic,
                "date": price_date.isoformat(),
                "value": value,
                "type": price_type,
                # created / updated (same source) / replaced (another
                # source's or the opposite direction's row gave way)
                # / kept (nothing written).
                "status": status,
                **shapes,
            }
            if status == "kept":
                result["note"] = self._kept_price_reason(
                    old, self._gnc_price_source(source),
                )
                result["existing"] = {
                    "source": old[4],
                    "value": str(
                        (Decimal(old[5].numerator) / Decimal(old[5].denominator))
                    ),
                    "quoted": (
                        "same direction" if old[1] == comm.guid
                        else "opposite direction"
                    ),
                }
                return result
            if status == "replaced":
                result["replaced"] = {
                    "source": old[4],
                    "quoted": (
                        "same direction" if old[1] == comm.guid
                        else "opposite direction"
                    ),
                }
            outranked_by = self._same_date_outranker(
                book, comm, resolved_currency, price_date, source,
            )
            if outranked_by:
                result["note"] = (
                    f"recorded, but a {outranked_by!r} price for "
                    f"this date outranks it as the effective rate "
                    f"(desktop's order: later stamp, then smaller GUID)"
                )

            return result

    def delete_price(
        self,
        commodity: str,
        namespace: str,
        price_date: date,
        source: str | None = None,
    ) -> dict:
        """Delete a single price entry, identified by
        ``(commodity, namespace, date)``.

        ``source`` disambiguates when the same date holds multiple
        prices (user-entered vs feed-fetched); omitted with
        multiple matches, the error lists them for a retry.

        Returns:
            The deleted price's identity plus ``value`` — echoed so
            the caller can confirm they removed the right one —
            and ``status: "deleted"``.

        Raises:
            ValueError: commodity not found, no matching price, or
                multiple matches without ``source``.
        """
        with self.open(readonly=False) as book:
            comm = self._find_commodity(book, commodity, namespace)
            if not comm:
                raise ValueError(
                    f"Commodity not found: {namespace}:{commodity}"
                )

            # Indexed query keyed on commodity (and source when set).
            # The date filter stays in Python because piecash's date
            # column is a DateTime under the hood; comparing on the
            # date portion is awkward in raw SQLAlchemy.
            filters = {"commodity_guid": comm.guid}
            if source is not None:
                filters["source"] = source
            candidates = book.session.query(Price).filter_by(
                **filters,
            ).all()
            matches = [
                p for p in candidates if _to_date(p.date) == price_date
            ]

            if len(matches) == 0:
                raise ValueError(
                    f"No price found for {namespace}:{commodity} on "
                    f"{price_date.isoformat()}"
                    + (f" (source={source!r})" if source else "")
                )

            if len(matches) > 1:
                summary = ", ".join(
                    f"{p.source} ({p.value})" for p in matches
                )
                raise ValueError(
                    f"Multiple prices found for {namespace}:{commodity} "
                    f"on {price_date.isoformat()}: {summary}. "
                    f"Specify source= to disambiguate."
                )

            target = matches[0]
            result = {
                "commodity": commodity,
                "namespace": namespace,
                "currency": target.currency.mnemonic,
                "date": price_date.isoformat(),
                "value": str(target.value),
                "source": target.source,
                "status": "deleted",
            }
            self._stage_audit_before({
                "commodity": commodity,
                "namespace": namespace,
                "date": price_date.isoformat(),
                "value": str(target.value),
                "source": target.source,
            })

            book.session.delete(target)
            book.save()
            self._invalidate_price_caches(book)

            return result

    def get_prices(
        self,
        commodity: str,
        namespace: str,
        start_date: date | None = None,
        end_date: date | None = None,
        currency: str | None = None,
        limit: int | None = None,
        compact: bool = True,
        offset: int = 0,
    ) -> dict | str:
        """Get price history for a commodity.

        Leads with a ``Showing X-Y of Z prices (date range)`` indicator;
        page with ``offset``. Sorted by date descending — most recent
        first, so a small ``limit`` still surfaces the freshest data.

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX").
            namespace: Namespace of the commodity (e.g., "FUND").
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            currency: Optional currency filter (e.g., "USD").
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.

        Returns:
            Verbose envelope ``{prices, showing, total, offset, count}``;
            compact leads with the indicator.

        Raises:
            ValueError: If commodity not found.
        """
        with self.open(readonly=True) as book:
            comm = self._find_commodity(book, commodity, namespace)
            if not comm:
                raise ValueError(
                    f"Commodity not found: {namespace}:{commodity}"
                )

            # Indexed by commodity_guid; the optional filters stay
            # in Python.
            candidates = book.session.query(Price).filter_by(
                commodity_guid=comm.guid,
            ).all()
            prices = []
            for p in candidates:
                if currency and p.currency.mnemonic != currency:
                    continue
                p_date = _to_date(p.date)
                if start_date and p_date < start_date:
                    continue
                if end_date and p_date > end_date:
                    continue

                prices.append({
                    "date": p_date.isoformat(),
                    # Four places, and more for a rate that needs
                    # them: an IDR/USD rate of 0.0000613 read 0.0001
                    # (adversarial review 2026-09-30, MM-10).
                    "value": _format_number(
                        p.value,
                        decimals=max(
                            4,
                            min(12, 3 - Decimal(str(p.value)).adjusted())
                            if p.value else 4,
                        ),
                        strip_trailing=True,
                    ),
                    "currency": p.currency.mnemonic,
                    "type": p.type,
                    "source": p.source,
                })

            prices.sort(key=lambda x: x["date"], reverse=True)
            total = len(prices)
            page, indicator = _paginate(
                prices,
                offset=offset,
                limit=limit,
                entity_name="prices",
                date_key=lambda p: p["date"],
            )

            if not compact:
                return {
                    "showing": indicator,
                    "total": total,
                    "offset": offset,
                    "count": len(page),
                    "prices": page,
                }

            # Compact: "2026-04-30  273.43  USD  last  yfinance",
            # columns aligned, under the indicator.
            if not page:
                return indicator
            value_w = max(len(p["value"]) for p in page)
            type_w = max(len(p.get("type") or "") for p in page)
            ccy_w = max(len(p["currency"]) for p in page)
            lines = [indicator]
            for p in page:
                lines.append(
                    f"{p['date']}  "
                    f"{p['value']:>{value_w}}  "
                    f"{p['currency']:<{ccy_w}}  "
                    f"{(p.get('type') or ''):<{type_w}}  "
                    f"{p.get('source') or ''}"
                )
            return "\n".join(lines)

    def get_latest_price(
        self,
        commodity: str,
        namespace: str,
        currency: str | None = None,
    ) -> dict | None:
        """Get the most recent price for a commodity.

        Args:
            commodity: Symbol (e.g., "VTSAX").
            namespace: Namespace (e.g., "FUND").
            currency: Defaults to the book default — a hardcoded
                "USD" would silently return None on non-USD books.
                Pass explicitly for a non-default-currency quote.

        Returns:
            Price dict with date, value, currency, type, source —
            or None if no price exists.

        Raises:
            ValueError: If commodity not found.
        """
        with self.open(readonly=True) as book:
            comm = self._find_commodity(book, commodity, namespace)
            if not comm:
                raise ValueError(
                    f"Commodity not found: {namespace}:{commodity}"
                )

            if currency is None:
                currency = self._require_default_currency(book).mnemonic

            # The chokepoint's list is newest-first with the same-date
            # tie-break every valuation path uses, so the first row in
            # the requested quote currency IS the rate the reports
            # price by, a transaction's implied rate included, as
            # desktop counts it.
            latest = next(
                (
                    p for p in self._find_prices(
                        book, commodity_guid=comm.guid,
                    )
                    if p.currency.mnemonic == currency
                ),
                None,
            )
            # A pair is priced in whichever direction it was last
            # quoted. USD/EUR 0.80 entered yesterday IS the latest
            # EUR/USD rate (1.25), and it is the one valuation uses;
            # this lookup read only its own direction and answered
            # with a 2024 quote (adversarial review 2026-09-30, MM-9).
            quote = self._find_commodity(book, currency, "CURRENCY")
            reverse = None
            if quote is not None and quote.guid != comm.guid:
                reverse = next(
                    (
                        p for p in self._find_prices(
                            book, commodity_guid=quote.guid,
                        )
                        if p.currency_guid == comm.guid and p.value
                    ),
                    None,
                )
            if reverse is not None and (
                latest is None
                or _to_date(reverse.date) > _to_date(latest.date)
            ):
                inverse = Decimal(1) / Decimal(str(reverse.value))
                shown = f"{inverse:.8f}".rstrip("0").rstrip(".")
                return {
                    "date": _to_date(reverse.date).isoformat(),
                    "value": shown,
                    "currency": currency,
                    "type": reverse.type,
                    "source": reverse.source,
                    "inverted_from": (
                        f"{quote.mnemonic}/{comm.mnemonic} "
                        f"{reverse.value}"
                    ),
                }
            if latest is None:
                return None
            latest_date = _to_date(latest.date)

            return {
                "date": latest_date.isoformat(),
                "value": str(latest.value),
                "currency": latest.currency.mnemonic,
                "type": latest.type,
                "source": latest.source,
            }

    # ── Lots (cost basis tracking) ────────────────────────────────

    def _find_lot(self, book: piecash.Book, guid: str):
        """Find a lot by GUID (supports partial GUIDs, 8+ chars)."""

        try:
            full_guid = self._resolve_guid("lots", guid)
        except ValueError as e:
            if "No lot" in str(e):
                return None
            raise
        return book.session.query(Lot).filter_by(guid=full_guid).first()

    def _lot_decimals(self, lot, book, default_ccy) -> dict:
        """Raw-Decimal source of truth for a lot's current state.

        Keeps full precision; ``_lot_summary`` formats the egress.
        Math (cost basis, gain) consumes this directly so we never
        round-trip a number through a formatted string for further
        computation. The classic precision-loss path: $100 / 3 shares
        formatted to 4 decimals as 33.3333, multiplied back by 3
        shares becomes $99.99 — but the actual cost was $100.

        ``purchase_value`` (and the ``cost_per_share`` /
        ``remaining_cost_basis`` derived from it) is in the book
        DEFAULT currency. ``split.value`` is in the purchase
        transaction's currency; a foreign-denominated buy is converted
        at its posting-date rate (matching ``calculate_lot_gain``), so
        a CNY-book holding bought in USD doesn't surface a bare USD
        number that reads as CNY. Missing rate degrades to the raw
        value.

        Returns:
            Dict of Decimals: purchase_quantity, purchase_value,
            sale_quantity, remaining, cost_per_share,
            remaining_cost_basis.
        """
        purchase_quantity = Decimal(0)
        purchase_value = Decimal(0)
        sale_quantity = Decimal(0)

        for split in lot.splits:
            # Skip voided splits by state — well-formed voids
            # contribute 0 only by coincidence, and partial
            # corruption (state=v, quantity != 0) must not count.
            if _is_voided(split):
                continue
            if split.quantity > 0:
                purchase_quantity += Decimal(str(split.quantity))
                value = Decimal(str(split.value))
                txn_ccy = split.transaction.currency
                if txn_ccy != default_ccy:
                    rate = self._cross_rate(
                        book, txn_ccy, default_ccy,
                        as_of=split.transaction.post_date,
                    )
                    if rate is not None:
                        value = value * rate
                purchase_value += value
            else:
                sale_quantity += abs(Decimal(str(split.quantity)))

        remaining = purchase_quantity - sale_quantity

        if purchase_quantity > 0:
            cost_per_share = purchase_value / purchase_quantity
            # Prorate cost basis on shares remaining, not
            # ``cost_per_share * remaining`` — for a lot bought at
            # $100/3 shares, the latter gives $99.999... while the
            # prorated form gives exactly $100 when ``remaining ==
            # purchase_quantity``.
            remaining_cost_basis = (
                purchase_value * remaining / purchase_quantity
            )
        else:
            cost_per_share = Decimal(0)
            remaining_cost_basis = Decimal(0)

        return {
            "purchase_quantity": purchase_quantity,
            "purchase_value": purchase_value,
            "sale_quantity": sale_quantity,
            "remaining": remaining,
            "cost_per_share": cost_per_share,
            "remaining_cost_basis": remaining_cost_basis,
        }

    def _lot_summary(self, lot, book, default_ccy) -> dict:
        """Compute current state of a lot from its splits.

        Returns:
            Dict with quantity, cost_basis (alias for
            remaining_cost_basis — kept for backward compat),
            remaining_cost_basis, original_cost_basis,
            cost_per_share, and is_closed.

        Both cost-basis fields ship because a lone ``cost_basis``
        (the post-sale residual) is ambiguous after a partial
        sale: ``cost_basis: $50`` on a lot bought for $100 reads
        as either the purchase cost or what's left of it. The
        ``cost_basis`` key keeps existing callers working.
        """
        raw = self._lot_decimals(lot, book, default_ccy)
        # Cost basis is a value in the book currency; the quantity is
        # in the lot account's commodity, at its places (review C20,
        # MM-10: 0.00004321 BTC read 0.0000); cost per share is a
        # price (``_format_price``).
        remaining_cb = _format_converted(
            raw["remaining_cost_basis"], default_ccy,
        )
        original_cb = _format_converted(
            raw["purchase_value"], default_ccy,
        )
        return {
            "quantity": _format_account_amount(raw["remaining"], lot.account),
            # Legacy: ``cost_basis`` returns the remaining (post-sale)
            # value, same as before the rename. New callers should
            # use ``remaining_cost_basis`` for clarity.
            "cost_basis": remaining_cb,
            "remaining_cost_basis": remaining_cb,
            "original_cost_basis": original_cb,
            "cost_per_share": _format_price(raw["cost_per_share"], default_ccy),
            "is_closed": _lot_is_closed(lot),
        }

    def create_lot(
        self,
        account: str,
        title: str,
        notes: str = "",
    ) -> dict:
        """Create a new lot for cost basis tracking.

        Lots group investment purchases for tracking cost basis and
        calculating capital gains when selling.

        Args:
            account: Full path of investment account (e.g., "Assets:Investments:VTSAX").
            title: Lot identifier (e.g., "VTSAX 2026-01-15 purchase").
            notes: Optional notes.

        Returns:
            Dict with guid, title, account, and status.

        Raises:
            ValueError: If account not found.
        """
        # Every free-text argument through the one text gate (scoped
        # review 2026-10-05, I-4): no control characters, GnuCash's
        # column width.
        for _field in ("name", "description", "notes", "title", "reference", "fullname", "mnemonic", "memo", "action"):
            _check_text(
                locals().get(_field),
                _SLOT_TEXT_WIDTH if _field == "notes" else _TEXT_WIDTH, _field,
            )
            if _field in ("name", "title", "reference", "fullname", "mnemonic", "action"):
                _check_one_line(locals().get(_field), _field)

        with self.open(readonly=False) as book:
            acct = self._resolve_account(book, account)
            if not acct:
                raise self._account_not_found_error(book, account)

            # gnc_lot_new's shape: the closed flag UNKNOWN (-1,
            # computed on read) and no notes slot until someone
            # writes a note. The server wrote 0 and an empty slot
            # (adversarial review 2026-09-30, SS-15).
            lot = Lot(
                title=title,
                account=acct,
                notes=notes or None,
                is_closed=_LOT_OPEN,
            )
            # No session.add — the Lot auto-registers via the
            # Account.lots back-populate.
            book.flush()
            lot.is_closed = _LOT_CLOSED_UNKNOWN
            book.save()

            all_lot_guids = [
                row[0] for row in book.session.query(Lot.guid).all()
            ]
            short_guid = _unique_prefix(lot.guid, all_lot_guids)
            return {
                "guid": short_guid,
                "title": title,
                "account": acct.fullname,
                "notes": notes,
                "status": "created",
            }

    def list_lots(
        self,
        account: str,
        include_closed: bool = False,
        compact: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> dict | str:
        """List all lots for an investment account.

        Leads with a ``Showing X-Y of Z lots`` indicator; page with
        ``offset``.

        Args:
            account: Full path of investment account.
            include_closed: If True, include fully-sold lots. Default False.
            compact: If True (default), return the indicator + a compact
                     newline-separated string with one line per lot.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.

        Returns:
            If compact: indicator + newline-separated lot lines.
            If not compact: envelope ``{showing, total, offset, count,
            lots}``.

        Raises:
            ValueError: If account not found.
        """
        with self.open(readonly=True) as book:
            acct = self._resolve_account(book, account)
            if not acct:
                raise self._account_not_found_error(book, account)

            default_ccy = self._require_default_currency(book)
            results = []
            # Acquisition order — earliest split date, then title, then
            # GUID — rather than the order the backend returned rows.
            def _lot_key(lot):
                dates = [
                    s.transaction.post_date for s in lot.splits
                    if s.transaction.post_date is not None
                ]
                return (min(dates) if dates else date.max, lot.title or "", lot.guid)

            for lot in sorted(acct.lots, key=_lot_key):
                if not include_closed and _lot_is_closed(lot):
                    continue
                summary = self._lot_summary(lot, book, default_ccy)
                # The open-positions view also skips zero-position
                # lots (voided buys, never-assigned, round-tripped
                # to zero) — noise rows in a holdings listing.
                # include_closed=True restores the full audit trail.
                if (
                    not include_closed
                    and Decimal(summary["quantity"]) == 0
                ):
                    continue
                results.append({
                    "guid": lot.guid,
                    "title": lot.title,
                    "notes": lot.notes or "",
                    **summary,
                })

            page, indicator = _paginate(
                results, offset=offset, limit=limit, entity_name="lots",
            )
            if compact:
                # Prefix map spans every lot in the book —
                # _resolve_guid searches table-wide.
                all_lot_guids = [
                    row[0]
                    for row in book.session.query(Lot.guid).all()
                ]
                prefixes = _guid_prefix_map(all_lot_guids)
                lines = [indicator]
                lines += [
                    _lot_to_compact_line(d, prefixes=prefixes) for d in page
                ]
                return "\n".join(lines)
            else:
                return {
                    "showing": indicator,
                    "total": len(results),
                    "offset": offset,
                    "count": len(page),
                    "lots": page,
                }

    def get_lot(self, guid: str) -> dict:
        """Get detailed information about a lot.

        Args:
            guid: Lot GUID.

        Returns:
            Dict with lot details including all splits and summary.

        Raises:
            ValueError: If lot not found.
        """
        with self.open(readonly=True) as book:
            lot = self._find_lot(book, guid)
            if not lot:
                raise ValueError(f"Lot not found: {guid}")

            # Split prefixes span the whole book — they feed back
            # into table-wide _resolve_guid lookups. Cached, one
            # indexed query (whole-tree review, class 4).
            prefixes = self._split_prefix_map(book)

            splits = []
            for split in lot.splits:
                row = {
                    "guid": prefixes.get(split.guid, split.guid),
                    "date": (
                        split.transaction.post_date.isoformat()
                        if split.transaction.post_date else None
                    ),
                    "description": split.transaction.description,
                    "quantity": str(split.quantity),
                    "value": str(split.value),
                }
                # The summary already excludes voided zombies; an
                # unmarked 0-row here would read as a real event the
                # summary then contradicts.
                if _is_voided(split):
                    row["voided"] = True
                splits.append(row)

            default_ccy = self._require_default_currency(book)
            summary = self._lot_summary(lot, book, default_ccy)
            # ``is_closed`` already lives at the top level
            # of this response. Drop it from the nested ``summary``
            # so callers see the field once, not twice.
            summary_compact = {k: v for k, v in summary.items() if k != "is_closed"}

            return {
                "guid": lot.guid,
                "title": lot.title,
                "account": lot.account.fullname,
                "notes": lot.notes or "",
                "is_closed": _lot_is_closed(lot),
                "splits": splits,
                "summary": summary_compact,
            }

    def assign_split_to_lot(
        self,
        split_guid: str,
        lot_guid: str,
    ) -> dict:
        """Assign a transaction split to a lot.

        Use after creating a buy/sell transaction to link the investment
        account split to its lot for cost basis tracking.

        Args:
            split_guid: GUID of the split (from transaction's investment account).
            lot_guid: GUID of the lot.

        Returns:
            Dict with status and updated lot summary.

        Raises:
            ValueError: If split or lot not found, split is in wrong account,
                       split already assigned to a lot, or lot is closed.
        """
        with self.open(readonly=False) as book:
            split = self._find_split(book, split_guid)
            if not split:
                raise ValueError(f"Split not found: {split_guid}")

            # Reject voided splits — a zero-contribution row would
            # trip the auto-close check and produce a degenerate lot.
            if _is_voided(split):
                raise ValueError(
                    f"Cannot assign voided split {split_guid} to "
                    f"a lot. Unvoid the transaction first, or "
                    f"assign a different (active) split."
                )

            lot = self._find_lot(book, lot_guid)
            if not lot:
                raise ValueError(f"Lot not found: {lot_guid}")

            if _lot_is_closed(lot):
                raise ValueError("Cannot assign split to a closed lot")

            if split.account != lot.account:
                raise ValueError(
                    f"Split account ({split.account.fullname}) does not match "
                    f"lot account ({lot.account.fullname})"
                )

            if split.lot is not None:
                raise ValueError(
                    f"Split is already assigned to lot: {split.lot.guid}"
                )

            # Everything that can fail runs before the ONE save. The
            # assignment used to be saved first; a failure after it
            # (a root account with no currency) left the split in the
            # lot, the tool reporting an error, the retry answering
            # "already assigned", and a lot the assignment had zeroed
            # still flagged open (adversarial review 2026-09-30, C32).
            default_ccy = self._require_default_currency(book)

            _lot_cache_flag(lot)
            split.lot = lot
            book.flush()
            summary = self._lot_summary(lot, book, default_ccy)

            # Auto-close at zero quantity — the value GnuCash itself
            # caches for a zero-balance lot (gnc_lot_get_balance).
            auto_closed = False
            if Decimal(summary["quantity"]) == 0 and len(lot.splits) > 0:
                lot.is_closed = _LOT_CLOSED
                auto_closed = True
            book.save()

            # Input GUIDs are echoes — dropped. ``is_closed`` is
            # surfaced because the auto-close is what the caller
            # wants to know about.
            return {
                "status": "assigned",
                **summary,
                "is_closed": auto_closed or _lot_is_closed(lot),
            }

    def calculate_lot_gain(
        self,
        lot_guid: str,
        shares: str | None = None,
        sale_price: str | None = None,
    ) -> dict:
        """Calculate potential or actual capital gain for a lot.

        If shares and sale_price provided, calculates hypothetical gain.
        Otherwise uses lot's current state and latest price.

        Args:
            lot_guid: Lot GUID.
            shares: Optional number of shares to calculate for.
                    Defaults to all remaining shares.
            sale_price: Optional sale price per share.
                        Defaults to latest price for the commodity.

        Returns:
            Dict with shares, cost_basis, sale_proceeds, capital_gain, gain_percent.

        Raises:
            ValueError: If lot not found, no shares remaining, or no price available.
        """
        with self.open(readonly=True) as book:
            lot = self._find_lot(book, lot_guid)
            if not lot:
                raise ValueError(f"Lot not found: {lot_guid}")

            default_ccy = self._require_default_currency(book)
            raw = self._lot_decimals(lot, book, default_ccy)
            remaining = raw["remaining"]

            if remaining <= 0:
                # Distinguish "sold to zero" (normal) from "voided
                # splits zeroed the quantity" (likely a mistake the
                # caller should know about).
                voided_split_count = sum(
                    1 for s in lot.splits if s.reconcile_state == "v"
                )
                if voided_split_count:
                    raise ValueError(
                        f"Lot has no remaining shares — "
                        f"{voided_split_count} split(s) in this lot "
                        f"are voided, zeroing the lot's quantity. "
                        f"Unvoid the underlying transaction(s) or "
                        f"calculate gain on a different lot."
                    )
                raise ValueError("Lot has no remaining shares")

            if shares is not None:
                shares_to_sell = _to_decimal(shares)
                if shares_to_sell > remaining:
                    raise ValueError(
                        f"Cannot sell {shares_to_sell}; lot has {remaining} shares"
                    )
            else:
                shares_to_sell = remaining

            if sale_price is not None:
                price = _to_decimal(sale_price)
            else:
                # Latest market price in the book default —
                # _find_prices applies both required filters
                # (market-only, currency) at one chokepoint.
                commodity = lot.account.commodity
                recent = self._find_prices(
                    book,
                    commodity_guid=commodity.guid,
                    currency_guid=default_ccy.guid,
                )
                if not recent:
                    raise ValueError(
                        f"No price found for {commodity.mnemonic}. "
                        "Provide sale_price explicitly."
                    )
                price = Decimal(str(recent[0].value))

            # Cost basis in the book default (proceeds' currency).
            # _lot_decimals already converts each purchase split at its
            # posting-date rate — same treatment, one chokepoint.
            purchase_value_default = raw["purchase_value"]

            # Prorate on shares-to-sell, never cost_per_share ×
            # shares — divide-then-multiply loses precision ($100/3
            # shares × 3 = $99.99…). Tax-relevant.
            cost_basis = (
                purchase_value_default * shares_to_sell
                / raw["purchase_quantity"]
            )
            # Proceeds are what a sale split would store: rounded
            # half-up to the currency, as a split is. The cost is a
            # converted figure, rounded once. The gain is their
            # difference, so the three lines agree (scoped review
            # 2026-10-05, M-3).
            quantum = _commodity_quantum(default_ccy)
            proceeds = (price * shares_to_sell).quantize(
                quantum, rounding=ROUND_HALF_UP,
            )
            cost_basis = _round_converted(cost_basis, default_ccy)
            gain = proceeds - cost_basis
            gain_pct = (gain / cost_basis * 100) if cost_basis else Decimal(0)

            return {
                "shares": _format_account_amount(shares_to_sell, lot.account),
                "cost_basis": _format_converted(cost_basis, default_ccy),
                "sale_proceeds": _format_converted(proceeds, default_ccy),
                "capital_gain": _format_converted(gain, default_ccy),
                "gain_percent": _format_number(gain_pct, decimals=2),
            }

    def close_lot(self, guid: str) -> dict:
        """Mark a zero-balance lot as closed.

        GnuCash defines a closed lot as one whose quantities sum to
        zero (``gnc_lot_get_balance`` caches exactly that), and it
        recomputes the flag whenever desktop touches the lot — so a
        "closed" flag on a lot that still holds shares would not
        survive the next desktop session. Use this when a lot is fully
        sold but the flag was never cached.

        Args:
            guid: Lot GUID.

        Returns:
            Dict with status.

        Raises:
            ValueError: If lot not found, already closed, or still
                holds a balance.
        """
        with self.open(readonly=False) as book:
            lot = self._find_lot(book, guid)
            if not lot:
                raise ValueError(f"Lot not found: {guid}")

            if _lot_is_closed(lot):
                raise ValueError("Lot is already closed")

            balance = sum((s.quantity for s in lot.splits), Decimal("0"))
            if balance != 0:
                raise ValueError(
                    f"Cannot close lot: it still holds "
                    f"{_format_account_amount(balance, lot.account)} "
                    f"{lot.account.commodity.mnemonic}. GnuCash defines "
                    f"a closed lot as zero balance; assign the sale "
                    f"split(s) first (assign_split_to_lot)."
                )

            lot.is_closed = _LOT_CLOSED
            book.save()


            all_lot_guids = [
                row[0] for row in book.session.query(Lot.guid).all()
            ]
            short_guid = _unique_prefix(lot.guid, all_lot_guids)
            return {
                "guid": short_guid,
                "title": lot.title,
                "status": "closed",
            }
