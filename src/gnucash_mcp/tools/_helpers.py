"""Shared helpers for MCP tool wrappers.

These used to live in server.py; they are now shared across every
tool-registration module under gnucash_mcp/tools/.
"""

import json
import logging
import traceback
from functools import wraps
from typing import Annotated, Callable

from pydantic import Field

from gnucash_mcp.book import GnuCashLockError

logger = logging.getLogger(__name__)


# ── Shared GUID parameter annotations ──────────────────────────────
#
# Tools that accept GUIDs all describe the format the same way (full
# 32-char hex or a prefix of ≥8 chars, case-insensitive — validated
# in _resolve_guid). Centralizing the Annotated types here means one
# place to update the description and slightly shorter schema payloads
# across the ~15 GUID parameters the tool catalog advertises.

TransactionGuid = Annotated[
    str, Field(description="Transaction GUID (32-char hex or 8+ char prefix)")
]
SplitGuid = Annotated[
    str, Field(description="Split GUID (32-char hex or 8+ char prefix)")
]
LotGuid = Annotated[
    str, Field(description="Lot GUID (32-char hex or 8+ char prefix)")
]
ScheduledTransactionGuid = Annotated[
    str,
    Field(description="Scheduled transaction GUID (32-char hex or 8+ char prefix)"),
]


def _strip_noise(obj):
    """Recursively remove keys with None or empty-string values from dicts."""
    if isinstance(obj, dict):
        return {k: _strip_noise(v) for k, v in obj.items()
                if v is not None and v != ""}
    if isinstance(obj, list):
        return [_strip_noise(item) for item in obj]
    return obj


def _json(obj) -> str:
    """Serialize to minified JSON, stripping noise values."""
    return json.dumps(_strip_noise(obj), separators=(",", ":"))


def safe_tool(func: Callable) -> Callable:
    """Decorator that wraps tool functions with comprehensive error handling.

    Catches all exceptions and returns them as JSON error responses instead of
    crashing the MCP server.
    """

    @wraps(func)
    def wrapper(*args, **kwargs) -> str:
        try:
            return func(*args, **kwargs)
        except GnuCashLockError as e:
            logger.warning(f"Lock error in {func.__name__}: {e}")
            return _json(
                {
                    "error": str(e),
                    "error_type": "lock_error",
                    "suggestion": "Close GnuCash application and try again.",
                }
            )
        except FileNotFoundError as e:
            logger.error(f"File not found in {func.__name__}: {e}")
            return _json(
                {
                    "error": str(e),
                    "error_type": "file_not_found",
                    "suggestion": "Check that GNUCASH_BOOK_PATH is set correctly.",
                }
            )
        except ValueError as e:
            logger.warning(f"Validation error in {func.__name__}: {e}")
            return _json({"error": str(e), "error_type": "validation_error"})
        except Exception as e:
            logger.error(
                f"Unexpected error in {func.__name__}: {e}\n{traceback.format_exc()}"
            )
            return _json(
                {
                    "error": f"Unexpected error: {type(e).__name__}: {e}",
                    "error_type": "unexpected_error",
                }
            )

    return wrapper
