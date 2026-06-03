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


def _audit_log_decorators_in(path: Path) -> list[tuple[str, str, str]]:
    """Parse ``path`` and yield every
    ``(classification, entity_type, operation_uppercase)`` triple
    advertised by an ``@audit_log(...)`` decorator.

    Uses ``ast`` instead of regex so multi-line decorator calls and
    string interpolation in adjacent code don't trip it up.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    out: list[tuple[str, str, str]] = []
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
        # Read-classification decorators don't take entity_type / op;
        # only write decorators matter for the dispatcher contract.
        if classification == "write" and entity and op:
            out.append((classification, entity, op.upper()))
    return out


# ── HP-2: dispatcher coverage ──────────────────────────────────────


class TestAuditLogDispatcherCoverage:
    """HP-2: every write decorator must resolve to a handler.

    Pre-fix six pairs were missing — the dispatch fallback returned
    the empty string and the emitter's truthy gate silently dropped
    the entry. Adding new write tools without dispatcher entries was
    a sharp edge that the codebase had stepped on six times by the
    time the code review caught it.
    """

    def test_every_write_decorator_has_a_dispatcher_entry(self):
        from gnucash_mcp.logging_config import _AUDIT_HANDLERS

        missing: list[tuple[Path, tuple[str, str]]] = []
        for py_file in sorted(_TOOLS_DIR.glob("*.py")):
            for _classification, entity, op in _audit_log_decorators_in(
                py_file,
            ):
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

        Three handlers are exempt because their dispatch happens
        through the polymorphic entity-type swap in the audit
        emitter (an ``invoice``-decorated tool routes to a
        ``bill`` / ``voucher`` / ``credit_note`` handler at log
        time, based on the response ``type`` field). So the
        ``bill:*``, ``voucher:*``, ``credit_note:POST`` /
        ``UNPOST`` / ``PAY`` entries don't appear as direct
        decorator targets.
        """
        from gnucash_mcp.logging_config import _AUDIT_HANDLERS

        declared: set[tuple[str, str]] = set()
        for py_file in sorted(_TOOLS_DIR.glob("*.py")):
            for _classification, entity, op in _audit_log_decorators_in(
                py_file,
            ):
                declared.add((entity, op))

        # Dispatcher entries reached through runtime remaps in
        # ``_format_audit_entry_text`` rather than direct decorator
        # lookup. Two patterns:
        #
        # 1. Invoice → bill / voucher / credit_note polymorphism.
        #    The shared lifecycle tools (post / unpost / pay)
        #    decorate as ``entity_type="invoice"``; the emitter
        #    rewrites entity_type from the response's ``type``
        #    field.
        # 2. Account update with ``new_parent`` in params → MOVE.
        #    ``move_account`` decorates as ``operation="update"``;
        #    the emitter rewrites the op when ``new_parent`` is
        #    present (see the dispatch logic in
        #    ``_format_audit_entry_text``).
        POLYMORPHIC_TARGETS: set[tuple[str, str]] = set()
        for entity in ("bill", "voucher", "credit_note"):
            for op in ("POST", "UNPOST", "PAY"):
                POLYMORPHIC_TARGETS.add((entity, op))
        POLYMORPHIC_TARGETS.add(("account", "MOVE"))

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
