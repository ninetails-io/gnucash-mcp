# 1.2.1 Patch Testing Spec

**Companion to:** `docs/PATCH_1_2_1_SPEC.md`
**Verifies:** PR #62 — `feat/patch-1-2-1`
**Test against:** Alex's book for Fix 1; Lin Wei's book for Fix 2,
Fix 3, and Fix 7. Either book works for Fixes 4, 5, 6, and 8.
**Cleanup:** Required at end (see Cleanup section)

The bookkeeper runs this against the running MCP server to verify
the patch landed correctly. Each fix has a happy path and one or
more rejection branches; the rejection branches are the actual
point of the fix and should be exercised explicitly.

The original five fixes (1–5) were signed off by the bookkeeper
in the first review pass. Fixes 6, 7, and 8 were added afterward
in response to bookkeeper / cousin field-finds; they need a
second review pass before PR #62 merges.

---

## Fix 1: `unpost_invoice` + `delete_transaction` guard

### Setup (Alex's book)

```
create_customer(name="Test Co - DELETE ME")
# Note the returned id, e.g. "000023"
create_invoice(customer_id="<that id>", currency="USD", date_opened="2026-04-15")
# Note the invoice id, e.g. "000019"
add_invoice_entry(invoice_id="<inv id>", account="Income:Consulting",
                  description="Test", quantity="1", price="500.00")
post_invoice(id="<inv id>", post_account="Assets:Receivables:Accounts Receivable",
             post_date="2026-04-15")
# Capture the transaction_guid from the response.
```

### Verifications

**A. `delete_transaction` rejects posting records**

```
delete_transaction(guid="<the captured transaction_guid>")
```

Expect `validation_error`:
*"Transaction is the posting record for invoice ⟨inv id⟩. Use unpost_invoice first."*

**B. `unpost_invoice` reverses cleanly**

```
unpost_invoice(id="<inv id>")
```

Expect `{"id": "<inv id>", "type": "invoice", "status": "unposted"}`.
Then:

```
get_invoice(id="<inv id>", owner_type="customer")
```

Expect `date_posted: null` and the entry still present.

**C. Re-post works after unpost**

```
post_invoice(id="<inv id>", post_account="Assets:Receivables:Accounts Receivable",
             post_date="2026-05-01")
```

Expect `status: "posted"`, `post_date: "2026-05-01"`. Lifecycle verified end-to-end.

**D. `unpost_invoice` rejects payments-applied invoice**

Create a second test invoice, post it, then apply a partial payment:

```
pay_invoice(id="<second inv id>", payment_account="Assets:Current Assets:Checking Account",
            amount="50.00", payment_date="2026-04-15")
```

Then:

```
unpost_invoice(id="<second inv id>")
```

Expect `validation_error`:
*"Invoice ⟨id⟩ has payments applied. Void payments first, then unpost."*

**D2. `unpost_invoice` succeeds when payments are voided**

Continuing from D — void the partial payment, then retry the unpost.
Capture the payment's transaction guid from D's `pay_invoice` response,
then:

```
void_transaction(guid="<payment transaction_guid>", reason="Test cleanup")
unpost_invoice(id="<second inv id>")
```

Expect `{"status": "unposted"}`. The "has payments applied" guard
asks an *economic* question; voided payments have zero economic
effect (they're zombie splits preserved for audit trail), so they
shouldn't block unpost.

**E. `unpost_invoice` rejects unposted invoice**

On any open (not-yet-posted) invoice:

```
unpost_invoice(id="<some open invoice id>")
```

Expect `validation_error`: *"Invoice ⟨id⟩ is not posted"*.

**F. `unpost_invoice` rejects unknown id**

```
unpost_invoice(id="999999")
```

Expect `validation_error`: *"Invoice/bill not found: 999999"*.

**Pass criteria for Fix 1:** A, B, C, D, D2, E, F all return their
expected outcomes. B, C, D2 verify happy paths; A, D, E, F verify
each rejection branch.

---

## Fix 2: `delete_price`

### Setup (Lin Wei's book)

Pick any commodity with prices already in the book (e.g., `USD` in
`CURRENCY` namespace). `get_book_summary` lists the available
commodities.

### Verifications

**A. Single-price delete echoes value**

```
create_price(commodity="USD", namespace="CURRENCY", date="2099-01-01", value="99.99")
get_prices(commodity="USD", namespace="CURRENCY", limit=3)
```

Confirm `2099-01-01` appears.

```
delete_price(commodity="USD", namespace="CURRENCY", date="2099-01-01")
```

Expect `{"status": "deleted", "value": "99.99", "date": "2099-01-01", ...}`.
Then `get_prices` again — `2099-01-01` should be gone.

**B. No-match raises clearly**

```
delete_price(commodity="USD", namespace="CURRENCY", date="2099-01-01")
```

(After A, this date has no price.) Expect `validation_error`:
*"No price found for CURRENCY:USD on 2099-01-01"*.

**C. Ambiguous-without-source lists candidates**

```
create_price(commodity="USD", namespace="CURRENCY", date="2099-02-01",
             value="7.10", source="user:price")
create_price(commodity="USD", namespace="CURRENCY", date="2099-02-01",
             value="7.15", source="user:yfinance")
delete_price(commodity="USD", namespace="CURRENCY", date="2099-02-01")
```

Expect `validation_error` mentioning *both* `user:price (7.1)` and
`user:yfinance (7.15)` and the phrase
*"Specify source= to disambiguate"*.

**D. Source disambiguates**

```
delete_price(commodity="USD", namespace="CURRENCY", date="2099-02-01", source="user:yfinance")
```

Expect `status: "deleted"` with `value: "7.15"`. Then:

```
get_prices(commodity="USD", namespace="CURRENCY", limit=3)
```

Confirm the `user:price` entry for 2099-02-01 (value 7.10) is
still there. Then clean up:

```
delete_price(commodity="USD", namespace="CURRENCY", date="2099-02-01", source="user:price")
```

**Pass criteria for Fix 2:** All four branches behave as expected.
Note any case where the value echo strips trailing zeros (`7.1`
vs `7.10`) — that's piecash's storage behavior, not a fix bug.

---

## Fix 3: `get_book_summary` data range

### Verification (Lin Wei's book)

Optionally inject a far-future test price first to exercise the
case the spec described:

```
create_price(commodity="USD", namespace="CURRENCY", date="2099-01-01", value="99.99")
```

Then:

```
get_book_summary()
```

Look at the **Data range** line near the top:

```
Data range: 2025-01-01 to 2025-12-31
```

**Pass criteria for Fix 3:** The displayed range matches Lin Wei's
actual transaction range and does *not* reach into 2099 (or
whatever far-future price you injected). Clean up the test price
afterward via `delete_price`.

> Note: this fix is "regression test only" — the underlying code
> already iterated transactions only. The patch locks the contract
> so a future refactor can't accidentally union price dates back in.
> If the displayed range surprises you, that's worth flagging.

---

## Fix 4: void-aware behavior in lot listing and gain calculation

Voided splits are zombie splits — preserved for audit but zeroed.
Two downstream display surfaces had to learn the same lesson the
``unpost_invoice`` guard learned in Fix 1: zero-value splits are
not real positions.

### Setup (Alex's book)

Pick any USD-denominated investment account that has at least one
buy transaction. Alex's ``Assets:Investments:VTSAX`` works if it
exists; otherwise ``Assets:Investments:S&P 500 ETF`` or any
similar.

```
list_lots(account="<the investment account>", verbose=True)
```

Note any existing lots and their states. We'll add and void one
without disturbing the rest.

### Verifications

**A. ``list_lots`` skips empty lots in the default view**

Create a brand-new lot, then list. With nothing assigned, the
new lot has zero remaining quantity:

```
create_lot(account="<the investment account>", title="DELETE ME — empty test")
list_lots(account="<the investment account>")
```

Expect the new lot to be **absent** from the output. The default
view is "open positions"; a zero-quantity lot doesn't qualify.

Then:

```
list_lots(account="<the investment account>", include_closed=true, verbose=true)
```

Expect the new lot to **appear** with `quantity: 0.0000` and
`cost_basis: 0.00`. ``include_closed=true`` is the audit-trail
view that surfaces empty lots.

**B. ``calculate_lot_gain`` calls out voided buys explicitly**

This requires a slightly more involved setup — a lot whose buy
transaction was voided. Skip if the live test would risk
disturbing real lots; the unit-test coverage locks the message
shape.

Optional: against a scratch invoice/lot, post → assign → void
the buy → call ``calculate_lot_gain``. Expect a
`validation_error` containing the phrases *"no remaining
shares"* and *"voided"* — not the generic message.

**Cleanup:** delete the test lot via `close_lot` (close-then-skip
is fine; lots can't be deleted directly).

**Pass criteria for Fix 4:**
- A: empty lot absent from default view, present with
  ``include_closed=true``
- B: voided-buy error mentions "voided" explicitly (or skip if
  not exercised live)

---

## Fix 5: ``void_transaction`` warns on reconciled splits

Voiding a transaction whose splits are reconciled breaks the
reconciled balance for the affected accounts. The void should
proceed (audit trail trumps bookkeeping cleanliness) but the
result must include a ``warning`` naming the affected accounts
so the bookkeeper knows what just got broken.

### Setup (Lin Wei's book)

Find an existing reconciled transaction:

```
get_unreconciled_splits(account="<some account>")
```

… or pick any transaction you know contains a reconciled split.
A transaction Lin Wei voided previously was on her checking
account; the test book ships with at least a few reconciled
transactions tied to opening balances.

If you'd rather create a fresh test, post a small transaction,
mark its checking-side split reconciled via
``set_reconcile_state(split_guid="...", state="y")``, then void
that transaction.

### Verifications

**A. Void with reconciled splits → warning surfaced**

```
void_transaction(guid="<txn guid>", reason="Test cleanup")
```

Expect:
- `status: "voided"` (the void went through)
- `warning` field present, containing:
  - the word "reconciled"
  - the account name(s) of the reconciled splits
  - language about the reconciled balance no longer matching the
    cleared statement

Example:
```json
{
  "status": "voided",
  "warning": "Voided transaction contained 1 reconciled account(s): Assets:Current Assets:Checking Account. The reconciled balance for these accounts no longer matches the cleared statement."
}
```

**B. Void without reconciled splits → no warning**

Sanity: pick any non-reconciled transaction, void it.

```
void_transaction(guid="<non-reconciled txn guid>", reason="Test")
```

Expect `status: "voided"` and **no** `warning` field. (A clean
void must not invent a warning.)

**Cleanup:** ``unvoid_transaction(guid="...")`` to restore both
test voids if they affected real data. The reconciliation state
restores along with the values.

**Pass criteria for Fix 5:**
- A: warning surfaces with the affected account name(s) and
  reconciliation language
- B: clean voids carry no warning

---

## Fix 6: `update_customer` / `update_vendor` / `update_employee`

The headline addition: closes the "I made a typo and now I have
to open GnuCash" gap. Pre-fix, `delete_customer` (etc.) refused
once the entity had any documents, so a typo on an address line
had no recovery path through the MCP server. The new updaters
mutate fields in place; existing invoices/bills are untouched.

### Setup (any book)

Pick or create a customer to mutate. Either:

```
create_customer(name="Update Test Co - DELETE ME",
                address={"addr1": "Old Street 1"})
```

…or grab an existing customer from `list_customers` if you'd
rather mutate-and-revert against real data.

### Verifications

**A. Update name**

```
update_customer(id="<id>", name="Update Test Co LLC")
```

Expect `{guid, id, status: "updated", name: "Update Test Co LLC"}`.
Diff-style response: only the fields that changed appear.
Then `get_customer(id="<id>")` should reflect the new name.

**B. Address merge — partial dict updates only the supplied keys**

```
update_customer(id="<id>", address={"phone": "555-1234"})
```

The `addr1` field from setup ("Old Street 1") should be untouched
in the response and in `get_customer`. Only `phone` shows as
changed.

**C. Address sub-field clear with empty string**

```
update_customer(id="<id>", address={"phone": ""})
```

Phone clears, addr1 still untouched.

**D. Unknown address key rejected upfront**

```
update_customer(id="<id>", address={"addresss": "typo"})
```

Expect `validation_error`: *"Unknown address field(s):
['addresss']. Valid keys: name, addr1, addr2, addr3, addr4,
phone, fax, email."*

**E. No-op call rejected**

```
update_customer(id="<id>")
```

Expect `validation_error`: *"No changes supplied — pass at least
one field to update on customer '<id>'."*

**F. Active toggle (deactivation = archive)**

```
update_customer(id="<id>", active=false)
```

Expect `{...status: "updated", active: false}`. Then:

```
list_customers(active_only=true)
```

The deactivated customer should be absent. Re-list with
`active_only=false` to confirm it still exists.

```
update_customer(id="<id>", active=true)
```

Reactivates.

**G. Currency switch via `_get_or_create_currency`**

```
update_customer(id="<id>", currency="EUR")
```

Should succeed even if the book hasn't seen EUR before — the
helper auto-loads ISO codes from piecash's table. (Same fix as
`create_price` in this same release.)

**H. Vendor and Employee parity**

Repeat A and B against `update_vendor` and `update_employee`:

```
update_vendor(id="<vendor id>", name="...", address={"phone": "..."})
update_employee(id="<employee id>", name="...", currency="USD")
```

Note: Employee has no `notes` parameter (piecash schema). Passing
one to `update_employee` should raise *"Employee has no notes
field — drop ``notes=`` from the update call."*

**I. The headline scenario — update after invoices exist**

The whole point: the limitation that breaks `delete_customer +
recreate` doesn't apply to update. Pick a customer with at
least one invoice (Lin Wei's book has several), update their
name, confirm it sticks. The invoice is unaffected.

**Pass criteria for Fix 6:** A through I all behave as expected.
G specifically verifies the auto-currency-load.

---

## Fix 7: `get_book_summary` work-queue dashboard

Three new lines turn the dashboard from a snapshot into an
LLM's first-call to-do list:

- **Last entry** — when did activity actually stop, and how
  far behind / ahead is that vs. today
- **Scheduled** line — now carries a "K due in next 7 days
  (CCY total)" or "none due in next 7 days" clause
- **Reconciliation** stale lines — now show "N splits
  unreconciled since DATE" instead of just "through DATE"

### Verification (Lin Wei's book)

```
get_book_summary()
```

Look at the top of the output:

**A. `Last entry` line — relative phrasing**

A line of the form:

```
Last entry: <YYYY-MM-DD> (<relative>)[ ⚠]
```

Possible relative phrasings:
- `(today)`
- `(yesterday)`
- `(N days behind)` — adds `⚠` past 14 days
- `(future-dated, N days ahead)` — no `⚠` (future-dated entries
  are normal, not "behind")

The future-dated case is the bookkeeper-found regression: pre-fix
a date 31 days in the future would render as "(yesterday)"
because the gate `days_behind <= 1` misclassified negatives. To
exercise: create a transaction post-dated several days into the
future and re-call `get_book_summary`. The line should call out
"future-dated, N days ahead", not call it yesterday.

**B. `Scheduled` line carries upcoming clause**

```
Scheduled: 13 recurring, 3 due in next 7 days (CNY 15,650)
```

…or:

```
Scheduled: 13 recurring, none due in next 7 days
```

The "none due" wording is intentional — empty windows surface
explicitly so the LLM doesn't have to guess from absence of a
clause. Books with zero scheduled transactions still skip the
Scheduled line entirely.

**C. Reconciliation stale lines carry split count**

When an account is more than 45 days behind on reconciliation
*and* has unreconciled splits past the last reconciled date,
the line takes the new shape:

```
  Checking Account: 47 splits unreconciled since 2025-12-30 (4 months behind) ⚠
```

If there are no unreconciled splits past the last reconciled
date (the count is zero), the line falls back to the old
`through DATE (4 months behind) ⚠` format — that case means
"the user's reconciliation IS through that date and there's no
new activity to clear." Both are valid, the count is the new
information when it exists.

**Pass criteria for Fix 7:** A's four phrasings (today /
yesterday / N days behind / future-dated) all render correctly,
including the future-dated regression. B's upcoming clause
appears whenever the Scheduled line appears. C's split-count
form appears for accounts with unreconciled-past-reconciled
activity.

---

## Fix 8: `owner_type` validation

Six tools accept `owner_type` (`get_invoice`, `list_invoices`,
`post_invoice`, `unpost_invoice`, `pay_invoice`,
`get_outstanding_invoices`). All validate upfront via a shared
helper. Pre-fix, anything other than `"customer"` / `"vendor"`
/ `None` silently fell through to "no filter" and the LLM
discovered the limitation only via a confusing downstream error.

### Verifications (any book)

**A. `"employee"` rejected with the specific scope message**

```
get_invoice(id="000001", owner_type="employee")
```

Expect `validation_error`:
*"owner_type='employee' is not yet supported. Employee expense
vouchers are out of scope for the 1.2.x business module. Use
'customer' or 'vendor'."*

**B. Typos rejected with the generic invalid-options message**

```
post_invoice(id="000001", post_account="Assets:Accounts Receivable",
             owner_type="custmer")
```

Expect `validation_error`:
*"Invalid owner_type 'custmer'. Must be 'customer' or 'vendor'."*

**C. `None` (or omitted) still works**

```
get_invoice(id="000001")
```

Should succeed (or raise the disambiguation error if the ID
collides cross-sequence — that's the existing Fix from #61's
PR, not a regression of this fix).

**D. Symmetry across all six entrypoints**

Sample-test each — the validation is identical, so confirming
one or two beyond A/B is sufficient to lock the contract:

```
list_invoices(owner_type="employee")          # rejected
unpost_invoice(id="000001", owner_type="bogus")  # rejected
pay_invoice(id="000001", payment_account="...", amount="50",
            owner_type="employee")           # rejected
get_outstanding_invoices(owner_type="venddor")  # rejected
```

**Pass criteria for Fix 8:** A and B return the expected
error messages explicitly. D confirms the validation fires on
all six surfaces.

---

## Cleanup

After the test run, on whichever books you used:

1. `delete_transaction(guid=...)` for any test transactions you
   created (including the future-dated one from Fix 7-A). The
   regular-deposit ones; posting records will refuse — use
   `unpost_invoice` first, then delete the unposted invoice via
   `delete_invoice`.
2. `delete_invoice(invoice_id=...)` for unposted test invoices.
3. `delete_customer(customer_id=...)` / `delete_vendor` /
   `delete_employee` for any "DELETE ME" entities created
   during Fix 6.
4. **Revert in-place updates from Fix 6** — if you mutated a
   real customer/vendor/employee (rather than a test entity),
   call `update_customer` / `update_vendor` / `update_employee`
   again with the original values. Capture the pre-update state
   from `get_customer` etc. *before* mutating so you have
   something to restore.
5. `delete_price(...)` for any test prices that survived.

If anything resists cleanup, that's interesting — flag it.

---

## Signoff

Eight lines back:

- **Fix 1** (`unpost_invoice` + `delete_transaction` guard): ✅ / ❌ + notes
- **Fix 2** (`delete_price`): ✅ / ❌ + notes
- **Fix 3** (`get_book_summary` data range): ✅ / ❌ + notes
- **Fix 4** (`list_lots` / `calculate_lot_gain` void-awareness): ✅ / ❌ + notes
- **Fix 5** (`void_transaction` reconciled-splits warning): ✅ / ❌ + notes
- **Fix 6** (`update_customer` / `update_vendor` / `update_employee`): ✅ / ❌ + notes
- **Fix 7** (`get_book_summary` work-queue dashboard, including future-dated handling): ✅ / ❌ + notes
- **Fix 8** (`owner_type` upfront validation): ✅ / ❌ + notes

If all eight are green, PR #62 is ready to merge to `develop`,
and that closes the 1.2.1 patch scope.
