# 1.2.1 Patch Testing Spec

**Companion to:** `docs/PATCH_1_2_1_SPEC.md`
**Verifies:** PR #62 — `feat/patch-1-2-1`
**Test against:** Alex's book for Fix 1, Lin Wei's book for Fix 2 and Fix 3
**Cleanup:** Required at end (see Cleanup section)

The bookkeeper runs this against the running MCP server to verify
all three patch fixes from the spec landed correctly. Each fix has
a happy path and one or more rejection branches; the rejection
branches are the actual point of the fix and should be exercised
explicitly.

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

**Pass criteria for Fix 1:** A through F all return their expected
outcomes. B and C verify the happy path; A, D, E, F verify each
rejection branch.

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

## Cleanup

After the test run, on whichever books you used:

1. `delete_transaction(guid=...)` for any test transactions you
   created. (The regular-deposit ones; posting records will
   refuse — use `unpost_invoice` first, then delete the unposted
   invoice via `delete_invoice`.)
2. `delete_invoice(invoice_id=...)` for unposted test invoices.
3. `delete_customer(customer_id=...)` for the "Test Co - DELETE ME"
   customer.
4. `delete_price(...)` for any test prices that survived.

If anything resists cleanup, that's interesting — flag it.

---

## Signoff

Three lines back:

- **Fix 1** (`unpost_invoice` + `delete_transaction` guard): ✅ / ❌ + notes
- **Fix 2** (`delete_price`): ✅ / ❌ + notes
- **Fix 3** (`get_book_summary` data range): ✅ / ❌ + notes

If all three are green, PR #62 is ready to merge to `develop`,
and that closes the 1.2.1 patch scope per the spec.
