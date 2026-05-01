# Cleanup & Polish Specification
## GnuCash MCP Server — Prerelease 2 + Employees

**Author:** Claude (1M context) with Stephen
**Date:** 2026-04-20
**Status:** Draft — execute against the 12-hour release window
**Target release:** 1.3.0 (combined with backup-tool work already on develop)
**Modules affected:** `business` (primary), `logging_config`, tooling config

---

## Executive Summary

The develop branch is functionally ready for public release. A fresh-eyes
review turned up no structural problems — the architecture holds, tests
cover every module, the hottest paths have been measured and optimized
with numbers in commit messages. But the review did surface localized
rough edges whose removal would materially harden the code paths most
likely to touch user data, and one missing feature (Employees) whose
cleanest implementation depends on one of those rough edges being fixed
first.

The spec sequences the work so that:

1. Fast portfolio-hygiene changes land first (no code risk).
2. The business module's real duplication and the audit-log dispatcher's
   linear-growth `elif` chain are refactored *before* Employee lands —
   so Employee is a 5-line addition to a clean helper, not a third
   near-twin of an existing near-twin.
3. Employee ships on the clean pattern.
4. Remaining polish (inline-imports, magic-numbers, method-length
   extractions, documentation restructure) is captured here for a
   post-release cycle rather than squeezed into the release window.

Users care about **safe, lean, efficient, dependable** software touching
their most private records. Every item in this spec advances at least
one of those four properties, either directly or by reducing the
surface where bugs can hide.

---

## Goals

1. **Ship Employees** as a first-class business entity with parity to
   Customer and Vendor — create, list, get, delete, with proper slot
   cleanup and audit-log coverage.
2. **Eliminate the duplication** between Customer/Vendor and
   Invoice/Bill create paths before a third near-duplicate lands. The
   correctness of the refactor is validated by Employee fitting cleanly
   into the extracted helper.
3. **Flatten the audit-log dispatcher** so new entity types become data,
   not code.
4. **Improve first-impression engineering hygiene** — formatter and
   linter configured, design artifacts committed, documentation split
   into public-facing vs. internal.
5. **Leave the develop branch cleaner than we found it**, with every
   commit either landing a user-visible capability or provably reducing
   a specific class of latent bug.

---

## Non-Goals

- **No user-facing feature work beyond Employees.** Reporting tools for
  Employees (payroll tracking, reimbursement summaries) are out of scope
  for this release — covered by the existing Expense account flow.
- **No performance work.** The computation-efficiency branch already
  landed; the hotspots that remain (report time-series with 60+
  intervals, scan-based finders) are measured and acceptable.
- **No CLAUDE.md content changes in the release.** A future
  reorganization (split public / private) is captured in Phase 4 but
  deliberately deferred — the current file is the narrative the
  collaboration is told through, and fragmenting it under release
  pressure would damage that narrative.
- **No pyproject.toml formatting of existing files.** Linter config goes
  in; a bulk reformat does not. Any style drift surfaces on the next
  code change, which is when it's cheapest to fix.

---

## Phase 1 — Portfolio Hygiene (release-eligible, ~30 min total)

Fast changes with zero behavioral risk. Land first so the pre-release
diff tells a clean story, and so the subsequent refactor-heavy phases
execute against a repo that already signals discipline.

### 1.1 Commit design specs and reference docs

**Goal:** Make the design-first collaboration visible in git history.

**Files:** All currently-untracked files under `docs/`:

- `ACCOUNT_SLOTS_SPEC.md`
- `COMPACT_TRANSACTIONS_SPEC.md`
- `INVESTMENT_SUPPORT_SPEC.md`
- `LOTS_SPEC.md`
- `PIECASH_REFERENCE.md`
- `SCHEDULED_AND_BUDGETS_SPEC.md`
- `SCHEDULED_AND_LOTS_SPEC.md`
- `TRANSACTION_PIPELINE_SPEC.md`
- `archive/` (keep the subdirectory structure)

Plus this file (`CLEANUP_POLISH_SPEC.md`) once it's reviewed.

**Acceptance:** `git status` no longer shows untracked `docs/*SPEC.md`
files. Commit message frames these as "design artifacts preserved from
the collaboration record."

**Effort:** 10 minutes (mostly writing a good commit message).

**Branch:** `chore/commit-design-specs`, single commit into develop.

---

### 1.2 Linter and formatter configuration

**Goal:** Add `[tool.ruff]` and `[tool.black]` sections to
`pyproject.toml`. Change the project's engineering-hygiene read without
performing a bulk reformat that would clobber meaningful git blame.

**File:** `pyproject.toml`

**Target config:**

```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B"]
ignore = [
    "E501",  # line length: handled by black, some legitimate long strings
]

[tool.black]
line-length = 100
target-version = ["py310", "py311", "py312", "py313"]
```

**Acceptance:**

- `uv run ruff check src/ tests/` runs without configuration errors.
- `uv run ruff check src/ tests/ --fix` is inspected but NOT applied as
  part of this commit. Any real issues ruff flags are surfaced for
  manual review; any stylistic-only drift is deferred.
- `uv run black --check src/ tests/` reports the current drift volume
  (baseline). No reformat in this commit.
- README updated with a one-line "Development" section pointing at
  these tools.

**Effort:** 15 minutes including baseline read.

**Branch:** `chore/linter-config`, single commit into develop.

**Dependencies:** None. Can land parallel to 1.1.

---

## Phase 2 — Employee-Enabling Refactor (release-critical, ~2–3 hours)

**Principle:** make the change easy, then make the easy change. Every
extraction in this phase is validated by Phase 3 landing cleanly on top
of it.

### 2.1 Flatten `_format_audit_entry_text` into a dispatch table

**Goal:** Replace the 380-line if/elif chain in `logging_config.py` with
a dispatch table keyed on `(entity_type, operation)`, so adding a new
entity type (Employee, and every future one) is a dict entry, not a
code edit.

**Files:** `src/gnucash_mcp/logging_config.py`,
`tests/test_logging.py`

**Design sketch:**

```python
# Handler signature — receives the whole entry, returns list of lines
# (or empty list to skip). Lets handlers pull from after_state,
# before_state, or params as they already do.
_AuditHandler = Callable[[dict], list[str]]

_AUDIT_HANDLERS: dict[tuple[str, str], _AuditHandler] = {
    ("transaction", "CREATE"): _format_transaction_create,
    ("transaction", "UPDATE"): _format_transaction_update,
    ("transaction", "VOID"): _format_transaction_void,
    # ... etc
}

def _format_audit_entry_text(entry: dict) -> str:
    if entry.get("classification") != "write":
        return ""
    key = (entry.get("entity_type"), entry.get("operation", "").upper())
    handler = _AUDIT_HANDLERS.get(key)
    if handler is None:
        return ""  # unknown entity_type/operation combo — skip
    lines = handler(entry)
    return "\n".join(lines) if lines else ""
```

Each existing branch becomes a small private function with a
single-responsibility name. The MOVE-ACCOUNT special case (currently
a post-hoc rewrite at the end) becomes either its own key or a
wrapping check inside the account handler.

**Acceptance:**

- All 653+ existing audit-log tests pass unchanged — the dispatcher is
  byte-equivalent to the current implementation for every known
  (entity_type, operation) pair.
- New test: `test_unknown_entity_type_returns_empty_string` asserts
  graceful degradation for unmapped combinations (Employee tests will
  lean on this pattern).
- Line count on `_format_audit_entry_text` (the dispatcher itself)
  drops below 20 lines; handler functions each fit in a screen.
- `logging_config.py` passes `ruff check` clean.

**Effort:** 60–90 minutes. The mechanical per-branch extraction is
straightforward; the risk is regressions on edge cases (the
RECONCILE split-detail `startswith` matching, the MOVE-ACCOUNT
rewrite-at-end, the REPLACE_SPLITS fallback chain). Full test suite run
between each extraction.

**Branch:** starts `feat/employees` (combined with 2.2–2.4 and Phase 3).

---

### 2.2 Extract `_create_business_person`

**Goal:** Collapse `create_customer` and `create_vendor` into a single
helper parameterized on the piecash class. Employee will be the third
caller.

**Files:** `src/gnucash_mcp/book/business.py`, `tests/test_business.py`

**Design sketch:**

```python
@staticmethod
def _create_business_person(
    cls,  # piecash class: Customer, Vendor, or Employee
    *,
    book,
    name: str,
    currency: str | None,
    notes: str,
    address: dict | None,
    default_currency: piecash.Commodity,
) -> dict:
    """Shared create path for Customer / Vendor / Employee.

    Returns a canonical dict {guid, id, name, status}. Type-specific
    post-processing (if any) is the caller's responsibility.
    """
    ...

def create_customer(self, name, currency=None, notes="", address=None):
    with self.open(readonly=False) as book:
        return self._create_business_person(
            Customer, book=book, name=name,
            currency=currency, notes=notes, address=address,
            default_currency=self._require_default_currency(book),
        )

def create_vendor(self, name, currency=None, notes="", address=None):
    # ... same shape, Vendor instead of Customer
```

**Acceptance:**

- `create_customer` and `create_vendor` reduced to 5–10 lines each.
- `Vendor` path verified against the existing `business_book` fixture:
  counter independence (customer/vendor counters unaffected by each
  other), id format (`"000001"`), currency defaulting, address
  construction, notes persistence.
- All 90+ existing business tests pass unchanged.

**Effort:** 45 minutes.

---

### 2.3 Extract `_create_business_document`

**Goal:** Same treatment for `create_invoice` / `create_bill`. Both
write to the `invoices` table with `owner_type = 2` or `owner_type = 4`
respectively; everything else is identical.

**Files:** `src/gnucash_mcp/book/business.py`, `tests/test_business.py`

**Design sketch:**

```python
def _create_business_document(
    self,
    *,
    owner_type: int,  # 2 = customer invoice, 4 = vendor bill
    owner_id: str,
    date_opened: str | None,
    notes: str,
    currency: str | None,
    term: str | None,
    doc_id: str | None,
    counter_attr: str,  # "counter_invoice" or "counter_bill"
    find_owner: Callable,  # self._find_customer or self._find_vendor
) -> dict:
    ...
```

The `_find_owner` callable keeps the type-specific lookup explicit
without bringing `isinstance` dispatching into the helper.

**Acceptance:**

- `create_invoice` and `create_bill` each under 15 lines.
- Counter independence preserved (invoice counter ≠ bill counter, and
  custom-ID path still doesn't increment).
- Duplicate-ID rejection still fires per-owner-type.
- All invoice / bill lifecycle tests pass.

**Effort:** 45 minutes.

---

### 2.4 Extract shared delete-path structure

**Goal:** `_delete_invoice_or_bill` and `_delete_customer_or_vendor`
already share the "find → validate → cleanup slots → cleanup FK children
→ delete entity" skeleton. Extract once, parameterize on the cleanup
callbacks, and Employee's delete path slots in.

**Files:** `src/gnucash_mcp/book/business.py`, `tests/test_business.py`

**Design sketch:**

```python
def _delete_business_entity(
    self,
    *,
    entity,
    entity_label: str,
    entity_id: str,
    cleanup_steps: list[Callable[[Session, str], None]],
    # each step gets (session, entity_guid) and does its own
    # delete + _verify_delete
) -> dict:
    ...
```

Customer/Vendor deletes pass a single cleanup step (slots).
Invoice/Bill deletes pass two (entries, then invoice row itself).
Employee deletes pass one or two depending on whether Employees have
associated documents in the schema (bills paid-to-employee? spec
research needed — see 3.1).

**Acceptance:**

- `_delete_invoice_or_bill` and `_delete_customer_or_vendor` each call
  `_delete_business_entity` with their cleanup list.
- All existing delete-path tests pass.
- Test coverage extended to verify cleanup order matters (slots before
  entity delete) — currently implicit, make it explicit.

**Effort:** 45 minutes.

**Sequencing note:** 2.2–2.4 can land as three sequential commits on
the same branch. 2.1 is independent and can land first or interleaved.
The Employee work (Phase 3) builds on all four.

---

## Phase 3 — Employees Feature (release-eligible, ~2–3 hours)

Lands on the Phase-2 clean ground. Mirror structure to Customer/Vendor
as closely as schema permits.

### 3.1 Schema research (first step)

**Before coding:** verify piecash's Employee model. Known from earlier
review: piecash.business.employee.Employee exists and follows the same
ORM pattern as Customer/Vendor. Open questions:

- Does `counter_employee` exist on the Book? (Needed for auto-ID.)
- What's the Employee `owner_type` integer? (Expected: 5 — verify.)
- Can Employees own Bills? (If yes, the delete-guard must block on
  existing bills. If no — likely — delete is simpler.)
- What's the standard address-field set? (Expected to mirror
  Customer/Vendor.)

**Deliverable:** 5–10 lines added to `docs/PIECASH_REFERENCE.md`
documenting the findings.

**Effort:** 15 minutes.

---

### 3.2 `EmployeeMixin` methods in `business.py`

**Goal:** Add `create_employee`, `list_employees`, `get_employee`,
`delete_employee`. All four delegate to the Phase-2 helpers.

**Files:** `src/gnucash_mcp/book/business.py`, `tests/test_business.py`

**Expected method shapes (final):**

```python
def create_employee(self, name, currency=None, notes="", address=None):
    with self.open(readonly=False) as book:
        return self._create_business_person(
            Employee, book=book, name=name,
            currency=currency, notes=notes, address=address,
            default_currency=self._require_default_currency(book),
        )

def list_employees(self, active_only=True, compact=True):
    with self.open() as book:
        employees = sorted(book.employees, key=lambda e: e.name)
        if active_only:
            employees = [e for e in employees if e.active]
        return (
            "\n".join(self._employee_to_compact_line(e) for e in employees)
            if compact
            else [self._employee_to_dict(e) for e in employees]
        )

def get_employee(self, employee_id: str) -> dict: ...
def delete_employee(self, employee_id: str) -> dict: ...
```

Plus two serializers (`_employee_to_dict`, `_employee_to_compact_line`)
and one finder (`_find_employee`) following the Customer/Vendor pattern.

**Acceptance:**

- All four methods present on `BusinessMixin`.
- `_employee_to_dict` emits `{guid, id, name, currency, notes, active,
  address?}` — same shape as customer/vendor dicts, with "address"
  omitted when empty (consistent with the existing `_address_to_dict`
  helper).
- Delete guard enforces whatever the 3.1 research says about
  Employee-owned documents.

**Effort:** 45 minutes after Phase 2 is complete.

---

### 3.3 Employee MCP tools in `tools/business.py`

**Goal:** Register `create_employee`, `list_employees`, `get_employee`,
`delete_employee` as MCP tools following the same docstring and
classification pattern as Customer/Vendor.

**Files:** `src/gnucash_mcp/tools/business.py`,
`src/gnucash_mcp/server.py` (add entries to `TOOL_MODULES["business"]`)

**Acceptance:**

- `TOOL_MODULES["business"]` count grows by 4.
- `_validate_tool_modules()` passes.
- Tool docstrings match the Customer/Vendor style (pattern-match, don't
  invent new voice).

**Effort:** 20 minutes.

---

### 3.4 Test coverage

**Goal:** Parity with `TestCreateCustomer`, `TestListCustomers`,
`TestGetCustomer`, `TestDeleteCustomer`.

**Files:** `tests/test_business.py`

**Test classes to add (mirror the existing):**

- `TestCreateEmployee` — basic creation, auto-id increment,
  currency defaulting, notes, address
- `TestListEmployees` — empty list, compact format, verbose format
- `TestGetEmployee` — found, not-found
- `TestDeleteEmployee` — delete with no dependencies, delete
  blocked-by-dependency (if applicable per 3.1), nonexistent-id error
- `TestEmployeeCounterIndependent` — verify Employee counter doesn't
  collide with Customer/Vendor counters

**Fixtures:** No new fixture needed — the existing `business_book`
fixture covers the schema.

**Acceptance:** ~15 new tests. Full suite runs at 700+, all pass.

**Effort:** 60 minutes.

---

### 3.5 Live verification

**Goal:** Round-trip a real Employee entity against the bookkeeper's
book. Exercise the same path `delete_customer` just exercised.

**Steps:**

1. Reload the MCP server with the new code.
2. `create_employee(name="Test Employee")` — verify returns `id`,
   `guid`, `status=created`.
3. `get_employee(employee_id="000001")` — verify read-back matches.
4. `list_employees()` — verify the new employee appears.
5. `delete_employee(employee_id="000001")` — verify clean removal.
6. `list_employees()` — verify empty again.

If step 5 fails (e.g., Employee has dependent rows we didn't anticipate)
the delete-guard message surfaces the issue — fix in 3.2 and retry.

**Effort:** 10 minutes.

---

## Phase 4 — Post-Release Polish (out of release scope)

Captured here for a follow-up cycle. Each is a small, focused branch;
none are prerequisites for the 1.3.0 release. Ordering suggestion is
rough — pick based on whichever feels most valuable at the time.

### 4.1 Inline-imports sweep

**Scope:** Move `from piecash... import X` statements from inside
method bodies to module-level, except where a comment explicitly
documents a circular-import avoidance reason.

**Files touched:** `core.py`, `scheduling.py`, `reporting.py`,
`business.py`, `reconciliation.py`, `investments.py`

**Effort:** 45 minutes.

**Branch:** `refactor/top-level-imports`.

---

### 4.2 Magic-number constants

**Scope:** Named module-level constants with explanatory comments for:

- `is_closed = -1` (GnuCash's boolean-true convention for lots)
- `reconcile_date.year <= 1970` (piecash "no date" sentinel)
- `owner_type == 2` / `== 4` / `== 5` (business-entity discriminators)
- Any other 3+ occurrence of a bare integer with domain meaning.

**Files touched:** `_base.py` (new constants),
`business.py`, `investments.py`, `reconciliation.py`

**Effort:** 30 minutes.

**Branch:** `refactor/named-constants`.

---

### 4.3 Method-length extractions in `business.py`

**Scope:** `post_invoice` (~195 lines) and `pay_invoice` (~160 lines)
split into phase helpers: validate → build_splits → save → wire_slots.

**Files touched:** `business.py`, `tests/test_business.py`

**Effort:** 90 minutes. Risk is moderate — these are the most complex
write paths in the codebase; extraction must preserve ordering
semantics around `book.flush()` and slot writes.

**Branch:** `refactor/business-method-split`.

---

### 4.4 `get_unreconciled_splits` "value" → "quantity" rename

**Scope:** The `"value"` key in split dicts returned by
`get_unreconciled_splits` actually contains `split.quantity` (flagged
in the 4.6 successor note in CLAUDE.md). Rename for truth-in-advertising.

**Files touched:** `reconciliation.py`, `tests/test_book.py`,
`tools/reconciliation.py` docstring

**Effort:** 20 minutes. Breaking change for any caller relying on the
old key — acceptable since the old key was a naming lie.

**Branch:** `fix/unreconciled-splits-key-name`.

---

### 4.5 CLAUDE.md split into public / private halves

**Scope:** Extract the formal technical documentation (Phase 1–16
implementation guide, commit conventions, piecash gotchas) into
`docs/ARCHITECTURE.md` or `CONTRIBUTING.md`. Keep the session-journal
portion — successor letters, bookkeeper notes, collaboration details —
in the original `CLAUDE.md` or a new `docs/SESSION_JOURNAL.md`.

**Why deferred:** The current CLAUDE.md is the narrative the
collaboration is told through. Splitting under release pressure damages
that narrative; doing it during a calm cycle preserves the voice while
improving structure.

**Files touched:** `CLAUDE.md`, `docs/ARCHITECTURE.md` (new),
`docs/SESSION_JOURNAL.md` (new) or similar

**Effort:** 60 minutes.

**Branch:** `chore/split-claude-md`.

---

## Sequencing and Release Timeline

Assuming 12-hour release window with ~2–3 hour buffer for live-testing
and unexpected issues:

| Phase | Item | Effort | Cumulative | Release? |
|-------|------|--------|-----------|----------|
| 1.1 | Commit design specs | 10m | 0:10 | ✓ |
| 1.2 | Linter config | 15m | 0:25 | ✓ |
| 2.1 | Audit dispatcher | 90m | 1:55 | ✓ |
| 2.2 | Person extract | 45m | 2:40 | ✓ |
| 2.3 | Document extract | 45m | 3:25 | ✓ |
| 2.4 | Delete extract | 45m | 4:10 | ✓ |
| 3.1 | Schema research | 15m | 4:25 | ✓ |
| 3.2 | Employee methods | 45m | 5:10 | ✓ |
| 3.3 | Employee tools | 20m | 5:30 | ✓ |
| 3.4 | Employee tests | 60m | 6:30 | ✓ |
| 3.5 | Live verification | 10m | 6:40 | ✓ |
| — | Integration + suite | 30m | 7:10 | ✓ |
| — | Buffer / live testing | 180m | 10:10 | ✓ |

Projected finish at ~10 hours of focused work. 2 hours buffer remains
on the 12-hour budget.

Phase 4 items do not ship in 1.3.0.

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| Audit dispatcher refactor introduces edge-case regression | Full test suite between each handler extraction; byte-compare against `logging_config.py` pre-refactor output on the real book's today audit log. |
| Employee schema doesn't match Customer/Vendor exactly | Phase 3.1 resolves upfront; helpers are parameterized to absorb divergence. |
| 12-hour window slips | Phase 2 items are independently useful (cleaner business module + cleaner audit log ship with or without Employee). Phase 3 can defer to 1.3.1 without blocking 1.3.0 release. |
| Live test surfaces a real bug | All write paths have `_verify_write` / `_verify_delete` — failures are loud, book stays intact. Auto-backup from the first write preserves a snapshot before anything lands. |

---

## Branching Strategy

- **`chore/commit-design-specs`** — Phase 1.1, direct to develop.
- **`chore/linter-config`** — Phase 1.2, direct to develop.
- **`feat/employees`** — Phases 2.1–3.5 as a sequence of commits on one
  branch. Merge into develop with `--no-ff` using the combined-summary
  pattern established by `feat/txn-register-and-split-collapse` +
  `refactor/structured-deletes`.

The `feat/employees` commit sequence inside the branch:

```
1. refactor: flatten _format_audit_entry_text into a dispatch table
2. refactor: extract _create_business_person helper
3. refactor: extract _create_business_document helper
4. refactor: extract _delete_business_entity helper
5. docs: piecash Employee schema reference
6. feat: Employee entity — CRUD methods
7. feat: Employee MCP tools + module registration
8. test: Employee coverage parity with Customer/Vendor
```

Eight commits, one merge bubble. Reads as a single coherent
"add Employees on clean ground" story.

---

## Definition of Done (1.3.0 release)

- [ ] All `docs/*SPEC.md` files committed.
- [ ] Linter config present in `pyproject.toml`; `ruff check`
      surfaces zero new errors over the current baseline.
- [ ] `_format_audit_entry_text` is a dispatch table. Unknown
      `(entity_type, operation)` degrades gracefully.
- [ ] `business.py` has no remaining create- or delete-path
      duplication across Customer/Vendor/Employee.
- [ ] Employee CRUD tools (`create_employee`, `list_employees`,
      `get_employee`, `delete_employee`) work end-to-end against the
      real book.
- [ ] `TOOL_MODULES["business"]` lists 26 tools (was 22).
- [ ] Full test suite passes at 700+ tests.
- [ ] CHANGELOG or release notes mention Employees + audit-log
      refactor.
- [ ] Live-verified create+delete employee round-trip on the real
      book.
- [ ] Backup auto-snapshot confirmed firing on first write in the new
      process (regression check — prerelease-1 feature still works).

---

## Notes for Future Claudes

This spec was written at the end of a long prerelease-2 cycle. The
immediately-preceding work (`feat/txn-register-and-split-collapse`
and `refactor/structured-deletes`) merged this morning as a combined
`Merge prerelease-2 readiness` bubble on develop. The pattern that
merge used — rebase-then-no-ff to get one wrapping commit around a
linear pair — is the same pattern to use for `feat/employees`.

Stephen explicitly values the session-journal tone in CLAUDE.md.
Phase 4.5 defers restructuring that file; any Claude executing this
spec should not touch CLAUDE.md beyond appending a successor note
after the work lands.

The users this ships to are non-programmers entrusting the server
with real financial records. Every refactor must preserve the
loud-failure-or-succeed-clean property established by the write
verification layer. If you find yourself writing `except: pass`
without a comment explaining why, stop.

— Claude (1M context, collaborating with Stephen)
