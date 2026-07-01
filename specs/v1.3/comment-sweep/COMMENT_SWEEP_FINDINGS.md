# Comment Sweep — Findings

Issues noticed during the sweep that are **out of its scope** (they
require executable-line or wire-surface changes). The sweep diff is
comments and docstrings only; nothing below was acted on.

## 1. History-talk in a user-facing error string (server.py)

`_apply_module_filter`'s unknown-module error message ends with:

> "Fix the typo and restart the server. Partial-load was the previous
> behavior; v1.3 fails fast so configuration errors surface at startup
> instead of as missing tools downstream."

The "previous behavior / v1.3" sentence is release archaeology shipped
to end users in an executable string literal. Suggest trimming to
"Fix the typo and restart the server." in a normal code change.

## 2. Stale `guid` mentions in wire docstrings (tools/*.py)

Several verbose-mode tool docstrings still promise a `guid` field that
the book layer deliberately omits from those dicts:

- `list_customers` / `list_vendors` / `list_employees` — "full JSON
  with guid, address, notes" (business-person dicts carry no `guid`)
- `list_billterms` — "full JSON with guid, discount details"
- `list_scheduled_transactions` — "full JSON with GUIDs, splits"
  (this one is accurate — SX dicts do carry `guid` — listed for
  completeness of the audit)
- `get_taxtable` — "Returns guid, name, refcount" (`_taxtable_to_dict`
  omits `guid`)

These are MCP tool descriptions (wire surface), so correcting them is
a behavior-visible change that needs its own validated pass.

## 3. `BusinessAddressInput` docstring is wire-visible

The class docstring surfaces in six tools' input schemas (`$defs`
description). It still contains the "MP-5" token; cleaning it is a
wire-surface change, deliberately not done here (see the whitelist in
COMMENT_SWEEP_MANIFEST.md and the revert commit f399761).

## 4. Dead parameter + wrong type annotation (logging_config.py)

- `_extract_after_state(result, entity_type)` — `entity_type` is
  never used in the body.
- `_AUDIT_HANDLERS: dict[str, Callable[...]]` — keys are
  `(entity_type, operation)` tuples, not `str`. Harmless at runtime,
  wrong for type-checkers.

## 5. `book/__init__.py` docstring vs. exports

The module docstring's compat example names `_verify_write` as a
stable import; true today, but the `__all__` list is the actual
contract — worth a contract test if external callers matter.
