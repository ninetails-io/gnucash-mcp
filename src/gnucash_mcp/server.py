"""MCP server definition for GnuCash."""

import asyncio
import json
import os
from datetime import date

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool

from gnucash_mcp.book import GnuCashBook
from gnucash_mcp.tools import TOOLS

server = Server("gnucash-mcp")

# Global book instance - initialized on first use
_book: GnuCashBook | None = None


def get_book() -> GnuCashBook:
    """Get or create the GnuCashBook instance."""
    global _book
    if _book is None:
        path = os.environ.get("GNUCASH_BOOK_PATH")
        if not path:
            raise ValueError("GNUCASH_BOOK_PATH environment variable not set")
        _book = GnuCashBook(path)
    return _book


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return available tools."""
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls by routing to GnuCashBook methods."""
    book = get_book()

    try:
        if name == "list_accounts":
            result = book.list_accounts()

        elif name == "get_account":
            result = book.get_account(arguments["name"])
            if result is None:
                result = {"error": f"Account not found: {arguments['name']}"}

        elif name == "get_balance":
            as_of_date = None
            if "as_of_date" in arguments and arguments["as_of_date"]:
                as_of_date = date.fromisoformat(arguments["as_of_date"])
            balance = book.get_balance(arguments["account_name"], as_of_date)
            result = {
                "account": arguments["account_name"],
                "balance": str(balance),
                "as_of_date": as_of_date.isoformat() if as_of_date else "current",
            }

        elif name == "list_transactions":
            start = None
            end = None
            if "start_date" in arguments and arguments["start_date"]:
                start = date.fromisoformat(arguments["start_date"])
            if "end_date" in arguments and arguments["end_date"]:
                end = date.fromisoformat(arguments["end_date"])
            limit = arguments.get("limit", 50)
            account = arguments.get("account")
            result = book.list_transactions(account, start, end, limit)

        elif name == "get_transaction":
            result = book.get_transaction(arguments["guid"])
            if result is None:
                result = {"error": f"Transaction not found: {arguments['guid']}"}

        elif name == "create_transaction":
            trans_date = None
            if "date" in arguments and arguments["date"]:
                trans_date = date.fromisoformat(arguments["date"])
            guid = book.create_transaction(
                description=arguments["description"],
                splits=arguments["splits"],
                trans_date=trans_date,
            )
            result = {"guid": guid, "status": "created"}

        elif name == "search_transactions":
            field = arguments.get("field", "description")
            result = book.search_transactions(arguments["query"], field)

        else:
            result = {"error": f"Unknown tool: {name}"}

    except ValueError as e:
        result = {"error": str(e)}
    except FileNotFoundError as e:
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


@server.list_resources()
async def list_resources() -> list[Resource]:
    """Return available resources."""
    return [
        Resource(
            uri="gnucash://accounts",
            name="Chart of Accounts",
            description="Full chart of accounts from the GnuCash book",
            mimeType="application/json",
        ),
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    """Handle resource reads."""
    book = get_book()

    if uri == "gnucash://accounts":
        accounts = book.list_accounts()
        return json.dumps(accounts, indent=2)

    # Handle balance resources: gnucash://balance/{account_name}
    if uri.startswith("gnucash://balance/"):
        account_name = uri.replace("gnucash://balance/", "")
        # URL decode the account name (replace %3A with :)
        account_name = account_name.replace("%3A", ":")
        try:
            balance = book.get_balance(account_name)
            return json.dumps(
                {"account": account_name, "balance": str(balance)}, indent=2
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})

    return json.dumps({"error": f"Unknown resource: {uri}"})


def main() -> None:
    """Run the MCP server."""

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
