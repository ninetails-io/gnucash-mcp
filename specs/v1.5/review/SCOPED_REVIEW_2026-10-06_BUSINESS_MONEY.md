# Scoped review: business MONEY (fix/v1.5-adversarial-blockers, 43353a7..d42400e)

Every finding below was checked against the CURRENT working tree (HEAD 204f4d1, business.py last touched by 4e536e0). Line numbers are from the current tree. Repro tests are in this folder. Run them with `./run.sh <file>`; `conftest.py` re-exports `tests/conftest.py`.

## Findings

### BM-1: SERIOUS (by the C15 grading; BLOCKER by the letter, because it writes a wrong lot-link posting). CONFIRMED
**File:** `src/gnucash_mcp/book/business.py:9754-9765` (`apply_credit_note`, split signs)

**What happens:** The C15 fix added a refusal when both lots carry a balance on the same side. It did not change how the link splits are signed. Those signs are still hard-coded by side: on the customer side the credit-note split is +apply and the target split is −apply. They are not derived from the two lots' balances, as `gncOwnerCreateLotLink` derives them (`xaccSplitSetBaseValue (split, gnc_numeric_neg (from_lot_bal) …)`).

The new check lets through the one opposite-signed pair that is not the ordinary one: a credit note carrying a debit (its lines net to a charge, +50) applied to an invoice carrying a credit (a negative-total invoice per C44, −70, or an overpaid one). For that pair, the link pushes both lots further from zero.

**Repro** (`test_bm1_apply_direction.py`):
- Before: INV total −70, due −70. CN total −50, due −50.
- `apply_credit_note` returns `amount_applied 50.00`, `credit_note_remaining 100.00`, `target_remaining 120.00`.
- After: INV `amount_paid 50.00`, `amount_due −120.00`. CN `amount_paid 50.00`, `amount_due −100.00`.
- Raw lot sums: −120 and +100.

A/R in total is unchanged, but both documents now misstate what is owed, by twice the applied amount. The response reports this as a successful application. The repository's own correct port, `_create_lot_link`, is not used here.

**Fix:** Sign from the balances: `cn_split_value = -copysign(apply, cn_balance)` and `target_split_value = -cn_split_value`. Better, route through `_create_lot_link` with a cap. Lock it with a case for the abnormal pair, on both the A/R and A/P sides.

### BM-2: SERIOUS (narrower trigger than C47, same symptom). CONFIRMED
**File:** `business.py:1167-1189` (`_credited_share`, line 1184)

**What happens:** A lot split counts as a "credit application" when any OTHER split of its transaction sits in a lot. The transaction type is not checked. `_document_payments` checks `== "L"`; this does not.

Some cash payments have an A/R or A/P split in two lots:
- every server `allow_prepayment` payment;
- every desktop payment that covers more than one document, or that leaves a remainder (`gncOwnerReduceSplitTo` splits it inside the one `P` transaction).

Cash from such a payment that ends up in a document's lot (through `from_prepayment` or desktop's auto-apply) is counted as credit. That shrinks the early-payment discount, although the docstring says "Cash already paid does not reduce it".

**Repro** (`test_bm2_credited_share.py`):
- Invoice A (100) is paid with 300 using `allow_prepayment`.
- Invoice B (1000, "2/10 net 30") is settled 200 `from_prepayment`.
- Paying B 780 with `apply_discount` is **refused**: "expected discount 16.00 … adjust amount to 784.00".
- The same 200 paid as direct cash: 780 is accepted with a 20.00 discount.

**Fix:** Count a split only when `_txn_type(txn) == "L"`, as `_document_payments` does. Add a C47 test with a prepayment-applied split and a desktop two-document payment.

### BM-3: SERIOUS. CONFIRMED (desktop-shaped payment, built by SQL as the payment twin records it)
**File:** `business.py:11128-11190` (`_bill_amounts_in_default`, feeding `vendor_spending_report`)

**What happens:** "Outstanding" is the default-currency sum of the lot's splits via `_posting_split_in_default`, which takes `split.value` whenever the transaction is in the default currency. Since the 2026-09-30 payment-currency change, a cross-currency payment is a pay-account-currency transaction. Its A/P split's VALUE is therefore the pay-date bank amount.

That equals the carrying amount only when the server books its FX split beside it. Desktop never books one, and neither does the server when `rate_at_post` or the third-currency rate is missing.

**Repro** (`test_bm_vendor_report.py`):
- EUR 1,000 bill (book USD), posted at 1.10, paid 1,000 EUR = 1,200 USD.
- Server shape: billed 1100 / paid 1100 / outstanding 0.
- Desktop shape: billed 1100 / paid **1000** / outstanding **100.00**, while `get_document` reads `amount_due 0`.

A fully paid bill shows as owing 100 USD in the vendor report whenever desktop recorded the payment.

**Fix:** Read outstanding from `_document_settlement` (document currency, by quantity). Convert it at the posting's rate, the same rate `billed` uses, and keep `paid = billed − outstanding`.

### BM-4: MINOR. CONFIRMED
**File:** `business.py:8781-8802` (discount leg of `pay_invoice` with `payment_account_amount`)

**What happens:** With `payment_account_amount`, the docstring promises "no quote is consulted (or needed)". The discount leg still converts through `_convert` (a quote, plus the 7-day freshness guard).
- (a) With no EUR/USD quote on file, a discounted cross-currency payment with the received amount stated raises "Cross-currency payment requires an exchange rate". The same payment without a discount succeeds.
- (b) With a quote, the discount is valued at the quote (1.10) rather than the rate actually paid (1.1224). Discount income and FX loss are each misallocated by the difference (22.00 against 22.45 in the repro). The transaction still balances.

**Repro:** `test_bm_fx.py::test_received_amount_with_discount_no_quote` and `::test_bill_card_discount`.

**Fix:** When `received` is given, take `disc_quantity = (expected * exchange_rate)` at the pay currency, converting onward only if the discount account is a third currency.

### BM-5: MINOR. CONFIRMED
**Files:** `business.py:8595-8660` (excess on a refund); `business.py:2499` and `9283` (readers)

**What happens:** `allow_prepayment` accepts an excess on a refund direction too: a customer credit note, or a negative document. The excess lot then sits on the "owed by the party" side:
- +50 in A/R for a customer;
- −x in A/P for a vendor credit-note refund.

`_unapplied_payments` drops it (`available <= 0`), so nothing lists it. `from_prepayment` on an invoice refuses it. Yet the response says "Held in the post account as the party's unapplied payment. Settle a later document from it with from_prepayment=true."

**Repro** (`test_bm_cn_prepay.py`): customer CN 100 refunded 150 with `allow_prepayment` → A/R +50. The outstanding list is empty, there are no unapplied payments, and the next invoice's `from_prepayment` is refused.

**Fix:** Refuse `allow_prepayment` when the payment is a refund (effective direction flipped by credit note or negative total). Alternatively, list opposite-sign owner lots as money owed by the party, and word the note accordingly.

### BM-6: MINOR. CONFIRMED
**Files:** `business.py:7542` (`_convert_invoice_amount`), `8799-8801` (discount value), `1350`, `1373`, `1411`, `1426`, `1437` (`_compute_fx_gain_loss`), `11126`

**What happens:** Cross-commodity amounts are quantized with Python's default ROUND_HALF_EVEN. Desktop's exchange dialog, which supplies these amounts for a post or a payment, rounds half-up: `dialog-transfer.cpp:1006-1007`, `gnc_numeric_mul(amount, price_value, scu, GNC_HOW_RND_ROUND_HALF_UP)`.

**Repro** (`test_bm_halfeven.py`): EUR 100.15 at 1.10 = 110.165. The server posts the USD income quantity, and quotes the payment amount, as **110.16**; desktop computes 110.17. It is off by one unit on exact ties only, but it lands in stored quantities. The entry math (`_compute_discount_summary`) is already explicit half-up; these sites are not.

**Fix:** Pass `rounding=ROUND_HALF_UP` at these quantize sites, or one helper that does.

### BM-7: NIT. CONFIRMED
**File:** `business.py:3855` (`_document_settlement`)

`amount_due = (sign * balance).quantize(quantum)` with `sign = -1` and a zero balance gives `Decimal('-0.00')`. Every fully paid vendor bill or applied customer credit note reads `amount_due "-0.00"` in `get_document`, and `remaining_balance "-0.00"` in `from_prepayment`. `pay_invoice` says "0.00" for the same state.

**Repro:** `test_bm_negzero.py`, and in `test_bm_unpost.py` C1/C2 read `-0.00`.

**Fix:** `+ Decimal(0)` after the multiply, or `copy_abs()` when the value is zero.

### BM-8: NIT. CONFIRMED (cosmetic)
- `business.py:8745`: the discount-mismatch error prints `remaining_before` unquantized ("Outstanding balance: 800 USD").
- `business.py:1470`: the FX memo stored on the split prints the pay rate at 28 digits (`pay-rate 1.122448979591836734693877551`) beside a 4-place post rate.
- `_entry_to_dict`: a line's `total` is the raw `qty*price` (60.015), not the value the line posts (60.02).

### BM-9: NIT. PLAUSIBLE (not run)
**File:** `_base.py:423` (`_commodity_quantum`), as used in `business.py:7542`

For a currency whose fraction is not a power of ten (GnuCash's MGA, `smallest-fraction="5"`), `Decimal(1)/5 = 0.2`. `quantize(0.2)` rounds to 0.1, so a converted quantity like 1.3 MGA (not a multiple of 1/5) can be produced. `round_half_up` in `_entry_math` handles fraction 5 correctly. The conversion path does not.

## Examined and sound

- **`_entry_math.py`, ported against GnuCash 5.12 source:**
  - Checked line by line against `gncEntryComputeValueInt` (aggregate; tvalue/tpercent; the tax-included back-out; PRETAX/SAMETIME/POSTTAX; VALUE vs PERCENT discount; per-account tax collapse), against `gncEntryRecomputeValues` (bill side zero discount; table only when taxable), and against `gncInvoiceGetNetAndTaxesInternal` (rounded net per line, unrounded tax summed per account then rounded half-up once).
  - The totals oracle was run live on shapes the recorded generator never produces: JPY (fraction 1) and BHD (1000) books; negative, zero and fractional-negative quantities; negative tax percentages; percent + VALUE mixed tables on bills; flat-value tables with tax-included lines. That was 3 × 120 documents (`desktop_totals_variant.py`): **0 mismatches** against `gnucash-cli`.
  - `round_half_up` is symmetric about zero, and correct for fractions 1, 5, 100 and 1000.
- **`_card_charges` and `post_invoice`** against the `GNC_PAYMENT_CARD` branch and `to_charge_amount` (memo "Extra to Charge Card", credit-note sign handling, rounded per-line value, A/P = total − card). Repro: 40 cash + 3×20.005 card + 5 extra → A/P −35, card −60.02/−5, expense 100.02. `get_document`, `pay_document`, unpost and the outstanding/unapplied lists all agree.
- **`_billterm_return_child`:** refcount decrement on the parent and none on the child, matching `gncBillTermIncRef/DecRef`; a child is reused on re-post. `_billterm_compute_time` matches `compute_time`.
- **Settlement readers:** `_posted_total`, `_lot_split_amount` and `_calculate_lot_balance` read the quantity for pay-currency payment splits. A desktop-shaped USD payment of a EUR bill reads `amount_due 0` from `get_document` (contrast BM-3).
- **`pay_invoice`, cross-currency:** a EUR bill paid from a USD CREDIT card with `payment_account_amount` and a discount gives A/P relieved at carrying (1100), card −1100, FX loss 22 and discount −22. It balances, the lot closes, and the signs are right for the bill side.
- **Excess and price recording:** `settle_pay_quantity` / `prepay_row` values balance. `_record_payment_price` uses exact fractions.
- **FX freshness guard:** a forced stale payment writes the `user:xfer-dialog` price typed `transaction`, which `_market_prices_only` excludes. The next unforced payment is still refused. The guard is not laundered.
- **gncOwner.c ports:** `_find_offsetting_split`, `_reduce_split_to` (half-up target value, remainder = original − target), `_offset_lots` and `_auto_apply_lots` match `gncOwnerFindOffsettingSplit`, `ReduceSplitTo`, `OffsetLots` and `AutoApplyPaymentsWithLots`. `_create_lot_link` is balance-signed, unlike BM-1. `_lot_link_date` uses the later of the two latest dates.
- **`unpost_invoice`** with a lot-link transaction shared by two credit notes: unposting C2 removes the shared link and re-links I1↔C1 (I1 due 300). Unposting I1 keeps the 100 payment as unapplied and C1 reopens with a 100 credit. A/R −200 is consistent.
- **`apply_credit_note`** on the ordinary pairs, and the C15 refusal for same-sign lots.
- **Discount window (C46):** anchored on the posting date.
- **C47 proration:** correct for real `L` links; see BM-2 for the exception.
- **Float conversions:** none (`Decimal(float)` / `float(`) in `business.py` or `_entry_math.py`.
