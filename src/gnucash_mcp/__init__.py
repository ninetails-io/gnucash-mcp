"""GnuCash MCP Server - AI assistant interface to GnuCash accounting data."""

from gnucash_mcp.book import GnuCashLockError
from gnucash_mcp.server import main

__version__ = "1.3.0"
__all__ = ["main", "GnuCashLockError"]
