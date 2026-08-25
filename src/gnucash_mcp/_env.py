"""Environment-interface primitives, importable by every layer.

This module owns two things the rest of the package builds on:

* **The boolean toggle vocabulary** (``_TOGGLE_TRUE`` /
  ``_TOGGLE_FALSE`` and ``_parse_env_toggle``) — the single parser
  for every GNUCASH_* boolean env var. The MCPB manifest renders
  checkboxes as ``"true"``/``"false"``; CLI users type ``1``/``0``;
  two sites once parsed this independently and a third convention
  (bare ``== "1"``) made ``GNUCASH_MCP_DEBUG=true`` a silent no-op
  (PR #153 review).

* **The advanced-options passthrough** (``_apply_advanced_env``) —
  GNUCASH_MCP_ADVANCED holds shell-style ``GNUCASH_*=value`` pairs
  applied into the process environment. It is called from the top of
  ``gnucash_mcp/__init__.py``, BEFORE any sibling module imports, so
  every env read anywhere in the package — import-time seeds
  included — sees the overrides. (``python -m gnucash_mcp`` executes
  ``__init__`` first; applying inside ``server.py`` was too late for
  that guarantee.)

It deliberately imports nothing from the package so ``server.py``
and ``logging_config.py`` can both use it without a cycle.
"""

from __future__ import annotations

import os
import sys

_TOGGLE_TRUE = frozenset({"true", "1", "yes", "on"})
_TOGGLE_FALSE = frozenset({"false", "0", "no", "off", ""})


def _parse_env_toggle(var: str) -> bool | None:
    """Read a boolean env toggle: None when unset, True/False for the
    accepted vocabulary, ValueError naming the variable and value on
    anything else — a typo'd toggle must not silently serve the
    default it meant to change."""
    raw = os.environ.get(var)
    if raw is None:
        return None
    norm = raw.strip().lower()
    if norm in _TOGGLE_TRUE:
        return True
    if norm in _TOGGLE_FALSE:
        return False
    raise ValueError(
        f"Invalid {var}={raw!r}: expected true/false "
        "(also accepted: 1/0, yes/no, on/off)"
    )


# Malformed configuration found at import. Import must stay
# exception-safe (tests, inspectors, and `mcp dev` import this
# package without running main()), so problems are recorded here —
# and echoed to stderr immediately so harnesses that never reach
# main() still surface them — while main() upgrades them to a fatal
# exit 2.
_env_errors: list[str] = []

# Keys the box must refuse: itself (recursion), and the book list —
# --book (which the MCPB manifest always passes) unconditionally
# overwrites GNUCASH_BOOK_PATH, so accepting it here would be the one
# misconfiguration that silently evaporates instead of failing fast.
_ADVANCED_REJECTED_KEYS = {
    "GNUCASH_MCP_ADVANCED": "it cannot set itself",
    "GNUCASH_BOOK_PATH": (
        "books are configured through the book picker / --book, "
        "which overrides this variable"
    ),
}


def _apply_advanced_env() -> None:
    """Parse GNUCASH_MCP_ADVANCED into environment overrides.

    Shell-style tokens (quoted values may contain spaces) with
    backslash escaping DISABLED — ``shlex``'s POSIX escapes would
    silently mangle an unquoted Windows path
    (``GNUCASH_LOG_DIR=C:\\Temp`` became ``C:Temp``; PR #153 review),
    and no legitimate value here needs escapes. Keys are restricted
    to the GNUCASH_* namespace — this is a server-options field, not
    a general environment editor — and pairs OVERRIDE existing
    values, since the manifest itself sets GNUCASH_REDACT_PATHS=1
    and the box must be able to turn that off.
    """
    raw = os.environ.get("GNUCASH_MCP_ADVANCED")
    if not raw or not raw.strip():
        return
    import re
    import shlex
    lex = shlex.shlex(raw, posix=True)
    lex.whitespace_split = True
    lex.escape = ""
    before = len(_env_errors)
    try:
        tokens = list(lex)
    except ValueError as exc:
        _env_errors.append(
            f"Invalid advanced options (GNUCASH_MCP_ADVANCED): {exc}"
        )
        print(_env_errors[-1], file=sys.stderr)
        return
    for token in tokens:
        key, sep, value = token.partition("=")
        if key in _ADVANCED_REJECTED_KEYS:
            _env_errors.append(
                f"Invalid advanced option {key}: "
                f"{_ADVANCED_REJECTED_KEYS[key]}."
            )
            continue
        if not sep or not re.fullmatch(r"GNUCASH_[A-Z0-9_]+", key):
            _env_errors.append(
                f"Invalid advanced option {token!r}: expected "
                "GNUCASH_*=value pairs, e.g. GNUCASH_MCP_DEBUG=1 "
                'GNUCASH_LOG_DIR="/path/with spaces"'
            )
            continue
        os.environ[key] = value
    for line in _env_errors[before:]:
        print(line, file=sys.stderr)
