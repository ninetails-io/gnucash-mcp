"""ReconciliationMixin — split reconciliation state + void/unvoid.

Covers the bank reconciliation workflow (marking splits as cleared
or reconciled against a statement) and the proper accounting void
for transactions that must be preserved for audit.

Depends on shared helpers from BaseGnuCashBook:
  - self.open, self._find_account, self._find_split,
    self._find_transaction
  - _unreconciled_split_to_compact_line, _split_to_compact_dict (module-level)
"""

from datetime import date, datetime
from decimal import Decimal

from gnucash_mcp.book._base import (
    _guid_prefix_map,
    _split_to_compact_dict,
    _transaction_to_dict,
    _unreconciled_split_to_compact_line,
)


def _split_state_dict(split) -> dict:
    """Build the before-state dict for a single split.

    Shape matches what the audit-log formatter expects when entity_type
    is "split" — account path, current quantity, reconcile state/date,
    plus transaction context for human-readable log lines.
    """
    rec_date = split.reconcile_date
    return {
        "guid": split.guid,
        "account": split.account.fullname,
        "amount": str(split.quantity),
        "reconcile_state": split.reconcile_state,
        "reconcile_date": rec_date.isoformat() if rec_date else None,
        "transaction_description": split.transaction.description,
        "transaction_date": split.transaction.post_date.isoformat(),
    }


class ReconciliationMixin:
    """Split reconciliation and transaction void/unvoid."""

    # Valid reconcile states
    VALID_RECONCILE_STATES = {"n", "c", "y"}  # new, cleared, reconciled

    def set_reconcile_state(
        self,
        split_guid: str,
        state: str,
        reconcile_date: date | None = None,
    ) -> dict:
        """Set the reconciliation state for a split.

        Args:
            split_guid: GUID of the split to update.
            state: New reconcile state ('n'=new, 'c'=cleared, 'y'=reconciled).
            reconcile_date: Date of reconciliation. Required if state is 'y',
                           defaults to today if not provided.

        Returns:
            Dict with split details and status.

        Raises:
            ValueError: If split not found or invalid state.
        """
        state = state.lower()
        if state not in self.VALID_RECONCILE_STATES:
            raise ValueError(
                f"Invalid reconcile state: {state}. "
                f"Valid states: 'n' (new), 'c' (cleared), 'y' (reconciled)"
            )

        with self.open(readonly=False) as book:
            split = self._find_split(book, split_guid)
            if not split:
                raise ValueError(f"Split not found: {split_guid}")

            # Stage pre-update state for the audit log.
            self._stage_audit_before(_split_state_dict(split))

            split.reconcile_state = state

            if state == "y":
                if reconcile_date:
                    split.reconcile_date = datetime.combine(
                        reconcile_date, datetime.min.time()
                    )
                else:
                    split.reconcile_date = datetime.now()
            elif state == "n":
                split.reconcile_date = None

            book.save()

            # Response carries the resolved-from-prefix full split_guid
            # plus context the LLM only had a GUID for (account, amount).
            # The requested state is echo — dropped. reconcile_date
            # stays because it's computed (today if not provided).
            return {
                "split_guid": split.guid,
                "account": split.account.fullname,
                "amount": str(split.quantity),
                "reconcile_date": split.reconcile_date.isoformat() if split.reconcile_date and split.reconcile_date.year > 1970 else None,
                "status": "updated",
            }

    def get_unreconciled_splits(
        self,
        account_name: str,
        as_of_date: date | None = None,
        compact: bool = True,
    ) -> dict | str:
        """Get all unreconciled splits for an account.

        Args:
            account_name: Full account path.
            as_of_date: Only include splits on or before this date.
            compact: If True (default), return a compact newline-separated
                     string with one line per split plus a summary footer.
                     If False, return the full dict with splits list.

        Returns:
            If compact: newline-separated string of split lines with summary.
            If not compact: dict with account info, splits list, and totals.

        Raises:
            ValueError: If account not found.
        """
        with self.open(readonly=True) as book:
            account = self._find_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            unreconciled = []
            cleared_total = Decimal("0")
            uncleared_total = Decimal("0")

            splits = sorted(
                account.splits,
                key=lambda s: (s.transaction.post_date, s.transaction.enter_date)
            )

            for split in splits:
                if as_of_date and split.transaction.post_date > as_of_date:
                    continue

                # Only include non-reconciled splits (n or c, not y)
                if split.reconcile_state != "y":
                    split_dict = {
                        "guid": split.guid,
                        "date": split.transaction.post_date.isoformat(),
                        "description": split.transaction.description,
                        "amount": str(split.quantity),
                        "reconcile_state": split.reconcile_state,
                        "memo": split.memo or "",
                    }
                    unreconciled.append(split_dict)

                    if split.reconcile_state == "c":
                        cleared_total += split.quantity
                    else:
                        uncleared_total += split.quantity

            result = {
                "account": account_name,
                "as_of_date": as_of_date.isoformat() if as_of_date else None,
                "splits": unreconciled,
                "cleared_total": str(cleared_total),
                "uncleared_total": str(uncleared_total),
                "count": len(unreconciled),
            }

            if compact:
                # Prefix uniqueness spans every split in the book because
                # set_reconcile_state / reconcile_account resolve split
                # GUIDs table-wide — not scoped to the current account.
                all_split_guids = (
                    s.guid for txn in book.transactions for s in txn.splits
                )
                prefixes = _guid_prefix_map(all_split_guids)
                lines = [
                    _unreconciled_split_to_compact_line(s, prefixes=prefixes)
                    for s in unreconciled
                ]
                footer = f"{len(unreconciled)} splits\tcleared:{cleared_total}\tuncleared:{uncleared_total}"
                lines.append(footer)
                return "\n".join(lines)
            else:
                return result

    def reconcile_account(
        self,
        account_name: str,
        statement_date: date,
        statement_balance: str,
        split_guids: list[str],
    ) -> dict:
        """Reconcile multiple splits against a statement balance.

        Args:
            account_name: Full account path.
            statement_date: Statement ending date.
            statement_balance: Expected balance from statement (as string).
            split_guids: List of split GUIDs to mark as reconciled.

        Returns:
            Dict with reconciliation results.

        Raises:
            ValueError: If account not found, split not found, or balance mismatch.
        """
        expected_balance = Decimal(statement_balance)

        with self.open(readonly=False) as book:
            account = self._find_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            reconciled_balance = Decimal("0")
            for split in account.splits:
                if split.reconcile_state == "y":
                    reconciled_balance += split.quantity

            splits_to_reconcile = []
            reconciling_total = Decimal("0")

            for guid in split_guids:
                split = self._find_split(book, guid)
                if not split:
                    raise ValueError(f"Split not found: {guid}")
                if split.account.fullname != account_name:
                    raise ValueError(
                        f"Split {guid} belongs to account '{split.account.fullname}', "
                        f"not '{account_name}'"
                    )
                if split.reconcile_state == "y":
                    raise ValueError(f"Split {guid} is already reconciled")

                splits_to_reconcile.append(split)
                reconciling_total += split.quantity

            new_balance = reconciled_balance + reconciling_total
            if new_balance != expected_balance:
                raise ValueError(
                    f"Balance mismatch: reconciled balance would be {new_balance}, "
                    f"but statement balance is {expected_balance}. "
                    f"Difference: {expected_balance - new_balance}"
                )

            # Stage pre-reconcile state for the audit log. Shape mirrors
            # the multi-split payload the audit formatter expects for
            # RECONCILE operations: {"splits": [state_dict, ...]} so it
            # can display per-split context (description, amount) next
            # to each short GUID in the reconciled list.
            self._stage_audit_before(
                {"splits": [_split_state_dict(s) for s in splits_to_reconcile]}
            )

            reconcile_datetime = datetime.combine(statement_date, datetime.min.time())
            for split in splits_to_reconcile:
                split.reconcile_state = "y"
                split.reconcile_date = reconcile_datetime

            book.save()

            # Return only the computed info — the audit log reads inputs
            # (account_name, statement_date, statement_balance) from tool
            # params, so we don't echo them here.
            return {
                "splits_reconciled": len(splits_to_reconcile),
                "new_reconciled_balance": str(new_balance),
                "status": "reconciled",
            }

    def void_transaction(self, guid: str, reason: str) -> dict:
        """Void a transaction (proper accounting void, not delete).

        Voiding preserves the transaction for audit purposes but zeroes out
        all split values. Original values are stored in slots for potential
        unvoiding.

        Args:
            guid: Transaction GUID to void.
            reason: Reason for voiding (required for audit trail).

        Returns:
            Dict with transaction details and status.

        Raises:
            ValueError: If transaction not found or already voided.
        """
        if not reason or not reason.strip():
            raise ValueError("Void reason is required")

        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            if any(s.reconcile_state == "v" for s in transaction.splits):
                raise ValueError(f"Transaction {guid} is already voided")

            # Stage pre-void state — the VOID formatter renders "Was:
            # description (date)" plus the original splits from it.
            self._stage_audit_before(_transaction_to_dict(transaction))

            # GnuCash slot keys for void info
            transaction["void-reason"] = reason
            transaction["void-time"] = datetime.now().isoformat()

            for split in transaction.splits:
                split["void-former-value"] = str(split.value)
                split["void-former-quantity"] = str(split.quantity)

                split.value = Decimal("0")
                split.quantity = Decimal("0")

                split.reconcile_state = "v"

            book.save()

            return {
                "guid": transaction.guid,
                "description": transaction.description,
                "void_reason": reason,
                "status": "voided",
            }

    def unvoid_transaction(self, guid: str) -> dict:
        """Restore a voided transaction.

        Restores original split values from stored slots and removes void markers.

        Args:
            guid: Transaction GUID to unvoid.

        Returns:
            Dict with transaction details and status.

        Raises:
            ValueError: If transaction not found or not voided.
        """
        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            if not any(s.reconcile_state == "v" for s in transaction.splits):
                raise ValueError(f"Transaction {guid} is not voided")

            for split in transaction.splits:
                former_value = split.get("void-former-value")
                former_quantity = split.get("void-former-quantity")

                if former_value is not None:
                    split.value = Decimal(former_value)
                    del split["void-former-value"]

                if former_quantity is not None:
                    split.quantity = Decimal(former_quantity)
                    del split["void-former-quantity"]

                split.reconcile_state = "n"

            if "void-reason" in transaction:
                del transaction["void-reason"]
            if "void-time" in transaction:
                del transaction["void-time"]

            book.save()

            # Restored splits ARE new info (they were zeroed while
            # voided, values stashed in slots). Emit them compactly —
            # full _split_to_dict would carry guid/reconcile_state="n"/
            # reconcile_date=None/lot_guid=None per split, all noise.
            return {
                "guid": transaction.guid,
                "date": transaction.post_date.isoformat(),
                "description": transaction.description,
                "splits": [
                    _split_to_compact_dict(s) for s in transaction.splits
                ],
                "status": "unvoided",
            }
