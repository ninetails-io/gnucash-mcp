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
    _is_unreconciled,
    _is_voided,
    _split_to_compact_dict,
    _to_decimal,
    _transaction_to_dict,
    _unique_prefix,
    _unreconciled_split_to_compact_line,
)
from gnucash_mcp._format import _paginate


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

            # Reject state changes on voided splits — moving one to
            # 'y' erases the void marker and defeats
            # unvoid_transaction's recovery path. Unvoid first.
            if _is_voided(split):
                raise ValueError(
                    f"Cannot change reconcile state of voided split "
                    f"{split_guid}. Unvoid the transaction first "
                    f"(unvoid_transaction), then reconcile."
                )

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

            # Short split prefix + context the LLM only had a GUID
            # for. The requested state is an echo — dropped;
            # reconcile_date stays (computed when not provided).
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
        offset: int = 0,
    ) -> dict | str:
        """Get unreconciled splits for an account.

        Leads with a ``Showing X-Y of Z splits`` indicator, post-date
        ascending; page with ``offset``. The cleared/uncleared totals
        always reflect the **full** unreconciled set, not the page —
        truncation hides line items, never the headline summary.

        **Currency unit:** the totals are in the **account's
        commodity** (sum of ``split.quantity``, not ``split.value``)
        — compare to the bank statement in the currency the account
        holds.

        Args:
            account_name: Account ref.
            as_of_date: Only include splits on or before this date.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.
            compact: One line per split + summary footer (default),
                or the dict envelope {account, splits, totals,
                count, total, showing}.

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

                # ``_is_unreconciled`` is the chokepoint shared with
                # the dashboard count, so the two surfaces agree by
                # construction.
                if not _is_unreconciled(split):
                    continue
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

            unreconciled, indicator = _paginate(
                all_unreconciled,
                offset=offset,
                limit=limit,
                entity_name="splits",
                date_key=lambda s: s["date"],
            )
            total_count = len(all_unreconciled)

            result = {
                "account": account.fullname,
                "as_of_date": as_of_date.isoformat() if as_of_date else None,
                "showing": indicator,
                "splits": unreconciled,
                # Totals always reflect the full unreconciled set —
                # truncation hides line items, never the headline summary.
                "cleared_total": str(cleared_total),
                "uncleared_total": str(uncleared_total),
                "offset": offset,
                "count": len(unreconciled),
                "total": total_count,
            }

            if compact:
                # Prefixes span every split in the book — the
                # consuming tools resolve GUIDs table-wide.
                all_split_guids = (
                    s.guid for txn in book.transactions for s in txn.splits
                )
                prefixes = _guid_prefix_map(all_split_guids)
                lines = [indicator]
                lines += [
                    _unreconciled_split_to_compact_line(s, prefixes=prefixes)
                    for s in unreconciled
                ]
                footer = (
                    f"{total_count} splits\tcleared:{cleared_total}\t"
                    f"uncleared:{uncleared_total}"
                )
                lines.append(footer)
                return "\n".join(lines)
            else:
                return result

    def reconcile_account(
        self,
        account_name: str,
        statement_date: date,
        statement_balance: str,
        split_guids: list[str] | None = None,
        *,
        reconcile_all: bool = False,
        through_date: date | None = None,
        except_guids: list[str] | None = None,
    ) -> dict:
        """Reconcile multiple splits against a statement balance.

        Two operating modes:

        - **Targeted** (``split_guids=[...]``): reconcile exactly
          the listed splits.
        - **Bulk** (``reconcile_all=True``): reconcile every
          unreconciled split on the account — the OFX-import common
          case. ``through_date`` optionally bounds the sweep; the
          default is NO date filter (defaulting to statement_date
          would silently exclude payments dated after the
          statement). ``except_guids`` excludes named splits ("the
          statement covers everything except this pending ACH");
          non-resolving prefixes are silently ignored.

        The two modes are mutually exclusive, and both verify the
        resulting reconciled balance ties to ``statement_balance``
        BEFORE mutating; mismatch raises with the discrepancy.

        Args:
            account_name: Account ref (path, ``%short``, or GUID).
            statement_date: Statement ending date.
            statement_balance: Expected balance, as string.

        Returns:
            ``{splits_reconciled, new_reconciled_balance, status}``.

        Raises:
            ValueError: account not found, mode ambiguous, split
                missing / on the wrong account / voided, or balance
                mismatch.
        """
        expected_balance = _to_decimal(statement_balance)

        if reconcile_all and split_guids:
            raise ValueError(
                "Cannot combine reconcile_all=True with split_guids. "
                "Use either bulk mode (reconcile_all=True) or targeted "
                "mode (split_guids=[...]), not both."
            )
        if not reconcile_all and not split_guids:
            raise ValueError(
                "Must provide split_guids for targeted reconciliation, "
                "or set reconcile_all=True for bulk mode."
            )
        if except_guids and not reconcile_all:
            raise ValueError(
                "except_guids is only valid with reconcile_all=True. "
                "For targeted reconciliation, just include the splits "
                "you want in split_guids."
            )

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

            if reconcile_all:
                # Pre-resolve except_guids to full GUIDs for a fast
                # set lookup; non-resolving prefixes drop silently.
                exempt_guids: set[str] = set()
                if except_guids:
                    for prefix in except_guids:
                        found = self._find_split(book, prefix)
                        if found is not None:
                            exempt_guids.add(found.guid)

                for split in account.splits:
                    if split.reconcile_state == "y":
                        continue
                    # Voided splits were never reconcilable; the
                    # sweep skips them silently (the targeted mode
                    # below refuses loudly instead).
                    if _is_voided(split):
                        continue
                    if split.guid in exempt_guids:
                        continue
                    if (
                        through_date is not None
                        and split.transaction.post_date > through_date
                    ):
                        continue
                    splits_to_reconcile.append(split)
                    reconciling_total += split.quantity
            else:
                for guid in split_guids:
                    split = self._find_split(book, guid)
                    if not split:
                        raise ValueError(f"Split not found: {guid}")
                    # Compare by GUID against the resolved account —
                    # comparing the raw input string would reject
                    # %short/GUID forms that resolve correctly.
                    if split.account.guid != account.guid:
                        raise ValueError(
                            f"Split {guid} belongs to account "
                            f"'{split.account.fullname}', not "
                            f"'{account.fullname}'"
                        )
                    if split.reconcile_state == "y":
                        raise ValueError(f"Split {guid} is already reconciled")
                    # Refuse loudly on a named voided split — same
                    # contract as set_reconcile_state.
                    if _is_voided(split):
                        raise ValueError(
                            f"Split {guid} is voided. Voided splits "
                            f"cannot be reconciled; use "
                            f"unvoid_transaction first."
                        )

                    splits_to_reconcile.append(split)
                    reconciling_total += split.quantity

            # Quantize both sides to the account commodity's
            # smallest fraction before comparing — otherwise
            # "1234.567" against a 2-decimal book is a perpetual
            # 0.007 mismatch even when the books agree at the cent.
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

            # Audit before-state in the multi-split shape the
            # RECONCILE formatter expects: {"splits": [...]}.
            self._stage_audit_before(
                {"splits": [_split_state_dict(s) for s in splits_to_reconcile]}
            )

            reconcile_datetime = datetime.combine(statement_date, datetime.min.time())
            for split in splits_to_reconcile:
                split.reconcile_state = "y"
                split.reconcile_date = reconcile_datetime

            book.save()

            # Computed info only — the audit log reads the inputs
            # from tool params.
            return {
                "splits_reconciled": len(splits_to_reconcile),
                "new_reconciled_balance": str(new_balance),
                "status": "reconciled",
            }

    def void_transaction(self, guid: str, reason: str) -> dict:
        """Void a transaction (proper accounting void, not delete).

        Preserves the transaction for audit, zeroes the split
        values, and stashes the originals in slots for unvoiding.

        Voiding reconciled splits breaks the affected accounts'
        reconciliation balance. The void proceeds anyway — it's an
        audit operation that must never be silently rejected (unlike
        ``delete_transaction``, which gates on force) — and the
        result carries a ``warning`` naming the affected accounts.

        Args:
            guid: Transaction GUID to void.
            reason: Required for the audit trail.

        Raises:
            ValueError: If transaction not found or already voided.
        """
        if not reason or not reason.strip():
            raise ValueError("Void reason is required")
        # 4 KiB byte-cap (not chars — unicode payloads can't sneak
        # past): room for any real explanation, no runaway bloat.
        _VOID_REASON_MAX_BYTES = 4 * 1024
        reason_bytes = len(reason.encode("utf-8"))
        if reason_bytes > _VOID_REASON_MAX_BYTES:
            raise ValueError(
                f"Void reason too long: "
                f"{reason_bytes} bytes exceeds the "
                f"{_VOID_REASON_MAX_BYTES}-byte cap. Summarize the "
                f"reason; keep detailed context outside the book."
            )

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

            # tz-aware local time (audit-log convention) — a naive
            # datetime.now() means different absolute times across
            # DST shifts and zone changes.
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
            # void-former slots — otherwise partial corruption
            # produces a partial unvoid (one split restored, its
            # sibling stuck at zero). Refuse and surface it.
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

            # Restored splits ARE new info (zeroed while voided);
            # emitted compactly.
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
