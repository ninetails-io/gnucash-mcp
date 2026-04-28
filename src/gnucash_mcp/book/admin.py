"""AdminMixin — account slot CRUD.

Provides get/set/delete for piecash account slots (KVP metadata).
Slots are used to store per-account data like APR, credit limit,
statement close day, etc. Values are stored as strings.
"""


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
                    value = account[key]
                    slots = {key: str(value.value) if hasattr(value, 'value') else str(value)}
                except KeyError:
                    slots = {}
            else:
                slots = {}
                for k, v in account.iteritems():
                    slots[k] = str(v.value)

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
            key: Slot key (e.g., "apr", "credit_limit").
            value: Slot value. Stored as string.

        Returns:
            Dict with status ("created" or "updated"). Input parameters are
            not echoed — the audit log captures them from tool params.

        Raises:
            ValueError: If account not found.
        """
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
            ValueError: If account not found or key not found.
        """
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
