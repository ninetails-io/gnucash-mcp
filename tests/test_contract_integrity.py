"""Regression tests for the Branch 2 contract-integrity work.

Two contracts the codebase claimed but did not enforce:

- ``TestAuditLogDispatcherCoverage`` — HP-2: every
  ``@audit_log(classification="write", ...)`` decorator in
  ``tools/*.py`` must have a matching entry in
  ``_AUDIT_HANDLERS``. Pre-fix six write operations had decorators
  but no handler — the fallback returns ``""`` and the audit-log
  emitter gates on truthy, so they emitted NOTHING to the human-
  readable trail.

- ``TestNewAuditFormatters`` — smoke tests for the six new
  formatters added to close HP-2 (``commodity:CREATE``,
  ``price:CREATE``, ``lot:CREATE``, ``lot:UPDATE``,
  ``scheduled_transaction:CREATE``, ``budget:CREATE``). Each
  must produce a non-empty list with the expected header verb.

- ``TestWriteVerificationCoverage`` — HP-1 (commit 2 of branch):
  every raw-SQL write site in ``book/*.py`` must have a paired
  ``_verify_*`` call within a short window. ORM writes are
  implicitly verified by SQLAlchemy's commit — they don't need
  explicit verification.

If any of these tests fails without an intentional change to the
contract, the bug class is open again.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


# ── Helpers ────────────────────────────────────────────────────────


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOLS_DIR = _REPO_ROOT / "src" / "gnucash_mcp" / "tools"
_BOOK_DIR = _REPO_ROOT / "src" / "gnucash_mcp" / "book"


def _audit_log_decorators_in(
    path: Path,
) -> tuple[list[tuple[str, str, str]], list[tuple[int, dict[str, str]]]]:
    """Parse ``path`` and return two lists:

    1. ``valid``: ``(classification, entity_type, operation_uppercase)``
       triples for every well-formed ``@audit_log(...)`` decorator.
       Read decorators are included with empty entity/op; write
       decorators only appear here when both ``entity_type`` and
       ``operation`` are present and non-empty.
    2. ``malformed``: ``(line_number, kwargs_dict)`` for every
       ``classification="write"`` decorator that's MISSING
       ``entity_type`` or ``operation``. The dispatcher would
       silently drop those entries — a contract violation we want
       the test layer to surface loudly. Silently filtering them
       out here (the pre-Copilot-review behavior) made the
       coverage check vacuously pass on malformed input.

    Uses ``ast`` instead of regex so multi-line decorator calls and
    string interpolation in adjacent code don't trip it up.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    valid: list[tuple[str, str, str]] = []
    malformed: list[tuple[int, dict[str, str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match bare ``audit_log(...)`` calls; the decorator is
        # imported by name in every ``tools/*.py`` file.
        if not (isinstance(func, ast.Name) and func.id == "audit_log"):
            continue
        kwargs: dict[str, str] = {}
        for kw in node.keywords:
            if kw.arg is None:
                continue
            v = kw.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                kwargs[kw.arg] = v.value
        classification = kwargs.get("classification", "")
        entity = kwargs.get("entity_type", "")
        op = kwargs.get("operation", "")
        if classification == "write":
            if entity and op:
                valid.append((classification, entity, op.upper()))
            else:
                # ``@audit_log(classification="write")`` without
                # entity_type/operation passes the type checker but
                # silently drops from the dispatcher. Surface it.
                malformed.append((node.lineno, dict(kwargs)))
        else:
            # Read decorators or any other shape — pass through with
            # empty entity/op so the caller can still ignore them in
            # the contract checks (they don't gate on entity/op).
            valid.append((classification, entity, op.upper() if op else ""))
    return valid, malformed


# ── HP-2: dispatcher coverage ──────────────────────────────────────


class TestAuditLogDispatcherCoverage:
    """HP-2: every write decorator must resolve to a handler.

    Pre-fix six pairs were missing — the dispatch fallback returned
    the empty string and the emitter's truthy gate silently dropped
    the entry. Adding new write tools without dispatcher entries was
    a sharp edge that the codebase had stepped on six times by the
    time the code review caught it.
    """

    def test_no_malformed_write_decorators(self):
        """``@audit_log(classification="write")`` without
        ``entity_type`` or ``operation`` passes Python type-check
        (the decorator's kwargs default to optional) but silently
        drops from the dispatcher — there's no ``(None, None)`` key
        in ``_AUDIT_HANDLERS``. The contract test below uses the
        decorator triple to look up handlers; pre-Copilot-review
        the helper silently filtered malformed decorators out,
        making the coverage check vacuously pass on them.

        Fails loud here so the regression class is caught at the
        test layer, before downstream tests would have missed it.
        """
        all_malformed: list[str] = []
        for py_file in sorted(_TOOLS_DIR.glob("*.py")):
            _valid, malformed = _audit_log_decorators_in(py_file)
            for line_no, kwargs in malformed:
                missing_fields = [
                    f for f in ("entity_type", "operation")
                    if not kwargs.get(f)
                ]
                all_malformed.append(
                    f"{py_file.name}:{line_no}: "
                    f"@audit_log(classification=\"write\") missing "
                    f"{', '.join(missing_fields)} "
                    f"(would silently drop from audit log)"
                )
        assert not all_malformed, (
            "Malformed write decorators (missing entity_type or "
            "operation — would silently drop from the dispatcher):\n"
            + "\n".join(f"  {m}" for m in all_malformed)
        )

    def test_every_write_decorator_has_a_dispatcher_entry(self):
        from gnucash_mcp.logging_config import _AUDIT_HANDLERS

        missing: list[tuple[str, tuple[str, str]]] = []
        for py_file in sorted(_TOOLS_DIR.glob("*.py")):
            valid, _malformed = _audit_log_decorators_in(py_file)
            for classification, entity, op in valid:
                if classification != "write":
                    continue
                if (entity, op) not in _AUDIT_HANDLERS:
                    missing.append((py_file.name, (entity, op)))
        assert not missing, (
            "Write decorators without an _AUDIT_HANDLERS entry "
            "(the fallback returns '' and the audit emitter gates "
            "on truthy, so these would silently drop from the "
            "human-readable trail):\n"
            + "\n".join(f"  {f}: {pair}" for f, pair in missing)
        )

    def test_no_dispatcher_entries_for_undeclared_decorators(self):
        """Reverse direction: every ``_AUDIT_HANDLERS`` entry should
        correspond to a write decorator that actually exists. Catches
        stale handlers left behind after a tool rename / deletion.

        Nine dispatcher pairs are reached through runtime entity-
        type / operation remaps rather than direct decorator
        lookup — they're exempted below. The remaps live in two
        places: the invoice→bill/voucher/credit_note swap is in
        the ``audit_log`` decorator wrapper itself (around the
        ``entity_type == "invoice" and result_data.get("type") in
        {"bill", ...}`` check), while the account UPDATE→MOVE
        remap is in ``_format_audit_entry_text``.
        """
        from gnucash_mcp.logging_config import _AUDIT_HANDLERS

        declared: set[tuple[str, str]] = set()
        for py_file in sorted(_TOOLS_DIR.glob("*.py")):
            valid, _malformed = _audit_log_decorators_in(py_file)
            for classification, entity, op in valid:
                if classification == "write":
                    declared.add((entity, op))

        # Dispatcher entries reached through runtime remaps (the
        # decorated entity_type / operation gets rewritten before
        # the dispatcher lookup, so the literal pair never appears
        # as a decorator target). Two patterns, 3+3+3 = 9 pairs:
        #
        # 1. Invoice → bill / voucher / credit_note polymorphism
        #    for POST / UNPOST / PAY. The shared lifecycle tools
        #    decorate as ``entity_type="invoice"``; the ``audit_log``
        #    decorator wrapper (NOT _format_audit_entry_text)
        #    rewrites entity_type when the response's ``type``
        #    field is bill / voucher / credit_note. See the
        #    ``entity_type == "invoice" and result_data.get("type")``
        #    block in the decorator's success path.
        # 2. Account update with ``new_parent`` in params → MOVE.
        #    ``move_account`` decorates as ``operation="update"``;
        #    the operation is rewritten inside
        #    ``_format_audit_entry_text`` when ``new_parent`` is
        #    present in params.
        POLYMORPHIC_TARGETS: set[tuple[str, str]] = set()
        # Document family: registered as "invoice", swapped to the
        # response's type for the whole lifecycle (consolidated
        # create/delete included).
        for entity in ("bill", "voucher", "credit_note"):
            for op in ("POST", "UNPOST", "PAY", "CREATE", "DELETE"):
                POLYMORPHIC_TARGETS.add((entity, op))
        # Party family: registered as "customer", swapped to
        # vendor/employee from the response's type.
        for entity in ("vendor", "employee"):
            for op in ("CREATE", "UPDATE", "DELETE"):
                POLYMORPHIC_TARGETS.add((entity, op))
        POLYMORPHIC_TARGETS.add(("account", "MOVE"))
        # Retired-operation renderers: the deprecated singulars'
        # formatters, retained as the audit-decorator test
        # harness's vehicles (see the banner in logging_config).
        # Deleting a name here without porting the harness breaks
        # tests/test_logging.py — 1.5.x cleanup, together.
        RETIRED_OP_RENDERERS = {
            ("transaction", "CREATE"),
            ("transaction", "UPDATE"),
        }
        POLYMORPHIC_TARGETS |= RETIRED_OP_RENDERERS

        stale = (
            set(_AUDIT_HANDLERS.keys())
            - declared
            - POLYMORPHIC_TARGETS
        )
        assert not stale, (
            "_AUDIT_HANDLERS entries with no corresponding write "
            "decorator (possible stale handler after a rename "
            "or deletion):\n"
            + "\n".join(f"  {pair}" for pair in sorted(stale))
        )

    @pytest.mark.parametrize(
        "entity_op",
        [
            ("commodity", "CREATE"),
            ("price", "CREATE"),
            ("lot", "CREATE"),
            ("lot", "UPDATE"),
            ("scheduled_transaction", "CREATE"),
            ("budget", "CREATE"),
        ],
    )
    def test_six_previously_suppressed_pairs_are_registered(
        self, entity_op,
    ):
        """The six pairs the code review specifically called out as
        suppressed (HP-2) must each be in the dispatcher."""
        from gnucash_mcp.logging_config import _AUDIT_HANDLERS
        assert entity_op in _AUDIT_HANDLERS, (
            f"{entity_op} missing from _AUDIT_HANDLERS — "
            f"the dispatcher would fall back to '' and the audit "
            f"emitter would silently drop the entry."
        )


# ── HP-2: per-formatter rendering smoke tests ──────────────────────


class TestNewAuditFormatters:
    """Each of the six new formatters produces a non-empty list with
    the expected header verb. These are smoke tests — the dispatcher
    coverage tests above lock the contract; these confirm the
    handlers actually return useful output (not just ``[]``)."""

    @staticmethod
    def _entry(operation: str, entity_type: str, **kwargs) -> dict:
        """Build a minimal entry dict shaped like the audit emitter
        feeds the formatter."""
        return {
            "classification": "write",
            "operation": operation,
            "entity_type": entity_type,
            "timestamp": "2026-06-04T12:34:56-07:00",
            **kwargs,
        }

    def test_commodity_create_renders(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        rendered = _format_audit_entry_text(self._entry(
            "create", "commodity",
            params={"namespace": "FUND", "mnemonic": "VTSAX"},
            after_state={
                "namespace": "FUND", "mnemonic": "VTSAX",
                "fullname": "Vanguard Total Stock Market",
                "fraction": 10000, "status": "created",
            },
        ))
        assert "CREATE COMMODITY  FUND:VTSAX" in rendered
        assert "Vanguard Total Stock Market" in rendered

    def test_price_create_renders_for_fresh_write(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        rendered = _format_audit_entry_text(self._entry(
            "create", "price",
            params={
                "namespace": "FUND", "commodity": "VTSAX",
                "value": "250.45", "currency": "USD",
            },
            after_state={
                "namespace": "FUND", "commodity": "VTSAX",
                "date": "2026-06-01", "value": "250.45",
                "currency": "USD", "type": "nav", "status": "created",
            },
        ))
        assert "CREATE PRICE  FUND:VTSAX" in rendered
        assert "2026-06-01" in rendered

    def test_price_create_renders_update_verb_when_overwriting(self):
        """``create_price`` overwrites existing prices at the same
        (commodity, currency, date, source) — the response's
        ``status="updated"`` signals this. Header verb should reflect
        the actual operation."""
        from gnucash_mcp.logging_config import _format_audit_entry_text
        rendered = _format_audit_entry_text(self._entry(
            "create", "price",
            params={"namespace": "FUND", "commodity": "VTSAX"},
            after_state={
                "namespace": "FUND", "commodity": "VTSAX",
                "date": "2026-06-01", "value": "250.45",
                "currency": "USD", "status": "updated",
            },
        ))
        assert "UPDATE PRICE  FUND:VTSAX" in rendered
        assert "CREATE PRICE" not in rendered

    def test_lot_create_renders(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        rendered = _format_audit_entry_text(self._entry(
            "create", "lot",
            params={
                "account": "Assets:Investments:VTSAX",
                "title": "VTSAX 2026-01-15 purchase",
            },
            after_state={
                "title": "VTSAX 2026-01-15 purchase",
                "account": "Assets:Investments:VTSAX",
                "status": "created",
            },
        ))
        assert 'CREATE LOT  "VTSAX 2026-01-15 purchase"' in rendered
        assert "Assets:Investments:VTSAX" in rendered

    def test_lot_update_renders_assign_branch(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        rendered = _format_audit_entry_text(self._entry(
            "update", "lot",
            params={
                "split_guid": "abcd1234",
                "lot_guid": "ef567890",
            },
            after_state={
                "status": "assigned", "quantity": "10.0000",
                "cost_basis": "1000.00", "is_closed": False,
            },
        ))
        assert "ASSIGN SPLIT TO LOT" in rendered
        assert "abcd1234" in rendered
        assert "ef567890" in rendered

    def test_lot_update_renders_close_branch(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        rendered = _format_audit_entry_text(self._entry(
            "update", "lot",
            params={"guid": "abcd1234"},
            after_state={
                "guid": "abcd1234",
                "title": "VTSAX 2026-01-15 purchase",
                "status": "closed",
            },
        ))
        assert 'CLOSE LOT  "VTSAX 2026-01-15 purchase"' in rendered
        assert "ASSIGN SPLIT" not in rendered

    def test_scheduled_transaction_create_renders(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        rendered = _format_audit_entry_text(self._entry(
            "create", "scheduled_transaction",
            params={
                "name": "Monthly Rent",
                "description": "Rent payment",
                "start_date": "2026-01-01",
                "frequency": "monthly",
            },
            after_state={
                "name": "Monthly Rent", "frequency": "monthly",
                "next_occurrence": "2026-01-01", "status": "created",
            },
        ))
        assert 'CREATE SCHEDULED  "Monthly Rent"' in rendered
        assert "monthly" in rendered
        assert "2026-01-01" in rendered

    def test_budget_create_renders(self):
        from gnucash_mcp.logging_config import _format_audit_entry_text
        rendered = _format_audit_entry_text(self._entry(
            "create", "budget",
            params={
                "name": "2026 Budget", "year": 2026,
                "num_periods": 12, "period_type": "monthly",
            },
            after_state={"name": "2026 Budget", "status": "created"},
        ))
        assert 'CREATE BUDGET  "2026 Budget"' in rendered
        assert "12" in rendered
        assert "monthly" in rendered

    def test_each_formatter_returns_non_empty(self):
        """End-to-end coverage: every newly-added (entity, op) pair
        renders to a non-empty string when given a minimal but
        well-formed entry. Catches the regression class where a
        handler returns ``[]`` (which the emitter would still drop
        as falsy)."""
        from gnucash_mcp.logging_config import _format_audit_entry_text
        for entity, op, params, after in [
            ("commodity", "create",
             {"namespace": "FUND", "mnemonic": "X"},
             {"mnemonic": "X", "namespace": "FUND",
              "status": "created"}),
            ("price", "create",
             {"namespace": "FUND", "commodity": "X"},
             {"namespace": "FUND", "commodity": "X",
              "date": "2026-01-01", "value": "1.0",
              "currency": "USD", "status": "created"}),
            ("lot", "create",
             {"account": "Assets:X", "title": "T"},
             {"title": "T", "account": "Assets:X",
              "status": "created"}),
            ("lot", "update",
             {"split_guid": "a", "lot_guid": "b"},
             {"status": "assigned"}),
            ("scheduled_transaction", "create",
             {"name": "X", "start_date": "2026-01-01",
              "frequency": "monthly"},
             {"name": "X", "frequency": "monthly",
              "status": "created"}),
            ("budget", "create",
             {"name": "X", "year": 2026, "num_periods": 12,
              "period_type": "monthly"},
             {"name": "X", "status": "created"}),
        ]:
            rendered = _format_audit_entry_text(self._entry(
                op, entity, params=params, after_state=after,
            ))
            assert rendered, (
                f"({entity!r}, {op.upper()!r}) handler produced "
                f"empty output — the emitter would drop this entry "
                f"from the audit log."
            )


# ── HP-1: write-verification contract ──────────────────────────────


class TestWriteVerificationCoverage:
    """HP-1: the codebase claimed "every write is verified" but the
    actual contract is two-tier:

    - **ORM writes** (``book.session.add(obj)``, attribute mutation,
      ``book.session.delete(obj)``) rely on SQLAlchemy's commit-side
      verification — any constraint violation, missing FK, or
      stale-object failure raises during ``book.save()``. Explicit
      ``_verify_*`` would be redundant.
    - **Raw-SQL writes** (``book.session.execute(Table.__table__
      .insert/update/delete(...))``) need explicit verification —
      SQLAlchemy executes the SQL but can't tell whether the WHERE
      clause matched any rows or the INSERT actually landed.

    This class locks the second tier. Every raw-SQL DML site in
    ``book/*.py`` must have a paired ``_verify_*`` call within a
    short window. The contract test catches the regression class
    where someone adds a new ``book.session.execute(Table.insert())``
    without the matching verify (or accidentally drops a verify
    during a refactor) — the audit identified this happening five
    times in the codebase's history before the contract was
    codified.
    """

    # Window size: 40 lines. The existing call sites all verify
    # within ~30 lines; 40 leaves headroom for slightly longer
    # insert payloads without becoming so generous the test misses
    # a real gap.
    _VERIFY_WINDOW = 40

    def _scan_dml_sites(self) -> list[tuple[Path, int, str, str]]:
        """Yield ``(path, line, table, op)`` for every raw-SQL DML
        site in ``book/*.py``."""
        import re
        dml = re.compile(
            r"(\w+)\.__table__\.(insert|delete|update)\(\)"
        )
        out: list[tuple[Path, int, str, str]] = []
        for py_file in sorted(_BOOK_DIR.glob("*.py")):
            lines = py_file.read_text().splitlines()
            for i, line in enumerate(lines, 1):
                m = dml.search(line)
                if not m:
                    continue
                out.append((py_file, i, m.group(1), m.group(2)))
        return out

    def test_every_raw_sql_dml_site_has_paired_verify(self):
        import re
        verify_pattern = re.compile(
            r"_verify_(write|composite_write|delete)\b"
        )
        missing: list[str] = []
        for path, line_no, table, op in self._scan_dml_sites():
            lines = path.read_text().splitlines()
            window = "\n".join(
                lines[line_no:line_no + self._VERIFY_WINDOW]
            )
            if not verify_pattern.search(window):
                missing.append(
                    f"{path.name}:{line_no}: "
                    f"{table}.__table__.{op}()  → no _verify_* "
                    f"within {self._VERIFY_WINDOW} lines"
                )
        assert not missing, (
            "Raw-SQL DML sites without a paired _verify_* helper. "
            "SQLAlchemy can't tell whether the SQL changed any rows; "
            "the verify reads back the affected key and raises if "
            "the round-trip doesn't match.\n"
            + "\n".join(f"  {m}" for m in missing)
        )

    def test_dml_site_scan_finds_known_sites(self):
        """Sanity check on the scanner itself — ensure it actually
        finds the call sites we know exist. Catches a future
        refactor that hides DML behind a wrapper the regex doesn't
        recognize, which would silently make the previous test
        vacuous (no sites → no failures)."""
        sites = self._scan_dml_sites()
        # Known invariants of the current codebase: budgets,
        # business, and scheduling all do raw-SQL DML.
        files_with_dml = {p.name for p, _, _, _ in sites}
        assert "budgets.py" in files_with_dml
        assert "business.py" in files_with_dml
        assert "scheduling.py" in files_with_dml
        # Should be more than a handful of sites total.
        assert len(sites) >= 10, (
            f"Scanner found only {len(sites)} DML sites — "
            f"likely the regex regressed or a refactor hid them"
        )
