"""AdminMixin — account slot CRUD.

Provides get/set/delete for piecash account slots (KVP metadata).
Slots are used to store per-account data like APR, credit limit,
statement close day, etc. Values are stored as strings.
"""

import re

from gnucash_mcp.book._base import _slot_value_str  # noqa: F401  (re-exported for callers)
from gnucash_mcp.book._base import (
    _SLOT_TEXT_WIDTH,
    _check_control_chars,
    _check_text,
)

# Slot keys with embedded ``/`` create hierarchical sub-slots in
# GnuCash's KVP store rather than flat keys. The MCP-facing
# account-slot tools only manage flat keys (``apr``,
# ``credit_limit``, ``minimum_payment``, etc.), so we restrict
# user input to a safe alphabet up-front. A key like
# ``credit/limit`` would silently create a sub-slot under ``credit`` —
# invisible to ``get_account_slots`` keyed lookups.
#
# Note: internal slot keys (set by our own book methods, not
# accepted from users) can and do use ``/`` for namespacing —
# see the ``gnc-mcp/...`` convention in
# ``BusinessMixin._APPLIES_TO_SLOT_KEY``. This regex gates
# USER input only.
_SLOT_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# GnuCash's own top-level account keys the user-slot tools must not
# touch. Two kinds, each pinned from the GnuCash 5.12 source:
#
# - FRAMES desktop owns. Writing a string at the key breaks desktop's
#   reader (and the server's own reconcile writer); deleting the frame
#   through the ORM cascades through any GUID slot inside it and
#   deletes every slot of the account that slot points at (adversarial
#   review 2026-09-30, C8). Account.cpp: KEY_RECONCILE_INFO
#   ("reconcile-info"), KEY_LOT_MGMT ("lot-mgmt"),
#   KEY_ASSOC_INCOME_ACCOUNT ("ofx/associated-income-account"),
#   KEY_BALANCE_LIMIT ("balance-limit"), IMAP_FRAME ("import-map"),
#   IMAP_FRAME_BAYES ("import-map-bayes"), {"tax-US", …},
#   xaccAccountSetAssociatedAccount ("associated-account/<tag>");
#   gnc-ab-kvp.c: "hbci".
# - FLAGS that mirror a column. Desktop reads ``placeholder`` and
#   ``hidden`` from the KVP slot (xaccAccountGetPlaceholder /
#   xaccAccountGetHidden) while this server reads the column, so a
#   slot written alone makes the two disagree about the account.
#
# ``gnc-mcp`` is this server's own frame (designated-account GUIDs).
# String keys desktop stores exactly as a user slot would (``notes``,
# ``color``, ``tax-related``, ``last-num``, ``equity-type``) stay
# writable: same row either way.
_RESERVED_ACCOUNT_KEYS: dict[str, str] = {
    "reconcile-info": "GnuCash's reconcile state; use reconcile_account",
    "lot-mgmt": "GnuCash's lot-management frame",
    "ofx": "GnuCash's OFX import links",
    "import-map": "GnuCash's import matcher data",
    "import-map-bayes": "GnuCash's import matcher data",
    "balance-limit": "GnuCash's balance-limit frame",
    "tax-US": "GnuCash's tax-report settings",
    "associated-account": "GnuCash's associated-account links",
    "hbci": "GnuCash's online-banking settings",
    "gnc-mcp": "this server's own state",
    "placeholder": (
        "a flag GnuCash keeps in step with the account record; "
        "use update_account(placeholder=...)"
    ),
    "hidden": (
        "a flag GnuCash keeps in step with the account record; "
        "use update_account(hidden=...)"
    ),
}


def _check_user_slot_key(key: str, action: str) -> None:
    """The one gate on a user-supplied account slot key: flat
    alphabet, and not one of GnuCash's (or this server's) own keys.
    Shared by set and delete so the two can't diverge."""
    if not _SLOT_KEY_RE.fullmatch(key):
        raise ValueError(
            f"Invalid slot key {key!r}: must match [A-Za-z0-9_.-]+. "
            f"Embedded '/' would target hierarchical sub-slots "
            f"(internal namespaced state); use flat keys."
        )
    why = _RESERVED_ACCOUNT_KEYS.get(key)
    if why is not None:
        raise ValueError(
            f"Cannot {action} slot {key!r}: reserved — it is {why}."
        )


def _check_user_slot_row(slot, key: str, action: str) -> None:
    """A user slot is a plain string row. Anything else at the key —
    a frame, a GUID, a number, a date — is structured data some other
    writer owns: overwriting it changes its type under that writer,
    and ORM-deleting a frame or GUID slot cascades into the slots of
    whatever it references. Refuse by SHAPE so a GnuCash key this
    module has never heard of is still safe."""
    from piecash.kvp import KVP_Type
    if slot.slot_type != KVP_Type.KVP_TYPE_STRING:
        kind = slot.slot_type.name.removeprefix("KVP_TYPE_").lower()
        raise ValueError(
            f"Cannot {action} slot {key!r}: it holds structured "
            f"{kind} data, not a user metadata string."
        )


# Upper bound on slot value length. 64 KiB is generous for any
# legitimate per-account metadata (APR strings, credit limits,
# statement-close-day, structured JSON config blobs) — well past
# what real bookkeeping needs but short enough that a malicious
# or runaway caller can't exhaust the book file with a single
# slot write.
_SLOT_VALUE_MAX_BYTES = 64 * 1024


class AdminMixin:
    """Account slot operations.

    Merged into GnuCashBook when the 'admin' module is enabled.
    Depends on `self.open()` and `self._find_account()` from
    BaseGnuCashBook.
    """

    def get_account_slots(
        self, account_name: str, key: str | None = None
    ) -> dict:
        """Read all slots (or a specific slot) from an account.

        Args:
            account_name: Full account path.
            key: Specific slot key to retrieve. If None, return all slots.

        Returns:
            Dict with account name and slots dict.

        Raises:
            ValueError: If account not found.
        """
        with self.open(readonly=True) as book:
            account = self._resolve_account(book, account_name)
            if not account:
                raise self._account_not_found_error(book, account_name)

            if key is not None:
                try:
                    slots = {key: _slot_value_str(account[key])}
                except KeyError:
                    slots = {}
            else:
                slots = {}
                for k, v in account.iteritems():
                    slots[k] = _slot_value_str(v)

            return {
                "account": account.fullname,
                "slots": slots,
            }

    def set_account_slot(
        self, account_name: str, key: str, value: str
    ) -> dict:
        """Set a single key-value pair on an account.

        Args:
            account_name: Full account path.
            key: Slot key (e.g., "apr", "credit_limit"). Restricted
                to ``[A-Za-z0-9_.-]``; embedded ``/`` would create
                hierarchical sub-slots in GnuCash's KVP store
                rather than a flat key (the slot would be
                invisible to keyed lookups). Reject up front
                rather than create silently-wrong storage.
            value: Slot value. Stored as string.

        Returns:
            Dict with status ("created" or "updated"). Input parameters are
            not echoed — the audit log captures them from tool params.

        Raises:
            ValueError: If account not found or key contains
                disallowed characters.
        """
        _check_user_slot_key(key, "set")
        _check_control_chars(value, "slot value")
        _check_text(key, _SLOT_TEXT_WIDTH, "slot key")
        _check_text(value, _SLOT_TEXT_WIDTH, "slot value")
        # Length cap. Encode to UTF-8 to count bytes (so a
        # multi-byte unicode payload can't sneak past a char-count
        # check). 64 KiB is generous for any real per-account
        # metadata. Compute the byte length once; reusing
        # ``value.encode(...)`` would allocate a fresh copy of the
        # already-large string.
        value_bytes = len(value.encode("utf-8"))
        if value_bytes > _SLOT_VALUE_MAX_BYTES:
            raise ValueError(
                f"Slot value too long: "
                f"{value_bytes} bytes exceeds the "
                f"{_SLOT_VALUE_MAX_BYTES}-byte cap. Store large "
                f"structured data outside the book."
            )
        with self.open(readonly=False) as book:
            account = self._resolve_account(book, account_name)
            if not account:
                raise self._account_not_found_error(book, account_name)

            existing = False
            try:
                slot = account[key]
                existing = True
            except KeyError:
                pass
            if existing:
                _check_user_slot_row(slot, key, "set")

            account[key] = value
            book.save()

            return {"status": "updated" if existing else "created"}

    def delete_account_slot(self, account_name: str, key: str) -> dict:
        """Remove a slot from an account.

        Args:
            account_name: Full account path.
            key: Slot key to remove.

        Returns:
            Dict with status. Input parameters are not echoed — the audit
            log captures them from tool params.

        Raises:
            ValueError: If account not found, key contains disallowed
                characters, or key not found.
        """
        # Same gate as ``set_account_slot``. Without it, delete could
        # target internal namespaced slots
        # (``gnc-mcp/applies-to-invoice``, etc.) or GnuCash's own
        # frames.
        _check_user_slot_key(key, "delete")
        with self.open(readonly=False) as book:
            account = self._resolve_account(book, account_name)
            if not account:
                raise self._account_not_found_error(book, account_name)

            try:
                slot = account[key]
            except KeyError:
                raise ValueError(f"Slot key not found: {key}")
            # Only a plain string row is ORM-deleted here: a frame or
            # GUID slot would cascade (see _check_user_slot_row), so
            # this ``del`` can never reach one.
            _check_user_slot_row(slot, key, "delete")

            del account[key]
            book.save()

            return {"status": "deleted"}
