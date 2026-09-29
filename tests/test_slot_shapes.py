"""Every KVP slot this server writes is a shape GnuCash reads.

The storage invariant (CLAUDE.md): the server never invents a storage
shape for a GnuCash object. Four migrations shipped because it did —
schedule recipes, the invoice link key, budget signs, and the due-date
slot — each found one at a time by desktop refusing to read it. This
file is the structural answer: a REGISTRY of every slot key the server
writes, typed the way GnuCash's own source types it, and three checks
that fail when a write disagrees with it.

1. Static: every slot write site in ``book/*.py`` names a registered
   key and, where the type is visible at the site, the registered type.
2. Census: every ``(name, slot_type)`` row in the committed sample
   books is a registered shape, a legacy shape ``_upgrade_book_shapes``
   converts, a server-namespaced key, or a user-defined flat key.
3. Runtime: a void written today lands as desktop would write it.

A key GnuCash defines is cited to the source line that defines it.
Add a new write site by adding its key here first, with the citation.
"""

from __future__ import annotations

import ast
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from piecash.kvp import KVP_Type

from gnucash_mcp.book import GnuCashBook

_REPO = Path(__file__).resolve().parent.parent
_BOOK_DIR = _REPO / "src" / "gnucash_mcp" / "book"
_SAMPLES = _REPO / "samples"

K = KVP_Type

# ── The registry: key → (type, GnuCash source) ─────────────────────
# Types are GnuCash's, read from the C/C++ that writes each key
# (stable branch, read 2026-09-28/29). ``None`` type = a frame child
# namespace whose children are typed by their own entries.
REGISTRY: dict[str, tuple[KVP_Type, str]] = {
    # Business documents — gncInvoice.c
    "gncInvoice": (K.KVP_TYPE_FRAME, "gncInvoice.c GNC_INVOICE_ID"),
    "gncInvoice/invoice-guid": (K.KVP_TYPE_GUID, "gncInvoice.c GNC_INVOICE_GUID"),
    "credit-note": (K.KVP_TYPE_GINT64, "gncInvoice.c GNC_INVOICE_IS_CN (gboolean → int64)"),
    # Transactions — Transaction.cpp
    "trans-txn-type": (K.KVP_TYPE_STRING, "Transaction.cpp TRANS_TXN_TYPE_KVP, set_kvp_string_path"),
    "trans-read-only": (K.KVP_TYPE_STRING, "Transaction.cpp TRANS_READ_ONLY_REASON"),
    "trans-date-due": (K.KVP_TYPE_TIMESPEC, "Transaction.cpp xaccTransSetDateDue: Time64 (SQL: timespec)"),
    "date-posted": (K.KVP_TYPE_GDATE, "Transaction.cpp xaccTransSetDatePostedGDate"),
    "notes": (K.KVP_TYPE_STRING, "Transaction.cpp trans_notes_str; Account.cpp 'notes'; gnc-lot.cpp 'notes'"),
    "from-sched-xaction": (K.KVP_TYPE_GUID, "Transaction.cpp GNC_SX_FROM"),
    # Void — Transaction.cpp xaccTransVoid, Split.cpp xaccSplitVoid
    "void-reason": (K.KVP_TYPE_STRING, "Transaction.cpp void_reason_str"),
    "void-time": (K.KVP_TYPE_STRING, "Transaction.cpp void_time_str, gnc_time64_to_iso8601_buff"),
    "void-former-notes": (K.KVP_TYPE_STRING, "Transaction.cpp void_former_notes_str"),
    "void-former-amount": (K.KVP_TYPE_NUMERIC, "Split.cpp void_former_amt_str, gnc_numeric"),
    "void-former-value": (K.KVP_TYPE_NUMERIC, "Split.cpp void_former_val_str, gnc_numeric"),
    # Lots — gnc-lot.cpp
    "title": (K.KVP_TYPE_STRING, "gnc-lot.cpp gnc_lot_set_title"),
    # Accounts — Account.cpp
    "placeholder": (K.KVP_TYPE_STRING, "Account.cpp 'placeholder' (boolean stored as string)"),
    "reconcile-info": (K.KVP_TYPE_FRAME, "Account.cpp KEY_RECONCILE_INFO"),
    "reconcile-info/last-date": (K.KVP_TYPE_GINT64, "Account.cpp xaccAccountSetReconcileLastDate (time64)"),
    "reconcile-info/last-interval": (K.KVP_TYPE_FRAME, "Account.cpp xaccAccountSetReconcileLastInterval"),
    "reconcile-info/last-interval/months": (K.KVP_TYPE_GINT64, "Account.cpp xaccAccountSetReconcileLastInterval"),
    "reconcile-info/last-interval/days": (K.KVP_TYPE_GINT64, "Account.cpp xaccAccountSetReconcileLastInterval"),
    "reconcile-info/include-children": (K.KVP_TYPE_GINT64, "Account.cpp xaccAccountSetReconcileChildrenStatus"),
    # Scheduled-transaction templates — SchedXaction.h / SX-book.h
    "sched-xaction": (K.KVP_TYPE_FRAME, "SchedXaction.h GNC_SX_ID"),
    "sched-xaction/account": (K.KVP_TYPE_GUID, "SchedXaction.h GNC_SX_ACCOUNT"),
    "sched-xaction/credit-formula": (K.KVP_TYPE_STRING, "SchedXaction.h GNC_SX_CREDIT_FORMULA"),
    "sched-xaction/debit-formula": (K.KVP_TYPE_STRING, "SchedXaction.h GNC_SX_DEBIT_FORMULA"),
    "sched-xaction/credit-numeric": (K.KVP_TYPE_NUMERIC, "SchedXaction.h GNC_SX_CREDIT_NUMERIC"),
    "sched-xaction/debit-numeric": (K.KVP_TYPE_NUMERIC, "SchedXaction.h GNC_SX_DEBIT_NUMERIC"),
    # Book — gnc-features.cpp, qofbook.cpp
    "features": (K.KVP_TYPE_FRAME, "gnc-features.cpp GNC_FEATURES"),
    "features/Use natural signs in budget amounts": (K.KVP_TYPE_STRING, "gnc-features.cpp: feature name → description string"),
    "counters": (K.KVP_TYPE_FRAME, "qofbook.cpp 'counters'"),
}

# Children of a GnuCash frame whose names vary (counters/gncInvoice …).
_DYNAMIC_CHILDREN: dict[str, KVP_Type] = {
    "counters/": K.KVP_TYPE_GINT64,
}

# The server's own namespace. Desktop ignores keys it doesn't know;
# the convention (CLAUDE.md, slot key naming) keeps them under one
# frame so they can never collide with a GnuCash key.
_SERVER_NAMESPACE = "gnc-mcp"

# Shapes the server shipped before 1.5 and converts on the first
# write through ``_upgrade_book_shapes``. Allowed on disk, never at a
# write site.
_LEGACY_ON_DISK: dict[tuple[str, KVP_Type], str] = {
    ("splits-json", K.KVP_TYPE_STRING): "1.2–1.4.4 schedule recipe (_migrate_all_legacy)",
    ("description", K.KVP_TYPE_STRING): "1.2–1.4.4 schedule description (same)",
    ("invoice", K.KVP_TYPE_GUID): "pre-1.5 invoice link child (_migrate_invoice_link_keys)",
    ("gncInvoice/invoice", K.KVP_TYPE_GUID): "desktop's re-save of the above (same)",
    ("void-former-value", K.KVP_TYPE_STRING): "pre-fix void, string (_migrate_void_shapes)",
    ("void-former-quantity", K.KVP_TYPE_STRING): "pre-fix void, invented key (same)",
}

# User-defined account slots (set_account_slot) and the bare
# conventions the tools read (apr, credit_limit, is_retirement …):
# flat keys, always strings, and never a GnuCash key.
_USER_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# GnuCash's own void-time format (gnc-datetime.cpp delim_iso regex):
# "YYYY-MM-DD HH:MM:SS[.frac] [+-]HH[:MM]" — a space, never a 'T'.
GNC_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{0,9})?\s*"
    r"(?:[+-]\d{2}(?::?\d{2})?)?$"
)


# A user-defined flat key may not borrow a GnuCash key family: the
# segment before the first hyphen of every registered hyphenated key
# ("void-", "trans-", "sched-", …) is reserved, so an invented
# "void-former-quantity" can never pass as a user key.
_RESERVED_FAMILIES = {k.split("-")[0] for k in REGISTRY if "-" in k}


def _registered(key: str) -> tuple[KVP_Type | None, str]:
    """(expected type, how the key is allowed) or (None, reason) when
    it is not allowed at all. A frame's own row is typed FRAME. A key
    with a ``{…}`` hole (an f-string at the write site) is allowed
    when every registry key it can spell agrees on the type."""
    if "{…}" in key:
        pattern = re.compile("^" + re.escape(key).replace(r"\{…\}", r"[^/]+") + "$")
        matches = [t for k, (t, _src) in REGISTRY.items() if pattern.match(k)]
        if matches and len(set(matches)) == 1:
            return matches[0], "registry (pattern)"
        return None, "UNREGISTERED"
    if key in REGISTRY:
        return REGISTRY[key][0], "registry"
    for prefix, typ in _DYNAMIC_CHILDREN.items():
        if key.startswith(prefix):
            return typ, "registry (dynamic child)"
    if key == _SERVER_NAMESPACE:
        return K.KVP_TYPE_FRAME, "server namespace frame"
    if key.startswith(_SERVER_NAMESPACE + "/"):
        return None, "server namespace"
    if "-" in key and key.split("-")[0] in _RESERVED_FAMILIES:
        return None, "UNREGISTERED"
    if _USER_KEY_RE.match(key) and "/" not in key:
        return K.KVP_TYPE_STRING, "user flat key"
    return None, "UNREGISTERED"


# ── 1. Static: every write site ────────────────────────────────────

_ENTITY_NAMES = {
    "txn", "transaction", "split", "account", "acct", "new_account",
    "lot", "book", "sx", "inv", "invoice", "entity", "root",
}
_KVP_ATTRS = {t.name: t for t in KVP_Type}


def _resolve_str(node: ast.AST, module, klass) -> str | None:
    """A slot key at a write site: a constant, a module constant, a
    class constant (``self._X`` / ``Cls._X``), or an f-string whose
    static prefix is a registered frame path."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return getattr(module, node.id, None) if isinstance(
            getattr(module, node.id, None), str) else None
    if isinstance(node, ast.Attribute):
        owners = []
        if isinstance(node.value, ast.Name) and node.value.id != "self":
            owners.append(getattr(module, node.value.id, None))
        owners.extend(klass)
        owners.append(module)
        for owner in owners:
            v = getattr(owner, node.attr, None) if owner is not None else None
            if isinstance(v, str):
                return v
        return None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                inner = _resolve_str(v.value, module, klass)
                parts.append(inner if inner is not None else "{…}")
        return "".join(parts)
    return None


def _value_type(node: ast.AST) -> KVP_Type | None:
    """piecash types a bracket-assigned value by its Python type
    (kvp.py ``slot()``): str → STRING, Decimal → NUMERIC, int →
    GINT64, datetime → TIMESPEC, date → GDATE, ORM object → GUID.
    Classify the obvious expression shapes; None when unsure."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return None
        if isinstance(node.value, str):
            return K.KVP_TYPE_STRING
        if isinstance(node.value, int):
            return K.KVP_TYPE_GINT64
    if isinstance(node, ast.JoinedStr):
        return K.KVP_TYPE_STRING
    if isinstance(node, ast.Call):
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else "")
        if name in ("str", "isoformat", "format", "strftime"):
            return K.KVP_TYPE_STRING
        if name == "Decimal":
            return K.KVP_TYPE_NUMERIC
        if name == "int":
            return K.KVP_TYPE_GINT64
    if isinstance(node, ast.Attribute) and node.attr in ("value", "quantity"):
        return K.KVP_TYPE_NUMERIC
    if isinstance(node, ast.Attribute) and node.attr == "guid":
        return K.KVP_TYPE_STRING  # a guid string, not a GUID slot
    return None


def _write_sites() -> list[tuple[str, int, str, KVP_Type | None]]:
    """[(file, line, key, type_or_None)] for every slot write in
    book/*.py: Core inserts on the slots table, ``_slot_insert``
    calls, and bracket assignments on ORM entities."""
    import importlib

    sites = []
    for path in sorted(_BOOK_DIR.glob("*.py")):
        module = importlib.import_module(f"gnucash_mcp.book.{path.stem}")
        tree = ast.parse(path.read_text(), filename=str(path))
        # Every class in the module: ``self._X`` may resolve on any.
        klass = [
            getattr(module, node.name)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and hasattr(module, node.name)
        ]
        for node in ast.walk(tree):
            # Slot.__table__.insert().values(name=…, slot_type=…)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "values"
                and any(k.arg == "slot_type" for k in node.keywords)
            ):
                key = typ = None
                for kw in node.keywords:
                    if kw.arg == "name":
                        key = _resolve_str(kw.value, module, klass)
                    if kw.arg == "slot_type" and isinstance(kw.value, ast.Attribute):
                        typ = _KVP_ATTRS.get(kw.value.attr)
                if key is not None:
                    sites.append((path.name, node.lineno, key, typ))
                continue
            # self._slot_insert(book, guid, name, KVP_Type.X, …)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_slot_insert"
                and len(node.args) >= 4
            ):
                key = _resolve_str(node.args[2], module, klass)
                typ = (
                    _KVP_ATTRS.get(node.args[3].attr)
                    if isinstance(node.args[3], ast.Attribute) else None
                )
                if key is not None:
                    sites.append((path.name, node.lineno, key, typ))
                continue
            # entity["key"] = value  (piecash __setitem__)
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Subscript)
            ):
                target = node.targets[0]
                base = target.value
                base_name = (
                    base.id if isinstance(base, ast.Name)
                    else base.attr if isinstance(base, ast.Attribute) else ""
                )
                if base_name not in _ENTITY_NAMES and base_name != "root_account":
                    continue
                key = _resolve_str(target.slice, module, klass)
                if key is None:
                    # A dynamic key (set_account_slot's user key):
                    # unchecked statically unless the value is
                    # visibly not a string; the runtime layer
                    # covers what lands.
                    vt = _value_type(node.value)
                    if vt is None or vt == K.KVP_TYPE_STRING:
                        continue
                    sites.append((path.name, node.lineno, "<dynamic>", vt))
                    continue
                sites.append((path.name, node.lineno, key, _value_type(node.value)))
    return sites


def test_every_write_site_names_a_registered_key_with_its_type():
    sites = _write_sites()
    assert sites, "the scanner found no slot write sites — it is broken"
    problems = []
    for file, line, key, typ in sites:
        if key == "<dynamic>":
            problems.append(f"{file}:{line}: dynamic slot key with a non-string value")
            continue
        expected, how = _registered(key)
        if how == "UNREGISTERED":
            problems.append(
                f"{file}:{line}: writes slot {key!r}, which GnuCash does not "
                f"define and this registry does not allow"
            )
        elif expected is not None and typ is not None and typ != expected:
            problems.append(
                f"{file}:{line}: writes {key!r} as {typ.name}; GnuCash stores "
                f"it as {expected.name} ({REGISTRY.get(key, ('', how))[1]})"
            )
    assert not problems, (
        "slot writes that disagree with GnuCash's shape:\n  "
        + "\n  ".join(problems)
    )


# ── 2. Census: the committed sample books ──────────────────────────

@pytest.mark.parametrize(
    "sample", ["alex-chen-morales.gnucash", "lin-wei.gnucash", "sabine-brenner.gnucash"],
)
def test_sample_books_hold_only_registered_shapes(sample):
    path = _SAMPLES / sample
    if not path.exists():
        pytest.skip(f"{sample} not present")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT DISTINCT name, slot_type FROM slots ORDER BY name"
    ).fetchall()
    con.close()
    problems = []
    for name, slot_type in rows:
        typ = KVP_Type(slot_type)
        if (name, typ) in _LEGACY_ON_DISK:
            # The frozen samples predate the converters; every
            # entry here names the converter that rewrites it on
            # the first write.
            continue
        expected, how = _registered(name)
        if how == "UNREGISTERED":
            problems.append(f"{name} ({typ.name}): not a shape GnuCash or this server defines")
        elif expected is not None and typ != expected:
            problems.append(
                f"{name}: stored as {typ.name}, GnuCash stores it as {expected.name}"
            )
    assert not problems, (
        f"{sample} holds slot rows desktop cannot read:\n  " + "\n  ".join(problems)
    )


# ── 3. Runtime: a void written today ───────────────────────────────

def _slots_for(book_path: Path, obj_guids: list[str]) -> list[tuple]:
    con = sqlite3.connect(f"file:{book_path}?mode=ro", uri=True)
    q = ",".join("?" * len(obj_guids))
    rows = con.execute(
        f"SELECT obj_guid, name, slot_type, string_val, numeric_val_num, "
        f"numeric_val_denom FROM slots WHERE obj_guid IN ({q}) ORDER BY name",
        obj_guids,
    ).fetchall()
    con.close()
    return rows


def test_void_lands_the_way_desktop_writes_it(test_book):
    """xaccTransVoid / xaccSplitVoid: the transaction carries
    void-reason and void-time (GnuCash's ISO 8601, a space before the
    time) as strings and is marked read-only; each split carries
    void-former-amount and void-former-value as NUMERIC. Nothing
    else, and no key GnuCash does not define."""
    gc = GnuCashBook(str(test_book))
    created = gc.create_transaction(
        description="To be voided",
        splits=[
            {"account": "Assets:Checking", "amount": "-42.50"},
            {"account": "Expenses:Groceries", "amount": "42.50"},
        ],
        trans_date=date.today() - timedelta(days=1),
    )
    gc.void_transaction(created["guid"], reason="test void")
    with gc.open(readonly=True) as book:
        txn = gc._find_transaction(book, created["guid"])
        guids = [txn.guid] + [s.guid for s in txn.splits]
        txn_guid = txn.guid
    rows = _slots_for(test_book, guids)
    by_obj: dict[str, dict[str, tuple]] = {}
    for obj, name, typ, sval, num, denom in rows:
        by_obj.setdefault(obj, {})[name] = (KVP_Type(typ), sval, num, denom)

    problems = []
    txn_slots = by_obj.get(txn_guid, {})
    for key in ("void-reason", "void-time", "trans-read-only"):
        if key not in txn_slots:
            problems.append(f"transaction lacks {key}")
        elif txn_slots[key][0] != K.KVP_TYPE_STRING:
            problems.append(f"transaction {key} is {txn_slots[key][0].name}, not STRING")
    vt = txn_slots.get("void-time")
    if vt and not GNC_ISO8601_RE.match(vt[1] or ""):
        problems.append(
            f"void-time {vt[1]!r} is not GnuCash's ISO 8601 "
            f"(gnc-datetime.cpp wants 'YYYY-MM-DD HH:MM:SS +HHMM')"
        )
    for obj, slots in by_obj.items():
        if obj == txn_guid:
            continue
        for key in ("void-former-amount", "void-former-value"):
            if key not in slots:
                problems.append(f"split {obj[:8]} lacks {key}")
            elif slots[key][0] != K.KVP_TYPE_NUMERIC:
                problems.append(
                    f"split {obj[:8]} {key} is {slots[key][0].name}, not NUMERIC"
                )
        for key in slots:
            if _registered(key)[1] == "UNREGISTERED":
                problems.append(f"split {obj[:8]} carries {key!r}, a key GnuCash does not define")
    assert not problems, "void differs from desktop's:\n  " + "\n  ".join(problems)
