# Voided-Split State Claims — Adversarial Verification

Verifier method: read the cited code; trace concrete voided-split scenarios; do not trust comments or predecessor letters; default to CONFIRMED if I cannot refute.

GnuCash voided splits ("zombies"): `reconcile_state == 'v'`, `value == 0`, `quantity == 0`, row preserved, original value/quantity stashed in slots `void-former-value` / `void-former-quantity` (see `book/reconciliation.py:514-523`).

---

## Claim V-1 (state F2)

**Claim:** `get_unreconciled_splits` filter is `!= "y"`, which admits 'v' (voided) splits and counts them as "uncleared." Should be excluding voided.

### Verdict: CONFIRMED

### Reasoning

`book/reconciliation.py:181-195`:

```python
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
```

The filter is literally `!= "y"`. The set `{"n", "c", "v"}` all satisfy it. The follow-up branch is `== "c"` vs. else — and `else` covers BOTH `"n"` and `"v"`. The docstring comment "(n or c, not y)" is aspirational; the code is broader. The footer comment lines (around the docstring at L130-139) say "cleared / uncleared totals reflect the **full** unreconciled set" — that reads as a feature, but it accidentally pulls voided zombies into the uncleared bucket.

### Concrete demonstration

Account `Assets:Checking` with three splits on otherwise different transactions:
- Split A: `reconcile_state='y'`, quantity=100 → skipped (correct)
- Split B: `reconcile_state='c'`, quantity=50 → admitted; `cleared_total = 50`
- Split C: `reconcile_state='v'`, quantity=0 (zombie) → admitted into `all_unreconciled`; falls to `else`; `uncleared_total += 0`

The `uncleared_total` doesn't change in dollars (voided quantity is 0), but:
- `count` and `total` are inflated by 1 (Split C is in the list)
- The compact rendering emits a line for Split C with `amount=0` (cryptic)
- Header footer reports more "splits" than the user has actually-unreconciled
- The dict response's `splits` list contains Split C with `reconcile_state="v"` — a state the caller did not ask for

The dollar totals happen to be correct by coincidence (voided quantity == 0), but the **count surface and the splits list are wrong**: voided zombies appear as work items the user must address.

---

## Claim V-2 (state F3)

**Claim:** Code everywhere assumes `state=='v'` implies `value==0` without checking. The invariant holds by convention, not by assertion — if a write path corrupts that pairing the reports go wrong.

### Verdict: CONFIRMED

### Reasoning

I searched for sites that filter on `state=='v'` OR `value==0`. There is exactly one paired site (the `(state, value)` pairing only happens at the write end, inside `void_transaction` at `reconciliation.py:514-523`, which sets both atomically). Every reader trusts the pairing implicitly:

- `book/business.py:1758` — `_calculate_lot_balance` filters by `reconcile_state == "v"`; assumes those splits had value=0 (they do post-void)
- `book/business.py:5757-5760` — `pay_invoice` voided-posting check uses `all(s.reconcile_state == "v" for s in inv.post_txn.splits)`; no value cross-check
- `book/investments.py:912-914` — `calculate_lot_gain` counts `s.reconcile_state == "v"` to differentiate "ran to zero through sales" from "voided"; no value check
- `book/reconciliation.py:486` — `void_transaction` rejects an already-voided txn via `reconcile_state == "v"`; no value check
- `book/reconciliation.py:563` — `unvoid_transaction` gate on `reconcile_state == "v"`; restores from `void-former-value`/`void-former-quantity` slots — does NOT verify current value is 0 before overwriting

No site asserts `state=='v' ⇒ value==0` or vice versa. The closest thing to an assertion is `unvoid_transaction` validating that BOTH slots are present (`reconciliation.py:573-592`); but it does not validate that the current split values are actually zero.

Conversely, no site filters on `value == 0` to detect voided splits — they all key on state. So if a write path zeros a split's value without setting `reconcile_state='v'` (e.g., a buggy update path, a manual SQL repair, or a partial corruption mid-`void_transaction` save), readers will treat it as a live `n`/`c`/`y` split with $0 value. `get_unreconciled_splits` would surface it; `_calculate_lot_balance` would include it (and contribute 0, so no $ drift, but lot.splits count is "active"); `_lot_decimals` would route through the `else` branch (quantity not > 0) and `abs(0) = 0`, so no observable lot drift in that case either.

The cleanly-broken direction is the OTHER way: `reconcile_state='v'` with nonzero value (slots present but mutation didn't atomic-commit). `_calculate_lot_balance` would skip it (correctly per spec, but the dollars are real). `unvoid_transaction` would overwrite the existing value with the slot value — a partial restoration if they already disagreed.

### Concrete demonstration

Suppose a write path (hypothetical bug or external SQL edit) leaves a single split in state `v` with value=$50 still present (slots also set):

- `get_unreconciled_splits` correctly skips it (state != y matches, BUT — wait, V-1 just showed `!=` admits `v` too; so it'd be admitted as "uncleared" with amount=50)
- `_calculate_lot_balance` (`business.py:1758`) skips it — `total` is short by $50
- `pay_invoice` (`business.py:5756-5768`) — if every split on the posting txn is `v`, refuses payment; this is "all v" so the $50 outstanding balance is invisible AND the operation is blocked. Stuck state.
- `unvoid_transaction` (`reconciliation.py:594-606`) blindly overwrites `split.value = Decimal(former_value)` — corruption silently hidden by clobber.

Inverse: value=0, state='c'. `get_unreconciled_splits` admits it as cleared (with $0 — confusing but harmless). `_calculate_lot_balance` includes it (adds 0). `_lot_decimals` routes to `else` and adds `abs(0) = 0` to sale_quantity. Mostly invisible.

The invariant is convention, not assertion. The repository ships without a `_split_is_voided(s)` helper or any cross-check. Documented gotcha from `CLAUDE.md`:

> Voided splits are zombies, not gone. ... Code that asks "does this lot/account have any payment activity" must filter on `s.value != 0` or `s.reconcile_state != 'v'`, not on split presence alone.

The note offers `value != 0` OR `state != 'v'` as alternatives — exactly the choice that exposes the implication-by-convention. No site uses the conjunction.

---

## Claim V-3 (state F4)

**Claim:** `set_reconcile_state` (`book/reconciliation.py:52-101`) accepts state inputs of 'n'/'c'/'y' but doesn't check the CURRENT state. Setting a voided split (state='v', value=0) to 'y' creates a "reconciled $0" split and breaks `unvoid_transaction` which uses state='v' as the signal to restore from slots.

### Verdict: CONFIRMED

### Reasoning

`reconciliation.py:74-89`:

```python
state = state.lower()
if state not in self.VALID_RECONCILE_STATES:
    raise ValueError(...)

with self.open(readonly=False) as book:
    split = self._find_split(book, split_guid)
    if not split:
        raise ValueError(f"Split not found: {split_guid}")

    # Stage pre-update state for the audit log.
    self._stage_audit_before(_split_state_dict(split))

    split.reconcile_state = state
```

`VALID_RECONCILE_STATES = {"n", "c", "y"}` (L50). The validation gates the **input**, not the current state of the split being mutated. There is no `if split.reconcile_state == "v": raise` guard. The assignment `split.reconcile_state = state` blindly overwrites.

Now look at the unvoid gate (`reconciliation.py:563`):

```python
if not any(s.reconcile_state == "v" for s in transaction.splits):
    raise ValueError(f"Transaction {guid} is not voided")
```

`unvoid_transaction` uses state=='v' as the **sole** signal. The void-former slots also remain (no caller deletes them through `set_reconcile_state`), but they only become readable via the unvoid path, which is now blocked.

### Concrete demonstration

1. `void_transaction(txn_guid, "test")` → all of txn's splits get state='v', value=0, quantity=0; slots `void-former-value` / `void-former-quantity` set.
2. Caller (or LLM) issues `set_reconcile_state(split_guid_A, "y")` for one split of that voided transaction.
3. Split A now: state='y', value=0, quantity=0, reconcile_date=today, slots `void-former-*` STILL PRESENT.
4. `get_unreconciled_splits` shows the other splits as voided-pretending-uncleared (Claim V-1) but Split A is now "reconciled $0."
5. `unvoid_transaction(txn_guid)`: `any(s.reconcile_state == "v" for s in transaction.splits)` evaluates over the txn's splits. If the txn has 2+ splits and the others are still 'v', `any()` is still True — proceeds. Inside the loop (L594-606) it checks `if former_value is not None` per split and applies — Split A gets its original value restored, BUT `split.reconcile_state = "n"` is set on EVERY split in the txn unconditionally (L606). So Split A goes from 'y' (corrupted) → 'n' (correct-by-accident on unvoid).
   
   Single-split transactions can't normally exist in GnuCash (double-entry needs at least 2 splits), so the `any()` gate works for any normal txn. But the corrupted state — `y` with stash slots present — persists in the book between steps 3 and 5, and during that window:
   - `pay_invoice` voided-posting check `all(s.reconcile_state == "v" ...)` evaluates False (Split A is 'y'); the txn is no longer recognized as voided. Pay-against-voided-posting blocking is bypassed.
   - `_calculate_lot_balance` no longer skips Split A; the void-aware filter at `business.py:1758` is keyed on `'v'`.
   - The audit-log history shows a state change `v → y` with no acknowledgment that this is an illegal transition.

6. If a buggy unvoid-pre-condition refactor ever uses `all(s.reconcile_state == "v")` instead of `any(...)`, step 5 silently rejects the unvoid — the transaction becomes permanently un-unvoidable from the API.

There is no `set_reconcile_state` guard preventing 'v' → 'y'/'c'/'n'. The slot-cleanup work that `unvoid_transaction` does is bypassable.

---

## Claim V-4 (state F6)

**Claim:** `book/investments.py:547-596` (`_lot_decimals`) doesn't filter voided splits. A voided BUY after a partial SELL yields negative `remaining`, breaks auto-close in `assign_split_to_lot`.

### Verdict: CONFIRMED

### Reasoning

`investments.py:562-596`:

```python
purchase_quantity = Decimal(0)
purchase_value = Decimal(0)
sale_quantity = Decimal(0)

for split in lot.splits:
    if split.quantity > 0:
        purchase_quantity += Decimal(str(split.quantity))
        purchase_value += Decimal(str(split.value))
    else:
        sale_quantity += abs(Decimal(str(split.quantity)))

remaining = purchase_quantity - sale_quantity
```

No `reconcile_state == "v"` filter. Voided splits have quantity=0 so they fall into the `else` branch (`0 > 0` is False) and contribute `abs(0) = 0` to `sale_quantity`. That doesn't directly drive `remaining` negative.

BUT the issue is what the BUY contributed BEFORE the void: nothing — voiding zeros the quantity in place. After the void, the historical `purchase_quantity` contribution is gone. The SELL split was a separate transaction; voiding the BUY does not also void the SELL. So:

### Concrete demonstration

Lot setup:
1. `create_lot(account="Assets:Brokerage:STOCK")`
2. BUY: txn creates a split with quantity=+100 (in STOCK), value=+$1000. Assigned to lot. → `purchase_quantity=100, purchase_value=1000, sale_quantity=0, remaining=100`.
3. SELL: txn creates a split with quantity=-30, value=-$300 (or so). Assigned to lot. → `purchase_quantity=100, sale_quantity=30, remaining=70`.

Now void the BUY transaction:
4. `void_transaction(buy_txn_guid, "wrong basis")` → BUY split now: quantity=0, value=0, state='v'.

`_lot_decimals` iterates `lot.splits` (the splits ARE still in the lot — voiding doesn't remove the lot assignment):
- BUY split: `quantity=0`, `0 > 0` False → `else` branch: `sale_quantity += abs(0) = 0`.
- SELL split: `quantity=-30`, `-30 > 0` False → `else`: `sale_quantity += abs(-30) = 30`.

Result: `purchase_quantity=0, purchase_value=0, sale_quantity=30, remaining = 0 - 30 = -30`.

`remaining` is **negative**. Downstream:

- `cost_per_share = Decimal(0)` (because `purchase_quantity > 0` is False), `remaining_cost_basis = 0`.
- `_lot_summary` reports `quantity = "-30.0000"`, `cost_basis = "0.00"`.
- `assign_split_to_lot` (`investments.py:856-861`) auto-close check:
  ```python
  if Decimal(summary["quantity"]) == 0 and len(lot.splits) > 0:
      lot.is_closed = -1
  ```
  `-30 == 0` is False → **does NOT auto-close**. The lot stays open with a negative remaining and a cost basis of $0, ready to absorb more sales that will increase the negative.
  
- `calculate_lot_gain` (`investments.py:906-923`) guard `if remaining <= 0` triggers; the voided-split path raises with a helpful error message. So `calculate_lot_gain` is defended; `_lot_summary` and the auto-close path are not.

- `list_lots` and `get_lot` surface `quantity: "-30.0000"` as a visible value — a negative share count on an open lot is a real bookkeeping smell the LLM may try to "fix" by selling more, deepening the corruption.

Auto-close in `assign_split_to_lot` is the specific failure cited in the claim. The claim names the right failure: voided BUY + partial SELL yields `remaining = -30`, which is `!= 0`, which skips the auto-close branch. The lot stays open in a clearly-wrong state.

---

## Claim V-5 (state F7)

**Claim:** `assign_split_to_lot` doesn't reject voided splits as input. A voided split can be assigned to an open lot, polluting cost-basis math.

### Verdict: CONFIRMED

### Reasoning

`investments.py:828-852`:

```python
with self.open(readonly=False) as book:
    split = self._find_split(book, split_guid)
    if not split:
        raise ValueError(f"Split not found: {split_guid}")

    lot = self._find_lot(book, lot_guid)
    if not lot:
        raise ValueError(f"Lot not found: {lot_guid}")

    if lot.is_closed:
        raise ValueError("Cannot assign split to a closed lot")

    if split.account != lot.account:
        raise ValueError(...)

    if split.lot is not None:
        raise ValueError(...)

    split.lot = lot
    book.save()
```

Four guards: split exists, lot exists, lot not closed, account match, not already in a lot. **No guard on `split.reconcile_state == "v"`.**

### Concrete demonstration

1. Create txn with BUY split (quantity=100, value=$1000) on `Assets:Brokerage:STOCK`.
2. `void_transaction(buy_txn_guid, "to be re-entered")` → BUY split: quantity=0, value=0, state='v', slots set. Critically: the BUY split's `split.lot` is NOT cleared by void — but in this scenario the BUY wasn't assigned to a lot before voiding.
3. Create a new lot on `Assets:Brokerage:STOCK`. Lot is open, empty.
4. `assign_split_to_lot(buy_split_guid, new_lot_guid)`:
   - Split exists ✓
   - Lot exists ✓
   - Lot not closed ✓ (just created)
   - Same account ✓
   - `split.lot is None` ✓ (the voided BUY was never lotted)
   - **No void check** → proceeds. `split.lot = lot`. Save.
5. The new lot now contains a voided zombie. `_lot_decimals` over this lot:
   - Voided split: `quantity=0`, `0 > 0` False → `else`: `abs(0) = 0` to sale_quantity.
   - Result: all zeros. `remaining = 0`.
6. `assign_split_to_lot`'s own auto-close: `Decimal("0.0000") == 0` is True, `len(lot.splits) > 0` is True → **auto-closes the brand-new empty lot** the caller just associated with a zombie.

The lot is created, populated with a single voided split, immediately auto-closed. The caller's `list_lots` will show it as closed; `get_lot` will show quantity=0, cost_basis=0. Bookkeeping is internally consistent but the lot is dead-on-arrival and would confuse a future caller trying to assign a real BUY to it.

Worse case: the caller is doing lot-cleanup after a void/recreate workflow, intending to lot a different (live) BUY to this new lot. The voided one got assigned first by mistake (LLM picked the wrong split GUID). Now the lot is auto-closed and the second real-BUY assignment hits the `is_closed` guard with "Cannot assign split to a closed lot." Recovery requires opening the lot manually (no tool for that in this surface — `close_lot` exists, no `reopen_lot`).

No void filter on input. Confirmed.

---

## Claim V-6 (state F13)

**Claim:** `get_book_summary` reconciliation backlog (`book/core.py` around line 340) counts voided splits as "unreconciled" — they show up in the backlog count and noise the warning.

### Verdict: CONFIRMED

### Reasoning

`core.py:327-344`:

```python
if latest_y_date is None:
    unreconciled_count = sum(
        1 for s in account.splits
        if s.reconcile_state != "y"
    )
    results.append({
        "account": account.fullname,
        "status": "never reconciled",
        "days_behind": None,
        "unreconciled_count": unreconciled_count,
    })
else:
    days_behind = (today - latest_y_date).days
    unreconciled_count = sum(
        1 for s in account.splits
        if s.reconcile_state != "y"
        and s.transaction.post_date > latest_y_date
    )
```

Both branches use `reconcile_state != "y"`. Voided splits with state='v' satisfy this. The comment at L325-326 ("'c' (cleared) splits count as unreconciled / for this purpose; they're not finalized") acknowledges 'c' inclusion as intentional but says nothing about 'v'.

Additionally, look at the upstream gate at L300-308:

```python
for s in account.splits:
    any_splits = True
    rstate = s.reconcile_state
    if rstate in ("y", "c"):
        has_yc = True
    if rstate == "y":
        pd = s.transaction.post_date
        ...
```

The `has_yc` gate decides whether an ASSET-type account is included at all (L313-314: `if account.type == "ASSET" and not has_yc: continue`). State='v' does not satisfy `in ("y", "c")` — so an ASSET account whose only history is a voided txn is correctly skipped. That's accidentally fine here, but it doesn't help the BANK/CREDIT/LIABILITY accounts that always pass the type filter.

### Concrete demonstration

`Assets:Checking` (a BANK account) has:
- 50 splits in state 'y' (reconciled through 2025-12-31)
- 10 splits in state 'c' (cleared, post-2025-12-31)
- 3 splits in state 'v' (voided txns from earlier in the year, post-dates 2025-06-15, 2025-08-22, 2025-09-10)

`latest_y_date = 2025-12-31`. The `else` branch runs (L338-351):

```python
unreconciled_count = sum(
    1 for s in account.splits
    if s.reconcile_state != "y"
    and s.transaction.post_date > latest_y_date
)
```

Of the 3 voided splits, post-dates 2025-06-15 / 2025-08-22 / 2025-09-10 are all BEFORE 2025-12-31, so the `post_date > latest_y_date` filter drops them in THIS scenario.

Different scenario: the voided txn is post-dated AFTER the last reconciliation (e.g., user voided a January 2026 transfer):
- voided split: state='v', post_date=2026-01-15, > 2025-12-31 ✓ → counted as unreconciled
- The dashboard reports `unreconciled_count=11` (10 cleared + 1 voided) instead of 10.

For the `latest_y_date is None` branch (never reconciled), every voided split on the account is counted, no date filter applied.

For a BANK account with state distribution {n: 20, c: 5, v: 2, y: 0}:
- `has_yc = False` (no 'y' or 'c'... wait, there ARE 5 'c' splits, so has_yc = True). Re-do: {n: 20, c: 0, v: 2, y: 0} for a never-reconciled bank account with two voids: `has_yc = False`, but BANK is not gated by `has_yc` (only ASSET is, L313). So BANK passes through.
- `latest_y_date is None` → first branch. `unreconciled_count = sum(1 for s if s.reconcile_state != "y")` over all 22 splits → **22**. Includes the 2 voided zombies.
- Dashboard shows "Assets:Checking: never reconciled, 22 splits unreconciled" when the real work is on 20 splits.

The voided splits also noise the warning rendering: the LLM reads "22 splits" and plans a reconciliation pass sized for 22 — when 2 of them are voids that cannot be reconciled at all (they're zombies; their `reconcile_state` is supposed to stay 'v'; setting them to 'y' is the corruption Claim V-3 documents).

Confirmed.

---

## Summary

| Claim | Verdict |
|-------|---------|
| V-1 — `get_unreconciled_splits` admits voided | CONFIRMED |
| V-2 — state='v' ⇒ value==0 is convention, not asserted | CONFIRMED |
| V-3 — `set_reconcile_state` doesn't reject voided targets | CONFIRMED |
| V-4 — `_lot_decimals` doesn't filter voided; voided BUY after SELL drives `remaining` negative and bypasses auto-close | CONFIRMED |
| V-5 — `assign_split_to_lot` doesn't reject voided splits | CONFIRMED |
| V-6 — `get_book_summary` backlog counts voided as unreconciled | CONFIRMED |

All six claims confirmed. The common shape: this codebase consistently treats voided splits as a state to *recognize* (when convenient — `_calculate_lot_balance`, `pay_invoice`, `calculate_lot_gain`) rather than a state to *exclude by default* (everywhere a "live splits only" iteration is needed). The fix surface is well-bounded: a shared `_is_voided(split)` predicate, applied consistently in `get_unreconciled_splits`, `_lot_decimals`, `assign_split_to_lot`'s input guard, `set_reconcile_state`'s pre-write guard, and the `_account_reconciliation_status` sums in `core.py`. The audit-log behavior of `set_reconcile_state` (V-3) needs an additional guard regardless — `v → y/c/n` should fail closed because the unvoid path is the only legitimate way out of state 'v'.
