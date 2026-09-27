"""Drive the gnucash-mcp server over stdio, the way a client does.

Usage: uv run python mcpcall.py BOOK CALLS.json
CALLS.json is a list of [tool_name, {args}] pairs, run in order in one
server session. Each reply is printed exactly as the client receives it.
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO = "/Users/stephen/Projects/gnucash-mcp"


async def main(book: str, calls: list) -> None:
    params = StdioServerParameters(
        command="uv",
        args=["run", "--directory", REPO, "gnucash-mcp"],
        env={
            **os.environ,
            "GNUCASH_BOOK_PATH": book,
            "GNUCASH_MCP_MODULES": "all",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for name, args in calls:
                result = await session.call_tool(name, args)
                text = "\n".join(
                    c.text for c in result.content if hasattr(c, "text")
                )
                flag = " [isError]" if result.isError else ""
                print(f"\n>>> {name} {json.dumps(args)}{flag}\n{text}")


if __name__ == "__main__":
    with open(sys.argv[2]) as f:
        asyncio.run(main(sys.argv[1], json.load(f)))
