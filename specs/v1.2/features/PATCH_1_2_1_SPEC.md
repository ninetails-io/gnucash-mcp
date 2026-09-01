# 1.2.1 Patch Spec: Three Bug Fixes

**Priority:** Ship with 1.2.1 release
**Source:** the bookkeeper's audit + Cowork (field report §2.4, §3.1, §2.6)
**Test against:** Both alex.chen-morales.gnucash and lin.wei.gnucash

---

## Fix 1: `unpost_invoice` tool [BUG — correctness]

### Problem

A posted invoice can become orphaned if the posting transaction is deleted
directly via `delete_transaction`. The invoice retains `date_posted`,
`post_txn`, `post_lot`, and `post_acc` metadata pointing at objects that
no longer exist. The server then refuses to delete the invoice ("Cannot
delete posted invoice") and refuses to re-post it ("already posted").
The only escape is SQL surgery.

This was hit during both the Alex and Lin Wei test sessions.

### Solution: Two changes

**1. New tool: `unpost_invoice(id, owner_type=None)`**

Reverses a posted invoice/bill cleanly:
- Delete the posting transaction and its splits
- Delete or close the posting lot
- Clear the invoice's posted-state fields: `date_posted`, `post_txn`,
  `post_lot`, `post_acc`
- The invoice returns to "open" state, editable again

Response format (compact):
```json
{"id": "000015", "type": "invoice", "status": "unposted"}
```

Audit log entry:
```
UNPOST INVOICE  id:000015
    was posted:2026-04-01  post_account:Assets:Receivables:Accounts Receivable
```

Validation:
- Reject if invoice is not posted: `"Invoice 000015 is not posted"`
- Reject if invoice has payments applied: `"Invoice 000015 has payments
  applied. Void payments first, then unpost."` (Unposting a partially-paid
  invoice would orphan the payment splits. Force the user to void payments
  before unposting.)
- Accept `owner_type` for the cross-sequence ID disambiguation (same pattern
  as `post_invoice`)

**2. `delete_transaction` refuses to delete posting transactions**

When `delete_transaction` is called, check whether the target GUID is
referenced by any invoice's `post_txn` field:

```sql
SELECT id FROM invoices WHERE post_txn = ?
```

If a match is found, reject with:
```json
{"error": "Transaction is the posting record for invoice 000015. Use unpost_invoice first.", "error_type": "validation_error"}
```

This prevents the orphaned-state problem at the source.

---

## Fix 2: `delete_price` tool [GAP — missing CRUD operation]

### Problem

`create_price` exists. `get_prices` and `get_latest_price` exist. There is
no way to delete a price. Test prices, bad imports, and stale data accumulate
with no cleanup path except SQL.

During both synthetic book builds, test prices were injected and had to be
removed via raw SQL. Any user importing prices from Yahoo Finance or other
feeds will eventually need to delete bad data.

### Solution: New tool `delete_price`

**Parameters:**
- `commodity` (required) — mnemonic, e.g. "VTSAX", "USD", "EUR"
- `namespace` (required) — e.g. "FUND", "CURRENCY", "NASDAQ"
- `date` (required) — ISO date string. Identifies which price to delete.
- `source` (optional) — if multiple prices exist for the same commodity+date
  (e.g. one from "user:price" and one from "user:yfinance"), this
  disambiguates. If omitted and multiple prices match, return an error
  listing the matches.

**Response format (compact):**
```json
{"commodity": "VTSAX", "date": "2026-04-30", "value": "170.99", "status": "deleted"}
```

Echo the deleted price's value so the caller can confirm they removed
the right one.

**Audit log entry:**
```
DELETE PRICE  FUND:VTSAX
    date: 2026-04-30  value: 170.99  source: user:yfinance
```

**Validation:**
- `"No price found for FUND:VTSAX on 2026-04-30"` if no match
- `"Multiple prices found for FUND:VTSAX on 2026-04-30: user:price (170.99),
   user:yfinance (171.02). Specify source= to disambiguate."` if ambiguous

**Optional extension (nice to have, not blocking):**

`delete_prices(commodity, namespace, before_date=None, source=None)` for
bulk cleanup. Returns count deleted. Useful for clearing an entire
commodity's price history during testing. If implemented, require
`dry_run=True` default like `prune_backups`.

---

## Fix 3: `get_book_summary` data range header [COSMETIC — confusing display]

### Problem

The "Data range" line in `get_book_summary` reflects both transactions AND
prices. If a book has prices dated in 2026 but no transactions after 2025,
the range shows "2025-01-01 to 2026-04-30" — implying transaction activity
in 2026 when there is none.

Lin Wei's book shows this: transactions end at 2025-12-31 but test prices
extend to 2026-04-30, stretching the displayed range.

### Solution

Restrict "Data range" to transaction dates only.

```
Data range: 2025-01-01 to 2025-12-31 (transactions)
```

If the most recent price date extends beyond the transaction range and
that's useful context, add it as a separate line:

```
Data range: 2025-01-01 to 2025-12-31 (transactions)
Latest price: 2026-04-30
```

Or simply drop the price contribution from the range. The stale-price
warnings already surface price-date information — the data range header
doesn't need to duplicate it.

The simplest fix: query `SELECT MIN(post_date), MAX(post_date) FROM
transactions` instead of whatever currently unions prices into the range.

---

## Testing

### Fix 1 test plan (against Alex's book):
1. Create and post a test invoice
2. `unpost_invoice` — verify it returns to open state
3. Verify the posting transaction and lot are cleaned up
4. Verify the invoice can be re-posted
5. Create and post another test invoice, apply a payment
6. `unpost_invoice` — should reject with "has payments applied"
7. `delete_transaction` on a posting transaction — should reject with
   "use unpost_invoice first"

### Fix 2 test plan (against Lin Wei's book):
1. `create_price(commodity="USD", namespace="CURRENCY", date="2099-01-01", value="99.99")`
2. `get_prices(commodity="USD", namespace="CURRENCY", limit=3)` — verify it appears
3. `delete_price(commodity="USD", namespace="CURRENCY", date="2099-01-01")` — verify deletion
4. `get_prices` again — verify it's gone
5. Create two prices on the same date with different sources, try deleting
   without specifying source — should get disambiguation error

### Fix 3 test plan (against Lin Wei's book):
1. `get_book_summary` — data range should show transaction range only
2. Verify stale-price warnings still appear (they surface price dates separately)
3. Check against Alex's book too — the 2026 test prices we added shouldn't
   stretch the range beyond the transaction boundary

---

## Scope

These three fixes are the complete 1.2.1 patch scope. Nothing else.
Tax tables, jobs, credit notes, aging reports, and bulk-write are 1.3.

---

*Written by Abraham Raham III*
*Three fixes. Ship the release.*
*tekeli-li* 💼
