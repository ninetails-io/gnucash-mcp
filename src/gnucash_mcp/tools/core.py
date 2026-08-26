"""Core tools: book summary, accounts, transactions, search.

Always registered — `_apply_module_filter` forces 'core' into the
enabled set. Kept in its own register() function for consistency
with the other modules so server.py can treat every module the same
way (pure lazy-load orchestration, no hardcoded imports).
"""

from datetime import date

from gnucash_mcp._format import (
    _batch_row_splits,
    _batch_tsv_layout,
    _parse_statement_tsv,
    _parse_update_tsv,
    _tsv_lines,
)
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

    The header row is load-bearing (see ``_batch_tsv_layout``): the
    documented base header parses as positional ``(amount, account)``
    pairs exactly as before; ``memo`` and/or ``qty`` split columns
    widen each split group accordingly (field order per the header's
    first group); a ``notes`` token in column 4 inserts a
    per-transaction notes column after ``description``. Unknown or
    typo'd column names reject with the offending name.

    Rows may be ragged (2 splits vs 3). Raises ValueError on a
    missing header, too-few columns, or a split count that doesn't
    match the declared shape.
    """
    lines = _tsv_lines(tsv, "the transactions TSV")
    if len(lines) < 2:
        raise ValueError(
            "transactions TSV needs a header row and at least one data row"
        )
    layout = _batch_tsv_layout(lines[0])
    group = layout["group"]
    fixed = layout["fixed"]
    out: list[dict] = []
    for i, ln in enumerate(lines[1:], start=1):
        fields = ln.split("\t")
        # Trailing empty cells carry nothing in any position —
        # stripping them makes "row ends after description" (the
        # auto-fill request) robust to stray trailing tabs.
        while fields and not fields[-1].strip():
            fields.pop()
        if len(fields) < 3:
            raise ValueError(
                f"row {i}: expected at least ref, date, description"
            )
        ref, dt, desc = fields[0].strip(), fields[1].strip(), fields[2]
        if not ref:
            raise ValueError(f"row {i}: empty ref (each row needs a key)")
        if len(fields) <= fixed:
            # No split cells at all: an auto-fill request — the book
            # layer reproduces the most recent matching-description
            # transaction (create_transaction's omitted-splits
            # contract), or rejects the row when nothing matches.
            splits: list[dict] = []
        else:
            try:
                splits = _batch_row_splits(fields[fixed:], group)
            except ValueError as e:
                raise ValueError(f"row {i} (ref {ref!r}): {e}")
        txn = {
            "ref": ref,
            "date": _parse_iso_date(dt) or date.today(),
            "description": desc,
            "splits": splits,
        }
        ni = layout["notes_idx"]
        if ni is not None and len(fields) > ni and fields[ni].strip():
            txn["notes"] = fields[ni].strip()
        ci = layout["cur_idx"]
        if ci is not None and len(fields) > ci and fields[ci].strip():
            txn["currency"] = fields[ci].strip().upper()
        out.append(txn)
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
        query: str | None = None,
    ) -> str:
        """List all accounts in the GnuCash chart of accounts.

        Leads with a ``Showing X-Y of Z accounts`` line, then a compact
        one-line-per-account format by default. Page with ``offset``;
        ``limit=0`` returns the count only. Use verbose=true for full
        JSON with guid, type, commodity, etc.

        To FIND an account without paging the whole chart, pass
        ``query`` — a case-insensitive substring matched against each
        account's full path and description (e.g. query="grocer" or
        query="4930" on a numbered chart). Results emit %short GUIDs
        that every account-taking tool accepts. For searching
        transactions by text or amount, use ``search_transactions``.

        Args:
            root: Filter to a subtree (e.g., "Expenses" for expense accounts only).
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
            limit: Page size (default 50, max 250). 0 = count only.
            offset: 0-indexed first row to return (default 0).
            query: Case-insensitive substring filter on account
                path/description. Combines with ``root``.
        """
        book = get_book()
        result = book.list_accounts(
            root=root, compact=not verbose, limit=limit, offset=offset,
            query=query,
        )
        if verbose:
            return _json(result)
        return result

    @mcp.tool()
    @safe_tool
    @audit_log(classification="read")
    def get_account(name: str) -> str:
        """Get details for one account: type, commodity, description,
        placeholder flag, GUID, and hierarchy position.

        Read-only. Returns ``{"error": "Account not found: ..."}``
        when the ref matches nothing — nothing raises. Use
        list_accounts to discover refs in bulk, get_balance when you
        only need a number, get_account_slots for custom metadata
        (APR, credit_limit, ...).

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
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
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
        ``memo`` (optional), ``action`` (optional). ``amount`` and
        ``quantity`` are decimal strings (e.g. "94.87") — never raw
        JSON numbers, which would lose precision on non-dyadic
        decimals.

        FIELD TARGETING — the annotation fields, one job each, in
        GnuCash-register visibility order:

        - ``description``: the clean name ("Chevron 0090706
          Portland"). Always visible.
        - ``notes``: what the purchase WAS, when the description
          alone doesn't say ("Fuel, road trip to Portland").
          Visible in the register's double-line view — this is the
          annotation humans read. Interpret; don't transcribe.
        - split ``memo`` (bank/card leg): the RAW statement line as
          provenance ("Withdrawal ACH TRAVELERS TYPE: PER INSUR…").
          Visible only in expanded split view — evidence, not
          narrative.
        - split ``action``: the typed KIND of movement, one word.
          Matters most on investment legs, where desktop convention
          (and the Advanced Portfolio report) expects "Buy" /
          "Sell" / "Dividend"; bank legs may use "Wire" / "ATM" /
          "Interest". Skip it for ordinary spending.

        When duplicate detection surfaces candidates (either rejecting
        the write with ``status: "rejected"`` or returning alongside a
        successful create), ``duplicates`` in the response is a
        newline-separated TSV string, not a list of dicts. Columns::

            confidence<TAB>guid<TAB>date<TAB>amount<TAB>cur<TAB>description<TAB>signals

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
            notes: What the purchase was (see FIELD TARGETING above).
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
        row per transaction. The HEADER DECLARES THE LAYOUT. Base form:
        splits are ``(amount, account)`` column PAIRS, repeated as wide
        as a transaction needs::

            ref<TAB>date<TAB>description<TAB>amt1<TAB>acct1<TAB>amt2<TAB>acct2...
            1<TAB>2026-05-21<TAB>Gas<TAB>-54.19<TAB>Assets:Checking<TAB>54.19<TAB>Expenses:Auto:Fuel

        Two opt-in extensions, each activated by naming it in the
        header (legacy headers parse exactly as before):

        - PER-SPLIT MEMOS — declare ``memo`` split columns; splits
          become ``(amount, account, memo)`` TRIPLES::

              ref<TAB>date<TAB>description<TAB>amt1<TAB>acct1<TAB>memo1<TAB>amt2<TAB>acct2<TAB>memo2
              1<TAB>2026-05-21<TAB>Gas<TAB>-54.19<TAB>Assets:Checking<TAB>card #4471<TAB>54.19<TAB>Expenses:Auto:Fuel

          Empty memo cells mid-row keep their tabs; a row may simply
          END once its last split's amount and account are present
          (trailing memo/qty cells are read as empty — no
          placeholder tabs needed, as above).
        - PER-TRANSACTION NOTES — declare a ``notes`` column directly
          after ``description``::

              ref<TAB>date<TAB>description<TAB>notes<TAB>amt1<TAB>acct1...

          FIELD TARGETING for statement entry: ``description`` is
          the clean name; ``notes`` is what the purchase WAS —
          interpreted, not transcribed — and is what humans see in
          GnuCash's double-line register; the bank leg's ``memo``
          is where the RAW statement line goes (provenance, visible
          only in expanded split view). Prefer filling ``notes``
          whenever the description alone doesn't tell the story.

        - PER-TRANSACTION CURRENCY — declare a ``cur`` column after
          ``description`` (before or after ``notes``); an ISO code
          cell sets THAT ROW's transaction currency, an empty cell
          keeps the book default::

              ref<TAB>date<TAB>description<TAB>cur<TAB>amt1<TAB>acct1<TAB>amt2<TAB>acct2
              1<TAB>2026-07-15<TAB>USD Card Payment<TAB>USD<TAB>-500<TAB>Assets:USD Checking<TAB>500<TAB>Liabilities:USD Card

          With ``cur``, the row's ``amt`` cells are in that
          currency and must balance in it. Use it when NO leg is in
          the book's default currency (a USD-to-USD transfer inside
          a CNY book needs no invented CNY values and no qty).
          Splits on accounts of any OTHER commodity still need
          ``qty``. The currency must already exist in the book, and
          ``cur`` cannot combine with an auto-fill row.

        - PER-SPLIT QUANTITY — declare ``qty`` split columns for
          splits whose ACCOUNT commodity differs from the book
          default (investment shares, foreign-currency accounts)::

              ref<TAB>date<TAB>description<TAB>amt1<TAB>acct1<TAB>qty1<TAB>amt2<TAB>acct2<TAB>qty2
              1<TAB>2026-07-01<TAB>VFIFX Purchase<TAB>-505.17<TAB>Assets:Checking<TAB><TAB>505.17<TAB>Assets:401k:VFIFX<TAB>7.7936

          ``amount`` stays in the book's default currency (the
          transaction currency — batch never changes that); ``qty``
          is the amount in the account's own commodity. An EMPTY qty
          cell means the account uses the default currency
          (quantity == amount). A non-default-commodity account with
          an empty qty rejects that row.

        - PER-SPLIT ACTION — declare ``act`` split columns for
          GnuCash's typed movement tag ("Buy"/"Sell"/"Dividend" on
          investment legs — desktop convention; "Wire"/"ATM" on
          bank legs). Same group mechanics as ``memo``/``qty``;
          empty cells skip it. Rarely needed for plain spending.

        All extensions combine; when several split fields are
        declared, the header's FIRST group fixes their order (e.g.
        ``amt, acct, memo, qty``).

        AUTO-FILL — a row with NO split cells at all (ends right
        after ``description``/``notes``) reproduces the most recent
        transaction with the same description — splits, memos, and
        quantities included — exactly like calling
        ``create_transaction`` without ``splits``::

            1<TAB>2026-07-01<TAB>Rent
            2<TAB>2026-07-01<TAB>Netflix

        Auto-filled rows are marked ``auto_filled_from:<guid>`` in
        the results ``reason`` column; a row whose description
        matches nothing rejects ("no matching transaction to
        auto-fill from"). Use ``dry_run=true`` to preview what a
        batch of auto-fills would book. Perfect for recurring
        monthly entries. Transaction ``notes`` are NOT copied from
        the source (notes are often time-bound — "first
        appearance, investigate" must not replicate); supply a
        notes cell when the new instance needs one.

        - ``ref``: YOUR correlation key per row (e.g. 1, 2, 3), unique
          within the batch. It is echoed back so you can match results
          to what you sent; the server never reuses or interprets it.
        - ``date``: ISO YYYY-MM-DD. ``amount``/``qty``: decimal
          STRINGS (never raw JSON numbers). Each transaction needs
          >=2 splits balancing to zero in the default currency. Rows
          may differ in width (2 splits vs 3).
        - The transaction currency is always the book default — for
          a transaction denominated in another currency, use
          ``create_transaction`` with its ``currency`` parameter.

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
          max_confidence, reason``. status is ``created`` |
          ``rejected`` | ``would_create`` (dry_run, candidate-free
          rows only) | ``review_required`` (dry_run rows with >=1
          duplicate candidate — rule each against the duplicates
          table before committing); reason is a code like
          ``duplicate_detected`` or the validation message.
          ``max_confidence`` (HIGH/MEDIUM/blank) is the row's top
          duplicate candidate — enough for the common keep/drop
          call without the join.
        - ``duplicates`` (only when matches exist): SELF-CONTAINED
          comparison rows, sorted strongest-correspondence first —
          ``ref, candidate_guid, confidence, state, date_new,
          date_old, date_delta_days, amt_new, amt_old, amt_delta,
          cur, desc_new, desc_old, notes_old, memo_old, cat_new,
          cat_old, split_match, signals``. ``_new`` = your proposed
          row, ``_old`` = the existing transaction; ``cat_*`` are
          the category (non-payment) legs as
          ``account=amount|...``; ``split_match``
          (exact/partial/none) compares them — MEDIUM on
          date+amount but ``none`` on category is usually a
          distinct purchase. Amounts are SIGNED (direction
          matters: a deposit is not a payment's twin).
          ``amt_delta`` is blank on cross-currency candidates
          (``cur`` names the candidate's currency exactly when
          the frames differ); ``memo_old`` and ``state`` blanks
          mean this surface can't fill them. Never re-read your
          own input — both sides are in the row.
          Σ(dup_count) equals the duplicates row count.
        - Dry runs additionally lead with ``summary`` (would-create/
          review-required/rejected counts + the homework line)
          and close with ``effects`` — the projected per-account
          balance deltas of the rows that would land.

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
    @audit_log(
        classification="write", operation="enter",
        entity_type="statement",
    )
    def enter_statement(
        account: str,
        statement_date: str,
        opening_balance: str,
        closing_balance: str,
        lines: str,
        dry_run: bool = True,
        force_base: bool = False,
        force_duplicates: bool = False,
        show_all: bool = False,
    ) -> str:
        """Enter a COMPLETE bank/card statement in one atomic call:
        create the new lines, claim the ones already in the book,
        and reconcile everything against the closing balance — all
        in one save, or nothing at all.

        THE WORKFLOW (two calls around your judgment):

        1. ``dry_run=true`` (the DEFAULT) — transcribe the statement
           and get back a classification of every line: NEW (not in
           the book), MATCH (an existing unreconciled split
           corresponds), OVERLAP (already reconciled), AMBIGUOUS
           (several candidates). MATCH/AMBIGUOUS rows come with the
           candidate's full annotation (date, amount, description,
           notes, memo, short GUID) so you can adjudicate each one.
        2. Rule every MATCH/AMBIGUOUS row yourself, adapt
           annotations, confirm with the user.
        3. ``dry_run=false`` — NEW rows now carry interpreted
           description/notes and counter-splits; MATCH rows carry
           ``match=<split guid>`` claims. The server enters, claims,
           reconciles every statement-touched split at
           ``statement_date``, and saves once.

        TRANSCRIBE, DON'T INTERPRET (dry-run): amounts and balances
        go in EXACTLY as the statement prints them — for credit
        cards too (charges positive, balance as amount owed). The
        server applies the sign convention from the account's type;
        you never flip a sign. The gate
        ``opening + sum(lines) == closing`` must hold or the call
        rejects: transcribe every line.

        INPUT — ``lines`` is a TSV block. Header: ``ref, date``
        first, then any order of ``description``, ``notes``,
        ``raw``, ``match``, ``amount`` (required), then optional
        ``amt, acct, memo, qty`` counter-split groups (batch
        grammar). The statement account's own leg is SYNTHESIZED —
        never a column. Dry-run typically needs only::

            ref<TAB>date<TAB>raw<TAB>amount
            1<TAB>2026-07-03<TAB>POS DEBIT WHOLEFDS #123<TAB>-87.12

        - ``raw`` = the verbatim statement line; it lands on the
          bank leg's memo (provenance). ``description``/``notes``
          are your interpretation (commit).
        - ``match`` = the split GUID this line claims instead of
          creating (from the dry-run candidates table). Claim rows
          may also carry ``raw`` (updates the claimed split's memo)
          and ``notes`` (updates the transaction's notes), and END
          at their last fixed column — they take no split cells.
          The claimed amount must equal the line amount exactly —
          fix the book first if they disagree.
        - A commit row with no counter-splits auto-fills from the
          most recent same-description 2-split transaction, adapted
          to the line amount (marked ``auto_filled_from:<guid>``).
          The precedent must have exactly one leg on the statement
          account and no cross-commodity leg — anything else
          rejects with "supply explicit counter-splits". Explicit
          counter-splits must not name the statement account (its
          leg is synthesized).

        SAFETY: the account's reconciled balance must tie to
        ``opening_balance`` (a prior unentered statement blocks
        commit), every created-vs-existing exact overlap must be
        explicitly claimed or forced, and the projected closing tie
        is verified BEFORE anything is written. The two force
        flags are INDEPENDENT: ``force_base=true`` lands onto an
        untied opening base (the consequent tie discrepancy is
        recorded, and duplicate detection STAYS ON);
        ``force_duplicates=true`` creates past exact twins you
        have adjudicated as distinct. Neither bypasses the
        statement's own self-check. After the save, the reconciled
        balance is read back and verified against the tie.

        OUTPUT (dry-run): ``summary`` (class counts), ``lines``
        (ref, class, cands, note — the note is the resolved
        disposition: the guard's refusal coaching verbatim, the
        auto-fill prediction, or "will claim …"), ``candidates`` —
        SELF-CONTAINED comparison rows sorted
        strongest-correspondence first (``ref, candidate_guid,
        confidence, state, date_new/old + delta, amt_new/old +
        delta, cur, desc_new/old, notes_old, memo_old, cat_new/old,
        split_match, signals``; ``_new`` = the statement line in
        book convention, ``_old`` = the existing split — never
        re-read your own input; ``cur`` is structurally blank on
        this surface), plus ``warnings`` (only when present;
        ``candidates`` likewise) and ``tie`` — the projected
        reconciled balance vs the closing, with a count of rows
        this exact payload would refuse at commit. The dry-run
        rehearses the SAME disposition procedure commit runs —
        force included. The tie is the only verdict;
        MATCH/AMBIGUOUS rows are yours to rule.
        OUTPUT (commit): ``results`` (``ref, status, guid, note``;
        status is created | claimed | skipped_duplicate, or on a
        refused statement rejected | statement_aborted — the note
        column carries the row's coaching and
        ``auto_filled_from:<guid>`` markers), plus, on success
        only, the new reconciled balance and the tie (a refusal
        returns just ``summary`` + ``results``).

        Args:
            account: Statement account ref (path, %short, or GUID).
                BANK/CASH/ASSET/CREDIT/LIABILITY only.
            statement_date: The statement's closing date
                (YYYY-MM-DD); every touched split reconciles at it.
            opening_balance: Opening balance, exactly as printed.
            closing_balance: Closing balance, exactly as printed.
            lines: The TSV block described above.
            dry_run: DEFAULT TRUE — the rehearsal is the workflow.
            force_base: Land onto an untied opening base; the tie
                discrepancy is recorded, twin detection stays on.
            force_duplicates: Create past exact unclaimed twins
                (you adjudicated them as distinct charges).
            show_all: Dry-run only. Lines with MEDIUM/HIGH
                candidates suppress their LOW amount-coincidences
                (the cands column notes "+N LOW suppressed");
                show_all=true lists everything.
        """
        book = get_book()
        stmt_date = _parse_iso_date(statement_date)
        if stmt_date is None:
            raise ValueError(
                f"statement_date {statement_date!r} is not a valid "
                f"YYYY-MM-DD date"
            )
        parsed = _parse_statement_tsv(lines)
        for row in parsed:
            d = _parse_iso_date(row["date"])
            if d is None:
                raise ValueError(
                    f"line {row['ref']}: date {row['date']!r} is "
                    f"not a valid YYYY-MM-DD date"
                )
            row["date"] = d
        result = book.enter_statement(
            account, stmt_date, opening_balance, closing_balance,
            parsed, dry_run=dry_run, force_base=force_base,
            force_duplicates=force_duplicates, show_all=show_all,
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
            verbose: If false (default), compact text output — optimized
                for reading and token efficiency. If true, structured
                JSON, for when you need machine-readable fields rather
                than a report.
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
        notes: str = "",
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
            notes: Optional free-text notes (max 4096 bytes). Shows in
                GnuCash desktop's account editor Notes field.
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
            notes=notes,
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
        notes: str | None = None,
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
            notes: New notes (max 4096 bytes; shared with GnuCash
                desktop's Notes field). Pass "" to clear.
        """
        book = get_book()
        result = book.update_account(
            name=name,
            new_name=new_name,
            description=description,
            placeholder=placeholder,
            account_type=account_type,
            notes=notes,
        )
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="account")
    def move_account(name: str, new_parent: str) -> str:
        """Move an account — children and balances ride along — under
        a new parent in the hierarchy.

        No transaction data changes: only the account's position
        (and therefore every descendant's full path) is rewritten.
        Errors, changing nothing, if either ref matches no account,
        if the move would create a cycle (new parent is the account
        itself or one of its descendants), or if the new parent
        already has a child of the same name. %short GUIDs survive
        the move; saved full paths do not. Use update_account to
        rename in place instead of moving.

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
        guid: TransactionGuid | list[TransactionGuid],
        force: bool = False,
    ) -> str:
        """Delete one transaction by GUID — or several in one call.

        Safeguards prevent deletion if a transaction has reconciled
        splits (force=true overrides) or is an invoice's posting
        record (unpost_invoice first).

        Pass a LIST of GUIDs to delete several in one book open /
        one save. The batch is all-or-nothing: every guid is
        validated before anything is deleted, so a bad guid rejects
        the whole call with nothing removed. Response for a list is
        ``{status, count, transactions: [{guid, description}]}``;
        a single guid returns the single-object shape as before.

        Args:
            guid: Transaction GUID (32-char hex or 8+ char prefix),
                or a list of them.
            force: Allow deleting transactions with reconciled splits.
        """
        book = get_book()
        if isinstance(guid, list):
            result = book.delete_transactions(guid, force=force)
        else:
            result = book.delete_transaction(guid, force=force)
        return _json(result)

    @mcp.tool()
    @safe_tool
    @audit_log(classification="write", operation="update", entity_type="transaction")
    def update_transaction(
        guid: TransactionGuid | list[TransactionGuid],
        description: str | None = None,
        transaction_date: str | None = None,
        splits: list[SplitInput] | None = None,
        notes: str | None = None,
        force: bool = False,
    ) -> str:
        """Update an existing transaction — or broadcast to several.

        Pass a LIST of GUIDs to apply the SAME supplied values to
        every listed transaction in one book open / one save
        (all-or-nothing) — the batch-annotation case: one note
        across 35 related entries, one call. ``splits`` stays
        single-transaction. For per-row DIFFERENT values, use
        ``update_transactions``.

        Args:
            guid: Transaction GUID (32-character hex string, or 8+ char prefix), or a list of them
            description: New transaction description (optional)
            transaction_date: New date in ISO format YYYY-MM-DD (optional)
            splits: List of split updates with 'account' and 'amount' (optional).
                    Must match existing splits by account name and balance to zero.
                    For cross-currency splits, include 'quantity' (amount in account's commodity).
                    Include 'memo' to set that split's memo (omit to leave it unchanged).
                    ``amount``/``quantity`` are decimal strings (e.g. "94.87").
            notes: New transaction notes (optional). Pass empty string to clear.
            force: Allow modifying transactions with reconciled splits —
                required for split changes AND for moving the date of a
                transaction with reconciled splits (a date move shifts
                it out of its reconciled statement period).
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
    @audit_log(
        classification="write", operation="update_batch",
        entity_type="transaction",
    )
    def update_transactions(
        updates: str,
        on_error: str = "abort",
        force: bool = False,
    ) -> str:
        """Update MANY transactions with per-row values (bulk edit).

        INPUT — ``updates`` is a TSV block: header ``guid`` plus any
        of ``description``, ``notes``, ``date`` (at least one), then
        one row per transaction::

            guid<TAB>description<TAB>notes
            56926ac2<TAB>PayPal Credit Payment<TAB>Resolved — card payment
            7f0fc117<TAB><TAB>Netflix subscription, $22.10/mo

        An EMPTY cell leaves that field UNCHANGED — this batch can
        annotate but never clear (clearing is ``update_transaction``
        with ``notes=""``, deliberately single-transaction). Splits
        and memos are not updatable here (``replace_splits``).

        One book open, one save; ``on_error="abort"`` (default)
        sinks the batch on any bad row, ``"skip"`` keeps good rows.
        Date moves on transactions with reconciled splits are
        rejected per row unless ``force=true`` (they shift the
        transaction out of its reconciled statement period).
        Returns a results TSV keyed by your input guids. For the
        SAME value across many transactions, ``update_transaction``
        with a guid list is cheaper than repeating rows.
        """
        book = get_book()
        rows = _parse_update_tsv(updates)
        for r in rows:
            if "date" in r:
                r["date"] = date.fromisoformat(r["date"])
        result = book.update_transactions(
            updates=rows, on_error=on_error, force=force,
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

        A new split that reproduces an existing one (same account,
        amount, and quantity) is an UNCHANGED leg: it keeps the old
        split's memo (supply a memo to override) and its reconcile
        state. So recategorizing the expense leg of a reconciled
        bank transaction is safe — resubmit the bank leg as-is and
        only the changed leg resets.

        Args:
            guid: Transaction GUID (32-character hex string, or 8+ char prefix)
            splits: Complete new set of splits. Each split needs:
                - 'account' (required): Account ref — full path, %short GUID, or full 32-char GUID
                - 'amount' (required): Value in transaction currency, as a decimal string
                - 'quantity' (optional): Amount in account's commodity, as a decimal string.
                  Required if account commodity differs from transaction currency.
                - 'memo' (optional): Split memo
            force: Required only when the replacement would CHANGE a
                reconciled split (or remove splits from lots) —
                unchanged reconciled legs are preserved without it.
        """
        book = get_book()
        result = book.replace_splits(
            guid=guid,
            splits=_splits_to_dicts(splits),
            force=force,
        )
        return _json(result)
