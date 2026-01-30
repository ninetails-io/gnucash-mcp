"""MCP server definition for GnuCash."""

import asyncio
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server


server = Server("gnucash-mcp")


def get_book_path() -> str:
    """Get the GnuCash book path from environment."""
    path = os.environ.get("GNUCASH_BOOK_PATH")
    if not path:
        raise ValueError("GNUCASH_BOOK_PATH environment variable not set")
    if not os.path.exists(path):
        raise FileNotFoundError(f"GnuCash book not found: {path}")
    return path


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
