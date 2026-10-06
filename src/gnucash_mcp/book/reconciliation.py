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
    _account_unit,
    _commodity_quantum,
    _day_end,
    _future_statement_warning,
    _is_unreconciled,
    _is_voided,
    _money_precision_error,
    _set_split_amounts,
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
                    split.reconcile_date = _day_end(reconcile_date)
                else:
                    split.reconcile_date = datetime.now()
            elif state == "n":
                split.reconcile_date = None

            book.save()

            # Short split prefix + context the LLM only had a GUID
            # for. The requested state is an echo — dropped;
            # reconcile_date stays (computed when not provided).
            # The cached table-wide map is one indexed query; the
            # relationship walk it replaces lazy-loaded one splits
            # collection per transaction (whole-tree review, class 4).
            short_guid = self._split_prefix_map(book)[split.guid]
            return {
                "split_guid": short_guid,
                "account": split.account.fullname,
                "amount": str(split.quantity),
                "reconcile_date": split.reconcile_date.isoformat() if split.reconcile_date and split.reconcile_date.year > 1970 else None,
                "status": "updated",
            }

    def get_reconciliation_status(
        self, compact: bool = True, limit: int = 50, offset: int = 0,
    ) -> dict | str:
        """Per-account reconciliation table behind the dashboard's
        aggregate counts — the drill-down that answers "WHICH five
        accounts are never reconciled?"

        One row per reconcilable account with activity, bucketed by
        the same classification the dashboard uses (agree-by-
        construction): ``behind`` (most-behind first), ``never``,
        ``current``, ``dormant`` ($0 and idle: fully reconciled, or never
        reconciled with no activity in 180 days), and
        ``excluded`` (the account's ``no_reconcile`` slot — set via
        set_account_slot — opts it out of dashboard warnings;
        loans and escrow payables with no statement to reconcile).

        Args:
            compact: One TSV line per account (default) or the
                verbose envelope.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return.
        """
        with self.open(readonly=True) as book:
            accounts = list(book.accounts)
            rows = self._account_reconciliation_status(book, accounts)
            for r in rows:
                r["bucket"] = self._classify_reconciliation(r)
            bucket_rank = {
                "behind": 0, "never": 1, "current": 2,
                "dormant": 3, "excluded": 4,
            }
            rows.sort(key=lambda r: (
                bucket_rank[r["bucket"]],
                -(r["days_behind"] or 0),
                r["account"],
            ))

            page, indicator = _paginate(
                rows, offset=offset, limit=limit,
                entity_name="accounts",
            )
            if compact:
                lines = [indicator]
                for r in page:
                    cells = [r["account"], r["bucket"], r["status"]]
                    n = r["unreconciled_count"]
                    if n:
                        cell = f"{n} unreconciled"
                        if r.get("oldest_unreconciled_date"):
                            cell += (
                                f" (oldest: "
                                f"{r['oldest_unreconciled_date']})"
                            )
                        cells.append(cell)
                    # Same split the dashboard makes (spec B2):
                    # items older than the last reconcile are not
                    # backlog and never make the account behind.
                    m = r.get("outstanding_count")
                    if m:
                        cells.append(
                            f"{m} outstanding older than last reconcile "
                            f"(oldest: {r['outstanding_oldest_date']})"
                        )
                    lines.append("\t".join(cells))
                return "\n".join(lines)
            return {
                "showing": indicator,
                "total": len(rows),
                "offset": offset,
                "count": len(page),
                "accounts": page,
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
                raise self._account_not_found_error(book, account_name)

            all_unreconciled = []
            cleared_total = Decimal("0")
            uncleared_total = Decimal("0")

            # One indexed query for this account's transactions —
            # the sort key below reads split.transaction per split,
            # which lazy-loaded one SELECT each (whole-tree review,
            # class 4's second head). Strong reference held for the
            # walk; see the helper.
            _txn_keepalive = (  # noqa: F841 — keepalive
                self._preload_account_transactions(book, account)
            )
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
                # consuming tools resolve GUIDs table-wide. Cached,
                # one indexed query (whole-tree review, class 4).
                prefixes = self._split_prefix_map(book)
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
          unreconciled split on the account dated on or before
          ``through_date``, which DEFAULTS TO ``statement_date`` —
          a statement reconciliation is bounded by the statement.
          (The old no-filter default made multi-month entry
          unreconcilable in bulk: later months broke the balance
          tie, forcing a targeted GUID-picking dance per statement
          — live bookkeeper finding, 2026-07-24. Nothing is
          silently excluded either way: the tie check rejects with
          the discrepancy when the window and the statement
          disagree.) Pass a later ``through_date`` explicitly to
          widen the sweep. ``except_guids`` excludes named splits
          ("the statement covers everything except this pending
          ACH"); non-resolving prefixes are silently ignored.

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
        if reconcile_all and through_date is None:
            through_date = statement_date

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
                raise self._account_not_found_error(book, account_name)

            # Bulk mode and the audit before-state both read
            # split.transaction per split — warm the account's
            # transactions once (strong reference held for the call).
            _txn_keepalive = (  # noqa: F841 — keepalive
                self._preload_account_transactions(book, account)
            )

            reconciled_balance = Decimal("0")
            for split in account.splits:
                if split.reconcile_state == "y":
                    reconciled_balance += split.quantity

            splits_to_reconcile = []
            reconciling_total = Decimal("0")

            if reconcile_all:
                # Pre-resolve except_guids to full GUIDs for a fast
                # set lookup. One that names no split of THIS account
                # is refused: dropped silently (a transaction GUID
                # pasted for a split's), the item it was meant to
                # hold back was reconciled with everything else
                # (adversarial review 2026-09-30, IV-26).
                exempt_guids: set[str] = set()
                if except_guids:
                    unmatched = []
                    for prefix in except_guids:
                        try:
                            found = self._find_split(book, prefix)
                        except ValueError:
                            found = None
                        if found is None or found.account_guid != account.guid:
                            unmatched.append(prefix)
                        else:
                            exempt_guids.add(found.guid)
                    if unmatched:
                        raise ValueError(
                            f"except_guids names no split of "
                            f"{account.fullname}: "
                            f"{', '.join(unmatched)}. These must be "
                            f"SPLIT GUIDs from "
                            f"get_unreconciled_splits (a transaction "
                            f"GUID is not one). Nothing was "
                            f"reconciled."
                        )

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
            quantum = _commodity_quantum(account.commodity)
            # A statement balance finer than the currency's unit is a
            # typo; rounding it could tie a reconciliation that does
            # not tie (enter_statement already refuses — MM-11).
            if account.commodity.namespace == "CURRENCY":
                error = _money_precision_error(
                    expected_balance, account.commodity,
                    "statement_balance",
                )
                if error:
                    raise error
            expected_q = expected_balance.quantize(quantum)
            new_balance = (
                reconciled_balance + reconciling_total
            ).quantize(quantum)
            if new_balance != expected_q:
                hint = ""
                if reconcile_all:
                    hint = (
                        " Bulk mode swept splits through "
                        f"{through_date.isoformat()}; if the statement "
                        "includes later-dated items (e.g. a payoff "
                        "payment), pass through_date past them."
                    )
                raise ValueError(
                    f"Balance mismatch: reconciled balance would be {new_balance}, "
                    f"but statement balance is {expected_q}. "
                    f"Difference: {expected_q - new_balance}.{hint}"
                )

            # Audit before-state in the multi-split shape the
            # RECONCILE formatter expects: {"splits": [...]}.
            self._stage_audit_before(
                {"splits": [_split_state_dict(s) for s in splits_to_reconcile]}
            )

            # Desktop dates a reconciled split at the statement's local
            # day end (gnc_time64_get_day_end), not midnight.
            reconcile_datetime = _day_end(statement_date)
            for split in splits_to_reconcile:
                split.reconcile_state = "y"
                split.reconcile_date = reconcile_datetime

            # What desktop's reconcile window records on Finish:
            # the statement cycle the dashboard's threshold reads
            # (spec B4).
            self._write_reconcile_info(book, account, statement_date)

            book.save()

            # Computed info only — the audit log reads the statement
            # inputs from tool params and the reconciled-split list
            # from the staged before-state above.
            result = {
                "splits_reconciled": len(splits_to_reconcile),
                "new_reconciled_balance": str(new_balance),
                "status": "reconciled",
            }
            warning = _future_statement_warning(statement_date)
            if warning:
                result["warning"] = warning
            return result

    def void_transaction(
        self, guid: str, reason: str, force: bool = False,
    ) -> dict:
        """Void a transaction (proper accounting void, not delete).

        Preserves the transaction for audit, zeroes the split
        values, and stashes the originals in slots for unvoiding.

        Voiding reconciled splits breaks the affected accounts'
        reconciliation balance, so it is refused unless ``force``,
        the same gate as ``delete_transaction`` and
        ``replace_splits`` (desktop's register asks before it
        changes a reconciled split). A forced void carries a
        ``warning`` naming the affected accounts.

        Args:
            guid: Transaction GUID to void.
            reason: Required for the audit trail.
            force: Allow voiding a transaction with reconciled splits.

        Raises:
            ValueError: If transaction not found, already voided, or
                reconciled and not forced.
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

            # xaccTransVoid: "Refusing to void a read-only
            # transaction!" A voided posting zeroes the split the
            # document's lot is measured from.
            self._refuse_posting_record(book, transaction, "void")
            self._require_force_for_reconciled(
                transaction, force, "Voiding",
            )

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

            # Every pre-fix void in the book converts on this write
            # (nothing else changes) — see _migrate_void_shapes.
            shapes = self._upgrade_book_shapes(book)

            # xaccTransVoid, key for key (Transaction.cpp): the notes
            # move to void-former-notes and read "Voided transaction";
            # void-reason and void-time are strings, the time in
            # GnuCash's own ISO 8601 (format_iso8601: UTC,
            # "YYYY-MM-DD HH:MM:SS" — a space, no zone; the 'T' form
            # Python emits fails gnc-datetime's parser, and desktop
            # then does not see the transaction as voided at all);
            # the transaction becomes read-only. Each split keeps its
            # originals as NUMERIC void-former-amount / -value
            # (xaccSplitVoid), which is what desktop's Unvoid restores.
            self._write_void_slots(transaction, reason)

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
            result.update(shapes)
            closed = self._read_only_period_note(
                book, [transaction.post_date],
                "voiding this transaction",
            )
            if closed:
                result["read_only_period"] = closed
            if reconciled_accounts:
                result["warning"] = (
                    f"Voided transaction contained "
                    f"{len(reconciled_accounts)} reconciled "
                    f"account(s): {', '.join(reconciled_accounts)}. "
                    f"The reconciled balance for these accounts no "
                    f"longer matches the cleared statement."
                )
            return result

    @staticmethod
    def _gnc_void_time(now: datetime | None = None) -> str:
        """``gnc_time64_to_iso8601_buff``: UTC, ``YYYY-MM-DD HH:MM:SS``
        (GncDateTimeImpl::format_iso8601 — to_iso_extended_string of
        the UTC time with the 'T' replaced by a space, 19 chars)."""
        from datetime import timezone

        now = now or datetime.now(timezone.utc)
        return now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _write_void_slots(self, transaction, reason: str) -> None:
        """Void one transaction the way ``xaccTransVoid`` and
        ``xaccSplitVoid`` do, key for key and type for type. piecash
        types a bracket-assigned Decimal as a NUMERIC slot and a str
        as a STRING slot (kvp.py ``slot()``), which is what makes the
        shapes line up with GnuCash's."""
        # xaccTransVoid copies the notes whenever the slot holds a
        # string, an empty one included.
        if "notes" in transaction:
            transaction["void-former-notes"] = transaction.notes or ""
        transaction.notes = "Voided transaction"
        transaction["void-reason"] = reason
        transaction["void-time"] = self._gnc_void_time()
        # gnc_numeric carries the split's own denominators — the
        # account's unit for the amount, the currency's fraction for
        # the value — not the Decimal's exponent.
        value_q = _commodity_quantum(transaction.currency)
        for split in transaction.splits:
            split["void-former-amount"] = Decimal(str(split.quantity)).quantize(
                _account_unit(split.account)
            )
            split["void-former-value"] = Decimal(str(split.value)).quantize(value_q)
            _set_split_amounts(split, Decimal("0"), Decimal("0"))
            split.reconcile_state = "v"
        transaction["trans-read-only"] = "Transaction Voided"

    def _migrate_void_shapes(self, book) -> int:
        """Write path only: rewrite every void the server made before
        the fix into GnuCash's shape, and say how many.

        The server voided with ``void-former-value`` and an invented
        ``void-former-quantity``, both STRING slots, and a
        ``void-time`` in Python's ISO form. ``xaccSplitUnvoid`` reads
        NUMERIC ``void-former-value`` / ``void-former-amount`` and
        ``xaccTransGetVoidStatus`` parses ``void-time`` with
        gnc-datetime's regex, so desktop saw none of it: the
        transaction was not "voided" to desktop, and Unvoid, had it
        run, would have restored zeros. Each such transaction gets
        the numeric pair, the legacy key removed, the time
        reformatted, and the read-only marker; the amounts it holds
        are kept exactly. Nothing posts. Every void, unvoid,
        schedule, budget, and business write calls this through
        ``_upgrade_book_shapes``. Returns the number of transactions
        converted.
        """
        from piecash.core.transaction import Split
        from sqlalchemy import text

        split_guids = [
            r[0] for r in book.session.execute(
                text(
                    "SELECT DISTINCT obj_guid FROM slots "
                    "WHERE name = 'void-former-quantity'"
                ),
            ).fetchall()
        ]
        if not split_guids:
            return 0
        transactions = {}
        for sg in split_guids:
            split = book.session.query(Split).filter_by(guid=sg).first()
            if split is None:
                continue
            former_value = split.get("void-former-value")
            former_quantity = split.get("void-former-quantity")
            # Replace, never assign over: piecash keeps a slot's
            # class, and the old row is a SlotString.
            for key in ("void-former-value", "void-former-quantity"):
                if split.get(key) is not None:
                    del split[key]
            book.flush()
            if former_value is not None:
                split["void-former-value"] = Decimal(str(former_value)).quantize(
                    _commodity_quantum(split.transaction.currency)
                )
            if former_quantity is not None:
                split["void-former-amount"] = Decimal(str(former_quantity)).quantize(
                    _account_unit(split.account)
                )
            transactions[split.transaction.guid] = split.transaction
        for txn in transactions.values():
            vt = txn.get("void-time")
            if vt is not None and "T" in str(vt):
                try:
                    parsed = datetime.fromisoformat(str(vt))
                    txn["void-time"] = self._gnc_void_time(parsed)
                except ValueError:
                    pass
            if "trans-read-only" not in txn:
                txn["trans-read-only"] = "Transaction Voided"
        book.flush()
        return len(transactions)

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

            # Every pre-fix void converts first, so the read below
            # sees GnuCash's keys; the legacy pair is still accepted
            # in case a conversion is refused mid-way.
            shapes = self._upgrade_book_shapes(book)

            # Validate up-front that EVERY voided split has its
            # void-former slots — otherwise partial corruption
            # produces a partial unvoid (one split restored, its
            # sibling stuck at zero). Refuse and surface it.
            missing_slots = []
            for split in transaction.splits:
                if split.reconcile_state != "v":
                    continue
                has_value = split.get("void-former-value") is not None
                has_qty = (
                    split.get("void-former-amount") is not None
                    or split.get("void-former-quantity") is not None
                )
                if not (has_value and has_qty):
                    missing_slots.append(
                        f"{split.account.fullname} (value={has_value}, "
                        f"amount={has_qty})"
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

            # xaccTransUnvoid / xaccSplitUnvoid, key for key.
            for split in transaction.splits:
                former_value = split.get("void-former-value")
                former_amount = split.get("void-former-amount")
                if former_amount is None:
                    former_amount = split.get("void-former-quantity")

                if former_value is not None or former_amount is not None:
                    _set_split_amounts(
                        split,
                        Decimal(str(former_value)) if former_value is not None
                        else split.value,
                        Decimal(str(former_amount))
                        if former_amount is not None else split.quantity,
                    )
                for key in (
                    "void-former-value", "void-former-amount",
                    "void-former-quantity",
                ):
                    if split.get(key) is not None:
                        del split[key]

                split.reconcile_state = "n"

            # xaccTransUnvoid: the former notes come back when there
            # are any; otherwise the notes stay as the void left them
            # (GnuCash writes that text in the user's language, so it
            # cannot be recognized and is not guessed at).
            former_notes = transaction.get("void-former-notes")
            if former_notes is not None:
                transaction.notes = str(former_notes)
                del transaction["void-former-notes"]
            for key in ("void-reason", "void-time", "trans-read-only"):
                if key in transaction:
                    del transaction[key]
            # A posting record voided before the guard existed lost
            # its read-only reason to "Transaction Voided"; clearing
            # that (xaccTransClearReadOnly) must not leave a posting
            # transaction desktop will let the user edit. Put back
            # what posting wrote.
            if self._posting_document_id(book, transaction) is not None:
                transaction["trans-read-only"] = (
                    self._POSTING_READ_ONLY_REASON
                )

            book.save()

            # Restored splits ARE new info (zeroed while voided);
            # emitted compactly.
            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            closed = self._read_only_period_note(
                book, [transaction.post_date],
                "restoring this transaction",
            )
            return {
                "guid": short_guid,
                "date": transaction.post_date.isoformat(),
                "description": transaction.description,
                **shapes,
                "splits": [
                    _split_to_compact_dict(s) for s in transaction.splits
                ],
                "status": "unvoided",
                **({"read_only_period": closed} if closed else {}),
            }
