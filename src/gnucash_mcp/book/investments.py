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
from gnucash_mcp._format import _format_number, _paginate


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

            # One pass over book.prices builds the latest-quote map.
            # _is_market_price is required — a newer
            # type='transaction' placeholder would otherwise shadow
            # the user's last nav quote.
            latest_market: dict[str, tuple[date, "Price"]] = {}
            for p in book.prices:
                if not _is_market_price(p):
                    continue
                key = p.commodity.guid
                p_date = _to_date(p.date)
                existing = latest_market.get(key)
                if existing is None or p_date > existing[0]:
                    latest_market[key] = (p_date, p)

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

                if commodity.guid in latest_market:
                    _, price = latest_market[commodity.guid]
                    entry["latest_price"] = {
                        "value": str(price.value),
                        "currency": price.currency.mnemonic,
                        "date": _to_date(price.date).isoformat(),
                    }
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
        # fraction must be a positive integer — zero or negative
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

    @staticmethod
    def _upsert_price(
        book, comm, resolved_currency, price_date,
        value: str, price_type: str, source: str,
    ) -> str:
        """Create or update-in-place one price row; NO save.

        Single chokepoint for the same-(commodity, currency, date,
        source)-updates-in-place semantic, shared by ``create_price``
        and ``create_prices`` so single and batch entry can't
        diverge. The caller owns ``book.save()`` — batch saves once
        for the whole set.

        Returns ``"updated"`` or ``"created"``.
        """
        # Indexed query, not a full book.prices walk.
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
            return "updated"
        # piecash expects datetime.date, not datetime.datetime
        piecash.Price(
            commodity=comm,
            currency=resolved_currency,
            date=price_date,
            value=_to_decimal(value),
            type=price_type,
            source=source,
        )
        return "created"

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
        "user:price"), price_type (optional, default "nav")}``.

        Per-row semantics are ``create_price``'s exactly (shared
        upsert chokepoint): same (commodity, currency, date,
        source) updates in place → ``status: updated``; otherwise
        ``created``. ``on_error="abort"`` (default) sinks the whole
        batch on any invalid row; ``"skip"`` keeps the good rows.
        ``dry_run`` reports ``would_create`` / ``would_update``
        without writing.

        Returns ``{"results": TSV}`` — columns
        ``ref, status, commodity, date, value, currency, reason``.
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
                    _to_decimal(value)  # reject non-decimal early
                    prepared.append({
                        "ref": ref,
                        "comm": comm,
                        "currency": resolved_currency,
                        "date": p["date"],
                        "value": str(value),
                        "type": p.get("price_type") or "nav",
                        "source": p.get("source") or "user:price",
                    })
                except (ValueError, KeyError, ArithmeticError) as e:
                    by_ref[ref] = {
                        "ref": ref, "status": "rejected",
                        "reason": str(e),
                    }

            # Two rows sharing one canonical price identity
            # (commodity, currency, date, source) would race the
            # upsert against piecash's commit-time uniqueness
            # validation: the second row creates a duplicate the
            # save then rejects with an internals-leaking error,
            # while dry_run (each row checked against the original
            # book state) reports both as would_create. Rejecting
            # the duplicate up front keeps dry-run and live
            # agreeing and turns the failure into a per-row reason.
            seen_identity: dict = {}
            deduped = []
            for row in prepared:
                identity = (
                    row["comm"].guid, row["currency"].guid,
                    row["date"], row["source"],
                )
                first_ref = seen_identity.get(identity)
                if first_ref is not None:
                    by_ref[row["ref"]] = {
                        "ref": row["ref"], "status": "rejected",
                        "reason": (
                            f"duplicate price identity — same "
                            f"commodity/currency/date/source as "
                            f"ref '{first_ref}'"
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
                if dry_run:
                    # Same detection as the upsert, no mutation.
                    candidates = book.session.query(Price).filter_by(
                        commodity_guid=row["comm"].guid,
                        currency_guid=row["currency"].guid,
                        source=row["source"],
                    ).all()
                    exists = any(
                        _to_date(c.date) == row["date"]
                        for c in candidates
                    )
                    status = "would_update" if exists else "would_create"
                else:
                    status = self._upsert_price(
                        book, row["comm"], row["currency"],
                        row["date"], row["value"], row["type"],
                        row["source"],
                    )
                    wrote = True
                by_ref[row["ref"]] = {
                    "ref": row["ref"], "status": status,
                    "commodity": row["comm"].mnemonic,
                    "date": row["date"].isoformat(),
                    "value": row["value"],
                    "currency": row["currency"].mnemonic,
                }

            if wrote:
                book.save()
                self._invalidate_price_caches(book)
            return self._prices_envelope(prices, by_ref)

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
        price_type: str = "nav",
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
            price_type: "nav" (default), "last", "bid", "ask",
                "unknown".
            source: Source identifier. Default "user:price".

        Returns:
            Dict echoing the RESOLVED currency mnemonic, plus
            status "updated" (same commodity/currency/date/source
            existed) or "created".

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

            status = self._upsert_price(
                book, comm, resolved_currency, price_date,
                value, price_type, source,
            )
            existing = status == "updated"

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
                    "value": _format_number(p.value, decimals=4, strip_trailing=True),
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

            # Indexed query — only iterate prices for this commodity.
            candidates = book.session.query(Price).filter_by(
                commodity_guid=comm.guid,
            ).all()
            latest = None
            latest_date = None
            for p in candidates:
                if p.currency.mnemonic != currency:
                    continue
                # Skip type='transaction' placeholders — every other
                # valuation path excludes them, and this tool must
                # not return a transaction artifact where the user
                # expects their nav quote.
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
            # No session.add — the Lot auto-registers via the
            # Account.lots back-populate.
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
                raise ValueError(f"Account not found: {account}")

            default_ccy = self._require_default_currency(book)
            results = []
            for lot in acct.lots:
                if not include_closed and lot.is_closed:
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
            # into table-wide _resolve_guid lookups.
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

            default_ccy = self._require_default_currency(book)
            summary = self._lot_summary(lot, book, default_ccy)

            # Auto-close if quantity reaches zero; GnuCash uses -1 for boolean true
            auto_closed = False
            if Decimal(summary["quantity"]) == 0 and len(lot.splits) > 0:
                lot.is_closed = -1
                book.save()
                auto_closed = True

            # Input GUIDs are echoes — dropped. ``is_closed`` is
            # surfaced because the auto-close is what the caller
            # wants to know about.
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
            proceeds = price * shares_to_sell
            gain = proceeds - cost_basis
            gain_pct = (gain / cost_basis * 100) if cost_basis else Decimal(0)

            return {
                "shares": _format_number(shares_to_sell, decimals=4),
                "cost_basis": _format_number(cost_basis, decimals=2),
                "sale_proceeds": _format_number(proceeds, decimals=2),
                "capital_gain": _format_number(gain, decimals=2),
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
