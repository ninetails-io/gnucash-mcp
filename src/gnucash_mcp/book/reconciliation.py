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
    _to_decimal,
    _transaction_to_dict,
    _unique_prefix,
    _unreconciled_split_to_compact_line,
)
from gnucash_mcp._format import _apply_limit


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
            reconcile_date: Date of reconciliation. Optional even
                for state ``'y'`` — defaults to today's date when
                not provided. Pass explicitly to record a
                reconciliation as of a specific statement date.

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

            # Response carries a collision-safe short prefix of the split
            # GUID plus context the LLM only had a GUID for (account,
            # amount). The requested state is echo — dropped. reconcile_date
            # stays because it's computed (today if not provided).
            all_split_guids = (
                s.guid for txn in book.transactions for s in txn.splits
            )
            short_guid = _unique_prefix(split.guid, all_split_guids)
            return {
                "split_guid": short_guid,
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
        limit: int | None = None,
    ) -> dict | str:
        """Get unreconciled splits for an account.

        Returns up to ``limit`` splits (default 50) ordered by post-date
        ascending. The cleared / uncleared totals reflect the **full**
        unreconciled set on the account, not just the truncated slice —
        so the summary footer still tells you how far behind the
        reconciliation is even when individual lines are clipped.

        **Currency unit:** ``cleared_total`` and ``uncleared_total``
        are in the **account's commodity** (sum of ``split.quantity``,
        not ``split.value``). For a USD account on a USD-default
        book they're indistinguishable; for a EUR-denominated A/R
        account on a USD book the totals are in EUR. Compare to
        the bank statement in the same currency the account holds.

        Args:
            account_name: Full account path.
            as_of_date: Only include splits on or before this date.
            compact: If True (default), return a compact newline-separated
                     string with one line per split plus a summary footer.
                     If False, return the full dict with splits list.
            limit: Maximum splits to return. Defaults to 50, capped at
                   250 server-side. The full count is always reflected
                   in the summary footer / ``count`` field.

        Returns:
            If compact: newline-separated string of split lines + footer
                + optional truncation notice.
            If not compact: dict with account info, splits list (possibly
                truncated), totals reflecting the full unreconciled set,
                ``count`` (truncated length), ``total`` (untruncated),
                and ``notice`` (truncation message or None).

        Raises:
            ValueError: If account not found.
        """
        with self.open(readonly=True) as book:
            account = self._resolve_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            all_unreconciled = []
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
                    all_unreconciled.append(split_dict)

                    if split.reconcile_state == "c":
                        cleared_total += split.quantity
                    else:
                        uncleared_total += split.quantity

            unreconciled, notice = _apply_limit(
                all_unreconciled,
                limit=limit,
                entity_name="splits",
                suggest_narrow=True,
            )
            total_count = len(all_unreconciled)

            result = {
                "account": account.fullname,
                "as_of_date": as_of_date.isoformat() if as_of_date else None,
                "splits": unreconciled,
                # Totals always reflect the full unreconciled set —
                # truncation hides line items, never the headline summary.
                "cleared_total": str(cleared_total),
                "uncleared_total": str(uncleared_total),
                "count": len(unreconciled),
                "total": total_count,
                "notice": notice,
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
                footer = (
                    f"{total_count} splits\tcleared:{cleared_total}\t"
                    f"uncleared:{uncleared_total}"
                )
                lines.append(footer)
                if notice:
                    lines.append(notice)
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
        expected_balance = _to_decimal(statement_balance)

        with self.open(readonly=False) as book:
            account = self._resolve_account(book, account_name)
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

            # Quantize both sides to the account commodity's
            # smallest fraction before comparing. Pre-fix a user
            # typing ``"1234.567"`` against a 2-decimal book
            # produced a perpetual 0.007 mismatch with no clear
            # error — every reconciliation attempt failed even
            # when the books agreed at the cent. Now the
            # statement balance and computed balance are both
            # normalized to the commodity's smallest unit (USD ->
            # 2 decimals, JPY -> 0, BHD -> 3) before equality.
            fraction = getattr(account.commodity, "fraction", 100)
            quantum = (
                Decimal(1) / Decimal(fraction) if fraction > 1 else Decimal(1)
            )
            expected_q = expected_balance.quantize(quantum)
            new_balance = (
                reconciled_balance + reconciling_total
            ).quantize(quantum)
            if new_balance != expected_q:
                raise ValueError(
                    f"Balance mismatch: reconciled balance would be {new_balance}, "
                    f"but statement balance is {expected_q}. "
                    f"Difference: {expected_q - new_balance}"
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

        When the transaction contains reconciled splits, voiding them
        breaks the reconciliation balance for the affected accounts —
        the bank statement that originally reconciled is no longer
        accurate. The void proceeds (audit trail trumps bookkeeping
        cleanliness), but the result includes a ``warning`` field
        listing the affected accounts so the caller can re-reconcile
        or investigate as needed. Unlike ``delete_transaction`` (which
        blocks on reconciled splits absent ``force=True``), voiding is
        an audit operation that should never be silently rejected —
        the warning is informational, not gating.

        Args:
            guid: Transaction GUID to void.
            reason: Reason for voiding (required for audit trail).

        Returns:
            Dict with transaction details and status. When reconciled
            splits were affected, also includes ``warning`` describing
            the reconciliation impact.

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

            # Detect reconciled splits BEFORE zeroing — capture the
            # account names we'll cite in the warning.
            reconciled_accounts = sorted({
                s.account.fullname
                for s in transaction.splits
                if s.reconcile_state == "y"
            })

            # Stage pre-void state — the VOID formatter renders "Was:
            # description (date)" plus the original splits from it.
            self._stage_audit_before(_transaction_to_dict(transaction))

            # GnuCash slot keys for void info. Use a tz-aware
            # local time (mirroring the audit-log convention) so a
            # later reader can reconstruct "when was this voided"
            # unambiguously across DST transitions and timezone
            # changes. Pre-fix this stored a naive ``datetime.now()``
            # whose interpretation depended on the host's current
            # zone — same string would mean different absolute times
            # before/after a DST shift.
            transaction["void-reason"] = reason
            transaction["void-time"] = (
                datetime.now().astimezone().isoformat()
            )

            for split in transaction.splits:
                split["void-former-value"] = str(split.value)
                split["void-former-quantity"] = str(split.quantity)

                split.value = Decimal("0")
                split.quantity = Decimal("0")

                split.reconcile_state = "v"

            book.save()

            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            result = {
                "guid": short_guid,
                "description": transaction.description,
                "void_reason": reason,
                "status": "voided",
            }
            if reconciled_accounts:
                result["warning"] = (
                    f"Voided transaction contained "
                    f"{len(reconciled_accounts)} reconciled "
                    f"account(s): {', '.join(reconciled_accounts)}. "
                    f"The reconciled balance for these accounts no "
                    f"longer matches the cleared statement."
                )
            return result

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

            # Validate up-front that EVERY voided split has its
            # void-former slots present. Pre-fix partial corruption
            # (e.g., a split missing its void-former-value but with
            # void-former-quantity present) silently produced a
            # partial unvoid — the value-missing split would stay
            # at zero while its sibling was restored. Better to
            # refuse and surface the corruption explicitly.
            missing_slots = []
            for split in transaction.splits:
                if split.reconcile_state != "v":
                    continue
                has_value = split.get("void-former-value") is not None
                has_qty = split.get("void-former-quantity") is not None
                if not (has_value and has_qty):
                    missing_slots.append(
                        f"{split.account.fullname} (value={has_value}, "
                        f"quantity={has_qty})"
                    )
            if missing_slots:
                raise ValueError(
                    f"Cannot unvoid transaction {guid}: voided splits "
                    f"are missing their void-former slots, indicating "
                    f"partial corruption. Affected splits: "
                    f"{'; '.join(missing_slots)}. Restore the slots "
                    f"manually (or void/recreate the transaction) "
                    f"before retrying."
                )

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
            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            return {
                "guid": short_guid,
                "date": transaction.post_date.isoformat(),
                "description": transaction.description,
                "splits": [
                    _split_to_compact_dict(s) for s in transaction.splits
                ],
                "status": "unvoided",
            }
