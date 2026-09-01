"""GnuCash MCP Server - AI assistant interface to GnuCash accounting data."""

# Applied FIRST — before any sibling module imports — so every
# GNUCASH_* env read anywhere in the package (import-time seeds
# included) sees the advanced-options overrides. `python -m
# gnucash_mcp` executes this file before gnucash_mcp.server, so
# applying inside server.py would be too late for that guarantee.
from gnucash_mcp._env import _apply_advanced_env

_apply_advanced_env()

from gnucash_mcp.book import GnuCashLockError
from gnucash_mcp.server import main

__version__ = "1.4.4"
__all__ = ["main", "GnuCashLockError"]
