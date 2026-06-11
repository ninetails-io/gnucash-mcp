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

from datetime import date
from decimal import Decimal

import piecash
from piecash.core.commodity import Price
from piecash.core.transaction import Lot

from gnucash_mcp.book._base import (
    _commodity_to_compact_line,
    _guid_prefix_map,
    _is_market_price,
    _is_voided,
    _lot_to_compact_line,
    _to_date,
    _to_decimal,
    _unique_prefix,
)
from gnucash_mcp._format import _apply_limit, _format_number


class InvestmentsMixin:
    """Commodity/price/lot CRUD and capital-gain calculation."""

    # ── Commodities and prices ────────────────────────────────────

    def list_commodities(self, compact: bool = True) -> dict | str:
        """List all commodities in the book with latest prices.

        Args:
            compact: If True (default), return compact one-line-per-commodity
                     string. If False, return full dict grouped by namespace.

        Returns:
            If compact: newline-separated string of commodity lines.
            If not compact: dict with commodities grouped by namespace.
        """
        with self.open(readonly=True) as book:
            by_namespace: dict[str, list[dict]] = {}

            # Single pass over ``book.prices`` to build a latest-
            # market-quote map keyed by commodity GUID. Pre-fix this
            # method iterated ``book.prices`` without the
            # ``_is_market_price`` filter, so a ``type='transaction'``
            # placeholder newer than the user's last ``nav`` quote
            # would shadow it (SB-11). The fix applies the predicate
            # in this same single pass — same chokepoint, no N+1
            # per-commodity query (Copilot-flagged on PR #95: a
            # per-commodity ``_find_prices`` call would issue one DB
            # query per commodity).
            latest_market: dict[str, tuple[date, "Price"]] = {}
            for p in book.prices:
                if not _is_market_price(p):
                    continue
                key = p.commodity.guid
                p_date = _to_date(p.date)
                existing = latest_market.get(key)
                if existing is None or p_date > existing[0]:
                    latest_market[key] = (p_date, p)

            for commodity in book.commodities:
                ns = commodity.namespace
                # GnuCash's ``template`` pseudo-commodity backs SX
                # template accounts in desktop-created books —
                # scaffolding, not a tracked holding.
                if ns.lower() == "template":
                    continue
                if ns not in by_namespace:
                    by_namespace[ns] = []

                entry: dict = {
                    "mnemonic": commodity.mnemonic,
                    "fullname": commodity.fullname,
                    "fraction": commodity.fraction,
                }

                if commodity.guid in latest_market:
                    _, price = latest_market[commodity.guid]
                    entry["latest_price"] = {
                        "value": str(price.value),
                        "currency": price.currency.mnemonic,
                        "date": _to_date(price.date).isoformat(),
                    }

                by_namespace[ns].append(entry)

            for ns in by_namespace:
                by_namespace[ns].sort(key=lambda c: c["mnemonic"])

            result = {
                "default_currency": self._require_default_currency(book).mnemonic,
                "commodities": by_namespace,
            }

            if compact:
                lines = []
                for ns, entries in sorted(by_namespace.items()):
                    for entry in entries:
                        lines.append(_commodity_to_compact_line(ns, entry))
                return "\n".join(lines)
            else:
                return result

    def create_commodity(
        self,
        mnemonic: str,
        fullname: str,
        namespace: str = "FUND",
        fraction: int = 10000,
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
                      Default 10000.
            cusip: Optional CUSIP/ISIN identifier for the security.

        Returns:
            Dict with mnemonic, namespace, fullname, fraction, and status.

        Raises:
            ValueError: If commodity already exists in that namespace.
        """
        # MP-13: validate inputs up front. Mirrors HP-11's
        # symmetric-gate principle — bad inputs reject before
        # the ORM round-trip, with a useful error rather than
        # IntegrityError / silent corruption downstream.
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
        # fraction is the smallest representable subunit — must be
        # a positive power-of-10 style integer per piecash's
        # Decimal-from-num/denom encoding. Zero or negative
        # breaks every quantity computation that divides by it.
        if not isinstance(fraction, int) or fraction <= 0:
            raise ValueError(
                f"Commodity fraction must be a positive integer. "
                f"Got: {fraction!r}."
            )

        with self.open(readonly=False) as book:
            existing = self._find_commodity(book, mnemonic, namespace)
            if existing:
                raise ValueError(
                    f"Commodity {namespace}:{mnemonic} already exists"
                )

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

    def create_price(
        self,
        commodity: str,
        namespace: str,
        value: str,
        currency: str | None = None,
        price_date: date | None = None,
        price_type: str = "nav",
        source: str = "user:price",
    ) -> dict:
        """Record a price for a commodity (stock price, NAV, exchange rate).

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX", "AAPL").
            namespace: Namespace of the commodity (e.g., "FUND", "NASDAQ").
            value: Price per unit as decimal string (e.g., "250.45").
            currency: Currency the price is denominated in. Defaults to
                the book's default currency. For non-USD-default books
                (e.g. CNY) the default makes ``create_price(commodity=
                "USD", value="7.30")`` mean "1 USD = 7.30 CNY", which
                matches the bookkeeper's mental model. Pass explicitly
                to store cross-currency pairs that don't involve the
                book default.
            price_date: Price date. Defaults to today.
            price_type: Type of price: "nav", "last", "bid", "ask", "unknown".
                        Default "nav".
            source: Source identifier. Default "user:price".

        Returns:
            Dict with commodity, date, value, type, currency (the
            resolved currency mnemonic, not the input), and status.

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

            # Resolve currency: explicit input wins; otherwise default
            # to the book's currency. Pre-fix, an unspecified currency
            # silently became "USD" — which on a non-USD-default book
            # stored prices like ``commodity=USD currency=USD`` (1 USD
            # = X USD, nonsense), invisible to ``_find_exchange_rate``
            # and silently shadowed by older valid prices on lookup.
            if currency is None:
                resolved_currency = self._require_default_currency(book)
            else:
                resolved_currency = self._get_or_create_currency(
                    book, currency,
                )

            # Check for existing price (same commodity/currency/date/source).
            # Indexed query — pre-fix this walked every price in the book
            # for every create_price call. On a book with thousands of
            # historical prices that's a measurable hot path.
            candidates = book.session.query(Price).filter_by(
                commodity_guid=comm.guid,
                currency_guid=resolved_currency.guid,
                source=source,
            ).all()
            existing = None
            for p in candidates:
                if _to_date(p.date) == price_date:
                    existing = p
                    break

            if existing:
                existing.value = _to_decimal(value)
                existing.type = price_type
            else:
                # piecash expects datetime.date, not datetime.datetime
                piecash.Price(
                    commodity=comm,
                    currency=resolved_currency,
                    date=price_date,
                    value=_to_decimal(value),
                    type=price_type,
                    source=source,
                )

            book.save()

            result = {
                "commodity": commodity,
                "namespace": namespace,
                # Echo the resolved mnemonic, not the input — the input
                # might have been None (book default). This way the
                # caller sees what was actually stored.
                "currency": resolved_currency.mnemonic,
                "date": price_date.isoformat(),
                "value": value,
                "type": price_type,
                "status": "updated" if existing else "created",
            }

            return result

    def delete_price(
        self,
        commodity: str,
        namespace: str,
        price_date: date,
        source: str | None = None,
    ) -> dict:
        """Delete a single price entry.

        Identifies the price by ``(commodity, namespace, date)``.
        When the same date has multiple prices for one commodity
        (different sources, e.g. one user-entered and one fetched
        from a feed), ``source`` disambiguates. If ``source`` is
        omitted and multiple matches exist, raises with a list of
        the matches so the caller can retry with the right source.

        Args:
            commodity: Symbol (e.g., "VTSAX", "USD", "EUR").
            namespace: Namespace (e.g., "FUND", "CURRENCY").
            price_date: Date of the price to delete.
            source: Optional source tag (e.g., "user:price",
                "user:yfinance"). Required to disambiguate when
                multiple prices exist on the same commodity+date.

        Returns:
            Dict with the deleted price's commodity, date, value,
            and ``status: "deleted"``. Echoing the value lets the
            caller confirm they removed the right one.

        Raises:
            ValueError: If commodity not found, no matching price,
                or multiple prices match without a ``source``
                disambiguator.
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
                # source omitted but multiple sources match — the
                # caller's call would have been destructive without
                # disambiguation. List what's there so they can retry.
                summary = ", ".join(
                    f"{p.source} ({p.value})" for p in matches
                )
                raise ValueError(
                    f"Multiple prices found for {namespace}:{commodity} "
                    f"on {price_date.isoformat()}: {summary}. "
                    f"Specify source= to disambiguate."
                )

            target = matches[0]
            # Capture before-state for audit log: source + value
            # tell the human reader exactly what was deleted.
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
    ) -> dict | str:
        """Get price history for a commodity.

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX").
            namespace: Namespace of the commodity (e.g., "FUND").
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            currency: Optional currency filter (e.g., "USD").
            limit: Maximum prices to return. Defaults to 50, capped at
                   250 server-side. Pre-fix this method dumped every
                   matching price regardless of caller intent.

        Returns:
            Dict with ``prices`` (list, possibly truncated), ``count``
            (truncated length), ``total`` (untruncated), and ``notice``
            (truncation message or None). Sorted by date descending —
            most recent first, so a small ``limit`` still surfaces the
            freshest data.

        Raises:
            ValueError: If commodity not found.
        """
        with self.open(readonly=True) as book:
            comm = self._find_commodity(book, commodity, namespace)
            if not comm:
                raise ValueError(
                    f"Commodity not found: {namespace}:{commodity}"
                )

            # Indexed query: filter by commodity_guid up front so we
            # don't iterate every price in the book. Currency filter
            # stays in Python because it's optional.
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
                    "value": _format_number(p.value, decimals=4, strip_trailing=True),
                    "currency": p.currency.mnemonic,
                    "type": p.type,
                    "source": p.source,
                })

            prices.sort(key=lambda x: x["date"], reverse=True)
            total = len(prices)
            prices, notice = _apply_limit(
                prices,
                limit=limit,
                entity_name="prices",
                suggest_narrow=True,
            )

            full = {
                "prices": prices,
                "count": len(prices),
                "total": total,
                "notice": notice,
            }
            if not compact:
                return full

            # Compact format: one line per price, columns aligned.
            # Format::
            #
            #     2026-04-30  273.43  USD  last      yfinance
            #     2026-03-31  253.79  USD  last      yfinance
            if not prices:
                return notice or "No prices found."
            value_w = max(len(p["value"]) for p in prices)
            type_w = max(len(p.get("type") or "") for p in prices)
            ccy_w = max(len(p["currency"]) for p in prices)
            lines = []
            for p in prices:
                lines.append(
                    f"{p['date']}  "
                    f"{p['value']:>{value_w}}  "
                    f"{p['currency']:<{ccy_w}}  "
                    f"{(p.get('type') or ''):<{type_w}}  "
                    f"{p.get('source') or ''}"
                )
            if notice:
                lines.append(notice)
            return "\n".join(lines)

    def get_latest_price(
        self,
        commodity: str,
        namespace: str,
        currency: str | None = None,
    ) -> dict | None:
        """Get the most recent price for a commodity.

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX").
            namespace: Namespace of the commodity (e.g., "FUND").
            currency: Currency for the price. Defaults to the book's
                default currency. Pre-fix the default was hardcoded
                ``"USD"``, which silently returned None for every
                price on a non-USD-default book (CNY, EUR, etc.) —
                same USD-default-everywhere assumption the v1.2.1
                multi-currency hardening pass fixed for
                ``create_price`` but missed here. Pass explicitly
                to get a non-default-currency price (e.g., the USD
                price of a stock on a CNY-default book).

        Returns:
            Price dict with date, value, type, and source, or None if no price exists.

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

            # Indexed query — only iterate prices for this commodity.
            candidates = book.session.query(Price).filter_by(
                commodity_guid=comm.guid,
            ).all()
            latest = None
            latest_date = None
            for p in candidates:
                if p.currency.mnemonic != currency:
                    continue
                # Skip piecash's auto-created ``type='transaction'``
                # placeholder rows (effective rate of one cross-
                # currency transaction, source=``user:split-register``).
                # Every other valuation path in the codebase
                # (``get_book_summary``, ``_rates_as_of``,
                # ``_find_exchange_rate``, ``_latest_market_rates``,
                # stale-price warnings) excludes them; this single-
                # answer "what's the latest price" tool was the one
                # exception, returning a transaction artifact when
                # the user expected their nav quote.
                if not _is_market_price(p):
                    continue
                p_date = _to_date(p.date)
                if latest_date is None or p_date > latest_date:
                    latest_date = p_date
                    latest = p

            if latest is None:
                return None

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

    def _lot_decimals(self, lot) -> dict:
        """Raw-Decimal source of truth for a lot's current state.

        Keeps full precision; ``_lot_summary`` formats the egress.
        Math (cost basis, gain) consumes this directly so we never
        round-trip a number through a formatted string for further
        computation. The classic precision-loss path: $100 / 3 shares
        formatted to 4 decimals as 33.3333, multiplied back by 3
        shares becomes $99.99 — but the actual cost was $100.

        Returns:
            Dict of Decimals: purchase_quantity, purchase_value,
            sale_quantity, remaining, cost_per_share,
            remaining_cost_basis.
        """
        purchase_quantity = Decimal(0)
        purchase_value = Decimal(0)
        sale_quantity = Decimal(0)

        for split in lot.splits:
            # Voided splits are zombies — preserved for audit trail
            # with quantity/value zeroed. Skip explicitly so the
            # intent is documented and partial-corruption cases
            # (state=v but quantity != 0) are also excluded. Pre-fix
            # this worked by coincidence because zeroed quantity
            # contributes 0 to either branch below.
            if _is_voided(split):
                continue
            if split.quantity > 0:
                purchase_quantity += Decimal(str(split.quantity))
                purchase_value += Decimal(str(split.value))
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

    def _lot_summary(self, lot) -> dict:
        """Compute current state of a lot from its splits.

        Returns:
            Dict with quantity, cost_basis (alias for
            remaining_cost_basis — kept for backward compat),
            remaining_cost_basis, original_cost_basis,
            cost_per_share, and is_closed.

        Pre-fix the field name was just ``cost_basis`` (the
        post-sale residual). After a partial sale the reading
        ``cost_basis: $50`` on a lot bought for $100 was
        ambiguous — was the lot bought for $50, or is $50 what's
        left of the original cost? Now both fields ship; the
        legacy ``cost_basis`` key keeps existing callers working.
        """
        raw = self._lot_decimals(lot)
        remaining_cb = _format_number(
            raw["remaining_cost_basis"], decimals=2,
        )
        original_cb = _format_number(
            raw["purchase_value"], decimals=2,
        )
        return {
            # Quantity is shares (or other commodity units): 4 decimals
            # is a good default for funds and stocks. Crypto callers
            # who need finer granularity get the same _format_number
            # logic at decimals=6 in their own paths.
            "quantity": _format_number(raw["remaining"], decimals=4),
            # Legacy: ``cost_basis`` returns the remaining (post-sale)
            # value, same as before the rename. New callers should
            # use ``remaining_cost_basis`` for clarity.
            "cost_basis": remaining_cb,
            "remaining_cost_basis": remaining_cb,
            "original_cost_basis": original_cb,
            "cost_per_share": _format_number(raw["cost_per_share"], decimals=4),
            "is_closed": bool(lot.is_closed),
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

        with self.open(readonly=False) as book:
            acct = self._resolve_account(book, account)
            if not acct:
                raise ValueError(f"Account not found: {account}")

            lot = Lot(
                title=title,
                account=acct,
                notes=notes,
                is_closed=0,
            )
            # MP-11: ``book.session.add(lot)`` is redundant —
            # piecash's Lot.__init__ assigns ``self.account``,
            # which back-populates through Account.lots and
            # auto-registers the Lot with the session. Verified
            # against piecash/core/transaction.py:514.
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
    ) -> list[dict] | str:
        """List all lots for an investment account.

        Args:
            account: Full path of investment account.
            include_closed: If True, include fully-sold lots. Default False.
            compact: If True (default), return a compact newline-separated
                     string with one line per lot.

        Returns:
            If compact: newline-separated string of lot lines.
            If not compact: list of lot dicts.

        Raises:
            ValueError: If account not found.
        """
        with self.open(readonly=True) as book:
            acct = self._resolve_account(book, account)
            if not acct:
                raise ValueError(f"Account not found: {account}")

            results = []
            for lot in acct.lots:
                if not include_closed and lot.is_closed:
                    continue
                summary = self._lot_summary(lot)
                # Skip lots with no remaining position in the default
                # (open-positions) view. This catches three cases that
                # all read the same to a portfolio manager: voided
                # buys (GnuCash zeros the splits but preserves them
                # in the lot), never-assigned lots, and lots that
                # round-tripped to zero without being closed. All
                # three appear as "0 shares, 0 cost basis" rows that
                # add noise to a holdings listing. Available via
                # ``include_closed=True`` for callers who need the
                # full audit trail.
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

            if compact:
                # _resolve_guid("lots", ...) searches the whole lots table,
                # so the prefix map has to span every lot in the book — not
                # just lots on this account.
                all_lot_guids = [
                    row[0]
                    for row in book.session.query(Lot.guid).all()
                ]
                prefixes = _guid_prefix_map(all_lot_guids)
                lines = [
                    _lot_to_compact_line(d, prefixes=prefixes) for d in results
                ]
                return "\n".join(lines)
            else:
                return results

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

            # Build a collision-safe prefix map across every split in
            # the book — the LLM can paste any short prefix back into
            # ``set_reconcile_state`` / ``assign_split_to_lot`` etc.
            # and ``_resolve_guid("splits", ...)`` will resolve it.
            all_split_guids = (
                s.guid for txn in book.transactions for s in txn.splits
            )
            prefixes = _guid_prefix_map(all_split_guids)

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
                # unmarked 0-row here read as a real event the
                # summary then contradicted (A8).
                if _is_voided(split):
                    row["voided"] = True
                splits.append(row)

            summary = self._lot_summary(lot)
            # Phase 6A: ``is_closed`` already lives at the top level
            # of this response. Drop it from the nested ``summary``
            # so callers see the field once, not twice.
            summary_compact = {k: v for k, v in summary.items() if k != "is_closed"}

            return {
                "guid": lot.guid,
                "title": lot.title,
                "account": lot.account.fullname,
                "notes": lot.notes or "",
                "is_closed": bool(lot.is_closed),
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

            # Reject voided splits — they're zombies (quantity=0,
            # value=0) and would attach a zero-contribution row to
            # the lot, then immediately trip the auto-close check
            # ``remaining == 0`` if no other splits exist. Better
            # to refuse than to silently produce a degenerate lot.
            if _is_voided(split):
                raise ValueError(
                    f"Cannot assign voided split {split_guid} to "
                    f"a lot. Unvoid the transaction first, or "
                    f"assign a different (active) split."
                )

            lot = self._find_lot(book, lot_guid)
            if not lot:
                raise ValueError(f"Lot not found: {lot_guid}")

            if lot.is_closed:
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

            split.lot = lot
            book.save()

            summary = self._lot_summary(lot)

            # Auto-close if quantity reaches zero; GnuCash uses -1 for boolean true
            auto_closed = False
            if Decimal(summary["quantity"]) == 0 and len(lot.splits) > 0:
                lot.is_closed = -1
                book.save()
                auto_closed = True

            # split_guid and lot_guid are echoed inputs — dropped.
            # ``is_closed`` is included on this response (Phase 6A
            # removed it from ``_lot_summary``, but the auto-close
            # behavior of this tool is exactly what the caller wants
            # to know about — keep it surfaced here).
            return {
                "status": "assigned",
                **summary,
                "is_closed": auto_closed or bool(lot.is_closed),
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

            raw = self._lot_decimals(lot)
            remaining = raw["remaining"]

            if remaining <= 0:
                # Distinguish "lot ran to zero through sales" (normal,
                # closed lot) from "lot has voided splits zeroing its
                # purchase quantity" (likely an accounting mistake the
                # caller should know about). GnuCash marks voided
                # splits with reconcile_state='v'.
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

            default_ccy = self._require_default_currency(book)

            if sale_price is not None:
                price = _to_decimal(sale_price)
            else:
                # Look up latest market price for this commodity in
                # the book's default currency. Pre-fix this walk had
                # two bugs: no ``_is_market_price`` filter (a
                # ``type='transaction'`` placeholder could shadow
                # real quotes — SB-11 placeholder portion) and no
                # currency filter (a foreign-currency quote could
                # mis-denominate proceeds against a default-currency
                # cost basis — SB-11 currency portion). Routing
                # through ``_find_prices`` with both filters fixes
                # both at one chokepoint.
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

            # Cost basis must be in the same currency as the
            # proceeds (A1). ``purchase_value`` sums raw
            # ``split.value`` — TRANSACTION-currency units. A
            # foreign-denominated buy (EUR-currency purchase
            # transaction in a USD book) must convert at its
            # historical purchase date or the tax-relevant gain is
            # wrong by the full FX factor. Same-currency buys (the
            # common case) pass through unchanged; a missing
            # historical rate degrades to the raw value (house
            # fallback convention).
            purchase_value_default = Decimal("0")
            for split in lot.splits:
                if _is_voided(split) or split.quantity <= 0:
                    continue
                value = Decimal(str(split.value))
                txn_ccy = split.transaction.currency
                if txn_ccy != default_ccy:
                    rate = self._cross_rate(
                        book, txn_ccy, default_ccy,
                        as_of=split.transaction.post_date,
                    )
                    if rate is not None:
                        value = value * rate
                purchase_value_default += value

            # Prorate cost basis on shares-to-sell. Avoids the precision
            # loss of ``cost_per_share * shares`` for non-round
            # per-share costs (e.g., $100 / 3 shares: prorate gives
            # exactly $100 for selling all 3, while the divide-then-
            # multiply path gives $99.99...). Tax-relevant.
            cost_basis = (
                purchase_value_default * shares_to_sell
                / raw["purchase_quantity"]
            )
            proceeds = price * shares_to_sell
            gain = proceeds - cost_basis
            gain_pct = (gain / cost_basis * 100) if cost_basis else Decimal(0)

            return {
                "shares": _format_number(shares_to_sell, decimals=4),
                "cost_basis": _format_number(cost_basis, decimals=2),
                "sale_proceeds": _format_number(proceeds, decimals=2),
                "capital_gain": _format_number(gain, decimals=2),
                # The 26-digit case the spec called out — ``(gain /
                # cost_basis) * 100`` produces an unbounded repeating
                # decimal in the general case. 2 decimal places is
                # what humans and reports actually use.
                "gain_percent": _format_number(gain_pct, decimals=2),
            }

    def close_lot(self, guid: str) -> dict:
        """Mark a lot as closed.

        Use when a lot is fully sold but wasn't automatically marked closed,
        or to manually close a lot with zero shares.

        Args:
            guid: Lot GUID.

        Returns:
            Dict with status.

        Raises:
            ValueError: If lot not found or already closed.
        """
        with self.open(readonly=False) as book:
            lot = self._find_lot(book, guid)
            if not lot:
                raise ValueError(f"Lot not found: {guid}")

            if lot.is_closed:
                raise ValueError("Lot is already closed")

            # GnuCash uses -1 for boolean true
            lot.is_closed = -1
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
