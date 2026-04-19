"""MCP tool registration modules.

Each submodule (admin, reconciliation, reporting, etc.) exports a
`register(mcp, get_book)` function that attaches its tools to the
FastMCP server when called. This allows main() to lazy-import only
the tool modules requested via --modules, so disabled modules never
parse their tool definitions or build Pydantic schemas.
"""
