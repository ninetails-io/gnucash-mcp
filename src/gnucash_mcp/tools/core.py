"""Core tools: book summary, accounts, transactions, search.

Always registered — `_apply_module_filter` forces 'core' into the
enabled set. Kept in its own register() function for consistency
with the other modules so server.py can treat every module the same
way (pure lazy-load orchestration, no hardcoded imports).
"""

from datetime import date

from gnucash_mcp.logging_config import audit_log
from gnucash_mcp.tools._helpers import (
    SplitInput,
    TransactionGuid,
    _json,
    _parse_iso_date,
    _splits_to_dicts,
    safe_tool,
)


def _parse_transactions_tsv(tsv: str) -> list[dict]:
    """Parse the batch-entry TSV into structured transactions.

    Header row + one row per transaction; positional parse so rows may
    be ragged. First three fields are ref / date / description; the rest
    are ``(amount, account)`` pairs. Raises ValueError on a missing
    header, too-few columns, or an odd trailing count.
    """
    lines = [ln for ln in tsv.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError(
            "transactions TSV needs a header row and at least one data row"
        )
    out: list[dict] = []
    for i, ln in enumerate(lines[1:], start=1):
        fields = ln.split("\t")
        if len(fields) < 5:
            raise ValueError(
                f"row {i}: expected ref, date, description, and at least "
                f"one (amount, account) pair"
            )
        ref, dt, desc = fields[0].strip(), fields[1].strip(), fields[2]
        if not ref:
            raise ValueError(f"row {i}: empty ref (each row needs a key)")
        rest = fields[3:]
        if len(rest) % 2 != 0:
            raise ValueError(
                f"row {i} (ref {ref!r}): trailing fields must be "
                f"(amount, account) pairs — got an odd count"
            )
        splits = [
            {"account": rest[j + 1], "amount": rest[j]}
            for j in range(0, len(rest), 2)
        ]
        out.append({
            "ref": ref,
            "date": _parse_iso_date(dt) or date.today(),
            "description": desc,
            "splits": splits,
        })
    return out


def register(mcp, get_book) -> None:
    """Attach core tools to the FastMCP server."""

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_book_summary() -> str:
        """Get a compact overview of the entire GnuCash book.

        Returns book path, currency, account structure, transaction counts,
        key balances, net worth, commodities, and scheduled transactions
        in a single text response. Use this first to orient yourself.
        """
        book = get_book()
        summary = book.get_book_summary()
        # Multi-book sessions: name the current book up front so the
        # client knows which book these numbers belong to. The book
        # layer stays ignorant of server session state, so the marker
        # is added here, not in get_book_summary itself.
        from gnucash_mcp import server as _server
        if _server.multi_book_active():
            from gnucash_mcp._format import _book_display_name
            name = _book_display_name(book.book_path)
            count = len(_server._book_paths)
            summary = (
                f"Current book: {name} ({count} books available — "
                f"switch_book to change)\n{summary}"
            )
        return summary

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_accounts(
        root: str | None = None,
        verbose: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """List all accounts in the GnuCash chart of accounts.

        Leads with a ``Showing X-Y of Z accounts`` line, then a compact
        one-line-per-account format by default. Page with ``offset``;
        ``limit=0`` returns the count only. Use verbose=true for full
        JSON with guid, type, commodity, etc.

        Args:
            root: Filter to a subtree (e.g., "Expenses" for expense accounts only).
            verbose: If true, return full JSON details for each account.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
        """
        book = get_book()
        result = book.list_accounts(
            root=root, compact=not verbose, limit=limit, offset=offset
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_account(name: str) -> str:
        """Get details for a specific account by name.

        Args:
            name: Account ref: full path (e.g. 'Assets:Bank:Checking'), %short GUID, or full 32-char GUID
        """
        book = get_book()
        result = book.get_account(name)
        if result is None:
            return _json({"error": f"Account not found: {name}"})
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_balance(account_name: str, as_of_date: str | None = None) -> str:
        """Get the balance of an account as of a specific date.

        Defaults to today's date — future-dated transactions
        (scheduled payments, accrued interest) are excluded. To
        project a balance forward including future entries, pass
        an explicit ``as_of_date`` past today.

        Args:
            account_name: Account ref: full path (e.g. 'Assets:Bank:Checking'), %short GUID, or full 32-char GUID
            as_of_date: Date in ISO format (YYYY-MM-DD). Defaults to today.
        """
        book = get_book()
        date_obj = _parse_iso_date(as_of_date)
        # Deliberate double-fetch (get_account + get_balance): the
        # extra indexed query buys the canonical-fullname echo that
        # TestCanonicalAccountEcho (tests/test_book.py) locks in —
        # a %short caller instantly confirms which account answered.
        account_dict = book.get_account(account_name)
        if account_dict is None:
            raise ValueError(f"Account not found: {account_name}")
        canonical_name = account_dict["fullname"]
        balance = book.get_balance(account_name, date_obj)
        resolved_date = as_of_date if as_of_date else date.today().isoformat()
        result = {
            "account": canonical_name,
            "balance": str(balance),
            "as_of_date": resolved_date,
        }
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def list_transactions(
        account: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
        offset: int = 0,
        verbose: bool = False,
    ) -> str:
        """List transactions with optional filters.

        Leads with a ``Showing X-Y of Z transactions (date range)``
        line so a truncated view is never mistaken for the whole set.
        Page with ``offset``; ``limit=0`` returns the count only.

        Compact format (default):
        - Unfiltered: ``DATE<TAB>guid<TAB>Description<TAB>splits``
        - Filtered by account (register form):
          ``DATE<TAB>guid<TAB>±Amount<TAB>Description<TAB>other splits``
          Column 3 is the signed impact on the filtered account; that
          account is dropped from the splits column.

        Transactions with more than 4 splits collapse to the top 3 by
        |value| plus ``+N more`` — call ``get_transaction`` for the
        full breakdown.

        Args:
            account: Filter by account name (switches output to register form)
            start_date: Start date in ISO format (YYYY-MM-DD)
            end_date: End date in ISO format (YYYY-MM-DD)
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
            verbose: If true, return full JSON details for each transaction.
        """
        book = get_book()
        start = _parse_iso_date(start_date)
        end = _parse_iso_date(end_date)
        result = book.list_transactions(
            account, start, end, limit, offset, compact=not verbose
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_transaction(
        guid: TransactionGuid,
    ) -> str:
        """Get details for a specific transaction by GUID.

        Args:
            guid: Transaction GUID (32-character hex string, or 8+ char prefix)
        """
        book = get_book()
        result = book.get_transaction(guid)
        if result is None:
            return _json({"error": f"Transaction not found: {guid}"})
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="transaction")
    def create_transaction(
        description: str,
        splits: list[SplitInput] | None = None,
        transaction_date: str | None = None,
        currency: str | None = None,
        notes: str | None = None,
        check_duplicates: bool = True,
        force_create: bool = False,
        dry_run: bool = False,
    ) -> str:
        """Create a new transaction with splits. Splits must balance to zero.

        Each split: ``account`` (full path, required), ``amount``
        (required, in transaction currency), ``quantity`` (required
        when account commodity differs from transaction currency),
        ``memo`` (optional). ``amount`` and ``quantity`` are decimal
        strings (e.g. "94.87") — never raw JSON numbers, which would
        lose precision on non-dyadic decimals.

        When duplicate detection surfaces candidates (either rejecting
        the write with ``status: "rejected"`` or returning alongside a
        successful create), ``duplicates`` in the response is a
        newline-separated TSV string, not a list of dicts. Columns::

            confidence<TAB>guid<TAB>date<TAB>amount<TAB>description<TAB>signals

        Confidence is ``HIGH`` (all three signals match) or ``MEDIUM``
        (two of three). Signals is a three-char code: position 0
        description, position 1 amount (±$1 tolerance), position 2
        date (±2 days); ``D``/``A``/``D`` for match, ``-`` for miss.

        Args:
            description: Transaction description.
            splits: List of split dicts (see above). Omit to auto-fill
                from the most recent matching-description transaction.
            transaction_date: ISO date (YYYY-MM-DD). Defaults to today.
            currency: ISO currency code. Defaults to book's default.
            notes: Optional free-text annotation.
            check_duplicates: Run duplicate detection. Default True.
            force_create: Create even if HIGH-confidence duplicates found.
            dry_run: Validate + dupe check only; don't write.
        """
        book = get_book()
        trans_date = _parse_iso_date(transaction_date)
        result = book.create_transaction(
            description=description,
            splits=_splits_to_dicts(splits),
            trans_date=trans_date,
            currency=currency,
            notes=notes,
            check_duplicates=check_duplicates,
            force_create=force_create,
            dry_run=dry_run,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(
        classification="write", operation="create_batch",
        entity_type="transaction",
    )
    def create_transactions(
        transactions: str,
        force: bool = False,
        dry_run: bool = False,
        on_error: str = "abort",
    ) -> str:
        """Create MANY transactions in one atomic command (bulk entry).

        INPUT — ``transactions`` is a TSV block: a header row, then one
        row per transaction. Splits are ``(amount, account)`` column
        PAIRS, repeated as wide as a transaction needs::

            ref<TAB>date<TAB>description<TAB>amt1<TAB>acct1<TAB>amt2<TAB>acct2...
            1<TAB>2026-05-21<TAB>Gas<TAB>-54.19<TAB>Assets:Checking<TAB>54.19<TAB>Expenses:Auto:Fuel

        - ``ref``: YOUR correlation key per row (e.g. 1, 2, 3), unique
          within the batch. It is echoed back so you can match results
          to what you sent; the server never reuses or interprets it.
        - ``date``: ISO YYYY-MM-DD. ``amount``: decimal STRING (never a
          raw JSON number). Each transaction needs >=2 splits balancing
          to zero. Rows may differ in width (2 splits vs 3).
        - v1 is same-currency (book default) with no per-split memo —
          use ``create_transaction`` for cross-currency, investment, or
          memo-bearing entries.

        BEHAVIOR — one book-open, one atomic save:
        - A STRUCTURAL error (unbalanced, unknown account, bad pairs)
          aborts the WHOLE batch by default; nothing is written. Pass
          ``on_error="skip"`` to write the good rows and reject only the
          bad ones.
        - A duplicate rejects ONLY its row; ``force=True`` overrides all
          blocking duplicates (as in ``create_transaction``).
          ``dry_run=True`` validates + screens without writing.

        OUTPUT — a JSON envelope of two TSV tables joined by ``ref``:
        - ``results`` (always): ``ref, status, txn_guid, dup_count,
          reason``. status is ``created`` | ``rejected`` |
          ``would_create`` (dry_run); reason is a code like
          ``duplicate_detected`` or the validation message.
        - ``duplicates`` (only when matches exist): ``ref, confidence,
          guid, date, amount, description, signals`` — the columns
          ``create_transaction`` emits, keyed back to the offending
          ``ref``. Σ(dup_count) equals the duplicates row count.

        Args:
            transactions: The TSV block described above.
            force: Override ALL blocking (HIGH) duplicates this batch.
            dry_run: Validate + screen, write nothing.
            on_error: "abort" (default) or "skip" for structural errors.
        """
        book = get_book()
        parsed = _parse_transactions_tsv(transactions)
        result = book.create_transactions(
            parsed, force=force, dry_run=dry_run, on_error=on_error,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def search_transactions(
        query: str,
        field: str = "description",
        limit: int = 50,
        offset: int = 0,
        verbose: bool = False,
    ) -> str:
        """Search transactions by description, memo, notes, or amount.

        Compact format (default):
        ``DATE<TAB>guid<TAB>Description<TAB>splits``
        Transactions with more than 4 splits collapse to the top 3 by
        |value| plus ``+N more`` — call ``get_transaction`` for the
        full breakdown. Leads with a ``Showing X-Y of Z transactions``
        line; page with ``offset``, or pass ``limit=0`` for the count.

        Args:
            query: Search query string. For amount, supports: exact ("100"), greater (">100"), less ("<100"), range ("100-200")
            field: Field to search: 'description', 'memo', 'notes', or 'amount'
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
            verbose: If true, return full JSON details for each transaction.
        """
        book = get_book()
        result = book.search_transactions(
            query, field, limit=limit, offset=offset, compact=not verbose
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="create", entity_type="account")
    def create_account(
        name: str,
        account_type: str,
        parent: str | None = None,
        description: str = "",
        placeholder: bool = False,
        commodity: str | None = None,
        commodity_namespace: str = "CURRENCY",
    ) -> str:
        """Create a new account in the chart of accounts.

        Args:
            name: Account name (e.g., "AI Subscriptions").
            account_type: One of ASSET, BANK, CASH, CREDIT, EQUITY,
                EXPENSE, INCOME, LIABILITY, MUTUAL, STOCK, RECEIVABLE,
                PAYABLE.
            parent: Parent account ref (full path, %short GUID, or full
                32-char GUID). Omit for top-level.
            description: Optional description.
            placeholder: Container-only account. Default False.
            commodity: ISO currency code ("USD") or stock/fund symbol
                ("VTSAX"). Defaults to book's default currency.
            commodity_namespace: "CURRENCY" (default), "FUND", or an
                exchange ("NASDAQ", "NYSE"). Required with non-currency
                commodities.
        """
        book = get_book()
        result = book.create_account(
            name=name,
            account_type=account_type,
            parent=parent,
            description=description,
            placeholder=placeholder,
            commodity=commodity,
            commodity_namespace=commodity_namespace,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="account")
    def update_account(
        name: str,
        new_name: str | None = None,
        description: str | None = None,
        placeholder: bool | None = None,
        account_type: str | None = None,
    ) -> str:
        """Update an existing account's properties.

        Args:
            name: Account ref to update (full path e.g. "Expenses:Groceries", %short GUID, or full 32-char GUID)
            new_name: New name for the account (just the leaf name, not full path)
            description: New description
            placeholder: New placeholder status (true = container only)
            account_type: New account type (e.g., "CREDIT", "BANK"). Only changes
                within the same debit/credit polarity are allowed — e.g.,
                LIABILITY to CREDIT, ASSET to BANK. Cross-polarity changes
                (e.g., ASSET to LIABILITY) are blocked.
        """
        book = get_book()
        result = book.update_account(
            name=name,
            new_name=new_name,
            description=description,
            placeholder=placeholder,
            account_type=account_type,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="account")
    def move_account(name: str, new_parent: str) -> str:
        """Move an account to a new parent in the hierarchy.

        Args:
            name: Account ref to move (full path e.g. "Expenses:Old:Account", %short GUID, or full 32-char GUID)
            new_parent: New parent account ref (full path, %short GUID, or full 32-char GUID)
        """
        book = get_book()
        result = book.move_account(name=name, new_parent=new_parent)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="account")
    def delete_account(name: str) -> str:
        """Delete an account from the chart of accounts.

        Safeguards prevent deletion if the account has children or transactions.

        Args:
            name: Account ref to delete (full path, %short GUID, or full 32-char GUID)
        """
        book = get_book()
        result = book.delete_account(name=name)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="delete", entity_type="transaction")
    def delete_transaction(
        guid: TransactionGuid,
        force: bool = False,
    ) -> str:
        """Delete a transaction by GUID.

        Safeguards prevent deletion if the transaction has reconciled splits.
        Use force=true to override.

        Args:
            guid: Transaction GUID (32-character hex string, or 8+ char prefix)
            force: Allow deleting transactions with reconciled splits
        """
        book = get_book()
        result = book.delete_transaction(guid, force=force)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="transaction")
    def update_transaction(
        guid: TransactionGuid,
        description: str | None = None,
        transaction_date: str | None = None,
        splits: list[SplitInput] | None = None,
        notes: str | None = None,
        force: bool = False,
    ) -> str:
        """Update an existing transaction.

        Args:
            guid: Transaction GUID to update (32-character hex string, or 8+ char prefix)
            description: New transaction description (optional)
            transaction_date: New date in ISO format YYYY-MM-DD (optional)
            splits: List of split updates with 'account' and 'amount' (optional).
                    Must match existing splits by account name and balance to zero.
                    For cross-currency splits, include 'quantity' (amount in account's commodity).
                    Include 'memo' to set that split's memo (omit to leave it unchanged).
                    ``amount``/``quantity`` are decimal strings (e.g. "94.87").
            notes: New transaction notes (optional). Pass empty string to clear.
            force: Allow modifying transactions with reconciled splits
        """
        book = get_book()
        trans_date = _parse_iso_date(transaction_date)
        result = book.update_transaction(
            guid=guid,
            description=description,
            trans_date=trans_date,
            splits=_splits_to_dicts(splits),
            notes=notes,
            force=force,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="replace_splits", entity_type="transaction")
    def replace_splits(
        guid: TransactionGuid,
        splits: list[SplitInput],
        force: bool = False,
    ) -> str:
        """Replace all splits in a transaction with a new set.

        Replace all splits in a transaction with a completely new set.
        The transaction's currency, description, date, and notes are preserved.
        New splits must balance to zero.

        Args:
            guid: Transaction GUID (32-character hex string, or 8+ char prefix)
            splits: Complete new set of splits. Each split needs:
                - 'account' (required): Account ref — full path, %short GUID, or full 32-char GUID
                - 'amount' (required): Value in transaction currency, as a decimal string
                - 'quantity' (optional): Amount in account's commodity, as a decimal string.
                  Required if account commodity differs from transaction currency.
                - 'memo' (optional): Split memo
            force: Allow replacing reconciled splits or splits in lots
        """
        book = get_book()
        result = book.replace_splits(
            guid=guid,
            splits=_splits_to_dicts(splits),
            force=force,
        )
        return _json(result)
