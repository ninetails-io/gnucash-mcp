"""MCP tool implementations for GnuCash operations."""

from mcp.types import Tool, TextContent

# Tool definitions will be registered with the server
# Each tool maps to a GnuCashBook method

TOOLS: list[Tool] = [
    Tool(
        name="list_accounts",
        description="List all accounts in the GnuCash chart of accounts",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="get_account",
        description="Get details for a specific account by name",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Full account name (e.g., 'Assets:Bank:Checking')",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="get_balance",
        description="Get the balance of an account, optionally as of a specific date",
        inputSchema={
            "type": "object",
            "properties": {
                "account_name": {
                    "type": "string",
                    "description": "Full account name (e.g., 'Assets:Bank:Checking')",
                },
                "as_of_date": {
                    "type": "string",
                    "description": "Date in ISO format (YYYY-MM-DD). Defaults to current date.",
                },
            },
            "required": ["account_name"],
        },
    ),
    Tool(
        name="list_transactions",
        description="List transactions with optional filters",
        inputSchema={
            "type": "object",
            "properties": {
                "account": {
                    "type": "string",
                    "description": "Filter by account name",
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date in ISO format (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in ISO format (YYYY-MM-DD)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of transactions to return (default 50)",
                    "default": 50,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="get_transaction",
        description="Get details for a specific transaction by GUID",
        inputSchema={
            "type": "object",
            "properties": {
                "guid": {
                    "type": "string",
                    "description": "Transaction GUID (32-character hex string)",
                },
            },
            "required": ["guid"],
        },
    ),
    Tool(
        name="create_transaction",
        description="Create a new transaction with splits. Splits must balance to zero.",
        inputSchema={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Transaction description",
                },
                "date": {
                    "type": "string",
                    "description": "Transaction date in ISO format (YYYY-MM-DD). Defaults to today.",
                },
                "splits": {
                    "type": "array",
                    "description": "List of splits. Each split has 'account' (name) and 'amount' (string, positive for debit, negative for credit)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "account": {
                                "type": "string",
                                "description": "Full account name",
                            },
                            "amount": {
                                "type": "string",
                                "description": "Amount as string (e.g., '100.00' or '-100.00')",
                            },
                            "memo": {
                                "type": "string",
                                "description": "Optional memo for this split",
                            },
                        },
                        "required": ["account", "amount"],
                    },
                },
            },
            "required": ["description", "splits"],
        },
    ),
    Tool(
        name="search_transactions",
        description="Search transactions by description, memo, or amount",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string",
                },
                "field": {
                    "type": "string",
                    "enum": ["description", "memo", "amount"],
                    "description": "Field to search (default: description)",
                    "default": "description",
                },
            },
            "required": ["query"],
        },
    ),
]


async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool calls to appropriate handlers.

    Args:
        name: Tool name.
        arguments: Tool arguments.

    Returns:
        List of TextContent with the result.
    """
    # TODO: Implement tool routing to GnuCashBook methods
    raise NotImplementedError(f"Tool {name} not yet implemented")
