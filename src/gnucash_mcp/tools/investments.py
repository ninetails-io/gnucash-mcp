"""Investment tools: commodities, prices, and lots (cost-basis tracking)."""

from datetime import date as date_type

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import (
    LotGuid,
    SplitGuid,
    _json,
    safe_tool,
)


def register(mcp, get_book) -> None:
    """Attach investment tools to the FastMCP server."""

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_commodities(
        verbose: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """List all commodities (currencies, stocks, etc.) in the book.

        Leads with a ``Showing X-Y of Z commodities`` line, then a
        compact one-line-per-commodity format by default. Page with
        ``offset``; ``limit=0`` returns the count only. Use verbose=true
        for full JSON with fraction, latest prices, etc.

        Args:
            verbose: If true, return full JSON details for each commodity.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
        """
        book = get_book()
        result = book.list_commodities(
            compact=not verbose, limit=limit, offset=offset
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="commodity")
    def create_commodity(
        mnemonic: str,
        fullname: str,
        namespace: str = "FUND",
        fraction: int = 10000,
        cusip: str | None = None,
    ) -> str:
        """Create a new commodity (stock, mutual fund, etc.).

        Args:
            mnemonic: Symbol (e.g., "VTSAX"). Unique within namespace.
            fullname: Human-readable name.
            namespace: "FUND" (default) for mutual funds, "NASDAQ"/"NYSE"/
                "AMEX" for stocks, or any custom string.
            fraction: Smallest fractional unit. 1 = whole units, 100 =
                2 decimals, 10000 = 4 decimals (default, shares),
                1000000 = 6 decimals (crypto).
            cusip: Optional CUSIP/ISIN identifier.
        """
        book = get_book()
        result = book.create_commodity(
            mnemonic=mnemonic,
            fullname=fullname,
            namespace=namespace,
            fraction=fraction,
            cusip=cusip,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="price")
    def create_price(
        commodity: str,
        namespace: str,
        value: str,
        currency: str | None = None,
        date: str | None = None,
        price_type: str = "nav",
        source: str = "user:price",
    ) -> str:
        """Record a price for a commodity (stock, NAV, exchange rate).

        An existing price with the same commodity/currency/date/source
        is updated rather than duplicated.

        Args:
            commodity: Symbol (e.g., "VTSAX").
            namespace: Commodity namespace (e.g., "FUND", "NASDAQ").
            value: Price per unit as decimal string (e.g., "250.45").
            currency: ISO currency code. Defaults to the book's default
                currency — so ``create_price(commodity="USD", value="7.30")``
                on a CNY-default book stores "1 USD = 7.30 CNY" (the
                natural reading). Pass explicitly for cross-currency
                pairs that don't involve the book default.
            date: ISO date (YYYY-MM-DD). Defaults to today.
            price_type: "nav" (default, mutual funds), "last", "bid",
                "ask", or "unknown".
            source: Source identifier. Default "user:price".
        """
        book = get_book()
        price_date = date_type.fromisoformat(date) if date else None

        result = book.create_price(
            commodity=commodity,
            namespace=namespace,
            value=value,
            currency=currency,
            price_date=price_date,
            price_type=price_type,
            source=source,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="price")
    def delete_price(
        commodity: str,
        namespace: str,
        date: str,
        source: str | None = None,
    ) -> str:
        """Delete a single price entry.

        Identifies the price by ``(commodity, namespace, date)``.
        Pass ``source`` to disambiguate when multiple prices exist
        on the same commodity+date (e.g. one user-entered and one
        fetched from a feed).

        Args:
            commodity: Symbol (e.g., "VTSAX", "USD", "EUR").
            namespace: Namespace (e.g., "FUND", "CURRENCY").
            date: ISO date (YYYY-MM-DD) of the price to delete.
            source: Optional source tag (e.g., "user:price",
                "user:yfinance"). Required when multiple prices
                exist on the same commodity+date.
        """
        book = get_book()
        result = book.delete_price(
            commodity=commodity,
            namespace=namespace,
            price_date=date_type.fromisoformat(date),
            source=source,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_prices(
        commodity: str,
        namespace: str,
        start_date: str | None = None,
        end_date: str | None = None,
        currency: str | None = None,
        limit: int = 50,
        verbose: bool = False,
        offset: int = 0,
    ) -> str:
        """Get price history for a commodity.

        Leads with a ``Showing X-Y of Z prices (date range)`` line, then
        a compact aligned text table by default. Page with ``offset``;
        ``limit=0`` returns the count only. Use verbose=true for the full
        structured envelope (``prices`` list, ``showing``, ``total``,
        ``offset``, ``count``).

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX").
            namespace: Namespace of the commodity (e.g., "FUND").
            start_date: Optional start date filter (YYYY-MM-DD).
            end_date: Optional end date filter (YYYY-MM-DD).
            currency: Optional currency filter (e.g., "USD").
            limit: Page size (default 50, max 250). 0 = count only.
            verbose: If true, return the structured dict.
            offset: 0-indexed first row to return (default 0).
        """
        book = get_book()
        start = date_type.fromisoformat(start_date) if start_date else None
        end = date_type.fromisoformat(end_date) if end_date else None

        result = book.get_prices(
            commodity=commodity,
            namespace=namespace,
            start_date=start,
            end_date=end,
            currency=currency,
            limit=limit,
            compact=not verbose,
            offset=offset,
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_latest_price(
        commodity: str,
        namespace: str,
        currency: str | None = None,
    ) -> str:
        """Get the most recent price for a commodity.

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX").
            namespace: Namespace of the commodity (e.g., "FUND").
            currency: Currency for the price. Defaults to the book's
                default currency. Pass explicitly to get a price
                quoted in a non-default currency.

        Returns:
            JSON with date, value, type, and source of most recent price.
            Returns null if no price exists.
        """
        book = get_book()
        result = book.get_latest_price(
            commodity=commodity,
            namespace=namespace,
            currency=currency,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="lot")
    def create_lot(
        account: str,
        title: str,
        notes: str = "",
    ) -> str:
        """Create a new lot for cost basis tracking.

        Lots group investment purchases for tracking cost basis and
        calculating capital gains when selling.

        Args:
            account: Account ref for the investment account: full path (e.g., "Assets:Investments:VTSAX"), %short GUID, or full 32-char GUID.
            title: Lot identifier (e.g., "VTSAX 2026-01-15 purchase").
            notes: Optional notes.
        """
        book = get_book()
        result = book.create_lot(account=account, title=title, notes=notes)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_lots(
        account: str,
        include_closed: bool = False,
        verbose: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """List all lots for an investment account.

        Leads with a ``Showing X-Y of Z lots`` line, then a compact
        one-line-per-lot format by default. Page with ``offset``;
        ``limit=0`` returns the count only. Use verbose=true for full
        JSON with guid, title, notes, etc.

        Args:
            account: Account ref (full path, %short GUID, or full 32-char GUID).
            include_closed: If True, include fully-sold lots. Default False.
            verbose: If true, return full JSON details for each lot.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
        """
        book = get_book()
        result = book.list_lots(
            account=account, include_closed=include_closed,
            compact=not verbose, limit=limit, offset=offset,
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_lot(
        guid: LotGuid,
    ) -> str:
        """Get detailed information about a lot.

        Args:
            guid: Lot GUID (or 8+ char prefix).

        Returns:
            JSON with lot details including all splits:
            - title, notes, is_closed
            - splits: list of all splits with date, quantity, value
            - summary: total quantity, cost basis, cost per share
        """
        book = get_book()
        result = book.get_lot(guid=guid)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="lot")
    def assign_split_to_lot(
        split_guid: SplitGuid,
        lot_guid: LotGuid,
    ) -> str:
        """Assign a transaction split to a lot.

        Use after creating a buy/sell transaction to link the investment
        account split to its lot for cost basis tracking.

        Args:
            split_guid: GUID of the split (from transaction's investment account). 8+ char prefix accepted.
            lot_guid: GUID of the lot (or 8+ char prefix).

        Workflow:
            1. create_lot("Assets:VTSAX", "VTSAX Jan 2026")
            2. create_transaction(...buy 10 shares...)
            3. assign_split_to_lot(investment_split_guid, lot_guid)
        """
        book = get_book()
        result = book.assign_split_to_lot(split_guid=split_guid, lot_guid=lot_guid)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def calculate_lot_gain(
        lot_guid: LotGuid,
        shares: str | None = None,
        sale_price: str | None = None,
    ) -> str:
        """Calculate potential or actual capital gain for a lot.

        If shares and sale_price provided, calculates hypothetical gain.
        Otherwise uses lot's current state and latest price.

        Args:
            lot_guid: Lot GUID (or 8+ char prefix).
            shares: Optional number of shares to calculate for.
                    Defaults to all remaining shares.
            sale_price: Optional sale price per share.
                        Defaults to latest price for the commodity.
        """
        book = get_book()
        result = book.calculate_lot_gain(
            lot_guid=lot_guid, shares=shares, sale_price=sale_price,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="lot")
    def close_lot(
        guid: LotGuid,
    ) -> str:
        """Mark a lot as closed.

        Use when a lot is fully sold but wasn't automatically marked closed,
        or to manually close a lot with zero shares.

        Args:
            guid: Lot GUID (or 8+ char prefix).

        Note:
            Lots are automatically marked closed when their quantity reaches zero
            through assigned splits. This tool is for manual cleanup.
        """
        book = get_book()
        result = book.close_lot(guid=guid)
        return _json(result)
