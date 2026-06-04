"""AdminMixin — account slot CRUD.

Provides get/set/delete for piecash account slots (KVP metadata).
Slots are used to store per-account data like APR, credit limit,
statement close day, etc. Values are stored as strings.
"""

import re

from gnucash_mcp.book._base import _slot_value_str  # noqa: F401  (re-exported for callers)

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

# Upper bound on slot value length. 64 KiB is generous for any
# legitimate per-account metadata (APR strings, credit limits,
# statement-close-day, structured JSON config blobs) — well past
# what real bookkeeping needs but short enough that a malicious
# or runaway caller can't exhaust the book file with a single
# slot write. Pre-fix slot values were unbounded; HP-9 from
# specs/CODE_REVIEW_v1_3.md.
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
                raise ValueError(f"Account not found: {account_name}")

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
        if not _SLOT_KEY_RE.fullmatch(key):
            raise ValueError(
                f"Invalid slot key {key!r}: must match [A-Za-z0-9_.-]+. "
                f"Embedded '/' would create hierarchical sub-slots; "
                f"use flat keys."
            )
        # HP-9 length cap. Encode to UTF-8 to count bytes (so a
        # multi-byte unicode payload can't sneak past a char-count
        # check). 64 KiB is generous for any real per-account
        # metadata.
        if len(value.encode("utf-8")) > _SLOT_VALUE_MAX_BYTES:
            raise ValueError(
                f"Slot value too long: "
                f"{len(value.encode('utf-8'))} bytes exceeds the "
                f"{_SLOT_VALUE_MAX_BYTES}-byte cap. Store large "
                f"structured data outside the book."
            )
        with self.open(readonly=False) as book:
            account = self._resolve_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            existing = False
            try:
                account[key]
                existing = True
            except KeyError:
                pass

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
        # Same regex gate as ``set_account_slot``. Pre-fix delete
        # skipped the validator, so a user could target internal
        # namespaced slots (``gnc-mcp/applies-to-invoice``, etc.)
        # that the credit-note linkage and other internal features
        # depend on. HP-11 from specs/CODE_REVIEW_v1_3.md.
        if not _SLOT_KEY_RE.fullmatch(key):
            raise ValueError(
                f"Invalid slot key {key!r}: must match [A-Za-z0-9_.-]+. "
                f"Embedded '/' would target hierarchical sub-slots "
                f"(internal namespaced state); use flat keys."
            )
        with self.open(readonly=False) as book:
            account = self._resolve_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            try:
                account[key]
            except KeyError:
                raise ValueError(f"Slot key not found: {key}")

            del account[key]
            book.save()

            return {"status": "deleted"}
