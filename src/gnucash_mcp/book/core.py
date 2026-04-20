"""CoreMixin — the foundation layer: accounts, transactions, book summary.

Every other module (reconciliation, reporting, budgets, scheduling,
investments, business, admin) presupposes the methods here. `--modules`
always adds 'core' to the enabled set.

Holds:
  - get_book_summary (one-shot orientation: accounts, balances, net
    worth, txn count, commodities, scheduled)
  - Account CRUD + list/get/balance
  - Transaction CRUD + list/get/search/replace_splits
  - The duplicate-detection / auto-fill / split-consistency pipeline
    behind create_transaction

Depends on shared helpers from BaseGnuCashBook (via MRO):
  - self.open, self.book_path
  - self._find_account, self._find_transaction, self._find_split
  - self._find_commodity, self._require_default_currency,
    self._get_or_create_currency, self._resolve_guid,
    self._collect_descendants

SchedulingMixin.create_transaction_from_scheduled calls
self.create_transaction (defined here), resolved via MRO — that is
the one extracted-to-core dependency in the whole tree.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import piecash

from gnucash_mcp.book._base import (
    _account_to_compact_line,
    _account_to_dict,
    _guid_prefix_map,
    _split_to_compact_dict,
    _split_to_dict,
    _transaction_to_compact_line,
    _transaction_to_dict,
    _unique_prefix,
)


@dataclass
class _CreateSignals:
    """Signals gathered in a single pass over ``book.transactions`` for
    the ``create_transaction`` preflight and post-write checks.

    Before this was consolidated, ``create_transaction`` made four
    separate helper calls — each opening the book, scanning the full
    transaction list, and producing one signal. That's ``~4 × (open +
    O(N))`` per create. On a 10k-txn book the four opens alone cost
    80–150 ms, plus ~40k iterations for the scans.

    Collecting everything in one pass turns the hot path into a single
    book-open and a single sort + O(N) traversal. Signals are opt-in
    via ``want_*`` flags so the collector does only the work each call
    actually needs (e.g., skip duplicate detection when
    ``check_duplicates=False``).

    Attributes:
        auto_fill: ``(splits_list, source_info)`` for the most recent
            description match, or ``None`` when ``want_auto_fill`` was
            False or no match was found. ``source_info`` carries the
            source transaction's short guid prefix, description, and
            date — enough for the LLM to follow up via
            ``get_transaction``.
        stability_warnings: Zero or one warning dicts. Populated only
            when recent matching-description transactions disagree on
            the categorization account pattern — meaning auto-fill is
            drawing from inconsistent history.
        duplicates: HIGH- and MEDIUM-confidence duplicate candidates,
            sorted HIGH first. LOW-confidence (single-signal) matches
            are suppressed as noise.
        recent_matches: Up to five most-recent matching-description
            piecash.Transaction objects for the post-write split-
            consistency warning. Live ORM instances — caller must read
            them while the session is open.
    """

    auto_fill: tuple[list[dict], dict] | None = None
    stability_warnings: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    recent_matches: list = field(default_factory=list)

    @property
    def has_high_duplicate(self) -> bool:
        """True iff at least one candidate has confidence ``HIGH``."""
        return any(d["confidence"] == "HIGH" for d in self.duplicates)


class CoreMixin:
    """Accounts, transactions, and the book-summary view. Always loaded."""

    def get_book_summary(self) -> str:
        """Return a compact text summary of the entire book.

        Provides instant orientation: account structure, transaction volume,
        key balances, commodities, and scheduled transactions — all in one call.

        Returns:
            Pre-formatted text summary string.
        """
        from piecash.core.transaction import ScheduledTransaction

        with self.open(readonly=True) as book:
            currency = self._require_default_currency(book).mnemonic

            # --- Identify template accounts (scheduled transaction placeholders) ---
            template_guids = set()
            rt = book.root_template
            if rt:
                template_guids.add(rt.guid)
                for child in rt.children:
                    template_guids.add(child.guid)

            # --- Collect parent GUIDs (placeholder containers) ---
            parent_guids = set()
            for account in book.accounts:
                if account.parent and account.parent.type != "ROOT":
                    parent_guids.add(account.parent.guid)

            # --- Account stats ---
            asset_types = {"ASSET", "BANK", "CASH", "STOCK", "MUTUAL"}

            # Assets: (leaf_name, balance) for non-placeholder leaf accounts
            asset_leaves: list[tuple[str, Decimal]] = []
            # Liabilities: (leaf_name, positive_balance) grouped by category
            credit_cards: list[tuple[str, Decimal]] = []
            loan_accts: list[tuple[str, Decimal]] = []
            other_liab_accts: list[tuple[str, Decimal]] = []

            income_active = 0
            income_total = 0
            expense_active = 0
            expense_total = 0
            total_accounts = 0

            for account in book.accounts:
                if account.type == "ROOT":
                    continue
                if account.guid in template_guids:
                    continue
                total_accounts += 1

                has_activity = len(account.splits) > 0
                is_leaf = account.guid not in parent_guids

                # Calculate balance
                balance = Decimal("0")
                for split in account.splits:
                    balance += split.quantity

                leaf = account.fullname.split(":")[-1]

                if account.type in asset_types:
                    if is_leaf and balance != 0:
                        asset_leaves.append((leaf, balance))
                elif account.type == "CREDIT":
                    if is_leaf:
                        credit_cards.append((leaf, -balance))
                elif account.type == "LIABILITY":
                    if is_leaf:
                        neg_balance = -balance
                        if "loan" in account.fullname.lower():
                            loan_accts.append((leaf, neg_balance))
                        else:
                            other_liab_accts.append((leaf, neg_balance))
                elif account.type == "INCOME":
                    income_total += 1
                    if has_activity:
                        income_active += 1
                elif account.type == "EXPENSE":
                    expense_total += 1
                    if has_activity:
                        expense_active += 1

            # Compute totals from leaf accounts
            def _r2(v: Decimal) -> Decimal:
                return v.quantize(Decimal("0.01"))

            assets_total = _r2(sum(b for _, b in asset_leaves) if asset_leaves else Decimal(0))
            credit_total = _r2(sum(b for _, b in credit_cards) if credit_cards else Decimal(0))
            loan_total = _r2(sum(b for _, b in loan_accts) if loan_accts else Decimal(0))
            other_liab_total = _r2(sum(b for _, b in other_liab_accts) if other_liab_accts else Decimal(0))
            liabilities_total = _r2(credit_total + loan_total + other_liab_total)
            net_worth = _r2(assets_total - liabilities_total)

            # All liability leaves sorted by balance descending for top-N
            all_liab_leaves = credit_cards + loan_accts + other_liab_accts
            all_liab_leaves.sort(key=lambda x: x[1], reverse=True)

            # --- Transaction stats ---
            transactions = list(book.transactions)
            total_txns = len(transactions)
            unreconciled_txns = 0
            first_date = None
            last_date = None

            for txn in transactions:
                d = txn.post_date
                if first_date is None or d < first_date:
                    first_date = d
                if last_date is None or d > last_date:
                    last_date = d
                if any(s.reconcile_state != "y" for s in txn.splits):
                    unreconciled_txns += 1

            # --- Scheduled transactions ---
            all_sx = book.session.query(ScheduledTransaction).all()
            enabled_sx = sum(1 for sx in all_sx if sx.enabled)

            # --- Commodities ---
            commodity_mnemonics = sorted(set(
                c.mnemonic for c in book.commodities
            ))

            # --- Build output ---
            lines = []
            lines.append(f"Book: {self.book_path}")
            lines.append(f"Currency: {currency}")

            if first_date and last_date:
                lines.append(f"Data range: {first_date.isoformat()} to {last_date.isoformat()}")

            lines.append(f"Accounts: {total_accounts} total")

            # Assets section — leaf accounts with balances
            lines.append(f"Assets: {len(asset_leaves)} accounts, {currency} {assets_total}")
            for name, bal in sorted(asset_leaves, key=lambda x: x[1], reverse=True):
                lines.append(f"  {name}: {currency} {_r2(bal)}")

            # Liabilities section — grouped subtotals + top 3
            liab_count = len(credit_cards) + len(loan_accts) + len(other_liab_accts)
            lines.append(f"Liabilities: {liab_count} accounts, {currency} {liabilities_total}")
            if credit_cards:
                lines.append(f"  Credit cards ({len(credit_cards)}): {currency} {credit_total}")
            if loan_accts:
                lines.append(f"  Loans ({len(loan_accts)}): {currency} {loan_total}")
            if other_liab_accts:
                lines.append(f"  Other ({len(other_liab_accts)}): {currency} {other_liab_total}")
            if len(all_liab_leaves) > 1:
                top_n = all_liab_leaves[:3]
                top_parts = [f"{n} {currency} {_r2(b)}" for n, b in top_n]
                lines.append(f"  Top {len(top_n)}: {', '.join(top_parts)}")

            lines.append(f"Income: {income_active} active ({income_total} total)")
            lines.append(f"Expenses: {expense_active} active ({expense_total} total)")

            lines.append(f"Transactions: {total_txns} ({unreconciled_txns} unreconciled)")

            if enabled_sx > 0:
                lines.append(f"Scheduled: {enabled_sx} recurring")

            lines.append(f"Commodities: {', '.join(commodity_mnemonics)}")
            lines.append(f"Net worth: {currency} {net_worth}")

            return "\n".join(lines)

    def list_accounts(
        self,
        root: str | None = None,
        compact: bool = True,
    ) -> list[dict] | str:
        """List all accounts in the chart of accounts.

        Args:
            root: Optional root account path to filter to a subtree.
                  E.g., "Expenses" returns only Expenses and descendants.
            compact: If True (default), return a compact newline-separated
                     string with one line per account. If False, return
                     the full list of account dicts.

        Returns:
            If compact: newline-separated string of account lines.
            If not compact: flat list of account dicts with full paths.
        """
        with self.open(readonly=True) as book:
            filtered = []
            for account in book.accounts:
                if account.type == "ROOT":
                    continue
                if root is not None:
                    fn = account.fullname
                    if fn != root and not fn.startswith(root + ":"):
                        continue
                filtered.append(account)

            filtered.sort(key=lambda a: a.fullname)

            if compact:
                lines = [_account_to_compact_line(a) for a in filtered]
                return "\n".join(lines)
            else:
                return [_account_to_dict(a) for a in filtered]

    def get_account(self, name: str) -> dict | None:
        """Get details for a specific account by full name.

        Args:
            name: Full account path (e.g., 'Assets:Bank:Checking').

        Returns:
            Account dict if found, None otherwise.
        """
        with self.open(readonly=True) as book:
            account = self._find_account(book, name)
            if account:
                return _account_to_dict(account)
            return None

    def get_balance(self, account_name: str, as_of_date: date | None = None) -> Decimal:
        """Get balance for an account, optionally as of a specific date.

        Returns raw GnuCash balance (accounting sign convention).

        Args:
            account_name: Full account path.
            as_of_date: Date to calculate balance as of. Defaults to all time.

        Returns:
            Account balance as Decimal.

        Raises:
            ValueError: If account not found.
        """
        with self.open(readonly=True) as book:
            account = self._find_account(book, account_name)
            if not account:
                raise ValueError(f"Account not found: {account_name}")

            balance = Decimal("0")
            for split in account.splits:
                if as_of_date is None or split.transaction.post_date <= as_of_date:
                    balance += split.quantity

            return balance

    def list_transactions(
        self,
        account: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
        compact: bool = True,
    ) -> list[dict] | str:
        """List transactions with optional filters.

        Args:
            account: Filter by account full name.
            start_date: Filter transactions on or after this date.
            end_date: Filter transactions on or before this date.
            limit: Maximum number of transactions to return.
            compact: If True (default), return a compact newline-separated
                     string with one line per transaction. If False, return
                     the full list of transaction dicts.

        Returns:
            If compact: newline-separated string of transaction lines.
            If not compact: list of transaction dicts, most recent first.

        Raises:
            ValueError: If specified account not found.
        """
        with self.open(readonly=True) as book:
            # If filtering by account, get transactions through that account's splits
            if account:
                acct = self._find_account(book, account)
                if not acct:
                    raise ValueError(f"Account not found: {account}")
                transactions = {split.transaction for split in acct.splits}
            else:
                transactions = set(book.transactions)

            # Apply date filters
            filtered = []
            for trans in transactions:
                if start_date and trans.post_date < start_date:
                    continue
                if end_date and trans.post_date > end_date:
                    continue
                filtered.append(trans)

            # Sort by date descending
            filtered.sort(key=lambda t: t.post_date, reverse=True)

            # Apply limit
            filtered = filtered[:limit]

            if compact:
                # Build collision-safe prefix map across ALL transactions in
                # the book (not just the filtered batch) so emitted prefixes
                # remain valid _resolve_guid lookup keys against the full table.
                prefixes = _guid_prefix_map(t.guid for t in book.transactions)
                lines = [
                    _transaction_to_compact_line(
                        t, exclude_account=account, prefixes=prefixes
                    )
                    for t in filtered
                ]
                return "\n".join(lines)
            else:
                return [_transaction_to_dict(t) for t in filtered]

    def get_transaction(self, guid: str) -> dict | None:
        """Get details for a specific transaction by GUID.

        Args:
            guid: Transaction GUID (32-character hex string).

        Returns:
            Transaction dict if found, None otherwise.
        """
        with self.open(readonly=True) as book:
            transaction = self._find_transaction(book, guid)
            if transaction:
                return _transaction_to_dict(transaction)
            return None

    _FUNDING_ACCOUNT_TYPES = {
        "BANK", "CASH", "ASSET", "CREDIT", "LIABILITY", "EQUITY",
    }

    @staticmethod
    def _extract_account_pattern(accounts) -> frozenset[str]:
        """Extract categorization (non-funding) account names.

        Filters out funding account types (BANK, CASH, ASSET, CREDIT,
        LIABILITY, EQUITY) to isolate expense/income categorization.
        Falls back to all accounts if filtering leaves nothing
        (e.g., bank-to-bank transfers).

        Args:
            accounts: Iterable of piecash Account objects.

        Returns:
            frozenset of account fullnames representing the pattern.
        """
        all_names = frozenset(a.fullname for a in accounts)
        categorization = frozenset(
            a.fullname for a in accounts
            if a.type not in CoreMixin._FUNDING_ACCOUNT_TYPES
        )
        return categorization if categorization else all_names

    def _find_recent_description_matches(
        self,
        book,
        description: str,
        limit: int = 5,
        days: int = 90,
    ) -> list:
        """Find recent transactions with matching descriptions.

        Uses bidirectional case-insensitive substring matching
        (same logic as _auto_fill_splits and _find_duplicates).

        Args:
            book: Open piecash book (readonly).
            description: Description to match.
            limit: Maximum matches to return.
            days: How far back to search.

        Returns:
            List of piecash Transaction objects, most recent first.
        """
        desc_lower = description.lower()
        cutoff = date.today() - timedelta(days=days)
        matches = []

        sorted_txns = sorted(
            book.transactions, key=lambda t: t.post_date, reverse=True
        )
        for txn in sorted_txns:
            if txn.post_date < cutoff:
                break
            txn_desc_lower = txn.description.lower()
            if desc_lower in txn_desc_lower or txn_desc_lower in desc_lower:
                matches.append(txn)
                if len(matches) >= limit:
                    break

        return matches

    def _collect_create_signals(
        self,
        book: piecash.Book,
        description: str,
        trans_date: date,
        proposed_amounts: list[Decimal],
        *,
        want_auto_fill: bool,
        want_stability: bool,
        want_duplicates: bool,
        want_recent: bool,
        duplicate_window_days: int = 30,
        stability_days: int = 90,
        stability_limit: int = 5,
        recent_days: int = 30,
        recent_limit: int = 5,
    ) -> "_CreateSignals":
        """Gather every signal ``create_transaction`` might need in a
        single pass over ``book.transactions``.

        The four original helpers (``_auto_fill_splits``,
        ``_check_auto_fill_stability``, ``_find_duplicates``, and the
        post-write ``_find_recent_description_matches``) each opened the
        book and did its own full-table scan. This collector folds all
        of them into one sort + one traversal, classifying each
        transaction into whichever signal bucket(s) it matches.

        Callers supply ``want_*`` flags so the collector only does work
        the caller will actually consume — e.g., auto-fill has no
        meaning when the caller already provided splits, and duplicate
        detection is skipped when ``check_duplicates`` is False.

        Must be called before the new transaction is committed to the
        book; otherwise the just-created transaction shows up as a
        false-positive recent-match / duplicate of itself.

        Args:
            book: An open piecash book session.
            description: The proposed transaction description.
            trans_date: The proposed transaction date (for the
                duplicate-window filter).
            proposed_amounts: Absolute values of the proposed splits
                (for the duplicate amount-signal check). Unused when
                ``want_duplicates`` is False; pass ``[]`` in that case.
            want_auto_fill: Find the most recent matching-description
                transaction and extract its splits for auto-fill.
            want_stability: Compare account patterns across the recent
                matching history; warn when they disagree (auto-fill
                would draw from inconsistent data).
            want_duplicates: Score transactions in the ±``window``
                range on description, amount, and date; emit HIGH /
                MEDIUM candidates.
            want_recent: Keep the top N matching-description
                transactions for the post-write split-consistency
                warning.
            duplicate_window_days: ±N day window for duplicate search.
            stability_days: Lookback horizon for stability signal.
            stability_limit: Cap on how many recent matches the
                stability check inspects.
            recent_days: Lookback horizon for post-write recent-match.
            recent_limit: Cap on post-write recent matches.

        Returns:
            A ``_CreateSignals`` bundle. Untracked signals remain at
            their default (None / empty list).
        """
        today = date.today()
        stability_cutoff = today - timedelta(days=stability_days)
        recent_cutoff = today - timedelta(days=recent_days)
        dup_start = trans_date - timedelta(days=duplicate_window_days)
        dup_end = trans_date + timedelta(days=duplicate_window_days)
        desc_lower = description.lower()

        # Short-guid prefix map built once, shared across all emitted
        # guids (auto-fill source, duplicates). Caller never sees the
        # raw 32-char guid — prefixes flow straight into tool responses.
        emitting_guids = want_auto_fill or want_duplicates
        txn_prefixes = (
            _guid_prefix_map(t.guid for t in book.transactions)
            if emitting_guids
            else {}
        )

        # Local accumulators — each bucket is independent. We finalize
        # them into the returned _CreateSignals after the loop.
        auto_fill_source = None  # piecash.Transaction
        stability_matches: list = []  # list[piecash.Transaction]
        recent_matches: list = []  # list[piecash.Transaction]
        duplicates: list[dict] = []

        # One sort, one iteration. Descending by post_date so auto-fill
        # and the "recent" / "stability" buckets fill from most-recent
        # outward — their caps short-circuit the rest.
        sorted_txns = sorted(
            book.transactions, key=lambda t: t.post_date, reverse=True
        )

        for txn in sorted_txns:
            txn_desc_lower = txn.description.lower()
            desc_match = (
                desc_lower in txn_desc_lower
                or txn_desc_lower in desc_lower
            )

            # --- Auto-fill: first description match wins ---
            if want_auto_fill and auto_fill_source is None and desc_match:
                auto_fill_source = txn

            # --- Stability: recent matches within horizon, capped ---
            if (
                want_stability
                and desc_match
                and txn.post_date >= stability_cutoff
                and len(stability_matches) < stability_limit
            ):
                stability_matches.append(txn)

            # --- Recent: for post-write split-consistency warning ---
            if (
                want_recent
                and desc_match
                and txn.post_date >= recent_cutoff
                and len(recent_matches) < recent_limit
            ):
                recent_matches.append(txn)

            # --- Duplicates: multi-signal check within date window ---
            if (
                want_duplicates
                and dup_start <= txn.post_date <= dup_end
            ):
                # Signal 2: any proposed amount within ±$1.00 of any
                # split's absolute value.
                amount_match = False
                txn_amounts = [abs(s.value) for s in txn.splits]
                for proposed_amt in proposed_amounts:
                    for txn_amt in txn_amounts:
                        if abs(proposed_amt - txn_amt) <= Decimal("1.00"):
                            amount_match = True
                            break
                    if amount_match:
                        break

                # Signal 3: date within ±2 days of trans_date (tighter
                # than the window filter — window is for "worth
                # considering at all").
                date_match = abs((txn.post_date - trans_date).days) <= 2

                signals = sum([desc_match, amount_match, date_match])
                if signals >= 2:
                    confidence = "HIGH" if signals == 3 else "MEDIUM"
                    signal_str = (
                        ("D" if desc_match else "-")
                        + ("A" if amount_match else "-")
                        + ("D" if date_match else "-")
                    )
                    # Primary amount: max absolute split value — enough
                    # context for the LLM to recognize the duplicate.
                    primary_amount = max(abs(s.value) for s in txn.splits)
                    duplicates.append({
                        "confidence": confidence,
                        "guid": txn_prefixes[txn.guid],
                        "date": txn.post_date.isoformat(),
                        "description": txn.description,
                        "amount": str(primary_amount),
                        "signals": signal_str,
                    })

        # --- Finalize the bundle ---

        # Auto-fill: build (splits_list, source_info) if we found one.
        auto_fill: tuple[list[dict], dict] | None = None
        if auto_fill_source is not None:
            filled_splits = []
            for s in auto_fill_source.splits:
                split_dict = {
                    "account": s.account.fullname,
                    "amount": str(s.value),
                }
                if s.quantity != s.value:
                    split_dict["quantity"] = str(s.quantity)
                if s.memo:
                    split_dict["memo"] = s.memo
                filled_splits.append(split_dict)
            source_info = {
                "guid": txn_prefixes[auto_fill_source.guid],
                "description": auto_fill_source.description,
                "date": auto_fill_source.post_date.isoformat(),
            }
            auto_fill = (filled_splits, source_info)

        # Stability: if 2+ recent matches disagree on pattern, warn.
        stability_warnings: list[dict] = []
        if want_stability and len(stability_matches) >= 2:
            patterns = [
                self._extract_account_pattern([s.account for s in t.splits])
                for t in stability_matches
            ]
            first_pattern = patterns[0]
            different = sum(1 for p in patterns[1:] if p != first_pattern)
            if different > 0:
                stability_warnings.append({
                    "type": "auto_fill_unstable",
                    "message": (
                        f"Recent '{description}' transactions use different "
                        f"account patterns. Auto-filled from most recent "
                        f"({stability_matches[0].post_date.isoformat()}), but "
                        f"{different} of {len(stability_matches)} recent "
                        f"matches used different categorization."
                    ),
                })

        # Duplicates: sort HIGH first.
        order = {"HIGH": 0, "MEDIUM": 1}
        duplicates.sort(key=lambda c: order[c["confidence"]])

        return _CreateSignals(
            auto_fill=auto_fill,
            stability_warnings=stability_warnings,
            duplicates=duplicates,
            recent_matches=recent_matches,
        )

    @staticmethod
    def _generate_warnings(
        trans_date: date,
        splits: list[dict],
        accounts: list,
    ) -> list[dict]:
        """Generate warnings for unusual but valid transaction attributes.

        Args:
            trans_date: Transaction date.
            splits: Original split dicts with 'amount' keys.
            accounts: Resolved piecash account objects, same order as splits.

        Returns:
            List of warning dicts with 'type' and 'message' keys.
        """
        warnings = []
        today = date.today()

        if trans_date > today:
            warnings.append({
                "type": "future_date",
                "message": f"Transaction date {trans_date.isoformat()} is in the future",
            })

        days_old = (today - trans_date).days
        if days_old > 365:
            warnings.append({
                "type": "old_date",
                "message": (
                    f"Transaction date {trans_date.isoformat()} "
                    f"is {days_old} days in the past"
                ),
            })

        for split_data, account in zip(splits, accounts):
            amount = Decimal(split_data["amount"])
            if account.type == "EXPENSE" and amount < 0:
                warnings.append({
                    "type": "negative_expense",
                    "message": (
                        f"Negative amount ({amount}) to expense account "
                        f"'{account.fullname}'"
                    ),
                })
            elif account.type == "INCOME" and amount > 0:
                warnings.append({
                    "type": "positive_income",
                    "message": (
                        f"Positive amount ({amount}) to income account "
                        f"'{account.fullname}'"
                    ),
                })

        return warnings

    def create_transaction(
        self,
        description: str,
        splits: list[dict] | None = None,
        trans_date: date | None = None,
        currency: str | None = None,
        notes: str | None = None,
        check_duplicates: bool = True,
        force_create: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Create a new transaction with splits.

        Args:
            description: Transaction description.
            splits: List of splits, each with:
                - 'account' (required): Full account path.
                - 'amount' (required): Value in transaction currency.
                - 'quantity' (optional): Amount in account's commodity.
                  Required if account commodity differs from transaction currency.
                - 'memo' (optional): Split memo.
                If omitted or empty, auto-fills from the most recent
                transaction with a matching description.
            trans_date: Transaction date. Defaults to today.
            currency: ISO currency code for the transaction (e.g., "USD", "EUR").
                      Defaults to book's default currency.
            notes: Transaction notes (optional). Free-text annotation
                   stored separately from the description.
            check_duplicates: Run duplicate detection. Default True.
            force_create: Create even if HIGH confidence duplicates found.
            dry_run: Validate and return proposal without writing.

        Returns:
            Dict with 'guid' and 'status' keys. May include 'warnings',
            'duplicates', and 'auto_filled_from'. If a HIGH duplicate is
            found and force_create is False, returns 'status': 'rejected'
            instead. In dry_run mode, returns 'dry_run': True with
            proposed transaction.

        Raises:
            ValueError: If splits don't balance, fewer than 2 splits,
                       accounts don't exist, cross-currency splits
                       missing quantity, or no match found for auto-fill.
        """
        # Dry runs don't need a writable session; all other paths do.
        readonly = dry_run
        if trans_date is None:
            trans_date = date.today()

        # One book-open for the whole create pipeline — preflight signal
        # gathering, write, and post-write consistency warning all live
        # inside this session.
        with self.open(readonly=readonly) as book:
            # --- Preflight pass 1: auto-fill + stability (if needed) ---
            #
            # When splits=None, we need the auto-fill source before we
            # can compute proposed_amounts for the duplicate scan. So
            # this first pass requests only auto-fill + stability, then
            # the second pass runs duplicates + recent with the now-known
            # amounts. When splits are provided up front, we skip pass 1
            # and do everything in pass 2 — a single scan.
            auto_filled_from = None
            auto_fill_warnings: list[dict] = []
            if not splits:
                preflight = self._collect_create_signals(
                    book,
                    description,
                    trans_date,
                    proposed_amounts=[],
                    want_auto_fill=True,
                    want_stability=True,
                    want_duplicates=False,
                    want_recent=False,
                )
                if preflight.auto_fill is None:
                    raise ValueError(
                        "No matching transaction found for auto-fill. "
                        "Provide explicit splits."
                    )
                splits, auto_filled_from = preflight.auto_fill
                auto_fill_warnings = preflight.stability_warnings

            if len(splits) < 2:
                raise ValueError("Transaction must have at least 2 splits")

            # Validate balance (using "amount" as transaction-currency value).
            total = Decimal("0")
            for split in splits:
                total += Decimal(split["amount"])
            if total != Decimal("0"):
                raise ValueError(f"Splits do not balance: total is {total}")

            proposed_amounts = [abs(Decimal(s["amount"])) for s in splits]

            # --- Preflight pass 2: duplicates + recent matches ---
            signals = self._collect_create_signals(
                book,
                description,
                trans_date,
                proposed_amounts,
                want_auto_fill=False,
                want_stability=False,
                want_duplicates=check_duplicates,
                want_recent=True,
            )
            duplicates = signals.duplicates

            # HIGH-confidence duplicate short-circuits the write.
            if (
                signals.has_high_duplicate
                and not force_create
                and not dry_run
            ):
                return {
                    "status": "rejected",
                    "reason": "duplicate_detected",
                    "duplicates": duplicates,
                }

            # --- Validate accounts and build piecash splits ---
            # Currency resolution: writable sessions may auto-create the
            # currency via ISO fallback; dry_run uses readonly and must
            # find an existing one (or default).
            if currency is None:
                trans_currency = self._require_default_currency(book)
            elif readonly:
                trans_currency = self._find_commodity(book, currency)
                if not trans_currency:
                    raise ValueError(
                        f"Currency '{currency}' not found in book. "
                        f"Dry run cannot create new currencies."
                    )
            else:
                trans_currency = self._get_or_create_currency(book, currency)

            piecash_splits = []
            resolved_accounts = []
            for split in splits:
                account = self._find_account(book, split["account"])
                if not account:
                    raise ValueError(f"Account not found: {split['account']}")

                if account.placeholder:
                    children_hint = ", ".join(
                        c.fullname for c in account.children
                    )
                    raise ValueError(
                        f"Account '{account.fullname}' is a placeholder and "
                        f"cannot receive transactions. "
                        f"Use one of: {children_hint}"
                    )

                resolved_accounts.append(account)
                value = Decimal(split["amount"])

                # Determine quantity (same-currency: equals value;
                # cross-currency: caller must provide and sign-match).
                if account.commodity == trans_currency:
                    quantity = value
                elif "quantity" in split:
                    quantity = Decimal(split["quantity"])
                    if quantity * value < 0:
                        raise ValueError(
                            f"Split for '{split['account']}': quantity and value "
                            f"must have same sign "
                            f"(got value={value}, quantity={quantity})"
                        )
                else:
                    raise ValueError(
                        f"Split for '{split['account']}' requires 'quantity' "
                        f"because account commodity "
                        f"({account.commodity.mnemonic}) differs from "
                        f"transaction currency ({trans_currency.mnemonic})"
                    )

                # Don't construct piecash.Split objects during dry_run —
                # adding to the session would stage a write even if we
                # never call book.save().
                if not readonly:
                    piecash_splits.append(
                        piecash.Split(
                            account=account,
                            value=value,
                            quantity=quantity,
                            memo=split.get("memo", ""),
                        )
                    )

            # --- Warnings (shared by dry_run and write paths) ---
            warnings = self._generate_warnings(
                trans_date, splits, resolved_accounts
            )
            proposed_pattern = self._extract_account_pattern(resolved_accounts)
            # The collector gathered recent matches before we wrote, so
            # the just-created txn is automatically absent — no post-
            # write exclusion needed.
            if signals.recent_matches:
                recent_accts = [
                    s.account for s in signals.recent_matches[0].splits
                ]
                recent_pattern = self._extract_account_pattern(recent_accts)
                if proposed_pattern != recent_pattern:
                    warnings.append({
                        "type": "split_consistency",
                        "message": (
                            f"Recent '{signals.recent_matches[0].description}' "
                            f"transactions used "
                            f"{', '.join(sorted(recent_pattern))}, but this "
                            f"transaction uses "
                            f"{', '.join(sorted(proposed_pattern))}."
                        ),
                    })
            warnings.extend(auto_fill_warnings)

            # --- Dry run branches out here with the proposal ---
            if dry_run:
                result: dict = {
                    "dry_run": True,
                    "warnings": warnings,
                    "duplicates": duplicates,
                }
                if auto_filled_from:
                    result["auto_filled_from"] = auto_filled_from
                return result

            # --- Commit the write ---
            transaction = piecash.Transaction(
                currency=trans_currency,
                description=description,
                notes=notes,
                post_date=trans_date,
                splits=piecash_splits,
            )
            book.save()

            # Emit a collision-safe short guid prefix so the LLM can feed
            # it straight back into guid-accepting tools without spending
            # tokens on the full 32-char string.
            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            result = {"guid": short_guid, "status": "created"}
            if warnings:
                result["warnings"] = warnings
            if duplicates:
                result["duplicates"] = duplicates
            if auto_filled_from:
                result["auto_filled_from"] = auto_filled_from
            return result

    def search_transactions(
        self, query: str, field: str = "description", compact: bool = True,
    ) -> list[dict] | str:
        """Search transactions by field.

        Args:
            query: Search string. For 'amount' field, supports:
                   - Exact: "100.00"
                   - Greater than: ">100"
                   - Less than: "<100"
                   - Range: "100-200"
            field: Field to search: 'description', 'memo', 'notes',
                   or 'amount'.
            compact: If True (default), return a compact newline-separated
                     string with one line per transaction. If False, return
                     the full list of transaction dicts.

        Returns:
            If compact: newline-separated string of transaction lines.
            If not compact: list of matching transaction dicts.

        Raises:
            ValueError: If field is not valid.
        """
        if field not in ("description", "memo", "notes", "amount"):
            raise ValueError(f"Invalid search field: {field}")

        with self.open(readonly=True) as book:
            matched = []

            for transaction in book.transactions:
                if field == "description":
                    if query.lower() in transaction.description.lower():
                        matched.append(transaction)

                elif field == "notes":
                    if transaction.notes and query.lower() in transaction.notes.lower():
                        matched.append(transaction)

                elif field == "memo":
                    for split in transaction.splits:
                        if split.memo and query.lower() in split.memo.lower():
                            matched.append(transaction)
                            break

                elif field == "amount":
                    if self._match_amount(transaction, query):
                        matched.append(transaction)

            # Sort by date descending
            matched.sort(key=lambda t: t.post_date, reverse=True)

            if compact:
                # Collision-safe prefix map over the whole transactions table
                prefixes = _guid_prefix_map(t.guid for t in book.transactions)
                lines = [
                    _transaction_to_compact_line(t, prefixes=prefixes)
                    for t in matched
                ]
                return "\n".join(lines)
            else:
                return [_transaction_to_dict(t) for t in matched]

    def _match_amount(self, transaction: piecash.Transaction, query: str) -> bool:
        """Check if any split amount matches the query.

        Args:
            transaction: Transaction to check.
            query: Amount query (exact, >N, <N, or N-M range).

        Returns:
            True if any split matches.

        Raises:
            ValueError: If the amount query is malformed.
        """
        # Get absolute values of all splits
        amounts = [abs(split.value) for split in transaction.splits]

        # Parse query
        query = query.strip()

        try:
            # Greater than: >100
            if query.startswith(">"):
                threshold = Decimal(query[1:])
                return any(amt > threshold for amt in amounts)

            # Less than: <100
            if query.startswith("<"):
                threshold = Decimal(query[1:])
                return any(amt < threshold for amt in amounts)

            # Range: 100-200
            if "-" in query and not query.startswith("-"):
                parts = query.split("-")
                if len(parts) == 2:
                    low = Decimal(parts[0])
                    high = Decimal(parts[1])
                    return any(low <= amt <= high for amt in amounts)

            # Exact match
            target = Decimal(query)
            return any(amt == target for amt in amounts)

        except InvalidOperation as e:
            raise ValueError(f"Invalid amount query '{query}': {e}") from e

    # Valid GnuCash account types
    VALID_ACCOUNT_TYPES = {
        "ASSET",
        "BANK",
        "CASH",
        "CREDIT",
        "EQUITY",
        "EXPENSE",
        "INCOME",
        "LIABILITY",
        "MUTUAL",
        "PAYABLE",
        "RECEIVABLE",
        "STOCK",
    }

    def create_account(
        self,
        name: str,
        account_type: str,
        parent: str | None = None,
        description: str = "",
        placeholder: bool = False,
        commodity: str | None = None,
        commodity_namespace: str = "CURRENCY",
    ) -> dict:
        """Create a new account in the chart of accounts.

        Args:
            name: Account name (e.g., "AI Subscriptions").
            account_type: GnuCash account type (ASSET, EXPENSE, etc.).
            parent: Full path of parent account (e.g., "Expenses:Online Services").
                    If omitted, creates a top-level account at the book root.
            description: Optional description.
            placeholder: If True, account is container-only. Default False.
            commodity: ISO currency code (e.g., "USD", "EUR") or commodity mnemonic.
                       Defaults to book's default currency.
            commodity_namespace: Commodity namespace for non-currency commodities.
                                Default "CURRENCY".

        Returns:
            Dict with guid, fullname, and status. Includes a warning if
            created at root level.

        Raises:
            ValueError: If parent not found, invalid type, duplicate name,
                       or invalid commodity.
        """
        # Validate account type
        if account_type.upper() not in self.VALID_ACCOUNT_TYPES:
            raise ValueError(
                f"Invalid account type: {account_type}. "
                f"Valid types: {', '.join(sorted(self.VALID_ACCOUNT_TYPES))}"
            )

        with self.open(readonly=False) as book:
            # Determine parent account
            is_root_level = parent is None
            if is_root_level:
                parent_account = book.root_account
                parent_label = "root"
            else:
                parent_account = self._find_account(book, parent)
                if not parent_account:
                    raise ValueError(f"Parent account not found: {parent}")
                parent_label = parent

            # Check for duplicate - same name under same parent
            for child in parent_account.children:
                if child.name == name:
                    raise ValueError(
                        f"Account '{name}' already exists under '{parent_label}'"
                    )

            # Determine commodity
            if commodity is None:
                account_commodity = self._require_default_currency(book)
            elif commodity_namespace == "CURRENCY":
                account_commodity = self._get_or_create_currency(book, commodity)
            else:
                account_commodity = self._find_commodity(
                    book, commodity, commodity_namespace
                )
                if not account_commodity:
                    raise ValueError(
                        f"Commodity not found: {commodity_namespace}:{commodity}"
                    )

            # Create the account
            new_account = piecash.Account(
                name=name,
                type=account_type.upper(),
                parent=parent_account,
                commodity=account_commodity,
                description=description,
                placeholder=placeholder,
            )

            book.save()

            short_guid = _unique_prefix(
                new_account.guid, (a.guid for a in book.accounts)
            )
            result = {
                "guid": short_guid,
                "fullname": new_account.fullname,
                "status": "created",
            }
            if is_root_level:
                result["warning"] = (
                    "Account created at root level, outside the standard "
                    "account hierarchy (Assets, Liabilities, Equity, Income, "
                    "Expenses). This may affect reports and balance sheet "
                    "calculations."
                )
            return result

    # Polarity groups for account type change validation.
    # Types within the same group can be freely converted.
    _ACCOUNT_TYPE_POLARITY = {
        "ASSET": "debit_asset",
        "BANK": "debit_asset",
        "CASH": "debit_asset",
        "RECEIVABLE": "debit_asset",
        "LIABILITY": "credit_liability",
        "CREDIT": "credit_liability",
        "PAYABLE": "credit_liability",
        "INCOME": "credit_income",
        "EXPENSE": "debit_expense",
        "EQUITY": "credit_equity",
        "STOCK": "debit_investment",
        "MUTUAL": "debit_investment",
    }

    def update_account(
        self,
        name: str,
        new_name: str | None = None,
        description: str | None = None,
        placeholder: bool | None = None,
        account_type: str | None = None,
    ) -> dict:
        """Update an existing account's properties.

        Args:
            name: Full account path to update (e.g., "Expenses:Groceries").
            new_name: New name for the account (just the name, not full path).
            description: New description.
            placeholder: New placeholder status.
            account_type: New account type (e.g., "CREDIT", "BANK"). Only
                changes within the same debit/credit polarity family are
                allowed (e.g., LIABILITY to CREDIT, ASSET to BANK).

        Returns:
            Dict with updated account details.

        Raises:
            ValueError: If account not found, new name conflicts, or type
                change would flip debit/credit polarity.
        """
        with self.open(readonly=False) as book:
            account = self._find_account(book, name)
            if not account:
                raise ValueError(f"Account not found: {name}")

            # Stage pre-mutation state for the audit log so the decorator
            # doesn't have to reopen the book to capture it.
            self._stage_audit_before(_account_to_dict(account))

            # Check for name conflict if renaming
            if new_name and new_name != account.name:
                if account.parent:
                    for sibling in account.parent.children:
                        if sibling.name == new_name and sibling.guid != account.guid:
                            raise ValueError(
                                f"Account '{new_name}' already exists under "
                                f"'{account.parent.fullname}'"
                            )
                account.name = new_name

            if description is not None:
                account.description = description

            if placeholder is not None:
                account.placeholder = placeholder

            if account_type is not None:
                new_type = account_type.upper()
                old_type = account.type

                if new_type not in self.VALID_ACCOUNT_TYPES:
                    raise ValueError(
                        f"Invalid account type: {new_type}. "
                        f"Valid types: {', '.join(sorted(self.VALID_ACCOUNT_TYPES))}"
                    )

                if new_type != old_type:
                    old_polarity = self._ACCOUNT_TYPE_POLARITY.get(old_type)
                    new_polarity = self._ACCOUNT_TYPE_POLARITY.get(new_type)

                    if old_polarity != new_polarity:
                        raise ValueError(
                            f"Cannot change account type from {old_type} to "
                            f"{new_type} — this would flip the debit/credit "
                            f"polarity and corrupt existing transaction balances."
                        )

                    account.type = new_type

            book.save()

            short_guid = _unique_prefix(
                account.guid, (a.guid for a in book.accounts)
            )
            return _account_to_dict(account) | {
                "guid": short_guid,
                "status": "updated",
            }

    def move_account(self, name: str, new_parent: str) -> dict:
        """Move an account to a new parent in the hierarchy.

        Args:
            name: Full account path to move (e.g., "Expenses:Old:Account").
            new_parent: Full path of the new parent account.

        Returns:
            Dict with updated account details including new fullname.

        Raises:
            ValueError: If account or parent not found, or would create cycle.
        """
        with self.open(readonly=False) as book:
            account = self._find_account(book, name)
            if not account:
                raise ValueError(f"Account not found: {name}")

            # Stage pre-move state (fullname derives old parent for log).
            self._stage_audit_before(_account_to_dict(account))

            new_parent_account = self._find_account(book, new_parent)
            if not new_parent_account:
                raise ValueError(f"Parent account not found: {new_parent}")

            # Check for circular reference (can't move to self or descendant)
            check = new_parent_account
            while check:
                if check.guid == account.guid:
                    raise ValueError(
                        f"Cannot move account under itself or its descendants"
                    )
                check = check.parent

            # Check for name conflict in new location
            for sibling in new_parent_account.children:
                if sibling.name == account.name:
                    raise ValueError(
                        f"Account '{account.name}' already exists under '{new_parent}'"
                    )

            account.parent = new_parent_account

            book.save()

            short_guid = _unique_prefix(
                account.guid, (a.guid for a in book.accounts)
            )
            return _account_to_dict(account) | {
                "guid": short_guid,
                "status": "moved",
            }

    def delete_account(self, name: str) -> dict:
        """Delete an account from the chart of accounts.

        Args:
            name: Full account path to delete.

        Returns:
            Dict with deleted account info and status.

        Raises:
            ValueError: If account not found, has children, or has transactions.
        """
        with self.open(readonly=False) as book:
            account = self._find_account(book, name)
            if not account:
                raise ValueError(f"Account not found: {name}")

            # Safeguard: Check for children
            if account.children:
                child_names = [c.name for c in account.children]
                raise ValueError(
                    f"Cannot delete account with children: {', '.join(child_names)}"
                )

            # Safeguard: Check for transactions (splits)
            if account.splits:
                raise ValueError(
                    f"Cannot delete account with {len(account.splits)} transaction(s). "
                    f"Move or delete transactions first."
                )

            # Stage pre-delete state for the audit log.
            self._stage_audit_before(_account_to_dict(account))

            # Capture info before deletion. Short guid computed against
            # the remaining accounts (the target is still present here).
            short_guid = _unique_prefix(
                account.guid, (a.guid for a in book.accounts)
            )
            result = {
                "guid": short_guid,
                "fullname": account.fullname,
                "status": "deleted",
            }

            book.session.delete(account)
            book.save()

            return result

    def delete_transaction(self, guid: str, force: bool = False) -> dict:
        """Delete a transaction by GUID.

        Args:
            guid: Transaction GUID (32-character hex string).
            force: If True, allow deleting transactions with reconciled splits.

        Returns:
            Dict with guid, description, and status.

        Raises:
            ValueError: If transaction not found, or has reconciled splits
                       and force is False.
        """
        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # Check for reconciled splits
            reconciled = [
                s for s in transaction.splits if s.reconcile_state == "y"
            ]
            if reconciled and not force:
                acct_names = ", ".join(s.account.fullname for s in reconciled)
                raise ValueError(
                    f"Transaction has reconciled splits in: {acct_names}. "
                    f"Deleting will break reconciliation. Use force=true to override."
                )

            # Stage pre-delete state for the audit log.
            self._stage_audit_before(_transaction_to_dict(transaction))

            # Short guid computed pre-delete (target still in the table).
            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            result = {
                "guid": short_guid,
                "description": transaction.description,
                "status": "deleted",
            }
            if reconciled:
                result["reconciled_splits_affected"] = len(reconciled)

            # Delete the transaction
            book.session.delete(transaction)
            book.save()

            return result

    def update_transaction(
        self,
        guid: str,
        description: str | None = None,
        trans_date: date | None = None,
        splits: list[dict] | None = None,
        notes: str | None = None,
        force: bool = False,
    ) -> dict:
        """Update an existing transaction.

        Args:
            guid: Transaction GUID to update.
            description: New description (optional).
            trans_date: New transaction date (optional).
            splits: List of split updates with 'account', 'amount', and
                    optionally 'quantity' (optional). Must match existing
                    splits by account name. For cross-currency splits,
                    'quantity' is required when the account commodity differs
                    from the transaction currency.
            notes: New transaction notes (optional). Pass empty string
                   to clear existing notes.
            force: If True, allow modifying transactions with reconciled
                   splits. Only checked when splits are being updated.

        Returns:
            Dict with updated transaction details.

        Raises:
            ValueError: If transaction not found, splits don't balance,
                       account not found in splits, cross-currency split
                       missing quantity, or has reconciled splits and
                       force is False.
        """
        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # Check for reconciled splits when modifying splits
            if splits is not None:
                reconciled = [
                    s for s in transaction.splits if s.reconcile_state == "y"
                ]
                if reconciled and not force:
                    acct_names = ", ".join(
                        s.account.fullname for s in reconciled
                    )
                    raise ValueError(
                        f"Transaction has reconciled splits in: {acct_names}. "
                        f"Modifying will break reconciliation. "
                        f"Use force=true to override."
                    )

            # Stage pre-update state for the audit log.
            self._stage_audit_before(_transaction_to_dict(transaction))

            # Update description if provided
            if description is not None:
                transaction.description = description

            # Update notes if provided
            if notes is not None:
                transaction.notes = notes if notes else None

            # Update date if provided
            if trans_date is not None:
                transaction.post_date = trans_date

            # Update splits if provided
            if splits is not None:
                # Validate splits balance to zero
                total = Decimal("0")
                for split in splits:
                    total += Decimal(split["amount"])
                if total != Decimal("0"):
                    raise ValueError(f"Splits do not balance: total is {total}")

                # Build a map of account -> split data
                split_updates = {s["account"]: s for s in splits}

                trans_currency = transaction.currency

                # Update existing splits
                for split in transaction.splits:
                    account_name = split.account.fullname
                    if account_name in split_updates:
                        update = split_updates[account_name]
                        new_value = Decimal(update["amount"])
                        split.value = new_value

                        # Determine quantity
                        if split.account.commodity == trans_currency:
                            split.quantity = new_value
                        elif "quantity" in update:
                            new_quantity = Decimal(update["quantity"])
                            if new_quantity * new_value < 0:
                                raise ValueError(
                                    f"Split for '{account_name}': quantity and value "
                                    f"must have same sign "
                                    f"(got value={new_value}, quantity={new_quantity})"
                                )
                            split.quantity = new_quantity
                        else:
                            raise ValueError(
                                f"Split for '{account_name}' requires 'quantity' "
                                f"because account commodity "
                                f"({split.account.commodity.mnemonic}) differs from "
                                f"transaction currency ({trans_currency.mnemonic})"
                            )

                        # Update memo if provided
                        if "memo" in update:
                            split.memo = update["memo"]

                        del split_updates[account_name]

                # Check if all provided accounts were found
                if split_updates:
                    missing = list(split_updates.keys())[0]
                    raise ValueError(f"Account not found in transaction: {missing}")

            book.save()

            # Thin response — the LLM submitted the changes, so the only
            # fields worth echoing are enough for a quick sanity check
            # (guid + currently-stored description/date). If they want
            # the full post-update state they can call get_transaction.
            # The audit log resolves splits/description/date from params
            # when absent from after_state (see _resolve_entry_field).
            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            return {
                "guid": short_guid,
                "date": transaction.post_date.isoformat(),
                "description": transaction.description,
                "status": "updated",
            }

    def replace_splits(
        self,
        guid: str,
        splits: list[dict],
        force: bool = False,
    ) -> dict:
        """Replace all splits in a transaction with a new set.

        Replace all splits in a transaction with a completely new set.
        The transaction's currency, description, date, and notes are preserved.
        New splits must balance to zero.

        Args:
            guid: Transaction GUID.
            splits: Complete new set of splits. Each split needs:
                - 'account' (required): Full account path
                - 'amount' (required): Value in transaction currency
                - 'quantity' (optional): Amount in account's commodity.
                  Required if account commodity differs from transaction currency.
                - 'memo' (optional): Split memo
            force: Required if existing splits are reconciled ('y') or
                   assigned to lots.

        Returns:
            Dict with updated transaction details, previous splits for audit
            trail, status, and any warnings.

        Raises:
            ValueError: If transaction not found, splits don't balance,
                       account not found, placeholder account used,
                       cross-currency split missing quantity, or has
                       reconciled/lot splits without force.
        """
        # Validate split count upfront
        if len(splits) < 2:
            raise ValueError("At least 2 splits required")

        # Validate balance upfront
        total = sum(Decimal(s["amount"]) for s in splits)
        if total != Decimal("0"):
            raise ValueError(f"Splits do not balance: total is {total}")

        with self.open(readonly=False) as book:
            warnings = []

            # 1. Find transaction
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            # 2. Capture previous splits for audit trail (before deletion).
            # Use the compact serializer — old-split GUIDs are not
            # addressable anymore, quantity/memo/reconcile_state only
            # emit when non-default. ~50 chars/split vs. ~140.
            previous_splits = [
                _split_to_compact_dict(s) for s in transaction.splits
            ]

            # Stage pre-replace state for the audit log (description/date
            # aren't changing but the REPLACE_SPLITS formatter wants them).
            self._stage_audit_before(_transaction_to_dict(transaction))

            # 3. Resolve and validate all accounts upfront
            resolved_accounts = []
            for split_data in splits:
                account_name = split_data["account"]
                account = self._find_account(book, account_name)
                if not account:
                    raise ValueError(f"Account not found: {account_name}")
                if account.placeholder:
                    raise ValueError(
                        f"Cannot use placeholder account: {account_name}"
                    )
                resolved_accounts.append((account, split_data))

            # 4. Check reconciled splits
            reconciled = [
                s for s in transaction.splits if s.reconcile_state == "y"
            ]
            if reconciled and not force:
                names = ", ".join(s.account.fullname for s in reconciled)
                raise ValueError(
                    f"Transaction has reconciled splits in: {names}. "
                    f"Use force=true to override."
                )
            if reconciled:
                names = ", ".join(s.account.fullname for s in reconciled)
                warnings.append(f"Replaced reconciled splits in: {names}")

            # 5. Check lot assignments
            in_lots = [s for s in transaction.splits if s.lot is not None]
            if in_lots and not force:
                names = ", ".join(s.account.fullname for s in in_lots)
                raise ValueError(
                    f"Transaction has splits in lots: {names}. "
                    f"Use force=true to override."
                )
            if in_lots:
                lot_info = ", ".join(
                    f"{s.lot.title} ({s.account.fullname})" for s in in_lots
                )
                warnings.append(
                    f"Removed splits from lots: {lot_info}. "
                    f"Cost basis tracking affected."
                )

            # 6. Delete existing splits
            for split in list(transaction.splits):
                book.delete(split)

            # 7. Create new splits
            trans_currency = transaction.currency
            for account, split_data in resolved_accounts:
                amount = Decimal(split_data["amount"])

                # Determine quantity
                if account.commodity == trans_currency:
                    quantity = amount
                elif "quantity" in split_data:
                    quantity = Decimal(split_data["quantity"])
                    if quantity * amount < 0:
                        raise ValueError(
                            f"Split for '{account.fullname}': quantity and "
                            f"value must have same sign "
                            f"(got value={amount}, quantity={quantity})"
                        )
                else:
                    raise ValueError(
                        f"Split for '{account.fullname}' requires 'quantity' "
                        f"because account commodity "
                        f"({account.commodity.mnemonic}) differs from "
                        f"transaction currency ({trans_currency.mnemonic})"
                    )

                piecash.Split(
                    account=account,
                    value=amount,
                    quantity=quantity,
                    memo=split_data.get("memo", ""),
                    transaction=transaction,
                )

            # 8. Save
            book.save()

            # 9. Build thin response.
            # - `splits` echo dropped (LLM just submitted them).
            # - description/date/currency/notes don't change on a splits
            #   replace, so no reason to re-send them.
            # - previous_splits is the one piece the LLM doesn't already
            #   have — kept so callers can diff / undo / confirm.
            # - Audit log falls back to params for the "after" splits
            #   (see logging_config._resolve_entry_field).
            short_guid = _unique_prefix(
                transaction.guid, (t.guid for t in book.transactions)
            )
            result = {
                "guid": short_guid,
                "status": "splits_replaced",
                "previous_splits": previous_splits,
            }
            if warnings:
                result["warnings"] = warnings

            return result

    # Reconciliation / reporting / budgets / scheduling / lots methods moved to their mixins.
