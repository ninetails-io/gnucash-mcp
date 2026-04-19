"""Investment tools: commodities, prices, and lots (cost-basis tracking)."""

from datetime import date as date_type
from typing import Annotated

from pydantic import Field

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import _json, safe_tool


def register(mcp, get_book) -> None:
    """Attach investment tools to the FastMCP server."""

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_commodities(verbose: bool = False) -> str:
        """List all commodities (currencies, stocks, etc.) in the book.

        Returns a compact one-line-per-commodity format by default.
        Use verbose=true for full JSON with fraction, latest prices, etc.

        Args:
            verbose: If true, return full JSON details for each commodity.
        """
        book = get_book()
        result = book.list_commodities(compact=not verbose)
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
        """Create a new commodity (stock, mutual fund, etc.) in the book.

        Args:
            mnemonic: Symbol (e.g., "VTSAX", "AAPL"). Must be unique within namespace.
            fullname: Full name (e.g., "Vanguard Total Stock Market Index Fund").
            namespace: Grouping category. Common values:
                - "FUND" for mutual funds (default)
                - "NASDAQ", "NYSE", "AMEX" for stocks
                - Any custom string for other assets
            fraction: Smallest fractional unit. Use:
                - 1 for whole units only
                - 100 for 2 decimal places
                - 10000 for 4 decimal places (default, standard for shares)
                - 1000000 for 6 decimal places (crypto)
            cusip: Optional CUSIP/ISIN identifier for the security.
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
        currency: str = "USD",
        date: str | None = None,
        price_type: str = "nav",
        source: str = "user:price",
    ) -> str:
        """Record a price for a commodity (stock price, NAV, exchange rate).

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX", "AAPL").
            namespace: Namespace of the commodity (e.g., "FUND", "NASDAQ").
            value: Price per unit as decimal string (e.g., "250.45").
            currency: Currency the price is denominated in. Default "USD".
            date: Price date in ISO format (YYYY-MM-DD). Defaults to today.
            price_type: Type of price:
                - "nav" for mutual fund net asset value (default)
                - "last" for last trade price
                - "bid" / "ask" for bid/ask prices
                - "unknown" for unspecified
            source: Source identifier. Default "user:price".

        Note:
            If a price already exists for the same commodity/currency/date/source,
            it will be updated rather than creating a duplicate.
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
    @audit_log(classification="read")
    def get_prices(
        commodity: str,
        namespace: str,
        start_date: str | None = None,
        end_date: str | None = None,
        currency: str | None = None,
    ) -> str:
        """Get price history for a commodity.

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX").
            namespace: Namespace of the commodity (e.g., "FUND").
            start_date: Optional start date filter (YYYY-MM-DD).
            end_date: Optional end date filter (YYYY-MM-DD).
            currency: Optional currency filter (e.g., "USD").

        Returns:
            JSON with list of prices sorted by date descending (most recent first).
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
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_latest_price(
        commodity: str,
        namespace: str,
        currency: str = "USD",
    ) -> str:
        """Get the most recent price for a commodity.

        Args:
            commodity: Symbol of the commodity (e.g., "VTSAX").
            namespace: Namespace of the commodity (e.g., "FUND").
            currency: Currency for the price. Default "USD".

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
            account: Full path of investment account (e.g., "Assets:Investments:VTSAX").
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
    ) -> str:
        """List all lots for an investment account.

        Returns a compact one-line-per-lot format by default.
        Use verbose=true for full JSON with guid, title, notes, etc.

        Args:
            account: Full path of investment account.
            include_closed: If True, include fully-sold lots. Default False.
            verbose: If true, return full JSON details for each lot.
        """
        book = get_book()
        result = book.list_lots(account=account, include_closed=include_closed, compact=not verbose)
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_lot(
        guid: Annotated[str, Field(description="Lot GUID (or 8+ char prefix)")],
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
        split_guid: Annotated[str, Field(description="GUID of the split (from transaction's investment account). 8+ char prefix accepted.")],
        lot_guid: Annotated[str, Field(description="GUID of the lot (or 8+ char prefix)")],
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
        lot_guid: Annotated[str, Field(description="Lot GUID (or 8+ char prefix)")],
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
        guid: Annotated[str, Field(description="Lot GUID (or 8+ char prefix)")],
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
