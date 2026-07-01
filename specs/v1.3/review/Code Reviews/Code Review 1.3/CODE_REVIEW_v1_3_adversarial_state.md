# Adversarial Review — State, Concurrency, Data Integrity (v1.3)

Audit pass against the threading-local audit-staging pattern, the
`_verify_write` invariant, the voided-split "zombie" claim, the lock
retry path, and the composite-write atomicity model. Reviewer
declined to trust predecessor letters; findings are anchored to code.

Scope:

- `src/gnucash_mcp/book/_base.py`
- `src/gnucash_mcp/book/core.py`
- `src/gnucash_mcp/book/reconciliation.py`
- `src/gnucash_mcp/book/investments.py`
- `src/gnucash_mcp/book/business.py`
- `src/gnucash_mcp/book/scheduling.py`
- `src/gnucash_mcp/book/backup.py`
- `src/gnucash_mcp/book/admin.py`
- `src/gnucash_mcp/logging_config.py`

---

## Findings — Severity ordered

### F1 — CONFIRMED — Scheduled-transaction instantiation is non-atomic

**Where:** `book/scheduling.py:599-707` (`create_transaction_from_scheduled`)

```python
# Inside `with self.open(readonly=False) as book:`
sx.last_occur = max(last, txn_date) if last else txn_date
sx.instance_count += 1
...
book.save()      # ← session 1 commits here

# Create the actual transaction (separate session)
txn_result = self.create_transaction(
    description=sx_name,
    splits=splits,
    trans_date=txn_date,
)
```

**Scenario.** The first session commits the schedule's bookkeeping
(`last_occur` advanced, `instance_count + 1`). The book context
exits. `create_transaction` then opens a second session for the
actual posting. If that second open fails (lock contention,
disk-full, ValueError from balance check on a tampered template,
duplicate-detect rejecting with HIGH match), the schedule has
*already* advanced. The user lost an occurrence — next call says
"transaction date is not after last_occur" until they manually
rewind. The user has no audit trail of the failed sibling write.

The docstring acknowledges the two-session split as a
"note" but does not flag the loss-on-failure shape.

**Tell:** The "never rewind last_occur" comment (line 684) shows the
author was thinking about ordering — but treats forward-only
advance as the safety property. The real safety property
("advance only if the paired transaction lands") needs both
operations in the same session, or a rollback hook on the second.

**Classify:** CONFIRMED data-integrity gap.

**Fix sketch.** Inline `create_transaction`'s body (or pass the
already-open session through) so both writes share one
`book.save()`. Alternative: stage a "pending-advance" marker, only
flip `last_occur` after the second save returns. The shared-session
fix is cleaner — `create_transaction` already accepts the same
inputs the inline path would build.

**Test sketch.** Patch `self.create_transaction` to raise
`RuntimeError` mid-call; assert `sx.last_occur` / `sx.instance_count`
are unchanged after the catch.

---

### F2 — CONFIRMED — `get_unreconciled_splits` includes voided splits as "uncleared"

**Where:** `book/reconciliation.py:181-195`

```python
# Only include non-reconciled splits (n or c, not y)
if split.reconcile_state != "y":
    split_dict = {...}
    all_unreconciled.append(split_dict)

    if split.reconcile_state == "c":
        cleared_total += split.quantity
    else:
        uncleared_total += split.quantity
```

**Scenario.** Voided splits have `reconcile_state == 'v'` and
`value == 0` / `quantity == 0` per the void path. The filter at
line 181 (`!= "y"`) admits them. They land in the `else` branch
(line 194) and are counted as "uncleared." With value/quantity
zeroed by void this is *numerically* harmless (adds zero to
`uncleared_total`), but it:

1. Inflates the split count surfaced via the `count` field and the
   compact-line listing.
2. Populates the truncation notice with phantom rows.
3. Makes voided zombies addressable through the unreconciled-list
   workflow — a user resolving a reconciliation backlog sees a
   voided split listed and can call `set_reconcile_state(v_guid,
   'y')` against it, which lands (no guard, see F4).

**Tell:** The dual-class behavior of `'v'` (semantically deleted
financial event vs. preserved audit row) is exactly what the
filter at line 181 needs to know about. The check is one
character short.

**Classify:** CONFIRMED — semantic/display correctness issue;
zero risk of dollar-amount divergence on the totals, but real
risk on split-count display and on the addressability of zombies.

**Fix sketch.** Either filter `s.reconcile_state in ("n", "c")`
or `s.reconcile_state != "y" and s.value != 0`. The latter is
defense-in-depth against the partial-void shape described in F3.

**Test sketch.** Add `multi_currency_book` fixture variation where
one split is voided; assert `count == total - 1`.

---

### F3 — NEEDS_VERIFICATION — Voided-split semantics: code assumes `state == 'v'` implies `value == 0` without checking

**Where:** Multiple sites — most clearly `book/business.py:1738-1761`
(`_calculate_lot_balance`) and `book/investments.py:912` (lot-gain
voided-count). Both branches on `reconcile_state == 'v'` and never
test that `value == 0` / `quantity == 0`.

```python
# _calculate_lot_balance
for split in lot.splits:
    if split.reconcile_state == "v":
        continue
    total += Decimal(str(split.value))
```

**Scenario.** `void_transaction` sets the slot data, zeroes
`value` and `quantity`, then sets `reconcile_state = 'v'`, then
calls `book.save()` (lines 514-523 in `reconciliation.py`). If
`book.save()` raises after the in-memory mutations but before
SQLite commit:

- SQLAlchemy session rollback should revert all three changes.
  This is the happy path.

But: the void mutation crosses *two* invariants (zeroed value AND
reconcile-state v). The code everywhere assumes they move
together. If a future change skips the value-zero step (e.g., a
"soft-void that preserves balance for partial-void semantics" gets
added) or a manual DB edit creates the partial shape, every
downstream consumer:

- `_calculate_lot_balance` (skips → balance undercounts)
- `_lot_decimals` (no `'v'` filter at all — see F6)
- `unpost_invoice`'s "are there real payments on this lot?" check
- `get_book_summary` reconciliation-backlog count (no filter)

… disagree silently. This is a hidden coupling — both the predecessor
note and the comments above each filter site reinforce "voided = v
+ zeroed", but no code asserts it. A single forgotten zero
propagates as silent wrong-balances.

**Classify:** NEEDS_VERIFICATION (today the invariant holds because
all void paths set both; the risk is future writers and
hand-edited books). Flag as **a class of bug** rather than a
specific occurrence.

**Fix sketch.** Add a `_is_voided(split)` helper in `_base.py`
(`return split.reconcile_state == "v"`) and a paired
`_is_consistent_void(split)` assertion (`raise if state == 'v' and
value != 0`). Use the assertion at the top of
`_calculate_lot_balance`, `_lot_decimals`, and similar hot paths
to fail loud on partial-void corruption rather than computing a
silently-wrong total.

**Test sketch.** Construct a book with a hand-crafted "partial void"
(state='v', value!=0); assert every reporting tool either skips it
or raises a clear error — current behavior summarizes wrong.

---

### F4 — CONFIRMED — `set_reconcile_state` can un-void a split silently

**Where:** `book/reconciliation.py:52-101`

```python
VALID_RECONCILE_STATES = {"n", "c", "y"}  # new, cleared, reconciled
...
if state not in self.VALID_RECONCILE_STATES:
    raise ValueError(...)

with self.open(readonly=False) as book:
    split = self._find_split(book, split_guid)
    ...
    split.reconcile_state = state
```

**Scenario.** The validator allowlists `'n' | 'c' | 'y'` for inputs
— rejecting user attempts to set `'v'`. But it has no check on the
CURRENT state. If `split.reconcile_state == 'v'` (voided), a call
to `set_reconcile_state(guid, 'y', reconcile_date=...)` succeeds:

- The split's value is still 0.
- The split is now marked `'y'` (reconciled).
- The void-former-value / void-former-quantity slots stay.

The LLM (or an LLM-assisted bookkeeper) now has a "reconciled
$0" split on the account. `unvoid_transaction` (line 563) checks
`if not any(s.reconcile_state == "v" ...)`: this split no longer
appears voided, so unvoid REFUSES "Transaction {guid} is not
voided" even though the slots are still there. The user must
either manually edit slots or rebuild the transaction.

F2 makes this discoverable (`get_unreconciled_splits` surfaces
voided splits as uncleared, inviting reconciliation). F4 is the
follow-on bug.

**Classify:** CONFIRMED state-corruption gap. Discoverability
requires F2 first.

**Fix sketch.** Two options:

1. Reject when `split.reconcile_state == 'v'` with "split is voided
   — unvoid the transaction before reconciling."
2. Auto-unvoid (assert the slots match, restore them, then set the
   target state). Riskier; surprising.

Recommend option 1, paired with F2's filter fix.

**Test sketch.** Void a transaction; call `set_reconcile_state(s,
'y')`; assert it raises rather than landing.

---

### F5 — CONFIRMED — Lock retry only handles `sqlite3.OperationalError`, not piecash's `GnucashException`

**Where:** `book/_base.py:923-972` (`open()` context manager)

```python
except sqlite3.OperationalError as e:
    last_error = e
    error_msg = str(e).lower()
    if "locked" in error_msg or "busy" in error_msg:
        if attempt < max_retries - 1:
            time.sleep(retry_delay * (attempt + 1))
            continue
        raise GnuCashLockError(...) from e
    raise
```

**Scenario.** GnuCash uses an in-database `gnclock` table row to
mark "this book is open in the GUI." piecash checks this row on
`open_book` and raises `piecash.business.exceptions.GnucashException`
(or similar) with "Lock on the file" — NOT a `sqlite3.OperationalError`.
The try block's `except` doesn't match; the exception propagates
unchanged.

Implications:

- The friendly `GnuCashLockError` message ("Close GnuCash and try
  again") never fires when the lock is the `gnclock` row.
- The retry logic doesn't engage. A user who just closed GnuCash
  but whose `gnclock` row took 100ms to clean up sees an
  immediate piecash failure instead of a 0.5s-1.5s retried success.
- A stale `gnclock` row (held PID dead) needs manual `DELETE FROM
  gnclock` — the current open path gives no hint about this.

**Classify:** CONFIRMED user-experience gap. Not data corruption,
but does turn a recoverable lock into a hard failure.

**Fix sketch.** Catch `piecash.GnucashException` (or its lock-specific
subclass) alongside `sqlite3.OperationalError`; add a stale-lock
detection — if the lock has been held more than (say) 60 seconds
and the holding PID is not running, surface that in the error
message with the SQL hint. Document the hint in
`docs/RESTORE_FROM_BACKUP.md`.

**Test sketch.** Create a synthetic book, INSERT a fake gnclock row,
attempt `self.open()`, assert the user-visible error names the
lock table and suggests inspection.

---

### F6 — CONFIRMED — `_lot_decimals` doesn't filter voided splits, leading to silently-wrong lot summaries

**Where:** `book/investments.py:547-596`

```python
for split in lot.splits:
    if split.quantity > 0:
        purchase_quantity += Decimal(str(split.quantity))
        purchase_value += Decimal(str(split.value))
    else:
        sale_quantity += abs(Decimal(str(split.quantity)))
```

**Scenario.** Consider a lot with one buy (qty=100, value=$1000)
and one sale (qty=-30, value=-$300). Now the user voids the buy
transaction. After void: buy split has qty=0/value=0, sale split
unchanged.

`_lot_decimals` runs:

- Buy split: `quantity > 0` is False; falls to `else`,
  `sale_quantity += abs(0) = 0`. No effect.
- Sale split: `quantity > 0` is False; `else`,
  `sale_quantity += 30 = 30`.

`purchase_quantity = 0`, `sale_quantity = 30`,
`remaining = 0 - 30 = -30`. Negative.

Downstream:

- `_lot_summary` reports `quantity: "-30.0000"` — visible nonsense.
- `assign_split_to_lot` auto-close (line 858) checks
  `Decimal(summary["quantity"]) == 0`; -30 ≠ 0, so the lot stays
  OPEN despite being numerically dead.
- `calculate_lot_gain` returns at line 906 with the friendly
  "lot has no remaining shares — voided splits..." message,
  because the check is `remaining <= 0`. That path works (good).
  But the lot remains open in `list_lots`.

`assign_split_to_lot` (line 807) also doesn't reject voided splits
as input — F7 below.

**Classify:** CONFIRMED. The error message in `calculate_lot_gain`
shows the author was aware of voided-split-in-lot states; the fix
landed for one consumer but not for `_lot_decimals` itself.

**Fix sketch.** Add an early `if split.reconcile_state == "v":
continue` at the top of the `for split in lot.splits` loop.
Document the invariant: "the lot's decimal state ignores voided
splits — paired with `void_transaction` semantics."

**Test sketch.** Build a lot with buy+sale; void the buy; assert
`_lot_summary` returns `quantity == 0` and `cost_basis == 0`.

---

### F7 — CONFIRMED — `assign_split_to_lot` doesn't reject voided splits

**Where:** `book/investments.py:807-872`

```python
with self.open(readonly=False) as book:
    split = self._find_split(book, split_guid)
    if not split:
        raise ValueError(f"Split not found: {split_guid}")

    lot = self._find_lot(book, lot_guid)
    ...
    if lot.is_closed:
        raise ValueError("Cannot assign split to a closed lot")

    if split.account != lot.account:
        ...
    if split.lot is not None:
        ...

    split.lot = lot
    book.save()
```

**Scenario.** No check on `split.reconcile_state == 'v'`. A voided
split (zeroed value and quantity) can be assigned to a lot. The
assignment itself is geometrically meaningless — the split
contributes nothing to the lot's totals — but it creates a
real DB relationship row.

`auto_closed` (line 858) inspects `summary["quantity"]`: with
the voided zero contributing nothing, the lot's existing state
determines closure. If the lot previously had `remaining == 0`,
this voided-split assignment auto-closes the lot (one zero
doesn't move it, but the surrounding logic doesn't notice the
assignment is moot).

Combined with F6: a voided split assigned to a lot is now a real
relationship row in `lot.splits` that `_lot_decimals` will
misinterpret in any future call.

**Classify:** CONFIRMED. Belt-and-suspenders item — F6's fix
mitigates the downstream impact; F7 is the entrance check that
prevents the input from ever entering this regime.

**Fix sketch.** After resolving `split`, add:

```python
if split.reconcile_state == "v":
    raise ValueError(
        f"Split is voided (reconcile_state='v'); voided splits "
        f"do not affect lot accounting. Unvoid the transaction "
        f"or assign a non-voided split."
    )
```

---

### F8 — CONFIRMED — `_resolve_account` via `%short` or full GUID bypasses template-account filter

**Where:** `book/_base.py:1134-1179`

```python
def _resolve_account(self, book, ref):
    if ref.startswith(self._SHORT_ACCOUNT_GUID_PREFIX):
        ...
        return book.session.query(Account).filter_by(guid=full_guid).first()

    if len(ref) == 32 and _HEX_GUID_RE.fullmatch(ref):
        return book.session.query(Account).filter_by(guid=ref.lower()).first()

    return self._find_account(book, ref)
```

`_find_account` filters out scheduled-transaction template
accounts. The two earlier branches do not.

**Scenario.** The codebase emits short GUIDs (`%xxxxxxx`) via
`_account_short_guid_map`, which iterates *every* account
including templates (line 1048: `[a.guid for a in book.accounts]`).
So template-account GUIDs are in the prefix pool. A determined
caller — or a bug in another tool that mistakenly emits a
template-account short GUID — can then pass it back as `account`
to e.g. `create_transaction`, `update_account`, `move_account`,
`get_balance`, etc. The downstream method receives the template
account and operates on it.

For most read tools this is just a confusing error ("Template Root
not a real chart account"); for **write tools** like
`update_account` (rename), `move_account`, or `delete_account`,
the operation could land — corrupting the scheduled-transaction
templates and breaking `create_transaction_from_scheduled` for
every subsequent call.

**Tell:** The contract is documented at `_find_account`'s
docstring ("Scheduled-transaction template accounts ... are never
returned") but the contract was extended to two new resolution
paths in `_resolve_account` without re-applying the filter.

**Classify:** CONFIRMED design gap. Likely never hit in normal
LLM usage because the short-GUID map sorts hex first, then
prefixes, and template accounts rarely have human-meaningful
paths — but the boundary is breakable by code, not just by
intent.

**Fix sketch.** After the SQLAlchemy query returns the account,
check membership in `self._template_account_guids(book)`; if
present, return `None`. Apply to both the `%short` and the full-
GUID branches. The full-GUID branch also wants this fix; an LLM
that learned the template root's GUID via a debug path could
otherwise reference it.

Also worth: filter template-account GUIDs out of the prefix pool
at `_account_short_guid_map` so the LLM never sees a `%short`
referencing one in the first place.

**Test sketch.** Construct a book with a scheduled transaction
(forcing template-account creation), look up the template root
GUID via `book.root_template.guid`, pass `%` + the 7-char prefix
to `update_account(name=..., new_name='renamed')`; assert it
returns "Account not found" or similar guard message.

---

### F9 — CONFIRMED — `set_account_slot` / `delete_account_slot` have no `_verify_write` round-trip

**Where:** `book/admin.py:71-115` and `book/admin.py:117-144`

```python
account[key] = value
book.save()

return {"status": "updated" if existing else "created"}
```

**Scenario.** Every other write path that the contributor guide
flags as a write-verification target (`_verify_write` for raw SQL
INSERTs, `_verify_transaction_state` for transaction updates,
`_verify_delete` for raw SQL DELETEs) does a round-trip read-back.
The slot writes are bare setitem-then-save.

If piecash's KVP-set path silently no-ops (key collides with a
piecash builtin, a typed slot wrapper rejects the input type, a
hierarchical sub-slot is created instead of a flat key because
the `_SLOT_KEY_RE` validator drifts), the response says
`"updated"` but disk has the prior value.

`set_account_slot` also does a try-then-fall-through-on-KeyError
check (line 106-110) to compute the `"created"` vs `"updated"`
label — and that very check has the side effect of touching the
slot through `account[key]`. Mostly benign; cited because it shows
piecash's setitem/getitem path has surprising side effects.

**Classify:** CONFIRMED. Lower severity than the others — slots are
rarely security-critical — but inconsistent with the contributor
guide's "every write is verified" invariant.

**Fix sketch.** After save, `account[key]` (catching `KeyError`)
and compare to `value`; raise `RuntimeError` on mismatch.

**Test sketch.** Monkeypatch piecash's slot-setter to no-op; call
`set_account_slot`; assert it raises rather than reporting
success.

---

### F10 — CONFIRMED — Audit log is unbounded daily file with no rotation

**Where:** `logging_config.py:380-417` (`setup_logging`)

```python
audit_file = audit_dir / f"{today}.txt"

# Write header if file is new
write_header = not audit_file.exists()

audit_handler = logging.FileHandler(audit_file)
```

**Scenario.** Daily file, no rotation, no size cap. A single day's
audit log can grow until the disk fills. The pre-clear in the
audit decorator and the `_flush_logger(logger)` flush after each
entry mean every write hits disk synchronously — a misbehaving
caller that loops on a no-op write tool can fill the disk in
hours.

There's no compaction (the same write entry repeats verbatim
every minute if a stuck client retries forever). Each write
entry is ~150-500 bytes including the format-text rendering.
At 100 writes/second sustained → ~30 MB/min → 1.7 GB/hour. A
small VPS dies overnight.

The auto-backup chain (BackupMixin) also writes into the same
`.mcp` directory tree (under `backups/`) — running out of disk
inside the audit log breaks future auto-backups too.

**Classify:** CONFIRMED denial-of-service / data-loss surface.

**Fix sketch.** Use `logging.handlers.RotatingFileHandler` with a
size cap (say 100 MB) and a small backup count (5), or
`TimedRotatingFileHandler` with daily rotation + a backup count
to limit total disk consumption. Header logic generalizes per
new file. Pair with a `prune_audit_logs` tool similar to
`prune_backups`.

---

### F11 — NEEDS_VERIFICATION — `_normalize_account_refs_for_audit` opens the book again per write

**Where:** `logging_config.py:1748-1844`

```python
if _get_book_func is not None:
    try:
        book_wrapper = _get_book_func()
        if book_wrapper is not None:
            with book_wrapper.open(readonly=True) as book:
                for ref in refs:
                    try:
                        account = book_wrapper._resolve_account(book, ref)
```

**Scenario.** The note from 4.7-with-1M-context (April 19) flagged
the "double book-open per write" cost coming from `@audit_log`'s
before-state capture; that was killed by the `_stage_audit_before`
/ `_consume_audit_before` threading-local pattern. But this
function — called from `_format_audit_entry_text` on every write
that has any account ref in `params` — opens a new book session
per audit-log entry to canonicalize the refs. That's still a
~40-100ms second open per write tool that touches accounts.

`refs` is built only from `params`; for tools where the LLM
passed paths (not `%short` GUIDs) the `_looks_like_guid_ref`
filter at line 1775/1781 returns empty `refs` and the function
returns early (line 1784-1786). So this only fires when the LLM
used short GUIDs — which is encouraged by the orientation
instructions and the comms-audit work.

**Classify:** NEEDS_VERIFICATION — confirmed mechanical re-open
but the magnitude depends on workload. For an LLM that follows
the project's "use short GUIDs" guidance, this is the dominant
path.

**Fix sketch.** Same as 4.7's "session sharing" backlog item:
either thread the session through (the tool's book session is
already closed by this point, so this is non-trivial), or hand
the audit decorator a callable that resolves refs lazily via
`_resolve_guid`'s raw SQLite read-only connection (which doesn't
need a full piecash open).

---

### F12 — NEEDS_VERIFICATION — `delete_account` safeguard is correct but `splits` check could see voided/lot zombies

**Where:** `book/core.py:3704-3756`

```python
if account.children:
    ...raise...

if account.splits:
    raise ValueError(
        f"Cannot delete account with {len(account.splits)} transaction(s)..."
    )
```

**Scenario.** The check uses `len(account.splits)` — every split
ever associated with the account, including voided zombies. This is
*conservative* (refuses delete on an account whose entire history
has been voided to zero), which is OK for safety but might be
surprising to the user: "I voided every transaction on this
account, why won't it delete?" The error message tells them to
"Move or delete transactions first" but the voided transactions
can't be deleted-then-moved without an unvoid+rebuild cycle.

This isn't a corruption bug — it's a UX gap that surfaces only
after a user does a lot of voiding on the account they're now
trying to retire.

Also: `delete_account` does NOT accept a `replacement_account`
parameter (the contributor guide and CLAUDE.md docstring describe
one in spec but the implementation only has `name`). The doc
talks about "move or delete transactions first." The disconnect
between spec ("force=True with replacement_account") and
implementation (no force, no replacement) is worth flagging.

**Classify:** NEEDS_VERIFICATION — design ambiguity rather than a
correctness bug. Most users hit a clear error.

**Fix sketch.** Make a decision:

- Option A (safer): leave as-is, update docs/spec to remove the
  `replacement_account` parameter language.
- Option B: implement `force=True, replacement_account=...` per
  spec. Validate the replacement isn't a child of the deleted
  account, isn't the deleted account itself, and isn't a
  template. Move splits via raw SQL or piecash repointing, then
  `_verify_*` the move before deleting.

If B is chosen, the move would also need to preserve reconciled
state on the moved splits (or refuse on reconciled-without-force
similar to `delete_transaction`).

---

### F13 — CONFIRMED — `get_book_summary` reconciliation backlog counts voided splits

**Where:** `book/core.py:300-352`

```python
for s in account.splits:
    any_splits = True
    rstate = s.reconcile_state
    if rstate in ("y", "c"):
        has_yc = True
    if rstate == "y":
        ...

# Count unreconciled splits past the last 'y' date
if latest_y_date is None:
    unreconciled_count = sum(
        1 for s in account.splits
        if s.reconcile_state != "y"
    )
```

**Scenario.** Same shape as F2: `!= "y"` admits `'v'`. Voided
splits with `value == 0` join the unreconciled-count headline.
The bookkeeper sees "47 splits unreconciled since 2025-12-15"
and 3 of those are voided zombies they already dealt with months
ago. They reconcile the live splits, the count stays at 3, they
spend a frustrated minute clicking around to figure out what's
left.

**Classify:** CONFIRMED — display correctness; UX cost similar to F2.

**Fix sketch.** Same as F2: `s.reconcile_state not in ("y", "v")`,
or add a global `_is_voided(split)` helper used everywhere this
filter shape appears.

---

## Considered and verified safe

### Threading-local audit staging — no leak between sibling tool calls

The `_stage_audit_before` / `_consume_audit_before` pattern is
defended by three converging cleanups in `logging_config.py`:

1. **Pre-clear at wrapper entry** (line 2016-2022): clears any
   leftover state from a prior tool BEFORE the new one begins.
2. **Success-branch consume** (line 2072-2079): clears after the
   wrapped tool returns.
3. **Exception-branch consume** (line 2143-2149): clears on the
   raise path.

The threading-local is also per-thread, so concurrent requests on
different threads don't see each other's state. The MCP server in
stdio mode runs single-threaded, so the lock-free pattern is
correct for the documented deployment. If multi-worker deployment
arrives, the threading-local naturally scopes per worker; no
shared state to race on.

The only scenario where state could leak: a write book method
calls another book method that ALSO calls `_stage_audit_before`.
That inner stage clobbers the outer. **This DOES happen in
`create_transaction_from_scheduled`** (F1) — the inner
`create_transaction` doesn't stage (verified by grep), so this
specific nesting is safe. If a future contributor wires a write
into another write that DOES stage, the audit-log diff will
silently swap. Worth a comment on `_stage_audit_before`'s docstring
warning about this.

### `_verify_transaction_state` round-trip is correctly bypass-the-cache

`session.expire(transaction)` at line 3853 before the field reads
is the right call — otherwise SQLAlchemy's identity map would
return the in-session value we just wrote, making the verification
trivially pass. The shortcut input → canonical name resolution at
line 3910 is also correctly done before key comparison. This
defended the bookkeeper-found regression in PR #75.

### Composite-write atomicity in `pay_invoice`

The `pay_invoice` path builds all splits (including the FX
gain/loss split when applicable) BEFORE creating the
`piecash.Transaction`. The transaction-creation step adds all
splits in one ORM operation. Then `ar_ap_split.lot = lot_obj`,
then `book.flush()`, then slot writes, then `book.save()`. If
any step before `book.save()` raises, the session rolls back —
no partial write lands on disk. The "voided posting transaction"
preflight at line 5756-5768 specifically blocks the
pay-against-voided edge that would otherwise leave a payment
split assigned to a soon-to-auto-close lot. Verified.

The cross-currency price write happens upstream of the payment via
`create_price`; failure there means no rate found and `_convert`
raises before any splits are built. No "invoice posted with no
rate on file" intermediate state exists in code, only in failed
multi-step user workflows the server is not responsible for
atomic-izing.

### `_maybe_auto_backup` flag-then-run is intentional

Setting `_backup_checked_in_process = True` BEFORE running
`create_backup` is the documented design (line 665-668): "Flag
BEFORE running so a raise here won't cause the audit hook to
retry on every subsequent write of the process." This shifts the
"backup chain broken" signal to `_write_attempt_status`, where
`get_book_summary` surfaces it. The trade-off — one failed
auto-backup per process lifecycle vs. retry storm on every
subsequent write — is the right one.

### `prune_backups` vs `create_backup` ordering

Auto-backup invokes `create_backup` and `_prune_auto_stages`
sequentially within a single thread (`_maybe_auto_backup` line
694-701). Manual `create_backup` and `prune_backups` are
independent MCP tool calls; the single-threaded stdio MCP
deployment serializes them at the request level. The
`_backup_check_lock` (line 326) is only for the first-write gate,
not for cross-tool serialization — but cross-tool serialization
isn't needed under stdio. A multi-worker deployment would need
attention; flagged in F11's general theme.

---

## Summary

13 findings. 10 CONFIRMED, 3 NEEDS_VERIFICATION.

By severity:

**Data integrity (high):** F1 (scheduled-transaction non-atomic),
F4 (set_reconcile_state un-voids), F6 (lot decimals on voided),
F8 (template-account boundary bypass).

**State drift on voided splits (medium, several sites):** F2, F3,
F7, F13.

**Operability / DoS (medium):** F5 (lock-retry misses piecash),
F10 (audit log unbounded), F11 (audit format double-open).

**Verification gaps (low):** F9 (slot writes).

**Design ambiguity (low):** F12 (delete_account spec drift).

The single biggest theme is **voided splits as a half-handled
state class**: 5 of 13 findings (F2, F3, F4, F6, F7, F13) trace
back to "we filter `state == 'v'` here but not there." A shared
`_is_voided(split)` helper in `_base.py` with consistent use
across investments, business, reconciliation, and reporting
modules would close most of this in one pass.

The next-biggest is **boundary bypass through alternate resolution
paths** — F8 (`%short` GUIDs reaching templates) is the
representative case; the lesson is "every resolution helper needs
the same filter set as the path it bypasses."

F1 is the standalone item worth fixing first — it's a real
"transaction lost, schedule advanced" data-loss shape.
