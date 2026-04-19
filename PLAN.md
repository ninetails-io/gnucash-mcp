# feat/computation-efficiency — handoff plan

Branched from `develop` at `b187e9f` (the token-efficiency merge). No
changes yet. This file is a handoff from the previous Claude; delete it
when the work is done.

## What this branch is for

Four carried-over performance items from earlier audits, in priority
order. Each is substantial enough to deserve its own commit or two; do
them sequentially, not as one giant PR.

---

### 1. Kill the double book-open on writes (biggest single win)

**The bug:** `@audit_log` decorator's `_capture_before_state` helper in
[`logging_config.py`](src/gnucash_mcp/logging_config.py) calls
`book.get_transaction(guid)` / `book.get_account(name)` etc. BEFORE the
tool runs. The tool then opens the book again. Every write op pays
~40-100ms of redundant SQLite open/close.

**Where to look:**
- `logging_config.py::_capture_before_state` (~line 161) — the helper
- `logging_config.py::audit_log` decorator wraps `safe_tool` which
  wraps the tool wrapper. Decorator order matters.
- Every write method in `book/` opens its own book via
  `with self.open(readonly=False) as book:`.

**Design options I considered but didn't pick:**
- **(a) Thread a session.** Have `@audit_log` accept an already-open
  book from a context manager. Requires refactoring every write tool
  wrapper.
- **(b) Move before-state capture into the book method.** Book method
  opens once, captures, writes, returns both. Decorator fishes it
  from the response (or a sidecar). Cleaner but bigger change.
- **(c) Lazy before-state via the same session the tool opens.** The
  book method calls a capture helper that uses the already-open
  session. `@audit_log` just reads the captured state from an attr
  on the book/transaction. Smallest design footprint.

I'd probably start with **(c)** — least invasive. But design review it
with Stephen first; he'll have opinions.

**Watch for:** the audit log's `before_state` is what
`_format_audit_entry_text` uses to render "Before: ..." lines. Don't
break the audit log while fixing this — the Phase-2 work in
`logging_config.py` put a fallback resolver in place
(`_resolve_entry_field`) so even trimmed responses render; that
mechanism will still work if `before_state` stays a dict.

**Test target:** add a test that counts book-opens per write. Patch
`piecash.open_book` with a counter, call a write tool, assert count == 1.

---

### 2. Replace O(N) scans in `_find_*` with indexed piecash queries

**The bug:** `_find_transaction` / `_find_split` / `_find_lot` /
`_find_scheduled_transaction` all do this:

```python
full_guid = self._resolve_guid("transactions", guid)  # SQL, indexed
for transaction in book.transactions:                  # O(N) Python scan!
    if transaction.guid == full_guid:
        return transaction
```

Piecash exposes `book.session.query(Transaction).filter_by(guid=...).first()`
which is indexed via SQLAlchemy. Same for `Split`, `Lot`,
`ScheduledTransaction`.

**Where to look:**
- `book/_base.py::_find_transaction` (line ~572)
- `book/_base.py::_find_split` (line ~613)
- `book/investments.py::InvestmentsMixin._find_lot` (line ~370ish)
- `book/scheduling.py::SchedulingMixin._find_scheduled_transaction` (~170)

**Implementation:** One-line-ish change per helper. The awkward bit is
`_find_transaction` and `_find_split` live in `_base.py` which doesn't
import piecash ORM classes (only via method bodies). You can import
locally.

**Tests:** existing tests should pass unchanged — behavior is identical,
just faster. Optional: add a perf test that creates 10k dummy
transactions and asserts `_find_transaction` completes in < Xms.

---

### 3. Consolidate `create_transaction`'s 3-4 passes over `book.transactions`

**The bug:** `book/core.py::create_transaction` chains:
- `_auto_fill_splits` (scan for matching description)
- `_check_auto_fill_stability` (scan again for pattern consistency)
- `_find_duplicates` (scan again for match signals)
- post-write: `_find_recent_description_matches` for the warnings

Each is O(N). For a 10k-txn book, ~40k iterations per create.

**Fix:** single-pass collector. Walk `book.transactions` once, gather:
- Candidate auto-fill source (most recent matching description)
- Auto-fill stability signal (are recent matches consistent?)
- Duplicate candidates (description/amount/date signals)
- Recent-match list for warnings

Then branch on what `create_transaction` needs based on args
(auto-fill only if `splits=None`, duplicates only if
`check_duplicates=True`, etc.).

**Where to look:** `book/core.py::create_transaction` (~line 720) calls
all four. The helpers are in the same file. Design the collector
first, keep the existing helpers as thin wrappers around it while
migrating. Kill the wrappers when nothing calls them.

**Test target:** count calls to `book.transactions` iteration. Tricky
to measure directly — maybe wrap `book.transactions` with a counting
proxy in a fixture.

---

### 4. SQL aggregates for reporting methods

**The bug:** `balance_sheet`, `net_worth` (especially time-series),
`spending_by_category`, `income_by_source`, `cash_flow` all iterate
`book.transactions` or `account.splits` in Python with
`if post_date <= X` date compares. A 5-year monthly net-worth
time-series = 60 full scans.

**Fix:** drop to `book.session.execute(text(...))` with SQL aggregates.
The splits table has `value_num`/`value_denom` — you'll need to
reconstruct Decimal values. Transactions have `post_date` indexed in
GnuCash's default schema.

**Where to look:** `book/reporting.py`. Each method is largely
self-contained.

**Watch for:**
- Multi-currency books: `split.value` is in transaction currency,
  `split.quantity` is in account commodity. For net-worth and
  balance-sheet you want quantity (account commodity). Earlier commits
  flagged this — Abe's note in CLAUDE.md.
- Account type filters matter (`ASSET`, `BANK`, `LIABILITY`, etc.).
  Join against the `accounts` table to filter.
- Null dates exist in some old books; handle them.

**Test target:** existing tests cover correctness. Add a large-book
fixture (maybe 10k transactions, 20 accounts) and assert the method
completes in reasonable time. Not needed for small books.

**Lowest priority of the four** because it only matters at scale.
Finance books for individuals often have < 1000 txns, where the Python
loop is fast enough. If Stephen's book is small, defer this.

---

## Collaboration model Stephen prefers

- **Propose before implementing.** Write a short plan, get sign-off,
  then write code. Don't spelunk for hours.
- **Small commits, feature branches.** One logical change per commit.
  Merge via `--merge --delete-branch` (or `git merge --no-ff` locally).
- **Live testing at inflection points.** Pure code-motion he'll skip;
  new behavior he'll test himself.
- **CLAUDE.md conventions.** Read CLAUDE.md. Especially:
  - No attribution lines in commits
  - `feat/<name>` for branches off develop
  - Never `git add -A` / `git add .`
  - Never commit CLAUDE.md (excluded from git)
  - Don't create `.md` files unless asked (this file is an exception
    — Stephen asked for a handoff plan)

## Gotchas I hit last session

- `CallableList` in piecash doesn't support integer indexing.
  `book.accounts(fullname=name)[0]` raises an assertion error. Use
  the `_find_account` helper.
- `_format_audit_entry_text` in `logging_config.py` is a giant
  if/elif dispatcher. It's built to tolerate thin responses — the
  `_resolve_entry_field` helper falls through after_state → params →
  before_state. If you change what gets captured to `before_state`,
  make sure this still works.
- `tests/test_tools.py` has a module-scoped autouse fixture that
  force-loads all tool modules and binds their `.fn` back onto the
  server module. Required because tools are closures now. Don't break.
- `tests/conftest.py` builds `test_book` via piecash `factories`.
  Read it before writing new fixtures — there's probably already a
  helper for what you need.

## Opening move

Start with item 1 (double book-open). I flagged it as the biggest
win; it's also the item that most reshapes the audit log / book method
interface, so doing it first avoids rework on items 2-4.

Propose a design to Stephen before coding. Include:
- Which option you're taking (my a/b/c from above, or your own)
- What gets touched (file list)
- How the existing audit log behavior stays intact
- A test plan

Good luck. The project is in good shape; the bones are right; Stephen
is a steady collaborator.

— Previous Claude (1M context), April 2026
