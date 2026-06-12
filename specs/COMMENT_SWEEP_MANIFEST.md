# Comment Sweep Manifest

Phase A classification for the comment sweep. Companion to
[COMMENT_DOCTRINE.md](COMMENT_DOCTRINE.md). Line numbers refer to the
working tree at the sweep snapshot (2026-06-11).

**Dispositions:** KEEP (untouched) / CUT (delete) / REWRITE (edit per the
stated reason) / FIX-FALSE (rule 6 — false about the present code; lands in
the `fix(comments):` commit).

Coverage: every run of 3+ consecutive `#` comment lines in `src/` appears
below (classified entries first, then auto-KEEP runs per file); every
banned-pattern hit is either inside a classified entry or in the whitelist
at the end; every history-bearing docstring has a classified entry. Tool
function docstrings in `tools/*.py` are the MCP wire surface and are out of
scope by policy — only `#` comments and non-tool docstrings there are
classified.
---

## src/gnucash_mcp/server.py

### server.py:15-32 | REWRITE | rule 2
extra='forbid' rationale is rule-1 gold; strip ONLY the attribution phrase 'Bookkeeper-found bug on PR #92 review:' — the `except=` vs `except_guids=` anecdote itself stays (rule-3 guardrail motivating extra='forbid')

```
# Strict tool-argument validation: reject unknown kwargs at the MCP
# boundary instead of silently ignoring them.
#
# FastMCP generates a Pydantic model per tool from the function
# signature, all inheriting from ``ArgModelBase``. The base class's
# default config doesn't set ``extra``, so Pydantic falls back to
# ``"ignore"`` — typo'd or stale-spec parameter names silently
# no-op. Bookkeeper-found bug on PR #92 review: calling
# ``reconcile_account`` with ``except=[...]`` (the spec's name)
# instead of ``except_guids=[...]`` (the actual Python-safe param)
# ran the tool with no exclusion at all, surfacing only as a
# balance mismatch downstream.
#
# Patching ``ArgModelBase.model_config`` to include
# ``extra="forbid"`` makes the dynamically-created arg models
# reject unknown fields with a clear ``"Extra inputs are not
# permitted"`` error. Applied at import time, before any tool
# module loads.
```

### server.py:74-86 | FIX-FALSE | rule 6
doctrine specimen: 'Today the dict is empty' is false (populated below); 'Populated in subsequent commits' is stale mid-refactor narration

```
# ---------------------------------------------------------------------------
# MODULE_GROUPS — composition aliases that expand to one or more modules.
#
# Lets ``--modules=core`` (or any group name) expand to a set of underlying
# module keys. Today the dict is empty — no behavior change vs. the
# pre-restructure baseline. Populated in subsequent commits as the
# role-aligned partition (Core / Personal / Portfolio / Investor /
# Freelancer / Business) takes shape.
#
# Expansion is single-pass (groups don't reference other groups). The
# partition is deliberately flat; if nesting becomes useful later we'll
# add cycle detection then.
# ---------------------------------------------------------------------------
```

### server.py:88-99 | REWRITE | rule 2
keep always-on semantics + why reconciliation is core; cut 'joined core in v1.3.1 — bookkeeper-flagged' migration history

```
    # ``core`` expands to its nine ledger sub-modules. Always-on
    # (force-added in _apply_module_filter), so a user with no
    # --modules flag gets all nine. Users can ALSO pick individual
    # sub-modules — e.g. ``--modules=accounts`` is valid but doesn't
    # change the fact that core is loaded too.
    #
    # ``reconciliation`` joined core in v1.3.1 — bookkeeper-flagged
    # that reconciliation touches money and every configuration
    # touches money, so excluding it from any persona-aligned cut
    # produced a server that couldn't reconcile statements (a hole
    # in the "any configuration that handles ledgers" promise).
    # Moved from the bookkeeper group to core; now always loaded.
```

### server.py:105-110 | REWRITE | rule 2
cut '(Reconciliation used to live here too — moved to core in v1.3.1)' history tail

```
    # ``bookkeeper`` bundles the personal-finance management
    # cluster: run reports, manage budgets, schedule recurring
    # transactions. The three underlying modules stay separately
    # selectable for users who want a finer cut.
    # (Reconciliation used to live here too — moved to core in
    # v1.3.1, see the comment above.)
```

### server.py:125-140 | REWRITE | rule 2
keep superset rationale + leaf selectability; cut 'Pre-v1.3 business was a standalone leaf… Bookkeeper-found on PR #92 review'

```
    # ``business`` is the small-business persona alias. It expands
    # to ``freelancer`` (the 19 customer-facing invoice tools) plus
    # ``business_complete`` (the 29 vendor/employee/jobs/credit-
    # notes/billing-terms tools). Pre-v1.3 ``business`` was a
    # standalone leaf containing only the second half — a user
    # picking ``--modules=business`` for "small business workflow"
    # got vendor management but couldn't create or post a customer
    # invoice. Bookkeeper-found on PR #92 review; the fix
    # restructures business into the natural superset.
    #
    # The two leaves stay independently selectable for users who
    # want a finer cut (a solo freelancer with no vendor activity
    # uses ``--modules=freelancer``; a back-office bookkeeper
    # managing only vendor side could pick
    # ``--modules=business_complete`` though that's a less common
    # carve-out).
```

### server.py:147-163 | REWRITE | rule 6
restate the non-1:1 mapping in present tense; 'Pre-restructure the mapping was 1:1… The restructure breaks that' is history framing

```
# ---------------------------------------------------------------------------
# MODULE_BACKED_BY — per public-module, the set of legacy tool-file /
# mixin names needed to back its tools.
#
# Pre-restructure the mapping was 1:1 (module ``X`` → tool file
# ``tools/X.py`` → mixin ``XMixin``). The restructure breaks that:
# Core's 29 tools include void/unvoid AND the reconciliation
# surface (both from ``reconciliation.py``), the slot tools +
# audit log (from ``admin.py``), and the backup tools (from
# ``backup.py``). The mixin classes still live in their original
# files; this dict tells ``_apply_module_filter`` which tool files to
# lazy-load AND ``main()`` which mixins to compose for the requested
# module set.
#
# An entry missing from this dict means "1:1 — uses the legacy name
# of the same module."
# ---------------------------------------------------------------------------
```

### server.py:205-208 | FIX-FALSE | rule 6
'normally all eight are loaded together' — core has NINE sub-modules (reconciliation included)

```
    # ── Core ledger sub-modules (composed via MODULE_GROUPS["core"]) ──
    # Each is independently selectable via --modules but normally all
    # eight are loaded together because the ``core`` group alias is
    # always force-added.
```

### server.py:381-394 | REWRITE | rule 2
keep entity/workflow split rationale; trim 'via the v1.3.1 redistribution' version archaeology

```
    # ``business_complete`` — vendor + employee surface only.
    # Together with ``freelancer`` (which now owns billterms,
    # jobs, and credit notes via the v1.3.1 redistribution),
    # forms the full small-business toolkit. Both leaves expand
    # together under the ``business`` group alias in
    # MODULE_GROUPS.
    #
    # Polymorphic tools (jobs, credit notes) live in freelancer
    # — a freelancer-only user uses them for customer-side
    # operations; vendor-side use requires ``business_complete``
    # via the _gate_owner_type check. This module owns the
    # *entities* (vendors, employees) and the *vendor-side
    # workflows* (bills, vouchers, vendor_spending_report) that
    # don't make sense without those entities.
```

### server.py:421-433 | REWRITE | rule 4
strip 'MP-10:' finding ID; compress the pre-fix typo-trap into a present-tense guardrail (silent empty expansion)

```
    """MP-10: every member of a MODULE_GROUPS expansion must exist
    in TOOL_MODULES.

    Pre-fix the group definitions referenced module names by
    convention only — a typo (``"reconcilation"`` vs
    ``"reconciliation"``) would silently produce an empty expansion
    at runtime: ``--modules=core`` would just not load the
    misspelled member, and the user would see "tool X not
    available" with no indication that the alias was the cause.

    This check fires at import time alongside ``_validate_tool_modules``
    so the developer feedback is immediate and loud.
    """
```

### server.py:498-508 | REWRITE | rule 3
guardrail is real (registered-tool heuristic false-positives); restate 'the old heuristic broke' as a present-tense warning

```
# Track which ``gnucash_mcp.tools.<file>`` modules have already had
# their ``register()`` called. The old heuristic — "skip if any tool
# from this module is already registered" — broke after the
# restructure: post-rebucket, ``void_transaction`` is in Core's tool
# list but lives in ``tools/reconciliation.py``. With reconciliation
# loaded first, ``any(t in registered for t in TOOL_MODULES['core'])``
# returns True (because void_transaction is registered) — so
# ``tools/core.py`` would never load, and create_transaction et al.
# would never register. Tracking files explicitly avoids the false
# positive.
_loaded_tool_files: set[str] = set()
```

### server.py:576-596 | REWRITE | rule 2
keep Claude-Desktop-stderr rationale + validation-before-'all' invariant; cut 'Pre-v1.3.0…', 'Bookkeeper-found bug on the PR #92 review pass', 'Pre-v1.3.1 all shortcut…'

```
    # Fail-fast on names that don't resolve to a known sub-module
    # or group. Pre-v1.3.0 this was a stderr warning, then partial
    # load — silent in practice because Claude Desktop captures
    # MCP server stderr into a log file the user never sees. A
    # typo'd ``--modules=bookeeper`` (missing the 'k') would
    # silently load only ``core``, leaving the user unable to
    # tell whether the tools they wanted are missing because
    # they typed it wrong or because the server is broken.
    # Bookkeeper-found bug on the PR #92 review pass; same
    # principle as ``extra="forbid"`` on tool kwargs — financial
    # software shouldn't silently swallow typos in configuration
    # either.
    #
    # **Validation runs BEFORE the ``all`` check** so a typo'd
    # name alongside ``all`` (e.g. ``--modules=bookeeper,all``)
    # still rejects. Pre-v1.3.1 ``all`` shortcut past validation
    # to "load everything" and any typos in the list were
    # silently ignored — the typo would only surface later if the
    # user removed ``all`` and got a different failure mode. v1.3
    # treats ``all`` as a loading instruction, not a validation
    # bypass: every supplied name must be a real module or group.
```

### server.py:752-752 | CUT | rule 2
tombstone: 'moved to gnucash_mcp/tools/_helpers.py'

```
# safe_tool, _json, _strip_noise moved to gnucash_mcp/tools/_helpers.py
```

### server.py:755-758 | CUT | rule 2
tombstone section: 'Core tools moved to…'

```
# ============== Tools ==============
# Core tools moved to gnucash_mcp/tools/core.py — every module now
# lives in its own file and registers lazily via _apply_module_filter.

```

### server.py:760-760 | CUT | rule 2
tombstone: 'Reconciliation/reporting/… tools moved'

```
# Reconciliation/reporting/budgets/scheduling/lots tools moved to gnucash_mcp/tools/<module>.py.
```

### server.py:763-766 | CUT | rule 2
tombstone: 'Admin tools … moved to gnucash_mcp/tools/admin.py'

```
# Admin tools (get_account_slots, set_account_slot, delete_account_slot,
# get_audit_log) moved to gnucash_mcp/tools/admin.py — registered on
# demand via register() when the 'admin' module is enabled.

```

### server.py:768-771 | CUT | rule 2
tombstone: 'Business tools (22) moved…' — count also stale

```
# ============== Business Tools ==============
# Business tools (22) moved to gnucash_mcp/tools/business.py — registered
# on demand when the 'business' module is enabled.

```

### server.py:784-784 | CUT | rule 2
tombstone: 'get_audit_log moved to…'

```
# get_audit_log moved to gnucash_mcp/tools/admin.py.
```

### server.py:813-830 | REWRITE | rule 4
keep the no-@audit_log self-documenting exception (the omission IS the contract); strip 'MP-1:' tag and 'Previously gated behind --debug; now…' history

```
# Register get_server_config unconditionally at import time so it
# survives _apply_module_filter's keep-set pass (Core's tool list
# includes it). Previously gated behind --debug; now always
# available as a diagnostic surface.
#
# MP-1: deliberately omits ``@audit_log``. Every other read tool
# carries the decorator, but this one is a zero-side-effect config
# inspection that the LLM calls reflexively as part of its
# orientation pass (see the MCP server instructions and
# ``get_book_summary``'s docstring referrals). Logging every
# get_server_config call adds noise without signal — there's no
# bookkeeping question the audit log answers about who looked at
# the module list — and would clutter the human-readable trail the
# bookkeeper depends on for forensic review of real activity.
# The exception is documented here rather than enforced by a
# contract test because the omission is the contract: a future
# contributor adding @audit_log to this tool should read this
# comment first and confirm they have a real reason to override.
```

### server.py:962-969 | FIX-FALSE | rule 6
'to back its 26 tools' — stale count (core surface is 29); also restate 'With the Core restructure… migrated' in present tense

```
    global _book_class
    # Expand each loaded module to its backing mixin set. With the
    # Core restructure (void/unvoid + admin tools + backups migrated
    # into Core), the mixin layer still owns those methods in their
    # original files — Core needs CoreMixin + ReconciliationMixin +
    # AdminMixin + BackupMixin composed together to back its 26
    # tools. ``MODULE_BACKED_BY`` provides the mapping; modules not
    # listed there default to 1:1 (the legacy convention).
```

### server.py:1015-1017 | CUT | rule 2
explains an absence by narrating history ('is now registered… so no per-run registration is needed here')

```
    # get_server_config is now registered unconditionally at module
    # import time (see above), so no per-run conditional registration
    # is needed here.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `114-121` KEEP — # ``investor`` bundles the two halves of the legacy / # ``investments`` module: ``tax_lots`` (cost-basis tracking)
- `165-172` KEEP — # Core sub-modules — each maps to the legacy tool-file / mixin / # name(s) that host its tools. summary/accounts/transactions/
- `181-185` KEEP — # ``portfolio`` (prices / commodities) and ``tax_lots`` (cost- / # basis tracking) are the two halves of what used to be the
- `188-198` KEEP — # ``freelancer`` (customer-facing invoicing) and / # ``business_complete`` (vendor + employee management, vendor
- `229-231` KEEP — # Void / unvoid live here — they're the audit-preserving / # erasure path for transactions, paired with delete_transaction
- `246-248` KEEP — # Auto-snapshot hook is always-on regardless; these expose / # the manual control surface for inspecting / pruning the
- `254-257` KEEP — # THE canonical accounting report — Assets, Liabilities, / # Equity reconciling to zero. Analytical reports (cash flow,
- `293-297` KEEP — # ``investments`` split into ``portfolio`` (the multi-currency / # primitive: commodities + prices) and ``investor`` (tax-lot
- `314-331` KEEP — # ``business`` split into ``freelancer`` (customer-facing / # invoicing — the natural surface for a solo consultant) and
- `352-356` KEEP — # Billterms — payment-terms registry shared by customer / # invoices and vendor bills. Lives here because every
- `359-365` KEEP — # Jobs — project-level grouping over invoices/bills for / # a single customer or vendor. Polymorphic on owner_type
- `372-375` KEEP — # Credit notes — refund/return documents. Polymorphic on / # owner_type. A freelancer issuing a customer refund
- `409-411` KEEP — # Employee expense vouchers (v1.3). Lifecycle (post / / # unpost / pay) flows through the polymorphic invoice
- `475-481` KEEP — # Phantom check is scoped to modules whose backing tool files / # aren't extracted (i.e., tools ship at server.py import time
- `537-541` KEEP — # Snapshot of which public module names are enabled in the current / # run. Populated by ``_apply_module_filter``; read by tool wrappers
- `600-603` KEEP — # Per-typo, suggest the closest known name (did-you-mean). / # Use simple shared-character ratio; close enough for the
- `633-635` KEEP — # ``core`` is always-on. Force-add before group expansion so / # the eight core sub-modules come along even when the user
- `638-640` KEEP — # Expand groups single-pass. Groups don't reference other / # groups (deliberate flat partition); if that changes,
- `650-652` KEEP — # Keep only known sub-module names. Group expansion above may / # have introduced names that aren't TOOL_MODULES keys if the
- `655-658` KEEP — # Build a group-aware display string for get_server_config. / # Groups render as ``group[member1, member2, ...]``; standalone
- `673-675` KEEP — # Expand each enabled module to its backing tool-files (per / # MODULE_BACKED_BY; default 1:1). Lazy-load any backing files
- `697-702` KEEP — # Snapshot the enabled set for tool wrappers that gate behavior / # on module availability (e.g., owner_type='vendor' on Freelancer
- `734-737` KEEP — # Initialize logging at module import time / # Use GNUCASH_MCP_DEBUG=1 env var to enable debug logging
- `958-961` KEEP — # Build a GnuCashBook class that includes only the mixins for enabled / # modules. If get_book() has already been called (tests / module
- `998-1002` KEEP — # Populate runtime state and conditionally register debug tool. / # ``modules_display`` was set by ``_apply_module_filter`` with

## src/gnucash_mcp/_format.py

### _format.py:1-17 | REWRITE | rule 5
module docstring: keep layer-neutral contract; cut 'Replaces ad-hoc str(decimal) calls' and 'Mirrors the contract previously baked into CoreMixin._truncation_notice' refactor history

```
"""Cross-layer formatting helpers.

These shape numeric and list output the same way regardless of who's
emitting them — the book layer producing display-ready dicts, the tool
layer wrapping responses, the audit log rendering text. Both layers
import from here; ``book/`` and ``tools/`` stay decoupled.

Two pieces:

- :func:`_format_number` — single chokepoint for currency / percentage
  / share-quantity rounding. Replaces ad-hoc ``str(decimal)`` calls
  that leaked 26-digit Decimal arithmetic into responses.
- :func:`_apply_limit` — generalized truncation + notice helper.
  Mirrors the contract previously baked into
  ``CoreMixin._truncation_notice`` but parameterized so any tool can
  plug in its own entity name.
"""
```

### _format.py:90-114 | REWRITE | rule 3
privacy rationale is the keeper; restate 'used to include the full absolute path' as a present-tense prohibition

```
def _book_display_name(book_path) -> str:
    """Render a book path as filename only — no directory leakage.

    Routine LLM-visible responses (``get_server_config``,
    ``get_book_summary``'s orientation line) used to include the full
    absolute path to the GnuCash book. That leaks the user's
    username, home directory layout, and any custom organization
    (``~/Finances/``, ``~/Documents/Books/``) into every transcript
    — a privacy concern for a tool that gets used on real personal
    financial data and a security concern for screenshots / shared
    sessions.

    The filename alone is sufficient to verify *which* book is
    loaded ("yes, this is Alex's book, not Lin Wei's"); the path
    components leading to it are the sensitive bit.

    This is an always-on redaction for the book path specifically,
    distinct from :func:`gnucash_mcp.logging_config.redact_paths`
    which is opt-in via ``GNUCASH_REDACT_PATHS=1`` and aimed at
    error-message paths (where the directory may be load-bearing
    debugging signal).

    Returns ``"not set"`` for falsy input so the orientation reads
    sensibly when ``GNUCASH_BOOK_PATH`` was never set.
    """
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `70-72` KEEP — # Not a number — pass through unchanged. Defensive: callers / # can hand us free-form strings (e.g. "N/A") for fields that

## src/gnucash_mcp/logging_config.py

### logging_config.py:100-104 | REWRITE | rule 2
cut 'matching pre-Stage-6 behavior' arc reference

```
        steady-state rate.

    Default (no env vars): no limiting — writes proceed at full
    speed, matching pre-Stage-6 behavior.
    """
```

### logging_config.py:1204-1207 | REWRITE | rule 2
keep the id/invoice_id alias fact; cut 'Plumb Bob bookkeeper-flagged' attribution

```
    # Plumb Bob bookkeeper-flagged: delete_invoice accepts ``id`` as
    # the preferred alias OR ``invoice_id`` for back-compat. Prefer
    # whichever the caller supplied; fall back to empty string so
    # the formatter never renders ``id:None``.
```

### logging_config.py:1405-1409 | REWRITE | rule 3
keep 'bills need their own label'; cut 'Pre-fix the (invoice, POST) handler fired for both'

```
def _fmt_bill_post(entry: dict) -> list[str]:
    """Bill POST — same shape as invoice POST but with the right
    label so the audit log doesn't mis-categorize a vendor bill
    as a customer invoice. Pre-fix the (invoice, POST) handler
    fired for both because ``post_invoice`` accepts either."""
```

### logging_config.py:1585-1590 | REWRITE | rule 2
Decimal-vs-string-'0' guardrail stays verbatim; cut '(Copilot PR #87 review.)'

```
    # ``apply_credit_note`` returns quantized strings like
    # "0.00", not "0", so a string-equality check against "0"
    # would print "remaining: 0.00" lines on fully-settled
    # documents. Decimal comparison handles every quantize
    # shape ("0", "0.00", "0.0000") uniformly.
    # (Copilot PR #87 review.)
```

### logging_config.py:1867-1873 | FIX-FALSE | rule 6
'APPLY is the netting tool added alongside this commit' — commit narration, false as a description of the present table

```
    # Credit-note CREATE / DELETE for the create-side surface;
    # POST / UNPOST / PAY are reached via the polymorphic
    # entity_type swap (the lifecycle tools register as
    # ``entity_type="invoice"`` and the audit-decorator's
    # polymorphism handler rewrites it when the response type
    # is "credit_note"). APPLY is the netting tool added
    # alongside this commit.
```

### logging_config.py:1915-1918 | CUT | rule 2/4
'R-3: _looks_like_guid_ref moved to book/_base.py' — tombstone with dead finding ID; the import tells the reader where it lives

```
# R-3: ``_looks_like_guid_ref`` moved to ``book/_base.py`` so it
# sits next to ``_resolve_account`` (the chokepoint it gates) and
# can be shared by any future display surface that wants to skip
# already-canonical path strings before opening a session.
```

### logging_config.py:1924-1939 | REWRITE | rule 4
keep config-vs-mechanics division of labor; strip 'R-3 split the responsibilities' framing

```
    """Audit-log-specific wrapper around
    :meth:`BaseGnuCashBook._normalize_account_refs`.

    R-3 split the responsibilities:

    - This function knows the audit-log-specific config — which
      param keys carry account refs always, which carry them only
      for specific ``(entity_type, operation)`` pairs.
    - The book layer owns the actual session-open + resolve + walk
      mechanics, alongside ``_resolve_account`` and the other
      chokepoints it depends on.

    Falls back to ``params`` unchanged when the book wrapper isn't
    available (server not fully wired up yet, or audit log fired
    during init) — log rendering still produces a useful line with
    raw refs.
```

### logging_config.py:2043-2052 | FIX-FALSE | rule 6
entity_type documented as 'transaction, account, or split' — stale enumeration (60+ pairs in the dispatch table)

```
def _extract_after_state(result: str, entity_type: str | None) -> dict | None:
    """Extract entity state from tool result JSON.

    Args:
        result: JSON string returned by tool
        entity_type: "transaction", "account", or "split"

    Returns:
        State dict with guid, or None.
    """
```

### logging_config.py:2079-2086 | FIX-FALSE | rule 6
audit_log docstring: same stale operation/entity_type enumerations

```
    """Decorator that logs tool calls to the audit log.

    Args:
        classification: "read" or "write"
        operation: For writes: "create", "update", "delete", "void", "unvoid",
                   "reconcile", "set_state"
        entity_type: "transaction", "account", or "split"
    """
```

### logging_config.py:2095-2101 | REWRITE | rule 4
strip '(Stage 6 #3)' tag; ordering rationale (before backup + pre-clear) stays

```
            # Write rate limit (Stage 6 #3). Default disabled —
            # enabled when the user sets GNUCASH_WRITE_RATE_LIMIT.
            # Checked BEFORE the auto-backup trigger and BEFORE
            # the pre-clear of staged audit state: a rate-limited
            # call hasn't started the tool, so it shouldn't
            # disturb the next call's audit-staging slot or
            # provoke a backup snapshot.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `196-198` KEEP — # Split on either separator so Windows paths matched on a / # POSIX-running host (where ``Path("C:\\...").name`` would
- `263-273` KEEP — # 0o020 = group-write, 0o002 = world-write. / # Reject regardless of sticky bit: the sticky bit
- `290-294` KEEP — # Defense in depth: if a ``.mcp`` already exists at the / # derived path, require it to be a real directory owned
- `367-371` KEEP — # Log directory lives alongside the book file by default / # (e.g., /path/to/finances.gnucash → /path/to/finances.gnucash.mcp/),
- `400-406` KEEP — # Restrict the audit file to owner read/write. Audit logs / # contain transaction descriptions, account paths, dollar
- `536-546` KEEP — # ── Audit text-format dispatcher ─────────────────────────────────── / #
- `602-604` KEEP — # update_transaction's response is thin (no description/date/splits / # echo). Resolve through after_state → params → before_state so
- `810-813` KEEP — # Show first 10 reconciled splits with description + amount if the / # before_state carried the per-split context. Prefix/full GUID
- `1010-1015` KEEP — # ── Job formatters ─────────────────────────────────────────── / # Jobs aren't business-persons (no currency/address fields), so
- `1045-1047` KEEP — # Show only the fields that changed (the book method returns / # only changed keys in after_state — anything missing means
- `1069-1071` KEEP — # force=True re-parented invoices to the underlying / # customer/vendor — call that out so a reviewer sees the
- `1107-1109` KEEP — # account_paths-resolved (preferred) → "account"; falls back / # to "account_guid" for entries serialized without the path
- `1111-1114` KEEP — # Trim to leaf name to match _taxtable_entry_summary's / # ``e.account.name`` output. A path "Liabilities:GST Payable"
- `1460-1466` KEEP — # ── Voucher formatters ─────────────────────────────────────── / # Vouchers (employee expense reimbursements) share the
- `1520-1526` KEEP — # ── Credit-note formatters ─────────────────────────────────── / # Credit notes can be customer- or vendor-sided (owner_type 2 or
- `1616-1618` KEEP — # owner_id key is owner-type-dependent: customer credit notes / # surface customer_id; vendor credit notes surface vendor_id.
- `1657-1660` KEEP — # Entry can belong to an invoice / bill / voucher / credit / # note — the params carry whichever ID key the tool wrapper
- `1717-1719` KEEP — # Show before/after per period. ``prior_amounts`` is keyed by / # period number; sort numerically so the human reader sees
- `1815-1819` KEEP — # ── Dispatch table ──────────────────────────────────────────────── / #
- `1904-1907` KEEP — # Keys whose values are account refs ONLY for specific (entity_type, / # operation) pairs. ``name`` is the canonical example: it's the leaf
- `1951-1953` KEEP — # Test-fixture / lightweight wrappers may not subclass / # ``BaseGnuCashBook``; degrade gracefully rather than crash
- `2003-2005` KEEP — # Substitute canonical fullnames in for any %short / full-GUID / # account refs the LLM passed. Non-destructive: the source entry
- `2125-2135` KEEP — # Defense-in-depth: clear any previously-staged audit / # before-state at the TOP of the wrapper. The post-call
- `2144-2147` KEEP — # Normalize up front so pydantic models (e.g. list[SplitInput] / # from the transaction-creating tools) become plain dicts before
- `2166-2170` KEEP — # Before the first write of each process, give the backup / # system a chance to snapshot. BackupMixin's own flag makes
- `2187-2190` KEEP — # Consume any before-state the book method staged while / # its session was open. Always consume (even on read /
- `2212-2224` KEEP — # Invoice/bill/voucher/credit_note / # polymorphism: post_invoice /
- `2260-2262` KEEP — # Drop any staged before-state so it can't leak into the / # next call. Failed writes don't render before_state in

## src/gnucash_mcp/book/__init__.py

### __init__.py:1-14 | REWRITE | rule 2
restate 'Public exports match what the old book.py exposed' as a present-tense compat contract

```
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
```

### __init__.py:28-31 | REWRITE | rule 2
'Every module lives in its own file now' — drop the 'now'

```
# Map of module name → (relative import path, mixin class name).
# Every module lives in its own file now; CoreMixin is always included
# by _apply_module_filter (core is never truly "disabled"), but it goes
# through the same registration path as every other module.
```

### __init__.py:54-61 | REWRITE | rule 2
'Now every module is extracted… Retained for API stability' — restate present tense

```
def extracted_modules() -> set[str]:
    """Return the set of modules that have their own mixin file.

    Now every module is extracted, so this equals the full TOOL_MODULES
    keyset. Retained for API stability — `_apply_module_filter` still
    consults it to decide which modules are lazy-loadable via
    gnucash_mcp.tools.<name>.register(mcp, get_book).
    """
```

## src/gnucash_mcp/book/_base.py

### _base.py:51-65 | REWRITE | rule 2
keep centralized-access rationale; cut 'Hoisted to _base from admin in v1.3' migration story

```
def _slot_value_str(value) -> str:
    """Stringify a piecash slot value to a stable ``str``.

    piecash returns either a typed wrapper with a ``.value``
    attribute (``SlotString``, ``SlotInt64``, etc.) or the raw
    value depending on slot type. Centralizing the access keeps
    every caller agreeing on the same extraction.

    Hoisted to ``_base`` from ``admin`` in v1.3 — the credit-note
    slot helpers in ``BusinessMixin`` need the same access pattern,
    and a sideways import from ``business`` into ``admin`` would
    add a coupling that doesn't reflect the dependency direction
    (admin is the slot-tool mixin; business is also a slot
    consumer; both should pull a shared utility from ``_base``).
    """
```

### _base.py:81-96 | REWRITE | rule 3
keep state-only semantics + chokepoint principle; compress the five-site pre-v1.3 bug list to one guardrail sentence

```
    Single source of truth for "is this split voided." Pre-v1.3
    release five iteration sites disagreed:

    - ``get_unreconciled_splits`` used ``state != "y"`` (admitting
      voided as "unreconciled")
    - ``get_book_summary``'s reconciliation backlog count: same bug
    - ``set_reconcile_state`` had no state guard (could move a
      voided split to ``"y"``, defeating ``unvoid_transaction``)
    - ``assign_split_to_lot`` had no state guard (let voided
      splits attach to lots)
    - ``_lot_decimals`` had no explicit filter (worked by
      coincidence because voided splits contribute 0 either side
      of its branch, but didn't document the intent or guard
      against the corruption case)

    Routing every site through this predicate enforces the
```

### _base.py:108-142 | REWRITE | rule 4
strip three 'HP-8' IDs; keep the two-surfaces-agree-by-construction invariant and the as-of-today scope paragraph intact

```
def _is_unreconciled(split) -> bool:
    """True iff ``split`` counts as pending reconciliation work.

    Single source of truth for "is this split unreconciled" — the
    same predicate ``get_unreconciled_splits`` (the detail tool)
    and ``_account_reconciliation_status`` (the dashboard count)
    both consult. Routing both sites through this helper enforces
    HP-8's convergence structurally instead of by docstring promise.

    ``state == "n"`` (new) and ``state == "c"`` (cleared) both
    count — cleared splits are not finalized; they're the
    bookkeeper's tentative state before a final ``"y"`` mark.
    ``state == "y"`` (reconciled) and voided zombies are excluded.

    Pre-HP-8 the dashboard count and the detail tool disagreed on
    pre-``latest_y_date`` unreconciled splits (the dashboard scoped
    its count to splits AFTER the most recent ``y``; the detail
    tool returned all non-y). The fix landed at both sites by
    hand-aligning the predicate; this helper chokepoints it so a
    future change can't recreate HP-8 in a new shape.

    Scope: this is the **as-of-today** predicate. The dashboard
    surface (``_account_reconciliation_status`` →
    ``get_book_summary``) is the morning-check view and
    intentionally has no historical-tie-out parameter. The detail
    tool (``get_unreconciled_splits``) accepts a separate
    ``as_of_date`` arg that filters out post-``as_of`` splits — a
    different scoping concern layered ON TOP of this predicate, not
    a reason to thread date into the chokepoint. Counts at the two
    surfaces agree by construction when ``as_of_date`` is unset;
    they're expected to diverge when it's used, and that's the
    detail tool's contract — the morning-check view doesn't reach
    into history.
    """
    return split.reconcile_state != "y" and not _is_voided(split)
```

### _base.py:245-259 | REWRITE | rule 2
keep shape-matching note; cut 'The previous signature hardcoded FROM slots…' history

```
def _verify_delete(
    session, table, conditions: dict, label: str
) -> None:
    """Verify a SQL DELETE removed the expected row(s).

    Must be called within the same session, before book.save().
    Raises RuntimeError if matching rows still exist.

    Shape-matches ``_verify_composite_write``: the ``table`` argument
    is a SQLAlchemy Core Table (``Entity.__table__``). The previous
    signature hardcoded ``FROM slots`` via raw SQL, which kept this
    helper scoped to slot deletes; generalizing to any table lets us
    pair deletes-with-verification for Entry / Invoice / Customer /
    Vendor cleanup too.
    """
```

### _base.py:429-443 | FIX-FALSE | rule 6
line 429 is a stale duplicate header sentence left above its own rewrite; also strip '(the bookkeeper's testing covers the English-default case)'

```
# Mapping of top-level parent to "obvious" account types that need no annotation
# Top-level account names paired with their default types — used by
# ``_account_to_compact_line`` to suppress ``[TYPE]`` annotations
# when the type is implied by the conventional GnuCash hierarchy
# (``Assets:Checking [ASSET]`` reads as redundant noise; the
# annotation only fires when the type DEPARTS from the convention,
# e.g. ``Assets:Old Loan [LIABILITY]``).
#
# **Localization note:** keys are GnuCash's English defaults. Books
# created with non-English chart-of-accounts templates ("Activos",
# "Activités", "資産", etc.) won't match — every account in those
# books gets the redundant ``[ASSET]`` annotation. Acceptable for
# now (the bookkeeper's testing covers the English-default case);
# a future localization pass would add lookup-by-account-type
# instead of by-name.
```

### _base.py:487-497 | REWRITE | rule 2
keep prefix-map contract + fallback; cut 'v1.3.1 bookkeeper feedback drove the prefix path…' provenance

```

    When ``split_prefixes`` / ``lot_prefixes`` are provided, the
    emitted ``guid`` and ``lot_guid`` fields are truncated to
    collision-safe short forms (typically 8 chars) via lookup in
    the prefix maps. When omitted, falls back to full 32-char
    GUIDs — preserved for any caller that hasn't migrated to the
    prefix-aware path. v1.3.1 bookkeeper feedback drove the
    prefix path on read tools (get_transaction, verbose
    list_transactions) where the full GUIDs were dead-weight
    tokens the LLM never used.
    """
```

### _base.py:587-588 | REWRITE | rule 4
strip '(A9)' tag

```
        # Null post_date is a legal old-book artifact (A9); render
        # as None rather than crashing the whole listing.
```

### _base.py:735-741 | CUT | rule 3
'History note: earlier prereleases used an exclude_account variant…' — register-form rationale above already carries the design; no plausible reintroduction

```
    History note: earlier prereleases of this server used an
    ``exclude_account`` variant of this function that stripped the
    filtered split silently, which made the filtered output look like
    it was missing the description (when really the description had
    shifted into a column the reader was parsing as splits). Register
    form fixes that ambiguity structurally and makes the filtered
    account's amount first-class — the field readers care about most.
```

### _base.py:752-752 | REWRITE | rule 4
strip '(A9)' tag from null-post_date note

```
    # Null post_date is a legal old-book artifact (A9).
```

### _base.py:872-886 | REWRITE | rule 3
keep allowlist-implicit-in-dict rationale; restate 'pre-fix the query was built as f-string' and 'Coverage extended to…' present-tense

```
    # Tables that support GUID resolution. Each entry maps table
    # name → its prefix-lookup SQL. The dispatch dict eliminates
    # the f-string interpolation in ``_resolve_guid`` — pre-fix
    # the query was built as ``f"SELECT guid FROM {table} ..."``,
    # safe via the ``_GUID_TABLES`` allowlist but a fragile pattern
    # if a future contributor added a table without re-validating.
    # Storing the full statement per-table makes the validation
    # implicit (no entry → no lookup) and gives each table room to
    # diverge if its schema warrants it.
    #
    # Coverage extended to ``prices`` and ``entries`` (both have
    # ``guid`` columns and may surface as short prefixes from any
    # tool that emits them). ``slots`` is intentionally absent —
    # slots have no primary GUID; they're keyed by ``obj_guid``
    # (the parent entity) plus name.
```

### _base.py:1219-1226 | REWRITE | rule 2
keep purpose; cut 'Pre-v1.3.1 these surfaces emitted full 32-char GUIDs — identified by the bookkeeper as token bloat'

```

        Same mtime-keyed pattern as ``_transaction_prefix_map``.
        Used by ``get_transaction`` and verbose ``list_transactions``
        to emit collision-safe short split GUIDs in responses. Pre-
        v1.3.1 these surfaces emitted full 32-char split GUIDs —
        24 wasted chars per split per call, identified by the
        bookkeeper as token bloat in real LLM workflows.
        """
```

### _base.py:1283-1291 | REWRITE | rule 3
compress 'pre-v1.3 release the template check was only applied on the path branch' to a one-line uniform-filter guardrail (inline comment at 1320-1324 already carries the keep-rationale)

```
        Template-filter chokepoint: pre-v1.3 release the template check
        was only applied on the path branch (via :meth:`_find_account`).
        ``%short`` and full-GUID input bypassed it, so the same logical
        account resolved to two different values depending on input
        form — letting ``update_account`` / ``move_account`` /
        ``delete_account`` silently mutate template-tree rows when
        called with a non-path ref. The post-dispatch check below
        applies the filter uniformly regardless of input shape.
        """
```

### _base.py:1343-1352 | REWRITE | rule 4
strip 'R-3:' and 'Pre-fix this lived as _normalize_account_refs_for_audit inside logging_config.py'; keep mechanics-vs-config division

```
        R-3: chokepoint for the account-ref-to-fullname rewrite
        pattern. Pre-fix this lived as
        ``_normalize_account_refs_for_audit`` inside
        ``logging_config.py`` and opened its own book session per
        audit-log render via ``_get_book_func()``. Moving the book
        mechanics into BaseGnuCashBook puts the work next to
        ``_resolve_account`` — the other chokepoint it relies on —
        and lets future display surfaces route through the same
        primitive without re-deriving the session-open + walk-splits
        + resolve loop.
```

### _base.py:1593-1606 | REWRITE | rule 4
strip '(C8 read-side)' and '(old-book artifact, A9)' tags; the three rules stay verbatim

```
        The one rule every own-splits sum shares (C8 read-side):

        - voided splits are excluded by **state**, not value. A
          well-formed void contributes 0 either way; the corrupted
          partial-void shape (``state='v'`` with non-zero values,
          producible by legacy data or desktop edits) must not move
          balances when the same split is invisible to cash_flow /
          lots / reconciliation counts.
        - ``as_of`` (inclusive) caps to posted-by-then transactions;
          ``None`` means no date bound — callers that intentionally
          include future-dated transactions pass nothing.
        - null ``post_date`` rows (old-book artifact, A9) are
          excluded — same rule ``_query_filtered_splits`` applies,
          so this sum agrees with the SQL-backed reports.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `27-30` KEEP — # Re-exported for callers that still import these from ``_base``. / # Canonical definitions live in ``_currency`` alongside the
- `38-41` KEEP — # GnuCash stores GUIDs as lowercase hex (via uuid4().hex). We accept both / # cases on input for ergonomics — users pasting from external tools may
- `278-296` KEEP — # ── GUID prefix protection ──────────────────────────────────────── / #
- `657-662` KEEP — # Split-list collapse threshold: transactions with more than this many / # splits (in the column that would be rendered) get truncated to
- `912-918` KEEP — # Resolve to an absolute path with ``..`` segments collapsed. / # The audit / debug / backups directories are derived from
- `927-931` KEEP — # Thread-local staging buffer for audit-log before_state. / # Write book methods call `_stage_audit_before(...)` while their
- `933-941` KEEP — # Cache for the full-table transaction prefix map. Three call / # sites (``list_transactions``, ``search_transactions``,
- `943-946` KEEP — # Same cache shape for split and lot GUID prefix maps — used / # by get_transaction and verbose list_transactions to emit
- `1126-1142` KEEP — # ── Short account GUIDs ─────────────────────────────────────────── / #
- `1302-1304` KEEP — # No-match on a well-formed prefix degrades to None, / # mirroring _find_account's contract. Validation errors
- `1320-1324` KEEP — # ── Template-filter chokepoint ── / # The path branch's _find_account already filters templates,
- `1395-1397` KEEP — # Defer the import to dodge the audit-log surfaces that / # don't always have ``logging_config`` already loaded
- `1408-1412` KEEP — # Stale, ambiguous, malformed — leave the / # raw ref in place; the log line is still
- `1431-1433` KEEP — # Pass 3: rewrite. Non-destructive — return a new dict so / # any prior log line that already captured the original
- `1470-1472` KEEP — # Indexed SQL lookup via SQLAlchemy — the `guid` column is the / # primary key on the transactions table. Replaces an O(N) Python

## src/gnucash_mcp/book/_query.py

### _query.py:12-17 | REWRITE | rule 2
placement rationale stays; cut '(where it originated)'

```
The function lives here rather than on ``ReportingMixin`` (where it
originated) because budgets needs it too — and any future module
that wants date-range-filtered splits should reach for the same
primitive rather than rolling its own Python-side
``for txn in book.transactions: if date_match`` loop.
"""
```

### _query.py:41-50 | REWRITE | rule 3
keep the don't-loop / don't-SUM-in-SQL guardrails; restate 'replacing the … pattern that used to touch every row' present-tense

```
        SQLite backing store — one query returns exactly the rows the
        caller needs, replacing the Python-side
        ``for txn in book.transactions: if date_match`` pattern that
        used to touch every row in the book regardless of relevance.

        The query yields ORM objects (not raw num/denom pairs) so
        callers can aggregate ``split.quantity`` / ``split.value`` as
        exact ``Decimal`` values in Python. Aggregating in SQL via
        ``SUM(num * 1.0 / denom)`` would collapse to IEEE-754 floats,
        which is unacceptable for financial arithmetic.
```

### _query.py:89-97 | REWRITE | rule 4
strip 'HP-12' tag; dormant-defense rationale stays

```
        # HP-12 defense-in-depth: exclude template-subtree accounts.
        # Currently dormant — ``Transaction.post_date.isnot(None)``
        # above already filters SX templates (their splits live on
        # transactions with null post_date). But making the account-
        # level exclusion explicit closes a latent path where a
        # future codepath posts to a template account, and matches
        # the convention applied at every other report iteration
        # site that filters templates via
        # ``_template_account_guids``.
```

### _query.py:104-124 | REWRITE | rule 2
top-tier piecash _DateAsDateTime knowledge — keep all of it; cut only 'Bookkeeper-flagged on Lin Wei's book during Branch 1 validation' and 'Copilot-flagged on PR #95'

```
            # piecash's ``_DateAsDateTime`` TypeDecorator stores
            # ``post_date`` as a DateTime with a 10:59:00
            # neutral-time component (see
            # ``piecash.sa_extra._DateAsDateTime.process_bind_param``).
            # A bare-date upper bound coerces to midnight in the SQL
            # comparison, so ``post_date <= as_of`` would exclude
            # same-day transactions whose stored time is 10:59 —
            # ``balance_sheet(2025-12-31)`` returning a balance that
            # excluded December 31 activity, while ``get_balance``
            # (which compares Python-side, post-``process_result_value``,
            # where the time has already been stripped) showed the
            # correct number. Bookkeeper-flagged on Lin Wei's book
            # during Branch 1 validation. Using the day after as a
            # strict upper bound includes the full as_of date
            # regardless of stored time component.
            #
            # ``end_date == date.max`` is treated as "no upper bound"
            # — ``date.max + timedelta(days=1)`` overflows. A caller
            # passing ``date.max`` semantically wants every row,
            # which is what dropping the filter does. Copilot-flagged
            # on PR #95.
```

## src/gnucash_mcp/book/_currency.py

### _currency.py:23-32 | FIX-FALSE | rule 6
'the v1.3 performance sweep replaces those walks' — stale future-work narration; also _is_market_price is defined HERE, not in _base (pointer is misleading)

```
All helpers skip piecash's auto-created ``type='transaction'`` price
placeholders via :func:`gnucash_mcp.book._base._is_market_price` —
those rows capture the effective rate of one specific cross-currency
transaction and would shadow real user-supplied market quotes.

The indexed query primitive :meth:`CurrencyMixin._find_prices` is
provided here for future call-site migration; the rate-collecting
helpers above still walk ``book.prices`` directly today (the v1.3
performance sweep replaces those walks).
"""
```

### _currency.py:41-60 | REWRITE | rule 2/3
keep cap semantics + unreachable-error guardrail; strip '(Plumb Bob validation, 2026-06-04)' and reword '(pre-fix behavior)'

```
# ── FX staleness cap (Plumb Bob validation, 2026-06-04) ───────────
#
# Pre-fix ``_find_exchange_rate`` would happily use the temporally-
# closest price regardless of distance from ``as_of`` — a 2027
# invoice could silently use a 2026 rate, a 2020 invoice could
# silently use a 2025 rate. The error message promised a price "on
# or near DATE" but the function had no proximity bound, so the
# error was effectively unreachable for any currency with at least
# one price on file.
#
# The cap below filters candidates to ``|days_offset| <=
# _FX_STALENESS_DAYS``. When no price within the window exists,
# the function returns ``None`` and the caller's existing
# "Add a price with create_price, then retry" error fires correctly
# (now with a real chance to fire).
#
# Default 90 days matches a typical bookkeeping cadence (monthly
# statement close + a grace period). The
# ``GNUCASH_FX_STALENESS_DAYS`` env var overrides it; ``0`` or
# negative disables the cap entirely (pre-fix behavior).
```

### _currency.py:203-224 | REWRITE | rule 2
keep events-vs-revaluations convention + TestCrossToolPriceAgreement pointer (verified); cut 'Pre-v1.3 release the convention was implicit…' tail

```
    @staticmethod
    def _anchor_for_as_of(as_of: date) -> date:
        """Translate a report's ``as_of`` into the date used for
        market-price filtering.

        The convention (locked by the cross-tool agreement test in
        ``TestCrossToolPriceAgreement``): future-dated TRANSACTIONS
        are excluded from "now" balances (events haven't happened
        yet) but future-dated PRICES are INCLUDED in "now"
        valuations — they're intentional forecasts the bookkeeper
        wrote, the most authoritative rate they have on file. So
        any anchor at or beyond today folds to ``date.max`` so
        every forecast is in scope; past anchors stay literal for
        historical reconstruction.

        Every report-level caller of ``_account_conversion_factors``
        and ``_rates_as_of`` runs its ``as_of`` through this helper
        first so the convention is enforced exactly once. Pre-v1.3
        release the convention was implicit in the ``as_of=None``
        default; now that the default is gone, the helper is the
        explicit home for it.
        """
```

### _currency.py:244-267 | REWRITE | rule 3/4
strip '(issue #94)' label; compress '**Required** — pre-v1.3 release this defaulted to None… Five historical-report sites…' to the no-default guardrail

```
        **Intermediate-currency chaining (issue #94).** A commodity
        with no price *directly* in the default currency, but
        reachable through an intermediate, is resolved via
        :meth:`_market_rate_to_default` (direct → inverse → single
        pivot, then a security-priced-in-foreign-currency outer hop).
        This covers a fund priced in USD inside an AED book
        (fund→USD→AED), a foreign-cash balance whose pair is only
        quoted through a vehicle currency (GBP→USD→AED), and the
        3-hop composition. Every leg reuses the market-price filter,
        so ``type='transaction'`` auto-placeholders never pollute a
        chained rate. Commodities with no resolvable path stay absent
        (caller falls back to cost basis).

        Args:
            book: Open piecash book.
            as_of: Upper bound on the price date. **Required** — pre-
                v1.3 release this defaulted to ``None`` (no upper
                bound, i.e. always-latest rates). Five historical-
                report sites passed nothing and silently used today's
                rates regardless of report date; the default has been
                dropped so every caller must declare its intent. Pass
                the report's as_of / end_date; the
                ``_anchor_for_as_of`` helper handles the "include
                future forecasts at now-or-future anchors" convention.
```

### _currency.py:294-309 | REWRITE | rule 4
strip 'Issue #94:' and '(C7)'; chain-pass and past-anchor rationale stay

```
        # Issue #94: chain pass. For every commodity referenced by a
        # market price that the direct pass above couldn't rate, try
        # to reach the default currency through an intermediate. Only
        # commodities with at least one market price are candidates —
        # one with no price at all has no leg to chain and stays on
        # cost basis. Each resolution memoizes nothing here; the
        # per-commodity cost is a few indexed price walks, run only
        # for the non-direct minority.
        # Past anchors forbid after-anchor fallbacks in the chain
        # legs (C7): the direct pass above hard-filters future
        # prices for historical reports, and a chained commodity
        # must honor the same convention — not value a 2025-06-30
        # sheet at a rate first quoted in September. Now/future
        # anchors fold to date.max, where every price is "before"
        # and the flag is moot (forecasts included by convention).
        allow_after = anchor >= date.today()
```

### _currency.py:464-483 | REWRITE | rule 4
strip '(issue #94)' from the chaining description (self-contained without it)

```
        """Market rate converting one unit of ``commodity`` to the
        book default currency, with the intermediate path, chaining
        when there is no direct price (issue #94).

        Resolution order:

        1. :meth:`_cross_rate_with_path` ``commodity → default`` —
           handles a direct/inverse default-currency price, a currency
           that triangulates through a pivot (case B), and a security
           whose quote currency *is* a pivot leg (case A).
        2. Security-outer fallback: for the newest market price of
           ``commodity`` in some quote currency ``X`` that itself
           reaches the default, return ``price(commodity in X) ×
           rate(X → default)`` with path ``[X] + rest`` — the 3-hop
           case C (fund priced in GBP, GBP only reachable via USD).

        Returns ``(rate, intermediates)`` or ``None`` when no path
        exists (caller keeps cost basis). ``intermediates`` is ``[]``
        only for a direct default-currency price.
        """
```

### _currency.py:495-498 | REWRITE | rule 4
strip '(C7)' from the outer-hop anchor note

```
            # Newest-first list with no date bound; the outer hop
            # honors the same anchor convention as the legs (C7) —
            # past anchors never price off a future quote.
            if not allow_after and _to_date(p.date) > as_of:
```

### _currency.py:559-565 | REWRITE | rule 4
strip '(C7 cosmetic relative)'; keep the provenance-must-match-rate-path warning

```
        # Fold the anchor exactly as ``_rates_as_of`` does, and apply
        # the same past-anchor allow_after rule — otherwise the
        # provenance pass can resolve a DIFFERENT path than the one
        # that produced the rate and the "(via …)" note lies (C7
        # cosmetic relative).
        anchor = self._anchor_for_as_of(as_of)
        allow_after = anchor >= date.today()
```

### _currency.py:600-604 | REWRITE | rule 3
compress 'pre-v1.3 release this took only book and silently fetched today's rates' to the no-default guardrail

```
        ``as_of`` is required: pre-v1.3 release this took only
        ``book`` and silently fetched today's rates regardless of the
        caller's report date. Every caller now declares the date its
        valuation is anchored to — historical reports use historical
        rates, "now" helpers pass ``date.today()`` explicitly.
```

### _currency.py:749-771 | REWRITE | rule 2/4
keep three-band staleness model + allow_after semantics; strip '(Plumb Bob, 2026-06-04)', 'restores pre-fix behavior', '(issue #94)', '(C7)'

```
        **Staleness cap (Plumb Bob, 2026-06-04):** candidates more
        than ``GNUCASH_FX_STALENESS_DAYS`` (default 90) from
        ``as_of`` are excluded. Pre-fix the function would happily
        return a 5-year-old rate on a 2027 invoice; the documented
        "on or near DATE" promise was effectively unreachable.
        Setting the env var to ``0`` or a negative value disables
        the cap (restores pre-fix behavior). ``respect_staleness_cap=
        False`` disables it per-call — used by the valuation chain
        (issue #94), which must value a holding at its latest
        available rate regardless of age (matching the cap-free
        direct path in ``_rates_as_of``); the separate stale-price
        warning, not a hard cap, is what flags age for reporting. The
        cap stays on for invoice posting, where a stale rate is etched
        and must be refused.

        ``allow_after=False`` drops preference 3 and 4 entirely —
        no price dated after ``as_of`` is ever considered. Used by
        the valuation chain for PAST anchors (C7): the direct pass in
        ``_rates_as_of`` hard-filters future prices for historical
        reports, and the chain legs must honor the same convention or
        a commodity first priced after the report date silently
        values at that future rate while its directly-priced sibling
        falls back to cost basis.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `76-78` KEEP — # Malformed value falls back to the default. We don't / # want a startup typo to silently disable the cap (which
- `82-101` KEEP — # ── FX freshness guard (stale-rate guard on post/pay) ───────────── / #
- `313-318` KEEP — # Use the future-folded ``anchor`` (not raw ``as_of``) so a / # chained commodity applies the same "now/future anchors
- `401-403` KEEP — # Valuation chain: legs ignore the FX staleness cap so a / # holding values at its latest available rate (matching the

## src/gnucash_mcp/book/admin.py

### admin.py:12-25 | REWRITE | rule 3
sub-slot trap is a real guardrail; restate 'Pre-fix a key like credit/limit silently created' as a conditional present

```
# Slot keys with embedded ``/`` create hierarchical sub-slots in
# GnuCash's KVP store rather than flat keys. The MCP-facing
# account-slot tools only manage flat keys (``apr``,
# ``credit_limit``, ``minimum_payment``, etc.), so we restrict
# user input to a safe alphabet up-front. Pre-fix a key like
# ``credit/limit`` silently created a sub-slot under ``credit`` —
# invisible to ``get_account_slots`` keyed lookups.
#
# Note: internal slot keys (set by our own book methods, not
# accepted from users) can and do use ``/`` for namespacing —
# see the ``gnc-mcp/...`` convention in
# ``BusinessMixin._APPLIES_TO_SLOT_KEY``. This regex gates
# USER input only.
_SLOT_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
```

### admin.py:27-34 | REWRITE | rule 4
cap rationale stays; cut 'Pre-fix slot values were unbounded; HP-9 from specs/CODE_REVIEW_v1_3.md' (file no longer exists at that path)

```
# Upper bound on slot value length. 64 KiB is generous for any
# legitimate per-account metadata (APR strings, credit limits,
# statement-close-day, structured JSON config blobs) — well past
# what real bookkeeping needs but short enough that a malicious
# or runaway caller can't exhaust the book file with a single
# slot write. Pre-fix slot values were unbounded; HP-9 from
# specs/CODE_REVIEW_v1_3.md.
_SLOT_VALUE_MAX_BYTES = 64 * 1024
```

### admin.py:109-114 | REWRITE | rule 4
strip 'HP-9' tag; byte-count rationale stays

```
        # HP-9 length cap. Encode to UTF-8 to count bytes (so a
        # multi-byte unicode payload can't sneak past a char-count
        # check). 64 KiB is generous for any real per-account
        # metadata. Compute the byte length once; reusing
        # ``value.encode(...)`` would allocate a fresh copy of the
        # already-large string.
```

### admin.py:155-159 | REWRITE | rule 4
keep internal-namespaced-slot protection; cut 'Pre-fix delete skipped the validator… HP-11 from specs/CODE_REVIEW_v1_3.md' (dead pointer)

```
        # Same regex gate as ``set_account_slot``. Pre-fix delete
        # skipped the validator, so a user could target internal
        # namespaced slots (``gnc-mcp/applies-to-invoice``, etc.)
        # that the credit-note linkage and other internal features
        # depend on. HP-11 from specs/CODE_REVIEW_v1_3.md.
```

## src/gnucash_mcp/book/backup.py

### backup.py:111-121 | REWRITE | rule 3
same-second-collision + SQLite-truncates trap stays; drop 'Pre-fix' framing

```
def _format_ts(ts: datetime) -> str:
    """Format a UTC timestamp for filenames (colons stripped).

    Includes microseconds. Pre-fix, second-resolution timestamps meant
    two ``create_backup`` calls within the same second produced the
    same filename — and SQLite's ``connection.backup(dest_conn)``
    truncates an existing dest, so the second snapshot silently
    overwrote the first. Microsecond resolution makes collisions
    practically impossible; the explicit ``Path.exists()`` check in
    ``create_backup`` is the second line of defense.
    """
```

### backup.py:134-141 | REWRITE | rule 3
round-not-floor rationale stays; compress 'pre-fix a 59.9-minute age displayed as…'

```
def _describe_age(ts: datetime, reference: datetime | None = None) -> str:
    """Human-readable age string for listings — 'just now', '3 days ago', etc.

    Rounds to nearest unit rather than floor — pre-fix a 59.9-minute
    age displayed as "59 minutes ago" (one unit short of the next
    boundary). Round-half-up makes the boundary cases honest:
    59m30s reads as "60 minutes ago" → which then promotes to "1
    hour ago" via the next-bucket check.
```

### backup.py:346-358 | REWRITE | rule 4
strip '(MP-4)'; the redacted-path-unlink trap is the keeper

```
    def _resolve_backup_path(self, entry: dict) -> Path:
        """Absolute on-disk path for a backup listing entry.

        Pruning must never trust ``entry["path"]`` directly: under
        ``GNUCASH_REDACT_PATHS=1`` ``list_backups`` redacts that field
        to the bare basename (MP-4), so ``Path(entry["path"]).unlink()``
        would resolve against the process CWD — a silent no-op, or a
        delete of an unrelated same-named file there. Reconstruct from
        the backups dir + the filename so a prune only ever deletes a
        file that actually lives in the backups directory, redaction on
        or off. (``list_backups`` lists only direct children of the
        backups dir, so the basename round-trips cleanly.)
        """
```

### backup.py:435-443 | REWRITE | rule 3
partial-file-on-disk trap stays; compress 'Pre-fix the try/finally only closed the connection'

```
                # Disk-full (or any other) failure mid-copy leaves
                # a partial/empty file at backup_path. Pre-fix the
                # try/finally only closed the connection — the
                # truncated file stayed on disk and would surface
                # in ``list_backups`` as a "valid backup" until the
                # next ``PRAGMA integrity_check`` (which only runs
                # in the success path). Best to fail loud: close
                # the connection, unlink the partial file, and
                # propagate the original exception.
```

### backup.py:478-488 | REWRITE | rule 4
strip 'MP-4:' and 'L-5:' tags; redaction + shlex rationale stay

```
        # MP-4: route path-bearing fields through ``redact_paths``
        # (imported at module top). Pass-through unless
        # ``GNUCASH_REDACT_PATHS=1``; when set, paths collapse to
        # basename so responses are safe to share externally
        # without leaking filesystem layout.

        # L-5: shell-quote interpolated paths. Paths with spaces
        # or shell metachars would otherwise break the command —
        # and if a future code path ever passes a user-influenced
        # path component, an unquoted f-string is a latent
        # injection.
```

### backup.py:528-532 | REWRITE | rule 3
broken-symlink visibility rationale stays; compress 'Pre-fix this was silently dropped'

```
                # or deleted). Pre-fix this was silently dropped —
                # ``list_backups`` showed N-1 entries and
                # ``prune_backups`` would never clean the broken
                # link. Logging surfaces the issue at the next
                # debug-log inspection without breaking the listing.
```

### backup.py:543-543 | REWRITE | rule 4
strip 'MP-4:' tag

```
                # MP-4: opt-in path redaction.
```

### backup.py:614-625 | REWRITE | rule 4
strip 'MP-3:' tag; symmetric footgun guard rationale stays verbatim

```
        # MP-3: symmetric footgun guard for the auto stages.
        # ``prune_backups(keep_last_n=0)`` without an explicit
        # ``stage`` deletes every session / weekly / monthly
        # backup in one call. The user typed it intending "free up
        # disk space"; the intent was almost certainly per-stage,
        # not "wipe all auto backups." Auto backups rebuild over
        # time (sessions on next write, weekly on next Monday,
        # monthly on next 1st), but until then the recoverability
        # window is gone — and the user has no way to recover
        # backups they didn't realize they were deleting. Mirror
        # the manual-stage guard: require the caller to opt in
        # explicitly by naming the stage.
```

### backup.py:670-677 | REWRITE | rule 3
two-pass stable sort rationale stays; cut 'Pre-fix it was just timestamp-desc'

```
        # Sort would_keep by stage-then-timestamp-desc so a multi-
        # stage prune groups sessions/weeklies/monthlies together
        # (newest-first within each stage). Pre-fix it was just
        # timestamp-desc, which interleaved stages — readable for
        # single-stage prunes, awkward for the ``stage=None`` (all
        # auto stages) case. Two-pass stable sort: timestamp-desc
        # first so the within-stage order is newest-first, then
        # by stage to group.
```

### backup.py:779-785 | REWRITE | rule 3
persist-the-failure guardrail stays; compress 'pre-fix, OSError-on-disk-full was silently swallowed for weeks'

```
        except Exception as e:
            # Failure path: the user's write is still allowed to
            # proceed, but the bookkeeper needs to find out — pre-fix,
            # OSError-on-disk-full was silently swallowed for weeks.
            # We persist the failure so get_book_summary's Warnings
            # section can surface it on the next read.
            debug_logger.warning(f"Auto-backup skipped: {e}")
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `61-64` KEEP — # Order matters: higher-priority first. When multiple stages are due / # at the same time, the resulting backup file is tagged with the
- `78-84` KEEP — # Filename schema: / #   {book_stem}-{YYYYmmddTHHMMSSffffff}-{stage}[-{label}].gnucash
- `217-224` KEEP — # ── Auto-backup attempt status (separate file) ─────────────────────── / #
- `317-319` KEEP — # Flag toggled on first auto-backup attempt per process lifecycle. / # Prevents stat + JSON read on every subsequent write once we've
- `322-328` KEEP — # Serialize the read+write of ``_backup_checked_in_process`` across / # threads. Without this, two simultaneous "first writes" of the
- `413-417` KEEP — # Defense in depth against filename collisions. The microsecond / # resolution in ``_format_ts`` makes this practically
- `425-428` KEEP — # Perform the SQLite online backup. We open the source via / # piecash's readonly context (no write lock on the live book)
- `451-453` KEEP — # Idempotent: safe to call after the explicit close / # in the except branch — sqlite3.connection.close()
- `456-459` KEEP — # Verify the backup with PRAGMA integrity_check before / # declaring success. If the check fails, delete the bad file
- `583-598` KEEP — # Catastrophic-footgun guard. ``keep_last_n=0`` on the / # manual stage with ``dry_run=False`` deletes every
- `660-662` KEEP — # Stage not targeted: all of its backups are retained / # (but not reported in "would_keep" either — the
- `724-728` KEEP — # Serialize the gate so concurrent first-writes can't both / # pass the check before either flips the flag. Without the
- `732-734` KEEP — # Flag BEFORE running so a raise here won't cause the / # audit hook to retry on every subsequent write of the
- `742-744` KEEP — # Identify due stages. Process in priority order (monthly / # > weekly > session) so the first "due" stage becomes the
- `752-754` KEEP — # Nothing was due, so this isn't an "attempt" we need / # to record — skipping is the expected outcome when
- `757-759` KEEP — # Take ONE backup tagged with the highest-priority due / # stage. Advance every due stage's timestamp so multiple
- `770-772` KEEP — # Record success so get_book_summary can surface a green / # "auto-backup ran N minutes ago" signal — and so a later

## src/gnucash_mcp/book/budgets.py

### budgets.py:87-104 | REWRITE | rule 4
strip 'Layout per Phase 5B'; the format example carries the contract

```
def _format_budget_report_compact(report: dict) -> str:
    """Render a budget-report dict as a compact text table.

    Layout per Phase 5B::

        2026 Annual Budget — Period 3 (Apr 2026)
        Account                          Budget   Actual  Remaining  %Used
        Auto:Fuel                           250   199.61      50.39  79.8%
        Business:Contractor Payments      6,200 6,128.00      72.00  98.8%
        Groceries                           450   608.57    -158.57  135.2% ⚠
        Medical                             200 1,488.03  -1,288.03  744.0% ⚠
        TOTAL                             7,971 9,022.90  -1,051.90  113.2% ⚠

    ``⚠`` markers fire on rows where ``percent_used > 110%`` — same
    threshold ``get_book_summary`` uses for the budget headline.
    Strips a common ``Expenses:`` / ``Income:`` prefix from leaf
    names to keep the column readable.
    """
```

### budgets.py:492-495 | CUT | rule 2
'Bookkeeper-flagged after PR #98: …' — the use-case sentence above it already explains why start_date exists

```
                Bookkeeper-flagged after PR #98: "no way to create
                a budget that begins in the past blocks comparing
                a freshly-authored budget against historical
                actuals."
```

### budgets.py:796-803 | REWRITE | rule 3/4
multi-currency targets guardrail stays; strip '(SB-6)' and compress 'Pre-fix factors were built only for actuals'

```
            # Pre-compute conversion factors so both targets (this
            # loop) and actuals (the loop further down) value at the
            # same period-end anchor. Pre-fix factors were built
            # only for actuals — budget *targets* were summed raw
            # in their stored account commodity, so ``used_pct`` was
            # meaningless on multi-currency budgets where the actuals
            # were in the book's default currency but the targets
            # weren't (SB-6).
```

### budgets.py:845-854 | REWRITE | rule 3/4
depth-by-colon-count rationale stays; strip '(HP-7)' and compress 'Pre-fix used len(name)'

```
                for desc in descendants:
                    if desc.fullname in budgeted:
                        continue  # separately budgeted — don't roll up
                    # If multiple budgeted ancestors cover this descendant
                    # (nested parents), keep the nearest one (the deepest
                    # budgeted ancestor). Pre-fix used ``len(name)`` as a
                    # depth proxy — broke under pathological naming where
                    # a shallower path with longer leaf names would
                    # incorrectly win over a deeper path. ``count(":")``
                    # is the real depth (HP-7).
```

### budgets.py:862-868 | REWRITE | rule 3
SQL-pushdown + stays-in-Python split stays; compress 'pre-fix the inner Python loop touched every transaction'

```
            # Calculate actuals from transactions. Date filter pushed
            # to SQL via _query_filtered_splits — pre-fix the inner
            # Python loop touched every transaction in the book before
            # gating on the date range. The rollup-membership check
            # and the EXPENSE/INCOME sign discipline stay in Python
            # (the rollup map is per-budget, not expressible as a
            # plain WHERE clause).
```

### budgets.py:880-889 | REWRITE | rule 2/4
signed-accumulation netting guardrail + 200/120→80 example stay; strip commit hash 'a34867c' and '(adversarial pass 2, C3)'

```
                )
                # Accumulate SIGNED amounts so contra splits (refunds
                # on expense accounts, losses/clawbacks on income
                # accounts) net against the rollup target — the same
                # netting fix income_by_source / spending_by_category
                # received in a34867c. The pre-fix per-split gross
                # filter (`amount > 0` / `amount < 0`) made the budget
                # report contradict those reports on identical data
                # (adversarial pass 2, C3): spend 200 + refund 120
                # showed actual 200 instead of net 80.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `628-631` KEEP — # Stage prior amounts (per period) so the audit log can / # render before/after diffs. Without this, the bookkeeper
- `647-654` KEEP — # Quantize to the account commodity's smallest fraction: / # USD (fraction=100) → 2 decimals, JPY (fraction=1) → 0,
- `691-693` KEEP — # periods_set is computed (e.g., "q1" → [0, 1, 2]) so we keep / # it. The echoed inputs (budget, account, amount) come from
- `806-808` KEEP — # Gather budgeted amounts (FX-converted to default / # currency so the comparison with default-currency
- `818-822` KEEP — # Convert target amount to default currency at the / # period-end rate. ``None`` factor → no rate on
- `834-839` KEEP — # Roll-up map: descendant-account-fullname → nearest-ancestor- / # fullname that is itself budgeted. Lets budgets set on a
- `980-985` KEEP — # Stage budget snapshot for the audit log BEFORE delete. / # Without this, the audit log shows only "deleted budget X"

## src/gnucash_mcp/book/business.py

### business.py:32-48 | REWRITE | rule 3
JPY/BHD silent-corruption trap stays; restate 'Pre-fix, every conversion hardcoded 0.01… Now derives' present-tense

```
def _commodity_quantum(commodity) -> Decimal:
    """Smallest representable unit of a commodity, as a Decimal quantum.

    Pre-fix, every cross-currency conversion in this module hardcoded
    ``Decimal("0.01")`` — implicitly assuming 2-decimal currencies
    (USD, EUR, GBP, CNY). The hardcode silently corrupts:

    - **JPY** (``fraction=1``): a ¥1,234.50 conversion would round to
      ¥1,234.50 stored, but JPY can't represent half-yen — every
      cross-currency JPY transaction loses the rounding direction.
    - **BHD / KWD** (``fraction=1000``, 3 decimals): a 100.123 BHD
      input is silently rounded to 100.12, losing 3 mils per
      conversion.

    Now derives the quantum from ``commodity.fraction`` (piecash
    stores 100 for USD, 1 for JPY, 1000 for BHD, 10000 for shares).
    """
```

### business.py:62-72 | REWRITE | rule 2
piecash ''-date crash behavior stays (rule-1 gold); cut 'The bookkeeper hit the underlying piecash bug on Alex Chen-Morales's book'

```

    The bookkeeper hit the underlying piecash bug on Alex
    Chen-Morales's book: a freshly auto-id'd bill's
    ``date_posted`` came back as ``''`` in SQL. SQLAlchemy's
    regex-based DATETIME parser raises "Couldn't parse datetime
    string" when reading that — a hard crash on a never-posted
    document. Same failure mode applies verbatim to
    ``date_opened`` (and any future ``_DateTime``-typed column on
    invoices). One helper, parameterized on the attribute name,
    covers every caller.

```

### business.py:207-214 | REWRITE | rule 2
mutual-exclusivity note stays; cut '(Copilot PR #88 review caught the original comment showing them together.)'

```
        # Job annotation — appended after any (CN)/(BILL) tag
        # so a row that's both a credit note AND job-attached
        # reads as ``Customer (CN) (job:JOB-001)``, or a plain
        # vendor bill in a job as ``Vendor (BILL) (job:JOB-001)``.
        # ``(CN)`` and ``(BILL)`` are mutually exclusive per the
        # if/elif above — credit notes are flagged via (CN)
        # regardless of side. (Copilot PR #88 review caught the
        # original comment showing them together.)
```

### business.py:236-237 | REWRITE | rule 4
strip '(C2)'; overpaid-direction rationale stays

```
        # aging-clock columns would invite double-collection.
        # Surface the direction explicitly instead (C2).
```

### business.py:249-254 | REWRITE | rule 3
two-phrasings rationale stays; cut 'Pre-fix the templating concatenated… days past past due'

```
            # Two phrasings — "X days past due" reads as contractual
            # (the user agreed to a date and missed it), "X days past
            # 30-day default" anchors the duration to the assumption
            # we made when no term was set. Pre-fix the templating
            # concatenated "days past " with another "past due" so
            # the contractual case rendered as "days past past due".
```

### business.py:278-283 | REWRITE | rule 2
indexed-query rationale; restate 'was a real hot-path cost' present-tense

```

        Uses an indexed ``filter_by`` query rather than scanning
        ``book.customers``. The ORM-backed CallableList iteration
        was a real hot-path cost in business workflows that look up
        the same customer multiple times per write.
        """
```

### business.py:372-379 | REWRITE | rule 2
GAAP-routing convention stays; restate 'The bookkeeper's call for v1.3 was the small-business convention' as the chosen default + override

```
    # supplier bill is income (a cost reduction).
    #
    # Larger businesses sometimes prefer contra-revenue / contra-
    # expense treatment (Income:Sales Discounts Given as a negative
    # against Revenue). The bookkeeper's call for v1.3 was the small-
    # business convention below; the user can override via the
    # ``discount_account`` parameter on pay_invoice if they want
    # contra accounts instead.
```

### business.py:681-685 | REWRITE | rule 2
tax-not-discounted rationale stays; reword 'per the bookkeeper-validated convention' → 'deliberately'

```
        per the bookkeeper-validated convention: GST/PST is collected
        on behalf of the tax authority at the gross rate and is NOT
        reduced by the discount. Discounting tax would short the
        remittance.
        """
```

### business.py:735-743 | REWRITE | rule 3
explicit-target-vs-first-split guardrail stays; restate 'Pre-fix this method took just…' conditionally

```
        Pre-fix this method took just ``(post_txn,
        invoice_currency)`` and returned the rate of the FIRST
        non-invoice-currency split — which in a multi-cross-currency
        post (e.g., EUR invoice with USD income + GBP A/R) would
        silently pick whichever split happened to come first. Now
        requires an explicit target so the caller gets the rate they
        actually want.

        Returns the Decimal rate, or None if no matching split is
```

### business.py:810-816 | REWRITE | rule 4
discount-leg drift rationale stays; strip '(C10 companion)' and compress 'pre-fix it was booked nowhere'

```
                The discount leg's drift — ``discount ×
                (pay_rate − post_rate)`` — is realized FX exactly
                like the payment leg's; pre-fix it was booked
                nowhere, leaving assets ≠ equity by that amount on
                any cross-currency discount settlement (C10
                companion).

```

### business.py:879-885 | REWRITE | rule 3
convert-to-default-before-booking guardrail + GBP-on-USD-account trap stay; compress 'Pre-fix triple-currency case'

```
        # Convert to the book default currency before booking — the FX
        # account has commodity=default, and a quantity in any other
        # commodity would be silently treated as default by every
        # report that reads this account. Pre-fix triple-currency case
        # (book=USD, invoice=EUR, pay=GBP): a £5 GBP gain landed on a
        # USD-commodity FX account as quantity=5, rendered as "$5
        # gain" — silently wrong.
```

### business.py:894-905 | REWRITE | rule 4
graceful-skip-over-raise rationale stays; strip 'HP-5:'

```
                # HP-5: missing third-currency rate. Mirror the
                # ``rate_at_post`` branch above (which returns
                # None when no rate is available) — skip the FX
                # booking gracefully instead of raising. The
                # payment itself still records correctly; only
                # the realized FX delta is not surfaced. Raising
                # here blocked the entire payment write path for
                # the rare triple-currency case (book=USD,
                # invoice=EUR, pay=GBP, no GBP→USD rate on file)
                # which is worse than silently omitting the
                # gain/loss split.
                return None
```

### business.py:912-920 | REWRITE | rule 4
strip '(C10 companion)'; same-shape-as-payment-leg rationale stays

```
        # Discount leg (C10 companion). Same shape as the payment
        # leg: the discount expense/income was booked at the
        # pay-date rate while the A/R it helped relieve was carried
        # at the post-date rate; the difference is realized FX.
        # Skipped when the discount is denominated in the invoice
        # currency itself (no conversion → no drift) or when a
        # needed rate is missing (mirror the graceful-skip
        # convention above — the payment still records, only the
        # delta isn't surfaced).
```

### business.py:1053-1061 | REWRITE | rule 2
owner-type mapping contract stays; cut 'Pre-vouchers this returned a not-yet-supported error' and '(added in v1.3 with the vouchers feature)'

```
        Anything else raises ``ValueError`` with a message that
        names the three valid options. ``"employee"`` (added in
        v1.3 with the vouchers feature) is the third counterparty
        type in piecash's invoice/bill/voucher polymorphic
        table — see ``counter_exp_voucher`` in piecash's Book
        model. Pre-vouchers this returned a "not yet supported"
        error explicitly to give the LLM a useful hint; now it's
        a first-class type.
        """
```

### business.py:1079-1097 | REWRITE | rule 2
self-heal semantics stay (piecash regex parser crash on ''); cut 'The bookkeeper hit this on Alex Chen-Morales's book'

```
        Self-heals malformed ``date_posted=''`` values to NULL on
        writable sessions before the ORM query runs. piecash's
        ``_DateTime`` TypeDecorator's regex parser raises
        ``ValueError: Couldn't parse datetime string: ''`` when
        loading a row whose ``date_posted`` is an empty string —
        a hard crash that blocks every subsequent invoice/bill
        operation. The bookkeeper hit this on Alex Chen-Morales's
        book where a freshly auto-id'd bill's ``date_posted``
        landed as ``''`` instead of NULL through some persistence
        path. Coercing to NULL upstream of the query is the only
        reliable fix; the ORM never sees the malformed value.

        piecash doesn't expose a readonly flag, so the heal is
        always attempted; the try/except absorbs the failure when
        the session is readonly. Books that need healing must be
        touched through a write operation at least once; after
        that, the cleanup persists and readonly operations
        succeed too.

```

### business.py:1105-1118 | REWRITE | rule 2
collision contract + fail-loud rationale stay; cut 'Pre-fix this returned whichever row…' and 'The bookkeeper hit this on a CNY book…'

```
        Raises:
            ValueError: When ``owner_type=None`` and the ID matches
                both a customer invoice *and* a vendor bill. GnuCash
                runs the two as separate ID sequences sharing one
                ``invoices`` table, so collisions are normal — both
                can legitimately be id ``"000003"``. Pre-fix this
                returned whichever row the query happened to surface
                first, silently routing reads/writes to the wrong
                document. The bookkeeper hit this on a CNY book
                where ``get_invoice("000003")`` returned a customer
                invoice's CNY currency for what was actually a USD
                vendor bill. The error lists candidates with their
                type and currency so the caller can pass
                ``owner_type`` to disambiguate.
```

### business.py:1139-1158 | REWRITE | rule 3
job-attached lookup semantics stay; compress 'Pre-fix: add_invoice_entry / delete_invoice / etc. would have failed'

```
            # Job-attached invoices have owner_type=3 internally
            # — the owner_guid points at a Job, which in turn
            # points at the customer or vendor. From the
            # bookkeeper's perspective, those invoices are still
            # customer/vendor invoices (just grouped). So an
            # owner_type=2 lookup must match BOTH direct
            # customer invoices (owner_type=2) AND job-attached
            # invoices whose job has owner_type=2.
            #
            # Pre-fix: ``add_invoice_entry`` /
            # ``delete_invoice`` / etc. all filter by
            # owner_type=2 and would have failed with "Invoice
            # not found" on any invoice attached to a job. Fix:
            # build a subquery of job GUIDs matching the
            # requested owner_type and OR it in.
            #
            # Employee (owner_type=5) is exempt — piecash's job
            # model is customer/vendor only, so vouchers can't
            # be job-attached. The lookup degrades to the
            # original direct-match filter.
```

### business.py:1237-1242 | REWRITE | rule 2
guid-omitted contract stays; reword 'Bookkeeper-validated finding:' to the rationale itself

```
        Business-object GUIDs are deliberately omitted from the
        response. Bookkeeper-validated finding: every consumer
        addresses customers via ``id`` (human-readable, like
        "000001"); the 32-char GUID is dead weight on every read.
        Same treatment applies to vendor, employee, job, billterm,
        taxtable, invoice, and entry response shapes.
```

### business.py:1478-1480 | REWRITE | rule 4
strip 'Phase 3C:' from _invoice_to_dict docstring

```
        is dropped too. Phase 3C: ``owner_guid`` (raw 32-char hex)
        is dropped in favor of ``owner_name`` resolved by the caller
        — the same readability swap we did for entry account refs.
```

### business.py:1571-1583 | REWRITE | rule 3
owner+amount scanability rationale stays; compress 'Pre-Phase-3B this rendered as… useless for scanning'

```
    def _invoice_to_compact_line(self, book, invoice) -> str:
        """One-line compact format with action columns:

            ``id  TYPE  owner_name  CCY total  date_opened  status``

        Pre-Phase-3B this rendered as ``"000027  INV  2026-05-01
        posted"`` — no owner, no amount, useless for scanning. With
        owner and total in place a bookkeeper can scan a hundred
        invoices and immediately spot what's outstanding for whom.

        Currency is shown when present so multi-currency books read
        unambiguously. Bills get the ``BILL`` tag (was already there).
        """
```

### business.py:1595-1599 | REWRITE | rule 2
single-lookup note stays; cut '(Copilot PR #88 review flagged the original two-call pattern as redundant.)'

```
        #
        # Single Job lookup via ``_resolve_owner_type_and_job``
        # — both the type tag and the job annotation come from
        # the same Job row. (Copilot PR #88 review flagged the
        # original two-call pattern as redundant.)
```

### business.py:1647-1656 | REWRITE | rule 3
narrow-except rationale stays; restate 'Pre-fix the bare except Exception would have swallowed' conditionally

```
        except (ValueError, AttributeError, TypeError):
            # Empty entries (ValueError from
            # ``_get_invoice_entries_and_total``), missing currency
            # attr, or arithmetic on a None — render "?" so the
            # listing line still produces a row. Pre-fix the bare
            # ``except Exception`` would have swallowed programming
            # errors (KeyError / NameError / etc.) silently too;
            # tightened to the predictable shapes that "? amount"
            # is the right rendering for.
            amount_str = "?"
```

### business.py:1718-1720 | REWRITE | rule 2
reword 'Bookkeeper-validated: never used' to the no-tool-surface rationale

```
        # ``guid`` omitted — entries are nested under invoices and
        # have no standalone tool surface (no delete_entry,
        # update_entry, etc.). Bookkeeper-validated: never used.
```

### business.py:1728-1733 | REWRITE | rule 4
strip 'Phase 3C:'

```
        if account_paths is not None and acct_guid:
            # Phase 3C: surface the readable path. Falls back to the
            # raw GUID when a stale entry references a deleted account.
            result["account"] = account_paths.get(
                acct_guid, acct_guid,
            )
```

### business.py:1826-1837 | REWRITE | rule 3
void-aware filter consistency rationale stays; compress 'Pre-fix this method summed every split (right by accident)'

```
        Voided splits (``reconcile_state == 'v'``) are GnuCash's
        zombie audit-trail records — value/quantity zeroed but the
        row preserved. Filtering them here matches the void-aware
        treatment ``unpost_invoice`` uses for "are there real
        payments on this lot?" and the lot-listing helpers use for
        active-position math. Pre-fix this method summed every
        split (voided ones contribute zero so the SUM was right by
        accident), which left the helper out of step with the rest
        of the codebase's void semantics — a future caller might
        reasonably check ``len(lot.splits)`` against this balance
        and get inconsistent answers.
        """
```

### business.py:1847-1855 | REWRITE | rule 4
strip '(C10)'; carried-quantity semantics stay

```
        """Sum of split QUANTITIES in a lot, skipping voided splits.

        Companion to :meth:`_calculate_lot_balance` (which sums
        ``value``, i.e. invoice-currency units). Quantities are in
        the post account's commodity — when that commodity differs
        from the invoice currency, this is what the receivable /
        payable account actually carries, and it's the number a
        settlement must relieve exactly (C10).
        """
```

### business.py:1889-1893 | REWRITE | rule 3
exists-so-both-surfaces-agree purpose stays; compress 'Without it the two were diverging'

```
        This helper exists so the warnings collector and
        ``get_outstanding_invoices`` produce identical due-date math.
        Without it the two were diverging — warnings used the full
        three-step chain, ``get_outstanding_invoices`` had nothing.
        """
```

### business.py:2170-2171 | REWRITE | rule 4
strip 'MP-5:'

```
        # MP-5: cap free-text byte lengths up front.
        notes_kwarg = extra_kwargs.get("notes")
```

### business.py:2209-2211 | REWRITE | rule 2
'v1.3.1: guid dropped (bookkeeper-validated as unused)' → present-tense contract ('id is the working handle')

```
        # v1.3.1: business-object ``guid`` dropped from write
        # responses (bookkeeper-validated as unused on the LLM
        # surface). ``id`` is the working handle.
```

### business.py:2227-2234 | REWRITE | rule 4
strip 'MP-5:' and 'Same shape as HP-9's caps'; bloat-prevention rationale stays

```
    # MP-5: free-text caps on business-entity fields. Same shape as
    # HP-9's caps on ``void_transaction(reason)`` and
    # ``set_account_slot(value)`` — prevents a misbehaving LLM (or
    # a user copy-pasting a multi-MB blob) from bloating the
    # business-entity rows. Notes can be paragraphs; address sub-
    # fields are typically one-liners; the bounds reflect that.
    _NOTES_MAX_BYTES = 4 * 1024
    _ADDRESS_FIELD_MAX_BYTES = 1024
```

### business.py:2243-2250 | REWRITE | rule 4
strip 'MP-5:' from docstring

```
        """MP-5: validate notes / address sub-field byte lengths
        before they reach the ORM. UTF-8 byte length, not character
        length — the storage backing is SQLite TEXT and the byte
        cap is what matters for serialization.

        Raises ValueError naming the offending field so the LLM
        can shorten and retry. Empty / missing values pass through.
        """
```

### business.py:2371-2375 | REWRITE | rule 2
auto-load-ISO rationale stays; cut '(matches the create_price fix earlier in this release)'

```
        if currency is not None:
            # ``_get_or_create_currency`` auto-loads ISO codes the book
            # hasn't seen before (matches the ``create_price`` fix
            # earlier in this release). Users shouldn't have to
            # pre-load EUR before switching a vendor to EUR.
```

### business.py:2391-2392 | REWRITE | rule 4
strip 'MP-5:'

```
            # MP-5: cap notes byte length up front.
            self._validate_business_freetext(notes=notes)
```

### business.py:2403-2404 | REWRITE | rule 4
strip 'MP-5:'

```
            # MP-5: cap address-field byte lengths up front.
            self._validate_business_freetext(address=address)
```

### business.py:2433-2433 | REWRITE | rule 2
'v1.3.1: guid dropped' version tag → plain cross-reference

```
        # v1.3.1: ``guid`` dropped — see _create_business_person.
```

### business.py:2835-2835 | REWRITE | rule 2
'v1.3.1: guid dropped' version tag

```
            # v1.3.1: ``guid`` dropped — billterms addressed by ``name``.
```

### business.py:2866-2888 | FIX-FALSE | rule 6
doctrine specimen: 'see commit 3's posting math… wire-up (commit 4)… happens later' — all landed; restate present-tense (refcount discipline paragraph stays)

```
    # ── Taxtable CRUD ─────────────────────────────────────────────
    #
    # Taxtables route sales-tax math on invoice/bill/voucher/credit-
    # note line entries. A taxtable holds one or more
    # ``TaxtableEntry`` rows; each entry contributes either a
    # percentage rate (5.00 means 5%) or a flat value (5.00 means a
    # fixed $5 surcharge) routed to a specific GL account.
    #
    # A multi-entry taxtable (e.g., GST 5% + PST 7%) produces N tax
    # splits per line at posting time, one per entry to its own
    # account — see commit 3's posting math. This commit is pure
    # data CRUD; the wire-up into entries (commit 4) and into
    # ``_get_invoice_entries_and_total`` (commit 3) happens later.
    #
    # **Refcount discipline.** GnuCash desktop maintains
    # ``Taxtable.refcount`` as the number of entries referencing the
    # taxtable. piecash does not auto-maintain it; we manage it
    # manually on entry create/delete in commit 4. For lifecycle
    # checks here (delete guard, update warning), we use
    # ``_compute_taxtable_refcount`` — an indexed SQL count over the
    # ``entries`` table, authoritative regardless of the stored
    # ``refcount`` column. Voided invoices still pin their taxtables
    # because their entry rows persist for audit trail.
```

### business.py:3277-3277 | REWRITE | rule 2
'v1.3.1: guid dropped' version tag

```
            # v1.3.1: ``guid`` dropped — taxtables addressed by ``name``.
```

### business.py:3515-3515 | REWRITE | rule 2
'v1.3.1: guid dropped' version tag

```
                # v1.3.1: ``guid`` dropped.
```

### business.py:3631-3635 | REWRITE | rule 2
restate 'The legacy two-way is_bill idiom doesn't extend' present-tense

```
    # ── Owner-type label helpers ─────────────────────────────────
    # The legacy two-way "is_bill = owner_type == 4" idiom doesn't
    # extend cleanly to three counterparty types. These small
    # helpers replace the binary check with a three-way lookup so
    # rendering / dispatch code stays readable.
```

### business.py:3655-3666 | REWRITE | rule 2
bill-side semantics stay; cut 'The legacy code used… the rename captures the truth'

```
    @staticmethod
    def _is_bill_side(owner_type: int) -> bool:
        """Bills (4) and vouchers (5) both represent "company owes
        someone" semantics — same posting direction, same entry
        column group (``b_*``), same allowed account types. The
        legacy code used ``inv.owner_type == 4`` to mean this; the
        rename to ``_is_bill_side`` captures the truth.

        For job-attached invoices (owner_type=3), pass the job's
        owner_type instead — see ``_effective_owner_type``.
        """
        return owner_type in (4, 5)
```

### business.py:3696-3713 | REWRITE | rule 2
single-query contract stays; cut 'avoids the redundant query pattern Copilot flagged on PR #88 where callsites…'

```
    @staticmethod
    def _resolve_owner_type_and_job(book, invoice):
        """Single-query variant of ``_effective_owner_type``
        that also returns the Job object when one is linked.

        Returns ``(effective_owner_type, job_or_none)``. For
        direct documents (owner_type 2/4/5) the Job is None.
        For job-attached docs (owner_type=3), both are derived
        from a single Job lookup — avoids the redundant query
        pattern Copilot flagged on PR #88 where callsites in
        ``_invoice_to_compact_line`` and
        ``get_outstanding_invoices`` were doing
        ``_effective_owner_type`` AND ``_find_job_by_guid``
        side-by-side (each chasing the same Job row).

        Defensive fallback to (raw_owner_type, None) on dangling
        owner_guid — same shape as the original helper.
        """
```

### business.py:3721-3726 | REWRITE | rule 2/4
doctrine-adjacent: three-way label rationale stays; cut 'replaces the legacy binary…(Copilot PR #86 review.)'

```
    # Human-readable document label for use in error messages,
    # status returns, and audit log entries. Title-case canonical
    # form. Three-way (Invoice / Bill / Voucher) replaces the
    # legacy binary ``"Bill" if is_bill else "Invoice"`` which
    # mislabeled vouchers as bills. (Copilot PR #86 review.)
    _OWNER_TYPE_TO_DOC_LABEL = {2: "Invoice", 4: "Bill", 5: "Voucher"}
```

### business.py:3947-3965 | REWRITE | rule 3
owner-currency-first resolution + $500-booked-as-¥500 trap stay; compress 'Pre-fix this fallback used book default unconditionally'

```
            else:
                # Resolution order when currency isn't passed explicitly:
                #   1. Owner's currency (customer/vendor) — every
                #      business document inherits the trading
                #      relationship's currency by default. A USD
                #      vendor's bill should be USD, not the book's
                #      default. This matches GnuCash desktop UI
                #      behavior and the bookkeeper's mental model.
                #   2. Book default — fallback for owners that have
                #      no currency set (shouldn't happen with piecash
                #      owners, but defensive).
                #
                # Pre-fix this fallback used book default unconditionally,
                # which broke cross-currency posting for any book with
                # foreign customers/vendors: bills against a USD vendor
                # on a CNY book got created in CNY, then ``post_invoice``
                # saw inv.currency == account.commodity and skipped the
                # rate-conversion path entirely. $500 was then booked
                # as ¥500.
```

### business.py:3996-4018 | REWRITE | rule 2/3
counter-drift guardrail + max(counter, MAX(id)) fix description stay; cut 'The bookkeeper hit this on Alex's synthetic book: 2025 bills sat at IDs…'

```
            else:
                # Auto-generate the next document ID. The book
                # counter (``counter_invoice`` / ``counter_bill``)
                # SHOULD be the canonical source, but it can drift
                # below the actual MAX(id) — e.g. when historical
                # documents are imported via raw SQL without
                # bumping the counter, or when the file was edited
                # outside the MCP server's lifecycle. The
                # bookkeeper hit this on Alex's synthetic book:
                # 2025 bills sat at IDs 000006 / 000007 but the
                # counter was lower, so a new 2026 ``create_bill``
                # auto-assigned 000006 — colliding with the
                # 2025 row and breaking every subsequent
                # ``post_invoice`` / ``get_outstanding_invoices``
                # lookup that resolved to the wrong record.
                #
                # Fix: take the max of (book counter, actual max
                # numeric ID in the table for this owner_type) and
                # use that + 1. Re-sync the book counter so the
                # next auto-id picks up where this one left off.
                # Non-numeric existing IDs (custom strings the
                # user supplied) are skipped — they're irrelevant
                # to numeric auto-numbering.
```

### business.py:4082-4083 | REWRITE | rule 2
'v1.3.1: guid dropped' version tag

```
            # v1.3.1: ``guid`` dropped — invoices/bills/vouchers/
            # credit notes addressed by ``id``.
```

### business.py:4277-4283 | REWRITE | rule 2
three-way name-the-right-tool dispatch rationale stays; cut '(Copilot PR #87 review): the legacy binary INV-vs-BILL branch would have suggested…'

```
        if not self._get_is_credit_note(inv):
            # Found the ID but it's a regular invoice/bill/voucher.
            # Name the right tool to use instead so the LLM can
            # correct course in one hop. Three-way dispatch
            # (Copilot PR #87 review): the legacy binary "INV vs
            # BILL" branch would have suggested add_bill_entry /
            # delete_bill for a voucher (owner_type=5) — wrong.
```

### business.py:4436-4441 | REWRITE | rule 2
credit-on-credit-is-meaningless rationale stays; cut '(Copilot PR #87 review.)'

```
                # Source must be a regular invoice/bill, not
                # itself a credit note. Credit-note-against-
                # credit-note is semantically meaningless: a
                # credit reverses a posted document; chaining
                # them doesn't represent anything in real
                # bookkeeping. (Copilot PR #87 review.)
```

### business.py:4679-4706 | REWRITE | rule 5
doctrine specimen: reorganize contract-first; cut '90% duplicated' history and '(commit 4 of the v1.3 taxtable arc)' heading; taxtable semantics + refcount side effect stay

```
        """Shared implementation behind ``add_invoice_entry`` and
        ``add_bill_entry``. The two methods were 90% duplicated
        (the only differences: which side of the entries-table
        column pair gets the price/account, the owner_type code,
        the allowed account types, and the response id key). This
        helper takes ``owner_type`` (2=customer invoice, 4=vendor
        bill), looks up the per-doc config in ``_ENTRY_CONFIG``,
        and writes the entry.

        **Taxtable wire-up (commit 4 of the v1.3 taxtable arc):**

        When ``taxtable`` is given, the entry is marked taxable and
        the resolved taxtable's GUID is written to ``i_taxtable``
        or ``b_taxtable`` (side-dependent). ``tax_included`` flags
        whether the line price represents pre-tax (False, default)
        or gross (True) — the posting math in
        ``_get_invoice_entries_and_total`` uses this to decide
        whether to extract pretax from gross or add tax on top.

        ``tax_included=True`` without ``taxtable`` raises — silently
        treating it as no-op would drop the caller's signal that
        they thought they were enabling tax.

        The taxtable's stored ``refcount`` column is incremented
        post-insert to keep GnuCash desktop's bookkeeping in sync
        (our own lifecycle checks use SQL-computed counts, but the
        desktop UI reads the stored value).
        """
```

### business.py:4734-4743 | REWRITE | rule 2
effective-owner-type side-check rationale stays; cut 'Copilot PR #88 flagged this as duplicate logic with the helper'

```
            # Side-check: the invoice's "effective" owner_type
            # is owner_type directly when 2/4/5; for owner_type=3
            # (job-attached) it's the underlying job's
            # owner_type. ``_find_invoice`` already filters by
            # both direct and job-attached for owner_type 2/4,
            # so reaching here with the wrong side means a true
            # caller mistake (customer-id passed to
            # add_bill_entry, e.g.).
            # Centralize via _effective_owner_type — Copilot PR
            # #88 flagged this as duplicate logic with the helper.
```

### business.py:4862-4863 | REWRITE | rule 2
'v1.3.1: entry guid dropped' version tag

```
            # v1.3.1: entry ``guid`` dropped — no tool surface
            # consumes a standalone entry GUID.
```

### business.py:5003-5005 | CUT | rule 2
docstring: 'Pre-fix this method dumped every invoice in the book regardless of caller intent'

```
            limit: Maximum invoices to return. Defaults to 50, capped at
                   250 server-side. Pre-fix this method dumped every
                   invoice in the book regardless of caller intent.
```

### business.py:5073-5082 | REWRITE | rule 2/4
N+1 job preload rationale stays; strip 'Phase 3C' and 'Copilot flagged on PR #88'

```
                # Verbose path: resolve owner per invoice so each dict
                # carries the readable name. ``owner_guid`` was dropped
                # from the dict shape in Phase 3C.
                #
                # Job preload — avoids the N+1 query pattern Copilot
                # flagged on PR #88. Single query for every Job
                # referenced by job-attached invoices in the result
                # set, indexed by GUID, then in-memory lookup per
                # row. With 100 job-attached invoices spanning 5
                # jobs, this is 1 query instead of 100.
```

### business.py:5112-5119 | REWRITE | rule 2
envelope-shape contract stays; cut 'The bookkeeper noticed verbose was the odd one out'

```
                # Envelope shape matches ``get_unreconciled_splits`` and
                # ``get_prices`` so verbose-mode callers see truncation
                # signal in the response. The bookkeeper noticed verbose
                # was the odd one out: compact got the [Showing N of M]
                # notice appended, but the dict version had no count /
                # total / notice fields at all. ``count`` = truncated
                # length, ``total`` = full filter set size, ``notice``
                # is the same string compact appends (or None).
```

### business.py:5166-5171 | REWRITE | rule 4
strip 'Phase 3C:'; one-query account_paths rationale stays

```
            # Phase 3C: build a guid → fullname map once (covers every
            # account referenced by entries on this invoice), then
            # thread it through ``_entry_to_dict`` so each entry shows
            # ``account: "Income:LLC Revenue"`` instead of an opaque
            # 32-char hex GUID. One query, ``account_paths`` shared
            # across all entries.
```

### business.py:5250-5254 | REWRITE | rule 3
three-way dispatch rationale stays; compress 'Pre-fix this was a binary check that returned None for vouchers'

```
            # Three-way owner lookup — vouchers route to employees,
            # bills to vendors, invoices to customers. Pre-fix this
            # was a binary "is_bill → vendor else customer" check
            # that returned None for vouchers because employees
            # weren't in the dispatch.
```

### business.py:5336-5341 | REWRITE | rule 4
strip 'R-2:'; shared-chokepoint contract stays

```
        R-2: shared chokepoint for the cross-currency math that
        ``post_invoice`` and ``pay_invoice`` previously did with
        nearly-identical inline closures (``_qty_for_split`` and
        ``_convert``). One helper, two callers, same error shape
        — so a future fix to the rate-lookup or quantization
        cascade hits both paths without re-derivation.
```

### business.py:5502-5520 | REWRITE | rule 2
cross-sequence collision + post-account-type disambiguation rationale stay; cut 'the bookkeeper hit this on Alex's book trying to post vendor bill 000010'

```
            # GnuCash uses separate ID sequences for customer
            # invoices (owner_type=2) and vendor bills
            # (owner_type=4) but stores both in the ``invoices``
            # table. IDs collide across sequences (a $5K Emerald
            # Analytics invoice and a $250 Office Depot bill can
            # both be id=000010). Without an owner_type filter,
            # ``_find_invoice`` returns whichever row hits first
            # — the bookkeeper hit this on Alex's book trying to
            # post a vendor bill 000010 and getting back an
            # already-posted customer invoice 000010, raising
            # spurious "already posted".
            #
            # When the caller didn't specify ``owner_type``,
            # disambiguate by reading the post_account type:
            # a RECEIVABLE account can only receive customer
            # invoices, a PAYABLE only vendor bills. The
            # post_account validation later in this method already
            # uses the same predicate; using it upstream for the
            # lookup eliminates the collision class entirely.
```

### business.py:5535-5548 | REWRITE | rule 2
truthy-vs-is-not-None piecash gotcha + cross-reference list stay; cut 'The bookkeeper hit this on Alex's book where bill 000008…'

```
            # Truthy check rather than ``is not None``: piecash's
            # _DateTime TypeDecorator can return falsy non-None
            # values (empty string in some persistence paths) for
            # never-posted documents. The bookkeeper hit this on
            # Alex's book where freshly auto-id'd bill 000008 had
            # ``date_posted=""`` in SQL and post_invoice raised
            # "already posted" on it. ``if inv.date_posted`` treats
            # both None and "" as "not posted"; only a real
            # datetime is truthy. Same fix applied to all other
            # date_posted checks in this module — see also
            # ``add_invoice_entry``, ``add_bill_entry``,
            # ``pay_invoice``, ``list_invoices`` filter,
            # ``_delete_invoice_or_bill``, and the dependency
            # check in ``_invoice_dependency_check``.
```

### business.py:5578-5580 | REWRITE | rule 4
strip 'MP-11:'; auto-register fact stays

```
            # MP-11: see investments.py — Lot auto-registers via
            # the account back-pop; the explicit session.add was
            # redundant.
```

### business.py:5590-5596 | REWRITE | rule 3
''-vs-None sentinel rationale stays; compress 'pre-fix here was ``or owner_name``'

```
            # ``description=None`` (the default) falls back to the
            # owner's name — historical behavior. An explicit ``""``
            # is treated as "use an empty description on purpose"
            # so the caller can deliberately blank the field
            # without inheriting the owner name. Same pattern
            # ``pay_invoice`` uses; pre-fix here was ``or owner_name``
            # which collapsed "" into the fallback.
```

### business.py:5603-5610 | REWRITE | rule 4
strip 'R-2:'; shared-chokepoint + fx_stale collection notes stay

```
            # Helper: convert a value in invoice currency to the
            # equivalent quantity in the given account's commodity.
            # R-2: routes through ``_convert_invoice_amount`` so
            # post and pay share one rate-lookup + quantization
            # chokepoint. The rate is unused here (post doesn't
            # need it for downstream FX-gain math); ``stale_meta``
            # is collected so the response can surface ``fx_stale``
            # when ``force`` overrode the freshness guard.
```

### business.py:5809-5813 | REWRITE | rule 2
three-way label dispatch rationale stays; cut 'was a binary… that mislabeled vouchers as bills (Copilot PR #86 review)'

```
            is_bill = self._is_bill_side(self._effective_owner_type(book, inv))
            # Three-way label dispatch — was a binary "Bill if
            # is_bill else Invoice" that mislabeled vouchers as
            # bills (Copilot PR #86 review).
            doc_label = self._doc_label_for(inv.owner_type)
```

### business.py:6058-6066 | REWRITE | rule 2
voided-posting refusal rationale stays; reword '(post-fix it skips voided splits explicitly)'

```
            # Refuse to pay against a voided posting transaction.
            # GnuCash's void zeroes split values but preserves rows
            # with ``reconcile_state='v'``. ``_calculate_lot_balance``
            # would compute remaining=0 (post-fix it skips voided
            # splits explicitly), and the lot would auto-close on
            # save — leaving the new payment split assigned to a
            # closed lot. Block the operation up front so the user
            # has to ``unvoid_transaction`` (or unpost + re-post)
            # first.
```

### business.py:6090-6097 | REWRITE | rule 3
explicit-empty-description contract stays; compress 'Pre-fix "" was indistinguishable from None'

```
            # ``description if not None`` (vs. ``description or
            # owner_name``) lets the caller pass an empty string
            # explicitly when they want a blank transaction
            # description — e.g., to avoid leaking the customer
            # name into a memo. ``description=None`` (the default)
            # falls back to the owner's name, preserving the
            # historical default. Pre-fix ``""`` was indistinguish-
            # able from ``None``.
```

### business.py:6109-6123 | REWRITE | rule 3+CUT
doctrine specimen: compress the pay-path-didn't-convert story to a both-sides-convert guardrail; CUT the 'R-2: … 2-tuple shape so the unpack sites below are unchanged' diff-narration tail (lines 6100-6108 stay as-is)

```
            #
            # Pre-fix, ``pay_invoice`` only converted the bank-side
            # quantity. ``post_invoice`` correctly converted the A/R
            # side via ``_qty_for_split(post_acct, ...)``; the pay
            # path didn't, so a USD A/R holding a EUR invoice was
            # liquidated in EUR-as-USD on payment.
            # R-2: ``post_invoice`` and ``pay_invoice`` share
            # the cross-currency rate-lookup + quantization
            # chokepoint via ``_convert_invoice_amount``. Pay
            # consumes both the converted quantity AND the
            # rate (the rate feeds ``_compute_fx_gain_loss``
            # below). ``stale_meta`` is collected so the response
            # can surface ``fx_stale`` when ``force`` overrode the
            # freshness guard; ``_convert`` keeps its 2-tuple shape
            # so the unpack sites below are unchanged.
```

### business.py:6175-6184 | KEEP | rule 3
doctrine specimen: invariant + disaster story guards the obvious abs(); strip ONLY '(adversarial pass 2, C2)' from the header line

```
            # ── Overpayment guard (adversarial pass 2, C2) ────────
            # The lot balance is signed: positive for A/R invoices,
[... 8 more lines ...]
```

### business.py:6203-6209 | REWRITE | rule 4
five-rejection-cases + explicit-opt-in rationale stay; reword 'Per the spec:' (spec not in repo)

```
            # ── Early-payment discount validation ────────────────
            # Five rejection cases, each with a distinct error so
            # the caller can see exactly which precondition failed.
            # Per the spec: discount is an explicit opt-in (no
            # auto-detect from coincidental shortfall) — when
            # apply_discount=True, the call either honors all
            # invariants and books the discount split, or rejects.
```

### business.py:6306-6316 | REWRITE | rule 4
book-at-shortfall-not-expectation guardrail (GncImbalanceError) stays; strip '(A5)'

```
                # Book the discount at the ACTUAL shortfall, not the
                # computed expectation. The tolerance above admits a
                # 1-quantum mismatch (rounding of the user's payment);
                # booking ``expected`` then left the splits off by
                # that quantum and the transaction died later with an
                # opaque GncImbalanceError after the validator had
                # blessed the input (A5). A/R clears
                # ``remaining_before`` and the bank brings
                # ``payment_amount``, so the discount leg must be
                # exactly their difference.
                expected = shortfall.quantize(quantum)
```

### business.py:6357-6366 | REWRITE | rule 4
relieve-at-carried-rate guardrail (phantom A/R) stays; strip 'C10:'

```
            # C10: when the A/R-A/P account's commodity differs from
            # the invoice currency, relieve the lot at the rate it is
            # CARRIED at — proportional to the lot's remaining
            # quantity — never at the pay-date rate. Pay-date relief
            # left the post→pay drift in the account's quantity
            # balance forever (a permanent phantom A/R on a fully
            # settled invoice) while the same drift was also booked
            # as the explicit FX split. A full settlement relieves
            # the remaining quantity exactly; a partial one relieves
            # pro-rata in invoice-currency terms.
```

### business.py:6494-6498 | REWRITE | rule 4
never-abs()-a-lot-balance guardrail stays; strip '(C2)'

```
            # Direction-normalized remaining: positive = still owed.
            # Never abs() a lot balance — with the overpayment guard
            # above this is always >= 0 via pay_invoice, but if some
            # other path left the lot negative, a credit must surface
            # as negative rather than masquerade as money owed (C2).
```

### business.py:6534-6538 | REWRITE | rule 4
effective_is_bill-not-is_bill label rationale stays; strip 'Pre-fix a booked FX loss… (adversarial pass 2, A4)'

```
                    # label must follow the same direction the ledger
                    # split was booked with (_compute_fx_gain_loss is
                    # called with effective_is_bill above). Pre-fix a
                    # booked FX loss on a customer credit-note refund
                    # was labeled "gain" (adversarial pass 2, A4).
```

### business.py:6730-6743 | REWRITE | rule 2
per-currency-A/R convention + what-this-catches stay; cut '(Alex has Accounts Receivable, … CAD)' and '(Copilot PR #87 review.)'

```
            # Cross-currency apply isn't supported here — the
            # netting transaction is in the post account's
            # commodity, so the document currency must match.
            # In practice every well-formed book has per-currency
            # A/R accounts (Alex has 'Accounts Receivable',
            # 'Accounts Receivable EUR', 'Accounts Receivable
            # CAD'), so EUR documents post to EUR A/R and the
            # commodity equals the document currency. The case
            # this catches: someone deliberately posted a EUR
            # invoice to a USD A/R account (unusual, but
            # ``post_invoice`` supports it via FX rates). For
            # those, apply_credit_note rejects with a clear
            # message rather than silently producing wrong split
            # values. (Copilot PR #87 review.)
```

### business.py:6812-6819 | REWRITE | rule 2
sub-quantum no-op guard rationale stays; cut '(Copilot PR #87 review.)'

```
            # Quantize the apply amount to the post account's
            # commodity. Defensive guard against a sub-quantum
            # amount (e.g. ``amount="0.001"`` on a USD account
            # with 0.01 quantum) producing a no-op netting
            # transaction that reports success while moving
            # nothing. Same shape as the cross-currency
            # quantize-to-zero guard in pay_invoice.
            # (Copilot PR #87 review.)
```

### business.py:7050-7063 | REWRITE | rule 4
why-no-_verify_* rationale + scanner-scope note stay, restated self-contained with verified pointer (tests/test_contract_integrity.py::TestWriteVerificationCoverage); strip '(Review L3: corruption harm refuted…)' framing

```
                for ref in tax_refs:
                    # Intentionally NOT paired with a _verify_*: the
                    # stored ``refcount`` column has no in-process
                    # consumer (every refcount decision goes through
                    # ``_compute_taxtable_refcount``'s live COUNT(*)),
                    # ``MAX(0, …)`` makes any miss a harmless no-op, and
                    # ``taxtable_guid`` is read from the same entry rows
                    # being deleted in this transaction. This is a
                    # best-effort cache decrement, not a source of truth.
                    # (Review L3: corruption harm refuted; the contract
                    # scanner covers ``Table.__table__`` DML, not raw
                    # text() — broadening it to every text() statement
                    # would flag the many legitimately-unverified slot
                    # operations, so that is deliberately out of scope.)
```

### business.py:7095-7100 | REWRITE | rule 4
strip '(A6)'; no-ON-DELETE-CASCADE rationale stays

```
            # Slot cleanup (A6): credit-note flag, the
            # gnc-mcp/applies-to-invoice linkage, and any date
            # slots live in the slots table keyed by this guid with
            # no ON DELETE CASCADE — the raw-SQL row delete above
            # would orphan them. Same explicit-cleanup pattern as
            # ``_delete_business_person``.
```

### business.py:7202-7202 | REWRITE | rule 2
'v1.3.1: guid dropped' version tag

```
            # v1.3.1: ``guid`` dropped from delete responses too.
```

### business.py:7324-7345 | KEEP | rule 1
doctrine specimen: open-constructor / auto-id / no-posted-state / PersonType-KeyError knowledge exists nowhere else — untouchable

```
    # ── Job CRUD ─────────────────────────────────────────────────
    #
[... 20 more lines ...]
```

### business.py:7432-7432 | REWRITE | rule 2
'v1.3.1: guid dropped' version tag

```
            # v1.3.1: ``guid`` dropped — jobs addressed by ``id``.
```

### business.py:7636-7636 | REWRITE | rule 2
'v1.3.1: guid dropped' version tag

```
            # v1.3.1: ``guid`` dropped from update responses.
```

### business.py:7711-7711 | REWRITE | rule 2
'v1.3.1: guid dropped' version tag

```
            # v1.3.1: ``guid`` dropped from delete response.
```

### business.py:7788-7793 | REWRITE | rule 2
resolve-up-front rationale stays; cut 'Copilot PR #88 review caught the original side-by-side as redundant'

```
                # Single Job query via _resolve_owner_type_and_job
                # — Copilot PR #88 review caught the original
                # ``_effective_owner_type`` + ``_find_job_by_guid``
                # side-by-side as redundant. Resolved up front
                # because the lot-balance direction below needs the
                # effective side too.
```

### business.py:7825-7831 | REWRITE | rule 4
negative-means-overpaid + double-collection trap stay; strip 'Pre-fix this was abs()'d… (adversarial pass 2, C2)'

```
                # A/P bill lots negative, credit notes flipped.
                # NEGATIVE here means overpaid — the counterparty
                # holds a credit. Pre-fix this was abs()'d, so an
                # overpaid invoice rendered as money still OWED in a
                # collections list, inviting double-collection
                # (adversarial pass 2, C2).
                amount_due = -balance if (is_bill ^ is_credit_note) else balance
```

### business.py:7841-7844 | REWRITE | rule 3
signed-arithmetic example (grand 3500/due −1000→paid 4500) stays; drop 'the pre-fix abs() derivation showed paid 2500'

```
                # Signed arithmetic keeps amount_paid honest in the
                # overpaid case: grand 3500, due −1000 → paid 4500
                # (the pre-fix abs() derivation showed paid 2500).
                amount_paid = grand_total - amount_due
```

### business.py:7847-7853 | REWRITE | rule 3
polymorphic-dispatch rationale stays; compress 'Pre-fix this used the direct finders keyed off is_bill'

```
                # routes customer/vendor/employee/job (chasing
                # owner_type=3 through the Job to the underlying
                # counterparty). Pre-fix this used the direct
                # customer/vendor finders keyed off ``is_bill``,
                # which returned None for job-attached invoices
                # because inv.owner_guid points at a Job, not a
                # customer/vendor row.
```

### business.py:7860-7867 | REWRITE | rule 4
no-aging-clock-on-credit-notes/overpaid rationale stays; strip '(C2)' and compress 'pre-fix an unapplied credit note carried days_past_due: 238'

```
                # Resolve the due date through the same three-step
                # chain the warnings collector uses, so the bookkeeper
                # sees identical numbers in both places. No aging
                # clock on credit notes (money the business OWES has
                # no past-due concept) or overpaid docs (nothing left
                # to collect) — pre-fix an unapplied credit note
                # carried ``days_past_due: 238`` in a collections
                # list (C2).
```

### business.py:8007-8014 | REWRITE | rule 4
direction-normalized-not-abs rationale stays; strip '(adversarial pass 2, C2)'

```
                    posted_count += 1
                    # Posted: paid = billed - outstanding (from the
                    # lot balance). Direction-normalized, NOT abs()'d:
                    # an overpaid invoice carries a NEGATIVE
                    # outstanding (credit held by the counterparty),
                    # which keeps ``paid`` honest instead of
                    # understating it by twice the credit
                    # (adversarial pass 2, C2).
```

### business.py:8106-8116 | REWRITE | rule 4
strip '(Phase 4D)' x2; dropped-period-echo contract restated present-tense

```
            vendor_id: Optional filter to specific vendor.
            compact: If True (default), return an aligned text table
                     suitable for direct LLM consumption (Phase 4D).
                     Verbose mode returns the structured dict.

        Returns:
            If compact: text table (one line per vendor + TOTAL).
            If not compact: dict with vendor breakdown and grand totals.
            The Phase 4D spec dropped the ``period`` echo (it duplicated
            input the caller already has); verbose mode no longer
            includes it either.
```

### business.py:8124-8127 | REWRITE | rule 3
compress 'pre-fix the table emitted $ regardless of book setting'

```
            # Capture default currency for the compact formatter —
            # pre-fix the table emitted ``$`` regardless of book
            # setting.
            default_currency = self._require_default_currency(book)
```

### business.py:8130-8133 | REWRITE | rule 4
strip 'Pre-v1.3 release this omitted as_of… — SB-3'; period-end-rates rationale stays

```
            # Latest market rates for FX conversion as of the
            # report period's end. Pre-v1.3 release this omitted
            # ``as_of`` and used today's rates against historical
            # vendor periods — SB-3.
```

### business.py:8205-8213 | REWRITE | rule 3
lot-balance fallback rationale stays; compress 'Pre-fix this silently substituted Decimal(0)'

```
                except ValueError:
                    # Empty/corrupted entries — fall back to the
                    # lot balance (computed above). Pre-fix this
                    # silently substituted Decimal(0), which made
                    # ``total_billed`` understate by the bill's
                    # full amount on any data-corruption case. The
                    # lot balance reflects the actual outstanding
                    # liability so the vendor's row stays sane.
                    total = abs(balance)
```

### business.py:8229-8234 | REWRITE | rule 4
strip trailing 'L4.'; quantize-the-FX-product rationale stays

```
                    if rate is not None:
                        # Quantize the FX products to the default
                        # currency's precision: the verbose path emits
                        # these via str(), so an unquantized product
                        # would expose a long fractional tail (the
                        # compact path masks it via _money's 2dp). L4.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `198-202` KEEP — # Owner suffix communicates the document side. Credit / # notes win over BILL because the credit-note semantic
- `223-227` KEEP — # Credit notes don't have a "due date" concept the way / # invoices/bills do — they sit as available credit until
- `342-346` KEEP — # Path for the auto-created realized-FX-gain/loss income account. / # Single credit-natural account: positive balance = net gain, negative
- `349-355` KEEP — # Substrings that identify a user-named FX gain/loss account on / # the leaf-name match below. Books in the wild use a wide range
- `494-499` KEEP — # Construct — piecash.Account auto-adds to the session via the / # parent linkage. Don't flush here: the caller is still building
- `696-700` KEEP — # Use date_opened as the discount-window anchor — that's the / # invoice issuance date (what's printed on the invoice the
- `845-852` KEEP — # Find the rate that was applied at posting (invoice currency / # → pay_acct.commodity). First try the post transaction's own
- `871-873` KEEP — # ``expected_at_post`` is in pay-account commodity (we'll / # subtract pay_quantity from it). Quantize to that commodity's
- `961-963` KEEP — # Skip booking the FX split when the realized delta is below / # the smallest representable unit in the FX account's
- `971-974` KEEP — # Customer (is_bill=False): received more → gain → credit / # income (quantity = -fx_diff for gain, +|fx_diff| for loss).
- `1132-1134` KEEP — # Best-effort heal — readonly sessions, locked / # connections, and other rare failures fall through;
- `1174-1178` KEEP — # owner_type=None: caller didn't disambiguate. Pull all / # matches and fail loud on collision rather than silently
- `1512-1516` KEEP — # Type resolution: owner_type 2/4/5 maps directly. For / # owner_type=3 (Job), the semantic type follows the job's
- `1546-1551` KEEP — # Credit-note keys conditionally included so the response / # shape for normal documents is byte-identical to pre-v1.3.
- `1556-1559` KEEP — # Job link present iff caller threaded a job dict. The / # ``id`` and ``name`` are the surface a bookkeeper wants;
- `1564-1566` KEEP — # Tax summary conditional on the invoice having any / # tax-bearing lines. Non-tax invoices keep their pre-v1.3
- `1614-1618` KEEP — # Owner lookup — customers/vendors/employees/jobs share / # the same ``owner_guid`` namespace shape across four
- `1624-1628` KEEP — # Job annotation: append ``(job:XXXXXX)`` to the owner / # column so a bookkeeper scanning a long invoice list
- `1632-1634` KEEP — # Total: sum of (quantity * price) across entries. Falls back / # to "?" when entries can't be loaded — keeps the row legible
- `1737-1739` KEEP — # Tax fields conditional on taxable=1 — non-tax entries / # keep their byte-identical pre-v1.3 shape so existing
- `2013-2015` KEEP — # Taxtable resolution is cached so an invoice with many / # lines sharing the same taxtable hits SQL once per
- `2027-2030` KEEP — # Defensive: ``i_taxtable``/``b_taxtable`` points at / # a missing row. Entry creation validates this; if
- `2088-2091` KEEP — # Tax-payable accounts get their per-entry components. / # Composite taxtables that route to the same account
- `2104-2107` KEEP — # Per-taxtable rollup: when this line was tax-bearing, / # attribute its total tax to the source taxtable so
- `2219-2221` KEEP — # Address sub-fields piecash exposes on the Address record. Used / # both to validate caller-supplied dict keys and to iterate during
- `2346-2349` KEEP — # Stage pre-update state for the audit log. Mirrors the / # update_account precedent — the audit decorator reads
- `2405-2412` KEEP — # piecash's ``Address`` is a composite view over raw / # ``addr_*`` columns on the Customer / Vendor / Employee
- `3163-3165` KEEP — # Residual adjustment: enforce gross = pretax + tax_total / # exactly. The residual is at most ±1 quantum from the
- `3259-3261` KEEP — # Capture before-close to avoid DetachedInstanceError / # on attribute access after ``book.save()`` exits the
- `3421-3423` KEEP — # Capture before-state for the audit log (entries / # snapshot serialized eagerly so detach doesn't bite
- `3475-3480` KEEP — # Replacing via slice assignment relies on the / # entries relation's ``cascade="all, delete-orphan"``
- `3489-3495` KEEP — # Flush so the new entries' ``account_guid`` FK and / # autoincrement ``id`` columns are populated before
- `3596-3602` KEEP — # ── Invoice / Bill creation, posting, payment ───────────────── / #
- `3637-3639` KEEP — # Response ``type`` field — lowercase, used by the audit log / # decorator to swap entity_type for the post/pay/unpost
- `3642-3644` KEEP — # Compact-line type tag (short, all-caps). Invoices stay "INV" / # for backward compat with existing render tests; bills /
- `3740-3758` KEEP — # ── Credit-note slot helpers ───────────────────────────────── / #
- `3890-3899` KEEP — # Job linkage: when ``job_id`` is given, the invoice / # is grouped under a Job. The job must (a) exist,
- `4043-4046` KEEP — # When linked to a job, the invoice's / # polymorphic owner pointer routes to the
- `4067-4072` KEEP — # Apply caller-supplied slots BEFORE save so the slot / # writes and the row insert land in a single
- `4224-4233` KEEP — # ── Credit-note resolution ───────────────────────────────── / #
- `4261-4264` KEEP — # Employee credit notes are deliberately excluded — / # the create path rejects them, but a caller could
- `4401-4403` KEEP — # Validate + capture data from the source invoice if linked. / # This is a read-only lookup so it doesn't conflict with
- `4455-4457` KEEP — # Currency rules: / #  - If caller passed currency, it must match source's.
- `4489-4492` KEEP — # Augment the standard response with credit-note keys. / # The owner_id key from _create_business_document is
- `4552-4556` KEEP — # Delegate to _add_entry with the resolved owner_type. / # The helper's allowed-account-type validation is the same
- `4567-4569` KEEP — # Surface the credit_note_id key in the response (the / # base helper returns invoice_id / bill_id based on
- `4611-4613` KEEP — # Re-key the response: base returns ``type`` of / # ``invoice``/``bill``; for a credit note the truth is
- `4617-4619` KEEP — # owner_type → per-document config table for ``_add_entry``. / # Same dispatch idiom as ``_BUSINESS_DOC_CONFIG`` for create —
- `4647-4652` KEEP — # Voucher entries route through the SAME ``b_*`` columns / # and ``bill`` FK as vendor bills — GnuCash's schema
- `4717-4721` KEEP — # Both vendor bills (4) and employee expense vouchers (5) / # route through the ``b_*`` column group and the ``bill``
- `4774-4777` KEEP — # Resolve the taxtable upfront so a missing-taxtable / # error fires before any write. The resolved Taxtable
- `4790-4799` KEEP — # The Entries table has parallel ``i_*`` (invoice side) / # and ``b_*`` (bill side) column groups. Exactly one
- `4851-4855` KEEP — # Refcount maintenance: GnuCash desktop reads / # Taxtable.refcount to know whether a taxtable is in
- `5024-5030` KEEP — # job_id filter trumps owner_type for the SQL query — / # job-attached invoices have owner_type=3 regardless
- `5176-5182` KEEP — # Build taxtable_guid → name map only for taxtables / # actually referenced by this invoice's entries — skip
- `5209-5214` KEEP — # Tax summary: compute via the seam so the math / # matches what posting would produce. Empty entries
- `5243-5245` KEEP — # Customer-facing total uses the seam's / # grand_total (gross). Non-tax invoices fold to
- `5260-5265` KEEP — # Resolve applies_to for credit notes — the source / # link is stored as a GUID slot; we render it as the
- `5267-5272` KEEP — # Resolve job linkage for job-attached invoices / # (owner_type=3 means owner_guid points at a Job).
- `5290-5303` KEEP — # Forward signal: when this invoice has discount terms, / # surface the eligible-until date and the dollar amount
- `5421-5423` KEEP — # FX freshness guard. The 90-day cap already excluded / # anything further out, so age_days here is <= cap; this
- `5627-5637` KEEP — # Build transaction splits / # For customer invoice: A/R debit (positive), income credit (negative)
- `5720-5724` KEEP — # When the credit-note flag is set, surface / # ``type='credit_note'`` so the audit log
- `5735-5739` KEEP — # Transaction + lot GUIDs are USED by consumers / # (e.g. bookkeeper passes transaction_guid to
- `5750-5752` KEEP — # Surface the worst-aged forced override (if any) as a / # single fx_stale block — the common case is one foreign
- `5815-5818` KEEP — # Capture before-state for the audit log: the user wants / # to see what they unposted ("was posted: 2026-04-01,
- `5841-5852` KEEP — # Reject when there are *live* (non-voided) payment splits / # in the lot. The lot starts with one split — the A/R
- `5871-5874` KEEP — # Clear the invoice's posted-state pointers BEFORE / # deleting the underlying transaction/lot. Otherwise the
- `5881-5887` KEEP — # Capture the credit-note flag BEFORE we delete the / # posting transaction / lot. Post-save, slot reads on
- `5892-5894` KEEP — # Delete the posting transaction (which cascades its / # splits) and the lot (now empty after the inv.post_lot
- `6147-6152` KEEP — # Guard against extreme-rate-quantize-to-zero. With a / # tiny exchange rate (< quantum / payment_amount) the
- `6166-6171` KEEP — # Credit-note refund direction reverses: paying a / # customer credit note means SENDING cash to the
- `6224-6226` KEEP — # Two reasons _compute_discount_summary returns / # None: no billterm linked, or billterm has no
- `6265-6272` KEEP — # Use the CURRENT remaining lot balance as the / # principal to settle — not the original grand_total.
- `6318-6322` KEEP — # All validation passed — resolve the discount account / # and build the split. Quantize to the discount
- `6344-6348` KEEP — # When discount applies, the A/R/A/P side absorbs the / # FULL remaining_balance — not just payment_amount.
- `6387-6391` KEEP — # Pay vendor bill (or refund customer credit note): / # debit A/P (positive), credit bank (negative). When
- `6406-6409` KEEP — # Receive customer payment (or refund-in from a / # vendor credit note): credit A/R (negative),
- `6424-6436` KEEP — # Realized FX gain/loss: when cross-currency and the rate / # moved between post-date and pay-date, the actual amount
- `6443-6447` KEEP — # FX gain/loss direction follows the effective / # is_bill of THIS payment — a customer credit
- `6515-6517` KEEP — # Transaction GUID emitted as a short prefix — / # consumers (e.g. get_transaction lookup) accept
- `6545-6547` KEEP — # Amount on the FX account is in the book's / # default currency — that's the account's
- `6560-6564` KEEP — # Surface what was booked so the caller can confirm the / # discount applied without re-reading the transaction.
- `6771-6774` KEEP — # Lot balances. Customer-side: target_lot is positive / # (receivable), cn_lot is negative (credit). Vendor
- `6831-6836` KEEP — # Build the netting transaction: / #   Customer side: cn_lot gets +apply_amount (settles
- `6884-6886` KEEP — # Mark txn as a payment-type transaction (GnuCash UI / # convention) so desktop knows to render it under
- `6889-6892` KEEP — # Close lots that reached zero. A fully-settled credit / # note (apply == cn_remaining) closes its lot. A
- `6906-6909` KEEP — # Quantize all amounts to the post-account's commodity / # so the response shape stays consistent regardless
- `7018-7021` KEEP — # Entry cleanup — count first so the response can report how / # many were removed, then delete via SQLAlchemy Core. A column
- `7028-7036` KEEP — # Refcount maintenance: before deleting tax-bearing / # entries, tally per-taxtable counts and decrement
- `7183-7186` KEEP — # Slot cleanup via SQLAlchemy Core. Slots on business-person / # rows (notes, tax info, etc.) have no ON DELETE CASCADE
- `7409-7413` KEEP — # The Job constructor handles owner_type, owner_guid, / # and (on book.add) the auto-id assignment. The
- `7423-7425` KEEP — # Verify the row landed (consistency with the rest of / # the business module's "every write is verified"
- `7474-7476` KEEP — # Symmetric with create_job — surface the constraint / # at the filter boundary too rather than silently
- `7565-7567` KEEP — # Linked invoices: owner_type=3 + owner_guid=job.guid / # is the polymorphic linkage. Sort by ID for stable
- `7692-7696` KEEP — # Re-parent: rewrite owner_type/owner_guid on / # each linked invoice to point at the customer
- `7881-7885` KEEP — # Credit-note row: type stays as the owner-type / # tag ('invoice' / 'bill'), but the is_credit_note
- `7976-7978` KEEP — # Lot-balance direction for every invoice linked to this / # job follows the job's side: A/R lots run positive, A/P
- `7995-7998` KEEP — # Face value: sum of (qty * price) across entries. / # Falls back to 0 on entry-load failure rather
- `8037-8039` KEEP — # Defensive: lot not found despite / # posted state — treat as fully owed
- `8153-8155` KEEP — # Filter by date range. ``_safe_invoice_date`` returns / # None for records where date_posted is missing or
- `8166-8170` KEEP — # FX chokepoint: bills whose currency has no rate on file are / # NOT folded into the default-currency totals (that silently
- `8218-8225` KEEP — # Convert each per-bill total from the bill's own / # currency to the book default before summing. A
- `8240-8243` KEEP — # No rate on file: exclude from the default- / # currency totals rather than summing raw
- `8288-8290` KEEP — # Warn (once per currency) about bills excluded from the / # default-currency totals for lack of a rate, naming the raw

## src/gnucash_mcp/book/core.py

### core.py:56-70 | REWRITE | rule 5
lead with what the collector does; compress 'Before this was consolidated… four separate helper calls / 80-150 ms' history to the single-pass rationale

```
    """Signals gathered in a single pass over ``book.transactions`` for
    the ``create_transaction`` preflight and post-write checks.

    Before this was consolidated, ``create_transaction`` made four
    separate helper calls — each opening the book, scanning the full
    transaction list, and producing one signal. That's ``~4 × (open +
    O(N))`` per create. On a 10k-txn book the four opens alone cost
    80–150 ms, plus ~40k iterations for the scans.

    Collecting everything in one pass turns the hot path into a single
    book-open and a single sort + O(N) traversal. Signals are opt-in
    via ``want_*`` flags so the collector does only the work each call
    actually needs (e.g., skip duplicate detection when
    ``check_duplicates=False``).

```

### core.py:102-121 | REWRITE | rule 4
strip 'R-1:'; internal-shape warning stays; compress 'previously used inline'

```
@dataclass
class _SummaryData:
    """Categorized account-balance + roll-up data for
    ``get_book_summary``.

    Populated in one walk over ``book.accounts`` by
    ``_collect_summary_balance_sheet`` so the multi-pass shape that
    ``get_book_summary`` previously used inline collapses into a
    single collector → many renderers pipeline.

    All ``Decimal`` totals are pre-rounded to 2 dp (consistent with
    every place ``get_book_summary`` displays them); per-leaf
    balances are stored at native precision so renderers can
    re-round if they want.

    R-1: the dataclass is internal — no caller outside
    ``get_book_summary`` and its renderers should depend on the
    field shape, since the renderers will continue evolving as
    the bookkeeper review surfaces new section requirements.
    """
```

### core.py:173-186 | REWRITE | rule 2
restate 'The bookkeeper's principle' as the design principle itself ('tell the LLM what needs attention, not what exists')

```
    def _business_summary_counts(self, book) -> dict:
        """Action-signal counts for the get_book_summary business
        lines: open invoices, open bills, how many overdue, and
        active jobs. Returns zeros when BusinessMixin isn't loaded
        (helpers like ``_calculate_lot_balance`` then come back
        ``None``) — the rendering layer omits the lines anyway
        when there's no Receivables/Payables activity.

        The bookkeeper's principle for the summary: "tell the LLM
        what needs attention, not what exists." Invoice counts
        and overdue counts are actionable; account counts are
        structural and already shown via the nested per-account
        breakdown below each line.
        """
```

### core.py:208-218 | REWRITE | rule 2/3
pre-index N+1 rationale stays; cut 'The pre-fix loop did one SQL query per invoice…' and 'Copilot flagged it'

```
        # and credit notes / payments reduce the count correctly.
        #
        # Pre-index accounts and lots once, not per-invoice — this
        # method runs inside get_book_summary on every dashboard
        # call. The pre-fix loop did one SQL query per invoice to
        # resolve the post account, then a linear scan of
        # post_acct.lots to find the matching lot. On a book with
        # 100 posted invoices that's 100 round-trips plus 100
        # linear scans against potentially hundreds of lots each —
        # noticeable latency on every summary call. Copilot
        # flagged it; pre-indexing is the standard N+1 fix.
```

### core.py:241-245 | REWRITE | rule 4
strip 'MP-12:'; swallow-and-log rationale stays

```
                # MP-12: swallow ORM hiccups (detached instance,
                # missing account/lot) so summary signals survive
                # partial corruption; log so the underlying cause
                # can be investigated when --debug is on.
                _debug_logger.debug(
```

### core.py:251-257 | REWRITE | rule 2
credit-notes-never-age invariant stays; cut 'which the live-test signoff flagged this surface as contradicting'

```
            # Credit notes stay in the OPEN counts (they're open
            # documents awaiting application/refund) but never age
            # into the overdue counts — they're money the business
            # OWES, with no past-due concept. Matches
            # get_outstanding_invoices, which the live-test signoff
            # flagged this surface as contradicting.
            is_credit_note = False
```

### core.py:275-277 | REWRITE | rule 4
strip 'MP-12:'

```
                    # MP-12: due-date resolution can fail on
                    # corrupt term records; surface in debug log.
                    _debug_logger.debug(
```

### core.py:307-309 | REWRITE | rule 4
strip 'MP-12:'

```
            # MP-12: jobs table may not exist on very old books;
            # log and continue.
            _debug_logger.debug(
```

### core.py:330-335 | REWRITE | rule 3
restate 'The single-int count this replaces was operationally useless' as the design rationale for per-account shape

```
        The single-int "N unreconciled" count this replaces was
        operationally useless: it included income/expense/equity
        splits that conceptually can't be reconciled, so the number
        was misleadingly large and gave the LLM no actionable signal
        about which accounts had drifted from reality.

```

### core.py:373-391 | REWRITE | rule 4
strip 'Post-HP-8' framing; single-sweep + chokepoint + scope notes stay

```
            # Single pass over splits — derive everything we need:
            #   - latest_y_date (most recent reconciled split)
            #   - has_yc (any 'y' or 'c' for the ASSET gate)
            #   - any_splits (used vs. unused account)
            #   - unreconciled_count (total non-y, non-voided)
            #   - oldest_unreconciled_date (oldest pending work)
            # Post-HP-8 the count is state-only (doesn't depend on
            # ``latest_y_date``), so it folds into the existing
            # sweep — half the splits work per dashboard call.
            # ``_is_unreconciled`` is the chokepoint shared with
            # ``get_unreconciled_splits`` so the dashboard count
            # and the detail tool agree by construction.
            #
            # Chokepoint scope note: ``_is_unreconciled`` is the
            # as-of-today predicate. ``get_unreconciled_splits``
            # accepts a separate ``as_of_date`` arg for historical
            # tie-outs; the dashboard intentionally has no such
            # parameter (it's the morning-check surface, not the
            # reporting surface). Documented at the helper.
```

### core.py:398-400 | REWRITE | rule 4
strip '(A8)'

```
                # Voided splits are zombies, not reconcilable
                # activity (A8) — they must not make an account
                # surface in the reconciliation section.
```

### core.py:416-418 | REWRITE | rule 4
strip '(A9)'

```
                    # Null post_date (old-book artifact, A9) still
                    # counts as backlog; it just can't anchor the
                    # oldest-date lag display.
```

### core.py:477-488 | REWRITE | rule 3
must-agree-with-balance_sheet invariant stays; compress the both-were-wrong-in-the-same-direction history

```
    # Asset-side and liability-side type sets used by the net-worth
    # trajectory in get_book_summary. RECEIVABLE and PAYABLE belong
    # here despite having dedicated dashboard sections elsewhere in
    # the summary — accounting-wise, A/R is an asset and A/P a
    # liability, so the trajectory's "now" anchor (and every past-
    # anchor reconstruction) must include them to agree with
    # balance_sheet and net_worth. Pre-v1.3.0 these were excluded
    # here while balance_sheet excluded them too; both were wrong in
    # the same direction, so the cross-tool numbers happened to
    # agree. Fixing balance_sheet (v1.3) forces this set to follow
    # so the dashboard's headline net worth doesn't drift from the
    # canonical balance-sheet identity.
```

### core.py:500-518 | REWRITE | rule 2/4
algorithm description stays; cut 'the (former) bottom-line has since been retired' history and '(adversarial pass 2, C1)'

```

        Single source of truth for the net-worth number the summary
        displays. Trajectory's "now" anchor and the (former) bottom-
        line "Net worth:" line both agreed-by-construction when both
        existed; the bottom-line has since been retired in favor of
        the trajectory's "now", but this helper preserves the
        semantics so the user's reference number stays the same.

        Algorithm mirrors the per-account breakdown elsewhere in
        ``get_book_summary``:

        - **Own-splits-per-account.** Every account contributes the
          sum of its *own* splits. There is no roll-up in this code,
          so a parent's (or placeholder's) direct splits are real
          money not represented by any other row — skipping them
          drops it. Parents and placeholders without direct splits
          contribute zero and fall out via the ``balance == 0``
          check. Shared rule with ``balance_sheet`` and ``net_worth``
          in ``book/reporting.py`` (adversarial pass 2, C1).
```

### core.py:543-554 | REWRITE | rule 2
forecast-prices convention stays; reword 'the bookkeeper has deliberately written' → user-generic

```
        # "Now" anchors (as_of >= today) use the absolute latest price
        # on file — including any future-dated forecasts the bookkeeper
        # has deliberately written. Past anchors filter to prices
        # observed by the anchor date (historical reconstruction).
        # See the comment in get_book_summary's inline price loop for
        # the rationale; both paths converge on this behavior so the
        # "now" anchor agrees with balance_sheet by construction.
        # ``_rates_as_of`` runs ``as_of`` through ``_anchor_for_as_of``,
        # which folds now-or-future anchors to ``date.max`` so the
        # bookkeeper's intentional future-dated forecasts are
        # included in "now" valuations. Past anchors stay literal
        # for historical reconstruction.
```

### core.py:685-707 | REWRITE | rule 2
ASSET-overcounts trap stays with one-line consequence; cut 'The bookkeeper hit this on Alex's book: condo ($473K) / vehicle ($28K) / 768 vs 116 days' case study

```
    # runway even if they're wealth.
    #
    # The spec originally proposed including ASSET-typed accounts
    # whose commodity is the book default ("cash-equivalent ASSET")
    # as a heuristic for catching brokerage cash and escrow. In
    # practice GnuCash's ASSET type is structurally for fixed assets
    # — users code real estate, vehicles, and similar wealth as
    # ASSET in default currency, and that heuristic over-counts.
    # The bookkeeper hit this on Alex's book: a USD-default condo
    # ($473K) and vehicle ($28K) added $501K of "liquid" that Alex
    # cannot use to make payroll next week. 768 days of runway
    # ("Alex is fine for two years") vs. 116 days ("Alex has four
    # months to collect receivables or restructure") is a very
    # different conversation.
    #
    # Cleaner rule, observed across actual user books: BANK, CASH,
    # STOCK, MUTUAL. Brokerage positions (STOCK/MUTUAL) ARE liquid
    # — they're sellable in a day at market price. Real fixed
    # assets (ASSET-typed) are not. Users who legitimately have a
    # cash-equivalent ASSET (HSA, prepaid USD) can recategorize it
    # as BANK and it will count; the structural type is honored as
    # the source of truth.
    _RUNWAY_LIQUID_TYPES = frozenset({"BANK", "CASH", "STOCK", "MUTUAL"})
```

### core.py:755-761 | REWRITE | rule 2
'the conversation Robin needs to have today' — persona name; reword generic

```

        Within each category, most-severe / most-overdue first.
        The category ordering puts operational urgency (cash
        flow signals) above data-quality concerns: a near-empty
        bank account or unpaid receivable is the conversation
        Robin needs to have today; stale prices are next-week
        cleanup.
```

### core.py:815-829 | REWRITE | rule 4
strip 'MP-2:'; per-currency display vs converted sort-key rationale stays

```
        # ── 1. Data integrity: Imbalance / Orphan accounts ──
        # MP-2: each Imbalance-{ccy}/Orphan-{ccy} account is its
        # own commodity, so the per-account balance is correct in
        # its own currency — that's the right display unit
        # ("Imbalance-EUR: 234.56" tells the user the defect is
        # 234.56 EUR). But the sort across DIFFERENT Imbalance
        # accounts compared raw quantities, which is wrong on a
        # multi-currency book — a 5 USD defect could sort above
        # a 200 CNY defect (raw 200 > 5) even though 200 CNY is
        # materially larger in USD terms. Sort key now goes
        # through default-currency conversion so the biggest
        # *defect* surfaces first regardless of denomination.
        rates_for_sort = self._rates_as_of(
            book, today, default_currency,
        )
```

### core.py:888-891 | REWRITE | rule 4
strip '(C5)'

```

                    # "Now" warning: cap at today (C5) so a future-
                    # dated deposit can't suppress a real low-cash
                    # alarm (or a future payment fire a premature one).
```

### core.py:959-967 | REWRITE | rule 3
credit-note exemption stays; compress 'this surface said 238 days past 30-day default about the same document'

```
                        # Credit notes never belong in a past-due
                        # warning: their balance is money the
                        # business OWES (available to apply or
                        # refund), and an aging clock on it reads
                        # as a collections item.
                        # get_outstanding_invoices already exempts
                        # them; this surface said "238 days past
                        # 30-day default" about the same document.
                        if get_is_cn is not None and get_is_cn(inv):
```

### core.py:1066-1083 | REWRITE | rule 3/4
filter-before-mark guardrail stays; strip 'HP-6:' and compress two 'Pre-fix' narrations

```
                    in_use.add(a.commodity.guid)

            # Single pass over book.prices builds both signals we
            # need: in-use commodities (every priced commodity is
            # in-use even if no account holds it) and the latest
            # market-price date per commodity. Pre-fix the method
            # iterated ``book.prices`` twice — once for ``in_use``,
            # once for ``by_commodity_latest`` — paying the ORM
            # hydration cost twice on a book with hundreds of prices.
            #
            # HP-6: ``in_use.add`` runs AFTER the
            # ``_is_market_price`` filter. Pre-fix a commodity that
            # only had piecash auto-placeholder prices (created on
            # cross-currency transactions) was marked in_use, and
            # the downstream "no price on file" warning misfired:
            # the placeholder isn't a real quote, the commodity
            # has no market price, but in_use says it's tracked.
            # Filter first, then mark.
```

### core.py:1183-1190 | REWRITE | rule 3
surface-failures-to-summary rationale stays; compress 'Pre-fix, the debug log was the only place this surfaced — and the bookkeeper doesn't read debug logs'

```
        # Surface auto-backup chain breaks. The single failure mode
        # this server most fears is data loss; an auto-backup that has
        # been silently failing for weeks turns into "you have no
        # recovery option" the day the book corrupts. Pre-fix, the
        # debug log was the only place this surfaced — and the
        # bookkeeper doesn't read debug logs. We render the warning
        # right next to integrity issues because backup health is
        # itself a data-safety concern.
```

### core.py:1271-1277 | REWRITE | rule 4
strip '(SB-9)' trailing tag; rollup semantics stay

```
        its own line stays out of the rollup so its actuals aren't
        double-counted (matches ``get_budget_report`` behavior;
        SB-9). The full budget report is a separate tool the LLM
        can call for category-level detail; the headline trades
        that detail for a single-line summary the LLM can reference
        proactively ("you're 11% over pace; want me to identify
        which categories are driving it?").
```

### core.py:1329-1334 | REWRITE | rule 4
strip '(SB-6, mirroring the get_budget_report fix in budgets.py)'; FX-convert-targets rationale stays

```
        # Sum budget targets across all (account, period) pairs and
        # also FX-convert each target to default currency at the
        # period-end rate. Pre-fix targets were summed raw — apples-
        # to-oranges against default-currency actuals on multi-
        # currency budgets (SB-6, mirroring the get_budget_report
        # fix in budgets.py).
```

### core.py:1351-1357 | REWRITE | rule 2/4
strip 'SB-9:' and 'PR #46 fixed this in get_budget_report; the dashboard headline was left behind'; keep mirrors-get_budget_report invariant

```
        # SB-9: roll descendants of placeholder-budgeted parents
        # into the rollup set so their splits contribute to actuals.
        # PR #46 fixed this in ``get_budget_report``; the dashboard
        # headline was left behind. A descendant that's separately
        # budgeted on its own line stays out of the rollup so its
        # actuals aren't double-counted toward both its own line
        # and its ancestor's line.
```

### core.py:1374-1382 | REWRITE | rule 3
FX-convert-actuals rationale stays; compress 'Pre-v1.3.0 a EUR expense account budgeted at $X contributed raw EUR quantities'

```
        # Each split is converted to the book's default currency at
        # the most recent market rate so foreign-currency budgeted
        # accounts contribute their default-currency-equivalent
        # spend, not raw quantity. Pre-v1.3.0 a EUR expense account
        # budgeted at $X contributed raw EUR quantities to the
        # actuals comparison, wildly miscalibrating used_pct.
        # Factors anchored to ``period_end`` so a historical budget
        # period values its actuals at the rate of that period —
        # not today's.
```

### core.py:1396-1401 | REWRITE | rule 2/4
signed-accumulation rationale stays; strip 'the a34867c pattern' and '(adversarial pass 2, C3)'

```
                # Accumulate SIGNED amounts so contra splits (expense
                # refunds, income clawbacks) net into the headline —
                # the a34867c pattern, mirrored from get_budget_report
                # (adversarial pass 2, C3). EXPENSE: positive = spend.
                # INCOME: stored negative; flip so revenue counts
                # positive toward the target.
```

### core.py:1448-1453 | REWRITE | rule 2
per-split FX conversion contract stays; cut 'Pre-v1.3.0… on Lin Wei (CNY-default with USD subscriptions)' persona provenance

```
        Each EXPENSE split is converted to the book's default
        currency at the most recent market rate. Pre-v1.3.0 this
        summed ``split.value`` raw, mixing currencies on books with
        foreign-currency expenses; on Lin Wei (CNY-default with
        USD subscriptions) the burn was understated by the USD
        component's spot-rate factor.
```

### core.py:1458-1464 | REWRITE | rule 4
book-age clamp guardrail (~10x runway overstatement) stays; strip 'SB-7'

```
        # SB-7 book-age clamp. The 180-day window is a MAX, not a
        # fixed denominator: dividing recent spend by 180 days on
        # a 19-day-old book over-stated runway by ~10×. Use the
        # actual book age when smaller. ``transactions`` is the
        # pre-materialized list ``get_book_summary`` builds; if
        # it's empty the function returns 0 below regardless, so
        # the fallback of 1 day avoids divide-by-zero.
```

### core.py:1467-1467 | REWRITE | rule 4
strip '(A9)'

```
            if t.post_date is not None  # old-book artifact (A9)
```

### core.py:1478-1478 | REWRITE | rule 4
strip '(A9)'

```
            if txn.post_date is None:  # old-book artifact (A9)
```

### core.py:1557-1569 | REWRITE | rule 2
retirement-exclusion rationale stays; cut 'The bookkeeper hit this on Alex's book: a $13,716 401k… 95 to 124 days'

```
            if self._is_in_retirement_subtree(account):
                # Retirement accounts (IRA, 401k, 403b, pension, etc.)
                # share BANK / STOCK / MUTUAL types with truly liquid
                # accounts but carry early-withdrawal penalties that
                # disqualify them from "if income stops today" runway.
                # The bookkeeper hit this on Alex's book: a $13,716
                # 401k under Assets:Investments:Retirement was being
                # counted as liquid, inflating runway from ~95 days
                # to 124. Filtering by ancestor-named-Retirement is
                # the structural-intent heuristic — fragile if a user
                # names the subtree "Tax-advantaged" instead, but
                # clean enough for the standard naming convention.
                continue
```

### core.py:1571-1575 | REWRITE | rule 3/4
cap-at-today guardrail stays; strip '(C5)' and compress 'Pre-fix the liquid pass summed unbounded'

```
            # "Now" surface: cap at today (C5). Pre-fix the liquid
            # pass summed unbounded while its own cost-basis fallback
            # below filtered future splits — a rent payment dated
            # +10 days moved runway while net_worth correctly didn't.
            balance = self._own_splits_balance(account, as_of=today)
```

### core.py:1661-1668 | REWRITE | rule 3
mixed-currency trap stays as one line; compress 'Pre-v1.3.0 this summed split.value raw… 9,200 of nothing-in-particular'

```
        Multi-currency handling: each split is converted to the
        book's default currency at the most recent market rate via
        ``_split_in_default_currency``. Pre-v1.3.0 this summed
        ``split.value`` raw across currencies — a EUR invoice
        for €4,200 and a USD invoice for $5,000 mixed as 9,200 of
        nothing-in-particular. Correct on USD-only books, silently
        wrong on any book with foreign-currency income/expense.
        """
```

### core.py:1670-1677 | FIX-FALSE | rule 6
'deferred to keep this commit focused on the dropped-default-as_of fix' — commit narration; restate the deliberate uniform-rates limitation present-tense

```
        # Single factors map applied uniformly across the 12-month
        # trajectory. Strictly per-month historical rates would
        # require the same per-boundary restructure SB-1 calls for
        # in net_worth's time series; deferred to keep this commit
        # focused on the dropped-default-as_of fix. Threading
        # ``today`` here documents the current uniform behavior
        # rather than hiding it behind the old default.
        factors = self._account_conversion_factors(book, today)
```

### core.py:1720-1721 | REWRITE | rule 4
strip '(A9)'

```
            if d is None:  # old-book artifact (A9)
                continue
```

### core.py:1797-1811 | REWRITE | rule 3
collector→renderer pattern description stays; compress 'Pre-extraction the render block was one long sequence'

```
    # ── Section renderers ─────────────────────────────────────────────
    #
    # Each ``_render_*`` helper consumes the data produced by its
    # paired ``_collect_*`` / ``_*_metrics`` / ``_*_headline`` /
    # ``_*_trajectory`` method and returns a ``list[str]`` of lines
    # to append to the summary (or ``[]`` to omit the section
    # entirely — absence-as-signal, per the spec).
    #
    # Pre-extraction the render block was one long sequence inside
    # ``get_book_summary``. Pulling each section into its own helper
    # mirrors the data layer's decomposition and makes adding or
    # modifying a section a one-method change instead of surgery
    # through the render block. Each helper is self-contained — no
    # cross-section state — so the rendering order in
    # ``get_book_summary`` becomes a one-glance read.
```

### core.py:1928-1930 | REWRITE | rule 4
strip 'L-1: inlined _format_monthly_net — single caller'; keep sign/format note

```
            # L-1: inlined ``_format_monthly_net`` — single caller.
            # Always shows explicit sign + thousands separator;
            # whole dollars (cents would noise up the summary).
```

### core.py:1959-1970 | REWRITE | rule 3/4
strip 'R-1:' and '~480 lines' history; pattern description stays

```
    # ── Summary collector / section renderers ────────────────────
    #
    # R-1: ``get_book_summary`` used to inline every section's data
    # collection and rendering — ~480 lines, mostly a long sequence
    # of ``lines.append(...)`` calls with intermixed totals work.
    # Decomposed so each section has a single owner.
    #
    # The pattern matches the already-extracted helpers
    # (``_render_reconciliation`` / ``_render_runway`` / etc.):
    # collector returns the data, renderer turns it into ``list[str]``
    # (or ``[]`` to omit the section). New sections become a single
    # method addition + one ``lines.extend(...)`` call.
```

### core.py:1981-1991 | REWRITE | rule 3
compress 'Pre-fix the same walk was inline in get_book_summary'

```
        """Single-pass account walker for ``get_book_summary``.

        Walks ``accounts`` once, building the categorized lists and
        running counters the renderers need. Pre-fix the same walk
        was inline in ``get_book_summary``; extracting it keeps the
        renderer signatures focused on what they consume rather
        than threading 15 collection variables through.

        Returns a ``_SummaryData`` with pre-rounded totals so
        renderers can format directly.
        """
```

### core.py:2004-2010 | REWRITE | rule 4
strip '(C1)'; own-splits/no-rollup rationale stays

```
            # Balance of the account's OWN splits in its own
            # commodity, today-filtered (future-dated transactions
            # excluded so the snapshot agrees with trajectory's "now"
            # anchor). Parents and placeholders are included: there is
            # no roll-up here, so direct splits on them are real money
            # no other row represents (C1). Accounts with no direct
            # splits fall out via the ``balance != 0`` checks below.
```

### core.py:2025-2031 | REWRITE | rule 3
FX-chokepoint rationale stays; compress 'Pre-fix this appended raw account-commodity quantity'

```
            elif account.type == "CREDIT":
                if balance != 0:
                    # FX chokepoint: convert via the same rate map as
                    # assets/AR/AP, then negate the credit-natural
                    # balance to a positive magnitude. Pre-fix this
                    # appended raw account-commodity quantity, diverging
                    # from balance_sheet/net_worth on foreign debt.
```

### core.py:2121-2133 | REWRITE | rule 2
'bookkeeper-asked-for because' → state the rationale directly (reconcile-vs-catch-up pivot)

```
        """Render Book / Currency / Data range / Last entry header.

        ``Last entry`` carries a staleness signal — bookkeeper-asked-for
        because the answer to "let's reconcile" vs "let's enter 200
        transactions first" pivots on it. Four cases keyed on
        ``(today - last_date).days``:

        - ``< 0``  → future-dated (normal for scheduled-txn ahead-of-
          today posting). ``(future-dated, N days ahead)``.
        - ``= 0``  → today.
        - ``= 1``  → yesterday.
        - ``> 1``  → N days behind. ⚠ past ``_LAST_ENTRY_WARN_DAYS``.
        """
```

### core.py:2425-2430 | REWRITE | rule 4
strip 'R-1:' from get_book_summary docstring; orchestrator-only note stays

```

        R-1: orchestrator only. Data collection and per-section
        rendering live in dedicated helpers (``_collect_*`` /
        ``_render_*``); see the section-renderers block above.
        Adding a new section is a single method addition plus one
        ``lines.extend(...)`` call here.
```

### core.py:2442-2458 | REWRITE | rule 2
today-filter + forecast-prices conventions stay; cut 'bookkeeper hit this on Alex's book… $2,906 gap' and de-personalize 'The bookkeeper writes future-dated yfinance close prices'

```
            # Balances are computed as-of-today: future-dated
            # transactions are excluded so the displayed Assets /
            # Liabilities totals agree with trajectory's "now" by
            # construction. Without this filter, future-dated
            # transactions in the book would skew the current
            # snapshot — bookkeeper hit this on Alex's book where
            # 34 days of data past today produced a $2,906 gap
            # between Assets-Liabilities and trajectory.
            #
            # Prices are NOT today-filtered. The bookkeeper writes
            # future-dated yfinance close prices intentionally as
            # forecasts the displays should track; balance_sheet
            # uses the absolute latest, and this summary now
            # matches by construction. ``_compute_net_worth_at``
            # (above) special-cases ``as_of >= today`` to use the
            # same all-prices lookup so the trajectory "now"
            # anchor agrees here too.
```

### core.py:2466-2469 | REWRITE | rule 2
materialize-once rationale stays; cut 'CODE_REVIEW noted 7-10 passes'

```
            # Materialize the account list once. CODE_REVIEW noted
            # 7-10 passes over ``book.accounts`` between this method
            # and the sub-helpers it calls; threading the in-memory
            # list collapses each pass.
```

### core.py:2477-2480 | REWRITE | rule 4
strip '(issue #94)'

```
            # Provenance for intermediate-chain-derived rates (issue
            # #94) so a synthesized valuation renders "@ rate (via
            # USD)" instead of an unfamiliar opaque number.
            rate_via = self._rate_provenance(book, today, default_currency)
```

### core.py:2492-2499 | REWRITE | rule 3
restate 'Per-split unreconciled counting was dropped from this surface' present-tense (the Reconciliation section is the actionable surface)

```
            # Transaction stats. Per-split unreconciled counting
            # was dropped from this surface — the Reconciliation
            # section below (per-account) is the actionable
            # replacement. SX template recipes are filtered: a
            # desktop-created template dated years before the first
            # real entry would otherwise stretch the activity range
            # and inflate the count (this list also feeds
            # _collect_warnings and the daily-burn clamp).
```

### core.py:2509-2509 | REWRITE | rule 4
strip '(A9)' tag

```
                if d is None:  # old-book artifact (A9)
```

### core.py:2568-2571 | REWRITE | rule 2
'the bookkeeper said…' quote → absence-as-signal rationale stated directly

```
            # Jobs: single conditional line. Absence-as-signal —
            # the bookkeeper said "jobs existing doesn't need
            # attention but knowing they're in play points to the
            # right drill-down tool."
```

### core.py:2861-2867 | REWRITE | rule 2
verbose-short-prefix rationale stays; compress 'pre-v1.3.1 verbose left them at full 32-char width'

```
                # Verbose mode: emit short prefixes for transaction
                # GUIDs, split GUIDs, and lot GUIDs so the bookkeeper
                # workflow doesn't pay 24 wasted chars per GUID per
                # row. Compact mode already does this; pre-v1.3.1
                # verbose left them at full 32-char width even
                # though every consuming tool accepts 8+ char
                # prefixes via ``_resolve_guid``.
```

### core.py:2990-3001 | REWRITE | rule 5
lead with the single-pass contract; compress 'The four original helpers (…, since deleted) each opened the book'

```
        The four original helpers (``_auto_fill_splits``,
        ``_check_auto_fill_stability``, ``_find_duplicates``, and a
        post-write recent-description matcher, since deleted) each
        opened the book and did its own full-table scan. This collector folds all
        of them into one sort + one traversal, classifying each
        transaction into whichever signal bucket(s) it matches.

        Callers supply ``want_*`` flags so the collector only does work
        the caller will actually consume — e.g., auto-fill has no
        meaning when the caller already provided splits, and duplicate
        detection is skipped when ``check_duplicates`` is False.

```

### core.py:3102-3110 | REWRITE | rule 4
voided-txns-not-signal-sources guardrail ($0 auto-fill clone) stays; strip '(C8)'

```
            # Voided transactions are not signal sources (C8): the
            # void-and-re-enter workflow this server recommends makes
            # the voided txn the most recent description match, and
            # auto-fill would clone its zeroed splits into a silent
            # $0 transaction that passes validation. Duplicates /
            # stability buckets skip them for the same reason — a
            # voided entry is not evidence the event happened.
            if any(_is_voided(s) for s in txn.splits):
                continue
```

### core.py:3112-3114 | REWRITE | rule 4
strip '(A9)'

```
            # Undated rows can't anchor cadence or duplicate-window
            # math (A9).
            if txn.post_date is None:
```

### core.py:3117-3123 | REWRITE | rule 2
empty-description-matches-everything guardrail stays; cut '(bookkeeper live-test blocker, pass-2 signoff)'

```
            # Empty/whitespace descriptions carry no match signal:
            # "" is a substring of everything, so an empty-description
            # transaction would otherwise desc-match EVERY proposed
            # description — and when nothing real matched, auto-fill
            # would clone that unrelated transaction under the caller's
            # description instead of raising "no match found"
            # (bookkeeper live-test blocker, pass-2 signoff).
```

### core.py:3163-3175 | REWRITE | rule 3
primary-amount-vs-any-to-any guardrail + paycheck example stay; restate 'Earlier iterations compared' as conditional present

```
                #
                # Earlier iterations compared any-to-any across every
                # split pair. On multi-split transactions (paychecks
                # with 10+ deduction splits) that produced
                # false-positive MEDIUM matches whenever a tiny
                # deduction happened to land within ±$1 of a
                # candidate's amount — e.g. a paycheck-vs-coffee-shop
                # match because some $5-ish union/medicare deduction
                # sat near the coffee's $5.67 total. "Primary" means
                # the headline number a human reading the register
                # uses to recognize a transaction; matching on that
                # kills the noise without losing real duplicates
                # (paycheck-vs-paycheck still matches on gross).
```

### core.py:3269-3273 | REWRITE | rule 2
TSV-size rationale stays; reword 'A list-of-dicts JSON response to the bookkeeper was ~120 chars' generic

```
        A list-of-dicts JSON response to the bookkeeper was
        ~120 chars per candidate; the TSV form is closer to 40. The
        rejection path emits two or three candidates typically, and
        the savings compound when the LLM retries a mis-hit.

```

### core.py:3365-3372 | REWRITE | rule 3
validate-before-mutate guardrail stays; compress 'Pre-extraction, update_transaction interleaved these checks'

```
        Pre-extraction, ``update_transaction`` interleaved these
        checks with the mutation loop — the first split would have
        ``split.value`` reassigned BEFORE the sibling split's
        cross-currency quantity was validated, so a bad-input
        update could leave the transaction in a partial state if
        the session didn't rollback cleanly. Validating everything
        up-front makes the subsequent mutation pass effectively
        infallible.
```

### core.py:3851-3863 | REWRITE | rule 4
strip '(MP-14)'; chokepoint + corruption rationale stay

```
    def _validate_account_name(name: str) -> None:
        """Validate a user-supplied account name (MP-14).

        ``:`` is the path separator (``Expenses:Groceries``); a name
        containing it corrupts every downstream ``fullname.split(":")``
        traversal. Control characters (\\x00-\\x1f, \\x7f) round-trip
        badly through SQLite text storage and the audit log's text
        rendering. Empty / whitespace-only names aren't user-meaningful
        even if piecash would accept them. Shared chokepoint for
        create_account and update_account's rename branch so both entry
        points enforce the same rule. Raises ``ValueError`` on any
        violation.
        """
```

### core.py:3918-3920 | REWRITE | rule 4
strip 'MP-14:'

```
        # MP-14: validate the account name (shared chokepoint with
        # update_account's rename branch).
        self._validate_account_name(name)
```

### core.py:4044-4047 | REWRITE | rule 4
strip 'MP-14:'; unguarded-parallel-entry-point rationale stays

```
                # MP-14: same name validation as create_account — the
                # rename path was an unguarded parallel entry point
                # (':' / control chars / empty would corrupt fullname
                # parsing downstream).
```

### core.py:4189-4198 | REWRITE | rule 3
no-short-guid-on-delete guardrail stays (handle to a deleted row invites misuse); compress 'Pre-fix the response included…'

```
            # Capture info before deletion. Pre-fix the response
            # included a short-prefix GUID computed against
            # ``book.accounts`` BEFORE the delete — but the LLM
            # would receive a handle pointing at a row that no
            # longer exists. ``_resolve_guid`` would then raise
            # "No account" on any subsequent attempt to use it.
            # Returning ``fullname`` and ``status="deleted"`` is
            # enough for the audit-log human reader and the LLM to
            # confirm what was deleted; the short GUID was always
            # unaddressable post-delete and just invited misuse.
```

### core.py:4286-4303 | REWRITE | rule 3
piecash-silent-setattr-no-op rationale + CLAUDE.md pointer stay; compress 'Pre-fix, both methods called book.save() and trusted the result'

```
        expected_date: date | None = None,
        expected_notes: str | None = None,
        expected_splits: list[dict] | None = None,
    ) -> None:
        """Re-load the transaction from disk and verify expected
        fields landed. Closes the gap CLAUDE.md's "Every write is
        verified" invariant calls out for ``update_transaction`` and
        ``replace_splits``.

        Pre-fix, both methods called ``book.save()`` and trusted the
        result. piecash has historically silently no-op'd setattrs
        on some slot-backed fields; without this round-trip the
        thin response could lie about what's stored. Bypasses the
        ORM identity map via ``session.expire`` so we read what's
        actually on disk, not what we just put in the cache.

        Raises RuntimeError on any mismatch.
        """
```

### core.py:4344-4348 | REWRITE | rule 3
multiset-vs-dict guardrail stays; restate 'Keying by fullname alone collapsed' as conditional present

```
            # Multiset of actual splits per account. Keying by fullname
            # alone collapsed two splits to the SAME account (legal via
            # replace_splits) — the second overwrote the first, leaving
            # its value unverified. A per-account list, consuming one
            # entry per matched expected split, verifies every split.
```

### core.py:4356-4362 | REWRITE | rule 2
resolve-before-compare rule stays; cut '(Bookkeeper finding from PR #75 review: …)'

```
            for expected in expected_splits:
                # Normalize the input account ref to canonical
                # fullname before lookup. The book methods accept
                # full path, ``%short`` GUID, or full 32-char GUID;
                # post-save splits are keyed by ``Account.fullname``.
                # (Bookkeeper finding from PR #75 review: a shortcut
                # ref like ``%77b59dd`` must resolve before comparison.)
```

### core.py:4452-4458 | REWRITE | rule 4
voided-immutable + void-slot-overwrite rationale stays; strip '(C8)'

```
            # Voided transactions are immutable (C8). Writing values
            # into state='v' splits is the partial-void corruption
            # generator: the amounts move balance sums while staying
            # invisible to cash_flow / lots / reconciliation, and a
            # later re-void overwrites the void-former-* slots with
            # the new values, destroying the originals. No force
            # override — the legitimate path is unvoid first.
```

### core.py:4499-4507 | REWRITE | rule 3
duplicate of the validator's own story; compress to one pointer at _validate_transaction_splits

```
                # Validate everything up-front via the shared validator:
                # sum-to-zero, account resolution (path / %short / full
                # GUID), cross-currency quantity/sign. Pre-extraction
                # the validation interleaved with mutation — the first
                # split's ``value`` was reassigned before the sibling's
                # cross-currency quantity was checked, so a bad-input
                # update could leave the transaction in a partial state
                # if the session didn't rollback cleanly. Now the
                # mutation pass below is effectively infallible.
```

### core.py:4543-4548 | REWRITE | rule 3
CLAUDE.md invariant pointer stays; compress 'pre-fix, update_transaction skipped this'

```
            # Verify the write landed — re-read the transaction from
            # disk and compare each field we tried to set. Honors the
            # ``Every write is verified`` invariant CLAUDE.md spells
            # out; pre-fix, ``update_transaction`` skipped this and a
            # piecash silent setattr no-op would have shipped a thin
            # response that lied about what was stored.
```

### core.py:4649-4650 | REWRITE | rule 4
strip '(C8)'

```
            # 4a. Voided transactions are immutable (C8) — same
            # rationale as update_transaction; no force override.
```

### core.py:4754-4754 | CUT | rule 2
tombstone: 'methods moved to their mixins'

```
    # Reconciliation / reporting / budgets / scheduling / lots methods moved to their mixins.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `151-154` KEEP — # Account types where bank-statement reconciliation is meaningful. / # ASSET is added conditionally on a per-account basis when the
- `157-161` KEEP — # Reconciliation freshness threshold. Accounts whose last / # reconciled date is more than this many days behind today (or
- `164-170` KEEP — # "Last transaction" staleness threshold for the book-summary / # warning. Beyond this many days since the most recent
- `287-292` KEEP — # owner_type 2 (customer), 3 (job), 5 (voucher) all / # render as customer-side receivables here. Voucher
- `425-427` KEEP — # ASSET passes only when it has reconcilable history. / # Investment positions / real estate / vehicles carry
- `443-451` KEEP — # Lag is computed from the OLDEST unreconciled split / # when there's pending work — the honest scope-of-
- `575-583` KEEP — # FX chokepoint: value the signed balance in the default / # currency via the same rate map the report tools use, then
- `630-632` KEEP — # (months_ago, label) — labels right-padded to 8 chars so / # the rendered values column visually aligns even with the
- `664-668` KEEP — # Budget overspend warning threshold. Variance over +10% (used% / # ahead of elapsed%) earns a ⚠ marker — under that, the user
- `671-674` KEEP — # Runway warning threshold. < 60 days earns a ⚠ marker — that's / # roughly two months, the window where a household should be
- `677-680` KEEP — # Days in the burn-rate averaging window. 180 days smooths over / # monthly billing cycles and seasonal variance without diluting
- `734-738` KEEP — # Stale-price threshold. Prices older than this in days surface / # in the Warnings section. Matches the cadence at which most
- `848-851` KEEP — # No FX on file — fall back to raw / # magnitude rather than dropping the
- `864-869` KEEP — # ── 2. Critically low cash ── / # Per-account threshold: positive balance below 1 day of
- `896-898` KEEP — # Zero = unused, not low. Negative = overdraft, / # captured separately by runway's
- `901-903` KEEP — # Convert to default currency for the threshold / # comparison. Without a rate, skip rather than
- `930-938` KEEP — # ── 3. Overdue invoices and bills ── / # Each posted invoice/bill with non-zero lot balance whose
- `970-976` KEEP — # Three-step due-date resolution lives on / # ``BusinessMixin`` (``_resolve_invoice_due_date``)
- `1021-1030` KEEP — # When no term and no explicit due_date were / # set, anchor the days count to the assumption
- `1059-1061` KEEP — # Template accounts would mark GnuCash's ``template`` / # pseudo-commodity in-use and misfire a permanent
- `1100-1102` KEEP — # Track (sort_key, message) so we can order most-stale / # first regardless of whether the commodity has a price
- `1128-1131` KEEP — # ── 5. Overdue scheduled transactions ── / # Requires SchedulingMixin's helpers (_next_occurrence,
- `1160-1162` KEEP — # Search relative to "yesterday" so today's / # occurrence isn't classified as overdue
- `1208-1211` KEEP — # No backup file in 30+ days: chain is stale even if / # the attempt status is fine. Could be that nothing
- `1221-1223` KEEP — # Integrity tier (imbalance/orphan) leads — actual data / # corruption that calls every other number into question.
- `1586-1595` KEEP — # Cost-basis fallback: sum split.value / # (transaction currency = book default for
- `1606-1608` KEEP — # Daily burn comes from the shared helper so the warnings / # section's "less than 1 day of burn" threshold and runway's
- `1679-1682` KEEP — # Build the calendar-month windows, oldest → newest. Plain / # arithmetic on (year, month) avoids a dateutil dependency
- `1713-1717` KEEP — # Single pass over the materialized transactions list. Index / # math: the bucket for a transaction is
- `1851-1867` KEEP — # Sub-line shape: "47 splits unreconciled (6 years / # behind, oldest: 2020-03-15) ⚠". The split count
- `2461-2463` KEEP — # Identify template accounts (scheduled-transaction / # scaffolding). Shared helper on BaseGnuCashBook walks
- `2472-2475` KEEP — # ``_rates_as_of(book, today)`` — future TRANSACTIONS / # are excluded but future PRICES are included.
- `2523-2525` KEEP — # The ``template`` namespace holds GnuCash's pseudo- / # commodity for SX template accounts — scaffolding, not
- `2532-2535` KEEP — # Assemble the output by chaining section renderers. / # Each one returns ``list[str]`` (empty to omit). The
- `2543-2546` KEEP — # Warnings: scan-first section. Lives near the top / # because data-integrity / stale-price warnings inform
- `2586-2589` KEEP — # Reconciliation, trajectory, monthly net, runway, / # budget: each section's data collector lives in its
- `2659-2662` KEEP — # Hide scheduled-transaction template accounts — they live / # under book.root_template as real Account rows (piecash
- `2688-2690` KEEP — # Build the short-guid map across the *whole* book so / # prefixes are unambiguous against every resolvable
- `2792-2796` KEEP — # Capture the canonical fullname for register-form / # rendering. _transaction_to_compact_line compares the
- `2800-2807` KEEP — # Hide scheduled-transaction template recipes — real / # Transaction rows in desktop-created books whose
- `2816-2818` KEEP — # Apply date filters. Null post_date rows (an old-book / # artifact — see _query.py) sort as date.min: visible in
- `2838-2842` KEEP — # Build collision-safe prefix map across ALL transactions in / # the book (not just the filtered batch) so emitted prefixes
- `3043-3046` KEEP — # Short-guid prefix map built once, shared across all emitted / # guids (auto-fill source, duplicates). Caller never sees the
- `3054-3064` KEEP — # Template-account GUID set — used to skip scheduled-transaction / # template transactions. GnuCash persists each SX's split
- `3067-3071` KEEP — # Proposed primary — the headline amount (max abs split value) / # used by the duplicate amount-signal. Computed once; when
- `3081-3085` KEEP — # One sort, one iteration. Descending by post_date so auto-fill / # and the "recent" / "stability" buckets fill from most-recent
- `3093-3098` KEEP — # Skip scheduled-transaction template rows — their splits / # post to Template Accounts, they have no bearing on the
- `3181-3183` KEEP — # Signal 3: date within ±2 days of trans_date (tighter / # than the window filter — window is for "worth
- `3496-3498` KEEP — # One book-open for the whole create pipeline — preflight signal / # gathering, write, and post-write consistency warning all live
- `3500-3507` KEEP — # --- Preflight pass 1: auto-fill + stability (if needed) --- / #
- `3532-3542` KEEP — # Sum-to-zero / account-resolution / cross-currency / # sign-and-quantity checks live in the shared validator
- `3559-3561` KEEP — # HIGH-confidence duplicate short-circuits the write. The / # rejection always carries at least one candidate, so
- `3573-3576` KEEP — # --- Validate accounts and build piecash splits --- / # Currency resolution: writable sessions may auto-create the
- `3589-3593` KEEP — # Shared validator: sum-to-zero, account resolution, / # cross-currency quantity/sign. Returns pre-resolved
- `3614-3616` KEEP — # Don't construct piecash.Split objects during dry_run — / # adding to the session would stage a write even if we
- `3632-3634` KEEP — # The collector gathered recent matches before we wrote, so / # the just-created txn is automatically absent — no post-
- `3674-3676` KEEP — # Emit a collision-safe short guid prefix so the LLM can feed / # it straight back into guid-accepting tools without spending
- `3732-3734` KEEP — # Same template-recipe filter as list_transactions: all / # four field modes would otherwise match SX templates in
- `4036-4039` KEEP — # Track which fields the caller actually changed. Echoing / # only the diff (vs. the entire account record) keeps the
- `4146-4149` KEEP — # Both ``fullname`` (the new path) and ``parent`` (the / # destination) are useful here: ``fullname`` answers
- `4228-4236` KEEP — # Reject if this transaction is the posting record for / # an invoice or bill. Deleting it directly orphans the
- `4396-4398` KEEP — # No match — surface a precise diff against the / # first remaining entry (the only one in the common
- `4557-4562` KEEP — # Thin response — the LLM submitted the changes, so the only / # fields worth echoing are enough for a quick sanity check
- `4624-4627` KEEP — # 2. Capture previous splits for audit trail (before deletion). / # Use the compact serializer — old-split GUIDs are not
- `4733-4740` KEEP — # 9. Build thin response. / # - `splits` echo dropped (LLM just submitted them).

## src/gnucash_mcp/book/investments.py

### investments.py:53-63 | REWRITE | rule 2/4
single-pass + no-N+1 rationale stays; strip '(SB-11)' and '(Copilot-flagged on PR #95: …)'

```

            # Single pass over ``book.prices`` to build a latest-
            # market-quote map keyed by commodity GUID. Pre-fix this
            # method iterated ``book.prices`` without the
            # ``_is_market_price`` filter, so a ``type='transaction'``
            # placeholder newer than the user's last ``nav`` quote
            # would shadow it (SB-11). The fix applies the predicate
            # in this same single pass — same chokepoint, no N+1
            # per-commodity query (Copilot-flagged on PR #95: a
            # per-commodity ``_find_prices`` call would issue one DB
            # query per commodity).
```

### investments.py:143-146 | REWRITE | rule 4
strip 'MP-13:' and 'Mirrors HP-11's symmetric-gate principle'; validate-before-ORM rationale stays

```
        # MP-13: validate inputs up front. Mirrors HP-11's
        # symmetric-gate principle — bad inputs reject before
        # the ORM round-trip, with a useful error rather than
        # IntegrityError / silent corruption downstream.
```

### investments.py:251-256 | REWRITE | rule 3
USD-default trap is a real guardrail; compress 'Pre-fix, an unspecified currency silently became USD' to conditional present

```
            # Resolve currency: explicit input wins; otherwise default
            # to the book's currency. Pre-fix, an unspecified currency
            # silently became "USD" — which on a non-USD-default book
            # stored prices like ``commodity=USD currency=USD`` (1 USD
            # = X USD, nonsense), invisible to ``_find_exchange_rate``
            # and silently shadowed by older valid prices on lookup.
```

### investments.py:264-267 | REWRITE | rule 3
indexed-query rationale stays; drop 'pre-fix this walked every price in the book'

```
            # Check for existing price (same commodity/currency/date/source).
            # Indexed query — pre-fix this walked every price in the book
            # for every create_price call. On a book with thousands of
            # historical prices that's a measurable hot path.
```

### investments.py:428-431 | CUT | rule 2
docstring: 'Pre-fix this method dumped every matching price regardless of caller intent' — pure history

```
            limit: Maximum prices to return. Defaults to 50, capped at
                   250 server-side. Pre-fix this method dumped every
                   matching price regardless of caller intent.

```

### investments.py:525-533 | REWRITE | rule 3
default-currency rationale stays; compress 'Pre-fix the default was hardcoded USD… the v1.2.1 multi-currency hardening pass fixed for create_price but missed here'

```
            currency: Currency for the price. Defaults to the book's
                default currency. Pre-fix the default was hardcoded
                ``"USD"``, which silently returned None for every
                price on a non-USD-default book (CNY, EUR, etc.) —
                same USD-default-everywhere assumption the v1.2.1
                multi-currency hardening pass fixed for
                ``create_price`` but missed here. Pass explicitly
                to get a non-default-currency price (e.g., the USD
                price of a stock on a CNY-default book).
```

### investments.py:560-569 | REWRITE | rule 3
skip-placeholder rationale stays; restate 'was the one exception, returning a transaction artifact' present-tense

```
                # Skip piecash's auto-created ``type='transaction'``
                # placeholder rows (effective rate of one cross-
                # currency transaction, source=``user:split-register``).
                # Every other valuation path in the codebase
                # (``get_book_summary``, ``_rates_as_of``,
                # ``_find_exchange_rate``, ``_latest_market_rates``,
                # stale-price warnings) excludes them; this single-
                # answer "what's the latest price" tool was the one
                # exception, returning a transaction artifact when
                # the user expected their nav quote.
```

### investments.py:620-626 | REWRITE | rule 3
state-not-value filter rationale stays; compress 'Pre-fix this worked by coincidence'

```
        for split in lot.splits:
            # Voided splits are zombies — preserved for audit trail
            # with quantity/value zeroed. Skip explicitly so the
            # intent is documented and partial-corruption cases
            # (state=v but quantity != 0) are also excluded. Pre-fix
            # this worked by coincidence because zeroed quantity
            # contributes 0 to either branch below.
```

### investments.py:660-675 | REWRITE | rule 5
lead with the two-field contract (cost_basis = legacy alias); drop 'Pre-fix the field name was just cost_basis' rename narrative

```
    def _lot_summary(self, lot) -> dict:
        """Compute current state of a lot from its splits.

        Returns:
            Dict with quantity, cost_basis (alias for
            remaining_cost_basis — kept for backward compat),
            remaining_cost_basis, original_cost_basis,
            cost_per_share, and is_closed.

        Pre-fix the field name was just ``cost_basis`` (the
        post-sale residual). After a partial sale the reading
        ``cost_basis: $50`` on a lot bought for $100 was
        ambiguous — was the lot bought for $50, or is $50 what's
        left of the original cost? Now both fields ship; the
        legacy ``cost_basis`` key keeps existing callers working.
        """
```

### investments.py:733-737 | REWRITE | rule 4
strip 'MP-11:'; auto-register fact + piecash source citation stay

```
            # MP-11: ``book.session.add(lot)`` is redundant —
            # piecash's Lot.__init__ assigns ``self.account``,
            # which back-populates through Account.lots and
            # auto-registers the Lot with the session. Verified
            # against piecash/core/transaction.py:514.
```

### investments.py:859-861 | REWRITE | rule 4
strip '(A8)'; unmarked-zero-row contradiction rationale stays

```
                # The summary already excludes voided zombies; an
                # unmarked 0-row here read as a real event the
                # summary then contradicted (A8).
```

### investments.py:866-870 | REWRITE | rule 4
strip 'Phase 6A:'; emit-once rationale stays

```
            summary = self._lot_summary(lot)
            # Phase 6A: ``is_closed`` already lives at the top level
            # of this response. Drop it from the nested ``summary``
            # so callers see the field once, not twice.
            summary_compact = {k: v for k, v in summary.items() if k != "is_closed"}
```

### investments.py:950-954 | REWRITE | rule 4
strip '(Phase 6A removed it from _lot_summary…)'; restate why is_closed is surfaced here

```
            # split_guid and lot_guid are echoed inputs — dropped.
            # ``is_closed`` is included on this response (Phase 6A
            # removed it from ``_lot_summary``, but the auto-close
            # behavior of this tool is exactly what the caller wants
            # to know about — keep it surfaced here).
```

### investments.py:1026-1035 | REWRITE | rule 4
strip 'SB-11 placeholder/currency portion' tags; the two-filter requirement (market + currency) stays

```
                # Look up latest market price for this commodity in
                # the book's default currency. Pre-fix this walk had
                # two bugs: no ``_is_market_price`` filter (a
                # ``type='transaction'`` placeholder could shadow
                # real quotes — SB-11 placeholder portion) and no
                # currency filter (a foreign-currency quote could
                # mis-denominate proceeds against a default-currency
                # cost basis — SB-11 currency portion). Routing
                # through ``_find_prices`` with both filters fixes
                # both at one chokepoint.
```

### investments.py:1049-1058 | REWRITE | rule 4
strip '(A1)'; same-currency-as-proceeds invariant stays verbatim

```
            # Cost basis must be in the same currency as the
            # proceeds (A1). ``purchase_value`` sums raw
            # ``split.value`` — TRANSACTION-currency units. A
            # foreign-denominated buy (EUR-currency purchase
            # transaction in a USD book) must convert at its
            # historical purchase date or the tax-relevant gain is
            # wrong by the full FX factor. Same-currency buys (the
            # common case) pass through unchanged; a missing
            # historical rate degrades to the raw value (house
            # fallback convention).
```

### investments.py:1092-1096 | REWRITE | rule 2
drop 'The 26-digit case the spec called out'; unbounded-repeating-decimal rationale stays

```
                # The 26-digit case the spec called out — ``(gain /
                # cost_basis) * 100`` produces an unbounded repeating
                # decimal in the general case. 2 decimal places is
                # what humans and reports actually use.
                "gain_percent": _format_number(gain_pct, decimals=2),
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `76-78` KEEP — # GnuCash's ``template`` pseudo-commodity backs SX / # template accounts in desktop-created books —
- `170-173` KEEP — # fraction is the smallest representable subunit — must be / # a positive power-of-10 style integer per piecash's
- `298-300` KEEP — # Echo the resolved mnemonic, not the input — the input / # might have been None (book default). This way the
- `351-354` KEEP — # Indexed query keyed on commodity (and source when set). / # The date filter stays in Python because piecash's date
- `373-375` KEEP — # source omitted but multiple sources match — the / # caller's call would have been destructive without
- `449-451` KEEP — # Indexed query: filter by commodity_guid up front so we / # don't iterate every price in the book. Currency filter
- `491-495` KEEP — # Compact format: one line per price, columns aligned. / # Format::
- `639-643` KEEP — # Prorate cost basis on shares remaining, not / # ``cost_per_share * remaining`` — for a lot bought at
- `684-687` KEEP — # Quantity is shares (or other commodity units): 4 decimals / # is a good default for funds and stocks. Crypto callers
- `689-691` KEEP — # Legacy: ``cost_basis`` returns the remaining (post-sale) / # value, same as before the rename. New callers should
- `783-792` KEEP — # Skip lots with no remaining position in the default / # (open-positions) view. This catches three cases that
- `806-808` KEEP — # _resolve_guid("lots", ...) searches the whole lots table, / # so the prefix map has to span every lot in the book — not
- `838-841` KEEP — # Build a collision-safe prefix map across every split in / # the book — the LLM can paste any short prefix back into
- `908-912` KEEP — # Reject voided splits — they're zombies (quantity=0, / # value=0) and would attach a zero-contribution row to
- `994-998` KEEP — # Distinguish "lot ran to zero through sales" (normal, / # closed lot) from "lot has voided splits zeroing its
- `1074-1078` KEEP — # Prorate cost basis on shares-to-sell. Avoids the precision / # loss of ``cost_per_share * shares`` for non-round

## src/gnucash_mcp/book/reconciliation.py

### reconciliation.py:88-93 | REWRITE | rule 3
voided→y defeats unvoid: real guardrail; compress 'Pre-fix the input gate accepted any of {n,c,y}'

```
            # Reject state changes on voided splits. Pre-fix the input
            # gate accepted any of {n, c, y} regardless of current
            # state, so a voided split could be moved to ``y`` — which
            # zeroed the void marker and defeated
            # ``unvoid_transaction``'s recovery path. To re-reconcile
            # a previously-voided split the user must unvoid first.
```

### reconciliation.py:195-201 | REWRITE | rule 4
strip 'HP-8'; chokepoint cross-reference to _is_unreconciled stays

```
                # Skip reconciled and voided splits — only states
                # ``n`` (new) and ``c`` (cleared) count as pending
                # bookkeeping work. ``_is_unreconciled`` is the
                # chokepoint shared with the dashboard count
                # (``_account_reconciliation_status``) so HP-8
                # convergence is structural rather than
                # docstring-promised.
```

### reconciliation.py:285-290 | REWRITE | rule 2/3
keep the default-through_date guardrail (statement_date default silently excludes later payments); cut 'bookkeeper-validated… which the tester hit on a CareCredit payoff'

```
          a specific date; by default no date filter is applied
          (the bookkeeper-validated semantics — pre-fix this
          defaulted to ``statement_date`` and silently excluded
          payment splits dated after the statement, which the
          tester hit on a CareCredit payoff). Reject if
          ``split_guids`` is also given to avoid ambiguity.
```

### reconciliation.py:390-394 | REWRITE | rule 4
strip '(C8)'; skip-voided rationale stays

```
                    # Voided splits are zombies, not pending work —
                    # flipping one to 'y' is the exact mutation
                    # set_reconcile_state rejects, and it defeats
                    # unvoid_transaction (C8). The sweep skips them
                    # silently; they were never reconcilable.
```

### reconciliation.py:412-415 | REWRITE | rule 3
compare-by-GUID guardrail stays; restate 'Pre-fix the check compared against the raw input string' conditionally

```
                    # ``account_name`` accepts ``%short`` and full-GUID
                    # input. Pre-fix the check compared against the raw
                    # input string, rejecting any shortcut form even
                    # when it resolved to the right account.
```

### reconciliation.py:437-445 | REWRITE | rule 3
quantize-both-sides guardrail + 0.007 example stay; drop 'Pre-fix'/'Now' framing

```
            # Quantize both sides to the account commodity's
            # smallest fraction before comparing. Pre-fix a user
            # typing ``"1234.567"`` against a 2-decimal book
            # produced a perpetual 0.007 mismatch with no clear
            # error — every reconciliation attempt failed even
            # when the books agreed at the cent. Now the
            # statement balance and computed balance are both
            # normalized to the commodity's smallest unit (USD ->
            # 2 decimals, JPY -> 0, BHD -> 3) before equality.
```

### reconciliation.py:518-526 | REWRITE | rule 4
strip 'HP-9' tag; void-reason byte-cap rationale stays

```
        # HP-9 length cap. 4 KiB covers any realistic void
        # explanation — multi-paragraph context, audit trail
        # notes, references to ticket numbers — while keeping a
        # malicious or runaway caller from exhausting the book
        # file with a single void. Byte-count (not char-count)
        # so unicode payloads can't sneak past. Compute the byte
        # length once; ``reason.encode(...)`` would allocate a
        # fresh copy of the already-large string.
        _VOID_REASON_MAX_BYTES = 4 * 1024
```

### reconciliation.py:556-563 | REWRITE | rule 3
tz-aware-time guardrail stays (naive now() is the attractive wrong move); compress 'Pre-fix this stored…'

```
            # GnuCash slot keys for void info. Use a tz-aware
            # local time (mirroring the audit-log convention) so a
            # later reader can reconstruct "when was this voided"
            # unambiguously across DST transitions and timezone
            # changes. Pre-fix this stored a naive ``datetime.now()``
            # whose interpretation depended on the host's current
            # zone — same string would mean different absolute times
            # before/after a DST shift.
```

### reconciliation.py:621-627 | REWRITE | rule 3
partial-unvoid corruption guardrail stays; compress 'Pre-fix partial corruption silently produced…'

```
            # Validate up-front that EVERY voided split has its
            # void-former slots present. Pre-fix partial corruption
            # (e.g., a split missing its void-former-value but with
            # void-former-quantity present) silently produced a
            # partial unvoid — the value-missing split would stay
            # at zero while its sibling was restored. Better to
            # refuse and surface the corruption explicitly.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `118-121` KEEP — # Response carries a collision-safe short prefix of the split / # GUID plus context the LLM only had a GUID for (account,
- `241-243` KEEP — # Prefix uniqueness spans every split in the book because / # set_reconcile_state / reconcile_account resolve split
- `367-372` KEEP — # Pre-resolve except_guids prefixes to full GUIDs so / # the per-split skip check is a fast set lookup. A
- `380-386` KEEP — # Walk the account's own splits — by construction every / # entry is on this account, so no membership check
- `424-426` KEEP — # Explicitly-targeted voided split: refuse loudly / # (same contract as set_reconcile_state) rather
- `461-465` KEEP — # Stage pre-reconcile state for the audit log. Shape mirrors / # the multi-split payload the audit formatter expects for
- `477-479` KEEP — # Return only the computed info — the audit log reads inputs / # (account_name, statement_date, statement_balance) from tool
- `670-673` KEEP — # Restored splits ARE new info (they were zeroed while / # voided, values stashed in slots). Emit them compactly —

## src/gnucash_mcp/book/reporting.py

### reporting.py:32-44 | FIX-FALSE | rule 6
lines 32-35 are two overlapping header drafts stacked on each other; also compress 'Pre-v1.3.0 these were excluded — invoices… breaking A = L + E' to the keep-RECEIVABLE/PAYABLE guardrail

```
# Account-type groups used across the reports. Defined at module level
# so the SQL IN() clauses share a single canonical definition rather
# than drifting across methods.
# Type sets for balance_sheet / net_worth bucketing.
#
# RECEIVABLE and PAYABLE belong here despite GnuCash treating them
# as their own first-class account types. Accounting-wise, A/R is
# an asset (someone owes us money) and A/P is a liability (we owe
# someone money); both flow through the balance sheet's totals and
# the canonical "net worth = total assets − total liabilities"
# identity. Pre-v1.3.0 these were excluded — invoices issued to
# customers were invisible on balance_sheet, breaking A = L + E by
# the outstanding-invoice amount.
```

### reporting.py:108-113 | REWRITE | rule 2
currency-arg rationale stays; cut 'Pre-fix this helper hardcoded $ and broke as soon as the bookkeeper pointed it at a CNY-default book'

```
    mnemonic (``"USD"``, ``"CNY"``, ``"EUR"``, etc.) — matches
    ``get_book_summary``'s ``"USD 6700.00"`` rendering style and
    works for non-USD books out of the box. Pre-fix this helper
    hardcoded ``$`` and broke as soon as the bookkeeper pointed it
    at a CNY-default book.
    """
```

### reporting.py:146-150 | CUT | rule 2
'Replaces the verbose dict… the heaviest single response in Abe's audit' — provenance + diff narration

```

    Replaces the verbose dict (with multi-line YETI ``explanation`` per
    account) that was the heaviest single response in Abe's audit.
    Verbose mode preserves the dict for programmatic consumers.
    """
```

### reporting.py:265-267 | REWRITE | rule 4
strip '(Phase 4C)' from compact-arg doc

```
            compact: If True (default), return an aligned text table
                     suitable for direct LLM consumption (Phase 4C).
                     Verbose mode returns the structured dict.
```

### reporting.py:281-287 | REWRITE | rule 3
historical-rates guardrail stays; compress 'Pre-v1.3 release the factors call omitted as_of'

```
            # Convert each split to the book's default currency at
            # rates as of the period's end. Pre-v1.3 release the
            # factors call omitted ``as_of`` and silently used
            # today's rates regardless of the historical period
            # being reported — wrong for any historical multi-
            # currency view.
            factors = self._account_conversion_factors(book, end_date)
```

### reporting.py:301-305 | REWRITE | rule 4
depth-vs-depth-1 off-by-one guardrail stays ('Expenses 100%' collapse); strip '(A7)'

```
                # ``depth`` (not ``depth - 1``): path[0] is the type
                # root ("Expenses"), so the documented "depth 1 =
                # top-level buckets (Expenses:Food)" needs path[1].
                # Pre-fix the off-by-one collapsed the entire default
                # report to a single "Expenses 100%" row (A7).
```

### reporting.py:314-321 | REWRITE | rule 4
net-after-aggregation invariant stays; strip '(C6)'

```
            # Net decision is made AFTER aggregation, not per split. A
            # category whose net spend is < 0 (net-refunded for the
            # period) isn't a spend LINE, but its net still belongs in
            # the TOTAL — dropping it made the total change with the
            # ``depth`` parameter (a net-negative leaf nets against
            # siblings at low depth, vanishes at high depth) and untied
            # income − spending from net income (C6). Net-negative
            # groups surface explicitly instead of vanishing.
```

### reporting.py:378-380 | REWRITE | rule 4
strip '(Phase 4C)'

```
            compact: If True (default), return an aligned text table
                     suitable for direct LLM consumption (Phase 4C).
                     Verbose mode returns the structured dict.
```

### reporting.py:413-414 | REWRITE | rule 4
'same A7 off-by-one fix' → self-contained cross-reference to spending_by_category

```
                # ``depth`` (not ``depth - 1``) — same A7 off-by-one
                # fix as spending_by_category.
```

### reporting.py:423-428 | REWRITE | rule 4
strip '(C6)'

```
            # Net decision is made AFTER aggregation, not per split. A
            # source whose net is < 0 (net loss for the period) isn't
            # an income LINE, but its net still belongs in the TOTAL
            # so the total stays depth-invariant and income − spending
            # ties to net income (C6). Net-loss sources surface
            # explicitly instead of vanishing.
```

### reporting.py:509-513 | REWRITE | rule 3
one-SQL-pass rationale stays; restate 'The old code opened Python loops per account; now we…'

```
        # Sum splits across every relevant type in one SQL-filtered
        # pass. The old code opened Python loops per account; now we
        # hit the splits table once with an IN() clause and bucket the
        # results in memory. Net income (Income - Expenses) is
        # retained-earnings-equivalent and rolls into equity below.
```

### reporting.py:518-521 | REWRITE | rule 4
strip 'Pre-v1.3 release this fetched today's rates… — SB-2'; same-date-rates invariant stays

```
            # Historical balance_sheet must use rates as of the same
            # date the balances are computed for. Pre-v1.3 release
            # this fetched today's rates against historical
            # quantities — SB-2.
```

### reporting.py:530-534 | REWRITE | rule 4
strip '(Phase 4B)'

```
            # commodity-quantity, account ref). The quantity / commodity
            # bits are needed to render the "230.7600 VTSAX @ 156.23
            # (USD 36,043.66)" triplet for non-default-currency accounts
            # (Phase 4B). Currency-default accounts ignore that detail.
            balances: dict[str, dict] = {}
```

### reporting.py:537-541 | REWRITE | rule 4
strip '(C8)'; voided-by-state rationale stays

```
                # Voided by state, not value: a well-formed void
                # contributes 0 anyway; the corrupted partial-void
                # shape (state='v', non-zero values) must not move
                # the sheet when cash_flow / lots can't see it (C8).
                if _is_voided(split):
```

### reporting.py:543-551 | REWRITE | rule 4
do-NOT-skip-placeholders guardrail stays (skipping silently deletes assets); strip 'The SB-8 skip…' and '(adversarial pass 2, C1)'

```
                # Placeholder accounts are NOT skipped: there is no
                # roll-up in this report, so direct splits on a
                # placeholder — rare but legal — are real money no
                # other row represents. The SB-8 skip guarded a
                # double-count this code never produces; with the
                # balancing-residual equity line it silently deleted
                # the dropped asset instead (adversarial pass 2, C1).
                # Same own-splits-per-account rule as ``net_worth``
                # and ``_compute_net_worth_at``.
```

### reporting.py:576-583 | REWRITE | rule 4
strip '(SB-2)'

```
            # Latest market rates keyed by commodity guid — same data
            # ``_market_value`` in get_book_summary uses for the per-
            # account display. ``_rates_as_of`` excludes piecash
            # auto-created ``type='transaction'`` prices. ``as_of=
            # as_of_date`` so a historical balance sheet renders
            # each non-default-currency holding at the rate it would
            # have been valued at on the report date — not today's
            # rate (SB-2).
```

### reporting.py:586-590 | REWRITE | rule 4
strip '(issue #94)'

```
            # intermediate currency (issue #94), so a chained value
            # renders "@ rate (… via USD)" — distinguishing a derived
            # cross from a directly-quoted rate the reader entered.
            rate_via = self._rate_provenance(
                book, as_of_date, default_currency,
```

### reporting.py:670-678 | REWRITE | rule 3
format contract stays; compress two 'Pre-fix' sentences (precision noise / hardcoded USD) to present-tense rationale

```
                All numeric outputs flow through ``_format_number``
                (currency-style: 2 decimals always padded). Pre-fix
                the per-account values leaked Decimal precision noise
                (e.g. ``"612011.489832"``) into responses.
                """
                # Default-currency mnemonic for the triplet rendering.
                # Pre-fix this hardcoded "USD"; on a CNY-default book
                # that lied about the currency on every investment row.
                ccy_mnemonic = default_currency.mnemonic
```

### reporting.py:709-713 | REWRITE | rule 2
field semantics stay; cut 'Pre-fix this field was named usd_value… Renamed to reflect' rename narrative

```
                        # ``default_currency_value`` carries the
                        # parseable amount in the book's default
                        # currency. Pre-fix this field was named
                        # ``usd_value`` — a lie on non-USD books.
                        # Renamed to reflect the actual semantics.
```

### reporting.py:721-727 | FIX-FALSE | rule 6
garbled duplicate '`balanced`/``balanced``' line; 'was dropped in an earlier audit pass' history — restate clean

```
            # as_of_date is also an input echo, but it's cheap and
            # useful when a log is reviewed out of context. `balanced`
            # ``balanced`` is derivable (assets == liabilities + equity)
            # and was dropped in an earlier audit pass. Rollup totals
            # flow through ``_format_number`` (2 decimals, currency
            # style) so the response no longer leaks Decimal precision
            # noise like ``"612011.489832"``.
```

### reporting.py:794-796 | REWRITE | rule 4
strip '(C8 read-side)'

```
                    # Voided-by-state filter — same rule as
                    # balance_sheet (C8 read-side).
                    if _is_voided(split):
```

### reporting.py:835-846 | REWRITE | rule 4
per-boundary-rates correctness rationale stays; strip 'Pre-v1.3 (and through commit 4 of this branch)' and '(SB-1)'

```
            # Per-boundary factors. Pre-v1.3 (and through commit 4 of
            # this branch) this method computed a single factors map
            # outside the sweep and applied it uniformly to every
            # boundary — wrong, because each historical snapshot
            # should be valued at the rate of its own date, not
            # today's (SB-1). Pre-computing here trades O(boundaries)
            # extra factors-builds for correctness: each call walks
            # ``book.prices`` once with a different upper-bound date.
            factors_by_boundary: dict[date, dict[str, Decimal | None]] = {
                b: self._account_conversion_factors(book, b)
                for b in boundaries
            }
```

### reporting.py:848-859 | REWRITE | rule 3
why-no-single-running-total stays; restate 'like the pre-fix algorithm did' conditionally

```
            # Pull every asset/liability split up through end_date in
            # post_date order. With per-boundary factors the same
            # split contributes a different default-currency value at
            # each boundary (factor × quantity for boundaries that
            # have a rate on file, split.value fallback for boundaries
            # that don't). We can't carry a single ``running`` total
            # forward like the pre-fix algorithm did; instead we track
            # per-account quantity AND value running totals and
            # convert at snapshot time using that boundary's factors.
            # Cost: O(splits + boundaries × accounts_with_splits) —
            # still much cheaper than the original O(intervals × splits)
            # and correct under per-boundary rates.
```

### reporting.py:891-905 | REWRITE | rule 4
strict-> boundary guardrail (>= silently breaks balance_sheet tie) stays verbatim; strip 'MP-8:'

```
            for split, txn, account in rows:
                # MP-8: snapshot every boundary STRICTLY BEFORE
                # this split. The strict ``>`` is correct and
                # deliberate: a boundary equal to a split's
                # post_date includes that split in its snapshot
                # (the boundary is "end of day", so a transaction
                # posted that day has happened by then). The
                # cumulative running totals above this loop
                # advance after the snapshot is taken, so the
                # snapshot reflects every prior split AND every
                # split posted on the boundary date — matching
                # the inclusive-end semantics ``_query_filtered_splits``
                # enforces at the SQL boundary. A ``>=`` here
                # would exclude same-day splits, silently breaking
                # the trajectory's tie to ``balance_sheet(as_of)``.
```

### reporting.py:915-917 | REWRITE | rule 4
strip '(C8 read-side)'

```
                # Voided-by-state filter (C8 read-side) — placed
                # after the boundary advance so a voided split still
                # flushes due snapshots, just never accumulates.
```

### reporting.py:971-980 | REWRITE | rule 4
strip trailing 'SB-5.' and '(C4)' from cash_flow docstring; rationale stays

```
        neither inflow nor outflow in cash-flow terms. Filtering
        that noise is what makes the totals answer "where did money
        come from and where did it go?" rather than "every debit
        and credit that touched a cash account, including same-
        pocket reshuffling." SB-5.

        Invoice/bill settlements (lot-linked A/R / A/P legs) count
        as real flow despite having no income/expense split — the
        income was recognized at post time, and the payment is when
        the cash actually moved (C4). See ``_cashflow_txn_guids``.
```

### reporting.py:1027-1033 | REWRITE | rule 4
strip 'SB-5:' tag

```
            # SB-5: build the set of transaction GUIDs in the
            # period that have at least one INCOME or EXPENSE
            # split — the "real" cash flow events. Transactions
            # outside this set are internal transfers (pure
            # asset/liability/equity rearrangement) and get
            # filtered unless include_transfers is True. One
            # indexed SQL query — no N+1 over txn.splits.
```

### reporting.py:1093-1120 | REWRITE | rule 3/4
lot-link-as-deterministic-key guardrail stays (FX-drift rescue made classification rate-dependent); strip '(SB-5)', '(C4)', 'the chokepoint discipline this project adopted in Branch 1'

```
        """Set of transaction GUIDs in the period that count as real
        cash-flow events rather than internal transfers.

        Used by ``cash_flow`` to filter "internal transfer" noise
        from the default report — see that method's docstring
        for the rationale (SB-5).

        Two qualifying shapes (C4):

        1. A non-voided INCOME or EXPENSE split — the ordinary
           earn/spend event.
        2. A non-voided **lot-linked** RECEIVABLE / PAYABLE split —
           an invoice/bill settlement. Income was recognized at post
           time (A/R ↔ Income never touches cash), so the payment is
           A/R ↔ bank: structurally a transfer, but it IS the
           revenue receipt. Pre-fix these were filtered out — unless
           FX drift happened to add a realized-FX income split,
           which rescued that one payment and made classification
           depend on rate movement. Keying on the lot link is
           deterministic and pulls every settlement into the flow;
           manual A/R adjustments without a lot stay transfers.

        Routes through ``_query_filtered_splits`` to inherit the
        date-bound fix, template-account exclusion, and null-
        post_date filter — the chokepoint discipline this project
        adopted in Branch 1. The voided-split filter is applied
        Python-side so a voided salary or expense doesn't
        rescue a zombie transaction from the transfer filter.
```

### reporting.py:1271-1273 | REWRITE | rule 3
compress 'pre-fix this method emitted $ regardless of book setting'

```
            # Capture the book's default currency for the compact
            # formatter — pre-fix this method emitted ``$`` regardless
            # of book setting, breaking on non-USD books.
```

### reporting.py:1279-1283 | REWRITE | rule 4
strip 'MP-9:'; two-failure-modes rationale stays

```
            # MP-9: track whether ANY debt-typed account exists, so
            # the no-debts error can distinguish "no CREDIT/LIABILITY
            # accounts at all" from "they exist but lack the apr
            # slot" — the user's next action differs sharply
            # between those cases.
```

### reporting.py:1295-1301 | REWRITE | rule 3
FX-chokepoint rationale stays; compress 'Pre-fix this summed raw split.quantity, the lone reporting-layer method that bypassed…'

```
            # FX chokepoint: per-account conversion factors so a
            # foreign-currency debt is valued in the book default
            # currency (rate × quantity, cost-basis fallback) instead
            # of summing raw foreign units. "Now" report → today's
            # rates. Pre-fix this summed raw split.quantity, the lone
            # reporting-layer method that bypassed the FX helper used by
            # balance_sheet / net_worth / cash_flow.
```

### reporting.py:1313-1318 | REWRITE | rule 3
materialize-once rationale stays; restate 'Pre-fix, each account[key] access went through…' present-tense

```
                # Materialize ``account.slots`` into a dict once. Pre-fix,
                # each ``account[key]`` access (apr, minimum_payment,
                # credit_limit — three per account) went through piecash's
                # slot-helper path, hitting the slots collection
                # independently per key. One iteration + three dict gets
                # is cheaper.
```

### reporting.py:1333-1339 | REWRITE | rule 4
strip '(C5)'; future-dated exclusion rationale stays

```
                # Calculate current balance in the book default currency
                # (negate because liabilities are stored negative).
                # "Now" report: future-dated transactions are excluded
                # (C5) — a payment scheduled for next week hasn't
                # reduced today's payoff balance — and voided splits
                # are excluded by state. Null post_date rows (old-book
                # artifact) can't be dated, so they're skipped too.
```

### reporting.py:1368-1375 | REWRITE | rule 4
strip '(A3)'; account-commodity slot convention + ¥2,000-as-$2,000 trap stay

```
                # Slots store untagged scalars; the convention is
                # the ACCOUNT's commodity (matching the dashboard's
                # utilization math, which compares credit_limit to
                # the account-commodity balance). The plan's math
                # runs in book default currency, so convert with the
                # same factor the balance used (A3) — pre-fix a
                # ¥2,000 minimum was treated as $2,000 and skewed
                # the feasibility gate and the avalanche schedule.
```

### reporting.py:1424-1424 | REWRITE | rule 4
strip '(A3)' tag

```
                credit_limit = None  # account-ccy slot; converted below (A3)
```

### reporting.py:1442-1445 | REWRITE | rule 3
precompute rationale stays; drop 'pre-fix recomputed apr/100/12 every iteration'

```
                    # Pre-compute monthly rate once per debt. _run_avalanche's
                    # inner loop iterates up to 1200 months, and pre-fix
                    # recomputed apr/100/12 every iteration for every debt
                    # that still had a balance.
```

### reporting.py:1452-1453 | REWRITE | rule 4
strip 'MP-9:'

```
            # MP-9: distinguish the two failure modes so the LLM's
            # next action is right.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `71-74` KEEP — # Strip a common leading "Expenses:" / "Income:" prefix when every / # row shares it. This keeps the leaf-name column readable without
- `151-153` KEEP — # Account names: leaf-name only when path is unambiguous (saves / # context vs. echoing "Liabilities:Credit Card:Business Amex" on
- `194-196` KEEP — # Footer: YETI plus totals. The YETI line speaks plainly because / # it's the actionable signal — "this purchase costs you X.XX times
- `291-297` KEEP — # Expense splits are positive when money is spent; a / # refund / return / intra-type reclassification posts a
- `394-396` KEEP — # Convert each split to the book's default currency at / # rates as of the period's end — same historical-rates
- `401-409` KEEP — # Income splits are stored negative (money coming in); / # flip to positive for the "how much did I earn" view. A
- `620-646` KEEP — # ── Unrealized gain/loss (balancing adjustment) ────────── / # Assets render at market value (factor × quantity for
- `747-753` KEEP — # Synthetic — closes A = L + E when assets / # render at market value. Single signed line
- `798-802` KEEP — # Liabilities are stored negative, so adding the / # converted amount directly gives assets minus
- `824-826` KEEP — # Build the list of interval boundaries up-front. Every / # boundary gets a net_worth snapshot below; end_date is
- `920-922` KEEP — # Accumulate per-account quantity AND value. Both are / # kept so ``_snapshot_at`` can pick the right view
- `1006-1008` KEEP — # Two filter modes: a named account (one-GUID IN() clause) / # or the default "all cash/bank accounts" (account-type
- `1048-1054` KEEP — # Voided splits (state='v', value=quantity=0) are / # zombies, not active cash flow. Skip before the
- `1069-1074` KEEP — # period is input echo (LLM supplied dates), net is derivable / # (inflows - outflows). Both dropped.
- `1177-1180` KEEP — # Step 1: Apply monthly interest to each balance. Rate is / # pre-computed per debt in ``debt_payoff_plan`` (apr/100/12);
- `1286-1292` KEEP — # Template accounts (scheduled-transaction / # scaffolding) inherit type=CREDIT/LIABILITY from
- `1358-1360` KEEP — # 1. Check minimum_payment slot (user override). / #    Wins for both CREDIT and LIABILITY — the user has
- `1383-1392` KEEP — # 2. Type-aware fallback. Credit cards and amortizing / #    loans have very different minimum-payment shapes:
- `1402-1409` KEEP — # LIABILITY: standard amortization formula. / # PMT = P × r(1+r)^n / ((1+r)^n − 1)

## src/gnucash_mcp/book/scheduling.py

### scheduling.py:279-291 | REWRITE | rule 3
ghost-template-account cleanup rationale stays; compress 'pre-fix the caller would retry…'

```
            # Create template account under root_template. We flush
            # this immediately because the SX row references its
            # GUID via ``template_act_guid``. If any of the
            # subsequent inserts (SX row, Recurrence, Slot) fail,
            # the template account is already on disk — pre-fix the
            # caller would retry with the same name and hit the
            # duplicate-name check fine, but a "ghost" template
            # account with no scheduled-transaction owner would sit
            # under ``root_template`` forever.
            #
            # Wrap the whole sequence in try/except so partial-
            # failure cleans up the orphan template account before
            # propagating the error.
```

### scheduling.py:298-304 | REWRITE | rule 4
strip 'MP-11:'; auto-register + flush-needed-for-raw-SQL facts stay (CLAUDE.md pointer live)

```
            # MP-11: ``book.session.add(template_acct)`` is
            # redundant — piecash's Account auto-registers via
            # the parent relationship. Documented in CLAUDE.md
            # under "piecash gotchas." The flush is kept because
            # the next block does a raw SQL INSERT against the
            # scheduled-transaction table and needs the template
            # account row to exist first.
```

### scheduling.py:583-585 | REWRITE | rule 2
reword 'legacy slots whose pre-fix JSON may still carry a numeric literal' → 'older slots may carry numeric literals'

```
                    # _to_decimal is defensive for any legacy slots whose
                    # pre-fix JSON may still carry a numeric literal.
                    total = Decimal("0")
```

### scheduling.py:622-648 | REWRITE | rule 3/4
three-phase contract is excellent and stays; strip '(SB-10)' and compress 'Pre-fix the schedule advanced BEFORE the transaction call' to one guardrail line

```
        Three-phase write to keep the schedule advance and the
        transaction in lockstep (SB-10):

        1. **Read-only** session: resolve the scheduled-transaction
           row, compute the target ``txn_date``, validate
           preflight (frequency known, date past ``last_occur``,
           splits non-empty). Captures everything needed for the
           write without mutating anything.
        2. ``self.create_transaction(...)`` runs in its own session.
           On success this lands a transaction; on a raise the
           schedule has not advanced and the caller's retry is
           safe; on ``status="rejected"`` the duplicate detector
           found an equivalent prior transaction (so an
           equivalent transaction DOES exist for this period —
           we treat that as a successful no-op and still advance
           the schedule).
        3. **Read-write** session: advance ``last_occur`` and
           ``instance_count``. Reached only when phase 2 returned
           without raising — so the schedule-advance-vs-transaction-
           existence invariant holds in both the success and
           duplicate-detected branches.

        Pre-fix the schedule advanced BEFORE the transaction call;
        any raise in phase 2 left the schedule moved with no
        transaction posted, and a re-run would skip the period
        entirely.

```

### scheduling.py:741-747 | REWRITE | rule 4
strip 'for SB-10 purposes' — restate as the schedule-advance invariant

```
        # ── Phase 2: create the transaction. ────────────────────
        # On raise: schedule is unchanged (phase 3 not reached).
        # On status="rejected": duplicate detector caught an
        # equivalent prior transaction — for SB-10 purposes that
        # transaction IS the one for this period, so we proceed
        # to advance the schedule and forward the rejection signal
        # to the caller in the response.
```

### scheduling.py:769-787 | REWRITE | rule 2
never-rewind / no-double-count invariant stays; cut 'Copilot-flagged on PR #97'

```
                # ``last`` was captured under the readonly session;
                # if desktop pre-created ahead while phase 2 ran,
                # use whatever's current. Never rewind.
                current_last = sx.last_occur
                if isinstance(current_last, datetime):
                    current_last = current_last.date()
                # Only advance + increment when ``txn_date`` is
                # actually beyond the current marker. If a
                # concurrent writer covered this period between
                # phases 2 and 3 (desktop's "Since Last Run", or
                # another tool invocation), the schedule already
                # registered the period — incrementing
                # ``instance_count`` again would double-count.
                # Copilot-flagged on PR #97. The MCP server runs
                # single-threaded so this is practically
                # unreachable today, but the gate is cheap and the
                # invariant ("instance_count equals the number of
                # distinct periods this schedule has produced")
                # holds under any future multi-writer scenario.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `96-102` KEEP — # Anchor each occurrence to ``start_date + (n × period)`` rather / # than chaining ``occurrence += delta``. ``relativedelta`` clamps
- `245-247` KEEP — # _to_decimal routes any stray float (direct caller bypassing the / # tool-layer SplitInput model) through Python's shortest-repr so
- `347-353` KEEP — # Store split templates as JSON in a slot. Normalize / # `amount` through _to_decimal → str so the persisted
- `378-381` KEEP — # Cleanup the orphan template account so a retry / # doesn't accumulate unowned template scaffolding.
- `509-518` KEEP — # splits-json amounts are in the book default currency by / # construction: create_scheduled_transaction pins the
- `717-721` KEEP — # Preflight: refuse to instantiate an occurrence on or / # before last_occur. GnuCash desktop's "Since Last Run"
- `754-757` KEEP — # ── Phase 3: advance the schedule. ────────────────────── / # Re-resolve under a read-write session and apply both
- `761-766` KEEP — # Edge case: schedule deleted concurrently between / # phases 1 and 3. The transaction exists; we
- `794-796` KEEP — # ── Build response. ───────────────────────────────────── / # Not a write phase — the docstring describes three
- `805-812` KEEP — # Explicit evidence for downstream LLMs (including / # dumber models that might retry status="rejected"
- `853-855` KEEP — # Stage prior state for the audit log so the bookkeeper / # can see what changed (enable/disable; end-date set/clear).
- `896-899` KEEP — # Stage SX snapshot for the audit log BEFORE delete so the / # bookkeeper can recover the schedule's identity from the
- `918-920` KEEP — # Delete the splits-json slot via SQLAlchemy Core — column / / # table renames surface as AttributeError at import, not as
- `937-942` KEEP — # Desktop-created SXs store the split recipe as real / # Transaction rows posted to the template account;

## src/gnucash_mcp/tools/_helpers.py

### _helpers.py:1-5 | REWRITE | rule 2
module docstring: 'These used to live in server.py' → present-tense purpose

```
"""Shared helpers for MCP tool wrappers.

These used to live in server.py; they are now shared across every
tool-registration module under gnucash_mcp/tools/.
"""
```

### _helpers.py:27-31 | REWRITE | rule 4
strip 'R-4:' from _parse_iso_date docstring (internal helper; not wire surface); chokepoint note stays

```
    R-4: chokepoints the ``date.fromisoformat(x) if x else None``
    pattern that recurred at ~21 tool-wrapper sites. Required
    dates (where the caller has already guaranteed non-None) keep
    calling ``date.fromisoformat`` directly — the distinction is
    explicit at the call site.
```

### _helpers.py:70-91 | REWRITE | rule 2/4
schema-layer-validates-before-auto-backup rationale is rule-1 gold and stays; strip '(MP-5)' and 'Plumb Bob (bookkeeper validation, 2026-06-04) flagged this:'

```
# ── Business-entity free-text caps (MP-5) ─────────────────────────
#
# The book-layer ``_validate_business_freetext`` chokepoint rejects
# oversize input correctly — but it runs INSIDE the tool body,
# AFTER ``@audit_log`` fires ``_maybe_auto_backup``. On the first
# write of a session against a large book, that backup can take
# seconds-to-minutes; from the caller's seat, a 5000-byte ``notes``
# value looks like a hang before the validation rejects.
#
# Plumb Bob (bookkeeper validation, 2026-06-04) flagged this:
# "for a defense-in-depth input gate, a hang is worse than the
# unbounded write it was meant to prevent." Pydantic Field
# constraints validate at the FastMCP schema layer — BEFORE any
# decorator runs, including auto-backup — so an oversize value
# rejects in milliseconds with the correct error shape.
#
# Cap is in characters (Pydantic's ``max_length`` semantics);
# UTF-8 byte length is at most ~4× character length for the
# pathological multi-byte case, so the effective byte ceiling is
# ~16 KiB even for the worst-case input. Book-layer byte check
# stays as belt-and-suspenders for direct callers (scripts,
# tests) that bypass the MCP boundary.
```

### _helpers.py:124-127 | REWRITE | rule 4
'Same MP-5 rationale' → 'Same rationale as BusinessNotes'. CAUTION: pydantic model docstring may surface in tool input schemas — Phase B baseline must capture schemas, not just descriptions

```
    Same MP-5 rationale as ``BusinessNotes``: the cap fires at the
    schema layer so an oversize value rejects fast without auto-
    backup running first.
    """
```

### _helpers.py:146-166 | REWRITE | rule 3
float-epsilon trap + coerce_numbers_to_str rationale stay; restate 'Before this model existed, the tool signature was list[dict]' conditionally

```
# ── Split payload schema ──────────────────────────────────────────
#
# Transaction-creating tools (create_transaction, update_transaction,
# replace_splits, create_scheduled_transaction) all take a list of
# split dicts. Before this model existed, the tool signature was
# ``splits: list[dict]`` — pydantic doesn't descend into bare ``dict``,
# so the inner ``amount`` / ``quantity`` values round-tripped through
# whatever type the JSON parser produced. When a client sent a bare
# JSON number (``"amount": 94.87``), the parser emitted a float, and
# ``Decimal(split["amount"])`` inside the book method inherited the
# IEEE-754 epsilon ( ``0.8699999999999999955591...`` ) — causing
# spurious "splits do not balance" errors on non-dyadic decimals.
#
# ``SplitInput`` enforces ``amount`` / ``quantity`` as strings at the
# MCP boundary. With ``coerce_numbers_to_str=True`` pydantic routes a
# stray JSON number through Python's ``str()`` (shortest-repr) before
# we ever construct a Decimal — so ``94.87`` becomes ``"94.87"``, not
# a noisy float. Internal book methods also wrap every
# user-derived ``Decimal(...)`` call with ``_to_decimal(...)`` as
# belt-and-suspenders for direct callers (tests, scripts) that
# bypass this layer.
```

### _helpers.py:179-186 | REWRITE | rule 4
silent-typo'd-key trap stays; cut 'Pre-fix this used extra=ignore' and 'HP-10 from specs/CODE_REVIEW_v1_3.md' (dead pointer)

```
    # ``extra="forbid"`` matches the server-global setting on
    # ``ArgModelBase`` (set in server.py at import time). Pre-fix
    # this used ``extra="ignore"``, which silently dropped typo'd
    # keys: ``{"quantitiy": "10"}`` instead of ``{"quantity": "10"}``
    # would discard the quantity entirely and the transaction would
    # post with cross-currency value/quantity mismatch (or with the
    # default-zero behavior, depending on the path). HP-10 from
    # specs/CODE_REVIEW_v1_3.md.
```

### _helpers.py:276-287 | REWRITE | rule 2
ensure_ascii rationale stays; cut 'Bookkeeper-flagged on a CNY-default test book' sentence

```
def _json(obj) -> str:
    """Serialize to minified JSON, stripping noise values.

    ``ensure_ascii=False`` so non-ASCII strings (Chinese commodity
    names like "贵州茅台", customer names with accented characters,
    etc.) round-trip as raw UTF-8 instead of being escaped to
    ``\\uXXXX`` form. The escape behavior is technically valid JSON
    but makes the wire format unreadable for human reviewers and
    breaks any downstream substring match on the original text.
    Bookkeeper-flagged on a CNY-default test book where every
    SSE/SZSE commodity name came back as escape sequences.
    """
```

### _helpers.py:355-362 | REWRITE | rule 2
reject-unknown-owner_type rationale stays; cut 'Pre-fix: venddor → coerced…' and '(Copilot review on PR #86.)'

```
    # Only None and 'customer' fall through to "customer" coercion.
    # Anything else (typos like 'venddor', unknown future types) is
    # rejected here so the LLM gets a clear validation error instead
    # of a silent coercion that masks the typo as "search customer
    # invoices, find nothing, return not-found." Pre-fix: 'venddor'
    # → coerced to 'customer' → book-layer lookup against customer
    # invoices → "invoice not found", with no indication the input
    # was misspelled. (Copilot review on PR #86.)
```

### _helpers.py:466-477 | REWRITE | rule 3
dedicated error_type rationale stays; compress 'Pre-fix this masked write-verification failures behind the same error_type'

```
        except RuntimeError as e:
            # ``_verify_write`` / ``_verify_composite_write`` /
            # ``_verify_delete`` and the per-method
            # ``_verify_transaction_state`` raise ``RuntimeError``
            # specifically for "the write didn't land" — a critical
            # correctness signal that should NOT collapse into the
            # generic "unexpected_error" bucket. Pre-fix this
            # masked write-verification failures behind the same
            # error_type as e.g. ``KeyError`` lookup failures, so
            # callers couldn't tell "the write failed" from "we
            # tried to read a missing key."
            msg = str(e)
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `37-41` KEEP — # Re-exports from the layer-neutral format module. Tool wrappers can / # keep importing from ``tools._helpers`` (the historical home) without
- `47-53` KEEP — # ── Shared GUID parameter annotations ────────────────────────────── / #
- `130-132` KEEP — # Match the server-global ``extra="forbid"`` on / # ``ArgModelBase`` — typo'd address keys (``adr1`` instead of
- `329-335` KEEP — # ``business_complete`` is the leaf that owns vendor + employee / # management. The ``business`` MODULE_GROUPS alias expands to
- `418-423` KEEP — # Path redaction is applied at the MCP boundary (response / # going out to the LLM) but NOT to the internal logger.error
- `449-453` KEEP — # Subclass of ValueError — must be caught BEFORE the / # generic ValueError handler below, or the structured

## src/gnucash_mcp/tools/admin.py

### admin.py:16-23 | REWRITE | rule 4
path-traversal guardrail stays; strip 'pre-fix' framing and '(SB-15 from specs/CODE_REVIEW_v1_3.md)' (dead pointer)

```
# Audit log filenames are exactly ``YYYY-MM-DD.txt``. Validate
# ``log_date`` against this shape before constructing the path —
# pre-fix, ``Path(audit_dir) / f"{log_date}.txt"`` would happily
# interpolate ``../../../../etc/passwd`` and read arbitrary
# ``*.txt`` files. Prompt injection through any free-text field
# that surfaces into the audit log was the attack vector
# (SB-15 from specs/CODE_REVIEW_v1_3.md).
_LOG_DATE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
```

### admin.py:124-136 | REWRITE | rule 2/4
raise-through-safe_tool rationale stays; strip 'SB-15' tag and 'Copilot-flagged on PR #97'

```
        # SB-15 path-traversal gate. ``target_date`` is
        # interpolated into the log path; reject anything that
        # isn't a literal ``YYYY-MM-DD`` before the join so
        # ``../../../../etc/passwd`` style inputs can't escape
        # the audit directory.
        #
        # Raise rather than build the JSON envelope inline so
        # ``safe_tool`` handles the rejection through its standard
        # path: same JSON shape, plus the boundary-layer logger
        # warning AND path redaction applied to the error message
        # (``redact_paths`` in safe_tool's ValueError branch).
        # Copilot-flagged on PR #97 — inline envelope duplicated
        # the shape and skipped redaction.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `147-153` KEEP — # Cap the size we'll read in one shot. A long-lived / # deployment with daily growth could produce multi-MB
- `177-179` KEEP — # Entries are separated by blank lines. The first block is the / # day's header (box-drawing banner) — preserve it regardless of

## src/gnucash_mcp/tools/backup.py

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `26-30` KEEP — # Classified as read-w.r.t.-the-book: these tools touch disk but / # never mutate the GnuCash file. Read classification also prevents

## src/gnucash_mcp/tools/business.py

### business.py:1275-1281 | REWRITE | rule 2
Job header stays; trim '(v1.3)' version tag

```
    # ── Job CRUD tools ───────────────────────────────────────
    #
    # Jobs are project-level grouping over invoices/bills for a
    # single customer or vendor. The financial lifecycle stays
    # on the linked invoices; the job itself only has
    # ``active``/``inactive`` state. See create_invoice and
    # create_bill (v1.3) for how to link a new invoice to a job.
```

### business.py:1347-1351 | REWRITE | rule 4
uniform-_json + no-indent-bloat rationale stays; strip 'HP-4 sweep (v1.3)'

```
        # Match the other list_* tools' verbose pattern. HP-4
        # sweep (v1.3) routed all list_* verbose returns through
        # _json so the response shape is uniform; the previous
        # ``json.dumps(indent=2)`` form added 40-60% bloat from
        # indentation and skipped the ``_strip_noise`` pass.
```

### Remaining 3+-line `#` runs — all KEEP (rules 1/7)
Reviewed this session; no banned patterns, no history-talk, no falsehoods.

- `1475-1478` KEEP — # When Business isn't loaded, vendor_id is also a vendor-only / # surface — reject it the same way an explicit
- `1481-1484` KEEP — # Check the leaf (``business_complete``) rather than the / # ``business`` group alias, so a user who explicitly

## src/gnucash_mcp/tools/core.py

### core.py:92-108 | REWRITE | rule 2/4
deliberate double-fetch trade-off + canonical-echo contract stay, with verified pointer tests/test_book.py::TestCanonicalAccountEcho; strip 'MP-6:' and 'PR #56's bookkeeper work'

```
        # Resolve once to capture the canonical fullname for the
        # response. Echoing the path the caller passed in (or, when
        # they passed a %short, resolving to the readable form they'd
        # rather see back) gives a uniform contract: every tool that
        # echoes an account responds with the canonical full path.
        #
        # MP-6: this double-fetch (get_account here + get_balance
        # below) is a deliberate trade-off. Dropping the get_account
        # call would shave one indexed query per ``get_balance`` —
        # cheap on its own — but it would also break the "every
        # tool echoes canonical paths" contract that
        # ``TestCanonicalAccountEcho`` locks in PR #56's bookkeeper
        # work. The contract wins: a caller who passed ``%2e78c86``
        # gets back ``Assets:Current Assets:Savings`` and instantly
        # confirms which account they were asking about, with zero
        # ambiguity if they fat-fingered the prefix. The extra
        # query is the cost of that disambiguation.
```

## src/gnucash_mcp/tools/reporting.py

### reporting.py:92-97 | REWRITE | rule 2/3
None-vs-empty-string distinction stays; cut 'Pre-fix, an empty-string silently fell back to today… Copilot PR #92 review caught this'

```
        # Distinguish "not provided" (None → today) from "provided
        # but empty/garbage" (raise). Pre-fix, an empty-string
        # ``as_of_date=""`` silently fell back to today — a caller
        # bug that produced silently wrong-dated reports. Copilot
        # PR #92 review caught this; the strict-kwargs pattern
        # extends to the value, not just the parameter name.
```

---

## Whitelist — generic-role `bookkeeper` (and module name)

These remaining `bookkeeper` hits are audience/persona rationale or the
literal `bookkeeper` module name — legitimate per the doctrine's special
case; they survive the sweep and are exempt from the Phase B grep gate:

- `server.py:111` — `"bookkeeper": [`
- `server.py:863` — `bookkeeper   Reporting + budgets + scheduling.`
- `server.py:896` — `--modules=bookkeeper for personal finance;`
- `logging_config.py:1222` — `guard. Surfacing "forced" here gives the bookkeeper a traceable`
- `logging_config.py:1552` — `the cash-refund path (the bookkeeper sent cash to a customer`
- `tools/business.py:884` — `bookkeeper issues a credit note against an overcharge,`
- `tools/business.py:1461` — `Sorted most-overdue-first so the bookkeeper sees the urgent`
- `book/_base.py:916` — `# bookkeeper's intended directory. ``Path.resolve(strict=True)```
- `book/backup.py:223` — `# can surface backup-chain breaks the bookkeeper would otherwise`
- `book/investments.py:226` — `matches the bookkeeper's mental model. Pass explicitly`
- `book/reconciliation.py:385` — `# bookkeeper can match a statement balance regardless`
- `book/core.py:445` — `# work signal the bookkeeper plans against. "4 months`
- `book/core.py:935` — `# the warning so the bookkeeper knows the duration is`
- `book/core.py:1026` — `# Same number, honest framing: the bookkeeper`
- `book/core.py:1780` — `years behind" is the bookkeeper's planning number; "72`
- `book/core.py:1859` — `# reconcile. A bookkeeper looking at "4 months`
- `book/core.py:1866` — `# — useful when the bookkeeper is deciding which`
- `book/business.py:178` — `count tell the bookkeeper exactly which invoice is bleeding the`
- `book/business.py:323` — `counterparty a bookkeeper cares about. Callers that need`
- `book/business.py:1219` — `modern bookkeepers use. If a real workflow needs fax`
- `book/business.py:1343` — `a bookkeeper would scan for — customer jobs and vendor`
- `book/business.py:1491` — `customer invoice or vendor bill. From the bookkeeper's`
- `book/business.py:1557` — `# ``id`` and ``name`` are the surface a bookkeeper wants;`
- `book/business.py:1625` — `# column so a bookkeeper scanning a long invoice list`
- `book/business.py:4335` — `omitted for floating credit notes the bookkeeper will`
- `book/business.py:5293` — `# the LLM as a bookkeeper — "you can save $X if you pay`
- `book/business.py:5736` — `# (e.g. bookkeeper passes transaction_guid to`
- `book/business.py:7912` — `# bookkeeper sees the urgent receivables / bills at the top.`
- `book/business.py:7941` — `bookkeeper looking at a job's pipeline wants drafts in`
- `book/budgets.py:629` — `# render before/after diffs. Without this, the bookkeeper`
- `book/budgets.py:982` — `# — the bookkeeper can't tell what amounts/periods were`
- `book/scheduling.py:101` — `# Apr 30 → May 31, preserving the bookkeeper's "31st of every`
- `book/scheduling.py:853` — `# Stage prior state for the audit log so the bookkeeper`
- `book/scheduling.py:897` — `# bookkeeper can recover the schedule's identity from the`
- `book/reporting.py:961` — `neither is "cash flow" in the bookkeeper's sense and they`

Note: `tools/business.py:884` and `tools/business.py:1461` sit inside tool
docstrings (wire surface) — untouchable regardless.

## Test/helper pointers to be cited in rewritten comments

Each verified by reading/grepping this session:

- tests/test_book.py::TestCanonicalAccountEcho (line 3766) — verified
- tests/test_book.py::TestCrossToolPriceAgreement (line 8455) — verified (already cited at book/_currency.py:208; kept)
- tests/test_modules.py::TestToolFileVsModulesMapping — verified
- tests/test_contract_integrity.py::TestWriteVerificationCoverage — verified (cited in the business.py:7050 rewrite)
- specs/GET_BOOK_SUMMARY_SPEC.md — verified (existing pointers in book/core.py kept)
- specs/PIECASH_REFERENCE.md — verified (existing pointers in book/business.py kept)
- specs/archive/BACKUP_TOOL_SPEC.md — verified (book/backup.py:21 kept)
- docs/RESTORE_FROM_BACKUP.md — verified (book/backup.py + tools/backup.py kept)
- CLAUDE.md 'Every raw-SQL write is verified' / piecash-gotchas sections — verified (pointers in book/core.py, book/scheduling.py kept)
- Helpers cited in rewrites (_is_unreconciled, _resolve_account, _convert_invoice_amount, _commodity_quantum, _find_prices, _anchor_for_as_of, _validate_transaction_splits) — all read this session

**Dead pointer being removed (4 sites):** `specs/CODE_REVIEW_v1_3.md` —
the file moved to the untracked `specs/Code Reviews/Code Review 1.3/`
folder and is not in the repository; cited at book/admin.py:33,
book/admin.py:159, tools/_helpers.py:186, tools/admin.py:22. Each citation
is replaced with a self-contained statement — NOT re-pointed at the
untracked location. Policy: no comment may cite an untracked file.

## Phase B notes

- Wire-surface baseline: capture **descriptions AND input schemas** —
  `BusinessAddressInput` / `SplitInput` are pydantic models whose field
  descriptions (and possibly class docstrings) surface in tool schemas;
  the `_helpers.py:124` rewrite must be verified against a full-schema
  diff, not just (name, description) pairs.
- `server.py:618-624` error-message string mentions 'Partial-load was the
  previous behavior; v1.3 fails fast' — it is an executable string literal,
  out of scope for this sweep; logged for COMMENT_SWEEP_FINDINGS.md.
- Whitelisted `bookkeeper` lines above are excluded from acceptance grep #1.
- Additional whitelist: `server.py:886` ("Bookkeeper members (3): reporting,
  budgets, scheduling.") — module-group name inside the executable `--help`
  string; out of edit scope (executable line), exempt from grep gate #1.

## Stats

| file | KEEP | CUT | REWRITE | FIX-FALSE |
|---|---|---|---|---|
| server.py | 25 | 7 | 10 | 3 |
| _format.py | 1 | 0 | 2 | 0 |
| logging_config.py | 28 | 1 | 6 | 3 |
| book/__init__.py | 0 | 0 | 3 | 0 |
| book/_base.py | 15 | 1 | 12 | 1 |
| book/_query.py | 0 | 0 | 4 | 0 |
| book/_currency.py | 4 | 0 | 9 | 1 |
| book/admin.py | 0 | 0 | 4 | 0 |
| book/backup.py | 17 | 0 | 10 | 0 |
| book/budgets.py | 7 | 1 | 5 | 0 |
| book/business.py | 107 | 1 | 88 | 1 |
| book/core.py | 65 | 1 | 68 | 1 |
| book/investments.py | 16 | 1 | 15 | 0 |
| book/reconciliation.py | 8 | 0 | 9 | 0 |
| book/reporting.py | 19 | 1 | 34 | 2 |
| book/scheduling.py | 14 | 0 | 6 | 0 |
| tools/_helpers.py | 6 | 0 | 9 | 0 |
| tools/admin.py | 2 | 0 | 2 | 0 |
| tools/backup.py | 1 | 0 | 0 | 0 |
| tools/business.py | 2 | 0 | 2 | 0 |
| tools/core.py | 0 | 0 | 1 | 0 |
| tools/reporting.py | 0 | 0 | 1 | 0 |
| **total** | **337** | **14** | **300** | **12** |
