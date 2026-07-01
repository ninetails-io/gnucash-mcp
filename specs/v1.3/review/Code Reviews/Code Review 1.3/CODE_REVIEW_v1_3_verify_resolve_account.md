# Adversarial verification — `_resolve_account` and template-account filtering

Adversarial pass over three claims about asymmetric template-account
filtering in `_resolve_account` and `_query_filtered_splits`. Default
disposition is "refute"; verdicts below land where the code actually
forced the conclusion.

Files inspected:
- `src/gnucash_mcp/book/_base.py` — `_resolve_account`, `_find_account`,
  `_resolve_guid`, `_template_account_guids`, `_GUID_TABLE_QUERIES`.
- `src/gnucash_mcp/book/_query.py` — `_query_filtered_splits`.
- `src/gnucash_mcp/book/core.py` — `get_account`, `update_account`,
  `move_account`, `delete_account`.
- `src/gnucash_mcp/book/scheduling.py` — `create_scheduled_transaction`
  (to confirm what type/parent template accounts hold).

---

## Claim R-1 — `_resolve_account` short-GUID and full-GUID branches skip the template filter

### Verdict
**CONFIRMED.** The path branch filters templates; the `%short` and
32-char full-GUID branches do not. Write tools resolving via the
non-path branches can bind a template-tree account.

### Reasoning
`_resolve_account` (lines 1134–1179) has three branches:

```
ref starts with "%"      → _resolve_guid("accounts", suffix)
                             then book.session.query(Account)
                             .filter_by(guid=full_guid).first()

len(ref) == 32, hex      → book.session.query(Account)
                             .filter_by(guid=ref.lower()).first()

anything else            → _find_account(book, ref)  ← template-filtered
```

Only the third branch enters `_find_account`, which explicitly
iterates `book.accounts`, skips any GUID in
`_template_account_guids(book)`, and matches on `fullname`.

The short-GUID branch goes through `_resolve_guid("accounts", ...)`,
whose SQL is

```
SELECT guid FROM accounts WHERE guid LIKE ?
```

(`_GUID_TABLE_QUERIES["accounts"]`, line 763.) Template accounts live
in the same `accounts` table — `book.root_template` and its
descendants are real Account rows, as `_template_account_guids`
itself documents (lines 1274–1297). The `WHERE guid LIKE ?` predicate
has no template exclusion. Likewise the 32-char branch is a plain
`filter_by(guid=…)` against the same table.

Both downstream lookups (`session.query(Account).filter_by(guid=…)`
and the SQLite prefix LIKE) return whatever row matches — template
or not.

Confirmed downstream impact: `update_account`, `move_account`, and
`delete_account` in `book/core.py:3555–3756` all call
`self._resolve_account(book, name)` and immediately operate on the
returned object. Neither the resolver nor the call sites apply a
template-guid filter. `delete_account` does check `account.children`
and `account.splits` before deleting, which would block deleting
`book.root_template` itself (it has children) or any template
account that's been wired to a scheduled-transaction split — but a
newly created or pruned template account with no splits and no
children is unguarded.

`update_account` and `move_account` are completely unguarded — a
caller can rename a template account or move it under the regular
chart-of-accounts root, which silently corrupts scheduled-
transaction infrastructure.

### Concrete reproduction
Setup: any book with at least one scheduled transaction created via
`create_scheduled_transaction`. `book/scheduling.py:292–297` shows
the template account is created as `type="BANK"` under
`book.root_template`.

1. User runs `list_scheduled_transactions` → notes the SX name (the
   template account shares the SX name).
2. Through some other surface (a tool that emits short GUIDs over
   `book.accounts`, e.g. a future `list_template_accounts`, or just
   guessing/scanning), the caller learns the template account's
   GUID prefix — say `abc1234`.
3. Caller invokes `update_account(name="%abc1234", new_name="hijacked")`.
4. Walk-through of `_resolve_account("%abc1234")`:
   - Branch 1 fires (starts with `"%"`).
   - `_resolve_guid("accounts", "abc1234", min_len=7)` runs
     `SELECT guid FROM accounts WHERE guid LIKE 'abc1234%'`.
   - Template account row matches; full GUID returned.
   - `book.session.query(Account).filter_by(guid=full_guid).first()`
     returns the template Account.
5. `update_account` proceeds: `_stage_audit_before`, the rename
   conflict check against `account.parent.children` (the parent is
   `root_template` — no collisions there), `account.name = new_name`,
   `book.save()`.
6. Result: the scheduled-transaction template account is renamed.
   On next instantiation via `create_transaction_from_scheduled`,
   piecash's `sx.template_account` lookup still works (it's GUID-
   keyed), so the rename may go unnoticed for the SX flow itself,
   but any code path that surfaces the template account by name —
   or any future `delete_scheduled_transaction` that depends on a
   name match — drifts silently.

The same script with `move_account(name="%abc1234", new_parent="Assets")`
would reparent the template under the regular chart of accounts.
The next `book.accounts` iteration anywhere — `list_accounts`,
`balance_sheet`, `net_worth` — now treats it as a real BANK
account, because the template-guid set (computed from
`root_template` descent) no longer includes it.

The asymmetry against the path branch is the surprise. A caller
passing the same account by its fullname (e.g.
`"Template Root:Rent SX"`) hits `_find_account`, which silently
returns `None`, and the write tool raises `"Account not found"`.
The same logical operation through `%short` form succeeds.

### Fix shape
Apply the template-guid filter in `_resolve_account` after each
branch returns, before handing the Account to the caller:

```python
acct = book.session.query(Account).filter_by(guid=full_guid).first()
if acct is not None and acct.guid in self._template_account_guids(book):
    return None
return acct
```

Doing it in `_resolve_account` (rather than at each call site)
keeps the contract symmetric across all three input shapes and
matches the documented invariant ("any iteration that aggregates
balances, surfaces accounts to the user, or classifies by type must
filter them via `self._template_account_guids(book)`").

---

## Claim R-2 — `_query_filtered_splits` does not template-filter

### Verdict
**CONFIRMED, dormant.** The function has no template filter. The
condition is currently unreachable in practice because user-posted
splits don't reference template accounts — template accounts back
SX *templates*, not posted splits.

### Reasoning
`_query_filtered_splits` (`book/_query.py:27–96`) builds:

```python
book.session.query(Split, Transaction, Account)
    .join(Transaction, Split.transaction_guid == Transaction.guid)
    .join(Account, Split.account_guid == Account.guid)
    .filter(Transaction.post_date.isnot(None))
```

with optional `start_date`, `end_date`, `account_types`,
`account_guids`, and an order-by clause. There is no
`Account.guid.notin_(template_guids)` clause and no equivalent in
Python.

Why it's safe today: GnuCash scheduled-transaction templates are
modeled as `Transaction` rows referenced via `schedxactions.template_act_guid`
that do not have a `post_date` (they're templates, not posted
transactions). Any template-attached split is excluded by the
`Transaction.post_date.isnot(None)` clause already in the query.

So the residual exposure is only:
1. A future contributor materializes a transaction with a
   `post_date` whose split references a template-tree account (e.g.
   debugging code, an import path that misroutes, a hand-written
   SQL fix), or
2. The `post_date.isnot(None)` invariant changes upstream in piecash
   or GnuCash and template rows start having dates.

Either would cause every report routed through
`_query_filtered_splits` (`balance_sheet`, `net_worth`, `cash_flow`,
`spending_by_category`, `income_by_source`, plus the budget
actuals helpers at lines 262, 335, 407, 644, 693, 761, 768 in
`reporting.py`) to silently include template-account quantities.

### Concrete reproduction (theoretical)
A direct SQL insert routing a split to a template account with a
posted transaction (which would never come from this codebase but
could come from another GnuCash client or a bug):

```sql
INSERT INTO splits (guid, tx_guid, account_guid, value_num, value_denom, ...)
VALUES ('newsplit...', '<txn-with-post-date>', '<template-acct-guid>', 100, 1, ...);
```

After such an insert, `balance_sheet(as_of_date=today)` would
include the template account's `type` ("BANK" per
`create_scheduled_transaction` line 294) in the `_ASSET_TYPES`
bucket and surface its quantity as part of total assets — silently,
no warning.

Verifying this is currently dormant: in this repo,
`create_scheduled_transaction` is the only path that touches
`root_template`, and it only creates the template *account* (no
splits with posted transactions). `create_transaction_from_scheduled`
creates a real, user-account-targeted Transaction, not a template-
account-targeted one.

### Fix shape
Add an opt-out filter to `_query_filtered_splits`:

```python
template_guids = self._template_account_guids(book)
if template_guids:
    q = q.filter(Account.guid.notin_(list(template_guids)))
```

Defense-in-depth: zero practical impact today, blocks the entire
class of "template Account row sneaks into a report" bugs at the
single chokepoint.

---

## Claim R-3 — `get_account("%abc1234")` can return a template account; `get_account("Template/Path/Name")` correctly returns `None`

### Verdict
**CONFIRMED.** Direct consequence of R-1. Same input semantically;
opposite outcomes depending on which input shape the caller uses.

### Reasoning
`get_account` in `book/core.py:2301–2314` is a thin wrapper:

```python
def get_account(self, name: str) -> dict | None:
    with self.open(readonly=True) as book:
        account = self._resolve_account(book, name)
        if account:
            return _account_to_dict(account)
        return None
```

It does no filtering of its own. The asymmetry in `_resolve_account`
(documented under R-1) flows straight through:
- Path input → `_find_account` → template filter applies → `None`.
- `%short` input → `_resolve_guid` → SQL by GUID → Account returned →
  serialized via `_account_to_dict`.
- 32-char GUID input → SQL by GUID → Account returned → serialized.

### Concrete reproduction
Assume a book with one scheduled transaction named "Rent SX". Its
template account is `book.root_template.children[0]` with
`fullname` something like `"Template Root:Rent SX"` and a GUID
starting `abc1234`.

Symmetric calls, asymmetric outcomes:

```
get_account("Template Root:Rent SX")
  → _resolve_account hits the path branch
  → _find_account iterates book.accounts, skips template-guid set,
    returns None
  → get_account returns None  ✓ (documented contract)

get_account("%abc1234")
  → _resolve_account hits the %short branch
  → _resolve_guid runs SELECT guid FROM accounts WHERE guid LIKE 'abc1234%'
  → matches the template account
  → session.query(Account).filter_by(guid=full).first() returns it
  → _account_to_dict serializes the template account
  → get_account returns the dict  ✗ (contract violation)

get_account(full_32char_template_guid)
  → 32-char branch, same outcome as %short
  → returns the dict  ✗
```

Same logical operation. Three input shapes. Two of them violate the
documented contract.

This isn't a hypothetical — the get_account dict from
`_account_to_dict` includes `parent`, `type`, `commodity`, etc.,
which look like a normal account dict. The LLM, on seeing a
plausible Account dict back, has no signal that it's poking at SX
scaffolding. Down the chain, any tool that takes the returned
`fullname` and feeds it forward to `update_account` or
`move_account` would still hit the path branch and get a `None` —
so the inconsistency manifests as "I can `get_account` it but I
can't `update_account` it by the same path" until the caller tries
`%short` again, at which point it succeeds and corrupts.

### Fix shape
Same as R-1. Pushing the template filter into `_resolve_account`
(once, post-branch) is the single source of truth that closes R-1
and R-3 simultaneously.

---

## Summary table

| Claim | Verdict | Severity | Fix locus |
|-------|---------|----------|-----------|
| R-1 — `_resolve_account` `%short`/32-hex branches skip template filter | Confirmed | High (write tools can mutate SX template accounts) | `_resolve_account` in `_base.py` — apply filter after each branch |
| R-2 — `_query_filtered_splits` no template filter | Confirmed, dormant | Low today, latent risk | Add `Account.guid.notin_(template_guids)` clause |
| R-3 — `get_account` asymmetric on input shape | Confirmed | Medium (information leak + symmetry violation) | Same fix as R-1 |

All three claims survive adversarial scrutiny. The single most
valuable fix is hoisting the template-guid filter into
`_resolve_account` itself — that closes R-1 and R-3 in one move
and matches the existing documented invariant. R-2 is a defense-
in-depth backstop worth taking on the same change.
