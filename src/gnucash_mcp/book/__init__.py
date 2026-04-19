"""GnuCashBook package — assembled from BaseGnuCashBook + module mixins.

`build_book_class(modules)` constructs a GnuCashBook class with only
the mixins needed for the requested modules. Modules not in the set
are never imported, so their methods never parse into memory.

The module-level `GnuCashBook` symbol is the "all modules" variant,
used by tests and by any caller that hasn't opted into module
filtering (backward compatibility).

Public exports match what the old book.py exposed so existing
imports (`from gnucash_mcp.book import GnuCashBook, _verify_write`)
continue to work.
"""

import importlib

from gnucash_mcp.book._base import (
    BaseGnuCashBook,
    GnuCashLockError,
    _verify_composite_write,
    _verify_delete,
    _verify_write,
)
from gnucash_mcp.book._legacy import LegacyMixin

# Map of module name → (relative import path, mixin class name).
# As each module is peeled off the monolith, add an entry here.
_MIXIN_MAP: dict[str, tuple[str, str]] = {
    "admin": (".admin", "AdminMixin"),
    "business": (".business", "BusinessMixin"),
}


def _load_mixin(module_name: str):
    """Import and return the mixin class for a module name."""
    if module_name not in _MIXIN_MAP:
        raise ValueError(f"No extracted mixin for module: {module_name}")
    mod_path, cls_name = _MIXIN_MAP[module_name]
    module = importlib.import_module(mod_path, package=__package__)
    return getattr(module, cls_name)


def extracted_modules() -> set[str]:
    """Return the set of modules that have their own mixin file.

    Modules not in this set still live in LegacyMixin. Tool registration
    logic uses this to decide which modules can be lazy-loaded from
    gnucash_mcp.tools.<name> vs. filtered from server.py-registered tools.
    """
    return set(_MIXIN_MAP.keys())


def build_book_class(modules: set[str] | None = None) -> type:
    """Build a GnuCashBook class with the requested module mixins.

    Args:
        modules: Set of enabled module names (e.g., {"core", "admin"}).
                 If None, include all extracted mixins (default class).

    Returns:
        Dynamically-created class inheriting from LegacyMixin, the
        requested module mixins, and BaseGnuCashBook.
    """
    mixins: list[type] = [LegacyMixin]

    if modules is None:
        # Default / tests: include every extracted mixin
        for name in sorted(_MIXIN_MAP.keys()):
            mixins.append(_load_mixin(name))
    else:
        for name in sorted(modules):
            if name in _MIXIN_MAP:
                mixins.append(_load_mixin(name))

    bases = tuple(mixins) + (BaseGnuCashBook,)

    # Guard against accidental method-name collisions between mixins
    seen: dict[str, str] = {}
    for m in mixins:
        for attr in vars(m):
            if attr.startswith("__"):
                continue
            if attr in seen:
                raise RuntimeError(
                    f"Mixin collision: '{attr}' defined in both "
                    f"{seen[attr]} and {m.__name__}"
                )
            seen[attr] = m.__name__

    return type("GnuCashBook", bases, {})


# Default export — "all modules" class for backward compatibility
GnuCashBook = build_book_class(None)


__all__ = [
    "BaseGnuCashBook",
    "GnuCashBook",
    "GnuCashLockError",
    "build_book_class",
    "extracted_modules",
    "_verify_write",
    "_verify_composite_write",
    "_verify_delete",
]
