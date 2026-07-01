# Architecture / contracts review — v1.3 pre-release

Captured from deep-read agent run on 2026-06-02.

## Summary

Strong architectural discipline: contract tests lock the
tool-to-module mapping, lazy-load semantics, and MCP decorator
patterns. v1.3 restructuring is correctly implemented. Two specific
gaps to address; otherwise production-ready.

## Findings

### `get_server_config` lacks `@audit_log` (`server.py:786-788`)
Every other tool carries `@audit_log(classification="read")`. This
one is registered with only `@mcp.tool()` + `@safe_tool`. Comment
above frames it as a deliberate diagnostic surface registered outside
the module gate, so the omission may be intentional. Either way,
break "every tool call logged" — decide and document.

### `_strip_noise` type-hint gap (`tools/_helpers.py:149`)
Peer helpers carry hints; this one doesn't.

### `SplitInput` model uses `extra="ignore"` (`tools/_helpers.py:83-86`)
Server-global `extra="forbid"` already catches unknowns; the local
override creates a one-off inconsistency. Promote to `forbid` for
clarity.

### `MODULE_GROUPS` member validation incomplete (`server.py`)
`_apply_module_filter` validates group names exist but not that group
members exist in `TOOL_MODULES`. Invalid members would silently
filter out. Add upstream validation.

### Audit-log formatter dispatch falls through silently (`logging_config.py`)
Unknown `(entity_type, operation)` pairs render empty with no
warning. A new write tool without a formatter would silently emit
nothing. Add a startup check (or a contract test) that every
registered write tool has a formatter.

## What looks right

- Module-composition MRO guard prevents accidental shadowing.
- Write verification — all writes call `_verify_*` before session
  close.
- Audit-log dispatch architecture — clean `(entity_type, operation)`
  → formatter table.
- Contract tests — bidirectional totality checks in
  `tests/test_modules.py`.
- Decorator stacking — consistent `@mcp.tool() → @safe_tool →
  @audit_log()`.
- Lazy-load idempotency — `_loaded_tool_files` tracking prevents
  re-imports.
- Pydantic strict validation — global `extra="forbid"` at import
  time.
- No dead code — all functions serve clear purposes.
- Documentation — CLAUDE.md patterns accurate and current; README
  and CHANGELOG comprehensive.

## Minor

- `_LOADED_MODULES` docstring should clarify it contains both
  sub-module names AND group names when groups are fully loaded.

## v1.4 follow-ups

- Extend contract tests to verify `@audit_log` presence on every
  tool.
- Add startup validation for MODULE_GROUPS members.
- Document formatter expectations (write tools MUST have dispatcher
  entries).
- Standardize helper type annotations.
